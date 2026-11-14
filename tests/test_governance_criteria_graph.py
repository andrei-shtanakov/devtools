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
    assert (
        cgr.derive_ac(ac2, g2, {"BEH-01": "traced", "BEH-02": "unconfirmed"})
        == "unconfirmed"
    )
    assert (
        cgr.derive_ac(ac2, g2, {"BEH-01": "error", "BEH-02": "unconfirmed"}) == "error"
    )
    manual = cgr.Ac("AC-09", "manual", ("FR-01",), (), "Must")
    assert cgr.derive_ac(manual, g2, {}) == "human"


REQ_H = "#### FR-01: A\n**Priority**: Should\n#### FR-02: B\n**Priority**: Won't\n"
BEH_H = (
    "#### BEH-01: a\n`traces: [FR-01]`\n"
    "- **checked_by**: `status: planned` `kind: unit` `owner: qa`\n"
    "#### BEH-02: b\n`traces: [FR-01]`\n"
    "- **checked_by**: `status: planned` `kind: manual` `owner: qa`\n"
    "#### BEH-03: c\n`traces: [FR-01]`\n"
    "- **checked_by**: `status: waived` `kind: unit` `owner: qa`\n"
    "#### BEH-04: d\n`traces: [FR-02]`\n"
    "- **checked_by**: `status: planned` `kind: manual` `owner: qa`\n"
)
ACC_H = (
    "#### AC-01: a · verification: test\ntraces: [FR-01]\n"
    "scenarios: [BEH-01, BEH-02, BEH-03]\n"
    "#### AC-02: b · verification: manual\ntraces: [FR-02]\nscenarios: [BEH-04]\n"
)


def test_human_criteria_counts_graph_not_ac_status():
    """Ревью пары M1: AC-01 со статусом unconfirmed не прячет ручной BEH-02 и
    waived BEH-03; Won't (BEH-04, AC-02) не считается."""
    g = cgr.build_graph(REQ_H, BEH_H, ACC_H)
    assert cgr.derive_ac(g.acs["AC-01"], g, {"BEH-01": "unconfirmed"}) == "unconfirmed"
    assert cgr.human_criteria(g) == 2


REQ_S = "#### FR-01: A\n**Priority**: Must\n#### FR-02: B\n**Priority**: Should\n"
BEH_S = (
    "#### BEH-01: a\n`traces: [FR-01]`\n"
    "- **checked_by**: `status: planned` `kind: unit` `owner: qa`\n"
    "#### BEH-02: b\n`traces: [FR-02]`\n"
    "- **checked_by**: `status: planned` `kind: unit` `owner: qa`\n"
)
ACC_S = (
    "#### AC-01: a · verification: test\ntraces: [FR-01]\nscenarios: [BEH-01]\n"
    "#### AC-02: b · verification: test\ntraces: [FR-02]\nscenarios: [BEH-02]\n"
)


def test_should_test_ac_outside_threshold_is_automatic_and_non_blocking():
    """Правило DSL acceptance (dfl discovery v1.2, финальное ревью):
    Should-сценарии — в отдельном AC с `verification: test`, вне порога.
    Такой AC не человеческий критерий (закрытие без `human-merge-required`),
    а незелёный Should-сценарий — строка, не стоп закрытия. С `manual` тот же
    AC сделал бы закрытие человеческим."""
    from governance import criteria_check as ck

    g = cgr.build_graph(REQ_S, BEH_S, ACC_S)
    assert g.errors == [] and cgr.orphan_findings(g) == []
    assert g.acs["AC-02"].priority == "Should"
    assert cgr.human_criteria(g) == 0
    out = ck.outcome(g, {"BEH-01": "traced", "BEH-02": "unconfirmed"})
    assert out.closure == "traced"
    assert out.stop_reasons == []
    assert out.report_rows == ["BEH-02 (Should): unconfirmed"]
    manual = cgr.build_graph(
        REQ_S,
        BEH_S,
        ACC_S.replace("b · verification: test", "b · verification: manual"),
    )
    assert cgr.human_criteria(manual) == 1


REQ_D = REQ_S + "#### FR-03: C\n**Priority**: Won't\n"
BEH_D = BEH_S + (
    "#### BEH-03: c\n`traces: [FR-03]`\n"
    "- **checked_by**: `status: planned` `kind: unit` `owner: qa`\n"
)


def test_deferred_wont_scenario_needs_no_ac_and_should_ac_passes_coverage():
    """Ревью #763: сценарий отложенного (Won't) требования не сирота и без AC;
    отдельный Should-AC с трассой на FR (не OUT-…) проходит покрытие гейта."""
    g = cgr.build_graph(REQ_D, BEH_D, ACC_S)
    assert g.behs["BEH-03"].priority == "Won't"
    assert g.errors == [] and cgr.orphan_findings(g) == []
    assert ag.coverage_findings(REQ_D, BEH_D, ACC_S) == []
    out_traced = ACC_S.replace("traces: [FR-02]", "traces: [OUT-01]")
    assert any("OUT-01" in f for f in ag.coverage_findings(REQ_D, BEH_D, out_traced))
