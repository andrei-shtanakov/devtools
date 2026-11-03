"""`df.precheck_reap`: leftover processes of `df-precheck` (2b Task 10,
option c).

The lister and the killer are injected: no test reads or signals a real
process (the autouse `_no_real_reap` of `conftest.py` guards the rest)."""

from __future__ import annotations

import os
import signal
from pathlib import Path

import pytest

from df import cli, precheck_reap, sudoers
from df import platform as plat
from df.precheck_reap import (
    REAP_AFTER,
    ListingError,
    ProcInfo,
    leftovers,
    parse_ps,
    reap,
)
from df.publisher.precheck import PRECHECK_WAIT

UID = 4242
#: `list_processes` before the autouse `_no_real_reap` replaces it.
REAL_LIST = precheck_reap.list_processes
OLD = REAP_AFTER + 1
YOUNG = REAP_AFTER - 1


def proc(pid: int, ppid: int = 1, *, uid: int = UID, age: float = OLD) -> ProcInfo:
    return ProcInfo(pid, ppid, uid, age, False)


class Host:
    """A process table the killer edits: a killed process is gone."""

    def __init__(self, procs: list[ProcInfo]) -> None:
        self.procs = list(procs)
        self.killed: list[tuple[int, int]] = []

    def lister(self) -> list[ProcInfo]:
        return list(self.procs)

    def killer(self, pid: int, signo: int) -> None:
        self.killed.append((pid, signo))
        self.procs = [p for p in self.procs if p.pid != pid]


def test_rungs_order() -> None:
    """The threshold lies past every rung: sudo's 180 s, the wait's 200 s."""
    assert sudoers.PRECHECK_TIMEOUT < PRECHECK_WAIT < REAP_AFTER


def test_old_process_killed_young_spared() -> None:
    host = Host([proc(10), proc(11, age=YOUNG), proc(12, uid=UID + 1)])
    killed, problems = reap(UID, lister=host.lister, killer=host.killer, own=99)
    assert (killed, problems) == ([10], [])
    assert host.killed == [(10, signal.SIGKILL)]


def test_self_and_ancestors_spared() -> None:
    """Own process and its chain up are never killed, old or not; a
    sibling of the same age is."""
    table = [proc(20, 1), proc(21, 20), proc(22, 21), proc(23, 20)]
    host = Host(table)
    killed, _ = reap(UID, lister=host.lister, killer=host.killer, own=22)
    assert killed == [23]


def test_zombies_spared() -> None:
    zombie = ProcInfo(30, 1, UID, OLD, True)
    assert leftovers(UID, [zombie]) == []


def test_unparseable_listing_is_a_problem_not_a_crash() -> None:
    def broken() -> list[ProcInfo]:
        raise ListingError("ps: строка 'x'")

    killed, problems = reap(UID, lister=broken, killer=Host([]).killer, own=1)
    assert killed == [] and problems == ["процессы не прочитаны: ps: строка 'x'"]


def test_kill_failure_named() -> None:
    def denied(pid: int, signo: int) -> None:
        raise PermissionError(1, "Operation not permitted")

    killed, problems = reap(UID, lister=Host([proc(40)]).lister, killer=denied, own=1)
    assert killed == [] and problems and problems[0].startswith("40: kill:")


def test_survivor_named_after_wait() -> None:
    host = Host([proc(50)])
    killed, problems = reap(
        UID, lister=host.lister, killer=lambda p, s: None, own=1, wait=0.1
    )
    assert killed == [50] and problems == ["не завершились: [50]"]


@pytest.mark.parametrize(
    ("etime", "seconds"),
    [("00:05", 5), ("03:30", 210), ("01:00:00", 3600), ("2-01:02:03", 176523)],
)
def test_parse_ps(etime: str, seconds: int) -> None:
    text = f"  1     0     0 {etime} Ss\n  77    1   501 00:01 Z\n"
    found = parse_ps(text)
    assert found[0] == ProcInfo(1, 0, 0, float(seconds), False)
    assert found[1].zombie


@pytest.mark.parametrize(
    "text",
    ["", "1 0 0 00:05\n", "1 0 x 00:05 S\n", "1 0 0 5s S\n", "1 0 0 1:2:3:4 S\n"],
)
def test_parse_ps_refuses_unknown(text: str) -> None:
    with pytest.raises(ListingError):
        parse_ps(text)


def _proc_tree(tmp_path: Path, stat: str, status: str) -> Path:
    proc_dir = tmp_path / "proc"
    (proc_dir / "7").mkdir(parents=True)
    (proc_dir / "uptime").write_text("1000.50 1.0\n")
    (proc_dir / "7" / "stat").write_text(stat)
    (proc_dir / "7" / "status").write_text(status)
    (proc_dir / "self").mkdir()
    return proc_dir


def test_proc_processes(tmp_path: Path) -> None:
    """Linux `/proc`: ppid and start from `stat` (a `comm` with `) ` in
    it), the real UID from `status`."""
    ticks = os.sysconf("SC_CLK_TCK")
    rest = " ".join(["0"] * 17)
    stat = f"7 (a) b) T 3 {rest} {50 * ticks} 0 0\n"
    status = "Name:\ta\nUid:\t1001\t1001\t1001\t1001\n"
    found = precheck_reap._proc_processes(_proc_tree(tmp_path, stat, status))  # noqa: SLF001
    assert found == [ProcInfo(7, 3, 1001, 950.5, False)]


def test_proc_processes_refuses_unknown(tmp_path: Path) -> None:
    root = _proc_tree(tmp_path, "7 (a) T 3\n", "Uid:\t1\n")
    with pytest.raises(ListingError):
        precheck_reap._proc_processes(root)  # noqa: SLF001


def test_proc_processes_skips_ended_and_unreadable(tmp_path: Path) -> None:
    """A process gone while read (no `stat`) and one whose files this UID
    cannot read (`hidepid`) are skipped, not a failure."""
    ticks = os.sysconf("SC_CLK_TCK")
    rest = " ".join(["0"] * 17)
    root = _proc_tree(tmp_path, f"7 (a) S 1 {rest} {ticks} 0\n", "Uid:\t5\n")
    (root / "8").mkdir()  # ended: no files
    hidden = root / "9"
    hidden.mkdir()
    (hidden / "stat").write_text("9 (x) S 1\n")
    (hidden / "status").write_text("Uid:\t6\n")
    real_read = Path.read_text

    def read_text(path: Path, *args: object, **kwargs: object) -> str:
        if path.parent == hidden:
            raise PermissionError(13, "Permission denied")
        return real_read(path, *args, **kwargs)  # type: ignore[arg-type]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "read_text", read_text)
        found = precheck_reap._proc_processes(root)  # noqa: SLF001
    assert [p.pid for p in found] == [7]


def test_list_processes_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """macOS: `ps` with the fixed columns; its failure is `ListingError`.
    Linux: `/proc`."""
    lister = REAL_LIST
    seen: list[list[str]] = []

    def run(argv: list[str], **_kw: object) -> plat.RunResult:
        seen.append(list(argv))
        return plat.RunResult(0, b"  1 0 0 00:05 Ss\n", b"", True)

    monkeypatch.setattr(plat, "detect_platform", lambda: "macos-arm64")
    monkeypatch.setattr(plat, "run_command", run)
    assert lister() == [ProcInfo(1, 0, 0, 5.0, False)]
    assert seen == [["/bin/ps", "-A", "-o", "pid=,ppid=,ruid=,etime=,stat="]]
    monkeypatch.setattr(
        plat, "run_command", lambda *_a, **_k: plat.RunResult(1, b"", b"x", True)
    )
    with pytest.raises(ListingError, match="ps: код 1"):
        lister()
    calls: list[Path] = []
    monkeypatch.setattr(plat, "detect_platform", lambda: "linux-x86_64")
    monkeypatch.setattr(
        precheck_reap, "_proc_processes", lambda p: calls.append(p) or []
    )
    assert lister() == [] and calls == [Path("/proc")]


def test_vanished_process_skipped() -> None:
    """A leftover gone before the kill (`ProcessLookupError`) is neither
    killed nor a problem."""

    def gone(pid: int, signo: int) -> None:
        raise ProcessLookupError(3, "No such process")

    killed, problems = reap(UID, lister=Host([proc(60)]).lister, killer=gone, own=1)
    assert (killed, problems) == ([], [])


def test_reused_pid_not_killed() -> None:
    """The second reading shows the PID younger (reused): no kill."""
    readings = iter([[proc(70)], [proc(70, age=1.0)]])
    host = Host([])
    killed, problems = reap(
        UID, lister=lambda: next(readings), killer=host.killer, own=1
    )
    assert (killed, problems, host.killed) == ([], [], [])


def test_listing_oserror_is_a_problem() -> None:
    def broken() -> list[ProcInfo]:
        raise OSError(5, "Input/output error")

    killed, problems = reap(UID, lister=broken, killer=Host([]).killer, own=1)
    assert killed == [] and problems[0].startswith("процессы не прочитаны:")


def test_cli_reap_reports_on_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`cli._reap`: problems and kills are named on stderr and never stop
    the run (the order against the environment reset and the program load
    is `test_cli.py::test_precheck_serve_reaps_after_reset_before_load`)."""
    monkeypatch.setattr(
        precheck_reap, "reap", lambda uid: ([5], ["процессы не прочитаны: x"])
    )
    cli._reap(7)  # noqa: SLF001
    err = capsys.readouterr().err
    assert "precheck serve: уборка: процессы не прочитаны: x" in err
    assert "precheck serve: уборка: убиты [5]" in err
