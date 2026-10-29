"""Task 12: `darkfactory doctor` level 1 from facts (spec 3.8, 2.4).

The world is the installed world of Tasks 9-B/9-C (`installed`): a real
bootstrap `init` over `fake_layout` with the stand-ins of `make_world`. The
checks read one `Snapshot`; the files under `/etc/df` and `current` are
never read again (audit hook and `watching`).
"""

from __future__ import annotations

import contextlib
import dataclasses
import errno
import json
import os
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

import df
from df import (
    acl,
    admin,
    boundary,
    cli,
    digest,
    doctor,
    fsutil,
    journal,
    launch,
    launcher_entry,
    libexec,
    probe,
    roles_setup,
    setup_agent,
    state,
    sudoers,
)
from df import build as build_mod
from df import platform as plat
from df.config import Account, ConfigError, dump_accounts, load_accounts
from df.doctor import (
    Check,
    doctor_main,
    level1_checks,
    level1_reached,
    render_brief,
    render_json,
)
from df.journal import Stable, Transitional, journal_event
from df.models import ROLES
from df.paths import Layout
from df.publisher import request
from df.publisher.github import Installation, PerimeterStats, SelfCheckResult
from df.publisher.request import Response
from df.stage2_config import Product, dump_products
from tests.df.conftest import FakeLayout
from tests.df.install_world import (
    OTHER_SHA,
    World,
    installed,
    interrupt,
    make_world,
)
from tests.df.route_world import BOOT
from tests.df.test_install_steps import watching
from tests.df.test_launcher_entry import ID, record, run_entry, world, write_registry

SET_OPENSSL = ["cli", "set", "openssl", "/usr/bin/openssl"]
LEVEL1_IDS = [
    "install.pending",
    "etc.schemas",
    "release.active",
    "release.digest",
    "release.admin",
    "release.inputs",
    "release.perms",
    "release.boundary",
    "trusted.paths",
    "cli.approved",
    "build.user",
]
SET_AUTHOR = ["models", "set", "author", "--harness", "claude", "--model", "m-a"]
SET_REVIEWER = ["models", "set", "reviewer", "--harness", "claude", "--model", "m-a"]
SAME_MODELS = "предупреждение: модели автора и ревьюера совпадают"
SUDO = "нужен sudo"

#: Paths the audit hook records while a test listens (`audit_opens`).
_OPENS: list[list[str]] = []


def _audit(event: str, args: tuple[object, ...]) -> None:
    if event == "open" and _OPENS and args and isinstance(args[0], (str, Path)):
        _OPENS[-1].append(str(args[0]))


sys.addaudithook(_audit)


@contextlib.contextmanager
def audit_opens() -> Iterator[list[str]]:
    """The paths opened (audit event `open`) inside the block."""
    opens: list[str] = []
    _OPENS.append(opens)
    try:
        yield opens
    finally:
        _OPENS.remove(opens)


@pytest.fixture(autouse=True)
def _boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fsutil, "boot_id", lambda: BOOT)


@pytest.fixture()
def fake_installed(monkeypatch: pytest.MonkeyPatch, fake_layout: FakeLayout) -> World:
    """A committed first install; the current user plays root for `doctor`."""
    w = make_world(fake_layout, monkeypatch)
    installed(w)
    monkeypatch.setattr(doctor, "ROOT_UID", os.geteuid())
    return w


def snap(w: World) -> state.Snapshot:
    """The reader's snapshot of the world as it is now."""
    return state.parse_snapshot(state.read_raw(w.lay), w.lay, mode="reader")


def by_id(checks: list[Check]) -> dict[str, Check]:
    return {c.id: c for c in checks}


def checks_of(w: World, *, euid: int | None = None) -> dict[str, Check]:
    uid = os.geteuid() if euid is None else euid
    return by_id(level1_checks(snap(w), w.lay, euid=uid))


def release(w: World) -> Path:
    rid = os.readlink(w.lay.current).removeprefix("releases/")
    return w.lay.release_dir(rid)


def tamper_admin(w: World) -> None:
    """Append to the first regular file of the active release's admin code."""
    s = snap(w)
    assert s.release_id is not None
    files = sorted(p for p in w.lay.admin_dir(s.release_id).rglob("*") if p.is_file())
    files[0].write_bytes(files[0].read_bytes() + b"\n# changed\n")


def run_doctor(w: World, argv: list[str], read: state.ReadResult | None = None) -> int:
    if read is None:
        raw = state.read_raw(w.lay)
        read = Stable(raw, raw.generation)
    return cli.main(
        ["doctor", *argv], mode="reader", read=read, lock_fd=None, layout=w.lay
    )


def blocking(checks: dict[str, Check]) -> dict[str, str]:
    return {k: c.status for k, c in checks.items() if c.status != "ok"}


# ---------- level 1 reached ----------


def test_clean_install_level1_reached(
    fake_installed: World, capsys: pytest.CaptureFixture[str]
) -> None:
    checks = level1_checks(snap(fake_installed), fake_installed.lay, euid=os.geteuid())
    assert [c.id for c in checks] == LEVEL1_IDS
    assert all(c.status == "ok" for c in checks), checks
    assert all(c.time for c in checks)
    assert level1_reached(checks)
    assert render_brief(checks) == "уровень 1: достигнут"
    assert run_doctor(fake_installed, []) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "уровень 1: достигнут"
    assert SAME_MODELS not in out


def test_tampered_file_breaks_digest(fake_installed: World) -> None:
    """§3.8 «подмена файла в релизе после установки → doctor «нарушено»»."""
    rel = release(fake_installed)
    (rel / "src" / "devtools").mkdir(parents=True)
    (rel / "src" / "devtools" / "README.md").write_text("x")
    checks = level1_checks(snap(fake_installed), fake_installed.lay, euid=os.geteuid())
    c = by_id(checks)
    assert c["release.digest"].status == "нарушено"
    assert c["release.digest"].reason.startswith("релиз повреждён")
    # R72: the boundary is checked only over a release whose digest holds
    assert c["release.boundary"].status == "не проверено"
    assert c["release.boundary"].reason == "release.digest нарушен"
    assert not level1_reached(checks)
    assert render_brief(checks).startswith("уровень 1: не достигнут — release.digest")


def test_tampered_existing_file_breaks_digest(fake_installed: World) -> None:
    rel = release(fake_installed)
    lock = rel / "python" / ".lock"
    lock.write_bytes(lock.read_bytes() + b"x")
    assert checks_of(fake_installed)["release.digest"].status == "нарушено"


def test_release_json_digest_disagrees(fake_installed: World) -> None:
    """The tree equals the registry, but `release.json` names another digest."""
    path = release(fake_installed) / "release.json"
    data = json.loads(path.read_text())
    data["digest"] = "sha256:" + "f" * 64
    path.write_text(json.dumps(data))
    c = checks_of(fake_installed)
    assert c["release.digest"].status == "нарушено"
    assert c["release.digest"].reason.startswith("релиз повреждён")


def test_registry_digest_disagrees(fake_installed: World) -> None:
    s = snap(fake_installed)
    assert s.entry is not None
    bad = dataclasses.replace(s.entry, digest="sha256:" + "e" * 64)
    checks = by_id(
        level1_checks(
            dataclasses.replace(s, entry=bad), fake_installed.lay, euid=os.geteuid()
        )
    )
    assert checks["release.digest"].status == "нарушено"


# ---------- transitional ----------


def test_doctor_transitional(
    fake_installed: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """`Transitional(g)`: no part of a snapshot, no file of the layout."""
    with audit_opens() as opens, watching(fake_installed) as reads:
        code = doctor_main(Transitional(7), fake_installed.lay, euid=0, as_json=True)
    assert code == cli.EXIT_REFUSED
    assert json.loads(capsys.readouterr().out) == {
        "level1": "not reached",
        "state": "transitional",
        "generation": 7,
    }
    root = str(fake_installed.lay.root)
    assert [p for p in opens if p.startswith(root)] == []
    assert reads.hits == []
    assert doctor_main(Transitional(7), fake_installed.lay, euid=0, as_json=False) == 2
    assert capsys.readouterr().out == (
        f"уровень 1: не достигнут — install.transitional: {doctor.TRANSITIONAL}\n"
    )


def test_transitional_text_names_what_recovers() -> None:
    """#643 recheck: an even `Transitional` is `pending.toml` (only `install`
    recovers; the other writers are refused there); an odd one may be
    either, so it names both ways. `roles setup` is not offered: without
    `openssl` it refuses before it can close the generation."""
    assert doctor.transitional_text(8) == doctor.TRANSITIONAL_PENDING
    assert doctor.transitional_text(7) == doctor.TRANSITIONAL
    pending = doctor.TRANSITIONAL_PENDING
    assert "darkfactory install" in pending
    for writer in ("models set", "cli set", "roles setup"):
        assert writer not in pending
    odd = doctor.TRANSITIONAL
    assert "если установка не завершена — darkfactory install" in odd
    assert "models set" in odd and "cli set" in odd
    assert "roles setup" not in odd


def entry_reader(w: World) -> state.ReadResult:
    """The shipped entry's reader read of the world as it is now."""
    w.mp.setattr(launcher_entry, "ROOT_UID", os.geteuid())
    w.mp.setattr(launcher_entry, "owner_uid", w.fl.owner_of)
    paths = launcher_entry.EntryPaths(str(w.lay.root))
    return launcher_entry.typed_read(
        state, launcher_entry.take_snapshot(paths, "reader")
    )


def test_doctor_interrupted_install(
    fake_installed: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """An install killed inside its bracket: the entry's reader gets an odd
    `Transitional` (it cannot see `pending.toml` then), so doctor gives the
    conditional text whose first way is `darkfactory install`; the other
    writers are refused (`_FINISH`) and `install` recovers."""
    w = fake_installed
    interrupt(w, w.writer, ["install"], "build")
    read = entry_reader(w)
    assert isinstance(read, Transitional) and read.generation % 2 == 1
    capsys.readouterr()
    assert run_doctor(w, [], read) == 2
    assert capsys.readouterr().out == (
        f"уровень 1: не достигнут — install.transitional: {doctor.TRANSITIONAL}\n"
    )
    assert w.writer(SET_OPENSSL) == 2
    assert "завершите darkfactory install" in capsys.readouterr().err
    assert w.writer(["install"]) == 0
    assert isinstance(entry_reader(w), Stable)


def test_doctor_pending_seen_names_install_only(
    fake_installed: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """`pending.toml` under an even generation: the entry's stable read saw
    it and hands an even `Transitional`; doctor names `install` only."""
    w = fake_installed
    interrupt(w, w.writer, ["install"], "build")
    journal.generation_end(w.lay)
    read = entry_reader(w)
    assert isinstance(read, Transitional) and read.generation % 2 == 0
    capsys.readouterr()
    assert run_doctor(w, [], read) == 2
    assert capsys.readouterr().out == (
        "уровень 1: не достигнут — install.transitional: "
        f"{doctor.TRANSITIONAL_PENDING}\n"
    )


def test_doctor_odd_generation_names_both_ways(
    fake_installed: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """A writer killed inside its bracket (no `pending.toml`): an odd
    `Transitional`; doctor gives the conditional text, and repeating a
    named writer (`cli set`) recovers."""
    w = fake_installed
    journal.generation_begin(w.lay)
    read = entry_reader(w)
    assert isinstance(read, Transitional) and read.generation % 2 == 1
    capsys.readouterr()
    assert run_doctor(w, [], read) == 2
    assert capsys.readouterr().out == (
        f"уровень 1: не достигнут — install.transitional: {doctor.TRANSITIONAL}\n"
    )
    assert w.writer(SET_OPENSSL) == 0
    assert isinstance(entry_reader(w), Stable)


def test_doctor_transitional_through_main(
    fake_installed: World, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_doctor(fake_installed, [], Transitional(3)) == 2
    out = capsys.readouterr()
    assert out.out.startswith("уровень 1: не достигнут — install.transitional: ")
    assert "Task 12" not in out.err


# ---------- the snapshot only ----------


def test_level1_uses_passed_snapshot_only(
    fake_installed: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """Snapshot A is passed; `install.toml`, `models.toml`, `releases.toml`
    and `current` then become B. The real chain `main(read=Stable(A))` →
    `doctor_main` → `level1_checks` and the models section opens none of
    them, never reads `current` as a link, and reports A (S1-R5-N2)."""
    w = fake_installed
    lay = w.lay
    assert w.writer(SET_AUTHOR) == 0
    raw_a = state.read_raw(lay)
    capsys.readouterr()
    # B: another configuration, no model choice, another registry, `current`
    # on a release that does not exist
    install_b = (lay.etc / "install.toml").read_text().replace("0" * 40, OTHER_SHA)
    (lay.etc / "install.toml").write_text(install_b)
    (lay.etc / "models.toml").write_text('schema = "df/models/v1"\n')
    (lay.etc / "releases.toml").write_text('schema = "df/releases/v1"\n')
    os.unlink(lay.current)
    os.symlink("releases/other-1", lay.current)
    with audit_opens() as opens, watching(w) as reads:
        code = run_doctor(w, ["--json"], Stable(raw_a, raw_a.generation))
    assert code == 0, capsys.readouterr()
    report = json.loads(capsys.readouterr().out)
    assert report["level1"] == "reached"
    roles = {m["role"]: m["status"] for m in report["models"]}
    assert roles["author"] == "не проверено"  # A's choice, not B's «не задана»
    assert roles["judge"] == "модель не задана"
    snapshot_files = {
        str(lay.etc / n) for n in ("install.toml", "releases.toml", "models.toml")
    } | {str(lay.pending)}
    assert snapshot_files.isdisjoint(opens), opens
    assert reads.hits == []
    with audit_opens() as seen:  # control: the hook sees such an open
        (lay.etc / "install.toml").read_text()
    assert str(lay.etc / "install.toml") in seen


# ---------- facts, not the journal ----------


def test_level1_ignores_journal(fake_installed: World) -> None:
    """M19, the doctor half of `test_crash_after_activate_before_journal_done`:
    level 1 comes from the facts; the journal is not read."""
    lay = fake_installed.lay
    lay.journal.write_text("")  # no `activate: done`
    checks = level1_checks(snap(fake_installed), lay, euid=os.geteuid())
    assert level1_reached(checks), checks
    # a forged `activate: done` without `current` gives no level
    rid = os.readlink(lay.current).removeprefix("releases/")
    os.unlink(lay.current)
    journal_event(lay.journal, run_id="forged", step="activate", event="done")
    checks = level1_checks(snap(fake_installed), lay, euid=os.geteuid())
    c = by_id(checks)
    assert c["release.active"].status == "нарушено"
    assert not level1_reached(checks)
    assert render_brief(checks).startswith("уровень 1: не достигнут — release.active")
    assert rid  # the release itself is still there


def test_level1_reads_no_journal(fake_installed: World) -> None:
    lay = fake_installed.lay
    s = snap(fake_installed)
    with audit_opens() as opens:
        level1_checks(s, lay, euid=os.geteuid())
    assert str(lay.journal) not in opens


def test_revoked_release_is_not_active(fake_installed: World) -> None:
    s = snap(fake_installed)
    assert s.entry is not None
    revoked = dataclasses.replace(s.entry, status="revoked", reason="bad")
    c = by_id(
        level1_checks(
            dataclasses.replace(s, entry=revoked),
            fake_installed.lay,
            euid=os.geteuid(),
        )
    )
    assert c["release.active"].status == "нарушено"
    assert "отозван" in c["release.active"].reason


# ---------- pending, quarantine, admin, inputs ----------


def test_pending_blocks_level1(fake_installed: World) -> None:
    s = dataclasses.replace(snap(fake_installed), pending_present=True)
    checks = level1_checks(s, fake_installed.lay, euid=os.geteuid())
    c = by_id(checks)
    assert c["install.pending"].status == "нарушено"
    assert c["install.pending"].reason == (
        "переходное состояние, завершите darkfactory install"
    )
    assert not level1_reached(checks)
    assert render_brief(checks) == (
        "уровень 1: не достигнут — install.pending: нарушено: переходное "
        "состояние, завершите darkfactory install"
    )


def test_quarantine_warns_without_blocking(
    fake_installed: World, capsys: pytest.CaptureFixture[str]
) -> None:
    (fake_installed.lay.quarantine / "left-1").mkdir()
    s = snap(fake_installed)
    lay = fake_installed.lay
    assert level1_reached(level1_checks(s, lay, euid=os.geteuid()))
    notes = doctor.warnings(s, lay, euid=os.geteuid())
    assert len(notes) == 1 and notes[0].startswith(
        "предупреждение: staging/.quarantine"
    )
    assert run_doctor(fake_installed, []) == 0
    assert notes[0] in capsys.readouterr().out
    assert run_doctor(fake_installed, ["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["warnings"] == notes


def test_empty_quarantine_no_warning(fake_installed: World) -> None:
    s = snap(fake_installed)
    assert doctor.warnings(s, fake_installed.lay, euid=os.geteuid()) == []


def test_admin_tamper_breaks_level1(fake_installed: World) -> None:
    tamper_admin(fake_installed)
    checks = level1_checks(snap(fake_installed), fake_installed.lay, euid=os.geteuid())
    c = by_id(checks)
    assert c["release.admin"].status == "нарушено"
    assert c["release.digest"].status == "ok"
    assert render_brief(checks).startswith("уровень 1: не достигнут — release.admin")


def test_admin_ready_gets_the_entry_values(
    fake_installed: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rev 8, r11: both expected values come from the snapshot's entry."""
    seen: list[tuple[str, str, str]] = []

    def ready(
        layout: Layout, rid: str, *, expected_admin_digest: str, expected_python: str
    ) -> bool:
        seen.append((rid, expected_admin_digest, expected_python))
        return True

    monkeypatch.setattr(admin, "admin_ready", ready)
    s = snap(fake_installed)
    assert s.entry is not None
    entry = dataclasses.replace(s.entry, admin_digest="sha256:x", admin_python="3.9.9")
    lay = fake_installed.lay
    level1_checks(dataclasses.replace(s, entry=entry), lay, euid=os.geteuid())
    assert seen == [(s.release_id, "sha256:x", "3.9.9")]


def test_inputs_disagree_with_install(fake_installed: World) -> None:
    s = snap(fake_installed)
    assert s.install is not None
    other = dataclasses.replace(s.install, manifest_sha=OTHER_SHA)
    c = by_id(
        level1_checks(
            dataclasses.replace(s, install=other), fake_installed.lay, euid=os.geteuid()
        )
    )
    assert c["release.inputs"].status == "нарушено"
    profile = dataclasses.replace(s.install, profile="q")
    c = by_id(
        level1_checks(
            dataclasses.replace(s, install=profile),
            fake_installed.lay,
            euid=os.geteuid(),
        )
    )
    assert c["release.inputs"].status == "нарушено"


# ---------- permissions ----------


def test_acl_on_release_file_broken(
    fake_installed: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = release(fake_installed) / "python" / ".lock"
    real = acl.acl_present_path

    def present(path: Path) -> bool:
        return Path(path) == target or real(path)

    monkeypatch.setattr(acl, "acl_present_path", present)
    c = checks_of(fake_installed)
    assert c["release.perms"].status == "нарушено"
    assert str(target) in c["release.perms"].reason


def test_acl_error_is_unavailable(
    fake_installed: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(path: Path) -> bool:
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(acl, "acl_present_path", broken)
    c = checks_of(fake_installed)
    assert c["release.perms"].status == "данные недоступны"


@pytest.mark.parametrize("kind", ["group-write", "setuid", "foreign-owner"])
def test_release_perms_broken(
    fake_installed: World, fake_layout: FakeLayout, kind: str
) -> None:
    target = release(fake_installed) / "python" / ".lock"
    if kind == "group-write":
        target.chmod(0o664)
    elif kind == "setuid":
        target.chmod(0o4644)
    else:
        fake_layout.set_owner(target, fake_layout.build_uid)
    assert checks_of(fake_installed)["release.perms"].status == "нарушено"


# ---------- unavailable is never ok ----------


def test_missing_file_is_unavailable_not_ok(fake_installed: World) -> None:
    (release(fake_installed) / "release.json").unlink()
    checks = level1_checks(snap(fake_installed), fake_installed.lay, euid=os.geteuid())
    c = by_id(checks)
    assert c["release.digest"].status == "данные недоступны"
    assert c["release.inputs"].status == "данные недоступны"
    assert c["release.boundary"].status == "не проверено"
    assert not level1_reached(checks)


def test_io_error_is_unavailable_not_ok(
    fake_installed: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(tree: Path) -> str:
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(digest, "release_digest", failing)
    checks = level1_checks(snap(fake_installed), fake_installed.lay, euid=os.geteuid())
    c = by_id(checks)
    assert c["release.digest"].status == "данные недоступны"
    assert "I/O error" in c["release.digest"].reason
    assert not level1_reached(checks)


def test_non_root_needs_sudo(
    fake_installed: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R71: a check that cannot read what it needs as the invoking user is
    «данные недоступны: нужен sudo», never `ok` from a partial read."""
    other = os.geteuid() + 1

    def denied(tree: Path) -> str:
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(digest, "release_digest", denied)
    checks = level1_checks(snap(fake_installed), fake_installed.lay, euid=other)
    c = by_id(checks)
    for name in ("release.digest", "build.user"):
        assert (c[name].status, c[name].reason) == ("данные недоступны", SUDO)
    assert c["release.boundary"].status == "не проверено"
    assert not level1_reached(checks)
    assert "данные недоступны: нужен sudo" in render_brief(checks)


def test_non_root_root_only_checks(fake_installed: World) -> None:
    c = checks_of(fake_installed, euid=os.geteuid() + 1)
    for name in ("release.boundary", "build.user"):
        assert (c[name].status, c[name].reason) == ("данные недоступны", SUDO)


def test_non_root_models_section(
    fake_installed: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """R71: without root a role with a record is «данные недоступны: нужен
    sudo» (Task 11's `current_status`)."""
    w = fake_installed
    assert w.writer(SET_AUTHOR) == 0
    w.mp.setattr(probe, "read_record", lambda *_a: object())
    w.mp.setattr(doctor, "ROOT_UID", os.geteuid() + 1)
    capsys.readouterr()
    assert run_doctor(w, ["--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["level1"] == "not reached"
    roles = {m["role"]: m["status"] for m in report["models"]}
    assert roles["author"] == "данные недоступны: нужен sudo"
    assert roles["judge"] == "модель не задана"


def test_build_user_problems_broken(
    fake_installed: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build_mod, "verify_build_user", lambda _l: ["uid 0"])
    assert checks_of(fake_installed)["build.user"].status == "нарушено"


def test_untrusted_path_broken(fake_installed: World) -> None:
    fake_installed.untrusted.add(fake_installed.lay.etc)
    c = checks_of(fake_installed)
    assert c["trusted.paths"].status == "нарушено"


def test_approved_cli_broken(
    fake_installed: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from df import platform as plat  # noqa: PLC0415

    monkeypatch.setattr(plat, "approved_cli_problems", lambda _c, _p: ["git: bad"])
    assert checks_of(fake_installed)["cli.approved"].status == "нарушено"


def test_boundary_problems_broken(
    fake_installed: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from df import boundary  # noqa: PLC0415

    monkeypatch.setattr(boundary, "static_problems", lambda *_a: ["venv: bad"])
    c = checks_of(fake_installed)
    assert c["release.boundary"].status == "нарушено"
    assert "venv: bad" in c["release.boundary"].reason


# ---------- /etc/df ----------


def test_later_stage_files_not_checked(fake_installed: World) -> None:
    """Files of stages > 1 are named by the listing, never opened."""
    etc = fake_installed.lay.etc
    (etc / "trust.toml").write_text("garbage = [")
    s = snap(fake_installed)
    with audit_opens() as opens:
        checks = level1_checks(s, fake_installed.lay, euid=os.geteuid())
    c = by_id(checks)
    assert (c["etc.trust.toml"].status, c["etc.trust.toml"].reason) == (
        "не проверено",
        "этап 3",
    )
    assert "etc.publisher.toml" not in c
    assert c["etc.schemas"].status == "ok"
    assert level1_reached(checks)
    assert render_brief(checks) == "уровень 1: достигнут"
    assert str(etc / "trust.toml") not in set(opens)


def test_publisher_toml_is_parsed(fake_installed: World) -> None:
    lay = fake_installed.lay
    pub = lay.etc / "publisher.toml"
    pub.write_text('schema = "df/publisher/v1"\n')
    ok = by_id(level1_checks(snap(fake_installed), lay, euid=os.geteuid()))
    assert ok["etc.schemas"].status == "ok"
    pub.write_text("garbage = [")
    bad = by_id(level1_checks(snap(fake_installed), lay, euid=os.geteuid()))
    assert bad["etc.schemas"].status == "нарушено"
    assert "publisher.toml" in bad["etc.schemas"].reason


def test_broken_publisher_does_not_hide_other_problems(
    fake_installed: World,
) -> None:
    lay = fake_installed.lay
    (lay.etc / "publisher.toml").write_text("garbage = [")
    (lay.etc / "extra.toml").write_text("x = 1\n")
    c = by_id(level1_checks(snap(fake_installed), lay, euid=os.geteuid()))
    reason = c["etc.schemas"].reason
    assert c["etc.schemas"].status == "нарушено"
    assert "publisher.toml" in reason and "extra.toml" in reason


def test_publisher_changed_after_snapshot_is_transient(
    fake_installed: World,
) -> None:
    """A writer replaced `publisher.toml` after the snapshot (counter moved):
    no «нарушено» verdict on the half-seen file, «данные недоступны»."""
    w = fake_installed
    s = snap(w)
    (w.lay.etc / "publisher.toml").write_text("garbage = [")
    w.lay.generation.write_text(f"{s.generation + 2}\n")
    c = by_id(level1_checks(s, w.lay, euid=os.geteuid()))["etc.schemas"]
    assert (c.status, c.reason) == (
        "данные недоступны",
        "идёт административная запись, повторите doctor",
    )
    w.lay.generation.write_text(f"{s.generation}\n")  # control: settled
    c = by_id(level1_checks(s, w.lay, euid=os.geteuid()))["etc.schemas"]
    assert c.status == "нарушено" and "publisher.toml" in c.reason


def test_unknown_etc_file_broken(fake_installed: World) -> None:
    (fake_installed.lay.etc / "extra.toml").write_text("x = 1\n")
    checks = level1_checks(snap(fake_installed), fake_installed.lay, euid=os.geteuid())
    c = by_id(checks)
    assert c["etc.schemas"].status == "нарушено"
    assert "extra.toml" in c["etc.schemas"].reason
    assert render_brief(checks).startswith("уровень 1: не достигнут — etc.schemas")


def test_bad_models_text_broken(fake_installed: World) -> None:
    s = dataclasses.replace(snap(fake_installed), models_text="garbage = [")
    c = by_id(level1_checks(s, fake_installed.lay, euid=os.geteuid()))
    assert c["etc.schemas"].status == "нарушено"
    assert "models.toml" in c["etc.schemas"].reason


def test_not_checked_blocks_unless_later_stage() -> None:
    """Only «не проверено (этап N)» of a later-stage file is not blocking."""
    t = "2026-10-24T00:00:00+00:00"
    ok = Check("etc.schemas", "ok", "", t)
    assert level1_reached([ok, Check("etc.trust.toml", "не проверено", "этап 3", t)])
    assert not level1_reached(
        [ok, Check("release.boundary", "не проверено", "этап 3", t)]
    )
    assert not level1_reached(
        [ok, Check("release.boundary", "не проверено", "release.digest нарушен", t)]
    )
    assert not level1_reached([ok, Check("x", "данные недоступны", SUDO, t)])
    assert not level1_reached([])


# ---------- models ----------


def test_same_model_author_reviewer_warns_only(
    fake_installed: World, capsys: pytest.CaptureFixture[str]
) -> None:
    w = fake_installed
    assert w.writer(SET_AUTHOR) == 0
    assert w.writer(SET_REVIEWER) == 0
    capsys.readouterr()
    s = snap(w)
    assert SAME_MODELS in doctor.warnings(s, w.lay, euid=os.geteuid())
    assert level1_reached(level1_checks(s, w.lay, euid=os.geteuid()))
    assert run_doctor(w, []) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "уровень 1: достигнут"
    assert SAME_MODELS in out


def test_models_section_lists_every_role(
    fake_installed: World, capsys: pytest.CaptureFixture[str]
) -> None:
    w = fake_installed
    assert w.writer(SET_AUTHOR) == 0
    capsys.readouterr()
    assert run_doctor(w, []) == 0
    out = capsys.readouterr().out
    assert "author: не проверено" in out
    assert "judge: модель не задана" in out


# ---------- JSON ----------


def test_json_lists_all_problems(
    fake_installed: World, capsys: pytest.CaptureFixture[str]
) -> None:
    w = fake_installed
    rel = release(w)
    (rel / "python" / ".lock").write_text("changed")
    tamper_admin(w)
    (w.lay.etc / "extra.toml").write_text("")
    assert run_doctor(w, ["--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["level1"] == "not reached"
    statuses = {c["id"]: c["status"] for c in report["checks"]}
    assert [c["id"] for c in report["checks"]] == LEVEL1_IDS
    assert {k: v for k, v in statuses.items() if v != "ok"} == {
        "etc.schemas": "нарушено",
        "release.digest": "нарушено",
        "release.admin": "нарушено",
        "release.boundary": "не проверено",
    }
    keys = {"id", "status", "reason", "time", "problems"}
    assert all(set(c) == keys for c in report["checks"])
    assert [m["role"] for m in report["models"]] == list(ROLES)


def test_render_json_shape() -> None:
    t = "2026-10-24T00:00:00+00:00"
    checks = [Check("install.pending", "ok", "", t)]
    data = json.loads(render_json(checks, []))
    assert data == {
        "level1": "reached",
        "checks": [
            {
                "id": "install.pending",
                "status": "ok",
                "reason": "",
                "time": t,
                "problems": [],
            }
        ],
        "warnings": [],
        "models": [],
    }


# ---------- the command line ----------


def test_doctor_json_with_probe_models_refused(
    fake_installed: World, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_doctor(fake_installed, ["--probe-models", "--json"]) == 2
    assert "--json" in capsys.readouterr().err


def test_doctor_exit_codes(
    fake_installed: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """R68: 0 reached, 2 not reached; the brief line names the first id."""
    assert run_doctor(fake_installed, []) == 0
    (release(fake_installed) / "python" / ".lock").write_text("changed")
    capsys.readouterr()
    assert run_doctor(fake_installed, []) == 2
    first = capsys.readouterr().out.splitlines()[0]
    assert first.startswith("уровень 1: не достигнут — release.digest: нарушено")


def test_doctor_allowed_with_pending(
    fake_installed: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """M1/M7: an interrupted install leaves `pending.toml` at an even
    generation; the entry's reader read (`take_snapshot`, `typed_read`) is
    then `Transitional` (launcher_entry.py, `take_snapshot`), so `doctor`
    runs and shows the transitional state — a Stable read with `pending.toml`
    cannot reach it."""
    w = fake_installed
    interrupt(w, w.writer, ["install"], "activate")
    assert journal.read_generation(w.lay) % 2 == 1
    journal.generation_end(w.lay)  # even: only `pending.toml` marks it
    assert os.path.lexists(w.lay.pending)
    paths = launcher_entry.EntryPaths(str(w.lay.root))
    read = launcher_entry.typed_read(
        state, launcher_entry.take_snapshot(paths, "reader")
    )
    assert isinstance(read, Transitional) and read.generation % 2 == 0
    capsys.readouterr()
    assert run_doctor(w, ["--json"], read) == 2
    assert json.loads(capsys.readouterr().out) == {
        "level1": "not reached",
        "state": "transitional",
        "generation": read.generation,
    }


# ---------- the public entry ----------


@pytest.mark.parametrize(
    "generation,pending", [(5, False), (2, True)], ids=["odd", "even-pending"]
)
def test_doctor_transitional_via_public_entry(
    tmp_path: Path, generation: int, pending: bool
) -> None:
    """F4 (Task 8a): the real `darkfactory-entry.py` (`-I -B -S`) at an odd
    generation, or an even one with `pending.toml` (M1), runs the real `df`
    as admin code: exit 2 (level 1 not reached) and the transitional JSON."""
    lay = world(tmp_path, generation=f"{generation}\n".encode(), pending=pending)
    package = lay.admin_dir(ID) / "df"
    shutil.rmtree(package)
    shutil.copytree(
        Path(df.__file__).parent, package, ignore=shutil.ignore_patterns("__pycache__")
    )
    write_registry(lay, [record(ID, admin.admin_digest(lay, ID))])
    done = run_entry(lay, "doctor", "--json")
    assert done.returncode == 2, done.stdout + done.stderr
    assert json.loads(done.stdout) == {
        "level1": "not reached",
        "state": "transitional",
        "generation": generation,
    }


def test_journal_untouched_by_doctor(fake_installed: World) -> None:
    before = fake_installed.lay.journal.read_bytes()
    gen = journal.read_generation(fake_installed.lay)
    assert run_doctor(fake_installed, []) == 0
    assert fake_installed.lay.journal.read_bytes() == before
    assert journal.read_generation(fake_installed.lay) == gen


# ---------- fix round 1 ----------


def test_leftover_write_temp_named(
    fake_installed: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M2: the temp a crash leaves before `write_atomic`'s rename is named
    with what to do; it still blocks (it may hold a partial configuration)."""
    names: list[str] = []

    def crash(tmp: Path, _text: str, _mode: int) -> None:
        names.append(tmp.name)
        tmp.write_text("partial")
        raise KeyboardInterrupt("crash before rename")

    etc = fake_installed.lay.etc
    with monkeypatch.context() as m:
        m.setattr(fsutil, "_write_tmp", crash)
        with pytest.raises(KeyboardInterrupt):
            fsutil.write_atomic(etc / "install.toml", "x")
    assert fsutil.WRITE_TEMP.fullmatch(names[0])
    checks = level1_checks(snap(fake_installed), fake_installed.lay, euid=os.geteuid())
    c = by_id(checks)["etc.schemas"]
    assert c.status == "нарушено"
    assert c.reason == (
        f"осталась временная запись после сбоя: {names[0]}; удалите вручную (sudo rm)"
    )
    assert not level1_reached(checks)
    (etc / ".other.toml.1.tmp").write_text("")
    reason = checks_of(fake_installed)["etc.schemas"].reason
    assert "неизвестные файлы /etc/df: .other.toml.1.tmp" in reason


def with_src(w: World, src: list[str], listed: list[str]) -> state.Snapshot:
    """The release with `src/<r>` for `src` and `release.json` naming
    `listed`; the digest, `release.json` and the entry agree again."""
    rel = release(w)
    for repo in src:
        (rel / "src" / repo).mkdir(parents=True)
        (rel / "src" / repo / "pyproject.toml").write_text('[project]\nname = "x"\n')
    new = digest.release_digest(rel)
    path = rel / "release.json"
    data = json.loads(path.read_text())
    data["digest"] = new
    data["repos"] = [{"name": r} for r in listed]
    path.write_text(json.dumps(data))
    s = snap(w)
    assert s.entry is not None
    return dataclasses.replace(s, entry=dataclasses.replace(s.entry, digest=new))


@pytest.fixture()
def static_calls(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    calls: list[object] = []

    def static(_tree: Path, _platform: str, expected: object) -> list[str]:
        calls.append(expected)
        return []

    monkeypatch.setattr(boundary, "static_problems", static)
    return calls


def test_boundary_repos_from_src(
    fake_installed: World, static_calls: list[object]
) -> None:
    s = with_src(fake_installed, ["devtools"], ["devtools"])
    c = by_id(level1_checks(s, fake_installed.lay, euid=os.geteuid()))
    assert c["release.digest"].status == "ok"
    assert c["release.boundary"].status == "ok"
    assert static_calls == [{"devtools": frozenset()}]


@pytest.mark.parametrize(
    "src,listed",
    [(["devtools"], []), (["devtools", "extra"], ["devtools"])],
    ids=["dropped-from-release-json", "extra-dir-in-src"],
)
def test_boundary_src_disagrees_with_release_json(
    fake_installed: World,
    static_calls: list[object],
    src: list[str],
    listed: list[str],
) -> None:
    """M5: `release.json` is outside the digest; the repos come from `src/`."""
    s = with_src(fake_installed, src, listed)
    c = by_id(level1_checks(s, fake_installed.lay, euid=os.geteuid()))
    assert c["release.digest"].status == "ok"
    assert (c["release.boundary"].status, c["release.boundary"].reason) == (
        "нарушено",
        "состав src/ не совпадает с release.json",
    )
    assert static_calls == []


def test_config_error_of_one_check_is_its_verdict(
    fake_installed: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M6: a `ConfigError`/`ValueError` of one helper is that check's
    «нарушено»; the rest of the report goes on."""

    def bad(*_a: object) -> Path:
        raise ConfigError("invalid python")

    monkeypatch.setattr(admin, "admin_python_path", bad)
    checks = level1_checks(snap(fake_installed), fake_installed.lay, euid=os.geteuid())
    c = by_id(checks)
    assert c["trusted.paths"].status == "нарушено"
    assert "invalid python" in c["trusted.paths"].reason
    assert [x.id for x in checks] == LEVEL1_IDS
    assert c["cli.approved"].status == "ok"
    monkeypatch.setattr(
        build_mod,
        "verify_build_user",
        lambda _l: (_ for _ in ()).throw(ValueError("v")),
    )
    assert checks_of(fake_installed)["build.user"].status == "нарушено"


def test_program_error_propagates(
    fake_installed: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def bug(*_a: object) -> list[str]:
        raise RuntimeError("bug")

    monkeypatch.setattr(build_mod, "verify_build_user", bug)
    with pytest.raises(RuntimeError):
        level1_checks(snap(fake_installed), fake_installed.lay, euid=os.geteuid())
    assert run_doctor(fake_installed, []) == cli.EXIT_BUG


# ---------- PR #631 review ----------


@pytest.mark.parametrize("bump", [1, 2], ids=["odd-bracket-open", "moved-even"])
def test_live_write_temp_is_not_a_leftover(fake_installed: World, bump: int) -> None:
    """A concurrent writer between the snapshot and the check: its temp in
    `/etc/df` and the generation counter moved (odd while its bracket is
    open, or already closed at another even value) -> «данные недоступны»,
    never the advice to remove it."""
    w = fake_installed
    s = snap(w)
    temp = f".models.toml.{os.getpid()}.{'a' * 32}.tmp"
    (w.lay.etc / temp).write_text("partial")
    w.lay.generation.write_text(f"{s.generation + bump}\n")
    checks = level1_checks(s, w.lay, euid=os.geteuid())
    c = by_id(checks)["etc.schemas"]
    assert (c.status, c.reason) == (
        "данные недоступны",
        "идёт административная запись, повторите doctor",
    )
    assert "удалите" not in render_brief(checks)
    assert not level1_reached(checks)
    w.lay.generation.write_text(f"{s.generation}\n")  # control: a leftover
    c = checks_of(w)["etc.schemas"]
    assert c.status == "нарушено" and "удалите вручную" in c.reason


def test_generation_read_is_not_a_snapshot_reread(fake_installed: World) -> None:
    """The counter is read only to tell a live write; the snapshot parts are
    still never reopened (I2)."""
    w = fake_installed
    s = snap(w)
    (w.lay.etc / f".install.toml.1.{'b' * 32}.tmp").write_text("")
    with watching(w) as reads:
        level1_checks(s, w.lay, euid=os.geteuid())
    assert reads.hits == []


def test_json_carries_every_problem(
    fake_installed: World,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`reason` keeps 5 problems and «и ещё N»; `--json` carries all."""
    many = [f"git: problem {i}" for i in range(12)]
    monkeypatch.setattr(plat, "approved_cli_problems", lambda _c, _p: many)
    assert run_doctor(fake_installed, ["--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    check = next(c for c in report["checks"] if c["id"] == "cli.approved")
    assert check["problems"] == many
    assert check["reason"] == "; ".join(many[:5]) + "; и ещё 7"
    assert run_doctor(fake_installed, []) == 2
    out = capsys.readouterr().out
    assert f"  cli.approved: нарушено: {'; '.join(many[:5])}; и ещё 7" in out
    assert "problem 5" not in out


def test_unknown_etc_names_all_in_problems(fake_installed: World) -> None:
    names = [f"extra{i:02}.toml" for i in range(7)]
    for name in names:
        (fake_installed.lay.etc / name).write_text("")
    c = checks_of(fake_installed)["etc.schemas"]
    assert c.problems == tuple(f"неизвестный файл /etc/df: {n}" for n in names)
    assert c.reason.endswith("и ещё 2")


def test_secrets_dir_is_not_an_unknown_etc_file(fake_installed: World) -> None:
    secrets = fake_installed.lay.etc / "secrets"
    secrets.mkdir(mode=0o700)
    (secrets / "token").write_text("never opened")
    os.chmod(secrets, 0o000)  # opening it would fail: it is only lstat-ed
    try:
        checks = level1_checks(
            snap(fake_installed), fake_installed.lay, euid=os.geteuid()
        )
    finally:
        os.chmod(secrets, 0o700)
    assert by_id(checks)["etc.schemas"].status == "ok"


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_non_directory_secrets_is_still_flagged(
    fake_installed: World, kind: str
) -> None:
    path = fake_installed.lay.etc / "secrets"
    if kind == "file":
        path.write_text("x")
    else:
        path.symlink_to(fake_installed.lay.var)
    c = by_id(
        level1_checks(snap(fake_installed), fake_installed.lay, euid=os.geteuid())
    )
    assert c["etc.schemas"].status == "нарушено"
    assert "secrets" in c["etc.schemas"].reason


def test_publisher_write_is_a_live_write_for_doctor(
    fake_installed: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR #641: `roles setup` writes `publisher.toml` inside a generation
    bracket, so a doctor that sees the temp of `write_atomic` at that moment
    says «идёт запись», not «осталась после сбоя»."""
    from df import fsutil
    from df.stage2_config import PublisherConfig, dump_publisher

    w = fake_installed
    s = snap(w)
    monkeypatch.setattr(
        roles_setup, "_publisher_text", lambda _l: dump_publisher(PublisherConfig({}))
    )
    real = fsutil.write_atomic
    seen: list[tuple[str, str]] = []

    def write_atomic(path: Path, text: str, mode: int = 0o644) -> None:
        temp = path.with_name(f".{path.name}.{os.getpid()}.{'b' * 32}.tmp")
        temp.write_text("partial")
        c = by_id(level1_checks(s, w.lay, euid=os.geteuid()))["etc.schemas"]
        seen.append((c.status, c.reason))
        temp.unlink()
        real(path, text, mode)

    monkeypatch.setattr(fsutil, "write_atomic", write_atomic)
    roles_setup._write_publisher(w.lay)  # noqa: SLF001
    assert seen == [
        ("данные недоступны", "идёт административная запись, повторите doctor")
    ]
    assert w.lay.generation.read_text() == f"{s.generation + 2}\n"


# ---------- level 2 (stage 2a, Task 13) ----------
#
# Every external call of level 2 is a seam answering as the role's or the
# publisher's real operation would: `check` reports (the shape of
# `launch_release.check_report`), `check-github` JSON and refusals (the
# shape `launch_release.main` prints), `self-check` and `perimeter-status`
# response lines (`request.dump_response` of a `SelfCheckResult`), parsed by
# `doctor` with the production parsers. Real `sudo` runs only in Task 14.

PRODUCT = 4711
APP_ID = 31
INSTALLATION = 77
BOT = "df-bot"
OPERATOR = "alice"
UIDS = {"df-exec": 71001, "df-review": 71002, "df-publisher": 71003}
LEVEL2_IDS = [
    "roles.users",
    "roles.sudoers",
    "roles.libexec",
    "roles.isolation",
    "publisher.answers",
    "publisher.foreign",
    "product.registered",
    "github.machine_user",
    "github.app",
    "models.access",
]
FRESH = "подтверждён 2026-10-29T00:00:00+00:00"


def answer(outcome: str, reason: str | None, result: dict[str, object]) -> bytes:
    """A publisher response line as `df-publish` prints it."""
    resp = Response(outcome, reason, result)  # type: ignore[arg-type]
    return (request.dump_response(resp) + "\n").encode()


def self_check_answer(
    *,
    problems: tuple[str, ...] = (),
    repos: tuple[int, ...] = (PRODUCT,),
    rights: dict[str, str] | None = None,
    installation: int = INSTALLATION,
    stats: PerimeterStats | None = PerimeterStats(12, 1 << 20, 1 << 30),
) -> bytes:
    perms = {"contents": "write", "pull_requests": "write", "metadata": "read"}
    inst = Installation(installation, "acme", repos, rights or perms)
    return answer("done", None, SelfCheckResult((inst,), problems, stats).to_json())


def github_report(
    *, login: str = BOT, push: bool = True, admin: bool = False, code: int = 0
) -> plat.RunResult:
    row = {
        "full_name": "acme/product",
        "permissions": {"admin": admin, "push": push, "pull": True},
        "mismatch": login != BOT,
    }
    report = {"login": login, "products": {str(PRODUCT): row}}
    return plat.RunResult(code, json.dumps(report).encode() + b"\n", b"", True)


def refused(text: str) -> plat.RunResult:
    """`check-github` refused: `launch_release.main` prints the reason."""
    return plat.RunResult(2, b"", f"Refused: {text}\n".encode(), True)


@dataclasses.dataclass
class L2:
    """The level-2 world: the installed world, roles, the product, every
    seam's next answer, and the calls the seams saw."""

    w: World
    gh_dir: Path
    reports: dict[str, dict[str, object] | plat.RunResult]
    gh_read: plat.RunResult
    status: dict[str, bytes | plat.RunResult]
    merge: plat.RunResult
    github: plat.RunResult
    self_check: bytes | Exception
    probe_current: bool = True
    calls: list[tuple[object, ...]] = dataclasses.field(default_factory=list)

    @property
    def lay(self) -> Layout:
        return self.w.lay

    def level2(self, operator: str | None = OPERATOR) -> dict[str, Check]:
        second = doctor.level2(
            snap(self.w), self.lay, PRODUCT, euid=os.geteuid(), operator=operator
        )
        return by_id(second.checks)

    def doctor(self, argv: list[str]) -> int:
        return run_doctor(self.w, ["--product", str(PRODUCT), *argv])

    def write_products(self, profile: str = "write") -> None:
        product = Product(
            PRODUCT, "acme/product", profile, APP_ID, INSTALLATION, BOT, "/srv/p"
        )  # type: ignore[arg-type]
        fsutil.write_atomic(
            self.lay.etc / "products.toml", dump_products({PRODUCT: product})
        )


def green_report(lay: Layout, role: str) -> dict[str, object]:
    """What `launch_release.check_report` prints for a well-isolated role."""
    foreign = [lay.secrets_dir, *(lay.role_home(r) for r in UIDS if r != role)]
    return {
        "nonce": "0" * 32,
        "uid": UIDS[role],
        "euid": UIDS[role],
        "gid": UIDS[role],
        "groups": [UIDS[role]],
        "env": ["HOME", "LANG", "PATH", "TZ"],
        "cwd": str(lay.role_home(role) / "work"),
        "fds": [],
        "denied": {str(p): errno.EACCES for p in foreign},
    }


def _no_sudo(monkeypatch: pytest.MonkeyPatch) -> None:
    """No call of level 2 may reach a real `sudo` (every one is a seam)."""
    real = plat.run_command

    def run_command(argv: list[str], **kwargs: object) -> plat.RunResult:
        assert argv[0] != launch.SUDO or argv[1:3] == ["-n", "-l"], argv
        return real(argv, **kwargs)  # type: ignore[arg-type]

    def run_with_input(argv: list[str], *_a: object, **_k: object) -> plat.RunResult:
        raise AssertionError(argv)

    monkeypatch.setattr(plat, "run_command", run_command)
    monkeypatch.setattr(plat, "run_with_input", run_with_input)


@pytest.fixture()
def l2(fake_installed: World, tmp_path: Path) -> L2:
    """Level 1 reached; roles ready, P registered (`write`), every model role
    set, and every level-2 operation answering green."""
    w = fake_installed
    mp = w.mp
    _no_sudo(mp)
    accounts = load_accounts(w.lay.accounts)
    for name, uid in UIDS.items():
        accounts[name] = Account(name, uid, uid, "ready", "2026-10-29T00:00:00Z")
    fsutil.write_atomic(w.lay.accounts, dump_accounts(accounts))
    for role in ROLES:
        assert (
            w.writer(["models", "set", role, "--harness", "claude", "--model", "m"])
            == 0
        )
    sudoers_file = tmp_path / "sudoers.d-df"
    sudoers_file.write_text(sudoers.render(w.lay, OPERATOR))
    sudoers_file.chmod(0o440)
    w.lay.libexec.mkdir(mode=0o755, exist_ok=True)
    w.lay.run_start.parent.mkdir(mode=0o755, exist_ok=True)
    libexec.install_wrappers(w.lay, libexec.admin_python_of(w.lay, snap(w)))
    gh_dir = tmp_path / "operator-home" / ".config" / "gh"
    gh_dir.mkdir(parents=True)
    world = L2(
        w,
        gh_dir,
        {role: green_report(w.lay, role) for role in doctor.MODEL_ROLES},
        plat.RunResult(1, b"", b"", True),
        {r: answer("refused", "run_unknown", {}) for r in doctor.MODEL_ROLES},
        plat.RunResult(1, b"", b"Sorry, user df-review is not allowed\n", True),
        github_report(),
        self_check_answer(),
    )
    world.write_products()
    mp.setattr(plat, "verify_service_user", lambda _l, _n: [])
    mp.setattr(sudoers, "SUDOERS_FILE", sudoers_file)
    mp.setattr(sudoers, "validate", lambda _t: [])
    mp.setattr(sudoers, "policy_problems", lambda *_a, **_k: [])
    mp.setattr(sudoers, "io_log_problems", lambda _l: [])
    _seams(world, mp)
    return world


def _seams(world: L2, mp: pytest.MonkeyPatch) -> None:
    def role_check(layout: Layout, role: str) -> plat.RunResult:
        world.calls.append(("check", role))
        report = world.reports[role]
        if isinstance(report, plat.RunResult):
            return report
        return plat.RunResult(0, json.dumps(report).encode() + b"\n", b"", True)

    def role_reads(role: str, path: Path) -> plat.RunResult:
        world.calls.append(("test -r", role, path))
        return world.gh_read

    def role_perimeter_status(layout: Layout, role: str, data: bytes) -> plat.RunResult:
        world.calls.append(("perimeter-status", role, data))
        out = world.status[role]
        if isinstance(out, plat.RunResult):
            return out
        return plat.RunResult(2, out, b"", True)

    def review_merge(layout: Layout) -> plat.RunResult:
        world.calls.append(("merge",))
        return world.merge

    def self_check(layout: Layout, *, purpose: str = "") -> bytes:
        world.calls.append(("self-check", purpose))
        if isinstance(world.self_check, Exception):
            raise world.self_check
        return world.self_check

    def exec_check_github(layout: Layout) -> plat.RunResult:
        world.calls.append(("check-github",))
        return world.github

    def current_status(
        layout: Layout, role: str, choice: object, install: object, **_k: object
    ) -> tuple[probe.ProbeStatus, None]:
        if world.probe_current:
            return probe.ProbeStatus(FRESH, True), None
        return probe.ProbeStatus("не подтверждён: нет пробы", False), None

    mp.setattr(doctor, "role_check", role_check)
    mp.setattr(doctor, "role_reads", role_reads)
    mp.setattr(doctor, "role_perimeter_status", role_perimeter_status)
    mp.setattr(doctor, "review_merge", review_merge)
    mp.setattr(doctor, "operator_gh", lambda _o: world.gh_dir)
    mp.setattr(setup_agent, "publisher_self_check", self_check)
    mp.setattr(setup_agent, "exec_check_github", exec_check_github)
    mp.setattr(probe, "current_status", current_status)


def status_of(world: L2, check_id: str, **kwargs: str | None) -> tuple[str, str]:
    c = world.level2(**kwargs)[check_id]  # type: ignore[arg-type]
    return c.status, c.reason


# --- the positive control and the command ---


def test_level2_reached_on_a_green_set(
    l2: L2, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control of the mechanism: every operation answers green
    (a fixture probe for every model role) -> level 2 reached, exit 0."""
    monkeypatch.setenv("SUDO_USER", OPERATOR)
    checks = l2.level2()
    assert list(checks) == LEVEL2_IDS
    assert {k: c.status for k, c in checks.items()} == dict.fromkeys(LEVEL2_IDS, "ok")
    assert checks["github.app"].reason == "источники доверия: не проверено (этап 3)"
    assert checks["product.registered"].reason == "профиль write"
    capsys.readouterr()
    assert l2.doctor([]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[:5] == [
        "уровень 1: достигнут",
        "уровень 2: достигнут",
        "уровень 3: не проверено (этап 3)",
        "уровень 4: не проверено (этап 4)",
        "профиль: write",
    ]
    assert any(line.startswith("  perimeter.journal: ok: событий 12") for line in out)


def test_level2_json_carries_everything(
    l2: L2, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUDO_USER", OPERATOR)
    l2.github = github_report(login="someone-else")
    capsys.readouterr()
    assert l2.doctor(["--json"]) == 2
    data = json.loads(capsys.readouterr().out)
    assert data["level1"] == "reached"
    assert data["level2"] == "not reached"
    assert (data["product"], data["profile"]) == (PRODUCT, "write")
    assert [c["id"] for c in data["level2_checks"]] == LEVEL2_IDS
    assert data["perimeter"]["id"] == "perimeter.journal"
    assert (data["level3"], data["level4"]) == (
        "not checked (stage 3)",
        "not checked (stage 4)",
    )
    machine = next(c for c in data["level2_checks"] if c["id"] == "github.machine_user")
    assert machine["status"] == "нарушено"
    assert any("someone-else" in p for p in machine["problems"])


def test_doctor_without_product_computes_no_level2(
    l2: L2, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    assert run_doctor(l2.w, []) == 0
    out = capsys.readouterr().out
    assert "уровень 2" not in out.replace("модели (уровень 2", "")
    assert l2.calls == []
    assert run_doctor(l2.w, ["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert "level2" not in data and "level2_checks" not in data
    assert l2.calls == []


def test_probe_models_with_product_still_refused(
    fake_installed: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--probe-models --product` keeps its refusal before any read (R64)."""
    fake_installed.mp.setattr(cli, "ROOT_UID", os.geteuid())
    raw = state.read_raw(fake_installed.lay)
    with audit_opens() as opens:
        code = run_doctor(
            fake_installed, ["--probe-models", "--product", "42"], Stable(raw, 2)
        )
    assert code == 2
    assert "реестр продуктов не поддерживается до этапа 3" in capsys.readouterr().err
    assert [p for p in opens if p.startswith(str(fake_installed.lay.root))] == []


def test_level2_not_reached_names_first_blocking(
    l2: L2, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUDO_USER", OPERATOR)
    l2.write_products("fork")
    capsys.readouterr()
    assert l2.doctor([]) == 2
    out = capsys.readouterr().out.splitlines()
    assert out[1].startswith("уровень 2: не достигнут — github.machine_user: нарушено")
    assert out[4] == "профиль: fork"


def test_level1_not_reached_blocks_level2(
    l2: L2, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUDO_USER", OPERATOR)
    monkeypatch.setattr(build_mod, "verify_build_user", lambda _l: ["uid 0"])
    capsys.readouterr()
    assert l2.doctor([]) == 2
    out = capsys.readouterr().out.splitlines()
    assert out[1] == "уровень 2: не достигнут — уровень 1 не достигнут"


def test_non_root_level2_needs_sudo_and_asks_nothing(l2: L2) -> None:
    """R13-3: without root every level-2 check is «нужен sudo»."""
    second = doctor.level2(
        snap(l2.w), l2.lay, PRODUCT, euid=os.geteuid() + 1, operator=OPERATOR
    )
    assert [(c.status, c.reason) for c in second.checks] == [
        ("данные недоступны", SUDO)
    ] * len(LEVEL2_IDS)
    assert (second.perimeter.status, second.perimeter.reason) == (
        "данные недоступны",
        SUDO,
    )
    assert l2.calls == []
    assert not doctor.level2_reached([], second.checks)


# --- 1-3 ---


def test_users_broken(l2: L2, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        plat,
        "verify_service_user",
        lambda _l, n: ["uid 0"] if n == "df-review" else [],
    )
    assert status_of(l2, "roles.users") == ("нарушено", "df-review: uid 0")


def test_sudoers_without_operator_unavailable(l2: L2) -> None:
    """R13-2: `SUDO_USER` unset -> «данные недоступны», no guess."""
    checks = l2.level2(operator=None)
    assert (checks["roles.sudoers"].status, checks["roles.sudoers"].reason) == (
        "данные недоступны",
        "оператор неизвестен (SUDO_USER не задан)",
    )
    assert checks["roles.isolation"].status == "данные недоступны"
    assert "SUDO_USER" in checks["roles.isolation"].reason


def test_sudoers_installed_text_differs_broken(l2: L2) -> None:
    path = sudoers.SUDOERS_FILE
    path.chmod(0o640)
    path.write_text(path.read_text() + "ALL ALL=(ALL) NOPASSWD: ALL\n")
    path.chmod(0o440)
    status, reason = status_of(l2, "roles.sudoers")
    assert status == "нарушено"
    assert "текст не совпадает" in reason


def test_sudoers_validate_and_policy_problems_broken(
    l2: L2, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def policy(name: str, *, layout: Layout, operator: str) -> list[str]:
        seen.append(name)
        return [f"sudo -l -U {name}: лишнее правило"] if name == "df-review" else []

    monkeypatch.setattr(sudoers, "validate", lambda _t: ["parse error"])
    monkeypatch.setattr(sudoers, "policy_problems", policy)
    monkeypatch.setattr(sudoers, "io_log_problems", lambda _l: ["log_output"])
    c = l2.level2()["roles.sudoers"]
    assert c.status == "нарушено"
    assert seen == ["df-exec", "df-review", "df-publisher", "df-build", OPERATOR]
    assert len(c.problems) == 3
    assert "parse error" in c.problems[0]


def test_libexec_broken(l2: L2) -> None:
    wrapper = l2.lay.libexec / "df-launch"
    wrapper.write_text("#!/bin/sh\nexec /bin/sh\n")
    status, reason = status_of(l2, "roles.libexec")
    assert status == "нарушено" and "текст не совпадает" in reason


# --- 4: isolation ---


@pytest.mark.parametrize(
    "role,key,value,needle",
    [
        ("df-exec", "uid", 0, "uid 0"),
        ("df-review", "gid", UIDS["df-exec"], "gid"),
        ("df-exec", "fds", [3], "дескрипторы"),
        ("df-review", "env", ["HOME", "SSH_AUTH_SOCK"], "SSH_AUTH_SOCK"),
    ],
)
def test_isolation_report_broken(
    l2: L2, role: str, key: str, value: object, needle: str
) -> None:
    report = green_report(l2.lay, role)
    report[key] = value
    l2.reports[role] = report
    status, reason = status_of(l2, "roles.isolation")
    assert status == "нарушено" and needle in reason


@pytest.mark.parametrize(
    "role,which,got",
    [
        ("df-exec", "secrets", 0),
        ("df-exec", "home", errno.ENOENT),
        ("df-review", "secrets", None),
        ("df-review", "home", errno.EPERM),
    ],
)
def test_isolation_denied_must_be_eacces(
    l2: L2, role: str, which: str, got: int | None
) -> None:
    """Each path of the role's list must be EACCES exactly (R13-1): opened,
    another errno or absent from the report is broken."""
    other = "df-review" if role == "df-exec" else "df-exec"
    path = l2.lay.secrets_dir if which == "secrets" else l2.lay.role_home(other)
    report = green_report(l2.lay, role)
    denied = dict(report["denied"])  # type: ignore[call-overload]
    if got is None:
        del denied[str(path)]
    else:
        denied[str(path)] = got
    report["denied"] = denied
    l2.reports[role] = report
    status, reason = status_of(l2, "roles.isolation")
    assert status == "нарушено" and str(path) in reason


def test_isolation_check_not_started_unavailable(l2: L2) -> None:
    l2.reports["df-review"] = plat.RunResult(-1, b"", b"", False)
    assert status_of(l2, "roles.isolation") == (
        "данные недоступны",
        "df-review check: не стартовал",
    )


def test_isolation_check_refused_broken(l2: L2) -> None:
    l2.reports["df-exec"] = plat.RunResult(
        2, b"", b"StepFailed: launch: release_revoked\n", True
    )
    status, reason = status_of(l2, "roles.isolation")
    assert status == "нарушено" and "release_revoked" in reason


def test_isolation_gh_readable_broken(l2: L2) -> None:
    l2.gh_read = plat.RunResult(0, b"", b"", True)
    assert status_of(l2, "roles.isolation") == (
        "нарушено",
        f"df-exec читает {l2.gh_dir}",
    )
    assert ("test -r", "df-exec", l2.gh_dir) in l2.calls


def test_isolation_gh_sudo_refusal_is_not_a_denial(l2: L2) -> None:
    """Negative twin: sudo's own refusal is exit 1 too, but it speaks; it
    must not pass as «df-exec cannot read»."""
    l2.gh_read = plat.RunResult(1, b"", b"sudo: unknown user df-exec\n", True)
    status, reason = status_of(l2, "roles.isolation")
    assert status == "данные недоступны" and "unknown user" in reason


def test_isolation_gh_absent_is_ok_with_note(l2: L2) -> None:
    l2.gh_dir.rmdir()
    assert status_of(l2, "roles.isolation") == ("ok", f"нет {l2.gh_dir}")
    assert not any(c[0] == "test -r" for c in l2.calls)


def test_isolation_publisher_key_unusable_broken(l2: L2) -> None:
    l2.self_check = self_check_answer(problems=(f"app_key_unusable: {APP_ID}",))
    status, reason = status_of(l2, "roles.isolation")
    assert status == "нарушено"
    assert str(l2.lay.app_key(APP_ID)) in reason


def test_isolation_other_apps_key_is_not_ps(l2: L2) -> None:
    l2.self_check = self_check_answer(problems=("app_key_unusable: 999",))
    assert status_of(l2, "roles.isolation")[0] == "ok"


def test_isolation_publisher_silent_unavailable(l2: L2) -> None:
    l2.self_check = OSError("the call failed (code -1)")
    status, reason = status_of(l2, "roles.isolation")
    assert status == "данные недоступны" and reason.startswith("df-publisher:")


# --- 5-6: the publisher ---


def test_answers_fresh_install_ok(l2: L2) -> None:
    """r2 N3: no run at all -- `refused: run_unknown` from both roles is the
    publisher answering; the probe request is a valid perimeter-status."""
    assert status_of(l2, "publisher.answers") == ("ok", "")
    sent = [c for c in l2.calls if c[0] == "perimeter-status"]
    assert [c[1] for c in sent] == ["df-exec", "df-review"]
    for call in sent:
        data = call[2]
        assert isinstance(data, bytes)
        req = request.parse("perimeter-status", data)
        assert req.run_id == "doctor-probe"
        assert req.request_id.startswith("doctor-")


@pytest.mark.parametrize(
    "out,status",
    [
        (answer("refused", "sender_unknown", {}), "нарушено"),
        (answer("done", None, {"state": "clear"}), "нарушено"),
        (b"not json\n", "нарушено"),
        (plat.RunResult(1, b"", b"sudo: a password is required\n", True), "нарушено"),
        (plat.RunResult(-1, b"", b"", False), "данные недоступны"),
        (plat.RunResult(-9, b"", b"", True, timed_out=True), "данные недоступны"),
    ],
)
def test_answers_not_ok(l2: L2, out: bytes | plat.RunResult, status: str) -> None:
    l2.status["df-review"] = out
    assert status_of(l2, "publisher.answers")[0] == status


def test_foreign_refused_by_sudo_ok(l2: L2) -> None:
    assert status_of(l2, "publisher.foreign") == ("ok", "")
    assert ("merge",) in l2.calls


@pytest.mark.parametrize(
    "result",
    [
        plat.RunResult(2, answer("refused", "bad_request", {}), b"", True),
        plat.RunResult(0, b"", b"", True),
        plat.RunResult(1, b'{"x": 1}\n', b"", True),
    ],
)
def test_foreign_let_through_broken(l2: L2, result: plat.RunResult) -> None:
    """R13-7: any stdout or another exit code is the publisher reached."""
    l2.merge = result
    assert status_of(l2, "publisher.foreign")[0] == "нарушено"


def test_foreign_without_review_reaching_publisher_not_checked(l2: L2) -> None:
    """Negative twin: a sudo that cannot run as df-review at all also exits
    1 silently; without df-review's answer the refusal proves nothing."""
    l2.status["df-review"] = plat.RunResult(1, b"", b"sudo: unknown user\n", True)
    status, _reason = status_of(l2, "publisher.foreign")
    assert status == "не проверено"
    assert ("merge",) not in l2.calls


# --- 7-10: the product ---


def test_unregistered_product(l2: L2) -> None:
    """R13-4: 8-10 are «не проверено: продукт не зарегистрирован»."""
    (l2.lay.etc / "products.toml").unlink()
    checks = l2.level2()
    assert checks["product.registered"].status == "нарушено"
    assert str(PRODUCT) in checks["product.registered"].reason
    for name in ("github.machine_user", "github.app", "models.access"):
        assert (checks[name].status, checks[name].reason) == (
            "не проверено",
            "продукт не зарегистрирован",
        )
    assert list(checks) == LEVEL2_IDS
    assert ("check-github",) not in l2.calls


@pytest.mark.parametrize(
    "profile,push,admin,ok",
    [
        ("write", True, False, True),
        ("write", False, False, False),  # a fork-profile PAT on a write product
        ("write", True, True, False),
        ("fork", False, False, True),
        ("fork", True, False, False),
    ],
)
def test_machine_user_rights_by_profile(
    l2: L2, profile: str, push: bool, admin: bool, ok: bool
) -> None:
    l2.write_products(profile)
    l2.github = github_report(push=push, admin=admin)
    status, reason = status_of(l2, "github.machine_user")
    assert (status == "ok") == ok, reason


def test_machine_user_wrong_login_broken(l2: L2) -> None:
    l2.github = github_report(login="intruder", code=1)
    status, reason = status_of(l2, "github.machine_user")
    assert status == "нарушено" and "intruder" in reason


@pytest.mark.parametrize(
    "result,status,needle",
    [
        (refused("GET https://api.github.com/user: HTTP 401"), "нарушено", "отозван"),
        (
            refused("GET https://api.github.com/user: данные недоступны: URLError()"),
            "данные недоступны",
            "GitHub",
        ),
        (
            refused("GET https://api.github.com/user: HTTP 503"),
            "данные недоступны",
            "503",
        ),
        (refused("GET https://api.github.com/user: HTTP 403"), "нарушено", "403"),
        (refused("токен /h/.config/df/github-token: не читается"), "нарушено", "токен"),
        (plat.RunResult(-1, b"", b"", False), "данные недоступны", "не стартовал"),
    ],
)
def test_machine_user_refusals(
    l2: L2, result: plat.RunResult, status: str, needle: str
) -> None:
    l2.github = result
    got, reason = status_of(l2, "github.machine_user")
    assert got == status and needle in reason


@pytest.mark.parametrize(
    "kwargs,needle",
    [
        ({"repos": (1,)}, "без репозитория"),
        ({"installation": 5}, "нет установки"),
        ({"rights": {"contents": "read", "pull_requests": "write"}}, "contents"),
        ({"rights": {"contents": "write"}}, "pull_requests"),
        ({"problems": ("app_installation_outside_products: 9",)}, "outside"),
    ],
)
def test_app_not_ok(l2: L2, kwargs: dict[str, object], needle: str) -> None:
    l2.self_check = self_check_answer(**kwargs)  # type: ignore[arg-type]
    status, reason = status_of(l2, "github.app")
    assert status == "нарушено" and needle in reason


@pytest.mark.parametrize(
    "out,status",
    [
        (answer("unknown", "github_unavailable", {}), "данные недоступны"),
        (answer("refused", "openssl_not_configured", {}), "нарушено"),
        (answer("done", None, {"installations": []}), "нарушено"),
        (OSError("the call failed"), "данные недоступны"),
    ],
)
def test_app_self_check_failures(l2: L2, out: bytes | Exception, status: str) -> None:
    l2.self_check = out
    assert status_of(l2, "github.app")[0] == status


def test_self_check_asked_once_per_run(l2: L2) -> None:
    second = doctor.level2(
        snap(l2.w), l2.lay, PRODUCT, euid=os.geteuid(), operator=OPERATOR
    )
    assert second.perimeter.status == "ok"
    assert [c for c in l2.calls if c[0] == "self-check"] == [("self-check", "doctor")]


def test_models_access_v1_not_confirmed(l2: L2) -> None:
    """Decision 9: in v1 no model role has a current probe -> level 2 not
    reached, «доступ к моделям не подтверждён»."""
    l2.probe_current = False
    status, reason = status_of(l2, "models.access")
    assert status == "не проверено"
    assert reason.startswith("доступ к моделям не подтверждён: interview:")


def test_models_access_unset_role(l2: L2) -> None:
    """A role without a choice for P is not confirmed either."""
    w = l2.w
    original = doctor.models.resolve

    def resolve(cfg: object, role: str, product: int | None) -> object:
        assert product == PRODUCT
        if role == "judge":
            raise ConfigError("judge: модель не задана")
        return original(cfg, role, product)  # type: ignore[arg-type]

    w.mp.setattr(doctor.models, "resolve", resolve)
    status, reason = status_of(l2, "models.access")
    assert status == "не проверено" and "judge: модель не задана" in reason


# --- the perimeter line ---


@pytest.mark.parametrize(
    "stats,status,needle",
    [
        (PerimeterStats(12, 3 << 20, 1 << 30), "ok", "событий 12, база 3 МиБ"),
        (PerimeterStats(9000, 1 << 20, 1 << 30), "ok", "приближается к пределу 10 000"),
        (PerimeterStats(10_000, 1 << 20, 1 << 30), "ok", "приближается"),
        (PerimeterStats(10_001, 1 << 20, 1 << 30), "нарушено", "отвечают unavailable"),
        (PerimeterStats(5, 1 << 20, 10 << 20), "ok", "свободно меньше 64 МиБ"),
        (None, "данные недоступны", "perimeter_unavailable: lost"),
    ],
)
def test_perimeter_line(
    l2: L2, stats: PerimeterStats | None, status: str, needle: str
) -> None:
    problems = ("perimeter_unavailable: lost",) if stats is None else ()
    l2.self_check = self_check_answer(stats=stats, problems=problems)
    second = doctor.level2(
        snap(l2.w), l2.lay, PRODUCT, euid=os.geteuid(), operator=OPERATOR
    )
    line = second.perimeter
    assert line.status == status and needle in line.reason, line


def test_perimeter_line_normal_has_no_warning(l2: L2) -> None:
    second = doctor.level2(
        snap(l2.w), l2.lay, PRODUCT, euid=os.geteuid(), operator=OPERATOR
    )
    assert "предупреждение" not in second.perimeter.reason


def test_perimeter_line_does_not_block_level2(l2: L2) -> None:
    """The line is operational, not a level-2 check (r4 N5)."""
    l2.self_check = self_check_answer(stats=PerimeterStats(10_001, 1, 1 << 30))
    second = doctor.level2(
        snap(l2.w), l2.lay, PRODUCT, euid=os.geteuid(), operator=OPERATOR
    )
    assert second.perimeter.status == "нарушено"
    level1 = level1_checks(snap(l2.w), l2.lay, euid=os.geteuid())
    assert doctor.level2_reached(level1, second.checks)


def test_perimeter_line_self_check_silent(l2: L2) -> None:
    l2.self_check = OSError("the call failed")
    second = doctor.level2(
        snap(l2.w), l2.lay, PRODUCT, euid=os.geteuid(), operator=OPERATOR
    )
    assert second.perimeter.status == "данные недоступны"
    assert "нет ответа" in second.perimeter.reason


# --- the seams ---


def test_role_check_seam_argv(
    fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def run_command(argv: list[str], **_kw: object) -> plat.RunResult:
        calls.append(list(argv))
        return plat.RunResult(0, b"{}", b"", True)

    monkeypatch.setattr(plat, "run_command", run_command)
    lay = fake_layout.layout
    doctor.role_check(lay, "df-review")
    doctor.role_reads("df-exec", Path("/home/alice/.config/gh"))
    assert calls == [
        ["/usr/bin/sudo", "-n", "-u", "df-review", str(lay.libexec / "df-launch")]
        + ["df-review", "check"],
        ["/usr/bin/sudo", "-n", "-u", "df-exec", "/bin/test", "-r"]
        + ["/home/alice/.config/gh"],
    ]


def test_nested_seams_go_through_the_roles_sudo(
    fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], bytes]] = []

    def run_with_input(argv: list[str], data: bytes, **_kw: object) -> plat.RunResult:
        calls.append((list(argv), data))
        return plat.RunResult(1, b"", b"", True)

    monkeypatch.setattr(plat, "run_with_input", run_with_input)
    lay = fake_layout.layout
    publish = str(lay.libexec / "df-publish")
    doctor.role_perimeter_status(lay, "df-exec", b"{}")
    doctor.review_merge(lay)
    inner = ["/usr/bin/sudo", "-n", "-u", "df-publisher", publish]
    assert calls == [
        (["/usr/bin/sudo", "-n", "-u", "df-exec", *inner, "perimeter-status"], b"{}"),
        (["/usr/bin/sudo", "-n", "-u", "df-review", *inner, "merge"], b""),
    ]


def test_self_check_seam_names_doctor(
    fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[bytes] = []

    def run_with_input(argv: list[str], data: bytes, **_kw: object) -> plat.RunResult:
        sent.append(data)
        return plat.RunResult(0, b"", b"", True)

    monkeypatch.setattr(plat, "run_with_input", run_with_input)
    setup_agent.publisher_self_check(fake_layout.layout, purpose="doctor")
    (data,) = sent
    assert request.parse("self-check", data).request_id.startswith("doctor-")
