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


def test_gh_view_uses_full_repo_slug(monkeypatch, tmp_path: Path) -> None:
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(list(cmd))

        class R:
            returncode = 1
            stdout = ""
            stderr = "boom"

        return R()

    monkeypatch.setattr(issue_worker.subprocess, "run", fake_run)
    monkeypatch.setattr(
        "sys.argv",
        ["issue_worker.py", "--repo", "alpha", "--owner", "owner",
         "--number", "7", "--author", "a", "--kind", "fix",
         "--internal", "yes", "--output-root", str(tmp_path)],
    )
    assert issue_worker.main() == 2
    gh_cmd = captured[0]
    assert gh_cmd[:4] == ["gh", "issue", "view", "7"]
    assert "--repo" in gh_cmd
    assert gh_cmd[gh_cmd.index("--repo") + 1] == "owner/alpha"


def test_missing_codex_binary_is_clean_exit(monkeypatch, tmp_path: Path) -> None:
    calls = {"n": 0}

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        if cmd[0] == "gh":
            class R:
                returncode = 0
                stdout = "{}"
                stderr = ""

            return R()
        raise FileNotFoundError("codex")

    monkeypatch.setattr(issue_worker.subprocess, "run", fake_run)
    monkeypatch.setattr(
        "sys.argv",
        ["issue_worker.py", "--repo", "alpha", "--owner", "owner",
         "--number", "7", "--author", "a", "--kind", "fix",
         "--internal", "yes", "--output-root", str(tmp_path)],
    )
    assert issue_worker.main() == 3
    assert calls["n"] == 2
    assert not (tmp_path / "issues" / "alpha" / "7" / "result.json").exists()
