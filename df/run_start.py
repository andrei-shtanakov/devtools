"""`df-run-start`: register a run, then hand it to `df-exec` (spec 5.8).

`darkfactory run start --product ID` and `run resume RUN_ID
[--accept-model-change --from-step N]` are root's entry: `sudo` runs the
`bin/df-run-start` wrapper, which runs `run start|resume` here. In this
order the process, still root,

1. checks the form of the arguments and that it is root, and that
   `df-exec` has a `ready` record in `accounts.toml`;
2. reads data only -- the snapshot, `products.toml`, `trust.toml`, the last
   probe records of `/var/lib/df/doctor/probes/` -- and decides: the release
   of the run (start: the snapshot's `current`; resume: the record's, which
   must still be `allowed`), the model pins (`models.toml` of the snapshot),
   and on resume which pins changed;
3. writes: the run record (`O_EXCL`) or the `model_change` events (append);
4. closes descriptors, `chdir("/")`, drops to `df-exec` (`setgroups`,
   `setgid`, `setuid`, then checks the process really is `df-exec`) and
   `execve`s `df-launch df-exec run` with the `run_id` on stdin.

Nothing under a role's home, no product file and no code of any release is
read or run before the drop. `current` is read once, in the snapshot.
"""

from __future__ import annotations

import fcntl
import os
import re
import secrets
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from df import fsutil, launch, models, probe
from df import platform as plat
from df.config import Account, ConfigError, load_accounts
from df.journal import StepFailed
from df.models import ModelChoice, ModelsConfig
from df.paths import Layout, default_layout
from df.stage2_config import (
    ModelChange,
    ModelPin,
    RunRecord,
    dump_model_change,
    dump_run,
    effective_pins,
    load_products,
    load_trust,
)
from df.state import Snapshot

EXEC_ROLE = "df-exec"
RECORD_MODE = 0o644
NO_PROBE = "не проверено"
NO_ACTUAL = "не сообщён"
RESUME_HINT = "повторите с --accept-model-change --from-step N"
_STEP = "run"
_NUMBER = re.compile(r"[1-9][0-9]{0,8}", re.ASCII)
_WRITE_FLAGS = os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC
_APPEND_FLAGS = os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC


@dataclass(frozen=True)
class Request:
    """What `run start|resume` was asked for."""

    action: Literal["start", "resume"]
    product: int | None = None
    run_id: str | None = None
    accept: bool = False
    from_step: int | None = None


def parse(argv: Sequence[str]) -> Request:
    """The one accepted form of each command; anything else is `StepFailed`.

    `start --product ID`; `resume RUN_ID` with `--accept-model-change` and
    `--from-step N` (N a positive integer) either both or neither.
    """
    words = list(argv)
    if words[:1] == ["start"]:
        return _parse_start(words[1:])
    if words[:1] == ["resume"]:
        return _parse_resume(words[1:])
    raise StepFailed(_STEP, f"нужно start или resume, получено {words}")


def _parse_start(rest: list[str]) -> Request:
    try:
        if len(rest) != 2 or rest[0] != "--product":
            raise ValueError(rest)
        return Request("start", product=models.repository_id(rest[1]))
    except ValueError:
        raise StepFailed(_STEP, "start: нужно --product <repository_id>") from None


def _parse_resume(rest: list[str]) -> Request:
    run_id, options = (rest[0], rest[1:]) if rest else ("", [])
    if not (launch.RUN_ID.fullmatch(run_id) and len(run_id) <= launch.MAX_RUN_ID):
        raise StepFailed(_STEP, "resume: нужен RUN_ID ([A-Za-z0-9._-]+)")
    if not options:
        return Request("resume", run_id=run_id)
    step = _step_of(options)
    if step is None:
        raise StepFailed(_STEP, f"resume: {_BOTH}; N — целое больше нуля")
    return Request("resume", run_id=run_id, accept=True, from_step=step)


def _step_of(options: list[str]) -> int | None:
    """N of `--accept-model-change --from-step N` or `--from-step N
    --accept-model-change`; any other shape is `None`."""
    flag = "--accept-model-change"
    if options[:1] == [flag] and options[1:2] == ["--from-step"]:
        number = options[2:]
    elif options[:1] == ["--from-step"] and options[2:] == [flag]:
        number = options[1:2]
    else:
        return None
    ok = len(number) == 1 and _NUMBER.fullmatch(number[0])
    return int(number[0]) if ok else None


_BOTH = "--accept-model-change и --from-step N задаются только вместе"


def main(argv: Sequence[str], *, layout: Layout, snap: Snapshot) -> int:
    """`run start|resume`; returns only if `execve` does not replace the
    process (a test's fake)."""
    req = parse(argv)
    if os.geteuid() != plat.ROOT_UID:
        raise StepFailed(_STEP, "нужен root: запустите через darkfactory run …")
    account = _exec_account(layout)
    run_id = (
        _start(layout, snap, req)
        if req.action == "start"
        else _resume(layout, snap, req)
    )
    hand_over(layout, account, run_id)
    return 1


def human_main(argv: Sequence[str], *, layout: Layout | None = None) -> int:
    """`darkfactory run start|resume` for the human: `chdir("/")`, close
    descriptors, `execv` of `sudo <opt>/bin/df-run-start <argv>` (the sudo
    password is allowed; no `-n`, decision 12)."""
    parse(argv)
    lay = default_layout() if layout is None else layout
    os.chdir("/")
    os.closerange(3, launch.fd_limit())
    os.execv(launch.SUDO, ["sudo", str(lay.run_start), *argv])
    return 1


def new_run_id() -> str:
    """`run-<UTC time>-<16 hex>`."""
    return f"run-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(8)}"


# --- start and resume -------------------------------------------------------------


def _start(layout: Layout, snap: Snapshot, req: Request) -> str:
    assert req.product is not None
    if req.product not in load_products(layout.etc / "products.toml"):
        raise StepFailed(
            _STEP, f"product_unknown: продукт {req.product} не зарегистрирован"
        )
    if snap.release_id is None:
        raise StepFailed(_STEP, "нет активного релиза: release_unknown")
    entry = launch.allowed_entry(snap, snap.release_id)
    pins = pins_from_data(layout, req.product, _models_of(snap))
    trust = load_trust(layout.etc / "trust.toml")
    rec = RunRecord(
        new_run_id(),
        entry.id,
        entry.digest,
        entry.admin_digest,
        req.product,
        pins,
        None if trust is None else trust.root_id,
        datetime.now(UTC).isoformat(),
        _started_by(),
    )
    register(layout, rec)
    return rec.run_id


def _resume(layout: Layout, snap: Snapshot, req: Request) -> str:
    assert req.run_id is not None
    rec = launch.release_of_record(layout, snap, req.run_id)
    # Two resumes of one run take turns: the second reads the events after
    # the first one's append, so no event has a stale `before`.
    with _locked(layout.runs_dir / f"{rec.run_id}.json"):
        events = launch.read_root_file(_events_path(layout, rec.run_id), optional=True)
        in_force = effective_pins(rec, _lines(events))
        fresh = pins_from_data(layout, rec.product, _models_of(snap))
        changes = model_changes(rec.run_id, in_force, fresh, req.from_step or 0)
        if changes and not req.accept:
            raise StepFailed(_STEP, f"{_describe(changes)}; {RESUME_HINT}")
        append_events(layout, rec.run_id, changes)
    return rec.run_id


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """An exclusive `flock` on the run record (root's, so only root can hold
    it), held for the block."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


_PIN_FIELDS = (
    "harness",
    "harness_version",
    "model",
    "actual_model",
    "effort",
    "params",
)


def _describe(changes: Sequence[ModelChange]) -> str:
    """`пин изменился: <role>: <field> <before> → <after>` per changed role."""
    return "; ".join(
        f"пин изменился: {c.role}: {_diff(c.before, c.after)}" for c in changes
    )


def _diff(before: ModelPin, after: ModelPin) -> str:
    fields = [f for f in _PIN_FIELDS if getattr(before, f) != getattr(after, f)]
    fields = fields or ["config_hash"]
    return ", ".join(f"{f} {getattr(before, f)} → {getattr(after, f)}" for f in fields)


def _models_of(snap: Snapshot) -> ModelsConfig:
    """The models of the snapshot; `models.toml` is not read again."""
    if snap.models_text is None:
        return models.EMPTY
    return models.parse_models(snap.models_text)


def _started_by() -> int:
    """The human behind `sudo`; root itself when there is none."""
    value = os.environ.get("SUDO_UID", "")
    ok = value.isascii() and value.isdigit() and int(value) < 1 << 32
    return int(value) if ok else 0


def _lines(text: str | None) -> list[str]:
    if not text:
        return []
    lines = text.split("\n")
    return lines[:-1] if lines[-1] == "" else lines


def _events_path(layout: Layout, run_id: str) -> Path:
    return layout.runs_dir / f"{run_id}.events.jsonl"


# --- pins ---------------------------------------------------------------------------


def pins_from_data(
    layout: Layout, product: int, cfg: ModelsConfig
) -> dict[str, ModelPin]:
    """One pin per role: the choice of `models.toml` and the last probe record
    of the role, both as data (nothing of a role's home is read). No record of
    the same choice: `harness_version` «не проверено», `actual_model` «не
    сообщён»."""
    pins: dict[str, ModelPin] = {}
    for role in models.ROLES:
        choice, _ = models.resolve(cfg, role, product)
        pins[role] = _pin(choice, _last_probe(layout, role, product, choice))
    return pins


def _last_probe(
    layout: Layout, role: str, product: int, choice: ModelChoice
) -> probe.ProbeRecord | None:
    """The product's record of the role, else the common one -- only a record
    of this very choice (its `config_hash`) says anything about the pin."""
    want = models.config_hash(choice)
    for scope in (product, None):
        found = probe.read_record(layout, role, scope)
        if found is not None and found.config_hash == want:
            return found
    return None


def _pin(choice: ModelChoice, found: probe.ProbeRecord | None) -> ModelPin:
    return ModelPin(
        choice.harness,
        found.harness_version if found and found.harness_version else NO_PROBE,
        choice.model,
        found.actual_model if found and found.actual_model else NO_ACTUAL,
        choice.effort,
        dict(choice.params),
        models.config_hash(choice),
    )


def model_changes(
    run_id: str,
    in_force: Mapping[str, ModelPin],
    fresh: Mapping[str, ModelPin],
    step: int,
) -> list[ModelChange]:
    """One `model_change` per role whose pin in force differs from the fresh
    one, in `models.ROLES` order; `step` is the first step on the new pin."""
    missing = [role for role in models.ROLES if role not in in_force]
    if missing:
        raise StepFailed(_STEP, f"запись {run_id}: нет пина роли {missing[0]}")
    return [
        ModelChange(run_id, role, in_force[role], fresh[role], step)
        for role in models.ROLES
        if in_force[role] != fresh[role]
    ]


# --- writes -------------------------------------------------------------------------


def register(layout: Layout, rec: RunRecord) -> None:
    """Write `runs/<run_id>.json`: root, `0644`, `O_EXCL | O_NOFOLLOW`, the
    file and the directory synced. A name that exists is never reused."""
    path = layout.runs_dir / f"{rec.run_id}.json"
    try:
        fd = fsutil.create_file(path, _WRITE_FLAGS, RECORD_MODE)
    except FileExistsError as exc:
        raise StepFailed(_STEP, f"{path}: уже есть") from exc
    _write_and_sync(fd, dump_run(rec))


def append_events(layout: Layout, run_id: str, changes: Sequence[ModelChange]) -> None:
    """Append the events to `runs/<run_id>.events.jsonl` in one write, synced.
    Only `dump_model_change` renders a line, as the reader parses it."""
    if not changes:
        return
    path = _events_path(layout, run_id)
    text = "".join(f"{dump_model_change(c)}\n" for c in changes)
    try:
        fd = fsutil.create_file(path, _APPEND_FLAGS, RECORD_MODE)
    except FileExistsError:
        fd = _append_open(path)
    _write_and_sync(fd, text)


def _append_open(path: Path) -> int:
    """The existing events file, judged like the reader judges it."""
    fd = os.open(path, _APPEND_FLAGS | os.O_NONBLOCK)
    problem = launch.record_problem(path, os.fstat(fd))
    if problem:
        os.close(fd)
        raise StepFailed(_STEP, problem)
    return fd


def _write_and_sync(fd: int, text: str) -> None:
    try:
        view = memoryview(text.encode("utf-8"))
        while view:
            view = view[os.write(fd, view) :]
        fsutil.sync_file(fd)
    finally:
        os.close(fd)


# --- the hand-over ---------------------------------------------------------------------


def _exec_account(layout: Layout) -> Account:
    try:
        account = load_accounts(layout.accounts).get(EXEC_ROLE)
    except ConfigError as exc:
        raise StepFailed(_STEP, f"accounts.toml: {exc}") from exc
    if account is None or account.state != "ready":
        raise StepFailed(_STEP, f"{EXEC_ROLE}: нет готовой записи в accounts.toml")
    return account


def hand_over(layout: Layout, account: Account, run_id: str) -> None:
    """Close descriptors, leave the caller's directory, drop to `df-exec`,
    put `run_id` on stdin and `execve` `df-launch df-exec run`."""
    os.closerange(3, launch.fd_limit())
    os.chdir("/")
    drop_privileges(account)
    read_end, write_end = os.pipe()
    try:
        os.write(write_end, f"{run_id}\n".encode("ascii"))
    finally:
        os.close(write_end)
    os.dup2(read_end, 0)
    os.close(read_end)
    wrapper = str(layout.libexec / "df-launch")
    os.execve(
        wrapper, ["df-launch", EXEC_ROLE, "run"], launch.role_env(layout, EXEC_ROLE)
    )


def drop_privileges(account: Account) -> None:
    """`setgroups([gid])`, `setgid`, `setuid` -- in this order, the groups
    first while still root -- then prove the process is the account: real and
    effective UID and GID."""
    os.setgroups([account.gid])
    os.setgid(account.gid)
    os.setuid(account.uid)
    have = (os.getuid(), os.geteuid(), os.getgid(), os.getegid())
    want = (account.uid, account.uid, account.gid, account.gid)
    if have != want:
        raise StepFailed(
            _STEP, f"после сброса uid/euid/gid/egid {have}, ожидалось {want}"
        )
