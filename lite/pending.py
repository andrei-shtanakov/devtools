"""Вопрос наружу, ответ внутрь: `dfl` без терминала (#766).

Место, где прогон ждёт человека, записывает вопрос в `pending.json` каталога
прогона и останавливается; `dfl reply` кладёт ответ в `replies.json`; следующий
`dfl run` забирает его. Модуль — только хранение и проверка ответа.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from governance import manual_checks as mc
from lite import i18n
from lite.interview_chat import is_skip

REVIEW = "review"
MANUAL = "manual"
REMARKS_CONFIRM = "remarks-confirm"
EDGE = "edge"
INTERVIEW_QUESTION = "interview-question"
INTERVIEW_TOPUP = "interview-topup"
INTERVIEW_REPAIR = "interview-repair"
INTERVIEW_KINDS = frozenset({INTERVIEW_QUESTION, INTERVIEW_TOPUP, INTERVIEW_REPAIR})
#: личный путь без терминала: нужен бриф (ответ — `dfl note`/`dfl brief`)
INTERVIEW_PERSONAL = "interview-personal"
#: «Верно?» по сводке брифа, построенной `dfl` (ответ — `dfl reply`)
BRIEF_CONFIRM = "brief-confirm"
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
    "passed": mc.DONE,
    "failed": mc.NOT_DONE,
    "passed with caveat": mc.CAVEAT,
}
#: слова «этап готов, дальше» для паузы ревью
REVIEW_DONE_WORDS = frozenset({"дальше", "да", "ок", "1", "next", "yes", "ok", "done"})

#: префиксы свободного ответа по узлу (как в `edge_resolve`)
EDGE_OWN_PREFIX = "своё:"
EDGE_DISAGREE_PREFIX = "не согласен:"
#: те же префиксы на двух языках (русский — первым)
EDGE_OWN_PREFIXES = (EDGE_OWN_PREFIX, "own:")
EDGE_DISAGREE_PREFIXES = (EDGE_DISAGREE_PREFIX, "disagree:")
#: слова «оставить как было» для подтверждения замечаний
CONFIRM_KEEP_WORDS = frozenset(
    {"2", "нет", "оставить", "оставить как было", "keep", "keep as it was"}
)
#: русское слово согласия вида правки → английское (`удалить` → `remove`)
AGREE_EN = {"удалить": "remove", "снять": "drop", "принять": "accept"}

NO_PENDING = "нет вопроса, ждущего ответа — dfl pending"
STALE = "вопрос уже другой — dfl pending"
ANSWERED = "ответ записан — продолжить: dfl run"
#: строка «ждёт ответа» (начало — `manual_ask.WAIT_PREFIX` на обоих языках)
WAIT_LINE = (
    "ждёт ответа: {first} — вопрос: dfl pending{pick}; "
    "ответ: dfl reply{pick} --id {qid} --text '…'"
)
#: строка ожидания «нужен бриф» (`INTERVIEW_PERSONAL`): ответ — не `dfl reply`
WAIT_LINE_PERSONAL = (
    "ждёт ответа: {first} — вопрос: dfl pending{pick}; "
    "ответ: dfl note{pick} --id {qid}, затем dfl brief{pick} --id {qid}"
)
#: приглашение ответить в выводе `dfl pending` для `INTERVIEW_PERSONAL`
PERSONAL_ANSWER_HINT = (
    "ответ: dfl note --id {qid} --role … --text '…', затем dfl brief --id {qid} "
    "--file <бриф.json>"
)
REVIEW_EMPTY_REFUSAL = "ответ: «дальше» или замечания словами"
MANUAL_REFUSAL = "ответ: 1/выполнено, 2/не выполнено[: заметка], 3/с оговоркой: <текст>"
CONFIRM_REFUSAL = "ответ: 1/{yes} или 2/оставить"
EDGE_REFUSAL = "ответ: номер варианта 1–{n}, «своё: …» или «не согласен: <почему>»"
INTERVIEW_REFUSAL = (
    "ответ по существу: dfl answer --items <файл> --id {qid}; "
    "здесь — только «пропустить»"
)
PERSONAL_REPLY_REFUSAL = (
    "ответ на этот вопрос — dfl note и dfl brief (бриф собирает скил)"
)
CONFIRM_FIX_REFUSAL = "ответ: «да» или поправка словами"
NUMERIC_REFUSAL = (
    "ответ по существу: dfl answer --items <файл> --id {qid}; "
    "здесь — только «пропустить» или «неизвестно»"
)
#: префикс поправки на «Верно?» («2.», «поправить:», «fix»); общий для терминала
#: (`interviewer.fix_text`) и проверки ответа `brief-confirm` без терминала
FIX_PREFIX = re.compile(
    r"^\s*(?:2\s*[.):](?!\d)\s*)?(?:(?:поправить|fix)\s*:?\s*)?", re.IGNORECASE
)
#: ключи починки, ответ на которые — текст человека (`dfl reply`), не раскладка
TEXT_KEY_PREFIXES = ("verdict:", "category:", "owner:")
TEXT_KEYS = frozenset({"assumptions"})
NUMERIC_KEY_PREFIX = "nfr:"


def takes_text(key: str) -> bool:
    """Ответ на вопрос интервью с ключом `key` — текст через `dfl reply`."""
    return key in TEXT_KEYS or key.startswith(TEXT_KEY_PREFIXES)


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


def post(run_dir: Path, q: Question, data: Mapping | None = None) -> None:
    """Записать вопрос в `pending.json` (с данными для скила, если есть).

    Сменился вопрос — ответы на прежний (гонка с `dfl reply`) не переживают
    его; тот же вопрос — ответ мог прийти только что, его не стираем.
    """
    current = load(run_dir)
    if current is None or current.get("id") != q.id:
        (run_dir / REPLIES_FILE).unlink(missing_ok=True)
    record_ = {
        **as_dict(q),
        "run_id": run_dir.name,
        "at": datetime.now().astimezone().isoformat(),
    }
    if data:
        record_["data"] = dict(data)
    _write_json(run_dir / PENDING_FILE, record_)


def asker(run_dir: Path) -> Ask:
    """Спрашивалка без терминала: ответ из `replies.json` или запись вопроса."""

    def ask(q: Question) -> str | None:
        text = take(run_dir, q)
        if text is not None:
            return text
        post(run_dir, q)
        return None

    return ask


def _line(qid: str, text: str, subject: str | None = None, kind: str = "") -> str:
    first = text.splitlines()[0] if text.strip() else ""
    pick = f" --subject '{subject}'" if subject else ""
    line = WAIT_LINE_PERSONAL if kind == INTERVIEW_PERSONAL else WAIT_LINE
    return i18n.t(line).format(first=first, pick=pick, qid=qid)


def wait_line(q: Question, subject: str | None = None) -> str:
    """Строка «ждёт ответа» для вывода `dfl run`; `subject` — тема в
    подсказке, когда без неё `dfl pending`/`dfl reply` не выберут прогон."""
    return _line(q.id, q.text, subject, q.kind)


def wait_text(run_dir: Path, subject: str | None = None) -> str:
    """`wait_line` по записанному вопросу (ответ уже записан — `ANSWERED`);
    пусто, если вопроса нет. `subject` — тема в подсказке, когда без неё
    `dfl pending`/`dfl reply` не выберут прогон."""
    rec = load(run_dir)
    if rec is None:
        return ""
    if answered(run_dir):
        return i18n.t(ANSWERED)
    qid, text = str(rec.get("id", "")), str(rec.get("text", ""))
    return _line(qid, text, subject, str(rec.get("kind", "")))


def parse_manual(text: str) -> dict:
    """Ответ по ручной проверке: `{"verdict", "note"}` (вердикт — до `:`)."""
    head, _, note = text.partition(":")
    verdict = VERDICT_WORDS.get(head.strip().casefold())
    return {"verdict": verdict, "note": note.strip()}


def _check_manual(text: str) -> str | None:
    parsed = parse_manual(text)
    if parsed["verdict"] is None:
        return i18n.t(MANUAL_REFUSAL)
    if parsed["verdict"] == mc.CAVEAT and not parsed["note"]:
        return i18n.t(MANUAL_REFUSAL)
    return None


def is_done(text: str) -> bool:
    """Ответ — слово «этап готов, дальше»."""
    return text.strip().casefold() in REVIEW_DONE_WORDS


def _check_review(text: str, rec: Mapping | None) -> str | None:
    if is_done(text):
        return None
    return None if text.strip() else i18n.t(REVIEW_EMPTY_REFUSAL)


def _options(rec: Mapping | None) -> list[str]:
    return [str(o) for o in (rec or {}).get("options") or ()]


def _check_confirm(text: str, rec: Mapping | None) -> str | None:
    options = _options(rec)
    yes = options[0] if options else "да"
    word = text.strip().casefold()
    en = AGREE_EN.get(yes.casefold(), "yes")
    ru = {v: k for k, v in AGREE_EN.items()}.get(yes.casefold(), "да")
    accepted = {"1", "да", "yes", yes.casefold(), en, ru} | CONFIRM_KEEP_WORDS
    if word in accepted:
        return None
    if yes.casefold() in AGREE_EN.values():  # вопрос английского прогона
        en = yes.casefold()
    shown = en if i18n.current() == "en" else yes
    return i18n.t(CONFIRM_REFUSAL).format(yes=shown)


def _fold(text: str) -> str:
    return text.casefold().replace("ё", "е")


def _check_edge(text: str, rec: Mapping | None) -> str | None:
    n = len(_options(rec))
    body = text.strip()
    if body.isascii() and body.isdigit() and 1 <= int(body) <= n:
        return None
    folded = _fold(body)
    for prefix in (*EDGE_OWN_PREFIXES, *EDGE_DISAGREE_PREFIXES):
        if folded.startswith(_fold(prefix)) and body[len(prefix) :].strip():
            return None
    return i18n.t(EDGE_REFUSAL).format(n=n)


def fix_rest(text: str) -> str:
    """Слова поправки без префикса `FIX_PREFIX` (пусто — слов нет)."""
    return FIX_PREFIX.sub("", text, count=1).strip()


def _check_brief_confirm(text: str) -> str | None:
    """«Верно?»: любой ответ со словами; пустой, голое «2» и префикс поправки
    без слов («2.», «поправить:», «fix») — отказ: поправка — словами."""
    word = text.strip()
    if word == "2" or not fix_rest(word):
        return i18n.t(CONFIRM_FIX_REFUSAL)
    return None


def _check_interview(text: str, rec: Mapping | None) -> str | None:
    if is_skip(text):
        return None
    key = str((rec or {}).get("key", ""))
    if takes_text(key) and text.strip():
        return None
    qid = (rec or {}).get("id", "")
    if key.startswith(NUMERIC_KEY_PREFIX):
        # ленивый импорт: `brief_v13` тянет правила починки
        from lite.brief_v13 import is_unknown

        return None if is_unknown(text) else NUMERIC_REFUSAL.format(qid=qid)
    return INTERVIEW_REFUSAL.format(qid=qid)


def check(kind: str, text: str, rec: Mapping | None = None) -> str | None:
    """Причина отказа принять ответ или `None`.

    `rec` — записанный вопрос (`load()`): по нему проверяются варианты и
    ключ вопроса интервью. На «нужен бриф» (`INTERVIEW_PERSONAL`) reply не
    отвечают: ответ — `dfl note`/`dfl brief`.
    """
    if kind == REVIEW:
        return _check_review(text, rec)
    if kind == REMARKS_CONFIRM:
        return _check_confirm(text, rec)
    if kind == EDGE:
        return _check_edge(text, rec)
    if kind == MANUAL:
        return _check_manual(text)
    if kind in INTERVIEW_KINDS:
        return _check_interview(text, rec)
    if kind == INTERVIEW_PERSONAL:
        return i18n.t(PERSONAL_REPLY_REFUSAL)
    if kind == BRIEF_CONFIRM:
        return _check_brief_confirm(text)
    return f"неизвестный вид вопроса: {kind}"


def record(run_dir: Path, qid: str, text: str) -> None:
    """Записать ответ на ожидающий вопрос `qid` или отказать `PendingError`."""
    current = load(run_dir)
    if current is None:
        raise PendingError(i18n.t(NO_PENDING))
    if current.get("id") != qid:
        raise PendingError(i18n.t(STALE))
    reason = check(str(current.get("kind", "")), text, current)
    if reason is not None:
        raise PendingError(reason)
    replies = _read_json(run_dir / REPLIES_FILE) or {}
    replies[qid] = text
    _write_json(run_dir / REPLIES_FILE, replies)
