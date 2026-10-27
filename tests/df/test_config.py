"""Tests for layout and strict config schemas."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest

from df.config import (
    KNOWN_SCHEMAS,
    REQUIRED_CLI,
    STAGE_OF,
    STATE_SCHEMAS,
    Account,
    ConfigError,
    InstallConfig,
    Pending,
    ReleaseEntry,
    ReleasesConfig,
    config_digest,
    dump_accounts,
    dump_install,
    dump_pending,
    dump_releases,
    load_accounts,
    load_install,
    load_pending,
    load_releases,
    parse_install,
)
from df.fsutil import write_atomic
from df.paths import Layout, default_layout

CLI = {
    "git": Path("/usr/bin/git"),
    "uv": Path("/usr/local/libexec/darkfactory/bin/uv"),
    "tar": Path("/usr/bin/tar"),
}


def make_cfg(**over: object) -> InstallConfig:
    base: dict = {
        "manifest_repo": "andrei-shtanakov/ai-orchestrators-workspace",
        "manifest_sha": "a" * 40,
        "profile": "core",
        "approved_cli": dict(CLI),
    }
    base.update(over)
    return InstallConfig(**base)


def test_install_roundtrip(tmp_path: Path) -> None:
    cfg = make_cfg()
    p = tmp_path / "install.toml"
    write_atomic(p, dump_install(cfg))
    assert load_install(p) == cfg
    assert tomllib.loads(p.read_text())["schema"] == "df/install/v1"
    assert parse_install(dump_install(cfg)) == cfg


def test_config_digest_stable_and_sensitive() -> None:
    a, b = make_cfg(), make_cfg(profile="other")
    assert config_digest(a) == config_digest(make_cfg())
    assert config_digest(a) != config_digest(b)
    assert len(config_digest(a)) == 64


def doc(**patch: object) -> str:
    data: dict = tomllib.loads(dump_install(make_cfg()))
    for k, v in patch.items():
        data[k] = v
    import df.toml_emit as te

    return te.dumps(data)


@pytest.mark.parametrize(
    ("patch", "key"),
    [
        ({"schema": "df/install/v2"}, "schema"),
        ({"extra": 1}, "extra"),
        ({"manifest_sha": "abc"}, "manifest_sha"),
        ({"manifest_sha": "A" * 40}, "manifest_sha"),
        ({"profile": ""}, "profile"),
        ({"manifest_repo": ""}, "manifest_repo"),
        ({"approved_cli": {"git": "rel/git", "uv": "/u", "tar": "/t"}}, "git"),
        ({"approved_cli": {"git": 3, "uv": "/u", "tar": "/t"}}, "git"),
    ],
)
def test_install_strict(tmp_path: Path, patch: dict, key: str) -> None:
    p = tmp_path / "install.toml"
    p.write_text(doc(**patch))
    with pytest.raises(ConfigError, match=key):
        load_install(p)


def test_install_missing_key() -> None:
    data = tomllib.loads(dump_install(make_cfg()))
    del data["profile"]
    import df.toml_emit as te

    with pytest.raises(ConfigError, match="profile"):
        parse_install(te.dumps(data))


def test_install_bad_toml(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        parse_install("this is = = not toml")


def test_install_deeply_nested_toml_is_config_error() -> None:
    with pytest.raises(ConfigError, match="invalid TOML"):
        parse_install("a = " + "[" * 200_000 + "]" * 200_000)


def test_install_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_install(tmp_path / "absent.toml")


@pytest.mark.parametrize("missing", sorted(REQUIRED_CLI))
def test_install_requires_git_uv_tar(missing: str, tmp_path: Path) -> None:
    cli = {k: v for k, v in CLI.items() if k != missing}
    with pytest.raises(ConfigError, match=missing):
        make_cfg(approved_cli=cli)
    data = tomllib.loads(dump_install(make_cfg()))
    del data["approved_cli"][missing]
    import df.toml_emit as te

    with pytest.raises(ConfigError, match=missing):
        parse_install(te.dumps(data))


def test_install_extra_cli_allowed() -> None:
    cfg = make_cfg(approved_cli={**CLI, "gh": Path("/usr/bin/gh")})
    assert parse_install(dump_install(cfg)) == cfg


def entry(**over: object) -> ReleaseEntry:
    base: dict = {
        "id": "aaaaaaaaaaaa-macos-arm64-1",
        "digest": "d" * 64,
        "admin_digest": "e" * 64,
        "admin_python": "3.13.5",
        "status": "allowed",
        "reason": None,
    }
    base.update(over)
    return ReleaseEntry(**base)


def test_releases_roundtrip(tmp_path: Path) -> None:
    cfg = ReleasesConfig((entry(), entry(id="x-2", status="revoked", reason="bad")))
    p = tmp_path / "releases.toml"
    write_atomic(p, dump_releases(cfg))
    assert load_releases(p) == cfg
    assert ReleasesConfig(()) == load_releases_text(dump_releases(ReleasesConfig(())))


def load_releases_text(text: str) -> ReleasesConfig:
    from df.config import parse_releases

    return parse_releases(text)


def test_releases_status_closed_set(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="status"):
        entry(status="maybe")
    p = tmp_path / "releases.toml"
    text = dump_releases(ReleasesConfig((entry(),))).replace("allowed", "maybe")
    p.write_text(text)
    with pytest.raises(ConfigError, match="status"):
        load_releases(p)


def test_releases_requires_admin_python(tmp_path: Path) -> None:
    text = dump_releases(ReleasesConfig((entry(),)))
    text = "\n".join(ln for ln in text.splitlines() if "admin_python" not in ln)
    with pytest.raises(ConfigError, match="admin_python"):
        load_releases_text(text)
    for bad in ("3.13", "3.13.x", "v3.13.5", "", "٣.١٢.١٣", "03.12.13"):
        with pytest.raises(ConfigError, match="admin_python"):
            entry(admin_python=bad)


def _after_schema(text: str, line: str) -> str:
    head, rest = text.split("\n", 1)
    assert head.startswith("schema")
    return f"{head}\n{line}\n{rest}"


def test_releases_strict() -> None:
    text = dump_releases(ReleasesConfig((entry(),)))
    with pytest.raises(ConfigError, match="schema"):
        load_releases_text(text.replace("df/releases/v1", "df/releases/v2"))
    with pytest.raises(ConfigError, match="bogus"):
        load_releases_text(_after_schema(text, "bogus = 1"))


def test_releases_unknown_entry_key_and_duplicates() -> None:
    text = dump_releases(ReleasesConfig((entry(),)))
    with pytest.raises(ConfigError, match="zzz"):
        load_releases_text(text.replace("status", "zzz = 1\nstatus", 1))
    dup = ReleasesConfig((entry(), entry()))
    with pytest.raises(ConfigError, match="duplicate"):
        load_releases_text(dump_releases(dup))


def test_releases_reason_rules() -> None:
    with pytest.raises(ConfigError, match="reason"):
        entry(status="allowed", reason="why")


def test_pending_and_accounts_strict(tmp_path: Path) -> None:
    p = Pending("run1", "rel-1", "x = 1\n", "f" * 64, None)
    text = dump_pending(p)
    path = tmp_path / "pending.toml"
    write_atomic(path, text)
    assert load_pending(path) == p
    p2 = Pending("run1", "rel-1", "x = 1\n", "f" * 64, "rel-0")
    write_atomic(path, dump_pending(p2))
    assert load_pending(path) == p2
    assert load_pending(tmp_path / "absent.toml") is None
    path.write_text(text + "extra = 1\n")
    with pytest.raises(ConfigError, match="extra"):
        load_pending(path)
    path.write_text(text.replace("df/pending/v1", "df/accounts/v1"))
    with pytest.raises(ConfigError, match="schema"):
        load_pending(path)

    accs = {"df-build": Account("df-build", 501, 20, "creating", "2026-10-16")}
    ap = tmp_path / "accounts.toml"
    write_atomic(ap, dump_accounts(accs))
    assert load_accounts(ap) == accs
    assert load_accounts(tmp_path / "absent.toml") == {}
    ap.write_text(_after_schema(dump_accounts(accs), "extra = 1"))
    with pytest.raises(ConfigError, match="extra"):
        load_accounts(ap)
    ap.write_text(dump_accounts(accs).replace("creating", "weird"))
    with pytest.raises(ConfigError, match="state"):
        load_accounts(ap)
    ap.write_text(dump_accounts(accs).replace("df/accounts/v1", "df/other/v1"))
    with pytest.raises(ConfigError, match="schema"):
        load_accounts(ap)


def test_pending_digest_format() -> None:
    with pytest.raises(ConfigError, match="install_digest"):
        Pending("r", "rel", "x = 1\n", "bad", None)


def test_known_schemas() -> None:
    assert KNOWN_SCHEMAS == {
        "install.toml": "df/install/v1",
        "trust.toml": "df/trust/v1",
        "releases.toml": "df/releases/v1",
        "products.toml": "df/products/v1",
        "publisher.toml": "df/publisher/v1",
        "models.toml": "df/models/v1",
    }
    assert STAGE_OF == {"trust.toml": 3, "products.toml": 3}
    assert STATE_SCHEMAS == {
        "pending.toml": "df/pending/v1",
        "accounts.toml": "df/accounts/v1",
        "accept/<id>.toml": "df/accept/v1",
        "runs/<id>.json": "df/run/v1",
    }


def test_layout_paths(tmp_path: Path) -> None:
    root = Path(tmp_path.resolve())
    lay = Layout(root)
    assert lay.opt == root / "opt/df"
    assert lay.etc == root / "etc/df"
    assert lay.var == root / "var/lib/df"
    assert lay.install_dir == lay.var / "install"
    assert lay.releases == lay.opt / "releases"
    assert lay.staging == lay.opt / "staging"
    assert lay.current == lay.opt / "current"
    assert lay.admin == lay.opt / "admin"
    assert lay.admin_python == lay.opt / "admin-python"
    assert lay.bin_dir == lay.opt / "bin"
    assert lay.launcher == lay.bin_dir / "darkfactory"
    assert lay.entry == lay.bin_dir / "darkfactory-entry.py"
    assert lay.journal == lay.install_dir / "journal.jsonl"
    assert lay.lock == lay.install_dir / "lock"
    assert lay.generation == lay.install_dir / "generation"
    assert lay.pending == lay.install_dir / "pending.toml"
    assert lay.accounts == lay.install_dir / "accounts.toml"
    assert lay.marks_dir == lay.install_dir / "accept"
    assert lay.verify_dir == lay.var / "verify"
    assert lay.doctor_dir == lay.var / "doctor"
    assert lay.probes_dir == lay.doctor_dir / "probes"
    assert lay.probe_cwd == lay.doctor_dir / "probe-cwd"
    import sys

    home = "Users" if sys.platform == "darwin" else "home"
    assert lay.build_home == root / home / "df-build"
    assert lay.release_dir("r1") == lay.releases / "r1"
    assert lay.admin_dir("r1") == lay.admin / "r1"
    assert lay.staging_dir("r1") == lay.staging / "r1"
    assert lay.staging_tree("r1") == lay.staging / "r1" / "tree"
    assert lay.quarantine == lay.staging / ".quarantine"


def test_default_layout() -> None:
    assert default_layout().opt == Path("/opt/df")


def test_layout_rejects_noncanonical(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    link = root / "l"
    link.symlink_to(root)
    with pytest.raises(ConfigError):
        Layout(link)
    with pytest.raises(ConfigError):
        Layout(Path("relative"))


@pytest.mark.parametrize("bad", ["", "..", "a/b", ".", "a\x00b", "../x"])
def test_layout_rejects_bad_ids(tmp_path: Path, bad: str) -> None:
    lay = Layout(tmp_path.resolve())
    for fn in (lay.release_dir, lay.admin_dir, lay.staging_dir, lay.staging_tree):
        with pytest.raises(ConfigError):
            fn(bad)


def test_loaders_fail_closed_on_links(tmp_path: Path) -> None:
    dangling = tmp_path / "dangling.toml"
    dangling.symlink_to(tmp_path / "nowhere")
    loop = tmp_path / "loop.toml"
    loop.symlink_to(loop)
    fifo = tmp_path / "fifo.toml"
    os.mkfifo(fifo)
    notdir = tmp_path / "file" / "x.toml"
    (tmp_path / "file").write_text("x")
    loaders = (load_pending, load_accounts, load_install, load_releases)
    for path in (dangling, loop, fifo, notdir):
        for loader in loaders:
            with pytest.raises(ConfigError, match=path.name):
                loader(path)
    for loader in (load_install, load_releases):
        with pytest.raises(ConfigError, match="absent.toml"):
            loader(tmp_path / "absent.toml")
    assert load_pending(tmp_path / "absent.toml") is None
    assert load_accounts(tmp_path / "absent.toml") == {}
