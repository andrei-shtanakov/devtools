"""Гард decomposition-узла: DT-грамматика и инварианты графа (спека
2026-09-05-decomposition-node §3). Чистые функции над строками — без
git/ФС/steward; переиспользуются S4-гейтом (runner) и мостом
(task_bridge). Канон модуля — governance/design_guard.py."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

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
# `-` и непустое содержимое сразу после него; список завершается на первой
# строке, которая НЕ является ни `- ` элементом (в любой форме), ни
# пустой строкой (round 13 minor, сохранено).
_LIST_ITEM_RE = re.compile(r"^[ \t]*-[ \t]+(\S.*?)\s*$")


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
                continue
            break
        items.append(item.group(1))
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
    multi-file DT-14): список файлов, за которыми присматривает
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


def _waiver_field(
    block: str, dt_id: str, dt_type: str, depends_on: tuple[str, ...],
) -> tuple[DtWaiver | None, list[str]]:
    """Объявление TDD-waiver'а либо находки — fail-closed в обе стороны.

    Проекция (спека decomposition-node §3a) делает `Mode: standard` НЕ
    молчаливым: рычаг, снимающий RED у implement-задачи, в spec-runner
    ровно один, и вопрос лишь в том, объявлена ли причина durable и
    адресуемо. Поэтому здесь не «разрешить побольше», а отказать всюду,
    где объявление не доказано.

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
    matches = _WAIVER_RE.findall(block)
    # Сверяется ЧИСЛО КЛЮЧЕЙ с числом разобранных, а не наличие
    # разобранных (минор ревью #197). `findall` видит только удавшийся
    # разбор, поэтому «ключ есть, форма не разобрана» было достижимо лишь
    # когда валидных строк нет ни одной: битая строка РЯДОМ с валидной
    # уходила молча, и какое объявление подействует, решала позиция.
    # Проглатывалось при этом ровно то состояние, которое контракт
    # объявляет недостижимым, — «класс без санкции».
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
    if findings:
        return None, findings
    return DtWaiver(node_class=node_class, sanction=sanction), findings


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
                "ожидается блочный список `  - <путь>` (с отступом) либо "
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
        waiver, waiver_findings = _waiver_field(block, dt_id, dt_type,
                                                depends_on or ())
        findings += waiver_findings
        tasks.append(DtTask(
            dt_id=dt_id, title=m.group(2), type=dt_type,
            owner=m.group(4), scenarios=scenarios or (),
            depends_on=depends_on or (), delivered_by=delivered_by,
            parallel_group=group_m.group(1) if group_m else "",
            verifies=verifies, waiver=waiver,
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
    heads = list(_BEH_HEAD_RE.finditer(text))
    result: dict[str, tuple[str | None, str | None]] = {}
    for idx, m in enumerate(heads):
        end = heads[idx + 1].start() if idx + 1 < len(heads) else len(text)
        block = text[m.start() : end]
        checked = None
        for checked in _BEH_CHECKED_RE.finditer(block):
            pass  # последнее вхождение — как у моста
        if checked is None:
            result[m.group(1)] = (None, None)
        else:
            target = checked.group(2).split("::", 1)[0]
            result[m.group(1)] = (target, checked.group(1))
    return result


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


def graph_findings(behaviour_text: str, decomposition_text: str) -> list[str]:
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
    PR #161, минор — промотирована из non-fatal: условие `verify-DT без
    checked_by-цели в scenarios И без verifies` ТОЖДЕСТВЕННО тому, при
    котором `task_bridge.render_tasks_dt` детерминированно поднимает
    RuntimeError «нечего прогонять» — «рекомендация формы, не блокирует
    доставку» была ложью именно для этого входа; легаси-форма,
    checked_by-цель ЕСТЬ и verifies нет, — по-прежнему НЕ находка).
    """
    tasks, findings = parse_dt_tasks(decomposition_text)
    bindings = _parse_beh_bindings(behaviour_text)
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

    # Группа наблюдения verify-DT не выводится ВООБЩЕ — FATAL (round 13
    # ревью PR #161, минор, промотирована из non-fatal, см. докстринг
    # выше): ни одной checked_by-цели через scenarios И verifies пуст —
    # прогонять нечего, render_tasks_dt детерминированно упал бы
    # RuntimeError. Легаси-форма (checked_by-цель ЕСТЬ, verifies нет) —
    # НЕ находка.
    for t in tasks:
        if t.type != "verify":
            continue
        has_own_target = any(
            bindings.get(beh, (None, None))[0] is not None
            for beh in t.scenarios
        )
        if not has_own_target and not t.verifies:
            findings.append(
                f"{t.dt_id}: type: verify — "
                f"{_VERIFY_GROUP_UNDERIVABLE_MARKER} (нет ни "
                "checked_by-целей в scenarios, ни verifies) — группу "
                "наблюдения прогонять нечем, доставка этого DT невозможна"
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
