"""Зонд состояния сессии `discovery`: JSON в stdout.

Запуск: `uv run --frozen --project <discovery> python -I interview_probe.py
<session_id>` — интерпретатором окружения `discovery`; код devtools только
читает его модули. С `-I` каталог скрипта не в `sys.path`, поэтому импорты —
только stdlib и `discovery` (внутренний API закреплённой версии: связь держит
пин и тест на нём).
"""

from __future__ import annotations

import json
import sys
from typing import Any

import yaml
from discovery.cli import sessions_root
from discovery.contract.gate_check import FRAMES
from discovery.journal import Journal
from discovery.render import (
    _answered_coverage_keys,
    effective_entries,
    entry_holders,
    readiness,
)


def _payload(raw: object) -> dict[str, Any]:
    """YAML-тело ответа как словарь (не словарь — пустой)."""
    loaded = yaml.safe_load(raw) if isinstance(raw, str) else None
    return loaded if isinstance(loaded, dict) else {}


def _declared(events: list[dict]) -> list[str]:
    """Все id, когда-либо объявленные в ответах (в т.ч. вытесненные)."""
    seen: dict[str, None] = {}
    for event in events:
        if event.get("event") != "answer_recorded":
            continue
        for entry in _payload(event.get("payload")).get("entries") or []:
            if isinstance(entry, dict) and "id" in entry:
                seen[str(entry["id"])] = None
    return list(seen)


def _answers(events: list[dict]) -> dict[str, dict[str, Any]]:
    """question_id → тело последнего ответа; порядок — по последнему ответу."""
    out: dict[str, dict[str, Any]] = {}
    for e in events:
        if e.get("event") == "answer_recorded":
            out.pop(e["question_id"], None)
            out[e["question_id"]] = _payload(e.get("payload"))
    return out


def _asked(events: list[dict]) -> dict[str, str]:
    """question_id → coverage_key заданного вопроса (из `question_asked`)."""
    return {
        e["question_id"]: str(e["coverage_key"])
        for e in events
        if e.get("event") == "question_asked" and e.get("coverage_key") is not None
    }


def build_state(session_id: str) -> dict[str, Any]:
    """Состояние сессии одним словарём (формат — в `lite/interview_state.py`)."""
    root = sessions_root() / session_id
    header = json.loads((root / "header.json").read_text(encoding="utf-8"))
    frame = header["frame"]
    events = Journal(root / "journal.jsonl").events()
    verdict = readiness(events, frame)
    return {
        "frame": frame,
        "required": dict(FRAMES[frame]["required"]),
        "optional": dict(FRAMES[frame]["optional"]),
        "entries": [
            {"id": e.eid, "body": e.body, "fields": e.fields}
            for e in effective_entries(events)
        ],
        "declared": _declared(events),
        "holders": {k: v[0] for k, v in entry_holders(events).items()},
        "answers": _answers(events),
        "answered_keys": sorted(_answered_coverage_keys(events)),
        "readiness": verdict.verdict,
        "findings": list(verdict.findings),
        "asked": _asked(events),
    }


def main(argv: list[str]) -> int:
    """`interview_probe.py <session_id>` → JSON; сбой — причина в stderr, код 1."""
    if len(argv) != 2:
        print("usage: interview_probe.py <session_id>", file=sys.stderr)
        return 2
    try:
        state = build_state(argv[1])
    except Exception as exc:  # noqa: BLE001 — причина уходит вызывающему
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(state, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
