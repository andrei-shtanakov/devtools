"""The publisher's trust state: observations, findings, accepted heads,
invariant I and meta-invariant K (spec 6.1, rev 11; stage 3a, Task 8)."""

from __future__ import annotations

import ast
import multiprocessing as mp
import os
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from df.paths import Layout
from df.publisher import perimeter, trust_state
from df.publisher.perimeter import Root, Unavailable
from df.publisher.trust_state import Observation
from df.publisher.trust_types import Finding, PolicyPin, Route
from df.stage2_config import TrustBinding

ROOT: Root = ("github.com", 5, "main")
R2: Root = ("github.com", 9, "main")
BINDING = TrustBinding("github.com", 5, "o/df-trust", "main", 6, "public")
BINDING_R2 = TrustBinding("github.com", 9, "o/df-trust-2", "main", 6, "public")
ADMIN, RULES = "admin-1", "rules-1"
H1, H2, H3, X = "1" * 40, "2" * 40, "3" * 40, "9" * 40
P1 = PolicyPin("o/approval-policy", "main", "a" * 40)
P2 = PolicyPin("o/approval-policy", "main", "b" * 40)
RULESET = Finding("ruleset_missing", "df-trust", "no ruleset")
NOT_DESCENDANT = Finding("not_descendant", "df-trust", "H1 is not after H2")
POLICY_BREAK = Finding("policy_history_break", "approval-policy", "rewritten")


@pytest.fixture()
def lay(tmp_path: Path) -> Layout:
    layout = Layout(Path(os.path.realpath(tmp_path)) / "root")
    layout.perimeter_dir.mkdir(parents=True, mode=0o700)
    assert perimeter.create_db(layout) == "created"
    return layout


def observe(
    db: perimeter.Db,
    *,
    route: Route = "trust-status",
    accepted: str | None = None,
    head: str = H1,
    policy: PolicyPin | None = None,
    binding: TrustBinding = BINDING,
    admin: str = ADMIN,
    rules: str = RULES,
) -> Observation:
    ctx = trust_state.ctx_id(route, binding, admin, rules, accepted, head, policy)
    return trust_state.open_observation(
        db,
        binding.root_id,
        ctx,
        route=route,
        binding=binding,
        admin_digest=admin,
        rules=rules,
        accepted=accepted,
        head=head,
        policy=policy,
    )


def accept(
    lay: Layout, head: str, epoch: int, *, binding: TrustBinding = BINDING
) -> None:
    """A clean `trust-status` pair (A, head) accepted at `epoch`."""
    with perimeter.open_db(lay) as db:
        root = binding.root_id
        found = trust_state.accepted_head(db.conn, root)
        obs = observe(
            db, accepted=None if found is None else found.sha, head=head,
            binding=binding,
        )  # fmt: skip
        out = trust_state.advance_head(
            db, root, obs.id, lambda: head, last_epoch=epoch, source_digest="env"
        )
        assert out == "accepted"


def PENDING(_conn: sqlite3.Connection, _root: Root) -> int | None:  # noqa: N802
    """No verified current snapshot: the registration rule's `pending`."""
    return None


def epochs_at(head: str) -> trust_state.EpochOf:
    return trust_state.current_epoch(lambda: head, BINDING, ADMIN, RULES)


def outcome(lay: Layout, obs: int) -> str:
    conn = sqlite3.connect(lay.perimeter_dir / perimeter.DB_NAME)
    try:
        row = conn.execute(
            "SELECT outcome FROM trust_observations WHERE observation_id = ?", (obs,)
        ).fetchone()
    finally:
        conn.close()
    return str(row[0])


def events(lay: Layout) -> list[tuple]:
    conn = sqlite3.connect(lay.perimeter_dir / perimeter.DB_NAME)
    try:
        return conn.execute(
            "SELECT event_id, state, epoch, detected_by, violated_role "
            "FROM events ORDER BY event_id"
        ).fetchall()
    finally:
        conn.close()


def is_blocked(lay: Layout, root: Root = ROOT) -> bool:
    with perimeter.open_db(lay) as db:
        return trust_state.blocked(db.conn, root)


# --- context identity ------------------------------------------------------------


def test_ctx_id_covers_every_input() -> None:
    base = trust_state.ctx_id("merge", BINDING, ADMIN, RULES, H1, X, P1)
    variants = [
        trust_state.ctx_id("trust-status", BINDING, ADMIN, RULES, H1, X, None),
        trust_state.ctx_id("merge", BINDING, "admin-2", RULES, H1, X, P1),
        trust_state.ctx_id("merge", BINDING, ADMIN, "rules-2", H1, X, P1),
        trust_state.ctx_id("merge", BINDING, ADMIN, RULES, None, X, P1),
        trust_state.ctx_id("merge", BINDING, ADMIN, RULES, H1, H2, P1),
        trust_state.ctx_id("merge", BINDING, ADMIN, RULES, H1, X, P2),
    ]
    for key in ("df_trust_full_name", "approval_policy_repository_id"):
        value = "o/other" if key == "df_trust_full_name" else 7
        changed = replace(BINDING, **{key: value})
        variants.append(trust_state.ctx_id("merge", changed, ADMIN, RULES, H1, X, P1))
    history = replace(BINDING, history_gh_config_dir="/etc/df/history-gh")
    variants.append(trust_state.ctx_id("merge", history, ADMIN, RULES, H1, X, P1))
    assert len({base, *variants}) == len(variants) + 1
    assert base == trust_state.ctx_id("merge", BINDING, ADMIN, RULES, H1, X, P1)


def test_open_observation_refuses_a_ctx_of_other_inputs(lay: Layout) -> None:
    ctx = trust_state.ctx_id("trust-status", BINDING, ADMIN, RULES, None, H2, None)
    with perimeter.open_db(lay) as db, pytest.raises(ValueError):
        trust_state.open_observation(
            db, ROOT, ctx, route="trust-status", binding=BINDING,
            admin_digest=ADMIN, rules=RULES, accepted=None, head=H1, policy=None,
        )  # fmt: skip


def test_open_observation_pins_the_accepted_head(lay: Layout) -> None:
    """A stale A (the row moved since the caller read it) is refused: the
    observation would pin an A it does not have."""
    accept(lay, H1, 1)
    with perimeter.open_db(lay) as db, pytest.raises(Unavailable) as info:
        observe(db, accepted=None, head=H2)
    assert info.value.reason == "trust_head_moved"


# --- observations ----------------------------------------------------------------


def test_observation_dedup_equal_ctx_only(lay: Layout) -> None:
    with perimeter.open_db(lay) as db:
        first = observe(db, head=H1)
        again = observe(db, head=H1)
        other_route = observe(db, route="perimeter-status", head=H1)
        other_head = observe(db, head=H2)
        merge_p1 = observe(db, route="merge", head=X, policy=P1)
        merge_p2 = observe(db, route="merge", head=X, policy=P2)
        assert again == first
        ids = {first.id, other_route.id, other_head.id, merge_p1.id, merge_p2.id}
        assert len(ids) == 5
        assert [o.id for o in trust_state.unfinished(db.conn, ROOT)] == sorted(ids)
    assert first.row_version == 0 and first.accepted is None and first.head == H1
    assert merge_p1.policy == P1 and merge_p1.route == "merge"


def test_trust_status_does_not_complete_merge_observation(lay: Layout) -> None:
    """A narrower check (`trust-status` (A, A)) never completes a wider one
    (`merge` (A, X, P)): the merge observation stays and the root stays
    blocked (I)."""
    accept(lay, H1, 1)
    with perimeter.open_db(lay) as db:
        merge = observe(db, route="merge", accepted=H1, head=X, policy=P1)
        narrow = observe(db, accepted=H1, head=H1)
        assert narrow.ctx != merge.ctx
        out = trust_state.advance_head(
            db, ROOT, narrow.id, lambda: H1, last_epoch=1, source_digest="env"
        )
        assert out == "accepted"
    assert outcome(lay, merge.id) == "open"
    assert is_blocked(lay)


def test_two_p_two_events(lay: Layout) -> None:
    accept(lay, H1, 1)
    with perimeter.open_db(lay) as db:
        one = observe(db, route="merge", accepted=H1, head=X, policy=P1)
        two = observe(db, route="merge", accepted=H1, head=X, policy=P2)
        a = trust_state.register_findings(
            db, ROOT, one.ctx, [POLICY_BREAK], epochs_at(H1)
        )
        b = trust_state.register_findings(
            db, ROOT, two.ctx, [POLICY_BREAK], epochs_at(H1)
        )
    assert a != b
    assert [e[1:] for e in events(lay)] == [
        ("registered", 1, "trust:merge", "approval-policy"),
        ("registered", 1, "trust:merge", "approval-policy"),
    ]


def test_findings_outside_the_route_refused(lay: Layout) -> None:
    with perimeter.open_db(lay) as db:
        obs = observe(db)
        with pytest.raises(Unavailable) as info:
            trust_state.register_findings(
                db, ROOT, obs.ctx, [RULESET, POLICY_BREAK], epochs_at(H1)
            )
    assert info.value.reason == "verdict_unbound"
    assert events(lay) == []
    assert outcome(lay, obs.id) == "open"


def test_finding_registered_without_advance(lay: Layout) -> None:
    accept(lay, H1, 1)
    with perimeter.open_db(lay) as db:
        obs = observe(db, accepted=H1, head=H2)
        ids = trust_state.register_findings(db, ROOT, obs.ctx, [RULESET], epochs_at(H1))
        head = trust_state.accepted_head(db.conn, ROOT)
    assert outcome(lay, obs.id) == "finding_registered"
    assert head is not None and head.sha == H1 and head.row_version == 1
    assert [e[:3] for e in events(lay)] == [(ids[0], "registered", 1)]
    assert is_blocked(lay)
    with perimeter.open_db(lay) as db:  # the pair may still be accepted after
        out = trust_state.advance_head(
            db, ROOT, obs.id, lambda: H2, last_epoch=1, source_digest="env"
        )
        head = trust_state.accepted_head(db.conn, ROOT)
    assert out == "finding_registered"
    assert head is not None and head.sha == H2 and head.finding_event_id == ids[0]
    assert is_blocked(lay)  # by the event, until a later epoch
    accept(lay, H3, 2)
    assert not is_blocked(lay)


def _ctx() -> mp.context.BaseContext:
    return mp.get_context("fork")


def _advance_child(root: str, obs: int, barrier: object, out: object) -> None:
    lay = Layout(Path(root))
    with perimeter.open_db(lay) as db:
        barrier.wait()  # type: ignore[attr-defined]
        got = trust_state.advance_head(
            db, ROOT, obs, lambda: H2, last_epoch=1, source_digest="env"
        )
    out.put((obs, got))  # type: ignore[attr-defined]


def test_concurrent_advance_one_wins_findings_kept(lay: Layout) -> None:
    accept(lay, H1, 1)
    with perimeter.open_db(lay) as db:
        plain = observe(db, accepted=H1, head=H2)
        found = observe(db, route="perimeter-status", accepted=H1, head=H2)
        ids = trust_state.register_findings(
            db, ROOT, found.ctx, [RULESET], epochs_at(H1)
        )
    ctx = _ctx()
    barrier, out = ctx.Barrier(2, timeout=10), ctx.Queue()
    procs = [
        ctx.Process(target=_advance_child, args=(str(lay.root), o, barrier, out))
        for o in (plain.id, found.id)
    ]
    for p in procs:
        p.start()
    got = dict(out.get(timeout=30) for _ in procs)
    for p in procs:
        p.join(timeout=30)
        assert p.exitcode == 0
    assert got[found.id] == "finding_registered"
    assert got[plain.id] in ("accepted", "superseded")
    with perimeter.open_db(lay) as db:
        head = trust_state.accepted_head(db.conn, ROOT)
    assert head is not None and head.sha == H2 and head.row_version == 2
    winner_found = got[plain.id] == "superseded"
    assert head.finding_event_id == (ids[0] if winner_found else None)
    assert [e[0] for e in events(lay)] == ids  # the finding is kept either way


def test_rollback_h1_h2_h1_finding_after_restart(lay: Layout) -> None:
    """The branch goes back H2 -> H1: the pair (H2, H1) is `not_descendant`.
    After a restart the same pair gives the same finding: the same event,
    no new epoch."""
    accept(lay, H1, 1)
    accept(lay, H2, 1)
    with perimeter.open_db(lay) as db:
        obs = observe(db, accepted=H2, head=H1)
        first = trust_state.register_findings(
            db, ROOT, obs.ctx, [NOT_DESCENDANT], epochs_at(H2)
        )
    with perimeter.open_db(lay) as db:  # a restart: a new process opens anew
        again = observe(db, accepted=H2, head=H1)
        assert again.id != obs.id and again.ctx == obs.ctx
        second = trust_state.register_findings(
            db, ROOT, again.ctx, [NOT_DESCENDANT], epochs_at(H2)
        )
    assert second == first
    assert [e[1:3] for e in events(lay)] == [("registered", 1)]
    assert outcome(lay, again.id) == "finding_registered"
    assert is_blocked(lay)


def test_lost_answer_reevaluated_same_inputs(lay: Layout) -> None:
    """The checker's answer is lost: the observation stays unfinished and
    the next call of any route of the root evaluates the same pinned inputs
    again (dedup by `ctx_id`)."""
    accept(lay, H1, 1)
    with perimeter.open_db(lay) as db:
        lost = observe(db, route="merge", accepted=H1, head=X, policy=P1)
    assert is_blocked(lay)
    with perimeter.open_db(lay) as db:
        again = observe(db, route="merge", accepted=H1, head=X, policy=P1)
        assert again == lost
        assert trust_state.unfinished(db.conn, ROOT) == [lost]
        assert not trust_state.stale_context(lost, BINDING, ADMIN, RULES)


def test_unresolved_until_journal_readable(lay: Layout) -> None:
    accept(lay, H1, 1)
    with perimeter.open_db(lay) as db:
        obs = observe(db, accepted=H1, head=H2)
        for status in ("tree_incomplete", "unset"):
            assert trust_state.OUTCOME_OF_VERDICT[status] == "unresolved"
            assert (
                trust_state.apply_status(
                    db, ROOT, obs.id, status, epochs_of=epochs_at(H1)
                )
                == "unresolved"
            )
        assert trust_state.blocked(db.conn, ROOT)
        assert observe(db, accepted=H1, head=H2).id == obs.id  # still the one
    with perimeter.open_db(lay) as db:  # the journal is readable again
        out = trust_state.advance_head(
            db, ROOT, obs.id, lambda: H2, last_epoch=1, source_digest="env"
        )
    assert out == "accepted"
    assert not is_blocked(lay)


def test_context_change_conservative(lay: Layout) -> None:
    """The binding changed while an observation was unfinished: its inputs
    cannot be reproduced -- K, never an eternal `unresolved`."""
    accept(lay, H1, 1)
    with perimeter.open_db(lay) as db:
        obs = observe(db, accepted=H1, head=H2)
        trust_state.apply_status(
            db, ROOT, obs.id, "tree_incomplete", epochs_of=epochs_at(H1)
        )
    newer = replace(BINDING, approval_policy_repository_id=7)
    assert trust_state.stale_context(obs, newer, ADMIN, RULES)
    assert trust_state.stale_context(obs, BINDING, "admin-2", RULES)
    with perimeter.open_db(lay) as db:
        event = trust_state.conservative(
            db, ROOT, obs.ctx, obs.id, epochs_of=epochs_at(H1)
        )
        again = trust_state.conservative(db, ROOT, obs.ctx, obs.id, epochs_of=PENDING)
        assert again == event
    assert outcome(lay, obs.id) == "conservative"
    assert events(lay) == [
        (event, "registered", 1, trust_state.CONSERVATIVE_SOURCE, None)
    ]
    assert is_blocked(lay)
    accept(lay, H2, 2)  # the next epoch, opened by the human
    assert not is_blocked(lay)


def test_unsupported_size_conservative(lay: Layout) -> None:
    accept(lay, H1, 1)
    with perimeter.open_db(lay) as db:
        obs = observe(db, accepted=H1, head=H2)
        assert trust_state.OUTCOME_OF_VERDICT["unsupported_size"] == "conservative"
        assert (
            trust_state.apply_status(
                db, ROOT, obs.id, "unsupported_size", epochs_of=PENDING
            )
            == "conservative"
        )
        with pytest.raises(KeyError):
            trust_state.apply_status(db, ROOT, obs.id, "clean", epochs_of=PENDING)
    assert outcome(lay, obs.id) == "conservative"
    assert [e[1:4] for e in events(lay)] == [
        ("pending", None, trust_state.CONSERVATIVE_SOURCE)
    ]
    assert is_blocked(lay)


def test_pinned_p_objects_missing_conservative(lay: Layout) -> None:
    """The objects of a pinned P are gone for good: the merge observation
    is completed by K, and never `clear` on it."""
    accept(lay, H1, 1)
    with perimeter.open_db(lay) as db:
        obs = observe(db, route="merge", accepted=H1, head=X, policy=P1)
        event = trust_state.conservative(
            db, ROOT, obs.ctx, obs.id, epochs_of=epochs_at(H1)
        )
        out = trust_state.advance_head(
            db, ROOT, obs.id, lambda: X, last_epoch=1, source_digest="env"
        )
    assert out == "conservative"
    assert events(lay)[0][:3] == (event, "registered", 1)
    assert is_blocked(lay)


def test_epoch_from_current_snapshot_e2_blocks_until_e3(lay: Layout) -> None:
    """Detected in E1, registered after E2 opened: the epoch is that of the
    verified current snapshot (E2), so it blocks until E3. A stale head
    (not the accepted row's) is no source: `pending`."""
    accept(lay, H1, 1)
    with perimeter.open_db(lay) as db:
        obs = observe(db, accepted=H1, head=H1)  # detected in E1
    accept(lay, H2, 2)  # E2 opened meanwhile
    with perimeter.open_db(lay) as db:
        stale = trust_state.current_epoch(lambda: H1, BINDING, ADMIN, RULES)
        assert stale(db.conn, ROOT) is None
        other = trust_state.current_epoch(lambda: H2, BINDING, "admin-2", RULES)
        assert other(db.conn, ROOT) is None
        trust_state.register_findings(db, ROOT, obs.ctx, [RULESET], epochs_at(H2))
    assert [e[1:3] for e in events(lay)] == [("registered", 2)]
    assert is_blocked(lay)
    accept(lay, H3, 3)
    assert not is_blocked(lay)


def test_rebind_return_known_root_uses_old_A(lay: Layout) -> None:
    accept(lay, H1, 1)  # R1
    accept(lay, H2, 1, binding=BINDING_R2)  # --rebind R2
    with perimeter.open_db(lay) as db:  # back to R1: its A, events, observations
        head = trust_state.accepted_head(db.conn, ROOT)
        assert head is not None and head.sha == H1
        obs = observe(db, accepted=head.sha, head=H3)
        assert obs.accepted == H1 and obs.row_version == 1
        assert trust_state.known_roots(db.conn, ROOT) == [ROOT, R2]
        with pytest.raises(Unavailable, match="trust_head_moved"):
            observe(db, accepted=None, head=H3)  # never cold for a known root


def test_rebind_same_triple_cold_history_kept(lay: Layout) -> None:
    """The same triple with other parameters: the check of A is invalid
    (cold path), A and the history stay; an unfinished observation of the
    old parameters is K."""
    accept(lay, H1, 1)
    with perimeter.open_db(lay) as db:
        old = observe(db, accepted=H1, head=H2)
    rebound = replace(BINDING, df_trust_full_name="o/df-trust-renamed")
    assert rebound.root_id == BINDING.root_id
    with perimeter.open_db(lay) as db:
        head = trust_state.accepted_head(db.conn, ROOT)
        assert head is not None and head.sha == H1
        assert trust_state.head_valid(head, BINDING, ADMIN, RULES)
        assert not trust_state.head_valid(head, rebound, ADMIN, RULES)
        assert trust_state.stale_context(old, rebound, ADMIN, RULES)
        trust_state.conservative(db, ROOT, old.ctx, old.id, epochs_of=PENDING)
        new = observe(db, accepted=H1, head=H2, binding=rebound)
        assert new.id != old.id
        out = trust_state.advance_head(
            db, ROOT, new.id, lambda: H2, last_epoch=1, source_digest="env"
        )
        head = trust_state.accepted_head(db.conn, ROOT)
    assert out == "accepted"
    assert head is not None and head.sha == H2
    assert trust_state.head_valid(head, rebound, ADMIN, RULES)
    assert len(events(lay)) == 1  # the history of the root is kept


def test_known_roots_include_heads_and_observations(lay: Layout) -> None:
    r3: Root = ("github.com", 11, "main")
    accept(lay, H1, 1, binding=BINDING_R2)  # trust_heads only
    b3 = TrustBinding("github.com", 11, "o/three", "main", 6, "public")
    with perimeter.open_db(lay) as db:
        observe(db, head=H1, binding=b3)  # trust_observations only
        conn = db.conn
        assert trust_state.known_roots(conn, ROOT) == [ROOT, R2, r3]
        assert perimeter.known_roots(conn, ROOT) == [ROOT, R2, r3]


def test_superseded_when_the_head_moved(lay: Layout) -> None:
    accept(lay, H1, 1)
    with perimeter.open_db(lay) as db:
        obs = observe(db, accepted=H1, head=H2)
        out = trust_state.advance_head(
            db, ROOT, obs.id, lambda: H3, last_epoch=1, source_digest="env"
        )
        head = trust_state.accepted_head(db.conn, ROOT)
    assert out == "superseded"
    assert head is not None and head.sha == H1


def test_conservative_needs_an_epoch_rule(lay: Layout) -> None:
    """The epoch of a K event is the caller's choice, never a default."""
    with perimeter.open_db(lay) as db:
        obs = observe(db)
        with pytest.raises(TypeError):
            trust_state.conservative(db, ROOT, obs.ctx, obs.id)  # type: ignore[call-arg]


def test_invariants_use_no_sql_json_functions() -> None:
    """JSON1 is not guaranteed on SQLite 3.31-3.37 (or a build without it):
    a `json_` call in the open checks would make every open fail. JSON is
    checked in Python (`_observations_ok`) instead."""
    for sql in perimeter._INVARIANTS:  # noqa: SLF001
        assert "json_" not in sql.lower(), sql


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE trust_observations SET event_ids = 'not json'",
        "UPDATE trust_observations SET event_ids = '{}'",
        "UPDATE trust_observations SET event_ids = '[\"x\"]'",
        "UPDATE trust_observations SET event_ids = '[999]'",
        "UPDATE trust_observations SET binding = 'not json'",
        "UPDATE trust_observations SET binding = '[]'",
        "UPDATE trust_observations SET binding = '{}'",
        "UPDATE trust_observations SET event_ids = 5",
        "UPDATE trust_observations SET event_ids = '" + "[" * 100_000 + "'",
        (
            "UPDATE trust_observations SET event_ids = '"
            + "[" * 100_000
            + "]" * 100_000
            + "'"
        ),
        "UPDATE trust_observations SET binding = '" + "[" * 100_000 + "'",
        (
            "UPDATE trust_observations SET event_ids = '"
            + "[" * 30_000
            + "]" * 30_000
            + "'"
        ),
        "UPDATE trust_observations SET binding = '" + "[" * 60_000 + "'",
        "UPDATE trust_observations SET event_ids = '[" + "1, " * 30_000 + "1]'",
        "UPDATE trust_observations SET event_ids = '[NaN]'",
        "UPDATE trust_observations SET event_ids = '[" + "9" * 5000 + "]'",
        (
            "UPDATE trust_observations SET binding = "
            '\'{"df_trust_repository_id": Infinity, "approval_policy_repository_id": 6}\''
        ),
        "UPDATE trust_observations SET event_ids = '[true]'",
        (
            "UPDATE trust_observations SET binding = "
            '\'{"df_trust_repository_id": "5", "approval_policy_repository_id": 6}\''
        ),
    ],
)
def test_corrupt_observation_row_is_integrity(lay: Layout, sql: str) -> None:
    """A damaged observation row is a database that fails its check
    (`unavailable`), never a program error of a later read."""
    with perimeter.open_db(lay) as db:
        observe(db)
    conn = sqlite3.connect(lay.perimeter_dir / perimeter.DB_NAME)
    conn.execute(sql)
    conn.commit()
    conn.close()
    with pytest.raises(Unavailable) as info:
        perimeter.open_db(lay)
    assert info.value.reason == "integrity"


# --- crash points ----------------------------------------------------------------


def _crash_child(root: str, point: str, obs: int, ctx: str) -> None:
    def step(name: str) -> None:
        if name == point:
            os._exit(0)

    trust_state.TRUST_STEP = step
    lay = Layout(Path(root))
    with perimeter.open_db(lay) as db:
        if point == "observation":
            observe(db, accepted=H1, head=H3)
        elif point == "findings":
            trust_state.register_findings(db, ROOT, ctx, [RULESET], epochs_at(H1))
        elif point == "advance":
            trust_state.advance_head(
                db, ROOT, obs, lambda: H2, last_epoch=1, source_digest="env"
            )
        else:
            trust_state.conservative(db, ROOT, ctx, obs, epochs_of=PENDING)
    os._exit(3)  # the point was never reached


def _snapshot(lay: Layout) -> tuple[list[tuple], list[tuple], list[tuple]]:
    conn = sqlite3.connect(lay.perimeter_dir / perimeter.DB_NAME)
    try:
        return (
            conn.execute("SELECT * FROM events").fetchall(),
            conn.execute("SELECT * FROM trust_heads").fetchall(),
            conn.execute("SELECT * FROM trust_observations").fetchall(),
        )
    finally:
        conn.close()


@pytest.mark.parametrize(
    "point", ["observation", "findings", "advance", "conservative"]
)
def test_crash_points(lay: Layout, point: str) -> None:
    """A writer killed inside each transaction of the trust state, before
    its `COMMIT`: the next open rolls the journal back and the state is the
    one before -- never half an observation, a finding without its outcome
    or a head without its `row_version`. The repeat then completes."""
    accept(lay, H1, 1)
    with perimeter.open_db(lay) as db:
        obs = observe(db, accepted=H1, head=H2)
    before = _snapshot(lay)
    child = _ctx().Process(
        target=_crash_child, args=(str(lay.root), point, obs.id, obs.ctx)
    )
    child.start()
    child.join(timeout=60)
    assert child.exitcode == 0, point
    assert (lay.perimeter_dir / perimeter.JOURNAL).exists()
    perimeter.open_db(lay).close()  # the publisher's open rolls it back
    assert _snapshot(lay) == before
    with perimeter.open_db(lay) as db:
        if point == "observation":
            assert observe(db, accepted=H1, head=H3).id > obs.id
        elif point == "findings":
            trust_state.register_findings(db, ROOT, obs.ctx, [RULESET], epochs_at(H1))
            assert outcome(lay, obs.id) == "finding_registered"
        elif point == "advance":
            got = trust_state.advance_head(
                db, ROOT, obs.id, lambda: H2, last_epoch=1, source_digest="env"
            )
            assert got == "accepted"
        else:
            trust_state.conservative(db, ROOT, obs.ctx, obs.id, epochs_of=PENDING)
            assert outcome(lay, obs.id) == "conservative"
    assert not (lay.perimeter_dir / perimeter.JOURNAL).exists()


# --- the module ------------------------------------------------------------------


def test_trust_state_stdlib_only() -> None:
    """The key holder's trust state imports no third-party code: stdlib and
    `df` only, `governance` neither directly nor through `df`."""
    tree = ast.parse(Path(trust_state.__file__).read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split(".")[0])
    assert names - {"__future__", "df"} <= set(sys.stdlib_module_names)
    code = (
        "import sys, df.publisher.trust_state; "
        "print(sorted(m for m in sys.modules if m.split('.')[0] in "
        "('governance', 'yaml', 'steward')))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert out.stdout.strip() == "[]"
