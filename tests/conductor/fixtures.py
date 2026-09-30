"""Общие фикстуры флота для тестов conductor."""

from __future__ import annotations

from typing import Any

from conductor.inputs import Inputs, RepoTodo

REPOS = (
    "a",
    "b",
    "c",
    "devtools",
    "spec-runner",
    "arbiter",
    "deployer",
    "ecosystem-kb",
)
GITHUB_NAME = {"ecosystem-kb": "prograph-vault"}
EPICS = {
    "eco.focus1": {"status": "active"},
    "eco.focus2": {"status": "active"},
    "eco.parked": {"status": "active"},
    "eco.bg": {"status": "active"},
    "eco.paused": {"status": "paused"},
}
ROADMAP = """
schema_version = 1
updated = "2026-09-29"
autonomy = 0
writer_host = "vps"
writer_since = "2026-09-29T00:00:00Z"
[[focus]]
epic = "eco.focus1"
goal = "todo://a/goal"
[[focus]]
epic = "eco.focus2"
[parked]
epics = ["eco.parked"]
"""
MANIFEST_TEXT = "".join(
    f'[cores.{r}]\nrepo_url = "git@github.com:own/{GITHUB_NAME.get(r, r)}.git"\n'
    f'git_dir = "{GITHUB_NAME.get(r, r)}"\n'
    for r in REPOS
)


def record(repo: str, number: int, **fields: Any) -> dict[str, Any]:
    """GhRecord с разумными умолчаниями (PR — review/ci неизвестны)."""
    base: dict[str, Any] = {
        "repo": repo,
        "number": number,
        "is_pr": False,
        "title": f"{repo}#{number}",
        "body": "",
        "state": "open",
        "state_reason": None,
        "merged": False,
        "author": "own",
        "labels": [],
        "updated_at": "2026-09-28T00:00:00Z",
        "url": "",
        "comments": [],
        "closing_refs": [],
    }
    if fields.get("is_pr"):
        base.update(
            head_sha="h",
            review_decision="REVIEW_REQUIRED",
            ci="unknown",
            approved_at_head=False,
            files=[],
            complete=True,
        )
    base.update(fields)
    return base


def inputs(
    todos: dict[str, str],
    records: Any = (),
    roadmap: str | None = ROADMAP,
    epics: dict | None = None,
    movement: dict[str, str] | None = None,
    gh_state: str = "read",
    **extra: Any,
) -> Inputs:
    """Inputs из текстов TODO (прочие репо — absent)."""
    base = Inputs(
        captured_at="2026-09-29T12:00:00Z",
        host="test",
        owner="own",
        manifest_text=MANIFEST_TEXT,
        todos=[
            RepoTodo(r, todos.get(r), "sha-" + r, "read" if r in todos else "absent")
            for r in REPOS
        ],
        gh_records=list(records),
        gh_state=gh_state,  # type: ignore[arg-type]
        gh_detail="",
        roadmap_text=roadmap,
        roadmap_state="read" if roadmap is not None else "absent",
        roadmap_source="origin",
        roadmap_sha="sha-rm",
        epics=EPICS if epics is None else epics,
        epics_state="read",
        epics_detail="",
        repo_names={GITHUB_NAME.get(r, r): r for r in REPOS},
        movement=movement or {},
    )
    for key, value in extra.items():
        setattr(base, key, value)
    return base
