"""roadmap.toml: разбор, валидация, классы эпиков (спека §2)."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from conductor.model import Finding

GOAL_RE = re.compile(r"^todo://[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._-]{0,63}$")
LIMIT_DEFAULTS: dict[str, tuple[int, int, int]] = {
    "max_writes_per_run": (20, 1, 200),
    "max_launches_per_run": (2, 0, 10),
    "max_open_prs_per_repo": (2, 0, 10),
    "worker_timeout_min": (45, 5, 180),
    "run_timeout_min": (55, 10, 240),
    "max_model_calls_per_run": (20, 0, 200),
    "max_prerequisite_launches_per_run": (1, 0, 10),
    "stale_after_days": (3, 1, 60),
    "renudge_after_days": (7, 1, 60),
}
# Действия среза 1 (спека среза 1, §1): только они включаются роадмапом.
ACTIONS: tuple[str, ...] = (
    "owner_queue",
    "notify_satisfied",
    "nudge",
    "pr_nudge",
    "close_shipped",
)
Klass = Literal["focus", "parked", "background"]


@dataclass(frozen=True)
class Focus:
    """Фокус роадмапа; rank — позиция, начиная с 1."""

    epic: str
    rank: int
    goal: str | None
    autonomy: int
    pull_prerequisites: bool


@dataclass(frozen=True)
class Roadmap:
    """Разобранный роадмап; valid=False — RM-INVALID (§2.2)."""

    autonomy: int = 0
    writer_host: str = ""
    writer_since: str = ""
    focus: tuple[Focus, ...] = ()
    parked: frozenset[str] = frozenset()
    limits: dict[str, int] = field(
        default_factory=lambda: {k: v[0] for k, v in LIMIT_DEFAULTS.items()}
    )
    enabled_actions: frozenset[str] = frozenset()
    valid: bool = False
    findings: tuple[Finding, ...] = ()

    def focus_of(self, epic: str | None) -> Focus | None:
        """Фокус эпика или None."""
        return next((f for f in self.focus if f.epic == epic), None)

    def klass(self, epic: str | None) -> Klass:
        """Собственный класс узла по его эпику (§2.3)."""
        if self.focus_of(epic) is not None:
            return "focus"
        if epic is not None and epic in self.parked:
            return "parked"
        return "background"


def _invalid(detail: str) -> Finding:
    return Finding("RM-INVALID", "error", "roadmap", detail)


def _int_in(value: Any, low: int, high: int) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool) and low <= value <= high
    )


def _is_utc_instant(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _limits(raw: Any, errors: list[Finding]) -> dict[str, int]:
    if raw is not None and not isinstance(raw, dict):
        errors.append(_invalid("limits должен быть таблицей"))
    table = raw if isinstance(raw, dict) else {}
    limits: dict[str, int] = {}
    for name, (default, low, high) in LIMIT_DEFAULTS.items():
        value = table.get(name, default)
        if not _int_in(value, low, high):
            errors.append(_invalid(f"limits.{name}={value!r} вне {low}..{high}"))
            value = default
        limits[name] = value
    return limits


def _focus(
    raw: Any,
    top: int,
    epics: dict[str, dict],
    errors: list[Finding],
    warnings: list[Finding],
) -> tuple[Focus, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        errors.append(_invalid("focus должен быть массивом таблиц [[focus]]"))
        return ()
    result: list[Focus] = []
    for rank, entry in enumerate(raw, start=1):
        epic = entry.get("epic") if isinstance(entry, dict) else None
        if (
            not isinstance(entry, dict)
            or not isinstance(epic, str)
            or epic not in epics
        ):
            errors.append(_invalid(f"focus #{rank}: неизвестный эпик {epic!r}"))
            continue
        status = epics[epic].get("status")
        if status == "done":
            errors.append(_invalid(f"focus #{rank}: эпик {epic} в статусе done"))
        if status == "paused":
            warnings.append(Finding("RM-FOCUS-PAUSED", "warning", epic))
        goal = entry.get("goal")
        if goal is not None and not (isinstance(goal, str) and GOAL_RE.match(goal)):
            errors.append(_invalid(f"focus #{rank}: goal {goal!r} не todo://"))
            goal = None
        autonomy = entry.get("autonomy", top)
        if not _int_in(autonomy, 0, 3):
            errors.append(_invalid(f"focus #{rank}: autonomy={autonomy!r}"))
            autonomy = 0
        pull = entry.get("pull_prerequisites", False)
        if not isinstance(pull, bool):
            errors.append(_invalid(f"focus #{rank}: pull_prerequisites={pull!r}"))
            pull = False
        result.append(Focus(epic, rank, goal, autonomy, pull))
    return tuple(result)


def _parked(raw: Any, epics: dict[str, dict], errors: list[Finding]) -> list[str]:
    if raw is None:
        return []
    items = raw.get("epics", []) if isinstance(raw, dict) else None
    if not isinstance(items, list) or not all(isinstance(e, str) for e in items):
        errors.append(_invalid("parked должен быть таблицей с epics = [строки]"))
        return []
    for epic in sorted(set(items) - set(epics)):
        errors.append(_invalid(f"parked: неизвестный эпик {epic!r}"))
    return items


def _enabled(raw: Any, errors: list[Finding]) -> frozenset[str]:
    """enabled_actions (срез 1, §2.1): нет поля — ничего не включено."""
    if raw is None:
        return frozenset()
    if not isinstance(raw, list) or not all(isinstance(a, str) for a in raw):
        errors.append(_invalid("enabled_actions должен быть массивом строк"))
        return frozenset()
    for name in sorted(set(raw) - set(ACTIONS)):
        errors.append(_invalid(f"enabled_actions: неизвестное действие {name!r}"))
    for name in sorted({a for a in raw if raw.count(a) > 1}):
        errors.append(_invalid(f"enabled_actions: {name!r} дважды"))
    return frozenset(a for a in raw if a in ACTIONS)


def parse_roadmap(text: str | None, epics: dict[str, dict]) -> Roadmap:
    """Разбор и валидация (§2.1–2.2); ошибки становятся RM-INVALID."""
    if text is None:
        return Roadmap(findings=(_invalid("roadmap.toml не прочитан"),))
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return Roadmap(findings=(_invalid(f"TOML: {exc}"),))
    errors: list[Finding] = []
    warnings: list[Finding] = []
    if data.get("schema_version") != 1:
        errors.append(_invalid(f"schema_version={data.get('schema_version')!r}"))
    top = data.get("autonomy", 0)
    if not _int_in(top, 0, 3):
        errors.append(_invalid(f"autonomy={top!r}"))
        top = 0
    writer = data.get("writer_host", "")
    if not isinstance(writer, str) or not writer:
        errors.append(_invalid("writer_host пуст"))
        writer = ""
    since = data.get("writer_since", "")
    if not _is_utc_instant(since):
        errors.append(_invalid(f"writer_since={since!r} не RFC 3339 UTC"))
    focus = _focus(data.get("focus"), top, epics, errors, warnings)
    parked = _parked(data.get("parked"), epics, errors)
    seen = [f.epic for f in focus] + parked
    for epic in sorted({e for e in seen if seen.count(e) > 1}):
        errors.append(_invalid(f"эпик {epic} встречается дважды"))
    limits = _limits(data.get("limits"), errors)
    enabled = _enabled(data.get("enabled_actions"), errors)
    return Roadmap(
        autonomy=top,
        writer_host=writer,
        writer_since=since if isinstance(since, str) else "",
        focus=focus,
        parked=frozenset(parked),
        limits=limits,
        enabled_actions=enabled,
        valid=not errors,
        findings=tuple(errors + warnings),
    )
