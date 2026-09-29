"""criteria_graph: словарь приоритетов, приоритет по трассам, сироты, вывод AC."""
from __future__ import annotations

from governance import acceptance_guard as ag
from governance import criteria_graph as cgr

REQ = """#### FR-01: A
**Priority**: Must

#### FR-02: B
**Priority**: Could

#### FR-03: C
**Priority**: Won't
"""
BEH = """#### BEH-01: one
`traces: [FR-01]`
- **checked_by**: `status: planned` `kind: unit` `owner: qa` `target: tests/t.py`

#### BEH-02: two
`traces: [FR-02, FR-01]`
- **checked_by**: `status: planned` `kind: manual` `owner: qa` `target: doc`

#### BEH-03: three
`traces: [FR-03]`
- **checked_by**: `status: waived` `kind: unit` `owner: qa` `target: tests/t.py`

#### BEH-04: four
`traces: [FR-01]`
- **checked_by**: `status: planned` `kind: unit` `owner: qa` `target: tests/t.py`

#### BEH-05: five
`traces: []`
- **checked_by**: `status: planned` `kind: unit` `owner: qa` `target: tests/t.py`

#### BEH-06: six
`traces: [FR-01]`
- **checked_by**: `status: planned` `kind: unit` `owner: qa` `target: tests/t.py`
"""
ACC = """#### AC-01: a · verification: test
traces: [FR-01]
scenarios: [BEH-01, BEH-02]

#### AC-02: b · verification: test
traces: [FR-03]
scenarios: [BEH-04]
"""


def test_could_and_wont_are_in_the_vocabulary():
    prio, findings = ag._parse_requirements(REQ)
    assert prio == {"FR-01": "Must", "FR-02": "Could", "FR-03": "Won't"}
    assert findings == []


def test_priority_is_max_of_traces_and_empty_trace_is_error():
    g = cgr.build_graph(REQ, BEH, ACC)
    assert g.behs["BEH-02"].priority == "Must"
    assert g.behs["BEH-03"].priority == "Won't"
    assert g.behs["BEH-05"].priority is None
    assert any("BEH-05" in e for e in g.errors)


def test_waived_wins_over_executable_kind():
    g = cgr.build_graph(REQ, BEH, ACC)
    assert g.behs["BEH-03"].waived
    assert [b.id for b in cgr.test_behs(g)] == ["BEH-01", "BEH-04", "BEH-05", "BEH-06"]


def test_orphans_both_forms():
    g = cgr.build_graph(REQ, BEH, ACC)
    orphans = cgr.orphan_findings(g)
    # форма 1: Must-BEH-04 только в Won't-AC (AC-02 трассирует FR-03) → сирота
    assert g.behs["BEH-04"].priority == "Must"
    assert any("BEH-04" in f for f in orphans)
    # форма 2: BEH-06 вне всех AC → сирота
    assert any("BEH-06" in f for f in orphans)
    # BEH-03 сам Won't → не сирота
    assert not any("BEH-03" in f for f in orphans)
    assert not any("BEH-01" in f for f in orphans)


def test_derive_ac_table():
    g = cgr.build_graph(REQ, BEH, ACC)
    ac = g.acs["AC-01"]
    assert cgr.derive_ac(ac, g, {"BEH-01": "traced"}) == "human"  # BEH-02 manual
    g2 = cgr.build_graph(REQ, BEH.replace("`kind: manual`", "`kind: unit`"), ACC)
    ac2 = g2.acs["AC-01"]
    assert cgr.derive_ac(ac2, g2, {"BEH-01": "traced", "BEH-02": "traced"}) == "traced"
    assert cgr.derive_ac(ac2, g2, {"BEH-01": "traced", "BEH-02": "unconfirmed"}) == "unconfirmed"
    assert cgr.derive_ac(ac2, g2, {"BEH-01": "error", "BEH-02": "unconfirmed"}) == "error"
    manual = cgr.Ac("AC-09", "manual", ("FR-01",), (), "Must")
    assert cgr.derive_ac(manual, g2, {}) == "human"
