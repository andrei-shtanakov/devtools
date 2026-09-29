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
