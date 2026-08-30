from pathlib import Path
from types import SimpleNamespace

import issue_console


def _raw(
    repo: str = "alpha",
    body: str = "slug: slug-one",
    labels: tuple[str, ...] = ("inbox",),
    author: str = "owner",
) -> dict:
    return {
        "repository": {"name": repo, "nameWithOwner": f"owner/{repo}"},
        "number": 7,
        "title": "Research queue",
        "body": body,
        "author": {"login": author},
        "createdAt": "2026-08-30T10:00:00Z",
        "updatedAt": "2026-08-30T11:00:00Z",
        "url": "https://example/7",
        "labels": [{"name": x} for x in labels],
    }


def _fleet(tmp_path: Path, todo: str | None = "- [ ] do thing slug-one\n") -> Path:
    repo = tmp_path / "alpha"
    (repo / ".git").mkdir(parents=True)
    if todo is not None:
        (repo / "TODO.md").write_text(todo)
    return tmp_path


def test_classify_is_conservative() -> None:
    assert issue_console.classify("Fix parser regression", "", ()) == "fix"
    assert issue_console.classify("Explore model choices", "research", ()) == "research"
    assert issue_console.classify("Fix docs typo", "", ()) == "unknown"
    assert issue_console.classify("Unclear request", "", ()) == "unknown"


def test_classify_covers_each_kind() -> None:
    assert issue_console.classify("Update README documentation", "", ()) == "document"
    assert issue_console.classify("Implement feature support", "", ()) == "code"


def test_acceptance_accepted(tmp_path: Path) -> None:
    root = _fleet(tmp_path)
    issue = issue_console.parse_issues([_raw()], root, {"owner"})[0]
    assert issue.accepted == "accepted"
    assert issue.internal and issue.inbox


def test_acceptance_not_accepted(tmp_path: Path) -> None:
    root = _fleet(tmp_path, todo="- [ ] unrelated item\n")
    issue = issue_console.parse_issues([_raw()], root, {"owner"})[0]
    assert issue.accepted == "not-accepted"


def test_acceptance_unverifiable_without_slug(tmp_path: Path) -> None:
    root = _fleet(tmp_path)
    issue = issue_console.parse_issues(
        [_raw(body="просто текст")], root, {"owner"}
    )[0]
    assert issue.accepted == "unverifiable"


def test_acceptance_unverifiable_without_todo(tmp_path: Path) -> None:
    root = _fleet(tmp_path, todo=None)
    issue = issue_console.parse_issues([_raw()], root, {"owner"})[0]
    assert issue.accepted == "unverifiable"


def test_acceptance_na_for_non_inbox(tmp_path: Path) -> None:
    root = _fleet(tmp_path)
    issue = issue_console.parse_issues(
        [_raw(labels=("research",))], root, {"owner"}
    )[0]
    assert issue.accepted == "n/a"


def test_acceptance_ignores_slug_in_prose(tmp_path: Path) -> None:
    root = _fleet(
        tmp_path, todo="в прозе упомянут slug-one, но пункта нет\n"
    )
    issue = issue_console.parse_issues([_raw()], root, {"owner"})[0]
    assert issue.accepted == "not-accepted"


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
