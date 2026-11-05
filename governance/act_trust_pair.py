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
checks explicitly.

Not yet in the ``act_precheck`` import closure (it joins it with Task 7).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from governance import act_trust
from governance.act_epochs import Epoch
from governance.act_source import SourceV2
from governance.act_time import Unavailable
from governance.act_trust import (
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

_SHA = re.compile(r"[0-9a-f]{40}")
_ROLES: dict[str, Role] = {"approval-policy": "approval-policy", "df-trust": "df-trust"}


@dataclass(frozen=True)
class PairResult:
    """The pair (A, H) as read by SHA.

    ``relation`` is ``first`` without a base and ``descendant`` only when proven;
    when H's objects no longer read it is ``not_descendant`` with the single
    finding ``history_unverifiable``. ``snapshot`` is ``None`` when the journal of
    H was not verified (a history finding says why). ``source`` is ``None`` when
    ``source.env`` on H was not read or is invalid (``history_unverifiable`` or
    ``source_env_invalid``).
    """

    relation: Relation
    snapshot: TrustSnapshot | None
    source: SourceDescriptor | None
    findings: tuple[Finding, ...]


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
    observation stays unresolved). ``source_descriptor`` may raise
    ``UnsupportedSize`` (a too long policy name or branch).
    """
    if binding.host != SUPPORTED_HOST:
        return Unavailable(f"привязка: хост {binding.host!r} не поддерживается v1")
    if not _SHA.fullmatch(head) or (base is not None and not _SHA.fullmatch(base.sha)):
        return Unavailable("пара df-trust: A и H — полные SHA")
    repo = act_trust._named(
        transport, binding.df_trust_repository_id, binding.df_trust_full_name
    )
    if isinstance(repo, Unavailable):
        return repo
    chain = act_trust._chain(transport, repo, head, None if base is None else base.sha)
    if isinstance(chain, Unavailable):
        return chain
    if isinstance(chain, HistoryFault):
        return PairResult(_relation(base, False), None, None, (_finding(chain),))
    descends = base is not None and chain[0] == base.sha
    relation = _relation(base, descends)
    findings: tuple[Finding, ...] = ()
    if relation == "not_descendant" and base is not None:
        detail = (
            f"{repo}@{head[:12]}: не потомок принятой {base.sha[:12]} по first-parent"
        )
        findings = (Finding("not_descendant", "df-trust", clip_detail(detail)),)
    found = act_trust._source_at(transport, binding, repo, head)
    if isinstance(found, Unavailable):
        return found
    if isinstance(found, HistoryFault):
        return PairResult(relation, None, None, (*findings, _finding(found)))
    raw, source = found
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
        epochs = _cold(transport, repo, head, chain, descends, source)
    if isinstance(epochs, Unavailable):
        return epochs
    if isinstance(epochs, HistoryFault):
        return PairResult(relation, None, descriptor, (*findings, _finding(epochs)))
    root = TrustRoot(
        binding.host, binding.df_trust_repository_id, binding.df_trust_branch, head
    )
    snapshot = TrustSnapshot(root, repo, source, epochs, transport=transport)
    return PairResult(relation, snapshot, descriptor, findings)


def settings_findings(
    transport: Any, snap: TrustSnapshot, *, policy_branch: str
) -> tuple[Finding, ...] | Unavailable:
    """Settings findings at the moment of the call (spec §6.1, «Находки настроек»).

    Calls explicitly: the history token self-report (``_history_self_report``;
    without a history token it cannot read ``permissions`` and the call is
    ``Unavailable``), the rulesets of both trust repos (``_rulesets``),
    ``allow_rebase_merge`` of ``approval-policy`` and of ``df-trust``
    (``_rebase``), and the ``source.env`` policy branch against ``policy_branch``,
    the default branch the publisher resolved (С-2). A hidden
    ``allow_rebase_merge`` is an unverified premise, not a finding. A finding wins
    over another check's unavailability; otherwise that is ``Unavailable``.
    """
    checks = (
        ("token_write", lambda: act_trust._history_self_report(transport, snap)),
        ("ruleset_missing", lambda: act_trust._rulesets(transport, snap)),
        ("rebase_allowed", lambda: act_trust._rebase(transport, snap)),
        ("rebase_allowed", lambda: act_trust._rebase(transport, snap, "df-trust")),
    )
    findings: list[Finding] = []
    gap: Unavailable | None = None
    for kind, check in checks:
        found = check()
        if isinstance(found, tuple):
            role, detail = found
            findings.append(Finding(kind, _ROLES[role], clip_detail(detail)))
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
    if findings or gap is None:
        return tuple(findings)
    return gap


def _relation(base: AcceptedBase | None, descends: bool) -> Relation:
    if base is None:
        return "first"
    return "descendant" if descends else "not_descendant"


def _finding(fault: HistoryFault) -> Finding:
    return Finding(fault.kind, "df-trust", clip_detail(fault.detail))


def _warm(
    transport: Any,
    repo: str,
    chain: list[str],
    base: AcceptedBase,
    source: SourceV2,
) -> _Epochs | None:
    """Journal of H checked forward from the version on A; ``None`` — the base does
    not describe A (no journal on A, or another last epoch): take the cold path."""
    at_a = act_trust._journal_versions(transport, repo, chain[:1])
    if isinstance(at_a, Unavailable):
        return at_a
    if isinstance(at_a, HistoryFault) or len(at_a) != 1:
        return None
    if at_a[0][-1].epoch != base.last_epoch:
        return None
    versions = act_trust._journal_versions(transport, repo, chain)
    if not isinstance(versions, list):
        return versions
    return act_trust._checked_epochs(versions, source, bootstrap=False)


def _cold(
    transport: Any,
    repo: str,
    head: str,
    chain: list[str],
    descends: bool,
    source: SourceV2,
) -> _Epochs:
    """The whole first-parent chain of H, checked from the bootstrap."""
    if descends:
        full = act_trust._chain(transport, repo, head)
        if not isinstance(full, list):
            return full
        chain = full
    versions = act_trust._journal_versions(transport, repo, chain)
    if not isinstance(versions, list):
        return versions
    return act_trust._checked_epochs(versions, source)
