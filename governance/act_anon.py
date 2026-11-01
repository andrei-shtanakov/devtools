"""REST-транспорт источников доверия (спека rev 18 §6.2, steward §12 п. 1).

Реализация протокола `steward.approvalfacts.v3.Transport` без credentials либо
с read-only токеном истории (спека rev 18 §6.2; строит только
`act_trust.history_transport`): `GET https://api.github.com/<path>` без
`Authorization` (с токеном — только `Authorization: Bearer`), без перенаправлений,
с пределом размера ответа. Любой HTTP-ответ — `Response(status, body,
link)`, классификацию делает библиотека; сбой доставки — `Response(None,
error=…)`; исключений `send` не бросает (инструкция steward, «Транспорт»).

Кэш (решение плана Р-3) — только в памяти процесса и только для путей,
неизменных по SHA: подменённый на диске ответ коммита скрыл бы прямой push,
а содержимое коммита по REST-ответу не проверить.
"""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from governance.act_time import Unavailable

API = "https://api.github.com/"
MAX_BODY = 32 << 20
TIMEOUT = 30.0
#: Путь, который не относительный путь API: флаг, абсолютный, вложенный URL,
#: обратный слэш, пробельный символ (тот же класс, что у `GhTransport`).
_UNSAFE_PATH = re.compile(r"\A[-/]|://|\\|\s")
_SHA = re.compile(r"[0-9a-f]{40}")
_IMMUTABLE = re.compile(
    r"\Arepos/[^/?]+/[^/?]+/(?:commits/[0-9a-f]{40}"
    r"|git/trees/[0-9a-f]{40}(?:\?recursive=1)?"
    r"|git/blobs/[0-9a-f]{40}"
    r"|contents/[^?]+\?ref=[0-9a-f]{40})\Z"
)
_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "devtools-act-anon",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _default_open() -> Callable[..., Any]:
    # Пустой ProxyHandler: прокси из окружения (и их credentials) не используются.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    return opener.open


def _response(cls: Any, status: int, stream: Any, link: str | None) -> Any:
    raw = stream.read(MAX_BODY + 1)
    if len(raw) > MAX_BODY:
        return cls(None, error=f"anon: ответ больше {MAX_BODY} байт")
    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        body = None
    return cls(status, body=body, link=link)


class AnonTransport:
    """`Transport` steward источников доверия: без credentials либо с read-only
    токеном истории (спека rev 18 §6.2); строит только
    `act_trust.history_transport`; `sent` — пути, ушедшие в сеть."""

    def __init__(
        self,
        *,
        open_url: Callable[..., Any] | None = None,
        timeout: float = TIMEOUT,
        token: str | None = None,
    ) -> None:
        self._open = open_url or _default_open()
        self._timeout = timeout
        self._token = token
        auth = {} if token is None else {"Authorization": f"Bearer {token}"}
        self._headers = _HEADERS | auth
        self._memo: dict[str, Any] = {}
        self.sent: list[str] = []

    def send(self, request: Any) -> Any:
        """Один `GET` (анонимно либо с токеном истории); не-`GET` и не относительный путь — `status=None`."""
        from steward.approvalfacts.v3 import Response

        path = getattr(request, "path", "")
        if (
            getattr(request, "method", "") != "GET"
            or not isinstance(path, str)
            or not path
            or _UNSAFE_PATH.search(path)
        ):
            return Response(
                None, error=f"anon: только GET относительного пути: {path!r}"
            )
        if path in self._memo:
            return copy.deepcopy(self._memo[path])
        resp = self._fetch(path, Response)
        if resp.status == 200 and _IMMUTABLE.match(path):
            self._memo[path] = copy.deepcopy(resp)
        return resp

    def _fetch(self, path: str, cls: Any) -> Any:
        self.sent.append(path)
        req = urllib.request.Request(API + path, method="GET", headers=self._headers)
        try:
            try:
                with self._open(req, timeout=self._timeout) as answer:
                    return _response(
                        cls, answer.status, answer, answer.headers.get("Link")
                    )
            except urllib.error.HTTPError as err:
                link = err.headers.get("Link") if err.headers is not None else None
                return _response(cls, err.code, err, link)
        except Exception as exc:  # noqa: BLE001 — send не бросает (инструкция steward)
            return cls(None, error=self._mask(f"anon: {type(exc).__name__}: {exc}"))

    def _mask(self, text: str) -> str:
        """Значение токена в тексте сбоя — `***` (R1)."""
        return text if self._token is None else text.replace(self._token, "***")


_REPO = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")


def _tree_entry(transport: Any, repo: str, tree: str, name: str, where: str) -> Any:
    """Запись `name` в полностью прочитанном дереве; `None` — её там нет."""
    from steward.approvalfacts.v3 import Request

    resp = transport.send(Request("GET", f"repos/{repo}/git/trees/{tree}"))
    body = resp.body
    if resp.status != 200 or not isinstance(body, dict):
        return Unavailable(f"{where}: дерево {tree[:12]}: HTTP {resp.status}")
    entries = body.get("tree")
    if body.get("truncated") is not False or not isinstance(entries, list):
        return Unavailable(f"{where}: дерево {tree[:12]} прочитано не полностью")
    for entry in entries:
        if isinstance(entry, dict) and entry.get("path") == name:
            return entry
    return None


def _locate(transport: Any, repo: str, parts: list[str], ref: str, where: str) -> Any:
    """`None` — пути нет в полном дереве коммита; строка — blob-oid файла из дерева."""
    from steward.approvalfacts.v3 import Request

    resp = transport.send(Request("GET", f"repos/{repo}/commits/{ref}"))
    commit = resp.body.get("commit") if isinstance(resp.body, dict) else None
    tree = commit.get("tree") if isinstance(commit, dict) else None
    sha = tree.get("sha") if isinstance(tree, dict) else None
    if resp.status != 200 or not isinstance(sha, str) or not _SHA.fullmatch(sha):
        return Unavailable(f"{where}: коммит не прочитан: HTTP {resp.status}")
    for depth, name in enumerate(parts):
        entry = _tree_entry(transport, repo, sha, name, where)
        if entry is None or isinstance(entry, Unavailable):
            return entry
        last = depth == len(parts) - 1
        kind, sha = entry.get("type"), str(entry.get("sha"))
        if kind == "blob" and last:
            if not _SHA.fullmatch(sha):
                return Unavailable(f"{where}: blob-oid {name!r} не SHA")
            return sha
        if kind == "blob":
            return None
        if kind != "tree" or last or not _SHA.fullmatch(sha):
            return Unavailable(f"{where}: запись дерева {name!r} типа {kind!r}")
    return None


def contents_bytes(
    transport: Any, repo: str, path: str, ref: str
) -> bytes | None | Unavailable:
    """Байты `path` в коммите `ref`; `None` — пути нет в полном дереве коммита.

    Отсутствие устанавливается только деревом (`truncated = false`), прочитанным
    по сегментам пути; HTTP 404 никогда не «установленное отсутствие» (контракт
    steward). `ref` — 40-hex SHA. Содержимое обязано хешироваться в blob-oid записи дерева коммита.
    """
    from steward.approvalfacts.v3 import Request

    where = f"{repo}@{ref[:12]}:{path}"
    parts = path.split("/")
    if not _REPO.fullmatch(repo) or not _SHA.fullmatch(ref) or not all(parts):
        return Unavailable(f"{where}: repo/path/ref не в форме owner/name, путь, SHA")
    found = _locate(transport, repo, parts, ref, where)
    if not isinstance(found, str):
        return found
    api = f"repos/{repo}/contents/{quote(path)}?ref={ref}"
    resp = transport.send(Request("GET", api))
    body = resp.body
    if resp.status != 200 or not isinstance(body, dict):
        return Unavailable(f"{where}: HTTP {resp.status} {resp.error or ''}".rstrip())
    if body.get("type") != "file" or body.get("encoding") != "base64":
        return Unavailable(f"{where}: не файл base64 (type={body.get('type')!r})")
    try:
        raw = base64.b64decode(
            str(body.get("content", "")).replace("\n", ""), validate=True
        )
    except (binascii.Error, ValueError):
        return Unavailable(f"{where}: содержимое не base64")
    # Привязка к коммиту `ref` — blob-oid записи дерева, не самоотчёт ответа contents.
    oid = hashlib.sha1(b"blob %d\x00" % len(raw) + raw).hexdigest()
    if oid != found or body.get("sha") != found:
        return Unavailable(f"{where}: содержимое не равно blob дерева {found!r}")
    return raw
