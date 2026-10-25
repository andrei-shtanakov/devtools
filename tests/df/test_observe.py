"""Unit tests of the root-access observer's parsers (`tests/df/observe.py`).

Recorded samples are Step 0's (CI run 40189716039): eslogger JSON lines
(macOS, `/` escaped) and `ausearch --format raw` of the `-F dir=` twins
(Linux). They run without root; the observer itself runs only in CI.
"""

from __future__ import annotations

import json
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
    assert observe._es_time(None) == 0
    assert observe._es_time("garbage") == 0


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


def test_missing_observer_is_unobserved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No eslogger/auditctl: `unobserved` with a reason, never an error."""
    monkeypatch.setattr(observe, "_backend", lambda control, workdir: None)
    src, ctl = tmp_path / "src", tmp_path / "ctl"
    src.mkdir()
    ctl.mkdir()
    record = tmp_path / "rec" / "obs.json"
    with observe.observe_root_access(
        [src], control=ctl, workdir=tmp_path, record=record
    ) as obs:
        obs.watch(src)
    assert obs.status == "unobserved"
    assert "нет наблюдателя" in obs.reason
    assert json.loads(record.read_text())["status"] == "unobserved"


def test_source_must_exist(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="не каталог"):
        with observe.observe_root_access(
            [tmp_path / "absent"], control=tmp_path, workdir=tmp_path
        ):
            pass


class _FakeBackend:
    def __init__(self, failure_at_start: str | None, ended: bool) -> None:
        self.failure_at_start = failure_at_start
        self.ended = ended
        self.watched: list[Path] = []

    def start(self, sources: object) -> str | None:
        return self.failure_at_start

    def watch(self, path: Path) -> None:
        self.watched.append(path)

    def finish(self) -> tuple[list[Access], int, str | None]:
        hit = [Access(1, 0, "open", "/s/f", "/bin/cat")]
        return hit, 2, None if self.ended else "конец не подтверждён"


@pytest.mark.parametrize(
    ("start", "ended", "status"),
    [(None, True, "observed"), (None, False, "unobserved"), ("x", True, "unobserved")],
)
def test_status_follows_both_control_markers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    start: str | None,
    ended: bool,
    status: str,
) -> None:
    backend = _FakeBackend(start, ended)
    monkeypatch.setattr(observe, "_backend", lambda control, workdir: backend)
    with observe.observe_root_access(
        [tmp_path], control=tmp_path, workdir=tmp_path
    ) as obs:
        obs.watch(tmp_path)
    assert obs.status == status
    if start is None:
        assert backend.watched == [tmp_path]
        assert obs.violations and obs.control_events == 2
