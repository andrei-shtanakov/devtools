"""Unit tests of the root-access observer's parsers (`tests/df/observe.py`).

Recorded samples are Step 0's (CI run 40189716039): eslogger JSON lines
(macOS, `/` escaped) and `ausearch --format raw` of the `-F dir=` twins
(Linux). They run without root; the observer itself runs only in CI.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.df import observe
from tests.df.observe import Access

FIXTURES = Path(__file__).parent / "fixtures" / "observe"
STEP0_KEY = "df-step0-3078"
SRC = "/var/tmp-df/step0-3078/audit-src"
PYTHON = "/home/runner/work/_temp/uv-python-dir/cpython-3.12.13-linux-x86_64-gnu"


def _recorded_es() -> list[str]:
    return (FIXTURES / "eslogger-step0.jsonl").read_text().splitlines()


def test_recorded_eslogger_open_and_readdir_are_root_accesses() -> None:
    found = observe.eslogger_accesses(_recorded_es(), [("/dev", 0)])
    assert [(a.op, a.path, a.euid) for a in found] == [
        ("open", "/dev/fd", 0),
        ("readdir", "/dev/fd", 0),
    ]
    assert found[0].pid == 2304
    assert found[0].exe.endswith("/bin/python3.12")


def test_recorded_eslogger_exec_arguments_are_not_accesses() -> None:
    """The recorded exec of /bin/cat names the control file only in argv."""
    control = "/private/var/tmp-df/step0-2193/es-control"
    assert observe.eslogger_accesses(_recorded_es(), [(control, 0)]) == []
    found = observe.eslogger_accesses(_recorded_es(), [("/bin", 0)])
    assert [(a.op, a.path) for a in found] == [("exec", "/bin/cat")]


def _es_line(op: str, path: str, *, euid: int = 0, pid: int = 7, time: str = "") -> str:
    where = {
        "open": {"file": {"path": path}},
        "readdir": {"target": {"path": path}},
        "exec": {"target": {"executable": {"path": path}}, "args": [path]},
    }[op]
    event = {
        "event": {op: where},
        "process": {
            "audit_token": {"euid": euid, "pid": pid},
            "executable": {"path": "/bin/x"},
        },
        "time": time or "2026-10-25T11:38:51.018729555Z",
    }
    return json.dumps(event).replace("/", "\\/")  # eslogger escapes `/`


@pytest.mark.parametrize("op", ["open", "readdir", "exec"])
def test_eslogger_path_under_source_by_root(op: str) -> None:
    line = _es_line(op, "/r/src/a/b")
    found = observe.eslogger_accesses([line], [("/r/src", 0)])
    assert found == [Access(pid=7, euid=0, op=op, path="/r/src/a/b", exe="/bin/x")]


@pytest.mark.parametrize(
    ("line", "why"),
    [
        (_es_line("open", "/r/src/f", euid=501), "not root"),
        (_es_line("open", "/r/srcx/f"), "a sibling with the same prefix"),
        (_es_line("open", "/r/other"), "outside"),
        ("not json /r/src", "garbage"),
    ],
)
def test_eslogger_not_a_violation(line: str, why: str) -> None:
    assert observe.eslogger_accesses([line], [("/r/src", 0)]) == [], why


def test_eslogger_watch_counts_from_its_time() -> None:
    """R93: events before `watch` (root creating the path) do not count."""
    since = observe._es_time("2026-10-25T11:38:51.500000000Z")
    early = _es_line("open", "/r/rel", time="2026-10-25T11:38:51.499999999Z")
    late = _es_line("open", "/r/rel", time="2026-10-25T11:38:51.500000001Z")
    found = observe.eslogger_accesses([early, late], [("/r/rel", since)])
    assert len(found) == 1


def test_es_time() -> None:
    assert observe._es_time("1970-01-01T00:00:01.5Z") == 1_500_000_000
    assert observe._es_time("1970-01-01T00:00:02Z") == 2_000_000_000
    assert observe._es_time(None) == observe.UNKNOWN_TIME
    assert observe._es_time("garbage") == observe.UNKNOWN_TIME
    assert observe._es_time("2026-10-25T11:38:51+00:00") == observe.UNKNOWN_TIME
    assert observe._es_time("2026-10-25T11:38:51.x5Z") == observe.UNKNOWN_TIME


def test_recorded_ausearch_twins() -> None:
    """Step 0's twins: every event of the key, relative names as recorded."""
    raw = (FIXTURES / "ausearch-step0.txt").read_text()
    found = observe.audit_accesses(raw, STEP0_KEY)
    by_pid: dict[int, list[tuple[str, str]]] = {}
    for a in found:
        assert a.euid == 0
        by_pid.setdefault(a.pid, []).append((a.op, a.path))
    assert by_pid == {
        3173: [("openat", f"{SRC}/f")],
        3174: [("openat", SRC), ("openat", "f")],
        3175: [("openat", SRC)],
        3176: [("openat", SRC)],
        3177: [("openat", f"{SRC}/f")],
    }
    cat = next(a for a in found if a.pid == 3177)
    assert cat.exe == "/usr/bin/cat"
    assert next(a for a in found if a.pid == 3173).exe == f"{PYTHON}/bin/python3.12"


def test_ausearch_other_key_and_rule_change_ignored() -> None:
    raw = (FIXTURES / "ausearch-step0.txt").read_text()
    assert observe.audit_accesses(raw, "df-r5-other") == []
    # the CONFIG_CHANGE of add_rule carries the key, its SYSCALL does not
    assert all(a.op != "44" for a in observe.audit_accesses(raw, STEP0_KEY))


def test_ausearch_hex_encoded_name() -> None:
    name = b"/r/src/a b".hex().upper()
    raw = (
        "type=SYSCALL msg=audit(1.0:9): arch=c000003e syscall=2 success=yes "
        'pid=5 euid=0 comm="cat" exe="/usr/bin/cat" key="k"\x1dARCH=x86_64\n'
        f"type=PATH msg=audit(1.0:9): item=0 name={name} nametype=NORMAL\n"
    )
    assert observe.audit_accesses(raw, "k") == [
        Access(pid=5, euid=0, op="open", path="/r/src/a b", exe="/usr/bin/cat")
    ]


def test_audit_str() -> None:
    assert observe._audit_str('"x"') == "x"
    assert observe._audit_str("(null)") == ""
    assert observe._audit_str("2F78") == "/x"


@pytest.fixture()
def canon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A canonical tmp dir whose children pass the source rule as this user."""
    monkeypatch.setattr(observe, "ROOT_UID", os.getuid())
    top = Path(os.path.realpath(tmp_path))
    for name in ("src", "ctl"):
        (top / name).mkdir()
    return top


@pytest.mark.parametrize("time_value", [None, "garbage", "2026-10-25T11:38:51+00:00"])
def test_eslogger_unreadable_time_counts(time_value: str | None) -> None:
    """M1 (F8): an event whose time cannot be read counts under a mid-run
    watch instead of being dropped."""
    event = json.loads(_es_line("open", "/r/rel/f").replace("\\/", "/"))
    event["time"] = time_value
    line = json.dumps(event)
    found = observe.eslogger_accesses([line], [("/r/rel", 10**30)])
    assert [a.path for a in found] == ["/r/rel/f"]


def _with_seq(line: str, seq: int) -> str:
    event = json.loads(line)
    event["global_seq_num"] = seq
    return json.dumps(event)


def test_eslogger_seq_gap_is_unobserved() -> None:
    """I1 (F1): a gap in `global_seq_num` anywhere is counted, matched or not."""
    other = _es_line("open", "/elsewhere")
    hit = _es_line("open", "/r/src/f")
    whole = [_with_seq(other, 0), _with_seq(hit, 1), _with_seq(other, 2)]
    assert observe.eslogger_scan(whole, [("/r/src", 0)]).missing == 0
    gapped = [_with_seq(other, 0), _with_seq(other, 3), _with_seq(hit, 4)]
    scan = observe.eslogger_scan(gapped, [("/r/src", 0)])
    assert scan.missing == 2
    assert len(scan.accesses) == 1


def test_marker_requires_pid_and_time() -> None:
    """M3 (F13): the end marker is this pid's open, not earlier than the wait."""
    ctl = Path("/r/ctl")
    t = "2026-10-25T11:38:51.500000000Z"
    since = observe._es_time(t)
    line = _es_line("open", "/r/ctl/f", pid=42, time=t)
    assert observe.marker_seen([line], ctl, 42, since)
    assert not observe.marker_seen([line], ctl, 43, since)
    assert not observe.marker_seen([line], ctl, 42, since + 1)
    nonroot = _es_line("open", "/r/ctl/f", pid=42, euid=501, time=t)
    assert not observe.marker_seen([nonroot], ctl, 42, since)
    untimed = json.loads(line)
    untimed["time"] = None
    assert not observe.marker_seen([json.dumps(untimed)], ctl, 42, 0)


def test_audit_lost_parsed() -> None:
    status = "enabled 1\nfailure 1\npid 712\nrate_limit 0\nbacklog_limit 8192\nlost 7\n"
    assert observe.audit_lost(status) == 7
    assert observe.audit_lost("enabled 1\n") is None


def test_audit_lost_growth_is_unobserved(tmp_path: Path) -> None:
    """I1 (F1): records lost between start and finish -> `unobserved`."""
    backend = observe._Audit(tmp_path, "auditctl", "ausearch")
    backend.lost = 3
    assert backend._verdict(3, True) is None
    assert "потеряно записей 2" in str(backend._verdict(5, True))
    assert "lost" in str(backend._verdict(None, True))
    assert "конец" in str(backend._verdict(3, False))


@pytest.mark.parametrize(
    ("rc", "stderr", "ok"),
    [(0, "", True), (1, "<no matches>\n", True), (1, "", False), (10, "x", False)],
)
def test_ausearch_failure_is_unobserved(rc: int, stderr: str, ok: bool) -> None:
    """M2 (F14): a failed search is a failure, not "no events"."""
    assert (observe.ausearch_failure(rc, stderr) is None) is ok


def test_non_canonical_source_refused(canon: Path) -> None:
    """I2 (F2): a source through an alias would never match ES paths."""
    alias = canon / "alias"
    alias.symlink_to(canon / "src")
    with pytest.raises(ValueError, match="не канонический"):
        with observe.observe_root_access(
            [alias / "."], control=canon / "ctl", workdir=canon
        ):
            pass
    assert "не канонический" in str(observe.source_problem(alias))


def test_source_with_builder_writable_parent_refused(canon: Path) -> None:
    """I3 (F3): a source whose parent df-build could write could be renamed
    away; the parent must be root's (here: this user's) and not group/other
    writable."""
    assert observe.source_problem(canon / "src") is None
    os.chmod(canon, 0o777)
    try:
        assert "не закреплён" in str(observe.source_problem(canon / "src"))
    finally:
        os.chmod(canon, 0o700)


def test_source_parent_of_another_owner_refused(tmp_path: Path) -> None:
    """With the production owner (root), a user's tmp parent is refused."""
    src = Path(os.path.realpath(tmp_path)) / "src"
    src.mkdir()
    assert "не закреплён" in str(observe.source_problem(src))


def test_source_must_be_a_directory(canon: Path) -> None:
    (canon / "file").write_text("x")
    assert "не каталог" in str(observe.source_problem(canon / "file"))
    assert "недоступен" in str(observe.source_problem(canon / "absent"))


def test_missing_observer_is_unobserved(
    monkeypatch: pytest.MonkeyPatch, canon: Path
) -> None:
    """No eslogger/auditctl: `unobserved` with a reason and the limits."""
    monkeypatch.setattr(observe, "_backend", lambda control, workdir: None)
    record = canon / "rec" / "obs.json"
    with observe.observe_root_access(
        [canon / "src"], control=canon / "ctl", workdir=canon, record=record
    ) as obs:
        obs.watch(canon / "src")
    assert obs.status == "unobserved"
    assert "нет наблюдателя" in obs.reason
    data = json.loads(record.read_text())
    assert data["status"] == "unobserved"
    assert data["limits"] == dict(observe.limits_here())
    assert "before-watch" in data["limits"]


def test_watch_non_canonical_refused(
    monkeypatch: pytest.MonkeyPatch, canon: Path
) -> None:
    monkeypatch.setattr(observe, "_backend", lambda control, workdir: None)
    alias = canon / "alias"
    alias.symlink_to(canon / "src")
    with observe.observe_root_access(
        [canon / "src"], control=canon / "ctl", workdir=canon
    ) as obs:
        with pytest.raises(ValueError, match="не канонический"):
            obs.watch(alias)


class _FakeBackend:
    def __init__(self, failure_at_start: str | None, ended: bool) -> None:
        self.failure_at_start = failure_at_start
        self.ended = ended
        self.watched: list[Path] = []

    def start(self, sources: object) -> str | None:
        return self.failure_at_start

    def watch(self, path: Path) -> None:
        self.watched.append(path)

    def finish(self) -> observe.Finish:
        hit = [Access(1, 0, "open", "/s/f", "/bin/cat")]
        return observe.Finish(hit, 2, None if self.ended else "конец не подтверждён")


@pytest.mark.parametrize(
    ("start", "ended", "status"),
    [(None, True, "observed"), (None, False, "unobserved"), ("x", True, "unobserved")],
)
def test_status_follows_both_control_markers(
    monkeypatch: pytest.MonkeyPatch,
    canon: Path,
    start: str | None,
    ended: bool,
    status: str,
) -> None:
    backend = _FakeBackend(start, ended)
    monkeypatch.setattr(observe, "_backend", lambda control, workdir: backend)
    with observe.observe_root_access(
        [canon / "src"], control=canon / "ctl", workdir=canon
    ) as obs:
        obs.watch(canon / "src")
    assert obs.status == status
    if start is None:
        assert backend.watched == [canon / "src"]
        assert obs.violations and obs.control_events == 2
