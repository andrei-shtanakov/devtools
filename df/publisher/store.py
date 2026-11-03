"""The publisher's durable state under `/var/lib/df/publisher` (spec 5.6).

`ids/<request_id>` binds an id to `(sender, operation, digest)` for the whole
installation; `keys/<sha256(sender|op|digest)>.lock` serialises identical
requests; `intents/<key>.json` is written durably before an external action
and `results/<key>.json` after a final outcome; `journal.jsonl` has one line
per request and never a secret.

`merge` (stage 2b, plan decision 8) adds phases, `intents/<key>.<name>.json`,
each written once, and the ownership of a PR, `owners/<repository_id>-<pr>.json`
naming the request key that holds it (taken and released under `owners.lock`).
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
from df.publisher.request import (
    FINAL_OUTCOMES,
    InFlight,
    Refused,
    Request,
    Response,
)

IDS_LOCK = "ids.lock"
OWNERS_LOCK = "owners.lock"
JOURNAL = "journal.jsonl"
SUBDIRS = ("ids", "keys", "intents", "results", "owners")
#: The phases of `merge` in their order; `rechecked.<n>` sit between
#: `attested` and `armed` (plan decision 8).
PHASES = ("attest", "attested", "armed", "put_answer")
RECHECKED = re.compile(r"rechecked\.([1-9][0-9]{0,3})", re.ASCII)
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
#: How long a lock may be waited for before `unknown: in_flight` (R24).
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
    `flock`, waiting at most `LOCK_WAIT` seconds (`InFlight`); the
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
                raise InFlight("lock") from None
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


def has_state(layout: Layout, key: str) -> bool:
    """An intent, a phase or a result of the idempotency key exists (`lstat`
    only; a link or a damaged file counts). Fails closed: anything but
    "absent" (an unreadable directory, say) is `True`. Ownerships are not
    scanned: a request owns a PR only past its intent, and another PR's
    damaged ownership must not change unrelated answers."""
    base = layout.publisher_dir
    paths = (base / "intents" / f"{key}.json", base / "results" / f"{key}.json")
    if any(_present(p) for p in paths):
        return True
    return _has_phase(base / "intents", key)


def _has_phase(intents: Path, key: str) -> bool:
    try:
        names = [e.name for e in os.scandir(intents)]
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return any(name.startswith(f"{key}.") for name in names)


def _present(path: Path) -> bool:
    try:
        os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True
    return True


# --- phases of merge -------------------------------------------------------


def _valid_phase(name: str) -> bool:
    return name in PHASES or RECHECKED.fullmatch(name) is not None


def phase_path(layout: Layout, key: str, name: str) -> Path:
    """`intents/<key>.<name>.json`."""
    return layout.publisher_dir / "intents" / f"{key}.{name}.json"


def phase(res: Reservation, name: str, payload: Mapping[str, object]) -> None:
    """Write phase `name` of the request durably, once: a phase that is
    already there is an `OSError` (the key's lock is held, so nobody else
    writes it)."""
    if not _valid_phase(name):
        raise ValueError(f"unknown phase {name!r}")
    path = phase_path(res.layout, res.key, name)
    if _present(path):
        raise OSError(f"{path}: phase {name} already written")
    record = {"request_id": res.request_id, "phase": name, "payload": dict(payload)}
    _write_state(path, json.dumps(record, sort_keys=True))


def read_phases(res: Reservation) -> dict[str, Mapping[str, object]]:
    """Every phase of the request, by name. A phase file that cannot be
    read or is not a phase record is an `OSError`: the state is damaged."""
    return _phases_of(res.layout, res.key)


def _phases_of(layout: Layout, key: str) -> dict[str, Mapping[str, object]]:
    intents = layout.publisher_dir / "intents"
    prefix, intent = f"{key}.", f"{key}.json"
    found: dict[str, Mapping[str, object]] = {}
    for name in sorted(e.name for e in os.scandir(intents)):
        if not name.startswith(prefix) or name == intent:
            continue
        stem = name[len(prefix) :].removesuffix(".json")
        if not name.endswith(".json") or not _valid_phase(stem):
            raise OSError(f"{intents / name}: not a phase of {key}")
        found[stem] = _phase_payload(intents / name, stem)
    return found


def _phase_payload(path: Path, name: str) -> Mapping[str, object]:
    try:
        record = json.loads(_read(path, MAX_STATE))
    except ValueError as exc:
        raise OSError(f"{path}: unreadable phase: {exc}") from exc
    payload = record.get("payload") if isinstance(record, dict) else None
    if not isinstance(payload, dict) or record.get("phase") != name:
        raise OSError(f"{path}: not a phase record")
    return payload


def last_phase(phases: Mapping[str, object]) -> str | None:
    """The latest phase of `phases` in the order of decision 8."""
    order = [*PHASES[:2], *rechecks(phases), *PHASES[2:]]
    present = [name for name in order if name in phases]
    return present[-1] if present else None


def rechecks(phases: Mapping[str, object]) -> list[str]:
    """The `rechecked.<n>` names of `phases`, by `n`."""
    numbered = [
        (int(m.group(1)), name)
        for name in phases
        if (m := RECHECKED.fullmatch(name)) is not None
    ]
    return [name for _n, name in sorted(numbered)]


# --- ownership of a PR --------------------------------------------------------


@dataclass(frozen=True)
class Owner:
    """One ownership file, as `doctor` lists it. `damaged` names what is
    wrong with the file itself (then only the ids of its name are known);
    `phases_damaged`: the request's phase files cannot be read."""

    repository_id: int | None
    pr: int | None
    request_key: str | None
    request_id: str | None
    last_phase: str | None
    taken_at: str | None
    final: bool
    damaged: str | None = None
    phases_damaged: bool = False


def owner_path(layout: Layout, repository_id: int, pr: int) -> Path:
    """`owners/<repository_id>-<pr>.json`."""
    return layout.publisher_dir / "owners" / f"{repository_id}-{pr}.json"


def _owner_record(path: Path) -> dict[str, object]:
    """The owner file's record; `ValueError` if it is not one."""
    record = json.loads(_read(path))
    if not isinstance(record, dict) or not isinstance(record.get("request_key"), str):
        raise ValueError(f"{path}: not an owner record")
    return record


def _holder(path: Path) -> str | None:
    """The request key the owner file names (`None`: no file). A damaged
    file is an `OSError`: it holds the PR for nobody can tell whom."""
    try:
        return str(_owner_record(path)["request_key"])
    except FileNotFoundError:
        return None
    except ValueError as exc:
        raise OSError(str(exc)) from exc


def _final(layout: Layout, key: str) -> bool:
    return _present(layout.publisher_dir / "results" / f"{key}.json")


def pr_busy(layout: Layout, repository_id: int, pr: int, request_key: str) -> bool:
    """Another request holds the PR: the owner file names another key whose
    request has no final result, or cannot be read (read only)."""
    try:
        holder = _holder(owner_path(layout, repository_id, pr))
    except OSError:
        return True
    return holder not in (None, request_key) and not _final(layout, str(holder))


def own_pr(layout: Layout, repository_id: int, pr: int, request_key: str) -> bool:
    """Take the PR for `request_key` under `owners.lock` (durably, before
    the final read). `True` if it is (or already was) this request's;
    `False` if another request without a final result holds it. An
    ownership whose request has a final result is stale and taken over."""
    path = owner_path(layout, repository_id, pr)
    fd = _lock_file(layout.publisher_dir / OWNERS_LOCK)
    try:
        try:
            holder = _holder(path)
        except OSError:
            return False
        if holder == request_key:
            return True
        if holder is not None and not _final(layout, holder):
            return False
        record = {
            "request_key": request_key,
            "repository_id": repository_id,
            "pr": pr,
            "taken_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        fsutil.write_atomic(path, json.dumps(record, sort_keys=True), 0o600)
        return True
    finally:
        _unlock(fd)


def release_pr(layout: Layout, repository_id: int, pr: int, request_key: str) -> bool:
    """Remove the ownership if it names `request_key` (idempotent); whether
    it was removed. Called only once the request's final result is kept."""
    path = owner_path(layout, repository_id, pr)
    fd = _lock_file(layout.publisher_dir / OWNERS_LOCK)
    try:
        try:
            holder = _holder(path)
        except OSError:
            return False
        if holder != request_key:
            return False
        fsutil.remove_durably(path)
        return True
    finally:
        _unlock(fd)


_OWNER_NAME = re.compile(r"([1-9][0-9]{0,18})-([1-9][0-9]{0,18})\.json", re.ASCII)


def owners(layout: Layout) -> list[Owner]:
    """Every ownership file, one entry each, with its request's id (from the
    intent), last phase and whether the request has a final result. A file
    that is not an ownership record is an entry with `damaged` (its ids from
    its name, if the name has them): one file never hides the others. An
    unreadable directory is an `OSError`."""
    directory = layout.publisher_dir / "owners"
    try:
        names = sorted(e.name for e in os.scandir(directory) if not _is_temp(e.name))
    except FileNotFoundError:
        return []
    return [_owner_of(layout, directory / name) for name in names]


def _owner_of(layout: Layout, path: Path) -> Owner:
    named = _OWNER_NAME.fullmatch(path.name)
    repository_id = int(named.group(1)) if named else None
    pr = int(named.group(2)) if named else None
    try:
        record = _owner_record(path)
    except (OSError, ValueError) as exc:
        return Owner(repository_id, pr, None, None, None, None, False, str(exc))
    key = str(record["request_key"])
    if (record.get("repository_id"), record.get("pr")) != (repository_id, pr):
        why = f"{path}: record does not match its name"
        return Owner(repository_id, pr, key, None, None, None, False, why)
    try:
        phases, broken = _phases_of(layout, key), False
    except OSError:
        phases, broken = {}, True
    return Owner(
        repository_id,
        pr,
        key,
        _request_id_of(layout, key),
        last_phase(phases),
        str(record.get("taken_at")),
        _final(layout, key),
        phases_damaged=broken,
    )


def _request_id_of(layout: Layout, key: str) -> str | None:
    path = layout.publisher_dir / "intents" / f"{key}.json"
    try:
        record = json.loads(_read(path, MAX_STATE))
    except (OSError, ValueError):
        return None
    found = record.get("request_id") if isinstance(record, dict) else None
    return found if isinstance(found, str) else None


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
