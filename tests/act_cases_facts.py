"""Фабрика контекста проверки доставки (Task 12); импортирует steward.

Отдельно от `tests/act_cases.py` (контракт импорта без governance); имя с
префиксом `tests/act_cases` — та же защищённая цепочка (authority-root и
`_HARNESS_PREFIXES`).
"""

from collections.abc import Callable
from datetime import datetime

from steward.approvalfacts import v3

from governance.act_check import ActivationView, DeliveryContext
from governance.act_decision import DeliveryView
from governance.act_epochs import Epoch
from governance.act_policy import PolicySet, parse_policy_set
from governance.act_request import NodeEntry, NodeRequest, PolicyPin
from governance.act_source import SourceV2
from governance.act_time import Unavailable, parse_ts
from tests.act_cases import FILES

PRODUCT = "owner/product-b"
HEAD = "1" * 40
PATH = "workstreams/ws-7/requirements.md"
DIGEST = "h" * 40
SOURCE = SourceV2("owner/approval-policy", "main", f"{'p1':0<40}", frozenset({"owner"}))


def ts(hms: str) -> str:
    return f"2026-10-12T{hms}Z"


def sha(name: str) -> str:
    return f"{name:0<40}"


def act(name: str, hms: str) -> ActivationView:
    return ActivationView(("activation", sha(name)), sha(name), parse_ts(ts(hms)))


def review(
    rid: str,
    login: str,
    hms: str | None,
    *,
    state: str = "APPROVED",
    commit: str = HEAD,
) -> v3.Review:
    """Ревью в форме steward: логин бота без `[bot]` (О9)."""
    hint = "Bot" if login.startswith("df-") else "User"
    return v3.Review(rid, login, hint, state, commit, ts(hms) if hms else None)


def policy_files(mode: str | None, approvers: tuple[str, ...]) -> dict[str, bytes]:
    files = dict(FILES)
    modes = f"  {PRODUCT}: {mode}\n" if mode else "  owner/other: merge\n"
    files["policy/modes.yaml"] = (
        b"schema: approval-policy-modes/v1\nmodes:\n" + modes.encode()
    )
    humans = ", ".join(f"github:{a}" for a in approvers)
    files["policy/approvers.env"] = (
        f"AUTHORIZED_APPROVER_ACCOUNTS={','.join(approvers)}\n"
        "AUTHORIZED_MERGER_ACCOUNTS=df-reviewer[bot]\n"
    ).encode()
    files["policy/actors.yaml"] = (
        f"version: 1\nhuman_identities: [{humans}]\n"
        "agent_identities: [github:df-reviewer, github:ai-prosto]\n"
    ).encode()
    return files


def context(
    *,
    ranges: dict[int, tuple[ActivationView, ...] | Unavailable] | None = None,
    modes: dict[str, str | None] | None = None,
    pin: str = "p1",
    approve_at: str = "09:50:00",
    approve_commit: str = HEAD,
    reviews: tuple[v3.Review, ...] | None = None,
    dismissals: tuple[tuple[str, str], ...] = (),
    approvers: tuple[str, ...] = ("alice",),
    merged: bool = True,
    merged_at: str = "10:00:00",
    request_act: str | None = "review/v1",
    request_product: str = PRODUCT,
    epochs: tuple[Epoch, ...] = (Epoch(1, sha("p1")),),
    anchor_at: dict[str, datetime | Unavailable] | None = None,
    unreadable_sets: frozenset[str] = frozenset(),
    pin_digests: dict[str, str] | None = None,
    merger: tuple[str, str] = ("df-reviewer", "Bot"),
    blobs: dict[str, tuple[str | None, str]] | None = None,
    self_hash: str = DIGEST,
    legacy: Callable[[], object] | None = None,
) -> DeliveryContext:
    ranges = ranges or {1: (act("p1", "08:00:00"),)}
    activations = [
        a for span in ranges.values() if isinstance(span, tuple) for a in span
    ]
    modes = modes or {a.merge_commit: "review" for a in activations}
    sets: dict[str, PolicySet | Unavailable] = {
        a.merge_commit: (
            Unavailable("набор не прочитан")
            if a.merge_commit in unreadable_sets
            else parse_policy_set(
                a.merge_commit, policy_files(modes[a.merge_commit], approvers)
            )
        )
        for a in activations
    }
    pinned = sets.get(sha(pin))
    digests = pinned.files_sha256 if isinstance(pinned, PolicySet) else {}
    request = NodeRequest(
        "approve-2-1-1",
        "ws-7",
        request_product,
        {"requirements": NodeEntry(PATH, DIGEST, 1)},
        {"requirements": {}},
        PolicyPin(SOURCE.repo, SOURCE.branch, sha(pin), pin_digests or digests),
    )
    dismissed = {rid for rid, _ in dismissals}
    if reviews is None:
        state = "DISMISSED" if "11" in dismissed else "APPROVED"
        reviews = (
            review("11", "alice", approve_at, state=state, commit=approve_commit),
        )
    facts = v3.ReviewFacts(
        PRODUCT,
        7,
        HEAD,
        "main",
        "MERGED" if merged else "OPEN",
        reviews,
        tuple(v3.Dismissal(rid, "carol", ts(at), "APPROVED") for rid, at in dismissals),
    )
    delivery = (
        DeliveryView(
            True,
            "m" * 40,
            ts(merged_at),
            merger[0],
            merger[1],
            HEAD,
            True,
            blobs or {PATH: ("b" * 40, "b" * 40)},
        )
        if merged
        else DeliveryView(False, None, None, None, None, None, False, {})
    )
    return DeliveryContext(
        source=SOURCE,
        product=PRODUCT,
        request=request,
        request_act=request_act,
        head_sha=HEAD,
        delivery=delivery,
        facts=facts,
        head_self_hashes={PATH: self_hash},
        epochs=epochs,
        anchor_at=anchor_at or {sha("p1"): parse_ts(ts("08:00:00"))},
        ranges=ranges,
        policy_sets=sets,
        legacy=legacy,
    )
