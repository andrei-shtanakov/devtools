"""`darkfactory cli set openssl PATH` (stage 2a, Task 4a; owner 27.10).

The one supported way to add the optional approved `openssl` (spec 3.7) to
the `install.toml` of an existing install: a root writer under the writer
lock, one durable write inside a generation bracket. `approved_cli` is part
of `config_digest`; the regression tests pin that the digest is compared
only within one install run (`pending.toml` and that run's marks), never
against a finished install, so `doctor`, the next `install`, `models set`
and `roles setup` keep working after the change.
"""

from __future__ import annotations

import errno
import os
import pwd
import stat
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from df import cli, doctor, fsutil, journal, launcher_entry, roles_setup, state
from df import platform as plat
from df.config import (
    InstallConfig,
    config_digest,
    dump_install,
    load_install,
    load_pending,
)
from df.doctor import level1_checks, level1_reached
from df.journal import Stable
from df.paths import Layout
from tests.df.conftest import FakeLayout
from tests.df.install_world import (
    World,
    committed_problems,
    current_id,
    files,
    installed,
    interrupt,
    make_world,
)
from tests.df.route_world import BOOT

OPENSSL = Path("/usr/bin/openssl")
SET = ["cli", "set", "openssl", str(OPENSSL)]
SET_AUTHOR = ["models", "set", "author", "--harness", "claude", "--model", "m-a"]
RAW = state.RawSnapshot(0, None, None, None, None, False)
OPERATOR = "alice"


@pytest.fixture(autouse=True)
def _boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fsutil, "boot_id", lambda: BOOT)


@pytest.fixture()
def w(monkeypatch: pytest.MonkeyPatch, fake_layout: FakeLayout) -> World:
    """A committed first install without `openssl`; the policy is recorded."""
    world = make_world(fake_layout, monkeypatch)
    installed(world)
    monkeypatch.setattr(doctor, "ROOT_UID", os.geteuid())
    return world


def install_toml(w: World) -> Path:
    return w.lay.etc / "install.toml"


def with_openssl(cfg: InstallConfig, path: Path = OPENSSL) -> InstallConfig:
    return replace(cfg, approved_cli={**cfg.approved_cli, "openssl": path})


def record_policy(
    w: World, refuse: Mapping[str, list[str]] | None = None
) -> list[dict[str, Path]]:
    """The approved-CLI policy records each map it is asked about and
    refuses the names in `refuse`."""
    seen: list[dict[str, Path]] = []

    def policy(cli_map: Mapping[str, Path], _platform: object) -> list[str]:
        seen.append(dict(cli_map))
        bad = refuse or {}
        return [f"{n}: {p}" for n in sorted(cli_map) for p in bad.get(n, [])]

    w.mp.setattr(plat, "approved_cli_problems", policy)
    return seen


def snap(w: World) -> state.Snapshot:
    return state.parse_snapshot(state.read_raw(w.lay), w.lay, mode="reader")


# ---------- the command's shape ----------


def test_cli_set_is_a_writer() -> None:
    assert ("cli", "set") in launcher_entry.WRITER_COMMANDS
    assert launcher_entry.mode_of(SET) == "writer"
    assert cli.parsed_mode(cli._parse(SET)) == "writer"  # noqa: SLF001
    assert launcher_entry.mode_of(["cli"]) == "reader"


@pytest.mark.parametrize(
    "argv",
    [
        ["cli"],
        ["cli", "set"],
        ["cli", "set", "openssl"],
        ["cli", "set", "openssl", "/usr/bin/openssl", "extra"],
        ["cli", "get", "openssl"],
    ],
)
def test_wrong_shape_is_a_usage_error(argv: list[str]) -> None:
    with pytest.raises(cli.UsageError):
        cli._parse(argv)  # noqa: SLF001


def test_without_root_refused_by_main(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "ROOT_UID", os.geteuid() + 1)
    lay = Layout(Path(os.path.realpath(tmp_path)))
    code = cli.main(SET, mode="writer", read=Stable(RAW, 0), lock_fd=99, layout=lay)
    assert code == 2
    assert "требует root" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_entry_refuses_without_root_before_any_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(launcher_entry, "ROOT_UID", os.geteuid() + 1)
    assert launcher_entry.run(SET, root=str(tmp_path)) == 2
    assert "требует root" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_without_root_on_an_install_changes_nothing(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    w.mp.setattr(cli, "ROOT_UID", os.geteuid() + 1)
    before = files(w)
    assert w.writer(SET) == 2
    assert "требует root" in capsys.readouterr().err
    assert files(w) == before


# ---------- the write ----------


def test_set_adds_openssl_and_keeps_every_other_field(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    old = load_install(install_toml(w))
    gen = journal.read_generation(w.lay)
    assert "openssl" not in old.approved_cli
    assert w.writer(SET) == 0
    new = load_install(install_toml(w))
    assert new == with_openssl(old)
    assert install_toml(w).read_text() == dump_install(with_openssl(old))
    assert stat.S_IMODE(install_toml(w).stat().st_mode) == 0o644
    assert journal.read_generation(w.lay) == gen + 2
    assert "openssl" in capsys.readouterr().out


def test_set_replaces_an_existing_openssl(w: World) -> None:
    assert w.writer(SET) == 0
    other = Path("/usr/local/bin/openssl")
    assert w.writer(["cli", "set", "openssl", str(other)]) == 0
    assert load_install(install_toml(w)).approved_cli["openssl"] == other


def test_same_value_again_is_a_no_op(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """Idempotent: the same value again writes nothing and opens no
    generation (`files` and the counter are unchanged), exit 0."""
    assert w.writer(SET) == 0
    capsys.readouterr()
    before = files(w)
    gen = journal.read_generation(w.lay)
    w.mp.setattr(fsutil, "write_atomic", _no_write)
    assert w.writer(SET) == 0
    assert "не изменён" in capsys.readouterr().out
    assert files(w) == before
    assert journal.read_generation(w.lay) == gen


def _no_write(path: Path, text: str, mode: int = 0o644) -> None:
    raise AssertionError(f"write_atomic({path})")


def test_write_is_inside_the_generation_bracket(w: World) -> None:
    seen: list[int] = []
    real = fsutil.write_atomic

    def write(path: Path, text: str, mode: int = 0o644) -> None:
        seen.append(journal.read_generation(w.lay))
        real(path, text, mode)

    gen = journal.read_generation(w.lay)
    w.mp.setattr(fsutil, "write_atomic", write)
    assert w.writer(SET) == 0
    assert seen == [gen + 1]
    assert journal.read_generation(w.lay) == gen + 2


def test_refused_write_closes_its_generation(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    before = install_toml(w).read_bytes()
    gen = journal.read_generation(w.lay)

    def refuse(path: Path, text: str, mode: int = 0o644) -> None:
        raise OSError(errno.ENOSPC, "no space", str(path))

    w.mp.setattr(fsutil, "write_atomic", refuse)
    assert w.writer(SET) == 2
    assert "no space" in capsys.readouterr().err
    assert journal.read_generation(w.lay) == gen + 2
    assert install_toml(w).read_bytes() == before


def test_closes_an_odd_generation_first(w: World) -> None:
    """M9, as `models set`: a crashed bracket without `pending.toml` is
    closed with a `warn` before this command opens its own."""
    journal.generation_begin(w.lay)
    gen = journal.read_generation(w.lay)
    assert w.writer(SET) == 0
    assert journal.read_generation(w.lay) == gen + 3
    rows = journal.read_journal(w.lay.journal)
    assert any(r["step"] == "generation" and r["event"] == "warn" for r in rows)


def test_write_is_durable(fake_world: World) -> None:
    """Under the OS-crash model nothing of the write is left unsynced."""
    installed(fake_world)
    assert fake_world.writer(SET) == 0
    assert (
        committed_problems(
            fake_world, str(current_id(fake_world)), with_openssl(fake_world.cfg())
        )
        == []
    )


# ---------- refusals change nothing ----------


@pytest.mark.parametrize("name", ["git", "uv", "tar", "curl", "OPENSSL", ""])
def test_other_names_refused(
    w: World, name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    before = files(w)
    assert w.writer(["cli", "set", name, str(OPENSSL)]) == 2
    err = capsys.readouterr().err
    assert "только openssl" in err
    assert files(w) == before


@pytest.mark.parametrize("path", ["openssl", "bin/openssl", "./openssl", ""])
def test_relative_path_refused(
    w: World, path: str, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = record_policy(w)
    before = files(w)
    assert w.writer(["cli", "set", "openssl", path]) == 2
    assert "абсолютн" in capsys.readouterr().err
    assert files(w) == before
    assert seen == []


def test_untrusted_path_refused(w: World, capsys: pytest.CaptureFixture[str]) -> None:
    """The same policy as every approved CLI (spec 3.10), asked about
    exactly `{"openssl": PATH}`; a problem refuses before any change."""
    seen = record_policy(w, {"openssl": ["владелец не root"]})
    before = files(w)
    assert w.writer(SET) == 2
    assert "openssl: владелец не root" in capsys.readouterr().err
    assert files(w) == before
    assert seen == [{"openssl": OPENSSL}]


def test_trusted_path_asks_the_policy(w: World) -> None:
    seen = record_policy(w)
    assert w.writer(SET) == 0
    assert seen == [{"openssl": OPENSSL}]


def test_same_value_no_longer_trusted_is_refused(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """The policy runs before the no-op: a path approved earlier that stopped
    passing §3.10 is refused with the reason, not reported unchanged."""
    assert w.writer(SET) == 0
    capsys.readouterr()
    record_policy(w, {"openssl": ["владелец не root"]})
    before = files(w)
    assert w.writer(SET) == 2
    err = capsys.readouterr().err
    assert "openssl: владелец не root" in err
    assert "не изменён" not in err
    assert files(w) == before


def test_damaged_current_refused_before_any_change(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """G1 (I9, spec 3.8): `current` damaged and `install.toml` not agreeing
    with it -- `cli set` is refused at `damaged` before its first change."""
    rid = current_id(w)
    assert rid is not None
    other = replace(load_install(install_toml(w)), manifest_sha="1" * 40)
    install_toml(w).write_text(dump_install(other))
    (w.lay.release_dir(rid) / "extra-file").write_text("x")
    before = files(w)
    capsys.readouterr()
    assert w.writer(SET) == 2
    assert capsys.readouterr().err.startswith("darkfactory: damaged: ")
    assert files(w) == before
    assert "openssl" not in load_install(install_toml(w)).approved_cli


def test_pending_install_refused(w: World, capsys: pytest.CaptureFixture[str]) -> None:
    """An unfinished install is not touched: refused like `models set`."""
    interrupt(w, w.writer, ["install"], "build")
    capsys.readouterr()
    before = files(w)
    assert w.writer(SET) == 2
    assert "незавершённая установка" in capsys.readouterr().err
    assert files(w) == before


def test_without_install_toml_refused(
    fake_layout: FakeLayout,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Refused; nothing but the test's own writer lock is created (the
    shipped entry refuses earlier, without `current`)."""
    world = make_world(fake_layout, monkeypatch)
    assert world.writer(SET) == 2
    assert "bootstrap init" in capsys.readouterr().err
    assert set(files(world)) == {"var/lib/df/install/lock"}


# ---------- regression: the config digest after `cli set` ----------


def test_digest_changes_but_doctor_level1_stays_reached(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """(1) `approved_cli` is in `config_digest`, so the digest changes; level
    1 compares no config digest (only `manifest_sha` and `profile` of
    `install.toml`) and checks `openssl` with the other approved CLIs."""
    old = load_install(install_toml(w))
    assert w.writer(SET) == 0
    assert config_digest(load_install(install_toml(w))) != config_digest(old)
    seen = record_policy(w)
    checks = level1_checks(snap(w), w.lay, euid=os.geteuid())
    assert level1_reached(checks), [c for c in checks if c.status != "ok"]
    assert with_openssl(old).approved_cli in seen
    raw = state.read_raw(w.lay)
    capsys.readouterr()
    code = cli.main(
        ["doctor"],
        mode="reader",
        read=Stable(raw, raw.generation),
        lock_fd=None,
        layout=w.lay,
    )
    assert code == 0
    assert capsys.readouterr().out.splitlines()[0] == "уровень 1: достигнут"


def test_next_install_builds_a_new_release_with_openssl(w: World) -> None:
    """(2) The next `install` (the shipped entry) reads the new
    `install.toml`: a new release id under the new digest; the old
    release's mark, under the old digest, is never compared."""
    first = current_id(w)
    assert w.writer(SET) == 0
    assert w.entry(["install"]) == 0
    second = current_id(w)
    assert second is not None and second != first
    assert committed_problems(w, second, with_openssl(w.cfg())) == []


def test_interrupted_install_after_set_resumes_the_same_release(w: World) -> None:
    """(2) The same release: an install begun after `cli set` and
    interrupted resumes its own `pending.toml` (the new digest) to the end."""
    assert w.writer(SET) == 0
    interrupt(w, w.entry, ["install"], "copy")
    pending = load_pending(w.lay.pending)
    assert pending is not None
    assert pending.install_digest == config_digest(with_openssl(w.cfg()))
    assert w.entry(["install"]) == 0
    assert current_id(w) == pending.release_id
    assert committed_problems(w, pending.release_id, with_openssl(w.cfg())) == []


def test_models_set_after_cli_set(w: World) -> None:
    """(3) `models set` is unaffected and keeps `openssl`."""
    assert w.writer(SET) == 0
    assert w.writer(SET_AUTHOR) == 0
    assert (w.lay.etc / "models.toml").exists()
    assert load_install(install_toml(w)).approved_cli["openssl"] == OPENSSL


def test_roles_setup_after_cli_set_passes_its_preconditions(w: World) -> None:
    """(3) Without `openssl` `roles setup` refuses at `openssl`; after
    `cli set` it passes every precondition and reaches its steps."""
    real = pwd.getpwnam
    me = pwd.getpwuid(os.getuid()).pw_name
    w.mp.setattr(pwd, "getpwnam", lambda n: real(me if n == OPERATOR else n))
    reached: list[str] = []
    w.mp.setattr(roles_setup, "_steps", lambda _l, op, _p: reached.append(op) or [])
    argv = ["roles", "setup", "--operator", OPERATOR]
    assert w.writer(argv) == 2
    assert reached == []
    assert w.writer(SET) == 0
    assert w.writer(argv) == 0
    assert reached == [OPERATOR]


def test_no_openssl_message_names_the_command() -> None:
    assert (
        "sudo darkfactory cli set openssl /usr/bin/openssl" in roles_setup._NO_OPENSSL  # noqa: SLF001
    )
