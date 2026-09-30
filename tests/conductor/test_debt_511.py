"""Долг среза 0 (devtools#511): находки терминального ревью #509 и #513."""

import subprocess
from pathlib import Path

from conductor.collect import HUMAN_MERGE_RE, _roadmap
from conductor.render import render_status
from conductor.snapshot import evaluate, owner_questions
from tests.conductor.fixtures import ROADMAP, inputs, record

GOAL = "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 "
R3 = ROADMAP.replace("autonomy = 0", "autonomy = 3")
DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "conductor"


def test_local_roadmap_file_caps_the_level_at_zero() -> None:
    # §2.1/§2.4: полномочия даёт только роадмап с origin зонтика
    todos = {"a": GOAL + "\n"}
    assert evaluate(inputs(todos, roadmap=R3), 3).run_level == 3
    local = inputs(todos, roadmap=R3, roadmap_source="file:/tmp/draft.toml")
    assert evaluate(local, 3).run_level == 0


def test_missing_roadmap_file_is_a_source_state_not_a_crash(tmp_path: Path) -> None:
    text, sha, state, source = _roadmap(tmp_path, tmp_path / "nope.toml")
    assert (text, sha, state) == (None, None, "error")
    assert source.startswith("file:")


def test_closed_issue_without_reason_is_unknown_not_done() -> None:
    todos = {"a": GOAL + "@blocked_by:b#5\n"}
    closed = record("b", 5, state="closed", state_reason=None)
    result = evaluate(inputs(todos, [closed]), 0)
    wait = next(w for w in result.waits if w.consumer == "todo://a/goal")
    assert (wait.verdict, wait.reason) == ("unknown", "closed_unknown")
    assert [q["reason"] for q in owner_questions(result)] == ["closed_unknown"]


def test_weak_open_pr_is_not_a_queue_position() -> None:
    # инвариант §3.1: слабые данные не подтверждают готовность к мержу
    weak = record(
        "b", 12, is_pr=True, weak=True, approved_at_head=True, ci="green", files=["x"]
    )
    result = evaluate(inputs({}, [weak]), 0)
    assert "b!12" not in {e.node_id for e in result.queue}
    assert "b!12" not in render_status(result)


def test_header_from_unknown_repo_is_not_a_request_in_flight() -> None:
    todos = {"a": GOAL + "@blocked_by:todo://b/need\n"}
    stray = record("b", 7, body="slug: need\nfrom: acme\n")
    result = evaluate(inputs(todos, [stray]), 0)
    wait = next(w for w in result.waits if w.prereq == "todo://b/need")
    assert wait.reason == "missing"


def test_human_merge_line_is_the_governance_ssot() -> None:
    from governance.policy_sources import _HUMAN_LINE

    assert HUMAN_MERGE_RE is _HUMAN_LINE
    assert HUMAN_MERGE_RE.search("- Мерж: человек (все PR, кроме вендор-волн)\n")


def test_busy_lock_is_skipped_not_failed() -> None:
    unit = (DEPLOY / "conductor.service").read_text(encoding="utf-8")
    assert "flock -n -E 0 /srv/conductor/state/conductor.lock" in unit


def test_gh_version_check_refuses_non_numeric_versions(tmp_path: Path) -> None:
    setup = (DEPLOY / "setup.sh").read_text(encoding="utf-8")
    check = setup.split("# >>> gh-version-check")[1].split("# <<< gh-version-check")[0]
    for version, code in (("abc", 1), ("", 1), ("2.48.0-rc1", 1), ("2.48.0", 0)):
        fake = tmp_path / "gh"
        fake.write_text(f"#!/bin/sh\necho 'gh version {version} (x)'\n")
        fake.chmod(0o755)
        done = subprocess.run(
            ["bash", "-c", check],
            env={"PATH": f"{tmp_path}:/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == code, (version, done.stdout)
