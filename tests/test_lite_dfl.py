"""`dfl run` / `dfl status` (lite/dfl.py): один шаг вперёд по журналу прогонов.

Конвейер подменён рекордерами: `spec_loop.main`, `brief_tools.main`, факты
форджи brief-PR и `user_targets.register`; прогоны — в `tmp_path`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from governance import brief_tools as bt
from governance import interview as iv
from governance import run_state as rs
from governance import spec_loop, user_targets
from governance.brief_facts import BriefPrFacts
from governance.facts import Fact, Outcome
from lite import dfl
from tests.conftest import _STRICT_GH_DIRS
from tests.df_config_fixture import use_test_real_home, write_config

REPO = "alpha"
SLUG = "o/alpha"
URL = f"git@github.com:{SLUG}.git"


@dataclass
class Forge:
    """Факты brief-PR: ветка → номера PR, номер → состояние."""

    branches: dict[str, list[int]] = field(default_factory=dict)
    states: dict[int, str] = field(default_factory=dict)

    def find_brief_pr_fact(self, repo_slug: str, head_ref: str) -> Fact[list[int]]:
        assert repo_slug == SLUG
        return Fact(Outcome.FOUND, self.branches.get(head_ref, []), "ветка")

    def brief_pr_fact(self, repo_slug: str, pr: int) -> Fact[BriefPrFacts]:
        facts = BriefPrFacts(
            pr, self.states[pr], "main", "brief/x", "h", (), None, None, None
        )
        return Fact(Outcome.FOUND, facts, "pr")


@dataclass
class Env:
    """Журнал вызовов конвейера и подменённый форджа."""

    tmp: Path
    spec_calls: list[list[str]] = field(default_factory=list)
    brief_calls: list[list[str]] = field(default_factory=list)
    forge: Forge = field(default_factory=Forge)
    registered: int = 0

    def run(self, *args: str) -> int:
        return dfl.main(["--cwd", str(self.tmp / "target"), *args])


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    home = tmp_path / "home"
    home.mkdir()
    use_test_real_home(monkeypatch, home)
    xdg = tmp_path / "xdg"
    for key, value in write_config(xdg).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    (tmp_path / "target").mkdir()
    e = Env(tmp_path)

    def register(target_dir: Path) -> user_targets.UserTarget:
        e.registered += 1
        return user_targets.UserTarget(REPO, target_dir, URL)

    def spec_main(argv: list[str]) -> int:
        e.spec_calls.append(list(argv))
        return 0

    def brief_main(argv: list[str]) -> int:
        e.brief_calls.append(list(argv))
        return 0

    monkeypatch.setattr(user_targets, "register", register)
    monkeypatch.setattr(spec_loop, "main", spec_main)
    monkeypatch.setattr(bt, "main", brief_main)
    monkeypatch.setattr(dfl, "_forge", lambda: e.forge)
    return e


def _run(
    run_id: str,
    ws_id: str,
    frame: str,
    status: str,
    *,
    subject: str = "тема",
    repo: str = REPO,
) -> rs.RunState:
    state = rs.new_run(
        mode="test",
        subject=subject,
        repo=repo,
        repo_slug=SLUG,
        ws_id=ws_id,
        target_dir="/t",
        bundle_dir=f"workstreams/{ws_id}/spec",
        profile="profiles/team-exp.yaml",
        run_id=run_id,
        interview=iv.InterviewSpec(
            frame, "customer", SLUG, None, None, brief_only=frame == "customer"
        ).as_state(),
    )
    state.status = status
    rs.save(state)
    return state


def _accepted(state: rs.RunState, url: str = "https://github.com/o/alpha/pull/9"):
    state.ops["code-accept"] = {"status": "completed", "result": {"url": url}}
    rs.save(state)


def _approved_file(run_id: str) -> None:
    path = rs.run_dir(run_id) / bt.APPROVED_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("signed", encoding="utf-8")


def _no_effects(env: Env) -> bool:
    return not env.spec_calls and not env.brief_calls


# --- отказы до журнала ---


def test_refuses_strict_config(env: Env, capsys) -> None:
    write_config(env.tmp / "xdg", mode="strict", **_STRICT_GH_DIRS)
    assert env.run("run", "--subject", "s") == 2
    assert "только в режиме test" in capsys.readouterr().err
    assert _no_effects(env) and env.registered == 0


def test_run_non_github_origin_refused(env: Env, monkeypatch, capsys) -> None:
    def refuse(target_dir: Path) -> user_targets.UserTarget:
        raise user_targets.TargetsError("неизвестная форма remote-URL: 'x'")

    monkeypatch.setattr(user_targets, "register", refuse)
    assert env.run("run", "--subject", "s") == 2
    assert "неизвестная форма remote-URL" in capsys.readouterr().err
    assert _no_effects(env)


def test_first_run_requires_subject(env: Env, capsys) -> None:
    assert env.run("run") == 2
    assert "первый запуск: dfl run --subject '<тема>'" in capsys.readouterr().err
    assert _no_effects(env)


def test_banner_printed(env: Env, capsys) -> None:
    env.run("run")
    assert "режим test" in capsys.readouterr().out


# --- шаги конвейера ---


def test_first_run_starts_customer_brief_only(env: Env) -> None:
    assert env.run("run", "--subject", "новая тема") == 0
    assert env.spec_calls == [
        [
            "--subject",
            "новая тема",
            "--repo",
            REPO,
            "--need",
            "--frame",
            "customer",
            "--stakeholder",
            "customer",
            "--brief-only",
        ]
    ]


def test_customer_in_progress_resumes_by_run_id(env: Env) -> None:
    _run("WS-1-a", "WS-1", "customer", "running")
    assert env.run("run") == 0
    assert env.spec_calls == [
        ["--subject", "тема", "--repo", REPO, "--run-id", "WS-1-a"]
    ]
    assert not env.brief_calls


def test_brief_ready_unmerged_proposes_and_waits(env: Env, capsys) -> None:
    _run("WS-1-a", "WS-1", "customer", "brief_ready")
    env.forge.branches["brief/WS-1"] = [5]
    env.forge.states[5] = "OPEN"
    assert env.run("run") == 0
    assert env.brief_calls == [["propose", "--run", "WS-1-a"]]
    assert not env.spec_calls
    out = capsys.readouterr().out
    assert "бриф ждёт вашего мержа: https://github.com/o/alpha/pull/5" in out


def test_brief_merged_approves_then_starts_engineer(env: Env) -> None:
    cust = _run("WS-1-a", "WS-1", "customer", "brief_ready")
    env.forge.branches["brief/WS-1"] = [5]
    env.forge.states[5] = "MERGED"
    assert env.run("run", "--stakeholder", "po") == 0
    assert env.brief_calls == [
        ["propose", "--run", "WS-1-a"],
        ["approve", "--run", "WS-1-a", "--pr", "5"],
    ]
    assert env.spec_calls == [bt.engineer_args(cust, 5, "po")]
    assert "--brief-only" not in env.spec_calls[0]


def test_brief_approved_engineer_missing_starts_engineer_only(env: Env) -> None:
    cust = _run("WS-1-a", "WS-1", "customer", "brief_ready")
    _approved_file("WS-1-a")
    env.forge.branches["brief/WS-1"] = [5]
    env.forge.states[5] = "MERGED"
    assert env.run("run") == 0
    assert not env.brief_calls
    assert env.spec_calls == [bt.engineer_args(cust, 5, "customer")]


def test_propose_failure_stops(env: Env, monkeypatch) -> None:
    _run("WS-1-a", "WS-1", "customer", "brief_ready")
    monkeypatch.setattr(bt, "main", lambda argv: 2)
    assert env.run("run") == 2
    assert not env.spec_calls


def test_engineer_run_resumes(env: Env) -> None:
    _run("WS-1-a", "WS-1", "customer", "brief_ready")
    _run("WS-1-eng-b", "WS-1-eng", "engineer", "running")
    assert env.run("run") == 0
    assert env.spec_calls == [
        ["--subject", "тема", "--repo", REPO, "--run-id", "WS-1-eng-b"]
    ]
    assert not env.brief_calls


def test_code_accepted_prints_url_no_effects(env: Env, capsys) -> None:
    _run("WS-1-a", "WS-1", "customer", "brief_ready")
    _accepted(_run("WS-1-eng-b", "WS-1-eng", "engineer", "running"))
    assert env.run("run", "--subject", "тема") == 0
    out = capsys.readouterr().out
    assert (
        "PR с кодом проверен: https://github.com/o/alpha/pull/9; мерж — за вами"
    ) in out
    assert _no_effects(env)


# --- выбор customer-прогона (решение контроллера, Ruling 1) ---


def test_many_unfinished_customer_runs_refused(env: Env, capsys) -> None:
    _run("WS-1-a", "WS-1", "customer", "running", subject="t1")
    _run("WS-2-a", "WS-2", "customer", "brief_ready", subject="t2")
    assert env.run("run") == 2
    err = capsys.readouterr().err
    assert "WS-1-a" in err and "WS-2-a" in err
    assert _no_effects(env)


def test_new_subject_while_unfinished_refused(env: Env, capsys) -> None:
    _run("WS-1-a", "WS-1", "customer", "running", subject="t1")
    assert env.run("run", "--subject", "другая") == 2
    err = capsys.readouterr().err
    assert "WS-1-a" in err and "dfl run без --subject продолжит его" in err
    assert _no_effects(env)


def test_finished_pipeline_allows_new_subject(env: Env) -> None:
    _run("WS-1-a", "WS-1", "customer", "brief_ready", subject="t1")
    _accepted(_run("WS-1-eng-b", "WS-1-eng", "engineer", "running", subject="t1"))
    assert env.run("run", "--subject", "другая") == 0
    assert env.spec_calls[0][:2] == ["--subject", "другая"]
    assert "--brief-only" in env.spec_calls[0]


def test_finished_pipeline_without_subject_asks_for_one(env: Env, capsys) -> None:
    _run("WS-1-a", "WS-1", "customer", "brief_ready", subject="t1")
    _accepted(_run("WS-1-eng-b", "WS-1-eng", "engineer", "running", subject="t1"))
    assert env.run("run") == 2
    assert "dfl run --subject '<тема>'" in capsys.readouterr().err
    assert _no_effects(env)


def test_subject_selects_existing_run(env: Env) -> None:
    _run("WS-1-a", "WS-1", "customer", "running", subject="t1")
    _run("WS-2-a", "WS-2", "customer", "running", subject="t2")
    assert env.run("run", "--subject", "t2") == 0
    assert env.spec_calls == [["--subject", "t2", "--repo", REPO, "--run-id", "WS-2-a"]]


def test_other_repo_runs_ignored(env: Env) -> None:
    _run("WS-9-a", "WS-9", "customer", "running", repo="beta")
    assert env.run("run", "--subject", "s") == 0
    assert "--brief-only" in env.spec_calls[0]


# --- status ---


def test_status_lists_runs_no_effects(env: Env, monkeypatch, capsys) -> None:
    target = env.tmp / "target"
    monkeypatch.setattr(
        user_targets,
        "load",
        lambda: {REPO: user_targets.UserTarget(REPO, target.resolve(), URL)},
    )
    _run("WS-1-a", "WS-1", "customer", "brief_ready", subject="t1")
    _accepted(_run("WS-1-eng-b", "WS-1-eng", "engineer", "completed", subject="t1"))
    _run("WS-2-a", "WS-2", "customer", "stopped_interview", subject="t2")
    _run("WS-3-a", "WS-3", "customer", "brief_ready", subject="t3")
    _run("WS-9-a", "WS-9", "customer", "running", repo="beta")
    monkeypatch.setattr(dfl, "_forge", lambda: pytest.fail("форджа в status"))
    assert env.run("status") == 0
    out = capsys.readouterr().out
    assert "WS-1-a" in out and "WS-3-a" in out and "мерж брифа, затем dfl run" in out
    assert "WS-1-eng-b" in out and "https://github.com/o/alpha/pull/9" in out
    assert "WS-2-a" in out and "разберите причину выше и dfl run" in out
    assert "WS-9-a" not in out
    assert _no_effects(env) and env.registered == 0


def test_status_unregistered_repo_refused(env: Env, monkeypatch, capsys) -> None:
    monkeypatch.setattr(user_targets, "load", lambda: {})
    assert env.run("status") == 2
    assert "dfl run" in capsys.readouterr().err
