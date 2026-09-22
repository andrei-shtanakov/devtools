"""Состав активного DAG бандла — одно определение на весь governance.

Переехало из `task_bridge` целиком и без изменения тел (переименованы
только имена: модуль публичный, подчёркиваний наружу не носим). Причина та
же, что у `frontmatter`: механика одобрения узла (§I12) обязана спрашивать
состав активного DAG и уровни узлов в нём, а импортировать ради этого мост
нельзя — мост сам УЖЕ потребитель гейта одобрения (катовер PR #191), и
цикл был бы настоящим.

Мост продолжает звать те же функции под прежними (приватными) именами через
алиасы импорта, поэтому ни один его вызов и ни один его тест о переезде не
знают.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# DAG бандла в порядке штампа (топологический): каждый узел перечисляет
# node-id своих upstream'ов; штамп идёт по порядку тюпла, и пин(ы) узла
# пересчитываются ПОСЛЕ штампа ВСЕХ его upstream-файлов (иначе пин
# протухает в момент записи). design и acceptance — узлы с ДВУМЯ
# upstream-пинами (design — Task 5 плана design-узла; acceptance — Task 7
# плана acceptance-node). decomposition — терминальный узел, пинует ОБА
# upstream (design, acceptance — Task 7 плана acceptance-node).
BUNDLE_DAG: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("00-charter.md", ()),
    ("10-requirements.md", ("charter",)),
    ("15-behaviour-spec.md", ("requirements",)),
    ("20-design.md", ("requirements", "behaviour-spec")),
    ("25-acceptance.md", ("requirements", "behaviour-spec")),
    ("30-decomposition.md", ("design", "acceptance")),
)

# Вариант ДО раскатки acceptance-узла (Task 7 плана acceptance-node,
# `--legacy-bundle=5`) — ЛИТЕРАЛЬНЫЙ отдельный кортеж, не срез нового
# `BUNDLE_DAG`: decomposition этой эры пинует только design (acceptance
# ещё не существовал), состав каталога — ровно 00/10/15/20/30.
BUNDLE_DAG_LEGACY5: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("00-charter.md", ()),
    ("10-requirements.md", ("charter",)),
    ("15-behaviour-spec.md", ("requirements",)),
    ("20-design.md", ("requirements", "behaviour-spec")),
    ("30-decomposition.md", ("design",)),
)


def node_id(filename: str) -> str:
    """Имя файла бандла → node-id (числовой префикс и `.md` отрезаны)."""
    return filename.rsplit(".", 1)[0].split("-", 1)[1]


def node_filenames(
    dag: tuple[tuple[str, tuple[str, ...]], ...] = BUNDLE_DAG,
) -> tuple[tuple[str, str], ...]:
    """(node-id, имя файла) активного состава в топологическом порядке.

    Единственный способ спросить «какие узлы вообще есть в бандле и в
    каких файлах они лежат». Заведено под индекс `sources` S4-гейта
    (`runner._step_gate`): прежде тот перечислял узлы собственным
    кортежем, charter в кортеж не попал, и КАЖДАЯ ссылка вида
    `charter#CON-01` отвергалась как «узла нет в бандле» — при том, что
    `00-charter.md` лежал в бандле рядом с остальными (боевой прогон
    review-pr-unreachable-base-coverage-20260921, S4).

    Состав берётся из `dag`, а не из литерала здесь: у легаси-бандлов и
    у чужого профиля состав свой, и подставляется он вызовом `dag_for`.
    Узел, которого в DAG нет, адресуемым не становится молча — нулевой
    узел discovery (`00-discovery/brief.md`) в `BUNDLE_DAG` не входит и
    в индекс не попадает.
    """
    return tuple((node_id(fname), fname) for fname, _ in dag)


def levels(
    dag: tuple[tuple[str, tuple[str, ...]], ...],
) -> dict[str, int]:
    """Уровень узла: 0 у корня, иначе `1 + max` по прямым upstream (§I12 `K`).

    Единственное определение уровня в governance: `approve_node._levels`
    делегирует сюда, волновой прогон (`sequential-node-approval` S1) считает
    номер волны как `level + 1`. Обход идёт по `dag`, который топологичен по
    построению, поэтому upstream всегда уже посчитан.
    """
    out: dict[str, int] = {}
    for fname, ups in dag:
        out[node_id(fname)] = 1 + max((out[u] for u in ups), default=-1)
    return out


def wave_count(dag: tuple[tuple[str, tuple[str, ...]], ...]) -> int:
    """Число волн DAG — число уровней (волны 1-based: `wave = level + 1`)."""
    return max(levels(dag).values(), default=-1) + 1


def dag_upto(
    dag: tuple[tuple[str, tuple[str, ...]], ...], level: int
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Узлы уровней ≤ `level` — префикс DAG по уровням, порядок сохранён.

    `level < 0` — пустой префикс (бандла ещё нет)."""
    lv = levels(dag)
    return tuple(
        (fname, ups) for fname, ups in dag if lv[node_id(fname)] <= level
    )


# Якорный узел моста — терминальный узел DAG (decomposition). Выводится из
# BUNDLE_DAG, а не хардкодится второй раз (Task 6): смена терминального
# узла бандла — правка одной строки DAG, не поиск по файлу.
ANCHOR_FILENAME = BUNDLE_DAG[-1][0]
ANCHOR_NODE_ID = node_id(ANCHOR_FILENAME)


def dag_for(
    legacy_bundle: int | None,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Активный DAG по значению `--legacy-bundle` (Task 7 плана
    acceptance-node): `None` — полный DAG (текущий якорь — decomposition,
    два upstream-пина: design и acceptance); `3`/`4` — точный префикс
    `BUNDLE_DAG` (легаси-бандлы, авторенные до раскатки
    design/decomposition-узла: три узла — charter→requirements→
    behaviour-spec, четыре — плюс design); `5` — `BUNDLE_DAG_LEGACY5`
    (отдельный литеральный кортеж, НЕ срез: бандлы, авторенные до раскатки
    acceptance-узла — decomposition этой эры пинует только design). Иное
    значение — ValueError, argparse (`choices=(3, 4, 5)`) отсекает его на
    CLI-границе раньше, но функция вызывается и напрямую (тесты,
    `stamp_bundle_approved`/`conform_approved`/`deliver`/`deliver_conform`).
    """
    if legacy_bundle is None:
        return BUNDLE_DAG
    if legacy_bundle == 5:
        return BUNDLE_DAG_LEGACY5
    if legacy_bundle in (3, 4):
        return BUNDLE_DAG[:legacy_bundle]
    raise ValueError(
        f"legacy_bundle: ожидается 3, 4 или 5, получено {legacy_bundle!r}"
    )


def check_bundle_composition(
    target_dir: str,
    bundle_dir: str,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    *,
    mode: str = "full",
) -> int:
    """Заявленный состав бандла (`dag`) обязан совпасть с фактическим РОВНО.

    Возвращает максимальный уровень фактического состава. `mode="full"` —
    прежнее поведение (вызовы моста возврат игнорируют). `mode="waves"`
    (спека sequential-node-approval S4а): отсутствующий каталог — пустой
    состав (`-1`), а фактический состав обязан быть ПРЕФИКСОМ DAG по
    уровням — `dag_upto(dag, m)` для некоторого `m`; «дыра» в уровнях
    (есть узел волны 3, нет узла волны 2) — отказ, волновой прогон не
    продолжается.

    «По самому длинному существующему» запрещён (спека §4): красил бы
    недоавторенный бандл зелёным, если в каталоге случайно лежит лишний
    (или недостаёт) узел DAG. Сравнение — по множеству имён файлов, не по
    префиксу и не по count — лишний ИЛИ недостающий узел одинаково
    отказывает.

    Полностью отсутствующий каталог — отдельный конфигурационный исход, не
    «пустой состав»: оператору нужно исправить `bundle_dir`/доставку в base,
    а не подбирать legacy-режим (devtools#168).
    """
    bundle = Path(target_dir) / bundle_dir
    if not bundle.is_dir():
        if mode == "waves":
            return -1
        raise RuntimeError(
            f"каталога бандла {bundle_dir!r} нет в {target_dir!r}: "
            "проверьте bundle_dir в run.json и что бандл вмержен "
            "в base; подбор --legacy-bundle отсутствующий "
            "каталог не исправит"
        )
    declared = {fname for fname, _ in dag}
    known = {fname for fname, _ in BUNDLE_DAG}
    actual = {
        p.name for p in bundle.glob("*.md")
        if p.name in known
    }
    if mode == "waves":
        return _level_prefix(dag, actual)
    if actual != declared:
        raise RuntimeError(
            f"состав бандла {sorted(actual)} не совпадает с заявленным "
            f"{sorted(declared)}: проверьте bundle_dir и что весь бандл "
            "вмержен в base; если путь верен, доавторьте "
            "недостающие узлы либо передайте "
            "--legacy-bundle=3|4|5 с ТОЧНЫМ фактическим составом"
        )
    return max(levels(dag).values(), default=-1)


def _level_prefix(
    dag: tuple[tuple[str, tuple[str, ...]], ...], actual: set[str]
) -> int:
    """Верхний уровень `actual`, если это префикс `dag` по уровням; иначе отказ."""
    lv = levels(dag)
    top = max((lv[node_id(f)] for f in actual), default=-1)
    prefix = {fname for fname, _ in dag_upto(dag, top)}
    if actual != prefix:
        raise RuntimeError(
            f"состав бандла {sorted(actual)} не является префиксом DAG по "
            f"уровням (ожидалось {sorted(prefix)} для уровней ≤ {top}): "
            "дыра в уровнях — волновой прогон не продолжается"
        )
    return top


#: Версия схемы отпечатка состава. Отвечает за КАНОНИЗАЦИЮ: смена правил
#: (порядок, разделитель) не должна выглядеть сменой состава.
COMPOSITION_SCHEME = "v1"


def composition(dag: tuple[tuple[str, tuple[str, ...]], ...]) -> tuple[str, ...]:
    """Состав активного DAG — его node-id в топологическом порядке.

    ОБЩИЙ ВЫЧИСЛИТЕЛЬ (§I12): состав спрашивают двое — §I8 («тот же ли
    активный DAG, что у предыдущей доставки») и §I12 («тот же ли, что
    записан в intent волны»). Делится именно ВЫЧИСЛИТЕЛЬ, а не суждение:
    каждый сравнивает свою пару величин, но «что сейчас в составе»
    отвечает одна процедура. Второго способа получить этот ответ не
    заводится по тому же правилу, по которому не заводится второе
    определение одобренности.
    """
    return tuple(node_id(fname) for fname, _ in dag)


def composition_fingerprint(nodes: tuple[str, ...]) -> str:
    """Отпечаток состава: признак РАВЕНСТВА для записи и аудита.

    Он отвечает ровно на один вопрос — тот же это состав или другой, — и
    не обещает большего: доказательством неподменности он не является, как
    и отпечаток политики авторизации. Величина обязана обещать ровно то,
    что умеет.

    Порядок канонизируется сортировкой: состав — это МНОЖЕСТВО узлов, и
    перестановка топологического порядка при том же наборе сменой состава
    не является.
    """
    payload = ",".join(sorted(nodes)).encode("utf-8")
    return f"{COMPOSITION_SCHEME}:{hashlib.sha1(payload).hexdigest()}"
