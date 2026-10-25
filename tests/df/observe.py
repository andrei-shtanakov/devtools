"""OS-level observer of root's accesses to the builder's objects (Task 13).

Spec 3.8 «Наблюдение» (rev 8 r5), pre-flight rulings R92-R94, R98:

    with observe_root_access([src], control=ctl, workdir=w, scope=R) as obs:
        ...                     # root code under test
        obs.watch(new_source)   # a source that appeared mid-run (R93)
    obs.status                  # "observed" | "unobserved"
    obs.violations              # root's opens/listings/execs of a source object
    obs.limits                  # what this platform's observer cannot see

Matching (R98) is by identity and by path:
- identity: `(st_dev, st_ino)` of every object under each source, enumerated
  by `lstat` before the observation starts, plus the watched directory itself
  at `watch()` (only `lstat` inside the context: listing it would be a root
  access). An event whose own object has one of these identities is a
  violation wherever the OS reports it (macOS: anywhere; Linux: only under
  a watched directory, limit `identity-scope`); objects that were not
  enumerated are matched by path only (limit `created-moved-out`);
- path: the event path under a source's on-disk spelling (every component
  of a source must appear byte-exact in its parent's listing: case and
  Unicode-normalisation aliases are refused).

Sources must also keep a trusted ancestor chain (`plat.trusted_path_problems`
of the parent: root's, no group/other write, no ACL), so `df-build` cannot
rename a source or an ancestor away; the chain, the canonical spelling and
the source's identity are checked at entry (refusal: `ValueError`) and again
at exit (a change: "unobserved"; one undone in between is not seen, limit
`entry-exit-only`).

The guarantee: "observed" with no violations means no root (euid 0) open,
listing or exec of a source object, identified as above, from entry (or
`watch()`) to the end marker, with no dropped events; the observer sees
root's accesses to objects under the watched directories (on macOS also to
entry identities elsewhere), and only those, except the forms in `LIMITS`.

- macOS: `eslogger open readdir exec --format json` (Endpoint Security),
  started detached (its parent's events are muted, CI run 40189716039). The
  object of an event is the open file, the listed directory or the exec
  target, with its `stat`. Every line must carry an integer
  `global_seq_num`, contiguous over the stream; otherwise "unobserved".
- Linux: one `auditctl` rule per source and one for `scope` (a root-owned
  directory holding everything the test touches, for identity matches
  outside the sources), `-a always,exit -F arch=b64 -S <SYSCALLS> -F
  dir=<dir> -F euid=0 -k <key>`, removed in `finally`. Every event of a
  source's key is a violation; an event of the scope key is one when one of
  its PATH records has a source identity; an object outside every watched
  directory produces no record. A `never` or `exclude` rule on the host,
  or growth of `lost` in `auditctl -s`, makes the run "unobserved".
- Markers (R92, R98): the observer's own process creates a uniquely named
  file in `control` on entry and as the very last act on exit, and waits
  until the OS reports that exact path from this pid. The exit marker
  proves delivery of everything before it. Control events of this pid are
  dropped from `violations`.
- A root process this one started during the observation and still alive
  at its end makes the run "unobserved" (it could read a source unseen), and
  so does a `ps` that fails at either end; a process that detached from
  this one's tree is not seen (limit `detached-process`).
- Missing tools, a refused start, an unseen marker, dropped events, a
  failed search: "unobserved" with a `reason`, never an error; the test
  turns it into `pytest.xfail`.
- `LIMITS` names, per platform, the forms this observer does not report
  (the form table of the Task 13-B report); twins in `test_platform_root.py`
  hold the entries that can be exercised.

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

from df import platform as plat
from tests.df.ci_step0 import (
    ESLOGGER,
    ESLOGGER_EVENTS,
    SBIN_PATH,
    _LineReader,
    _start_detached,
    _stop_detached,
)

Status = Literal["observed", "unobserved"]
#: `(st_dev, st_ino)`, `st_dev` as an unsigned 32-bit number (ES signs it).
Ident = tuple[int, int]
#: Seconds to wait for the OS to report a marker.
CONTROL_TIMEOUT = 30.0
#: x86_64 numbers of the syscalls the Linux rule lists (N5c adds the
#: non-`open*`/`execve` ways to open, list and execute).
X86_64_SYSCALLS: Mapping[int, str] = {
    2: "open",
    59: "execve",
    78: "getdents",
    85: "creat",
    217: "getdents64",
    257: "openat",
    304: "open_by_handle_at",
    322: "execveat",
    437: "openat2",
}
SYSCALLS = ",".join(sorted(X86_64_SYSCALLS.values()))
#: The process listing of the surviving-child check (tests substitute it).
PS_ARGV: Sequence[str] = ("/bin/ps", "-A", "-o", "pid=,ppid=,stat=")
#: The trusted-path policy of an ancestor chain (tests substitute it).
trusted_problems: Callable[[Path], list[str]] = plat.trusted_path_problems

_COMMON_LIMITS: Mapping[str, str] = {
    "before-watch": "доступы до входа в наблюдение и до watch() не наблюдаются",
    "created-moved-out": (
        "по идентичности (inode) сопоставляются только объекты источников, "
        "перечисленные при входе, и сам каталог, добавленный через watch(); "
        "остальные — созданные в источнике после входа или после watch(), а "
        "также бывшие в каталоге watch() ещё до вызова watch() — "
        "сопоставляются только по пути: перенесённые df-build в каталог вне "
        "источников, они дальше не прослеживаются"
    ),
    "entry-exit-only": (
        "цепочка родителей, написание и идентичность источника проверяются "
        "на входе и на выходе; изменение, отменённое между двумя проверками, "
        "не видно"
    ),
    "detached-process": (
        "проверка переживших процессов идёт по дереву потомков этого "
        "процесса; процесс, отсоединившийся от него (двойной fork/setsid с "
        "переходом к init/launchd), и его доступы после конечной метки не "
        "видны"
    ),
    "held-fd-read": "read/mmap через дескриптор, открытый до наблюдения, — не событие",
    "symlink-out": (
        "open через ссылку источника на объект вне источников сообщается "
        "объектом цели — не нарушение"
    ),
}
#: What each platform's observer does not report (form table, Task 13-B).
LIMITS: Mapping[str, Mapping[str, str]] = {
    "darwin": {
        **_COMMON_LIMITS,
        "other-ops": (
            "наблюдаются только open, readdir, exec; stat/rename/unlink, "
            "clonefile/copyfile (копия содержимого без open) — нет"
        ),
        "es-default-mutes": (
            "процессы из набора приглушения ES по умолчанию (если eslogger его "
            "не снимает) не видны — не измерено"
        ),
    },
    "linux": {
        **_COMMON_LIMITS,
        "identity-scope": (
            "правила audit следят только за наблюдаемыми каталогами "
            "(источники, control, scope): видны доступы root к объектам под "
            "ними, и только к ним; объект источника, который df-build связал "
            "жёсткой ссылкой или перенёс за их пределы (например, в /var/tmp), "
            "root читает там незамеченным; без scope сопоставления по "
            "идентичности нет"
        ),
        "other-ops": (
            f"наблюдаются только {SYSCALLS}; stat/rename/unlink, sendfile/"
            "copy_file_range по чужому дескриптору — нет"
        ),
        "held-fd-getdents": "getdents по удерживаемому дескриптору не наблюдается",
        "b32-io-uring": (
            "32-битные вызовы (arch=b32) и open через io_uring не наблюдаются"
        ),
    },
}
_AUDIT_RECORD = re.compile(r"type=(\w+) msg=audit\((\d+)\.(\d+):(\d+)\): (.*)")
_AUDIT_FIELD = re.compile(r"\b(\w+)=(\"[^\"]*\"|\S+)")
_HEX = re.compile(r"(?:[0-9A-F]{2})+")
_SEQ = re.compile(r'"global_seq_num":(\d+)[,}]')
_LOST = re.compile(r"^lost (\d+)$", re.MULTILINE)
#: Time of an event that has none readable: it counts under every watch (M1).
UNKNOWN_TIME = -1


def limits_here() -> Mapping[str, str]:
    """The limits of this platform's observer."""
    return LIMITS["darwin" if sys.platform == "darwin" else "linux"]


def ident(st: os.stat_result) -> Ident:
    """The identity of an object as both backends report it."""
    return st.st_dev & 0xFFFFFFFF, st.st_ino


@dataclass(frozen=True)
class Access:
    """One access of root reported by the OS."""

    pid: int
    euid: int
    op: str
    path: str
    exe: str


@dataclass(frozen=True)
class Watch:
    """A source: its spelling, identities and since when it counts (ns)."""

    path: Path
    root: Ident
    idents: frozenset[Ident]
    since: int


@dataclass
class Observation:
    """What the observer saw; `watch` adds a source that appeared mid-run."""

    status: Status = "unobserved"
    violations: list[Access] = field(default_factory=list)
    reason: str = ""
    control_events: int = 0
    stats: dict[str, object] = field(default_factory=dict)
    limits: dict[str, str] = field(default_factory=lambda: dict(limits_here()))
    _watch: Callable[[Watch], None] = field(default=lambda _w: None, repr=False)
    _watches: list[Watch] = field(default_factory=list, repr=False)

    def watch(self, path: Path) -> None:
        """Observe `path` from now on (R93); refused like a source. Only
        `lstat` here: the directory itself is identified, its later content
        by path."""
        _require_source(path)
        st = os.lstat(path)
        entry = Watch(path, ident(st), frozenset({ident(st)}), time.time_ns())
        self._watches.append(entry)
        self._watch(entry)


@dataclass(frozen=True)
class Finish:
    """A backend's result: accesses, control events, a failure, figures."""

    violations: list[Access]
    controls: int
    failure: str | None
    stats: dict[str, object] = field(default_factory=dict)


class _Backend(Protocol):
    def start(self, watches: Sequence[Watch]) -> str | None: ...
    def watch(self, entry: Watch) -> None: ...
    def finish(self) -> Finish: ...


# ---------- sources ----------


def source_problem(path: Path) -> str | None:
    """Why `path` cannot be observed (I2, I3, N1, N2); `None` when it can."""
    if not path.is_absolute():
        return f"источник не абсолютный: {path}"
    spelling = _spelling_problem(path)
    if spelling is not None:
        return spelling
    try:
        st = os.lstat(path)
    except OSError as exc:
        return f"источник недоступен: {exc}"
    if not stat.S_ISDIR(st.st_mode):
        return f"источник не каталог: {path}"
    problems = trusted_problems(path.parent)
    if problems:
        return f"цепочка родителей источника не закреплена: {'; '.join(problems)}"
    return None


def _spelling_problem(path: Path) -> str | None:
    """Every component, byte-exact, in its parent's listing (no link, no
    case or normalisation alias); the listing is of the parent only."""
    current = Path("/")
    for part in path.parts[1:]:
        try:
            names = os.listdir(current)
        except OSError as exc:
            return f"источник недоступен: {current}: {exc}"
        if part not in names:
            return f"источник не канонический: {path} ({part!r} нет в {current})"
        current = current / part
        if os.path.islink(current):
            return f"источник не канонический: {current} — ссылка"
    return None


def _require_source(path: Path) -> None:
    problem = source_problem(path)
    if problem is not None:
        raise ValueError(problem)


def enumerate_source(path: Path) -> Watch:
    """A source at entry: the identities of every object below it (`lstat`
    of each name of a walk that follows no link), counting from the start."""
    root = ident(os.lstat(path))
    found = {root}
    for dirpath, dirs, files in os.walk(path, followlinks=False):
        for name in (*dirs, *files):
            found.add(ident(os.lstat(os.path.join(dirpath, name))))
    return Watch(path, root, frozenset(found), 0)


def exit_problem(watches: Sequence[Watch]) -> str | None:
    """N2: at exit every source is still the same directory under a trusted
    chain and the same spelling; `None` when all are."""
    for entry in watches:
        problem = source_problem(entry.path)
        if problem is None and ident(os.lstat(entry.path)) != entry.root:
            problem = f"{entry.path}: каталог подменён"
        if problem is not None:
            return f"источник перестал быть закреплён: {problem}"
    return None


# ---------- the context ----------


@contextmanager
def observe_root_access(
    sources: Sequence[Path],
    *,
    control: Path,
    workdir: Path,
    scope: Path | None = None,
    record: Path | None = None,
) -> Iterator[Observation]:
    """Observe root's accesses to `sources` (module doc).

    `control` is a directory for the markers (an extra source, R92);
    `workdir` a root-owned directory outside every source (eslogger's raw
    stream); `scope` (Linux) a trusted directory holding everything the
    test touches, watched for identity matches; `record`, if given,
    receives the observation as JSON.
    """
    for path in (*sources, control, *([scope] if scope else [])):
        _require_source(path)
    obs = Observation()
    obs._watches = [enumerate_source(p) for p in sources]
    control_watch = enumerate_source(control)
    backend = _backend(control_watch, workdir, scope)
    if backend is None:
        obs.reason = "нет наблюдателя ОС (eslogger/auditctl)"
        yield obs
        _record(obs, record)
        return
    failure = backend.start([*obs._watches])
    if failure is not None:
        obs.reason = failure
        yield obs
        _record(obs, record)
        return
    obs._watch = backend.watch
    before = descendants(os.getpid())
    try:
        yield obs
    finally:
        done = backend.finish()
        obs.control_events = done.controls
        obs.violations = done.violations
        obs.stats = done.stats
        failure = (
            done.failure
            or exit_problem([*obs._watches, control_watch])
            or _survivors(before)
        )
        obs.status = "observed" if failure is None else "unobserved"
        obs.reason = failure or ""
        _record(obs, record)


def _backend(control: Watch, workdir: Path, scope: Path | None) -> _Backend | None:
    if sys.platform == "darwin":
        return _Eslogger(control, workdir) if ESLOGGER.exists() else None
    auditctl = shutil.which("auditctl", path=SBIN_PATH)
    ausearch = shutil.which("ausearch", path=SBIN_PATH)
    if auditctl is None or ausearch is None:
        return None
    return _Audit(control, auditctl, ausearch, scope)


def _record(obs: Observation, record: Path | None) -> None:
    if record is None:
        return
    data = {
        "status": obs.status,
        "reason": obs.reason,
        "control_events": obs.control_events,
        "violations": [asdict(a) for a in obs.violations],
        "stats": obs.stats,
        "limits": obs.limits,
    }
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def make_marker(control: Path) -> Path:
    """A uniquely named file this (root) process creates in `control` and
    then opens again: the open of an existing file is the event both OSes
    report by its exact path."""
    path = control / f"marker-{uuid.uuid4().hex}"
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
    os.close(os.open(path, os.O_RDONLY))
    return path


def descendants(pid: int) -> set[int] | str:
    """Live descendants of `pid` (from `ps`; the `ps` itself excluded), or
    why `ps` gave no process tree (A5: a failed check is never a pass)."""
    try:
        done = subprocess.run(
            list(PS_ARGV), capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"ps не выполнен: {exc}"
    if done.returncode != 0:
        return f"ps: код {done.returncode}: {done.stderr.strip()[:300]}"
    children = process_tree(done.stdout)
    if children is None or not any(pid in kids for kids in children.values()):
        return f"ps: дерево процессов не прочитано (нет строки pid {pid})"
    found: set[int] = set()
    todo = [pid]
    while todo:
        for child in children.get(todo.pop(), []):
            if child not in found:
                found.add(child)
                todo.append(child)
    return found


def process_tree(listing: str) -> dict[int, list[int]] | None:
    """`ps -o pid=,ppid=,stat=` -> live children by parent pid (zombies
    left out); `None` when a line is not `pid ppid stat`."""
    children: dict[int, list[int]] = {}
    for line in listing.splitlines():
        fields = line.split()
        if not fields:
            continue
        if len(fields) != 3 or not (fields[0].isdigit() and fields[1].isdigit()):
            return None
        if not fields[2].startswith("Z"):
            children.setdefault(int(fields[1]), []).append(int(fields[0]))
    return children


def _survivors(before: set[int] | str) -> str | None:
    """N5a: a process this one started in the observation and still alive;
    a `ps` that failed at either end is the reason (A5)."""
    if isinstance(before, str):
        return before
    after = descendants(os.getpid())
    if isinstance(after, str):
        return after
    left = {p for p in after - before if _alive(p)}
    if left:
        return f"процессы, запущенные в наблюдении, живы после него: {sorted(left)}"
    return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")


# ---------- macOS: eslogger ----------


class _Eslogger:
    """eslogger detached; the stream is parsed after the context."""

    def __init__(self, control: Watch, workdir: Path) -> None:
        self.control = control
        self.raw = workdir / f"eslogger-{uuid.uuid4().hex[:8]}.jsonl"
        self.err = self.raw.with_suffix(".err")
        self.watches: list[Watch] = []
        self.pid = 0
        self.reader = _LineReader(self.raw)

    def start(self, watches: Sequence[Watch]) -> str | None:
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
            unseen = f"eslogger: метка не увидена за {CONTROL_TIMEOUT} с"
            return unseen + self._stderr()
        self.watches = list(watches)
        return None

    def _marker(self) -> bool:
        """Create a new marker until one of them is in the stream (N4)."""
        made: set[str] = set()
        deadline = time.monotonic() + CONTROL_TIMEOUT
        while time.monotonic() < deadline:
            made.add(str(make_marker(self.control.path)))
            time.sleep(0.5)
            if marker_seen(self.reader.new_lines(), made, os.getpid()):
                return True
        return False

    def _stderr(self) -> str:
        try:
            text = self.err.read_text(errors="replace").strip()
        except OSError:
            return ""
        return f"; stderr: {text[-500:]}" if text else ""

    def watch(self, entry: Watch) -> None:
        self.watches.append(entry)

    def finish(self) -> Finish:
        try:
            ended = self._marker()
        finally:
            _stop_detached(self.pid)
        with self.raw.open("rb") as stream:
            lines = (b.decode("utf-8", "replace") for b in stream)
            scan = eslogger_scan(lines, [*self.watches, self.control])
        own = os.getpid()
        control = str(self.control.path)
        controls = [
            a for a in scan.accesses if a.pid == own and _under(a.path, control)
        ]
        violations = [a for a in scan.accesses if a not in controls]
        failure = None
        if scan.unnumbered:
            failure = f"eslogger: строк без global_seq_num {scan.unnumbered}"
        elif scan.missing:
            failure = f"eslogger: пропущено событий {scan.missing} (global_seq_num)"
        elif scan.truncated:
            failure = f"eslogger: событий root с усечённым путём {scan.truncated}"
        elif not ended:
            failure = "eslogger: конец наблюдения не подтверждён"
        return Finish(violations, len(controls), failure, scan.stats())


def marker_seen(lines: Iterable[str], markers: set[str], pid: int) -> bool:
    """A root open of one of `markers` (exact paths) by `pid` (N4)."""
    for line in lines:
        if not any(m in line or m.replace("/", "\\/") in line for m in markers):
            continue
        access = _es_access(_json(line))
        if access is not None and access.euid == 0 and access.pid == pid:
            if access.path in markers:
                return True
    return False


@dataclass
class EsScan:
    """Root's accesses to the sources, and the integrity of the stream."""

    accesses: list[Access]
    seqs: list[int]
    unnumbered: int
    truncated: int

    @property
    def missing(self) -> int:
        """Numbers absent between the smallest and largest `global_seq_num`."""
        unique = set(self.seqs)
        return max(unique) - min(unique) + 1 - len(unique) if unique else 0

    def stats(self) -> dict[str, object]:
        return {
            "seq_first": min(self.seqs) if self.seqs else None,
            "seq_last": max(self.seqs) if self.seqs else None,
            "seq_count": len(self.seqs),
            "seq_missing": self.missing,
            "lines_without_seq": self.unnumbered,
            "root_truncated_paths": self.truncated,
        }


def eslogger_scan(lines: Iterable[str], watches: Sequence[Watch]) -> EsScan:
    """One pass over eslogger JSON lines (module doc).

    An event is root's access to a source when its object's identity is a
    source identity, or its path is under a source spelling, and its time is
    not earlier than the source's `since` (an unreadable time counts, M1).
    Every complete line must carry `global_seq_num` (N3); a root event with a
    truncated path is counted (it cannot be matched by path).
    """
    needles = [
        n for w in watches for n in (str(w.path), str(w.path).replace("/", "\\/"))
    ]
    inodes = {str(i) for w in watches for _d, i in w.idents}
    scan = EsScan([], [], 0, 0)
    for line in lines:
        if not line.endswith("\n"):
            continue  # the last line of a stopped stream may be cut
        seq = _SEQ.search(line)
        if seq is None:
            scan.unnumbered += 1
            continue
        scan.seqs.append(int(seq.group(1)))
        if not (any(n in line for n in needles) or _has_inode(line, inodes)):
            if '"path_truncated":true' not in line:
                continue
        event = _json(line)
        obj = _es_object(event)
        if obj is None or obj.access.euid != 0:
            continue
        if obj.truncated:
            scan.truncated += 1
        when = _es_time(event.get("time") if isinstance(event, dict) else None)
        if any(_matches(obj, when, w) for w in watches):
            scan.accesses.append(obj.access)
    return scan


def eslogger_accesses(lines: Iterable[str], watches: Sequence[Watch]) -> list[Access]:
    """The accesses of `eslogger_scan`."""
    return eslogger_scan(lines, watches).accesses


_INODE = re.compile(r'"st_ino":(\d+)')


def _has_inode(line: str, inodes: set[str]) -> bool:
    return any(m.group(1) in inodes for m in _INODE.finditer(line))


def _matches(obj: EsObject, when: int, entry: Watch) -> bool:
    if when != UNKNOWN_TIME and when < entry.since:
        return False
    if obj.ident is not None and obj.ident in entry.idents:
        return True
    return _under(obj.access.path, str(entry.path))


@dataclass(frozen=True)
class EsObject:
    """The object of an ES event: who acted, its path and identity."""

    access: Access
    ident: Ident | None
    truncated: bool


def _json(line: str) -> object:
    try:
        return json.loads(line)
    except ValueError:
        return None


def _es_access(event: object) -> Access | None:
    obj = _es_object(event)
    return None if obj is None else obj.access


def _es_object(event: object) -> EsObject | None:
    if not isinstance(event, dict):
        return None
    body = event.get("event")
    process = event.get("process")
    if not isinstance(body, dict) or not isinstance(process, dict):
        return None
    token = _dict(process.get("audit_token"))
    exe = _dict(process.get("executable")).get("path")
    for op, keys in (
        ("open", ("file",)),
        ("readdir", ("target",)),
        ("exec", ("target", "executable")),
    ):
        if op not in body:
            continue
        node = _dict(_dig(body[op], keys))
        path = node.get("path")
        if not isinstance(path, str):
            return None
        access = Access(
            pid=int(str(token.get("pid", -1))),
            euid=int(str(token.get("euid", -1))),
            op=op,
            path=path,
            exe=exe if isinstance(exe, str) else "",
        )
        st = _dict(node.get("stat"))
        dev, ino = st.get("st_dev"), st.get("st_ino")
        found = (
            (int(dev) & 0xFFFFFFFF, int(ino))
            if isinstance(dev, int) and isinstance(ino, int)
            else None
        )
        return EsObject(access, found, node.get("path_truncated") is True)
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
    """`-F dir=` rules per source and scope; events by key after the context."""

    def __init__(
        self, control: Watch, auditctl: str, ausearch: str, scope: Path | None
    ) -> None:
        self.control = control
        self.auditctl = auditctl
        self.ausearch = ausearch
        self.scope = scope
        self.token = uuid.uuid4().hex[:8]
        self.rules: list[list[str]] = []
        self.keys: list[str] = []
        #: identity -> the marker after which it counts (`None`: from entry)
        self.idents: dict[Ident, str | None] = {}
        self.control_key = f"df-r5-{self.token}-c"
        self.scope_key = f"df-r5-{self.token}-s"
        self.failure: str | None = None
        self.lost: int | None = None

    def _rule(self, path: Path, key: str) -> list[str]:
        head = ["always,exit", "-F", "arch=b64", "-S", SYSCALLS]
        return [*head, "-F", f"dir={path}", "-F", "euid=0", "-k", key]

    def _add(self, path: Path, key: str) -> str | None:
        rule = self._rule(path, key)
        done = _run([self.auditctl, "-a", *rule])
        if done.returncode != 0:
            return f"auditctl -a {path}: {done.stderr.strip()}"
        self.rules.append(rule)
        return None

    def _lost(self) -> int | None:
        return audit_lost(_run([self.auditctl, "-s"]).stdout)

    def start(self, watches: Sequence[Watch]) -> str | None:
        try:
            failure = self._start(watches)
        except BaseException:
            self._remove()
            raise
        if failure is not None:
            self._remove()
        return failure

    def _start(self, watches: Sequence[Watch]) -> str | None:
        suppressing = suppressing_rules(_run([self.auditctl, "-l"]).stdout)
        if suppressing:
            return f"auditctl: правила never/exclude на хосте: {suppressing}"
        self.lost = self._lost()
        if self.lost is None:
            return "auditctl -s: нет счётчика lost"
        failure = self._add(self.control.path, self.control_key)
        if failure is None and self.scope is not None:
            failure = self._add(self.scope, self.scope_key)
        for entry in watches:
            if failure is None:
                failure = self._watch(entry, None)
        if failure is None and not self._marker():
            failure = f"auditd: метка не увидена за {CONTROL_TIMEOUT} с"
        return failure

    def _watch(self, entry: Watch, marker: str | None) -> str | None:
        self.keys.append(f"df-r5-{self.token}-{len(self.keys)}")
        for found in entry.idents:
            self.idents.setdefault(found, marker)
        return self._add(entry.path, self.keys[-1])

    def watch(self, entry: Watch) -> None:
        """A mid-run source: its rule, then a marker; scope events of its
        identity count only after that marker (kernel serial order)."""
        key = f"df-r5-{self.token}-{len(self.keys)}"
        self.keys.append(key)
        failure = self._add(entry.path, key)
        marker = str(make_marker(self.control.path))
        for found in entry.idents:
            self.idents.setdefault(found, marker)
        if failure is not None and self.failure is None:
            self.failure = failure

    def _marker(self) -> bool:
        """Create a new marker until one of them is logged (N4)."""
        made: set[str] = set()
        deadline = time.monotonic() + CONTROL_TIMEOUT
        own = os.getpid()
        while time.monotonic() < deadline:
            made.add(str(make_marker(self.control.path)))
            time.sleep(0.5)
            found = self._found(self.control_key)
            if self.failure is not None:
                return False
            if any(e.access.pid == own and e.names & made for e in found):
                return True
        return False

    def _found(self, key: str) -> list[AuditEvent]:
        done = _run([self.ausearch, "-k", key, "--format", "raw"])
        failure = ausearch_failure(done.returncode, done.stderr)
        if failure is not None and self.failure is None:
            self.failure = failure
        return audit_events(done.stdout, key)

    def finish(self) -> Finish:
        try:
            ended = self._marker()
            found = [e.access for k in self.keys for e in self._found(k)]
            in_control = self._found(self.control_key)
            scoped = self._found(self.scope_key) if self.scope is not None else []
            lost = self._lost()
        finally:
            self._remove()
        own = os.getpid()
        serials = {n: e.serial for e in in_control for n in e.names}
        controls = [e.access for e in in_control if e.access.pid == own]
        others = [e.access for e in in_control if e.access.pid != own]
        by_ident = [e.access for e in scoped if self._counts(e, serials)]
        violations = _unique([*found, *by_ident, *others])
        stats = {"lost_start": self.lost, "lost_end": lost}
        return Finish(violations, len(controls), self._verdict(lost, ended), stats)

    def _counts(self, event: AuditEvent, serials: Mapping[str, int]) -> bool:
        """A scope event of a source identity, after that identity's marker."""
        for found in event.idents & self.idents.keys():
            marker = self.idents[found]
            if marker is None:
                return True
            if marker not in serials:
                self.failure = (
                    self.failure or f"auditd: метка watch не найдена {marker}"
                )
                return True
            if event.serial > serials[marker]:
                return True
        return False

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


def _unique(accesses: Iterable[Access]) -> list[Access]:
    return list(dict.fromkeys(accesses))


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv), capture_output=True, text=True, timeout=60, check=False
    )


#: Rule actions/lists of `auditctl -l` that drop events (N5b, A3).
SUPPRESSING = frozenset({"never", "exclude"})


def suppressing_rules(listing: str) -> list[str]:
    """Rules of `auditctl -l` that suppress events: a `never` action or the
    `exclude` list, in either order (`-a never,exit`, `-a always,exclude`)."""
    return [
        line
        for line in listing.splitlines()
        if SUPPRESSING & set(",".join(line.split()[:2]).split(","))
    ]


def audit_lost(status: str) -> int | None:
    """The `lost` counter of `auditctl -s` output; `None` if absent."""
    match = _LOST.search(status)
    return int(match.group(1)) if match else None


def ausearch_failure(returncode: int, stderr: str) -> str | None:
    """`None` for a search that worked (rc 0, or rc 1 with `<no matches>`)."""
    if returncode == 0 or (returncode == 1 and "<no matches>" in stderr):
        return None
    return f"ausearch: код {returncode}: {stderr.strip()[:300]}"


@dataclass(frozen=True)
class AuditEvent:
    """One audited syscall: the access, every PATH name and identity, and
    its serial (kernel order)."""

    access: Access
    names: frozenset[str]
    idents: frozenset[Ident]
    serial: int


def audit_accesses(raw: str, key: str) -> list[Access]:
    """The accesses of `audit_events`."""
    return [e.access for e in audit_events(raw, key)]


def audit_events(raw: str, key: str) -> list[AuditEvent]:
    """Events of `ausearch --format raw` whose SYSCALL record carries `key`:
    the access (path: the first non-PARENT name, as the kernel recorded it,
    relative for `openat(dirfd, name)`), every PATH name and every PATH
    identity (`dev=MM:mm`, `inode=`). Hex-encoded values are decoded."""
    events: dict[str, dict[str, object]] = {}
    for line in raw.splitlines():
        match = _AUDIT_RECORD.match(line.strip())
        if match is None:
            continue
        kind, _sec, _ms, serial, body = match.groups()
        fields = dict(_AUDIT_FIELD.findall(body.split("\x1d")[0]))
        event = events.setdefault(serial, {"paths": []})
        if kind == "SYSCALL":
            event["syscall"] = fields
        elif kind == "PATH" and "name" in fields:
            paths = event["paths"]
            assert isinstance(paths, list)
            paths.append(fields)
    found: list[AuditEvent] = []
    for serial, event in events.items():
        call = event.get("syscall")
        paths = event["paths"]
        assert isinstance(paths, list)
        if not isinstance(call, dict) or _audit_str(call.get("key", "")) != key:
            continue
        names = [_audit_str(p["name"]) for p in paths]
        own = [_audit_str(p["name"]) for p in paths if p.get("nametype") != "PARENT"]
        number = int(str(call.get("syscall", "-1")))
        access = Access(
            pid=int(str(call.get("pid", "-1"))),
            euid=int(str(call.get("euid", "-1"))),
            op=X86_64_SYSCALLS.get(number, str(number)),
            path=(own or names or [""])[0],
            exe=_audit_str(str(call.get("exe", ""))),
        )
        idents = frozenset(i for p in paths if (i := _path_ident(p)) is not None)
        found.append(AuditEvent(access, frozenset(names), idents, int(serial)))
    return found


def _path_ident(fields: Mapping[str, str]) -> Ident | None:
    """`dev=08:01 inode=9184318` -> `(makedev(8, 1), 9184318)`."""
    dev, inode = fields.get("dev", ""), fields.get("inode", "")
    major, sep, minor = dev.partition(":")
    if not sep or not inode.isdigit():
        return None
    try:
        number = os.makedev(int(major, 16), int(minor, 16))
    except ValueError:
        return None
    return number & 0xFFFFFFFF, int(inode)


def _audit_str(value: str) -> str:
    """An audit string field: quoted text, `(null)`, or hex-encoded bytes."""
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value == "(null)":
        return ""
    if _HEX.fullmatch(value):
        return bytes.fromhex(value).decode("utf-8", "surrogateescape")
    return value
