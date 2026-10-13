# Акт утверждения через approve человека — план реализации (devtools)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task (решение владельца 13.10: только subagent-driven, протокол — раздел «Решения владельца», п. 3; `executing-plans` не применяется). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** перевести человеческий акт на путях «узел бандла (§I12)» и «бриф» с мержа PR
учёткой человека на approve-ревью человека по head SHA заявки; мерж становится доставкой и
выполняется агентом.

**Architecture:** решение вычисляется чистыми функциями над фактами форджа (`approval-facts/v3`
steward), пинованным набором политик (`approval-policy` на `activation_commit`), журналом эпох
доверия и манифестом заявки в одобренном коммите. Всё новое в devtools — модули `governance/act_*`
(под authority-root). Равные метки времени разрешаются одним механизмом (§8.2) через объект
`Clock`, который каждая проверка «до T?» спрашивает одинаково. Старый путь `merge` исполняем до
опустошения; новый включается по продукту через `ACT_MODE` и барьер миграции.

**Tech Stack:** Python ≥3.12, stdlib + `pyyaml`; `steward` (пиненая git-зависимость, группа
`governance`) — общий читатель фактов; pytest; ruff; pyrefly.

**Spec:** `docs/superpowers/specs/2026-10-12-approval-act-review-design.md` (rev 14, подписана
владельцем 2026-10-12; rev 15 — уточнения по решениям владельца 13.10). План спорит со спекой, а не заменяет её: исполнитель читает обе.

## Global Constraints

- Акт = эффективное `APPROVED` логина из `human_approvers` на `commit_id` = head заявки =
  `head_at_merge`; `reviewDecision` не используется (спека D1).
- Решение вычисляется на `T = merged_at`; любое сравнение с T — только через `Clock.precedes`
  (§8.2); безусловных отказов из-за равной секунды нет.
- Группа равных меток больше 8 событий (вместе с доставкой) → `ambiguous_event_order`.
- `unavailable` — никогда не «пустой список»; неустановленный факт заявку не хоронит
  (правило `governance/approval_facts.py`: в `invalidated` переводит только установленный факт).
  Типы исходов: `Rejected(code, detail)` — установленный отказ (заявка может быть
  инвалидирована); `act_time.Unavailable(reason)` и `act_time.Ambiguous()` — не установлено,
  заявка жива. Преобразовывать `Unavailable` в `Rejected` запрещено.
- Неоднозначность (§8.2) определяется по **вердикту**, а не по свидетельству: поля-свидетельства
  исходов (`review_id`, `login`, `detail`) объявлены `field(compare=False)`.
- Admission (§10) выполняется **до** выбора читателя на обоих путях; старый код получает
  управление только после `OLD_READER`.
- Все файлы политики читаются из **одного** `activation_commit`; смешивание версий — отказ.
- Локальные копии политики акторов (`contracts/steward-actor-policy/v1`) акт не читает никогда,
  в том числе как fallback.
- `ACT_MODE` без записи для продукта → отказ; обратное переключение `review → merge` →
  `unavailable`.
- Режим заявки выбирается **только** по манифесту в одобренном коммите; fallback на frontmatter
  узла для `review/v1` запрещён.
- Журнал эпох: единственная допустимая мутация — атомарное закрытие последней открытой эпохи +
  добавление следующей.
- Локальная настройка установки (`act.toml`) может только остановить создание заявок; разрешить
  что-либо она не может.
- `_cowork_output/` код не читает (правило воркспейса).
- **Контракт импорта без `governance`** (`ci.yml:27`, ревью #499): любой `governance/act_*`
  импортируется без steward — импорт steward только внутри функций, которым он нужен. Тест,
  которому нужен steward, начинается с `pytest.importorskip("steward")` **до** импорта
  зависимых модулей; общий `tests/act_cases.py` steward не импортирует (фабрика с типами M0 —
  отдельный `tests/act_check_cases.py`). CI уже гоняет оба прогона: без группы и с
  `--group governance` + `GOVERNANCE_REQUIRED=1` (`ci.yml:28–36`, `tests/conftest.py`) — второй
  роняет скип по отсутствию steward, так что тесты не исчезают молча.
- Новые модули — `governance/act_*.py`; префикс `governance/act_` добавляется в
  `contracts/authority-root/v1/paths.env` (Task 1) → все PR плана мержит человек.
- Строки ≤ 88 символов, type hints, docstrings публичных функций; `uv run ruff check`,
  `uv run ruff format`, `pyrefly check` после каждой задачи.

## Решения владельца при подписи плана (13.10)

Два прежних отступления от буквы спеки rev 14 владелец разрешил 13.10; спека уточнена до rev 15
(§6.1 п. 2, §6.2 б, §8 №11, §11.1 №28 и правило стендовых строк, §13). План следует rev 15.

1. **`allow_rebase_merge` — четыре состояния (спека §6.2 б).** Замер 2026-10-12:
   `GET /repos/andrei-shtanakov/approval-policy` от `ai-prosto` (права `pull` без `push`) отдаёт
   `allow_rebase_merge: null` — настройку мержа GitHub показывает только вызывающему с правом
   записи, а агенту такое право запрещено периметром. Правило рантайма:
   - `false` — настройка проверена;
   - `true` — нарушение периметра (маркер §6.3, `Unavailable`);
   - `null` или поле отсутствует **в корректном ответе** — непроверенная предпосылка: решение
     не блокирует, записывается в `Trust.unverified`; обязательная проверка — человеком при
     установке (§6.2 а);
   - ошибка запроса или некорректный ответ (не объект, значение не `bool`/`null`) —
     `Unavailable` без маркера; в допустимый `null` не превращается.

   **Предел обнаружения (поправка владельца к rev 9 плана).** Форма истории ловит только
   многокоммитный rebase-merge: его промежуточные коммиты first-parent не являются
   `merge_commit_sha` никакого PR → `HistoryViolation`. Однокоммитный rebase-merge следа не
   оставляет: GitHub отдаёт в `merge_commit_sha` коммит, на который обновилась базовая ветка
   (документированная семантика API; живым тестом не проверялось), так что сверка коммит ↔ PR
   проходит, и по форме он неотличим от squash. Проверка формы истории обнаруживает
   определённые нарушения, но **не доказывает отсутствие rebase-merge**; запрет остаётся
   частью человеческого периметра. Однокоммитный случай зафиксирован вектором (M0) и строкой
   приёмки №28.
2. **Разрушительные сценарии — только на стенде (спека §11.1).** Сценарии №21, 28, 29, 33–37,
   39, 45 на реальном продукте не ставятся: настоящий `approval-policy` ради приёмки не
   повреждается. Их evidence фазы 1 принимается при условиях Task 18 (SHA реализаций и
   конфигурация стенда, соответствие механизма реальной установке, пометка «проверено на
   стенде» со ссылкой, безопасные проверки периметра и сквозной путь — на продукте).
3. **Исполнение — subagent-driven** (`superpowers:subagent-driven-development`): свежий
   исполнитель и независимый ревьюер на задачу; зависимые задачи — последовательно; после
   исправлений ревью повторяется по **актуальному** коду (вердикт по прежней версии не
   переносится). В конце каждой части (A, B, C) и перед Task 19 — общий интеграционный обзор
   всего пути утверждения (заявка → approve → admission → решение → доставка → конверт →
   узел/бриф): локальные ревью задач его не заменяют.

Ворота перед частью B сохраняются: опубликованный M0 → детализация задач 11–17 по фактическому
API → ревью обновлённого плана → реализация.

## Review Focus

1. **Фикстура с `Z` и с `+00:00`/дробными секундами** — метки одного события, пришедшие из REST
   и GraphQL в разной записи, обязаны сравниваться как равные (Task 3, `parse_ts`).
2. **Логин бота в двух формах** (`slug[bot]` из REST, `slug` из GraphQL) у мержера —
   классификация обязана дать одно и то же; иначе D3 зависит от транспорта (Task 7, тест
   `test_merger_bot_forms_agree`).
3. **Approve на старый коммит + approve на новый head от того же логина** — эффективным
   считается последний, и `commit_id` старого не должен «подтянуться» (Task 7,
   `test_latest_review_commit_wins`).
4. **Пустой `paths` в манифесте** — заявка без единого утверждённого пути не может пройти D2
   вакуумно (Task 5, `test_manifest_without_nodes_refused`).
5. **Повторный запуск после частичного прогона** — envelope уже записан, finalize не смёржен:
   второй прогон не создаёт второй конверт другого поколения (Task 13,
   `test_finalize_resume_reuses_envelope`).

---

## Порядок и зависимости

| Часть | Задачи | Зависит от | Выход |
|---|---|---|---|
| A — чистое ядро | 1–10, 5a | ничего (steward только `classify_actor`, уже в пине) | PR devtools #1 (authority-root → человек) |
| H — шаги человека | H1–H2 | Часть A влита | PR в `approval-policy`, PR devtools (контракт v2) |
| B — интеграция узла | 11–16, 12a, 13a, 15a | **M0 steward** выпущен; H1–H2 | PR devtools #2 |
| C — путь брифа | 17 | **M2 discovery** | PR devtools #3 |
| D — живая приёмка M3 | 18 | B, C | evidence в devtools |
| E — дефолт M4 | 19 | D пройдена | PR devtools #4 |

**Ворота перед частью B.** Задачи 12a (тела тестов), 13–17 (включая 13a, 15a) заданы правилами, интерфейсами и
перечнем тестов, а не полным кодом:
они вплетаются в `approve_node.py` (2600 строк) и зависят от фактического API релиза M0. Перед
стартом части B исполнитель сверяет «Интерфейс M0» с релизом steward и в любом случае
обновляет этот план (Tasks 11–17) отдельным коммитом до кода — с тем же уровнем детализации,
что задачи 1–12 (полный код тестов и реализации), и проводит его через тот же локальный круг
ревью, что и эту пару.

Части M0 (steward) и M2 (discovery) — в своих репо, по inbox-issues с интерфейсом из раздела
«Интерфейс M0» ниже. Этот план их не исполняет.

### Интерфейс M0 (ожидаемый от steward, `steward.approvalfacts.v3`)

devtools импортирует steward **только** в `governance/act_facts.py` (Task 11). Если steward
назовёт иначе — меняется один адаптер. Ожидаемые имена:

```python
EventKey = tuple[str, str]  # ("review", "<id>") | ("dismissal", "<review_id>") | ...

@dataclass(frozen=True)
class Review:
    review_id: str; login: str; type_hint: str; state: str
    commit_id: str; submitted_at: str | None

@dataclass(frozen=True)
class Dismissal:
    review_id: str; actor: str; created_at: str; previous_state: str | None

@dataclass(frozen=True)
class ReviewFacts:
    repository: str; pr: int; head_sha: str; base_ref: str; pr_state: str
    reviews: tuple[Review, ...]; dismissals: tuple[Dismissal, ...]

@dataclass(frozen=True)
class PathBlob:
    path: str; blob_at_merge: str; blob_at_head: str

@dataclass(frozen=True)
class Delivery:
    merged: bool; merge_commit: str | None; merged_at: str | None
    merged_by_login: str | None; merged_by_type_hint: str | None
    head_at_merge: str | None; in_base: bool; paths: tuple[PathBlob, ...]

@dataclass(frozen=True)
class Unavailable:
    reason: str

def read_act_facts(transport, repo: str, pr: int, *, paths: Sequence[str]
                   ) -> tuple[Delivery, ReviewFacts | None] | Unavailable: ...
    # §5.5: доставка первой; не смёржен → (Delivery(merged=False,...), None)

def effective_reviews(facts: ReviewFacts, t: str, *, before: frozenset[EventKey]
                      ) -> dict[str, Review] | Unavailable: ...
    # §5.3 на T; событие с меткой == T считается «до T» тогда и только тогда,
    # когда его ключ в `before`; Review.state — восстановленное состояние

@dataclass(frozen=True)
class Activation:
    pr: int; merge_commit: str; merged_at: str; files_sha256: dict[str, str]

@dataclass(frozen=True)
class HistoryViolation:
    reason: str  # установленное нарушение формы истории (коммит без PR и т. п.)

def read_policy_history(transport, repo: str, branch: str, *, anchor: str,
                        until: str | None
                        ) -> tuple[Activation, ...] | HistoryViolation | Unavailable: ...
    # §6.1 п. 2–5: first-parent anchor…until (None = HEAD), только PR-мержи,
    # порядок — first-parent (старые первыми). Нарушение формы — HistoryViolation
    # (потребитель пишет маркер §6.3), сбой чтения — Unavailable (маркер не пишется).
    # Многокоммитный rebase-merge → HistoryViolation (промежуточные коммиты без PR);
    # однокоммитный неотличим от squash и возвращается как Activation — предел,
    # не нарушение (спека §6.1 п. 2, rev 15)

def read_open_review(transport, repo: str, pr: int) -> ReviewFacts | Unavailable: ...
    # предпроверка открытого PR перед агентским мержем: те же два полных прохода
    # §5.5 по ревью и таймлайну, без доставки; решение по нему НЕ окончательное

def read_activation(transport, repo: str, merge_commit: str
                    ) -> Activation | Unavailable: ...
    # факт одной активации (якорь эпохи): PR, merged_at, files_sha256

# гейты steward, читающие статус узла (gatecheck/checks.py:377), получают
# параметр `node_status: Callable[[str], str] | None`; узел с конвертом
# review/v1 без провайдера — «не одобрен» (fail-closed), frontmatter не читается
```

Векторы §5.6 steward публикует в `contracts/approval-facts/v3/vectors/`; devtools вендорит их
пиненой копией (Task 11).

---

## Часть A — чистое ядро (без M0)

### Task 1: Каркас `act_*`, authority-root, контракт источника v2

**Files:**
- Modify: `contracts/authority-root/v1/paths.env` (добавить `governance/act_`)
- Create: `contracts/approval-policy-source/v2/README.md`
- Create: `governance/act_source.py`
- Test: `tests/test_act_source.py`

**Interfaces:**
- Produces: `SourceV2(repo: str, branch: str, bootstrap_activation: str,
  policy_admins: frozenset[str])`; `parse_source(text: str) -> SourceV2` (raises
  `SourceError`); `SOURCE_V2_FILE: Path`; `load_source() -> SourceV2`.

- [ ] **Step 1: Write the failing test**

```python
"""Контракт источника политики v2 (спека §6.1 п. 1, §6.2)."""

from __future__ import annotations

import pytest

from governance.act_source import SourceError, parse_source

GOOD = """\
APPROVAL_POLICY_REPO=owner/approval-policy
APPROVAL_POLICY_BRANCH=main
BOOTSTRAP_ACTIVATION=0123456789abcdef0123456789abcdef01234567
POLICY_ADMINS=owner, second-admin
"""


def test_parses_all_keys() -> None:
    src = parse_source(GOOD)
    assert src.repo == "owner/approval-policy"
    assert src.branch == "main"
    assert src.bootstrap_activation.startswith("0123")
    assert src.policy_admins == frozenset({"owner", "second-admin"})


@pytest.mark.parametrize(
    "broken",
    [
        GOOD.replace("BOOTSTRAP_ACTIVATION=0123456789abcdef", "BOOTSTRAP_ACTIVATION=zz"),
        GOOD.replace("POLICY_ADMINS=owner, second-admin", "POLICY_ADMINS= , "),
        GOOD + "APPROVAL_POLICY_BRANCH=other\n",
        GOOD.replace("APPROVAL_POLICY_REPO=owner/approval-policy\n", ""),
    ],
    ids=["short-sha", "empty-admins", "duplicate-key", "missing-repo"],
)
def test_refuses_broken_source(broken: str) -> None:
    with pytest.raises(SourceError):
        parse_source(broken)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_act_source.py -v`
Expected: FAIL — `ModuleNotFoundError: governance.act_source`

- [ ] **Step 3: Write minimal implementation**

```python
"""Координаты доверенного источника политики v2 (спека §6.1 п. 1, §6.2).

Файл `contracts/approval-policy-source/v2/source.env` — authority-root:
закрепляется человеком при bootstrap и задаёт исходное доверие ВНЕ
проверяемой политики. `policy_admins` — allowlist принципалов с правом
записи в источник; набор проверяемых личностей не берётся из проверяемой
версии политики (§6.2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from governance import ssot_env

SOURCE_V2_FILE = (
    Path(__file__).resolve().parent.parent
    / "contracts"
    / "approval-policy-source"
    / "v2"
    / "source.env"
)
_SHA = re.compile(r"\A[0-9a-f]{40}\Z")
_KEYS = (
    "APPROVAL_POLICY_REPO",
    "APPROVAL_POLICY_BRANCH",
    "BOOTSTRAP_ACTIVATION",
    "POLICY_ADMINS",
)


class SourceError(ValueError):
    """Контракт источника не читается — отказ конфигурации, не факт."""


@dataclass(frozen=True)
class SourceV2:
    """Закреплённые человеком координаты источника политики."""

    repo: str
    branch: str
    bootstrap_activation: str
    policy_admins: frozenset[str]


def _one(text: str, key: str) -> str:
    lines = ssot_env.definition_lines(text, key)
    if len(lines) != 1 or not lines[0].strip():
        raise SourceError(f"{key}: ожидалась ровно одна непустая строка")
    return lines[0].strip()


def parse_source(text: str) -> SourceV2:
    """Разбор `source.env` v2; любая неоднозначность — `SourceError`."""
    values = {key: _one(text, key) for key in _KEYS}
    if not _SHA.match(values["BOOTSTRAP_ACTIVATION"]):
        raise SourceError("BOOTSTRAP_ACTIVATION: нужен полный 40-символьный SHA")
    admins = frozenset(
        part.strip() for part in values["POLICY_ADMINS"].split(",") if part.strip()
    )
    if not admins:
        raise SourceError("POLICY_ADMINS: пустой allowlist администраторов")
    return SourceV2(
        values["APPROVAL_POLICY_REPO"],
        values["APPROVAL_POLICY_BRANCH"],
        values["BOOTSTRAP_ACTIVATION"],
        admins,
    )


def load_source() -> SourceV2:
    """Контракт из репо devtools; файла нет — `SourceError` (bootstrap не сделан)."""
    try:
        return parse_source(SOURCE_V2_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SourceError(
            f"{SOURCE_V2_FILE} нет — bootstrap источника v2 не выполнен (шаг H2)"
        ) from exc
```

`ssot_env.definition_lines(text, key)` возвращает значения уже без `KEY=` и без пробелов по
краям, пропуская комментарии (`governance/ssot_env.py:33`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_act_source.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Authority-root + README**

Add ` governance/act_` to the end of the `AUTHORITY_ROOT_PREFIXES=` line in
`contracts/authority-root/v1/paths.env` (the prefix list is space-separated; keep one line).
Add `"governance/act_",` to `_HARNESS_PREFIXES` in `governance/accept_pr.py:85` (с комментарием:
«акт утверждения review/v1 — проверяльщик исполняется в обвязке мержа (`act_premerge`), та же
категория, что `brief_merge_check`»). `tests/harness_trust.py` сверяет **оба** перечня
(`test_every_harness_input_is_authority_root_but_the_kit`) — добавлять в один нельзя.
Create `contracts/approval-policy-source/v2/README.md`:

```markdown
# approval-policy-source v2

Координаты доверенного источника политики для акта `review/v1`
(спека `docs/superpowers/specs/2026-10-12-approval-act-review-design.md` §6).

- `source.env` — репо, ветка, `BOOTSTRAP_ACTIVATION`, `POLICY_ADMINS`; пишет человек (шаг H2).
- `epochs.yaml` — журнал эпох доверия (§6.3); единственная мутация — атомарное закрытие
  последней эпохи + добавление следующей; валидатор — `governance/act_epochs.py`.

Каталог под authority-root: PR, меняющий его, мержит человек. `v1/` остаётся для legacy-пути.
```

Run: `uv run pytest tests -k "authority_root or harness" -q`
Expected: PASS (existing guards accept the new prefix; if a test pins the exact prefix list,
update its expected list in the same commit).

- [ ] **Step 6: Commit**

```bash
git add governance/act_source.py tests/test_act_source.py \
  contracts/approval-policy-source/v2/README.md contracts/authority-root/v1/paths.env \
  governance/accept_pr.py
git commit -m "feat(act): контракт источника политики v2 и authority-root для act_*"
```

### Task 2: Журнал эпох доверия

**Files:**
- Create: `governance/act_epochs.py`
- Test: `tests/test_act_epochs.py`

**Interfaces:**
- Produces: `Epoch(epoch: int, anchor: str, last_confirmed: str | None,
  valid_until: str | None, closed_reason: str | None)` с `.closed: bool`;
  `parse_journal(text: str) -> tuple[Epoch, ...]`;
  `check_transition(prev: tuple[Epoch, ...], new: tuple[Epoch, ...]) -> None`;
  обе бросают `JournalError`.

- [ ] **Step 1: Write the failing test**

```python
"""Журнал эпох (спека §6.3): форма и единственная допустимая мутация."""

from __future__ import annotations

import dataclasses

import pytest

from governance.act_epochs import Epoch, JournalError, check_transition, parse_journal

A1 = "a" * 40
A2 = "b" * 40
LC = "c" * 40

ONE_OPEN = f"""\
schema: approval-policy-epochs/v1
epochs:
  - epoch: 1
    anchor: {A1}
"""

CLOSED_AND_OPEN = f"""\
schema: approval-policy-epochs/v1
epochs:
  - epoch: 1
    anchor: {A1}
    last_confirmed: {LC}
    valid_until: "2026-10-20T10:00:00Z"
    closed_reason: "агенту выдана запись, devtools#000"
  - epoch: 2
    anchor: {A2}
"""


def test_parses_open_journal() -> None:
    (only,) = parse_journal(ONE_OPEN)
    assert only == Epoch(1, A1, None, None, None)
    assert not only.closed


def test_parses_closed_then_open() -> None:
    first, second = parse_journal(CLOSED_AND_OPEN)
    assert first.closed and not second.closed


@pytest.mark.parametrize(
    "text",
    [
        "schema: approval-policy-epochs/v1\nepochs: []\n",
        ONE_OPEN.replace("epoch: 1", "epoch: 2"),
        CLOSED_AND_OPEN.replace("    closed_reason: \"агенту выдана запись, devtools#000\"\n", ""),
        CLOSED_AND_OPEN + f"  - epoch: 3\n    anchor: {A1}\n",
        ONE_OPEN.replace("approval-policy-epochs/v1", "approval-policy-epochs/v9"),
        ONE_OPEN.replace("epoch: 1", "epoch: true"),
        ONE_OPEN.replace("epoch: 1", "epoch: 1.0"),
    ],
    ids=["empty", "number-not-position", "half-closed", "two-open", "unknown-schema",
         "bool-number", "float-number"],
)
def test_refuses_malformed(text: str) -> None:
    with pytest.raises(JournalError):
        parse_journal(text)


def test_atomic_close_and_append_allowed() -> None:
    check_transition(parse_journal(ONE_OPEN), parse_journal(CLOSED_AND_OPEN))


def test_identity_transition_allowed() -> None:
    journal = parse_journal(CLOSED_AND_OPEN)
    check_transition(journal, journal)


def test_editing_closed_epoch_refused() -> None:
    prev = parse_journal(CLOSED_AND_OPEN)
    edited = (dataclasses.replace(prev[0], closed_reason="переписано"), prev[1])
    with pytest.raises(JournalError):
        check_transition(prev, edited)


def test_moving_anchor_refused() -> None:
    prev = parse_journal(ONE_OPEN)
    with pytest.raises(JournalError):
        check_transition(prev, (dataclasses.replace(prev[0], anchor=A2),))


def test_deleting_epoch_refused() -> None:
    prev = parse_journal(CLOSED_AND_OPEN)
    with pytest.raises(JournalError):
        check_transition(prev, prev[:1])


def test_append_without_closing_refused() -> None:
    prev = parse_journal(ONE_OPEN)
    with pytest.raises(JournalError):
        check_transition(prev, (prev[0], Epoch(2, A2, None, None, None)))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_act_epochs.py -v`
Expected: FAIL — `ModuleNotFoundError: governance.act_epochs`

- [ ] **Step 3: Write minimal implementation**

```python
"""Журнал эпох доверия `approval-policy-source/v2/epochs.yaml` (спека §6.3).

Порядок эпох — позиция в списке; номер обязан ей равняться. Все эпохи,
кроме последней, закрыты (заполнены `last_confirmed`, `valid_until`,
`closed_reason`), последняя открыта. Единственная допустимая мутация —
атомарный переход: закрыть последнюю открытую и добавить следующую.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import yaml

SCHEMA = "approval-policy-epochs/v1"
_CLOSING = ("last_confirmed", "valid_until", "closed_reason")


class JournalError(ValueError):
    """Журнал не читается или переход недопустим → `unavailable`."""


@dataclass(frozen=True)
class Epoch:
    """Одна эпоха доверия."""

    epoch: int
    anchor: str
    last_confirmed: str | None = None
    valid_until: str | None = None
    closed_reason: str | None = None

    @property
    def closed(self) -> bool:
        """Эпоха закрыта человеком."""
        return self.last_confirmed is not None


def _epoch(raw: object, position: int) -> Epoch:
    if not isinstance(raw, dict) or set(raw) - {"epoch", "anchor", *_CLOSING}:
        raise JournalError(f"эпоха #{position}: неизвестная форма {raw!r}")
    number = raw.get("epoch")
    if type(number) is not int or number != position:
        raise JournalError(f"эпоха #{position}: номер {number!r} ≠ позиции (int)")
    anchor = raw.get("anchor")
    if not isinstance(anchor, str) or len(anchor) != 40:
        raise JournalError(f"эпоха #{position}: anchor обязан быть полным SHA")
    closing = [raw.get(key) for key in _CLOSING]
    if any(v is not None for v in closing) and not all(
        isinstance(v, str) and v for v in closing
    ):
        raise JournalError(f"эпоха #{position}: закрыта частично")
    return Epoch(position, anchor, *closing)


def parse_journal(text: str) -> tuple[Epoch, ...]:
    """Разбор и проверка формы журнала."""
    data = yaml.safe_load(text)
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise JournalError(f"схема журнала не {SCHEMA}")
    raw = data.get("epochs")
    if not isinstance(raw, list) or not raw:
        raise JournalError("журнал без эпох")
    epochs = tuple(_epoch(item, i) for i, item in enumerate(raw, start=1))
    if any(not e.closed for e in epochs[:-1]) or epochs[-1].closed:
        raise JournalError("открытой обязана быть ровно последняя эпоха")
    return epochs


def check_transition(prev: tuple[Epoch, ...], new: tuple[Epoch, ...]) -> None:
    """Сверка новой версии журнала с прежней (CI и каждый потребитель)."""
    if new == prev:
        return
    if len(new) != len(prev) + 1 or new[: len(prev) - 1] != prev[:-1]:
        raise JournalError("допустимо только закрытие последней эпохи + новая эпоха")
    closed, last = new[len(prev) - 1], prev[-1]
    reopened = replace(closed, last_confirmed=None, valid_until=None, closed_reason=None)
    if reopened != last or not closed.closed:
        raise JournalError("закрытие обязано менять только поля закрытия")
    if new[-1].closed:
        raise JournalError("добавленная эпоха обязана быть открытой")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_act_epochs.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add governance/act_epochs.py tests/test_act_epochs.py
git commit -m "feat(act): журнал эпох доверия — форма и атомарный переход (§6.3)"
```

### Task 3: Единое правило равных меток (`Clock`, перебор §8.2)

**Files:**
- Create: `governance/act_time.py`
- Test: `tests/test_act_time.py`

**Interfaces:**
- Produces: `parse_ts(value: str) -> datetime`; `EventKey = tuple[str, str]`;
  `Clock(t: datetime, before: frozenset[EventKey])` с `.precedes(key, stamp) -> bool`;
  `Ambiguous` (frozen, `reason="ambiguous_event_order"`); `Unavailable(reason: str)` —
  общий тип «факт не установлен» для всех `act_*`; `MAX_GROUP = 8`;
  `before_sets(group, requires) -> list[frozenset[EventKey]]`;
  `resolve(t, group, requires, decide) -> R | Ambiguous`.

Эквивалентность перебора: исход решения зависит только от того, **какие** события группы
предшествуют доставке — события до T упорядочены собственными детерминированными правилами
(ревью одного логина — `review_id`, §5.3; активации — first-parent). Поэтому все линейные порядки
G проецируются на множества «до доставки», замкнутые вниз по зависимостям **внутри группы**:
зависимость от события вне группы уже упорядочена его собственной меткой (оно раньше T —
событие не может зависеть от более позднего). Тест `test_matches_brute_force_linear_orders`
доказывает равенство множеств (спека §8.2 п. 4).

Сравниваются **вердикты**: исходы — frozen dataclasses, у которых поля-свидетельства объявлены
`field(compare=False)`. При единственном вердикте возвращается исход порядка «все события группы
до доставки» (полное множество всегда допустимо) — его свидетельство действительно хотя бы в
одном допустимом порядке.

- [ ] **Step 1: Write the failing test**

```python
"""§8.2: события равной секунды не упорядочены; перебор совместных порядков."""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field

from governance.act_time import (
    MAX_GROUP,
    Ambiguous,
    Clock,
    before_sets,
    parse_ts,
    resolve,
)

T = "2026-10-12T10:00:00Z"
APPROVE = ("review", "1")
DISMISS = ("dismissal", "1")


def test_parse_ts_normalises_forms() -> None:
    assert parse_ts("2026-10-12T10:00:00Z") == parse_ts("2026-10-12T10:00:00.700+00:00")


def test_clock_orders_by_stamp_then_membership() -> None:
    clock = Clock(parse_ts(T), frozenset({APPROVE}))
    assert clock.precedes(("x", "1"), parse_ts("2026-10-12T09:59:59Z"))
    assert not clock.precedes(("x", "1"), parse_ts("2026-10-12T10:00:01Z"))
    assert clock.precedes(APPROVE, parse_ts(T))
    assert not clock.precedes(DISMISS, parse_ts(T))


def test_before_sets_respect_dependencies() -> None:
    sets = before_sets([APPROVE, DISMISS], {DISMISS: frozenset({APPROVE})})
    assert frozenset({DISMISS}) not in sets
    assert len(sets) == 3


def test_dependency_outside_group_is_satisfied() -> None:
    """Approve раньше T (вне группы), отзыв в T — «отзыв до доставки» допустим."""
    sets = before_sets([DISMISS], {DISMISS: frozenset({APPROVE})})
    assert frozenset({DISMISS}) in sets


@dataclass(frozen=True)
class _Verdict:
    verdict: str
    witness: str = field(compare=False, default="")


def test_same_verdict_different_witness_not_ambiguous() -> None:
    """Approve Alice и Bob заранее; отзыв Alice в T — акт есть при любом порядке."""
    d_alice = ("dismissal", "alice")

    def decide(clock: Clock) -> _Verdict:
        return _Verdict("accepted", "bob" if d_alice in clock.before else "alice")

    result = resolve(parse_ts(T), [d_alice], {}, decide)
    assert result == _Verdict("accepted")
    assert isinstance(result, _Verdict) and result.witness == "bob"


def _brute(group, requires):
    seen = set()
    for order in itertools.permutations([*group, "D"]):
        pos = {e: i for i, e in enumerate(order)}
        if all(pos[p] < pos[e] for e, ps in requires.items() for p in ps):
            seen.add(frozenset(e for e in group if pos[e] < pos["D"]))
    return seen


def test_matches_brute_force_linear_orders() -> None:
    rng = random.Random(7)
    outside = ("e", "outside")
    for _ in range(40):
        group = [("e", str(i)) for i in range(rng.randint(0, 5))]
        requires = {
            e: frozenset(p for p in [outside, *group[:i]] if rng.random() < 0.3)
            for i, e in enumerate(group)
        }
        inside = {e: ps - {outside} for e, ps in requires.items()}
        assert set(before_sets(group, requires)) == _brute(group, inside)


def test_single_outcome_accepted() -> None:
    assert resolve(parse_ts(T), [APPROVE], {}, lambda clock: "ok") == "ok"


def test_differing_outcomes_ambiguous() -> None:
    result = resolve(parse_ts(T), [APPROVE], {}, lambda clock: APPROVE in clock.before)
    assert result == Ambiguous()


def test_two_dismissals_same_second_ambiguous() -> None:
    """Приёмка №49: оба отзыва в T — есть порядок «оба до T» (акта нет)."""
    d_alice, d_bob = ("dismissal", "alice"), ("dismissal", "bob")

    def decide(clock: Clock) -> bool:
        return not ({d_alice, d_bob} <= clock.before)

    assert resolve(parse_ts(T), [d_alice, d_bob], {}, decide) == Ambiguous()


def test_group_over_cap_ambiguous_without_deciding() -> None:
    group = [("e", str(i)) for i in range(MAX_GROUP)]  # + доставка = 9

    def explode(clock: Clock) -> str:
        raise AssertionError("перебор не должен выполняться")

    assert resolve(parse_ts(T), group, {}, explode) == Ambiguous()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_act_time.py -v`
Expected: FAIL — `ModuleNotFoundError: governance.act_time`

- [ ] **Step 3: Write minimal implementation**

```python
"""Единое правило временной неоднозначности (спека §8.2).

Метки форджа — с точностью до секунды; события с равной меткой не
упорядочены. Каждое сравнение «до T?» во всех проверках акта идёт через
`Clock.precedes`: событие с меткой T считается предшествующим доставке
ровно тогда, когда его ключ входит в рассматриваемое множество `before`.
`resolve` перебирает все допустимые множества и принимает исход, только
если он один.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import combinations

EventKey = tuple[str, str]
MAX_GROUP = 8
AMBIGUOUS = "ambiguous_event_order"


def parse_ts(value: str) -> datetime:
    """Метка форджа → aware UTC с отброшенными долями секунды."""
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError(f"метка без часового пояса: {value!r}")
    return stamp.astimezone(UTC).replace(microsecond=0)


@dataclass(frozen=True)
class Ambiguous:
    """Вердикты при допустимых порядках различаются → `unavailable`."""

    reason: str = AMBIGUOUS


@dataclass(frozen=True)
class Unavailable:
    """Факт не установлен: решение не принимается, заявка жива."""

    reason: str


@dataclass(frozen=True)
class Clock:
    """Момент T и выбранное множество событий группы, стоящих до доставки."""

    t: datetime
    before: frozenset[EventKey] = frozenset()

    def precedes(self, key: EventKey, stamp: datetime) -> bool:
        """Событие `key` с меткой `stamp` предшествует доставке."""
        return stamp < self.t or (stamp == self.t and key in self.before)


def before_sets(
    group: Sequence[EventKey], requires: Mapping[EventKey, frozenset[EventKey]]
) -> list[frozenset[EventKey]]:
    """Все множества событий группы, замкнутые вниз по зависимостям группы.

    Зависимость от события вне группы выполнена: оно упорядочено своей
    меткой. Порядок результата — по возрастанию размера; последнее —
    полное множество.
    """
    members = frozenset(group)
    found: list[frozenset[EventKey]] = []
    for size in range(len(group) + 1):
        for combo in combinations(group, size):
            chosen = frozenset(combo)
            if all(requires.get(e, frozenset()) & members <= chosen for e in chosen):
                found.append(chosen)
    return found


def resolve[R: Hashable](
    t: datetime,
    group: Sequence[EventKey],
    requires: Mapping[EventKey, frozenset[EventKey]],
    decide: Callable[[Clock], R],
) -> R | Ambiguous:
    """Исход по §8.2: единственный вердикт либо `Ambiguous`.

    Возвращается исход полного множества — свидетельство одного
    допустимого порядка; равенство исходов игнорирует свидетельства.
    """
    if len(group) + 1 > MAX_GROUP:
        return Ambiguous()
    outcomes = [decide(Clock(t, before)) for before in before_sets(group, requires)]
    if len(set(outcomes)) != 1:
        return Ambiguous()
    return outcomes[-1]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_act_time.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add governance/act_time.py tests/test_act_time.py
git commit -m "feat(act): единое правило равных меток — Clock и перебор групп (§8.2)"
```

### Task 4: Пинованный набор политик

**Files:**
- Create: `governance/act_policy.py`
- Test: `tests/test_act_policy.py`

**Interfaces:**
- Consumes: `steward.gatecheck.approval.ApprovalPolicy`, `classify_actor` (уже в пине
  steward `b77ee0d`).
- Produces: `POLICY_FILES: tuple[str, ...]`; `PolicySet(activation_commit, approvers,
  mergers, modes, humans, agents, files_sha256)`; `parse_policy_set(activation_commit: str,
  files: Mapping[str, bytes]) -> PolicySet` (raises `PolicyError`);
  `classify(ps: PolicySet, login: str, type_hint: str | None) -> str`
  (`human|agent|unknown`); `mode_for(ps, product: str) -> str | None`.

- [ ] **Step 1: Write the failing test**

Общие фикстуры набора — `tests/act_cases.py` (паттерн `tests/approval_request_cases.py`):

```python
"""Общие фикстуры тестов act_*: набор политик на одном коммите."""


def code(outcome: object) -> str:
    """Код отказа либо имя типа исхода — для точных assert'ов (код вне `==`)."""
    return getattr(outcome, "code", type(outcome).__name__)


COMMIT = "d" * 40
FILES = {
    "policy/approvers.env": (
        b"AUTHORIZED_APPROVER_ACCOUNTS=alice\n"
        b"AUTHORIZED_MERGER_ACCOUNTS=df-reviewer[bot]\n"
    ),
    "policy/modes.yaml": (
        b"schema: approval-policy-modes/v1\n"
        b"modes:\n  owner/product-a: merge\n  owner/product-b: review\n"
    ),
    "policy/actors.yaml": (
        b"version: 1\nhuman_identities: [github:alice]\n"
        b"agent_identities: [github:df-reviewer, github:ai-prosto]\n"
    ),
}
```

`tests/test_act_policy.py`:

```python
"""Набор политик из одного activation_commit (спека §6, Q3, Q8)."""

from __future__ import annotations

import pytest

from governance.act_policy import PolicyError, classify, mode_for, parse_policy_set
from tests.act_cases import COMMIT, FILES


def test_parses_whole_set() -> None:
    ps = parse_policy_set(COMMIT, FILES)
    assert ps.approvers == frozenset({"alice"})
    assert ps.mergers == frozenset({"df-reviewer[bot]"})
    assert mode_for(ps, "owner/product-b") == "review"
    assert mode_for(ps, "owner/unknown") is None
    assert set(ps.files_sha256) == set(FILES)


def test_classification_uses_set_not_local_copy() -> None:
    pytest.importorskip("steward")
    ps = parse_policy_set(COMMIT, FILES)
    assert classify(ps, "alice", "User") == "human"
    assert classify(ps, "ai-prosto", "User") == "agent"
    assert classify(ps, "stranger", "User") == "unknown"


def test_merger_bot_forms_agree() -> None:
    """Review Focus 2: `slug[bot]` (REST) и `slug` (GraphQL) — одна личность."""
    pytest.importorskip("steward")
    ps = parse_policy_set(COMMIT, FILES)
    assert classify(ps, "df-reviewer[bot]", "Bot") == classify(ps, "df-reviewer", "Bot")


def test_intersection_of_humans_and_agents_refused() -> None:
    files = dict(FILES)
    files["policy/actors.yaml"] = (
        b"version: 1\nhuman_identities: [github:alice]\n"
        b"agent_identities: [github:alice]\n"
    )
    with pytest.raises(PolicyError, match="пересечение"):
        parse_policy_set(COMMIT, files)


@pytest.mark.parametrize("missing", sorted(FILES))
def test_missing_file_refused(missing: str) -> None:
    files = {k: v for k, v in FILES.items() if k != missing}
    with pytest.raises(PolicyError):
        parse_policy_set(COMMIT, files)


def test_unknown_mode_value_refused() -> None:
    files = dict(FILES)
    files["policy/modes.yaml"] = (
        b"schema: approval-policy-modes/v1\nmodes:\n  owner/product-a: maybe\n"
    )
    with pytest.raises(PolicyError):
        parse_policy_set(COMMIT, files)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --group governance pytest tests/test_act_policy.py -v`
Expected: FAIL — `ModuleNotFoundError: governance.act_policy`

Run (контракт без группы): `uv run pytest tests/test_act_policy.py -v` — после реализации
классификационные тесты `SKIPPED (steward)`, остальные PASS.

- [ ] **Step 3: Write minimal implementation**

```python
"""Пинованный набор политик акта `review/v1` (спека §6, Q3, Q8).

Утверждающие, мержеры, режимы продуктов и классификация акторов читаются
из ОДНОГО коммита активации источника. Локальные копии политики акторов
(`contracts/steward-actor-policy/v1`, `steward/profiles/…`) этот модуль не
читает ни при каких условиях. `files_sha256` — контроль целостности
прочитанного набора, не идентичность версии.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

import yaml

from governance import ssot_env

APPROVERS_FILE = "policy/approvers.env"
MODES_FILE = "policy/modes.yaml"
ACTORS_FILE = "policy/actors.yaml"
POLICY_FILES = (APPROVERS_FILE, MODES_FILE, ACTORS_FILE)
MODES_SCHEMA = "approval-policy-modes/v1"
MODES = ("merge", "review")


class PolicyError(ValueError):
    """Набор политик неполон или противоречив → отказ (`unavailable`)."""


@dataclass(frozen=True)
class PolicySet:
    """Политика на одном `activation_commit`."""

    activation_commit: str
    approvers: frozenset[str]
    mergers: frozenset[str]
    modes: Mapping[str, str]
    humans: frozenset[str]
    agents: frozenset[str]
    files_sha256: Mapping[str, str]


def _accounts(text: str, key: str) -> frozenset[str]:
    lines = ssot_env.definition_lines(text, key)
    if len(lines) != 1:
        raise PolicyError(f"{APPROVERS_FILE}: {key} — ожидалась ровно одна строка")
    accounts = frozenset(p.strip() for p in lines[0].split(",") if p.strip())
    if not accounts:
        raise PolicyError(f"{APPROVERS_FILE}: {key} пуст")
    return accounts


def _modes(text: str) -> dict[str, str]:
    data = yaml.safe_load(text)
    if not isinstance(data, dict) or data.get("schema") != MODES_SCHEMA:
        raise PolicyError(f"{MODES_FILE}: схема не {MODES_SCHEMA}")
    modes = data.get("modes")
    if not isinstance(modes, dict) or any(
        not isinstance(k, str) or v not in MODES for k, v in modes.items()
    ):
        raise PolicyError(f"{MODES_FILE}: modes — отображение repo → merge|review")
    return dict(modes)


def _identities(data: dict, field: str) -> frozenset[str]:
    value = data.get(field)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise PolicyError(f"{ACTORS_FILE}: {field} — список строк")
    return frozenset(value)


def parse_policy_set(activation_commit: str, files: Mapping[str, bytes]) -> PolicySet:
    """Разбор набора; любой отсутствующий файл или противоречие — `PolicyError`."""
    missing = [name for name in POLICY_FILES if name not in files]
    if missing:
        raise PolicyError(f"в {activation_commit} нет {', '.join(missing)}")
    text = {name: files[name].decode("utf-8") for name in POLICY_FILES}
    actors = yaml.safe_load(text[ACTORS_FILE])
    if not isinstance(actors, dict) or actors.get("version") != 1:
        raise PolicyError(f"{ACTORS_FILE}: ожидалась version: 1")
    humans = _identities(actors, "human_identities")
    agents = _identities(actors, "agent_identities")
    if humans & agents:
        raise PolicyError(f"{ACTORS_FILE}: пересечение human/agent {sorted(humans & agents)}")
    return PolicySet(
        activation_commit,
        _accounts(text[APPROVERS_FILE], "AUTHORIZED_APPROVER_ACCOUNTS"),
        _accounts(text[APPROVERS_FILE], "AUTHORIZED_MERGER_ACCOUNTS"),
        _modes(text[MODES_FILE]),
        humans,
        agents,
        {name: hashlib.sha256(files[name]).hexdigest() for name in POLICY_FILES},
    )


def _identity(login: str) -> str:
    return f"github:{login.removesuffix('[bot]')}"


def classify(ps: PolicySet, login: str, type_hint: str | None) -> str:
    """Класс актора по набору (steward `classify_actor`): human|agent|unknown."""
    from steward.gatecheck.approval import ApprovalPolicy, classify_actor

    policy = ApprovalPolicy(1, ps.humans, ps.agents)
    return classify_actor(_identity(login), type_hint, policy)


def mode_for(ps: PolicySet, product: str) -> str | None:
    """`ACT_MODE[product]`; записи нет — `None` (режима по умолчанию нет)."""
    return ps.modes.get(product)


def same_login(a: str, b: str) -> bool:
    """Логины равны с точностью до формы бота (`slug[bot]` ≡ `slug`)."""
    return a.removesuffix("[bot]") == b.removesuffix("[bot]")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --group governance pytest tests/test_act_policy.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add governance/act_policy.py tests/test_act_policy.py tests/act_cases.py
git commit -m "feat(act): пинованный набор политик — утверждающие, мержеры, режимы, акторы"
```

### Task 5: Манифест заявки узла `review/v1`

**Files:**
- Create: `governance/act_request.py`
- Test: `tests/test_act_request.py`

**Interfaces:**
- Produces: `ACT_REVIEW_V1 = "review/v1"`; `PolicyPin(repo, branch, activation_commit,
  files_sha256)`; `NodeEntry(path, content_hash, generation)`;
  `NodeRequest(request_id, ws_id, product, nodes: Mapping[str, NodeEntry], upstream_pins,
  policy: PolicyPin)`; `manifest_path(ws_id, request_id) -> str`;
  `render_manifest(req) -> str`; `parse_manifest(text) -> NodeRequest` (raises
  `RequestError`); `ledger_view(req) -> dict` (поля заявки в `run.json`);
  `mirror_mismatch(req, request_key: str, ledger_op: dict, pr_body: str) -> str | None`.

`upstream_pins` обязателен для **каждого** узла (пустое отображение допустимо только у узла без
upstream в DAG — это сверяет Task 13 по DAG); отсутствие ключа — отказ, а не «нет зависимостей».

- [ ] **Step 1: Write the failing test**

```python
"""Манифест заявки в одобряемом коммите (спека §2, §7)."""

from __future__ import annotations

import pytest

from governance.act_request import (
    NodeEntry,
    NodeRequest,
    PolicyPin,
    RequestError,
    ledger_view,
    manifest_path,
    mirror_mismatch,
    parse_manifest,
    render_manifest,
)

PIN = PolicyPin("owner/approval-policy", "main", "e" * 40, {"policy/approvers.env": "1" * 64})
REQ = NodeRequest(
    request_id="approve-2-1-1",
    ws_id="ws-7",
    product="owner/product-b",
    nodes={"requirements": NodeEntry("workstreams/ws-7/requirements.md", "f" * 40, 3)},
    upstream_pins={"requirements": {"charter": "a" * 40}},
    policy=PIN,
)


def test_round_trip() -> None:
    assert parse_manifest(render_manifest(REQ)) == REQ


def test_path_is_inside_workstream() -> None:
    assert manifest_path("ws-7", "approve-2-1-1") == (
        "workstreams/ws-7/approvals/requests/approve-2-1-1.yaml"
    )


def test_unknown_act_refused_before_anything_else() -> None:
    text = render_manifest(REQ).replace("act: review/v1", "act: review/v2")
    with pytest.raises(RequestError, match="review/v2"):
        parse_manifest(text)


def test_manifest_without_nodes_refused() -> None:
    """Review Focus 4: пустая заявка не проходит D2 вакуумно."""
    empty = NodeRequest(REQ.request_id, REQ.ws_id, REQ.product, {}, {}, PIN)
    with pytest.raises(RequestError, match="узл"):
        parse_manifest(render_manifest(empty))


BODY = f"act: review/v1\npolicy-activation: {PIN.activation_commit}\n"


def test_mirror_matches() -> None:
    assert mirror_mismatch(REQ, "approve-2-1-1", ledger_view(REQ), BODY) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("policy", {**PIN.as_record(), "activation_commit": "0" * 40}),
        ("content_hashes", {"requirements": "0" * 40}),
        ("upstream_pins", {"requirements": {}}),
        ("generations", {"requirements": 9}),
        ("act", None),
    ],
)
def test_mirror_rewritten_field_detected(field: str, value: object) -> None:
    """Приёмка №13: любое переписанное зеркало в run.json — отказ."""
    op = {**ledger_view(REQ), field: value}
    assert field in (mirror_mismatch(REQ, "approve-2-1-1", op, BODY) or "")


@pytest.mark.parametrize(
    "extra",
    ["act: merge\n", f"policy-activation: {'0' * 40}\n", "act: review/v1\n"],
    ids=["contradicting-act", "contradicting-pin", "duplicate-same"],
)
def test_body_duplicates_detected(extra: str) -> None:
    """Зеркало в теле: каждый ключ ровно один раз и равен манифесту."""
    assert mirror_mismatch(REQ, "approve-2-1-1", ledger_view(REQ), BODY + extra)


def test_mirror_other_request_key_detected() -> None:
    assert mirror_mismatch(REQ, "approve-9-9-9", ledger_view(REQ), BODY)


def test_missing_upstream_pins_refused() -> None:
    text = render_manifest(REQ).replace("upstream_pins:", "upstream_pinz:")
    with pytest.raises(RequestError, match="upstream"):
        parse_manifest(text)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_act_request.py -v`
Expected: FAIL — `ModuleNotFoundError: governance.act_request`

- [ ] **Step 3: Write minimal implementation**

```python
"""Манифест заявки узла `review/v1` (спека §2, §7).

Заявка записана ВНУТРИ одобряемого коммита:
`workstreams/<ws-id>/approvals/requests/<request-id>.yaml`. Она —
единственный источник режима и пинов; `run.json`, тело PR и конверт —
зеркала, расхождение с манифестом — отказ. Неизвестная версия акта —
отказ до любого вычисления.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import yaml

SCHEMA = "node-approval-request/v1"
ACT_REVIEW_V1 = "review/v1"


class RequestError(ValueError):
    """Манифест не читается или не того вида → отказ без утверждения."""


@dataclass(frozen=True)
class PolicyPin:
    """Единый пин набора политик (§6)."""

    repo: str
    branch: str
    activation_commit: str
    files_sha256: Mapping[str, str]

    def as_record(self) -> dict[str, object]:
        """Форма для манифеста и леджера."""
        return {
            "repo": self.repo,
            "branch": self.branch,
            "activation_commit": self.activation_commit,
            "files_sha256": dict(self.files_sha256),
        }


@dataclass(frozen=True)
class NodeEntry:
    """Узел заявки: путь, хеш одобряемых байтов (`self_hash`), поколение."""

    path: str
    content_hash: str
    generation: int


@dataclass(frozen=True)
class NodeRequest:
    """Заявка узлов, как она лежит в одобренном коммите."""

    request_id: str
    ws_id: str
    product: str
    nodes: Mapping[str, NodeEntry]
    upstream_pins: Mapping[str, Mapping[str, str]]
    policy: PolicyPin


def manifest_path(ws_id: str, request_id: str) -> str:
    """Путь манифеста в репо продукта."""
    return f"workstreams/{ws_id}/approvals/requests/{request_id}.yaml"


def render_manifest(req: NodeRequest) -> str:
    """YAML манифеста; порядок ключей фиксирован ради стабильного диффа."""
    data = {
        "schema": SCHEMA,
        "act": ACT_REVIEW_V1,
        "request_id": req.request_id,
        "ws_id": req.ws_id,
        "product": req.product,
        "nodes": {
            node: {"path": e.path, "content_hash": e.content_hash,
                   "generation": e.generation}
            for node, e in sorted(req.nodes.items())
        },
        "upstream_pins": {n: dict(p) for n, p in sorted(req.upstream_pins.items())},
        "policy": req.policy.as_record(),
    }
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _node(name: str, raw: object) -> NodeEntry:
    if not isinstance(raw, dict):
        raise RequestError(f"узел {name}: неизвестная форма")
    path, digest, gen = raw.get("path"), raw.get("content_hash"), raw.get("generation")
    if not isinstance(path, str) or not isinstance(digest, str):
        raise RequestError(f"узел {name}: нет path/content_hash")
    if not isinstance(gen, int) or gen < 1:
        raise RequestError(f"узел {name}: generation — целое ≥ 1")
    return NodeEntry(path, digest, gen)


def _pin(raw: object) -> PolicyPin:
    if not isinstance(raw, dict):
        raise RequestError("policy: нет пина")
    files = raw.get("files_sha256")
    fields = [raw.get(k) for k in ("repo", "branch", "activation_commit")]
    if not all(isinstance(v, str) and v for v in fields) or not isinstance(files, dict):
        raise RequestError("policy: неполный пин")
    return PolicyPin(*fields, dict(files))  # type: ignore[arg-type]


def parse_manifest(text: str) -> NodeRequest:
    """Разбор манифеста; акт проверяется ПЕРВЫМ."""
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise RequestError("манифест не отображение")
    if data.get("act") != ACT_REVIEW_V1:
        raise RequestError(f"неизвестная версия акта: {data.get('act')!r}")
    if data.get("schema") != SCHEMA:
        raise RequestError(f"схема манифеста не {SCHEMA}")
    nodes_raw = data.get("nodes")
    if not isinstance(nodes_raw, dict) or not nodes_raw:
        raise RequestError("в заявке нет ни одного узла")
    ids = [data.get(k) for k in ("request_id", "ws_id", "product")]
    if not all(isinstance(v, str) and v for v in ids):
        raise RequestError("нет request_id/ws_id/product")
    pins = data.get("upstream_pins")
    if not isinstance(pins, dict) or set(pins) != set(nodes_raw):
        raise RequestError("upstream_pins обязан перечислять каждый узел заявки")
    if not all(isinstance(p, dict) for p in pins.values()):
        raise RequestError("upstream_pins: значение узла — отображение")
    return NodeRequest(
        *ids,  # type: ignore[arg-type]
        nodes={str(n): _node(str(n), raw) for n, raw in nodes_raw.items()},
        upstream_pins={str(n): dict(p) for n, p in pins.items()},
        policy=_pin(data.get("policy")),
    )


def ledger_view(req: NodeRequest) -> dict[str, object]:
    """Поля заявки в `run.json` — зеркало манифеста."""
    return {
        "act": ACT_REVIEW_V1,
        "nodes": sorted(req.nodes),
        "content_hashes": {n: e.content_hash for n, e in req.nodes.items()},
        "generations": {n: e.generation for n, e in req.nodes.items()},
        "upstream_pins": {n: dict(p) for n, p in req.upstream_pins.items()},
        "policy": req.policy.as_record(),
    }


def mirror_mismatch(
    req: NodeRequest, request_key: str, ledger_op: dict, pr_body: str
) -> str | None:
    """Первое расхождение зеркал (`run.json`, тело PR) с манифестом либо `None`."""
    if request_key != req.request_id:
        return f"ключ заявки {request_key} ≠ request_id манифеста {req.request_id}"
    for field, value in ledger_view(req).items():
        got = ledger_op.get(field)
        if field == "nodes":
            got = sorted(got or [])
        if got != value:
            return f"run.json: {field} расходится с манифестом"
    expected = {"act": ACT_REVIEW_V1, "policy-activation": req.policy.activation_commit}
    for key, value in expected.items():
        found = [
            line.split(":", 1)[1].strip()
            for line in pr_body.splitlines()
            if line.split(":", 1)[0].strip() == key and ":" in line
        ]
        if found != [value]:
            return f"тело PR: {key} — ожидалась ровно одна строка {value!r}, есть {found!r}"
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_act_request.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git add governance/act_request.py tests/test_act_request.py
git commit -m "feat(act): манифест заявки узла review/v1 и сверка зеркал (§2, §7)"
```

### Task 5a: Версионированная проекция self-hash узла (§7)

**Files:**
- Create: `governance/act_projection.py`
- Test: `tests/test_act_projection.py`

**Interfaces:**
- Consumes: `governance.node_approval.APPROVAL_ENVELOPE_KEYS`, `self_hash`;
  `governance.frontmatter.split_frontmatter`, `join_frontmatter`;
  `governance.stale_adapter.blob_sha1`.
- Produces: `REVIEW_V1_STAMP_KEYS`; `self_hash_v(text: str, act: str | None) -> str`
  (raises `ProjectionError` для неизвестной версии **до** разбора текста). `act=None` — legacy,
  побайтово тот же результат, что `node_approval.self_hash` (старые доказательства не
  перехешируются). Task 13 считает `head_self_hashes` только через `self_hash_v`.

- [ ] **Step 1: Write the failing test**

```python
"""Проекции self-hash по версиям акта (спека §7)."""

from __future__ import annotations

import pytest

from governance import act_projection
from governance.act_projection import ProjectionError, self_hash_v
from governance.node_approval import self_hash

NODE = "---\nid: requirements\nstatus: draft\nversion: 2\n---\nТело узла.\n"
STAMPED = (
    "---\nid: requirements\nstatus: draft\nversion: 2\napproval_act: review/v1\n"
    "approval_review_id: '11'\ndelivered_by: df-reviewer[bot]\n"
    "delivered_at: '2026-10-12T10:00:00Z'\n---\nТело узла.\n"
)


def test_legacy_projection_unchanged() -> None:
    assert self_hash_v(NODE, None) == self_hash(NODE)


def test_review_v1_hash_same_before_and_after_stamp() -> None:
    assert self_hash_v(NODE, "review/v1") == self_hash_v(STAMPED, "review/v1")


@pytest.mark.parametrize("marker", ["review/v9", "merge"])
def test_foreign_marker_in_node_refused(marker: str) -> None:
    """Маркер во frontmatter неизвестен или противоречит манифесту — отказ до хеша."""
    with pytest.raises(ProjectionError):
        self_hash_v(STAMPED.replace("approval_act: review/v1", f"approval_act: {marker}"),
                    "review/v1")


def test_unknown_act_refused_before_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(text: str) -> tuple[dict, str]:
        raise AssertionError("текст не должен разбираться")

    monkeypatch.setattr(act_projection, "split_frontmatter", explode)
    with pytest.raises(ProjectionError, match="review/v9"):
        self_hash_v(NODE, "review/v9")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_act_projection.py -v`
Expected: FAIL — `ModuleNotFoundError: governance.act_projection`

- [ ] **Step 3: Write minimal implementation**

```python
"""Проекции self-hash узла по версиям акта (спека §7).

Для каждой версии — своё множество исключаемых полей. Инвариант: хеш до
штампа = хеш после штампа. Неизвестная версия — отказ до вычисления.
"""

from __future__ import annotations

from governance.frontmatter import join_frontmatter, split_frontmatter
from governance.node_approval import APPROVAL_ENVELOPE_KEYS
from governance.stale_adapter import blob_sha1

REVIEW_V1_STAMP_KEYS = (
    "approval_act",
    "approval_review_id",
    "delivered_by",
    "delivered_at",
)
_EXCLUDED: dict[str | None, tuple[str, ...]] = {
    None: APPROVAL_ENVELOPE_KEYS,
    "review/v1": APPROVAL_ENVELOPE_KEYS + REVIEW_V1_STAMP_KEYS,
}


class ProjectionError(ValueError):
    """Неизвестная версия акта — хеш не вычисляется."""


def self_hash_v(text: str, act: str | None) -> str:
    """Хеш собственной части узла в проекции версии `act`.

    Маркер `approval_act` во frontmatter, если есть, обязан быть известной
    версией и совпадать с `act` (режимом из манифеста) — иначе отказ до хеша.
    Отсутствие штампа допустимо (узел до финализации).
    """
    if act not in _EXCLUDED:
        raise ProjectionError(f"неизвестная версия акта: {act!r}")
    meta, body = split_frontmatter(text)
    marker = meta.get("approval_act")
    if marker is not None and (marker not in _EXCLUDED or marker != act):
        raise ProjectionError(f"маркер узла {marker!r} ≠ режиму заявки {act!r}")
    for key in _EXCLUDED[act]:
        meta.pop(key, None)
    return blob_sha1(join_frontmatter(meta, body))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_act_projection.py tests/test_governance_node_approval.py -v`
Expected: PASS (новые 5 + существующие без изменений)

- [ ] **Step 5: Commit**

```bash
git add governance/act_projection.py tests/test_act_projection.py
git commit -m "feat(act): версионированная проекция self-hash узла, отказ до хеша (§7)"
```

### Task 6: Admission режима (§10)

**Files:**
- Create: `governance/act_admission.py`
- Test: `tests/test_act_admission.py`

**Interfaces:**
- Consumes: `Clock` (Task 3); `ACT_REVIEW_V1` (Task 5).
- Produces: `ModeEvent(key: EventKey, at: datetime, mode: str)` — активация, меняющая
  `ACT_MODE[P]`, в порядке first-parent; `Admitted(reader: str)` с `reader ∈
  {"legacy", "merge", "review"}`; `Refused(reason: str)` — установленный отказ;
  `admit(request_act: str | None, mode_history: Sequence[ModeEvent], clock: Clock)
  -> Admitted | Refused | Unavailable` — обратное переключение `review → merge` даёт
  `Unavailable` (спека §10: политика недействительна для акта до исправления, заявка жива).

- [ ] **Step 1: Write the failing test**

```python
"""Единый admission режима до выбора читателя (спека §10, R4-4)."""

from __future__ import annotations

from governance.act_admission import Admitted, ModeEvent, Refused, admit
from governance.act_time import Clock, Unavailable, parse_ts

T = parse_ts("2026-10-12T10:00:00Z")
EARLY = parse_ts("2026-10-01T00:00:00Z")
LATE = parse_ts("2026-10-20T00:00:00Z")
M_MERGE = ModeEvent(("activation", "m1"), EARLY, "merge")
M_REVIEW = ModeEvent(("activation", "m2"), parse_ts("2026-10-05T00:00:00Z"), "review")


def test_before_first_mode_entry_is_legacy() -> None:
    late_mode = ModeEvent(("activation", "m1"), LATE, "merge")
    assert admit(None, [late_mode], Clock(T)) == Admitted("legacy")


def test_marker_before_first_mode_entry_refused() -> None:
    assert isinstance(admit("review/v1", [], Clock(T)), Refused)


def test_merge_mode_admits_only_unmarked() -> None:
    assert admit(None, [M_MERGE], Clock(T)) == Admitted("merge")
    assert isinstance(admit("review/v1", [M_MERGE], Clock(T)), Refused)


def test_review_mode_admits_only_marked() -> None:
    """Приёмка №24: v1-заявка после активации review — отказ."""
    assert admit("review/v1", [M_MERGE, M_REVIEW], Clock(T)) == Admitted("review")
    assert isinstance(admit(None, [M_MERGE, M_REVIEW], Clock(T)), Refused)


def test_reverse_switch_unavailable() -> None:
    """Приёмка №26: review → merge в v1 не поддерживается — unavailable, не отказ."""
    back = ModeEvent(("activation", "m3"), parse_ts("2026-10-06T00:00:00Z"), "merge")
    result = admit(None, [M_MERGE, M_REVIEW, back], Clock(T))
    assert isinstance(result, Unavailable) and "обратн" in result.reason


def test_removed_mode_entry_refused() -> None:
    removed = ModeEvent(("activation", "m3"), parse_ts("2026-10-06T00:00:00Z"), "")
    result = admit(None, [M_MERGE, removed], Clock(T))
    assert isinstance(result, Refused) and "отсутствует" in result.reason


def test_mode_activation_in_delivery_second_uses_clock() -> None:
    same = ModeEvent(("activation", "m2"), T, "review")
    before = Clock(T, frozenset({same.key}))
    assert admit(None, [M_MERGE, same], Clock(T)) == Admitted("merge")
    assert isinstance(admit(None, [M_MERGE, same], before), Refused)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_act_admission.py -v`
Expected: FAIL — `ModuleNotFoundError: governance.act_admission`

- [ ] **Step 3: Write minimal implementation**

```python
"""Admission режима акта — до выбора читателя, для обоих путей (спека §10).

`mode_history` — активации источника, менявшие `ACT_MODE[P]`, в порядке
first-parent; первая из них — «активация режима P» (граница legacy-эпохи).
Равная секунда с доставкой решается через `Clock` (§8.2) — вызывающий
перебирает порядки `act_time.resolve`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from governance.act_request import ACT_REVIEW_V1
from governance.act_time import Clock, EventKey, Unavailable


@dataclass(frozen=True)
class ModeEvent:
    """Активация, установившая `ACT_MODE[P] = mode`."""

    key: EventKey
    at: datetime
    mode: str


@dataclass(frozen=True)
class Admitted:
    """Какой читатель проверяет доставку."""

    reader: str


@dataclass(frozen=True)
class Refused:
    """Отказ admission с причиной."""

    reason: str


def admit(
    request_act: str | None, mode_history: Sequence[ModeEvent], clock: Clock
) -> Admitted | Refused | Unavailable:
    """Шаги 1–5 admission §10."""
    effective = [e for e in mode_history if clock.precedes(e.key, e.at)]
    if not effective:
        if request_act is None:
            return Admitted("legacy")
        return Refused("заявка review/v1 до первой записи ACT_MODE продукта")
    modes = [e.mode for e in effective]
    if modes[-1] not in ("merge", "review"):
        return Refused("ACT_MODE продукта отсутствует или неизвестен")
    if "review" in modes and modes[-1] != "review":
        return Unavailable("обратное переключение review → merge не поддерживается")
    if modes[-1] == "merge":
        if request_act is None:
            return Admitted("merge")
        return Refused("ACT_MODE=merge: допустима только заявка без маркера")
    if request_act == ACT_REVIEW_V1:
        return Admitted("review")
    return Refused(f"ACT_MODE=review: заявка {request_act or 'без маркера'} — нужна новая")
```

`mode_history` строится в Task 12 из активаций: активация входит в список, когда
`mode_for(policy_at_activation, P)` отличается от предыдущего значения, включая `None → merge`;
активация, **удалившая** запись P, входит как `ModeEvent(..., mode="")`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_act_admission.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add governance/act_admission.py tests/test_act_admission.py
git commit -m "feat(act): admission режима до выбора читателя (§10)"
```

### Task 7: Решение D1–D3 над фактами

**Files:**
- Create: `governance/act_decision.py`
- Test: `tests/test_act_decision.py`

**Interfaces:**
- Consumes: `Clock`, `PolicySet`, `classify`, `same_login`, `NodeRequest`.
- Produces (локальные зеркала типов M0, чтобы ядро не зависело от steward до Task 11):
  `ReviewView(review_id, login, type_hint, state, commit_id)`;
  `DeliveryView(merged, merge_commit, merged_at, merged_by_login, merged_by_type_hint,
  head_at_merge, in_base, blobs: Mapping[str, tuple[str, str]])`;
  `Accepted(merge_commit, merged_by, review_id, login)` — `review_id`/`login` —
  свидетельства (`compare=False`); `Rejected(code, detail)` — оба поля свидетельства
  (`compare=False`): все отказы равны как вердикт, тесты проверяют `.code`;
  коды `D1_NO_ACT`, `HEAD_MOVED`, `NOT_IN_BASE`, `I_BYTES`, `I_CONTENT`, `D3_MERGER`;
  `WaitingDelivery()`;
  `decide(*, request: NodeRequest, head_sha: str, delivery: DeliveryView,
  effective: Mapping[str, ReviewView], head_self_hashes: Mapping[str, str],
  policy: PolicySet) -> Accepted | Rejected | WaitingDelivery`.

`effective` — результат steward `effective_reviews` на текущем `Clock` (в тестах — готовое
отображение). `head_self_hashes` — хеши байтов узлов на head, посчитанные вызывающим через
`act_projection.self_hash_v(text, "review/v1")` (Task 5a).

- [ ] **Step 1: Write the failing test**

```python
"""D1–D3 на срезе T (спека §3)."""

from __future__ import annotations

import dataclasses

import pytest

pytest.importorskip("steward")  # decide → classify → steward classify_actor

from governance.act_decision import (  # noqa: E402
    D3_MERGER,
    I_BYTES,
    I_CONTENT,
    Accepted,
    DeliveryView,
    Rejected,
    ReviewView,
    WaitingDelivery,
    decide,
)
from governance.act_policy import parse_policy_set
from governance.act_request import NodeEntry, NodeRequest, PolicyPin
from tests.act_cases import COMMIT, FILES, code

HEAD = "1" * 40
PATH = "workstreams/ws-7/requirements.md"
REQ = NodeRequest(
    "approve-2-1-1", "ws-7", "owner/product-b",
    {"requirements": NodeEntry(PATH, "h" * 40, 1)}, {},
    PolicyPin("owner/approval-policy", "main", COMMIT, {}),
)
DELIVERY = DeliveryView(
    merged=True, merge_commit="m" * 40, merged_at="2026-10-12T10:00:00Z",
    merged_by_login="df-reviewer[bot]", merged_by_type_hint="Bot",
    head_at_merge=HEAD, in_base=True, blobs={PATH: ("b" * 40, "b" * 40)},
)
APPROVE = ReviewView("11", "alice", "User", "APPROVED", HEAD)
POLICY = parse_policy_set(COMMIT, FILES)


def run(**over):
    args = dict(
        request=REQ, head_sha=HEAD, delivery=DELIVERY, effective={"alice": APPROVE},
        head_self_hashes={PATH: "h" * 40}, policy=POLICY,
    )
    args.update(over)
    return decide(**args)


def test_happy_path_accepted() -> None:
    result = run()
    assert result == Accepted("m" * 40, "df-reviewer[bot]")
    assert isinstance(result, Accepted)
    assert (result.review_id, result.login) == ("11", "alice")


def test_not_merged_waits() -> None:
    assert run(delivery=dataclasses.replace(DELIVERY, merged=False)) == WaitingDelivery()


@pytest.mark.parametrize(
    "effective",
    [
        {"alice": dataclasses.replace(APPROVE, commit_id="0" * 40)},
        {"alice": dataclasses.replace(APPROVE, state="DISMISSED")},
        {"mallory": dataclasses.replace(APPROVE, login="mallory")},
        {"alice": dataclasses.replace(APPROVE, type_hint="Bot")},
        {},
    ],
    ids=["stale-commit", "dismissed", "not-approver", "bot-hint", "no-review"],
)
def test_d1_refusals(effective) -> None:
    assert isinstance(run(effective=effective), Rejected)


def test_latest_review_commit_wins() -> None:
    """Review Focus 3: эффективный — последний; старый commit_id не подтягивается."""
    newer_on_old = dataclasses.replace(APPROVE, review_id="12", commit_id="0" * 40)
    assert isinstance(run(effective={"alice": newer_on_old}), Rejected)


def test_head_moved_before_merge_rejected() -> None:
    moved = dataclasses.replace(DELIVERY, head_at_merge="2" * 40)
    assert isinstance(run(delivery=moved), Rejected)


def test_bytes_and_content_are_separate_invariants() -> None:
    """Приёмка №18: I-bytes и I-content — разные исходы."""
    bytes_moved = dataclasses.replace(DELIVERY, blobs={PATH: ("c" * 40, "b" * 40)})
    assert code(run(delivery=bytes_moved)) == I_BYTES
    assert code(run(head_self_hashes={PATH: "x" * 40})) == I_CONTENT


def test_merger_human_approver_allowed() -> None:
    human = dataclasses.replace(DELIVERY, merged_by_login="alice", merged_by_type_hint="User")
    assert isinstance(run(delivery=human), Accepted)


def test_agent_in_approvers_but_not_merger_refused() -> None:
    """Приёмка №16 / D3: agent проходит только через allowlist merger."""
    files = dict(FILES)
    files["policy/approvers.env"] = (
        b"AUTHORIZED_APPROVER_ACCOUNTS=alice,ai-prosto\n"
        b"AUTHORIZED_MERGER_ACCOUNTS=df-reviewer[bot]\n"
    )
    policy = parse_policy_set(COMMIT, files)
    agent = dataclasses.replace(
        DELIVERY, merged_by_login="ai-prosto", merged_by_type_hint="User"
    )
    assert code(run(delivery=agent, policy=policy)) == D3_MERGER


def test_not_in_base_rejected() -> None:
    assert isinstance(run(delivery=dataclasses.replace(DELIVERY, in_base=False)), Rejected)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --group governance pytest tests/test_act_decision.py -v`
Expected: FAIL — `ModuleNotFoundError: governance.act_decision`

- [ ] **Step 3: Write minimal implementation**

```python
"""Решение об акте и доставке: D1–D3 на срезе T (спека §3, §8).

Чистая функция: факты уже нормализованы на текущем `Clock` (steward
`effective_reviews`), политика — набор на пине заявки, проверенный
вызывающим на совпадение с активацией на T. Здесь нет чтений форджи.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from governance.act_policy import PolicySet, classify, same_login
from governance.act_request import NodeRequest

D1_NO_ACT = "d1_no_act"
HEAD_MOVED = "head_moved"
NOT_IN_BASE = "not_in_base"
I_BYTES = "i_bytes"
I_CONTENT = "i_content"
D3_MERGER = "d3_merger"


@dataclass(frozen=True)
class ReviewView:
    """Эффективное ревью логина на T (восстановленное состояние)."""

    review_id: str
    login: str
    type_hint: str
    state: str
    commit_id: str


@dataclass(frozen=True)
class DeliveryView:
    """Доставка: факты мержа и пары blob (merge, head) по путям."""

    merged: bool
    merge_commit: str | None
    merged_at: str | None
    merged_by_login: str | None
    merged_by_type_hint: str | None
    head_at_merge: str | None
    in_base: bool
    blobs: Mapping[str, tuple[str, str]]


@dataclass(frozen=True)
class Accepted:
    """Акт действителен, доставка засчитана; approve — свидетельство."""

    merge_commit: str
    merged_by: str
    review_id: str = field(default="", compare=False)
    login: str = field(default="", compare=False)


@dataclass(frozen=True)
class Rejected:
    """Установленный отказ. Вердикт — сам факт отказа; `code` и `detail` —
    свидетельства (разные причины отказа в разных порядках §8.2 — один
    вердикт «отказ», не неоднозначность). Ветвиться по `code` можно только
    после `resolve`."""

    code: str = field(compare=False)
    detail: str = field(default="", compare=False)


@dataclass(frozen=True)
class WaitingDelivery:
    """PR не смёржен — ничего не продвигается."""


Decision = Accepted | Rejected | WaitingDelivery


def _act(head_sha: str, effective, policy) -> ReviewView | None:
    for login, review in sorted(effective.items()):
        if (
            review.state == "APPROVED"
            and review.commit_id == head_sha
            and login in policy.approvers
            and review.type_hint != "Bot"
            and classify(policy, login, review.type_hint) == "human"
        ):
            return review
    return None


def _merger_ok(policy: PolicySet, login: str, hint: str | None) -> bool:
    actor = classify(policy, login, hint)
    if actor == "agent":
        return any(same_login(login, m) for m in policy.mergers)
    return actor == "human" and hint != "Bot" and login in policy.approvers


def _delivery_refusal(
    request: NodeRequest, head_sha: str, d: DeliveryView, self_hashes
) -> Rejected | None:
    if d.head_at_merge != head_sha:
        return Rejected(HEAD_MOVED, f"голова при мерже {d.head_at_merge} ≠ {head_sha}")
    if not d.in_base:
        return Rejected(NOT_IN_BASE, "merge-коммит недостижим из базы")
    for entry in request.nodes.values():
        at_merge, at_head = d.blobs.get(entry.path, (None, None))
        if at_merge is None or at_merge != at_head:
            return Rejected(I_BYTES, f"{entry.path} доставлен не теми байтами")
        if self_hashes.get(entry.path) != entry.content_hash:
            return Rejected(I_CONTENT, f"{entry.path} не соответствует заявке")
    return None


def decide(
    *,
    request: NodeRequest,
    head_sha: str,
    delivery: DeliveryView,
    effective: Mapping[str, ReviewView],
    head_self_hashes: Mapping[str, str],
    policy: PolicySet,
) -> Decision:
    """D1–D3 по одному срезу; порядок — доставка, акт, байты, мержер."""
    if not delivery.merged:
        return WaitingDelivery()
    act = _act(head_sha, effective, policy)
    if act is None:
        return Rejected(D1_NO_ACT, f"нет действительного approve человека на {head_sha}")
    refusal = _delivery_refusal(request, head_sha, delivery, head_self_hashes)
    if refusal is not None:
        return refusal
    login = delivery.merged_by_login or ""
    if not _merger_ok(policy, login, delivery.merged_by_type_hint):
        return Rejected(D3_MERGER, f"мержер {login or '?'} не имеет права доставки")
    return Accepted(delivery.merge_commit or "", login, act.review_id, act.login)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --group governance pytest tests/test_act_decision.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add governance/act_decision.py tests/test_act_decision.py
git commit -m "feat(act): решение D1–D3 и инварианты I-bytes/I-content (§3)"
```

### Task 8: Конверт узла, история поколений и остановки

**Files:**
- Create: `governance/act_envelope.py`
- Test: `tests/test_act_envelope.py`

**Interfaces:**
- Consumes: `NodeRequest`, `PolicyPin`, `manifest_path`.
- Produces:
  `Envelope(node, request_id, generation, manifest_path, head_sha, self_hash, upstream_pins,
  merge_commit, review_id, policy)` — `policy` — `PolicyPin.as_record()` (единая схема пина §6);
  `envelope_path(ws_id, node) -> str`; `render_envelope(env) -> str`;
  `parse_envelope(text) -> Envelope` (raises `EnvelopeError`);
  `StopRecord(node, generation, reason)`; `stop_path(ws_id, node, generation) -> str`;
  `render_stop(rec) -> str`; `parse_stop(text) -> StopRecord`;
  `DeliveredRequest(merge_commit, head_sha, request: NodeRequest)`;
  `DeliveredStop(merge_commit, stop: StopRecord)`;
  `History = Sequence[DeliveredRequest | DeliveredStop]` (first-parent базы, старые первыми);
  `current_event(history, node) -> DeliveredRequest | DeliveredStop | None`;
  `generation_refusal(history, node) -> str | None`;
  `next_generation(history, node) -> int`;
  `envelope_refusal(env, history, node, node_self_hash, upstream_pins, review_id)
  -> str | None`.

Остановка дальнейшего использования (спека §8, Q1; приёмка №25) — явное действие человека
`--reopen <node>` в режиме `review/v1` доставляет **запись остановки**
`workstreams/<ws>/approvals/stops/<node>-g<generation>.yaml`. Запись может только отозвать
одобрение, поэтому её доставка агентским мержем безопасна. Действующее одобрение — последнее по
first-parent событие узла; остановка делает узел «не одобрен», пока не доставлена новая заявка с
бОльшим поколением. Поколения узла по истории строго возрастают; нарушение — отказ.

- [ ] **Step 1: Write the failing test**

```python
"""Конверт узла и история поколений (спека §9.1 (а), §8, приёмка №17, 23, 25, 31)."""

from __future__ import annotations

import dataclasses

from governance.act_envelope import (
    DeliveredRequest,
    DeliveredStop,
    Envelope,
    StopRecord,
    current_event,
    envelope_path,
    envelope_refusal,
    generation_refusal,
    next_generation,
    parse_envelope,
    parse_stop,
    render_envelope,
    render_stop,
    stop_path,
)
from governance.act_request import NodeEntry, NodeRequest, PolicyPin, manifest_path

PIN = PolicyPin("o/approval-policy", "main", "e" * 40, {})
PINS = {"charter": "a" * 40}


def _req(rid: str, gen: int, digest: str = "h" * 40) -> NodeRequest:
    return NodeRequest(
        rid, "ws-7", "o/p", {"req": NodeEntry("workstreams/ws-7/req.md", digest, gen)},
        {"req": dict(PINS)}, PIN,
    )


OLD = DeliveredRequest("1" * 40, "a1" * 20, _req("approve-2-1-1", 1))
NEW = DeliveredRequest("2" * 40, "a2" * 20, _req("approve-2-1-2", 2))
STOP2 = DeliveredStop("3" * 40, StopRecord("req", 2, "переоткрыт человеком"))
AGAIN = DeliveredRequest("4" * 40, "a4" * 20, _req("approve-2-1-3", 3))


def _env(delivered: DeliveredRequest, review_id: str = "21") -> Envelope:
    req = delivered.request
    return Envelope(
        node="req", request_id=req.request_id, generation=req.nodes["req"].generation,
        manifest_path=manifest_path("ws-7", req.request_id), head_sha=delivered.head_sha,
        self_hash="h" * 40, upstream_pins=dict(PINS), merge_commit=delivered.merge_commit,
        review_id=review_id, policy=PIN.as_record(),
    )


def test_round_trips_and_paths() -> None:
    assert parse_envelope(render_envelope(_env(NEW))) == _env(NEW)
    assert parse_stop(render_stop(STOP2.stop)) == STOP2.stop
    assert envelope_path("ws-7", "req") == "workstreams/ws-7/approvals/req.yaml"
    assert stop_path("ws-7", "req", 2) == "workstreams/ws-7/approvals/stops/req-g2.yaml"


def test_current_event_is_last_delivered() -> None:
    assert current_event([OLD, NEW], "req") is NEW
    assert current_event([OLD, NEW, STOP2], "req") is STOP2
    assert current_event([OLD, NEW], "other") is None


def test_valid_envelope_accepted() -> None:
    assert envelope_refusal(_env(NEW), [OLD, NEW], "req", "h" * 40, PINS, "21") is None


def test_missing_envelope_not_approved_without_fallback() -> None:
    """Приёмка №23."""
    reason = envelope_refusal(None, [OLD, NEW], "req", "h" * 40, PINS, "21")
    assert reason and "нет конверта" in reason


def test_previous_generation_same_bytes_refused() -> None:
    """Приёмка №31."""
    reason = envelope_refusal(_env(OLD), [OLD, NEW], "req", "h" * 40, PINS, "21")
    assert reason and "поколени" in reason


def test_other_node_envelope_refused() -> None:
    """Приёмка №17."""
    other = dataclasses.replace(_env(NEW), node="design")
    assert envelope_refusal(other, [OLD, NEW], "req", "h" * 40, PINS, "21")


def test_reopen_with_unchanged_content_stops_approval() -> None:
    """Приёмка №25: остановка — явное действие; прежний конверт больше не действует."""
    reason = envelope_refusal(_env(NEW), [OLD, NEW, STOP2], "req", "h" * 40, PINS, "21")
    assert reason and "останов" in reason


def test_new_generation_after_stop_approved() -> None:
    history = [OLD, NEW, STOP2, AGAIN]
    assert envelope_refusal(_env(AGAIN), history, "req", "h" * 40, PINS, "21") is None
    assert next_generation(history, "req") == 4


def test_non_monotonic_generation_refused() -> None:
    stale = DeliveredRequest("5" * 40, "a5" * 20, _req("approve-2-1-4", 2))
    assert generation_refusal([OLD, NEW, stale], "req")


def test_envelope_with_other_policy_pin_refused() -> None:
    swapped = dataclasses.replace(_env(NEW), policy={**PIN.as_record(), "branch": "x"})
    assert envelope_refusal(swapped, [OLD, NEW], "req", "h" * 40, PINS, "21")


def test_node_bytes_changed_refused() -> None:
    assert envelope_refusal(_env(NEW), [OLD, NEW], "req", "z" * 40, PINS, "21")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_act_envelope.py -v`
Expected: FAIL — `ModuleNotFoundError: governance.act_envelope`

- [ ] **Step 3: Write minimal implementation**

```python
"""Конверт утверждения узла `review/v1` и история поколений (спека §9.1 (а), Q5, Q1).

Конверт `workstreams/<ws-id>/approvals/<node>.yaml` лежит вне путей
CODEOWNERS: его пишет агентский finalize. Он НЕ источник полномочий:
режим, продукт, узел и поколение устанавливаются через историю доставок
базовой ветки (заявки и записи остановки); конверт лишь обязан совпасть с
действующим событием. Отсутствующий или несовпадающий конверт — «не
одобрен», без fallback на frontmatter.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass

import yaml

from governance.act_request import NodeRequest, manifest_path

ENVELOPE_SCHEMA = "node-approval-envelope/v1"
STOP_SCHEMA = "node-approval-stop/v1"


class EnvelopeError(ValueError):
    """Конверт или запись остановки не читается → «не одобрен»."""


@dataclass(frozen=True)
class Envelope:
    """Идентичность одобрения узла (§9.1 п. 1) с единым пином политики."""

    node: str
    request_id: str
    generation: int
    manifest_path: str
    head_sha: str
    self_hash: str
    upstream_pins: Mapping[str, str]
    merge_commit: str
    review_id: str
    policy: Mapping[str, object]


@dataclass(frozen=True)
class StopRecord:
    """Явная остановка дальнейшего использования одобрения поколения."""

    node: str
    generation: int
    reason: str


@dataclass(frozen=True)
class DeliveredRequest:
    """Доставленный candidate: merge-коммит, одобренный head и его манифест."""

    merge_commit: str
    head_sha: str
    request: NodeRequest


@dataclass(frozen=True)
class DeliveredStop:
    """Доставленная запись остановки."""

    merge_commit: str
    stop: StopRecord


Event = DeliveredRequest | DeliveredStop


def envelope_path(ws_id: str, node: str) -> str:
    """Путь конверта в репо продукта."""
    return f"workstreams/{ws_id}/approvals/{node}.yaml"


def stop_path(ws_id: str, node: str, generation: int) -> str:
    """Путь записи остановки."""
    return f"workstreams/{ws_id}/approvals/stops/{node}-g{generation}.yaml"


def _dump(schema: str, data: dict) -> str:
    return yaml.safe_dump({"schema": schema, **data}, sort_keys=False, allow_unicode=True)


def _load(text: str, schema: str) -> dict:
    data = yaml.safe_load(text)
    if not isinstance(data, dict) or data.pop("schema", None) != schema:
        raise EnvelopeError(f"схема не {schema}")
    return data


def render_envelope(env: Envelope) -> str:
    """YAML конверта."""
    data = asdict(env)
    data["upstream_pins"] = dict(env.upstream_pins)
    data["policy"] = dict(env.policy)
    return _dump(ENVELOPE_SCHEMA, data)


def parse_envelope(text: str) -> Envelope:
    """Разбор конверта; неизвестная форма — `EnvelopeError`."""
    try:
        return Envelope(**_load(text, ENVELOPE_SCHEMA))
    except TypeError as exc:
        raise EnvelopeError(f"конверт: {exc}") from exc


def render_stop(rec: StopRecord) -> str:
    """YAML записи остановки."""
    return _dump(STOP_SCHEMA, asdict(rec))


def parse_stop(text: str) -> StopRecord:
    """Разбор записи остановки."""
    try:
        return StopRecord(**_load(text, STOP_SCHEMA))
    except TypeError as exc:
        raise EnvelopeError(f"запись остановки: {exc}") from exc


def _touches(event: Event, node: str) -> bool:
    if isinstance(event, DeliveredStop):
        return event.stop.node == node
    return node in event.request.nodes


def _generation(event: Event, node: str) -> int:
    if isinstance(event, DeliveredStop):
        return event.stop.generation
    return event.request.nodes[node].generation


def current_event(history: Sequence[Event], node: str) -> Event | None:
    """Действующее событие узла: последнее по first-parent."""
    for event in reversed(history):
        if _touches(event, node):
            return event
    return None


def generation_refusal(history: Sequence[Event], node: str) -> str | None:
    """Поколения заявок узла строго растут; остановка — того же поколения."""
    last = 0
    for event in history:
        if not _touches(event, node):
            continue
        gen = _generation(event, node)
        if isinstance(event, DeliveredStop):
            if gen != last:
                return f"узел {node}: остановка поколения {gen}, действует {last}"
        elif gen <= last:
            return f"узел {node}: поколение {gen} не больше прежнего {last}"
        last = gen
    return None


def next_generation(history: Sequence[Event], node: str) -> int:
    """Поколение следующей заявки узла."""
    gens = [_generation(e, node) for e in history if _touches(e, node)]
    return max(gens, default=0) + 1


def _expected(current: DeliveredRequest, node: str, review_id: str) -> Envelope:
    req = current.request
    return Envelope(
        node=node,
        request_id=req.request_id,
        generation=req.nodes[node].generation,
        manifest_path=manifest_path(req.ws_id, req.request_id),
        head_sha=current.head_sha,
        self_hash=req.nodes[node].content_hash,
        upstream_pins=dict(req.upstream_pins[node]),
        merge_commit=current.merge_commit,
        review_id=review_id,
        policy=req.policy.as_record(),
    )


def envelope_refusal(
    env: Envelope | None,
    history: Sequence[Event],
    node: str,
    node_self_hash: str,
    upstream_pins: Mapping[str, str],
    review_id: str,
) -> str | None:
    """Причина «не одобрен» либо `None` (§9.1 п. 0a, 1, 2; §8 остановка)."""
    broken = generation_refusal(history, node)
    if broken is not None:
        return broken
    current = current_event(history, node)
    if current is None:
        return f"узел {node}: нет доставленной заявки review/v1"
    if isinstance(current, DeliveredStop):
        return f"узел {node}: одобрение поколения {current.stop.generation} остановлено"
    if env is None:
        return f"узел {node}: нет конверта — не одобрен"
    expected = _expected(current, node, review_id)
    if env.node != node:
        return f"конверт узла {env.node} подставлен для {node}"
    if env.generation != expected.generation or env.request_id != expected.request_id:
        return f"узел {node}: конверт другого поколения ({env.generation})"
    if env != expected:
        return f"узел {node}: конверт не совпадает с манифестом заявки"
    if node_self_hash != expected.self_hash or dict(upstream_pins) != expected.upstream_pins:
        return f"узел {node}: байты или upstream-пины изменились после одобрения"
    return None
```

`review_id` — из решения, переустановленного по фактам форджа для `current.merge_commit`
(§9.1 п. 1): вызывающий (Task 14) выполняет `act_check.check_delivery` для этой доставки и
передаёт `Accepted.review_id`; решение не `Accepted` — узел «не одобрен».

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_act_envelope.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add governance/act_envelope.py tests/test_act_envelope.py
git commit -m "feat(act): конверт узла, история поколений и записи остановки (§9.1, §8)"
```

### Task 9: Периметр — маркер обнаружения и самоотчёт прав

**Files:**
- Create: `governance/act_perimeter.py`
- Test: `tests/test_act_perimeter.py`

**Interfaces:**
- Consumes: `Epoch` (Task 2).
- Produces: `marker_path(source_repo: str, state_home: Path) -> Path`;
  `record_violation(source_repo, epoch_no, detail, state_home) -> None`;
  `blocked(source_repo, epochs, state_home) -> str | None`;
  `permissions_refusal(permissions: Mapping[str, object] | None) -> str | None`.

- [ ] **Step 1: Write the failing test**

```python
"""Периметр (спека §6.2 б, §6.3): маркер и самоотчёт прав."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governance.act_epochs import Epoch
from governance.act_perimeter import blocked, permissions_refusal, record_violation

SRC = "owner/approval-policy"
E1 = (Epoch(1, "a" * 40),)
E2 = (Epoch(1, "a" * 40, "c" * 40, "2026-10-20T10:00:00Z", "x"), Epoch(2, "b" * 40))


def test_no_marker_not_blocked(tmp_path: Path) -> None:
    assert blocked(SRC, E1, tmp_path) is None


def test_marker_blocks_until_newer_epoch(tmp_path: Path) -> None:
    """Приёмка №36: перезапуск сохраняет блокировку до эпохи с большим номером."""
    record_violation(SRC, 1, "push=true у токена", tmp_path)
    assert blocked(SRC, E1, tmp_path)
    assert blocked(SRC, E2, tmp_path) is None


def test_unreadable_marker_blocks(tmp_path: Path) -> None:
    record_violation(SRC, 1, "x", tmp_path)
    next(tmp_path.rglob("*.json")).write_text("{", encoding="utf-8")
    assert blocked(SRC, E2, tmp_path)


@pytest.mark.parametrize("epoch", [0, -1, True, 1.5, "1", None, "broken"])
def test_malformed_marker_epoch_blocks(tmp_path: Path, epoch: object) -> None:
    """Битый номер эпохи в маркере блокирует, а не снимает блокировку."""
    path = tmp_path / "darkfactory" / "perimeter" / "owner__approval-policy.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"epoch": epoch}), encoding="utf-8")
    assert blocked(SRC, E2, tmp_path)


def test_late_writer_does_not_lower_marker(tmp_path: Path) -> None:
    """P4-2: E2 обнаружил, запоздалый писатель E1 не снимает блокировку E2."""
    record_violation(SRC, 2, "обнаружено в E2", tmp_path)
    record_violation(SRC, 1, "запоздалый E1", tmp_path)
    assert blocked(SRC, E2, tmp_path)


def test_corrupt_marker_not_overwritten(tmp_path: Path) -> None:
    record_violation(SRC, 1, "x", tmp_path)
    next(tmp_path.rglob("*.json")).write_text("{", encoding="utf-8")
    record_violation(SRC, 1, "повтор", tmp_path)
    assert blocked(SRC, E2, tmp_path)


def test_write_permissions_refused() -> None:
    """Приёмка №29."""
    assert permissions_refusal({"push": True, "maintain": False, "admin": False})


def test_missing_permissions_is_not_confirmation() -> None:
    """Приёмка №33: недоступное ≠ подтверждённое."""
    assert permissions_refusal(None)
    assert permissions_refusal({"push": False})


def test_read_only_passes() -> None:
    assert permissions_refusal({"push": False, "maintain": False, "admin": False}) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_act_perimeter.py -v`
Expected: FAIL — `ModuleNotFoundError: governance.act_perimeter`

- [ ] **Step 3: Write minimal implementation**

```python
"""Периметр источника политики (спека §6.2 б, §6.3).

Потребитель, обнаруживший нарушение, пишет долговечный маркер с номером
текущей эпохи и отказывает во всех решениях источника, пока в журнале не
появится эпоха с бОльшим номером. Канал уведомления человека — не часть
протокола. Битый маркер блокирует (fail-closed).
"""

from __future__ import annotations

import fcntl
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from governance.act_epochs import Epoch

_WRITE = ("push", "maintain", "admin")


def marker_path(source_repo: str, state_home: Path) -> Path:
    """`$XDG_STATE_HOME/darkfactory/perimeter/<owner>__<repo>.json`."""
    return state_home / "darkfactory" / "perimeter" / (
        source_repo.replace("/", "__") + ".json"
    )


def _marked_epoch(path: Path) -> int | None:
    """Номер эпохи маркера; `None` — маркер битый (блокирует)."""
    try:
        marked = json.loads(path.read_text(encoding="utf-8")).get("epoch")
    except (OSError, ValueError, AttributeError):
        return None
    if type(marked) is not int or marked < 1:
        return None
    return marked


def record_violation(
    source_repo: str, epoch_no: int, detail: str, state_home: Path
) -> None:
    """Записать маркер монотонно: под `flock` хранится максимум эпох обнаружения.

    Запоздалый писатель с меньшей эпохой маркер не понижает; битый маркер не
    перезаписывается (он и так блокирует — снимает его человек).
    """
    path = marker_path(source_repo, state_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            current = _marked_epoch(path)
            if current is None or current >= epoch_no:
                return
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"epoch": epoch_no, "detail": detail}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)


def blocked(source_repo: str, epochs: Sequence[Epoch], state_home: Path) -> str | None:
    """Причина блокировки источника либо `None`."""
    path = marker_path(source_repo, state_home)
    if not path.exists():
        return None
    marked = _marked_epoch(path)
    if marked is None:
        return f"маркер периметра {path} не читается — источник заблокирован"
    if epochs[-1].epoch > marked:
        return None
    return (
        f"нарушение периметра обнаружено в эпохе {marked}; ждём эпоху с большим "
        "номером в журнале (§6.3)"
    )


def permissions_refusal(permissions: Mapping[str, object] | None) -> str | None:
    """Самоотчёт прав credential на источнике: запись → отказ; нет поля → отказ."""
    if permissions is None or any(k not in permissions for k in _WRITE):
        return "права credential на источнике не установлены (недоступное ≠ подтверждённое)"
    granted = [k for k in _WRITE if permissions[k] is not False]
    if granted:
        return f"у credential процесса есть {', '.join(granted)} на источнике политики"
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_act_perimeter.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git add governance/act_perimeter.py tests/test_act_perimeter.py
git commit -m "feat(act): маркер периметра и самоотчёт прав credential (§6.2–6.3)"
```

### Task 10: Настройка установки `act.toml`

**Files:**
- Create: `governance/act_settings.py`
- Test: `tests/test_act_settings.py`

**Interfaces:**
- Produces: `ActSettings(review_v1: bool, draining: bool)`;
  `load_settings(config_home: Path) -> ActSettings`; `DEFAULT_REVIEW_V1 = False`
  (Task 19 меняет на `True`).

- [ ] **Step 1: Write the failing test**

```python
"""Локальная настройка только останавливает, ничего не разрешает (§10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from governance.act_settings import ActSettings, SettingsError, load_settings


def _write(home: Path, text: str) -> None:
    (home / "darkfactory").mkdir(parents=True)
    (home / "darkfactory" / "act.toml").write_text(text, encoding="utf-8")


def test_absent_file_gives_defaults(tmp_path: Path) -> None:
    assert load_settings(tmp_path) == ActSettings(review_v1=False, draining=False)


def test_reads_flags(tmp_path: Path) -> None:
    _write(tmp_path, 'review_v1 = true\nact_migration = "draining"\n')
    assert load_settings(tmp_path) == ActSettings(review_v1=True, draining=True)


@pytest.mark.parametrize("text", ['act_migration = "open"\n', "review_v1 = 1\n", "x = 1\n"])
def test_unknown_values_refused(tmp_path: Path, text: str) -> None:
    _write(tmp_path, text)
    with pytest.raises(SettingsError):
        load_settings(tmp_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_act_settings.py -v`
Expected: FAIL — `ModuleNotFoundError: governance.act_settings`

- [ ] **Step 3: Write minimal implementation**

```python
"""Локальная настройка установки акта (спека §10, «барьер миграции» п. 1).

`review_v1` — создавать новые заявки в режиме `review/v1` там, где
`ACT_MODE[P] = review`; `act_migration = "draining"` — не создавать новых
заявок без маркера. Обе только сужают: режим допуска определяет политика.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_REVIEW_V1 = False
_KEYS = {"review_v1", "act_migration"}


class SettingsError(ValueError):
    """Настройка не читается — отказ конфигурации."""


@dataclass(frozen=True)
class ActSettings:
    """Флаги установки."""

    review_v1: bool
    draining: bool


def load_settings(config_home: Path) -> ActSettings:
    """`$XDG_CONFIG_HOME/darkfactory/act.toml`; файла нет — умолчания."""
    path = config_home / "darkfactory" / "act.toml"
    if not path.exists():
        return ActSettings(DEFAULT_REVIEW_V1, False)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if set(data) - _KEYS:
        raise SettingsError(f"{path}: неизвестные ключи {sorted(set(data) - _KEYS)}")
    review = data.get("review_v1", DEFAULT_REVIEW_V1)
    migration = data.get("act_migration", "off")
    if not isinstance(review, bool) or migration not in ("off", "draining"):
        raise SettingsError(f"{path}: review_v1 — bool, act_migration — off|draining")
    return ActSettings(review, migration == "draining")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_act_settings.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Lint, types, full suite, commit**

Run: `uv run ruff format governance/act_*.py tests/test_act_*.py && uv run ruff check governance tests && pyrefly check && uv run --group governance pytest -q`
Expected: no lint errors, pyrefly clean, whole suite green.

```bash
git add governance/act_settings.py tests/test_act_settings.py
git commit -m "feat(act): локальная настройка установки — review_v1 и draining (§10)"
```

**Конец части A → PR devtools #1** (`feat/act-review-core`): authority-root, мерж человеком.
Ревью — `review-pr.sh … --dry-run --write-verdict`, затем `--use-verdict`.

---

## Часть H — шаги человека (между A и B)

### H1: Набор политик в `approval-policy`

Делает человек (не агент — у агента нет записи в источник по построению):

1. PR в `approval-policy`:
   - `policy/approvers.env` += `AUTHORIZED_MERGER_ACCOUNTS=<App>[bot]` (для текущего флота — пока
     `ai-prosto`, вариант Б придёт со спекой B);
   - `policy/actors.yaml` — перенос из `steward/profiles/approval-policy.yaml` (`version: 1`,
     `human_identities`, `agent_identities`), без пересечений;
   - `policy/modes.yaml` — `schema: approval-policy-modes/v1`, `modes:` все существующие
     продукты флота со значением `merge` (Q8: первая запись существующего продукта — `merge`).
     Список — из `ai-orchestrators-workspace/workspace-manifest.toml`; стенд
     `andrei-shtanakov/df-roles-probe: review` (новый продукт).
2. Настройки репо `approval-policy`: `allow_rebase_merge = false`; ruleset на `main`: только PR,
   запрет force-push и удаления, без bypass.
3. Мерж методом merge-commit. Записать `merge_commit` этого PR — это `bootstrap_activation`.
4. Для приёмки M3 (Task 18, фаза 1) — отдельный стенд-источник
   `andrei-shtanakov/df-roles-probe-policy` с тем же составом файлов, теми же настройками и своим
   bootstrap; на нём допустимы разрушительные сценарии приёмки. И стендовый forge-origin devtools
   `andrei-shtanakov/df-roles-probe-devtools` с контрактом v2 на стенд-источник (Task 18).

### H2: Bootstrap контракта v2 в devtools

PR devtools (authority-root, мерж человеком):

- `contracts/approval-policy-source/v2/source.env`:

```
APPROVAL_POLICY_REPO=andrei-shtanakov/approval-policy
APPROVAL_POLICY_BRANCH=main
BOOTSTRAP_ACTIVATION=<merge_commit из H1 п. 3>
POLICY_ADMINS=andrei-shtanakov
```

- `contracts/approval-policy-source/v2/epochs.yaml`:

```yaml
schema: approval-policy-epochs/v1
epochs:
  - epoch: 1
    anchor: <тот же merge_commit>
```

- тест `tests/test_act_contract_files.py`: оба файла разбираются `parse_source` /
  `parse_journal`, `epochs[0].anchor == source.bootstrap_activation`.
- CI-проверка перехода журнала (§6.3: «CI devtools … сверяет новую версию с предыдущей»):
  `main()` в `act_epochs.py` — `python governance/act_epochs.py --check-transition <base-sha>`:
  `git cat-file -e <base-sha>^{commit}` (нет коммита → код 2, отказ CI); `git show
  <base-sha>:contracts/approval-policy-source/v2/epochs.yaml` → `check_transition(prev, new)`;
  файла **в существующем** базовом коммите нет (`git ls-tree` пуст) → допустим только bootstrap:
  текущий журнал — одна открытая эпоха с якорем `BOOTSTRAP_ACTIVATION`. Базовый SHA
  **неизменяемый**, не ветка: шаг в `ci.yml` job `test` —
  `BASE_SHA: ${{ github.event.pull_request.base.sha || github.event.before }}`, перед ним
  `git fetch --no-tags --depth=1 origin "$BASE_SHA"` (checkout сейчас неглубокий). На push в
  `master` это `before` — версия до пуша, а не `origin/master`, уже содержащий правку.
  Workflow — `.github/`, то есть authority-root; это тот же человеческий PR.
- Тесты `tests/test_act_epochs_cli.py` (временный git-репо): запрещённая смена якоря →
  ненулевой код; правка закрытой эпохи → ненулевой; законное атомарное закрытие → 0;
  отсутствующий файл в существующем базовом коммите + bootstrap-журнал → 0; несуществующий
  `BASE_SHA` → 2.

---

## Часть B — интеграция узла (после M0 steward)

### Task 11: Пин steward v3, адаптер фактов, вендор векторов

**Files:**
- Modify: `pyproject.toml` (`[tool.uv.sources] steward rev = <релиз M0>`), `uv.lock`
- Create: `governance/act_facts.py`
- Create: `contracts/approval-facts/v3/vectors/` (копия из steward) + `PINNED.txt`
- Test: `tests/test_act_facts_vectors.py`

**Interfaces:**
- Consumes: «Интерфейс M0».
- Produces: `read(ops_transport, repo, pr, paths) -> tuple[DeliveryView,
  ReviewFacts | None] | Unavailable`; `effective(facts, clock: Clock) ->
  dict[str, ReviewView] | Unavailable`; `history(transport, source: SourceV2, anchor: str,
  until: str | None) -> tuple[Activation, ...] | Unavailable`; `event_stamps(facts)
  -> dict[EventKey, datetime]` и `review_requires(facts) -> dict[EventKey,
  frozenset[EventKey]]` (отправка ревью предшествует его отзыву).

- [ ] **Step 1: Vendor the vectors**

```bash
STEWARD_REV=<sha релиза M0>
mkdir -p contracts/approval-facts/v3/vectors
git -C ../steward archive "$STEWARD_REV" contracts/approval-facts/v3/vectors \
  | tar -x --strip-components=4 -C contracts/approval-facts/v3/vectors
( cd contracts/approval-facts/v3/vectors && \
  printf 'source: steward@%s contracts/approval-facts/v3/vectors\n' "$STEWARD_REV" \
  > ../PINNED.txt && find . -type f | sort | xargs shasum -a 256 >> ../PINNED.txt )
```

- [ ] **Step 2: Write the failing test**

```python
"""Векторы сбора §5.6 steward, прогнанные через адаптер devtools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("steward")

from governance import act_facts  # noqa: E402
from governance.act_time import Clock, Unavailable, parse_ts  # noqa: E402

VECTORS = Path(__file__).resolve().parent.parent / "contracts/approval-facts/v3/vectors"


def _cases() -> list[Path]:
    return sorted(VECTORS.glob("*.json"))


def test_vectors_vendored() -> None:
    assert _cases(), "векторы v3 не завендорены"


def test_pinned_digests_match() -> None:
    act_facts.verify_vendored_vectors(VECTORS.parent / "PINNED.txt")


@pytest.mark.parametrize("case", _cases(), ids=lambda p: p.stem)
def test_vector(case: Path) -> None:
    spec = json.loads(case.read_text(encoding="utf-8"))
    transport = act_facts.replay_transport(spec["transport"])
    got = act_facts.read(transport, spec["repo"], spec["pr"], spec.get("paths", []))
    if "unavailable" in spec["expect"]:
        assert isinstance(got, Unavailable)
        return
    delivery, facts = got
    clock = Clock(parse_ts(spec["t"]), frozenset(map(tuple, spec.get("before", []))))
    eff = act_facts.effective(facts, clock)
    assert {k: v.state for k, v in eff.items()} == spec["expect"]["effective"]
```

The vector file shape (`transport`, `repo`, `pr`, `t`, `before`, `expect`) is what the steward
inbox issue asks for; if steward ships a different layout, adapt this test, not the vectors.
`replay_transport` and `verify_vendored_vectors` are re-exported from steward's test helpers if
steward ships them; otherwise implement `verify_vendored_vectors` here (same algorithm as
`governance/discovery_approval.py:pins()` + digest check).

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run --group governance pytest tests/test_act_facts_vectors.py -v`
Expected: FAIL — `ModuleNotFoundError: governance.act_facts`

- [ ] **Step 4: Write the adapter**

```python
"""Единственная точка импорта общего читателя фактов steward (спека D4, §5).

Остальные `act_*` модули видят только свои типы (`DeliveryView`,
`ReviewView`): смена API steward меняет этот файл и ничего больше.
steward импортируется внутри функций (`_v3()`), чтобы модуль загружался без
группы governance (контракт `ci.yml:27`).
Вторая реализация логики ревью в devtools запрещена — здесь только
перекладка полей.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from governance.act_decision import DeliveryView, ReviewView
from governance.act_source import SourceV2
from governance.act_time import Clock, EventKey, Unavailable, parse_ts


def _v3():
    from steward.approvalfacts import v3

    return v3


def _delivery(d) -> DeliveryView:
    return DeliveryView(
        merged=d.merged,
        merge_commit=d.merge_commit,
        merged_at=d.merged_at,
        merged_by_login=d.merged_by_login,
        merged_by_type_hint=d.merged_by_type_hint,
        head_at_merge=d.head_at_merge,
        in_base=d.in_base,
        blobs={p.path: (p.blob_at_merge, p.blob_at_head) for p in d.paths},
    )


def read(transport, repo: str, pr: int, paths: Sequence[str]):
    """Факты доставки и ревью по протоколу §5.5 (steward)."""
    v3 = _v3()
    got = v3.read_act_facts(transport, repo, pr, paths=paths)
    if isinstance(got, v3.Unavailable):
        return Unavailable(got.reason)
    delivery, facts = got
    return _delivery(delivery), facts


def effective(facts, clock: Clock):
    """Эффективные ревью на `clock` (steward §5.3)."""
    stamp = clock.t.strftime("%Y-%m-%dT%H:%M:%SZ")
    v3 = _v3()
    got = v3.effective_reviews(facts, stamp, before=clock.before)
    if isinstance(got, v3.Unavailable):
        return Unavailable(got.reason)
    return {
        login: ReviewView(r.review_id, r.login, r.type_hint, r.state, r.commit_id)
        for login, r in got.items()
    }


def event_stamps(facts) -> dict[EventKey, datetime]:
    """Метки событий ревью/отзывов для сборки группы равных меток."""
    stamps: dict[EventKey, datetime] = {}
    for r in facts.reviews:
        if r.submitted_at is not None:
            stamps[("review", r.review_id)] = parse_ts(r.submitted_at)
    for d in facts.dismissals:
        stamps[("dismissal", d.review_id)] = parse_ts(d.created_at)
    return stamps


def review_requires(facts) -> dict[EventKey, frozenset[EventKey]]:
    """Отправка ревью предшествует его отзыву (§8.2 п. 2)."""
    return {
        ("dismissal", d.review_id): frozenset({("review", d.review_id)})
        for d in facts.dismissals
    }


def history(transport, source: SourceV2, anchor: str, until: str | None):
    """Активации источника в диапазоне эпохи (steward §6.1)."""
    v3 = _v3()
    got = v3.read_policy_history(
        transport, source.repo, source.branch, anchor=anchor, until=until
    )
    if isinstance(got, v3.Unavailable):
        return Unavailable(got.reason)
    return got  # tuple[Activation, ...] | v3.HistoryViolation — различает Task 12a


def open_review(transport, repo: str, pr: int):
    """Предпроверка открытого PR (steward `read_open_review`)."""
    v3 = _v3()
    got = v3.read_open_review(transport, repo, pr)
    if isinstance(got, v3.Unavailable):
        return Unavailable(got.reason)
    return got


def activation(transport, source: SourceV2, merge_commit: str):
    """Факт одной активации — время якоря эпохи."""
    v3 = _v3()
    got = v3.read_activation(transport, source.repo, merge_commit)
    if isinstance(got, v3.Unavailable):
        return Unavailable(got.reason)
    return got
```

Transport: steward takes an injected GitHub transport. Production transport = steward's
default `gh`-based one under the agent profile (`GH_CONFIG_DIR` of `ai-prosto`, as
`ops.pr_facts` uses today) — take it from steward (`v3.GhTransport(...)` or equivalent named in
the M0 release notes).

- [ ] **Step 5: Run tests, commit**

Run: `uv sync --group governance && uv run --group governance pytest tests/test_act_facts_vectors.py tests/test_act_*.py -v`
Expected: PASS

```bash
git add pyproject.toml uv.lock governance/act_facts.py contracts/approval-facts/v3 \
  tests/test_act_facts_vectors.py
git commit -m "feat(act): пин steward approval-facts/v3, адаптер и векторы сбора (§5)"
```

### Task 12: Полная проверка доставки (`act_check`)

**Files:**
- Create: `governance/act_check.py`
- Create: `tests/act_check_cases.py` (фабрика контекста; импортирует steward)
- Test: `tests/test_act_check.py`

**Interfaces:**
- Consumes: Tasks 2–8, 11.
- Produces: `ActivationView(key: EventKey, merge_commit: str, at: datetime)` — ключ
  `("activation", merge_commit)`; **тот же ключ** у якоря эпохи (одно событие форджа — один
  ключ); `DeliveryContext(...)` (поля — в коде ниже); `OLD_READER = Admitted("old")`;
  коды отказа `EPOCH_GAP`, `MARKER_BEFORE_MODE`, `ADMISSION`, `NO_MANIFEST`,
  `POLICY_SOURCE`, `MIXED_POLICY`, `POLICY_CHANGED` (= `approval_facts.INVALIDATION_POLICY_CHANGED`);
  `LegacyAccepted(merged_by)` — вердикт старого пути; `ctx.legacy: LegacyAccepted |
  Rejected | Unavailable | None` — **готовый** вердикт старого кода потребителя;
  `check_delivery(ctx) -> Accepted | LegacyAccepted | Rejected | WaitingDelivery |
  Unavailable | Ambiguous`; `live_policy_refusal(pin: PolicyPin, current_activation: str)
  -> str | None`.

**Полный исход в каждом порядке (§8.2 п. 3).** Старый путь — не «маршрут», а вердикт: в каждом
порядке, где admission даёт legacy/merge, `_decide` возвращает `ctx.legacy`. Этот вердикт
**вычисляет сам потребитель его собственным старым кодом до перебора** (§10: legacy
«проверяется старым кодом … и прежними проверками»), без побочных эффектов: для узла —
`approve_node._legacy_outcome` (Task 13: все проверки `_reconcile_candidate` — опубликованный
состав, состояние, пин политики, подпись — без `invalidate_request`), для брифа —
`brief_provenance.legacy_outcome` (Task 17: прежний `read_act` на пине, без требования
«пин = текущая»; допуск upstream — отдельно по P/W/C). Старая проверка от порядка событий в T не
зависит (она смотрит на мерж и пин, не на время), поэтому одно значение годится для всех
порядков, а копий правил в ядре нет — полнота по построению. `resolve` сравнивает полные
вердикты; маршрутизация и записи леджера — **после** `resolve`. Контрпримеры: `merge → review`
ровно в T, немаркированная заявка; старый вердикт — отказ (мержер вне allowlist **или**
неполный опубликованный состав) → `Rejected` в обоих порядках, не `Ambiguous`.

Порядок `_decide(ctx, clock)` (спека §6.4, §10, §6, §3):
1. Эпоха доставки. До якоря эпохи 1 — доказанный pre-bootstrap legacy: заявка без маркера →
   `OLD_READER`, с маркером → `Rejected(MARKER_BEFORE_MODE)`. Межэпохальный интервал →
   `Rejected(EPOCH_GAP)` для **любой** заявки.
2. Диапазон эпохи (`ctx.ranges[epoch]`): не прочитан → `Unavailable`; `merged_at`
   противоречит first-parent → `Unavailable`. История режима и активация на T берутся **только**
   из диапазона эпохи доставки (e7: старая история не требуется; e5: недоступный диапазон своей
   эпохи → `Unavailable`).
3. Admission по истории режима диапазона → `Refused` → `Rejected(ADMISSION)`;
   `Unavailable` → `Unavailable`; legacy/merge → `OLD_READER` (оба — старый код, §10).
4. `review`: манифест есть; пин `repo/branch` = источник v2; `activation_commit` = активация на T;
   `files_sha256` = набор; набор прочитан (`PolicySet`, не `Unavailable`).
5. `act_facts.effective` → `Unavailable` сохраняется как `Unavailable`; затем `decide`.

- [ ] **Step 1: Write the failing tests**

Фабрика — в **отдельном** `tests/act_check_cases.py` (импортирует steward; `act_cases.py`
остаётся без него):

```python
from datetime import datetime

from steward.approvalfacts import v3

from tests.act_cases import FILES

from governance.act_check import ActivationView, DeliveryContext
from governance.act_decision import DeliveryView
from governance.act_epochs import Epoch
from governance.act_policy import PolicySet, parse_policy_set
from governance.act_request import NodeEntry, NodeRequest, PolicyPin
from governance.act_source import SourceV2
from governance.act_time import Unavailable, parse_ts

PRODUCT = "owner/product-b"
HEAD = "1" * 40
PATH = "workstreams/ws-7/requirements.md"
DIGEST = "h" * 40
SOURCE = SourceV2("owner/approval-policy", "main", f"{'p1':0<40}", frozenset({"owner"}))


def ts(hms: str) -> str:
    return f"2026-10-12T{hms}Z"


def sha(name: str) -> str:
    return f"{name:0<40}"


def act(name: str, hms: str) -> ActivationView:
    return ActivationView(("activation", sha(name)), sha(name), parse_ts(ts(hms)))


def policy_files(mode: str | None) -> dict[str, bytes]:
    files = dict(FILES)
    modes = f"  {PRODUCT}: {mode}\n" if mode else "  owner/other: merge\n"
    files["policy/modes.yaml"] = (
        b"schema: approval-policy-modes/v1\nmodes:\n" + modes.encode()
    )
    return files


def context(
    *,
    ranges: dict[int, tuple[ActivationView, ...] | Unavailable] | None = None,
    modes: dict[str, str | None] | None = None,
    pin: str = "p1",
    approve_at: str = "09:50:00",
    dismissals: tuple[tuple[str, str], ...] = (),
    merged_at: str = "10:00:00",
    request_act: str | None = "review/v1",
    epochs: tuple[Epoch, ...] = (Epoch(1, sha("p1")),),
    anchor_at: dict[str, datetime | Unavailable] | None = None,
    unreadable_sets: frozenset[str] = frozenset(),
    merger: tuple[str, str] = ("df-reviewer[bot]", "Bot"),
    legacy: object = None,
) -> DeliveryContext:
    ranges = ranges or {1: (act("p1", "08:00:00"),)}
    activations = [a for span in ranges.values() if isinstance(span, tuple) for a in span]
    modes = modes or {a.merge_commit: "review" for a in activations}
    sets: dict[str, PolicySet | Unavailable] = {
        a.merge_commit: (
            Unavailable("набор не прочитан")
            if a.merge_commit in unreadable_sets
            else parse_policy_set(a.merge_commit, policy_files(modes[a.merge_commit]))
        )
        for a in activations
    }
    pinned = sets.get(sha(pin))
    digests = pinned.files_sha256 if isinstance(pinned, PolicySet) else {}
    request = NodeRequest(
        "approve-2-1-1", "ws-7", PRODUCT, {"requirements": NodeEntry(PATH, DIGEST, 1)},
        {"requirements": {}}, PolicyPin(SOURCE.repo, SOURCE.branch, sha(pin), digests),
    )
    facts = v3.ReviewFacts(
        PRODUCT, 7, HEAD, "main", "MERGED",
        (v3.Review("11", "alice", "User", "APPROVED", HEAD, ts(approve_at)),),
        tuple(v3.Dismissal(rid, "alice", ts(at), "APPROVED") for rid, at in dismissals),
    )
    delivery = DeliveryView(
        True, "m" * 40, ts(merged_at), merger[0], merger[1], HEAD, True,
        {PATH: ("b" * 40, "b" * 40)},
    )
    return DeliveryContext(
        source=SOURCE, product=PRODUCT, request=request, request_act=request_act,
        head_sha=HEAD, delivery=delivery, facts=facts, head_self_hashes={PATH: DIGEST},
        epochs=epochs,
        anchor_at=anchor_at or {sha("p1"): parse_ts(ts("08:00:00"))},
        ranges=ranges, policy_sets=sets, legacy=legacy,
    )
```

`tests/test_act_check.py`:

```python
"""Полная проверка доставки на T (спека §6, §6.4, §8, §10)."""

from __future__ import annotations

import pytest

pytest.importorskip("steward")

from governance.act_check import (  # noqa: E402
    ADMISSION,
    EPOCH_GAP,
    POLICY_CHANGED,
    LegacyAccepted,
    check_delivery,
)
from governance.act_decision import Accepted, Rejected
from governance.act_epochs import Epoch
from governance.act_time import Ambiguous, Unavailable, parse_ts
from tests.act_cases import code  # noqa: E402
from tests.act_check_cases import act, context, sha, ts  # noqa: E402

TWO_EPOCHS_GAP = (
    Epoch(1, sha("p1"), sha("p1"), ts("09:00:00"), "нарушение"),
    Epoch(2, sha("p2")),
)
ANCHORS = {sha("p1"): parse_ts(ts("08:00:00")), sha("p2"): parse_ts(ts("11:00:00"))}


def test_happy_path() -> None:
    """§8.1 №1 / приёмка №1."""
    assert isinstance(check_delivery(context()), Accepted)


def test_policy_changed_before_merge() -> None:
    """§8.1 №4, приёмка №8, №14."""
    ranges = {1: (act("p1", "08:00:00"), act("p2", "09:30:00"))}
    assert code(check_delivery(context(ranges=ranges))) == POLICY_CHANGED


def test_policy_changed_after_merge_accepted() -> None:
    """§8.1 №5, приёмка №15."""
    ranges = {1: (act("p1", "08:00:00"), act("p2", "10:30:00"))}
    assert isinstance(check_delivery(context(ranges=ranges)), Accepted)


def test_revert_to_same_bytes_is_new_activation() -> None:
    """§8.1 №12, приёмка №22."""
    ranges = {1: (act("p1", "08:00:00"), act("p2", "09:55:00"), act("p3", "09:56:00"))}
    assert code(check_delivery(context(ranges=ranges))) == POLICY_CHANGED


def test_two_activations_same_second_first_parent_wins() -> None:
    """Приёмка №30."""
    ranges = {1: (act("p1", "08:00:00"), act("p2", "09:00:00"), act("p3", "09:00:00"))}
    assert code(check_delivery(context(ranges=ranges, pin="p2"))) == POLICY_CHANGED
    assert isinstance(check_delivery(context(ranges=ranges, pin="p3")), Accepted)


def test_activation_same_second_differing_outcome_ambiguous() -> None:
    """§8.1 №10, приёмка №20."""
    ranges = {1: (act("p1", "08:00:00"), act("p2", "10:00:00"))}
    assert check_delivery(context(ranges=ranges)) == Ambiguous()


def test_mode_switch_same_second_ambiguous() -> None:
    """Приёмка №27."""
    ranges = {1: (act("p1", "08:00:00"), act("p2", "10:00:00"))}
    modes = {sha("p1"): "merge", sha("p2"): "review"}
    ctx = context(ranges=ranges, modes=modes, request_act=None,
                  legacy=LegacyAccepted("df-reviewer[bot]"))
    assert check_delivery(ctx) == Ambiguous()


def test_first_merge_entry_same_second_admitted() -> None:
    """Приёмка №27a."""
    ranges = {1: (act("p1", "08:00:00"), act("p2", "10:00:00"))}
    modes = {sha("p1"): None, sha("p2"): "merge"}
    ctx = context(ranges=ranges, modes=modes, request_act=None,
                  legacy=LegacyAccepted("df-reviewer[bot]"))
    assert check_delivery(ctx) == LegacyAccepted("df-reviewer[bot]")


@pytest.mark.parametrize("legacy_code", ["legacy_merger", "legacy_unpublished"])
def test_switch_in_t_rejected_both_sides_is_rejection(legacy_code: str) -> None:
    """P4-1: merge→review в T, немаркированная заявка, старый код отказал (мержер или
    неполный состав) — отказ в обоих порядках → отказ, не неоднозначность."""
    ranges = {1: (act("p1", "08:00:00"), act("p2", "10:00:00"))}
    modes = {sha("p1"): "merge", sha("p2"): "review"}
    ctx = context(ranges=ranges, modes=modes, request_act=None,
                  legacy=Rejected(legacy_code))
    assert code(check_delivery(ctx)) in (legacy_code, ADMISSION)


def test_rejected_for_different_reasons_is_one_verdict() -> None:
    """Approve в T + запрещённый мержер: D1 в одном порядке, D3 в другом — отказ, не
    неоднозначность (§8.2: сравниваются исходы)."""
    ctx = context(approve_at="10:00:00", merger=("stranger", "User"))
    assert code(check_delivery(ctx)) in ("d1_no_act", "d3_merger")


def test_policy_changed_constant_matches_ledger() -> None:
    from governance.approval_facts import INVALIDATION_POLICY_CHANGED

    assert POLICY_CHANGED == INVALIDATION_POLICY_CHANGED


def test_only_approve_in_merge_second_ambiguous() -> None:
    """Приёмка №47."""
    assert check_delivery(context(approve_at="10:00:00")) == Ambiguous()


def test_dismissal_in_merge_second_ambiguous() -> None:
    """Приёмка №46 — approve раньше T, отзыв в T."""
    assert check_delivery(context(dismissals=(("11", "10:00:00"),))) == Ambiguous()


def test_interepoch_gap_refused_for_review() -> None:
    """Приёмка №42 / e3."""
    ctx = context(epochs=TWO_EPOCHS_GAP, anchor_at=ANCHORS)
    assert code(check_delivery(ctx)) == EPOCH_GAP


def test_interepoch_gap_refused_for_unmarked_merge_mode() -> None:
    """e3 для старого читателя: OLD_READER в разрыве не выдаётся."""
    modes = {sha("p1"): "merge"}
    ctx = context(epochs=TWO_EPOCHS_GAP, anchor_at=ANCHORS, modes=modes, request_act=None)
    assert code(check_delivery(ctx)) == EPOCH_GAP


def test_closed_epoch_legit_old_act() -> None:
    """Приёмка №41 / e2."""
    epochs = (Epoch(1, sha("p1"), sha("p1"), ts("12:00:00"), "позже"), Epoch(2, sha("p2")))
    anchors = {sha("p1"): parse_ts(ts("08:00:00")), sha("p2"): parse_ts(ts("13:00:00"))}
    assert isinstance(check_delivery(context(epochs=epochs, anchor_at=anchors)), Accepted)


def test_new_epoch_does_not_need_old_range() -> None:
    """e7: акт в E2, диапазон E1 недоступен — проверка от якоря E2."""
    epochs = (Epoch(1, sha("p0"), sha("p0"), ts("07:00:00"), "разрыв"), Epoch(2, sha("p1")))
    anchors = {sha("p0"): parse_ts(ts("06:00:00")), sha("p1"): parse_ts(ts("08:00:00"))}
    ranges = {1: Unavailable("объекты удалены"), 2: (act("p1", "08:00:00"),)}
    ctx = context(epochs=epochs, anchor_at=anchors, ranges=ranges)
    assert isinstance(check_delivery(ctx), Accepted)


def test_new_epoch_cannot_restart_migration() -> None:
    """P2-1: E1 переключил продукт на review; E2 начинается с merge — обратное
    переключение через границу эпох → unavailable, не OLD_READER."""
    epochs = (Epoch(1, sha("p0"), sha("p0"), ts("07:00:00"), "разрыв"), Epoch(2, sha("p1")))
    anchors = {sha("p0"): parse_ts(ts("06:00:00")), sha("p1"): parse_ts(ts("08:00:00"))}
    ranges = {1: (act("p0", "06:00:00"),), 2: (act("p1", "08:00:00"),)}
    modes = {sha("p0"): "review", sha("p1"): "merge"}
    ctx = context(epochs=epochs, anchor_at=anchors, ranges=ranges, modes=modes,
                  request_act=None, pin="p1")
    assert isinstance(check_delivery(ctx), Unavailable)


def test_new_epoch_without_entry_after_review_refused() -> None:
    """P2-1: E2 без записи продукта после review в E1 → отказ admission."""
    epochs = (Epoch(1, sha("p0"), sha("p0"), ts("07:00:00"), "разрыв"), Epoch(2, sha("p1")))
    anchors = {sha("p0"): parse_ts(ts("06:00:00")), sha("p1"): parse_ts(ts("08:00:00"))}
    ranges = {1: (act("p0", "06:00:00"),), 2: (act("p1", "08:00:00"),)}
    modes = {sha("p0"): "review", sha("p1"): None}
    ctx = context(epochs=epochs, anchor_at=anchors, ranges=ranges, modes=modes,
                  request_act=None, pin="p1")
    assert code(check_delivery(ctx)) == ADMISSION


def test_merge_mode_in_new_epoch_needs_prior_history() -> None:
    """Без предыстории режим merge в E2 не проверяем → unavailable."""
    epochs = (Epoch(1, sha("p0"), sha("p0"), ts("07:00:00"), "разрыв"), Epoch(2, sha("p1")))
    anchors = {sha("p0"): parse_ts(ts("06:00:00")), sha("p1"): parse_ts(ts("08:00:00"))}
    ranges = {1: Unavailable("объекты удалены"), 2: (act("p1", "08:00:00"),)}
    ctx = context(epochs=epochs, anchor_at=anchors, ranges=ranges,
                  modes={sha("p1"): "merge"}, request_act=None, pin="p1")
    assert isinstance(check_delivery(ctx), Unavailable)


def test_old_anchor_time_not_needed_for_new_epoch() -> None:
    """P1-4 / e7: время якоря E1 недоступно — акт E2 всё равно проверяется."""
    epochs = (Epoch(1, sha("p0"), sha("p0"), ts("07:00:00"), "разрыв"), Epoch(2, sha("p1")))
    anchors = {sha("p0"): Unavailable("нет объекта"), sha("p1"): parse_ts(ts("08:00:00"))}
    ranges = {1: Unavailable("объекты удалены"), 2: (act("p1", "08:00:00"),)}
    ctx = context(epochs=epochs, anchor_at=anchors, ranges=ranges, pin="p1")
    assert isinstance(check_delivery(ctx), Accepted)


def test_unreadable_policy_after_t_does_not_block_act() -> None:
    """P2-2: набор активации ПОСЛЕ T не читается — законный акт принят."""
    ranges = {1: (act("p1", "08:00:00"), act("p2", "11:00:00"))}
    ctx = context(ranges=ranges, unreadable_sets=frozenset({sha("p2")}))
    assert isinstance(check_delivery(ctx), Accepted)


def test_own_epoch_range_unavailable() -> None:
    """e5 без доступных объектов своей эпохи → unavailable, не отказ."""
    ctx = context(ranges={1: Unavailable("объекты удалены")})
    assert isinstance(check_delivery(ctx), Unavailable)


def test_unreadable_policy_set_is_unavailable() -> None:
    ctx = context(unreadable_sets=frozenset({sha("p1")}))
    assert isinstance(check_delivery(ctx), Unavailable)


def test_unmarked_after_review_mode_refused() -> None:
    """Приёмка №24."""
    assert code(check_delivery(context(request_act=None))) == ADMISSION


def test_anchor_and_activation_are_one_event() -> None:
    """Якорь эпохи в секунду доставки — одно событие, а не два независимых."""
    epochs = (Epoch(1, sha("p0"), sha("p0"), ts("10:00:00"), "x"), Epoch(2, sha("p1")))
    anchors = {sha("p0"): parse_ts(ts("07:00:00")), sha("p1"): parse_ts(ts("10:00:00"))}
    ranges = {1: (act("p0", "07:00:00"),), 2: (act("p1", "10:00:00"),)}
    ctx = context(epochs=epochs, anchor_at=anchors, ranges=ranges, pin="p1")
    assert check_delivery(ctx) == Ambiguous()
```

Остальные строки §8.1 (№2, 3, 6, 7, 9, 11) и приёмки (№7, 9, 19, 32, 43, 44, 46a, 48, 49)
добавляются тем же приёмом: один вызов фабрики `context(...)`, один assert на ожидание из колонки
«Ожидание» §11.1 дословно. Для №19 (дата коммита ≠ время активации) фабрика уже не знает дат
коммита — тест проверяет, что `ActivationView.at` берётся из `merged_at` (Task 12a).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group governance pytest tests/test_act_check.py -v`
Expected: FAIL — `ImportError: cannot import name 'ActivationView'`

- [ ] **Step 3: Write the implementation**

```python
"""Полная проверка доставки на срезе T (спека §6, §6.4, §8, §10).

`_decide(ctx, clock)` проходит: эпоха → диапазон → admission → пин и
набор → эффективные ревью → D1–D3. `check_delivery` перебирает её по всем
допустимым множествам событий группы равных меток (`act_time.resolve`).
Чтений форджи здесь нет. Неустановленное остаётся `Unavailable`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from governance import act_facts
from governance.act_admission import Admitted, ModeEvent, Refused, admit
from governance.act_decision import (
    Accepted,
    DeliveryView,
    Rejected,
    WaitingDelivery,
    decide,
)
from governance.act_epochs import Epoch
from governance.act_policy import PolicySet, mode_for
from governance.act_request import NodeRequest, PolicyPin
from governance.act_source import SourceV2
from governance.act_time import (
    Ambiguous,
    Clock,
    EventKey,
    Unavailable,
    parse_ts,
    resolve,
)

OLD_READER = Admitted("old")
EPOCH_GAP = "epoch_gap"
MARKER_BEFORE_MODE = "marker_before_mode"
ADMISSION = "admission"
NO_MANIFEST = "no_manifest"
POLICY_SOURCE = "policy_source"
MIXED_POLICY = "mixed_policy"
#: Литерал, а не импорт `approval_facts`: тот тянет `governance.ops`, а цепочка
#: импортов act_* обязана оставаться под authority-root (Task 15). Равенство с
#: `approval_facts.INVALIDATION_POLICY_CHANGED` держит тест.
POLICY_CHANGED = "policy_changed"

Span = tuple["ActivationView", ...]


@dataclass(frozen=True)
class ActivationView:
    """Активация источника; ключ совпадает с ключом якоря эпохи."""

    key: EventKey
    merge_commit: str
    at: datetime


@dataclass(frozen=True)
class DeliveryContext:
    """Всё, что решение читает; собирается из форджи (Task 12a, 13)."""

    source: SourceV2
    product: str
    request: NodeRequest | None
    request_act: str | None
    head_sha: str
    delivery: DeliveryView
    facts: object
    head_self_hashes: Mapping[str, str]
    epochs: tuple[Epoch, ...]
    anchor_at: Mapping[str, datetime | Unavailable]
    ranges: Mapping[int, Span | Unavailable]
    policy_sets: Mapping[str, PolicySet | Unavailable]
    legacy: "LegacyAccepted | Rejected | Unavailable | None" = None


@dataclass(frozen=True)
class LegacyAccepted:
    """Старый путь: старый код потребителя признал доставку подписью."""

    merged_by: str

Outcome = (
    Accepted | LegacyAccepted | Rejected | WaitingDelivery | Admitted | Unavailable | Ambiguous
)


def check_delivery(ctx: DeliveryContext) -> Outcome:
    """Исход доставки по §8.2: единственный вердикт либо `Ambiguous`."""
    merged_at = ctx.delivery.merged_at
    if not ctx.delivery.merged or merged_at is None:
        return WaitingDelivery()
    t = parse_ts(merged_at)
    group = sorted(k for k, at in _stamps(ctx).items() if at == t)
    return resolve(t, group, _requires(ctx), lambda clock: _decide(ctx, clock))


def live_policy_refusal(pin: PolicyPin, current_activation: str) -> str | None:
    """Живая заявка: пин обязан совпадать с текущей активацией (§6)."""
    if pin.activation_commit == current_activation:
        return None
    return (
        f"{POLICY_CHANGED}: заявка закрепила {pin.activation_commit}, "
        f"действует {current_activation}"
    )


def _activations(ctx: DeliveryContext) -> list[ActivationView]:
    return [a for span in ctx.ranges.values() if isinstance(span, tuple) for a in span]


def _stamps(ctx: DeliveryContext) -> dict[EventKey, datetime]:
    stamps = dict(act_facts.event_stamps(ctx.facts))
    stamps.update({a.key: a.at for a in _activations(ctx)})
    for e in ctx.epochs:
        anchor = ctx.anchor_at.get(e.anchor)
        if isinstance(anchor, datetime):
            stamps[("activation", e.anchor)] = anchor
        if e.valid_until is not None:
            stamps[("epoch_valid_until", str(e.epoch))] = parse_ts(e.valid_until)
    return stamps


def _requires(ctx: DeliveryContext) -> dict[EventKey, frozenset[EventKey]]:
    requires = dict(act_facts.review_requires(ctx.facts))
    for span in ctx.ranges.values():
        if not isinstance(span, tuple):
            continue
        for i, a in enumerate(span):
            same = frozenset(b.key for b in span[:i] if b.at == a.at)
            if same:
                requires[a.key] = requires.get(a.key, frozenset()) | same
    return requires


def _epoch_at(ctx: DeliveryContext, clock: Clock) -> Epoch | None | Rejected | Unavailable:
    """Эпоха доставки: обход от последней эпохи назад (e7: старые якоря не нужны,
    если более поздний уже предшествует T)."""
    for epoch in reversed(ctx.epochs):
        anchor = ctx.anchor_at.get(epoch.anchor, Unavailable("время якоря не загружено"))
        if isinstance(anchor, Unavailable):
            return anchor
        if not clock.precedes(("activation", epoch.anchor), anchor):
            continue
        if epoch.valid_until is not None and clock.precedes(
            ("epoch_valid_until", str(epoch.epoch)), parse_ts(epoch.valid_until)
        ):
            return Rejected(EPOCH_GAP, "межэпохальный интервал: политика не определена")
        return epoch
    return None


def _span(ctx: DeliveryContext, epoch: Epoch) -> Span | Unavailable:
    span = ctx.ranges.get(epoch.epoch, Unavailable(f"диапазон эпохи {epoch.epoch} нет"))
    if isinstance(span, Unavailable):
        return span
    if any(later.at < earlier.at for earlier, later in zip(span, span[1:])):
        return Unavailable("merged_at активаций противоречит first-parent")
    return span


def _until_t(span: Span, clock: Clock) -> Span:
    """Активации, способные предшествовать доставке: метка ≤ T (политика после T
    на завершённый акт не влияет и не загружается — спека §6, §8.1 №5)."""
    return tuple(a for a in span if a.at <= clock.t)


def _mode_events(
    ctx: DeliveryContext, activations: Span
) -> list[ModeEvent] | Unavailable:
    events: list[ModeEvent] = []
    previous = ""
    for a in activations:
        policy = ctx.policy_sets.get(a.merge_commit, Unavailable("набор не загружен"))
        if isinstance(policy, Unavailable):
            return policy
        mode = mode_for(policy, ctx.product) or ""
        if mode != previous:
            events.append(ModeEvent(a.key, a.at, mode))
        previous = mode
    return events


def _earlier(ctx: DeliveryContext, epoch: Epoch, clock: Clock) -> Span | Unavailable:
    """Активации всех предыдущих эпох (до их `last_confirmed`), по порядку."""
    collected: list[ActivationView] = []
    for prior in ctx.epochs[: epoch.epoch - 1]:
        span = _span(ctx, prior)
        if isinstance(span, Unavailable):
            return span
        collected.extend(_until_t(span, clock))
    return tuple(collected)


def _mode_history(
    ctx: DeliveryContext, epoch: Epoch, own: Span, clock: Clock
) -> list[ModeEvent] | Unavailable:
    """История режима, от которой зависит admission.

    Если на T в своей эпохе действует `review` — admission зависит только от
    маркера, и старая история не нужна (e7). Иначе (`merge`, нет записи)
    исход зависит от всей предыстории — legacy-граница и запрет обратного
    переключения переходят через границу эпох; недоступная предыстория →
    `Unavailable` (смена эпохи не разрешает начать миграцию заново).
    """
    own_events = _mode_events(ctx, own)
    if isinstance(own_events, Unavailable):
        return own_events
    current = [e for e in own_events if clock.precedes(e.key, e.at)]
    if current and current[-1].mode == "review":
        return own_events
    prior = _earlier(ctx, epoch, clock)
    if isinstance(prior, Unavailable):
        return prior
    return _mode_events(ctx, prior + own)


def _admission(ctx: DeliveryContext, clock: Clock) -> Span | Outcome:
    epoch = _epoch_at(ctx, clock)
    if epoch is None:
        if ctx.request_act is None:
            return OLD_READER
        return Rejected(MARKER_BEFORE_MODE, "заявка review/v1 до bootstrap источника")
    if isinstance(epoch, (Rejected, Unavailable)):
        return epoch
    span = _span(ctx, epoch)
    if isinstance(span, Unavailable):
        return span
    own = _until_t(span, clock)
    modes = _mode_history(ctx, epoch, own, clock)
    if isinstance(modes, Unavailable):
        return modes
    admission = admit(ctx.request_act, modes, clock)
    if isinstance(admission, Refused):
        return Rejected(ADMISSION, admission.reason)
    if isinstance(admission, Unavailable):
        return admission
    if admission.reader != "review":
        return OLD_READER
    return own


def _pinned_policy(ctx: DeliveryContext, span: Span, clock: Clock) -> PolicySet | Outcome:
    if ctx.request is None:
        return Rejected(NO_MANIFEST, "review/v1 без манифеста в одобренном коммите")
    pin = ctx.request.policy
    if (pin.repo, pin.branch) != (ctx.source.repo, ctx.source.branch):
        return Rejected(POLICY_SOURCE, f"пин указывает на {pin.repo}@{pin.branch}")
    seen = [a for a in span if clock.precedes(a.key, a.at)]
    if not seen:
        return Unavailable("в диапазоне эпохи нет активации до T")
    if pin.activation_commit != seen[-1].merge_commit:
        return Rejected(POLICY_CHANGED, f"на T действует {seen[-1].merge_commit}")
    policy = ctx.policy_sets.get(seen[-1].merge_commit, Unavailable("набор не загружен"))
    if isinstance(policy, Unavailable):
        return policy
    if dict(pin.files_sha256) != dict(policy.files_sha256):
        return Rejected(MIXED_POLICY, "files_sha256 пина ≠ набора активации")
    return policy


def _legacy_verdict(ctx: DeliveryContext) -> Outcome:
    """Вердикт старого кода потребителя, вычисленный им до перебора."""
    if ctx.legacy is None:
        return Unavailable("вердикт старой проверки не вычислен")
    return ctx.legacy


def _decide(ctx: DeliveryContext, clock: Clock) -> Outcome:
    span = _admission(ctx, clock)
    if span == OLD_READER:
        return _legacy_verdict(ctx)
    if not isinstance(span, tuple):
        return span
    policy = _pinned_policy(ctx, span, clock)
    if not isinstance(policy, PolicySet):
        return policy
    effective = act_facts.effective(ctx.facts, clock)
    if isinstance(effective, Unavailable):
        return effective
    assert ctx.request is not None
    return decide(
        request=ctx.request,
        head_sha=ctx.head_sha,
        delivery=ctx.delivery,
        effective=effective,
        head_self_hashes=ctx.head_self_hashes,
        policy=policy,
    )
```

`act_facts.Unavailable` (Task 11) — алиас steward-типа; адаптер обязан переводить его в
`act_time.Unavailable` (`return Unavailable(got.reason)`), чтобы ядро знало один тип. Поправьте
`read`, `effective`, `history` в Task 11 соответственно.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --group governance pytest tests/test_act_check.py tests/test_act_*.py -v`
Expected: PASS. Без группы: `uv run pytest tests/ -q` — `act_*` тесты со steward
`SKIPPED (steward)`, collection без ошибок.

- [ ] **Step 5: Commit**

```bash
git add governance/act_check.py tests/test_act_check.py tests/act_check_cases.py
git commit -m "feat(act): полная проверка доставки на срезе T — эпоха, admission, пин, D1–D3"
```

### Task 12a: Доверенный контекст — журнал, периметр, диапазоны

**Files:**
- Create: `governance/act_trust.py`
- Test: `tests/test_act_trust.py`

**Interfaces:**
- Consumes: Tasks 1, 2, 4, 9, 11, 12. Профиль агента — своя константа
  `AGENT_GH_CONFIG_DIR = Path.home() / ".config" / "review"` (переопределение
  `REVIEW_GH_CONFIG_DIR` из окружения — как `ops.py:1559`); импорт `governance.ops` запрещён
  цепочкой authority-root (Task 15).
- Produces: `Trust(source, epochs, anchor_at, unverified: tuple[str, ...])` — `unverified`:
  имена предпосылок, которые рантайм не смог проверить (пока только `"allow_rebase_merge"`);
  решение не блокируют, в вердикт `check_delivery` не входят (это свойство контекста, а не
  исхода) и **обязательно** записываются в evidence принятого акта: узел —
  `record_review_act(..., unverified=trust.unverified)` → поле `unverified_premises` в
  `run.json` (Task 13); бриф — `as_record` пишет `unverified_premises` рядом с
  `approval_act` (Task 17); `act_premerge` печатает их в stderr перед мержем (Task 15).
  Пустой кортеж пишется как `[]`, а не опускается: отсутствие поля в записи — дефект, а не
  «проверено»;
  `Readers` — инжектируемые чтения (protocol): `git(args) -> Fact[str]` в чекауте devtools,
  `gh_api(path, gh_config_dir) -> Fact[dict]`, `app_installations(key_path, app_id)
  -> Fact[list[dict]]`, `activation(merge_commit) -> Activation | Unavailable`,
  `history(anchor, until) -> tuple[Activation, ...] | Unavailable`,
  `policy_files(commit) -> Mapping[str, bytes] | Unavailable`;
  `credentials(env, config_home) -> tuple[Credential, ...]`;
  `load_trust(readers, *, state_home, credentials) -> Trust | Unavailable` — журнал, маркер,
  самоотчёты, ruleset (п. 1–4, 6 ниже; T не нужен);
  `load_anchor_times(readers, trust, t: datetime) -> Mapping[str, datetime | Unavailable]` —
  п. 5, лениво относительно T;
  `load_ranges(readers, trust, anchor_at, t) -> tuple[Mapping[int, Span | Unavailable],
  Mapping[str, PolicySet | Unavailable]]` — диапазоны эпох с якорем ≤ T (недоступный диапазон
  — значение `Unavailable`, решение спросит его, только если оно от него зависит) и наборы
  **только** на активациях с меткой ≤ T: набор после T не читается вовсе (§6: текущая
  активация на исторический вердикт не влияет).

Обязательные проверки `load_trust` перед **каждым** решением (спека §6.2 б, §6.3), по порядку; любое
недоступное обязательное чтение → `Unavailable` (маркер не пишется):
1. `git fetch origin master` в чекауте devtools; `source.env` и **все версии** `epochs.yaml` —
   из `origin/master` (`git log --first-parent --format=%H origin/master -- <path>`,
   `git show <c>:<path>`), не из рабочего дерева. Первая версия — одна открытая эпоха с якорем
   `BOOTSTRAP_ACTIVATION`; каждая следующая — `check_transition(prev, new)`; нарушение →
   `Unavailable` (журнал испорчен — решение не принимается).
2. `act_perimeter.blocked(source.repo, epochs, state_home)` → `Unavailable`.
3. Самоотчёт **каждого** credential процесса:
   - профиль `gh` агента (`AGENT_GH_CONFIG_DIR`), **фактический профиль мержа** (тот же выбор,
     что `merge-pr.sh:147`: `MERGE_GH_CONFIG_DIR`, иначе профиль по умолчанию обёртки) и токены
     окружения `GH_TOKEN`, `GITHUB_TOKEN` (каждый отдельно, совпадающие профили — один раз):
     `GET /repos/{source.repo}` → `permissions_refusal`; `act_premerge` получает профиль мержа
     обязательным аргументом `--merge-profile` от `merge-pr.sh` (тот же путь, которым обёртка
     затем мержит), без него — код 2;
   - каждый ключ App в `$XDG_CONFIG_HOME/darkfactory/apps/<app-id>.pem`: JWT (как в
     `conductor/` — `pyjwt[crypto]`) → `GET /app/installations` → установка на аккаунте-владельце
     источника с доступом к его репо — нарушение.
   Нарушение → `record_violation(source.repo, epochs[-1].epoch, detail)` + `Unavailable`.
4. Ruleset доверенной ветки: `GET /repos/{repo}/rules/branches/{branch}` (Metadata: read) —
   типы правил обязаны включать `deletion`, `non_fast_forward`, `pull_request`; отсутствие —
   нарушение (маркер).
   `allow_rebase_merge`: `GET /repos/{repo}` отдаёт настройки мержа только вызывающему с правом
   записи, которого у агента по построению нет (§6.2 б: «проверяется то, что видно без них»).
   Четыре состояния (раздел «Решения владельца», п. 1): `False` — проверено; `True` —
   нарушение (маркер, `Unavailable`); `None` или ключ отсутствует в корректном ответе (объект) —
   **не** «подтверждено»: строка `"allow_rebase_merge"` в `Trust.unverified`, решение не
   блокируется; ошибка запроса (`Fact` с ошибкой) или некорректный ответ (не объект; значение
   не `bool` и не `None`) — `Unavailable` без маркера. Обязательная проверка запрета —
   человеком при установке (§6.2 а, `df setup-repo`, спека B). **Отсутствие rebase-merge
   рантайм не доказывает:** п. 6 ловит многокоммитный rebase-merge (промежуточные коммиты
   first-parent без PR → `HistoryViolation`), однокоммитный по форме неотличим от squash и
   проходит.
5. Время якоря — `readers.activation(anchor).merged_at` **лениво**, от последней эпохи назад:
   якорь эпохи E нужен, только пока ни один более поздний якорь не предшествует T (e7: акт новой
   эпохи не требует объектов старой). Недоступное время нужного якоря → `Unavailable`;
   ненужного — записывается как `Unavailable` в `anchor_at` и не мешает (Task 12, `_epoch_at`).
6. История формы (§6.1 п. 4): `readers.history(anchor, until)` → `HistoryViolation` →
   `record_violation(...)` + `Unavailable` (§6.3: «признак в форме истории» — тоже маркер);
   `Unavailable` → `Unavailable` без маркера.

Порядок вызовов: историческая доставка — `load_trust` → `load_anchor_times(t=merged_at)` →
`load_ranges(t=merged_at)`; открытый PR (`act_premerge`, живой пин) — то же с `t = now`
(текущая активация = последняя активация открытой эпохи).

`credentials()` перечисляет то, что процесс **держит**, а не то, что объявлено в настройке:
агент не может «спрятать» credential, удалив его из списка.

- [ ] **Step 1: Write the failing tests** (`tests/test_act_trust.py`, стабы `Readers`):

```python
def test_journal_read_from_forge_not_worktree(tmp_path) -> None:
    """Подмена epochs.yaml в рабочем дереве ничего не меняет."""

def test_journal_chain_with_rewritten_closed_epoch_unavailable() -> None:
    """Приёмка №45: любая мутация, кроме атомарного закрытия, в истории файла."""

def test_marker_blocks_decision(tmp_path) -> None:
    """Приёмка №36."""

def test_token_with_push_records_marker_and_blocks(tmp_path) -> None:
    """Приёмка №29, №34 (обнаруживший потребитель)."""

def test_merge_profile_with_push_blocks(tmp_path) -> None:
    """P5-2: review-профиль read-only, merge-профиль с push на источнике → маркер,
    Unavailable; мерж не вызывается."""

def test_app_installed_on_source_is_violation(tmp_path) -> None:
    """Самоотчёт App: установка на источнике — нарушение."""

def test_missing_bypass_field_not_treated_as_empty(tmp_path) -> None:
    """Приёмка №33: рантайм не делает вывода «bypass нет» — поле не читается вовсе."""

def test_ruleset_without_pull_request_rule_is_violation(tmp_path) -> None:
    """Периметр: ветка источника без требования PR."""

def test_rebase_setting_false_is_verified(tmp_path) -> None:
    """allow_rebase_merge=False → Trust, unverified пуст."""

def test_rebase_setting_true_records_marker(tmp_path) -> None:
    """allow_rebase_merge=True → маркер + Unavailable."""

@pytest.mark.parametrize("repo", [{"allow_rebase_merge": None}, {}])
def test_rebase_setting_hidden_is_unverified_premise(tmp_path, repo) -> None:
    """null и отсутствующий ключ в корректном ответе → Trust, unverified содержит
    "allow_rebase_merge"; маркера нет."""

@pytest.mark.parametrize("fact", ["error", [], {"allow_rebase_merge": "false"}])
def test_rebase_setting_unreadable_is_unavailable(tmp_path, fact) -> None:
    """Ошибка запроса, не-объект, значение не bool/None → Unavailable без маркера;
    в null не превращается."""

def test_single_commit_rebase_merge_not_detected() -> None:
    """Приёмка №28, предел: история из стаба с однокоммитным rebase-merge (вектор M0)
    принимается, как squash; тест фиксирует, что рантайм отсутствие rebase не доказывает."""

def test_multi_commit_rebase_merge_records_marker(tmp_path) -> None:
    """Приёмка №28: HistoryViolation (промежуточный коммит без PR) → маркер, Unavailable."""

def test_permissions_unreadable_is_unavailable_without_marker(tmp_path) -> None:
    """Недоступное ≠ нарушение: маркер не пишется."""

def test_anchor_time_from_activation_not_commit_date() -> None:
    """Приёмка №19: время якоря — merged_at активации."""

def test_old_anchor_not_read_when_newer_precedes_t() -> None:
    """e7: старый якорь не читается, если более поздний уже раньше T."""

def test_history_violation_records_marker(tmp_path) -> None:
    """§6.3: нарушение формы истории — маркер, как и самоотчёт."""

def test_history_read_failure_no_marker(tmp_path) -> None:
    """Сбой чтения истории — Unavailable без маркера."""

def test_policy_sets_after_t_not_loaded() -> None:
    """Набор активации после T не запрашивается у читателя."""

def test_issue_signal_is_not_part_of_protocol(tmp_path) -> None:
    """Приёмка №39: снятый сигнал-issue не снимает маркер."""
```

  Каждый тест — полный код со стабами (паттерн `_StubOps` из
  `tests/test_governance_approval_facts.py`); ожидание — тип исхода и наличие/отсутствие файла
  маркера.
- [ ] **Step 2: Run** `uv run --group governance pytest tests/test_act_trust.py -v` → FAIL
- [ ] **Step 3: Implement** `act_trust.py` по порядку 1–5; каждая проверка — отдельная функция,
  возвращающая `None | Unavailable`; `load_trust` вызывает их цепочкой с ранним выходом.
- [ ] **Step 4: Run** → PASS; `pyrefly check`.
- [ ] **Step 5: Commit**

```bash
git add governance/act_trust.py tests/test_act_trust.py
git commit -m "feat(act): доверенный контекст — журнал с форджа, периметр, диапазоны эпох (§6.2–6.4)"
```

### Task 13: Путь узла — общий вход, леджер, finalize, аудит

**Files:**
- Create: `governance/act_node.py` (вся новая логика узла; `approve_node` только ветвится)
- Modify: `governance/approve_node.py` (диспетчер шага ~1640, `_reconcile_candidate` ~1787,
  `_reconcile_finalize` ~1994, `_finalize` ~2064, создание candidate, `HUMAN_MERGE_LABEL` ~85),
  `governance/runner.py` (`reopen` ~1172)
- Modify: `governance/approval_ledger.py` (`start_request`, новое `record_review_act`,
  `record_audit`)
- Test: `tests/test_act_node.py`; `tests/test_governance_approve_node.py` (не ломается)

**Interfaces:**
- Consumes: Tasks 5, 5a, 8, 10, 11, 12, 12a.
- Produces:
  `act_node.route(state, ops, dag, op, key, step: al.Step) -> ApprovalOutcome | None` —
  `None` значит «старый код продолжает», и возвращается **только** после `resolve` с вердиктом
  `LegacyAccepted` или для открытого/закрытого-без-мержа PR без манифеста;
  `act_node.load_context(ops, readers, trust, op, key, facts) -> DeliveryContext |
  Unavailable`;
  `act_node.create_request(...)` — манифест в коммите candidate;
  `act_node.reconcile_finalize(...)`, `act_node.finalize(...)`;
  `act_node.reopen(...)` — запись остановки в режиме `review/v1`;
  `act_node.audit_late_dismissals(state, ops, key) -> None`;
  `approval_ledger.record_review_act(state, key, accepted: Accepted, delivery: DeliveryView,
  activation_commit: str, unverified: tuple[str, ...]) -> None` (`unverified` —
  `Trust.unverified`, Task 12a; без значения по умолчанию); `approval_ledger.record_audit(state, key, event: dict)`;
  `HUMAN_APPROVAL_LABEL = "human-approval-required"`; `WAITING_HUMAN_APPROVAL`,
  `WAITING_DELIVERY`.

**Общий вход (спека §10 «до выбора читателя, для обоих путей»; §2 «режим из манифеста»).**
Маршрутизация выполняется в **диспетчере шага** (`approve_node.py:1640–1649`, функция, вызывающая
`al.next_step(op)`), до выбора любого шага — а не только в `_reconcile_candidate`. Иначе resume
на `FINALIZE`/`AWAIT_FINALIZE_MERGE` выбрал бы читателя по `op["act"]`, минуя проверку:

```python
    step = al.next_step(op)
    if step is not al.Step.CREATE_CANDIDATE:
        routed = act_node.route(state, ops, dag, op, key, step)
        if routed is not None:
            return routed
```

`route` для шагов после мержа candidate (`FINALIZE`, `AWAIT_FINALIZE_MERGE`) повторяет п. 1–3
ниже по **голове смёрженного candidate** (`op["head_sha"]`, сверенной с `headRefOid` форджи) и
только затем передаёт управление `act_node.finalize`/`reconcile_finalize` (манифест есть) или
старому коду (манифеста нет и admission дал `OLD_READER`; результат admission для доставки
записан в `op["admission"]` при первом решении и перепроверяется по форджу при каждом входе).

`route` по порядку:
1. Факты PR (`_pr_facts`), голова `headRefOid`.
2. Манифест читается **из головы PR**, не из `op`: `ops.repo_file_fact(product, head,
   manifest_path(ws, key))` → `FOUND` → `parse_manifest` (`RequestError` →
   `invalidate_request`); `ABSENT` → заявка без маркера; `UNAVAILABLE` → `_unresolved`.
3. Зеркала и привязка: `op.get("act")` обязан совпадать с наличием манифеста; для `review/v1`
   — `mirror_mismatch(req, key, op, body)`; затем **привязка к доверенному контексту прогона**:
   `req.product` = slug целевого репо прогона (`state.repo_slug`), `req.ws_id` = `state.ws_id`,
   `req.request_id` = `key`, для каждого узла `req.nodes[n].path` = путь узла `n` в DAG прогона
   (`_rel(state, _filename(dag, n))` — `approve_node.py:113/117`: `_filename` даёт только имя
   файла, полный путь строит `_rel` от `state.bundle_dir`), `req.upstream_pins[n]` =
   `direct_blobs` узла `n` на `source_sha` заявки (DAG-upstream и source-входы discovery). Любое расхождение → `invalidate_request`
   (приёмка №13). Удаление `act` из `run.json` при манифесте в голове — тоже расхождение.
4. PR OPEN, без манифеста → `None` (старый код ждёт; допуск всё равно решит admission на
   доставке). PR OPEN, `review/v1` → `live_policy_refusal(pin, текущая активация открытой эпохи)`
   → `invalidate_request("policy_changed: …")`; затем `open_review` + D1 на текущем head:
   нет акта → `WAITING_HUMAN_APPROVAL`; есть → `ops.merge(...)` (агентский мерж через
   `merge-pr.sh`, Task 15) и `WAITING_DELIVERY`.
5. PR CLOSED без мержа → `None` (старый код абандонирует; одинаково для обоих режимов).
6. PR MERGED → `load_trust` → `load_context` → `check_delivery`:
   - `LegacyAccepted` → `None` (старый код записывает подпись тем же путём, что вычислил
     вердикт); `ctx.legacy` = `_legacy_outcome(state, ops, dag, op, key, facts)` — новая чистая
     функция, выделенная из `_reconcile_candidate` (`approve_node.py:1787–1928`): те же
     проверки в том же порядке (опубликованный состав, `merge_event`, пин политики,
     `authorized_signature`), но вместо `invalidate_request`/`abandon_request`/`raise` —
     возврат `Rejected(code, detail)` / `Unavailable`; `_reconcile_candidate` переписывается
     поверх неё (поведение старого пути не меняется — существующие тесты
     `test_governance_approve_node.py` зелёные без правок);
   - `Accepted` → `record_review_act` → `act_node.finalize`;
   - `Rejected` → `invalidate_request(key, f"{code}: {detail}")`;
   - `Unavailable`, `Ambiguous` → `_unresolved` (заявка жива; для `Ambiguous` текст называет
     штатный выход — `--repropose`).

**Совместимость леджера (`next_step`, `approval_ledger.py:108`).** `record_review_act` пишет
**и** верхнеуровневые поля, по которым `next_step` выбирает шаг, **и** поля акта:

```python
def record_review_act(
    state: RunState,
    key: str,
    accepted: Accepted,
    delivery: DeliveryView,
    activation_commit: str,
    unverified: tuple[str, ...],
) -> None:
    """Акт review/v1 и доставка — одним write'ом (спека §7, `run.json`).

    `unverified` — непроверенные рантаймом предпосылки (`Trust.unverified`),
    пишутся всегда, пустые — как `[]`.

    Верхнеуровневые `merged_by`/`merged_at`/`merge_commit`/`authorization`
    обязательны: по ним `next_step` переводит заявку в FINALIZE.
    """
    _update(
        state,
        key,
        merged_by=accepted.merged_by,
        merged_at=delivery.merged_at,
        merge_commit=accepted.merge_commit,
        authorization={
            "login": accepted.login,
            "policy": activation_commit,
            "source": f"review/v1:{accepted.review_id}",
        },
        review_id=accepted.review_id,
        review_commit=delivery.head_at_merge,
        approved_by=accepted.login,
        delivery={
            "merge_commit": accepted.merge_commit,
            "merged_at": delivery.merged_at,
            "merged_by": accepted.merged_by,
        },
        unverified_premises=list(unverified),
    )
```

**Finalize `review/v1`.** Управление в `act_node.finalize` / `act_node.reconcile_finalize`
приходит только из `route` (выше). Finalize пишет конверты `envelope_path(ws, node)` (Task 8) и
**не** трогает frontmatter узлов. Агентский мерж finalize-PR идёт существующей дорогой ADR-ECO-011
D5 (scope-аттестация + `merge-pr.sh`), но **проверка формы** коммита конверта для `review/v1` —
своя: `act_node.envelope_commit_defect(state, ops, op, head) -> str | None` вместо
`_envelope_form_defect` (`approve_node.py:~2575`, требует файлы узлов). Правило: коммит трогает
ровно `{envelope_path(ws, n) for n in req.nodes}`; содержимое каждого = `render_envelope` от
ожидаемого конверта (`_expected` Task 8 по доставке candidate и `review_id` из решения); любое
иное — строка причины, мерж не выполняется. `_verify_nodes_in_base` для `review/v1` заменяется
`act_status.node_status` (Task 14) по базе после мержа finalize. Повтор после падения берёт уже записанный конверт, если он равен
ожидаемому (`test_finalize_resume_reuses_envelope`, Review Focus 5).

**Создание заявки.** `ACT_MODE[P] = review` по текущей активации **и** `settings.review_v1` →
`review/v1`: поколение каждого узла — `next_generation(history, node)` (история — Task 14
`load_history`); `upstream_pins` — **полный набор прямых входов** узла по существующему контракту
`bundle_inputs.direct_blobs` (`bundle_inputs.py:82`: DAG-upstream **и** source-входы — у charter
`discovery-brief`, у engineer-charter ещё `discovery-customer`), прочитанных на `source_sha`
волны, как `approve_node.py:267`; `{}` — только когда `direct_blobs` положительно установил
отсутствие всех входов; пустой набор при непустом `direct_blobs` — отказ создания; манифест — в коммит candidate; `op` получает `ledger_view(req)`;
тело PR — строки `act: review/v1`, `policy-activation: <sha>`; метка `human-approval-required`.
`ACT_MODE[P] = review` без `review_v1` → отказ создания с причиной (не тихий `merge`).
`settings.draining` при режиме `merge` → отказ создания заявки без маркера.

**Переоткрытие в `review/v1`.** Вход — `governance/runner.py:1172` `reopen(...)` (CLI
`--reopen <node>`). При действующем одобрении узла `review/v1` (Task 14 `node_status`) `reopen`
первым шагом вызывает `act_node.reopen(state, ops, node)`: отдельный PR с единственным файлом
`stop_path(ws, node, generation)` (Task 8) на ветке `stop/<ws-id>-<node>-g<generation>` (форма не
пересекается с candidate/finalize-глобами `patterns.env`), агентский мерж через `merge-pr.sh`
(запись только отзывает). Остальной `reopen` (новая волна `…-r<n>`) продолжается только после
`state=MERGED` stop-PR; до доставки остановки узел остаётся одобренным, и это видно в `status`.

**Аудит позднего отзыва (§8, приёмка №10).** `audit_late_dismissals` вызывается при `status`
и `resume` для заявок `review/v1` со статусом `completed`: отзывы с `created_at > merged_at` →
`record_audit(key, {"event": "review_dismissed_after_delivery", "review_id": …, "at": …})`
(идемпотентно по `review_id`); решение не пересматривается; `status` показывает событие.

- [ ] **Step 1: Write the failing tests** (`tests/test_act_node.py`, стабы ops как в
  `tests/test_governance_approve_node.py`):
  - создание `review/v1`: манифест в коммите, `ledger_view` в `op`, метка, тело, поколение 1;
  - отказ создания: `draining` без маркера; `ACT_MODE=review` без `review_v1`; узел с входами
    и пустыми пинами;
  - customer-charter и engineer-charter через настоящие `direct_blobs`: пины манифеста содержат
    `discovery-brief` (и `discovery-customer`), конверт после доставки принимается; смена брифа в
    базе → charter `not_approved` по пинам;
  - общий вход: legacy-candidate без манифеста, смёрженный после активации `review` →
    `invalidated` с кодом `admission` через настоящий диспетчер шага (приёмка №24);
  - resume на `FINALIZE` с удалённым из `run.json` `act` при манифесте в голове candidate →
    `invalidated`, старый finalize не вызывается (P1-2);
  - привязка: манифест с чужим `product`, `ws_id`, путём узла или upstream-пином → `invalidated`;
    положительный двойник — настоящие имена DAG (`15-requirements.md`) и непустой `bundle_dir`
    → заявка принята;
  - форма коммита конверта: лишний путь, путь узла, изменённое содержимое → причина, мерж не
    выполняется; верный коммит → агентский мерж finalize после scope-аттестации;
  - манифест в голове, `act` удалён из `run.json` → `invalidated` (приёмка №13);
  - открытый PR без approve → `WAITING_HUMAN_APPROVAL`; с approve → вызван `ops.merge`,
    `WAITING_DELIVERY`; живая заявка со старым пином → `invalidated: policy_changed`
    (приёмка №8, №9);
  - смёржен, `Accepted` → `next_step(op) is Step.FINALIZE` после `record_review_act`;
  - `unverified_premises` в `run.json`: `allow_rebase_merge` = `False` → `[]`; `None` и
    отсутствующий ключ → `["allow_rebase_merge"]` (решение принято, предпосылка названа);
  - падения: после `record_review_act`; после создания finalize-PR; после его мержа до
    `complete_request` — каждый повтор приходит к тому же итогу без второго конверта;
  - `Unavailable` от `check_delivery` → заявка жива, повтор с доступными фактами завершает её;
  - `Ambiguous` → заявка жива, текст называет `--repropose`;
  - `reopen` через настоящий `runner.reopen` в `review/v1` → stop-PR с одним файлом, агентский
    мерж; после доставки узел и его downstream «не одобрены» (`node_status`, `node_debt`);
    волна `-r<n>` стартует только после `MERGED`;
  - аудит: отзыв после доставки → одна запись `review_dismissed_after_delivery`, решение не
    меняется, повторный `status` не дублирует (приёмка №10).
- [ ] **Step 2: Run** `uv run --group governance pytest tests/test_act_node.py -v` → FAIL
- [ ] **Step 3: Implement** `act_node.py`, `record_review_act`, `record_audit`, ветвления.
- [ ] **Step 4: Run** `uv run --group governance pytest tests/test_act_node.py tests/test_governance_approve_node.py tests/test_governance_approval_ledger.py -v` → PASS
- [ ] **Step 5: Commit**

```bash
git add governance/act_node.py governance/approve_node.py governance/approval_ledger.py \
  tests/test_act_node.py tests/test_governance_approve_node.py
git commit -m "feat(act): путь узла — общий вход admission, леджер, finalize в конверт, аудит"
```

### Task 13a: `--repropose` — переиздание заявки узла и брифа

**Files:**
- Modify: `governance/approve_node.py` (CLI `--repropose <request-key>`), `governance/runner.py`
  (передача флага), `governance/approval_ledger.py` (поле `predecessor` в `start_request`),
  `governance/brief_tools.py` (подкоманда `repropose`)
- Test: `tests/test_act_repropose.py`

Спека §6 и Task 13 называют `--repropose` штатным выходом (`policy_changed`, `Ambiguous` —
неизменен по фактам доставки, `act_migrated`); сегодня такой команды нет (`approve_node.py:316`
всегда возвращается в `_advance`, `runner.reopen:1202` отказывает при живых заявках,
`brief_tools` — только `propose/approve`).

Правила:
- **Узел — два случая, без переписывания терминальных записей** (`approval_ledger.py:434`
  запрещает менять терминальную запись; `invalidated` уже терминален, `:65`):
  - *Предшественник уже терминален* (`invalidated:*`, в т.ч. `policy_changed`, `act_migrated`):
    его запись не трогается. Создаётся **преемник**: новая заявка (`next_attempt`, новое имя
    ветки через `approval_branches`, текущий пин) с полем `predecessor: <key>`.
  - *Предшественник жив* (смёрженный candidate с постоянным `Ambiguous` или «неизменным»
    `Unavailable`; открытый candidate со старым пином): сначала разрешённый переход
    live → `invalidate_request(key, "reproposed: <причина>")`, открытый PR закрывается
    (`confirm_closed`), затем преемник как выше.
  - Доказательства предшественника (`review_id`, `delivery`, `audit`) сохраняются в его записи.
  - Восстановление после прерывания: повторный `--repropose <key>` находит существующего
    преемника по `predecessor == key` и продолжает его, второго не создаёт.
- **История поколений не фильтруется по действительности акта** (спека §9.1 п. 0a: действующее
  одобрение — последний доставленный candidate с манифестом узла). Доставленный candidate
  преемника или предшественника без действительного акта остаётся в истории: он — последнее
  событие узла, его `check_delivery` не `Accepted` → узел «не одобрен»; откат к более раннему
  одобренному поколению запрещён. `next_generation` считается по полной истории.
- **Бриф.** `brief_tools repropose --run <id>`: старый brief-PR закрывается (открытый) или
  остаётся смёрженным без штампа; создаётся новая ветка `brief/<ws>-r<n>` и заявка v2 с текущим
  пином и `predecessor`; прежняя заявка в `run_dir` не переписывается.
- Никакой путь не превращает прежнюю заявку в одобренную.

- [ ] **Step 1: Write failing tests** — через настоящий CLI: `invalidate_request(policy_changed)`
  → `--repropose` → преемник с `predecessor`, запись предшественника не изменена → новый
  approve → доставка → узлы одобрены; постоянный `Ambiguous` у смёрженного candidate →
  `--repropose` → предшественник `invalidated: reproposed`, преемник; прерывание после
  инвалидации до создания преемника → повтор создаёт ровно одного преемника; R1/g1 одобрен,
  R2/g2 доставлен без действительного акта с теми же байтами → узел «не одобрен», конверт R1
  отвергнут, `next_generation == 3`; бриф — преемник с `predecessor`; `--repropose` для
  `completed` → отказ.
- [ ] **Step 2: Run** `uv run --group governance pytest tests/test_act_repropose.py -v` → FAIL
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS; `tests/test_governance_approve_node.py` и
  `tests/test_brief_tools.py` зелёные.
- [ ] **Step 5: Commit**

```bash
git add governance/approve_node.py governance/runner.py governance/approval_ledger.py \
  governance/brief_tools.py tests/test_act_repropose.py
git commit -m "feat(act): --repropose — переиздание заявки узла и брифа с сохранением доказательств"
```

### Task 14: Читатели статуса узла, история, хеш DAG

**Files:**
- Create: `governance/act_status.py`
- Modify: `governance/node_approval.py` (`node_debt` ~198), `governance/task_bridge.py`
  (каноническая проекция ~1167), вызов гейтов steward с провайдером статуса
- Test: `tests/test_act_status.py`

**Interfaces:**
- Produces: `act_status.load_history(ops, product, base_ref, ws_id) -> History | Unavailable`
  — доставленные по first-parent базы заявки (манифесты) и записи остановки;
  `act_status.node_status(ops, readers, trust, product, base_ref, ws_id, node)
  -> str` (`approved` | `not_approved:<reason>` | `unavailable:<reason>`) — история →
  `current_event` → для заявки — `check_delivery` её доставки (свидетельство `review_id`) →
  `envelope_refusal`;
  провайдер `node_status` для гейтов steward (интерфейс M0).

События истории (§9.1 п. 0a): **доставленная заявка** (манифест в candidate, смёрженном в базу),
**остановка** (запись `stops/…`, Task 8) и **supersede** — более поздняя доставленная заявка,
включающая узел (она и есть следующее событие; отдельной записи не нужно). **Каскад** — не
событие истории: одобрение зависимого узла перестаёт действовать, когда его upstream-пины в
конверте расходятся с текущими хешами прямых входов в базе (`envelope_refusal` сравнивает
`upstream_pins` с переданными — их вычисляет `act_status` через `bundle_inputs.direct_blobs` по
базе, включая source-входы discovery, как `node_debt` сегодня, `node_approval.py:207`). Переодобрение upstream с новыми байтами поэтому делает все
зависимые узлы `not_approved` без отдельной записи. **Остановка** upstream при неизменных байтах
пины не меняет, поэтому `node_status(B)` дополнительно требует, чтобы **каждый** upstream B по
DAG был `approved` (рекурсивно, с мемоизацией по узлу в одном вызове; цикл DAG невозможен —
`bundle_dag` его отвергает). Остановленный A делает B `not_approved:upstream_stopped:A`.

Правило (§9.1 п. 3–4): режим узла — по заявке действующего события истории: манифест →
только конверт, fallback на frontmatter запрещён; нет манифеста и доставлено до активации
`ACT_MODE=review` (admission `OLD_READER`) → старый путь (frontmatter). В каноническую проекцию
`task_bridge` для `review/v1` входит производный статус из `node_status`. Гейты steward вызываются
с провайдером; `unavailable:*` — не «одобрен».

- [ ] **Step 1: Write the failing tests** — смешанный DAG (A legacy, B review/v1); B без конверта
  → долг при `status: approved` во frontmatter; подмена конверта → долг; остановка → долг;
  supersede: вторая заявка над B с тем же содержимым → действует её конверт, первый — отказ
  (поколение); остановка A с неизменными байтами → B `not_approved:upstream_stopped:A`;
  каскад: upstream A переодобрен с новыми байтами → B `not_approved` по пинам,
  сквозной тест через `node_debt`, `task_bridge` и гейт steward;
  смена статуса B меняет канонический хеш DAG; гейт steward с провайдером видит долг B, без
  провайдера — тоже долг (fail-closed); `unavailable` истории → не одобрен.
- [ ] **Step 2: Run** `uv run --group governance pytest tests/test_act_status.py -v` → FAIL
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run --group governance pytest tests/test_act_status.py tests/test_governance_node_approval.py -v` → PASS; полный набор.
- [ ] **Step 5: Commit**

```bash
git add governance/act_status.py governance/node_approval.py governance/task_bridge.py \
  tests/test_act_status.py
git commit -m "feat(act): статус узла review/v1 из истории и конверта; гейты и хеш DAG (§9.1)"
```

### Task 15: Агентский мерж candidate `review/v1` — предпроверка и граница секунды

**Files:**
- Create: `governance/act_premerge.py` (authority-root по префиксу `governance/act_`)
- Modify: `merge-pr.sh` (authority-root; блок `case "$head_ref"` ~381)
- Test: `tests/test_act_premerge.py`, `tests/test_merge_pr.py` (существующий — дополнить)

**Interfaces:**
- Produces: запуск **файлом** (как `brief_merge_check`, `human-merge.sh:174`), но с группой
  `governance` — в ней steward (`pyproject.toml:42`; `default-groups` его не содержат):
  `uv run --frozen --exact --group governance --no-config --no-env-file python -I
  "$script_dir/governance/act_premerge.py" --repo <slug> --pr <n> --head <sha>` →
  0 — мерж разрешён (ожидание границы уже выполнено), 3 — отказ, 2 — не установлено.
  `pyproject.toml` имеет `package = false`, поэтому `-m governance…` под `-I` не найдёт пакет:
  файл начинается тем же явным bootstrap пакета по пути, что `brief_merge_check.py:25–39`.
  Цепочка импортов `act_premerge` — только модули под authority-root (`governance/act_*`,
  `ssot_env`, `frontmatter`, `facts`) и steward; `approval_facts`, `ops`, `stale_adapter`
  в неё не входят (поэтому `act_check` держит литерал `POLICY_CHANGED`, `act_trust` — свою
  константу профиля агента). Цепочку сверяет `tests/harness_trust.py`.

Сейчас `merge-pr.sh` безусловно отказывает (`die 3`) candidate-ветке. Новое правило блока:
candidate-ветка **с** меткой `human-approval-required` → запуск предпроверки той же формой, что
`human-merge.sh` запускает `brief_merge_check` (`uv run --frozen --exact --no-config
--no-env-file python -I`, собственное окружение, `tests/harness_trust.py` сверяет цепочку
импортов); 0 → мерж с пином `sha=<head>`; иначе — `die` с её кодом. Candidate **без** метки —
`die 3`, как сейчас (legacy-акт по-прежнему мерж человеком).

`act_premerge` по порядку: `load_trust` (непустой `Trust.unverified` — строка `unverified premise: <имя>` в stderr, мерж не блокирует); манифест в `--head` (нет → 3); `ACT_MODE[P] = review`
по текущей активации открытой эпохи (иначе 3); `live_policy_refusal` (3); `open_review` + D1 на
`--head` (нет акта → 3); голова PR = `--head` (иначе 3); **граница**: последнее событие,
способное повлиять на решение, — максимум из `submitted_at` ревью, `created_at` отзывов,
`merged_at` активаций открытой эпохи; спать до `max + 2 с` (ожидание > 10 с → 2, «часы
расходятся»); затем **повторить** чтения ревью, головы и текущей активации и сверить, что
ничего не изменилось (иначе 2). Окончательное решение принимает `check_delivery` после
доставки (Task 13) — предпроверка не подпись.

- [ ] **Step 1: Write failing tests** — `test_act_premerge.py`: approve 1 с назад → сон ≥ 1 с
  (подменённый `sleep`); активация политики 1 с назад → сон; пин устарел → 3; нет approve → 3;
  повторное чтение увидело отзыв → 2. `test_merge_pr.py`: candidate с меткой и предпроверкой 0
  → мерж с `sha=`; с меткой и предпроверкой 3 → отказ 3; без метки → отказ 3 как прежде;
  `human-merge-required` → отказ как прежде.
- [ ] **Step 2: Run** `uv run --group governance pytest tests/test_act_premerge.py tests/test_merge_pr.py -v` → FAIL
- [ ] **Step 3: Implement**; `shellcheck merge-pr.sh` чисто.
- [ ] **Step 4: Run** → PASS; тест-инвариант цепочки импортов (`tests/harness_trust.py`
  используется тестами harness — найти их `grep -l harness_trust tests/`) → PASS; плюс
  `test_premerge_runs_as_isolated_file`: настоящий subprocess `uv run --frozen … python -I
  governance/act_premerge.py --help` в **отдельном** окружении
  (`UV_PROJECT_ENVIRONMENT=<tmp>`, `env -i PATH=$PATH HOME=$HOME`) → 0 и `import steward`
  внутри прошёл; распознаватель формы запуска в `tests/harness_trust.py:48` расширяется на
  форму с `--group governance` (новая форма — отдельное правило, старая не ослабляется).
- [ ] **Step 5: Commit**

```bash
git add governance/act_premerge.py merge-pr.sh tests/test_act_premerge.py tests/test_merge_pr.py
git commit -m "feat(merge-pr): агентский мерж candidate review/v1 после предпроверки и границы секунды"
```

### Task 15a: `merge-pr.sh` в контексте GitHub App

**Files:**
- Modify: `merge-pr.sh` (preflight личности ~263–274), `governance/ops.py` (`RealOps.merge`
  передаёт App-контекст окружением)
- Create: `governance/act_app_token.py` (JWT → токен установки; authority-root по префиксу)
- Test: `tests/test_merge_pr.py`, `tests/test_act_app_token.py`

Положительный сценарий №1 и M3 требуют, чтобы candidate и бриф доставлял App. Действующий
preflight `merge-pr.sh:269` вызывает `gh api user`, а токен установки App получает на него 403
(spike; спека §9.4). Спека B ставит механику ролей, но её реализация идёт только после приёмки A,
поэтому минимальный App-режим обёртки нужен здесь.

Правила:
- Режим App включают переменные `MERGE_APP_ID` и `MERGE_APP_KEY` (путь к ключу в
  `$XDG_CONFIG_HOME/darkfactory/apps/`). Без них — прежний режим (`gh api user` = `AGENT_LOGIN`).
- Preflight в режиме App: `act_app_token.py` (тот же запуск файлом с `--group governance`, что
  Task 15; JWT — `pyjwt[crypto]`, уже в группе `conductor`; в `governance` — добавить) получает
  `GET /app` (slug) и токен установки на целевой репо; ожидаемая личность — `<slug>[bot]` из
  `AUTHORIZED_MERGER_ACCOUNTS` текущей активации (сверяет `act_premerge`). `gh` дальше работает с
  `GH_TOKEN=<installation token>`.
- Самоотчёт прав (Task 12a) для App — проверка установок, а не `GET /repos` от токена.
- Режим App допустим только для PR с меткой `human-approval-required` (candidate/brief
  `review/v1`); прочие PR — прежний профиль.

- [ ] **Step 1: Write failing tests** — App-режим: `gh api user` не вызывается; slug вне
  `AUTHORIZED_MERGER_ACCOUNTS` → код 2; без метки → отказ App-режима; прежний режим не изменился.
- [ ] **Step 2: Run** `uv run --group governance pytest tests/test_merge_pr.py tests/test_act_app_token.py -v` → FAIL
- [ ] **Step 3: Implement**; `shellcheck merge-pr.sh`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit**

```bash
git add merge-pr.sh governance/ops.py governance/act_app_token.py pyproject.toml uv.lock \
  tests/test_merge_pr.py tests/test_act_app_token.py
git commit -m "feat(merge-pr): App-режим — личность через JWT и токен установки, не gh api user"
```

### Task 16: Барьер миграции и отчёт затрагиваемых заявок

**Files:**
- Create: `governance/act_barrier.py` (+ `make act-barrier` в `Makefile`)
- Test: `tests/test_act_barrier.py`

**Interfaces:**
- Produces: `BarrierReport(items: tuple[str, ...], complete: bool, gaps: tuple[str, ...])`;
  `live_unmarked(product, runs_root, ops, *, confirmed: frozenset[int]) -> BarrierReport`;
  `live_review_requests(source, products, runs_root, ops) -> BarrierReport`;
  CLI `python -m governance.act_barrier --product owner/repo [--confirm-resolved <pr>]... [--json]`;
  выходы: 0 — пусто и полно; 1 — есть живые (печатает); 3 — неполно (печатает пробелы). Эту
  команду вызывает `df setup-repo apply` (спека B) в человеческом контексте.

Полнота выводится **из форджи**, а не из локальных леджеров (другая установка не видна
локально):
1. Все PR продукта (`state=all`, полная пагинация; неполная — `gaps`) с формой ветки candidate,
   finalize (`contracts/approval-branches/v1/patterns.env`) или `brief/*`.
2. Живые без маркера (§10 п. 2): OPEN candidate/finalize/brief без манифеста `review/v1` в
   голове; MERGED legacy-candidate считается разобранным **только** если в базе для каждого его
   узла действует подпись, доказанная существующей legacy-проверкой (`node_debt` без долга для
   узла и его `approved_by`/`approved_at` соответствуют акту именно этого candidate — тот же
   предикат, что `_verify_nodes_in_base`), либо узел позже покрыт другой доставленной заявкой;
   имя ветки finalize разбором не считается (merged-but-unrecorded / pending-finalize).
   Явная инвалидация (§10 п. 2: `invalidated: act_migrated`) признаётся разбором по форджу:
   `act_barrier --invalidate <pr>` (человеческий контекст, профиль человека из
   `human_approvers`) пишет в PR комментарий-маркер `<!-- act-migrated: <request-key> -->`;
   барьер принимает маркер только от логина из `human_approvers` текущей активации, без правок
   (`last_edited_at is None`). Локальный леджер той же заявки тем же вызовом получает
   `invalidated: act_migrated`; заявку другой установки её владелец инвалидирует у себя (Task 16
   п. 5 — аттестация); MERGED brief-PR с заявкой v1 — штамп одобрения
   живёт только в `run_dir` установки, поэтому без локального доказательства штампа он
   попадает в `items` с пометкой «требует подтверждения человека» и снимается только
   `--confirm-resolved <pr>` (флаг читает только CLI человеческого контекста; агентские вызовы
   его не передают).
3. Локальные леджеры (`rs.load` по прогонам продукта) — дополнительно; нечитаемый леджер —
   `gaps`.
4. Предусловие: `act_settings.load_settings().draining` — `True` (создание заявок без маркера
   остановлено в devtools; discovery читает тот же `act.toml`, межрепная заявка M2). Не включено →
   выход 3 «draining не включён» — пустой список без остановки создания ничего не доказывает.
5. Другие установки. Остановку создания на чужой машине форджа не видно. Поэтому полнота
   требует аттестации человека в человеческом контексте: `--attest-drained <installation>…` —
   перечень всех установок, создающих заявки для P, каждая подтверждена владельцем установки.
   Без аттестации `complete=False` с пробелом «draining других установок не подтверждён».
   Агентские вызовы флага не передают.

**Устойчивость барьера до переключения.** Порядок §10 — остановка создания, разбор, затем
активация. Draining держится, пока не активирован `review`; `df setup-repo apply` (спека B)
вызывает барьер **дважды**: перед предложением PR активации и повторно непосредственно перед
его мержем человеком. Второй вызов с новыми живыми заявками или `complete=False` останавливает
мерж активации. Заявка, всё же доставленная после переключения в обход draining, отказывает на
admission (Task 12) — это второй рубеж, а не замена порядка.

`live_review_requests` (предпросмотр Q9 для спеки B): живые заявки `review/v1` всех продуктов
источника — по форджу (OPEN candidate/brief с манифестом v1); недоступный продукт → `gaps`;
пустой список при `gaps` — никогда не «последствий нет».

- [ ] **Step 1: Write failing tests** — без `--attest-drained` при прочих пустых → 3; с ним → 0;
  инвалидация каждой стадии (`--invalidate`) → следующий барьер 0; маркер от агента или
  отредактированный → не признан;
  по тесту на каждую стадию; candidate другой установки
  (нет локального леджера) виден по форджу; смёрженный legacy-candidate со смёрженным finalize, но
  без подписи узла в базе → 1; неполная пагинация → 3; draining выключен → 3; смёрженный бриф без
  локального штампа → 1, с `--confirm-resolved` → 0; `live_review_requests` с недоступным
  продуктом → `complete=False`.
- [ ] **Step 2: Run** `uv run --group governance pytest tests/test_act_barrier.py -v` → FAIL
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS; полный набор; `uv run ruff check . && pyrefly check`.
- [ ] **Step 5: Commit**

```bash
git add governance/act_barrier.py Makefile tests/test_act_barrier.py
git commit -m "feat(act): барьер миграции по форджу и предпросмотр затрагиваемых заявок (§10, Q9)"
```

**Конец части B → PR devtools #2** (authority-root, мерж человеком).

---

## Часть C — путь брифа (после M2 discovery)

### Task 17: Бриф `review/v1` на стороне devtools — производитель, мерж, проверка

**Files:**
- Modify: `contracts/discovery-approval/` (ре-вендор из релиза M2 discovery + `PINNED.txt`)
- Modify: `governance/approval_request.py` (схема v2), `governance/brief_tools.py`
  (`expected_request` ~60, создание ~217, повторное усыновление PR)
- Modify: `governance/brief_merge_check.py`, `human-merge.sh`, `merge-pr.sh` (ветка `brief/*`),
  `governance/act_premerge.py` (вид заявки «бриф»), `governance/brief_provenance.py`
- Test: `tests/test_approval_request.py`, `tests/test_brief_tools.py`,
  `tests/test_brief_provenance.py`, `tests/test_discovery_approval.py`, новый
  `tests/test_act_brief.py`

Правила (§9.2, §7, §10):
- **Производитель (devtools).** `approval_request.py` получает схему
  `discovery-brief-approval-request/v2`: `act: review/v1`, единый пин `policy{repo, branch,
  activation_commit, files_sha256}` (§6) вместо `repo/ref/path/sha`; v1 остаётся для чтения
  legacy. `brief_tools.expected_request` выбирает версию по режиму так же, как Task 13:
  `ACT_MODE[P] = review` и `settings.review_v1` → v2; `review` без `review_v1` → отказ создания;
  `draining` при `merge` → отказ создания v1. Повторное усыновление открытого brief-PR сверяет
  заявку из головы и не переписывает её версию.
- **Предпроверка до approve.** `brief_merge_check` для v2 проверяет состав, self-hash
  (вендоренная проекция `review/v1`), живой пин — без мержа; выход 0 печатает «одобрите PR <url>».
  `human-merge.sh brief/*` при v2-заявке не мержит, печатает подсказку и предпроверку. Обе
  формы запуска `brief_merge_check` в `human-merge.sh:171,178` получают `--group governance`
  (v2-проверка читает активации через steward); распознаватель формы в
  `tests/harness_trust.py:48` — то же правило, что Task 15; subprocess-тест обёртки в
  отдельном окружении для v2 и регрессия v1.
- **Метка.** Производитель v2 вешает `HUMAN_APPROVAL_LABEL = "human-approval-required"`;
  `brief_tools.LABEL = "human-merge-required"` (`brief_tools.py:38`) остаётся для v1.
- **Агентская доставка после approve.** `merge-pr.sh` для `brief/*` с меткой
  `human-approval-required` запускает `act_premerge` (Task 15) с видом заявки «бриф»: заявка v2 из
  головы, живой пин, `ACT_MODE[P] = review`, D1 на голове, граница секунды, повторные чтения;
  0 → мерж с `sha=<head>`. Вызывающий — существующая команда `brief_tools approve`
  (`brief_tools.py:347`; CLI имеет только `propose/approve`): для v2-заявки при **открытом**
  brief-PR она сначала проверяет акт (`open_review` + D1); нет акта → код 2 «ждём approve
  человека»; есть → `ops.merge` (через `merge-pr.sh`), затем продолжает обычный путь
  `approve` по смёрженному PR. Без метки — прежнее поведение.
- **Акт брифа версионирован** (`brief_provenance.Act`). Сегодня `Act` несёт `merged_by` как
  подпись: `check_operator_brief` (`brief_provenance.py:234`) требует `approver == merged_by`,
  `check_policy` (`:306`) — мержера в утверждающих, `as_record` (`:53`) пишет
  `approver = merged_by`. Для `review/v1` вводится `ActV2(repo, pr, merge_commit, merged_at, dir, brief_self_hash,
  request, approver, review_id, delivered_by, delivered_at, activation_commit,
  unverified_premises: tuple[str, ...])` — `unverified_premises` обязательное, без умолчания:
  `read_act` для `review/v1` получает `Trust` (тот же, что передан в `check_delivery`) и
  заполняет поле из `trust.unverified`;
  идентичность PR и merge-коммит сохраняются (их читают `brief_tools.approve:359` и
  `working_version:257`), `request` — заявка v2 из головы: `approver` — логин из `Accepted`,
  `delivered_by` — мержер. `check_operator_brief` сверяет `approver` штампа с `ActV2.approver`
  и `approval_review_id`, `delivered_by` — с доставкой; `check_policy` для `ActV2` не
  перепроверяет мержера (D3 уже в `check_delivery`) и сверяет `activation_commit` с пином;
  `as_record` пишет `approval_act`, `approval_review_id`, `delivered_by`, `delivered_at`
  (§7) и `unverified_premises` из `ActV2.unverified_premises` (всегда, пустые — `[]`; тест
  итоговой записи штампа: `False` → `[]`, `None` и нет ключа → `["allow_rebase_merge"]`); `_judge_approve` и все вызывающие `read_act` ветвятся по типу.
- **P/W/C для `ActV2` (§6: `policy-reconfirm` сохраняет различие P/W/C).** P —
  `activation_commit` заявки; C — текущая активация открытой эпохи (`load_trust` +
  `load_ranges`); W — `working_version` с той же логикой, что сегодня
  (`brief_provenance.py:250`), но строки подтверждения называют активацию:
  `policy-reconfirm: <policy-repo>@<activation_commit>`; действительна, если коммит — активация
  источника v2, комментарий не правлен, позже `merged_at`, автор ∈ `human_approvers` **этой**
  активации. Использование утверждённого брифа engineer-путём допускается при `W = C`; иначе
  отказ «нужен policy-reconfirm» (как сегодня). Исторический акт при этом не пересматривается;
  reconfirm не оживляет живую заявку (Task 13 — живые заявки только `live_policy_refusal`).
- **Проверка после доставки.** `brief_provenance.read_act` идёт через тот же общий вход, что
  Task 13: заявка из головы смёрженного brief-PR → `check_delivery` (пути `brief.md`,
  `approval-request.yaml`, проекция self-hash discovery). `ctx.legacy` для брифа —
  `brief_provenance.legacy_outcome(ops, repo, pr)`: прежний `read_act` (`brief_provenance.py:139`)
  как чистая функция — историческая версия и мержер **на пине**, без требования «пин = текущая»
  (это правило узла, к брифу не переносится; допуск upstream — `check_policy` по P/W/C).
  После `resolve`: `LegacyAccepted` → строится прежний `Act` (тот же код), `Accepted` →
  `ActV2`, `Rejected`/`Unavailable`/`Ambiguous` → `Refusal` с прежними кодами выхода.
- Вендоренная проекция `discovery-approval`: неизвестная версия маркера — отказ до хеша.
- **Аудит позднего отзыва брифа (§8, №10).** `brief_tools approve` и `status` для `ActV2`
  сравнивают события отзыва с `created_at > merged_at` и дописывают их в
  `<run_dir>/brief-approval/audit.jsonl` (`{"event": "review_dismissed_after_delivery",
  "review_id", "at"}`, идемпотентно по `review_id`); решение и штамп не меняются; `status`
  показывает событие.

- [ ] **Step 1: Re-vendor** discovery-approval по `PINNED.txt` (приём Task 11 Step 1).
- [ ] **Step 2: Write failing tests** — `brief-propose` в режиме review создаёт v2 с единым
  пином; при draining v1 не создаётся; v2 — предпроверка без мержа; `human-merge.sh` не мержит
  v2; `merge-pr.sh brief/*` с меткой и предпроверкой 0 → мерж с `sha=`; сквозной тест
  `brief-propose` (v2, метка `human-approval-required`) → approve Alice → `brief_tools approve`
  доставляет (мержер — App) → `ActV2(approver="alice", delivered_by="<app>[bot]")` → штамп с
  полями §7 → `check_operator_brief`/`check_policy` проходят → бриф читается engineer-путём
  (приёмка №1 на пути брифа); законный legacy-бриф после смены текущей политики → `Act`
  установлен (историческая проверка на пине), допуск upstream — по P/W/C; смена C после
  доставки v2 → engineer-путь отказывает до
  `policy-reconfirm` человеком, после него — допускает, исторический акт не меняется;
  поздний отзыв после доставки → одна запись `audit.jsonl`, повторный `approve`/`status` её не
  дублирует, штамп прежний; approve нет → `brief_tools approve` код 2 без мержа; v1 после
  активации review — отказ admission (№24 для брифа); неизвестный маркер — отказ до хеша.
- [ ] **Step 3: Run** `uv run --group governance pytest tests/test_act_brief.py tests/test_approval_request.py tests/test_brief_tools.py tests/test_brief_provenance.py tests/test_discovery_approval.py -v` → FAIL
- [ ] **Step 4: Implement**; Run → PASS; полный набор; `shellcheck merge-pr.sh human-merge.sh`.
- [ ] **Step 5: Commit**

```bash
git add contracts/discovery-approval governance/approval_request.py governance/brief_tools.py \
  governance/brief_merge_check.py human-merge.sh merge-pr.sh governance/act_premerge.py \
  governance/brief_provenance.py tests/test_act_brief.py tests/test_approval_request.py \
  tests/test_brief_tools.py tests/test_brief_provenance.py tests/test_discovery_approval.py
git commit -m "feat(act): бриф review/v1 — заявка v2, агентская доставка после approve, проверка"
```

**Конец части C → PR devtools #3** (authority-root, мерж человеком).

---

## Часть D — живая приёмка M3

### Task 18: Матрица §11.1 — стенд, затем реальный продукт

**Files:**
- Create: `docs/acceptance/2026-10-approval-act-review/README.md` (протокол и итог)
- Create: `docs/acceptance/2026-10-approval-act-review/evidence.jsonl` (строка на сценарий и фазу)
- Create: `scripts/act_acceptance.py` (сбор evidence) + `tests/test_act_acceptance_script.py`

**Фаза 1 — стенд.** Разрушительные сценарии (force-push источника, временная запись агенту,
скрытый bypass) нельзя ставить на настоящем `approval-policy`. Поэтому стенду нужен свой
источник политики `andrei-shtanakov/df-roles-probe-policy` — его создаёт человек по шагам H1.
`load_trust` читает контракт источника и журнал **с `origin/<default>` чекаута devtools**
(Task 12a), поэтому стенд получает **свой forge-origin devtools**: репо
`andrei-shtanakov/df-roles-probe-devtools` (зеркало devtools), в дефолтной ветке которого
`contracts/approval-policy-source/v2/` указывает на стенд-источник; правки его журнала — тоже
только PR с мержем человеком. Приёмка исполняется из клона этого репо. Первая строка evidence —
**фактически прочитанные** `repo`, `branch`, `bootstrap_activation`, номер эпохи и SHA
`origin/<default>`; расхождение с ожидаемым стендом — стоп до сценариев. Подготовка стенда:
- ruleset D7 на `df-roles-probe`: `dismiss_stale_reviews_on_push`, `require_last_push_approval`,
  `require_code_owner_review`;
- CODEOWNERS — только логины `human_approvers`, владеет `/.github/`;
- App `df-probe-reviewer-ashtanakov` в `AUTHORIZED_MERGER_ACCOUNTS`, ключ в
  `$XDG_CONFIG_HOME/darkfactory/apps/5285716.pem`;
- `ACT_MODE[andrei-shtanakov/df-roles-probe] = review`.

**Фаза 2 — реальный продукт** (спека §11.1: «стенд, затем реальный продукт») с настоящим
`approval-policy` после H1–H2. Продукт выбирает владелец; переключение — через барьер (Task 16).
Прогоняется **вся** матрица §11.1, кроме разрушительных сценариев, требующих нарушить периметр
настоящего источника (выдать агенту запись, force-push/прямой push или rebase-merge в источник,
скрытый bypass, порча журнала): №21, 28, 29, 33, 34, 35, 36, 37, 39, 45. Они — **только на
стенде** (решение владельца 13.10, спека §11.1 rev 15). Evidence фазы 1 по ним принимается при
четырёх условиях, каждое — отдельной строкой evidence или разделом README:
1. **Провенанс стенда:** SHA проверенной реализации devtools (`checkout_sha`); пин steward в
   devtools (`uv.lock`); **установленная** discovery — SHA её чекаута/релиза и пин steward в её
   `uv.lock` (discovery — не зависимость devtools, её SHA снимается с исполняемой установки, а
   не из `uv.lock` devtools); пин вендоренного контракта заявки в devtools (`PINNED.txt`);
   `activation_commit` стенд-источника, номер эпохи и
   конфигурация стенда (ruleset, `bypass_actors`, CODEOWNERS, App id, `ACT_MODE`) — снимок,
   снятый человеком в момент прогона.
2. **Соответствие механизма реальной установке:** для каждой строки — что проверяемый путь кода
   тот же: **те же значения всех пунктов провенанса п. 1** (SHA devtools, SHA установленной
   discovery, пины steward у обоих, пин вендоренного контракта), та
   же схема контракта `approval-policy-source/v2`, отличаются только данные (`repo`,
   `bootstrap_activation`, журнал, `ACT_MODE`). Частичный дифф («не менялись такие-то файлы»)
   соответствия не доказывает: путь сценария проходит и через `approve_node.py`,
   `approval_ledger.py`, `brief_provenance.py`, вендоренные контракты и реализацию discovery.
   Любое расхождение SHA или пинов — повторный стендовый прогон строки на SHA фазы 2; до него
   строка — `not_verified`.
3. **Пометка в итоговой матрице:** фаза 2 по этим строкам — `проверено на стенде` со ссылкой на
   строку evidence фазы 1 (не `pass` фазы 2).
4. **Безопасные проверки реального периметра и сквозной путь — на продукте:** самоотчёт прав
   каждого credential процесса на настоящем источнике (№29 без выдачи записи), чтение ruleset
   (`deletion`, `non_fast_forward`, `pull_request`), состояние `allow_rebase_merge` в
   рантайме (ожидаемо `unverified` у агента) и его проверка человеком из установочного evidence,
   форма истории настоящего источника от якоря (№21/№28 без порчи), валидатор журнала на
   настоящем `epochs.yaml` (№45 без мутации), маркер отсутствует (№36), сценарии 1–4 на обоих
   путях — сквозной путь заявка → approve → мерж App → конверт → продолжение конвейера.

**Проверка таймлайна (§8, «требует проверки на стенде»).** Полнота `review_dismissed`, наличие
`previousReviewState`, метки событий относительно `merged_at` — отдельные строки evidence.

**Правило итога.** Каждая строка §11.1 получает `pass` только при evidence, совпадающем с колонкой
«Ожидание» дословно по смыслу. Строки, чьё ожидание само является пределом гарантии, проходят
evidence этого предела:
- №33 — рантайм не сделал вывода «bypass нет»;
- №34 — обнаруживший потребитель заблокирован, второй до закрытия эпохи не обязан;
- №21 — разрыв формы истории (прямой push, пропуск данных) → `unavailable` и отказ;
  force-push человеком на предка — вне гарантии v1, evidence фиксирует, что он не обнаружен;
- №28 — многокоммитный rebase-merge или коммит first-parent без PR → `unavailable`;
  однокоммитный rebase-merge проходит сверку как squash, indirect merge с формально связанным
  PR не обнаруживается — evidence фиксирует оба предела (стенд: включить rebase-merge
  человеком, смержить однокоммитный и многокоммитный PR, затем вернуть `false`).

Для них используются векторы CI плюс живой прогон на стенд-источнике. Нет credentials для живой
проверки → `not_verified`; такая строка **блокирует** M4. Исключений по номерам нет.

- [ ] **Step 1:** `scripts/act_acceptance.py` пишет строку с полями `phase`, `scenario`,
  `checkout_sha`, `review_id`, `login`, `head_sha`, `request`, `activation_commit`,
  `merge_commit`, `merged_by`, `outcome`, `expected`, `verdict`. Тест на стабах:
  `uv run --group governance pytest tests/test_act_acceptance_script.py -v`.
- [ ] **Step 2:** фаза 1 — все строки §11.1 и проверки таймлайна; каждая строка evidence сразу
  пишется в файл.
- [ ] **Step 3:** фаза 2 — все остальные строки §11.1 и безопасные проверки п. 4 выше на
  реальном продукте; строки провенанса (п. 1) и соответствия механизма (п. 2).
- [ ] **Step 4:** README: таблица «сценарий → фаза → pass / fail / not_verified / проверено на
  стенде → строка evidence». Любой `fail` или `not_verified` — стоп M4, находка уходит в
  задачу.
- [ ] **Step 4a:** общий интеграционный обзор всего пути утверждения (раздел «Решения
  владельца», п. 3) по актуальному коду и evidence — до Task 19.
- [ ] **Step 5:** commit + PR devtools (evidence — данные и проза; мерж агентский, если PR не
  трогает authority-root).

---

## Часть E — дефолт M4

### Task 19: `review_v1` по умолчанию

**Files:**
- Modify: `governance/act_settings.py` (`DEFAULT_REVIEW_V1 = True`)
- Modify: `tests/test_act_settings.py` (`test_absent_file_gives_defaults` → `review_v1=True`)
- Modify: `CLAUDE.md` (строки `merge-pr.sh`, `human-merge.sh`, раздел мержа: акт = approve)

Условие входа: README Task 18 — все строки §11.1 `pass` в фазе 1 и в фазе 2 (кроме
разрушительных строк, помеченных в фазе 2 «проверено на стенде» при выполненных условиях 1–4
Task 18), без `fail` и `not_verified`; интеграционный обзор Step 4a пройден. Режим по продукту по-прежнему включает `ACT_MODE` через барьер (Task 16). Этот
шаг меняет только одно: создаёт ли установка заявки `review/v1` без явной настройки.

- [ ] **Step 1:** правка теста → FAIL; **Step 2:** правка константы → PASS;
  **Step 3:** полный набор; **Step 4:** commit + PR (authority-root → человек).

```bash
git add governance/act_settings.py tests/test_act_settings.py CLAUDE.md
git commit -m "feat(act): review/v1 по умолчанию после живой приёмки M3"
```

---

## Межрепные заявки (после мержа пары спека+план)

- **steward** (inbox, `slug: approval-facts-v3`), M0:
  - контракт v3;
  - библиотека `steward.approvalfacts.v3` с интерфейсом из раздела «Интерфейс M0», включая
    `read_open_review` и `read_activation`;
  - векторы §5.6 в `contracts/approval-facts/v3/vectors/`, включая историю источника с
    многокоммитным rebase-merge (`HistoryViolation`) и с однокоммитным (принят как squash —
    вектор фиксирует предел, а не нарушение);
  - запрет пересечения `human ∩ agent`;
  - миграция `gate-check --stage release` на запись `delivery`;
  - провайдер статуса узла `node_status` для гейтов, читающих `status`: узел `review/v1` без
    провайдера — «не одобрен».
- **discovery** (inbox, `slug: approve-review-v1`), M2:
  - `discovery approve` на библиотеке steward (пиненая git-зависимость, Q2);
  - `approval-request.yaml` v2;
  - пины §6 (пиненая версия, не текущая);
  - проекция self-hash `review/v1` (§7);
  - отказ неизвестного маркера до хеша;
  - **draining** — `act_migration = "draining"` в `$XDG_CONFIG_HOME/darkfactory/act.toml`
    запрещает создание `approval-request.yaml` v1. Тот же файл и те же значения, что у devtools
    (Task 10).
- TODO.md devtools: пункты частей B и C с `@blocked_by:todo://steward/approval-facts-v3` и
  `@blocked_by:todo://discovery/approve-review-v1`.
