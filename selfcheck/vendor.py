"""The ``vendored-in`` role from vendoring declarations, fail-closed (spec §9.7).

Four declaration formats met in devtools (2026-09-26):

- A ``PIN`` with ``# SOURCE: <owner> @ <ref>…`` and ``<sha256>  <path>`` lines
  (paths from the repo root);
- B ``PIN`` lines ``<sha256>  <path>  <owner>@<ref>`` (paths from the PIN dir);
- C ``PINNED*`` with ``upstream: <url>``, ``commit: <ref>`` and ``<path> <sha256>``
  lines (paths from the declaration dir);
- D a header ``# VENDORED: <owner> @ <ref> — <path>`` inside the file itself.
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


@dataclass
class VendorResult:
    """Roles, protection and instrument findings of one repo."""

    members: dict[str, list[Declaration]] = field(default_factory=dict)
    protected: set[str] = field(default_factory=set)
    findings: list[Finding] = field(default_factory=list)
    broken: bool = False


def is_candidate(rel: str, text: str, *, is_node: bool) -> bool:
    """A file that claims to be a declaration (spec §9.7 «Кандидаты»)."""
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


def _parse_d(rel: str, text: str) -> Declaration | None:
    for line in text.splitlines()[:HEAD_LINES]:
        if line.startswith("# VENDORED:"):
            m = _D.match(line)
            if m is None:
                raise DeclarationError(f"{rel}: bad VENDORED header")
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


def parse_declaration(rel: str, text: str) -> Declaration:
    """Parse one candidate as format D, A, C or B; else DeclarationError."""
    decl = _parse_d(rel, text)
    if decl is not None:
        return decl
    lines = _body(text)
    return _parse_a(rel, lines) or _parse_c(rel, lines) or _parse_b(rel, lines)


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
        suggestion="почините декларацию вендоринга (формат A–D, §9.7)",
    )


def _named_paths(rel: str, text: str, corpus: frozenset[str]) -> set[str]:
    folder = posixpath.dirname(rel)
    found: set[str] = set()
    for token in text.split():
        for cand in (token, posixpath.join(folder, token)):
            norm = posixpath.normpath(cand)
            if norm in corpus:
                found.add(norm)
    return found


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
        except DeclarationError:
            res.findings.append(
                _finding(repo, "selfcheck/vendor-pin-unparsed", "high", rel, None)
            )
            res.protected |= _named_paths(rel, text, known)
            continue
        for member in decl.members:
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
