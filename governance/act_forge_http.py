"""Токен-транспорт GitHub REST/GraphQL для адаптера истории precheck.

Реализует `steward.approvalfacts.v3.transport.Transport`: только `GET` и
GraphQL-query; без перенаправлений и без прокси из окружения; тело ответа не
больше `act_anon.MAX_BODY`. Токен уходит только заголовком `Authorization:
Bearer` и не попадает в ошибки и `repr`. `send` не бросает исключений.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from governance.act_anon import MAX_BODY, _NoRedirect

#: Путь, который не относительный путь API (тот же класс, что у `GhTransport`).
_UNSAFE_PATH = re.compile(r"\A[-/]|://|\\|\s")
_NEXT = re.compile(r'<([^>]*)>\s*;\s*rel="next"')
#: Предел страниц `get_json`: защита от бесконечной цепочки `Link`.
MAX_PAGES = 100


def _default_open() -> Callable[..., Any]:
    # Пустой ProxyHandler: прокси окружения (и их credentials) не используются.
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirect
    ).open


class TokenTransport:
    """HTTPS-транспорт с токеном (`Authorization: Bearer`) к `api_base`."""

    def __init__(
        self,
        token: str,
        *,
        api_base: str,
        opener: Callable[..., Any] | None = None,
        timeout: float = 60.0,
    ) -> None:
        if not token:
            raise ValueError("forge: empty token")
        self._token = token
        self._base = api_base if api_base.endswith("/") else api_base + "/"
        self._open = opener or _default_open()
        self._timeout = timeout

    def __repr__(self) -> str:
        return f"TokenTransport(api_base={self._base!r})"

    def send(self, request: Any) -> Any:
        """Один запрос: `GET` относительного пути или GraphQL-query.

        Любой иной — `Response(None, error=…)` до сети. Ответ любого HTTP-статуса —
        `Response(status, body, link)`; сбой доставки — `status=None`.
        """
        from steward.approvalfacts.v3 import Response

        refusal = self._refusal(request)
        if refusal is not None:
            return Response(None, error=f"forge: {refusal}")
        if request.method == "GET":
            return self._fetch(self._base + request.path, None, Response)
        payload = {
            "query": request.query,
            "variables": dict(request.variables or {}),
            "operationName": request.op,
        }
        return self._fetch(self._base + "graphql", json.dumps(payload), Response)

    def get_json(self, path: str) -> tuple[int, object]:
        """`GET path` для REST-чтений адаптера: `(status, body)`.

        Список (JSON-массив) читается полностью: страницы по `Link rel="next"`
        склеиваются (не более `MAX_PAGES`); объект — одна страница. Не-200 —
        `(status, body)` без обхода. Сбой доставки, следующая страница вне
        `api_base` и превышение предела — `OSError` (без токена в тексте).
        """
        from steward.approvalfacts.v3 import Request

        url: str | None = path
        items: list[object] = []
        for _ in range(MAX_PAGES):
            resp = self.send(Request("GET", url or path))
            if resp.status is None:
                raise OSError(resp.error or "forge: transport failure")
            if resp.status != 200:
                return resp.status, resp.body
            if not isinstance(resp.body, list):
                return resp.status, resp.body
            items.extend(resp.body)
            url = self._next_path(resp.link)
            if url is None:
                return 200, items
        raise OSError(f"forge: more than {MAX_PAGES} pages")

    def _next_path(self, link: str | None) -> str | None:
        match = _NEXT.search(link or "")
        if match is None:
            return None
        url = match.group(1)
        if not url.startswith(self._base):
            raise OSError("forge: Link next outside api_base")
        return url[len(self._base) :]

    @staticmethod
    def _refusal(request: Any) -> str | None:
        method = getattr(request, "method", "")
        path = getattr(request, "path", "")
        if not isinstance(path, str) or not path or _UNSAFE_PATH.search(path):
            return f"not a relative API path: {path!r}"
        if method == "GET":
            return None
        query = getattr(request, "query", None)
        if method == "POST" and path == "graphql":
            if isinstance(query, str) and "mutation" not in query:
                return None
            return "GraphQL text is not a query"
        return f"read-only transport: {method} {path}"

    def _fetch(self, url: str, data: str | None, cls: Any) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "devtools-act-forge",
            "Authorization": f"Bearer {self._token}",
        }
        body = None if data is None else data.encode()
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            url, data=body, method="GET" if body is None else "POST", headers=headers
        )
        try:
            try:
                with self._open(req, timeout=self._timeout) as answer:
                    return self._response(
                        cls, answer.status, answer, answer.headers.get("Link")
                    )
            except urllib.error.HTTPError as err:
                link = err.headers.get("Link") if err.headers is not None else None
                return self._response(cls, err.code, err, link)
        except Exception as exc:  # noqa: BLE001 — send не бросает (инструкция steward)
            text = f"forge: {type(exc).__name__}: {exc}"
            return cls(None, error=text.replace(self._token, "***"))

    @staticmethod
    def _response(cls: Any, status: int, stream: Any, link: str | None) -> Any:
        raw = stream.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            return cls(None, error=f"forge: ответ больше {MAX_BODY} байт")
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = None
        return cls(status, body=parsed, link=link)
