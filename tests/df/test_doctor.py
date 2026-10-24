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
from df import acl, admin, cli, digest, doctor, fsutil, journal, probe, state
from df import build as build_mod
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
from tests.df.conftest import FakeLayout
from tests.df.install_world import (
    OTHER_SHA,
    World,
    installed,
    make_world,
)
from tests.df.route_world import BOOT
from tests.df.test_install_steps import watching
from tests.df.test_launcher_entry import ID, record, run_entry, world, write_registry

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
        f"уровень 1: не достигнут — install.transitional: {cli._TRANSITIONAL}\n"  # noqa: SLF001
    )


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
    (etc / "publisher.toml").write_text("garbage = [")
    s = snap(fake_installed)
    with audit_opens() as opens:
        checks = level1_checks(s, fake_installed.lay, euid=os.geteuid())
    c = by_id(checks)
    assert (c["etc.trust.toml"].status, c["etc.trust.toml"].reason) == (
        "не проверено",
        "этап 3",
    )
    assert (c["etc.publisher.toml"].status, c["etc.publisher.toml"].reason) == (
        "не проверено",
        "этап 2",
    )
    assert c["etc.schemas"].status == "ok"
    assert level1_reached(checks)
    assert render_brief(checks) == "уровень 1: достигнут"
    assert not {str(etc / "trust.toml"), str(etc / "publisher.toml")} & set(opens)


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
    assert all(set(c) == {"id", "status", "reason", "time"} for c in report["checks"])
    assert [m["role"] for m in report["models"]] == list(ROLES)


def test_render_json_shape() -> None:
    t = "2026-10-24T00:00:00+00:00"
    checks = [Check("install.pending", "ok", "", t)]
    data = json.loads(render_json(checks, []))
    assert data == {
        "level1": "reached",
        "checks": [{"id": "install.pending", "status": "ok", "reason": "", "time": t}],
        "warnings": [],
        "models": [],
    }


# ---------- the command line ----------


def test_doctor_json_with_probe_models_refused(
    fake_installed: World, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_doctor(fake_installed, ["--probe-models", "--json"]) == 2
    assert "--json" in capsys.readouterr().err


def test_doctor_product_refused_without_reads(
    fake_installed: World, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = state.read_raw(fake_installed.lay)
    with audit_opens() as opens:
        code = run_doctor(fake_installed, ["--product", "42"], Stable(raw, 2))
    assert code == 2
    assert "реестр продуктов не поддерживается до этапа 3" in capsys.readouterr().err
    assert [p for p in opens if p.startswith(str(fake_installed.lay.root))] == []


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


def test_doctor_allowed_with_pending(fake_installed: World) -> None:
    assert "doctor" in cli.PENDING_ALLOWED
    assert not hasattr(cli, "LATER")


# ---------- the public entry ----------


def test_doctor_transitional_via_public_entry(tmp_path: Path) -> None:
    """F4 (Task 8a): the real `darkfactory-entry.py` (`-I -B -S`) at an odd
    generation runs the real `df` as admin code: exit 2 (level 1 not
    reached) and the transitional JSON."""
    lay = world(tmp_path, generation=b"5\n")
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
        "generation": 5,
    }


def test_journal_untouched_by_doctor(fake_installed: World) -> None:
    before = fake_installed.lay.journal.read_bytes()
    gen = journal.read_generation(fake_installed.lay)
    assert run_doctor(fake_installed, []) == 0
    assert fake_installed.lay.journal.read_bytes() == before
    assert journal.read_generation(fake_installed.lay) == gen
