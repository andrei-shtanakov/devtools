"""Состояние сессии интервью (lite/interview_state.py, lite/interview_probe.py)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from governance import df_config
from lite import interview_state as st

DISCOVERY = Path(__file__).resolve().parents[2] / "discovery"
LIVE_DISCOVERY = (DISCOVERY / ".venv").is_dir()


def _raw(**over: object) -> dict:
    base = {
        "frame": "customer",
        "required": {"goals": "G", "functions": "FR"},
        "optional": {"risks": "RK"},
        "entries": [{"id": "FR-02", "body": "b", "fields": {"Priority": "Must"}}],
        "declared": ["FR-01", "FR-02"],
        "holders": {"FR-02": "customer.functions.01"},
        "answers": {"customer.functions.01": {"text": "t"}},
        "answered_keys": ["functions"],
        "readiness": "incomplete",
        "findings": ["required topic 'goals' is not covered"],
    }
    return {**base, **over}


def _view(**over: object) -> st.SessionView:
    return st._parse(_raw(**over))


@pytest.fixture
def fake_runner(monkeypatch):
    """Подменённый запуск зонда: записывает argv, отдаёт заданный результат."""
    monkeypatch.setattr(df_config, "workspace_dir", lambda name: Path("/ws") / name)

    def make(stdout: str = "", stderr: str = "", code: int = 0):
        calls: list[list[str]] = []

        def run(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, code, stdout, stderr)

        run.calls = calls  # type: ignore[attr-defined]
        return run

    return make


def test_load_session_parses_probe_json(fake_runner) -> None:
    run = fake_runner(json.dumps(_raw()))
    view = st.load_session("s-1", run)
    assert view.frame == "customer"
    assert view.entries == [st.Entry("FR-02", "b", {"Priority": "Must"})]
    assert view.holders == {"FR-02": "customer.functions.01"}
    assert view.readiness == "incomplete"
    argv = run.calls[0]
    assert argv[:5] == ["uv", "run", "--frozen", "--project", "/ws/discovery"]
    assert argv[5:8] == ["python", "-I", str(st.PROBE)]
    assert argv[-1] == "s-1"


def test_next_id_after_all_declared() -> None:
    view = _view()  # FR-01 заменён, действует FR-02 → следующий FR-03
    assert view.next_id("FR") == "FR-03"
    assert _view(declared=["FR-01", "FR-03"]).next_id("FR") == "FR-04"
    assert view.next_id("G") == "G-01"


def test_covered_by_prefix_and_answered_keys() -> None:
    view = _view(
        required={"goals": "G", "functions": "FR", "feasibility_review": None},
        answered_keys=["feasibility_review"],
    )
    assert view.covered() == {"functions", "feasibility_review"}
    assert view.prefixes() == {"G": "goals", "FR": "functions", "RK": "risks"}
    assert _view(answered_keys=["feasibility_review"]).covered() == {"functions"}


def test_probe_failure_raises_with_reason(fake_runner) -> None:
    with pytest.raises(st.InterviewStateError, match="SessionUnreadable: нет"):
        st.load_session("s-1", fake_runner(stderr="SessionUnreadable: нет", code=1))
    with pytest.raises(st.InterviewStateError, match="не то"):
        st.load_session("s-1", fake_runner(stdout="[]"))


def test_missing_discovery_dir_raises(monkeypatch) -> None:
    def boom(name: str):
        raise df_config.ConfigError("workspace.root=/x: нет каталога discovery")

    monkeypatch.setattr(df_config, "workspace_dir", boom)
    with pytest.raises(st.InterviewStateError, match="нет каталога discovery"):
        st.load_session("s-1")


def _discovery(env: dict[str, str], *args: str) -> dict:
    argv = ["uv", "run", "--frozen", "--project", str(DISCOVERY), "discovery", *args]
    done = subprocess.run(argv, env=env, capture_output=True, text=True, check=False)
    assert done.returncode not in (1, 2), done.stderr  # 20 = ждёт ввода
    return json.loads(done.stdout)


@pytest.mark.skipif(not LIVE_DISCOVERY, reason="нет .venv discovery")
def test_probe_reads_real_session(tmp_path: Path, monkeypatch) -> None:
    env = {**os.environ, "DISCOVERY_HOME": str(tmp_path / "home")}
    monkeypatch.setenv("DISCOVERY_HOME", env["DISCOVERY_HOME"])
    monkeypatch.setattr(df_config, "workspace_dir", lambda name: DISCOVERY)
    _discovery(env, "start", "--frame", "customer", "--target", "t",
               "--session-id", "s-live")  # fmt: skip
    first = tmp_path / "a1.yaml"
    first.write_text(
        "text: цели\nentries:\n  - id: G-01\n    body: цель\n"
        "  - id: G-02\n    body: вторая\n",
        encoding="utf-8",
    )
    answer = ["answer", "--session", "s-live", "--role", "owner"]
    _discovery(env, *answer, "--file", str(first))
    second = tmp_path / "a2.yaml"  # тот же вопрос: G-02 пропал, G-01 переобъявлен
    second.write_text(
        "text: цели v2\nentries:\n  - id: G-01\n    body: цель v2\n",
        encoding="utf-8",
    )
    _discovery(env, *answer, "--question", "customer.goals.01", "--supersede",
               "--file", str(second))  # fmt: skip

    view = st.load_session("s-live")
    assert view.frame == "customer"
    assert [e.id for e in view.entries] == ["G-01"]
    assert view.entries[0].body == "цель v2"
    assert view.declared == ["G-01", "G-02"]
    assert view.holders == {"G-01": "customer.goals.01"}
    assert view.answers["customer.goals.01"]["text"] == "цели v2"
    assert "goals" in view.answered_keys
    assert view.covered() == {"goals"}
    assert view.next_id("G") == "G-03"
    assert view.readiness == "incomplete"
    assert view.findings
