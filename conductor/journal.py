"""Журнал прогона и учёт мутаций (спека среза 1, §4.7).

Журнал — диагностика, не операционное состояние: из него ничего не читается
при принятии решений. calls.jsonl — только интерфейс мутаций: строка intent
до отправки и result после; intent без result — «мог быть отправлен».
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from conductor.durable import append_line, read_jsonl

SECRET_RE = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{16,}"
    r"|github_pat_[A-Za-z0-9_]{16,}"
    r"|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
    r"|-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----",
    re.DOTALL,
)


def redact(value: Any) -> Any:
    """Вырезать токены, JWT и PEM из любых строк (рекурсивно)."""
    if isinstance(value, str):
        return SECRET_RE.sub("[REDACTED]", value)
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


class RunJournal:
    """journal.jsonl прогона."""

    def __init__(self, run_dir: Path) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        self.path = run_dir / "journal.jsonl"

    def write(self, row: dict[str, Any]) -> None:
        """Строка журнала без секретов."""
        append_line(self.path, redact(row))


class MutationLog:
    """calls.jsonl: intent/result каждой мутации, run-end в конце прогона."""

    def __init__(self, run_dir: Path) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        self.path = run_dir / "calls.jsonl"
        self._seq = 0

    def intent(
        self,
        *,
        attempt_id: str,
        method: str,
        endpoint: str,
        repo: str,
        marker_key: str,
        body: bytes | None,
    ) -> int:
        """Строка до отправки; сбой — OSError (мутации нет)."""
        self._seq += 1
        append_line(
            self.path,
            {
                "t": "intent",
                "seq": self._seq,
                "attempt_id": attempt_id,
                "method": method,
                "endpoint": endpoint,
                "repo": repo,
                "marker_key": marker_key,
                "body_sha256": hashlib.sha256(body).hexdigest() if body else None,
            },
        )
        return self._seq

    def result(self, seq: int, *, sent: bool, outcome: str, status: int | None) -> None:
        """Строка после вызова."""
        append_line(
            self.path,
            {
                "t": "result",
                "seq": seq,
                "sent": sent,
                "outcome": outcome,
                "status": status,
            },
        )

    def end_run(self) -> None:
        """Учёт прогона завершён."""
        append_line(self.path, {"t": "run-end"})


def log_complete(path: Path) -> bool:
    """Завершён: последняя строка run-end и у каждого intent есть result."""
    try:
        read = read_jsonl(path)
    except (OSError, ValueError):
        return False
    if read.truncated_tail or not read.rows or read.rows[-1]["t"] != "run-end":
        return False
    intents = {r["seq"] for r in read.rows if r["t"] == "intent"}
    results = {r["seq"] for r in read.rows if r["t"] == "result"}
    return intents <= results
