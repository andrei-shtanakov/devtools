"""The ``vendored-in`` role from vendoring declarations, fail-closed (spec §9.7).

Four declaration formats met in devtools (2026-09-26):

- A ``PIN`` with ``# SOURCE: <owner> @ <ref>…`` and ``<sha256>  <path>`` lines
  (paths from the repo root);
- B ``PIN`` lines ``<sha256>  <path>  <owner>@<ref>`` (paths from the PIN dir);
- C ``PINNED*`` with ``upstream: <url>``, ``commit: <ref>`` and ``<path> <sha256>``
  lines (paths from the declaration dir);
- D a header ``# VENDORED: <owner> @ <ref> — <path>`` inside the file itself;
- E ``key: value`` prose with ``source:``/``repo:`` + a hex ref; members are the
  declaration's folder (spec §10.5).
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from selfcheck.model import Confidence, Finding, Location
from selfcheck.roles import Role

HEAD_LINES = 5
_HEX = r"[0-9a-f]{7,40}"
_SHA256 = r"[0-9a-f]{64}"
_D = re.compile(rf"^# VENDORED:\s*(\S+)\s+@\s+({_HEX})\b")
_A = re.compile(rf"^# SOURCE:\s*(\S+)\s+@\s+({_HEX})\b")
_A_MEMBER = re.compile(rf"^({_SHA256})\s+(\S+)$")
_B_MEMBER = re.compile(rf"^({_SHA256})\s+(\S+)\s+([\w.-]+)@({_HEX})$")
_C_MEMBER = re.compile(rf"^(\S+)\s+({_SHA256})$")
_C_UPSTREAM = re.compile(r"^upstream:\s*(\S+)$")
_C_COMMIT = re.compile(rf"^commit:\s*({_HEX})$")
_E_LINE = re.compile(r"^(?:#\s*)?([A-Za-z_]+):\s*(.*)$")
_E_SOURCE_AT = re.compile(rf"^(\S+?)@({_HEX})\b")
_E_REF = re.compile(rf"^({_HEX})\b")
_E_HEADS = ("source", "repo")
_SHA_ANY = re.compile(_SHA256)


class DeclarationError(ValueError):
    """A candidate that parses as none of the formats A–D."""


@dataclass(frozen=True)
class Declaration:
    """One parsed vendoring declaration."""

    path: str
    fmt: str
    owner: str
    ref: str
    members: tuple[str, ...]
    folder: str | None = None  # format E: members are this folder (§10.5)


@dataclass
class VendorResult:
    """Roles, protection and instrument findings of one repo."""

    members: dict[str, list[Declaration]] = field(default_factory=dict)
    protected: set[str] = field(default_factory=set)
    findings: list[Finding] = field(default_factory=list)
    broken: bool = False


def is_candidate(rel: str, text: str, *, is_node: bool) -> bool:
    """A file that claims to be a declaration (spec §9.7 «Кандидаты»)."""
    if rel.startswith(".github/"):
        return False
    base = posixpath.basename(rel).lower()
    if not is_node and (base == "pin" or base.startswith(("pinned", "vendor"))):
        return True
    head = text.splitlines()[:HEAD_LINES]
    if any(line.startswith("# VENDORED:") for line in head):
        return True
    if any(line.startswith("# SOURCE:") and "@" in line for line in head):
        return True
    has_upstream = any(line.startswith("upstream:") for line in head)
    return has_upstream and any(
        line.startswith("commit:") for line in text.splitlines()
    )


def _member_path(rel: str, raw: str, *, from_root: bool) -> str:
    if raw.startswith("/") or ".." in raw.split("/"):
        raise DeclarationError(f"{rel}: path escapes the repo: {raw}")
    base = "" if from_root else posixpath.dirname(rel)
    return posixpath.normpath(posixpath.join(base, raw))


def _body(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _mixed(text: str) -> bool:
    """Another format's header or member line next to a D header."""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(("# SOURCE:", "upstream:", "commit:")):
            return True
        if _A_MEMBER.match(line) or _B_MEMBER.match(line) or _C_MEMBER.match(line):
            return True
    return False


def _parse_d(rel: str, text: str) -> Declaration | None:
    for line in text.splitlines()[:HEAD_LINES]:
        if line.startswith("# VENDORED:"):
            m = _D.match(line)
            if m is None:
                raise DeclarationError(f"{rel}: bad VENDORED header")
            if _mixed(text):
                raise DeclarationError(f"{rel}: VENDORED header mixed with members")
            return Declaration(rel, "D", m.group(1), m.group(2), (rel,))
    return None


def _parse_a(rel: str, lines: list[str]) -> Declaration | None:
    heads = [ln for ln in lines if ln.startswith("# SOURCE:")]
    if not heads:
        return None
    m = _A.match(heads[0]) if len(heads) == 1 else None
    if m is None:
        raise DeclarationError(f"{rel}: bad SOURCE header")
    members = []
    for line in lines:
        if line.startswith("#"):
            continue
        member = _A_MEMBER.match(line)
        if member is None:
            raise DeclarationError(f"{rel}: not a member line: {line[:60]}")
        members.append(_member_path(rel, member.group(2), from_root=True))
    if not members:
        raise DeclarationError(f"{rel}: no members")
    return Declaration(rel, "A", m.group(1), m.group(2), tuple(members))


def _parse_c(rel: str, lines: list[str]) -> Declaration | None:
    upstream = [_C_UPSTREAM.match(ln) for ln in lines if ln.startswith("upstream:")]
    if not upstream:
        return None
    commits = [_C_COMMIT.match(ln) for ln in lines if ln.startswith("commit:")]
    if len(upstream) != 1 or not upstream[0] or len(commits) != 1 or not commits[0]:
        raise DeclarationError(f"{rel}: bad upstream/commit header")
    owner = posixpath.basename(upstream[0].group(1).rstrip("/")).removesuffix(".git")
    owner = owner.rsplit(":", 1)[-1]
    members = []
    for line in lines:
        if line.startswith(("upstream:", "commit:", "#")):
            continue
        member = _C_MEMBER.match(line)
        if member is None:
            raise DeclarationError(f"{rel}: not a member line: {line[:60]}")
        members.append(_member_path(rel, member.group(1), from_root=False))
    if not members:
        raise DeclarationError(f"{rel}: no members")
    return Declaration(rel, "C", owner, commits[0].group(1), tuple(members))


def _parse_b(rel: str, lines: list[str]) -> Declaration:
    rows = [ln for ln in lines if not ln.startswith("#")]
    matches = [_B_MEMBER.match(ln) for ln in rows]
    if not rows or not all(matches):
        raise DeclarationError(f"{rel}: not a declaration")
    owners = {(m.group(3), m.group(4)) for m in matches if m}
    if len(owners) != 1:
        raise DeclarationError(f"{rel}: mixed owners")
    ((owner, ref),) = owners
    members = tuple(
        _member_path(rel, m.group(2), from_root=False) for m in matches if m
    )
    return Declaration(rel, "B", owner, ref, members)


def _all_b(lines: list[str]) -> bool:
    rows = [ln for ln in lines if not ln.startswith("#")]
    return bool(rows) and all(_B_MEMBER.match(ln) for ln in rows)


def _e_keys(text: str) -> dict[str, list[str]]:
    keys: dict[str, list[str]] = {}
    for raw in text.splitlines():
        m = _E_LINE.match(raw.strip())
        if m:
            keys.setdefault(m.group(1).lower(), []).append(m.group(2).strip())
    return keys


def _e_owner_ref(rel: str, keys: dict[str, list[str]]) -> tuple[str, str]:
    heads = [(k, v) for k in _E_HEADS for v in keys.get(k, [])]
    if len(heads) != 1:
        raise DeclarationError(f"{rel}: expected one source/repo line")
    kind, head = heads[0]
    at = _E_SOURCE_AT.match(head) if kind == "source" else None
    if at is not None:
        return at.group(1), at.group(2)
    commits = keys.get("commit", [])
    ref = _E_REF.match(commits[0]) if len(commits) == 1 else None
    if ref is None:
        raise DeclarationError(f"{rel}: no hex ref for the folder declaration")
    token = head.split()[0] if head.split() else ""
    owner = (
        posixpath.basename(token.rstrip("/")).removesuffix(".git")
        if kind == "repo"
        # `source: o@main` + `commit:` — the owner is before `@` (#437.2)
        else token.split("@", 1)[0]
    )
    if not owner:
        raise DeclarationError(f"{rel}: no owner")
    return owner, ref.group(1)


def _parse_e(rel: str, text: str) -> Declaration | None:
    keys = _e_keys(text)
    if not any(k in keys for k in _E_HEADS):
        return None
    folder = posixpath.dirname(rel)
    if not folder:
        raise DeclarationError(f"{rel}: folder declaration in the repo root")
    owner, ref = _e_owner_ref(rel, keys)
    return Declaration(rel, "E", owner, ref, (), folder)


def parse_declaration(rel: str, text: str) -> Declaration:
    """Parse one candidate as format D, A, C or B; else DeclarationError."""
    decl = _parse_d(rel, text)
    if decl is not None:
        return decl
    lines = _body(text)
    return (
        _parse_a(rel, lines)
        or _parse_c(rel, lines)
        # B before E when every non-comment line is a B member: a `# repo:`
        # comment in a B PIN is not an E key line (#437.1)
        or (_parse_b(rel, lines) if _all_b(lines) else None)
        or _parse_e(rel, text)
        or _parse_b(rel, lines)
    )


def _finding(
    repo: str, rule: str, severity: str, decl: str, key: str | None
) -> Finding:
    return Finding(
        rule=rule,
        category="selfcheck",
        severity=severity,
        confidence=Confidence.CONFIRMED,
        owner_repo=repo,
        anchor=f"file:{decl}",
        locations=[Location(decl, 1)],
        text_key=key,
        suggestion="почините декларацию вендоринга (формат A–E, §9.7, §10.5)",
    )


def _named_paths(rel: str, text: str, corpus: frozenset[str]) -> set[str]:
    folder = posixpath.dirname(rel)
    found: set[str] = set()
    for raw in text.split():
        token = raw.rstrip(":,")  # the `sha256 <path>: <sha>` form (r3 R3-1)
        for cand in (token, posixpath.join(folder, token)):
            norm = posixpath.normpath(cand)
            if norm in corpus:
                found.add(norm)
    return found


def _member_line_paths(folder: str, text: str, known: frozenset[str]) -> set[str]:
    """Corpus paths named on sha256 member lines, from the folder or the root.

    The folder reading wins: a token present in the folder never also names a
    same-named root file (#426 review)."""
    found: set[str] = set()
    for line in text.splitlines():
        if not _SHA_ANY.search(line):
            continue
        for raw in line.split():
            token = raw.rstrip(":,")
            in_folder = posixpath.normpath(posixpath.join(folder, token))
            from_root = posixpath.normpath(token)
            if in_folder in known:
                found.add(in_folder)
            elif from_root in known:
                found.add(from_root)
    return found


def _e_members(
    decl: Declaration, text: str, known: frozenset[str], nodes: frozenset[str]
) -> tuple[str, ...]:
    """Folder members (§10.5): non-code files, plus code named on member lines.

    A member line (one carrying a sha256) naming a corpus path outside the
    folder makes the declaration unparsed; paths in prose do not count."""
    assert decl.folder is not None
    prefix = decl.folder + "/"
    named = _member_line_paths(decl.folder, text, known)
    outside = sorted(p for p in named if not p.startswith(prefix))
    if outside:
        raise DeclarationError(
            f"{decl.path}: names paths outside its folder: {outside}"
        )
    return tuple(
        p
        for p in sorted(known)
        if p.startswith(prefix) and p != decl.path and (p not in nodes or p in named)
    )


def vendor_roles(
    repo: str,
    corpus: Sequence[str],
    texts: Mapping[str, str],
    role: Callable[[str], Role],
    node_paths: frozenset[str],
) -> VendorResult:
    """Members, protection and ``vendor-pin-*`` findings for one repo."""
    res = VendorResult()
    known = frozenset(corpus)
    for rel in sorted(corpus):
        text = texts.get(rel, "")
        if role(rel) is not Role.SOURCE:
            continue
        if not is_candidate(rel, text, is_node=rel in node_paths):
            continue
        res.protected.add(rel)
        try:
            decl = parse_declaration(rel, text)
            members = (
                _e_members(decl, text, known, node_paths)
                if decl.folder is not None
                else decl.members
            )
        except DeclarationError:
            res.findings.append(
                _finding(repo, "selfcheck/vendor-pin-unparsed", "high", rel, None)
            )
            res.protected |= _named_paths(rel, text, known)
            continue
        if decl.folder is not None and not members:
            res.findings.append(
                _finding(
                    repo, "selfcheck/vendor-pin-dangling", "medium", rel, decl.folder
                )
            )
            continue
        for member in members:
            if member in known:
                res.members.setdefault(member, []).append(decl)
            else:
                res.findings.append(
                    _finding(
                        repo, "selfcheck/vendor-pin-dangling", "medium", rel, member
                    )
                )
    res.broken = bool(res.findings)
    return res
