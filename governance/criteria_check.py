"""criteria_check — сверка ответа criteria-closure/v1 (спека §5.3) и исход (§3.3).

Итоги производителя и статусы AC не принимаются: devtools пересчитывает по
своим байтам (полнота определений, тела функций, lock, content).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from governance import criteria_graph as cgr
from governance import criteria_tokens as ct


def owners(
    test_sources: dict[str, str], code: str, beh_ids: list[str]
) -> dict[str, set[tuple[str, str, int]]]:
    """BEH → определения-владельцы токена по парсеру devtools (§1.4).

    Неразбираемый исходник (синтаксис, NUL, предел рекурсии/памяти) не
    прерывает измерение (review I-1): producer ловит тот же набор
    исключений в `_owned_tokens` и отказывает только файлу коллекции, а
    кандидатом на владение бывает и несобранный файл (кандидатов отбирает
    `criteria_close._owner_candidates`, граница ревью #532). Если в тексте всё же есть квалифицированный
    токен BEH, записывается синтетический владелец `(путь, "<unparseable>",
    0)` — его нет ни в одном `test_items`, и `excluded_owner_behs`
    заблокирует `traced` именованной причиной; иначе файл пропускается, как
    недостижимый для сбора.
    """
    out: dict[str, set[tuple[str, str, int]]] = {b: set() for b in beh_ids}
    for path, src in test_sources.items():
        try:
            definitions = ct.owned_definitions(src)
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            for b in beh_ids:
                if ct.token_re(code, b).search(src):
                    out[b].add((path, "<unparseable>", 0))
            continue
        for d in definitions:
            for b in beh_ids:
                if f"{code}:{b}" in d.tokens:
                    out[b].add((path, d.qualname, d.line))
    return out


def _def(entry: dict) -> tuple[str, str, int]:
    d = entry["definition"]
    return d["file"], d["qualname"], d["line"]


def _dupes(node_ids: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for n in node_ids:
        counts[n] = counts.get(n, 0) + 1
    return {n: c for n, c in counts.items() if c > 1}


def completeness_findings(
    response: dict, owners_map: dict[str, set[tuple[str, str, int]]]
) -> list[str]:
    """B.6: селекторы BEH = все node_id из test_items, чьё определение владеет
    токеном; определение селектора = определение его node_id в test_items.

    Дублирующийся `node_id` в `test_items` схлопывается при построении
    словаря-поиска — отказ не может опираться на такой словарь, он
    докладывается явно (review: ложный `traced` через неоднозначный owner).
    """
    items_list = response["test_items"]
    out: list[str] = [
        f"test_items: node_id {n} повторяется ({c} раз)"
        for n, c in sorted(_dupes([i["node_id"] for i in items_list]).items())
    ]
    items = {i["node_id"]: _def(i) for i in items_list}
    for b in response["beh"]:
        bid = b["id"].split(":", 1)[1]
        want = {n for n, d in items.items() if d in owners_map.get(bid, set())}
        selector_ids = [s["node_id"] for s in b["selectors"]]
        got = set(selector_ids)
        for n in sorted(want - got):
            out.append(f"{bid}: нет селектора {n} (владелец токена собран)")
        for n in sorted(got - want):
            out.append(f"{bid}: лишний селектор {n}")
        for n, c in sorted(_dupes(selector_ids).items()):
            out.append(f"{bid}: селектор {n} повторяется ({c} раз)")
        for s in b["selectors"]:
            if s["node_id"] in items and _def(s) != items[s["node_id"]]:
                out.append(f"{bid}: определение селектора {s['node_id']} ≠ test_items")
    return out


def _covers(path: str, file: str) -> bool:
    norm = path.rstrip("/")
    if norm in ("", "."):
        return True
    return file == norm or file.startswith(norm + "/")


def excluded_owner_behs(
    response: dict, owners_map: dict[str, set[tuple[str, str, int]]]
) -> dict[str, str]:
    """spec-runner#623 п.4: владелец токена исключён из сбора или вне
    test_items (вне testpaths) — BEH не получает traced."""
    collected = {_def(i) for i in response["test_items"]}
    excluded = response["collection_excluded"]
    out: dict[str, str] = {}
    for bid, defs in owners_map.items():
        for d in sorted(defs):
            # явные исключения — ДО проверки «собран»: снятый с отбора параметр
            # не прячется за собранным соседом того же определения
            why = None
            for e in excluded:
                if e["how"] in ("skipped", "ignored") and _covers(e["path"], d[0]):
                    why = f"владелец в исключённом ({e['how']}) {e['path']}"
                elif e["how"] == "deselected" and e["definition"] and _def(e) == d:
                    # definition: null (§3.5) — элемент не функция/не разобран,
                    # токеном владеть не может, отбор по нему не блокирует.
                    why = f"тест владельца снят с отбора ({e['node_id']})"
                if why:
                    break
            if why is None and d not in collected:
                why = "владелец не собран (вне test_items и collection_excluded)"
            if why is not None:
                out[bid] = f"{d[0]}::{d[1]}@{d[2]}: {why}"
                break
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
    stops = [f"граф: {e}" for e in graph.errors] + [
        f"сирота: {o}" for o in cgr.orphan_findings(graph)
    ]
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


# Дизайн производителя §3.2: вид фиксирует retryable и код выхода. Своя
# таблица, а не чтение схемы: код выхода против вида сверяем сами.
ERROR_KINDS: dict[str, tuple[bool, int]] = {
    **dict.fromkeys(
        (
            "request-invalid",
            "owner-repo-mismatch",
            "product-sha-absent",
            "clone-failed",
            "environment-sync-failed",
            "unsupported-runtime",
            "collection-config-outside-checkout",
            "collection-failed",
            "timeout",
        ),
        (True, 2),
    ),
    **dict.fromkeys(
        (
            "lock-not-current",
            "environment-selection-invalid",
            "collection-error",
            "collection-mutated-checkout",
            "product-roots-undeclared",
            "product-roots-empty",
            "product-roots-invalid",
            "product-roots-no-python",
            "product-roots-overlap-tests",
            "definition-unresolved",
            "selector-absent",
            "distributed-execution",
        ),
        (False, 3),
    ),
}


@dataclass(frozen=True)
class Parsed:
    """Ответ производителя, разобранный и сверенный с кодом выхода (§5.3):
    `branch` — `answer` или `error`; `retryable` — вид ошибки по
    `ERROR_KINDS` (всегда `False` на ветке `answer`)."""

    response: dict
    branch: Literal["answer", "error"]
    retryable: bool


def parse_response(
    code: int, stdout: str, schema: dict | None
) -> tuple[Parsed | None, str | None]:
    """Первая линия отказа §5.3: код, JSON, схема, ветка против кода выхода."""
    import json

    import jsonschema

    if code not in (0, 2, 3):
        return None, f"код выхода {code} вне 0/2/3"
    if schema is None:
        return None, "схема ответа не вендорена"
    if not stdout.strip():
        return None, "ответ пуст"
    try:
        resp = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return None, f"ответ не JSON: {exc}"
    try:
        jsonschema.validate(resp, schema)
    except jsonschema.ValidationError as exc:
        return None, f"ответ не по схеме: {exc.message}"
    if "not_applicable" in resp:
        return None, "not_applicable в v1 не выпускается (дизайн §4)"
    if "error" in resp:
        if code == 0:
            return None, "код выхода 0 при ошибке"
        kind = resp["error"]["kind"]
        retry, want = ERROR_KINDS[kind]
        if code != want:
            return None, f"вид {kind} требует код {want}, получен {code}"
        return Parsed(resp, "error", retry), None
    if code != 0:
        return None, f"код выхода {code} при ответе"
    return Parsed(resp, "answer", False), None


_BEH_PRECEDENCE = (
    "not-passed",
    "nondeterministic",
    "no-product-execution",
    "subprocess-only",
)


def run_outcome(phases: dict[str, str]) -> str:
    """§3.7: исход прогона по трём фазам, как решает pytest.

    `passed` только если setup, call и teardown все `passed`. Иначе `failed`
    (любая фаза `failed`), иначе `skipped` — пропущенный teardown никогда не
    даёт `traced`.
    """
    values = [phases["setup"], phases["call"], phases["teardown"]]
    if all(v == "passed" for v in values):
        return "passed"
    return "failed" if "failed" in values else "skipped"


def _qualifying_lines(run: dict, function_lines: dict[str, set[int]]) -> int:
    """Число строк продукта из прогона, попавших в тела функций-владельцев."""
    return sum(
        1
        for block in run["product_lines"]
        for line in block["lines"]
        if line in function_lines.get(block["file"], set())
    )


def selector_status(
    sel: dict, function_lines: dict[str, set[int]]
) -> tuple[str, str | None]:
    """§3.7 по порядку, первое подходящее решает; оба прогона обязаны пройти
    каждую проверку — ни объединение, ни счёт по прогонам не прячут пустой
    второй прогон за хорошим первым.
    """
    runs = sel["runs"]
    for r in runs:
        if r["result"] == "error":
            return "error", r["reason"]
    outcomes = [run_outcome(r["phases"]) for r in runs]
    if any(o != "passed" for o in outcomes):
        same = runs[0]["phases"] == runs[1]["phases"]
        return (
            ("unconfirmed", "not-passed")
            if same
            else ("unconfirmed", "nondeterministic")
        )
    for r in runs:
        if _qualifying_lines(r, function_lines) == 0:
            reason = (
                "subprocess-only" if r["process_operations"] else "no-product-execution"
            )
            return "unconfirmed", reason
    return "traced", None


def beh_status(
    selector_results: list[tuple[str, str | None]],
) -> tuple[str, str | None]:
    """§3.7: BEH `traced` только если каждый выбранный тест `traced` (норма
    G3 «каждый»); иначе `error`, если есть хоть один селектор-`error`; иначе
    `unconfirmed` с причиной первого проигравшего селектора по приоритету
    `not-passed` > `nondeterministic` > `no-product-execution` >
    `subprocess-only`.
    """
    if not selector_results:
        return "unconfirmed", "no-test"
    if all(status == "traced" for status, _ in selector_results):
        return "traced", None
    errors = [reason for status, reason in selector_results if status == "error"]
    if errors:
        return "error", errors[0]
    reasons = {reason for status, reason in selector_results if status == "unconfirmed"}
    return "unconfirmed", next(p for p in _BEH_PRECEDENCE if p in reasons)


@dataclass(frozen=True)
class Checked:
    """Итог `validate_answer`: непустой `problems` — отказ шага; `beh_status`
    — статусы BEH по пересчёту devtools, не по ответу; `notes` — причина
    понижения `traced`→`unconfirmed` из `excluded_owner_behs`, по BEH."""

    problems: list[str]
    beh_status: dict[str, str]
    notes: dict[str, str] = field(default_factory=dict)


def validate_answer(
    request: dict,
    response: dict,
    *,
    declared_roots: tuple[str, ...],
    resolved_files: tuple[str, ...],
    groups: tuple[str, ...] | None,
    extras: tuple[str, ...],
    lock_sha: str,
    content_sha: str,
    function_lines: dict[str, set[int]],
    owners_map: dict[str, set[tuple[str, str, int]]],
    installed: str | None,
) -> Checked:
    """§5.3: ответ-вердикт сверяется по байтам devtools; статусы — свои."""
    out: list[str] = []
    if response["request"] != request:
        out.append("эхо request не совпало с запросом")
    if response["spec_runner_version"] != installed:
        out.append(
            f"spec_runner_version {response['spec_runner_version']} ≠ {installed}"
        )
    roots, env = response["product_roots"], response["environment"]
    if tuple(roots["declared"]) != declared_roots:
        out.append("product_roots.declared ≠ декларации на product_sha")
    if tuple(roots["files"]) != resolved_files:
        out.append("product_roots.files ≠ развёртке корней на product_sha")
    want_groups = list(groups) if groups is not None else None
    if env["groups"] != want_groups or env["extras"] != list(extras):
        out.append("environment.groups/extras ≠ конфигу продукта")
    if env["lock_sha256"] != lock_sha:
        out.append("lock_sha256 ≠ uv.lock на product_sha")
    if response["content_sha256"] != content_sha:
        out.append("content_sha256 ≠ пересчёту §6.1")
    want = sorted(c["id"] for c in request["test_criteria"])
    got = sorted(b["id"] for b in response["beh"])
    if got != want:
        return Checked([*out, f"множество BEH {got} ≠ запросу {want}"], {})
    tests_side = set(response["test_files"]) | {
        i["definition"]["file"] for i in response["test_items"]
    }
    overlap = sorted(set(resolved_files) & tests_side)
    if (
        overlap
    ):  # §3.4 product-roots-overlap-tests: исполнение теста ≠ исполнение продукта
        out.append(f"продукт пересекается с тестами: {overlap}")
    out += completeness_findings(response, owners_map)
    status: dict[str, str] = {}
    for b in response["beh"]:
        bid = b["id"].split(":", 1)[1]
        results = []
        for s in b["selectors"]:
            for i, r in enumerate(s["runs"]):
                if r["result"] != "complete":
                    continue
                if r["collected"] != [s["node_id"]]:
                    out.append(
                        f"{bid}: прогон {i + 1} собрал {r['collected']} вместо [{s['node_id']}]"
                    )
                if run_outcome(r["phases"]) != r["outcome"]:
                    out.append(
                        f"{bid}: outcome прогона {i + 1} ≠ фазам ({s['node_id']})"
                    )
                if (
                    sum(len(p["lines"]) for p in r["product_lines"])
                    != r["product_line_count"]
                ):
                    out.append(f"{bid}: product_line_count ≠ строкам ({s['node_id']})")
            mine = selector_status(s, function_lines)
            if mine != (s["status"], s.get("reason")):
                out.append(f"{bid}: статус селектора {s['node_id']} ≠ пересчёту {mine}")
            results.append(mine)
        mine_beh = beh_status(results)
        if mine_beh != (b["status"], b.get("reason")):
            out.append(f"{bid}: статус BEH ≠ пересчёту {mine_beh}")
        status[bid] = mine_beh[0]
    notes = excluded_owner_behs(response, owners_map)
    for bid in notes:
        if status.get(bid) == "traced":
            status[bid] = "unconfirmed"
    return Checked(out, status, notes)
