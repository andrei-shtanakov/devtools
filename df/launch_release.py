"""The operations of a role, run by the release's own code (spec 4.2, 5.8).

`df.launch.exec_main` `execve`s `<release>/venv/devtools/bin/python -I -B
<release>/src/devtools/df/launch_release.py <role> <op> <release_id> <run_id|->`
as the role. Under `-I` the script's directory is not on `sys.path`, so the
release's `src/devtools` is added first, from this file's own place.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import errno  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import resource  # noqa: E402
import secrets  # noqa: E402
import stat  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
from collections.abc import Callable, Mapping, Sequence  # noqa: E402
from typing import IO  # noqa: E402

from df import fsutil, launch, probe  # noqa: E402
from df.config import ConfigError, read_optional  # noqa: E402
from df.journal import StepFailed  # noqa: E402
from df.models import ROLE_CONTEXT, ROLES  # noqa: E402
from df.paths import Layout, default_layout  # noqa: E402
from df.stage2_config import (  # noqa: E402
    DEFAULT_API_BASE,
    Product,
    effective_pins,
    load_products,
    parse_publisher,
)

Transport = Callable[[str, Mapping[str, str]], tuple[int, bytes]]

TOKEN_PATH = (".config", "df", "github-token")
MAX_BODY = 1 << 20
MAX_TOKEN = 4096
TIMEOUT = 20
EXIT_MISMATCH = 1
EXIT_REFUSED = 2
_FULL_NAME = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", re.ASCII)
_TOKEN = re.compile(r"[\x21-\x7e]+", re.ASCII)
_FD_SCAN_CAP = 4096
_OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_STAGE3 = "конвейер — этап 3"


class Refused(Exception):
    """The operation is refused with a reason (exit 2)."""


def main(
    argv: Sequence[str],
    *,
    layout: Layout | None = None,
    file: str = __file__,
    transport: Transport | None = None,
    home: Path | None = None,
    out: IO[str] | None = None,
) -> int:
    """`argv = [role, op, release_id, run_id | "-"]`; the exit code.

    `file`, `transport`, `home` and `out` are seams for tests.
    """
    lay = default_layout() if layout is None else layout
    stdout = sys.stdout if out is None else out
    try:
        role, op, release_id, run_id = _arguments(argv, file)
        return _dispatch(role, op, release_id, run_id, lay, transport, home, stdout)
    except (Refused, ConfigError, StepFailed, OSError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_REFUSED


def check_report(layout: Layout | None = None) -> dict[str, object]:
    """What the role's process looks like from inside: a fresh nonce,
    identity, environment names, `cwd`, open descriptors above 2 and the
    `errno` of opening each of a fixed list of foreign paths (0: opened)."""
    lay = default_layout() if layout is None else layout
    return {
        "nonce": secrets.token_hex(16),
        "uid": os.getuid(),
        "euid": os.geteuid(),
        "gid": os.getgid(),
        "groups": sorted(os.getgroups()),
        "env": sorted(os.environ),
        "cwd": os.getcwd(),
        "fds": _open_fds(),
        "denied": {str(p): _open_errno(p) for p in _foreign_paths(lay)},
    }


def urllib_transport(url: str, headers: Mapping[str, str]) -> tuple[int, bytes]:
    """A minimal GET: no proxy, no redirect (the token must not follow one)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            return response.status, response.read(MAX_BODY + 1)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(MAX_BODY + 1)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


def _arguments(argv: Sequence[str], file: str) -> tuple[str, str, str, str | None]:
    if len(argv) != 4:
        raise Refused("нужно <роль> <операция> <release_id> <run_id|->")
    role, op, release_id, run_id = argv
    ops = launch.ROLE_OPS.get(role)
    if ops is None or op not in ops:
        raise Refused(f"{role}: операция {op!r} не допускается")
    own = Path(file).parents[3].name
    if release_id != own:
        raise Refused(f"чужой release_id {release_id!r}: этот код релиза {own!r}")
    if op in launch.CURRENT_OPS:
        if run_id != "-":
            raise Refused(f"{op}: run_id не принимается")
        return role, op, release_id, None
    if not launch.RUN_ID.fullmatch(run_id):
        raise Refused(f"{op}: нужен run_id")
    return role, op, release_id, run_id


def _dispatch(
    role: str,
    op: str,
    release_id: str,
    run_id: str | None,
    layout: Layout,
    transport: Transport | None,
    home: Path | None,
    out: IO[str],
) -> int:
    if op == "check":
        return _emit(out, check_report(layout), 0)
    if op == "check-github":
        base = Path(os.environ["HOME"]) if home is None else home
        send = urllib_transport if transport is None else transport
        report, code = check_github(layout, base, send)
        return _emit(out, report, code)
    assert run_id is not None
    return _pipeline(role, run_id, release_id, layout)


def _emit(out: IO[str], report: Mapping[str, object], code: int) -> int:
    print(json.dumps(report, sort_keys=True), file=out)
    return code


# --- check ------------------------------------------------------------------


def _open_fds() -> list[int]:
    soft = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    limit = _FD_SCAN_CAP if soft == resource.RLIM_INFINITY else min(soft, _FD_SCAN_CAP)
    found = []
    for fd in range(3, limit):
        try:
            os.fstat(fd)
        except OSError:
            continue
        found.append(fd)
    return found


def _foreign_paths(layout: Layout) -> list[Path]:
    others = (layout.role_home(r) for r in ("df-exec", "df-review", "df-publisher"))
    return [
        layout.secrets_dir,
        layout.publisher_dir,
        layout.perimeter_dir,
        layout.marks_dir,
        layout.verify_dir,
        layout.admin_home,
        *others,
    ]


def _open_errno(path: Path) -> int:
    try:
        fd = os.open(path, _OPEN_FLAGS)
    except OSError as exc:
        return exc.errno or errno.EIO
    os.close(fd)
    return 0


# --- check-github -----------------------------------------------------------


def check_github(
    layout: Layout, home: Path, transport: Transport
) -> tuple[dict[str, object], int]:
    """`{login, products: {repository_id: {full_name, permissions, mismatch}}}`
    from `GET /user` and `GET /repos/<full_name>` of every product, and 1 if
    any product's `machine_user` is not the token's login. A missing or
    unreadable token refuses before any request."""
    token = _read_token(home.joinpath(*TOKEN_PATH))
    api = _api_base(layout)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "darkfactory-check",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    login = _get(transport, f"{api}/user", headers).get("login")
    if not isinstance(login, str) or not login:
        raise Refused("GET /user: нет login")
    products = {}
    for rid, prod in sorted(load_products(layout.etc / "products.toml").items()):
        permissions = _permissions(transport, api, prod, headers)
        products[str(rid)] = {
            "full_name": prod.full_name,
            "permissions": permissions,
            "mismatch": prod.machine_user != login,
        }
    mismatch = any(p["mismatch"] for p in products.values())
    report = {"login": login, "products": products}
    return report, EXIT_MISMATCH if mismatch else 0


def _api_base(layout: Layout) -> str:
    text = read_optional(layout.etc / "publisher.toml")
    return DEFAULT_API_BASE if text is None else parse_publisher(text).api_base


def _permissions(
    transport: Transport, api: str, prod: Product, headers: Mapping[str, str]
) -> dict[str, object]:
    if not _FULL_NAME.fullmatch(prod.full_name):
        raise Refused(f"{prod.repository_id}: недопустимое имя {prod.full_name!r}")
    body = _get(transport, f"{api}/repos/{prod.full_name}", headers)
    permissions = body.get("permissions")
    if not isinstance(permissions, dict):
        raise Refused(f"GET /repos/{prod.full_name}: нет permissions")
    return permissions


def _get(
    transport: Transport, url: str, headers: Mapping[str, str]
) -> dict[str, object]:
    try:
        status, body = transport(url, headers)
    except OSError as exc:
        raise Refused(f"GET {url}: {exc}") from exc
    if status != 200:
        raise Refused(f"GET {url}: HTTP {status}")
    try:
        data = json.loads(body[:MAX_BODY]) if len(body) <= MAX_BODY else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise Refused(f"GET {url}: ответ не объект JSON")
    return data


def _read_token(path: Path) -> str:
    """The machine user's token: own regular file, `0600`-like, one word."""
    try:
        fd = os.open(path, _OPEN_FLAGS)
    except OSError as exc:
        raise Refused(f"токен {path}: не читается: {exc.strerror or exc}") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or fsutil.owner_uid(st) != os.geteuid():
            raise Refused(f"токен {path}: не обычный файл этой роли")
        if st.st_mode & 0o077:
            raise Refused(
                f"токен {path}: права {stat.S_IMODE(st.st_mode):04o}, нужны 0600"
            )
        data = os.read(fd, MAX_TOKEN + 1)
    except OSError as exc:
        raise Refused(f"токен {path}: не читается: {exc.strerror or exc}") from exc
    finally:
        os.close(fd)
    text = data.decode("ascii", errors="replace").strip()
    if len(data) > MAX_TOKEN or not _TOKEN.fullmatch(text):
        raise Refused(f"токен {path}: пуст или недопустим")
    return text


# --- run and review ---------------------------------------------------------


def _pipeline(role: str, run_id: str, release_id: str, layout: Layout) -> int:
    """Admission of the pins of this context's roles, then the stage-3 stop."""
    rec = launch.read_record(layout, run_id)
    if rec.release_id != release_id:
        raise Refused(f"запись {run_id}: релиз {rec.release_id!r}, не {release_id!r}")
    events = launch.read_root_file(
        layout.runs_dir / f"{run_id}.events.jsonl", optional=True
    )
    pins = effective_pins(rec, _lines(events))
    stale = []
    for name in (r for r in ROLES if ROLE_CONTEXT[r] == role):
        pin = pins.get(name)
        if pin is None:
            raise Refused(f"запись {run_id}: нет пина роли {name}")
        status = probe.pin_ready(layout, name, pin)
        if not status.current:
            stale.append(f"{name}: {status.text}")
    if stale:
        raise Refused("; ".join(stale))
    raise Refused(_STAGE3)


def _lines(text: str | None) -> list[str]:
    if not text:
        return []
    lines = text.split("\n")
    return lines[:-1] if lines[-1] == "" else lines


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
