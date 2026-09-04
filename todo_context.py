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
tell them apart will confidently work from a context it never had.

The grade at the bottom is computed from the CONTENT that was found — a written
requirement, sized — never from a source's state, because a state answers "did we
look?" and never "is there a requirement?". Reading a state as the answer is
exactly what let a `git grep` hit on a branch name grade an item executable
(ревью PR #125). The states are reported alongside, and every unread one is named
in `unknown_sources`, so a thin verdict can be told from an uninformed one.

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

#: Where a requirement can actually be WRITTEN: a markdown file under a
#: docs/spec/plan directory. A mention of the `@id` anywhere else — a branch name
#: in a plan, a literal inside a `print()`, a CI job — is context, never a
#: requirement, and must not be able to raise the grade (ревью PR #125, круг 1).
_DOC_MENTION_RE = re.compile(r"(?:^|/)(?:docs|spec|specs|plans|workstreams)/")

#: The tail of an id: the grammar is `[a-z0-9][a-z0-9._-]{0,63}` (ADR-ECO-005
#: PF-2B), so a match followed by one of these chars is a LONGER id, not this one.
#: A dot is the exception — it is both a legal id char and the end of a sentence,
#: so it only continues the id when something id-shaped follows it.
_ID_TAIL = r"(?![a-z0-9_-])(?!\.[a-z0-9])"

#: The start of any checklist item, at any indent — the boundary a continuation
#: block ends at. Same shape as the package's own item regex; it is used here only
#: to find where one item's lines STOP, never to decide what an item means.
_ITEM_START_RE = re.compile(r"^(\s*)[-*]\s*\[[ xX]\]\s")
_HEADING_START_RE = re.compile(r"^#{1,6}\s")
#: A continuation block this long (non-blank chars) is treated as a written
#: requirement. Two short lines of aside are not a spec; a paragraph is.
_BODY_SUBSTANTIAL = 120

#: The same floor for a named design doc. A path committed ahead of the writing
#: is a normal habit, and an empty stub is not a requirement — reading `exists`
#: alone reintroduced "empty looks green" on the docs side (ревью PR #125, круг 3).
_DOC_SUBSTANTIAL = 120

#: Prose in the reference's own section, once headings, blank lines and checklist
#: items are dropped. For a NAMED doc the file size is a fair proxy — the item
#: points AT the doc as its spec. For a mention the direction is reversed: the doc
#: points at the item, and every real doc clears a byte threshold, so file size
#: measured nothing about the item. A checklist line quoting `@id:` is a step in
#: someone else's plan, not a specification of this item (ревью PR #125, круг 7).
_SECTION_SUBSTANTIAL = 120

#: And for the originating issue. `inbox` deliberately does not require a body
#: (`inbox._well_formed`), so `slug:` + `from:` and nothing else is a valid
#: request — valid, but not a requirement (ревью PR #125, круг 4). Three sources
#: can carry the requirement, and all three answer to the same floor.
_ISSUE_SUBSTANTIAL = 120

_RULES_FILES = ("CLAUDE.md", "AGENTS.md")
#: Bytes (not characters) of a rules file inlined before it is marked truncated.
#: These files are mostly Cyrillic, where a character is two bytes: cutting by
#: character would inline ~1.4x the intended payload and misreport `truncated`
#: against the `bytes` printed beside it (Copilot, PR #125).
_RULES_CAP = 8000
_GREP_CAP = 40  # hits PRINTED from one `git grep`
#: Lines read from one `git grep` before the read itself is capped. The display
#: cap used to be applied to raw stdout, so a canonical reference sitting past
#: hit 40 was invisible to the grade while the source still reported a plain
#: `read` — "we did not look" wearing the face of "there is nothing there"
#: (ревью PR #125, круг 4).
_GREP_HARD_CAP = 2000
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


def build_inputs(root: Path,
                 index: Any) -> tuple[list[Any], dict[str, Path], list[str]]:
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
    unread: list[str] = []
    for repo in sorted(set(index.canonical_keys) | set(on_disk)):
        directory = on_disk.get(repo)
        if directory is None:
            inputs.append(_pf.RepoInput(repo, available=False))
            unread.append(repo)
            continue
        checkouts[repo] = directory
        todo = directory / "TODO.md"
        text = todo.read_text(encoding="utf-8", errors="ignore") \
            if todo.is_file() else None
        inputs.append(_pf.RepoInput(repo, todo_text=text, available=True))
    return inputs, checkouts, unread


def fleet_snapshot(
    root: Path, manifest_path: Path
) -> tuple[dict[str, Any], dict[str, Path], list[str], Any]:
    """The snapshot, where each repo lives, which repos could not be read, and the
    manifest index — the one authority on how a repo may be spelled."""
    if _pf is None:  # pragma: no cover - environment guard
        raise ContextError(
            "plan_fields is not importable — run through the pinned env:\n"
            "  uv run --frozen python todo_context.py ..."
        )
    if not manifest_path.is_file():
        raise ContextError(f"no workspace manifest at {manifest_path}")
    try:
        index = _pf.manifest_index(manifest_path)
        inputs, checkouts, unread = build_inputs(root, index)
    except _pf.AmbiguousIdentityError as exc:
        # Same answer `check-plan-fields.py` gives, and in BOTH places it can be
        # raised: `checkout_map` decides identity too. A workspace holding two
        # checkouts of one repo has no correct answer — a traceback is not one.
        raise ContextError(f"cannot resolve repo identity: {exc}") from exc
    return _pf.parse_fleet(inputs, index), checkouts, unread, index


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
        # The registry WAS read and the epic is genuinely not in it. Calling that
        # `error` put a successfully read source into `unknown_sources`, under a
        # note saying it "was not read" — the module's own rule, inverted
        # (ревью PR #125, круг 4). `error` stays for "could not read".
        return None, Source("epic", "absent",
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


def read_graph(snapshot: dict[str, Any], node_id: str,
               unread: list[str] | None = None) -> tuple[dict[str, Any], Source]:
    """What this item waits on, what waits on it, and what is wrong with that.

    Edge semantics, staleness and the diagnostics all come from the package
    (`parse_fleet` + `check_fleet`); nothing here re-decides them.

    The REVERSE side of the graph is host-dependent: a repo of the manifest that
    is not cloned here is skipped whole by `parse_fleet`, so its `@blocked_by` on
    this item produces neither an edge nor a diagnostic. `unread` names those
    repos, and the render prints them beside the edges — "nobody waits on this"
    and "half the fleet was never read" must not look alike (ревью PR #125,
    круг 2), which is the same rule the module keeps for every other source.

    It is incomplete a second way, and not because of this host: the transitional
    `<repo>#<slug>` form never becomes an edge — "no `resolved_target`, no edge,
    ever" — and when it matches exactly one item it raises no diagnostic either,
    so such a wait is invisible to this slice entirely. Those references are
    listed as `legacy_waits` and the count is named in the detail, but they are
    NOT resolved here: pairing a slug with an item is the package's rule
    (`check_legacy_fleet`), and a private one would be the round-5 mistake again.
    An exact slug match is reported as a candidate, nothing narrower or wider
    (ревью PR #125, круг 7).
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
    repo = node_id.removeprefix("todo://").split("/", 1)[0]
    item_id = node_id.rsplit("/", 1)[-1]
    legacy: list[dict[str, Any]] = []
    for ref in snapshot.get("references", []):
        raw = ref.get("legacy_blocker_ref")
        if not raw or "#" not in raw:
            continue
        target_repo, _, slug = raw.partition("#")
        if target_repo.lower() != repo.lower():
            continue
        legacy.append({"raw_ref": raw, "from": ref.get("source_node_id"),
                       "names_this_item": slug == item_id})
    block = {
        "blocked_by": blocked_by,
        "blocks": blocks,
        "legacy_waits": legacy,
        "unread_repos": sorted(unread or []),
        "unresolved_refs": [u for u in unresolved if u],
        "diagnostics": [{"code": d["code"], "severity": d["severity"],
                         "message": d["message"]} for d in mine],
    }
    notes = []
    if unread:
        notes.append(f"{len(unread)} репо флота не склонированы здесь: "
                     f"{', '.join(sorted(unread))}")
    if legacy:
        notes.append(f"переходные ожидания к `{repo}` ({len(legacy)}) ребром "
                     f"не становятся — в срезе рёбер их нет")
    detail = None if not notes else (
        "обратная сторона (кто ждёт этот пункт) неполна: " + "; ".join(notes))
    return block, Source("graph", "read", detail)


def named_doc_paths(item: dict[str, Any]) -> list[str]:
    """Doc paths written into the item line or its section heading."""
    return [path for path, _ in named_doc_sources(item)]


def named_doc_sources(item: dict[str, Any]) -> list[tuple[str, str]]:
    """Each named doc with WHERE it was named: `line` or `section`.

    A doc named in the item's own line is about this item. A doc named in the
    section heading is about the section — it was granting every item under that
    heading a written requirement none of them individually named (ревью PR #125,
    круг 6). It stays in the pack as context; only the item-level one grades.
    """
    seen: list[tuple[str, str]] = []
    known: set[str] = set()
    for where, text in (("line", item.get("source_line")),
                        ("section", item.get("section"))):
        for match in _DOC_PATH_RE.finditer(text or ""):
            path = match.group(1)
            if path not in known:
                known.add(path)
                seen.append((path, where))
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
        # errors="replace": a sibling repo holding one non-UTF-8 file must not
        # crash the pack — `docs: error` is an answer, a traceback is not
        done = subprocess.run(cmd, capture_output=True, text=True,
                              errors="replace", timeout=_GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"git grep failed: {exc}"
    if done.returncode not in (0, 1):  # 1 = no match, a legitimate answer
        return None, (done.stderr.strip().splitlines() or ["git grep failed"])[-1]
    hits: list[dict[str, Any]] = []
    for raw in done.stdout.splitlines()[:_GREP_HARD_CAP]:
        path, _, rest = raw.partition(":")
        line_no, _, text = rest.partition(":")
        # `full` is classified, `text` is displayed: a canonical reference past
        # the display cap must still count (ревью PR #125, круг 2)
        hits.append({"path": path, "line": line_no,
                     "text": text.strip()[:300], "full": text})
    return hits, None


def is_doc_mention(path: str) -> bool:
    """Can this path HOLD a requirement? Markdown under docs/spec/plan dirs."""
    return path.endswith(".md") and _DOC_MENTION_RE.search(path) is not None


def mention_states_a_requirement(lines: list[str], line_no: int) -> bool:
    """Does the reference on `line_no` sit in text that SPECIFIES the item?

    Two shapes appear in this fleet, and only one is a requirement. A heading or
    prose naming the item, followed by a section that says something — the
    roadmap's "## P1 — сократить промпт (`review-kit-prompt-diet`)" — is one. A
    checklist line quoting the item's future `TODO.md` text inside the plan of a
    DIFFERENT item — behaviour-console.md:159 — is the other, and it states
    nothing about the item at all (ревью PR #125, круг 7).
    """
    index = line_no - 1
    if not 0 <= index < len(lines):
        return False
    # climb to the line that OWNS this one: a reference three lines into a
    # checklist item is still inside that item (behaviour-console.md:156-159)
    owner = index
    while owner > 0 and lines[owner - 1].strip() and not _ITEM_START_RE.match(
            lines[owner]) and not _HEADING_START_RE.match(lines[owner]):
        owner -= 1
    if _ITEM_START_RE.match(lines[owner]):
        return False  # a step in a plan, not a spec of the item
    start = 0
    for i in range(index, -1, -1):
        if _HEADING_START_RE.match(lines[i]):
            start = i + 1
            break
    prose = 0
    for i in range(start, len(lines)):
        raw = lines[i]
        if i > start and _HEADING_START_RE.match(raw):
            break
        if not raw.strip() or _ITEM_START_RE.match(raw):
            continue
        prose += len(raw.strip())
    return prose >= _SECTION_SUBSTANTIAL


def item_ref_re(item_id: str) -> re.Pattern[str]:
    """Matches a line that REFERS to the item, not one that merely contains its id.

    `git grep --fixed-strings` matches a bare substring, so the id of an item
    also turns up inside a longer id, a component name, a branch name and a file
    name. A reference is MARKED, in one of the three ways this fleet marks one:
    the `@id:` tag, the `todo://<repo>/<id>` URI, or a code span holding exactly
    the id. A bare word in prose is not a reference — that is how a component, a
    branch and a file name are spelled too — but a code span is: dropping
    "## P1 — сократить промпт (`review-kit-prompt-diet`)", a real requirement in
    a roadmap, was this fix overshooting on the first try (ревью PR #125, круг 2).
    """
    esc = re.escape(item_id)
    return re.compile(rf"(?:(?:@id:|todo://[\w.-]+/){esc}{_ID_TAIL}|`{esc}`)")


def read_docs(directory: Path | None,
              item: dict[str, Any]) -> tuple[dict[str, Any], Source]:
    """Design docs for this item: paths it names, plus every mention of its `@id`.

    Mentions are collected from the whole repo but each one is marked `doc`:
    `git grep` matches a bare substring, so the id also turns up in branch names,
    CLI output literals and tests. Those are worth PRINTING as context and must
    never count as a written requirement — `grade` reads the mark, not the state.

    `doc` means BOTH: the file can hold a requirement (markdown under a
    docs/spec/plan directory) AND the line points at this item by one of the two
    canonical forms. Either half alone was demonstrably not enough.
    """
    named = named_doc_sources(item)
    if directory is None:
        # `named` keeps the shape of the success path: an error state must
        # degrade honestly, not blow up grade/render (ревью PR #125, круг 2)
        unresolved = [{"path": rel, "exists": False, "bytes": None, "named_in": where}
                      for rel, where in named]
        return ({"named": unresolved, "mentions": []},
                Source("docs", "error", "repo is not checked out here"))
    resolved = []
    for rel, where in named:
        path = directory / rel
        resolved.append({"path": rel, "exists": path.is_file(), "named_in": where,
                         "bytes": path.stat().st_size if path.is_file() else None})
    hits, error = git_grep(directory, item["id"])
    if error is not None:
        return ({"named": resolved, "mentions": []}, Source("docs", "error", error))
    ref = item_ref_re(item["id"])
    read: dict[str, list[str]] = {}
    for hit in hits:
        path, full = hit["path"], hit.pop("full", "")
        marked = is_doc_mention(path) and ref.search(full) is not None
        if marked and path not in read:
            file = directory / path
            read[path] = (file.read_text(encoding="utf-8", errors="replace")
                          .splitlines() if file.is_file() else [])
        hit["doc"] = marked and mention_states_a_requirement(
            read.get(path, []), int(hit["line"]) if hit["line"].isdigit() else 0)
    # classify first, then cut for display, and keep every reference: the cut is
    # about what is READABLE, never about what was found
    refs = [h for h in hits if h["doc"]]
    rest = [h for h in hits if not h["doc"]]
    shown = (refs + rest)[:_GREP_CAP]
    cut = len(hits) - len(shown)
    cut_refs = max(0, len(refs) - _GREP_CAP)
    hard = len(hits) >= _GREP_HARD_CAP
    block = {"named": resolved, "mentions": shown, "hidden_mentions": cut,
             "hidden_references": cut_refs, "grep_capped": hard}
    have = any(d["exists"] for d in resolved) or bool(hits)
    detail = None if have else f"no file names @id:{item['id']}"
    if hard:
        detail = (f"выдача git grep обрезана на {_GREP_HARD_CAP} строках — "
                  f"часть упоминаний не классифицирована")
    elif cut_refs:
        detail = (f"{cut} упоминаний не показаны, среди них {cut_refs} ссылок "
                  f"на пункт — печать обрезана на {_GREP_CAP}")
    elif cut:
        detail = f"{cut} упоминаний не показаны (ссылки на пункт показаны все)"
    return block, Source("docs", "read" if have else "absent", detail)


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
        raw = path.read_bytes()
        # decode AFTER the cut, ignoring a codepoint split by it
        out.append({"path": name, "bytes": len(raw),
                    "truncated": len(raw) > _RULES_CAP,
                    "text": raw[:_RULES_CAP].decode("utf-8", errors="ignore")})
    return out, Source("rules", "read" if out else "absent",
                       None if out else "repo keeps no CLAUDE.md/AGENTS.md")


def match_origin_issue(issues: list[dict[str, Any]],
                       item: dict[str, Any]) -> dict[str, Any] | None:
    """The inbox issue this item accepted, by the same rule `inbox.py` accepts with.

    ADR-ECO-006 D9: acceptance is *derived* — an issue's `slug:` appearing on a
    checkbox line of the target repo's `TODO.md` IS the acceptance, stored nowhere.
    Read backwards, that same fact names the request this item answers, and the
    request has the body the item lacks.

    The pair is (issue's repo → THAT repo's `TODO.md`), exactly as `inbox.render`
    derives it, so the issue's repo is checked here too. `search_inbox` returns
    every open `inbox` issue of the owner, and this fleet also writes OUTGOING
    requests into an item's own line ("заведён disputatio#52 (slug: …)"); without
    the check that outgoing wait comes back as this item's own requirement, with
    the direction reversed (ревью PR #125, круг 1).
    """
    line = item.get("source_line") or ""
    if not line:
        return None
    import inbox  # local: only this path needs it, and it shells out to gh

    repo = (item.get("repo") or "").lower()
    found: list[dict[str, Any]] = []
    for issue in issues:
        issue_repo = ((issue.get("repository") or {}).get("name") or "").lower()
        if not repo or issue_repo != repo:
            continue  # another repo's request; see the docstring
        slug = inbox.parse_field(issue.get("body") or "", "slug")
        if slug and slug in line:  # inbox.is_accepted's rule, not a second one
            found.append({
                "repo": (issue.get("repository") or {}).get("name"),
                "number": issue.get("number"),
                "title": issue.get("title"),
                "slug": slug,
                "body": issue.get("body"),
                "exact": slug_re(slug).search(line) is not None,
            })
    if not found:
        return None
    # Several open requests can pair with one line. Which one states the
    # requirement is then unknown, and picking by the order `gh` happened to
    # return them is a coin toss printed as a fact (ревью PR #125, круг 5).
    first = found[0]
    first["rival_issues"] = [f["number"] for f in found[1:]]
    return first


def slug_re(slug: str) -> re.Pattern[str]:
    """The slug as a token, for GRADING only — never for deciding the pair.

    Acceptance is one derived fact (ADR-ECO-006 D9), and `inbox.is_accepted` owns
    the test: a substring over the item's raw text, weaker than it reads
    (`benchmark-2` also matches `benchmark-20`) and deliberately so — its
    docstring says tightening is the package's call and "a private stricter rule
    here would be exactly the divergence that ADR removes". Round 4 introduced
    exactly that rule, so `make inbox` said "принят" where this said "нет issue"
    about the same pair (ревью PR #125, круг 5).

    So the pair is now found by the shared rule and reported the same way; this
    pattern only marks whether the match was a whole token. A prefix collision
    still shows up in the pack as context — it just cannot become the written
    requirement an executor is handed. Tightening the shared rule belongs in the
    package: `TODO.md @id:inbox-slug-token-match`.
    """
    return re.compile(rf"(?<![a-z0-9._-]){re.escape(slug)}{_ID_TAIL}")


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
          origin: dict[str, Any] | None,
          docs: dict[str, Any] | None = None,
          epic: dict[str, Any] | None = None) -> dict[str, Any]:
    """How much context was actually assembled, and whether `execute` may run.

    The floor for `execute` is a WRITTEN requirement — a design doc or the
    originating issue. An epic goal says what the stream is for; it does not say
    what this item must do, and a run that changes a repo on that basis is
    improvising. `plan` is always allowed: reading and proposing is what a thin
    context is for.

    A source's STATE answers "did we look?" and never "is there a requirement?" —
    reading the state as the answer let a `git grep` hit on a branch name grade an
    item as executable (ревью PR #125, круг 1). So the docs evidence is read from
    the block: an existing named doc, or a mention in a file that can hold a
    requirement.
    """
    states = {s.source: s.state for s in sources}
    docs = docs or {}
    # an epic whose goal is empty is not "the stream's goal is known" either:
    # same presence-is-not-evidence rule, applied before it was reported
    has_epic_goal = bool((epic or {}).get("goal") or (epic or {}).get("notes"))
    has_doc = (any((d.get("bytes") or 0) >= _DOC_SUBSTANTIAL
                   for d in docs.get("named", [])
                   if d.get("exists") and d.get("named_in", "line") == "line")
               or any(m.get("doc") for m in docs.get("mentions", [])))
    origin = origin or {}
    has_issue = (len(origin.get("body") or "") >= _ISSUE_SUBSTANTIAL
                 and origin.get("exact", True)
                 and not origin.get("rival_issues"))
    has_body = len(body.get("text") or "") >= _BODY_SUBSTANTIAL
    unknowns = sorted(s for s, state in states.items()
                      if state in ("not_queried", "error"))
    if has_doc or has_issue or has_body:
        level, why = "rich", "a written requirement was found"
    elif has_epic_goal:
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
    snapshot, checkouts, unread, index = fleet_snapshot(root, manifest_path)
    # `parse_fleet` keys nodes by the canonical name, so a legitimate spelling —
    # `Maestro`, or a git_dir locator the user sees on disk — must be normalised
    # by the contract, not by the user. Without it the refusal reads "no such
    # item" for an item that is right there (ревью PR #125, круг 3).
    resolved = index.resolve_ref(repo) or repo
    node_id = f"todo://{resolved}/{item_id}"
    item = read_item(snapshot, node_id, checkouts)
    directory = checkouts.get(item["repo"])
    sources = [Source("item", "read")]
    epic, src = read_epic(registry_path, item["epic"]); sources.append(src)
    body, src = read_body(directory, item); sources.append(src)
    graph, src = read_graph(snapshot, node_id, unread); sources.append(src)
    docs, src = read_docs(directory, item); sources.append(src)
    rules, src = read_rules(directory); sources.append(src)
    origin, src = read_origin_issue(item, owner); sources.append(src)
    return {
        "node_id": node_id,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        # Where the item's repo actually lives on this host. A consumer that RUNS
        # anything must run it there; without this it would inherit the caller's
        # cwd, which is devtools (ревью PR #126).
        "checkout": str(directory) if directory is not None else None,
        "item": item,
        "body": body,
        "epic": epic,
        "graph": graph,
        "docs": docs,
        "rules": rules,
        "origin_issue": origin,
        "sources": [asdict(s) for s in sources],
        "completeness": grade(sources, body, origin, docs, epic),
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
    if graph.get("unread_repos"):
        add(f"- ⚠ не склонированы здесь, их ожидания не видны: "
            f"{', '.join(graph['unread_repos'])}")
    for wait in graph.get("legacy_waits", []):
        mark = (" — слаг совпадает с `@id` этого пункта"
                if wait["names_this_item"] else "")
        add(f"- ⚠ переходное ожидание `{wait['raw_ref']}` от `{wait['from']}` "
            f"ребром не стало{mark}")
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
        where = ("названо в строке пункта"
                 if doc.get("named_in", "line") == "line"
                 else "названо в заголовке секции (контекст секции, не пункта)")
        add(f"- {where}: `{doc['path']}`{mark}")
    grouped: dict[str, int] = {}
    refs: set[str] = set()
    for hit in docs["mentions"]:
        grouped[hit["path"]] = grouped.get(hit["path"], 0) + 1
        if hit.get("doc"):
            refs.add(hit["path"])
    for path, count in sorted(grouped.items()):
        # the label must be the flag the grade reads, not a second opinion
        kind = "ссылается на пункт" if path in refs else "упоминание (не ссылка)"
        add(f"- {kind}: `{path}` ({count})")
    if not docs["named"] and not grouped:
        add("ничего не найдено")
    if docs.get("hidden_mentions"):
        tail = (f", среди них {docs['hidden_references']} ссылок на пункт"
                if docs.get("hidden_references") else " (все ссылки — выше)")
        add(f"- ещё {docs['hidden_mentions']} упоминаний не показаны{tail}")

    if pack["origin_issue"]:
        origin = pack["origin_issue"]
        add("")
        add(f"## Origin issue — {origin['repo']}#{origin['number']} "
            f"(slug: `{origin['slug']}`)")
        if not origin.get("exact", True):
            add("")
            add(f"⚠ слаг совпал подстрокой, не токеном (как в `inbox`), поэтому "
                f"требованием не считается — контекст")
        if origin.get("rival_issues"):
            others = ", ".join(f"#{n}" for n in origin["rival_issues"])
            add("")
            add(f"⚠ с этой строкой пары также образуют {others} — какой из них "
                f"ставит задачу, неизвестно; требованием не считается")
        add("")
        add(origin["title"] or "")
        if origin["body"]:
            add("")
            add(origin["body"])

    if pack["rules"]:
        add("")
        add("## Repo rules")
        for rule in pack["rules"]:
            # the text itself is inlined in the pack (--json), not here: a scope
            # fence is thousands of bytes and would bury the item. Say where it
            # is, so "(обрезано)" stops describing invisible text (круг 3).
            cut = f", инлайн обрезан до {_RULES_CAP} байт" if rule["truncated"] else ""
            add(f"- `{rule['path']}` — {rule['bytes']} байт{cut}; "
                f"текст — в `--json`")

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
