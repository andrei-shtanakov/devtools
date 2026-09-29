"""criteria_check — сверка ответа criteria-closure/v1 (спека §5.3) и исход (§3.3).

Итоги производителя и статусы AC не принимаются: devtools пересчитывает по
своим байтам (полнота определений, тела функций, lock, content).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from governance import criteria_graph as cgr
from governance import criteria_tokens as ct

REASONS = {
    "unconfirmed": {"no-test", "no-product-execution", "subprocess-only", "not-passed", "nondeterministic"},
    "error": {"io", "runner"},
}
_ECHO = ("protocol", "owner_repo", "workstream", "code", "bundle_pin", "product_sha")
_LEVEL = ("product_roots", "environment", "content_sha256")


def expected_definitions(
    test_files: dict[str, str], code: str, beh_ids: list[str]
) -> dict[str, set[tuple[str, str]]]:
    """BEH → определения тестов, несущие его токен (по байтам devtools)."""
    out: dict[str, set[tuple[str, str]]] = {b: set() for b in beh_ids}
    for path, src in test_files.items():
        for qn, tokens in ct.definition_tokens(src).items():
            for b in beh_ids:
                if f"{code}:{b}" in tokens:
                    out[b].add((path, qn))
    return out


def roots_findings(roots: object) -> list[str]:
    """Продуктовые корни ответа — не доверенная цифра (ревью среза 1, I4)."""
    if not isinstance(roots, list) or not roots:
        return ["product_roots пуст или не список"]
    out: list[str] = []
    for r in roots:
        parts = str(r).replace("\\", "/").split("/")
        if str(r).startswith("/") or ".." in parts:
            out.append(f"product_root {r!r} вне репо")
        elif parts[0] in ("tests", "test") or "tests" in parts:
            out.append(f"product_root {r!r} — тестовый путь")
    return out


def _traced_selector_findings(bid: str, s: dict, function_lines: dict[str, set[int]]) -> list[str]:
    out: list[str] = []
    runs = [r for r in s.get("runs", []) if r.get("phase") == "call"]
    if len(runs) < 2 or any(r.get("outcome") != "passed" for r in runs):
        out.append(f"{bid}: traced без двух passed в фазе call ({s.get('node_id')})")
    body = [p for p in s.get("product_lines", []) if p.get("line") in function_lines.get(p.get("file"), set())]
    if not body:
        out.append(f"{bid}: traced без исполненных строк тел функций продукта ({s.get('node_id')})")
    if s.get("subprocess") and not body:
        out.append(f"{bid}: traced при исполнении только в подпроцессе ({s.get('node_id')})")
    return out


def validate_response(
    request: dict, response: dict, *, expected: dict[str, set[tuple[str, str]]],
    function_lines: dict[str, set[int]], lock_sha: str, content_sha: str,
) -> list[str]:
    """Пусто — ответ валиден; иначе причины отказа шага."""
    out: list[str] = []
    for key in _ECHO:
        if response.get(key) != request.get(key):
            out.append(f"эхо {key} не совпало с запросом")
    if response.get("error") is not None or response.get("not_applicable") is not None:
        if response.get("beh"):
            out.append("error/not_applicable уровня ответа вместе с beh")
        return out
    for key in _LEVEL:
        if key not in response:
            out.append(f"нет поля {key}")
    out += roots_findings(response.get("product_roots"))
    env = response.get("environment") or {}
    if env.get("lock_sha256") != lock_sha:
        out.append("sha256 lock не совпал")
    if response.get("content_sha256") != content_sha:
        out.append("content_sha256 не совпал")
    code = request["code"]
    want = [c["id"] for c in request["test_criteria"]]
    got = [b.get("id") for b in response.get("beh", [])]
    if sorted(got) != sorted(want) or len(set(got)) != len(got):
        out.append(f"множество BEH ответа {got} ≠ запросу {want}")
        return out
    for b in response["beh"]:
        bid = b["id"].split(":", 1)[1]
        status, reason = b.get("status"), b.get("reason")
        if status == "traced":
            if reason is not None:
                out.append(f"{bid}: traced с reason")
        elif status in REASONS:
            if reason not in REASONS[status]:
                out.append(f"{bid}: {status} без допустимой reason")
            continue
        else:
            out.append(f"{bid}: статус {status!r} вне словаря")
            continue
        defs = {(s["definition"]["file"], s["definition"]["qualname"]) for s in b.get("selectors", [])}
        if defs != expected.get(bid, set()):
            out.append(f"{bid}: определения селекторов {sorted(defs)} ≠ определениям с токеном {code}:{bid}")
        for s in b.get("selectors", []):
            out += _traced_selector_findings(bid, s, function_lines)
    return out


@dataclass(frozen=True)
class Outcome:
    closure: str
    beh_status: dict[str, str]
    ac_status: dict[str, str]
    stop_reasons: list[str] = field(default_factory=list)
    report_rows: list[str] = field(default_factory=list)


def outcome(graph: cgr.Graph, beh_status: dict[str, str]) -> Outcome:
    """Таблица §3.3: Must unconfirmed/error и любой error — стоп."""
    stops = [f"граф: {e}" for e in graph.errors] + [f"сирота: {o}" for o in cgr.orphan_findings(graph)]
    rows: list[str] = []
    for beh in cgr.test_behs(graph):
        st = beh_status.get(beh.id, "error")
        if st == "traced":
            continue
        line = f"{beh.id} ({beh.priority}): {st}"
        if st == "error" or beh.priority == "Must":
            stops.append(line)
        else:
            rows.append(line)
    acs = {a.id: cgr.derive_ac(a, graph, beh_status) for a in graph.acs.values()}
    for a in graph.acs.values():
        if a.priority == "Must" and acs[a.id] in ("unconfirmed", "error"):
            stops.append(f"{a.id} (Must): {acs[a.id]}")
    return Outcome("blocked" if stops else "traced", dict(beh_status), acs, stops, rows)


def parse_response(code: int, stdout: str, schema: dict | None) -> tuple[dict | None, str | None]:
    """Первая линия отказа §5.3: код, пустота, JSON, схема (если вендорена)."""
    import json

    import jsonschema

    if code not in (0, 3):
        return None, f"код выхода {code} вне договорённых 0/3"
    if not stdout.strip():
        return None, "ответ пуст"
    try:
        resp = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return None, f"ответ не JSON: {exc}"
    if schema is not None:
        try:
            jsonschema.validate(resp, schema)
        except jsonschema.ValidationError as exc:
            return None, f"ответ не по схеме: {exc.message}"
    return resp, None
