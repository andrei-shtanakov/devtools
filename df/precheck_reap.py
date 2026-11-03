"""Leftover processes and snapshots of `df-precheck` (stage 2b, Task 10).

The precheck has three timeout rungs (plan decision 5): its own `DEADLINE`
(150 s), sudo's `command_timeout` (180 s) and the publisher's wait
(`PRECHECK_WAIT`, 200 s). Rung 2 kills a running or blocked command, but
not a stopped one -- sudo suspends itself when its command stops (CI run
41673160363, both OSes) -- nor the git children, which run in their own
session. The publisher's guarantee is rung 3 alone: it answers
`precheck_failed: timeout` at `PRECHECK_WAIT` and signals no one.

So every `precheck serve` first reaps what earlier runs left: each process
whose real UID is `df-precheck` and whose age exceeds `REAP_AFTER` (no
precheck the publisher still waits for is that old) gets `SIGKILL`, never
the caller itself or its ancestors; then, under the precheck's own lock
discipline (`precheck.lock` exclusive without waiting: no live run holds
it shared), the snapshots left under `<home>/precheck-snapshots` are
removed. `darkfactory doctor` lists such processes (`roles.precheck`).

Processes are read from `/proc` (Linux) or `/bin/ps` with explicit columns
(macOS); an unknown form is `ListingError`, never a guess. Stdlib only.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import re
import shutil
import signal
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from df import platform as plat
from df.publisher.precheck import PRECHECK_WAIT

#: Seconds after which a `df-precheck` process is a leftover: past the
#: publisher's wait (rung 3), with a margin for its own start and the mint.
REAP_AFTER = PRECHECK_WAIT + 10.0
#: Seconds `reap` waits for the killed processes to be gone.
REAP_WAIT = 5.0
#: `act_precheck.SNAPSHOTS` / `act_precheck.LOCK` (the key holder's side
#: does not import `governance`; tests hold them equal).
SNAPSHOTS = "precheck-snapshots"
LOCK = "precheck.lock"
PS = "/bin/ps"
PS_COLUMNS = "pid=,ppid=,ruid=,etime=,stat="
_ETIME = re.compile(r"(?:(?:(\d+)-)?(\d+):)?(\d+):(\d+)", re.ASCII)
_PROC_STAT = re.compile(
    r"\d+ \(.*\) (\S) (\d+)(?: \S+){17} (\d+) .*", re.ASCII | re.DOTALL
)


class ListingError(Exception):
    """The processes could not be read in a known form."""


class SnapshotError(Exception):
    """The leftover snapshots could not be made consistent."""


@dataclass(frozen=True)
class ProcInfo:
    """One process: PID, parent, real UID, age in seconds, zombie or not."""

    pid: int
    ppid: int
    ruid: int
    age: float
    zombie: bool


Lister = Callable[[], list[ProcInfo]]
Killer = Callable[[int, int], None]


def kill(pid: int, signo: int) -> None:
    """`os.kill` (tests replace this function)."""
    os.kill(pid, signo)


def list_processes() -> list[ProcInfo]:
    """Every process of the host (`/proc` on Linux, `ps` on macOS)."""
    if plat.detect_platform() == "linux-x86_64":
        return _proc_processes(Path("/proc"))
    result = plat.run_command([PS, "-A", "-o", PS_COLUMNS], timeout=30.0)
    if not result.launched or result.returncode != 0:
        raise ListingError(f"ps: код {result.returncode}")
    return parse_ps(result.stdout.decode("ascii", "replace"))


def parse_ps(text: str) -> list[ProcInfo]:
    """`ps -A -o pid=,ppid=,ruid=,etime=,stat=` output; any other line is
    `ListingError`."""
    found = []
    for line in text.splitlines():
        words = line.split()
        if not words:
            continue
        if len(words) != 5 or not all(w.isdigit() for w in words[:3]):
            raise ListingError(f"ps: строка {line[:120]!r}")
        age = _etime_seconds(words[3])
        pid, ppid, ruid = (int(w) for w in words[:3])
        found.append(ProcInfo(pid, ppid, ruid, age, words[4].startswith("Z")))
    if not found:
        raise ListingError("ps: пустой вывод")
    return found


def _etime_seconds(value: str) -> float:
    """`[[dd-]hh:]mm:ss` in seconds."""
    match = _ETIME.fullmatch(value)
    if match is None:
        raise ListingError(f"ps: etime {value!r}")
    days, hours, minutes, seconds = (int(g or 0) for g in match.groups())
    return float(((days * 24 + hours) * 60 + minutes) * 60 + seconds)


def _proc_processes(proc: Path) -> list[ProcInfo]:
    """`/proc/<pid>/stat` and `status` of every process; one that ends
    while read is skipped."""
    try:
        uptime = float((proc / "uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError) as exc:
        raise ListingError(f"/proc/uptime: {exc}") from exc
    ticks = os.sysconf("SC_CLK_TCK")
    found = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            status = (entry / "status").read_text()
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError as exc:
            raise ListingError(f"{entry}: {exc}") from exc
        found.append(_proc_info(int(entry.name), stat, status, uptime, ticks))
    return found


def _proc_info(pid: int, stat: str, status: str, uptime: float, ticks: int) -> ProcInfo:
    match = _PROC_STAT.fullmatch(stat.strip())
    uid = next(
        (ln.split()[1:2] for ln in status.splitlines() if ln.startswith("Uid:")), []
    )
    if match is None or not uid or not uid[0].isdigit():
        raise ListingError(f"/proc/{pid}: неизвестная форма")
    state, ppid, start = match.groups()
    age = uptime - int(start) / ticks
    return ProcInfo(pid, int(ppid), int(uid[0]), age, state == "Z")


def leftovers(
    uid: int, processes: Sequence[ProcInfo], *, own: int | None = None
) -> list[ProcInfo]:
    """The live processes of `uid` older than `REAP_AFTER`, without `own`
    and its ancestors."""
    spared = _lineage(own, processes) if own is not None else set()
    return [
        p
        for p in processes
        if p.ruid == uid and not p.zombie and p.age > REAP_AFTER and p.pid not in spared
    ]


def _lineage(pid: int, processes: Sequence[ProcInfo]) -> set[int]:
    """`pid` and every ancestor of it in `processes`."""
    parent = {p.pid: p.ppid for p in processes}
    seen = {pid}
    while (pid := parent.get(pid, 0)) > 1 and pid not in seen:
        seen.add(pid)
    return seen


def reap(
    uid: int,
    *,
    lister: Lister | None = None,
    killer: Killer | None = None,
    own: int | None = None,
    wait: float = REAP_WAIT,
) -> tuple[list[int], list[str]]:
    """`SIGKILL` every leftover of `uid` (`leftovers`, sparing `own`, by
    default this process, and its ancestors), then wait up to `wait`
    seconds for them to be gone. Returns the PIDs killed and the problems
    (named reasons; never raises). `lister` and `killer` default to
    `list_processes` and `kill`, looked up at the call (tests replace
    them)."""
    lister = lister or list_processes
    killer = killer or kill
    own = os.getpid() if own is None else own
    try:
        old = leftovers(uid, lister(), own=own)
    except ListingError as exc:
        return [], [f"процессы не прочитаны: {exc}"]
    killed, problems = [], []
    for proc in old:
        try:
            killer(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
        except OSError as exc:
            problems.append(f"{proc.pid}: kill: {exc}")
            continue
        killed.append(proc.pid)
    deadline = time.monotonic() + wait
    while killed:
        try:
            alive = {p.pid for p in lister() if not p.zombie} & set(killed)
        except ListingError as exc:
            problems.append(f"процессы не прочитаны: {exc}")
            break
        if not alive:
            break
        if time.monotonic() >= deadline:
            problems.append(f"не завершились: {sorted(alive)}")
            break
        time.sleep(0.05)
    return killed, problems


def clear_snapshots(home: Path) -> list[str]:
    """Remove the snapshots under `<home>/precheck-snapshots` if no run
    holds `<home>/precheck.lock` (exclusive, without waiting; a live run
    holds it shared and its snapshot stays). Returns what was removed; a
    lock or snapshot that cannot be handled is `SnapshotError`; no home
    at all is nothing to clear (the program then fails on its own)."""
    root = home / SNAPSHOTS
    if not home.is_dir():
        return []
    try:
        fd = os.open(home / LOCK, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    except OSError as exc:
        raise SnapshotError(f"{LOCK}: {exc}") from exc
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return []  # a live run: its snapshot is not stale
        except OSError as exc:
            raise SnapshotError(f"{LOCK}: {exc}") from exc
        return _remove_all(root)
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)


def _remove_all(root: Path) -> list[str]:
    try:
        names = sorted(p.name for p in root.iterdir())
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise SnapshotError(f"{root}: {exc}") from exc
    for name in names:
        try:
            shutil.rmtree(root / name)
        except OSError as exc:
            raise SnapshotError(f"{root / name}: {exc}") from exc
    return names


__all__ = [
    "REAP_AFTER",
    "ListingError",
    "ProcInfo",
    "SnapshotError",
    "clear_snapshots",
    "leftovers",
    "list_processes",
    "parse_ps",
    "reap",
]
