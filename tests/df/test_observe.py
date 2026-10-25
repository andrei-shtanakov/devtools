"""Unit tests of the root-access observer (`tests/df/observe.py`), non-root.

Recorded samples are Step 0's (CI run 40189716039): eslogger JSON lines
(macOS, `/` escaped) and `ausearch --format raw` of the `-F dir=` twins
(Linux). The source rules run over tmp directories with the trusted-path
policy replaced by one for this user; the observer itself runs only in CI.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import unicodedata
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.df import observe
from tests.df.observe import Access, Ident, Watch

FIXTURES = Path(__file__).parent / "fixtures" / "observe"
STEP0_KEY = "df-step0-3078"
SRC = "/var/tmp-df/step0-3078/audit-src"
PYTHON = "/home/runner/work/_temp/uv-python-dir/cpython-3.12.13-linux-x86_64-gnu"
T = "2026-10-25T11:38:51.018729555Z"
_seq = itertools.count(100)


def _w(path: str, since: int = 0, idents: tuple[Ident, ...] = ()) -> Watch:
    return Watch(Path(path), (0, 0), frozenset(idents), since)


def _recorded_es() -> list[str]:
    return (FIXTURES / "eslogger-step0.jsonl").read_text().splitlines(keepends=True)


def test_recorded_eslogger_open_and_readdir_are_root_accesses() -> None:
    found = observe.eslogger_accesses(_recorded_es(), [_w("/dev")])
    assert [(a.op, a.path, a.euid) for a in found] == [
        ("open", "/dev/fd", 0),
        ("readdir", "/dev/fd", 0),
    ]
    assert found[0].pid == 2304
    assert found[0].exe.endswith("/bin/python3.12")


def test_recorded_eslogger_identity_matches_without_path() -> None:
    """R98: the recorded `/dev/fd` stat (st_dev negative in ES) matches its
    identity under an unrelated source path."""
    ident = (-1192366862 & 0xFFFFFFFF, 333)
    found = observe.eslogger_accesses(_recorded_es(), [_w("/elsewhere", 0, (ident,))])
    assert [(a.op, a.path) for a in found] == [
        ("open", "/dev/fd"),
        ("readdir", "/dev/fd"),
    ]


def test_recorded_eslogger_exec_arguments_are_not_accesses() -> None:
    """The recorded exec of /bin/cat names the control file only in argv."""
    control = "/private/var/tmp-df/step0-2193/es-control"
    assert observe.eslogger_accesses(_recorded_es(), [_w(control)]) == []
    found = observe.eslogger_accesses(_recorded_es(), [_w("/bin")])
    assert [(a.op, a.path) for a in found] == [("exec", "/bin/cat")]


def _es_line(
    op: str,
    path: str,
    *,
    euid: int = 0,
    pid: int = 7,
    time: str | None = T,
    stat: tuple[int, int] | None = None,
    seq: int | None = None,
    truncated: bool = False,
) -> str:
    node: dict[str, object] = {"path": path, "path_truncated": truncated}
    if stat is not None:
        node["stat"] = {"st_dev": stat[0], "st_ino": stat[1]}
    where = {
        "open": {"file": node},
        "readdir": {"target": node},
        "exec": {"target": {"executable": node}, "args": [path]},
    }[op]
    event = {
        "event": {op: where},
        "process": {
            "audit_token": {"euid": euid, "pid": pid},
            "executable": {"path": "/bin/x"},
        },
        "time": time,
        "global_seq_num": next(_seq) if seq is None else seq,
    }
    return json.dumps(event, separators=(",", ":")).replace("/", "\\/") + "\n"


@pytest.mark.parametrize("op", ["open", "readdir", "exec"])
def test_eslogger_path_under_source_by_root(op: str) -> None:
    found = observe.eslogger_accesses([_es_line(op, "/r/src/a/b")], [_w("/r/src")])
    assert found == [Access(pid=7, euid=0, op=op, path="/r/src/a/b", exe="/bin/x")]


@pytest.mark.parametrize("op", ["open", "readdir", "exec"])
def test_eslogger_identity_outside_the_source(op: str) -> None:
    """R98 (F6, F4): a hardlink or a moved object keeps its identity."""
    line = _es_line(op, "/r/home/hardlink", stat=(5, 42))
    found = observe.eslogger_accesses([line], [_w("/r/src", 0, ((5, 42),))])
    assert [a.path for a in found] == ["/r/home/hardlink"]
    other = _es_line(op, "/r/home/hardlink", stat=(5, 43))
    assert observe.eslogger_accesses([other], [_w("/r/src", 0, ((5, 42),))]) == []


@pytest.mark.parametrize(
    ("line", "why"),
    [
        (_es_line("open", "/r/src/f", euid=501), "not root"),
        (_es_line("open", "/r/srcx/f"), "a sibling with the same prefix"),
        (_es_line("open", "/r/other"), "outside"),
        ("not json /r/src\n", "garbage"),
    ],
)
def test_eslogger_not_a_violation(line: str, why: str) -> None:
    assert observe.eslogger_accesses([line], [_w("/r/src")]) == [], why


def test_eslogger_watch_counts_from_its_time() -> None:
    """R93 (F7): events before `watch` (root creating the path) do not count."""
    since = observe._es_time("2026-10-25T11:38:51.500000000Z")
    early = _es_line("open", "/r/rel", time="2026-10-25T11:38:51.499999999Z")
    late = _es_line("open", "/r/rel", time="2026-10-25T11:38:51.500000001Z")
    found = observe.eslogger_accesses([early, late], [_w("/r/rel", since)])
    assert len(found) == 1


@pytest.mark.parametrize("time_value", [None, "garbage", "2026-10-25T11:38:51+00:00"])
def test_eslogger_unreadable_time_counts(time_value: str | None) -> None:
    """M1 (F8): an unreadable time counts under a mid-run watch."""
    line = _es_line("open", "/r/rel/f", time=time_value)
    found = observe.eslogger_accesses([line], [_w("/r/rel", 10**30)])
    assert [a.path for a in found] == ["/r/rel/f"]


def test_es_time() -> None:
    assert observe._es_time("1970-01-01T00:00:01.5Z") == 1_500_000_000
    assert observe._es_time("1970-01-01T00:00:02Z") == 2_000_000_000
    assert observe._es_time(None) == observe.UNKNOWN_TIME
    assert observe._es_time("garbage") == observe.UNKNOWN_TIME
    assert observe._es_time("2026-10-25T11:38:51+00:00") == observe.UNKNOWN_TIME
    assert observe._es_time("2026-10-25T11:38:51.x5Z") == observe.UNKNOWN_TIME


def test_eslogger_seq_gap_is_counted() -> None:
    """I1 (F1): a gap in `global_seq_num` anywhere, matched or not."""
    other, hit = "/elsewhere", "/r/src/f"
    whole = [_es_line("open", other, seq=0), _es_line("open", hit, seq=1)]
    assert observe.eslogger_scan(whole, [_w("/r/src")]).missing == 0
    gapped = [
        _es_line("open", other, seq=0),
        _es_line("open", other, seq=3),
        _es_line("open", hit, seq=4),
    ]
    scan = observe.eslogger_scan(gapped, [_w("/r/src")])
    assert scan.missing == 2
    assert len(scan.accesses) == 1
    assert scan.stats()["seq_missing"] == 2


def test_eslogger_line_without_seq_is_counted() -> None:
    """N3: a complete line without an integer `global_seq_num` is counted;
    a cut last line (no newline) is not."""
    event = json.loads(_es_line("open", "/elsewhere"))
    del event["global_seq_num"]
    no_seq = json.dumps(event) + "\n"
    as_text = _es_line("open", "/elsewhere").replace(
        '"global_seq_num":', '"global_seq_num":"'
    )
    scan = observe.eslogger_scan([no_seq, as_text, '{"cut'], [_w("/r/src")])
    assert scan.unnumbered == 2
    assert scan.stats()["lines_without_seq"] == 2


def test_eslogger_truncated_root_path_is_counted() -> None:
    line = _es_line("open", "/very/long", truncated=True)
    assert observe.eslogger_scan([line], [_w("/r/src")]).truncated == 1
    nonroot = _es_line("open", "/very/long", truncated=True, euid=501)
    assert observe.eslogger_scan([nonroot], [_w("/r/src")]).truncated == 0


def test_marker_is_an_exact_path_of_this_pid() -> None:
    """N4: the end marker is this pid's open of the exact marker path."""
    marker = "/r/ctl/marker-1"
    line = _es_line("open", marker, pid=42)
    assert observe.marker_seen([line], {marker}, 42)
    assert not observe.marker_seen([line], {marker}, 43)
    assert not observe.marker_seen([line], {"/r/ctl/marker-2"}, 42)
    nonroot = _es_line("open", marker, pid=42, euid=501)
    assert not observe.marker_seen([nonroot], {marker}, 42)


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
    assert next(a for a in found if a.pid == 3177).exe == "/usr/bin/cat"
    assert next(a for a in found if a.pid == 3173).exe == f"{PYTHON}/bin/python3.12"


def test_recorded_ausearch_identities() -> None:
    """R98: PATH `dev=08:01 inode=…` give the identity `lstat` gives."""
    raw = (FIXTURES / "ausearch-step0.txt").read_text()
    events = observe.audit_events(raw, STEP0_KEY)
    relative = next(e for e in events if e.access.path == "f")
    assert relative.idents == {(os.makedev(8, 1) & 0xFFFFFFFF, 9184318)}
    assert relative.serial == 149


def test_ausearch_other_key_and_rule_change_ignored() -> None:
    raw = (FIXTURES / "ausearch-step0.txt").read_text()
    assert observe.audit_accesses(raw, "df-r5-other") == []
    assert all(a.op != "44" for a in observe.audit_accesses(raw, STEP0_KEY))


def test_ausearch_create_names_parent_and_file() -> None:
    """A created marker: the PARENT record is not the access path, and every
    name is kept for the marker match."""
    raw = (
        "type=SYSCALL msg=audit(1.0:9): arch=c000003e syscall=257 success=yes "
        'pid=5 euid=0 comm="python" exe="/usr/bin/python3" key="k"\x1dARCH=x86_64\n'
        'type=PATH msg=audit(1.0:9): item=0 name="/r/ctl/" inode=7 dev=08:01 '
        "nametype=PARENT\n"
        'type=PATH msg=audit(1.0:9): item=1 name="/r/ctl/marker-1" inode=8 '
        "dev=08:01 nametype=CREATE\n"
    )
    [event] = observe.audit_events(raw, "k")
    assert event.access.path == "/r/ctl/marker-1"
    assert event.names == {"/r/ctl/", "/r/ctl/marker-1"}


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


@pytest.mark.parametrize(("number", "name"), [(322, "execveat"), (78, "getdents")])
def test_ausearch_added_syscalls_named(number: int, name: str) -> None:
    """N5c: execveat and getdents are in the rule and named."""
    assert name in observe.SYSCALLS.split(",")
    raw = (
        f"type=SYSCALL msg=audit(1.0:9): arch=c000003e syscall={number} "
        'success=yes pid=5 euid=0 exe="/x" key="k"\n'
    )
    assert observe.audit_accesses(raw, "k")[0].op == name


def test_audit_str() -> None:
    assert observe._audit_str('"x"') == "x"
    assert observe._audit_str("(null)") == ""
    assert observe._audit_str("2F78") == "/x"


def test_audit_lost_parsed() -> None:
    status = "enabled 1\nfailure 1\npid 712\nbacklog_limit 8192\nlost 7\n"
    assert observe.audit_lost(status) == 7
    assert observe.audit_lost("enabled 1\n") is None


def test_never_rules() -> None:
    """N5b: a host `never` rule masks events of the first match."""
    listing = (
        "-a never,exit -F arch=b64 -S openat -F exe=/bin/cat\n-a always,exit -k x\n"
    )
    assert observe.never_rules(listing) == [listing.splitlines()[0]]
    assert observe.never_rules("No rules\n") == []


def test_audit_lost_growth_is_unobserved(tmp_path: Path) -> None:
    """I1 (F1): records lost between start and finish -> `unobserved`."""
    entry = Watch(tmp_path, (0, 0), frozenset(), 0)
    backend = observe._Audit(entry, "auditctl", "ausearch", None)
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


# ---------- sources (identity, spelling, ancestor chain) ----------


@pytest.fixture()
def canon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A canonical tmp tree whose own levels stand in for root's chain: the
    policy checks each level below its top for this user and no group/other
    write."""
    top = Path(os.path.realpath(tmp_path))

    def policy(path: Path) -> list[str]:
        problems = []
        node = path
        while node != top.parent:
            st = os.lstat(node)
            if st.st_uid != os.getuid() or st.st_mode & 0o022:
                problems.append(f"{node}: не закреплён")
            node = node.parent
        return problems

    monkeypatch.setattr(observe, "trusted_problems", policy)
    os.chmod(top, 0o755)
    for name in ("src", "ctl"):
        (top / name).mkdir()
    (top / "src" / "f").write_text("x")
    return top


def test_source_passes(canon: Path) -> None:
    assert observe.source_problem(canon / "src") is None


def test_link_alias_refused(canon: Path) -> None:
    """I2 (F2): an alias through a link."""
    alias = canon / "alias"
    alias.symlink_to(canon / "src")
    assert "не канонический" in str(observe.source_problem(alias))
    assert "не канонический" in str(observe.source_problem(alias / "."))


def test_case_alias_refused(canon: Path) -> None:
    """N1: on a case-insensitive filesystem `src` reaches the on-disk `Src`."""
    (canon / "Upper").mkdir()
    if not (canon / "upper").exists():
        pytest.skip("файловая система различает регистр: псевдонима нет")
    assert "не канонический" in str(observe.source_problem(canon / "upper"))


def test_normalisation_alias_refused(canon: Path) -> None:
    """N1: an NFC spelling of an on-disk NFD name (APFS finds both)."""
    nfd = unicodedata.normalize("NFD", "café")
    nfc = unicodedata.normalize("NFC", "café")
    (canon / nfd).mkdir()
    if not (canon / nfc).exists():
        pytest.skip("файловая система различает нормализацию: псевдонима нет")
    if nfc in os.listdir(canon):
        pytest.skip("файловая система хранит имя в NFC")
    assert "не канонический" in str(observe.source_problem(canon / nfc))


def test_ancestor_writable_by_others_refused(canon: Path) -> None:
    """N2: the whole chain, not only the parent (here the grandparent)."""
    deep = canon / "mid" / "src"
    deep.mkdir(parents=True)
    os.chmod(canon / "mid", 0o755)
    assert observe.source_problem(deep) is None
    os.chmod(canon, 0o777)
    try:
        assert "не закреплена" in str(observe.source_problem(deep))
    finally:
        os.chmod(canon, 0o755)


def test_real_policy_refuses_a_user_chain(tmp_path: Path) -> None:
    """With the production policy (root's chain) a user's tmp is refused."""
    src = Path(os.path.realpath(tmp_path)) / "src"
    src.mkdir()
    assert "не закреплена" in str(observe.source_problem(src))


def test_source_must_be_a_directory(canon: Path) -> None:
    assert "не каталог" in str(observe.source_problem(canon / "src" / "f"))
    assert "не канонический" in str(observe.source_problem(canon / "absent"))


def test_enumeration_has_every_object(canon: Path) -> None:
    (canon / "src" / "d").mkdir()
    (canon / "src" / "d" / "g").write_text("y")
    (canon / "src" / "l").symlink_to("/etc")
    entry = observe.enumerate_source(canon / "src")
    names = ("", "f", "d", "d/g", "l")
    want = {observe.ident(os.lstat(canon / "src" / n)) for n in names}
    assert entry.idents == want
    assert entry.since == 0


def test_exit_refuses_a_replaced_source(canon: Path) -> None:
    """N2 at exit: a source renamed away and recreated is a different dir."""
    entry = observe.enumerate_source(canon / "src")
    assert observe.exit_problem([entry]) is None
    os.rename(canon / "src", canon / "moved")
    (canon / "src").mkdir()
    assert "подменён" in str(observe.exit_problem([entry]))


def test_exit_refuses_a_chain_that_lost_trust(canon: Path) -> None:
    entry = observe.enumerate_source(canon / "src")
    os.chmod(canon, 0o777)
    try:
        assert "перестал быть закреплён" in str(observe.exit_problem([entry]))
    finally:
        os.chmod(canon, 0o755)


# ---------- the context ----------


class _FakeBackend:
    def __init__(self, failure_at_start: str | None, ended: bool) -> None:
        self.failure_at_start = failure_at_start
        self.ended = ended
        self.watched: list[Watch] = []

    def start(self, watches: object) -> str | None:
        return self.failure_at_start

    def watch(self, entry: Watch) -> None:
        self.watched.append(entry)

    def finish(self) -> observe.Finish:
        hit = [Access(1, 0, "open", "/s/f", "/bin/cat")]
        failure = None if self.ended else "конец не подтверждён"
        return observe.Finish(hit, 2, failure, {"seq_count": 3})


@pytest.fixture()
def fake(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[_FakeBackend]]:
    made: list[_FakeBackend] = []

    monkeypatch.setattr(observe, "_backend", lambda _c, _w, _s: made[0])
    yield made


@pytest.mark.parametrize(
    ("start", "ended", "status"),
    [(None, True, "observed"), (None, False, "unobserved"), ("x", True, "unobserved")],
)
def test_status_follows_both_markers(
    fake: list[_FakeBackend], canon: Path, start: str | None, ended: bool, status: str
) -> None:
    fake.append(_FakeBackend(start, ended))
    record = canon / "rec" / "obs.json"
    with observe.observe_root_access(
        [canon / "src"], control=canon / "ctl", workdir=canon, record=record
    ) as obs:
        obs.watch(canon / "ctl")
    assert obs.status == status
    data = json.loads(record.read_text())
    assert data["limits"] == dict(observe.limits_here())
    if start is None:
        [entry] = fake[0].watched
        assert entry.path == canon / "ctl" and entry.since > 0
        assert obs.violations and obs.control_events == 2
        assert data["stats"] == {"seq_count": 3}


def test_source_replaced_during_observation_is_unobserved(
    fake: list[_FakeBackend], canon: Path
) -> None:
    fake.append(_FakeBackend(None, True))
    with observe.observe_root_access(
        [canon / "src"], control=canon / "ctl", workdir=canon
    ) as obs:
        os.rename(canon / "src", canon / "moved")
        (canon / "src").mkdir()
    assert obs.status == "unobserved"
    assert "подменён" in obs.reason


def test_child_alive_after_observation_is_unobserved(
    fake: list[_FakeBackend], canon: Path
) -> None:
    """N5a: a process started inside and still running could read unseen."""
    fake.append(_FakeBackend(None, True))
    with observe.observe_root_access(
        [canon / "src"], control=canon / "ctl", workdir=canon
    ) as obs:
        child = subprocess.Popen(["/bin/sleep", "30"])
    try:
        assert obs.status == "unobserved"
        assert str(child.pid) in obs.reason
    finally:
        child.kill()
        child.wait()


def test_missing_observer_is_unobserved(
    monkeypatch: pytest.MonkeyPatch, canon: Path
) -> None:
    monkeypatch.setattr(observe, "_backend", lambda control, workdir, scope: None)
    with observe.observe_root_access(
        [canon / "src"], control=canon / "ctl", workdir=canon
    ) as obs:
        obs.watch(canon / "src")
    assert obs.status == "unobserved"
    assert "нет наблюдателя" in obs.reason


def test_refusals_at_entry_and_watch(fake: list[_FakeBackend], canon: Path) -> None:
    fake.append(_FakeBackend(None, True))
    alias = canon / "alias"
    alias.symlink_to(canon / "src")
    with pytest.raises(ValueError, match="не канонический"):
        with observe.observe_root_access([alias], control=canon / "ctl", workdir=canon):
            pass
    with observe.observe_root_access(
        [canon / "src"], control=canon / "ctl", workdir=canon
    ) as obs:
        with pytest.raises(ValueError, match="не канонический"):
            obs.watch(alias)


def test_marker_file_is_unique_and_opened(canon: Path) -> None:
    first = observe.make_marker(canon / "ctl")
    second = observe.make_marker(canon / "ctl")
    assert first != second and first.parent == second.parent == canon / "ctl"
    assert first.is_file()


def _scope_event(serial: int, ident: Ident) -> observe.AuditEvent:
    access = Access(1, 0, "openat", "x", "/bin/x")
    return observe.AuditEvent(access, frozenset({"x"}), frozenset({ident}), serial)


def test_audit_identity_counts_after_its_watch_marker(tmp_path: Path) -> None:
    """R93 on Linux: root's own creation of a watched directory (before the
    watch marker, in kernel order) is not a violation; later access is."""
    entry = Watch(tmp_path, (0, 0), frozenset(), 0)
    backend = observe._Audit(entry, "auditctl", "ausearch", tmp_path)
    backend.idents = {(1, 1): None, (1, 2): "/c/marker-w"}
    serials = {"/c/marker-w": 50}
    assert backend._counts(_scope_event(10, (1, 1)), serials)
    assert not backend._counts(_scope_event(49, (1, 2)), serials)
    assert backend._counts(_scope_event(51, (1, 2)), serials)
    assert not backend._counts(_scope_event(60, (9, 9)), serials)
    assert backend.failure is None
    assert backend._counts(_scope_event(1, (1, 2)), {})
    assert "метка watch" in str(backend.failure)
