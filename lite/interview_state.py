"""Состояние сессии интервью из `discovery` (зонд `lite/interview_probe.py`)."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from governance import df_config

Runner = Callable[..., subprocess.CompletedProcess]

PROBE = Path(__file__).resolve().parent / "interview_probe.py"
_ID_RE = re.compile(r"^([A-Z]+)-(\d+)$")


class InterviewStateError(Exception):
    """Состояние сессии не получено; текст — причина."""


@dataclass(frozen=True)
class Entry:
    """Действующий пункт интервью."""

    id: str
    body: str
    fields: dict[str, Any]


@dataclass(frozen=True)
class SessionView:
    """Снимок сессии `discovery`: фрейм, темы, пункты, ответы, готовность.

    `answers` упорядочены по последнему ответу; `asked` — тема заданных
    вопросов (question_id → coverage_key)."""

    frame: str
    required: dict[str, str | None]
    optional: dict[str, str | None]
    entries: list[Entry]
    declared: list[str]
    holders: dict[str, str]
    answers: dict[str, dict[str, Any]]
    answered_keys: list[str]
    readiness: str
    findings: list[str] = field(default_factory=list)
    asked: dict[str, str] = field(default_factory=dict)

    def prefixes(self) -> dict[str, str]:
        """prefix → coverage_key (темы без префикса не входят)."""
        topics = {**self.required, **self.optional}
        return {p: k for k, p in topics.items() if p is not None}

    def covered(self) -> set[str]:
        """Покрытые темы: есть пункт с префиксом темы либо (тема без префикса)
        на её вопрос есть ответ."""
        present = {e.id.rsplit("-", 1)[0] for e in self.entries}
        return {
            key
            for key, prefix in {**self.required, **self.optional}.items()
            if (prefix in present if prefix is not None else key in self.answered_keys)
        }

    def entry_record(self, eid: str) -> dict[str, Any]:
        """Запись пункта в ответе держателя целиком (с неизвестными dfl
        полями); нет её там — собранная из действующего пункта."""
        answer = self.answers.get(self.holders.get(eid, ""), {})
        for entry in answer.get("entries") or []:
            if isinstance(entry, dict) and entry.get("id") == eid:
                return dict(entry)
        current = next(e for e in self.entries if e.id == eid)
        return {"id": eid, "body": current.body, **current.fields}

    def held_answer(self, question_id: str) -> dict[str, Any]:
        """Копия ответа на вопрос только с теми записями, которые он сейчас
        держит: пункт, переобъявленный позже другим ответом, при пересылке
        этого ответа не должен вернуться к старой версии."""
        answer = self.answers.get(question_id, {})
        copy = dict(answer)
        copy["entries"] = [
            dict(e)
            for e in answer.get("entries") or []
            if isinstance(e, dict) and self.holders.get(str(e.get("id"))) == question_id
        ]
        return copy

    def uncovered_required(self) -> list[str]:
        """Обязательные темы без покрытия (в порядке фрейма)."""
        covered = self.covered()
        return [key for key in self.required if key not in covered]

    def next_id(self, prefix: str) -> str:
        """Следующий свободный id: после всех когда-либо объявленных."""
        used = [
            int(m.group(2))
            for i in (*self.declared, *(e.id for e in self.entries))
            if (m := _ID_RE.match(i)) and m.group(1) == prefix
        ]
        return f"{prefix}-{max(used, default=0) + 1:02d}"


def _probe_argv(session_id: str) -> list[str]:
    """argv зонда закреплённым окружением `discovery`."""
    try:
        project = df_config.workspace_dir("discovery")
    except df_config.ConfigError as exc:
        raise InterviewStateError(f"конфиг DarkFactory: {exc}") from exc
    return [
        "uv", "run", "--frozen", "--project", str(project),
        "python", "-I", str(PROBE), session_id,
    ]  # fmt: skip


def _parse(raw: dict[str, Any]) -> SessionView:
    """JSON зонда → `SessionView`."""
    entries = [Entry(e["id"], e["body"], e["fields"]) for e in raw["entries"]]
    return SessionView(
        frame=raw["frame"],
        required=raw["required"],
        optional=raw["optional"],
        entries=entries,
        declared=raw["declared"],
        holders=raw["holders"],
        answers=raw["answers"],
        answered_keys=raw["answered_keys"],
        readiness=raw["readiness"],
        findings=raw["findings"],
        asked=raw["asked"],
    )


def load_session(session_id: str, run: Runner = subprocess.run) -> SessionView:
    """Состояние сессии; сбой зонда или разбора — `InterviewStateError`."""
    argv = _probe_argv(session_id)
    try:
        done = run(argv, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise InterviewStateError(f"зонд не запущен: {exc}") from exc
    if done.returncode != 0:
        reason = (done.stderr or "").strip() or f"код {done.returncode}"
        raise InterviewStateError(f"зонд сессии {session_id}: {reason}")
    try:
        return _parse(json.loads(done.stdout))
    except (ValueError, KeyError, TypeError) as exc:
        raise InterviewStateError(f"зонд вернул не то: {exc!r}") from exc
