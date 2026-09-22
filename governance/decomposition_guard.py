"""Гард decomposition-узла: DT-грамматика и инварианты графа (спека
2026-09-05-decomposition-node §3). Чистые функции над строками — без
git/ФС/steward; переиспользуются S4-гейтом (runner) и мостом
(task_bridge). Канон модуля — governance/design_guard.py."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from collections.abc import Mapping
from typing import NamedTuple

import yaml

from governance.frontmatter import split_frontmatter
from governance.spec_runner_contract import (
    SelectorPolicy,
    is_file_target_form,
)

# Маркер-подстрока non-fatal находки формы «осиротевший/опечатанный путь
# в verifies» (round 7 ревью PR #161, минор) — единственная точка истины
# её текста, используется в `_orphan_verifies_findings` НИЖЕ (единственный
# продьюсер). Эта находка НИКОГДА не попадает в `graph_findings` (её
# fatal-результат про неё не знает — она её не вычисляет и не
# фильтрует) — публичная точка входа `non_fatal_findings` вычисляет её
# ОТДЕЛЬНО; единственный потребитель — S4-гейт `governance.runner.
# _step_gate` (round 6/7/8 ревью PR #161), пишет её как
# `warning GC-DT-GRAPH:` в gate-findings.txt, не останавливая прогон:
# элемент verifies, не являющийся checked_by-целью НИ ОДНОЙ задачи бандла.
_ORPHAN_VERIFIES_TARGET_MARKER = "опечатка либо осиротевший путь"
# Маркер-подстрока находки «группа наблюдения не выводится вовсе» — теперь
# FATAL (round 13 ревью PR #161, минор — до этого была non-fatal
# `_VERIFY_GROUP_UNDERIVABLE_MARKER`-находка формы; промотирована в
# `graph_findings`, см. её докстринг: условие тождественно тому, при
# котором `render_tasks_dt` детерминированно поднимает RuntimeError —
# «не блокирует доставку» было ложью именно для этого входа).
_VERIFY_GROUP_UNDERIVABLE_MARKER = "группа наблюдения не выводится"


_DT_HEAD_RE = re.compile(
    r"^####\s+(DT-\d+):\s*(.+?)\s*·\s*type:\s*(implement|verify)"
    r"\s*·\s*owner:\s*(\S+)\s*$",
    re.M,
)
# near-miss: начинается как DT-заголовок, но строгую грамматику не прошёл;
# широкая форма id ([^\s:]*) — суффиксный мусор (DT-3a) тоже находка
_DT_NEAR_RE = re.compile(r"^####\s+(DT-[^\s:]*)", re.M)
_SECTION_RE = re.compile(r"^#{1,3}\s", re.M)


def _list_field(block: str, name: str) -> tuple[str, ...] | None:
    m = re.search(rf"^{name}:\s*\[([^\]]*)\]\s*$", block, re.M)
    if m is None:
        return None
    inner = m.group(1).strip()
    if not inner:
        return ()
    return tuple(part.strip() for part in inner.split(","))


# Форма блочного элемента (round 14 ревью PR #161, major, контракт
# владельца — откат чрезмерной строгости round 13): отступ ОПЦИОНАЛЕН —
# ОБЕ формы валидны и обязаны парситься: элементы под `verifies:` С
# отступом (`  - <путь>`) И элементы БЕЗ отступа, столбец 0 (`- <путь>`
# прямо под ключом) — обе формы валидный YAML, и промпт авторинга
# (`_AUTHOR_DSL["decomposition"]`) никогда не требовал отступа, только
# «one `- <file>` per line». Строгая (round 13, обязательный отступ) версия
# делала конформный по промпту бандл фатальной находкой формы — стопила
# S4-гейт и deliver() на честно авторенном входе. ОБЯЗАТЕЛЕН пробел после
# `-` и ровно один непустой DSL-токен после него; список завершается на
# первой строке, которая НЕ является ни `- ` элементом (в любой форме),
# ни пустой строкой (round 13 minor, сохранено).
# Selector — один DSL-токен, как ``checked_by target``; пробельная проза
# после ``-`` принадлежит Markdown-телу DT, а не группе наблюдения (#162).
_LIST_ITEM_RE = re.compile(r"^([ \t]*)-[ \t]+(\S+)[ \t]*$")


def _block_list_field(block: str, name: str) -> tuple[str, ...] | None:
    """`name:` в блочной YAML-форме (`- элемент` построчно, без `[...]`).

    None — ключ `name:` НЕ найден в блоке вовсе (форма отсутствует).
    Пустой кортеж `()` НЕ возвращается: если ключ найден, но за ним нет ни
    одной валидной `- <элемент>` строки (round 13 ревью PR #161, major) —
    тоже None, чтобы вызывающая сторона (`_verifies_field`/`parse_dt_tasks`)
    могла отличить «поля нет вовсе» (легаси, не находка) от «поле есть, но
    не разобрано» (находка формы) — раньше оба случая молча сливались в
    один пустой кортеж.
    """
    m = re.search(rf"^{name}:\s*$", block, re.M)
    if m is None:
        return None
    items: list[str] = []
    # m.end() стоит ПЕРЕД '\n' ($ в re.M его не поглощает) — первый элемент
    # splitlines() всегда пустая строка-остаток заголовка, отбрасываем её.
    item_indent: str | None = None
    after_blank = False
    for line in block[m.end():].splitlines()[1:]:
        item = _LIST_ITEM_RE.match(line)
        if item is None:
            # Пустая строка список НЕ обрывает (round 14 ревью PR #161,
            # major): в YAML блочный список продолжается через пустые
            # строки, и обрыв на первой из них молча терял всё, что за
            # ней — без находки формы, без closure-проверки и без строки
            # в `**Verifies:**`. Список кончается на первой НЕПУСТОЙ
            # строке, не являющейся `- <элемент>`.
            if not line.strip():
                after_blank = bool(items)
                continue
            break
        indent = item.group(1)
        # Пустая строка допустима ВНУТРИ YAML-списка, но только пока
        # продолжается тот же уровень отступа. Смена уровня после пустой
        # строки — соседний Markdown-список прозы, не selector entry
        # (#162); без этой границы его пункты уезжали в **Verifies:**.
        if items and after_blank and indent != item_indent:
            break
        if item_indent is None:
            item_indent = indent
        items.append(item.group(2))
        after_blank = False
    return tuple(items) if items else None


# Ключ `verifies:` присутствует в блоке В ЛЮБОЙ форме (скаляр, блочный
# список, инлайн-список, что угодно) — используется ТОЛЬКО чтобы отличить
# «поля нет вовсе» (легаси, молчание — не находка) от «поле объявлено, но
# не разобрано ни одной принятой формой» (round 13, major — находка формы).
_VERIFIES_KEY_RE = re.compile(r"^verifies:", re.M)


#: Классы TDD-waiver'а — ЗАКРЫТЫЙ словарь (спека decomposition-node §3a).
#: Закрытость и есть замена эвристике по слову «waiver» в прозе: при
#: открытом словаре санкцией становилось бы любое слово, которое автор
#: счёл подходящим, то есть догадка вместо санкции. Новый класс требует
#: правки спеки — тот же порядок, что у нового статуса узла в §I12.
#: Условия класса — ЗДЕСЬ, рядом с самим классом, и словарь классов из
#: них выводится. Класс без условий поэтому невозможен построением: не
#: «забыли дописать условия», а нечего объявлять. Мост печатает эти же
#: строки дословно — второй редакции условий не заводится, иначе бандл и
#: доставленная задача разошлись бы молча.
WAIVER_CONDITIONS: dict[str, tuple[str, ...]] = {
    "characterisation": (
        "поведение уже доставлено зависимостями задачи",
        "задача добавляет отсутствующее characterisation/acceptance-покрытие",
        "честный baseline RED невозможен",
        "новый тест имеет negative control, доказывающий, что он краснеет "
        "при нарушении свойства",
        "baseline-sha зафиксирован фактический на старте задачи",
    ),
}

WAIVER_CLASSES = tuple(WAIVER_CONDITIONS)

#: Объявление waiver'а: класс и санкция ОДНОЙ строкой, разделитель ` · `
#: — тот же, что в заголовке DT. Одной строкой, а не двумя ключами,
#: намеренно: два ключа делали бы достижимым состояние «класс без
#: санкции», а здесь строка либо разбирается целиком, либо это находка.
_WAIVER_RE = re.compile(
    r"^tdd_waiver:\s*(\S+)\s*·\s*sanction:\s*(\S+)\s*$", re.M
)
_WAIVER_KEY_RE = re.compile(r"^tdd_waiver:", re.M)

#: Негативный контроль (devtools#336, spec-runner#428): селектор теста,
#: который ОБЯЗАН покраснеть под патчем, ломающим проверяемое свойство.
#: Один токен без пробелов — намеренно: разделитель потребителя ` :: `
#: (с пробелами по обе стороны) внутри значения дал бы у него
#: неоднозначное деление, а пробел в пути — селектор, которого никто не
#: писал. Путь патча здесь НЕ объявляется: его выводит мост из номера
#: задачи, которого DT не знает.
#: `[ \t]*`, а не `\s*`: в многострочном режиме `\s*` перешагивает перенос
#: строки и берёт селектором ПЕРВОЕ слово следующей строки — пустое
#: значение читалось бы как валидное.
_CONTROL_RE = re.compile(r"^negative_control:[ \t]*(\S+)[ \t]*$", re.M)
_CONTROL_KEY_RE = re.compile(r"^negative_control:", re.M)

#: Санкция — ЗАКРЫТАЯ грамматика, а не свободный текст: иначе поле есть
#: украшение, и `sanction: потому-что-можно` прошло бы наравне с
#: настоящим решением. Две формы, обе машиночитаемы: датированное решение
#: владельца и ссылка на PR/issue репозитория.
_SANCTION_BATCH_RE = re.compile(r"^batch-approve-(\d{4}-\d{2}-\d{2})$")
_SANCTION_REF_RE = re.compile(r"^[A-Za-z0-9][\w.-]*#\d+$")
SANCTION_FORMS = "batch-approve-<YYYY-MM-DD> либо <repo>#<номер>"


def _sanction_is_valid(sanction: str) -> bool:
    """Санкция соответствует закрытой грамматике (спека §3a).

    Дата проверяется КАЛЕНДАРНО, а не только по виду: `2026-13-45` —
    строка нужной формы, но не дата, и принять её значило бы проверить
    форму формы. `date.fromisoformat` отвергает и несуществующий месяц, и
    неполные `2026-9-9`.

    Существование того, на что ссылка указывает, НЕ проверяется, и это
    осознанный отказ: модуль — чистые функции над строками, его зовут и
    гейт, и мост, а сеть внутри сделала бы отказ гейта зависящим от
    доступности форджа — свёрнутый исход начал бы закрывать дверь. Форма
    отсекает опечатку и произвол; за то, что названное решение принято,
    отвечает владелец, и подпись под санкцией адресна.

    Дата в будущем тоже не отвергается: «сегодня» внутри чистой функции
    сделало бы результат гварда зависящим от момента запуска.
    """
    batch = _SANCTION_BATCH_RE.match(sanction)
    if batch is not None:
        try:
            date.fromisoformat(batch.group(1))
        except ValueError:
            return False
        return True
    return _SANCTION_REF_RE.match(sanction) is not None


@dataclass(frozen=True)
class DtWaiver:
    """Разобранное объявление TDD-waiver'а: класс и ссылка на санкцию.

    Величины хранятся РАЗДЕЛЬНО, а не строкой: мост печатает условия
    КЛАССА, и выводить класс повторным разбором строки значило бы
    завести второго вычислителя одного факта — ровно то, что мы сводим
    в одно место везде остальное.
    """

    node_class: str
    sanction: str
    #: Селектор негативного контроля (devtools#336). Живёт НА waiver'е,
    #: а не отдельным полем DT: контроль без снятого обязательства не
    #: имеет предмета, а waiver без контроля spec-runner ≥ 3.0.0 отказывает
    #: до платного вызова — ни одно из двух состояний не должно быть
    #: достижимым, и структура держит это построением.
    control_selector: str


def _control_field(block: str, dt_id: str) -> tuple[str | None, list[str], bool]:
    """`negative_control:` → (селектор, находки формы, ключ_присутствует).

    Та же дисциплина, что у `tdd_waiver`: число ключей считается раньше
    разбора, дубль и неразобранная форма — находки, не молчание.
    Парность с waiver'ом судит вызывающий: этой функции waiver не виден.
    """
    keys = len(_CONTROL_KEY_RE.findall(block))
    if keys == 0:
        return None, [], False
    matches = _CONTROL_RE.findall(block)
    if keys > 1:
        return None, [
            f"{dt_id}: negative_control объявлен {keys} раза "
            "(ожидается ровно один)"
        ], True
    if keys != len(matches):
        return None, [
            f"{dt_id}: поле negative_control объявлено, но не разобрано — "
            "ожидается `negative_control: <селектор>`: один токен без "
            "пробелов, без ` :: `"
        ], True
    return matches[0], [], True


def _verifies_field(block: str) -> tuple[str, ...] | None:
    """`verifies:` — инлайн (`[a, b]`) ИЛИ блочная (`- a`) форма, обе
    принимаются (спека владельца по DT-14, FIX 1). None — НИ ОДНА форма не
    дала ни одного элемента (ключ либо отсутствует вовсе, либо присутствует
    в нераспознанном виде — вызывающая сторона разбирается через
    `_VERIFIES_KEY_RE`, эта функция два случая не различает)."""
    inline = _list_field(block, "verifies")
    if inline is not None:
        return inline
    return _block_list_field(block, "verifies")


@dataclass(frozen=True)
class DtTask:
    """Одна задача decomposition-узла (заголовок #### DT-NN).

    ``verifies`` — структурное поле группы наблюдения (owner ruling,
    multi-file DT-14): список объявленных селекторов, за которыми присматривает
    ``type: verify`` DT, СТРУКТУРНО отдельный от ``checked_by``-владения
    (которое несут ``scenarios``) и НЕ участвующий в single-owner
    инварианте ``graph_findings`` вовсе (round 4 ревью PR #161, finding 3:
    исключение по verifies снято — тот же файл, дошедший до проверки через
    ``scenarios``/``checked_by``, ВСЕГДА заявка на редактирующее владение,
    независимо от того, что ещё перечислено в ``verifies``; владение
    всегда старше наблюдения).
    """

    dt_id: str
    title: str
    type: str
    owner: str
    scenarios: tuple[str, ...]
    depends_on: tuple[str, ...]
    delivered_by: tuple[str, ...]
    parallel_group: str
    verifies: tuple[str, ...] = ()
    waiver: DtWaiver | None = None
    #: Объявленные результаты поставки (спека §3b). Пустой кортеж у
    #: легаси-DT неотличим от `delivers: []` НАМЕРЕННО: судит эту разницу
    #: `dt_contract_findings` под объявленной версией, а не парсер —
    #: иначе каждый легаси-бандл получал бы находку формы оттуда, где
    #: версия вообще не читается.
    delivers: tuple[Deliverable, ...] = ()


def _waiver_field(
    block: str, dt_id: str, dt_type: str, depends_on: tuple[str, ...],
) -> tuple[DtWaiver | None, list[str]]:
    """Объявление TDD-waiver'а либо находки — fail-closed в обе стороны.

    Проекция (спека decomposition-node §3a) состоит из трёх разных
    носителей: `Mode: standard` выбирает ослабленный режим исполнения,
    `TDD-waiver` делает его адресным для spec-runner, а checklist несёт
    условия класса человеку. Durable-объявление DT — источник всех трёх.
    Поэтому здесь не «разрешить побольше», а отказать всюду, где
    объявление не доказано.

    Четыре находки, и ни одна не молчит:

    1. **ключ есть, форма не разобрана.** Тот же урок, что у `verifies`
       (round 13 ревью PR #161): молчаливая деградация до «объявления не
       было» неотличима от легаси-DT, и задача уехала бы в проектный
       `tdd` с непройденным RED — ровно тот останов, ради которого
       проекция заводится;
    2. **неизвестный класс.** Словарь закрыт; открытый означал бы, что
       санкцией становится любое подходящее по мнению автора слово;
    3. **waiver у `type: verify`.** Не «избыточен», а противоречив: у
       verify свой режим исполнения, и какой из двух попал бы в
       `**Mode:**`, решал бы порядок строк рендера;
    4. **waiver без зависимостей.** Условие 1 класса — «поведение уже
       доставлено ЗАВИСИМОСТЯМИ задачи»; у DT без единой зависимости
       доставлять его нечем, и объявление противоречит собственному
       классу — принять его значило бы снять RED там, где честный RED
       как раз возможен.

    Чего проверка НЕ делает и почему. Условие «зависимости в статусе
    DONE» из текста декомпозиции не наблюдаемо вовсе: статусы живут в
    доставленной tasks-спеке, а сюда приходит только бандл. Гвард
    проверяет сильнейший СТРУКТУРНЫЙ факт, который в его входе есть, —
    что зависимости объявлены; на остальное отвечает адресная санкция
    владельца. Выдавать структурную проверку за проверку исполнения
    здесь нельзя ровно по тому же правилу, по которому неустановленный
    факт не открывает дверь.
    """
    findings: list[str] = []
    selector, control_findings, control_declared = _control_field(block, dt_id)
    findings += control_findings
    matches = _WAIVER_RE.findall(block)
    # Сначала считается ЧИСЛО КЛЮЧЕЙ (минор ревью #197). Ветка `keys > 1`
    # выше всего и ловит смешанный случай «битая + валидная» именно как
    # неоднозначный дубль. Ниже keys уже только 0 или 1, поэтому mismatch
    # означает единственный неразобранный ключ; сравнение счётчиков само
    # по себе смешанный случай не ловит (#198).
    keys = len(_WAIVER_KEY_RE.findall(block))
    if keys > 1:
        findings.append(
            f"{dt_id}: tdd_waiver объявлен {keys} раза "
            "(ожидается ровно один)"
        )
        return None, findings
    if keys != len(matches):
        findings.append(
            f"{dt_id}: поле tdd_waiver объявлено, но не разобрано — "
            "ожидается `tdd_waiver: <класс> · sanction: <id>`"
        )
        return None, findings
    if not matches:
        if control_declared:
            # Зеркало отказа потребителя (`validate`: «Negative-control без
            # TDD-waiver»): контроль без снятого обязательства не имеет
            # предмета, и молчать здесь значило бы отдать отказ соседу.
            findings.append(
                f"{dt_id}: negative_control объявлен без tdd_waiver — "
                "контроль без снятого обязательства не имеет предмета"
            )
        return None, findings
    node_class, sanction = matches[0]
    # Четыре условия проверяются ВСЕ, а не до первого отказа: у гейта
    # правило «показывает всё сразу, не по одной», и ранний возврат
    # прятал бы вторую причину за первой — оператор чинил бы объявление
    # кругами, по одной находке за заход. Порядок в перечне ни на что не
    # влияет ровно потому, что до конца доходят все.
    if node_class not in WAIVER_CLASSES:
        findings.append(
            f"{dt_id}: класс waiver'а {node_class!r} не известен контракту "
            f"(допустимы {', '.join(WAIVER_CLASSES)})"
        )
    if not _sanction_is_valid(sanction):
        findings.append(
            f"{dt_id}: sanction {sanction!r} не соответствует форме — "
            f"допустимы {SANCTION_FORMS}"
        )
    if dt_type == "verify":
        findings.append(
            f"{dt_id}: tdd_waiver запрещён при type: verify — у задачи "
            "проверки свой режим исполнения (verify_first)"
        )
    if not depends_on:
        findings.append(
            f"{dt_id}: tdd_waiver объявлен у задачи без зависимостей — "
            "класс требует, чтобы поведение было доставлено зависимостями, "
            "а доставлять его нечем"
        )
    if not control_declared:
        # Пятое условие (devtools#336). spec-runner ≥ 3.0.0 (#428)
        # отказывает waived-задаче без `**Negative-control:**` до первого
        # платного вызова, терминально; мост печатает эту строку ТОЛЬКО из
        # объявления DT. Пропустить объявление здесь значило бы доставить
        # задачу, которая выглядит запускаемой и не запускается никогда.
        findings.append(
            f"{dt_id}: tdd_waiver объявлен без negative_control — "
            "spec-runner ≥ 3.0.0 отказывает waived-задаче без "
            "`**Negative-control:**` до платного вызова; ожидается "
            "`negative_control: <селектор теста, который краснеет под патчем>`"
        )
    if findings or selector is None:
        return None, findings
    return DtWaiver(
        node_class=node_class, sanction=sanction, control_selector=selector,
    ), findings


def parse_dt_tasks(text: str) -> tuple[list[DtTask], list[str]]:
    """DT-грамматика → (задачи, findings формы).

    Findings формы (не графа — граф в graph_findings): near-miss
    заголовок, дубль DT-id, отсутствие scenarios/depends_on/
    parallel_group, пустой scenarios. Блок задачи ограничен следующим
    DT-заголовком ЛИБО следующей секцией уровня 1–3 (урок major'а
    PR #145 — хвост документа не читается как метаданные последней
    задачи).
    """
    findings: list[str] = []
    strict = {m.start() for m in _DT_HEAD_RE.finditer(text)}
    for near in _DT_NEAR_RE.finditer(text):
        if near.start() not in strict:
            findings.append(
                f"{near.group(1)}: заголовок не соответствует машинной "
                "грамматике DT (`#### DT-NN: <название> · type: "
                "implement|verify · owner: <роль>`)"
            )
    matches = list(_DT_HEAD_RE.finditer(text))
    seen: dict[str, int] = {}
    tasks: list[DtTask] = []
    for idx, m in enumerate(matches):
        dt_id = m.group(1)
        seen[dt_id] = seen.get(dt_id, 0) + 1
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[m.end() : end]
        section = _SECTION_RE.search(block)
        if section is not None:
            block = block[: section.start()]
        scenarios = _list_field(block, "scenarios")
        depends_on = _list_field(block, "depends_on")
        delivered_by = _list_field(block, "delivered_by") or ()
        verifies_parsed = _verifies_field(block)
        # Неразобранное поле — находка формы, не тихое «поля нет» (round
        # 13 ревью PR #161, major, контракт владельца): ключ `verifies:`
        # присутствует в блоке в КАКОЙ-ТО форме (скаляр, пустой блочный
        # список без элементов, что угодно нераспознанное), но ни инлайн-,
        # ни блочная форма не дала ни одного элемента. Раньше это молча
        # деградировало до пустого кортежа — неотличимо от «поля нет
        # вовсе» (легаси-бандл), и файл ни разу не проверялся closure-
        # инвариантом и не попадал в доставленную tasks-спеку.
        if verifies_parsed is None and _VERIFIES_KEY_RE.search(block):
            findings.append(
                f"{dt_id}: поле verifies объявлено, но не разобрано — "
                "ожидается блочный список `- <путь>` (отступ необязателен) "
                "либо "
                "инлайн `verifies: [<путь>, …]`"
            )
        verifies = verifies_parsed or ()
        dt_type = m.group(3)
        group_m = re.search(r"^parallel_group:\s*(\S+)\s*$", block, re.M)
        if scenarios is None or not scenarios:
            findings.append(f"{dt_id}: строка scenarios отсутствует или пуста")
        if depends_on is None:
            findings.append(f"{dt_id}: строка depends_on отсутствует")
        if group_m is None:
            findings.append(f"{dt_id}: строка parallel_group отсутствует")
        # verifies — структурное поле группы НАБЛЮДЕНИЯ (owner ruling,
        # DT-14): рекомендовано у type: verify (объявляется, когда нужна
        # multi-file группа наблюдения отдельно от checked_by-владения) и
        # ЗАПРЕЩЕНО у type: implement (checked_by через scenarios —
        # единственный канал ВЛАДЕНИЯ implement-задач). «Группа наблюдения
        # не выводится вовсе» (verify без verifies И без checked_by-целей
        # в scenarios) — тоже находка, но она требует bindings behaviour-
        # spec (какой BEH какой checked_by-таргет несёт), которых
        # `parse_dt_tasks` не видит (только decomposition_text) — эта
        # проверка живёт в `graph_findings` (round 7 ревью PR #161, минор,
        # контракт владельца: условие обязано учитывать легаси-форму —
        # verify-DT с checked_by-целями через scenarios И БЕЗ verifies не
        # находка, находка только когда группу вывести решительно не из
        # чего).
        if dt_type == "implement" and verifies:
            findings.append(f"{dt_id}: verifies запрещён при type: implement")
        # Находки формы `delivers` здесь СОЗНАТЕЛЬНО отбрасываются: их
        # единственный судья — `dt_contract_findings`, который один знает
        # объявленную версию диалекта. Парсер версии не читает, и,
        # вынеси он те же находки, легаси-бандл краснел бы оттуда, где
        # режим совместимости не виден вовсе. Дефектная запись при этом
        # до моста не доезжает по другому пути: гейт останавливает
        # прогон на ошибках `GC-DT-CONTRACT` раньше доставки.
        _, delivers = _parse_delivers(dt_id, block)
        waiver, waiver_findings = _waiver_field(block, dt_id, dt_type,
                                                depends_on or ())
        findings += waiver_findings
        tasks.append(DtTask(
            dt_id=dt_id, title=m.group(2), type=dt_type,
            owner=m.group(4), scenarios=scenarios or (),
            depends_on=depends_on or (), delivered_by=delivered_by,
            parallel_group=group_m.group(1) if group_m else "",
            verifies=verifies, waiver=waiver,
            delivers=tuple(delivers),
        ))
    for dt_id, count in seen.items():
        if count > 1:
            findings.append(
                f"{dt_id}: объявлен {count} раза (ожидается ровно один)"
            )
    return tasks, findings


# Та же строгая грамматика, что _BEH_HEADER моста (`: <название>`
# обязательны) — minor круга 4: расхождение (гард видит `#### BEH-02` без
# двоеточия, мост — нет) давало бы зелёный гейт и пустую задачу в
# tasks-спеке.
# [a-z]?-суффикс — как у _BEH_HEADER моста (BEH-18a из раундов ревью).
# NEAR ловит ЛЮБОЙ BEH-подобный заголовок (BEH-18A, BEH-18ab, BEH-18a2):
# неизвестная форма id — находка формы, не молчание; узкий NEAR с \b
# был слеп к суффиксам вне [a-z]? — тот же fail-open, что и до фикса
# (major ревью PR #148).
_BEH_HEAD_RE = re.compile(r"^####\s+(BEH-\d+[a-z]?):\s*\S", re.M)
_BEH_NEAR_RE = re.compile(r"^####\s+(BEH-[^\s:]*)", re.M)
_BEH_CHECKED_RE = re.compile(
    r"\*\*checked_by\*\*.*?`kind:\s*(\S+?)`.*?`target:\s*(\S+?)`"
)


def _parse_beh_binding_records(
    text: str,
) -> dict[str, tuple[str | None, str | None, str | None]]:
    """beh_id → (file, kind, declared target), taking the last binding."""

    heads = list(_BEH_HEAD_RE.finditer(text))
    result: dict[str, tuple[str | None, str | None, str | None]] = {}
    for idx, match in enumerate(heads):
        end = heads[idx + 1].start() if idx + 1 < len(heads) else len(text)
        block = text[match.start() : end]
        checked = None
        for checked in _BEH_CHECKED_RE.finditer(block):
            pass
        if checked is None:
            result[match.group(1)] = (None, None, None)
        else:
            declared = checked.group(2)
            result[match.group(1)] = (
                declared.split("::", 1)[0],
                checked.group(1),
                declared,
            )
    return result


def _parse_beh_bindings(text: str) -> dict[str, tuple[str | None, str | None]]:
    """beh_id → (файл checked_by-цели, kind); `::селектор` отброшен.

    Дубликат грамматики task_bridge._CHECKED (фактическое имя константы
    моста) намеренный и запинован тестом согласованности
    (test_beh_binding_grammar_matches_task_bridge): гард обязан остаться
    чистым модулем без импорта task_bridge (канон design_guard), а
    расхождение грамматик ловится тестом, не ревьюером. Берётся
    ПОСЛЕДНЕЕ вхождение checked_by в блоке — как у построчного разбора
    моста, где новая строка перетирает предыдущую (minor круга 2:
    расхождение «первое против последнего» пропускало бы single-owner
    по неактуальной цели).
    """
    return {
        beh_id: (target, kind)
        for beh_id, (target, kind, _declared) in _parse_beh_binding_records(
            text
        ).items()
    }


def _transitive_deps(
    start: str, edges: dict[str, tuple[str, ...]]
) -> set[str]:
    seen: set[str] = set()
    stack = list(edges.get(start, ()))
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(edges.get(node, ()))
    return seen


def _orphan_verifies_findings(
    tasks: list[DtTask],
    bindings: dict[str, tuple[str | None, str | None]],
) -> list[str]:
    """Единственная ОСТАВШАЯСЯ non-fatal находка verifies (round 7 ревью
    PR #161, минор, контракт владельца; «группа наблюдения не выводится»
    промотирована в fatal round 13, см. `graph_findings`) — общая точка
    для `non_fatal_findings` (публичная точка входа для S4-гейта):
    элемент verifies, не являющийся checked_by-целью НИ ОДНОЙ задачи
    бандла — опечатка либо осиротевший путь; уезжал бы в tasks-спеку как
    селектор прогона, которого ни одна задача не создаёт.
    """
    findings: list[str] = []
    owned_files = {
        target for target, _kind in bindings.values() if target is not None
    }
    for t in tasks:
        for f in t.verifies:
            # Срез `::`-селектора ПЕРЕД сверкой (round 10 ревью PR #161,
            # минор): bindings/owned_files несут ГОЛЫЕ пути
            # (`_parse_beh_bindings` уже режет `::` для checked_by-целей)
            # — тот же канон, что уже применён в render_tasks_dt (union-
            # дедуп, round 9) и в closure-инварианте graph_findings ниже.
            # Без среза `file.py::test`-форма в verifies никогда не
            # совпадала бы с owned_files, даже когда файл реально в
            # бандле, — ложная находка «опечатка».
            if f.split("::", 1)[0] not in owned_files:
                findings.append(
                    f"{t.dt_id}: verifies {f}: наблюдаемая цель не "
                    "принадлежит ни одной задаче — "
                    f"{_ORPHAN_VERIFIES_TARGET_MARKER}"
                )
    return findings


def non_fatal_findings(
    behaviour_text: str, decomposition_text: str
) -> list[str]:
    """Non-fatal находки формы про `verifies` (round 7 ревью PR #161,
    минор, контракт владельца) — единственная точка их вычисления, и
    единственный потребитель — `governance.runner._step_gate`: показывает
    их оператору как `warning GC-DT-GRAPH:` в gate-findings.txt, НЕ
    останавливая прогон. `graph_findings` (fatal-агрегат S4-гейта и
    `task_bridge.deliver`) про эту находку НЕ ЗНАЕТ ВООБЩЕ — она её не
    вычисляет и не фильтрует (round 8 ревью PR #161, минор: исправлен
    комментарий модуля, ранее ошибочно обещавший обратное); общий с этой
    функцией источник — только `_orphan_verifies_findings`.
    """
    tasks, _form_findings = parse_dt_tasks(decomposition_text)
    bindings = _parse_beh_bindings(behaviour_text)
    return _orphan_verifies_findings(tasks, bindings)


def graph_findings(
    behaviour_text: str,
    decomposition_text: str,
    *,
    selector_policy: SelectorPolicy | None = None,
) -> list[str]:
    """Инварианты графа DT (§3 спеки) + findings формы парсера.

    Порядок проверок фиксирован, findings накапливаются (гейт показывает
    всё сразу, не по одной). Пустой список — граф валиден.

    Единственная ОСТАВШАЯСЯ non-fatal находка про `verifies`
    (`_orphan_verifies_findings`, round 7 ревью PR #161, минор — опечатка/
    осиротевший путь) в результат НЕ попадает вовсе — вычисляется
    ОТДЕЛЬНО, публичной `non_fatal_findings` (потребитель —
    `runner._step_gate`, показывает её как warning, не останавливая
    прогон); эта функция про неё просто не знает.

    Fatal-инвариант verifies-замыкания (round 8 ревью PR #161, major,
    контракт владельца): элемент verifies обязан ссылаться на файл, чей
    владелец (checked_by) — в ТРАНЗИТИВНОМ ЗАМЫКАНИИ depends_on
    наблюдающей задачи — тот же контракт, что уже есть у delivered_by;
    иначе verify_first-прогон законно стартовал бы раньше, чем владелец
    наблюдаемого файла его вообще создаст.

    «Группа наблюдения не выводится вовсе» — тоже FATAL (round 13 ревью
    PR #161, минор — промотирована из non-fatal): учитываются только
    исполняемые checked_by-цели и verifies; `kind: manual` не превращает
    документ в селектор. Это ТОЖДЕСТВЕННО условию, при котором
    `task_bridge.render_tasks_dt` детерминированно поднимает
    RuntimeError «нечего прогонять» — «рекомендация формы, не блокирует
    доставку» была ложью именно для этого входа; легаси-форма,
    checked_by-цель ЕСТЬ и verifies нет, — по-прежнему НЕ находка).
    Orphan verifies остаётся отдельным warning о причине, но не считается
    исполняемой целью: если других целей нет, underivable-finding управляет
    исходом и останавливает гейт (#162).
    """
    tasks, findings = parse_dt_tasks(decomposition_text)
    records = _parse_beh_binding_records(behaviour_text)
    bindings = {
        beh_id: (target, kind)
        for beh_id, (target, kind, _declared) in records.items()
    }
    ids = {t.dt_id for t in tasks}
    edges = {t.dt_id: t.depends_on for t in tasks}

    # near-miss BEH-заголовки behaviour-spec — тот же стандарт, что для
    # DT (Global Constraints): битый заголовок — находка, не молчание
    strict_beh = {m.start() for m in _BEH_HEAD_RE.finditer(behaviour_text)}
    for near in _BEH_NEAR_RE.finditer(behaviour_text):
        if near.start() not in strict_beh:
            findings.append(
                f"{near.group(1)}: заголовок behaviour-spec не соответствует "
                "машинной грамматике BEH (`#### BEH-NN[a-z]: <название>` — "
                "суффикс не более одной строчной буквы)"
            )

    # ссылки на несуществующее
    for t in tasks:
        for ref in (*t.depends_on, *t.delivered_by):
            if ref not in ids:
                findings.append(f"{t.dt_id}: ссылка на несуществующий {ref}")
        for beh in t.scenarios:
            if beh not in bindings:
                findings.append(
                    f"{t.dt_id}: сценарий {beh} отсутствует в behaviour-spec"
                )

    # порядок объявления: depends_on обязан ссылаться только на DT, уже
    # объявленные ВЫШЕ по документу (Task 9 находка 2 финального ревью) —
    # мост-транслятор переносит рёбра как есть, и forward-ссылка «уезжает»
    # в целевой репо как ссылка на задачу, которой там ещё нет
    order = {t.dt_id: idx for idx, t in enumerate(tasks)}
    for t in tasks:
        for ref in t.depends_on:
            if ref in order and order[ref] > order[t.dt_id]:
                findings.append(
                    f"{t.dt_id}: depends_on ссылается на {ref}, "
                    "объявленный ниже по документу — порядок объявления "
                    "обязан быть топологическим"
                )

    # сюръекция BEH без дублей
    coverage: dict[str, list[str]] = {}
    for t in tasks:
        for beh in t.scenarios:
            coverage.setdefault(beh, []).append(t.dt_id)
    for beh in bindings:
        owners = coverage.get(beh, [])
        if not owners:
            findings.append(f"{beh}: не покрыт ни одной DT-задачей")
        elif len(owners) > 1:
            findings.append(
                f"{beh}: покрыт дважды и более ({', '.join(owners)})"
            )

    # single-owner тест-файла — БЕЗ исключения для verifies (round 4 ревью
    # PR #161, finding 3: узкое per-task исключение round 2/3, `target in
    # t.verifies`, срабатывало РОВНО там, где задача t сама владеет
    # файлом — target здесь ВСЕГДА выведен из checked_by-цели t
    # (bindings по её же scenarios), т.е. это ВСЕГДА заявка t на
    # редактирующее владение, независимо от того, что ещё перечислено в
    # t.verifies. Владение (scenarios/checked_by) всегда старше
    # наблюдения (verifies, owner ruling DSL: «checked_by remains the
    # single source of EDITING ownership») — этот цикл смотрит только на
    # scenarios/checked_by и НИКОГДА на verifies, так что вопрос
    # исключения здесь просто не встаёт: single-owner проверяется как для
    # любых двух задач.
    file_owner: dict[str, str] = {}
    for t in tasks:
        for beh in t.scenarios:
            target, _kind = bindings.get(beh, (None, None))
            if target is None:
                continue
            prior = file_owner.get(target)
            if prior is not None and prior != t.dt_id:
                findings.append(
                    f"{target}: нарушен single-owner — checked_by-цель у "
                    f"{prior} и {t.dt_id}"
                )
            file_owner.setdefault(target, t.dt_id)

    # Негативный контроль — в файле, которым DT владеет через checked_by
    # своих сценариев (devtools#336). Контроль доказывает, что НОВЫЙ тест
    # задачи краснеет под патчем; тест в файле другого DT — чужой, и его
    # краснота ничего не говорит о работе этой задачи. Совпадение с
    # объявленной checked_by-целью — объявленная связь, а не догадка по
    # пути: сверяется ФАЙЛ селектора с файлами целей, `::` режется той же
    # грамматикой, что у `_parse_beh_bindings`. Manual-цели — документы,
    # тест в них не живёт.
    for t in tasks:
        if t.waiver is None:
            continue
        own_files = sorted({
            target for target, kind in (
                bindings.get(beh, (None, None)) for beh in t.scenarios
            )
            if target is not None and kind != "manual"
        })
        control_file = t.waiver.control_selector.split("::", 1)[0]
        if control_file not in own_files:
            findings.append(
                f"{t.dt_id}: negative_control {t.waiver.control_selector} "
                f"указывает на файл {control_file}, которым DT не владеет "
                "через checked_by своих сценариев (владеет: "
                f"{', '.join(own_files) or 'ничем исполняемым'})"
            )

    # Группа наблюдения verify-DT не выводится ВООБЩЕ — FATAL (round 13
    # ревью PR #161, минор, промотирована из non-fatal, см. докстринг
    # выше): ни одной исполняемой checked_by-цели через scenarios и ни
    # одного verifies после исключения manual-only путей — прогонять
    # нечего, render_tasks_dt детерминированно упал бы RuntimeError.
    kinds_by_file: dict[str, set[str | None]] = {}
    for target, kind in bindings.values():
        if target is not None:
            kinds_by_file.setdefault(target, set()).add(kind)
    manual_only = {
        target for target, kinds in kinds_by_file.items()
        if kinds == {"manual"}
    }
    owned_files = set(kinds_by_file)
    for t in tasks:
        if t.type != "verify":
            continue
        runnable_targets: list[str] = []
        for beh in t.scenarios:
            _target, kind, declared = records.get(beh, (None, None, None))
            if (
                declared
                and kind != "manual"
                and declared not in runnable_targets
            ):
                runnable_targets.append(declared)
        for declared in t.verifies:
            target = declared.split("::", 1)[0]
            if target in manual_only or target not in owned_files:
                continue
            if declared not in runnable_targets:
                runnable_targets.append(declared)
        if not runnable_targets:
            findings.append(
                f"{t.dt_id}: type: verify — "
                f"{_VERIFY_GROUP_UNDERIVABLE_MARKER} (нет ни "
                "исполняемых checked_by-целей, ни verifies; kind: manual "
                "не является селектором) — группу "
                "наблюдения прогонять нечем, доставка этого DT невозможна"
            )
        if selector_policy and not selector_policy.supports_file_targets:
            for declared in runnable_targets:
                if is_file_target_form(declared):
                    findings.append(
                        f"{t.dt_id}: {declared} — адаптер "
                        f"{selector_policy.name} не поддерживает file target; "
                        "объявите исполняемый селектор "
                        f"{selector_policy.node_id_hint}"
                    )

    # verify/implement-контракт delivered_by + транзитивное замыкание
    for t in tasks:
        if t.type == "verify":
            if not t.delivered_by:
                findings.append(
                    f"{t.dt_id}: type: verify без delivered_by"
                )
            else:
                closure = _transitive_deps(t.dt_id, edges)
                outside = [d for d in t.delivered_by if d not in closure]
                if outside:
                    findings.append(
                        f"{t.dt_id}: delivered_by "
                        f"({', '.join(outside)}) вне транзитивного "
                        "замыкания depends_on"
                    )
        elif t.delivered_by:
            findings.append(
                f"{t.dt_id}: delivered_by запрещён при type: implement"
            )

    # verifies обязан ссылаться на файлы, чей владелец (checked_by, тот же
    # file_owner, что и single-owner выше) — в ТРАНЗИТИВНОМ ЗАМЫКАНИИ
    # depends_on наблюдающей задачи (round 8 ревью PR #161, major, контракт
    # владельца) — тот же инвариант, что уже есть у delivered_by: verify_
    # first-прогон не имеет права начаться раньше, чем владелец
    # наблюдаемого файла его реально создаст (промпт авторинга рекомендует
    # класть в verifies именно файлы ДРУГИХ DT — ops.py, «owned by other
    # DTs» — но не требует ребра к ним, гард обязан требовать сам).
    # Файлы, не являющиеся чьей-либо checked_by-целью вовсе (опечатка/
    # осиротевший путь), здесь не смотрим — non-fatal-версия этой проверки
    # уже сделана отдельно в `non_fatal_findings`. Срез `::`-селектора
    # ПЕРЕД сверкой с file_owner (round 10 ревью PR #161, минор) — тот же
    # канон, что уже применён выше в orphan-проверке и в render_tasks_dt:
    # без него `file.py::test`-форма в verifies никогда не находила бы
    # своего владельца (file_owner несёт голые пути), и closure-инвариант
    # молча пропускался бы ДАЖЕ когда владелец реально вне замыкания.
    for t in tasks:
        for f in t.verifies:
            owner = file_owner.get(f.split("::", 1)[0])
            if owner is None or owner == t.dt_id:
                continue
            closure = _transitive_deps(t.dt_id, edges)
            if owner not in closure:
                findings.append(
                    f"{t.dt_id}: verifies {f} — владелец {owner} вне "
                    f"транзитивного замыкания depends_on {t.dt_id}"
                )

    # ацикличность (DFS с цветами)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(ids, WHITE)

    def _visit(node: str) -> bool:
        color[node] = GRAY
        for nxt in edges.get(node, ()):
            if nxt not in color:
                continue
            if color[nxt] == GRAY:
                return True
            if color[nxt] == WHITE and _visit(nxt):
                return True
        color[node] = BLACK
        return False

    if any(color[n] == WHITE and _visit(n) for n in sorted(ids)):
        findings.append("depends_on: в графе есть цикл")

    # рёбра в чужую группу — от ВСЕХ стоков этой группы
    groups: dict[str, set[str]] = {}

    def _group_key(task: DtTask) -> str:
        # solo — задача сама по себе: собственная одиночная группа,
        # не общая ветвь всех solo (major ревью плана)
        if task.parallel_group == "solo":
            return f"solo:{task.dt_id}"
        return task.parallel_group

    for t in tasks:
        groups.setdefault(_group_key(t), set()).add(t.dt_id)
    dependents: dict[str, set[str]] = {i: set() for i in ids}
    for t in tasks:
        for dep in t.depends_on:
            if dep in dependents:
                dependents[dep].add(t.dt_id)
    for t in tasks:
        foreign = {
            dep for dep in t.depends_on
            if dep in ids
        }
        by_group: dict[str, set[str]] = {}
        own_key = _group_key(t)
        # рёбра, обоснованные delivered_by, из правила стоков исключены
        # (verify точечно за проверяемым — §3 спеки, minor круга 2)
        foreign -= set(t.delivered_by)
        for dep in foreign:
            dep_group = next(
                g for g, members in groups.items() if dep in members
            )
            if dep_group != own_key:
                by_group.setdefault(dep_group, set()).add(dep)
        if len(by_group) < 2:
            # точечное ребро в одну чужую группу — не «свод» (major
            # круга 4): правило стоков действует только на задачу,
            # сводящую две и более чужих группы
            continue
        for g, deps in by_group.items():
            sinks = {
                member for member in groups[g]
                if not (dependents[member] & groups[g])
            }
            missing = sinks - set(t.depends_on)
            if missing:
                findings.append(
                    f"{t.dt_id}: зависит от группы {g}, но не от всех её "
                    f"стоков (нет: {', '.join(sorted(missing))})"
                )
    return findings


# --- Версия DT-контракта и форма `delivers` (devtools#282, спека §3b) ------
#: Единственная поддержанная версия DT-диалекта. Список закрытый: неизвестная
#: версия — ошибка, а не «наверное, совместимо».
_DT_CONTRACT_VERSIONS = ("2",)

#: Ссылка на пункт источника: `<узел>#<id>`. Номера строк и текст заголовка
#: идентификаторами НЕ считаются (спека §3b.2) — и то и другое меняется при
#: редактуре, а ссылка обязана её переживать.
_SOURCE_REF_RE = re.compile(r"^[a-z][a-z0-9-]*#[A-Za-z0-9][A-Za-z0-9._-]*$")

_DELIVERS_KEY_RE = re.compile(r"^delivers:", re.M)

#: Контрактная форма id результата поставки (спека §3b.3 — «задача
#: сохраняет ссылку на `DEL-NN`»). Проверяется ЗДЕСЬ, потому что гвард
#: объявлен единственным судьёй формы `delivers`, а мост опознаёт
#: результат в чек-листе по этой же форме. Разойдись они — гейт зеленел
#: бы, а доставка падала бы с ЛОЖНОЙ причиной «результат не доехал»,
#: хотя пункт отрендерен и на месте (находка ревью PR #290).
_DELIVERABLE_ID_RE = re.compile(r"^DEL-\d+$")

#: Закрытый словарь видов результата (спека §3b.1). Открытый превратил бы
#: машинную классификацию в свободный текст — тот же провал, от которого
#: репо закрылось `WAIVER_CLASSES`: при открытом словаре видом становилось
#: бы любое слово, которое автор счёл подходящим.
DELIVERABLE_KINDS = ("capability", "module", "document", "config")


class Deliverable(NamedTuple):
    """Один объявленный результат поставки DT (спека §3b.1).

    Три поля несут три разные роли и не заменяют друг друга: `kind`
    классифицирует, `id` обеспечивает связь, `statement` задаёт
    обязательство. `covered_by` — четвёртое, отдельное: оно объявляет, что
    результат уже закрыт существующим пунктом чек-листа (сценарием того же
    DT), и ровно поэтому мост не создаёт для него второй пункт. Совпадение
    пути или похожего текста таким объявлением НЕ является — это тот же
    прокси, от которого отказывается весь §3b.
    """

    id: str
    kind: str
    statement: str
    sources: tuple[str, ...]
    covered_by: str | None
    #: Повтор обязательства предшествующей задачи (§3b.6, контракт
    #: владельца 2026-09-21). Ссылка ведёт на ИСХОДНОЕ обязательство, а не
    #: на посредника: цепочка рассеяла бы источник, и пункт назвал бы
    #: задачу, где обязательство лишь повторено, а не исполнено.
    #: `restates` значит ПОВТОР ТОГО ЖЕ обязательства; расширение или
    #: новое ограничение — самостоятельный результат со своим id.
    #: Смысловую эквивалентность гвард НЕ судит и судить не может — это
    #: предмет ревью; здесь проверяется только структура связи.
    restates: str | None = None


def _dt_blocks(text: str) -> list[tuple[str, str]]:
    """(dt_id, тело блока) — та же нарезка, что у `parse_dt_tasks`.

    Границы блока не переопределяются: второе определение разъехалось бы с
    парсером, и проверка формы смотрела бы не на тот текст, что разбор.
    """
    matches = list(_DT_HEAD_RE.finditer(text))
    out: list[tuple[str, str]] = []
    for idx, m in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[m.end() : end]
        section = _SECTION_RE.search(block)
        if section is not None:
            block = block[: section.start()]
        out.append((m.group(1), block))
    return out


def _delivers_region(block: str) -> str | None:
    """Текст `delivers:` вместе с его отступленным продолжением.

    Регион вырезается и отдаётся YAML целиком, а не разбирается регуляркой
    по полям: запись — вложенная структура, и свой разбор здесь был бы
    вторым, более слабым YAML.
    """
    lines = block.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("delivers:"):
            continue
        region = [line]
        for nxt in lines[i + 1:]:
            if nxt.strip() and not nxt.startswith((" ", "\t")):
                break
            region.append(nxt)
        return "\n".join(region)
    return None


#: Id узла в заголовке ЛЮБОГО DSL бандла. Две живые формы разделителя, и
#: обе обязаны разбираться одним экстрактором: `#### AC-07: <текст>`
#: (двоеточие, acceptance/behaviour/requirements) и `#### Q-03 ·
#: owner_role: …` (интерпункт, design). Экстрактор, знающий одну форму,
#: отдал бы для другого узла ПУСТОЙ индекс — и каждая ссылка на него
#: стала бы «пункт не найден», то есть отказ по несуществующей причине.
#:
#: Интерпункт в границе НЕ перечислен намеренно: в живой форме design он
#: отделён от id пробелом, который `\s` уже покрывает. Перечисление его
#: отдельно выглядело бы проверкой, которой нет — мутация, снявшая эту
#: ветку, выжила бы, потому что предикат от неё не зависит.
_NODE_ID_RE = re.compile(r"^####\s+([A-Z][A-Z0-9]*-\d+[a-z]?)(?=[\s:]|$)", re.M)


def node_id_counts(text: str) -> dict[str, int]:
    """Id пунктов одного узла бандла → сколько РАЗ каждый определён.

    Сырьё для индекса `sources`, и именно в этой форме: кратность обязана
    дожить до проверки ссылки. Схлопни её во множество раньше — и ссылка
    на дважды определённый id разрешится молча, выбрав одно из двух
    определений без права на такой выбор.

    Заголовок `#### <ID>` — место ОПРЕДЕЛЕНИЯ пункта; упоминания того же
    id в прозе заголовками не являются и здесь не считаются.

    Чистая функция над текстом: файлы читает вызывающий. Гвард остаётся
    без файлового ввода-вывода намеренно — иначе его нельзя было бы
    звать из моста и гейта одним и тем же способом.
    """
    counts: dict[str, int] = {}
    for found in _NODE_ID_RE.findall(text):
        counts[found] = counts.get(found, 0) + 1
    return counts



def _parse_delivers(
    dt_id: str, block: str
) -> tuple[list[str], list[Deliverable]]:
    """Находки формы `delivers` одного DT и разобранные записи.

    ОДИН разбор на обоих потребителей — гвард и мост. Второй разбор того
    же текста в мосте был бы вторым вычислителем предиката: он разошёлся
    бы с гвардом молча, и разойтись мог бы как раз на том, что гвард
    признал валидным.

    Записи возвращаются СПИСКОМ с сохранением порядка и повторов id, а не
    множеством: множество схлопывало дубль до сверки, и внутри-DT повтор
    не находился никогда — собственное сообщение проверки для него нельзя
    было даже напечатать (блокер ревью PR #289).
    """
    keys = len(_DELIVERS_KEY_RE.findall(block))
    if keys > 1:
        # Число ключей считается отдельно от числа разборов — канон репо
        # для этого класса (`tdd_waiver`). Регион берётся по первому
        # совпадению, поэтому строка-заглушка выше настоящего блока молча
        # съедала бы его.
        return ([f"{dt_id}: ключ delivers объявлен дважды ({keys}) — "
                 f"разбирается только первый, объявленное ниже невидимо"], [])
    region = _delivers_region(block)
    if region is None:
        return ([f"{dt_id}: поле delivers отсутствует (dt_contract_version: 2 "
                 f"требует его у каждого DT; `delivers: []` — законное "
                 f"утверждение «объявленных результатов нет»)"], [])
    try:
        parsed = yaml.safe_load(region)
    except yaml.YAMLError as exc:
        return ([f"{dt_id}: поле delivers не разобрано как YAML: {exc}"], [])
    items = (parsed or {}).get("delivers")
    if items is None:
        # Ключ есть, значения нет — находка формы, а не «поля нет»: тот же
        # приём, что у `verifies` (round 13 ревью PR #161).
        return ([f"{dt_id}: поле delivers объявлено, но пусто — ожидается "
                 f"список записей либо явный `delivers: []`"], [])
    if items == []:
        return ([], [])
    if not isinstance(items, list):
        return ([f"{dt_id}: delivers обязан быть списком записей"], [])
    findings: list[str] = []
    records: list[Deliverable] = []
    # Сценарии читаются из ТОГО ЖЕ блока: связь `covered_by` локальна DT,
    # и проверять её по соседям значило бы принять пункт, который в этой
    # задаче не окажется.
    own_scenarios = _list_field(block, "scenarios") or ()
    for pos, item in enumerate(items, start=1):
        where = f"{dt_id}: delivers[{pos}]"
        if not isinstance(item, dict):
            findings.append(f"{where}: ожидается запись с полями id/kind/"
                            f"statement/sources")
            continue
        for field in ("id", "kind", "statement", "sources"):
            if not item.get(field):
                findings.append(f"{where}: поле {field} отсутствует или пусто")
        del_id = item.get("id")
        if isinstance(del_id, str) and del_id and not _DELIVERABLE_ID_RE.match(
            del_id
        ):
            findings.append(
                f"{where}: id {del_id!r} не соответствует контрактной форме "
                f"`DEL-NN`; по ней результат опознаётся в чек-листе, и id "
                f"иной формы дал бы отказ доставки с ложной причиной"
            )
        kind = item.get("kind")
        if isinstance(kind, str) and kind and kind not in DELIVERABLE_KINDS:
            findings.append(
                f"{where}: неизвестный kind {kind!r} — словарь закрыт "
                f"({', '.join(DELIVERABLE_KINDS)}); при открытом видом "
                f"стало бы любое слово, которое автор счёл подходящим"
            )
        statement = item.get("statement")
        if isinstance(statement, str) and statement and not statement.strip():
            findings.append(f"{where}: statement пуст")
        elif isinstance(statement, str) and "\n" in statement.strip():
            # Пункт чек-листа — ОДНА физическая строка по построению: и
            # spec-runner разбирает его построчно, и перенос §I11 опознаёт
            # носитель состояния по строке. Многострочный statement рвал
            # пункт на две, и доставка падала с ложной причиной
            # «результат встречается 0 раз» — пункт был отрендерен целиком
            # (находка ревью PR #295). Судья формы — гвард, и отказ обязан
            # приходить от него: на своей стадии и с верной причиной.
            findings.append(
                f"{where}: statement занимает несколько строк — "
                f"обязательство доезжает до исполнителя ОДНОЙ строкой "
                f"чек-листа, и многострочный текст её разорвал бы"
            )
        sources = item.get("sources")
        refs: list[str] = []
        if isinstance(sources, list):
            for ref in sources:
                if not isinstance(ref, str) or not _SOURCE_REF_RE.match(ref):
                    findings.append(
                        f"{where}: sources — ожидается `<узел>#<id>` "
                        f"(например `acceptance#AC-07`), получено {ref!r}; "
                        f"номер строки и текст заголовка идентификаторами "
                        f"не считаются"
                    )
                else:
                    refs.append(ref)
        elif sources is not None:
            findings.append(f"{where}: sources обязан быть списком ссылок")
        covered_by = item.get("covered_by")
        if covered_by is not None and not (
            isinstance(covered_by, str) and covered_by.strip()
        ):
            findings.append(
                f"{where}: covered_by — ожидается id сценария этого DT "
                f"(например `BEH-33`), получено {covered_by!r}"
            )
            covered_by = None
        elif isinstance(covered_by, str) and covered_by not in own_scenarios:
            # `covered_by` работает тем, что ОТМЕНЯЕТ создание отдельного
            # пункта. Назови он чужой сценарий — пункта с такой связью в
            # задаче не появится вовсе, и результат исчезнет молча: тот
            # самый дефект, против которого заведён §3b, приобретённый
            # через сам механизм защиты от него. Поэтому не предупреждение.
            findings.append(
                f"{where}: covered_by {covered_by!r} не входит в scenarios "
                f"этого DT ({', '.join(own_scenarios) or 'пусто'}) — "
                f"связь объявлена с пунктом, которого в задаче не будет"
            )
            covered_by = None
        restates = item.get("restates")
        if restates is not None and not (
            isinstance(restates, str) and _DELIVERABLE_ID_RE.match(restates)
        ):
            findings.append(
                f"{where}: restates — ожидается id результата в форме "
                f"`DEL-NN`, получено {restates!r}"
            )
            restates = None
        if isinstance(del_id, str) and del_id:
            records.append(
                Deliverable(
                    id=del_id,
                    kind=kind if isinstance(kind, str) else "",
                    statement=statement if isinstance(statement, str) else "",
                    sources=tuple(refs),
                    covered_by=covered_by,
                    restates=restates,
                )
            )
    return (findings, records)


def _sources_findings(
    dt_id: str,
    records: list[Deliverable],
    node_index: Mapping[str, Mapping[str, int]],
) -> list[str]:
    """Разрешение `sources` против индекса узлов бандла (спека §3b.2).

    Гвард проверяет РАЗРЕШИМОСТЬ ссылки; обоснованность — предмет ревью, и
    притворяться, что структурная проверка её доказывает, он не вправе.

    Три причины неразрешимости, и каждая — своё сообщение, потому что
    каждая чинится в своём месте:

    * узла нет в бандле — чинится составом бандла/профилем;
    * пункта нет в узле — чинится id в ссылке (обычно опечатка);
    * пункт определён больше одного раза — чинится САМИМ УЗЛОМ: пока
      определений два, ссылка указывает на оба сразу, и выбрать за автора
      одно из них гвард не вправе. Молчаливое разрешение здесь было бы
      худшим исходом: ревьюер сверялся бы с источником, которого автор не
      имел в виду.
    """
    findings: list[str] = []
    for record in records:
        for ref in record.sources:
            node, _, item = ref.partition("#")
            if node not in node_index:
                findings.append(
                    f"{dt_id}: delivers {record.id} ссылается на {ref} — "
                    f"узел {node!r} в бандле отсутствует (известны: "
                    f"{', '.join(sorted(node_index)) or 'ни одного'})"
                )
                continue
            count = node_index[node].get(item, 0)
            if count == 0:
                findings.append(
                    f"{dt_id}: delivers {record.id} ссылается на {ref} — "
                    f"пункт {item!r} в узле {node!r} не найден; номер "
                    f"строки и текст заголовка идентификаторами не считаются"
                )
            elif count > 1:
                findings.append(
                    f"{dt_id}: delivers {record.id} ссылается на {ref} — "
                    f"пункт {item!r} определён в узле {node!r} "
                    f"{count} раза: ссылка неоднозначна, определение "
                    f"должно быть ровно одно"
                )
    return findings


def _restates_findings(
    owner_of: dict[str, str],
    records_of: dict[str, list[Deliverable]],
    edges: dict[str, tuple[str, ...]],
) -> list[str]:
    """Кросс-DT инварианты повтора обязательства (§3b.6).

    Четыре правила, и каждое закрывает свой способ сделать пометку ложной:

    1. цель существует — иначе пункт отошлёт исполнителя к обязательству,
       которого в бандле нет;
    2. цель принадлежит ДРУГОМУ DT — самоссылка дала бы «проверить, что
       сделано в этой же задаче», то есть проверку без исполнителя;
    3. DT-владелец лежит в транзитивном замыкании `depends_on` — тот же
       инвариант, что у `delivered_by` и `verifies`. Без ребра «уже
       сделано» НЕ гарантировано: задачи могут идти параллельно, и
       пометка станет ложью ровно в тот момент, когда на неё положатся;
    4. цепочек нет — ссылка ведёт на ИСХОДНОЕ обязательство. Цепочка
       рассеивает источник: пункт назвал бы задачу-посредника, где
       обязательство лишь повторено, а не исполнено.

    Чего здесь нет и быть не может: суждения, что два `statement`
    действительно об одном и том же. Это смысл, его судит ревью; структурный
    гвард, притворившийся его судьёй, лишь спрятал бы вопрос.
    """
    findings: list[str] = []
    for dt_id, records in records_of.items():
        for record in records:
            target = record.restates
            if target is None:
                continue
            where = f"{dt_id}: delivers {record.id} restates {target}"
            if target not in owner_of:
                findings.append(
                    f"{where} — такого результата в бандле нет"
                )
                continue
            owner = owner_of[target]
            if owner == dt_id:
                findings.append(
                    f"{where} — это результат самого {dt_id}: повтор "
                    f"ссылается на ПРЕДШЕСТВУЮЩУЮ задачу, а ссылка на себя "
                    f"дала бы проверку без исполнителя"
                )
                continue
            if owner not in _transitive_deps(dt_id, edges):
                findings.append(
                    f"{where} — {owner} не лежит в транзитивном замыкании "
                    f"depends_on {dt_id}: без ребра «уже сделано» не "
                    f"гарантировано, задачи могут идти параллельно"
                )
            if any(r.id == target and r.restates for r in records_of[owner]):
                findings.append(
                    f"{where} — цепочка повторов: {target} сам объявлен "
                    f"повтором. Ссылка обязана вести на ИСХОДНОЕ "
                    f"обязательство, иначе пункт назовёт посредника"
                )
    return findings


def dt_contract_findings(
    decomposition_text: str,
    *,
    node_index: Mapping[str, Mapping[str, int]],
    allow_legacy_dt: bool = False,
) -> tuple[list[str], list[str]]:
    """Версия DT-диалекта и форма `delivers` → (ошибки, предупреждения).

    Одна функция на обе половины намеренно: версия порождает и фатальную
    находку, и предупреждение, и два входа с одним флагом разъехались бы.

    Таблица (решение владельца 2026-09-21, спека §3b.4):

    | версии нет, совместимость выключена | ошибка |
    | версии нет, совместимость включена  | легаси + явная диагностика |
    | версия 2                            | полная проверка, флаг не влияет |
    | версия неизвестна/не разобрана      | ошибка, совместимость не маскирует |

    ОТСУТСТВИЕ ВЕРСИИ САМО ПО СЕБЕ РЕЖИМ НЕ ВКЛЮЧАЕТ: иначе новый документ
    с забытым полем молча обошёл бы контракт, то есть барьер отключался бы
    ровно тем, от чего защищает. Режим включает оператор параметром.

    `node_index` (узел → id его пунктов с КРАТНОСТЬЮ определения,
    стройте через `node_id_counts`) —
    параметр БЕЗ значения по умолчанию, и это не придирка к сигнатуре.
    Дефолт `None` со смыслом «тогда не проверяем» превратил бы забывчивость
    вызывающего в тихое отключение проверки — класс отказа, который в этом
    репо уже случался (45 тестов шли мимо пина, который считались
    покрывающими). Пустой индекс `{}` — законный вход со ЗНАЧЕНИЕМ «узлов
    нет», и каждая ссылка на нём честно не разрешается.

    Файлы читает вызывающий: гвард остаётся чистой функцией над строками,
    иначе его нельзя звать из гейта и моста одним способом.
    """
    try:
        meta, body = split_frontmatter(decomposition_text)
    except ValueError as exc:
        return ([f"decomposition: frontmatter не разобран: {exc}"], [])
    if "dt_contract_version" not in meta:
        if allow_legacy_dt:
            return ([], [
                "decomposition: dt_contract_version не объявлена — старый "
                "формат по явному разрешению оператора; ГАРАНТИЯ ПЕРЕНОСА "
                "deliverables ОТСУТСТВУЕТ"
            ])
        return ([
            "decomposition: требуется версия контракта "
            "(`dt_contract_version` во frontmatter); отсутствие поля режим "
            "совместимости НЕ включает — его включает оператор"
        ], [])
    raw = meta["dt_contract_version"]
    version = "" if raw is None else str(raw)
    if version not in _DT_CONTRACT_VERSIONS:
        return ([
            f"decomposition: неизвестная dt_contract_version {version!r} "
            f"(поддержана: {', '.join(_DT_CONTRACT_VERSIONS)}); режим "
            f"совместимости неизвестную версию не маскирует"
        ], [])
    errors: list[str] = []
    seen: dict[str, str] = {}
    records_of: dict[str, list[Deliverable]] = {}
    edges: dict[str, tuple[str, ...]] = {}
    for dt_id, block in _dt_blocks(body):
        block_errors, records = _parse_delivers(dt_id, block)
        errors.extend(block_errors)
        errors.extend(_sources_findings(dt_id, records, node_index))
        records_of[dt_id] = records
        edges[dt_id] = _list_field(block, "depends_on") or ()
        for del_id in (r.id for r in records):
            if del_id in seen:
                errors.append(
                    f"{dt_id}: delivers id {del_id} уже объявлен в "
                    f"{seen[del_id]} — id обеспечивает связь, дубль сделал "
                    f"бы её неоднозначной"
                )
            else:
                seen[del_id] = dt_id
    errors.extend(_restates_findings(seen, records_of, edges))
    return (errors, [])
