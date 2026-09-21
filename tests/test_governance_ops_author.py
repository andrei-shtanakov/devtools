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


def test_decomposition_dsl_declares_the_delivers_contract():
    """Срез 3 #282: авторинг обязан выпускать контракт, а не описывать его.

    До этого среза промпт о `delivers` молчал, поэтому каждый честно
    сгенерированный бандл выходил легаси — и барьер, поставленный срезом 1,
    стерёг вход, которого авторинг не умел произвести.
    """
    from governance.ops import _AUTHOR_DSL

    dsl = _AUTHOR_DSL["decomposition"]
    # Якоря НЕ голые имена полей: `dt_contract_version` и `delivers:`
    # встречаются в промпте и в пояснительной прозе, поэтому вхождение
    # голого имени не проваливается, когда поле выпало из ОБЯЗАТЕЛЬНОГО
    # перечня, — а именно перечень и делает контракт обязательным.
    # Мутанты «убрать версию из frontmatter» и «убрать delivers из
    # метаданных DT» на голых именах выживали оба.
    assert (
        "frontmatter (required): spec_stage: decomposition, "
        "dt_contract_version: 2, status: draft" in dsl
    ), dsl
    assert (
        "`delivers:` (REQUIRED under dt_contract_version: 2" in dsl
    ), dsl
    for needle in ("id: DEL-NN", "statement:", "sources:", "covered_by:"):
        assert needle in dsl, needle


def test_decomposition_dsl_takes_the_closed_kind_vocabulary_from_the_guard():
    """Словарь видов ВЫВОДИТСЯ из гварда, а не пересказывается промптом.

    Пересказ дрейфует молча: промпт продолжал бы предлагать вид, который
    гвард уже отверг, и автор получал бы отказ за то, что сделал ровно
    как написано.

    Честно о силе этой проверки: пока текст выведен, она тавтологична —
    обе стороны считаются из одной константы. Её работа начинается в тот
    момент, когда словарь МЕНЯЕТСЯ: подмени кто-нибудь вывод обратно на
    литералы, сегодня тест этого не заметит, но первая же правка
    `DELIVERABLE_KINDS` его уронит — то есть ровно тогда, когда
    расхождение впервые станет вредным.
    """
    from governance.decomposition_guard import DELIVERABLE_KINDS
    from governance.ops import _AUTHOR_DSL

    dsl = _AUTHOR_DSL["decomposition"]
    assert "|".join(DELIVERABLE_KINDS) in dsl, dsl
    for kind in DELIVERABLE_KINDS:
        assert f"`{kind}`" in dsl, kind
