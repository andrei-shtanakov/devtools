"""The `df-trust` pair (A, H) read by SHA (installer spec §6.1, `op: trust`).

The checker is stateless: the base of the incremental check is the accepted head
A from the request (its SHA, last epoch, ``source.env`` digest and validation
version). With the same validation and the same ``source.env`` digest on H, and H
a first-parent descendant of A, only the journal version on A and the new links
A → … → H are read and checked forward from A; otherwise the whole chain is read
and checked from the bootstrap (the cold path). Everything is read by SHA, never
by the current branch head, so the same pair always gives the same findings.

Settings are mutable state observed at the moment of the call: they are checked
apart from the history (``settings_findings``), by calling the ``act_trust``
checks explicitly, per role.

Not yet in the ``act_precheck`` import closure (it joins it with Task 7).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from governance import act_trust
from governance.act_anon import contents_bytes
from governance.act_epochs import Epoch, JournalError, parse_journal
from governance.act_source import SourceV2
from governance.act_time import Unavailable
from governance.act_trust import (
    JOURNAL_FILE,
    MAX_CHAIN,
    REBASE_PREMISE,
    SUPPORTED_HOST,
    Binding,
    HistoryFault,
    TrustRoot,
    TrustSnapshot,
)
from governance.act_trust_findings import source_descriptor
from governance.act_trust_types import (
    AcceptedBase,
    Finding,
    Role,
    SourceDescriptor,
    clip_detail,
)

Relation = Literal["descendant", "not_descendant", "first"]
_Epochs = tuple[Epoch, ...] | Unavailable | HistoryFault
_Check = tuple[str, Callable[[Role], act_trust.Found | str]]

#: Closed set of unverified premises of the settings check (spec §6.1 «Свежие
#: проверки»: what the credential cannot see is unverified, not «ok»; O14: an
#: unkeyed binding is checked anonymously, with the warning).
UNVERIFIED_REBASE: dict[Role, str] = {
    "approval-policy": f"{REBASE_PREMISE}@approval-policy",
    "df-trust": f"{REBASE_PREMISE}@df-trust",
}
HISTORY_TOKEN_REQUIRED = "history_token_required"
UNVERIFIED = frozenset({*UNVERIFIED_REBASE.values(), HISTORY_TOKEN_REQUIRED})

_SHA = re.compile(r"[0-9a-f]{40}")
_ROLE_ORDER: tuple[Role, ...] = ("approval-policy", "df-trust")


@dataclass(frozen=True)
class PairResult:
    """The pair (A, H) as read by SHA.

    ``relation`` is ``first`` without a base and ``descendant`` only when proven;
    when H's objects no longer read it is ``not_descendant`` with the finding
    ``history_unverifiable`` (and no ``not_descendant`` finding). ``snapshot`` is
    ``None`` when the journal of H was not verified (a history finding says
    why). ``source`` is ``None`` when ``source.env`` on H was not read or is
    invalid (``history_unverifiable`` or ``source_env_invalid``). ``findings``
    also carry ``not_public`` of either trust repo, read with the identities.
    """

    relation: Relation
    snapshot: TrustSnapshot | None
    source: SourceDescriptor | None
    findings: tuple[Finding, ...]


@dataclass(frozen=True)
class Settings:
    """Settings findings and unverified premises (codes from ``UNVERIFIED``)."""

    findings: tuple[Finding, ...]
    unverified: tuple[str, ...]


def read_pair(
    transport: Any,
    binding: Binding,
    base: AcceptedBase | None,
    head: str,
    *,
    validation: str,
) -> PairResult | Unavailable:
    """Read the pair (A = ``base``, H = ``head``) by SHA and check H's journal.

    ``Unavailable`` — a temporary read failure or a wrong root identity (the
    observation stays unresolved). A visible ``private: true`` is the finding
    ``not_public`` and the read goes on. ``source_descriptor`` may raise
    ``UnsupportedSize`` (a too long policy name or branch).
    """
    if binding.host != SUPPORTED_HOST:
        return Unavailable(f"привязка: хост {binding.host!r} не поддерживается v1")
    if not _SHA.fullmatch(head) or (base is not None and not _SHA.fullmatch(base.sha)):
        return Unavailable("пара df-trust: A и H — полные SHA")
    repo = binding.df_trust_full_name
    seen = _visible(transport, binding.df_trust_repository_id, repo, "df-trust")
    if isinstance(seen, Unavailable):
        return seen
    findings = seen
    walked = act_trust._walk(transport, repo, head, None if base is None else base.sha)
    if isinstance(walked, Unavailable):
        return walked
    if isinstance(walked, HistoryFault):
        return PairResult(_relation(base, False), None, None, (*findings, _of(walked)))
    chain, below = walked
    descends = base is not None and chain[0] == base.sha
    relation = _relation(base, descends)
    if relation == "not_descendant" and base is not None:
        detail = (
            f"{repo}@{head[:12]}: не потомок принятой {base.sha[:12]} по first-parent"
        )
        findings = (
            *findings,
            Finding("not_descendant", "df-trust", clip_detail(detail)),
        )
    found = act_trust._source_at(transport, repo, head)
    if isinstance(found, Unavailable):
        return found
    if isinstance(found, HistoryFault):
        return PairResult(relation, None, None, (*findings, _of(found)))
    raw, source = found
    policy = _visible(
        transport, binding.approval_policy_repository_id, source.repo, "approval-policy"
    )
    if isinstance(policy, Unavailable):
        return policy
    findings = (*findings, *policy)
    descriptor = source_descriptor(source, raw)
    warm = (
        base
        if descends
        and base is not None
        and base.validation == validation
        and base.source_digest == descriptor.digest
        else None
    )
    epochs = None if warm is None else _warm(transport, repo, chain, warm, source)
    if epochs is None:
        epochs = _cold(transport, repo, chain, below if descends else None, source)
    if isinstance(epochs, Unavailable):
        return epochs
    if isinstance(epochs, HistoryFault):
        return PairResult(relation, None, descriptor, (*findings, _of(epochs)))
    root = TrustRoot(
        binding.host, binding.df_trust_repository_id, binding.df_trust_branch, head
    )
    snapshot = TrustSnapshot(root, repo, source, epochs, transport=transport)
    return PairResult(relation, snapshot, descriptor, findings)


def settings_findings(
    transport: Any, snap: TrustSnapshot, *, policy_branch: str, keyed: bool
) -> Settings | Unavailable:
    """Settings at the moment of the call (spec §6.1, «Находки настроек»), per role.

    Calls explicitly, for ``approval-policy`` and ``df-trust``: the history token
    self-report (``_history_report_of``, finding ``token_write``), the required
    rules (``_ruleset_of``, ``ruleset_missing``) and ``allow_rebase_merge``
    (``_rebase``, ``rebase_allowed``); then the ``source.env`` policy branch
    against ``policy_branch``, the default branch the publisher resolved
    (``policy_branch_mismatch``, С-2). ``keyed`` — the binding has a history key:
    without it the self-report is skipped and ``history_token_required`` is
    unverified (O14: anonymous, with the warning); with it, an unreadable
    self-report is a gap, never an anonymous fallback. A hidden
    ``allow_rebase_merge`` is unverified per role. A finding wins over another
    check's gap; with no finding a gap is ``Unavailable``. Visibility
    (``not_public``) is read with the identities, by ``read_pair``.
    """
    findings: list[Finding] = []
    unverified: list[str] = [] if keyed else [HISTORY_TOKEN_REQUIRED]
    gap: Unavailable | None = None
    for kind, check in _checks(transport, snap, keyed):
        for role in _ROLE_ORDER:
            found = check(role)
            if isinstance(found, tuple):
                findings.append(Finding(kind, found[0], clip_detail(found[1])))
            elif found == REBASE_PREMISE:
                unverified.append(UNVERIFIED_REBASE[role])
            elif isinstance(found, Unavailable) and gap is None:
                gap = found
    if snap.source.branch != policy_branch:
        detail = (
            f"{snap.source.repo}: ветка source.env {snap.source.branch!r} ≠ "
            f"default branch {policy_branch!r}"
        )
        findings.append(
            Finding("policy_branch_mismatch", "approval-policy", clip_detail(detail))
        )
    if gap is not None and not findings:
        return gap
    return Settings(tuple(findings), tuple(unverified))


def _checks(transport: Any, snap: TrustSnapshot, keyed: bool) -> tuple[_Check, ...]:
    """(finding kind, per-role check) in the order they are called."""
    checks: tuple[_Check, ...] = (
        ("ruleset_missing", lambda role: act_trust._ruleset_of(transport, snap, role)),
        ("rebase_allowed", lambda role: act_trust._rebase(transport, snap, role)),
    )
    if not keyed:
        return checks
    own: _Check = (
        "token_write",
        lambda role: act_trust._history_report_of(transport, snap, role),
    )
    return (own, *checks)


def _visible(
    transport: Any, repo_id: int, expected: str, role: Role
) -> tuple[Finding, ...] | Unavailable:
    """`act_trust._named` for the pair: the identity is checked the same way, but a
    visible ``private: true`` is the finding ``not_public`` instead of a refusal.
    A failed read (a 404 included) stays ``Unavailable``."""
    body = act_trust._get(transport, f"repositories/{repo_id}")
    if isinstance(body, Unavailable):
        return body
    if not isinstance(body, dict) or body.get("full_name") != expected:
        got = body.get("full_name") if isinstance(body, dict) else body
        return Unavailable(f"repositories/{repo_id}: {got!r}, привязка — {expected!r}")
    private = body.get("private")
    if private is True:
        detail = f"{expected}: репо не публичный (private = true)"
        return (Finding("not_public", role, clip_detail(detail)),)
    if private is False:
        return ()
    return Unavailable(f"{expected}: видимость не прочитана ({private!r})")


def _relation(base: AcceptedBase | None, descends: bool) -> Relation:
    if base is None:
        return "first"
    return "descendant" if descends else "not_descendant"


def _of(fault: HistoryFault) -> Finding:
    return Finding(fault.kind, "df-trust", clip_detail(fault.detail))


def _warm(
    transport: Any, repo: str, chain: list[str], base: AcceptedBase, source: SourceV2
) -> _Epochs | None:
    """Journal of H checked forward from the version on A, which is read once;
    ``None`` — the base does not describe A (no journal on A, or another last
    epoch): take the cold path."""
    raw = contents_bytes(transport, repo, JOURNAL_FILE, base.sha)
    if isinstance(raw, Unavailable):
        return raw
    if raw is None:
        return None
    try:
        at_a = parse_journal(raw.decode("utf-8"))
    except (JournalError, UnicodeDecodeError):
        return None
    if at_a[-1].epoch != base.last_epoch:
        return None
    versions = act_trust._journal_versions(transport, repo, chain[1:], (raw, at_a))
    if not isinstance(versions, list):
        return versions
    return act_trust._checked_epochs(versions, source, bootstrap=False)


def _cold(
    transport: Any, repo: str, chain: list[str], below: str | None, source: SourceV2
) -> _Epochs:
    """The whole first-parent chain of H, checked from the bootstrap. ``below`` —
    the first parent of a walk that stopped at A: the walk goes on from it, so
    no link is read twice."""
    if below is not None:
        rest = act_trust._walk(transport, repo, below, room=MAX_CHAIN - len(chain))
        if not isinstance(rest, tuple):
            return rest
        chain = rest[0] + chain
    versions = act_trust._journal_versions(transport, repo, chain)
    if not isinstance(versions, list):
        return versions
    return act_trust._checked_epochs(versions, source)
