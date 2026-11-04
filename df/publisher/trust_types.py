"""Shared trust types for the publisher (installer spec §6.1).

Stdlib only. Mirror of ``governance/act_trust_types.py`` (``df`` never imports
``governance``); ``tests/test_act_trust_types.py`` keeps the two equal.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

Role = Literal["approval-policy", "df-trust"]
Route = Literal["perimeter-status", "trust-status", "merge"]

MAX_DETAIL_BYTES = 512
MAX_EPOCHS = 64

_BOTH: frozenset[Role] = frozenset({"approval-policy", "df-trust"})
_TRUST: frozenset[Role] = frozenset({"df-trust"})
_POLICY: frozenset[Role] = frozenset({"approval-policy"})

# Closed list of finding kinds with the roles each may carry (spec §6.1):
# 12 kinds, 16 (kind, role) combinations.
KINDS: Mapping[str, frozenset[Role]] = MappingProxyType(
    {
        "not_descendant": _TRUST,
        "journal_transition": _TRUST,
        "journal_parse": _TRUST,
        "bootstrap_mismatch": _TRUST,
        "source_env_invalid": _TRUST,
        "history_unverifiable": _TRUST,
        "policy_history_break": _POLICY,
        "ruleset_missing": _BOTH,
        "rebase_allowed": _BOTH,
        "not_public": _BOTH,
        "token_write": _BOTH,
        "policy_branch_mismatch": _POLICY,
    }
)

_TRUST_KINDS = frozenset(KINDS) - {"policy_history_break"}

# Admissible kinds by source route (spec §6.1): perimeter-status and
# trust-status read the df-trust history and settings; merge adds the
# policy-source history.
ROUTE_KINDS: Mapping[Route, frozenset[str]] = MappingProxyType(
    {
        "perimeter-status": _TRUST_KINDS,
        "trust-status": _TRUST_KINDS,
        "merge": frozenset(KINDS),
    }
)


def detail_size(text: str) -> int:
    """Serialised size of a detail, in bytes, without the surrounding quotes.

    Measured on ``json.dumps(text, ensure_ascii=False)`` encoded as UTF-8, the
    form the verdict is serialised in: C0 control characters grow to six bytes
    (``\\uXXXX``). Escaping never shrinks text, so this also bounds the raw
    UTF-8 size.
    """
    return len(json.dumps(text, ensure_ascii=False).encode("utf-8")) - 2


def clip_detail(text: str) -> str:
    """Longest code-point prefix of ``text`` with ``detail_size`` within the limit.

    Cuts on code points, so neither a character nor an escape is split.
    Producers clip first; ``Finding`` still refuses an over-long detail.
    """
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if detail_size(text[:mid]) <= MAX_DETAIL_BYTES:
            low = mid
        else:
            high = mid - 1
    return text[:low]


@dataclass(frozen=True)
class Finding:
    """One finding: a kind from KINDS, the repo role, a short detail.

    The detail limit (``detail_size``) is an invariant (``ValueError``); use ``clip_detail``
    before constructing.
    """

    kind: str
    role: Role
    detail: str

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown finding kind: {self.kind!r}")
        if self.role not in KINDS[self.kind]:
            raise ValueError(f"kind {self.kind!r} not allowed for role {self.role!r}")
        if detail_size(self.detail) > MAX_DETAIL_BYTES:
            raise ValueError(f"detail over {MAX_DETAIL_BYTES} bytes")


@dataclass(frozen=True)
class AcceptedBase:
    """The accepted head A of the trust history."""

    sha: str
    last_epoch: int
    source_digest: str
    validation: str


@dataclass(frozen=True)
class PolicyPin:
    """Pinned policy-source coordinates: full name, branch, head P."""

    full_name: str
    branch: str
    p_sha: str


@dataclass(frozen=True)
class RangeDescriptor:
    """Compact descriptor of the expected policy ranges (at most 64 epochs)."""

    ranges: tuple[tuple[int, str, str], ...]
    digest: str

    def __post_init__(self) -> None:
        if len(self.ranges) > MAX_EPOCHS:
            raise ValueError(f"more than {MAX_EPOCHS} epochs")


@dataclass(frozen=True)
class SourceDescriptor:
    """Compact descriptor of ``source.env``; the admins list is not carried."""

    digest: str
    policy_full_name: str
    policy_branch: str
    bootstrap_activation: str
    admins_count: int
    admins_digest: str
