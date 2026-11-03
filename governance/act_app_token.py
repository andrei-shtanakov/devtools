"""Личность GitHub App для обвязки мержа (спека §9.4) и самоотчёт установок (§6.2).

Исполняется `merge-pr.sh` файлом (форма Task 15): `uv run --frozen --exact --group
governance --no-config --no-env-file python -I <этот файл> --app-id … --key …
--repo …` → stdout две строки: `<slug>[bot]` и токен установки на целевой репо;
иначе код 2 и причина в stderr (без токенов и байтов ключа). `--revoke` (Task 9,
п. 9d) — отзыв токена установки, сам токен — только stdin (не argv: виден в `ps`;
не окружение): код 0 — отозван, 2 — нет. JWT —
`pyjwt[crypto]` (группа governance, пин в uv.lock); HTTP — stdlib `urllib`, без
перенаправлений.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, "") and "governance" not in sys.modules:
    # Исполнение файлом под `python -I`: пакет — явно по пути (как act_premerge).
    import importlib.util

    _pkg_dir = Path(__file__).resolve().parent
    _spec = importlib.util.spec_from_file_location(
        "governance",
        _pkg_dir / "__init__.py",
        submodule_search_locations=[str(_pkg_dir)],
    )
    assert _spec is not None and _spec.loader is not None
    _pkg = importlib.util.module_from_spec(_spec)
    sys.modules["governance"] = _pkg
    _spec.loader.exec_module(_pkg)

import argparse  # noqa: E402
import http.client  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
from collections.abc import Callable, Mapping  # noqa: E402
from typing import Any  # noqa: E402

from governance.act_anon import MAX_BODY  # noqa: E402
from governance.act_time import Unavailable  # noqa: E402

API = "https://api.github.com"
PER_PAGE = 100
MAX_PAGES = 50
#: `owner/name` целевого репо — подставляется в путь запроса.
REPO_SLUG = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
#: Slug App: одна строка stdout без пробелов (логин `<slug>[bot]`).
APP_SLUG = re.compile(r"[A-Za-z0-9_.-]+")
#: Токен установки: одна строка stdout, та же форма, что сверяет `merge-pr.sh`.
TOKEN = re.compile(r"[A-Za-z0-9_]+")
#: Полномочия токена мержа: `PUT …/pulls/{n}/merge`, `GET …/compare`,
#: `DELETE …/git/refs/heads/{ветка}` — contents и pull_requests на запись.
MERGE_PERMISSIONS = {"contents": "write", "pull_requests": "write"}
Http = Callable[..., tuple[int, Any]]
Found = tuple[str, str] | Unavailable | None
"""Как `act_trust.Found`: нарушение `(роль репо, деталь)`, недоступность или чисто."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """3xx не исполняется: ответ возвращается кодом (Authorization не уезжает)."""

    def redirect_request(  # type: ignore[override]
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def _build_opener() -> urllib.request.OpenerDirector:
    """Как `act_anon`: пустой ProxyHandler — прокси из окружения (и их
    credentials) не используются; перенаправления не исполняются."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


_OPENER = _build_opener()


def _http(
    method: str, path: str, auth: str, body: dict | None = None
) -> tuple[int, Any]:
    """Запрос к API GitHub; ошибка — `(код, None)`; сбой транспорта и тело больше
    `MAX_BODY` (предел `act_anon`) — `(0, None)`."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": auth,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "devtools-act-app",
        },
    )
    try:
        with _OPENER.open(req, timeout=30) as answer:
            raw = answer.read(MAX_BODY + 1)
            if len(raw) > MAX_BODY:
                return 0, None
            return answer.status, json.loads(raw or b"null")
    except urllib.error.HTTPError as err:
        return err.code, None
    except (OSError, ValueError, http.client.HTTPException):
        return 0, None


def _field(data: Any, field: str) -> Any:
    return data.get(field) if isinstance(data, dict) else None


def _page_ids(
    http: Http, path: str, auth: str, field: str | None
) -> list[int] | Unavailable:
    """`id` всех записей списка: до первой неполной страницы (полнота по построению).

    `field` — ключ списка в объекте с `total_count` (тогда сумма сверяется с
    ним); `None` — ответ сам список. Каждая запись — объект с целым `id`, иначе
    `Unavailable`: неразобранная запись не выпадает молча (ревью PR #674).
    Больше `MAX_PAGES` — `Unavailable`."""
    items: list[int] = []
    total: object = None
    for page in range(1, MAX_PAGES + 1):
        status, data = http("GET", f"{path}?per_page={PER_PAGE}&page={page}", auth)
        chunk = data if field is None else _field(data, field)
        if status != 200 or not isinstance(chunk, list):
            return Unavailable(f"GET {path} (стр. {page}): HTTP {status}")
        if field is not None:
            total = _field(data, "total_count")
        ids = [_field(entry, "id") for entry in chunk]
        if any(type(ident) is not int for ident in ids):
            return Unavailable(f"GET {path} (стр. {page}): запись без целого id")
        items.extend(ids)
        if len(chunk) < PER_PAGE:
            if field is not None and (type(total) is not int or total != len(items)):
                return Unavailable(f"GET {path}: {len(items)} из total_count={total!r}")
            return items
    return Unavailable(f"GET {path}: больше {MAX_PAGES} страниц")


def _bearer(app_id: int, key: Path, now: int | None) -> str | Unavailable:
    """`Bearer <JWT>` App; ключ не прочитан или не подписывает — `Unavailable`
    без текста исключения (в нём могли бы оказаться байты ключа)."""
    import jwt

    at = int(time.time()) if now is None else now
    claims = {"iat": at - 60, "exp": at + 540, "iss": str(app_id)}
    try:
        return "Bearer " + jwt.encode(claims, key.read_bytes(), algorithm="RS256")
    except OSError:
        return Unavailable(f"ключ App {key} не прочитан")
    except Exception:  # noqa: BLE001 — разбор ключа: текст исключения не выводим
        return Unavailable(f"ключ App {key} не подписывает JWT (RS256)")


def _token(
    http: Http, bearer: str, installation: int, body: dict | None = None
) -> str | Unavailable:
    path = f"/app/installations/{installation}/access_tokens"
    status, data = http("POST", path, bearer, body)
    token = _field(data, "token")
    if status != 201 or not isinstance(token, str) or TOKEN.fullmatch(token) is None:
        return Unavailable(f"токен установки {installation}: HTTP {status}")
    return token


def app_identity(
    app_id: int, key: Path, repo: str, *, http: Http = _http, now: int | None = None
) -> tuple[str, str] | Unavailable:
    """`(<slug>[bot], токен установки на repo)` — личность без `gh api user` (403)."""
    if REPO_SLUG.fullmatch(repo) is None:
        return Unavailable(f"репо {repo!r} — не owner/name")
    bearer = _bearer(app_id, key, now)
    if isinstance(bearer, Unavailable):
        return bearer
    status, data = http("GET", "/app", bearer)
    slug = _field(data, "slug")
    if status != 200 or not isinstance(slug, str) or APP_SLUG.fullmatch(slug) is None:
        return Unavailable(f"GET /app: HTTP {status}")
    status, data = http("GET", f"/repos/{repo}/installation", bearer)
    installation = _field(data, "id")
    if status != 200 or type(installation) is not int:
        return Unavailable(f"установка App на {repo}: HTTP {status}")
    # Токен сужен до целевого репо и полномочий мержа (ревью Task 15a, m1).
    narrow = {"repositories": [repo.split("/", 1)[1]], "permissions": MERGE_PERMISSIONS}
    token = _token(http, bearer, installation, narrow)
    if isinstance(token, Unavailable):
        return token
    return f"{slug}[bot]", token


def revoke(token: str, *, http: Http = _http) -> Unavailable | None:
    """Отзыв токена установки (`DELETE /installation/token`, 204); `None` — отозван.
    Токен не той формы — отказ без запроса; текст отказа токена не несёт."""
    if TOKEN.fullmatch(token) is None:
        return Unavailable("токен установки не той формы")
    status, _data = http("DELETE", "/installation/token", f"token {token}")
    if status != 204:
        return Unavailable(f"отзыв токена установки: HTTP {status}")
    return None


def installations_refusal(
    app_id: int,
    key: Path,
    trust_repos: Mapping[str, int],
    *,
    http: Http = _http,
    now: int | None = None,
) -> Found:
    """Установка App, видящая репо доверия, — нарушение `(роль, деталь)` (§6.2:
    проверяется держатель ключа, не производный токен). Оба списка — со всеми
    страницами (ревизия b1, B1-12); неполный — `Unavailable`, не «чисто»."""
    bearer = _bearer(app_id, key, now)
    if isinstance(bearer, Unavailable):
        return bearer
    installations = _page_ids(http, "/app/installations", bearer, None)
    if isinstance(installations, Unavailable):
        return installations
    for ident in installations:
        token = _token(http, bearer, ident, {"permissions": {"metadata": "read"}})
        if isinstance(token, Unavailable):
            return token
        auth = f"token {token}"
        repos = _page_ids(http, "/installation/repositories", auth, "repositories")
        if isinstance(repos, Unavailable):
            return Unavailable(f"репо установки {ident}: {repos.reason}")
        for role, repo_id in trust_repos.items():
            if repo_id in repos:
                return role, f"установка App {ident} видит {role} ({repo_id})"
    return None


def main(argv: list[str] | None = None) -> int:
    """CLI для `merge-pr.sh`: две строки — `<slug>[bot]` и токен; иначе код 2.
    `--revoke` — отзыв токена из stdin (одна строка), без прочих аргументов."""
    p = argparse.ArgumentParser(prog="act_app_token")
    p.add_argument("--revoke", action="store_true")
    p.add_argument("--app-id", type=int)
    p.add_argument("--key", type=Path)
    p.add_argument("--repo")
    args = p.parse_args(argv)
    if args.revoke:
        if (args.app_id, args.key, args.repo) != (None, None, None):
            p.error("--revoke: токен только из stdin, без --app-id/--key/--repo")
        failed = revoke(sys.stdin.read().removesuffix("\n"))
        if failed is not None:
            print(f"act_app_token: {failed.reason}", file=sys.stderr)
            return 2
        return 0
    if args.app_id is None or args.key is None or args.repo is None:
        p.error("нужны --app-id, --key и --repo")
    got = app_identity(args.app_id, args.key, args.repo)
    if isinstance(got, Unavailable):
        print(f"act_app_token: {got.reason}", file=sys.stderr)
        return 2
    print(got[0])
    print(got[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
