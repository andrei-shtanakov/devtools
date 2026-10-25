"""OS-level observer of root's accesses to the builder's objects (Task 13).

Spec 3.8 «Наблюдение» (rev 8 r5), pre-flight rulings R92-R94:

    with observe_root_access([src], control=ctl, workdir=w) as obs:
        ...                     # root code under test
        obs.watch(new_source)   # a source that appeared mid-run (R93)
    obs.status                  # "observed" | "unobserved"
    obs.violations              # root's opens/listings/execs under a source
    obs.limits                  # what this platform's observer cannot see

- macOS: `eslogger open readdir exec --format json` (Endpoint Security),
  started detached (its parent's events are muted, CI run 40189716039); a
  violation is an event whose own path (open file, listed directory, exec
  target) is under a source and whose `audit_token.euid` is 0. A gap in
  `global_seq_num` (ES dropped messages) makes the run "unobserved".
- Linux: one `auditctl` rule per source, `-a always,exit -F arch=b64 -S
  open,openat,openat2,getdents64,execve -F dir=<source> -F euid=0 -k <key>`,
  removed in `finally`; every event of a source's key is a violation (the
  rule already says "root, under the source"). `ausearch -k <key> --format
  raw` after the context. Growth of `lost` in `auditctl -s` (the kernel
  dropped records) makes the run "unobserved".
- Sources (`observe_root_access`, `watch`) must be canonical (their own
  realpath: ES reports resolved paths) and sit in a directory of root that
  group and others cannot write, so `df-build` cannot rename a source away;
  13-D watches the root-pinned directory, not a builder-renamable leaf.
  Otherwise `ValueError`.
- Positive control (R92): `control` is a directory with a file `f`; the
  observer's own process opens `control/f` as root on entry and again on exit
  and waits until the OS reports each open (this pid; macOS: not earlier than
  the wait). Unseen -> "unobserved". Exactly these opens are dropped from
  `violations`. The exit marker proves delivery up to it.
- A missing tool, a refused start, an unseen control, dropped events or a
  failed search are "unobserved" with a `reason`, never an error: the test
  turns it into `pytest.xfail`.
- `LIMITS` names, per platform, the forms this observer does not report
  (the form table of the Task 13-B report); twins in `test_platform_root.py`
  hold each entry.

Digests of a source are taken outside the context (R94): inside it, root's
reads of a source are exactly what this observer reports.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from tests.df.ci_step0 import (
    ESLOGGER,
    ESLOGGER_EVENTS,
    SBIN_PATH,
    X86_64_SYSCALLS,
    _LineReader,
    _start_detached,
    _stop_detached,
)

Status = Literal["observed", "unobserved"]
#: Owner a source's parent must have (tests substitute their own uid).
ROOT_UID = 0
#: The file of the control directory the observer opens (R92).
CONTROL_FILE = "f"
#: Seconds to wait for the OS to report a control open.
CONTROL_TIMEOUT = 30.0
SYSCALLS = "open,openat,openat2,getdents64,execve"

_COMMON_LIMITS: Mapping[str, str] = {
    "before-watch": "доступы до входа в наблюдение и до watch() не наблюдаются",
    "moved-out": (
        "объект, перенесённый df-build из источника в ненаблюдаемый каталог, "
        "дальше не прослеживается"
    ),
    "hardlink-out": (
        "open через жёсткую ссылку вне источников на файл источника "
        "не гарантированно сообщается"
    ),
    "held-fd-read": (
        "read/mmap через дескриптор, открытый до наблюдения, — не событие"
    ),
    "symlink-out": (
        "open через ссылку источника на объект вне источников сообщается "
        "путём цели — не нарушение"
    ),
}
#: What each platform's observer does not report (form table, Task 13-B).
LIMITS: Mapping[str, Mapping[str, str]] = {
    "darwin": {
        **_COMMON_LIMITS,
        "other-ops": (
            "наблюдаются только open, readdir, exec (stat/rename/unlink — нет)"
        ),
        "path-truncated": (
            "событие с усечённым путём (path_truncated) не сопоставляется"
        ),
    },
    "linux": {
        **_COMMON_LIMITS,
        "other-ops": (
            "наблюдаются только open, openat, openat2, getdents64, execve "
            "(stat/rename/unlink — нет)"
        ),
        "held-fd-getdents": (
            "getdents64 по удерживаемому дескриптору не наблюдается (Step 0)"
        ),
        "b32-io-uring": (
            "32-битные вызовы (arch=b32) и open через io_uring не наблюдаются"
        ),
    },
}
_AUDIT_RECORD = re.compile(r"type=(\w+) msg=audit\([\d.]+:(\d+)\): (.*)")
_AUDIT_FIELD = re.compile(r"\b(\w+)=(\"[^\"]*\"|\S+)")
_HEX = re.compile(r"(?:[0-9A-F]{2})+")
_SEQ = re.compile(r'"global_seq_num"\s*:\s*(\d+)')
_LOST = re.compile(r"^lost (\d+)$", re.MULTILINE)
_NO_WRITE_OTHERS = stat.S_IWGRP | stat.S_IWOTH
#: Time of an event that has none readable: before every cutoff is wrong
#: (it would hide the event), so it counts under every watch (M1).
UNKNOWN_TIME = -1


def limits_here() -> Mapping[str, str]:
    """The limits of this platform's observer."""
    return LIMITS["darwin" if sys.platform == "darwin" else "linux"]


@dataclass(frozen=True)
class Access:
    """One access of root reported by the OS."""

    pid: int
    euid: int
    op: str
    path: str
    exe: str


@dataclass
class Observation:
    """What the observer saw; `watch` adds a source that appeared mid-run."""

    status: Status = "unobserved"
    violations: list[Access] = field(default_factory=list)
    reason: str = ""
    control_events: int = 0
    limits: dict[str, str] = field(default_factory=lambda: dict(limits_here()))
    _watch: Callable[[Path], None] = field(default=lambda _p: None, repr=False)

    def watch(self, path: Path) -> None:
        """Observe `path` from now on (R93); refused like a source."""
        _require_source(path)
        self._watch(path)


@dataclass(frozen=True)
class Finish:
    """A backend's result: accesses, control opens and a failure reason."""

    violations: list[Access]
    controls: int
    failure: str | None


class _Backend(Protocol):
    def start(self, sources: Sequence[Path]) -> str | None: ...
    def watch(self, path: Path) -> None: ...
    def finish(self) -> Finish: ...


def source_problem(path: Path) -> str | None:
    """Why `path` cannot be observed (I2, I3); `None` when it can."""
    if os.path.realpath(path) != str(path):
        return f"источник не канонический: {path} (realpath {os.path.realpath(path)})"
    try:
        st = os.lstat(path)
        parent = os.lstat(path.parent)
    except OSError as exc:
        return f"источник недоступен: {exc}"
    if not stat.S_ISDIR(st.st_mode):
        return f"источник не каталог: {path}"
    if parent.st_uid != ROOT_UID or parent.st_mode & _NO_WRITE_OTHERS:
        return (
            f"родитель источника {path.parent} не закреплён root "
            f"(uid {parent.st_uid}, права {stat.S_IMODE(parent.st_mode):04o})"
        )
    return None


def _require_source(path: Path) -> None:
    problem = source_problem(path)
    if problem is not None:
        raise ValueError(problem)


@contextmanager
def observe_root_access(
    sources: Sequence[Path],
    *,
    control: Path,
    workdir: Path,
    record: Path | None = None,
) -> Iterator[Observation]:
    """Observe root's accesses under `sources` and `control` (module doc).

    `workdir` is a root-owned directory outside every source (eslogger's
    raw stream); `record`, if given, receives the observation as JSON.
    """
    for path in (*sources, control):
        _require_source(path)
    backend = _backend(control, workdir)
    obs = Observation()
    if backend is None:
        obs.reason = "нет наблюдателя ОС (eslogger/auditctl)"
        yield obs
        _record(obs, record)
        return
    failure = backend.start(sources)
    if failure is not None:
        obs.reason = failure
        yield obs
        _record(obs, record)
        return
    obs._watch = backend.watch
    try:
        yield obs
    finally:
        done = backend.finish()
        obs.control_events = done.controls
        obs.violations = done.violations
        obs.status = "observed" if done.failure is None else "unobserved"
        obs.reason = done.failure or ""
        _record(obs, record)


def _backend(control: Path, workdir: Path) -> _Backend | None:
    if sys.platform == "darwin":
        return _Eslogger(control, workdir) if ESLOGGER.exists() else None
    auditctl = shutil.which("auditctl", path=SBIN_PATH)
    ausearch = shutil.which("ausearch", path=SBIN_PATH)
    if auditctl is None or ausearch is None:
        return None
    return _Audit(control, auditctl, ausearch)


def _record(obs: Observation, record: Path | None) -> None:
    if record is None:
        return
    data = {
        "status": obs.status,
        "reason": obs.reason,
        "control_events": obs.control_events,
        "violations": [asdict(a) for a in obs.violations],
        "limits": obs.limits,
    }
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def touch_control(control: Path) -> None:
    """The control access: this (root) process opens `control/f`."""
    os.close(os.open(control / CONTROL_FILE, os.O_RDONLY))


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")


# ---------- macOS: eslogger ----------


class _Eslogger:
    """eslogger detached; the stream is parsed after the context."""

    def __init__(self, control: Path, workdir: Path) -> None:
        self.control = control
        self.raw = workdir / f"eslogger-{uuid.uuid4().hex[:8]}.jsonl"
        self.err = self.raw.with_suffix(".err")
        self.watches: list[tuple[str, int]] = []
        self.pid = 0
        self.reader = _LineReader(self.raw)

    def start(self, sources: Sequence[Path]) -> str | None:
        argv = [str(ESLOGGER), *ESLOGGER_EVENTS, "--format", "json"]
        try:
            self.pid = _start_detached(argv, self.raw, self.err)
        except (OSError, subprocess.SubprocessError) as exc:
            return f"eslogger не стартовал: {exc}"
        try:
            seen = self._marker()
        except BaseException:
            _stop_detached(self.pid)
            raise
        if not seen:
            _stop_detached(self.pid)
            unseen = f"eslogger: контроль не увиден за {CONTROL_TIMEOUT} с"
            return unseen + self._stderr()
        self.watches = [(str(p), 0) for p in sources]
        return None

    def _marker(self) -> bool:
        """Touch the control until this pid's open, not earlier than now, is
        in the stream (M3)."""
        since = time.time_ns()
        deadline = time.monotonic() + CONTROL_TIMEOUT
        while time.monotonic() < deadline:
            touch_control(self.control)
            time.sleep(0.5)
            if marker_seen(self.reader.new_lines(), self.control, os.getpid(), since):
                return True
        return False

    def _stderr(self) -> str:
        try:
            text = self.err.read_text(errors="replace").strip()
        except OSError:
            return ""
        return f"; stderr: {text[-500:]}" if text else ""

    def watch(self, path: Path) -> None:
        self.watches.append((str(path), time.time_ns()))

    def finish(self) -> Finish:
        try:
            ended = self._marker()
        finally:
            _stop_detached(self.pid)
        with self.raw.open("rb") as stream:
            lines = (b.decode("utf-8", "replace") for b in stream)
            scan = eslogger_scan(lines, [*self.watches, (str(self.control), 0)])
        controls = [a for a in scan.accesses if _is_control(a, self.control)]
        violations = [a for a in scan.accesses if not _is_control(a, self.control)]
        failure = None
        if scan.missing:
            failure = f"eslogger: пропущено событий {scan.missing} (global_seq_num)"
        elif not ended:
            failure = "eslogger: конец наблюдения не подтверждён"
        return Finish(violations, len(controls), failure)


def _is_control(access: Access, control: Path) -> bool:
    return access.pid == os.getpid() and _under(access.path, str(control))


def marker_seen(lines: Iterable[str], control: Path, pid: int, since: int) -> bool:
    """A root open of `control` by `pid` at or after `since` (ns) (M3); an
    event without a readable time is not a marker."""
    needle = str(control).replace("/", "\\/")
    for line in lines:
        if needle not in line and str(control) not in line:
            continue
        event = _json(line)
        access = _es_access(event)
        if access is None or access.euid != 0 or access.pid != pid:
            continue
        when = _es_time(event.get("time") if isinstance(event, dict) else None)
        if _under(access.path, str(control)) and when >= since:
            return True
    return False


@dataclass(frozen=True)
class EsScan:
    """Root's accesses under the watches, and how many messages ES dropped."""

    accesses: list[Access]
    missing: int


def eslogger_scan(lines: Iterable[str], watches: Sequence[tuple[str, int]]) -> EsScan:
    """One pass over eslogger JSON lines (module doc).

    `watches` are `(prefix, since_ns)`: an event counts for a prefix if its
    `time` is not earlier, or is unreadable (M1). The event's own path is the
    open file, the listed directory or the exec target; exec arguments and
    the working directory are not accesses. `missing` counts the numbers
    absent from `global_seq_num` between its smallest and largest value (I1).
    """
    needles = [n for p, _ in watches for n in (p, p.replace("/", "\\/"))]
    found: list[Access] = []
    seqs: set[int] = set()
    for line in lines:
        seq = _SEQ.search(line)
        if seq is not None:
            seqs.add(int(seq.group(1)))
        if not any(n in line for n in needles):
            continue
        event = _json(line)
        access = _es_access(event)
        if access is None or access.euid != 0:
            continue
        when = _es_time(event.get("time") if isinstance(event, dict) else None)
        if any(_counts(access.path, when, p, since) for p, since in watches):
            found.append(access)
    missing = max(seqs) - min(seqs) + 1 - len(seqs) if seqs else 0
    return EsScan(found, missing)


def eslogger_accesses(
    lines: Iterable[str], watches: Sequence[tuple[str, int]]
) -> list[Access]:
    """The accesses of `eslogger_scan`."""
    return eslogger_scan(lines, watches).accesses


def _counts(path: str, when: int, prefix: str, since: int) -> bool:
    return _under(path, prefix) and (when == UNKNOWN_TIME or when >= since)


def _json(line: str) -> object:
    try:
        return json.loads(line)
    except ValueError:
        return None


def _es_access(event: object) -> Access | None:
    if not isinstance(event, dict):
        return None
    body = event.get("event")
    process = event.get("process")
    if not isinstance(body, dict) or not isinstance(process, dict):
        return None
    token = _dict(process.get("audit_token"))
    exe = _dict(process.get("executable")).get("path")
    for op, keys in (
        ("open", ("file", "path")),
        ("readdir", ("target", "path")),
        ("exec", ("target", "executable", "path")),
    ):
        if op in body:
            path = _dig(body[op], keys)
            if isinstance(path, str):
                return Access(
                    pid=int(token.get("pid", -1)),
                    euid=int(token.get("euid", -1)),
                    op=op,
                    path=path,
                    exe=exe if isinstance(exe, str) else "",
                )
    return None


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _dig(value: object, keys: Sequence[str]) -> object:
    for key in keys:
        value = _dict(value).get(key)
    return value


def _es_time(value: object) -> int:
    """`2026-10-25T11:38:51.018729555Z` -> ns since the epoch;
    `UNKNOWN_TIME` when absent or unreadable."""
    if not isinstance(value, str) or not value.endswith("Z"):
        return UNKNOWN_TIME
    whole, _, frac = value[:-1].partition(".")
    try:
        base = datetime.strptime(whole, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return UNKNOWN_TIME
    if frac and not frac.isdigit():
        return UNKNOWN_TIME
    digits = (frac + "000000000")[:9]
    return int(base.timestamp()) * 1_000_000_000 + int(digits)


# ---------- Linux: auditd ----------


class _Audit:
    """One `-F dir=` rule per source; events by key after the context."""

    def __init__(self, control: Path, auditctl: str, ausearch: str) -> None:
        self.control = control
        self.auditctl = auditctl
        self.ausearch = ausearch
        self.token = uuid.uuid4().hex[:8]
        self.rules: list[list[str]] = []
        self.keys: list[str] = []
        self.control_key = f"df-r5-{self.token}-c"
        self.failure: str | None = None
        self.lost: int | None = None

    def _rule(self, path: Path, key: str) -> list[str]:
        return [
            "always,exit",
            "-F",
            "arch=b64",
            "-S",
            SYSCALLS,
            "-F",
            f"dir={path}",
            "-F",
            "euid=0",
            "-k",
            key,
        ]

    def _add(self, path: Path, key: str) -> str | None:
        rule = self._rule(path, key)
        done = _run([self.auditctl, "-a", *rule])
        if done.returncode != 0:
            return f"auditctl -a {path}: {done.stderr.strip()}"
        self.rules.append(rule)
        return None

    def _lost(self) -> int | None:
        return audit_lost(_run([self.auditctl, "-s"]).stdout)

    def start(self, sources: Sequence[Path]) -> str | None:
        self.lost = self._lost()
        failure = None if self.lost is not None else "auditctl -s: нет счётчика lost"
        if failure is None:
            failure = self._add(self.control, self.control_key)
        for i, path in enumerate(sources):
            if failure is None:
                self.keys.append(f"df-r5-{self.token}-{i}")
                failure = self._add(path, self.keys[-1])
        if failure is None and not self._marker(0):
            failure = f"auditd: контроль не увиден за {CONTROL_TIMEOUT} с"
        if failure is not None:
            self._remove()
        return failure

    def watch(self, path: Path) -> None:
        key = f"df-r5-{self.token}-{len(self.keys)}"
        self.keys.append(key)
        failure = self._add(path, key)
        if failure is not None and self.failure is None:
            self.failure = failure

    def _controls(self) -> list[Access]:
        own = os.getpid()
        found, _failure = self._search(self.control_key)
        return [a for a in found if a.pid == own]

    def _marker(self, before: int) -> bool:
        deadline = time.monotonic() + CONTROL_TIMEOUT
        while time.monotonic() < deadline:
            touch_control(self.control)
            time.sleep(0.5)
            if len(self._controls()) > before:
                return True
        return False

    def _search(self, key: str) -> tuple[list[Access], str | None]:
        done = _run([self.ausearch, "-k", key, "--format", "raw"])
        failure = ausearch_failure(done.returncode, done.stderr)
        return audit_accesses(done.stdout, key), failure

    def finish(self) -> Finish:
        try:
            ended = self._marker(len(self._controls()))
            found = [self._found(key) for key in self.keys]
            in_control = self._found(self.control_key)
            lost = self._lost()
        finally:
            self._remove()
        own = os.getpid()
        controls = [a for a in in_control if a.pid == own]
        violations = [a for group in found for a in group]
        violations += [a for a in in_control if a.pid != own]
        return Finish(violations, len(controls), self._verdict(lost, ended))

    def _found(self, key: str) -> list[Access]:
        found, failure = self._search(key)
        if failure is not None and self.failure is None:
            self.failure = failure
        return found

    def _verdict(self, lost: int | None, ended: bool) -> str | None:
        if self.failure is not None:
            return self.failure
        if lost is None or self.lost is None:
            return "auditctl -s: нет счётчика lost"
        if lost > self.lost:
            return f"auditd: потеряно записей {lost - self.lost} (lost)"
        return None if ended else "auditd: конец наблюдения не подтверждён"

    def _remove(self) -> None:
        for rule in self.rules:
            _run([self.auditctl, "-d", *rule])
        self.rules = []


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv), capture_output=True, text=True, timeout=60, check=False
    )


def audit_lost(status: str) -> int | None:
    """The `lost` counter of `auditctl -s` output; `None` if absent."""
    match = _LOST.search(status)
    return int(match.group(1)) if match else None


def ausearch_failure(returncode: int, stderr: str) -> str | None:
    """`None` for a search that worked (rc 0, or rc 1 with `<no matches>`)."""
    if returncode == 0 or (returncode == 1 and "<no matches>" in stderr):
        return None
    return f"ausearch: код {returncode}: {stderr.strip()[:300]}"


def audit_accesses(raw: str, key: str) -> list[Access]:
    """Accesses of `ausearch --format raw` whose SYSCALL record carries `key`.

    The path is the first PATH name of the event as the kernel recorded it
    (relative for `openat(dirfd, name)`); hex-encoded values are decoded.
    """
    events: dict[str, dict[str, object]] = {}
    for line in raw.splitlines():
        match = _AUDIT_RECORD.match(line.strip())
        if match is None:
            continue
        kind, serial, body = match.groups()
        fields = dict(_AUDIT_FIELD.findall(body.split("\x1d")[0]))
        event = events.setdefault(serial, {"names": []})
        if kind == "SYSCALL":
            event["syscall"] = fields
        elif kind == "PATH" and "name" in fields:
            names = event["names"]
            assert isinstance(names, list)
            names.append(_audit_str(fields["name"]))
    found: list[Access] = []
    for event in events.values():
        call = event.get("syscall")
        names = event["names"]
        if not isinstance(call, dict) or _audit_str(call.get("key", "")) != key:
            continue
        number = int(str(call.get("syscall", "-1")))
        assert isinstance(names, list)
        found.append(
            Access(
                pid=int(str(call.get("pid", "-1"))),
                euid=int(str(call.get("euid", "-1"))),
                op=X86_64_SYSCALLS.get(number, str(number)),
                path=str(names[0]) if names else "",
                exe=_audit_str(str(call.get("exe", ""))),
            )
        )
    return found


def _audit_str(value: str) -> str:
    """An audit string field: quoted text, `(null)`, or hex-encoded bytes."""
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value == "(null)":
        return ""
    if _HEX.fullmatch(value):
        return bytes.fromhex(value).decode("utf-8", "surrogateescape")
    return value
