"""Проверка узла личного пути и инварианты декомпозиции (спека §4.4).

`check_node` проводит одно ребро edge-check (`nodes.EDGES`) и кладёт запись в
раскладку, которую читает `lite.edge_journal` (`w{wave}/{kind}--{edge}.json`,
`node` = вид узла). `decomposition_problems` — детерминированные инварианты
декомпозиции: граф DT/BEH и покрытие каждого критерия брифа.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from governance.decomposition_guard import graph_findings
from governance.edge_check.author_rules import DEFAULT_CONTRACTS
from governance.edge_check.check import run_check
from governance.edge_check.coordinator import DEFAULT_MODEL
from lite import edge_journal as ej
from lite.personal.brief import Brief
from lite.personal.nodes import EDGES, FILENAMES
from lite.personal.state import PersonalRun, run_dir

#: Волна записи: дизайн проверяется первой, декомпозиция второй.
WAVES = {"personal-design": 1, "personal-decomposition": 2}

#: Метки читаются в обоих языках: русская и английская пара.
_CRITERIA = re.compile(r"\*\*(?:Критерии|Criteria):\*\*\s*(.+)$", re.MULTILINE)
_REF = re.compile(r"FR-\d+/\d+")
_CHECKS = re.compile(
    r"^## (?:Проверки|Checks)\s*$(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL
)
#: Каталог контрактов edge-check по языку прогона (`v1-en` — только рёбра
#: личного пути).
_CONTRACTS = {"ru": DEFAULT_CONTRACTS, "en": DEFAULT_CONTRACTS.parent / "v1-en"}
_UNCOVERED = {
    "ru": "критерий {ref} не покрыт ни одной проверкой BEH",
    "en": "criterion {ref} is not covered by any BEH check",
}
_UNMET_NO_REASON = {"ru": "без причины", "en": "no reason"}


def contracts_dir(lang: str) -> Path:
    """Каталог правил edge-check личного пути для языка прогона."""
    return _CONTRACTS[lang]


@dataclass(frozen=True)
class NodeCheck:
    """Итог проверки узла; `failure` непуст при `ERROR`/сбое прибора.

    `unmet` — непройденные критерии записи (`criteria[]`) строками
    «id (status): reason»: причина FAIL, у которого нет блокирующих находок.
    """

    verdict: str
    findings: list[ej.Finding]
    failure: str
    unmet: tuple[str, ...] = ()


def check_node(
    run: PersonalRun, kind: str, *, check: Callable = run_check
) -> NodeCheck:
    """Провести ребро узла `kind` и записать результат для журнала.

    Каталог правил и язык запроса — по `run.language`."""
    edge_id, bases = EDGES[kind]
    wave = WAVES[kind]
    bundle = Path(run.target_dir) / run.bundle_dir
    record_path = (
        run_dir(run.run_id) / "edge-check" / f"w{wave}" / f"{kind}--{edge_id}.json"
    )
    record_path.unlink(missing_ok=True)  # сбой прибора не оставляет старых находок
    try:
        result = check(
            edge_id,
            bundle,
            [bundle / FILENAMES[kind]],
            [(role, bundle / name) for role, name in bases],
            contracts_dir=contracts_dir(run.language),
            model=DEFAULT_MODEL,
            lang=run.language,
        )
    except Exception as exc:  # noqa: BLE001 - прибор не роняет прогон
        return NodeCheck("ERROR", [], f"{type(exc).__name__}: {exc}")
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record = {**result, "node": kind, "wave": wave}
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    verdict = str(result.get("verdict"))
    if verdict == "ERROR":
        reason = f"{result.get('error_code', '')}: {result.get('reason', '')}"
        return NodeCheck(verdict, [], reason.strip(": ") or "ERROR")
    findings = ej.blocking_findings(run_dir(run.run_id), wave)
    unmet = _unmet(result.get("criteria"), run.language)
    return NodeCheck(verdict, findings, "", unmet)


def _unmet(criteria: object, lang: str) -> tuple[str, ...]:
    """Критерии записи со статусом не `pass` — «id (status): reason»; нет
    причины — пометка на языке прогона `lang`."""
    if not isinstance(criteria, list):
        return ()
    none = _UNMET_NO_REASON[lang]
    return tuple(
        f"{c.get('id')} ({c.get('status')}): {c.get('reason') or none}"
        for c in criteria
        if isinstance(c, dict) and c.get("status") != "pass"
    )


def split_decomposition(text: str) -> tuple[str, str]:
    """(раздел `## Проверки`, весь текст); раздела нет — пустая строка."""
    m = _CHECKS.search(text)
    return (m.group(1) if m else ""), text


def _cited(checks: str) -> set[str]:
    """Все ссылки `FR-NN/k` строк `**Критерии:**` — при любом разделителе
    («,», «;», « и », пробел, обратные кавычки)."""
    return {ref for m in _CRITERIA.finditer(checks) for ref in _REF.findall(m[1])}


def decomposition_problems(text: str, brief: Brief, *, lang: str = "ru") -> list[str]:
    """Нарушения графа DT/BEH и критерии брифа, не покрытые ни одним BEH.

    `lang` — язык собственных текстов (уходят автору в промпт правки)."""
    checks, whole = split_decomposition(text)
    problems = list(graph_findings(checks, whole, lang=lang))
    cited = _cited(checks)
    for fn in brief.functions:
        for k in range(1, len(fn.criteria) + 1):
            ref = f"{fn.id}/{k}"
            if ref not in cited:
                problems.append(_UNCOVERED[lang].format(ref=ref))
    return problems
