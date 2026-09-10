"""Имена веток двухфазного одобрения узлов бандла (§I12) — python-половина.

SSOT — не этот модуль, а `contracts/approval-branches/v1/patterns.env`:
шаблон имени лежит там, и вторая половина (`merge-pr.sh`, гвард агентского
мержа) читает ТОТ ЖЕ файл. Здесь только вывод из шаблона — имя подстановкой
значений, глоб по правилу `_template_glob`.

Почему шаблон, а не пара «строитель имени + отдельный глоб»: глоб, выведенный
из шаблона механически, не может от него отстать. Дописывание номера заявки
меняет одну строку в одном файле, и обе стороны едут за ней сами. Две
независимо написанные строки разъезжаются молча — это уже произошло дважды:
`_approve_branch` строил `spec/<ws-id>-bundle-approve`, а контракт за сутки
сменил арность нумерации с `<W>-<K>` на `<W>-<K>-<A>`.

Механика одобрения обязана строить имена ЧЕРЕЗ этот модуль, а не литералом:
литерал в `task_bridge.py` — и есть то второе определение, которое разъедется.

Имя ветки — не идентичность PR, а лишь его форма: контракт (§I12) обязывает
`--approve-node` вешать метку `human-merge-required` в самом вызове создания
обоих PR, и гвард мержа проверяет ОБА признака независимо.
"""

from __future__ import annotations

import re
from fnmatch import fnmatchcase
from pathlib import Path

from governance import ssot_env

#: SSOT-файл; путь относительно корня репо (родитель пакета `governance`).
PATTERNS_PATH = (
    Path(__file__).resolve().parent.parent
    / "contracts"
    / "approval-branches"
    / "v1"
    / "patterns.env"
)

_PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
#: Соседние `*`, разделённые одним разделителем — схлопываются в один.
_ADJACENT_STARS = re.compile(r"\*[-._]\*")

_CANDIDATE_KEY = "APPROVAL_CANDIDATE_TEMPLATE"
_FINALIZE_KEY = "APPROVAL_FINALIZE_SUFFIX"


def _read_patterns() -> dict[str, str]:
    """Оба ключа SSOT-файла; разбор — общий (`ssot_env`, там же правила).

    Fail-closed: недоступный файл, отсутствующий ключ, дубль ключа, пустое
    значение — исключение, а не вшитый дефолт. Молчаливый дефолт означал бы,
    что имена веток снова живут в двух местах, причём второе — невидимое.
    """
    what = "SSOT имён веток одобрения"
    return {
        key: ssot_env.read_key(PATTERNS_PATH, key, what)
        for key in (_CANDIDATE_KEY, _FINALIZE_KEY)
    }


def candidate_template() -> str:
    """Шаблон candidate-ветки как он записан в SSOT."""
    return _read_patterns()[_CANDIDATE_KEY]


def finalize_suffix() -> str:
    """Суффикс финализирующей ветки как он записан в SSOT."""
    return _read_patterns()[_FINALIZE_KEY]


def candidate_branch(ws_id: str, wave: int, step: int, attempt: int) -> str:
    """Ветка заявки на одобрение: волна `wave`, уровень `step`, заявка
    `attempt` внутри шага (§I12).

    Подстановка строгая: шаблон с неизвестным плейсхолдером роняет `format`
    с KeyError. Это намеренно — новый плейсхолдер обязан быть замечен здесь,
    а не молча превратиться в кривое имя ветки.
    """
    return candidate_template().format(
        ws_id=ws_id, wave=wave, step=step, attempt=attempt
    )


def finalize_branch(ws_id: str, wave: int, step: int, attempt: int) -> str:
    """Финализирующая ветка той же заявки — конверт подписи (§I12)."""
    return candidate_branch(ws_id, wave, step, attempt) + finalize_suffix()


def _template_glob(template: str) -> str:
    """Глоб ФОРМЫ имени: плейсхолдеры → `*`, соседние `*` схлопываются.

    Второй шаг существен. Наивная замена дала бы глоб, привязанный к арности
    нумерации (`spec/*-approve-*-*-*`), и ветка ПРЕЖНЕЙ формы, `<W>-<K>` без
    номера заявки, прошла бы мимо гварда — дыра ровно там, где контракт
    менялся. После схлопывания обе арности дают один глоб
    `spec/*-approve-*`: опознаётся форма (литерал `-approve-` плюс хвост),
    а не то, сколько чисел в хвосте сегодня.
    """
    glob = _PLACEHOLDER.sub("*", template)
    while True:
        collapsed = _ADJACENT_STARS.sub("*", glob)
        if collapsed == glob:
            return glob
        glob = collapsed


def candidate_glob() -> str:
    """Глоб формы candidate-ветки."""
    return _template_glob(candidate_template())


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
