"""Tests for the route interpreter, guards and call table (Task 7, part D1).

Every route runs through the real `run_route` and real guards over a
`fake_layout`. Every call Task 7 owns (`acceptance_calls`) is real (7-D2);
only the leaves of later tasks (`LATER_LEAVES`) are stand-ins generated from
`leaf_variants` (F3): each performs exactly its protocol's steps through the
patched lower operations (`fault_point`, `sync_dir`, `sync_tree`, `run_as`,
`reap_user_processes`) and changes only the objects its change steps name.
`run_build` is real with F30's fakes: `verify_build_user`/`whoami_as`
replaced and a local `uv` script as the plan's uv (no network). Task 9 reruns
the table with all-real calls.

The expected sequence of a route is computed from the data alone: the
unfolding of `ROUTES` (`unfold_route`), each step kept when its guards hold in
a small facet model of the prepared state, each leaf expanded to its protocol
steps with concrete objects. Events are compared as sequences: order,
multiplicity and objects.
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import os
import re
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from df import accept, fsutil, routes
from df import build as build_mod
from df import install_table as table
from df import platform as plat
from df.accept import AcceptMark, Facts, load_mark
from df.build import BuildPlan
from df.config import (
    Account,
    InstallConfig,
    ReleaseEntry,
    ReleasesConfig,
    config_digest,
    dump_accounts,
    dump_releases,
)
from df.digest import TreeError, tree_digest
from df.journal import StepFailed, read_journal
from df.lockset import DECLARED_LIMIT
from df.manifest import Profile, RepoPin
from df.paths import Layout
from df.receive import CopyLimits
from df.routes import (
    GUARDS,
    LATER_LEAVES,
    TARGETS,
    RouteContext,
    acceptance_calls,
    implementation,
    route_action,
    route_guard,
    run_route,
)
from tests.df.conftest import BUILD_UID, FakeLayout

ID = "rel1"
PY = "3.13.1"
KEY = "cpython-3.13.1-test-triple"
BOOT = "boot-1"
UV = "uv 0.9.2"
Event = tuple[object, ...]
StrPath = str | os.PathLike[str]
State = dict[str, object]

# ---------- the facet model of guard state ----------

#: Facets of the prepared state and their values; guards read them.
FACETS: Mapping[str, tuple[object, ...]] = {
    "r": ("absent", "root", "builder"),
    "S": ("none", "container", "tree"),
    "admin": (False, True),
    "m": (False, True),
    "reg": (False, True),
    "py": (False, True),
    "pystaging": (False, True),
    "local_bin": (False, True),
}
FACET_OF_GUARD: Mapping[str, str] = {
    "r=root": "r",
    "r=builder": "r",
    "S=tree": "S",
    "S=container": "S",
    "admin=present": "admin",
    "m=present": "m",
    "not reg": "reg",
    "py=absent": "py",
    "py-staging=present": "pystaging",
    "local_bin=trusted": "local_bin",
}
GUARD_MODEL: Mapping[str, Callable[[State], bool]] = {
    "r=root": lambda s: s["r"] == "root",
    "r=builder": lambda s: s["r"] == "builder",
    "S=tree": lambda s: s["S"] == "tree",
    "S=container": lambda s: s["S"] == "container",
    "admin=present": lambda s: bool(s["admin"]),
    "m=present": lambda s: bool(s["m"]),
    "not reg": lambda s: not s["reg"],
    "py=absent": lambda s: not s["py"],
    "py-staging=present": lambda s: bool(s["pystaging"]),
    "local_bin=trusted": lambda s: bool(s["local_bin"]),
}
#: What a leaf on a target changes in the facets (no entry: nothing).
EFFECTS: Mapping[tuple[str, str], State] = {
    ("prepare_dir", "releases/<id>"): {"r": "builder"},
    ("seal", "releases/<id>"): {"r": "absent", "S": "tree"},
    ("accept_copy", "releases/<id>"): {"r": "root"},
    ("remove_own_tree", "releases/<id>"): {"r": "absent"},
    ("cleanup_tree", "releases/<id>"): {"r": "absent"},
    ("cleanup_tree", "staging/<id>/tree"): {"S": "container"},
    ("remove_empty_dir", "staging/<id>"): {"S": "none"},
    ("remove_own_tree", "admin/<id>"): {"admin": False},
    ("install_admin", "admin/<id>"): {"admin": True},
    ("drop_mark", "accept/<id>.toml"): {"m": False},
    ("remove_own_tree", "admin-python/.staging"): {"pystaging": False},
    ("install_admin_python", "admin-python/cpython-…"): {
        "py": True,
        "pystaging": True,
    },
}
#: The prepared state each route starts from (its row's objects), before the
#: facets of its guards; a list gives one case per base. `stage`: the valid
#: mark the row holds (the real `write_mark` and `tree_digest` read it).
BASES: Mapping[str, tuple[State, ...]] = {
    "build": ({"r": "builder"},),
    "seal": ({"r": "builder"}, {"r": "builder", "S": "container"}),
    "copy": ({"S": "tree", "m": True, "stage": "built"},),
    "copy-partial": ({"r": "root", "S": "tree", "m": True, "stage": "built"},),
    "verify": ({"r": "root", "m": True, "stage": "copied"},),
    "admin": ({"r": "root", "m": True, "stage": "verified"},),
    "register": ({"r": "root"},),
    "commit-config": ({"pending": True},),
    "rollback": ({"pending": True},),
}
DEFAULT: State = {k: v[0] for k, v in FACETS.items()} | {
    "pending": False,
    "stage": None,
}


def route_guards(name: str) -> list[str]:
    """Facets read by the guards of a route, in first-use order."""
    out: list[str] = []
    for guards, _call, _target in table.unfold_route(name):
        for g in guards:
            if FACET_OF_GUARD[g] not in out:
                out.append(FACET_OF_GUARD[g])
    return out


def route_states(name: str) -> list[State]:
    """Every base of the route × every combination of its guards' facets."""
    facets = route_guards(name)
    out: list[State] = []
    for base in BASES.get(name, ({},)):
        for values in itertools.product(*(FACETS[f] for f in facets)):
            out.append(DEFAULT | base | dict(zip(facets, values, strict=True)))
    return out


def state_id(state: State) -> str:
    return ",".join(f"{k}={v}" for k, v in state.items() if DEFAULT.get(k) != v)


CASES = [
    pytest.param(name, state, id=f"{name}[{state_id(state)}]")
    for name in table.ROUTES
    for state in route_states(name)
]


# ---------- objects of protocol steps ----------

#: Fault kinds of fsutil for the protocols' change words.
FAULT_KIND = {"rename": "renamed", "unlink": "unlinked"}


#: Every target text of `ROUTES` -> its object, written from the spec
#: independently of `routes.TARGETS` (I2); `None`: no object.
OBJECTS: Mapping[str, Callable[[Layout], Path] | None] = {
    "releases/<id>": lambda L: L.root / "opt/df/releases" / ID,
    "от `df-build`": None,
    "df-build": None,
    "accept/<id>.toml: built": lambda L: (
        L.root / f"var/lib/df/install/accept/{ID}.toml"
    ),
    "accept/<id>.toml: copied": lambda L: (
        L.root / f"var/lib/df/install/accept/{ID}.toml"
    ),
    "accept/<id>.toml: max(стадия, verified)": lambda L: (
        L.root / f"var/lib/df/install/accept/{ID}.toml"
    ),
    "accept/<id>.toml: admin": lambda L: (
        L.root / f"var/lib/df/install/accept/{ID}.toml"
    ),
    "accept/<id>.toml": lambda L: L.root / f"var/lib/df/install/accept/{ID}.toml",
    "releases/<id> = отметке": lambda L: L.root / "opt/df/releases" / ID,
    "build_home/tmp": lambda L: L.build_home / "tmp",
    "build_home/cache": lambda L: L.build_home / "cache",
    "admin-python/.staging": lambda L: L.root / "opt/df/admin-python/.staging",
    "admin-python/cpython-…": lambda L: L.root / "opt/df/admin-python" / KEY,
    "admin/<id>": lambda L: L.root / "opt/df/admin" / ID,
    "staging/<id>/tree": lambda L: L.root / "opt/df/staging" / ID / "tree",
    "staging/<id>": lambda L: L.root / "opt/df/staging" / ID,
    "releases/<id>/release.json": lambda L: (
        L.root / "opt/df/releases" / ID / "release.json"
    ),
    "/etc/df/releases.toml": lambda L: L.root / "etc/df/releases.toml",
    "/etc/df/install.toml": lambda L: L.root / "etc/df/install.toml",
    "/opt/df/current": lambda L: L.root / "opt/df/current",
    "bin/darkfactory-entry.py": lambda L: L.root / "opt/df/bin/darkfactory-entry.py",
    "bin/darkfactory": lambda L: L.root / "opt/df/bin/darkfactory",
    "/usr/local/bin/darkfactory": lambda L: L.root / "usr/local/bin/darkfactory",
    "pending.toml": lambda L: L.root / "var/lib/df/install/pending.toml",
}


def obj(lay: Layout, target: str) -> Path:
    """The object of a target from the independent table `OBJECTS`."""
    get = OBJECTS[target]
    assert get is not None, target
    return get(lay)


def change_path(
    lay: Layout, proto: str, variant: str | None, i: int, target: str
) -> Path:
    """The object a change step names: the target's, except where the
    protocol's text names another (seal's container and tree, the
    quarantine entry, the admin Python's staging)."""
    special: Mapping[tuple[str, str | None, int], Callable[[], Path]] = {
        ("seal", "new", 0): lambda: lay.staging_dir(ID),
        ("seal", "new", 1): lambda: lay.staging_tree(ID),
        ("seal", "reuse", 0): lambda: lay.staging_tree(ID),
        ("cleanup_tree", "quarantine", 1): lambda: lay.quarantine / f"{ID}.1",
        ("install_admin_python", None, 0): lambda: lay.admin_python / ".staging",
    }
    get = special.get((proto, variant, i))
    return get() if get is not None else obj(lay, target)


def dir_paths(lay: Layout, arg: str, target: str) -> list[Path]:
    """Directories a `sync_dir(<arg>)` step syncs (`staging`: both)."""
    if arg == "parent":
        return [obj(lay, target).parent]
    named: Mapping[str, list[Path]] = {
        "release_root": [lay.release_dir(ID)],
        "releases": [lay.releases],
        "staging": [lay.staging, lay.staging_dir(ID)],
        "quarantine": [lay.quarantine],
        "admin": [lay.admin],
        "admin_python": [lay.admin_python],
        "opt_df": [lay.opt],
        "local_bin": [lay.local_bin],
    }
    return named[arg]


def tree_path(lay: Layout, arg: str, target: str) -> Path:
    if arg == "py":
        return lay.admin_python / ".staging" / KEY
    return obj(lay, target)


def parse(token: str) -> tuple[str, str]:
    """A token of `leaf_variants` as (op, arg)."""
    if token == "run_as(df-build)+":
        return "builder", "df-build"
    m = re.fullmatch(r"(sync_dir|sync_tree|process)\((.+)\)", token)
    return (m.group(1), m.group(2)) if m else ("change", token)


def expected_events(
    lay: Layout, leaf: str, variant: str | None, target: str
) -> list[Event]:
    """The concrete events of one leaf call in `variant`."""
    proto = table.WRAPS.get(leaf, leaf)
    out: list[Event] = []
    for i, token in enumerate(table.leaf_variants(leaf)[variant]):
        op, arg = parse(token)
        if op == "change":
            out.append(
                (FAULT_KIND.get(arg, arg), change_path(lay, proto, variant, i, target))
            )
        elif op == "sync_dir":
            out += [("sync_dir", p) for p in dir_paths(lay, arg, target)]
        elif op == "sync_tree":
            out.append(("sync_tree", tree_path(lay, arg, target)))
        elif op == "process":
            out.append(("process", "df-build"))
        else:
            out.append(("run_as+", "df-build"))
    return out


def normalize(events: list[Event]) -> list[Event]:
    """Recorded events as compared: ownership changes dropped (their fault
    point is the event), consecutive runs as `df-build` collapsed."""
    out: list[Event] = []
    for raw in events:
        if raw[0] in ("fchown", "chown") and len(raw) == 3:
            continue
        e = ("run_as+", raw[1]) if raw[0] == "run_as" else raw
        if e[0] == "run_as+" and out and out[-1] == e:
            continue
        out.append(e)
    return out


# ---------- stand-ins ----------


class StandIns:
    """Table-generated stand-ins of route calls over one fake layout (F3)."""

    def __init__(self, fl: FakeLayout) -> None:
        self.fl = fl
        self.lay = fl.layout

    def stand_in(
        self,
        name: str,
        variant: str | None = None,
        *,
        target: str | None = None,
        extra: tuple[str, ...] = (),
    ) -> routes.Call:
        """A call that emits exactly `leaf_variants(name)[variant]` (chosen by
        the state when `None`). `target` and `extra` make twins only: the
        object resolved from another target, extra steps appended."""

        def call(step_target: str) -> None:
            t = target or step_target
            v = variant if variant is not None else self.variant(name, t)
            tokens = (*table.leaf_variants(name)[v], *extra)
            proto = table.WRAPS.get(name, name)
            for i, token in enumerate(tokens):
                self.step(proto, v, i, token, t)

        return call

    def calls(self, **override: routes.Call) -> dict[str, routes.Call]:
        """Every leaf and wrapper, each call marked in events: the real call
        of `acceptance_calls`, a stand-in for a later leaf, or `override`."""
        names = [*table.route_leaves(), *table.WRAPS]
        real = make_calls(self.fl)
        made = {n: override.get(n) or real.get(n) or self.stand_in(n) for n in names}
        return {n: self.marked(n, f) for n, f in made.items()}

    def marked(self, name: str, f: routes.Call) -> routes.Call:
        def call(target: str) -> None:
            self.fl.events.append(("call", name, target))
            f(target)

        return call

    def variant(self, name: str, target: str) -> str | None:
        """The variant the state selects (protocols with variants only)."""
        variants = list(table.leaf_variants(name))
        if variants == [None]:
            return None
        if name == "seal":
            return "reuse" if os.path.lexists(self.lay.staging_dir(ID)) else "new"
        path = obj(self.lay, target)
        if name == "cleanup_build_home":
            return "tree" if os.path.lexists(path) else "absent"
        if not os.path.lexists(path):
            return "absent"
        return "tree" if accept.is_real_dir(path) else "link"

    def step(self, proto: str, variant: str | None, i: int, token: str, t: str) -> None:
        op, arg = parse(token)
        lay = self.lay
        if op == "change":
            path = change_path(lay, proto, variant, i, t)
            self.change(proto, arg, path, t)
            fsutil.fault_point(FAULT_KIND.get(arg, arg), path)  # type: ignore[arg-type]
        elif op == "sync_dir":
            for p in dir_paths(lay, arg, t):
                fsutil.sync_dir(p)
        elif op == "sync_tree":
            fsutil.sync_tree(tree_path(lay, arg, t))
        elif op == "process":
            plat.reap_user_processes(plat.BUILD_USER)
        else:
            plat.run_as(
                plat.BUILD_USER,
                ["/usr/bin/true"],
                env={"PATH": "/usr/bin:/bin", "LANG": "C"},
                cwd=lay.build_home,
                network=False,
                timeout=10.0,
            )

    def change(self, proto: str, kind: str, path: Path, t: str) -> None:
        """The declared change, on its object only; an absent object fails."""
        lay = self.lay
        if kind == "created":
            path.mkdir(mode=0o711 if proto == "seal" else 0o700, exist_ok=True)
            if proto == "install_admin_python":
                (path / KEY).mkdir(exist_ok=True)
        elif kind == "unlink":
            if accept.is_real_dir(path):
                shutil.rmtree(path)
            else:
                os.unlink(path)
        elif kind == "chown":
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fchown(fd, self.fl.build_uid, -1)
            finally:
                os.close(fd)
        elif kind == "chmod":
            os.chmod(path, 0o755)
        elif proto == "seal":
            os.rename(lay.release_dir(ID), path)
        elif proto == "cleanup_tree":
            os.rename(obj(lay, t), path)
        elif proto == "install_admin_python":
            os.rename(lay.admin_python / ".staging" / KEY, path)
        elif proto in ("activate", "link_command"):
            tmp = path.with_name(f".{path.name}.tmp")
            os.symlink(f"releases/{ID}", tmp)
            os.replace(tmp, path)
        else:  # write_atomic
            path.write_text("stand-in\n")


# ---------- preparing and running ----------


def reset(fl: FakeLayout) -> None:
    """An empty layout again (between the cases of one test)."""
    shutil.rmtree(fl.layout.root)
    fl.layout.root.mkdir()
    fl.events.clear()
    fl.owners.clear()


def prepare(fl: FakeLayout, monkeypatch: pytest.MonkeyPatch, state: State) -> None:
    """Objects of the state; the trusted-path policy of `local_bin` faked."""
    lay = fl.layout
    fl.make_dirs()
    write_accounts(fl)
    if state["r"] == "root":
        (lay.release_dir(ID) / "src").mkdir(parents=True)
    elif state["r"] == "builder":
        fl.builder_dir(lay.release_dir(ID))
    if state["S"] != "none":
        lay.staging_dir(ID).mkdir(mode=0o711)
    if state["S"] == "tree":
        fl.builder_dir(lay.staging_tree(ID))
    if state["admin"]:
        (lay.admin_dir(ID) / "df").mkdir(parents=True)
    if state["m"]:
        write_state_mark(fl, state["stage"])
    if state["reg"]:
        register(fl)
    if state["py"]:
        (lay.admin_python / KEY).mkdir()
    if state["pystaging"]:
        (lay.admin_python / ".staging").mkdir()
    if state["pending"]:
        lay.pending.write_text("raw\n")
    trusted = bool(state["local_bin"])
    monkeypatch.setattr(
        plat, "trusted_path_problems", lambda _p: [] if trusted else ["untrusted"]
    )


def write_state_mark(fl: FakeLayout, stage: object) -> None:
    """The row's valid mark (`stage`), else a raw mark file (F32: `m=present`
    is the file's existence)."""
    lay = fl.layout
    if stage is None:
        lay.mark_file(ID).write_text("raw\n")
        return
    copied = stage != "built"
    digest = routes.release_digest(lay.release_dir(ID)) if copied else None
    mark = AcceptMark(
        config_digest(INSTALL),
        stage,
        BOOT,
        digest,
        None,
        None,
        UV,  # type: ignore[arg-type]
    )
    accept.write_mark(lay, ID, mark)


def write_accounts(fl: FakeLayout) -> None:
    """A ready `df-build` record with the fake uid (prepare_dir reads it)."""
    account = Account(plat.BUILD_USER, BUILD_UID, BUILD_UID, "ready", "2026-10-22")
    fl.layout.accounts.write_text(dump_accounts({plat.BUILD_USER: account}))


def register(fl: FakeLayout) -> None:
    entry = ReleaseEntry(
        ID, "sha256:" + "1" * 64, "sha256:" + "2" * 64, PY, "allowed", None
    )
    (fl.layout.etc / "releases.toml").write_text(
        dump_releases(ReleasesConfig((entry,)))
    )


def model_variant(leaf: str, state: State) -> str | None:
    """The variant the model expects; prepared trees are directories."""
    if list(table.leaf_variants(leaf)) == [None]:
        return None
    if leaf == "seal":
        return "reuse" if state["S"] == "container" else "new"
    return {"cleanup_tree": "tree", "cleanup_build_home": "absent"}[leaf]


def expected_route(lay: Layout, name: str, state: State) -> tuple[list[Event], State]:
    """Events of the route from the data, and the final facets."""
    s = dict(state)
    out: list[Event] = []
    for guards, call, target in table.unfold_route(name):
        if not all(GUARD_MODEL[g](s) for g in guards):
            continue
        leaf = implementation(call, target)
        out.append(("call", leaf, target))
        out += expected_events(lay, leaf, model_variant(leaf, s), target)
        s.update(EFFECTS.get((leaf, target), {}))
        if leaf == "write_mark":
            s["m"] = True
    return out, s


def run_case(
    fl: FakeLayout,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    state: State,
    **override: routes.Call,
) -> tuple[list[Event], list[Event], State]:
    """Prepare, compute the expectation from the data, run the real route."""
    prepare(fl, monkeypatch, state)
    expected, final = expected_route(fl.layout, name, state)
    fl.clear()
    calls = StandIns(fl).calls(**override)
    run_route(fl.layout, ID, name, calls=calls, python_key=KEY, build_uid=BUILD_UID)
    return normalize(fl.events), expected, final


def case_matches(
    fl: FakeLayout,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    state: State,
    **override: routes.Call,
) -> bool:
    """One case of a twin: False on a mismatch or a refusal."""
    reset(fl)
    try:
        got, expected, _final = run_case(fl, monkeypatch, name, state, **override)
    except (OSError, StepFailed, TreeError, AssertionError):
        return False
    return got == expected


@pytest.fixture(autouse=True)
def _boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fsutil, "boot_id", lambda: BOOT)


@pytest.fixture()
def fl(monkeypatch: pytest.MonkeyPatch, fake_layout: FakeLayout) -> FakeLayout:
    return setup_layout(monkeypatch, fake_layout)


def setup_layout(
    monkeypatch: pytest.MonkeyPatch, fake_layout: FakeLayout
) -> FakeLayout:
    """The layout's directories; F30's fakes and the local uv of the plan."""
    fake_layout.make_dirs()
    uv = fake_uv(fake_layout)
    uv.write_text(f"#!/bin/sh\n[ \"$1\" = --version ] && echo '{UV}'\nexit 0\n")
    uv.chmod(0o755)
    monkeypatch.setattr(build_mod, "verify_build_user", lambda _layout: [])
    monkeypatch.setattr(build_mod, "whoami_as", lambda _user: (BUILD_UID, BUILD_UID))
    return fake_layout


def fake_uv(fl: FakeLayout) -> Path:
    """The plan's uv: a local script (`--version` prints `UV`), outside root."""
    return fl.layout.root.parent / "uv"


# ---------- routes match the table ----------


@pytest.mark.parametrize("name,state", CASES)
def test_routes_match_table(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout, name: str, state: State
) -> None:
    got, expected, final = run_case(fl, monkeypatch, name, state)
    assert got == expected
    # The stand-ins changed what the guards read exactly as the model says.
    for guard in {g for gs, _c, _t in table.unfold_route(name) for g in gs}:
        value = route_guard(fl.layout, ID, guard, python_key=KEY, build_uid=BUILD_UID)
        assert value == GUARD_MODEL[guard](final), guard


def test_every_route_and_guard_is_exercised() -> None:
    """Each route has cases; each guard is read true and false somewhere."""
    assert {n for n, _s in (c.values for c in CASES)} == set(table.ROUTES)
    seen: set[tuple[str, bool]] = set()
    for c in CASES:
        name, state = c.values
        for gs, _c, _t in table.unfold_route(name):  # type: ignore[arg-type]
            seen |= {(g, GUARD_MODEL[g](state)) for g in gs}  # type: ignore[arg-type]
    assert {g for g, _v in seen} == table.GUARD_NAMES
    assert all((g, v) in seen for g in table.GUARD_NAMES for v in (True, False))


def test_fake_event_shapes(monkeypatch: pytest.MonkeyPatch, fl: FakeLayout) -> None:
    """Carry 7 of 7-C: the reap and ownership events have the fake's shapes."""
    prepare(fl, monkeypatch, DEFAULT)
    fl.clear()
    run_route(
        fl.layout, ID, "build-dir", calls=StandIns(fl).calls(), build_uid=BUILD_UID
    )
    assert ("fchown", fl.layout.release_dir(ID), fl.build_uid) in fl.events
    assert fl.owner_of(os.stat(fl.layout.release_dir(ID))) == fl.build_uid
    fl.clear()
    run_route(fl.layout, ID, "seal", calls=StandIns(fl).calls(), build_uid=BUILD_UID)
    assert fl.events.count(("process", "df-build")) == 1
    assert [e for e in fl.events if e[0] == "process"] == [("process", "df-build")]


def test_cleanup_removes_both_trees_in_route_order(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """M9 (S1-R10-N1): unregistered root `releases/<id>` and `admin/<id>` at
    once — the real wrappers remove both, in the route's order."""
    lay = fl.layout
    state = DEFAULT | {"r": "root", "admin": True}
    got, expected, final = run_case(fl, monkeypatch, "cleanup", state)
    assert got == expected
    assert [e for e in got if e[0] == "unlinked"] == [
        ("unlinked", lay.release_dir(ID)),
        ("unlinked", lay.admin_dir(ID)),
    ]
    assert [e[1:] for e in got if e[0] == "call"] == [
        ("remove_own_tree", "releases/<id>"),
        ("remove_own_tree", "admin/<id>"),
    ]
    assert not os.path.lexists(lay.release_dir(ID))
    assert not os.path.lexists(lay.admin_dir(ID))
    assert (final["r"], final["admin"]) == ("absent", False)


# ---------- twins: each must fail ----------


def _twin_fails(
    fl: FakeLayout,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    *,
    routes_twin: Mapping[str, tuple[table.RouteStep, ...]] | None = None,
    **override: routes.Call,
) -> bool:
    """True if some case of `name` breaks under the twin."""
    states = route_states(name)
    results = []
    for state in states:
        if routes_twin is None:
            results.append(case_matches(fl, monkeypatch, name, state, **override))
            continue
        # The expectation is computed from the original data, the run uses the twin.
        reset(fl)
        prepare(fl, monkeypatch, state)
        expected, _final = expected_route(fl.layout, name, state)
        fl.clear()
        with monkeypatch.context() as m:
            m.setattr(table, "ROUTES", {**table.ROUTES, **routes_twin})
            try:
                run_route(
                    fl.layout,
                    ID,
                    name,
                    calls=StandIns(fl).calls(),
                    python_key=KEY,
                    build_uid=BUILD_UID,
                )
                results.append(normalize(fl.events) == expected)
            except (OSError, StepFailed, TreeError):
                results.append(False)
    return not all(results)


def test_twin_rollback_bypassing_cleanup_staging(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    cleanup = tuple(
        (None, "remove_empty_dir", "staging/<id>") if call == "cleanup_staging" else s
        for s in table.ROUTES["cleanup"]
        for _g, call, _t in (s,)
    )
    assert _twin_fails(fl, monkeypatch, "rollback", routes_twin={"cleanup": cleanup})


def test_twin_cleanup_without_second_remove_own_tree(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    cleanup = tuple(s for s in table.ROUTES["cleanup"] if s[2] != "admin/<id>")
    assert len(cleanup) == len(table.ROUTES["cleanup"]) - 1
    assert _twin_fails(fl, monkeypatch, "cleanup", routes_twin={"cleanup": cleanup})


def test_twin_remove_own_tree_with_wrong_target(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    wrong = StandIns(fl).stand_in("remove_own_tree", target="releases/<id>")
    assert _twin_fails(fl, monkeypatch, "cleanup", remove_own_tree=wrong)


def test_twin_accept_copy_with_extra_sync(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """The real leaf plus one `sync_dir(releases)` of its own (rev 8, r10)."""
    real = routes.accept_copy

    def extra(*args: Any, **kwargs: Any) -> str:
        digest = real(*args, **kwargs)
        fsutil.sync_dir(fl.layout.releases)
        return digest

    monkeypatch.setattr(routes, "accept_copy", extra)
    assert _twin_fails(fl, monkeypatch, "copy")


def test_twin_guard_dropped_is_caught(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """Spec: twins drop each guard (true by assumption); a primitive refusing
    an absent object, a step the table did not take, or F13 refusing a
    registered id is the counterexample — the only exception is `r=builder`
    in cleanup."""
    for name, steps in table.ROUTES.items():
        for i, (guard, call, target) in enumerate(steps):
            if guard is None or (name, guard) == ("cleanup", "r=builder"):
                continue
            twin = {name: (*steps[:i], (None, call, target), *steps[i + 1 :])}
            outer = [
                n for n, st in table.ROUTES.items() if name in {c for _g, c, _t in st}
            ]
            runs = [name, *outer] if name != "cleanup_staging" else outer
            assert any(
                _twin_fails(fl, monkeypatch, r, routes_twin=twin) for r in runs
            ), (name, guard)


# ---------- guards ----------

LSTAT_GUARDS = sorted(table.GUARD_NAMES - {"not reg", "local_bin=trusted"})


def _guard_reads(lay: Layout, guard: str) -> tuple[list[Path], list[str]]:
    """(directories opened, names stat'ed through them) when the object is
    present: only parents, never the object."""
    staging_id = (lay.staging, lay.staging_dir(ID))
    reads: Mapping[str, tuple[tuple[Path, ...], tuple[str, ...]]] = {
        "r=root": ((lay.releases,), (ID,)),
        "r=builder": ((lay.releases,), (ID,)),
        "S=tree": (staging_id, ("tree",)),
        "S=container": ((lay.staging, *staging_id), (ID, "tree")),
        "admin=present": ((lay.admin,), (ID,)),
        "m=present": ((lay.marks_dir,), (f"{ID}.toml",)),
        "py=absent": ((lay.admin_python,), (KEY,)),
        "py-staging=present": ((lay.admin_python,), (".staging",)),
    }
    opened, names = reads[guard]
    return list(opened), list(names)


def _guard_state(guard: str) -> State:
    """A state where the guard's object is present."""
    present: Mapping[str, State] = {
        "r=root": {"r": "root"},
        "r=builder": {"r": "builder"},
        "S=tree": {"S": "tree"},
        "S=container": {"S": "container"},
        "admin=present": {"admin": True},
        "m=present": {"m": True},
        "py=absent": {"py": True},
        "py-staging=present": {"pystaging": True},
    }
    return DEFAULT | present[guard]


@pytest.mark.parametrize("guard", LSTAT_GUARDS)
def test_route_guard_uses_lstat_via_parent_fd(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout, guard: str
) -> None:
    prepare(fl, monkeypatch, _guard_state(guard))
    real_open, real_stat = os.open, os.stat
    fds: dict[int, Path] = {}
    opened: list[Path] = []
    stats: list[tuple[str, bool, bool]] = []

    def open_(
        path: StrPath, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        full = Path(str(path)) if dir_fd is None else fds[dir_fd] / str(path)
        assert flags & os.O_NOFOLLOW and flags & os.O_DIRECTORY, full
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        fds[fd] = full
        opened.append(full)
        return fd

    def stat_(
        path: StrPath, *, dir_fd: int | None = None, follow_symlinks: bool = True
    ) -> os.stat_result:
        stats.append((str(path), dir_fd in fds, follow_symlinks))
        return real_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    def other(kind: str) -> Callable[..., object]:
        def record(*args: object, **kwargs: object) -> object:
            others.append((kind, args))
            raise AssertionError(f"{kind} in a guard")

        return record

    others: list[tuple[str, tuple[object, ...]]] = []
    monkeypatch.setattr(os, "open", open_)
    monkeypatch.setattr(os, "stat", stat_)
    for kind in ("lstat", "scandir", "listdir"):
        monkeypatch.setattr(os, kind, other(kind))
    route_guard(fl.layout, ID, guard, python_key=KEY, build_uid=BUILD_UID)
    want_opened, want_names = _guard_reads(fl.layout, guard)
    assert opened == want_opened
    assert stats == [(n, True, False) for n in want_names]
    assert others == []


@pytest.mark.parametrize("guard", LSTAT_GUARDS)
@pytest.mark.parametrize("present", [False, True])
def test_route_guard_values(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout, guard: str, present: bool
) -> None:
    state = _guard_state(guard) if present else DEFAULT
    prepare(fl, monkeypatch, state)
    assert route_guard(
        fl.layout, ID, guard, python_key=KEY, build_uid=BUILD_UID
    ) == GUARD_MODEL[guard](state)


def test_r_guards_tell_root_from_builder(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    prepare(fl, monkeypatch, DEFAULT | {"r": "builder"})
    assert not route_guard(fl.layout, ID, "r=root", build_uid=BUILD_UID)
    assert route_guard(fl.layout, ID, "r=builder", build_uid=BUILD_UID)
    fl.set_owner(fl.layout.release_dir(ID), os.getuid())
    assert route_guard(fl.layout, ID, "r=root", build_uid=BUILD_UID)
    assert not route_guard(fl.layout, ID, "r=builder", build_uid=BUILD_UID)


def test_r_guards_false_on_non_directory(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    prepare(fl, monkeypatch, DEFAULT)
    fl.layout.release_dir(ID).write_text("x")
    assert not route_guard(fl.layout, ID, "r=root", build_uid=BUILD_UID)
    assert not route_guard(fl.layout, ID, "r=builder", build_uid=BUILD_UID)


def test_route_guard_refuses_foreign_parent(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    prepare(fl, monkeypatch, DEFAULT | {"S": "tree"})
    fl.set_owner(fl.layout.staging_dir(ID), fl.build_uid)
    with pytest.raises(StepFailed, match="не root"):
        route_guard(fl.layout, ID, "S=tree", build_uid=BUILD_UID)


def test_route_guard_refuses_linked_parent(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    prepare(fl, monkeypatch, DEFAULT)
    real = fl.layout.root / "elsewhere"
    (real / "tree").mkdir(parents=True)
    fl.layout.staging_dir(ID).symlink_to(real)
    with pytest.raises(StepFailed) as exc:
        route_guard(fl.layout, ID, "S=tree", build_uid=BUILD_UID)
    assert exc.value.step == "route"


def test_absent_parent_is_absent_name(fake_layout: FakeLayout) -> None:
    lay = fake_layout.layout
    assert not route_guard(lay, ID, "r=root", build_uid=BUILD_UID)
    assert not route_guard(lay, ID, "S=tree", build_uid=BUILD_UID)
    assert route_guard(lay, ID, "py=absent", python_key=KEY, build_uid=BUILD_UID)


def test_py_guard_needs_python_key(fl: FakeLayout) -> None:
    with pytest.raises(AssertionError, match="python_key"):
        route_guard(fl.layout, ID, "py=absent", build_uid=BUILD_UID)


def test_not_reg_reads_only_the_registry(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """F15: the registry through the hardened reader, nothing else opened."""
    real_open = os.open
    opened: list[str] = []

    def open_(path: object, flags: int, *a: object, **kw: object) -> int:
        opened.append(str(path))
        return real_open(path, flags, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", open_)
    assert route_guard(fl.layout, ID, "not reg", build_uid=BUILD_UID)
    assert opened == []
    register(fl)
    assert not route_guard(fl.layout, ID, "not reg", build_uid=BUILD_UID)
    assert opened == [str(fl.layout.etc / "releases.toml")]


def test_not_reg_broken_registry_stops(fl: FakeLayout) -> None:
    (fl.layout.etc / "releases.toml").write_text("garbage = [")
    with pytest.raises(StepFailed):
        route_guard(fl.layout, ID, "not reg", build_uid=BUILD_UID)


@pytest.mark.parametrize("problems,value", [([], True), (["x: не root"], False)])
def test_local_bin_guard_is_one_policy_call(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout, problems: list[str], value: bool
) -> None:
    """F15/F23: exactly one `trusted_path_problems(layout.local_bin)`."""
    calls: list[Path] = []

    def policy(path: Path) -> list[str]:
        calls.append(path)
        return problems

    monkeypatch.setattr(plat, "trusted_path_problems", policy)
    assert route_guard(fl.layout, ID, "local_bin=trusted", build_uid=BUILD_UID) is value
    assert calls == [fl.layout.local_bin]


def test_guards_cover_guard_names() -> None:
    assert set(GUARDS) == table.GUARD_NAMES


# ---------- the interpreter ----------


def test_cleanup_refuses_registered_before_any_step(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """F13: a registered id is never removed — refused before the first step."""
    prepare(fl, monkeypatch, DEFAULT | {"r": "root", "admin": True, "m": True})
    register(fl)
    fl.clear()
    with pytest.raises(StepFailed) as exc:
        run_route(
            fl.layout, ID, "cleanup", calls=StandIns(fl).calls(), build_uid=BUILD_UID
        )
    assert exc.value.step == "cleanup"
    assert fl.events == []
    assert fl.layout.release_dir(ID).is_dir() and fl.layout.admin_dir(ID).is_dir()


def test_nested_route_runs_through_the_interpreter(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """A nested route is never a key of `calls`: the interpreter recurses."""
    prepare(
        fl,
        monkeypatch,
        DEFAULT | {"r": "root", "S": "tree", "m": True, "stage": "copied"},
    )
    fl.clear()
    calls = StandIns(fl).calls()
    assert "cleanup_staging" not in calls
    run_route(fl.layout, ID, "verify", calls=calls, python_key=KEY, build_uid=BUILD_UID)
    marks = [e[1:] for e in fl.events if e[0] == "call"]
    assert marks[:2] == [
        ("cleanup_tree", "staging/<id>/tree"),
        ("remove_empty_dir", "staging/<id>"),
    ]


def test_wrapped_targets_go_to_their_wrapper() -> None:
    assert implementation("write_atomic", "accept/<id>.toml: built") == "write_mark"
    assert implementation("write_atomic", "/etc/df/releases.toml") == "register"
    assert (
        implementation("write_atomic", "releases/<id>/release.json")
        == "write_release_json"
    )
    assert implementation("write_atomic", "/etc/df/install.toml") == "write_atomic"
    assert implementation("remove_durably", "accept/<id>.toml") == "drop_mark"
    assert implementation("remove_durably", "pending.toml") == "remove_durably"
    with pytest.raises(KeyError):
        implementation("write_atomic", "accept/<id>.toml: other")


def _facts(point: Mapping[str, object]) -> Facts:
    """Facts of a table point (0/1 as booleans)."""
    values: dict[str, Any] = {
        k: v if isinstance(v, str) else bool(v) for k, v in point.items()
    }
    return Facts(**values)


def _facts_of(phase: str) -> Facts:
    """A point of `phase` (the first cube's smallest values)."""
    cube = table.row_of(phase).cubes[0]
    point = {
        k: sorted(cube[k], key=str)[0] if k in cube else d[0]
        for k, d in table.DOMAINS.items()
    }
    f = _facts(point)
    assert accept.classify(f) == phase
    return f


def test_route_action_refuses_outside_its_phase(fl: FakeLayout) -> None:
    """F14: `seal` with the facts of `building` — refused, nothing called."""
    act = route_action(
        fl.layout,
        ID,
        "seal",
        calls=StandIns(fl).calls(),
        python_key=KEY,
        build_uid=BUILD_UID,
    )
    with pytest.raises(StepFailed) as exc:
        act(_facts_of("building"))
    assert (exc.value.step, str(exc.value.detail)) == ("seal", "не в своей фазе")
    assert fl.events == []
    assert not fl.layout.staging_dir(ID).exists()


def test_route_action_runs_its_route_in_its_phase(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    prepare(fl, monkeypatch, DEFAULT | {"r": "builder"})
    fl.clear()
    act = route_action(
        fl.layout,
        ID,
        "seal",
        calls=StandIns(fl).calls(),
        python_key=KEY,
        build_uid=BUILD_UID,
    )
    act(_facts_of("built"))
    assert [e[1] for e in fl.events if e[0] == "call"] == [
        "reap_user_processes",
        "seal",
    ]
    assert fl.layout.staging_tree(ID).is_dir()


def test_unknown_phase_runs_cleanup(fl: FakeLayout) -> None:
    """`unknown` is a row whose action is `cleanup` (F9)."""
    point = {k: d[0] for k, d in table.DOMAINS.items()} | {"P": 1, "m": "copied"}
    f = _facts(point)
    assert accept.classify(f) == "unknown"
    act = route_action(
        fl.layout,
        ID,
        "cleanup",
        calls=StandIns(fl).calls(),
        python_key=KEY,
        build_uid=BUILD_UID,
    )
    act(f)
    assert fl.events == []


# ---------- targets ----------


def test_targets_cover_every_target_text() -> None:
    """F7: TARGETS is exactly the target texts of leaf steps."""
    texts = {t for _c, t in routes.leaf_steps()}
    assert set(TARGETS) == texts


def test_every_target_resolves(fl: FakeLayout) -> None:
    for text, spec in TARGETS.items():
        if spec.where is None:
            with pytest.raises(ValueError):
                routes.where(fl.layout, ID, text, KEY)
            continue
        parent, name = routes.where(fl.layout, ID, text, KEY)
        assert parent.is_relative_to(fl.layout.root) and "/" not in name, text


@pytest.mark.parametrize(
    "mutate",
    ["drop", "extra", "wrong-wrap", "stage-without-mark"],
)
def test_check_targets_twins(monkeypatch: pytest.MonkeyPatch, mutate: str) -> None:
    targets = dict(TARGETS)
    if mutate == "drop":
        del targets["staging/<id>"]
    elif mutate == "extra":
        targets["nowhere"] = routes.Target(None)
    elif mutate == "wrong-wrap":
        targets["pending.toml"] = dataclasses.replace(
            targets["pending.toml"], wrap="write_mark"
        )
    else:
        targets["releases/<id>"] = dataclasses.replace(
            targets["releases/<id>"], stage="built"
        )
    monkeypatch.setattr(routes, "TARGETS", targets)
    with pytest.raises(AssertionError):
        routes.check_targets()


# ---------- acceptance_calls ----------

INSTALL = InstallConfig(
    manifest_repo="https://example.invalid/m",
    manifest_sha="0" * 40,
    profile="p",
    approved_cli={
        "git": Path("/usr/bin/git"),
        "uv": Path("/usr/bin/uv"),
        "tar": Path("/usr/bin/tar"),
    },
)
PROFILE = Profile("p", PY, ())


def make_calls(fl: FakeLayout) -> dict[str, routes.Call]:
    lay = fl.layout
    plan = BuildPlan(
        ID,
        lay.release_dir(ID),
        PROFILE,
        fake_uv(fl),
        Path("/usr/bin/git"),
        Path("/usr/bin/tar"),
    )
    return acceptance_calls(
        lay,
        ID,
        install=INSTALL,
        profile=PROFILE,
        platform=plat.detect_platform(),
        plan=plan,
        limits=CopyLimits(),
        refs={},
        journal=lay.journal,
        run_id="run-1",
        python_key=KEY,
    )


def test_acceptance_calls_and_later_leaves_cover_the_table(fl: FakeLayout) -> None:
    """§5: Task 7's calls and the later tasks' leaves are exactly the leaves
    of every route and the wrappers, without overlap."""
    keys = set(make_calls(fl))
    assert keys.isdisjoint(LATER_LEAVES)
    assert keys | set(LATER_LEAVES) == set(table.route_leaves()) | set(table.WRAPS)
    assert set(LATER_LEAVES.values()) <= {"7a", "8", "8a", "9"}


@pytest.mark.parametrize("target", ["releases/<id>", "admin/<id>"])
def test_remove_own_tree_never_removes_registered(fl: FakeLayout, target: str) -> None:
    """F13: the wrapper reads the registry before the primitive."""
    path = obj(fl.layout, target)
    path.mkdir()
    register(fl)
    with pytest.raises(StepFailed) as exc:
        make_calls(fl)["remove_own_tree"](target)
    assert exc.value.step == "cleanup"
    assert path.is_dir()
    assert fl.events == []


def test_remove_own_tree_unregistered_removes(fl: FakeLayout) -> None:
    path = fl.layout.admin_dir(ID)
    path.mkdir()
    make_calls(fl)["remove_own_tree"]("admin/<id>")
    assert not path.exists()
    assert fl.events == [("unlinked", path), ("sync_dir", fl.layout.admin)]


def _journal(fl: FakeLayout) -> list[tuple[str, str, str]]:
    if not fl.layout.journal.exists():
        return []
    rows = read_journal(fl.layout.journal)
    return [(str(r["step"]), str(r["event"]), str(r["detail"])) for r in rows]


@pytest.mark.parametrize("left", [[], [4242]])
def test_reap_leftovers_are_a_warning(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout, left: list[int]
) -> None:
    """F28: leftovers after the reap are journaled, acceptance continues."""
    monkeypatch.setattr(plat, "reap_user_processes", lambda name: left)
    make_calls(fl)["reap_user_processes"]("df-build")
    want = [("seal", "warn", "процессы df-build остались: [4242]")] if left else []
    assert _journal(fl) == want


def test_reap_runs_the_real_reap(fl: FakeLayout) -> None:
    make_calls(fl)["reap_user_processes"]("df-build")
    assert fl.events == [("process", "df-build")]


# ---------- distribution_problems ----------

LOCK_A = b"""version = 1
revision = 3

[[package]]
name = "app"
version = "0.1.0"
source = { virtual = "." }
dependencies = [{ name = "a" }]

[[package]]
name = "a"
version = "1.0"
source = { registry = "https://pypi.org/simple" }
"""


def _dist_calls(
    fl: FakeLayout, repos: tuple[str, ...], refs: dict[str, Path], ctx: RouteContext
) -> dict[str, routes.Call]:
    pins = tuple(
        RepoPin(r, f"https://example.invalid/{r}", "0" * 40, ()) for r in repos
    )
    profile = Profile("p", PY, pins)
    plan = BuildPlan(
        ID, fl.layout.release_dir(ID), profile, fake_uv(fl), Path("/g"), Path("/t")
    )
    return acceptance_calls(
        fl.layout,
        ID,
        install=INSTALL,
        profile=profile,
        platform=plat.detect_platform(),
        plan=plan,
        limits=CopyLimits(),
        refs=refs,
        journal=fl.layout.journal,
        run_id="run-1",
        python_key=KEY,
        ctx=ctx,
    )


def _repo(fl: FakeLayout, repo: str, lock: bytes, dists: list[tuple[str, str]]) -> Path:
    """A trusted checkout with `lock` and a venv of registry `dists`."""
    ref = fl.layout.verify_dir / repo
    ref.mkdir()
    (ref / "uv.lock").write_bytes(lock)
    site = fl.layout.release_dir(ID) / "venv" / repo / "lib" / "python3.13"
    site = site / "site-packages"
    site.mkdir(parents=True)
    for name, version in dists:
        info = site / f"{name}-{version}.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(f"Name: {name}\nVersion: {version}\n")
    return ref


def test_distribution_problems_leaf_accepts_and_keeps_installed(fl: FakeLayout) -> None:
    ref = _repo(fl, "r1", LOCK_A, [("a", "1.0")])
    ctx = RouteContext()
    _dist_calls(fl, ("r1",), {"r1": ref}, ctx)["distribution_problems"]("releases/<id>")
    assert [(d.name, d.version) for d in ctx.installed["r1"]] == [("a", "1.0")]
    assert _journal(fl) == [("verify", "distributions-note", DECLARED_LIMIT)]


def test_distribution_problems_root_is_the_builds_project_path(
    fl: FakeLayout,
) -> None:
    """The root is expected at exactly the `--project` path the build passes
    to `uv sync` (uv records it as given, not resolved)."""
    lock = LOCK_A.replace(b'{ virtual = "." }', b'{ editable = "." }')
    ref = _repo(fl, "r1", lock, [("a", "1.0"), ("app", "0.1.0")])
    calls = _dist_calls(fl, ("r1",), {"r1": ref}, RouteContext())
    profile = Profile(
        "p", PY, (RepoPin("r1", "https://example.invalid/r1", "0" * 40, ()),)
    )
    plan = BuildPlan(
        ID, fl.layout.release_dir(ID), profile, fake_uv(fl), Path("/g"), Path("/t")
    )
    argv = dict(build_mod.build_commands(plan, fl.layout))["sync:r1"]
    project = argv[argv.index("--project") + 1]
    site = fl.layout.release_dir(ID) / "venv" / "r1" / "lib" / "python3.13"
    info = site / "site-packages" / "app-0.1.0.dist-info"
    (info / "direct_url.json").write_text(
        json.dumps({"url": f"file://{project}", "dir_info": {}})
    )
    calls["distribution_problems"]("releases/<id>")


def test_distribution_problems_leaf_lists_every_repo(fl: FakeLayout) -> None:
    """Problems of all repos in one failure, with the named limit."""
    r1 = _repo(fl, "r1", LOCK_A, [("a", "1.0"), ("b", "2.0")])
    r2 = _repo(fl, "r2", b"version = 1\nrevision = 4\n", [])
    ctx = RouteContext()
    calls = _dist_calls(fl, ("r1", "r2", "r3"), {"r1": r1, "r2": r2}, ctx)
    with pytest.raises(StepFailed) as exc:
        calls["distribution_problems"]("releases/<id>")
    assert exc.value.step == "verify"
    detail = str(exc.value)
    assert DECLARED_LIMIT in detail
    assert "r1: лишнее: b 2.0" in detail
    assert "r2: неподдерживаемый lock" in detail
    assert "r3: нет доверенного чекаута" in detail
    assert ctx.installed == {}
    assert _journal(fl) == []


# ---------- the mark wrapper ----------


def _mark(fl: FakeLayout) -> AcceptMark | None:
    return load_mark(fl.layout, ID, install_digest=config_digest(INSTALL))


def _write(fl: FakeLayout, stage: str, **kw: object) -> AcceptMark:
    later = stage != "built"
    admin = stage == "admin"
    values: dict[str, object] = {
        "install_digest": config_digest(INSTALL),
        "stage": stage,
        "built_boot_id": "boot-0",
        "tree_digest": "sha256:" + "1" * 64 if later else None,
        "admin_digest": "sha256:" + "2" * 64 if admin else None,
        "admin_python": PY if admin else None,
        "uv_version_declared": "uv 0.1.0",
    }
    values.update(kw)
    mark = AcceptMark(**values)  # type: ignore[arg-type]
    accept.write_mark(fl.layout, ID, mark)
    return mark


def _no_boot_id() -> str:
    raise AssertionError("boot_id() called")


def test_built_mark_takes_boot_and_uv_of_the_build(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    runners: list[object] = []

    def run_build(plan: BuildPlan, layout: Layout, *, runner: object) -> str:
        runners.append(runner)
        return UV

    monkeypatch.setattr(routes.build_mod, "run_build", run_build)
    calls = make_calls(fl)
    calls["run_build"]("от `df-build`")
    assert runners == [plat.run_as]
    calls["write_mark"]("accept/<id>.toml: built")
    mark = _mark(fl)
    assert mark is not None
    assert (mark.stage, mark.built_boot_id, mark.uv_version_declared) == (
        "built",
        BOOT,
        UV,
    )
    assert fl.events == [
        ("renamed", fl.layout.mark_file(ID)),
        ("sync_dir", fl.layout.marks_dir),
    ]


def test_mark_without_the_value_of_its_step_refused(fl: FakeLayout) -> None:
    """A value is handed once: no build in this route — no `built` mark."""
    with pytest.raises(StepFailed) as exc:
        make_calls(fl)["write_mark"]("accept/<id>.toml: built")
    assert exc.value.step == "mark"
    assert not fl.layout.mark_file(ID).exists()


def test_copied_mark_takes_the_copy_digest_once(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    _write(fl, "built", built_boot_id=BOOT)
    digest = "sha256:" + "3" * 64
    monkeypatch.setattr(routes, "accept_copy", lambda *a, **kw: digest)
    calls = make_calls(fl)
    calls["accept_copy"]("releases/<id>")
    calls["write_mark"]("accept/<id>.toml: copied")
    mark = _mark(fl)
    assert mark is not None
    assert (mark.stage, mark.tree_digest, mark.built_boot_id) == (
        "copied",
        digest,
        BOOT,
    )
    assert mark.uv_version_declared == "uv 0.1.0"
    with pytest.raises(StepFailed):
        calls["write_mark"]("accept/<id>.toml: copied")


def test_verify_mark_carries_from_the_record_not_boot_id(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    old = _write(fl, "copied")
    monkeypatch.setattr(fsutil, "boot_id", _no_boot_id)
    make_calls(fl)["write_mark"]("accept/<id>.toml: max(стадия, verified)")
    assert _mark(fl) == dataclasses.replace(old, stage="verified")


def test_verify_mark_keeps_a_higher_admin_mark(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    old = _write(fl, "admin")
    monkeypatch.setattr(fsutil, "boot_id", _no_boot_id)
    fl.clear()
    make_calls(fl)["write_mark"]("accept/<id>.toml: max(стадия, verified)")
    assert _mark(fl) == old
    assert fl.events == [
        ("renamed", fl.layout.mark_file(ID)),
        ("sync_dir", fl.layout.marks_dir),
    ]


def test_admin_mark_digests_the_admin_code(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    old = _write(fl, "verified")
    code = fl.layout.admin_dir(ID) / "df"
    code.mkdir(parents=True)
    (code / "cli.py").write_text("print(1)\n")
    monkeypatch.setattr(fsutil, "boot_id", _no_boot_id)
    make_calls(fl)["write_mark"]("accept/<id>.toml: admin")
    want = tree_digest(fl.layout.admin_dir(ID), exclude=frozenset())
    assert _mark(fl) == dataclasses.replace(
        old, stage="admin", admin_digest=want, admin_python=PY
    )


def test_later_mark_without_a_record_refused(fl: FakeLayout) -> None:
    with pytest.raises(StepFailed) as exc:
        make_calls(fl)["write_mark"]("accept/<id>.toml: admin")
    assert exc.value.step == "mark"


def test_drop_mark_and_remove_durably(fl: FakeLayout) -> None:
    _write(fl, "built", built_boot_id=BOOT)
    fl.layout.pending.write_text("raw\n")
    fl.clear()
    calls = make_calls(fl)
    calls["drop_mark"]("accept/<id>.toml")
    calls["remove_durably"]("pending.toml")
    lay = fl.layout
    assert fl.events == [
        ("unlinked", lay.mark_file(ID)),
        ("sync_dir", lay.marks_dir),
        ("unlinked", lay.pending),
        ("sync_dir", lay.install_dir),
    ]


def test_tree_digest_check_against_the_mark(fl: FakeLayout) -> None:
    tree = fl.layout.release_dir(ID)
    (tree / "src").mkdir(parents=True)
    _write(fl, "copied", tree_digest=tree_digest(tree))
    calls = make_calls(fl)
    calls["tree_digest"]("releases/<id> = отметке")
    (tree / "src" / "late.py").write_text("x\n")
    with pytest.raises(StepFailed) as exc:
        calls["tree_digest"]("releases/<id> = отметке")
    assert exc.value.step == "verify"


def test_cleanup_tree_call_uses_the_release_label(fl: FakeLayout) -> None:
    """The real `cleanup_tree` on the target's object, `quarantine_label=<id>`."""
    lay = fl.layout
    lay.staging_dir(ID).mkdir(mode=0o711)
    (fl.builder_dir(lay.staging_tree(ID)) / "f").write_text("x")
    make_calls(fl)["cleanup_tree"]("staging/<id>/tree")
    assert not os.path.lexists(lay.staging_tree(ID))
    assert normalize(fl.events) == [
        ("run_as+", "df-build"),
        ("unlinked", lay.staging_tree(ID)),
        ("sync_dir", lay.staging_dir(ID)),
    ]


def test_check_links_call_refuses_an_escaping_link(fl: FakeLayout) -> None:
    tree = fl.layout.release_dir(ID)
    tree.mkdir()
    (tree / "out").symlink_to("/etc/passwd")
    with pytest.raises(StepFailed) as exc:
        make_calls(fl)["check_links"]("releases/<id>")
    assert exc.value.step == "verify" and "out" in exc.value.detail


# ---------- fix round 1 ----------


def test_targets_agree_with_the_independent_objects(fl: FakeLayout) -> None:
    """I2: TARGETS/where resolve every target text to the object `OBJECTS`
    names (written from the spec, not from the code under test)."""
    assert set(OBJECTS) == set(TARGETS)
    for text, get in OBJECTS.items():
        if get is None:
            assert TARGETS[text].where is None, text
            continue
        parent, name = routes.where(fl.layout, ID, text, KEY)
        assert parent / name == get(fl.layout), text


@pytest.mark.parametrize("rel", ["tmp", "cache"])
def test_cleanup_build_home_call_removes_only_its_target(
    fl: FakeLayout, rel: str
) -> None:
    """The real `cleanup_build_home` leaf on `build_home/<rel>`: the named
    directory goes, the other stays; only runs as `df-build`."""
    home = fl.layout.build_home
    for name in ("tmp", "cache"):
        (home / name / "x").mkdir(parents=True)
    make_calls(fl)["cleanup_build_home"](f"build_home/{rel}")
    other = "cache" if rel == "tmp" else "tmp"
    assert not (home / rel).exists() and (home / other / "x").is_dir()
    assert normalize(fl.events) == [("run_as+", "df-build")]
    fl.clear()
    make_calls(fl)["cleanup_build_home"](f"build_home/{rel}")  # absent: success
    assert normalize(fl.events) == [("run_as+", "df-build")]


def test_r_guard_foreign_owner_stops(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """I1: owned by neither root nor df-build — StepFailed naming the owner."""
    prepare(fl, monkeypatch, DEFAULT | {"r": "builder"})
    fl.set_owner(fl.layout.release_dir(ID), BUILD_UID + 1)
    for guard in ("r=root", "r=builder"):
        with pytest.raises(StepFailed, match=str(BUILD_UID + 1)) as exc:
            route_guard(fl.layout, ID, guard, build_uid=BUILD_UID)
        assert exc.value.step == "route"


def test_r_guard_needs_build_uid(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    prepare(fl, monkeypatch, DEFAULT | {"r": "root"})
    with pytest.raises(AssertionError, match="build_uid"):
        route_guard(fl.layout, ID, "r=root")


def test_rollback_forced_into_cleanup_refuses_registered(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """M2: F13 holds through nesting — `rollback` with its `not reg` dropped
    enters `cleanup`, which refuses before any step."""
    prepare(fl, monkeypatch, DEFAULT | {"r": "root", "reg": True, "pending": True})
    fl.clear()
    twin = (
        (None, "cleanup", ""),
        *table.ROUTES["rollback"][1:],
    )
    monkeypatch.setattr(table, "ROUTES", {**table.ROUTES, "rollback": twin})
    with pytest.raises(StepFailed) as exc:
        run_route(
            fl.layout, ID, "rollback", calls=StandIns(fl).calls(), build_uid=BUILD_UID
        )
    assert exc.value.step == "cleanup"
    assert fl.events == []
    assert fl.layout.release_dir(ID).is_dir() and fl.layout.pending.exists()


def test_final_stat_error_is_a_step_failure(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """M8: an error other than absence reading the name stops the route."""
    real_stat = os.stat

    def stat_(path: StrPath, **kw: object) -> os.stat_result:
        if kw.get("dir_fd") is not None:
            raise PermissionError(13, "denied")
        return real_stat(path, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "stat", stat_)
    with pytest.raises(StepFailed) as exc:
        route_guard(fl.layout, ID, "admin=present", build_uid=BUILD_UID)
    assert exc.value.step == "route"
