"""criteria_tokens: граница токена и владение по AST (спека §1.3–1.4)."""

from __future__ import annotations

from governance import criteria_tokens as ct


def test_boundary():
    r = ct.token_re("ENC", "BEH-03")
    assert r.search("# ENC:BEH-03 x")
    for bad in ("XENC:BEH-03", "ENC:BEH-030", "ENC:BEH-03a", "ENC:BEH-03_"):
        assert not r.search(bad)


SRC = '''
"""ENC:BEH-09 module header does not count"""

def helper():
    # ENC:BEH-08 helper owns this, not a test
    return 1

def test_plain():
    # ENC:BEH-01
    assert helper() == 1

def test_outer():
    def inner():
        # ENC:BEH-07 nested helper owns this
        return 2
    assert inner() == 2

class TestGroup:
    """ENC:BEH-02 class-level token goes to every test method"""

    def test_red_neighbour(self):
        assert False

    def test_green(self):
        # ENC:BEH-03 only this method
        assert True

@decorator  # ENC:BEH-04 decorator line counts
def test_decorated():
    pass

@pytest.mark.parametrize("x", [1, 2])
def test_param(x):
    # ENC:BEH-05 one definition, every collected param selector
    assert x
'''


def test_ownership_table():
    got = ct.definition_tokens(SRC)
    assert got["test_plain"] == {"ENC:BEH-01"}
    assert got["test_outer"] == frozenset()
    assert got["TestGroup.test_red_neighbour"] == {"ENC:BEH-02"}
    assert got["TestGroup.test_green"] == {"ENC:BEH-02", "ENC:BEH-03"}
    assert got["test_decorated"] == {"ENC:BEH-04"}
    assert got["test_param"] == {"ENC:BEH-05"}
    assert "helper" not in got
    assert all("ENC:BEH-09" not in v for v in got.values())


# --- Паритет владения со spec-runner (devtools#491, D.1–D.7) ---------------


def test_form_feed_keeps_ast_line_numbering_d1():
    """`splitlines()` режет по \\x0c — строки расходились с нумерацией ast."""
    src = "# page\x0cbreak\ndef test_a(): pass  # ENC:BEH-01\n"
    assert ct.definition_tokens(src)["test_a"] == {"ENC:BEH-01"}


def test_redefined_class_drops_old_methods_d2():
    src = (
        "class TestA:\n    def test_old(self):\n        # ENC:BEH-01\n        pass\n"
        "class TestA:\n    def test_new(self):\n        pass\n"
    )
    assert set(ct.definition_tokens(src)) == {"TestA.test_new"}


def test_if_else_same_name_keeps_the_last_in_source_d2():
    src = (
        "import sys\nif sys.platform == 'x':\n    def test_a():\n        # ENC:BEH-01\n"
        "        pass\nelse:\n    def test_a():\n        # ENC:BEH-02\n        pass\n"
    )
    owned = ct.owned_definitions(src)
    assert [(d.qualname, d.line, d.tokens) for d in owned] == [
        ("test_a", 7, ("ENC:BEH-02",))
    ]


def test_test_under_module_compound_statement_is_indexed_d3():
    src = (
        "try:\n    import x  # ENC:BEH-09 compound owns nothing\nexcept ImportError:\n"
        "    pass\nif True:\n    def test_a():\n        # ENC:BEH-01\n        pass\n"
    )
    assert ct.definition_tokens(src) == {"test_a": frozenset({"ENC:BEH-01"})}


def test_outer_class_token_reaches_nested_class_methods_d4():
    src = (
        "class TestOuter:\n    # ENC:BEH-01\n    class TestInner:\n"
        "        # ENC:BEH-02\n        def test_c(self):\n            pass\n"
    )
    got = ct.definition_tokens(src)
    assert got["TestOuter.TestInner.test_c"] == {"ENC:BEH-01", "ENC:BEH-02"}


def test_utf8_bom_is_read_d5():
    src = "﻿def test_a():\n    # ENC:BEH-01\n    pass\n"
    assert ct.definition_tokens(src)["test_a"] == {"ENC:BEH-01"}


def test_test_method_of_non_test_class_is_not_a_test_d6():
    src = "class Helper:\n    def test_x(self):\n        # ENC:BEH-01\n        pass\n"
    assert ct.definition_tokens(src) == {}
    assert [d.qualname for d in ct.owned_definitions(src)] == ["Helper.test_x"]


def test_test_nested_in_test_function_is_not_a_definition_d7():
    src = (
        "def test_x():\n    def test_inner():\n        # ENC:BEH-01\n"
        "        pass\n    test_inner()\n"
    )
    assert ct.definition_tokens(src) == {"test_x": frozenset()}
    assert [d.qualname for d in ct.owned_definitions(src)] == ["test_x"]


def test_owned_definitions_shape_matches_shared_fixtures():
    """Форма §6.3 дизайна spec-runner: все функции и методы (не только test*),
    без классов; `line` — первый декоратор; токены отсортированы; порядок —
    (line, qualname)."""
    owned = ct.owned_definitions(SRC)
    assert [(d.qualname, d.line) for d in owned] == [
        ("helper", 4),
        ("test_plain", 8),
        ("test_outer", 12),
        ("TestGroup.test_red_neighbour", 21),
        ("TestGroup.test_green", 24),
        ("test_decorated", 28),
        ("test_param", 32),
    ]
    by_name = {d.qualname: d.tokens for d in owned}
    assert by_name["helper"] == ("ENC:BEH-08",)
    assert by_name["TestGroup.test_green"] == ("ENC:BEH-02", "ENC:BEH-03")


def test_syntax_error_and_nul_raise():
    for src in ("def test_a(:\n", "def test_a():\n    pass\x00\n"):
        try:
            ct.owned_definitions(src)
        except (SyntaxError, ValueError):
            continue
        raise AssertionError(f"не отказал: {src!r}")
