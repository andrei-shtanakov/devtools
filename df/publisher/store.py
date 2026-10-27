"""The publisher's durable state under `/var/lib/df/publisher` (spec 5.6).

`ids/<request_id>` binds an id to `(sender, operation, digest)` for the whole
installation; `keys/<sha256(sender|op|digest)>.lock` serialises identical
requests; `intents/<key>.json` is written durably before an external action
and `results/<key>.json` after a final outcome; `journal.jsonl` has one line
per request and never a secret.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import stat
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from df import fsutil
from df.paths import Layout
from df.publisher import request
from df.publisher.request import FINAL_OUTCOMES, Refused, Request, Response

IDS_LOCK = "ids.lock"
JOURNAL = "journal.jsonl"
SUBDIRS = ("ids", "keys", "intents", "results")
#: What a replayed request gets back is a stored result; these three are
#: computed (issued) anew at every call (spec 5.6).
NOT_REPLAYED = frozenset({"perimeter-status", "read-token", request.SELF_CHECK})
#: Response keys whose value is never written to the journal.
SECRET_KEYS = frozenset({"token"})
REDACTED = "<redacted>"
_CANONICAL = re.compile(r"0|[1-9][0-9]{0,18}", re.ASCII)
_MAX_RECORD = 4096
#: Intents and results carry a request's content (a review body): one cap.
MAX_STATE = 1 << 20
#: How long a lock may be waited for before `refused: busy` (no intent yet).
LOCK_WAIT = 30.0
_LOCK_POLL = 0.02
_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


@dataclass
class Reservation:
    """A reserved id with the idempotency key's lock held until `close`."""

    layout: Layout
    sender: int
    operation: str
    request_id: str
    digest: str
    key: str
    _fd: int | None

    def close(self) -> None:
        """Release the key's lock (idempotent)."""
        fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)

    def __enter__(self) -> Reservation:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def intent_path(self) -> Path:
        """`intents/<key>.json`."""
        return self.layout.publisher_dir / "intents" / f"{self.key}.json"

    @property
    def result_path(self) -> Path:
        """`results/<key>.json`."""
        return self.layout.publisher_dir / "results" / f"{self.key}.json"


def idempotency_key(sender: int, op: str, digest: str) -> str:
    """`sha256(sender|op|digest)`: the file stem of the key's lock, intent and
    result."""
    return hashlib.sha256(f"{sender}|{op}|{digest}".encode("ascii")).hexdigest()


def reserve(layout: Layout, sender_uid: int, op: str, req: Request) -> Reservation:
    """Reserve `req.request_id` for `(sender_uid, op, digest)`, spec 5.6.

    Under the global lock `ids.lock`: sweep dead processes' temporary files
    (under the lock a temporary file can only be a dead process's), write
    `{sender, operation, digest}` to `ids/.<id>.<pid>.tmp` durably, `link` it
    to `ids/<id>` (`EEXIST`: the binding must be the same, else `refused:
    request_id_reused`), sync the directory, remove the own temporary file;
    then release the global lock and take the key's lock. The key's lock is
    held in the returned `Reservation`."""
    base = layout.publisher_dir
    _ensure_dirs(base)
    digest = request.digest(req)
    binding = {"sender": sender_uid, "operation": op, "digest": digest}
    gfd = _lock_file(base / IDS_LOCK)
    try:
        _sweep_temps(base / "ids")
        _bind(base / "ids", req.request_id, binding)
    finally:
        _unlock(gfd)
    key = idempotency_key(sender_uid, op, digest)
    kfd = _lock_file(base / "keys" / f"{key}.lock")
    return Reservation(layout, sender_uid, op, req.request_id, digest, key, kfd)


def _ensure_dirs(base: Path) -> None:
    parent = os.open(base, _DIR)
    try:
        for name in SUBDIRS:
            _subdir(parent, name, base / name)
    finally:
        os.close(parent)


def _subdir(parent: int, name: str, path: Path) -> None:
    """`name` is a real directory of ours, `0700` (made durably if absent)."""
    try:
        fd = os.open(name, _DIR, dir_fd=parent)
    except FileNotFoundError:
        fsutil.make_dir(parent, name, 0o700, path=path)
        fd = os.open(name, _DIR, dir_fd=parent)
    try:
        st = os.fstat(fd)
    finally:
        os.close(fd)
    if stat.S_IMODE(st.st_mode) != 0o700 or fsutil.owner_uid(st) != os.geteuid():
        raise OSError(f"{path}: not a 0700 directory of this user")


def _regular(fd: int, path: Path) -> os.stat_result:
    """`fd` is a regular file with one link (nothing planted next to it)."""
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        raise OSError(f"{path}: not a regular file with one link")
    return st


def _lock_file(path: Path) -> int:
    """Create (born `0600`, durable) or open `path` and take an exclusive
    `flock`, waiting at most `LOCK_WAIT` seconds (`Refused("busy")`); the
    descriptor is returned."""
    flags = os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        fd = fsutil.create_file(path, flags, 0o600)
    except FileExistsError:
        fd = os.open(path, _READ)
    try:
        _regular(fd, path)
        _flock(fd)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _flock(fd: int) -> None:
    deadline = time.monotonic() + LOCK_WAIT
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise Refused("busy") from None
            time.sleep(_LOCK_POLL)


def _unlock(fd: int) -> None:
    os.close(fd)  # closing drops the flock


def _sweep_temps(ids: Path) -> None:
    """Remove every `ids/.*.tmp` (the caller holds the global lock)."""
    stale = [e.name for e in os.scandir(ids) if _is_temp(e.name)]
    for name in stale:
        with contextlib.suppress(FileNotFoundError):
            fsutil.remove_durably(ids / name)


def _is_temp(name: str) -> bool:
    return name.startswith(".") and name.endswith(".tmp")


def _before_link(tmp: Path) -> None:
    """The window "temporary file durable, not yet linked"; tests replace it."""


def _bind(ids: Path, request_id: str, binding: Mapping[str, object]) -> None:
    final = ids / request_id
    tmp = ids / f".{request_id}.{os.getpid()}.tmp"
    text = json.dumps(binding, sort_keys=True, separators=(",", ":"))
    fsutil.write_atomic(tmp, text, 0o600)
    try:
        _before_link(tmp)
        try:
            os.link(tmp, final)
        except FileExistsError:
            _same_binding(final, binding)
        fsutil.sync_dir(ids)
    finally:
        with contextlib.suppress(FileNotFoundError):
            fsutil.remove_durably(tmp)


def _same_binding(final: Path, binding: Mapping[str, object]) -> None:
    """The whole binding `(sender, operation, digest)` must be the same."""
    try:
        found = json.loads(_read(final))
    except (OSError, ValueError):
        found = None
    if found != dict(binding):
        raise Refused("request_id_reused")


def _read(path: Path, limit: int = _MAX_RECORD) -> bytes:
    fd = os.open(path, _READ)
    try:
        _regular(fd, path)
        data = os.read(fd, limit + 1)
    finally:
        os.close(fd)
    if len(data) > limit:
        raise OSError(f"{path}: too long")
    return data


# --- intent and result --------------------------------------------------------


def intent(res: Reservation, payload: Mapping[str, object]) -> None:
    """Record, durably, what is about to be done outside (before doing it)."""
    record = {"request_id": res.request_id, "payload": dict(payload)}
    _write_state(res.intent_path, json.dumps(record, sort_keys=True))


def read_intent(res: Reservation) -> Mapping[str, object] | None:
    """The payload of an earlier attempt's intent, if there is one."""
    try:
        record = json.loads(_read(res.intent_path, MAX_STATE))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise OSError(f"{res.intent_path}: unreadable intent: {exc}") from exc
    payload = record.get("payload") if isinstance(record, dict) else None
    if not isinstance(payload, dict):
        raise OSError(f"{res.intent_path}: not an intent record")
    return payload


def result(res: Reservation, response: Response) -> bool:
    """Keep a final outcome (`done`, `refused`) for repeats of the request.

    `unknown` (and `stale_snapshot`) are never kept: a repeat goes through
    the reconciliation again and an external action is never repeated blind
    (r2 N4). Returns whether it was kept."""
    if response.outcome not in FINAL_OUTCOMES:
        return False
    _write_state(res.result_path, request.dump_response(response))
    return True


def _write_state(path: Path, text: str) -> None:
    if len(text) > MAX_STATE:  # the JSON is ASCII: characters are bytes
        raise ValueError(f"{path.name}: more than {MAX_STATE} bytes")
    fsutil.write_atomic(path, text, 0o600)


def has_state(res: Reservation) -> bool:
    """An intent or a result of this request exists (a link or a damaged
    file counts): an external action may have been done."""
    return any(os.path.lexists(p) for p in (res.intent_path, res.result_path))


def replay(res: Reservation) -> Response | None:
    """The kept result of an identical earlier request, if there is one and
    the operation is replayed at all."""
    if res.operation in NOT_REPLAYED:
        return None
    try:
        raw = _read(res.result_path, MAX_STATE)
    except FileNotFoundError:
        return None
    return request.parse_response(raw)


# --- journal ------------------------------------------------------------------


def redact(value: object) -> object:
    """`value` with every `SECRET_KEYS` entry (at any depth) replaced."""
    if isinstance(value, Mapping):
        return {
            k: REDACTED if k in SECRET_KEYS else redact(v) for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def journal_line(
    sender: int,
    op: str,
    req: Request | None,
    digest: str | None,
    response: Response,
) -> str:
    """One journal line: when, who, what, the ids, the digest, the outcome.
    The nonce is not in `response` here; secrets are redacted."""
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "sender": sender,
        "operation": op,
        "request_id": None if req is None else req.request_id,
        "run_id": None if req is None else req.run_id,
        "digest": digest,
        "outcome": response.outcome,
        "reason": response.reason,
        "result": redact(response.result),
    }
    return json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"


def refusal_line(op: str, sudo_uid: str | None, reason: str) -> str:
    """The journal line of a refused sender: no sender, the raw `SUDO_UID` only
    if canonical (else a mask: never an arbitrary string), nothing of a
    request."""
    shown = None if sudo_uid is None else sudo_uid
    if shown is not None and not _CANONICAL.fullmatch(shown):
        shown = "<noncanonical>"
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "sender": None,
        "sudo_uid": shown,
        "operation": op,
        "request_id": None,
        "run_id": None,
        "digest": None,
        "outcome": "refused",
        "reason": reason,
        "result": {},
    }
    return json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"


def journal(layout: Layout, line: str) -> None:
    """Append `line` to `journal.jsonl` (born `0600`) and sync it."""
    path = layout.publisher_dir / JOURNAL
    flags = os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    try:
        fd = fsutil.create_file(path, flags, 0o600)
    except FileExistsError:
        fd = os.open(path, flags)
    try:
        _regular(fd, path)
        view = memoryview(line.encode("utf-8"))
        while view:
            view = view[os.write(fd, view) :]
        fsutil.sync_file(fd)
    finally:
        os.close(fd)
