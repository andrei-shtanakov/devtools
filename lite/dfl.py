"""`dfl` — одна команда коллеги: customer → бриф → engineer (режим test).

`dfl run` — идемпотентный шаг вперёд по журналу прогонов репо в `--cwd`:
бриф-интервью, предложение брифа, после мержа брифа — одобрение и
engineer-прогон, затем проверенный PR с кодом. `dfl answer --file <f>` —
ответ на вопрос интервью (закреплённым discovery) и снова шаг `dfl run`.
`dfl abandon` — бросить незаконченный конвейер (каталоги прогонов уходят в
`out/governance-runs-abandoned/<время>/`, обратимо). `dfl status` — только
журнал, без форджи и эффектов. Конвейер исполняется в процессе
(`spec_loop.main`, `brief_tools.main`).
"""

from __future__ import annotations

import argparse
import functools
import shutil
import subprocess
import sys
import traceback
from collections.abc import Callable
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

from governance import brief_tools as bt
from governance import df_config, run_lock, spec_loop, user_targets
from governance import interview as iv
from governance import run_state as rs
from governance.facts import Outcome
from lite import failure_log as fl
from lite.init_repo import DEVTOOLS, InitError, init_repo, real_gh
from lite.output import refusal
from lite.pipeline import (
    CHAIN_STOPPED_HINT,
    accepted_url,
    chain_open,
    code_merged,
    merged_unreviewed,
)
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
INIT_FILES = (
    "profiles/team-exp.yaml",
    "scripts/review/local.sh",
    "spec-runner.config.yaml",
)


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
    и последней строкой — подсказка `dfl report`. Запись сбоя — после выхода
    из `tee_output`: только тогда вывод дочитан целиком."""
    pending: list[Callable[[list[str]], None]] = []
    with tee_output() as tail:
        try:
            code = main(argv, defer=pending)
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
    """CLI: `--cwd <dir>` и `run` | `answer` | `abandon` | `status` | `init-repo`.

    Ненулевой код и исключение пишут запись сбоя (`lite.failure_log`);
    ненулевой код печатает подсказку `dfl report` последней строкой. С
    `defer` запись откладывается: `cli` выполнит её с полным хвостом вывода;
    без него — пишется сразу с пустым хвостом."""
    raw = list(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(raw)
    cwd = Path(args.cwd)
    before = fl.snapshot_runs()

    def record(code: int, exc: BaseException | None) -> None:
        job = functools.partial(_record, args, cwd, raw, code, exc, before)
        if defer is None:
            job([])
        else:
            defer.append(job)

    try:
        code, refused = _dispatch(args, cwd)
    except Exception as exc:
        record(1, exc)
        raise
    if code != 0:
        record(code, refused)
        print(REPORT_HINT, file=sys.stderr)
    return code


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
            ),
        )
    except OSError as err:
        print(f"запись сбоя не удалась: {err}", file=sys.stderr)


def _public_argv(raw: list[str]) -> list[str]:
    """argv без `--cwd <путь>`; у `--file` — только имя файла."""
    out: list[str] = []
    skip = False
    for i, arg in enumerate(raw):
        if skip:
            skip = False
            continue
        if arg == "--cwd":
            skip = True
            continue
        out.append(Path(arg).name if i > 0 and raw[i - 1] == "--file" else arg)
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


def _dispatch(args: argparse.Namespace, cwd: Path) -> tuple[int, BaseException | None]:
    """Тело подкоманды: (код, отказ-исключение или `None`)."""
    try:
        _require_test_mode()
        if args.command == "status":
            return status(cwd), None
        if args.command == "init-repo":
            return init_repo(cwd, DEVTOOLS, real_gh), None
        if args.command == "answer":
            code = answer(
                cwd,
                Path(args.file),
                args.subject,
                args.stakeholder,
                question=args.question,
                supersede=args.supersede,
            )
            return code, None
        if args.command == "abandon":
            return abandon(cwd, args.subject), None
        return run(cwd, args.subject, args.stakeholder), None
    except REFUSALS as exc:
        print(refusal(f"dfl {args.command}", str(exc)), file=sys.stderr)
        return 2, exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dfl")
    parser.add_argument("--cwd", required=True, help="каталог репо цели")
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", help="следующий шаг конвейера")
    p_run.add_argument("--subject", help="тема нового прогона")
    p_run.add_argument("--stakeholder", default="customer")
    p_ans = sub.add_parser("answer", help="ответ на вопрос интервью и dfl run")
    p_ans.add_argument(
        "--file", required=True, help="YAML-файл ответа (text: и entries:)"
    )
    p_ans.add_argument("--question", help="id вопроса (по умолчанию — текущий)")
    p_ans.add_argument(
        "--supersede",
        action="store_true",
        help="заменить уже данный ответ на вопрос --question",
    )
    p_ans.add_argument("--subject", help="тема конвейера, если их несколько")
    p_ans.add_argument("--stakeholder", default="customer")
    p_ab = sub.add_parser("abandon", help="бросить незаконченный конвейер")
    p_ab.add_argument("--subject", help="тема конвейера, если их несколько")
    sub.add_parser("status", help="прогоны репо и что дальше")
    sub.add_parser("init-repo", help="PR с шаблонами DarkFactory в репо")
    return parser


def _require_test_mode() -> None:
    """Конфиг обязан быть в режиме test (иначе отказ); печать баннера."""
    cfg = df_config.load()
    if cfg.mode != "test":
        raise DflError(
            f"dfl работает только в режиме test (mode = {cfg.mode!r}) — "
            f'поставьте mode = "test" в {df_config.canonical_config_path()} '
            "(install.sh существующий конфиг не переписывает) или работайте "
            "через make"
        )
    print(df_config.test_banner(cfg))
    print()


def run(cwd: Path, subject: str | None, role: str) -> int:
    """Один шаг конвейера для репо в `cwd`; код шага или 0/2 самого `dfl`."""
    target = user_targets.register(cwd)
    code = _step(target, subject, role)
    _hint_if_waiting(target.repo)
    return code


def _step(target: user_targets.UserTarget, subject: str | None, role: str) -> int:
    repo = target.repo
    runs = _readable_runs(repo)
    cust = _select_customer(runs, subject)
    if cust is None:
        _require_init_files(target.path)
        return spec_loop.main(
            ["--subject", str(subject), "--repo", repo, "--need", "--frame",
             "customer", "--stakeholder", role, "--brief-only"]
        )  # fmt: skip
    eng = _engineer_of(cust, runs)
    if eng is not None:
        return _engineer_step(eng, repo)
    if cust.status != "brief_ready":
        return spec_loop.main(
            ["--subject", cust.subject, "--repo", repo, "--run-id", cust.run_id]
        )
    return _brief_step(cust, role)


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


def _hint_if_waiting(repo: str) -> None:
    """Прогон ждёт ответа на вопрос интервью — как отвечать через dfl."""
    runs, _ = _repo_runs(repo)
    for state in runs:
        if state.status == WAITING:
            print(f"dfl: прогон {state.run_id} ждёт ответа на вопрос выше")
            print(f"{ANSWER_HINT}\n{ANSWER_FORMAT}")


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


def _engineer_step(eng: rs.RunState, repo: str) -> int:
    url = _accepted_url(eng)
    if url is not None and merged_unreviewed(eng.ops):
        print(
            f"PR с кодом вмержен без автоматической приёмки: {url}; конвейер закончен"
        )
        return 0
    if url is not None and code_merged(eng.ops):
        # merged+reviewed пишет только auto_tasks._finish_merged: ревью этой
        # головы одобрило, но приёмка не завершилась (ревью #739)
        print(
            f"PR с кодом вмержен человеком после одобряющего ревью, приёмка "
            f"не завершена: {url}; конвейер закончен"
        )
        return 0
    if url is not None:
        print(f"PR с кодом проверен: {url}; мерж — за вами")
        return 0
    return spec_loop.main(
        ["--subject", eng.subject, "--repo", repo, "--run-id", eng.run_id]
    )


def _brief_step(cust: rs.RunState, role: str) -> int:
    """Бриф готов: предложение → мерж человеком → одобрение → engineer."""
    approved = (rs.run_dir(cust.run_id) / bt.APPROVED_REL).exists()
    if not approved:
        _refuse_closed_brief(cust)
        code = bt.main(["propose", "--run", cust.run_id])
        if code != 0:
            return code
    pr, merged = _brief_pr(cust)
    if not approved:
        if not merged:
            url = f"https://github.com/{cust.repo_slug}/pull/{pr}"
            print(f"бриф ждёт вашего мержа: {url} — после мержа снова dfl run")
            return 0
        code = bt.main(["approve", "--run", cust.run_id, "--pr", str(pr)])
        if code != 0:
            return code
    return spec_loop.main(bt.engineer_args(cust, pr, role))


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
        print(f"{run_id}  не читается: {rs.run_dir(run_id)}/run.json")
    for state in runs:
        line = f"{state.run_id}  {_frame(state) or '-'}  {state.status}"
        hint = _next_hint(state, runs)
        print(f"{line}  дальше: {hint}" if hint else line)
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
        return "мерж брифа, затем dfl run"
    if state.status.startswith("stopped_"):
        return f"разберите причину выше и dfl run (или {ABANDON_HINT})"
    return None


def answer(
    cwd: Path,
    file: Path,
    subject: str | None,
    role: str,
    *,
    question: str | None = None,
    supersede: bool = False,
) -> int:
    """Ответ на вопрос интервью конвейера, затем шаг `dfl run`.

    Без `question` — текущий вопрос; `supersede` заменяет уже данный ответ
    на вопрос `question` (discovery требует их вместе)."""
    if supersede and not question:
        raise DflError("--supersede требует --question <id вопроса>")
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
    return run(cwd, cust.subject, role)


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
    argv = [
        "uv",
        "run",
        "--frozen",
        "--project",
        str(df_config.workspace_dir("discovery")),
        "discovery",
        "answer",
        "--session",
        session_id,
        "--role",
        role,
        "--file",
        str(file),
    ]
    if question:
        argv += ["--question", question]
    if supersede:
        argv.append("--supersede")
    done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)
    return iv.parse_reply(done.returncode, done.stdout, done.stderr)


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
