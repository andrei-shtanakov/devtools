"""Tests for acceptance marks, facts and settle (Task 7, part C)."""

from __future__ import annotations

import dataclasses
import errno
import os
import shutil
from pathlib import Path

import pytest

from df import accept, fsutil
from df import install_table as table
from df import platform as plat
from df.accept import (
    AcceptMark,
    acquire_paths,
    builder_tree_nonempty,
    classify,
    drop_mark,
    load_mark,
    read_facts,
    settle,
    settle_on_acquire,
    settle_paths,
    write_mark,
)
from df.config import (
    ConfigError,
    Pending,
    ReleaseEntry,
    ReleasesConfig,
    dump_pending,
    dump_releases,
)
from df.digest import TreeError, tree_digest
from df.journal import StepFailed, journal_event, read_journal
from df.paths import Layout
from df.platform import RunResult
from df.receive import remove_own_tree
from tests.df.conftest import FakeLayout

ID = "rel1"
INSTALL = "a" * 64
OTHER_INSTALL = "b" * 64
BOOT = "boot-1"
UV = "0.9.2"
PY = "3.13.1"
OLD_PY = "3.12.7"
#: `python_declared` of a verified mark: canonical JSON `{repo: sys.version}`.
DECLARED = '{"r1":"3.13.1 (main)"}'


@pytest.fixture(autouse=True)
def _boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fsutil, "boot_id", lambda: BOOT)


@pytest.fixture()
def fl(fake_layout: FakeLayout) -> FakeLayout:
    fake_layout.make_dirs()
    return fake_layout


def mark(stage: str, **kw: object) -> AcceptMark:
    """A valid mark of `stage` with the fields that stage requires."""
    later = stage != "built"
    admin = stage == "admin"
    values: dict[str, object] = {
        "install_digest": INSTALL,
        "stage": stage,
        "built_boot_id": BOOT,
        "tree_digest": "sha256:" + "1" * 64 if later else None,
        "admin_digest": "sha256:" + "2" * 64 if admin else None,
        "admin_python": PY if admin else None,
        "uv_version_declared": UV,
        "python_declared": DECLARED if stage in ("verified", "admin") else None,
    }
    values.update(kw)
    return AcceptMark(**values)  # type: ignore[arg-type]


def root_tree(fl: FakeLayout, mode: int = 0o755) -> str:
    """A root-owned `releases/<id>` with content; returns its digest."""
    tree = fl.layout.release_dir(ID)
    (tree / "src").mkdir(parents=True)
    (tree / "src" / "app.py").write_text("print(1)\n")
    tree.chmod(mode)
    return tree_digest(tree)


def admin_tree(fl: FakeLayout, *, pythons: tuple[str, ...] = (PY,)) -> str:
    """`admin/<id>` and admin Pythons on disk; returns the admin digest."""
    admin = fl.layout.admin_dir(ID)
    admin.mkdir(parents=True)
    (admin / "darkfactory.py").write_text("main()\n")
    for version in pythons:
        (fl.layout.admin_python / version).mkdir()
    return tree_digest(admin)


def admin_ready_model(
    layout: Layout, release_id: str, *, expected_admin_digest: str, expected_python: str
) -> bool:
    """Stand-in of 8a's `admin_ready` (model.py `py_good`): the admin digest,
    the exact Python present and passing the policy (marker file = fails)."""
    admin = layout.admin_dir(release_id)
    python = layout.admin_python / expected_python
    return (
        admin.is_dir()
        and tree_digest(admin) == expected_admin_digest
        and python.is_dir()
        and not (python / "POLICY-FAIL").exists()
    )


def register(fl: FakeLayout, digest: str, admin_digest: str, python: str = PY) -> None:
    entry = ReleaseEntry(ID, digest, admin_digest, python, "allowed", None)
    (fl.layout.etc / "releases.toml").write_text(
        dump_releases(ReleasesConfig((entry,)))
    )


def pending(fl: FakeLayout, release_id: str = ID) -> None:
    p = Pending("run-1", release_id, 'schema = "df/install/v1"\n', INSTALL, None)
    fl.layout.pending.write_text(dump_pending(p))


def facts(fl: FakeLayout, **kw: object) -> accept.Facts:
    args: dict[str, object] = {
        "install_digest": INSTALL,
        "build_uid": fl.build_uid,
        "launcher_ready": lambda: False,
        "admin_ready": admin_ready_model,
    }
    args.update(kw)
    return read_facts(fl.layout, ID, **args)  # type: ignore[arg-type]


# ---------- marks ----------


def test_mark_roundtrip_mode_and_schema(fl: FakeLayout) -> None:
    m = mark("admin")
    write_mark(fl.layout, ID, m)
    path = fl.layout.mark_file(ID)
    assert path == fl.layout.marks_dir / f"{ID}.toml"
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert 'schema = "df/accept/v1"' in path.read_text()
    assert load_mark(fl.layout, ID, install_digest=INSTALL) == m


@pytest.mark.parametrize(
    "change",
    [
        {"install_digest": "sha256:" + "a" * 64},
        {"stage": "registered"},
        {"built_boot_id": ""},
        {"uv_version_declared": ""},
        {"tree_digest": None},
        {"tree_digest": "1" * 64},
        {"admin_digest": None},
        {"admin_python": None},
        {"admin_python": "3.13"},
    ],
)
def test_mark_fields_validated(change: dict[str, object]) -> None:
    """F11: the uv version is a required field; admin fields come together."""
    with pytest.raises(ConfigError):
        dataclasses.replace(mark("admin"), **change)  # type: ignore[arg-type]


@pytest.mark.parametrize("stage", ["built", "copied", "verified"])
def test_mark_admin_fields_only_with_admin(stage: str) -> None:
    with pytest.raises(ConfigError):
        mark(stage, admin_digest="sha256:" + "2" * 64, admin_python=PY)
    if stage == "built":
        with pytest.raises(ConfigError):
            mark(stage, tree_digest="sha256:" + "1" * 64)


def test_mark_monotonic_and_drop(fl: FakeLayout) -> None:
    write_mark(fl.layout, ID, mark("built"))
    write_mark(fl.layout, ID, mark("copied"))
    write_mark(fl.layout, ID, mark("copied"))  # same stage: allowed
    write_mark(fl.layout, ID, mark("admin"))
    for lower in ("built", "copied", "verified"):
        with pytest.raises(StepFailed, match="не понижается") as exc:
            write_mark(fl.layout, ID, mark(lower))
        assert exc.value.step == "mark"
    assert load_mark(fl.layout, ID, install_digest=INSTALL) == mark("admin")
    fl.clear()
    drop_mark(fl.layout, ID)
    path = fl.layout.mark_file(ID)
    assert fl.events == [("unlinked", path), ("sync_dir", fl.layout.marks_dir)]
    assert load_mark(fl.layout, ID, install_digest=INSTALL) is None
    write_mark(fl.layout, ID, mark("built"))  # after cleanup: a fresh attempt


def test_write_mark_is_write_atomic(fl: FakeLayout) -> None:
    write_mark(fl.layout, ID, mark("built"))
    path = fl.layout.mark_file(ID)
    assert fl.events == [("renamed", path), ("sync_dir", fl.layout.marks_dir)]


@pytest.mark.parametrize("key", ["built_boot_id", "uv_version_declared"])
def test_mark_carried_fields_unchanged(fl: FakeLayout, key: str) -> None:
    """F11/F32: carried by every later stage, never rewritten."""
    write_mark(fl.layout, ID, mark("built"))
    with pytest.raises(StepFailed, match=key):
        write_mark(fl.layout, ID, mark("copied", **{key: "other"}))


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "{}x",
        "[]",
        '{"r1": "3.13.1 (main)"}',  # not canonical (spacing)
        '{"r2":"a","r1":"b"}',  # not canonical (key order)
        '{"r1":1}',
        "[" * 100000,
    ],
)
@pytest.mark.parametrize("stage", ["verified", "admin"])
def test_mark_python_declared_required_and_canonical(
    stage: str, value: str | None
) -> None:
    """R40/F7 (I4a): `verified` and `admin` carry the interpreter answers as
    canonical JSON `{repo: version}`; anything else is refused."""
    with pytest.raises(ConfigError):
        mark(stage, python_declared=value)


@pytest.mark.parametrize("stage", ["built", "copied"])
def test_mark_python_declared_absent_before_verified(stage: str) -> None:
    with pytest.raises(ConfigError):
        mark(stage, python_declared=DECLARED)


def test_python_declared_dump_is_canonical() -> None:
    assert accept.dump_declared({"r2": "b", "r1": "a"}) == '{"r1":"a","r2":"b"}'
    assert accept.dump_declared({}) == "{}"
    assert accept.parse_declared('{"r1":"a"}') == {"r1": "a"}


def test_mark_python_declared_carried(fl: FakeLayout) -> None:
    """I4a: written with `verified`, carried unchanged by `admin` (checked
    like `built_boot_id`); an `at_least` rewrite keeps a higher mark."""
    write_mark(fl.layout, ID, mark("copied"))
    write_mark(fl.layout, ID, mark("verified"))
    other = '{"r1":"3.13.2"}'
    for stage in ("verified", "admin"):
        with pytest.raises(StepFailed, match="python_declared") as exc:
            write_mark(fl.layout, ID, mark(stage, python_declared=other))
        assert exc.value.step == "mark"
    write_mark(fl.layout, ID, mark("admin"))
    loaded = load_mark(fl.layout, ID, install_digest=INSTALL)
    assert loaded is not None and loaded.python_declared == DECLARED


def test_mark_of_other_config_ignored(fl: FakeLayout) -> None:
    write_mark(fl.layout, ID, mark("admin", install_digest=OTHER_INSTALL))
    assert load_mark(fl.layout, ID, install_digest=INSTALL) is None
    assert load_mark(fl.layout, ID, install_digest=OTHER_INSTALL) is not None
    write_mark(fl.layout, ID, mark("built"))  # other config = absent (F32)
    assert load_mark(fl.layout, ID, install_digest=INSTALL) == mark("built")
    assert facts(fl).m == "built"


def test_built_mark_from_previous_boot_invalid(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    pending(fl)
    write_mark(fl.layout, ID, mark("built"))
    tree = fl.builder_dir(fl.layout.release_dir(ID))
    (tree / "out").write_text("x")
    assert classify(facts(fl)) == "built"
    monkeypatch.setattr(fsutil, "boot_id", lambda: "boot-2")
    assert load_mark(fl.layout, ID, install_digest=INSTALL) is None
    f = facts(fl)
    assert (f.m, f.r) == ("none", "b-full")
    assert classify(f) == "building"
    write_mark(fl.layout, ID, mark("built", built_boot_id="boot-2"))  # stale = absent


def test_copied_mark_survives_reboot(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    write_mark(fl.layout, ID, mark("copied"))
    monkeypatch.setattr(fsutil, "boot_id", lambda: "boot-2")
    assert load_mark(fl.layout, ID, install_digest=INSTALL) == mark("copied")


@pytest.mark.parametrize(
    "shape", ["garbage", "unknown-key", "wrong-schema", "dir", "link"]
)
def test_malformed_mark_is_absent(fl: FakeLayout, shape: str) -> None:
    """F32: malformed or non-regular — `None` (the `m=present` guard removes it)."""
    path = fl.layout.mark_file(ID)
    good = accept.dump_mark(mark("copied"))
    if shape == "garbage":
        path.write_text("= =\n")
    elif shape == "unknown-key":
        path.write_text(good + 'extra = "x"\n')
    elif shape == "wrong-schema":
        path.write_text(good.replace("df/accept/v1", "df/accept/v2"))
    elif shape == "dir":
        path.mkdir()
    else:
        other = fl.layout.marks_dir / "real.toml"
        other.write_text(good)
        path.symlink_to(other)
    assert load_mark(fl.layout, ID, install_digest=INSTALL) is None


def test_write_mark_refuses_missing_marks_dir(fake_layout: FakeLayout) -> None:
    with pytest.raises(StepFailed, match="нет каталога отметок"):
        write_mark(fake_layout.layout, ID, mark("built"))


# ---------- facts ----------


def test_root_mode_regressed_reverifies(fl: FakeLayout) -> None:
    """stage=verified, root 0700 -> copied (the reverify run itself is 7-E)."""
    pending(fl)
    digest = root_tree(fl, 0o755)
    write_mark(fl.layout, ID, mark("verified", tree_digest=digest))
    assert classify(facts(fl)) == "verified"
    fl.layout.release_dir(ID).chmod(0o700)
    f = facts(fl)
    assert (f.m, f.r, f.full) == ("verified", "root-700", True)
    assert classify(f) == "copied"
    fl.layout.release_dir(ID).chmod(0o750)
    assert facts(fl).r == "root-x"
    assert classify(facts(fl)) == "copied"


def test_registered_unhealthy_is_damaged(fl: FakeLayout) -> None:
    pending(fl)
    digest = root_tree(fl)
    admin_digest = admin_tree(fl)
    register(fl, digest, admin_digest)
    (fl.layout.current).symlink_to(f"releases/{ID}")
    f = facts(fl, launcher_ready=lambda: True)
    assert (f.reg, f.intact, f.cur, f.L) == (True, True, True, True)
    assert classify(f) == "launched"
    (fl.layout.release_dir(ID) / "src" / "app.py").write_text("tampered\n")
    f = facts(fl, launcher_ready=lambda: True)
    assert not f.intact
    assert classify(f) == "damaged"


@pytest.mark.parametrize(
    "spoil", ["none", "mode", "tree", "admin", "builder", "absent", "link"]
)
def test_entry_intact_is_the_fact_intact(fl: FakeLayout, spoil: str) -> None:
    """`entry_intact` (Task 9) gives exactly `read_facts`'s `intact` for the
    entry, without `build_uid` or the other facts."""
    pending(fl)
    lay = fl.layout
    digest = root_tree(fl)
    admin_digest = admin_tree(fl)
    register(fl, digest, admin_digest)
    tree = lay.release_dir(ID)
    if spoil == "mode":
        tree.chmod(0o700)
    elif spoil == "tree":
        (tree / "src" / "app.py").write_text("tampered\n")
    elif spoil == "admin":
        (lay.admin_python / PY / "POLICY-FAIL").write_text("")
    elif spoil == "builder":
        fl.set_owner(tree, fl.build_uid)
    elif spoil in ("absent", "link"):
        shutil.rmtree(tree)
        if spoil == "link":
            tree.symlink_to(lay.admin_dir(ID))
    entry = accept.registry_entry(lay, ID)
    assert entry is not None
    got = accept.entry_intact(lay, ID, entry, admin_ready=admin_ready_model)
    assert got is (spoil == "none")
    if spoil not in ("builder", "link"):
        assert facts(fl).intact is got


def test_registry_entry_is_authoritative_over_mark(fl: FakeLayout) -> None:
    """The mark agrees with the disk, the entry does not: the entry wins."""
    pending(fl)
    digest = root_tree(fl)
    admin_digest = admin_tree(fl, pythons=(OLD_PY,))
    write_mark(
        fl.layout,
        ID,
        mark(
            "admin", tree_digest=digest, admin_digest=admin_digest, admin_python=OLD_PY
        ),
    )
    register(fl, digest, admin_digest, python=PY)
    f = facts(fl)
    assert f.A and not f.intact
    assert classify(f) == "damaged"
    register(fl, "sha256:" + "9" * 64, admin_digest, python=OLD_PY)
    f = facts(fl)
    assert f.full and not f.intact
    assert classify(f) == "damaged"


def test_current_without_registration_is_damaged(fl: FakeLayout) -> None:
    pending(fl)
    root_tree(fl)
    fl.layout.current.symlink_to(f"releases/{ID}")
    f = facts(fl)
    assert (f.reg, f.cur) == (False, True)
    assert classify(f) == "damaged"
    fl.layout.current.unlink()
    fl.layout.current.symlink_to("releases/other")
    assert not facts(fl).cur


def test_current_not_a_link_is_outside_the_table(fl: FakeLayout) -> None:
    fl.layout.current.mkdir()
    with pytest.raises(StepFailed) as exc:
        facts(fl)
    assert exc.value.step == "facts"


def test_admin_tree_lost_falls_back_to_verified(fl: FakeLayout) -> None:
    pending(fl)
    digest = root_tree(fl)
    admin_digest = admin_tree(fl)
    write_mark(
        fl.layout, ID, mark("admin", tree_digest=digest, admin_digest=admin_digest)
    )
    assert classify(facts(fl)) == "admin-ready"
    for path in sorted(fl.layout.admin_dir(ID).rglob("*"), reverse=True):
        path.unlink()
    fl.layout.admin_dir(ID).rmdir()
    f = facts(fl)
    assert (f.m, f.A) == ("admin", False)
    assert classify(f) == "verified"


def test_empty_prepared_dir_is_prepared_not_building(fl: FakeLayout) -> None:
    pending(fl)
    tree = fl.builder_dir(fl.layout.release_dir(ID))
    f = facts(fl)
    assert (f.m, f.r) == ("none", "b-empty")
    assert classify(f) == "prepared"
    (tree / ".hidden").write_text("x")
    assert classify(facts(fl)) == "building"


def test_root_owned_empty_dir_is_unknown_not_prepared(fl: FakeLayout) -> None:
    """A crash before `fchown`: root's empty dir is not the builder's (F1)."""
    pending(fl)
    fl.layout.release_dir(ID).mkdir(mode=0o700)
    f = facts(fl)
    assert (f.m, f.r) == ("none", "root-700")
    assert classify(f) == "unknown"


def test_partial_tree_not_accepted_by_journal(fl: FakeLayout) -> None:
    """A journal "copy: done" without the `copied` mark proves nothing."""
    pending(fl)
    write_mark(fl.layout, ID, mark("built"))
    fl.layout.staging_tree(ID).mkdir(parents=True)
    root_tree(fl, 0o700)
    journal_event(fl.layout.journal, run_id="run-1", step="copy", event="done")
    f = facts(fl)
    assert (f.m, f.r, f.S, f.full) == ("built", "root-700", True, False)
    assert classify(f) == "partial-copy"


def test_stray_mark_digest_mismatch_not_full(fl: FakeLayout) -> None:
    """`full` compares the disk with the mark (F24's `.tmp` leftover too)."""
    pending(fl)
    digest = root_tree(fl)
    write_mark(fl.layout, ID, mark("copied", tree_digest=digest))
    assert facts(fl).full
    (fl.layout.release_dir(ID) / ".release.json.1.x.tmp").write_text("{}")
    f = facts(fl)
    assert not f.full
    assert classify(f) == "unknown"


#: Expected facts per prepared state (provenance: r18-local-check/model.py
#: `facts()` and `py_good()`; literal by F8 — tests never read `_cowork_output`).
#: python -> (A before registration, phase; intact after it, phase).
ADMIN_PYTHON_MODEL = {
    "present": ((True, "admin-ready"), (True, "registered")),
    "absent": ((False, "verified"), (False, "damaged")),
    "policy-fail": ((False, "verified"), (False, "damaged")),
    "other-version": ((False, "verified"), (False, "damaged")),
    "both-versions": ((True, "admin-ready"), (True, "registered")),
}


@pytest.mark.parametrize("python", list(ADMIN_PYTHON_MODEL))
@pytest.mark.parametrize("registered", [False, True])
def test_read_facts_admin_python_matches_model(
    fl: FakeLayout, python: str, registered: bool
) -> None:
    """F5: `admin_ready` injected; the mark names the Python before
    registration, the entry after; both 3.12.7 and 3.13.1 may be on disk."""
    pending(fl)
    digest = root_tree(fl)
    pythons = {
        "present": (PY,),
        "absent": (),
        "policy-fail": (PY,),
        "other-version": (OLD_PY,),
        "both-versions": (OLD_PY, PY),
    }[python]
    admin_digest = admin_tree(fl, pythons=pythons)
    if python == "policy-fail":
        (fl.layout.admin_python / PY / "POLICY-FAIL").write_text("")
    calls: list[tuple[str, str]] = []

    def admin_ready(layout: Layout, rid: str, **kw: str) -> bool:
        calls.append((kw["expected_admin_digest"], kw["expected_python"]))
        return admin_ready_model(layout, rid, **kw)  # type: ignore[arg-type]

    if registered:
        register(fl, digest, admin_digest, python=PY)
    else:
        write_mark(
            fl.layout, ID, mark("admin", tree_digest=digest, admin_digest=admin_digest)
        )
    f = facts(fl, admin_ready=admin_ready)
    before, after = ADMIN_PYTHON_MODEL[python]
    ready, phase = after if registered else before
    assert (f.intact if registered else f.A) == ready
    assert classify(f) == phase
    assert calls == [(admin_digest, PY)]


def test_builder_emptiness_decided_by_build_user(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    pending(fl)
    tree = fl.builder_dir(fl.layout.release_dir(ID))
    results: list[RunResult] = []
    seen: list[tuple[str, tuple[str, ...], dict[str, object]]] = []

    def run_as(user: str, argv: list[str], **kw: object) -> RunResult:
        seen.append((user, tuple(argv), kw))
        return results.pop(0)

    opened: list[str] = []
    real_listdir, real_scandir = os.listdir, os.scandir

    def listdir(path: object = ".") -> list[str]:
        opened.append(str(path))
        return real_listdir(path)  # type: ignore[arg-type]

    def scandir(path: object = ".") -> object:
        opened.append(str(path))
        return real_scandir(path)  # type: ignore[arg-type]

    monkeypatch.setattr(plat, "run_as", run_as)
    monkeypatch.setattr(os, "listdir", listdir)
    monkeypatch.setattr(os, "scandir", scandir)
    cases = [
        (RunResult(0, b"x\n", b"", launched=True), "b-full"),
        (RunResult(0, b"", b"", launched=True), "b-empty"),
        (RunResult(1, b"", b"", launched=True), "b-full"),
        (RunResult(0, b"", b"", launched=True, timed_out=True), "b-full"),
        (RunResult(127, b"", b"", launched=False), "b-full"),
    ]
    for result, expected in cases:
        results.append(result)
        assert facts(fl).r == expected
    assert not [p for p in opened if p.startswith(str(tree))]
    user, argv, kw = seen[0]
    assert user == "df-build"
    assert argv == (
        "/usr/bin/find",
        str(tree),
        "-mindepth",
        "1",
        "-maxdepth",
        "1",
        "-print",
        "-quit",
    )
    assert kw == {
        "env": {"PATH": "/usr/bin:/bin", "LANG": "C"},
        "cwd": fl.layout.build_home,
        "network": False,
        "timeout": 10.0,
    }


def test_builder_emptiness_real_find(fl: FakeLayout) -> None:
    """Through the fake launch chain: a real `find`, recorded as run_as(df-build)."""
    tree = fl.builder_dir(fl.layout.release_dir(ID))
    assert not builder_tree_nonempty(fl.layout, tree)
    (tree / "f").write_text("x")
    assert builder_tree_nonempty(fl.layout, tree)
    assert [e[:2] for e in fl.events] == [("run_as", "df-build")] * 2


@pytest.mark.parametrize("shape", ["file", "link", "foreign"])
def test_r_outside_domain_stops(fl: FakeLayout, shape: str) -> None:
    """F20: not a directory, or owned by neither root nor df-build."""
    path = fl.layout.release_dir(ID)
    if shape == "file":
        path.write_text("x")
    elif shape == "link":
        path.symlink_to(fl.layout.admin)
    else:
        path.mkdir()
        fl.set_owner(path, fl.build_uid + 1)
    with pytest.raises(StepFailed) as exc:
        facts(fl)
    assert exc.value.step == "facts"


def test_facts_table_has_exactly_domain_keys(fl: FakeLayout) -> None:
    f = facts(fl)
    assert list(f.table()) == list(table.DOMAINS)
    assert not f.P and f.m == "none" and f.r == "absent" and f.mark is None
    assert classify(f) == "committed"


def test_broken_registry_stops(fl: FakeLayout) -> None:
    (fl.layout.etc / "releases.toml").write_text("garbage = = \n")
    with pytest.raises(StepFailed) as exc:
        facts(fl)
    assert exc.value.step == "facts"


# ---------- settle ----------


def synced(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    calls: list[Path] = []
    monkeypatch.setattr(fsutil, "sync_dir", lambda p: calls.append(p))
    return calls


def test_settle_paths_keys_are_dirs(fl: FakeLayout) -> None:
    assert set(accept.SETTLE_PATHS) == set(table.DIRS)
    assert set(accept.FIRST_PATHS) == set(table.SETTLE_FIRST)
    for paths in (settle_paths(fl.layout, ID), acquire_paths(fl.layout, ID)):
        assert fl.layout.admin_python in paths
        assert paths[0] == fl.layout.quarantine


def test_settle_paths_twin_without_admin_python_fails() -> None:
    twin = {k: v for k, v in accept.SETTLE_PATHS.items() if k != "admin_python"}
    assert set(twin) != set(table.DIRS)


def test_settle_syncs_exactly_settle_paths(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    root_tree(fl, 0o700)
    fl.layout.opt.mkdir(exist_ok=True)
    calls = synced(monkeypatch)
    settle(fl.layout, ID)
    lay = fl.layout
    expected = [
        lay.quarantine,  # SETTLE_FIRST
        lay.install_dir,
        lay.marks_dir,
        lay.etc,
        lay.opt,
        lay.releases,
        lay.release_dir(ID),  # root-owned: its mode is a fact
        lay.staging,  # staging_dir(id) absent: skipped
        lay.admin,
        lay.admin_python,
        lay.bin_dir,  # local_bin: not trusted under tmp_path
    ]
    assert calls == expected
    assert set(calls) == {p for p in settle_paths(lay, ID) if p.is_dir()}
    assert lay.build_home.parent not in calls  # account source, acquire only


def test_settle_skips_builder_root_and_syncs_staging_dir(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    fl.builder_dir(fl.layout.release_dir(ID))
    fl.layout.staging_dir(ID).mkdir()
    calls = synced(monkeypatch)
    settle(fl.layout, ID)
    assert fl.layout.release_dir(ID) not in calls
    assert fl.layout.staging_dir(ID) in calls


def test_settle_syncs_local_bin_iff_trusted(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """F23: one predicate (`local_bin_trusted`) decides; never the host's path."""
    asked: list[Path] = []

    def problems(path: Path) -> list[str]:
        asked.append(path)
        return []

    monkeypatch.setattr(plat, "trusted_path_problems", problems)
    calls = synced(monkeypatch)
    settle(fl.layout, ID)
    assert fl.layout.local_bin in calls
    assert asked == [fl.layout.local_bin]
    assert fl.layout.local_bin != Path("/usr/local/bin")


def test_settle_on_acquire_syncs_acquire_paths(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    lay = fl.layout
    pending(fl)
    root_tree(fl, 0o700)
    lay.staging_dir(ID).mkdir()
    calls = synced(monkeypatch)
    settle_on_acquire(lay)
    existing = [
        p for p in acquire_paths(lay, ID) if p.is_dir() and p != lay.install_dir
    ]
    assert calls == [lay.install_dir, *existing]
    assert calls.count(lay.install_dir) == 1  # synced once (review minor 8)
    assert lay.build_home.parent in calls  # account home (S1-R6-N1, F22)
    assert lay.release_dir(ID) in calls and lay.staging_dir(ID) in calls
    order = acquire_paths(lay, ID)
    assert order.index(lay.install_dir) < order.index(lay.build_home.parent)


def test_settle_on_acquire_without_pending_skips_id_paths(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    lay = fl.layout
    root_tree(fl, 0o700)
    lay.staging_dir(ID).mkdir()
    calls = synced(monkeypatch)
    settle_on_acquire(lay)
    assert lay.release_dir(ID) not in calls and lay.staging_dir(ID) not in calls
    assert calls == [
        lay.install_dir,
        *[p for p in acquire_paths(lay, None) if p.is_dir() and p != lay.install_dir],
    ]


@pytest.mark.parametrize("bad", ["garbage", "bad-id"])
def test_settle_on_acquire_with_bad_pending(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout, bad: str
) -> None:
    """F33: an unreadable pending or id settles id-independent paths, no raise."""
    lay = fl.layout
    if bad == "garbage":
        lay.pending.write_text("= =\n")
    else:
        pending(fl, release_id="../evil")
    calls = synced(monkeypatch)
    settle_on_acquire(lay)
    assert calls == [
        lay.install_dir,
        *[p for p in acquire_paths(lay, None) if p.is_dir() and p != lay.install_dir],
    ]


def test_acquire_paths_cover_settle_paths(fl: FakeLayout) -> None:
    lay = fl.layout
    fl.layout.release_dir(ID).mkdir()
    assert set(settle_paths(lay, ID)) <= set(acquire_paths(lay, ID))
    extra = set(acquire_paths(lay, ID)) - set(settle_paths(lay, ID))
    assert extra == {lay.build_home.parent}


def test_mark_dataclass_field_order() -> None:
    """The brief's positional fields, then the uv version (F11), then the
    interpreter answers (Task 9, I4a)."""
    assert [f.name for f in dataclasses.fields(AcceptMark)] == [
        "install_digest",
        "stage",
        "built_boot_id",
        "tree_digest",
        "admin_digest",
        "admin_python",
        "uv_version_declared",
        "python_declared",
    ]


# ---------- one owner seam (fix round 1) ----------


def test_remove_own_tree_refuses_builder_tree_under_fake(fl: FakeLayout) -> None:
    """The virtual owner reaches `receive` too: a route sending a builder tree to
    `remove_own_tree` fails in tests as it would in production."""
    tree = fl.builder_dir(fl.layout.release_dir(ID))
    with pytest.raises(TreeError, match="not root"):
        remove_own_tree(fl.layout.releases, ID)
    assert tree.is_dir()
    fl.set_owner(tree, os.getuid())  # root's again
    remove_own_tree(fl.layout.releases, ID)
    assert not tree.exists()


def test_every_owner_decision_reads_the_seam() -> None:
    """Class guard: no df module reads `st_uid` but `fsutil.owner_uid`.

    The one exception is the stdlib-only entry (Task 8a), which cannot import
    `fsutil`: it reads `st_uid` once, in its own seam `owner_uid`.
    """
    df_dir = Path(__file__).resolve().parents[2] / "df"
    readers = sorted(
        p.name for p in df_dir.glob("*.py") if "st_uid" in p.read_text(encoding="utf-8")
    )
    assert readers == ["fsutil.py", "launcher_entry.py"]
    entry_text = (df_dir / "launcher_entry.py").read_text(encoding="utf-8")
    assert entry_text.count("st_uid") == 1
    assert "    return st.st_uid\n" in entry_text


@pytest.mark.parametrize("how", ["rmdir", "unlink"])
def test_virtual_owner_does_not_survive_removal(fl: FakeLayout, how: str) -> None:
    """Review minor 5: a reused inode never inherits a stale `df-build` owner."""
    path = fl.layout.release_dir(ID)
    if how == "rmdir":
        fl.builder_dir(path)
        os.rmdir(path)
    else:
        path.write_text("x")
        fl.set_owner(path, fl.build_uid)
        path.unlink()
    assert fl.owners == {}
    fl.set_owner(fl.layout.admin, fl.build_uid)  # an entry on a live dir
    path.mkdir(mode=0o700)
    assert fsutil.owner_uid(os.lstat(path)) == os.getuid()
    assert facts(fl).r == "root-700"


def test_new_dir_drops_a_stale_entry(fl: FakeLayout) -> None:
    path = fl.layout.release_dir(ID)
    path.mkdir()
    st = os.lstat(path)
    os.rmdir(path)
    fl.owners[(st.st_dev, st.st_ino)] = fl.build_uid  # stale, as after a cleaner
    os.mkdir(path)
    new = os.lstat(path)
    assert (new.st_dev, new.st_ino) not in fl.owners


def test_digest_io_error_stops_not_cleans(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """Review minor 4: `OSError` while digesting is `StepFailed("facts")`
    (fail-closed); a `TreeError` reads as "differs"."""
    pending(fl)
    digest = root_tree(fl)
    write_mark(fl.layout, ID, mark("copied", tree_digest=digest))

    def unreadable(tree: Path) -> str:
        raise PermissionError(13, "denied")

    monkeypatch.setattr(accept, "release_digest", unreadable)
    with pytest.raises(StepFailed) as exc:
        facts(fl)
    assert exc.value.step == "facts"

    def refused(tree: Path) -> str:
        raise TreeError("bad name")

    monkeypatch.setattr(accept, "release_digest", refused)
    assert not facts(fl).full


# ---------- fix round 2 ----------


def test_digest_eio_inside_release_stops_drive_without_cleanup(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """An I/O error on one entry of a root-owned `releases/<id>` (m=copied) is
    `StepFailed("facts")`, journaled; no action (cleanup) runs, the tree stays."""
    pending(fl)
    digest = root_tree(fl)
    write_mark(fl.layout, ID, mark("copied", tree_digest=digest))
    bad = fl.layout.release_dir(ID) / "src" / "app.py"
    real_lstat = os.lstat

    def lstat(path: object, *args: object, **kw: object) -> os.stat_result:
        if path == bad:
            raise OSError(errno.EIO, "Input/output error", str(path))
        return real_lstat(path, *args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "lstat", lstat)
    with pytest.raises(StepFailed) as exc:
        facts(fl)
    assert exc.value.step == "facts"
    ran: list[str] = []
    with pytest.raises(StepFailed) as exc:
        accept.drive(
            fl.layout,
            ID,
            actions={a: (lambda f, a=a: ran.append(a)) for a in ("cleanup", "verify")},
            read=lambda: facts(fl),
            journal=fl.layout.journal,
            run_id="run-1",
        )
    assert exc.value.step == "facts"
    assert ran == []
    assert bad.exists()
    rows = read_journal(fl.layout.journal)
    assert [(r["step"], r["event"]) for r in rows] == [("facts", "failed")]
