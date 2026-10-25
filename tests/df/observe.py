"""OS-level observer of root's accesses to the builder's objects (Task 13).

Spec 3.8 «Наблюдение» (rev 8 r5), pre-flight rulings R92-R94:

    with observe_root_access([src], control=ctl, workdir=w) as obs:
        ...                     # root code under test
        obs.watch(new_source)   # a source that appeared mid-run (R93)
    obs.status                  # "observed" | "unobserved"
    obs.violations              # root's opens/listings/execs under a source

- macOS: `eslogger open readdir exec --format json` (Endpoint Security),
  started detached (its parent's events are muted, CI run 40189716039); a
  violation is an event whose own path (open file, listed directory, exec
  target) is under a source and whose `audit_token.euid` is 0.
- Linux: one `auditctl` rule per source, `-a always,exit -F arch=b64 -S
  open,openat,openat2,getdents64,execve -F dir=<source> -F euid=0 -k <key>`,
  removed in `finally`; every event of a source's key is a violation (the
  rule already says "root, under the source"). `ausearch -k <key> --format
  raw` after the context. Step 0 measured: `getdents64` on a held directory
  descriptor matches no `dir=` rule (`LINUX_GAPS`).
- Positive control (R92): `control` is a directory with a file `f`; the
  observer's own process opens `control/f` as root on entry and again on exit
  and waits until the OS reports each open. Unseen -> "unobserved". Exactly
  these opens (this pid, under `control`) are dropped from `violations`.
  The exit marker also proves every earlier event was delivered.
- A missing tool, a refused start or an unseen control is "unobserved" with
  a `reason`, never an error: the test turns it into `pytest.xfail`.

Digests of a source are taken outside the context (R94): inside it, root's
reads of a source are exactly what this observer reports.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
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
    wait_for_control,
)

Status = Literal["observed", "unobserved"]
#: What the Linux rules cannot see (Step 0, CI run 40189716039): evidence.
LINUX_GAPS = ("getdents64 по удерживаемому дескриптору не наблюдается (Linux)",)
#: The file of the control directory the observer opens (R92).
CONTROL_FILE = "f"
#: Seconds to wait for the OS to report a control open.
CONTROL_TIMEOUT = 30.0
SYSCALLS = "open,openat,openat2,getdents64,execve"
_AUDIT_RECORD = re.compile(r"type=(\w+) msg=audit\([\d.]+:(\d+)\): (.*)")
_AUDIT_FIELD = re.compile(r"\b(\w+)=(\"[^\"]*\"|\S+)")
_HEX = re.compile(r"(?:[0-9A-F]{2})+")


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
    _watch: Callable[[Path], None] = field(default=lambda _p: None, repr=False)

    def watch(self, path: Path) -> None:
        """Observe `path` (an existing directory) from now on (R93)."""
        self._watch(path)


class _Backend(Protocol):
    def start(self, sources: Sequence[Path]) -> str | None: ...
    def watch(self, path: Path) -> None: ...
    def finish(self) -> tuple[list[Access], int, str | None]: ...


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
        if not path.is_dir():
            raise ValueError(f"источник наблюдения не каталог: {path}")
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
        accesses, controls, failure = backend.finish()
        obs.control_events = controls
        obs.violations = accesses
        obs.status = "observed" if failure is None else "unobserved"
        obs.reason = failure or ""
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
        "gaps": list(LINUX_GAPS) if sys.platform != "darwin" else [],
    }
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def touch_control(control: Path) -> None:
    """The control access: this (root) process opens `control/f`."""
    os.close(os.open(control / CONTROL_FILE, os.O_RDONLY))


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
        if not self._marker():
            _stop_detached(self.pid)
            unseen = f"eslogger: контроль не увиден за {CONTROL_TIMEOUT} с"
            return unseen + self._stderr()
        self.watches = [(str(p), 0) for p in sources]
        return None

    def _marker(self) -> bool:
        ways = {"control": (lambda: touch_control(self.control), _own_exe())}
        seen = wait_for_control(
            self.reader.new_lines, ways, self.control, timeout=CONTROL_TIMEOUT
        )
        seconds = seen["seconds"]
        return isinstance(seconds, dict) and seconds["control"] is not None

    def _stderr(self) -> str:
        try:
            text = self.err.read_text(errors="replace").strip()
        except OSError:
            return ""
        return f"; stderr: {text[-500:]}" if text else ""

    def watch(self, path: Path) -> None:
        self.watches.append((str(path), time.time_ns()))

    def finish(self) -> tuple[list[Access], int, str | None]:
        ended = self._marker()
        _stop_detached(self.pid)
        with self.raw.open("rb") as stream:
            lines = (b.decode("utf-8", "replace") for b in stream)
            accesses = eslogger_accesses(lines, [*self.watches, (str(self.control), 0)])
        controls = [a for a in accesses if _is_control(a, self.control)]
        violations = [a for a in accesses if not _is_control(a, self.control)]
        failure = None if ended else "eslogger: конец наблюдения не подтверждён"
        return violations, len(controls), failure


def _own_exe() -> str:
    return os.path.realpath(sys.executable)


def _is_control(access: Access, control: Path) -> bool:
    return access.pid == os.getpid() and _under(access.path, str(control))


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")


def eslogger_accesses(
    lines: Iterator[str] | Sequence[str], watches: Sequence[tuple[str, int]]
) -> list[Access]:
    """Root's accesses in eslogger JSON lines under any watched prefix.

    `watches` are `(prefix, since_ns)`: an event counts for a prefix only if
    its `time` is not earlier (R93). The event's own path is the open file,
    the listed directory or the exec target; exec arguments and the
    working directory are not accesses.
    """
    needles = [n for p, _ in watches for n in (p, p.replace("/", "\\/"))]
    found: list[Access] = []
    for line in lines:
        if not any(n in line for n in needles):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        access = _es_access(event)
        if access is None or access.euid != 0:
            continue
        when = _es_time(event.get("time"))
        if any(_under(access.path, p) and when >= since for p, since in watches):
            found.append(access)
    return found


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
                    exe=str(exe),
                )
    return None


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _dig(value: object, keys: Sequence[str]) -> object:
    for key in keys:
        value = _dict(value).get(key)
    return value


def _es_time(value: object) -> int:
    """`2026-10-25T11:38:51.018729555Z` -> ns since the epoch (0 if unknown)."""
    if not isinstance(value, str) or not value.endswith("Z"):
        return 0
    whole, _, frac = value[:-1].partition(".")
    try:
        base = datetime.strptime(whole, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return 0
    digits = (frac + "000000000")[:9] if frac.isdigit() else "0"
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

    def start(self, sources: Sequence[Path]) -> str | None:
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
        return [a for a in self._search(self.control_key) if a.pid == own]

    def _marker(self, before: int) -> bool:
        deadline = time.monotonic() + CONTROL_TIMEOUT
        while time.monotonic() < deadline:
            touch_control(self.control)
            time.sleep(0.5)
            if len(self._controls()) > before:
                return True
        return False

    def _search(self, key: str) -> list[Access]:
        done = _run([self.ausearch, "-k", key, "--format", "raw"])
        return audit_accesses(done.stdout, key)

    def finish(self) -> tuple[list[Access], int, str | None]:
        try:
            before = len(self._controls())
            ended = self._marker(before)
            violations = [a for k in self.keys for a in self._search(k)]
            controls = self._controls()
            others = [a for a in self._search(self.control_key) if a not in controls]
        finally:
            self._remove()
        failure = self.failure
        if failure is None and not ended:
            failure = "auditd: конец наблюдения не подтверждён"
        return violations + others, len(controls), failure

    def _remove(self) -> None:
        for rule in self.rules:
            _run([self.auditctl, "-d", *rule])
        self.rules = []


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv), capture_output=True, text=True, timeout=60, check=False
    )


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
