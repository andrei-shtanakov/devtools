"""criteria_check: сверка ответа (§5.3) и исход (§3.3)."""

from __future__ import annotations

import json

import pytest

from governance import criteria_check as ck
from governance import criteria_contract as cc
from governance import criteria_graph as cgr

SCHEMA = json.loads((cc.CONTRACT_DIR / "response.schema.json").read_text())


def golden(name: str) -> str:
    return (cc.RESPONSES_DIR / f"{name}.json").read_text()


REQ = "#### FR-01: A\n**Priority**: Must\n\n#### FR-02: B\n**Priority**: Should\n"
BEH = (
    "#### BEH-01: a\n`traces: [FR-01]`\n- **checked_by**: `status: planned` `kind: unit` `owner: qa` `target: t`\n\n"
    "#### BEH-02: b\n`traces: [FR-02]`\n- **checked_by**: `status: planned` `kind: unit` `owner: qa` `target: t`\n"
)
ACC = "#### AC-01: a · verification: test\ntraces: [FR-01]\nscenarios: [BEH-01]\n\n#### AC-02: b · verification: test\ntraces: [FR-02]\nscenarios: [BEH-02]\n"


def test_error_kinds_cover_schema_enum():
    enum = SCHEMA["definitions"]["error_kind"]["enum"]
    retryable = set(SCHEMA["definitions"]["retryable_kinds"]["enum"])
    assert set(ck.ERROR_KINDS) == set(enum)
    for kind, (retry, exit_code) in ck.ERROR_KINDS.items():
        assert retry == (kind in retryable)
        assert exit_code == (2 if retry else 3)


@pytest.mark.parametrize(
    ("code", "name", "branch", "retryable"),
    [
        (0, "answer", "answer", False),
        (3, "error-blocked", "error", False),
        (2, "error-retryable", "error", True),
    ],
)
def test_goldens_parse(code, name, branch, retryable):
    parsed, why = ck.parse_response(code, golden(name), SCHEMA)
    assert why is None
    assert (parsed.branch, parsed.retryable) == (branch, retryable)


@pytest.mark.parametrize(
    ("code", "name", "why"),
    [
        (2, "answer", "код выхода 2 при ответе"),
        (0, "error-blocked", "код выхода 0 при ошибке"),
        (2, "error-blocked", "вид product-roots-undeclared требует код 3"),
        (3, "error-retryable", "вид product-sha-absent требует код 2"),
        (0, "not-applicable", "not_applicable в v1 не выпускается"),
        (1, "answer", "код выхода 1 вне 0/2/3"),
    ],
)
def test_branch_and_exit_must_agree(code, name, why):
    parsed, got = ck.parse_response(code, golden(name), SCHEMA)
    assert parsed is None and why in got


def test_schema_is_required():
    parsed, why = ck.parse_response(0, golden("answer"), None)
    assert parsed is None and "схема" in why


def _schema_violating_answer() -> str:
    resp = json.loads(golden("answer"))
    resp["protocol"] = 2
    return json.dumps(resp)


@pytest.mark.parametrize(
    ("stdout", "why"),
    [
        ("", "ответ пуст"),
        ("   \n\t  ", "ответ пуст"),
        ("not json", "ответ не JSON"),
    ],
)
def test_parse_response_refuses_before_schema(stdout, why):
    parsed, got = ck.parse_response(0, stdout, SCHEMA)
    assert parsed is None and why in got


def test_parse_response_refuses_schema_violation():
    parsed, why = ck.parse_response(0, _schema_violating_answer(), SCHEMA)
    assert parsed is None and "ответ не по схеме" in why


def test_orphan_blocks_closure():
    beh = (
        BEH
        + "\n#### BEH-03: c\n`traces: [FR-01]`\n- **checked_by**: `status: planned` `kind: unit` `owner: qa` `target: t`\n"
    )
    g = cgr.build_graph(REQ, beh, ACC)
    out = ck.outcome(g, {"BEH-01": "traced", "BEH-02": "traced", "BEH-03": "traced"})
    assert out.closure == "blocked" and any("BEH-03" in r for r in out.stop_reasons)


def test_must_ac_over_should_beh_stops():
    acc = "#### AC-01: a · verification: test\ntraces: [FR-01]\nscenarios: [BEH-01, BEH-02]\n\n#### AC-02: b · verification: test\ntraces: [FR-02]\nscenarios: [BEH-02]\n"
    g = cgr.build_graph(REQ, BEH, acc)
    out = ck.outcome(g, {"BEH-01": "traced", "BEH-02": "unconfirmed"})
    assert out.ac_status["AC-01"] == "unconfirmed"
    assert out.closure == "blocked" and any("AC-01" in r for r in out.stop_reasons)


def test_outcome_must_unconfirmed_stops_should_reports():
    g = cgr.build_graph(REQ, BEH, ACC)
    ok = ck.outcome(g, {"BEH-01": "traced", "BEH-02": "unconfirmed"})
    assert ok.closure == "traced" and ok.stop_reasons == []
    assert any("BEH-02" in r for r in ok.report_rows)
    stop = ck.outcome(g, {"BEH-01": "unconfirmed", "BEH-02": "traced"})
    assert stop.closure == "blocked" and any("BEH-01" in r for r in stop.stop_reasons)
    err = ck.outcome(g, {"BEH-01": "traced", "BEH-02": "error"})
    assert err.closure == "blocked"


def test_graph_errors_block():
    g = cgr.build_graph(REQ, BEH.replace("`traces: [FR-02]`", "`traces: []`"), ACC)
    assert ck.outcome(g, {"BEH-01": "traced", "BEH-02": "traced"}).closure == "blocked"


LINES_3_7 = {"pkg/mod.py": {3, 4}}


def run_3_7(**over):
    base = {
        "result": "complete",
        "collected": ["t::a"],
        "phases": {"setup": "passed", "call": "passed", "teardown": "passed"},
        "outcome": "passed",
        "product_lines": [{"file": "pkg/mod.py", "lines": [3, 4]}],
        "product_line_count": 2,
        "process_operations": [],
    }
    return {**base, **over}


def sel_3_7(*runs):
    return {"node_id": "t::a", "runs": list(runs)}


@pytest.mark.parametrize(
    ("phases", "want"),
    [
        ({"setup": "passed", "call": "passed", "teardown": "passed"}, "passed"),
        ({"setup": "passed", "call": "passed", "teardown": "failed"}, "failed"),
        ({"setup": "passed", "call": "passed", "teardown": "skipped"}, "skipped"),
        ({"setup": "skipped", "call": "not-reached", "teardown": "passed"}, "skipped"),
    ],
)
def test_run_outcome_over_three_phases(phases, want):
    assert ck.run_outcome(phases) == want


def test_both_runs_traced():
    assert ck.selector_status(sel_3_7(run_3_7(), run_3_7()), LINES_3_7) == (
        "traced",
        None,
    )


def test_empty_second_run_is_not_traced():
    empty = run_3_7(product_lines=[], product_line_count=0)
    assert ck.selector_status(sel_3_7(run_3_7(), empty), LINES_3_7) == (
        "unconfirmed",
        "no-product-execution",
    )


def test_lines_outside_function_bodies_do_not_qualify():
    header = run_3_7(
        product_lines=[{"file": "pkg/mod.py", "lines": [1]}], product_line_count=1
    )
    assert (
        ck.selector_status(sel_3_7(header, header), LINES_3_7)[1]
        == "no-product-execution"
    )


def test_subprocess_only():
    child = run_3_7(
        product_lines=[], product_line_count=0, process_operations=["subprocess.Popen"]
    )
    assert ck.selector_status(sel_3_7(child, child), LINES_3_7) == (
        "unconfirmed",
        "subprocess-only",
    )


def test_error_run_wins():
    err = {"result": "error", "reason": "io", "detail": "x"}
    assert ck.selector_status(sel_3_7(run_3_7(), err), LINES_3_7) == ("error", "io")


def test_nondeterministic_vs_not_passed():
    failed = run_3_7(
        phases={"setup": "passed", "call": "failed", "teardown": "passed"},
        outcome="failed",
    )
    assert ck.selector_status(sel_3_7(run_3_7(), failed), LINES_3_7) == (
        "unconfirmed",
        "nondeterministic",
    )
    assert ck.selector_status(sel_3_7(failed, failed), LINES_3_7) == (
        "unconfirmed",
        "not-passed",
    )


def test_teardown_skipped_never_traced():
    t = run_3_7(
        phases={"setup": "passed", "call": "passed", "teardown": "skipped"},
        outcome="skipped",
    )
    assert ck.selector_status(sel_3_7(t, t), LINES_3_7) == ("unconfirmed", "not-passed")


def test_beh_precedence():
    assert ck.beh_status([]) == ("unconfirmed", "no-test")
    assert ck.beh_status([("traced", None), ("traced", None)]) == ("traced", None)
    assert ck.beh_status([("traced", None), ("error", "io")]) == ("error", "io")
    assert ck.beh_status(
        [("unconfirmed", "subprocess-only"), ("unconfirmed", "not-passed")]
    ) == ("unconfirmed", "not-passed")


# B.6, #623 п.4: полнота по node_id и исключённые владельцы токена.

SRC = "def test_a():\n    # ENC:BEH-01\n    assert 1\n"


def answer(test_items, beh_selectors, excluded=()):
    return {
        "test_items": test_items,
        "collection_excluded": list(excluded),
        "beh": [{"id": "ENC:BEH-01", "status": "traced", "selectors": beh_selectors}],
    }


def item(node, line=1, file="tests/test_a.py", qn="test_a"):
    return {"node_id": node, "definition": {"file": file, "qualname": qn, "line": line}}


def test_owners_by_file_qualname_line():
    assert ck.owners({"tests/test_a.py": SRC}, "ENC", ["BEH-01"]) == {
        "BEH-01": {("tests/test_a.py", "test_a", 1)}
    }


def test_lost_parametrized_node_id_refused():
    o = ck.owners({"tests/test_a.py": SRC}, "ENC", ["BEH-01"])
    items = [item("tests/test_a.py::test_a[1]"), item("tests/test_a.py::test_a[2]")]
    resp = answer(items, [item("tests/test_a.py::test_a[1]")])
    assert any("test_a[2]" in f for f in ck.completeness_findings(resp, o))


def test_selector_definition_must_equal_test_item():
    o = ck.owners({"tests/test_a.py": SRC}, "ENC", ["BEH-01"])
    resp = answer(
        [item("tests/test_a.py::test_a")], [item("tests/test_a.py::test_a", line=9)]
    )
    assert ck.completeness_findings(resp, o)


def test_definition_from_other_branch_is_not_owner():
    """Парсер (§1.4, «последнее определение побеждает») отдаёт токен def из
    `else` (строка 6); pytest на рантайме собрал def из `if` (строка 3) —
    владелец не собран, BEH не traced."""
    src = (
        "import sys\nif sys.version_info >= (3,):\n    def test_a():\n        assert 1\n"
        "else:\n    def test_a():\n        # ENC:BEH-01\n        assert 1\n"
    )
    o = ck.owners({"tests/test_a.py": src}, "ENC", ["BEH-01"])
    assert o == {"BEH-01": {("tests/test_a.py", "test_a", 6)}}
    resp = answer([item("tests/test_a.py::test_a", line=3)], [])
    assert "BEH-01" in ck.excluded_owner_behs(resp, o)
    assert ck.completeness_findings(resp, o) == []


@pytest.mark.parametrize(
    "excluded",
    [
        {"how": "skipped", "path": "tests/test_a.py", "reason": "importorskip"},
        {"how": "ignored", "path": "tests"},
        {
            "how": "deselected",
            "node_id": "tests/test_a.py::test_a",
            "definition": {"file": "tests/test_a.py", "qualname": "test_a", "line": 1},
        },
    ],
)
def test_excluded_owner_blocks_traced(excluded):
    o = ck.owners({"tests/test_a.py": SRC}, "ENC", ["BEH-01"])
    resp = answer([], [], [excluded])
    assert "BEH-01" in ck.excluded_owner_behs(resp, o)


def test_partially_deselected_parametrized_blocks_traced():
    """Один параметр собран, другой снят с отбора — у определения тот же
    (file, qualname, line); собранный сосед не прячет исключение."""
    o = ck.owners({"tests/test_a.py": SRC}, "ENC", ["BEH-01"])
    dropped = {
        "how": "deselected",
        "node_id": "tests/test_a.py::test_a[2]",
        "definition": {"file": "tests/test_a.py", "qualname": "test_a", "line": 1},
    }
    resp = answer(
        [item("tests/test_a.py::test_a[1]")],
        [item("tests/test_a.py::test_a[1]")],
        [dropped],
    )
    assert "BEH-01" in ck.excluded_owner_behs(resp, o)


def test_owner_outside_testpaths_blocks_traced():
    o = ck.owners({"tests/test_a.py": SRC}, "ENC", ["BEH-01"])
    assert "BEH-01" in ck.excluded_owner_behs(answer([], []), o)


# Review round 1: duplicate node_ids, null-definition deselect, path forms.


def test_duplicate_test_item_node_id_refused():
    """Риск (f): дублирующийся node_id в test_items схлопывается при
    построении словаря-поиска — иначе владелец без селектора проходит
    как «собранный» (ложный traced)."""
    o = ck.owners({"tests/test_a.py": SRC}, "ENC", ["BEH-01"])
    dup = [
        item("tests/test_a.py::test_a"),
        item("tests/test_a.py::test_a", file="tests/other.py", qn="test_other"),
    ]
    resp = answer(dup, [])
    findings = ck.completeness_findings(resp, o)
    assert any("test_a" in f and "повтор" in f for f in findings)


def test_deselected_null_definition_never_blocks_an_owner():
    """Round 2 ruling: `definition: null` (§3.5) значит «не функция/не
    разобран» — такой элемент не может владеть токеном, поэтому снятый с
    отбора посторонний элемент того же файла (напр. doctest) не должен
    блокировать честно собранного и выбранного владельца."""
    o = ck.owners({"tests/test_a.py": SRC}, "ENC", ["BEH-01"])
    unrelated = {
        "how": "deselected",
        "node_id": "tests/test_a.py::test_other",
        "definition": None,
    }
    resp = answer(
        [item("tests/test_a.py::test_a")],
        [item("tests/test_a.py::test_a")],
        [unrelated],
    )
    assert "BEH-01" not in ck.excluded_owner_behs(resp, o)


@pytest.mark.parametrize(
    "excluded",
    [
        {"how": "skipped", "path": "tests/test_a.py/", "reason": "importorskip"},
        {"how": "ignored", "path": "."},
    ],
)
def test_covers_normalises_trailing_slash_and_root(excluded):
    """Минор (b): завершающий `/` у файлового path и корень `.` тоже
    покрывают владельца. Не просто «BEH исключён» (это даёт и общий фоллбэк
    «не собран») — должна сработать именно явная причина `_covers`."""
    o = ck.owners({"tests/test_a.py": SRC}, "ENC", ["BEH-01"])
    resp = answer([], [], [excluded])
    msg = ck.excluded_owner_behs(resp, o)["BEH-01"]
    assert "исключённом" in msg and excluded["how"] in msg


def test_duplicate_selector_node_id_refused():
    """Минор: повторяющийся node_id среди селекторов одного BEH молча
    схлопывается в `set` — отказ должен его заметить."""
    o = ck.owners({"tests/test_a.py": SRC}, "ENC", ["BEH-01"])
    dup_selectors = [
        item("tests/test_a.py::test_a"),
        item("tests/test_a.py::test_a"),
    ]
    resp = answer([item("tests/test_a.py::test_a")], dup_selectors)
    assert any("повтор" in f for f in ck.completeness_findings(resp, o))


# Task 6: validate_answer — сверка ответа целиком против v1 golden-эталона.


def golden_answer():
    return json.loads(golden("answer"))


def ok_args(resp):
    return {
        "declared_roots": ("pkg",),
        "resolved_files": ("pkg/__init__.py", "pkg/mod.py"),
        "groups": None,
        "extras": (),
        "lock_sha": "c" * 64,
        "content_sha": "d" * 64,
        "function_lines": {"pkg/mod.py": {3, 4}},
        "owners_map": {
            "BEH-01": {("tests/test_mod.py", "test_run", 4)},
            "BEH-02": set(),
        },
        "installed": "4.5.0",
    }


def test_golden_answer_is_valid():
    resp = golden_answer()
    got = ck.validate_answer(resp["request"], resp, **ok_args(resp))
    assert got.problems == []
    assert got.beh_status == {"BEH-01": "traced", "BEH-02": "unconfirmed"}


def ok_request():
    return golden_answer()["request"]


@pytest.mark.parametrize(
    ("mutate", "why"),
    [
        (lambda r, a: r["request"].update(code="XYZ"), "эхо request"),
        (lambda r, a: a.update(declared_roots=("other",)), "product_roots.declared"),
        (lambda r, a: a.update(resolved_files=("pkg/mod.py",)), "product_roots.files"),
        (lambda r, a: a.update(groups=("test",)), "environment.groups"),
        (lambda r, a: a.update(lock_sha="e" * 64), "lock_sha256"),
        (lambda r, a: a.update(content_sha="e" * 64), "content_sha256"),
        (lambda r, a: a.update(installed="4.4.0"), "spec_runner_version"),
        (
            lambda r, a: r["beh"][0]["selectors"][0]["runs"][1].update(
                outcome="failed"
            ),
            "outcome",
        ),
        (
            lambda r, a: r["beh"][0].update(status="unconfirmed", reason="not-passed"),
            "статус",
        ),
        (
            lambda r, a: r["beh"][0]["selectors"][0]["runs"][0].update(
                product_line_count=5
            ),
            "product_line_count",
        ),
    ],
)
def test_answer_refusals(mutate, why):
    resp = golden_answer()
    args = ok_args(resp)
    mutate(resp, args)
    got = ck.validate_answer(ok_request(), resp, **args)
    assert any(why in p for p in got.problems), got.problems


@pytest.mark.parametrize(
    ("run_idx", "collected"),
    [
        (0, []),
        (1, []),
        (0, ["tests/test_mod.py::test_other"]),
        (1, ["tests/test_mod.py::test_run", "tests/test_mod.py::test_other"]),
    ],
)
def test_run_must_collect_exactly_its_selector(run_idx, collected):
    resp = golden_answer()
    resp["beh"][0]["selectors"][0]["runs"][run_idx]["collected"] = collected
    got = ck.validate_answer(ok_request(), resp, **ok_args(resp))
    assert any("собрал" in p for p in got.problems), got.problems


def test_product_overlapping_tests_is_refused():
    resp = golden_answer()
    args = ok_args(resp)
    args["resolved_files"] = ("pkg/__init__.py", "pkg/mod.py", "tests/test_mod.py")
    resp["product_roots"]["files"] = list(args["resolved_files"])
    got = ck.validate_answer(ok_request(), resp, **args)
    assert any("пересекается с тестами" in p for p in got.problems)


def test_excluded_owner_downgrades_traced():
    resp = golden_answer()
    args = ok_args(resp)
    args["owners_map"]["BEH-01"].add(("tests/test_mod.py", "test_gone", 9))
    got = ck.validate_answer(ok_request(), resp, **args)
    assert got.problems == [] and got.beh_status["BEH-01"] == "unconfirmed"
    assert "BEH-01" in got.notes


def test_beh_foreign_code_prefix_refused():
    """BEH-set check compares FULL ids: a response BEH id carrying the wrong
    code prefix (`XYZ:BEH-01` instead of `ENC:BEH-01`) is not the same id as
    the request's, even though `completeness_findings` strips the prefix
    before matching against owners."""
    resp = golden_answer()
    resp["beh"][0]["id"] = "XYZ:BEH-01"
    got = ck.validate_answer(ok_request(), resp, **ok_args(resp))
    assert any("BEH" in p for p in got.problems), got.problems


# Task 7 (ruling 4): regression guards for Task 6's validate_answer — each
# passing already; a failure here is a real bug in validate_answer, not a
# wording nit.


def test_honest_error_run_recomputes_to_error_with_no_problems():
    """(a) один прогон селектора — result: error/reason: runner; производитель
    честно заявляет тот же статус и на селекторе, и на BEH — проблем нет."""
    resp = golden_answer()
    sel0 = resp["beh"][0]["selectors"][0]
    sel0["runs"][1] = {"result": "error", "reason": "runner", "detail": "boom"}
    sel0["status"] = "error"
    sel0["reason"] = "runner"
    resp["beh"][0]["status"] = "error"
    resp["beh"][0]["reason"] = "runner"
    got = ck.validate_answer(ok_request(), resp, **ok_args(resp))
    assert got.problems == []
    assert got.beh_status["BEH-01"] == "error"


def test_error_run_but_producer_claims_traced_is_refused():
    """(b) тот же прогон с ошибкой, но производитель оставил заявленный
    статус селектора/BEH `traced` — отказ."""
    resp = golden_answer()
    resp["beh"][0]["selectors"][0]["runs"][1] = {
        "result": "error",
        "reason": "runner",
        "detail": "boom",
    }
    got = ck.validate_answer(ok_request(), resp, **ok_args(resp))
    assert any("статус селектора" in p for p in got.problems), got.problems


def test_empty_function_lines_but_producer_claims_traced_is_refused():
    """(c) те же прогоны, но пустой `function_lines` (продукт пересчитан как
    «не исполнялся») — пересчёт unconfirmed/no-product-execution, заявленный
    `traced` отказан."""
    resp = golden_answer()
    args = ok_args(resp)
    args["function_lines"] = {}
    got = ck.validate_answer(ok_request(), resp, **args)
    assert any("статус селектора" in p for p in got.problems), got.problems


def test_installed_none_is_refused():
    """(d) `installed=None` (spec-runner не установлен на машине измерения) —
    эхо `spec_runner_version` не может совпасть ни с чем, отказ."""
    resp = golden_answer()
    args = ok_args(resp)
    args["installed"] = None
    got = ck.validate_answer(ok_request(), resp, **args)
    assert any("spec_runner_version" in p for p in got.problems), got.problems


def test_declared_empty_groups_differs_from_response_null_groups():
    """(e) `groups=()` (продукт объявил ПУСТОЙ список групп) ≠ `environment.groups:
    null` ответа (не объявлено вовсе) — design §3.3: null и [] разные среды."""
    resp = golden_answer()
    args = ok_args(resp)
    args["groups"] = ()
    got = ck.validate_answer(ok_request(), resp, **args)
    assert any("environment.groups" in p for p in got.problems), got.problems


def test_selector_mismatch_refused_even_when_beh_status_agrees():
    """(f) заявленный статус СЕЛЕКТОРА разошёлся с пересчётом, хотя заявленный
    статус BEH совпал с агрегатом пересчёта (который берётся из пересчитанного
    результата селектора, а не из его неверно заявленных полей) — отказ не
    прячется за совпавшим BEH-статусом."""
    resp = golden_answer()
    sel0 = resp["beh"][0]["selectors"][0]
    sel0["status"] = "unconfirmed"
    sel0["reason"] = "not-passed"
    got = ck.validate_answer(ok_request(), resp, **ok_args(resp))
    assert any("статус селектора" in p for p in got.problems), got.problems
