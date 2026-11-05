"""Core of the candidate `review/v1` premerge check (spec §8.2, §9.1).

Split out of `act_premerge` (df stage 3a, Task 7, review D1): `act_premerge` stays
the file `merge-pr.sh` runs (its CLI, `real_deps`, the App mode with
`act_app_installations` → `act_app_token` → `jwt`); this module is the check
itself — `PremergeDeps`, `decide`, `_check`, `_trust` — which the precheck program
of df (protocol v2, `act_precheck_v2`) imports without the CLI. So the AST closure
of `act_precheck` (`df/precheck_tree.toml`) holds this core and never `jwt`, and
both chains are visible to their tracers statically.

Exit codes: 0 — merge allowed (the second boundary held), 3 — refused, 2 — not
established. The import chain: only protected `governance/act_*`, `facts`,
`ssot_env`; `node_approval` and `stale_adapter` stay out (no `act_projection`).
v2 passes the merger of its request (N4), the derived-credential mode (N3), the
pinned P and the D16 surface (`surface`).
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from governance import act_facts, act_trust, ssot_env
from governance.act_anon import contents_bytes
from governance.act_check import ActivationView, live_policy_refusal
from governance.act_decision import find_act, merger_allowed
from governance.act_identity import RequestIdentity, manifest_refusal
from governance.act_policy import PolicySet
from governance.act_request import NodeRequest, RequestError, parse_manifest
from governance.act_time import Clock, Unavailable, parse_ts

EXIT_OK, EXIT_UNSET, EXIT_REFUSED = 0, 2, 3
#: Мерж — не раньше 2 с после последнего события решения (§8.2).
GAP = timedelta(seconds=2)
#: Дольше ждать нельзя: такая граница значит, что часы расходятся.
MAX_WAIT = 10.0
PATTERNS = (
    Path(__file__).resolve().parent.parent
    / "contracts/approval-branches/v1/patterns.env"
)
REGISTERED = " — событие записано в журнал периметра; мерж не выполнен"

Installations = Callable[[act_trust.Binding], "act_trust.Found"]
"""Самоотчёт установок App у держателя ключа (Task 15a); `None` — режим профиля."""
Surface = Callable[[RequestIdentity, NodeRequest], "str | None"]
"""The D16 surface of the whole PR (df precheck v2): a refusal reason or `None`."""


class _Stop(Exception):
    def __init__(self, code: int, text: str) -> None:
        super().__init__(text)
        self.code = code


@dataclass(frozen=True)
class PremergeDeps:
    """Внешние чтения предпроверки; тесты подменяют целиком."""

    readers: act_trust.Readers
    gh: Any
    binding: Any  # Binding | None | Unavailable
    journal: Any  # act_trust.Journal | Unavailable — по привязке
    now: Callable[[], datetime]
    sleep: Callable[[float], None]
    creds: tuple[act_trust.Credential, ...] | act_trust.KeyHolderCovered
    installations: Installations | None = None
    #: The history token by the df launch contract (the approved `gh`, §3.10).
    token_source: Callable[[Path], str | Unavailable] | None = None
    #: The pinned head P of `approval-policy` (df precheck v2, spec B §6.1).
    policy_head: str | None = None
    #: The trust snapshot reader (df precheck v2: the pair by SHA).
    snapshot: act_trust.SnapshotReader | None = None
    surface: Surface | None = None
    #: Observer of the `Trust` the check decided on (df precheck v2: its
    #: unverified premises go into the verdict).
    on_trust: Callable[[act_trust.Trust], None] | None = None


def request_of_branch(head_ref: str) -> tuple[str, str] | None:
    """`(ws_id, request_key)` по форме candidate из SSOT `patterns.env`;
    finalize и иные формы — `None`. Битый SSOT — `RuntimeError`."""
    template = ssot_env.read_key(
        PATTERNS, "APPROVAL_CANDIDATE_TEMPLATE", "patterns.env"
    )
    pattern = re.escape(template)  # полное совпадение: finalize (`…-final`) не подходит
    for name, group in (
        ("ws_id", r"(?P<ws_id>.+?)"),
        ("wave", r"(?P<wave>\d+)"),
        ("step", r"(?P<step>\d+)"),
        ("attempt", r"(?P<attempt>\d+)"),
    ):
        pattern = pattern.replace(re.escape("{" + name + "}"), group)
    match = re.fullmatch(pattern, head_ref)
    if match is None:
        return None
    key = f"approve-{match['wave']}-{match['step']}-{match['attempt']}"
    return match["ws_id"], key


def _manifest_text(
    deps: PremergeDeps, repo: str, head: str, path: str
) -> str | None | Unavailable:
    raw = contents_bytes(deps.gh, repo, path, head)
    if not isinstance(raw, bytes):
        return raw
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return Unavailable(f"{path}: не UTF-8")


Live = tuple[ActivationView, PolicySet, Sequence[ActivationView]]


def _live(deps: PremergeDeps, trust: act_trust.Trust, product: str) -> Live:
    """Текущая активация, её набор и диапазон открытой эпохи; режим продукта —
    по ПОЛНОЙ истории (B1-5)."""
    now = deps.now()
    anchors = act_trust.load_anchor_times(trust, now)  # транспорт — из `trust`
    loaded = act_trust.load_ranges(trust, anchors, now, deps.journal)
    if isinstance(loaded, Unavailable):
        raise _Stop(EXIT_UNSET, f"история источника политики: {loaded.reason}")
    ranges, sets = loaded
    mode = act_trust.product_mode(trust, ranges, sets, product, now)
    if isinstance(mode, Unavailable):
        raise _Stop(EXIT_UNSET, f"режим продукта: {mode.reason}")
    if mode != "review":
        raise _Stop(EXIT_REFUSED, f"ACT_MODE[{product}] = {mode} — не review")
    current = act_trust.current_activation(trust, ranges)
    if isinstance(current, Unavailable):
        raise _Stop(EXIT_UNSET, f"текущая активация: {current.reason}")
    policy = sets.get(current.merge_commit, Unavailable("набор не прочитан"))
    if isinstance(policy, Unavailable):
        raise _Stop(EXIT_UNSET, f"набор текущей активации: {policy.reason}")
    # `current_activation` уже прочитал этот диапазон — он есть и он кортеж.
    span = ranges[trust.epochs[-1].epoch]
    assert not isinstance(span, Unavailable)
    return current, policy, span


def _boundary(facts: Any, span: Sequence[ActivationView]) -> datetime:
    """Последнее событие, способное повлиять на решение (§8.2)."""
    try:
        stamps = [a.at for a in span]
        stamps += [parse_ts(r.submitted_at) for r in facts.reviews if r.submitted_at]
        stamps += [parse_ts(d.created_at) for d in facts.dismissals]
    except ValueError as exc:
        raise _Stop(EXIT_UNSET, f"метка события не разобрана: {exc}") from exc
    return max(stamps)


def _trust(deps: PremergeDeps) -> act_trust.Trust:
    """Привязка, журнал, проверки периметра; App — самоотчёт установок (B1-11)."""
    if deps.binding is None:
        raise _Stop(
            EXIT_REFUSED, "заявка review/v1 без корня доверия (нет /etc/df/trust.toml)"
        )
    if isinstance(deps.binding, Unavailable):
        raise _Stop(EXIT_UNSET, deps.binding.reason)
    if isinstance(deps.journal, Unavailable):
        raise _Stop(EXIT_UNSET, f"журнал периметра: {deps.journal.reason}")
    trust = act_trust.load_trust(
        deps.readers,
        deps.binding,
        deps.journal,
        creds=deps.creds,
        token_source=deps.token_source,
        snapshot=deps.snapshot,
        policy_head=deps.policy_head,
    )
    if isinstance(trust, Unavailable):
        raise _Stop(EXIT_UNSET, trust.reason)
    if deps.installations is not None:
        found = deps.installations(deps.binding)
        if isinstance(found, Unavailable):
            raise _Stop(EXIT_UNSET, f"установки App: {found.reason}")
        if found is not None:
            failed = deps.journal.record(trust.snapshot, found[0], found[1])
            tail = REGISTERED if failed is None else f"; регистрация: {failed.reason}"
            raise _Stop(EXIT_UNSET, f"нарушение периметра: {found[1]}{tail}")
    for name in trust.unverified:
        print(f"unverified premise: {name}", file=sys.stderr)
    if deps.on_trust is not None:
        deps.on_trust(trust)
    return trust


def _check(args: argparse.Namespace, deps: PremergeDeps) -> None:
    try:
        branch = request_of_branch(args.head_ref)
    except RuntimeError as exc:
        raise _Stop(EXIT_UNSET, str(exc)) from exc
    if branch is None:
        raise _Stop(EXIT_REFUSED, f"ветка {args.head_ref!r} — не форма candidate")
    ident = RequestIdentity(args.repo, *branch)
    trust = _trust(deps)
    current, policy, span = _live(deps, trust, args.repo)
    text = _manifest_text(deps, args.repo, args.head, ident.manifest_path)
    if isinstance(text, Unavailable):
        raise _Stop(EXIT_UNSET, f"манифест: {text.reason}")
    if text is None:
        raise _Stop(
            EXIT_REFUSED, f"в {args.head[:12]} нет манифеста {ident.request_id}"
        )
    try:
        req = parse_manifest(text)
    except RequestError as exc:
        raise _Stop(EXIT_REFUSED, f"манифест: {exc}") from exc
    refusal = manifest_refusal(req, ident) or live_policy_refusal(
        req.policy, current.merge_commit
    )
    if refusal is not None:
        raise _Stop(EXIT_REFUSED, refusal)
    off_surface = None if deps.surface is None else deps.surface(ident, req)
    if off_surface is not None:
        raise _Stop(EXIT_REFUSED, f"surface_violation: {off_surface}")
    if not merger_allowed(policy, args.merger, args.merger_type):
        raise _Stop(EXIT_REFUSED, f"мержер {args.merger} не пройдёт D3")
    facts = _act(args, deps, policy)
    wait = (_boundary(facts, span) + GAP - deps.now()).total_seconds()
    if wait > MAX_WAIT:
        raise _Stop(EXIT_UNSET, f"граница секунды через {wait:.0f} с — часы расходятся")
    if wait > 0:
        deps.sleep(wait)
    again = act_facts.open_review(deps.gh, args.repo, args.pr)
    current2, _policy2, _span2 = _live(deps, trust, args.repo)
    if again != facts or current2 != current:
        raise _Stop(
            EXIT_UNSET, "ревью или текущая активация изменились за время ожидания"
        )


def _act(args: argparse.Namespace, deps: PremergeDeps, policy: PolicySet) -> Any:
    """D1 на `--head`: факты ревью названы на этой голове и акт на ней есть."""
    facts = act_facts.open_review(deps.gh, args.repo, args.pr)
    if isinstance(facts, Unavailable):
        raise _Stop(EXIT_UNSET, f"ревью PR #{args.pr}: {facts.reason}")
    if facts.head_sha != args.head:
        raise _Stop(
            EXIT_REFUSED, f"голова PR {facts.head_sha[:12]} ≠ --head {args.head[:12]}"
        )
    now = deps.now()
    effective = act_facts.effective(facts, Clock(now))
    if isinstance(effective, Unavailable):
        raise _Stop(EXIT_UNSET, effective.reason)
    if find_act(args.head, effective, policy) is None:
        if _approve_in_current_second(facts, args.head, now):
            raise _Stop(
                EXIT_UNSET,
                f"approve на {args.head[:12]} — в текущей секунде: на срезе T он "
                "ещё не предшествует T (§8.2); повтор уместен",
            )
        raise _Stop(EXIT_REFUSED, f"нет approve человека на {args.head[:12]}")
    return facts


def _approve_in_current_second(facts: Any, head: str, now: datetime) -> bool:
    """APPROVED на `head` с `submitted_at` (до секунды) ≥ T: срез T его не видит
    (равная секунда предшествует T только по `before`), но через секунду увидит —
    это «не установлено», а не отказ (ревью PR #673)."""
    for r in facts.reviews:
        if r.state != "APPROVED" or r.commit_id != head or not r.submitted_at:
            continue
        try:
            if parse_ts(r.submitted_at) >= now:
                return True
        except ValueError:
            return True  # метку не разобрать — тоже не установлено
    return False


def decide(args: argparse.Namespace, deps: PremergeDeps) -> tuple[int, str]:
    """The check without printing: `(EXIT_OK, "")` or the exit code and reason
    (`main`; the df precheck v2 maps 3 to `refuse`, 2 to `unset`)."""
    try:
        _check(args, deps)
    except _Stop as stop:
        return stop.code, str(stop)
    return EXIT_OK, ""
