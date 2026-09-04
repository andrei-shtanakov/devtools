#!/usr/bin/env python3
"""todo-context — everything the fleet already knows about ONE `TODO.md` item.

A plan item is one line. It carries an `@id`, an `@epic`, sometimes a `@defect`
and a `@blocked_by`, and nothing else: `plan_fields` reads items line by line and
never sees a continuation, so an item has no body by construction (ADR-ECO-010 D3).
Handing that line to an agent and asking it to do the work is asking it to invent
the requirement.

The body is not missing, though — it is scattered across sources that already
exist and that nobody assembles: the epic's own `goal` in `epics.toml`, the design
doc whose path the section heading usually names, the neighbours in the
`@blocked_by` graph, the repo's `CLAUDE.md` scope fence, and — for an item that
arrived through the inbox (ADR-ECO-006) — the body of the originating issue. This
tool collects exactly those and reports what it found.

**The honesty rule this exists to keep.** Every source reports one of
`read | absent | not_queried | error`, and a source that was never consulted is
NEVER reported as absent. That is the same distinction `epics.py` keeps between
`unavailable` and `missing`, for the same reason: "we did not look" and "there is
nothing there" produce identical-looking empty output, and an executor that cannot
tell them apart will confidently work from a context it never had. The grade at
the bottom is computed from those states, not from whether the text came out
non-empty.

Nothing here re-implements plan semantics. Identity, parsing, the cross-repo graph
and its diagnostics are `plan_fields`' (`checkout_map` / `parse_fleet` /
`check_fleet`); this module only locates one node in that snapshot and reads
sources around it.

Usage (needs the pinned env, like `check-plan-fields.py` — `uv run --frozen`):

    python todo_context.py --repo dispatcher --id waits-graph-view
    python todo_context.py --uri todo://dispatcher/waits-graph-view --json
    python todo_context.py --repo maestro --id foo --issues   # asks gh
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:  # the pinned shared parser; absent only when run outside `uv run --frozen`
    import plan_fields as _pf
except ImportError:  # pragma: no cover - guarded at call time, not import time
    _pf = None  # type: ignore[assignment]

#: A source's state. `not_queried` is the one this tool would be useless without:
#: `--issues` is off by default (it costs a network round trip), and reporting the
#: originating issue as `absent` in that case would be a lie the grade then acts on.
SOURCE_STATES = ("read", "absent", "not_queried", "error")

#: Doc paths written inside a section heading or an item line. The fleet's own
#: habit, discovered rather than invented: headings like
#: "Waits graph (спека docs/superpowers/specs/2026-08-26-waits-graph-design.md)"
#: already name the design doc, which makes them the cheapest link that exists.
_DOC_PATH_RE = re.compile(
    r"(?:^|[\s(\[`])((?:docs|specs|plans)/[\w./-]+\.(?:md|yaml|yml))"
)

#: The start of any checklist item, at any indent — the boundary a continuation
#: block ends at. Same shape as the package's own item regex; it is used here only
#: to find where one item's lines STOP, never to decide what an item means.
_ITEM_START_RE = re.compile(r"^(\s*)[-*]\s*\[[ xX]\]\s")
_HEADING_START_RE = re.compile(r"^#{1,6}\s")
#: A continuation block this long (non-blank chars) is treated as a written
#: requirement. Two short lines of aside are not a spec; a paragraph is.
_BODY_SUBSTANTIAL = 120

_RULES_FILES = ("CLAUDE.md", "AGENTS.md")
_RULES_CAP = 8000  # bytes of a rules file inlined before it is marked truncated
_GREP_CAP = 40  # hits kept from one `git grep`
_GIT_TIMEOUT = 20


@dataclass(frozen=True)
class Source:
    """One context source and whether it was actually read."""

    source: str
    state: str
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.state not in SOURCE_STATES:
            raise ValueError(f"unknown source state: {self.state}")


class ContextError(Exception):
    """The item cannot be located; nothing partial is worth printing."""


# ─────────────────────────── fleet discovery ───────────────────────────


def find_root(explicit: str | None) -> Path:
    """The workspace directory holding the repos (parent of devtools)."""
    if explicit:
        return Path(explicit).resolve()
    return Path(__file__).resolve().parent.parent


def default_manifest(root: Path) -> Path:
    """The umbrella's frozen fleet manifest — SSOT of which repos exist."""
    return root / "ai-orchestrators-workspace" / "workspace-manifest.toml"


def default_registry(root: Path) -> Path:
    """The umbrella's epic registry — SSOT of epic VALUES, read live, never vendored."""
    return root / "ai-orchestrators-workspace" / "epics.toml"


def build_inputs(root: Path, index: Any) -> tuple[list[Any], dict[str, Path]]:
    """One `RepoInput` per manifest repo, frozen from disk.

    Deliberately a thin loop over the package's `checkout_map`: locating and
    naming checkouts is identity work and belongs there, so this and
    `check-plan-fields.py` cannot drift on what a repo is called. The loop is
    duplicated rather than imported because that script's name has a dash and is
    not importable; the *semantics* are not duplicated.
    """
    on_disk = _pf.checkout_map(root, index)
    inputs: list[Any] = []
    checkouts: dict[str, Path] = {}
    for repo in sorted(set(index.canonical_keys) | set(on_disk)):
        directory = on_disk.get(repo)
        if directory is None:
            inputs.append(_pf.RepoInput(repo, available=False))
            continue
        checkouts[repo] = directory
        todo = directory / "TODO.md"
        text = todo.read_text(encoding="utf-8", errors="ignore") \
            if todo.is_file() else None
        inputs.append(_pf.RepoInput(repo, todo_text=text, available=True))
    return inputs, checkouts


def fleet_snapshot(root: Path,
                   manifest_path: Path) -> tuple[dict[str, Any], dict[str, Path]]:
    """The canonical cross-repo plan snapshot plus where each repo lives."""
    if _pf is None:  # pragma: no cover - environment guard
        raise ContextError(
            "plan_fields is not importable — run through the pinned env:\n"
            "  uv run --frozen python todo_context.py ..."
        )
    if not manifest_path.is_file():
        raise ContextError(f"no workspace manifest at {manifest_path}")
    index = _pf.manifest_index(manifest_path)
    inputs, checkouts = build_inputs(root, index)
    return _pf.parse_fleet(inputs, index), checkouts


# ─────────────────────────── the sources ───────────────────────────


def read_item(snapshot: dict[str, Any], node_id: str,
              checkouts: dict[str, Path]) -> dict[str, Any]:
    """The node itself, plus its verbatim source line and section heading.

    `epic`/`defect` are read from the canonical fields when the pinned parser
    publishes them and fall back to the raw tag map when it does not, so this
    works across a pin bump instead of silently reporting an untagged item.
    """
    node = next((n for n in snapshot["nodes"] if n["node_id"] == node_id), None)
    if node is None:
        raise ContextError(f"no item {node_id} in the fleet snapshot")
    repo = node["repo"]
    line_no = node.get("provenance", {}).get("line")
    section = source_line = None
    directory = checkouts.get(repo)
    if directory is not None and (directory / "TODO.md").is_file():
        text = (directory / "TODO.md").read_text(encoding="utf-8", errors="ignore")
        for item in _pf.scrape_items(text):
            if item.item_id == node["id"]:
                section, source_line = item.section, item.raw_text
                break
    raw = node.get("raw", {}) or {}
    return {
        "node_id": node_id,
        "repo": repo,
        "id": node["id"],
        "title": node["title"],
        "status": node["declared_status"],
        "epic": node.get("epic") or raw.get("epic"),
        "defect": node.get("defect") or raw.get("defect"),
        "owner": (node.get("owner_ref") or {}).get("raw"),
        "trigger": node.get("trigger") or raw.get("trigger"),
        "section": section,
        "path": node.get("provenance", {}).get("path"),
        "line": line_no,
        "source_line": source_line,
        "tags": raw,
    }


def read_epic(registry_path: Path,
              epic_id: str | None) -> tuple[dict[str, Any] | None, Source]:
    """The epic's own `goal`/`notes` — the closest thing to a written intent.

    `load_registry` is used when the pinned parser has it (it validates the file
    against `epics/v1`); otherwise the VALUES are read directly with `tomllib`,
    which the contract explicitly allows — `epics.toml` is read live by path and is
    never vendored — and the degradation is recorded rather than hidden.
    """
    if epic_id is None:
        return None, Source("epic", "absent", "item carries no @epic")
    if not registry_path.is_file():
        return None, Source("epic", "error", f"no registry at {registry_path}")
    validated = True
    try:
        registry = _pf.load_registry(registry_path)  # type: ignore[union-attr]
        epics, programs = registry.epics, registry.programs
    except AttributeError:
        validated = False
        try:
            data = tomllib.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            return None, Source("epic", "error", f"registry unreadable: {exc}")
        epics, programs = data.get("epics", {}), data.get("programs", {})
    except (OSError, ValueError) as exc:
        return None, Source("epic", "error", f"registry unreadable: {exc}")

    entry = epics.get(epic_id)
    if entry is None:
        return None, Source("epic", "error",
                            f"{epic_id} is not in the registry (EP-UNKNOWN)")
    program_id = epic_id.split(".", 1)[0]
    program = programs.get(program_id, {})
    block = {
        "id": epic_id,
        "title": entry.get("title"),
        "status": entry.get("status"),
        "goal": entry.get("goal"),
        "opened": entry.get("opened"),
        "notes": entry.get("notes"),
        "moved_to": entry.get("moved_to"),
        "program": {
            "id": program_id,
            "title": program.get("title"),
            "kind": program.get("kind"),
            "notes": program.get("notes"),
        },
    }
    detail = None if validated else (
        "read without schema validation (pinned parser has no load_registry)")
    return block, Source("epic", "read", detail)


def read_graph(snapshot: dict[str, Any], node_id: str) -> tuple[dict[str, Any], Source]:
    """What this item waits on, what waits on it, and what is wrong with that.

    Edge semantics, staleness and the diagnostics all come from the package
    (`parse_fleet` + `check_fleet`); nothing here re-decides them.
    """
    by_id = {n["node_id"]: n for n in snapshot["nodes"]}
    findings = list(snapshot["diagnostics"]) + _pf.check_fleet(snapshot)

    def describe(other_id: str) -> dict[str, Any]:
        other = by_id.get(other_id)
        return {
            "node_id": other_id,
            "title": other["title"] if other else None,
            "status": other["declared_status"] if other else "unknown",
            "repo": other["repo"] if other else None,
        }

    blocked_by = [describe(e["target_node_id"]) for e in snapshot["edges"]
                  if e["source_node_id"] == node_id and e["kind"] == "blocked_by"]
    blocks = [describe(e["source_node_id"]) for e in snapshot["edges"]
              if e["target_node_id"] == node_id and e["kind"] == "blocked_by"]
    unresolved = [r.get("raw_ref") for r in snapshot.get("references", [])
                  if r.get("source_node_id") == node_id
                  and not r.get("resolved_target")]
    mine = [d for d in findings
            if node_id in (d.get("subject_uri"), d.get("related_uri"))]
    block = {
        "blocked_by": blocked_by,
        "blocks": blocks,
        "unresolved_refs": [u for u in unresolved if u],
        "diagnostics": [{"code": d["code"], "severity": d["severity"],
                         "message": d["message"]} for d in mine],
    }
    return block, Source("graph", "read")


def named_doc_paths(item: dict[str, Any]) -> list[str]:
    """Doc paths written into the item line or its section heading."""
    haystack = " ".join(x for x in (item.get("source_line"), item.get("section")) if x)
    seen: list[str] = []
    for match in _DOC_PATH_RE.finditer(haystack):
        path = match.group(1)
        if path not in seen:
            seen.append(path)
    return seen


def git_grep(directory: Path,
             needle: str) -> tuple[list[dict[str, Any]] | None, str | None]:
    """`git grep` for a literal string, excluding TODO.md. `None` = could not run.

    `git grep` rather than a walk: it honours `.gitignore`, so a vendored
    `.venv` cannot flood the pack, and it is the repo's own idea of its files.
    """
    cmd = ["git", "-C", str(directory), "grep", "-nI", "--fixed-strings", "--",
           needle, "--", ".", ":(exclude)TODO.md"]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=_GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"git grep failed: {exc}"
    if done.returncode not in (0, 1):  # 1 = no match, a legitimate answer
        return None, (done.stderr.strip().splitlines() or ["git grep failed"])[-1]
    hits: list[dict[str, Any]] = []
    for raw in done.stdout.splitlines()[:_GREP_CAP]:
        path, _, rest = raw.partition(":")
        line_no, _, text = rest.partition(":")
        hits.append({"path": path, "line": line_no, "text": text.strip()[:300]})
    return hits, None


def read_docs(directory: Path | None,
              item: dict[str, Any]) -> tuple[dict[str, Any], Source]:
    """Design docs for this item: paths it names, plus every mention of its `@id`."""
    named = named_doc_paths(item)
    if directory is None:
        return ({"named": named, "mentions": []},
                Source("docs", "error", "repo is not checked out here"))
    resolved = []
    for rel in named:
        path = directory / rel
        resolved.append({"path": rel, "exists": path.is_file(),
                         "bytes": path.stat().st_size if path.is_file() else None})
    hits, error = git_grep(directory, item["id"])
    if error is not None:
        return ({"named": resolved, "mentions": []}, Source("docs", "error", error))
    block = {"named": resolved, "mentions": hits}
    have = any(d["exists"] for d in resolved) or bool(hits)
    return block, Source("docs", "read" if have else "absent",
                         None if have else f"no file names @id:{item['id']}")


def read_body(directory: Path | None,
              item: dict[str, Any]) -> tuple[dict[str, Any], Source]:
    """The item's indented continuation lines — the body the parser cannot see.

    `plan_fields` reads items line by line and stops at the first line: that is a
    deliberate contract decision (a tag on a continuation is not a tag), and it is
    right for the operational plane. But the fleet's authors have been writing real
    prose under items anyway — `ai-orchestrators-workspace/TODO.md` carries whole
    paragraphs of rationale indented under single items — and that prose is the
    closest thing to a requirement an item ever has.

    Reading it here changes no plan semantics: this is a devtools-side read of the
    same file, purely syntactic (indented lines until the next item or heading),
    and nothing about it flows back into the graph or the tags.
    """
    if directory is None:
        return ({"text": None, "lines": 0},
                Source("body", "error", "repo is not checked out here"))
    todo = directory / "TODO.md"
    if not todo.is_file():
        return ({"text": None, "lines": 0},
                Source("body", "error", "repo keeps no TODO.md"))
    lines = todo.read_text(encoding="utf-8", errors="ignore").splitlines()
    start = (item.get("line") or 0) - 1
    if not 0 <= start < len(lines):
        return ({"text": None, "lines": 0},
                Source("body", "error", "item line is out of range"))
    match = _ITEM_START_RE.match(lines[start])
    indent = len(match.group(1)) if match else 0
    collected: list[str] = []
    for raw in lines[start + 1:]:
        if not raw.strip():
            collected.append("")
            continue
        if _HEADING_START_RE.match(raw) or _ITEM_START_RE.match(raw):
            break
        if len(raw) - len(raw.lstrip()) <= indent:
            break
        collected.append(raw.strip())
    while collected and not collected[-1]:
        collected.pop()
    text = "\n".join(collected).strip()
    if not text:
        return ({"text": None, "lines": 0},
                Source("body", "absent", "no continuation lines under the item"))
    return ({"text": text, "lines": len([x for x in collected if x])},
            Source("body", "read"))


def read_rules(directory: Path | None) -> tuple[list[dict[str, Any]], Source]:
    """The repo's scope fence — `CLAUDE.md` / `AGENTS.md`, inlined and capped."""
    if directory is None:
        return [], Source("rules", "error", "repo is not checked out here")
    out: list[dict[str, Any]] = []
    for name in _RULES_FILES:
        path = directory / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        out.append({"path": name, "bytes": len(text.encode("utf-8")),
                    "truncated": len(text) > _RULES_CAP, "text": text[:_RULES_CAP]})
    return out, Source("rules", "read" if out else "absent",
                       None if out else "repo keeps no CLAUDE.md/AGENTS.md")


def match_origin_issue(issues: list[dict[str, Any]],
                       item: dict[str, Any]) -> dict[str, Any] | None:
    """The inbox issue this item accepted, by the same rule `inbox.py` accepts with.

    ADR-ECO-006 D9: acceptance is *derived* — an issue's `slug:` appearing on a
    checkbox line of the target repo's `TODO.md` IS the acceptance, stored nowhere.
    Read backwards, that same fact names the request this item answers, and the
    request has the body the item lacks.
    """
    line = item.get("source_line") or ""
    if not line:
        return None
    import inbox  # local: only this path needs it, and it shells out to gh

    for issue in issues:
        slug = inbox.parse_field(issue.get("body") or "", "slug")
        if slug and slug in line:
            return {
                "repo": (issue.get("repository") or {}).get("name"),
                "number": issue.get("number"),
                "title": issue.get("title"),
                "slug": slug,
                "body": issue.get("body"),
            }
    return None


def read_origin_issue(item: dict[str, Any],
                      owner: str | None) -> tuple[dict[str, Any] | None, Source]:
    """The originating inbox issue, when asked for. Never guessed offline."""
    if owner is None:
        return None, Source("origin_issue", "not_queried", "pass --issues to ask gh")
    import inbox

    issues = inbox.search_inbox(owner or inbox.DEFAULT_OWNER)
    if issues is None:
        return None, Source("origin_issue", "error", "gh unavailable or failed")
    found = match_origin_issue(issues, item)
    if found is None:
        return None, Source("origin_issue", "absent",
                            "no open inbox issue whose slug is on this line")
    return found, Source("origin_issue", "read")


# ─────────────────────────── the verdict ───────────────────────────


def grade(sources: list[Source], body: dict[str, Any],
          origin: dict[str, Any] | None) -> dict[str, Any]:
    """How much context was actually assembled, and whether `execute` may run.

    The floor for `execute` is a WRITTEN requirement — a design doc or the
    originating issue. An epic goal says what the stream is for; it does not say
    what this item must do, and a run that changes a repo on that basis is
    improvising. `plan` is always allowed: reading and proposing is what a thin
    context is for.
    """
    states = {s.source: s.state for s in sources}
    has_doc = states.get("docs") == "read"
    has_issue = origin is not None
    has_body = len(body.get("text") or "") >= _BODY_SUBSTANTIAL
    unknowns = sorted(s for s, state in states.items()
                      if state in ("not_queried", "error"))
    if has_doc or has_issue or has_body:
        level, why = "rich", "a written requirement was found"
    elif states.get("epic") == "read":
        level, why = "thin", (
            "only the epic's goal — no written requirement for THIS item")
    else:
        level, why = "bare", "nothing beyond the item line itself"
    return {
        "grade": level,
        "reason": why,
        "execute_allowed": level == "rich",
        "unknown_sources": unknowns,
        "note": ("sources listed in unknown_sources were not read; their "
                 "emptiness above is not evidence of absence") if unknowns else None,
    }


def build_pack(root: Path, manifest_path: Path, registry_path: Path, repo: str,
               item_id: str, owner: str | None = None) -> dict[str, Any]:
    """Assemble the whole context pack for one item."""
    node_id = f"todo://{repo}/{item_id}"
    snapshot, checkouts = fleet_snapshot(root, manifest_path)
    item = read_item(snapshot, node_id, checkouts)
    directory = checkouts.get(item["repo"])
    sources = [Source("item", "read")]
    epic, src = read_epic(registry_path, item["epic"]); sources.append(src)
    body, src = read_body(directory, item); sources.append(src)
    graph, src = read_graph(snapshot, node_id); sources.append(src)
    docs, src = read_docs(directory, item); sources.append(src)
    rules, src = read_rules(directory); sources.append(src)
    origin, src = read_origin_issue(item, owner); sources.append(src)
    return {
        "node_id": node_id,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "item": item,
        "body": body,
        "epic": epic,
        "graph": graph,
        "docs": docs,
        "rules": rules,
        "origin_issue": origin,
        "sources": [asdict(s) for s in sources],
        "completeness": grade(sources, body, origin),
    }


# ─────────────────────────── rendering ───────────────────────────


def render(pack: dict[str, Any]) -> str:
    """The pack as text meant to be pasted into an agent session."""
    item, out = pack["item"], []
    add = out.append
    add(f"# {item['node_id']}")
    add("")
    add(f"**{item['title']}**")
    add("")
    add(f"- repo: `{item['repo']}` · status: `{item['status']}` · "
        f"{item['path']}:{item['line']}")
    if item["section"]:
        add(f"- section: {item['section']}")
    add(f"- epic: `{item['epic'] or '—'}` · defect: `{item['defect'] or '—'}` · "
        f"owner: `{item['owner'] or '—'}`")
    if item["trigger"]:
        add(f"- trigger: {item['trigger']}")
    if item["source_line"]:
        add("")
        add("```")
        add(item["source_line"])
        add("```")

    if pack["body"]["text"]:
        add("")
        add("## Body (продолжение пункта в TODO.md)")
        add("")
        add(pack["body"]["text"])

    epic = pack["epic"]
    add("")
    add("## Epic")
    if epic is None:
        add("_нет_ (см. sources ниже)")
    else:
        program = epic["program"]
        add(f"`{epic['id']}` — {epic['title']} · status `{epic['status']}` · "
            f"программа `{program['id']}` ({program['kind']})")
        if epic["goal"]:
            add("")
            add(f"**Цель потока:** {epic['goal']}")
        if epic["notes"]:
            add("")
            add(epic["notes"])

    graph = pack["graph"]
    add("")
    add("## Graph")
    if not any((graph["blocked_by"], graph["blocks"],
                graph["unresolved_refs"], graph["diagnostics"])):
        add("нет рёбер и диагностик")
    for edge in graph["blocked_by"]:
        add(f"- ждёт: `{edge['node_id']}` [{edge['status']}] {edge['title'] or ''}")
    for edge in graph["blocks"]:
        add(f"- его ждёт: `{edge['node_id']}` [{edge['status']}] {edge['title'] or ''}")
    for ref in graph["unresolved_refs"]:
        add(f"- нерезолвленная ссылка: `{ref}`")
    for diag in graph["diagnostics"]:
        add(f"- [{diag['severity']}] {diag['code']}: {diag['message']}")

    docs = pack["docs"]
    add("")
    add("## Docs")
    for doc in docs["named"]:
        mark = "" if doc["exists"] else " — ФАЙЛ НЕ НАЙДЕН"
        add(f"- названо в пункте/секции: `{doc['path']}`{mark}")
    grouped: dict[str, int] = {}
    for hit in docs["mentions"]:
        grouped[hit["path"]] = grouped.get(hit["path"], 0) + 1
    for path, count in sorted(grouped.items()):
        add(f"- упоминает `@id`: `{path}` ({count})")
    if not docs["named"] and not grouped:
        add("ничего не найдено")

    if pack["origin_issue"]:
        origin = pack["origin_issue"]
        add("")
        add(f"## Origin issue — {origin['repo']}#{origin['number']} "
            f"(slug: `{origin['slug']}`)")
        add("")
        add(origin["title"] or "")
        if origin["body"]:
            add("")
            add(origin["body"])

    if pack["rules"]:
        add("")
        add("## Repo rules")
        for rule in pack["rules"]:
            suffix = " (обрезано)" if rule["truncated"] else ""
            add(f"- `{rule['path']}` — {rule['bytes']} байт{suffix}")

    completeness = pack["completeness"]
    add("")
    add("## Completeness")
    add(f"- grade: **{completeness['grade']}** — {completeness['reason']}")
    add(f"- execute_allowed: **{completeness['execute_allowed']}**")
    for source in pack["sources"]:
        detail = f" — {source['detail']}" if source["detail"] else ""
        add(f"- {source['source']}: {source['state']}{detail}")
    if completeness["note"]:
        add(f"- ⚠ {completeness['note']}")
    return "\n".join(out)


# ─────────────────────────── cli ───────────────────────────


def parse_uri(uri: str) -> tuple[str, str]:
    match = re.fullmatch(r"todo://([^/]+)/(.+)", uri.strip())
    if match is None:
        raise ContextError(f"not a todo:// uri: {uri}")
    return match.group(1), match.group(2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--uri", help="todo://<repo>/<id>")
    parser.add_argument("--repo")
    parser.add_argument("--id", dest="item_id")
    parser.add_argument("--root", default=None, help="workspace root")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--registry", default=None, help="epics.toml")
    parser.add_argument("--issues", nargs="?", const="", default=None,
                        metavar="OWNER",
                        help="ask gh for the originating inbox issue "
                             "(owner defaults to inbox.DEFAULT_OWNER)")
    parser.add_argument("--json", action="store_true", help="machine-readable pack")
    args = parser.parse_args(argv)

    try:
        if args.uri:
            repo, item_id = parse_uri(args.uri)
        elif args.repo and args.item_id:
            repo, item_id = args.repo, args.item_id
        else:
            parser.error("give --uri, or both --repo and --id")
        root = find_root(args.root)
        pack = build_pack(
            root,
            Path(args.manifest) if args.manifest else default_manifest(root),
            Path(args.registry) if args.registry else default_registry(root),
            repo, item_id, owner=args.issues,
        )
    except ContextError as exc:
        print(f"todo-context: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(pack, ensure_ascii=False, indent=2) if args.json else render(pack))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
