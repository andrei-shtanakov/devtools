"""`dfl` — одна команда коллеги: customer → бриф → engineer (режим test).

`dfl run` — идемпотентный шаг вперёд по журналу прогонов репо в `--cwd`:
бриф-интервью, предложение брифа, после мержа брифа — одобрение и
engineer-прогон, затем проверенный PR с кодом. `dfl answer --file <f>` —
ответ на вопрос интервью (закреплённым discovery) и снова шаг `dfl run`.
В терминале (`stdin` — tty) `dfl run` и `dfl answer` без `--file` ведут
интервью разговором (`lite.interview_chat`): вопрос, ответ своими словами,
уточнения, отправка — до конца интервью, затем обычный шаг конвейера.
`dfl abandon` — бросить незаконченный конвейер (каталоги прогонов уходят в
`out/governance-runs-abandoned/<время>/`, обратимо). `dfl status` — только
журнал, без форджи и эффектов. Конвейер исполняется в процессе
(`spec_loop.main`, `brief_tools.main`).
"""

from __future__ import annotations

import argparse
import functools
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from collections.abc import Callable
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

from governance import brief_tools as bt
from governance import df_config, run_lock, runner, spec_loop, user_targets
from governance import interview as iv
from governance import run_state as rs
from governance.facts import Outcome
from lite import failure_log as fl
from lite import interview_chat as ic
from lite import interview_view as iview
from lite import runlog
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
SCREEN_MARK = "уровень 3: вывод раннера шёл на экран"

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
        _require_test_mode(status_header=args.command == "status")
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
        if args.command == "correct":
            return correct(cwd, args.text, args.subject), None
        return run(cwd, args.subject, args.stakeholder), None
    except REFUSALS as exc:
        say(refusal(f"dfl {args.command}", str(exc)), 0, err=True)
        return 2, exc


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


def _require_test_mode(*, status_header: bool = False) -> None:
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
    level = verbosity()
    full = level >= runlog.PASSTHROUGH_LEVEL or (status_header and level >= 1)
    banner = df_config.test_banner(cfg) if full else df_config.test_banner_line(cfg)
    if banner is not None:
        say(banner)
    if status_header:
        say(f"конфиг: {df_config.canonical_config_path()}", 1)
    say("")


def run(cwd: Path, subject: str | None, role: str, *, chat: bool = True) -> int:
    """Один шаг конвейера для репо в `cwd`; код шага или 0/2 самого `dfl`.

    В терминале (и `chat`) при `harness.author = claude` ждущий ответа прогон
    ведётся интервью разговором; иначе — подсказка `dfl answer --file`."""
    target = user_targets.register(cwd)
    chatting = chat and _is_tty()
    if chatting and _author_is_claude():
        return _run_chatting(target, subject, role)
    code = _step(target, subject, role, chatting=False)
    if chatting and _waiting_run(target.repo, subject) is not None:
        raise DflError(CHAT_HARNESS_REFUSAL)
    return code


def _run_chatting(
    target: user_targets.UserTarget, subject: str | None, role: str
) -> int:
    """Шаг конвейера; ждёт ответа — интервью разговором и снова шаг.

    Прогон, остановленный неполным интервью (findings 10/11), тоже ведётся
    разговором (добор тем), но один раз за вызов: человек мог оставить темы
    пустыми. Подсказки раннера (`discovery answer …`, `make …`) не
    печатаются: вопрос показывает сам цикл."""
    with runner.quiet_interview_hints():
        code = _step(target, subject, role, chatting=True)
        incomplete = True
        while (state := _waiting_run(target.repo, subject, incomplete)) is not None:
            incomplete = False
            if _converse(state) is ic.ChatResult.STOPPED:
                say(CHAT_STOPPED)
                return 0
            code = _step(target, subject, role, chatting=True)
    return code


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
    target: user_targets.UserTarget, subject: str | None, role: str, *, chatting: bool
) -> int:
    repo = target.repo
    loop = functools.partial(_loop, cwd=target.path, chatting=chatting)
    runs = _readable_runs(repo)
    cust = _select_customer(runs, subject)
    if cust is None:
        _require_init_files(target.path)
        return loop(
            ["--subject", str(subject), "--repo", repo, "--need", "--frame",
             "customer", "--stakeholder", role, "--brief-only"]
        )  # fmt: skip
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
    состояние, следующий шаг (на уровне 0, если он — действие человека)."""
    if line is not None:
        say(line)
    for found in sf.lines(state.run_id, state.status):
        say(f"  - {sf.readable(found)}")
    unread = state.status == WAITING and not chatting and not _say_question(state)
    stopped = state.status.startswith("stopped_")
    if (stopped or unread) and code == 0 and _LOG is not None:
        say(LOG_HINT)  # при code != 0 строку печатает `main`: одна на вызов
    if verbosity() >= 2:
        for text in _state_lines(before_ops, state):
            say(text, 2)
    if state.status == WAITING:
        return  # как отвечать — уже сказано (вопрос) или скажет цикл
    hint = _next_hint(state, _repo_runs(state.repo)[0])
    if hint:
        say(f"дальше: {hint}", 0 if _awaits_human(state) else 1)


def _awaits_human(state: rs.RunState) -> bool:
    """Следующий шаг — действие человека: он виден на любом уровне (спека §2)."""
    return (
        _accepted_url(state) is not None
        or chain_open(state.mode, _frame(state), state.status, state.ops)
        or state.status in HUMAN_STEP_STATUSES
        or bool(sf.lines(state.run_id, state.status))  # правка узла (#757)
    )


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
    eng = _engineer_of(cust, runs)
    return eng is not None and _accepted_url(eng) is not None


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
    if url is not None and merged_unreviewed(eng.ops):
        say(f"PR с кодом вмержен без автоматической приёмки: {url}; конвейер закончен")
        return 0
    if url is not None and code_merged(eng.ops):
        # merged+reviewed пишет только auto_tasks._finish_merged: ревью этой
        # головы одобрило, но приёмка не завершилась (ревью #739)
        say(
            f"PR с кодом вмержен человеком после одобряющего ревью, приёмка "
            f"не завершена: {url}; конвейер закончен"
        )
        return 0
    if url is not None:
        say(f"PR с кодом проверен: {url}; мерж — за вами")
        return 0
    return loop(["--subject", eng.subject, "--repo", repo, "--run-id", eng.run_id])


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
    return loop(bt.engineer_args(cust, pr, role))


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
    repo = _registered_repo(cwd)
    runs, broken = _repo_runs(repo)
    for run_id in broken:
        say(f"{run_id}  не читается: {rs.run_dir(run_id)}/run.json")
    for state in runs:
        line = f"{state.run_id}  {_frame(state) or '-'}  {state.status}"
        hint = _next_hint(state, runs)
        say(f"{line}  дальше: {hint}" if hint else line)
        for key, op_status in _op_statuses(state).items():
            say(f"  {key}: {op_status}", 2)
    return 0


def _registered_repo(cwd: Path) -> str:
    here = cwd.resolve()
    for target in user_targets.load().values():
        if here == target.path or target.path in here.parents:
            return target.repo
    raise DflError(f"{cwd} не зарегистрирован — сначала dfl run в этом каталоге")


def _next_hint(state: rs.RunState, runs: list[rs.RunState]) -> str | None:
    url = _accepted_url(state)
    if url is not None:
        # конвейер закончен и без мержа: dfl run его не продолжает (ревью #739);
        # новую тему предлагаем, только если её не заблокирует другой конвейер
        done = (
            f"конвейер закончен ({url} вмержен)"
            if code_merged(state.ops)
            else (f"смержите {url} — конвейер закончен")
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
    found = sf.lines(state.run_id, state.status)
    if found:
        return f"{sf.edit_hint(state, found)} (или {ABANDON_HINT})"
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
    """Бросить незаконченный конвейер: его прогоны — в каталог брошенных."""
    target = user_targets.register(cwd)
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


if __name__ == "__main__":
    sys.exit(cli())
