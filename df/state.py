"""The snapshot of shared state a command works from (spec 3.3, 3.9).

The entry of `darkfactory` (Task 8a) reads the raw parts once and hands them
to `df.cli.main` as `Stable(RawSnapshot, g)` or `Transitional(g)`; bootstrap
reads them itself under the writer lock (`read_raw`). `parse_snapshot` turns
the parts into values; no command reads `current`, `install.toml`,
`models.toml` or `releases.toml` again. The writer's own `pending.toml` is
read once under its lock (`df.install.read_pending`, I2), not here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from df.config import (
    ConfigError,
    InstallConfig,
    ReleaseEntry,
    ReleasesConfig,
    parse_install,
    parse_releases,
    read_optional,
)
from df.journal import Stable, StepFailed, Transitional, read_generation
from df.paths import Layout

__all__ = [
    "Mode",
    "RawSnapshot",
    "ReadResult",
    "Snapshot",
    "Stable",
    "Transitional",
    "parse_snapshot",
    "read_raw",
]

Mode = Literal["writer", "reader", "bootstrap"]
_STEP = "snapshot"
_CURRENT_PREFIX = "releases/"


@dataclass(frozen=True)
class RawSnapshot:
    """The raw parts, as the entry reads them (fields = `RAW_FIELDS`).

    `releases_text` is `None` before the first registration (M2).
    """

    generation: int
    current_id: str | None
    releases_text: bytes | None
    install_text: bytes | None
    models_text: bytes | None
    pending_present: bool


ReadResult = Stable[RawSnapshot] | Transitional


@dataclass(frozen=True)
class Snapshot:
    """The parsed snapshot: the active release, its registry entry, the whole
    registry (`releases`, from the same text), the configuration, the models
    text and whether `pending.toml` was seen."""

    release_id: str | None
    entry: ReleaseEntry | None
    install: InstallConfig | None
    models_text: str | None
    pending_present: bool
    generation: int
    releases: ReleasesConfig | None = None


def read_raw(layout: Layout) -> RawSnapshot:
    """One read of every part (bootstrap and tests; the caller holds the
    writer lock): the generation, `current` once, the three files through
    the hardened reader, and whether `pending.toml` exists."""
    try:
        return RawSnapshot(
            generation=read_generation(layout),
            current_id=_current_id(layout),
            releases_text=_read_bytes(layout.etc / "releases.toml"),
            install_text=_read_bytes(layout.etc / "install.toml"),
            models_text=_read_bytes(layout.etc / "models.toml"),
            # `lexists` is False on EACCES too; still fail-closed: the
            # writer's `read_pending` then raises, or disagrees (`_writer`)
            pending_present=os.path.lexists(layout.pending),
        )
    except ConfigError as exc:
        raise StepFailed(_STEP, str(exc)) from exc


def parse_snapshot(raw: RawSnapshot, layout: Layout, *, mode: Mode) -> Snapshot:
    """Values of the raw parts; any invalid part is `StepFailed("snapshot")`.

    A reader's stable snapshot never has `pending.toml` (the entry hands a
    reader `Transitional` then, §3.3): one that does is refused here too.
    """
    if mode == "reader" and raw.pending_present:
        raise StepFailed(_STEP, "pending.toml у читателя: переходное состояние")
    try:
        release_id = raw.current_id
        if release_id is not None:
            layout.release_dir(release_id)  # a valid id, else ConfigError
        releases = _releases_of(raw.releases_text)
        return Snapshot(
            release_id=release_id,
            entry=_entry_of(releases, release_id),
            install=_install_of(raw.install_text),
            models_text=_decode(raw.models_text, "models.toml"),
            pending_present=raw.pending_present,
            generation=raw.generation,
            releases=releases,
        )
    except ConfigError as exc:
        raise StepFailed(_STEP, str(exc)) from exc


def _releases_of(text: bytes | None) -> ReleasesConfig | None:
    decoded = _decode(text, "releases.toml")
    return None if decoded is None else parse_releases(decoded)


def _entry_of(
    releases: ReleasesConfig | None, release_id: str | None
) -> ReleaseEntry | None:
    if releases is None or release_id is None:
        return None
    return next((e for e in releases.releases if e.id == release_id), None)


def _install_of(text: bytes | None) -> InstallConfig | None:
    decoded = _decode(text, "install.toml")
    return None if decoded is None else parse_install(decoded)


def _decode(text: bytes | None, name: str) -> str | None:
    if text is None:
        return None
    try:
        return text.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{name}: не UTF-8: {exc}") from exc


def _read_bytes(path: Path) -> bytes | None:
    """The hardened reader (`df.config.read_optional`); the entry hands bytes,
    so bytes here too (a file that is not UTF-8 is refused either way)."""
    text = read_optional(path)
    return None if text is None else text.encode("utf-8")


def _current_id(layout: Layout) -> str | None:
    """The id of `current` (relative link `releases/<id>`); absent -> None."""
    try:
        text = os.readlink(layout.current)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StepFailed(_STEP, f"{layout.current}: не ссылка: {exc}") from exc
    release_id = text.removeprefix(_CURRENT_PREFIX)
    if not text.startswith(_CURRENT_PREFIX):
        raise StepFailed(_STEP, f"{layout.current}: чужая цель {text!r}")
    layout.release_dir(release_id)  # a valid id, else ConfigError
    return release_id
