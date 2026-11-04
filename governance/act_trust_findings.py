"""Trust descriptors and the bounded verdict (installer spec §6.1, «Размер вердикта»).

The mandatory envelope is bounded by construction: the ranges descriptor carries
at most 64 epochs, the ``source.env`` descriptor carries the count and digest of
``POLICY_ADMINS`` instead of the list. Codes and mandatory fields are never
truncated; only the diagnostics are cut by bytes to the remaining budget.

Stdlib only: the module joins the ``act_precheck`` import closure (Task 7).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Protocol

from governance.act_source import SourceV2
from governance.act_trust_types import (
    MAX_DETAIL_BYTES,
    MAX_EPOCHS,
    ROUTE_KINDS,
    Finding,
    RangeDescriptor,
    SourceDescriptor,
)

VERDICT_LIMIT = 32 * 1024
MAX_FULL_NAME_BYTES = 140
MAX_BRANCH_BYTES = 255
_RESERVED = ("findings", "diagnostics")


class UnsupportedSize(Exception):
    """Volume outside the supported one: fixed-size ``unsupported_size: <field>``."""

    def __init__(self, field: str) -> None:
        super().__init__(f"unsupported_size: {field}")
        self.field = field


class EpochLike(Protocol):
    """An epoch of the journal on X (``act_epochs.Epoch``)."""

    @property
    def epoch(self) -> int: ...

    @property
    def anchor(self) -> str: ...

    @property
    def last_confirmed(self) -> str | None: ...


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_plain,
    ).encode("utf-8")


def _plain(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    raise TypeError(f"not serialisable: {type(value).__name__}")


def range_descriptor(epochs: Sequence[EpochLike], p_sha: str) -> RangeDescriptor:
    """Expected ranges: closed ``anchor…last_confirmed``, open ``anchor…P``."""
    if len(epochs) > MAX_EPOCHS:
        raise UnsupportedSize("epochs")
    ranges = tuple(
        (e.epoch, e.anchor, e.last_confirmed if e.last_confirmed is not None else p_sha)
        for e in epochs
    )
    digest = hashlib.sha256(_canonical([list(r) for r in ranges])).hexdigest()
    return RangeDescriptor(ranges, digest)


def source_descriptor(source: SourceV2, raw: bytes) -> SourceDescriptor:
    """Compact ``source.env``: digest of ``raw``, coordinates, admins count+digest."""
    if len(source.repo.encode("utf-8")) > MAX_FULL_NAME_BYTES:
        raise UnsupportedSize("policy_full_name")
    if len(source.branch.encode("utf-8")) > MAX_BRANCH_BYTES:
        raise UnsupportedSize("policy_branch")
    admins = "\n".join(sorted(source.policy_admins)).encode("utf-8")
    return SourceDescriptor(
        digest=hashlib.sha256(raw).hexdigest(),
        policy_full_name=source.repo,
        policy_branch=source.branch,
        bootstrap_activation=source.bootstrap_activation,
        admins_count=len(source.policy_admins),
        admins_digest=hashlib.sha256(admins).hexdigest(),
    )


def clip_detail(text: str) -> str:
    """A finding detail cut to 512 bytes of UTF-8 on a character boundary."""
    return text.encode("utf-8")[:MAX_DETAIL_BYTES].decode("utf-8", "ignore")


def fit_verdict(
    envelope: Mapping[str, object], findings: Iterable[Finding], diagnostics: str
) -> bytes:
    """Serialised verdict ≤ 32 KiB; only ``diagnostics`` is cut to the remainder.

    A finding outside the kinds of ``envelope["origin_route"]``, a second
    finding of one (kind, role) or an envelope that leaves no budget is a
    caller error (``ValueError``): the envelope is bounded by construction.
    """
    route = envelope.get("origin_route")
    if route not in ROUTE_KINDS:
        raise ValueError(f"unknown origin_route: {route!r}")
    if any(key in envelope for key in _RESERVED):
        raise ValueError(f"envelope carries reserved keys {_RESERVED}")
    rows = _finding_rows(findings, ROUTE_KINDS[route])
    doc = {**envelope, "findings": rows, "diagnostics": ""}
    if len(_canonical(doc)) > VERDICT_LIMIT:
        raise ValueError(f"envelope over {VERDICT_LIMIT} bytes")
    return _canonical({**doc, "diagnostics": _fit_text(doc, diagnostics)})


def _finding_rows(
    findings: Iterable[Finding], allowed: frozenset[str]
) -> list[dict[str, str]]:
    rows: dict[tuple[str, str], dict[str, str]] = {}
    for f in findings:
        if f.kind not in allowed:
            raise ValueError(f"finding {f.kind!r} outside route kinds")
        if (f.kind, f.role) in rows:
            raise ValueError(f"duplicate finding ({f.kind}, {f.role})")
        rows[(f.kind, f.role)] = dataclasses.asdict(f)
    return [rows[key] for key in sorted(rows)]


def _fit_text(doc: Mapping[str, object], text: str) -> str:
    """The longest prefix of ``text`` that keeps ``doc`` within the budget."""
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(_canonical({**doc, "diagnostics": text[:mid]})) <= VERDICT_LIMIT:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo]
