"""Типизированные исходы фактов, от которых зависит терминализация (§I12).

Слой `ops` местами сознательно не различает «факта нет» и «узнать не
удалось»: `show_file` отдаёт `None` и когда ревизии нет, и когда файла в ней
нет; `delete_remote_branch` отдаёт `False` и при отсутствии прав, и когда
ветки уже нет (issue #177). Контракт (§I12, «Требование к наблюдаемости»)
обязывает реализацию либо ввести локально типизированный результат —
`FOUND`/`ABSENT`/`UNAVAILABLE`/`FORBIDDEN`, — либо честно оставить ветку
терминализации недостижимой. Этот модуль вводит тип и классифицирует по нему
ровно те примитивы, на которых стоит терминализация заявки.

ГЛАВНОЕ ПРАВИЛО, и оно же единственное место, где реализация способна
незаметно обнулить весь §I12: **в `invalidated` переводит только
положительно установленный факт**. `None`, `False`, `rc != 0`, исключение
сети и любой иной свёрнутый исход дают `UNAVAILABLE` и оставляют заявку
ЖИВОЙ. Эвристики («здесь очевидно, что файла просто нет») запрещены прямым
текстом контракта: снаружи такая эвристика выглядит как работающая
терминализация, а на деле хоронит живые заявки при первом же сбое сети.

Асимметрия цены прямая: ошибка в сторону «временно» стоит лишний круг
(оператор повторяет вызов), ошибка в сторону «постоянно» требует заново
пройти весь путь одобрения — самый дорогой шаг схемы. Fail-closed здесь
означает «не хоронить», а не «отказать».

Что НЕ типизируется и почему: `delete_remote_branch`. §I12 выводит вывод
ветки из обращения из блокирующих шагов («оставшаяся ветка — неубранный
мусор, а не опасность»), значит терминализация на нём не стоит вовсе, а
типизировать примитив ради единообразия — расширять поверхность без
предмета.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar

from governance.ops import Ops

T = TypeVar("T")


class Outcome(Enum):
    """Четыре исхода чтения факта (форма задана §I12).

    `FOUND` — факт прочитан, значение есть; `ABSENT` — прочитано, что факта
    НЕТ (примитив это различает); `FORBIDDEN` — прочитано, что факт есть, но
    он запрещает то, ради чего его читали; `UNAVAILABLE` — установить не
    удалось.

    Установленными считаются первые три: каждый из них — прочитанный,
    однозначный ответ форджи или git'а. Только они вправе терминализовать
    заявку.
    """

    FOUND = "found"
    ABSENT = "absent"
    FORBIDDEN = "forbidden"
    UNAVAILABLE = "unavailable"


#: Исходы, которые контракт считает положительно установленным фактом.
#: Перечень закрыт и определён ОДИН раз: «установлен ли факт» спрашивают в
#: нескольких местах, и второе определение разошлось бы молча.
ESTABLISHED = (Outcome.FOUND, Outcome.ABSENT, Outcome.FORBIDDEN)


@dataclass(frozen=True)
class Fact(Generic[T]):
    """Исход чтения одного факта плюс его значение и человеческий повод.

    `detail` не украшение: диагностика §I12 обязана сказать «факт не
    установлен, заявка сохранена» и назвать, КАКОЙ именно сверки это
    касается, — и не вправе приписывать конкретную причину, которой не
    устанавливала.
    """

    outcome: Outcome
    value: T | None = None
    detail: str = ""

    @property
    def established(self) -> bool:
        """Факт положительно установлен ⇒ им можно терминализовать заявку."""
        return self.outcome in ESTABLISHED


def unavailable(detail: str) -> Fact[T]:
    """Исход «установить не удалось» — заявка остаётся живой."""
    return Fact(Outcome.UNAVAILABLE, None, detail)


# --- Факт: есть ли PR на ветке заявки -----------------------------------


def search_pr(
    ops: Ops, repo_slug: str, branch: str, *, any_state: bool = False
) -> Fact[int]:
    """PR на ветке `branch`: `FOUND` номер / `ABSENT` / `UNAVAILABLE`.

    `ABSENT` здесь ЗАКОННЫЙ, и это не оценка «по контексту», а гарантия
    самого примитива: `ops.find_pr` отдаёт `None` ТОЛЬКО когда таких PR
    нет, а сбой запроса (rc != 0, битый JSON, неожиданная форма) поднимает
    `RuntimeError` — различие заведено там же и ровно за этим (финальное
    ревью #156). Классификация лишь переносит его в тип.

    Крэш-окно «коммит есть, PR ещё нет» (§I12) читает этот факт первым, но
    решение принимает НЕ по нему: имя ветки говорит, где смотреть, а
    идентичность устанавливает записанный заявкой `head_sha`.
    """
    try:
        found = ops.find_pr(repo_slug, branch, any_state=any_state)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        return unavailable(f"PR на ветке {branch}: запрос не удался ({exc})")
    if found is None:
        return Fact(Outcome.ABSENT, None, f"PR на ветке {branch} нет")
    return Fact(Outcome.FOUND, found, f"PR на ветке {branch}: #{found}")


# --- Факт: состояние PR и обстоятельства его мержа -----------------------


class Disposition(Enum):
    """Состояние PR — прочитанное, а не выведенное."""

    OPEN = "open"
    MERGED = "merged"
    CLOSED_UNMERGED = "closed_unmerged"


@dataclass(frozen=True)
class MergeEvent:
    """Forge-событие мержа: учётка, время, коммит.

    Ровно то, что §I12 называет источником подписи, и ровно столько:
    `approved_by` — учётка, которой форджа атрибутировала мерж,
    `approved_at` — время этого события. Что подпись НЕ доказывает —
    физическое присутствие человека — записано в контракте остатком, а не
    замаскировано, и этот тип не пытается доказать больше.
    """

    login: str
    merged_at: str
    commit: str


def read_pr(ops: Ops, repo_slug: str, pr: int) -> Fact[dict]:
    """Сырые факты PR: `FOUND` словарь либо `UNAVAILABLE`.

    `ABSENT` здесь не бывает намеренно. `gh pr view` отдаёт ненулевой rc и
    на несуществующий PR, и на сетевой сбой, и на отозванный токен —
    различить нечем, а «PR не существует» как постоянный отказ похоронило
    бы заявку по сетевой ошибке.
    """
    try:
        facts = ops.pr_facts(repo_slug, pr)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        return unavailable(f"факты PR #{pr}: запрос не удался ({exc})")
    if not isinstance(facts, dict) or not facts:
        return unavailable(f"факты PR #{pr}: пустой либо неожиданный ответ")
    return Fact(Outcome.FOUND, facts, f"факты PR #{pr} прочитаны")


def disposition(facts: dict) -> Fact[Disposition]:
    """Состояние PR из его фактов: `FOUND` одно из трёх либо `UNAVAILABLE`.

    Величина `state` читается как есть; `MERGED` от `CLOSED` форджа
    различает сама, и выводить одно из другого (скажем, по непустому
    `mergedAt`) значило бы завести второе определение того же факта.
    Незнакомое значение — `UNAVAILABLE`, а не «наверное открыт»: молчаливый
    дефолт здесь и есть эвристика, которую §I12 запрещает.
    """
    state = facts.get("state")
    if state == "MERGED":
        return Fact(Outcome.FOUND, Disposition.MERGED, "PR вмержен")
    if state == "CLOSED":
        return Fact(
            Outcome.FOUND, Disposition.CLOSED_UNMERGED, "PR закрыт без мержа"
        )
    if state == "OPEN":
        return Fact(Outcome.FOUND, Disposition.OPEN, "PR открыт")
    return unavailable(f"состояние PR не прочитано: state={state!r}")


def merge_event(facts: dict) -> Fact[MergeEvent]:
    """Акт мержа: `FOUND` событие / `ABSENT` / `UNAVAILABLE`.

    - `FOUND` — PR вмержен И все три величины события непусты. Это тот
      самый акт одобрения узла (§I12): подпись берётся отсюда;
    - `ABSENT` — PR ЗАКРЫТ без мержа. Факт установлен и однозначен:
      одобрения не было и уже не будет, заявка терминализуется в
      `abandoned` (человек решил не одобрять);
    - `UNAVAILABLE` — PR ОТКРЫТ (акта ещё не было — ждём человека, §I9),
      состояние не прочитано, либо вмержен, но какой-то величины события в
      ответе нет. Последний случай нарочно не `FOUND` с дырой: неполное
      событие не может ни подписать узел, ни быть сверенным фазой 3.

    Открытый PR даёт `UNAVAILABLE`, а не `ABSENT`, потому что вопрос здесь
    один — «состоялся ли акт», — и «ещё нет» ответом на него не является:
    прими его за установленное отсутствие, и живая заявка, ждущая
    человека, была бы похоронена своим же ожиданием. Кому нужно отличить
    «ждём» от «не удалось прочитать», спрашивает `disposition` — там это
    `FOUND(OPEN)`.
    """
    where = disposition(facts)
    if where.value is Disposition.CLOSED_UNMERGED:
        return Fact(Outcome.ABSENT, None, "PR закрыт без мержа — акта не было")
    if where.value is not Disposition.MERGED:
        return unavailable(f"акт мержа не установлен: {where.detail}")
    merged_by = facts.get("mergedBy")
    login = merged_by.get("login") if isinstance(merged_by, dict) else None
    merged_at = facts.get("mergedAt")
    commit = facts.get("mergeCommit")
    oid = commit.get("oid") if isinstance(commit, dict) else None
    missing = [
        name
        for name, value in (
            ("mergedBy.login", login),
            ("mergedAt", merged_at),
            ("mergeCommit.oid", oid),
        )
        if not value
    ]
    if missing:
        return unavailable(
            f"PR вмержен, но факты события неполны: нет {', '.join(missing)}"
        )
    return Fact(
        Outcome.FOUND,
        MergeEvent(str(login), str(merged_at), str(oid)),
        f"мерж от {login} в {merged_at}",
    )


# --- Факт: создаёт ли этот мерж подпись ----------------------------------

#: Единственная точка настройки списка авторизованных approver-учёток
#: (§I12). Имя говорит про АВТОРИЗАЦИЮ, а не про человечность: «список
#: человеческих merger-логинов» обещал бы ровно то, чего механизм не
#: проверяет. Дефолт — ПУСТО, то есть «подписать не может никто».
#:
#: Не путать с allowlist'ом §I10 (`REPLACEMENT_REVIEW_ALLOWLIST`): у них
#: разные предметы и противоположная полярность — тот перечисляет, чьё
#: ревью НЕ блокирует замену, этот — чей мерж СОЗДАЁТ подпись. Общее
#: только fail-closed по умолчанию.
APPROVER_ALLOWLIST_ENV = "AUTHORIZED_APPROVER_ACCOUNTS"


def approver_allowlist() -> frozenset[str]:
    """Учётки, чей мерж создаёт подпись; по умолчанию — НИ ОДНОЙ.

    Пустой дефолт — не заготовка, а поведение: пока список не выставлен
    явно, подписать не может никто. Получить непустой случайно нельзя —
    имя переменной уникально, значение перечисляется поимённо, пустые
    элементы отбрасываются.

    Учётки ревью-контура здесь нет и по умолчанию быть не может: опознание
    контура через непустой дефолт §I12 убрал как вторую, несовместимую
    семантику «списка учёток» рядом с той, которую репо уже принял в §I10.
    """
    raw = os.environ.get(APPROVER_ALLOWLIST_ENV, "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def authorized_signature(event: MergeEvent) -> Fact[MergeEvent]:
    """Создаёт ли этот мерж подпись: `FOUND` либо `FORBIDDEN`.

    `FORBIDDEN` — положительно установленный факт: учётка прочитана, её
    нет в `authorized_approver_accounts`, и агентский мерж подписи не
    создаёт. Это законная дорога в `invalidated` — заявка терминальна с
    причиной, восстановление идёт новым candidate (§I12).

    `UNAVAILABLE` здесь не бывает: пустой allowlist — не «не удалось
    прочитать конфигурацию», а прочитанное «подписать не может никто».
    """
    if event.login in approver_allowlist():
        return Fact(Outcome.FOUND, event, f"{event.login} авторизован")
    return Fact(
        Outcome.FORBIDDEN,
        event,
        f"мерж от {event.login}: учётки нет в "
        f"{APPROVER_ALLOWLIST_ENV} — подписи этот мерж не создаёт",
    )


# --- Факт: байты узла в base --------------------------------------------


def read_blob_text(
    ops: Ops, target_dir: str, ref: str, path: str
) -> Fact[str]:
    """Текст файла в ревизии: `FOUND` либо `UNAVAILABLE` — и НИКОГДА `ABSENT`.

    `ops.show_file` отдаёт `None` и когда ревизии нет, и когда файла в ней
    нет (issue #177). Прочитать из этого «файла нет» — ровно та эвристика,
    которую §I12 запрещает прямым текстом: ветка терминализации стала бы
    достижимой на вид, а на деле хоронила бы заявки при каждом сбое
    выборки. Пока #177 не закрыт, отсутствие файла в `base` остаётся
    неклассифицируемым, и это законный исход, а не недоделка.

    Пустой файл — `FOUND` с пустой строкой, а не «нет»: `show_file`
    возвращает `""` и `None` РАЗНЫМИ величинами, и сворачивать их в одну
    значило бы завести ту же эвристику через ложность строки.

    Сверки фазы 3 при этом достижимы полностью: они сравнивают
    ПРОЧИТАННЫЕ байты (`FOUND`) с записанным в заявке, а расхождение —
    положительно установленный семантический факт.
    """
    try:
        text = ops.show_file(target_dir, ref, path)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        return unavailable(f"{ref}:{path}: чтение не удалось ({exc})")
    if text is None:
        return unavailable(
            f"{ref}:{path}: вывод не удался — «нет ревизии» и «нет файла» "
            "слой ops не различает (issue #177)"
        )
    return Fact(Outcome.FOUND, text, f"{ref}:{path} прочитан")


# --- Факт: закрытие PR подтверждено --------------------------------------


def confirm_closed(
    ops: Ops, repo_slug: str, pr: int, comment: str
) -> Fact[int]:
    """Закрытие PR: `FOUND` подтверждено либо `UNAVAILABLE`.

    `ops.close_pr` отдаёт `False` и при отсутствии прав, и когда PR уже
    закрыт — различать нечем (#177). Значит неподтверждённое закрытие
    остаётся неустановленным фактом, и §I12 говорит, что делать: операция
    над upstream остаётся возобновляемой и mergeable candidate НЕ
    публикует. Порядок «сначала инвалидация, потом публикация» — не
    предпочтение, а единственный способ закрыть окно, в котором mergeable
    предложение ниже переживает своё основание.
    """
    try:
        closed = ops.close_pr(repo_slug, pr, comment)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        return unavailable(f"закрытие PR #{pr} не удалось ({exc})")
    if not closed:
        return unavailable(
            f"закрытие PR #{pr} не подтверждено — «нет прав» и «уже закрыт» "
            "слой ops не различает (issue #177)"
        )
    return Fact(Outcome.FOUND, pr, f"PR #{pr} закрыт")
