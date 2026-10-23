"""The world of the route tests: a release under `fake_layout` (7-final M3).

Shared by the tests of routes, leaves, scenarios, the admin environment,
releases and the install transaction instead of importing one test module
from another. It holds the constants of the test release, the facet model
of guard state, the objects of protocol steps, the table-generated
stand-ins (twins only: every real call is `df.install.install_calls`), the
preparation of a state and the expected events of a route computed from the
data alone.
"""

from __future__ import annotations

import itertools
import os
import re
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from df import accept, admin, fsutil, launcher_entry, routes
from df import build as build_mod
from df import install_table as table
from df import platform as plat
from df.accept import AcceptMark
from df.build import BuildPlan
from df.config import (
    Account,
    InstallConfig,
    Pending,
    ReleaseEntry,
    ReleasesConfig,
    config_digest,
    dump_accounts,
    dump_install,
    dump_releases,
)
from df.digest import TreeError
from df.install import InstallRun, install_calls
from df.journal import StepFailed
from df.manifest import Profile
from df.paths import Layout
from df.receive import CopyLimits
from df.routes import RouteContext, implementation, run_route
from tests.df.conftest import BUILD_UID, FakeLayout

ID = "rel1"
PY = "3.13.1"
SHORT = ".".join(PY.split(".")[:2])
KEY = build_mod.python_key(PY, plat.detect_platform())
BOOT = "boot-1"
UV = "uv 0.9.2"
#: `python_declared` of a verified mark: the profile has no repos.
DECLARED = "{}"
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
#: Bases of the routes of Task 9 whose real calls need a release on disk:
#: `register` an accepted copy (`admin` mark), `activate` and `launcher` a
#: registered intact release (`world`).
BASES = {
    **BASES,
    "register": ({"r": "root", "m": True, "stage": "admin"},),
    "activate": ({"world": True},),
    "launcher": ({"world": True},),
}
DEFAULT: State = {k: v[0] for k, v in FACETS.items()} | {
    "pending": False,
    "stage": None,
    "world": False,
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
        of `install_calls`, or `override`."""
        names = [*table.route_leaves(), *table.WRAPS]
        real = make_calls(self.fl)
        made = {n: override.get(n) or real[n] for n in names}
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
    trusted = bool(state["local_bin"])
    monkeypatch.setattr(
        plat,
        "trusted_path_problems",
        lambda p: ["untrusted"] if p == lay.local_bin and not trusted else [],
    )
    if state["world"]:
        release_world(fl)
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


def write_state_mark(fl: FakeLayout, stage: object) -> None:
    """The row's valid mark (`stage`), else a raw mark file (F32: `m=present`
    is the file's existence)."""
    lay = fl.layout
    if stage is None:
        lay.mark_file(ID).write_text("raw\n")
        return
    copied = stage != "built"
    digest = routes.release_digest(lay.release_dir(ID)) if copied else None
    is_admin = stage == "admin"
    mark = AcceptMark(
        config_digest(INSTALL),
        stage,  # type: ignore[arg-type]
        BOOT,
        digest,
        "sha256:" + "2" * 64 if is_admin else None,
        PY if is_admin else None,
        UV,
        DECLARED if stage in ("verified", "admin") else None,
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


def setup_layout(
    monkeypatch: pytest.MonkeyPatch, fake_layout: FakeLayout
) -> FakeLayout:
    """The layout's directories; F30's fakes and the local uv of the plan."""
    fake_layout.make_dirs()
    uv = fake_uv(fake_layout)
    uv.write_text(FAKE_UV)
    uv.chmod(0o755)
    monkeypatch.setattr(admin, "UV_ADMIN_CONFIRMED", frozenset({UV.split()[1]}))
    monkeypatch.setattr(build_mod, "verify_build_user", lambda _layout: [])
    monkeypatch.setattr(build_mod, "whoami_as", lambda _user: (BUILD_UID, BUILD_UID))
    return fake_layout


#: The plan's uv: `--version` prints `UV`; `python install --no-bin <key>`
#: puts a stand-in interpreter `<key>/bin/python<X.Y>` and a service file
#: into `$UV_PYTHON_INSTALL_DIR` (as uv leaves `.lock` there); else nothing.
FAKE_UV = f"""#!/bin/sh
[ "$1" = --version ] && echo '{UV}' && exit 0
if [ "$1 $2 $3" = "python install --no-bin" ]; then
  mkdir -p "$UV_PYTHON_INSTALL_DIR/$4/bin" || exit 1
  printf '#!/bin/sh\\n' > "$UV_PYTHON_INSTALL_DIR/$4/bin/python{SHORT}"
  : > "$UV_PYTHON_INSTALL_DIR/.lock"
fi
exit 0
"""


def fake_uv(fl: FakeLayout) -> Path:
    """The plan's uv: a local script (`FAKE_UV`), outside root."""
    return fl.layout.root.parent / "uv"


def devtools_ref(fl: FakeLayout) -> Path:
    """Root's trusted `devtools` checkout: a small `df/` under `verify` with
    the dev entry module (the release's entry source, M-1)."""
    ref = fl.layout.verify_dir / "devtools"
    (ref / "df").mkdir(parents=True, exist_ok=True)
    (ref / "df" / "__init__.py").write_text('"""admin code"""\n')
    shutil.copyfile(launcher_entry.__file__, ref / "df" / "launcher_entry.py")
    return ref


def release_world(fl: FakeLayout) -> ReleaseEntry:
    """A registered intact release `ID` (Task 9's routes after `register`):
    a root tree `0755`, its admin code and admin Python by the real
    primitives, and its `allowed` entry with the real digests."""
    lay = fl.layout
    tree = lay.release_dir(ID)
    (tree / "src").mkdir(parents=True, exist_ok=True)
    (tree / "src" / "app.py").write_text("print(1)\n")
    tree.chmod(0o755)
    if not os.path.lexists(lay.admin_python / KEY):
        admin.install_admin_python(
            lay, PY, uv=fake_uv(fl), platform=plat.detect_platform()
        )
    admin.install_admin(lay, ID, devtools_ref=devtools_ref(fl))
    entry = ReleaseEntry(
        ID,
        routes.release_digest(tree),
        admin.admin_digest(lay, ID),
        PY,
        "allowed",
        None,
    )
    (lay.etc / "releases.toml").write_text(dump_releases(ReleasesConfig((entry,))))
    return entry


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


#: The `pending.toml` of the test release.
PENDING = Pending("run-1", ID, dump_install(INSTALL), config_digest(INSTALL), None)


def install_run(
    fl: FakeLayout,
    ctx: RouteContext | None = None,
    *,
    profile: Profile = PROFILE,
    refs: Mapping[str, Path] | None = None,
    pending: Pending = PENDING,
) -> InstallRun:
    """The install run of `pending`'s release (default: the test release)
    over `fl` (Task 9, I1)."""
    lay = fl.layout
    rid = pending.release_id
    plan = BuildPlan(
        rid,
        lay.release_dir(rid),
        profile,
        fake_uv(fl),
        Path("/usr/bin/git"),
        Path("/usr/bin/tar"),
    )
    return InstallRun(
        layout=lay,
        cfg=INSTALL,
        pending=pending,
        profile=profile,
        platform=plat.detect_platform(),
        plan=plan,
        limits=CopyLimits(),
        refs=refs if refs is not None else {"devtools": devtools_ref(fl)},
        journal=lay.journal,
        run_id="run-1",
        python_key=KEY,
        build_uid=BUILD_UID,
        ctx=ctx if ctx is not None else RouteContext(),
    )


def make_calls(
    fl: FakeLayout, ctx: RouteContext | None = None
) -> dict[str, routes.Call]:
    """Every real call of the test release (`install_calls`)."""
    return install_calls(install_run(fl, ctx))


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


def repo_ref(
    fl: FakeLayout, repo: str, lock: bytes, dists: list[tuple[str, str]]
) -> Path:
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
