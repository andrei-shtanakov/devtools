"""Журнал вызовов установки App и запрет по лимиту (спека среза 1, §5.5).

Запрет не хранится отдельным значением — выводится из журнала: каждый вызов
токеном App/JWT — строки `begin` (до отправки) и `end` (после). Неизвестный
срок — 1 ч · 2^k, k по классу операций, сброс только успехом своего класса.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from conductor.durable import append_line, move_to_corrupt, read_jsonl, write_atomic
from conductor.opstate import iso, parse_ts

CLASSES = ("create", "update", "service")
ANY_CLASS = "*"
HOUR = timedelta(hours=1)
MAX_K = 3
HOST_INIT = "HOST_INIT"


@dataclass(frozen=True)
class RateInfo:
    """Числовые параметры лимита из заголовков ответа."""

    retry_after_s: int | None = None
    reset: int | None = None
    remaining: int | None = None


def journal_path(shared: Path, app_id: int, installation_id: int) -> Path:
    """Путь журнала установки — общий для всех профилей хоста."""
    return shared / f"app-calls-{app_id}-{installation_id}.jsonl"


def _dump(rows: list[dict[str, Any]]) -> str:
    return "".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows
    )


def init_host(shared: Path, app_id: int, installation_id: int, now: datetime) -> None:
    """init-state: журнал установки и HOST_INIT; существующее не перезаписывает.

    Пустой журнал создаётся только при ПЕРВОЙ регистрации хоста (нет
    HOST_INIT). Журнал, пропавший после неё, — потеря, а не новая установка:
    новый журнал начинается событием `lost` (запрет неизвестного срока, §5.5),
    иначе init-state нового профиля стёр бы действующий запрет флота.
    """
    shared.mkdir(parents=True, exist_ok=True)
    path = journal_path(shared, app_id, installation_id)
    registered = (shared / HOST_INIT).exists()
    if not path.exists():
        lost = [{"t": "lost", "at": iso(now)}] if registered else []
        write_atomic(path, _dump(lost))
    if not registered:
        write_atomic(shared / HOST_INIT, json.dumps({"created_at": iso(now)}))


def _known_term(now: datetime, outcome: str, rate: RateInfo) -> datetime | None:
    if outcome == "rate_limited" and rate.retry_after_s is not None:
        return now + timedelta(seconds=rate.retry_after_s)
    if outcome in ("rate_limited", "ok") and rate.remaining == 0 and rate.reset:
        return datetime.fromtimestamp(rate.reset, UTC)
    return None


def derive(rows: list[dict[str, Any]]) -> datetime | None:
    """Действующий запрет: максимум известных сроков и сроков неизвестных."""
    k = dict.fromkeys(CLASSES, 0)
    until: datetime | None = None
    opened: dict[int, dict[str, Any]] = {}

    def push(term: datetime) -> None:
        nonlocal until
        until = term if until is None else max(until, term)

    def unknown(at: str, klass: str) -> None:
        classes = CLASSES if klass not in k else (klass,)
        level = max(k[c] for c in classes)
        for c in classes:
            k[c] = min(k[c] + 1, MAX_K)
        push(parse_ts(at) + HOUR * 2 ** min(level, MAX_K))

    for row in rows:
        kind = row["t"]
        if kind == "begin":
            opened[row["seq"]] = row
        elif kind == "end":
            begin = opened.pop(row["seq"], None) or {}
            klass = row.get("class") or begin.get("class", ANY_CLASS)
            if row["outcome"] == "ok" and klass in k:
                k[klass] = 0
            if row.get("blocked_until"):
                push(parse_ts(row["blocked_until"]))
            if row.get("unknown_term"):
                unknown(row["at"], klass)
        elif kind in ("lost", "orphan"):
            unknown(row["at"], ANY_CLASS)
    for begin in opened.values():
        unknown(begin["at"], begin["class"])
    return until


class AppCalls:
    """Журнал вызовов одной установки App на хосте."""

    def __init__(self, path: Path, rows: list[dict[str, Any]]) -> None:
        self.path = path
        self.rows = rows
        self._seq = max((r.get("seq", 0) for r in rows), default=0)

    @classmethod
    def open(
        cls, shared: Path, app_id: int, installation_id: int, now: datetime
    ) -> tuple[AppCalls | None, str | None]:
        """Открыть журнал; утрата/порча — новый журнал с событием `lost`."""
        path = journal_path(shared, app_id, installation_id)
        if not (shared / HOST_INIT).is_file():
            return None, "OPSTATE-UNINITIALIZED"
        try:
            if not path.is_file():
                return cls._restart(path, now)
            read = read_jsonl(path)
        except (OSError, ValueError):
            try:
                move_to_corrupt(shared, [path.name], now.strftime("%Y%m%dT%H%M%SZ"))
                return cls._restart(path, now)
            except OSError:
                return None, "RATE-STATE-LOST"
        rows = read.rows
        if read.truncated_tail:
            try:  # починка хвоста не удалась — как утрата журнала (ревью #538)
                mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC)
                rows = [*rows, {"t": "orphan", "at": iso(mtime)}]
                write_atomic(path, _dump(rows))
            except OSError:
                return None, "RATE-STATE-LOST"
        return cls(path, rows), None

    @classmethod
    def _restart(cls, path: Path, now: datetime) -> tuple[AppCalls | None, str]:
        rows = [{"t": "lost", "at": iso(now)}]
        try:
            write_atomic(path, _dump(rows))
        except OSError:
            return None, "RATE-STATE-LOST"
        return cls(path, rows), "RATE-STATE-LOST"

    def blocked_until(self) -> datetime | None:
        """Срок действующего запрета или None."""
        return derive(self.rows)

    def begin(self, klass: str, now: datetime) -> int:
        """Строка begin до отправки; сбой — OSError (вызова нет)."""
        self._seq += 1
        row = {"t": "begin", "seq": self._seq, "class": klass, "at": iso(now)}
        append_line(self.path, row)
        self.rows.append(row)
        return self._seq

    def end(
        self,
        seq: int,
        now: datetime,
        outcome: str,
        status: int | None,
        rate: RateInfo,
        klass: str,
    ) -> None:
        """Строка end: исход и числовые параметры лимита (без заголовков)."""
        term = _known_term(now, outcome, rate)
        row = {
            "t": "end",
            "seq": seq,
            "class": klass,
            "at": iso(now),
            "outcome": outcome,
            "status": status,
            "retry_after_s": rate.retry_after_s,
            "ratelimit_reset": rate.reset,
            "ratelimit_remaining": rate.remaining,
            "blocked_until": iso(term) if term else None,
            "unknown_term": outcome == "rate_limited" and term is None,
        }
        append_line(self.path, row)
        self.rows.append(row)
