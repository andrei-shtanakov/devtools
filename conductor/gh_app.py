"""Клиент GitHub App (спека среза 1, §4.3; О §8.2).

Единственный путь вызовов токеном App или JWT: перед каждым — запрет по
лимиту (§5.5), строка begin журнала установки, затем HTTP-адаптер без
перенаправлений (§4.5) и строка end. Секреты живут только в памяти.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import jwt as pyjwt

from conductor.app_calls import AppCalls, RateInfo
from conductor.host_config import HostConfig
from conductor.http import (
    Outcome,
    Response,
    Transport,
    TransportError,
    classify,
    rate_info,
    urllib_transport,
)
from conductor.opstate import iso, parse_ts

API = "https://api.github.com"
TOKEN_MARGIN = timedelta(minutes=10)
Auth = Literal["jwt", "token"]


class Blocked(Exception):
    """Вызов не отправлен: запрет по лимиту, нет токена или журнала."""


class JournalLost(Exception):
    """Вызов мог уйти, но строка end не записана: исход неизвестен."""


@dataclass(frozen=True)
class CallResult:
    """Исход вызова и ответ (None — ответа нет)."""

    outcome: Outcome
    response: Response | None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _read_key(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class AppClient:
    """Вызовы GitHub API от имени App."""

    def __init__(
        self,
        cfg: HostConfig,
        calls: AppCalls,
        transport: Transport = urllib_transport,
        clock: Callable[[], datetime] = _utcnow,
        read_key: Callable[[Path], str] = _read_key,
    ) -> None:
        self._cfg = cfg
        self._calls = calls
        self._transport = transport
        self._clock = clock
        self._read_key = read_key
        self._token: str | None = None
        self._expires: datetime | None = None
        self.bot_login: str | None = None

    def jwt(self) -> str:
        """Свежий JWT RS256 (iat − 60 с, exp + 9 мин)."""
        try:
            key = self._read_key(self._cfg.private_key)
        except OSError as exc:
            raise Blocked("ключ App не прочитан") from exc
        now = int(self._clock().timestamp())
        claims = {"iat": now - 60, "exp": now + 540, "iss": str(self._cfg.app_id)}
        return pyjwt.encode(claims, key, algorithm="RS256")

    def token(self) -> str | None:
        """Токен установки из памяти; обновление, если до истечения < 10 мин."""
        now = self._clock()
        if self._token and self._expires and self._expires - now > TOKEN_MARGIN:
            return self._token
        self._token = None
        path = f"/app/installations/{self._cfg.installation_id}/access_tokens"
        result = self.call("service", "POST", path, auth="jwt")
        data = (
            result.response.json()
            if result.outcome == "ok" and result.response
            else None
        )
        if not isinstance(data, dict) or "token" not in data:
            return None
        self._token, self._expires = data["token"], parse_ts(data["expires_at"])
        return self._token

    def call(
        self,
        klass: str,
        method: str,
        path: str,
        *,
        auth: Auth,
        body: dict | None = None,
        graphql: bool = False,
    ) -> CallResult:
        """Один вызов: запрет → учётные данные → begin → транспорт → end."""
        now = self._clock()
        until = self._calls.blocked_until()
        if until is not None and now < until:
            raise Blocked(f"запрет по лимиту до {iso(until)}")
        credential = self.jwt() if auth == "jwt" else self.token()
        if credential is None:
            raise Blocked("нет токена установки")
        headers = {
            "Authorization": f"Bearer {credential}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "conductor",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            seq = self._calls.begin(klass, now)
        except OSError as exc:
            raise Blocked("журнал вызовов установки не записан") from exc
        try:
            resp: Response | None = self._transport(method, API + path, headers, data)
        except TransportError:
            resp = None
        outcome: Outcome = classify(resp, graphql) if resp is not None else "uncertain"
        info = rate_info(resp.headers) if resp is not None else RateInfo()
        try:
            self._calls.end(
                seq, self._clock(), outcome, resp.status if resp else None, info, klass
            )
        except OSError as exc:
            raise JournalLost("строка end не записана") from exc
        return CallResult(outcome, resp)

    def check_key(self) -> str | None:
        """Шаг 5 §5.1: свежий JWT и GET /app; логин бота или None."""
        result = self.call("service", "GET", "/app", auth="jwt")
        data = (
            result.response.json()
            if result.outcome == "ok" and result.response
            else None
        )
        if not isinstance(data, dict) or data.get("id") != self._cfg.app_id:
            return None
        self.bot_login = f"{data['slug']}[bot]"
        return self.bot_login

    def check_installation(self, owner: str) -> bool:
        """О §8.2 на старте: установка существует и принадлежит владельцу."""
        path = f"/app/installations/{self._cfg.installation_id}"
        result = self.call("service", "GET", path, auth="jwt")
        data = (
            result.response.json()
            if result.outcome == "ok" and result.response
            else None
        )
        return (
            isinstance(data, dict) and (data.get("account") or {}).get("login") == owner
        )

    def covers(self, repo: str) -> bool | None:
        """Шаг 7 §5.1: свежий запрос покрытия репо установкой."""
        result = self.call("service", "GET", f"/repos/{repo}/installation", auth="jwt")
        if (
            result.outcome == "failed"
            and result.response
            and result.response.status == 404
        ):
            return False
        if result.outcome != "ok" or result.response is None:
            return None
        data = result.response.json()
        return isinstance(data, dict) and data.get("id") == self._cfg.installation_id
