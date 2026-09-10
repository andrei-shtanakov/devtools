"""Имена веток двухфазного одобрения узлов бандла (§I12) — python-половина.

SSOT — не этот модуль, а `contracts/approval-branches/v1/patterns.env`:
шаблон имени лежит там, и вторая половина (`merge-pr.sh`, гвард агентского
мержа) читает ТОТ ЖЕ файл. Здесь только вывод из шаблона — имя подстановкой
значений, глоб заменой плейсхолдеров на `*`.

Почему шаблон, а не пара «строитель имени + отдельный глоб»: глоб, выведенный
из шаблона механически, не может от него отстать. Дописывание номера волны
меняет одну строку в одном файле, и обе стороны едут за ней сами. Две
независимо написанные строки разъезжаются молча — это уже произошло
(`_approve_branch` строит `spec/<ws-id>-bundle-approve`, контракт требует
`spec/<ws-id>-approve-<W>-<K>`).

Механика одобрения обязана строить имена ЧЕРЕЗ этот модуль, а не литералом:
литерал в `task_bridge.py` — и есть то второе определение, которое разъедется.
"""

from __future__ import annotations

import re
from fnmatch import fnmatchcase
from pathlib import Path

#: SSOT-файл; путь относительно корня репо (родитель пакета `governance`).
PATTERNS_PATH = (
    Path(__file__).resolve().parent.parent
    / "contracts"
    / "approval-branches"
    / "v1"
    / "patterns.env"
)

_PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")

_CANDIDATE_KEY = "APPROVAL_CANDIDATE_TEMPLATE"
_FINALIZE_KEY = "APPROVAL_FINALIZE_SUFFIX"


def _read_patterns() -> dict[str, str]:
    """Разобрать SSOT-файл в KEY=VALUE (файл парсится, не исполняется).

    Fail-closed: отсутствие файла или любого из двух ключей — исключение, а не
    вшитый дефолт. Молчаливый дефолт означал бы, что имена веток снова живут в
    двух местах, причём второе — невидимое.
    """
    try:
        text = PATTERNS_PATH.read_text(encoding="utf-8")
    except OSError as exc:  # noqa: TRY003 — путь важнее классификации
        raise RuntimeError(
            f"SSOT имён веток одобрения недоступен: {PATTERNS_PATH} ({exc})"
        ) from exc
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    for key in (_CANDIDATE_KEY, _FINALIZE_KEY):
        if not values.get(key):
            raise RuntimeError(f"в {PATTERNS_PATH} нет непустого {key}")
    return values


def candidate_template() -> str:
    """Шаблон candidate-ветки как он записан в SSOT."""
    return _read_patterns()[_CANDIDATE_KEY]


def finalize_suffix() -> str:
    """Суффикс финализирующей ветки как он записан в SSOT."""
    return _read_patterns()[_FINALIZE_KEY]


def candidate_branch(ws_id: str, wave: int, step: int) -> str:
    """Ветка шага одобрения: волна `wave`, уровень `step` внутри неё.

    Подстановка строгая: шаблон с неизвестным плейсхолдером роняет `format`
    с KeyError. Это намеренно — новый плейсхолдер обязан быть замечен здесь,
    а не молча превратиться в кривое имя ветки.
    """
    return candidate_template().format(ws_id=ws_id, wave=wave, step=step)


def finalize_branch(ws_id: str, wave: int, step: int) -> str:
    """Финализирующая ветка того же шага — конверт подписи (§I12)."""
    return candidate_branch(ws_id, wave, step) + finalize_suffix()


def candidate_glob() -> str:
    """Глоб формы candidate-ветки: плейсхолдеры шаблона → `*`."""
    return _PLACEHOLDER.sub("*", candidate_template())


def finalize_glob() -> str:
    """Глоб формы финализирующей ветки."""
    return candidate_glob() + finalize_suffix()


def is_finalize(branch: str) -> bool:
    """Ветка имеет форму финализирующей."""
    return fnmatchcase(branch, finalize_glob())


def is_candidate(branch: str) -> bool:
    """Ветка имеет форму candidate-ветки шага одобрения.

    Финализирующая форму candidate тоже удовлетворяет (она — candidate плюс
    суффикс), поэтому вызывающая сторона, которой важно РАЗЛИЧИТЬ их в
    диагностике, спрашивает `is_finalize` первым. Для решения «мержить или
    нет» различие безразлично: запрещены обе.
    """
    return fnmatchcase(branch, candidate_glob())
