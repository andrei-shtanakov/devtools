# Оракул бандла, срез 2a — подпись и штамп — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Закрытие `traced` перестаёт быть приёмкой само по себе: предложение со снимком политики, мерж PR предложения (человеком из снимка, если есть человеческие критерии), предикат §3.5 по фактам форджи, агентский stamp-PR `status: accepted`; гейт `[x]` требует штампа.

**Architecture:** Гейт `[x]` проверяет происхождение штампа модулем `governance/acceptance_provenance.py` (факты форджи и истории git, политика из доверенного источника по пину, граф по пину). Чистые решения — новый модуль `governance/criteria_accept.py` (тексты предложения и штампа, предикат по собранным фактам, проверка штампа) и `criteria_graph.human_criteria`. Приёмку ведёт **одна функция `_advance` по фактам форджи** в `governance/criteria_close.py`: каждый вызов читает состояние PR и делает следующий шаг; локальное состояние — только write-ahead намерений (собственная голова коммита, номер PR, факт аттестации) и записанные исходы. Гейт — `governance/closure_gate.py`. Ops получает одно поле (`state`) в `prs_by_head_prefix`.

**Tech Stack:** Python 3.12+, pytest, настоящий git во временных каталогах (тестовая «форджа»), uv.

**Spec:** `docs/superpowers/specs/2026-09-28-bundle-criteria-oracle-design.md` — §7.2a (объём этого плана, authority), §3.2 (проверка штампа, `FORBIDDEN env/source` в любой фазе), §3.4–3.5 (предложение, предикат, снимок), §7.1 (гейт).

**Ревью пары, круг 1** (`scratchpad/s2a-review-r1.md`, свежий ревьюер Claude: Codex заблокирован классификатором разрешений, решение владельца 2026-10-02): B1 (resume по локальному флагу `merged`, три формы) → механизм заменён — `_advance` по фактам форджи; B2/M2 (голова из `ls-remote`, не своя) → голова — собственный коммит, пишется до создания PR, усыновление только той же формы; M1 (флаг из `human_pending`) → `human_criteria` по графу; M3 (ломаются существующие тесты) → Task 3 только фикстура и явный перечень адаптаций до кода продукта; M4 (гейт доверяет тексту) → названная граница в спеке + проверка полей штампа, происхождение в CI — 2b; m1–m5 — учтены по месту.

**Вычитка владельцем 2026-10-02** (#544): проверка происхождения — в 2a (Task 7 переписана: гейт проверяет акт, связанный с закрытием; Task 9 — CI-проводка через `.github/`, мерж человеком); обход старым форматом убран (файл среза 1 без `status` зелёный только при графе на пине без человеческих критериев; непрочитанный граф — красный); миграция незавершённого закрытия среза 1 — отдельная ветка предложения, обработка прежнего PR, сквозной тест без перемера (Tasks 4–6).

**Круги 4–5 и решение по authority-root** (`s2a-review-r4.md`, `r5.md`): R4-B1 (пин из файла агента прятал ручные критерии) → правило «пин — текущий бандл» в обеих ветках `traced`; R5-M1 (перевёрнутая семантика compare) → `ahead`/`identical` = в истории ref + unit-тест; владелец 2026-10-02 — гейт и модули его правила в authority-root (Task 7b), гейт развязан от `ops.py`, **PR кода мержит человек**.

**Круг 7 — перебор форм** (`s2a-review-r7.md`; вторая находка класса «незащищённый код в процессе гейта» → стоп, механизм вместо экземпляра). Класс A (код в процессе гейта): A1 `governance.*` — инвариант; A2 модуль в корне репо затеняет stdlib/`yaml` через `sys.path[0]` (M7-1) — запуск `python -I` с корнем репо **в конце** `sys.path`; A3 зависимости репо (`pyproject.toml`/`uv.lock`, их `.pth`) — гейт в изолированном окружении: stdlib + `pyyaml==6.0.3`, версии в защищённом workflow; A4 env/`sitecustomize` — `-I` + защищённый workflow; A5 ленивый импорт (m7-4) — AST-скан. Инвариант 7b — той же формой запуска: модули репо в процессе гейта ⊆ authority-root, сторонние ⊆ {`yaml`}. Класс B (путь к `[x]` без подписи): n/a, `status`, устаревший пин, понижение/удаление/переименование charter'а (`deletion_findings`), два charter'а, переименование `@id`, битый frontmatter — закрыты; перепривязка `plan_item` (M7-2) — `plan_item` неизменяем у charter'а схемы 2 против базы, как `code` (Task 7, `charter_guard`).

**Круг 6** (`s2a-review-r6.md`, 0/2 major): M6-1 (гейт тянул `ops`/`facts` через `approval_facts` — незащищённый код в процессе гейта) → `policy_rule.py`, `governance/__init__.py` в перечне, инвариант «замыкание импорта ⊆ authority-root»; M6-2 (n/a — путь к `[x]` без подписи, исход решал незащищённый `criteria_contract`) → словарь причин закрыт, `spec-runner-version`/`schema-1` всегда красные, `language` — сверка графа по пину, `oracle_released` снят с гейта; m6-2 CRLF, m6-1/m6-5 — по месту.

**Круг 3** (`scratchpad/s2a-review-r3.md`): `VERDICT: converged`; minor R3-m1 (унаследованный `pr` среза 1) исправлен без нового круга — `_propose` сбрасывает `pr`/`attested`/`closed`; граница пути к штампу для красного файла среза 1 названа в §7.2a п.6.

**Круг 2** (`scratchpad/s2a-review-r2.md`, 0 blocker / 3 major): B-M1 (флаг human из текста, fail-open без поля) → флаг передаётся в `_propose` из графа узлов на пине; B-M2 (после Task 6 падает `test_new_measurement_closes_stale_pr`) → адаптация названа в Task 6; B-M3 (отсутствие файла/узла свёрнуто в «не установлено», прогон запирается) → `_path_fact` различает «нет» и «не прочитано», голова сверяется первой; minor: живая приёмка Step 3 (выход 2, не 4, на polygon; теперь Task 10), `_close_stale` пропускает ключи с исходом, исключения `find_pr`/`candidate_template` → отказ шага, «Update branch» названа границей и предупреждением в PR, граница гейта для файлов среза 1 и «нет исполнителя» в 2a — в спеке.

## Global Constraints

- Флага обхода стопа и приёмки нет и не добавлять (`--skip-criteria` и аналоги запрещены спекой §3.3).
- Акт подписи — человек; агент обрабатывает подпись и выпускает штамп. PR с человеческими критериями создаётся с метками `criteria-close,human-merge-required` **тем же вызовом** `create_pr` (окна без метки нет).
- Агентский мерж — только `Ops.merge` (→ `merge-pr.sh`) и только по **записанной собственной голове**; прямых `gh pr merge`/merge-API в коде нет.
- Предикат — одна функция `criteria_accept.predicate`; приёмка — одна функция `_advance` на всех путях (первый проход, resume, PR, влитый кем угодно).
- Неустановленный факт (`UNAVAILABLE`, `None`, rc ≠ 0, незаписанная голова) — отказ шага без перехода, **никогда** `rejected`/`superseded`.
- Ключ с предложением никогда не публикуется повторно путём среза 1 (`_publish`).
- Коды выхода `criteria-close`: 0 — принято / опубликовано (`blocked`, `not-applicable`); 2 — отказ шага или команды; 4 — ждёт мержа человеком; 5 — приёмка терминально отклонена/устарела на этом ключе; 6 — ключ измерен (в т.ч. уже принят).
- Штамп = текст предложения, где `status: proposed → accepted` и добавлены `accepted_merge`, `accepted_pr`; больше ничего.
- Соседние репо read-only; `_cowork_output` из кода не читать; uv, не pip; реальные `gh`/`claude`/`codex` в тестах не вызывать.
- Line length 88, ruff + pyrefly чисто; полный набор: `GOVERNANCE_REQUIRED=1 uv run --frozen --group governance pytest -q -p no:cacheprovider`.

## Review Focus

1. Человек смержил PR предложения, верхушка default ушла вперёд мерж-коммитом, чекаут оператора на старом `product_sha` → resume приёмки не требует чекаута и не перемеряет → `test_resume_after_human_merge_needs_no_checkout` (Task 5).
2. PR предложения закрыт без мержа — на **любом** пути — → `rejected (closed)`, повтор того же ключа — выход 5, нового PR нет → `test_closed_proposal_is_rejected_on_both_paths` (Task 5).
3. Транзиентный сбой (ревью, `gh pr view`, fetch) на любом шаге → отказ шага 2, исхода нет, повтор дожимает; подпись человека не хоронится → `test_review_failure_on_human_path_is_retried_not_buried`, `test_unreadable_pr_facts_is_step_refusal_then_resume` (Task 5).
4. Stamp-PR смержен, но в default файл не равен штампу или удалён → новый stamp-PR, не `accepted` → `test_tampered_stamp_reissues_stamp_pr`, `test_deleted_closure_reissues_stamp_pr` (Task 6).
5. Повтор после `accepted` на человеческом пути → выход 6, никакой публикации (не откат `accepted → proposed`) → `test_rerun_after_human_acceptance_publishes_nothing` (Task 6).

## Исполнение

- Отдельный worktree от `origin/master`, ветка `feat/bundle-oracle-slice2a` (не основной чекаут `devtools/` — параллельные сессии).
- Исполнение (решение владельца): **через субагентов** — свежий исполнитель и ревьюер на каждую задачу; Tasks 4–6 строго последовательно; после Task 8 — общее интеграционное ревью всей ветки (самая сильная модель).
- Зависимости: 2 после 1; 3 независима (только тесты); 4 после 1 и 3; 5 после 2 и 4; 6 после 5; 7 после 1; 7b после 7; 8 после 6 и 7b; 9 после мержа PR кода; 10 — после мержа Task 9 (живая приёмка, акты владельца).
- **Два PR, оба мержит человек.** Код (Tasks 1–8, 7b) правит authority-root (`contracts/authority-root/`, `governance/` модули правила гейта — Task 7b) — `merge-pr.sh` отказывает категорически, **мерж человеком** (CI devtools зелёный и без токена: в devtools нет charter'ов схемы 2 с `[x]`, проверка происхождения там не срабатывает). Task 9 правит `.github/workflows/ci.yml` — отдельный PR с меткой `human-merge-required`, **мерж человеком**.

---

### Task 1: `criteria_accept` (тексты, предикат, проверка штампа) и `human_criteria` по графу

**Files:**
- Create: `governance/criteria_accept.py`
- Modify: `governance/criteria_graph.py` (функция `human_criteria`)
- Test: `tests/test_governance_criteria_accept.py`, `tests/test_governance_criteria_graph.py`

**Interfaces:**
- Consumes: `governance.frontmatter.split_frontmatter`, `join_frontmatter` (круговой проход точный, порядок ключей сохраняется — сверено на polygon `oracle-positive`).
- Produces:
  - константы `STATUS_PROPOSED = "proposed"`, `STATUS_ACCEPTED = "accepted"`, `TERMINAL = ("rejected", "superseded")`, `EXIT_WAITING_HUMAN = 4`, `EXIT_TERMINAL = 5`, `LABEL = "criteria-close"`, `HUMAN_LABELS = "criteria-close,human-merge-required"`;
  - `text_sha256(text: str) -> str`;
  - `proposal_text(closure_text: str, policy_source: str) -> str`;
  - `stamp_text(proposal: str, *, merge_oid: str, pr: int) -> str` (ValueError, если `status` ≠ `proposed`);
  - `@dataclass(frozen=True) class Proposal: head: str | None; base: str; text_sha256: str; human: bool; accounts: frozenset[str]` (`head is None` — голова не записана → `unavailable`, никогда не сравнение с пустой строкой);
  - `@dataclass(frozen=True) class AcceptFacts: state: str | None; head: str | None; base: str | None; merged_by: str | None; merge_oid: str | None; merged_blob_sha256: str | None; nodes_fresh: bool | None; approval_pr_open: bool | None; product_on_tip: bool | None; agent_login: str | None`;
  - `@dataclass(frozen=True) class Verdict: kind: str; reason: str` — `kind` ∈ `waiting | stamping | rejected | superseded | unavailable`;
  - `predicate(p: Proposal, f: AcceptFacts) -> Verdict`;
  - `check_stamp(expected: str, actual: str | None) -> str` — `valid | invalid | unavailable` (`""` — файла нет, установлено → `invalid`; `None` — не прочитано → `unavailable`);
  - `criteria_graph.human_criteria(graph: Graph) -> int` — не-Won't BEH с `waived` или неисполняемым `kind` плюс не-Won't AC с `verification ≠ test` (спека §7.2a п.1: по графу, не по производному статусу AC).

- [ ] **Step 1: Write the failing tests**

```python
"""criteria_accept — чистые решения среза 2a (спека §7.2a, §3.2, §3.5)."""

from dataclasses import replace

import pytest

from governance import criteria_accept as ca
from governance.frontmatter import split_frontmatter

CLOSURE = (
    "---\nworkstream: ws\ncode: ENC\nproduct_sha: abc\nclosure: traced\n"
    "human_pending: 0\n---\n# Закрытие воркстрима\n\nтело\n"
)
SOURCE = "github:o/approval-policy@" + "a" * 40 + ":policy/approvers.env"


def _p(human=False, accounts=("owner",)):
    return ca.Proposal(
        head="h1",
        base="master",
        text_sha256="s1",
        human=human,
        accounts=frozenset(accounts),
    )


def _f(**over):
    base = ca.AcceptFacts(
        state="MERGED",
        head="h1",
        base="master",
        merged_by="ai-prosto",
        merge_oid="m1",
        merged_blob_sha256="s1",
        nodes_fresh=True,
        approval_pr_open=False,
        product_on_tip=True,
        agent_login="ai-prosto",
    )
    return replace(base, **over)


def test_proposal_text_adds_status_and_source_keeps_body():
    text = ca.proposal_text(CLOSURE, SOURCE)
    meta, body = split_frontmatter(text)
    assert meta["status"] == "proposed" and meta["policy_source"] == SOURCE
    assert body == split_frontmatter(CLOSURE)[1]
    assert list(meta)[:2] == ["workstream", "code"]


def test_stamp_differs_from_proposal_only_by_three_fields():
    prop = ca.proposal_text(CLOSURE, SOURCE)
    stamp = ca.stamp_text(prop, merge_oid="m1", pr=7)
    pm, pb = split_frontmatter(prop)
    sm, sb = split_frontmatter(stamp)
    assert pb == sb
    assert sm["status"] == "accepted"
    assert (sm["accepted_merge"], sm["accepted_pr"]) == ("m1", 7)
    assert {k: v for k, v in sm.items() if k not in ("status", "accepted_merge", "accepted_pr")} == {
        k: v for k, v in pm.items() if k != "status"
    }


def test_stamp_refuses_non_proposal():
    with pytest.raises(ValueError):
        ca.stamp_text(CLOSURE, merge_oid="m1", pr=7)


def test_test_only_merged_by_agent_is_stamping():
    assert ca.predicate(_p(), _f()).kind == "stamping"


def test_test_only_merged_by_snapshot_account_is_stamping():
    assert ca.predicate(_p(), _f(merged_by="owner")).kind == "stamping"


def test_human_merged_by_snapshot_account_is_stamping():
    assert ca.predicate(_p(human=True), _f(merged_by="owner")).kind == "stamping"


@pytest.mark.parametrize("login", ["ai-prosto", "stranger"])
def test_human_merged_by_agent_or_outsider_is_rejected(login):
    """§8.4 п.5: человеческий критерий, мерж не человеком из снимка."""
    v = ca.predicate(_p(human=True), _f(merged_by=login))
    assert v.kind == "rejected" and "act" in v.reason


def test_human_with_agent_listed_in_policy_still_rejected():
    v = ca.predicate(_p(human=True, accounts=("owner", "ai-prosto")), _f())
    assert v.kind == "rejected"


def test_test_only_outsider_rejected():
    assert ca.predicate(_p(), _f(merged_by="stranger")).kind == "rejected"


@pytest.mark.parametrize(
    ("over", "kind", "word"),
    [
        ({"state": "OPEN"}, "waiting", "open"),
        ({"state": "CLOSED"}, "rejected", "closed"),
        ({"head": "h2"}, "rejected", "head-moved"),
        ({"base": "main"}, "rejected", "base"),
        ({"merged_blob_sha256": "s2"}, "rejected", "blob"),
        ({"nodes_fresh": False}, "superseded", "bundle"),
        ({"approval_pr_open": True}, "superseded", "approval"),
        ({"product_on_tip": False}, "superseded", "product"),
    ],
)
def test_predicate_table(over, kind, word):
    v = ca.predicate(_p(), _f(**over))
    assert v.kind == kind and word in v.reason


@pytest.mark.parametrize(
    "over",
    [
        {"state": None},
        {"state": "WEIRD"},
        {"head": None},
        {"merged_blob_sha256": None},
        {"nodes_fresh": None},
        {"approval_pr_open": None},
        {"product_on_tip": None},
        {"merged_by": None},
        {"merge_oid": None},
    ],
)
def test_unestablished_fact_is_unavailable_never_terminal(over):
    assert ca.predicate(_p(), _f(**over)).kind == "unavailable"


def test_human_needs_agent_login_to_exclude_it():
    v = ca.predicate(_p(human=True), _f(merged_by="owner", agent_login=None))
    assert v.kind == "unavailable"


def test_test_only_outsider_with_unknown_agent_is_unavailable():
    """Не узнали учётку агента — «чужой» не установлен положительно."""
    v = ca.predicate(_p(), _f(merged_by="stranger", agent_login=None))
    assert v.kind == "unavailable"


def test_check_stamp():
    assert ca.check_stamp("x", "x") == "valid"
    assert ca.check_stamp("x", "y") == "invalid"
    assert ca.check_stamp("x", "") == "invalid"  # файла в default нет
    assert ca.check_stamp("x", None) == "unavailable"


def test_foreign_head_wins_over_unread_blob():
    """Ревью круга 2 B-M3: голова установленно чужая — rejected, не unavailable."""
    v = ca.predicate(_p(), _f(head="h2", merged_blob_sha256=None))
    assert v.kind == "rejected" and "head-moved" in v.reason


def test_absent_closure_in_merge_commit_is_rejected():
    v = ca.predicate(_p(), _f(merged_blob_sha256=""))
    assert v.kind == "rejected" and "blob" in v.reason


def test_unrecorded_head_is_unavailable_not_head_moved():
    """Ревью пары B2: незаписанная голова — не «head-moved»."""
    p = replace(_p(), head=None)
    assert ca.predicate(p, _f()).kind == "unavailable"
```

В `tests/test_governance_criteria_graph.py`:

```python
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
```

(В этом файле модуль графа импортирован как `cgr`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run -q --frozen --group governance pytest tests/test_governance_criteria_accept.py tests/test_governance_criteria_graph.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'criteria_accept'` и `AttributeError: … 'human_criteria'`.

- [ ] **Step 3: Write the implementation**

```python
"""criteria_accept — приёмка закрытия `traced`, срез 2a (спека §7.2a).

Чистые решения без побочных эффектов: текст предложения и штампа (§3.4),
предикат перехода в `stamping` по собранным фактам форджи (§3.5) и проверка
штампа (§3.2). Факты собирает и действия выполняет `criteria_close`.

Правило фактов (как в `approval_facts`): в `rejected`/`superseded` переводит
только положительно установленный факт; `None` — «не установлено» и даёт
`unavailable` (отказ шага без перехода), а не терминал.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from governance.frontmatter import join_frontmatter, split_frontmatter

STATUS_PROPOSED = "proposed"
STATUS_ACCEPTED = "accepted"
TERMINAL = ("rejected", "superseded")
EXIT_WAITING_HUMAN = 4
EXIT_TERMINAL = 5
LABEL = "criteria-close"
HUMAN_LABELS = "criteria-close,human-merge-required"


def text_sha256(text: str) -> str:
    """sha256 текста файла (UTF-8) — идентичность предложения и штампа."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def proposal_text(closure_text: str, policy_source: str) -> str:
    """Файл закрытия + `status: proposed` и источник снимка политики (§7.2a п.1)."""
    meta, body = split_frontmatter(closure_text)
    return join_frontmatter(
        {**meta, "status": STATUS_PROPOSED, "policy_source": policy_source}, body
    )


def stamp_text(proposal: str, *, merge_oid: str, pr: int) -> str:
    """Штамп: ровно предложение со `status: accepted` и ссылкой на мерж (§3.2)."""
    meta, body = split_frontmatter(proposal)
    if meta.get("status") != STATUS_PROPOSED:
        raise ValueError(f"штамп выпускается из предложения, а не из {meta.get('status')!r}")
    return join_frontmatter(
        {
            **meta,
            "status": STATUS_ACCEPTED,
            "accepted_merge": merge_oid,
            "accepted_pr": pr,
        },
        body,
    )


@dataclass(frozen=True)
class Proposal:
    """Записанное предложение: что именно и по какому снимку подписывается."""

    head: str | None
    base: str
    text_sha256: str
    human: bool
    accounts: frozenset[str]


@dataclass(frozen=True)
class AcceptFacts:
    """Факты форджи и git для предиката; `None` — факт не установлен."""

    state: str | None
    head: str | None
    base: str | None
    merged_by: str | None
    merge_oid: str | None
    merged_blob_sha256: str | None
    nodes_fresh: bool | None
    approval_pr_open: bool | None
    product_on_tip: bool | None
    agent_login: str | None


@dataclass(frozen=True)
class Verdict:
    """`waiting | stamping | rejected | superseded | unavailable` + причина."""

    kind: str
    reason: str = ""


def _unavailable(what: str) -> Verdict:
    return Verdict("unavailable", f"не установлено: {what}")


def predicate(p: Proposal, f: AcceptFacts) -> Verdict:
    """Предикат §3.5: идентичность (1), актуальность оракула (2), акт (3)."""
    if f.state == "OPEN":
        return Verdict("waiting", "PR предложения open — мержа ещё не было")
    if f.state == "CLOSED":
        return Verdict("rejected", "closed — PR предложения закрыт без мержа")
    if f.state != "MERGED":
        return _unavailable(f"состояние PR ({f.state!r})")
    identity = _identity(p, f)
    if identity is not None:
        return identity
    fresh = _freshness(f)
    if fresh is not None:
        return fresh
    return _act(p, f)


def _identity(p: Proposal, f: AcceptFacts) -> Verdict | None:
    """`merged_blob_sha256 == ""` — файла в мерж-коммите нет (установлено)."""
    if p.head is None:
        return _unavailable("голова предложения не записана")
    if f.head is not None and f.head != p.head:  # голова первой (ревью круга 2)
        return Verdict("rejected", f"head-moved: смержена {f.head}, предложена {p.head}")
    if f.head is None or f.base is None or f.merged_blob_sha256 is None:
        return _unavailable("голова/база PR или файл в мерж-коммите")
    if f.base != p.base:
        return Verdict("rejected", f"base: PR в {f.base}, предложение в {p.base}")
    if f.merged_blob_sha256 != p.text_sha256:
        return Verdict("rejected", "blob: файл в мерж-коммите ≠ предложенному")
    return None


def _freshness(f: AcceptFacts) -> Verdict | None:
    if f.nodes_fresh is None or f.approval_pr_open is None or f.product_on_tip is None:
        return _unavailable("актуальность оракула (узлы, PR одобрения, product_sha)")
    if not f.nodes_fresh:
        return Verdict("superseded", "bundle: узлы на верхушке ≠ байтам bundle_pin")
    if f.approval_pr_open:
        return Verdict("superseded", "approval: открыт PR ветки одобрения воркстрима")
    if not f.product_on_tip:
        return Verdict("superseded", "product: product_sha не предок верхушки")
    return None


def _act(p: Proposal, f: AcceptFacts) -> Verdict:
    if f.merged_by is None or f.merge_oid is None:
        return _unavailable("акт мержа (mergedBy/mergeCommit)")
    if p.human:
        if f.agent_login is None:
            return _unavailable("учётка агента (исключить её из подписантов)")
        if f.merged_by == f.agent_login or f.merged_by not in p.accounts:
            return Verdict(
                "rejected", f"act: {f.merged_by} — не человек из снимка политики"
            )
        return Verdict("stamping")
    if f.merged_by in p.accounts or f.merged_by == f.agent_login:
        return Verdict("stamping")
    if f.agent_login is None:
        return _unavailable("учётка агента (мержер вне снимка)")
    return Verdict("rejected", f"act: {f.merged_by} — ни агент, ни учётка снимка")


def check_stamp(expected: str, actual: str | None) -> str:
    """Проверка штампа §3.2: файл в default ровно ожидаемый штамп."""
    if actual is None:
        return "unavailable"
    return "valid" if actual == expected else "invalid"
```

В `governance/criteria_graph.py` после `test_behs`:

```python
def human_criteria(graph: Graph) -> int:
    """Критерии, которые подписывает человек (спека §7.2a п.1): не-Won't BEH с
    `waived` или неисполняемым `kind` и не-Won't AC с `verification ≠ test`.
    Считается по графу, а не по производному статусу AC: `unconfirmed` в AC
    стоит раньше `human` (`derive_ac`) и спрятал бы ручной критерий."""
    behs = sum(
        1
        for b in graph.behs.values()
        if b.priority != "Won't" and (b.waived or b.kind not in EXEC_KINDS)
    )
    acs = sum(
        1
        for a in graph.acs.values()
        if a.priority != "Won't" and a.verification != "test"
    )
    return behs + acs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: команда Step 2.
Expected: PASS (все). Затем `uv run -q --frozen --group=selfcheck ruff format governance/criteria_accept.py tests/test_governance_criteria_accept.py && uv run -q --frozen --group=selfcheck ruff check governance tests && uv run -q --frozen --group selfcheck pyrefly check governance/criteria_accept.py` — чисто.

- [ ] **Step 5: Commit**

```bash
git add governance/criteria_accept.py governance/criteria_graph.py tests/test_governance_criteria_accept.py tests/test_governance_criteria_graph.py
git commit -m "criteria_accept: предложение, предикат §3.5, проверка штампа; human_criteria по графу"
```

---

### Task 2: `prs_by_head_prefix` несёт `state`; факт «открыт PR ветки одобрения»

**Files:**
- Modify: `governance/ops.py` (`RealOps.prs_by_head_prefix`: запрос GraphQL и возвращаемый словарь)
- Modify: `governance/criteria_close.py` (новая функция `_approval_pr_open`)
- Test: `tests/test_governance_ops.py`, `tests/test_governance_criteria_close.py`

**Interfaces:**
- Consumes: `approval_branches.candidate_template()` → `"spec/{ws_id}-approve-{wave}-{step}-{attempt}"` (finalize = тот же префикс + `-final`).
- Produces: элементы `prs_by_head_prefix` получают ключ `"state"` (`OPEN|CLOSED|MERGED` как от GitHub); `criteria_close._approval_pr_open(ops, state) -> bool | None` (None — факт не установлен).

- [ ] **Step 1: Write the failing tests**

В `tests/test_governance_ops.py`, в `test_prs_by_head_prefix_paginates_all_states_and_filters`: добавить `"state": "OPEN"` в узел #41 и `"state": "MERGED"` в узел #12, и в конец теста:

```python
    assert [item["state"] for item in result] == ["OPEN", "MERGED"]
    query = next(a for a in argv if a.startswith("query="))
    assert "headRefName state" in query
```

В `tests/test_governance_criteria_close.py`:

```python
@pytest.mark.parametrize(
    ("prs", "expected"),
    [
        ([], False),
        ([{"state": "MERGED"}, {"state": "CLOSED"}], False),
        ([{"state": "MERGED"}, {"state": "OPEN"}], True),
        ([{"state": None}], None),
    ],
)
def test_approval_pr_open(prs, expected):
    class O:
        def prs_by_head_prefix(self, slug, prefix):
            assert prefix == "spec/ws-approve-"
            return prs

    class S:
        ws_id = "ws"
        repo_slug = "o/r"

    assert cc._approval_pr_open(O(), S()) is expected


def test_approval_pr_open_unavailable_on_error():
    class O:
        def prs_by_head_prefix(self, slug, prefix):
            raise RuntimeError("gh api rc=1")

    class S:
        ws_id = "ws"
        repo_slug = "o/r"

    assert cc._approval_pr_open(O(), S()) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run -q --frozen --group governance pytest tests/test_governance_ops.py tests/test_governance_criteria_close.py -q -p no:cacheprovider -k "prs_by_head_prefix or approval_pr_open"`
Expected: FAIL — `KeyError: 'state'` и `AttributeError: ... '_approval_pr_open'`.

- [ ] **Step 3: Implement**

В `governance/ops.py`, `RealOps.prs_by_head_prefix`: в запросе `"nodes{number title body headRefName}"` → `"nodes{number title body headRefName state}"`; в `found.append({...})` добавить `"state": item.get("state"),`. В docstring добавить строку: «`state` — как отдаёт GitHub (`OPEN|CLOSED|MERGED`); интерпретирует вызывающий».

В `governance/criteria_close.py` — импорт `approval_branches` в общий `from governance import (...)` и функция в секцию публикации:

```python
def _approval_pr_open(ops: Ops, state) -> bool | None:
    """Открыт ли PR ветки одобрения воркстрима (§3.5 п.2); None — не установлено.

    Префикс — из SSOT шаблона ветки одобрения (`approval_branches`), общий у
    candidate и finalize; незнакомое состояние PR — не «закрыт», а None."""
    try:
        template = approval_branches.candidate_template()
        prefix = template[: template.index("{wave}")].replace("{ws_id}", state.ws_id)
        prs = ops.prs_by_head_prefix(state.repo_slug, prefix)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError):
        return None
    states = [pr.get("state") for pr in prs]
    if any(s not in ("OPEN", "CLOSED", "MERGED") for s in states):
        return None
    return "OPEN" in states
```

- [ ] **Step 4: Run tests to verify they pass**

Run: та же команда, что в Step 2, затем `uv run -q --frozen --group governance pytest tests/test_governance_spec_loop.py -q -p no:cacheprovider` (единственный иной потребитель `prs_by_head_prefix`; новое поле он не читает).
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add governance/ops.py governance/criteria_close.py tests/test_governance_ops.py tests/test_governance_criteria_close.py
git commit -m "ops: prs_by_head_prefix несёт state; criteria_close: факт открытого PR одобрения"
```

---

### Task 3: тестовая форджа на настоящем git — только фикстура, продукт не меняется

Цель: до любой правки продукта перевести тесты `criteria_close` на форджу,
которая действительно вливает PR в `origin/master`, и доказать, что набор
среза 1 на ней зелёный. Иначе Tasks 4–6 не отличат свою поломку от поломки
фикстуры (ревью пары M3).

**Files:**
- Modify: `tests/test_governance_criteria_close.py` (`_ops`, `_oracle_on`, `_commit`, новые `SNAP`, `_forge_merge`, `_land`; перечисленные ниже адаптации)

**Interfaces:**
- Produces (для Tasks 4–6): `_ops(verify)` — FakeOps с полями `forge_prs: dict[int, dict]`, `approval_prs: list[dict]`, `pr_facts_error: str | None`; `merge` вливает PR в `origin/master` от имени `ai-prosto`; `pr_facts` отдаёт факты PR; `agent_login() == "ai-prosto"`; `is_ancestor` — настоящий git. `_forge_merge(ops, pr, login)` — мерж «в UI» любой учёткой. `_land(target) -> str` — чекаут догоняет `origin/master`, возвращает HEAD. `SNAP` — снимок политики `{"owner-human"}`; `_oracle_on(monkeypatch, snapshot=None)` подменяет и `approval_facts.policy_snapshot`.

- [ ] **Step 1: Fixture**

Импорты в начало файла: `from governance import approval_facts as af`, `from governance.facts import Fact, Outcome`. Заменить `_ops`, `_oracle_on`, `_commit` и добавить:

```python
SNAP = af.PolicySnapshot(
    "owner/approval-policy",
    "main",
    "policy/approvers.env",
    "a" * 40,
    frozenset({"owner-human"}),
    af.policy_fingerprint({"owner-human"}),
)


def _forge_merge(ops, pr, login):
    """Мерж PR форджей: --no-ff ветки PR в master origin из отдельного клона."""
    rec = ops.forge_prs[pr]
    target = Path(rec["dir"])
    clone = target.parent / "forge"
    if not clone.exists():
        origin = _git(target, "remote", "get-url", "origin")
        subprocess.run(
            ["git", "clone", "-q", origin, str(clone)], check=True, capture_output=True
        )
    _git(clone, "fetch", "-q", "origin")
    _git(clone, "checkout", "-q", "-B", "master", "origin/master")
    head = _git(clone, "rev-parse", f"origin/{rec['branch']}")
    _git(clone, "merge", "-q", "--no-ff", "-m", f"Merge PR #{pr}", head)
    _git(clone, "push", "-q", "origin", "master")
    rec["facts"] = {
        "state": "MERGED",
        "headRefOid": head,
        "baseRefName": "master",
        "mergedBy": {"login": login},
        "mergedAt": "2026-10-02T00:00:00Z",
        "mergeCommit": {"oid": _git(clone, "rev-parse", "HEAD")},
    }


def _ops(verify=(0, "")):
    from tests.test_governance_runner import FakeOps

    class _Ops(FakeOps):
        def create_pr(
            self, target_dir, repo_slug, branch, title, body, label, *, draft=False
        ):
            self.calls.append(("create_pr", branch, label))
            number = 100 + len(self.existing_prs)
            self.existing_prs[branch] = number
            head = _git(target_dir, "ls-remote", "origin", f"refs/heads/{branch}")
            self.forge_prs[number] = {
                "dir": target_dir,
                "branch": branch,
                "facts": {
                    "state": "OPEN",
                    "headRefOid": head.split()[0],
                    "baseRefName": "master",
                },
            }
            return number

        def find_pr(self, repo_slug, branch):
            """Как `gh pr list --state open`: закрытый/влитый PR не находится."""
            self.calls.append(("find_pr", branch))
            pr = self.existing_prs.get(branch)
            if pr is None or pr not in self.forge_prs:
                return pr
            return pr if self.forge_prs[pr]["facts"]["state"] == "OPEN" else None

        def close_pr(self, repo_slug, pr, comment):
            self.calls.append(("close_pr", pr))
            if pr in self.forge_prs:
                self.forge_prs[pr]["facts"]["state"] = "CLOSED"
            return True

        def criteria_verify(self, target_dir, request_path):
            self.calls.append(("criteria_verify", request_path))
            return self.verify

        def merge(self, repo_name, pr, sha, base=None):
            self.calls.append(("merge", pr, sha))
            if not self.merge_ok:
                return self.merge_code
            self.merged.append((pr, sha))
            _forge_merge(self, pr, "ai-prosto")
            return 0

        def pr_facts(self, repo_slug, pr):
            self.calls.append(("pr_facts", pr))
            if self.pr_facts_error:
                raise RuntimeError(self.pr_facts_error)
            return dict(self.forge_prs[pr]["facts"])

        def agent_login(self):
            return "ai-prosto"

        def prs_by_head_prefix(self, repo_slug, branch_prefix):
            return list(self.approval_prs)

        def is_ancestor(self, target_dir, sha, ref):
            rc = subprocess.run(
                ["git", "-C", target_dir, "merge-base", "--is-ancestor", sha, ref],
                capture_output=True,
            ).returncode
            return {0: True, 1: False}.get(rc)

    ops = _Ops()
    ops.verify = verify
    ops.forge_prs = {}
    ops.approval_prs = []
    ops.pr_facts_error = None
    return ops


def _oracle_on(monkeypatch, snapshot=None):
    monkeypatch.setattr(cc.task_bridge, "spec_runner_version", lambda: "4.5.0")
    monkeypatch.setattr(cc.criteria_contract, "vendored", lambda *a: True)
    monkeypatch.setattr(
        cc.criteria_contract, "read_min_version", lambda *a: ctr.MinVersion("4.5.0")
    )
    fact = snapshot or Fact(Outcome.FOUND, SNAP, "stub")
    monkeypatch.setattr(
        cc.approval_facts, "policy_snapshot", lambda ops, *, pinned_sha: fact
    )


def _land(target):
    """Чекаут оператора догоняет origin/master (форджа влила закрытие/штамп)."""
    _git(target, "pull", "-q", "--ff-only", "origin", "master")
    return _git(target, "rev-parse", "HEAD")


def _commit(target, path, text):
    _land(target)  # форджа могла влить PR: коммит поверх живой верхушки
    (Path(target) / path).parent.mkdir(parents=True, exist_ok=True)
    (Path(target) / path).write_text(text)
    _git(target, "add", path)
    _git(target, "commit", "-qm", f"edit {path}")
    return _git(target, "rev-parse", "HEAD")
```

`cc.approval_facts` появится в модуле в Task 4; до того строка `monkeypatch.setattr(cc.approval_facts, …)` упадёт. Поэтому в этой задаче — временно через `raising=False`-безопасную форму:

```python
    if hasattr(cc, "approval_facts"):
        monkeypatch.setattr(
            cc.approval_facts, "policy_snapshot", lambda ops, *, pinned_sha: fact
        )
```

(в Task 4 `if hasattr` снимается — модуль импортирован всегда).

- [ ] **Step 2: Run the whole file and apply the enumerated adaptations**

Run: `uv run -q --frozen --group governance pytest tests/test_governance_criteria_close.py -q -p no:cacheprovider`

Ожидаемые падения (ревью пары M3) и **единственно допустимые** формы адаптации:

| форма | где | правка |
|---|---|---|
| (а) повтор без `product_sha` после успешного прогона: HEAD ≠ верхушке | `test_same_content_second_measure_is_g6`, `test_same_content_new_response_refused`, `test_g6_pre_check_sees_new_test_file_same_machine` (повтор на «неизменном содержимом») | `_land(target)` перед повторным `cc.run` |
| (б) ручной ff-мерж ветки закрытия поверх влитого форджей | все блоки «`fetch origin <branch>` → `merge --ff-only origin/<branch>` → `push origin master`» (`…another_machine_refused`, `…new_test_file_cross_machine`, `test_fixed_helper_refusal_from_another_machine`) | блок заменяется `merged = _land(target)` (если ниже нужен SHA) или `_land(target)` |
| (в) второй экземпляр `ops` делит PR с первым | `test_response_level_error_publish_failure_is_retried_not_burned` (`ops2.existing_prs = ops.existing_prs`) | добавить `ops2.forge_prs = ops.forge_prs` |
| (г) `_commit` после влитого PR | `…noop_outside_py…`, `…fixed_helper_outside_key…`, `…revert_of_outside_py…`, `test_new_pytest_ini_buys_remeasure_not_g6` и др. | покрыто `_land` внутри `_commit` (Step 1) — правок теста нет |

Expected после адаптаций: весь файл PASS на **неизменённом** продукте. Падение
вне таблицы — находка о фикстуре или продукте: остановиться и записать
`Ruling:` в леджер, тест не подгонять.

- [ ] **Step 3: Commit**

```bash
git add tests/test_governance_criteria_close.py
git commit -m "tests: форджа на настоящем git для criteria_close (срез 2a, без изменений продукта)"
```

---

### Task 4: фаза предложения — собственный коммит, усыновление той же формы, снимок политики

**Files:**
- Modify: `governance/criteria_close.py` (`render_closure` + `human_criteria`, вызов в `_measure`; `_push_closure` → возвращает голову; `_adopt_branch` → голова или None с проверкой формы; новые `_materialize`, `_snapshot_record`, `_propose`, `_policy_config_refusal`; импорты `os`, `approval_facts`, `criteria_accept`, `Outcome`)
- Test: `tests/test_governance_criteria_close.py`

**Interfaces:**
- Consumes: Task 1 (`criteria_accept.*`, `criteria_graph.human_criteria`), Task 3 (фикстура).
- Produces:
  - `render_closure(..., human_criteria: int | None = None)` — пишет `human_criteria` во frontmatter, если не None;
  - `_push_closure(state, branch, text, closure) -> str` — голова собственного коммита;
  - `_adopt_branch(state, branch, text) -> str | None` — голова ветки на origin, если это один коммит поверх предка `origin/<base>`, правящий только файл закрытия, с ровно этим текстом; ветки нет — None; иная форма — `CloseError`;
  - `_materialize(state, branch, text, closure) -> str` — усыновить или запушить, вернуть голову;
  - `_proposal_branch(state, key) -> str` — `<ветка ключа>-proposal`;
  - `_propose(state, ops, run_id, key, text, *, human: bool) -> dict` — пишет и `slice1_pr` (номер PR среза 1 из записи или None); `human` считает вызывающий по графу узлов на пине (спека §7.2a п.1; поле файла не источник — ревью круга 2 B-M1); запись `measured[key]["proposal"] = {"text", "sha256", "human", "base", "branch", "head": None, "policy": {repo, ref, path, sha, fingerprint, source, accounts: [...]}}` (write-ahead, неизменяемая; повтор возвращает записанное);
  - `_policy_config_refusal() -> str | None` — `FORBIDDEN env`/`source` локально, без сети.

- [ ] **Step 1: Write the failing tests**

```python
TRACED = "---\nclosure: traced\nproduct_sha: abc\nhuman_criteria: {n}\n---\nтело\n"


def test_render_writes_human_criteria():
    from governance.criteria_check import Outcome as O

    text = cc.render_closure(
        O("traced", {"BEH-01": "traced"}, {"AC-01": "traced"}),
        ws_id="ws",
        code="ENC",
        bundle_pin="p",
        product_sha="s",
        response_sha="r",
        spec_runner_version="4.5.0",
        host="h",
        human_criteria=2,
    )
    assert split_frontmatter(text)[0]["human_criteria"] == 2


def test_materialize_pushes_own_single_file_commit(tmp_path, monkeypatch):
    state, target, pin = _env(tmp_path, monkeypatch)
    head = cc._materialize(state, "criteria-close/x", "текст\n", "traced")
    _git(target, "fetch", "-q", "origin", "criteria-close/x")
    rel = "workstreams/ws/spec/90-acceptance-closure.md"
    assert _git(target, "diff", "--name-only", f"{head}^", head) == rel
    assert _git(target, "rev-parse", f"{head}^") == pin
    assert cc._materialize(state, "criteria-close/x", "текст\n", "traced") == head


@pytest.mark.parametrize("extra_file", [True, False])
def test_adopt_refuses_foreign_shape(tmp_path, monkeypatch, extra_file):
    """Ревью пары M2: усыновляется только наш коммит формы §7.2a п.1."""
    state, target, pin = _env(tmp_path, monkeypatch)
    rel = "workstreams/ws/spec/90-acceptance-closure.md"
    _git(target, "checkout", "-q", "-b", "foreign")
    (target / rel).write_text("текст\n" if extra_file else "другой\n")
    if extra_file:
        (target / "pkg/m.py").write_text("def f():\n    return 7\n")
    _git(target, "add", "-A")
    _git(target, "commit", "-qm", "foreign")
    _git(target, "push", "-q", "origin", "HEAD:refs/heads/criteria-close/x")
    _git(target, "checkout", "-q", "master")
    with pytest.raises(cc.CloseError):
        cc._materialize(state, "criteria-close/x", "текст\n", "traced")


def test_propose_records_snapshot_and_human_flag(tmp_path, monkeypatch):
    state, _target, _pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    p = cc._propose(state, _ops(), "run-1", "k", TRACED.format(n=0), human=True)
    meta, _ = split_frontmatter(p["text"])
    assert meta["status"] == "proposed" and meta["policy_source"] == SNAP.source
    assert p["human"] is True and p["head"] is None and p["base"] == "master"
    assert p["policy"]["accounts"] == ["owner-human"]
    assert p["sha256"] == criteria_accept.text_sha256(p["text"])
    _oracle_on(monkeypatch, snapshot=Fact(Outcome.UNAVAILABLE, None, "сеть"))
    assert cc._propose(state, _ops(), "run-1", "k", TRACED.format(n=0), human=False) == p


def test_propose_drops_inherited_slice1_pr(tmp_path, monkeypatch):
    """Ревью круга 3 R3-m1: PR среза 1 в записи ключа — не PR предложения."""
    state, _target, _pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    cc._record("run-1", "k", closure="traced", text="t", pr=7, attested=True)
    p = cc._propose(state, _ops(), "run-1", "k", TRACED.format(n=0), human=True)
    e = cc._entry("run-1", "k")
    assert e["pr"] is None and e["attested"] is False and e["closed"] is False
    assert e["slice1_pr"] == 7 and p["branch"].endswith("-proposal")


def test_propose_flag_from_argument_not_from_text(tmp_path, monkeypatch):
    """Ревью круга 2 B-M1: текст без human_criteria (запись до 2a) не
    открывает test-only путь — флаг даёт граф."""
    state, _target, _pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    legacy = "---\nclosure: traced\nproduct_sha: abc\n---\nтело\n"
    assert cc._propose(state, _ops(), "run-1", "k", legacy, human=True)["human"] is True


@pytest.mark.parametrize(
    "kind",
    [af.POLICY_REFUSAL_ENV, af.POLICY_REFUSAL_SOURCE, af.POLICY_REFUSAL_ABSENT, af.POLICY_REFUSAL_EMPTY],
)
def test_propose_refuses_forbidden_policy(tmp_path, monkeypatch, kind):
    state, _target, _pin = _env(tmp_path, monkeypatch)
    refusal = Fact(Outcome.FORBIDDEN, af.PolicyRefusal(kind, "нет"), "нет")
    _oracle_on(monkeypatch, snapshot=refusal)
    with pytest.raises(cc.CloseError) as exc:
        cc._propose(state, _ops(), "run-1", "k", TRACED.format(n=0), human=False)
    waiting = kind in (af.POLICY_REFUSAL_ABSENT, af.POLICY_REFUSAL_EMPTY)
    assert ("wait: policy" in str(exc.value)) is waiting
    assert "proposal" not in (cc._entry("run-1", "k") or {})


def test_propose_unavailable_policy_is_step_refusal(tmp_path, monkeypatch):
    state, _target, _pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch, snapshot=Fact(Outcome.UNAVAILABLE, None, "сеть"))
    with pytest.raises(cc.CloseError, match="не установлен"):
        cc._propose(state, _ops(), "run-1", "k", TRACED.format(n=0), human=False)


def test_policy_config_refusal(monkeypatch):
    monkeypatch.delenv(af.APPROVER_ALLOWLIST_ENV, raising=False)
    assert cc._policy_config_refusal() is None
    monkeypatch.setenv(af.APPROVER_ALLOWLIST_ENV, "x")
    assert "выставлена" in cc._policy_config_refusal()
    monkeypatch.delenv(af.APPROVER_ALLOWLIST_ENV)

    def broken():
        raise RuntimeError("битый source.env")

    monkeypatch.setattr(cc.approval_facts, "policy_source", broken)
    assert "битый" in cc._policy_config_refusal()
```

(`criteria_accept` импортировать в тест: `from governance import criteria_accept`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run -q --frozen --group governance pytest tests/test_governance_criteria_close.py -q -p no:cacheprovider -k "human_criteria or materialize or adopt_refuses or propose or policy_config"`
Expected: FAIL — `TypeError` на `human_criteria=`, нет `_materialize`/`_propose`/`_policy_config_refusal`.

- [ ] **Step 3: Implement**

Импорты: `import os`; в `from governance import (...)` добавить `approval_facts`, `criteria_accept`; `from governance.facts import Outcome`. В фикстуре Task 3 снять `if hasattr(cc, "approval_facts")`.

`render_closure`: параметр `human_criteria: int | None = None`; в ветке `else:` (Outcome) после `meta.update(closure=..., human_pending=human)`:

```python
        if human_criteria is not None:
            meta["human_criteria"] = human_criteria
```

`_measure`, вызов `render_closure(...)` в конце — добавить
`human_criteria=criteria_graph.human_criteria(graph) if roots is not None else None,`
(ветка ответа-ошибки `roots is None` — закрытие `blocked`, приёмки нет).

`_push_closure` — вернуть голову: перед `finally` после цикла шагов:

```python
        return _git(wt, "rev-parse", "HEAD").stdout.strip()
```

и аннотацию `-> str`.

`_adopt_branch` — заменить целиком:

```python
def _adopt_branch(state, branch: str, text: str) -> str | None:
    """Голова ветки на origin, если это наш коммит (§7.2a п.1): один коммит
    поверх предка origin/<base>, правящий только файл закрытия, ровно с этим
    текстом. Ветки нет — None; иная форма — отказ (ревью пары M2: «голова
    совпала» доказывает «дифф — только этот файл» лишь для своего коммита)."""
    ref = f"refs/heads/{branch}"
    if not _git(
        state.target_dir, "ls-remote", "--exit-code", "origin", ref
    ).stdout.strip():
        return None
    base = state.base_ref or "master"
    local = f"refs/criteria-close/{branch}"
    if _git(
        state.target_dir, "fetch", "--quiet", "origin", f"+{ref}:{local}", base
    ).returncode:
        raise CloseError(f"fetch {branch} не удался")
    rel = f"{state.bundle_dir}/{CLOSURE_NAME}"
    shown = _git(state.target_dir, "show", f"{local}:{rel}")
    names = _git(state.target_dir, "diff", "--name-only", f"{local}^", local)
    parent_ok = (
        _git(
            state.target_dir, "merge-base", "--is-ancestor", f"{local}^", f"origin/{base}"
        ).returncode
        == 0
    )
    if (
        shown.returncode != 0
        or shown.stdout != text
        or names.returncode != 0
        or names.stdout.split() != [rel]
        or not parent_ok
    ):
        raise CloseError(
            f"ветка {branch} на origin — не наш коммит (один коммит поверх "
            f"origin/{base}, только {rel}, этот текст) — удалите её "
            f"(`git push origin --delete {branch}`) и повторите"
        )
    return _git(state.target_dir, "rev-parse", local).stdout.strip()


def _materialize(state, branch: str, text: str, closure: str) -> str:
    """Ветка с собственным коммитом формы §7.2a п.1: усыновить или запушить."""
    head = _adopt_branch(state, branch, text)
    return head if head is not None else _push_closure(state, branch, text, closure)
```

В `_publish` (срез 1, `blocked`/`not-applicable`): `if not _adopt_branch(state, branch, text):` → `if _adopt_branch(state, branch, text) is None:`.

Новые функции (секция публикации):

```python
def _policy_config_refusal() -> str | None:
    """`FORBIDDEN env`/`source` в любой фазе, и после мержа (§3.2, §7.2a п.3):
    обе проверки локальные и сеть не трогают."""
    if os.environ.get(approval_facts.APPROVER_ALLOWLIST_ENV) is not None:
        return (
            f"{approval_facts.APPROVER_ALLOWLIST_ENV} выставлена в окружении — "
            "переменная больше не источник политики подписи; снимите её"
        )
    try:
        approval_facts.policy_source()
    except RuntimeError as exc:
        return f"конфигурация источника политики не читается: {exc}"
    return None


def _snapshot_record(snap: approval_facts.PolicySnapshot) -> dict:
    """Полный снимок (§3.5, Р3′): `as_record()` состав не пишет — пишем сами."""
    return {
        **snap.as_record(),
        "source": snap.source,
        "accounts": sorted(snap.accounts),
    }


def _propose(state, ops: Ops, run_id: str, key: str, text: str, *, human: bool) -> dict:
    """Предложение до push (§7.2a п.1): write-ahead, неизменяемое. `human` —
    по графу узлов на пине (вызывающий), не по полю файла: запись, измеренная
    до 2a, поля не несёт, и его отсутствие открыло бы test-only путь."""
    entry = _entry(run_id, key) or {}
    if entry.get("proposal"):
        return entry["proposal"]
    fact = approval_facts.policy_snapshot(ops, pinned_sha=None)
    if fact.outcome is Outcome.UNAVAILABLE:
        raise CloseError(f"снимок политики не установлен: {fact.detail}")
    snap = fact.value
    if fact.outcome is not Outcome.FOUND or not isinstance(
        snap, approval_facts.PolicySnapshot
    ):
        kind = getattr(snap, "kind", "")
        waiting = kind in (
            approval_facts.POLICY_REFUSAL_ABSENT,
            approval_facts.POLICY_REFUSAL_EMPTY,
        )
        raise CloseError(
            ("wait: policy — " if waiting else "") + f"политика подписи: {fact.detail}"
        )
    body = criteria_accept.proposal_text(text, snap.source)
    proposal = {
        "text": body,
        "sha256": criteria_accept.text_sha256(body),
        "human": human,
        "base": state.base_ref or "master",
        "branch": _proposal_branch(state, key),
        "head": None,
        "policy": _snapshot_record(snap),
    }
    # Запись, опубликованная срезом 1, несёт `pr` PR среза 1 на ветке ключа —
    # у предложения своя ветка и свой PR (решение владельца: миграция). Старый
    # PR запоминается (`slice1_pr`), его обработает `_advance`.
    _record(
        run_id,
        key,
        proposal=proposal,
        slice1_pr=entry.get("pr"),
        pr=None,
        attested=False,
        closed=False,
    )
    return proposal


def _proposal_branch(state, key: str) -> str:
    """Ветка предложения 2a — не ветка ключа среза 1 (там может лежать
    опубликованный файл среза 1, и `_materialize` его не усыновит)."""
    return _branch(state, key) + "-proposal"
```

- [ ] **Step 4: Run tests**

Run: команда Step 2, затем весь файл.
Expected: новые — PASS; весь файл — PASS (продукт пока публикует `traced` путём среза 1: `_propose` ещё никем не вызывается).

- [ ] **Step 5: Commit**

```bash
git add governance/criteria_close.py tests/test_governance_criteria_close.py
git commit -m "criteria_close: предложение — собственный коммит, усыновление той же формы, снимок политики"
```

---

### Task 5: `_advance` — приёмка по фактам форджи; маршрутизация `run`/`_measure`

**Files:**
- Modify: `governance/criteria_close.py` (`_close_stale` из `_publish`, `_pr_state`, `_open_pr`, `_show_or_none`, `_accept_facts`, `_proposal_view`, `_advance`, `_settled`, `_pending_acceptance`, заглушка `_stamp`; `run`; обе ветки `known` в `_measure`)
- Test: `tests/test_governance_criteria_close.py`

**Interfaces:**
- Consumes: Task 1, Task 2 (`_approval_pr_open`), Task 4 (`_propose`, `_materialize`, `_policy_config_refusal`), `approval_facts.read_pr/merge_event`.
- Produces: `_advance(state, ops, run_id, key, bundle_pin) -> int`; записи `measured[key]`: `proposal.head`, `pr`, `branch`, `attested: bool`, `merge: {"oid", "by"}`, `merged: True` (при прохождении предиката), `acceptance: {"state", "reason"}`; `_stamp(state, ops, run_id, key) -> int` (заглушка, реализация — Task 6).

- [ ] **Step 1: Write the failing tests**

```python
BEH_HUMAN = BEH + (
    "#### BEH-02: оператор читает отчёт\n`traces: [FR-01]`\n"
    "- **checked_by**: `status: planned` `kind: manual` `owner: qa`\n"
)
ACC_HUMAN = ACC + (
    "#### AC-02: отчёт · verification: manual\ntraces: [FR-01]\n"
    "scenarios: [BEH-02]\n"
)
REQ_SHOULD = "#### FR-01: A\n**Priority**: Should\n"
BEH_MIXED = BEH + (
    "#### BEH-02: ручной\n`traces: [FR-01]`\n"
    "- **checked_by**: `status: planned` `kind: manual` `owner: qa`\n"
)
ACC_MIXED = "#### AC-01: a · verification: test\ntraces: [FR-01]\nscenarios: [BEH-01, BEH-02]\n"
SPEC = "workstreams/ws/spec"


def _env_human(tmp_path, monkeypatch):
    return _env(
        tmp_path,
        monkeypatch,
        extra={f"{SPEC}/15-behaviour-spec.md": BEH_HUMAN, f"{SPEC}/25-acceptance.md": ACC_HUMAN},
    )


def _proposal_pr(ops):
    return min(ops.forge_prs)


def _key():
    (key,) = cc._load("run-1")["measured"]
    return key


def test_human_criterion_waits_for_human_merge(tmp_path, monkeypatch, capsys):
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 4
    p = cc._entry("run-1", _key())["proposal"]
    assert ("create_pr", p["branch"], "criteria-close,human-merge-required") in ops.calls
    assert p["head"] == ops.forge_prs[_proposal_pr(ops)]["facts"]["headRefOid"]
    assert any(c[0] == "review" for c in ops.calls) and not ops.merged
    assert "человеком" in capsys.readouterr().out


def test_human_flag_by_graph_not_ac_status(tmp_path, monkeypatch):
    """Ревью пары M1: Should-AC unconfirmed + ручной BEH — всё равно человек."""
    _state, target, pin = _env(
        tmp_path,
        monkeypatch,
        extra={
            f"{SPEC}/10-requirements.md": REQ_SHOULD,
            f"{SPEC}/15-behaviour-spec.md": BEH_MIXED,
            f"{SPEC}/25-acceptance.md": ACC_MIXED,
        },
    )
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin, status="unconfirmed"))))
    assert cc.run("run-1", ops) == 4
    assert not ops.merged


def test_human_flag_ignores_missing_field_in_text(tmp_path, monkeypatch):
    """Ревью круга 2 B-M1: текст без human_criteria (как у записи среза 1) —
    флаг всё равно из графа: выход 4, агент не мержит."""
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    real = cc.render_closure

    def legacy(*a, **k):
        k.pop("human_criteria", None)
        return real(*a, **k)

    monkeypatch.setattr(cc, "render_closure", legacy)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 4
    assert not ops.merged


def test_node_deleted_on_tip_supersedes(tmp_path, monkeypatch):
    """Ревью круга 2 B-M3: узла нет на верхушке — superseded, не вечный отказ."""
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 4
    pr = _proposal_pr(ops)
    _forge_merge(ops, pr, "owner-human")
    clone = Path(ops.forge_prs[pr]["dir"]).parent / "forge"
    _git(clone, "rm", "-q", f"{SPEC}/25-acceptance.md")
    _git(clone, "commit", "-qm", "drop node")
    _git(clone, "push", "-q", "origin", "master")
    assert cc.run("run-1", ops) == 5
    acc = cc._entry("run-1", _key())["acceptance"]
    assert acc["state"] == "superseded" and "bundle" in acc["reason"]


def test_human_merge_by_snapshot_account_records_merge(tmp_path, monkeypatch):
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 4
    _forge_merge(ops, _proposal_pr(ops), "owner-human")
    cc.run("run-1", ops)
    e = cc._entry("run-1", _key())
    assert e["merge"]["by"] == "owner-human" and e["merged"] is True


def test_resume_after_human_merge_needs_no_checkout(tmp_path, monkeypatch):
    """Review Focus 1: верхушка ушла вперёд, чекаут на старом product_sha."""
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 4
    _forge_merge(ops, _proposal_pr(ops), "owner-human")
    calls = sum(1 for c in ops.calls if c[0] == "criteria_verify")
    cc.run("run-1", ops)
    assert _git(target, "rev-parse", "HEAD") == pin
    assert sum(1 for c in ops.calls if c[0] == "criteria_verify") == calls
    assert cc._entry("run-1", _key())["merge"]["by"] == "owner-human"


def test_human_pr_still_open_keeps_waiting(tmp_path, monkeypatch):
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 4
    assert cc.run("run-1", ops) == 4
    assert sum(1 for c in ops.calls if c[0] == "create_pr") == 1
    assert sum(1 for c in ops.calls if c[0] == "review") == 1  # аттестация записана


def test_human_criterion_merged_by_outsider_is_rejected(tmp_path, monkeypatch):
    """§8.4 п.5 тестом: на polygon третьей учётки нет."""
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 4
    _forge_merge(ops, _proposal_pr(ops), "stranger")
    assert cc.run("run-1", ops) == 5
    assert cc._entry("run-1", _key())["acceptance"]["state"] == "rejected"
    assert not any("-stamp" in c[1] for c in ops.calls if c[0] == "create_pr")


@pytest.mark.parametrize("human", [True, False])
def test_closed_proposal_is_rejected_on_both_paths(tmp_path, monkeypatch, capsys, human):
    """Review Focus 2 / ревью пары B1(б)."""
    env = _env_human if human else _env
    _state, target, pin = env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    ops.merge_ok = False  # test-only: обвязка отказала, PR остался открытым
    assert cc.run("run-1", ops) == (4 if human else 2)
    ops.forge_prs[_proposal_pr(ops)]["facts"]["state"] = "CLOSED"
    assert cc.run("run-1", ops) == 5
    assert cc.run("run-1", ops) == 5
    assert sum(1 for c in ops.calls if c[0] == "create_pr") == 1
    assert "closed" in capsys.readouterr().out


def test_test_only_merged_by_human_goes_to_predicate(tmp_path, monkeypatch):
    """Ревью пары B1(а): на polygon нет review-kit — PR предложения мержит человек."""
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    ops.merge_ok = False
    assert cc.run("run-1", ops) == 2
    _forge_merge(ops, _proposal_pr(ops), "owner-human")
    cc.run("run-1", ops)
    assert cc._entry("run-1", _key())["merge"]["by"] == "owner-human"
    assert sum(1 for c in ops.calls if c[0] == "create_pr" and "-stamp" not in c[1]) == 1


def test_review_failure_on_human_path_is_retried_not_buried(tmp_path, monkeypatch):
    """Review Focus 3 / ревью пары B2."""
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    ops.review_exit = 1
    assert cc.run("run-1", ops) == 2
    assert cc._entry("run-1", _key())["proposal"]["head"]  # своя голова уже записана
    ops.review_exit = 0
    assert cc.run("run-1", ops) == 4
    _forge_merge(ops, _proposal_pr(ops), "owner-human")
    cc.run("run-1", ops)
    e = cc._entry("run-1", _key())
    assert e["merge"]["by"] == "owner-human"
    assert e.get("acceptance", {}).get("state") != "rejected"


def test_unreadable_pr_facts_is_step_refusal_then_resume(tmp_path, monkeypatch):
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 4
    _forge_merge(ops, _proposal_pr(ops), "owner-human")
    ops.pr_facts_error = "gh: network"
    assert cc.run("run-1", ops) == 2
    assert "acceptance" not in cc._entry("run-1", _key())
    ops.pr_facts_error = None
    cc.run("run-1", ops)
    assert cc._entry("run-1", _key())["merge"]["by"] == "owner-human"


def test_open_approval_pr_supersedes(tmp_path, monkeypatch):
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    ops.approval_prs = [{"state": "OPEN", "number": 9}]
    assert cc.run("run-1", ops) == 5
    acc = cc._entry("run-1", _key())["acceptance"]
    assert acc["state"] == "superseded" and "approval" in acc["reason"]


def test_policy_env_refuses_command_in_any_phase(tmp_path, monkeypatch, capsys):
    """Ревью пары m1: FORBIDDEN env — отказ команды и после открытия PR."""
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 4
    _forge_merge(ops, _proposal_pr(ops), "owner-human")
    monkeypatch.setenv(af.APPROVER_ALLOWLIST_ENV, "x")
    assert cc.run("run-1", ops) == 2
    assert "отказ команды" in capsys.readouterr().out
    assert "merge" not in cc._entry("run-1", _key())


def test_slice1_pr_close_failure_is_step_refusal(tmp_path, monkeypatch):
    """R4-m3/R5 m-a: PR среза 1 не закрылся — отказ шага, повтор пробует снова."""
    state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    nodes = cc._bundle_at_pin(state, pin)
    charter = cc.charter_guard.read_charter(nodes["00-charter.md"])
    key, closure, text = cc._measure(
        state, ops, "run-1", charter, nodes, pin, pin, "4.5.0", "h"
    )
    ops.review_exit = 1
    assert cc._publish(state, ops, "run-1", key, text, closure) == 2
    old_pr = cc._entry("run-1", key)["pr"]
    ops.review_exit = 0
    ops.close_pr = lambda slug, pr, comment: False
    assert cc.run("run-1", ops) == 2
    assert not cc._entry("run-1", key).get("slice1_done")
    assert cc._entry("run-1", key)["pr"] is None  # предложение ещё без PR
    assert ops.forge_prs[old_pr]["facts"]["state"] == "OPEN"


def test_new_content_after_rejection_measures_again(tmp_path, monkeypatch):
    """Терминал — только на своём ключе (§7.2a п.5): прогон не запирается."""
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 4
    ops.forge_prs[_proposal_pr(ops)]["facts"]["state"] = "CLOSED"
    assert cc.run("run-1", ops) == 5
    changed = _commit(target, "pkg/m.py", "def f():\n    return 99\n")
    _git(target, "push", "-q", "origin", "master")
    ops.verify = (0, json.dumps(_response(target, changed, bundle_pin=pin)))
    assert cc.run("run-1", ops, product_sha=changed) == 4
    assert sum(1 for c in ops.calls if c[0] == "create_pr") == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run -q --frozen --group governance pytest tests/test_governance_criteria_close.py -q -p no:cacheprovider -k "waits_for_human or by_graph or missing_field or node_deleted or records_merge or needs_no_checkout or still_open or outsider or both_paths or merged_by_human or not_buried or unreadable_pr or supersedes or any_phase or after_rejection"`
Expected: FAIL — `traced` всё ещё публикуется путём среза 1.

- [ ] **Step 3: Implement**

`_publish`: цикл закрытия устаревших PR вынести в функцию и вызвать её из `_publish` на прежнем месте:

```python
def _close_stale(state, ops: Ops, run_id: str, key: str) -> None:
    """Устаревшие неслитые PR других ключей закрываются (I1, срез 1)."""
    branch = _branch(state, key)
    for other_key, other in _load(run_id)["measured"].items():
        if (
            other_key != key
            and other.get("pr")
            and not other.get("merged")
            and not other.get("closed")
            and not other.get("acceptance")  # исход приёмки — PR уже не «устарел»
        ):
            ops.close_pr(
                state.repo_slug, other["pr"], f"устарело: новое измерение {branch}"
            )
            _record(run_id, other_key, closed=True)
```

Новые функции:

```python
def _show_or_none(repo: str, ref: str, path: str) -> str | None:
    proc = _git(repo, "show", f"{ref}:{path}")
    return proc.stdout if proc.returncode == 0 else None


def _path_fact(repo: str, ref: str, path: str) -> str | None:
    """Файл в ревизии: текст; "" — пути нет (установлено `ls-tree`); None —
    не прочитано. Отсутствие — факт, а не «не установлено» (ревью круга 2
    B-M3: иначе удалённый узел/файл запирал прогон вечным отказом шага)."""
    listed = _git(repo, "ls-tree", ref, "--", path)
    if listed.returncode:
        return None
    if not listed.stdout.strip():
        return ""
    return _show_or_none(repo, ref, path)


def _pr_state(ops: Ops, repo_slug: str, pr: int) -> tuple[str | None, dict]:
    """Состояние PR по фактам форджи; None — не установлено (§I12-правило)."""
    fact = approval_facts.read_pr(ops, repo_slug, pr)
    if fact.outcome is not Outcome.FOUND or not isinstance(fact.value, dict):
        return None, {}
    st = fact.value.get("state")
    return (st if st in ("OPEN", "CLOSED", "MERGED") else None), fact.value


def _open_pr(state, ops: Ops, branch: str, title: str, body: str, labels: str) -> int:
    """Открытый PR ветки или новый; метки — тем же вызовом создания."""
    try:
        pr = ops.find_pr(state.repo_slug, branch)
    except (subprocess.CalledProcessError, OSError, RuntimeError) as exc:
        raise CloseError(f"поиск PR ветки {branch} не удался: {exc}") from exc
    if pr is not None:
        return pr
    try:
        return ops.create_pr(
            state.target_dir, state.repo_slug, branch, title, body, labels
        )
    except (subprocess.CalledProcessError, OSError, RuntimeError) as exc:
        detail = (getattr(exc, "stderr", "") or str(exc)).strip()
        raise CloseError(
            f"gh pr create не удался для {branch} (метки {labels} и права в "
            f"{state.repo_slug}?): {detail[-300:]}"
        ) from exc


def _accept_facts(
    state, ops: Ops, p: dict, raw: dict, bundle_pin: str
) -> criteria_accept.AcceptFacts:
    """Факты предиката §3.5; любой несобранный — None (→ unavailable)."""
    base = p["base"]
    event = approval_facts.merge_event(raw)
    login = oid = None
    if event.outcome is Outcome.FOUND and event.value is not None:
        login, oid = event.value.login, event.value.commit
    fetched = _git(state.target_dir, "fetch", "--quiet", "origin", base).returncode == 0
    rel = f"{state.bundle_dir}/{CLOSURE_NAME}"
    blob = nodes = None
    if fetched and oid:
        merged = _path_fact(state.target_dir, oid, rel)
        blob = (
            None
            if merged is None
            else ""
            if merged == ""
            else criteria_accept.text_sha256(merged)
        )
    if fetched:
        pairs = [
            (
                _show_or_none(state.target_dir, bundle_pin, f"{state.bundle_dir}/{n}"),
                _path_fact(state.target_dir, f"origin/{base}", f"{state.bundle_dir}/{n}"),
            )
            for n in _NODES
        ]
        if all(a is not None and b is not None for a, b in pairs):
            nodes = all(a == b for a, b in pairs)  # "" (узла нет) ≠ байтам пина
    product = split_frontmatter(p["text"])[0].get("product_sha")
    on_tip = (
        ops.is_ancestor(state.target_dir, product, f"origin/{base}")
        if fetched and isinstance(product, str)
        else None
    )
    return criteria_accept.AcceptFacts(
        state=raw.get("state"),
        head=raw.get("headRefOid"),
        base=raw.get("baseRefName"),
        merged_by=login,
        merge_oid=oid,
        merged_blob_sha256=blob,
        nodes_fresh=nodes,
        approval_pr_open=_approval_pr_open(ops, state),
        product_on_tip=on_tip,
        agent_login=ops.agent_login(),
    )


def _proposal_view(p: dict) -> criteria_accept.Proposal:
    return criteria_accept.Proposal(
        head=p["head"],
        base=p["base"],
        text_sha256=p["sha256"],
        human=p["human"],
        accounts=frozenset(p["policy"]["accounts"]),
    )


def _settled(e: dict) -> int | None:
    """Терминальная приёмка ключа — выход 5, без публикации (§7.2a п.5).

    Принятый ключ сюда не нужен: при прохождении предиката пишется
    `merged=True`, и прежние ветки G6 отвечают 6 со своей диагностикой (в т.ч.
    «изменились файлы вне ключа», #540); незавершённая приёмка продолжается
    раньше измерения (`_pending_acceptance`). Так ни одна форма ключа с
    предложением не доходит до `_publish` (ревью пары B1(в))."""
    acc = e.get("acceptance") or {}
    if acc.get("state") in criteria_accept.TERMINAL:
        print(f"criteria-close: приёмка {acc['state']}: {acc['reason']}")
        return criteria_accept.EXIT_TERMINAL
    return None


def _pending_acceptance(run_id: str) -> str | None:
    """Ключ с незавершённой приёмкой: предложение есть, исхода нет, не устарел."""
    for key, e in _load(run_id)["measured"].items():
        if e.get("proposal") and not e.get("acceptance") and not e.get("closed"):
            return key
    return None


def _advance(state, ops: Ops, run_id: str, key: str, bundle_pin: str) -> int:
    """Приёмка ключа по фактам форджи (§7.2a пп.2–5): один шаг за вызов-фазу.

    Локально — только намерения (своя голова, номер PR, аттестация) и
    исходы; «влит ли PR» читается у форджи при каждом вызове (ревью пары B1)."""
    refusal = _policy_config_refusal()
    if refusal is not None:
        print(f"criteria-close: отказ команды — {refusal}")
        return 2
    e = _entry(run_id, key) or {}
    settled = _settled(e)
    if settled is not None:
        return settled
    if (e.get("acceptance") or {}).get("state") == "accepted":
        return 0
    if e.get("merge"):
        return _stamp(state, ops, run_id, key)
    p = e["proposal"]
    if e.get("slice1_pr") and not e.get("slice1_done"):
        # миграция среза 1: прежний PR на ветке ключа заменён предложением
        st, _ = _pr_state(ops, state.repo_slug, e["slice1_pr"])
        if st is None:
            print(f"criteria-close: отказ шага — PR среза 1 #{e['slice1_pr']} не прочитан")
            return 2
        if st == "OPEN" and not ops.close_pr(
            state.repo_slug,
            e["slice1_pr"],
            f"заменён предложением приёмки {p['branch']} (срез 2a)",
        ):
            # R4-m3: открытый PR среза 1 мог бы влить человек — конфликт с
            # предложением и head-moved; без подтверждённого закрытия — повтор
            print(f"criteria-close: отказ шага — PR среза 1 #{e['slice1_pr']} не закрыт")
            return 2
        _record(run_id, key, slice1_done=True)
    if not p.get("head"):
        p["head"] = _materialize(state, p["branch"], p["text"], "traced")
        _record(run_id, key, proposal=p)
    if not e.get("pr"):
        labels = criteria_accept.HUMAN_LABELS if p["human"] else criteria_accept.LABEL
        pr = _open_pr(
            state,
            ops,
            p["branch"],
            f"criteria-close: {state.ws_id} — предложение приёмки",
            "Предложение приёмки (срез 2a): status: proposed, снимок "
            f"{p['policy']['source']}. Не нажимайте «Update branch»: новая "
            "голова — rejected (head-moved), выход только --repropose (2b).",
            labels,
        )
        _record(run_id, key, pr=pr, branch=p["branch"])
        e = _entry(run_id, key) or {}
    st, raw = _pr_state(ops, state.repo_slug, e["pr"])
    if st is None:
        print(f"criteria-close: отказ шага — состояние PR #{e['pr']} не прочитано")
        return 2
    if st == "OPEN":
        if not e.get("attested"):
            if ops.review(state.repo, e["pr"]) != 0:
                return 2
            _record(run_id, key, attested=True)
        if p["human"]:
            print(
                f"criteria-close: PR #{e['pr']} предложения ждёт мержа человеком "
                f"из снимка политики ({p['policy']['source']})"
            )
            return criteria_accept.EXIT_WAITING_HUMAN
        if ops.merge(state.repo, e["pr"], p["head"]) != 0:
            return 2
        st, raw = _pr_state(ops, state.repo_slug, e["pr"])
        if st != "MERGED":
            print(f"criteria-close: отказ шага — PR #{e['pr']} ещё не влит; повторите")
            return 2
    v = criteria_accept.predicate(
        _proposal_view(p), _accept_facts(state, ops, p, raw, bundle_pin)
    )
    if v.kind in criteria_accept.TERMINAL:
        _record(run_id, key, acceptance={"state": v.kind, "reason": v.reason})
        print(f"criteria-close: приёмка {v.kind}: {v.reason}")
        return criteria_accept.EXIT_TERMINAL
    if v.kind != "stamping":
        print(f"criteria-close: отказ шага — {v.reason}; повторите")
        return 2
    event = approval_facts.merge_event(raw).value
    _record(
        run_id,
        key,
        merged=True,
        merge={"oid": event.commit, "by": event.login},
    )
    return _stamp(state, ops, run_id, key)


def _stamp(state, ops: Ops, run_id: str, key: str) -> int:
    raise CloseError("штамп — Task 6")
```

(`event` здесь не None: предикат вернул `stamping` только при установленных
`merged_by` и `merge_oid`, а они взяты из того же `merge_event(raw)`.)

`run()` — первым шагом внутри существующего `try:` (до `nodes = …`):

```python
        pending = _pending_acceptance(run_id)
        if pending is not None:
            return _advance(state, ops, run_id, pending, bundle_pin)
```

и хвост:

```python
        key, closure, text = measured
        if closure == "traced":
            _close_stale(state, ops, run_id, key)
            graph = criteria_graph.build_graph(
                nodes["10-requirements.md"],
                nodes["15-behaviour-spec.md"],
                nodes["25-acceptance.md"],
            )
            human = criteria_graph.human_criteria(graph) > 0
            _propose(state, ops, run_id, key, text, human=human)
            return _advance(state, ops, run_id, key, bundle_pin)
        return _publish(state, ops, run_id, key, text, closure)
```

`_measure`, ветка `if known is not None:` (пред-проверка) — первыми строками:

```python
        k, e = known
        settled = _settled(e)
        if settled is not None:
            return settled
```

(прежнее `k, e = known` удалить — оно стало первой строкой). Пост-проверка:
перед `if known is not None and not known[1].get("merged"):` вставить

```python
    if known is not None:
        settled = _settled(known[1])
        if settled is not None:
            return settled
```

Docstring `run`: «0 — принято/опубликовано; 2 — отказ шага или команды; 4 —
ждёт мержа человеком; 5 — приёмка отклонена/устарела на этом ключе; 6 — ключ
измерен». Docstring модуля: «Ревизий, подписи и штампа нет — это срез 2» →
«Закрытие `traced` — предложение приёмки, подпись человека и штамп (срез 2a,
спека §7.2a): `_advance` по фактам форджи».

- [ ] **Step 4: Run tests**

Run: команда Step 2, затем весь файл.
Expected: тесты этой задачи PASS. Весь файл: существующие тесты `traced`,
ждущие `== 0`, теперь доходят до заглушки `_stamp` и получают 2 — ожидаемо до
Task 6 (их список: все, где `traced` и `cc.run(...) == 0`). Прочие — PASS.

- [ ] **Step 5: Commit**

```bash
git add governance/criteria_close.py tests/test_governance_criteria_close.py
git commit -m "criteria_close: _advance — приёмка по фактам форджи, предикат §3.5, терминал на ключе"
```

---

### Task 6: штамп — собственный коммит, мерж по своей голове, проверка

**Files:**
- Modify: `governance/criteria_close.py` (`_stamp` вместо заглушки; `_new_stamp`, `_base_closure`, `_check_and_accept`)
- Test: `tests/test_governance_criteria_close.py`

**Interfaces:**
- Consumes: Task 4 (`_materialize`), Task 5 (`_open_pr`, `_pr_state`, `_show_or_none`, запись `merge`), `criteria_accept.stamp_text/check_stamp/LABEL`.
- Produces: `measured[key]["stamp"] = {"n", "branch", "head", "pr", "attested"}`; `acceptance.state = "accepted"` при `valid`.

- [ ] **Step 1: Write the failing tests**

```python
CLOSURE_REL = "workstreams/ws/spec/90-acceptance-closure.md"


def _stamp_prs(ops):
    return [c for c in ops.calls if c[0] == "create_pr" and "-stamp" in c[1]]


def test_test_only_honest_run_is_accepted(tmp_path, monkeypatch):
    """§8.4 п.7: честный прогон → PR приёмки, мерж, stamp-PR → accepted."""
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 0
    e = cc._entry("run-1", _key())
    assert e["acceptance"]["state"] == "accepted"
    _land(target)
    meta, _ = split_frontmatter((target / CLOSURE_REL).read_text())
    assert meta["status"] == "accepted" and meta["accepted_merge"] == e["merge"]["oid"]
    assert len(_stamp_prs(ops)) == 1 and _stamp_prs(ops)[0][2] == "criteria-close"
    stamp = e["stamp"]
    assert any(c == ("merge", stamp["pr"], stamp["head"]) for c in ops.calls)
    assert cc.run("run-1", ops) == 6


def test_human_path_accepted_after_human_merge(tmp_path, monkeypatch):
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 4
    _forge_merge(ops, _proposal_pr(ops), "owner-human")
    assert cc.run("run-1", ops) == 0
    assert cc._entry("run-1", _key())["acceptance"]["state"] == "accepted"


def test_rerun_after_human_acceptance_publishes_nothing(tmp_path, monkeypatch):
    """Review Focus 5 / ревью пары B1(в): не откат accepted → proposed."""
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 4
    _forge_merge(ops, _proposal_pr(ops), "owner-human")
    assert cc.run("run-1", ops) == 0
    created = sum(1 for c in ops.calls if c[0] == "create_pr")
    _land(target)
    assert cc.run("run-1", ops) == 6
    assert sum(1 for c in ops.calls if c[0] == "create_pr") == created


def test_stamp_pr_review_failure_resumes(tmp_path, monkeypatch):
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    ops.fail_stamp_review = True

    def review(repo, pr):
        ops.calls.append(("review", pr))
        stamp = "-stamp" in ops.forge_prs[pr]["branch"]
        return 1 if stamp and ops.fail_stamp_review else 0

    ops.review = review
    assert cc.run("run-1", ops) == 2
    ops.fail_stamp_review = False
    assert cc.run("run-1", ops) == 0
    assert len(_stamp_prs(ops)) == 1


def test_stamp_merged_by_human_is_adopted(tmp_path, monkeypatch):
    """На polygon нет review-kit: stamp-PR мержит человек — resume принимает."""
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))

    def review(repo, pr):
        ops.calls.append(("review", pr))
        return 1 if "-stamp" in ops.forge_prs[pr]["branch"] else 0

    ops.review = review
    assert cc.run("run-1", ops) == 2
    _forge_merge(ops, max(ops.forge_prs), "owner-human")
    assert cc.run("run-1", ops) == 0


def _tamper(ops, pr, how):
    clone = Path(ops.forge_prs[pr]["dir"]).parent / "forge"
    f = clone / CLOSURE_REL
    if how == "edit":
        f.write_text(f.read_text() + "правка\n")
        _git(clone, "commit", "-qam", "tamper")
    else:
        _git(clone, "rm", "-q", CLOSURE_REL)
        _git(clone, "commit", "-qm", "delete")
    _git(clone, "push", "-q", "origin", "master")


@pytest.mark.parametrize("old_merged_by_human", [False, True])
def test_slice1_unfinished_closure_migrates_without_remeasure(
    tmp_path, monkeypatch, old_merged_by_human
):
    """Решение владельца: опубликованное, не влитое агентом закрытие среза 1
    (PR открыт или влит человеком — R4-m4) → предложение 2a на своей ветке →
    подпись → штамп, без перемера."""
    state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    nodes = cc._bundle_at_pin(state, pin)
    charter = cc.charter_guard.read_charter(nodes["00-charter.md"])
    key, closure, text = cc._measure(
        state, ops, "run-1", charter, nodes, pin, pin, "4.5.0", "h"
    )
    ops.review_exit = 1
    assert cc._publish(state, ops, "run-1", key, text, closure) == 2  # срез 1
    old_pr = cc._entry("run-1", key)["pr"]
    if old_merged_by_human:
        _forge_merge(ops, old_pr, "owner-human")
        _land(target)
    ops.review_exit = 0
    assert cc.run("run-1", ops) == 4
    e = cc._entry("run-1", key)
    assert e["slice1_pr"] == old_pr
    assert (("close_pr", old_pr) in ops.calls) is not old_merged_by_human
    assert e["proposal"]["branch"].endswith("-proposal") and e["pr"] != old_pr
    _forge_merge(ops, e["pr"], "owner-human")
    assert cc.run("run-1", ops) == 0
    assert cc._entry("run-1", key)["acceptance"]["state"] == "accepted"
    assert sum(1 for c in ops.calls if c[0] == "criteria_verify") == 1


@pytest.mark.parametrize("how", ["edit", "delete"])
def test_tampered_or_deleted_stamp_reissues_stamp_pr(tmp_path, monkeypatch, how):
    """Review Focus 4 / ревью пары m4: иное содержимое или файла нет → новый stamp-PR."""
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    real_merge = ops.merge
    done = {"x": False}

    def merge(repo, pr, sha, base=None):
        code = real_merge(repo, pr, sha, base)
        if code == 0 and "-stamp" in ops.forge_prs[pr]["branch"] and not done["x"]:
            done["x"] = True
            _tamper(ops, pr, how)
        return code

    ops.merge = merge
    assert cc.run("run-1", ops) == 2
    assert cc.run("run-1", ops) == 0
    stamps = [c[1] for c in _stamp_prs(ops)]
    assert len(stamps) == 2 and stamps[1].endswith("-stamp-2")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run -q --frozen --group governance pytest tests/test_governance_criteria_close.py -q -p no:cacheprovider -k "accepted or stamp or publishes_nothing or migrates"`
Expected: FAIL — заглушка `_stamp` («штамп — Task 6», код 2).

- [ ] **Step 3: Implement** (заменить заглушку)

```python
def _new_stamp(branch: str, n: int) -> dict:
    suffix = "-stamp" + ("" if n == 1 else f"-{n}")
    return {"n": n, "branch": branch + suffix, "head": None, "pr": None, "attested": False}


def _base_closure(state, base: str) -> str | None:
    """Файл закрытия на верхушке default: текст; "" — файла нет (установлено);
    None — fetch/чтение не удалось."""
    if _git(state.target_dir, "fetch", "--quiet", "origin", base).returncode:
        return None
    return _path_fact(
        state.target_dir, f"origin/{base}", f"{state.bundle_dir}/{CLOSURE_NAME}"
    )


def _check_and_accept(state, run_id: str, key: str, expected: str, base: str) -> int | None:
    """Проверка штампа §3.2: valid → accepted (0); unavailable → 2; invalid → None."""
    check = criteria_accept.check_stamp(expected, _base_closure(state, base))
    if check == "valid":
        _record(run_id, key, acceptance={"state": "accepted", "reason": ""})
        print("criteria-close: принято — status: accepted")
        return 0
    if check == "unavailable":
        print("criteria-close: отказ шага — файл закрытия в default не прочитан; повторите")
        return 2
    return None


def _stamp(state, ops: Ops, run_id: str, key: str) -> int:
    """Штамп (§3.2, §7.2a п.4): свой коммит, мерж по своей голове, проверка."""
    e = _entry(run_id, key) or {}
    p = e["proposal"]
    expected = criteria_accept.stamp_text(
        p["text"], merge_oid=e["merge"]["oid"], pr=e["pr"]
    )
    done = _check_and_accept(state, run_id, key, expected, p["base"])
    if done is not None:
        return done
    s = e.get("stamp") or _new_stamp(p["branch"], 1)
    if s["pr"] is not None:
        st, _ = _pr_state(ops, state.repo_slug, s["pr"])
        if st is None:
            print(f"criteria-close: отказ шага — состояние stamp-PR #{s['pr']} не прочитано")
            return 2
        if st in ("MERGED", "CLOSED"):  # влит без штампа в default или закрыт
            s = _new_stamp(p["branch"], s["n"] + 1)
    if not s["head"]:
        s["head"] = _materialize(state, s["branch"], expected, "accepted")
        _record(run_id, key, stamp=s)
    if s["pr"] is None:
        s["pr"] = _open_pr(
            state,
            ops,
            s["branch"],
            f"criteria-close: {state.ws_id} — штамп accepted",
            f"Штамп приёмки (срез 2a): предложение PR #{e['pr']}, мерж "
            f"{e['merge']['oid']}.",
            criteria_accept.LABEL,
        )
        _record(run_id, key, stamp=s)
    if not s["attested"]:
        if ops.review(state.repo, s["pr"]) != 0:
            return 2
        s["attested"] = True
        _record(run_id, key, stamp=s)
    if ops.merge(state.repo, s["pr"], s["head"]) != 0:
        return 2
    done = _check_and_accept(state, run_id, key, expected, p["base"])
    if done is not None:
        return done
    print("criteria-close: штамп в default не совпал — повторите: будет новый stamp-PR")
    return 2
```

(`ruff format` разрежет длинные строки; смысл не меняется.)

- [ ] **Step 4: Adapt and run the whole file**

Единственная адаптация существующего теста в этой задаче (ревью круга 2
B-M2): `test_new_measurement_closes_stale_pr` считает `create_pr` всего; после
2a второй (`traced`) прогон создаёт ещё и stamp-PR. Счёт заменить на вызовы без
`-stamp` в имени ветки:

```python
    assert len([c for c in ops.calls if c[0] == "create_pr" and "-stamp" not in c[1]]) == 2
```

Иные существующие тесты не правятся; падение вне этого — находка (`Ruling:`).
Замечание для леджера (не правка): `test_post_check_unpublished_key_publishes_first_result`
после 2a проходит через `_pending_acceptance`, а не пост-проверку — пост-проверка
для `traced` остаётся покрытой только для записей без предложения (до 2a).

Run: `uv run -q --frozen --group governance pytest tests/test_governance_criteria_close.py tests/test_governance_criteria_accept.py tests/test_governance_criteria_graph.py -q -p no:cacheprovider`
Expected: PASS все, включая существующие тесты `traced`, упиравшиеся в заглушку в Task 5.

- [ ] **Step 5: Commit**

```bash
git add governance/criteria_close.py tests/test_governance_criteria_close.py
git commit -m "criteria_close: штамп — свой коммит, мерж по своей голове, status: accepted"
```

---

### Task 7: гейт `[x]` — происхождение штампа и граф по пину

Решение владельца 2026-10-02: проверка происхождения — в 2a. Гейт проверяет
акт, связанный с этим закрытием (спека §7.2a п.6), а не текст.

**Files:**
- Create: `governance/acceptance_provenance.py`, `governance/policy_rule.py`
- Modify: `governance/charter_guard.py` (`plan_item_change_findings` в `repo_findings` — M7-2), `governance/approval_facts.py` (константы, `policy_source`, `policy_accounts` — реэкспорт из `policy_rule`), `governance/closure_gate.py` (ветки `traced` и `not-applicable`, `main`: `--repo-slug`, `RealForge`; без `criteria_contract`/`oracle_released`)
- Test: `tests/test_governance_acceptance_provenance.py`, `tests/test_governance_closure_gate.py`, `tests/test_governance_charter_guard.py`

**Interfaces:**
- Consumes: `criteria_accept.stamp_text`, `criteria_graph.build_graph/human_criteria`, `policy_rule.policy_source/policy_accounts`, `frontmatter.split_frontmatter`. **Не** `approval_facts`, `ops.py`, `facts.py`, `criteria_contract`: всё замыкание импорта гейта уходит под authority-root и проверяется инвариантом (Task 7b; ревью круга 6 M6-1 — `approval_facts` на уровне модуля тянул `ops.py` и цепочку незащищённых модулей, тела которых исполняются в процессе гейта). Форджа — собственными вызовами `gh`, учётка агента — константа `AGENT_LOGIN`.
- Produces:
  - `governance/policy_rule.py` (зависит только от `ssot_env`): `APPROVER_ALLOWLIST_ENV`, `POLICY_SOURCE_FILE`, `policy_source() -> (repo, ref, path)`, `policy_accounts(content) -> frozenset[str] | None`; `approval_facts` реэкспортирует все четыре (его потребители не меняются);
  - `acceptance_provenance.Forge` (Protocol): `pr_facts(slug, pr) -> dict | None`, `pr_files(slug, pr) -> list[str] | None`, `default_branch(slug) -> str | None`, `policy_file(repo, sha, path) -> str | None` — None всегда «не установлено»;
  - `RealForge` — через `gh` (`pr view --json state,baseRefName,mergeCommit,mergedBy|files`, `repo view --json defaultBranchRef`, `api repos/{repo}/contents/{path}?ref={sha}`, `api repos/{repo}/compare/{sha}...{ref}`);
  - `human_needed(repo: Path, spec_dir: str, bundle_pin: object) -> bool | None` — по графу узлов на пине;
  - `stamp_findings(repo: Path, spec_dir: str, text: str, *, slug: str | None, forge: Forge | None, agent: str = AGENT_LOGIN) -> list[str]` — пусто = штамп доказан;
  - `closure_gate.gate_findings(repo, *, slug=None, forge=None)` — параметр `oracle_released` и функция `oracle_released()` удалены (M6-2).

- [ ] **Step 1: Write the failing tests** — `tests/test_governance_acceptance_provenance.py`

```python
"""Гейт [x]: происхождение штампа (спека §7.2a п.6, решение владельца 2026-10-02)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from governance import acceptance_provenance as ap
from governance import approval_facts as af
from governance import criteria_accept as ca

SPEC = "workstreams/ws/spec"
REL = f"{SPEC}/90-acceptance-closure.md"
REQ = "#### FR-01: A\n**Priority**: Must\n"
BEH_TEST = (
    "#### BEH-01: a\n`traces: [FR-01]`\n"
    "- **checked_by**: `status: planned` `kind: unit` `owner: qa`\n"
)
BEH_HUMAN = BEH_TEST + (
    "#### BEH-02: b\n`traces: [FR-01]`\n"
    "- **checked_by**: `status: planned` `kind: manual` `owner: qa`\n"
)
ACC_TEST = "#### AC-01: a · verification: test\ntraces: [FR-01]\nscenarios: [BEH-01]\n"
ACC_HUMAN = ACC_TEST + (
    "#### AC-02: b · verification: manual\ntraces: [FR-01]\nscenarios: [BEH-02]\n"
)
SHA = "a" * 40


def _git(repo, *args):
    return subprocess.run(
        [
            "git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
            "-c", "core.autocrlf=false", *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _source():
    repo, _ref, path = af.policy_source()
    return f"github:{repo}@{SHA}:{path}"


class FakeForge:
    def __init__(self, merge, login, files=None, default="master", base="master"):
        self.facts = {
            "state": "MERGED",
            "baseRefName": base,
            "mergeCommit": {"oid": merge},
            "mergedBy": {"login": login},
        }
        self.files = files if files is not None else [REL]
        self.default = default
        self.down = False
        self.on_ref = True

    def pr_facts(self, slug, pr):
        return None if self.down else dict(self.facts)

    def pr_files(self, slug, pr):
        return None if self.down else list(self.files)

    def default_branch(self, slug):
        return None if self.down else self.default

    def policy_file(self, repo, sha, path):
        if self.down or sha != SHA or repo != af.policy_source()[0]:
            return None
        return f"{af.APPROVER_ALLOWLIST_ENV}=owner-human\n"

    def policy_on_ref(self, repo, sha, ref):
        return None if self.down else self.on_ref


def _signed(tmp_path, human=True, source=None):
    """Репо: бандл на пине → предложение (merge) → штамп. → (repo, merge, stamp)."""
    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", "-b", "master", str(repo)], check=True)
    spec = repo / SPEC
    spec.mkdir(parents=True)
    (spec / "10-requirements.md").write_text(REQ)
    (spec / "15-behaviour-spec.md").write_text(BEH_HUMAN if human else BEH_TEST)
    (spec / "25-acceptance.md").write_text(ACC_HUMAN if human else ACC_TEST)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "bundle")
    pin = _git(repo, "rev-parse", "HEAD")
    closure = f"---\nclosure: traced\nbundle_pin: {pin}\n---\nтело\n"
    proposal = ca.proposal_text(closure, source or _source())
    (repo / REL).write_text(proposal)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "proposal")
    merge = _git(repo, "rev-parse", "HEAD")
    stamp = ca.stamp_text(proposal, merge_oid=merge, pr=7)
    (repo / REL).write_text(stamp)
    _git(repo, "commit", "-qam", "stamp")
    return repo, merge, stamp


def _check(repo, text, forge, slug="o/r"):
    return ap.stamp_findings(repo, SPEC, text, slug=slug, forge=forge)


def test_human_signed_stamp_is_green(tmp_path):
    repo, merge, stamp = _signed(tmp_path)
    assert _check(repo, stamp, FakeForge(merge, "owner-human")) == []


def test_test_only_agent_merge_is_green(tmp_path):
    repo, merge, stamp = _signed(tmp_path, human=False)
    assert _check(repo, stamp, FakeForge(merge, "ai-prosto")) == []


def test_agent_merge_with_human_criterion_is_red(tmp_path):
    repo, merge, stamp = _signed(tmp_path)
    assert _check(repo, stamp, FakeForge(merge, "ai-prosto"))


def test_outsider_merge_is_red(tmp_path):
    repo, merge, stamp = _signed(tmp_path, human=False)
    assert _check(repo, stamp, FakeForge(merge, "stranger"))


def test_fabricated_stamp_is_red(tmp_path):
    """Выдуманный штамп: accepted_merge указывает не на предложение."""
    repo, merge, stamp = _signed(tmp_path)
    pin = _git(repo, "rev-parse", "HEAD~2")
    fake = stamp.replace(merge, pin)
    assert _check(repo, fake, FakeForge(pin, "owner-human"))


def test_foreign_pr_is_red(tmp_path):
    """Ссылка на чужое предложение: PR влит человеком, но правит иной файл
    или его мерж-коммит — не accepted_merge."""
    repo, merge, stamp = _signed(tmp_path)
    assert _check(repo, stamp, FakeForge(merge, "owner-human", files=["other.md"]))
    assert _check(repo, stamp, FakeForge("b" * 40, "owner-human"))


def test_edited_after_signature_is_red(tmp_path):
    repo, merge, stamp = _signed(tmp_path)
    assert _check(repo, stamp.replace("тело", "другое тело"), FakeForge(merge, "owner-human"))


def test_pr_into_other_branch_or_not_merged_is_red(tmp_path):
    repo, merge, stamp = _signed(tmp_path)
    assert _check(repo, stamp, FakeForge(merge, "owner-human", base="dev"))
    forge = FakeForge(merge, "owner-human")
    forge.facts["state"] = "CLOSED"
    assert _check(repo, stamp, forge)


def test_unavailable_forge_is_red(tmp_path):
    repo, merge, stamp = _signed(tmp_path)
    forge = FakeForge(merge, "owner-human")
    forge.down = True
    assert _check(repo, stamp, forge)
    assert _check(repo, stamp, None)
    assert _check(repo, stamp, FakeForge(merge, "owner-human"), slug=None)


def test_untrusted_policy_source_is_red(tmp_path):
    """Ревью круга 4 R4-m2: чужой источник — в самом ПРЕДЛОЖЕНИИ (штамп честный)."""
    evil = f"github:evil/policy@{SHA}:policy/approvers.env"
    repo, merge, stamp = _signed(tmp_path, source=evil)
    assert _check(repo, stamp, FakeForge(merge, "owner-human"))


def test_policy_sha_off_ref_is_red(tmp_path):
    """R4-m1: SHA политики не в истории APPROVAL_POLICY_REF (ветка, форк)."""
    repo, merge, stamp = _signed(tmp_path)
    forge = FakeForge(merge, "owner-human")
    forge.on_ref = False
    assert _check(repo, stamp, forge)


def _add_manual_ac(repo):
    spec = repo / SPEC
    (spec / "15-behaviour-spec.md").write_text(BEH_HUMAN)
    (spec / "25-acceptance.md").write_text(ACC_HUMAN)
    _git(repo, "commit", "-qam", "manual AC")


def test_stale_test_only_stamp_after_manual_ac_is_red(tmp_path):
    """Ревью круга 4 R4-B1: штамп test-only версии, затем в бандл добавлен
    ручной AC — штамп не для текущего бандла."""
    repo, merge, stamp = _signed(tmp_path, human=False)
    _add_manual_ac(repo)
    assert _check(repo, stamp, FakeForge(merge, "ai-prosto"))


def test_old_test_only_pin_in_new_proposal_is_red(tmp_path):
    """R4-B1, атака: бандл уже с ручным AC, агент пишет предложение со старым
    test-only пином и мержит его сам."""
    repo, _merge, _stamp = _signed(tmp_path, human=False)
    old_pin = _git(repo, "rev-parse", "HEAD~2")
    _add_manual_ac(repo)
    closure = f"---\nclosure: traced\nbundle_pin: {old_pin}\n---\nтело\n"
    proposal = ca.proposal_text(closure, _source())
    (repo / REL).write_text(proposal)
    _git(repo, "commit", "-qam", "proposal with old pin")
    merge = _git(repo, "rev-parse", "HEAD")
    stamp = ca.stamp_text(proposal, merge_oid=merge, pr=8)
    (repo / REL).write_text(stamp)
    _git(repo, "commit", "-qam", "stamp")
    assert _check(repo, stamp, FakeForge(merge, "ai-prosto"))
    assert ap.pin_current(repo, SPEC, old_pin) is False


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ('{"status": "ahead"}', True),
        ('{"status": "identical"}', True),
        ('{"status": "behind"}', False),
        ('{"status": "diverged"}', False),
        ('{"status": "weird"}', None),
        ("not json", None),
        (None, None),
    ],
)
def test_real_forge_policy_on_ref_semantics(monkeypatch, payload, expected):
    """R5-M1: sha...ref — `ahead`/`identical` значат «sha в истории ref»."""
    monkeypatch.setattr(ap, "_gh", lambda *a: payload)
    assert ap.RealForge().policy_on_ref("o/p", SHA, "main") is expected


def test_real_forge_policy_file_decodes_base64(monkeypatch):
    import base64 as b64

    body = b64.b64encode(f"{af.APPROVER_ALLOWLIST_ENV}=x\n".encode()).decode()
    monkeypatch.setattr(
        ap, "_gh", lambda *a: f'{{"encoding": "base64", "content": "{body}"}}'
    )
    expected = f"{af.APPROVER_ALLOWLIST_ENV}=x\n"
    assert ap.RealForge().policy_file("o/p", SHA, "p") == expected
    monkeypatch.setattr(ap, "_gh", lambda *a: None)
    assert ap.RealForge().policy_file("o/p", SHA, "p") is None


def test_crlf_nodes_are_current(tmp_path):
    """Ревью круга 6 m6-2: узел с CRLF — не ложный «не текущий бандл»."""
    repo, _merge, _stamp = _signed(tmp_path)
    node = repo / SPEC / "10-requirements.md"
    node.write_bytes(REQ.replace("\n", "\r\n").encode())
    _git(repo, "commit", "-qam", "crlf")
    assert ap.pin_current(repo, SPEC, _git(repo, "rev-parse", "HEAD")) is True


def test_non_sha_refs_never_reach_git(tmp_path):
    """R4-m5: ссылка из файла — только SHA (не опция git)."""
    repo, merge, stamp = _signed(tmp_path)
    assert _check(repo, stamp.replace(merge, "--output=x"), FakeForge(merge, "owner-human"))
    assert ap.human_needed(repo, SPEC, "--output=x") is None
    assert ap.pin_current(repo, SPEC, "--output=x") is None


@pytest.mark.parametrize(("human", "need"), [(True, True), (False, False)])
def test_human_needed_by_graph_at_pin(tmp_path, human, need):
    repo, _merge, _stamp = _signed(tmp_path, human=human)
    pin = _git(repo, "rev-parse", "HEAD~2")
    assert ap.human_needed(repo, SPEC, pin) is need
    assert ap.human_needed(repo, SPEC, "f" * 40) is None
    assert ap.human_needed(repo, SPEC, None) is None


def test_policy_accounts():
    key = af.APPROVER_ALLOWLIST_ENV
    assert af.policy_accounts(f"{key}=a, b\n") == frozenset({"a", "b"})
    assert af.policy_accounts("") is None
    assert af.policy_accounts(f"{key}= , \n") is None
    assert af.policy_accounts(f"{key}=a\n{key}=b\n") is None
```

В `tests/test_governance_closure_gate.py` — снять `oracle_released` (M6-2):
удалить `test_oracle_released_false_when_min_version_pending`,
`…_true_when_min_version_numeric`, `…_false_when_not_vendored`,
`test_na_spec_runner_version_not_flagged_while_oracle_pending` и хелпер
`vendor`, если он больше не используется; во всех вызовах `gate_findings`
убрать аргумент `oracle_released=`; `test_gate_table` — без колонки
`oracle_released`, строки: нет файла → красный; `blocked` → красный;
`traced` + `human_pending: 0` → красный (граф не прочитан); n/a `schema-1` →
красный; `NA_SR` → красный; `test_na_spec_runner_version_flagged_once_oracle_released`
переименовать в `test_na_spec_runner_version_always_red` и очистить тело от
`vendor`/`contract_dir` (вызов `gate_findings` по `make(..., closure=NA_SR)`);
удалить хелпер `vendor` и ставшие лишними импорты `hashlib`/`json`;
`test_na_version_names_version_and_host_in_error_and_warning` — только
ошибка (одним вызовом); `test_gate_reads_a_real_rendered_closure` — ожидание
`errors` непусто (n/a `spec-runner-version`); `test_language_is_green_with_visible_warning`
— с подменой `pin_current → True`, `human_needed → False`. Новые:

```python
@pytest.mark.parametrize(
    ("current", "need", "red"),
    [(True, False, False), (True, True, True), (True, None, True), (False, None, True), (None, None, True)],
)
def test_language_na_checks_graph(tmp_path, monkeypatch, current, need, red):
    monkeypatch.setattr(g.acceptance_provenance, "pin_current", lambda *a: current)
    monkeypatch.setattr(g.acceptance_provenance, "human_needed", lambda *a: need)
    na = "closure: not-applicable\nnot_applicable_reason: language\nbundle_pin: p"
    errors, _ = g.gate_findings(make(tmp_path, done=True, closure=na))
    assert bool(errors) is red


@pytest.mark.parametrize("reason", ["foo", "null", "''"])
def test_unknown_na_reason_is_red(tmp_path, reason):
    na = f"closure: not-applicable\nnot_applicable_reason: {reason}"
    errors, _ = g.gate_findings(make(tmp_path, done=True, closure=na))
    assert errors
```

Далее — правило без `status` теперь по графу
(в `make` гит-репо нет → граф не прочитан → **красный**: «непрочитанный граф не
означает “ручных критериев нет”»). Строку таблицы `test_gate_table`
`("closure: traced\nhuman_pending: 0", False, False)` заменить на
`("closure: traced\nhuman_pending: 0", False, True)`; `test_warnings_visible_for_human_pending`
заменить на `test_slice1_closure_without_graph_is_red` (ожидание: ошибка
«граф бандла на пине не прочитан»). Добавить:

```python
def test_slice1_test_only_closure_green_with_warning_by_graph(tmp_path, monkeypatch):
    monkeypatch.setattr(g.acceptance_provenance, "pin_current", lambda *a: True)
    monkeypatch.setattr(g.acceptance_provenance, "human_needed", lambda *a: False)
    errors, warns = g.gate_findings(
        make(tmp_path, done=True, closure="closure: traced\nbundle_pin: p"),
    )
    assert errors == [] and any("без штампа" in w for w in warns)


def test_slice1_closure_with_human_criteria_by_graph_is_red(tmp_path, monkeypatch):
    monkeypatch.setattr(g.acceptance_provenance, "pin_current", lambda *a: True)
    monkeypatch.setattr(g.acceptance_provenance, "human_needed", lambda *a: True)
    errors, _ = g.gate_findings(
        make(tmp_path, done=True, closure="closure: traced\nhuman_pending: 0"),
    )
    assert any("подпись человека не получена" in e for e in errors)


def test_slice1_closure_for_stale_bundle_is_red(tmp_path, monkeypatch):
    """R4-B1 в старом формате: пин не на текущий бандл — красный."""
    monkeypatch.setattr(g.acceptance_provenance, "pin_current", lambda *a: False)
    errors, _ = g.gate_findings(
        make(tmp_path, done=True, closure="closure: traced\nbundle_pin: p"),
    )
    assert any("не для текущего бандла" in e for e in errors)


@pytest.mark.parametrize(
    ("closure", "red"),
    [
        ("closure: traced\nstatus: proposed", True),
        ("closure: traced\nstatus: weird", True),
    ],
)
def test_traced_non_accepted_status_is_red(tmp_path, closure, red):
    errors, _ = g.gate_findings(
        make(tmp_path, done=True, closure=closure)
    )
    assert bool(errors) is red


def test_accepted_delegates_to_provenance(tmp_path, monkeypatch):
    seen = {}

    def fake(repo, spec_dir, text, *, slug, forge, **kw):
        seen.update(spec_dir=spec_dir, slug=slug, forge=forge)
        return ["нет акта"]

    monkeypatch.setattr(g.acceptance_provenance, "stamp_findings", fake)
    errors, _ = g.gate_findings(
        make(tmp_path, done=True, closure="closure: traced\nstatus: accepted"),
        slug="o/r",
        forge="F",
    )
    assert any("нет акта" in e for e in errors)
    assert seen == {"spec_dir": "workstreams/ws-a/spec", "slug": "o/r", "forge": "F"}
```

В `tests/test_governance_charter_guard.py` (M7-2: перепривязка `plan_item`
снимала гейт с пункта):

```python
def test_plan_item_change_on_schema2_is_finding():
    base = cg.Charter(2, "ENC", "todo://alpha/x")
    assert cg.plan_item_change_findings("ws", base, cg.Charter(2, "ENC", "todo://alpha/y"))
    assert cg.plan_item_change_findings("ws", base, base) == []
    assert cg.plan_item_change_findings("ws", None, base) == []  # новый charter
    assert cg.plan_item_change_findings("ws", cg.Charter(1, None, None), base) == []
```

(Модуль импортирован в этом файле как `cg` — сверить с существующим импортом.)

- [ ] **Step 2: Run to verify fail**

Run: `uv run -q --frozen --group governance pytest tests/test_governance_acceptance_provenance.py tests/test_governance_closure_gate.py tests/test_governance_charter_guard.py -q -p no:cacheprovider`
Expected: FAIL — нет модуля `acceptance_provenance`, нет `policy_accounts`, нет `plan_item_change_findings`; гейт без `status` ещё по `human_pending`.

- [ ] **Step 3: Implement**

`governance/policy_rule.py` (новый; под authority-root, импортирует только `ssot_env`):

```python
"""policy_rule — источник и состав политики подписи (правило гейта [x]).

Вынесено из `approval_facts` (ревью круга 6 M6-1): тот на уровне модуля
импортирует `ops`/`facts`, и гейт, читая политику через него, исполнял бы
незащищённый код. Здесь — только то, что решает доверие: координаты
источника из SSOT под authority-root и разбор состава.
"""

from __future__ import annotations

from pathlib import Path

from governance import ssot_env

#: Ключ политики в `policy/approvers.env` и имя переменной, выставление
#: которой — отказ (approval-policy S7). Одно имя в двух местах — намеренно.
APPROVER_ALLOWLIST_ENV = "AUTHORIZED_APPROVER_ACCOUNTS"

#: Координаты источника политики — SSOT под authority-root (S8).
POLICY_SOURCE_FILE = (
    Path(__file__).resolve().parent.parent
    / "contracts"
    / "approval-policy-source"
    / "v1"
    / "source.env"
)


def policy_source() -> tuple[str, str, str]:
    """(repo, ref, path) из SSOT под authority-root; RuntimeError на битом файле."""
    what = "SSOT источника политики подписи"
    return (
        ssot_env.read_key(POLICY_SOURCE_FILE, "APPROVAL_POLICY_REPO", what),
        ssot_env.read_key(POLICY_SOURCE_FILE, "APPROVAL_POLICY_REF", what),
        ssot_env.read_key(POLICY_SOURCE_FILE, "APPROVAL_POLICY_PATH", what),
    )


def policy_accounts(content: str) -> frozenset[str] | None:
    """Состав политики из `approvers.env` (тем же правилом, что `policy_snapshot`):
    ключа нет, дубль или ни одного логина — None."""
    lines = ssot_env.definition_lines(content, APPROVER_ALLOWLIST_ENV)
    if len(lines) != 1 or not lines[0]:
        return None
    accounts = frozenset(p.strip() for p in lines[0].split(",") if p.strip())
    return accounts or None
```

`governance/approval_facts.py`: определения `APPROVER_ALLOWLIST_ENV`,
`POLICY_SOURCE_FILE` и `policy_source` заменить реэкспортом (комментарии к
ним перенести в `policy_rule`):

```python
from governance.policy_rule import (  # noqa: F401 — реэкспорт для потребителей
    APPROVER_ALLOWLIST_ENV,
    POLICY_SOURCE_FILE,
    policy_accounts,
    policy_source,
)
```

Потребители `approval_facts.policy_source`/`APPROVER_ALLOWLIST_ENV`
(`criteria_close`, `approve_node`, тесты) не меняются. Тесты, подменяющие
`approval_facts.policy_source` через `monkeypatch.setattr`, продолжают работать
для кода, читающего его через `approval_facts` (в т.ч. `policy_snapshot`).

`governance/acceptance_provenance.py`:

```python
"""acceptance_provenance — происхождение штампа для гейта [x] (спека §7.2a п.6).

Зелёный только при установленном акте, связанном с ЭТИМ закрытием:
штамп = подписанное предложение (в accepted_merge) с разрешёнными правками;
PR accepted_pr этого репо влит в default, его мерж-коммит = accepted_merge,
правит ровно этот файл; политика — из доверенного источника по пину
предложения; нужен ли человек — по графу на bundle_pin; подписант
предложения — человек из снимка (или, только test, агент). Любой
неустановленный факт — находка (красный).
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
from pathlib import Path
from typing import Protocol

from governance import criteria_accept, criteria_graph, policy_rule
from governance.frontmatter import split_frontmatter

#: Учётка агента. Здесь, а не из `ops.py`: модуль и всё, что решает правило
#: гейта, — под authority-root (решение владельца 2026-10-02); `ops.py` туда
#: не входит, и константа в нём ослаблялась бы агентским PR.
AGENT_LOGIN = "ai-prosto"

CLOSURE_NAME = "90-acceptance-closure.md"
_NODES = ("10-requirements.md", "15-behaviour-spec.md", "25-acceptance.md")
_SOURCE = re.compile(r"^github:(?P<repo>[^@]+)@(?P<sha>[0-9a-f]{40}):(?P<path>.+)$")
_SHA = re.compile(r"^[0-9a-f]{40}$")  # ссылки из файла — только SHA, не опции git


class Forge(Protocol):
    """Факты форджи для гейта; None — не установлено."""

    def pr_facts(self, slug: str, pr: int) -> dict | None: ...

    def pr_files(self, slug: str, pr: int) -> list[str] | None: ...

    def default_branch(self, slug: str) -> str | None: ...

    def policy_file(self, repo: str, sha: str, path: str) -> str | None: ...

    def policy_on_ref(self, repo: str, sha: str, ref: str) -> bool | None: ...


def _gh(*args: str) -> str | None:
    try:
        done = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    except OSError:
        return None
    return done.stdout if done.returncode == 0 else None


def _gh_json(*args: str) -> object | None:
    out = _gh(*args)
    try:
        return json.loads(out) if out is not None else None
    except json.JSONDecodeError:
        return None


class RealForge:
    """Форджа через `gh` (в CI — `GH_TOKEN`); любой сбой — None. Собственные
    вызовы, не `ops.py`: зависимости правила гейта — под authority-root."""

    def pr_facts(self, slug: str, pr: int) -> dict | None:
        v = _gh_json(
            "pr", "view", str(pr), "-R", slug,
            "--json", "state,baseRefName,mergeCommit,mergedBy",
        )
        return v if isinstance(v, dict) else None

    def pr_files(self, slug: str, pr: int) -> list[str] | None:
        v = _gh_json("pr", "view", str(pr), "-R", slug, "--json", "files")
        files = v.get("files") if isinstance(v, dict) else None
        if not isinstance(files, list):
            return None
        paths = [f.get("path") for f in files if isinstance(f, dict)]
        return paths if all(isinstance(p, str) for p in paths) else None

    def default_branch(self, slug: str) -> str | None:
        v = _gh_json("repo", "view", slug, "--json", "defaultBranchRef")
        ref = v.get("defaultBranchRef") if isinstance(v, dict) else None
        name = ref.get("name") if isinstance(ref, dict) else None
        return name if isinstance(name, str) and name else None

    def policy_file(self, repo: str, sha: str, path: str) -> str | None:
        v = _gh_json("api", f"repos/{repo}/contents/{path}?ref={sha}")
        if not isinstance(v, dict) or v.get("encoding") != "base64":
            return None
        try:
            return base64.b64decode(str(v.get("content", ""))).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None

    def policy_on_ref(self, repo: str, sha: str, ref: str) -> bool | None:
        """SHA политики — в истории `ref`. GitHub compare `BASE...HEAD` даёт
        статус HEAD относительно BASE: при BASE=sha, HEAD=ref «sha — предок
        ref» ⇔ `ahead`/`identical` (ревью круга 5 R5-M1: было перевёрнуто)."""
        v = _gh_json("api", f"repos/{repo}/compare/{sha}...{ref}?per_page=1")
        status = v.get("status") if isinstance(v, dict) else None
        if status in ("ahead", "identical"):
            return True
        if status in ("behind", "diverged"):
            return False
        return None


def _show(repo: Path, ref: str, path: str) -> str | None:
    try:
        # text=True — universal newlines, как у `read_text` в `pin_current`
        # (ревью круга 6 m6-2: иначе CRLF-узел давал ложный красный)
        done = subprocess.run(
            ["git", "-C", str(repo), "show", f"{ref}:{path}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except UnicodeDecodeError:  # R5 m-c: находка, не трейсбек
        return None
    return done.stdout if done.returncode == 0 else None


def pin_current(repo: Path, spec_dir: str, bundle_pin: object) -> bool | None:
    """Узлы на пине = узлам проверяемой ревизии (рабочее дерево гейта).

    Пин пишет автор файла (для test-only — агент): без этой сверки старый
    test-only пин или устаревший штамп прятали бы ручные критерии текущего
    бандла (ревью круга 4 R4-B1). None — не прочитано."""
    if not isinstance(bundle_pin, str) or not _SHA.match(bundle_pin):
        return None
    for n in _NODES:
        at_pin = _show(repo, bundle_pin, f"{spec_dir}/{n}")
        here = repo / spec_dir / n
        if at_pin is None or not here.is_file():
            return None
        try:
            current = here.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return None
        if at_pin != current:
            return False
    return True


def human_needed(repo: Path, spec_dir: str, bundle_pin: object) -> bool | None:
    """Нужен ли человек — по графу узлов на пине (§7.2a п.1); None — не прочитан."""
    if not isinstance(bundle_pin, str) or not _SHA.match(bundle_pin):
        return None
    texts = [_show(repo, bundle_pin, f"{spec_dir}/{n}") for n in _NODES]
    if any(t is None for t in texts):
        return None
    req, beh, acc = (t or "" for t in texts)
    return criteria_graph.human_criteria(criteria_graph.build_graph(req, beh, acc)) > 0


def _trusted_accounts(source: object, forge: Forge) -> frozenset[str] | None:
    """Состав политики по пину предложения — только из SSOT-источника."""
    m = _SOURCE.match(source) if isinstance(source, str) else None
    if m is None:
        return None
    try:
        repo, _ref, path = policy_rule.policy_source()
    except RuntimeError:
        return None
    if (m["repo"], m["path"]) != (repo, path):
        return None
    if forge.policy_on_ref(repo, m["sha"], _ref) is not True:
        return None
    content = forge.policy_file(repo, m["sha"], path)
    return policy_rule.policy_accounts(content) if content is not None else None


def stamp_findings(
    repo: Path,
    spec_dir: str,
    text: str,
    *,
    slug: str | None,
    forge: Forge | None,
    agent: str = AGENT_LOGIN,
) -> list[str]:
    """Пусто — штамп доказан актом, связанным с этим закрытием.

    `agent` — константа учётки агента (`AGENT_LOGIN`); предикат
    `criteria_close` берёт её из профиля (`ops.agent_login()`). Разойтись они
    могут только в ложный красный, не в зелёный (ревью круга 4 R4-m7)."""
    rel = f"{spec_dir}/{CLOSURE_NAME}"
    try:
        meta, _ = split_frontmatter(text)
    except ValueError:
        return ["frontmatter штампа не разбирается"]
    merge, pr = meta.get("accepted_merge"), meta.get("accepted_pr")
    if not isinstance(merge, str) or not _SHA.match(merge) or not isinstance(pr, int):
        return ["штамп без accepted_merge (SHA) / accepted_pr"]
    proposal = _show(repo, merge, rel)
    if proposal is None:
        return [f"предложение в {merge[:12]} не прочитано (история git?)"]
    try:
        expected = criteria_accept.stamp_text(proposal, merge_oid=merge, pr=pr)
    except ValueError:
        return [f"в {merge[:12]} не предложение (status ≠ proposed)"]
    if expected != text:
        return ["штамп ≠ подписанному предложению (правка после подписи?)"]
    if slug is None or forge is None:
        return ["форджа/репозиторий не установлены — происхождение не проверить"]
    facts = forge.pr_facts(slug, pr)
    files = forge.pr_files(slug, pr)
    default = forge.default_branch(slug)
    if facts is None or files is None or default is None:
        return [f"факты PR #{pr} не получены"]
    commit = facts.get("mergeCommit")
    oid = commit.get("oid") if isinstance(commit, dict) else None
    out = [
        why
        for bad, why in (
            (facts.get("state") != "MERGED", f"PR #{pr} не влит"),
            (facts.get("baseRefName") != default, f"PR #{pr} влит не в {default}"),
            (oid != merge, f"accepted_merge ≠ мерж-коммиту PR #{pr}"),
            (files != [rel], f"PR #{pr} правит не только {rel}: {files}"),
        )
        if bad
    ]
    if out:
        return out
    pmeta, _ = split_frontmatter(proposal)
    current = pin_current(repo, spec_dir, pmeta.get("bundle_pin"))
    if current is None:
        return ["узлы бандла на пине предложения не прочитаны"]
    if not current:
        return ["штамп не для текущего бандла: узлы на пине ≠ узлам ревизии"]
    accounts = _trusted_accounts(pmeta.get("policy_source"), forge)
    if accounts is None:
        return ["политика по пину предложения не прочитана из доверенного источника"]
    need = human_needed(repo, spec_dir, pmeta.get("bundle_pin"))
    if need is None:
        return ["граф бандла на пине не прочитан"]
    by = facts.get("mergedBy")
    login = by.get("login") if isinstance(by, dict) else None
    if not isinstance(login, str) or not login:
        return [f"подписант PR #{pr} не установлен"]
    if need and (login == agent or login not in accounts):
        return [f"PR #{pr} влит {login} — не человек из снимка политики"]
    if not need and login != agent and login not in accounts:
        return [f"PR #{pr} влит {login} — ни агент, ни учётка снимка"]
    return []
```

`governance/charter_guard.py`, рядом с `code_change_findings` (тот же контракт):

```python
def plan_item_change_findings(ws_id: str, base: Charter | None, head: Charter) -> list[str]:
    """`plan_item` charter'а схемы 2 неизменяем против базы, как `code`
    (ревью круга 7 M7-2): перепривязка снимала гейт [x] с пункта, ради
    которого бандл существует, — `[x]` без подписи одним агентским PR."""
    if base is None or base.schema != 2 or head.schema != 2:
        return []
    if base.plan_item == head.plan_item:
        return []
    return [
        f"{ws_id}: plan_item схемы 2 неизменяем ({base.plan_item} → "
        f"{head.plan_item}); новый пункт — новый воркстрим"
    ]
```

В `repo_findings` — рядом с вызовом `code_change_findings` для той же пары
(base, head) — тем же образом добавить `plan_item_change_findings`.

`governance/closure_gate.py`: импорты `os`, `subprocess`; `from governance import charter_guard, criteria_contract` → `from governance import acceptance_provenance, charter_guard` (гейту `criteria_contract` больше не нужен — M6-2); функцию `oracle_released()` удалить. Сигнатура
`gate_findings(repo: Path, *, slug: str | None = None, forge: acceptance_provenance.Forge | None = None)`.

Ветку `elif state == "not-applicable":` заменить (M6-2: «не применим» — путь
к `[x]` без подписи, поэтому словарь закрыт и граф сверяется так же):

```python
        elif state == "not-applicable":
            reason = meta.get("not_applicable_reason")
            where = f"spec-runner {meta.get('spec_runner_version')} на {meta.get('host')}"
            spec_dir = charter_path.parent.relative_to(repo).as_posix()
            if reason == "spec-runner-version":
                # оракул выпущен с spec-runner v4.5.0 — всегда перегнать
                errors.append(
                    f"{ws}: not-applicable spec-runner-version ({where}) — перегнать "
                    "закрытие на машине с spec-runner ≥ min"
                )
            elif reason == "schema-1":
                errors.append(
                    f"{ws}: charter схемы 2, а закрытие — schema-1: перегнать закрытие"
                )
            elif reason == "language":
                pin = meta.get("bundle_pin")
                current = acceptance_provenance.pin_current(repo, spec_dir, pin)
                need = (
                    acceptance_provenance.human_needed(repo, spec_dir, pin)
                    if current
                    else None
                )
                if current is False:
                    errors.append(f"{ws}: закрытие не для текущего бандла")
                elif need is None:
                    errors.append(f"{ws}: граф бандла на пине не прочитан")
                elif need:
                    errors.append(
                        f"@id:{m.group(2)} [x], но у {ws} есть человеческие критерии, "
                        "а оракул не применим (language) — путь подписи для n/a в 2b"
                    )
                else:
                    warns.append(f"{ws}: оракул не применим (language)")
            else:
                errors.append(
                    f"{ws}: not_applicable_reason {reason!r} вне словаря "
                    "language|schema-1|spec-runner-version"
                )
```
Ветку `elif state == "traced":` заменить:

```python
        elif state == "traced":
            status = meta.get("status")
            spec_dir = charter_path.parent.relative_to(repo).as_posix()
            if status == "accepted":
                errors += [
                    f"{ws}: {why}"
                    for why in acceptance_provenance.stamp_findings(
                        repo,
                        spec_dir,
                        closure_path.read_text(),
                        slug=slug,
                        forge=forge,
                    )
                ]
            elif status is not None:
                errors.append(
                    f"@id:{m.group(2)} [x], но приёмка {ws} не завершена "
                    f"(status: {status!r} — нет штампа accepted)"
                )
            else:
                pin = meta.get("bundle_pin")
                current = acceptance_provenance.pin_current(repo, spec_dir, pin)
                need = (
                    acceptance_provenance.human_needed(repo, spec_dir, pin)
                    if current
                    else None
                )
                if current is False:
                    errors.append(f"{ws}: закрытие не для текущего бандла (узлы на пине ≠ ревизии)")
                elif need is None:
                    errors.append(f"{ws}: граф бандла на пине не прочитан — закрытие среза 1 без штампа")
                elif need:
                    errors.append(
                        f"@id:{m.group(2)} [x], но у {ws} есть человеческие критерии — "
                        "подпись человека не получена (закрытие среза 1 без штампа)"
                    )
                else:
                    warns.append(f"{ws}: закрытие среза 1 без штампа (только test-критерии)")
```

`main`: аргумент `--repo-slug` (умолчание — `GITHUB_REPOSITORY`, иначе `owner/name` из `git remote get-url origin`, иначе None) и `forge=acceptance_provenance.RealForge()`:

```python
def _origin_slug(repo: Path) -> str | None:
    done = subprocess.run(
        ["git", "-C", str(repo), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", done.stdout.strip())
    return m.group(1) if done.returncode == 0 and m else None
```

```python
    parser.add_argument("--repo-slug")
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    slug = args.repo_slug or os.environ.get("GITHUB_REPOSITORY") or _origin_slug(repo)
    errors, warns = gate_findings(
        repo, slug=slug, forge=acceptance_provenance.RealForge()
    )
```

Docstring модуля: «С 2a `traced` зелёный только со штампом, доказанным актом
(`acceptance_provenance`, спека §7.2a п.6); файл среза 1 без `status` —
зелёный с предупреждением лишь при графе на пине без человеческих критериев.
Нужны история git и чтение форджи (в CI — Task 9).»

- [ ] **Step 4: Run tests** — команда Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add governance/acceptance_provenance.py governance/policy_rule.py governance/approval_facts.py governance/charter_guard.py governance/closure_gate.py tests/test_governance_acceptance_provenance.py tests/test_governance_closure_gate.py tests/test_governance_charter_guard.py
git commit -m "closure_gate: происхождение штампа — акт, связанный с закрытием (срез 2a)"
```

---

### Task 7b: authority-root — гейт и модули, определяющие его правило

Решение владельца 2026-10-02: гейт обеспечивает обязательность человеческой
подписи, поэтому изменение его правил мержит человек; вместе с ним — модули,
через которые правило можно ослабить.

**Files:**
- Modify: `contracts/authority-root/v1/paths.env` (префиксы + абзац обоснования)
- Modify: `tests/test_governance_authority_root.py` (поимённый перечень с комментарием на каждую строку; инвариант замыкания импорта)

| модуль | что решает в правиле гейта |
|---|---|
| `governance/closure_gate.py` | какие пункты `[x]` и как проверяются |
| `governance/acceptance_provenance.py` | происхождение штампа, пин — текущий бандл, подписант |
| `governance/criteria_accept.py` | какие поля штамп вправе менять (`stamp_text`) |
| `governance/criteria_graph.py` | нужен ли человек (`human_criteria`, `build_graph`) |
| `governance/acceptance_guard.py` | приоритеты требований → что Won't и выпадает из счёта |
| `governance/frontmatter.py` | разбор/сериализация, на которых стоит сверка «штамп = предложение» |
| `governance/charter_guard.py` | какие charter'ы и пункты плана гейт видит |
| `governance/policy_rule.py` | источник политики и состав подписантов (вынесено из `approval_facts`) |
| `governance/ssot_env.py` | парсер SSOT и файла политики |
| `governance/__init__.py` | исполняется при любом импорте `governance.*` |

Сознательно вне перечня (назвать в абзаце `paths.env`): `criteria_close.py`
(выпускает предложение и штамп, но его ослабление ловит сам гейт по фактам
форджи); `approval_facts.py`, `ops.py`, `facts.py`, `criteria_contract.py` —
вне замыкания импорта гейта (Task 7: `policy_rule`, собственные вызовы `gh`,
`oracle_released` снят). Полнота — не таблицей, а инвариантом: замыкание
`governance.*` гейта ⊆ authority-root. Названная граница (не защищается):
сторонние пакеты (`yaml`) и их версии (`pyproject.toml`/`uv.lock`), запуск
интерпретатора (`.pth`, `sitecustomize`) — цепочка поставки. Цена: будущие правки
`frontmatter.py`, `charter_guard.py`, `criteria_graph.py` (ими пользуются
раннер и мост) тоже мержит человек.

- [ ] **Step 1: Write the failing test** — в `tests/test_governance_authority_root.py` в поимённый перечень (рядом с `"governance/halt_gate.py"`) добавить десять строк таблицы с комментарием «оракул бандла, срез 2a: правило гейта [x] — подпись человека (решение владельца 2026-10-02)», и инвариант (ревью круга 6 M6-1: полнота — механически, не таблицей):

```python
def _gate_process_modules(root: Path) -> list[tuple[str, str]]:
    """(имя, файл) модулей, загруженных гейтом в форме запуска CI (Task 9):
    `python -I` — без env, user site и cwd в sys.path; корень репо — В КОНЕЦ
    sys.path, чтобы модуль в корне не затенял stdlib/`yaml` (M7-1)."""
    probe = (
        "import sys, runpy\n"
        f"sys.path.append({str(root)!r})\n"
        "import governance.closure_gate\n"
        "for n, m in sorted(sys.modules.items()):\n"
        "    f = getattr(m, '__file__', None)\n"
        "    if f: print(n, f)\n"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", probe],
        cwd="/",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return [tuple(line.split(" ", 1)) for line in out]


def test_gate_import_closure_is_authority_root() -> None:
    """Класс «незащищённый код в процессе гейта» (ревью кругов 6–7): модули
    репо, загруженные гейтом, ⊆ authority-root; сторонние — только `yaml`;
    stdlib не затенён модулем из репо. Новый импорт в модуле правила
    краснеет здесь, а не уходит молча."""
    root = Path(__file__).resolve().parent.parent
    mods = _gate_process_modules(root)
    in_repo = sorted(
        os.path.relpath(f, root)
        for _n, f in mods
        if Path(f).resolve().is_relative_to(root)
        and ".venv" not in Path(f).resolve().relative_to(root).parts
    )
    assert "governance/closure_gate.py" in in_repo
    assert "governance/acceptance_provenance.py" in in_repo
    unprotected = sorted(set(in_repo) - set(authority_root.touched(in_repo)))
    assert unprotected == [], unprotected
    third = sorted(
        {n.split(".")[0] for n, f in mods if "site-packages" in f}
    )
    assert set(third) <= {"yaml", "_yaml"}, third
    # A5 (m7-4): ленивые импорты внутри функций модулей правила — тоже
    for rel in in_repo:
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module == "governance":
                names = [f"governance.{a.name}" for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.startswith("governance."):
                    path = name.replace(".", "/") + ".py"
                    assert authority_root.touched([path]) == [path], (rel, name)


def test_root_module_does_not_shadow_stdlib_in_gate_launch(tmp_path) -> None:
    """M7-1: в форме запуска CI модуль `re.py` в корне не исполняется."""
    (tmp_path / "re.py").write_text("raise SystemExit('SHADOW')\n")
    probe = f"import sys; sys.path.append({str(tmp_path)!r}); import re; print(re.__file__)"
    out = subprocess.run(
        [sys.executable, "-I", "-c", probe],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert str(tmp_path) not in out


def test_criteria_close_stays_agent_mergeable() -> None:
    """Выпуск предложения/штампа — не правило гейта: его ослабление ловит гейт."""
    assert authority_root.touched(["governance/criteria_close.py"]) == []
```

(Импорты в тест-файле: `ast`, `os`, `subprocess`, `sys`, `Path` — добавить, если их нет.)

- [ ] **Step 2: Run to verify fail**

Run: `uv run -q --frozen --group governance pytest tests/test_governance_authority_root.py tests/test_merge_pr.py -q -p no:cacheprovider`
Expected: FAIL — пути не в перечне.

- [ ] **Step 3: Implement** — в `paths.env` дописать десять префиксов таблицы в `AUTHORITY_ROOT_PREFIXES` и абзац комментария (что решает каждый, что сознательно вне перечня и почему, инвариант замыкания, названная граница цепочки поставки, цена). Если инвариант покажет модуль вне таблицы — это находка: либо развязать гейт от него, либо внести с обоснованием (`Ruling:` в леджер).

- [ ] **Step 4: Run tests** — команда Step 2, затем `GOVERNANCE_REQUIRED=1 uv run --frozen --group governance pytest -q -p no:cacheprovider` (перечень читают `accept_pr`, раннер и `merge-pr.sh`). Expected: PASS; `test_merge_pr` — новые пути в `merge-pr.sh` литералами не упоминаются.

- [ ] **Step 5: Commit**

```bash
git add contracts/authority-root/v1/paths.env tests/test_governance_authority_root.py
git commit -m "authority-root: гейт [x] и модули его правила — мерж человеком (срез 2a)"
```

---

### Task 8: документация, полный прогон, PR кода

**Files:**
- Modify: `CLAUDE.md` (строка `governance/criteria_close.py` в таблице инструментов), `Makefile` (строка help `criteria-close`)

- [ ] **Step 1: Docs**

`CLAUDE.md`, строка `governance/criteria_close.py`: после «файл `90-acceptance-closure.md` агентским PR …» вставить: «Срез 2a (спека §7.2a): закрытие `traced` — предложение (`status: proposed`, снимок политики, свой коммит на ветке `…-proposal`); PR с человеческими критериями (`human_criteria` по графу) — метка `human-merge-required`, мержит человек из снимка (выход 4 — ждём); приёмку ведёт `_advance` по фактам форджи: предикат §3.5, stamp-PR `status: accepted`; выходы 0/2/4/5/6; незавершённое закрытие среза 1 мигрирует в предложение без перемера. Гейт `[x]` на `traced` проверяет происхождение штампа (`acceptance_provenance`: акт, связанный с закрытием; политика по пину из SSOT; граф по пину) — нужны история git и чтение форджи.»

`Makefile`, help `criteria-close`: «(срез 1: файл закрытия агентским PR; флага обхода нет)» → «(срез 2a: предложение → мерж (человеком при ручных критериях) → штамп accepted; выходы 0/2/4/5/6; флага обхода нет)».

- [ ] **Step 2: Full verification**

Run:
```bash
uv run -q --frozen --group=selfcheck ruff format --check . && uv run -q --frozen --group=selfcheck ruff check . && uv run -q --frozen --group selfcheck pyrefly check governance/criteria_accept.py governance/criteria_close.py governance/criteria_graph.py governance/closure_gate.py governance/acceptance_provenance.py governance/policy_rule.py governance/charter_guard.py governance/approval_facts.py governance/ops.py
GOVERNANCE_REQUIRED=1 uv run --frozen --group governance pytest -q -p no:cacheprovider
```
Expected: всё чисто, набор зелёный.

- [ ] **Step 3: Integration review, commit, PR, review**

До PR — общее интеграционное ревью всей ветки (решение владельца): свежий ревьюер, самая сильная модель, на `review-package` от `origin/master`; находки Critical/Important — один проход фиксов с RED→GREEN.

```bash
git add CLAUDE.md Makefile
git commit -m "docs: criteria-close срез 2a — предложение, подпись человека, штамп, происхождение в гейте"
git push -u origin feat/bundle-oracle-slice2a
gh pr create --base master --label human-merge-required --title "criteria-close: срез 2a — подпись человека, штамп accepted, происхождение в гейте" --body "Срез 2a оракула (спека §7.2a, план docs/superpowers/plans/2026-10-02-bundle-oracle-slice2a.md): criteria_accept и human_criteria по графу; предложение — свой коммит на ветке -proposal со снимком политики и human-merge-required; _advance — приёмка по фактам форджи; миграция незавершённого закрытия среза 1; stamp-PR status: accepted; гейт [x] — происхождение штампа (acceptance_provenance). Выходы 0/2/4/5/6. CI-проводка гейта (.github) — отдельный PR, мерж человеком."
```
Ревью — `review-pr.sh` двухфазно (`--dry-run --write-verdict F`, затем `--use-verdict F`); PR — с меткой `human-merge-required`, **мерж человеком** (правит authority-root: Task 7b).

---

### Task 9: CI-проводка гейта — `.github/`, отдельный PR, мерж человеком

Гейту в CI нужны чтение форджи (PR, их файлы, default-ветка, файл политики) и
история git. История уже есть (`fetch-depth: 0` в `oracle-gates`).

**Files:**
- Modify: `.github/workflows/ci.yml` (job `oracle-gates`: `permissions`; шаг `closure_gate` — `GH_TOKEN` и изолированная форма запуска)

- [ ] **Step 1: Edit**

В job `oracle-gates` добавить права только на чтение и токен в шаг гейта:

```yaml
  oracle-gates:
    runs-on: ubuntu-latest
    # Гейт [x] проверяет происхождение штампа по фактам форджи (спека §7.2a п.6).
    permissions:
      contents: read
      pull-requests: read
    steps:
      ...
      - name: closure_gate
        env:
          GH_TOKEN: ${{ github.token }}
        # Изолированный процесс гейта (ревью круга 7, класс A): окружение —
        # только stdlib + pyyaml, версия закреплена ЗДЕСЬ (защищённый путь),
        # а не pyproject/uv.lock репо; `python -I` — без env, user site и cwd в
        # sys.path; корень репо — в конец sys.path (модуль в корне не затеняет
        # stdlib). Форму проверяет инвариант tests/test_governance_authority_root.py.
        run: >
          uv run --isolated --no-project --python 3.12 --with pyyaml==6.0.3
          python -I -c "import runpy, sys; sys.path.append('.');
          sys.argv = ['closure_gate', '--repo', '.'];
          runpy.run_module('governance.closure_gate', run_name='__main__')"
```

(`GITHUB_REPOSITORY` Actions выставляет сами — `--repo-slug` не нужен. Если
`uv run --isolated --no-project --with` в версии uv из `setup-uv` недоступен —
эквивалент: отдельный `uv venv` + `uv pip install pyyaml==6.0.3` в каталоге вне
репо и `<venv>/bin/python -I -c …`; правило то же: окружение гейта задаёт
workflow, не репо.)

- [ ] **Step 2: Verify locally**

Run (из корня devtools): `uv run --isolated --no-project --python 3.12 --with pyyaml==6.0.3 python -I -c "import runpy, sys; sys.path.append('.'); sys.argv = ['closure_gate', '--repo', '.']; runpy.run_module('governance.closure_gate', run_name='__main__')"` — Expected: rc 0 (в devtools нет charter'ов схемы 2 с `[x]`). Затем `uv run -q --frozen --group selfcheck zizmor .github/workflows/ci.yml` (или `actionlint`) — без новых находок уровня error.

- [ ] **Step 3: PR с человеческим мержем**

```bash
git switch -c ci/closure-gate-forge-read origin/master
git add .github/workflows/ci.yml
git commit -m "ci: гейт [x] читает форджу — pull-requests: read и GH_TOKEN (срез 2a)"
git push -u origin ci/closure-gate-forge-read
gh pr create --base master --label human-merge-required --title "ci: closure_gate читает форджу (срез 2a)" --body "Проверке происхождения штампа (acceptance_provenance) нужны факты PR и файл политики: permissions pull-requests: read и GH_TOKEN в шаге closure_gate. .github/ — мерж человеком."
```
Ревью — `review-pr.sh`; мерж — **человек** (агент PR с `.github/` и меткой `human-merge-required` не мержит).

---

### Task 10: живая приёмка 2a на polygon (после мержа Tasks 1–9; акты — владельца)

Не код — прогон. Бандлы готовит агент PR-ами в polygon (вариант B, журнал
`completed` вручную), мержи — владелец (на polygon нет review-kit, поэтому
агентские мержи PR предложения/штампа там упираются в ревью: выход 2, мерж
владельцем, resume — путь `test_test_only_merged_by_human_goes_to_predicate`
и `test_stamp_merged_by_human_is_adopted`). **Автоматический агентский мерж на
polygon не проверяется** — это отдельно указать в итоговой записи.

- [ ] **Step 1: Метка.** `gh label create human-merge-required -R DarkFactory-polygon/polygon` (если нет).
- [ ] **Step 2: Бандл test-only** `oracle-stamp` (код `PLS`, 1 Must-BEH `kind: unit` с тестом, вызывающим `greet`) → PR → мерж владельцем → журнал `polygon-oracle-stamp-<дата>` → `make criteria-close` → предложение `status: proposed` на ветке `…-proposal` (выход 2 на ревью) → владелец мержит PR предложения → resume → stamp-PR (выход 2 на ревью) → владелец мержит → resume → `accepted`, выход 0. `mergedBy` владельца ∈ снимок — test-only путь допускает.
- [ ] **Step 3: Бандл с человеком** `oracle-human` (код `PLH`, BEH-01 `kind: unit` + BEH-02 `kind: manual` в AC `verification: manual`) → PR → мерж → журнал → `make criteria-close` → PR с метками `criteria-close` и `human-merge-required` создан, аттестация на polygon падает (нет review-kit) → выход **2**, не 4 (на репо с review-kit было бы 4) → владелец мержит PR предложения → resume → stamp-PR → владелец мержит → resume → `accepted`.
- [ ] **Step 4: Гейт с происхождением.** Пункты TODO polygon обоих воркстримов `[x]` → `uv run python -m governance.closure_gate --repo <polygon>` (slug — из origin) = 0. Контрольно (не коммитить): правка тела штампа `oracle-human` в рабочем дереве → красный «штамп ≠ подписанному предложению»; `oracle-positive` (файл среза 1 без `status`, граф на пине без человеческих критериев) — зелёный с предупреждением, если узлы его бандла на `main` polygon не правились после пина `387d507` (сверить `git diff 387d507 HEAD -- workstreams/oracle-positive/spec/1* workstreams/oracle-positive/spec/2*` до шага; иначе ожидаем красный «не для текущего бандла» — правило R4-B1).
- [ ] **Step 5: Закрыть** `bundle-oracle-slice2` в devtools TODO формулировкой с номерами PR polygon и пином; отдельно: «автоматический агентский мерж на polygon не проверялся (нет review-kit; мержи PR предложения и штампа — владельцем)»; п.5 §8.4 — тестами (`test_human_criterion_merged_by_outsider_is_rejected`, `test_agent_merge_with_human_criterion_is_red`), на polygon нет третьей учётки; п.6 — в 2b.
