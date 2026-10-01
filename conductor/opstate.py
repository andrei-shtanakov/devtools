"""Операционное состояние писателя (спека среза 1, §3, §4.6, §5.3–5.4).

I7: состояние не даёт полномочий и не доказывает выполнения — только
защитные запреты и задержки и вопросы об операционных сбоях.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from conductor.durable import append_line, move_to_corrupt, read_jsonl, write_atomic

INIT = "INIT"
ATTEMPTS = "attempts.jsonl"
FAILURES = "failures.json"
QUARANTINE_FILE = "quarantine.json"
FILES = [ATTEMPTS, FAILURES, QUARANTINE_FILE]
QUARANTINE = timedelta(minutes=65)
RETRY_DELAY = timedelta(minutes=60)
OPEN = frozenset({"in_flight", "uncertain"})
STATE_ERRORS = (OSError, ValueError, KeyError, TypeError)


def iso(dt: datetime) -> str:
    """RFC 3339 UTC с суффиксом Z."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: str) -> datetime:
    """Разбор RFC 3339 (Z допустим)."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _stamp(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


class StateError(Exception):
    """Команда над состоянием отвергнута (init на непустом, recover при INIT)."""


@dataclass(frozen=True)
class Episode:
    """Эпизод сбоя (§5.4): серия ≥ 2; id — первый прогон серии."""

    mutation_id: str
    first_run: str
    runs: tuple[str, ...]
    error: str
    times: tuple[str, ...]
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Opened:
    """Результат открытия: state=None — записей нет; finding — находка."""

    state: OpState | None
    finding: str | None


def _fresh(directory: Path, generation: int, now: datetime, unknown: bool) -> None:
    """Шаги 2–3 восстановления: карантин, затем пустые файлы поколения."""
    write_atomic(
        directory / QUARANTINE_FILE,
        json.dumps(
            {
                "generation": generation,
                "started_at": iso(now),
                "unknown_previous": unknown,
            }
        ),
    )
    header = json.dumps({"t": "header", "generation": generation})
    write_atomic(directory / ATTEMPTS, header + "\n")
    write_atomic(
        directory / FAILURES, json.dumps({"generation": generation, "series": {}})
    )


def _write_init(directory: Path, now: datetime) -> None:
    write_atomic(directory / INIT, json.dumps({"created_at": iso(now)}))


def _previous_generation(directory: Path) -> int | None:
    for name in (QUARANTINE_FILE, FAILURES):
        try:
            value = json.loads((directory / name).read_text(encoding="utf-8"))
            generation = value["generation"]
        except STATE_ERRORS:
            continue
        if isinstance(generation, int):
            return generation
    return None


def init_state(state_dir: Path, now: datetime) -> None:
    """Первый запуск: пустое состояние поколения 1 и карантин; INIT последним."""
    state_dir.mkdir(parents=True, exist_ok=True)
    if any(state_dir.iterdir()):
        raise StateError(f"{state_dir} не пуст: нужен init-state --recover")
    _fresh(state_dir, 1, now, unknown=False)
    _write_init(state_dir, now)


def recover_state(state_dir: Path, now: datetime) -> None:
    """Непустой каталог без INIT: всё в corrupt/, новое поколение, карантин."""
    if (state_dir / INIT).exists():
        raise StateError("INIT есть: восстановление выполняет обычный run")
    if not state_dir.is_dir() or not any(state_dir.iterdir()):
        raise StateError(f"{state_dir} пуст: нужен init-state")
    previous = _previous_generation(state_dir)
    names = [p.name for p in state_dir.iterdir() if p.name != "corrupt"]
    move_to_corrupt(state_dir, names, _stamp(now))
    _fresh(state_dir, (previous or 0) + 1, now, unknown=previous is None)
    _write_init(state_dir, now)


def open_state(state_dir: Path, now: datetime) -> Opened:
    """Открыть состояние; порча — восстановление с карантином (§4.6)."""
    if not (state_dir / INIT).is_file():
        return Opened(None, "OPSTATE-UNINITIALIZED")
    try:
        return Opened(OpState.load(state_dir), None)
    except STATE_ERRORS:
        pass
    previous = _previous_generation(state_dir)
    try:
        move_to_corrupt(state_dir, FILES, _stamp(now))
        _fresh(state_dir, (previous or 0) + 1, now, unknown=previous is None)
        return Opened(OpState.load(state_dir), "OPSTATE-LOST")
    except STATE_ERRORS:
        return Opened(None, "OPSTATE-LOST")


def _dump_rows(rows: list[dict[str, Any]]) -> str:
    return "".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows
    )


class OpState:
    """Попытки, серии сбоев и карантин одного профиля."""

    def __init__(
        self,
        directory: Path,
        generation: int,
        quarantine_started: datetime,
        attempts: dict[str, dict[str, Any]],
        series: dict[str, dict[str, Any]],
    ) -> None:
        self.directory = directory
        self.generation = generation
        self.quarantine_started = quarantine_started
        self.attempts = attempts
        self.series = series
        self._touched: set[str] = set()

    @classmethod
    def load(cls, directory: Path) -> OpState:
        """Прочитать и сверить поколения; любая порча — исключение."""
        json.loads((directory / INIT).read_text(encoding="utf-8"))
        quarantine = json.loads(
            (directory / QUARANTINE_FILE).read_text(encoding="utf-8")
        )
        failures = json.loads((directory / FAILURES).read_text(encoding="utf-8"))
        read = read_jsonl(directory / ATTEMPTS)
        header = read.rows[0]
        generation = quarantine["generation"]
        if header.get("t") != "header" or not (
            header["generation"] == failures["generation"] == generation
        ):
            raise ValueError("поколения состояния не совпадают")
        if not isinstance(failures["series"], dict):
            raise ValueError("series не объект")
        if read.truncated_tail:  # оборванная строка: попытка не отправлялась
            write_atomic(directory / ATTEMPTS, _dump_rows(read.rows))
        attempts: dict[str, dict[str, Any]] = {}
        for row in read.rows[1:]:
            if row["t"] == "begin":
                attempts[row["attempt_id"]] = {**row, "status": "in_flight"}
            elif row["t"] == "end":
                attempts[row["attempt_id"]]["status"] = row["outcome"]
            else:
                raise ValueError(f"неизвестная строка {row['t']!r}")
        return cls(
            directory,
            generation,
            parse_ts(quarantine["started_at"]),
            attempts,
            failures["series"],
        )

    def quarantined(self, now: datetime) -> bool:
        """Карантин 65 мин от начала; часы назад его не сокращают."""
        return now < self.quarantine_started + QUARANTINE

    def begin_attempt(
        self,
        *,
        attempt_id: str,
        mutation_id: str,
        effect_key: str,
        target: str,
        marker_key: str,
        action: str,
        subject: str,
        now: datetime,
        op: str = "",
        expected: str = "",
    ) -> None:
        """Устойчивая запись попытки ДО отправки; сбой — OSError (мутации нет).

        op и expected — идентичность эффекта для поиска после обрыва (§5.3).
        """
        row = {
            "t": "begin",
            "attempt_id": attempt_id,
            "mutation_id": mutation_id,
            "effect_key": effect_key,
            "target": target,
            "marker_key": marker_key,
            "action": action,
            "subject": subject,
            "op": op,
            "expected": expected,
            "started_at": iso(now),
        }
        append_line(self.directory / ATTEMPTS, row)
        self.attempts[attempt_id] = {**row, "status": "in_flight"}

    def finish_attempt(self, attempt_id: str, outcome: str, now: datetime) -> None:
        """Исход попытки после проверки."""
        append_line(
            self.directory / ATTEMPTS,
            {"t": "end", "attempt_id": attempt_id, "outcome": outcome, "at": iso(now)},
        )
        self.attempts[attempt_id]["status"] = outcome

    def delayed(self, effect_key: str, now: datetime) -> bool:
        """Есть in_flight/uncertain попытка этого эффекта моложе 60 мин (§5.3)."""
        return any(
            a["effect_key"] == effect_key
            and a["status"] in OPEN
            and now < parse_ts(a["started_at"]) + RETRY_DELAY
            for a in self.attempts.values()
        )

    def open_attempts(self) -> list[dict[str, Any]]:
        """Незавершённые попытки (in_flight/uncertain), по одной на эффект."""
        seen: dict[str, dict[str, Any]] = {}
        for a in self.attempts.values():
            if a["status"] in OPEN:
                seen.setdefault(a["effect_key"], a)
        return list(seen.values())

    def settle(self, effect_key: str, now: datetime) -> None:
        """Результат найден по идентичности: открытые попытки эффекта — ok."""
        for attempt_id, a in list(self.attempts.items()):
            if a["effect_key"] == effect_key and a["status"] in OPEN:
                self.finish_attempt(attempt_id, "ok", now)

    def series_event(
        self,
        mutation_id: str,
        event: str,
        run_id: str,
        now: datetime,
        error: str = "",
        detail: dict[str, Any] | None = None,
    ) -> int:
        """Переход серии по таблице §5.4; возвращает длину серии."""
        self._touched.add(mutation_id)
        current = self.series.get(mutation_id)
        if event == "ok":
            if self.series.pop(mutation_id, None) is not None:
                self._persist()
            return 0
        if event != "failed":  # uncertain / rate_limited / delay — без изменений
            return current["count"] if current else 0
        entry = current or {"count": 0, "runs": [], "times": [], "error": ""}
        if run_id not in entry["runs"]:
            entry["count"] += 1
            entry["runs"].append(run_id)
            entry["times"].append(iso(now))
        entry["error"], entry["detail"] = error, detail or {}
        self.series[mutation_id] = entry
        self._persist()
        return entry["count"]

    def end_run(self, now: datetime) -> None:
        """Ключи, которых прогон не касался, рвут серию (§5.4)."""
        for mutation_id in list(self.series):
            if mutation_id not in self._touched:
                del self.series[mutation_id]
        self._touched = set()
        self._persist()

    def episodes(self) -> list[Episode]:
        """Открытые эпизоды сбоев (серия ≥ 2)."""
        return [
            Episode(
                mid,
                s["runs"][0],
                tuple(s["runs"]),
                s.get("error", ""),
                tuple(s["times"]),
                s.get("detail") or {},
            )
            for mid, s in sorted(self.series.items())
            if s["count"] >= 2
        ]

    def _persist(self) -> None:
        write_atomic(
            self.directory / FAILURES,
            json.dumps(
                {"generation": self.generation, "series": self.series},
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
