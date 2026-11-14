"""Замечания edge-check, принятые человеком вопреки (журнал решений `dfl`).

Журнал `edge-decisions.jsonl` пишет `lite/edge_journal.py`; читает его и
раннер, поэтому читатель override-решений живёт здесь: governance не
импортирует lite (направление зависимости — lite → governance).

Решение привязано к вердикту правила на конкретном тексте узла:
`finding_digest` — sha256 над (узел, ребро, правило, класс) и хэшами входа
subject ребра. Изменённый текст узла, другое правило или класс — другой
digest, принятие не переносится; переформулировка той же находки
ревьюером — тот же digest. Строка журнала без digest не принимает.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

JOURNAL = "edge-decisions.jsonl"
OVERRIDE_KIND = "override"
#: Классы, валящие проверку: `severity.blocking` правил ребра
#: (`contracts/edge-check/v1/rules/*.yaml`, у всех рёбер одинаков — сверяет
#: `tests/test_lite_stop_findings.py`).
BLOCKING = frozenset({"blocker", "major"})

Key = tuple[str, str, str]


def subject_shas(subject: object) -> list[str]:
    """Хэши входа subject записи ребра (`[{path, sha256}]`), отсортированные."""
    if not isinstance(subject, list):
        return []
    return sorted(str(f.get("sha256", "")) for f in subject if isinstance(f, dict))


def bound_subject(subject: object) -> list[str] | None:
    """Хэши subject, к которым можно привязать принятие; иначе `None`.

    Нет subject, пустой список или запись без `sha256` (пустой) — привязать
    не к чему: иначе разные тексты сводились бы к одному `[""]`/`[]` и
    принятие переносилось бы на них (fail-closed).
    """
    shas = subject_shas(subject)
    if not shas or not all(s.strip() for s in shas):
        return None
    return shas


def finding_digest(node: str, edge: str, finding: dict, subject: list[str]) -> str:
    """Отпечаток принятия: узел, ребро, правило, класс и хэши subject.

    Формулировки и места нет: ревьюер недетерминирован (devtools#728), а
    после принятия шаг edge перезапускает его на неизменённом узле —
    переформулированная находка того же правила не должна спрашивать
    человека снова. Смысл: человек принял блокирующий вердикт этого правила
    на этом тексте узла. Один на `dfl` (запись) и раннер/публикацию (сверка).
    """
    payload = [
        node,
        edge,
        str(finding.get("rule_id")),
        str(finding.get("class")),
        sorted(subject),
    ]
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _digest_of(row: object, wave: int) -> str | None:
    """Digest строки журнала волны `wave` (любого вида решения)."""
    if not isinstance(row, dict) or row.get("wave") != wave:
        return None
    digest = row.get("digest")
    return digest if isinstance(digest, str) and digest else None


def overridden(run_dir: Path, wave: int) -> set[str]:
    """Digest'ы, чьё ПОСЛЕДНЕЕ решение в волне `wave` — `override`.

    Более позднее решение по тому же digest (вариант, «своё», несогласие)
    отзывает принятие — одинаково для `dfl`, раннера и публикации. Строка
    без digest (журнал до привязки) не принимает и не отзывает; битые
    строки пропускаются; нет файла — пустое множество.
    """
    path = run_dir / JOURNAL
    if not path.is_file():
        return set()
    latest: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        digest = _digest_of(row, wave)
        if digest is not None:
            latest[digest] = str(row.get("kind"))
    return {d for d, kind in latest.items() if kind == OVERRIDE_KIND}


def overridable(records: dict[tuple[str, str], dict]) -> bool:
    """Можно ли принять уровень вопреки замечаниям — независимо от журнала
    (`non_overridable_reason` is None). Один предикат на `dfl` (что
    предлагать) и раннер/публикацию (`accepted`)."""
    return non_overridable_reason(records) is None


def non_overridable_reason(records: dict[tuple[str, str], dict]) -> str | None:
    """Почему уровень нельзя принять вопреки; можно — `None`.

    Принимается только FAIL, объяснённый блокирующими находками: у каждой
    не-PASS записи — вердикт `FAIL`, блокирующие находки, каждый не-`pass`
    пункт правил покрыт находкой с причиной и хэши subject. Повтор проверки
    советуется только там, где он может помочь (пункт не оценён, ошибка).
    """
    # N/A засчитывается уровню как пройденное (coordinator.run_level)
    failing = [
        (edge, r)
        for (_node, edge), r in records.items()
        if r.get("verdict") not in ("PASS", "N/A")
    ]
    if not failing:
        return f"{_NO} на уровне нет FAIL"
    for edge, record in failing:
        reason = _record_reason(edge, record)
        if reason is not None:
            return f"{_NO} {reason}"
    return None


_NO = "принять вопреки нельзя:"
_FIX_NODE = "поправьте узел по замечаниям"


def _record_reason(edge: str, record: dict) -> str | None:
    verdict = record.get("verdict")
    if verdict != "FAIL":
        return f"проверка ребра {edge} не состоялась ({verdict}) — повторите проверку"
    if not _blocking(record):
        return f"ребро {edge} без блокирующих замечаний — {_FIX_NODE}"
    if not _explained(record):
        return f"пункт правил ребра {edge} не оценён — повторите проверку"
    if bound_subject(record.get("subject")) is None:
        return f"у записи ребра {edge} нет хэшей входа — {_FIX_NODE}"
    return None


def accepted(
    records: dict[tuple[str, str], dict], digests: set[str]
) -> set[Key] | None:
    """Ключи блокирующих находок уровня, если человек принял их ВСЕ (digest
    каждой — в `digests`, см. `finding_digest`); иначе `None`.

    Уровень обязан быть `overridable`; иначе, как и при непринятой
    находке, — `None` (прежний стоп).
    """
    if not overridable(records):
        return None
    found: set[Key] = set()
    for (node, edge), record in records.items():
        if record.get("verdict") == "PASS":
            continue
        subject = bound_subject(record.get("subject")) or []
        for f in _blocking(record):
            if finding_digest(node, edge, f, subject) not in digests:
                return None
            found.add((node, edge, str(f.get("rule_id"))))
    return found or None


def blocking_digests(records: dict[tuple[str, str], dict]) -> set[str]:
    """Digest'ы блокирующих находок всех записей уровня."""
    return {
        finding_digest(node, edge, f, subject_shas(record.get("subject")))
        for (node, edge), record in records.items()
        for f in _blocking(record)
    }


def _blocking(record: dict) -> list[dict]:
    return [
        f
        for f in record.get("findings") or []
        if isinstance(f, dict) and f.get("class") in BLOCKING
    ]


def _explained(record: dict) -> bool:
    """Каждый не-`pass` пункт — `fail` с причиной и находкой по нему."""
    remarked = {
        str(f.get("rule_id"))
        for f in record.get("findings") or []
        if isinstance(f, dict)
    }
    return all(
        c.get("status") == "pass"
        or (
            c.get("status") == "fail"
            and str(c.get("id")) in remarked
            and bool(str(c.get("reason") or "").strip())
        )
        for c in record.get("criteria") or []
        if isinstance(c, dict)
    )
