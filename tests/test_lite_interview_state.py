"""Состояние сессии интервью (lite/interview_state.py, `discovery show`)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from governance import df_config
from lite import interview_state as st

DISCOVERY = Path(__file__).resolve().parents[2] / "discovery"
FIXTURES = Path(__file__).parent / "fixtures" / "discovery_show"
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
        "asked": {"customer.functions.01": "functions"},
    }
    return {**base, **over}


def _envelope(raw: dict) -> dict:
    """Конверт `discovery show` из полей `_raw` (форма реального вывода)."""
    topics = {**raw["required"], **raw["optional"]}
    return {
        "readiness": raw["readiness"],
        "readiness_findings": raw["findings"],
        "session": {
            "frame": raw["frame"],
            "topics": {"required": raw["required"], "optional": raw["optional"]},
            "coverage": {
                k: "covered" if k in raw["answered_keys"] else "missing" for k in topics
            },
            "entries": raw["entries"],
            "declared": raw["declared"],
            "holders": raw["holders"],
            "asked": [
                {"question_id": q, "coverage_key": k, "question_text": "?"}
                for q, k in raw["asked"].items()
            ],
            "answers": [
                {"question_id": q, "payload": p} for q, p in raw["answers"].items()
            ],
        },
    }


def _view(**over: object) -> st.SessionView:
    return st._parse(_envelope(_raw(**over)))


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


def test_load_session_parses_show_json(fake_runner) -> None:
    run = fake_runner(json.dumps(_envelope(_raw())), code=20)
    view = st.load_session("s-1", run)
    assert view.frame == "customer"
    assert view.entries == [st.Entry("FR-02", "b", {"Priority": "Must"})]
    assert view.holders == {"FR-02": "customer.functions.01"}
    assert view.readiness == "incomplete"
    assert view.asked == {"customer.functions.01": "functions"}
    argv = run.calls[0]
    assert argv[:5] == ["uv", "run", "--frozen", "--project", "/ws/discovery"]
    assert argv[5:] == ["discovery", "show", "--session", "s-1"]


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


def test_show_failure_raises_with_reason(fake_runner) -> None:
    with pytest.raises(st.InterviewStateError, match="SessionUnreadable: нет"):
        st.load_session("s-1", fake_runner(stderr="SessionUnreadable: нет", code=1))
    with pytest.raises(st.InterviewStateError, match="код 2"):
        st.load_session("s-1", fake_runner(stdout="{}", code=2))
    with pytest.raises(st.InterviewStateError, match="не то"):
        st.load_session("s-1", fake_runner(stdout="[]"))


def test_missing_discovery_dir_raises(monkeypatch) -> None:
    def boom(name: str):
        raise df_config.ConfigError("workspace.root=/x: нет каталога discovery")

    monkeypatch.setattr(df_config, "workspace_dir", boom)
    with pytest.raises(st.InterviewStateError, match="нет каталога discovery"):
        st.load_session("s-1")


def test_show_failure_reason_from_stdout_envelope(fake_runner) -> None:
    envelope = {"operation": {"status": "error", "reason": "нет сессии s-1"}}
    run = fake_runner(stdout=json.dumps(envelope), code=1)
    with pytest.raises(st.InterviewStateError, match="нет сессии s-1"):
        st.load_session("s-1", run)


def test_parse_show_output_of_old_pin() -> None:
    """Сессия, созданная discovery c7961b9, читается `show` пина 91543b1."""
    raw = (FIXTURES / "old_pin_c7961b9.json").read_text(encoding="utf-8")
    view = st._parse(json.loads(raw))
    assert view.frame == "customer"
    assert [e.id for e in view.entries] == ["G-01", "G-02"]
    assert view.covered() == {"goals"}


def test_parse_real_show_output() -> None:
    """Фикстура — реальный вывод `discovery show` (pin 91543b1): сессия
    customer, G-01 переобъявлен вторым ответом, G-02 вытеснен."""
    raw = (FIXTURES / "customer_two_goals.json").read_text(encoding="utf-8")
    view = st._parse(json.loads(raw))
    assert view.frame == "customer"
    assert view.required["goals"] == "G"
    assert view.optional == {"risks": "RK"}
    assert view.entries == [st.Entry("G-01", "цель v2", {})]
    assert view.declared == ["G-01", "G-02"]
    assert view.holders == {"G-01": "customer.goals.01"}
    assert list(view.answers) == ["customer.goals.01"]
    assert view.answers["customer.goals.01"]["text"] == "цели v2"
    assert view.asked == {
        "customer.goals.01": "goals",
        "customer.goals.02": "goals",
    }
    assert view.answered_keys == ["goals"]
    assert view.covered() == {"goals"}
    assert view.readiness == "incomplete"
    assert "required topic 'personas' is not covered" in view.findings
    assert view.next_id("G") == "G-03"


def _discovery(env: dict[str, str], *args: str) -> dict:
    argv = ["uv", "run", "--frozen", "--project", str(DISCOVERY), "discovery", *args]
    done = subprocess.run(argv, env=env, capture_output=True, text=True, check=False)
    assert done.returncode not in (1, 2), done.stderr  # 20 = ждёт ввода
    return json.loads(done.stdout)


@pytest.mark.skipif(not LIVE_DISCOVERY, reason=f"нет .venv discovery: {DISCOVERY}")
def test_show_reads_real_session(tmp_path: Path, monkeypatch) -> None:
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
    assert view.asked["customer.goals.01"] == "goals"
    assert "goals" in view.answered_keys
    assert view.covered() == {"goals"}
    assert view.next_id("G") == "G-03"
    assert view.readiness == "incomplete"
    assert view.findings


def test_held_answer_keeps_only_entries_it_holds() -> None:
    view = _view(
        entries=[
            {"id": "FR-01", "body": "a", "fields": {}},
            {"id": "FR-02", "body": "b2", "fields": {}},
        ],
        holders={"FR-01": "q1", "FR-02": "q2"},
        answers={
            "q1": {"text": "t", "entries": [{"id": "FR-01", "body": "a"},
                                            {"id": "FR-02", "body": "b"}]},
            "q2": {"text": "u", "entries": [{"id": "FR-02", "body": "b2", "X": 1}]},
        },
    )  # fmt: skip
    assert view.held_answer("q1") == {
        "text": "t",
        "entries": [{"id": "FR-01", "body": "a"}],
    }
    assert view.entry_record("FR-02") == {"id": "FR-02", "body": "b2", "X": 1}
