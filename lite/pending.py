"""Вопрос наружу, ответ внутрь: `dfl` без терминала (#766).

Место, где прогон ждёт человека, записывает вопрос в `pending.json` каталога
прогона и останавливается; `dfl reply` кладёт ответ в `replies.json`; следующий
`dfl run` забирает его. Модуль — только хранение и проверка ответа.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from governance import manual_checks as mc

REVIEW = "review"
MANUAL = "manual"
PENDING_FILE = "pending.json"
REPLIES_FILE = "replies.json"

#: слова вердикта ручной проверки → вердикт
VERDICT_WORDS = {
    "1": mc.DONE,
    "выполнено": mc.DONE,
    "2": mc.NOT_DONE,
    "не выполнено": mc.NOT_DONE,
    "3": mc.CAVEAT,
    "с оговоркой": mc.CAVEAT,
    "принять с оговоркой": mc.CAVEAT,
}
#: слова «этап готов, дальше» для паузы ревью
REVIEW_DONE_WORDS = frozenset({"дальше", "да", "ок", "1"})

NO_PENDING = "нет вопроса, ждущего ответа — dfl pending"
STALE = "вопрос уже другой — dfl pending"
ANSWERED = "ответ записан — продолжить: dfl run"
REVIEW_REFUSAL = (
    "замечания к этапу пока принимаются только в терминале: dfl run; "
    "без замечаний — «дальше»"
)
MANUAL_REFUSAL = "ответ: 1/выполнено, 2/не выполнено[: заметка], 3/с оговоркой: <текст>"


class PendingError(Exception):
    """Ответ не принят: нет вопроса, вопрос другой или ответ не годится."""


@dataclass(frozen=True)
class Question:
    """Вопрос человеку: вид, ключ места, текст, варианты и ссылки."""

    kind: str
    key: str
    text: str
    options: tuple[str, ...] = ()
    links: tuple[str, ...] = ()
    accepts: str = ""

    @property
    def id(self) -> str:
        """Стабильный идентификатор, привязанный к тексту вопроса."""
        digest = hashlib.sha256(self.text.encode()).hexdigest()[:8]
        return f"{self.kind}:{self.key}:{digest}"


Ask = Callable[[Question], str | None]


def as_dict(q: Question) -> dict:
    """Запись вопроса для `pending.json` (без времени)."""
    data = asdict(q)
    data["options"] = list(q.options)
    data["links"] = list(q.links)
    return {"id": q.id, **data}


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load(run_dir: Path) -> dict | None:
    """Ожидающий вопрос или `None` (нет файла или он битый)."""
    return _read_json(run_dir / PENDING_FILE)


def clear(run_dir: Path) -> None:
    """Снять ожидающий вопрос."""
    (run_dir / PENDING_FILE).unlink(missing_ok=True)


def take(run_dir: Path, q: Question) -> str | None:
    """Забрать ответ на `q`: убрать из `replies.json`, снять вопрос.

    Ответ берётся, только пока `q` — записанный ждущий вопрос: ответ,
    переживший смену вопроса (гонка с `dfl reply`), не проходит следующий
    показ того же вопроса. Ответа нет — `None`, ничего не пишется.
    """
    current = load(run_dir)
    if current is None or current.get("id") != q.id:
        return None
    replies = _read_json(run_dir / REPLIES_FILE) or {}
    if q.id not in replies:
        return None
    text = str(replies.pop(q.id))
    _write_json(run_dir / REPLIES_FILE, replies)
    clear(run_dir)
    return text


def discard(run_dir: Path) -> None:
    """Живой ответ победил (или вопрос устарел): снять вопрос и записанные
    ответы — ответ годится только на текущий вопрос."""
    (run_dir / REPLIES_FILE).unlink(missing_ok=True)
    clear(run_dir)


def answered(run_dir: Path) -> bool:
    """На ждущий вопрос уже записан ответ (`dfl reply`), ждём `dfl run`."""
    rec = load(run_dir)
    replies = _read_json(run_dir / REPLIES_FILE) or {}
    return rec is not None and rec.get("id") in replies


def asker(run_dir: Path) -> Ask:
    """Спрашивалка без терминала: ответ из `replies.json` или запись вопроса."""

    def ask(q: Question) -> str | None:
        text = take(run_dir, q)
        if text is not None:
            return text
        current = load(run_dir)
        if current is None or current.get("id") != q.id:
            # вопрос сменился: ответы на прежний (гонка с `dfl reply`) не
            # переживают его; тот же вопрос — ответ мог прийти только что
            (run_dir / REPLIES_FILE).unlink(missing_ok=True)
        record_ = {
            **as_dict(q),
            "run_id": run_dir.name,
            "at": datetime.now().astimezone().isoformat(),
        }
        _write_json(run_dir / PENDING_FILE, record_)
        return None

    return ask


def _line(qid: str, text: str, subject: str | None = None) -> str:
    first = text.splitlines()[0] if text.strip() else ""
    pick = f" --subject '{subject}'" if subject else ""
    return (
        f"ждёт ответа: {first} — вопрос: dfl pending{pick}; "
        f"ответ: dfl reply{pick} --id {qid} --text '…'"
    )


def wait_line(q: Question) -> str:
    """Строка «ждёт ответа» для вывода `dfl run`."""
    return _line(q.id, q.text)


def wait_text(run_dir: Path, subject: str | None = None) -> str:
    """`wait_line` по записанному вопросу (ответ уже записан — `ANSWERED`);
    пусто, если вопроса нет. `subject` — тема в подсказке, когда без неё
    `dfl pending`/`dfl reply` не выберут прогон."""
    rec = load(run_dir)
    if rec is None:
        return ""
    if answered(run_dir):
        return ANSWERED
    return _line(str(rec.get("id", "")), str(rec.get("text", "")), subject)


def parse_manual(text: str) -> dict:
    """Ответ по ручной проверке: `{"verdict", "note"}` (вердикт — до `:`)."""
    head, _, note = text.partition(":")
    verdict = VERDICT_WORDS.get(head.strip().casefold())
    return {"verdict": verdict, "note": note.strip()}


def _check_manual(text: str) -> str | None:
    parsed = parse_manual(text)
    if parsed["verdict"] is None:
        return MANUAL_REFUSAL
    if parsed["verdict"] == mc.CAVEAT and not parsed["note"]:
        return MANUAL_REFUSAL
    return None


def check(kind: str, text: str) -> str | None:
    """Причина отказа принять ответ или `None`."""
    if kind == REVIEW:
        done = text.strip().casefold() in REVIEW_DONE_WORDS
        return None if done else REVIEW_REFUSAL
    if kind == MANUAL:
        return _check_manual(text)
    return f"неизвестный вид вопроса: {kind}"


def record(run_dir: Path, qid: str, text: str) -> None:
    """Записать ответ на ожидающий вопрос `qid` или отказать `PendingError`."""
    current = load(run_dir)
    if current is None:
        raise PendingError(NO_PENDING)
    if current.get("id") != qid:
        raise PendingError(STALE)
    reason = check(str(current.get("kind", "")), text)
    if reason is not None:
        raise PendingError(reason)
    replies = _read_json(run_dir / REPLIES_FILE) or {}
    replies[qid] = text
    _write_json(run_dir / REPLIES_FILE, replies)
