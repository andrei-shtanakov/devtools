"""The perimeter schema transition 1 -> 2 and the `restored` flag (spec
6.1; stage 3a, Task 8; owner decision 7, option (a)).

`perimeter-init` (`create_db`) is the only place that migrates; it is called
in this process with the test layout, crash points in forked children
through `perimeter.MIGRATE_STEP` (as `test_perimeter_restore.py` does with
`RESTORE_STEP`).
"""

from __future__ import annotations

import hashlib
import multiprocessing as mp
import os
import sqlite3
from pathlib import Path

import pytest

from df.paths import Layout
from df.publisher import perimeter, trust_state
from df.publisher.perimeter import Root, Snapshot, Unavailable
from df.stage2_config import TrustBinding

ROOT: Root = ("github.com", 5, "main")
R2: Root = ("github.com", 9, "main")
BINDING = TrustBinding("github.com", 5, "o/df-trust", "main", 6, "public")
BINDING_R2 = TrustBinding("github.com", 9, "o/df-trust-2", "main", 6, "public")
S1, S2 = "1" * 40, "2" * 40
V2_TABLES = {"meta", "events", "transitions", "trust_heads", "trust_observations"}


class Epochs:
    def __init__(self, sha: str, epoch: int) -> None:
        self.snapshot = Snapshot(sha, epoch)

    def head(self, _root: Root) -> Snapshot | Unavailable:
        return self.snapshot


@pytest.fixture()
def lay(tmp_path: Path) -> Layout:
    layout = Layout(Path(os.path.realpath(tmp_path)) / "root")
    layout.perimeter_dir.mkdir(parents=True, mode=0o700)
    layout.etc.mkdir(parents=True, exist_ok=True)
    return layout


def db_file(lay: Layout) -> Path:
    return lay.perimeter_dir / perimeter.DB_NAME


def raw(lay: Layout) -> sqlite3.Connection:
    return sqlite3.connect(db_file(lay), isolation_level=None)


def make_v1(lay: Layout, admission: str = "ready", *, events: int = 0) -> None:
    """A database as stage 2b's `create_db` left it (schema 1), its marker
    and root's `perimeter.created`."""
    conn = raw(lay)
    try:
        for sql in perimeter.SCHEMA_V1:
            conn.execute(sql)
        conn.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            [("schema", "1"), ("admission", admission)],
        )
        for n in range(events):
            conn.execute(
                "INSERT INTO events (root_host, root_repository_id, root_branch, "
                "request_key, detected_by, observed_at, registered_at, state, "
                "provenance, detail) VALUES ('github.com', 5, 'main', ?, 'x', "
                "'t', 't', 'pending', '{}', 'd')",
                (f"k{n}",),
            )
    finally:
        conn.close()
    for path in (lay.perimeter_dir / perimeter.MARKER, lay.perimeter_created):
        path.write_text("2026-10-30T00:00:00Z\n")
    (lay.perimeter_dir / perimeter.LOCK).touch(mode=0o600)


def meta(lay: Layout) -> dict[str, str]:
    conn = raw(lay)
    try:
        return dict(conn.execute("SELECT key, value FROM meta").fetchall())
    finally:
        conn.close()


def tables(lay: Layout) -> set[str]:
    conn = raw(lay)
    try:
        found = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    finally:
        conn.close()
    return {str(n) for (n,) in found}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def opens(lay: Layout) -> None:
    perimeter.open_db(lay).close()


# --- the transition -------------------------------------------------------------


def test_fresh_database_is_schema_2(lay: Layout) -> None:
    assert perimeter.SCHEMA_VERSION == "2"
    assert perimeter.create_db(lay) == "created"
    assert meta(lay) == {"schema": "2", "admission": "ready", "restored": "false"}
    assert tables(lay) == V2_TABLES
    opens(lay)


def test_migrate_admissible_2b_db(lay: Layout) -> None:
    make_v1(lay)
    assert perimeter.create_db(lay) == "migrated"
    assert meta(lay) == {"schema": "2", "admission": "ready", "restored": "false"}
    assert tables(lay) == V2_TABLES
    opens(lay)  # verified as a fresh schema-2 database is


@pytest.mark.parametrize("why", ["events", "trust.toml", "both"])
def test_migrate_non_admissible_2b_db_refused_unchanged(lay: Layout, why: str) -> None:
    make_v1(lay, events=0 if why == "trust.toml" else 2)
    if why != "events":
        (lay.etc / "trust.toml").write_text("# a binding\n")
    before = digest(db_file(lay))
    for call in (perimeter.create_db, perimeter.migrate):
        with pytest.raises(Unavailable) as info:
            call(lay)
        assert info.value.reason == "migration_precondition"
    assert digest(db_file(lay)) == before
    assert meta(lay) == {"schema": "1", "admission": "ready"}
    with pytest.raises(Unavailable, match="schema_migration_required"):
        opens(lay)


def _ctx() -> mp.context.BaseContext:
    return mp.get_context("fork")


def _migrate_crash(root: str, point: str) -> None:
    def step(name: str) -> None:
        if name == point:
            os._exit(0)

    perimeter.MIGRATE_STEP = step
    perimeter.create_db(Layout(Path(root)))
    os._exit(3)  # the point was never reached


def crash_at(lay: Layout, point: str) -> None:
    p = _ctx().Process(target=_migrate_crash, args=(str(lay.root), point))
    p.start()
    p.join(timeout=60)
    assert p.exitcode == 0, point


def test_migrate_killed_before_commit_whole_v1_then_completes(lay: Layout) -> None:
    make_v1(lay, "restored_pending")
    crash_at(lay, "before-commit")
    journal = lay.perimeter_dir / perimeter.JOURNAL
    assert journal.exists()  # the transaction never ended
    with pytest.raises(Unavailable) as info:
        opens(lay)  # SQLite rolls the hot journal back in the publisher
    assert info.value.reason == perimeter.MIGRATION_REQUIRED
    assert meta(lay) == {"schema": "1", "admission": "restored_pending"}
    assert tables(lay) == {"meta", "events", "transitions"}
    assert perimeter.create_db(lay) == "migrated"
    assert not journal.exists()
    assert meta(lay)["restored"] == "true"
    opens(lay)


def test_migrate_killed_after_commit_whole_v2_rerun_current_verified(
    lay: Layout,
) -> None:
    make_v1(lay)
    crash_at(lay, "committed")
    assert meta(lay) == {"schema": "2", "admission": "ready", "restored": "false"}
    conn = raw(lay)  # an invariant broken after the commit: the rerun sees it
    conn.execute(
        "INSERT INTO trust_heads VALUES ('github.com', 5, 'main', 'x', 1, 'd', "
        "'a', 'r', 'b', 0, 't', NULL)"
    )
    conn.close()
    with pytest.raises(Unavailable, match="integrity"):
        perimeter.create_db(lay)
    conn = raw(lay)
    conn.execute("DELETE FROM trust_heads")
    conn.close()
    assert perimeter.create_db(lay) == "current"


def test_migrate_rerun_idempotent(lay: Layout) -> None:
    make_v1(lay)
    assert perimeter.create_db(lay) == "migrated"
    after = digest(db_file(lay))
    assert perimeter.create_db(lay) == "current"
    assert perimeter.migrate(lay) == "current"
    assert digest(db_file(lay)) == after


def test_migrate_without_marker_marks(lay: Layout) -> None:
    """A schema-1 database whose marker was never written (a crash of 2b's
    creation) is migrated and then marked."""
    make_v1(lay)
    (lay.perimeter_dir / perimeter.MARKER).unlink()
    lay.perimeter_created.unlink()
    assert perimeter.create_db(lay) == "migrated"
    assert (lay.perimeter_dir / perimeter.MARKER).exists()


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE meta SET value = '3' WHERE key = 'schema'",
        "UPDATE meta SET value = '2' WHERE key = 'schema'",  # v1 tables, v2 meta
        "CREATE TABLE extra (x)",
        "INSERT INTO meta (key, value) VALUES ('restored', 'false')",
    ],
)
def test_unknown_schema_untouched_integrity(lay: Layout, sql: str) -> None:
    make_v1(lay)
    conn = raw(lay)
    conn.execute(sql)
    conn.close()
    before = digest(db_file(lay))
    for call in (perimeter.create_db, perimeter.migrate, opens):
        with pytest.raises(Unavailable) as info:
            call(lay)
        assert info.value.reason == "integrity"
    assert digest(db_file(lay)) == before


def test_unknown_v2_meta_integrity(lay: Layout) -> None:
    perimeter.create_db(lay)
    conn = raw(lay)
    conn.execute("UPDATE meta SET value = 'maybe' WHERE key = 'restored'")
    conn.close()
    with pytest.raises(Unavailable, match="integrity"):
        opens(lay)


def test_v1_db_ordinary_open_names_migration(lay: Layout) -> None:
    make_v1(lay)
    before = digest(db_file(lay))
    with pytest.raises(Unavailable) as info:
        opens(lay)
    assert info.value.reason == perimeter.MIGRATION_REQUIRED
    assert info.value.reason == (
        "schema_migration_required: выполните darkfactory roles setup"
    )
    stats = perimeter.stats(lay)
    assert isinstance(stats, Unavailable)
    assert stats.reason == perimeter.MIGRATION_REQUIRED
    assert digest(db_file(lay)) == before


# --- restored ---------------------------------------------------------------------


def test_restored_empty_v1_migrates_restored_true(lay: Layout) -> None:
    """`restore --empty` under 2b left `restored_pending`: the known restore
    survives the transition as `restored = true`; `admission` is kept."""
    make_v1(lay, "restored_pending")
    assert perimeter.create_db(lay) == "migrated"
    assert meta(lay) == {
        "schema": "2",
        "admission": "restored_pending",
        "restored": "true",
    }


def test_restored_empty_v1_admit_r1_then_rebind_r2_conservative(lay: Layout) -> None:
    make_v1(lay, "restored_pending")
    perimeter.create_db(lay)
    with perimeter.open_db(lay) as db:
        perimeter.admit(db, Epochs(S1, 1), ROOT)
        assert perimeter.admission(db.conn) == "ready"
        assert perimeter.restored(db.conn)
        assert trust_state.blocked(db.conn, ROOT)  # db_restore at epoch 1
    with perimeter.open_db(lay) as db:  # a late `--rebind R2`
        ctx = trust_state.ctx_id("trust-status", BINDING_R2, "a", "r", None, S2, None)
        trust_state.open_observation(
            db, R2, ctx, route="trust-status", binding=BINDING_R2,
            admin_digest="a", rules="r", accepted=None, head=S2, policy=None,
        )  # fmt: skip
        got = db.conn.execute(
            "SELECT state, epoch, detected_by FROM events WHERE root_repository_id = 9"
        ).fetchall()
        assert got == [("pending", None, trust_state.CONSERVATIVE_SOURCE)]
        assert trust_state.blocked(db.conn, R2)


def test_restored_flag_never_returns_false(lay: Layout) -> None:
    make_v1(lay, "restored_pending")
    perimeter.create_db(lay)
    with perimeter.open_db(lay) as db:
        perimeter.admit(db, Epochs(S1, 1), ROOT)
        perimeter.restore_conservative(db.conn, [ROOT], Epochs(S1, 1))
        perimeter.admit(db, Epochs(S1, 1), ROOT)
        assert perimeter.admission(db.conn) == "ready"
        assert perimeter.restored(db.conn)
    assert perimeter.create_db(lay) == "current"
    assert meta(lay)["restored"] == "true"


@pytest.mark.parametrize("source", ["empty", "copy"])
def test_restore_v2_sets_restored_before_admission(lay: Layout, source: str) -> None:
    """The restore operation itself marks the database it puts in place:
    `restored = true` together with `restored_pending`, before any admission
    clears the latter."""
    perimeter.create_db(lay)
    copy = None if source == "empty" else db_file(lay).read_bytes()
    assert meta(lay)["restored"] == "false"
    assert perimeter.restore(lay, copy) == "restored"
    assert meta(lay) == {
        "schema": "2",
        "admission": "restored_pending",
        "restored": "true",
    }
    with perimeter.open_db(lay) as db:
        perimeter.admit(db, Epochs(S1, 1), ROOT)
    assert meta(lay)["admission"] == "ready"
    assert meta(lay)["restored"] == "true"


def test_restored_copy_with_open_observation_conservative_never_clear(
    lay: Layout,
) -> None:
    """A copy taken while an observation was open: its context cannot be
    reproduced, so admission completes it by K -- a conservative event --
    and the root is never clear on it."""
    perimeter.create_db(lay)
    ctx = trust_state.ctx_id("trust-status", BINDING, "a", "r", None, S1, None)
    with perimeter.open_db(lay) as db:
        obs = trust_state.open_observation(
            db, ROOT, ctx, route="trust-status", binding=BINDING,
            admin_digest="a", rules="r", accepted=None, head=S1, policy=None,
        )  # fmt: skip
    copy = db_file(lay).read_bytes()
    perimeter.restore(lay, copy)
    with perimeter.open_db(lay) as db:
        perimeter.admit(db, Epochs(S1, 1), ROOT)
        conn = db.conn
        assert conn.execute(
            "SELECT outcome FROM trust_observations WHERE observation_id = ?",
            (obs.id,),
        ).fetchall() == [("conservative",)]
        sources = conn.execute(
            "SELECT detected_by, state, epoch FROM events ORDER BY event_id"
        ).fetchall()
        assert sources == [
            (perimeter.DB_RESTORE, "registered", 1),
            (trust_state.CONSERVATIVE_SOURCE, "registered", 1),
        ]
        out = trust_state.advance_head(
            db, ROOT, obs.id, lambda: S1, last_epoch=1, source_digest="d"
        )
        assert out == "conservative"
        assert trust_state.accepted_head(conn, ROOT) is None
        assert trust_state.blocked(conn, ROOT)


def test_restore_copy_without_root_then_rebind_conservative(lay: Layout) -> None:
    """A copy that never saw R2: after admission, R2 seen for the first
    time is admitted by K, not as a new root."""
    perimeter.create_db(lay)
    perimeter.restore(lay, db_file(lay).read_bytes())
    with perimeter.open_db(lay) as db:
        perimeter.admit(db, Epochs(S1, 1), ROOT)
        assert trust_state.known_roots(db.conn, ROOT) == [ROOT]
        ctx = trust_state.ctx_id("trust-status", BINDING_R2, "a", "r", None, S2, None)
        trust_state.open_observation(
            db, R2, ctx, route="trust-status", binding=BINDING_R2,
            admin_digest="a", rules="r", accepted=None, head=S2, policy=None,
        )  # fmt: skip
        got = db.conn.execute(
            "SELECT detected_by FROM events WHERE root_repository_id = 9"
        ).fetchall()
        assert got == [(trust_state.CONSERVATIVE_SOURCE,)]
        assert trust_state.blocked(db.conn, R2)


def test_new_root_without_restore_is_cold_not_conservative(lay: Layout) -> None:
    """Without a restore an unknown root is new: no conservative event."""
    perimeter.create_db(lay)
    ctx = trust_state.ctx_id("trust-status", BINDING_R2, "a", "r", None, S2, None)
    with perimeter.open_db(lay) as db:
        trust_state.open_observation(
            db, R2, ctx, route="trust-status", binding=BINDING_R2,
            admin_digest="a", rules="r", accepted=None, head=S2, policy=None,
        )  # fmt: skip
        assert db.conn.execute("SELECT count(*) FROM events").fetchone() == (0,)
