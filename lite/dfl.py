"""`dfl` — одна команда коллеги: customer → бриф → engineer (режим test).

`dfl run` — идемпотентный шаг вперёд по журналу прогонов репо в `--cwd`:
бриф-интервью, предложение брифа, после мержа брифа — одобрение и
engineer-прогон, затем проверенный PR с кодом. `dfl answer --file <f>` —
ответ на вопрос интервью (закреплённым discovery) и снова шаг `dfl run`.
В терминале (`stdin` — tty) `dfl run` и `dfl answer` без `--file` ведут
интервью разговором (`lite.interview_chat`): вопрос, ответ своими словами,
уточнения, отправка — до конца интервью, затем обычный шаг конвейера.
`dfl abandon` — бросить незаконченный конвейер (каталоги прогонов уходят в
`out/governance-runs-abandoned/<время>/`, обратимо; активный личный прогон —
первым, в `out/personal-runs-abandoned/<время>/`). `dfl status` — только
журнал, без форджи и эффектов. Конвейер исполняется в процессе
(`spec_loop.main`, `brief_tools.main`).
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from collections.abc import Callable
from contextlib import ExitStack
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

from governance import brief_tools as bt
from governance import df_config, run_lock, runner, spec_loop, user_targets
from governance import interview as iv
from governance import manual_checks as mc
from governance import run_state as rs
from governance.author_launch import AuthorCall, LaunchOutcome, launch_author
from governance.edge_check.check import run_check
from governance.facts import Outcome
from governance.ops import Ops, RealOps
from lite import edge_resolve as er
from lite import failure_log as fl
from lite import interview_chat as ic
from lite import interview_view as iview
from lite import manual_ask as ma
from lite import owner_review as orv
from lite import pending, runlog
from lite import review_pause as rp
from lite import stop_findings as sf
from lite import structure as st
from lite.init_repo import DEVTOOLS, InitError, init_repo, real_gh
from lite.interview_state import InterviewStateError, load_session
from lite.output import (
    refusal,
    say,
    set_sink,
    set_verbosity,
    verbosity,
    verbosity_from_env,
)
from lite.personal import flow as personal_flow
from lite.personal import state as personal_state
from lite.pipeline import (
    CHAIN_STOPPED_HINT,
    accepted_url,
    chain_open,
    code_merged,
    merged_unreviewed,
)
from lite.report import run_report
from lite.tee import tee_output

ENGINEER_SUFFIX = bt.ENGINEER_SUFFIX
NEW_RUN_HINT = "первый запуск: dfl run --subject '<тема>'"
ABANDON_HINT = "dfl abandon бросит его"
NEXT_SUBJECT_HINT = "новая тема: dfl run --subject '<тема>'"
WAITING = "waiting_interview"
REPORT_HINT = "отчёт разработчикам: dfl report"
ANSWER_HINT = "ответьте: dfl answer --file answer.yaml"
ANSWER_FORMAT = """\
файл ответа — YAML: text (ответ своими словами) и entries (пункты темы —
только они засчитываются в покрытие интервью; id — с префикса темы вопроса):
  text: |
    команда печатает версию пакета
  entries:
    - id: FR-01
      body: команда smoke --version печатает версию и выходит с кодом 0
      traces: [G-01, J-01]
      Priority: Must
      Acceptance: вывод «smoke <версия>», код выхода 0
поправить уже данный ответ: dfl answer --question <id вопроса> --supersede --file f
(команду `discovery answer …` выше не запускайте — dfl answer сделает это сам)"""
CHAT_HARNESS_REFUSAL = (
    "интервью разговором требует harness.author = claude — используйте "
    "dfl answer --file answer.yaml"
)
NO_TTY_REFUSAL = "без терминала: dfl answer --file answer.yaml"
NO_INTERVIEW_REFUSAL = "интервью не идёт — начните: dfl run --subject '<тема>'"
CHAT_STOPPED = "интервью прервано — продолжить: dfl run"
INTERRUPTED = "прервано"
# причина стопа раннера по findings: `_interview_after_reply`, ветка 10/11
INCOMPLETE_STOP_RE = re.compile(r"(status|brief) вернул 1[01]: ")
INTERVIEW_COMPLETED_REFUSAL = (
    "интервью завершено, бриф уже собран — поправка в бриф не попадёт"
)
INIT_FILES = (
    "profiles/team-exp.yaml",
    "scripts/review/local.sh",
    "spec-runner.config.yaml",
)
# команды, исполняющие шаг конвейера или discovery: только они пишут лог вызова
LOGGED_COMMANDS = frozenset({"run", "answer", "correct", "init-repo"})
LOG_HINT = "подробно: dfl log"
BRIEF_READY_HINT = "dfl run — откроет PR брифа (или покажет открытый) на ваш мерж"
# статусы, после которых конвейер стоит до действия человека (кроме URL
# проверенного PR и остановленной цепочки — их узнают по `ops`)
HUMAN_STEP_STATUSES = frozenset({"brief_ready", "waiting_human_merge"})
NOT_CONVERGED_HINT = (
    "дальше: dfl run — ещё круг (платные вызовы), правка файла руками или dfl abandon"
)
SCREEN_MARK = "уровень 3: вывод раннера шёл на экран"
TRACK_QUESTION = "Что это за проект?"
TRACK_OPTIONS = (
    "Личный — для себя (возможно, с близким кругом)",
    "Enterprise — заказчик, команда, регламенты",
)
TRACKS = ("personal", "enterprise")  # в порядке `TRACK_OPTIONS`
TRACK_RETRY = "ответьте номером: 1 или 2"
TRACK_STOPPED = "прогон не начат — повторите dfl run --subject '<тема>'"
TRACK_PERSONAL_TAKEN = (
    "тема «{subject}» уже есть у enterprise-конвейера (идёт или закончен), "
    "--track personal не применим — продолжить её: dfl run --subject "
    "'{subject}' без --track; личный проект заведите под другой темой "
    "(или сначала dfl abandon)"
)
ABANDON_ENTERPRISE_LEFT = (
    "незаконченный enterprise-конвейер репо не тронут — бросить и его: "
    "повторите dfl abandon"
)
ABANDON_ENTERPRISE_UNKNOWN = (
    "enterprise-конвейеры репо не проверены — журнал прогона не читается "
    "(dfl status назовёт его)"
)
TRACK_ENTERPRISE_TAKEN = (
    "тема «{subject}» ведётся личным путём (прогон {run_id}), --track enterprise "
    "не применим — продолжить её: dfl run без --track; enterprise-конвейер для "
    "неё — сначала dfl abandon, затем dfl run --subject '<тема>' --track enterprise"
)
TRACK_NEEDS_SUBJECT = (
    "--track требует --subject: активного личного прогона в репо нет — "
    "dfl run --subject '<тема>' --track personal"
)
REVIEW_FIXED = (
    "этапы с паузой на просмотр зафиксированы при старте прогона: {recorded} — "
    "продолжить: dfl run без --review; другой выбор — сначала dfl abandon, "
    "затем dfl run --subject '<тема>' --review <этапы>"
)
TRACK_NO_TTY_HINT = (
    "тип проекта не спрошен (нет терминала) — enterprise; личный проект: "
    "dfl run --subject '…' --track personal"
)
#: хэши файлов волны на момент показа паузы (экран или ссылки без терминала):
#: «дальше» после ручной правки сверяется с ними (final review #768, I1)
REVIEW_SHOWN = "review-shown.json"
#: узлы паузы волны, на которые «дальше» уже получено без терминала (#766)
REVIEW_ACKED = "review-acked.json"
OWNER_REVIEW_PAUSED = "просмотр этапа «{label}» прерван — продолжить: dfl run"
OWNER_NEEDS_CLAUDE = (
    "замечания на паузе поддерживаются только с автором claude "
    "(harness.author = claude); откройте {links} и продолжите: dfl run"
)

#: этапы с паузой по пути (`--track`); без пути — оба
TRACK_STAGES = {"personal": rp.PERSONAL_STAGES, "enterprise": rp.ENTERPRISE_STAGES}
ALL_STAGES = tuple(dict.fromkeys(rp.PERSONAL_STAGES + rp.ENTERPRISE_STAGES))
REVIEW_NEEDS_CLAUDE = (
    "--review: пауза на просмотр этапа работает только с автором claude "
    "(harness.author = claude) — поставьте его в конфиге или запустите без --review"
)
REVIEW_CONFIG_DROPPED = (
    "[review] pause_after не действует: пауза на просмотр этапа работает только "
    "с автором claude (harness.author = claude) — прогон идёт без пауз"
)

# лог текущего вызова; путь переживает закрытие лога — его читает `_record`,
# который `cli` выполняет после выхода из `main`
_LOG: runlog.RunLog | None = None
_LOG_PATH = ""


class DflError(Exception):
    """Отказ `dfl` с причиной и следующим шагом (код 2)."""


# Отказы с причиной «причина — что сделать»: печатаются блоком `refusal`.
REFUSALS: tuple[type[Exception], ...] = (
    DflError,
    df_config.ConfigError,
    user_targets.TargetsError,
    InitError,
)


def cli(argv: list[str] | None = None) -> int:
    """Точка входа обёртки `dfl`: вывод дублируется в запись сбоя.

    Неперехваченное исключение печатается traceback'ом, как прежде, с кодом 1,
    и последней строкой — подсказка `dfl report`. Ctrl-C — одна строка
    «прервано», код 130, без трассировки и записи сбоя. Запись сбоя — после
    выхода из `tee_output`: только тогда вывод дочитан целиком."""
    pending: list[Callable[[list[str]], None]] = []
    with tee_output() as tail:
        try:
            code = main(argv, defer=pending)
        except KeyboardInterrupt:
            print(f"\n{INTERRUPTED}", file=sys.stderr)
            code = 130
        except Exception:  # noqa: BLE001 — верхний уровень: traceback, код 1
            traceback.print_exc()
            print(REPORT_HINT, file=sys.stderr)
            code = 1
    for job in pending:
        job(tail.lines())
    return code


def main(
    argv: list[str] | None = None,
    *,
    defer: list[Callable[[list[str]], None]] | None = None,
) -> int:
    """CLI: `--cwd <dir>` и `run` | `answer` | `interview` | `correct` | `abandon`
    | `status` | `init-repo` | `report` | `log`.

    Ненулевой код и исключение пишут запись сбоя (`lite.failure_log`);
    ненулевой код печатает `подробно: dfl log` (вызов писал лог) и подсказку
    `dfl report` (уровень ≥ 1 или без лога). С
    `defer` запись откладывается: `cli` выполнит её с полным хвостом вывода;
    без него — пишется сразу с пустым хвостом."""
    global _LOG_PATH
    raw = list(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(raw)
    _set_level(args)
    _LOG_PATH = ""
    cwd = Path(args.cwd)
    if args.command == "report":  # до режима test: сломанный конфиг — тоже сбой
        return run_report(cwd, print_only=args.print_only, with_log=args.with_log)
    if args.command == "log":  # как report: без режима test, лога и регистрации
        return run_log(cwd, list_only=args.list_only, nth=args.nth)
    before = fl.snapshot_runs()

    def record(code: int, exc: BaseException | None) -> None:
        job = functools.partial(_record, args, cwd, raw, code, exc, before)
        if defer is None:
            job([])
        else:
            defer.append(job)

    code = 1
    try:
        try:
            code, refused = _dispatch(args, cwd, raw)
        except KeyboardInterrupt:
            code = 130
            raise
        except Exception as exc:
            record(1, exc)
            raise
        if code != 0:
            record(code, refused)
            _failure_hint()
        return code
    finally:
        _close_log(code)


def run_log(cwd: Path, *, list_only: bool, nth: int) -> int:
    """`dfl log`: последний лог (или N-й с конца) целиком либо список логов."""
    infos = runlog.list_logs(cwd)
    if not infos:
        say("логов нет")
        return 0
    if list_only:
        for info in reversed(infos):
            say(_list_line(info))
        return 0
    if not 1 <= nth <= len(infos):
        message = (
            f"логов {len(infos)}, а запрошен {nth}-й с конца — "
            "dfl log --list покажет все"
        )
        say(refusal("dfl log", message), 0, err=True)
        return 2
    info = infos[-nth]
    say(runlog.read_log(info.path).rstrip("\n"))
    if info.code:
        say(REPORT_HINT)
    return 0


def _list_line(info: runlog.LogInfo) -> str:
    stamp = datetime.strptime(info.path.name[:15], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
    code = "—" if info.code is None else info.code
    return f"{stamp.astimezone():%Y-%m-%d %H:%M:%S} {info.command} код {code}"


def _set_level(args: argparse.Namespace) -> None:
    """Уровень подробности: флаги `-v` до и после подкоманды складываются;
    без флагов — `DFL_VERBOSITY` (неверное значение — одна строка
    предупреждения и уровень 0)."""
    flags = (args.verbose_root or 0) + (args.verbose or 0)
    if args.verbose_root is not None or args.verbose is not None:
        set_verbosity(flags)
        return
    level, warning = verbosity_from_env(os.environ)
    set_verbosity(level)
    if warning is not None:
        say(warning, 0, err=True)


def _failure_hint() -> None:
    """Ненулевой код: с логом — `подробно: dfl log` (и `dfl report` с `-v`),
    без лога — прежняя подсказка `dfl report`."""
    if not _LOG_PATH:
        say(REPORT_HINT, 0, err=True)
        return
    say(LOG_HINT, 0, err=True)
    say(REPORT_HINT, 1, err=True)


def _open_log(args: argparse.Namespace, cwd: Path, raw: list[str]) -> None:
    """Лог вызова для `LOGGED_COMMANDS`; не пишется — предупреждение, без лога."""
    global _LOG, _LOG_PATH
    if args.command not in LOGGED_COMMANDS:
        return
    header = f"dfl {' '.join(_public_argv(raw))} уровень {verbosity()}"
    last = fl.load(cwd)
    protect = Path(last.log_path) if last is not None and last.log_path else None
    try:
        _LOG = runlog.open_log(cwd, args.command, header, protect=protect)
    except OSError as err:
        say(f"лог не пишется: {err}", 0, err=True)
        return
    _LOG_PATH = str(_LOG.path)
    set_sink(_LOG.fh)
    if verbosity() >= runlog.PASSTHROUGH_LEVEL:
        say(SCREEN_MARK, 4)


def _close_log(code: int) -> None:
    """Код выхода в лог, лог закрыт; путь остаётся в `_LOG_PATH`."""
    global _LOG
    set_sink(None)
    if _LOG is None:
        return
    log, _LOG = _LOG, None
    try:
        runlog.close_log(log, code)
    except OSError as err:
        say(f"лог не дописан: {err}", 0, err=True)


def _record(
    args: argparse.Namespace,
    cwd: Path,
    raw: list[str],
    code: int,
    exc: BaseException | None,
    before: fl.RunsSnapshot,
    lines: list[str],
) -> None:
    """Записать сбой; собственная ошибка записи — одна строка, не traceback."""
    try:
        run_ids, note = fl.changed_runs(before, fl.snapshot_runs(), cwd)
        cause = fl.cause_from(exc, run_ids, lines, refusals=REFUSALS)
        fl.write(
            cwd,
            fl.FailureRecord(
                command=args.command,
                argv=_public_argv(raw),
                exit_code=code,
                time=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                output_tail=lines,
                run_ids=run_ids,
                runs_note=note,
                cwd_repo=_origin_slug(cwd),
                cause=cause,
                log_path=_LOG_PATH,
                verbosity=verbosity(),
            ),
        )
    except OSError as err:
        print(f"запись сбоя не удалась: {err}", file=sys.stderr)


def _public_argv(raw: list[str]) -> list[str]:
    """argv без `--cwd`; у `--file` — только имя файла (обе формы: `--x v`, `--x=v`)."""
    out: list[str] = []
    skip = False
    for i, arg in enumerate(raw):
        if skip:
            skip = False
            continue
        if arg == "--cwd":
            skip = True
            continue
        if arg.startswith("--cwd="):
            continue
        if arg.startswith("--file="):
            arg = "--file=" + Path(arg.removeprefix("--file=")).name  # noqa: PLW2901
        elif i > 0 and raw[i - 1] == "--file":
            arg = Path(arg).name  # noqa: PLW2901
        out.append(arg)
    return out


def _origin_slug(cwd: Path) -> str:
    try:
        done = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        return (
            user_targets.slug_from_url(done.stdout.strip())
            if done.returncode == 0
            else ""
        )
    except (OSError, ValueError, user_targets.TargetsError):
        return ""


def _dispatch(
    args: argparse.Namespace, cwd: Path, raw: list[str]
) -> tuple[int, BaseException | None]:
    """Тело подкоманды: (код, отказ-исключение или `None`).

    Лог вызова открывается после строки режима test (её отказ лога не пишет)."""
    try:
        _require_test_mode(
            status_header=args.command == "status",
            quiet=args.command == "pending" and args.json,
        )
        _open_log(args, cwd, raw)
        if args.command == "status":
            return status(cwd), None
        if args.command == "init-repo":
            return init_repo(cwd, DEVTOOLS, real_gh), None
        if args.command == "answer":
            code = answer(
                cwd,
                Path(args.file) if args.file else None,
                args.subject,
                args.stakeholder,
                question=args.question,
                supersede=args.supersede,
            )
            return code, None
        if args.command == "abandon":
            return abandon(cwd, args.subject), None
        if args.command == "interview":
            return interview(cwd, args.subject), None
        if args.command == "pending":
            return pending_cmd(cwd, args.subject, as_json=args.json), None
        if args.command == "reply":
            return reply_cmd(cwd, args.subject, args.id, _reply_text(args)), None
        if args.command == "correct":
            return correct(cwd, args.text, args.subject), None
        code = run(
            cwd, args.subject, args.stakeholder, track=args.track, review=args.review
        )
        return code, None
    except REFUSALS as exc:
        say(refusal(f"dfl {args.command}", str(exc)), 0, err=True)
        return 2, exc


def _reply_text(args: argparse.Namespace) -> str:
    """Текст ответа `dfl reply`: `--text` или содержимое `--file`."""
    if args.text is not None:
        return str(args.text)
    try:
        return Path(args.file).read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise DflError(f"файл ответа не прочитан: {exc}") from exc


def _verbose_parent(dest: str) -> argparse.ArgumentParser:
    """`-v` (счётчик): до подкоманды и после неё — разные `dest`, складываются."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "-v",
        dest=dest,
        action="count",
        help="подробнее: -v подсказки, -vv состояние, -vvv всё как раньше",
    )
    return parent


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dfl", parents=[_verbose_parent("verbose_root")]
    )
    parser.add_argument("--cwd", required=True, help="каталог репо цели")
    sub = parser.add_subparsers(dest="command", required=True)
    add = functools.partial(sub.add_parser, parents=[_verbose_parent("verbose")])
    p_run = add("run", help="следующий шаг конвейера")
    p_run.add_argument("--subject", help="тема нового прогона")
    p_run.add_argument("--stakeholder", default="customer")
    p_run.add_argument(
        "--track", choices=TRACKS, help="тип проекта (без него — вопрос)"
    )
    p_run.add_argument(
        "--review",
        help="этапы с паузой на просмотр: через запятую, all или none "
        "(без него — из конфига); только для нового прогона",
    )
    p_ans = add("answer", help="ответ на вопрос интервью и dfl run")
    p_ans.add_argument(
        "--file",
        help="YAML-файл ответа (text: и entries:); без него — интервью разговором",
    )
    p_ans.add_argument("--question", help="id вопроса (по умолчанию — текущий)")
    p_ans.add_argument(
        "--supersede",
        action="store_true",
        help="заменить уже данный ответ на вопрос --question",
    )
    p_ans.add_argument("--subject", help="тема конвейера, если их несколько")
    p_ans.add_argument("--stakeholder", default="customer")
    p_ab = add("abandon", help="бросить незаконченный конвейер")
    p_ab.add_argument("--subject", help="тема конвейера, если их несколько")
    p_iv = add("interview", help="что собрано в интервью")
    p_iv.add_argument("--subject", help="тема конвейера, если их несколько")
    p_cor = add("correct", help="поправить собранное текстом")
    p_cor.add_argument("text", help="поправка, например: не раз в неделю, а в месяц")
    p_cor.add_argument("--subject", help="тема конвейера, если их несколько")
    p_pen = add("pending", help="вопрос, на котором прогон ждёт ответа")
    p_pen.add_argument("--json", action="store_true", help="запись как JSON")
    p_pen.add_argument("--subject", help="тема конвейера, если их несколько")
    p_rpl = add("reply", help="ответ на ждущий вопрос (затем dfl run)")
    p_rpl.add_argument("--id", required=True, help="id вопроса из dfl pending")
    src = p_rpl.add_mutually_exclusive_group(required=True)
    src.add_argument("--text", help="текст ответа")
    src.add_argument("--file", help="файл с текстом ответа")
    p_rpl.add_argument("--subject", help="тема конвейера, если их несколько")
    add("status", help="прогоны репо и что дальше")
    add("init-repo", help="PR с шаблонами DarkFactory в репо")
    p_rep = add(
        "report", help="отчёт о последнем сбое — разработчикам (issue в devtools)"
    )
    p_rep.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        help="только показать, не отправлять",
    )
    p_rep.add_argument(
        "--with-log",
        action="store_true",
        help="хвост из лога вызова — с выводом git, gh и ревью",
    )
    p_log = add("log", help="лог последнего вызова dfl")
    p_log.add_argument("--list", dest="list_only", action="store_true")
    p_log.add_argument(
        "-n", dest="nth", type=int, default=1, help="N-й лог с конца (1 — последний)"
    )
    return parser


def _require_test_mode(*, status_header: bool = False, quiet: bool = False) -> None:
    """Конфиг обязан быть в режиме test (иначе отказ); печать баннера.

    Полный баннер — на уровне 3; у `dfl status` (`status_header`) — уже с
    уровня 1, вместе с путём конфига."""
    cfg = df_config.load()
    if cfg.mode != "test":
        raise DflError(
            f"dfl работает только в режиме test (mode = {cfg.mode!r}) — "
            f'поставьте mode = "test" в {df_config.canonical_config_path()} '
            "(install.sh существующий конфиг не переписывает) или работайте "
            "через make"
        )
    if quiet:  # машинный вывод (`pending --json`): без баннера в stdout
        return
    level = verbosity()
    full = level >= runlog.PASSTHROUGH_LEVEL or (status_header and level >= 1)
    banner = df_config.test_banner(cfg) if full else df_config.test_banner_line(cfg)
    if banner is not None:
        say(banner)
    if status_header:
        say(f"конфиг: {df_config.canonical_config_path()}", 1)
    say("")


def run(
    cwd: Path,
    subject: str | None,
    role: str,
    *,
    chat: bool = True,
    track: str | None = None,
    review: str | None = None,
) -> int:
    """Один шаг конвейера для репо в `cwd`; код шага или 0/2 самого `dfl`.

    Сначала — личный путь (`_personal`): активный личный прогон продолжается,
    новая тема начинается вопросом о типе проекта (или `track`). Иначе — шаг
    enterprise-конвейера: в терминале (и `chat`) при `harness.author = claude`
    ждущий ответа прогон ведётся интервью разговором; иначе — подсказка
    `dfl answer --file`. `review` — этапы с паузой нового прогона
    (`resolve_review`); на продолжении он обязан совпасть с записанным.
    С автором не claude выход с паузы невозможен — `--review` с этапами
    отказывает сразу (RF-a)."""
    if _review_selected(review, track) and not _author_is_claude():
        raise DflError(REVIEW_NEEDS_CLAUDE)  # RF-a: до регистрации и эффектов
    target = user_targets.register(cwd)
    code = _personal(target, subject, track, review)
    if code is not None:
        return code
    chatting = chat and _is_tty()
    if chatting and _author_is_claude():
        return _run_chatting(target, subject, role, review)
    code = _step(target, subject, role, chatting=False, review=review)
    if not _is_tty() and _owner_review_run(target.repo, subject) is not None:
        return _owner_review_rounds(target, subject, role, code, remote=True)
    if chatting and _waiting_run(target.repo, subject) is not None:
        raise DflError(CHAT_HARNESS_REFUSAL)
    _say_owner_review_wait(target.repo, subject, tty=chatting)
    return code


def _pending_dir(cwd: Path, subject: str | None) -> Path | None:
    """Каталог прогона, который продолжит `dfl run`: активный личный, иначе
    enterprise (engineer-прогон, если он есть); прогона нет — `None`."""
    target = user_targets.register(cwd)
    slug = user_targets.slug_from_url(target.repo_url)
    active = personal_state.active_for(slug, subject=subject)
    if active is not None:
        return personal_state.run_dir(active.run_id)
    state = _current_run(target.repo, subject)
    return rs.run_dir(state.run_id) if state is not None else None


def _pending_lines(rec: dict) -> list[str]:
    """Вывод `dfl pending` по записи `pending.json`."""
    lines = [str(rec.get("text", ""))]
    for n, option in enumerate(rec.get("options") or [], start=1):
        lines.append(f"  {n}. {option}")
    lines += [f"ссылка: {link}" for link in rec.get("links") or []]
    if rec.get("accepts"):
        lines.append(f"принимается: {rec['accepts']}")
    lines.append(f"ответ: dfl reply --id {rec.get('id', '')} --text '…'")
    return lines


def pending_cmd(cwd: Path, subject: str | None, *, as_json: bool) -> int:
    """`dfl pending`: вопрос, на котором прогон ждёт человека (код 0)."""
    run_dir = _pending_dir(cwd, subject)
    rec = pending.load(run_dir) if run_dir is not None else None
    if as_json:
        say(json.dumps(rec, ensure_ascii=False, indent=2))
        return 0
    if rec is None:
        say("вопросов нет")
        return 0
    for line in _pending_lines(rec):
        say(line)
    return 0


def reply_cmd(cwd: Path, subject: str | None, qid: str, text: str) -> int:
    """`dfl reply`: записать ответ на ждущий вопрос `qid`; дальше — `dfl run`."""
    run_dir = _pending_dir(cwd, subject)
    if run_dir is None:
        raise DflError(pending.NO_PENDING)
    try:
        with run_lock.run_lock(run_dir.name):
            pending.record(run_dir, qid, text)
    except run_lock.LockBusy as exc:
        raise DflError(f"{exc} — дождитесь конца dfl run и повторите") from exc
    except pending.PendingError as exc:
        raise DflError(str(exc)) from exc
    say("ответ записан — продолжить: dfl run")
    return 0


def resolve_review(flag: str | None, stages: tuple[str, ...]) -> tuple[str, ...]:
    """Этапы с паузой нового прогона: флаг `--review` сильнее конфига
    (`[review] pause_after`); имя конфига, чужое пути, молча отбрасывается,
    неизвестное флагу — отказ с перечнем допустимых. Паузы конфига при
    авторе не claude отбрасываются с предупреждением (флаг отказывает `run`). Значение каноническое —
    порядок этапов пути, без повторов: его записывают и с ним сравнивают."""
    if flag is not None:
        try:
            chosen = rp.parse_flag(flag, stages)
        except rp.ReviewError as exc:
            raise DflError(str(exc)) from exc
    else:
        names = getattr(df_config.load(), "review_pause_after", ())
        chosen = rp.from_config(tuple(names), stages)
        if chosen and not _author_is_claude():
            say(REVIEW_CONFIG_DROPPED)  # RF-a: не блокирует прогон
            return ()
    return tuple(stage for stage in stages if stage in chosen)


def _review_selected(flag: str | None, track: str | None = None) -> bool:
    """Флаг `--review` выбирает хоть один этап (`all` или непустой список, не
    `none`); неизвестное имя — отказ «неизвестный этап» раньше проверки
    автора. Перечень допустимых — этапы пути `track`; путь не выбран — по
    обоим путям раздельно."""
    if flag is None or flag.strip().casefold() == "none":
        return False
    if flag.strip().casefold() == "all":
        return True
    stages = TRACK_STAGES.get(track or "")
    try:
        return bool(rp.parse_flag(flag, stages or ALL_STAGES))
    except rp.ReviewError as exc:
        if stages:
            raise DflError(str(exc)) from exc
        names = [n.strip() for n in flag.split(",") if n.strip()]
        unknown = ", ".join(n for n in names if n not in ALL_STAGES)
        raise DflError(
            f"неизвестный этап: {unknown}; допустимы: личный путь: "
            f"{', '.join(rp.PERSONAL_STAGES)}; enterprise: "
            f"{', '.join(rp.ENTERPRISE_STAGES)}"
        ) from exc


def _check_review(
    flag: str | None, recorded: tuple[str, ...] | list[str], stages: tuple[str, ...]
) -> None:
    """Продолжение прогона: `--review`, расходящийся с записанным, — отказ."""
    if flag is None or resolve_review(flag, stages) == tuple(recorded):
        return
    raise DflError(REVIEW_FIXED.format(recorded=", ".join(recorded) or "без пауз"))


def _review_argv(stages: tuple[str, ...] | list[str]) -> list[str]:
    """`--review-pause a,b` для `spec_loop`; без пауз — argv прежний."""
    return ["--review-pause", ",".join(stages)] if stages else []


def _personal(
    target: user_targets.UserTarget,
    subject: str | None,
    track: str | None,
    review: str | None = None,
) -> int | None:
    """Код шага личного пути или `None` — дальше enterprise-путь.

    Без `subject` — активный личный прогон репо (RF-4: он важнее
    enterprise-прогонов). С `subject` — активный личный прогон той же темы;
    иначе новая тема: тип — `track`, вопрос (только в терминале и только для
    темы без enterprise-прогона), без терминала — enterprise, как прежде,
    с подсказкой про `--track personal`. Явный `--track`, противоречащий
    найденному (личный прогон при `enterprise`; `personal` без темы и без
    личного прогона), — отказ, а не молчаливое игнорирование."""
    slug = user_targets.slug_from_url(target.repo_url)
    active = personal_state.active_for(slug, subject=subject)
    if active is not None:
        if track == "enterprise":
            raise DflError(
                TRACK_ENTERPRISE_TAKEN.format(
                    subject=active.subject, run_id=active.run_id
                )
            )
        _check_review(review, active.review_pause, rp.PERSONAL_STAGES)
        return _advance_personal(active)
    if subject is None:
        if track == "personal":
            raise DflError(TRACK_NEEDS_SUBJECT)
        return None
    new = _new_subject(target.repo, subject)
    if track == "personal" and not new:
        raise DflError(TRACK_PERSONAL_TAKEN.format(subject=subject))
    if track is None and new:
        if not _track_tty():
            say(TRACK_NO_TTY_HINT)
            return None
        track = _ask_track(ic.stdin_read)
        if track is None:
            say(TRACK_STOPPED)
            return 0
    if track != "personal":
        return None
    run = personal_state.new_personal_run(
        subject,
        target.repo,
        slug,
        str(target.path),
        date.today(),
        resolve_review(review, rp.PERSONAL_STAGES),
        manual_checks=True,
    )
    personal_state.save(run)
    say(f"личный прогон {run.run_id}", 1)
    return _advance_personal(run)


def _track_tty() -> bool:
    """Можно спросить тип проекта: stdin — терминал (подменяется в тестах;
    отдельно от `_is_tty`, чтобы тесты разговора enterprise не получали
    вопроса)."""
    return sys.stdin.isatty()


def _new_subject(repo: str, subject: str) -> bool:
    """Темы нет среди customer-прогонов репо (их продолжение — без вопроса)."""
    runs, _ = _repo_runs(repo)
    return not any(_frame(s) == "customer" and s.subject == subject for s in runs)


def _ask_track(read: ic.Read) -> str | None:
    """Вопрос о типе проекта; ответ номером. `None` — EOF или `/стоп`."""
    while True:
        say(TRACK_QUESTION)
        for n, option in enumerate(TRACK_OPTIONS, start=1):
            say(f"{n}. {option}")
        text = ic.read_answer(read, quick=str.isdigit)
        if text is None:
            return None
        text = text.strip()
        if text.isdigit() and 1 <= int(text) <= len(TRACKS):
            return TRACKS[int(text) - 1]
        say(TRACK_RETRY)


def _advance_personal(run: personal_state.PersonalRun) -> int:
    """`advance` с настоящими терминалом, агентом и эффектами.

    Под `runlog.captured` — каждый эффект (git/gh/spec-runner, автор узла,
    edge-check, агент), а не весь `advance`: вопросы интервью и разбора
    замечаний (`say` + `read`) идут на экран, как у `_run_chatting`, где
    разговор тоже вне перехвата."""
    return personal_flow.advance(
        run,
        cast(Ops, _QuietOps(RealOps())),
        read=ic.stdin_read,
        agent=_quiet(st.claude_agent(df_config.load().author_model)),
        launch=_quiet(launch_author),
        check=_quiet(run_check),
    )


def _quiet[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """`fn`, вызов которого идёт под `runlog.captured` (вывод потомков — в лог)."""

    @functools.wraps(fn)
    def call(*args: P.args, **kwargs: P.kwargs) -> R:
        with runlog.captured(_LOG, verbosity()):
            return fn(*args, **kwargs)

    return call


class _QuietOps:
    """Обёртка `Ops`: каждый метод — под `runlog.captured` (`_quiet`)."""

    def __init__(self, ops: object) -> None:
        self._ops = ops

    def __getattr__(self, name: str) -> object:
        attr = getattr(self._ops, name)
        return _quiet(attr) if callable(attr) else attr


def _run_chatting(
    target: user_targets.UserTarget,
    subject: str | None,
    role: str,
    review: str | None = None,
) -> int:
    """Шаг конвейера; ждёт ответа — интервью разговором и снова шаг.

    Прогон, остановленный неполным интервью (findings 10/11), тоже ведётся
    разговором (добор тем), но один раз за вызов: человек мог оставить темы
    пустыми. Подсказки раннера (`discovery answer …`, `make …`) не
    печатаются: вопрос показывает сам цикл."""
    with runner.quiet_interview_hints():
        code = _step(target, subject, role, chatting=True, review=review)
        incomplete = True
        while (state := _waiting_run(target.repo, subject, incomplete)) is not None:
            incomplete = False
            if _converse(state) is ic.ChatResult.STOPPED:
                say(CHAT_STOPPED)
                return 0
            code = _step(target, subject, role, chatting=True, review=review)
    code = _resolve_rounds(target, subject, role, code)
    return _owner_review_rounds(target, subject, role, code)


def _resolve_rounds(
    target: user_targets.UserTarget, subject: str | None, role: str, code: int
) -> int:
    """Стоп edge-check с находками — разбор разговором и снова шаг, не больше
    `er.MAX_ROUNDS` кругов за вызов; не сошлось — сводка (спека Ц4).

    Разбор не начался или остановлен — прежняя подсказка «что править»
    (`_report_step` её отложил) и код шага."""
    seen: set[tuple[str, str, str]] = set()
    calls = er.PaidCalls()
    rounds = 0
    while (state := _review_run(target.repo, subject)) is not None:
        keys = er.stop_keys(state)
        if rounds == er.MAX_ROUNDS and keys:
            _say_not_converged(keys, seen, calls)
            return code
        outcome = (
            _resolve(state, rounds + 1, calls)
            if rounds < er.MAX_ROUNDS
            else er.Outcome.NOT_APPLICABLE
        )
        if outcome not in (er.Outcome.REVISED, er.Outcome.OVERRIDDEN):
            _say_next(state)
            if calls.spent():
                say(calls.line())
            return code
        rounds += 1
        seen |= keys
        code = _step(target, subject, role, chatting=True)
        if _edge_ran(state):
            calls.check += 1
        say(calls.line(), 1)
    return code


def _edge_ran(state: rs.RunState) -> bool:
    """Повторный шаг дошёл до edge-check волны `state`: op `edge-<w>` есть.

    `runner.resume` из `stopped_review` снимает этот op и файл стопа
    (`_STOPPED_RESET_OPS`, `_drop_findings_of`), заново op создаёт только
    `_step_edge`; стоп раньше (гейт, автор, коммит) — платной проверки не
    было."""
    try:
        now = rs.load(state.run_id)
    except (OSError, ValueError, TypeError):
        return False
    return runner.wave_key(state, "edge") in now.ops


def _review_run(repo: str, subject: str | None) -> rs.RunState | None:
    """Прогон конвейера (engineer, иначе customer) в `stopped_review` или `None`."""
    state = _current_run(repo, subject)
    return state if state is not None and state.status == sf.EDGE else None


def _current_run(repo: str, subject: str | None) -> rs.RunState | None:
    """Текущий прогон конвейера темы: engineer, иначе customer; нет — `None`."""
    runs, _ = _repo_runs(repo)
    try:
        cust = _select_customer(runs, subject)
    except DflError:
        return None
    if cust is None:
        return None
    return _engineer_of(cust, runs) or cust


# --- пауза просмотра enterprise-волны (#768) ---------------------------------


def _owner_review_rounds(
    target: user_targets.UserTarget,
    subject: str | None,
    role: str,
    code: int,
    *,
    remote: bool = False,
) -> int:
    """Пауза просмотра волны (`WAITING_OWNER_REVIEW`): замечания владельца
    к выбранным узлам, затем op паузы и шаг (resume) — пока прогон снова
    встаёт на паузу. Кругов без ограничения: каждый — решение человека.

    Правка сбрасывает проверку волны: повторный шаг проверяет её заново
    (стоп проверки — разбор `_resolve_rounds`), и пауза показывается снова.
    `/стоп` — код 0, стоп правки — 1; число платных вызовов печатается.

    `remote` — без терминала (#766): «дальше» приходит через `dfl reply`
    (`pending.asker` каталога прогона), вопрос ждёт — код 0."""
    calls = er.PaidCalls()
    while (state := _owner_review_run(target.repo, subject)) is not None:
        ask = pending.asker(rs.run_dir(state.run_id)) if remote else None
        stopped = _owner_pause(state, calls, ask)
        if stopped is not None:
            if calls.spent():
                say(calls.line())
            return stopped
        revised = _pause_revised(state.run_id)
        code = _step(target, subject, role, chatting=not remote)
        if revised and _edge_ran(state):
            calls.check += 1
        if calls.spent():
            say(calls.line())
        if not remote:
            code = _resolve_rounds(target, subject, role, code)
    return code


def _owner_review_run(repo: str, subject: str | None) -> rs.RunState | None:
    """Прогон на паузе просмотра, ещё не пройденной, или `None`."""
    state = _current_run(repo, subject)
    if state is None or state.status != runner.WAITING_OWNER_REVIEW:
        return None
    return None if runner.review_pause_passed(state) else state


def _pause_files(state: rs.RunState) -> list[tuple[str, Path]]:
    """Выбранные для просмотра узлы волны и их файлы в рабочем дереве."""
    bundle = Path(state.target_dir) / state.bundle_dir
    return [
        (kind, bundle / filename)
        for _key, kind, filename in runner._author_steps_for(state)
        if kind in state.review_pause
    ]


def _say_owner_review_wait(repo: str, subject: str | None, *, tty: bool) -> None:
    """Пауза без цикла замечаний не пропускается: подсказка со ссылками на
    файлы волны — без терминала или (`tty`) с автором не claude."""
    state = _owner_review_run(repo, subject)
    if state is None:
        return
    files = _pause_files(state)
    _remember_shown(state, files)
    links = orv.links(p for _k, p in files)
    if tty:
        say(OWNER_NEEDS_CLAUDE.format(links=links))
        return
    label = ", ".join(rp.STAGE_LABELS[kind] for kind, _path in files)
    say(orv.NO_TTY.format(label=label, links=links))


def _owner_pause(
    state: rs.RunState, calls: er.PaidCalls, ask: pending.Ask | None = None
) -> int | None:
    """Один показ паузы: узлы волны по очереди (W4 — оба подряд). `None` —
    пауза пройдена и op записан; иначе код выхода."""
    files = _pause_files(state)
    _remember_shown(state, files)
    for stage, path in files:
        stopped = _owner_node(state, stage, path, calls, ask)
        if stopped is not None:
            return stopped
    _write_pause_op(state, complete=True)
    return None


def _owner_node(
    state: rs.RunState,
    stage: str,
    path: Path,
    calls: er.PaidCalls,
    ask: pending.Ask | None = None,
) -> int | None:
    """Узел на паузе: недоигранные замечания (RF-1: без вопроса) или ответ
    на экране паузы; правка принята — op помечен «правлено», затем
    замечания «применены». «Дальше» после ручной правки файла (хэш ≠
    снятому при показе) — тоже «правлено»: перепроверка и пауза снова.
    `ask` — без терминала (#766): «дальше» через `dfl reply`, замечания
    пока только в терминале. `None` — дальше; иначе код (0 — пауза)."""
    rd = rs.run_dir(state.run_id)
    label = rp.STAGE_LABELS[stage]
    batch = rp.pending(rd, stage)
    if batch and ask is not None:
        say(orv.NO_TTY.format(label=label, links=orv.links([path])))
        return 0
    if not batch:
        answer = _owner_answer(state, stage, label, path, ask)
        if answer is None:
            return 0
        if not answer:
            if _edited_on_pause(state, stage, path):
                _write_pause_op(state, complete=False)  # перепроверка и пауза снова
            return None
        rp.record(rd, stage, answer)
        batch = rp.pending(rd, stage)
    say(f"замечания к этапу «{label}» — правка")
    outcome, detail = orv.revise_by_owner(
        path,
        orv.decisions(batch),
        revise=functools.partial(_owner_revise, state, stage, path, calls),
        read=ic.stdin_read,
    )
    if outcome == orv.ACCEPTED:
        _write_pause_op(state, complete=False)  # до «применено»: правка не теряется
        rp.mark_applied(rd, stage, len(batch))
        return None
    if outcome == orv.PAUSED:
        say(OWNER_REVIEW_PAUSED.format(label=label))
        return 0
    if outcome == orv.UNCLEAR:
        rp.mark_dropped(rd, stage, len(batch))
        say(orv.UNCLEAR_REMARK.format(ids=detail))
        return 1
    say(f"{detail} — продолжить: dfl run")
    return 1


def _owner_answer(
    state: rs.RunState,
    stage: str,
    label: str,
    path: Path,
    ask: pending.Ask | None,
) -> str | None:
    """Ответ на экране паузы узла: `""` — «дальше»; `None` — пауза (строка
    о ней уже напечатана). Терминал: записанный `dfl reply` берётся вместо
    ввода, живой ответ снимает вопрос (терминал побеждает, спека §5.1)."""
    rd = rs.run_dir(state.run_id)
    if ask is not None:
        return _owner_answer_remote(state, stage, label, path, ask)
    q = rp.question(stage, label, [path])
    if pending.take(rd, q) is not None:
        return ""
    answer = rp.ask(label, [path], read=ic.stdin_read)
    if answer is None:
        say(OWNER_REVIEW_PAUSED.format(label=label))
        return None
    pending.clear(rd)
    return answer


def _acked_path(state: rs.RunState) -> Path:
    return rs.run_dir(state.run_id) / REVIEW_ACKED


def _acked(state: rs.RunState) -> list[str]:
    """Узлы волны, на которые «дальше» уже получено (W4: по одному на вызов)."""
    try:
        data = json.loads(_acked_path(state).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or data.get("wave") != state.wave:
        return []
    stages = data.get("stages")
    return [str(x) for x in stages] if isinstance(stages, list) else []


def _owner_answer_remote(
    state: rs.RunState,
    stage: str,
    label: str,
    path: Path,
    ask: pending.Ask,
) -> str | None:
    """«Дальше» узла без терминала: уже полученное запоминается в
    `review-acked.json` — иначе второй узел W4 вернул бы первый на вопрос."""
    acked = _acked(state)
    if stage in acked:
        return ""
    answer = rp.ask_remote(stage, label, [path], ask=ask)
    if answer is None:
        say(pending.wait_text(rs.run_dir(state.run_id)))
        return None
    _acked_path(state).write_text(
        json.dumps({"wave": state.wave, "stages": [*acked, stage]}) + "\n",
        encoding="utf-8",
    )
    return answer


def _owner_revise(
    state: rs.RunState,
    stage: str,
    path: Path,
    calls: er.PaidCalls,
    before: str,
    decisions: str,
) -> str | None:
    """Один вызов автора по замечаниям владельца; сбой — причина."""
    calls.revise += 1
    call = AuthorCall(
        run_id=state.run_id,
        run_dir=os.path.realpath(rs.run_dir(state.run_id)),
        target_dir=state.target_dir,
        bundle_dir=state.bundle_dir,
        filename=path.name,
    )
    outcome = _revise(call, stage, state.subject, before, "нет", decisions)
    if outcome.code != 0:
        return f"правка {path.name} не состоялась: {outcome.reason or outcome.code}"
    return None


def _revise(
    call: AuthorCall,
    kind: str,
    subject: str,
    current: str,
    findings: str,
    decisions: str,
) -> LaunchOutcome:
    """Граница автора (подменяется в тестах)."""
    return RealOps().revise(call, kind, subject, current, findings, decisions)


def _pause_revised(run_id: str) -> bool:
    """Op паузы текущей волны несёт `revised` (правки за эту паузу)."""
    state = rs.load(run_id)
    op = state.ops.get(runner.wave_key(state, runner.REVIEW_PAUSE)) or {}
    return bool(op.get("revised"))


def _digest(path: Path) -> str:
    """sha256 файла; нет файла — пустая строка."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _shown_digests(state: rs.RunState) -> dict[str, str]:
    """Хэши узлов текущей волны, снятые при первом показе паузы."""
    try:
        data = json.loads((rs.run_dir(state.run_id) / REVIEW_SHOWN).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("wave") != state.wave:
        return {}
    digests = data.get("digests")
    return digests if isinstance(digests, dict) else {}


def _remember_shown(state: rs.RunState, files: list[tuple[str, Path]]) -> None:
    """Запомнить хэши узлов при первом показе паузы волны (повторный показ
    их не переписывает: правка между показами — тоже правка). Сбрасывает
    запись `_write_pause_op` при пройденной паузе."""
    digests = _shown_digests(state)
    for stage, path in files:
        digests.setdefault(stage, _digest(path))
    (rs.run_dir(state.run_id) / REVIEW_SHOWN).write_text(
        json.dumps({"wave": state.wave, "digests": digests}) + "\n",
        encoding="utf-8",
    )


def _edited_on_pause(state: rs.RunState, stage: str, path: Path) -> bool:
    """Файл узла изменён с первого показа паузы (ручная правка владельца)."""
    shown = _shown_digests(state).get(stage)
    return shown is not None and shown != _digest(path)


def _write_pause_op(state: rs.RunState, *, complete: bool) -> None:
    """Op паузы волны под блокировкой прогона: `started` с `revised=True`
    сразу после принятой правки (переживает `/стоп` до конца паузы),
    `completed` — когда пауза пройдена; `revised` не сбрасывается."""
    try:
        with run_lock.run_lock(state.run_id):
            now = rs.load(state.run_id)
            if now.status != runner.WAITING_OWNER_REVIEW or now.wave != state.wave:
                raise DflError(
                    f"прогон {state.run_id} уже не на паузе просмотра волны "
                    f"{state.wave} — повторите dfl run"
                )
            key = runner.wave_key(now, runner.REVIEW_PAUSE)
            revised = not complete or bool((now.ops.get(key) or {}).get("revised"))
            if complete:
                rs.op_complete(now, key, revised=revised)
                (rs.run_dir(now.run_id) / REVIEW_SHOWN).unlink(missing_ok=True)
            else:
                rs.op_start(now, key, revised=True)
            _acked_path(now).unlink(missing_ok=True)
    except run_lock.LockBusy as exc:
        raise DflError(f"{exc} — дождитесь конца dfl run и повторите") from exc


def _say_not_converged(
    keys: set[tuple[str, str, str]],
    seen: set[tuple[str, str, str]],
    calls: er.PaidCalls,
) -> None:
    """Сводка предела кругов: что осталось и повторяется ли (devtools#728)."""
    named = ", ".join("/".join(k) for k in sorted(keys)) or "—"
    line = f"не сошлось за {er.MAX_ROUNDS} круга: {named}"
    if keys & seen:
        line += "; повторяются — признак devtools#728"
    say(line)
    say(calls.line())
    say(NOT_CONVERGED_HINT)


def _resolve(state: rs.RunState, round_no: int, calls: er.PaidCalls) -> er.Outcome:
    """Круг разбора с настоящими терминалом, агентом и границей автора."""
    return er.resolve(
        state,
        read=ic.stdin_read,
        agent=st.claude_agent(df_config.load().author_model),
        revise=RealOps().revise,
        round_no=round_no,
        calls=calls,
    )


def _is_tty() -> bool:
    """stdin — терминал (подменяется в тестах)."""
    return sys.stdin.isatty()


def _author_is_claude() -> bool:
    return df_config.load().author_harness == "claude"


def _waiting_run(
    repo: str, subject: str | None, incomplete: bool = False
) -> rs.RunState | None:
    """Прогон конвейера (engineer, иначе customer), ждущий ответа, или `None`;
    с `incomplete` — и остановленный неполным интервью."""
    runs, _ = _repo_runs(repo)
    try:
        cust = _select_customer(runs, subject)
    except DflError:
        return None
    if cust is None:
        return None
    state = _engineer_of(cust, runs) or cust
    if state.status == WAITING or (incomplete and _stopped_incomplete(state)):
        return state
    return None


def _stopped_incomplete(state: rs.RunState) -> bool:
    """Стоп стадии Need по findings discovery (10/11): интервью неполное."""
    reason = str((state.interview or {}).get("stop_reason", ""))
    return state.status == "stopped_interview" and bool(
        INCOMPLETE_STOP_RE.match(reason)
    )


def _converse(state: rs.RunState) -> ic.ChatResult:
    """Интервью разговором; сбой агента или состояния сессии — отказ `dfl`."""
    try:
        return _chat(state)
    except (ic.ChatError, InterviewStateError) as exc:
        raise DflError(str(exc)) from exc


def _chat(state: rs.RunState) -> ic.ChatResult:
    """Цикл интервью с настоящими терминалом, агентом и discovery."""
    cwd = rs.run_dir(state.run_id)
    return ic.chat(
        state,
        read=ic.stdin_read,
        agent=st.claude_agent(df_config.load().author_model),
        status=lambda session_id: _discovery_status(session_id, cwd),
        send=_sender(state),
        resend=_sender(state, supersede=True),
        load=load_session,
        upstream=lambda: _upstream_text(state),
    )


def _upstream_text(state: rs.RunState) -> str:
    """customer-бриф engineer-прогона (`upstream.md`); нет — пусто."""
    try:
        return (rs.run_dir(state.run_id) / iv.UPSTREAM_REL).read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _sender(
    state: rs.RunState, *, supersede: bool = False
) -> Callable[[str, str], iv.DiscoveryReply]:
    """`send(question_id, yaml)`: временный файл в каталоге прогона →
    `discovery answer --question <id>` (`--supersede` — для поправок), файл
    удаляется."""
    interview = state.interview or {}
    session_id = str(interview.get("session_id"))
    role = str(interview.get("stakeholder_role"))
    cwd = rs.run_dir(state.run_id)

    def send(question_id: str, payload: str) -> iv.DiscoveryReply:
        fd, name = tempfile.mkstemp(prefix="answer-", suffix=".yaml", dir=cwd)
        path = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            return _discovery_answer(
                session_id, role, path, cwd, question=question_id, supersede=supersede
            )
        finally:
            path.unlink(missing_ok=True)

    return send


def _step(
    target: user_targets.UserTarget,
    subject: str | None,
    role: str,
    *,
    chatting: bool,
    review: str | None = None,
) -> int:
    """Шаг enterprise-конвейера. Выбор этапов с паузой (`review`) пишется в
    новый customer-прогон и переходит в engineer-прогон (`_brief_step`)."""
    repo = target.repo
    loop = functools.partial(_loop, cwd=target.path, chatting=chatting)
    runs = _readable_runs(repo)
    cust = _select_customer(runs, subject)
    if cust is None:
        pause = resolve_review(review, rp.ENTERPRISE_STAGES)
        _require_init_files(target.path)
        return loop(
            ["--subject", str(subject), "--repo", repo, "--need", "--frame",
             "customer", "--stakeholder", role, "--brief-only",
             *_review_argv(pause)]
        )  # fmt: skip
    _check_review(review, cust.review_pause, rp.ENTERPRISE_STAGES)
    eng = _engineer_of(cust, runs)
    if eng is not None:
        return _engineer_step(eng, repo, loop)
    if cust.status != "brief_ready":
        return loop(
            ["--subject", cust.subject, "--repo", repo, "--run-id", cust.run_id]
        )
    return _brief_step(cust, role, loop)


def _loop(argv: list[str], cwd: Path, *, chatting: bool) -> int:
    """`spec_loop.main` под перехватом, затем итог шага по журналу прогона.

    Итог (`_outcome`) и вопрос интервью — уровень 0, подсказка `_next_hint` —
    1, состояние (`_state_lines`) — 2. `подробно: dfl log` при остановке с
    кодом 0 печатается здесь; при ненулевом коде — в `main`."""
    before = fl.snapshot_runs()
    before_ops = _ops_by_run() if verbosity() >= 2 else {}
    with runlog.captured(_LOG, verbosity()):
        code = spec_loop.main(argv)
    run_ids, note = fl.changed_runs(before, fl.snapshot_runs(), cwd)
    # `note` — шаг прогонов не менял, и `changed_runs` назвал свежий прогон
    # репо наугад: его итог (старая причина стопа) был бы ложным итогом шага.
    # Прогон, названный в `--run-id`, известен и так — его состояние и
    # следующий шаг печатаются без причины стопа (resume без записи run.json)
    state = _load_run(run_ids[-1]) if run_ids and not note else None
    unchanged = _argv_run(argv) if state is None else None
    if unchanged is not None:
        line = _unchanged_outcome(unchanged, code)
        ops = before_ops.get(unchanged.run_id, {})
        _report_step(unchanged, code, ops, chatting=chatting, line=line)
        return code
    if state is None:
        if code != 0:
            say(f"шаг завершился с кодом {code}")
        return code
    line = _outcome(state, code)
    _report_step(
        state, code, before_ops.get(state.run_id, {}), chatting=chatting, line=line
    )
    return code


def _argv_run(argv: list[str]) -> rs.RunState | None:
    """Прогон из `--run-id` в argv шага; нет флага или не читается — `None`."""
    if "--run-id" not in argv:
        return None
    i = argv.index("--run-id") + 1
    return _load_run(argv[i]) if i < len(argv) else None


def _unchanged_outcome(state: rs.RunState, code: int) -> str:
    """Итог шага, не изменившего прогон: статус без причины стопа."""
    line = f"прогон {state.run_id} шагом не изменён: {state.status}"
    return line if code == 0 else f"шаг завершился с кодом {code}; {line}"


def _report_step(
    state: rs.RunState,
    code: int,
    before_ops: dict[str, str],
    *,
    chatting: bool,
    line: str | None,
) -> None:
    """Итог шага по уровням для прогона `state`: строка итога `line`, вопрос,
    состояние, следующий шаг (на уровне 0, если он — действие человека).

    Пауза просмотра — без строки итога и следующего шага: за шагом идёт
    экран паузы `_owner_review_rounds` (разговор) или строка со ссылками
    `_say_owner_review_wait` (без терминала, автор не claude)."""
    screen = _owner_pause_next(state, code)
    if line is not None and not screen:
        say(line)
    stop = sf.load(state.run_id, state.status)
    for text, level in sf.shown(stop) if stop is not None else []:
        say(text, level)
    unread = state.status == WAITING and not chatting and not _say_question(state)
    stopped = state.status.startswith("stopped_")
    if (stopped or unread) and code == 0 and _LOG is not None:
        say(LOG_HINT)  # при code != 0 строку печатает `main`: одна на вызов
    if verbosity() >= 2:
        for text in _state_lines(before_ops, state):
            say(text, 2)
    if state.status == WAITING:
        return  # как отвечать — уже сказано (вопрос) или скажет цикл
    if chatting and state.status == sf.EDGE:
        return  # вместо «поправьте» — разбор; не начнётся — скажет он
    if screen:
        return
    _say_next(state)


def _owner_pause_next(state: rs.RunState, code: int) -> bool:
    """За шагом в этом же `dfl run` последует экран паузы просмотра или
    строка ожидания со ссылками (`run` печатает её после шага без разговора)."""
    return (
        code == 0
        and state.status == runner.WAITING_OWNER_REVIEW
        and not runner.review_pause_passed(state)
    )


def _say_next(state: rs.RunState) -> None:
    """Следующий шаг: на уровне 0, если он — действие человека."""
    hint = _next_hint(state, _repo_runs(state.repo)[0])
    if hint:
        say(f"дальше: {hint}", 0 if _awaits_human(state) else 1)


def _awaits_human(state: rs.RunState) -> bool:
    """Следующий шаг — действие человека: он виден на любом уровне (спека §2)."""
    return (
        _accepted_url(state) is not None
        or chain_open(state.mode, _frame(state), state.status, state.ops)
        or state.status in HUMAN_STEP_STATUSES
        or _edit_hint(state) is not None  # правка узла по находкам (#757)
    )


def _edit_hint(state: rs.RunState) -> str | None:
    """Что править по находкам проверки, на которой стоит прогон."""
    stop = sf.load(state.run_id, state.status)
    return sf.edit_hint(state, stop) if stop is not None else None


def _outcome(state: rs.RunState, code: int) -> str | None:
    """Строка итога шага (спека §5); `None` — прогон ждёт ответа: вопрос
    печатает `_say_question` или цикл интервью (при ненулевом коде — код)."""
    if state.status == WAITING:
        return f"шаг завершился с кодом {code}" if code != 0 else None
    if state.status.startswith("stopped_"):
        cause = fl.cause_from(None, [state.run_id], [], refusals=REFUSALS)
        first = cause.strip().splitlines()[0] if cause.strip() else fl.NO_CAUSE
        if first != fl.NO_CAUSE or code == 0:
            return f"прогон {state.run_id} остановлен: {first}"
    if code != 0:
        return f"шаг завершился с кодом {code}"
    if state.status == runner.WAITING_OWNER_REVIEW:
        return f"прогон {state.run_id} ждёт просмотра этапа: dfl run"
    return f"прогон {state.run_id}: {state.status}"


def _say_question(state: rs.RunState) -> bool:
    """Текст текущего вопроса интервью и как ответить; не прочитался —
    куда смотреть вопрос (и `False`: лог назовёт вызывающий)."""
    question = None
    session_id = (state.interview or {}).get("session_id")
    if session_id:
        try:
            reply = _discovery_status(str(session_id), rs.run_dir(state.run_id))
            question = ic.current_question(reply)
        except (InterviewStateError, OSError, df_config.ConfigError):
            question = None  # нет закреплённого discovery — тоже «не прочитался»
    if question is None:
        say(f"прогон {state.run_id} ждёт ответа; вопрос: dfl interview")
        return False
    say(question.text)
    say(ANSWER_HINT)
    say(ANSWER_FORMAT, 1)
    return True


def _state_lines(before_ops: dict[str, str], state: rs.RunState) -> list[str]:
    """Краткое состояние (уровень 2): прогон и шаги, пройденные за вызов."""
    head = f"{state.run_id} · {_frame(state) or '-'} · {state.status}"
    if state.pr is not None:
        head += f" · PR #{state.pr}"
    if state.wave:
        head += f" · волна {state.wave}"
    now = _op_statuses(state)
    steps = [key for key, status in now.items() if before_ops.get(key) != status]
    return [head, f"шаги: {', '.join(steps) if steps else '—'}"]


def _op_statuses(state: rs.RunState) -> dict[str, str]:
    """`ops` прогона: ключ → статус операции."""
    return {
        key: str(op.get("status", "")) if isinstance(op, dict) else str(op)
        for key, op in state.ops.items()
    }


def _ops_by_run() -> dict[str, dict[str, str]]:
    """Статусы `ops` всех читаемых прогонов — снимок до шага."""
    try:
        run_ids = rs.all_run_ids()
    except (OSError, ValueError):
        return {}
    out: dict[str, dict[str, str]] = {}
    for run_id in run_ids:
        state = _load_run(run_id)
        if state is not None:
            out[run_id] = _op_statuses(state)
    return out


def _load_run(run_id: str) -> rs.RunState | None:
    """Журнал прогона или `None`, если не читается (итог — подсказка)."""
    try:
        return rs.load(run_id)
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _readable_runs(repo: str) -> list[rs.RunState]:
    """Прогоны репо; нечитаемый журнал где угодно — отказ (Ruling 3)."""
    runs, broken = _repo_runs(repo)
    if broken:
        dirs = ", ".join(str(rs.run_dir(b)) for b in broken)
        raise DflError(
            f"журнал прогона не читается: {dirs} — по нему не понять, закончен "
            "ли конвейер; почините или уберите каталог(и) и повторите dfl run"
        )
    return runs


def _require_init_files(top: Path) -> None:
    """Новый конвейер — только в репо, где влит PR `dfl init-repo` (Ruling 8)."""
    missing = [rel for rel in INIT_FILES if not (top / rel).is_file()]
    if missing:
        raise DflError(
            f"репо не подготовлен: нет {', '.join(missing)} — сначала "
            "dfl init-repo, смержите его PR и git pull"
        )


def _repo_runs(repo: str) -> tuple[list[rs.RunState], list[str]]:
    """Прогоны репо в порядке `run_id` и `run_id` нечитаемых журналов.

    Чей нечитаемый журнал — неизвестно, поэтому он возвращается всегда:
    вызывающий не вправе молча его пропустить (fail closed)."""
    out, broken = [], []
    for run_id in rs.all_run_ids():
        try:
            state = rs.load(run_id)
        except (OSError, ValueError, TypeError, KeyError):
            broken.append(run_id)
            continue
        if state.repo == repo:
            out.append(state)
    return out, broken


def _frame(state: rs.RunState) -> str | None:
    return (state.interview or {}).get("frame")


def _engineer_of(cust: rs.RunState, runs: list[rs.RunState]) -> rs.RunState | None:
    ws = cust.ws_id + ENGINEER_SUFFIX
    return next((s for s in runs if s.ws_id == ws), None)


def _accepted_url(state: rs.RunState) -> str | None:
    """URL проверенного PR с кодом, если `code-accept` завершён с URL."""
    return accepted_url(state.ops)


def _finished(cust: rs.RunState, runs: list[rs.RunState]) -> bool:
    """PR с кодом проверен и ручные проверки отвечены (#771)."""
    eng = _engineer_of(cust, runs)
    return (
        eng is not None and _accepted_url(eng) is not None and not _manual_pending(eng)
    )


#: op записи ответов на ручные проверки (#771)
MANUAL_OP = "manual-checks"
MANUAL_HINT = "ждут ответа ручные проверки — dfl run"


def _manual_pending(state: rs.RunState) -> bool:
    return mc.pending(state.manual_checks, state.ops.get(MANUAL_OP))


def _manual_summary(state: rs.RunState, closed: frozenset[int] = frozenset()) -> str:
    record = state.ops.get(MANUAL_OP) or {}
    return mc.summary(mc.checks_of(record), record.get("answers") or {}, closed)


def _with_summary(line: str, state: rs.RunState) -> str:
    tail = _manual_summary(state)
    return f"{line}; {tail}" if tail else line


def _bundle_texts(state: rs.RunState) -> tuple[str, str]:
    bundle = Path(state.target_dir) / state.bundle_dir
    return (
        (bundle / "15-behaviour-spec.md").read_text(encoding="utf-8"),
        (bundle / "30-decomposition.md").read_text(encoding="utf-8"),
    )


def _manual_step(state: rs.RunState, url: str) -> int | None:
    """Ручные проверки перед «конвейер закончен» (#771), вне перехвата
    вывода: вопрос и ответ — на экране. `None` — пройдено; код — стоп."""
    result = (state.ops.get("code-accept") or {}).get("result") or {}
    pr = int(result.get("pr") or url.rstrip("/").rsplit("/", 1)[-1])
    record = state.ops.setdefault(MANUAL_OP, {})
    reason = ma.settle(
        record,
        load_texts=lambda: _bundle_texts(state),
        tty=_is_tty(),
        read=ic.stdin_read,
        ops=_QuietOps(_forge()),
        repo_slug=state.repo_slug,
        pr=pr,
        pr_url=url,
        run_id=state.run_id,
        save=lambda: _manual_save(state),
        run_dir=rs.run_dir(state.run_id),
    )
    if reason is None:
        return None
    say(reason)
    return 0 if ma.is_wait(reason) else 1


def _manual_save(state: rs.RunState) -> None:
    """Запись ответов под блокировкой поверх свежего run.json: человек думает
    сколько угодно, чужие записи прогона за это время не теряются."""
    try:
        with run_lock.run_lock(state.run_id):
            now = rs.load(state.run_id)
            now.ops[MANUAL_OP] = state.ops[MANUAL_OP]
            rs.save(now)
    except run_lock.LockBusy as exc:
        raise DflError(f"{exc} — дождитесь конца dfl run и повторите") from exc


def _select_customer(
    runs: list[rs.RunState], subject: str | None
) -> rs.RunState | None:
    """Customer-прогон для шага; `None` — начать новый (`subject` задан)."""
    customers = [s for s in runs if _frame(s) == "customer"]
    unfinished = [c for c in customers if not _finished(c, runs)]
    if subject is not None:
        same = [c for c in customers if c.subject == subject]
        if same:
            return next((c for c in same if c in unfinished), same[-1])
        if unfinished:
            names = ", ".join(c.run_id for c in unfinished)
            raise DflError(
                f"конвейер репо не закончен ({names}) — новая тема не "
                f"начинается; dfl run без --subject продолжит его, {ABANDON_HINT}"
            )
        return None
    if len(unfinished) == 1:
        return unfinished[0]
    if not unfinished:
        raise DflError(f"незаконченного прогона нет — {NEW_RUN_HINT}")
    names = ", ".join(c.run_id for c in unfinished)
    raise DflError(
        f"незаконченных прогонов несколько: {names} — выберите: "
        "dfl run --subject '<тема одного из них>' (то же для dfl answer и "
        "dfl abandon)"
    )


def _engineer_step(
    eng: rs.RunState, repo: str, loop: Callable[[list[str]], int]
) -> int:
    url = _accepted_url(eng)
    if url is None:
        code = loop(["--subject", eng.subject, "--repo", repo, "--run-id", eng.run_id])
        fresh = _load_run(eng.run_id) if code == 0 else None
        url = _accepted_url(fresh) if fresh is not None else None
        if fresh is None or url is None or not _manual_pending(fresh):
            return code
        eng = fresh  # приёмка завершилась этим шагом — ручные проверки сейчас
    if _manual_pending(eng):
        stopped = _manual_step(eng, url)
        if stopped is not None:
            return stopped
    say(_with_summary(_accepted_line(eng, url), eng))
    return 0


def _accepted_line(eng: rs.RunState, url: str) -> str:
    """Итог шага, когда PR с кодом уже проверен (до хвоста ручных проверок)."""
    if merged_unreviewed(eng.ops):
        return (
            f"PR с кодом вмержен без автоматической приёмки: {url}; конвейер закончен"
        )
    if code_merged(eng.ops):
        # merged+reviewed пишет только auto_tasks._finish_merged: ревью этой
        # головы одобрило, но приёмка не завершилась (ревью #739)
        return (
            f"PR с кодом вмержен человеком после одобряющего ревью, приёмка "
            f"не завершена: {url}; конвейер закончен"
        )
    return f"PR с кодом проверен: {url}; мерж — за вами"


def _brief_step(cust: rs.RunState, role: str, loop: Callable[[list[str]], int]) -> int:
    """Бриф готов: предложение → мерж человеком → одобрение → engineer."""
    approved = (rs.run_dir(cust.run_id) / bt.APPROVED_REL).exists()
    if not approved:
        _refuse_closed_brief(cust)
        with runlog.captured(_LOG, verbosity()):
            code = bt.main(["propose", "--run", cust.run_id])
        if code != 0:
            say(f"бриф: propose завершился с кодом {code}")
            return code
    pr, merged = _brief_pr(cust)
    if not approved:
        if not merged:
            url = f"https://github.com/{cust.repo_slug}/pull/{pr}"
            say(f"бриф ждёт вашего мержа: {url} — после мержа снова dfl run")
            return 0
        with runlog.captured(_LOG, verbosity()):
            code = bt.main(["approve", "--run", cust.run_id, "--pr", str(pr)])
        if code != 0:
            say(f"бриф: approve завершился с кодом {code}")
            return code
    return loop([*bt.engineer_args(cust, pr, role), *_review_argv(cust.review_pause)])


def _refuse_closed_brief(cust: rs.RunState) -> None:
    """Brief-PR закрыт без мержа — конвейер не продолжить, только бросить."""
    ops = _forge()
    found = ops.find_brief_pr_fact(cust.repo_slug, bt.brief_branch(cust.ws_id))
    if found.outcome is Outcome.UNAVAILABLE or not found.value:
        return  # нет PR или факт не прочитан — решает propose
    for pr in found.value:
        facts = ops.brief_pr_fact(cust.repo_slug, pr)
        if facts.value is None or facts.value.state != "CLOSED":
            return
    raise DflError(
        f"brief-PR {found.value} закрыт без мержа — этот конвейер не "
        "продолжить; dfl abandon бросит его, затем dfl run --subject '<тема>'"
    )


def _brief_pr(cust: rs.RunState) -> tuple[int, bool]:
    """Номер brief-PR и вмержен ли он — по фактам форджи."""
    ops = _forge()
    branch = bt.brief_branch(cust.ws_id)
    found = ops.find_brief_pr_fact(cust.repo_slug, branch)
    if found.outcome is Outcome.UNAVAILABLE or found.value is None:
        raise DflError(f"PR ветки {branch} не установлен ({found.detail}) — повторите")
    if len(found.value) != 1:
        raise DflError(
            f"у {branch} PR: {found.value or 'нет'} — ожидался ровно один; "
            "разберитесь в репо и повторите dfl run"
        )
    pr = found.value[0]
    facts = ops.brief_pr_fact(cust.repo_slug, pr)
    if facts.outcome is Outcome.UNAVAILABLE or facts.value is None:
        raise DflError(f"brief-PR #{pr} не прочитан ({facts.detail}) — повторите")
    return pr, facts.value.state == "MERGED"


def _forge():
    """Чтение фактов форджи (подменяется в тестах)."""
    from governance.ops import RealOps

    return RealOps()


def status(cwd: Path) -> int:
    """Прогоны репо из журнала и следующий шаг; форджа и эффектов нет."""
    target = _registered_target(cwd)
    runs, broken = _repo_runs(target.repo)
    for run_id in broken:
        say(f"{run_id}  не читается: {rs.run_dir(run_id)}/run.json")
    for state in runs:
        line = f"{state.run_id}  {_frame(state) or '-'}  {state.status}"
        hint = _next_hint(state, runs)
        say(f"{line}  дальше: {hint}" if hint else line)
        if verbosity() >= 1:
            _manual_status(state.ops.get(MANUAL_OP) or {}, state.repo_slug)
        for key, op_status in _op_statuses(state).items():
            say(f"  {key}: {op_status}", 2)
    active_slug = user_targets.slug_from_url(target.repo_url)
    active = personal_state.active_for(active_slug)
    if active is not None:
        line = f"личный прогон {active.run_id}: шаг {active.step}"
        say(f"{line} — {active.stop_reason}" if active.stop_reason else line)
        if verbosity() >= 1:
            _manual_status(active.manual, active.repo_slug)
    for done in personal_state.finished_for(active_slug):
        tail = mc.summary(mc.checks_of(done.manual), done.manual.get("answers") or {})
        if not tail and not (verbosity() >= 1 and done.manual.get("checks")):
            continue
        line = f"личный прогон {done.run_id}: закончен"
        say(f"{line}; {tail}" if tail else line)
        if verbosity() >= 1:
            _manual_status(done.manual, done.repo_slug)
    return 0


def _manual_status(record: dict, repo_slug: str) -> None:
    """`dfl status -v`: строка на ручную проверку; состояние issue «не
    выполнено» — вживую (закрыт = выполнено позже, журнал только растёт)."""
    answers = record.get("answers") or {}
    for check in mc.checks_of(record):
        answer = answers.get(check.beh_id)
        if answer is None:
            say(f"  {check.beh_id} ({check.task_id}): ждёт ответа", 1)
            continue
        line = f"  {check.beh_id} ({check.task_id}): "
        line += mc.VERDICT_WORDS.get(str(answer.get("verdict")), "?")
        if answer.get("note"):
            line += f" — {answer['note']}"
        issue = answer.get("issue")
        if issue:
            state = _forge().issue_state(repo_slug, int(issue))
            mark = {"CLOSED": "закрыт — выполнено позже", "OPEN": "открыт"}
            line += (
                f" → issue #{issue} ({mark.get(str(state), 'состояние не прочитано')})"
            )
        say(line, 1)


def _registered_target(cwd: Path) -> user_targets.UserTarget:
    here = cwd.resolve()
    for target in user_targets.load().values():
        if here == target.path or target.path in here.parents:
            return target
    raise DflError(f"{cwd} не зарегистрирован — сначала dfl run в этом каталоге")


def _next_hint(state: rs.RunState, runs: list[rs.RunState]) -> str | None:
    url = _accepted_url(state)
    if url is not None and _manual_pending(state):
        return MANUAL_HINT
    if url is not None:
        # конвейер закончен и без мержа: dfl run его не продолжает (ревью #739);
        # новую тему предлагаем, только если её не заблокирует другой конвейер
        done = _with_summary(
            f"конвейер закончен ({url} вмержен)"
            if code_merged(state.ops)
            else (f"смержите {url} — конвейер закончен"),
            state,
        )
        busy = any(_frame(c) == "customer" and not _finished(c, runs) for c in runs)
        return done if busy else f"{done}; {NEXT_SUBJECT_HINT}"
    if chain_open(state.mode, _frame(state), state.status, state.ops):
        return f"{CHAIN_STOPPED_HINT} (или {ABANDON_HINT})"
    if _engineer_of(state, runs) is not None:
        return None  # бриф уже ушёл в engineer-прогон — шаг там
    if state.status == WAITING:
        return ANSWER_HINT
    if state.status == "brief_ready":
        return BRIEF_READY_HINT
    if state.status == "waiting_human_merge":
        what = f"https://github.com/{state.repo_slug}/pull/{state.pr}"
        return f"смержите {what if state.pr else 'PR прогона'}, затем dfl run"
    edit = _edit_hint(state)
    if edit is not None:
        return f"{edit} (или {ABANDON_HINT})"
    if state.status.startswith("stopped_"):
        return f"разберите причину выше и dfl run (или {ABANDON_HINT})"
    return None


def answer(
    cwd: Path,
    file: Path | None,
    subject: str | None,
    role: str,
    *,
    question: str | None = None,
    supersede: bool = False,
) -> int:
    """Ответ на вопрос интервью конвейера, затем шаг `dfl run`.

    Без `question` — текущий вопрос; `supersede` заменяет уже данный ответ
    на вопрос `question` (discovery требует их вместе). Без `file` —
    интервью разговором (только в терминале, `harness.author = claude`)."""
    if file is None and (question or supersede):
        raise DflError(
            "--question и --supersede — только вместе с --file: "
            "dfl answer --question <id> --supersede --file answer.yaml"
        )
    if supersede and not question:
        raise DflError("--supersede требует --question <id вопроса>")
    if file is None and not _is_tty():
        raise DflError(NO_TTY_REFUSAL)
    if file is None and not _author_is_claude():
        raise DflError(CHAT_HARNESS_REFUSAL)
    target = user_targets.register(cwd)
    runs = _readable_runs(target.repo)
    cust = _existing_customer(runs, subject)
    eng = _engineer_of(cust, runs)
    state = eng if eng is not None else cust
    if state.status != WAITING:
        raise DflError(
            f"прогон {state.run_id} не ждёт ответа (статус {state.status}) — "
            "dfl status покажет, что дальше"
        )
    if file is None:
        with runner.quiet_interview_hints():
            result = _converse(state)
        if result is ic.ChatResult.STOPPED:
            say(CHAT_STOPPED)
            return 0
        return run(cwd, cust.subject, role)
    path = file if file.is_absolute() else cwd / file
    if not path.is_file():
        raise DflError(f"нет файла ответа {path} — создайте его\n{ANSWER_FORMAT}")
    interview = state.interview or {}
    reply = _discovery_answer(
        str(interview.get("session_id")),
        str(interview.get("stakeholder_role")),
        path.resolve(),
        rs.run_dir(state.run_id),
        question=question,
        supersede=supersede,
    )
    if reply.code in (1, 2):
        reason = reply.envelope.get("operation", {}).get("reason", "")
        raise DflError(
            f"discovery не принял ответ ({reason}) — исправьте {path} "
            "и повторите dfl answer"
        )
    return run(cwd, cust.subject, role, chat=False)


def _existing_customer(runs: list[rs.RunState], subject: str | None) -> rs.RunState:
    """Незаконченный customer-прогон конвейера (`subject` выбирает из многих)."""
    cust = _select_customer(runs, subject)
    if cust is None or _finished(cust, runs):
        raise DflError(f"незаконченного конвейера с темой {subject!r} нет")
    return cust


def _discovery_answer(
    session_id: str,
    role: str,
    file: Path,
    cwd: Path,
    *,
    question: str | None = None,
    supersede: bool = False,
) -> iv.DiscoveryReply:
    """`discovery answer` закреплённым discovery — как `RealOps._discovery`."""
    args = ["answer", "--session", session_id, "--role", role, "--file", str(file)]
    if question:
        args += ["--question", question]
    if supersede:
        args.append("--supersede")
    return _discovery(args, cwd)


def _discovery_status(session_id: str, cwd: Path) -> iv.DiscoveryReply:
    """`discovery status` сессии: журнал не меняется; 20 — ждёт ответа."""
    return _discovery(["status", "--session", session_id], cwd)


def _discovery(args: list[str], cwd: Path) -> iv.DiscoveryReply:
    """Вызов закреплённого discovery (его окружение) и проверка границы."""
    project = str(df_config.workspace_dir("discovery"))
    argv = ["uv", "run", "--frozen", "--project", project, "discovery", *args]
    done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)
    if done.stdout:
        say(done.stdout.rstrip("\n"), 3)
    if done.stderr:
        say(done.stderr.rstrip("\n"), 3, err=True)
    return iv.parse_reply(done.returncode, done.stdout, done.stderr)


def interview(cwd: Path, subject: str | None) -> int:
    """Состояние интервью текстом: вопрос, темы, пункты, что мешает готовности.

    Агент и терминал не нужны; работает в любом статусе прогона с сессией."""
    state = _interview_run(cwd, subject)
    session_id = str((state.interview or {}).get("session_id"))
    try:
        view = load_session(session_id)
    except InterviewStateError as exc:
        raise DflError(str(exc)) from exc
    reply = _discovery_status(session_id, rs.run_dir(state.run_id))
    print(f"Тема: {state.subject}")
    failure = _status_failure(reply)
    print(iview.render_view(view, ic.current_question(reply), failure))
    return 0


def _status_failure(reply: iv.DiscoveryReply) -> str | None:
    """Причина сбоя `discovery status`; None — вопрос есть или интервью кончилось."""
    if reply.code in (0, 10, 11, 20):
        return None
    return str(reply.envelope.get("operation", {}).get("reason", "") or "")


def correct(cwd: Path, text: str, subject: str | None) -> int:
    """Поправка собранных пунктов текстом (`harness.author = claude`).

    После конца интервью (`interview.completed_at`) — отказ: бриф уже собран,
    и поправка в журнале discovery в него не попадёт."""
    if not _author_is_claude():
        raise DflError(CHAT_HARNESS_REFUSAL)
    state = _interview_run(cwd, subject)
    if (state.interview or {}).get("completed_at"):
        raise DflError(INTERVIEW_COMPLETED_REFUSAL)
    agent = st.claude_agent(df_config.load().author_model)
    try:
        result = iview.correct(
            state,
            text,
            agent=agent,
            load=load_session,
            send=_sender(state, supersede=True),
        )
    except (iview.CorrectError, InterviewStateError) as exc:
        raise DflError(str(exc)) from exc
    if isinstance(result, st.Ask):
        say(
            f"Уточните: {result.question} — повторите "
            "dfl correct '<текст с уточнением>'"
        )
        return 0
    say(str(result))
    return 0


def _interview_run(cwd: Path, subject: str | None) -> rs.RunState:
    """Прогон с сессией интервью: engineer, если она у него есть, иначе customer."""
    target = user_targets.register(cwd)
    runs = _readable_runs(target.repo)
    for cust in _interview_customers(runs, subject):
        for state in (_engineer_of(cust, runs), cust):
            if state is not None and (state.interview or {}).get("session_id"):
                return state
    raise DflError(NO_INTERVIEW_REFUSAL)


def _interview_customers(
    runs: list[rs.RunState], subject: str | None
) -> list[rs.RunState]:
    """Customer-прогоны кандидаты: выбранный как у `dfl answer`; без `subject`
    и без незаконченных конвейеров — все, новейший первым (интервью смотрят
    и после конца конвейера)."""
    customers = [s for s in runs if _frame(s) == "customer"]
    if subject is None and all(_finished(c, runs) for c in customers):
        return customers[::-1]
    cust = _select_customer(runs, subject)
    return [] if cust is None else [cust]


def abandon(cwd: Path, subject: str | None) -> int:
    """Бросить незаконченный конвейер: его прогоны — в каталог брошенных.

    Активный личный прогон (по `subject`, без него — последний) бросается
    первым, как `dfl run` первым продолжает его (RF-4)."""
    target = user_targets.register(cwd)
    slug = user_targets.slug_from_url(target.repo_url)
    personal = personal_state.active_for(slug, subject=subject)
    if personal is not None:
        rc = _abandon_personal(personal)
        if subject is None:
            left = _enterprise_unfinished(target.repo)
            if left is None:
                print(ABANDON_ENTERPRISE_UNKNOWN)
            elif left:
                print(ABANDON_ENTERPRISE_LEFT)
        return rc
    runs = _readable_runs(target.repo)
    cust = _existing_customer(runs, subject)
    wss = {cust.ws_id, cust.ws_id + ENGINEER_SUFFIX}
    moved = [s.run_id for s in runs if s.ws_id in wss]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = rs.RUNS_ROOT.parent / "governance-runs-abandoned" / stamp
    with ExitStack() as stack:
        for run_id in moved:
            try:
                stack.enter_context(run_lock.run_lock(run_id))
            except run_lock.LockBusy as exc:
                raise DflError(f"{exc} — дождитесь конца dfl run и повторите") from exc
        dest.mkdir(parents=True, exist_ok=False)
        for run_id in moved:
            shutil.move(str(rs.run_dir(run_id)), str(dest / run_id))
            print(f"брошен: {run_id} → {dest / run_id}")
    print(
        "открытые PR конвейера (бриф, задачи) не тронуты — закройте их сами; "
        f"вернуть прогон — перенести каталог обратно в {rs.RUNS_ROOT}"
    )
    return 0


def _enterprise_unfinished(repo: str) -> bool | None:
    """В репо есть незаконченный enterprise-конвейер (customer-прогон);
    `None` — не установлено (журнал не читается): подсказка после
    состоявшегося броска не меняет его исход (ревью #797)."""
    try:
        runs = _readable_runs(repo)
    except DflError:
        return None
    return any(_frame(s) == "customer" and not _finished(s, runs) for s in runs)


def _abandon_personal(run: personal_state.PersonalRun) -> int:
    """Личный прогон — в брошенные; PR, ветку и файлы бандла не трогает."""
    dest = personal_state.abandon(run)
    print(f"брошен: {run.run_id} → {dest}")
    print(
        f"открытые PR прогона (spec-PR, integration-PR), ветка "
        f"spec/{run.ws_id}-personal и файлы {run.bundle_dir} в репо не тронуты — "
        "уберите их сами; вернуть прогон — перенести каталог обратно в "
        f"{personal_state.personal_root()}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(cli())
