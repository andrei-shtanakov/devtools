"""Test producers for `df.receive.receive_tree` (same user, own session)."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from df.digest import TreeError
from df.platform import TAR_FLAGS, detect_platform

TAR = "/usr/bin/tar" if sys.platform == "darwin" else "tar"
_GONE_SECONDS = 5.0


class LocalProducer:
    """`Producer` over a process of the current user in its own session.

    `end` kills the whole process group, reaps the direct child and checks
    that nothing of the group is left.
    """

    def __init__(self, argv: Sequence[str | os.PathLike[str]]) -> None:
        self._proc = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            start_new_session=True,
        )
        assert self._proc.stdout is not None
        self._stdout = self._proc.stdout
        self.pgid = self._proc.pid
        self.ended = False

    @property
    def fd(self) -> int:
        """Producer stdout."""
        return self._stdout.fileno()

    def wait(self, timeout: float) -> int:
        """Exit code of the direct child; timeout is a `TreeError`."""
        try:
            return self._proc.wait(timeout)
        except subprocess.TimeoutExpired as exc:
            raise TreeError("producer did not exit in time") from exc

    def end(self) -> None:
        """Kill the session group, reap the child, check the group is gone."""
        self.ended = True
        _kill_group(self.pgid)
        try:
            self._proc.wait(10)
        except subprocess.TimeoutExpired as exc:
            raise TreeError("producer not reaped") from exc
        self._stdout.close()
        if not group_gone(self.pgid):
            raise TreeError("producer left processes behind")


class KillOneProducer(LocalProducer):
    """Negative twin: `end` kills only the direct child (the old rule)."""

    def end(self) -> None:
        """Kill the direct child only."""
        self.ended = True
        self._proc.kill()
        self._proc.wait(10)
        self._stdout.close()


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        pass  # macOS: EPERM when every member is already a zombie


def group_gone(pgid: int) -> bool:
    """Wait briefly until no process of the group is left."""
    deadline = time.monotonic() + _GONE_SECONDS
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass  # macOS: zombies not yet reaped by init answer EPERM
        time.sleep(0.02)
    return False


def pid_gone(pid: int) -> bool:
    """Wait briefly until `pid` is gone (zombies are reaped by init)."""
    deadline = time.monotonic() + _GONE_SECONDS
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def data_producer(tmp_path: Path, data: bytes) -> LocalProducer:
    """Producer that writes `data` and exits."""
    src = tmp_path / f"stream-{time.monotonic_ns()}.bin"
    src.write_bytes(data)
    return LocalProducer(["/bin/cat", src])


def tar_producer(src: Path, *, trailer: int = 0) -> LocalProducer:
    """Producer running the system tar over `src` (plus `trailer` NUL bytes)."""
    argv = [TAR, "-cf", "-", *TAR_FLAGS[detect_platform()], "-C", str(src), "."]
    if not trailer:
        return LocalProducer(argv)
    script = f'"$@" && head -c {trailer} /dev/zero'
    return LocalProducer(["/bin/sh", "-c", script, "sh", *argv])
