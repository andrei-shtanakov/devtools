"""`df.precheck_reap`: leftovers of `df-precheck` (2b Task 10, option c).

The lister and the killer are injected: no test reads or signals a real
process (the autouse `_no_real_reap` of `conftest.py` guards the rest)."""

from __future__ import annotations

import fcntl
import os
import signal
from pathlib import Path

import pytest

from df import cli, precheck_reap, sudoers
from df.precheck_reap import (
    REAP_AFTER,
    ListingError,
    ProcInfo,
    SnapshotError,
    clear_snapshots,
    leftovers,
    parse_ps,
    reap,
)
from df.publisher.precheck import PRECHECK_WAIT

UID = 4242
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


def test_names_equal_act_precheck() -> None:
    from governance import act_precheck

    assert precheck_reap.SNAPSHOTS == act_precheck.SNAPSHOTS
    assert precheck_reap.LOCK == act_precheck.LOCK


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


def _home(tmp_path: Path, names: tuple[str, ...]) -> Path:
    home = tmp_path / "home"
    for name in names:
        (home / precheck_reap.SNAPSHOTS / name / "repo.git").mkdir(parents=True)
    home.mkdir(exist_ok=True)
    return home


def test_snapshots_cleared_when_no_run_holds_the_lock(tmp_path: Path) -> None:
    home = _home(tmp_path, ("a" * 32, "b" * 32))
    assert clear_snapshots(home) == ["a" * 32, "b" * 32]
    assert list((home / precheck_reap.SNAPSHOTS).iterdir()) == []


def test_snapshots_of_a_live_run_kept(tmp_path: Path) -> None:
    """A live run holds the lock shared: nothing is removed."""
    home = _home(tmp_path, ("c" * 32,))
    fd = os.open(home / precheck_reap.LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        assert clear_snapshots(home) == []
    finally:
        os.close(fd)
    assert [p.name for p in (home / precheck_reap.SNAPSHOTS).iterdir()] == ["c" * 32]


def test_snapshot_not_removable_refused(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root removes anything")
    home = _home(tmp_path, ("d" * 32,))
    locked = home / precheck_reap.SNAPSHOTS / ("d" * 32)
    locked.chmod(0o500)
    try:
        with pytest.raises(SnapshotError):
            clear_snapshots(home)
    finally:
        locked.chmod(0o700)


def test_no_home_nothing_to_clear(tmp_path: Path) -> None:
    assert clear_snapshots(tmp_path / "absent") == []


def test_serve_reaps_before_the_program(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`precheck serve`: the reap runs after the environment reset and
    before the program loads; its problems go to stderr by name and do not
    stop the run; snapshots that cannot be cleared refuse it."""
    order: list[str] = []

    def fake_reap(uid: int) -> tuple[list[int], list[str]]:
        order.append(f"reap {uid} {os.environ.get('HOME')}")
        return [5], ["процессы не прочитаны: x"]

    monkeypatch.setattr(precheck_reap, "reap", fake_reap)
    monkeypatch.setattr(
        precheck_reap, "clear_snapshots", lambda h: order.append("clear")
    )
    home = tmp_path / "h"
    cli._reap(7, home)  # noqa: SLF001
    assert order == [f"reap 7 {os.environ.get('HOME')}", "clear"]
    err = capsys.readouterr().err
    assert "precheck serve: уборка: процессы не прочитаны: x" in err
    assert "убиты [5]" in err

    def bad(_home: Path) -> list[str]:
        raise SnapshotError("precheck.lock: denied")

    monkeypatch.setattr(precheck_reap, "clear_snapshots", bad)
    with pytest.raises(cli.Refusal, match="уборка: снимки: precheck.lock: denied"):
        cli._reap(7, home)  # noqa: SLF001
