"""Тесты `governance.ops` author-DSL контракта для узла design (спека §4)
и машинной грамматики Q в requirements.
"""


def test_design_author_dsl_contract():
    from governance.ops import _AUTHOR_DSL, _AUTHOR_FILENAMES

    assert _AUTHOR_FILENAMES["design"] == "20-design.md"
    dsl = _AUTHOR_DSL["design"]
    for needle in (
        "spec_stage: design",
        "owner_role: architects",
        "traces_to: [requirements, behaviour-spec]",
        "resolution: resolved|deferred",
        "reason:",
        "Открытых архитектурных вопросов нет (входной набор пуст)",
    ):
        assert needle in dsl, needle


def test_requirements_dsl_declares_q_grammar():
    from governance.ops import _AUTHOR_DSL

    dsl = _AUTHOR_DSL["requirements"]
    assert "Q-NN" in dsl and "owner_role" in dsl and "blocking" in dsl
