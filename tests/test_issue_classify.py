import json
import subprocess
from pathlib import Path

import issue_classify
import issue_console
from tests.test_issue_console import _issue


def _unknown(number: int = 1) -> object:
    issue = _issue(number=number)
    assert issue.kind == "unknown"
    return issue


def _answer(number: int = 1, kind: str = "fix", confidence: float = 0.9) -> dict:
    return {"repo": "alpha", "number": number, "kind": kind,
            "confidence": confidence}


def test_new_issue_calls_ai_and_caches(tmp_path: Path) -> None:
    cache = tmp_path / "cache.json"
    calls: list[list[dict]] = []

    def fake_run(batch: list[dict]) -> list[dict]:
        calls.append(batch)
        return [_answer()]

    kinds = issue_classify.refine([_unknown()], cache, run=fake_run)
    assert kinds == {"alpha#1": "fix"}
    assert len(calls) == 1
    saved = json.loads(cache.read_text())
    assert saved["owner/alpha#1@2026-08-30T11:00:00Z"] == "fix"


def test_unchanged_updated_at_is_cache_hit(tmp_path: Path) -> None:
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"owner/alpha#1@2026-08-30T11:00:00Z": "fix"}))

    def fail_run(batch: list[dict]) -> list[dict]:
        raise AssertionError("AI не должен вызываться при cache hit")

    assert issue_classify.refine([_unknown()], cache, run=fail_run) == {
        "alpha#1": "fix"}


def test_changed_updated_at_reclassifies(tmp_path: Path) -> None:
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"owner/alpha#1@2026-08-01T00:00:00Z": "code"}))
    kinds = issue_classify.refine([_unknown()], cache, run=lambda b: [_answer()])
    assert kinds == {"alpha#1": "fix"}


def test_low_confidence_stays_unknown(tmp_path: Path) -> None:
    kinds = issue_classify.refine(
        [_unknown()], tmp_path / "c.json",
        run=lambda b: [_answer(confidence=0.5)])
    assert kinds == {}


def test_broken_cache_and_dead_codex_keep_console_alive(tmp_path: Path) -> None:
    cache = tmp_path / "cache.json"
    cache.write_text("{битый json")

    def dead_run(batch: list[dict]) -> list[dict]:
        raise issue_classify.ClassifyError("codex недоступен")

    assert issue_classify.refine([_unknown()], cache, run=dead_run) == {}


def test_cache_write_is_atomic_no_partial_file(tmp_path: Path) -> None:
    cache = tmp_path / "sub" / "cache.json"
    issue_classify.refine([_unknown()], cache, run=lambda b: [_answer()])
    assert json.loads(cache.read_text())  # валидный json, каталог создан
    assert not list(cache.parent.glob("*.tmp*"))


def test_run_codex_raises_classify_error_on_launch_failure(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("codex")

    monkeypatch.setattr(subprocess, "run", fake_run)
    try:
        issue_classify.run_codex([])
        raise AssertionError("ожидался ClassifyError")
    except issue_classify.ClassifyError:
        pass


def test_refine_survives_codex_launch_failure(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "cache.json"

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("codex")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert issue_classify.refine(
        [_unknown()], cache, run=issue_classify.run_codex) == {}


def test_kinds_match_issue_console() -> None:
    assert issue_classify.KINDS == issue_console.KINDS


def test_confidence_threshold_is_inclusive(tmp_path: Path) -> None:
    kinds = issue_classify.refine(
        [_unknown()], tmp_path / "c.json",
        run=lambda b: [_answer(confidence=0.75)])
    assert kinds == {"alpha#1": "fix"}
