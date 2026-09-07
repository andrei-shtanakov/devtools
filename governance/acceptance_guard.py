"""Гард acceptance-узла: AC-грамматика и покрытие Must-требований (спека
2026-09-07-acceptance-node §3). Чистые функции над строками — без
git/ФС/steward; потребители — S4-гейт (runner) и мост (task_bridge).
Канон — governance/design_guard.py и governance/decomposition_guard.py."""

from __future__ import annotations

import re
from dataclasses import dataclass

_AC_HEAD_RE = re.compile(
    r"^####\s+(AC-\d+[a-z]?):\s*(.+?)\s*·\s*verification:\s*"
    r"(test|manual|metric)\s*$",
    re.M,
)
# NEAR ловит любую AC-подобную шапку (уроки PR #145/#148): неизвестная
# форма id/строки — находка формы, не молчание.
_AC_NEAR_RE = re.compile(r"^####\s+(AC-[^\s:]*)", re.M)
_SECTION_RE = re.compile(r"^#{1,3}\s", re.M)

#: Дословная декларация пустого набора Must (§3 спеки).
EMPTY_MUST_DECLARATION = "Must-требований во входном наборе нет"


def _list_field(block: str, name: str) -> tuple[str, ...] | None:
    m = re.search(rf"^{name}:\s*\[([^\]]*)\]\s*$", block, re.M)
    if m is None:
        return None
    inner = m.group(1).strip()
    if not inner:
        return ()
    return tuple(part.strip() for part in inner.split(","))


@dataclass(frozen=True)
class AcCriterion:
    """Один критерий приёмки (заголовок #### AC-NN)."""

    ac_id: str
    title: str
    verification: str
    traces: tuple[str, ...]
    scenarios: tuple[str, ...]


def parse_ac_criteria(text: str) -> tuple[list[AcCriterion], list[str]]:
    """AC-грамматика → (критерии, findings формы).

    Findings формы: near-miss заголовок, дубль AC-id, пустой/отсутствующий
    traces, `verification: test` без непустого scenarios. Блок критерия
    ограничен следующим AC-заголовком ЛИБО секцией уровня 1–3 (урок
    PR #145 — хвост документа не читается как метаданные последнего AC).
    """
    findings: list[str] = []
    strict = {m.start() for m in _AC_HEAD_RE.finditer(text)}
    for near in _AC_NEAR_RE.finditer(text):
        if near.start() not in strict:
            findings.append(
                f"{near.group(1)}: заголовок не соответствует машинной "
                "грамматике AC (`#### AC-NN: <название> · verification: "
                "test|manual|metric`)"
            )
    matches = list(_AC_HEAD_RE.finditer(text))
    seen: dict[str, int] = {}
    crits: list[AcCriterion] = []
    for idx, m in enumerate(matches):
        ac_id = m.group(1)
        seen[ac_id] = seen.get(ac_id, 0) + 1
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[m.end() : end]
        section = _SECTION_RE.search(block)
        if section is not None:
            block = block[: section.start()]
        traces = _list_field(block, "traces")
        scenarios = _list_field(block, "scenarios") or ()
        if not traces:
            findings.append(
                f"{ac_id}: строка traces отсутствует или пуста (каждый "
                "критерий обязан покрывать хотя бы одно требование)"
            )
        if m.group(3) == "test" and not scenarios:
            findings.append(
                f"{ac_id}: verification: test без непустого scenarios — "
                "тестовый критерий обязан называть свои сценарии"
            )
        crits.append(AcCriterion(
            ac_id=ac_id, title=m.group(2), verification=m.group(3),
            traces=traces or (), scenarios=scenarios,
        ))
    for ac_id, count in seen.items():
        if count > 1:
            findings.append(
                f"{ac_id}: объявлен {count} раза (ожидается ровно один)"
            )
    return crits, findings


_REQ_HEAD_RE = re.compile(r"^####\s+((?:FR|NFR)-\d+[a-z]?):", re.M)
_REQ_NEAR_RE = re.compile(r"^#{2,6}\s+((?:FR|NFR)-[^\s:]*)", re.M)
_PRIORITY_RE = re.compile(r"^\*\*Priority\*\*:\s*(\S+)", re.M)
_BEH_ID_RE = re.compile(r"^####\s+(BEH-\d+[a-z]?):", re.M)


def _parse_requirements(req_text: str) -> tuple[dict[str, str], list[str]]:
    """id → priority по строгой грамматике; findings достоверности.

    Near-miss заголовок FR/NFR и заголовок без распознанной строки
    `**Priority**: …` — находки «входное множество недостоверно» (канон
    design_guard, major круга 2 ревью спеки): промах грамматики не
    должен молча сжимать множество Must.
    """
    findings: list[str] = []
    strict = {m.start() for m in _REQ_HEAD_RE.finditer(req_text)}
    for near in _REQ_NEAR_RE.finditer(req_text):
        if near.start() not in strict:
            findings.append(
                f"{near.group(1)}: заголовок requirements не соответствует "
                "машинной грамматике — входное множество недостоверно"
            )
    heads = list(_REQ_HEAD_RE.finditer(req_text))
    priorities: dict[str, str] = {}
    seen: dict[str, int] = {}
    for idx, m in enumerate(heads):
        rid = m.group(1)
        seen[rid] = seen.get(rid, 0) + 1
        end = heads[idx + 1].start() if idx + 1 < len(heads) else len(req_text)
        block = req_text[m.end() : end]
        section = _SECTION_RE.search(block)
        if section is not None:
            block = block[: section.start()]
        pr = _PRIORITY_RE.search(block)
        if pr is None:
            findings.append(
                f"{rid}: строка **Priority**: не распознана — "
                "входное множество недостоверно"
            )
            continue
        if pr.group(1) not in ("Must", "Should"):
            findings.append(
                f"{rid}: значение **Priority**: {pr.group(1)} вне "
                "словаря Must|Should — входное множество недостоверно"
            )
            continue
        priorities[rid] = pr.group(1)
    for rid, count in seen.items():
        if count > 1:
            findings.append(
                f"{rid}: объявлен {count} раза (ожидается ровно один)"
            )
    return priorities, findings


def _parse_beh_ids(beh_text: str) -> set[str]:
    return {m.group(1) for m in _BEH_ID_RE.finditer(beh_text)}


def coverage_findings(
    req_text: str, beh_text: str, acc_text: str
) -> list[str]:
    """Инварианты §3 спеки: Must-покрытие FR/NFR + ссылочная целостность.

    Findings накапливаются (гейт показывает всё сразу); пустой список —
    покрытие валидно.
    """
    crits, findings = parse_ac_criteria(acc_text)
    priorities, req_findings = _parse_requirements(req_text)
    findings += req_findings
    beh_ids = _parse_beh_ids(beh_text)

    for c in crits:
        for ref in c.traces:
            if ref not in priorities:
                findings.append(
                    f"{c.ac_id}: ссылка на несуществующее требование {ref}"
                )
        for beh in c.scenarios:
            if beh not in beh_ids:
                findings.append(
                    f"{c.ac_id}: сценарий {beh} отсутствует в behaviour-spec"
                )

    covered: set[str] = set()
    for c in crits:
        covered.update(c.traces)
    must = [rid for rid, pr in priorities.items() if pr == "Must"]
    if not must:
        declared = re.search(
            rf"^{re.escape(EMPTY_MUST_DECLARATION)}", acc_text, re.M
        )
        if declared is None:
            findings.append(
                "acceptance: множество Must-требований пусто, но "
                f"строка-декларация «{EMPTY_MUST_DECLARATION}» отсутствует"
            )
        return findings
    for rid in must:
        if rid not in covered:
            findings.append(
                f"{rid}: Must-требование не покрыто ни одним AC"
            )
    return findings
