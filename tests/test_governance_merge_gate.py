"""Табличные тесты merge_gate — «агент может» превращается в «агенту можно» (спека §9)."""

from __future__ import annotations

import pytest

from governance.merge_gate import Authority, MergeVerdict, PrFacts, Safety, decide

GOOD_FACTS = PrFacts(
    checks_rollup="green",
    mergeable="mergeable",
    behind_base=False,
    unresolved_threads=False,
    diff_class="document",
    touches_authority_root=False,
)
SAFE = Safety(agent_merge_allowed=True, actor_class="agent")
UNSAFE_TODAY = Safety(agent_merge_allowed=False, actor_class="agent")


def test_happy_path_agent() -> None:
    v = decide(Authority(), SAFE, 0, GOOD_FACTS)
    assert v == MergeVerdict("agent", v.reason)
    assert v.reason


def test_today_state_is_human_by_data() -> None:
    """Регрессия спеки §6: пока agent_merge_allowed=false — автономная ветка недостижима."""
    v = decide(Authority(), UNSAFE_TODAY, 0, GOOD_FACTS)
    assert v.decision == "human"
    assert "agent_merge_allowed" in v.reason


@pytest.mark.parametrize(
    "auth",
    [
        Authority(ecosystem="human"),
        Authority(repo="human"),
        Authority(run="human"),
    ],
)
def test_any_level_tightens_to_human(auth: Authority) -> None:
    assert decide(auth, SAFE, 0, GOOD_FACTS).decision == "human"


@pytest.mark.parametrize(
    "exit_code,expected",
    [
        (1, "human"),  # request-changes: находки -> человеку
        (2, "human"),  # прибор не отработал
        (3, "human"),
        (4, "human"),  # голова уехала
        (None, "human"),  # ревью не приходило вовсе = unknown
    ],
)
def test_review_gate(exit_code: int | None, expected: str) -> None:
    assert decide(Authority(), SAFE, exit_code, GOOD_FACTS).decision == expected


def test_refuse_on_red_gate() -> None:
    facts = GOOD_FACTS.__class__(**{**GOOD_FACTS.__dict__, "checks_rollup": "red"})
    v = decide(Authority(), SAFE, 0, facts)
    assert v.decision == "refuse"


@pytest.mark.parametrize(
    "field,value",
    [
        ("checks_rollup", "empty"),  # пустой rollup != прошли (спека §8)
        ("checks_rollup", "unknown"),
        ("mergeable", "unknown"),
        ("mergeable", "conflicting"),
        ("behind_base", True),
        ("unresolved_threads", True),  # седьмое предусловие
    ],
)
def test_fact_degradations_block_agent(field: str, value) -> None:
    facts = GOOD_FACTS.__class__(**{**GOOD_FACTS.__dict__, field: value})
    assert decide(Authority(), SAFE, 0, facts).decision == "human"


@pytest.mark.parametrize("diff_class", ["code", "research"])
def test_non_document_diff_forces_human(diff_class: str) -> None:
    """Предохранитель runner'а (спека §6): сам мержит только document-диффы."""
    facts = GOOD_FACTS.__class__(**{**GOOD_FACTS.__dict__, "diff_class": diff_class})
    assert decide(Authority(), SAFE, 0, facts).decision == "human"


def test_authority_root_always_human() -> None:
    facts = GOOD_FACTS.__class__(
        **{**GOOD_FACTS.__dict__, "touches_authority_root": True}
    )
    v = decide(Authority(), SAFE, 0, facts)
    assert v.decision == "human"
    assert "authority-root" in v.reason


@pytest.mark.parametrize(
    "safety",
    [
        Safety(agent_merge_allowed=None, actor_class="agent"),  # копия недоступна
        Safety(agent_merge_allowed=True, actor_class="unknown"),  # актор не в списках
    ],
)
def test_safety_unknown_is_fail_closed(safety: Safety) -> None:
    assert decide(Authority(), safety, 0, GOOD_FACTS).decision == "human"


def test_reason_is_always_present() -> None:
    for v in (
        decide(Authority(), SAFE, 0, GOOD_FACTS),
        decide(Authority(run="human"), SAFE, 0, GOOD_FACTS),
        decide(Authority(), UNSAFE_TODAY, 1, GOOD_FACTS),
    ):
        assert v.reason.strip()
