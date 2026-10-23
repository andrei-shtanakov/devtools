"""A release's id, record, registration and activation (spec 3.5, 3.3).

`next_release_id` names a new release; `ReleaseRecord` is `release.json`;
`write_release_json` and `register` are the wrappers of `write_atomic` the
route `register` runs (`WRAPS`); `activate` is the protocol of the route
`activate`: the link `current` is switched atomically to a registered,
intact release.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from df import accept, admin, fsutil
from df.config import (
    ConfigError,
    ReleaseEntry,
    ReleasesConfig,
    dump_releases,
    parse_releases,
    read_optional,
)
from df.inputs import InstalledDist
from df.journal import StepFailed
from df.paths import Layout
from df.platform import PlatformName
from df.sources import SourceId

RELEASE_SCHEMA = "df/release/v1"
#: The record's name inside `releases/<id>` (left out of the release digest).
RELEASE_JSON = "release.json"
#: Mode of `release.json` and of `releases.toml` (M13).
RECORD_MODE = 0o644
#: `release.json` says of a registry source what it does not prove (spec 3.5).
INDEX_NOT_DECLARED = "not declared"
#: The temporary name of `activate`'s link in `/opt/df` (m4).
CURRENT_TMP = ".current.tmp"

_ID_STEP = "release-id"
_REGISTER_STEP = "register"
_ACTIVATE_STEP = "activate"
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


# ---------- the id ----------


def id_prefix(manifest_sha: str, platform: PlatformName) -> str:
    """`<sha12>-<platform>-`: every id of one configuration starts with it
    (spec 3.5)."""
    return f"{manifest_sha[:12]}-{platform}-"


def next_release_id(layout: Layout, manifest_sha: str, platform: PlatformName) -> str:
    """The smallest `<sha12>-<platform>-<n>`, `n >= 1`, used nowhere (M1).

    Used: an id in `releases.toml` (any status); a name of `releases/`,
    `staging/` or `admin/`; a mark `accept/<id>.toml`; a quarantine name
    equal to the id or `<id>.<k>`. `-1` is not used by `-10`. A registry or
    a directory that cannot be read is `StepFailed("release-id")`.
    """
    prefix = id_prefix(manifest_sha, platform)
    exact = _registered_ids(layout) | {
        name
        for parent in (layout.releases, layout.staging, layout.admin)
        for name in _names(parent)
    }
    marks = set(_names(layout.marks_dir))
    quarantined = _names(layout.quarantine)
    n = 1
    while True:
        release_id = f"{prefix}{n}"
        layout.release_dir(release_id)  # a valid id, else ConfigError
        used = (
            release_id in exact
            or f"{release_id}.toml" in marks
            or any(
                q == release_id or q.startswith(f"{release_id}.") for q in quarantined
            )
        )
        if not used:
            return release_id
        n += 1


def _registered_ids(layout: Layout) -> set[str]:
    try:
        text = read_optional(layout.etc / "releases.toml")
        entries = () if text is None else parse_releases(text).releases
    except ConfigError as exc:
        raise StepFailed(_ID_STEP, str(exc)) from exc
    return {e.id for e in entries}


def _names(directory: Path) -> list[str]:
    try:
        return os.listdir(directory)
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise StepFailed(_ID_STEP, f"{directory}: {exc}") from exc


# ---------- the record ----------


@dataclass(frozen=True)
class RepoRecord:
    """One repo of `release.json`: its pin and its trusted lock's digest."""

    name: str
    url: str
    sha: str
    lock_sha256: str
    groups: tuple[str, ...]


@dataclass(frozen=True)
class ReleaseRecord:
    """`release.json` (spec 3.5): what root accepted, and what it was told.

    `digest`, `admin_digest`, `admin_python`, `python_declared` and
    `uv_version_declared` come from the `admin` mark; `repos[].lock_sha256`
    from the trusted checkouts (M12); `distributions_declared` from the
    environments of the accepted copy (I4b). The `*_declared` values are the
    builder's statements, kept as such.
    """

    id: str
    digest: str
    admin_digest: str
    admin_python: str
    manifest_repo: str
    manifest_sha: str
    profile: str
    platform: PlatformName
    created_at: str
    repos: tuple[RepoRecord, ...]
    python_declared: Mapping[str, str]
    uv_version_declared: str
    distributions_declared: Mapping[str, tuple[InstalledDist, ...]]

    def entry(self) -> ReleaseEntry:
        """The registry entry of this release: `allowed`."""
        return ReleaseEntry(
            self.id, self.digest, self.admin_digest, self.admin_python, "allowed", None
        )


def release_json(rec: ReleaseRecord) -> str:
    """The text of `release.json`: sorted keys, two-space indent, final `\\n`."""
    data: dict[str, object] = {
        "schema": RELEASE_SCHEMA,
        "id": rec.id,
        "digest": rec.digest,
        "admin_digest": rec.admin_digest,
        "admin_python": rec.admin_python,
        "manifest_repo": rec.manifest_repo,
        "manifest_sha": rec.manifest_sha,
        "profile": rec.profile,
        "platform": rec.platform,
        "created_at": rec.created_at,
        "repos": [{**asdict(r), "groups": list(r.groups)} for r in rec.repos],
        "python_declared": dict(rec.python_declared),
        "uv_version_declared": rec.uv_version_declared,
        "distributions_declared": {
            repo: [_dist(d) for d in dists]
            for repo, dists in rec.distributions_declared.items()
        },
    }
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _dist(dist: InstalledDist) -> dict[str, object]:
    return {"name": dist.name, "version": dist.version, "source": _source(dist.source)}


def _source(source: SourceId) -> dict[str, object]:
    """The fields of `SourceId`; a registry's index is `not declared`."""
    out: dict[str, object] = {k: v for k, v in asdict(source).items() if v is not None}
    if source.kind == "registry":
        out["index"] = INDEX_NOT_DECLARED
    return out


def write_release_json(release_dir: Path, rec: ReleaseRecord) -> None:
    """Wrapper of `write_atomic`: `release_dir/release.json`, `0644`.

    `release_dir` must be the real directory of `rec.id`; a `release.json`
    the builder left there is replaced (N3). An `OSError` writing it is
    `StepFailed("register")` (m5). Effects: `write_atomic()`.
    """
    if release_dir.name != rec.id or not accept.is_real_dir(release_dir):
        raise StepFailed(_REGISTER_STEP, f"{release_dir}: не каталог релиза {rec.id}")
    _write(release_dir / RELEASE_JSON, release_json(rec))


def register(layout: Layout, rec: ReleaseRecord) -> None:
    """Wrapper of `write_atomic`: `releases.toml` plus `rec.entry()`, `0644`.

    The registry is read by the hardened reader; a broken one, or one that
    already names `rec.id` (any status), or an `OSError` writing it, is
    `StepFailed("register")`. Effects: `write_atomic()`.
    """
    path = layout.etc / "releases.toml"
    try:
        text = read_optional(path)
        entries = () if text is None else parse_releases(text).releases
    except ConfigError as exc:
        raise StepFailed(_REGISTER_STEP, str(exc)) from exc
    if any(e.id == rec.id for e in entries):
        raise StepFailed(_REGISTER_STEP, f"{rec.id} уже в releases.toml")
    _write(path, dump_releases(ReleasesConfig((*entries, rec.entry()))))


def _write(path: Path, text: str) -> None:
    """`write_atomic` of a record (`0644`); an `OSError` names the step."""
    try:
        fsutil.write_atomic(path, text, RECORD_MODE)
    except OSError as exc:
        raise StepFailed(_REGISTER_STEP, f"{path}: {exc}") from exc


# ---------- activation ----------


def activate(layout: Layout, release_id: str) -> None:
    """Protocol `activate`: `current` -> `releases/<id>`, atomically (§3.3).

    Precondition (else `StepFailed("activate")`, nothing changed): the
    registry entry of `release_id` (read by `accept.registry_entry`) is
    `allowed` and `intact` (`accept.entry_intact`: the tree root's `0755`
    with the entry's digest, `admin_ready` with the entry's admin digest
    and Python). A temporary relative link `releases/<id>` in `/opt/df`
    under the one name `.current.tmp` (a leftover of a kill or an OS crash
    is unlinked first; the writer lock makes the name exclusive, m4),
    `rename` over `current`, `fault_point("renamed")`, `sync_dir(/opt/df)`.
    Effects: `renamed` -> `sync_dir(opt_df)`.
    """
    entry = accept.registry_entry(layout, release_id)
    if entry is None:
        raise StepFailed(_ACTIVATE_STEP, f"{release_id}: нет в releases.toml")
    if entry.status != "allowed":
        raise StepFailed(_ACTIVATE_STEP, f"{release_id}: статус {entry.status}")
    if not accept.entry_intact(
        layout, release_id, entry, admin_ready=admin.admin_ready
    ):
        raise StepFailed(_ACTIVATE_STEP, f"{release_id}: дерево или admin не целы")
    _switch_current(layout, f"releases/{release_id}")


def _switch_current(layout: Layout, target: str) -> None:
    name = layout.current.name
    tmp = CURRENT_TMP
    try:
        fd = os.open(layout.opt, _DIR_FLAGS)
    except OSError as exc:
        raise StepFailed(_ACTIVATE_STEP, f"{layout.opt}: {exc}") from exc
    try:
        _unlink_absent_ok(tmp, fd)
        os.symlink(target, tmp, dir_fd=fd)
        try:
            os.rename(tmp, name, src_dir_fd=fd, dst_dir_fd=fd)
        except BaseException:
            _unlink_quietly(tmp, fd)
            raise
        fsutil.fault_point("renamed", layout.current)
        fsutil.sync_dir(fd)
    except OSError as exc:
        raise StepFailed(_ACTIVATE_STEP, f"{layout.current}: {exc}") from exc
    finally:
        os.close(fd)


def _unlink_absent_ok(name: str, dir_fd: int) -> None:
    try:
        os.unlink(name, dir_fd=dir_fd)
    except FileNotFoundError:
        pass


def _unlink_quietly(name: str, dir_fd: int) -> None:
    try:
        os.unlink(name, dir_fd=dir_fd)
    except OSError:
        pass
