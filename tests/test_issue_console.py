from pathlib import Path
from types import SimpleNamespace

import issue_console


def test_classify_is_conservative() -> None:
    assert issue_console.classify("Fix parser regression", "", ()) == "fix"
    assert issue_console.classify("Explore model choices", "research", ()) == "research"
    assert issue_console.classify("Fix docs typo", "", ()) == "unknown"
    assert issue_console.classify("Unclear request", "", ()) == "unknown"


def test_parse_groups_internal_and_inbox(tmp_path: Path) -> None:
    repo = tmp_path / "alpha"
    (repo / ".git").mkdir(parents=True)
    (repo / "TODO.md").write_text("- [ ] do thing slug-one\n")
    raw = [{
        "repository": {"name": "alpha"}, "number": 7, "title": "Research queue",
        "body": "slug: slug-one", "author": {"login": "owner"},
        "createdAt": "2026-08-30T10:00:00Z", "url": "https://example/7",
        "labels": [{"name": "inbox"}, {"name": "research"}],
    }]
    issue = issue_console.parse_issues(raw, tmp_path, {"owner"})[0]
    assert issue.internal and issue.inbox and issue.accepted
    assert issue.kind == "research"


def test_fetch_uses_gh_search_issues_compatible_flags(monkeypatch) -> None:
    captured = []

    def fake_run(cmd, **kwargs):
        captured.extend(cmd)
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(issue_console.subprocess, "run", fake_run)
    assert issue_console.fetch_issues("owner") == []
    assert captured[:3] == ["gh", "search", "issues"]
    assert "--include-prs" not in captured
    assert "--type" not in captured
