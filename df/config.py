"""Strict parsers for `/etc/df` and `/var/lib/df/install` files (spec 3.7).

Every file carries `schema = "df/<name>/v1"`; an unknown schema or key is a
refusal (`ConfigError`) naming the offending key.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from df.toml_emit import dumps


class ConfigError(Exception):
    """A configuration or state file is invalid."""


KNOWN_SCHEMAS: Mapping[str, str] = {
    "install.toml": "df/install/v1",
    "trust.toml": "df/trust/v1",
    "releases.toml": "df/releases/v1",
    "products.toml": "df/products/v1",
    "publisher.toml": "df/publisher/v1",
    "models.toml": "df/models/v1",
}
STAGE_OF: Mapping[str, int] = {
    "trust.toml": 3,
    "products.toml": 3,
}
STATE_SCHEMAS: Mapping[str, str] = {
    "pending.toml": "df/pending/v1",
    "accounts.toml": "df/accounts/v1",
    "accept/<id>.toml": "df/accept/v1",
    "runs/<id>.json": "df/run/v1",
}
REQUIRED_CLI = frozenset({"git", "uv", "tar"})

SHA1 = re.compile(r"[0-9a-f]{40}", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_NUM = r"(0|[1-9][0-9]*)"
PY_VERSION = re.compile(rf"{_NUM}\.{_NUM}\.{_NUM}", re.ASCII)
# Every way tomllib can fail on hostile input; deep nesting exhausts its recursion.
TOML_ERRORS = (tomllib.TOMLDecodeError, UnicodeDecodeError, RecursionError)


def _need(ok: bool, key: str, what: str) -> None:
    if not ok:
        raise ConfigError(f"{key}: {what}")


def _nonempty(value: object, key: str) -> str:
    _need(isinstance(value, str) and bool(value), key, "must be a non-empty string")
    assert isinstance(value, str)
    return value


def _parse_toml(text: str, schema: str) -> dict[str, object]:
    try:
        data = tomllib.loads(text)
    except TOML_ERRORS as exc:
        raise ConfigError(f"schema {schema}: invalid TOML: {exc}") from exc
    _need(data.get("schema") == schema, "schema", f"expected {schema!r}")
    return data


def _check_keys(
    data: Mapping[str, object],
    required: set[str],
    optional: set[str],
    where: str,
    allow_schema: bool = True,
) -> None:
    exempt = {"schema"} if allow_schema else set()
    for key in sorted(set(data) - required - optional - exempt):
        raise ConfigError(f"{where}: unknown key {key!r}")
    for key in sorted(required - set(data)):
        raise ConfigError(f"{where}: missing key {key!r}")


def _read(path: Path) -> str:
    """Read a required file through the hardened reader; absent is an error."""
    text = _read_optional(path)
    if text is None:
        raise ConfigError(f"{path}: cannot read: file does not exist")
    return text


def _read_optional(path: Path) -> str | None:
    """Read a regular file without following links; None only if absent."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        if os.path.islink(path):
            raise ConfigError(f"{path}: dangling symbolic link") from None
        return None
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ConfigError(f"{path}: not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as fh:
            return fh.read().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path}: cannot read: {exc}") from exc
    finally:
        os.close(fd)


def read_optional(path: Path) -> str | None:
    """Public hardened reader: a regular file without following links.

    `None` only if absent; a link, a non-regular file or an unreadable one is
    a `ConfigError`.
    """
    return _read_optional(path)


def _digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- install.toml -----------------------------------------------------------


@dataclass(frozen=True)
class InstallConfig:
    """`/etc/df/install.toml`."""

    manifest_repo: str
    manifest_sha: str
    profile: str
    approved_cli: Mapping[str, Path]

    def __post_init__(self) -> None:
        _nonempty(self.manifest_repo, "manifest_repo")
        _need(
            bool(SHA1.fullmatch(self.manifest_sha)),
            "manifest_sha",
            "must be 40 lowercase hex characters",
        )
        _nonempty(self.profile, "profile")
        missing = sorted(REQUIRED_CLI - set(self.approved_cli))
        _need(not missing, "approved_cli", f"missing required: {', '.join(missing)}")
        for name, path in self.approved_cli.items():
            _need(
                isinstance(path, Path) and path.is_absolute(),
                name,
                "approved_cli path must be absolute",
            )


def parse_install(text: str) -> InstallConfig:
    """Parse the text of `install.toml`."""
    data = _parse_toml(text, KNOWN_SCHEMAS["install.toml"])
    _check_keys(
        data,
        {"manifest_repo", "manifest_sha", "profile", "approved_cli"},
        set(),
        "install.toml",
    )
    table = data["approved_cli"]
    _need(isinstance(table, dict), "approved_cli", "must be a table")
    assert isinstance(table, dict)
    cli: dict[str, Path] = {}
    for name, value in table.items():
        _need(isinstance(value, str), name, "approved_cli path must be a string")
        cli[name] = Path(value)
    for key in ("manifest_repo", "manifest_sha", "profile"):
        _need(isinstance(data[key], str), key, "must be a string")
    return InstallConfig(
        str(data["manifest_repo"]),
        str(data["manifest_sha"]),
        str(data["profile"]),
        cli,
    )


def load_install(path: Path) -> InstallConfig:
    """Read and strictly parse `install.toml`."""
    return parse_install(_read(path))


def dump_install(cfg: InstallConfig) -> str:
    """Render `install.toml` deterministically."""
    return dumps(
        {
            "schema": KNOWN_SCHEMAS["install.toml"],
            "manifest_repo": cfg.manifest_repo,
            "manifest_sha": cfg.manifest_sha,
            "profile": cfg.profile,
            "approved_cli": {
                k: str(cfg.approved_cli[k]) for k in sorted(cfg.approved_cli)
            },
        }
    )


def config_digest(cfg: InstallConfig) -> str:
    """SHA-256 of the rendered `install.toml`."""
    return _digest_text(dump_install(cfg))


# --- releases.toml ----------------------------------------------------------


@dataclass(frozen=True)
class ReleaseEntry:
    """One release in `releases.toml`."""

    id: str
    digest: str
    admin_digest: str
    admin_python: str
    status: Literal["allowed", "revoked"]
    reason: str | None

    def __post_init__(self) -> None:
        _nonempty(self.id, "id")
        _nonempty(self.digest, "digest")
        _nonempty(self.admin_digest, "admin_digest")
        _need(
            bool(PY_VERSION.fullmatch(self.admin_python)),
            "admin_python",
            "must be an exact version X.Y.Z",
        )
        _need(
            self.status in ("allowed", "revoked"),
            "status",
            "must be 'allowed' or 'revoked'",
        )
        if self.reason is None:
            return
        _need(self.status == "revoked", "reason", "only a revoked release has one")
        _nonempty(self.reason, "reason")


@dataclass(frozen=True)
class ReleasesConfig:
    """`/etc/df/releases.toml`."""

    releases: tuple[ReleaseEntry, ...]


_ENTRY_REQUIRED = {"id", "digest", "admin_digest", "admin_python", "status"}


def parse_releases(text: str) -> ReleasesConfig:
    """Parse the text of `releases.toml`."""
    data = _parse_toml(text, KNOWN_SCHEMAS["releases.toml"])
    _check_keys(data, set(), {"release"}, "releases.toml")
    rows = data.get("release", [])
    _need(isinstance(rows, list), "release", "must be an array of tables")
    assert isinstance(rows, list)
    entries = tuple(_parse_entry(row) for row in rows)
    ids = [e.id for e in entries]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    _need(not dup, "release", f"duplicate id {dup}")
    return ReleasesConfig(entries)


def _parse_entry(row: object) -> ReleaseEntry:
    _need(isinstance(row, dict), "release", "must be a table")
    assert isinstance(row, dict)
    _check_keys(row, _ENTRY_REQUIRED, {"reason"}, "release")
    for key, value in row.items():
        _need(isinstance(value, str), key, "must be a string")
    return ReleaseEntry(
        row["id"],
        row["digest"],
        row["admin_digest"],
        row["admin_python"],
        row["status"],
        row.get("reason"),
    )


def load_releases(path: Path) -> ReleasesConfig:
    """Read and strictly parse `releases.toml`."""
    return parse_releases(_read(path))


def dump_releases(cfg: ReleasesConfig) -> str:
    """Render `releases.toml` deterministically."""
    rows = []
    for e in cfg.releases:
        row: dict[str, object] = {
            "id": e.id,
            "digest": e.digest,
            "admin_digest": e.admin_digest,
            "admin_python": e.admin_python,
            "status": e.status,
        }
        if e.reason is not None:
            row["reason"] = e.reason
        rows.append(row)
    data: dict[str, object] = {"schema": KNOWN_SCHEMAS["releases.toml"]}
    if rows:
        data["release"] = rows
    return dumps(data)


# --- pending.toml -----------------------------------------------------------


@dataclass(frozen=True)
class Pending:
    """`pending.toml`: an unfinished installation (spec 3.3)."""

    run_id: str
    release_id: str
    install_text: str
    install_digest: str
    previous_current: str | None

    def __post_init__(self) -> None:
        _nonempty(self.run_id, "run_id")
        _nonempty(self.release_id, "release_id")
        _nonempty(self.install_text, "install_text")
        _need(
            bool(_SHA256.fullmatch(self.install_digest)),
            "install_digest",
            "must be 64 lowercase hex characters",
        )
        if self.previous_current is not None:
            _nonempty(self.previous_current, "previous_current")


_PENDING_REQUIRED = {"run_id", "release_id", "install_text", "install_digest"}


def load_pending(path: Path) -> Pending | None:
    """Read `pending.toml`; `None` if the file does not exist."""
    text = _read_optional(path)
    if text is None:
        return None
    data = _parse_toml(text, STATE_SCHEMAS["pending.toml"])
    _check_keys(data, _PENDING_REQUIRED, {"previous_current"}, "pending.toml")
    for key, value in data.items():
        _need(isinstance(value, str), key, "must be a string")
    return Pending(
        str(data["run_id"]),
        str(data["release_id"]),
        str(data["install_text"]),
        str(data["install_digest"]),
        _opt_str(data.get("previous_current")),
    )


def _opt_str(value: object) -> str | None:
    return None if value is None else str(value)


def dump_pending(p: Pending) -> str:
    """Render `pending.toml`."""
    data: dict[str, object] = {
        "schema": STATE_SCHEMAS["pending.toml"],
        "run_id": p.run_id,
        "release_id": p.release_id,
        "install_text": p.install_text,
        "install_digest": p.install_digest,
    }
    if p.previous_current is not None:
        data["previous_current"] = p.previous_current
    return dumps(data)


# --- accounts.toml ----------------------------------------------------------


@dataclass(frozen=True)
class Account:
    """A service account record."""

    name: str
    uid: int
    gid: int
    state: Literal["creating", "ready"]
    created_at: str

    def __post_init__(self) -> None:
        _nonempty(self.name, "name")
        _need(self.uid >= 0, "uid", "must be non-negative")
        _need(self.gid >= 0, "gid", "must be non-negative")
        _need(
            self.state in ("creating", "ready"),
            "state",
            "must be 'creating' or 'ready'",
        )
        _nonempty(self.created_at, "created_at")


_ACCOUNT_KEYS = {"name", "uid", "gid", "state", "created_at"}


def load_accounts(path: Path) -> dict[str, Account]:
    """Read `accounts.toml`; empty if the file does not exist."""
    text = _read_optional(path)
    if text is None:
        return {}
    data = _parse_toml(text, STATE_SCHEMAS["accounts.toml"])
    _check_keys(data, set(), {"account"}, "accounts.toml")
    rows = data.get("account", [])
    _need(isinstance(rows, list), "account", "must be an array of tables")
    assert isinstance(rows, list)
    result: dict[str, Account] = {}
    for row in rows:
        acc = _parse_account(row)
        _need(acc.name not in result, "account", f"duplicate name {acc.name!r}")
        result[acc.name] = acc
    return result


def _parse_account(row: object) -> Account:
    _need(isinstance(row, dict), "account", "must be a table")
    assert isinstance(row, dict)
    _check_keys(row, _ACCOUNT_KEYS, set(), "account")
    for key in ("uid", "gid"):
        _need(
            isinstance(row[key], int) and not isinstance(row[key], bool),
            key,
            "must be an integer",
        )
    for key in ("name", "state", "created_at"):
        _need(isinstance(row[key], str), key, "must be a string")
    return Account(row["name"], row["uid"], row["gid"], row["state"], row["created_at"])


def dump_accounts(accounts: Mapping[str, Account]) -> str:
    """Render `accounts.toml` sorted by name."""
    rows = [
        {
            "name": a.name,
            "uid": a.uid,
            "gid": a.gid,
            "state": a.state,
            "created_at": a.created_at,
        }
        for _, a in sorted(accounts.items())
    ]
    data: dict[str, object] = {"schema": STATE_SCHEMAS["accounts.toml"]}
    if rows:
        data["account"] = rows
    return dumps(data)
