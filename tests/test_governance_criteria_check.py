"""criteria_check: сверка ответа (§5.3) и исход (§3.3)."""

from __future__ import annotations

import copy
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
TEST_SRC = "def test_a():\n    # ENC:BEH-01\n    assert 1\n\ndef test_b():\n    # ENC:BEH-02\n    assert 1\n"

REQUEST = {
    "protocol": 1,
    "owner_repo": "devtools",
    "workstream": "ws",
    "code": "ENC",
    "bundle_pin": "p" * 40,
    "product_sha": "s" * 40,
    "test_criteria": [
        {"id": "ENC:BEH-01", "verify_task": False},
        {"id": "ENC:BEH-02", "verify_task": False},
    ],
}


def sel(qn, lines=(5,)):
    return {
        "node_id": f"tests/t.py::{qn}",
        "definition": {"file": "tests/t.py", "qualname": qn},
        "runs": [
            {"phase": "call", "outcome": "passed"},
            {"phase": "call", "outcome": "passed"},
        ],
        "product_lines": [{"file": "pkg/m.py", "line": n} for n in lines],
        "subprocess": False,
    }


GOOD = {
    **{
        k: REQUEST[k]
        for k in (
            "protocol",
            "owner_repo",
            "workstream",
            "code",
            "bundle_pin",
            "product_sha",
        )
    },
    "product_roots": ["pkg"],
    "environment": {"lock_sha256": "L", "python": "3.12", "pytest_plugins": []},
    "content_sha256": "C",
    "beh": [
        {"id": "ENC:BEH-01", "status": "traced", "selectors": [sel("test_a")]},
        {"id": "ENC:BEH-02", "status": "traced", "selectors": [sel("test_b")]},
    ],
}


def check(resp):
    expected = ck.expected_definitions(
        {"tests/t.py": TEST_SRC}, "ENC", ["BEH-01", "BEH-02"]
    )
    return ck.validate_response(
        REQUEST,
        resp,
        expected=expected,
        function_lines={"pkg/m.py": {5, 6}},
        lock_sha="L",
        content_sha="C",
    )


def test_good_response_is_valid():
    assert check(GOOD) == []


def mutate(fn):
    r = copy.deepcopy(GOOD)
    fn(r)
    return r


@pytest.mark.parametrize(
    "name,fn",
    [
        ("foreign product_sha", lambda r: r.update(product_sha="x" * 40)),
        ("missing BEH", lambda r: r["beh"].pop()),
        (
            "extra BEH",
            lambda r: r["beh"].append(
                {"id": "ENC:BEH-09", "status": "traced", "selectors": [sel("test_a")]}
            ),
        ),
        ("duplicate BEH", lambda r: r["beh"].append(copy.deepcopy(r["beh"][0]))),
        ("no environment", lambda r: r.pop("environment")),
        ("traced one run", lambda r: r["beh"][0]["selectors"][0]["runs"].pop()),
        (
            "traced not passed",
            lambda r: r["beh"][0]["selectors"][0]["runs"][1].update(outcome="failed"),
        ),
        (
            "traced zero body lines",
            lambda r: r["beh"][0]["selectors"][0].update(product_lines=[]),
        ),
        (
            "traced module-level line",
            lambda r: r["beh"][0]["selectors"][0].update(
                product_lines=[{"file": "pkg/m.py", "line": 1}]
            ),
        ),
        (
            "traced subprocess-only",
            lambda r: r["beh"][0]["selectors"][0].update(
                subprocess=True, product_lines=[]
            ),
        ),
        ("traced with reason", lambda r: r["beh"][0].update(reason="no-test")),
        (
            "unconfirmed without reason",
            lambda r: r["beh"][0].update(status="unconfirmed"),
        ),
        (
            "selector without token",
            lambda r: r["beh"][0]["selectors"].__setitem__(0, sel("test_b")),
        ),
        ("incomplete definitions", lambda r: r["beh"][0].update(selectors=[])),
        ("lock mismatch", lambda r: r["environment"].update(lock_sha256="X")),
        ("content mismatch", lambda r: r.update(content_sha256="X")),
        ("error with beh", lambda r: r.update(error="collection")),
        ("not_applicable with beh", lambda r: r.update(not_applicable="language")),
        ("foreign bundle_pin", lambda r: r.update(bundle_pin="x" * 40)),
        ("foreign code", lambda r: r.update(code="XYZ")),
        ("no product_roots", lambda r: r.pop("product_roots")),
        ("no content_sha256", lambda r: r.pop("content_sha256")),
        (
            "error without reason",
            lambda r: r["beh"][0].update(status="error", selectors=[]),
        ),
    ],
)
def test_negative_table(name, fn):
    assert check(mutate(fn)) != [], name


PARAM_SRC = "@pytest.mark.parametrize('x', [1, 2])\ndef test_a(x):\n    # ENC:BEH-01\n    assert x\n"


def test_parametrized_selectors_share_one_definition():
    expected = ck.expected_definitions({"tests/t.py": PARAM_SRC}, "ENC", ["BEH-01"])
    assert expected == {"BEH-01": {("tests/t.py", "test_a")}}


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


@pytest.mark.parametrize(
    "roots", [["tests"], ["tests/unit"], ["/abs"], ["../x"], ["pkg/../tests"], []]
)
def test_product_roots_that_are_not_product_are_refused(roots):
    """Ревью среза 1, I4: корни — чужая цифра; тестовые, абсолютные, с `..` и
    пустые — отказ, иначе тело теста засчитывалось бы исполнением продукта."""
    assert check(mutate(lambda r: r.update(product_roots=roots))) != []


def test_traced_without_selectors_refused_even_when_no_test_expected():
    """Финальное ревью I-1: пустые селекторы == пустое ожидаемое не делают
    `traced` правдой — `traced` требует хотя бы одного селектора."""
    expected = ck.expected_definitions(
        {"tests/t.py": "def test_x():\n    assert 1\n"}, "ENC", ["BEH-01", "BEH-02"]
    )
    resp = mutate(lambda r: r["beh"][0].update(selectors=[]))
    problems = ck.validate_response(
        REQUEST,
        resp,
        expected=expected,
        function_lines={"pkg/m.py": {5, 6}},
        lock_sha="L",
        content_sha="C",
    )
    assert any("BEH-01" in p for p in problems)


@pytest.mark.parametrize("roots", [["."], [""], ["./"]])
def test_repo_root_as_product_root_refused(roots):
    """I-2: корень `.` включает тесты — их тела засчитывались бы продуктом."""
    assert check(mutate(lambda r: r.update(product_roots=roots))) != []


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


def test_deselected_null_definition_falls_back_to_node_id_file():
    """Минор (c): `definition: null` у deselected (§3.5) — владелец всё
    равно узнаётся по части node_id до `::`."""
    o = ck.owners({"tests/test_a.py": SRC}, "ENC", ["BEH-01"])
    dropped = {
        "how": "deselected",
        "node_id": "tests/test_a.py::test_a[2]",
        "definition": None,
    }
    resp = answer(
        [item("tests/test_a.py::test_a[1]")],
        [item("tests/test_a.py::test_a[1]")],
        [dropped],
    )
    assert "BEH-01" in ck.excluded_owner_behs(resp, o)


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
