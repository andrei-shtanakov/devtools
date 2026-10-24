"""Tests for the snapshot (`df.state`, Task 9-B)."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

from df import launcher_entry, state
from df.config import (
    ReleaseEntry,
    ReleasesConfig,
    dump_install,
    dump_releases,
)
from df.journal import Stable, StepFailed, Transitional
from df.paths import Layout
from tests.df.route_world import INSTALL

ID = "rel1"
ENTRY = ReleaseEntry(
    ID, "sha256:" + "1" * 64, "sha256:" + "2" * 64, "3.13.1", "allowed", None
)


@pytest.fixture()
def lay(tmp_path: Path) -> Layout:
    root = Path(os.path.realpath(tmp_path)) / "root"
    layout = Layout(root)
    layout.install_dir.mkdir(parents=True)
    layout.etc.mkdir(parents=True)
    layout.releases.mkdir(parents=True)
    return layout


def installed(lay: Layout) -> None:
    (lay.etc / "releases.toml").write_text(dump_releases(ReleasesConfig((ENTRY,))))
    (lay.etc / "install.toml").write_text(dump_install(INSTALL))
    (lay.etc / "models.toml").write_text('schema = "df/models/v1"\n')
    os.symlink(f"releases/{ID}", lay.current)
    lay.generation.write_text("4\n")


def test_raw_fields_are_the_entrys() -> None:
    """F4: the entry hands exactly these parts (`RAW_FIELDS`)."""
    names = tuple(f.name for f in dataclasses.fields(state.RawSnapshot))
    assert names == launcher_entry.RAW_FIELDS


def test_reexports_are_journals() -> None:
    assert state.Stable is Stable and state.Transitional is Transitional


def test_read_raw_reads_every_part(lay: Layout) -> None:
    installed(lay)
    lay.pending.write_text("x\n")
    raw = state.read_raw(lay)
    assert raw == state.RawSnapshot(
        generation=4,
        current_id=ID,
        releases_text=(lay.etc / "releases.toml").read_bytes(),
        install_text=dump_install(INSTALL).encode(),
        models_text=b'schema = "df/models/v1"\n',
        pending_present=True,
    )


def test_read_raw_of_an_empty_layout(lay: Layout) -> None:
    """Bootstrap before anything: no registry, no current (M2)."""
    assert state.read_raw(lay) == state.RawSnapshot(0, None, None, None, None, False)


@pytest.mark.parametrize("bad", ["absolute", "foreign", "bare", "bad-id", "dir"])
def test_read_raw_refuses_a_bad_current(lay: Layout, bad: str) -> None:
    if bad == "dir":
        lay.current.mkdir()
    else:
        target = {
            "absolute": str(lay.releases / ID),
            "foreign": "elsewhere/x",
            "bare": ID,
            "bad-id": "releases/../x",
        }[bad]
        os.symlink(target, lay.current)
    with pytest.raises(StepFailed) as exc:
        state.read_raw(lay)
    assert exc.value.step == "snapshot"


@pytest.mark.parametrize("name", ["releases.toml", "install.toml", "models.toml"])
def test_read_raw_refuses_a_linked_part(lay: Layout, name: str) -> None:
    (lay.root / "elsewhere").write_text("x\n")
    os.symlink(lay.root / "elsewhere", lay.etc / name)
    with pytest.raises(StepFailed) as exc:
        state.read_raw(lay)
    assert exc.value.step == "snapshot"


def test_parse_snapshot_values(lay: Layout) -> None:
    installed(lay)
    snap = state.parse_snapshot(state.read_raw(lay), lay, mode="writer")
    assert snap == state.Snapshot(
        release_id=ID,
        entry=ENTRY,
        install=INSTALL,
        models_text='schema = "df/models/v1"\n',
        pending_present=False,
        generation=4,
    )


def test_parse_snapshot_current_without_entry(lay: Layout) -> None:
    raw = state.RawSnapshot(2, "other", None, None, None, False)
    snap = state.parse_snapshot(raw, lay, mode="bootstrap")
    assert (snap.release_id, snap.entry, snap.install) == ("other", None, None)


@pytest.mark.parametrize(
    "parts",
    [
        {"releases_text": b"schema = 1\n"},
        {"install_text": b'schema = "df/install/v1"\n'},
        {"install_text": b"\xff"},
        {"models_text": b"\xff"},
        {"current_id": "../x"},
    ],
    ids=["releases", "install", "install-utf8", "models-utf8", "id"],
)
def test_parse_snapshot_refuses_invalid_parts(lay: Layout, parts: dict) -> None:
    raw = dataclasses.replace(
        state.RawSnapshot(2, ID, None, None, None, False), **parts
    )
    with pytest.raises(StepFailed) as exc:
        state.parse_snapshot(raw, lay, mode="writer")
    assert exc.value.step == "snapshot"


def test_reader_snapshot_with_pending_refused(lay: Layout) -> None:
    """The entry hands a reader `Transitional` when it sees `pending.toml`."""
    raw = state.RawSnapshot(2, None, None, None, None, True)
    with pytest.raises(StepFailed):
        state.parse_snapshot(raw, lay, mode="reader")
    assert state.parse_snapshot(raw, lay, mode="writer").pending_present
