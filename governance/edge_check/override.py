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
    if not isinstance(row, dict):
        return None
    if row.get("wave") != wave or row.get("kind") != OVERRIDE_KIND:
        return None
    digest = row.get("digest")
    return digest if isinstance(digest, str) and digest else None


def overridden(run_dir: Path, wave: int) -> set[str]:
    """Digest'ы находок с решением `override` в волне `wave`.

    Строка без digest (журнал до привязки) не принимает ничего; битые
    строки пропускаются; нет файла — пустое множество.
    """
    path = run_dir / JOURNAL
    if not path.is_file():
        return set()
    digests: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            digest = _digest_of(json.loads(line), wave)
        except json.JSONDecodeError:
            continue
        if digest is not None:
            digests.add(digest)
    return digests


def accepted(
    records: dict[tuple[str, str], dict], digests: set[str]
) -> set[Key] | None:
    """Ключи блокирующих находок уровня, если человек принял их ВСЕ (digest
    каждой — в `digests`, см. `finding_digest`); иначе `None`.

    Принимается вопреки только FAIL, объяснённый блокирующими находками:
    ребро `ERROR` или с иным вердиктом, FAIL без блокирующих находок, пункт
    правил не `pass`, не покрытый находкой (`insufficient`, `fail` без
    причины), и непринятая находка — `None` (прежний стоп).
    """
    found: set[Key] = set()
    for (node, edge), record in records.items():
        verdict = record.get("verdict")
        if verdict == "PASS":
            continue
        blocking = _blocking(record)
        if verdict != "FAIL" or not blocking or not _explained(record):
            return None
        subject = subject_shas(record.get("subject"))
        for f in blocking:
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
