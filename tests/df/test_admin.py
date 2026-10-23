"""Tests for the administrative environment (Task 8a part 1, `df.admin`).

Everything runs in `fake_layout` as the current user (root faked): the admin
Python comes from the fixture uv of the routes test (`FAKE_UV`) or a variant
of it; only the Step 1 premise tests run the real pinned uv — the hash test
against a local mirror (no external network), the relocation test opt-in
(`DF_NETWORK_TESTS=1`, a real download; Task 13 turns it into its `network`
marker).
"""

from __future__ import annotations

import http.server
import io
import os
import shutil
import stat
import subprocess
import tarfile
import threading
from collections.abc import Sequence
from pathlib import Path

import pytest

from df import acl, admin, fsutil, launcher_entry
from df import build as build_mod
from df import install_table as table
from df import platform as plat
from df.build import BuildPlan
from df.digest import tree_digest
from df.journal import StepFailed
from df.launcher_entry import entry_text
from df.lockset import UV_PINNED_VERSION
from df.paths import Layout
from df.platform import RunResult
from df.receive import CopyLimits
from df.routes import acceptance_calls, run_route
from tests.df.conftest import BUILD_UID, FakeLayout
from tests.df.route_world import (
    BASES,
    BOOT,
    DEFAULT,
    FAKE_UV,
    ID,
    INSTALL,
    KEY,
    PROFILE,
    PY,
    SHORT,
    StandIns,
    devtools_ref,
    expected_events,
    expected_route,
    fake_uv,
    make_calls,
    normalize,
    prepare,
    setup_layout,
    write_accounts,
)

PLATFORM = plat.detect_platform()
ADMIN = "admin/<id>"
PYTHON_TARGET = "admin-python/cpython-…"
Event = tuple[object, ...]


class Crash(Exception):
    """A simulated crash at a fault point."""


@pytest.fixture(autouse=True)
def _boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fsutil, "boot_id", lambda: BOOT)


@pytest.fixture()
def fl(monkeypatch: pytest.MonkeyPatch, fake_layout: FakeLayout) -> FakeLayout:
    layout = setup_layout(monkeypatch, fake_layout)
    write_accounts(layout)
    monkeypatch.setattr(plat, "trusted_path_problems", lambda _p: [])
    return layout


def uv_runs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], dict[str, str]]]:
    """Every `plat.run_command` (argv, env), recorded, then run for real."""
    runs: list[tuple[list[str], dict[str, str]]] = []
    real = plat.run_command

    def run_command(argv: Sequence[str], **kw: object) -> RunResult:
        runs.append((list(argv), dict(kw["env"])))  # type: ignore[arg-type]
        return real(argv, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(plat, "run_command", run_command)
    return runs


def custom_uv(fl: FakeLayout, extra: str) -> Path:
    """`FAKE_UV` with `extra` run after the install (`$T` = the new tree)."""
    path = fl.layout.root.parent / "uv-custom"
    body = f'\n  T="$UV_PYTHON_INSTALL_DIR/$4"\n  {extra}\nfi\n'
    path.write_text(FAKE_UV.replace("\nfi\n", body))
    path.chmod(0o755)
    return path


def install_python(fl: FakeLayout, uv: Path | None = None) -> Path:
    return admin.install_admin_python(
        fl.layout, PY, uv=uv or fake_uv(fl), platform=PLATFORM
    )


def mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path, follow_symlinks=False).st_mode)


# ---------- the admin Python ----------


def test_admin_python_path_is_the_build_key(fl: FakeLayout) -> None:
    lay = fl.layout
    want = lay.admin_python / KEY / "bin" / f"python{SHORT}"
    assert admin.admin_python_path(lay, PY, PLATFORM) == want
    assert KEY == build_mod.python_key(PY, PLATFORM)


def test_install_admin_python_argv_and_env_exact(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """F1: `uv python install --no-bin <exact key>` with exactly six
    variables; the version is asked first with the same environment."""
    runs = uv_runs(monkeypatch)
    lay = fl.layout
    assert install_python(fl) == admin.admin_python_path(lay, PY, PLATFORM)
    env = {
        "UV_PYTHON_INSTALL_DIR": str(lay.admin_python / ".staging"),
        "UV_NO_CONFIG": "1",
        "UV_PYTHON_DOWNLOADS": "automatic",
        "HOME": str(lay.install_dir / "admin-home"),
        "UV_CACHE_DIR": str(lay.install_dir / "admin-home" / "cache"),
        "PATH": "/usr/bin:/bin",
    }
    uv = str(fake_uv(fl))
    assert runs == [
        ([uv, "--version"], env),
        ([uv, "python", "install", "--no-bin", KEY], env),
    ]


def test_install_admin_python_timeout_is_the_builds(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """A 24 MiB download gets the build's command timeout, not 60 s."""
    seen: list[object] = []
    real = plat.run_command

    def run_command(argv: Sequence[str], **kw: object) -> RunResult:
        seen.append(kw["timeout"])
        return real(argv, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(plat, "run_command", run_command)
    install_python(fl)
    assert seen == [admin.VERSION_TIMEOUT, build_mod.COMMAND_TIMEOUT]


def test_install_admin_python_events_exact(fl: FakeLayout) -> None:
    lay = fl.layout
    fl.clear()
    install_python(fl)
    assert normalize(fl.events) == expected_events(
        lay, "install_admin_python", None, PYTHON_TARGET
    )
    # What uv left besides the tree stays for the next route step (r10).
    assert sorted(os.listdir(lay.admin_python / ".staging")) == [".lock"]


def test_admin_home_created_root_0700(fl: FakeLayout) -> None:
    home = fl.layout.admin_home
    assert home == fl.layout.install_dir / "admin-home"
    install_python(fl)
    assert mode(home) == 0o700


@pytest.mark.parametrize("bad", ["mode", "link", "owner", "file"])
def test_admin_home_refused_unless_root_0700(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout, bad: str
) -> None:
    """F6: an existing `admin-home` must be root's real directory `0700`."""
    runs = uv_runs(monkeypatch)
    home = fl.layout.admin_home
    if bad == "link":
        other = fl.layout.root.parent / "elsewhere"
        other.mkdir(mode=0o700)
        home.symlink_to(other)
    elif bad == "file":
        home.write_text("x\n")
    else:
        home.mkdir(mode=0o700)
        home.chmod(0o755 if bad == "mode" else 0o700)
        if bad == "owner":
            fl.set_owner(home, BUILD_UID)
    with pytest.raises(StepFailed) as exc:
        install_python(fl)
    assert exc.value.step == "admin"
    assert runs == []


@pytest.mark.parametrize("python", ["3.11.9", "3.9.6", "3.12", "x"])
def test_python_below_floor_refused_before_uv(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout, python: str
) -> None:
    """F11: the admin code needs Python 3.12+; nothing runs below it."""
    runs = uv_runs(monkeypatch)
    with pytest.raises(StepFailed) as exc:
        admin.install_admin_python(fl.layout, python, uv=fake_uv(fl), platform=PLATFORM)
    assert exc.value.step == "admin"
    assert runs == []
    assert not os.path.lexists(fl.layout.admin_python / ".staging")


@pytest.mark.parametrize("existing", [".staging", KEY])
def test_install_admin_python_refuses_existing(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout, existing: str
) -> None:
    runs = uv_runs(monkeypatch)
    (fl.layout.admin_python / existing).mkdir()
    fl.clear()
    with pytest.raises(StepFailed) as exc:
        install_python(fl)
    assert exc.value.step == "admin"
    assert runs == [] and fl.events == []


def test_unconfirmed_uv_refused(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """F8: a uv version without both confirmed premises installs nothing."""
    monkeypatch.setattr(admin, "UV_ADMIN_CONFIRMED", frozenset({UV_PINNED_VERSION}))
    runs = uv_runs(monkeypatch)
    with pytest.raises(StepFailed) as exc:
        install_python(fl)
    assert exc.value.step == "admin"
    assert "uv 0.9.2: сверка загрузки Python не подтверждена" in exc.value.detail
    assert "перенос Python не подтверждён" in exc.value.detail
    assert [argv[1:] for argv, _env in runs] == [["--version"]]
    assert not os.path.lexists(fl.layout.admin_python / ".staging")


def test_pinned_uv_is_confirmed() -> None:
    assert frozenset({UV_PINNED_VERSION}) == admin.UV_ADMIN_CONFIRMED


@pytest.mark.parametrize(
    "extra,why",
    [
        ("exit 3", "код 3"),
        ('rm -rf "$T"', "uv не создал интерпретатор"),
    ],
)
def test_uv_failure_refused(fl: FakeLayout, extra: str, why: str) -> None:
    with pytest.raises(StepFailed) as exc:
        install_python(fl, custom_uv(fl, extra))
    assert exc.value.step == "admin" and why in exc.value.detail
    assert not os.path.lexists(fl.layout.admin_python / KEY)


TREE_PROBLEMS = [
    pytest.param(f'chmod g+w "$T/bin/python{SHORT}"', "запись для группы", id="g+w"),
    pytest.param('chmod o+w "$T/bin"', "запись для группы", id="o+w-dir"),
    pytest.param(
        f'ln -s "$T/bin/python{SHORT}" "$T/bin/python"', "абсолютная ссылка", id="abs"
    ),
    pytest.param('ln -s ../../../.. "$T/up"', "up:", id="escaping"),
    pytest.param('ln -s missing "$T/bin/dangling"', "dangling", id="dangling"),
    pytest.param('mkfifo "$T/pipe"', "не файл, не каталог", id="fifo"),
    pytest.param(f'chmod u+s "$T/bin/python{SHORT}"', "setuid", id="setuid"),
]


@pytest.mark.parametrize("extra,why", TREE_PROBLEMS)
def test_admin_tree_check_refuses(fl: FakeLayout, extra: str, why: str) -> None:
    """F7/F8: the whole installed tree, not only the interpreter's chain: a
    refusal before the rename; `.staging` is left for the next route step."""
    lay = fl.layout
    fl.clear()
    with pytest.raises(StepFailed) as exc:
        install_python(fl, custom_uv(fl, extra))
    assert exc.value.step == "admin" and why in exc.value.detail
    assert not os.path.lexists(lay.admin_python / KEY)
    assert (lay.admin_python / ".staging" / KEY).is_dir()
    assert normalize(fl.events) == [("created", lay.admin_python / ".staging")]


def test_admin_tree_relative_inner_links_pass(fl: FakeLayout) -> None:
    """The positive twin: uv's own links are relative and inside the tree."""
    uv = custom_uv(fl, f'ln -s python{SHORT} "$T/bin/python3"; ln -s bin "$T/b"')
    install_python(fl, uv)
    assert os.readlink(fl.layout.admin_python / KEY / "bin" / "python3") == (
        f"python{SHORT}"
    )


def test_admin_tree_foreign_owner_refused(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    real = plat.run_command

    def run_command(argv: Sequence[str], **kw: object) -> RunResult:
        result = real(argv, **kw)  # type: ignore[arg-type]
        if "install" in argv:
            tree = fl.layout.admin_python / ".staging" / KEY
            fl.set_owner(tree / "bin" / f"python{SHORT}", BUILD_UID)
        return result

    monkeypatch.setattr(plat, "run_command", run_command)
    with pytest.raises(StepFailed) as exc:
        install_python(fl)
    assert f"владелец uid {BUILD_UID}" in exc.value.detail


def test_umask_is_set_not_inherited(fl: FakeLayout) -> None:
    """F7: under a permissive inherited umask uv still writes no group/other
    write (uv runs under `ADMIN_UMASK`), and the caller's umask comes back."""
    old = os.umask(0)
    try:
        install_python(fl)
        restored = os.umask(old)
    finally:
        os.umask(old)
    assert restored == 0
    tree = fl.layout.admin_python / KEY
    for dirpath, dirs, files in os.walk(tree):
        for name in (".", *dirs, *files):
            assert mode(Path(dirpath) / name) & 0o022 == 0, name


def test_untrusted_interpreter_chain_refused(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    interpreter = admin.admin_python_path(fl.layout, PY, PLATFORM)
    monkeypatch.setattr(
        plat,
        "trusted_path_problems",
        lambda p: ["/opt: запись для группы или всех"] if p == interpreter else [],
    )
    with pytest.raises(StepFailed) as exc:
        install_python(fl)
    assert exc.value.step == "admin"


# ---------- the route `admin`: staging, crash, rerun ----------


def admin_state(**facets: object) -> dict[str, object]:
    return DEFAULT | BASES["admin"][0] | facets


def crash_at(monkeypatch: pytest.MonkeyPatch, kind: str, path: Path) -> None:
    record = fsutil.fault_point

    def fault_point(k: str, p: Path) -> None:
        record(k, p)  # type: ignore[arg-type]
        if (k, p) == (kind, path):
            raise Crash(f"{k} {p}")

    monkeypatch.setattr(fsutil, "fault_point", fault_point)


def run_admin(fl: FakeLayout) -> None:
    calls = StandIns(fl).calls()
    run_route(fl.layout, ID, "admin", calls=calls, python_key=KEY, build_uid=BUILD_UID)


@pytest.mark.parametrize("stop", ["created", "renamed"])
def test_install_admin_python_staging_then_rename(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout, stop: str
) -> None:
    """The real leaf in the real route: a crash at `stop`, then the route
    again. `created`: `remove_own_tree(.staging)` first, then the install;
    `renamed`: `py=absent` is false, the install is skipped, `.staging` is
    removed by the route's own step. Both runs match the data exactly."""
    lay = fl.layout
    state = admin_state()
    prepare(fl, monkeypatch, state)
    fl.clear()
    record = fsutil.fault_point
    where = {
        "created": lay.admin_python / ".staging",
        "renamed": lay.admin_python / KEY,
    }
    crash_at(monkeypatch, stop, where[stop])
    with pytest.raises(Crash):
        run_admin(fl)
    leaf = expected_events(lay, "install_admin_python", None, PYTHON_TARGET)
    got = normalize(fl.events)
    start = got.index(("call", "install_admin_python", PYTHON_TARGET)) + 1
    assert got[start:] == leaf[: leaf.index((stop, where[stop])) + 1]

    monkeypatch.setattr(fsutil, "fault_point", record)
    after = admin_state(pystaging=True, py=stop == "renamed")
    expected, final = expected_route(lay, "admin", after)
    fl.clear()
    run_admin(fl)
    assert normalize(fl.events) == expected
    calls = [e[1] for e in expected if e[0] == "call"]
    assert calls[0] == "remove_own_tree"
    assert ("install_admin_python" in calls) is (stop == "created")
    assert final["py"] and not final["pystaging"]
    assert not os.path.lexists(lay.admin_python / ".staging")


def test_admin_tree_synced_before_mark(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """Route `admin`: `sync_tree(admin/<id>)` -> `sync_dir(admin)` -> the
    mark's rename; the mark's digest is `admin_digest`."""
    lay = fl.layout
    prepare(fl, monkeypatch, admin_state())
    fl.clear()
    run_admin(fl)
    events = normalize(fl.events)
    order = [
        events.index(("sync_tree", lay.admin_dir(ID))),
        events.index(("sync_dir", lay.admin)),
        events.index(("renamed", lay.mark_file(ID))),
    ]
    assert order == sorted(order)
    text = lay.mark_file(ID).read_text()
    assert admin.admin_digest(lay, ID) in text


# ---------- the admin code ----------


def test_install_admin_is_leaf(monkeypatch: pytest.MonkeyPatch, fl: FakeLayout) -> None:
    """S1-R9-N1: its events are exactly its protocol's — no mark, no removal,
    no uv; an existing `admin/<id>` is refused and left untouched."""
    lay = fl.layout
    runs = uv_runs(monkeypatch)
    ref = devtools_ref(fl)
    fl.clear()
    admin.install_admin(lay, ID, devtools_ref=ref)
    assert normalize(fl.events) == expected_events(lay, "install_admin", None, ADMIN)
    assert len(table.PROTOCOLS["install_admin"]) == 4
    assert runs == [] and not lay.mark_file(ID).exists()
    before = tree_digest(lay.admin_dir(ID), exclude=frozenset())
    fl.clear()
    with pytest.raises(StepFailed) as exc:
        admin.install_admin(lay, ID, devtools_ref=ref)
    assert exc.value.step == "admin"
    assert fl.events == []
    assert tree_digest(lay.admin_dir(ID), exclude=frozenset()) == before


def test_admin_code_comes_from_reference_not_release(fl: FakeLayout) -> None:
    """A `df/` swapped in the release tree never reaches `admin/<id>`."""
    lay = fl.layout
    evil = lay.release_dir(ID) / "df"
    evil.mkdir(parents=True)
    (evil / "cli.py").write_text("raise SystemExit('swapped')\n")
    make_calls(fl)["install_admin"](ADMIN)
    code = lay.admin_dir(ID) / "df"
    assert sorted(os.listdir(code)) == ["__init__.py", "launcher_entry.py"]
    ref = lay.verify_dir / "devtools" / "df"
    assert tree_digest(code, exclude=frozenset()) == tree_digest(
        ref, exclude=frozenset()
    )


def test_install_admin_without_devtools_ref(fl: FakeLayout) -> None:
    """F13 carry: a profile without `devtools` has no admin code."""
    lay = fl.layout
    plan = BuildPlan(
        ID, lay.release_dir(ID), PROFILE, fake_uv(fl), Path("/g"), Path("/t")
    )
    calls = acceptance_calls(
        lay,
        ID,
        install=INSTALL,
        profile=PROFILE,
        platform=PLATFORM,
        plan=plan,
        limits=CopyLimits(),
        refs={},
        journal=lay.journal,
        run_id="run-1",
        python_key=KEY,
    )
    with pytest.raises(StepFailed) as exc:
        calls["install_admin"](ADMIN)
    assert exc.value.step == "admin"
    assert "профиль без devtools" in exc.value.detail
    assert not os.path.lexists(lay.admin_dir(ID))


def test_acceptance_calls_refuse_another_python_key(fl: FakeLayout) -> None:
    """F1: the guards' key is the key the admin Python is installed under."""
    lay = fl.layout
    plan = BuildPlan(
        ID, lay.release_dir(ID), PROFILE, fake_uv(fl), Path("/g"), Path("/t")
    )
    with pytest.raises(AssertionError):
        acceptance_calls(
            lay,
            ID,
            install=INSTALL,
            profile=PROFILE,
            platform=PLATFORM,
            plan=plan,
            limits=CopyLimits(),
            refs={},
            journal=lay.journal,
            run_id="run-1",
            python_key="cpython-3.13.1-test-triple",
        )


@pytest.mark.parametrize("umask", [0o000, 0o077])
def test_install_admin_modes_by_fchmod(fl: FakeLayout, umask: int) -> None:
    """F13: directories and executables `0755`, other files `0644`, whatever
    the umask and the source's own group/other bits."""
    ref = devtools_ref(fl)
    src = ref / "df"
    (src / "sub").mkdir()
    (src / "sub" / "tool.py").write_text("x\n")
    os.chmod(src / "sub" / "tool.py", 0o777)
    os.chmod(src / "__init__.py", 0o666)
    os.chmod(src / "sub", 0o777)
    old = os.umask(umask)
    try:
        admin.install_admin(fl.layout, ID, devtools_ref=ref)
    finally:
        os.umask(old)
    code = fl.layout.admin_dir(ID)
    assert mode(code) == mode(code / "df") == mode(code / "df" / "sub") == 0o755
    assert mode(code / "df" / "__init__.py") == 0o644
    assert mode(code / "df" / "sub" / "tool.py") == 0o755


@pytest.mark.parametrize("bad", ["link", "dir-link", "fifo", "root-link", "absent"])
def test_install_admin_copies_only_files_and_dirs(fl: FakeLayout, bad: str) -> None:
    ref = devtools_ref(fl)
    src = ref / "df"
    if bad == "link":
        (src / "x.py").symlink_to("__init__.py")
    elif bad == "dir-link":
        (src / "d").symlink_to(fl.layout.root)
    elif bad == "fifo":
        os.mkfifo(src / "p")
    elif bad == "root-link":
        shutil.rmtree(src)
        src.symlink_to(fl.layout.root)
    else:
        shutil.rmtree(src)
    with pytest.raises(StepFailed) as exc:
        admin.install_admin(fl.layout, ID, devtools_ref=ref)
    assert exc.value.step == "admin"


# ---------- admin_ready ----------


def ready_world(fl: FakeLayout) -> str:
    """An installed admin env; returns its digest."""
    install_python(fl)
    admin.install_admin(fl.layout, ID, devtools_ref=devtools_ref(fl))
    return admin.admin_digest(fl.layout, ID)


def ready(fl: FakeLayout, digest: str, python: str = PY) -> bool:
    return admin.admin_ready(
        fl.layout, ID, expected_admin_digest=digest, expected_python=python
    )


def test_admin_ready_true_on_installed_env(fl: FakeLayout) -> None:
    assert ready(fl, ready_world(fl))


@pytest.fixture()
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Paths opened during the test, via `os.open` and `io.open`/`open`."""
    seen: list[str] = []
    real_os_open, real_io_open = os.open, io.open

    def spy_os_open(path: object, *a: object, **kw: object) -> int:
        seen.append(str(path))
        return real_os_open(path, *a, **kw)  # type: ignore[arg-type]

    def spy_io_open(file: object, *a: object, **kw: object) -> object:
        seen.append(str(file))
        return real_io_open(file, *a, **kw)  # type: ignore[arg-type, call-overload]

    monkeypatch.setattr(os, "open", spy_os_open)
    monkeypatch.setattr(io, "open", spy_io_open)
    monkeypatch.setattr("builtins.open", spy_io_open)
    return seen


def test_admin_ready_uses_passed_digest_only(fl: FakeLayout, opened: list[str]) -> None:
    """rev 8 r5: the expected values are arguments; neither the registry nor
    a mark is opened, and another digest is false."""
    digest = ready_world(fl)
    (fl.layout.etc / "releases.toml").write_text("broken [\n")
    fl.layout.mark_file(ID).write_text("broken [\n")
    opened.clear()
    assert ready(fl, digest)
    assert not ready(fl, "sha256:" + "0" * 64)
    assert not any(p.endswith("releases.toml") for p in opened)
    assert not any(str(fl.layout.marks_dir) in p for p in opened)


def test_admin_ready_digest_excludes_nothing(fl: FakeLayout) -> None:
    """F12: a file named `release.json` in the admin tree counts (the mark's
    digest excludes nothing), so `admin_digest` and the route agree."""
    ready_world(fl)
    (fl.layout.admin_dir(ID) / "release.json").write_text("{}\n")
    whole = tree_digest(fl.layout.admin_dir(ID), exclude=frozenset())
    assert admin.admin_digest(fl.layout, ID) == whole
    assert ready(fl, whole)


@pytest.mark.parametrize(
    "spoil",
    ["no-admin", "admin-link", "fifo-in-admin", "no-python", "python-dir", "py-link"],
)
def test_admin_ready_false(fl: FakeLayout, spoil: str) -> None:
    lay = fl.layout
    digest = ready_world(fl)
    interpreter = admin.admin_python_path(lay, PY, PLATFORM)
    if spoil == "no-admin":
        shutil.rmtree(lay.admin_dir(ID))
    elif spoil == "admin-link":
        shutil.move(lay.admin_dir(ID), lay.admin / "moved")
        lay.admin_dir(ID).symlink_to(lay.admin / "moved")
    elif spoil == "fifo-in-admin":
        os.mkfifo(lay.admin_dir(ID) / "p")
    elif spoil == "no-python":
        interpreter.unlink()
    elif spoil == "python-dir":
        interpreter.unlink()
        interpreter.mkdir()
    else:
        interpreter.rename(interpreter.with_name("real"))
        interpreter.symlink_to("real")
    assert not ready(fl, digest)


def test_admin_ready_other_python_false(fl: FakeLayout) -> None:
    assert not ready(fl, ready_world(fl), python="3.13.2")


def test_admin_ready_untrusted_chain_false(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    digest = ready_world(fl)
    monkeypatch.setattr(plat, "trusted_path_problems", lambda _p: ["x"])
    assert not ready(fl, digest)


@pytest.mark.parametrize(
    "spoil", ["world-file", "group-dir", "foreign-owner", "inner-link", "setuid", "acl"]
)
def test_admin_ready_false_on_untrusted_code(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout, spoil: str
) -> None:
    """I-1(b): `A` covers the whole admin code tree, not only its digest."""
    lay = fl.layout
    ready_world(fl)
    pkg = lay.admin_dir(ID) / "df"
    target = next(p for p in sorted(pkg.iterdir()) if p.is_file())
    if spoil == "world-file":
        target.chmod(0o666)
    elif spoil == "group-dir":
        pkg.chmod(0o775)
    elif spoil == "foreign-owner":
        fl.set_owner(target, BUILD_UID)
    elif spoil == "inner-link":
        (pkg / "extra").symlink_to(target.name)
    elif spoil == "setuid":
        target.chmod(0o4755)
    else:
        real = acl.acl_present_path
        monkeypatch.setattr(acl, "acl_present_path", lambda p: p == target or real(p))
    digest = admin.admin_digest(lay, ID)  # the digest agrees: only trust is wrong
    assert not ready(fl, digest)
    assert admin.admin_code_problems(lay, ID)


def test_admin_ready_checks_the_admin_dir_chain(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    digest = ready_world(fl)
    admin_dir = fl.layout.admin_dir(ID)
    monkeypatch.setattr(
        plat, "trusted_path_problems", lambda p: ["acl"] if p == admin_dir else []
    )
    assert not ready(fl, digest)


@pytest.mark.parametrize(
    "spoil",
    [
        "g+w-file",
        "o+w-dir",
        "foreign-owner",
        "setuid",
        "acl",
        "abs-link",
        "escaping-link",
    ],
)
def test_admin_ready_checks_the_whole_python_tree(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout, spoil: str
) -> None:
    """M-4: `A` applies the install rule to the whole admin Python tree."""
    digest = ready_world(fl)
    assert ready(fl, digest)  # positive control
    tree = fl.layout.admin_python / KEY
    lib = tree / "lib"
    lib.mkdir(mode=0o755)
    target = lib / "os.py"
    target.write_text("x\n")
    assert ready(fl, digest)  # a clean extra file is fine
    if spoil == "g+w-file":
        target.chmod(0o664)
    elif spoil == "o+w-dir":
        lib.chmod(0o757)
    elif spoil == "foreign-owner":
        fl.set_owner(target, BUILD_UID)
    elif spoil == "setuid":
        target.chmod(0o4755)
    elif spoil == "acl":
        real = acl.acl_present_path
        monkeypatch.setattr(acl, "acl_present_path", lambda p: p == target or real(p))
    elif spoil == "abs-link":
        (lib / "l").symlink_to(target)
    else:
        (lib / "l").symlink_to("../../..")
    assert not ready(fl, digest)
    assert admin.admin_python_problems(fl.layout, PY, PLATFORM)


@pytest.mark.parametrize("which", ["admin", "admin_python"])
def test_admin_ready_refuses_a_link_on_the_chain(fl: FakeLayout, which: str) -> None:
    """M-5: `A` refuses what the entry refuses — a link on the chain."""
    lay = fl.layout
    digest = ready_world(fl)
    real = getattr(lay, which)
    moved = real.with_name(real.name + "-real")
    os.rename(real, moved)
    os.symlink(moved, real)
    assert admin.chain_link_problems(real / "x")
    assert not ready(fl, digest)


def test_admin_code_refuses_bytecode_and_install_skips_it(fl: FakeLayout) -> None:
    """n-5: `__pycache__`/`*.pyc` (casefold) never enter the admin code."""
    lay = fl.layout
    ref = devtools_ref(fl)
    (ref / "df" / "__pycache__").mkdir(exist_ok=True)
    (ref / "df" / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"\0")
    (ref / "df" / "Y.PYC").write_bytes(b"\0")
    install_python(fl)
    admin.install_admin(lay, ID, devtools_ref=ref)
    pkg = lay.admin_dir(ID) / "df"
    assert sorted(p.name for p in pkg.iterdir()) == ["__init__.py", "launcher_entry.py"]
    assert admin.admin_code_problems(lay, ID) == []
    (pkg / "__pycache__").mkdir()
    assert admin.admin_code_problems(lay, ID)
    digest = admin.admin_digest(lay, ID)
    assert not ready(fl, digest)


def test_admin_code_problems_clean_tree(fl: FakeLayout) -> None:
    ready_world(fl)
    assert admin.admin_code_problems(fl.layout, ID) == []


def test_admin_ready_io_error_is_step_failed(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    digest = ready_world(fl)

    def broken(_layout: object, _id: str) -> str:
        raise PermissionError(13, "denied")

    monkeypatch.setattr(admin, "admin_digest", broken)
    with pytest.raises(StepFailed) as exc:
        ready(fl, digest)
    assert exc.value.step == "facts"


# ---------- the command ----------


def entry_source(fl: FakeLayout, text: str | None = None) -> Path:
    """The release's own entry module `admin/<id>/df/launcher_entry.py`
    (default: a copy of the dev module)."""
    source = fl.layout.admin_dir(ID) / "df" / "launcher_entry.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    if text is None:
        text = Path(launcher_entry.__file__).read_text(encoding="utf-8")
    source.write_text(text)
    return source


def write_command(fl: FakeLayout, target: str) -> None:
    """Task 9's `write_atomic` of the launcher files (the generated texts)."""
    lay = fl.layout
    if target == "bin/darkfactory-entry.py":
        if not admin.entry_source(lay, ID).exists():
            entry_source(fl)
        fsutil.write_atomic(lay.entry, admin.release_entry_text(lay, ID), 0o644)
    elif target == "bin/darkfactory":
        fsutil.write_atomic(lay.launcher, admin.launcher_text(lay, PY, PLATFORM), 0o755)
    else:
        raise AssertionError(target)


def test_launcher_text_quotes_paths(tmp_path: Path) -> None:
    root = Path(os.path.realpath(tmp_path)) / "a b"
    root.mkdir()
    lay = Layout(root)
    text = admin.launcher_text(lay, PY, PLATFORM)
    py = admin.admin_python_path(lay, PY, PLATFORM)
    assert text == f"#!/bin/sh\nexec '{py}' -I -B -S '{lay.entry}' \"$@\"\n"


def test_entry_text_sets_the_root(fl: FakeLayout) -> None:
    text = entry_text(fl.layout)
    assert f"_ROOT = {str(fl.layout.root)!r}\n" in text
    assert '_ROOT = "/"\n' not in text


@pytest.mark.parametrize("trusted", [False, True])
def test_usr_local_link_only_when_trusted(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout, trusted: bool
) -> None:
    """The route `launcher` with the real `link_command`: the guard decides,
    `link_command` reads no policy itself, and `L` holds either way (the
    full path is printed by Task 9's install; F4)."""
    lay = fl.layout
    inside: list[bool] = []

    def policy(path: Path) -> list[str]:
        assert not inside, "link_command read the trusted-path policy"
        return [] if trusted or path != lay.local_bin else ["untrusted"]

    def link(target: str) -> None:
        inside.append(True)
        try:
            admin.link_command(lay)
        finally:
            inside.pop()

    monkeypatch.setattr(plat, "trusted_path_problems", policy)
    calls = StandIns(fl).calls(
        write_atomic=lambda t: write_command(fl, t), link_command=link
    )
    fl.clear()
    run_route(lay, ID, "launcher", calls=calls, python_key=KEY, build_uid=BUILD_UID)
    linked = ("call", "link_command", "/usr/local/bin/darkfactory") in fl.events
    assert linked is trusted
    link_path = lay.local_bin / "darkfactory"
    assert os.path.lexists(link_path) is trusted
    if trusted:
        assert os.readlink(link_path) == str(lay.launcher)
    assert admin.launcher_ready(lay, ID, python=PY)


def test_launcher_never_named_df(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    lay = fl.layout
    write_command(fl, "bin/darkfactory-entry.py")
    write_command(fl, "bin/darkfactory")
    admin.link_command(lay)
    assert sorted(os.listdir(lay.local_bin)) == ["darkfactory"]
    assert sorted(os.listdir(lay.bin_dir)) == ["darkfactory", "darkfactory-entry.py"]
    assert lay.launcher.name == "darkfactory"


def test_link_command_events_and_replace(fl: FakeLayout) -> None:
    lay = fl.layout
    old = lay.local_bin / "darkfactory"
    old.symlink_to("/somewhere/else")
    fl.clear()
    admin.link_command(lay)
    assert fl.events == [("renamed", old), ("sync_dir", lay.local_bin)]
    assert os.readlink(old) == str(lay.launcher)
    assert sorted(os.listdir(lay.local_bin)) == ["darkfactory"]


def test_link_command_reuses_one_temporary_name(fl: FakeLayout) -> None:
    """C13 (as `activate`'s m4): one fixed temporary name; a leftover from a
    killed run is replaced, not accumulated."""
    lay = fl.layout
    leftover = lay.local_bin / admin.LINK_TMP
    leftover.symlink_to("/stale")
    admin.link_command(lay)
    admin.link_command(lay)
    assert sorted(os.listdir(lay.local_bin)) == ["darkfactory"]
    assert os.readlink(lay.local_bin / "darkfactory") == str(lay.launcher)


def test_link_command_over_a_directory_refused(fl: FakeLayout) -> None:
    lay = fl.layout
    (lay.local_bin / "darkfactory").mkdir()
    with pytest.raises(StepFailed) as exc:
        admin.link_command(lay)
    assert exc.value.step == "launcher"
    assert sorted(os.listdir(lay.local_bin)) == ["darkfactory"]


@pytest.mark.parametrize(
    "spoil",
    [
        "none",
        "no-entry",
        "entry-text",
        "entry-mode",
        "command-text",
        "command-mode",
        "command-link",
        "command-owner",
        "no-link",
        "link-text",
        "link-file",
    ],
)
def test_launcher_ready(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout, spoil: str
) -> None:
    """F2: `L` is the generated files, root's, with their modes, and the
    public link when `/usr/local/bin` is trusted."""
    lay = fl.layout
    write_command(fl, "bin/darkfactory-entry.py")
    write_command(fl, "bin/darkfactory")
    admin.link_command(lay)
    link = lay.local_bin / "darkfactory"
    if spoil == "no-entry":
        lay.entry.unlink()
    elif spoil == "entry-text":
        fsutil.write_atomic(lay.entry, admin.release_entry_text(lay, ID) + "#\n", 0o644)
    elif spoil == "entry-mode":
        lay.entry.chmod(0o664)
    elif spoil == "command-text":
        fsutil.write_atomic(lay.launcher, admin.launcher_text(lay, "3.13.2", PLATFORM))
        lay.launcher.chmod(0o755)
    elif spoil == "command-mode":
        lay.launcher.chmod(0o775)
    elif spoil == "command-link":
        lay.launcher.rename(lay.bin_dir / "real")
        lay.launcher.symlink_to("real")
    elif spoil == "command-owner":
        fl.set_owner(lay.launcher, BUILD_UID)
    elif spoil == "no-link":
        link.unlink()
    elif spoil == "link-text":
        link.unlink()
        link.symlink_to("/elsewhere")
    elif spoil == "link-file":
        link.unlink()
        link.write_text("x\n")
    assert admin.launcher_ready(lay, ID, python=PY) is (spoil == "none")


@pytest.mark.parametrize("which", ["entry", "launcher"])
def test_launcher_ready_requires_trusted_paths(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout, which: str
) -> None:
    """M-3: an ACL or an untrusted chain on either command file makes `L` false."""
    lay = fl.layout
    write_command(fl, "bin/darkfactory-entry.py")
    write_command(fl, "bin/darkfactory")
    admin.link_command(lay)
    assert admin.launcher_ready(lay, ID, python=PY)  # positive control
    bad = getattr(lay, which)
    monkeypatch.setattr(
        plat, "trusted_path_problems", lambda p: ["acl"] if p == bad else []
    )
    assert not admin.launcher_ready(lay, ID, python=PY)


def test_launcher_ready_ignores_link_when_untrusted(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    lay = fl.layout
    write_command(fl, "bin/darkfactory-entry.py")
    write_command(fl, "bin/darkfactory")
    monkeypatch.setattr(
        plat, "trusted_path_problems", lambda p: ["x"] if p == lay.local_bin else []
    )
    assert admin.launcher_ready(lay, ID, python=PY)


def test_launcher_ready_other_python_false(fl: FakeLayout) -> None:
    write_command(fl, "bin/darkfactory-entry.py")
    write_command(fl, "bin/darkfactory")
    admin.link_command(fl.layout)
    assert not admin.launcher_ready(fl.layout, ID, python="3.13.2")


def test_launcher_ready_compares_with_the_release_source(fl: FakeLayout) -> None:
    """M-1: the entry is the activated release's own module, not the running
    `df` — an entry from another source is not `L`; the same is."""
    lay = fl.layout
    write_command(fl, "bin/darkfactory-entry.py")
    write_command(fl, "bin/darkfactory")
    admin.link_command(lay)
    assert admin.launcher_ready(lay, ID, python=PY)
    entry_source(fl, 'release = "N+1"\n_ROOT = "/"\n')
    assert not admin.launcher_ready(lay, ID, python=PY)
    want = f'release = "N+1"\n_ROOT = {str(lay.root)!r}\n'
    assert admin.release_entry_text(lay, ID) == want
    write_command(fl, "bin/darkfactory-entry.py")
    assert lay.entry.read_text() == want
    assert admin.launcher_ready(lay, ID, python=PY)


@pytest.mark.parametrize("bad", ["absent", "no-root-line", "link"])
def test_bad_entry_source_is_not_ready_and_refuses_launcher(
    fl: FakeLayout, bad: str
) -> None:
    """M13: a source without exactly one `_ROOT` line (or none, or a link) —
    `L` false (no exception) and the launcher's text `StepFailed("launcher")`."""
    lay = fl.layout
    write_command(fl, "bin/darkfactory-entry.py")
    write_command(fl, "bin/darkfactory")
    source = lay.admin_dir(ID) / "df" / "launcher_entry.py"
    if bad == "absent":
        source.unlink()
    elif bad == "no-root-line":
        source.write_text("x = 1\n")
    else:
        real = source.with_name("real.py")
        source.rename(real)
        source.symlink_to(real.name)
    assert not admin.launcher_ready(lay, ID, python=PY)
    with pytest.raises(StepFailed) as exc:
        admin.release_entry_text(lay, ID)
    assert exc.value.step == "launcher"


def test_launcher_ready_source_io_error_is_step_failed(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    write_command(fl, "bin/darkfactory-entry.py")
    write_command(fl, "bin/darkfactory")

    def broken(_layout: object, _source: object = None) -> str:
        raise PermissionError(13, "denied")

    monkeypatch.setattr(admin, "entry_text", broken)
    with pytest.raises(StepFailed) as exc:
        admin.launcher_ready(fl.layout, ID, python=PY)
    assert exc.value.step == "facts"


# ---------- Step 1: premises of the pinned uv ----------


def pinned_uv() -> Path:
    """The pinned uv on PATH, else skip (the premise is per uv version)."""
    found = shutil.which("uv")
    if found is None:
        pytest.skip("uv не найден")
    out = subprocess.run(
        [found, "--version"], capture_output=True, text=True, check=False
    ).stdout.split()
    if out[1:2] != [UV_PINNED_VERSION]:
        pytest.skip(f"uv {out[1:2]} — не закреплённая {UV_PINNED_VERSION}")
    return Path(found)


#: A Python the pinned uv's download metadata knows (measured, Task 8a
#: pre-flight §2.2).
PROBE_PYTHON = "3.12.13"


def _tar_gz() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"not python\n"
        info = tarfile.TarInfo("python/README")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_uv_refuses_corrupt_download(tmp_path: Path) -> None:
    """Spec 3.9 premise (Step 1): uv checks the download's SHA-256. A local
    mirror serves a well-formed but different archive; uv refuses it. Only
    127.0.0.1 is contacted."""
    uv = pinned_uv()
    body = _tar_gz()
    paths: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            paths.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        home = tmp_path / "home"
        env = {
            "UV_PYTHON_INSTALL_DIR": str(tmp_path / ".staging"),
            "UV_NO_CONFIG": "1",
            "UV_PYTHON_DOWNLOADS": "automatic",
            "HOME": str(home),
            "UV_CACHE_DIR": str(home / "cache"),
            "PATH": "/usr/bin:/bin",
            "UV_PYTHON_INSTALL_MIRROR": f"http://127.0.0.1:{server.server_port}",
        }
        key = build_mod.python_key(PROBE_PYTHON, PLATFORM)
        argv = [str(uv), "python", "install", "--no-bin", key]
        done = subprocess.run(
            argv, env=env, capture_output=True, timeout=120, check=False
        )
    finally:
        server.shutdown()
        server.server_close()
    assert done.returncode != 0
    assert b"Hash mismatch" in done.stderr, done.stderr
    assert len(paths) >= 1
    assert not (tmp_path / ".staging" / key).exists()


@pytest.mark.skipif(
    os.environ.get("DF_NETWORK_TESTS") != "1",
    reason="сеть: DF_NETWORK_TESTS=1 (маркер network — Task 13)",
)
def test_admin_python_relocation_precondition(
    monkeypatch: pytest.MonkeyPatch, fake_layout: FakeLayout
) -> None:
    """Spec 3.9 premise (Step 1): the real `install_admin_python` with the
    pinned uv; after the rename (and `.staging` gone) `sys.prefix` is the new
    path and no link in `admin-python/` points into `.staging`."""
    uv = pinned_uv()
    lay = fake_layout.layout
    fake_layout.make_dirs()
    monkeypatch.setattr(plat, "trusted_path_problems", lambda _p: [])
    interpreter = admin.install_admin_python(
        lay, PROBE_PYTHON, uv=uv, platform=PLATFORM
    )
    staging = lay.admin_python / ".staging"
    shutil.rmtree(staging)
    key = lay.admin_python / build_mod.python_key(PROBE_PYTHON, PLATFORM)
    code = "import sys; print(sys.prefix); print(sys.base_prefix)"
    out = subprocess.run(
        [str(interpreter), "-I", "-B", "-S", "-c", code],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\n")
    assert out[:2] == [str(key), str(key)]
    for dirpath, dirs, files in os.walk(lay.admin_python):
        for name in (*dirs, *files):
            path = Path(dirpath) / name
            if path.is_symlink():
                target = os.path.normpath(path.parent / os.readlink(path))
                assert not target.startswith(str(staging)), path
