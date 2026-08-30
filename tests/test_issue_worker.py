import json
from pathlib import Path

import issue_worker


def test_policy_decision_is_deterministic() -> None:
    assert issue_worker.policy_decision(True) == "accept"
    assert issue_worker.policy_decision(False) == "reject"


def test_enforce_policy_blocks_llm_flip() -> None:
    flipped = {"decision": "accept", "kind": "fix", "summary": "s",
               "todo": "t", "next_step": "n", "changed_files": []}
    fixed = issue_worker.enforce_policy(dict(flipped), "reject")
    assert fixed["decision"] == "reject"


def test_enforce_policy_allows_needs_human() -> None:
    result = {"decision": "needs_human", "kind": "fix", "summary": "s",
              "todo": "t", "next_step": "n", "changed_files": []}
    assert issue_worker.enforce_policy(dict(result), "accept")["decision"] == (
        "needs_human")


def test_result_path_layout(tmp_path: Path) -> None:
    path = issue_worker.result_path(tmp_path, "alpha", 7)
    assert path == tmp_path / "issues" / "alpha" / "7" / "result.json"


def test_external_execute_degrades_to_read_only() -> None:
    assert issue_worker.effective_execute(mode="execute", internal=False) is False
    assert issue_worker.effective_execute(mode="execute", internal=True) is True
    assert issue_worker.effective_execute(mode="plan", internal=True) is False


def test_schema_keeps_decision_enum() -> None:
    assert issue_worker.SCHEMA["properties"]["decision"]["enum"] == [
        "accept", "reject", "needs_human"]
