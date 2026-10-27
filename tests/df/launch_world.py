"""A small world for the launch tests: accounts, a registry, run records."""

from __future__ import annotations

import builtins
import io
import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from df import launch
from df import platform as plat
from df.config import (
    Account,
    ReleaseEntry,
    ReleasesConfig,
    dump_accounts,
    dump_releases,
)
from df.stage2_config import ModelPin, RunRecord, dump_run
from df.state import Snapshot
from tests.df.conftest import FakeLayout

DIGEST = "sha256:" + "d" * 64
ADMIN = "sha256:" + "a" * 64


def pin(model: str = "m", config: str = "c" * 8) -> ModelPin:
    return ModelPin("claude", "1.0", model, f"{model}-x", None, {}, config)


def entry(release_id: str, status: str = "allowed", digest: str = DIGEST):  # noqa: ANN201
    reason = "bad" if status == "revoked" else None
    return ReleaseEntry(release_id, digest, ADMIN, "3.12.13", status, reason)  # type: ignore[arg-type]


def record(run_id: str, release_id: str, digest: str = DIGEST, **pins: ModelPin):  # noqa: ANN201
    every = (
        "interview",
        "author",
        "reviewer",
        "executor-review",
        "edge-check",
        "judge",
    )
    models = {**{r: pin() for r in every}, **pins}
    return RunRecord(
        run_id, release_id, digest, ADMIN, 7, models,
        ("github.com", 5, "main"), "2026-10-27T00:00:00+00:00", 501,
    )  # fmt: skip


def snapshot(current: str | None, *entries: ReleaseEntry) -> Snapshot:
    chosen = next((e for e in entries if e.id == current), None)
    return Snapshot(
        current, chosen, None, None, False, 1, ReleasesConfig(tuple(entries))
    )


@dataclass
class Events:
    log: list[tuple[object, ...]] = field(default_factory=list)
    environ_at_stdin: dict[str, str] | None = None

    def names(self) -> list[object]:
        return [e[0] for e in self.log]


def fake_os(monkeypatch: pytest.MonkeyPatch, stdin: bytes = b"") -> Events:
    """Fake every privilege and exec call of `launch`; record the order."""
    ev = Events()
    chunks = [stdin] if stdin else []

    def reading(size: int) -> bytes:
        ev.log.append(("stdin", size))
        ev.environ_at_stdin = dict(os.environ)
        return chunks.pop(0)[:size] if chunks else b""

    def once(name: str, real):  # noqa: ANN001, ANN202
        def seen() -> int:
            if not ev.log or ev.log[-1] != ("identity",):
                ev.log.append(("identity",))
            return real()

        monkeypatch.setattr(os, name, seen)

    for name in ("getuid", "geteuid", "getgid", "getegid"):
        once(name, getattr(os, name))
    monkeypatch.setattr(
        os, "closerange", lambda lo, hi: ev.log.append(("closerange", lo, hi))
    )
    monkeypatch.setattr(os, "chdir", lambda p: ev.log.append(("chdir", Path(p))))
    monkeypatch.setattr(
        os,
        "execve",
        lambda p, a, e: ev.log.append(("execve", str(p), list(a), dict(e))),
    )
    monkeypatch.setattr(
        os, "execv", lambda p, a: ev.log.append(("execv", str(p), list(a)))
    )
    monkeypatch.setattr(launch, "_stdin", reading)
    return ev


def put_accounts(
    fake: FakeLayout, *, uid: int | None = None, state: str = "ready"
) -> None:
    lay = fake.layout
    me = os.getuid() if uid is None else uid
    rows = {
        r: Account(r, me, os.getgid(), state, "t")  # type: ignore[arg-type]
        for r in ("df-exec", "df-review")
    }
    lay.install_dir.mkdir(parents=True, exist_ok=True)
    lay.accounts.write_text(dump_accounts(rows))


def put_record(fake: FakeLayout, rec: RunRecord, mode: int = 0o644) -> Path:
    path = fake.layout.runs_dir / f"{rec.run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_run(rec))
    path.chmod(mode)
    return path


def put_registry(fake: FakeLayout, *entries: ReleaseEntry) -> None:
    fake.layout.etc.mkdir(parents=True, exist_ok=True)
    (fake.layout.etc / "releases.toml").write_text(
        dump_releases(ReleasesConfig(tuple(entries)))
    )


def trust_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ancestor policy of the host is not under test here."""
    monkeypatch.setattr(plat, "trusted_path_problems", lambda _p: [])


def stdin_of(data: bytes):  # noqa: ANN201
    """A stdin that yields `data` once (in reads of at most `n`), then EOF."""
    left = [data]

    def read(size: int) -> bytes:
        if not left[0]:
            return b""
        head, left[0] = left[0][:size], left[0][size:]
        return head

    return read


#: What no command may read again after the snapshot.
FORBIDDEN = ("releases.toml", "install.toml", "models.toml", "current")


class Spy:
    """Records every open/stat/listing of a `FORBIDDEN` name (`os.*`, the
    builtin `open` and `io.open`). `publisher serve` joins in Task 7."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.hits: list[str] = []
        for name in ("open", "readlink", "stat", "lstat", "listdir", "scandir"):
            real = getattr(os, name)
            monkeypatch.setattr(os, name, self._wrap(real))
        for owner in (builtins, io):
            monkeypatch.setattr(owner, "open", self._wrap(owner.open))

    def _wrap(self, real):  # noqa: ANN001, ANN202
        def seen(path, *a, **kw):  # noqa: ANN001, ANN002, ANN003, ANN202
            if isinstance(path, (str, os.PathLike)) and Path(path).name in FORBIDDEN:
                self.hits.append(str(path))
            return real(path, *a, **kw)

        return seen
