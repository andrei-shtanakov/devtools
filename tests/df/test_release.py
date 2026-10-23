"""Tests for the release and the real route calls (Task 9, part 9-A).

`df.release` (id, record, registration, activation) and `df.install` part 1
(`InstallRun`, `install_calls`, `install_actions`, `read_for`,
`command_ready`, `rollback_calls`). Everything runs in `fake_layout` as the
current user (root faked), through the world of `tests/df/route_world.py`:
no root, no network. `drive` runs with the real actions and the real facts
from `none` to `committed`.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import os
import shutil
import textwrap
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from df import accept, admin, fsutil, install, release, routes
from df import install_table as table
from df import platform as plat
from df.accept import Facts, classify
from df.config import (
    Pending,
    ReleaseEntry,
    ReleasesConfig,
    config_digest,
    dump_install,
    dump_pending,
    dump_releases,
    load_releases,
)
from df.digest import release_digest, tree_digest
from df.inputs import lock_digest
from df.install import (
    ACTIONS,
    InstallRun,
    command_ready,
    install_actions,
    install_calls,
    read_for,
    rollback_calls,
)
from df.journal import StepFailed, read_journal
from df.launcher_entry import entry_text
from df.manifest import Profile, RepoPin
from df.release import ReleaseRecord, RepoRecord, next_release_id
from df.routes import RouteContext, implementation, route_guard, run_route
from df.sources import SourceId
from tests.df.conftest import BUILD_UID, FakeLayout
from tests.df.route_world import (
    BOOT,
    ID,
    INSTALL,
    KEY,
    LOCK_A,
    PENDING,
    PY,
    State,
    devtools_ref,
    expected_route,
    install_run,
    normalize,
    release_world,
    repo_ref,
    setup_layout,
    write_accounts,
)

SHA = "0" * 40
PLATFORM = plat.detect_platform()
PREFIX = f"{SHA[:12]}-{PLATFORM}-"
#: The journal of a clean pass: every action of a fresh release, in order.
CLEAN_PASS = [
    "build-dir",
    "build",
    "seal",
    "copy",
    "verify",
    "admin",
    "register",
    "activate",
    "launcher",
    "commit-config",
]
Event = tuple[object, ...]


@pytest.fixture(autouse=True)
def _boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fsutil, "boot_id", lambda: BOOT)


@pytest.fixture()
def fl(monkeypatch: pytest.MonkeyPatch, fake_layout: FakeLayout) -> FakeLayout:
    """The world of a fresh install: accounts, `pending.toml`, every path
    trusted (`/usr/local/bin` included)."""
    world = setup_layout(monkeypatch, fake_layout)
    write_accounts(world)
    world.layout.pending.write_text(dump_pending(PENDING))
    monkeypatch.setattr(plat, "trusted_path_problems", lambda _p: [])
    return world


def drive_run(
    run: InstallRun,
    *,
    actions: Mapping[str, Callable[[Facts], None]] | None = None,
    read: Callable[[], Facts] | None = None,
) -> None:
    """`drive` of the run's release: the real actions and facts by default."""
    accept.drive(
        run.layout,
        run.release_id,
        actions=actions if actions is not None else install_actions(run),
        read=read if read is not None else read_for(run),
        journal=run.layout.journal,
        run_id=run.run_id,
    )


def done(fl: FakeLayout, run_id: str = "run-1") -> list[str]:
    return [
        str(r["step"])
        for r in read_journal(fl.layout.journal)
        if r["run_id"] == run_id and r["event"] == "done"
    ]


# ---------- the id ----------


def test_next_id_skips_registered_quarantined_and_leftovers(fl: FakeLayout) -> None:
    """M1: any registry status, `releases/`, `staging/`, `admin/` by exact
    name, a mark `<id>.toml`, a quarantine `<id>` or `<id>.<k>`."""
    lay = fl.layout
    revoked = ReleaseEntry(
        f"{PREFIX}1", "sha256:" + "1" * 64, "sha256:" + "2" * 64, PY, "revoked", "x"
    )
    (lay.etc / "releases.toml").write_text(dump_releases(ReleasesConfig((revoked,))))
    (lay.releases / f"{PREFIX}2").mkdir()
    (lay.staging / f"{PREFIX}3").mkdir()
    (lay.quarantine / f"{PREFIX}4.1").mkdir()
    (lay.admin / f"{PREFIX}5").mkdir()
    (lay.marks_dir / f"{PREFIX}6.toml").write_text("raw\n")
    (lay.quarantine / f"{PREFIX}7").mkdir()
    (lay.releases / f"{PREFIX}10").mkdir()
    assert next_release_id(lay, SHA, PLATFORM) == f"{PREFIX}8"


def test_next_id_names_are_exact(fl: FakeLayout) -> None:
    """`-10`, `-10.1`, `-1x`, `-1.toml` in `releases/` do not use `-1`."""
    lay = fl.layout
    (lay.releases / f"{PREFIX}10").mkdir()
    (lay.quarantine / f"{PREFIX}10.1").mkdir()
    (lay.admin / f"{PREFIX}1x").mkdir()
    (lay.releases / f"{PREFIX}1.toml").mkdir()
    (lay.marks_dir / f"{PREFIX}1.json").write_text("x")
    assert next_release_id(lay, SHA, PLATFORM) == f"{PREFIX}1"
    shutil.rmtree(lay.root / "opt")
    assert next_release_id(lay, SHA, PLATFORM) == f"{PREFIX}1"


def test_next_id_broken_registry_refused(fl: FakeLayout) -> None:
    (fl.layout.etc / "releases.toml").write_text("garbage = [\n")
    with pytest.raises(StepFailed) as exc:
        next_release_id(fl.layout, SHA, PLATFORM)
    assert exc.value.step == "release-id"


# ---------- activation ----------


@pytest.mark.parametrize(
    "spoil", ["unregistered", "revoked", "admin-missing", "tree", "mode"]
)
def test_activate_refuses_unregistered_revoked_or_admin_missing(
    fl: FakeLayout, spoil: str
) -> None:
    """The precondition of `activate`: an `allowed`, intact entry — else
    `StepFailed("activate")` before any change; `current` stays."""
    lay = fl.layout
    entry = release_world(fl)
    lay.current.symlink_to("releases/old")
    if spoil == "unregistered":
        (lay.etc / "releases.toml").unlink()
    elif spoil == "revoked":
        bad = dataclasses.replace(entry, status="revoked", reason="x")
        (lay.etc / "releases.toml").write_text(dump_releases(ReleasesConfig((bad,))))
    elif spoil == "admin-missing":
        shutil.rmtree(lay.admin_dir(ID))
    elif spoil == "tree":
        (lay.release_dir(ID) / "src" / "app.py").write_text("tampered\n")
    else:
        lay.release_dir(ID).chmod(0o700)
    fl.clear()
    with pytest.raises(StepFailed) as exc:
        release.activate(lay, ID)
    assert exc.value.step == "activate"
    assert fl.events == []
    assert os.readlink(lay.current) == "releases/old"


@pytest.mark.parametrize("before", [None, "releases/old"])
def test_activate_is_atomic_relative_symlink_and_syncs(
    fl: FakeLayout, before: str | None
) -> None:
    """Protocol `activate`: one `rename` of a temporary relative link over
    `current`, then `sync_dir(/opt/df)`; no temporary name is left."""
    lay = fl.layout
    release_world(fl)
    if before is not None:
        lay.current.symlink_to(before)
    names = set(os.listdir(lay.opt)) | {"current"}
    fl.clear()
    release.activate(lay, ID)
    assert os.readlink(lay.current) == f"releases/{ID}"
    assert fl.events == [("renamed", lay.current), ("sync_dir", lay.opt)]
    assert set(os.listdir(lay.opt)) == names


def test_activate_crash_after_rename_leaves_only_current(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    lay = fl.layout
    release_world(fl)

    def crash(kind: str, path: Path) -> None:
        raise RuntimeError(f"{kind} {path}")

    monkeypatch.setattr(fsutil, "fault_point", crash)
    with pytest.raises(RuntimeError):
        release.activate(lay, ID)
    assert os.readlink(lay.current) == f"releases/{ID}"
    assert not [n for n in os.listdir(lay.opt) if n.endswith(".tmp")]


# ---------- the record and the registry ----------


def record(**kw: object) -> ReleaseRecord:
    values: dict[str, object] = {
        "id": ID,
        "digest": "sha256:" + "1" * 64,
        "admin_digest": "sha256:" + "2" * 64,
        "admin_python": PY,
        "manifest_repo": "o/m",
        "manifest_sha": SHA,
        "profile": "p",
        "platform": PLATFORM,
        "created_at": "2026-10-23T00:00:00+00:00",
        "repos": (RepoRecord("r1", "https://x/r1", SHA, "sha256:" + "3" * 64, ("g",)),),
        "python_declared": {"r1": "3.13.1 (main)"},
        "uv_version_declared": "uv 0.9.2",
        "distributions_declared": {
            "r1": (
                install.InstalledDist("a", "1.0", SourceId("registry")),
                install.InstalledDist(
                    "b", "2.0", SourceId("git", "https://x/b", None, "c" * 40)
                ),
            )
        },
    }
    values.update(kw)
    return ReleaseRecord(**values)  # type: ignore[arg-type]


def test_release_json_schema_fields_and_mode(fl: FakeLayout) -> None:
    """§3.5: the schema, the digests, the pins with their lock digests, the
    declared values; a registry source's index is `not declared`."""
    lay = fl.layout
    tree = lay.release_dir(ID)
    tree.mkdir()
    empty = release_digest(tree)
    fl.clear()
    release.write_release_json(tree, record())
    path = tree / "release.json"
    assert fl.events == [("renamed", path), ("sync_dir", tree)]
    assert os.stat(path).st_mode & 0o777 == 0o644
    data = json.loads(path.read_text())
    assert data == {
        "schema": "df/release/v1",
        "id": ID,
        "digest": "sha256:" + "1" * 64,
        "admin_digest": "sha256:" + "2" * 64,
        "admin_python": PY,
        "manifest_repo": "o/m",
        "manifest_sha": SHA,
        "profile": "p",
        "platform": PLATFORM,
        "created_at": "2026-10-23T00:00:00+00:00",
        "repos": [
            {
                "name": "r1",
                "url": "https://x/r1",
                "sha": SHA,
                "lock_sha256": "sha256:" + "3" * 64,
                "groups": ["g"],
            }
        ],
        "python_declared": {"r1": "3.13.1 (main)"},
        "uv_version_declared": "uv 0.9.2",
        "distributions_declared": {
            "r1": [
                {
                    "name": "a",
                    "version": "1.0",
                    "source": {"kind": "registry", "index": "not declared"},
                },
                {
                    "name": "b",
                    "version": "2.0",
                    "source": {"kind": "git", "url": "https://x/b", "commit": "c" * 40},
                },
            ]
        },
    }
    assert release_digest(tree) == empty  # not in the digest (spec 3.5)


def test_release_json_replaces_the_builders_file(fl: FakeLayout) -> None:
    """N3: a `release.json` the builder left in the copy is replaced."""
    tree = fl.layout.release_dir(ID)
    tree.mkdir()
    (tree / "release.json").write_text('{"forged": true}\n')
    release.write_release_json(tree, record())
    assert json.loads((tree / "release.json").read_text())["id"] == ID


@pytest.mark.parametrize("bad", ["absent", "link", "other-id"])
def test_release_json_needs_the_release_dir(fl: FakeLayout, bad: str) -> None:
    lay = fl.layout
    tree = lay.release_dir(ID)
    if bad == "link":
        (lay.releases / "other").mkdir()
        tree.symlink_to("other")
    elif bad == "other-id":
        tree.mkdir()
    rec = record(id="other") if bad == "other-id" else record()
    with pytest.raises(StepFailed) as exc:
        release.write_release_json(tree, rec)
    assert exc.value.step == "register"
    assert not (lay.releases / "other" / "release.json").exists()


def test_register_appends_an_allowed_entry(fl: FakeLayout) -> None:
    lay = fl.layout
    path = lay.etc / "releases.toml"
    fl.clear()
    release.register(lay, record(id="old"))
    release.register(lay, record())
    assert fl.events == [("renamed", path), ("sync_dir", lay.etc)] * 2
    assert os.stat(path).st_mode & 0o777 == 0o644
    entry = accept.registry_entry(lay, ID)
    assert entry == ReleaseEntry(
        ID, "sha256:" + "1" * 64, "sha256:" + "2" * 64, PY, "allowed", None
    )
    assert accept.registry_entry(lay, "old") is not None


@pytest.mark.parametrize("bad", ["duplicate", "broken", "link"])
def test_register_refuses(fl: FakeLayout, bad: str) -> None:
    lay = fl.layout
    path = lay.etc / "releases.toml"
    if bad == "duplicate":
        revoked = dataclasses.replace(record().entry(), status="revoked", reason="x")
        path.write_text(dump_releases(ReleasesConfig((revoked,))))
    elif bad == "broken":
        path.write_text("garbage = [\n")
    else:
        (lay.etc / "real.toml").write_text(dump_releases(ReleasesConfig(())))
        path.symlink_to("real.toml")
    before = path.read_bytes() if not path.is_symlink() else None
    fl.clear()
    with pytest.raises(StepFailed) as exc:
        release.register(lay, record())
    assert exc.value.step == "register"
    assert fl.events == []
    if before is not None:
        assert path.read_bytes() == before


# ---------- the real route calls ----------


def test_install_actions_cover_rows(fl: FakeLayout) -> None:
    """Keys of `install_actions` = every `Row.action` (`unknown`'s included)
    without `""` and `"stop"`; each runs only in its own phase (F14)."""
    want = {r.action for r in (*table.ROWS, table.UNKNOWN_ROW)} - {"", "stop"}
    actions = install_actions(install_run(fl))
    assert set(actions) == want == set(ACTIONS)
    none = Facts(
        P=True,
        reg=False,
        cur=False,
        L=False,
        intact=False,
        m="none",
        r="absent",
        full=False,
        A=False,
        S=False,
    )
    assert classify(none) == "none"
    fl.clear()
    for name, act in actions.items():
        if name == "build-dir":
            continue
        with pytest.raises(StepFailed, match="не в своей фазе"):
            act(none)
    assert fl.events == []


def test_action_table_is_route_unfolding(fl: FakeLayout) -> None:
    """R33.F4: keys of `install_calls` == `route_leaves()` ∪ `WRAPS`; the
    block `install-route-calls` of `install_actions`' docstring is
    `render_route_calls()`."""
    calls = install_calls(install_run(fl))
    assert set(calls) == set(table.route_leaves()) | set(table.WRAPS)
    doc = textwrap.dedent(inspect.getdoc(install_actions) or "")
    begin, end = (
        "<!-- install-route-calls:begin -->",
        "<!-- install-route-calls:end -->",
    )
    block = doc.split(begin, 1)[1].split(end, 1)[0].strip("\n")
    assert block == table.render_route_calls()


def test_rollback_calls_are_the_route_unfolding(fl: FakeLayout) -> None:
    """I5: exactly the calls the unfolding of `rollback` names — and they run
    the route without a profile, a network or a `df-build` record."""
    want = {implementation(c, t) for _g, c, t in table.unfold_route("rollback")}
    calls = rollback_calls(fl.layout, ID, fl.layout.journal, "run-1")
    assert set(calls) == want
    lay = fl.layout
    (lay.release_dir(ID) / "src").mkdir(parents=True)
    (lay.admin_dir(ID) / "df").mkdir(parents=True)
    (lay.marks_dir / f"{ID}.toml").write_text("raw\n")
    lay.accounts.unlink()
    run_route(lay, ID, "rollback", calls=calls, python_key=None, build_uid=None)
    for path in (
        lay.release_dir(ID),
        lay.admin_dir(ID),
        lay.mark_file(ID),
        lay.pending,
    ):
        assert not os.path.lexists(path), path


def test_rollback_without_build_uid_refuses_a_foreign_tree(fl: FakeLayout) -> None:
    """I5: no `df-build` record, a tree owned by someone else — `StepFailed`
    naming the owner before anything is removed."""
    lay = fl.layout
    tree = fl.builder_dir(lay.release_dir(ID))
    calls = rollback_calls(lay, ID, lay.journal, "run-1")
    fl.clear()
    with pytest.raises(StepFailed, match=str(BUILD_UID)) as exc:
        run_route(lay, ID, "rollback", calls=calls, build_uid=None)
    assert exc.value.step == "route"
    assert tree.is_dir() and lay.pending.exists()
    assert fl.events == []


def test_clean_pass_walks_rows_in_table_order(fl: FakeLayout) -> None:
    """`drive` with the real actions and facts: a fresh release walks every
    row to `committed`; then `current`, the command, `install.toml` and the
    registry agree, and `pending.toml` is gone."""
    run = install_run(fl)
    drive_run(run)
    lay = fl.layout
    assert done(fl) == CLEAN_PASS
    assert classify(read_for(run)()) == "committed"
    assert os.readlink(lay.current) == f"releases/{ID}"
    assert (lay.etc / "install.toml").read_text() == dump_install(INSTALL)
    assert os.stat(lay.etc / "install.toml").st_mode & 0o777 == 0o644
    assert not os.path.lexists(lay.pending)
    entry = accept.registry_entry(lay, ID)
    assert entry is not None and entry.status == "allowed"
    assert entry.digest == release_digest(lay.release_dir(ID))
    assert command_ready(lay, ID)
    assert os.readlink(lay.local_bin / "darkfactory") == str(lay.launcher)
    data = json.loads((lay.release_dir(ID) / "release.json").read_text())
    assert (data["id"], data["digest"]) == (ID, entry.digest)


def _facets(fl: FakeLayout) -> State:
    """The facet state of the route model, read by the real guards."""
    lay = fl.layout

    def g(name: str) -> bool:
        return route_guard(lay, ID, name, python_key=KEY, build_uid=BUILD_UID)

    r = "root" if g("r=root") else "builder" if g("r=builder") else "absent"
    s = "tree" if g("S=tree") else "container" if g("S=container") else "none"
    return {
        "r": r,
        "S": s,
        "admin": g("admin=present"),
        "m": g("m=present"),
        "reg": not g("not reg"),
        "py": not g("py=absent"),
        "pystaging": g("py-staging=present"),
        "local_bin": g("local_bin=trusted"),
    }


def test_clean_pass_routes_match_table_with_real_calls(fl: FakeLayout) -> None:
    """R33.F3: every action of the clean pass, with all-real calls, emits
    exactly the events its route's unfolding predicts from the state it ran
    in (the facet model of the routes test; call markers aside)."""
    run = install_run(fl)
    seen: list[tuple[str, list[Event], list[Event]]] = []

    def recorded(name: str, act: Callable[[Facts], None]) -> Callable[[Facts], None]:
        def go(facts: Facts) -> None:
            expected, _final = expected_route(fl.layout, name, _facets(fl))
            start = len(fl.events)
            act(facts)
            got = normalize(fl.events[start:])
            seen.append((name, got, normalize([e for e in expected if e[0] != "call"])))

        return go

    actions = {n: recorded(n, a) for n, a in install_actions(run).items()}
    drive_run(run, actions=actions)
    assert [n for n, _g, _e in seen] == CLEAN_PASS
    for name, got, expected in seen:
        assert got == expected, name


def test_install_digest_is_one_value(fl: FakeLayout) -> None:
    """Task 7 final M1, M8: the digest `read_for` reads the marks under is
    `config_digest(cfg)`, the one `acceptance_calls` writes them under, and
    `pending.install_digest`; another `pending` digest is refused. Twin: a
    read under another digest stops `drive` at the first post check."""
    run = install_run(fl)
    assert run.install_digest == config_digest(INSTALL) == PENDING.install_digest
    for bad in ({"install_digest": "f" * 64}, {"install_text": "x = 1\n"}):
        with pytest.raises(StepFailed) as exc:
            install_run(fl, pending=dataclasses.replace(PENDING, **bad))
        assert exc.value.step == "pending"
    seen: list[str] = []
    real = accept.read_facts

    def spy(*a: object, **kw: object) -> Facts:
        seen.append(str(kw["install_digest"]))
        return real(*a, **kw)  # type: ignore[arg-type]

    with pytest.MonkeyPatch.context() as m:
        m.setattr(accept, "read_facts", spy)
        read_for(run)()
    assert seen == [config_digest(INSTALL)]

    def other() -> Facts:
        return accept.read_facts(
            run.layout,
            ID,
            install_digest="f" * 64,
            build_uid=BUILD_UID,
            launcher_ready=lambda: False,
            admin_ready=admin.admin_ready,
        )

    with pytest.raises(StepFailed) as stop:
        drive_run(run, read=other)
    assert (stop.value.step, stop.value.detail) == ("build", "prepared → building")


def test_read_facts_binds_admin_predicates(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """R33.F5, I3: `read_for` reads the facts with `df.admin.admin_ready` and
    `L` = `command_ready(layout, id)` of the run's release and `build_uid`."""
    run = install_run(fl)
    got: dict[str, object] = {}

    def spy(layout: object, release_id: str, **kw: object) -> Facts:
        got.update(kw, layout=layout, release_id=release_id)
        raise StepFailed("spy", "")

    monkeypatch.setattr(accept, "read_facts", spy)
    with pytest.raises(StepFailed):
        read_for(run)()
    assert got["admin_ready"] is admin.admin_ready
    assert (got["layout"], got["release_id"], got["build_uid"]) == (
        fl.layout,
        ID,
        BUILD_UID,
    )
    asked: list[tuple[object, str]] = []
    monkeypatch.setattr(
        install, "command_ready", lambda lay, rid: asked.append((lay, rid)) or True
    )
    launcher = got["launcher_ready"]
    assert callable(launcher) and launcher() is True
    assert asked == [(fl.layout, ID)]


def test_command_ready_reads_nothing_before_registration(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """I3: no entry, or an entry not intact — false without asking
    `launcher_ready` (nothing read from an unchecked `admin/<id>`)."""
    lay = fl.layout
    asked: list[str] = []
    monkeypatch.setattr(
        admin, "launcher_ready", lambda *a, **kw: asked.append("L") or True
    )
    assert not command_ready(lay, ID)
    release_world(fl)
    (lay.release_dir(ID) / "src" / "app.py").write_text("tampered\n")
    assert not command_ready(lay, ID)
    assert asked == []


def test_command_ready_uses_the_entry_python(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    lay = fl.layout
    release_world(fl)
    asked: list[tuple[str, str]] = []

    def ready(_lay: object, rid: str, *, python: str) -> bool:
        asked.append((rid, python))
        return True

    monkeypatch.setattr(admin, "launcher_ready", ready)
    assert command_ready(lay, ID)
    assert asked == [(ID, PY)]


def test_entry_text_from_activated_admin_code(fl: FakeLayout) -> None:
    """Task 8a M-1: the entry is written from `admin/<id>/df/launcher_entry.py`
    of the activated release — not the running `df` — and `L` compares with
    the same source."""
    ref = devtools_ref(fl)
    source = ref / "df" / "launcher_entry.py"
    source.write_text(source.read_text() + "# release N+1\n")
    run = install_run(fl, refs={"devtools": ref})
    drive_run(run)
    lay = fl.layout
    admin_source = lay.admin_dir(ID) / "df" / "launcher_entry.py"
    assert lay.entry.read_text() == entry_text(lay, admin_source)
    assert lay.entry.read_text().endswith("# release N+1\n")
    assert lay.entry.read_text() != entry_text(lay)
    assert admin.launcher_ready(lay, ID, python=PY)


def test_launcher_without_registry_entry_refused(fl: FakeLayout) -> None:
    """The command names the registered admin Python; no entry — refusal."""
    calls = install_calls(install_run(fl))
    with pytest.raises(StepFailed) as exc:
        calls["write_atomic"]("bin/darkfactory")
    assert exc.value.step == "launcher"
    with pytest.raises(AssertionError):
        calls["write_atomic"]("/etc/df/other.toml")


def test_install_run_guards_its_invariants(fl: FakeLayout) -> None:
    run = install_run(fl)
    with pytest.raises(AssertionError, match="python_key"):
        dataclasses.replace(run, python_key="cpython-3.12.0-x")
    with pytest.raises(AssertionError, match="plan"):
        dataclasses.replace(run, plan=dataclasses.replace(run.plan, release_id="x"))


# ---------- release.json (I4, M12) ----------

PIN = RepoPin("r1", "https://example.invalid/r1", SHA, ())
PROFILE_R1 = Profile("p", PY, (PIN,))


def _accepted_copy(fl: FakeLayout) -> tuple[dict[str, Path], RouteContext]:
    """A copy with `venv/r1` accepted by the real `distribution_problems`
    leaf, and its `admin` mark with the copy's digest."""
    ref = repo_ref(fl, "r1", LOCK_A, [("a", "1.0")])
    refs = {"r1": ref, "devtools": devtools_ref(fl)}
    ctx = RouteContext(python_declared={"r1": "3.13.1 (main)"})
    calls = install_calls(install_run(fl, ctx, profile=PROFILE_R1, refs=refs))
    calls["distribution_problems"]("releases/<id>")
    digest = release_digest(fl.layout.release_dir(ID))
    mark = accept.AcceptMark(
        config_digest(INSTALL),
        "admin",
        BOOT,
        digest,
        "sha256:" + "2" * 64,
        PY,
        "uv 0.9.2",
        accept.dump_declared(ctx.python_declared or {}),
    )
    accept.write_mark(fl.layout, ID, mark)
    return refs, ctx


def test_release_json_distributions_from_the_accepted_tree(fl: FakeLayout) -> None:
    """I4: `distributions_declared` == the lists `distribution_problems`
    accepted on the same tree, also from a new process after `verified`
    (fresh context); `python_declared` and `uv_version_declared` come from
    the mark; `lock_sha256` is the trusted lock's (M12)."""
    refs, ctx = _accepted_copy(fl)
    registry = {"kind": "registry", "index": "not declared"}
    accepted = [
        {"name": d.name, "version": d.version, "source": registry}
        for d in ctx.installed["r1"]
    ]
    assert [d["name"] for d in accepted] == ["a"]
    fresh = install_run(fl, profile=PROFILE_R1, refs=refs)
    assert fresh.ctx.installed == {} and fresh.ctx.python_declared is None
    install_calls(fresh)["write_release_json"]("releases/<id>/release.json")
    data = json.loads((fl.layout.release_dir(ID) / "release.json").read_text())
    assert data["distributions_declared"] == {"r1": accepted}
    assert data["python_declared"] == {"r1": "3.13.1 (main)"}
    assert data["uv_version_declared"] == "uv 0.9.2"
    assert data["repos"] == [
        {
            "name": "r1",
            "url": PIN.url,
            "sha": SHA,
            "lock_sha256": lock_digest(refs["r1"] / "uv.lock", "r1"),
            "groups": [],
        }
    ]
    assert data["digest"] == release_digest(fl.layout.release_dir(ID))


@pytest.mark.parametrize("bad", ["tree", "mark"])
def test_release_json_refused_unless_the_copy_is_the_marks(
    fl: FakeLayout, bad: str
) -> None:
    """I4b: the environments are read only after the copy's digest equals
    the mark's; no `admin` mark — no record."""
    refs, _ctx = _accepted_copy(fl)
    if bad == "tree":
        (fl.layout.release_dir(ID) / "venv" / "extra").write_text("x\n")
    else:
        accept.drop_mark(fl.layout, ID)
    calls = install_calls(install_run(fl, profile=PROFILE_R1, refs=refs))
    for name, target in (
        ("write_release_json", "releases/<id>/release.json"),
        ("register", "/etc/df/releases.toml"),
    ):
        with pytest.raises(StepFailed) as exc:
            calls[name](target)
        assert exc.value.step == "register"
    assert not (fl.layout.release_dir(ID) / "release.json").exists()
    assert accept.registry_entry(fl.layout, ID) is None


# ---------- upgrade ----------

ID2 = "rel2"


def test_upgrade_keeps_shared_admin_python(fl: FakeLayout) -> None:
    """S1-R10-N2: the profile's Python is installed by the first release; the
    second's route `admin` skips `install_admin_python`, the first release
    and its `admin/<id>` are untouched, `A` holds and the upgrade commits."""
    lay = fl.layout
    drive_run(install_run(fl))
    assert done(fl) == CLEAN_PASS
    python_dir = lay.admin_python / KEY
    before = (os.stat(python_dir).st_ino, tree_digest(python_dir))
    first_admin = admin.admin_digest(lay, ID)
    first_tree = release_digest(lay.release_dir(ID))
    pending = Pending("run-2", ID2, PENDING.install_text, PENDING.install_digest, ID)
    lay.pending.write_text(dump_pending(pending))
    run = install_run(fl, pending=pending)
    fl.clear()
    drive_run(run)
    # The command files of the first release are already the second's (the
    # same admin code, the same Python): `L` holds, `launcher` is not needed.
    upgrade = [a for a in CLEAN_PASS if a != "launcher"]
    assert done(fl) == CLEAN_PASS + upgrade
    changed = [
        e
        for e in fl.events
        if e[0] in ("created", "renamed", "unlinked", "chmod", "sync_tree")
        and Path(str(e[1])).is_relative_to(lay.admin_python)
    ]
    assert changed == []
    assert (os.stat(python_dir).st_ino, tree_digest(python_dir)) == before
    assert admin.admin_digest(lay, ID) == first_admin
    assert release_digest(lay.release_dir(ID)) == first_tree
    assert os.readlink(lay.current) == f"releases/{ID2}"
    entries = {e.id: e for e in load_releases(lay.etc / "releases.toml").releases}
    assert set(entries) == {ID, ID2}
    assert entries[ID2].admin_python == entries[ID].admin_python == PY


# ---------- fix round 1 ----------


def _builder_staging(fl: FakeLayout) -> Path:
    """A rollback that needs the cleaner: `staging/<id>/tree` of `df-build`."""
    lay = fl.layout
    lay.staging_dir(ID).mkdir(mode=0o711)
    tree = fl.builder_dir(lay.staging_tree(ID))
    (tree / "f").write_text("x\n")
    return tree


@pytest.mark.parametrize("account", ["no-accounts", "not-ready"])
def test_rollback_never_runs_as_build_user_before_it_is_ready(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout, account: str
) -> None:
    """I-1 (security order): `rollback_calls` reaches no `run_as(df-build)`
    while `verify_build_user` has problems — `StepFailed("rollback")` before
    anything changes; no `accounts.toml` included."""
    lay = fl.layout
    tree = _builder_staging(fl)
    if account == "no-accounts":
        lay.accounts.unlink()
        monkeypatch.setattr(
            install.build_mod, "verify_build_user", plat.verify_build_user
        )
    else:
        monkeypatch.setattr(
            install.build_mod, "verify_build_user", lambda _l: ["state = creating"]
        )
    runs: list[object] = []
    monkeypatch.setattr(plat, "run_as", lambda *a, **kw: runs.append(a))
    calls = rollback_calls(lay, ID, lay.journal, "run-1")
    fl.clear()
    with pytest.raises(StepFailed) as exc:
        run_route(lay, ID, "rollback", calls=calls, build_uid=None)
    assert exc.value.step == "rollback"
    assert runs == [] and fl.events == []
    assert tree.is_dir() and lay.pending.exists()


def test_rollback_runs_the_cleaner_when_the_account_is_ready(fl: FakeLayout) -> None:
    """The positive twin of I-1: a ready account — the cleaner runs as
    `df-build` and the builder tree is removed."""
    lay = fl.layout
    _builder_staging(fl)
    calls = rollback_calls(lay, ID, lay.journal, "run-1")
    fl.clear()
    run_route(lay, ID, "rollback", calls=calls, build_uid=BUILD_UID)
    assert any(e[0] == "run_as" for e in fl.events)
    assert not os.path.lexists(lay.staging_tree(ID))
    assert not os.path.lexists(lay.pending)


def test_python_declared_survives_a_new_process_after_verified(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """m1, R40/F7: run A stops after `verified`; run B, a new process with a
    fresh context that never runs `transfer_check`, carries A's answers
    through `admin` into `release.json`."""
    answers = {"devtools": "3.13.1 (main, A)"}
    real = routes.transfer_check

    def transfer(*a: object, **kw: object) -> object:
        result = real(*a, **kw)
        return dataclasses.replace(result, python_declared=answers)

    with monkeypatch.context() as m:
        m.setattr(routes, "transfer_check", transfer)
        with pytest.raises(StepFailed, match="нет продвижения"):
            accept.drive(
                fl.layout,
                ID,
                actions=install_actions(install_run(fl)),
                read=read_for(install_run(fl)),
                journal=fl.layout.journal,
                run_id="run-a",
                max_iter=5,
            )
    mark = accept.load_mark(fl.layout, ID, install_digest=config_digest(INSTALL))
    assert mark is not None and mark.stage == "verified"

    def no_transfer(*_a: object, **_kw: object) -> object:
        raise AssertionError("run B re-ran transfer_check")

    monkeypatch.setattr(routes, "transfer_check", no_transfer)
    run_b = install_run(fl)
    assert run_b.ctx.python_declared is None
    drive_run(run_b)
    data = json.loads((fl.layout.release_dir(ID) / "release.json").read_text())
    assert data["python_declared"] == answers


def test_install_run_platform_is_the_hosts(fl: FakeLayout) -> None:
    """m2: the command text and `L` name one platform."""
    other = "linux-x86_64" if PLATFORM == "macos-arm64" else "macos-arm64"
    with pytest.raises(AssertionError, match="platform"):
        dataclasses.replace(install_run(fl), platform=other)


def test_read_facts_asks_l_only_when_intact(fl: FakeLayout) -> None:
    """m3: `L` is read only with `intact` (the table uses it only there)."""
    asked: list[bool] = []
    f = accept.read_facts(
        fl.layout,
        ID,
        install_digest=config_digest(INSTALL),
        build_uid=BUILD_UID,
        launcher_ready=lambda: asked.append(True) or True,
        admin_ready=admin.admin_ready,
    )
    assert (f.intact, f.L, asked) == (False, False, [])
    release_world(fl)
    f = accept.read_facts(
        fl.layout,
        ID,
        install_digest=config_digest(INSTALL),
        build_uid=BUILD_UID,
        launcher_ready=lambda: asked.append(True) or True,
        admin_ready=admin.admin_ready,
    )
    assert (f.intact, f.L, asked) == (True, True, [True])


def test_activate_reuses_one_temporary_name(fl: FakeLayout) -> None:
    """m4: a temporary link left by a kill is replaced, not accumulated."""
    lay = fl.layout
    release_world(fl)
    (lay.opt / ".current.tmp").symlink_to("releases/stale")
    names = set(os.listdir(lay.opt)) - {".current.tmp"} | {"current"}
    release.activate(lay, ID)
    assert os.readlink(lay.current) == f"releases/{ID}"
    assert set(os.listdir(lay.opt)) == names


def test_register_wrappers_name_their_step(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """m5: an I/O error of either wrapper, and an unreadable trusted lock,
    are `StepFailed("register")`."""
    tree = fl.layout.release_dir(ID)
    tree.mkdir()
    (tree / "release.json").mkdir()
    with pytest.raises(StepFailed) as exc:
        release.write_release_json(tree, record())

    assert exc.value.step == "register"

    def broken(*_a: object, **_kw: object) -> None:
        raise PermissionError(13, "denied")

    monkeypatch.setattr(fsutil, "write_atomic", broken)
    with pytest.raises(StepFailed) as exc:
        release.register(fl.layout, record())
    assert exc.value.step == "register"
    monkeypatch.undo()
    refs, _ctx = _accepted_copy(fl)
    (refs["r1"] / "uv.lock").unlink()
    calls = install_calls(install_run(fl, profile=PROFILE_R1, refs=refs))
    with pytest.raises(StepFailed) as exc:
        calls["write_release_json"]("releases/<id>/release.json")
    assert exc.value.step == "register" and "uv.lock" in exc.value.detail
