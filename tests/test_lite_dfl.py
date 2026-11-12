"""`dfl run` / `dfl status` (lite/dfl.py): один шаг вперёд по журналу прогонов.

Конвейер подменён рекордерами: `spec_loop.main`, `brief_tools.main`, факты
форджи brief-PR и `user_targets.register`; прогоны — в `tmp_path`.
"""

from __future__ import annotations

import json
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
from lite import failure_log as fl
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
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
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
    assert "WS-1-a" in out and "WS-3-a" in out and "мерж брифа, затем dfl run" in out
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
    assert env.run("answer", "--file", "answer.yaml") == 0
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
    def spec_main(argv: list[str]) -> int:
        _waiting("WS-1-a", "WS-1", "customer")
        return 0

    monkeypatch.setattr(spec_loop, "main", spec_main)
    assert env.run("run", "--subject", "тема") == 0
    out = capsys.readouterr().out
    assert "прогон WS-1-a ждёт ответа" in out
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
    assert env.run("run") == 2  # первый запуск без --subject
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
    def refuse() -> None:
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
    assert env.run("run", "--subject", "тема") == 0
    out = capsys.readouterr().out
    assert "прогон WS-1-a ждёт ответа" in out and "dfl answer --file" in out
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
    assert "dfl answer --file" not in capsys.readouterr().out


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
