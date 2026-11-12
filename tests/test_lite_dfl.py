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
    assert "WS-1-eng-b" in out and "https://github.com/o/alpha/pull/9" in out
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
