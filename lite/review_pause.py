"""Ядро паузы «посмотреть этап» в `dfl` (#768).

Общее для личного и enterprise-путей: разбор флага, журнал замечаний
владельца, экран паузы и подтверждение удалений. Сам цикл «переписать →
проверить» живёт в вызывающем коде.

Журнал `review-remarks.jsonl` — append-only, по JSON-объекту в строке:
`{"kind": "remark", "stage", "text", "ts"}` и `{"kind": "applied" | "dropped",
"stage", "upto", "ts"}`. Строки `applied`/`dropped` этапа по порядку гасят
следующие `upto` замечаний этапа: `applied` — правка внесена, `dropped` —
отклонена (правка удаляла пункты); погашенное не недоиграно. Строки не
переписываются, оборванная последняя строка игнорируется.
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
APPLIED, DROPPED, PENDING = "applied", "dropped", "pending"
_CONSUMED = (APPLIED, DROPPED)
_YES_WORDS = frozenset({"1", "да"})
_NO_WORDS = frozenset({"2", "нет"})


class ReviewError(Exception):
    """Неверная настройка паузы (например, неизвестное имя этапа)."""


@dataclass(frozen=True)
class Remark:
    """Замечание владельца к этапу."""

    stage: str
    text: str
    #: `APPLIED` | `DROPPED` | `PENDING`
    status: str

    @property
    def applied(self) -> bool:
        """Правка по замечанию внесена."""
        return self.status == APPLIED


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


def _consumed(rows: list[dict[str, object]]) -> dict[str, list[str]]:
    """Этап → исходы погашенных замечаний по порядку (`upto` раз каждый)."""
    result: dict[str, list[str]] = {}
    for row in rows:
        kind, upto = row.get("kind"), row.get("upto", 0)
        if kind in _CONSUMED and isinstance(upto, int) and upto > 0:
            result.setdefault(str(row.get("stage", "")), []).extend([str(kind)] * upto)
    return result


def all_remarks(run_dir: Path) -> list[Remark]:
    """Все замечания по порядку записи, с исходом.

    N-е замечание этапа (с 1) получает N-й исход из строк `applied`/`dropped`
    этого этапа (каждая — `upto` исходов, по порядку журнала); исходов
    меньше — `PENDING`."""
    rows = _journal(run_dir)
    consumed = _consumed(rows)
    seen: dict[str, int] = {}
    result: list[Remark] = []
    for row in rows:
        if row.get("kind") != "remark":
            continue
        stage = str(row.get("stage", ""))
        seen[stage] = seen.get(stage, 0) + 1
        done = consumed.get(stage, [])
        status = done[seen[stage] - 1] if seen[stage] <= len(done) else PENDING
        result.append(Remark(stage, str(row.get("text", "")), status))
    return result


def pending(run_dir: Path, stage: str) -> list[Remark]:
    """Недоигранные (не применённые и не отклонённые) замечания этапа."""
    return [r for r in all_remarks(run_dir) if r.stage == stage and r.status == PENDING]


def record(run_dir: Path, stage: str, text: str) -> None:
    """Записать замечание (не применено)."""
    _append(run_dir, {"kind": "remark", "stage": stage, "text": text})


def mark_applied(run_dir: Path, stage: str, upto: int) -> None:
    """Пометить применёнными ближайшие `upto` неприменённых замечаний этапа.

    `upto` = `len(pending(...))` того снимка, который потребила переписка;
    записанные после снимка остаются неприменёнными."""
    _append(run_dir, {"kind": APPLIED, "stage": stage, "upto": upto})


def mark_dropped(run_dir: Path, stage: str, upto: int) -> None:
    """Пометить отклонёнными ближайшие `upto` недоигранных замечаний этапа
    (правка по ним удаляла пункты); снимок — как у `mark_applied`."""
    _append(run_dir, {"kind": DROPPED, "stage": stage, "upto": upto})


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


def confirm_removals(ids: list[str], *, read: Read) -> set[str] | None:
    """Спросить по каждому id, можно ли удалить: разрешённые id; `None` —
    `/стоп`/EOF (человек прервал — пауза у вызывающего, не «нет»)."""
    allowed: set[str] = set()
    for item in ids:
        notice(f"удалить {item}? 1. да · 2. нет")
        answer = read_answer(
            read, quick=lambda s: s.strip().casefold() in _YES_WORDS | _NO_WORDS
        )
        if answer is None:
            return None
        if answer.casefold() in _YES_WORDS:
            allowed.add(item)
    return allowed


def pr_section(run_dir: Path) -> str:
    """Раздел описания PR: применённые замечания списком, отклонённые —
    отдельной строкой; нет ни тех, ни других — пустая строка."""
    remarks = all_remarks(run_dir)
    lines = [f"- {r.stage}: {r.text}" for r in remarks if r.status == APPLIED]
    dropped = [f"{r.stage}: {r.text}" for r in remarks if r.status == DROPPED]
    if dropped:
        lines += ["", "не применены (правка удаляла пункты): " + "; ".join(dropped)]
    if not lines:
        return ""
    return "## Замечания владельца\n\n" + "\n".join(lines).lstrip("\n") + "\n"
