"""Helpers shared by probe adapters."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from selfcheck.anchors import python_anchor
from selfcheck.model import Confidence, Finding, Location, make_text_key

if TYPE_CHECKING:
    from selfcheck.probes.base import ProbeCtx


def rel_path(ctx: ProbeCtx, raw: str) -> str:
    """Tool-reported path (absolute, or relative to the probe cwd) → copy path."""
    path = Path(raw)
    if not path.is_absolute():
        path = ctx.cwd / path
    return path.resolve().relative_to(ctx.target.copy.resolve()).as_posix()


def source_text(ctx: ProbeCtx, rel: str) -> str:
    """Text of a file in the copy ('' when unreadable)."""
    try:
        return (ctx.target.copy / rel).read_text(errors="replace")
    except OSError:
        return ""


def copy_paths(ctx: ProbeCtx) -> list[str]:
    """Inputs as absolute paths inside the copy (``files`` input form)."""
    return [str(ctx.target.copy / p) for p in ctx.inputs]


def line_finding(
    ctx: ProbeCtx,
    rule: str,
    rel: str,
    line: int,
    *,
    category: str,
    severity: str,
    confidence: Confidence = Confidence.LIKELY,
    message: str = "",
    key_text: str | None = None,
) -> Finding:
    """Finding at ``rel:line`` with anchor and text_key (spec §2.1)."""
    text = source_text(ctx, rel)
    lines = text.splitlines()
    line_text = lines[line - 1] if 0 < line <= len(lines) else ""
    anchor = python_anchor(text, rel, line) if rel.endswith(".py") else f"file:{rel}"
    evidence = [{"kind": "message", "detail": message}] if message else []
    return Finding(
        rule=rule,
        category=category,
        severity=severity,
        confidence=confidence,
        owner_repo=ctx.target.name,
        anchor=anchor,
        locations=[Location(rel, line)],
        text_key=make_text_key(key_text if key_text is not None else line_text),
        group=anchor,
        evidence=evidence,
    )


def config_hash(copy: Path, names: tuple[str, ...]) -> str:
    """sha1 over the probe's config files present in the copy."""
    digest = hashlib.sha1()
    for name in names:
        path = copy / name
        if path.is_file():
            digest.update(name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()
