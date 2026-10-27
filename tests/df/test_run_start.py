"""`df-run-start`: the run registry and the privilege drop (stage 2a, Task 6)."""

from __future__ import annotations

import builtins
import io
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from df import cli, launch, models, probe, run_start, state
from df.config import Account, ConfigError, dump_accounts
from df.journal import Stable, StepFailed, Transitional
from df.models import ModelChoice, ModelsConfig, dump_models
from df.probe import ProbeRecord
from df.stage2_config import (
    ModelChange,
    Product,
    RunRecord,
    dump_model_change,
    dump_products,
    effective_pins,
    parse_model_change,
    parse_run,
)
from tests.df.conftest import FakeLayout
from tests.df.launch_world import (
    ADMIN,
    DIGEST,
    Spy,
    entry,
    put_accounts,
    put_record,
    put_registry,
    record,
    snapshot,
    trust_paths,
)

A, B = entry("rel-a"), entry("rel-b")
EXEC_UID, EXEC_GID = 4242, 4343
TRUST = """\
schema = "df/trust/v1"
host = "github.com"
df_trust_repository_id = 5
df_trust_full_name = "o/df-trust"
df_trust_branch = "main"
approval_policy_repository_id = 6
transport = "public"
"""
CHOICE = ModelChoice("claude", "opus", "high", {})
OTHER = ModelChoice("claude", "sonnet", None, {})
RESUME_HINT = "повторите с --accept-model-change --from-step N"


@dataclass
class Ev:
    log: list[tuple[object, ...]] = field(default_factory=list)
    stdin: bytes | None = None
    dropped: bool = False
    uid: int = 0

    def names(self) -> list[object]:
        return [e[0] for e in self.log]

    def reset(self) -> None:
        """Back to root, with an empty log (a second command in one test)."""
        self.log.clear()
        self.dropped, self.uid, self.stdin = False, 0, None

    def of(self, name: str) -> list[tuple[object, ...]]:
        return [e for e in self.log if e[0] == name]


@pytest.fixture()
def world(fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch) -> FakeLayout:
    fake_layout.make_dirs()
    fake_layout.layout.runs_dir.mkdir(parents=True)
    put_accounts(fake_layout, uid=EXEC_UID)
    rows = {
        r: Account(r, EXEC_UID, EXEC_GID, "ready", "t")
        for r in ("df-exec", "df-review")
    }
    fake_layout.layout.accounts.write_text(dump_accounts(rows))
    trust_paths(monkeypatch)
    etc = fake_layout.layout.etc
    (etc / "products.toml").write_text(
        dump_products({7: Product(7, "o/r", "write", 1, 2, "u", "/x/checkout")})
    )
    (etc / "trust.toml").write_text(TRUST)
    return fake_layout


@pytest.fixture()
def ev(monkeypatch: pytest.MonkeyPatch) -> Ev:
    """Every privileged `os.*` and exec call is a fake that records."""
    e = Ev()
    real_uid = os.getuid()

    def ident() -> int:
        return e.uid if e.dropped else real_uid

    def setuid(uid: int) -> None:
        e.log.append(("setuid", uid))
        e.dropped, e.uid = True, uid

    real_dup2 = os.dup2

    def dup2(src: int, dst: int, *a: object, **k: object) -> int:
        if dst != 0 or not e.dropped:  # pytest's own capture uses dup2
            return real_dup2(src, dst, *a, **k)  # type: ignore[arg-type]
        e.stdin = os.read(src, 1000)
        e.log.append(("dup2", dst))
        return dst

    monkeypatch.setattr(os, "setgroups", lambda g: e.log.append(("setgroups", list(g))))
    monkeypatch.setattr(os, "setgid", lambda g: e.log.append(("setgid", g)))
    monkeypatch.setattr(os, "setuid", setuid)
    monkeypatch.setattr(os, "getuid", ident)
    monkeypatch.setattr(os, "geteuid", ident)
    monkeypatch.setattr(os, "getgid", lambda: EXEC_GID if e.dropped else 1)
    monkeypatch.setattr(os, "getegid", lambda: EXEC_GID if e.dropped else 1)
    monkeypatch.setattr(os, "dup2", dup2)
    monkeypatch.setattr(os, "chdir", lambda p: e.log.append(("chdir", Path(p))))
    monkeypatch.setattr(
        os, "closerange", lambda lo, hi: e.log.append(("closerange", lo, hi))
    )
    monkeypatch.setattr(
        os, "execve", lambda p, a, env: e.log.append(("execve", str(p), list(a), env))
    )
    monkeypatch.setattr(
        os, "execv", lambda p, a: e.log.append(("execv", str(p), list(a)))
    )
    return e


def put_models(world: FakeLayout, **roles: ModelChoice) -> str:
    chosen = {r: CHOICE for r in models.ROLES} | roles
    return dump_models(ModelsConfig(chosen, {}))


def snap_of(world: FakeLayout, current: str = "rel-a", *entries, **roles: ModelChoice):  # noqa: ANN201
    text = put_models(world, **roles)
    base = snapshot(current, *(entries or (A,)))
    return state.Snapshot(
        base.release_id, base.entry, None, text, False, 1, base.releases
    )


def start(world: FakeLayout, snap, product: str = "7") -> int:  # noqa: ANN001
    return run_start.main(
        ["start", "--product", product], layout=world.layout, snap=snap
    )


def records(world: FakeLayout) -> list[Path]:
    return sorted(world.layout.runs_dir.glob("*.json"))


def execve_of(ev: Ev) -> tuple[str, list[str], dict[str, str]]:
    (found,) = ev.of("execve")
    return found[1], found[2], found[3]  # type: ignore[return-value]


# --- parse (R15) ---------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["resume", "run-1", "--accept-model-change"],
        ["resume", "run-1", "--from-step", "2"],
        ["resume", "run-1", "--accept-model-change", "--from-step", "0"],
        ["resume", "run-1", "--accept-model-change", "--from-step", "-1"],
        ["resume", "run-1", "--accept-model-change", "--from-step", "x"],
        ["resume", "run-1", "--accept-model-change", "--from-step", "01"],
        ["resume", "run-1", "--accept-model-change", "--from-step", "٣"],
        ["resume", "run-1", "--accept-model-change", "--from-step"],
        ["resume", "run-1", "--from-step", "--accept-model-change", "2"],
        ["resume", "run-1", "--accept-model-change", "2", "--from-step"],
        ["resume", "run-1", "2", "--from-step", "--accept-model-change"],
        ["resume", "run-1", "--accept-model-change", "--accept-model-change"],
        ["resume", "run-1", "--from-step", "1", "--from-step", "2"],
        ["resume"],
        ["resume", "a/b"],
        ["resume", "run-1", "extra"],
        ["start"],
        ["start", "--product"],
        ["start", "--product", "0"],
        ["start", "--product", "07"],
        ["start", "--product", "x"],
        ["start", "--product", "7", "extra"],
        ["start", "--product", "7", "--accept-model-change"],
        ["stop"],
        [],
    ],
)
def test_parse_refuses_bad_shapes(argv: list[str]) -> None:
    with pytest.raises(StepFailed):
        run_start.parse(argv)


def test_parse_accepts_the_forms() -> None:
    assert run_start.parse(["start", "--product", "7"]).product == 7
    big = run_start.parse(["start", "--product", "1012345678"])
    assert big.product == 1012345678  # real GitHub ids have ten digits
    plain = run_start.parse(["resume", "run-1"])
    assert (plain.run_id, plain.accept, plain.from_step) == ("run-1", False, None)
    both = run_start.parse(
        ["resume", "run-1", "--from-step", "3", "--accept-model-change"]
    )
    assert (both.accept, both.from_step) == (True, 3)
    first = run_start.parse(
        ["resume", "r", "--accept-model-change", "--from-step", "4"]
    )
    assert (first.accept, first.from_step) == (True, 4)


def test_option_alone_exits_before_anything_is_read(
    world: FakeLayout, ev: Ev, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    real = os.open
    monkeypatch.setattr(
        os, "open", lambda p, *a, **k: (opened.append(str(p)), real(p, *a, **k))[1]
    )
    with pytest.raises(StepFailed):
        run_start.main(
            ["resume", "run-1", "--accept-model-change"],
            layout=world.layout,
            snap=snap_of(world),
        )
    assert opened == [] and ev.log == []


# --- new_run_id / register -------------------------------------------------------


def test_new_run_id_form() -> None:
    a, b = run_start.new_run_id(), run_start.new_run_id()
    assert re.fullmatch(r"run-\d{8}T\d{6}Z-[0-9a-f]{16}", a)
    assert a != b and launch.RUN_ID.fullmatch(a)


def test_register_exclusive(world: FakeLayout) -> None:
    rec = record("run-1", "rel-a")
    run_start.register(world.layout, rec)
    path = world.layout.runs_dir / "run-1.json"
    assert parse_run(path.read_text()) == rec
    assert path.stat().st_mode & 0o777 == 0o644
    before = path.read_text()
    with pytest.raises(StepFailed, match="уже есть"):
        run_start.register(world.layout, record("run-1", "rel-b"))
    assert path.read_text() == before


def test_register_refuses_a_planted_link(world: FakeLayout) -> None:
    victim = world.layout.root / "victim"
    victim.write_text("keep")
    (world.layout.runs_dir / "run-1.json").symlink_to(victim)
    with pytest.raises((StepFailed, OSError)):
        run_start.register(world.layout, record("run-1", "rel-a"))
    assert victim.read_text() == "keep"


def test_register_syncs_file_and_directory(
    world: FakeLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    from df import fsutil

    seen: list[str] = []
    monkeypatch.setattr(fsutil, "sync_file", lambda fd: seen.append("file"))
    monkeypatch.setattr(fsutil, "sync_dir", lambda p: seen.append("dir"))
    run_start.register(world.layout, record("run-1", "rel-a"))
    assert "file" in seen and "dir" in seen


# --- start -------------------------------------------------------------------------


def test_start_registers_drops_and_execs_in_order(world: FakeLayout, ev: Ev) -> None:
    assert start(world, snap_of(world)) == 1  # a fake execve returns
    (path,) = records(world)
    rec = parse_run(path.read_text())
    assert rec.run_id == path.stem and rec.product == 7
    assert (rec.release_id, rec.release_digest) == ("rel-a", DIGEST)
    assert rec.admin_digest == ADMIN
    assert set(rec.models) == set(models.ROLES)
    assert rec.models["author"].model == "opus"
    assert rec.models["author"].config_hash == models.config_hash(CHOICE)
    names = ev.names()
    assert names.index("setgroups") < names.index("setgid") < names.index("setuid")
    assert names.index("setuid") < names.index("dup2") < names.index("execve")
    assert ev.of("setgroups")[0][1] == [EXEC_GID]
    assert ev.of("setgid")[0][1] == EXEC_GID
    assert ev.of("setuid")[0][1] == EXEC_UID
    assert ("chdir", Path("/")) in ev.log
    assert names.index("chdir") < names.index("setgroups")
    assert names.index("closerange") < names.index("setgroups")
    assert ev.stdin == f"{rec.run_id}\n".encode()
    exe, argv, env = execve_of(ev)
    assert exe == str(world.layout.libexec / "df-launch")
    assert argv == ["df-launch", "df-exec", "run"]
    assert env == launch.role_env(world.layout, "df-exec")


def test_start_pins_probe_data_and_marks_missing_ones(
    world: FakeLayout, ev: Ev
) -> None:
    world.layout.probes_dir.mkdir(parents=True)
    rec_probe = _probe_record("author", models.config_hash(CHOICE))
    (world.layout.probes_dir / "author.json").write_text(rec_probe.to_json())
    start(world, snap_of(world))
    (path,) = records(world)
    pins = parse_run(path.read_text()).models
    assert (pins["author"].harness_version, pins["author"].actual_model) == (
        "9.9.9",
        "opus-4",
    )
    assert pins["judge"].harness_version == "не проверено"
    assert pins["judge"].actual_model == "не сообщён"


def _probe_record(role: str, config_hash: str, **kw: object) -> ProbeRecord:
    fields: dict[str, object] = {
        "role": role,
        "product": None,
        "time": "2026-10-24T10:00:00+00:00",
        "harness": "claude",
        "harness_version": "9.9.9",
        "adapter_version": probe.ADAPTER_VERSION,
        "isolation": "verified",
        "requested_model": "opus",
        "actual_model": "opus-4",
        "effort": "high",
        "params": {},
        "config_hash": config_hash,
        "executable_sha256": "e" * 64,
        "managed_digest": "m" * 64,
        "auth_tracking": "x",
        "auth_files": (),
        "outcome": "confirmed",
        "reason": None,
    }
    fields.update(kw)
    return ProbeRecord(**fields)  # type: ignore[arg-type]


def test_a_probe_of_another_config_is_not_pinned(world: FakeLayout, ev: Ev) -> None:
    world.layout.probes_dir.mkdir(parents=True)
    stale = _probe_record("author", models.config_hash(OTHER))
    (world.layout.probes_dir / "author.json").write_text(stale.to_json())
    start(world, snap_of(world))
    pins = parse_run(records(world)[0].read_text()).models
    assert pins["author"].harness_version == "не проверено"


def test_product_unknown_registers_nothing(world: FakeLayout, ev: Ev) -> None:
    with pytest.raises(StepFailed, match="product_unknown"):
        start(world, snap_of(world), product="8")
    (world.layout.etc / "products.toml").unlink()
    with pytest.raises(StepFailed, match="product_unknown"):
        start(world, snap_of(world))
    assert records(world) == [] and ev.log == []


def test_revoked_release_refused_at_start(world: FakeLayout, ev: Ev) -> None:
    revoked = entry("rel-a", "revoked")
    with pytest.raises(StepFailed, match="release_revoked"):
        start(world, snap_of(world, "rel-a", revoked))
    with pytest.raises(StepFailed, match="release_unknown"):
        base = snap_of(world)
        start(
            world,
            state.Snapshot(None, None, None, base.models_text, False, 1, base.releases),
        )
    assert records(world) == [] and ev.log == []


def test_missing_model_refuses_before_registering(world: FakeLayout, ev: Ev) -> None:
    snap = state.Snapshot(
        "rel-a", A, None, None, False, 1, snapshot("rel-a", A).releases
    )
    with pytest.raises(ConfigError, match="модель не задана"):
        start(world, snap)
    assert records(world) == [] and ev.log == []


def test_root_id_recorded(world: FakeLayout, ev: Ev) -> None:
    start(world, snap_of(world))
    rec = parse_run(records(world)[0].read_text())
    assert rec.root_id == ("github.com", 5, "main")


def test_root_id_none_when_trust_is_absent(world: FakeLayout, ev: Ev) -> None:
    (world.layout.etc / "trust.toml").unlink()
    start(world, snap_of(world))
    assert parse_run(records(world)[0].read_text()).root_id is None


def test_broken_trust_refuses_and_registers_nothing(world: FakeLayout, ev: Ev) -> None:
    (world.layout.etc / "trust.toml").write_text("garbage = [")
    with pytest.raises(ConfigError):
        start(world, snap_of(world))
    assert records(world) == [] and ev.log == []


def test_not_root_refused(world: FakeLayout, ev: Ev, monkeypatch) -> None:  # noqa: ANN001
    from df import platform as plat

    monkeypatch.setattr(plat, "ROOT_UID", os.getuid() + 1)
    with pytest.raises(StepFailed, match="root"):
        start(world, snap_of(world))
    assert records(world) == [] and ev.log == []


def test_no_ready_account_refuses_before_registering(world: FakeLayout, ev: Ev) -> None:
    rows = {"df-exec": Account("df-exec", 1, 2, "creating", "t")}
    world.layout.accounts.write_text(dump_accounts(rows))
    with pytest.raises(StepFailed, match="df-exec"):
        start(world, snap_of(world))
    assert records(world) == [] and ev.log == []


# --- the privilege drop ---------------------------------------------------------------


def test_a_drop_that_did_not_take_is_refused(
    world: FakeLayout, ev: Ev, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "setuid", lambda uid: ev.log.append(("setuid", uid)))
    with pytest.raises(StepFailed, match="uid"):
        start(world, snap_of(world))
    assert ev.of("execve") == []


def test_a_half_dropped_uid_is_refused(
    world: FakeLayout, ev: Ev, monkeypatch: pytest.MonkeyPatch
) -> None:
    """euid dropped, real uid kept (a `seteuid`-like drop): refused."""
    me = os.getuid()
    monkeypatch.setattr(os, "setuid", lambda uid: ev.log.append(("setuid", uid)))
    monkeypatch.setattr(os, "geteuid", lambda: EXEC_UID if ev.dropped else me)
    monkeypatch.setattr(
        os,
        "setuid",
        lambda uid: (ev.log.append(("setuid", uid)), setattr(ev, "dropped", True)),
    )
    monkeypatch.setattr(os, "getuid", lambda: me)
    with pytest.raises(StepFailed, match="uid"):
        start(world, snap_of(world))
    assert ev.of("execve") == []


def test_no_role_home_or_product_file_read_before_the_drop(
    world: FakeLayout, ev: Ev, monkeypatch: pytest.MonkeyPatch
) -> None:
    hits: list[tuple[str, bool]] = []
    forbidden = (str(world.layout.role_home("df-exec")), "/x/checkout")
    forbidden += (str(world.layout.role_home("df-review")),)
    forbidden += (str(world.layout.role_home("df-publisher")),)

    def wrap(real):  # noqa: ANN001, ANN202
        def seen(path, *a, **kw):  # noqa: ANN001, ANN002, ANN003, ANN202
            if isinstance(path, (str, os.PathLike)):
                if str(path).startswith(forbidden):
                    hits.append((str(path), ev.dropped))
            return real(path, *a, **kw)

        return seen

    home = world.layout.role_home("df-exec")
    home.mkdir(parents=True)
    (home / ".claude").write_text("secret")
    for name in ("open", "stat", "lstat", "listdir", "scandir", "access", "readlink"):
        monkeypatch.setattr(os, name, wrap(getattr(os, name)))
    for owner in (builtins, io):
        monkeypatch.setattr(owner, "open", wrap(owner.open))
    start(world, snap_of(world))
    assert [h for h in hits if not h[1]] == []


def test_pins_from_data_never_reads_role_home(
    world: FakeLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = str(world.layout.role_home("df-exec"))
    hits: list[str] = []
    real = os.open

    def seen(path, *a, **kw):  # noqa: ANN001, ANN002, ANN003, ANN202
        if str(path).startswith(home):
            hits.append(str(path))
        return real(path, *a, **kw)

    monkeypatch.setattr(os, "open", seen)
    world.layout.probes_dir.mkdir(parents=True)
    (world.layout.probes_dir / "author.json").write_text(
        _probe_record("author", models.config_hash(CHOICE)).to_json()
    )
    cfg = ModelsConfig({r: CHOICE for r in models.ROLES}, {})
    pins = run_start.pins_from_data(world.layout, 7, cfg)
    assert pins["author"].harness_version == "9.9.9" and hits == []


def test_release_code_not_executed_before_drop(
    world: FakeLayout, ev: Ev, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The release's interpreter is a sentinel that writes a file when run as
    root; `run start` never starts it (the root test of Task 14 repeats this
    with a real `sudo`)."""
    marker = world.layout.root / "sentinel-ran"
    python = world.layout.release_dir("rel-a") / "venv" / "devtools" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(f"#!/bin/sh\necho ran > {marker}\n")
    python.chmod(0o755)
    started: list[object] = []

    def boom(*a: object, **k: object) -> None:
        started.append(a)
        raise AssertionError("a process was started")

    for owner, name in (
        (subprocess, "Popen"),
        (subprocess, "run"),
        (os, "system"),
        (os, "posix_spawn"),
        (os, "fork"),
    ):
        monkeypatch.setattr(owner, name, boom)
    opened: list[str] = []
    real = os.open
    release_dir = str(world.layout.release_dir("rel-a"))

    def seen(path, *a, **kw):  # noqa: ANN001, ANN002, ANN003, ANN202
        if str(path).startswith(release_dir):
            opened.append(str(path))
        return real(path, *a, **kw)

    monkeypatch.setattr(os, "open", seen)
    start(world, snap_of(world))
    assert not marker.exists() and started == [] and opened == []
    exe, _, _ = execve_of(ev)
    assert exe == str(world.layout.libexec / "df-launch")
    assert ev.dropped


# --- resume ---------------------------------------------------------------------------


def put_run(
    world: FakeLayout, run_id: str = "run-1", release: str = "rel-a"
) -> RunRecord:
    rec = record(run_id, release)
    put_record(world, rec)
    return rec


def resume(world: FakeLayout, snap, *extra: str, run_id: str = "run-1") -> int:  # noqa: ANN001
    return run_start.main(["resume", run_id, *extra], layout=world.layout, snap=snap)


def events_of(world: FakeLayout, run_id: str = "run-1") -> list[str]:
    path = world.layout.runs_dir / f"{run_id}.events.jsonl"
    return path.read_text().splitlines() if path.exists() else []


def same_models_snap(world: FakeLayout, **roles: ModelChoice):  # noqa: ANN201
    return snap_of(world, "rel-a", A, **roles)


def test_resume_without_change_runs_on_the_recorded_release(
    world: FakeLayout, ev: Ev
) -> None:
    rec = put_run(world)
    pin_models = {r: _pin_of(CHOICE) for r in models.ROLES}
    put_record(world, _with_models(rec, pin_models))
    before = (world.layout.runs_dir / "run-1.json").read_text()
    # `current` is rel-b; the record's release rel-a is allowed in the registry
    snap = snap_of(world, "rel-b", A, B)
    assert resume(world, snap) == 1
    assert (world.layout.runs_dir / "run-1.json").read_text() == before
    assert events_of(world) == []
    assert ev.stdin == b"run-1\n" and execve_of(ev)[1] == [
        "df-launch",
        "df-exec",
        "run",
    ]


def _pin_of(choice: ModelChoice):  # noqa: ANN202
    from df.stage2_config import ModelPin

    return ModelPin(
        choice.harness,
        "не проверено",
        choice.model,
        "не сообщён",
        choice.effort,
        dict(choice.params),
        models.config_hash(choice),
    )


def _with_models(rec: RunRecord, pins) -> RunRecord:  # noqa: ANN001
    return RunRecord(
        rec.run_id, rec.release_id, rec.release_digest, rec.admin_digest,
        rec.product, pins, rec.root_id, rec.started_at, rec.started_by_uid,
    )  # fmt: skip


def put_run_with_pins(world: FakeLayout, choice: ModelChoice = CHOICE) -> RunRecord:
    rec = _with_models(
        record("run-1", "rel-a"), {r: _pin_of(choice) for r in models.ROLES}
    )
    put_record(world, rec)
    return rec


def test_resume_revoked_release_refused_even_if_current_allowed(
    world: FakeLayout, ev: Ev
) -> None:
    put_run_with_pins(world)
    snap = snap_of(world, "rel-b", entry("rel-a", "revoked"), B)
    with pytest.raises(StepFailed, match="release_revoked"):
        resume(world, snap)
    assert ev.log == [] and events_of(world) == []


def test_resume_digest_mismatch_refused(world: FakeLayout, ev: Ev) -> None:
    put_run_with_pins(world)
    snap = snap_of(world, "rel-a", entry("rel-a", digest="sha256:" + "9" * 64))
    with pytest.raises(StepFailed, match="release_digest_mismatch"):
        resume(world, snap)
    assert ev.log == []


def test_resume_unknown_run_refused(world: FakeLayout, ev: Ev) -> None:
    with pytest.raises(StepFailed):
        resume(world, snap_of(world), run_id="run-nope")
    assert ev.log == []


def test_resume_model_change_needs_flag_and_step(world: FakeLayout, ev: Ev) -> None:
    put_run_with_pins(world)
    changed = snap_of(world, "rel-a", A, author=OTHER, judge=OTHER)
    with pytest.raises(StepFailed) as err:
        resume(world, changed)
    text = str(err.value)
    assert "author" in text and "judge" in text and RESUME_HINT in text
    assert "reviewer" not in text
    assert ev.log == [] and events_of(world) == []
    # the flags together: the events are written, then the drop and the exec
    assert resume(world, changed, "--accept-model-change", "--from-step", "3") == 1
    lines = events_of(world)
    assert len(lines) == 2
    parsed = [parse_model_change(line) for line in lines]
    assert {e.role for e in parsed} == {"author", "judge"}
    assert all(e.from_step == 3 and e.run_id == "run-1" for e in parsed)
    assert all(
        line == dump_model_change(e) for line, e in zip(lines, parsed, strict=True)
    )
    rec = parse_run((world.layout.runs_dir / "run-1.json").read_text())
    pins = effective_pins(rec, lines)
    assert pins["author"].model == "sonnet" and pins["reviewer"].model == "opus"
    assert ev.names().index("setuid") < ev.names().index("execve")


def test_events_are_written_before_the_drop(
    world: FakeLayout, ev: Ev, monkeypatch: pytest.MonkeyPatch
) -> None:
    put_run_with_pins(world)
    seen: list[bool] = []
    real = run_start.drop_privileges

    def spy(account) -> None:  # noqa: ANN001
        seen.append(bool(events_of(world)))
        real(account)

    monkeypatch.setattr(run_start, "drop_privileges", spy)
    resume(
        world,
        snap_of(world, "rel-a", A, author=OTHER),
        "--accept-model-change",
        "--from-step",
        "1",
    )
    assert seen == [True]


def test_accepted_change_is_the_baseline_for_the_next_resume(
    world: FakeLayout, ev: Ev
) -> None:
    put_run_with_pins(world)
    changed = snap_of(world, "rel-a", A, author=OTHER)
    resume(world, changed, "--accept-model-change", "--from-step", "2")
    ev.reset()
    assert resume(world, changed) == 1  # no flag needed: nothing new changed
    assert len(events_of(world)) == 1
    ev.reset()
    again = snap_of(world, "rel-a", A, author=CHOICE)
    with pytest.raises(StepFailed, match=RESUME_HINT):
        resume(world, again)  # a change back is a change too


def test_flags_without_a_change_write_nothing(world: FakeLayout, ev: Ev) -> None:
    put_run_with_pins(world)
    snap = snap_of(world)
    assert resume(world, snap, "--accept-model-change", "--from-step", "2") == 1
    assert events_of(world) == [] and ev.of("execve")


def test_events_append_to_an_existing_file(world: FakeLayout, ev: Ev) -> None:
    put_run_with_pins(world)
    resume(
        world,
        snap_of(world, "rel-a", A, author=OTHER),
        "--accept-model-change",
        "--from-step",
        "2",
    )
    path = world.layout.runs_dir / "run-1.events.jsonl"
    assert path.stat().st_mode & 0o777 == 0o644
    first = path.read_text()
    ev.reset()
    resume(
        world,
        snap_of(world, "rel-a", A, author=OTHER, judge=OTHER),
        "--accept-model-change",
        "--from-step",
        "4",
    )
    text = path.read_text()
    assert text.startswith(first) and len(text.splitlines()) == 2


def test_a_linked_events_file_is_refused(world: FakeLayout, ev: Ev) -> None:
    put_run_with_pins(world)
    victim = world.layout.root / "victim"
    victim.write_text("keep")
    (world.layout.runs_dir / "run-1.events.jsonl").symlink_to(victim)
    with pytest.raises((StepFailed, OSError)):
        resume(
            world,
            snap_of(world, "rel-a", A, author=OTHER),
            "--accept-model-change",
            "--from-step",
            "2",
        )
    assert victim.read_text() == "keep" and ev.of("execve") == []


def test_a_foreign_event_in_the_file_refuses(world: FakeLayout, ev: Ev) -> None:
    put_run_with_pins(world)
    foreign = ModelChange("run-9", "author", _pin_of(CHOICE), _pin_of(OTHER), 1)
    path = world.layout.runs_dir / "run-1.events.jsonl"
    path.write_text(dump_model_change(foreign) + "\n")
    path.chmod(0o644)
    with pytest.raises((StepFailed, ConfigError)):
        resume(world, snap_of(world))
    assert ev.log == []


# --- reads nothing again (carried from Task 5) -------------------------------------------


def test_start_and_resume_reread_nothing(
    world: FakeLayout, ev: Ev, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the snapshot `releases.toml`, `install.toml`, `models.toml` and
    `current` are not read; edits after it mix nothing in."""
    put_run_with_pins(world)
    put_registry(world, entry("rel-a", "revoked"), B)
    (world.layout.etc / "install.toml").write_text("garbage")
    (world.layout.etc / "models.toml").write_text("garbage")
    spy = Spy(monkeypatch)
    resume(world, snap_of(world, "rel-b", A, B))
    ev.reset()
    start_snap = snap_of(world, "rel-b", B)
    start(world, start_snap)
    assert spy.hits == []
    ev.reset()
    revoked = snap_of(world, "rel-b", entry("rel-a", "revoked"), B)
    with pytest.raises(StepFailed, match="release_revoked"):
        resume(world, revoked)
    assert spy.hits == []


# --- the human form --------------------------------------------------------------------


def test_human_main_chdir_before_sudo(world: FakeLayout, ev: Ev) -> None:
    argv = ["resume", "run-1", "--accept-model-change", "--from-step", "2"]
    assert run_start.human_main(argv, layout=world.layout) == 1
    names = ev.names()
    assert names == ["chdir", "closerange", "execv"]
    assert ev.log[0] == ("chdir", Path("/"))
    _, exe, args = ev.log[2]
    assert exe == "/usr/bin/sudo"
    assert args == ["sudo", str(world.layout.run_start), *argv]
    assert "-n" not in args


def test_human_main_refuses_a_bad_form_before_anything(
    world: FakeLayout, ev: Ev
) -> None:
    with pytest.raises(StepFailed):
        run_start.human_main(
            ["resume", "run-1", "--from-step", "2"], layout=world.layout
        )
    assert ev.log == []


# --- through cli.main --------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["run"],
        ["run", "stop"],
        ["run", "start"],
        ["run", "start", "--product", "7", "extra"],
        ["run", "resume", "r", "--accept-model-change"],
        ["run", "resume", "r", "--from-step", "2"],
        ["run", "resume", "r", "--accept-model-change", "--from-step", "0"],
    ],
)
def test_cli_parse_refuses_bad_forms(argv: list[str]) -> None:
    with pytest.raises(cli.UsageError):
        cli._parse(argv)  # noqa: SLF001


def raw_of(world: FakeLayout) -> state.RawSnapshot:
    from df.config import ReleasesConfig, dump_releases

    text = dump_releases(ReleasesConfig((A,))).encode()
    return state.RawSnapshot(1, "rel-a", text, None, put_models(world).encode(), False)


def test_cli_dispatches_root_to_main_and_human_to_sudo(
    world: FakeLayout, ev: Ev, monkeypatch: pytest.MonkeyPatch
) -> None:
    read = Stable(raw_of(world), 1)
    monkeypatch.setattr("df.cli.ROOT_UID", os.getuid())  # the fake root
    code = cli.main(
        ["run", "start", "--product", "7"],
        mode="reader", read=read, lock_fd=None, layout=world.layout,
    )  # fmt: skip
    assert code == 1 and ev.of("execve")
    ev.reset()
    monkeypatch.setattr("df.cli.ROOT_UID", os.getuid() + 1)  # a human
    code = cli.main(
        ["run", "start", "--product", "7"],
        mode="reader", read=read, lock_fd=None, layout=world.layout,
    )  # fmt: skip
    assert code == 1 and ev.names() == ["chdir", "closerange", "execv"]


def test_cli_transitional_state_is_refused_for_root(
    world: FakeLayout, ev: Ev, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("df.cli.ROOT_UID", os.getuid())
    code = cli.main(
        ["run", "start", "--product", "7"],
        mode="reader", read=Transitional(1), lock_fd=None, layout=world.layout,
    )  # fmt: skip
    assert code == 2 and ev.log == [] and records(world) == []


# --- fix round 1 -----------------------------------------------------------------------


def test_start_with_a_ten_digit_product(world: FakeLayout, ev: Ev) -> None:
    big = 1012345678
    (world.layout.etc / "products.toml").write_text(
        dump_products({big: Product(big, "o/r", "write", 1, 2, "u", "/x/checkout")})
    )
    run_start.main(
        ["start", "--product", str(big)], layout=world.layout, snap=snap_of(world)
    )
    assert parse_run(records(world)[0].read_text()).product == big


def test_cli_accepts_a_ten_digit_product() -> None:
    args = cli._parse(["run", "start", "--product", "1012345678"])  # noqa: SLF001
    assert args.words == ["start", "--product", "1012345678"]
    assert cli._parse(["doctor", "--product", "1012345678"]).product == 1012345678  # noqa: SLF001


def test_started_by_is_the_sudo_uid(
    world: FakeLayout, ev: Ev, monkeypatch: pytest.MonkeyPatch
) -> None:
    for value, want in (
        ("501", 501),
        ("", 0),
        ("abc", 0),
        ("-1", 0),
        ("99999999999", 0),
        ("9" * 5000, 0),
        ("٣", 0),
    ):
        ev.reset()
        for path in records(world):
            path.unlink()
        monkeypatch.setenv("SUDO_UID", value)
        start(world, snap_of(world))
        assert parse_run(records(world)[0].read_text()).started_by_uid == want, value
    ev.reset()
    for path in records(world):
        path.unlink()
    monkeypatch.delenv("SUDO_UID")
    start(world, snap_of(world))
    assert parse_run(records(world)[0].read_text()).started_by_uid == 0


def test_an_empty_harness_version_in_a_probe_does_not_abort_start(
    world: FakeLayout, ev: Ev
) -> None:
    world.layout.probes_dir.mkdir(parents=True)
    odd = _probe_record(
        "author", models.config_hash(CHOICE), harness_version="", actual_model=""
    )
    (world.layout.probes_dir / "author.json").write_text(odd.to_json())
    start(world, snap_of(world))
    pin = parse_run(records(world)[0].read_text()).models["author"]
    assert (pin.harness_version, pin.actual_model) == ("не проверено", "не сообщён")


def test_a_record_missing_a_role_is_a_clean_refusal(world: FakeLayout, ev: Ev) -> None:
    rec = record("run-1", "rel-a")
    few = {r: _pin_of(CHOICE) for r in models.ROLES if r != "judge"}
    put_record(world, _with_models(rec, few))
    with pytest.raises(StepFailed, match="нет пина роли judge"):
        resume(world, snap_of(world))
    assert ev.log == []


def test_cli_missing_role_exits_2_without_a_traceback(
    world: FakeLayout,
    ev: Ev,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("df.cli.ROOT_UID", os.getuid())
    few = {r: _pin_of(CHOICE) for r in models.ROLES if r != "judge"}
    put_record(world, _with_models(record("run-1", "rel-a"), few))
    code = cli.main(
        ["run", "resume", "run-1"],
        mode="reader", read=Stable(raw_of(world), 1), lock_fd=None, layout=world.layout,
    )  # fmt: skip
    err = capsys.readouterr().err
    assert code == 2 and "judge" in err and "Traceback" not in err


def test_the_existing_events_file_is_judged_again_at_append(
    world: FakeLayout, ev: Ev, monkeypatch: pytest.MonkeyPatch
) -> None:
    put_run_with_pins(world)
    path = world.layout.runs_dir / "run-1.events.jsonl"
    path.write_text("")
    path.chmod(0o666)  # appears between the read and the append
    real = launch.read_root_file

    def unseen(file: Path, **kw: bool) -> str | None:
        return None if file.name.endswith(".events.jsonl") else real(file, **kw)

    monkeypatch.setattr(launch, "read_root_file", unseen)
    with pytest.raises(StepFailed, match="запись для группы или всех"):
        resume(
            world,
            snap_of(world, "rel-a", A, author=OTHER),
            "--accept-model-change",
            "--from-step",
            "2",
        )
    assert path.read_text() == "" and ev.of("execve") == []


def test_concurrent_resumes_take_turns(
    world: FakeLayout, ev: Ev, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second resume gets the lock only after the first appended: it
    reads that event, finds nothing new and writes none (no stale `before`)."""
    import fcntl

    put_run_with_pins(world)
    changed = snap_of(world, "rel-a", A, author=OTHER)
    flags = ("--accept-model-change", "--from-step", "2")
    real = fcntl.flock
    calls: list[int] = []

    def first_finishes_while_we_wait(fd: int, how: int) -> None:
        calls.append(how)
        if len(calls) == 1:
            monkeypatch.setattr(fcntl, "flock", lambda *a: None)
            resume(world, changed, *flags)  # the other process, to its end
            ev.reset()
            monkeypatch.setattr(fcntl, "flock", first_finishes_while_we_wait)
        real(fd, how)

    monkeypatch.setattr(fcntl, "flock", first_finishes_while_we_wait)
    assert resume(world, changed, *flags) == 1
    assert calls[0] & fcntl.LOCK_EX
    lines = events_of(world)
    assert len(lines) == 1
    rec = parse_run((world.layout.runs_dir / "run-1.json").read_text())
    assert effective_pins(rec, lines)["author"].model == "sonnet"


def test_a_pin_change_names_the_role_field_and_values(
    world: FakeLayout, ev: Ev
) -> None:
    put_run_with_pins(world)
    with pytest.raises(StepFailed) as err:
        resume(world, snap_of(world, "rel-a", A, author=OTHER))
    text = str(err.value)
    assert "пин изменился: author: model opus → sonnet" in text
    assert "effort high → None" in text
    assert text.endswith(RESUME_HINT)
    assert "модель изменилась" not in text


def test_a_probe_appearing_after_start_is_a_pin_change(
    world: FakeLayout, ev: Ev
) -> None:
    put_run_with_pins(world)
    world.layout.probes_dir.mkdir(parents=True)
    probed = _probe_record("author", models.config_hash(CHOICE))
    (world.layout.probes_dir / "author.json").write_text(probed.to_json())
    with pytest.raises(StepFailed) as err:
        resume(world, snap_of(world))
    assert "author: harness_version не проверено → 9.9.9" in str(err.value)


# --- the resume lock is root's alone ----------------------------------------------------


def test_a_role_holding_the_record_does_not_block_resume(
    world: FakeLayout, ev: Ev
) -> None:
    import fcntl

    put_run_with_pins(world)
    fd = os.open(world.layout.runs_dir / "run-1.json", os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)  # what a role can do to the record
        assert resume(world, snap_of(world)) == 1
    finally:
        os.close(fd)
    lock = world.layout.runs_dir / ".locks" / "run-1.lock"
    assert lock.stat().st_mode & 0o777 == 0o600
    assert (world.layout.runs_dir / ".locks").stat().st_mode & 0o777 == 0o700


def test_contention_past_the_bound_refuses(
    world: FakeLayout, ev: Ev, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fcntl

    put_run_with_pins(world)
    resume(world, snap_of(world))  # creates the lock file
    ev.reset()
    other = os.open(world.layout.runs_dir / ".locks" / "run-1.lock", os.O_RDONLY)
    fcntl.flock(other, fcntl.LOCK_EX)  # another resume holds it
    monkeypatch.setattr(run_start, "LOCK_WAIT", 0.2)
    monkeypatch.setattr(run_start, "LOCK_POLL", 0.01)
    try:
        with pytest.raises(StepFailed, match="прогон занят другим resume"):
            resume(world, snap_of(world))
    finally:
        os.close(other)
    assert ev.log == []
    ev.reset()
    assert resume(world, snap_of(world)) == 1  # released: it goes through


@pytest.mark.parametrize("mode", [0o755, 0o770, 0o777])
def test_a_lock_directory_with_wrong_mode_refuses(
    world: FakeLayout, ev: Ev, mode: int
) -> None:
    put_run_with_pins(world)
    directory = world.layout.runs_dir / ".locks"
    directory.mkdir()
    directory.chmod(mode)
    with pytest.raises(StepFailed, match="права"):
        resume(world, snap_of(world))
    assert ev.log == []


def test_a_lock_directory_of_another_owner_refuses(
    world: FakeLayout, ev: Ev, monkeypatch: pytest.MonkeyPatch
) -> None:
    from df import fsutil

    put_run_with_pins(world)
    directory = world.layout.runs_dir / ".locks"
    directory.mkdir(mode=0o700)
    real = fsutil.owner_uid
    monkeypatch.setattr(
        fsutil,
        "owner_uid",
        lambda st: 4711 if st.st_ino == directory.stat().st_ino else real(st),
    )
    with pytest.raises(StepFailed, match="владелец"):
        resume(world, snap_of(world))
    assert ev.log == []


def test_the_wait_is_bounded_by_the_clock(
    world: FakeLayout, ev: Ev, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lock is always taken: the resume sleeps `LOCK_WAIT` seconds of a
    fake clock in `LOCK_POLL` steps, no more than one step over, then refuses."""
    import fcntl
    from types import SimpleNamespace

    put_run_with_pins(world)
    clock = [1000.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds
        assert len(sleeps) < 10_000, "the wait has no bound"

    def busy(fd: int, how: int) -> None:
        raise BlockingIOError(11, "busy")

    monkeypatch.setattr(
        run_start, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep)
    )
    monkeypatch.setattr(fcntl, "flock", busy)
    with pytest.raises(StepFailed, match="прогон занят другим resume"):
        resume(world, snap_of(world))
    assert sum(sleeps) >= run_start.LOCK_WAIT
    assert len(sleeps) <= run_start.LOCK_WAIT / run_start.LOCK_POLL + 2
    assert ev.log == []


def test_a_lock_directory_that_is_a_link_refuses(world: FakeLayout, ev: Ev) -> None:
    put_run_with_pins(world)
    elsewhere = world.layout.root / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    (world.layout.runs_dir / ".locks").symlink_to(elsewhere)
    with pytest.raises(StepFailed):
        resume(world, snap_of(world))
    assert list(elsewhere.iterdir()) == [] and ev.log == []


@pytest.mark.parametrize("how", ["mode", "link", "hardlink"])
def test_a_bad_lock_file_refuses(world: FakeLayout, ev: Ev, how: str) -> None:
    put_run_with_pins(world)
    directory = world.layout.runs_dir / ".locks"
    directory.mkdir(mode=0o700)
    lock = directory / "run-1.lock"
    if how == "mode":
        lock.write_text("")
        lock.chmod(0o644)
    elif how == "hardlink":
        lock.write_text("")
        lock.chmod(0o600)
        os.link(lock, directory / "twin")
    else:
        lock.symlink_to(world.layout.root / "elsewhere")
    with pytest.raises(StepFailed):
        resume(world, snap_of(world))
    assert ev.log == []
