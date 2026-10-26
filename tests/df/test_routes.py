"""Tests for the route interpreter, guards and call table (Task 7, part D1).

Every route runs through the real `run_route` and real guards over a
`fake_layout`, and every call is real: `df.install.install_calls` (Task 9,
R33.F3) — `acceptance_calls` of Tasks 7, 7a, 8 and 8a and the release's own
calls; `LATER_LEAVES` is empty. The table-generated stand-ins of
`leaf_variants` (F3) remain only for twins. `run_build` is real with F30's
fakes: `verify_build_user`/`whoami_as` replaced and a local `uv` script as
the plan's uv (no network); the same script installs a stand-in admin
Python and the trusted `devtools` checkout is a local `df/`. The world and
the model live in `tests/df/route_world.py` (7-final M3).

The expected sequence of a route is computed from the data alone: the
unfolding of `ROUTES` (`unfold_route`), each step kept when its guards hold in
a small facet model of the prepared state, each leaf expanded to its protocol
steps with concrete objects. Events are compared as sequences: order,
multiplicity and objects.
"""

from __future__ import annotations

import dataclasses
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from df import accept, fsutil, routes
from df import build as build_mod
from df import install_table as table
from df import platform as plat
from df.accept import AcceptMark, Facts, load_mark
from df.boundary import TransferResult
from df.build import BuildPlan
from df.config import (
    config_digest,
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
from tests.df.route_world import (
    BASES,
    BOOT,
    CASES,
    DECLARED,
    DEFAULT,
    GUARD_MODEL,
    ID,
    INSTALL,
    KEY,
    LOCK_A,
    OBJECTS,
    PY,
    SHORT,
    UV,
    StandIns,
    State,
    StrPath,
    case_matches,
    expected_route,
    fake_uv,
    install_run,
    make_calls,
    normalize,
    obj,
    prepare,
    register,
    repo_ref,
    reset,
    route_states,
    run_case,
    setup_layout,
)


@pytest.fixture(autouse=True)
def _boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fsutil, "boot_id", lambda: BOOT)


@pytest.fixture()
def fl(monkeypatch: pytest.MonkeyPatch, fake_layout: FakeLayout) -> FakeLayout:
    return setup_layout(monkeypatch, fake_layout)


# ---------- routes match the table ----------


@pytest.mark.parametrize("name,state", CASES)
def test_routes_match_table(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout, name: str, state: State
) -> None:
    got, expected, final = run_case(fl, monkeypatch, name, state)
    assert got == expected
    # The calls changed what the guards read exactly as the model says.
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
    assert exc.value.step == "registered"
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

#: The calls `df.install` adds to `acceptance_calls` (Task 9).
RELEASE_CALLS = {"activate", "write_release_json", "register", "write_atomic"}


def test_install_calls_cover_the_table(fl: FakeLayout) -> None:
    """§5, R33.F3: `install_calls` are exactly the leaves of every route and
    the wrappers; `acceptance_calls` is all of them but the release's own;
    no leaf is left to a later task (M18)."""
    every = set(table.route_leaves()) | set(table.WRAPS)
    assert set(make_calls(fl)) == every
    run = install_run(fl)
    accepted = acceptance_calls(
        run.layout,
        run.release_id,
        install=run.cfg,
        profile=run.profile,
        platform=run.platform,
        plan=run.plan,
        limits=run.limits,
        refs=run.refs,
        journal=run.journal,
        run_id=run.run_id,
        python_key=run.python_key,
    )
    assert set(accepted) == every - RELEASE_CALLS
    assert LATER_LEAVES == {}


@pytest.mark.parametrize("target", ["releases/<id>", "admin/<id>"])
def test_remove_own_tree_never_removes_registered(fl: FakeLayout, target: str) -> None:
    """F13: the wrapper reads the registry before the primitive."""
    path = obj(fl.layout, target)
    path.mkdir()
    register(fl)
    with pytest.raises(StepFailed) as exc:
        make_calls(fl)["remove_own_tree"](target)
    assert exc.value.step == "registered"
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


def _dist_calls(
    fl: FakeLayout,
    repos: tuple[str, ...],
    refs: dict[str, Path],
    ctx: RouteContext,
    groups: tuple[str, ...] = (),
) -> dict[str, routes.Call]:
    pins = tuple(
        RepoPin(r, f"https://example.invalid/{r}", "0" * 40, groups) for r in repos
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


def test_distribution_problems_leaf_accepts_and_keeps_installed(fl: FakeLayout) -> None:
    ref = repo_ref(fl, "r1", LOCK_A, [("a", "1.0")])
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
    ref = repo_ref(fl, "r1", lock, [("a", "1.0"), ("app", "0.1.0")])
    calls = _dist_calls(fl, ("r1",), {"r1": ref}, RouteContext())
    profile = Profile(
        "p", PY, (RepoPin("r1", "https://example.invalid/r1", "0" * 40, ()),)
    )
    plan = BuildPlan(
        ID, fl.layout.release_dir(ID), profile, fake_uv(fl), Path("/g"), Path("/t")
    )
    argv = dict(build_mod.build_commands(plan, fl.layout))["sync:r1"]
    project = argv[argv.index("--project") + 1]
    site = fl.layout.release_dir(ID) / "venv" / "r1" / "lib" / f"python{SHORT}"
    info = site / "site-packages" / "app-0.1.0.dist-info"
    (info / "direct_url.json").write_text(
        json.dumps({"url": f"file://{project}", "dir_info": {}})
    )
    calls["distribution_problems"]("releases/<id>")


def test_distribution_problems_leaf_passes_groups_env_and_root_as_spelled(
    fl: FakeLayout, tmp_path: Path
) -> None:
    """M2: the pin's groups, the install platform's markers and the root at
    the unresolved build path (`build_home/tmp` is a link here) all reach
    `expected_distributions`."""
    here = "darwin" if plat.detect_platform() == "macos-arm64" else "linux"
    other = "linux" if here == "darwin" else "darwin"
    lock = (
        b'version = 1\nrevision = 3\n\n[[package]]\nname = "app"\n'
        b'version = "0.1.0"\nsource = { editable = "." }\n'
        b"dependencies = ["
        + f'{{ name = "mine", marker = "sys_platform == \'{here}\'" }}, '
        f'{{ name = "theirs", marker = "sys_platform == \'{other}\'" }}'.encode()
        + b']\n[package.dev-dependencies]\ng = [{ name = "grp" }]\n'
        + b"".join(
            f'\n[[package]]\nname = "{n}"\nversion = "1.0"\n'
            f'source = {{ registry = "https://pypi.org/simple" }}\n'.encode()
            for n in ("mine", "theirs", "grp")
        )
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (fl.layout.build_home / "tmp").symlink_to(elsewhere)
    ref = repo_ref(fl, "r1", lock, [("mine", "1.0"), ("grp", "1.0"), ("app", "0.1.0")])
    project = build_mod.build_src_dir(fl.layout, ID, "r1")
    site = fl.layout.release_dir(ID) / "venv" / "r1" / "lib" / f"python{SHORT}"
    (site / "site-packages" / "app-0.1.0.dist-info" / "direct_url.json").write_text(
        json.dumps({"url": f"file://{project}", "dir_info": {}})
    )
    ctx = RouteContext()
    calls = _dist_calls(fl, ("r1",), {"r1": ref}, ctx, groups=("g",))
    calls["distribution_problems"]("releases/<id>")
    assert sorted(d.name for d in ctx.installed["r1"]) == ["app", "grp", "mine"]


def test_distribution_problems_root_dir_contract_is_listed(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """A broken `root_dir` contract is a listed problem, not a bare error."""
    ref = repo_ref(fl, "r1", LOCK_A, [("a", "1.0")])
    monkeypatch.setattr(
        build_mod, "build_src_dir", lambda _lay, _id, repo: Path("relative") / repo
    )
    calls = _dist_calls(fl, ("r1",), {"r1": ref}, RouteContext())
    with pytest.raises(StepFailed) as exc:
        calls["distribution_problems"]("releases/<id>")
    assert "r1: root_dir" in str(exc.value)


def test_distribution_problems_leaf_lists_every_repo(fl: FakeLayout) -> None:
    """Problems of all repos in one failure, with the named limit."""
    r1 = repo_ref(fl, "r1", LOCK_A, [("a", "1.0"), ("b", "2.0")])
    r2 = repo_ref(fl, "r2", b"version = 1\nrevision = 4\n", [])
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


# ---------- static_problems and transfer_check (Task 8) ----------

PYPROJECT = '[project]\nname = "r1"\n[project.scripts]\ntool = "r1:main"\n'


def _ref(fl: FakeLayout, repo: str, text: str) -> Path:
    ref = fl.layout.verify_dir / repo
    ref.mkdir()
    (ref / "pyproject.toml").write_text(text + "[build-system]\nrequires = []\n")
    return ref


@pytest.mark.parametrize("leaf", ["static_problems", "transfer_check"])
def test_boundary_leaves_pass_the_trusted_inventory(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout, leaf: str
) -> None:
    """Each leaf reads `[project.scripts]` of the trusted checkout and hands
    the copy, the platform / `df-build` and that inventory to its check."""
    seen: list[tuple[object, ...]] = []

    def fake_static(tree: Path, platform: str, expected: object) -> list[str]:
        seen.append((tree, platform, expected))
        return []

    def fake_transfer(tree: Path, layout: Layout, **kw: object) -> TransferResult:
        seen.append((tree, layout, kw))
        return TransferResult([], {"r1": "3.13.1 (main)"})

    monkeypatch.setattr(routes, "static_problems", fake_static)
    monkeypatch.setattr(routes, "transfer_check", fake_transfer)
    ctx = RouteContext()
    refs = {"r1": _ref(fl, "r1", PYPROJECT)}
    _dist_calls(fl, ("r1",), refs, ctx)[leaf]("releases/<id>")
    tree, expected = fl.layout.release_dir(ID), {"r1": frozenset({"tool"})}
    if leaf == "static_problems":
        assert seen == [(tree, plat.detect_platform(), expected)]
        assert ctx.python_declared is None
    else:
        kw = {"user": plat.BUILD_USER, "expected": expected}
        assert seen == [(tree, fl.layout, kw)]
        assert ctx.python_declared == {"r1": "3.13.1 (main)"}


@pytest.mark.parametrize("leaf", ["static_problems", "transfer_check"])
def test_boundary_leaves_list_every_problem(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout, leaf: str
) -> None:
    """A missing or unreadable inventory and the check's own problems go into
    one `StepFailed("verify")`; nothing reaches the context."""
    monkeypatch.setattr(routes, "static_problems", lambda *a: ["venv/r1: плохо"])
    monkeypatch.setattr(
        routes, "transfer_check", lambda *a, **k: TransferResult(["запуск: плохо"], {})
    )
    ctx = RouteContext()
    refs = {"r1": _ref(fl, "r1", PYPROJECT), "r2": _ref(fl, "r2", "[project\n")}
    calls = _dist_calls(fl, ("r1", "r2", "r3"), refs, ctx)
    with pytest.raises(StepFailed) as exc:
        calls[leaf]("releases/<id>")
    assert exc.value.step == "verify"
    assert "r2: инвентарь entrypoints" in exc.value.detail
    assert "r3: нет доверенного чекаута" in exc.value.detail
    if leaf == "static_problems":
        assert "venv/r1: плохо" in exc.value.detail
    ctx_refs = {"r1": refs["r1"]}
    with pytest.raises(StepFailed) as exc:
        _dist_calls(fl, ("r1",), ctx_refs, ctx)[leaf]("releases/<id>")
    assert ("venv/r1: плохо" if leaf == "static_problems" else "запуск: плохо") in (
        exc.value.detail
    )
    assert ctx.python_declared is None


def _transfer_runs(fl: FakeLayout) -> list[tuple[str, ...]]:
    """Commands run as `df-build` inside the `transfer_check` call."""
    runs: list[tuple[str, ...]] = []
    inside = False
    for event in fl.events:
        if event[0] == "call":
            inside = event[1] == "transfer_check"
        elif inside and event[0] == "run_as":
            runs.append(event[2])  # type: ignore[arg-type]
    return runs


def _transfer_honest(fl: FakeLayout, monkeypatch: pytest.MonkeyPatch) -> bool:
    """The verify route matches the table and `transfer_check` ran exactly
    the leftover `find` as `df-build` (the profile has no repos), so it
    removed nothing. `run_as(df-build)+` alone cannot tell a hidden removal
    as `df-build` from the find: the argv of each run is compared too."""
    state = DEFAULT | BASES["verify"][0]
    if not case_matches(fl, monkeypatch, "verify", state):
        return False
    home = str(fl.layout.build_home)
    find = ("/usr/bin/find", home, "-mindepth", "1", "-maxdepth", "1")
    find += ("(", "-name", "tmp", "-o", "-name", "cache", ")", "-print")
    return _transfer_runs(fl) == [find]


def test_transfer_check_in_route_only_finds(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    assert _transfer_honest(fl, monkeypatch)


def test_twin_transfer_check_with_hidden_build_home_cleanup(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """R33.F3 twin: the real check preceded by a hidden `cleanup_build_home`."""
    real = routes.transfer_check

    def hidden(release_dir: Path, layout: Layout, **kw: Any) -> TransferResult:
        plat.cleanup_build_home(layout, "tmp")
        return real(release_dir, layout, **kw)

    monkeypatch.setattr(routes, "transfer_check", hidden)
    assert not _transfer_honest(fl, monkeypatch)


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
        "python_declared": DECLARED if stage in ("verified", "admin") else None,
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
    ctx = RouteContext(python_declared={"r1": "3.13.1 (main)"})
    make_calls(fl, ctx)["write_mark"]("accept/<id>.toml: max(стадия, verified)")
    assert _mark(fl) == dataclasses.replace(
        old, stage="verified", python_declared='{"r1":"3.13.1 (main)"}'
    )


def test_verify_mark_needs_the_transfer_check_answers(fl: FakeLayout) -> None:
    """I4a: no `transfer_check` in this process — no `verified` mark."""
    _write(fl, "copied")
    with pytest.raises(StepFailed, match="python_declared") as exc:
        make_calls(fl)["write_mark"]("accept/<id>.toml: max(стадия, verified)")
    assert exc.value.step == "mark"
    mark = _mark(fl)
    assert mark is not None and mark.stage == "copied"


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


@pytest.mark.parametrize("r", ["absent", "root", "builder"])
def test_r_guards_without_build_uid(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout, r: str
) -> None:
    """I5: no `df-build` record (`build_uid=None`, a rollback before step 3):
    a root tree is root's; any other owner is `StepFailed("route")` — no
    `df-build` process ran, so no builder tree can be there."""
    prepare(fl, monkeypatch, DEFAULT | {"r": r})
    if r == "builder":
        for guard in ("r=root", "r=builder"):
            with pytest.raises(StepFailed, match=str(BUILD_UID)) as exc:
                route_guard(fl.layout, ID, guard)
            assert exc.value.step == "route"
        return
    assert route_guard(fl.layout, ID, "r=root") is (r == "root")
    assert route_guard(fl.layout, ID, "r=builder") is False


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
    assert exc.value.step == "registered"
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
