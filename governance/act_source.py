"""Координаты доверенного источника политики v2 (спека §6.1 п. 1, §6.2).

Файл `contracts/approval-policy-source/v2/source.env` — authority-root:
закрепляется человеком при bootstrap и задаёт исходное доверие ВНЕ
проверяемой политики. `policy_admins` — allowlist принципалов с правом
записи в источник; набор проверяемых личностей не берётся из проверяемой
версии политики (§6.2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from governance import ssot_env

SOURCE_V2_FILE = (
    Path(__file__).resolve().parent.parent
    / "contracts"
    / "approval-policy-source"
    / "v2"
    / "source.env"
)
_SHA = re.compile(r"\A[0-9a-f]{40}\Z")
_KEYS = (
    "APPROVAL_POLICY_REPO",
    "APPROVAL_POLICY_BRANCH",
    "BOOTSTRAP_ACTIVATION",
    "POLICY_ADMINS",
)


class SourceError(ValueError):
    """Контракт источника не читается — отказ конфигурации, не факт."""


@dataclass(frozen=True)
class SourceV2:
    """Закреплённые человеком координаты источника политики."""

    repo: str
    branch: str
    bootstrap_activation: str
    policy_admins: frozenset[str]


def _one(text: str, key: str) -> str:
    lines = ssot_env.definition_lines(text, key)
    if len(lines) != 1 or not lines[0].strip():
        raise SourceError(f"{key}: ожидалась ровно одна непустая строка")
    return lines[0].strip()


def parse_source(text: str) -> SourceV2:
    """Разбор `source.env` v2; любая неоднозначность — `SourceError`."""
    values = {key: _one(text, key) for key in _KEYS}
    if not _SHA.match(values["BOOTSTRAP_ACTIVATION"]):
        raise SourceError("BOOTSTRAP_ACTIVATION: нужен полный 40-символьный SHA")
    admins = frozenset(
        part.strip() for part in values["POLICY_ADMINS"].split(",") if part.strip()
    )
    if not admins:
        raise SourceError("POLICY_ADMINS: пустой allowlist администраторов")
    return SourceV2(
        values["APPROVAL_POLICY_REPO"],
        values["APPROVAL_POLICY_BRANCH"],
        values["BOOTSTRAP_ACTIVATION"],
        admins,
    )


def load_source() -> SourceV2:
    """Контракт из репо devtools; файла нет — `SourceError` (bootstrap не сделан)."""
    try:
        return parse_source(SOURCE_V2_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SourceError(
            f"{SOURCE_V2_FILE} нет — bootstrap источника v2 не выполнен (шаг H2)"
        ) from exc
