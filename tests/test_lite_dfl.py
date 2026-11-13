"""`dfl run` / `dfl status` (lite/dfl.py): один шаг вперёд по журналу прогонов.

Конвейер подменён рекордерами: `spec_loop.main`, `brief_tools.main`, факты
форджи brief-PR и `user_targets.register`; прогоны — в `tmp_path`.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from governance import brief_tools as bt
from governance import df_config, spec_loop, user_targets
from governance import interview as iv
from governance import run_state as rs
from governance.brief_facts import BriefPrFacts
from governance.facts import Fact, Outcome
from lite import dfl, runlog
from lite import failure_log as fl
from lite.output import VERBOSITY_ENV, verbosity
from tests.conftest import _STRICT_GH_DIRS
from tests.df_config_fixture import use_test_real_home, write_config

REPO = "alpha"
SLUG = "o/alpha"
URL = f"git@github.com:{SLUG}.git"
BRIEF_NEXT = "dfl run — откроет PR брифа (или покажет открытый) на ваш мерж"


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
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv(VERBOSITY_ENV, raising=False)
    (tmp_path / "target").mkdir()
    for rel in dfl.INIT_FILES:
        (tmp_path / "target" / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "target" / rel).write_text("x", encoding="utf-8")
    e = Env(tmp_path)

    def register(target_dir: Path) -> user_targets.UserTarget:
        e.registered += 1
        return user_targets.UserTarget(REPO, target_dir, URL)

    def spec_main(argv: list[str]) -> int:
        e.spec_calls.append(list(argv))
        print("runner-line")
        return 0

    def brief_main(argv: list[str]) -> int:
        e.brief_calls.append(list(argv))
        return 0

    monkeypatch.setattr(user_targets, "register", register)
    monkeypatch.setattr(spec_loop, "main", spec_main)
    monkeypatch.setattr(bt, "main", brief_main)
    monkeypatch.setattr(dfl, "_forge", lambda: e.forge)
    monkeypatch.setattr(dfl, "_is_tty", lambda: False)  # и под `pytest -s`
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
    err = capsys.readouterr().err
    assert "только в режиме test" in err
    assert 'поставьте mode = "test" в' in err and "config.toml" in err
    assert "переустановите" not in err
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
    assert "WS-1-a" in out and "WS-3-a" in out and BRIEF_NEXT in out
    # в репо есть незаконченные WS-2/WS-3: новую тему не предлагаем (#739)
    assert (
        "дальше: смержите https://github.com/o/alpha/pull/9 — конвейер закончен\n"
        in out
    )
    assert "новая тема" not in out
    assert "WS-2-a" in out and "разберите причину выше и dfl run" in out
    assert "WS-9-a" not in out
    assert _no_effects(env) and env.registered == 0


def test_status_unregistered_repo_refused(env: Env, monkeypatch, capsys) -> None:
    monkeypatch.setattr(user_targets, "load", lambda: {})
    assert env.run("status") == 2
    assert "dfl run" in capsys.readouterr().err


# --- раунд ревью 1 ---


def _status_target(env: Env, monkeypatch) -> None:
    target = env.tmp / "target"
    monkeypatch.setattr(
        user_targets,
        "load",
        lambda: {REPO: user_targets.UserTarget(REPO, target.resolve(), URL)},
    )


def test_status_refuses_strict_config(env: Env, monkeypatch, capsys) -> None:
    _status_target(env, monkeypatch)
    _run("WS-1-a", "WS-1", "customer", "running")
    write_config(env.tmp / "xdg", mode="strict", **_STRICT_GH_DIRS)
    assert env.run("status") == 2
    out, err = capsys.readouterr()
    assert "только в режиме test" in err and "WS-1-a" not in out


def test_code_accept_without_url_is_not_finished(env: Env, capsys) -> None:
    _run("WS-1-a", "WS-1", "customer", "brief_ready")
    eng = _run("WS-1-eng-b", "WS-1-eng", "engineer", "running")
    eng.ops["code-accept"] = {"status": "completed", "result": {}}
    rs.save(eng)
    assert env.run("run") == 0
    assert env.spec_calls == [
        ["--subject", "тема", "--repo", REPO, "--run-id", "WS-1-eng-b"]
    ]
    assert "PR с кодом проверен" not in capsys.readouterr().out


def test_code_merged_unreviewed_is_finished(env: Env, capsys) -> None:
    """Ревью #733: мерж человека без приёмки закрывает конвейер для dfl."""
    _run("WS-1-a", "WS-1", "customer", "brief_ready")
    eng = _run("WS-1-eng-b", "WS-1-eng", "engineer", "completed")
    url = "https://github.com/o/alpha/pull/9"
    eng.ops["code-accept"] = {
        "status": "completed",
        "result": {"url": url, "merged": True, "reviewed": False},
    }
    rs.save(eng)
    assert env.run("run", "--subject", "тема") == 0
    out = capsys.readouterr().out
    assert f"PR с кодом вмержен без автоматической приёмки: {url}" in out
    assert _no_effects(env)
    assert "PR с кодом проверен" not in out


def test_approve_failure_stops_before_engineer(env: Env, monkeypatch) -> None:
    _run("WS-1-a", "WS-1", "customer", "brief_ready")
    env.forge.branches["brief/WS-1"] = [5]
    env.forge.states[5] = "MERGED"
    calls: list[list[str]] = []

    def brief_main(argv: list[str]) -> int:
        calls.append(list(argv))
        return 1 if argv[0] == "approve" else 0

    monkeypatch.setattr(bt, "main", brief_main)
    assert env.run("run") == 1
    assert [c[0] for c in calls] == ["propose", "approve"]
    assert not env.spec_calls


def _broken(run_id: str) -> None:
    path = rs.run_dir(run_id) / "run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")


def test_unreadable_ledger_refuses_run(env: Env, capsys) -> None:
    _broken("WS-1-a")
    assert env.run("run", "--subject", "новая") == 2
    err = capsys.readouterr().err
    assert "не читается" in err and str(rs.run_dir("WS-1-a")) in err
    assert _no_effects(env)


def test_unreadable_ledger_listed_in_status(env: Env, monkeypatch, capsys) -> None:
    _status_target(env, monkeypatch)
    _broken("WS-1-a")
    _run("WS-2-a", "WS-2", "customer", "running")
    assert env.run("status") == 0
    out = capsys.readouterr().out
    assert "WS-1-a  не читается" in out and "WS-2-a" in out


# --- финальное ревью: C1 (dfl answer), I1 (abandon), I2 (цепочка), I3 (init) ---


@dataclass
class Discovery:
    """Подмена `_discovery_answer`: журнал вызовов и код ответа."""

    code: int = 20
    calls: list[tuple[str, str, Path, Path]] = field(default_factory=list)
    flags: list[tuple[str | None, bool]] = field(default_factory=list)

    def __call__(
        self,
        session_id: str,
        role: str,
        file: Path,
        cwd: Path,
        *,
        question: str | None = None,
        supersede: bool = False,
    ) -> iv.DiscoveryReply:
        self.calls.append((session_id, role, file, cwd))
        self.flags.append((question, supersede))
        reason = "payload missing text" if self.code in (1, 2) else ""
        envelope = {"operation": {"status": "ok", "reason": reason}}
        return iv.DiscoveryReply(self.code, envelope, "")


def _waiting(run_id: str, ws_id: str, frame: str) -> rs.RunState:
    state = _run(run_id, ws_id, frame, "waiting_interview")
    assert state.interview is not None
    state.interview["session_id"] = f"sess-{run_id}"
    rs.save(state)
    return state


def _answer_file(env: Env) -> Path:
    path = env.tmp / "target" / "answer.yaml"
    path.write_text("text: да\n", encoding="utf-8")
    return path


def test_answer_runs_discovery_then_continues(env: Env, monkeypatch, capsys) -> None:
    _waiting("WS-1-a", "WS-1", "customer")
    _answer_file(env)
    disc = Discovery()
    monkeypatch.setattr(dfl, "_discovery_answer", disc)
    monkeypatch.setattr(dfl, "_discovery_status", lambda s, c: _question_reply())

    def spec_main(argv: list[str]) -> int:
        env.spec_calls.append(list(argv))
        _waiting("WS-1-a", "WS-1", "customer")  # следующий вопрос
        return 0

    monkeypatch.setattr(spec_loop, "main", spec_main)
    assert env.run("answer", "-v", "--file", "answer.yaml") == 0
    sid, role, file, cwd = disc.calls[0]
    assert (sid, role) == ("sess-WS-1-a", "customer")
    assert file == (env.tmp / "target" / "answer.yaml").resolve()
    assert cwd == rs.run_dir("WS-1-a")
    assert env.spec_calls == [
        ["--subject", "тема", "--repo", REPO, "--run-id", "WS-1-a"]
    ]
    out = capsys.readouterr().out
    assert "ответьте: dfl answer --file answer.yaml" in out and "text:" in out
    assert "entries:" in out and "--supersede" in out
    assert disc.flags == [(None, False)]


def test_answer_supersede_passes_question(env: Env, monkeypatch) -> None:
    """Находка живого прогона 11.11: поправить данный ответ можно только
    `discovery answer --question --supersede`, который dfl запрещает звать."""
    _waiting("WS-1-a", "WS-1", "customer")
    _answer_file(env)
    disc = Discovery()
    monkeypatch.setattr(dfl, "_discovery_answer", disc)
    argv = ("answer", "--file", "answer.yaml", "--question", "q-3", "--supersede")
    assert env.run(*argv) == 0
    assert disc.flags == [("q-3", True)]


def test_answer_supersede_without_question_refused(
    env: Env, monkeypatch, capsys
) -> None:
    _waiting("WS-1-a", "WS-1", "customer")
    _answer_file(env)
    monkeypatch.setattr(dfl, "_discovery_answer", lambda *a, **k: pytest.fail("вызов"))
    assert env.run("answer", "--file", "answer.yaml", "--supersede") == 2
    assert "--supersede требует --question" in capsys.readouterr().err
    assert _no_effects(env)


def test_answer_targets_engineer_run(env: Env, monkeypatch) -> None:
    _run("WS-1-a", "WS-1", "customer", "brief_ready")
    _waiting("WS-1-eng-b", "WS-1-eng", "engineer")
    _answer_file(env)
    disc = Discovery()
    monkeypatch.setattr(dfl, "_discovery_answer", disc)
    assert env.run("answer", "--file", "answer.yaml") == 0
    assert disc.calls[0][0] == "sess-WS-1-eng-b"
    assert env.spec_calls[0][-1] == "WS-1-eng-b"


def test_answer_refused_by_discovery_names_file(env: Env, monkeypatch, capsys) -> None:
    _waiting("WS-1-a", "WS-1", "customer")
    _answer_file(env)
    monkeypatch.setattr(dfl, "_discovery_answer", Discovery(code=1))
    assert env.run("answer", "--file", "answer.yaml") == 2
    err = capsys.readouterr().err
    assert "payload missing text" in err and "повторите dfl answer" in err
    assert _no_effects(env)


def test_answer_when_not_waiting_refused(env: Env, monkeypatch, capsys) -> None:
    _run("WS-1-a", "WS-1", "customer", "running")
    _answer_file(env)
    monkeypatch.setattr(dfl, "_discovery_answer", lambda *a: pytest.fail("вызов"))
    assert env.run("answer", "--file", "answer.yaml") == 2
    assert "не ждёт ответа" in capsys.readouterr().err
    assert _no_effects(env)


def test_answer_missing_file_shows_format(env: Env, monkeypatch, capsys) -> None:
    _waiting("WS-1-a", "WS-1", "customer")
    monkeypatch.setattr(dfl, "_discovery_answer", lambda *a: pytest.fail("вызов"))
    assert env.run("answer", "--file", "nope.yaml") == 2
    err = capsys.readouterr().err
    assert "нет файла ответа" in err and "text:" in err and "entries:" in err


def test_discovery_answer_invocation(env: Env, monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(argv: list[str], **kw: object):
        seen["argv"], seen["cwd"] = argv, kw.get("cwd")
        return dfl.subprocess.CompletedProcess(argv, 1, "", "boom")

    disc_dir = env.tmp / "discovery"
    monkeypatch.setattr(dfl.df_config, "workspace_dir", lambda name: disc_dir)
    monkeypatch.setattr(dfl.subprocess, "run", fake_run)
    reply = dfl._discovery_answer("s1", "customer", Path("/a.yaml"), Path("/rd"))
    assert seen["argv"] == [
        "uv", "run", "--frozen", "--project", str(disc_dir), "discovery",
        "answer", "--session", "s1", "--role", "customer", "--file", "/a.yaml",
    ]  # fmt: skip
    assert seen["cwd"] == Path("/rd") and reply.code == 1
    dfl._discovery_answer(
        "s1", "customer", Path("/a.yaml"), Path("/rd"), question="q-2", supersede=True
    )
    assert seen["argv"][-3:] == ["--question", "q-2", "--supersede"]


def test_waiting_run_prints_dfl_answer_hint(env: Env, monkeypatch, capsys) -> None:
    monkeypatch.setattr(dfl, "_discovery_status", lambda s, c: _question_reply())

    def spec_main(argv: list[str]) -> int:
        _waiting("WS-1-a", "WS-1", "customer")
        return 0

    monkeypatch.setattr(spec_loop, "main", spec_main)
    assert env.run("run", "--subject", "тема") == 0
    out = capsys.readouterr().out
    assert "Кто ваш клиент?" in out
    assert "ответьте: dfl answer --file answer.yaml" in out


def test_status_waiting_hint(env: Env, monkeypatch, capsys) -> None:
    _status_target(env, monkeypatch)
    _waiting("WS-1-a", "WS-1", "customer")
    assert env.run("status") == 0
    assert "дальше: ответьте: dfl answer --file answer.yaml" in capsys.readouterr().out


def test_abandon_moves_pipeline_runs(env: Env, capsys) -> None:
    _run("WS-1-a", "WS-1", "customer", "brief_ready")
    _run("WS-1-eng-b", "WS-1-eng", "engineer", "stopped_gate")
    _run("WS-9-a", "WS-9", "customer", "running", repo="beta")
    assert env.run("abandon") == 0
    assert rs.all_run_ids() == ["WS-9-a"]
    moved = list((rs.RUNS_ROOT.parent / "governance-runs-abandoned").glob("*/*"))
    assert sorted(p.name for p in moved) == ["WS-1-a", "WS-1-eng-b"]
    assert all((p / "run.json").is_file() for p in moved)
    out = capsys.readouterr().out
    assert "брошен: WS-1-a" in out and "закройте их сами" in out
    assert _no_effects(env)
    assert env.run("run", "--subject", "новая") == 0  # новая тема снова можно


def test_abandon_nothing_refused(env: Env, capsys) -> None:
    assert env.run("abandon") == 2
    assert "незаконченного прогона нет" in capsys.readouterr().err


def test_abandon_busy_run_refused(env: Env, capsys) -> None:
    from governance import run_lock

    _run("WS-1-a", "WS-1", "customer", "running")
    with run_lock.run_lock("WS-1-a"):
        assert env.run("abandon") == 2
    assert "исполняется другим процессом" in capsys.readouterr().err
    assert rs.all_run_ids() == ["WS-1-a"]


def test_new_subject_refusal_names_abandon(env: Env, capsys) -> None:
    _run("WS-1-a", "WS-1", "customer", "running", subject="t1")
    assert env.run("run", "--subject", "другая") == 2
    assert "dfl abandon" in capsys.readouterr().err


def test_closed_brief_pr_names_abandon(env: Env, capsys) -> None:
    _run("WS-1-a", "WS-1", "customer", "brief_ready")
    env.forge.branches["brief/WS-1"] = [5]
    env.forge.states[5] = "CLOSED"
    assert env.run("run") == 2
    err = capsys.readouterr().err
    assert "закрыт без мержа" in err and "dfl abandon" in err
    assert "--repropose" not in err
    assert _no_effects(env)


def test_completed_engineer_with_stopped_chain(env: Env, monkeypatch, capsys) -> None:
    _status_target(env, monkeypatch)
    _run("WS-1-a", "WS-1", "customer", "brief_ready")
    _run("WS-1-eng-b", "WS-1-eng", "engineer", "completed")
    assert env.run("status") == 0
    assert "конвейер после прогона остановился" in capsys.readouterr().out
    assert env.run("run") == 0
    assert env.spec_calls == [
        ["--subject", "тема", "--repo", REPO, "--run-id", "WS-1-eng-b"]
    ]
    assert env.run("run", "--subject", "другая") == 2  # конвейер не закончен


def test_new_pipeline_requires_init_files(env: Env, capsys) -> None:
    (env.tmp / "target" / "scripts" / "review" / "local.sh").unlink()
    assert env.run("run", "--subject", "s") == 2
    err = capsys.readouterr().err
    assert "scripts/review/local.sh" in err
    assert "dfl init-repo, смержите его PR и git pull" in err
    assert _no_effects(env)


def test_resume_does_not_require_init_files(env: Env) -> None:
    (env.tmp / "target" / "spec-runner.config.yaml").unlink()
    _run("WS-1-a", "WS-1", "customer", "running")
    assert env.run("run") == 0
    assert env.spec_calls


@pytest.mark.parametrize("reviewed", [True, False])
def test_status_merged_code_pr_is_finished(
    env: Env, monkeypatch, capsys, reviewed: bool
) -> None:
    """devtools#737: вмерженный PR с кодом — не «дальше: <url>», а конец."""
    _status_target(env, monkeypatch)
    eng = _run("WS-1-eng-b", "WS-1-eng", "engineer", "completed")
    url = "https://github.com/o/alpha/pull/9"
    eng.ops["code-accept"] = {
        "status": "completed",
        "result": {"url": url, "merged": True, "reviewed": reviewed},
    }
    rs.save(eng)
    assert env.run("status") == 0
    out = capsys.readouterr().out
    assert f"конвейер закончен ({url} вмержен)" in out
    assert "смержите" not in out


def test_run_after_reviewed_merge_says_finished(env: Env, capsys) -> None:
    """Ревью одобрило, приёмка не завершилась, человек вмержил (ревью #739):
    не «мерж — за вами» и не «проверен» — оговорка приёмки сохраняется."""
    _run("WS-1-a", "WS-1", "customer", "brief_ready")
    eng = _run("WS-1-eng-b", "WS-1-eng", "engineer", "completed")
    url = "https://github.com/o/alpha/pull/9"
    eng.ops["code-accept"] = {
        "status": "completed",
        "result": {"url": url, "merged": True, "reviewed": True},
    }
    rs.save(eng)
    assert env.run("run", "--subject", "тема") == 0
    out = capsys.readouterr().out
    assert (
        "PR с кодом вмержен человеком после одобряющего ревью, приёмка не "
        f"завершена: {url}; конвейер закончен"
    ) in out
    assert "мерж — за вами" not in out and "проверен" not in out
    assert _no_effects(env)


def test_status_hint_for_accepted_pr_is_runnable(env: Env, monkeypatch, capsys) -> None:
    """Ревью #739: подсказка не ведёт в `dfl run`, который откажет кодом 2."""
    _status_target(env, monkeypatch)
    _accepted(_run("WS-1-eng-b", "WS-1-eng", "engineer", "completed"))
    assert env.run("status") == 0
    out = capsys.readouterr().out
    assert "затем dfl run" not in out
    assert "новая тема: dfl run --subject '<тема>'" in out
    assert env.run("run") == 2  # и правда: без --subject продолжать нечего


# --- запись сбоя и подсказка dfl report (план dfl-report, Task 3) ---


def test_refusal_writes_failure_record_and_hint(env: Env, capsys) -> None:
    assert env.run("run", "-v") == 2  # первый запуск без --subject
    rec = fl.load(env.tmp / "target")
    assert rec is not None
    assert (rec.command, rec.exit_code) == ("run", 2)
    assert rec.cause.startswith("незаконченного прогона нет")
    assert capsys.readouterr().err.rstrip().endswith(dfl.REPORT_HINT)


def test_success_keeps_previous_record(env: Env, monkeypatch) -> None:
    assert env.run("run") == 2
    first = fl.load(env.tmp / "target")
    monkeypatch.setattr(dfl, "status", lambda cwd: 0)
    assert env.run("status") == 0
    assert fl.load(env.tmp / "target") == first


def test_failure_before_registration_is_recorded(env: Env, monkeypatch) -> None:
    def refuse(**_: object) -> None:
        raise dfl.DflError("dfl работает только в режиме test — поставьте test")

    monkeypatch.setattr(dfl, "_require_test_mode", refuse)
    assert env.run("status") == 2
    rec = fl.load(env.tmp / "target")
    assert rec is not None and rec.cause == "dfl работает только в режиме test"
    assert env.registered == 0


def test_unhandled_exception_recorded_and_reraised(env: Env, monkeypatch) -> None:
    def boom(cwd: Path) -> int:
        raise RuntimeError("boom\nподробно")

    monkeypatch.setattr(dfl, "status", boom)
    with pytest.raises(RuntimeError):
        env.run("status")
    rec = fl.load(env.tmp / "target")
    assert rec is not None
    assert (rec.exit_code, rec.cause) == (1, "RuntimeError: boom")


def test_file_argument_reduced_to_name(env: Env, monkeypatch) -> None:
    def refuse(*a, **kw) -> int:
        raise dfl.DflError("нет")

    monkeypatch.setattr(dfl, "answer", refuse)
    assert env.run("answer", "--file", "/abs/secret/dir/answer.yaml") == 2
    rec = fl.load(env.tmp / "target")
    assert rec is not None
    assert rec.argv == ["answer", "--file", "answer.yaml"]


def test_record_write_failure_keeps_exit_code(env: Env, monkeypatch, capsys) -> None:
    def fail(cwd: Path, rec: fl.FailureRecord) -> None:
        raise OSError("диск полон")

    monkeypatch.setattr(fl, "write", fail)
    assert env.run("run") == 2
    assert "запись сбоя не удалась: диск полон" in capsys.readouterr().err


def test_deferred_record_gets_tail(env: Env) -> None:
    tail = ["шум", "dfl run: отказ (код 2)", "  причина:     нет", "  что сделать: x"]
    pending: list = []
    assert dfl.main(["--cwd", str(env.tmp / "target"), "run"], defer=pending) == 2
    assert fl.load(env.tmp / "target") is None  # отложено
    for job in pending:
        job(list(tail))
    rec = fl.load(env.tmp / "target")
    assert rec is not None and rec.output_tail == tail


def test_cli_prints_hint_after_traceback(env: Env, monkeypatch, capsys) -> None:
    def boom(cwd: Path) -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr(dfl, "status", boom)
    monkeypatch.setattr(dfl, "tee_output", _no_tee)
    assert dfl.cli(["--cwd", str(env.tmp / "target"), "status"]) == 1
    err = capsys.readouterr().err
    assert "RuntimeError: boom" in err
    assert err.rstrip().endswith(dfl.REPORT_HINT)


def test_cli_ctrl_c_is_quiet(env: Env, monkeypatch, capsys) -> None:
    """I1: Ctrl-C вне разговора — одна строка «прервано», код 130, без трассировки."""

    def interrupted(cwd: Path) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(dfl, "status", interrupted)
    monkeypatch.setattr(dfl, "tee_output", _no_tee)
    assert dfl.cli(["--cwd", str(env.tmp / "target"), "status"]) == 130
    captured = capsys.readouterr()
    assert captured.err.strip() == "прервано"
    assert "Traceback" not in captured.err + captured.out


class _NoTail:
    def lines(self) -> list[str]:
        return []


from contextlib import contextmanager  # noqa: E402


@contextmanager
def _no_tee(max_lines: int = 200):
    yield _NoTail()


# --- dfl report: маршрут (Task 5) ---


def test_report_routed_before_test_mode(env: Env, monkeypatch) -> None:
    seen: list[tuple[Path, bool]] = []

    def refuse() -> None:
        raise AssertionError("report не должен требовать режим test")

    def fake_report(cwd: Path, *, print_only: bool) -> int:
        seen.append((cwd, print_only))
        return 2

    monkeypatch.setattr(dfl, "_require_test_mode", refuse)
    monkeypatch.setattr(dfl, "run_report", fake_report)
    assert env.run("report", "--print") == 2
    assert seen == [(env.tmp / "target", True)]
    assert fl.load(env.tmp / "target") is None  # отказ report не пишет запись


def test_report_failure_has_no_report_hint(env: Env, monkeypatch, capsys) -> None:
    monkeypatch.setattr(dfl, "run_report", lambda cwd, *, print_only: 2)
    assert env.run("report") == 2
    assert dfl.REPORT_HINT not in capsys.readouterr().err


def test_public_argv_handles_equals_form() -> None:
    raw = [
        "--cwd=/home/a/repo",
        "answer",
        "--file=/home/a/s/answer.yaml",
        "--subject",
        "t",
    ]
    assert dfl._public_argv(raw) == ["answer", "--file=answer.yaml", "--subject", "t"]


# --- Интервью разговором (Task 4) -------------------------------------------


@dataclass
class Chat:
    """Подделка цикла интервью: итоги по очереди, запоминает прогоны."""

    results: list[str] = field(default_factory=lambda: ["finished"])
    states: list[str] = field(default_factory=list)
    quiet: list[bool] = field(default_factory=list)

    def __call__(self, state: rs.RunState) -> object:
        from governance import runner

        self.states.append(state.run_id)
        self.quiet.append(runner._QUIET_HINTS.get())
        rs.save(_run_status(state, "running"))
        return dfl.ic.ChatResult(self.results.pop(0))


def _run_status(state: rs.RunState, status: str) -> rs.RunState:
    state.status = status
    return state


def _tty(monkeypatch, on: bool = True) -> None:
    monkeypatch.setattr(dfl, "_is_tty", lambda: on)


def _waiting_after_first_step(monkeypatch, env: Env) -> None:
    """Первый шаг конвейера оставляет прогон ждать ответа, следующие — нет."""

    def spec_main(argv: list[str]) -> int:
        env.spec_calls.append(list(argv))
        if len(env.spec_calls) == 1:
            _waiting("WS-1-a", "WS-1", "customer")
        return 0

    monkeypatch.setattr(spec_loop, "main", spec_main)


def test_dfl_run_without_tty_keeps_old_hint(env: Env, monkeypatch, capsys) -> None:
    _tty(monkeypatch, False)
    _waiting_after_first_step(monkeypatch, env)
    monkeypatch.setattr(dfl, "_chat", lambda state: pytest.fail("цикл без tty"))
    monkeypatch.setattr(dfl, "_discovery_status", lambda s, c: _question_reply())
    assert env.run("run", "--subject", "тема") == 0
    out = capsys.readouterr().out
    assert "Кто ваш клиент?" in out and "dfl answer --file" in out
    assert len(env.spec_calls) == 1


def test_dfl_run_tty_chats_then_continues(env: Env, monkeypatch, capsys) -> None:
    _tty(monkeypatch)
    _waiting_after_first_step(monkeypatch, env)
    chat = Chat()
    monkeypatch.setattr(dfl, "_chat", chat)
    assert env.run("run", "--subject", "тема") == 0
    assert chat.states == ["WS-1-a"] and chat.quiet == [True]
    # после интервью — тот же следующий шаг, что после dfl answer
    assert env.spec_calls[1] == ["--subject", "тема", "--repo", REPO,
                                 "--run-id", "WS-1-a"]  # fmt: skip
    out = capsys.readouterr().out
    assert "dfl answer --file" not in out and "make spec-loop" not in out


def test_dfl_run_tty_stop_says_how_to_resume(env: Env, monkeypatch, capsys) -> None:
    _tty(monkeypatch)
    _waiting_after_first_step(monkeypatch, env)
    monkeypatch.setattr(dfl, "_chat", Chat(results=["stopped"]))
    assert env.run("run", "--subject", "тема") == 0
    assert "интервью прервано — продолжить: dfl run" in capsys.readouterr().out
    assert len(env.spec_calls) == 1


def test_dfl_run_tty_agent_down_refused(env: Env, monkeypatch, capsys) -> None:
    _tty(monkeypatch)
    _waiting_after_first_step(monkeypatch, env)

    def down(state: rs.RunState) -> object:
        raise dfl.ic.ChatError("агент недоступен: claude не найден")

    monkeypatch.setattr(dfl, "_chat", down)
    assert env.run("run", "--subject", "тема") == 2
    assert "агент недоступен: claude не найден" in capsys.readouterr().err


def test_dfl_run_tty_non_claude_harness_refused(env: Env, monkeypatch, capsys) -> None:
    cfg = env.tmp / "xdg" / "darkfactory" / "config.toml"
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace('author = "claude"', 'author = "x"'),
        encoding="utf-8",
    )
    _tty(monkeypatch)
    _waiting_after_first_step(monkeypatch, env)
    monkeypatch.setattr(dfl, "_chat", lambda state: pytest.fail("цикл"))
    assert env.run("run", "--subject", "тема") == 2
    err = capsys.readouterr().err
    assert "интервью разговором требует harness.author = claude" in err
    assert "dfl answer --file answer.yaml" in err


def test_dfl_answer_file_still_works(env: Env, monkeypatch) -> None:
    _tty(monkeypatch)
    _waiting("WS-1-a", "WS-1", "customer")
    _answer_file(env)
    disc = Discovery()
    monkeypatch.setattr(dfl, "_discovery_answer", disc)
    monkeypatch.setattr(dfl, "_chat", lambda state: pytest.fail("цикл при --file"))
    monkeypatch.setattr(
        spec_loop, "main", lambda argv: env.spec_calls.append(argv) or 0
    )
    assert env.run("answer", "--file", "answer.yaml") == 0
    assert disc.flags == [(None, False)] and len(disc.calls) == 1


def test_dfl_answer_without_file_chats(env: Env, monkeypatch, capsys) -> None:
    _tty(monkeypatch)
    _waiting("WS-1-a", "WS-1", "customer")
    chat = Chat()
    monkeypatch.setattr(dfl, "_chat", chat)
    assert env.run("answer") == 0
    assert chat.states == ["WS-1-a"]
    assert env.spec_calls == [
        ["--subject", "тема", "--repo", REPO, "--run-id", "WS-1-a"]
    ]


def test_dfl_answer_without_file_needs_tty(env: Env, monkeypatch, capsys) -> None:
    _tty(monkeypatch, False)
    _waiting("WS-1-a", "WS-1", "customer")
    monkeypatch.setattr(dfl, "_chat", lambda state: pytest.fail("цикл без tty"))
    assert env.run("answer") == 2
    assert "без терминала: dfl answer --file answer.yaml" in capsys.readouterr().err
    assert _no_effects(env)


def test_dfl_answer_question_requires_file(env: Env, monkeypatch, capsys) -> None:
    _tty(monkeypatch)
    _waiting("WS-1-a", "WS-1", "customer")
    monkeypatch.setattr(dfl, "_chat", lambda state: pytest.fail("цикл"))
    assert env.run("answer", "--question", "q-1") == 2
    assert "только вместе с --file" in capsys.readouterr().err
    assert env.run("answer", "--question", "q-1", "--supersede") == 2
    assert _no_effects(env)


def test_discovery_status_invocation(env: Env, monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(argv: list[str], **kw: object):
        seen["argv"], seen["cwd"] = argv, kw.get("cwd")
        return dfl.subprocess.CompletedProcess(argv, 1, "", "boom")

    disc_dir = env.tmp / "discovery"
    monkeypatch.setattr(dfl.df_config, "workspace_dir", lambda name: disc_dir)
    monkeypatch.setattr(dfl.subprocess, "run", fake_run)
    reply = dfl._discovery_status("s1", Path("/rd"))
    assert seen["argv"] == [
        "uv", "run", "--frozen", "--project", str(disc_dir), "discovery",
        "status", "--session", "s1",
    ]  # fmt: skip
    assert seen["cwd"] == Path("/rd") and reply.code == 1


def test_send_writes_temp_answer_with_question(env: Env, monkeypatch) -> None:
    state = _waiting("WS-1-a", "WS-1", "customer")
    seen: dict[str, object] = {}

    def fake_answer(sid, role, file, cwd, *, question=None, supersede=False):
        seen.update(sid=sid, role=role, cwd=cwd, question=question)
        seen["supersede"], seen["text"] = supersede, file.read_text(encoding="utf-8")
        seen["file"] = file
        return iv.DiscoveryReply(0, {}, "")

    monkeypatch.setattr(dfl, "_discovery_answer", fake_answer)
    reply = dfl._sender(state)("q-1", "text: да\n")
    assert reply.code == 0
    assert (seen["sid"], seen["role"], seen["question"]) == (
        "sess-WS-1-a",
        "customer",
        "q-1",
    )
    assert seen["supersede"] is False and seen["text"] == "text: да\n"
    assert seen["cwd"] == rs.run_dir("WS-1-a")
    file = seen["file"]
    assert isinstance(file, Path) and file.parent == rs.run_dir("WS-1-a")
    assert not file.exists()


# --- dfl interview / dfl correct ---


def _session_view():
    from lite.interview_state import Entry, SessionView

    fr = {"Priority": "Must", "Acceptance": "ок", "traces": ["G-01"]}
    return SessionView(
        frame="customer",
        required={"goals": "G", "functions": "FR"},
        optional={},
        entries=[Entry("G-01", "Цель", {}), Entry("FR-01", "раз в неделю", fr)],
        declared=["G-01", "FR-01"],
        holders={"G-01": "q-g", "FR-01": "q-f"},
        answers={
            "q-g": {"text": "ц", "entries": [{"id": "G-01", "body": "Цель"}]},
            "q-f": {
                "text": "ф",
                "entries": [{"id": "FR-01", "body": "раз в неделю", **fr}],
            },
        },
        answered_keys=["goals", "functions"],
        readiness="ready",
    )


def test_dfl_interview_without_session_refuses(env: Env, capsys) -> None:
    _run("WS-1-a", "WS-1", "customer", "brief_ready")  # без session_id
    assert env.run("interview") == 2
    err = capsys.readouterr().err
    assert "интервью не идёт" in err and "начните: dfl run --subject" in err


def test_dfl_interview_no_runs_refuses(env: Env, capsys) -> None:
    assert env.run("interview", "--subject", "новая") == 2
    assert "интервью не идёт" in capsys.readouterr().err


def test_dfl_interview_prints_without_tty_and_agent(
    env: Env, monkeypatch, capsys
) -> None:
    _waiting("WS-1-a", "WS-1", "customer")
    monkeypatch.setattr(dfl, "load_session", lambda sid: _session_view())
    action = {"question_id": "q", "coverage_key": "goals", "question_text": "Зачем?"}
    reply = iv.DiscoveryReply(20, {"next_action": action}, "")
    monkeypatch.setattr(dfl, "_discovery_status", lambda sid, cwd: reply)
    assert env.run("interview") == 0
    out = capsys.readouterr().out
    assert "Тема: тема" in out and "Вопрос 3 (цели): Зачем?" in out
    assert "FR-01 раз в неделю — Must; критерий: ок; → G-01" in out


def test_dfl_interview_status_failure_is_not_no_questions(
    env: Env, monkeypatch, capsys
) -> None:
    _waiting("WS-1-a", "WS-1", "customer")
    monkeypatch.setattr(dfl, "load_session", lambda sid: _session_view())
    reply = iv.DiscoveryReply(1, {"operation": {"reason": "упал"}}, "")
    monkeypatch.setattr(dfl, "_discovery_status", lambda sid, cwd: reply)
    assert env.run("interview") == 0
    out = capsys.readouterr().out
    assert "текущий вопрос не получен (упал)" in out
    assert "вопросов больше нет" not in out
    assert "FR-01 раз в неделю" in out


def test_dfl_interview_prefers_engineer_session(env: Env, monkeypatch) -> None:
    _run("WS-1-a", "WS-1", "customer", "brief_ready")
    _waiting("WS-1-eng-b", "WS-1-eng", "engineer")
    seen: list[str] = []
    monkeypatch.setattr(
        dfl, "load_session", lambda sid: seen.append(sid) or _session_view()
    )
    monkeypatch.setattr(
        dfl, "_discovery_status", lambda sid, cwd: iv.DiscoveryReply(0, {}, "")
    )
    assert env.run("interview") == 0
    assert seen == ["sess-WS-1-eng-b"]


def _claude_agent_returning(monkeypatch, answer: str) -> None:
    monkeypatch.setattr(dfl.st, "claude_agent", lambda model: lambda prompt: answer)


def test_dfl_correct_sends_supersede_to_holder(env: Env, monkeypatch, capsys) -> None:
    _waiting("WS-1-a", "WS-1", "customer")
    monkeypatch.setattr(dfl, "load_session", lambda sid: _session_view())
    item = {"id": "FR-01", "prefix": "FR", "body": "раз в месяц", "traces": ["G-01"]}
    _claude_agent_returning(monkeypatch, json.dumps({"items": [item], "ask": None}))
    disc = Discovery()
    monkeypatch.setattr(dfl, "_discovery_answer", disc)
    assert env.run("correct", "не раз в неделю, а раз в месяц") == 0
    assert disc.flags == [("q-f", True)]
    assert "FR-01: «раз в неделю» → «раз в месяц»" in capsys.readouterr().out


def test_dfl_correct_ask_prints_clarification_without_send(
    env: Env, monkeypatch, capsys
) -> None:
    _waiting("WS-1-a", "WS-1", "customer")
    monkeypatch.setattr(dfl, "load_session", lambda sid: _session_view())
    _claude_agent_returning(monkeypatch, '{"items": [], "ask": "Что поправить?"}')
    monkeypatch.setattr(dfl, "_discovery_answer", lambda *a, **k: pytest.fail("вызов"))
    assert env.run("correct", "поменяй") == 0
    out = capsys.readouterr().out
    assert "Уточните: Что поправить? — повторите dfl correct" in out


def test_dfl_correct_non_claude_harness_refused(env: Env, capsys) -> None:
    cfg = env.tmp / "xdg" / "darkfactory" / "config.toml"
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace('author = "claude"', 'author = "x"'),
        encoding="utf-8",
    )
    _waiting("WS-1-a", "WS-1", "customer")
    assert env.run("correct", "x") == 2
    assert "harness.author = claude" in capsys.readouterr().err


def test_dfl_correct_agent_down_refused(env: Env, monkeypatch, capsys) -> None:
    _waiting("WS-1-a", "WS-1", "customer")
    monkeypatch.setattr(dfl, "load_session", lambda sid: _session_view())

    def down(prompt: str) -> str:
        raise dfl.st.AgentUnavailable("нет claude")

    monkeypatch.setattr(dfl.st, "claude_agent", lambda model: down)
    assert env.run("correct", "x") == 2
    assert "нет claude" in capsys.readouterr().err


def test_dfl_interview_finished_pipeline_without_subject(
    env: Env, monkeypatch, capsys
) -> None:
    """R10: интервью смотрят и после конца конвейера, без --subject."""
    cust = _waiting("WS-1-a", "WS-1", "customer")
    cust.status = "brief_ready"
    rs.save(cust)
    eng = _run("WS-1-eng-b", "WS-1-eng", "engineer", "done")
    _accepted(eng)
    monkeypatch.setattr(dfl, "load_session", lambda sid: _session_view())
    monkeypatch.setattr(
        dfl, "_discovery_status", lambda sid, cwd: iv.DiscoveryReply(0, {}, "")
    )
    assert env.run("interview") == 0
    out = capsys.readouterr().out
    assert "вопросов больше нет" in out and "FR-01 раз в неделю" in out


def test_dfl_interview_finished_pipelines_picks_latest_with_session(
    env: Env, monkeypatch
) -> None:
    for tag in ("a", "c"):
        _waiting(f"WS-{tag}-x", f"WS-{tag}", "customer")
        _accepted(_run(f"WS-{tag}-eng-y", f"WS-{tag}-eng", "engineer", "done"))
    seen: list[str] = []
    monkeypatch.setattr(
        dfl, "load_session", lambda sid: seen.append(sid) or _session_view()
    )
    monkeypatch.setattr(
        dfl, "_discovery_status", lambda sid, cwd: iv.DiscoveryReply(0, {}, "")
    )
    assert env.run("interview") == 0
    assert seen == ["sess-WS-c-x"]


def test_dfl_interview_finished_without_any_session_refuses(env: Env, capsys) -> None:
    _run("WS-1-a", "WS-1", "customer", "brief_ready")
    _accepted(_run("WS-1-eng-b", "WS-1-eng", "engineer", "done"))
    assert env.run("interview") == 2
    assert "интервью не идёт" in capsys.readouterr().err


def test_dfl_correct_refused_after_interview_completed(
    env: Env, monkeypatch, capsys
) -> None:
    """I3: после completed_at бриф уже собран — поправка в него не попадёт."""
    state = _waiting("WS-1-a", "WS-1", "customer")
    assert state.interview is not None
    state.interview["completed_at"] = "2026-11-12T10:00:00+00:00"
    state.status = "brief_ready"
    rs.save(state)
    monkeypatch.setattr(dfl, "load_session", lambda sid: _session_view())
    monkeypatch.setattr(dfl.st, "claude_agent", lambda model: pytest.fail("агент"))
    monkeypatch.setattr(dfl, "_discovery_answer", lambda *a, **k: pytest.fail("вызов"))
    assert env.run("correct", "раз в месяц") == 2
    err = capsys.readouterr().err
    assert "интервью завершено, бриф уже собран" in err
    assert "поправка в бриф не попадёт" in err


def test_dfl_interview_works_after_interview_completed(env: Env, monkeypatch) -> None:
    state = _waiting("WS-1-a", "WS-1", "customer")
    assert state.interview is not None
    state.interview["completed_at"] = "2026-11-12T10:00:00+00:00"
    rs.save(state)
    monkeypatch.setattr(dfl, "load_session", lambda sid: _session_view())
    monkeypatch.setattr(
        dfl, "_discovery_status", lambda sid, cwd: iv.DiscoveryReply(0, {}, "")
    )
    assert env.run("interview") == 0


# --- финальное ревью: I5 (добор тем: замена ответа и остановленный прогон) ---


def test_chat_wires_supersede_resend(env: Env, monkeypatch) -> None:
    state = _waiting("WS-1-a", "WS-1", "customer")
    seen: dict[str, object] = {}
    monkeypatch.setattr(dfl.ic, "chat", lambda st, **kw: seen.update(kw) or "x")
    monkeypatch.setattr(dfl.st, "claude_agent", lambda model: lambda p: "")
    dfl._chat(state)
    disc = Discovery()
    monkeypatch.setattr(dfl, "_discovery_answer", disc)
    seen["send"]("q-1", "text: a\n")  # type: ignore[operator]
    seen["resend"]("q-2", "text: b\n")  # type: ignore[operator]
    assert disc.flags == [("q-1", False), ("q-2", True)]


def _stopped(reason: str) -> rs.RunState:
    state = _waiting("WS-1-a", "WS-1", "customer")
    assert state.interview is not None
    state.status = "stopped_interview"
    state.interview["stop_reason"] = reason
    rs.save(state)
    return state


def test_dfl_run_tty_incomplete_stop_gets_one_top_up(
    env: Env, monkeypatch, capsys
) -> None:
    """Интервью кончилось неполным (стоп 11): dfl run в терминале — один
    заход в разговор (добор), затем шаг; снова неполное — без петли."""
    _tty(monkeypatch)
    _stopped("status вернул 11: findings в /r/interview-findings.json")
    seen: list[str] = []

    def chat(state: rs.RunState) -> object:
        seen.append(state.run_id)
        return dfl.ic.ChatResult.FINISHED

    monkeypatch.setattr(dfl, "_chat", chat)
    monkeypatch.setattr(
        spec_loop, "main", lambda argv: env.spec_calls.append(argv) or 0
    )
    assert env.run("run") == 0
    assert seen == ["WS-1-a"] and len(env.spec_calls) == 2


def test_dfl_run_tty_other_interview_stop_not_chatted(env: Env, monkeypatch) -> None:
    _tty(monkeypatch)
    _stopped("session_state_unknown: boom")
    monkeypatch.setattr(dfl, "_chat", lambda state: pytest.fail("цикл"))
    monkeypatch.setattr(
        spec_loop, "main", lambda argv: env.spec_calls.append(argv) or 0
    )
    assert env.run("run") == 0
    assert len(env.spec_calls) == 1


# --- уровни подробности, лог вызова и перехват (план dfl-clean-output, Task 3) ---


def _bind_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    """capfd подменяет sys.stdout файлом мимо fd 1; привязываем потоки к fd."""
    out = open(1, "w", closefd=False, encoding="utf-8")
    err = open(2, "w", closefd=False, encoding="utf-8")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)


def _read(capfd) -> tuple[str, str]:
    """Сбросить привязанные к fd потоки и прочитать захват."""
    sys.stdout.flush()
    sys.stderr.flush()
    return capfd.readouterr()


def _logs(env: Env) -> list[runlog.LogInfo]:
    return runlog.list_logs(env.tmp / "target")


def _recording_level(env: Env, monkeypatch) -> list[int]:
    seen: list[int] = []

    def spec_main(argv: list[str]) -> int:
        env.spec_calls.append(list(argv))
        seen.append(verbosity())
        return 0

    monkeypatch.setattr(spec_loop, "main", spec_main)
    return seen


def test_level0_hides_runner_output_and_logs_it(env: Env, monkeypatch, capfd) -> None:
    _bind_streams(monkeypatch)
    assert env.run("run", "--subject", "t") == 0
    out, err = _read(capfd)
    assert "runner-line" not in out + err
    assert "режим test: ослаблено" in out
    assert df_config.TEST_BANNER_HEADER not in out
    [info] = _logs(env)
    assert (info.command, info.code) == ("run", 0)
    text = runlog.read_log(info.path)
    assert "runner-line" in text
    assert text.startswith("# dfl run --subject t уровень 0\n")


def test_vvv_shows_runner_output_and_full_banner(env: Env, monkeypatch, capfd) -> None:
    _bind_streams(monkeypatch)
    assert env.run("run", "-vvv", "--subject", "t") == 0
    out, _ = _read(capfd)
    assert "runner-line" in out and df_config.TEST_BANNER_HEADER in out
    [info] = _logs(env)
    text = runlog.read_log(info.path)
    assert "уровень 3: вывод раннера шёл на экран" in text
    assert "уровень 3: вывод раннера" not in out


def test_flag_before_and_after_subcommand_add_up(env: Env, monkeypatch) -> None:
    seen = _recording_level(env, monkeypatch)
    assert env.run("-v", "run", "-v", "--subject", "t") == 0
    assert seen == [2]


def test_flags_above_three_cap_at_three(env: Env, monkeypatch) -> None:
    seen = _recording_level(env, monkeypatch)
    assert env.run("-vv", "run", "-vvv", "--subject", "t") == 0
    assert seen == [3]


def test_env_verbosity_default_and_flag_wins(env: Env, monkeypatch) -> None:
    seen = _recording_level(env, monkeypatch)
    monkeypatch.setenv(VERBOSITY_ENV, "2")
    assert env.run("run", "--subject", "t") == 0
    assert env.run("run", "-v", "--subject", "t") == 0
    assert seen == [2, 1]


def test_env_verbosity_garbage_warns(env: Env, monkeypatch, capfd) -> None:
    _bind_streams(monkeypatch)
    seen = _recording_level(env, monkeypatch)
    monkeypatch.setenv(VERBOSITY_ENV, "много")
    assert env.run("run", "--subject", "t") == 0
    _, err = _read(capfd)
    warned = [line for line in err.splitlines() if VERBOSITY_ENV in line]
    assert len(warned) == 1 and "использую уровень 0" in warned[0]
    assert seen == [0]


def test_level_resets_between_calls(env: Env, monkeypatch) -> None:
    seen = _recording_level(env, monkeypatch)
    assert env.run("run", "-vv", "--subject", "t") == 0
    assert env.run("run", "--subject", "t") == 0
    assert seen == [2, 0]


def test_status_writes_no_log(env: Env, monkeypatch) -> None:
    _status_target(env, monkeypatch)
    assert env.run("status") == 0
    assert _logs(env) == []


def test_report_writes_no_log(env: Env, monkeypatch) -> None:
    monkeypatch.setattr(dfl, "run_report", lambda cwd, *, print_only: 0)
    assert env.run("report") == 0
    assert _logs(env) == []


def test_refusal_before_test_mode_writes_no_log(env: Env, capsys) -> None:
    write_config(env.tmp / "xdg", mode="strict", **_STRICT_GH_DIRS)
    assert env.run("run", "--subject", "s") == 2
    err = capsys.readouterr().err
    assert _logs(env) == []
    assert "подробно: dfl log" not in err
    assert err.rstrip().endswith(dfl.REPORT_HINT)  # без лога — прежняя подсказка


def test_log_dir_unwritable_runs_without_log(env: Env, monkeypatch, capfd) -> None:
    _bind_streams(monkeypatch)
    blocker = env.tmp / "state-file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("XDG_STATE_HOME", str(blocker))
    assert env.run("run", "--subject", "t") == 0
    out, err = _read(capfd)
    assert "лог не пишется:" in err
    assert "runner-line" in out


def test_failure_record_has_log_path_and_level(env: Env, monkeypatch, capsys) -> None:
    monkeypatch.setattr(spec_loop, "main", lambda argv: 3)
    assert env.run("run", "--subject", "t") == 3
    rec = fl.load(env.tmp / "target")
    assert rec is not None and rec.verbosity == 0
    assert Path(rec.log_path).is_file()
    assert [i.path for i in _logs(env)] == [Path(rec.log_path)]
    err = capsys.readouterr().err
    assert "подробно: dfl log" in err and dfl.REPORT_HINT not in err
    assert "подробно: dfl log" in runlog.read_log(Path(rec.log_path))
    assert env.run("run", "-v", "--subject", "t") == 3
    err = capsys.readouterr().err
    assert "подробно: dfl log" in err and err.rstrip().endswith(dfl.REPORT_HINT)
    rec = fl.load(env.tmp / "target")
    assert rec is not None and rec.verbosity == 1


def test_rotation_keeps_log_of_last_failure(env: Env, monkeypatch) -> None:
    monkeypatch.setattr(spec_loop, "main", lambda argv: 3)
    assert env.run("run", "--subject", "t") == 3
    rec = fl.load(env.tmp / "target")
    assert rec is not None
    monkeypatch.setattr(spec_loop, "main", lambda argv: 0)
    for _ in range(runlog.KEEP + 2):
        assert env.run("run", "--subject", "t") == 0
    assert Path(rec.log_path).is_file()


def test_discovery_output_goes_to_log_only(env: Env, monkeypatch, capfd) -> None:
    _bind_streams(monkeypatch)
    _waiting("WS-1-a", "WS-1", "customer")
    _answer_file(env)

    def fake_run(argv: list[str], **kw: object):
        return dfl.subprocess.CompletedProcess(argv, 1, "disc-out", "disc-err")

    monkeypatch.setattr(dfl.df_config, "workspace_dir", lambda name: env.tmp)
    monkeypatch.setattr(dfl.subprocess, "run", fake_run)
    assert env.run("answer", "--file", "answer.yaml") == 2  # stdout не JSON
    out, err = _read(capfd)
    assert "disc-out" not in out + err and "disc-err" not in out + err
    [info] = _logs(env)
    text = runlog.read_log(info.path)
    assert "disc-out" in text and "disc-err" in text


# --- итог шага, вопрос на экране, уровни (план dfl-clean-output, Task 4) ---


def _question_reply(text: str = "Кто ваш клиент?") -> iv.DiscoveryReply:
    action = {"session_id": "s", "question_id": "q-1", "question_text": text}
    return iv.DiscoveryReply(20, {"next_action": action}, "")


def _step_leaves(
    monkeypatch, env: Env, status: str, *, code: int = 0, ops: dict | None = None
) -> None:
    """Шаг раннера оставляет WS-1-a в `status` (и с `ops`), код `code`."""

    def spec_main(argv: list[str]) -> int:
        env.spec_calls.append(list(argv))
        state = _waiting("WS-1-a", "WS-1", "customer")
        state.status = status
        state.ops.update(ops or {})
        rs.save(state)
        return code

    monkeypatch.setattr(spec_loop, "main", spec_main)


def test_waiting_without_tty_shows_question_text(env: Env, monkeypatch, capfd) -> None:
    _bind_streams(monkeypatch)
    _step_leaves(monkeypatch, env, dfl.WAITING)
    seen: list[tuple[str, Path]] = []

    def status(session_id: str, cwd: Path) -> iv.DiscoveryReply:
        seen.append((session_id, cwd))
        return _question_reply()

    monkeypatch.setattr(dfl, "_discovery_status", status)
    assert env.run("run", "--subject", "t") == 0
    out, _ = _read(capfd)
    assert "Кто ваш клиент?" in out and dfl.ANSWER_HINT in out
    assert "вопрос выше" not in out and "text (ответ своими словами)" not in out
    assert seen == [("sess-WS-1-a", rs.run_dir("WS-1-a"))]
    assert env.run("run", "-v") == 0
    out, _ = _read(capfd)
    assert "text (ответ своими словами)" in out  # формат файла — уровень 1


@pytest.mark.parametrize("broken", ["code", "raise"])
def test_waiting_question_unreadable_points_to_interview(
    env: Env, monkeypatch, capfd, broken: str
) -> None:
    _bind_streams(monkeypatch)
    _step_leaves(monkeypatch, env, dfl.WAITING)

    def status(session_id: str, cwd: Path) -> iv.DiscoveryReply:
        if broken == "raise":
            raise OSError("uv не найден")
        return iv.DiscoveryReply(1, {"operation": {"reason": "x"}}, "")

    monkeypatch.setattr(dfl, "_discovery_status", status)
    assert env.run("run", "--subject", "t") == 0
    out, _ = _read(capfd)
    assert "прогон WS-1-a ждёт ответа; вопрос: dfl interview" in out


def test_stopped_run_prints_cause_and_log_hint(env: Env, monkeypatch, capfd) -> None:
    _bind_streams(monkeypatch)

    def spec_main(argv: list[str]) -> int:
        _run("WS-1-a", "WS-1", "customer", "stopped_need")
        (rs.run_dir("WS-1-a") / "stop-reason.txt").write_text(
            "нет прав на репо\nподробности", encoding="utf-8"
        )
        return 0

    monkeypatch.setattr(spec_loop, "main", spec_main)
    assert env.run("run", "--subject", "t") == 0
    out, err = _read(capfd)
    assert "прогон WS-1-a остановлен: нет прав на репо\n" in out
    assert (out + err).count(dfl.LOG_HINT) == 1


def test_stopped_run_nonzero_code_one_log_hint(env: Env, monkeypatch, capfd) -> None:
    _bind_streams(monkeypatch)

    def spec_main(argv: list[str]) -> int:
        _run("WS-1-a", "WS-1", "customer", "stopped_need")
        (rs.run_dir("WS-1-a") / "stop-reason.txt").write_text("нет прав", "utf-8")
        return 3

    monkeypatch.setattr(spec_loop, "main", spec_main)
    assert env.run("run", "--subject", "t") == 3
    out, err = _read(capfd)
    assert "прогон WS-1-a остановлен: нет прав" in out
    assert (out + err).count(dfl.LOG_HINT) == 1


def test_nonzero_code_without_run_names_code(env: Env, monkeypatch, capfd) -> None:
    _bind_streams(monkeypatch)
    monkeypatch.setattr(spec_loop, "main", lambda argv: 3)
    assert env.run("run", "--subject", "t") == 3
    out, err = _read(capfd)
    assert "шаг завершился с кодом 3" in out
    assert (out + err).count(dfl.LOG_HINT) == 1


def test_zero_code_without_run_prints_no_outcome(env: Env, monkeypatch, capfd) -> None:
    _bind_streams(monkeypatch)
    assert env.run("run", "--subject", "t") == 0
    out, err = _read(capfd)
    assert "шаг завершился" not in out and "прогон" not in out
    assert dfl.LOG_HINT not in out + err


def test_running_run_outcome_names_status(env: Env, monkeypatch, capfd) -> None:
    _bind_streams(monkeypatch)
    _step_leaves(monkeypatch, env, "brief_ready")
    assert env.run("run", "--subject", "t") == 0
    out, _ = _read(capfd)
    assert "прогон WS-1-a: brief_ready" in out


def test_level2_adds_state_lines(env: Env, monkeypatch, capfd) -> None:
    _bind_streams(monkeypatch)
    ops = {"need": {"status": "completed"}}
    _step_leaves(monkeypatch, env, "running", ops=ops)
    assert env.run("run", "-v", "--subject", "t") == 0
    out, _ = _read(capfd)
    assert "шаги:" not in out and "WS-1-a · customer" not in out
    assert env.run("run", "-vv") == 0
    out, _ = _read(capfd)
    assert "WS-1-a · customer · running" in out
    assert "шаги: —" in out  # второй шаг ничего в ops не поменял
    _step_leaves(monkeypatch, env, "running", ops={"brief": {"status": "completed"}})
    assert env.run("run", "-vv") == 0
    out, _ = _read(capfd)
    assert "шаги: brief" in out


def test_level2_state_line_names_pr_and_wave(env: Env) -> None:
    state = _run("WS-1-eng-b", "WS-1-eng", "engineer", "running")
    state.ops = {"a": {"status": "completed"}, "b": {"status": "running"}}
    state.pr, state.wave = 7, 2
    lines = dfl._state_lines({"a": "completed"}, state)
    assert lines == ["WS-1-eng-b · engineer · running · PR #7 · волна 2", "шаги: b"]


def test_brief_waiting_merge_visible_at_level0(env: Env, monkeypatch, capfd) -> None:
    _bind_streams(monkeypatch)
    _run("WS-1-a", "WS-1", "customer", "brief_ready")
    env.forge.branches["brief/WS-1"] = [5]
    env.forge.states[5] = "OPEN"
    assert env.run("run") == 0
    out, _ = _read(capfd)
    assert "бриф ждёт вашего мержа: https://github.com/o/alpha/pull/5" in out


def test_chatting_waiting_prints_no_answer_hint(env: Env, monkeypatch, capfd) -> None:
    _bind_streams(monkeypatch)
    _tty(monkeypatch)
    _waiting_after_first_step(monkeypatch, env)
    monkeypatch.setattr(dfl, "_chat", Chat(results=["stopped"]))
    monkeypatch.setattr(
        dfl, "_discovery_status", lambda s, c: pytest.fail("вопрос — у цикла")
    )
    assert env.run("run", "--subject", "тема") == 0
    out, err = _read(capfd)
    assert dfl.ANSWER_HINT not in out + err
    assert dfl.CHAT_STOPPED in out


def test_status_v_lists_relaxations_and_config(env: Env, monkeypatch, capfd) -> None:
    _bind_streams(monkeypatch)
    _status_target(env, monkeypatch)
    assert env.run("status") == 0
    out, _ = _read(capfd)
    assert df_config.TEST_BANNER_HEADER not in out and "конфиг:" not in out
    assert env.run("status", "-v") == 0
    out, _ = _read(capfd)
    assert df_config.TEST_BANNER_HEADER in out
    assert f"конфиг: {df_config.canonical_config_path()}" in out


def test_status_vv_lists_ops(env: Env, monkeypatch, capfd) -> None:
    _bind_streams(monkeypatch)
    _status_target(env, monkeypatch)
    state = _run("WS-1-a", "WS-1", "customer", "running")
    state.ops["need"] = {"status": "completed"}
    rs.save(state)
    assert env.run("status", "-v") == 0
    out, _ = _read(capfd)
    assert "  need: completed" not in out
    assert env.run("status", "-vv") == 0
    out, _ = _read(capfd)
    assert "WS-1-a  customer  running\n  need: completed\n" in out


# --- правки ревью Task 4 ---


def test_unchanged_resumed_run_old_cause_not_reported(
    env: Env, monkeypatch, capfd
) -> None:
    """Раннер упал до записи run.json: старая причина стопа — не итог шага."""
    _bind_streams(monkeypatch)
    state = _run("WS-1-a", "WS-1", "customer", "stopped_need")
    state.target_dir = str((env.tmp / "target").resolve())  # fallback сработает
    rs.save(state)
    (rs.run_dir("WS-1-a") / "stop-reason.txt").write_text(
        "старая причина", encoding="utf-8"
    )
    monkeypatch.setattr(spec_loop, "main", lambda argv: 3)
    assert env.run("run") == 3
    out, err = _read(capfd)
    assert "старая причина" not in out + err
    assert "шаг завершился с кодом 3" in out
    assert (out + err).count(dfl.LOG_HINT) == 1
    monkeypatch.setattr(spec_loop, "main", lambda argv: 0)
    assert env.run("run") == 0
    out, err = _read(capfd)
    assert "старая причина" not in out + err and "шаг завершился" not in out


@pytest.mark.parametrize("code", [0, 3])
def test_unreadable_question_points_to_log_once(
    env: Env, monkeypatch, capfd, code: int
) -> None:
    _bind_streams(monkeypatch)
    _step_leaves(monkeypatch, env, dfl.WAITING, code=code)

    def status(session_id: str, cwd: Path) -> iv.DiscoveryReply:
        raise OSError("uv не найден")

    monkeypatch.setattr(dfl, "_discovery_status", status)
    assert env.run("run", "--subject", "t") == code
    out, err = _read(capfd)
    assert "вопрос: dfl interview" in out
    assert (out + err).count(dfl.LOG_HINT) == 1


def test_readable_question_no_log_hint(env: Env, monkeypatch, capfd) -> None:
    _bind_streams(monkeypatch)
    _step_leaves(monkeypatch, env, dfl.WAITING)
    monkeypatch.setattr(dfl, "_discovery_status", lambda s, c: _question_reply())
    assert env.run("run", "--subject", "t") == 0
    out, err = _read(capfd)
    assert dfl.LOG_HINT not in out + err


def test_stopped_without_cause_nonzero_names_code(env: Env, monkeypatch, capfd) -> None:
    _bind_streams(monkeypatch)
    _step_leaves(monkeypatch, env, "stopped_need", code=3)
    assert env.run("run", "--subject", "t") == 3
    out, _ = _read(capfd)
    assert "шаг завершился с кодом 3" in out
    assert "причина не записана" not in out


@pytest.mark.parametrize(
    ("failing", "merged"), [("propose", "OPEN"), ("approve", "MERGED")]
)
def test_brief_tool_failure_names_code(
    env: Env, monkeypatch, capfd, failing: str, merged: str
) -> None:
    _bind_streams(monkeypatch)
    _run("WS-1-a", "WS-1", "customer", "brief_ready")
    env.forge.branches["brief/WS-1"] = [5]
    env.forge.states[5] = merged
    monkeypatch.setattr(bt, "main", lambda argv: 2 if argv[0] == failing else 0)
    assert env.run("run") == 2
    out, err = _read(capfd)
    assert f"бриф: {failing} завершился с кодом 2" in out
    assert (out + err).count(dfl.LOG_HINT) == 1


def _make_log(env: Env, command: str, body: str, code: int | None) -> Path:
    log = runlog.open_log(
        env.tmp / "target", command, f"dfl {command} уровень 0", protect=None
    )
    log.fh.write(body)
    if code is None:
        log.fh.close()
    else:
        runlog.close_log(log, code)
    return log.path


def test_log_prints_last_and_report_hint_on_failure(env: Env, capfd) -> None:
    _make_log(env, "run", "старый\n", 0)
    _make_log(env, "answer", "новый\n", 1)
    assert env.run("log") == 0
    out, _ = _read(capfd)
    assert "новый" in out and "старый" not in out
    assert out.rstrip("\n").splitlines()[-1] == dfl.REPORT_HINT
    assert "режим test" not in out


def test_log_success_has_no_report_hint(env: Env, capfd) -> None:
    _make_log(env, "run", "ок\n", 0)
    assert env.run("log") == 0
    out, _ = _read(capfd)
    assert dfl.REPORT_HINT not in out


def test_log_writes_no_log_and_needs_no_registration(env: Env) -> None:
    _make_log(env, "run", "x\n", 0)
    before = len(_logs(env))
    assert env.run("log") == 0
    assert len(_logs(env)) == before
    assert env.registered == 0


def test_log_n_and_list(env: Env, capfd) -> None:
    _make_log(env, "run", "первый\n", 0)
    _make_log(env, "answer", "второй\n", 3)
    _make_log(env, "abandon", "третий\n", None)
    assert env.run("log", "-n", "2") == 0
    out, _ = _read(capfd)
    assert "второй" in out and "третий" not in out
    assert env.run("log", "-n", "1") == 0
    out, _ = _read(capfd)
    assert "третий" in out
    assert env.run("log", "--list") == 0
    lines = _read(capfd)[0].splitlines()
    assert len(lines) == 3
    pattern = r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d "
    assert re.match(pattern + r"abandon код —$", lines[0])
    assert re.match(pattern + r"answer код 3$", lines[1])
    assert re.match(pattern + r"run код 0$", lines[2])


def test_log_n_too_large_refused(env: Env, capfd) -> None:
    _make_log(env, "run", "x\n", 0)
    assert env.run("log", "-n", "2") == 2
    _, err = _read(capfd)
    assert "dfl log: отказ (код 2)" in err
    assert "что сделать:" in err


def test_log_n_zero_refused(env: Env, capfd) -> None:
    _make_log(env, "run", "x\n", 0)
    assert env.run("log", "-n", "0") == 2
    assert "dfl log: отказ (код 2)" in _read(capfd)[1]


def test_log_empty(env: Env, capfd) -> None:
    assert env.run("log") == 0
    assert "логов нет" in _read(capfd)[0]
    assert env.run("log", "--list") == 0
    assert "логов нет" in _read(capfd)[0]


def test_log_non_utf8_bytes(env: Env, capfd) -> None:
    path = _make_log(env, "run", "до\n", 0)
    with path.open("ab") as fh:
        fh.write(b"\xff\xfe\n")
    assert env.run("log") == 0
    assert "до" in _read(capfd)[0]


def test_log_n_does_not_clash_with_verbose(env: Env, capfd) -> None:
    _make_log(env, "run", "x\n", 0)
    assert env.run("log", "-v", "-n", "1") == 0


# --- правки финального ревью ---


def _step_creates_engineer(monkeypatch, env: Env, *, url: str | None) -> None:
    """Шаг раннера завершает engineer-прогон: с проверенным PR или без него."""

    def spec_main(argv: list[str]) -> int:
        env.spec_calls.append(list(argv))
        state = _run("WS-1-eng-b", "WS-1-eng", "engineer", "completed")
        if url is not None:
            _accepted(state, url)
        return 0

    monkeypatch.setattr(spec_loop, "main", spec_main)


def test_accepted_code_pr_merge_visible_at_level0(env: Env, monkeypatch, capfd) -> None:
    _bind_streams(monkeypatch)
    url = "https://github.com/o/alpha/pull/9"
    _step_creates_engineer(monkeypatch, env, url=url)
    assert env.run("run", "--subject", "t") == 0
    out, _ = _read(capfd)
    assert f"дальше: смержите {url} — конвейер закончен" in out


def test_chain_stopped_hint_visible_at_level0_points_to_log(
    env: Env, monkeypatch, capfd
) -> None:
    _bind_streams(monkeypatch)
    _step_creates_engineer(monkeypatch, env, url=None)
    assert env.run("run", "--subject", "t") == 0
    out, _ = _read(capfd)
    assert f"дальше: {dfl.CHAIN_STOPPED_HINT}" in out
    assert "в выводе dfl run" not in out
    assert "dfl log" in dfl.CHAIN_STOPPED_HINT


def test_brief_ready_next_step_visible_at_level0(env: Env, monkeypatch, capfd) -> None:
    _bind_streams(monkeypatch)
    _step_leaves(monkeypatch, env, "brief_ready")
    assert env.run("run", "--subject", "t") == 0
    out, _ = _read(capfd)
    assert f"дальше: {BRIEF_NEXT}" in out
    assert "мерж брифа, затем" not in out


def test_optional_hint_stays_level1(env: Env, monkeypatch, capfd) -> None:
    _bind_streams(monkeypatch)

    def spec_main(argv: list[str]) -> int:
        _run("WS-1-a", "WS-1", "customer", "stopped_need")
        return 0

    monkeypatch.setattr(spec_loop, "main", spec_main)
    assert env.run("run", "--subject", "t") == 0
    out, _ = _read(capfd)
    assert "дальше:" not in out
    assert env.run("run", "-v") == 0
    out, _ = _read(capfd)
    assert "дальше: разберите причину" in out


def test_unchanged_run_waiting_merge_reports_status_and_action(
    env: Env, monkeypatch, capfd
) -> None:
    """resume на `waiting_human_merge` не пишет run.json — итог всё равно есть."""
    _bind_streams(monkeypatch)
    state = _run("WS-1-a", "WS-1", "customer", "waiting_human_merge")
    state.pr = 7
    rs.save(state)
    monkeypatch.setattr(spec_loop, "main", lambda argv: 0)
    assert env.run("run") == 0
    out, _ = _read(capfd)
    assert "прогон WS-1-a шагом не изменён: waiting_human_merge" in out
    assert "дальше: смержите https://github.com/o/alpha/pull/7" in out


def test_unchanged_orphan_interview_reports_question_pointer(
    env: Env, monkeypatch, capfd
) -> None:
    _bind_streams(monkeypatch)
    _run("WS-1-a", "WS-1", "customer", dfl.WAITING)  # сессии нет — сирота
    monkeypatch.setattr(spec_loop, "main", lambda argv: 0)
    assert env.run("run") == 0
    out, err = _read(capfd)
    assert "прогон WS-1-a шагом не изменён: waiting_interview" in out
    assert "прогон WS-1-a ждёт ответа; вопрос: dfl interview" in out
    assert (out + err).count(dfl.LOG_HINT) == 1


def test_waiting_nonzero_code_names_code(env: Env, monkeypatch, capfd) -> None:
    _bind_streams(monkeypatch)
    _step_leaves(monkeypatch, env, dfl.WAITING, code=3)
    monkeypatch.setattr(dfl, "_discovery_status", lambda s, c: _question_reply())
    assert env.run("run", "--subject", "t") == 3
    out, _ = _read(capfd)
    assert "шаг завершился с кодом 3" in out


def test_correct_output_reaches_log(env: Env, monkeypatch, capfd) -> None:
    _bind_streams(monkeypatch)
    _waiting("WS-1-a", "WS-1", "customer")
    monkeypatch.setattr(dfl, "load_session", lambda sid: _session_view())
    _claude_agent_returning(monkeypatch, '{"items": [], "ask": "Что поправить?"}')
    assert env.run("correct", "поменяй") == 0
    assert "Уточните: Что поправить?" in _read(capfd)[0]
    [info] = _logs(env)
    assert "Уточните: Что поправить?" in runlog.read_log(info.path)


def test_cli_with_real_tee_level0_hides_runner_logs_it(
    env: Env, monkeypatch, capfd
) -> None:
    """Боевой путь: `cli` оборачивает потоки настоящим `_Tee`."""
    _bind_streams(monkeypatch)
    assert dfl.cli(["--cwd", str(env.tmp / "target"), "run", "--subject", "t"]) == 0
    out, err = _read(capfd)
    assert "runner-line" not in out + err
    [info] = _logs(env)
    assert "runner-line" in runlog.read_log(info.path)


def test_log_list_shows_local_time(monkeypatch) -> None:
    """`dfl log --list`: метка имени (UTC) показывается местным временем."""
    import time

    monkeypatch.setenv("TZ", "Etc/GMT-3")  # UTC+3
    time.tzset()
    try:
        info = runlog.LogInfo(Path("20261113T090000000000Z-run.log"), "run", 0)
        assert dfl._list_line(info) == "2026-11-13 12:00:00 run код 0"
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()
