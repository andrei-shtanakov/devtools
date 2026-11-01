"""Анонимный REST-транспорт источников доверия (спека §6.2, steward §12 п. 1).

Реализация протокола `steward.approvalfacts.v3.Transport` БЕЗ credentials:
`GET https://api.github.com/<path>` без `Authorization`, без перенаправлений,
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
    return urllib.request.build_opener(_NoRedirect).open


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
    """`Transport` steward без credentials; `sent` — пути, ушедшие в сеть."""

    def __init__(
        self, *, open_url: Callable[..., Any] | None = None, timeout: float = TIMEOUT
    ) -> None:
        self._open = open_url or _default_open()
        self._timeout = timeout
        self._memo: dict[str, Any] = {}
        self.sent: list[str] = []

    def send(self, request: Any) -> Any:
        """Один анонимный `GET`; не-`GET` и не относительный путь — `status=None`."""
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
            return self._memo[path]
        resp = self._fetch(path, Response)
        if resp.status == 200 and _IMMUTABLE.match(path):
            self._memo[path] = resp
        return resp

    def _fetch(self, path: str, cls: Any) -> Any:
        self.sent.append(path)
        req = urllib.request.Request(API + path, method="GET", headers=_HEADERS)
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
            return cls(None, error=f"anon: {type(exc).__name__}: {exc}")


def contents_bytes(
    transport: Any, repo: str, path: str, ref: str
) -> bytes | None | Unavailable:
    """Байты `path` в коммите `ref`; `None` — установленно нет (404).

    `ref` — SHA уже прочитанного коммита: 404 тогда значит «пути нет», а не
    «нет коммита». Содержимое обязано хешироваться в git-oid `sha` ответа.
    """
    from steward.approvalfacts.v3 import Request

    owner, _, name = repo.partition("/")
    api = (
        f"repos/{quote(owner, safe='')}/{quote(name, safe='')}/contents/"
        f"{quote(path)}?ref={ref}"
    )
    where = f"{repo}@{ref[:12]}:{path}"
    resp = transport.send(Request("GET", api))
    if resp.status == 404:
        return None
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
    if hashlib.sha1(b"blob %d\x00" % len(raw) + raw).hexdigest() != body.get("sha"):
        return Unavailable(f"{where}: содержимое не равно blob {body.get('sha')!r}")
    return raw
