"""Unit tests of the CI Step 0 probe script (Task 13-A, `tests/df/ci_step0.py`).

Only the pure parts and the refusals run here: the probes themselves call
`sudo` or need root and run only in CI df-stage1.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

import pytest

from tests.df import ci_step0


@pytest.mark.parametrize(
    ("part", "euid", "env", "allowed"),
    [
        ("runner", 501, {"GITHUB_ACTIONS": "true"}, True),
        ("runner", 501, {}, False),
        ("root", 0, {"DF_ROOT_ACCEPTANCE": "1"}, True),
        ("root", 0, {}, False),
        ("root", 501, {"DF_ROOT_ACCEPTANCE": "1"}, False),
    ],
)
def test_refusal_reason(
    part: str, euid: int, env: dict[str, str], allowed: bool
) -> None:
    assert (ci_step0.refusal_reason(part, euid, env) is None) is allowed


def test_refused_part_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    out = tmp_path / "out"
    assert ci_step0.main(["runner", "--out", str(out)]) == 2
    assert not out.exists()


def test_collect_records_a_raising_probe() -> None:
    def broken() -> object:
        raise PermissionError(1, "not permitted")

    facts = ci_step0.collect({"ok": lambda: 1, "broken": broken})
    assert facts["ok"] == 1
    assert facts["broken"] == {"error": "PermissionError: [Errno 1] not permitted"}


def test_cmd_records_launch_failure_and_output(tmp_path: Path) -> None:
    missing = ci_step0.cmd([tmp_path / "absent"])
    assert "error" in missing
    done = ci_step0.cmd(["/bin/sh", "-c", "echo out; echo err >&2; exit 3"])
    assert (done["rc"], done["stdout"], done["stderr"]) == (3, "out\n", "err\n")


def test_dscl_ids_and_free_ids() -> None:
    text = "_www       70\nnobody     -2\nrunner     501\nbroken\n_x  4x\n"
    assert ci_step0.dscl_ids(text) == {70, -2, 501}
    assert ci_step0.free_ids(range(400, 405), {401}, {403}) == [400, 402, 404]


AUSEARCH = """\
----
type=PROCTITLE msg=audit(1729000000.100:51): proctitle=707974686F6E
type=PATH msg=audit(1729000000.100:51): item=0 name="f" inode=12 nametype=NORMAL
type=CWD msg=audit(1729000000.100:51): cwd="/"
type=SYSCALL msg=audit(1729000000.100:51): arch=c000003e syscall=257 success=yes \
exit=4 a0=3 ppid=10 pid=4242 auid=1001 uid=0 euid=0 key="df-step0-1"
----
type=SYSCALL msg=audit(1729000000.200:52): arch=c000003e syscall=217 success=yes \
exit=48 ppid=10 pid=4243 uid=0 euid=0 key="df-step0-1"
type=PATH msg=audit(1729000000.300:53): item=0 name=/var/tmp-df/x/f inode=12
type=SYSCALL msg=audit(1729000000.300:53): arch=c000003e syscall=999 ppid=1 pid=4243
"""


def test_audit_events_grouped_by_pid() -> None:
    events = ci_step0.audit_events(AUSEARCH)
    assert events[4242] == [{"names": ["f"], "syscall": "openat"}]
    assert events[4243] == [
        {"names": [], "syscall": "getdents64"},
        {"names": ["/var/tmp-df/x/f"], "syscall": "999"},
    ]
    assert 10 not in events  # `ppid=` is not `pid=`


CONTROL = Path("/private/var/tmp-df/step0-1/es-control")
#: eslogger's JSON escapes `/` (CI run 40186274147, macos-15).
ES_OTHER = (
    '{"event_type":10,"process":{"audit_token":{"euid":0}},'
    '"event":{"open":{"file":{"path":"\\/usr\\/lib\\/dyld"}}}}'
)
ES_CONTROL = (
    '{"event_type":10,"process":{"audit_token":{"euid":0},'
    '"executable":{"path":"\\/usr\\/bin\\/python3"}},'
    '"event":{"open":{"file":{"path":'
    '"\\/private\\/var\\/tmp-df\\/step0-1\\/es-control\\/f"}}}}'
)


def test_eslogger_control_decodes_escaped_paths() -> None:
    assert "\\/" in ES_CONTROL and str(CONTROL) not in ES_CONTROL
    seen = ci_step0.eslogger_control(
        [ES_OTHER, "not json", ES_CONTROL], CONTROL, near="/private"
    )
    assert (seen["events"], seen["control_events"], seen["control_seen"]) == (
        2,
        1,
        True,
    )
    sample = seen["sample"]
    assert isinstance(sample, dict)
    assert sample["process_audit_token"] == {"euid": 0}
    assert sample["process_executable"] == "/usr/bin/python3"


def test_eslogger_control_absent_lists_kinds_paths_and_near_events() -> None:
    sibling = _es("/private/var/tmp-df/x", exe="/bin/cat")
    seen = ci_step0.eslogger_control(
        [ES_OTHER, sibling], CONTROL, near="/private/var/tmp-df"
    )
    assert seen["control_seen"] is False
    sample = seen["sample"]
    assert isinstance(sample, dict)
    assert sample["event_kinds"] == ["open"]
    assert sample["top_paths"] == [
        ("/usr/lib/dyld", 1),
        ("/private/var/tmp-df/x", 1),
    ]
    assert [e["process_executable"] for e in sample["near_events"]] == ["/bin/cat"]


def _es(path: str, euid: int = 0, exe: str = "/bin/cat") -> str:
    """One eslogger `open` line as eslogger writes it (`/` escaped)."""
    esc = path.replace("/", "\\/")
    exe_esc = exe.replace("/", "\\/")
    return (
        f'{{"event_type":10,"process":{{"audit_token":{{"euid":{euid}}},'
        f'"executable":{{"path":"{exe_esc}"}}}},'
        f'"event":{{"open":{{"file":{{"path":"{esc}"}}}}}}}}'
    )


class _Clock:
    """Fake time: `sleep` advances it and releases lines due by then."""

    def __init__(self, feed: dict[float, list[str]]) -> None:
        self.now = 0.0
        self.feed = feed
        self.pending: list[str] = []

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        self.pending += self.feed.pop(self.now, [])

    def new_lines(self) -> list[str]:
        lines, self.pending = self.pending, []
        return lines


def test_wait_for_control_per_way() -> None:
    """Each way is seen only by its own executable, euid 0, on the control;
    a seen way stops touching; event counts are recorded at every poll."""
    clock = _Clock(
        {
            1.0: [_es("/usr/bin", exe="/bin/ls")],
            1.5: [
                _es(f"{CONTROL}/f", exe="/bin/cat"),
                _es(str(CONTROL), euid=501, exe="/bin/ls"),
            ],
            2.5: [_es(str(CONTROL), exe="/bin/ls")],
        }
    )
    touches: list[tuple[str, float]] = []

    def way(name: str, exe: str) -> tuple[object, str]:
        return (lambda: touches.append((name, clock.now)), exe)

    result = ci_step0.wait_for_control(
        clock.new_lines,
        {"cat": way("cat", "/bin/cat"), "ls": way("ls", "/bin/ls")},  # type: ignore[dict-item]
        CONTROL,
        clock=lambda: clock.now,
        sleep=clock.sleep,
    )
    assert result["seconds"] == {"cat": 1.5, "ls": 2.5}
    assert [t for n, t in touches if n == "cat"] == [0.0, 0.5, 1.0]
    assert [t for n, t in touches if n == "ls"] == [0.0, 0.5, 1.0, 1.5, 2.0]
    assert result["lines_during_wait"] == [
        (0.5, 0),
        (1.0, 1),
        (1.5, 3),
        (2.0, 3),
        (2.5, 4),
    ]


def test_wait_for_control_times_out() -> None:
    clock = _Clock({})
    result = ci_step0.wait_for_control(
        clock.new_lines,
        {"python": (lambda: None, "/usr/bin/python3")},
        CONTROL,
        timeout=3.0,
        clock=lambda: clock.now,
        sleep=clock.sleep,
    )
    assert result["seconds"] == {"python": None}
    assert clock.now == 3.0


def test_line_reader_returns_only_complete_new_lines(tmp_path: Path) -> None:
    path = tmp_path / "raw"
    reader = ci_step0._LineReader(path)
    assert reader.new_lines() == []
    path.write_bytes(b"a\nb")
    assert reader.new_lines() == ["a"]
    with path.open("ab") as stream:
        stream.write(b"c\nd\n")
    assert reader.new_lines() == ["bc", "d"]


def test_eslogger_control_needs_euid_0() -> None:
    seen = ci_step0.eslogger_control(
        [_es(f"{CONTROL}/f", euid=501), _es("/usr/bin"), _es("/usr/bin")],
        CONTROL,
        near="/private/var/tmp-df",
    )
    assert seen["control_seen"] is False
    sample = seen["sample"]
    assert isinstance(sample, dict)
    assert sample["top_paths"] == [("/usr/bin", 2), (f"{CONTROL}/f", 1)]


@pytest.mark.parametrize(("problems", "rc"), [([], 0), (["git: bad"], 1)])
def test_r96_main(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    problems: list[str],
    rc: int,
) -> None:
    seen: list[object] = []

    def fake(cli: object, platform: object) -> list[str]:
        seen.append(cli)
        return problems

    monkeypatch.setattr(ci_step0.plat, "approved_cli_problems", fake)
    monkeypatch.setattr(ci_step0.plat, "detect_platform", lambda: "macos-arm64")
    assert ci_step0.r96_main() == rc
    assert seen == [ci_step0.R96_TOOLS]
    assert (
        "::error title=select CommandLineTools (R96)::git: bad"
        in capsys.readouterr().out
    ) is bool(problems)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_for(path: Path, seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists():
        assert time.monotonic() < deadline, f"{path} did not appear"
        time.sleep(0.05)


def test_detached_command_stops_on_sigint(tmp_path: Path) -> None:
    """m2: the command is not our child, and SIGINT stops it (no orphan)."""
    pid = ci_step0._start_detached(
        ["/bin/sleep", "60"], tmp_path / "out", tmp_path / "err"
    )
    try:
        with pytest.raises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)  # not our child: detached
        assert ci_step0._stop_detached(pid) == "exited on SIGINT"
    finally:
        if _alive(pid):
            os.kill(pid, signal.SIGKILL)
    assert not _alive(pid)


def test_detached_command_ignoring_sigint_is_killed(tmp_path: Path) -> None:
    """m2: a command ignoring SIGINT is killed, and its exit is confirmed."""
    ready = tmp_path / "ready"
    code = (
        "import signal, sys, time; signal.signal(signal.SIGINT, signal.SIG_IGN); "
        "open(sys.argv[1], 'w').close(); time.sleep(60)"
    )
    pid = ci_step0._start_detached(
        [sys.executable, "-c", code, str(ready)], tmp_path / "out", tmp_path / "err"
    )
    try:
        _wait_for(ready)  # SIGINT before the handler would simply stop it
        assert ci_step0._stop_detached(pid, grace=0.5) == "killed"
    finally:
        if _alive(pid):
            os.kill(pid, signal.SIGKILL)
    assert not _alive(pid)


def test_stop_of_a_gone_process(tmp_path: Path) -> None:
    pid = ci_step0._start_detached(["/usr/bin/true"], tmp_path / "o", tmp_path / "e")
    deadline = time.monotonic() + 10
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert ci_step0._stop_detached(pid) == "exited before SIGINT"
