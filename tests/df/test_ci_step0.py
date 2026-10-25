"""Unit tests of the CI Step 0 probe script (Task 13-A, `tests/df/ci_step0.py`).

Only the pure parts and the refusals run here: the probes themselves call
`sudo` or need root and run only in CI df-stage1.
"""

from __future__ import annotations

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
    seen = ci_step0.eslogger_control([ES_OTHER, "not json", ES_CONTROL], CONTROL)
    assert (seen["events"], seen["control_events"], seen["control_seen"]) == (
        2,
        1,
        True,
    )
    sample = seen["sample"]
    assert isinstance(sample, dict)
    assert sample["process_audit_token"] == {"euid": 0}
    assert sample["process_executable"] == "/usr/bin/python3"


def test_eslogger_control_absent_lists_first_paths() -> None:
    sibling = ES_OTHER.replace("\\/usr\\/lib\\/dyld", "\\/private\\/var\\/tmp-df\\/x")
    seen = ci_step0.eslogger_control([ES_OTHER, sibling], CONTROL)
    assert seen["control_seen"] is False
    assert seen["sample"] == {
        "first_event_paths": [["/usr/lib/dyld"], ["/private/var/tmp-df/x"]]
    }
