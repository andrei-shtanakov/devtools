"""HTTP-адаптер вызовов App/JWT (спека среза 1, §4.5; решение владельца 14).

Перенаправления не выполняются: любой 3xx возвращается как ответ (исход
`moved`), повторного запроса по Location нет. `gh api` этого запретить не
даёт, поэтому для вызовов App не используется.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from conductor.app_calls import RateInfo

TIMEOUT = 30
FAILED_STATUS = frozenset({400, 401, 403, 404, 410, 422})
GQL_FAILED = frozenset({"NOT_FOUND", "FORBIDDEN", "UNPROCESSABLE"})
Outcome = Literal["ok", "failed", "rate_limited", "uncertain", "moved"]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(  # type: ignore[override]
        self, req, fp, code, msg, headers, newurl
    ):
        return None  # urllib поднимет HTTPError с кодом 3xx


_OPENER = urllib.request.build_opener(_NoRedirect)


@dataclass(frozen=True)
class Response:
    """Ответ сервера: статус, заголовки (нижний регистр), байты тела."""

    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        """Тело как JSON; пустое или битое — None."""
        try:
            return json.loads(self.body) if self.body else None
        except ValueError:
            return None


class TransportError(Exception):
    """Ответа нет (таймаут, обрыв, отказ соединения): исход неизвестен."""


Transport = Callable[[str, str, dict[str, str], bytes | None], Response]


def _lower(headers: Any) -> dict[str, str]:
    return {k.lower(): v for k, v in (headers or {}).items()}


def urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> Response:
    """Один запрос без следования перенаправлениям."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as resp:
            return Response(resp.status, _lower(resp.headers), resp.read())
    except urllib.error.HTTPError as exc:
        data = exc.read() if exc.fp is not None else b""
        return Response(exc.code, _lower(exc.headers), data)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise TransportError(type(exc).__name__) from exc


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def rate_info(headers: dict[str, str]) -> RateInfo:
    """Числовые параметры лимита (без остальных заголовков)."""
    return RateInfo(
        retry_after_s=_int(headers.get("retry-after")),
        reset=_int(headers.get("x-ratelimit-reset")),
        remaining=_int(headers.get("x-ratelimit-remaining")),
    )


def _rate_limited(resp: Response) -> bool:
    if resp.status not in (403, 429):
        return False
    info = rate_info(resp.headers)
    text = resp.body.decode("utf-8", "replace").lower()
    return (
        info.remaining == 0
        or info.retry_after_s is not None
        or "secondary rate limit" in text
    )


def classify(resp: Response, graphql: bool) -> Outcome:
    """Исход вызова по §4.5 (приоритет: moved → лимит → failed → прочее)."""
    if 300 <= resp.status < 400:
        return "moved"
    if _rate_limited(resp):
        return "rate_limited"
    if resp.status in FAILED_STATUS:
        return "failed"
    if not 200 <= resp.status < 300:
        return "uncertain"
    data = resp.json()
    if data is None:
        return "uncertain"
    if graphql:
        if not isinstance(data, dict):
            return "uncertain"
        errors = [e for e in data.get("errors") or [] if isinstance(e, dict)]
        types = {e.get("type") for e in errors}
        if "RATE_LIMITED" in types:
            return "rate_limited"
        if errors:
            return "failed" if types and types <= GQL_FAILED else "uncertain"
        if not data.get("data"):
            return "uncertain"
    return "ok"
