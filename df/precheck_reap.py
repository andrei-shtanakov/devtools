"""Leftover processes of `df-precheck` (stage 2b, Task 10).

The precheck has three timeout rungs (plan decision 5): its own `DEADLINE`
(150 s), sudo's `command_timeout` (180 s, configured; that it fires is not
proved by a row) and the publisher's wait (`PRECHECK_WAIT`, 200 s). A
stopped command is never ended by rung 2 -- sudo suspends itself when its
command stops (CI run 41673160363, both OSes) -- nor are the git children,
which run in their own session. The publisher's guarantee is rung 3 alone:
it answers `precheck_failed: timeout` at `PRECHECK_WAIT` and signals no one.

So every `precheck serve` first reaps what earlier runs left: each process
whose real UID is `df-precheck` and whose age exceeds `REAP_AFTER` (no
precheck the publisher still waits for is that old) gets `SIGKILL`, never
the caller itself or its ancestors. Killing a leftover releases its shared
hold of `precheck.lock`; the program's own stale-snapshot cleanup
(`act_precheck._locked`) then removes its snapshot. `darkfactory doctor`
lists such processes (`roles.precheck`).

Processes are read from `/proc` (Linux) or `/bin/ps` with explicit columns
(macOS); an unknown form is `ListingError`, never a guess. Stdlib only.
"""

from __future__ import annotations

import os
import re
import signal
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from df import platform as plat
from df.publisher.precheck import PRECHECK_WAIT

#: Seconds after which a `df-precheck` process is a leftover: past the
#: publisher's wait (rung 3), with a margin of 30 s for its own start and
#: the mint (and for the next call's own start, so a leftover is never
#: reaped by the call right after the wait).
REAP_AFTER = PRECHECK_WAIT + 30.0
#: Seconds `reap` waits for the killed processes to be gone.
REAP_WAIT = 5.0
ROOT_UID = 0
PS = "/bin/ps"
#: Seconds one `ps` listing of the reap may take. `reap` lists before the program
#: starts its `DEADLINE` (twice, then while it waits for the killed; a
#: failed listing ends the wait), so a hung `ps` must not use up the margin
#: between rung 1 and rung 3 (50 s). A timeout is `ListingError`.
PS_TIMEOUT = 3.0
PS_COLUMNS = "pid=,ppid=,ruid=,etime=,stat="
_ETIME = re.compile(r"(?:(?:(\d+)-)?(\d+):)?(\d+):(\d+)", re.ASCII)
_PROC_STAT = re.compile(
    r"\d+ \(.*\) (\S) (\d+)(?: \S+){17} (\d+) .*", re.ASCII | re.DOTALL
)


class ListingError(Exception):
    """The processes could not be read in a known form."""


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


def list_processes(*, timeout: float = PS_TIMEOUT) -> list[ProcInfo]:
    """Every process of the host (`/proc` on Linux, `ps` on macOS, bounded
    by `timeout` seconds: `PS_TIMEOUT` for the reap, more for `doctor`)."""
    if plat.detect_platform() == "linux-x86_64":
        return _proc_processes(Path("/proc"))
    result = plat.run_command([PS, "-A", "-o", PS_COLUMNS], timeout=timeout)
    if result.timed_out:
        raise ListingError(f"ps: срок {timeout:g} с истёк")
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
    while read, or whose files this UID cannot read, is skipped."""
    try:
        uptime = float((proc / "uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError) as exc:
        raise ListingError(f"/proc/uptime: {exc}") from exc
    ticks = os.sysconf("SC_CLK_TCK")
    found = []
    try:
        entries = list(proc.iterdir())
    except OSError as exc:
        raise ListingError(f"{proc}: {exc}") from exc
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            # bytes: `comm` is cut at 15 bytes, possibly inside a character
            stat = (entry / "stat").read_bytes().decode("utf-8", "replace")
            status = (entry / "status").read_bytes().decode("utf-8", "replace")
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            # ended while read, or unreadable to this UID (`hidepid`): not
            # a process of this UID
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
    and its ancestors; never pid 1, never any process of UID 0 (a role with
    UID 0 is no role: `reap` refuses it by name)."""
    if uid == ROOT_UID:
        return []
    spared = _lineage(own, processes) if own is not None else set()
    return [
        p
        for p in processes
        if p.ruid == uid
        and p.pid > 1
        and not p.zombie
        and p.age > REAP_AFTER
        and p.pid not in spared
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
    as named reasons: a listing that fails in any way is a problem, not an
    exception; UID 0 is refused by name and nothing is killed. `lister` and `killer` default to
    `list_processes` and `kill`, looked up at the call (tests replace
    them).

    PID reuse: the processes are read twice and a leftover is killed only
    if the second reading still shows it with this UID and at least its
    age (a reused PID is younger). The gap between that reading and the
    kill remains; a PID reused inside it can only be another process of
    this UID (the kernel's permission rule), and the publisher calls the
    precheck one at a time."""
    if uid == ROOT_UID:
        return [], ["UID 0: уборка не выполняется (роль с UID 0 — не роль)"]
    lister = lister or list_processes
    killer = killer or kill
    own = os.getpid() if own is None else own
    try:
        old = leftovers(uid, lister(), own=own)
        again = {p.pid: p for p in lister()} if old else {}
    except Exception as exc:  # noqa: BLE001 - the fence: any listing failure
        return [], [f"процессы не прочитаны: {type(exc).__name__}: {exc}"]
    killed, problems = [], []
    for proc in old:
        now = again.get(proc.pid)
        if now is None or now.zombie or now.ruid != uid or now.age < proc.age:
            continue  # gone, or the PID is someone else's now
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
        except Exception as exc:  # noqa: BLE001 - the fence, as above
            problems.append(f"процессы не прочитаны: {type(exc).__name__}: {exc}")
            break
        if not alive:
            break
        if time.monotonic() >= deadline:
            problems.append(f"не завершились: {sorted(alive)}")
            break
        time.sleep(0.05)
    return killed, problems


__all__ = [
    "REAP_AFTER",
    "ListingError",
    "ProcInfo",
    "leftovers",
    "list_processes",
    "parse_ps",
    "reap",
]
