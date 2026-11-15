"""Ядро паузы «посмотреть этап» в `dfl` (#768).

Общее для личного и enterprise-путей: разбор флага, журнал замечаний
владельца, экран паузы и подтверждение удалений. Сам цикл «переписать →
проверить» живёт в вызывающем коде.

Журнал `review-remarks.jsonl` — append-only, по JSON-объекту в строке:
`{"kind": "remark", "stage", "text", "ts"}` и `{"kind": "applied", "stage",
"ts"}`. Замечание применено, если после него есть строка `applied` того же
этапа. Строки не переписываются, оборванная последняя строка игнорируется.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from governance.auto_tasks import notice
from lite.interview_chat import STOP_WORD, Read, read_answer

PERSONAL_STAGES = ("brief", "design", "decomposition")
ENTERPRISE_STAGES = (
    "charter",
    "requirements",
    "behaviour-spec",
    "design",
    "acceptance",
    "decomposition",
)
DONE_WORDS = frozenset({"дальше", "да", "ок", "1"})
REMARKS = "review-remarks.jsonl"
_YES_WORDS = frozenset({"1", "да"})
_NO_WORDS = frozenset({"2", "нет"})


class ReviewError(Exception):
    """Неверная настройка паузы (например, неизвестное имя этапа)."""


@dataclass(frozen=True)
class Remark:
    """Замечание владельца к этапу."""

    stage: str
    text: str
    applied: bool


def parse_flag(value: str, stages: tuple[str, ...]) -> tuple[str, ...]:
    """`--review`: `all` — все этапы пути, `none`/пусто — без пауз, иначе список."""
    text = value.strip()
    if text.casefold() == "all":
        return stages
    if text.casefold() in ("", "none"):
        return ()
    names = tuple(n.strip() for n in text.split(",") if n.strip())
    unknown = [n for n in names if n not in stages]
    if unknown:
        raise ReviewError(
            f"неизвестный этап: {', '.join(unknown)}; допустимы: {', '.join(stages)}"
        )
    return names


def from_config(names: tuple[str, ...], stages: tuple[str, ...]) -> tuple[str, ...]:
    """Этапы из конфига, относящиеся к этому пути; чужие молча отбрасываются."""
    return tuple(n for n in names if n in stages)


def _journal(run_dir: Path) -> list[dict[str, object]]:
    path = run_dir / REMARKS
    if not path.is_file():
        return []
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _append(run_dir: Path, row: dict[str, object]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    row["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    path = run_dir / REMARKS
    data = path.read_bytes() if path.is_file() else b""
    torn = bool(data) and not data.endswith(b"\n")
    with path.open("a", encoding="utf-8") as fh:
        if torn:
            fh.write("\n")
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _applied_counts(rows: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if row.get("kind") != "applied":
            continue
        stage, upto = str(row.get("stage", "")), row.get("upto", 0)
        if isinstance(upto, int):
            counts[stage] = counts.get(stage, 0) + upto
    return counts


def all_remarks(run_dir: Path) -> list[Remark]:
    """Все замечания по порядку записи, с признаком «применено».

    Замечание применено, если его номер среди замечаний своего этапа (с 1)
    не больше суммы `upto` из строк `applied` этого этапа (применяются по порядку)."""
    rows = _journal(run_dir)
    applied = _applied_counts(rows)
    seen: dict[str, int] = {}
    result: list[Remark] = []
    for row in rows:
        if row.get("kind") != "remark":
            continue
        stage = str(row.get("stage", ""))
        seen[stage] = seen.get(stage, 0) + 1
        done = seen[stage] <= applied.get(stage, 0)
        result.append(Remark(stage, str(row.get("text", "")), done))
    return result


def pending(run_dir: Path, stage: str) -> list[Remark]:
    """Неприменённые замечания этапа."""
    return [r for r in all_remarks(run_dir) if r.stage == stage and not r.applied]


def record(run_dir: Path, stage: str, text: str) -> None:
    """Записать замечание (не применено)."""
    _append(run_dir, {"kind": "remark", "stage": stage, "text": text})


def mark_applied(run_dir: Path, stage: str, upto: int) -> None:
    """Пометить применёнными ближайшие `upto` неприменённых замечаний этапа.

    `upto` = `len(pending(...))` того снимка, который потребила переписка;
    записанные после снимка остаются неприменёнными."""
    _append(run_dir, {"kind": "applied", "stage": stage, "upto": upto})


def ask(stage_label: str, paths: list[Path], *, read: Read) -> str | None:
    """Экран паузы. `""` — «дальше»; текст — замечания; `None` — `/стоп`/EOF."""
    notice(f"этап «{stage_label}» готов и прошёл проверку — посмотрите:")
    for path in paths:
        notice(f"  file://{path.resolve()}")
    notice(
        "замечания своими словами (пустая строка — отправить); "
        f"«дальше» — без замечаний; {STOP_WORD} — прервать"
    )
    answer = read_answer(read, quick=lambda s: s.strip().casefold() in DONE_WORDS)
    if answer is None:
        return None
    return "" if answer.casefold() in DONE_WORDS else answer


def confirm_removals(ids: list[str], *, read: Read) -> set[str]:
    """Спросить по каждому id, можно ли удалить; `None` от чтения — никого."""
    allowed: set[str] = set()
    for item in ids:
        notice(f"удалить {item}? 1. да · 2. нет")
        answer = read_answer(
            read, quick=lambda s: s.strip().casefold() in _YES_WORDS | _NO_WORDS
        )
        if answer is None:
            return set()
        if answer.casefold() in _YES_WORDS:
            allowed.add(item)
    return allowed


def pr_section(run_dir: Path) -> str:
    """Раздел описания PR с применёнными замечаниями; нет их — пустая строка."""
    lines = [f"- {r.stage}: {r.text}" for r in all_remarks(run_dir) if r.applied]
    if not lines:
        return ""
    return "## Замечания владельца\n\n" + "\n".join(lines) + "\n"
