"""`darkfactory setup-agent` (spec 4.3; stage 2a, Task 12): root installs
the GitHub credentials the human made.

- `app-key --app-id N --file PATH`: the App's private key as
  `/etc/df/secrets/app-<N>.pem` (`df-publisher`, `0400`);
- `machine-token --file PATH`: the machine user's token as
  `~df-exec/.config/df/github-token` (`df-exec`, `0600`).

Root reads the source as data (a regular file, never through a final link,
bounded, of the expected form) and never deletes it. Everything that can be
refused is refused before the first write. Every write goes through an
open descriptor (`fsutil.write_under`); root never changes the owner or the
mode of an object it found. A secret is never put into a message: refusals
name the file and the reason only.

After the write, `check_app_key` asks the publisher's `self-check` and
`check_machine_token` the role's `check-github`; each answers a `Check`
the command prints (a failed one is `NeedsHuman`, exit 3).
"""

from __future__ import annotations

import errno
import json
import os
import re
import stat
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from df import fsutil, inputs, perimeter_client
from df import platform as plat
from df.config import Account, ConfigError, load_accounts
from df.digest import TreeError
from df.journal import StepFailed
from df.launch import SUDO
from df.paths import MACHINE_TOKEN, Layout
from df.publisher import request
from df.publisher.request import BadResponse
from df.stage2_config import Product, load_products

STEP = "setup-agent"
PUBLISHER = "df-publisher"
EXEC = "df-exec"
#: The largest App key root reads (a 4096-bit RSA PEM is about 3.2 KiB).
KEY_CAP = 16 * 1024
#: The longest token line (without its one final newline).
TOKEN_CAP = 255
KEY_MODE = 0o400
TOKEN_MODE = 0o600
TOKEN_DIR_MODE = 0o700
SECRETS_MODE = 0o700
#: Seconds root waits for `check-github` (a GET per product, 20 s each).
CHECK_TIMEOUT = 120.0
#: Seconds root waits for the publisher's `self-check`: a JWT, the App's
#: installations, then a token and a repository listing per installation, each
#: request up to 20 s (`perimeter-status`'s 60 s is too short for that).
SELF_CHECK_TIMEOUT = 300.0
_PEM = re.compile(
    rb"-----BEGIN (RSA |)PRIVATE KEY-----\r?\n"
    rb"(?:[A-Za-z0-9+/=]{1,76}\r?\n){1,400}"
    rb"-----END \1PRIVATE KEY-----\r?\n?"
)
_TOKEN = re.compile(rb"github_pat_[A-Za-z0-9_]{1,244}|ghp_[A-Za-z0-9]{1,251}")
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_DIR_FOLLOW_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
_WRITABLE = stat.S_IWGRP | stat.S_IWOTH
#: Self-check problems that concern an App (its key, its installations); any
#: other (`perimeter_unavailable`, ...) is not the key's fault: a warning.
APP_PROBLEMS = ("app_installation_", "app_key_unusable", "app_unreadable")
_ROLES_SETUP = "выполните sudo darkfactory roles setup"
_NOT_DIR = (errno.ELOOP, errno.ENOTDIR, errno.EMLINK)


@dataclass(frozen=True)
class Check:
    """The outcome of a post-write check: lines for the human, warnings, and
    the failure (`None`: passed)."""

    lines: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    failure: str | None = None


# --- app-key ---------------------------------------------------------------


def install_app_key(layout: Layout, app_id: int, source: Path) -> None:
    """Write `source` (a PEM private key) as `layout.app_key(app_id)`.

    `secrets/` must be `df-publisher`'s `0700` directory on a trusted path;
    the key is written through its descriptor, `df-publisher` `0400`."""
    if app_id <= 0:
        raise StepFailed(STEP, f"app-key: --app-id {app_id}: нужно целое > 0")
    owner = _account(layout, PUBLISHER)
    data = _read_key(source)
    target = layout.app_key(app_id)
    fd = _open_secrets(layout, owner)
    try:
        fsutil.write_under(
            fd,
            target.name,
            data,
            mode=KEY_MODE,
            uid=owner.uid,
            gid=owner.gid,
            path=target,
        )
    finally:
        os.close(fd)


def setup_app_key(
    layout: Layout,
    app_id: int,
    source: Path,
    *,
    before_check: Callable[[], None] = lambda: None,
) -> Check:
    """`install_app_key`, then `before_check` (the writer closes an odd
    generation there: its refusals are behind it, and the check's readers
    need a stable snapshot), then `check_app_key`."""
    install_app_key(layout, app_id, source)
    before_check()
    return check_app_key(layout, app_id)


def _read_key(source: Path) -> bytes:
    data = _read_source(source, KEY_CAP)
    if _PEM.fullmatch(data) is None:
        raise StepFailed(
            STEP,
            f"{source}: не PEM-ключ (нужен «-----BEGIN [RSA ]PRIVATE KEY-----» "
            "без шифрования); ничего не записано",
        )
    return data


def _open_secrets(layout: Layout, owner: Account) -> int:
    """`secrets/` opened from `/`: its parents on a trusted path, itself
    `df-publisher`'s and exactly `0700`. The system components up to `etc`
    follow links (macOS `/etc -> private/etc`); the installer's own, `df`
    and `secrets`, never do (`open_own`)."""
    path = layout.secrets_dir
    fd = open_own(layout, path, owner.uid, system=layout.etc.parent)
    mode = stat.S_IMODE(os.fstat(fd).st_mode)
    if mode != SECRETS_MODE:
        os.close(fd)
        raise StepFailed(
            STEP, f"{path}: права {mode:04o}, нужны {SECRETS_MODE:04o}; {_ROLES_SETUP}"
        )
    return fd


# --- machine-token ---------------------------------------------------------


def install_machine_token(layout: Layout, source: Path) -> None:
    """Write the token of `source` as `~df-exec/.config/df/github-token`.

    The home is opened from `/` one component at a time; `.config` and `df`
    are opened under it without following a link, and created (`df-exec`,
    `0700`, through their descriptors) only when absent; one that is found
    must be `df-exec`'s directory, else a refusal with nothing changed.
    Its warnings (a found directory writable by others) are returned by
    `setup_machine_token` only."""
    _install_token(layout, _read_token(source))


def setup_machine_token(
    layout: Layout,
    source: Path,
    *,
    before_check: Callable[[], None] = lambda: None,
) -> Check:
    """`install_machine_token`, then `before_check` (as `setup_app_key`),
    then `check_machine_token` (the token known to the check only to mask
    it)."""
    token = _read_token(source)
    warnings = _install_token(layout, token)
    before_check()
    check = check_machine_token(layout, token)
    return replace(check, warnings=warnings + check.warnings)


def _install_token(layout: Layout, token: bytes) -> tuple[str, ...]:
    owner = _account(layout, EXEC)
    home = layout.role_home(EXEC)
    # the home's parents are the system's (a Linux `/home -> var/home`)
    fd = open_own(layout, home, owner.uid, system=home.parent)
    warnings: list[str] = []
    try:
        here = home
        for name in MACHINE_TOKEN[:-1]:
            here = here / name
            child = _token_dir(fd, name, here, owner, warnings)
            os.close(fd)
            fd = child
        target = home.joinpath(*MACHINE_TOKEN)
        fsutil.write_under(
            fd,
            target.name,
            token + b"\n",
            mode=TOKEN_MODE,
            uid=owner.uid,
            gid=owner.gid,
            path=target,
        )
    finally:
        os.close(fd)
    return tuple(warnings)


def _read_token(source: Path) -> bytes:
    data = _read_source(source, TOKEN_CAP + 1)
    token = data.removesuffix(b"\n")
    if _TOKEN.fullmatch(token) is None:  # its bounds are TOKEN_CAP's
        raise StepFailed(
            STEP,
            f"{source}: не токен (нужна одна строка github_pat_… или ghp_… "
            f"до {TOKEN_CAP} байт); ничего не записано",
        )
    return token


def _token_dir(
    parent: int, name: str, path: Path, owner: Account, warnings: list[str]
) -> int:
    """`name` under `parent` as a descriptor. A found one is judged and kept
    as it is; an absent one is made, then handed over through its own
    descriptor -- only if what is opened is still root's, `0700` and empty
    (else it was swapped: a found object)."""
    try:
        return _found_dir(parent, name, path, owner, warnings)
    except FileNotFoundError:
        pass
    created = fsutil.make_dir(parent, name, TOKEN_DIR_MODE, path=path, repair=False)
    if not created:  # someone else made it meanwhile: a found object
        return _found_dir(parent, name, path, owner, warnings)
    fd = _open_dir(parent, name, path)
    if not _fresh(fd):
        os.close(fd)
        return _found_dir(parent, name, path, owner, warnings)
    try:
        os.fchown(fd, owner.uid, owner.gid)
        os.fchmod(fd, TOKEN_DIR_MODE)
        fsutil.sync_dir(fd)
        fsutil.sync_dir(parent)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _fresh(fd: int) -> bool:
    """The directory `make_dir` just created: root's, `0700`, empty."""
    st = os.fstat(fd)
    if fsutil.owner_uid(st) != plat.ROOT_UID:
        return False
    return stat.S_IMODE(st.st_mode) == TOKEN_DIR_MODE and not os.listdir(fd)


def _found_dir(
    parent: int, name: str, path: Path, owner: Account, warnings: list[str]
) -> int:
    fd = _open_dir(parent, name, path)
    st = os.fstat(fd)
    have = fsutil.owner_uid(st)
    if have != owner.uid:
        hint = (
            f"; если это пустой каталог root, оставшийся после сбоя, удалите его "
            f"(sudo rmdir {path}) и повторите"
            if have == plat.ROOT_UID and not os.listdir(fd)
            else ""
        )
        os.close(fd)
        raise StepFailed(
            STEP,
            f"{path}: владелец uid {have}, нужен {owner.name} ({owner.uid}); "
            f"исправьте вручную, root не меняет владельца найденного{hint}",
        )
    if st.st_mode & _WRITABLE:
        mode = stat.S_IMODE(st.st_mode)
        warnings.append(
            f"{path}: права {mode:04o}, запись для группы или всех "
            "(root не меняет найденное; советуем chmod 0700)"
        )
    return fd


# --- shared ----------------------------------------------------------------


def open_own(layout: Layout, path: Path, owner: int, *, system: Path) -> int:
    """Directory `path` of the installer (or of a role) as a descriptor: the
    whole way to it passes the trusted-path policy first; `system` and its
    parents are then opened following links (vetted by that policy), every
    component below `system` -- `path` itself included -- never; `path` is
    `owner`'s (`open_chain`)."""
    if system not in path.parents:  # `path` itself is never followed
        raise ValueError(f"{system} is not a parent of {path}")
    problems = plat.trusted_path_problems(path.parent)
    if problems:
        raise StepFailed(STEP, "; ".join(problems) + f"; {_ROLES_SETUP}")
    return open_chain(layout, path, owner, follow_to=system)


def open_chain(
    layout: Layout, path: Path, owner: int, *, follow_to: Path | None = None
) -> int:
    """Directory `path` as a descriptor, opened from `/` one component at a
    time (`O_DIRECTORY|O_NOFOLLOW`): every component from the layout root
    down is root's and not writable by group or others, the last is
    `owner`'s. The components above the layout root (only a test's root has
    any) are judged by the trusted-path policy instead. `follow_to` and its
    parents are system components that may be links: they are opened
    following them (the caller has vetted them by the trusted-path policy)
    and judged by what they lead to."""
    problems = plat.trusted_path_problems(layout.root)
    if problems:
        raise StepFailed(STEP, "; ".join(problems))
    fd = os.open("/", _DIR_FLAGS)
    here = Path("/")
    try:
        parts = path.parts[1:]
        _judge(fd, here, layout, None if parts else owner)
        for i, part in enumerate(parts):
            here = here / part
            system = follow_to is not None and (
                here == follow_to or here in follow_to.parents
            )
            try:
                nxt = _open_dir(fd, part, here, follow=system)
            except FileNotFoundError as exc:
                raise StepFailed(STEP, f"{here}: нет каталога; {_ROLES_SETUP}") from exc
            os.close(fd)
            fd = nxt
            _judge(fd, here, layout, owner if i == len(parts) - 1 else None)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _open_dir(parent: int, name: str, path: Path, *, follow: bool = False) -> int:
    flags = _DIR_FOLLOW_FLAGS if follow else _DIR_FLAGS
    try:
        return os.open(name, flags, dir_fd=parent)
    except FileNotFoundError:
        raise
    except OSError as exc:
        if exc.errno in _NOT_DIR:
            raise StepFailed(
                STEP, f"{path}: ссылка или не каталог; ничего не записано"
            ) from exc
        raise


def _judge(fd: int, path: Path, layout: Layout, owner: int | None) -> None:
    if path in layout.root.parents:
        return
    st = os.fstat(fd)
    have = fsutil.owner_uid(st)
    if owner is not None:
        if have != owner:
            raise StepFailed(
                STEP, f"{path}: владелец uid {have}, нужен {owner}; {_ROLES_SETUP}"
            )
        return
    if have != plat.ROOT_UID or st.st_mode & _WRITABLE:
        raise StepFailed(
            STEP,
            f"{path}: не каталог root без записи для группы и всех; {_ROLES_SETUP}",
        )


def _read_source(source: Path, cap: int) -> bytes:
    """The source as data: absolute, a regular file read without following a
    final link, at most `cap` bytes. Never deleted."""
    if not source.is_absolute():
        raise StepFailed(STEP, f"--file {str(source)!r}: путь не абсолютный")
    try:  # never open a device or a FIFO: refused before the open
        st = os.lstat(source)
    except OSError as exc:
        raise StepFailed(STEP, f"--file {source}: {exc.strerror or exc}") from exc
    if not stat.S_ISREG(st.st_mode):
        raise StepFailed(STEP, f"--file {source}: не обычный файл")
    try:
        return inputs.read_capped(source, cap)
    except TreeError as exc:
        raise StepFailed(STEP, f"--file: {exc}") from exc


def _account(layout: Layout, name: str) -> Account:
    try:
        account = load_accounts(layout.accounts).get(name)
    except ConfigError as exc:
        raise StepFailed(STEP, f"accounts.toml: {exc}") from exc
    if account is None or account.state != "ready":
        raise StepFailed(STEP, f"нет готовой учётки {name}; {_ROLES_SETUP}")
    return account


# --- post-write checks ------------------------------------------------------


def publisher_self_check(layout: Layout, *, purpose: str = STEP) -> bytes:
    """`df-publish self-check` from root (`sudo -n -u df-publisher`); its
    stdout. No answer is `OSError`. `purpose` prefixes the request id
    (`setup-agent`, `doctor`). The tests' seam."""
    payload = {
        "schema": request.REQUEST_SCHEMA,
        "request_id": f"{purpose}-{uuid.uuid4().hex}",
    }
    data = json.dumps(payload, sort_keys=True).encode("utf-8")
    return perimeter_client.sudo_call(
        data, layout, operation=request.SELF_CHECK, timeout=SELF_CHECK_TIMEOUT
    )


def exec_check_github(layout: Layout) -> plat.RunResult:
    """`df-launch df-exec check-github` from root (`sudo -n -u df-exec`).
    The tests' seam."""
    wrapper = layout.libexec / "df-launch"
    argv = [SUDO, "-n", "-u", EXEC, str(wrapper), EXEC, "check-github"]
    return plat.run_command(argv, timeout=CHECK_TIMEOUT)


def check_app_key(layout: Layout, app_id: int) -> Check:
    """The publisher's `self-check`: `done` without App problems
    (`APP_PROBLEMS`) passes; another problem is a warning. No answer or
    another outcome fails. An App no product of `products.toml` uses is not
    signed with by the check: a warning says the key was not checked."""
    products, warnings = _products(layout)
    if not any(p.app_id == app_id for p in products.values()):
        warnings += (_unused(app_id),)
    try:
        raw = publisher_self_check(layout)
    except OSError as exc:
        return Check((), warnings, f"self-check: нет ответа: {exc}")
    lines = raw.decode("utf-8", "replace").splitlines()
    if len(lines) != 1:
        return Check((), warnings, f"self-check: нет ответа ({len(lines)} строк)")
    try:
        resp = request.parse_response(lines[0])
    except BadResponse as exc:
        return Check((), warnings, f"self-check: нет ответа: {exc}")
    if resp.outcome != "done":
        return Check((), warnings, f"self-check: {resp.outcome}: {resp.reason}")
    problems = resp.result.get("problems")
    if not isinstance(problems, list):
        return Check((), warnings, "self-check: в ответе нет problems")
    texts = [str(p) for p in problems]
    installs = installation_apps(products.values())
    app = [p for p in texts if problem_app(p, installs) in (app_id, None)]
    other = tuple(f"self-check: {p}" for p in texts if p not in app)
    if app:
        return Check((), warnings + other, "self-check: " + "; ".join(app))
    line = "проблем App нет" if other else "проблем нет"
    return Check((f"self-check издателя: {line}",), warnings + other)


def installation_apps(products: Iterable[Product]) -> dict[int, int | None]:
    """Installation id -> its App; an id two Apps claim maps to `None`
    (ambiguous: judged as this App's, conservative)."""
    apps: dict[int, int | None] = {}
    for p in products:
        known = apps.get(p.installation_id, p.app_id)
        apps[p.installation_id] = p.app_id if known == p.app_id else None
    return apps


def problem_app(problem: str, installs: Mapping[int, int | None]) -> int | None:
    """The App a problem of `APP_PROBLEMS` concerns: `app_key_unusable: <id>`
    and `app_unreadable: <id>: ...` name it; `app_installation_*: <id>`
    names an installation, mapped through `products.toml`. `None`: not
    attributable (judged as this App's: conservative). `-1`: not an App
    problem."""
    if not problem.startswith(APP_PROBLEMS):
        return -1
    ident = problem.partition(": ")[2].split(":", 1)[0].strip()
    if not ident.isascii() or not ident.isdigit():
        return None
    if problem.startswith("app_installation_"):
        return installs.get(int(ident))
    return int(ident)


def _products(layout: Layout) -> tuple[dict[int, Product], tuple[str, ...]]:
    try:
        return load_products(layout.etc / "products.toml"), ()
    except ConfigError as exc:
        return {}, (f"products.toml не читается: {exc}",)


def _unused(app_id: int) -> str:
    return (
        f"не проверено: нет продукта с App {app_id} в products.toml (self-check "
        "этот ключ не использовал; регистрация продукта — этап 3)"
    )


def check_machine_token(layout: Layout, token: bytes = b"") -> Check:
    """`check-github` as `df-exec`: exit 0 passes, 1 (a product's
    `machine_user` is not the login) is a warning, anything else fails.
    The login is a line for the human. `token`, if given, is masked in any
    text taken from the role's output."""
    result = exec_check_github(layout)
    err = _masked(result.stderr, token).strip()
    if not result.launched or result.timed_out:
        state = "срок истёк" if result.timed_out else "не стартовал"
        return Check(failure=f"check-github: {state}" + (f": {err}" if err else ""))
    if result.returncode not in (0, 1):
        tail = err or f"код {result.returncode}"
        return Check(failure=f"check-github: {tail}")
    try:
        report = json.loads(result.stdout)
    except ValueError:
        report = None
    login = report.get("login") if isinstance(report, dict) else None
    if not isinstance(report, dict) or not isinstance(login, str) or not login:
        return Check(failure="check-github: ответ без login")
    lines = (f"machine user: {login}",)
    if result.returncode == 0:
        return Check(lines)
    return Check(lines, _mismatches(report, login))


def _mismatches(report: dict[str, object], login: str) -> tuple[str, ...]:
    products = report.get("products")
    rows = products.items() if isinstance(products, dict) else ()
    found = tuple(
        f"логин {login} не совпадает с machine_user продукта "
        f"{row.get('full_name')} ({rid}) в products.toml"
        for rid, row in rows
        if isinstance(row, dict) and row.get("mismatch") is True
    )
    return found or (f"check-github: логин {login} не совпадает с machine_user",)


def _masked(data: bytes, token: bytes) -> str:
    text = data.decode("utf-8", "replace")
    return "***".join(text.split(token.decode("ascii"))) if token else text
