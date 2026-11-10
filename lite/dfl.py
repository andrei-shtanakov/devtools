"""`dfl` — одна команда коллеги: customer → бриф → engineer (режим test).

`dfl run` — идемпотентный шаг вперёд по журналу прогонов репо в `--cwd`:
бриф-интервью, предложение брифа, после мержа брифа — одобрение и
engineer-прогон, затем проверенный PR с кодом. `dfl status` — только журнал,
без форджи и эффектов. Конвейер исполняется в процессе
(`spec_loop.main`, `brief_tools.main`).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from governance import brief_tools as bt
from governance import df_config, spec_loop, user_targets
from governance import run_state as rs
from governance.facts import Outcome
from lite.init_repo import DEVTOOLS, init_repo, real_gh

ENGINEER_SUFFIX = bt.ENGINEER_SUFFIX
NEW_RUN_HINT = "первый запуск: dfl run --subject '<тема>'"


class DflError(Exception):
    """Отказ `dfl` с причиной и следующим шагом (код 2)."""


def main(argv: list[str] | None = None) -> int:
    """CLI: `--cwd <dir> run [--subject S] [--stakeholder R]` | `status` | `init-repo`."""
    parser = argparse.ArgumentParser(prog="dfl")
    parser.add_argument("--cwd", required=True, help="каталог репо цели")
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", help="следующий шаг конвейера")
    p_run.add_argument("--subject", help="тема нового прогона")
    p_run.add_argument("--stakeholder", default="customer")
    sub.add_parser("status", help="прогоны репо и что дальше")
    sub.add_parser("init-repo", help="PR с шаблонами DarkFactory в репо")
    args = parser.parse_args(argv)
    cwd = Path(args.cwd)
    try:
        _require_test_mode()
        if args.command == "status":
            return status(cwd)
        if args.command == "init-repo":
            return init_repo(cwd, DEVTOOLS, real_gh)
        return run(cwd, args.subject, args.stakeholder)
    except (DflError, df_config.ConfigError, user_targets.TargetsError) as exc:
        print(f"dfl: {exc}", file=sys.stderr)
        return 2


def _require_test_mode() -> None:
    """Конфиг обязан быть в режиме test (иначе отказ); печать баннера."""
    cfg = df_config.load()
    if cfg.mode != "test":
        raise DflError(
            f"dfl работает только в режиме test (config.toml: mode = {cfg.mode!r})"
            " — переустановите install.sh или работайте через make"
        )
    print(df_config.test_banner(cfg))


def run(cwd: Path, subject: str | None, role: str) -> int:
    """Один шаг конвейера для репо в `cwd`; код шага или 0/2 самого `dfl`."""
    repo = user_targets.register(cwd).repo
    runs, broken = _repo_runs(repo)
    if broken:
        dirs = ", ".join(str(rs.run_dir(b)) for b in broken)
        raise DflError(
            f"журнал прогона не читается: {dirs} — по нему не понять, закончен "
            "ли конвейер; почините или уберите каталог(и) и повторите dfl run"
        )
    cust = _select_customer(runs, subject)
    if cust is None:
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
    record = state.ops.get("code-accept") or {}
    if record.get("status") != "completed":
        return None
    url = (record.get("result") or {}).get("url")
    return str(url) if url else None


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
                "начинается; dfl run без --subject продолжит его"
            )
        return None
    if len(unfinished) == 1:
        return unfinished[0]
    if not unfinished:
        raise DflError(f"незаконченного прогона нет — {NEW_RUN_HINT}")
    names = ", ".join(c.run_id for c in unfinished)
    raise DflError(
        f"незаконченных прогонов несколько: {names} — выберите: "
        "dfl run --subject '<тема одного из них>'"
    )


def _engineer_step(eng: rs.RunState, repo: str) -> int:
    url = _accepted_url(eng)
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
        return url
    if _engineer_of(state, runs) is not None:
        return None  # бриф уже ушёл в engineer-прогон — шаг там
    if state.status == "brief_ready":
        return "мерж брифа, затем dfl run"
    if state.status.startswith("stopped_"):
        return "разберите причину выше и dfl run"
    return None


if __name__ == "__main__":
    sys.exit(main())
