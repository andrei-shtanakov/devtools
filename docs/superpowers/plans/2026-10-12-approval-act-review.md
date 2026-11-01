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
владельцем 2026-10-12; rev 15 — уточнения по решениям владельца 13.10; rev 18 — решение владельца
С-1, 01.11: read-only токен истории, §6.2). План спорит со спекой, а не заменяет её: исполнитель читает обе.

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

## Ревизия спеки A rev 16 (13.10, проектирование спеки B)

Корень доверия переезжает из файлов devtools в отдельный репо установки `df-trust` с защищённой
привязкой `/etc/df/trust.toml` и чтением по снимку (спека A rev 16 §6.1 п. 1, §6.3, §6.5);
в v1 оба источника доверия публичны, самоотчёт — по credentials процесса (§6.2). В плане
изменены: H2 (bootstrap `df-trust` вместо файлов devtools), Task 12a п. 1 и п. 3, стенд Task 18.
Часть A (влита, #613) не меняется в разборе: `parse_source`/`parse_journal` независимы от
транспорта; `load_source()` выводится из контура решений в Task 12a (тест на отсутствие вызова
из пути решения). Детализация части B на воротах учитывает эти изменения, а также спеку B
(`2026-10-13-df-installer-design.md`):
- в v1 оба источника доверия **публичны**, чтение содержимого без credentials; App-читателя нет
  (С-1, 01.11, спека rev 18 §6.2: либо read-only токеном истории из привязки — Task 11c);
- самоотчёт — каждым процессом по credentials, которые он держит (спека B §6.1): `df-exec` —
  токен machine user на **обоих** репо доверия; издатель — установки App продукта;
- **два развёртывания** (спека B §5.4): во флоте владельца до спеки B действует Task 15a —
  App-режим `merge-pr.sh` только для candidate/brief `review/v1`, finalize и stop прежним путём
  ADR-ECO-011 D5; в установке `df` все четыре вида мержит издатель по нормативной таблице
  маршрутов спеки B §5.4. Правила не смешиваются в одной установке;
- `TrustRoot` проходит до конечных записей: `record_review_act` (узел), `ActV2` (бриф), ответ
  издателя, строки приёмки Task 18;
- нарушения периметра — журнал событий периметра (спека A rev 16 §6.3, спека B §6.1) вместо
  маркер-файлов части A; пишет издатель (`record-violation`), отдельная задача на воротах части B.

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
| A' — CLI журнала | 10a | Часть A влита | PR devtools (authority-root → человек) |
| H — шаги человека | H1–H2 | 10a влита | PR в `approval-policy`, репо `df-trust` + привязка `/etc/df/trust.toml` |
| B — интеграция узла | 11, 11a, 11b, 12, 12a, 14, 11c, 13, 13a, 15, 15a, 16 (порядок исполнения — ревизия 31.10; 11c — решение владельца С-1, 01.11) | **M0 steward** выпущен (v0.2.0, `68f46a9`, 31.10); H1–H2 | PR devtools #2 |
| C — путь брифа | 17 | **M2 discovery** | PR devtools #3 |
| D — живая приёмка M3 | 18 | B, C | evidence в devtools |
| E — дефолт M4 | 19 | D пройдена | PR devtools #4 |

**Ворота перед частью B.** Задачи 12a (тела тестов), 13–17 (включая 13a, 15a) заданы правилами, интерфейсами и
перечнем тестов, а не полным кодом:
они вплетаются в `approve_node.py` (2600 строк) и зависят от фактического API релиза M0. Перед
стартом части B исполнитель сверяет «Интерфейс M0» с релизом steward и в любом случае
обновляет этот план (Tasks 11–17) отдельным коммитом до кода — с тем же уровнем детализации,
что задачи 1–12 (полный код тестов и реализации), и проводит его через тот же локальный круг
ревью, что и эту пару. **31.10:** M0 выпущен (steward v0.2.0); детализация выполнена ревизией
«Ревизия части B по релизу M0, 31.10» (начало части B); ревью обновлённого плана — до кода.

Части M0 (steward) и M2 (discovery) — в своих репо, по inbox-issues с интерфейсом из раздела
«Интерфейс M0» ниже. Этот план их не исполняет. M0 выпущен 31.10 (steward#211); раздел ниже —
фактический API релиза и сверка с прежними ожиданиями.

### Интерфейс M0 (релиз steward v0.2.0, 68f46a9)

Релиз: тег `v0.2.0` = `68f46a9b2e5cfe9ecd1f09524e986c3f687f4155` (steward#211, ответ о выпуске
31.10). Инструкция потребителя — `steward/docs/approval-facts-v3.md`; нормативная спека —
`steward/docs/superpowers/specs/2026-10-30-approval-facts-v3-design.md`. Пин — **полный SHA**
в `[tool.uv.sources]`, тег — метка (инструкция, «Вендоринг», п. 1).

devtools импортирует `steward.approvalfacts.v3` **только** в `governance/act_facts.py`
(Task 11a) и только внутри функций (контракт импорта без группы `governance`). Исключение — тесты
векторов (`run_vector`, `replay_transport`) и `tests/act_check_cases.py`. Импорт пакета steward не
делает ввода-вывода (`tests/approvalfacts/v3/test_api.py::test_import_has_no_io` у steward).

Фактические имена (`v3.__all__`), которыми пользуется devtools:

```python
from steward.approvalfacts import v3

EventKey = tuple[str, str]  # ("review", "<id>") | ("dismissal", "<review_id>")

@dataclass(frozen=True)
class Review:            # login — GraphQL-форма: у бота без "[bot]" (О9)
    review_id: str; login: str; type_hint: str; state: str
    commit_id: str; submitted_at: str | None   # канон YYYY-MM-DDTHH:MM:SSZ

@dataclass(frozen=True)
class Dismissal:
    review_id: str; actor: str; created_at: str; previous_state: str | None

@dataclass(frozen=True)
class ReviewFacts:
    repository: str; pr: int; head_sha: str; base_ref: str; pr_state: str
    reviews: tuple[Review, ...]; dismissals: tuple[Dismissal, ...]

@dataclass(frozen=True)
class PathBlob:
    path: str; blob_at_merge: str | None; blob_at_head: str     # О1

@dataclass(frozen=True)
class Delivery:
    merged: bool; merge_commit: str | None; merged_at: str | None
    merged_by_login: str | None; merged_by_type_hint: str | None
    head_at_merge: str | None; in_base: bool; paths: tuple[PathBlob, ...]

@dataclass(frozen=True)
class Unavailable:
    reason: str          # "<code>" | "<code>: <detail>"; code ∈ UNAVAILABLE_CODES (О5)
    @property
    def code(self) -> str: ...

@dataclass(frozen=True)
class Activation:
    pr: int; merge_commit: str; merged_at: str        # merged_at — из PR, не коммита
    files_sha256: dict[str, str]                      # hex; отсутствующий файл — без ключа (О3)

@dataclass(frozen=True)
class HistoryViolation:
    reason: str          # "<code>: <detail>"; code ∈ HISTORY_VIOLATION_CODES
    @property
    def code(self) -> str: ...

UNAVAILABLE_CODES: frozenset[str]          # 24 кода, закрытый словарь
HISTORY_VIOLATION_CODES: frozenset[str]    # commit_without_pr, rebase_intermediate,
                                           # pr_base_mismatch, anchor_not_reached
POLICY_FILES = ("policy/approvers.env", "policy/modes.yaml", "policy/actors.yaml")

class Transport(Protocol):
    def send(self, request: Request) -> Response: ...
Request(method, path, op=None, variables=None, query=None)
Response(status: int | None, body=None, link=None, error=None)
GhTransport(gh_config_dir: Path | None = None, *, timeout: float = 60)   # О10
replay_transport(spec: Mapping) -> ReplayTransport

def read_act_facts(transport, repo: str, pr: int, *, paths: Sequence[str]
                   ) -> tuple[Delivery, ReviewFacts | None] | Unavailable: ...
def read_open_review(transport, repo: str, pr: int) -> ReviewFacts | Unavailable: ...
def effective_reviews(facts: ReviewFacts, t: str, *, before: frozenset[EventKey]
                      ) -> dict[str, Review] | Unavailable: ...   # t не в каноне → ValueError (О8)
def read_policy_history(transport, repo: str, branch: str, *, anchor: str,
                        until: str | None, files: Sequence[str] = POLICY_FILES
                        ) -> tuple[Activation, ...] | HistoryViolation | Unavailable: ...
def read_activation(transport, repo: str, merge_commit: str, *,
                    files: Sequence[str] = POLICY_FILES) -> Activation | Unavailable: ...
def run_vector(vector: Mapping) -> VectorOutcome      # VectorOutcome(problems, requests).ok
```

Гейты steward (`gate-check`, та же поставка M0, О4/О14): `--act-mode legacy|review` **обязателен**
(без него — код 2; API `collect_bundle(graph, spec_dir, *, act_mode)` без него — `TypeError`);
`--node-status <file.json>` = `{"snapshot_digest": "sha256:…", "nodes": {node: status}}` только
при `--act-mode review`; `gate-check --print-snapshot-digest [--candidate] <dir>` печатает
дайджест снимка (без `--act-mode`). Ответ провайдера — `approved` | `not_approved:<reason>` |
`unavailable:<reason>`. Каталог гейтов v3 (`profiles/gate-catalog.yaml`, `version: 3`) — коды
`GC-ACT-MODE-CONFLICT`, `GC-ACT-PROVIDER-MISSING`, `GC-ACT-NOT-APPROVED`.

**Правило транспорта** (инструкция, «Транспорт»; steward §12 п. 1): факты акта
(`read_act_facts`, `read_open_review`) — GraphQL, только `GhTransport` с токеном профиля агента;
история и активация (`read_policy_history`, `read_activation`) — только REST и **обязаны**
читаться без credentials. `GhTransport` без токена отдаёт `Response(status=None)` →
`Unavailable: transport`, поэтому devtools реализует собственный анонимный REST-транспорт
(`governance/act_anon.py`, Task 11b). **С-1 (01.11, спека rev 18 §6.2):** тот же транспорт с
read-only токеном истории из защищённой привязки (Task 11c); библиотека credentials не
проверяет, а буква инструкции steward («обязаны работать без credentials», «токен потребителю
не предлагается») выведена из прежней §6.2 и устарела — inbox-issue steward#232 (раздел
«Решение владельца 01.11 (С-1)»). Любое исключение из вызова библиотеки — «факт не
установлен», как `Unavailable`, никогда не разрешение (инструкция, «Транспорт»; адаптер ловит
`Exception` на каждой границе вызова).

**Сверка ожиданий с релизом.** Левая колонка — прежний раздел «Интерфейс M0 (ожидаемый)»
(ревизия плана до 31.10); «О» — номер отступления в steward §10 / инструкции.

| Ожидание devtools | Что выпущено | Следствие для задач devtools |
|---|---|---|
| `PathBlob.blob_at_merge: str` | `str \| None` — установленное удаление (О1) | без изменений: `DeliveryView.blobs` части A уже несёт `(None, sha)` → `Rejected(I_BYTES)` (заметки части A, п. 5). Task 11a — тест маппинга |
| `read_policy_history(..., anchor, until)` | + `files=POLICY_FILES` (О2), то же у `read_activation` | вызов не меняется; Task 11a передаёт `act_policy.POLICY_FILES` явно и тест сверяет его с `v3.POLICY_FILES` |
| формат `files_sha256` не задан | hex без префикса; отсутствующий файл — без ключа (О3) | Task 12a: набор с отсутствующим файлом → `Unavailable` (тот же исход, что `PolicyError` части A); байты набора, прочитанные devtools, сверяются с `files_sha256` steward (смешение → `Unavailable`) |
| `node_status: Callable[[str], str] \| None` у гейтов | `NodeStatus = Callable[[str, Snapshot], str]`; CLI `--act-mode`/`--node-status`/`--print-snapshot-digest` (О4) | Task 14: провайдер отвечает за **снимок**, который читает steward (§12 п. 3), файл несёт `snapshot_digest`; отрицательный двойник «тот же ответ при изменённых байтах» |
| `Unavailable.reason: str` | `"<code>[: detail]"` + `.code` + закрытый словарь (О5) | адаптер переносит `reason` дословно в `act_time.Unavailable`; ветвление по `code` — только для `HistoryViolation` против `Unavailable` (разные типы), коды `rebase_intermediate`/`commit_without_pr` в решении не различаются (§12 п. 5) |
| векторы одной формы `{transport, repo, pr, t, before, expect}` | `kind` + подкаталоги `open_review/`, `history/`, `activation/`; ключи `expect.waiting_delivery`, `expect.delivery`, `expect.effective_commit` (О6) | Task 11a: вендорится весь `contracts/approval-facts/v3/` (79 векторов), обход рекурсивный; прогон **только** через `v3.run_vector` (M0-J, MUST) + отдельная сверка адаптера на тех же обменах |
| `replay_transport`, `verify_vendored_vectors` из steward | `replay_transport` есть, `verify_vendored_vectors` нет — copy-integrity на потребителе (О7) | Task 11a: своя сверка по `MANIFEST.json` в обе стороны + sha256 самого манифеста в `PINNED.txt` + равенство пина вендора и `[tool.uv.sources]` (upstream-drift) |
| `effective_reviews` → `Unavailable` на неканоничном `t` | `ValueError` (О8) | адаптер форматирует `t` из `Clock.t` (`parse_ts` уже нормализовал) и всё равно ловит исключение → `Unavailable` |
| форма `merged_by_login` не задана | GraphQL-форма без `[bot]` (О9) | без изменений в ядре: `act_policy.classify` и `same_login` уже равняют `slug[bot]` ≡ `slug` (Review Focus 2); фабрика `tests/act_check_cases.py` переходит на `"df-reviewer"` |
| «`v3.GhTransport(...)` or equivalent» | `GhTransport(gh_config_dir=None, *, timeout=60)` (О10) | Task 11a `act_facts.gh_transport(path)`; профиль — `~/.config/review` (`REVIEW_GH_CONFIG_DIR`) |
| история по **закреплённой** ветке | ветка обязана быть веткой по умолчанию источника, иначе `branch_not_default` (О11) | `APPROVAL_POLICY_BRANCH` в `df-trust/source.env` обязан равняться default branch `approval-policy` (fail-closed при смене; с rev 18 — требование спеки §6.1 п. 1, решение владельца С-2); df-trust читает своя функция по закреплённой ветке (спека §6.1 п. 1 «не следует за сменой default branch») — steward туда не вызывается. Примечание к H1/H2 |
| — | О12 снято в r2 | — |
| этап отказа по `previous_state`/`submitted_at`/`DISMISSED` без события не задан | отказ на **чтении**; `effective_reviews` повторяет проверки (О13) | Task 12: фабрика контекста обязана давать целые факты — отозванное ревью в состоянии `DISMISSED` (иначе `malformed` в обоих порядках и вместо `Ambiguous` получился бы `Unavailable`) |
| гейты без нового параметра работают как прежде | `act_mode` обязателен, ломающее (О14) | **Task 11 в том же коммите, что пин**: все вызовы `gate-check` и `collect_bundle` получают явный `legacy`; Task 14 заменяет его результатом admission продукта + провайдером |
| неустановленный актор не оговорён | `actor_unresolved` на чтении для **всего** PR — любой автор ревью, актор отзыва, мержер (О15) | заявка с удалённым пользователем среди ревьюеров навсегда `Unavailable` (заявка жива, но решения нет) → штатный выход `--repropose` (Task 13a называет и этот случай); текст `_unresolved` в Task 13 называет код |
| `read_open_review` — «те же два прохода» | так и есть; `pr_state` как есть, `headRefOid` входит в равенство проходов | Task 13/15: голова из фактов сверяется с `--head`; `pr_state` судит потребитель |
| `read_activation(transport, repo, merge_commit)` | ветку не проверяет; коммит без PR → `no_pr_for_commit` | Task 12a: время якоря — только `read_activation`; принадлежность якоря ветке доказывает история диапазона |
| гейты получают провайдер «узел review/v1 без провайдера — не одобрен» | `GC-ACT-PROVIDER-MISSING` под `review` без провайдера; под `legacy` — детектор `GC-ACT-MODE-CONFLICT` по `<spec>/../approvals/` | Task 14: под `review` провайдер обязателен; `legacy` допустим только когда у продукта нет режима `review` |
| — (заметка §12 п. 2) | gate-catalog v3 приходит с пином | Task 11: ре-вендор `profiles/gate-catalog.yaml` до `version: 3` тем же коммитом |
| — (ответ steward#211, п. 6) | живой `approval-policy` сейчас — один корневой прямой коммит: `read_policy_history(main, anchor=<этот коммит>)` → `HistoryViolation: commit_without_pr` | активаций нет до H1 (fail-closed). После H1 якорь = merge-коммит PR H1, корень в цепочку `anchor…HEAD` не входит, так что сам корень нарушением не станет. Live-проверки части B (Task 18) — только после H1/H2; юнит-тесты — на replay-обменах. Примечание к H1 |

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

## Часть A' — CLI проверки журнала (до H2)

### Task 10a: `act_epochs --check-transition` и CI-шаблон `df-trust`

**Files:**
- Modify: `governance/act_epochs.py` (`main()`; CLI)
- Create: `templates/df-trust/.github/workflows/epochs.yml` (шаблон для репо `df-trust`)
- Test: `tests/test_act_epochs_cli.py`

**Interfaces:** `python governance/act_epochs.py --check-transition <base-sha> --file epochs.yaml
--source-env source.env` в git-чекауте `df-trust`; коды: 0 — допустимо, 1 — недопустимый переход
или bootstrap, 2 — недоступный `BASE_SHA`/ошибка разбора аргументов.

Алгоритм — H2 п. 3 (bootstrap без файла в существующем базовом коммите → ровно одна открытая
эпоха 1 с якорем `BOOTSTRAP_ACTIVATION` из `source.env` того же head; файл есть → `check_transition`;
`git cat-file -e <base>^{commit}` не прошёл → 2). Workflow-шаблон: `BASE_SHA` —
`github.event.pull_request.base.sha || github.event.before`, `git fetch --no-tags --depth=1 origin
"$BASE_SHA"`, запуск CLI из **пиненого релиза devtools** (checkout devtools по SHA, `uv run
--frozen`), не из дерева PR.

- [ ] **Step 1:** тесты (временный git-репо): запрещённая смена якоря → 1; правка закрытой эпохи →
  1; законное атомарное закрытие → 0; bootstrap без файла в существующем базовом коммите → 0;
  bootstrap с неверным якорем → 1; две эпохи в bootstrap → 1; несуществующий `BASE_SHA` → 2.
- [ ] **Step 2:** RED → **Step 3:** реализация → **Step 4:** GREEN; ruff, pyrefly.
- [ ] **Step 5:** commit + PR (authority-root → человек).

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
   bootstrap; на нём допустимы разрушительные сценарии приёмки. И стендовый корень доверия
   `andrei-shtanakov/df-roles-probe-trust` (спека A rev 16; Task 18).

**Примечание по релизу M0 (31.10)** — следствия для H1/H2, не новые шаги:
- живой `approval-policy` сейчас — один корневой прямой коммит без PR: `read_policy_history` от
  него — `HistoryViolation: commit_without_pr` (ответ steward#211, п. 6). Первая активация
  появляется только PR-мержем H1 п. 3; `BOOTSTRAP_ACTIVATION` — merge-коммит этого PR, **не**
  корневой коммит (якорь-корень дал бы нарушение формы истории и событие периметра на первом же
  решении). Цепочка `anchor…HEAD` от merge-коммита H1 корень не включает;
- `APPROVAL_POLICY_BRANCH` в `df-trust/source.env` обязан совпадать с веткой по умолчанию
  `approval-policy` (О11: иначе каждое чтение истории — `Unavailable: branch_not_default`;
  с rev 18 — требование спеки §6.1 п. 1, решение владельца С-2);
- привязка H2 п. 4 — формат `df/trust/v1` установщика (`schema`, `host`,
  `df_trust_repository_id`, `df_trust_full_name`, `df_trust_branch`,
  `approval_policy_repository_id`, `transport = "public"`); её разбирает и `act_trust` (Р-6).

### H2: Bootstrap корня доверия `df-trust` (спека A rev 16)

Человек, по спеке B §6.2 (bootstrap-порядок); для флота владельца — его собственный `df-trust`.

1. Создать **публичный** репо `andrei-shtanakov/df-trust` (v1: оба источника доверия публичны;
   приватные не поддерживаются — спека A rev 16 §6.2, спека B §2.3). Начальная база — один коммит.
   Проверить публичность и `approval-policy` (H1): существующий приватный источник — **стоп** с
   показом публикуемого содержимого; открывает его только отдельное действие человека, тариф
   видимость не выбирает.
2. Ruleset дефолтной ветки: только PR, запрет force-push и удаления, `bypass_actors` пуст или из
   `POLICY_ADMINS`; `allow_rebase_merge = false`. Ни machine user, ни App продукта доступа нет.
3. PR с файлами (мерж человеком):
   - `source.env`:

```
APPROVAL_POLICY_REPO=andrei-shtanakov/approval-policy
APPROVAL_POLICY_BRANCH=main
BOOTSTRAP_ACTIVATION=<merge_commit из H1 п. 3>
POLICY_ADMINS=andrei-shtanakov
```

   - `epochs.yaml`:

```yaml
schema: approval-policy-epochs/v1
epochs:
  - epoch: 1
    anchor: <тот же merge_commit>
```

   - CI `df-trust` — проверка перехода журнала, исполняемая из пиненого релиза devtools, не из
     дерева PR. Поставляется **Task 10a до H2** (CLI, workflow-шаблон, тесты); H2 ссылается на
     её пин. Алгоритм (реализует Task 10a):
     - `BASE_SHA` — **неизменяемый** SHA (`github.event.pull_request.base.sha || github.event.before`),
       перед проверкой `git fetch --no-tags --depth=1 origin "$BASE_SHA"`;
     - `git cat-file -e <base-sha>^{commit}` не прошёл → код 2, отказ CI;
     - файл `epochs.yaml` **есть** в базовом коммите → `check_transition(parse(prev), parse(new))`;
     - файла **нет в существующем** базовом коммите (`git ls-tree` пуст) → допустим **только
       bootstrap**: новый журнал — ровно одна открытая эпоха 1 с якорем `BOOTSTRAP_ACTIVATION` из
       `source.env` того же коммита; иначе ненулевой код;
     - тесты: запрещённая смена якоря → ненулевой; правка закрытой эпохи → ненулевой; законное
       атомарное закрытие → 0; bootstrap в существующем базовом коммите без файла → 0; bootstrap
       с неверным якорем → ненулевой; несуществующий `BASE_SHA` → 2.
4. Привязка `/etc/df/trust.toml` (root): `host`, `df_trust_repository_id` (числовой),
   `df_trust_branch`, `approval_policy_repository_id`; транспорт — только `public` (v1: оба репо
   доверия публичны; перед публикацией человек видит содержимое — спека B §2.3).
5. Полная проверка периметра обоих репо доверия человеком (спека A §6.2 а) → evidence.

Пин CLI — merge-коммит Task 10a. Тесты (в части B, Task 12a): оба файла снимка разбираются `parse_source` / `parse_journal`,
`epochs[0].anchor == source.bootstrap_activation`.

---

## Часть B — интеграция узла (после M0 steward)

### Ревизия части B по релизу M0, 31.10

Ворота части B (раздел «Порядок и зависимости»): M0 выпущен (steward v0.2.0 = `68f46a9`,
steward#211). Этот коммит сверяет «Интерфейс M0» с релизом и детализирует задачи 11–16, 12a, 13a,
15a до уровня части A. Что изменилось и почему:

1. **«Интерфейс M0»** — фактический API и таблица сверки (О1–О15, заметки §12, ответ #211).
2. **Task 11 разделена на три**: 11 — пин steward и совместимость существующих вызовов
   `gate-check`/`collect_bundle` (`--act-mode` обязателен, О14, ломающее — без этого пин роняет
   весь набор), ре-вендор gate-catalog v3; 11a — адаптер `act_facts`, вендор контракта, строгий
   прогон векторов через `v3.run_vector` (M0-J, просьба steward); 11b — анонимный REST-транспорт
   `act_anon` (steward его не поставляет, а `GhTransport` без токена — `Unavailable: transport`).
3. **Task 12** — код влит в план ранее; правки по M0 (отозванное ревью в `DISMISSED`, мержер в
   GraphQL-форме, привязка `product` манифеста к репо доставки — заметки части A п. 7,
   `LegacyAccepted.evidence`, `_requires` и удаление записи режима — п. 3, 4) и недостающие строки
   §8.1/§11.1 полным кодом.
4. **Task 12a** — переписана под спеку A rev 16: снимок `df-trust` по привязке
   `/etc/df/trust.toml`, журнал событий периметра (хранилище и клиент уже влиты этапом 2a
   установщика — `df/publisher/perimeter.py`, `df/perimeter_client.py`), маркер-файлы части A
   выводятся из кода. Отдельная задача «журнал событий периметра», которую требовала прежняя
   редакция, закрыта этапом 2a; в плане остаётся адаптер потребителя (`act_journal`).
5. **Порядок исполнения внутри части B меняется**: Task 14 (история и статус) идёт **до** Task 13
   — прежняя редакция Task 13 уже опиралась на `load_history`/`node_status` Task 14, то есть
   номера расходились с зависимостями. Сборка `DeliveryContext` вынесена в `act_context` (Task 12a),
   чтобы и 13, и 14 её потребляли.
6. **Task 13** — вопрос владельца по п. 9 заметок части A (остановка не действующего поколения)
   **решён владельцем 01.11: вариант (Б)** с единым вычислением состояния истории
   (`act_envelope.node_history`, Task 13 Step 8); см. «Решения владельца 01.11».
7. **Task 15** — `act_premerge` опрашивает журнал периметра, как каждый потребитель (спека §6.3):
   узкий клиент `act_journal_wire` (Task 12a, Step 5b) — тот же формат и правило блокировки без
   импорта `df/`, поэтому цепочка обвязки мержа остаётся в `_HARNESS_PREFIXES` (Р-16). Нарушение,
   найденное предпроверкой, регистрируется; блок другого потребителя останавливает мерж.
8. **Live-проверки части B** — только после H1/H2: живой `approval-policy` сейчас один корневой
   прямой коммит, активаций нет (ответ #211, п. 6). **Привязка — предусловие развёртывания**
   (ревизия b1, B1-5): режим продукта выводится из истории `ACT_MODE` по корню доверия; без файла
   `/etc/df/trust.toml` режим «не установлен» (стоп шага, отказ создания заявки), а не legacy. До
   слияния PR devtools #2 привязка ставится на каждой машине, где идут путь узла или раннер; в
   тестах вне `act_*` режим подменяет `tests/conftest.py` (`mode_probe` → `PRE_MIGRATION`).
   **Профиль `gh` runner'а — второе предусловие** (ревью d5): для приватного продукта
   `REVIEW_GH_CONFIG_DIR` (по умолчанию `~/.config/review`) аутентифицирован на github.com с
   правом чтения продукта — проверка допуска берёт свежий снимок базы по HTTPS через
   `gh auth git-credential`, не через `origin`/SSH клона; иначе отказ (fail closed). Публичный
   продукт читается анонимно.

Листинги части B проверены при ревизии: все полные модули разбираются `ast`, `ruff --select F`
не находит неопределённых имён; длина строк приводится `ruff format` при реализации (после
форматирования остаются единичные строковые литералы длиннее 88 — их переносит исполнитель).

**Порядок исполнения части B:** 11 → 11a → 11b → 12 → 12a → 14 → 11c → 13 → 13a → 15 → 15a → 16
(11c — решение владельца С-1, 01.11: сразу после Task 14, чей `real_deps` она меняет). Все
задачи — один PR devtools #2 (authority-root, мерж человеком); каждый шаг коммита держит полный
набор зелёным (`uv run pytest -q` и `uv run --group governance pytest -q` c
`GOVERNANCE_REQUIRED=1`).

**Решения исполнителя плана** (спека молчит, выбор вынужден; ревьюер вправе оспорить):

| # | Решение | Почему | Цена, если неверно |
|---|---|---|---|
| Р-1 | **снято ревизией b1** (B1-5): спека буквально — режим по истории `ACT_MODE` (§6.1, §10); нет привязки — режим не установлен (`Unavailable`), не legacy | — | — |
| Р-2 | Читатель снимка `df-trust` — свой (REST транспортом источников доверия в одном из двух режимов, выбор — защищённой привязкой `/etc/df/trust.toml`: ключа `history_gh_config_dir` нет — анонимно (`AnonTransport()`), ключ есть — read-only токеном истории профиля из этого ключа (`AnonTransport(token=…)`), токен не получен — `Unavailable` без единого запроса и без анонимного чтения; транспорт строит только `load_trust` фабрикой `history_transport` из привязки, извне он не принимается; Task 11c, спека rev 18 §6.2; чтения: `repositories/{id}` → имя, голова закреплённой ветки, first-parent обход, версии `epochs.yaml` по `contents?ref=`); имя репо обязано совпасть с `df_trust_full_name` привязки | steward читает только `approval-policy` и требует default branch (О11), а спека §6.1 п. 1 запрещает следовать за default branch `df-trust` | переименование `df-trust` даёт `Unavailable` до правки привязки человеком (консервативно) |
| Р-3 | Кэш анонимных ответов — только в памяти процесса и только для путей, неизменных по SHA (`commits/{sha}`, `git/trees/{sha}`, `git/blobs/{oid}`, `contents/…?ref={sha}`) | диск пишет агент того же пользователя: подменённый ответ коммита (родители) скрыл бы прямой push, а проверить содержимое коммита по SHA из REST нельзя | без токена истории: при пределе 60 анонимных запросов/ч несколько решений в час → `Unavailable: http`. **С-1 решён владельцем 01.11** (раздел «Решение владельца 01.11 (С-1)»): с ключом привязки — read-only токен истории, 5000 запросов/ч; правило кэша то же (Task 11c) |
| Р-4 | **снято ревизией b1** (B1-4): `HistoryViolation` в диапазоне любой эпохи — событие периметра и отказ (§6.3); от возраста диапазона не зависит | — | — |
| Р-5 | Журнал периметра во флоте владельца — режим разработки: то же хранилище `df.publisher.perimeter` в `$XDG_STATE_HOME/darkfactory/perimeter/root/var/lib/df/perimeter/`; в установке `df` (есть `/opt/df/libexec/df-publish`) — клиент издателя (`df.perimeter_client`); без контекста прогона `df` — `Unavailable`. В событии — настоящий `approval_policy_repository_id` привязки (B1-18) | спека §6.3: «в режиме разработки … в том же формате»; формат — схема этапа 2a; путь `Layout` установщика не настраивается под произвольный каталог | в установке `df` до подключения источника эпох (этап 3 установщика, решение владельца 26.10) все решения — `Unavailable: no_binding`; часть B работает во флоте владельца |
| Р-6 | Свой строгий разбор `trust.toml` в `act_trust` (те же ключи, `O_NOFOLLOW`, владелец root, не пишется группой/всеми); тест сверяет исход с `df.stage2_config.parse_trust` на тех же фикстурах | `act_trust` входит в цепочку `act_premerge` (обвязка мержа), а `df.stage2_config` тянет полдюжины модулей `df/`, не входящих в `_HARNESS_PREFIXES` | два разборщика одного файла; расхождение ловит тест равенства, а не ревью |
| Р-7 | **снято ревизией b1** (B1-4): журнал обязателен и в предпроверке — через `act_journal_wire` (Р-16) | — | — |
| Р-8 | **сужено b1** (B1-9): история узла — по first-parent базы; заявка — коммит, добавивший манифест; голова заявки — `headRefOid` из фактов PR merge-коммита (`prs_containing_commit` + `pr_facts`), а не второй родитель — squash поддержан; объект головы догружается `refs/pull/N/head`. Правка или удаление манифеста/остановки после доставки → история `Unavailable` | спека требует манифест в одобренном head (§9.1 (а) п. 0) и запрещает переписывать доставленное; форма мержа контрактом не ограничена | правка доставленной заявки блокирует историю воркстрима до разбора человеком (fail-closed) |
| Р-9 | **механизм b1, b2** (B1-3, B2-1): узел без события `review/v1` получает статус только через provenance старого одобрения — подпись frontmatter внесена finalize-PR старой заявки **этого воркстрима**, узел — среди файлов её candidate и в его голове `approval_pending`, candidate смёржен той же учёткой в то же время, а вердикт admission до активации — результат **полного старого контракта**: байтовая часть (`legacy_contract_refusal`) и `approve_node._legacy_outcome` — опубликованный снимок, пин `approval-policy-source/v1` из записи заявки в леджере прогона, отпечаток, `authorized_signature` по прежнему allowlist (b3, B3-1); у каждого звена свой код отказа; без записи заявки или пина — отказ, политика недоступна — `unavailable`, никогда `approved` | правило — спека §9.1 (а) п. 3 и :747, без отката на «нет события»; плану остаётся только механизм цепочки | цепочка опирается на SSOT формы веток; переименование формы без миграции даст `legacy_provenance` (fail-closed) |
| Р-10 | **механизм b1** (B1-5): `gate-check` S4/S8 — `--act-mode` по `product_mode` (история режима): `review` → провайдер; `PRE_MIGRATION`/`merge` → `legacy`; удаление записи, откат `review → merge`, нет привязки, недочитанная история → стоп шага | О14 требует результат admission; спека §10 запрещает выводить режим из одного текущего значения | продукт в `merge` с уже доставленной заявкой `review/v1` (невозможно по барьеру §10) упрётся в `GC-ACT-MODE-CONFLICT` — fail-closed |
| Р-11 | **дополнено b1** (B1-15): проверка конверта после мержа finalize — побайтовое равенство с `render_envelope(ожидаемый)` на merge-коммите finalize **и** достижимость этого коммита из базы (`is_ancestor`); `reopen` — наличие и идентичность `StopRecord` в stop-PR, достижимость его merge-коммита | прежняя редакция ссылалась на Task 14 из Task 13; доставка в нужную базу — часть утверждения «конверт в base» | нет |
| Р-12 | Вендоренный `contracts/approval-facts/` — в authority-root и `_HARNESS_PREFIXES` | данные тестов ядра акта защищаются так же, как сами тесты `tests/test_act_*` (заметки части A, строка 10) | PR, правящий только векторы, мержит человек |
| Р-13 | **решено владельцем 01.11 (В-1)**: гибрид — байты всех `approvals/` в хеше DAG как оптимизация; перед каждым no-op §I5 узлы `review/v1` проверяются заново (`Statuses.of`), хеш один no-op не даёт | — | — |
| Р-14 | `--repropose` брифа переезжает из Task 13a в Task 17 | заявка брифа с пином активации (`v2`) появляется только с M2 discovery — переиздавать до неё нечего | до части C выход для брифа — новая заявка через `brief-propose` |
| Р-15 | **усилено b1** (B1-13): барьер считает смёрженный legacy-candidate разобранным, только если каждый узел, отданный candidate на одобрение (`approval_pending` в его голове, состав — независимо от finalize), подписан этим или более поздним актом в базе; пустой состав — живой пункт; пины узла не судит | §10 п. 2 спрашивает «доведена или инвалидирована», а не «узел без долга»; дрейф пинов после законного акта — долг узла | узел с долгом пинов пройдёт барьер и встанет на гейте доставки |
| Р-16 | Предпроверка мержа пишет журнал периметра узким клиентом `act_journal_wire`: в установке `df` — протокол издателя `df/publisher-request/v1` (`sudo -n -u df-publisher df-publish`), в режиме разработки — то же SQLite-хранилище (правило блокировки и вставка `pending` в `BEGIN IMMEDIATE`); совпадение форматов держит тест на одном файле базы | спека §6.3 требует журнал у каждого потребителя; цепочка обвязки мержа не может включать `df/` без смены процесса ревью этих путей (решение владельца, не плана) | второй писатель того же хранилища: расхождение схемы ловит тест равенства с `df.publisher.perimeter`, номер эпохи `pending` назначает полный журнал |
| Р-17 | Идентичность судимого объекта — одна на объект: `RequestIdentity(product, ws_id, request_id)` + пути узлов DAG + байты манифеста в одобренном head; для конверта — все поля `Envelope`; для старого акта — воркстрим, узел в candidate, байты, прежние пины; маркер миграции — `<!-- act-migrated: <product>/<ws_id>/<request_id> -->`. Реестр ведёт ревьюер задачи; мета-тест (`tests/test_act_identity_table.py`, Task 16) проверяет классификацию по правилу «проверка-отказ — место суждения» (детектор формы — перекрёстная проверка, полноту не доказывает), точную причину каждой строки по AST и новые проверки изменённых функций; обход каждой строки — обязательный мутационный проход Task 16 Step 4a | ревизии b1 и b2 нашли места, сверявшие часть компонентов (B1-1/2/13/14/15, B2-1/B2-3) — второй круг одного класса; спека формат маркера не задаёт | маркеры старой формы (`approve-W-K-A` без продукта и воркстрима) барьер не принимает — их ставят заново `--invalidate` |

**Вопросы к спеке** (план не поглощает; список — в отчёте ревизии): С-1 — анонимный REST
(спека §6.2, 60 запросов/ч на IP) против чтения истории обоих источников доверия перед **каждым**
решением: по оценке 20–40 запросов на решение без кэша между процессами; **решено владельцем
01.11** — read-only токен истории (спека rev 18 §6.2; раздел «Решение владельца 01.11 (С-1)»,
Task 11c). С-2 — O11 (ветка
источника политики обязана быть default branch) — новое ограничение, которого нет в §6.1;
поглощается как fail-closed предусловие H1/H2, но в спеку его стоит внести; **решено владельцем
01.11** — внесено в спеку rev 18 §6.1 п. 1 (раздел «Решение владельца 01.11 (С-1)», п. С-2),
новой задачи нет. С-3 — §9.1 (а) п. 4
(«производный статус из проверенного конверта» в каноническом хеше DAG) требует чтений форджи в
`_content_anchor`, который сегодня их не делает; **решено владельцем 01.11 (В-1 ниже)**: хеш
остаётся локальным, проверенный статус спрашивается перед каждым no-op.


### Правки по ревью b1

Ревью `plan-review-b1` (3 blocker, 12 major, 3 minor). Подписанная спека обязательна: план не
решает отступлений. Р-1, Р-4, Р-7 сняты (спека буквально), Р-13 переведено в вопрос владельцу
В-1, Р-8–Р-11 и Р-15 переписаны под спеку; новые Р-16, Р-17 — механизмы, а не отступления.

- **B1-1, B1-2, B1-13, B1-14, B1-15 — один класс**: суждение сверяло часть идентичности объекта.
  Введена одна идентичность на объект (`governance/act_identity.py`, Task 12a Step 5a; Р-17) и
  таблица ниже; на каждый недостающий компонент — отрицательный двойник.
- **B1-3**: legacy-узел — только provenance старого одобрения + admission (`LegacyProbe`, Task 14;
  Р-9); отката на «нет события `review/v1`» нет.
- **B1-4**: журнал обязателен всюду, включая предпроверку (`act_journal_wire`, Р-16);
  `HistoryViolation` любой эпохи — событие периметра (Р-4 снято).
- **B1-5**: режим продукта — `act_trust.product_mode` по истории `ACT_MODE`: `PRE_MIGRATION` (записи
  не было никогда) отличён от удалённой записи, отката `review → merge` и потери привязки
  (`Unavailable`); единая точка `act_status.mode_probe` для гейтов, DAG и создания заявки.
- **B1-6**: С-3 — вопрос владельцу В-1; вариант по умолчанию хеширует все файлы `approvals/`
  (дыры supersede нет), тест «те же байты узла, новое поколение — хеш другой, no-op не скрывает».
- **B1-7, B1-8 — один класс**: смена поверхности `Ops` не доходила до всех мест вызова. Два
  статических обхода: `test_every_gate_check_site_names_act_mode` (Task 11 — все вызовы,
  переопределения и пересылки `gate_check_*`) и `test_new_ops_surface_reached_only_behind_mode_probe`
  (Task 14 — новые методы `Ops` только из `act_*`, чтение истории вне `act_*` — только за
  `mode_probe`).
- **B1-9**: голова заявки — из фактов PR (squash поддержан), тест полного цикла «squash → чтение →
  `--repropose`» (Task 13a).
- **B1-10**: `load_anchor_times` читает все якоря, `load_ranges` — все эпохи с якорем ≤ t;
  интеграционный тест загрузчика с admission перехода в эпоху 2 (Task 12a).
- **B1-11**: производный токен App не самоотчитывается: предпроверка в App-режиме идёт с
  `GH_TOKEN=""`, установки App проверяются у держателя ключа (`app_installations`), прочие
  credentials — как обычно; сквозной тест App → `act_premerge` → `load_trust` (Task 15a).
- **B1-12**: оба списка самоотчёта App — все страницы с проверкой полноты, иначе `Unavailable`;
  HTTP без перенаправлений (`_NoRedirect`).
- **B1-16**: тест JWT проверяет подпись без часов прогона, `iat`/`exp` — отдельно.
- **B1-17**: состав коммита Task 13 перечислен для каждого варианта решения по п. 9 части A.
- **B1-18**: в событии журнала — настоящий `approval_policy_repository_id`; тест полей события.

**Идентичность судимого объекта — классификация по правилу, детектор — перекрёстная проверка**
(ревизии b3–b5). Реестр — `tests/test_act_identity_table.py` (Task 16); его ведёт ревьюер задачи:
детектор формы отказа ловит типовые формы (возврат/присваивание `Unavailable`/`Rejected`/…, `cls(...,
error=…)`, «отказ | None», `raise`), но полноту не доказывает — отказ иной формы вносится руками.
Состав: 224 определений (13 модулей части B и 18 дополнений в существующие
модули) и 15 новых проверок внутри изменённых существующих функций (`MODIFIED_EXISTING`,
ключ `<функция>#<проверка>`); `JUDGMENT` — 135 мест, 258 строк; `NOT_JUDGMENT` — 104.
Строка = компонент × функция проверки × литерал проверки (ровно один в её исходнике) ×
двойник × литерал утверждения (по AST входит в `assert` о результате или `pytest.raises(match=)`).

| Место суждения | Компонент → функция проверки |
|---|---|
| `act_facts._call` | library_exception |
| `act_facts._own` | steward_unavailable |
| `act_anon.AnonTransport.send` | memo_only_sha, relative_get |
| `act_anon._response` | size_limit |
| `act_anon.AnonTransport._fetch` | transport_failure |
| `act_anon.contents_bytes` | blob_oid, absent, unreadable |
| `act_check.check_delivery` | product → `act_check._pinned_policy`, act_head → `act_decision.decide` |
| `act_check.live_policy_refusal` | current_activation |
| `act_check._admission` | mode_after_review, marker_before_bootstrap |
| `act_check._epoch_at` | interepoch_gap |
| `act_check._span` | activation_order |
| `act_check._pinned_policy` | activation_pin, mixed_set |
| `act_check._decide` | approver → `act_decision.decide`, dismissal → `act_decision.decide`, merger → `act_decision.decide`, bytes → `act_decision._delivery_refusal`, content → `act_decision._delivery_refusal` |
| `act_check._legacy_verdict` | legacy_missing |
| `act_trust.parse_binding` | transport, keys, ids |
| `act_trust.load_binding` | owner, absent |
| `act_trust._get` | http_status |
| `act_trust._named` | full_name, public |
| `act_trust._chain` | commit_identity |
| `act_trust._journal_versions` | journal_deleted |
| `act_trust._checked_epochs` | bootstrap_first, transition |
| `act_trust.read_trust_snapshot` | head_named |
| `act_trust.gh_get` | gh_failed, not_json |
| `act_trust._self_report` | push_on_policy, push_on_df_trust, unreadable |
| `act_trust._rulesets` | pull_request_rule |
| `act_trust._rebase` | rebase_true, rebase_hidden |
| `act_trust._register` | violation_event |
| `act_trust._snapshot_for_decision` | blocked, stale |
| `act_trust.load_trust` | violation_wins |
| `act_trust.policy_set` | bytes_vs_activation, actors_file |
| `act_trust.load_ranges` | history_violation, unknown_anchor |
| `act_trust._modes` | unread_set |
| `act_trust.product_mode` | never_entered, removed, rolled_back, prior_epoch |
| `act_trust.current_activation` | open_epoch |
| `act_journal.ForgeEpochs.head` | root |
| `act_journal._status_of` | snapshot_identity, state |
| `act_journal.DevJournal._open` | store |
| `act_journal.DevJournal.status` | stale → `act_journal._status_of`, lost → `act_journal.DevJournal._open` |
| `act_journal.DevJournal.record` | store_refused |
| `act_journal.InstalledJournal.status` | df_run → `act_journal.journal_for` |
| `act_journal.InstalledJournal.record` | refused |
| `act_journal.journal_for` | df_run |
| `act_journal_wire.WireJournal._call` | garbled |
| `act_journal_wire.WireJournal.status` | snapshot_identity |
| `act_journal_wire.WireJournal.record` | refused |
| `act_journal_wire.DevStoreJournal._open` | restore, missing |
| `act_journal_wire.DevStoreJournal.status` | blocking |
| `act_journal_wire.DevStoreJournal.record` | insert_failed |
| `act_journal_wire.journal_for_premerge` | df_run |
| `act_context.delivery_verdict` | not_merged, legacy_only_unmarked |
| `act_identity.manifest_refusal` | product, ws_id, request_id |
| `act_identity.paths_refusal` | node_in_dag, node_path |
| `act_identity.head_manifest_refusal` | in_head, bytes |
| `act_status.pr_for_merge` | exactly_one |
| `act_status._pr_head` | head_fetched |
| `act_status._event` | request:path, request:no_pr, immutable, stop:ws_id, stop:path |
| `act_status.load_history` | base_unread |
| `act_status.Decider._binding` | identity, head_manifest, upstream_pins |
| `act_status.Decider.__call__` | memo_key, memo_request |
| `act_status.Decider.trust` | binding |
| `act_status.Decider._head_hashes` | head_bytes |
| `act_status.Decider._decide` | merge_commit, head_sha |
| `act_status.Decider.legacy` | contract_result |
| `act_status.candidate_identity` | ssot_roundtrip |
| `act_status.legacy_contract_refusal` | pending, bytes, hash_field, pins |
| `act_status.LegacyProbe.__call__` | edit_in_history, signature_commit |
| `act_status.LegacyProbe._finalize_branch` | via_pr, finalize_form |
| `act_status.LegacyProbe._candidate` | ws_id, candidate_exists, candidate_merged, candidate_merger, candidate_time, ledger_record, ledger_candidate, ledger_node |
| `act_status.LegacyProbe._offered` | node_in_candidate |
| `act_status.LegacyProbe._verdict` | admission |
| `act_status.LegacyProbe._verdict.contract` | byte_contract |
| `act_status.Statuses.of` | memo_node |
| `act_status.Statuses.is_review` | no_event |
| `act_status.Statuses._legacy` | debt_first |
| `act_status.Statuses._compute` | stop, upstream, delivery_verdict |
| `act_status.Statuses._envelope` | envelope:node → `act_envelope.envelope_refusal`, envelope:request_id → `act_envelope.envelope_refusal`, envelope:generation → `act_envelope.envelope_refusal`, envelope:manifest_path → `act_envelope.envelope_refusal`, envelope:head_sha → `act_envelope.envelope_refusal`, envelope:self_hash → `act_envelope.envelope_refusal`, envelope:upstream_pins → `act_envelope.envelope_refusal`, envelope:merge_commit → `act_envelope.envelope_refusal`, envelope:review_id → `act_envelope.envelope_refusal`, envelope:policy → `act_envelope.envelope_refusal`, ws_id → `act_envelope.envelope_refusal`, node_bytes → `act_envelope.envelope_refusal`, current_pins → `act_envelope.envelope_refusal`, generation → `act_envelope.envelope_refusal` |
| `act_status._blobs.read` | inputs_unread |
| `act_status.live_policy` | binding |
| `act_status.gate_run` | legacy_mode, snapshot_digest |
| `act_node.creation` | mode_unset, draining, review_v1_off |
| `act_node._manifest` | parseable |
| `act_node._binding_refusal` | identity, upstream_pins |
| `act_node._check_marked` | act_mirror |
| `act_node.route` | merged_unmarked_mode |
| `act_node._open` | mode, live_pin, head, act |
| `act_node._legacy.verdict` | halt |
| `act_node._head_hashes` | head_bytes |
| `act_node._merged` | rejected |
| `act_node._record_act` | recorded_act |
| `act_node.envelope_commit_defect` | paths, bytes |
| `act_node._finalize` | commit_made |
| `act_node._reconcile_finalize` | envelope:node, envelope:request_id, envelope:generation, envelope:manifest_path, envelope:head_sha, envelope:self_hash, envelope:upstream_pins, envelope:merge_commit, envelope:review_id, envelope:policy |
| `act_node._require_in_base` | in_base |
| `act_node.reopen` | none, other_node, other_generation, other_reason, in_base, current_event_after, already_stopped |
| `act_node.repropose` | unknown, completed |
| `act_premerge.request_of_branch` | form |
| `act_premerge._trust` | binding, journal, app_unknown, app_violation |
| `act_premerge._live` | mode_unset, not_review |
| `act_premerge._check` | identity, manifest_present, merger, skew, reread |
| `act_premerge._act` | head_sha, act |
| `act_app_token._pages` | completeness, total_count |
| `act_app_token._token` | token |
| `act_app_token.app_identity` | app, installation |
| `act_app_token.installations_refusal` | trust_repo, second_page |
| `act_barrier._marked` | absent, unparsed, foreign |
| `act_barrier._migrated` | author, edited, identity |
| `act_barrier._signed` | status, signer |
| `act_barrier._candidate_nodes` | pending |
| `act_barrier._legacy_resolved` | finalize_merged, node_in_base, nodes_found |
| `act_barrier._judge` | brief_open, brief_merged, open_unmarked |
| `act_barrier._local_brief_stamp` | run, file |
| `act_barrier._local_items` | live_local, unreadable |
| `act_barrier.live_unmarked` | draining, attestation, pagination |
| `act_barrier.live_review_requests` | product_gaps |
| `act_barrier.invalidate` | human, ws_id |
| `approve_node._legacy_outcome` | old_allowlist, old_pin_missing, old_pin_substituted, old_pin_unavailable, old_pin_superseded, snapshot_published |
| `approval_ledger.successor_of` | single_successor |
| `act_barrier._identity` | request_form |
| `ops.RealOps.first_parent_changes` | git_failed |
| `approve_node._propose#act_incompatible` | act |
| `approve_node._propose#other_pin` | policy_pin |
| `approve_node._propose#status_unavailable` | status |
| `approve_node._propose#successor_joins_live` | successor |
| `approve_node.read_dag_state#mode_unset` | mode |
| `approve_node.read_dag_state#status_unavailable` | status |
| `approve_node.read_dag_state#review_debt` | debt |
| `approve_node._require_upstream_ready#mode_unset` | mode |
| `approve_node._require_upstream_ready#upstream_unavailable` | status |
| `approve_node._require_upstream_ready#upstream_not_approved` | debt |
| `approve_node._try_agent_merge#form` | form |
| `runner._step_gate#act_mode_unset` | mode |
| `runner._step_s8#act_mode_unset` | mode |
| `act_envelope.envelope_refusal#bytes` | bytes |
| `act_envelope.envelope_refusal#pins` | pins |
| `ops.RealOps.pr_body_fact` | gh_failed |

**Правило смены поверхности `Ops`** (B1-7/B1-8): каждая задача, меняющая сигнатуру или добавляющая
метод `Ops`, несёт статический обход всех мест вызова, переопределения и пересылки по
`governance/` и `tests/` — список мест сверяется с деревом при исполнении задачи, а не с
памятью плана.

**В-1 — решено владельцем 01.11** (ревизия b1, B1-6; вопрос к спеке С-3) — канонический хеш DAG
для узлов `review/v1`. Слова владельца: «В-1 — локальный хеш содержимого допустим как оптимизация,
но он не заменяет проверку одобрения. Перед успешным no-op для review/v1 проверяется актуальный
допуск.» Принят гибрид (Task 14): вариант (б) — хеш как оптимизация — плюс свежая проверка
`Statuses.of` узлов `review/v1` перед каждым успехом без полного пути (`act_status.noop_refusal`). История
вопроса (до решения). Спека §9.1 (а) п. 4: «в каноническую проекцию входит производный статус
из проверенного конверта».

- **(а) Спека буквально** — `_content_anchor` получает проверенный статус `Statuses.of` и
  подставляет его вместо `status` узла `review/v1`. Следствия: доверенный контекст и чтения форджи
  во всех четырёх местах `_content_anchor` (сегодня без форджи), каждое no-op §I5 тратит
  анонимную квоту (С-1; после решения 01.11 — квоту токена истории, если он настроен, Task 11c);
  недоступность форджи — `Unavailable` вместо no-op.
- **(б) Байты всех файлов `approvals/` воркстрима** (конверты, манифесты, остановки) в манифесте
  хеша. Следствия: без форджи; любое новое поколение, остановка, supersede меняют хеш — no-op не
  скроет изменение допуска; хеш меняется и от байтов, не влияющих на допуск (лишний перегон §I5,
  не пропуск).
- **Рекомендация исполнителя была (б)** — та же гарантия «изменение допуска не скрыто», без
  чтений форджи в `_content_anchor`; цена — лишние перегоны. Шаг `_canonical_dag_hash` Task 14 был помечен
  «ждёт решения владельца В-1»; листинг — вариант (б), тест
  `test_canonical_hash_sees_new_generation_and_stops` проходит при обоих вариантах.

### Правки по ревью b2

Ревью `plan-review-b2` (1 blocker, 3 major); 15 из 18 находок b1 закрыты, B1-3, B1-6, B1-9 —
частично. B2-1 и B2-3 — второй круг класса «идентичность судимого объекта», поэтому чинится
механизм, а не экземпляры: таблица Р-17 перестроена по листингам (каждая функция с положительным
суждением × каждый компонент → двойник через этот вход), данные таблицы — в мета-тесте
`tests/test_act_identity_table.py` (Task 16), который падает на строке без теста, на исчезнувшем
тесте и на новой функции с признаком положительного суждения вне таблицы.

- **B2-1**: `LegacyProbe` связывает старый акт с воркстримом прогона (форма ветки — круговой
  подстановкой в SSOT), с узлом (среди файлов candidate, в его голове `approval_pending`) и с его
  байтами и пинами (`legacy_contract_refusal`); `Decider.legacy(pr, contract)` отдаёт результат
  этой проверки — безусловного `LegacyAccepted` в коде нет. Двойники: чужой воркстрим, узел не из
  candidate, узел не на одобрении, изменённые байты, подпись под другими пинами, контракт через
  `Decider.legacy`.
- **B2-2**: каталог `approvals/` для хеша — из идентичности воркстрима путём производителя
  (`envelope_path(ws_id, …)`), не из `bundle_dir`; двойники `bundle_dir=spec`, чужой воркстрим и
  корневой `approvals/`; величина с `approvals/` — эпоха `v3`, несопоставимая с `v2`, поэтому
  `_delivered_content_anchor` без `approvals/` даёт `comparison: unavailable`, а не ложное
  совпадение.
- **B2-3**: добавлены места `_event` (заявка и остановка), память `Decider` и `Statuses.of`,
  `Statuses._envelope` по всем полям конверта, `envelope_commit_defect`, `_reconcile_finalize` по
  всем полям конверта, `reopen` (чужой узел, поколение, байты, вне базы, плюс проверка действующего
  события перечитанной историей), предпроверка (`product`, голова фактов); таблица выше.
- **B2-4**: сквозной squash-тест синхронизирует target перед чтением истории; отдельный тест
  догрузки squash-головы, которой нет в клоне (`git clone --no-local`), и её двойник без
  `refs/pull/N/head` — `Unavailable` (`_pr_head` после неудачной догрузки больше не отдаёт
  голову).
- **Побочное замечание**: `_surface_offenders` проверяет, что `mode_probe` вызван оператором
  верхнего уровня функции (не в ветке/цикле/`try`/лямбде) и **до** первого чтения истории;
  почему этого достаточно — в Task 14.
- `tests/harness_trust.py` уже снимает ведущие присваивания окружения — оговорка о правке
  защищённого файла в Task 15a снята.

### Правки по ревью b3

Ревью `plan-review-b3` (1 blocker, 3 major); B1-3, B2-1, B2-3 — частично. Третий круг одного
класса — меняется механизм: реестр мест суждения стал исчерпывающим, двойники — точными.

- **B3-1**: старый путь исполняет ПОЛНЫЙ старый контракт тем же кодом, что старый путь
  (`approve_node._legacy_outcome` выделен в Task 14 Step 3a — раньше `LegacyProbe`, Task 13 его
  потребляет): опубликованный снимок, пин `approval-policy-source/v1` из записи заявки в леджере
  прогона (запись обязана называть этот candidate и этот узел), отпечаток, `authorized_signature`
  мержера по прежнему allowlist. Сквозные двойники через `LegacyProbe → Decider.legacy →
  delivery_verdict → check_delivery`: мержер вне прежнего allowlist, пин отсутствует / подменён /
  недоступен / заменён новой версией, снимок не опубликован, записи заявки нет / про другой
  candidate / без узла. Следствие (вопрос не плана, а миграции): узел, одобренный на другой
  установке или чей леджер утрачен, в продукте `review` получает `not_approved:legacy_no_ledger_record`
  и переодобряется заявкой `review/v1`.
- **B3-2**: имя-маркеры заменены исчерпывающей классификацией: все функции и методы верхнего уровня
  13 модулей части B — ровно в `JUDGMENT` (с компонентами и двойниками) или `NOT_JUDGMENT` (с
  причиной); маркеры — лишь перекрёстная проверка. Добавлены места, названные ревью
  (`Decider._binding`, `legacy_contract_refusal`, `_require_in_base`, `act_premerge._check`,
  `act_barrier._signed`, `_judge`, `_local_brief_stamp`, `live_unmarked`) и все остальные места
  суждения в `act_check`, `act_trust`, журналах, `act_context`, `act_identity`, `act_anon`,
  `act_app_token`; новые двойники там, где их не было (`current_activation`,
  `candidate_identity`, `_manifest`, `_local_items`, `_local_brief_stamp`, `_signed` по статусу).
- **B3-3**: у каждой проверки старого акта — свой код (`act_status.NO_EDIT … PINS` и коды
  `_legacy_outcome`), двойник проверяет ровно его, вход построен так, что все предыдущие проверки
  проходят. `_binding_refusal`: компонент подменяется в самом манифесте головы при верных
  зеркалах — отказ именно `_binding_refusal` с причиной компонента. Двойники конверта и барьера
  проверяют точную причину. `reopen`: двойник «новое событие узла между первым чтением и проверкой
  остановки».
- **B3-4**: `reopen[other_reason]` и соседние подмены лежат в ОТКРЫТОМ stop-PR, который мержит сам
  `reopen` после первого чтения истории; уже доставленная остановка — отдельный тест
  идемпотентности.
- **Мутационный проход** (scratch): legacy-путь — 24 мутации, барьер — 25; отключение каждой
  проверки убивается хотя бы одним двойником (49/49). Выжившие в первом проходе закрыты:
  `finalize` открыт/закрыт без мержа (новый двойник), `brief-PR OPEN` (точный текст пункта);
  недостижимая проверка учётки старого кода снята (её гарантирует ранняя проверка мержера).

### Правки по ревью b4

Ревью `plan-review-b4` (4 major; B2-3, B3-2, B3-3 — частично, весь класс «классификация и
двойники»). Оставшиеся свойства класса сделаны механическими:

- **B3-5 (состав)**: реестр собирается по итоговому коду всех задач, включая 13a
  (`_create_successor`, `repropose`) и определения, которые часть B добавляет в существующие
  модули (`EXISTING_ADDITIONS`: `ops._gate_check_exe`, `_act_args`, `Ops`/`RealOps` ×
  `gate_snapshot_digest`/`first_parent_changes`/`pr_body_fact`; `approval_ledger.record_review_act`,
  `audit_events`, `record_audit`, `successor_of`; `act_decision.find_act`, `merger_allowed`;
  `act_envelope.expected_envelope`; `act_settings.config_home`; `approve_node._legacy_outcome`,
  `_review_debt`). Обход AST рекурсивный, с квалифицированными именами; делегирующие замыкания
  классифицированы (`LegacyProbe._verdict.contract`, `_blobs.read`, `act_node._legacy.verdict` —
  места суждения; `app_installations.check`, `real_deps.journal`,
  `InstalledJournal.status.reload`, `_gate_statuses.text_of` — нет, с причиной). Изменённые
  существующие функции (не дополнения) судят через места суждения части B, которые зовут:
  `approve_node._advance`/`_propose`/`read_dag_state`/`_require_upstream_ready` → `act_node.route`,
  `mode_probe`, `Statuses`; `_try_agent_merge(form=)` → `envelope_commit_defect`;
  `_reconcile_candidate` → `_legacy_outcome`; `runner._step_gate`/`_step_s8` → `gate_run`;
  `task_bridge._canonical_dag_hash`/`_content_anchor` — хеш (В-1), не суждение;
  `act_envelope.envelope_refusal` — проверки конверта (двойники `Statuses._envelope`).
- **B3-6 (правило классификации)**: `NOT_JUDGMENT` допустим, только если в собственном теле нет
  условного пути к отказу (`raise`, `return Unavailable/Rejected/Violation/_Stop/LegacyHalt`,
  `_unresolved`, `_invalidate`, ответ `unavailable:`/`not_approved:`, условный `return False`,
  форма «отказ | None»); мета-тест проверяет это на каждом определении. Аудит прежнего
  `NOT_JUDGMENT` по правилу перенёс в `JUDGMENT` 25 определений: `_status_of`, `_record_act`, `_open`
  (названы ревью) и `act_facts._call`/`_own`, `act_check._epoch_at`/`_span`, `act_trust._get`/
  `gh_get`/`_register`/`_modes`, `act_journal.ForgeEpochs.head`/`DevJournal._open`/`record`/
  `InstalledJournal.record`, `act_journal_wire.WireJournal._call`/`record`/
  `DevStoreJournal._open`/`record`, `act_status.load_history`/`Decider._head_hashes`,
  `act_node._head_hashes`/`_finalize`, `act_app_token._token`, `act_barrier._identity`; впервые
  видимые рекурсивному обходу и составу — `_blobs.read`, `_legacy.verdict`,
  `LegacyProbe._verdict.contract`, `RealOps.first_parent_changes`/`pr_body_fact`, `successor_of`,
  `_legacy_outcome`, `repropose`.
  Ложные срабатывания — `GUARD_EXEMPT` (3, с причиной).
- **B3-7/B3-8 (точная причина)**: у каждой строки — литерал проверки (в исходнике её функции ровно
  один раз) и литерал утверждения двойника; мета-тест проверяет оба и отвергает утверждения,
  не называющие причину (только тип/код выхода). Проверки, делившие одну причину, разделены:
  `envelope_refusal` (байты / пины), `act_barrier._marked`/`_migrated`/`_signed`/
  `_local_brief_stamp` (`Unmarked(причина)`, коды причин), `Decider._binding` (`identity` /
  `copy`). Конкретные случаи ревью: `upstream_pins` — зеркало леджера согласовано с манифестом,
  срабатывает только `_binding_refusal`; `_act × head_sha` — approve есть на `--head`, отказ только
  проверкой головы; `app_identity` — остальные ответы успешны, причина `GET /app: HTTP 403`;
  `_require_in_base × stop` снята (reopen зовёт `_in_base` сам; строка `reopen × in_base`).
  `request_of_branch` — недостижимая проверка суффикса finalize снята (полное совпадение с
  шаблоном её покрывает). Двойники `act_premerge` проверяют причину в stderr.
- **Мутационный проход** (scratch, по ВСЕМ строкам с исполнимым двойником — 200 из 240; 40 строк
  `test_act_node.py`/`test_act_repropose.py` требуют стенда `World`): отключение проверки (условие
  → `False`, для «принимающих» проверок и замыканий — заданная замена) поймано утверждением
  двойника в 164 строках (138 автоматически, 26 заданной заменой); в 36 строках отключение
  условия роняет производственный код исключением (проверка несущая) или неприменимо
  (обработчик исключения) — для них подмена причины отказа (оператор O3) поймана утверждением
  двойника во всех 36. Выживших 0, необработанных ошибок 0. Байткод между мутантами
  очищается (`-B`, `__pycache__`): в проходе b3 этого не было, его числа могли быть завышены.
- Ссылка на Task 13 в пояснении Task 12 исправлена на Task 14 Step 3a.

### Правки по ревью b5

Ревью `plan-review-b5` (3 major, 1 minor; B2-3, B3-2, B3-3, B3-6, B3-8 — частично). Гарантии
мета-теста приведены к тому, что он действительно проверяет:

- **B3-9**: в `JUDGMENT` — `Decider.trust`, `act_anon._response` и `AnonTransport._fetch` (с
  двойниками); новые проверки внутри изменённых существующих функций — `MODIFIED_EXISTING` с
  ключами `<функция>#<проверка>`: `_propose` (несовместимый акт, другой пин, неустановленный
  статус, преемник при живой заявке), `read_dag_state` (режим, статус, долг review/v1),
  `_require_upstream_ready` (режим, upstream не установлен / не одобрен), `_try_agent_merge#form`,
  `runner._step_gate`/`_step_s8` (режим не установлен), `envelope_refusal` (байты / пины); прочие
  изменённые функции — с причиной «новой проверки нет». Фрагменты `_require_upstream_ready`,
  `_step_s8` и проверки преемника записаны кодом (литералы закреплены). Детектор дополнен
  присвоенными отказами и `cls(..., error=…)`; текст плана и докстринг реестра больше не
  утверждают полноту — детектор перекрёстный, реестр ведёт ревьюер задачи.
- **B3-10**: связь причины с утверждением — по AST: литерал или связанное с ним имя
  (присваивание, параметр `parametrize`, константа модуля, производные имена) входит в `assert`
  о результате либо в `pytest.raises(match=)`; самопроверка механизма — 8 двойников
  (`test_reason_link_mechanism`: литерал только в присваивании, только в комментарии, константа
  без использования, только код выхода — отказ; в `assert`, через имя, `match=` — принято).
- **B3-11**: двойники проверяют тип результата до обращения к полям (`ForgeEpochs.head`,
  `_rebase`, прямые двойники `_call`/`_own`); для несущих проверок — операторы обхода с
  корректными данными (`BYPASS` в `tests/act_mutation.py`), O3 засчитывается только как проверка
  причины и строку не закрывает; недостижимая проверка `check_delivery` «не смёржен» (её
  дублирует `decide`) снята со строк; production-код ради мутаций не менялся.
- **B3-12**: Task 16 Step 4a — обязательный проход `tests/act_mutation.py` по ВСЕМ строкам, включая
  53 строки на стенде `World` и раннере; результат коммитится в рабочее пространство SDD
  (`docs/superpowers/plans/2026-10-12-approval-act-review.runs/`); `survived`/`error`/`n/a`/непрогнанная
  строка блокируют коммит, исключение — только запись `RULED` с причиной.
- **Мутационный проход при ревизии** (scratch, -B): исполнимых строк 205 из 258 — 201 закрыто
  обходом (144 условие → `False`, 57 `BYPASS`), 4 в `RULED` (хранилище журнала недоступно /
  утрачено, нет файла блокировки, PR не смёржен — продолжить без данных нечем); выживших и
  ошибок нет. 53 строки (`World`, раннер, проверки изменённых функций) — Step 4a.

### Правки по ревью b6

Ревью `plan-review-b6` (5 major, все в диффе b5). Только эти пять:

- **B6-1**: фрагмент `_require_upstream_ready` передаёт локальный `text` функции
  (`_review_debt(up, text, verdict)`), а не `texts[up]` из `read_dag_state`. Проверено
  подстановкой фрагмента в настоящую функцию части A (scratch): режим не установлен, upstream
  `unavailable:`, upstream не одобрен (причина долга `review_v1`) и `pre_migration` (прежнее
  поведение части A) — 4 passed.
- **B6-2**: двойники `act_incompatible` / `other_pin` строятся на двух узлах одного шага
  (`design` и `acceptance` — оба ждут `behaviour-spec`): `_level_three` одобряет
  charter → requirements → behaviour-spec на `pre_migration`, затем режим `review` и статусы
  трёх узлов «одобрен»; живая заявка над `design` создаётся одним актом/активацией, `acceptance`
  присоединяется другим — отказ guard'а, без пропуска. Двойник преемника вызывает одобрение
  `acceptance` с `predecessor=…`, над которым живой заявки нет, — до `_advance` дело не доходит,
  отказывает новый guard. Production-маршрутизация не менялась.
- **B6-3**: `asserts_reason` анализирует только `Assert.test` (сообщение `Assert.msg`
  исключено); `assert "ЛИТЕРАЛ"` отвергается; литерал должен сравниваться с выражением,
  производным от результата (имя из вызова, атрибут/подписка/вызов над ним; `in`/`==`,
  `startswith`/`endswith` над производным, `raises(match=)`). Самопроверки дополнены тремя
  отрицательными: причина только в сообщении `assert`, причина через имя только в сообщении,
  тавтология `assert "ПРИЧИНА"`. Двойники, державшиеся на сообщении или на литерале без
  сравнения с результатом, переписаны (`memo_only_sha`, `memo_request`, `head_sha`,
  `ssot_roundtrip`, `stale`, `violation_wins`, маркер барьера).
- **B6-4**: `tests/act_mutation.py --check` — применимость операторов ко ВСЕМ строкам без
  двойников; Step 4a начинается с неё. При ревизии **259/259**: 249 — по коду scratch (186
  условие → `False`, 58 `BYPASS`, 5 `RULED`), 10 строк `<функция>#<проверка>` — по AST
  фрагментов плана (в scratch хуков части B нет; 9 условие → `False`, 1 `BYPASS`). Поиск
  функции проверки — по всем одноимённым определениям, а не по первому (протокол и
  реализация с одним именем; в scratch это дало ложное `n/a` для `RealOps.pr_body_fact`). Найдено и исправлено: `act_node._manifest:
  parseable` (`except RequestError`) — `BYPASS` «нечитаемый манифест = манифеста нет»
  (`return None`): двойник ждёт `invalidated` с причиной «манифест … не читается»;
  `_try_agent_merge#form` (IfExp `form() if form is not None`, литерал не в условии) — `BYPASS`
  `defect = None` (дефекта формы нет → агентский мерж).
- **B6-5**: строка `check_delivery × not_merged` возвращена (двойник
  `test_not_merged_waits`: `check_delivery(context(merged=False)) == WaitingDelivery()`).
  Отключение guard'а не доходит до `decide`: `parse_ts(None)` падает раньше — обход с
  корректными данными подменил бы сам факт мержа, поэтому строка в `RULED` с этой причиной.
  Прежнее объяснение b5 («недостижима, дублируется `decide`») было неверным.
- **Мутации затронутых строк** (scratch, -B): 11 закрыто (9 условие → `False`, 2 `BYPASS`),
  `not_merged` — `ruled`; `act_node._manifest:parseable`, `_try_agent_merge#form` и строки
  раннера/`_propose` — на стенде `World` / с хуками части B, прогон — Step 4a.

### Правка по ревью b7

Ревью `plan-review-b7` (B6-1/2/4/5 закрыты, B6-3 частично; 1 major):

- **B7-1**: `asserts_reason` отслеживает происхождение сторон. Сравнение засчитывается, только
  если одна сторона укоренена (база цепочки `Name`/`Attribute`/`Subscript`/`Call`) в наблюдаемом
  значении — локальном результате вычисления, фикстуре или сборщике (`seen = []`), — а другая
  содержит литерал или связанное с ним имя. Стороны из одних ожиданий (литерал, имена,
  связанные с ним без вычисления, константы модуля, локальные данные из литералов, `dict(…)`)
  отвергаются. Конъюнкты — только через `and`; `or`, `not`, IfExp и прочие формы не
  засчитываются (fail closed); `!=`/`not in`/`is not` — только когда литерал реестра
  охватывает всё сравнение с оператором. Самопроверки: 20 (было 11) — добавлены оба
  контрпримера Codex (`expected["reason"] == expected["reason"]`,
  `assert True or got.reason == "ПРИЧИНА"`), `!=`, `not`, IfExp, `dict(…)`-ожидание,
  константа против литерала; положительные — конъюнкт `and`, `startswith`. Переписан один
  двойник: `test_read_dag_state_review_debt` (генератор в `any(...)` — неподдерживаемая форма →
  явный отбор долгов и сравнение причины).

### Решения владельца 01.11

Слова владельца (01.11), дословно:

> «П. 9 — Б, с единым вычислением состояния истории. В-1 — локальный хеш содержимого допустим
> как оптимизация, но он не заменяет проверку одобрения. Перед успешным no-op для review/v1
> проверяется актуальный допуск.»

- **П. 9 заметок части A → вариант (Б)** (Task 13 Step 8 — из «ждёт решения» в конкретный шаг):
  `act_envelope.node_history(history, node) -> NodeHistory(current, generation, top, ignored)` —
  один проход по записям узла; заявка с поколением не больше действующего и остановка не
  действующего поколения (или без принятой заявки) игнорируются с причиной, не становятся
  событием и не сдвигают поколение. `current_event`, `next_generation`, `envelope_refusal`,
  `Statuses` (`of`, `is_review`, `_stopped`), `act_node.reopen`, `act_node.generation_of` выводят
  своё из него; `generation_refusal` удалён. Тесты части A `test_non_monotonic_generation_refused`
  → `test_non_monotonic_request_ignored`, `test_stop_of_other_generation_refused` →
  `test_stop_of_other_generation_is_noop`; новые — остановка без заявки, заявка `g+1` после
  проигнорированной записи, согласие потребителей (`test_history_consumers_agree`: 10 историй,
  `test_statuses_agree_with_node_history`: 5), остановка не делает узел старого пути `review`;
  Task 14-тест `test_ignored_record_then_next_generation_approves`. Текст варианта (А) — только
  как история выбора.
- **В-1 → гибрид** (Task 14, шаг `_canonical_dag_hash` — из «ждёт решения» в конкретный):
  байты всех `approvals/` воркстрима в хеше DAG остаются оптимизацией; перед бесследным no-op
  §I5 заново спрашивается актуальный допуск. Тесты: хеш тот же + узел одобрен → no-op; хеш тот
  же + остановка доставлена / ревью отозвано / доверенный контекст недоступен / режим не
  установлен → no-op нет, вызван гейт; продукт до миграции — статусы не читаются.
- **Правки по d1** (ревью `plan-review-d1`, 2 major одного класса — «ещё один успешный выход без
  свежей проверки допуска»; второй экземпляр класса → перечень, а не заплатка). Правило владельца
  «перед успешным no-op для review/v1 проверяется актуальный допуск» применено к **любому
  успешному раннему выходу**: no-op по хешу, повтор шага по сохранённому `completed`, переход
  волны по завершённой заявке, возврат уже доставленного PR, повтор доставки из намерения,
  no-op предложения по frontmatter. Одна точка проверки — `act_status.noop_refusal(state, ops,
  dag, nodes)` (Task 14): каждый названный узел с событием `review/v1` — `approved` по
  `statuses_at_base`; режим не установлен / не `approved` / `unavailable:` — успеха нет (fail
  closed); продукт не в `review` — прежние правила, статусы не читаются (квота С-1 — по одному
  чтению статусов на каждый такой выход продукта в `review`). D1-1: `_propose` отказывает при
  `Unavailable` режима сразу после `mode_probe`, до любого no-op. D1-2: сохранённый зелёный S4/S8
  при неподтверждённом допуске исполняется заново.

  Перечень успешных ранних выходов в путях, которые трогает часть B (`runner.py`,
  `task_bridge.py`, `approve_node.py`, листинги `act_node.py`; сверено по master `a16e305`):

  | # | Место | Что пропускает | Проверка допуска | Тест | Статус прогона при отказе |
  |---|---|---|---|---|---|
  | 1 | `task_bridge.deliver_superseded`, no-op §I5 (равный `content_anchor`) | переиздание и гейт §I12 | `_admission_refusal` до no-op; не подтверждено — гейт §I12 по прежним правилам | `test_review_noop_with_fresh_approval`, `test_review_noop_refused_without_fresh_approval[stop_delivered/act_revoked/trust_unavailable]`, `…_when_mode_unset` | не меняется (прогон `completed`; отказ — гейт §I12 доставки, не шаг прогона) |
  | 2 | `deliver_superseded`, повтор `--replace-revision 1` | новую ревизию, гейт | `_require_admission` перед `return repeated` | `test_stored_success_requires_admission[v1_replacement]` | не меняется (`completed`): отказ относится к доставке, статус прогона её не утверждает |
  | 3 | `deliver_superseded`, `return_pr` (ревизия `completed`, PR открыт) | новую ревизию, гейт | `_require_admission` перед возвратом | `…[returned_pr]` | то же |
  | 4 | `deliver_superseded`, `complete` (PR ревизии вмержен) | гейт | то же | `…[returned_merged]` | то же |
  | 5 | `deliver_superseded`, `continue` с открытым PR | гейт | то же | `…[returned_open]` | то же |
  | 6 | `deliver_superseded`, повтор доставки из намерения (`started`, PR нет) | гейт §I12 (судился при записи намерения) | `_require_admission` до `_recover_commit` (до эффекта) | `…[redo_intent]` | то же |
  | 7 | `deliver_superseded`, открытая v1 | новую ревизию, гейт | `_require_admission` перед возвратом | `…[returned_v1]` | то же |
  | 8 | `task_bridge.deliver_for_run`, `op completed` (вкл. ответ замены v1) | гейт §I12 | `_require_admission` после проверки `pr` | `…[for_run_completed]`, `test_for_run_completed_returns_pr_with_admission` | то же |
  | 9 | `deliver_for_run`, принят существующий PR по ветке | гейт §I12 | `_require_admission` перед `return existing` | `…[for_run_adopted]` | то же |
  | 10 | `runner._step_gate`, сохранённый `completed` S4 | гейт steward S4 | `noop_refusal` по узлам уровней ниже волны; нет — гейт заново | `test_s4_completed_gate_rerun_without_admission[stop_delivered/admission_unavailable]`, `test_s4_completed_gate_kept_with_admission` | `running` уже (S4 исполняется только внутри `advance` при `running`); `op_start` пишет состояние до гейта |
  | 11 | `runner._step_s8`, сохранённый `completed`/`exit=0` | authoritative-гейт S8 | `noop_refusal` по всем узлам; нет — гейт заново | `test_s8_completed_gate_rerun_without_admission[…]`, `test_s8_completed_gate_kept_with_admission` | **`running` — сохраняется до чекаута и гейта (d2, D2-2)**; прежде оставался `completed` |
  | 12 | `runner._next_wave` (resume по завершённой заявке ~1058, два выхода `_finalize_wave`) | допуск узлов волны перед следующей | `noop_refusal` по узлам волны; нет — `stopped_review` | `test_next_wave_needs_admission[…]` | `stopped_review` — `_stop_with_comment` сохраняет статус первым |
  | 13 | `approve_node._propose`, режим `Unavailable` | любой no-op | отказ сразу после `mode_probe` (D1-1) | `test_frontmatter_noop_refused_when_mode_unavailable` | не применимо (CLI); в раннере — `_finalize_wave` → `stopped_review`/`stopped_stale` |
  | 14 | `approve_node._propose`, no-op по frontmatter в продукте `review` (узел без события) | provenance старого акта (§9.1 (а) п. 3) | `Statuses.of` — не `approved` → новая заявка; `unavailable:` → отказ | `test_frontmatter_noop_in_review_needs_status` | то же |
  | 15 | `approve_node._propose`, no-op узла `review/v1` | — | уже `Statuses.of == approved` (Task 13) | `test_review_noop_needs_current_status` | то же |
  | 16 | `approve_node.propose_from_source` | — | успешного выхода без записи нет; `creation` отказывает на `Unavailable` до записи | — | — |
  | 17 | `approve_node` старые завершения finalize (`_reconcile_finalize`, «уже в base») | — | только заявки без маркера; в продукте `review` `route` их до старого кода не пускает | — | — |
  | 18 | `act_node._reconcile_finalize` — «узлы одобрены» после мержа finalize (гонка: остановка доставлена между актом и мержем) | объявление одобрения | `noop_refusal` по узлам заявки после сверки конверта: заявка завершается, но успеха нет — `RuntimeError` с фактическим состоянием | `test_stop_between_act_and_finalize_merge_not_reported_approved` | заявка `completed`; в раннере `_finalize_wave._approve` → `stopped_stale` (нетерминальный) |
  | 19 | `act_node.reopen` «остановка уже доставлена», `repropose` завершённой | — | успех — отнятие допуска / отказ, не одобрение | — | — |
  | 20 | прочие `completed`-выходы раннера (`_step_wave_branch`, `_step_materialize_brief`, `_step_authoring`, `_step_commit`, `_step_edge`, `_step_candidate`, интервью), `advance` при `merge completed` | — | допуска не судят; следующий судящий шаг — №10–12 | — | — |
  | 21 | `task_bridge.deliver_approve` | — | штамп tasks-спеки, узлов бандла не судит (§I12, таблица) | — | — |

  Строки 1–14 и 18 — с проверкой (15 мест; 12 новых в d1, строка 18 — решением team-lead о
  гонке stop/finalize), остальные 6 — разобраны и проверки не требуют (причина в строке).
- **Правки по d2** (ревью `plan-review-d2`): D2-1 — проверка допуска видит **свежую базу**: в
  одной точке, `act_status.noop_refusal`, перед `statuses_at_base` — `fresh_base` (`git fetch
  origin <base>` → снимок `origin/<base>`, `statuses_at_base(..., ref=…)`); сбой обновления —
  отказ («свежая база: …»), локальная, возможно устаревшая, ветка не читается. Тесты: локальная
  база «approved», на origin доставлена остановка → успеха нет для S4, S8, `_next_wave`; `fetch`
  не удался → успеха нет (те же три места). D2-2 — S8 при утраченном допуске сохраняет
  нетерминальный `running` до чекаута и гейта; регрессия через `advance`: чекаут падает после
  обнаружения — прогон не `completed`. Колонка «Статус прогона при отказе» выше: утечка
  терминального статуса была только в S8 (строка 11); в мосту прогон остаётся `completed`
  намеренно — отказ относится к доставке, а не к шагу прогона.
- **Правки по d3** (ревью `plan-review-d3`; второй круг класса «свежий снимок — не свежая база
  продукта» → сменён механизм): `act_status.fresh_base` возвращает **SHA**, а не имя ref.
  Источник — `Ops.fetch_snapshot(target_dir, repo_slug, branch)`: `origin` обязан называть ровно
  `repo_slug` (после `insteadOf`; отстающий fork / чужой репозиторий — отказ), явный refspec
  `+refs/heads/<base>:refs/act/fresh/<base>` (не зависит от `remote.origin.fetch`), значение —
  SHA полученного объекта; URL в диагностику не попадает. Этот SHA — **единственный снимок** всех
  чтений проверки: `_statuses(…, snapshot=)` передаёт его в `load_history` и `LegacyProbe`
  (прежде там оставался локальный `state.base_ref`), `statuses_at_base(…, ref=)` — в байты и
  входы узлов. Сквозные тесты на настоящем git без подмены `statuses_at_base`: продукт с
  доставленной остановкой и устаревший клон — `noop_refusal` видит остановку, а сама
  локальная база дала бы «approved»; нестандартный `remote.origin.fetch` — снимок свежий;
  `origin` → fork — отказ; продукт недоступен / `origin` нет — отказ; запись refs —
  история и provenance читаются только SHA снимка; S4, S8, `_next_wave` на том же стенде
  успеха не дают.
- **Правки по d4** (ревью `plan-review-d4`; упрощение вместо разбора `origin`):
  - источник снимка — `RealOps._snapshot_url(repo_slug)` = `https://github.com/<repo_slug>.git`;
    `origin` клона не читается и не разбирается; `repo_slug` обязан иметь форму `owner/repo`
    (локальный путь, `file://`, `..`, опция — отказ без запуска git); переписывание URL через
    `url.*.insteadOf` (`git ls-remote --get-url`) — отказ; локальный источник — только
    переопределение `_snapshot_url` в тестовом наследнике (`ProductOps`), ни конфигурация, ни
    окружение его не задают;
  - `act_status.fresh_base` — контекстный менеджер: уникальный ref вызова
    `refs/act/fresh/<uuid>` держит объект до конца всех чтений проверки (`with` в
    `noop_refusal`) и удаляется в `finally` (`Ops.drop_ref`);
  - `git fetch --quiet --no-tags --no-write-fetch-head --refmap= <url> +refs/heads/<base>:<ref>`;
    посторонние ветки (в т. ч. `local-keep` из mapping `remote.origin.fetch`) и tracking-ref'ы
    не тронуты, `FETCH_HEAD` не пишется — тест; `--refmap=` при источнике-URL избыточен (git не
    применяет `remote.*.fetch` к fetch по URL, проверено) — строка реестра в `RULED`;
  - тест записи чтений расширен: история, provenance, байты узла, конверт и прямые входы — один
    SHA снимка, прочие чтения — только по SHA коммитов истории;
  - чередующиеся вызовы (один — из отстающего источника) держат свои ref'ы и читают свои SHA.
- **Правки по d5** (ревью `plan-review-d5`: TOCTOU `insteadOf` и учётные данные приватного
  продукта; решение team-lead — изолированный репозиторий снимка):
  - `Ops.snapshot_open` создаёт временный bare-репозиторий (`mkdtemp`, удаляется в `finally`
    `fresh_base`, в т. ч. при исключении) с `objects/info/alternates` на объекты продукта — в
    репозиторий продукта не пишется ничего (ни ref'ов, ни `FETCH_HEAD`, ни конфигурации);
  - каждый вызов git снимка — `--git-dir=<временный>` и окружение из allowlist
    (`PATH`, `HOME`, локаль, `TMPDIR`, сертификаты) + `GIT_CONFIG_NOSYSTEM=1`,
    `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_TERMINAL_PROMPT=0`, пустой `GIT_ASKPASS`; ни одной
    `GIT_*` извне; конфигурация временного репозитория — наша, без `url.*`: переписать источник
    через репозиторий продукта, глобальную конфигурацию или `GIT_CONFIG_*` нельзя вовсе —
    отдельная предпроверка `insteadOf` снята;
  - учётные данные: `-c credential.helper=` (сброс) и `-c credential.helper=!gh auth
    git-credential` с `GH_CONFIG_DIR` профиля runner'а (`REVIEW_GH_CONFIG_DIR`, как прочие вызовы
    `gh` агента); публичный продукт — анонимно; токен не попадает ни в argv, ни в диагностику;
  - все чтения проверки (история, `LegacyProbe`, байты, входы, догрузка головы squash-PR) — через
    `SnapshotOps` (временный репозиторий + SHA снимка). `SnapshotOps` — deny-by-default: пять
    методов git снимка и allowlist форджи `SNAPSHOT_FORGE_METHODS` (`brief_pr_fact`, `find_pr`,
    `pr_facts`, `prs_containing_commit`, `repo_file_fact`, `policy_version_fact` — сверено по
    всем вызовам внутри проверки); любой другой метод — `AttributeError` с именем метода, так что
    новое чтение git внутри проверки падает громко, а не читает клон. Тесты: непереопределённые
    методы git (`blob_in_commit`, `commit_files`, `checkout_and_pull`, `is_ancestor`, …) —
    отказ; мета-проверка — ни один метод allowlist не принимает `target_dir` и не запускает `git`;
  - тесты: инъекция `insteadOf` в config клона, в глобальный, через `GIT_CONFIG_COUNT`, в config
    клона между созданием снимка и fetch, унаследованный `GIT_DIR` — снимок свежий; команда
    fetch изолирована (окружение, сброс helper'а, канонический URL); приватный продукт — токен
    только от helper'а, глобальный helper не участвует; токена нет в argv и диагностике;
    профиль `gh` без токена — публичный продукт читается, запрос не интерактивен; снимок
    удаляется при исключении; чередующиеся снимки независимы; сквозные тесты устаревшего клона
    сохранены;
  - предусловие развёртывания (профиль `gh` runner'а для приватного продукта) — в п. 8 выше и в
    Task 14 рядом с привязкой `/etc/df/trust.toml`.
- **Правки по d6** (ревью `plan-review-d6`: какими учётными данными аутентифицируется fetch
  снимка): D6-2 — процессы git снимка получают свежий пустой `HOME` (каталог `home` внутри
  временного каталога снимка, удаляется вместе с ним) и `XDG_CONFIG_HOME` внутри него; путь
  профиля `REVIEW_GH_CONFIG_DIR` разрешается в абсолютный ДО подмены HOME и передаётся helper'у
  как `GH_CONFIG_DIR`. Регрессия: посторонние `~/.netrc` и `~/.gitconfig` (с credential helper)
  в настоящем HOME не участвуют — fetch снимка видит пустой HOME, профиль `gh` — абсолютный путь.
- **Остаточный риск D6-1** (принят решением team-lead, не перепроектируется): `GH_CONFIG_DIR`
  не изолирует системный keyring — при пустом профиле `gh auth git-credential` может вернуть
  общий токен github.com из keyring (`TokenFromKeyring("github.com")`), и приватный продукт
  будет прочитан более широким токеном, чем у профиля ревью. Целостность проверки допуска от
  личности читающего не зависит: URL канонический (`https://github.com/<repo_slug>.git`),
  содержимое адресовано SHA полученного снимка, чтения — только из него; худший случай —
  чтение приватного продукта более широким токеном (гигиена учётных данных), а не обход
  допуска. Последующая работа (TODO): собственный credential helper, читающий токен только
  профиля (`hosts.yml` профиля), без fallback к keyring — `gh` хранит токены в общем keyring с
  ключом по хосту; триггер — требование изоляции учётных данных между профилями.
- **Реестр Task 16**: `act_envelope.node_history` (3), `act_status.noop_refusal` (4 + 2 d2 + 2 d3),
  `act_status.fresh_base` (2), `ops.SnapshotOps.__getattr__` (1, deny), `ops.RealOps._snapshot_url` (1),
  `_credential_helper` (1),
  `_snapshot_env` (6, из них 2 — пустой HOME и абсолютный профиль, d6), `_snapshot_git` (1), `ops.RealOps.snapshot_open` (4, из них `--refmap=`/
  `FETCH_HEAD` — `RULED`),
  `runner._step_s8#run_status` (1),
  `task_bridge._require_admission` (1), `deliver_superseded#…` (7), `deliver_for_run#…` (2),
  `runner._step_gate#completed_admission`, `runner._step_s8#completed_admission`,
  `runner._next_wave#admission`, `approve_node._propose#mode_unavailable`,
  `#legacy_noop_review`; `current_event`/`next_generation` — в `MODIFIED_EXISTING`,
  `task_bridge._admission_refusal` — в `NOT_JUDGMENT` (делегирует). Применимость операторов —
  303/303 (291 по коду scratch, 12 по AST фрагментов); мутации новых строк: 8 (п. 9, В-1) + 17
  (d1, исполнимые в scratch) — все закрыты; `_propose#…` (2) — на стенде `World`, Step 4a.
- **Буква спеки**: В-1 теперь соответствует цели §9.1 (а) п. 4 (проверенный статус спрашивается
  перед no-op), хотя статус не входит в саму проекцию хеша; §9.1 п. 0a («последний доставленный
  candidate, чей манифест включает узел») при (Б) читается как «последний **принятый**» — правка
  формулировки спеки предложена в отчёте ревизии, план её не решает.

### Решение владельца 01.11 (С-1)

Слова владельца (01.11), дословно:

> «С-1 — да, read-only токен для истории»

> «С-2 — да, добавляй в rev 18»

**С-2** (там же, 01.11): ограничение steward M0 О11 — ветка `approval-policy`, закреплённая
человеком (`APPROVAL_POLICY_BRANCH` в `df-trust/source.env`), обязана быть default branch репо
`approval-policy`, иначе `unavailable` — внесено в спеку rev 18 как требование §6.1 п. 1; смена
default branch останавливает решения до исправления закрепления человеком. Новой задачи нет:
план уже поглощает О11 fail-closed — `act_facts.history` (Task 11a, `branch_not_default` →
`Unavailable`), предусловие H1/H2, строка сверки «история по закреплённой ветке» в «Интерфейсе
M0»; там добавлена ссылка на §6.1. Снимок `df-trust` (Р-2) по-прежнему читается по ветке привязки,
не по default branch. Ниже — С-1.

Повод — вопрос к спеке С-1 (ревизия 31.10; minor 2 платного ревью #666): история обоих
источников доверия (`approval-policy` через транспорт steward v3; снимок `df-trust` через
`act_trust.read_trust_snapshot` — first-parent ≤ `MAX_CHAIN` = 500, ~3 запроса на звено) читается
анонимным REST (`act_anon.AnonTransport`); анонимная квота GitHub — 60 запросов/ч на IP, одно
решение — ~20–40+ запросов и растёт с историей `df-trust` → каждое решение `Unavailable: http`
(fail-closed, но основной путь стоит). Спека — rev 18 §6.2, §9.4, §13. Правила контроллера
(R1–R9), по которым написана Task 11c:

- **R1 — транспорт.** Чтения истории и содержимого источников доверия — REST с выделенным
  read-only токеном («токен истории»), тот же протокол и те же гарантии, что у `AnonTransport`
  (только `GET` относительного пути, без прокси и перенаправлений, память только по SHA,
  blob-oid в `contents_bytes`, проверки `_REPO`/40-hex). Один путь кода: `AnonTransport(token=…)`.
  Токен — только в заголовке `Authorization`; не в argv, окружении дочерних процессов,
  диагностике, ключах памяти, исключениях.
- **R2 — откуда токен.** Защищённая привязка (`act_trust.load_binding`: root, не пишется
  группой/всеми) получает необязательный ключ `history_gh_config_dir` — каталог профиля `gh`
  `history`. Токен — `gh auth token --hostname github.com` под `GH_CONFIG_DIR=<каталог>`,
  окружение очищено как у `gh_get`; не из `GH_TOKEN`/`GITHUB_TOKEN`/окружения исполнителя.
  Место запуска проходит правило `allowed_literal_programs` (`gh`) контракта `spawn-sites.toml`
  (Р-22); тест инварианта остаётся зелёным без правки контракта.
  Остаточный риск D6-1 (пустой профиль → токен github.com из системного keyring) ограничен R3:
  самоотчёт идёт тем же значением, что и чтения (получено один раз на решение; один транспорт для всех чтений решения, журнала и STALE), поэтому
  подставленный токен с правом записи — нарушение, а не обход.
- **R3 — самоотчёт.** Токен истории — удерживаемый credential: запись `profile:history`
  создаёт `load_trust` из того же значения токена, что и транспорт (не `act_trust.credentials()`,
  решение после c1-r6); самоотчёт §6.2 (б) на обоих репо доверия (`push`/`maintain`/
  `admin` — `false`; нет поля, 404, ошибка → `Unavailable`); право записи — нарушение периметра.
- **R4 — тип токена.** Fine-grained PAT «public repositories (read-only)» без доступа к
  репозиториям или classic PAT без scopes; квота 5000 запросов/ч; создаёт человек (ключи и PAT
  через агента не проходят), шаг человека установщика.
- **R5 — нет ключа ≠ сломанный ключ.** Ключа нет — анонимный транспорт (v1 без изменений). Ключ
  есть, но профиля нет / токен не читается / самоотчёт не чист → `Unavailable` с причиной;
  **тихого возврата к анонимному чтению нет**.
- **R6 — только публичные.** Токен приватные источники не открывает (§6.2 остаётся); проверки
  идентичности (`repository_id`, `df_trust_full_name`) не меняются; чтение токеном не доказывает
  отсутствия прав у других credentials.
- **R7 — область.** Транспорт истории обслуживает историю и активации `approval-policy`
  (`act_facts.history`/`activation`), снимок `df-trust` (`read_trust_snapshot`, время якорей,
  `ForgeEpochs`), ruleset и `allow_rebase_merge`. Чтения продукта и GraphQL фактов PR
  (`gh_transport` с токеном агента) не меняются.
- **R8 — сторона установщика** (решение контроллера 01.11): разбор ключа в
  `df/stage2_config.py` (`parse_trust`: необязательный ключ, только абсолютный путь; схема
  остаётся `df/trust/v1`, файлы без ключа корректны) — **в Task 11c**; запись ключа
  `darkfactory setup-trust`, проверка `darkfactory doctor` и шаг человека «создать профиль
  `history`» — handoff этапу установщика 2b/3 (пункт в `TODO.md`). На хосте со старой версией
  `df` человек ключ не вписывает (прежний `parse_trust` отвергает незнакомый ключ); сторона акта
  обязана работать с привязкой без ключа (R5).
- **R9 — место в плане.** Новая Task 11c, исполняется сразу после Task 14 (до 13): 11 → 11a →
  11b → 12 → 12a → 14 → **11c** → 13 → 13a → 15 → 15a → 16. Меняет `act_anon`, `act_trust`
  (ключ привязки, фабрика `history_transport`, `load_trust` строит транспорт сам — решение
  контроллера после c1-r6), `act_context`, разбор ключа в `df/stage2_config.py` (R8) и
  `act_status` Task 14; Task 13+ не затронуты (листинги `act_premerge` Task 15 приведены к новым
  подписям).

**Выборы исполнителя плана внутри R1–R9** (спека и правила молчали; **Р-18, Р-19, Р-22 приняты
контроллером 01.11**, Р-18 внесён в текст спеки §6.2):

| # | Решение | Почему | Цена, если неверно |
|---|---|---|---|
| Р-18 | Самоотчёт `profile:history` идёт **через сам транспорт истории** (`GET repos/{repo}` с тем же `Authorization`), а не `gh api`; `gh_get` для `profile:history` отказывает. Ревью c1-r4/c1-r6 (решения контроллера): `load_trust` сам строит транспорт из привязки, сам создаёт запись самоотчёта `profile:history` из того же значения токена и выполняет её `_history_self_report(transport, snap)` тем же объектом, которым идут все чтения истории; `readers.as_credential` для неё не вызывается никогда | R1 запрещает токен в окружении дочерних процессов, а `gh_get` передаёт токен credential через `GH_TOKEN`; R2 требует то же значение, что у чтений, — транспорт даёт его по построению | второй способ самоотчёта (REST вместо `gh api`); расхождение форм ответа ловит тот же `permissions_refusal` |
| Р-19 | Неисправный настроенный токен → `history_transport` возвращает `Unavailable(f"токен истории: {причина}")`, `load_trust` отдаёт его до первого запроса (после c1-r6 `RefusedTransport` не нужен: транспорт строит только `load_trust`) | ни один путь чтения не может уйти анонимно — анонимный транспорт при ключе не строится вовсе | нет |
| Р-23 | **Один транспорт на решение** (C7-01, решение контроллера): журнал периметра перечитывает снимок тем же объектом транспорта, что построил `load_trust`. `read_trust_snapshot(transport, binding)` кладёт транспорт в `TrustSnapshot.transport`; `DevJournal.status/record(snap)` передаёт `snap.transport` в `ForgeEpochs.via(...)`; `ForgeEpochs(binding)` перечитывает `read_trust_snapshot(<этот транспорт>, binding)`, транспорт не строит и токен не читает; повторы `STALE` (до трёх) — тем же транспортом | спека §6.2: транспорт строит вход решения только из привязки; второе чтение токена дало бы неотчитанный credential | нет; `InstalledJournal` и узкий журнал (`act_journal_wire`) снимок в этом процессе не перечитывают (издатель — отдельный процесс со своими чтениями) |
| Р-20 | Профиль существует, только если `<каталог>/hosts.yml` — файл; иначе `Unavailable` **до** запуска `gh` | R5 («профиля нет» → `Unavailable`); при отсутствующем каталоге `gh auth token` отдал бы токен из keyring (D6-1) | профиль с токеном только в keyring без `hosts.yml` не распознаётся — отказ, не расширение |
| Р-21 | Значение ключа — абсолютный путь; токен — `[A-Za-z0-9_]+`, иначе `Unavailable` без значения в причине | относительный путь разрешался бы от cwd исполнителя; форма токена исключает управляющие символы в заголовке | токен нового формата GitHub с другими символами даст `Unavailable` до правки формы |
| Р-22 | Место запуска `gh auth token` проходит правило `allowed_literal_programs` (`gh`) контракта `spawn-sites.toml`, как `gh_get`; поимённой записи нет | `tests/test_author_sandbox_invariant.py::violations` считает только нелитеральные программы: запись `[[site]]` для литерального `gh` дала бы «в перечне 1, найдено 0» — красный тест | место проходит по правилу контракта, а не записью; argv и окружение держит тест Task 11c, `test_spawn_and_dynamic_load_sites_match_the_contract` — зелёный без правки контракта |

**Инструкция steward устарела** (`steward/docs/approval-facts-v3.md:79–86`, «История политики:
собственный анонимный REST-транспорт», и «Заметки §12» п. 1): «обязаны работать без
credentials», «токен потребителю не предлагается» выведены из прежней §6.2 devtools. Библиотека
credentials не проверяет, код steward не меняется; правка текста — у steward, inbox-issue
**steward#232** (отправлен контроллером).
### Task 11: Пин steward v0.2.0, совместимость вызовов gate-check, gate-catalog v3

**Files:**
- Modify: `pyproject.toml` (`[tool.uv.sources] steward rev`), `uv.lock`
- Modify: `profiles/gate-catalog.yaml` (ре-вендор из `steward@68f46a9`)
- Modify: `governance/ops.py` (`Ops.gate_check_s8`, `Ops.gate_check_candidate`, новый
  `Ops.gate_snapshot_digest`; `RealOps` — то же)
- Modify: `governance/runner.py` (`_step_gate` ~3275, `_step_s8` ~4024 — явный `act_mode`)
- Modify: `governance/bundle_state.py:70` (`collect_bundle(..., act_mode="legacy")`)
- Modify (тесты — вызовы, стабы и пересылки; полный список — статический обход ниже, ревизия
  b1, B1-7): `tests/test_governance_ops.py:316,341` (прямые вызовы `gate_check_s8` + проверки
  argv), `tests/test_governance_runner.py:514,522` (стабы), `:1692–1696,2893–2897` (пересылка в
  `RealOps`), `tests/test_governance_sandbox_live.py:855–860` (пересылка),
  `tests/test_governance_waves_e2e.py:206,209`,
  `tests/test_governance_bundle_state.py:375,387`, `tests/test_governance_stale_adapter.py:32,53,68`,
  `tests/test_governance_steward_surface.py` (все вызовы `collect_bundle`)
- Test: `tests/test_governance_gate_act_mode.py` (новый)

**Interfaces:**
- Consumes: steward v0.2.0 (`gate-check --act-mode`, `collect_bundle(..., *, act_mode)`,
  gate-catalog v3).
- Produces:
  `Ops.gate_check_s8(target_dir, bundle_dir, profile, *, act_mode: str, node_status: str | None = None) -> tuple[int, str]`;
  `Ops.gate_check_candidate(target_dir, bundle_dir, profile, *, act_mode: str, node_status: str | None = None) -> tuple[int, str]`;
  `Ops.gate_snapshot_digest(target_dir, bundle_dir, *, candidate: bool) -> tuple[int, str]`
  (`gate-check --print-snapshot-digest [--candidate] <dir>`; Task 14). `act_mode` — без
  умолчания: каждый вызывающий называет результат admission сам (О14). До Task 14 оба шага
  раннера передают `"legacy"` — до части B заявок `review/v1` нет нигде, и это тот же исход,
  что дал бы admission.

Места вызова gate-check в devtools (полный список по `git grep -nE "gate-check|gatecheck"`
вне `docs/`, `tests/`): `RealOps.gate_check_s8` (`ops.py:2105`, S8 раннера, `runner.py:4024`),
`RealOps.gate_check_candidate` (`ops.py:2154`, S4 раннера, `runner.py:3275`),
`bundle_state.candidate_state` → `collect_bundle` (`bundle_state.py:70`, консоль). Прочие
упоминания — комментарии и вендоренный линтер брифа discovery (`gate_check.py` — другой
инструмент). Вне devtools — dispatcher и spec-runner (их inbox-заявки ведёт steward, §12 п. 2).

- [ ] **Step 1: Write the failing test** — `tests/test_governance_gate_act_mode.py`:

```python
"""Вызовы gate-check несут --act-mode (approval-facts v3 §7.2, О14)."""

from __future__ import annotations

import ast
import inspect
import subprocess
from pathlib import Path

import pytest
import yaml

from governance import ops as ops_mod
from governance.ops import Ops, RealOps

ROOT = Path(__file__).resolve().parent.parent


class _Done:
    def __init__(self) -> None:
        self.returncode = 0
        self.stdout = "ok\n"
        self.stderr = ""


@pytest.fixture()
def argv(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    seen: list[list[str]] = []

    def run(cmd, **_kw):  # noqa: ANN001, ANN003 — подмена subprocess.run
        seen.append(list(cmd))
        return _Done()

    monkeypatch.setattr(ops_mod.subprocess, "run", run)
    return seen


@pytest.mark.parametrize("name", ["gate_check_s8", "gate_check_candidate"])
def test_act_mode_has_no_default(name: str) -> None:
    for owner in (Ops, RealOps):
        param = inspect.signature(getattr(owner, name)).parameters["act_mode"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is inspect.Parameter.empty


def test_s8_passes_act_mode(argv: list[list[str]]) -> None:
    RealOps().gate_check_s8("/t", "spec", "p.yaml", act_mode="legacy")
    assert argv[-1][-2:] == ["--act-mode", "legacy"]
    assert "--emit-verdicts" in argv[-1]


def test_candidate_passes_mode_and_provider(argv: list[list[str]]) -> None:
    RealOps().gate_check_candidate(
        "/t", "spec", "p.yaml", act_mode="review", node_status="/r/ns.json"
    )
    cmd = argv[-1]
    assert cmd[cmd.index("--act-mode") + 1] == "review"
    assert cmd[cmd.index("--node-status") + 1] == "/r/ns.json"
    assert "--candidate" in cmd


def test_snapshot_digest_needs_no_mode(argv: list[list[str]]) -> None:
    RealOps().gate_snapshot_digest("/t", "spec", candidate=True)
    assert argv[-1][1:] == ["--print-snapshot-digest", "--candidate", "spec"]
    assert "--act-mode" not in argv[-1]


def test_every_collect_bundle_call_names_act_mode() -> None:
    """Вызов steward-API без act_mode — TypeError на пине v0.2.0; ловим статически."""
    offenders: list[str] = []
    for path in [*ROOT.glob("governance/**/*.py"), *ROOT.glob("tests/**/*.py")]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", getattr(node.func, "attr", "")) == "collect_bundle"
                and not any(k.arg == "act_mode" for k in node.keywords)
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == []


GATE_METHODS = ("gate_check_s8", "gate_check_candidate")


def _gate_offenders(root: Path) -> list[str]:
    """Все места вызова и переопределения методов `Ops`, сменивших сигнатуру."""
    offenders: list[str] = []
    for path in [*root.glob("governance/**/*.py"), *root.glob("tests/**/*.py")]:
        rel = path.relative_to(root)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in GATE_METHODS:
                kwonly = {a.arg for a in node.args.kwonlyargs}
                if not {"act_mode", "node_status"} <= kwonly or node.args.kwarg is not None:
                    offenders.append(f"{rel}:{node.lineno} def {node.name}")
                for inner in ast.walk(node):  # пересылка обязана нести оба аргумента
                    if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
                            and inner.func.attr == node.name
                            and not {"act_mode", "node_status"}
                            <= {k.arg for k in inner.keywords}):
                        offenders.append(f"{rel}:{inner.lineno} пересылка {node.name}")
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in GATE_METHODS
                    and "act_mode" not in {k.arg for k in node.keywords}):
                offenders.append(f"{rel}:{node.lineno} вызов {node.func.attr}")
    return sorted(set(offenders))


def test_every_gate_check_site_names_act_mode() -> None:
    """B1-7: все места вызова, переопределения и пересылки `gate_check_*` —
    статически, чтобы пропущенный стаб не всплыл `TypeError` в чужом тесте."""
    assert _gate_offenders(ROOT) == []


def test_gate_catalog_is_v3_with_act_gates() -> None:
    data = yaml.safe_load((ROOT / "profiles/gate-catalog.yaml").read_text("utf-8"))
    assert data["version"] == 3
    acts = {"GC-ACT-MODE-CONFLICT", "GC-ACT-PROVIDER-MISSING", "GC-ACT-NOT-APPROVED"}
    assert acts <= set(data["gates"])
    assert all(data["gates"][g]["status"] == "active" for g in acts)


def test_cli_refuses_without_act_mode(tmp_path: Path) -> None:
    """Живой CLI пиненого steward: без --act-mode — код 2 (О14)."""
    pytest.importorskip("steward")
    exe = ROOT / ".venv" / "bin" / "gate-check"
    if not exe.exists():
        pytest.skip("gate-check не установлен в .venv")
    done = subprocess.run(
        [str(exe), str(tmp_path)], capture_output=True, text=True, check=False
    )
    assert done.returncode == 2
    assert "--act-mode is required" in done.stdout + done.stderr
```

`gates` в каталоге v3 — отображение `код → {obligation, status, title, stages}` (сверено по
`steward@68f46a9:profiles/gate-catalog.yaml`).

Статический обход `test_every_gate_check_site_names_act_mode` (ревизия b1, правило «класс, а не
экземпляр»): на сегодняшнем дереве он перечисляет ровно эти места — `governance/ops.py:306,312`
(`Ops`), `:2105,2154` (`RealOps`), `governance/runner.py:3275,4024`,
`tests/test_governance_ops.py:316,341`, `tests/test_governance_runner.py:514,522,1692/1696,
2893/2897`, `tests/test_governance_sandbox_live.py:855/859/860`,
`tests/test_governance_waves_e2e.py:206,209` (сверено прогоном обхода по `devtools@HEAD` ветки
плана). Каждое из них правится в этом же коммите; новый стаб или вызов без `act_mode` падает
здесь, а не `TypeError` в чужом тесте.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --group governance pytest tests/test_governance_gate_act_mode.py -v`
Expected: FAIL — `KeyError: 'act_mode'` (сигнатуры), `assert offenders == []`
(шесть файлов), `assert data["version"] == 3` (сейчас 2).

- [ ] **Step 3: Bump the pin and re-vendor the catalog**

```bash
WT=$(git rev-parse --show-toplevel)
STEWARD=$WT/../steward          # соседний чекаут, только чтение
REV=68f46a9b2e5cfe9ecd1f09524e986c3f687f4155
git -C "$STEWARD" cat-file -e "$REV^{commit}"
sed -i '' "s/^steward = { git = \"https:\/\/github.com\/andrei-shtanakov\/steward\", rev = \"[0-9a-f]*\" }/steward = { git = \"https:\/\/github.com\/andrei-shtanakov\/steward\", rev = \"$REV\" }/" pyproject.toml
grep -n "^steward = " pyproject.toml     # → rev = "68f46a9b…4155"
uv lock --upgrade-package steward
git -C "$STEWARD" show "$REV:profiles/gate-catalog.yaml" > profiles/gate-catalog.yaml
git diff --stat profiles/gate-catalog.yaml   # → только состав v2→v3 (+GC-ACT-*)
```

(GNU sed — без `''` после `-i`.) Каталог — побайтовая копия апстрима (тот же приём, что
`2d2c2bc`); правок devtools в нём нет.

- [ ] **Step 4: Make every call name the act mode**

`governance/ops.py` — протокол (около `:306`):

```python
    def gate_check_s8(
        self,
        target_dir: str,
        bundle_dir: str,
        profile: str,
        *,
        act_mode: str,
        node_status: str | None = None,
    ) -> tuple[int, str]: ...

    def gate_snapshot_digest(
        self, target_dir: str, bundle_dir: str, *, candidate: bool
    ) -> tuple[int, str]: ...

    def gate_check_candidate(
        self,
        target_dir: str,
        bundle_dir: str,
        profile: str,
        *,
        act_mode: str,
        node_status: str | None = None,
    ) -> tuple[int, str]: ...
```

`RealOps` (замена тел `:2105–2175`; докстринги сохраняются, абзац про пин дополняется):

```python
def _gate_check_exe() -> str:
    exe = DEVTOOLS_ROOT / ".venv" / "bin" / "gate-check"
    return str(exe) if exe.exists() else "gate-check"


def _act_args(act_mode: str, node_status: str | None) -> list[str]:
    """`--act-mode` обязателен на пине steward v0.2.0 (О14): результат
    admission вызывающего, steward его не выводит; провайдер — только при
    `review` (CLI steward сам отказывает кодом 2 на `legacy` + провайдер)."""
    args = ["--act-mode", act_mode]
    if node_status is not None:
        args += ["--node-status", node_status]
    return args
```

```python
    def gate_check_s8(
        self,
        target_dir: str,
        bundle_dir: str,
        profile: str,
        *,
        act_mode: str,
        node_status: str | None = None,
    ) -> tuple[int, str]:
        # (докстринг прежний + строка: «`--act-mode` — обязателен с steward v0.2.0»)
        done = subprocess.run(
            [
                _gate_check_exe(),
                bundle_dir,
                "--profile",
                profile,
                "--emit-verdicts",
                *_act_args(act_mode, node_status),
            ],
            cwd=target_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        return done.returncode, done.stdout + done.stderr

    def gate_snapshot_digest(
        self, target_dir: str, bundle_dir: str, *, candidate: bool
    ) -> tuple[int, str]:
        """`gate-check --print-snapshot-digest [--candidate] <dir>` (§7.2.3):
        дайджест снимка, к которому привязывается ответ провайдера."""
        flags = ["--candidate"] if candidate else []
        done = subprocess.run(
            [_gate_check_exe(), "--print-snapshot-digest", *flags, bundle_dir],
            cwd=target_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        return done.returncode, done.stdout.strip() or done.stderr

    def gate_check_candidate(
        self,
        target_dir: str,
        bundle_dir: str,
        profile: str,
        *,
        act_mode: str,
        node_status: str | None = None,
    ) -> tuple[int, str]:
        # (докстринг прежний)
        done = subprocess.run(
            [
                _gate_check_exe(),
                "--candidate",
                bundle_dir,
                "--profile",
                profile,
                *_act_args(act_mode, node_status),
            ],
            cwd=target_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        return done.returncode, done.stdout + done.stderr
```

`governance/runner.py` — оба вызова (до Task 14):

```python
    rc, output = ops.gate_check_candidate(
        state.target_dir, state.bundle_dir, profile_arg, act_mode="legacy"
    )
```

```python
        exit_code, output = ops.gate_check_s8(
            state.target_dir, state.bundle_dir, state.profile, act_mode="legacy"
        )
```

`governance/bundle_state.py:70` (консоль — read-модель без чтений форджи; провайдера здесь не
бывает по построению, поэтому `legacy`, а бандл с конвертами `review/v1` консоль покажет находкой
`GC-ACT-MODE-CONFLICT` — честно: статусов акта она не вычисляет; это фиксирует докстринг):

```python
    artifacts, findings = collect_bundle(graph, bundle_dir, act_mode="legacy")
```

Стабы `Ops` в тестах принимают новый аргумент (тело не меняется, кроме пересылающих в
`RealOps` — они передают аргументы дальше, см. ниже), например
`tests/test_governance_runner.py:514`:

```python
    def gate_check_candidate(
        self,
        target_dir: str,
        bundle_dir: str,
        profile: str,
        *,
        act_mode: str,
        node_status: str | None = None,
    ) -> tuple[int, str]:
        self.calls.append(("gate_check_candidate", bundle_dir))
        self.gate_modes.append(act_mode)
        ...
```

(`gate_modes: list[str] = field(default_factory=list)` в стабе раннера; тест раннера
`test_s4_and_s8_pass_legacy_until_act` дописывается: после прогона S4 и S8
`ops.gate_modes == ["legacy", "legacy"]`.) Остальные стабы — та же правка сигнатуры с
`**_kw`-неиспользуемыми аргументами не допускается: имя `act_mode` обязано быть в сигнатуре, чтобы
стаб падал, если вызывающий его не передал. Тесты, зовущие `collect_bundle` напрямую, получают
`act_mode="legacy"`.

Пересылающие стабы (`tests/test_governance_runner.py:1692,2893`,
`tests/test_governance_sandbox_live.py:855`) передают оба аргумента дальше без изменений:

```python
        def gate_check_candidate(
            self, target_dir, bundle_dir, profile, *, act_mode, node_status=None
        ):
            self.calls.append(("gate_check_candidate", bundle_dir))
            return RealOps().gate_check_candidate(
                target_dir, bundle_dir, profile, act_mode=act_mode, node_status=node_status
            )
```

(в `test_governance_sandbox_live.py:859–860` так же — и ветка `RealOps()`, и `super()`).
Прямые вызовы `tests/test_governance_ops.py:316,341` получают `act_mode="legacy"`, а их
проверки argv — хвост `["--act-mode", "legacy"]` (характеризация реальной команды обновляется
вместе с командой).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --group governance pytest tests/test_governance_gate_act_mode.py tests/test_governance_ops.py tests/test_governance_runner.py tests/test_governance_sandbox_live.py tests/test_governance_bundle_state.py tests/test_governance_stale_adapter.py tests/test_governance_steward_surface.py tests/test_governance_waves_e2e.py -v`
Expected: PASS. Затем полный набор в обоих режимах:
`uv run pytest -q` и `GOVERNANCE_REQUIRED=1 uv run --group governance pytest -q` — PASS
(никаких `SKIPPED (steward)` во втором). `uv run ruff check . && uv run ruff format --check . &&
uv run pyrefly check` — чисто.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock profiles/gate-catalog.yaml governance/ops.py governance/runner.py \
  governance/bundle_state.py tests/test_governance_gate_act_mode.py tests/test_governance_ops.py \
  tests/test_governance_runner.py \
  tests/test_governance_sandbox_live.py tests/test_governance_waves_e2e.py \
  tests/test_governance_bundle_state.py tests/test_governance_stale_adapter.py \
  tests/test_governance_steward_surface.py
git commit -m "build(steward): пин v0.2.0 (approval-facts v3), --act-mode legacy во всех вызовах gate-check, gate-catalog v3"
```

### Task 11a: Адаптер `act_facts`, вендор контракта v3, строгие векторы

**Files:**
- Create: `contracts/approval-facts/v3/` (побайтовая копия `steward@68f46a9`: `README.md`,
  `SCHEMA.json`, `MANIFEST.json`, `vectors/**`) + `contracts/approval-facts/v3/PINNED.txt`
- Create: `governance/act_facts.py`
- Modify: `contracts/authority-root/v1/paths.env`, `governance/accept_pr.py`
  (`_HARNESS_PREFIXES`), `tests/test_governance_authority_root.py` (ожидаемый набор) —
  `+ contracts/approval-facts/` (решение исполнителя плана Р-12: данные тестов ядра акта защищены
  так же, как сами тесты `tests/test_act_*`, заметки части A, строка 10)
- Test: `tests/test_act_facts.py`, `tests/test_act_facts_vectors.py`

**Interfaces:**
- Consumes: «Интерфейс M0 (релиз steward v0.2.0)»; Task 1–8 (`DeliveryView`, `ReviewView`,
  `Clock`, `parse_ts`, `SourceV2`, `POLICY_FILES`).
- Produces (`governance/act_facts.py`):
  `ActivationFact(pr: int, merge_commit: str, merged_at: datetime, files_sha256: Mapping[str, str])`;
  `HistoryBreak(reason: str)` — установленное нарушение формы истории;
  `gh_transport(gh_config_dir: Path) -> Transport`;
  `read(transport, repo, pr, paths) -> tuple[DeliveryView, ReviewFacts | None] | Unavailable`
  (`None` — PR не смёржен, `waiting_delivery`; ревью не читались);
  `open_review(transport, repo, pr) -> ReviewFacts | Unavailable`;
  `effective(facts, clock: Clock) -> dict[str, ReviewView] | Unavailable` — ключ — `review.login`
  (заметки части A, п. 1);
  `event_stamps(facts) -> dict[EventKey, datetime]` (каждая метка через `parse_ts`, п. 2);
  `review_requires(facts) -> dict[EventKey, frozenset[EventKey]]`;
  `history(transport, source: SourceV2, anchor: str, until: str | None) -> tuple[ActivationFact, ...] | HistoryBreak | Unavailable`;
  `activation(transport, source: SourceV2, merge_commit: str) -> ActivationFact | Unavailable`.
  Все `Unavailable` — `act_time.Unavailable` с `reason` steward дословно (`"<code>[: detail]"`).

- [ ] **Step 1: Vendor the contract**

```bash
REV=68f46a9b2e5cfe9ecd1f09524e986c3f687f4155
rm -rf contracts/approval-facts/v3
git -C ../steward archive "$REV" contracts/approval-facts/v3 | tar -x -C .
( cd contracts/approval-facts/v3 && {
    printf 'upstream: andrei-shtanakov/steward\ncommit: %s\n' "$REV"
    for f in MANIFEST.json README.md SCHEMA.json; do
      printf '%s %s\n' "$f" "$(shasum -a 256 "$f" | cut -d' ' -f1)"
    done
  } > PINNED.txt )
find contracts/approval-facts/v3/vectors -type f | wc -l   # → 79
```

`paths.env`: в строку `AUTHORITY_ROOT_PREFIXES=` дописать ` contracts/approval-facts/`;
`accept_pr._HARNESS_PREFIXES` — после `"tests/act_cases",`:

```python
    # Векторы сбора approval-facts v3 — данные тестов ядра акта; их подмена
    # ослабила бы проверку так же, как правка самих тестов (Р-12).
    "contracts/approval-facts/",
```

`tests/test_governance_authority_root.py` — ожидаемый набор префиксов `+ "contracts/approval-facts/"`.

- [ ] **Step 2: Write the failing tests**

`tests/test_act_facts_vectors.py`:

```python
"""Векторы сбора approval-facts v3 (steward §5): строгий прогон и адаптер devtools.

Строгий прогон — только `v3.run_vector` (решение M0-J, MUST): точный исход
(включая код `unavailable`), полное потребление обменов и общий порядок
запросов. Сверка адаптера — те же обмены через `act_facts`: перекладка полей
не теряет и не придумывает фактов.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

import pytest

pytest.importorskip("steward")

from steward.approvalfacts import v3  # noqa: E402

from governance import act_facts  # noqa: E402
from governance.act_policy import POLICY_FILES  # noqa: E402
from governance.act_source import SourceV2  # noqa: E402
from governance.act_time import Clock, Unavailable, parse_ts  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "contracts/approval-facts/v3"
VECTORS = CONTRACT / "vectors"
ALL = sorted(p for p in VECTORS.rglob("*.json"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _kind(kind: str) -> list[Path]:
    return [p for p in ALL if _load(p)["kind"] == kind]


def _id(path: Path) -> str:
    return path.relative_to(VECTORS).as_posix()


def _pinned() -> dict[str, str]:
    lines = (CONTRACT / "PINNED.txt").read_text(encoding="utf-8").splitlines()
    pairs = [line.split(": ", 1) if ": " in line else line.split(" ", 1) for line in lines]
    return {k: v for k, v in pairs}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_vendored_set_is_complete() -> None:
    assert len(ALL) == 79


def test_copy_integrity_both_ways() -> None:
    """О7: copy-integrity — гарантия потребителя (MANIFEST ↔ файлы, в обе стороны)."""
    pinned = _pinned()
    for name in ("MANIFEST.json", "README.md", "SCHEMA.json"):
        assert _sha256(CONTRACT / name) == pinned[name], name
    expected = json.loads((CONTRACT / "MANIFEST.json").read_text("utf-8"))["vectors"]
    actual = {_id(p): _sha256(p) for p in VECTORS.rglob("*") if p.is_file()}
    assert actual == expected


def test_vendor_pin_equals_dependency_pin() -> None:
    """Upstream-drift: копия контракта — от того же SHA, что зависимость."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    rev = data["tool"]["uv"]["sources"]["steward"]["rev"]
    assert _pinned()["commit"] == rev


def test_policy_files_are_stewards() -> None:
    assert POLICY_FILES == v3.POLICY_FILES


@pytest.mark.parametrize("path", ALL, ids=_id)
def test_vector_strict(path: Path) -> None:
    outcome = v3.run_vector(_load(path))
    assert outcome.ok, outcome.problems


def _code(got: object) -> str:
    assert isinstance(got, Unavailable), got
    return got.reason.split(":", 1)[0]


@pytest.mark.parametrize("path", _kind("act"), ids=_id)
def test_adapter_act(path: Path) -> None:
    spec = _load(path)
    expect = spec["expect"]
    transport = v3.replay_transport(spec["transport"])
    got = act_facts.read(transport, spec["repo"], spec["pr"], spec["paths"])
    if "unavailable" in expect:
        assert _code(got) == expect["unavailable"]
        return
    assert not isinstance(got, Unavailable), got
    delivery, facts = got
    if expect.get("waiting_delivery"):
        assert facts is None and delivery.merged is False
        assert delivery.merge_commit is None
        return
    assert facts is not None
    clock = Clock(parse_ts(spec["t"]), frozenset(tuple(k) for k in spec["before"]))
    eff = act_facts.effective(facts, clock)
    assert not isinstance(eff, Unavailable), eff
    assert {login: r.state for login, r in eff.items()} == expect["effective"]
    assert all(login == r.login for login, r in eff.items())
    for login, commit in expect.get("effective_commit", {}).items():
        assert eff[login].commit_id == commit
    for name, want in expect.get("delivery", {}).items():
        if name == "paths":
            pairs = {p["path"]: (p["blob_at_merge"], p["blob_at_head"]) for p in want}
            assert dict(delivery.blobs) == pairs
        else:
            assert getattr(delivery, name) == want, name


@pytest.mark.parametrize("path", _kind("open_review"), ids=_id)
def test_adapter_open_review(path: Path) -> None:
    spec = _load(path)
    got = act_facts.open_review(
        v3.replay_transport(spec["transport"]), spec["repo"], spec["pr"]
    )
    if "unavailable" in spec["expect"]:
        assert _code(got) == spec["expect"]["unavailable"]
        return
    clock = Clock(parse_ts(spec["t"]), frozenset(tuple(k) for k in spec["before"]))
    eff = act_facts.effective(got, clock)
    assert {k: r.state for k, r in eff.items()} == spec["expect"]["effective"]


def _source(spec: dict) -> SourceV2:
    branch = spec.get("branch", "main")
    return SourceV2(spec["repo"], branch, spec.get("anchor", "0" * 40), frozenset({"x"}))


def _as_record(a: act_facts.ActivationFact) -> dict:
    return {
        "pr": a.pr,
        "merge_commit": a.merge_commit,
        "merged_at": a.merged_at.strftime(act_facts.STAMP),
        "files_sha256": dict(a.files_sha256),
    }


@pytest.mark.parametrize("path", _kind("history"), ids=_id)
def test_adapter_history(path: Path) -> None:
    spec = _load(path)
    assert tuple(spec["files"]) == POLICY_FILES  # адаптер всегда читает один набор
    got = act_facts.history(
        v3.replay_transport(spec["transport"]),
        _source(spec),
        spec["anchor"],
        spec["until"],
    )
    expect = spec["expect"]
    if "unavailable" in expect:
        assert _code(got) == expect["unavailable"]
    elif "violation" in expect:
        assert isinstance(got, act_facts.HistoryBreak)
        assert got.reason.split(":", 1)[0] == expect["violation"]
    else:
        assert isinstance(got, tuple)
        assert [_as_record(a) for a in got] == expect["activations"]


@pytest.mark.parametrize("path", _kind("activation"), ids=_id)
def test_adapter_activation(path: Path) -> None:
    spec = _load(path)
    assert tuple(spec["files"]) == POLICY_FILES  # адаптер всегда читает один набор
    got = act_facts.activation(
        v3.replay_transport(spec["transport"]), _source(spec), spec["merge_commit"]
    )
    if "unavailable" in spec["expect"]:
        assert _code(got) == spec["expect"]["unavailable"]
    else:
        assert isinstance(got, act_facts.ActivationFact)
        assert _as_record(got) == spec["expect"]["activation"]


def test_review_focus_3_latest_review_commit_wins() -> None:
    """Review Focus 3 векторами M0 (заметки части A, п. 10)."""
    spec = _load(VECTORS / "login_approved_old_then_new.json")
    _, facts = act_facts.read(
        v3.replay_transport(spec["transport"]), spec["repo"], spec["pr"], spec["paths"]
    )
    eff = act_facts.effective(facts, Clock(parse_ts(spec["t"])))
    for login, commit in spec["expect"]["effective_commit"].items():
        assert eff[login].commit_id == commit


def test_review_focus_2_bot_login_two_forms() -> None:
    spec = _load(VECTORS / "bot_login_two_forms.json")
    assert v3.run_vector(spec).ok
    got = act_facts.read(
        v3.replay_transport(spec["transport"]), spec["repo"], spec["pr"], spec["paths"]
    )
    assert not isinstance(got, Unavailable)
    assert not got[0].merged_by_login.endswith("[bot]")  # О9: GraphQL-форма
```

Все векторы `history/`/`activation/` релиза объявляют `files = POLICY_FILES` (сверено): адаптер
передаёт этот набор всегда (§6 «один набор»), и тест это утверждает, а не пропускает.

`tests/test_act_facts.py` (поведение адаптера вне векторов):

```python
"""Адаптер act_facts: перевод исходов steward и граница исключений."""

from __future__ import annotations

from datetime import UTC

import pytest

pytest.importorskip("steward")

from steward.approvalfacts import v3  # noqa: E402

from governance import act_facts  # noqa: E402
from governance.act_source import SourceV2  # noqa: E402
from governance.act_time import Clock, Unavailable, parse_ts  # noqa: E402

HEAD = "1" * 40
SOURCE = SourceV2("owner/approval-policy", "main", "a" * 40, frozenset({"owner"}))


def _facts(*reviews: v3.Review, dismissals: tuple[v3.Dismissal, ...] = ()) -> v3.ReviewFacts:
    return v3.ReviewFacts("owner/p", 7, HEAD, "main", "MERGED", reviews, dismissals)


class _Raising:
    def send(self, request: v3.Request) -> v3.Response:
        raise OSError("socket closed")


def test_library_exception_is_unavailable() -> None:
    """Инструкция steward: исключение — «факт не установлен», не разрешение."""
    got = act_facts.read(_Raising(), "owner/p", 7, ["a.md"])
    assert isinstance(got, Unavailable)
    assert "OSError" in got.reason


def test_caller_error_is_unavailable_too() -> None:
    """`repo` не owner/name — ValueError у steward; граница переводит в Unavailable."""
    got = act_facts.history(_Raising(), SourceV2("bad", "main", "a" * 40, frozenset({"o"})),
                            "a" * 40, None)
    assert isinstance(got, Unavailable)


def test_effective_keys_by_review_login() -> None:
    facts = _facts(v3.Review("11", "alice", "User", "APPROVED", HEAD, "2026-10-12T09:00:00Z"))
    eff = act_facts.effective(facts, Clock(parse_ts("2026-10-12T10:00:00Z")))
    assert list(eff) == ["alice"] and eff["alice"].login == "alice"
    assert eff["alice"].commit_id == HEAD


def test_call_turns_exception_into_unavailable() -> None:
    """b5/B3-11 (`_call`): исключение библиотеки — свой `Unavailable` с именем исключения."""
    def boom():
        raise OSError("socket")

    got = act_facts._call("read_act_facts", boom)
    assert isinstance(got, Unavailable)
    assert got.reason == "steward read_act_facts: OSError: socket"


def test_own_translates_steward_unavailable() -> None:
    """b5/B3-11 (`_own`): `v3.Unavailable` переводится в свой тип с той же причиной."""
    got = act_facts._own(v3.Unavailable("transport"))
    assert isinstance(got, Unavailable) and got.reason == "transport"
    assert act_facts._own("значение") == "значение"


def test_steward_unavailable_reason_kept_verbatim() -> None:
    """Отзыв ревью, не находящегося в DISMISSED — `malformed` (О13), code сохраняется."""
    facts = _facts(
        v3.Review("11", "alice", "User", "APPROVED", HEAD, "2026-10-12T09:00:00Z"),
        dismissals=(v3.Dismissal("11", "bob", "2026-10-12T09:30:00Z", "APPROVED"),),
    )
    got = act_facts.effective(facts, Clock(parse_ts("2026-10-12T10:00:00Z")))
    assert isinstance(got, Unavailable)
    assert got.reason.startswith("malformed")


def test_event_stamps_are_normalised_for_clock() -> None:
    """Заметки части A, п. 2: Clock принимает каждую метку адаптера."""
    facts = _facts(
        v3.Review("11", "alice", "User", "DISMISSED", HEAD, "2026-10-12T09:00:00Z"),
        dismissals=(v3.Dismissal("11", "bob", "2026-10-12T10:00:00Z", "APPROVED"),),
    )
    stamps = act_facts.event_stamps(facts)
    clock = Clock(parse_ts("2026-10-12T10:00:00Z"))
    for key, stamp in stamps.items():
        assert stamp.tzinfo is UTC and stamp.microsecond == 0
        clock.precedes(key, stamp)  # не бросает ValueError
    assert act_facts.review_requires(facts) == {
        ("dismissal", "11"): frozenset({("review", "11")})
    }


def test_before_carries_only_review_keys() -> None:
    """Ключи активаций и границ эпох в Clock.before steward не передаются."""
    facts = _facts(v3.Review("11", "alice", "User", "APPROVED", HEAD, "2026-10-12T10:00:00Z"))
    clock = Clock(
        parse_ts("2026-10-12T10:00:00Z"),
        frozenset({("review", "11"), ("activation", "p" * 40)}),
    )
    eff = act_facts.effective(facts, clock)
    assert eff["alice"].state == "APPROVED"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run --group governance pytest tests/test_act_facts.py tests/test_act_facts_vectors.py -v`
Expected: FAIL — `ImportError: cannot import name 'act_facts' from 'governance'`; строгие векторы
(`test_vector_strict`) и целостность копии уже PASS — они не зависят от адаптера.

- [ ] **Step 4: Write the adapter**

```python
"""Единственная точка импорта общего читателя фактов steward (спека D4, §5).

Остальные `act_*` модули видят только свои типы (`DeliveryView`,
`ReviewView`, `ActivationFact`, `HistoryBreak`) и `act_time.Unavailable`:
смена API steward меняет этот файл и ничего больше. steward импортируется
внутри функций (`_v3()`), чтобы модуль загружался без группы governance
(контракт `ci.yml:27`). Любое исключение из вызова библиотеки — «факт не
установлен» (инструкция steward, «Транспорт»), никогда не разрешение.
Второй реализации логики ревью здесь нет — только перекладка полей.

Транспорт выбирает вызывающий (правило steward §12 п. 1): факты акта —
`gh_transport` (GraphQL с токеном), история и активация — анонимный
`act_anon.AnonTransport`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from governance.act_decision import DeliveryView, ReviewView
from governance.act_policy import POLICY_FILES
from governance.act_source import SourceV2
from governance.act_time import Clock, EventKey, Unavailable, parse_ts

#: Канон меток steward (`YYYY-MM-DDTHH:MM:SSZ`).
STAMP = "%Y-%m-%dT%H:%M:%SZ"
_REVIEW_KEYS = ("review", "dismissal")


@dataclass(frozen=True)
class ActivationFact:
    """Активация источника: PR, merge-коммит, время мержа PR, sha256 файлов."""

    pr: int
    merge_commit: str
    merged_at: datetime
    files_sha256: Mapping[str, str]


@dataclass(frozen=True)
class HistoryBreak:
    """Установленное нарушение формы истории (steward `HistoryViolation`).

    `reason` — `"<code>: <detail>"`; коды в решении не различаются (§12 п. 5).
    """

    reason: str


def _v3() -> Any:
    from steward.approvalfacts import v3

    return v3


def _call[T](what: str, fn: Callable[[], T]) -> T | Unavailable:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — граница библиотеки (инструкция steward)
        return Unavailable(f"steward {what}: {type(exc).__name__}: {exc}")


def _own(got: Any) -> Any:
    """`v3.Unavailable` → `act_time.Unavailable` с той же причиной; прочее — как есть."""
    if isinstance(got, _v3().Unavailable):
        return Unavailable(got.reason)
    return got


def gh_transport(gh_config_dir: Path) -> Any:
    """Транспорт фактов акта: `gh api` под профилем `gh_config_dir` (О10)."""
    return _v3().GhTransport(gh_config_dir, timeout=60)


def _delivery(d: Any) -> DeliveryView:
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


def read(
    transport: Any, repo: str, pr: int, paths: Sequence[str]
) -> tuple[DeliveryView, Any | None] | Unavailable:
    """Доставка и факты ревью на `T = merged_at` (steward §3.3, спека §5.5).

    Не смёржен — `(DeliveryView(merged=False, …), None)`: ревью не читались,
    предварительный результат для допуска не переиспользуется.
    """
    v3 = _v3()
    got = _own(
        _call(
            "read_act_facts",
            lambda: v3.read_act_facts(transport, repo, pr, paths=tuple(paths)),
        )
    )
    if isinstance(got, Unavailable):
        return got
    delivery, facts = got
    return _delivery(delivery), facts


def open_review(transport: Any, repo: str, pr: int) -> Any | Unavailable:
    """Факты ревью открытого PR (два прохода §3.4); решение по ним не окончательно."""
    v3 = _v3()
    return _own(_call("read_open_review", lambda: v3.read_open_review(transport, repo, pr)))


def effective(facts: Any, clock: Clock) -> dict[str, ReviewView] | Unavailable:
    """Эффективные ревью на `clock` (steward §3.5); ключ — `review.login`."""
    v3 = _v3()
    stamp = clock.t.strftime(STAMP)
    before = frozenset(k for k in clock.before if k[0] in _REVIEW_KEYS)
    got = _own(
        _call(
            "effective_reviews",
            lambda: v3.effective_reviews(facts, stamp, before=before),
        )
    )
    if isinstance(got, Unavailable):
        return got
    return {
        r.login: ReviewView(r.review_id, r.login, r.type_hint, r.state, r.commit_id)
        for r in got.values()
    }


def event_stamps(facts: Any) -> dict[EventKey, datetime]:
    """Метки событий ревью и отзывов — для группы равных меток (§8.2)."""
    stamps: dict[EventKey, datetime] = {}
    for r in facts.reviews:
        if r.submitted_at is not None:
            stamps[("review", r.review_id)] = parse_ts(r.submitted_at)
    for d in facts.dismissals:
        stamps[("dismissal", d.review_id)] = parse_ts(d.created_at)
    return stamps


def review_requires(facts: Any) -> dict[EventKey, frozenset[EventKey]]:
    """Отправка ревью предшествует его отзыву (§8.2 п. 2)."""
    return {
        ("dismissal", d.review_id): frozenset({("review", d.review_id)})
        for d in facts.dismissals
    }


def _activation(a: Any) -> ActivationFact:
    return ActivationFact(a.pr, a.merge_commit, parse_ts(a.merged_at), dict(a.files_sha256))


def history(
    transport: Any, source: SourceV2, anchor: str, until: str | None
) -> tuple[ActivationFact, ...] | HistoryBreak | Unavailable:
    """Активации ветки источника `anchor…until` по first-parent (steward §3.7).

    Транспорт — анонимный (`act_anon`); ветка обязана быть default branch
    источника (О11), иначе `Unavailable: branch_not_default`.
    """
    v3 = _v3()
    got = _own(
        _call(
            "read_policy_history",
            lambda: v3.read_policy_history(
                transport,
                source.repo,
                source.branch,
                anchor=anchor,
                until=until,
                files=POLICY_FILES,
            ),
        )
    )
    if isinstance(got, Unavailable):
        return got
    if isinstance(got, v3.HistoryViolation):
        return HistoryBreak(got.reason)
    return tuple(_activation(a) for a in got)


def activation(
    transport: Any, source: SourceV2, merge_commit: str
) -> ActivationFact | Unavailable:
    """Факт одной активации (якорь эпохи); ветку не проверяет — это делает история."""
    v3 = _v3()
    got = _own(
        _call(
            "read_activation",
            lambda: v3.read_activation(
                transport, source.repo, merge_commit, files=POLICY_FILES
            ),
        )
    )
    if isinstance(got, Unavailable):
        return got
    return _activation(got)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --group governance pytest tests/test_act_facts.py tests/test_act_facts_vectors.py tests/test_governance_authority_root.py -v`
Expected: PASS (79 строгих векторов + адаптер по видам). Без группы:
`uv run pytest tests/test_act_facts.py -q` → `SKIPPED (steward)`, collection без ошибок
(`import governance.act_facts` steward не тянет). ruff, pyrefly — чисто.

- [ ] **Step 6: Commit**

```bash
git add contracts/approval-facts governance/act_facts.py contracts/authority-root/v1/paths.env \
  governance/accept_pr.py tests/test_governance_authority_root.py tests/test_act_facts.py \
  tests/test_act_facts_vectors.py
git commit -m "feat(act): адаптер approval-facts/v3, вендор контракта @ steward 68f46a9, строгие векторы через run_vector"
```

### Task 11b: Анонимный REST-транспорт `act_anon`

> **С-1 (01.11):** текст задачи — как влита; тот же транспорт получает необязательный read-only
> токен истории в Task 11c (`AnonTransport(token=…)`, спека rev 18 §6.2). Утверждения ниже «без
> `Authorization`» верны для транспорта без токена.

**Files:**
- Create: `governance/act_anon.py`
- Test: `tests/test_act_anon.py`

**Interfaces:**
- Consumes: протокол `v3.Transport` (`send(Request) -> Response`), контракт реализации —
  инструкция steward, «История политики: собственный анонимный REST-транспорт».
- Produces: `AnonTransport(*, open_url: Callable | None = None, timeout: float = 30.0)` с
  `send(request) -> Response` и журналом `sent: list[str]` (пути, ушедшие в сеть — для оценки
  предела 60/ч в Task 18); `contents_bytes(transport, repo, path, ref) -> bytes | None |
  Unavailable` — байты файла в коммите через `repos/{o}/{r}/contents/{path}?ref={sha}`
  (`None` — установленно нет: 404 на существующий коммит; содержимое сверяется с git-oid `sha`
  ответа).

Правила (инструкция steward): только `GET` относительного пути, путь передаётся как есть (уже
закодирован библиотекой, может нести `?recursive=1` и путь `rel="next"`); любой HTTP-ответ, в том
числе не-2xx, — `Response(status, body, link)`; тело не JSON — `body=None`; сбой доставки —
`Response(None, error=…)`; исключений `send` не бросает. Без `Authorization`, без перенаправлений
(301 переименованного репо — статус, библиотека даст `http`), ответ ≤ 32 МиБ. Кэш — Р-3.

- [ ] **Step 1: Write the failing tests** — `tests/test_act_anon.py`:

```python
"""Анонимный транспорт: протокол steward без credentials (спека §6.2)."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

pytest.importorskip("steward")

from steward.approvalfacts import v3  # noqa: E402

from governance import act_anon  # noqa: E402
from governance.act_time import Unavailable  # noqa: E402

VECTORS = Path(__file__).resolve().parent.parent / "contracts/approval-facts/v3/vectors"


class _Answer(io.BytesIO):
    def __init__(self, status: int, body: object, link: str | None) -> None:
        super().__init__(json.dumps(body).encode() if body is not None else b"")
        self.status = status
        self.headers = Message()
        if link is not None:
            self.headers["Link"] = link


class _Fake:
    """Подмена `opener.open`: ответы по полному URL; журнал заголовков запросов."""

    def __init__(self, routes: dict[str, tuple[int, object, str | None]]) -> None:
        self.routes = routes
        self.headers: list[dict[str, str]] = []

    def __call__(self, req, timeout: float):  # noqa: ANN001
        self.headers.append(dict(req.header_items()))
        status, body, link = self.routes[req.full_url]
        if status >= 300:
            hdrs = Message()
            if link is not None:
                hdrs["Link"] = link
            raise urllib.error.HTTPError(
                req.full_url, status, "x", hdrs, io.BytesIO(json.dumps(body).encode())
            )
        return _Answer(status, body, link)


def _get(path: str) -> v3.Request:
    return v3.Request("GET", path)


def test_no_credentials_ever() -> None:
    fake = _Fake({act_anon.API + "repos/o/r": (200, {"default_branch": "main"}, None)})
    resp = act_anon.AnonTransport(open_url=fake).send(_get("repos/o/r"))
    assert resp.status == 200 and resp.body == {"default_branch": "main"}
    assert all("Authorization" not in h for h in fake.headers)


def test_non_2xx_is_a_response_not_an_exception() -> None:
    fake = _Fake({act_anon.API + "repos/o/r/commits/" + "a" * 40: (404, {"m": "x"}, None)})
    resp = act_anon.AnonTransport(open_url=fake).send(_get("repos/o/r/commits/" + "a" * 40))
    assert (resp.status, resp.body) == (404, {"m": "x"})


def test_redirect_is_not_followed() -> None:
    fake = _Fake({act_anon.API + "repos/o/r": (301, {"url": "elsewhere"}, None)})
    assert act_anon.AnonTransport(open_url=fake).send(_get("repos/o/r")).status == 301


def test_oversized_response_refused(monkeypatch) -> None:
    """b5 (`_response`): ответ больше предела — `Response(None, error=…)`, тело не читается."""
    monkeypatch.setattr(act_anon, "MAX_BODY", 8)
    fake = _Fake({act_anon.API + "repos/o/r": (200, {"x": "0123456789"}, None)})
    resp = act_anon.AnonTransport(open_url=fake).send(_get("repos/o/r"))
    assert resp.status is None and resp.error == "anon: ответ больше 8 байт"


def test_transport_failure_is_status_none() -> None:
    def broken(req, timeout):  # noqa: ANN001
        raise TimeoutError("slow")

    resp = act_anon.AnonTransport(open_url=broken).send(_get("repos/o/r"))
    assert resp.status is None and "TimeoutError" in resp.error


@pytest.mark.parametrize(
    "request_",
    [
        v3.Request("POST", "graphql", op="x", query="query{}"),
        v3.Request("GET", "/repos/o/r"),
        v3.Request("GET", "https://evil/x"),
        v3.Request("GET", "repos/o/r x"),
    ],
)
def test_only_relative_get(request_: v3.Request) -> None:
    resp = act_anon.AnonTransport(open_url=_Fake({})).send(request_)
    assert resp.status is None and "только GET относительного пути" in resp.error


def test_link_and_query_passed_through() -> None:
    path = "repos/o/r/commits/" + "b" * 40 + "/pulls?per_page=100"
    fake = _Fake({act_anon.API + path: (200, [], '<https://api.github.com/x?page=2>; rel="next"')})
    resp = act_anon.AnonTransport(open_url=fake).send(_get(path))
    assert resp.link == '<https://api.github.com/x?page=2>; rel="next"'


def test_memo_only_for_sha_addressed_paths() -> None:
    commit = "repos/o/r/commits/" + "c" * 40
    pulls = commit + "/pulls?per_page=100"
    fake = _Fake({act_anon.API + commit: (200, {}, None), act_anon.API + pulls: (200, [], None)})
    t = act_anon.AnonTransport(open_url=fake)
    for _ in range(2):
        t.send(_get(commit))
        t.send(_get(pulls))
    assert t.sent == [commit, pulls, pulls], "_IMMUTABLE: память — только пути по SHA"


def test_library_reads_activation_through_it() -> None:
    """Протокол совместим: steward читает активацию вектора через наш транспорт."""
    spec = json.loads((VECTORS / "activation/ok.json").read_text("utf-8"))
    routes = {
        act_anon.API + ex["request"]["path"]: (
            ex["response"]["status"],
            ex["response"].get("body"),
            ex["response"].get("link"),
        )
        for ex in spec["transport"]["exchanges"]
    }
    got = v3.read_activation(
        act_anon.AnonTransport(open_url=_Fake(routes)),
        spec["repo"],
        spec["merge_commit"],
        files=tuple(spec["files"]),
    )
    assert not isinstance(got, v3.Unavailable), got
    assert got.merge_commit == spec["merge_commit"]


def _contents(raw: bytes, *, sha: str | None = None) -> dict:
    oid = hashlib.sha1(b"blob %d\x00" % len(raw) + raw).hexdigest()
    return {
        "type": "file",
        "encoding": "base64",
        "content": base64.b64encode(raw).decode(),
        "sha": sha or oid,
    }


def test_contents_bytes_checked_against_blob_oid() -> None:
    ref = "d" * 40
    url = act_anon.API + f"repos/o/r/contents/policy/modes.yaml?ref={ref}"
    t = act_anon.AnonTransport(open_url=_Fake({url: (200, _contents(b"modes: {}\n"), None)}))
    assert act_anon.contents_bytes(t, "o/r", "policy/modes.yaml", ref) == b"modes: {}\n"
    bad = act_anon.AnonTransport(
        open_url=_Fake({url: (200, _contents(b"x", sha="e" * 40), None)})
    )
    got = act_anon.contents_bytes(bad, "o/r", "policy/modes.yaml", ref)
    assert isinstance(got, Unavailable) and "содержимое не равно blob" in got.reason


def test_contents_absent_vs_unreadable() -> None:
    ref = "d" * 40
    url = act_anon.API + f"repos/o/r/contents/epochs.yaml?ref={ref}"
    gone = act_anon.AnonTransport(open_url=_Fake({url: (404, {"message": "Not Found"}, None)}))
    assert act_anon.contents_bytes(gone, "o/r", "epochs.yaml", ref) is None
    limit = act_anon.AnonTransport(open_url=_Fake({url: (403, {"message": "rate"}, None)}))
    got = act_anon.contents_bytes(limit, "o/r", "epochs.yaml", ref)
    assert isinstance(got, Unavailable) and ": HTTP 403" in got.reason
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group governance pytest tests/test_act_anon.py -v`
Expected: FAIL — `ImportError: cannot import name 'act_anon' from 'governance'`.

- [ ] **Step 3: Write the transport**

```python
"""Анонимный REST-транспорт источников доверия (спека §6.2, steward §12 п. 1).

Реализация протокола `steward.approvalfacts.v3.Transport` БЕЗ credentials:
`GET https://api.github.com/<path>` без `Authorization`, без перенаправлений,
с пределом размера ответа. Любой HTTP-ответ — `Response(status, body,
link)`, классификацию делает библиотека; сбой доставки — `Response(None,
error=…)`; исключений `send` не бросает (инструкция steward, «Транспорт»).

Кэш (решение плана Р-3) — только в памяти процесса и только для путей,
неизменных по SHA: подменённый на диске ответ коммита скрыл бы прямой push,
а содержимое коммита по REST-ответу не проверить.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from governance.act_time import Unavailable

API = "https://api.github.com/"
MAX_BODY = 32 << 20
TIMEOUT = 30.0
#: Путь, который не относительный путь API: флаг, абсолютный, вложенный URL,
#: обратный слэш, пробельный символ (тот же класс, что у `GhTransport`).
_UNSAFE_PATH = re.compile(r"\A[-/]|://|\\|\s")
_IMMUTABLE = re.compile(
    r"\Arepos/[^/?]+/[^/?]+/(?:commits/[0-9a-f]{40}"
    r"|git/trees/[0-9a-f]{40}(?:\?recursive=1)?"
    r"|git/blobs/[0-9a-f]{40}"
    r"|contents/[^?]+\?ref=[0-9a-f]{40})\Z"
)
_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "devtools-act-anon",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _default_open() -> Callable[..., Any]:
    return urllib.request.build_opener(_NoRedirect).open


def _response(cls: Any, status: int, stream: Any, link: str | None) -> Any:
    raw = stream.read(MAX_BODY + 1)
    if len(raw) > MAX_BODY:
        return cls(None, error=f"anon: ответ больше {MAX_BODY} байт")
    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        body = None
    return cls(status, body=body, link=link)


class AnonTransport:
    """`Transport` steward без credentials; `sent` — пути, ушедшие в сеть."""

    def __init__(
        self, *, open_url: Callable[..., Any] | None = None, timeout: float = TIMEOUT
    ) -> None:
        self._open = open_url or _default_open()
        self._timeout = timeout
        self._memo: dict[str, Any] = {}
        self.sent: list[str] = []

    def send(self, request: Any) -> Any:
        """Один анонимный `GET`; не-`GET` и не относительный путь — `status=None`."""
        from steward.approvalfacts.v3 import Response

        path = getattr(request, "path", "")
        if (
            getattr(request, "method", "") != "GET"
            or not isinstance(path, str)
            or not path
            or _UNSAFE_PATH.search(path)
        ):
            return Response(None, error=f"anon: только GET относительного пути: {path!r}")
        if path in self._memo:
            return self._memo[path]
        resp = self._fetch(path, Response)
        if resp.status == 200 and _IMMUTABLE.match(path):
            self._memo[path] = resp
        return resp

    def _fetch(self, path: str, cls: Any) -> Any:
        self.sent.append(path)
        req = urllib.request.Request(API + path, method="GET", headers=_HEADERS)
        try:
            try:
                with self._open(req, timeout=self._timeout) as answer:
                    return _response(cls, answer.status, answer, answer.headers.get("Link"))
            except urllib.error.HTTPError as err:
                link = err.headers.get("Link") if err.headers is not None else None
                return _response(cls, err.code, err, link)
        except Exception as exc:  # noqa: BLE001 — send не бросает (инструкция steward)
            return cls(None, error=f"anon: {type(exc).__name__}: {exc}")


def contents_bytes(
    transport: Any, repo: str, path: str, ref: str
) -> bytes | None | Unavailable:
    """Байты `path` в коммите `ref`; `None` — установленно нет (404).

    `ref` — SHA уже прочитанного коммита: 404 тогда значит «пути нет», а не
    «нет коммита». Содержимое обязано хешироваться в git-oid `sha` ответа.
    """
    from steward.approvalfacts.v3 import Request

    owner, _, name = repo.partition("/")
    api = (
        f"repos/{quote(owner, safe='')}/{quote(name, safe='')}/contents/"
        f"{quote(path)}?ref={ref}"
    )
    where = f"{repo}@{ref[:12]}:{path}"
    resp = transport.send(Request("GET", api))
    if resp.status == 404:
        return None
    body = resp.body
    if resp.status != 200 or not isinstance(body, dict):
        return Unavailable(f"{where}: HTTP {resp.status} {resp.error or ''}".rstrip())
    if body.get("type") != "file" or body.get("encoding") != "base64":
        return Unavailable(f"{where}: не файл base64 (type={body.get('type')!r})")
    try:
        raw = base64.b64decode(str(body.get("content", "")).replace("\n", ""), validate=True)
    except (binascii.Error, ValueError):
        return Unavailable(f"{where}: содержимое не base64")
    if hashlib.sha1(b"blob %d\x00" % len(raw) + raw).hexdigest() != body.get("sha"):
        return Unavailable(f"{where}: содержимое не равно blob {body.get('sha')!r}")
    return raw
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --group governance pytest tests/test_act_anon.py -v`
Expected: PASS. ruff, pyrefly — чисто.

- [ ] **Step 5: Commit**

```bash
git add governance/act_anon.py tests/test_act_anon.py
git commit -m "feat(act): анонимный REST-транспорт источников доверия для steward v3"
```

### Task 12: Полная проверка доставки (`act_check`)

**Правки по релизу M0 (31.10).** Код задачи был в плане до M0; сверка с релизом меняет: фабрику
контекста (`tests/act_cases_facts.py` вместо `tests/act_check_cases.py` — имя попадает под
защищённый префикс `tests/act_cases`; отозванное ревью — в состоянии `DISMISSED`, иначе steward
отвечает `malformed` в обоих порядках (О13); мержер — в GraphQL-форме `df-reviewer` (О9));
`LegacyAccepted` несёт свидетельство старого кода (`evidence`, вне сравнения) — его записывает
`_reconcile_candidate` (Task 13); новый отказ `REQUEST_BINDING` — продукт манифеста обязан быть
репо доставки (заметки части A, п. 7); недостающие строки §8.1/§11.1 — полным кодом (раньше —
«тем же приёмом»). Порядок `_decide` и остальной код не меняются.

**Files:**
- Create: `governance/act_check.py`
- Create: `tests/act_cases_facts.py` (фабрика контекста; импортирует steward)
- Test: `tests/test_act_check.py`

**Interfaces:**
- Consumes: Tasks 2–8, 11a.
- Produces: `ActivationView(key: EventKey, merge_commit: str, at: datetime)` — ключ
  `("activation", merge_commit)`; **тот же ключ** у якоря эпохи (одно событие форджа — один
  ключ); `DeliveryContext(...)` (поля — в коде ниже); `OLD_READER = Admitted("old")`;
  коды отказа `EPOCH_GAP`, `MARKER_BEFORE_MODE`, `ADMISSION`, `NO_MANIFEST`,
  `POLICY_SOURCE`, `MIXED_POLICY`, `REQUEST_BINDING`, `POLICY_CHANGED` (=
  `approval_facts.INVALIDATION_POLICY_CHANGED`);
  `LegacyAccepted(merged_by, evidence=None)` — вердикт старого пути (`evidence` — `compare=False`); `ctx.legacy: LegacyAccepted |
  Rejected | Unavailable | None` — **готовый** вердикт старого кода потребителя;
  `check_delivery(ctx) -> Accepted | LegacyAccepted | Rejected | WaitingDelivery |
  Unavailable | Ambiguous`; `live_policy_refusal(pin: PolicyPin, current_activation: str)
  -> str | None`.

**Полный исход в каждом порядке (§8.2 п. 3).** Старый путь — не «маршрут», а вердикт: в каждом
порядке, где admission даёт legacy/merge, `_decide` возвращает `ctx.legacy`. Этот вердикт
**вычисляет сам потребитель его собственным старым кодом до перебора** (§10: legacy
«проверяется старым кодом … и прежними проверками»), без побочных эффектов: для узла —
`approve_node._legacy_outcome` (Task 14 Step 3a: все проверки `_reconcile_candidate` — опубликованный
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
4. `review`: манифест есть; `request.product` = продукт доставки (`ctx.product`); пин `repo/branch` = источник v2; `activation_commit` = активация на T;
   `files_sha256` = набор; набор прочитан (`PolicySet`, не `Unavailable`).
5. `act_facts.effective` → `Unavailable` сохраняется как `Unavailable`; затем `decide`.

- [ ] **Step 1: Write the failing tests**

Фабрика — в **отдельном** `tests/act_cases_facts.py` (импортирует steward; `act_cases.py`
остаётся без него):

```python
"""Фабрика контекста проверки доставки (Task 12); импортирует steward.

Отдельно от `tests/act_cases.py` (контракт импорта без governance); имя с
префиксом `tests/act_cases` — та же защищённая цепочка (authority-root и
`_HARNESS_PREFIXES`).
"""

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


def review(
    rid: str, login: str, hms: str | None, *, state: str = "APPROVED", commit: str = HEAD
) -> v3.Review:
    """Ревью в форме steward: логин бота без `[bot]` (О9)."""
    hint = "Bot" if login.startswith("df-") else "User"
    return v3.Review(rid, login, hint, state, commit, ts(hms) if hms else None)


def policy_files(mode: str | None, approvers: tuple[str, ...]) -> dict[str, bytes]:
    files = dict(FILES)
    modes = f"  {PRODUCT}: {mode}\n" if mode else "  owner/other: merge\n"
    files["policy/modes.yaml"] = (
        b"schema: approval-policy-modes/v1\nmodes:\n" + modes.encode()
    )
    humans = ", ".join(f"github:{a}" for a in approvers)
    files["policy/approvers.env"] = (
        f"AUTHORIZED_APPROVER_ACCOUNTS={','.join(approvers)}\n"
        "AUTHORIZED_MERGER_ACCOUNTS=df-reviewer[bot]\n"
    ).encode()
    files["policy/actors.yaml"] = (
        f"version: 1\nhuman_identities: [{humans}]\n"
        "agent_identities: [github:df-reviewer, github:ai-prosto]\n"
    ).encode()
    return files


def context(
    *,
    ranges: dict[int, tuple[ActivationView, ...] | Unavailable] | None = None,
    modes: dict[str, str | None] | None = None,
    pin: str = "p1",
    approve_at: str = "09:50:00",
    approve_commit: str = HEAD,
    reviews: tuple[v3.Review, ...] | None = None,
    dismissals: tuple[tuple[str, str], ...] = (),
    approvers: tuple[str, ...] = ("alice",),
    merged: bool = True,
    merged_at: str = "10:00:00",
    request_act: str | None = "review/v1",
    request_product: str = PRODUCT,
    epochs: tuple[Epoch, ...] = (Epoch(1, sha("p1")),),
    anchor_at: dict[str, datetime | Unavailable] | None = None,
    unreadable_sets: frozenset[str] = frozenset(),
    pin_digests: dict[str, str] | None = None,
    merger: tuple[str, str] = ("df-reviewer", "Bot"),
    blobs: dict[str, tuple[str | None, str]] | None = None,
    self_hash: str = DIGEST,
    legacy: object = None,
) -> DeliveryContext:
    ranges = ranges or {1: (act("p1", "08:00:00"),)}
    activations = [a for span in ranges.values() if isinstance(span, tuple) for a in span]
    modes = modes or {a.merge_commit: "review" for a in activations}
    sets: dict[str, PolicySet | Unavailable] = {
        a.merge_commit: (
            Unavailable("набор не прочитан")
            if a.merge_commit in unreadable_sets
            else parse_policy_set(
                a.merge_commit, policy_files(modes[a.merge_commit], approvers)
            )
        )
        for a in activations
    }
    pinned = sets.get(sha(pin))
    digests = pinned.files_sha256 if isinstance(pinned, PolicySet) else {}
    request = NodeRequest(
        "approve-2-1-1",
        "ws-7",
        request_product,
        {"requirements": NodeEntry(PATH, DIGEST, 1)},
        {"requirements": {}},
        PolicyPin(SOURCE.repo, SOURCE.branch, sha(pin), pin_digests or digests),
    )
    dismissed = {rid for rid, _ in dismissals}
    if reviews is None:
        state = "DISMISSED" if "11" in dismissed else "APPROVED"
        reviews = (review("11", "alice", approve_at, state=state, commit=approve_commit),)
    facts = v3.ReviewFacts(
        PRODUCT,
        7,
        HEAD,
        "main",
        "MERGED" if merged else "OPEN",
        reviews,
        tuple(v3.Dismissal(rid, "carol", ts(at), "APPROVED") for rid, at in dismissals),
    )
    delivery = (
        DeliveryView(
            True,
            "m" * 40,
            ts(merged_at),
            merger[0],
            merger[1],
            HEAD,
            True,
            blobs or {PATH: ("b" * 40, "b" * 40)},
        )
        if merged
        else DeliveryView(False, None, None, None, None, None, False, {})
    )
    return DeliveryContext(
        source=SOURCE,
        product=PRODUCT,
        request=request,
        request_act=request_act,
        head_sha=HEAD,
        delivery=delivery,
        facts=facts,
        head_self_hashes={PATH: self_hash},
        epochs=epochs,
        anchor_at=anchor_at or {sha("p1"): parse_ts(ts("08:00:00"))},
        ranges=ranges,
        policy_sets=sets,
        legacy=legacy,
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
    REQUEST_BINDING,
    LegacyAccepted,
    _requires,
    check_delivery,
    live_policy_refusal,
)
from governance.act_decision import Accepted, Rejected, WaitingDelivery  # noqa: E402
from governance.act_epochs import Epoch  # noqa: E402
from governance.act_request import PolicyPin  # noqa: E402
from governance.act_time import Ambiguous, Unavailable, parse_ts  # noqa: E402
from tests.act_cases import code  # noqa: E402
from tests.act_cases_facts import PATH, act, context, review, sha, ts  # noqa: E402

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


def test_new_head_after_approve_is_no_act() -> None:
    """Приёмка №2: approve на прежний коммит."""
    ctx = context(approve_commit="2" * 40)
    assert code(check_delivery(ctx)) == "d1_no_act"


def test_dismissed_before_merge_rejected() -> None:
    """§8.1 №2 / приёмка №3."""
    ctx = context(dismissals=(("11", "09:55:00"),))
    assert code(check_delivery(ctx)) == "d1_no_act"


def test_dismissed_after_merge_accepted() -> None:
    """§8.1 №3: отзыв после T на срезе отсутствует (previous_state)."""
    ctx = context(dismissals=(("11", "10:30:00"),))
    assert isinstance(check_delivery(ctx), Accepted)


def test_approver_outside_list_rejected() -> None:
    """Приёмка №4."""
    ctx = context(reviews=(review("11", "mallory", "09:50:00"),))
    assert code(check_delivery(ctx)) == "d1_no_act"


def test_not_merged_waits() -> None:
    """Приёмка №5."""
    assert check_delivery(context(merged=False)) == WaitingDelivery()


def test_agent_outside_merger_list() -> None:
    """Приёмка №6 и №16: агент (или machine user) вне AUTHORIZED_MERGER_ACCOUNTS."""
    assert code(check_delivery(context(merger=("ai-prosto", "User")))) == "d3_merger"
    assert code(check_delivery(context(merger=("ai-prosto", "Bot")))) == "d3_merger"


def test_old_reader_without_legacy_verdict_unavailable() -> None:
    """b4: OLD_READER без вычисленного вердикта старого кода — `Unavailable`, не отказ
    и не «принято» (`_legacy_verdict`)."""
    got = check_delivery(context(merged_at="07:00:00", request_act=None, legacy=None))
    assert isinstance(got, Unavailable) and got.reason == "вердикт старой проверки не вычислен"


def test_activations_out_of_order_unavailable() -> None:
    """b4: `merged_at` активаций против first-parent — диапазон не принимается (`_span`)."""
    ranges = {1: (act("p1", "09:00:00"), act("p2", "08:30:00"))}
    got = check_delivery(context(ranges=ranges))
    assert isinstance(got, Unavailable)
    assert got.reason == "merged_at активаций противоречит first-parent"


def test_before_bootstrap_unmarked_goes_to_old_reader() -> None:
    """Приёмка №7: доставка до якоря эпохи 1, заявка без маркера — старый код."""
    ctx = context(
        merged_at="07:00:00", request_act=None, legacy=LegacyAccepted("bob")
    )
    assert check_delivery(ctx) == LegacyAccepted("bob")


def test_before_bootstrap_marked_rejected() -> None:
    ctx = context(merged_at="07:00:00")
    assert code(check_delivery(ctx)) == "marker_before_mode"


def test_actors_only_change_is_policy_changed() -> None:
    """Приёмка №9: версия — активация, а не файл утверждающих."""
    ranges = {1: (act("p1", "08:00:00"), act("p2", "09:00:00"))}
    assert code(check_delivery(context(ranges=ranges))) == POLICY_CHANGED


def test_bytes_and_content_are_distinct_refusals() -> None:
    """Приёмка №18 (D2): I-bytes и I-content — разные исходы."""
    moved = context(blobs={PATH: ("c" * 40, "b" * 40)})
    assert code(check_delivery(moved)) == "i_bytes"
    deleted = context(blobs={PATH: (None, "b" * 40)})  # О1: удалён в merge-коммите
    assert code(check_delivery(deleted)) == "i_bytes"
    assert code(check_delivery(context(self_hash="z" * 40))) == "i_content"


@pytest.mark.parametrize(
    ("modes", "expected"),
    [
        ({"p1": "review", "p2": None}, ADMISSION),  # запись удалена → "" (п. 4, №26)
        ({"p1": "review", "p2": "merge"}, Unavailable),  # обратное переключение
    ],
)
def test_mode_entry_removed_or_reverted(modes: dict, expected: object) -> None:
    ranges = {1: (act("p1", "08:00:00"), act("p2", "09:00:00"))}
    ctx = context(
        ranges=ranges, modes={sha(k): v for k, v in modes.items()}, pin="p2"
    )
    got = check_delivery(ctx)
    if expected is Unavailable:
        assert isinstance(got, Unavailable)
    else:
        assert code(got) == expected


def test_mixed_policy_refused() -> None:
    """Приёмка №32: files_sha256 пина не от набора активации."""
    ctx = context(pin_digests={"policy/approvers.env": "0" * 64})
    assert code(check_delivery(ctx)) == "mixed_policy"


def test_other_product_switch_does_not_touch_merge_product() -> None:
    """Приёмка №38: режим считается по продукту доставки."""
    ctx = context(
        modes={sha("p1"): "merge"}, request_act=None, legacy=LegacyAccepted("bob")
    )
    assert check_delivery(ctx) == LegacyAccepted("bob")


def test_live_pin_against_current_activation() -> None:
    """Приёмка №44 (живое правило; вызывает Task 13/15)."""
    pin = PolicyPin("owner/approval-policy", "main", sha("p1"), {})
    assert live_policy_refusal(pin, sha("p1")) is None
    assert live_policy_refusal(pin, sha("p2")).startswith(POLICY_CHANGED)


def test_unrelated_dismissal_in_merge_second_accepted() -> None:
    """Приёмка №46a: отзыв ревью неавторизованного логина в секунду T."""
    reviews = (
        review("11", "alice", "09:50:00"),
        review("12", "mallory", "09:40:00", state="DISMISSED"),
    )
    ctx = context(reviews=reviews, dismissals=(("12", "10:00:00"),))
    assert isinstance(check_delivery(ctx), Accepted)


def test_pending_review_ignored() -> None:
    """Приёмка №48."""
    reviews = (
        review("11", "alice", "09:50:00"),
        review("12", "alice", None, state="PENDING"),
    )
    assert isinstance(check_delivery(context(reviews=reviews)), Accepted)


def test_both_approvals_dismissed_at_t_ambiguous() -> None:
    """Приёмка №49: среди порядков есть «оба отзыва до T» — акта нет."""
    reviews = (
        review("11", "alice", "09:50:00", state="DISMISSED"),
        review("12", "bob", "09:51:00", state="DISMISSED"),
    )
    ctx = context(
        reviews=reviews,
        approvers=("alice", "bob"),
        dismissals=(("11", "10:00:00"), ("12", "10:00:00")),
    )
    assert check_delivery(ctx) == Ambiguous()


def test_manifest_product_must_be_delivery_repo() -> None:
    """Заметки части A, п. 7: манифест чужого продукта не судится чужой политикой."""
    ctx = context(request_product="owner/other")
    assert code(check_delivery(ctx)) == REQUEST_BINDING


def test_requires_orders_review_and_same_second_activations() -> None:
    """Заметки части A, п. 3: отправка → отзыв; активации одной секунды — first-parent."""
    ranges = {1: (act("p1", "08:00:00"), act("p2", "09:00:00"), act("p3", "09:00:00"))}
    ctx = context(ranges=ranges, dismissals=(("11", "09:55:00"),))
    req = _requires(ctx)
    assert req[("dismissal", "11")] == frozenset({("review", "11")})
    assert req[("activation", sha("p3"))] == frozenset({("activation", sha("p2"))})
    assert ("activation", sha("p2")) not in req
```

Строки, которые решаются не в `check_delivery`: §8.1 №6, №8 и приёмка №8/№13/№44 — живое
правило и зеркала (Task 13); №11/№21/№28/№29/№33–37/№45 — доверенный контекст (Task 12a);
№19 (время активации = `merged_at` PR) — Task 12a
(`test_anchor_time_from_activation_not_commit_date`); №10/№25 — аудит и остановка (Task 13);
№17/№23/№31 — конверт и история (Task 14).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group governance pytest tests/test_act_check.py -v`
Expected: FAIL — `ImportError: cannot import name 'ActivationView'` (фабрика импортирует
`governance.act_check`)

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
from dataclasses import dataclass, field
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
#: Манифест называет не тот продукт, что доставил PR (заметки части A, п. 7).
REQUEST_BINDING = "request_binding"
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
    """Старый путь: старый код потребителя признал доставку подписью.

    `evidence` — то, что старый код запишет (`MergeEvent`, `Authorization`,
    Task 13); в вердикт не входит (§8.2: сравниваются исходы).
    """

    merged_by: str
    evidence: object = field(default=None, compare=False)

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
    if ctx.request.product != ctx.product:
        return Rejected(
            REQUEST_BINDING,
            f"манифест называет {ctx.request.product}, доставил {ctx.product}",
        )
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --group governance pytest tests/test_act_check.py tests/test_act_*.py -v`
Expected: PASS. Без группы: `uv run pytest tests/ -q` — `act_*` тесты со steward
`SKIPPED (steward)`, collection без ошибок.

- [ ] **Step 5: Commit**

```bash
git add governance/act_check.py tests/test_act_check.py tests/act_cases_facts.py
git commit -m "feat(act): полная проверка доставки на срезе T — эпоха, admission, пин, D1–D3"
```

### Task 12a: Доверенный контекст — корень `df-trust`, периметр, диапазоны эпох

**Files:**
- Create: `governance/act_trust.py` (привязка, снимок `df-trust`, самоотчёты, ruleset,
  `allow_rebase_merge`, время якорей, диапазоны и наборы ≤ T)
- Create: `governance/act_journal.py` (журнал событий периметра для потребителя; **не** входит в
  цепочку `act_premerge` — импортирует `df/`)
- Create: `governance/act_context.py` (решение по доставке поверх доверенного контекста — общее
  для Task 13 и Task 14)
- Create: `governance/act_identity.py` (идентичность судимого объекта — ревизия b1)
- Create: `governance/act_journal_wire.py` (узкий клиент журнала для `act_premerge`, без `df/` —
  ревизия b1)
- Modify: `governance/act_perimeter.py` — удаляются маркер-файлы части A (`marker_path`,
  `_marked_epoch`, `_write_marker`, `record_violation`, `blocked`, `PerimeterError`): механизм
  заменён журналом событий (спека A rev 16 §6.3); остаются `Violation`, `permissions_refusal`
- Modify: `tests/test_act_perimeter.py` — удаляются тесты маркеров (строки `:26–112`, `:151–162`),
  тесты `permissions_refusal` остаются
- Test: `tests/test_act_trust.py`, `tests/test_act_journal.py`, `tests/test_act_context.py`,
  `tests/test_act_identity.py`, `tests/test_act_journal_wire.py`

**Interfaces:**
- Consumes: Tasks 1, 2, 4, 9 (`parse_source`, `parse_journal`, `check_transition`,
  `parse_policy_set`, `permissions_refusal`, `Violation`), 11a (`act_facts`), 11b (`act_anon`),
  12 (`ActivationView`, `DeliveryContext`, `check_delivery`); этап 2a установщика —
  `df.publisher.perimeter` (`create_db`, `open_db`, `admit`, `record_violation`,
  `perimeter_status`, `EpochSource`, `Snapshot`), `df.publisher.request.parse`,
  `df.perimeter_client` (`status`, `sudo_call`, `RootId`, `TrustView`), `df.stage2_config.TrustBinding`.
- Produces (`act_trust`):
  `BINDING_PATH = Path("/etc/df/trust.toml")`; `Binding(host, df_trust_repository_id,
  df_trust_full_name, df_trust_branch, approval_policy_repository_id, transport)`;
  `parse_binding(text) -> Binding` (`TrustError`); `load_binding(path=None, *,
  owner_uid=0) -> Binding | None | Unavailable` (`None` — файла нет; потребители режима
  читают это как «режим не установлен», не legacy — ревизия b1, B1-5; `path=None` —
  `BINDING_PATH` на момент вызова);
  `TrustRoot(host, repository_id, branch, snapshot_sha)` с `.id_tuple`;
  `TrustSnapshot(root, full_name, source, epochs)`;
  `read_trust_snapshot(anon, binding) -> TrustSnapshot | Unavailable`;
  `Credential(label, gh_config_dir=None, token=None)`; `credentials(env, *, agent_dir, merge_dir=None)`;
  `gh_get(cred, path) -> Fact[object]` (самоотчёт от credential);
  `Readers(anon, as_credential)`, `default_readers()`;
  `Journal` (protocol: `status(snap) -> "clear" | "blocked" | STALE | Unavailable`,
  `record(snap, source, detail) -> Unavailable | None`); `STALE = "stale_snapshot"`;
  `Trust(snapshot, unverified: tuple[str, ...])` + свойства `source`, `epochs`, `root`;
  `load_trust(readers, binding, journal, *, creds) -> Trust | Unavailable` (журнал обязателен —
  ревизия b1);
  `load_anchor_times(anon, trust, t) -> dict[str, datetime | Unavailable]`;
  `load_ranges(anon, trust, anchor_at, t, journal) -> tuple[dict[int, Span | Unavailable],
  dict[str, PolicySet | Unavailable]] | Unavailable`;
  `policy_set(anon, source, activation: ActivationFact) -> PolicySet | Unavailable`;
  `current_activation(trust, ranges) -> ActivationView | Unavailable`;
  `PRE_MIGRATION`; `product_mode(trust, ranges, sets, product, now) -> str | Unavailable`
  (`PRE_MIGRATION` | `merge` | `review`; удаление записи, откат `review → merge`, недочитанная
  нужная история — `Unavailable`).
- Produces (`act_journal`): `ForgeEpochs(reread)` (реализация `df.publisher.perimeter.EpochSource`
  поверх `read_trust_snapshot` — её же подключает этап 3 установщика, handoff в отчёте);
  `DevJournal(state_home, run_id, reread, policy_repository_id)`; `InstalledJournal(df_run_id)`;
  `journal_for(*, state_home, run_id, reread, env, policy_repository_id) -> Journal | Unavailable`
  (Р-5; id approval-policy — из привязки, B1-18).
- Produces (`act_context`): `delivery_verdict(readers, trust, journal, *, product, pr, paths,
  request, request_act, head_sha, facts_transport, self_hashes, legacy) -> tuple[Outcome,
  DeliveryView | None]`.

Обязательные проверки `load_trust` перед **каждым** решением, по порядку (спека A rev 16 §6.1–6.3;
любое недоступное обязательное чтение → `Unavailable` без события периметра):
1. Привязка (`load_binding`; не env, не CLI, не рабочее дерево) → `read_trust_snapshot`:
   `repositories/{df_trust_repository_id}` (имя = `df_trust_full_name`, `private: false`),
   `repositories/{approval_policy_repository_id}` (имя = `APPROVAL_POLICY_REPO` из `source.env`
   снимка), голова **закреплённой** ветки (`branches/{df_trust_branch}`; default branch не
   читается), first-parent обход до корня (≤ 500 звеньев), версии `epochs.yaml` по
   `contents?ref=<звено>`: первая — ровно одна открытая эпоха 1 с якорем `BOOTSTRAP_ACTIVATION`,
   каждая следующая — `check_transition`; удаление файла после появления — нарушение журнала.
   SHA снимка — в `TrustRoot`. `act_source.load_source()` не вызывается.
2. Журнал событий периметра (обязателен у каждого потребителя, включая `act_premerge` — узкий
   клиент `act_journal_wire`, ревизия b1): `blocked` → `Unavailable`; `STALE` → перечитать снимок и
   повторить (не более трёх снимков).
3. Самоотчёт **каждого** credential процесса (`credentials`: профиль агента, фактический профиль
   мержа, `GH_TOKEN`, `GITHUB_TOKEN`; совпадающие — один раз; с Task 11c — и `profile:history`) на **обоих** репо доверия:
   `permissions_refusal` → `Violation` (у любого credential, перекрывает чужие `Unavailable`) →
   событие + `Unavailable`; иначе `Unavailable` → `Unavailable` без события.
4. Ruleset закреплённой ветки каждого репо доверия: типы правил ⊇ {`deletion`,
   `non_fast_forward`, `pull_request`}, иначе нарушение (с указанием репо); `bypass_actors` не
   читается и вывод «bypass нет» не делается (№33).
5. `allow_rebase_merge` у `approval-policy` (анонимно; с Task 11c — токеном истории, если он
   настроен): четыре состояния (раздел «Решения
   владельца», п. 1); `None`/нет ключа → `Trust.unverified = ("allow_rebase_merge",)`.

`load_anchor_times(t)`: время якоря **каждой** эпохи — `merged_at` из `read_activation` (№19);
недоступное время — значение `Unavailable`, которое мешает лишь решению, зависящему от него (e7;
ревизия b1, B1-10: admission при `merge`/без записи требует истории режима прежних эпох).
`load_ranges(t)`: история всех эпох с якорем `≤ t`; `HistoryBreak` в диапазоне **любой** эпохи —
событие периметра + `Unavailable` (§6.3; ревизия b1 сняла исключение закрытых эпох); наборы
политик — **только** активаций с `merged_at ≤ t` (набор после T не читается: §6, §8.1 №5); байты
набора — из того же `merge_commit` и сверяются с `files_sha256` steward (смешение → `Unavailable`,
№32); отсутствующий в активации файл (О3) → `Unavailable`.

Порядок вызовов: историческая доставка — `load_trust` → `load_anchor_times(merged_at)` →
`load_ranges(merged_at)` (это делает `delivery_verdict`); открытый PR (`act_node`, `act_premerge`)
— то же с `t = now`, текущая активация = `current_activation`, режим продукта сейчас —
`product_mode` (Task 14 `live_policy`): без привязки, при удалённой записи, откате или
недочитанной истории — `Unavailable` и стоп, а не legacy (ревизия b1, B1-5; привязка — предусловие
развёртывания части B).

- [ ] **Step 1: Write the failing tests**

`tests/test_act_trust.py`:

```python
"""Доверенный контекст (спека A rev 16 §6.1–6.5; приёмка №19, 28, 29, 33–36, 39, 45)."""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

import pytest

pytest.importorskip("steward")

from steward.approvalfacts import v3  # noqa: E402

from governance import act_facts, act_source, act_trust  # noqa: E402
from governance.act_epochs import Epoch  # noqa: E402
from governance.act_time import Unavailable, parse_ts  # noqa: E402
from governance.facts import Fact, Outcome, unavailable  # noqa: E402

DT = "owner/df-trust"
AP = "owner/approval-policy"
BOOT = "b" * 40
C0, C1, C2 = "0" * 40, "1" * 40, "2" * 40
SOURCE_ENV = (
    f"APPROVAL_POLICY_REPO={AP}\nAPPROVAL_POLICY_BRANCH=main\n"
    f"BOOTSTRAP_ACTIVATION={BOOT}\nPOLICY_ADMINS=owner\n"
)
J1 = f"schema: approval-policy-epochs/v1\nepochs:\n  - epoch: 1\n    anchor: {BOOT}\n"
J2 = (
    "schema: approval-policy-epochs/v1\nepochs:\n"
    f"  - epoch: 1\n    anchor: {BOOT}\n    last_confirmed: {BOOT}\n"
    "    valid_until: '2026-10-20T00:00:00Z'\n    closed_reason: x\n"
    f"  - epoch: 2\n    anchor: {'c' * 40}\n"
)
BINDING_TOML = (
    'schema = "df/trust/v1"\nhost = "github.com"\ndf_trust_repository_id = 11\n'
    f'df_trust_full_name = "{DT}"\ndf_trust_branch = "main"\n'
    'approval_policy_repository_id = 22\ntransport = "public"\n'
)


def _file(text: str) -> dict:
    raw = text.encode()
    oid = hashlib.sha1(b"blob %d\x00" % len(raw) + raw).hexdigest()
    return {"type": "file", "encoding": "base64",
            "content": base64.b64encode(raw).decode(), "sha": oid}


class Anon:
    """Анонимный транспорт стенда: путь → (статус, тело); журнал путей."""

    def __init__(self, routes: dict[str, tuple[int, object]]) -> None:
        self.routes = routes
        self.sent: list[str] = []

    def send(self, request: v3.Request) -> v3.Response:
        self.sent.append(request.path)
        if request.path not in self.routes:
            return v3.Response(None, error="stand: unexpected")
        status, body = self.routes[request.path]
        return v3.Response(status, body=body)


def trust_routes(*journals: str | None, rules: list[str] | None = None,
                 rebase: object = None, rebase_present: bool = True) -> dict:
    chain = [C0, C1, C2][: len(journals)]
    routes: dict[str, tuple[int, object]] = {
        "repositories/11": (200, {"full_name": DT, "private": False}),
        "repositories/22": (200, {"full_name": AP, "private": False}),
        f"repos/{DT}/branches/main": (200, {"commit": {"sha": chain[-1]}}),
        f"repos/{DT}/contents/source.env?ref={chain[-1]}": (200, _file(SOURCE_ENV)),
    }
    for i, sha in enumerate(chain):
        parents = [{"sha": chain[i - 1]}] if i else []
        routes[f"repos/{DT}/commits/{sha}"] = (200, {"sha": sha, "parents": parents})
        text = journals[i]
        routes[f"repos/{DT}/contents/epochs.yaml?ref={sha}"] = (
            (404, {"message": "Not Found"}) if text is None else (200, _file(text))
        )
    kinds = rules if rules is not None else ["deletion", "non_fast_forward", "pull_request"]
    for repo in (DT, AP):
        routes[f"repos/{repo}/rules/branches/main"] = (200, [{"type": k} for k in kinds])
    repo_body: dict = {"full_name": AP}
    if rebase_present:
        repo_body["allow_rebase_merge"] = rebase
    routes[f"repos/{AP}"] = (200, repo_body)
    return routes


def binding() -> act_trust.Binding:
    return act_trust.parse_binding(BINDING_TOML)


class Journal:
    def __init__(self, states: list[object] | None = None) -> None:
        self.states = states or ["clear"]
        self.records: list[tuple[str, str]] = []

    def status(self, snap: act_trust.TrustSnapshot) -> object:
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]

    def record(self, snap: act_trust.TrustSnapshot, source: str, detail: str) -> None:
        self.records.append((source, detail))


def _perms(push: object = False) -> dict:
    return {"permissions": {"push": push, "maintain": False, "admin": False}}


def as_credential(answers: dict[tuple[str, str], Fact[object]]):
    def read(cred: act_trust.Credential, path: str) -> Fact[object]:
        return answers.get((cred.label, path), Fact(Outcome.FOUND, _perms(), "ok"))

    return read


AGENT = act_trust.Credential("profile:agent", gh_config_dir=Path("/x/review"))


def load(routes: dict, journal: Journal | None = None, creds=(AGENT,), answers=None):
    readers = act_trust.Readers(Anon(routes), as_credential(answers or {}))
    return act_trust.load_trust(readers, binding(), journal or Journal(), creds=creds)


# --- привязка (Р-6; без Р-1 — ревизия b1) -----------------------------------


def test_binding_parse_agrees_with_installer() -> None:
    from df.stage2_config import parse_trust

    ours, theirs = binding(), parse_trust(BINDING_TOML)
    assert (ours.host, ours.df_trust_repository_id, ours.df_trust_full_name,
            ours.df_trust_branch, ours.approval_policy_repository_id) == (
        theirs.host, theirs.df_trust_repository_id, theirs.df_trust_full_name,
        theirs.df_trust_branch, theirs.approval_policy_repository_id)


@pytest.mark.parametrize(
    ("text", "match"),
    [(BINDING_TOML.replace('"public"', '"private"'), "только transport"),
     (BINDING_TOML + "extra = 1\n", "ключи"),
     (BINDING_TOML.replace("df_trust_repository_id = 11", "df_trust_repository_id = 0"),
      "положительные целые")],
    ids=["transport", "keys", "ids"],
)
def test_binding_rejects_bad_text(text: str, match: str) -> None:
    with pytest.raises(act_trust.TrustError, match=match):
        act_trust.parse_binding(text)


def test_binding_absent_is_none(tmp_path: Path) -> None:
    assert act_trust.load_binding(tmp_path / "trust.toml", owner_uid=os.getuid()) is None


def test_binding_symlink_or_foreign_owner_refused(tmp_path: Path) -> None:
    real = tmp_path / "real.toml"
    real.write_text(BINDING_TOML, encoding="utf-8")
    link = tmp_path / "trust.toml"
    link.symlink_to(real)
    assert isinstance(act_trust.load_binding(link, owner_uid=os.getuid()), Unavailable)
    foreign = act_trust.load_binding(real, owner_uid=os.getuid() + 1)
    assert isinstance(foreign, Unavailable) and "не файл root" in foreign.reason
    assert isinstance(act_trust.load_binding(real, owner_uid=os.getuid()), act_trust.Binding)


# --- снимок df-trust (§6.1 п. 1, §6.5, №45) --------------------------------


def test_snapshot_reads_both_files_from_one_sha() -> None:
    anon = Anon(trust_routes(None, J1, J2))
    snap = act_trust.read_trust_snapshot(anon, binding())
    assert isinstance(snap, act_trust.TrustSnapshot)
    assert snap.root == act_trust.TrustRoot("github.com", 11, "main", C2)
    assert [e.epoch for e in snap.epochs] == [1, 2]
    assert snap.source.bootstrap_activation == BOOT
    assert f"repos/{DT}/contents/source.env?ref={C2}" in anon.sent
    assert not any("default_branch" in p for p in anon.sent)


def test_rewritten_closed_epoch_in_history_unavailable() -> None:
    """Приёмка №45: любая мутация, кроме атомарного закрытия."""
    broken = J2.replace(f"anchor: {BOOT}\n    last", f"anchor: {'d' * 40}\n    last")
    snap = act_trust.read_trust_snapshot(Anon(trust_routes(J1, broken)), binding())
    assert isinstance(snap, Unavailable) and "недопустимый переход журнала" in snap.reason


def test_first_version_must_be_bootstrap() -> None:
    snap = act_trust.read_trust_snapshot(Anon(trust_routes(J2)), binding())
    assert isinstance(snap, Unavailable) and "не bootstrap" in snap.reason


def test_journal_deleted_after_appearing_unavailable() -> None:
    snap = act_trust.read_trust_snapshot(Anon(trust_routes(J1, None)), binding())
    assert isinstance(snap, Unavailable) and "epochs.yaml удалён" in snap.reason


@pytest.mark.parametrize(
    ("patch", "reason"),
    [({"repositories/11": (200, {"full_name": "evil/df-trust", "private": False})},
      "привязка —"),
     ({"repositories/11": (200, {"full_name": DT, "private": True})}, "только публичные"),
     ({"repositories/22": (200, {"full_name": "evil/approval-policy", "private": False})},
      "привязка —")],
    ids=["df_trust_name", "private_root", "policy_name"],
)
def test_root_substitution_refused(patch: dict, reason: str) -> None:
    """§6.5: иной repository_id/имя, приватный корень, source.env на чужой источник."""
    routes = {**trust_routes(None, J1), **patch}
    got = act_trust.read_trust_snapshot(Anon(routes), binding())
    assert isinstance(got, Unavailable) and reason in got.reason


def test_branch_head_unreadable_is_unavailable() -> None:
    """b4: ответ форджи не 200 — `_get` отказывает с путём и кодом."""
    routes = {**trust_routes(None, J1), f"repos/{DT}/branches/main": (500, None)}
    got = act_trust.read_trust_snapshot(Anon(routes), binding())
    assert isinstance(got, Unavailable) and "branches/main: HTTP 500" in got.reason


def test_branch_head_not_named_is_unavailable() -> None:
    """b4: ответ о ветке без SHA головы — снимок не читается дальше."""
    routes = {**trust_routes(None, J1), f"repos/{DT}/branches/main": (200, {"commit": {}})}
    got = act_trust.read_trust_snapshot(Anon(routes), binding())
    assert isinstance(got, Unavailable) and "голова main не прочитана" in got.reason


def test_commit_answer_for_other_sha_unavailable() -> None:
    """b4: ответ о другом коммите в цепочке first-parent — `_chain` отказывает."""
    routes = trust_routes(J1, J1)
    routes[f"repos/{DT}/commits/{C1}"] = (200, {"sha": C0, "parents": []})
    got = act_trust.read_trust_snapshot(Anon(routes), binding())
    assert isinstance(got, Unavailable) and "ответ не о том коммите" in got.reason


def test_gh_get_failures_are_unavailable(monkeypatch) -> None:
    """b4: `gh api` упал / ответ не JSON — `Unavailable` с путём и credential."""
    class Done:
        def __init__(self, rc, out="", err=""):
            self.returncode, self.stdout, self.stderr = rc, out, err

    cred = act_trust.Credential("profile:agent", gh_config_dir=Path("/x/review"))
    monkeypatch.setattr(act_trust.subprocess, "run", lambda *a, **k: Done(1, err="boom"))
    failed = act_trust.gh_get(cred, "repos/o/p")
    assert failed.outcome is Outcome.UNAVAILABLE and "(profile:agent): boom" in failed.detail
    monkeypatch.setattr(act_trust.subprocess, "run", lambda *a, **k: Done(0, out="{"))
    garbled = act_trust.gh_get(cred, "repos/o/p")
    assert garbled.outcome is Outcome.UNAVAILABLE and "ответ не JSON" in garbled.detail


def test_unreadable_history_version_unavailable() -> None:
    routes = trust_routes(None, J1)
    routes[f"repos/{DT}/contents/epochs.yaml?ref={C0}"] = (403, {"message": "rate"})
    assert isinstance(act_trust.read_trust_snapshot(Anon(routes), binding()), Unavailable)


def test_load_source_not_called(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden() -> None:
        raise AssertionError("load_source в пути решения")

    monkeypatch.setattr(act_source, "load_source", forbidden)
    assert isinstance(load(trust_routes(None, J1)), act_trust.Trust)


# --- периметр (§6.2 б, §6.3) ------------------------------------------------


def test_clean_trust_carries_root_and_premise() -> None:
    trust = load(trust_routes(None, J1, rebase=None), Journal())
    assert isinstance(trust, act_trust.Trust)
    assert trust.root.snapshot_sha == C1
    assert trust.unverified == ("allow_rebase_merge",)


def test_blocked_journal_blocks() -> None:
    """Приёмка №36: событие периметра сохраняет блокировку."""
    got = load(trust_routes(None, J1), Journal(["blocked"]))
    assert isinstance(got, Unavailable) and "периметр заблокирован" in got.reason


def test_stale_snapshot_reloads() -> None:
    journal = Journal([act_trust.STALE, "clear"])
    got = load(trust_routes(None, J1), journal)
    assert isinstance(got, act_trust.Trust)
    assert journal.states == ["clear"]  # STALE израсходован: снимок перечитан


def test_token_with_push_records_and_blocks() -> None:
    """Приёмка №29, №34 (обнаруживший потребитель)."""
    journal = Journal()
    tok = act_trust.Credential("env:GH_TOKEN", token="t")
    answers = {("env:GH_TOKEN", f"repos/{AP}"): Fact(Outcome.FOUND, _perms(True), "ok")}
    got = load(trust_routes(None, J1), journal, creds=(AGENT, tok), answers=answers)
    assert isinstance(got, Unavailable) and got.reason.startswith("нарушение периметра: ")
    assert journal.records[0][0] == "approval-policy"
    assert journal.records[0][1].startswith("env:GH_TOKEN на ")


def test_merge_profile_with_push_on_df_trust() -> None:
    """P5-2: профиль review чист, профиль мержа с push на df-trust."""
    journal = Journal()
    merge = act_trust.Credential("profile:merge", gh_config_dir=Path("/x/merge"))
    answers = {("profile:merge", f"repos/{DT}"): Fact(Outcome.FOUND, _perms(True), "ok")}
    got = load(trust_routes(None, J1), journal, creds=(AGENT, merge), answers=answers)
    assert isinstance(got, Unavailable) and journal.records[0][0] == "df-trust"
    assert journal.records[0][1].startswith("profile:merge на ")


def test_violation_wins_over_other_unavailable() -> None:
    journal = Journal()
    tok = act_trust.Credential("env:GH_TOKEN", token="t")
    answers = {
        ("profile:agent", f"repos/{AP}"): unavailable("404"),
        ("env:GH_TOKEN", f"repos/{DT}"): Fact(Outcome.FOUND, _perms(True), "ok"),
    }
    load(trust_routes(None, J1), journal, creds=(AGENT, tok), answers=answers)
    assert journal.records and journal.records[0][0] == "df-trust"


def test_self_report_404_is_unavailable_without_record() -> None:
    journal = Journal()
    answers = {("profile:agent", f"repos/{DT}"): unavailable("HTTP 404")}
    got = load(trust_routes(None, J1), journal, answers=answers)
    assert isinstance(got, Unavailable) and journal.records == []
    assert got.reason.startswith("самоотчёт profile:agent")


def test_ruleset_without_pull_request_is_violation() -> None:
    journal = Journal()
    got = load(trust_routes(None, J1, rules=["deletion", "non_fast_forward"]), journal)
    assert isinstance(got, Unavailable) and journal.records and "нет правил" in journal.records[0][1]


def test_missing_bypass_field_not_read() -> None:
    """Приёмка №33: рантайм не делает вывода «bypass нет» — поле не запрашивается."""
    anon = Anon(trust_routes(None, J1))
    act_trust.load_trust(act_trust.Readers(anon, as_credential({})), binding(), Journal(),
                         creds=(AGENT,))
    assert not any("rulesets" in p or "bypass" in p for p in anon.sent)


def test_issue_signal_not_part_of_protocol() -> None:
    """Приёмка №39: issues не читаются вовсе."""
    anon = Anon(trust_routes(None, J1))
    act_trust.load_trust(act_trust.Readers(anon, as_credential({})), binding(), Journal(),
                         creds=(AGENT,))
    assert not any("/issues" in p for p in anon.sent)


def test_rebase_false_is_verified() -> None:
    trust = load(trust_routes(None, J1, rebase=False), Journal())
    assert trust.unverified == ()


def test_rebase_true_is_violation() -> None:
    journal = Journal()
    assert isinstance(load(trust_routes(None, J1, rebase=True), journal), Unavailable)
    assert journal.records and journal.records[0][1].endswith("allow_rebase_merge = true")


@pytest.mark.parametrize("present", [True, False])
def test_rebase_hidden_is_unverified_premise(present: bool) -> None:
    trust = load(trust_routes(None, J1, rebase=None, rebase_present=present), Journal())
    assert isinstance(trust, act_trust.Trust)  # тип — до обращения к полям (B3-11)
    assert trust.unverified == ("allow_rebase_merge",)


@pytest.mark.parametrize("answer", [(500, {"m": "x"}), (200, []), (200, {"allow_rebase_merge": "false"})])
def test_rebase_unreadable_is_unavailable(answer: tuple) -> None:
    journal = Journal()
    routes = trust_routes(None, J1)
    routes[f"repos/{AP}"] = answer
    assert isinstance(load(routes, journal), Unavailable) and journal.records == []


def test_credentials_deduplicate_and_list_what_process_holds(tmp_path: Path) -> None:
    review = tmp_path / "review"
    got = act_trust.credentials(
        {"GH_TOKEN": "a", "GITHUB_TOKEN": "a"}, agent_dir=review, merge_dir=review
    )
    assert [c.label for c in got] == ["profile:agent", "env:GH_TOKEN"]


# --- якоря и диапазоны (№19, №28, e7, §6; без Р-4 — ревизия b1) ------------


def _trust(epochs: tuple[Epoch, ...], source_repo: str = AP, branch: str = "main",
           anchor: str = BOOT) -> act_trust.Trust:
    snap = act_trust.TrustSnapshot(
        act_trust.TrustRoot("github.com", 11, "main", C1), DT,
        act_source.SourceV2(source_repo, branch, anchor, frozenset({"owner"})), epochs,
    )
    return act_trust.Trust(snap, ())


def _activation(sha: str, at: str) -> act_facts.ActivationFact:
    return act_facts.ActivationFact(1, sha, parse_ts(at), {})


def test_anchor_time_from_activation_not_commit_date(monkeypatch) -> None:
    """Приёмка №19: время якоря — merged_at PR, а не дата коммита."""
    monkeypatch.setattr(
        act_facts, "activation", lambda t, s, sha: _activation(sha, "2026-10-12T08:00:00Z")
    )
    times = act_trust.load_anchor_times(Anon({}), _trust((Epoch(1, BOOT),)),
                                        parse_ts("2026-10-12T10:00:00Z"))
    assert times == {BOOT: parse_ts("2026-10-12T08:00:00Z")}


def test_old_anchor_unavailable_is_kept_not_blocking(monkeypatch) -> None:
    """e7 + B1-10: все якоря читаются; недоступный старый — значение, а не отказ."""
    def activation(t, s, sha):  # noqa: ANN001
        if sha == BOOT:
            return Unavailable("object_missing")
        return _activation(sha, "2026-10-12T08:00:00Z")

    monkeypatch.setattr(act_facts, "activation", activation)
    epochs = (Epoch(1, BOOT, BOOT, "2026-10-11T00:00:00Z", "x"), Epoch(2, "c" * 40))
    times = act_trust.load_anchor_times(Anon({}), _trust(epochs),
                                        parse_ts("2026-10-12T10:00:00Z"))
    assert isinstance(times[BOOT], Unavailable)
    assert times["c" * 40] == parse_ts("2026-10-12T08:00:00Z")


def _history_case(name: str) -> tuple[dict, act_trust.Trust]:
    import json

    root = Path(__file__).resolve().parent.parent
    spec = json.loads((root / "contracts/approval-facts/v3/vectors/history" / name)
                      .read_text("utf-8"))
    epochs = (Epoch(1, spec["anchor"]),)
    return spec, _trust(epochs, spec["repo"], spec["branch"], spec["anchor"])


@pytest.mark.parametrize(
    "name",
    ["rebase_multi_commit.json", "rebase_multi_commit_unassociated.json", "direct_push.json"],
)
def test_history_violation_records_event(name: str) -> None:
    """Приёмка №28 (многокоммитный rebase-merge), №21 (прямой push): HistoryViolation
    открытой эпохи → событие периметра, Unavailable."""
    spec, trust = _history_case(name)
    journal = Journal()
    got = act_trust.load_ranges(
        v3.replay_transport(spec["transport"]), trust,
        {spec["anchor"]: parse_ts("2026-01-01T00:00:00Z")},
        parse_ts("2027-01-01T00:00:00Z"), journal,
    )
    assert isinstance(got, Unavailable) and journal.records[0][0] == "approval-policy"
    assert got.reason.startswith("нарушение периметра")


def test_single_commit_rebase_merge_not_detected(monkeypatch) -> None:
    """Приёмка №28, предел: однокоммитный rebase-merge принят, как squash."""
    spec, trust = _history_case("rebase_single_commit_accepted.json")
    monkeypatch.setattr(act_trust, "policy_set", lambda anon, source, a: Unavailable("stub"))
    journal = Journal()
    got = act_trust.load_ranges(
        v3.replay_transport(spec["transport"]), trust,
        {spec["anchor"]: parse_ts("2026-01-01T00:00:00Z")},
        parse_ts("2027-01-01T00:00:00Z"), journal,
    )
    assert not isinstance(got, Unavailable) and journal.records == []
    assert isinstance(got[0][1], tuple)


def test_closed_epoch_break_registers_event(monkeypatch) -> None:
    """§6.3 (ревизия b1, B1-4): нарушение формы истории в диапазоне ЗАКРЫТОЙ
    подтверждённой эпохи — тоже событие периметра и отказ."""
    monkeypatch.setattr(act_facts, "history",
                        lambda t, s, a, u: act_facts.HistoryBreak("commit_without_pr: x")
                        if u else (act_facts.ActivationFact(1, a, parse_ts("2026-10-12T08:00:00Z"), {}),))
    monkeypatch.setattr(act_trust, "policy_set", lambda anon, source, a: Unavailable("stub"))
    epochs = (Epoch(1, BOOT, BOOT, "2026-10-11T00:00:00Z", "x"), Epoch(2, "c" * 40))
    journal = Journal()
    got = act_trust.load_ranges(
        Anon({}), _trust(epochs),
        {BOOT: parse_ts("2026-10-01T00:00:00Z"), "c" * 40: parse_ts("2026-10-12T08:00:00Z")},
        parse_ts("2026-10-12T10:00:00Z"), journal,
    )
    assert isinstance(got, Unavailable)
    assert journal.records == [("approval-policy", "commit_without_pr: x")]


def test_unknown_anchor_time_makes_range_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(act_facts, "history", lambda t, s, a, u: pytest.fail("read"))
    got = act_trust.load_ranges(Anon({}), _trust((Epoch(1, BOOT),)),
                                {BOOT: Unavailable("object_missing")},
                                parse_ts("2026-10-12T10:00:00Z"), Journal())
    assert isinstance(got[0][1], Unavailable)
    assert got[0][1].reason == "якорь эпохи 1: object_missing"


# --- режим продукта сейчас (§10; ревизия b1, B1-5) ----------------------------------


def _set(mode: str | None) -> object:
    from governance.act_policy import parse_policy_set
    from tests.act_cases import FILES

    modes = f"  owner/p: {mode}\n" if mode else "  owner/other: merge\n"
    return parse_policy_set(BOOT, {**FILES, "policy/modes.yaml":
                                   ("schema: approval-policy-modes/v1\nmodes:\n" + modes).encode()})


def _view(name: str, hms: str):
    from governance.act_check import ActivationView

    sha = f"{name:0<40}"
    return ActivationView(("activation", sha), sha, parse_ts(f"2026-10-12T{hms}Z"))


def _mode(modes: list[str | None], *, epochs=None, ranges=None):
    views = [_view(f"p{i}", f"0{i}:00:00") for i in range(len(modes))]
    sets = {v.merge_commit: _set(m) for v, m in zip(views, modes)}
    trust = _trust(epochs or (Epoch(1, views[0].merge_commit),))
    return act_trust.product_mode(trust, ranges or {1: tuple(views)}, sets, "owner/p",
                                  parse_ts("2026-10-12T12:00:00Z"))


def test_mode_never_entered_is_pre_migration() -> None:
    assert _mode([None, None]) == act_trust.PRE_MIGRATION


def test_current_activation_is_last_of_open_epoch_or_unavailable() -> None:
    """B3-2: «текущая активация» — последняя активация ОТКРЫТОЙ эпохи; пустая или
    недочитанная открытая эпоха — `Unavailable`, не активация закрытой."""
    v0, v1, v2 = _view("p0", "01:00:00"), _view("p1", "02:00:00"), _view("p2", "03:00:00")
    epochs = (Epoch(1, v0.merge_commit, v0.merge_commit, "2026-10-12T02:30:00Z", "x"),
              Epoch(2, v2.merge_commit))
    trust = _trust(epochs)
    assert act_trust.current_activation(trust, {1: (v0, v1), 2: (v2,)}) == v2
    empty = act_trust.current_activation(trust, {1: (v0, v1), 2: ()})
    assert isinstance(empty, Unavailable) and empty.reason == "в открытой эпохе нет активаций"
    assert isinstance(act_trust.current_activation(
        trust, {1: (v0, v1), 2: Unavailable("x")}), Unavailable)


def test_mode_current_values() -> None:
    assert _mode([None, "merge"]) == "merge"
    assert _mode([None, "merge", "review"]) == "review"


@pytest.mark.parametrize(("modes", "reason"), [
    (["merge", None], "удалён после первой записи"),
    (["review", None], "удалён после первой записи"),
    (["merge", "review", "merge"], "обратное переключение review → merge"),
], ids=["merge_removed", "review_removed", "rolled_back"])
def test_mode_removed_or_rolled_back_is_unavailable(modes: list, reason: str) -> None:
    got = _mode(modes)
    assert isinstance(got, Unavailable) and reason in got.reason


def test_mode_history_with_unread_set_is_unavailable() -> None:
    """b4: набор активации в истории режима не прочитан — `_modes` отказывает."""
    views = [_view("p0", "00:00:00"), _view("p1", "01:00:00")]
    sets = {views[0].merge_commit: _set("merge")}  # набора p1 нет
    got = act_trust.product_mode(_trust((Epoch(1, views[0].merge_commit),)),
                                 {1: tuple(views)}, sets, "owner/p",
                                 parse_ts("2026-10-12T12:00:00Z"))
    assert isinstance(got, Unavailable) and got.reason.startswith(f"набор {views[1].merge_commit[:12]}")


def test_mode_needs_prior_epoch_unless_review() -> None:
    v1, v2 = _view("p0", "01:00:00"), _view("p1", "02:00:00")
    epochs = (Epoch(1, v1.merge_commit, v1.merge_commit, "2026-10-12T01:30:00Z", "x"),
              Epoch(2, v2.merge_commit))
    sets = {v1.merge_commit: _set("review"), v2.merge_commit: _set("merge")}
    now = parse_ts("2026-10-12T12:00:00Z")
    gone = {1: Unavailable("object_missing"), 2: (v2,)}
    got = act_trust.product_mode(_trust(epochs), gone, sets, "owner/p", now)
    assert isinstance(got, Unavailable) and got.reason.startswith("история режима, эпоха 1")
    full = {1: (v1,), 2: (v2,)}
    assert isinstance(act_trust.product_mode(_trust(epochs), full, sets, "owner/p", now),
                      Unavailable)  # review в E1, merge в E2 — откат через границу эпох
    sets[v2.merge_commit] = _set("review")
    assert act_trust.product_mode(_trust(epochs), gone, sets, "owner/p", now) == "review"


def test_history_read_failure_no_event(monkeypatch) -> None:
    monkeypatch.setattr(act_facts, "history", lambda t, s, a, u: Unavailable("http: 403"))
    journal = Journal()
    got = act_trust.load_ranges(Anon({}), _trust((Epoch(1, BOOT),)),
                                {BOOT: parse_ts("2026-10-12T08:00:00Z")},
                                parse_ts("2026-10-12T10:00:00Z"), journal)
    assert isinstance(got[0][1], Unavailable) and journal.records == []


def test_policy_sets_after_t_not_loaded(monkeypatch) -> None:
    acts = (_activation(BOOT, "2026-10-12T08:00:00Z"),
            _activation("e" * 40, "2026-10-12T11:00:00Z"))
    monkeypatch.setattr(act_facts, "history", lambda t, s, a, u: acts)
    loaded: list[str] = []
    monkeypatch.setattr(act_trust, "policy_set",
                        lambda anon, source, a: loaded.append(a.merge_commit) or Unavailable("x"))
    act_trust.load_ranges(Anon({}), _trust((Epoch(1, BOOT),)),
                          {BOOT: parse_ts("2026-10-12T08:00:00Z")},
                          parse_ts("2026-10-12T10:00:00Z"), Journal())
    assert loaded == [BOOT]


def _policy_routes(files: dict[str, str], commit: str) -> dict:
    return {f"repos/{AP}/contents/{name}?ref={commit}": (200, _file(text))
            for name, text in files.items()}


POLICY = {
    "policy/approvers.env": "AUTHORIZED_APPROVER_ACCOUNTS=alice\n"
                            "AUTHORIZED_MERGER_ACCOUNTS=df-reviewer[bot]\n",
    "policy/modes.yaml": "schema: approval-policy-modes/v1\nmodes:\n  owner/p: review\n",
    "policy/actors.yaml": "version: 1\nhuman_identities: [github:alice]\n"
                          "agent_identities: [github:df-reviewer]\n",
}


def _fact(files: dict[str, str]) -> act_facts.ActivationFact:
    digests = {k: hashlib.sha256(v.encode()).hexdigest() for k, v in files.items()}
    return act_facts.ActivationFact(1, BOOT, parse_ts("2026-10-12T08:00:00Z"), digests)


def test_policy_set_from_one_commit() -> None:
    got = act_trust.policy_set(Anon(_policy_routes(POLICY, BOOT)),
                               _trust((Epoch(1, BOOT),)).source, _fact(POLICY))
    assert not isinstance(got, Unavailable) and got.modes == {"owner/p": "review"}


def test_policy_bytes_not_matching_activation_is_mixing() -> None:
    """Приёмка №32 для читателя: байты не те, что посчитал steward."""
    other = {**POLICY, "policy/modes.yaml": POLICY["policy/modes.yaml"] + "# x\n"}
    got = act_trust.policy_set(Anon(_policy_routes(other, BOOT)),
                               _trust((Epoch(1, BOOT),)).source, _fact(POLICY))
    assert isinstance(got, Unavailable) and got.reason.startswith("смешение версий")


def test_activation_without_actors_file_unavailable() -> None:
    """О3: отсутствующий файл не входит в files_sha256 → набор неполон."""
    partial = {k: v for k, v in POLICY.items() if k != "policy/actors.yaml"}
    got = act_trust.policy_set(Anon(_policy_routes(POLICY, BOOT)),
                               _trust((Epoch(1, BOOT),)).source, _fact(partial))
    assert isinstance(got, Unavailable) and got.reason.endswith(": нет policy/actors.yaml")
```

`tests/test_act_journal.py` (журнал событий в режиме разработки — то же хранилище, что у издателя
этапа 2a; клиент установки проверяется подменой `sudo_call`):

```python
"""Журнал событий периметра потребителя (спека A rev 16 §6.3; Р-5)."""

from __future__ import annotations

import json
from pathlib import Path

from df.publisher import request as preq
from governance import act_journal, act_trust
from governance.act_epochs import Epoch
from governance.act_source import SourceV2
from governance.act_time import Unavailable

ROOT = act_trust.TrustRoot("github.com", 11, "main", "1" * 40)


def snap(epoch: int = 1, sha: str = "1" * 40) -> act_trust.TrustSnapshot:
    epochs = tuple(Epoch(i, "a" * 40) for i in range(1, epoch + 1))
    root = act_trust.TrustRoot("github.com", 11, "main", sha)
    return act_trust.TrustSnapshot(root, "owner/df-trust",
                                   SourceV2("owner/ap", "main", "a" * 40, frozenset({"o"})),
                                   epochs)


class Head:
    """Источник эпох стенда: текущий снимок корня или недоступность."""

    def __init__(self, current: object) -> None:
        self.current = current

    def __call__(self) -> object:
        return self.current


POLICY_ID = 22


def journal(tmp_path: Path, head: Head) -> act_journal.DevJournal:
    return act_journal.DevJournal(tmp_path.resolve(), "run-1", head, POLICY_ID)


def test_event_names_the_violated_repository(tmp_path: Path) -> None:
    """B1-18: событие роли approval-policy несёт id approval-policy, не df-trust."""
    import sqlite3

    j = journal(tmp_path, Head(snap(1)))
    j.record(snap(1), "approval-policy", "push у токена")
    j.record(snap(1), "df-trust", "push у профиля мержа")
    db = j.layout.perimeter_dir / "events.sqlite"
    rows = sqlite3.connect(db).execute(
        "SELECT violated_role, violated_repository_id FROM events ORDER BY event_id"
    ).fetchall()
    assert rows == [("approval-policy", POLICY_ID), ("df-trust", 11)]


def test_clear_then_blocked_then_new_epoch(tmp_path: Path) -> None:
    head = Head(snap(1))
    j = journal(tmp_path, head)
    assert j.status(snap(1)) == "clear"
    assert j.record(snap(1), "approval-policy", "push у токена") is None
    assert j.status(snap(1)) == "blocked"
    head.current = snap(2, "2" * 40)
    assert j.status(snap(2, "2" * 40)) == "clear"


def test_pending_blocks_until_epochs_known(tmp_path: Path) -> None:
    """Приёмка №36: журнал эпох недоступен при регистрации → pending, блокирует."""
    head = Head(Unavailable("df-trust: 403"))
    j = journal(tmp_path, head)
    assert j.record(snap(1), "df-trust", "x") is None
    assert isinstance(j.status(snap(1)), Unavailable)
    head.current = snap(1)
    assert j.status(snap(1)) == "blocked"  # pending завершён номером 1, блокирует
    head.current = snap(2, "2" * 40)
    assert j.status(snap(2, "2" * 40)) == "clear"


def test_stale_snapshot_reported(tmp_path: Path) -> None:
    head = Head(snap(1, "2" * 40))
    assert journal(tmp_path, head).status(snap(1)) == act_trust.STALE


def test_corrupted_store_blocks(tmp_path: Path) -> None:
    """Заметки части A, п. 12: битое хранилище — fail-closed, новая эпоха не снимает."""
    head = Head(snap(1))
    j = journal(tmp_path, head)
    j.status(snap(1))
    db = j.layout.perimeter_dir / "events.sqlite"
    db.write_bytes(b"not a database")
    got = j.status(snap(1))
    assert isinstance(got, Unavailable) and got.reason.startswith("журнал периметра (dev): ")
    head.current = snap(2, "2" * 40)
    assert isinstance(j.status(snap(2, "2" * 40)), Unavailable)


def test_status_answer_about_other_snapshot_unavailable() -> None:
    """B3-6: ответ журнала не о снимке решения (другой sha или эпоха) — не `clear`."""
    for result in ({"state": "clear", "snapshot_sha": "9" * 40, "last_epoch": 1},
                   {"state": "clear", "snapshot_sha": "1" * 40, "last_epoch": 2}):
        got = act_journal._status_of(preq.Response("done", None, result), snap(1))
        assert isinstance(got, Unavailable) and got.reason == "периметр: ответ не о снимке решения"
    clear = {"state": "clear", "snapshot_sha": "1" * 40, "last_epoch": 1}
    assert act_journal._status_of(preq.Response("done", None, clear), snap(1)) == "clear"
    odd = act_journal._status_of(preq.Response("done", "x", {"state": "odd"}), snap(1))
    assert isinstance(odd, Unavailable) and odd.reason == "периметр: x"


def test_forge_epochs_refuse_other_root() -> None:
    """b4: источник эпох отвечает только о своём корне."""
    epochs = act_journal.ForgeEpochs(Head(snap(1)))
    other = epochs.head(("github.com", 99, "main"))
    assert isinstance(other, act_journal.store.Unavailable) and other.reason == "root_mismatch"
    gone = act_journal.ForgeEpochs(Head(Unavailable("403")))
    lost = gone.head(ROOT.id_tuple)
    assert isinstance(lost, act_journal.store.Unavailable) and lost.reason == "df-trust: 403"


def test_dev_record_refused_by_store_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    """b4: хранилище отказало в записи события — `Unavailable` с его причиной."""
    j = journal(tmp_path, Head(snap(1)))
    j.status(snap(1))

    def refuse(*a, **k):
        raise act_journal.store.Unavailable("integrity")

    monkeypatch.setattr(act_journal.store, "record_violation", refuse)
    got = j.record(snap(1), "df-trust", "x")
    assert isinstance(got, Unavailable) and got.reason == "журнал периметра (dev): integrity"


def test_installed_record_refused_is_unavailable(monkeypatch) -> None:
    """b4: издатель не принял событие — `Unavailable` с исходом и причиной."""
    def call(data: bytes, layout=None, *, operation: str, timeout: float = 60.0) -> bytes:
        resp = {"schema": "df/publisher-response/v1", "outcome": "refused",
                "reason": "busy", "result": {}, "evidence": {}}
        return (json.dumps(resp) + "\n").encode()

    monkeypatch.setattr(act_journal.perimeter_client, "sudo_call", call)
    got = act_journal.InstalledJournal("run-7").record(snap(1), "df-trust", "x")
    assert isinstance(got, Unavailable) and got.reason == "record-violation: refused: busy"


def test_deleted_store_with_marker_is_lost(tmp_path: Path) -> None:
    head = Head(snap(1))
    j = journal(tmp_path, head)
    j.status(snap(1))
    (j.layout.perimeter_dir / "events.sqlite").unlink()
    got = j.status(snap(1))
    assert isinstance(got, Unavailable) and "lost" in got.reason


def test_installed_journal_requires_df_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(act_journal, "_publisher_present", lambda: True)
    got = act_journal.journal_for(state_home=tmp_path, run_id="r", reread=Head(snap()),
                                  env={}, policy_repository_id=POLICY_ID)
    assert isinstance(got, Unavailable) and "DF_RUN_ID" in got.reason
    dev = act_journal.journal_for(state_home=tmp_path, run_id="r", reread=Head(snap()),
                                  env={"DF_RUN_ID": "run-7"}, policy_repository_id=POLICY_ID)
    assert isinstance(dev, act_journal.InstalledJournal)


def test_dev_journal_without_publisher(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(act_journal, "_publisher_present", lambda: False)
    got = act_journal.journal_for(state_home=tmp_path, run_id="r", reread=Head(snap()),
                                  env={"DF_RUN_ID": "ignored"}, policy_repository_id=POLICY_ID)
    assert isinstance(got, act_journal.DevJournal)


def test_installed_record_sends_publisher_request(monkeypatch) -> None:
    sent: list[tuple[bytes, str]] = []

    def call(data: bytes, layout=None, *, operation: str, timeout: float = 60.0) -> bytes:
        sent.append((data, operation))
        resp = {"schema": "df/publisher-response/v1", "outcome": "done", "reason": None,
                "result": {"event_id": 1, "state": "registered", "epoch": 1},
                "evidence": {}}
        return (json.dumps(resp) + "\n").encode()

    monkeypatch.setattr(act_journal.perimeter_client, "sudo_call", call)
    assert act_journal.InstalledJournal("run-7").record(snap(1), "df-trust", "x") is None
    data, op = sent[0]
    req = preq.parse("record-violation", data)
    assert op == "record-violation" and req.fields["source"] == "df-trust"
```

`tests/test_act_context.py`:

```python
"""Решение по доставке поверх доверенного контекста (общий вход Task 13/14)."""

from __future__ import annotations

import pytest

pytest.importorskip("steward")

from governance import act_context, act_facts, act_trust  # noqa: E402
from governance.act_check import LegacyAccepted  # noqa: E402
from governance.act_decision import Accepted, DeliveryView, WaitingDelivery  # noqa: E402
from governance.act_epochs import Epoch  # noqa: E402
from governance.act_policy import parse_policy_set  # noqa: E402
from governance.act_time import Unavailable, parse_ts  # noqa: E402
from tests.act_cases_facts import (  # noqa: E402
    HEAD,
    PATH,
    act,
    context,
    policy_files,
    sha,
    ts,
)

NOT_MERGED = DeliveryView(False, None, None, None, None, None, False, {})


def _trust(ctx) -> act_trust.Trust:
    snap = act_trust.TrustSnapshot(act_trust.TrustRoot("github.com", 11, "main", "1" * 40),
                                   "owner/df-trust", ctx.source, ctx.epochs)
    return act_trust.Trust(snap, ())


def _wire(monkeypatch, ctx, *, merged: bool = True) -> None:
    delivery = ctx.delivery if merged else NOT_MERGED
    monkeypatch.setattr(act_facts, "read",
                        lambda t, repo, pr, paths: (delivery, ctx.facts if merged else None))
    monkeypatch.setattr(act_trust, "load_anchor_times", lambda anon, trust, t: dict(ctx.anchor_at))
    monkeypatch.setattr(act_trust, "load_ranges",
                        lambda anon, trust, anchors, t, journal: (dict(ctx.ranges),
                                                                  dict(ctx.policy_sets)))


def _verdict(ctx, **kw):
    readers = act_trust.Readers(object(), lambda c, p: None)
    args = dict(product=ctx.product, pr=7, paths=[PATH], request=ctx.request,
                request_act=ctx.request_act, head_sha=HEAD, facts_transport=object(),
                self_hashes=lambda: dict(ctx.head_self_hashes), legacy=None)
    args.update(kw)
    return act_context.delivery_verdict(readers, _trust(ctx), None, **args)


def test_happy_path(monkeypatch) -> None:
    ctx = context()
    _wire(monkeypatch, ctx)
    outcome, delivery = _verdict(ctx)
    assert isinstance(outcome, Accepted) and delivery is ctx.delivery


def test_not_merged_waits_without_reading_ranges(monkeypatch) -> None:
    ctx = context()
    _wire(monkeypatch, ctx, merged=False)
    monkeypatch.setattr(act_trust, "load_ranges", lambda *a: pytest.fail("ranges"))
    assert _verdict(ctx)[0] == WaitingDelivery()


def test_legacy_computed_only_for_unmarked(monkeypatch) -> None:
    ctx = context(modes={sha("p1"): "merge"}, request_act=None)
    _wire(monkeypatch, ctx)
    calls: list[int] = []
    outcome, _ = _verdict(ctx, request=None, request_act=None,
                          legacy=lambda: calls.append(1) or LegacyAccepted("bob"))
    assert outcome == LegacyAccepted("bob") and calls == [1]


def test_unavailable_self_hash_is_unavailable(monkeypatch) -> None:
    ctx = context()
    _wire(monkeypatch, ctx)
    got = _verdict(ctx, self_hashes=lambda: Unavailable("хеш не вычислен"))[0]
    assert isinstance(got, Unavailable) and got.reason == "хеш не вычислен"


def test_ranges_unavailable_is_unavailable(monkeypatch) -> None:
    ctx = context(ranges={1: (act("p1", "08:00:00"),)})
    _wire(monkeypatch, ctx)
    monkeypatch.setattr(act_trust, "load_ranges", lambda *a: Unavailable("history"))
    got = _verdict(ctx)[0]
    assert isinstance(got, Unavailable) and got.reason == "history"


def test_loader_supplies_prior_epochs_for_merge_admission(monkeypatch) -> None:
    """B1-10: при `merge` на T admission требует историю эпохи 1 — настоящий
    загрузчик (`load_anchor_times` + `load_ranges`) её даёт; раньше — Unavailable."""
    p0, p1 = sha("p0"), sha("p1")
    epochs = (Epoch(1, p0, p0, ts("07:00:00"), "разрыв"), Epoch(2, p1))
    times = {p0: parse_ts(ts("06:00:00")), p1: parse_ts(ts("08:00:00"))}
    monkeypatch.setattr(act_facts, "activation",
                        lambda t, s, c: act_facts.ActivationFact(1, c, times[c], {}))
    monkeypatch.setattr(act_facts, "history",
                        lambda t, s, a, u: (act_facts.ActivationFact(1, a, times[a], {}),))
    monkeypatch.setattr(act_trust, "policy_set", lambda anon, s, a: parse_policy_set(
        a.merge_commit, policy_files("merge", ("alice",))))
    ctx = context(request_act=None)
    monkeypatch.setattr(act_facts, "read", lambda t, repo, pr, paths: (ctx.delivery, ctx.facts))
    snap = act_trust.TrustSnapshot(act_trust.TrustRoot("github.com", 11, "main", "1" * 40),
                                   "owner/df-trust", ctx.source, epochs)
    outcome, _ = act_context.delivery_verdict(
        act_trust.Readers(object(), lambda c, p: None), act_trust.Trust(snap, ()), None,
        product=ctx.product, pr=7, paths=[], request=None, request_act=None, head_sha=HEAD,
        facts_transport=object(), self_hashes=lambda: {}, legacy=lambda: LegacyAccepted("bob"),
    )
    assert outcome == LegacyAccepted("bob")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group governance pytest tests/test_act_trust.py tests/test_act_journal.py tests/test_act_context.py -v`
Expected: FAIL — `ImportError: cannot import name 'act_trust' from 'governance'`.

- [ ] **Step 3: Write `act_trust`**

```python
"""Доверенный контекст решения: корень `df-trust`, периметр, эпохи (спека A rev 16 §6).

Перед КАЖДЫМ решением: привязка `/etc/df/trust.toml` → снимок `df-trust`
(SHA фиксируется; `source.env` и все версии `epochs.yaml` — из него) →
журнал событий периметра (если передан) → самоотчёт каждого credential →
ruleset обоих репо доверия → `allow_rebase_merge`; затем лениво — время
якорей и диапазоны эпох с наборами ≤ T. Недоступное обязательное чтение —
`Unavailable` без события; событие — только для установленного нарушения.
`act_source.load_source()` (файл devtools) здесь не вызывается (§6.5).

Модуль в цепочке обвязки мержа (`act_premerge`): только `governance/act_*`,
`governance/facts`, `ssot_env`, stdlib. Журнал периметра — протокол
`Journal`; реализации (`act_journal`) тянут `df/` и сюда не импортируются.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from governance import act_facts
from governance.act_anon import AnonTransport, contents_bytes
from governance.act_check import ActivationView
from governance.act_epochs import Epoch, JournalError, check_transition, parse_journal
from governance.act_perimeter import Violation, permissions_refusal
from governance.act_policy import (
    POLICY_FILES,
    PolicyError,
    PolicySet,
    mode_for,
    parse_policy_set,
)
from governance.act_source import SourceError, SourceV2, parse_source
from governance.act_time import Unavailable
from governance.facts import Fact, Outcome, unavailable

BINDING_PATH = Path("/etc/df/trust.toml")
BINDING_SCHEMA = "df/trust/v1"
SUPPORTED_HOST = "github.com"
JOURNAL_FILE = "epochs.yaml"
SOURCE_FILE = "source.env"
MAX_CHAIN = 500
MAX_BINDING = 64 << 10
STALE = "stale_snapshot"
REQUIRED_RULES = frozenset({"deletion", "non_fast_forward", "pull_request"})
REBASE_PREMISE = "allow_rebase_merge"
_BINDING_KEYS = (
    "host",
    "df_trust_repository_id",
    "df_trust_full_name",
    "df_trust_branch",
    "approval_policy_repository_id",
    "transport",
)
Span = tuple[ActivationView, ...]


class TrustError(ValueError):
    """Привязка корня доверия не читается — отказ конфигурации."""


@dataclass(frozen=True)
class Binding:
    """Защищённая привязка `/etc/df/trust.toml` (спека B §3.7; Р-6)."""

    host: str
    df_trust_repository_id: int
    df_trust_full_name: str
    df_trust_branch: str
    approval_policy_repository_id: int
    transport: str


@dataclass(frozen=True)
class TrustRoot:
    """Идентичность и снимок корня доверия — в evidence каждого решения."""

    host: str
    repository_id: int
    branch: str
    snapshot_sha: str

    @property
    def id_tuple(self) -> tuple[str, int, str]:
        """Пространство журнала событий периметра (спека §6.3)."""
        return (self.host, self.repository_id, self.branch)


@dataclass(frozen=True)
class TrustSnapshot:
    """`source.env` и журнал эпох из ОДНОГО зафиксированного снимка."""

    root: TrustRoot
    full_name: str
    source: SourceV2
    epochs: tuple[Epoch, ...]


@dataclass(frozen=True)
class Trust:
    """Доверенный контекст решения; `unverified` — непроверенные предпосылки."""

    snapshot: TrustSnapshot
    unverified: tuple[str, ...]

    @property
    def source(self) -> SourceV2:
        return self.snapshot.source

    @property
    def epochs(self) -> tuple[Epoch, ...]:
        return self.snapshot.epochs

    @property
    def root(self) -> TrustRoot:
        return self.snapshot.root


@dataclass(frozen=True)
class Credential:
    """Credential, который процесс ДЕРЖИТ: профиль `gh` либо токен окружения."""

    label: str
    gh_config_dir: Path | None = None
    token: str | None = None


class Journal(Protocol):
    """Журнал событий периметра установки (реализации — `act_journal`)."""

    def status(self, snap: TrustSnapshot) -> str | Unavailable:
        """`clear` | `blocked` | `STALE` (снимок сдвинулся) | недоступно."""
        ...

    def record(self, snap: TrustSnapshot, source: str, detail: str) -> Unavailable | None:
        """Зарегистрировать нарушение; номер эпохи назначает писатель."""
        ...


@dataclass(frozen=True)
class Readers:
    """Инжектируемые чтения: анонимный REST и GET от имени credential."""

    anon: Any
    as_credential: Callable[[Credential, str], Fact[object]]


def default_readers() -> Readers:
    """Боевые чтения: `AnonTransport` и `gh api` под credential."""
    return Readers(AnonTransport(), gh_get)


# --- привязка -------------------------------------------------------------------


def parse_binding(text: str) -> Binding:
    """Строгий разбор `trust.toml` (те же ключи, что `df.stage2_config`)."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise TrustError(f"trust.toml не TOML: {exc}") from exc
    if data.get("schema") != BINDING_SCHEMA:
        raise TrustError(f"trust.toml: схема не {BINDING_SCHEMA}")
    if set(data) - {"schema"} != set(_BINDING_KEYS):
        raise TrustError(f"trust.toml: ключи {sorted(data)}, нужны {list(_BINDING_KEYS)}")
    ids = ("df_trust_repository_id", "approval_policy_repository_id")
    if any(type(data[k]) is not int or data[k] <= 0 for k in ids):
        raise TrustError("trust.toml: repository_id — положительные целые")
    texts = ("host", "df_trust_full_name", "df_trust_branch")
    if any(not isinstance(data[k], str) or not data[k] for k in texts):
        raise TrustError("trust.toml: host/имя/ветка — непустые строки")
    if data["transport"] != "public":
        raise TrustError("trust.toml: v1 поддерживает только transport = \"public\"")
    return Binding(**{k: data[k] for k in _BINDING_KEYS})


def load_binding(
    path: Path | None = None, *, owner_uid: int = 0
) -> Binding | None | Unavailable:
    """Привязка: обычный файл, без ссылок, владелец `owner_uid`, не пишется
    группой/всеми. Файла нет — `None` (режим продукта не установлен, не
    legacy — ревизия b1); иное — `Unavailable`.

    `path=None` — `BINDING_PATH`, прочитанный в момент вызова (тесты подменяют
    атрибут модуля, чтобы машина разработчика с настоящей привязкой не
    уводила их в сеть)."""
    path = BINDING_PATH if path is None else path
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return Unavailable(f"привязка {path}: {exc}")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != owner_uid or st.st_mode & 0o022:
            return Unavailable(f"привязка {path}: не файл root без записи группы/всех")
        raw = os.read(fd, MAX_BINDING + 1)
    except OSError as exc:
        return Unavailable(f"привязка {path}: {exc}")
    finally:
        os.close(fd)
    try:
        return parse_binding(raw.decode("utf-8"))
    except (TrustError, UnicodeDecodeError) as exc:
        return Unavailable(f"привязка {path}: {exc}")


# --- снимок df-trust --------------------------------------------------------------


def _get(anon: Any, path: str) -> dict | list | Unavailable:
    from steward.approvalfacts.v3 import Request

    resp = anon.send(Request("GET", path))
    if resp.status != 200 or not isinstance(resp.body, (dict, list)):
        return Unavailable(f"{path}: HTTP {resp.status} {resp.error or ''}".rstrip())
    return resp.body


def _repo_path(full_name: str) -> str:
    owner, _, name = full_name.partition("/")
    return f"repos/{quote(owner, safe='')}/{quote(name, safe='')}"


def _named(anon: Any, repo_id: int, expected: str) -> str | Unavailable:
    body = _get(anon, f"repositories/{repo_id}")
    if isinstance(body, Unavailable):
        return body
    if not isinstance(body, dict) or body.get("full_name") != expected:
        got = body.get("full_name") if isinstance(body, dict) else body
        return Unavailable(f"repositories/{repo_id}: {got!r}, привязка — {expected!r}")
    if body.get("private") is not False:
        return Unavailable(f"{expected}: v1 читает только публичные источники доверия")
    return expected


def _chain(anon: Any, repo: str, head: str) -> list[str] | Unavailable:
    """First-parent от `head` до корня; звенья от корня к голове."""
    chain: list[str] = []
    sha: str | None = head
    while sha is not None:
        if len(chain) == MAX_CHAIN:
            return Unavailable(f"{repo}: цепочка длиннее {MAX_CHAIN}")
        body = _get(anon, f"{_repo_path(repo)}/commits/{sha}")
        if isinstance(body, Unavailable):
            return body
        parents = body.get("parents") if isinstance(body, dict) else None
        if not isinstance(body, dict) or body.get("sha") != sha or not isinstance(parents, list):
            return Unavailable(f"{repo}@{sha[:12]}: ответ не о том коммите")
        chain.append(sha)
        first = parents[0].get("sha") if parents and isinstance(parents[0], dict) else None
        sha = first if isinstance(first, str) else None
    return chain[::-1]


def _journal_versions(
    anon: Any, repo: str, chain: Sequence[str]
) -> list[tuple[Epoch, ...]] | Unavailable:
    versions: list[tuple[Epoch, ...]] = []
    last: bytes | None = None
    for sha in chain:
        raw = contents_bytes(anon, repo, JOURNAL_FILE, sha)
        if isinstance(raw, Unavailable):
            return raw
        if raw is None:
            if versions:
                return Unavailable(f"{repo}@{sha[:12]}: {JOURNAL_FILE} удалён")
            continue
        if raw == last:
            continue
        try:
            versions.append(parse_journal(raw.decode("utf-8")))
        except (JournalError, UnicodeDecodeError) as exc:
            return Unavailable(f"{repo}@{sha[:12]}: {exc}")
        last = raw
    return versions


def _checked_epochs(
    versions: list[tuple[Epoch, ...]], source: SourceV2
) -> tuple[Epoch, ...] | Unavailable:
    if not versions:
        return Unavailable(f"в снимке нет {JOURNAL_FILE}")
    first = versions[0]
    if len(first) != 1 or first[0].closed or first[0].anchor != source.bootstrap_activation:
        return Unavailable("первая версия журнала — не bootstrap с BOOTSTRAP_ACTIVATION")
    try:
        for prev, new in zip(versions, versions[1:]):
            check_transition(prev, new)
    except JournalError as exc:
        return Unavailable(f"недопустимый переход журнала: {exc}")
    return versions[-1]


def read_trust_snapshot(anon: Any, binding: Binding) -> TrustSnapshot | Unavailable:
    """Снимок закреплённой ветки `df-trust` (§6.1 п. 1): всё — из одного SHA."""
    if binding.host != SUPPORTED_HOST:
        return Unavailable(f"привязка: хост {binding.host!r} не поддерживается v1")
    repo = _named(anon, binding.df_trust_repository_id, binding.df_trust_full_name)
    if isinstance(repo, Unavailable):
        return repo
    branch = quote(binding.df_trust_branch, safe="")
    body = _get(anon, f"{_repo_path(repo)}/branches/{branch}")
    if isinstance(body, Unavailable):
        return body
    commit = body.get("commit") if isinstance(body, dict) else None
    head = commit.get("sha") if isinstance(commit, dict) else None
    if not isinstance(head, str) or len(head) != 40:
        return Unavailable(f"{repo}: голова {binding.df_trust_branch} не прочитана")
    raw_source = contents_bytes(anon, repo, SOURCE_FILE, head)
    if not isinstance(raw_source, bytes):
        return raw_source or Unavailable(f"{repo}@{head[:12]}: нет {SOURCE_FILE}")
    try:
        source = parse_source(raw_source.decode("utf-8"))
    except (SourceError, UnicodeDecodeError) as exc:
        return Unavailable(f"{repo}@{head[:12]}: {exc}")
    policy = _named(anon, binding.approval_policy_repository_id, source.repo)
    if isinstance(policy, Unavailable):
        return policy
    chain = _chain(anon, repo, head)
    if isinstance(chain, Unavailable):
        return chain
    versions = _journal_versions(anon, repo, chain)
    if isinstance(versions, Unavailable):
        return versions
    epochs = _checked_epochs(versions, source)
    if isinstance(epochs, Unavailable):
        return epochs
    root = TrustRoot(
        binding.host, binding.df_trust_repository_id, binding.df_trust_branch, head
    )
    return TrustSnapshot(root, repo, source, epochs)


# --- периметр -----------------------------------------------------------------------


def gh_get(cred: Credential, path: str) -> Fact[object]:
    """`gh api <path>` ИМЕННО этим credential: токены окружения снимаются,
    чтобы профиль не подменился чужим токеном."""
    env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
    if cred.token is not None:
        env["GH_TOKEN"] = cred.token
    if cred.gh_config_dir is not None:
        env["GH_CONFIG_DIR"] = str(cred.gh_config_dir)
    try:
        done = subprocess.run(
            ["gh", "api", path], env=env, capture_output=True, text=True,
            timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return unavailable(f"gh api {path} ({cred.label}): {exc}")
    if done.returncode != 0:
        return unavailable(f"gh api {path} ({cred.label}): {done.stderr.strip()[:200]}")
    try:
        return Fact(Outcome.FOUND, json.loads(done.stdout), "ok")
    except ValueError:
        return unavailable(f"gh api {path} ({cred.label}): ответ не JSON")


def credentials(
    env: Mapping[str, str], *, agent_dir: Path, merge_dir: Path | None = None
) -> tuple[Credential, ...]:
    """Что процесс держит (не что объявлено): профиль агента, профиль мержа,
    `GH_TOKEN`, `GITHUB_TOKEN`; совпадающие — один раз."""
    found = [Credential("profile:agent", gh_config_dir=agent_dir)]
    if merge_dir is not None and merge_dir.resolve() != agent_dir.resolve():
        found.append(Credential("profile:merge", gh_config_dir=merge_dir))
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        token = env.get(var)
        if token and all(c.token != token for c in found):
            found.append(Credential(f"env:{var}", token=token))
    return tuple(found)


def _roles(snap: TrustSnapshot) -> tuple[tuple[str, str], ...]:
    return (("approval-policy", snap.source.repo), ("df-trust", snap.full_name))


Found = tuple[str, str] | Unavailable | None
"""Нарушение `(роль репо, деталь)`, недоступность или чисто."""


def _self_report(readers: Readers, creds: Sequence[Credential], snap: TrustSnapshot) -> Found:
    gap: Unavailable | None = None
    for cred in creds:
        for role, repo in _roles(snap):
            fact = readers.as_credential(cred, _repo_path(repo))
            body = fact.value if fact.outcome is Outcome.FOUND else None
            perms = body.get("permissions") if isinstance(body, dict) else None
            verdict = permissions_refusal(perms if isinstance(perms, dict) else None)
            if isinstance(verdict, Violation):
                return role, f"{cred.label} на {repo}: {verdict.detail}"
            if isinstance(verdict, Unavailable) and gap is None:
                gap = Unavailable(f"самоотчёт {cred.label} на {repo}: {fact.detail or verdict.reason}")
    return gap


def _rulesets(anon: Any, snap: TrustSnapshot) -> Found:
    branches = {"approval-policy": snap.source.branch, "df-trust": snap.root.branch}
    for role, repo in _roles(snap):
        branch = quote(branches[role], safe="")
        body = _get(anon, f"{_repo_path(repo)}/rules/branches/{branch}")
        if isinstance(body, Unavailable):
            return body
        if not isinstance(body, list):
            return Unavailable(f"{repo}: правила ветки — не список")
        kinds = {r.get("type") for r in body if isinstance(r, dict)}
        if missing := sorted(REQUIRED_RULES - kinds):
            return role, f"{repo}@{branches[role]}: нет правил {missing}"
    return None


def _rebase(anon: Any, snap: TrustSnapshot) -> Found | str:
    """Четыре состояния (решения владельца 13.10, п. 1); `REBASE_PREMISE` — непроверено."""
    body = _get(anon, _repo_path(snap.source.repo))
    if isinstance(body, Unavailable):
        return body
    if not isinstance(body, dict):
        return Unavailable(f"{snap.source.repo}: ответ не объект")
    value = body.get("allow_rebase_merge")
    if value is None:
        return REBASE_PREMISE
    if value is True:
        return "approval-policy", f"{snap.source.repo}: allow_rebase_merge = true"
    if value is False:
        return None
    return Unavailable(f"{snap.source.repo}: allow_rebase_merge = {value!r}")


def _register(journal: Journal, snap: TrustSnapshot, found: tuple[str, str]) -> Unavailable:
    """Обнаруженное нарушение — событие журнала периметра (§6.3), затем отказ."""
    role, detail = found
    failed = journal.record(snap, role, detail)
    if failed is not None:
        return Unavailable(f"нарушение периметра ({detail}); регистрация: {failed.reason}")
    return Unavailable(f"нарушение периметра: {detail}")


def _snapshot_for_decision(
    readers: Readers, binding: Binding, journal: Journal
) -> TrustSnapshot | Unavailable:
    for _ in range(3):
        snap = read_trust_snapshot(readers.anon, binding)
        if isinstance(snap, Unavailable):
            return snap
        state = journal.status(snap)
        if state == STALE:
            continue
        if isinstance(state, Unavailable):
            return Unavailable(f"журнал периметра: {state.reason}")
        if state != "clear":
            return Unavailable(f"корень {snap.root.id_tuple}: периметр заблокирован (§6.3)")
        return snap
    return Unavailable("снимок df-trust сдвигается быстрее проверки периметра")


def load_trust(
    readers: Readers,
    binding: Binding,
    journal: Journal,
    *,
    creds: Sequence[Credential],
) -> Trust | Unavailable:
    """Обязательные проверки перед решением (п. 1–5 Task 12a), с ранним выходом.

    Журнал периметра обязателен у КАЖДОГО потребителя, включая предпроверку
    мержа (ревизия b1: `act_journal_wire`): блок, зарегистрированный другим
    потребителем, учитывается, а обнаруженное здесь нарушение сохраняется."""
    snap = _snapshot_for_decision(readers, binding, journal)
    if isinstance(snap, Unavailable):
        return snap
    unverified: tuple[str, ...] = ()
    for check in (
        lambda: _self_report(readers, creds, snap),
        lambda: _rulesets(readers.anon, snap),
        lambda: _rebase(readers.anon, snap),
    ):
        found = check()
        if found == REBASE_PREMISE:
            unverified = (REBASE_PREMISE,)
        elif isinstance(found, Unavailable):
            return found
        elif isinstance(found, tuple):
            return _register(journal, snap, found)
    return Trust(snap, unverified)


# --- якоря, диапазоны, наборы --------------------------------------------------------


def load_anchor_times(anon: Any, trust: Trust, t: datetime) -> dict[str, datetime | Unavailable]:
    """Время якоря КАЖДОЙ эпохи (`merged_at` активации, №19).

    Не лениво (ревизия b1, B1-10): admission при `merge`/без записи на T
    требует истории режима всех прежних эпох (`act_check._mode_history`), а её
    диапазоны читаются от якорей. e7 сохраняется иначе: недоступное время
    старого якоря записывается `Unavailable` и мешает лишь решению, которое от
    него зависит (`_epoch_at` идёт от новой эпохи назад и до старого якоря не
    доходит, если новый уже предшествует T). `t` — для симметрии с
    `load_ranges` и будущего предела обхода; на чтение не влияет.
    """
    del t
    times: dict[str, datetime | Unavailable] = {}
    for epoch in reversed(trust.epochs):
        got = act_facts.activation(anon, trust.source, epoch.anchor)
        times[epoch.anchor] = got if isinstance(got, Unavailable) else got.merged_at
    return times


def policy_set(anon: Any, source: SourceV2, activation: act_facts.ActivationFact) -> PolicySet | Unavailable:
    """Набор активации: байты из её `merge_commit`, сверенные с `files_sha256`."""
    files: dict[str, bytes] = {}
    for name in POLICY_FILES:
        digest = activation.files_sha256.get(name)
        if digest is None:
            return Unavailable(f"активация {activation.merge_commit[:12]}: нет {name}")
        raw = contents_bytes(anon, source.repo, name, activation.merge_commit)
        if not isinstance(raw, bytes):
            return raw or Unavailable(f"{name} в {activation.merge_commit[:12]} не найден")
        if hashlib.sha256(raw).hexdigest() != digest:
            return Unavailable(f"смешение версий: {name} ≠ files_sha256 активации")
        files[name] = raw
    try:
        return parse_policy_set(activation.merge_commit, files)
    except PolicyError as exc:
        return Unavailable(f"набор {activation.merge_commit[:12]}: {exc}")


def load_ranges(
    anon: Any,
    trust: Trust,
    anchor_at: Mapping[str, datetime | Unavailable],
    t: datetime,
    journal: Journal,
) -> tuple[dict[int, Span | Unavailable], dict[str, PolicySet | Unavailable]] | Unavailable:
    """Диапазоны эпох с якорем ≤ t и наборы активаций ≤ t (§6.4).

    Нарушение формы истории в диапазоне ЛЮБОЙ эпохи — событие периметра и
    отказ (§6.3; ревизия b1 сняла исключение для закрытых эпох). Недочитанный
    диапазон — значение `Unavailable`: решение спросит его, только если оно от
    него зависит (e5, e7). Эпоха с неизвестным временем якоря — `Unavailable`.
    """
    ranges: dict[int, Span | Unavailable] = {}
    sets: dict[str, PolicySet | Unavailable] = {}
    for epoch in trust.epochs:
        at = anchor_at.get(epoch.anchor, Unavailable("время якоря не загружено"))
        if isinstance(at, Unavailable):
            ranges[epoch.epoch] = Unavailable(f"якорь эпохи {epoch.epoch}: {at.reason}")
            continue
        if at > t:
            continue
        got = act_facts.history(anon, trust.source, epoch.anchor, epoch.last_confirmed)
        if isinstance(got, act_facts.HistoryBreak):
            return _register(journal, trust.snapshot, ("approval-policy", got.reason))
        if isinstance(got, Unavailable):
            ranges[epoch.epoch] = got
            continue
        ranges[epoch.epoch] = tuple(
            ActivationView(("activation", a.merge_commit), a.merge_commit, a.merged_at)
            for a in got
        )
        for a in got:
            if a.merged_at <= t and a.merge_commit not in sets:
                sets[a.merge_commit] = policy_set(anon, trust.source, a)
    return ranges, sets


PRE_MIGRATION = "pre_migration"
"""Записи `ACT_MODE[P]` не было ни в одной активации — старый путь (§10, legacy-эпоха)."""


def _modes(
    span: Span, sets: Mapping[str, PolicySet | Unavailable], product: str, now: datetime
) -> list[str] | Unavailable:
    found: list[str] = []
    for a in span:
        if a.at > now:
            continue
        policy = sets.get(a.merge_commit, Unavailable("набор активации не загружен"))
        if isinstance(policy, Unavailable):
            return Unavailable(f"набор {a.merge_commit[:12]}: {policy.reason}")
        found.append(mode_for(policy, product) or "")
    return found


def product_mode(
    trust: Trust,
    ranges: Mapping[int, Span | Unavailable],
    sets: Mapping[str, PolicySet | Unavailable],
    product: str,
    now: datetime,
) -> str | Unavailable:
    """Режим продукта СЕЙЧАС по истории режима (§10): `PRE_MIGRATION` | `merge` | `review`.

    Не «текущее значение»: удаление записи после первой (`""`), обратное
    переключение `review → merge` и недочитанная нужная история — `Unavailable`,
    а не legacy (ревизия b1, B1-5). Текущее `review` от прежних эпох не зависит
    (e7); иначе нужна история всех эпох.
    """
    open_span = ranges.get(trust.epochs[-1].epoch, Unavailable("диапазон открытой эпохи нет"))
    if isinstance(open_span, Unavailable):
        return Unavailable(f"история режима: {open_span.reason}")
    current = _modes(open_span, sets, product, now)
    if isinstance(current, Unavailable):
        return current
    if current and current[-1] == "review":
        return "review"
    history: list[str] = []
    for epoch in trust.epochs:
        span = ranges.get(epoch.epoch)
        if span is None:
            continue  # якорь эпохи позже now — эпоха не наступила
        if isinstance(span, Unavailable):
            return Unavailable(f"история режима, эпоха {epoch.epoch}: {span.reason}")
        got = _modes(span, sets, product, now)
        if isinstance(got, Unavailable):
            return got
        history.extend(got)
    entered = [i for i, m in enumerate(history) if m]
    if not entered:
        return PRE_MIGRATION
    tail = history[entered[0]:]
    if tail[-1] == "":
        return Unavailable(f"ACT_MODE[{product}] удалён после первой записи (§10 п. 2)")
    if "review" in tail:
        return Unavailable(f"ACT_MODE[{product}]: обратное переключение review → merge")
    return "merge"


def current_activation(
    trust: Trust, ranges: Mapping[int, Span | Unavailable]
) -> ActivationView | Unavailable:
    """Последняя активация открытой эпохи — для живой заявки (§6)."""
    span = ranges.get(trust.epochs[-1].epoch, Unavailable("диапазон открытой эпохи не прочитан"))
    if isinstance(span, Unavailable):
        return span
    if not span:
        return Unavailable("в открытой эпохе нет активаций")
    return span[-1]
```

- [ ] **Step 4: Write `act_journal`**

```python
"""Журнал событий периметра для потребителя (спека A rev 16 §6.3, спека B §6.1).

Хранилище и автомат — этап 2a установщика (`df.publisher.perimeter`). В
установке `df` (есть обёртка издателя) потребитель спрашивает издателя
(`df.perimeter_client`); без неё — режим разработки: то же хранилище в
`$XDG_STATE_HOME/darkfactory/perimeter/root/…`, без гарантии защиты от агента
(спека §6.3; решение плана Р-5). Модуль НЕ входит в цепочку `act_premerge`.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from df import perimeter_client
from df.paths import Layout, default_layout
from df.publisher import perimeter as store
from df.publisher import request as preq
from df.stage2_config import TrustBinding
from governance.act_time import Unavailable
from governance.act_trust import STALE, TrustSnapshot

DEV_ROOT = Path("darkfactory") / "perimeter" / "root"
Reread = Callable[[], TrustSnapshot | Unavailable]


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _publisher_present() -> bool:
    return (default_layout().libexec / "df-publish").exists()


class ForgeEpochs:
    """`EpochSource` издателя поверх чтения снимка `df-trust` с форджа."""

    def __init__(self, reread: Reread) -> None:
        self._reread = reread

    def head(self, root: tuple[str, int, str]) -> store.Snapshot | store.Unavailable:
        snap = self._reread()
        if isinstance(snap, Unavailable):
            return store.Unavailable(f"df-trust: {snap.reason}")
        if snap.root.id_tuple != tuple(root):
            return store.Unavailable("root_mismatch")
        return store.Snapshot(snap.root.snapshot_sha, snap.epochs[-1].epoch)


def _root_id(snap: TrustSnapshot) -> dict[str, object]:
    host, repository_id, branch = snap.root.id_tuple
    return {"host": host, "repository_id": repository_id, "branch": branch}


def _status_of(resp: preq.Response, snap: TrustSnapshot) -> str | Unavailable:
    if resp.outcome == "stale_snapshot":
        return STALE
    result = resp.result
    state = result.get("state")
    if state not in ("blocked", "clear"):
        return Unavailable(f"периметр: {result.get('reason') or resp.reason}")
    if (result.get("snapshot_sha"), result.get("last_epoch")) != (
        snap.root.snapshot_sha,
        snap.epochs[-1].epoch,
    ):
        return Unavailable("периметр: ответ не о снимке решения")
    return str(state)


class DevJournal:
    """Режим разработки: хранилище этапа 2a в каталоге пользователя."""

    def __init__(
        self, state_home: Path, run_id: str, reread: Reread, policy_repository_id: int
    ) -> None:
        base = (state_home / DEV_ROOT).resolve()
        base.mkdir(parents=True, exist_ok=True)
        self.layout = Layout(base)
        self.run_id = run_id
        self.epochs = ForgeEpochs(reread)
        # настоящий id approval-policy из привязки: по нему издатель пишет
        # `violated_repository_id` события роли approval-policy (B1-18)
        self.policy_repository_id = policy_repository_id

    def _open(self) -> store.Db | Unavailable:
        try:
            self.layout.perimeter_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            store.create_db(self.layout)  # created | present; lost/integrity — исключение
            return store.open_db(self.layout)
        except (store.Unavailable, OSError) as exc:
            return Unavailable(f"журнал периметра (dev): {getattr(exc, 'reason', exc)}")

    def _request(self, op: str, fields: Mapping[str, object]) -> preq.Request:
        data = {
            "schema": preq.REQUEST_SCHEMA,
            "request_id": f"perimeter-{uuid.uuid4().hex}",
            "run_id": self.run_id,
            **fields,
        }
        return preq.parse(op, json.dumps(data, sort_keys=True).encode("utf-8"))

    def status(self, snap: TrustSnapshot) -> str | Unavailable:
        req = self._request(
            "perimeter-status",
            {"root_id": _root_id(snap), "expected_snapshot_sha": snap.root.snapshot_sha},
        )
        db = self._open()
        if isinstance(db, Unavailable):
            return db
        with db:
            try:
                store.admit(db, self.epochs, snap.root.id_tuple)
            except store.Unavailable as exc:
                return Unavailable(f"журнал периметра (dev): {exc.reason}")
            resp = store.perimeter_status(db.conn, req, self.epochs)
        return _status_of(resp, snap)

    def record(self, snap: TrustSnapshot, source: str, detail: str) -> Unavailable | None:
        req = self._request(
            "record-violation",
            {"root_id": _root_id(snap), "source": source, "observed_at": _now(),
             "detail": detail[: preq.MAX_DETAIL]},
        )
        host, repository_id, branch = snap.root.id_tuple
        binding = TrustBinding(host, repository_id, snap.full_name, branch,
                               self.policy_repository_id, "public")
        db = self._open()
        if isinstance(db, Unavailable):
            return db
        with db:
            try:
                store.admit(db, self.epochs, snap.root.id_tuple)
                store.record_violation(
                    db.conn, os.getuid(), req, f"dev:{req.request_id}", self.epochs, binding
                )
            except store.Unavailable as exc:
                return Unavailable(f"журнал периметра (dev): {exc.reason}")
        return None


class InstalledJournal:
    """Установка `df`: операции издателя `perimeter-status` / `record-violation`."""

    def __init__(self, df_run_id: str, layout: Layout | None = None) -> None:
        self.run_id = df_run_id
        self.layout = layout

    def status(self, snap: TrustSnapshot) -> str | Unavailable:
        moved: list[bool] = []

        def reload() -> perimeter_client.TrustView:
            moved.append(True)
            raise RuntimeError("снимок df-trust сдвинулся — перечитать доверие")

        got = perimeter_client.status(
            self.run_id,
            perimeter_client.RootId(*snap.root.id_tuple),
            perimeter_client.TrustView(snap.root.snapshot_sha, snap.epochs[-1].epoch),
            reload=reload,
            layout=self.layout,
        )
        if moved:
            return STALE
        if got.state == "unavailable":
            return Unavailable(f"периметр: {got.reason}")
        return got.state

    def record(self, snap: TrustSnapshot, source: str, detail: str) -> Unavailable | None:
        data = {
            "schema": preq.REQUEST_SCHEMA,
            "request_id": f"perimeter-{uuid.uuid4().hex}",
            "run_id": self.run_id,
            "root_id": _root_id(snap),
            "source": source,
            "observed_at": _now(),
            "detail": detail[: preq.MAX_DETAIL],
        }
        try:
            raw = perimeter_client.sudo_call(
                json.dumps(data, sort_keys=True).encode("utf-8"),
                self.layout,
                operation="record-violation",
            )
            resp = preq.parse_response(raw.decode("utf-8").strip())
        except (OSError, preq.BadResponse) as exc:
            return Unavailable(f"record-violation: {exc}")
        if resp.outcome != "done":
            return Unavailable(f"record-violation: {resp.outcome}: {resp.reason}")
        return None


def journal_for(
    *, state_home: Path, run_id: str, reread: Reread, env: Mapping[str, str],
    policy_repository_id: int,
) -> DevJournal | InstalledJournal | Unavailable:
    """Журнал этого процесса (Р-5): установка `df` — издатель; иначе dev."""
    if _publisher_present():
        df_run = env.get("DF_RUN_ID")
        if not df_run:
            return Unavailable("периметр: установка df без контекста прогона (DF_RUN_ID)")
        return InstalledJournal(df_run)
    return DevJournal(state_home, run_id, reread, policy_repository_id)
```

`preq.BadResponse` и `preq.MAX_DETAIL` — имена `df/publisher/request.py` (сверено: `MAX_DETAIL = 4000`,
`BadResponse` ловит `perimeter_client._ask`).

- [ ] **Step 5: Write `act_context`**

```python
"""Решение по доставке поверх доверенного контекста (общий вход Task 13, 14).

Порядок §5.5: доставка первой — не смёржен → `WaitingDelivery`, ничего
больше не читается; затем время якорей и диапазоны на `T = merged_at`,
вердикт старого кода (только у заявки без маркера), `check_delivery`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from governance import act_facts, act_trust
from governance.act_check import (
    DeliveryContext,
    LegacyAccepted,
    Outcome,
    check_delivery,
)
from governance.act_decision import DeliveryView, Rejected, WaitingDelivery
from governance.act_request import NodeRequest
from governance.act_time import Unavailable, parse_ts


def delivery_verdict(
    readers: act_trust.Readers,
    trust: act_trust.Trust,
    journal: act_trust.Journal | None,
    *,
    product: str,
    pr: int,
    paths: Sequence[str],
    request: NodeRequest | None,
    request_act: str | None,
    head_sha: str,
    facts_transport: Any,
    self_hashes: Callable[[], Mapping[str, str] | Unavailable],
    legacy: Callable[[], LegacyAccepted | Rejected | Unavailable] | None,
) -> tuple[Outcome, DeliveryView | None]:
    """Исход доставки PR `product#pr` и прочитанная доставка."""
    got = act_facts.read(facts_transport, product, pr, paths)
    if isinstance(got, Unavailable):
        return got, None
    delivery, facts = got
    if facts is None or delivery.merged_at is None:
        return WaitingDelivery(), delivery
    t = parse_ts(delivery.merged_at)
    anchors = act_trust.load_anchor_times(readers.anon, trust, t)
    loaded = act_trust.load_ranges(readers.anon, trust, anchors, t, journal)
    if isinstance(loaded, Unavailable):
        return loaded, delivery
    ranges, sets = loaded
    hashes = self_hashes() if request is not None else {}
    if isinstance(hashes, Unavailable):
        return hashes, delivery
    ctx = DeliveryContext(
        source=trust.source,
        product=product,
        request=request,
        request_act=request_act,
        head_sha=head_sha,
        delivery=delivery,
        facts=facts,
        head_self_hashes=hashes,
        epochs=trust.epochs,
        anchor_at=anchors,
        ranges=ranges,
        policy_sets=sets,
        legacy=legacy() if request is None and legacy is not None else None,
    )
    return check_delivery(ctx), delivery
```

`act_check.Outcome` — алиас исхода из Task 12 (уже экспортируется модулем).

- [ ] **Step 5a: Identity of judged objects** (ревизия b1, класс B1-1/B1-2/B1-13/B1-14/B1-15 —
  таблица «место суждения × компоненты» в ревизии части B). `governance/act_identity.py`:

```python
"""Идентичность судимого объекта акта (ревизия b1): одна на объект, одна проверка.

Каждое суждение о заявке, её доставке, маркере миграции сверяет ВСЕ
компоненты идентичности объекта, а не те, что оказались под рукой: продукт,
воркстрим, ключ заявки, пути узлов в DAG прогона и байты манифеста в
одобренном head. Таблица «место суждения × компоненты» — ревизия части B;
отрицательный двойник на каждый компонент — `tests/test_act_identity.py`.
Модуль чистый и входит в цепочку `act_premerge`: только `act_request`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from governance.act_request import NodeRequest, manifest_path


@dataclass(frozen=True)
class RequestIdentity:
    """Чья это заявка: продукт доставки, воркстрим прогона, ключ леджера."""

    product: str
    ws_id: str
    request_id: str

    @property
    def manifest_path(self) -> str:
        """Где манифест обязан лежать в одобренном коммите."""
        return manifest_path(self.ws_id, self.request_id)

    @property
    def migration_marker(self) -> str:
        """Маркер `act_migrated` этой и только этой заявки (§10 п. 2, B1-14)."""
        return f"<!-- act-migrated: {self.product}/{self.ws_id}/{self.request_id} -->"

    @property
    def memo_key(self) -> tuple[str, str, str]:
        """Ключ памяти вердиктов: заявка, а не коммит (B1-2)."""
        return (self.product, self.ws_id, self.request_id)


def manifest_refusal(req: NodeRequest, ident: RequestIdentity) -> str | None:
    """Манифест называет именно эту заявку этого воркстрима этого продукта."""
    for name, got, want in (
        ("product", req.product, ident.product),
        ("ws_id", req.ws_id, ident.ws_id),
        ("request_id", req.request_id, ident.request_id),
    ):
        if got != want:
            return f"манифест: {name} {got} ≠ {want}"
    return None


def paths_refusal(req: NodeRequest, expected: Mapping[str, str]) -> str | None:
    """Узлы манифеста — узлы активного DAG, путь каждого — его путь в прогоне."""
    for node, entry in sorted(req.nodes.items()):
        want = expected.get(node)
        if want is None:
            return f"манифест: узел {node} не из активного DAG"
        if entry.path != want:
            return f"манифест: путь узла {node} {entry.path} ≠ {want}"
    return None


def head_manifest_refusal(text_at_head: str | None, text_delivered: str) -> str | None:
    """Доставленный манифест лежит в одобренном head теми же байтами (§9.1 (а) п. 0)."""
    if text_at_head is None:
        return "манифест есть в доставке, но не в одобренном head"
    if text_at_head != text_delivered:
        return "манифест доставки ≠ манифесту одобренного head"
    return None
```

`tests/test_act_identity.py`:

```python
"""Идентичность судимых объектов: отрицательные двойники по каждому компоненту (b1)."""

from __future__ import annotations

import pytest

from governance.act_identity import (
    RequestIdentity,
    head_manifest_refusal,
    manifest_refusal,
    paths_refusal,
)
from governance.act_request import NodeEntry, NodeRequest, PolicyPin, render_manifest

ID = RequestIdentity("owner/p", "ws-7", "approve-2-1-1")
PIN = PolicyPin("owner/approval-policy", "main", "a" * 40, {})


def req(**kw: object) -> NodeRequest:
    base = dict(request_id="approve-2-1-1", ws_id="ws-7", product="owner/p",
                nodes={"requirements": NodeEntry("spec/10-r.md", "h", 1)},
                upstream_pins={"requirements": {}}, policy=PIN)
    base.update(kw)
    return NodeRequest(**base)  # type: ignore[arg-type]


def test_matching_manifest_passes() -> None:
    assert manifest_refusal(req(), ID) is None
    assert paths_refusal(req(), {"requirements": "spec/10-r.md"}) is None


@pytest.mark.parametrize("field", ["product", "ws_id", "request_id"])
def test_each_identity_component_is_checked(field: str) -> None:
    got = manifest_refusal(req(**{field: "other"}), ID)
    assert got is not None and f"манифест: {field} other" in got


def test_node_outside_dag_and_wrong_path() -> None:
    outside = paths_refusal(req(), {})
    assert outside is not None and "не из активного DAG" in outside
    moved = paths_refusal(req(), {"requirements": "spec/other.md"})
    assert moved is not None and "путь узла" in moved


def test_manifest_only_in_delivery_or_changed() -> None:
    text = render_manifest(req())
    assert head_manifest_refusal(text, text) is None
    absent = head_manifest_refusal(None, text)
    assert absent is not None and "не в одобренном head" in absent
    changed = head_manifest_refusal(text + "# x\n", text)
    assert changed is not None and "≠" in changed


def test_marker_and_memo_carry_full_identity() -> None:
    other_ws = RequestIdentity("owner/p", "ws-8", "approve-2-1-1")
    assert ID.migration_marker != other_ws.migration_marker
    assert ID.memo_key != other_ws.memo_key
    assert ID.manifest_path == "workstreams/ws-7/approvals/requests/approve-2-1-1.yaml"
```

- [ ] **Step 5b: Narrow journal client for the merge harness** (ревизия b1, B1-4: спека §6.3
  требует журнал у каждого потребителя; `act_journal` тянет `df/`, а цепочка `act_premerge` —
  только `_HARNESS_PREFIXES`). `governance/act_journal_wire.py` — тот же журнал без импорта
  `df/`: в установке — протокол издателя, в режиме разработки — то же хранилище SQLite (чтение
  правила блокировки, вставка события `pending`; завершение `pending → registered(E)` делает
  полный журнал на пути решения). Совпадение форматов держит тест с обоими клиентами на одном
  файле базы (решение исполнителя плана Р-16).

```python
"""Узкий клиент журнала периметра для обвязки мержа (ревизия b1, спека §6.3).

`act_premerge` исполняется `merge-pr.sh`, и его цепочка импортов — только
защищённые `_HARNESS_PREFIXES`; полный журнал (`act_journal`) тянет `df/`.
Этот модуль — тот же журнал без `df/`: в установке `df` — протокол издателя
`df/publisher-request/v1` поверх `sudo -n -u df-publisher …/df-publish`; в
режиме разработки — то же хранилище SQLite: правило блокировки (событие
`pending` либо `registered(E)` с `E ≥` последней эпохи) и вставка события
`pending` одной транзакцией. Номер эпохи `pending` назначает полный журнал
на пути решения. Совпадение форматов держит `tests/test_act_journal_wire.py`.
"""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from governance.act_time import Unavailable
from governance.act_trust import STALE, TrustSnapshot

REQUEST_SCHEMA = "df/publisher-request/v1"
RESPONSE_SCHEMA = "df/publisher-response/v1"
PUBLISHER = "df-publisher"
SUDO = "/usr/bin/sudo"
PUBLISH = Path("/opt/df/libexec/df-publish")
#: Каталог хранилища режима разработки — тот же, что `act_journal.DevJournal`.
DEV_BASE = Path("darkfactory/perimeter/root/var/lib/df/perimeter")
DB_NAME, LOCK, MARKER, RESTORE_INTENT = "events.sqlite", "db.lock", "created", "restore.intent"
BUSY_TIMEOUT_MS = 5000
LOCK_WAIT = 5.0
MAX_DETAIL = 4000
_BLOCKING = (
    "SELECT event_id FROM events WHERE root_host = ? AND root_repository_id = ? "
    "AND root_branch = ? AND (state = 'pending' OR (state = 'registered' AND epoch >= ?))"
)
_LOCK_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _root_id(snap: TrustSnapshot) -> dict[str, object]:
    host, repository_id, branch = snap.root.id_tuple
    return {"host": host, "repository_id": repository_id, "branch": branch}


class WireJournal:
    """Установка `df`: операции издателя `perimeter-status` и `record-violation`."""

    def __init__(self, df_run_id: str, *, run: Callable[..., Any] = subprocess.run) -> None:
        self.run_id = df_run_id
        self._run = run

    def _call(self, op: str, fields: Mapping[str, object]) -> dict | Unavailable:
        data = {
            "schema": REQUEST_SCHEMA,
            "request_id": f"perimeter-{uuid.uuid4().hex}",
            "run_id": self.run_id,
            **fields,
        }
        argv = [SUDO, "-n", "-u", PUBLISHER, str(PUBLISH), op]
        try:
            done = self._run(argv, input=json.dumps(data, sort_keys=True).encode("utf-8"),
                             capture_output=True, timeout=60, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return Unavailable(f"{op}: вызов издателя не состоялся: {exc}")
        lines = done.stdout.decode("utf-8", "replace").splitlines()
        try:
            resp = json.loads(lines[0]) if len(lines) == 1 else None
        except ValueError:
            resp = None
        if not isinstance(resp, dict) or resp.get("schema") != RESPONSE_SCHEMA:
            return Unavailable(f"{op}: ответ издателя не разобран (код {done.returncode})")
        return resp

    def status(self, snap: TrustSnapshot) -> str | Unavailable:
        resp = self._call("perimeter-status", {
            "root_id": _root_id(snap), "expected_snapshot_sha": snap.root.snapshot_sha})
        if isinstance(resp, Unavailable):
            return resp
        if resp.get("outcome") == "stale_snapshot":
            return STALE
        result = resp.get("result") if resp.get("outcome") == "done" else None
        state = result.get("state") if isinstance(result, dict) else None
        if state not in ("blocked", "clear"):
            reason = result.get("reason") if isinstance(result, dict) else resp.get("reason")
            return Unavailable(f"периметр: {reason}")
        if (result.get("snapshot_sha"), result.get("last_epoch")) != (
            snap.root.snapshot_sha, snap.epochs[-1].epoch):
            return Unavailable("периметр: ответ не о снимке решения")
        return str(state)

    def record(self, snap: TrustSnapshot, source: str, detail: str) -> Unavailable | None:
        resp = self._call("record-violation", {
            "root_id": _root_id(snap), "source": source, "observed_at": _now(),
            "detail": detail[:MAX_DETAIL]})
        if isinstance(resp, Unavailable):
            return resp
        if resp.get("outcome") != "done":
            return Unavailable(f"record-violation: {resp.get('outcome')}: {resp.get('reason')}")
        return None


class DevStoreJournal:
    """Режим разработки: то же хранилище, что `act_journal.DevJournal`, без `df/`."""

    def __init__(self, state_home: Path, run_id: str, policy_repository_id: int) -> None:
        self.base = state_home / DEV_BASE
        self.run_id = run_id
        self.policy_repository_id = policy_repository_id

    def _open(self) -> tuple[int, sqlite3.Connection] | Unavailable:
        if (self.base / RESTORE_INTENT).exists():
            return Unavailable("журнал периметра (dev): restore_incomplete")
        db = self.base / DB_NAME
        try:
            if not stat.S_ISREG(os.lstat(db).st_mode):
                return Unavailable("журнал периметра (dev): integrity")
            fd = os.open(self.base / LOCK, _LOCK_FLAGS)
        except FileNotFoundError:
            lost = (self.base / MARKER).exists()
            return Unavailable(f"журнал периметра (dev): {'lost' if lost else 'не создан'}")
        except OSError as exc:
            return Unavailable(f"журнал периметра (dev): {exc}")
        deadline = time.monotonic() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    return Unavailable("журнал периметра (dev): busy")
                time.sleep(0.02)
        try:
            conn = sqlite3.connect(f"{db.as_uri()}?mode=rw&nofollow=1", uri=True,
                                   isolation_level=None)
            conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA synchronous = EXTRA")
            if sys.platform == "darwin":
                conn.execute("PRAGMA fullfsync = ON")
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("integrity_check")
            admission = conn.execute(
                "SELECT value FROM meta WHERE key = 'admission'").fetchone()
            if admission is None or admission[0] != "ready":
                conn.close()
                os.close(fd)
                return Unavailable("журнал периметра (dev): restore_pending")
        except sqlite3.Error as exc:
            os.close(fd)
            return Unavailable(f"журнал периметра (dev): {exc}")
        return fd, conn

    def status(self, snap: TrustSnapshot) -> str | Unavailable:
        opened = self._open()
        if isinstance(opened, Unavailable):
            return opened
        fd, conn = opened
        try:
            rows = conn.execute(_BLOCKING, (*snap.root.id_tuple, snap.epochs[-1].epoch)).fetchall()
        except sqlite3.Error as exc:
            return Unavailable(f"журнал периметра (dev): {exc}")
        finally:
            conn.close()
            os.close(fd)
        return "blocked" if rows else "clear"

    def record(self, snap: TrustSnapshot, source: str, detail: str) -> Unavailable | None:
        opened = self._open()
        if isinstance(opened, Unavailable):
            return opened
        fd, conn = opened
        key = f"perimeter-{uuid.uuid4().hex}"
        violated = (self.policy_repository_id if source == "approval-policy"
                    else snap.root.repository_id)
        provenance = json.dumps({"snapshot_sha": snap.root.snapshot_sha,
                                 "unavailable": "epoch_assigned_on_completion"},
                                sort_keys=True)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO events (root_host, root_repository_id, root_branch, "
                "request_key, request_id, run_id, sender, violated_role, "
                "violated_repository_id, detected_by, observed_at, registered_at, "
                "state, epoch, provenance, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, ?)",
                (*snap.root.id_tuple, f"premerge:{key}", key, self.run_id, os.getuid(),
                 source, violated, f"sender:{os.getuid()}", _now(), _now(), provenance,
                 detail[:MAX_DETAIL]),
            )
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            return Unavailable(f"журнал периметра (dev): событие не записано: {exc}")
        finally:
            conn.close()
            os.close(fd)
        return None


def journal_for_premerge(
    *, env: Mapping[str, str], state_home: Path, run_id: str, policy_repository_id: int
) -> WireJournal | DevStoreJournal | Unavailable:
    """Тот же выбор, что `act_journal.journal_for` (Р-5): издатель есть — протокол."""
    if PUBLISH.exists():
        df_run = env.get("DF_RUN_ID")
        if not df_run:
            return Unavailable("периметр: установка df без контекста прогона (DF_RUN_ID)")
        return WireJournal(df_run)
    return DevStoreJournal(state_home, run_id, policy_repository_id)
```

`tests/test_act_journal_wire.py`:

```python
"""Узкий клиент журнала: совпадение формата с df/ на одном файле базы (b1, Р-16)."""

from __future__ import annotations

import json
from pathlib import Path

from df.publisher import request as preq
from governance import act_journal, act_journal_wire
from governance.act_epochs import Epoch
from governance.act_source import SourceV2
from governance.act_time import Unavailable
from governance.act_trust import STALE, TrustRoot, TrustSnapshot


def snap(epoch: int = 1, sha: str = "1" * 40) -> TrustSnapshot:
    return TrustSnapshot(TrustRoot("github.com", 11, "main", sha), "owner/df-trust",
                         SourceV2("owner/ap", "main", "a" * 40, frozenset({"o"})),
                         tuple(Epoch(i, "a" * 40) for i in range(1, epoch + 1)))


class Head:
    def __init__(self, current: object) -> None:
        self.current = current

    def __call__(self) -> object:
        return self.current


def pair(tmp_path: Path, head: Head):
    full = act_journal.DevJournal(tmp_path.resolve(), "run-1", head, 22)
    narrow = act_journal_wire.DevStoreJournal(tmp_path.resolve(), "run-1", 22)
    return full, narrow


def test_same_directory_as_full_journal(tmp_path: Path) -> None:
    full, narrow = pair(tmp_path, Head(snap()))
    assert narrow.base == full.layout.perimeter_dir


def test_missing_store_is_unavailable(tmp_path: Path) -> None:
    _, narrow = pair(tmp_path, Head(snap()))
    got = narrow.status(snap())
    assert isinstance(got, Unavailable) and got.reason.endswith("не создан")
    assert isinstance(narrow.record(snap(), "df-trust", "x"), Unavailable)


def test_narrow_pending_blocks_and_full_journal_completes_it(tmp_path: Path) -> None:
    head = Head(snap(1))
    full, narrow = pair(tmp_path, head)
    assert full.status(snap(1)) == "clear"           # полный журнал создаёт базу
    assert narrow.status(snap(1)) == "clear"
    assert narrow.record(snap(1), "approval-policy", "push у профиля мержа") is None
    assert narrow.status(snap(1)) == "blocked"       # pending блокирует
    assert full.status(snap(1)) == "blocked"         # база цела, pending → registered(1)
    head.current = snap(2, "2" * 40)
    assert full.status(snap(2, "2" * 40)) == "clear"
    assert narrow.status(snap(2, "2" * 40)) == "clear"


def test_registered_by_full_journal_seen_by_narrow(tmp_path: Path) -> None:
    head = Head(snap(1))
    full, narrow = pair(tmp_path, head)
    full.record(snap(1), "df-trust", "x")
    assert narrow.status(snap(1)) == full.status(snap(1)) == "blocked"


def test_restore_intent_and_corruption_unavailable(tmp_path: Path) -> None:
    full, narrow = pair(tmp_path, Head(snap()))
    full.status(snap())
    (narrow.base / "restore.intent").write_text("x")
    got = narrow.status(snap())
    assert isinstance(got, Unavailable) and got.reason.endswith("restore_incomplete")
    (narrow.base / "restore.intent").unlink()
    (narrow.base / "events.sqlite").write_bytes(b"not a database")
    assert isinstance(narrow.status(snap()), Unavailable)


class Run:
    def __init__(self, response: dict) -> None:
        self.response, self.calls = response, []

    def __call__(self, argv, *, input, capture_output, timeout, check):  # noqa: A002
        self.calls.append((argv, input))

        class Done:
            returncode = 0
            stdout = (json.dumps(self.response) + "\n").encode()

        return Done()


def _resp(outcome: str, result: dict) -> dict:
    return {"schema": "df/publisher-response/v1", "outcome": outcome, "reason": None,
            "result": result, "evidence": {}}


def test_wire_requests_parse_as_publisher_requests() -> None:
    run = Run(_resp("done", {"state": "clear", "reason": None, "snapshot_sha": "1" * 40,
                             "last_epoch": 1, "events": []}))
    wire = act_journal_wire.WireJournal("run-7", run=run)
    assert wire.status(snap()) == "clear"
    wire.record(snap(), "approval-policy", "x")
    (argv1, data1), (argv2, data2) = run.calls
    assert argv1[-1] == "perimeter-status" and argv2[-1] == "record-violation"
    assert preq.parse("perimeter-status", data1).fields["expected_snapshot_sha"] == "1" * 40
    assert preq.parse("record-violation", data2).fields["source"] == "approval-policy"


def test_wire_stale_and_foreign_snapshot() -> None:
    stale = act_journal_wire.WireJournal("r", run=Run(_resp("stale_snapshot", {})))
    assert stale.status(snap()) == STALE
    foreign = act_journal_wire.WireJournal("r", run=Run(_resp("done", {
        "state": "clear", "reason": None, "snapshot_sha": "9" * 40, "last_epoch": 1})))
    got = foreign.status(snap())
    assert isinstance(got, Unavailable) and got.reason == "периметр: ответ не о снимке решения"


def test_wire_garbled_or_refused_answers_unavailable() -> None:
    """b4: ответ издателя не разобран / запись не принята — `Unavailable` с причиной."""
    class Garbled(Run):
        def __call__(self, argv, *, input, capture_output, timeout, check):  # noqa: A002
            class Done:
                returncode = 1
                stdout = b"not json\n"
            return Done()

    garbled = act_journal_wire.WireJournal("r", run=Garbled({}))
    got = garbled.status(snap())
    assert isinstance(got, Unavailable) and "ответ издателя не разобран" in got.reason
    refused = act_journal_wire.WireJournal("r", run=Run({**_resp("refused", {}), "reason": "busy"}))
    got = refused.record(snap(), "df-trust", "x")
    assert isinstance(got, Unavailable) and got.reason == "record-violation: refused: busy"


def test_record_insert_failure_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    """b4: вставка события в хранилище не удалась — `Unavailable`, не «записано»."""
    import os
    import sqlite3

    full, narrow = pair(tmp_path, Head(snap()))
    full.status(snap())

    class Refusing:
        def execute(self, sql, *args):
            if sql.startswith("INSERT"):
                raise sqlite3.OperationalError("disk I/O error")

        def close(self):
            pass

    monkeypatch.setattr(narrow, "_open", lambda: (os.open(os.devnull, os.O_RDONLY), Refusing()))
    got = narrow.record(snap(), "df-trust", "x")
    assert isinstance(got, Unavailable) and "событие не записано" in got.reason


def test_premerge_selection_follows_publisher_presence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(act_journal_wire, "PUBLISH", tmp_path / "df-publish")
    dev = act_journal_wire.journal_for_premerge(env={}, state_home=tmp_path, run_id="r",
                                                policy_repository_id=22)
    assert isinstance(dev, act_journal_wire.DevStoreJournal)
    (tmp_path / "df-publish").write_text("")
    got = act_journal_wire.journal_for_premerge(
        env={}, state_home=tmp_path, run_id="r", policy_repository_id=22)
    assert isinstance(got, Unavailable) and "DF_RUN_ID" in got.reason
    assert isinstance(act_journal_wire.journal_for_premerge(
        env={"DF_RUN_ID": "run-7"}, state_home=tmp_path, run_id="r",
        policy_repository_id=22), act_journal_wire.WireJournal)
```

- [ ] **Step 6: Remove the marker files of Part A** — `governance/act_perimeter.py` после правки
(целиком):

```python
"""Самоотчёт прав credential на источниках доверия (спека §6.2 б).

Маркер-файлы части A выведены: регистрацию нарушений ведёт журнал событий
периметра установки (спека A rev 16 §6.3; `governance/act_journal.py`).
Здесь — только классификация самоотчёта: нарушение (`Violation`) и
неустановленный факт (`Unavailable`) — разные исходы §6.2 б / §6.3.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from governance.act_time import Unavailable

_WRITE = ("push", "maintain", "admin")


@dataclass(frozen=True)
class Violation:
    """Установленное нарушение периметра: вызывающий регистрирует событие."""

    detail: str


def permissions_refusal(
    permissions: Mapping[str, object] | None,
) -> Violation | Unavailable | None:
    # (тело и докстринг — без изменений, `act_perimeter.py:115–136`)
    ...
```

Тесты маркеров удаляются из `tests/test_act_perimeter.py`; `git grep -n
"record_violation\|marker_path\|act_perimeter.blocked" governance tests` после правки — только
`df/` (своё `record_violation` издателя) и новые модули.

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run --group governance pytest tests/test_act_trust.py tests/test_act_journal.py tests/test_act_context.py tests/test_act_identity.py tests/test_act_journal_wire.py tests/test_act_perimeter.py -v`
Expected: PASS. Затем `uv run pytest -q` (без группы: `test_act_trust`, `test_act_context` —
`SKIPPED (steward)`; `test_act_journal` проходит — `df/` stdlib) и полный набор с группой. ruff,
pyrefly — чисто.

- [ ] **Step 8: Commit**

```bash
git add governance/act_trust.py governance/act_journal.py governance/act_context.py \
  governance/act_identity.py governance/act_journal_wire.py governance/act_perimeter.py \
  tests/test_act_trust.py tests/test_act_journal.py tests/test_act_context.py \
  tests/test_act_identity.py tests/test_act_journal_wire.py tests/test_act_perimeter.py
git commit -m "feat(act): доверенный контекст — снимок df-trust, журнал периметра, диапазоны эпох (спека A rev 16 §6)"
```

### Task 11c: Read-only токен истории

Исполняется **после Task 14, до Task 13** (решение владельца С-1, 01.11; «Порядок исполнения
части B»): меняет `act_status` Task 14 (`real_deps`, `live_policy`) и `act_context` Task 12a;
листинги `act_premerge` Task 15 уже написаны под новые подписи. Основание — спека rev 18 §6.2 и
раздел «Решение владельца 01.11 (С-1)» (R1–R9, Р-18…Р-23).

**Механизм — вход решения строит транспорт источников доверия сам** (решение контроллера после
ревью c1-r6: четыре круга подряд находили дыры в проверке подключения, собранного снаружи, —
C3-01, C4-01, C5-01/02, C6-01/02; поверхность убрана, а не проверяется). `act_trust.load_trust`
не принимает транспорт источников доверия ни в каком виде: он строит его из защищённой привязки
единственной фабрикой `history_transport`, сам создаёт запись самоотчёта `profile:history` из того
же значения токена и выполняет самоотчёт тем же объектом транспорта. Внедряемы только
низкоуровневые швы ввода-вывода: `open_url` (сеть) и `token_source` (процесс `gh auth token`).
`Readers` транспорта больше не несёт — только `as_credential` для самоотчёта прочих credentials.

**Files:**
- Modify: `governance/act_anon.py` (`AnonTransport(token=…)`, маскировка токена в тексте сбоя
  доставки)
- Modify: `governance/act_trust.py` (ключ привязки `history_gh_config_dir`, `history_token`,
  `history_transport`; `load_trust(..., open_url=None, token_source=None)` строит транспорт сам,
  `_creds_refusal`, `_history_self_report`; `TrustSnapshot.transport`, свойство `Trust.transport`;
  `Readers(as_credential)` без `anon`; `default_readers()` → `Readers(gh_get)`;
  `load_anchor_times(trust, t)`, `load_ranges(trust, anchor_at, t, journal)` — транспорт из
  `trust`; `Credential.token` без `repr`; `transport_fact`; отказ `gh_get` для `profile:history`)
- Modify: `governance/act_context.py` (`delivery_verdict`: `load_anchor_times(trust, t)`,
  `load_ranges(trust, …)` вместо `readers.anon`)
- Modify: `governance/act_journal.py` (Р-23, C7-01: `ForgeEpochs(binding)` + `via(transport)`
  вместо `reread`; `DevJournal(state_home, run_id, binding, policy_repository_id)` передаёт
  `snap.transport` в `ForgeEpochs.via` в `status`/`record`; `journal_for(..., binding=…)` вместо
  `reread=…`)
- Modify: `governance/act_status.py` (Task 14: `real_deps` — `journal_for(..., binding=binding)`;
  `live_policy` — новые подписи `load_anchor_times`/`load_ranges`)
- Modify: `governance/act_facts.py` (только докстринги модуля `:12–13` и `history` `:171`:
  «анонимный» → «транспорт источников доверия, который строит `load_trust` (Task 11c)»)
- Modify: `df/stage2_config.py` (R8, решение контроллера 01.11: `TrustBinding.history_gh_config_dir`,
  `parse_trust` принимает необязательный ключ; схема `df/trust/v1` не меняется)
- Modify: `TODO.md` (пункт handoff R8 — см. «Вне задачи»)
- Не меняется: `contracts/author-sandbox/v1/spawn-sites.toml` — место `gh auth token` проходит
  правило `allowed_literal_programs` (Р-22); `tests/test_author_sandbox_invariant.py` остаётся
  зелёным без правки
- Test: `tests/test_act_anon.py`, `tests/test_act_trust.py`, `tests/test_act_context.py`,
  `tests/test_act_journal.py` (миграция `ForgeEpochs`/`journal_for` на `binding`),
  `tests/df/test_stage2_config.py`; Create: `tests/test_act_wiring.py` (детектор построения
  транспорта вне фабрики и реестр известных мест сборки `WIRING_SITES`)

**Interfaces:**
- Consumes: 11b (`AnonTransport`, `contents_bytes`, `_HEADERS`, `_default_open`), 12a (`Binding`,
  `parse_binding`, `_BINDING_KEYS`, `load_binding`, `Credential`, `credentials`, `gh_get`,
  `_GH_ENV_DROPPED`, `SUPPORTED_HOST`, `Readers`, `default_readers`, `_self_report`, `load_trust`,
  `read_trust_snapshot`, `load_anchor_times`, `load_ranges`, `act_context.delivery_verdict`),
  14 (`act_status.real_deps`, `live_policy`, `Deps`); этап 1 установщика (`df.config._check_keys`,
  `_need`, `ConfigError`).
- Produces (`df.stage2_config`): `TrustBinding(..., transport, history_gh_config_dir: str | None =
  None)` — `__post_init__`: `None` или строка с `PurePosixPath(value).is_absolute()`, иначе
  `ConfigError("history_gh_config_dir: must be an absolute path")`; `parse_trust` —
  `_check_keys(data, _TRUST_KEYS, {"history_gh_config_dir"}, "trust.toml")`. Писателя
  `trust.toml` в `df/` нет — правка только разбора.
- Produces (`act_anon`):
  `AnonTransport(*, open_url: Callable[..., Any] | None = None, timeout: float = TIMEOUT,
  token: str | None = None)` — с токеном каждый запрос несёт `Authorization: Bearer <token>`,
  без токена заголовка нет; остальное (только `GET` относительного пути, без перенаправлений и
  прокси, `MAX_BODY`, память `_IMMUTABLE`, `sent`) — как в 11b; `Response.error` токена не
  содержит (`***`).
- Produces (`act_trust`):
  `HISTORY_KEY = "history_gh_config_dir"`, `HISTORY_LABEL = "profile:history"`;
  `HISTORY_NOT_WIRED = "самоотчёт: credential не из закрытого набора credentials()"`;
  `Binding(host, df_trust_repository_id, df_trust_full_name, df_trust_branch,
  approval_policy_repository_id, transport, history_gh_config_dir: Path | None = None)`;
  `parse_binding(text) -> Binding` — `HISTORY_KEY` необязателен, если есть — абсолютный путь
  (`TrustError`); `load_binding` — без изменений;
  `history_token(config_dir: Path) -> str | Unavailable`;
  `history_transport(binding: Binding, *, open_url: Callable[..., Any] | None = None,
  token_source: Callable[[Path], str | Unavailable] | None = None) -> AnonTransport |
  Unavailable` — **единственное** место построения транспорта источников доверия;
  `TrustSnapshot(root, full_name, source, epochs, transport=None)` — `transport` объявлен
  `field(default=None, repr=False, compare=False)`; его задаёт `read_trust_snapshot(transport,
  binding)` (транспорт, которым снимок прочитан); `Trust.transport` — свойство
  `self.snapshot.transport` (отдельного поля нет);
  `load_trust(readers, binding, journal, *, creds, open_url=None, token_source=None) -> Trust |
  Unavailable`;
  `load_anchor_times(trust, t)`, `load_ranges(trust, anchor_at, t, journal)` — читают
  `trust.transport` (параметр `anon` удалён);
  `Readers(as_credential: Callable[[Credential, str], Fact[object]])`; `default_readers() ->
  Readers` (`Readers(gh_get)`);
  `Credential(label, gh_config_dir=None, token=None)` — `token` объявлен `field(default=None,
  repr=False)`;
  `transport_fact(transport, cred: Credential, path: str) -> Fact[object]`;
  `credentials(env, *, agent_dir, merge_dir=None)` — без изменений (токена истории в нём нет).
- Produces (`act_journal`): `ForgeEpochs(binding: Binding)` с `via(transport) -> None` и
  `head(root)` — `read_trust_snapshot(<транспорт из via>, binding)`; без `via` —
  `store.Unavailable("транспорт решения не передан")`; `DevJournal(state_home, run_id, binding,
  policy_repository_id)`; `journal_for(*, state_home, run_id, binding, env,
  policy_repository_id)`. Протокол `Journal` (`status`, `record`) не меняется.

Правила (R1–R7). `history_transport`: ключа нет → свежий `AnonTransport(open_url=open_url)`,
`token_source` не вызывается; ключ `D` → `(token_source or history_token)(D)` **ровно один раз**:
`Unavailable` → `Unavailable(f"токен истории: {причина}")` без единого запроса (R5, Р-19 — сбой
токена виден в причине); строка → `AnonTransport(open_url=open_url, token=<значение>)`.
`load_trust` по порядку: «нет credentials» (как в 12a) → `_creds_refusal(creds)` → транспорт
`history_transport(binding, …)` → снимок и журнал (`read_trust_snapshot(transport, …)`) →
`_self_report(readers, creds, snap)` (только переданные credentials, `readers.as_credential`) →
при ключе — `_history_self_report(transport, snap)` → `_rulesets(transport, …)` →
`_rebase(transport, …)` → `Trust(snap, unverified)`. Время якорей, диапазоны и наборы
политик читают `trust.transport` (R7) — другого транспорта у решения нет. Значение токена не
входит ни в одну причину `Unavailable`, ни в `Fact.detail`, ни в `repr`.

- **Запись самоотчёта `profile:history` создаётся только внутри `load_trust`** — из того же
  значения токена, которым построен транспорт: самоотчёт выполняет `_history_self_report(transport,
  snap)` — `GET repos/{approval-policy}` и `GET repos/{df-trust}` **этим объектом**
  (`transport_fact` → `permissions_refusal`; нарушение — событие и отказ, иначе `Unavailable` с
  меткой `profile:history` и репо). `readers.as_credential` для неё не вызывается (Р-18).
- **Закрытый набор форм credentials** (`_creds_refusal`, единственная проверка формы, до
  ввода-вывода): допустимы ровно формы `credentials()` — `profile:agent`, `profile:merge` (есть
  `gh_config_dir`, нет `token`), `env:GH_TOKEN`, `env:GITHUB_TOKEN` (есть `token`, нет
  `gh_config_dir`). Любой иной credential — с меткой `profile:history`, с неизвестной меткой, с
  токеном под меткой профиля — → `Unavailable(HISTORY_NOT_WIRED)`, ни одного запроса и ни одного
  вызова `token_source`.
- **Р-23 — один транспорт на решение** (C7-01). Журнал периметра перечитывает голову
  `df-trust` во время `journal.status`/`record` внутри `load_trust` (и при повторах `STALE`, до
  трёх снимков). Он делает это **тем же объектом транспорта**: снимок несёт транспорт
  (`TrustSnapshot.transport`), `DevJournal` передаёт его `ForgeEpochs.via`, а `ForgeEpochs`
  хранит только привязку. Ни журнал, ни повтор снимка транспорт не строят и `token_source` не
  вызывают: на решение — ровно одно чтение токена.

- [ ] **Step 1: Write the failing tests** — `tests/test_act_anon.py`. Тесты 11b, держащие
  гарантии транспорта, получают параметр `kw` (`KW` ниже) и строят транспорт
  `AnonTransport(open_url=…, **kw)` (`_anon(routes, **kw)`): `test_redirect_is_not_followed`,
  `test_oversized_response_refused`, `test_only_relative_get`, `test_link_and_query_passed_through`,
  `test_memo_only_for_sha_addressed_paths`, `test_memo_negatives`, `test_memo_returns_copies`,
  `test_library_reads_activation_through_it`, `test_contents_bytes_bound_to_tree_blob_oid`,
  `test_contents_bytes_checked_against_blob_oid`, `test_absent_only_from_full_tree`,
  `test_contents_404_with_path_in_tree_is_unavailable`, `test_truncated_tree_is_unavailable_not_absent`,
  `test_unreadable_commit_is_unavailable`, `test_bad_repo_slug_sends_nothing`,
  `test_contents_bytes_rejects_non_sha_ref`, `test_real_opener_does_not_follow_redirects`,
  `test_env_proxy_is_ignored`. `_Redirector` дополнительно пишет путь каждого запроса в
  `_Redirector.seen` (очищается в тесте): `test_real_opener_does_not_follow_redirects[token-…]`
  утверждает `_Redirector.seen == [f"/repos/o/r/{code}"]` — запрос с заголовком не повторён на
  `Location`. `test_no_credentials_ever` остаётся без параметра (анонимная ветка). Новые тесты:

```python
TOKEN = "ghp_History0token"
KW = pytest.mark.parametrize("kw", [{}, {"token": TOKEN}], ids=["anon", "token"])
COMMIT = "repos/o/r/commits/" + "c" * 40


@pytest.mark.parametrize(
    ("kw", "auth"), [({}, None), ({"token": TOKEN}, f"Bearer {TOKEN}")], ids=["anon", "token"]
)
def test_token_only_in_authorization_header(kw: dict, auth: str | None) -> None:
    """R1: токен — только заголовок `Authorization`; без токена заголовка нет."""
    fake = _Fake({act_anon.API + "repos/o/r": (200, {"id": 1}, None)})
    act_anon.AnonTransport(open_url=fake, **kw).send(_get("repos/o/r"))
    headers = fake.headers[0]
    assert headers.get("Authorization") == auth
    assert all(TOKEN not in v for k, v in headers.items() if k != "Authorization")


def test_token_never_in_error_sent_or_memo() -> None:
    """R1: текст сбоя доставки, журнал `sent`, ключи памяти и `repr` — без токена."""

    def leaky(req, timeout):  # noqa: ANN001
        raise OSError(f"proxy said: {dict(req.header_items())}")

    resp = act_anon.AnonTransport(open_url=leaky, token=TOKEN).send(_get(COMMIT))
    assert resp.status is None and TOKEN not in resp.error and "***" in resp.error
    t = act_anon.AnonTransport(
        open_url=_Fake({act_anon.API + COMMIT: (200, {}, None)}), token=TOKEN
    )
    t.send(_get(COMMIT))
    assert t.sent == [COMMIT] and list(t._memo) == [COMMIT]
    assert TOKEN not in repr(t)
```

`tests/test_act_trust.py` (импорты дополняются `io`, `json`, `subprocess`, `from dataclasses
import replace`, `from types import SimpleNamespace`, `from email.message import Message`, `act_anon` в импорт `governance`;
`DF_TOKEN_ENV` — пять имён `_GH_ENV_DROPPED`). **Миграция тестов 12a** на новые подписи (без
изменения их утверждений): стенд `_Wire` (ниже) встаёт рядом с `Anon`; помощник `load` —
`act_trust.load_trust(act_trust.Readers(as_credential(answers or {})), binding(), journal or
Journal(), creds=creds, open_url=_Wire(routes))`; `test_missing_bypass_field_not_read`,
`test_issue_signal_not_part_of_protocol`, `test_load_trust_without_credentials_unavailable` —
`wire = _Wire(...)`, `open_url=wire`, пути из `[p for p, _ in wire.calls]` вместо `anon.sent`;
вызовы `load_anchor_times(anon, trust, t)` / `load_ranges(anon, trust, …)` →
`load_anchor_times(_via(trust, anon), t)` / `load_ranges(_via(trust, anon), …)`, где
`_via(trust, anon) = replace(trust, snapshot=replace(trust.snapshot, transport=anon))`.
`tests/test_act_journal.py`: `ForgeEpochs(Head(...))` → `ForgeEpochs(binding())` с
`monkeypatch.setattr(act_journal, "read_trust_snapshot", lambda t, b: …)` и `via(object())`;
`journal_for(..., reread=…)` → `journal_for(..., binding=binding())`; новый
`test_forge_epochs_without_transport_unavailable` (без `via` → `store.Unavailable("транспорт
решения не передан")`, `read_trust_snapshot` не вызывается). `tests/test_act_context.py:73,165`: `act_trust.Readers(lambda c, p:
None)`. Новые тесты:

```python
TOKEN = "ghp_History0token"
HISTORY_TOML = BINDING_TOML + 'history_gh_config_dir = "/etc/df/gh-history"\n'
DF_TOKEN_ENV = (
    "GH_TOKEN", "GITHUB_TOKEN", "GH_HOST", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"
)


class _Done:
    def __init__(self, rc: int, out: str = "", err: str = "") -> None:
        self.returncode, self.stdout, self.stderr = rc, out, err


class _Answer(io.BytesIO):
    def __init__(self, status: int, body: object) -> None:
        super().__init__(json.dumps(body).encode())
        self.status, self.headers = status, Message()


class _Wire:
    """`open_url` настоящего `AnonTransport` поверх маршрутов `trust_routes`;
    журнал `(путь, заголовки)` каждого ушедшего запроса."""

    def __init__(self, routes: dict) -> None:
        self.routes, self.calls = routes, []

    def __call__(self, req, timeout: float):  # noqa: ANN001
        path = req.full_url.removeprefix(act_anon.API)
        self.calls.append((path, dict(req.header_items())))
        return _Answer(*self.routes[path])


class _Tokens:
    """`token_source`: журнал вызовов; значения — по очереди (последнее повторяется)."""

    def __init__(self, *values: object) -> None:
        self.values, self.calls = list(values), []

    def __call__(self, config_dir: Path) -> object:
        self.calls.append(config_dir)
        return self.values[min(len(self.calls), len(self.values)) - 1]


def _profile(tmp_path: Path) -> Path:
    profile = tmp_path / "gh-history"
    profile.mkdir()
    (profile / "hosts.yml").write_text("github.com:\n  user: reader\n")
    return profile


def _with_history(tmp_path: Path) -> act_trust.Binding:
    return replace(binding(), history_gh_config_dir=tmp_path / "gh-history")


def _self_reported(routes: dict) -> dict:
    """Ответы `GET repos/{repo}` с `permissions` — для самоотчёта транспортом (Р-18)."""
    routes[f"repos/{DT}"] = (200, {"full_name": DT, **_perms()})
    routes[f"repos/{AP}"] = (200, {**routes[f"repos/{AP}"][1], **_perms()})
    return routes


def _decide(b, routes_or_wire, *, creds=(AGENT,), tokens=None, readers=None, journal=None):
    """`load_trust` только через швы `open_url`/`token_source`."""
    wire = routes_or_wire if isinstance(routes_or_wire, _Wire) else _Wire(routes_or_wire)
    got = act_trust.load_trust(
        readers or act_trust.Readers(as_credential({})), b, journal or Journal(),
        creds=creds, open_url=wire, token_source=tokens or _Tokens(TOKEN),
    )
    return got, wire


def test_binding_history_key_optional() -> None:
    assert binding().history_gh_config_dir is None
    got = act_trust.parse_binding(HISTORY_TOML)
    assert got.history_gh_config_dir == Path("/etc/df/gh-history")


@pytest.mark.parametrize("value", ['""', '"gh-history"', "1"], ids=["empty", "relative", "int"])
def test_binding_history_key_must_be_absolute_path(value: str) -> None:
    with pytest.raises(act_trust.TrustError, match="history_gh_config_dir — абсолютный путь"):
        act_trust.parse_binding(BINDING_TOML + f"history_gh_config_dir = {value}\n")


def test_binding_history_key_agrees_with_installer() -> None:
    """Р-6 + R8: оба разборщика принимают ключ с тем же значением и отвергают те же
    недопустимые значения (абсолютный путь — и только он)."""
    from df.config import ConfigError
    from df.stage2_config import parse_trust

    ours, theirs = act_trust.parse_binding(HISTORY_TOML), parse_trust(HISTORY_TOML)
    assert ours.history_gh_config_dir == Path(theirs.history_gh_config_dir)
    assert parse_trust(BINDING_TOML).history_gh_config_dir is None
    for value in ('""', '"gh-history"', "1"):
        text = BINDING_TOML + f"history_gh_config_dir = {value}\n"
        with pytest.raises(act_trust.TrustError):
            act_trust.parse_binding(text)
        with pytest.raises(ConfigError, match="history_gh_config_dir"):
            parse_trust(text)


def test_history_token_spawn_argv_and_env(monkeypatch, tmp_path: Path) -> None:
    """R2: `gh auth token --hostname github.com` под профилем; окружение — как у `gh_get`."""
    seen: list[tuple[list[str], dict[str, str]]] = []

    def run(argv, *, env, **kw):  # noqa: ANN001, ANN003
        seen.append((argv, env))
        return _Done(0, out=TOKEN + "\n")

    for var in DF_TOKEN_ENV:
        monkeypatch.setenv(var, "stray")
    monkeypatch.setattr(act_trust.subprocess, "run", run)
    assert act_trust.history_token(_profile(tmp_path)) == TOKEN
    argv, env = seen[0]
    assert argv == ["gh", "auth", "token", "--hostname", "github.com"]
    assert not set(DF_TOKEN_ENV) & set(env)
    assert env["GH_CONFIG_DIR"] == str(tmp_path / "gh-history")


SECRET = "ghp_Leak0secret"


@pytest.mark.parametrize(
    ("case", "answer", "reason"),
    [
        ("missing", None, "профиль токена истории"),
        ("no_hosts", None, "профиль токена истории"),
        ("exit", _Done(1, out=SECRET, err=f"bad {SECRET}"),
         "gh auth token (profile:history): код выхода 1"),
        ("empty", _Done(0, out="\n", err=SECRET), "gh auth token (profile:history): пустой вывод"),
        ("form", _Done(0, out=f"{SECRET} extra\n"),
         "gh auth token (profile:history): вывод не в форме токена"),
        ("oserror", OSError(f"exec failed: {SECRET}"), "gh auth token (profile:history): OSError"),
        ("timeout", subprocess.TimeoutExpired(["gh"], 60, output=SECRET, stderr=SECRET),
         "gh auth token (profile:history): TimeoutExpired"),
    ],
    ids=["missing", "no_hosts", "exit", "empty", "form", "oserror", "timeout"],
)
def test_history_token_failures_are_unavailable(
    monkeypatch, tmp_path: Path, case: str, answer: object, reason: str
) -> None:
    """R5 / Р-20, Р-21, C1-02: профиля нет — `gh` не запускается (keyring); причина —
    фиксированная строка с кодом выхода или именем класса исключения, ни stdout, ни
    stderr, ни текста исключения (секрет — в каждом из них)."""
    profile = tmp_path / "gh-history"
    if case == "no_hosts":
        profile.mkdir()
    elif case != "missing":
        _profile(tmp_path)

    def run(*a, **k):  # noqa: ANN002, ANN003
        if answer is None:
            pytest.fail("gh запущен без профиля")
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(act_trust.subprocess, "run", run)
    got = act_trust.history_token(profile)
    assert isinstance(got, Unavailable) and reason in got.reason
    assert SECRET not in got.reason


@pytest.mark.parametrize("answer", [
    _Done(1, out=SECRET, err=SECRET), _Done(0, out=f"{SECRET} x"),
    OSError(SECRET), subprocess.TimeoutExpired(["gh"], 60, output=SECRET, stderr=SECRET),
], ids=["exit", "form", "oserror", "timeout"])
def test_history_token_error_absent_from_final_reason(monkeypatch, tmp_path, answer) -> None:
    """C1-02: и в конечной причине решения (`history_transport` → `load_trust`, настоящий
    `history_token` по умолчанию) секрета нет; запросов нет."""
    _profile(tmp_path)

    def run(*a, **k):  # noqa: ANN002, ANN003
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(act_trust.subprocess, "run", run)
    wire = _Wire({})
    got = act_trust.load_trust(act_trust.Readers(as_credential({})), _with_history(tmp_path),
                               Journal(), creds=(AGENT,), open_url=wire)
    assert isinstance(got, Unavailable) and "токен истории: gh auth token" in got.reason
    assert SECRET not in got.reason and wire.calls == []


def test_credential_repr_hides_token() -> None:
    assert "s3cret" not in repr(act_trust.Credential("env:GH_TOKEN", token="s3cret"))


def test_gh_get_refuses_history_profile(monkeypatch) -> None:
    """Р-18: самоотчёт токена истории не идёт через `gh` (токен не уходит в окружение)."""
    monkeypatch.setattr(act_trust.subprocess, "run", lambda *a, **k: pytest.fail("gh"))
    cred = act_trust.Credential("profile:history", gh_config_dir=Path("/etc/df/gh-history"))
    got = act_trust.gh_get(cred, "repos/o/p")
    assert got.outcome is Outcome.UNAVAILABLE
    assert "только через транспорт истории" in got.detail


def test_transport_fact_failure_is_unavailable() -> None:
    cred = act_trust.Credential("profile:history")
    got = act_trust.transport_fact(Anon({"repos/o/p": (403, {"message": "x"})}), cred, "repos/o/p")
    assert got.outcome is Outcome.UNAVAILABLE
    assert got.detail.startswith("GET repos/o/p (profile:history): HTTP 403")


def test_transport_fact_passes_body_exactly() -> None:
    """C1-01: успешное тело ответа уходит в `Fact` без изменений."""
    body = {"full_name": AP, "permissions": {"push": True, "maintain": False,
            "admin": False, "pull": True}, "extra": [1, {"k": None}]}
    cred = act_trust.Credential("profile:history")
    got = act_trust.transport_fact(Anon({"repos/o/p": (200, body)}), cred, "repos/o/p")
    assert got.outcome is Outcome.FOUND and got.value == body


# --- вход решения строит транспорт сам (C6, решение контроллера) -------------------


def test_history_transport_built_from_binding(monkeypatch, tmp_path: Path) -> None:
    """(а) Ключа нет — анонимно, `token_source` не вызывается. Ключ `D` — ровно один вызов
    `token_source(D)`, все запросы решения с этим токеном, самоотчёт `profile:history`
    на обоих репо — тем же транспортом; `Trust.transport` — тот же объект."""
    monkeypatch.setattr(act_trust.subprocess, "run", lambda *a, **k: pytest.fail("gh"))
    tokens = _Tokens(TOKEN, "ghp_Second0value")
    got, wire = _decide(binding(), _self_reported(trust_routes(None, J1)), tokens=tokens)
    assert isinstance(got, act_trust.Trust) and tokens.calls == [] and wire.calls
    assert all("Authorization" not in h for _, h in wire.calls)
    got, wire = _decide(
        _with_history(tmp_path), _self_reported(trust_routes(None, J1)), tokens=tokens
    )
    assert isinstance(got, act_trust.Trust)
    assert tokens.calls == [tmp_path / "gh-history"]
    assert all(h.get("Authorization") == f"Bearer {TOKEN}" for _, h in wire.calls)
    assert {p for p, _ in wire.calls} >= {f"repos/{AP}", f"repos/{DT}"}
    assert isinstance(got.transport, act_anon.AnonTransport)
    assert {p for p, _ in wire.calls} == set(got.transport.sent)


@pytest.mark.parametrize("keyed", [False, True], ids=["no_key", "key"])
@pytest.mark.parametrize(
    "extra",
    [
        act_trust.Credential("profile:history", gh_config_dir=Path("/etc/df/gh-history")),
        act_trust.Credential("profile:history", token="t"),
        act_trust.Credential("env:OTHER_TOKEN", token="t"),
        act_trust.Credential("profile:agent", gh_config_dir=Path("/x"), token="t"),
        act_trust.Credential("env:GH_TOKEN", gh_config_dir=Path("/x"), token="t"),
        act_trust.Credential("profile:agent"),
        act_trust.Credential("profile:merge"),
        act_trust.Credential("env:GH_TOKEN"),
        act_trust.Credential("env:GITHUB_TOKEN"),
    ],
    ids=["history_dir", "history_token", "unknown_label", "profile_with_token", "env_with_dir",
         "agent_without_dir", "merge_without_dir", "gh_token_without_token",
         "github_token_without_token"],
)
def test_caller_history_credential_refused_before_io(tmp_path, keyed: bool, extra) -> None:
    """(б) Credential вне закрытого набора форм `credentials()` → `HISTORY_NOT_WIRED`:
    ни одного запроса, `token_source` не вызывается."""
    tokens = _Tokens(TOKEN)
    b = _with_history(tmp_path) if keyed else binding()
    got, wire = _decide(b, trust_routes(None, J1), creds=(AGENT, extra), tokens=tokens)
    assert isinstance(got, Unavailable) and got.reason == act_trust.HISTORY_NOT_WIRED
    assert wire.calls == [] and tokens.calls == []


@pytest.mark.parametrize("keyed", [False, True], ids=["no_key", "key"])
def test_injected_transport_cannot_reach_trust_reads(tmp_path, keyed: bool) -> None:
    """(в) Объект, похожий на прежние `Readers` с транспортом (`anon`, `history`), — его
    транспорт не получает ни одного запроса источников доверия; все чтения — через
    `open_url` транспорта, построенного `load_trust`."""

    class Recorder:
        def __init__(self) -> None:
            self.sent: list[str] = []

        def send(self, request):  # noqa: ANN001
            self.sent.append(request.path)
            return v3.Response(200, body={})

    stale = Recorder()
    readers = SimpleNamespace(
        anon=stale, history=act_trust.Credential("profile:history"),
        as_credential=as_credential({}),
    )
    b = _with_history(tmp_path) if keyed else binding()
    got, wire = _decide(b, _self_reported(trust_routes(None, J1)), readers=readers)
    assert isinstance(got, act_trust.Trust)
    assert stale.sent == [] and wire.calls and got.transport is not stale


@pytest.mark.parametrize("stale_first", [False, True], ids=["clear", "stale_then_clear"])
def test_one_token_per_decision_through_journal(monkeypatch, tmp_path, stale_first: bool) -> None:
    """C7-01: настоящий путь журнала (`journal_for` → `DevJournal` → `ForgeEpochs.head`) и
    повтор `STALE`: `token_source` вызван ровно один раз на решение, каждый запрос — с T1,
    T2 не появляется ни в одном заголовке. Тот же `_Tokens` стоит и на месте
    `history_token` по умолчанию — построение транспорта в перечитывании его бы вызвало."""
    from governance import act_journal

    tokens = _Tokens(TOKEN, "ghp_Second0value")
    monkeypatch.setattr(act_trust, "history_token", tokens)
    monkeypatch.setattr(act_trust.subprocess, "run", lambda *a, **k: pytest.fail("gh"))
    monkeypatch.setattr(act_journal, "_publisher_present", lambda: False)
    b = _with_history(tmp_path)
    journal = act_journal.journal_for(
        state_home=tmp_path / "state", run_id="r", binding=b, env={}, policy_repository_id=22
    )
    if stale_first:  # первый ответ журнала — STALE: load_trust перечитывает снимок
        real_status = journal.status
        answers = [act_trust.STALE]
        monkeypatch.setattr(
            journal, "status", lambda snap: answers.pop() if answers else real_status(snap)
        )
    got, wire = _decide(b, _self_reported(trust_routes(None, J1)), tokens=tokens,
                        journal=journal)
    assert isinstance(got, act_trust.Trust)
    assert tokens.calls == [tmp_path / "gh-history"]
    assert all(h.get("Authorization") == f"Bearer {TOKEN}" for _, h in wire.calls)
    assert not any("ghp_Second0value" in str(h) for _, h in wire.calls)
    heads = [p for p, _ in wire.calls if p == f"repositories/11"]
    assert len(heads) >= (2 if stale_first else 1)


@pytest.fixture()
def history_world(monkeypatch, tmp_path: Path):
    """Ключ в привязке; профиль агента — стенд; `gh` запрещён.
    Возвращает `load(routes, journal) -> Trust | Unavailable`."""
    monkeypatch.setattr(act_trust.subprocess, "run", lambda *a, **k: pytest.fail("gh"))

    def run(routes: dict, journal: Journal) -> object:
        return _decide(_with_history(tmp_path), routes, journal=journal)[0]

    return run


@pytest.mark.parametrize(("repo", "role"), [(AP, "approval-policy"), (DT, "df-trust")])
def test_history_self_report_push_is_violation(history_world, repo: str, role: str) -> None:
    """R3, C1-01: ответ `GET repos/{repo}` транспортом истории с `push: true` —
    нарушение периметра этого репо (событие + отказ)."""
    routes = _self_reported(trust_routes(None, J1))
    routes[f"repos/{repo}"] = (200, {**routes[f"repos/{repo}"][1], **_perms(True)})
    journal = Journal()
    got = history_world(routes, journal)
    assert isinstance(got, Unavailable) and got.reason.startswith("нарушение периметра: ")
    assert len(journal.records) == 1 and journal.records[0][0] == role
    assert journal.records[0][1].startswith(f"profile:history на {repo}: ")


@pytest.mark.parametrize("repo", [AP, DT])
@pytest.mark.parametrize(
    ("answer", "tail"),
    [
        ((200, {"full_name": "x"}), "права credential на источнике не получены"),
        ((403, {"message": "Forbidden"}), "HTTP 403"),
    ],
    ids=["no_permissions", "http_403"],
)
def test_history_self_report_unreadable_is_unavailable(
    history_world, repo: str, answer: tuple, tail: str
) -> None:
    """R3, C1-01: нет `permissions` или HTTP 403 транспортом истории — `Unavailable`
    без события, с меткой credential и репо."""
    routes = _self_reported(trust_routes(None, J1))
    routes[f"repos/{repo}"] = answer
    journal = Journal()
    got = history_world(routes, journal)
    assert isinstance(got, Unavailable) and journal.records == []
    assert f"самоотчёт profile:history на {repo}: " in got.reason
    assert got.reason.endswith(tail)


def test_as_credential_never_called_for_history(tmp_path) -> None:
    """`readers.as_credential` получает только переданные не-history credential."""
    asked: list[str] = []

    def recorder(cred: act_trust.Credential, path: str) -> Fact[object]:
        asked.append(cred.label)
        return Fact(Outcome.FOUND, _perms(), "ok")

    got, _ = _decide(_with_history(tmp_path), _self_reported(trust_routes(None, J1)),
                     readers=act_trust.Readers(recorder))
    assert isinstance(got, act_trust.Trust)
    assert asked == ["profile:agent", "profile:agent"]


@pytest.mark.parametrize(
    "why", ["профиль токена истории /etc/df/gh-history не найден", "gh auth token: exit 1"]
)
def test_broken_history_token_no_anonymous_fallback(tmp_path, why: str) -> None:
    """R5 / Р-19, отрицательный двойник: ключ есть, токена нет — ни одного запроса."""
    got, wire = _decide(_with_history(tmp_path), trust_routes(None, J1),
                        tokens=_Tokens(Unavailable(why)))
    assert isinstance(got, Unavailable) and got.reason == f"токен истории: {why}"
    assert wire.calls == []


# --- матрица самоотчёта (C2-01: механизм вместо точечных тестов) ------------------
# Строки — виды credential, столбцы — состояния самоотчёта на одном репо. Четыре вида
# передаёт `credentials()`, `profile:history` создаёт `load_trust` (ключ в привязке).
# `gh_get` — записывающий стенд. Не чистая клетка отличается от чистой ТОЛЬКО
# состоянием этого credential.

KINDS = ("profile:agent", "profile:merge", "profile:history", "env:GH_TOKEN", "env:GITHUB_TOKEN")
STATES = ("push", "no_permissions", "http_403")
CELLS = [(k, s, r) for k in KINDS for s in STATES for r in (AP, DT)]
ROLE = {AP: "approval-policy", DT: "df-trust"}


def _gh_answer(state: str, repo: str) -> Fact[object]:
    return {
        "push": Fact(Outcome.FOUND, _perms(True), "ok"),
        "no_permissions": Fact(Outcome.FOUND, {"full_name": repo}, "ok"),
        "http_403": unavailable(f"gh api repos/{repo}: HTTP 403"),
    }[state]


def _wire_answer(routes: dict, state: str, repo: str) -> tuple[int, object]:
    return {
        "push": (200, {**routes[f"repos/{repo}"][1], **_perms(True)}),
        "no_permissions": (200, {"full_name": repo}),
        "http_403": (403, {"message": "Forbidden"}),
    }[state]


def _matrix(monkeypatch, tmp_path: Path, cell: tuple[str, str, str] | None):
    """Один прогон `load_trust` со всеми пятью видами; `cell` — (вид, состояние, репо)
    единственного отклонения или `None` (все чисты)."""
    seen: list[tuple[act_trust.Credential, str]] = []
    kind, state, repo = cell or (None, None, None)

    def gh_get(cred: act_trust.Credential, path: str) -> Fact[object]:
        seen.append((cred, path))
        if cred.label == kind and path == f"repos/{repo}":
            return _gh_answer(state, repo)
        return Fact(Outcome.FOUND, _perms(), "ok")

    monkeypatch.setattr(act_trust, "gh_get", gh_get)
    monkeypatch.setattr(act_trust.subprocess, "run", lambda *a, **k: pytest.fail("gh"))
    routes = _self_reported(trust_routes(None, J1))
    if kind == "profile:history":
        routes[f"repos/{repo}"] = _wire_answer(routes, state, repo)
    creds = act_trust.credentials(
        {"GH_TOKEN": "tok-gh", "GITHUB_TOKEN": "tok-github"},
        agent_dir=tmp_path / "review", merge_dir=tmp_path / "merge",
    )
    journal = Journal()
    got, wire = _decide(_with_history(tmp_path), routes, creds=creds,
                        readers=act_trust.default_readers(), journal=journal)
    return got, journal, seen, creds, wire


def test_self_report_matrix_clean_routes_every_credential(monkeypatch, tmp_path) -> None:
    """Чистая строка: решение есть; каждый переданный credential дошёл до `gh_get` ТЕМ ЖЕ
    объектом на оба репо; history — только транспортом, с заголовком токена."""
    got, journal, seen, creds, wire = _matrix(monkeypatch, tmp_path, None)
    assert isinstance(got, act_trust.Trust) and journal.records == []
    assert {c.label for c in creds} == set(KINDS) - {"profile:history"}
    assert all(c.label != "profile:history" for c, _ in seen)
    for cred in creds:
        assert [p for c, p in seen if c is cred] == [f"repos/{AP}", f"repos/{DT}"]
    assert {p for p, h in wire.calls if h.get("Authorization") == f"Bearer {TOKEN}"} >= {
        f"repos/{AP}", f"repos/{DT}"
    }


@pytest.mark.parametrize(("kind", "state", "repo"), CELLS,
                         ids=[f"{k}-{s}-{ROLE[r]}" for k, s, r in CELLS])
def test_self_report_matrix_cell(monkeypatch, tmp_path, kind: str, state: str, repo: str) -> None:
    """Каждая не чистая клетка: `push` — нарушение с событием ровно этого credential и
    репо; `no_permissions`/`http_403` — `Unavailable` без события с меткой credential."""
    got, journal, seen, creds, _ = _matrix(monkeypatch, tmp_path, (kind, state, repo))
    assert isinstance(got, Unavailable)
    if kind != "profile:history":  # маршрутизация: самоотчёт этого credential дошёл до gh_get
        assert any(c.label == kind and p == f"repos/{repo}" for c, p in seen)
    if state == "push":
        assert got.reason.startswith("нарушение периметра: ")
        assert journal.records == [(ROLE[repo], journal.records[0][1])]
        assert journal.records[0][1].startswith(f"{kind} на {repo}: ")
    else:
        assert journal.records == []
        assert f"самоотчёт {kind} на {repo}: " in got.reason
```

`tests/df/test_stage2_config.py` (рядом с `test_trust_unknown_key`; `TRUST` — фикстура файла):

```python
HISTORY = 'history_gh_config_dir = "/etc/df/gh-history"\n'


def test_trust_history_key_absent_unchanged() -> None:
    b = parse_trust(TRUST.format(transport="public"))
    assert b.history_gh_config_dir is None and b.root_id == ("github.com", 5, "main")


def test_trust_history_key_absolute_path() -> None:
    b = parse_trust(TRUST.format(transport="public") + HISTORY)
    assert b.history_gh_config_dir == "/etc/df/gh-history"


@pytest.mark.parametrize("value", ['""', '"gh-history"', '"./gh"', "1"],
                         ids=["empty", "relative", "dot", "int"])
def test_trust_history_key_rejects_bad_path(value: str) -> None:
    with pytest.raises(ConfigError, match="history_gh_config_dir: must be an absolute path"):
        parse_trust(TRUST.format(transport="public") + f"history_gh_config_dir = {value}\n")
```

`test_trust_unknown_key` остаётся: прочие незнакомые ключи отвергаются.

`tests/test_act_wiring.py` (новый; **детектор, а не доказательство полноты**: полноту даёт то,
что `load_trust` транспорт извне не принимает):

```python
"""Транспорт источников доверия строит только `act_trust.history_transport` (С-1, C6).

ДЕТЕКТОР, А НЕ ДОКАЗАТЕЛЬСТВО ПОЛНОТЫ. Сканер ищет в `governance/` построение
`AnonTransport` вне фабрики в трёх формах: прямой вызов, атрибутный
(`act_anon.AnonTransport(...)`) и через алиас `from … import AnonTransport as T`.
Присваивания и прочие обходы он не ловит и ловить не обязан: транспорт, построенный
где угодно ещё, до решения не доходит — `load_trust` строит свой. Реестр
`WIRING_SITES` — известные боевые места сборки зависимостей решения; тест прогоняет
каждое через `load_trust` с ключом истории (перекрёстная проверка, не полнота).
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("steward")

from governance import act_status, act_trust  # noqa: E402
from governance.act_time import Unavailable  # noqa: E402
from tests.test_act_trust import (  # noqa: E402
    AP, J1, TOKEN, Journal, _perms, _self_reported, _Tokens, _Wire, as_credential, trust_routes,
)

ROOT = Path(__file__).resolve().parent.parent
ENV = {"REVIEW_GH_CONFIG_DIR": "/x/review", "MERGE_GH_CONFIG_DIR": "/x/merge"}
Site = Callable[[Path], tuple[act_trust.Readers, tuple[act_trust.Credential, ...]]]
_CTOR = "AnonTransport"
#: Единственное разрешённое место построения — исключается поимённо.
_FACTORY = frozenset({"governance/act_trust.py::history_transport"})


def _status_site(tmp_path: Path):
    state = SimpleNamespace(repo_slug="o/p", ws_id="w", target_dir=str(tmp_path), run_id="r")
    env = {**ENV, "XDG_CONFIG_HOME": str(tmp_path / "cfg"), "XDG_STATE_HOME": str(tmp_path)}
    deps = act_status.real_deps(state, env=env)
    return deps.readers, deps.creds


#: `<файл>::<функция>` → сборка зависимостей решения этим местом. Task 15 добавляет
#: обязательную строку `governance/act_premerge.py::real_deps`.
WIRING_SITES: dict[str, Site] = {
    "governance/act_status.py::real_deps": _status_site,
}


def _constructions(rel: str, text: str) -> set[str]:
    """Места построения `AnonTransport` трёх распознаваемых форм в одном модуле."""
    tree = ast.parse(text)
    names = {_CTOR} | {
        a.asname
        for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        for a in node.names if a.name == _CTOR and a.asname
    }
    found: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            f = node.func if isinstance(node, ast.Call) else None
            if (isinstance(f, ast.Name) and f.id in names) or (
                isinstance(f, ast.Attribute) and f.attr == _CTOR
            ):
                found.add(f"{rel}::{fn.name}")
    return found - _FACTORY


@pytest.mark.parametrize(
    "src",
    [
        "from governance.act_anon import AnonTransport\ndef f():\n    AnonTransport()\n",
        "from governance import act_anon\ndef f():\n    act_anon.AnonTransport(token='t')\n",
        "from governance.act_anon import AnonTransport as T\ndef f():\n    T()\n",
    ],
    ids=["direct", "attribute", "import_alias"],
)
def test_detector_sees_claimed_forms(src: str) -> None:
    assert _constructions("governance/x.py", src) == {"governance/x.py::f"}


def test_detector_excludes_only_the_factory() -> None:
    src = ("from governance.act_anon import AnonTransport\n"
           "def history_transport(b):\n    return AnonTransport()\n"
           "def default_readers():\n    return AnonTransport()\n")
    assert _constructions("governance/act_trust.py", src) == {
        "governance/act_trust.py::default_readers"
    }


def test_no_trust_transport_outside_factory() -> None:
    found = {
        site
        for path in sorted((ROOT / "governance").rglob("*.py"))
        if path.name != "act_anon.py"
        for site in _constructions(path.relative_to(ROOT).as_posix(), path.read_text("utf-8"))
    }
    assert found == set()


@pytest.mark.parametrize("site", sorted(WIRING_SITES))
def test_wiring_site_decides_with_history_token(monkeypatch, tmp_path: Path, site: str) -> None:
    """Сборка места проходит закрытый набор форм credentials, а токен истории с `push`
    на источнике даёт нарушение периметра, а не решение."""
    with_key = act_trust.Binding(
        "github.com", 11, "owner/df-trust", "main", 22, "public", tmp_path / "gh-history"
    )
    routes = _self_reported(trust_routes(None, J1))
    routes[f"repos/{AP}"] = (200, {**routes[f"repos/{AP}"][1], **_perms(True)})
    monkeypatch.setattr(act_trust, "load_binding", lambda *a, **k: with_key)
    monkeypatch.setattr(act_trust, "gh_get", as_credential({}))
    readers, creds = WIRING_SITES[site](tmp_path)
    got = act_trust.load_trust(readers, with_key, Journal(), creds=creds,
                               open_url=_Wire(routes), token_source=_Tokens(TOKEN))
    assert isinstance(got, Unavailable)
    assert got.reason.startswith("нарушение периметра: profile:history на ")
```

Вызовы в тестах не сканируются; `act_anon.py` — определение класса, не место построения.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group governance pytest tests/test_act_anon.py tests/test_act_trust.py tests/test_act_context.py tests/df/test_stage2_config.py tests/test_act_wiring.py -v`
Expected: FAIL — `TypeError: AnonTransport.__init__() got an unexpected keyword argument 'token'`,
`TypeError: load_trust() got an unexpected keyword argument 'open_url'`, `AttributeError: …
'history_token'`, `TrustError: trust.toml: ключи …` на `HISTORY_TOML`, `ConfigError: trust.toml:
unknown key 'history_gh_config_dir'`. Тесты 11b с `[anon]` — PASS (гарантии не тронуты).

- [ ] **Step 3: Transport with the history token** — `governance/act_anon.py` (докстринг модуля:
  «без credentials либо с read-only токеном истории, спека rev 18 §6.2»):

```python
class AnonTransport:
    """`Transport` steward источников доверия: без credentials либо с read-only
    токеном истории (спека rev 18 §6.2); строит только `act_trust.history_transport`;
    `sent` — пути, ушедшие в сеть."""

    def __init__(
        self,
        *,
        open_url: Callable[..., Any] | None = None,
        timeout: float = TIMEOUT,
        token: str | None = None,
    ) -> None:
        self._open = open_url or _default_open()
        self._timeout = timeout
        self._token = token
        auth = {} if token is None else {"Authorization": f"Bearer {token}"}
        self._headers = _HEADERS | auth
        self._memo: dict[str, Any] = {}
        self.sent: list[str] = []

    # send — без изменений; _fetch строит запрос с `headers=self._headers`, а
    # сбой доставки — `cls(None, error=self._mask(f"anon: {type(exc).__name__}: {exc}"))`

    def _mask(self, text: str) -> str:
        """Значение токена в тексте сбоя — `***` (R1)."""
        return text if self._token is None else text.replace(self._token, "***")
```

Перенаправления не исполняются (`_NoRedirect`), прокси окружения не используются
(`ProxyHandler({})`) — поэтому заголовок с токеном не уходит ни на `Location`, ни в прокси;
`API` — `https://api.github.com/`, другого хоста у транспорта нет.

- [ ] **Step 4: Binding key, token, factory, decision entry** — `governance/act_trust.py`
  (импорты: `re`, `field` из `dataclasses`):

```python
HISTORY_KEY = "history_gh_config_dir"
HISTORY_LABEL = "profile:history"
HISTORY_NOT_WIRED = "самоотчёт: credential не из закрытого набора credentials()"
_TOKEN_FORM = re.compile(r"[A-Za-z0-9_]+")
#: Закрытый набор форм `credentials()`: метка → (есть gh_config_dir, есть token).
_CRED_SHAPES = {
    "profile:agent": (True, False),
    "profile:merge": (True, False),
    "env:GH_TOKEN": (False, True),
    "env:GITHUB_TOKEN": (False, True),
}


@dataclass(frozen=True)
class Binding:
    """Защищённая привязка `/etc/df/trust.toml` (спека B §3.7; Р-6; С-1 — `HISTORY_KEY`)."""

    host: str
    df_trust_repository_id: int
    df_trust_full_name: str
    df_trust_branch: str
    approval_policy_repository_id: int
    transport: str
    history_gh_config_dir: Path | None = None


@dataclass(frozen=True)
class Credential:
    """Credential, который процесс ДЕРЖИТ: профиль `gh` либо токен окружения."""

    label: str
    gh_config_dir: Path | None = None
    token: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Readers:
    """Самоотчёт переданных credentials (`gh api` под каждым). Транспорта источников
    доверия здесь нет: его строит `load_trust` (Task 11c, C6)."""

    as_credential: Callable[[Credential, str], Fact[object]]


@dataclass(frozen=True)
class TrustSnapshot:
    """`source.env` и журнал эпох из ОДНОГО снимка; `transport` — чем он прочитан."""

    root: TrustRoot
    full_name: str
    source: SourceV2
    epochs: tuple[Epoch, ...]
    transport: Any = field(default=None, repr=False, compare=False)


# Trust: поля прежние; новое свойство
#     @property
#     def transport(self) -> Any:
#         return self.snapshot.transport


def default_readers() -> Readers:
    """Боевой самоотчёт: `gh api` под credential."""
    return Readers(gh_get)


def history_transport(
    binding: Binding,
    *,
    open_url: Callable[..., Any] | None = None,
    token_source: Callable[[Path], str | Unavailable] | None = None,
) -> AnonTransport | Unavailable:
    """Единственное место построения транспорта источников доверия (С-1, C6):
    ключа нет — анонимный; ключ `D` — токен `(token_source or history_token)(D)`
    ровно один раз; сбой — `Unavailable(f"токен истории: {причина}")`."""


def _creds_refusal(creds: Sequence[Credential]) -> Unavailable | None:
    """Каждый credential — форма из `_CRED_SHAPES`, иначе `HISTORY_NOT_WIRED`."""


def _history_self_report(transport: Any, snap: TrustSnapshot) -> Found:
    """Самоотчёт `profile:history` объектом транспорта решения: `GET repos/{repo}`
    обоих репо доверия через `transport_fact`, вердикт — `permissions_refusal`;
    нарушение `(роль, f"profile:history на {repo}: …")`, недоступность
    `Unavailable(f"самоотчёт profile:history на {repo}: …")`."""


def load_trust(
    readers: Readers,
    binding: Binding,
    journal: Journal,
    *,
    creds: Sequence[Credential],
    open_url: Callable[..., Any] | None = None,
    token_source: Callable[[Path], str | Unavailable] | None = None,
) -> Trust | Unavailable:
    """Обязательные проверки перед решением (12a) на транспорте, который строит сам
    (порядок — правила Task 11c)."""


def transport_fact(transport: Any, cred: Credential, path: str) -> Fact[object]:
    """`GET <path>` транспортом решения; не 200 или не объект — `unavailable(
    f"GET {path} ({cred.label}): HTTP {status} {error}")`."""


def history_token(config_dir: Path) -> str | Unavailable:
    """Токен профиля `history`: `gh auth token --hostname github.com` под
    `GH_CONFIG_DIR=<config_dir>`, окружение без `_GH_ENV_DROPPED`, `timeout=60`.
    `<config_dir>/hosts.yml` не файл — `Unavailable` ДО запуска `gh` (Р-20).

    Безопасный формат причин (C1-02): только фиксированные строки
    `f"gh auth token ({HISTORY_LABEL}): " + <хвост>`, где хвост — `код выхода
    {rc}` | `пустой вывод` | `вывод не в форме токена` | `{type(exc).__name__}`
    (`OSError`, `TimeoutExpired`). stdout, stderr и `str(exc)` в причину не
    попадают никогда — ни целиком, ни срезом."""
```

`parse_binding`: проверка ключей — `set(_BINDING_KEYS) <= keys <= set(_BINDING_KEYS) | {HISTORY_KEY}`
(сообщение сохраняет «ключи …» — строка реестра `keys`); значение `HISTORY_KEY`, если есть, —
`str` с `Path(value).is_absolute()`, иначе `TrustError(f"trust.toml: {HISTORY_KEY} — абсолютный
путь")`; в `Binding` — `Path(value)` либо `None`. `gh_get` первой строкой отказывает
`profile:history` (`unavailable(f"gh api {path} ({HISTORY_LABEL}): самоотчёт токена истории —
только через транспорт истории")`). `_snapshot_for_decision`, `_rulesets`, `_rebase` получают
транспорт аргументом вместо `readers.anon`; `load_anchor_times(trust, t)` и `load_ranges(trust,
anchor_at, t, journal)` читают `trust.transport` (и передают его в `act_facts.activation`/
`history`, `policy_set`); `_self_report` — без изменений (ему приходят только формы
`_CRED_SHAPES`). `act_context.delivery_verdict` — `load_anchor_times(trust, t)`,
`load_ranges(trust, anchors, t, journal)`.

- [ ] **Step 4a: Installer parser** — `df/stage2_config.py` по Produces (`df.stage2_config`):
  поле `history_gh_config_dir: str | None = None` последним в `TrustBinding`, проверка в
  `__post_init__`, необязательный ключ в `_check_keys`, значение `data.get(...)` в конструктор.
  Схема и прочие ключи без изменений; потребители `TrustBinding` (`df/run_start.py`,
  `df/publisher/perimeter.py`) поле не читают.

- [ ] **Step 5: `act_journal` and `act_status` of Task 14** — `governance/act_journal.py` по
  Produces (`act_journal`): `DevJournal.status(snap)` и `record(snap, …)` первой строкой
  `self.epochs.via(snap.transport)`; тип `Reread` удаляется. `governance/act_status.py`: в
  `real_deps` закрытие журнала — `act_journal.journal_for(state_home=…, run_id=…,
  binding=binding, env=env, policy_repository_id=…)` (было `reread=lambda:
  act_trust.read_trust_snapshot(readers.anon, binding)`; аргумент `readers` закрытия больше не
  используется, сигнатура `Deps.journal` прежняя); `Deps(readers=act_trust.default_readers(), …,
  creds=act_trust.credentials(env, agent_dir=agent, merge_dir=merge), …)` — подписи прежние;
  в `live_policy` — `act_trust.load_anchor_times(trust, now)`, `act_trust.load_ranges(trust,
  anchors, now, journal)`. Докстринг `real_deps`: «Боевые зависимости: привязка, самоотчёт `gh`,
  журнал (Р-5); транспорт источников доверия строит `load_trust` (Task 11c)». Докстринги
  `act_facts` — по Files.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run --group governance pytest tests/test_act_anon.py tests/test_act_trust.py tests/test_act_context.py tests/test_act_journal.py tests/test_act_status.py tests/df/test_stage2_config.py tests/test_act_wiring.py tests/test_author_sandbox_invariant.py -v`
Expected: PASS (`test_spawn_and_dynamic_load_sites_match_the_contract` — без правки контракта,
Р-22). Затем полный набор: `uv run pytest -q` и `GOVERNANCE_REQUIRED=1 uv run --group governance
pytest -q`. ruff, pyrefly — чисто.

- [ ] **Step 7: Mutation pass** — в scratch-копии (`git worktree add` во временный каталог), по
  одной мутации на прогон названного теста; выживший мутант блокирует коммит, таблица «мутация →
  упавший тест» — в описание PR. **Предусловие:** базовый прогон Step 6 зелёный целиком, в том
  числе вся матрица (`test_self_report_matrix_*`, 31 случай); мутация засчитывается, только если
  тест падает на утверждении маршрутизации, отказа или события (`assert` после
  `load_trust`/`gh_get` в теле теста), а не на сборке стенда (`_matrix`, `_decide`, фикстуры) —
  в описании PR для каждой мутации указывается строка упавшего `assert` (C3-02):

  | # | Мутация | Должен упасть |
  |---|---|---|
  | 1 | `__init__`: заголовок `Authorization` не добавляется / добавляется всегда | `test_token_only_in_authorization_header[token]` / `[anon]` |
  | 2 | `_mask` возвращает текст как есть | `test_token_never_in_error_sent_or_memo` |
  | 4 | `parse_binding`: ключ обязателен / любой лишний ключ допустим / проверка абсолютного пути снята | `test_binding_history_key_optional` / `test_binding_rejects_bad_text[keys]` / `test_binding_history_key_must_be_absolute_path` |
  | 5 | `history_token`: без проверки `hosts.yml` / без очистки окружения / без проверки кода выхода / пустого / формы | `…_failures_are_unavailable[missing,no_hosts]` / `test_history_token_spawn_argv_and_env` / `[exit]` / `[empty]` / `[form]` |
  | 5a | `history_token`: в причину добавлен `done.stderr`, `done.stdout` или `str(exc)` (снятие безопасного формата C1-02) | `test_history_token_failures_are_unavailable[exit,empty,form,oserror,timeout]`, `test_history_token_error_absent_from_final_reason` |
  | 6 | `history_transport`: сбой токена → анонимный транспорт / токен читается при отсутствии ключа | `test_broken_history_token_no_anonymous_fallback`, `test_history_token_error_absent_from_final_reason` / `test_history_transport_built_from_binding` |
  | M1 | `load_trust` берёт транспорт у вызывающего (`getattr(readers, "anon", None) or history_transport(...)`) | `test_injected_transport_cannot_reach_trust_reads[no_key,key]` |
  | M2 | `load_trust` пропускает `_history_self_report` | `test_history_self_report_push_is_violation`, `test_self_report_matrix_cell[profile:history-push-…]`, `test_wiring_site_decides_with_history_token` |
  | M3 | токен читается дважды, транспорт строится со вторым значением (`token_source(D)` повторно) | `test_history_transport_built_from_binding` (`tokens.calls == [D]`, заголовок `Bearer TOKEN`) |
  | M5 | `ForgeEpochs.head` строит транспорт сам (`history_transport(self._binding)`) вместо `via` / повтор `STALE` в `_snapshot_for_decision` строит новый транспорт | `test_one_token_per_decision_through_journal[clear]` / `[stale_then_clear]` (`tokens.calls == [D]`, нет `ghp_Second0value` в заголовках) |
  | M4 | `_creds_refusal`, по отдельности: (i) снят целиком; (ii) нет проверки метки в `_CRED_SHAPES`; (iii) нет требования `gh_config_dir` у профиля; (iv) нет требования `token` у env; (v) нет запрета `token` у профиля; (vi) нет запрета `gh_config_dir` у env | (i) все случаи `test_caller_history_credential_refused_before_io`; (ii) `[…-history_dir,history_token,unknown_label]`; (iii) `[…-agent_without_dir,merge_without_dir]`; (iv) `[…-gh_token_without_token,github_token_without_token]`; (v) `[…-profile_with_token]`; (vi) `[…-env_with_dir]` |
  | 7 | самоотчёт `profile:history` через `readers.as_credential` вместо транспорта | `test_as_credential_never_called_for_history`, `test_history_self_report_push_is_violation` |
  | 7a | `transport_fact` подменяет `permissions` чистыми (`push/maintain/admin = false`) | `test_history_self_report_push_is_violation[approval-policy,df-trust]`, `test_transport_fact_passes_body_exactly` |
  | 7b | `_self_report`: для переданных credential готовые чистые `permissions` вместо `readers.as_credential` | `test_self_report_matrix_cell[profile:agent-…]`, `[profile:merge-…]`, `[env:GH_TOKEN-…]`, `[env:GITHUB_TOKEN-…]` (все состояния), `test_self_report_matrix_clean_routes_every_credential` |
  | 7c | `_self_report` подменяет объект credential копией или теряет один вид | `test_self_report_matrix_clean_routes_every_credential` (утверждения тождества объекта и состава множеством) |
  | 8 | отказ `gh_get` для `profile:history` снят | `test_gh_get_refuses_history_profile` |
  | 10 | `Credential.token` с `repr=True` | `test_credential_repr_hides_token` |
  | 11 | `transport_fact`: статус не проверяется | `test_transport_fact_failure_is_unavailable`, `test_history_self_report_unreadable_is_unavailable[http_403-…]` |
  | 12 | детектор: не разворачивает `import … as` / не видит атрибутный вызов / исключает файл, а не фабрику | `test_detector_sees_claimed_forms[import_alias]` / `[attribute]` / `test_detector_excludes_only_the_factory` |
  | 13 | `parse_trust`: ключ не в необязательных | `test_trust_history_key_absolute_path`, `test_binding_history_key_agrees_with_installer` |
  | 14 | `TrustBinding.__post_init__`: проверка абсолютного пути снята | `test_trust_history_key_rejects_bad_path[empty,relative,dot]` |

  Строки для реестра Task 16 (`tests/test_act_identity_table.py` создаёт Task 16, она идёт
  позже — её исполнитель вносит эти строки; листинг Task 16 помечен):
  новые места суждения `act_trust.history_token` (`profile`, `exit`, `empty`, `form`, `spawn`;
  мутации 5, 5a), `act_trust.history_transport` (`token_unavailable` — тест
  `test_broken_history_token_no_anonymous_fallback`, мутация 6), `act_trust._creds_refusal`
  (`shape` — `HISTORY_NOT_WIRED`, тест `test_caller_history_credential_refused_before_io`, мутация
  M4), `act_trust._history_self_report` (`violation` — `isinstance(verdict, Violation)`, тест
  `test_history_self_report_push_is_violation`, мутации M2, 7, 7a; `unreadable` — тест
  `test_history_self_report_unreadable_is_unavailable`, мутация 11), `act_trust.transport_fact`
  (`http`); новые строки `act_trust.parse_binding#history_path`,
  `act_trust.gh_get#history_label`, `act_trust.load_trust#own_transport` (`MODIFIED_EXISTING`,
  тест `test_injected_transport_cannot_reach_trust_reads`, мутации M1, M3); литерал строки
  `parse_binding/keys` — по новой проверке ключей; `act_trust.default_readers` остаётся
  `NOT_JUDGMENT` (`_BUILD`); `act_journal.ForgeEpochs.head#no_transport` (`MODIFIED_EXISTING`,
  тест `test_forge_epochs_without_transport_unavailable`); в `NOT_JUDGMENT` —
  `act_journal.ForgeEpochs.via` (`_BUILD`), `act_trust.Trust.transport` (`_BUILD`),
  `act_anon.AnonTransport._mask` (маскировка текста; отказа нет). Строки мутаций входят в обязательный проход Task 16 Step 4a.

- [ ] **Step 8: Commit**

```bash
git add governance/act_anon.py governance/act_trust.py governance/act_context.py \
  governance/act_journal.py governance/act_status.py governance/act_facts.py \
  df/stage2_config.py TODO.md tests/test_act_anon.py tests/test_act_trust.py \
  tests/test_act_context.py tests/test_act_journal.py tests/df/test_stage2_config.py \
  tests/test_act_wiring.py
git commit -m "feat(act): read-only токен истории источников доверия (С-1, спека rev 18 §6.2)"
```

**Вне задачи:**
- **R8, сторона установщика** — handoff этапу 2b/3 установщика (спека B): запись ключа
  `darkfactory setup-trust`, проверка профиля и самоотчёта `darkfactory doctor`, шаг человека
  «создать PAT (R4) и профиль `history`: `GH_CONFIG_DIR=<каталог> gh auth login --hostname
  github.com --with-token`». Разбор ключа (`parse_trust`) — уже в этой задаче. Задача заводит
  пункт `TODO.md` devtools (`slug: df-trust-history-token`). **Человек не вписывает ключ на
  хосте, где стоит `df` версии до этой задачи**: прежний `parse_trust` отвергает незнакомый ключ
  (`darkfactory doctor`, издатель и `run_start` упадут на разборе привязки); без ключа акт
  работает анонимно (R5).
- **Инструкция steward** (`docs/approval-facts-v3.md:79–86`, «Заметки §12» п. 1) устарела —
  inbox-issue steward#232 (отправлен контроллером); код steward не меняется.
- Живой замер квоты (5000/ч, остаток `x-ratelimit-remaining` на решение) — в приёмке Task 18
  вместе с журналом `Trust.transport.sent`.

### Task 13: Путь узла — общий вход, создание заявки, леджер, finalize в конверт, остановка, аудит

Исполняется после Task 14 (её `load_history`, `Statuses`, `Deps`, `mode_probe`/`live_policy`).
Ревизия b1: B1-5 (режим по истории, без Р-1), B1-1 (привязка через `act_identity`), B1-15
(доставка конверта и остановки в базу), B1-17 (полный состав коммита).

**Files:**
- Create: `governance/act_node.py` (вся новая логика узла; `approve_node` только ветвится)
- Modify: `governance/approve_node.py` — `_advance` ~1627 (общий вход), `_propose` ~366 и
  `propose_from_source` ~664 (режим создания), `_sync_branch_to_snapshot` ~1192 (манифест в
  коммите candidate), `_publish_candidate` ~1410 / `_candidate_body` ~1491 / `_policy_ref` ~866
  (метка, тело, тексты), `_try_agent_merge` ~2451 (`form=`); `_legacy_outcome` — из Task 14
  (Step 3a)
- Modify: `governance/approval_ledger.py` (`start_request(..., act, generations)`,
  `extend_request(..., generation)`, `record_review_act`, `record_audit`, `audit_events`)
- Modify: `governance/act_decision.py` (публичный `find_act`), `governance/act_envelope.py`
  (публичный `expected_envelope`; Step 8 — `node_history`, удаление `generation_refusal`)
- Modify: `governance/act_status.py` (Step 8 — `Statuses._compute` без `generation_refusal`)
- Modify: `governance/ops.py` (`Ops.pr_body_fact` + `RealOps`)
- Modify: `governance/runner.py` (`reopen` ~1172 — остановка до волны; `resume` ~899 — аудит;
  `_print_status` ~4093 — события аудита)
- Test: `tests/test_act_node.py`, `tests/test_act_ops_body.py`; `tests/test_act_envelope.py` (две
  правки ожиданий части A, Step 8) и `tests/test_act_status.py` (Step 8);
  `tests/test_governance_approve_node.py`,
  `tests/test_governance_approval_ledger.py`, `tests/test_governance_runner.py` — без правок
  ожиданий (регрессия старого пути)

**Interfaces:**
- Consumes: Tasks 5, 5a, 8, 10, 11a, 12, 12a, 14.
- Produces:
  `act_node.HUMAN_APPROVAL_LABEL = "human-approval-required"`, `WAITING_HUMAN_APPROVAL`,
  `WAITING_DELIVERY`; `act_node.DEPS_FACTORY: Callable[[RunState], act_status.Deps]` (точка
  подмены);
  `act_node.Creation(act: str | None, pin: PolicyPin | None, history: History)`;
  `act_node.creation(state, ops, deps) -> Creation` (отказы — `RuntimeError`; неустановленное —
  `_unresolved`);
  `act_node.request_of(state, dag, op, key) -> NodeRequest`;
  `act_node.manifest_file(state, dag, op, key) -> tuple[str, str] | None`;
  `act_node.label_for(op) -> str`;
  `act_node.route(state, ops, dag, op, key, step) -> ApprovalOutcome | None` — `None` значит «старый
  код продолжает» и возвращается только: нет candidate-PR; PR открыт/закрыт без манифеста;
  смёржен без манифеста в продукте `PRE_MIGRATION` (режим по истории, B1-5); `LegacyAccepted`
  после `resolve`;
  `act_node.envelope_commit_defect(state, ops, expected, head) -> str | None`;
  `act_node.reopen(state, ops, dag, node) -> str | None` (`None` — у узла нет действующего
  одобрения `review/v1`);
  `act_node.audit_late_dismissals(state, ops, key, deps=None) -> int`;
  `approval_ledger.record_review_act(state, key, accepted, delivery, activation_commit,
  unverified, trust_root) -> None` (без умолчаний); `approval_ledger.record_audit(state, key,
  event) -> bool`, `approval_ledger.audit_events(state, key) -> list[dict]`;
  `act_decision.find_act(head_sha, effective, policy) -> ReviewView | None`;
  `act_envelope.expected_envelope(current, node, review_id) -> Envelope`;
  `act_envelope.node_history(history, node) -> NodeHistory` (`current`, `generation`, `top`,
  `ignored: tuple[Ignored, ...]`), `act_envelope.Ignored(event, reason)`; удалено —
  `act_envelope.generation_refusal` (Step 8);
  `Ops.pr_body_fact(repo_slug, pr) -> Fact[str]`.

**П. 9 заметок части A — решено владельцем 01.11: вариант (Б).** Слова владельца (01.11):

> «П. 9 — Б, с единым вычислением состояния истории. В-1 — локальный хеш содержимого допустим
> как оптимизация, но он не заменяет проверку одобрения. Перед успешным no-op для review/v1
> проверяется актуальный допуск.»

Суть: одна доставленная остановка с номером **не действующего** поколения либо заявка с
**неубывающим** поколением (обе — записи first-parent базы, переписать нельзя) по коду части A
(`act_envelope.generation_refusal`) делали узел «не одобрен» навсегда. По решению такие записи
**игнорируются**: не становятся событием узла и не сдвигают действующее поколение; следующая
заявка с бо́льшим поколением одобряет узел как обычно. Состояние истории узла вычисляется
**один раз** — `act_envelope.node_history(history, node) -> NodeHistory(current, generation, top,
ignored)` (Step 8); `current_event`, `next_generation`, `envelope_refusal`, `Statuses`
(`of`/`is_review`/`_stopped`), `act_node.reopen` и `generation_of` выводят своё из него;
`generation_refusal` удаляется — второго правила пропуска нет нигде. Остановка, которую пишет
`act_node.reopen`, всегда несёт действующее поколение (`node_history(...).current`).

История выбора (до решения): вариант (А) — «блокировка остаётся», выход — новый воркстрим;
отклонён: одна запись в базе — отказ в обслуживании узла. Риск (Б), принятый владельцем:
история может нести запись, не созданную этим кодом (только отнимающую или проигнорированную);
полномочий она не даёт.

**Общий вход (спека §10 «до выбора читателя, для обоих путей»; §2 «режим — из манифеста»).**
Маршрутизация — в `_advance`, до выбора любого шага, кроме создания:

```python
def _advance(state, ops, dag, op, key):
    step = al.next_step(op)
    if step is not al.Step.CREATE_CANDIDATE:
        from governance import act_node

        routed = act_node.route(state, ops, dag, op, key, step)
        if routed is not None:
            return routed
    ...  # прежний код без изменений
```

`route` по порядку: (1) факты PR (`_pr_facts`), голова `headRefOid`, состояние; (2) манифест из
**головы PR** (`ops.repo_file_fact(slug, head, manifest_path(ws, key))`: `FOUND` → `parse_manifest`,
`RequestError` → `invalidate_request`; `ABSENT` → без маркера; `UNAVAILABLE` → `_unresolved`);
(3) зеркала: `op.get("act") == "review/v1"` ⇔ манифест есть, иначе `invalidated` (приёмка №13,
P1-2); для `review/v1` — `mirror_mismatch(req, key, op, body)` (тело — `pr_body_fact`) и привязка к
прогону — `act_identity` (`RequestIdentity(state.repo_slug, state.ws_id, key)`:
`manifest_refusal`, `paths_refusal` по путям `_rel(state, _filename(dag, n))`) и
`upstream_pins[n]` = `bundle_inputs.direct_blobs(..., ref=head)`; расхождение → `invalidated`; (4) PR открыт без манифеста → `None`; открыт с манифестом → живое
правило (`live_policy_refusal` против `act_status.live_policy`), затем `open_review` + D1
(`find_act`) на голове: нет акта → `WAITING_HUMAN_APPROVAL`; есть → `ops.merge` (обвязка,
предпроверка Task 15) → `WAITING_DELIVERY`; (5) закрыт без мержа → `None`; (6) смёржен: без
манифеста — `mode_probe`: продукт до миграции (`PRE_MIGRATION`: записи `ACT_MODE[P]` не было
никогда) → `None` — admission при пустой истории режима даёт тот же legacy-исход (§10);
`Unavailable` (нет привязки, удалённая запись, откат) → `_unresolved`; иначе `delivery_verdict`
(Task 12a) с `legacy` =
`_legacy_outcome` (только для заявки без маркера) → `LegacyAccepted` → `None`; `Accepted` →
`record_review_act` (идемпотентно) → finalize; `Rejected` → `invalidated: <code>: <detail>`;
`Ambiguous` → `_unresolved` с выходом `--repropose`; `Unavailable` → `_unresolved` (для
`actor_unresolved` (О15) текст называет `--repropose`: факты PR не изменятся). Для шагов после
мержа (`FINALIZE`, `AWAIT_FINALIZE_MERGE`) `route` выполняет те же п. 1–6 по голове смёрженного
candidate при **каждом** входе — решение переустанавливается из фактов, а не берётся из `op`.

**Создание заявки.** `act_node.creation(state, ops, deps, live)` перед первой записью, `live` —
`mode_probe` (ревизия b1): `Unavailable` → отказ создания (`_unresolved`, не legacy);
`PRE_MIGRATION`/`merge` → без маркера (если не `draining`); `review` и `settings.review_v1` →
`review/v1` (поколение узла — `next_generation(history, node)`, пин — текущая активация и её
`files_sha256`); `review` без `review_v1` → отказ создания с причиной; `merge`/нет записи при
`draining` → отказ создания без маркера. Присоединение к живой заявке шага — только той же
версии акта и того же пина. Манифест пишется в коммит candidate (`_sync_branch_to_snapshot`), в
`op` — `ledger_view(req)` (поля `act`, `generations`, `policy` = пин); тело PR — строки
`act: review/v1`, `policy-activation: <sha>`; метка `human-approval-required`.

**Finalize `review/v1`.** Только из `route`. Конверты `envelope_path(ws, node)` — содержимое
`render_envelope(expected_envelope(DeliveredRequest(merge_commit, head, req), node, review_id))`;
frontmatter узлов не трогается. Мерж finalize-PR — прежняя дорога ADR-ECO-011 D5
(`finalize_merge_policy`, scope-аттестация, `_try_agent_merge`), но проверка формы —
`envelope_commit_defect`: коммит трогает ровно пути конвертов узлов заявки, содержимое каждого —
ожидаемое. После мержа finalize — конверты читаются **на merge-коммите finalize** и сверяются
побайтово, а сам merge-коммит обязан быть достижим из базы (`is_ancestor`; B1-15) — иначе
`invalidated`; неустановленная достижимость — `_unresolved` (Р-11). Повтор после падения берёт записанный `finalize_head_sha`
(второго конверта нет, Review Focus 5).

**Переоткрытие.** `runner.reopen` первым шагом (после локальных отказов и проверки грязного
дерева, до правки `state`) зовёт `act_node.reopen`: у узла действующее событие — доставленная
заявка `review/v1` → отдельный PR `stop/<ws-id>-<node>-g<gen>` с единственным файлом
`stop_path(ws, node, gen)` (поколение — действующее), агентский мерж (`merge-pr.sh`; одобряющее
ревью проверяется до вызова, как у finalize), проверенный `state=MERGED`, merge-коммит stop-PR
достижим из базы и несёт ровно ожидаемую запись `StopRecord(node, gen, …)` по `stop_path`
(B1-15), а перечитанная история базы (`load_history`) называет действующим событием узла именно
эту остановку этим merge-коммитом (ревизия b2: суждение «остановлен» — тем же входом, что
статус); до этого `reopen` отказывает и узел остаётся одобренным. Повтор находит свой stop-PR по ветке.

**Аудит позднего отзыва (§8, приёмка №10).** `audit_late_dismissals` — на `resume` прогона и на
вызове `--approve-node` для завершённых заявок `review/v1`: отзывы с `created_at > merged_at` →
`record_audit(key, {"event": "review_dismissed_after_delivery", "review_id", "at"})`, идемпотентно
по `review_id`. Терминальная запись заявки не мутируется (§I4): события аудита живут в соседней
записи `state.ops["audit-<key>"]` (её ключ не разбирается как заявка — `requests` требует ровно
три числа после `approve-`). `_print_status` показывает их.

- [ ] **Step 1: Write the failing tests** — `tests/test_act_node.py`:

```python
"""Путь узла review/v1 (Task 13; приёмка №1, 8, 10, 13, 24; Review Focus 5)."""

from __future__ import annotations

import subprocess
from dataclasses import fields, replace
from pathlib import Path

import pytest

pytest.importorskip("steward")

from steward.approvalfacts import v3  # noqa: E402

from governance import act_context, act_facts, act_node, act_status, act_trust  # noqa: E402
from governance import approval_ledger as al  # noqa: E402
from governance import approve_node as an  # noqa: E402
from governance import node_approval as na  # noqa: E402
from governance.act_check import ActivationView  # noqa: E402
from governance.act_decision import Accepted, DeliveryView, Rejected, ReviewView  # noqa: E402
from governance.act_envelope import (  # noqa: E402
    DeliveredRequest,
    Envelope,
    StopRecord,
    envelope_path,
    parse_envelope,
    render_envelope,
    render_stop,
    stop_path,
)
from governance.act_epochs import Epoch  # noqa: E402
from governance.act_policy import parse_policy_set  # noqa: E402
from governance.act_request import (  # noqa: E402
    NodeEntry,
    NodeRequest,
    PolicyPin,
    manifest_path,
    parse_manifest,
    render_manifest,
)
from governance.act_settings import ActSettings  # noqa: E402
from governance.act_source import SourceV2  # noqa: E402
from governance.act_time import Ambiguous, Unavailable, parse_ts  # noqa: E402
from governance.facts import Fact, Outcome  # noqa: E402
from tests.act_cases import FILES  # noqa: E402
from tests.test_governance_approve_node import (  # noqa: E402
    SLUG,
    WS_ID,
    Ops,
    World,
    _git,
    drive_to_approved,
    _make_world,
    _show,
    approve,
    merge_pr,
)

ACT = "a" * 40
NOW = parse_ts("2026-10-12T12:00:00Z")
SOURCE = SourceV2("owner/approval-policy", "main", ACT, frozenset({"owner"}))
MODES = f"schema: approval-policy-modes/v1\nmodes:\n  {SLUG}: review\n".encode()
POLICY = parse_policy_set(ACT, {**FILES, "policy/modes.yaml": MODES})
ROOT = act_trust.TrustRoot("github.com", 11, "main", "s" * 40)


def trust(unverified: tuple[str, ...] = ()) -> act_trust.Trust:
    snap = act_trust.TrustSnapshot(ROOT, "owner/df-trust", SOURCE, (Epoch(1, ACT),))
    return act_trust.Trust(snap, unverified)


class BodyOps(Ops):
    """Стенд approve_node + тело PR и файлы манифестов из origin (форджа стенда
    отдаёт по `repo_file_fact` только файлы политики)."""

    def pr_body_fact(self, repo_slug: str, pr: int) -> Fact[str]:
        return Fact(Outcome.FOUND, self.forge.prs[pr]["body"], "ok")

    def repo_file_fact(self, repo_slug, sha, path):
        if "/approvals/requests/" not in path:
            return super().repo_file_fact(repo_slug, sha, path)
        done = subprocess.run(
            ["git", "-C", str(self.forge.origin), "show", f"{sha}:{path}"],
            capture_output=True, text=True, check=False,
        )
        if done.returncode != 0:
            return Fact(Outcome.ABSENT, None, "нет файла")
        return Fact(Outcome.FOUND, done.stdout, "ok")

    def prs_containing_commit(self, repo_slug, sha):
        return [{"number": n, "state": "MERGED", "mergeCommit": sha}
                for n, rec in self.forge.prs.items()
                if (rec.get("mergeCommit") or {}).get("oid") == sha]


class Stand:
    """Подмены доверия и фактов ревью; `verdict` — исход доставки candidate."""

    def __init__(self, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        self.world = world
        self.settings = ActSettings(review_v1=True, draining=False)
        self.binding: object = object()
        self.mode = "review"
        self.mode_unset: str | None = None  # причина Unavailable режима продукта
        self.unverified: tuple[str, ...] = ()
        self.effective: dict[str, ReviewView] = {}
        self.verdict: object = None
        self.dismissals: tuple[v3.Dismissal, ...] = ()
        monkeypatch.setattr(act_node, "DEPS_FACTORY", lambda state: self.deps())
        monkeypatch.setattr(act_status, "mode_probe", lambda state, deps=None: self.probe())
        monkeypatch.setattr(act_facts, "open_review", lambda t, repo, pr: self.facts(pr))
        monkeypatch.setattr(act_facts, "effective", lambda facts, clock: dict(self.effective))
        monkeypatch.setattr(act_context, "delivery_verdict", self.deliver)
        monkeypatch.setattr(act_status.Decider, "trust", lambda d: trust(self.unverified))

    def deps(self) -> act_status.Deps:
        return act_status.Deps(readers=None, binding=self.binding, journal=lambda r, b: None,
                               creds=(), gh=None, now=lambda: NOW, sleep=lambda s: None,
                               settings=self.settings)

    def probe(self) -> act_status.LivePolicy | Unavailable:
        return Unavailable(self.mode_unset) if self.mode_unset else self.live()

    def live(self) -> act_status.LivePolicy:
        policy = POLICY if self.mode == "review" else parse_policy_set(ACT, FILES)
        activation = ActivationView(("activation", ACT), ACT, parse_ts("2026-10-12T08:00:00Z"))
        return act_status.LivePolicy(self.mode, trust(self.unverified), None, activation, policy)

    def facts(self, pr: int) -> v3.ReviewFacts:
        head = self.world.forge.head_of(self.world.forge.prs[pr]["branch"])
        return v3.ReviewFacts(SLUG, pr, head, "master", "OPEN", (), self.dismissals)

    def approve_head(self, pr: int) -> None:
        head = self.world.forge.head_of(self.world.forge.prs[pr]["branch"])
        self.effective = {"alice": ReviewView("11", "alice", "User", "APPROVED", head)}

    def deliver(self, readers, trust_, journal, *, product, pr, paths, request, request_act,
                head_sha, facts_transport, self_hashes, legacy):
        rec = self.world.forge.prs[pr]
        delivery = DeliveryView(True, rec["mergeCommit"]["oid"], rec["mergedAt"],
                                "df-reviewer", "Bot", head_sha, True, {})
        if self.verdict is not None:
            return self.verdict, delivery
        if request is None:
            return legacy(), delivery
        return Accepted(delivery.merge_commit, "df-reviewer", "11", "alice"), delivery


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    w = _make_world(tmp_path, monkeypatch, seed_nodes=True)
    return replace(w, ops=BodyOps(w.forge))


@pytest.fixture()
def stand(world: World, monkeypatch: pytest.MonkeyPatch) -> Stand:
    return Stand(world, monkeypatch)


def _candidate(world: World) -> tuple[str, dict, int]:
    first = approve(world, "charter")
    key = first.request
    op = world.state.ops[key]
    return key, op, op["candidate_pr"]


# --- создание ----------------------------------------------------------------------


def test_review_request_carries_manifest_label_and_body(world: World, stand: Stand) -> None:
    key, op, pr = _candidate(world)
    rec = world.forge.prs[pr]
    assert rec["label"] == act_node.HUMAN_APPROVAL_LABEL
    head = world.forge.head_of(rec["branch"])
    req = parse_manifest(_show(world.target, f"{head}:{manifest_path(WS_ID, key)}"))
    assert req.nodes["charter"].generation == 1
    assert req.policy.activation_commit == ACT
    assert op["act"] == "review/v1" and op["generations"] == {"charter": 1}
    assert "act: review/v1" in rec["body"] and f"policy-activation: {ACT}" in rec["body"]


def test_draining_refuses_unmarked_creation(world: World, stand: Stand) -> None:
    stand.mode, stand.settings = "merge", ActSettings(review_v1=False, draining=True)
    with pytest.raises(RuntimeError, match="draining"):
        approve(world, "charter")
    assert al.requests(world.state) == []


def test_review_mode_without_review_v1_refuses(world: World, stand: Stand) -> None:
    stand.settings = ActSettings(review_v1=False, draining=False)
    with pytest.raises(RuntimeError, match="review_v1"):
        approve(world, "charter")


def test_unset_mode_refuses_creation(world: World, stand: Stand) -> None:
    """B1-5: нет привязки / удалённая запись / откат — не legacy, а отказ создания."""
    stand.mode_unset = "нет привязки корня доверия"
    with pytest.raises(RuntimeError, match="режим акта"):
        approve(world, "charter")
    assert al.requests(world.state) == []


def test_pre_migration_keeps_legacy_creation(world: World, stand: Stand) -> None:
    stand.mode = act_trust.PRE_MIGRATION
    key, op, pr = _candidate(world)
    assert op.get("act") is None
    assert world.forge.prs[pr]["label"] == an.HUMAN_MERGE_LABEL


# --- открытый PR ---------------------------------------------------------------------


def test_open_without_approve_waits(world: World, stand: Stand) -> None:
    key, op, pr = _candidate(world)
    out = approve(world, "charter")
    assert act_node.WAITING_HUMAN_APPROVAL in out.message
    assert world.forge.merge_calls == []


def test_open_with_approve_merges_by_agent(world: World, stand: Stand) -> None:
    key, op, pr = _candidate(world)
    stand.approve_head(pr)
    out = approve(world, "charter")
    assert act_node.WAITING_DELIVERY in out.message
    assert world.forge.merge_calls[0][0] == pr
    assert world.forge.prs[pr]["state"] == "MERGED"


def test_open_candidate_head_moved_between_reads(world: World, stand: Stand,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """B3-6 (`_open`): факты ревью названы на другой голове, чем голова PR — заявка жива,
    агентский мерж не вызывается."""
    key, op, pr = _candidate(world)
    stand.approve_head(pr)
    monkeypatch.setattr(act_facts, "open_review", lambda t, repo, n: v3.ReviewFacts(
        SLUG, n, "0" * 40, "master", "OPEN", (), ()))
    with pytest.raises(RuntimeError, match="сдвинулась между чтениями"):
        approve(world, "charter")
    assert world.forge.prs[pr]["state"] == "OPEN"
    assert world.state.ops[key]["status"] == al.STATUS_STARTED


def test_open_candidate_in_merge_mode_refused(world: World, stand: Stand) -> None:
    """B3-6 (`_open`): заявка review/v1, а режим продукта сейчас не review — не мержится."""
    key, op, pr = _candidate(world)
    stand.approve_head(pr)
    stand.mode = "merge"
    with pytest.raises(RuntimeError, match="а режим продукта сейчас — merge"):
        approve(world, "charter")
    assert world.forge.prs[pr]["state"] == "OPEN"


def _level_three(world: World, stand: Stand, monkeypatch: pytest.MonkeyPatch) -> None:
    """Стенд до шага, где `design` и `acceptance` — два узла одного шага с общими
    upstream (`requirements`, `behaviour-spec`): upstream проведены старым путём до
    миграции; их статус в продукте review — «approved» (судят двойники `LegacyProbe`,
    здесь — только проверки присоединения)."""
    stand.mode = act_trust.PRE_MIGRATION
    for node in ("charter", "requirements", "behaviour-spec"):
        drive_to_approved(world, node)
    real_of = act_status.Statuses.of
    monkeypatch.setattr(act_status.Statuses, "of", lambda self, node: (
        act_status.APPROVED if node in ("charter", "requirements", "behaviour-spec")
        else real_of(self, node)))


def _child_of_charter() -> str:
    dag = an.bundle_dag.dag_for(None)
    return next(an.bundle_dag.node_id(f) for f, ups in dag if "charter" in ups)


def test_join_live_legacy_request_refused(world: World, stand: Stand,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """b5/b6 (`approve_node._propose#act_incompatible`): живая заявка шага без маркера
    (`design`, до миграции) и новая `review/v1` (`acceptance`, тот же шаг) в одну не
    соединяются."""
    _level_three(world, stand, monkeypatch)
    first = approve(world, "design")  # заявка без маркера над шагом
    stand.mode = "review"
    with pytest.raises(RuntimeError, match="в одну заявку они не соединяются"):
        approve(world, "acceptance")
    assert world.state.ops[first.request]["status"] == al.STATUS_STARTED
    assert world.state.ops[first.request]["nodes"] == ["design"]


def test_join_request_pinned_to_other_activation_refused(world: World, stand: Stand,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """b5/b6 (`approve_node._propose#other_pin`): живая заявка шага (`design`, review/v1)
    закрепила другую активацию — `acceptance` того же шага к ней не дописывается."""
    _level_three(world, stand, monkeypatch)
    stand.mode = "review"
    first = approve(world, "design")
    newer = ActivationView(("activation", "b" * 40), "b" * 40, NOW)
    monkeypatch.setattr(act_status, "mode_probe", lambda state, deps=None: replace(
        stand.live(), activation=newer))
    with pytest.raises(RuntimeError, match="закрепила другую активацию"):
        approve(world, "acceptance")
    assert world.state.ops[first.request]["nodes"] == ["design"]


def test_review_status_unavailable_blocks_proposal(world: World, stand: Stand,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """b5 (`approve_node._propose#status_unavailable`): статус узла review/v1 не установлен —
    заявка не создаётся, а не «долг по frontmatter»."""
    monkeypatch.setattr(act_status.Statuses, "is_review", lambda self, node: True)
    monkeypatch.setattr(act_status.Statuses, "of", lambda self, node: "unavailable:история")
    with pytest.raises(RuntimeError, match="статус узла charter: unavailable:история"):
        approve(world, "charter")
    assert al.requests(world.state) == []


def test_read_dag_state_mode_unset(world: World, stand: Stand) -> None:
    """b5 (`approve_node.read_dag_state#mode_unset`)."""
    stand.mode_unset = "нет привязки корня доверия"
    got = an.read_dag_state(world.state, world.ops, an.bundle_dag.dag_for(None))
    assert got.unresolved == "режим акта продукта: нет привязки корня доверия"


def test_read_dag_state_status_unavailable(world: World, stand: Stand,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """b5 (`approve_node.read_dag_state#status_unavailable`)."""
    monkeypatch.setattr(act_status.Statuses, "of", lambda self, node: "unavailable:история")
    got = an.read_dag_state(world.state, world.ops, an.bundle_dag.dag_for(None))
    assert got.unresolved == "unavailable:история"


def test_read_dag_state_review_debt(world: World, stand: Stand,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """b5 (`approve_node.read_dag_state#review_debt`): не approved — долг review/v1."""
    monkeypatch.setattr(act_status.Statuses, "of", lambda self, node: "not_approved:act:d1_no_act")
    got = an.read_dag_state(world.state, world.ops, an.bundle_dag.dag_for(None))
    debts = [d for d in got.debts if d.kind == na.DEBT_REVIEW_V1]
    assert len(debts) == 1 and debts[0].reason == "act:d1_no_act"


@pytest.mark.parametrize("verdict,match", [
    (None, "режим акта продукта \\(upstream"),
    ("unavailable:история", "upstream charter узла .*: unavailable:история"),
    ("not_approved:act:d1_no_act", "upstream charter не одобрен \\(review/v1\\)"),
], ids=["mode_unset", "upstream_unavailable", "upstream_not_approved"])
def test_upstream_readiness_in_review(world: World, stand: Stand, monkeypatch: pytest.MonkeyPatch,
                                      verdict, match: str) -> None:
    """b5 (`approve_node._require_upstream_ready#…`): готовность upstream в продукте review —
    по статусу review/v1, не по frontmatter."""
    if verdict is None:
        stand.mode_unset = "нет привязки"
    else:
        monkeypatch.setattr(act_status.Statuses, "of", lambda self, node: verdict)
    with pytest.raises(RuntimeError, match=match):
        approve(world, _child_of_charter())


def test_finalize_form_defect_blocks_agent_merge(world: World, stand: Stand,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """b5 (`approve_node._try_agent_merge#form`): дефект формы коммита конверта — агентский
    мерж finalize не выполняется, причина — дефект формы."""
    key, pr = _deliver(world, stand)
    monkeypatch.setattr(act_node, "envelope_commit_defect",
                        lambda *a: "коммит конверта трогает лишний путь")
    outcome = approve(world, "charter")
    assert "коммит конверта трогает лишний путь" in outcome.message
    final = world.state.ops[key]["finalize_pr"]
    assert world.forge.prs[final]["state"] == "OPEN"


def test_live_request_with_old_pin_invalidated(world: World, stand: Stand, monkeypatch) -> None:
    """Приёмка №8, №9, №44: пин живой заявки ≠ текущей активации."""
    key, op, pr = _candidate(world)
    newer = ActivationView(("activation", "b" * 40), "b" * 40, NOW)
    monkeypatch.setattr(act_status, "mode_probe", lambda state, deps=None: replace(
        stand.live(), activation=newer))
    with pytest.raises(RuntimeError, match="policy_changed"):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED


# --- доставка ------------------------------------------------------------------------


def _deliver(world: World, stand: Stand) -> tuple[str, int]:
    key, op, pr = _candidate(world)
    stand.approve_head(pr)
    approve(world, "charter")  # агентский мерж candidate
    return key, pr


def test_accepted_records_act_and_completes_with_envelope(world: World, stand: Stand) -> None:
    """Приёмка №1: акт засчитан, конверт доставлен, заявка завершена."""
    stand.unverified = ("allow_rebase_merge",)
    key, pr = _deliver(world, stand)
    approve(world, "charter")
    op = world.state.ops[key]
    assert op["status"] == al.STATUS_COMPLETED
    assert op["review_id"] == "11" and op["approved_by"] == "alice"
    assert op["unverified_premises"] == ["allow_rebase_merge"]
    assert op["trust_root"]["snapshot_sha"] == ROOT.snapshot_sha
    world.sync()
    assert (world.target / envelope_path(WS_ID, "charter")).is_file()


def test_verified_premise_written_as_empty_list(world: World, stand: Stand) -> None:
    key, pr = _deliver(world, stand)
    approve(world, "charter")
    assert world.state.ops[key]["unverified_premises"] == []


def test_rejected_delivery_invalidates(world: World, stand: Stand) -> None:
    key, pr = _deliver(world, stand)
    stand.verdict = Rejected("d3_merger", "мержер вне allowlist")
    with pytest.raises(RuntimeError, match="d3_merger"):
        approve(world, "charter")
    assert world.state.ops[key]["reason"].startswith("d3_merger")


@pytest.mark.parametrize("verdict", [Unavailable("http: 502"), Ambiguous()])
def test_undecided_delivery_keeps_request_alive(world: World, stand: Stand, verdict) -> None:
    key, pr = _deliver(world, stand)
    stand.verdict = verdict
    with pytest.raises(RuntimeError) as err:
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_STARTED
    if isinstance(verdict, Ambiguous):
        assert "--repropose" in str(err.value)
    stand.verdict = None
    approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_COMPLETED


def test_legacy_candidate_merged_after_review_activation_refused(world: World,
                                                                 stand: Stand) -> None:
    """Приёмка №24: candidate без манифеста после активации review — admission, через
    настоящий диспетчер шага."""
    stand.mode = act_trust.PRE_MIGRATION
    key, op, pr = _candidate(world)  # заявка без маркера
    merge_pr(world, pr)
    stand.mode = "review"
    stand.verdict = Rejected("admission", "ACT_MODE=review: заявка без маркера")
    with pytest.raises(RuntimeError, match="admission"):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED


def test_frontmatter_noop_refused_when_mode_unavailable(world: World, stand: Stand) -> None:
    """d1 (D1-1): узел честно одобрен по frontmatter, режим акта не установлен — отказ сразу
    после `mode_probe`, до любого no-op (успеха без актуального допуска нет)."""
    stand.mode = act_trust.PRE_MIGRATION
    drive_to_approved(world, "charter")
    stand.mode_unset = "нет привязки корня доверия"
    with pytest.raises(RuntimeError, match="режим акта продукта .узел charter.: нет привязки"):
        approve(world, "charter")


def test_frontmatter_noop_in_review_needs_status(world: World, stand: Stand,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """d1: продукт в `review`, узел без события `review/v1` честно одобрен по frontmatter —
    no-op только при актуальном статусе (provenance старого акта, §9.1 (а) п. 3); иначе —
    новая заявка, не no-op."""
    stand.mode = act_trust.PRE_MIGRATION
    drive_to_approved(world, "charter")
    stand.mode = "review"
    monkeypatch.setattr(act_status.Statuses, "of", lambda self, node: act_status.APPROVED)
    noop = approve(world, "charter")
    assert noop.message == "charter: честно одобрен — одобрять нечего (no-op)"
    monkeypatch.setattr(act_status.Statuses, "of",
                        lambda self, node: "not_approved:legacy_provenance")
    outcome = approve(world, "charter")
    assert outcome.request is not None and outcome.request in world.state.ops


def test_review_noop_needs_current_status(world: World, stand: Stand,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """d1: no-op узла `review/v1` — только по актуальному `Statuses.of`; остановка,
    доставленная после одобрения, даёт новую заявку, а не no-op."""
    monkeypatch.setattr(act_status.Statuses, "is_review", lambda self, node: node == "charter")
    monkeypatch.setattr(act_status.Statuses, "of", lambda self, node: act_status.APPROVED)
    noop = approve(world, "charter")
    assert noop.message == "charter: честно одобрен (review/v1) — no-op"
    monkeypatch.setattr(act_status.Statuses, "of", lambda self, node: "not_approved:stopped:g1")
    outcome = approve(world, "charter")
    assert outcome.request is not None and outcome.request in world.state.ops


def test_merged_unmarked_with_unset_mode_stays_alive(world: World, stand: Stand) -> None:
    """B1-5: смёржен без маркера, режим продукта не установлен (откат review → merge) —
    заявка жива, старый код не продолжает."""
    stand.mode = act_trust.PRE_MIGRATION
    key, op, pr = _candidate(world)
    merge_pr(world, pr)
    stand.mode_unset = "ACT_MODE: обратное переключение review → merge"
    with pytest.raises(RuntimeError, match="режим акта"):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_STARTED


def test_act_removed_from_ledger_with_manifest_in_head(world: World, stand: Stand) -> None:
    """Приёмка №13 / P1-2: зеркало `act` удалено, манифест в голове — отказ."""
    key, pr = _deliver(world, stand)
    world.state.ops[key].pop("act")
    with pytest.raises(RuntimeError, match="зеркало act в run.json расходится"):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED


def _rewrite_manifest(world: World, op: dict, key: str, change) -> None:
    """Подменить манифест в голове candidate (ветка на origin) — до approve и мержа."""
    branch = op["branch"]
    _git(world.human, "fetch", "-q", "origin")
    _git(world.human, "switch", "-qC", branch, f"origin/{branch}")
    path = world.human / manifest_path(WS_ID, key)
    path.write_text(render_manifest(change(parse_manifest(path.read_text()))))
    _git(world.human, "commit", "-qam", "tamper manifest")
    _git(world.human, "push", "-q", "origin", branch)


def test_unreadable_manifest_in_head_invalidates(world: World, stand: Stand) -> None:
    """B3-2: файл манифеста в голове есть, но не разбирается — заявка invalidated
    (`_manifest`), а не «без маркера»."""
    key, op, pr = _candidate(world)
    branch = op["branch"]
    _git(world.human, "fetch", "-q", "origin")
    _git(world.human, "switch", "-qC", branch, f"origin/{branch}")
    (world.human / manifest_path(WS_ID, key)).write_text("schema: [битый\n")
    _git(world.human, "commit", "-qam", "broken manifest")
    _git(world.human, "push", "-q", "origin", branch)
    stand.approve_head(pr)
    with pytest.raises(RuntimeError, match="манифест в одобренном коммите не читается"):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED


BINDING_TWINS = {
    # компонент → (подмена манифеста, точная причина `_binding_refusal`)
    "product": (lambda r: replace(r, product="owner/other"), "манифест: product owner/other"),
    "ws_id": (lambda r: replace(r, ws_id="ws-other"), "манифест: ws_id ws-other"),
    "node_path": (lambda r: replace(r, nodes={n: NodeEntry("elsewhere/x.md", e.content_hash,
                                                           e.generation)
                                              for n, e in r.nodes.items()}),
                  "манифест: путь узла"),
    "upstream_pins": (lambda r: replace(r, upstream_pins={n: {"charter": "f" * 40}
                                                          for n in r.upstream_pins}),
                      "upstream-пины"),
}


@pytest.mark.parametrize("component", sorted(BINDING_TWINS))
def test_foreign_identity_binding_invalidates(world: World, stand: Stand,
                                              component: str) -> None:
    """B1-1/B1-14/B3-3: компонент идентичности подменён в САМОМ манифесте головы при
    прочих верных (ключ, зеркала `act`/политики, путь манифеста) — отказ именно
    `_binding_refusal` с причиной этого компонента."""
    change, reason = BINDING_TWINS[component]
    key, op, pr = _candidate(world)
    _rewrite_manifest(world, op, key, change)
    if component == "upstream_pins":
        # зеркало леджера согласовано с подменённым манифестом (B3-7): единственное
        # расхождение — манифест против прямых входов головы, то есть `_binding_refusal`
        world.state.ops[key]["upstream_pins"] = {n: {"charter": "f" * 40} for n in op["nodes"]}
    stand.approve_head(pr)
    with pytest.raises(RuntimeError, match=reason):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED


def test_recorded_act_differs_from_decision_unresolved(world: World, stand: Stand) -> None:
    """B3-6 (`_record_act`): в леджере записан другой акт, чем решение сейчас — стоп,
    запись не переписывается и заявка не продвигается."""
    key, pr = _deliver(world, stand)
    world.forge.agent_merge_rc_seq = [3]  # finalize не смёржен: акт записан, заявка жива
    approve(world, "charter")
    world.state.ops[key]["review_id"] = "99"
    with pytest.raises(RuntimeError, match="записан акт 99/"):
        approve(world, "charter")
    assert world.state.ops[key]["review_id"] == "99"


def test_legacy_halt_is_unresolved(world: World, stand: Stand,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """b4 (`_legacy.verdict`): старый код не установил факт — заявка жива, причина его."""
    stand.mode = act_trust.PRE_MIGRATION
    key, op, pr = _candidate(world)
    merge_pr(world, pr)
    stand.mode = "merge"
    monkeypatch.setattr(an, "_legacy_outcome", lambda *a: an.LegacyHalt("политика недоступна"))
    with pytest.raises(RuntimeError, match="политика недоступна"):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_STARTED


def test_envelope_commit_not_made_is_refused(world: World, stand: Stand,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """b4 (`_finalize`): коммит конверта не состоялся (HEAD не разрешается) — отказ,
    `finalize_head_sha` не записывается."""
    key, pr = _deliver(world, stand)
    real = world.ops.rev_parse
    monkeypatch.setattr(world.ops, "rev_parse",
                        lambda target, ref: None if ref == "HEAD" else real(target, ref))
    with pytest.raises(RuntimeError, match="коммит конверта не состоялся"):
        approve(world, "charter")
    assert world.state.ops[key].get("finalize_head_sha") is None


def test_head_hashes_unreadable_node_unavailable(world: World) -> None:
    """b4 (`act_node._head_hashes`): байты узла в голове не читаются — `Unavailable`."""
    req = NodeRequest("approve-1-0-1", WS_ID, SLUG,
                      {"charter": NodeEntry("spec/00-charter.md", "h", 1)}, {"charter": {}},
                      PolicyPin("owner/approval-policy", "main", ACT, {}))

    class _Ops:
        def show_file(self, target, ref, path):
            return None

    got = act_node._head_hashes(world.state, _Ops(), req, "h" * 40)
    assert isinstance(got, Unavailable) and got.reason.endswith(f"в {'h' * 12} не читается")


def test_finalize_resume_reuses_envelope(world: World, stand: Stand) -> None:
    """Review Focus 5: конверт записан, finalize не смёржен — второго конверта нет."""
    key, pr = _deliver(world, stand)
    world.forge.agent_merge_rc_seq = [3]  # первый агентский мерж finalize отказал
    approve(world, "charter")
    head = world.state.ops[key]["finalize_head_sha"]
    approve(world, "charter")
    op = world.state.ops[key]
    assert op["finalize_head_sha"] == head and op["status"] == al.STATUS_COMPLETED


def test_crash_after_finalize_merge_before_complete(world: World, stand: Stand,
                                                    monkeypatch) -> None:
    key, pr = _deliver(world, stand)
    real = al.complete_request
    calls: list[int] = []

    def flaky(state, k):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("упали до complete")
        real(state, k)

    monkeypatch.setattr(al, "complete_request", flaky)
    with pytest.raises(RuntimeError, match="упали"):
        approve(world, "charter")
    approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_COMPLETED
    assert len([n for n, r in world.forge.prs.items() if r["branch"].endswith("-final")]) == 1


def test_stop_between_act_and_finalize_merge_not_reported_approved(
        world: World, stand: Stand, monkeypatch: pytest.MonkeyPatch) -> None:
    """Решение владельца 01.11 (гонка stop/finalize): остановка поколения доставлена между
    актом и мержем finalize — заявка завершена (конверт в base), но «одобрены» не
    объявляется: ответ называет фактическое состояние узла."""
    key, pr = _deliver(world, stand)
    monkeypatch.setattr(act_status.Statuses, "is_review", lambda self, node: True)
    monkeypatch.setattr(act_status.Statuses, "of", lambda self, node: "not_approved:stopped:g1")
    with pytest.raises(RuntimeError, match="узлы не одобрены — узел charter: not_approved:stopped:g1"):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_COMPLETED


def test_finalize_merged_outside_base_invalidates(world: World, stand: Stand) -> None:
    """B1-15: конверт верен, но merge-коммит finalize недостижим из базы — не «в base»."""
    key, pr = _deliver(world, stand)
    world.ops.is_ancestor = lambda target, sha, ref: False  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="не в базу"):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED


def _substitute(env: Envelope, field: str) -> object:
    """Другое значение того же типа для компонента конверта."""
    value = getattr(env, field)
    if isinstance(value, int):
        return value + 7
    if isinstance(value, str):
        return value + "-x"
    return {**dict(value), "x": "y"}


@pytest.mark.parametrize("field", [f.name for f in fields(Envelope)])
def test_finalize_delivered_envelope_component_substituted_invalidates(
        world: World, stand: Stand, monkeypatch: pytest.MonkeyPatch, field: str) -> None:
    """B2-3: merge-коммит finalize несёт конверт с подменённым компонентом — заявка
    не завершается (вход `_reconcile_finalize`)."""
    key, pr = _deliver(world, stand)
    real, path = world.ops.show_file, envelope_path(WS_ID, "charter")

    def tampered(target: str, ref: str, p: str) -> str | None:
        text = real(target, ref, p)
        fpr = world.state.ops[key].get("finalize_pr")
        merged = (world.forge.prs.get(fpr) or {}).get("mergeCommit") if fpr else None
        if p != path or text is None or not merged or ref != merged["oid"]:
            return text
        env = parse_envelope(text)
        return render_envelope(replace(env, **{field: _substitute(env, field)}))

    monkeypatch.setattr(world.ops, "show_file", tampered)
    with pytest.raises(RuntimeError, match="не тот конверт"):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED


@pytest.mark.parametrize("touched,content", [
    ([envelope_path(WS_ID, "charter"), "spec/00-charter.md"], "x"),  # лишний путь
    ([], "x"),  # конверта в коммите нет
    ([envelope_path("ws-other", "charter")], "x"),  # конверт другого воркстрима
    ([envelope_path(WS_ID, "charter")], "y"),  # не то содержимое
], ids=["extra_path", "missing", "ws_id", "bytes"])
def test_envelope_commit_defect_twins(world: World, touched: list[str], content: str) -> None:
    """B2-3: форма коммита конверта — ровно пути конвертов узлов заявки ЭТОГО
    воркстрима с ожидаемыми байтами (вход `envelope_commit_defect`)."""
    class _Ops:
        def commit_files(self, target, head):
            return touched

        def show_file(self, target, ref, path):
            return content

    assert act_node.envelope_commit_defect(world.state, _Ops(), {"charter": "x"},
                                           "h" * 40) is not None
    ok = act_node.envelope_commit_defect(
        world.state, type("_Ok", (), {"commit_files": lambda s, t, h: [
            envelope_path(WS_ID, "charter")], "show_file": lambda s, t, r, p: "x"})(),
        {"charter": "x"}, "h" * 40)
    assert ok is None


# --- аудит и переоткрытие ------------------------------------------------------------


def test_late_dismissal_audited_once(world: World, stand: Stand) -> None:
    """Приёмка №10: решение не меняется, событие аудита одно."""
    key, pr = _deliver(world, stand)
    approve(world, "charter")
    stand.dismissals = (v3.Dismissal("11", "alice", "2030-01-01T00:00:00Z", "APPROVED"),)
    assert act_node.audit_late_dismissals(world.state, world.ops, key) == 1
    assert act_node.audit_late_dismissals(world.state, world.ops, key) == 0
    assert al.audit_events(world.state, key)[0]["event"] == "review_dismissed_after_delivery"
    assert world.state.ops[key]["status"] == al.STATUS_COMPLETED


def test_reopen_writes_stop_of_current_generation(world: World, stand: Stand) -> None:
    key, pr = _deliver(world, stand)
    approve(world, "charter")
    msg = act_node.reopen(world.state, world.ops, an.bundle_dag.dag_for(None), "charter")
    assert msg is not None and "g1" in msg
    stop_prs = [r for r in world.forge.prs.values() if r["branch"].startswith("stop/")]
    assert len(stop_prs) == 1 and stop_prs[0]["state"] == "MERGED"
    again = act_node.reopen(world.state, world.ops, an.bundle_dag.dag_for(None), "charter")
    assert again is None  # действующее событие — уже остановка


@pytest.mark.parametrize("record", ["none", "other_node", "other_generation", "other_reason"])
def test_reopen_refuses_stop_pr_without_record(world: World, stand: Stand,
                                               record: str) -> None:
    """B1-15/B2-3/B3-4: на ветке stop-PR лежит ОТКРЫТЫЙ PR с подменой (файла нет /
    остановка другого узла / другого поколения / другие байты). Первое чтение истории
    видит действующую заявку; `reopen` находит этот PR, мержит его и только ПОСЛЕ
    мержа сверяет запись — подмена ловится проверкой записи, а не ранним выходом."""
    key, pr = _deliver(world, stand)
    approve(world, "charter")
    branch = f"stop/{WS_ID}-charter-g1"
    _git(world.human, "fetch", "-q", "origin")
    _git(world.human, "switch", "-qc", branch, "origin/master")
    files = {
        "none": {"unrelated.txt": "x\n"},
        "other_node": {stop_path(WS_ID, "scope", 1): render_stop(StopRecord("scope", 1, "r"))},
        "other_generation": {stop_path(WS_ID, "charter", 2):
                             render_stop(StopRecord("charter", 2, "r"))},
        "other_reason": {stop_path(WS_ID, "charter", 1):
                         render_stop(StopRecord("charter", 1, "не тот повод"))},
    }[record]
    for rel, text in files.items():
        (world.human / rel).parent.mkdir(parents=True, exist_ok=True)
        (world.human / rel).write_text(text)
    _git(world.human, "add", "-A")
    _git(world.human, "commit", "-qm", "not a stop")
    _git(world.human, "push", "-q", "origin", branch)
    stop_pr = world.ops.create_pr(str(world.target), SLUG, branch, "t", "b", "")
    assert world.forge.prs[stop_pr]["state"] == "OPEN"  # подмена — до мержа reopen-ом
    with pytest.raises(RuntimeError, match="записи остановки"):
        act_node.reopen(world.state, world.ops, an.bundle_dag.dag_for(None), "charter")
    assert world.forge.prs[stop_pr]["state"] == "MERGED"  # проверка — после мержа


def test_reopen_already_delivered_stop_is_idempotent(world: World, stand: Stand) -> None:
    """B3-4: остановка уже доставлена — повтор `--reopen` ничего не делает и нового
    stop-PR не создаёт (ранний выход по действующему событию — штатный)."""
    key, pr = _deliver(world, stand)
    approve(world, "charter")
    assert act_node.reopen(world.state, world.ops, an.bundle_dag.dag_for(None), "charter")
    before = len(world.forge.prs)
    assert act_node.reopen(world.state, world.ops, an.bundle_dag.dag_for(None),
                           "charter") is None
    assert len(world.forge.prs) == before


def test_reopen_new_event_between_read_and_stop_check(world: World, stand: Stand,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """B3-3: между первым чтением истории и проверкой остановки в базу пришло новое
    событие узла (заявка следующего поколения) — `reopen` не сообщает об остановке."""
    key, pr = _deliver(world, stand)
    approve(world, "charter")
    real = act_status.load_history
    calls: list[int] = []

    def racing(ops, state, base):
        history = real(ops, state, base)
        calls.append(1)
        if len(calls) < 2 or isinstance(history, Unavailable):
            return history
        current = next(e for e in reversed(history) if isinstance(e, DeliveredRequest))
        entry = current.request.nodes["charter"]
        newer = replace(current.request, request_id="approve-9-9-9",
                        nodes={"charter": replace(entry, generation=entry.generation + 1)})
        return (*history, DeliveredRequest("n" * 40, "h" * 40, newer))

    monkeypatch.setattr(act_status, "load_history", racing)
    with pytest.raises(RuntimeError, match="не эта остановка"):
        act_node.reopen(world.state, world.ops, an.bundle_dag.dag_for(None), "charter")


def test_reopen_refuses_stop_pr_outside_base(world: World, stand: Stand) -> None:
    """B2-3: запись остановки верна, но merge-коммит stop-PR недостижим из базы."""
    key, pr = _deliver(world, stand)
    approve(world, "charter")
    world.ops.is_ancestor = lambda target, sha, ref: False  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="не в базу"):
        act_node.reopen(world.state, world.ops, an.bundle_dag.dag_for(None), "charter")


def test_reopen_of_legacy_node_is_old_path(world: World, stand: Stand) -> None:
    assert act_node.reopen(world.state, world.ops, an.bundle_dag.dag_for(None), "charter") \
        is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group governance pytest tests/test_act_node.py -v`
Expected: FAIL — `ImportError: cannot import name 'act_node' from 'governance'`.

- [ ] **Step 3: Ledger** (`governance/approval_ledger.py`)

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from governance.act_decision import Accepted, DeliveryView
    from governance.act_trust import TrustRoot

#: Соседняя запись аудита заявки: терминальная запись не мутируется (§I4),
#: а события аудита приходят после завершения. Ключ не разбирается как
#: заявка (`requests` требует ровно три числа после `approve-`).
AUDIT_PREFIX = "audit-"
```

`start_request` — два новых именованных параметра (умолчание — заявка без маркера, поведение
прежнее):

```python
    act: str | None = None,
    generations: dict[str, int] | None = None,
```

и в записи после `"source_sha": source_sha,`:

```python
        # Режим заявки — зеркало манифеста в коммите candidate (§7); None —
        # заявка без маркера. Расхождение с манифестом — отказ (act_node).
        **({"act": act, "generations": dict(generations or {})} if act else {}),
```

`extend_request(..., generation: int | None = None)` — при `generation is not None` пишет и
`generations={**op["generations"], node_id: generation}`. Новые функции:

```python
def record_review_act(
    state: RunState,
    key: str,
    accepted: Accepted,
    delivery: DeliveryView,
    activation_commit: str,
    unverified: tuple[str, ...],
    trust_root: TrustRoot,
) -> None:
    """Акт review/v1 и доставка — одним write'ом (спека §7, `run.json`).

    Верхнеуровневые `merged_by`/`merged_at`/`merge_commit`/`authorization`
    обязательны: по ним `next_step` переводит заявку в FINALIZE.
    `unverified` пишется всегда, пустой — как `[]`.
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
        trust_root={
            "host": trust_root.host,
            "repository_id": trust_root.repository_id,
            "branch": trust_root.branch,
            "snapshot_sha": trust_root.snapshot_sha,
        },
    )


def audit_events(state: RunState, key: str) -> list[dict]:
    """События аудита заявки (поздний отзыв), в порядке записи."""
    return list((state.ops.get(AUDIT_PREFIX + key) or {}).get("events", []))


def record_audit(state: RunState, key: str, event: dict) -> bool:
    """Дописать событие аудита; повтор того же `review_id` — False (идемпотентно)."""
    events = audit_events(state, key)
    if any(e.get("review_id") == event.get("review_id") for e in events):
        return False
    state.ops[AUDIT_PREFIX + key] = {"status": "completed", "events": [*events, dict(event)]}
    save(state)
    return True
```

- [ ] **Step 4: Small public seams**

`governance/act_decision.py` — публичное имя для D1 открытого PR (Task 13, 15):

```python
def find_act(
    head_sha: str, effective: Mapping[str, ReviewView], policy: PolicySet
) -> ReviewView | None:
    """Действительный approve человека на `head_sha` (D1) — для живой заявки."""
    return _act(head_sha, effective, policy)
```

`governance/act_envelope.py`:

```python
def expected_envelope(current: DeliveredRequest, node: str, review_id: str) -> Envelope:
    """Конверт, который обязан лежать в базе для действующей заявки (finalize)."""
    return _expected(current, node, review_id)
```

`governance/ops.py` — протокол и `RealOps`:

```python
    def pr_body_fact(self, repo_slug: str, pr: int) -> Fact[str]: ...
```

```python
    def pr_body_fact(self, repo_slug: str, pr: int) -> Fact[str]:
        """Тело PR (зеркало манифеста `review/v1`): FOUND / UNAVAILABLE."""
        done = subprocess.run(
            ["gh", "pr", "view", str(pr), "-R", repo_slug, "--json", "body", "--jq", ".body"],
            capture_output=True, text=True, check=False,
        )
        if done.returncode != 0:
            return unavailable(f"тело PR #{pr}: {done.stderr.strip()[:200]}")
        return Fact(Outcome.FOUND, done.stdout.rstrip("\n"), "ok")
```

Двойник отказа `pr_body_fact` (ревизия b4: функция с проверкой — место суждения) —
`tests/test_act_ops_body.py`:

```python
"""Тело PR через `gh` (`RealOps.pr_body_fact`, Task 13; ревизия b4)."""

from __future__ import annotations

from governance import ops as ops_mod
from governance.facts import Outcome
from governance.ops import RealOps


class _Done:
    def __init__(self, rc: int, out: str = "", err: str = "") -> None:
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_pr_body_unreadable_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(ops_mod.subprocess, "run", lambda *a, **k: _Done(1, err="no network"))
    got = RealOps().pr_body_fact("owner/p", 7)
    assert got.outcome is Outcome.UNAVAILABLE and got.detail == "тело PR #7: no network"


def test_pr_body_read(monkeypatch) -> None:
    monkeypatch.setattr(ops_mod.subprocess, "run", lambda *a, **k: _Done(0, out="act: review/v1\n"))
    got = RealOps().pr_body_fact("owner/p", 7)
    assert got.outcome is Outcome.FOUND and got.value == "act: review/v1"
```

Изоляция тестов вне `act_*` (`tests/conftest.py`) — уже в Task 14 (ревизия b1, B1-8).

- [ ] **Step 5: Write `act_node`**

```python
"""Путь узла `review/v1`: общий вход, создание, finalize в конверт, остановка, аудит.

`approve_node` только ветвится сюда (§2: режим — из манифеста в одобренном
коммите; §10: admission до выбора читателя). Решение по доставке —
`act_context.delivery_verdict` поверх доверенного контекста; старый код
получает управление после `LegacyAccepted` (или когда маркера нет и PR не
смёржен). Неустановленный факт заявку не хоронит (`_unresolved`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from governance import act_context, act_facts, act_status, act_trust, bundle_dag, bundle_inputs
from governance import approval_facts as af
from governance import approval_ledger as al
from governance import approve_node as an
from governance.act_check import LegacyAccepted, live_policy_refusal
from governance.act_decision import Accepted, Rejected, find_act
from governance.act_envelope import (
    DeliveredRequest,
    DeliveredStop,
    StopRecord,
    current_event,
    envelope_path,
    expected_envelope,
    next_generation,
    render_envelope,
    render_stop,
    stop_path,
)
from governance.act_identity import RequestIdentity, manifest_refusal, paths_refusal
from governance.act_projection import ProjectionError, self_hash_v
from governance.act_request import (
    ACT_REVIEW_V1,
    NodeEntry,
    NodeRequest,
    PolicyPin,
    RequestError,
    manifest_path,
    mirror_mismatch,
    parse_manifest,
    render_manifest,
)
from governance.act_time import AMBIGUOUS, Ambiguous, Clock, Unavailable
from governance.approval_facts import Disposition
from governance.facts import Outcome
from governance.ops import Ops
from governance.run_state import RunState

HUMAN_APPROVAL_LABEL = "human-approval-required"
WAITING_HUMAN_APPROVAL = "waiting_human_approval"
WAITING_DELIVERY = "waiting_delivery"
DEPS_FACTORY: Callable[[RunState], act_status.Deps] = act_status.real_deps
Dag = tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True)
class Creation:
    """Режим новой заявки: `act=None` — без маркера; `review/v1` — с пином и историей."""

    act: str | None
    pin: PolicyPin | None
    history: tuple


# --- создание ------------------------------------------------------------------------


def creation(state: RunState, ops: Ops, deps: act_status.Deps,
             live: act_status.LivePolicy | Unavailable) -> Creation:
    """Режим создания по режиму продукта сейчас (`mode_probe`) и настройке (§10, Task 10).

    Неустановленный режим (нет привязки, удалённая запись, откат) — отказ
    создания, не заявка без маркера (ревизия b1, B1-5)."""
    settings = deps.settings
    assert settings is not None
    if isinstance(live, Unavailable):
        raise an._unresolved(f"режим акта продукта: {live.reason}")
    if live.mode != "review":
        if settings.draining:
            raise RuntimeError(_DRAINING)
        return Creation(None, None, ())
    if not settings.review_v1:
        raise RuntimeError(
            f"ACT_MODE[{state.repo_slug}] = review, а review_v1 в act.toml выключен — "
            "заявка не создаётся (§10): включите review_v1 либо дождитесь дефолта M4"
        )
    history = act_status.load_history(ops, state, an._base_ref(state))
    if isinstance(history, Unavailable):
        raise an._unresolved(f"история заявок узлов: {history.reason}")
    pin = PolicyPin(
        live.trust.source.repo,
        live.trust.source.branch,
        live.activation.merge_commit,
        dict(live.policy.files_sha256),
    )
    return Creation(ACT_REVIEW_V1, pin, history)


_DRAINING = (
    "act_migration = draining: заявки без маркера не создаются (§10 п. 1) — "
    "дождитесь активации ACT_MODE=review для продукта"
)


def generation_of(made: Creation, node: str) -> int:
    """Поколение узла в новой заявке — по полной истории (§9.1 п. 0a)."""
    return next_generation(made.history, node)


def request_of(state: RunState, dag: Dag, op: Mapping, key: str) -> NodeRequest:
    """Заявка `review/v1` из записи леджера (обратное `ledger_view`)."""
    pin = op["policy"]
    nodes = {
        n: NodeEntry(an._rel(state, an._filename(dag, n)), op["content_hashes"][n],
                     op["generations"][n])
        for n in op["nodes"]
    }
    return NodeRequest(
        key, state.ws_id, state.repo_slug, nodes,
        {n: dict(op["upstream_pins"][n]) for n in op["nodes"]},
        PolicyPin(pin["repo"], pin["branch"], pin["activation_commit"],
                  dict(pin["files_sha256"])),
    )


def manifest_file(state: RunState, dag: Dag, op: Mapping, key: str) -> tuple[str, str] | None:
    """Путь и текст манифеста для коммита candidate; `None` — заявка без маркера."""
    if op.get("act") != ACT_REVIEW_V1:
        return None
    return manifest_path(state.ws_id, key), render_manifest(request_of(state, dag, op, key))


def label_for(op: Mapping) -> str:
    """Метка candidate-PR: акт = approve (`review/v1`) либо мерж человеком."""
    return HUMAN_APPROVAL_LABEL if op.get("act") == ACT_REVIEW_V1 else an.HUMAN_MERGE_LABEL


# --- общий вход ----------------------------------------------------------------------


def _manifest(state: RunState, ops: Ops, head: str, key: str) -> NodeRequest | None:
    fact = ops.repo_file_fact(state.repo_slug, head, manifest_path(state.ws_id, key))
    if fact.outcome is Outcome.ABSENT:
        return None
    if fact.outcome is not Outcome.FOUND or fact.value is None:
        raise an._unresolved(f"манифест заявки {key} в {head[:12]} — {fact.detail}")
    try:
        return parse_manifest(fact.value)
    except RequestError as exc:
        _invalidate(state, key, f"манифест в одобренном коммите не читается: {exc}")


def _invalidate(state: RunState, key: str, reason: str) -> NoReturn:
    al.invalidate_request(state, key, reason)
    raise RuntimeError(
        f"{reason}. Заявка {key} — invalidated; продолжение — новая заявка "
        f"(`--repropose {key}`)"
    )


def _binding_refusal(state: RunState, ops: Ops, dag: Dag, op: Mapping, req: NodeRequest,
                     head: str, key: str) -> str | None:
    ident = RequestIdentity(state.repo_slug, state.ws_id, key)
    expected = {bundle_dag.node_id(f): an._rel(state, f) for f, _ in dag}
    refusal = manifest_refusal(req, ident) or paths_refusal(req, expected)
    if refusal is not None:
        return refusal
    ops.fetch_branch(state.target_dir, op["branch"])
    for node in req.nodes:
        fact = bundle_inputs.direct_blobs(state, ops, dag, node, head)
        if fact.outcome is not Outcome.FOUND or fact.value is None:
            if fact.outcome is Outcome.FORBIDDEN:
                return f"прямые входы {node}: {fact.detail}"
            raise an._unresolved(f"прямые входы {node} в {head[:12]} — {fact.detail}")
        if dict(req.upstream_pins[node]) != fact.value:
            return f"манифест: upstream-пины {node} ≠ прямым входам в {head[:12]}"
    return None


def _check_marked(state, ops, dag, op, key, pr: int, head: str) -> NodeRequest | None:
    req = _manifest(state, ops, head, key)
    if (op.get("act") == ACT_REVIEW_V1) != (req is not None):
        _invalidate(state, key, "зеркало act в run.json расходится с манифестом в голове PR")
    if req is None:
        return None
    body = ops.pr_body_fact(state.repo_slug, pr)
    if body.outcome is not Outcome.FOUND or body.value is None:
        raise an._unresolved(f"тело candidate-PR #{pr} — {body.detail}")
    for refusal in (
        mirror_mismatch(req, key, op, body.value),
        _binding_refusal(state, ops, dag, op, req, head, key),
    ):
        if refusal is not None:
            _invalidate(state, key, refusal)
    return req


def route(state: RunState, ops: Ops, dag: Dag, op: dict, key: str,
          step: al.Step) -> an.ApprovalOutcome | None:
    """Общий вход до выбора шага; `None` — старый код продолжает (см. Interfaces)."""
    pr = op.get("candidate_pr")
    if pr is None:
        return None
    facts = an._pr_facts(state, ops, pr)
    head = facts.get("headRefOid")
    if not isinstance(head, str) or len(head) != 40:
        raise an._unresolved(f"голова candidate-PR #{pr} не названа")
    where = an._disposition(facts, pr)
    req = _check_marked(state, ops, dag, op, key, pr, head)
    if where is Disposition.OPEN:
        return None if req is None else _open(state, ops, op, key, pr, head, req)
    if where is Disposition.CLOSED_UNMERGED:
        return None
    deps = DEPS_FACTORY(state)
    if req is None:
        live = act_status.mode_probe(state, deps)
        if isinstance(live, Unavailable):
            raise an._unresolved(f"режим акта продукта: {live.reason}")
        if live.mode == act_trust.PRE_MIGRATION:
            # записи ACT_MODE[P] не было никогда: admission на T — legacy (§10),
            # тот же исход, что дал бы полный перебор; старый код продолжает
            return None
    return _merged(state, ops, dag, op, key, pr, head, req, facts, deps)


def _open(state, ops, op, key, pr: int, head: str, req: NodeRequest) -> an.ApprovalOutcome:
    deps = DEPS_FACTORY(state)
    live = act_status.mode_probe(state, deps)
    if isinstance(live, Unavailable):
        raise an._unresolved(f"режим акта продукта: {live.reason}")
    if live.mode != "review":
        raise an._unresolved(f"заявка review/v1, а режим продукта сейчас — {live.mode}")
    refusal = live_policy_refusal(req.policy, live.activation.merge_commit)
    if refusal is not None:
        _invalidate(state, key, refusal)
    facts = act_facts.open_review(deps.gh, state.repo_slug, pr)
    if isinstance(facts, Unavailable):
        raise an._unresolved(f"ревью candidate-PR #{pr}: {facts.reason}")
    if facts.head_sha != head:
        raise an._unresolved(f"голова PR #{pr} сдвинулась между чтениями")
    effective = act_facts.effective(facts, Clock(deps.now()))
    if isinstance(effective, Unavailable):
        raise an._unresolved(f"эффективные ревью PR #{pr}: {effective.reason}")
    act = find_act(head, effective, live.policy)
    if act is None:
        return an.ApprovalOutcome(
            f"{WAITING_HUMAN_APPROVAL}: candidate-PR #{pr} ждёт approve человека из "
            f"human_approvers по head {head[:12]}. Заявка {key} жива",
            request=key,
        )
    code = ops.merge(state.repo, pr, head, None)
    if code != 0:
        hint = an._MERGE_CODE_HINTS.get(code, "мерж не выполнен")
        return an.ApprovalOutcome(
            f"approve {act.login} есть, агентская доставка отказала кодом {code}: {hint}. "
            f"Заявка {key} жива",
            request=key,
        )
    return an.ApprovalOutcome(
        f"{WAITING_DELIVERY}: candidate-PR #{pr} доставлен агентом после approve "
        f"{act.login}; повторите вызов — решение по доставке и конверт",
        request=key,
    )


def _legacy(state, ops, dag, op, key, facts) -> Callable[[], object]:
    def verdict() -> object:
        got = an._legacy_outcome(state, ops, dag, op, key, facts)
        return Unavailable(got.text) if isinstance(got, an.LegacyHalt) else got

    return verdict


def _head_hashes(state: RunState, ops: Ops, req: NodeRequest, head: str):
    hashes: dict[str, str] = {}
    for entry in req.nodes.values():
        text = ops.show_file(state.target_dir, head, entry.path)
        if text is None:
            return Unavailable(f"{entry.path} в {head[:12]} не читается")
        try:
            hashes[entry.path] = self_hash_v(text, ACT_REVIEW_V1)
        except ProjectionError as exc:
            return Unavailable(str(exc))
    return hashes


def _merged(state, ops, dag, op, key, pr, head, req, facts, deps) -> an.ApprovalOutcome | None:
    decider = act_status.Decider(state, ops, deps, dag)
    trust = decider.trust()
    if isinstance(trust, Unavailable):
        raise an._unresolved(f"доверенный контекст: {trust.reason}")
    outcome, delivery = act_context.delivery_verdict(
        deps.readers, trust, decider.journal,
        product=state.repo_slug, pr=pr,
        paths=sorted(e.path for e in req.nodes.values()) if req else [],
        request=req, request_act=ACT_REVIEW_V1 if req else None, head_sha=head,
        facts_transport=deps.gh,
        self_hashes=lambda: _head_hashes(state, ops, req, head),
        legacy=_legacy(state, ops, dag, op, key, facts),
    )
    if isinstance(outcome, LegacyAccepted):
        return None
    if isinstance(outcome, Rejected):
        _invalidate(state, key, f"{outcome.code}: {outcome.detail}")
    if isinstance(outcome, Ambiguous):
        raise an._unresolved(
            f"{AMBIGUOUS}: исход зависит от порядка событий секунды мержа — факты не "
            f"изменятся; штатный выход — `--repropose {key}`"
        )
    if isinstance(outcome, Unavailable):
        tail = (" — факты PR не изменятся; выход — `--repropose`"
                if outcome.reason.startswith("actor_unresolved") else "")
        raise an._unresolved(f"{outcome.reason}{tail}")
    if not isinstance(outcome, Accepted) or req is None or delivery is None:
        raise an._unresolved(f"доставка candidate-PR #{pr} не установлена")
    _record_act(state, key, req, outcome, delivery, trust)
    current = DeliveredRequest(outcome.merge_commit, head, req)
    expected = {n: render_envelope(expected_envelope(current, n, outcome.review_id))
                for n in req.nodes}
    return _finalize(state, ops, dag, key, expected)


def _record_act(state, key, req, accepted, delivery, trust) -> None:
    op = state.ops[key]
    if op.get("review_id") is None:
        al.record_review_act(state, key, accepted, delivery, req.policy.activation_commit,
                             trust.unverified, trust.root)
        return
    if (op["review_id"], op["merge_commit"]) != (accepted.review_id, accepted.merge_commit):
        raise an._unresolved(
            f"заявка {key}: записан акт {op['review_id']}/{op['merge_commit']}, решение "
            f"сейчас — {accepted.review_id}/{accepted.merge_commit}"
        )


# --- finalize ------------------------------------------------------------------------


def envelope_commit_defect(state: RunState, ops: Ops, expected: Mapping[str, str],
                           head: str) -> str | None:
    """Коммит конверта — ровно пути конвертов узлов заявки с ожидаемым содержимым."""
    touched = ops.commit_files(state.target_dir, head)
    if touched is None:
        return f"коммит конверта {head[:12]} не читается"
    want = {envelope_path(state.ws_id, n): text for n, text in expected.items()}
    if set(touched) != set(want):
        return f"коммит конверта трогает {sorted(touched)}, ожидались ровно {sorted(want)}"
    for path, text in sorted(want.items()):
        if ops.show_file(state.target_dir, head, path) != text:
            return f"{path}: содержимое не равно ожидаемому конверту"
    return None


def _finalize(state, ops, dag, key, expected: Mapping[str, str]) -> an.ApprovalOutcome:
    op = state.ops[key]
    if op.get("finalize_pr") is not None:
        return _reconcile_finalize(state, ops, dag, key, expected)
    branch, head = op["finalize_branch"], op.get("finalize_head_sha")
    paths = sorted(envelope_path(state.ws_id, n) for n in expected)
    if head is None:
        ops.switch_to(state.target_dir, branch, an._base_ref(state))
        for node, text in expected.items():
            path = Path(state.target_dir) / envelope_path(state.ws_id, node)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        ops.commit_paths(state.target_dir, paths,
                         f"spec: {state.ws_id} — конверт review/v1 узлов {', '.join(expected)}")
        head = ops.rev_parse(state.target_dir, "HEAD")
        if head is None:
            raise RuntimeError("коммит конверта не состоялся: HEAD не разрешается")
        al.record_finalize_head_sha(state, key, head)
    else:
        an._switch_to_request_branch(state, ops, branch, head)
    ops.push_branch(state.target_dir, branch)
    who, why = an.finalize_merge_policy(state.target_dir)
    pr = an._adopt_or_create_pr(
        state, ops, branch, head,
        f"spec: {state.ws_id} — конверт review/v1 узлов {', '.join(expected)}",
        f"Конверт утверждения узлов {', '.join(expected)} (спека §9.1 (а)).\n\n"
        f"Акт — approve {op['approved_by']} по head candidate-PR #{op['candidate_pr']} "
        f"(review {op['review_id']}); этот PR полномочий не создаёт.",
        an.HUMAN_MERGE_LABEL if who == "human" else "",
    )
    al.record_finalize_pr(state, key, pr)
    if who == "human":
        return an.ApprovalOutcome(
            f"конверт вынесен PR #{pr}; мержит человек ({why}). Заявка {key} жива",
            request=key,
        )
    return _agent_merge(state, ops, dag, key, expected, pr, head)


def _agent_merge(state, ops, dag, key, expected, pr: int, head: str) -> an.ApprovalOutcome:
    reason = an._try_agent_merge(
        state, ops, dag, state.ops[key], list(expected), pr, head,
        form=lambda: envelope_commit_defect(state, ops, expected, head),
    )
    if reason is None:
        return _reconcile_finalize(state, ops, dag, key, expected)
    return an.ApprovalOutcome(
        f"конверт вынесен PR #{pr}; {reason.reason}. Заявка {key} жива", request=key
    )


def _reconcile_finalize(state, ops, dag, key, expected) -> an.ApprovalOutcome:
    op = state.ops[key]
    pr = op["finalize_pr"]
    facts = an._pr_facts(state, ops, pr)
    where = an._disposition(facts, pr)
    if where is Disposition.OPEN:
        head = op.get("finalize_head_sha")
        if head is not None and an.finalize_merge_policy(state.target_dir)[0] == "agent":
            return _agent_merge(state, ops, dag, key, expected, pr, str(head))
        return an.ApprovalOutcome(f"PR конверта #{pr} открыт — ждём мержа", request=key)
    if where is Disposition.CLOSED_UNMERGED:
        _invalidate(state, key, f"PR конверта #{pr} закрыт без мержа")
    event = af.merge_event(facts)
    if event.outcome is not Outcome.FOUND or event.value is None:
        raise an._unresolved(f"merge-коммит PR конверта #{pr} — {event.detail}")
    commit = event.value.commit
    _require_in_base(state, ops, key, commit, f"PR конверта #{pr}")
    for node, text in expected.items():
        got = ops.show_file(state.target_dir, commit, envelope_path(state.ws_id, node))
        if got is None:
            raise an._unresolved(f"конверт {node} на {commit[:12]} не читается")
        if got != text:
            _invalidate(state, key, f"PR конверта #{pr} доставил не тот конверт узла {node}")
    an._ensure_finalize_identity(state, ops, key, op)
    al.complete_request(state, key)
    # Решение владельца 01.11 (гонка stop/finalize): заявка завершена — конверт в base, — но
    # «одобрены» объявляется только при актуальном допуске; остановка, доставленная между
    # актом и мержем finalize, — фактическое состояние вместо успеха.
    refusal = act_status.noop_refusal(state, ops, dag, list(expected))
    if refusal is not None:
        raise RuntimeError(
            f"конверт review/v1 в base (PR #{pr}), заявка {key} завершена, но узлы не "
            f"одобрены — {refusal}"
        )
    return an.ApprovalOutcome(
        f"конверт review/v1 в base (PR #{pr}): узлы {', '.join(expected)} одобрены, "
        f"заявка {key} завершена",
        request=key,
    )


def _in_base(state: RunState, ops: Ops, commit: str) -> bool | None:
    """merge-коммит достижим из свежей базы (B1-15); `None` — не установлено."""
    ops.checkout_and_pull(state.target_dir, an._base_ref(state))
    return ops.is_ancestor(state.target_dir, commit, an._base_ref(state))


def _require_in_base(state: RunState, ops: Ops, key: str, commit: str, what: str) -> None:
    reached = _in_base(state, ops, commit)
    if reached is None:
        raise an._unresolved(f"{what}: достижимость {commit[:12]} из базы")
    if not reached:
        _invalidate(state, key, f"{what} смёржен не в базу {an._base_ref(state)}")


# --- переоткрытие и аудит ------------------------------------------------------------

STOP_REASON = "переоткрытие узла {node}"


def reopen(state: RunState, ops: Ops, dag: Dag, node: str) -> str | None:
    """Остановка действующего одобрения `review/v1` до новой волны (§8, Q1)."""
    base = an._base_ref(state)
    ops.checkout_and_pull(state.target_dir, base)
    history = act_status.load_history(ops, state, base)
    if isinstance(history, Unavailable):
        raise an._unresolved(f"история заявок узлов: {history.reason}")
    current = current_event(history, node)
    if not isinstance(current, DeliveredRequest):
        return None
    gen = current.request.nodes[node].generation
    branch = f"stop/{state.ws_id}-{node}-g{gen}"
    path = stop_path(state.ws_id, node, gen)
    record = render_stop(StopRecord(node, gen, STOP_REASON.format(node=node)))
    found = af.search_pr(ops, state.repo_slug, branch, any_state=True)
    if found.outcome is Outcome.UNAVAILABLE:
        raise an._unresolved(f"поиск stop-PR на ветке {branch} — {found.detail}")
    if found.outcome is Outcome.ABSENT:
        ops.switch_to(state.target_dir, branch, base)
        file = Path(state.target_dir) / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(record, encoding="utf-8")
        ops.commit_paths(state.target_dir, [path], f"spec: {state.ws_id} — остановка {node} g{gen}")
        ops.push_branch(state.target_dir, branch)
        head = ops.rev_parse(state.target_dir, "HEAD") or ""
        pr = ops.create_pr(state.target_dir, state.repo_slug, branch,
                           f"spec: {state.ws_id} — остановка одобрения {node} g{gen}",
                           f"Запись остановки (спека §8, Q1): `{path}`. Только отзывает.", "")
    else:
        pr = int(found.value or 0)
        head = an.pr_head(state, ops, pr) or ""
    facts = an._pr_facts(state, ops, pr)
    if an._disposition(facts, pr) is Disposition.OPEN:
        missing = an._missing_approving_review(state, ops, pr)
        if missing is not None:
            raise RuntimeError(f"{missing}; затем повторите --reopen {node}")
        code = ops.merge(state.repo, pr, head, None)
        if code != 0:
            raise RuntimeError(
                f"stop-PR #{pr}: агентский мерж отказал кодом {code} — остановка не "
                f"доставлена, узел {node} остаётся одобренным; повторите --reopen"
            )
        facts = an._pr_facts(state, ops, pr)
    if an._disposition(facts, pr) is not Disposition.MERGED:
        raise RuntimeError(f"stop-PR #{pr} не смёржен — узел {node} остаётся одобренным")
    event = af.merge_event(facts)
    if event.outcome is not Outcome.FOUND or event.value is None:
        raise an._unresolved(f"merge-коммит stop-PR #{pr} — {event.detail}")
    # доставка остановки доказывается базой, а не состоянием PR (B1-15)
    reached = _in_base(state, ops, event.value.commit)
    if reached is None:
        raise an._unresolved(f"stop-PR #{pr}: достижимость из базы")
    if not reached:
        raise RuntimeError(f"stop-PR #{pr} смёржен не в базу — узел {node} остаётся одобренным")
    if ops.show_file(state.target_dir, event.value.commit, path) != record:
        raise RuntimeError(
            f"stop-PR #{pr} не несёт записи остановки {path} узла {node} поколения {gen} — "
            "узел остаётся одобренным; разберите PR вручную"
        )
    # положительное суждение «остановлен» — тем же входом, что статус (B2-3): действующее
    # событие узла в истории базы — именно эта остановка этим merge-коммитом
    after = act_status.load_history(ops, state, base)
    if isinstance(after, Unavailable):
        raise an._unresolved(f"история после stop-PR #{pr}: {after.reason}")
    stopped = DeliveredStop(event.value.commit,
                            StopRecord(node, gen, STOP_REASON.format(node=node)))
    if current_event(after, node) != stopped:
        raise RuntimeError(
            f"stop-PR #{pr}: действующее событие узла {node} в базе — не эта остановка "
            f"g{gen}; разберите вручную"
        )
    return f"остановка {path} (g{gen}) доставлена PR #{pr}"


def audit_late_dismissals(state: RunState, ops: Ops, key: str,
                          deps: act_status.Deps | None = None) -> int:
    """Отзывы после доставки завершённой заявки `review/v1` → события аудита (§8)."""
    op = state.ops[key]
    if op.get("act") != ACT_REVIEW_V1 or op.get("status") != al.STATUS_COMPLETED:
        return 0
    deps = deps or DEPS_FACTORY(state)
    facts = act_facts.open_review(deps.gh, state.repo_slug, op["candidate_pr"])
    if isinstance(facts, Unavailable):
        return 0  # аудит не прочитан; решение от него не зависит
    merged_at = op["merged_at"]
    written = 0
    for d in facts.dismissals:
        if d.created_at > merged_at and al.record_audit(state, key, {
            "event": "review_dismissed_after_delivery",
            "review_id": d.review_id,
            "at": d.created_at,
        }):
            written += 1
    return written
```

(Метки — канон `YYYY-MM-DDTHH:MM:SSZ`: строковое сравнение совпадает с временным, как у steward.)

- [ ] **Step 6: Branch `approve_node`**

`_advance` — фрагмент выше. `_try_agent_merge` — именованный `form`:

```python
def _try_agent_merge(state, ops, dag, op, nodes, pr, head, *,
                     form: Callable[[], str | None] | None = None) -> MergeRefusal | None:
    ...
    defect = form() if form is not None else _envelope_form_defect(state, ops, dag, op, nodes, head)
```

`_propose` — сразу после `upstream_blobs = …` (`:375`), до блока долга (статус узла в продукте
`review` — не frontmatter):

```python
    from governance import act_node, act_status

    deps = act_node.DEPS_FACTORY(state)
    live = act_status.mode_probe(state, deps)  # один раз: и статус, и режим создания
    if isinstance(live, Unavailable):  # d1 (D1-1): до любого no-op — режим неизвестен
        raise _unresolved(f"режим акта продукта (узел {node_id}): {live.reason}")
    statuses = (act_status.statuses_at_base(state, ops, dag, live, deps)
                if live.mode == "review" else None)
```

после `_require_no_reopened_pr(...)`, до `self_hash`:

```python
    made = act_node.creation(state, ops, deps, live)  # Unavailable — отказ до записи
```

и замена блока `debt = na.node_debt(...)` / no-op / пины (`:376–386`):

```python
    if statuses is not None and statuses.is_review(node_id):
        verdict = statuses.of(node_id)
        if verdict == act_status.APPROVED:
            return ApprovalOutcome(f"{node_id}: честно одобрен (review/v1) — no-op")
        if verdict.startswith("unavailable:"):
            raise _unresolved(f"статус узла {node_id}: {verdict}")
        debt = _review_debt(node_id, text, verdict)
    else:
        debt = na.node_debt(node_id, text, upstream_blobs)
        if debt is None and statuses is not None:
            # d1: продукт в `review` — frontmatter без долга лишь предпосылка (§9.1 (а) п. 3);
            # no-op только при актуальном статусе узла (provenance старого акта)
            verdict = statuses.of(node_id)
            if verdict.startswith("unavailable:"):
                raise _unresolved(f"статус узла {node_id} без события review/v1: {verdict}")
            if verdict != act_status.APPROVED:
                debt = _review_debt(node_id, text, verdict)
        if debt is None:
            return ApprovalOutcome(f"{node_id}: честно одобрен — одобрять нечего (no-op)")
        if debt.kind in (na.DEBT_PINS, na.DEBT_UNKNOWN_STATUS):
            raise RuntimeError(debt.render())
```

(Порядок «гварды до первой записи» сохраняется: `mode_probe`, `statuses_at_base` и `creation`
только читают. Неустановленный режим отказывает **сразу после `mode_probe`**, до любого no-op
(ревью d1, D1-1): прежняя редакция оставляла `statuses=None` и отдавала no-op по frontmatter.
Каждый успешный ранний выход `_propose` для продукта в `review` идёт от `Statuses.of` — перечень
в разделе ревизии «Решения владельца 01.11».)
Присоединение и запись:

```python
    if joined is not None and joined[1].get("act") != made.act:
        raise RuntimeError(
            f"живая заявка {al.request_key(*joined[0])} шага — акт "
            f"{joined[1].get('act') or 'без маркера'}, новая — {made.act or 'без маркера'}: "
            "в одну заявку они не соединяются; дождитесь её терминализации"
        )
    if made.act is None:
        snapshot = _pinned_policy(state, ops, joined, node_id)
        policy_record = snapshot.as_record()
    else:
        policy_record = made.pin.as_record()
        if joined is not None and joined[1]["policy"] != policy_record:
            raise RuntimeError(
                f"живая заявка {al.request_key(*joined[0])} закрепила другую активацию "
                "политики — узел не дописан; дождитесь её policy_changed / --repropose"
            )
    generation = act_node.generation_of(made, node_id) if made.act else None
    ...
        al.extend_request(state, key, node_id, self_hash, upstream_blobs, generation=generation)
    ...
        key = al.start_request(
            ..., policy=policy_record, act=made.act,
            generations={node_id: generation} if made.act else None,
        )
```

`propose_from_source` — тот же приём: `live = act_status.mode_probe(state, deps)`,
`made = act_node.creation(state, ops, deps, live)` до первой записи;
`generations = {n: act_node.generation_of(made, n) for n in node_ids}` для `review/v1`;
`start_request(..., act=made.act, generations={first: generations[first]})`,
`extend_request(..., generation=generations[node])`.

`_sync_branch_to_snapshot` — перед `forced = _sync_source_layer(...)`:

```python
    from governance import act_node

    manifest = act_node.manifest_file(state, dag, op, key)
    if manifest is not None:
        rel, text = manifest
        path = Path(state.target_dir) / rel
        if not (path.is_file() and path.read_text(encoding="utf-8") == text):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            changed.append(rel)
```

`_publish_candidate` — `_adopt_or_create_pr(..., label=act_node.label_for(op))`; итоговое
сообщение для `review/v1`: «вынесены на одобрение, candidate-PR #N. Одобрение — approve человека
из human_approvers по head этого PR; после approve повторите вызов — агент доставит PR».
`_candidate_body` для `op["act"] == "review/v1"`:

```python
    if op.get("act") == "review/v1":
        policy = op["policy"]
        return "\n".join([
            f"Предложение об одобрении узлов бандла {state.ws_id} (§I12, акт review/v1).",
            "",
            "**Акт одобрения — approve человека из `human_approvers` по head этого PR.** "
            "Мерж выполнит агент после акта; мерж человеком подписи не создаёт.",
            "",
            f"Узлы: {', '.join(op['nodes'])}.",
            f"run-id: {state.run_id}",
            "act: review/v1",
            f"policy-activation: {policy['activation_commit']}",
            *(["", f"Повод: {debt.reason}."] if debt is not None else []),
        ])
```

`_policy_ref(op)` — для `review/v1`: `f"политики {op['policy']['repo']}@{op['policy']['activation_commit']} (review/v1)"`.

`_reconcile_candidate` уже стоит поверх `_legacy_outcome` — выделение перенесено в Task 14
(Step 3a, ревизия b3: `LegacyProbe` обязан исполнять тот же полный старый контракт); здесь
`_legacy_outcome` только потребляется (`act_node._legacy`).

- [ ] **Step 7: Runner hooks**

`governance/runner.py`, `reopen` — после проверки грязного дерева, до `state.wave = …`:

```python
    from governance import act_node

    stopped = act_node.reopen(state, ops, dag, node)
    if stopped is not None:
        print(f"reopen: {stopped}")
```

`resume` — в начале, после загрузки состояния:

```python
    from governance import act_node

    for nums, op in al.requests(state):
        if op.get("act") == "review/v1" and op.get("status") == al.STATUS_COMPLETED:
            act_node.audit_late_dismissals(state, ops, al.request_key(*nums))
```

`_print_status` — после списка `ops:` (события уже в `state.ops["audit-…"]`, отдельной печати
не требуется: ключ печатается общим циклом; строка `audit-approve-…` с `events=[…]` и есть
показ события).

Тест раннера (`tests/test_governance_runner.py`): `test_reopen_stops_review_node_before_wave` —
`act_node.reopen` подменён на поднимающий `RuntimeError` → `runner.reopen` отказывает, `state.wave`
и op'ы волн не тронуты.

- [ ] **Step 8: Единое состояние истории узла — п. 9, вариант (Б) (решение владельца 01.11)**

`governance/act_envelope.py` — `current_event`, `generation_refusal`, `next_generation` (~196–223)
заменяются одним вычислением; `generation_refusal` удаляется (потребителей вне `act_envelope`,
`act_status` и `tests/test_act_envelope.py` нет — сверено по дереву master `a16e305`):

```python
@dataclass(frozen=True)
class Ignored:
    """Запись узла, не ставшая его событием (п. 9 заметок части A, вариант (Б))."""

    event: Event
    reason: str


@dataclass(frozen=True)
class NodeHistory:
    """Действующее состояние истории узла — единственное вычисление (решение владельца
    01.11): `current` — действующее событие; `generation` — поколение последней принятой
    заявки (0 — не было); `top` — наибольшее поколение среди ВСЕХ записей узла, включая
    проигнорированные (`next_generation` его превышает: новый файл не совпадёт с лежащим);
    `ignored` — записи, не ставшие событием, с причиной."""

    current: Event | None
    generation: int
    top: int
    ignored: tuple[Ignored, ...]


def node_history(history: History, node: str) -> NodeHistory:
    """Один проход по записям узла, старые первыми. Заявка с поколением не больше
    действующего и остановка не действующего поколения игнорируются: не становятся
    событием и не сдвигают поколение — полномочий не дают и узел не запирают."""
    current: Event | None = None
    last = top = 0
    ignored: list[Ignored] = []
    for event in history:
        if not _touches(event, node):
            continue
        gen = _generation(event, node)
        top = max(top, gen)
        if isinstance(event, DeliveredStop) and (gen != last or last == 0):
            reason = f"узел {node}: остановка поколения {gen}, действует {last}"
            ignored.append(Ignored(event, reason))
            continue
        if isinstance(event, DeliveredRequest) and gen <= last:
            reason = f"узел {node}: поколение {gen} не больше прежнего {last}"
            ignored.append(Ignored(event, reason))
            continue
        if isinstance(event, DeliveredRequest):
            last = gen
        current = event
    return NodeHistory(current, last, top, tuple(ignored))


def current_event(history: History, node: str) -> Event | None:
    """Действующее событие узла — из `node_history`."""
    return node_history(history, node).current


def next_generation(history: History, node: str) -> int:
    """Поколение следующей заявки узла — из `node_history`."""
    return node_history(history, node).top + 1
```

`envelope_refusal` — первые три строки (`broken = generation_refusal(...)` / `if broken ...` /
`return broken`) удаляются: событие узла — `current_event` (то есть `node_history`), проверки
конверта прежние.

`governance/act_status.py` (листинг Task 14) — из импорта `governance.act_envelope` убирается
`generation_refusal`, из `Statuses._compute` — три строки `broken = generation_refusal(...)` /
`if broken is not None:` / `return f"not_approved:{broken}"`. `is_review`, `_stopped` и
`_compute` берут событие через `current_event` — то есть из того же `node_history`;
`act_node.reopen` (событие и поколение остановки) и `act_node.generation_of` (`next_generation`)
— тоже. Других мест пропуска записей нет: правило живёт в одной функции.

`tests/test_act_envelope.py` (тесты части A) — в импорте `generation_refusal` заменяется на
`node_history`; `test_non_monotonic_generation_refused` заменяется на:

```python
def test_non_monotonic_request_ignored() -> None:
    """П. 9 заметок части A, вариант (Б) (решение владельца 01.11): заявка с поколением не
    больше действующего — не событие узла; действующее событие и поколение прежние."""
    stale = DeliveredRequest("5" * 40, "a5" * 20, _req("approve-2-1-4", 2))
    state = node_history([OLD, NEW, stale], "req")
    assert state.current is NEW and state.generation == 2
    assert len(state.ignored) == 1
    assert state.ignored[0].reason == "узел req: поколение 2 не больше прежнего 2"
    assert envelope_refusal(_env(NEW), [OLD, NEW, stale], "req", "h" * 40, PINS, "21") is None
```

`test_stop_of_other_generation_refused` заменяется на (вместе с новыми тестами согласия):

```python
def test_stop_of_other_generation_is_noop() -> None:
    """П. 9 (Б): остановка не действующего поколения отнимать нечего — игнорируется."""
    stale = DeliveredStop("3" * 40, StopRecord("req", 1, "не то поколение"))
    state = node_history([OLD, NEW, stale], "req")
    assert state.current is NEW
    assert len(state.ignored) == 1
    assert state.ignored[0].reason == "узел req: остановка поколения 1, действует 2"
    assert envelope_refusal(_env(NEW), [OLD, NEW, stale], "req", "h" * 40, PINS, "21") is None


def test_stop_before_any_request_ignored() -> None:
    """П. 9 (Б): остановка узла без принятой заявки — не событие; узел остаётся без
    события (старый путь), следующее поколение — выше записи."""
    stray = DeliveredStop("3" * 40, StopRecord("req", 1, "чужая запись"))
    state = node_history([stray], "req")
    assert state.current is None and state.generation == 0
    assert len(state.ignored) == 1
    assert state.ignored[0].reason == "узел req: остановка поколения 1, действует 0"
    assert next_generation([stray], "req") == 2


def test_ignored_request_then_next_generation_is_current() -> None:
    """П. 9 (Б): после проигнорированной записи заявка `g+1` — действующее событие."""
    stale = DeliveredRequest("5" * 40, "a5" * 20, _req("approve-2-1-4", 2))
    history = [OLD, NEW, stale, AGAIN]
    assert node_history(history, "req").current is AGAIN
    assert envelope_refusal(_env(AGAIN), history, "req", "h" * 40, PINS, "21") is None
    assert next_generation(history, "req") == 4


STRAY_STOP = DeliveredStop("6" * 40, StopRecord("req", 9, "чужое поколение"))
STALE_REQ = DeliveredRequest("5" * 40, "a5" * 20, _req("approve-2-1-4", 1))


@pytest.mark.parametrize("history", [
    [], [OLD], [OLD, NEW], [OLD, NEW, STOP2], [OLD, NEW, STOP2, AGAIN], [STRAY_STOP],
    [OLD, STRAY_STOP], [OLD, NEW, STALE_REQ], [OLD, NEW, STOP2, STALE_REQ, AGAIN],
    [OLD, NEW, STOP2, STOP2],
], ids=["empty", "one", "two", "stopped", "after_stop", "stray_stop", "stop_other_gen",
        "stale_request", "stale_then_next", "double_stop"])
def test_history_consumers_agree(history: list) -> None:
    """Решение владельца 01.11: одно вычисление состояния — `current_event`,
    `next_generation` и `envelope_refusal` выводятся из `node_history`, второго правила
    пропуска нет."""
    state = node_history(history, "req")
    assert current_event(history, "req") is state.current
    assert next_generation(history, "req") == state.top + 1
    reason = envelope_refusal(_env(NEW), history, "req", "h" * 40, PINS, "21")
    if state.current is None:
        assert reason == "узел req: нет доставленной заявки review/v1"
    elif isinstance(state.current, DeliveredStop):
        gen = state.current.stop.generation
        assert reason == f"узел req: одобрение поколения {gen} остановлено"
    else:
        assert (reason is None) is (state.current is NEW)
```

`tests/test_act_status.py` — в импорт `governance.act_envelope` добавляются `next_generation`,
`node_history`; тесты:

```python
STRAY = DeliveredStop("d" * 40, StopRecord("behaviour-spec", 7, "чужое поколение"))


def _beh(rid: str, merge: str, gen: int) -> DeliveredRequest:
    return delivered(rid, merge, {"behaviour-spec": (H_BEH, gen)},
                     {"behaviour-spec": {"requirements": "blob-req"}})


def test_ignored_record_then_next_generation_approves() -> None:
    """П. 9 заметок части A, вариант (Б) (решение владельца 01.11): проигнорированные
    записи (остановка чужого поколения, заявка с неубывающим поколением) узел не запирают —
    одобрение прежнего поколения действует, следующая заявка `g+1` одобряет узел."""
    (r1, r2), snap = both_approved()
    history = (r1, r2, STRAY, _beh("approve-3-1-1", "c" * 40, 1))
    assert statuses(history, snap).of("behaviour-spec") == "approved"
    gen = next_generation(history, "behaviour-spec")
    r4 = _beh("approve-4-1-1", "e" * 40, gen)
    history = (*history, r4)
    assert "конверт другого поколения" in statuses(history, snap).of("behaviour-spec")
    snap.files[envelope_path(WS, "behaviour-spec")] = envelope(r4, "behaviour-spec")
    assert statuses(history, snap).of("behaviour-spec") == "approved"


@pytest.mark.parametrize("tail", ["none", "stray_stop", "stale_request", "own_stop",
                                  "stray_then_own_stop"])
def test_statuses_agree_with_node_history(tail: str) -> None:
    """Решение владельца 01.11: `Statuses` выводит событие узла из того же `node_history`,
    что `current_event`/`envelope_refusal`/`reopen`, — второго правила пропуска нет."""
    (r1, r2), snap = both_approved()
    own = DeliveredStop("e" * 40, StopRecord("behaviour-spec", 1, "переоткрыт"))
    extra = {"none": (), "stray_stop": (STRAY,), "own_stop": (own,),
             "stale_request": (_beh("approve-3-1-1", "c" * 40, 1),),
             "stray_then_own_stop": (STRAY, own)}[tail]
    history = (r1, r2, *extra)
    state = node_history(history, "behaviour-spec")
    st = statuses(history, snap)
    assert st.is_review("behaviour-spec") is (state.current is not None)
    expected = ("approved" if state.current is r2 else
                f"not_approved:stopped:g{state.current.stop.generation}")
    assert st.of("behaviour-spec") == expected


def test_stray_stop_does_not_make_legacy_node_review() -> None:
    """П. 9 (Б): остановка узла без события `review/v1` — не событие; узел остаётся на
    старом пути с тем же ответом, что без записи (DoS одной записью невозможен)."""
    _history, snap = both_approved()
    stray = DeliveredStop("d" * 40, StopRecord("requirements", 1, "чужая запись"))
    st = statuses((stray,), snap)
    assert st.is_review("requirements") is False
    assert st.of("requirements") == statuses((), snap).of("requirements")
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `uv run --group governance pytest tests/test_act_node.py tests/test_act_envelope.py tests/test_act_status.py tests/test_governance_approve_node.py tests/test_governance_approval_ledger.py tests/test_governance_runner.py -v`
Expected: PASS; существующие тесты старого пути — без правок ожиданий (`tests/conftest.py`
Task 14 даёт продукт до миграции: `route` для заявки без маркера возвращает `None`,
`_legacy_outcome` держит прежние тексты); у тестов части A `test_act_envelope.py` меняются
ожидания ровно двух тестов (Step 8, п. 9 (Б)). Полный набор в обоих режимах, ruff, pyrefly.

- [ ] **Step 10: Commit**

```bash
git add governance/act_node.py governance/approve_node.py governance/approval_ledger.py \
  governance/act_decision.py governance/act_envelope.py governance/ops.py governance/runner.py \
  governance/act_status.py \
  tests/test_act_node.py tests/test_act_ops_body.py tests/test_governance_runner.py \
  tests/test_act_envelope.py tests/test_act_status.py
git commit -m "feat(act): путь узла review/v1 — общий вход admission, заявка с манифестом, конверт, остановка, аудит"
```

### Task 13a: `--repropose` — переиздание заявки узла

**Files:**
- Modify: `governance/act_node.py` (`repropose`)
- Modify: `governance/approve_node.py` (`approve_node(..., predecessor=None)`, `_propose`,
  `propose_from_source` — сквозная передача `predecessor`)
- Modify: `governance/approval_ledger.py` (`start_request(..., predecessor=None)`, `successor_of`)
- Modify: `governance/task_bridge.py` (CLI `--repropose <request-key>`, взаимоисключающий с
  прочими действиями, как `--approve-node`)
- Test: `tests/test_act_repropose.py`

Спека §6 и Task 13 называют `--repropose` штатным выходом (`policy_changed`, `Ambiguous`,
неустановленное навсегда — в т. ч. `actor_unresolved` всего PR (О15), `act_migrated`); сегодня
такой команды нет. **Бриф** (`brief_tools repropose`) переезжает в Task 17: заявка брифа `v2` с
пином активации появляется только с M2 discovery (решение исполнителя плана Р-14 — без `v2`
переиздавать нечего; цена — до части C выход для брифа прежний, новая заявка вручную через
`brief-propose`).

Правила (без переписывания терминальных записей — `approval_ledger._update` запрещает):
- **Предшественник терминален** (`invalidated:*`, `abandoned`): запись не трогается; его ещё
  открытые PR закрываются (`_close_if_open` с причиной из записи — иначе
  `_require_no_reopened_pr` не пустит новый candidate); создаётся **преемник** — новая заявка
  (`next_attempt`, новая ветка, текущий пин) с полем `predecessor: <key>`.
- **Предшественник жив** (смёрженный candidate с постоянным `Ambiguous`/`Unavailable`; открытый
  candidate со старым пином): разрешённый переход live → `invalidate_request(key, "reproposed:
  <причина>")`, открытый PR закрывается (`_close_if_open`), затем преемник.
- **`completed`** — отказ: завершённое одобрение не переиздаётся, остановка — `--reopen`.
- Доказательства предшественника (`review_id`, `delivery`, аудит) остаются в его записи.
- Восстановление: повторный `--repropose <key>` находит преемника (`successor_of`) и продолжает
  его, второго не создаёт.
- История поколений не фильтруется по действительности акта (§9.1 п. 0a): доставленный без
  действительного акта candidate — последнее событие узла, узел «не одобрен», откат к более
  раннему поколению запрещён; `next_generation` — по полной истории (Task 14 уже так считает).
- Никакой путь не превращает прежнюю заявку в одобренную.

- [ ] **Step 1: Write the failing tests** — `tests/test_act_repropose.py`:

```python
"""--repropose: преемник заявки без переписывания терминальной записи (§6, Task 13)."""

from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("steward")

from governance import act_node, act_status  # noqa: E402
from governance import approval_ledger as al  # noqa: E402
from governance import approve_node as an  # noqa: E402
from governance.act_check import ActivationView  # noqa: E402
from governance.act_decision import Accepted, Rejected  # noqa: E402
from governance.act_envelope import (  # noqa: E402
    DeliveredRequest,
    envelope_path,
    next_generation,
)
from governance.act_time import Ambiguous, Unavailable  # noqa: E402
from tests.test_act_node import NOW, BodyOps, Stand  # noqa: E402
from tests.test_governance_approve_node import (  # noqa: E402
    SLUG,
    World,
    _git,
    _make_world,
    approve,
)


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    w = _make_world(tmp_path, monkeypatch, seed_nodes=True)
    return replace(w, ops=BodyOps(w.forge))


@pytest.fixture()
def stand(world: World, monkeypatch: pytest.MonkeyPatch) -> Stand:
    return Stand(world, monkeypatch)


def _repropose(world: World, key: str):
    return act_node.repropose(world.state, world.ops, key)


def test_after_policy_changed_successor_carries_predecessor(world, stand, monkeypatch):
    first = approve(world, "charter")
    key = first.request
    newer = ActivationView(("activation", "b" * 40), "b" * 40, NOW)
    monkeypatch.setattr(act_status, "mode_probe",
                        lambda s, d=None: replace(stand.live(), activation=newer))
    with pytest.raises(RuntimeError, match="policy_changed"):
        approve(world, "charter")
    before = copy.deepcopy(world.state.ops[key])
    out = _repropose(world, key)
    succ = al.successor_of(world.state, key)
    assert succ is not None and world.state.ops[succ]["predecessor"] == key
    assert world.state.ops[key] == before  # терминальная запись не тронута
    assert world.state.ops[succ]["policy"]["activation_commit"] == "b" * 40
    assert out.request == succ


def test_persistent_ambiguous_merged_candidate(world, stand):
    first = approve(world, "charter")
    key = first.request
    stand.approve_head(world.state.ops[key]["candidate_pr"])
    approve(world, "charter")  # агентская доставка
    stand.verdict = Ambiguous()
    with pytest.raises(RuntimeError, match="--repropose"):
        approve(world, "charter")
    _repropose(world, key)
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED
    assert world.state.ops[key]["reason"].startswith("reproposed:")
    assert al.successor_of(world.state, key) is not None


def _squash_pr(world: World, pr: int, *, login: str = "alice") -> str:
    """Человек мержит candidate squash-ом: один родитель, голова PR — только в
    `refs/pull/N/head` и фактах PR (B1-9)."""
    rec, clone = world.forge.prs[pr], world.forge.merger
    assert clone is not None
    _git(clone, "fetch", "-q", "origin")
    _git(clone, "switch", "-q", "master")
    _git(clone, "reset", "-q", "--hard", "origin/master")
    _git(clone, "merge", "-q", "--squash", f"origin/{rec['branch']}")
    _git(clone, "commit", "-qm", f"PR #{pr} (squash)")
    sha = _git(clone, "rev-parse", "HEAD")
    _git(clone, "push", "-q", "origin", "master",
         f"refs/remotes/origin/{rec['branch']}:refs/pull/{pr}/head")
    rec.update(state="MERGED", mergedBy={"login": login}, mergedAt=NOW,
               mergeCommit={"oid": sha})
    return sha


def test_squash_delivery_read_and_repropose_cycle(world, stand):
    """B1-9: squash-доставка читается историей (голова из фактов PR, не из второго
    родителя), постоянный Ambiguous уводит в --repropose, преемник создаётся."""
    first = approve(world, "charter")
    key = first.request
    pr = world.state.ops[key]["candidate_pr"]
    stand.approve_head(pr)
    _squash_pr(world, pr)
    # читатель истории идёт по ЛОКАЛЬНОЙ базе: сперва синхронизировать target (B2-4)
    world.ops.checkout_and_pull(world.state.target_dir, "master")
    history = act_status.load_history(world.ops, world.state, "master")
    assert not isinstance(history, Unavailable)
    delivered = [e for e in history if isinstance(e, DeliveredRequest)]
    assert [e.request.request_id for e in delivered] == [key]
    assert delivered[0].head_sha == world.ops.pr_facts(SLUG, pr)["headRefOid"]
    stand.verdict = Ambiguous()
    with pytest.raises(RuntimeError, match="--repropose"):
        approve(world, "charter")
    _repropose(world, key)
    assert world.state.ops[key]["reason"].startswith("reproposed:")
    succ = al.successor_of(world.state, key)
    assert succ is not None and world.state.ops[succ]["predecessor"] == key


def test_open_candidate_is_closed_before_successor(world, stand):
    first = approve(world, "charter")
    key = first.request
    pr = world.state.ops[key]["candidate_pr"]
    _repropose(world, key)
    assert world.forge.prs[pr]["state"] == "CLOSED"
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED


def test_interrupted_repropose_creates_exactly_one_successor(world, stand, monkeypatch):
    first = approve(world, "charter")
    key = first.request
    real = act_node._create_successor
    calls: list[int] = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("упали после инвалидации")
        return real(*args, **kwargs)

    monkeypatch.setattr(act_node, "_create_successor", flaky)
    with pytest.raises(RuntimeError, match="упали"):
        _repropose(world, key)
    _repropose(world, key)
    _repropose(world, key)  # повтор продвигает того же преемника
    succs = [k for k, op in world.state.ops.items() if op.get("predecessor") == key]
    assert len(succs) == 1


def test_unknown_request_refused(world, stand):
    """b4 (`repropose`): ключа нет в леджере — отказ, преемник не создаётся."""
    with pytest.raises(RuntimeError, match="нет в леджере"):
        _repropose(world, "approve-9-9-9")


def test_two_successors_refused(world, stand):
    """b4 (`approval_ledger.successor_of`): два преемника одной заявки — разбор вручную."""
    first = approve(world, "charter")
    key = first.request
    for other in ("approve-8-8-1", "approve-8-8-2"):
        world.state.ops[other] = {**world.state.ops[key], "predecessor": key}
    with pytest.raises(RuntimeError, match="несколько преемников"):
        al.successor_of(world.state, key)


def test_successor_refused_when_step_has_live_request(world, stand, monkeypatch):
    """b5/b6 (`approve_node._propose#successor_joins_live`): над `acceptance` заявки нет, но
    у её шага есть живая (`design`) — преемник не создаётся и к ней не присоединяется."""
    from tests.test_act_node import _level_three

    _level_three(world, stand, monkeypatch)
    stand.mode = "review"
    first = approve(world, "design")
    with pytest.raises(RuntimeError, match="уже есть — продвиньте её"):
        an.approve_node(world.state, world.ops, "acceptance", predecessor="approve-9-9-9")
    assert world.state.ops[first.request]["nodes"] == ["design"]


def test_completed_request_refused(world, stand):
    first = approve(world, "charter")
    key = first.request
    stand.approve_head(world.state.ops[key]["candidate_pr"])
    approve(world, "charter")
    approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_COMPLETED
    with pytest.raises(RuntimeError, match="--reopen"):
        _repropose(world, key)


def test_delivered_without_act_is_last_event_no_rollback() -> None:
    """R1/g1 одобрен, R2/g2 доставлен без действительного акта с теми же байтами:
    узел «не одобрен», конверт R1 отвергнут, следующее поколение — 3."""
    from tests.test_act_status import H_REQ, Snapshot, delivered, envelope, statuses

    r1 = delivered("approve-1-0-1", "a" * 40, {"requirements": (H_REQ, 1)})
    r2 = delivered("approve-1-0-2", "b" * 40, {"requirements": (H_REQ, 2)})
    files = {"spec/10-requirements.md": "---\nspec_stage: requirements\nstatus: "
             "approval_pending\nversion: 2\n---\nтело\n",
             envelope_path("ws-7", "requirements"): envelope(r1, "requirements")}

    def decide(event: DeliveredRequest):
        return (Accepted("a" * 40, "df-reviewer", "11", "alice")
                if event is r1 else Rejected("d1_no_act"))

    st = statuses((r1, r2), Snapshot(files, {}), decide=decide)
    assert st.of("requirements") == "not_approved:act:d1_no_act"
    assert next_generation((r1, r2), "requirements") == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group governance pytest tests/test_act_repropose.py -v`
Expected: FAIL — `AttributeError: module 'governance.act_node' has no attribute 'repropose'`.

- [ ] **Step 3: Implement**

`governance/approval_ledger.py`:

```python
def successor_of(state: RunState, key: str) -> str | None:
    """Ключ заявки-преемника (`predecessor == key`); `None` — её нет."""
    found = [request_key(*nums) for nums, op in requests(state)
             if op.get("predecessor") == key]
    if len(found) > 1:
        raise RuntimeError(f"у заявки {key} несколько преемников {found} — разберите вручную")
    return found[0] if found else None
```

`start_request(..., predecessor: str | None = None)` — в записи `"predecessor": predecessor`
(только если задан: `**({"predecessor": predecessor} if predecessor else {})`).

`governance/approve_node.py`: `approve_node(state, ops, node_id, *, legacy_bundle=None,
predecessor: str | None = None)` передаёт `predecessor` в `_propose(..., predecessor=...)`, тот —
в `al.start_request(..., predecessor=predecessor)` (при присоединении к живой заявке шага
преемник не создаётся: `predecessor` с `joined is not None` — `RuntimeError` «живая заявка шага
уже есть — продвиньте её»); `propose_from_source(..., predecessor=None)` — так же.

`governance/approve_node.py`, `_propose` — перед присоединением к живой заявке шага (новая
проверка части B; реестр — `approve_node._propose#successor_joins_live`):

```python
    if predecessor is not None and joined is not None:  # первой из проверок присоединения
        raise RuntimeError(
            f"живая заявка шага {al.request_key(*joined[0])} уже есть — продвиньте её, "
            f"преемник {predecessor} не создаётся"
        )
```

`governance/act_node.py`:

```python
def _create_successor(state: RunState, ops: Ops, key: str, op: Mapping) -> an.ApprovalOutcome:
    """Новая заявка над узлами предшественника; текущий пин берёт `creation`."""
    nodes = list(op["nodes"])
    if op.get("source_sha"):
        return an.propose_from_source(state, ops, nodes, op["source_sha"], predecessor=key)
    outcome = an.approve_node(state, ops, nodes[0], predecessor=key)
    for node in nodes[1:]:
        outcome = an.approve_node(state, ops, node)
    return outcome


def repropose(state: RunState, ops: Ops, key: str) -> an.ApprovalOutcome:
    """`--repropose <key>`: преемник заявки; терминальная запись не переписывается."""
    op = state.ops.get(key)
    if op is None:
        raise RuntimeError(f"заявки {key} нет в леджере")
    if op.get("status") == al.STATUS_COMPLETED:
        raise RuntimeError(
            f"заявка {key} завершена — одобрение не переиздаётся; остановить его — "
            "`--reopen <node>`"
        )
    successor = al.successor_of(state, key)
    if successor is not None:
        first = state.ops[successor]["nodes"][0]
        return an.approve_node(state, ops, first)
    reason = op.get("reason") or "переиздание заявки (--repropose)"
    for pr in (op.get("candidate_pr"), op.get("finalize_pr")):
        # открытый PR предшественника блокировал бы новый candidate
        # (`_require_no_reopened_pr`) и оставался бы mergeable
        if pr is not None:
            an._close_if_open(state, ops, pr, key, reason)
    if al.is_live(op):
        al.invalidate_request(state, key, "reproposed: штатное переиздание заявки")
    return _create_successor(state, ops, key, state.ops[key])
```

`governance/task_bridge.py` — аргумент рядом с `--approve-node`:

```python
    parser.add_argument(
        "--repropose",
        default=None,
        metavar="REQUEST-KEY",
        help="переиздать заявку на одобрение узла (approve-W-K-A) новой заявкой с "
        "текущим пином: штатный выход после policy_changed, ambiguous_event_order "
        "и неустранимого «факт не установлен»; прежняя запись не переписывается",
    )
```

и ветка (в списке взаимоисключающих действий — как `--approve-node`):

```python
    if args.repropose is not None:
        from governance import act_node

        try:
            outcome = act_node.repropose(state, ops, args.repropose)
        except RuntimeError as exc:
            print(f"task_bridge: {exc}")
            return 1
        print(outcome.message)
        return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --group governance pytest tests/test_act_repropose.py tests/test_act_node.py tests/test_governance_approve_node.py tests/test_governance_task_bridge.py -v`
Expected: PASS. Полный набор, ruff, pyrefly.

- [ ] **Step 5: Commit**

```bash
git add governance/act_node.py governance/approve_node.py governance/approval_ledger.py \
  governance/task_bridge.py tests/test_act_repropose.py
git commit -m "feat(act): --repropose — преемник заявки узла с сохранением доказательств предшественника"
```

### Task 14: Статус узла — история, конверт, provenance, провайдер гейтов, хеш DAG

Исполняется **до** Task 13 (порядок ревизии 31.10): Task 13 опирается на `load_history`,
`Statuses`, `Deps` и `live_policy`/`mode_probe` этой задачи. Ревизия b1: B1-1, B1-2, B1-3, B1-5,
B1-6, B1-8, B1-9 — см. «Правки по ревью b1».

**Files:**
- Create: `governance/act_status.py`
- Modify: `governance/ops.py` (`Ops.first_parent_changes`, `Ops.snapshot_open/_reader/_close` +
  `RealOps`; `SnapshotRepo`, `SnapshotOps` — изолированный снимок базы, d5)
- Modify: `governance/act_settings.py` (`config_home(env)` — заметки части A, п. 11)
- Modify: `governance/act_envelope.py` (`envelope_refusal`: причины «байты» и «пины» раздельно)
- Modify: `governance/node_approval.py` (`DEBT_REVIEW_V1`)
- Modify: `governance/approve_node.py` (`_legacy_outcome` + `LegacyHalt` выделены из
  `_reconcile_candidate` ~1787 — Step 3a; `read_dag_state` ~480, `_require_upstream_ready` ~939)
- Modify: `governance/runner.py` (`_step_gate` S4 ~3275, `_step_s8` ~4024 — режим и провайдер;
  сохранённый зелёный гейт S4/S8 и `_next_wave` ~1300 — актуальный допуск, d1)
- Modify: `governance/task_bridge.py` (`_canonical_dag_hash` ~1138 — байты `approvals/` как
  оптимизация; `_admission_refusal`/`_require_admission` — актуальный допуск перед no-op §I5
  `deliver_superseded` и перед каждым успехом по сохранённому результату; В-1, решение
  владельца 01.11, d1)
- Modify: `tests/conftest.py` — изоляция путей акта для тестов вне `act_*` (перенесено сюда из
  Task 13: с этой задачи старые пути зовут `mode_probe`)
- Test: `tests/test_act_status.py`, `tests/test_act_legacy.py`, `tests/test_act_gate.py`;
  дополнить `tests/test_governance_runner.py` (стоп S4 при неустановленном режиме) и
  `tests/test_governance_task_bridge.py` (хеш DAG)

**Interfaces:**
- Consumes: Tasks 5, 5a, 8 (`parse_manifest`, `manifest_path`, `self_hash_v`, `envelope_refusal`,
  `generation_refusal` — до Task 13 Step 8, затем `node_history`, `current_event`, `parse_envelope`, `parse_stop`, `envelope_path`,
  `stop_path`), 11, 11a, 12, 12a (`act_trust` с `product_mode`, `act_context.delivery_verdict`,
  `act_journal`, `act_identity`).
- Produces:
  `Ops.first_parent_changes(target_dir, ref) -> list[tuple[str, tuple[str, ...], tuple[tuple[str, str], ...]]] | None`;
  `act_settings.config_home(env) -> Path`;
  `act_status.load_history(ops, state, base_ref) -> History | Unavailable` — заявка = коммит
  first-parent, **добавивший** манифест; голова одобренного коммита — из фактов PR доставки
  (`pr_for_merge` + `pr_facts.headRefOid`; merge и squash — B1-9); правка/удаление записи истории
  после доставки → `Unavailable` (Р-8, сужено b1);
  `act_status.Deps(readers, binding, journal, creds, gh, now, sleep, settings)`,
  `act_status.real_deps(state, env=None) -> Deps`;
  `act_status.Decider(state, ops, deps, dag, *, trust=None, journal=None)` — `(DeliveredRequest)
  -> Outcome`; память — по полной идентичности заявки (B1-2); до вердикта steward — идентичность и
  привязка (`act_identity`: продукт/воркстрим/ключ, пути узлов DAG, те же байты манифеста в
  одобренном head, upstream-пины = прямым входам head; иначе `Rejected(REQUEST_BINDING)`, B1-1);
  `Decider.legacy(pr, contract: Callable[[], Outcome]) -> Outcome` — admission старой доставки,
  до активации — результат `contract()` (B2-1);
  `act_status.LegacyProbe(state, ops, base_ref, decide_legacy, dag)` — provenance старого
  одобрения и ПОЛНЫЙ старый контракт (B1-3, B2-1, B3-1); коды отказа — константы
  `act_status.NO_EDIT … PINS` (по одному на проверку); `act_status.legacy_contract_refusal(candidate_text, base_text) -> str | None`,
  `act_status.candidate_identity(branch) -> tuple[str, str] | None`;
  `act_status.Statuses(*, dag, ws_id, history, node_path, text_of, blobs_of, decide, legacy)` —
  `of(node) -> str`, `is_review(node) -> bool`;
  `act_status.statuses_at_base(state, ops, dag, live, deps=None) -> Statuses`;
  `act_status.LivePolicy(mode, trust, journal, activation, policy)` (`mode` —
  `PRE_MIGRATION` | `merge` | `review`), `act_status.live_policy(state, deps) -> LivePolicy |
  Unavailable`, `act_status.mode_probe(state, deps=None)` — единая точка «режим продукта сейчас»;
  `act_status.gate_run(state, ops, dag, *, candidate, out, deps=None) -> tuple[str, str | None] | Unavailable`;
  `act_status.pr_for_merge(ops, slug, merge_commit) -> int | Unavailable`;
  `act_status.REQUEST_BINDING`; `node_approval.DEBT_REVIEW_V1 = "review_v1"`;
  `act_status.noop_refusal(state, ops, dag, nodes=None, deps=None) -> str | None` — одна точка
  «актуальный допуск перед успехом без полного пути» (В-1, d1), по свежему снимку базы
  `act_status.fresh_base(state, ops)` — контекстный менеджер: `(SHA ветки базы продукта
  repo_slug, Ops чтений из снимка)` или `Unavailable`; снимок удаляется по выходе (d2–d5);
  `statuses_at_base(..., *, ref=None)` — `ref` становится единственным снимком истории,
  provenance, байтов и входов;
  `Ops.snapshot_open(target_dir, repo_slug, branch) -> Fact[SnapshotRepo]` (временный bare-
  репозиторий с `alternates` на объекты продукта, fetch `https://github.com/<repo_slug>.git` в
  изолированном окружении, credential helper — `gh` профиля runner'а),
  `Ops.snapshot_reader(repo) -> Ops`, `Ops.snapshot_close(repo)`;
  `task_bridge._admission_refusal(state, ops, legacy_bundle) -> str | None`,
  `task_bridge._require_admission(state, ops, legacy_bundle, done) -> None`.

**Режим продукта сейчас** (§6.1, §10; ревизия b1, B1-5): `live_policy` — привязка обязательна
(нет — `Unavailable`), режим — `act_trust.product_mode` по **истории** режима: записи не было
никогда → `PRE_MIGRATION` (legacy-эпоха, старый путь); `merge` → старый путь; `review` → путь
`review/v1`; удаление записи после первой, откат `review → merge`, недочитанная нужная история →
`Unavailable` — стоп с причиной, не legacy. Все пути вне `act_*` (гейты S4/S8, `read_dag_state`,
`_require_upstream_ready`, создание заявки, общий вход Task 13) спрашивают `mode_probe`. Следствие
спеки, а не решение плана: на каждой машине, где исполняется путь узла или раннер, нужна привязка
`/etc/df/trust.toml` (H2 п. 4) — иначе все эти пути останавливаются; порядок «H1–H2 до части B»
уже в таблице «Порядок и зависимости». **Второе предусловие развёртывания** (ревью d5): для
приватного продукта профиль `gh` runner'а (`REVIEW_GH_CONFIG_DIR`, по умолчанию
`~/.config/review`) аутентифицирован на github.com с правом чтения продукта — снимок базы для
проверки допуска берётся по HTTPS через `gh auth git-credential`, а не через `origin`/SSH клона;
без этого каждая проверка допуска продукта в `review` — отказ (fail closed). Публичный продукт
читается анонимно. Процессы git снимка получают пустой `HOME` снимка: `~/.netrc` и `~/.gitconfig`
настоящего HOME не участвуют (ревью d6). Остаточный риск D6-1 (fallback `gh` к общему keyring)
— в разделе ревизии «Решения владельца 01.11».

**Правила статуса** (§9.1 п. 0a–4) — только для продукта в `review`: событие узла — последнее по
first-parent базы (доставленная заявка с манифестом, запись остановки; supersede — более поздняя
заявка; событие и записи, не ставшие событием, — одно вычисление `act_envelope.node_history`
после Task 13 Step 8, п. 9 (Б), решение владельца 01.11; до него — `generation_refusal`). Есть
событие: остановка → идентичность и привязка заявки +
вердикт её доставки (`Decider`) → `envelope_refusal` по байтам **снимка** → каждый прямой upstream
`approved` (рекурсивно); остановленный upstream — `not_approved:upstream_stopped:<A>`. **Нет
события** (§9.1 (а) п. 3, B1-3): frontmatter без долга (`node_debt`) — лишь предпосылка; одобрение
даёт только `LegacyProbe`: последняя правка файла узла в first-parent базы — запись его нынешней
подписи; её PR — finalize старой заявки (форма ветки из SSOT) **воркстрима прогона**; узел —
среди файлов candidate этой заявки и в его голове `approval_pending`; candidate смёржен этой
учёткой в это время; доставка candidate проходит admission (`Decider.legacy`; до активации
`ACT_MODE[P]` — `OLD_READER`, и тогда вердикт — **результат полного старого контракта**: байтовая
часть `legacy_contract_refusal` (вынесенные байты = байтам базы, поле хеша им равно, пины подписи
= пинам выноса) и `approve_node._legacy_outcome` — тот же код, что судит старый путь
(опубликованный снимок, закреплённый пин `approval-policy-source/v1` из записи заявки в леджере
прогона, его отпечаток, `authorized_signature` мержера по прежнему allowlist); записи заявки в
леджере нет, она про другой candidate или не выносила узел — отказ (`legacy_no_ledger_record` и
др.); пин или политика недоступны — `unavailable`; после активации — `Rejected(admission)`;
ревизии b2, b3: B2-1, B3-1). Механизм цепочки — решение
исполнителя плана Р-9 (правило — спека). Провайдер отвечает за снимок, который читает steward
(§12 п. 3): S4 — байты рабочего дерева волны, S8 и гейт доставки — база.

**Канонический хеш DAG и no-op §I5 — В-1, решено владельцем 01.11** (вопрос к спеке С-3). Слова
владельца (01.11): «В-1 — локальный хеш содержимого допустим как оптимизация, но он не заменяет
проверку одобрения. Перед успешным no-op для review/v1 проверяется актуальный допуск.»
Гибрид: (1) в манифест хеша входят блобы **всех** файлов `approvals/` воркстрима — конверты,
манифесты заявок (`requests/`), записи остановки (вариант (б), листинг Step 5): новое поколение,
остановка, supersede меняют хеш без чтений форджи; у продукта без `approvals/` величина прежняя;
(2) равный хеш для узлов `review/v1` **одного** no-op не даёт. Класс шире хеша (ревью d1):
**любой успешный ранний выход** — no-op §I5, повтор шага по сохранённому результату (`completed`
S4/S8), переход волны по завершённой заявке, возврат уже доставленного PR, no-op предложения по
frontmatter — для продукта в `review` требует актуального допуска. Одна точка —
`act_status.noop_refusal(state, ops, dag, nodes)`: каждый названный узел с событием `review/v1` —
`approved` по `statuses_at_base` (доверенный контекст §9.1); режим не установлен, не `approved`,
`unavailable:` — причина, успеха нет (fail closed); продукт не в `review` — прежние правила,
статусы не читаются. Полный перечень мест (site × что пропускает × где проверка × тест) — в
разделе ревизии «Решения владельца 01.11»; код — Step 5 (мост, раннер) и Task 13 (`_propose`).
Буква спеки: §9.1 (а) п. 4 требует, чтобы изменение допуска не скрывалось за no-op, —
проверенный статус спрашивается перед каждым успехом без полного пути. Цена — квота С-1: каждый
такой выход продукта в `review` читает доверенный контекст и факты форджи по узлам `review/v1`
(как один прогон гейта доставки).

- [ ] **Step 1: Write the failing tests**

`tests/test_act_status.py`:

```python
"""Статус узла review/v1 (§9.1; приёмка №17, 23, 25, 31; каскад, supersede; b1)."""

from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("steward")

from governance import act_context, act_status, bundle_inputs  # noqa: E402
from governance.act_decision import Accepted, Rejected  # noqa: E402
from governance.act_envelope import (  # noqa: E402
    DeliveredRequest,
    DeliveredStop,
    Envelope,
    StopRecord,
    envelope_path,
    parse_envelope,
    render_envelope,
    render_stop,
    stop_path,
)
from governance.act_projection import self_hash_v  # noqa: E402
from governance.act_request import (  # noqa: E402
    NodeEntry,
    NodeRequest,
    PolicyPin,
    manifest_path,
    render_manifest,
)
from governance.act_time import Ambiguous, Unavailable  # noqa: E402
from governance.facts import Fact, Outcome  # noqa: E402
from governance.ops import RealOps  # noqa: E402

WS = "ws-7"
SLUG = "owner/p"
DAG = (("10-requirements.md", ()), ("15-behaviour-spec.md", ("requirements",)))
FILES = {"requirements": "10-requirements.md", "behaviour-spec": "15-behaviour-spec.md"}
PIN = PolicyPin("owner/approval-policy", "main", "p" * 40, {"policy/modes.yaml": "0" * 64})


def node_text(name: str, body: str = "тело", status: str = "approval_pending") -> str:
    return f"---\nspec_stage: {name}\nstatus: {status}\nversion: 2\n---\n{body}\n"


def request(rid: str, nodes: dict[str, tuple[str, int]], pins=None, *,
            path_of=None, product: str = SLUG, ws_id: str = WS) -> NodeRequest:
    path_of = path_of or (lambda n: f"spec/{FILES[n]}")
    entries = {n: NodeEntry(path_of(n), h, g) for n, (h, g) in nodes.items()}
    return NodeRequest(rid, ws_id, product, entries, pins or {n: {} for n in nodes}, PIN)


def envelope(event: DeliveredRequest, node: str, review_id: str = "11") -> str:
    req = event.request
    return render_envelope(Envelope(
        node=node, request_id=req.request_id, generation=req.nodes[node].generation,
        manifest_path=manifest_path(req.ws_id, req.request_id), head_sha=event.head_sha,
        self_hash=req.nodes[node].content_hash, upstream_pins=dict(req.upstream_pins[node]),
        merge_commit=event.merge_commit, review_id=review_id, policy=req.policy.as_record(),
    ))


class Snapshot:
    """Байты снимка: путь → текст; прямые входы узла — словарь."""

    def __init__(self, files: dict[str, str], blobs: dict[str, dict[str, str]]) -> None:
        self.files, self.blobs = files, blobs

    def text(self, path: str) -> str | None:
        return self.files.get(path)


def statuses(history, snap: Snapshot, decide=None, legacy=None) -> act_status.Statuses:
    return act_status.Statuses(
        dag=DAG, ws_id=WS, history=history,
        node_path=lambda n: f"spec/{FILES[n]}",
        text_of=snap.text, blobs_of=lambda n: snap.blobs.get(n, {}),
        decide=decide or (lambda event: Accepted("m" * 40, "df-reviewer", "11", "alice")),
        legacy=legacy or (lambda path, text: "approved"),
    )


REQ_TEXT = node_text("requirements")
BEH_TEXT = node_text("behaviour-spec")
H_REQ, H_BEH = self_hash_v(REQ_TEXT, "review/v1"), self_hash_v(BEH_TEXT, "review/v1")


def delivered(rid: str, merge: str, nodes: dict, pins=None) -> DeliveredRequest:
    return DeliveredRequest(merge, "h" * 40, request(rid, nodes, pins))


def both_approved() -> tuple[tuple, Snapshot]:
    r1 = delivered("approve-1-0-1", "a" * 40, {"requirements": (H_REQ, 1)})
    r2 = delivered("approve-2-1-1", "b" * 40, {"behaviour-spec": (H_BEH, 1)},
                   {"behaviour-spec": {"requirements": "blob-req"}})
    files = {"spec/10-requirements.md": REQ_TEXT, "spec/15-behaviour-spec.md": BEH_TEXT,
             envelope_path(WS, "requirements"): envelope(r1, "requirements"),
             envelope_path(WS, "behaviour-spec"): envelope(r2, "behaviour-spec")}
    snap = Snapshot(files, {"behaviour-spec": {"requirements": "blob-req"}})
    return (r1, r2), snap


def test_review_nodes_approved_from_envelopes() -> None:
    history, snap = both_approved()
    st = statuses(history, snap)
    assert (st.of("requirements"), st.of("behaviour-spec")) == ("approved", "approved")


def test_missing_envelope_not_approved_even_if_frontmatter_says_approved() -> None:
    """Приёмка №23: без fallback на frontmatter."""
    history, snap = both_approved()
    del snap.files[envelope_path(WS, "behaviour-spec")]
    snap.files["spec/15-behaviour-spec.md"] = node_text("behaviour-spec", status="approved")
    assert statuses(history, snap).of("behaviour-spec").startswith("not_approved:")


def test_envelope_of_other_node_substituted() -> None:
    """Приёмка №17."""
    history, snap = both_approved()
    snap.files[envelope_path(WS, "behaviour-spec")] = snap.files[envelope_path(WS, "requirements")]
    assert statuses(history, snap).of("behaviour-spec").startswith("not_approved:")


def test_previous_generation_envelope_with_same_bytes_refused() -> None:
    """Приёмка №31, supersede: вторая заявка с тем же содержимым — нужен её конверт."""
    (r1, r2), snap = both_approved()
    r3 = delivered("approve-3-1-1", "c" * 40, {"behaviour-spec": (H_BEH, 2)},
                   {"behaviour-spec": {"requirements": "blob-req"}})
    assert "конверт другого поколения" in statuses((r1, r2, r3), snap).of("behaviour-spec")
    snap.files[envelope_path(WS, "behaviour-spec")] = envelope(r3, "behaviour-spec")
    assert statuses((r1, r2, r3), snap).of("behaviour-spec") == "approved"


def test_stop_makes_node_not_approved() -> None:
    """Приёмка №25: остановка — явное действие, акт не отменяется ретроактивно."""
    (r1, r2), snap = both_approved()
    stop = DeliveredStop("d" * 40, StopRecord("behaviour-spec", 1, "переоткрыт"))
    assert statuses((r1, r2, stop), snap).of("behaviour-spec") == "not_approved:stopped:g1"


def test_stopped_upstream_with_unchanged_bytes_blocks_downstream() -> None:
    (r1, r2), snap = both_approved()
    stop = DeliveredStop("d" * 40, StopRecord("requirements", 1, "переоткрыт"))
    st = statuses((r1, r2, stop), snap)
    assert st.of("behaviour-spec") == "not_approved:upstream_stopped:requirements"


def test_cascade_by_pins_without_a_history_record() -> None:
    history, snap = both_approved()
    snap.blobs["behaviour-spec"] = {"requirements": "blob-req-v2"}
    assert statuses(history, snap).of("behaviour-spec") == (
        "not_approved:узел behaviour-spec: upstream-пины изменились после одобрения")


@pytest.mark.parametrize("verdict", [Unavailable("http: 502"), Ambiguous()])
def test_undecided_delivery_is_unavailable(verdict) -> None:
    history, snap = both_approved()
    st = statuses(history, snap, decide=lambda event: verdict)
    assert st.of("requirements").startswith("unavailable:")


def test_rejected_delivery_not_approved() -> None:
    history, snap = both_approved()
    st = statuses(history, snap, decide=lambda event: Rejected("d1_no_act"))
    assert st.of("requirements") == "not_approved:act:d1_no_act"


def test_history_unavailable_is_unavailable() -> None:
    _, snap = both_approved()
    assert statuses(Unavailable("git"), snap).of("requirements").startswith("unavailable:")


def test_legacy_node_needs_provenance_not_frontmatter() -> None:
    """B1-3 (§9.1 (а) п. 3): узел без события review/v1 с честным frontmatter
    одобрен ТОЛЬКО если provenance+admission старого акта установлены."""
    r2 = delivered("approve-2-1-1", "b" * 40, {"behaviour-spec": (H_BEH, 1)},
                   {"behaviour-spec": {"requirements": "blob-req"}})
    legacy = node_text("requirements", status="approved").replace(
        "version: 2\n", "version: 2\napproved_by: bob\napproved_at: '2026-01-01T00:00:00Z'\n"
        f"approved_content_hash: {H_REQ}\n")
    files = {"spec/10-requirements.md": legacy, "spec/15-behaviour-spec.md": BEH_TEXT,
             envelope_path(WS, "behaviour-spec"): envelope(r2, "behaviour-spec")}
    snap = Snapshot(files, {"behaviour-spec": {"requirements": "blob-req"}})
    refused = statuses((r2,), snap, legacy=lambda path, text: "not_approved:legacy_admission:x")
    assert refused.of("requirements") == "not_approved:legacy_admission:x"
    assert refused.of("behaviour-spec") == "not_approved:upstream:requirements"
    proven = statuses((r2,), snap, legacy=lambda path, text: "approved")
    assert (proven.of("requirements"), proven.of("behaviour-spec")) == ("approved", "approved")
    # `is_review` — то же различение для путей вне провайдера (approve_node)
    assert (proven.is_review("requirements"), proven.is_review("behaviour-spec")) == (False, True)


def test_legacy_probe_not_called_for_debt_node() -> None:
    _, snap = both_approved()
    snap.files["spec/10-requirements.md"] = node_text("requirements", status="draft")
    st = statuses((), snap, legacy=lambda path, text: pytest.fail("provenance"))
    assert st.of("requirements") == "not_approved:debt_status"


ENVELOPE_SUBSTITUTES = {
    "node": "behaviour-spec",
    "request_id": "approve-9-9-9",
    "generation": 7,
    "manifest_path": "workstreams/ws-8/approvals/requests/approve-1-0-1.yaml",
    "head_sha": "e" * 40,
    "self_hash": "sha256:" + "0" * 64,
    "upstream_pins": {"charter": "f" * 40},
    "merge_commit": "f" * 40,
    "review_id": "99",
}


@pytest.mark.parametrize("field", [*sorted(ENVELOPE_SUBSTITUTES), "policy"])
def test_envelope_component_substituted_not_approved(field: str) -> None:
    """B2-3: подмена ЛЮБОГО компонента конверта при прочем верном — не approved
    (вход `Statuses.of` → `_envelope`)."""
    history, snap = both_approved()
    path = envelope_path(WS, "requirements")
    env = parse_envelope(snap.files[path])
    value = (ENVELOPE_SUBSTITUTES[field] if field != "policy" else
             {k: ("z" * 40 if isinstance(v, str) else v) for k, v in env.policy.items()})
    snap.files[path] = render_envelope(replace(env, **{field: value}))
    reason = ("подставлен" if field == "node" else "другого поколения"
              if field in ("generation", "request_id") else "не совпадает с манифестом")
    got = statuses(history, snap).of("requirements")
    assert got.startswith("not_approved:") and reason in got  # причина ЭТОЙ проверки (b3)


def test_envelope_of_other_workstream_not_used() -> None:
    """B2-3: конверт узла лежит только в approvals/ другого воркстрима — не approved."""
    history, snap = both_approved()
    snap.files[envelope_path("ws-8", "requirements")] = snap.files.pop(
        envelope_path(WS, "requirements"))
    assert "нет конверта" in statuses(history, snap).of("requirements")


@pytest.mark.parametrize("order", [("requirements", "behaviour-spec"),
                                   ("behaviour-spec", "requirements")],
                         ids=["approved_first", "refused_first"])
def test_statuses_memo_is_per_node(order) -> None:
    """B2-3: память `Statuses.of` — по узлу: вердикт одного не отдаётся другому."""
    history, snap = both_approved()
    del snap.files[envelope_path(WS, "behaviour-spec")]
    st = statuses(history, snap)
    got = {node: st.of(node) for node in order}
    assert got["requirements"] == "approved"
    assert "нет конверта" in got["behaviour-spec"]


def test_provider_twin_changed_candidate_bytes_not_approved() -> None:
    """§12 п. 3 steward: тот же ответ при изменённых байтах кандидата — не approved."""
    history, snap = both_approved()
    snap.files["spec/15-behaviour-spec.md"] = node_text("behaviour-spec", body="другое")
    assert statuses(history, snap).of("behaviour-spec") == (
        "not_approved:узел behaviour-spec: байты узла изменились после одобрения")


# --- исторический Decider: идентичность и привязка (B1-1, B1-2) ---------------------


class _DecideOps:
    """Ops для Decider: манифест в merge-коммите и в head, PR коммита."""

    def __init__(self, delivered: dict[str, str], head: dict[str, str | None]) -> None:
        self.delivered, self.head = delivered, head

    def show_file(self, target, ref, path):
        return self.delivered.get(path) if ref == "m" * 40 else None

    def repo_file_fact(self, slug, sha, path):
        text = self.head.get(path)
        return Fact(Outcome.FOUND, text, "ok") if text else Fact(Outcome.ABSENT, None, "нет")

    def prs_containing_commit(self, slug, sha):
        return [{"number": 7, "state": "MERGED", "mergeCommit": sha}]


def _decider(monkeypatch, ops, *, blobs=None, delivered_by: str = "m" * 40,
             seen: list | None = None) -> act_status.Decider:
    state = SimpleNamespace(repo_slug=SLUG, ws_id=WS, target_dir="/t", bundle_dir="spec",
                            run_id="r", base_ref="master")
    monkeypatch.setattr(bundle_inputs, "direct_blobs",
                        lambda state, ops, dag, node, ref: Fact(Outcome.FOUND, blobs or {}, "ok"))

    def verdict(*a, **k):
        if seen is not None:
            seen.append(k)
        return (Accepted("m" * 40, "df-reviewer", "11", "alice"),
                SimpleNamespace(merge_commit=delivered_by))

    monkeypatch.setattr(act_context, "delivery_verdict", verdict)
    deps = act_status.Deps(readers=None, binding=object(), journal=None, creds=(), gh=None,
                           now=None, sleep=None, settings=None)
    return act_status.Decider(state, ops, deps, DAG, trust=object(), journal=None)


def _event(req: NodeRequest) -> DeliveredRequest:
    return DeliveredRequest("m" * 40, "h" * 40, req)


def test_decider_accepts_bound_manifest(monkeypatch) -> None:
    req = request("approve-1-0-1", {"requirements": (H_REQ, 1)})
    text = render_manifest(req)
    path = manifest_path(WS, req.request_id)
    got = _decider(monkeypatch, _DecideOps({path: text}, {path: text}))(_event(req))
    assert isinstance(got, Accepted)


@pytest.mark.parametrize("case", ["absent_in_head", "changed_in_head", "foreign_path",
                                  "foreign_product", "foreign_ws", "head_other_request",
                                  "pins_changed"])
def test_decider_refuses_unbound_manifest(monkeypatch, case: str) -> None:
    """B1-1/B2-3: манифест только в merge-коммите / иной в head / путь узла не из DAG /
    чужой продукт / чужой воркстрим / в head манифест другой заявки / upstream-пины ≠
    прямым входам head — установленный отказ через исторический Decider."""
    kw = {"foreign_path": {"path_of": lambda n: "elsewhere/x.md"},
          "foreign_product": {"product": "owner/other"},
          "foreign_ws": {"ws_id": "ws-8"}}.get(case, {})
    reason = {"absent_in_head": "не в одобренном head",
              "changed_in_head": "≠ манифесту одобренного head",
              "foreign_path": "манифест: путь узла", "foreign_product": "манифест: product",
              "foreign_ws": "манифест: ws_id",
              "head_other_request": "≠ манифесту одобренного head",
              "pins_changed": "upstream-пины"}[case]
    req = request("approve-1-0-1", {"requirements": (H_REQ, 1)}, **kw)
    text = render_manifest(req)
    path = manifest_path(WS, req.request_id)
    other = render_manifest(request("approve-1-0-2", {"requirements": (H_REQ, 1)}))
    head = {"absent_in_head": {path: None}, "changed_in_head": {path: text + "# x\n"},
            "head_other_request": {path: other}}.get(case, {path: text})
    blobs = {"charter": "f" * 40} if case == "pins_changed" else None
    got = _decider(monkeypatch, _DecideOps({path: text}, head), blobs=blobs)(_event(req))
    assert isinstance(got, Rejected) and got.code == act_status.REQUEST_BINDING
    assert reason in got.detail  # причина ЭТОЙ проверки (b4)


def test_decider_judges_review_on_event_head(monkeypatch) -> None:
    """B2-3: ревью судится на голове доставленного события, не на чём-то ещё."""
    req = request("approve-1-0-1", {"requirements": (H_REQ, 1)})
    text, path = render_manifest(req), manifest_path(WS, req.request_id)
    seen: list = []
    _decider(monkeypatch, _DecideOps({path: text}, {path: text}), seen=seen)(_event(req))
    assert seen[0]["head_sha"] == "h" * 40 and seen[0]["request"] == req


def test_decider_delivery_of_other_merge_commit_unavailable(monkeypatch) -> None:
    """B2-3: PR доставил другой merge-коммит, чем событие истории — не Accepted."""
    req = request("approve-1-0-1", {"requirements": (H_REQ, 1)})
    text, path = render_manifest(req), manifest_path(WS, req.request_id)
    got = _decider(monkeypatch, _DecideOps({path: text}, {path: text}),
                   delivered_by="n" * 40)(_event(req))
    assert isinstance(got, Unavailable) and f"доставил {'n' * 40}" in got.reason


def test_decider_without_binding_trust_unavailable() -> None:
    """b5 (`Decider.trust`): нет привязки — доверие не грузится, `Unavailable` с причиной."""
    state = SimpleNamespace(repo_slug=SLUG, ws_id=WS, target_dir="/t", bundle_dir="spec")
    deps = act_status.Deps(readers=None, binding=None, journal=None, creds=(), gh=None,
                           now=None, sleep=None, settings=None)
    got = act_status.Decider(state, object(), deps, DAG).trust()
    assert isinstance(got, Unavailable)
    assert got.reason == "нет привязки корня доверия (§6.1 п. 1)"


def test_decider_head_bytes_unreadable_unavailable(monkeypatch) -> None:
    """b4: байты узла в одобренном head не читаются — хеш проекции не вычислен."""
    req = request("approve-1-0-1", {"requirements": (H_REQ, 1)})
    decider = _decider(monkeypatch, _DecideOps({}, {}))
    got = decider._head_hashes(_event(req))
    assert isinstance(got, Unavailable) and got.reason == f"spec/10-requirements.md в {'h' * 12} не читается"


def test_blobs_reader_unavailable_names_node(monkeypatch) -> None:
    """b4: прямые входы узла не прочитаны — причина называет узел."""
    monkeypatch.setattr(bundle_inputs, "direct_blobs", lambda *a: Fact(
        Outcome.UNAVAILABLE, None, "объект недоступен"))
    got = act_status._blobs(SimpleNamespace(), object(), DAG, "master")("requirements")
    assert isinstance(got, Unavailable)
    assert got.reason == "прямые входы requirements: объект недоступен"


def test_decider_memo_key_carries_full_identity(monkeypatch) -> None:
    """B2-3: ключ памяти — продукт, воркстрим, заявка и merge-коммит; другой merge-коммит
    той же заявки судится заново."""
    req = request("approve-1-0-1", {"requirements": (H_REQ, 1)})
    text, path = render_manifest(req), manifest_path(WS, req.request_id)
    seen: list = []
    ops = _DecideOps({path: text}, {path: text})
    ops.show_file = lambda target, ref, p: text  # манифест доставлен обоими коммитами
    decider = _decider(monkeypatch, ops, seen=seen)
    decider(_event(req))
    decider(DeliveredRequest("n" * 40, "h" * 40, req))
    assert set(decider._memo) == {(SLUG, WS, "approve-1-0-1", "m" * 40),
                                  (SLUG, WS, "approve-1-0-1", "n" * 40)}
    assert len(seen) == 2


@pytest.mark.parametrize("order", [(0, 1), (1, 0)], ids=["good_first", "bad_first"])
def test_decider_memo_is_per_request_not_per_commit(monkeypatch, order) -> None:
    """B1-2: два манифеста одного merge-коммита — вердикт каждого свой."""
    good = request("approve-1-0-1", {"requirements": (H_REQ, 1)})
    bad = request("approve-1-0-2", {"requirements": (H_REQ, 2)},
                  path_of=lambda n: "elsewhere/x.md")
    texts = {manifest_path(WS, r.request_id): render_manifest(r) for r in (good, bad)}
    decider = _decider(monkeypatch, _DecideOps(texts, texts))
    events = [_event(good), _event(bad)]
    got = {events[i].request.request_id: decider(events[i]) for i in order}
    assert isinstance(got["approve-1-0-1"], Accepted)
    assert isinstance(got["approve-1-0-2"], Rejected)
    assert got["approve-1-0-2"].code == act_status.REQUEST_BINDING


# --- история базы (merge и squash, B1-9) — настоящий git ----------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


class GitOps(RealOps):
    """Настоящий git + форджа стенда: PR по merge-коммиту и его голова."""

    def __init__(self, prs: dict[str, tuple[int, str]]) -> None:
        self.prs = prs  # merge-коммит → (номер PR, голова)

    def prs_containing_commit(self, slug, sha):
        hit = self.prs.get(sha)
        return [] if hit is None else [{"number": hit[0], "state": "MERGED", "mergeCommit": sha}]

    def pr_facts(self, slug, pr):
        head = next(h for n, h in self.prs.values() if n == pr)
        return {"headRefOid": head}


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "p"
    subprocess.run(["git", "init", "-q", "-b", "master", str(r)], check=True)
    _git(r, "config", "user.email", "t@e.st")
    _git(r, "config", "user.name", "t")
    (r / "README.md").write_text("x\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "init")
    return r


def _state(repo: Path) -> SimpleNamespace:
    return SimpleNamespace(repo_slug=SLUG, ws_id=WS, target_dir=str(repo))


def _branch(repo: Path, branch: str, files: dict[str, str]) -> str:
    _git(repo, "switch", "-qc", branch)
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", branch)
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "switch", "-q", "master")
    return head


def _merge(repo: Path, branch: str) -> str:
    _git(repo, "merge", "-q", "--no-ff", branch, "-m", f"Merge {branch}")
    return _git(repo, "rev-parse", "HEAD")


def _squash(repo: Path, branch: str) -> str:
    _git(repo, "merge", "-q", "--squash", branch)
    _git(repo, "commit", "-qm", f"{branch} (squash)")
    return _git(repo, "rev-parse", "HEAD")


def test_history_from_first_parent(repo: Path) -> None:
    req = request("approve-1-0-1", {"requirements": (H_REQ, 1)})
    head = _branch(repo, "cand", {manifest_path(WS, req.request_id): render_manifest(req)})
    m1 = _merge(repo, "cand")
    _branch(repo, "final", {envelope_path(WS, "requirements"): "ignored: true\n"})
    _merge(repo, "final")
    stop = StopRecord("requirements", 1, "r")
    _branch(repo, "stop", {stop_path(WS, "requirements", 1): render_stop(stop)})
    m3 = _merge(repo, "stop")
    got = act_status.load_history(GitOps({m1: (7, head)}), _state(repo), "master")
    assert got == (DeliveredRequest(m1, head, req), DeliveredStop(m3, stop))


def test_history_supports_squash_delivery(repo: Path) -> None:
    """B1-9: голова одобренного коммита — из фактов PR, второй родитель не нужен."""
    req = request("approve-1-0-1", {"requirements": (H_REQ, 1)})
    head = _branch(repo, "cand", {manifest_path(WS, req.request_id): render_manifest(req)})
    sq = _squash(repo, "cand")
    got = act_status.load_history(GitOps({sq: (7, head)}), _state(repo), "master")
    assert got == (DeliveredRequest(sq, head, req),)


@pytest.mark.parametrize("pull_ref", [True, False])
def test_history_fetches_squash_head_absent_locally(tmp_path: Path, pull_ref: bool) -> None:
    """B2-4: squash-голова есть только в `refs/pull/N/head` форджи — в клоне её нет;
    читатель догружает её; без такого ref — `Unavailable`, а не «голова есть»."""
    origin = tmp_path / "origin"
    subprocess.run(["git", "init", "-q", "-b", "master", str(origin)], check=True)
    _git(origin, "config", "user.email", "t@e.st")
    _git(origin, "config", "user.name", "t")
    (origin / "README.md").write_text("x\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", "init")
    req = request("approve-1-0-1", {"requirements": (H_REQ, 1)})
    head = _branch(origin, "cand", {manifest_path(WS, req.request_id): render_manifest(req)})
    sq = _squash(origin, "cand")
    if pull_ref:
        _git(origin, "update-ref", "refs/pull/7/head", head)
    _git(origin, "branch", "-D", "cand")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--no-local", f"file://{origin}", str(clone)],
                   check=True)
    absent = subprocess.run(["git", "-C", str(clone), "cat-file", "-e", f"{head}^{{commit}}"],
                            check=False)
    assert absent.returncode != 0  # головы в клоне действительно нет
    got = act_status.load_history(GitOps({sq: (7, head)}), _state(clone), "master")
    if pull_ref:
        assert got == (DeliveredRequest(sq, head, req),)
        _git(clone, "cat-file", "-e", f"{head}^{{commit}}")
    else:
        assert isinstance(got, Unavailable) and "не догружена" in got.reason


def test_history_manifest_commit_without_pr_unavailable(repo: Path) -> None:
    req = request("approve-1-0-1", {"requirements": (H_REQ, 1)})
    _branch(repo, "cand", {manifest_path(WS, req.request_id): render_manifest(req)})
    _squash(repo, "cand")
    got = act_status.load_history(GitOps({}), _state(repo), "master")
    assert isinstance(got, Unavailable) and "нужен ровно один" in got.reason


def test_manifest_edited_after_delivery_unavailable(repo: Path) -> None:
    req = request("approve-1-0-1", {"requirements": (H_REQ, 1)})
    path = manifest_path(WS, req.request_id)
    head = _branch(repo, "cand", {path: render_manifest(req)})
    m1 = _merge(repo, "cand")
    (repo / path).write_text(render_manifest(req) + "# x\n")
    _git(repo, "commit", "-qam", "edit")
    got = act_status.load_history(GitOps({m1: (7, head)}), _state(repo), "master")
    assert isinstance(got, Unavailable) and "запись истории изменена после доставки" in got.reason


@pytest.mark.parametrize("component", ["request_id", "ws_id"])
def test_history_manifest_off_its_path_unavailable(repo: Path, component: str) -> None:
    """B2-3: манифест лежит по пути другой заявки / манифест чужого воркстрима в
    approvals/ этого — событие не принимается (вход `_event`)."""
    req = request("approve-1-0-1", {"requirements": (H_REQ, 1)},
                  ws_id="ws-8" if component == "ws_id" else WS)
    path = manifest_path(WS, "approve-1-0-2" if component == "request_id" else "approve-1-0-1")
    head = _branch(repo, "cand", {path: render_manifest(req)})
    m1 = _merge(repo, "cand")
    got = act_status.load_history(GitOps({m1: (7, head)}), _state(repo), "master")
    assert isinstance(got, Unavailable) and "манифест лежит не по своему пути" in got.reason


@pytest.mark.parametrize("component", ["node", "generation"])
def test_history_stop_off_its_path_unavailable(repo: Path, component: str) -> None:
    """B2-3: запись остановки не по пути своего узла/поколения — не остановка."""
    stop = StopRecord("requirements", 1, "r")
    path = (stop_path(WS, "behaviour-spec", 1) if component == "node"
            else stop_path(WS, "requirements", 2))
    _branch(repo, "stop", {path: render_stop(stop)})
    _merge(repo, "stop")
    got = act_status.load_history(GitOps({}), _state(repo), "master")
    assert isinstance(got, Unavailable) and "остановка лежит не по своему пути" in got.reason


def test_history_stop_edited_after_delivery_unavailable(repo: Path) -> None:
    stop = StopRecord("requirements", 1, "r")
    path = stop_path(WS, "requirements", 1)
    _branch(repo, "stop", {path: render_stop(stop)})
    _merge(repo, "stop")
    (repo / path).write_text(render_stop(StopRecord("requirements", 1, "другая")))
    _git(repo, "commit", "-qam", "edit stop")
    got = act_status.load_history(GitOps({}), _state(repo), "master")
    assert isinstance(got, Unavailable) and "запись истории изменена после доставки" in got.reason


def test_history_unreadable_base_unavailable() -> None:
    """b4: first-parent базы не прочитан — история `Unavailable`, не пустая."""
    class _Ops:
        def first_parent_changes(self, target_dir, ref):
            return None

    got = act_status.load_history(_Ops(), SimpleNamespace(target_dir="/t", ws_id=WS), "master")
    assert isinstance(got, Unavailable) and got.reason == "история master: first-parent не прочитан"


def test_history_stop_of_other_workstream_ignored(repo: Path) -> None:
    """B2-3: остановка в approvals/ другого воркстрима — не событие этого."""
    stop = StopRecord("requirements", 1, "r")
    _branch(repo, "stop", {stop_path("ws-8", "requirements", 1): render_stop(stop)})
    _merge(repo, "stop")
    assert act_status.load_history(GitOps({}), _state(repo), "master") == ()


def test_first_parent_unreadable_is_none(tmp_path: Path) -> None:
    """b4: `git log` не прочитан (не репозиторий) — `None`, а не пустая история."""
    assert RealOps().first_parent_changes(str(tmp_path), "master") is None


def test_pr_for_merge_needs_exactly_one() -> None:
    class _Ops:
        def __init__(self, prs):
            self.prs = prs

        def prs_containing_commit(self, slug, sha):
            return self.prs

    m = "m" * 40
    assert act_status.pr_for_merge(_Ops([{"number": 7, "state": "MERGED", "mergeCommit": m}]),
                                   "o/p", m) == 7
    got = act_status.pr_for_merge(_Ops([]), "o/p", m)
    assert isinstance(got, Unavailable) and "нужен ровно один" in got.reason


def test_config_home_resolution() -> None:
    """Заметки части A, п. 11: то же правило, что заявлено discovery (M2)."""
    from governance.act_settings import config_home

    assert config_home({"XDG_CONFIG_HOME": "/cfg"}) == Path("/cfg")
    assert config_home({"XDG_CONFIG_HOME": "relative"}) == Path.home() / ".config"
    assert config_home({}) == Path.home() / ".config"


class ProductOps(GitOps):
    """Git и форджа стенда; источник снимка базы — локальный репозиторий продукта.
    Единственный путь к локальному источнику — переопределение `_snapshot_url` в тесте."""

    def __init__(self, prs, product: Path) -> None:
        super().__init__(prs)
        self.product = product

    def _snapshot_url(self, repo_slug: str) -> str:
        return str(self.product)


def remote_stop_world(tmp_path: Path, node: str = "requirements",
                      fname: str = "10-requirements.md") -> tuple[Path, Path, ProductOps]:
    """d3–d4: репозиторий продукта с доставленной заявкой `review/v1` узла и его конвертом;
    локальный клон снят ДО того, как в продукт доставлена остановка (база клона устарела).
    Возвращает (продукт, клон, ops с форджей стенда и источником снимка — продуктом)."""
    origin = tmp_path / "owner" / "p"
    origin.parent.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "master", str(origin)], check=True)
    _git(origin, "config", "user.email", "t@e.st")
    _git(origin, "config", "user.name", "t")
    (origin / "README.md").write_text("x\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", "init")
    text = node_text(node)
    req = request("approve-1-0-1", {node: (self_hash_v(text, "review/v1"), 1)},
                  path_of=lambda n: f"spec/{fname}")
    head = _branch(origin, "cand", {f"spec/{fname}": text,
                                    manifest_path(WS, req.request_id): render_manifest(req)})
    merge = _merge(origin, "cand")
    _branch(origin, "final", {envelope_path(WS, node): envelope(DeliveredRequest(merge, head, req),
                                                                node)})
    _merge(origin, "final")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    _branch(origin, "stop", {stop_path(WS, node, 1): render_stop(StopRecord(node, 1, "переоткрыт"))})
    _merge(origin, "stop")
    return origin, clone, ProductOps({merge: (11, head)}, origin)


def review_live(monkeypatch) -> None:
    """Продукт в `review`; доверенный вердикт доставки — «принят» (стенд без форджи).
    `statuses_at_base` и чтения git — настоящие."""
    live = SimpleNamespace(mode="review", trust=None, journal=None)
    monkeypatch.setattr(act_status, "mode_probe", lambda state, deps=None: live)
    monkeypatch.setattr(act_status, "real_deps", lambda state, env=None: None)
    monkeypatch.setattr(act_status.Decider, "__call__",
                        lambda self, event: Accepted("m" * 40, "df-reviewer", "11", "alice"))


def clone_state(clone: Path, **over) -> SimpleNamespace:
    fields = {"repo_slug": SLUG, "ws_id": WS, "target_dir": str(clone), "bundle_dir": "spec",
              "base_ref": "master", "brief": None}
    return SimpleNamespace(**{**fields, **over})


def _refs(repo: Path) -> str:
    return _git(repo, "for-each-ref", "--format=%(refname) %(objectname)")


STOPPED = "узел requirements: not_approved:stopped:g1"


def test_noop_refusal_reads_fresh_product_base(tmp_path: Path, monkeypatch) -> None:
    """d3 (D3-1): локальная база клона устарела — по ней узел «approved»; проверка допуска
    берёт свежий снимок продукта и видит остановку, без подмены `statuses_at_base`."""
    _origin, clone, ops = remote_stop_world(tmp_path)
    review_live(monkeypatch)
    state = clone_state(clone)
    stale = act_status.statuses_at_base(state, ops, DAG, act_status.mode_probe(state), None)
    assert stale.of("requirements") == "approved"
    assert act_status.noop_refusal(state, ops, DAG, ["requirements"]) == STOPPED


def test_snapshot_leaves_product_repo_untouched(tmp_path: Path, monkeypatch) -> None:
    """d4–d5: снимок — во временном репозитории: ref'ы клона (в т. ч. `local-keep` из
    mapping `remote.origin.fetch`), его `config` и `FETCH_HEAD` не тронуты; снимок свежий."""
    _origin, clone, ops = remote_stop_world(tmp_path)
    _git(clone, "branch", "local-keep")
    _git(clone, "config", "remote.origin.fetch", "+refs/heads/master:refs/heads/local-keep")
    config = clone / ".git" / "config"
    refs_before, config_before = _refs(clone), config.read_text()
    review_live(monkeypatch)
    assert act_status.noop_refusal(clone_state(clone), ops, DAG, ["requirements"]) == STOPPED
    assert _refs(clone) == refs_before
    assert config.read_text() == config_before
    fetch_head = clone / ".git" / "FETCH_HEAD"
    assert fetch_head.exists() is False


def test_snapshot_ignores_origin_of_the_clone(tmp_path: Path, monkeypatch) -> None:
    """d4 (D3-3): `origin` клона не читается — отстающая копия с совпадающим суффиксом пути
    (`…/stale/owner/p`) свежести не подменяет; снимок — из источника продукта."""
    _origin, clone, ops = remote_stop_world(tmp_path)
    stale = tmp_path / "stale" / "owner" / "p"
    subprocess.run(["git", "clone", "-q", "--bare", str(clone), str(stale)], check=True)
    _git(clone, "remote", "set-url", "origin", str(stale))
    review_live(monkeypatch)
    assert act_status.noop_refusal(clone_state(clone), ops, DAG, ["requirements"]) == STOPPED


def test_injected_git_config_and_env_have_no_effect(tmp_path: Path, monkeypatch) -> None:
    """d5 (TOCTOU D3-3): `url.*.insteadOf` на отстающую копию — в config клона, в
    глобальном (`GIT_CONFIG_GLOBAL`), через `GIT_CONFIG_COUNT/KEY/VALUE`, и в config клона
    МЕЖДУ созданием снимка и fetch; унаследованный `GIT_DIR` — ничего не меняет: снимок
    свежий, источник — продукт."""
    from governance import ops as ops_mod

    origin, clone, ops = remote_stop_world(tmp_path)
    stale = tmp_path / "stale" / "owner" / "p"
    subprocess.run(["git", "clone", "-q", "--bare", str(clone), str(stale)], check=True)
    rule = (f"url.{stale}.insteadOf", str(origin))
    _git(clone, "config", *rule)
    glob = tmp_path / "global.gitconfig"
    glob.write_text(f'[url "{stale}"]\n\tinsteadOf = {origin}\n')
    for key, value in {"GIT_CONFIG_GLOBAL": str(glob), "GIT_CONFIG_COUNT": "1",
                       "GIT_CONFIG_KEY_0": rule[0], "GIT_CONFIG_VALUE_0": rule[1],
                       "GIT_DIR": str(stale)}.items():
        monkeypatch.setenv(key, value)
    real_run = subprocess.run

    def run(cmd, **kw):
        done = real_run(cmd, **kw)
        if "init" in cmd:  # между созданием снимка и fetch
            real_run(["git", "-C", str(clone), "config", "--add", *rule],
                     env={"PATH": os.environ["PATH"]}, check=True)
        return done

    monkeypatch.setattr(ops_mod.subprocess, "run", run)
    review_live(monkeypatch)
    assert act_status.noop_refusal(clone_state(clone), ops, DAG, ["requirements"]) == STOPPED


def test_production_snapshot_command_is_isolated(tmp_path: Path, monkeypatch) -> None:
    """d4–d5: боевой источник — только `https://github.com/<repo_slug>.git`; `origin` не
    спрашивается; fetch — во временном репозитории, credential helper сброшен и задан
    явно (`gh` профиля runner'а), окружение — из allowlist без `GIT_*` извне, запросов нет."""
    from governance import ops as ops_mod

    _origin, clone, _ops = remote_stop_world(tmp_path)
    profile = tmp_path / "review-profile"
    monkeypatch.setenv("REVIEW_GH_CONFIG_DIR", str(profile))
    monkeypatch.setenv("GIT_DIR", "/elsewhere")
    calls: list[tuple[list[str], dict]] = []
    real_run = subprocess.run

    def run(cmd, **kw):
        calls.append((cmd, kw.get("env") or {}))
        if "fetch" in cmd:
            return subprocess.CompletedProcess(cmd, 128, "", "")
        return real_run(cmd, **kw)

    monkeypatch.setattr(ops_mod.subprocess, "run", run)
    got = RealOps().snapshot_open(str(clone), "owner/p", "master")
    cmd, env = next((c, e) for c, e in calls if "fetch" in c)
    assert cmd[cmd.index("--refmap=") + 1] == "https://github.com/owner/p.git"
    assert cmd[2:6] == ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]
    isolation = tuple(env.get(k) for k in ("GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL",
                                            "GIT_TERMINAL_PROMPT", "GH_CONFIG_DIR"))
    assert isolation == ("1", os.devnull, "0", str(profile))
    assert [k for k in env if k.startswith("GIT_") and k not in ISOLATION] == []
    assert not any("origin" in c for c, _e in calls)
    assert got.detail == "fetch owner/p master: код 128"


ISOLATION = {"GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL", "GIT_TERMINAL_PROMPT", "GIT_ASKPASS"}


@pytest.mark.parametrize("slug", ["file:///tmp/stale/owner/p", "/tmp/stale/owner/p",
                                  "../owner/p", "owner/p/extra", "-uowner/p", "owner/.."],
                         ids=["file_url", "local_path", "dotdot", "extra", "dash", "dot_repo"])
def test_snapshot_rejects_non_slug_source(tmp_path: Path, monkeypatch, slug: str) -> None:
    """d4: в источник снимка не попадает ни локальный путь, ни `file://`, ни что-либо,
    кроме `owner/repo`; git не запускается."""
    from governance import ops as ops_mod

    monkeypatch.setattr(ops_mod.subprocess, "run", lambda *a, **k: pytest.fail("git"))
    got = RealOps().snapshot_open(str(tmp_path), slug, "master")
    assert got.detail == f"repo_slug {slug!r} — не owner/repo"


def test_noop_refusal_refuses_unfetchable_product(tmp_path: Path, monkeypatch) -> None:
    """d3: репозиторий продукта не отвечает (путь исчез) — отказ, а не локальная база;
    временный снимок удалён."""
    origin, clone, ops = remote_stop_world(tmp_path)
    origin.rename(tmp_path / "owner" / "gone")
    review_live(monkeypatch)
    got = act_status.noop_refusal(clone_state(clone), ops, DAG, ["requirements"])
    assert got == "свежая база: снимок owner/p@master: fetch owner/p master: код 128"


def test_interleaved_snapshots_are_independent(tmp_path: Path) -> None:
    """d4–d5 (D3-4): два чередующихся вызова на одном клоне — один из отстающего источника;
    у каждого свой временный репозиторий и свой SHA; после выхода оба удалены."""
    origin, clone, fresh_ops = remote_stop_world(tmp_path)
    stale = tmp_path / "stale" / "owner" / "p"
    subprocess.run(["git", "clone", "-q", "--bare", str(clone), str(stale)], check=True)
    stale_ops = ProductOps(fresh_ops.prs, stale)
    state = clone_state(clone)
    with act_status.fresh_base(state, fresh_ops) as a, act_status.fresh_base(state, stale_ops) as b:
        dirs = [a[1].repo.git_dir, b[1].repo.git_dir]
        assert (a[0], b[0]) == (_git(origin, "rev-parse", "master"), _git(stale, "rev-parse", "master"))
        assert len(set(dirs)) == 2
    assert [Path(d).exists() for d in dirs] == [False, False]


def test_snapshot_removed_on_exception(tmp_path: Path) -> None:
    """d5: исключение внутри проверки — временный репозиторий снимка всё равно удалён."""
    _origin, clone, ops = remote_stop_world(tmp_path)
    seen: list[str] = []
    with pytest.raises(RuntimeError, match="сбой чтения"):
        with act_status.fresh_base(clone_state(clone), ops) as snapshot:
            seen.append(snapshot[1].repo.git_dir)
            raise RuntimeError("сбой чтения")
    assert Path(seen[0]).exists() is False


class CredentialOps(ProductOps):
    """Шов теста: credential helper — скрипт, отдающий токен; никакого другого пути."""

    def __init__(self, prs, product: Path, helper: str) -> None:
        super().__init__(prs, product)
        self.helper = helper

    def _credential_helper(self) -> str:
        return self.helper


TOKEN = "ghs_TEST_TOKEN_SECRET"


def token_helper(tmp_path: Path) -> str:
    """Helper-скрипт: токен — из файла (как у `gh`), не в собственном тексте helper'а."""
    secret = tmp_path / "token"
    secret.write_text(TOKEN)
    return (f'!f() {{ test "$1" = get && echo username=x-access-token && '
            f'echo "password=$(cat {secret})"; }}; f')


def test_private_product_credentials_come_only_from_the_helper(tmp_path: Path,
                                                               monkeypatch) -> None:
    """d5 (D3-6): приватный продукт — учётные данные отдаёт только явно заданный helper,
    без интерактивного запроса; helper глобальной конфигурации не участвует."""
    _origin, _clone, ops = remote_stop_world(tmp_path)
    glob = tmp_path / "global.gitconfig"
    glob.write_text("[credential]\n\thelper = \"!echo password=FOREIGN\"\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(glob))
    cred = CredentialOps(ops.prs, ops.product, token_helper(tmp_path))
    git_dir = tmp_path / "probe.git"
    subprocess.run(["git", "init", "-q", "--bare", str(git_dir)], check=True)
    done = cred._snapshot_git(str(git_dir), "credential", "fill",
                              stdin="protocol=https\nhost=github.com\npath=owner/p.git\n\n")
    assert f"password={TOKEN}" in done.stdout


def test_token_never_in_argv_or_diagnostics(tmp_path: Path, monkeypatch) -> None:
    """d5: fetch приватного продукта не удался — токена нет ни в argv, ни в диагностике."""
    from governance import ops as ops_mod

    _origin, clone, ops = remote_stop_world(tmp_path)
    cred = CredentialOps(ops.prs, ops.product, token_helper(tmp_path))
    cred._snapshot_url = lambda slug: "https://127.0.0.1:9/owner/p.git"  # отказ соединения
    argv: list[str] = []
    real_run = subprocess.run
    monkeypatch.setattr(ops_mod.subprocess, "run",
                        lambda cmd, **kw: argv.extend(cmd) or real_run(cmd, **kw))
    got = cred.snapshot_open(str(clone), "owner/p", "master")
    assert got.outcome is Outcome.UNAVAILABLE
    assert TOKEN not in " ".join(argv) + got.detail


def test_public_product_works_without_gh_token(tmp_path: Path, monkeypatch) -> None:
    """d5: профиль `gh` без токена — публичный (здесь локальный) продукт читается анонимно,
    helper не мешает; запрос учётных данных не интерактивен и завершается отказом."""
    _origin, clone, ops = remote_stop_world(tmp_path)
    monkeypatch.setenv("REVIEW_GH_CONFIG_DIR", str(tmp_path / "empty-profile"))
    review_live(monkeypatch)
    assert act_status.noop_refusal(clone_state(clone), ops, DAG, ["requirements"]) == STOPPED
    git_dir = tmp_path / "probe.git"
    subprocess.run(["git", "init", "-q", "--bare", str(git_dir)], check=True)
    failing = CredentialOps(ops.prs, ops.product, "!false")
    done = failing._snapshot_git(str(git_dir), "credential", "fill",
                                 stdin="protocol=https\nhost=github.com\npath=owner/p.git\n\n")
    assert done.returncode != 0


class RecordingGitOps(ProductOps):
    """Git стенда: любое чтение git мимо снимка (по `target_dir` клона) — записывается."""

    def __init__(self, prs, product: Path) -> None:
        super().__init__(prs, product)
        self.outside: list[str] = []

    def first_parent_changes(self, target_dir, ref):
        self.outside.append(f"first_parent {ref}")
        return super().first_parent_changes(target_dir, ref)

    def show_file(self, target_dir, ref, path):
        self.outside.append(f"show {ref}:{path}")
        return super().show_file(target_dir, ref, path)


def test_admission_reads_only_the_snapshot(tmp_path: Path, monkeypatch) -> None:
    """d3–d5: внутри проверки допуска история, provenance старого акта, байты узла,
    конверт и прямые входы читаются из временного репозитория снимка по ОДНОМУ SHA; мимо
    снимка (клоном продукта) не читается ничего."""
    from governance import ops as ops_mod

    origin, clone, base_ops = remote_stop_world(tmp_path)
    ops = RecordingGitOps(base_ops.prs, origin)
    reads: list[tuple[str, str]] = []
    for name in ("first_parent_changes", "show_file", "show_file_bytes"):
        real = getattr(ops_mod.SnapshotOps, name)

        def record(self, target_dir, ref, *rest, _real=real):
            reads.append((ref, rest[0] if rest else ""))
            return _real(self, target_dir, ref, *rest)

        monkeypatch.setattr(ops_mod.SnapshotOps, name, record)
    review_live(monkeypatch)
    state = clone_state(clone)
    with act_status.fresh_base(state, ops) as (snapshot, reader):
        statuses = act_status.statuses_at_base(state, reader, DAG, act_status.mode_probe(state),
                                               None, ref=snapshot)
        statuses.of("requirements")
        statuses.blobs_of("behaviour-spec")  # прямой вход — requirements
        statuses.legacy("spec/15-behaviour-spec.md", BEH_TEXT)  # provenance
    assert ops.outside == []
    snapshot_refs = {snapshot}
    node_reads = {ref for ref, path in reads
                  if path in ("", "spec/10-requirements.md", envelope_path(WS, "requirements"))}
    assert node_reads == snapshot_refs
    assert all(len(ref) == 40 and set(ref) <= set("0123456789abcdef") for ref, _p in reads)


@pytest.mark.parametrize("name", ["blob_in_commit", "commit_files", "checkout_and_pull",
                                  "is_ancestor", "show_repo_file_bytes"])
def test_snapshot_ops_denies_unlisted_methods(tmp_path: Path, name: str) -> None:
    """d5 (deny-by-default): метод вне пяти git-методов снимка и allowlist форджи —
    `AttributeError` с именем метода; клон не читается."""
    _origin, clone, ops = remote_stop_world(tmp_path)
    with act_status.fresh_base(clone_state(clone), ops) as (_sha, reader):
        with pytest.raises(AttributeError, match=f"{name!r} не разрешён"):
            getattr(reader, name)


def test_snapshot_forge_allowlist_reads_no_clone() -> None:
    """d5: каждый метод allowlist форджи `SnapshotOps` — метод `RealOps` без `target_dir` и
    без запуска `git`: локальный клон он прочитать не может."""
    import inspect

    from governance import ops as ops_mod

    readers = [name for name in sorted(ops_mod.SNAPSHOT_FORGE_METHODS)
               if "target_dir" in inspect.signature(getattr(RealOps, name)).parameters
               or '"git"' in inspect.getsource(getattr(RealOps, name))]
    assert readers == []


def test_foreign_home_credentials_have_no_effect(tmp_path: Path, monkeypatch) -> None:
    """d6 (D6-2): чужие `~/.netrc` и `~/.gitconfig` настоящего HOME не участвуют — процессы
    git снимка получают пустой `HOME` (и `XDG_CONFIG_HOME`) внутри временного каталога
    снимка; профиль `gh` передаётся абсолютным путём, разрешённым ДО подмены HOME."""
    from governance import ops as ops_mod

    _origin, clone, ops = remote_stop_world(tmp_path)
    home = tmp_path / "real-home"
    home.mkdir()
    (home / ".netrc").write_text("machine github.com login x-access-token password FOREIGN\n")
    (home / ".gitconfig").write_text('[credential]\n\thelper = "!echo password=FOREIGN"\n')
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("REVIEW_GH_CONFIG_DIR", "~/.config/review")
    seen: list[tuple[str, list[str], bool, str]] = []
    real_run = subprocess.run

    def run(cmd, **kw):
        if "fetch" in cmd:
            env = kw["env"]
            seen.append((Path(env["HOME"]).name, sorted(os.listdir(env["HOME"])),
                         Path(env.get("XDG_CONFIG_HOME", "")).parent == Path(env["HOME"]),
                         env["GH_CONFIG_DIR"]))
        return real_run(cmd, **kw)

    monkeypatch.setattr(ops_mod.subprocess, "run", run)
    review_live(monkeypatch)
    assert act_status.noop_refusal(clone_state(clone), ops, DAG, ["requirements"]) == STOPPED
    expected = [("home", [], True, str((home / ".config" / "review").resolve()))]
    snapshot_home = list(seen)
    assert snapshot_home == expected
```

`tests/test_act_legacy.py`:

```python
"""Provenance старого одобрения: отрицательные двойники (§9.1 (а) п. 3; b1 B1-3, b2 B2-1,
b3 B3-1/B3-3).

Сквозной путь — настоящие `LegacyProbe → Decider.legacy → delivery_verdict →
check_delivery → approve_node._legacy_outcome`; подменены только факты форджи,
снимок старой политики и контекст доверия (до активации — `OLD_READER`). Каждый
двойник ломает ровно один компонент при прочих верных и проверяет ТОЧНЫЙ код
своей проверки.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("steward")

from governance import act_status  # noqa: E402
from governance import approval_branches as ab  # noqa: E402
from governance import approval_facts as af  # noqa: E402
from governance import node_approval as na  # noqa: E402
from governance.act_check import LegacyAccepted  # noqa: E402
from governance.act_decision import Rejected  # noqa: E402
from governance.brief_facts import BriefPrFacts  # noqa: E402
from governance.facts import Fact, Outcome  # noqa: E402
from governance.ops import RealOps  # noqa: E402
from tests.act_cases_facts import PRODUCT, context, sha  # noqa: E402
from tests.test_act_context import _trust, _wire  # noqa: E402

NODE = "spec/10-requirements.md"
DAG = (("10-requirements.md", ()),)
CAND = ab.candidate_branch("ws-7", 1, 0, 1)
FINAL = ab.finalize_branch("ws-7", 1, 0, 1)
AT = "2026-09-10T08:00:00Z"
PSHA, FP = "e" * 40, "fp-1"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _text(status: str, by: str = "", at: str = "", body: str = "тело",
          pins: dict | None = None) -> str:
    pin_lines = "".join(f"  {k}: '{v}'\n" for k, v in (pins or {}).items())
    pins_block = f"upstream_hashes:\n{pin_lines}" if pin_lines else "upstream_hashes: {}\n"
    text = (f"---\nspec_stage: requirements\nstatus: {status}\nversion: 2\n"
            f"approved_by: '{by}'\napproved_at: '{at}'\n{pins_block}"
            f"approved_content_hash: ''\n---\n{body}\n")
    return text.replace("approved_content_hash: ''",
                        f"approved_content_hash: '{na.self_hash(text)}'")


def code(got: str) -> str:
    """Код проверки из ответа провайдера `not_approved:<код>:<деталь>`."""
    assert got.startswith("not_approved:"), got
    rest = got.removeprefix("not_approved:")
    return ":".join(rest.split(":")[:2]) if rest.startswith("legacy_admission:") \
        else rest.split(":")[0]


class World:
    """Настоящий git: candidate (approval_pending) → finalize (подпись) → база;
    леджер прогона с записью заявки и её пином старой политики."""

    def __init__(self, tmp: Path, *, final_body: str = "тело",
                 final_pins: dict | None = None) -> None:
        r = self.repo = tmp / "p"
        subprocess.run(["git", "init", "-q", "-b", "master", str(r)], check=True)
        _git(r, "config", "user.email", "t@e.st")
        _git(r, "config", "user.name", "t")
        (r / "spec").mkdir()
        (r / NODE).write_text(_text("draft"))
        _git(r, "add", "-A")
        _git(r, "commit", "-qm", "seed")
        self.seed = _git(r, "rev-parse", "HEAD")
        _git(r, "switch", "-qc", CAND)
        pending = _text(na.STATUS_APPROVAL_PENDING)
        (r / NODE).write_text(pending)
        _git(r, "commit", "-qam", "candidate")
        self.cand_head = _git(r, "rev-parse", "HEAD")
        _git(r, "switch", "-qc", FINAL)
        (r / NODE).write_text(_text("approved", "alice", AT, final_body, final_pins))
        _git(r, "commit", "-qam", "envelope")
        _git(r, "switch", "-q", "master")
        _git(r, "merge", "-q", "--no-ff", CAND, "-m", "Merge candidate")
        self.cand_merge = _git(r, "rev-parse", "HEAD")
        _git(r, "merge", "-q", "--no-ff", FINAL, "-m", "Merge finalize")
        self.signed = _git(r, "rev-parse", "HEAD")
        self.base_text = (r / NODE).read_text()
        self.final_branch = FINAL
        self.cand_files: tuple = ((NODE, "MODIFIED"),)
        self.cand_head_ref = self.cand_head
        self.candidate: dict | None = {"state": "MERGED", "mergedBy": {"login": "alice"},
                                       "mergedAt": AT}
        self.signed_by_pr: dict[str, int] = {self.signed: 6}
        self.ledger: dict = {"approve-1-0-1": {
            "candidate_pr": 5, "nodes": ["requirements"], "branch": CAND,
            "content_hashes": {"requirements": na.self_hash(pending)},
            "upstream_pins": {"requirements": {}},
            "policy": {"sha": PSHA, "fingerprint": FP},
        }}
        self.allowlist = frozenset({"alice"})
        self.policy: object = None  # иначе — исход policy_snapshot
        self.mode = "merge"  # режим продукта на T мержа candidate (merge — OLD_READER)


class Ops(RealOps):
    """git настоящий; форджа — PR по merge-коммиту, ветки и файлы PR, candidate."""

    def __init__(self, w: World) -> None:
        self.w = w

    def prs_containing_commit(self, slug, sha_):
        pr = self.w.signed_by_pr.get(sha_)
        return [] if pr is None else [{"number": pr, "state": "MERGED", "mergeCommit": sha_}]

    def brief_pr_fact(self, slug, pr):
        branch, files = ((self.w.final_branch, ((NODE, "MODIFIED"),)) if pr == 6
                         else (CAND, self.w.cand_files))
        return Fact(Outcome.FOUND, BriefPrFacts(pr, "MERGED", "master", branch, "h" * 40,
                                                files, None, None, None), "ok")

    def find_pr(self, slug, branch, *, any_state=False):
        return 5 if self.w.candidate is not None and branch == CAND else None

    def pr_facts(self, slug, pr):
        return {**(self.w.candidate or {}), "headRefOid": self.w.cand_head_ref,
                "mergeCommit": {"oid": self.w.cand_merge}}


@pytest.fixture()
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def judge(w: World, monkeypatch: pytest.MonkeyPatch, text: str | None = None) -> str:
    """Сквозной вызов провайдера по узлу без события `review/v1`."""
    snapshot = af.PolicySnapshot("owner/approval-policy", "main", "ALLOWLIST", PSHA,
                                 w.allowlist, FP)
    monkeypatch.setattr(af, "policy_snapshot", lambda ops, pinned_sha: w.policy or Fact(
        Outcome.FOUND, snapshot, "ok"))
    ctx = context(modes={sha("p1"): w.mode}, request_act=None)
    _wire(monkeypatch, ctx)
    state = SimpleNamespace(repo_slug=PRODUCT, ws_id="ws-7", target_dir=str(w.repo),
                            bundle_dir="spec", brief=None, ops=w.ledger, run_id="r",
                            base_ref="master")
    deps = act_status.Deps(readers=SimpleNamespace(anon=object()), binding=object(),
                           journal=None, creds=(), gh=object(), now=None, sleep=None,
                           settings=None)
    ops = Ops(w)
    decider = act_status.Decider(state, ops, deps, DAG, trust=_trust(ctx), journal=None)
    probe = act_status.LegacyProbe(state, ops, "master", decider.legacy, DAG)
    return probe(NODE, w.base_text if text is None else text)


def test_proven_legacy_act_before_activation_is_approved(world, monkeypatch) -> None:
    assert judge(world, monkeypatch) == "approved"


# --- provenance (LegacyProbe.__call__ / _finalize_branch / _candidate) ---------------


def test_no_edit_of_node_in_history_refused(world, monkeypatch) -> None:
    """B3-3: файла узла нет в истории базы (подпись не из базы) — `__call__`."""
    state = SimpleNamespace(repo_slug=PRODUCT, ws_id="ws-7", target_dir=str(world.repo))
    probe = act_status.LegacyProbe(state, Ops(world), "master", lambda pr, c: c(), DAG)
    assert code(probe("spec/99-absent.md", world.base_text)) == act_status.NO_EDIT


def test_candidate_identity_round_trips_ssot_form() -> None:
    """B3-2: форма candidate — только круговая подстановка в SSOT-шаблон."""
    assert act_status.candidate_identity(CAND) == ("ws-7", "approve-1-0-1")
    assert act_status.candidate_identity(FINAL) is None
    assert act_status.candidate_identity("spec/ws-7-approve-01-0-1") is None
    assert act_status.candidate_identity("feat/ws-7-approve-1-0-1") is None


def test_frontmatter_signature_differs_from_last_edit(world, monkeypatch) -> None:
    """Подпись frontmatter ≠ записи последней правки — отказ `__call__`, не позже."""
    text = world.base_text.replace("approved_by: 'alice'", "approved_by: 'bob'")
    assert code(judge(world, monkeypatch, text)) == act_status.NOT_SIGNATURE


def test_signature_not_through_pr_refused(world, monkeypatch) -> None:
    world.signed_by_pr = {}
    assert code(judge(world, monkeypatch)) == act_status.NOT_VIA_PR


def test_signature_without_finalize_pr_refused(world, monkeypatch) -> None:
    world.final_branch = "feat/forged"
    assert code(judge(world, monkeypatch)) == act_status.NOT_FINALIZE


def test_finalize_of_other_workstream_refused(world, monkeypatch) -> None:
    """B2-1: finalize/candidate чужого воркстрима — не акт этого узла."""
    world.final_branch = ab.finalize_branch("ws-8", 1, 0, 1)
    assert code(judge(world, monkeypatch)) == act_status.FOREIGN_WS


@pytest.mark.parametrize("candidate,expected", [
    (None, act_status.NO_CANDIDATE),
    ({"state": "CLOSED", "mergedBy": {"login": "alice"}, "mergedAt": AT},
     act_status.CANDIDATE_NOT_MERGED),
    ({"state": "MERGED", "mergedBy": {"login": "bob"}, "mergedAt": AT},
     act_status.CANDIDATE_MERGER),
    ({"state": "MERGED", "mergedBy": {"login": "alice"}, "mergedAt": "2026-09-10T09:00:00Z"},
     act_status.CANDIDATE_TIME),
], ids=["absent", "not_merged", "other_merger", "other_time"])
def test_candidate_absent_or_not_matching_signature(world, monkeypatch, candidate,
                                                    expected) -> None:
    world.candidate = candidate
    assert code(judge(world, monkeypatch)) == expected


def test_node_not_in_candidate_refused(world, monkeypatch) -> None:
    """B2-1: узел не среди файлов candidate — candidate его не выносил."""
    world.cand_files = (("spec/20-design.md", "MODIFIED"),)
    assert code(judge(world, monkeypatch)) == act_status.NOT_IN_CANDIDATE


@pytest.mark.parametrize("case,expected", [
    ("absent", act_status.NO_LEDGER),
    ("other_candidate", act_status.LEDGER_CANDIDATE),
    ("node_absent", act_status.LEDGER_NODE),
], ids=["absent", "other_candidate", "node_absent"])
def test_ledger_record_binds_candidate_and_node(world, monkeypatch, case, expected) -> None:
    """B3-1: пин старой политики — только из записи ЭТОЙ заявки леджера прогона."""
    op = world.ledger["approve-1-0-1"]
    if case == "absent":
        world.ledger.clear()
    elif case == "other_candidate":
        op["candidate_pr"] = 9
    else:
        op["nodes"] = ["design"]
    assert code(judge(world, monkeypatch)) == expected


# --- байтовая часть старого контракта (legacy_contract_refusal) ----------------------


def test_node_not_pending_in_candidate_refused(world, monkeypatch) -> None:
    """B2-1: в голове candidate узел не `approval_pending` — не было заявки на него."""
    world.cand_head_ref = world.seed
    assert code(judge(world, monkeypatch)) == act_status.NOT_PENDING


def test_node_bytes_changed_by_finalize_refused(tmp_path, monkeypatch) -> None:
    """B2-1: подписаны не те байты, что выносились (правка тела в finalize)."""
    assert code(judge(World(tmp_path, final_body="другое тело"), monkeypatch)) == \
        act_status.BYTES


def test_hash_field_not_matching_base_bytes_refused(world, monkeypatch) -> None:
    text = world.base_text.replace("approved_content_hash: '", "approved_content_hash: 'x")
    w = world
    (w.repo / NODE).write_text(text)
    _git(w.repo, "commit", "-qam", "tamper hash field")  # правка = новая подпись?
    w.signed_by_pr = {_git(w.repo, "rev-parse", "HEAD"): 6}
    assert code(judge(w, monkeypatch, text)) == act_status.HASH_FIELD


def test_signature_under_changed_pins_refused(tmp_path, monkeypatch) -> None:
    """B2-1: подпись под другими пинами, чем выносились."""
    w = World(tmp_path, final_pins={"charter": "d" * 40})
    assert code(judge(w, monkeypatch)) == act_status.PINS


# --- старый код авторизации (approve_node._legacy_outcome), B3-1 -------------------


def test_merger_outside_old_allowlist_refused(world, monkeypatch) -> None:
    """Подпись согласована, но мержер не в закреплённой версии allowlist — отказ."""
    world.allowlist = frozenset({"bob"})
    assert code(judge(world, monkeypatch)) == "legacy_signature"


def test_old_pin_missing_refused(world, monkeypatch) -> None:
    world.ledger["approve-1-0-1"].pop("policy")
    assert code(judge(world, monkeypatch)) == "legacy_unpinned"


def test_old_pin_substituted_is_unavailable(world, monkeypatch) -> None:
    """Прочитан не тот снимок, что закрепляла заявка (отпечаток) — не установлено."""
    world.ledger["approve-1-0-1"]["policy"]["fingerprint"] = "fp-other"
    assert judge(world, monkeypatch).startswith("unavailable:политика")


def test_old_pin_unavailable_is_unavailable(world, monkeypatch) -> None:
    world.policy = Fact(Outcome.UNAVAILABLE, None, "approval-policy: 502")
    assert judge(world, monkeypatch).startswith("unavailable:политика подписи")


def test_old_pin_superseded_is_policy_changed(world, monkeypatch) -> None:
    refusal = af.PolicyRefusal(af.POLICY_REFUSAL_SUPERSEDED, "версия сменилась")
    world.policy = Fact(Outcome.FORBIDDEN, refusal, "superseded")
    assert code(judge(world, monkeypatch)) == "policy_changed"


def test_snapshot_not_published_in_candidate_refused(world, monkeypatch) -> None:
    """Старый код: в голове candidate узел не в форме снимка заявки (хеш леджера)."""
    world.ledger["approve-1-0-1"]["content_hashes"]["requirements"] = "sha:other"
    assert code(judge(world, monkeypatch)) == "legacy_unpublished"


# --- admission и сам Decider.legacy ---------------------------------------------------


def test_candidate_delivered_after_activation_refused(world, monkeypatch) -> None:
    """Заявка без манифеста, доставленная после ACT_MODE=review, — отказ admission."""
    world.mode = "review"
    assert code(judge(world, monkeypatch)).startswith("legacy_admission:")


def test_decider_legacy_returns_contract_result(monkeypatch) -> None:
    """B2-1: `Decider.legacy` отдаёт результат старой проверки, не безусловный
    `LegacyAccepted` (настоящий `delivery_verdict`, OLD_READER)."""
    ctx = context(modes={sha("p1"): "merge"}, request_act=None)
    _wire(monkeypatch, ctx)
    state = SimpleNamespace(repo_slug=PRODUCT, ws_id="ws-7", target_dir="/t",
                            bundle_dir="spec")
    deps = act_status.Deps(readers=SimpleNamespace(anon=object()), binding=object(),
                           journal=None, creds=(), gh=object(), now=None, sleep=None,
                           settings=None)
    decider = act_status.Decider(state, object(), deps, (), trust=_trust(ctx), journal=None)
    refused = Rejected(act_status.PINS, "пины")
    assert decider.legacy(5, lambda: refused) is refused
    assert decider.legacy(5, lambda: LegacyAccepted("alice")) == LegacyAccepted("alice")
```

`tests/test_act_gate.py`:

```python
"""Режим gate-check S4/S8 и провайдер статуса узла (О4, О14, §12 п. 2–3; Р-10; b1)."""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("steward")

from governance import act_status, act_trust  # noqa: E402
from governance.act_time import Unavailable  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class _State:
    repo_slug = "owner/p"
    ws_id = "ws-7"
    target_dir = "/t"
    bundle_dir = "spec"
    run_id = "r"
    base_ref = "master"


def _deps(binding) -> act_status.Deps:
    # журнал отвечает отказом с собственной причиной: отключённая проверка выше него
    # даёт ДРУГУЮ причину, а не исключение (b4, мутационная изоляция)
    return act_status.Deps(readers=None, binding=binding,
                           journal=lambda readers, b: Unavailable("журнал стенда"), creds=(),
                           gh=None, now=None, sleep=None, settings=None)


def _live(mode: str) -> act_status.LivePolicy:
    return act_status.LivePolicy(mode, "trust", "j", None, None)


def _real_probe(monkeypatch) -> None:
    """Снять подмену `tests/conftest.py`: проверяется настоящий `live_policy`."""
    monkeypatch.setattr(act_status, "mode_probe",
                        lambda state, deps=None: act_status.live_policy(state, deps))


def test_no_binding_is_unavailable_not_legacy(tmp_path: Path, monkeypatch) -> None:
    """B1-5: потеря привязки — не «до миграции»: стоп, файл провайдера не пишется."""
    _real_probe(monkeypatch)
    got = act_status.gate_run(_State(), object(), (), candidate=True,
                              out=tmp_path / "ns.json", deps=_deps(None))
    assert isinstance(got, Unavailable) and not (tmp_path / "ns.json").exists()
    assert got.reason.startswith("нет привязки корня доверия")


def test_unreadable_binding_stops(tmp_path: Path, monkeypatch) -> None:
    _real_probe(monkeypatch)
    got = act_status.gate_run(_State(), object(), (), candidate=True,
                              out=tmp_path / "ns.json", deps=_deps(Unavailable("perm")))
    assert isinstance(got, Unavailable)


@pytest.mark.parametrize("mode", [act_trust.PRE_MIGRATION, "merge"])
def test_pre_migration_and_merge_are_legacy(tmp_path: Path, monkeypatch, mode: str) -> None:
    monkeypatch.setattr(act_status, "mode_probe", lambda state, deps=None: _live(mode))
    class _Ops:  # если проверка режима отключена — дайджест «не прочитан», не исключение
        def gate_snapshot_digest(self, target_dir, bundle_dir, *, candidate):
            return 1, "no digest"

    got = act_status.gate_run(_State(), _Ops(), (), candidate=False,
                              out=tmp_path / "ns.json", deps=_deps(object()))
    assert got == ("legacy", None)


def test_snapshot_digest_failure_stops(tmp_path: Path, monkeypatch) -> None:
    """b4: `gate-check --print-snapshot-digest` упал — стоп шага, провайдер не пишется."""
    monkeypatch.setattr(act_status, "mode_probe", lambda state, deps=None: _live("review"))

    class _Ops:
        def gate_snapshot_digest(self, target_dir, bundle_dir, *, candidate):
            return 2, "boom"

    got = act_status.gate_run(_State(), _Ops(), (), candidate=True,
                              out=tmp_path / "ns.json", deps=_deps(object()))
    assert isinstance(got, Unavailable) and not (tmp_path / "ns.json").exists()
    assert got.reason == "gate-check --print-snapshot-digest: код 2: boom"


def test_mode_unavailable_stops(tmp_path: Path, monkeypatch) -> None:
    """Удалённая запись / откат review → merge — стоп шага, не legacy (B1-5)."""
    monkeypatch.setattr(act_status, "mode_probe",
                        lambda state, deps=None: Unavailable("обратное переключение"))
    got = act_status.gate_run(_State(), object(), (), candidate=True,
                              out=tmp_path / "ns.json", deps=_deps(object()))
    assert isinstance(got, Unavailable)


def test_review_mode_writes_provider_bound_to_digest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(act_status, "mode_probe", lambda state, deps=None: _live("review"))
    monkeypatch.setattr(act_status, "_gate_statuses",
                        lambda state, ops, dag, deps, live: {"requirements": "approved"})

    class _Ops:
        def gate_snapshot_digest(self, target_dir, bundle_dir, *, candidate):
            assert candidate is True
            return 0, "sha256:" + "a" * 64

    out = tmp_path / "ns.json"
    got = act_status.gate_run(_State(), _Ops(), (), candidate=True, out=out,
                              deps=_deps(object()))
    assert got == ("review", str(out))
    assert json.loads(out.read_text()) == {
        "snapshot_digest": "sha256:" + "a" * 64, "nodes": {"requirements": "approved"}}


def test_cli_accepts_provider_file_and_reports_not_approved(tmp_path: Path) -> None:
    """Живой CLI пиненого steward: файл провайдера нашей формы принят; неодобренный
    узел — GC-ACT-NOT-APPROVED, без провайдера — GC-ACT-PROVIDER-MISSING."""
    from tests.governance_fixtures.bundles import make_bundle, make_profile

    exe = ROOT / ".venv" / "bin" / "gate-check"
    if not exe.exists():
        pytest.skip("gate-check не установлен в .venv")
    profile = make_profile(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=True)
    digest = subprocess.run([str(exe), "--print-snapshot-digest", "--candidate", str(bundle)],
                            capture_output=True, text=True, check=True).stdout.strip()
    ns = tmp_path / "ns.json"
    ns.write_text(json.dumps({"snapshot_digest": digest,
                              "nodes": {"requirements": "not_approved:нет конверта"}}))
    run = [str(exe), "--candidate", str(bundle), "--profile", str(profile), "--act-mode",
           "review"]
    with_provider = subprocess.run([*run, "--node-status", str(ns)],
                                   capture_output=True, text=True, check=False)
    assert with_provider.returncode == 1
    assert "GC-ACT-NOT-APPROVED" in with_provider.stdout + with_provider.stderr
    without = subprocess.run(run, capture_output=True, text=True, check=False)
    assert "GC-ACT-PROVIDER-MISSING" in without.stdout + without.stderr


NEW_OPS = {"first_parent_changes", "pr_body_fact", "gate_snapshot_digest", "snapshot_open",
           "snapshot_reader", "snapshot_close"}
HISTORY = {"statuses_at_base", "load_history", "Decider"}
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _called(node: ast.AST) -> str:
    func = node.func if isinstance(node, ast.Call) else None
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _calls_here(stmt: ast.stmt) -> list[ast.Call]:
    """Вызовы в выражениях самого оператора — без вложенных функций и лямбд."""
    found: list[ast.Call] = []
    stack: list[ast.AST] = [stmt]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Call):
            found.append(node)
        stack.extend(c for c in ast.iter_child_nodes(node) if not isinstance(c, _SCOPES))
    return found


def _probe_line(fn: ast.FunctionDef) -> int | None:
    """Строка БЕЗУСЛОВНОГО `mode_probe`: оператор верхнего уровня тела функции
    (присваивание или выражение), а не ветка `if`/цикла/`try`/лямбды."""
    for stmt in fn.body:
        if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.Expr, ast.Return)) and any(
                _called(c) == "mode_probe" for c in _calls_here(stmt)):
            return stmt.lineno
    return None


def _surface_offenders(root: Path) -> list[str]:
    """Новые методы `Ops` части B — только из `governance/act_*`; чтение истории вне
    `act_*` — только ПОСЛЕ безусловного `mode_probe` той же функции."""
    offenders: list[str] = []
    for path in root.glob("governance/**/*.py"):
        if path.name.startswith("act_"):
            continue
        rel = path.relative_to(root)
        for fn in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(fn, ast.FunctionDef):
                continue
            calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)]
            new = sorted({_called(c) for c in calls} & NEW_OPS)
            if new:
                offenders.append(f"{rel}:{fn.lineno} {fn.name}: {new}")
            history = [c.lineno for c in calls if _called(c) in HISTORY]
            if not history:
                continue
            probe = _probe_line(fn)
            if probe is None or min(history) <= probe:
                offenders.append(f"{rel}:{fn.lineno} {fn.name}: история не после "
                                 "безусловного mode_probe")
    return offenders


def test_new_ops_surface_reached_only_behind_mode_probe() -> None:
    """B1-8 (класс B1-7): стабы `Ops` старых тестов не знают новых методов — значит,
    их не должен звать ни один путь, который тесты вне `act_*` проходят с
    `tests/conftest.py` (`mode_probe` → `PRE_MIGRATION`). Новый вызов вне `act_*`
    или чтение истории в обход `mode_probe` падает здесь, а не `AttributeError` в
    чужом тесте."""
    assert _surface_offenders(ROOT) == []
```

Дополнение `tests/test_governance_runner.py` (тот же стенд, что `test_gate_red_stops`):

```python
def test_s8_stops_when_act_mode_unset(tmp_path: Path, runs_root, monkeypatch, capsys) -> None:
    """b5 (`runner._step_s8#act_mode_unset`): режим акта не установлен на S8 — шаг
    останавливается (resumable), authoritative-гейт не зовётся."""
    from governance import act_status, runner
    from governance.act_time import Unavailable

    ops = FakeOps()
    state = locked_runner.start(**_start_kwargs(tmp_path, "r-act-s8", ops))
    monkeypatch.setattr(act_status, "gate_run",
                        lambda *a, **k: Unavailable("df-trust: HTTP 403"))
    before = len(ops.calls)
    assert runner._step_s8(state, ops) is False
    assert "S8: режим акта не установлен — df-trust: HTTP 403" in capsys.readouterr().out
    assert not any(c[0] == "gate_check_s8" for c in ops.calls[before:])


def test_s4_stops_when_act_mode_unset(tmp_path: Path, runs_root, monkeypatch) -> None:
    """B1-5/B1-8: режим акта не установлен — стоп S4 с причиной, гейт steward не зовётся."""
    from governance import act_status
    from governance.act_time import Unavailable

    monkeypatch.setattr(act_status, "gate_run",
                        lambda *a, **k: Unavailable("df-trust: HTTP 403"))
    ops = FakeOps()
    state = locked_runner.start(**_start_kwargs(tmp_path, "r-act-gate", ops))
    assert state.status == "stopped_gate"
    findings = (rs.run_dir("r-act-gate") / "gate-findings.txt").read_text(encoding="utf-8")
    assert "GC-ACT-UNAVAILABLE(devtools)" in findings and "HTTP 403" in findings
    assert ops.gate_modes == []


FRESH = "f" * 40  # SHA свежего снимка базы продукта (`Ops.snapshot_open`)


def _review_admission(monkeypatch, ops, remote: dict[str, str], *, fetched: bool = True,
                      local: dict[str, str] | None = None) -> None:
    """Продукт в `review`. Статусы зависят от снимка базы: свежий SHA — `remote`, локальная
    ветка — `local` (по умолчанию «approved»: устаревшая база остановки не видит)."""
    from governance import act_status
    from governance.facts import Fact, Outcome, unavailable
    from governance.ops import SnapshotRepo

    live = SimpleNamespace(mode="review")
    monkeypatch.setattr(act_status, "mode_probe", lambda state, deps=None: live)
    snapshot = (Fact(Outcome.FOUND, SnapshotRepo("/нет", FRESH, "u"), "ok") if fetched
                else unavailable("fetch: код 128"))
    monkeypatch.setattr(ops, "snapshot_open", lambda target_dir, slug, branch: snapshot,
                        raising=False)
    monkeypatch.setattr(ops, "snapshot_reader", lambda repo: ops, raising=False)
    monkeypatch.setattr(ops, "snapshot_close", lambda repo: None, raising=False)
    stale = local if local is not None else {node: "approved" for node in remote}

    def at_base(state, ops_, dag, live_, deps=None, *, ref=None):
        verdicts = remote if ref == FRESH else stale
        return SimpleNamespace(is_review=lambda node: node in verdicts,
                               of=lambda node: verdicts[node])

    monkeypatch.setattr(act_status, "statuses_at_base", at_base)


def _wave_state(tmp_path: Path, run_id: str, wave: int) -> tuple[rs.RunState, FakeOps]:
    ops = FakeOps(s8_exit=0)
    state = _wave_run_at_s8(tmp_path, run_id, ops)
    state.wave = wave
    return state, ops


def _gate_rerun(*_args, **_kwargs):
    raise RuntimeError("гейт S4 заново")


def _checkout_fails(*_args):
    raise RuntimeError("чекаут базы: гейт заново")


ADMISSION_LOST = ["not_approved:stopped:g1", "unavailable:df-trust: HTTP 403"]


@pytest.mark.parametrize("verdict", ADMISSION_LOST, ids=["stop_delivered", "admission_unavailable"])
def test_s4_completed_gate_rerun_without_admission(tmp_path, runs_root, monkeypatch, capsys,
                                                   verdict: str) -> None:
    """Решение владельца 01.11 (d1, D1-2): повторный вход в S4 с сохранённым зелёным гейтом —
    после доставленной остановки узла нижнего уровня или при недоступном допуске гейт
    исполняется заново, а не пропускается."""
    from governance import runner

    state, ops = _wave_state(tmp_path, "r-adm-s4", wave=2)
    state.ops[runner.wave_key(state, "gate-candidate")] = {"status": "completed"}
    _review_admission(monkeypatch, ops, {"charter": verdict})
    monkeypatch.setattr(runner, "wave_profile_dir", _gate_rerun)
    with pytest.raises(RuntimeError, match="гейт S4 заново"):
        runner._step_gate(state, ops)
    assert f"допуск review/v1 не подтверждён (узел charter: {verdict})" in capsys.readouterr().out


def test_s4_completed_gate_kept_with_admission(tmp_path, runs_root, monkeypatch) -> None:
    """d1: допуск подтверждён — сохранённый S4 пропускается, как раньше."""
    from governance import runner

    state, ops = _wave_state(tmp_path, "r-adm-s4-ok", wave=2)
    state.ops[runner.wave_key(state, "gate-candidate")] = {"status": "completed"}
    _review_admission(monkeypatch, ops, {"charter": "approved"})
    monkeypatch.setattr(runner, "wave_profile_dir", _gate_rerun)
    assert runner._step_gate(state, ops) is True


@pytest.mark.parametrize("verdict", ADMISSION_LOST, ids=["stop_delivered", "admission_unavailable"])
def test_s8_completed_gate_rerun_without_admission(tmp_path, runs_root, monkeypatch, capsys,
                                                   verdict: str) -> None:
    """d1 (D1-2): повторный вход в S8 с сохранённым `exit=0` — после доставленной остановки
    или при недоступном допуске гейт исполняется заново (здесь — до чекаута базы)."""
    from governance import runner

    state, ops = _wave_state(tmp_path, "r-adm-s8", wave=5)
    state.ops["gate-authoritative"] = {"status": "completed", "exit": 0, "output": ""}
    _review_admission(monkeypatch, ops, {"design": verdict})
    monkeypatch.setattr(ops, "checkout_and_pull", _checkout_fails)
    assert runner._step_s8(state, ops) is False
    assert state.ops["gate-authoritative"]["status"] == "started"
    assert f"допуск review/v1 не подтверждён (узел design: {verdict})" in capsys.readouterr().out


def test_s8_completed_gate_kept_with_admission(tmp_path, runs_root, monkeypatch) -> None:
    """d1: допуск подтверждён — сохранённый `exit=0` завершает S8, как раньше."""
    from governance import runner

    state, ops = _wave_state(tmp_path, "r-adm-s8-ok", wave=5)
    state.ops["gate-authoritative"] = {"status": "completed", "exit": 0, "output": ""}
    _review_admission(monkeypatch, ops, {"design": "approved"})
    assert runner._step_s8(state, ops) is True


@pytest.mark.parametrize("verdict", ADMISSION_LOST, ids=["stop_delivered", "admission_unavailable"])
def test_next_wave_needs_admission(tmp_path, runs_root, monkeypatch, verdict: str) -> None:
    """d1: переход волны по завершённой заявке — только при актуальном допуске узлов волны;
    иначе `stopped_review`, волна та же."""
    from governance import runner

    state, ops = _wave_state(tmp_path, "r-adm-next", wave=1)
    _review_admission(monkeypatch, ops, {"charter": verdict})
    monkeypatch.setattr(runner, "advance", lambda state, ops, *, lock: state)
    got = runner._next_wave(state, ops, lock=None)
    assert (got.status, got.wave) == ("stopped_review", 1)


def _site_s4(state, ops, monkeypatch) -> str:
    from governance import runner

    state.ops[runner.wave_key(state, "gate-candidate")] = {"status": "completed"}
    monkeypatch.setattr(runner, "wave_profile_dir", _gate_rerun)
    try:
        runner._step_gate(state, ops)
    except RuntimeError:
        return "no_success"
    return "success"


def _site_s8(state, ops, monkeypatch) -> str:
    from governance import runner

    state.ops["gate-authoritative"] = {"status": "completed", "exit": 0, "output": ""}
    monkeypatch.setattr(ops, "checkout_and_pull", _checkout_fails)
    return "success" if runner._step_s8(state, ops) else "no_success"


def _site_next_wave(state, ops, monkeypatch) -> str:
    from governance import runner

    monkeypatch.setattr(runner, "advance", lambda state, ops, *, lock: state)
    got = runner._next_wave(state, ops, lock=None)
    return "no_success" if got.status == "stopped_review" else "success"


#: Сайт → (исход, волна, узел, допуск которого спрашивается).
ADMISSION_SITES = {"s4": (_site_s4, 2, "charter"), "s8": (_site_s8, 5, "design"),
                   "next_wave": (_site_next_wave, 1, "charter")}


@pytest.mark.parametrize("site", sorted(ADMISSION_SITES))
def test_remote_stop_with_stale_local_base_no_success(tmp_path, runs_root, monkeypatch,
                                                      site: str) -> None:
    """d2 (D2-1): локальная база устарела («approved»), на origin остановка уже доставлена —
    проверка читает свежий снимок, успеха нет."""
    outcome, wave, node = ADMISSION_SITES[site]
    state, ops = _wave_state(tmp_path, f"r-adm-stale-{site}", wave=wave)
    _review_admission(monkeypatch, ops, {node: "not_approved:stopped:g1"})
    assert outcome(state, ops, monkeypatch) == "no_success"


@pytest.mark.parametrize("site", sorted(ADMISSION_SITES))
def test_admission_refused_when_base_not_refreshed(tmp_path, runs_root, monkeypatch,
                                                   site: str) -> None:
    """d2 (D2-1): свежий снимок базы не получен (`fetch` не удался) — успеха нет, хотя
    локальная база говорит «approved»."""
    outcome, wave, node = ADMISSION_SITES[site]
    state, ops = _wave_state(tmp_path, f"r-adm-fetch-{site}", wave=wave)
    _review_admission(monkeypatch, ops, {node: "approved"}, fetched=False)
    assert outcome(state, ops, monkeypatch) == "no_success"


def test_s8_lost_admission_run_not_completed_when_checkout_fails(tmp_path, runs_root,
                                                                 monkeypatch) -> None:
    """d2 (D2-2): прогон `completed`, сохранённый S8 `exit=0`, допуск утрачен; чекаут базы
    после обнаружения падает — прогон не остаётся `completed` (статус сохранён до чекаута)."""
    state, ops = _wave_state(tmp_path, "r-adm-s8-status", wave=5)
    state.status = "completed"
    state.ops["gate-authoritative"] = {"status": "completed", "exit": 0, "output": ""}
    rs.save(state)
    _review_admission(monkeypatch, ops, {"design": "not_approved:stopped:g1"})
    monkeypatch.setattr(ops, "checkout_and_pull", _checkout_fails)
    got = locked_runner.advance(state, ops)
    assert got.status == "running"
    assert rs.load("r-adm-s8-status").status == "running"


@pytest.mark.parametrize("site", sorted(ADMISSION_SITES))
def test_stale_clone_remote_stop_end_to_end(tmp_path, runs_root, monkeypatch, site: str) -> None:
    """d3 (D3-1): настоящий git — в продукте доставлена остановка `charter`, локальный клон
    устарел; `statuses_at_base` и чтения не подменены — S4, S8, `_next_wave` успеха не дают."""
    from tests.test_act_status import WS, clone_state, remote_stop_world, review_live

    outcome, wave, _node = ADMISSION_SITES[site]
    _origin, clone, git_ops = remote_stop_world(tmp_path / "git", node="charter",
                                                fname="00-charter.md")
    state, _fake = _wave_state(tmp_path, f"r-adm-e2e-{site}", wave=wave)
    for name, value in vars(clone_state(clone, ws_id=WS)).items():
        setattr(state, name, value)
    rs.save(state)
    review_live(monkeypatch)
    assert outcome(state, git_ops, monkeypatch) == "no_success"
```

Дополнение `tests/test_governance_task_bridge.py` (В-1, решение владельца 01.11):

```python
def _canon_bundle(root: Path, rel: str) -> tuple:
    dag = task_bridge._dag_for(None)
    bundle = root / rel
    bundle.mkdir(parents=True)
    for fname, _ in dag:
        (bundle / fname).write_text(f"---\nstatus: approved\n---\n{fname}\n")
    return dag


@pytest.mark.parametrize("rel", ["workstreams/ws-7/spec", "spec"])
def test_canonical_hash_sees_new_generation_and_stops(tmp_path: Path, rel: str) -> None:
    """B1-6/B2-2: новый манифест (поколение с теми же байтами узла и прежним конвертом)
    и запись остановки меняют хеш при ЛЮБОМ `bundle_dir` — каталог `approvals/` берётся
    из идентичности воркстрима (путь производителя), не из `bundle_dir`; пустой или
    отсутствующий `approvals/` — прежнее значение."""
    dag = _canon_bundle(tmp_path, rel)

    def canon() -> str:
        return task_bridge._canonical_dag_hash(str(tmp_path), rel, dag, ws_id="ws-7")

    before = canon()
    assert before == task_bridge._canonical_dag_hash(str(tmp_path), rel, dag)
    approvals = tmp_path / "workstreams/ws-7/approvals"
    approvals.mkdir(parents=True, exist_ok=True)
    assert canon() == before
    (approvals / "requirements.yaml").write_text("envelope: r1\n")
    with_envelope = canon()
    (approvals / "requests").mkdir()
    (approvals / "requests" / "approve-3-1-1.yaml").write_text("act: review/v1\n")
    with_new_generation = canon()
    assert len({before, with_envelope, with_new_generation}) == 3
    (approvals / "stops").mkdir()
    (approvals / "stops" / "requirements-g1.yaml").write_text("stop\n")
    assert canon() != with_new_generation


def test_canonical_hash_ignores_other_workstream_approvals(tmp_path: Path) -> None:
    """B2-2: `approvals/` чужого воркстрима и корня репо в хеш этого не входят."""
    rel = "spec"
    dag = _canon_bundle(tmp_path, rel)
    before = task_bridge._canonical_dag_hash(str(tmp_path), rel, dag, ws_id="ws-7")
    for other in ("workstreams/ws-8/approvals/requests", "approvals/requests"):
        (tmp_path / other).mkdir(parents=True)
        (tmp_path / other / "approve-1-0-1.yaml").write_text("act: review/v1\n")
    assert task_bridge._canonical_dag_hash(str(tmp_path), rel, dag, ws_id="ws-7") == before


def test_canonical_hash_with_approvals_is_a_new_epoch(tmp_path: Path) -> None:
    """B2-2: величина с байтами `approvals/` несопоставима с величиной без них
    (`v3` против `v2`): `_delivered_content_anchor`, не видящий `approvals/` head,
    даёт `comparison: unavailable`, а не ложное «содержание то же»."""
    rel = "workstreams/ws-7/spec"
    dag = _canon_bundle(tmp_path, rel)
    plain = task_bridge._canonical_dag_hash(str(tmp_path), rel, dag)
    (tmp_path / "workstreams/ws-7/approvals").mkdir()
    (tmp_path / "workstreams/ws-7/approvals/requirements.yaml").write_text("e\n")
    acted = task_bridge._canonical_dag_hash(str(tmp_path), rel, dag, ws_id="ws-7")
    assert plain.startswith("v2:") and acted.startswith("v3:")
    assert not task_bridge._comparable_anchors(plain, acted)


def _review_mode(monkeypatch, verdicts: dict[str, str]) -> None:
    """Продукт в `review`; `statuses_at_base` отвечает заданными статусами узлов."""
    from contextlib import nullcontext
    from types import SimpleNamespace

    from governance import act_status

    live = SimpleNamespace(mode="review")
    monkeypatch.setattr(act_status, "mode_probe", lambda state, deps=None: live)

    class _Statuses:
        def is_review(self, node: str) -> bool:
            return node in verdicts

        def of(self, node: str) -> str:
            return verdicts[node]

    monkeypatch.setattr(act_status, "fresh_base",
                        lambda state, ops: nullcontext(("f" * 40, ops)))
    monkeypatch.setattr(act_status, "statuses_at_base",
                        lambda state, ops, dag, live, deps=None, *, ref=None: _Statuses())


def _equal_anchor_state(tmp_path: Path, monkeypatch):
    """Запись доставки с тем же `content_anchor`, что у текущего дерева (хеш не менялся)."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {
        "status": "completed", "pr": 5, "anchor": "БЛОБ-ДОСТАВКИ-v1",
        "content_anchor": tb._content_anchor(state.target_dir, state.bundle_dir, None),
    }
    rs.save(state)
    return state


def _gate_reached(*_args):
    raise RuntimeError("гейт §I12 вызван")


def test_review_noop_with_fresh_approval(tmp_path, monkeypatch) -> None:
    """В-1 (решение владельца 01.11): хеш не менялся и узел `review/v1` одобрен сейчас —
    бесследный no-op §I5 прежний."""
    from governance import task_bridge as tb

    _review_mode(monkeypatch, {"requirements": "approved"})
    state = _equal_anchor_state(tmp_path, monkeypatch)
    monkeypatch.setattr(tb, "_approved_dag_or_refuse", _gate_reached)
    assert tb.deliver_superseded(state, _ProvOps(prs=[_MERGED_PR])) == tb.SupersedeResult("noop")


@pytest.mark.parametrize("verdict", [
    "not_approved:stopped:g1",
    "not_approved:act:d1_no_act",
    "unavailable:df-trust: HTTP 403",
], ids=["stop_delivered", "act_revoked", "trust_unavailable"])
def test_review_noop_refused_without_fresh_approval(tmp_path, monkeypatch, capsys,
                                                    verdict: str) -> None:
    """В-1: хеш не менялся, но допуск узла `review/v1` отозван (остановка, ревью
    отозвано) или не проверяется (доверенный контекст недоступен) — no-op не объявляется,
    дальше гейт §I12 по прежним правилам."""
    from governance import task_bridge as tb

    _review_mode(monkeypatch, {"requirements": verdict})
    state = _equal_anchor_state(tmp_path, monkeypatch)
    monkeypatch.setattr(tb, "_approved_dag_or_refuse", _gate_reached)
    with pytest.raises(RuntimeError, match="гейт §I12 вызван"):
        tb.deliver_superseded(state, _ProvOps(prs=[_MERGED_PR]))
    out = capsys.readouterr().out
    assert f"бесследного no-op нет (узел requirements: {verdict})" in out


def test_review_noop_refused_when_mode_unset(tmp_path, monkeypatch, capsys) -> None:
    """В-1: режим акта не установлен (нет привязки) — no-op не объявляется."""
    from governance import act_status
    from governance import task_bridge as tb
    from governance.act_time import Unavailable

    monkeypatch.setattr(act_status, "mode_probe",
                        lambda state, deps=None: Unavailable("нет привязки"))
    state = _equal_anchor_state(tmp_path, monkeypatch)
    monkeypatch.setattr(tb, "_approved_dag_or_refuse", _gate_reached)
    with pytest.raises(RuntimeError, match="гейт §I12 вызван"):
        tb.deliver_superseded(state, _ProvOps(prs=[_MERGED_PR]))
    assert "бесследного no-op нет (режим акта продукта: нет привязки)" in capsys.readouterr().out


def test_noop_without_review_nodes_reads_no_statuses(tmp_path, monkeypatch) -> None:
    """В-1: продукт до миграции — правило §I5 прежнее, статусы не читаются (квота С-1)."""
    from governance import act_status
    from governance import task_bridge as tb

    monkeypatch.setattr(act_status, "statuses_at_base",
                        lambda *a, **k: pytest.fail("статусы для продукта до миграции"))
    state = _equal_anchor_state(tmp_path, monkeypatch)
    assert tb.deliver_superseded(state, _ProvOps(prs=[_MERGED_PR])) == tb.SupersedeResult("noop")


def _for_run_completed(tmp_path: Path, monkeypatch) -> int:
    """Доставка уже записана `completed` — повтор возвращает её PR."""
    state = _recon_state(tmp_path, monkeypatch, op={"status": "completed", "pr": 5})
    return task_bridge.deliver_for_run(state, _ReconOps())


#: Успешные выходы по сохранённому результату (d1): сценарий — существующий тест этого исхода.
_STORED_SUCCESS = {
    "for_run_completed": lambda tp, mp, cs: _for_run_completed(tp, mp),
    "for_run_adopted": lambda tp, mp, cs: test_deliver_for_run_adopts_existing_pr_by_branch(tp, mp),
    "returned_pr": lambda tp, mp, cs: test_supersede_returns_existing_pr_when_revision_completed(tp, mp),
    "returned_merged": lambda tp, mp, cs: test_supersede_completes_started_revision_with_merged_pr(tp, mp),
    "returned_open": lambda tp, mp, cs: test_supersede_resumes_started_revision_with_open_pr(tp, mp),
    "returned_v1": lambda tp, mp, cs: test_supersede_returns_open_pr_of_first_delivery(tp, mp),
    "v1_replacement": lambda tp, mp, cs: test_replace_v1_repeat_after_live_replacement_explains(
        "OPEN", tp, mp, cs),
    "redo_intent": lambda tp, mp, cs: test_supersede_resumes_started_revision_without_pr(tp, mp),
}


def test_for_run_completed_returns_pr_with_admission(tmp_path, monkeypatch) -> None:
    """d1: допуск подтверждён (продукт до миграции) — повтор возвращает записанный PR."""
    assert _for_run_completed(tmp_path, monkeypatch) == 5


@pytest.mark.parametrize("site", sorted(_STORED_SUCCESS))
def test_stored_success_requires_admission(tmp_path, monkeypatch, capsys, site: str) -> None:
    """Решение владельца 01.11 (d1): успех по сохранённому результату — возврат уже
    доставленного PR, повтор доставки из намерения — для продукта в `review` только при
    актуальном допуске узлов `review/v1`; иначе отказ, успеха нет."""
    from governance import act_status

    monkeypatch.setattr(act_status, "noop_refusal",
                        lambda *a, **k: "узел requirements: not_approved:stopped:g1")
    with pytest.raises(RuntimeError, match="допуск review/v1 не подтверждён — узел requirements"):
        _STORED_SUCCESS[site](tmp_path, monkeypatch, capsys)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group governance pytest tests/test_act_status.py tests/test_act_legacy.py tests/test_act_gate.py -v`
Expected: FAIL — `ImportError: cannot import name 'act_status' from 'governance'`.

- [ ] **Step 3: Add the git primitive and `config_home`**

`governance/ops.py` — протокол и `RealOps`:

```python
    def first_parent_changes(
        self, target_dir: str, ref: str
    ) -> list[tuple[str, tuple[str, ...], tuple[tuple[str, str], ...]]] | None: ...

    def snapshot_open(
        self, target_dir: str, repo_slug: str, branch: str
    ) -> Fact[SnapshotRepo]: ...

    def snapshot_reader(self, repo: SnapshotRepo) -> Ops: ...

    def snapshot_close(self, repo: SnapshotRepo) -> None: ...
```

Изолированный снимок базы продукта (ревью d3–d5): модульные `_REPO_SLUG`,
`_SNAPSHOT_ENV_KEEP`, `SnapshotRepo`, `SNAPSHOT_FORGE_METHODS`, `SnapshotOps` (deny-by-default),
`_SNAPSHOT_FETCH`, `_parse_first_parent`
(рядом с `_ISSUE_URL_RE`; в импорты — `import tempfile`, `import uuid`,
`from dataclasses import dataclass`). `origin` клона, его `config`, глобальная и системная
конфигурация git и `GIT_*` окружения в снимок не попадают; другой источник — только
переопределение `_snapshot_url` в тестовом наследнике:

```python
#: Форма `repo_slug` продукта: `owner/repo` по правилам имён GitHub — ничего, что git принял
#: бы за путь, схему или опцию.
_REPO_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/(?!\.\.?$)[A-Za-z0-9_.-]+")
#: Окружение git снимка базы — только эти переменные (ревью d5): ни одной `GIT_*` извне.
_SNAPSHOT_ENV_KEEP = ("PATH", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR")


@dataclass(frozen=True)
class SnapshotRepo:
    """Изолированный снимок базы продукта (ревью d5, d6): временный каталог — bare-репозиторий
    `repo.git` (`objects/info/alternates` → объекты продукта; в продукт ничего не пишется) и
    пустой `home` процессов git снимка; SHA ветки базы и канонический URL источника."""

    git_dir: str
    sha: str
    url: str


#: Методы базовых `Ops`, которые `SnapshotOps` пропускает: только форджа (сеть, `gh`), ни один
#: не читает локальный клон (ревью d5, deny-by-default). Любой другой метод — отказ.
SNAPSHOT_FORGE_METHODS = frozenset({
    "brief_pr_fact", "find_pr", "pr_facts", "prs_containing_commit", "repo_file_fact",
    "policy_version_fact",
})


class SnapshotOps:
    """`Ops` поверх снимка, deny-by-default: пять методов git — из временного репозитория
    снимка с изолированным окружением (`target_dir` вызова не используется), методы форджи
    из `SNAPSHOT_FORGE_METHODS` — базовых `Ops`; любой другой метод — `AttributeError`:
    новое чтение git внутри проверки допуска падает громко, а не читает клон."""

    def __init__(self, base: RealOps, repo: SnapshotRepo) -> None:
        self._base, self.repo = base, repo

    def __getattr__(self, name: str) -> object:
        if name in SNAPSHOT_FORGE_METHODS:
            return getattr(self._base, name)
        raise AttributeError(
            f"SnapshotOps: метод {name!r} не разрешён внутри проверки допуска "
            "(deny-by-default: git — только из снимка, иное — форджа из allowlist)"
        )

    def _git(self, *args: str, text: bool = True) -> subprocess.CompletedProcess:
        return self._base._snapshot_git(self.repo.git_dir, *args, text=text)

    def first_parent_changes(
        self, target_dir: str, ref: str
    ) -> list[tuple[str, tuple[str, ...], tuple[tuple[str, str], ...]]] | None:
        done = self._git("log", "--first-parent", "--reverse", "--diff-merges=first-parent",
                         "--no-renames", "--name-status", "--format=%x00%H %P", ref)
        return _parse_first_parent(done.stdout) if done.returncode == 0 else None

    def show_file(self, target_dir: str, ref: str, path: str) -> str | None:
        done = self._git("show", f"{ref}:{path}")
        return done.stdout if done.returncode == 0 else None

    def show_file_bytes(self, target_dir: str, ref: str, path: str) -> bytes | None:
        done = self._git("show", f"{ref}:{path}", text=False)
        return done.stdout if done.returncode == 0 else None

    def rev_parse(self, target_dir: str, ref: str) -> str | None:
        return self._git("rev-parse", "--verify", "--quiet", ref).stdout.strip() or None

    def fetch_branch(self, target_dir: str, branch: str) -> bool:
        """Догрузка ref'а (голова squash-PR `refs/pull/N/head`) — в снимок, тем же
        изолированным fetch из канонического источника."""
        ref = branch if branch.startswith("refs/") else f"refs/heads/{branch}"
        done = self._git(*_SNAPSHOT_FETCH, self.repo.url,
                         f"+{ref}:refs/snap/extra/{uuid.uuid4().hex}")
        return done.returncode == 0


#: Fetch снимка: без тегов, без `FETCH_HEAD`, без отображения `remote.*.fetch`.
_SNAPSHOT_FETCH = ("fetch", "--quiet", "--no-tags", "--no-write-fetch-head", "--refmap=")


def _parse_first_parent(
    stdout: str,
) -> list[tuple[str, tuple[str, ...], tuple[tuple[str, str], ...]]]:
    """Разбор `git log --first-parent --name-status --format=%x00%H %P`."""
    rows: list[tuple[str, tuple[str, ...], tuple[tuple[str, str], ...]]] = []
    for chunk in stdout.split("\x00")[1:]:
        lines = [ln for ln in chunk.splitlines() if ln.strip()]
        commit, *parents = lines[0].split()
        changes = tuple(
            (ln.split("\t", 1)[0], ln.split("\t", 1)[1]) for ln in lines[1:] if "\t" in ln
        )
        rows.append((commit, tuple(parents), changes))
    return rows
```

Методы `RealOps`:

```python
    def _snapshot_url(self, repo_slug: str) -> str:
        """Источник снимка базы продукта — канонический URL из `repo_slug` (ревью d4).

        `origin` клона не читается: он может указывать на fork или копию. Другой
        источник — только переопределение метода в тестовом наследнике (локальный стенд);
        ни конфигурация, ни окружение его не задают."""
        return f"https://github.com/{repo_slug}.git"

    def _credential_helper(self) -> str:
        """Единственный credential helper fetch снимка — `gh` профиля runner'а (d5)."""
        return "!gh auth git-credential"

    def _snapshot_env(self, home: Path) -> dict[str, str]:
        """Изолированное окружение git снимка (d5, d6): переменные — из allowlist,
        конфигурации system/global нет, интерактивных запросов нет; `HOME` и
        `XDG_CONFIG_HOME` — пустой каталог снимка (ни `~/.netrc`, ни `~/.gitconfig`, ни
        чужой `gh` настоящего HOME); `gh` — профиль runner'а (`REVIEW_GH_CONFIG_DIR`, как
        прочие вызовы `gh` агента), абсолютный путь разрешён ДО подмены HOME."""
        env = {k: os.environ[k] for k in _SNAPSHOT_ENV_KEEP if k in os.environ}
        profile = Path(os.environ.get("REVIEW_GH_CONFIG_DIR") or REVIEW_GH_CONFIG_DIR)
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="", SSH_ASKPASS="",
                   HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
                   GH_CONFIG_DIR=str(profile.expanduser().resolve()), GH_PROMPT_DISABLED="1")
        return env

    def _snapshot_git(self, git_dir: str, *args: str, text: bool = True,
                      stdin: str | None = None) -> subprocess.CompletedProcess:
        """git над снимком: `--git-dir` временного репозитория, изолированное окружение
        с пустым `HOME` снимка (соседний каталог `home`), credential helper сброшен и задан
        явно. Токен в argv не попадает (его отдаёт helper через stdin/stdout git)."""
        return subprocess.run(
            ["git", f"--git-dir={git_dir}", "-c", "credential.helper=",
             "-c", f"credential.helper={self._credential_helper()}", *args],
            capture_output=True, text=text, input=stdin, check=False,
            env=self._snapshot_env(Path(git_dir).parent / "home"),
        )

    def snapshot_open(
        self, target_dir: str, repo_slug: str, branch: str
    ) -> Fact[SnapshotRepo]:
        """Свежий изолированный снимок ветки `branch` продукта `repo_slug` (ревью d3–d5).

        Временный bare-репозиторий (свой `config` без `url.*`; объекты продукта — через
        `objects/info/alternates`, в продукт ничего не пишется); fetch из
        `_snapshot_url(repo_slug)` в изолированном окружении; SHA полученного объекта.
        Конфигурация репозитория продукта, глобальная и системная, `GIT_*` окружения на
        источник и fetch не влияют. Неудача — `Unavailable`, временный каталог удалён;
        URL и токен в диагностику не попадают."""
        if not _REPO_SLUG.fullmatch(repo_slug):
            return unavailable(f"repo_slug {repo_slug!r} — не owner/repo")
        root = tempfile.mkdtemp(prefix="act-snapshot-")
        git_dir, home = str(Path(root) / "repo.git"), Path(root) / "home"
        home.mkdir()
        try:
            common = subprocess.run(
                ["git", "-C", target_dir, "rev-parse", "--path-format=absolute",
                 "--git-common-dir"],
                capture_output=True, text=True, check=False, env=self._snapshot_env(home),
            )
            if common.returncode != 0 or not common.stdout.strip():
                return unavailable("репозиторий продукта не читается")
            init = self._snapshot_git(git_dir, "init", "--quiet", "--bare", "--template=")
            if init.returncode != 0:
                return unavailable(f"снимок не создан: код {init.returncode}")
            alternates = Path(git_dir) / "objects" / "info" / "alternates"
            alternates.write_text(f"{common.stdout.strip()}/objects\n", encoding="utf-8")
            url = self._snapshot_url(repo_slug)
            done = self._snapshot_git(git_dir, *_SNAPSHOT_FETCH, url,
                                      f"+refs/heads/{branch}:refs/snap/base")
            if done.returncode != 0:
                return unavailable(f"fetch {repo_slug} {branch}: код {done.returncode}")
            sha = self._snapshot_git(git_dir, "rev-parse", "--verify", "--quiet",
                                     "refs/snap/base^{commit}").stdout.strip()
            if not sha:
                return unavailable(f"снимок {repo_slug} {branch} не разрешается")
            repo, root = SnapshotRepo(git_dir, sha, url), ""
            return Fact(Outcome.FOUND, repo, "ok")
        finally:
            if root:
                shutil.rmtree(root, ignore_errors=True)

    def snapshot_reader(self, repo: SnapshotRepo) -> SnapshotOps:
        """`Ops`, читающие git только из снимка (история, байты, входы, головы PR)."""
        return SnapshotOps(self, repo)

    def snapshot_close(self, repo: SnapshotRepo) -> None:
        """Удалить временный каталог снимка (репозиторий и пустой `home`)."""
        shutil.rmtree(Path(repo.git_dir).parent, ignore_errors=True)
```

```python
    def first_parent_changes(
        self, target_dir: str, ref: str
    ) -> list[tuple[str, tuple[str, ...], tuple[tuple[str, str], ...]]] | None:
        """First-parent `ref` (старые первыми): коммит, родители, изменения против
        ПЕРВОГО родителя (`--diff-merges=first-parent`, без переименований).

        Без pathspec намеренно: упрощение истории по пути скрыло бы merge-коммит,
        TREESAME второму родителю, — ровно доставку candidate. `None` —
        git не ответил (факт не установлен)."""
        done = subprocess.run(
            ["git", "-C", target_dir, "log", "--first-parent", "--reverse",
             "--diff-merges=first-parent", "--no-renames", "--name-status",
             "--format=%x00%H %P", ref],
            capture_output=True, text=True, check=False,
        )
        if done.returncode != 0:
            return None
        return _parse_first_parent(done.stdout)
```

`governance/act_settings.py`:

```python
def config_home(env: Mapping[str, str]) -> Path:
    """`$XDG_CONFIG_HOME` (только абсолютный, как требует XDG), иначе `~/.config`.

    То же правило обязана применять discovery для `act.toml` (M2, заметки
    части A п. 11) — расхождение дало бы draining только у одного из двух.
    """
    raw = env.get("XDG_CONFIG_HOME", "")
    return Path(raw) if raw and Path(raw).is_absolute() else Path.home() / ".config"
```

(`from collections.abc import Mapping` в импортах.) `node_approval.py` — после `DEBT_SELF_HASH`:
`DEBT_REVIEW_V1 = "review_v1"`.

`tests/conftest.py` — изоляция путей акта для тестов вне `act_*` (машина разработчика с
настоящей привязкой не уводит их в сеть; продукт стенда — до миграции, §10 legacy-эпоха, тот же
исход admission, что дал бы пустой журнал режима). Тесты `act_*` подменяют `mode_probe` сами:

```python
@pytest.fixture(autouse=True)
def _act_isolation(monkeypatch: pytest.MonkeyPatch, tmp_path_factory) -> None:
    from governance import act_status, act_trust

    monkeypatch.setattr(act_trust, "BINDING_PATH",
                        tmp_path_factory.getbasetemp() / "no-trust.toml")
    pre = act_status.LivePolicy(act_trust.PRE_MIGRATION, None, None, None, None)
    monkeypatch.setattr(act_status, "mode_probe", lambda state, deps=None: pre)
```

- [ ] **Step 3a: Выделить `approve_node._legacy_outcome`** (перенесено из Task 13, ревизия b3,
  B3-1: `LegacyProbe` исполняет ПОЛНЫЙ старый контракт через этот же код, а не параллельную
  частичную проверку). Поведение старого пути не меняется: `_reconcile_candidate` строится поверх
  чистой функции с прежними текстами; это держат существующие тесты
  `tests/test_governance_approve_node.py` без правок.

`_reconcile_candidate` поверх `_legacy_outcome` — новая чистая функция и её тип остановки:

```python
@dataclass(frozen=True)
class LegacyHalt:
    """Старый путь не установил факт: `text` — прежнее сообщение; `wrap` — через
    `_unresolved` (иначе прежний `RuntimeError` с тем же текстом)."""

    text: str
    wrap: bool = True


_LEGACY_UNPUBLISHED = "legacy_unpublished"
_LEGACY_UNPINNED = "legacy_unpinned"
_LEGACY_SIGNATURE = "legacy_signature"


def _legacy_outcome(state, ops, dag, op, key, facts) -> LegacyAccepted | Rejected | LegacyHalt:
    """Вердикт старого кода по смёрженному candidate без побочных эффектов (Task 12)."""
    pr = op["candidate_pr"]
    published = _snapshot_is_published(state, ops, dag, op, facts.get("headRefOid"))
    if published is None:
        return LegacyHalt(f"состав предложения в голове PR #{pr} — объект недоступен "
                          "либо голова не названа")
    if not published:
        return Rejected(_LEGACY_UNPUBLISHED,
                        f"в голове PR #{pr} ({facts.get('headRefOid')}) лежат не все узлы "
                        f"снимка заявки ({', '.join(op['nodes'])}), а PR уже закрыт либо "
                        "вмержен: предъявлено человеку было не то, что заявка выносит")
    event = af.merge_event(facts)
    if event.outcome is not Outcome.FOUND or event.value is None:
        return LegacyHalt(f"акт мержа candidate-PR #{pr} — {event.detail}")
    pinned = op.get("policy")
    if not pinned:
        return Rejected(_LEGACY_UNPINNED,
                        "заявка не закрепила версию политики (старый формат, §4.6)")
    policy = af.policy_snapshot(ops, pinned_sha=pinned["sha"])
    if policy.outcome is Outcome.UNAVAILABLE:
        return LegacyHalt(f"политика подписи для мержа PR #{pr}: {policy.detail}")
    if policy.outcome is Outcome.FORBIDDEN:
        refusal = policy.value
        assert isinstance(refusal, af.PolicyRefusal)
        if refusal.kind == af.POLICY_REFUSAL_SUPERSEDED:
            return Rejected(af.INVALIDATION_POLICY_CHANGED,
                            f"{af.INVALIDATION_POLICY_CHANGED}: {refusal.detail}")
        return LegacyHalt(f"{refusal.detail}. Заявка {key} жива, факт мержа не записан; "
                          "исправьте источник/окружение и повторите", wrap=False)
    snapshot = policy.value
    assert isinstance(snapshot, af.PolicySnapshot)
    if snapshot.fingerprint != pinned["fingerprint"]:
        return LegacyHalt(
            f"политика {snapshot.source}: прочитано не то, что закрепляла заявка {key} "
            f"(отпечаток {snapshot.fingerprint} ≠ {pinned['fingerprint']}) — подмена или "
            "сбой пути чтения; заявка жива, ничего не записано", wrap=False)
    signature = af.authorized_signature(event.value, snapshot)
    if signature.outcome is Outcome.FORBIDDEN:
        return Rejected(_LEGACY_SIGNATURE, signature.detail)
    return LegacyAccepted(event.value.login, evidence=(event.value, signature.value))


#: Тексты отказа старого пути — те же, что писал `_reconcile_candidate` до выделения.
_LEGACY_RAISE = {
    _LEGACY_UNPUBLISHED: "PR #{pr} завершён не на предложении заявки {key} — часть её узлов "
    "человеку не предъявлялась. Заявка invalidated; восстановление: новый candidate над "
    "теми же узлами",
    _LEGACY_UNPINNED: "заявка {key} не закрепила версию политики — invalidated; "
    "восстановление: новый candidate над теми же узлами",
    af.INVALIDATION_POLICY_CHANGED: "{detail}. Заявка {key} — invalidated; повторное "
    "установление авторизации — новый candidate над теми же узлами",
    _LEGACY_SIGNATURE: "{detail}. Заявка {key} — invalidated; восстановление: новый "
    "candidate над теми же узлами, мерж учёткой из allowlist даст верную подпись",
}
```

Хвост `_reconcile_candidate` после ветки `OPEN` (вместо `:1840–1928`):

```python
    event = af.merge_event(facts)
    if published and event.outcome is Outcome.ABSENT:
        al.abandon_request(state, key, f"candidate-PR #{pr} закрыт без мержа — не одобрено")
        return ApprovalOutcome(...)  # прежний текст без изменений
    verdict = _legacy_outcome(state, ops, dag, op, key, facts)
    if isinstance(verdict, LegacyHalt):
        raise _unresolved(verdict.text) if verdict.wrap else RuntimeError(verdict.text)
    if isinstance(verdict, Rejected):
        al.invalidate_request(state, key, verdict.detail)
        detail = verdict.detail.removeprefix(f"{verdict.code}: ")
        raise RuntimeError(_LEGACY_RAISE[verdict.code].format(pr=pr, key=key, detail=detail))
    merged, authorization = verdict.evidence
    al.record_merge(state, key, merged, authorization)
    return _finalize(state, ops, dag, state.ops[key], key, facts)
```

(`published` по-прежнему считается до ветвления по состоянию; при `published is None` прежний код
отказывал до ветки `OPEN` — этот порядок сохраняется: `_legacy_outcome` его повторяет только для
смёрженного PR.) Импорты: `from governance.act_check import LegacyAccepted`,
`from governance.act_decision import Rejected`.

`governance/act_envelope.py`, `envelope_refusal` — одна проверка «байты или пины» делится на
две с разными причинами (ревизия b4: у каждой проверки своя причина, двойник проверяет ровно её):

```python
    if node_self_hash != expected.self_hash:
        return f"узел {node}: байты узла изменились после одобрения"
    if dict(upstream_pins) != expected.upstream_pins:
        return f"узел {node}: upstream-пины изменились после одобрения"
```

- [ ] **Step 4: Write `act_status`**

```python
"""Статус узла `review/v1`: история доставок базы, вердикт доставки, конверт (§9.1).

Одобрение узла устанавливается историей first-parent базы (заявки и
остановки), а не конвертом: конверт обязан совпасть с действующим событием.
Узел без события `review/v1` в продукте под `ACT_MODE=review` судится старым
предикатом ТОЛЬКО после установленного provenance старого акта и admission
его доставки (§9.1 (а) п. 3; ревизия b1, B1-3). Ответ — в форме провайдера
steward: `approved` | `not_approved:<reason>` | `unavailable:<reason>` (О4).
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from governance import act_context, act_facts, act_trust, approval_branches, bundle_dag
from governance import bundle_inputs
from governance import node_approval as na
from governance.act_check import LegacyAccepted
from governance.act_decision import Accepted, Rejected
from governance.act_envelope import (
    DeliveredRequest,
    DeliveredStop,
    EnvelopeError,
    History,
    current_event,
    envelope_path,
    envelope_refusal,
    generation_refusal,
    parse_envelope,
    parse_stop,
    stop_path,
)
from governance.act_identity import (
    RequestIdentity,
    head_manifest_refusal,
    manifest_refusal,
    paths_refusal,
)
from governance.act_projection import ProjectionError, self_hash_v
from governance.act_request import ACT_REVIEW_V1, RequestError, manifest_path, parse_manifest
from governance.act_settings import ActSettings, config_home, load_settings
from governance.act_time import Ambiguous, Unavailable
from governance.facts import Outcome as FactOutcome
from governance.frontmatter import split_frontmatter
from governance.ops import Ops
from governance.run_state import RunState

APPROVED = "approved"
REQUEST_BINDING = "request_binding"
Dag = tuple[tuple[str, tuple[str, ...]], ...]
Rows = list[tuple[str, tuple[str, ...], tuple[tuple[str, str], ...]]]


def _rel(state: RunState, fname: str) -> str:
    return f"{state.bundle_dir}/{fname}"


def _node_paths(state: RunState, dag: Dag) -> dict[str, str]:
    return {bundle_dag.node_id(f): _rel(state, f) for f, _ in dag}


def pr_for_merge(ops: Ops, slug: str, merge_commit: str) -> int | Unavailable:
    """Единственный смёрженный PR, чей merge-коммит — `merge_commit` (merge и squash)."""
    try:
        prs = ops.prs_containing_commit(slug, merge_commit)
    except RuntimeError as exc:
        return Unavailable(f"PR merge-коммита {merge_commit[:12]}: {exc}")
    found = [p["number"] for p in prs
             if p.get("state") == "MERGED" and p.get("mergeCommit") == merge_commit]
    if len(found) != 1:
        return Unavailable(f"merge-коммит {merge_commit[:12]}: PR {found} (нужен ровно один)")
    return int(found[0])


def _pr_head(ops: Ops, state: RunState, pr: int) -> str | Unavailable:
    try:
        head = ops.pr_facts(state.repo_slug, pr).get("headRefOid")
    except (RuntimeError, OSError, ValueError) as exc:
        return Unavailable(f"голова PR #{pr}: {exc}")
    if not isinstance(head, str) or len(head) != 40:
        return Unavailable(f"голова PR #{pr} не названа")
    if ops.rev_parse(state.target_dir, f"{head}^{{commit}}") is None:
        ops.fetch_branch(state.target_dir, f"refs/pull/{pr}/head")  # squash: head вне базы
        if ops.rev_parse(state.target_dir, f"{head}^{{commit}}") is None:
            return Unavailable(f"голова PR #{pr} {head[:12]} не догружена (refs/pull/{pr}/head)")
    return head


# --- история ------------------------------------------------------------------------


def _event(
    ops: Ops, state: RunState, commit: str, status: str, path: str
) -> DeliveredRequest | DeliveredStop | Unavailable | None:
    requests_dir = f"workstreams/{state.ws_id}/approvals/requests/"
    stops_dir = f"workstreams/{state.ws_id}/approvals/stops/"
    if not path.startswith((requests_dir, stops_dir)):
        return None  # конверты и прочее — не события истории
    where = f"{commit[:12]}:{path}"
    if status != "A":
        return Unavailable(f"{where}: запись истории изменена после доставки ({status})")
    text = ops.show_file(state.target_dir, commit, path)
    if text is None:
        return Unavailable(f"{where}: не читается")
    if path.startswith(requests_dir):
        try:
            req = parse_manifest(text)
        except RequestError as exc:
            return Unavailable(f"{where}: {exc}")
        if req.ws_id != state.ws_id or manifest_path(req.ws_id, req.request_id) != path:
            return Unavailable(f"{where}: манифест лежит не по своему пути")
        # голова одобренного коммита — из фактов PR доставки (merge и squash, B1-9)
        pr = pr_for_merge(ops, state.repo_slug, commit)
        if isinstance(pr, Unavailable):
            return pr
        head = _pr_head(ops, state, pr)
        if isinstance(head, Unavailable):
            return head
        return DeliveredRequest(commit, head, req)
    try:
        stop = parse_stop(text)
    except EnvelopeError as exc:
        return Unavailable(f"{where}: {exc}")
    if stop_path(state.ws_id, stop.node, stop.generation) != path:
        return Unavailable(f"{where}: остановка лежит не по своему пути")
    return DeliveredStop(commit, stop)


def load_history(ops: Ops, state: RunState, base_ref: str) -> History | Unavailable:
    """Доставленные заявки и остановки по first-parent базы, старые первыми."""
    rows = ops.first_parent_changes(state.target_dir, base_ref)
    if rows is None:
        return Unavailable(f"история {base_ref}: first-parent не прочитан")
    events: list[DeliveredRequest | DeliveredStop] = []
    for commit, _parents, changes in rows:
        for status, path in changes:
            event = _event(ops, state, commit, status, path)
            if isinstance(event, Unavailable):
                return event
            if event is not None:
                events.append(event)
    return tuple(events)


# --- зависимости и вердикт доставки ------------------------------------------------


@dataclass(frozen=True)
class Deps:
    """Внешние чтения пути акта; тесты подменяют целиком."""

    readers: Any
    binding: Any  # act_trust.Binding | None | Unavailable
    journal: Callable[[Any, Any], Any]  # (readers, binding) -> Journal | Unavailable
    creds: tuple[act_trust.Credential, ...]
    gh: Any
    now: Callable[[], datetime]
    sleep: Callable[[float], None]
    settings: ActSettings | None


def real_deps(state: RunState, env: Mapping[str, str] | None = None) -> Deps:
    """Боевые зависимости: привязка, анонимный REST, профиль агента, журнал (Р-5)."""
    env = dict(os.environ) if env is None else env
    agent = Path(env.get("REVIEW_GH_CONFIG_DIR") or Path.home() / ".config" / "review")
    merge = Path(env["MERGE_GH_CONFIG_DIR"]) if env.get("MERGE_GH_CONFIG_DIR") else None
    state_home = Path(env.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")

    def journal(readers: Any, binding: act_trust.Binding) -> Any:
        from governance import act_journal

        return act_journal.journal_for(
            state_home=state_home,
            run_id=state.run_id,
            reread=lambda: act_trust.read_trust_snapshot(readers.anon, binding),
            env=env,
            policy_repository_id=binding.approval_policy_repository_id,
        )

    binding = act_trust.load_binding()
    # Task 11c (С-1, исполняется после этой задачи): `journal_for(..., binding=binding)` —
    # журнал перечитывает снимок транспортом решения (Р-23); транспорт строит `load_trust`.
    return Deps(
        readers=act_trust.default_readers(),
        binding=binding,
        journal=journal,
        creds=act_trust.credentials(env, agent_dir=agent, merge_dir=merge),
        gh=act_facts.gh_transport(agent) if isinstance(binding, act_trust.Binding) else None,
        now=lambda: datetime.now(UTC).replace(microsecond=0),
        sleep=time.sleep,
        settings=load_settings(config_home(env)),
    )


class Decider:
    """Вердикт доставки заявки из истории; доверие грузится один раз.

    Память — по полной идентичности заявки (`RequestIdentity` + merge-коммит),
    не по коммиту: один коммит может доставить несколько манифестов (B1-2).
    До вердикта steward — идентичность и привязка (B1-1): манифест называет эту
    заявку этого воркстрима продукта прогона, его узлы и пути — из DAG прогона,
    те же байты лежат в одобренном head, upstream-пины = прямым входам в head.
    """

    def __init__(self, state: RunState, ops: Ops, deps: Deps, dag: Dag, *,
                 trust: act_trust.Trust | None = None, journal: Any = None) -> None:
        self.state, self.ops, self.deps, self.dag = state, ops, deps, dag
        self.paths = _node_paths(state, dag)
        self._trust: act_trust.Trust | Unavailable | None = trust
        self._journal = journal
        self._memo: dict[tuple[str, ...], Any] = {}

    @property
    def journal(self) -> Any:
        """Журнал периметра, на котором загружено доверие (после `trust()`)."""
        return self._journal

    def trust(self) -> act_trust.Trust | Unavailable:
        """Доверенный контекст — один раз на вызов (п. 1–5 Task 12a)."""
        if self._trust is None:
            binding = self.deps.binding
            if binding is None:
                self._trust = Unavailable("нет привязки корня доверия (§6.1 п. 1)")
            elif isinstance(binding, Unavailable):
                self._trust = binding
            else:
                journal = self.deps.journal(self.deps.readers, binding)
                if isinstance(journal, Unavailable):
                    self._trust = journal
                else:
                    self._journal = journal
                    self._trust = act_trust.load_trust(
                        self.deps.readers, binding, journal, creds=self.deps.creds
                    )
        return self._trust

    def _head_hashes(self, event: DeliveredRequest) -> dict[str, str] | Unavailable:
        hashes: dict[str, str] = {}
        for entry in event.request.nodes.values():
            text = self.ops.show_file(self.state.target_dir, event.head_sha, entry.path)
            if text is None:
                return Unavailable(f"{entry.path} в {event.head_sha[:12]} не читается")
            try:
                hashes[entry.path] = self_hash_v(text, ACT_REVIEW_V1)
            except ProjectionError as exc:
                return Unavailable(str(exc))
        return hashes

    def _binding(self, event: DeliveredRequest, ident: RequestIdentity) -> Any:
        """`Rejected(REQUEST_BINDING)` — установленное несовпадение; `Unavailable`; `None`."""
        req = event.request
        identity = manifest_refusal(req, ident) or paths_refusal(req, self.paths)
        if identity is not None:
            return Rejected(REQUEST_BINDING, identity)
        delivered = self.ops.show_file(self.state.target_dir, event.merge_commit,
                                       ident.manifest_path)
        head = self.ops.repo_file_fact(self.state.repo_slug, event.head_sha,
                                       ident.manifest_path)
        if delivered is None or head.outcome is FactOutcome.UNAVAILABLE:
            return Unavailable(f"манифест {req.request_id}: не прочитан ({head.detail})")
        copy = head_manifest_refusal(
            head.value if head.outcome is FactOutcome.FOUND else None, delivered)
        if copy is not None:
            return Rejected(REQUEST_BINDING, copy)
        for node in req.nodes:
            fact = bundle_inputs.direct_blobs(self.state, self.ops, self.dag, node,
                                              event.head_sha)
            if fact.outcome is FactOutcome.FORBIDDEN:
                return Rejected(REQUEST_BINDING, f"прямые входы {node}: {fact.detail}")
            if fact.outcome is not FactOutcome.FOUND or fact.value is None:
                return Unavailable(f"прямые входы {node} в {event.head_sha[:12]}: {fact.detail}")
            if dict(req.upstream_pins[node]) != fact.value:
                return Rejected(REQUEST_BINDING, f"upstream-пины {node} ≠ прямым входам head")
        return None

    def __call__(self, event: DeliveredRequest) -> Any:
        ident = RequestIdentity(self.state.repo_slug, self.state.ws_id,
                                event.request.request_id)
        key = (*ident.memo_key, event.merge_commit)
        if key not in self._memo:
            self._memo[key] = self._decide(event, ident)
        return self._memo[key]

    def _decide(self, event: DeliveredRequest, ident: RequestIdentity) -> Any:
        refused = self._binding(event, ident)
        if refused is not None:
            return refused
        trust = self.trust()
        if isinstance(trust, Unavailable):
            return trust
        pr = pr_for_merge(self.ops, self.state.repo_slug, event.merge_commit)
        if isinstance(pr, Unavailable):
            return pr
        outcome, delivery = act_context.delivery_verdict(
            self.deps.readers, trust, self._journal,
            product=self.state.repo_slug, pr=pr,
            paths=sorted(e.path for e in event.request.nodes.values()),
            request=event.request, request_act=ACT_REVIEW_V1, head_sha=event.head_sha,
            facts_transport=self.deps.gh, self_hashes=lambda: self._head_hashes(event),
            legacy=None,
        )
        if delivery is not None and delivery.merge_commit != event.merge_commit:
            return Unavailable(f"PR #{pr} доставил {delivery.merge_commit}, не {event.merge_commit}")
        return outcome

    def legacy(self, pr: int, contract: Callable[[], Any]) -> Any:
        """Admission старой доставки (`request=None`): до активации — РЕЗУЛЬТАТ
        старой проверки `contract()` (`LegacyAccepted` | `Rejected` | `Unavailable`),
        никогда не безусловный `LegacyAccepted` (ревизия b2, B2-1)."""
        trust = self.trust()
        if isinstance(trust, Unavailable):
            return trust
        outcome, _ = act_context.delivery_verdict(
            self.deps.readers, trust, self._journal, product=self.state.repo_slug, pr=pr,
            paths=[], request=None, request_act=None, head_sha="",
            facts_transport=self.deps.gh, self_hashes=lambda: {}, legacy=contract,
        )
        return outcome


# --- provenance старого одобрения (§9.1 (а) п. 3) -----------------------------------

LEGACY_CONTRACT = "legacy_contract"
#: Коды отказа старого акта — по одному на проверку (ревизия b3: двойник проверяет
#: ровно свой код, вход построен так, что все предыдущие проверки проходят).
NO_EDIT, NOT_SIGNATURE = "legacy_no_edit", "legacy_not_signature_commit"
NOT_VIA_PR, NOT_FINALIZE = "legacy_signature_not_pr", "legacy_signature_not_finalize"
FOREIGN_WS, NO_CANDIDATE = "legacy_foreign_workstream", "legacy_no_candidate"
CANDIDATE_NOT_MERGED = "legacy_candidate_not_merged"
CANDIDATE_MERGER, CANDIDATE_TIME = "legacy_candidate_merger", "legacy_candidate_time"
NOT_IN_CANDIDATE = "legacy_node_not_in_candidate"
NO_LEDGER, LEDGER_CANDIDATE = "legacy_no_ledger_record", "legacy_ledger_other_candidate"
LEDGER_NODE = "legacy_ledger_node_absent"
NOT_PENDING, BYTES = "legacy_not_pending", "legacy_bytes"
HASH_FIELD, PINS = "legacy_hash_field", "legacy_pins"
#: Коды старого кода (`approve_node._legacy_outcome`): опубликованность снимка,
#: закреплённая политика, подпись по прежнему allowlist.
OLD_CODES = ("legacy_unpublished", "legacy_unpinned", "policy_changed", "legacy_signature")
_CANDIDATE = re.compile(r"spec/(?P<ws>.+)-approve-(?P<w>\d+)-(?P<k>\d+)-(?P<a>\d+)")


def candidate_identity(branch: str) -> tuple[str, str] | None:
    """`(ws_id, request_key)` ветки candidate; форма сверяется круговой
    подстановкой в SSOT-шаблон (`approval_branches.candidate_branch`)."""
    m = _CANDIDATE.fullmatch(branch)
    if m is None:
        return None
    w, k, a = int(m["w"]), int(m["k"]), int(m["a"])
    if approval_branches.candidate_branch(m["ws"], w, k, a) != branch:
        return None
    return m["ws"], f"approve-{w}-{k}-{a}"


def legacy_contract_refusal(candidate_text: str, base_text: str) -> tuple[str, str] | None:
    """Байтовая часть старого контракта (сверки 3–4 `_verify_nodes_in_base`):
    в candidate узел был `approval_pending`; собственные байты в базе — те, что
    выносились, и поле хеша им равно; пины подписи — пины выноса. `(код, деталь)`."""
    cand, _ = split_frontmatter(candidate_text)
    base, _ = split_frontmatter(base_text)
    if cand.get("status") != na.STATUS_APPROVAL_PENDING:
        return NOT_PENDING, "узел в candidate не был на одобрении (approval_pending)"
    recomputed = na.self_hash(base_text)
    if na.self_hash(candidate_text) != recomputed:
        return BYTES, "байты узла в базе ≠ вынесенным candidate"
    if base.get(na.SELF_HASH_KEY) != recomputed:
        return HASH_FIELD, f"{na.SELF_HASH_KEY} ≠ пересчёту байтов базы"
    if dict(cand.get("upstream_hashes") or {}) != dict(base.get("upstream_hashes") or {}):
        return PINS, "пины подписи ≠ пинам, под которыми узел выносился"
    return None


def _no(code: str, detail: str) -> str:
    return f"not_approved:{code}:{detail}"


class LegacyProbe:
    """Узел без события `review/v1` в продукте `review`: доказать старый акт ЦЕЛИКОМ.

    Цепочка (решение исполнителя плана Р-9, механизм; правило — спека), каждое
    звено — свой код: последняя правка файла узла в first-parent базы — запись
    ЕГО нынешней подписи; её PR — finalize старой заявки воркстрима прогона;
    candidate этой заявки смёржен этой учёткой в это время; узел — среди файлов
    candidate; запись заявки в леджере прогона называет этот candidate и этот
    узел. Вердикт `legacy` admission (`Decider.legacy`) — ПОЛНЫЙ старый контракт:
    байтовая часть (`legacy_contract_refusal`) и `approve_node._legacy_outcome`
    (опубликованный снимок, закреплённый пин `approval-policy-source/v1`, его
    отпечаток, `authorized_signature` мержера по прежнему allowlist; ревизия b3,
    B3-1). Нет пина или политики — отказ/`unavailable`, никогда `approved`.
    """

    def __init__(self, state: RunState, ops: Ops, base_ref: str,
                 decide_legacy: Callable[[int, Callable[[], Any]], Any], dag: Dag) -> None:
        self.state, self.ops, self.base_ref, self.dag = state, ops, base_ref, dag
        self.decide_legacy = decide_legacy
        self._rows: Rows | None = None

    def _changes(self) -> Rows | None:
        if self._rows is None:
            self._rows = self.ops.first_parent_changes(self.state.target_dir, self.base_ref)
        return self._rows

    def __call__(self, node_path: str, text: str) -> str:
        meta = split_frontmatter(text)[0]
        by, at = meta.get("approved_by"), meta.get("approved_at")
        rows = self._changes()
        if rows is None:
            return "unavailable:история базы не прочитана"
        commit = next((c for c, _p, ch in reversed(rows)
                       if any(path == node_path for _s, path in ch)), None)
        if commit is None:
            return _no(NO_EDIT, "в истории базы нет правки узла")
        signed = self.ops.show_file(self.state.target_dir, commit, node_path)
        signed_meta = split_frontmatter(signed)[0] if signed is not None else {}
        if (signed_meta.get("status"), signed_meta.get("approved_by"),
                signed_meta.get("approved_at")) != (na.STATUS_APPROVED, by, at):
            return _no(NOT_SIGNATURE, "последняя правка узла — не его подпись")
        return self._candidate(node_path, text, commit, str(by), str(at))

    def _finalize_branch(self, commit: str) -> str:
        """Ветка finalize-PR, смёржившего коммит подписи; `unavailable:`/`not_approved:`."""
        slug = self.state.repo_slug
        try:
            prs = self.ops.prs_containing_commit(slug, commit)
        except RuntimeError as exc:
            return f"unavailable:PR коммита подписи {commit[:12]}: {exc}"
        merged = [p["number"] for p in prs
                  if p.get("state") == "MERGED" and p.get("mergeCommit") == commit]
        if not merged:
            return _no(NOT_VIA_PR, "подпись внесена не смёрженным PR")
        if len(merged) > 1:
            return f"unavailable:коммит подписи — merge-коммит PR {merged}"
        facts = self.ops.brief_pr_fact(slug, int(merged[0]))
        if facts.outcome is not FactOutcome.FOUND or facts.value is None:
            return f"unavailable:PR подписи #{merged[0]}: {facts.detail}"
        if not approval_branches.is_finalize(facts.value.head_ref):
            return _no(NOT_FINALIZE, f"подпись внесена не finalize-PR (#{merged[0]})")
        return facts.value.head_ref

    def _candidate(self, node_path: str, text: str, commit: str, by: str, at: str) -> str:
        slug = self.state.repo_slug
        branch = self._finalize_branch(commit)
        if branch.startswith(("unavailable:", "not_approved:")):
            return branch
        candidate_branch = branch.removesuffix(approval_branches.finalize_suffix())
        ident = candidate_identity(candidate_branch)
        if ident is None or ident[0] != self.state.ws_id:
            return _no(FOREIGN_WS, f"finalize не заявки воркстрима {self.state.ws_id} "
                                   f"({candidate_branch})")
        key = ident[1]
        try:
            candidate = self.ops.find_pr(slug, candidate_branch, any_state=True)
            facts_c = self.ops.pr_facts(slug, candidate) if candidate is not None else {}
        except RuntimeError as exc:
            return f"unavailable:candidate {candidate_branch}: {exc}"
        if candidate is None:
            return _no(NO_CANDIDATE, f"нет candidate-PR {candidate_branch}")
        if facts_c.get("state") != "MERGED":
            return _no(CANDIDATE_NOT_MERGED, f"candidate #{candidate} не смёржен")
        if (facts_c.get("mergedBy") or {}).get("login") != by:
            return _no(CANDIDATE_MERGER, f"candidate #{candidate} смёржил не {by}")
        if facts_c.get("mergedAt") != at:
            return _no(CANDIDATE_TIME, f"мерж candidate #{candidate} не в {at}")
        offered = self._offered(candidate, node_path)
        if offered.startswith(("unavailable:", "not_approved:")):
            return offered
        op = self.state.ops.get(key)
        if op is None:
            return _no(NO_LEDGER, f"в леджере прогона нет заявки {key} — пин старой "
                                  "политики не установить")
        if op.get("candidate_pr") != candidate:
            return _no(LEDGER_CANDIDATE, f"заявка {key} леджера — candidate "
                                         f"#{op.get('candidate_pr')}, не #{candidate}")
        node = bundle_dag.node_id(node_path.rsplit("/", 1)[-1])
        if node not in op.get("nodes", ()):
            return _no(LEDGER_NODE, f"заявка {key} леджера не выносила узел {node}")
        return self._verdict(candidate, by, offered, text, op, key, facts_c)

    def _verdict(self, candidate: int, by: str, offered: str, text: str, op: Mapping,
                 key: str, facts_c: Mapping) -> str:
        from governance import approve_node as an  # ленивый: approve_node зовёт act_status

        def contract() -> Any:
            refusal = legacy_contract_refusal(offered, text)
            if refusal is not None:
                return Rejected(*refusal)
            got = an._legacy_outcome(self.state, self.ops, self.dag, dict(op), key,
                                     dict(facts_c))
            return Unavailable(got.text) if isinstance(got, an.LegacyHalt) else got

        verdict = self.decide_legacy(candidate, contract)
        if isinstance(verdict, LegacyAccepted):
            return APPROVED  # учётка старого кода = `by`: `_legacy_outcome` берёт её из facts_c
        if isinstance(verdict, Rejected):
            own = verdict.code.startswith("legacy_") or verdict.code in OLD_CODES
            return _no(verdict.code if own else f"legacy_admission:{verdict.code}",
                       verdict.detail or verdict.code)
        return f"unavailable:{getattr(verdict, 'reason', type(verdict).__name__)}"

    def _offered(self, candidate: int, node_path: str) -> str:
        """Текст узла в голове candidate — только если узел среди файлов candidate."""
        files = self.ops.brief_pr_fact(self.state.repo_slug, candidate)
        if files.outcome is not FactOutcome.FOUND or files.value is None:
            return f"unavailable:файлы candidate #{candidate}: {files.detail}"
        if node_path not in {path for path, _change in files.value.files}:
            return _no(NOT_IN_CANDIDATE, f"узел не из candidate #{candidate}")
        head = _pr_head(self.ops, self.state, candidate)
        if isinstance(head, Unavailable):
            return f"unavailable:{head.reason}"
        offered = self.ops.show_file(self.state.target_dir, head, node_path)
        if offered is None:
            return f"unavailable:узел в голове candidate {head[:12]} не читается"
        return offered


# --- статус узла --------------------------------------------------------------------


class Statuses:
    """Статусы узлов одного снимка; память по узлу (рекурсия по upstream)."""

    def __init__(
        self, *, dag: Dag, ws_id: str, history: History | Unavailable,
        node_path: Callable[[str], str], text_of: Callable[[str], str | None],
        blobs_of: Callable[[str], Mapping[str, str] | Unavailable],
        decide: Callable[[DeliveredRequest], Any],
        legacy: Callable[[str, str], str],
    ) -> None:
        self.dag, self.ws_id, self.history = dag, ws_id, history
        self.node_path, self.text_of, self.blobs_of = node_path, text_of, blobs_of
        self.decide, self.legacy = decide, legacy
        self._memo: dict[str, str] = {}

    def of(self, node: str) -> str:
        """Ответ провайдера по узлу."""
        if node not in self._memo:
            self._memo[node] = self._compute(node)
        return self._memo[node]

    def is_review(self, node: str) -> bool:
        """У узла есть событие `review/v1` в истории (неизвестно — тоже да)."""
        if isinstance(self.history, Unavailable):
            return True
        return current_event(self.history, node) is not None

    def _upstreams(self, node: str) -> tuple[str, ...]:
        for fname, ups in self.dag:
            if bundle_dag.node_id(fname) == node:
                return ups
        return ()

    def _stopped(self, node: str) -> bool:
        if isinstance(self.history, Unavailable):
            return False
        return isinstance(current_event(self.history, node), DeliveredStop)

    def _legacy(self, node: str) -> str:
        text = self.text_of(self.node_path(node))
        pins = self.blobs_of(node)
        if text is None:
            return "not_approved:нет файла узла"
        if isinstance(pins, Unavailable):
            return f"unavailable:{pins.reason}"
        debt = na.node_debt(node, text, dict(pins))
        if debt is not None:
            return f"not_approved:{debt.kind}"
        return self.legacy(self.node_path(node), text)

    def _compute(self, node: str) -> str:
        if isinstance(self.history, Unavailable):
            return f"unavailable:{self.history.reason}"
        current = current_event(self.history, node)
        if current is None:
            return self._legacy(node)
        broken = generation_refusal(self.history, node)
        if broken is not None:
            return f"not_approved:{broken}"
        if isinstance(current, DeliveredStop):
            return f"not_approved:stopped:g{current.stop.generation}"
        verdict = self.decide(current)
        if isinstance(verdict, (Unavailable, Ambiguous)):
            return f"unavailable:{verdict.reason}"
        if not isinstance(verdict, Accepted):
            return f"not_approved:act:{getattr(verdict, 'code', type(verdict).__name__)}"
        refusal = self._envelope(node, current, verdict.review_id)
        if refusal is not None:
            return refusal
        for up in self._upstreams(node):
            status = self.of(up)
            if status.startswith("unavailable:"):
                return f"unavailable:upstream:{up}"
            if status != APPROVED:
                kind = "upstream_stopped" if self._stopped(up) else "upstream"
                return f"not_approved:{kind}:{up}"
        return APPROVED

    def _envelope(self, node: str, current: DeliveredRequest, review_id: str) -> str | None:
        text = self.text_of(self.node_path(node))
        if text is None:
            return "not_approved:нет файла узла"
        raw = self.text_of(envelope_path(self.ws_id, node))
        try:
            env = parse_envelope(raw) if raw is not None else None
            own = self_hash_v(text, ACT_REVIEW_V1)
        except (EnvelopeError, ProjectionError) as exc:
            return f"not_approved:{exc}"
        pins = self.blobs_of(node)
        if isinstance(pins, Unavailable):
            return f"unavailable:{pins.reason}"
        assert not isinstance(self.history, Unavailable)
        refusal = envelope_refusal(env, self.history, node, own, pins, review_id)
        return None if refusal is None else f"not_approved:{refusal}"


def _blobs(state: RunState, ops: Ops, dag: Dag, ref: str | None):
    def read(node: str) -> Mapping[str, str] | Unavailable:
        fact = bundle_inputs.direct_blobs(state, ops, dag, node, ref)
        if fact.outcome is not FactOutcome.FOUND or fact.value is None:
            return Unavailable(f"прямые входы {node}: {fact.detail}")
        return fact.value

    return read


def _statuses(state: RunState, ops: Ops, dag: Dag, live: LivePolicy, deps: Deps, *,
              text_of: Callable[[str], str | None], ref: str | None,
              snapshot: str) -> Statuses:
    """`snapshot` — единственный снимок базы для истории и provenance старого акта (d3);
    `ref` — входы узлов (`None` — рабочее дерево S4), `text_of` — байты узлов."""
    decider = Decider(state, ops, deps, dag, trust=live.trust, journal=live.journal)
    paths = _node_paths(state, dag)
    return Statuses(
        dag=dag, ws_id=state.ws_id, history=load_history(ops, state, snapshot),
        node_path=paths.__getitem__, text_of=text_of, blobs_of=_blobs(state, ops, dag, ref),
        decide=decider, legacy=LegacyProbe(state, ops, snapshot, decider.legacy, dag),
    )


def statuses_at_base(state: RunState, ops: Ops, dag: Dag, live: LivePolicy,
                     deps: Deps | None = None, *, ref: str | None = None) -> Statuses:
    """Статусы по базе для гейта доставки и предложений (`read_dag_state`).

    Только для продукта в `review` (`live.mode`); иначе путь старый (§10). `ref` — снимок
    базы вместо локальной ветки (SHA от `fresh_base`, d3): им читаются история, provenance,
    байты и входы узлов — всё."""
    base = ref or state.base_ref or "master"
    return _statuses(state, ops, dag, live, deps or real_deps(state),
                     text_of=lambda path: ops.show_file(state.target_dir, base, path),
                     ref=base, snapshot=base)


# --- режим продукта сейчас (§10; ревизия b1, B1-5) ----------------------------------


@dataclass(frozen=True)
class LivePolicy:
    """Текущая активация открытой эпохи, её набор и режим продукта (живое правило §6)."""

    mode: str  # act_trust.PRE_MIGRATION | "merge" | "review"
    trust: Any  # act_trust.Trust
    journal: Any
    activation: Any  # act_check.ActivationView
    policy: Any  # act_policy.PolicySet


def live_policy(state: RunState, deps: Deps) -> LivePolicy | Unavailable:
    """Режим продукта сейчас по истории режима (`act_trust.product_mode`).

    Нет привязки, удалённая запись, откат `review → merge`, недочитанная
    нужная история — `Unavailable`, а не legacy (§6.1, §10; ревизия b1)."""
    if deps.binding is None:
        return Unavailable("нет привязки корня доверия /etc/df/trust.toml (§6.1 п. 1) — "
                           "режим продукта не установлен")
    if isinstance(deps.binding, Unavailable):
        return deps.binding
    journal = deps.journal(deps.readers, deps.binding)
    if isinstance(journal, Unavailable):
        return journal
    trust = act_trust.load_trust(deps.readers, deps.binding, journal, creds=deps.creds)
    if isinstance(trust, Unavailable):
        return trust
    now = deps.now()
    anchors = act_trust.load_anchor_times(deps.readers.anon, trust, now)  # 11c: (trust, now)
    loaded = act_trust.load_ranges(deps.readers.anon, trust, anchors, now, journal)  # 11c: (trust, …)
    if isinstance(loaded, Unavailable):
        return loaded
    ranges, sets = loaded
    mode = act_trust.product_mode(trust, ranges, sets, state.repo_slug, now)
    if isinstance(mode, Unavailable):
        return mode
    current = act_trust.current_activation(trust, ranges)
    if isinstance(current, Unavailable):
        return current
    policy = sets.get(current.merge_commit, Unavailable("набор текущей активации не прочитан"))
    if isinstance(policy, Unavailable):
        return policy
    return LivePolicy(mode, trust, journal, current, policy)


def mode_probe(state: RunState, deps: Deps | None = None) -> LivePolicy | Unavailable:
    """Единая точка «режим продукта сейчас» для путей вне `act_*` (гейты, DAG, создание).

    Тесты вне `act_*` подменяют её на `PRE_MIGRATION` (`tests/conftest.py`)."""
    return live_policy(state, deps or real_deps(state))


@contextmanager
def fresh_base(state: RunState, ops: Ops) -> Iterator[tuple[str, Ops] | Unavailable]:
    """Свежий изолированный снимок базы продукта для проверки допуска (ревью d2–d5):
    `(SHA ветки <base> репозитория state.repo_slug, Ops чтений из снимка)` или
    `Unavailable`. Снимок — временный репозиторий (`Ops.snapshot_open`): живёт до конца
    блока `with`, то есть до конца всех чтений проверки, затем удаляется — и при
    исключении. Все чтения проверки идут через отданные `Ops` и этот SHA."""
    base = state.base_ref or "master"
    fact = ops.snapshot_open(state.target_dir, state.repo_slug, base)
    if fact.outcome is not FactOutcome.FOUND or fact.value is None:
        yield Unavailable(f"снимок {state.repo_slug}@{base}: {fact.detail}")
        return
    repo = fact.value
    try:
        yield repo.sha, ops.snapshot_reader(repo)
    finally:
        ops.snapshot_close(repo)


def noop_refusal(state: RunState, ops: Ops, dag: Dag, nodes: Iterable[str] | None = None,
                 deps: Deps | None = None) -> str | None:
    """Почему успех без полного пути не допускается, или `None` (решение владельца 01.11).

    Любой успешный ранний выход — бесследный no-op §I5, повтор шага по сохранённому
    результату, переход волны по завершённой заявке, возврат уже доставленного PR — для
    продукта в `review` требует актуального допуска: каждый названный узел (`None` — все узлы
    DAG) с событием `review/v1` — `approved` по `statuses_at_base` на свежем снимке базы
    продукта (доверенный контекст, §9.1). Режим не установлен, снимок не получен, узел не
    `approved` или статус недоступен — причина (fail closed); продукт не в `review` —
    `None`, прежние правила. Все чтения — внутри `with fresh_base(...)`, через `Ops` снимка. Цена — квота С-1."""
    live = mode_probe(state, deps)
    if isinstance(live, Unavailable):
        return f"режим акта продукта: {live.reason}"
    if live.mode != "review":
        return None
    with fresh_base(state, ops) as snapshot:  # d2–d5: изолированный свежий снимок продукта
        if isinstance(snapshot, Unavailable):
            return f"свежая база: {snapshot.reason}"
        ref, reader = snapshot
        statuses = statuses_at_base(state, reader, dag, live, deps, ref=ref)
        wanted = None if nodes is None else set(nodes)
        for fname, _upstreams in dag:
            node = bundle_dag.node_id(fname)
            if (wanted is not None and node not in wanted) or not statuses.is_review(node):
                continue
            verdict = statuses.of(node)
            if verdict != APPROVED:
                return f"узел {node}: {verdict}"
    return None


# --- гейты steward (Р-10) -----------------------------------------------------------


def _gate_statuses(state: RunState, ops: Ops, dag: Dag, deps: Deps,
                   live: LivePolicy) -> dict[str, str]:
    worktree = Path(state.target_dir)

    def text_of(path: str) -> str | None:
        file = worktree / path
        return file.read_text(encoding="utf-8") if file.is_file() else None

    st = _statuses(state, ops, dag, live, deps, text_of=text_of, ref=None,
                   snapshot=state.base_ref or "master")
    paths = _node_paths(state, dag)
    return {node: st.of(node) for node, path in paths.items() if (worktree / path).is_file()}


def gate_run(
    state: RunState, ops: Ops, dag: Dag, *, candidate: bool, out: Path,
    deps: Deps | None = None,
) -> tuple[str, str | None] | Unavailable:
    """Режим `gate-check` по режиму продукта (Р-10) и файл провайдера (§12 п. 3)."""
    live = mode_probe(state, deps)
    if isinstance(live, Unavailable):
        return live
    if live.mode != "review":
        return "legacy", None  # PRE_MIGRATION или merge — старый читатель (§10)
    rc, digest = ops.gate_snapshot_digest(state.target_dir, state.bundle_dir, candidate=candidate)
    if rc != 0 or not digest.startswith("sha256:"):
        return Unavailable(f"gate-check --print-snapshot-digest: код {rc}: {digest}")
    nodes = _gate_statuses(state, ops, dag, deps or real_deps(state), live)
    out.write_text(
        json.dumps({"snapshot_digest": digest, "nodes": nodes}, sort_keys=True),
        encoding="utf-8",
    )
    return "review", str(out)
```

- [ ] **Step 5: Wire the readers**

`governance/approve_node.py`, `read_dag_state` — перед циклом по узлам:

```python
    from governance import act_status

    live = act_status.mode_probe(state)
    if isinstance(live, Unavailable):
        return DagState(None, unresolved=f"режим акта продукта: {live.reason}")
    statuses = (act_status.statuses_at_base(state, ops, dag, live)
                if live.mode == "review" else None)
```

и в цикле вместо безусловного `na.node_debt(...)`:

```python
        if statuses is not None:
            verdict = statuses.of(node)
            if verdict.startswith("unavailable:"):
                return DagState(None, unresolved=verdict)
            if verdict != act_status.APPROVED:
                debts.append(_review_debt(node, texts[node], verdict))
            continue
```

```python
def _review_debt(node: str, text: str, verdict: str) -> na.NodeDebt:
    """Долг узла в продукте `review`: причина — ответ статуса, процедура — новая заявка."""
    status = split_frontmatter(text)[0].get("status")
    return na.NodeDebt(
        node, status, na.DEBT_REVIEW_V1, verdict.removeprefix("not_approved:"),
        f"--approve-node {node} (заявка review/v1: approve человека по head)",
    )
```

`_require_upstream_ready` — тот же приём (новые проверки части B; реестр —
`approve_node._require_upstream_ready#mode_unset|upstream_unavailable|upstream_not_approved`):

```python
    from governance import act_status

    live = act_status.mode_probe(state)
    if isinstance(live, Unavailable):
        raise _unresolved(f"режим акта продукта (upstream {node_id}): {live.reason}")
    statuses = (act_status.statuses_at_base(state, ops, dag, live)
                if live.mode == "review" else None)
    for up in _upstreams(dag, node_id):
        text = _base_text(ops, state, _filename(dag, up))
        if statuses is not None:
            verdict = statuses.of(up)
            if verdict.startswith("unavailable:"):
                raise _unresolved(f"upstream {up} узла {node_id}: {verdict}")
            if verdict != act_status.APPROVED:
                raise RuntimeError(f"upstream {up} не одобрен (review/v1): "
                                   f"{_review_debt(up, text, verdict).reason}")
            continue
        debt = na.node_debt(  # прежний код без изменений
            up,
            text,
            _base_upstream_blobs(ops, state, dag, up),
            awaiting_merge_pr=_pr_awaiting_merge(state, up),
        )
        ...  # прежние `continue` / `RuntimeError` без изменений
```

Словами: один `mode_probe`; `Unavailable` — `_unresolved`;
`review` — `statuses_at_base(...).of(up)`: `approved` — готов, `unavailable:*` — `_unresolved`,
иное — прежний `RuntimeError` с `_review_debt(...).reason`; `PRE_MIGRATION`/`merge` — прежний
`node_debt`. `from governance.act_time import Unavailable` — в импорты модуля. В продукте до
миграции и в `merge` история не читается (`first_parent_changes` не зовётся) — стабы `Ops` старых
тестов (`tests/test_governance_task_bridge.py:366` `_StubOps` и наследники) не меняются; это
проверяет полный прогон Step 6, а класс целиком — статический обход
`tests/test_act_gate.py::test_new_ops_surface_reached_only_behind_mode_probe` (ревизия b1,
B1-8 как класс B1-7): новые методы `Ops` части B (`first_parent_changes`, `pr_body_fact`,
`gate_snapshot_digest`) зовутся только из `governance/act_*`, а `statuses_at_base`/
`load_history`/`Decider` вне `act_*` — только ниже **безусловного** `mode_probe` той же функции
(оператор верхнего уровня тела, не ветка `if`/цикла/`try`/лямбды; ревизия b2). Почему порядка
и безусловности достаточно: `mode_probe` вне `act_*` — единственная точка, которую подменяет
`tests/conftest.py`; если она выполнилась первой и вернула `PRE_MIGRATION`, все три места
(`_propose`, `read_dag_state`, `_require_upstream_ready`) по листингам ветвятся на
`live.mode == "review"` до чтения истории.

`governance/runner.py`, `_step_gate` — вместо `act_mode="legacy"` из Task 11:

```python
    from governance import act_status
    from governance.act_time import Unavailable

    gate = act_status.gate_run(
        state, ops, _wave_dag(), candidate=True,
        out=run_dir(state.run_id) / "node-status-s4.json",
    )
    if isinstance(gate, Unavailable):
        (run_dir(state.run_id) / "gate-findings.txt").write_text(
            f"error GC-ACT-UNAVAILABLE(devtools): режим акта не установлен — {gate.reason}\n",
            encoding="utf-8",
        )
        state.status = "stopped_gate"
        save(state)
        return False
    act_mode, provider = gate
    rc, output = ops.gate_check_candidate(
        state.target_dir, state.bundle_dir, profile_arg, act_mode=act_mode,
        node_status=provider,
    )
```

`_step_s8` — то же с `candidate=False`, файлом `node-status-s8.json`; недоступность — `print`
причины и `return False` (шаг resumable, как прочие стопы S8):

```python
    if isinstance(gate, Unavailable):
        print(f"S8: режим акта не установлен — {gate.reason}; шаг остановлен (resumable)")
        return False
```

`governance/task_bridge.py`, `_canonical_dag_hash` — В-1 (решение владельца 01.11), часть (1):
байты `approvals/` как оптимизация. Каталог `approvals/` — **из идентичности воркстрима**, путём производителя
(`act_envelope.envelope_path`), а не из `bundle_dir` (ревизия b2, B2-2: при `--bundle-dir spec`
`base.parent / "approvals"` читал бы `<repo>/approvals/`). Сигнатура получает ключевой
`ws_id: str | None = None`; `_content_anchor(..., *, ws_id=None)` его пробрасывает; вызовы
`task_bridge.py:2585` и `:4510` передают `ws_id=state.ws_id`, вызов `:2333` (внутри
`_delivered_content_anchor`) — без него (см. ниже). После цикла по узлам, до source-путей:

```python
    acted = False
    if ws_id is not None:
        approvals = Path(target_dir) / PurePosixPath(envelope_path(ws_id, "_")).parent
        if approvals.is_dir():
            for path in sorted(p for p in approvals.rglob("*") if p.is_file()):
                rel = path.relative_to(approvals).as_posix()
                lines.append(f"approvals {rel} {blob_sha1_bytes(path.read_bytes())}")
                acted = True
```

и в конце — `version = _CANON_VERSION_ACT if acted else _CANON_VERSION` (`_CANON_VERSION_ACT =
"v3"`): величина с байтами `approvals/` — новая эпоха канонизации, несопоставимая с `v2`
(`_comparable_anchors` уже так устроен). `_delivered_content_anchor` строит теневое дерево только
из файлов DAG head-коммита и `ws_id` не передаёт: у продукта в `review` его величина — `v2`,
текущая — `v3`, сверка — `comparison: unavailable` (тот же задокументированный fail-open, что при
недоступном head), а не ложное «содержание то же». Импорт: `from governance.act_envelope import
envelope_path`, `PurePosixPath` — уже в модуле.

(докстринг `_canonical_dag_hash` дополняется абзацем «`review/v1`: байты всех файлов
`workstreams/<ws-id>/approvals/` — конверты, манифесты, остановки — входят в хеш, эпоха `v3`
(В-1); равный хеш одного no-op узлов `review/v1` не даёт — `_admission_refusal`».)

`governance/task_bridge.py`, В-1 часть (2) и d1 — актуальный допуск перед успехом без полного
пути. Две функции (перед `deliver_superseded`; `_dag_for` — уже в модуле, импорт `act_status`
локальный, как у прочих путей вне `act_*`, — цикла импорта нет):

```python
def _admission_refusal(
    state: RunState, ops: Ops, legacy_bundle: int | None
) -> str | None:
    """Актуальный допуск узлов `review/v1` бандла перед успехом без полного пути
    (решение владельца 01.11) — `act_status.noop_refusal` по DAG прогона."""
    from governance import act_status

    return act_status.noop_refusal(state, ops, _dag_for(legacy_bundle))


def _require_admission(
    state: RunState, ops: Ops, legacy_bundle: int | None, done: str
) -> None:
    """Успех по сохранённому результату (доставка уже есть) — только при актуальном
    допуске узлов `review/v1`; иначе отказ (fail closed), сохранённое не меняется."""
    refusal = _admission_refusal(state, ops, legacy_bundle)
    if refusal is not None:
        raise RuntimeError(f"{done}, но допуск review/v1 не подтверждён — {refusal}")
```

`deliver_superseded` (~4510–4542): условие бесследного no-op выносится в имя `traceless` (комментарий
про `replace_fields` — прежний, без правок), и no-op требует подтверждённого допуска:

```python
    traceless = (
        # (прежний комментарий про `replace_fields` — без изменений)
        not replace_fields and comparable and recorded_content == content
    )
    # В-1 (решение владельца 01.11): равный хеш — оптимизация, допуск узлов
    # `review/v1` проверяется заново перед no-op; не подтверждён — дальше гейт
    # §I12 (`read_dag_state`) по прежним правилам: долг — отказ, недоступно — стоп.
    unconfirmed = _admission_refusal(state, ops, legacy_bundle) if traceless else None
    if unconfirmed is not None:
        print(
            "апстрим не менялся, но допуск review/v1 не подтверждён — "
            f"бесследного no-op нет ({unconfirmed})"
        )
    if traceless and unconfirmed is None:
        ...  # прежнее тело no-op: печать, `_debt_notice`, `return SupersedeResult("noop")`
```

Успехи по сохранённому результату (d1) — строка `_require_admission(...)` **перед** каждым:

| Место (master `a16e305`) | Вставка |
|---|---|
| `deliver_for_run`, `op completed` (~2514; покрывает и возврат замены v1, и `return pr_done`) | после `if pr_done is None: raise _missing_pr(state)`: `_require_admission(state, ops, legacy_bundle, f"tasks-спека уже доставлена (PR #{pr_done})")` |
| `deliver_for_run`, найден PR по ветке (~2557) | перед `return existing`: `_require_admission(state, ops, legacy_bundle, f"принят существующий PR #{existing}")` |
| `deliver_superseded`, повтор `--replace-revision 1` (~4282) | перед `return repeated`: `_require_admission(state, ops, legacy_bundle, f"возврат PR #{repeated.pr} замены первой доставки")` |
| `deliver_superseded`, `return_pr` (~4336) | перед `return SupersedeResult("returned", pr)`: `… f"возврат PR #{pr} ревизии {n}"` |
| `deliver_superseded`, `complete` (MERGED, ~4350) | перед возвратом: `… f"возврат вмерженного PR #{pr} ревизии {n}"` |
| `deliver_superseded`, `continue` с открытым PR (~4377) | перед возвратом: `… f"возврат открытого PR #{pr} ревизии {n}"` |
| `deliver_superseded`, повтор доставки из намерения (`started`, PR нет, ~4385) | перед `_recover_commit(...)` (до любого эффекта): `… f"повтор доставки ревизии {n} из намерения"` |
| `deliver_superseded`, открытая v1 (~4489) | перед `return SupersedeResult("returned", v1_pr)`: `… f"возврат PR #{v1_pr} первой доставки"` |

Отказ — `RuntimeError` «<что>, но допуск review/v1 не подтверждён — <причина>»; сохранённое не
меняется. Существующие тесты этих исходов — без правок: `tests/conftest.py` даёт продукт до
миграции, `noop_refusal` возвращает `None` без чтения статусов.

`governance/runner.py` (d1, D1-2) — сохранённый зелёный гейт и переход волны. `_step_gate` — вместо
`if op_status(state, key) == "completed": return True`:

```python
    key = wave_key(state, "gate-candidate")
    if op_status(state, key) == "completed":
        # Решение владельца 01.11: сохранённый зелёный гейт — успех без полного пути;
        # для `review/v1` — только при актуальном допуске судимых им узлов (уровни
        # ниже волны), иначе гейт заново по прежним правилам.
        from governance import act_status

        settled = [n for n, level in bundle_dag.levels(_wave_dag()).items()
                   if level < _wave_level(state)]
        refusal = act_status.noop_refusal(state, ops, _wave_dag(), settled)
        if refusal is None:
            return True
        print(f"S4: сохранённый гейт не принят — допуск review/v1 не подтверждён "
              f"({refusal}); гейт заново")
        op_start(state, key)
```

`_step_s8` — перед `if gate_op is not None and gate_op["status"] == "completed":` (существующий
`gate_op = state.ops.get(key)` — первая строка фрагмента):

```python
    gate_op = state.ops.get(key)

    if gate_op is not None and gate_op["status"] == "completed" and gate_op.get("exit") == 0:
        # Решение владельца 01.11: сохранённый зелёный S8 — успех без полного пути;
        # для `review/v1` — только при актуальном допуске всех узлов, иначе гейт заново.
        from governance import act_status

        refusal = act_status.noop_refusal(state, ops, _wave_dag())
        if refusal is not None:
            print(f"S8: сохранённый гейт не принят — допуск review/v1 не подтверждён "
                  f"({refusal}); гейт заново")
            # d2: допуск утрачен — прогон не завершён; нетерминальный статус сохраняется
            # (`op_start` пишет состояние) ДО любого шага, способного упасть
            state.status = "running"
            op_start(state, key)
            gate_op = state.ops[key]
```

`_next_wave` (~1300; три вызова — resume по завершённой заявке ~1058 и два в `_finalize_wave`) —
первыми строками тела:

```python
    from governance import act_status

    dag = _wave_dag()
    wave_nodes = [n for n, level in bundle_dag.levels(dag).items()
                  if level == _wave_level(state)]
    refusal = act_status.noop_refusal(state, ops, dag, wave_nodes)
    if refusal is not None:
        # Решение владельца 01.11: переход по завершённой заявке — успех по сохранённому
        # результату; для `review/v1` — только при актуальном допуске узлов волны.
        _stop_with_comment(
            state, ops, "stopped_review",
            f"волна {state.wave}: заявка завершена, но допуск review/v1 не подтверждён — "
            f"{refusal}; разберите узел (`--reopen`/`--repropose`), затем resume",
        )
        return state
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run --group governance pytest tests/test_act_status.py tests/test_act_legacy.py tests/test_act_gate.py tests/test_governance_runner.py tests/test_governance_approve_node.py tests/test_governance_node_approval.py tests/test_governance_task_bridge.py -v`
Expected: PASS. Существующие тесты — без правок ожиданий: `tests/conftest.py` даёт продукт до
миграции, старый путь не читает историю. Полный набор в обоих режимах (`uv run pytest -q`;
`GOVERNANCE_REQUIRED=1 uv run --group governance pytest -q`), ruff, pyrefly.

- [ ] **Step 7: Commit**

```bash
git add governance/act_status.py governance/ops.py governance/act_settings.py \
  governance/act_envelope.py \
  governance/node_approval.py governance/approve_node.py governance/runner.py \
  governance/task_bridge.py tests/conftest.py tests/test_act_status.py tests/test_act_legacy.py \
  tests/test_act_gate.py tests/test_governance_runner.py tests/test_governance_task_bridge.py
git commit -m "feat(act): статус узла review/v1 — история, provenance, провайдер gate-check, режим S4/S8, хеш DAG"
```

### Task 15: Агентская доставка candidate `review/v1` — предпроверка и граница секунды

**Files:**
- Create: `governance/act_premerge.py` (authority-root и `_HARNESS_PREFIXES` по префиксу
  `governance/act_`)
- Modify: `governance/act_decision.py` (публичный `merger_allowed` — D3 до мержа)
- Modify: `governance/act_projection.py` (снять `TODO(Part B, Task 15 …)` — см. ниже)
- Modify: `merge-pr.sh` (блок `case "$head_ref"` ~381, функция `has_label`, окружение
  предпроверки)
- Modify: `tests/harness_trust.py` (форма запуска с `--group governance`; `has_label` в
  `SAFE_COMMANDS`)
- Test: `tests/test_act_premerge.py`; дополнить `tests/test_merge_pr.py` (стаб `uv`)
- Modify: `tests/test_act_wiring.py` (Task 11c) — **обязательная** строка реестра
  `"governance/act_premerge.py::real_deps": _premerge_site`: `_premerge_site(tmp_path)` вызывает
  `act_premerge.real_deps(args, env=ENV)` с `args.merge_profile="/x/merge"`, `args.repo`,
  `args.pr` и возвращает `(deps.readers, deps.creds)`;
  `test_wiring_site_decides_with_history_token[…act_premerge…]` держит, что сборка предпроверки
  проходит закрытый набор форм credentials и даёт отказ при токене истории с `push` (C2-02;
  транспорт строит `load_trust`, Task 11c). `act_premerge` транспорт `AnonTransport` не строит —
  иначе красный `test_no_trust_transport_outside_factory`

**Interfaces:**
- Consumes: Tasks 4, 5, 7 (`find_act`), 11a, 11b, 11c (`load_trust` строит транспорт сам,
  `load_anchor_times(trust, t)`, `load_ranges(trust, …)`, `Readers(as_credential)`,
  `WIRING_SITES`), 12 (`live_policy_refusal`), 12a (`load_binding`,
  `load_trust` **с журналом** — `act_journal_wire.journal_for_premerge`, `load_anchor_times`,
  `load_ranges(..., journal)`, `product_mode`, `current_activation`, `act_identity`).
- Produces: запуск **файлом** из `merge-pr.sh`:
  `uv run --frozen --exact --group governance --no-config --no-env-file python -I
  "$script_dir/governance/act_premerge.py" --repo <slug> --pr <n> --head <sha> --head-ref <ветка>
  --merge-profile <dir> --merger <login> --merger-type User|Bot` → `0` — мерж разрешён (граница
  выдержана), `3` — отказ, `2` — не установлено. `main(argv, deps=None) -> int`;
  `PremergeDeps(readers, gh, binding, journal, now, sleep, creds, installations=None)`
  (`installations` — самоотчёт установок App у держателя ключа, его наполняет Task 15a);
  `real_deps(args, env=None)`; `request_of_branch(head_ref) ->
  tuple[str, str] | None` (`(ws_id, request_key)` из формы `contracts/approval-branches/v1`);
  `act_decision.merger_allowed(policy, login, hint) -> bool`.

Цепочка импортов `act_premerge` — `governance/__init__`, `governance/act_*` (кроме `act_journal`,
`act_node`, `act_status`, `act_context`; **включая** `act_journal_wire` — узкий журнал периметра,
ревизия b1, Р-16), `governance/facts`, `governance/ssot_env`, вендоренный
`contracts/approval-branches/` (данные), сторонние `steward`, `yaml` по `uv.lock`. **Не входят:**
`df/` (журнал — через `act_journal_wire`, не через `df.perimeter_client`), `governance/ops`,
`approval_facts`, `approval_branches`,
`node_approval`, `stale_adapter` (предпроверка не считает self-hash: `act_projection` не
импортируется). Отсюда п. 8 заметок части A выполняется так: `node_approval`/`stale_adapter` в
цепочке обвязки нет — инвариант `tests/harness_trust.py` это проверяет, и если будущая правка их
втянет, тест потребует добавить их в authority-root и `_HARNESS_PREFIXES`; `TODO` в
`act_projection.py` заменяется этой ссылкой.

`merge-pr.sh` сейчас безусловно отказывает (`die 3`) candidate-ветке. Новое правило: candidate
**с** меткой `human-approval-required` → предпроверка; `0` → дальше прежние гварды и мерж с пином
`sha=<head>`; `3` → `die 3`; иное → `die 2`. Candidate **без** метки — `die 3`, как сейчас
(legacy-акт — мерж человеком). Метка `human-merge-required` по-прежнему отказывает (гвард ниже).

`act_premerge` по порядку: (1) ветка → `RequestIdentity(product, ws_id, request_id)`, иначе 3;
(2) привязка: нет → 3 (заявка `review/v1` без корня доверия), недоступна → 2; журнал периметра
недоступен → 2; (3) `load_trust(readers, binding, journal)` — журнал **обязателен** (спека §6.3;
ревизия b1 сняла Р-7): блок, уже зарегистрированный другим потребителем, → 2; обнаруженное здесь
нарушение регистрируется (`_register`) → 2; непустой `Trust.unverified` → строки
`unverified premise: <имя>` в stderr, мерж не блокируют; (3a) App-режим (Task 15a): самоотчёт
установок у держателя ключа — нарушение регистрируется в журнале → 2, неизвестно → 2; (4)
`product_mode` по полной истории режима (B1-5): `review` → дальше, `PRE_MIGRATION`/`merge` → 3,
`Unavailable` (удаление записи, откат `review → merge`, недочитанная история) → 2; текущая
активация открытой эпохи и её набор; (5) манифест в `--head` (GraphQL-профиль агента,
`contents_bytes` через `GhTransport`): нет → 3, `act_identity.manifest_refusal` (идентичность из
ветки, не из манифеста) → 3; (6) `live_policy_refusal` → 3; (7) D3 заранее
(`merger_allowed(policy, --merger, --merger-type)`) → иначе 3; (8) `open_review` + D1
(`find_act`) на `--head`: голова фактов ≠ `--head` → 3, нет акта → 3; (9) **граница**: последнее
событие, способное повлиять на решение, — максимум `submitted_at` ревью, `created_at` отзывов,
`merged_at` активаций открытой эпохи; ждать до `max + 2 с`; ожидание > 10 с → 2 («часы
расходятся»); (10) повторные чтения ревью и текущей активации — что-то изменилось → 2. Окончательно
решает `check_delivery` после доставки (Task 13) — предпроверка не подпись.

- [ ] **Step 1: Write the failing tests**

`tests/test_act_premerge.py`:

```python
"""Предпроверка агентской доставки candidate review/v1 (§8.2; Task 15)."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

pytest.importorskip("steward")

from steward.approvalfacts import v3  # noqa: E402

from governance import act_facts, act_premerge, act_trust  # noqa: E402
from governance.act_check import ActivationView  # noqa: E402
from governance.act_decision import ReviewView  # noqa: E402
from governance.act_epochs import Epoch  # noqa: E402
from governance.act_policy import parse_policy_set  # noqa: E402
from governance.act_request import NodeEntry, NodeRequest, PolicyPin, render_manifest  # noqa: E402
from governance.act_source import SourceV2  # noqa: E402
from governance.act_time import Unavailable, parse_ts  # noqa: E402
from governance.facts import Fact, Outcome  # noqa: E402
from tests.act_cases import FILES  # noqa: E402

SLUG = "owner/p"
HEAD = "1" * 40
ACT = "a" * 40
BRANCH = "spec/ws-7-approve-2-1-1"
NOW = parse_ts("2026-10-12T12:00:00Z")
MODES = f"schema: approval-policy-modes/v1\nmodes:\n  {SLUG}: review\n".encode()
POLICY = parse_policy_set(ACT, {**FILES, "policy/modes.yaml": MODES})
SNAP = act_trust.TrustSnapshot(
    act_trust.TrustRoot("github.com", 11, "main", "s" * 40), "owner/df-trust",
    SourceV2("owner/approval-policy", "main", ACT, frozenset({"owner"})), (Epoch(1, ACT),))
BINDING = act_trust.Binding("github.com", 11, "owner/df-trust", "main", 22, "public")
REAL_LOAD_TRUST = act_trust.load_trust
CLEAN = {"admin": False, "maintain": False, "push": False}


def manifest(pin: str = ACT, request_id: str = "approve-2-1-1", ws_id: str = "ws-7",
             product: str = SLUG) -> str:
    return render_manifest(NodeRequest(
        request_id, ws_id, product, {"requirements": NodeEntry("spec/10-r.md", "h", 1)},
        {"requirements": {}},
        PolicyPin("owner/approval-policy", "main", pin, dict(POLICY.files_sha256))))


class Journal:
    """Журнал периметра: регистрация блокирует следующие решения (§6.3)."""

    def __init__(self, state: str = "clear") -> None:
        self.state, self.recorded = state, []

    def status(self, snap):
        return self.state

    def record(self, snap, source, detail):
        self.recorded.append((source, detail))
        self.state = "blocked"
        return None


class World:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.unverified: tuple[str, ...] = ()
        self.trust: object = None
        self.activation_at = "2026-10-12T08:00:00Z"
        self.approve_at = "2026-10-12T11:59:00Z"
        self.manifest: object = manifest()
        self.reads: list[v3.ReviewFacts] = []
        self.slept: list[float] = []
        self.clock = [NOW]
        self.journal: object = Journal()
        self.installations = None
        self.creds: tuple[act_trust.Credential, ...] = ()
        self.perms: dict[str, dict] = {}
        self.monkeypatch = monkeypatch
        monkeypatch.setattr(act_trust, "load_trust", lambda *a, **k: self.trust or
                            act_trust.Trust(SNAP, self.unverified))
        monkeypatch.setattr(act_trust, "load_anchor_times", lambda *a: {ACT: parse_ts(
            self.activation_at)})
        monkeypatch.setattr(act_trust, "load_ranges", lambda *a: (
            {1: (ActivationView(("activation", ACT), ACT, parse_ts(self.activation_at)),)},
            {ACT: POLICY}))
        monkeypatch.setattr(act_premerge, "_manifest_text", lambda *a: self.manifest)
        monkeypatch.setattr(act_facts, "open_review", lambda *a: self.next_facts())
        monkeypatch.setattr(act_facts, "effective", lambda facts, clock: {
            r.login: ReviewView(r.review_id, r.login, r.type_hint, r.state, r.commit_id)
            for r in facts.reviews})

    def real_perimeter(self) -> None:
        """Настоящий `load_trust`: снимок подменён, журнал и самоотчёт — свои."""
        self.monkeypatch.setattr(act_trust, "load_trust", REAL_LOAD_TRUST)
        self.monkeypatch.setattr(act_trust, "read_trust_snapshot", lambda anon, b: SNAP)
        self.monkeypatch.setattr(act_trust, "_rulesets", lambda anon, snap: None)
        self.monkeypatch.setattr(act_trust, "_rebase", lambda anon, snap: None)

    def as_credential(self, cred, path):
        perms = self.perms.get(cred.label, CLEAN)
        return Fact(Outcome.FOUND, {"permissions": perms}, "ok")

    def next_facts(self) -> v3.ReviewFacts:
        if self.reads:
            return self.reads.pop(0)
        review = v3.Review("11", "alice", "User", "APPROVED", HEAD, self.approve_at)
        return v3.ReviewFacts(SLUG, 7, HEAD, "master", "OPEN", (review,), ())

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.clock[0] = self.clock[0] + timedelta(seconds=seconds)

    def run(self, *extra: str) -> int:
        deps = act_premerge.PremergeDeps(
            readers=act_trust.Readers(self.as_credential), gh=object(),
            binding=BINDING, journal=self.journal, now=lambda: self.clock[0],
            sleep=self.sleep, creds=self.creds, installations=self.installations)
        argv = ["--repo", SLUG, "--pr", "7", "--head", HEAD, "--head-ref", BRANCH,
                "--merge-profile", "/x/review", "--merger", "df-reviewer[bot]",
                "--merger-type", "Bot", *extra]
        return act_premerge.main(argv, deps=deps)


@pytest.fixture()
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    return World(monkeypatch)


def test_clean_premerge_passes(world: World) -> None:
    assert world.run() == 0 and world.slept == []


def test_recent_approve_waits_past_second(world: World) -> None:
    """Approve 1 с назад → сон до max + 2 с."""
    world.approve_at = "2026-10-12T11:59:59Z"
    assert world.run() == 0
    assert world.slept == [1.0]


def test_recent_activation_waits(world: World) -> None:
    world.activation_at = "2026-10-12T11:59:59Z"
    assert world.run() == 0 and world.slept == [1.0]


def test_clock_skew_beyond_ten_seconds_unset(world: World, capsys) -> None:
    world.approve_at = "2026-10-12T12:00:20Z"
    assert world.run() == 2 and "часы расходятся" in capsys.readouterr().err


def test_stale_pin_refused(world: World, capsys) -> None:
    world.manifest = manifest(pin="b" * 40)
    assert world.run() == 3 and "policy_changed" in capsys.readouterr().err


@pytest.mark.parametrize("field", ["request_id", "ws_id", "product"])
def test_manifest_of_other_identity_refused(world: World, capsys, field: str) -> None:
    """Идентичность (product, ws_id, request_id) — по ветке и `--repo`, не по манифесту."""
    world.manifest = manifest(**{field: {"request_id": "approve-2-1-2", "ws_id": "ws-8",
                                         "product": "owner/other"}[field]})
    assert world.run() == 3 and f"манифест: {field}" in capsys.readouterr().err


def test_review_facts_head_differs_refused(world: World, capsys) -> None:
    """B2-3/B3-8: ревью-факты PR названы на другой голове, чем `--head` мержа; approve
    человека в фактах есть на голове `--head` — отказ только проверкой головы."""
    review = v3.Review("11", "alice", "User", "APPROVED", HEAD, world.approve_at)
    world.reads = [v3.ReviewFacts(SLUG, 7, "2" * 40, "master", "OPEN", (review,), ())]
    assert world.run() == 3 and "≠ --head" in capsys.readouterr().err


def test_no_approve_refused(world: World, capsys) -> None:
    world.reads = [v3.ReviewFacts(SLUG, 7, HEAD, "master", "OPEN", (), ())]
    assert world.run() == 3 and "нет approve человека" in capsys.readouterr().err


def test_reread_sees_dismissal_unset(world: World, capsys) -> None:
    world.approve_at = "2026-10-12T11:59:59Z"
    first = world.next_facts()
    dismissed = replace(first, reviews=(replace(first.reviews[0], state="DISMISSED"),),
                        dismissals=(v3.Dismissal("11", "carol", "2026-10-12T12:00:01Z",
                                                 "APPROVED"),))
    world.reads = [first, dismissed]
    assert world.run() == 2 and "изменились за время ожидания" in capsys.readouterr().err


def test_merger_outside_allowlist_refused(world: World, capsys) -> None:
    assert world.run("--merger", "stranger", "--merger-type", "User") == 3
    assert "не пройдёт D3" in capsys.readouterr().err


def test_no_manifest_refused(world: World, capsys) -> None:
    world.manifest = None
    assert world.run() == 3 and "нет манифеста" in capsys.readouterr().err


@pytest.mark.parametrize("mode,code", [
    (act_trust.PRE_MIGRATION, 3), ("merge", 3),
    (Unavailable("ACT_MODE: обратное переключение review → merge"), 2),
], ids=["pre_migration", "merge", "unset"])
def test_product_mode_gate(world: World, monkeypatch, capsys, mode, code: int) -> None:
    """B1-5: режим — по истории; неустановленный (удаление, откат) — 2, не отказ-legacy."""
    monkeypatch.setattr(act_trust, "product_mode", lambda *a: mode)
    assert world.run() == code
    reason = "режим продукта: " if code == 2 else "— не review"
    assert reason in capsys.readouterr().err


def test_existing_block_stops_premerge(world: World, capsys) -> None:
    """B1-4: блок, зарегистрированный другим потребителем, останавливает мерж."""
    world.real_perimeter()
    world.journal = Journal("blocked")
    assert world.run() == 2
    assert "заблокирован" in capsys.readouterr().err


def test_detected_violation_registered_and_blocks_restart(world: World, capsys) -> None:
    """B1-4: нарушение, найденное предпроверкой, записано; повторный запуск видит блок."""
    world.real_perimeter()
    world.creds = (act_trust.Credential("env:GH_TOKEN", token="ghp_x"),)
    world.perms = {"env:GH_TOKEN": {"admin": True, "maintain": True, "push": True}}
    assert world.run() == 2
    assert world.journal.recorded and world.journal.recorded[0][0] == "approval-policy"
    capsys.readouterr()
    world.perms = {}
    assert world.run() == 2
    assert "заблокирован" in capsys.readouterr().err


def test_journal_unavailable_is_unset(world: World, capsys) -> None:
    world.journal = Unavailable("журнал периметра (dev): integrity")
    assert world.run() == 2 and "журнал периметра: " in capsys.readouterr().err


def test_app_installation_violation_recorded(world: World, capsys) -> None:
    """B1-11: установка App на репо доверия — у держателя ключа; событие журнала, 2."""
    world.installations = lambda binding: ("df-trust", "установка App 9 видит df-trust (11)")
    assert world.run() == 2
    assert world.journal.recorded == [("df-trust", "установка App 9 видит df-trust (11)")]
    assert act_premerge.REGISTERED in capsys.readouterr().err


def test_app_installations_unknown_is_unset(world: World, capsys) -> None:
    world.installations = lambda binding: Unavailable("GET /app/installations: HTTP 502")
    assert world.run() == 2 and world.journal.recorded == []
    assert "установки App: " in capsys.readouterr().err


def test_no_binding_refused(world: World, monkeypatch, capsys) -> None:
    deps = act_premerge.PremergeDeps(
        readers=act_trust.Readers(world.as_credential), gh=object(), binding=None,
        journal=Unavailable("нет привязки"), now=lambda: NOW, sleep=world.sleep, creds=())
    argv = ["--repo", SLUG, "--pr", "7", "--head", HEAD, "--head-ref", BRANCH,
            "--merge-profile", "/x", "--merger", "m", "--merger-type", "Bot"]
    assert act_premerge.main(argv, deps=deps) == 3
    assert "без корня доверия" in capsys.readouterr().err


def test_unverified_premise_printed_but_not_blocking(world, capsys) -> None:
    world.unverified = ("allow_rebase_merge",)
    assert world.run() == 0
    assert "unverified premise: allow_rebase_merge" in capsys.readouterr().err


@pytest.mark.parametrize("branch,expected", [
    ("spec/ws-7-approve-2-1-1", ("ws-7", "approve-2-1-1")),
    ("spec/ws-7-approve-2-1-1-final", None),
    ("feat/x", None),
])
def test_request_of_branch(branch: str, expected) -> None:
    assert act_premerge.request_of_branch(branch) == expected


def test_premerge_chain_has_no_df_and_no_projection() -> None:
    """Р-16 и п. 8 заметок части A: цепочка обвязки — без df/ и без node_approval,
    но с узким журналом периметра."""
    from pathlib import Path

    from tests.harness_trust import closure

    files, problems, _third = closure(Path(__file__).resolve().parent.parent,
                                      "governance/act_premerge.py")
    assert problems == []
    assert "governance/act_journal_wire.py" in files
    assert not any(f.startswith("df/") for f in files)
    assert not {"governance/node_approval.py", "governance/stale_adapter.py",
                "governance/ops.py", "governance/act_journal.py"} & files
```

Дополнение `tests/test_merge_pr.py` — стаб `uv` рядом со стабом `gh` (в фикстуре `fleet`
каталог стабов уже в `PATH`):

```python
UV_STUB = """#!/usr/bin/env bash
echo "uv GH_TOKEN=[${GH_TOKEN-}] $*" >> "$GH_STUB_LOG"
exit "${UV_STUB_RC:-0}"
"""
```

```python
def _candidate(**env: str) -> dict[str, str]:
    return {"GH_STUB_HEADREF": approval_branches.candidate_branch("ws-42", 3, 2, 1), **env}


def test_review_candidate_premerge_ok_merges_with_pin(fleet: Fleet) -> None:
    res = fleet.run(**_candidate(GH_STUB_LABELS="human-approval-required"))
    assert res.returncode == 0, res.stderr
    assert any("act_premerge.py" in c and "--group governance" in c for c in fleet.calls())
    assert any(f"sha={HEAD_SHA}" in c for c in fleet.merge_calls())


def test_review_candidate_premerge_refusal_is_3(fleet: Fleet) -> None:
    res = fleet.run(**_candidate(GH_STUB_LABELS="human-approval-required", UV_STUB_RC="3"))
    assert res.returncode == 3 and fleet.merge_calls() == []


@pytest.mark.parametrize("rc", ["2", "1", "127"])
def test_review_candidate_premerge_unset_is_2(fleet: Fleet, rc: str) -> None:
    res = fleet.run(**_candidate(GH_STUB_LABELS="human-approval-required", UV_STUB_RC=rc))
    assert res.returncode == 2 and fleet.merge_calls() == []


def test_unlabelled_candidate_still_refused(fleet: Fleet) -> None:
    res = fleet.run(**_candidate())
    assert res.returncode == 3 and "candidate-PR" in res.stderr
    assert not any("act_premerge" in c for c in fleet.calls())


def test_review_candidate_with_human_merge_label_refused(fleet: Fleet) -> None:
    labels = "human-approval-required\nhuman-merge-required"
    res = fleet.run(**_candidate(GH_STUB_LABELS=labels))
    assert res.returncode == 3 and fleet.merge_calls() == []
```

(`fleet.calls()` — журнал всех строк лога стабов; если фикстура держит только `merge_calls()`,
исполнитель добавляет `calls()` рядом — тот же лог-файл.)

Тест изоляции файла (настоящий subprocess, отдельное окружение):

```python
def test_premerge_runs_as_isolated_file(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"],
           "UV_PROJECT_ENVIRONMENT": str(tmp_path / "venv")}
    done = subprocess.run(
        ["uv", "run", "--frozen", "--exact", "--group", "governance", "--no-config",
         "--no-env-file", "python", "-I", str(root / "governance/act_premerge.py"), "--help"],
        cwd=root, env=env, capture_output=True, text=True, check=False, timeout=600,
    )
    assert done.returncode == 0, done.stderr
    probe = subprocess.run(
        ["uv", "run", "--frozen", "--exact", "--group", "governance", "--no-config",
         "--no-env-file", "python", "-I", "-c", "import steward.approvalfacts.v3"],
        cwd=root, env=env, capture_output=True, text=True, check=False, timeout=600,
    )
    assert probe.returncode == 0, probe.stderr
```

(в `tests/test_act_premerge.py`, с `import os, subprocess` и `from pathlib import Path`;
помечен `@pytest.mark.slow`, если маркер заведён в `pyproject.toml`, иначе — обычный тест.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group governance pytest tests/test_act_premerge.py tests/test_merge_pr.py -v`
Expected: FAIL — `ImportError: cannot import name 'act_premerge'`; новые тесты `test_merge_pr`
падают на `returncode == 3` (обвязка ещё безусловно отказывает candidate).

- [ ] **Step 3: Implement**

`governance/act_decision.py`:

```python
def merger_allowed(policy: PolicySet, login: str, hint: str | None) -> bool:
    """D3 до мержа: тот же предикат, что `decide` применит к фактической доставке."""
    return _merger_ok(policy, login, hint)
```

`governance/act_premerge.py`:

```python
"""Предпроверка агентской доставки candidate `review/v1` (спека §8.2, §9.1).

Этот файл ИСПОЛНЯЕТ `merge-pr.sh` — файлом, в своём окружении:
`uv run --frozen --exact --group governance --no-config --no-env-file python -I
<этот файл> …` (форму распознаёт `tests/harness_trust.py`). Цепочка импортов —
только защищённые `governance/act_*`, `facts`, `ssot_env`; журнал периметра —
узкий клиент `act_journal_wire` (без `df/`, ревизия b1): уже зарегистрированный
блок останавливает мерж, обнаруженное здесь нарушение регистрируется. Коды:
0 — мерж разрешён (граница секунды выдержана), 3 — отказ, 2 — не установлено.
Предпроверка не подпись: окончательно решает `check_delivery` (Task 13).
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, "") and "governance" not in sys.modules:
    # Исполнение файлом под `python -I`: пакет — явно по пути, без дерева в
    # sys.path (тот же приём, что `brief_merge_check.py`).
    import importlib.util

    _pkg_dir = Path(__file__).resolve().parent
    _spec = importlib.util.spec_from_file_location(
        "governance", _pkg_dir / "__init__.py", submodule_search_locations=[str(_pkg_dir)]
    )
    assert _spec is not None and _spec.loader is not None
    _pkg = importlib.util.module_from_spec(_spec)
    sys.modules["governance"] = _pkg
    _spec.loader.exec_module(_pkg)

import argparse  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
from collections.abc import Callable, Sequence  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402
from typing import Any  # noqa: E402

from governance import act_facts, act_journal_wire, act_trust, ssot_env  # noqa: E402
from governance.act_anon import contents_bytes  # noqa: E402
from governance.act_check import live_policy_refusal  # noqa: E402
from governance.act_decision import find_act, merger_allowed  # noqa: E402
from governance.act_identity import RequestIdentity, manifest_refusal  # noqa: E402
from governance.act_request import RequestError, parse_manifest  # noqa: E402
from governance.act_time import Clock, Unavailable, parse_ts  # noqa: E402

EXIT_OK, EXIT_UNSET, EXIT_REFUSED = 0, 2, 3
GAP = timedelta(seconds=2)
MAX_WAIT = 10.0
PATTERNS = Path(__file__).resolve().parent.parent / "contracts/approval-branches/v1/patterns.env"
AGENT_GH_CONFIG_DIR = Path(
    os.environ.get("REVIEW_GH_CONFIG_DIR") or Path.home() / ".config" / "review"
)
REGISTERED = " — событие записано в журнал периметра; мерж не выполнен"

Installations = Callable[[act_trust.Binding], "act_trust.Found"]
"""Самоотчёт установок App у держателя ключа (Task 15a); `None` — режим профиля."""


class _Stop(Exception):
    def __init__(self, code: int, text: str) -> None:
        super().__init__(text)
        self.code = code


@dataclass(frozen=True)
class PremergeDeps:
    """Внешние чтения предпроверки; тесты подменяют целиком."""

    readers: act_trust.Readers
    gh: Any
    binding: Any  # Binding | None | Unavailable
    journal: Any  # act_trust.Journal | Unavailable — по привязке
    now: Callable[[], datetime]
    sleep: Callable[[float], None]
    creds: tuple[act_trust.Credential, ...]
    installations: Installations | None = None


def request_of_branch(head_ref: str) -> tuple[str, str] | None:
    """`(ws_id, request_key)` по форме candidate из SSOT `patterns.env`; finalize — нет."""
    text = PATTERNS.read_text(encoding="utf-8")
    template = ssot_env.definition_lines(text, "APPROVAL_CANDIDATE_TEMPLATE")[0].strip()
    pattern = re.escape(template)  # полное совпадение: finalize (`…-final`) не подходит
    for name, group in (("ws_id", r"(?P<ws_id>.+?)"), ("wave", r"(?P<wave>\d+)"),
                        ("step", r"(?P<step>\d+)"), ("attempt", r"(?P<attempt>\d+)")):
        pattern = pattern.replace(re.escape("{" + name + "}"), group)
    match = re.fullmatch(pattern, head_ref)
    if match is None:
        return None
    return match["ws_id"], f"approve-{match['wave']}-{match['step']}-{match['attempt']}"


def _manifest_text(deps: PremergeDeps, repo: str, head: str, path: str) -> str | None | Unavailable:
    raw = contents_bytes(deps.gh, repo, path, head)
    return raw.decode("utf-8") if isinstance(raw, bytes) else raw


def _live(deps: PremergeDeps, trust: act_trust.Trust, product: str):
    """Текущая активация, её набор и режим продукта по ПОЛНОЙ истории (B1-5)."""
    now = deps.now()
    anchors = act_trust.load_anchor_times(trust, now)  # Task 11c: транспорт — из `trust`
    loaded = act_trust.load_ranges(trust, anchors, now, deps.journal)
    if isinstance(loaded, Unavailable):
        raise _Stop(EXIT_UNSET, f"история источника политики: {loaded.reason}")
    ranges, sets = loaded
    mode = act_trust.product_mode(trust, ranges, sets, product, now)
    if isinstance(mode, Unavailable):
        raise _Stop(EXIT_UNSET, f"режим продукта: {mode.reason}")
    if mode != "review":
        raise _Stop(EXIT_REFUSED, f"ACT_MODE[{product}] = {mode} — не review")
    current = act_trust.current_activation(trust, ranges)
    if isinstance(current, Unavailable):
        raise _Stop(EXIT_UNSET, f"текущая активация: {current.reason}")
    policy = sets.get(current.merge_commit, Unavailable("набор текущей активации не прочитан"))
    if isinstance(policy, Unavailable):
        raise _Stop(EXIT_UNSET, policy.reason)
    span = ranges[trust.epochs[-1].epoch]
    if isinstance(span, Unavailable):
        raise _Stop(EXIT_UNSET, f"диапазон открытой эпохи: {span.reason}")
    return current, policy, span


def _boundary(facts: Any, span: Sequence[Any]) -> datetime:
    stamps = [a.at for a in span]
    stamps += [parse_ts(r.submitted_at) for r in facts.reviews if r.submitted_at]
    stamps += [parse_ts(d.created_at) for d in facts.dismissals]
    return max(stamps)


def _trust(deps: PremergeDeps) -> act_trust.Trust:
    """Привязка, журнал, проверки периметра; App — самоотчёт установок (B1-11)."""
    if deps.binding is None:
        raise _Stop(EXIT_REFUSED, "заявка review/v1 без корня доверия (нет /etc/df/trust.toml)")
    if isinstance(deps.binding, Unavailable):
        raise _Stop(EXIT_UNSET, deps.binding.reason)
    if isinstance(deps.journal, Unavailable):
        raise _Stop(EXIT_UNSET, f"журнал периметра: {deps.journal.reason}")
    trust = act_trust.load_trust(deps.readers, deps.binding, deps.journal, creds=deps.creds)
    if isinstance(trust, Unavailable):
        raise _Stop(EXIT_UNSET, trust.reason)
    if deps.installations is not None:
        found = deps.installations(deps.binding)
        if isinstance(found, Unavailable):
            raise _Stop(EXIT_UNSET, f"установки App: {found.reason}")
        if found is not None:
            failed = deps.journal.record(trust.snapshot, found[0], found[1])
            tail = REGISTERED if failed is None else f"; регистрация: {failed.reason}"
            raise _Stop(EXIT_UNSET, f"нарушение периметра: {found[1]}{tail}")
    for name in trust.unverified:
        print(f"unverified premise: {name}", file=sys.stderr)
    return trust


def _check(args: argparse.Namespace, deps: PremergeDeps) -> None:
    branch = request_of_branch(args.head_ref)
    if branch is None:
        raise _Stop(EXIT_REFUSED, f"ветка {args.head_ref!r} — не форма candidate")
    ident = RequestIdentity(args.repo, *branch)
    trust = _trust(deps)
    current, policy, span = _live(deps, trust, args.repo)
    text = _manifest_text(deps, args.repo, args.head, ident.manifest_path)
    if isinstance(text, Unavailable):
        raise _Stop(EXIT_UNSET, f"манифест: {text.reason}")
    if text is None:
        raise _Stop(EXIT_REFUSED, f"в {args.head[:12]} нет манифеста {ident.request_id}")
    try:
        req = parse_manifest(text)
    except RequestError as exc:
        raise _Stop(EXIT_REFUSED, f"манифест: {exc}") from exc
    refusal = manifest_refusal(req, ident) or live_policy_refusal(req.policy, current.merge_commit)
    if refusal is not None:
        raise _Stop(EXIT_REFUSED, refusal)
    if not merger_allowed(policy, args.merger, args.merger_type):
        raise _Stop(EXIT_REFUSED, f"мержер {args.merger} не пройдёт D3")
    facts = _act(args, deps, policy)
    wait = (_boundary(facts, span) + GAP - deps.now()).total_seconds()
    if wait > MAX_WAIT:
        raise _Stop(EXIT_UNSET, f"граница секунды через {wait:.0f} с — часы расходятся")
    if wait > 0:
        deps.sleep(wait)
    again = act_facts.open_review(deps.gh, args.repo, args.pr)
    current2, _policy2, _span2 = _live(deps, trust, args.repo)
    if again != facts or current2 != current:
        raise _Stop(EXIT_UNSET, "ревью или текущая активация изменились за время ожидания")


def _act(args: argparse.Namespace, deps: PremergeDeps, policy: Any) -> Any:
    facts = act_facts.open_review(deps.gh, args.repo, args.pr)
    if isinstance(facts, Unavailable):
        raise _Stop(EXIT_UNSET, f"ревью PR #{args.pr}: {facts.reason}")
    if facts.head_sha != args.head:
        raise _Stop(EXIT_REFUSED, f"голова PR {facts.head_sha[:12]} ≠ --head {args.head[:12]}")
    effective = act_facts.effective(facts, Clock(deps.now()))
    if isinstance(effective, Unavailable):
        raise _Stop(EXIT_UNSET, effective.reason)
    if find_act(args.head, effective, policy) is None:
        raise _Stop(EXIT_REFUSED, f"нет approve человека на {args.head[:12]}")
    return facts


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="act_premerge")
    for name in ("--repo", "--head", "--head-ref", "--merge-profile", "--merger"):
        p.add_argument(name, required=True)
    p.add_argument("--pr", required=True, type=int)
    p.add_argument("--merger-type", required=True, choices=("User", "Bot"))
    return p


def real_deps(args: argparse.Namespace, env: dict[str, str] | None = None) -> PremergeDeps:
    """Боевые чтения: GraphQL под профилем агента, привязка, журнал, самоотчёт `gh`;
    транспорт источников доверия строит `load_trust` (Task 11c)."""
    env = dict(os.environ) if env is None else env
    binding = act_trust.load_binding()
    journal: Any = Unavailable("нет привязки")
    if isinstance(binding, act_trust.Binding):
        journal = act_journal_wire.journal_for_premerge(
            env=env,
            state_home=Path(env.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"),
            run_id=f"merge-pr:{args.repo}#{args.pr}",
            policy_repository_id=binding.approval_policy_repository_id,
        )
    return PremergeDeps(
        readers=act_trust.default_readers(),
        gh=act_facts.gh_transport(AGENT_GH_CONFIG_DIR),
        binding=binding,
        journal=journal,
        now=lambda: datetime.now(UTC).replace(microsecond=0),
        sleep=time.sleep,
        creds=act_trust.credentials(env, agent_dir=AGENT_GH_CONFIG_DIR,
                                    merge_dir=Path(args.merge_profile)),
    )


def main(argv: Sequence[str] | None = None, deps: PremergeDeps | None = None) -> int:
    """CLI предпроверки; см. докстринг модуля."""
    args = _parser().parse_args(argv)
    deps = deps or real_deps(args)
    try:
        _check(args, deps)
    except _Stop as stop:
        print(f"act_premerge: {stop}", file=sys.stderr)
        return stop.code
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
```

`merge-pr.sh` — функция рядом с `die`:

```sh
#: Лейбл-рубильник «акт — approve человека, доставляет агент» (review/v1).
HUMAN_APPROVAL_LABEL="human-approval-required"
#: Своё окружение предпроверки (как у brief_merge_check в human-merge.sh).
premerge_env="${HOME}/.cache/devtools/act-premerge-venv"

has_label() {
    _want="$1"
    _saved_ifs="$IFS"
    IFS='
'
    for _label in $labels; do
        if [ "$_label" = "$_want" ]; then
            IFS="$_saved_ifs"
            return 0
        fi
    done
    IFS="$_saved_ifs"
    return 1
}
```

и блок гвардов:

```sh
# shellcheck disable=SC2254
case "$head_ref" in
    $finalize_glob) : ;;
    $candidate_glob)
        has_label "$HUMAN_APPROVAL_LABEL" || die 3 "PR ${slug}#${pr}: ветка \
'$head_ref' — candidate-PR шага одобрения (§I12). Его мерж человеком из allowlist \
И ЕСТЬ акт одобрения узлов; агентский мерж сжёг бы заявку впустую. Мержит человек."
        # review/v1: акт — approve человека; доставка агентом после предпроверки
        # (спека §8.2, §9.1). Исполняется ФАЙЛОМ в своём окружении.
        premerge_rc=0
        premerge_out=$(cd "$script_dir" && UV_PROJECT_ENVIRONMENT="$premerge_env" \
            uv run --frozen --exact --group governance --no-config --no-env-file \
            python -I "$script_dir/governance/act_premerge.py" \
            --repo "$slug" --pr "$pr" --head "$head_oid" --head-ref "$head_ref" \
            --merge-profile "$MERGE_GH_CONFIG_DIR" --merger "$login" \
            --merger-type "$merger_type") || premerge_rc=$?
        [ -n "$premerge_out" ] && echo "$premerge_out" >&2
        case "$premerge_rc" in
            0) : ;;
            3) die 3 "PR ${slug}#${pr}: предпроверка акта review/v1 отказала — мерж не выполняется" ;;
            *) die 2 "PR ${slug}#${pr}: предпроверка акта не установила факт (код $premerge_rc) — повторите" ;;
        esac ;;
esac
```

`merger_type` — `User` в профильном режиме (учётка machine user); App-режим (Task 15a) ставит
`Bot`. Цикл по лейблам ниже (`HUMAN_MERGE_LABEL`) переписывается через `has_label` — поведение то
же.

`tests/harness_trust.py`: `"has_label"` — в `SAFE_COMMANDS` (функция самого скрипта, её тело
проверяется тем же разбором); новая форма запуска — отдельное правило, прежняя не ослабляется:

```python
_PY_UV_GOV = re.compile(
    r"^uv run --frozen --exact --group governance --no-config --no-env-file "
    r'python -I "\$script_dir/([^"]+\.py)"(\s.*)?$'
)
```

и в `_scan` распознаётся как `Launch(script, "py-uv", path)` (те же требования к цепочке и
`UV_DEPENDENCY_FILES`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --group governance pytest tests/test_act_premerge.py tests/test_merge_pr.py tests/test_harness_trust.py -v`
(файл тестов харнесса — тот, что импортирует `tests/harness_trust.py`: `git grep -l harness_trust tests/`).
Expected: PASS. `shellcheck merge-pr.sh` — чисто. Полный набор, ruff, pyrefly.

- [ ] **Step 5: Commit**

```bash
git add governance/act_premerge.py governance/act_decision.py governance/act_projection.py \
  merge-pr.sh tests/harness_trust.py tests/test_act_premerge.py tests/test_merge_pr.py \
  tests/test_act_wiring.py
git commit -m "feat(merge-pr): доставка candidate review/v1 агентом после предпроверки и границы секунды"
```

### Task 15a: `merge-pr.sh` в контексте GitHub App

**Files:**
- Create: `governance/act_app_token.py` (JWT → slug и токен установки; authority-root по префиксу)
- Modify: `merge-pr.sh` (preflight личности ~263–274), `governance/act_premerge.py` (самоотчёт
  установок App: `--app-id`, `--app-key`), `pyproject.toml`/`uv.lock` (`pyjwt[crypto]` в группе
  `governance`; уже в `conductor`)
- Test: `tests/test_act_app_token.py`; `tests/test_merge_pr.py`

**Interfaces:**
- Produces: `python -I governance/act_app_token.py --app-id <id> --key <path> --repo <slug>` (та же
  форма запуска, что Task 15) → stdout две строки: `<slug>[bot]` и токен установки на целевой
  репо; коды 0 / 2; `app_identity(app_id, key, repo, *, http, now) -> tuple[str, str] |
  Unavailable`; `installations_refusal(app_id, key, trust_repos, *, http, now) -> Found`
  (`(роль, деталь)` | `Unavailable` | `None`; установка App на `approval-policy` или `df-trust` —
  нарушение; спека A rev 16 §6.2; оба списка — все страницы, B1-12);
  `act_premerge.app_installations(app_id, key, *, http=None) -> Installations`; `act_premerge`
  принимает `--app-id <id> --app-key <path>` (оба или ни одного).

Положительный сценарий №1 и M3 требуют, чтобы candidate доставлял App. Действующий preflight
`merge-pr.sh:269` вызывает `gh api user`, а токен установки App получает на него 403 (spike;
спека §9.4). Во флоте владельца до спеки B действует этот режим (раздел «Ревизия спеки A rev 16»):
App-режим обёртки только для candidate `review/v1` (бриф — Task 17), finalize и stop — прежним
профилем ADR-ECO-011 D5. В установке `df` мержит издатель (этап 2b установщика), этот режим там не
используется.

Правила:
- App-режим включают `MERGE_APP_ID` и `MERGE_APP_KEY` (путь к ключу в
  `$XDG_CONFIG_HOME/darkfactory/apps/`); без них — прежний режим (`gh api user` = `AGENT_LOGIN`).
- App-режим допустим только для PR с меткой `human-approval-required` на candidate-ветке; прочие
  PR при выставленных переменных — `die 2` («App-режим только для candidate review/v1»), не тихий
  откат на профиль.
- Preflight в App-режиме: `act_app_token.py` → `login=<slug>[bot]`, `merger_type=Bot`,
  `GH_TOKEN=<installation token>` для всех последующих `gh` вызовов обёртки (`gh_a` выставляет
  `GH_TOKEN` вместо `GH_CONFIG_DIR`). Совпадение `<slug>[bot]` с `AUTHORIZED_MERGER_ACCOUNTS`
  текущей активации проверяет `act_premerge` (`merger_allowed`).
- Самоотчёт App (Task 12a, спека §6.2): производный токен установки **не** проходит
  `_self_report` — спека освобождает его от самоотчёта; проверяется держатель ключа. Поэтому
  `merge-pr.sh` запускает `act_premerge` с `GH_TOKEN=""` (токен установки процессу предпроверки не
  передаётся, `credentials` его не видит), а `act_premerge --app-id/--app-key` читает установки App
  (`GET /app/installations`, `GET /installation/repositories` с токеном метаданных, все страницы) —
  установка, видящая `approval-policy` или `df-trust` по `repository_id` привязки, — нарушение:
  событие журнала периметра (Task 15, `act_journal_wire`) и код 2; неполный список или неизвестные
  полномочия установки — 2. Прочие credentials процесса (`GITHUB_TOKEN`, профили) самоотчитываются
  как обычно (ревизия b1, B1-11).

- [ ] **Step 1: Write the failing tests** — `tests/test_act_app_token.py` (HTTP подменён
  фабрикой ответов, JWT проверяется `jwt.decode` с публичным ключом тестовой пары):

```python
"""Личность App и самоотчёт установок (спека §9.4, §6.2; Task 15a)."""

from __future__ import annotations

import urllib.request

import pytest

jwt = pytest.importorskip("jwt")
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from governance import act_app_token  # noqa: E402
from governance.act_time import Unavailable  # noqa: E402

TRUST = {"approval-policy": 22, "df-trust": 11}
INST = "/app/installations?per_page=100&page={}"
REPOS = "/installation/repositories?per_page=100&page={}"


@pytest.fixture()
def key(tmp_path):
    pk = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / "app.pem"
    path.write_bytes(pk.private_bytes(serialization.Encoding.PEM,
                                      serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    return path, pk.public_key()


class Http:
    """Ответы по `(метод, путь)`; репо установки — по её токену (`visible`)."""

    def __init__(self, routes: dict, visible: dict[int, list[int]] | None = None) -> None:
        self.routes, self.visible, self.seen = routes, visible or {}, []

    def __call__(self, method: str, path: str, auth: str, body: dict | None = None):
        self.seen.append((method, path, auth))
        if (method, path) in self.routes:
            return self.routes[(method, path)]
        if path == REPOS.format(1) and auth.startswith("token ghs_"):
            ids = self.visible.get(int(auth.removeprefix("token ghs_")), [])
            return 200, {"total_count": len(ids), "repositories": [{"id": i} for i in ids]}
        return 502, None


def _installs(*pages: list[dict]) -> dict:
    """Маршруты списка установок по страницам и токены метаданных для каждой."""
    routes: dict = {("GET", INST.format(n)): (200, page) for n, page in enumerate(pages, 1)}
    for page in pages:
        for item in page:
            routes[("POST", f"/app/installations/{item['id']}/access_tokens")] = (
                201, {"token": f"ghs_{item['id']}"})
    return routes


def _repos(ids: list[int], total: int | None = None) -> dict:
    return {("GET", REPOS.format(1)): (200, {"total_count": len(ids) if total is None
                                             else total,
                                             "repositories": [{"id": i} for i in ids]})}


def test_identity_uses_jwt_not_gh_user(key) -> None:
    path, public = key
    http = Http({
        ("GET", "/app"): (200, {"slug": "df-probe-reviewer"}),
        ("GET", "/repos/o/p/installation"): (200, {"id": 9}),
        ("POST", "/app/installations/9/access_tokens"): (201, {"token": "ghs_x"}),
    })
    got = act_app_token.app_identity(5285716, path, "o/p", http=http, now=1_700_000_000)
    assert got == ("df-probe-reviewer[bot]", "ghs_x")
    # Подпись проверяется; срок — отдельно от часов прогона (ревизия b1, B1-16).
    claims = jwt.decode(http.seen[0][2].removeprefix("Bearer "), public, algorithms=["RS256"],
                        options={"verify_exp": False, "verify_iat": False})
    assert claims["iss"] == "5285716"
    assert (claims["iat"], claims["exp"]) == (1_700_000_000 - 60, 1_700_000_000 + 540)
    assert not any(p == "/user" for _, p, _ in http.seen)


def test_identity_unreadable_is_unavailable(key) -> None:
    """B3-8: ответ `/app` не принят — отказ этой проверки; установка и токен отвечают
    успешно, чтобы отключённая проверка не была подменена следующей."""
    http = Http({("GET", "/app"): (403, {"message": "x"}),
                 ("GET", "/repos/o/p/installation"): (200, {"id": 9}),
                 ("POST", "/app/installations/9/access_tokens"): (201, {"token": "ghs_x"})})
    got = act_app_token.app_identity(1, key[0], "o/p", http=http, now=0)
    assert isinstance(got, Unavailable) and got.reason == "GET /app: HTTP 403"


def test_installation_unreadable_is_unavailable(key) -> None:
    """Установка App на целевой репо не прочитана — отказ этой проверки."""
    http = Http({("GET", "/app"): (200, {"slug": "df"}),
                 ("GET", "/repos/o/p/installation"): (404, None),
                 ("POST", "/app/installations/9/access_tokens"): (201, {"token": "ghs_x"})})
    got = act_app_token.app_identity(1, key[0], "o/p", http=http, now=0)
    assert isinstance(got, Unavailable) and got.reason == "установка App на o/p: HTTP 404"


def test_installation_on_trust_repo_is_violation(key) -> None:
    http = Http({**_installs([{"id": 9}]), **_repos([7, 22])})
    got = act_app_token.installations_refusal(1, key[0], TRUST, http=http, now=0)
    assert got == ("approval-policy", "установка App 9 видит approval-policy (22)")


def test_installations_clean(key) -> None:
    http = Http({**_installs([{"id": 9}]), **_repos([7])})
    assert act_app_token.installations_refusal(1, key[0], TRUST, http=http, now=0) is None


def test_installation_token_refused_is_unavailable(key) -> None:
    """b4: токен метаданных установки не выдан — список не проверен, не «чисто»."""
    http = Http({("GET", INST.format(1)): (200, [{"id": 9}])})  # POST access_tokens → 502
    got = act_app_token.installations_refusal(1, key[0], TRUST, http=http, now=0)
    assert isinstance(got, Unavailable) and got.reason == "токен установки 9: HTTP 502"


def test_violation_on_second_installations_page(key) -> None:
    """B1-12: установка на репо доверия на второй странице не теряется."""
    first = [{"id": 1000 + n} for n in range(100)]
    http = Http(_installs(first, [{"id": 9}]), visible={9: [11]})
    got = act_app_token.installations_refusal(1, key[0], TRUST, http=http, now=0)
    assert got == ("df-trust", "установка App 9 видит df-trust (11)")


def test_full_last_page_requires_empty_next(key) -> None:
    """Ровно 100 установок: полнота — по пустой следующей странице, не по догадке."""
    page = [{"id": n} for n in range(1, 101)]
    http = Http(_installs(page))  # страница 2 не отвечает (502)
    got = act_app_token.installations_refusal(1, key[0], TRUST, http=http, now=0)
    assert isinstance(got, Unavailable) and "стр. 2" in got.reason
    http = Http({**_installs(page), ("GET", INST.format(2)): (200, [])})
    assert act_app_token.installations_refusal(1, key[0], TRUST, http=http, now=0) is None


def test_repositories_total_mismatch_is_unavailable(key) -> None:
    http = Http({**_installs([{"id": 9}]), **_repos([7], total=150)})
    got = act_app_token.installations_refusal(1, key[0], TRUST, http=http, now=0)
    assert isinstance(got, Unavailable) and "total_count" in got.reason


def test_http_does_not_follow_redirects() -> None:
    """B1-12: 3xx возвращается кодом — запрос с Authorization на новый адрес не идёт."""
    handler = act_app_token._NoRedirect()
    req = urllib.request.Request("https://api.github.com/app",
                                 headers={"Authorization": "Bearer x"})
    assert handler.redirect_request(req, None, 302, "Found", {},
                                    "https://evil.example/app") is None
    assert any(isinstance(h, act_app_token._NoRedirect)
               for h in act_app_token._OPENER.handlers)


# --- сквозной App → act_premerge → load_trust (ревизия b1, B1-11) -------------------

def _app_world(monkeypatch, key, env: dict[str, str], visible: dict[int, list[int]]):
    from pathlib import Path

    from governance import act_premerge, act_trust
    from tests.test_act_premerge import World

    world = World(monkeypatch)
    world.real_perimeter()
    world.creds = act_trust.credentials(env, agent_dir=Path("/x/review"),
                                        merge_dir=Path("/x/merge"))
    world.perms = {"env:GH_TOKEN": {}}  # токен установки: `permissions` в ответе нет
    world.installations = act_premerge.app_installations(
        1, key[0], http=Http(_installs([{"id": 9}]), visible))
    return world


def test_app_mode_premerge_clean(monkeypatch, key) -> None:
    """Запуск с `GH_TOKEN=""`: производный токен не держится и не самоотчитывается."""
    world = _app_world(monkeypatch, key, {"GH_TOKEN": ""}, {9: [7]})
    assert world.run() == 0 and world.journal.recorded == []


def test_app_token_leaked_into_env_is_unset(monkeypatch, key, capsys) -> None:
    """Двойник: токен установки в `GH_TOKEN` — самоотчёт невозможен (§6.2), 2."""
    world = _app_world(monkeypatch, key, {"GH_TOKEN": "ghs_inst"}, {9: [7]})
    assert world.run() == 2 and world.journal.recorded == []
    assert "самоотчёт env:GH_TOKEN" in capsys.readouterr().err


def test_app_installation_on_policy_repo_registered(monkeypatch, key) -> None:
    world = _app_world(monkeypatch, key, {"GH_TOKEN": ""}, {9: [22]})
    assert world.run() == 2
    assert [r[0] for r in world.journal.recorded] == ["approval-policy"]
```

`tests/test_merge_pr.py`: App-режим — `gh api user` не вызывается (лог стаба), `uv` зовётся с
`act_app_token.py` и затем `act_premerge.py … --merger <slug>[bot] --merger-type Bot --app-id …
--app-key …`, причём строка лога стаба для `act_premerge.py` несёт `GH_TOKEN=[]` (стаб `uv` Task 15
пишет значение; B1-11), а последующий вызов мержа `gh` — `GH_TOKEN` установки; App-режим
на PR без метки → 2; прежний режим (без `MERGE_APP_*`) не изменился (существующие тесты зелёные).
Стаб `uv` в App-тестах печатает две строки (`UV_STUB_OUT`) на вызов `act_app_token.py`.

- [ ] **Step 2: Run** `uv run --group governance pytest tests/test_act_app_token.py tests/test_merge_pr.py -v` → FAIL

- [ ] **Step 3: Implement** — `governance/act_app_token.py`:

```python
"""Личность GitHub App для обвязки мержа (спека §9.4) и самоотчёт установок (§6.2).

Исполняется `merge-pr.sh` файлом (форма Task 15). JWT — `pyjwt[crypto]`
(группа governance, пин в uv.lock); HTTP — stdlib `urllib`, без перенаправлений.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, "") and "governance" not in sys.modules:
    import importlib.util

    _pkg_dir = Path(__file__).resolve().parent
    _spec = importlib.util.spec_from_file_location(
        "governance", _pkg_dir / "__init__.py", submodule_search_locations=[str(_pkg_dir)]
    )
    assert _spec is not None and _spec.loader is not None
    _pkg = importlib.util.module_from_spec(_spec)
    sys.modules["governance"] = _pkg
    _spec.loader.exec_module(_pkg)

import argparse  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
from collections.abc import Callable, Mapping  # noqa: E402

from governance.act_time import Unavailable  # noqa: E402

API = "https://api.github.com"
PER_PAGE = 100
MAX_PAGES = 50
Http = Callable[..., tuple[int, object]]
Found = tuple[str, str] | Unavailable | None
"""Как `act_trust.Found`: нарушение `(роль репо, деталь)`, недоступность или чисто."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """3xx не исполняется: ответ возвращается кодом (Authorization не уезжает)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _http(method: str, path: str, auth: str, body: dict | None = None) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Accept": "application/vnd.github+json", "Authorization": auth,
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "devtools-act-app"})
    try:
        with _OPENER.open(req, timeout=30) as answer:
            return answer.status, json.loads(answer.read() or b"null")
    except urllib.error.HTTPError as err:
        return err.code, None
    except (OSError, ValueError) as exc:
        return 0, str(exc)


def _pages(http: Http, path: str, auth: str, field: str | None) -> list | Unavailable:
    """Все страницы списка: до первой неполной страницы (полнота по построению).

    `field` — ключ списка в объекте с `total_count` (тогда сумма сверяется с
    ним); `None` — ответ сам список. Больше `MAX_PAGES` — `Unavailable`."""
    items: list = []
    total: object = None
    for page in range(1, MAX_PAGES + 1):
        status, data = http("GET", f"{path}?per_page={PER_PAGE}&page={page}", auth)
        chunk = data.get(field) if field and isinstance(data, dict) else data
        if status != 200 or not isinstance(chunk, list):
            return Unavailable(f"GET {path} (стр. {page}): HTTP {status}")
        if field:
            total = data.get("total_count") if isinstance(data, dict) else None
        items.extend(chunk)
        if len(chunk) < PER_PAGE:
            if field and total != len(items):
                return Unavailable(f"GET {path}: {len(items)} из total_count={total!r}")
            return items
    return Unavailable(f"GET {path}: больше {MAX_PAGES} страниц")


def _jwt(app_id: int, key: Path, now: int) -> str:
    import jwt

    claims = {"iat": now - 60, "exp": now + 540, "iss": str(app_id)}
    return jwt.encode(claims, key.read_bytes(), algorithm="RS256")


def _token(http: Http, bearer: str, installation: int, body: dict | None = None) -> str | Unavailable:
    status, data = http("POST", f"/app/installations/{installation}/access_tokens", bearer, body)
    token = data.get("token") if isinstance(data, dict) else None
    if status != 201 or not isinstance(token, str):
        return Unavailable(f"токен установки {installation}: HTTP {status}")
    return token


def app_identity(app_id: int, key: Path, repo: str, *, http: Http = _http,
                 now: int | None = None) -> tuple[str, str] | Unavailable:
    """`(<slug>[bot], токен установки на repo)` — личность без `gh api user` (403)."""
    bearer = "Bearer " + _jwt(app_id, key, int(time.time()) if now is None else now)
    status, data = http("GET", "/app", bearer)
    slug = data.get("slug") if isinstance(data, dict) else None
    if status != 200 or not isinstance(slug, str) or not slug:
        return Unavailable(f"GET /app: HTTP {status}")
    status, data = http("GET", f"/repos/{repo}/installation", bearer)
    installation = data.get("id") if isinstance(data, dict) else None
    if status != 200 or type(installation) is not int:
        return Unavailable(f"установка App на {repo}: HTTP {status}")
    token = _token(http, bearer, installation)
    if isinstance(token, Unavailable):
        return token
    return f"{slug}[bot]", token


def installations_refusal(app_id: int, key: Path, trust_repos: Mapping[str, int], *,
                          http: Http = _http, now: int | None = None) -> Found:
    """Установка App, видящая репо доверия, — нарушение `(роль, деталь)` (§6.2:
    проверяется держатель ключа, не производный токен). Оба списка — со всеми
    страницами (ревизия b1, B1-12); неполный — `Unavailable`, не «чисто»."""
    bearer = "Bearer " + _jwt(app_id, key, int(time.time()) if now is None else now)
    installations = _pages(http, "/app/installations", bearer, None)
    if isinstance(installations, Unavailable):
        return installations
    for item in installations:
        ident = item.get("id") if isinstance(item, dict) else None
        if type(ident) is not int:
            return Unavailable("установка без id")
        token = _token(http, bearer, ident, {"permissions": {"metadata": "read"}})
        if isinstance(token, Unavailable):
            return token
        repos = _pages(http, "/installation/repositories", f"token {token}", "repositories")
        if isinstance(repos, Unavailable):
            return Unavailable(f"репо установки {ident}: {repos.reason}")
        seen = {r.get("id") for r in repos if isinstance(r, dict)}
        for role, repo_id in trust_repos.items():
            if repo_id in seen:
                return role, f"установка App {ident} видит {role} ({repo_id})"
    return None


def main(argv: list[str] | None = None) -> int:
    """CLI для `merge-pr.sh`: две строки — `<slug>[bot]` и токен; иначе код 2."""
    p = argparse.ArgumentParser(prog="act_app_token")
    p.add_argument("--app-id", required=True, type=int)
    p.add_argument("--key", required=True, type=Path)
    p.add_argument("--repo", required=True)
    args = p.parse_args(argv)
    got = app_identity(args.app_id, args.key, args.repo)
    if isinstance(got, Unavailable):
        print(f"act_app_token: {got.reason}", file=sys.stderr)
        return 2
    print(got[0])
    print(got[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

(Постраничность — оба списка читаются до первой неполной страницы (`per_page=100`); у
`/installation/repositories` сумма сверяется с `total_count`; больше `MAX_PAGES` страниц, ошибка
любой страницы или расхождение — `Unavailable`, а не «чисто» (ревизия b1, B1-12). Перенаправления
не исполняются: `_NoRedirect` возвращает 3xx кодом — заголовок `Authorization` не уходит на
другой хост.)

`merge-pr.sh` — профиль:

```sh
app_mode=0
if [ -n "${MERGE_APP_ID:-}" ] || [ -n "${MERGE_APP_KEY:-}" ]; then
    [ -n "${MERGE_APP_ID:-}" ] && [ -n "${MERGE_APP_KEY:-}" ] || die 2 "App-режим: \
нужны оба MERGE_APP_ID и MERGE_APP_KEY"
    app_mode=1
fi
```

preflight (вместо `gh api user` при `app_mode=1`; сам вызов — после чтения фактов PR, потому что
допустимость App-режима зависит от метки и формы ветки):

```sh
if [ "$app_mode" -eq 1 ]; then
    case "$head_ref" in
        $candidate_glob) has_label "$HUMAN_APPROVAL_LABEL" || die 2 "App-режим только для \
candidate review/v1 (метка $HUMAN_APPROVAL_LABEL)" ;;
        *) die 2 "App-режим только для candidate review/v1" ;;
    esac
    app_out=$(cd "$script_dir" && UV_PROJECT_ENVIRONMENT="$premerge_env" \
        uv run --frozen --exact --group governance --no-config --no-env-file \
        python -I "$script_dir/governance/act_app_token.py" \
        --app-id "$MERGE_APP_ID" --key "$MERGE_APP_KEY" --repo "$slug") \
        || die 2 "App-режим: личность App не установлена"
    login=$(printf '%s\n' "$app_out" | sed -n '1p')
    GH_TOKEN=$(printf '%s\n' "$app_out" | sed -n '2p')
    export GH_TOKEN
    merger_type="Bot"
fi
```

`gh_a()` в App-режиме не выставляет `GH_CONFIG_DIR` (токен — `GH_TOKEN`). Проверка
`${#GH_TOKEN} -gt 0` и формы `login` (`*[bot]`) — `die 2`. Запуск предпроверки (блок Task 15) в
App-режиме — отдельной строкой той же формы, с пустым `GH_TOKEN` и ключом App:

```sh
        if [ "$app_mode" -eq 1 ]; then
            premerge_out=$(cd "$script_dir" && GH_TOKEN="" \
                UV_PROJECT_ENVIRONMENT="$premerge_env" \
                uv run --frozen --exact --group governance --no-config --no-env-file \
                python -I "$script_dir/governance/act_premerge.py" \
                --repo "$slug" --pr "$pr" --head "$head_oid" --head-ref "$head_ref" \
                --merge-profile "$MERGE_GH_CONFIG_DIR" --merger "$login" \
                --merger-type "$merger_type" \
                --app-id "$MERGE_APP_ID" --app-key "$MERGE_APP_KEY") || premerge_rc=$?
        else
            premerge_out=$(…форма Task 15 без изменений…) || premerge_rc=$?
        fi
```

(`tests/harness_trust.py:151–158` уже снимает все ведущие присваивания окружения перед сверкой
формы запуска — префикс `GH_TOKEN=""` правки защищённого файла не требует; сверено ревью b2.)

`governance/act_premerge.py` — App-режим (функция перед `_parser`; `_parser` и `real_deps`):

```python
def app_installations(app_id: int, key: Path, *, http: Any = None) -> Installations:
    """Самоотчёт установок App у держателя ключа (§6.2; ревизия b1, B1-11).

    Производный токен установки предпроверка не держит (`merge-pr.sh` запускает
    её с `GH_TOKEN=""`), поэтому `_self_report` его не опрашивает; прочие
    credentials процесса (`GITHUB_TOKEN`, профили) проверяются как обычно."""
    from governance import act_app_token

    def check(binding: act_trust.Binding) -> act_trust.Found:
        repos = {"approval-policy": binding.approval_policy_repository_id,
                 "df-trust": binding.df_trust_repository_id}
        extra = {} if http is None else {"http": http}
        return act_app_token.installations_refusal(app_id, key, repos, **extra)

    return check
```

```python
    p.add_argument("--app-id", type=int, default=None)
    p.add_argument("--app-key", type=Path, default=None)
```

в `main` после разбора: `(args.app_id is None) != (args.app_key is None)` → `parser.error`
(«App-режим: нужны оба --app-id и --app-key»); `real_deps` добавляет
`installations=app_installations(args.app_id, args.app_key) if args.app_id is not None else
None`.

- [ ] **Step 4: Run** → PASS; `shellcheck merge-pr.sh`; полный набор.
- [ ] **Step 5: Commit**

```bash
git add merge-pr.sh governance/act_app_token.py governance/act_premerge.py pyproject.toml \
  uv.lock tests/test_merge_pr.py tests/test_act_app_token.py
git commit -m "feat(merge-pr): App-режим — личность через JWT и токен установки, самоотчёт установок"
```

### Task 16: Барьер миграции и отчёт затрагиваемых заявок

**Files:**
- Create: `governance/act_barrier.py` (+ `make act-barrier` в `Makefile`)
- Create: `tests/act_mutation.py` (обязательный мутационный проход по реестру, Step 4a); его
  результат — `docs/superpowers/plans/2026-10-12-approval-act-review.runs/act-mutation-<sha>.json`
  (рабочее пространство SDD ветки, коммитится вместе с задачей)
- Test: `tests/test_act_barrier.py`; `tests/test_act_identity_table.py` (мета-тест таблицы
  идентичности всей части B, ревизия b2 — последняя задача части B, когда все места суждения уже
  в коде)

**Interfaces:**
- Consumes: Tasks 10 (`act_settings`), 12a (`act_identity`), 13 (`approval_branches`), 14
  (`live_policy`); `Ops.prs_by_head_prefix` (вся история PR, GraphQL с пагинацией; сбой —
  `RuntimeError`), `Ops.pr_facts`, `Ops.repo_file_fact`, `Ops.brief_pr_fact` (файлы PR),
  `Ops.default_branch_fact`, `Ops.pr_comments_fact`, `Ops.comment`.
- Produces: `BarrierReport(items: tuple[str, ...], complete: bool, gaps: tuple[str, ...])`;
  `live_unmarked(product, ops, *, humans, confirmed, attested, draining, runs) -> BarrierReport`;
  `live_review_requests(products, ops) -> BarrierReport`;
  `invalidate(product, ops, pr, *, humans, caller) -> str`;
  CLI `python -m governance.act_barrier --product owner/repo [--confirm-resolved <pr>]…
  [--attest-drained <installation>]… [--invalidate <pr>] [--json]`; выходы: 0 — пусто и полно;
  1 — есть живые (печатает); 3 — неполно (печатает пробелы). Вызывает `darkfactory setup-repo
  apply` (спека B) в человеческом контексте — дважды: перед предложением PR активации и
  непосредственно перед его мержем.

Полнота — **из форджи**, локальные леджеры — дополнительно (другая установка локально не
видна):
1. Все PR продукта (`prs_by_head_prefix(product, "")`; сбой — `gaps`) формы candidate, finalize
   (`approval_branches`) или `brief/*`.
2. OPEN candidate/finalize без **действительного** манифеста `review/v1` этой заявки в голове
   (строгий `parse_manifest` + `act_identity.manifest_refusal` по идентичности
   `(product, ws_id, request_id)` из ветки; некорректный YAML, чужой воркстрим или ключ — не
   маркер; ревизия b1, B1-13), OPEN brief-PR (заявка v1; `v2` — Task 17) → `items`. MERGED
   legacy-candidate **разобран**, только если смёржен его finalize и **каждый узел, отданный
   candidate на одобрение**, — файлы диффа candidate со `status: approval_pending` в его голове,
   состав устанавливается независимо от finalize, — в голове базы несёт `status: approved` с
   подписью этого candidate (`approved_by = mergedBy`, `approved_at = mergedAt`) либо подпись
   более позднего акта (`approved_at > mergedAt` — узел покрыт позже); пустой состав —
   `items` («разберите вручную»); иначе — `items` («merged-but-unrecorded / pending-finalize»);
   неустановленный состав или голова — `gaps`. Пины узла барьер не судит (решение исполнителя
   плана Р-15: дрейф пинов после законного старого акта — долг узла, а не живая заявка; цена —
   узел с таким долгом пройдёт барьер и встанет на гейте доставки). Имя ветки finalize и его
   состав разбором не считаются.
3. Явная инвалидация (§10 п. 2, `act_migrated`): `--invalidate <pr>` (человеческий контекст)
   пишет в PR комментарий `RequestIdentity.migration_marker` —
   `<!-- act-migrated: <product>/<ws_id>/<request-key> -->`; барьер принимает маркер только
   дословно для идентичности этого PR, от логина из `human_approvers` **текущей активации** и
   без правок (`last_edited_at is None`); локальный леджер получает `invalidated: act_migrated`
   только в прогоне с тем же `(repo_slug, ws_id)` (B1-14: тот же ключ в другом воркстриме —
   другая заявка).
4. MERGED brief-PR с заявкой v1: штамп живёт только в `run_dir` установки; без локального
   доказательства (прогон с `interview.approval == {repo: product, pr}` и файлом
   `brief-approval/customer-brief.md`) — `items` с пометкой «требует подтверждения человека»,
   снимается только `--confirm-resolved <pr>` (флаг — только CLI человеческого контекста).
5. Локальные леджеры (`rs.all_run_ids`/`rs.load` прогонов продукта): живая заявка без маркера,
   ещё не дошедшая до PR, — `items`; нечитаемый леджер — `gaps`.
6. Предусловия полноты: `act_settings.load_settings(config_home(env)).draining` (иначе `gaps`
   «draining не включён» — пустой список без остановки создания ничего не доказывает) и
   `--attest-drained <installation>…` (иначе `gaps` «draining других установок не подтверждён»).

`live_review_requests` (предпросмотр Q9 для спеки B): живые заявки `review/v1` всех продуктов
источника — OPEN candidate с манифестом; недоступный продукт → `gaps`; пустой список при `gaps`
— никогда не «последствий нет».

- [ ] **Step 1: Write the failing tests** — `tests/test_act_barrier.py`:

```python
"""Барьер миграции по форджу (§10, Q9; Task 16)."""

from __future__ import annotations

import pytest

from governance import act_barrier
from governance import approval_branches as ab
from governance import run_state as rs
from governance.act_identity import RequestIdentity
from governance.act_request import NodeEntry, NodeRequest, PolicyPin, render_manifest
from governance.brief_facts import BriefPrFacts, DefaultBranch, PrComment
from governance.facts import Fact, Outcome

P = "owner/p"
HEAD = "1" * 40
TIP = "9" * 40
CAND = ab.candidate_branch("ws-7", 2, 1, 1)
FINAL = ab.finalize_branch("ws-7", 2, 1, 1)
NODE = "workstreams/ws-7/spec/10-requirements.md"
NODE2 = "workstreams/ws-7/spec/20-design.md"
IDENT = RequestIdentity(P, "ws-7", "approve-2-1-1")
MARK = IDENT.migration_marker
PENDING = "---\nstatus: approval_pending\n---\nтело\n"


@pytest.fixture(autouse=True)
def _runs(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Локальные леджеры — только стенда, не настоящего out/governance-runs."""
    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")


def manifest(ws_id: str = "ws-7", request_id: str = "approve-2-1-1", product: str = P) -> str:
    return render_manifest(NodeRequest(
        request_id, ws_id, product, {"requirements": NodeEntry(NODE, "h", 1)},
        {"requirements": {}}, PolicyPin("owner/approval-policy", "main", "a" * 40, {})))


def signed(by: str = "alice", at: str = "2026-10-01T10:00:00Z") -> str:
    return f"---\nstatus: approved\napproved_by: {by}\napproved_at: '{at}'\n---\nтело\n"


class Ops:
    def __init__(self, prs: list[dict], **kw: object) -> None:
        self.prs = prs
        self.files: dict[tuple[str, str], str] = {(HEAD, NODE): PENDING, **kw.get("files", {})}
        self.manifests: dict[str, str] = kw.get("manifests", {})  # head sha -> текст
        self.comments: dict[int, list[PrComment]] = kw.get("comments", {})
        self.pr_files: dict[int, tuple] = kw.get("pr_files", {})
        self.fail_list = kw.get("fail_list", False)
        self.posted: list[tuple[int, str]] = []

    def prs_by_head_prefix(self, slug: str, prefix: str) -> list[dict]:
        if self.fail_list:
            raise RuntimeError("graphql: pagination broken")
        return self.prs

    def pr_facts(self, slug: str, pr: int) -> dict:
        rec = next(p for p in self.prs if p["number"] == pr)
        return {"headRefOid": rec.get("head", HEAD), "mergedBy": {"login": "alice"},
                "mergedAt": "2026-10-01T10:00:00Z", "state": rec["state"]}

    def repo_file_fact(self, slug: str, sha: str, path: str) -> Fact[str]:
        if "/approvals/requests/" in path:
            text = self.manifests.get(sha)
        else:
            text = self.files.get((sha, path))
        return Fact(Outcome.FOUND, text, "ok") if text else Fact(Outcome.ABSENT, None, "нет")

    def brief_pr_fact(self, slug: str, pr: int) -> Fact[BriefPrFacts]:
        files = self.pr_files.get(pr, ((NODE, "MODIFIED"),))
        return Fact(Outcome.FOUND, BriefPrFacts(pr, "MERGED", "master", CAND, HEAD, files,
                                                "ai-prosto", "2026-10-01T10:05:00Z",
                                                "f" * 40), "ok")

    def default_branch_fact(self, slug: str) -> Fact[DefaultBranch]:
        return Fact(Outcome.FOUND, DefaultBranch("master", TIP), "ok")

    def pr_comments_fact(self, slug: str, pr: int) -> Fact[list[PrComment]]:
        return Fact(Outcome.FOUND, self.comments.get(pr, []), "ok")

    def comment(self, slug: str, pr: int, body: str) -> None:
        self.posted.append((pr, body))


def run(ops: Ops, **kw: object) -> act_barrier.BarrierReport:
    args = dict(humans=frozenset({"alice"}), confirmed=frozenset(), attested=("fleet",),
                draining=True, runs=())
    args.update(kw)
    return act_barrier.live_unmarked(P, ops, **args)


def pr(number: int, branch: str, state: str, **kw: object) -> dict:
    return {"number": number, "headRefName": branch, "state": state, "title": "", "body": "",
            **kw}


def test_identity_only_for_request_branches() -> None:
    """b4: идентичность заявки — только у веток формы candidate/finalize."""
    assert act_barrier._identity(P, CAND) == IDENT
    assert act_barrier._identity(P, FINAL) == IDENT
    assert act_barrier._identity(P, "brief/ws-7") is None


def test_without_attestation_incomplete() -> None:
    got = run(Ops([]), attested=())
    assert got.items == () and not got.complete
    assert got.gaps == ("draining других установок не подтверждён (--attest-drained)",)


def test_clean_and_attested_complete() -> None:
    got = run(Ops([]))
    assert got == act_barrier.BarrierReport((), True, ())


def test_draining_off_incomplete() -> None:
    got = run(Ops([]), draining=False)
    assert not got.complete and got.gaps == ("draining не включён (act.toml act_migration)",)


@pytest.mark.parametrize("branch,item", [
    (CAND, "#5 candidate OPEN без маркера: манифеста нет в голове; маркера миграции нет"),
    (FINAL, "#5 finalize OPEN без маркера: манифеста нет в голове; маркера миграции нет"),
    ("brief/ws-7", "#5 brief-PR OPEN — заявка v1 без маркера"),
], ids=["candidate", "finalize", "brief"])
def test_open_unmarked_stage_is_live(branch: str, item: str) -> None:
    """`_marked` × NO_MANIFEST_FILE (candidate/finalize); `_judge` × brief OPEN."""
    assert run(Ops([pr(5, branch, "OPEN")])).items == (item,)


def test_open_review_candidate_is_not_a_barrier() -> None:
    got = run(Ops([pr(5, CAND, "OPEN")], manifests={HEAD: manifest()}))
    assert got.items == ()


@pytest.mark.parametrize("text,reason", [
    ("act: review/v1\n", "манифест не разобран"),  # не манифест: строгий разбор
    ("schema: [не yaml\n", "манифест не разобран"),
    (manifest(ws_id="ws-8"), "манифест чужой заявки: манифест: ws_id"),
    (manifest(request_id="approve-2-1-2"), "манифест чужой заявки: манифест: request_id"),
    (manifest(product="owner/q"), "манифест чужой заявки: манифест: product"),
], ids=["not_manifest", "bad_yaml", "ws_id", "request_id", "product"])
def test_malformed_or_foreign_manifest_is_not_a_marker(text: str, reason: str) -> None:
    """B1-13/B1-14/b4: найденный файл — ещё не манифест этой заявки; причина — своя."""
    got = run(Ops([pr(5, CAND, "OPEN")], manifests={HEAD: text}))
    assert len(got.items) == 1 and reason in got.items[0]


def test_merged_legacy_with_signed_node_is_resolved() -> None:
    ops = Ops([pr(5, CAND, "MERGED"), pr(6, FINAL, "MERGED")],
              files={(TIP, NODE): signed()})
    assert run(ops).items == ()


def test_merged_legacy_without_signature_in_base_is_live() -> None:
    ops = Ops([pr(5, CAND, "MERGED"), pr(6, FINAL, "MERGED")],
              files={(TIP, NODE): signed(by="bob", at="2026-09-01T00:00:00Z")})
    assert run(ops).items == (f"#5 candidate MERGED: {NODE} — подпись другого акта "
                              "— merged-but-unrecorded",)


def test_unapproved_node_in_base_is_live() -> None:
    """B3-3: подпись этого акта есть, но статус узла в базе — не `approved` (`_signed`)."""
    text = signed().replace("status: approved", "status: approval_pending")
    ops = Ops([pr(5, CAND, "MERGED"), pr(6, FINAL, "MERGED")], files={(TIP, NODE): text})
    items = run(ops).items
    assert len(items) == 1 and "статус узла в базе не approved" in items[0]


def test_node_covered_later_is_resolved() -> None:
    ops = Ops([pr(5, CAND, "MERGED"), pr(6, FINAL, "MERGED")],
              files={(TIP, NODE): signed(by="bob", at="2026-10-05T00:00:00Z")})
    assert run(ops).items == ()


def test_partial_finalize_leaves_candidate_live() -> None:
    """B1-13: candidate отдал два узла; finalize подписал один — заявка не разобрана."""
    ops = Ops([pr(5, CAND, "MERGED"), pr(6, FINAL, "MERGED")],
              pr_files={5: ((NODE, "MODIFIED"), (NODE2, "MODIFIED")),
                        6: ((NODE, "MODIFIED"),)},
              files={(HEAD, NODE2): PENDING, (TIP, NODE): signed()})
    got = run(ops)
    assert got.items == (f"#5 candidate MERGED: {NODE2} — узла нет в базе "
                         "— merged-but-unrecorded",)


def test_empty_finalize_does_not_resolve() -> None:
    """B1-13: состав берётся из candidate, а не из (пустого) finalize."""
    ops = Ops([pr(5, CAND, "MERGED"), pr(6, FINAL, "MERGED")], pr_files={6: ()})
    assert run(ops).items == (f"#5 candidate MERGED: {NODE} — узла нет в базе "
                              "— merged-but-unrecorded",)


def test_candidate_without_pending_nodes_is_live() -> None:
    ops = Ops([pr(5, CAND, "MERGED"), pr(6, FINAL, "MERGED")],
              files={(HEAD, NODE): signed()})
    got = run(ops)
    assert len(got.items) == 1 and "узлов на одобрении не найдено" in got.items[0]


@pytest.mark.parametrize("finalize", [None, "OPEN", "CLOSED"], ids=["absent", "open", "closed"])
def test_merged_candidate_without_finalize_is_live(finalize: str | None) -> None:
    """B3-3: finalize нет / открыт / закрыт без мержа — заявка не разобрана."""
    prs = [pr(5, CAND, "MERGED")] + ([pr(6, FINAL, finalize)] if finalize else [])
    got = run(Ops(prs, files={(TIP, NODE): signed()}))
    assert got.items[0] == ("#5 candidate MERGED без смёрженного finalize — "
                            "pending-finalize")


def test_human_migration_marker_resolves() -> None:
    marker = PrComment("c1", "alice", MARK, "2026-10-10T00:00:00Z", None)
    assert run(Ops([pr(5, CAND, "OPEN")], comments={5: [marker]})).items == ()


@pytest.mark.parametrize("comment,reason", [
    (PrComment("c1", "ai-prosto", MARK, "t", None), "маркер не от human_approvers"),
    (PrComment("c1", "alice", MARK, "t", "2026-10-11"), "маркер правлен"),
    (PrComment("c1", "alice", "<!-- act-migrated: approve-2-1-1 -->", "t", None),
     "маркер не этой заявки"),
    (PrComment("c1", "alice", RequestIdentity(P, "ws-8", "approve-2-1-1").migration_marker,
               "t", None), "маркер не этой заявки"),
    (PrComment("c1", "alice", RequestIdentity(P, "ws-7", "approve-9-9-9").migration_marker,
               "t", None), "маркер не этой заявки"),
    (PrComment("c1", "alice", RequestIdentity("owner/q", "ws-7", "approve-2-1-1")
               .migration_marker, "t", None), "маркер не этой заявки"),
], ids=["author", "edited", "old_form", "ws_id", "request_id", "product"])
def test_marker_must_be_human_unedited_and_this_request(comment: PrComment, reason: str) -> None:
    """B1-14/b4: маркер агента, правленый, старой формы или с чужой идентичностью —
    отвергнут с причиной своей проверки."""
    got = run(Ops([pr(5, CAND, "OPEN")], comments={5: [comment]}))
    assert len(got.items) == 1 and got.items[0].endswith(reason)


def test_merged_brief_needs_human_confirmation() -> None:
    ops = Ops([pr(7, "brief/ws-7", "MERGED")])
    got = run(ops)
    assert len(got.items) == 1 and "--confirm-resolved 7" in got.items[0]
    assert run(ops, confirmed=frozenset({7})).items == ()


def _brief_runs(monkeypatch, tmp_path, runs: dict[str, tuple[dict, bool]]) -> None:
    """Локальные прогоны: `interview.approval` и наличие штампа брифа."""
    from types import SimpleNamespace

    states = {rid: SimpleNamespace(interview={"approval": approval}, repo_slug=P,
                                   ws_id="ws-7", ops={}) for rid, (approval, _s) in runs.items()}
    for rid, (_a, stamped) in runs.items():
        if stamped:
            (tmp_path / rid / "brief-approval").mkdir(parents=True)
            (tmp_path / rid / "brief-approval" / "customer-brief.md").write_text("x")
    monkeypatch.setattr(rs, "all_run_ids", lambda: list(states))
    monkeypatch.setattr(rs, "load", lambda rid: states[rid])
    monkeypatch.setattr(rs, "run_dir", lambda rid: tmp_path / rid)


@pytest.mark.parametrize("approval,stamped,reason", [
    ({"repo": P, "pr": 7}, True, None),
    ({"repo": "owner/q", "pr": 7}, True, "нет прогона с interview.approval этого PR"),
    ({"repo": P, "pr": 8}, True, "нет прогона с interview.approval этого PR"),
    ({"repo": P, "pr": 7}, False, "нет файла штампа брифа"),
], ids=["match", "other_product", "other_pr", "no_stamp"])
def test_local_brief_stamp_binds_product_pr_and_file(monkeypatch, tmp_path, approval,
                                                     stamped, reason) -> None:
    """B3-2/B3-3: штамп брифа доказывает разбор только своего продукта, своего PR и при
    наличии файла штампа (`_local_brief_stamp`); причина отказа — своя."""
    _brief_runs(monkeypatch, tmp_path, {"r1": (approval, stamped)})
    got = run(Ops([pr(7, "brief/ws-7", "MERGED")]))
    if reason is None:
        assert got.items == ()
    else:
        assert len(got.items) == 1 and reason in got.items[0]


def test_local_live_request_without_pr_is_item(monkeypatch) -> None:
    """B3-2: живая заявка локального леджера без маркера, не дошедшая до PR — пункт
    (`_local_items`); заявка другого продукта — нет."""
    from types import SimpleNamespace

    from governance import approval_ledger as al

    states = {"r1": SimpleNamespace(repo_slug=P), "r2": SimpleNamespace(repo_slug="owner/q")}
    monkeypatch.setattr(rs, "load", lambda rid: states[rid])
    monkeypatch.setattr(al, "live_requests", lambda state: [((2, 1, 1), {})])
    got = run(Ops([]), runs=("r1", "r2"))
    assert got.items == ("r1:approve-2-1-1 — заявка без маркера до PR",)


def test_unreadable_local_ledger_incomplete(monkeypatch) -> None:
    """B3-2: нечитаемый леджер прогона — пробел полноты, а не «пусто» (`live_unmarked`)."""
    def broken(run_id):
        raise ValueError("битый run.json")

    monkeypatch.setattr(rs, "load", broken)
    got = run(Ops([]), runs=("r1",))
    assert not got.complete and "леджер r1 не читается" in got.gaps[0]


def test_broken_pagination_incomplete() -> None:
    got = run(Ops([], fail_list=True))
    assert not got.complete and got.items == ()
    assert got.gaps == ("список PR owner/p неполон: graphql: pagination broken",)


def test_invalidate_posts_marker_for_human_only() -> None:
    ops = Ops([pr(5, CAND, "OPEN")])
    act_barrier.invalidate(P, ops, 5, humans=frozenset({"alice"}), caller="alice")
    assert ops.posted == [(5, MARK)]
    with pytest.raises(RuntimeError, match="human_approvers"):
        act_barrier.invalidate(P, ops, 5, humans=frozenset({"alice"}), caller="ai-prosto")


def test_invalidate_touches_only_selected_workstream(monkeypatch) -> None:
    """B1-14: тот же ключ approve-2-1-1 в двух воркстримах — инвалидируется один."""
    from types import SimpleNamespace

    from governance import approval_ledger as al

    states = {"r7": SimpleNamespace(repo_slug=P, ws_id="ws-7",
                                    ops={"approve-2-1-1": {"status": al.STATUS_STARTED}}),
              "r8": SimpleNamespace(repo_slug=P, ws_id="ws-8",
                                    ops={"approve-2-1-1": {"status": al.STATUS_STARTED}})}
    hit: list[str] = []
    monkeypatch.setattr(rs, "all_run_ids", lambda: list(states))
    monkeypatch.setattr(rs, "load", lambda run_id: states[run_id])
    monkeypatch.setattr(al, "invalidate_request",
                        lambda state, key, reason: hit.append(state.ws_id))
    act_barrier.invalidate(P, Ops([pr(5, CAND, "OPEN")]), 5, humans=frozenset({"alice"}),
                           caller="alice")
    assert hit == ["ws-7"]


def test_review_preview_unavailable_product_incomplete() -> None:
    good = Ops([pr(5, CAND, "OPEN")], manifests={HEAD: manifest()})
    bad = Ops([], fail_list=True)
    got = act_barrier.live_review_requests({"owner/p": good, "owner/b": bad})
    assert len(got.items) == 1 and not got.complete
    assert got.gaps == ("owner/b: список PR неполон — graphql: pagination broken",)
```

Мета-тест таблицы идентичности (ревизии b2–b4, Р-17: исчерпывающая классификация определений
части B по правилу «проверка-отказ — место суждения», точная причина у каждой строки; таблица в
разделе ревизии — человекочитаемая копия) — `tests/test_act_identity_table.py`:

```python
"""Таблица идентичности судимых объектов: исчерпывающая классификация определений
части B (ревизии b2–b4, Р-17; правило «класс, а не экземпляр»).

Механизм (ревизия b4), каждое свойство — отдельный мета-тест:

1. Состав. Рекурсивный обход AST (вложенные функции и классы, `async def`,
   определения под `if`/`try`) всех модулей части B и перечисленных дополнений
   части B в существующие модули; каждое определение — ровно в одном реестре.
2. Классификация по правилу, а не по имени. `NOT_JUDGMENT` допустим, только если в
   собственном теле функции нет условного пути к отказу (`raise`, `return
   Unavailable/Rejected/Violation/_Stop/LegacyHalt`, `cls(..., error=…)`,
   `_unresolved`, `_invalidate`, ответ `unavailable:`/`not_approved:`, условный
   `return False`, форма «отказ | None», отказ, присвоенный имени и возвращённый
   позже); ложные срабатывания — `GUARD_EXEMPT` с причиной. Детектор — ПЕРЕКРЁСТНАЯ
   проверка формы, а не доказательство полноты: отказ иной формы (доменный объект,
   вызов помощника, вернувший отказ) он не видит. Реестр ведёт ревьюер задачи: каждое
   новое определение части B и каждая новая проверка внутри изменённой существующей
   функции (`MODIFIED_EXISTING`) вносятся руками при ревью, с двойником.
3. Точная причина. Строка `JUDGMENT` = компонент × функция проверки × литерал
   проверки (встречается в её исходнике ровно один раз — то есть принадлежит одной
   проверке) × двойник × литерал утверждения: по AST литерал (или имя, связанное с
   ним) входит в `assert` о результате либо в `pytest.raises(..., match=…)` —
   утверждение только о типе или коде выхода не засчитывается; механизм проверяется
   собственными отрицательными двойниками (`test_reason_link_mechanism`).
4. Маркеры положительного суждения — перекрёстная проверка (`MARKERS`).

Мутационная проверка строк (обойти проверку → двойник падает на утверждении, а не
исключением) обязательна перед коммитом Task 16 по ВСЕМ строкам, включая стенд
`World` (`tests/act_mutation.py`, Task 16 Step 4a).
"""

# ruff: noqa: E501 — таблица данных: строка = место × компонент × тест, перенос её не читается

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULES = ("act_facts", "act_anon", "act_check", "act_trust", "act_journal", "act_context",
           "act_identity", "act_journal_wire", "act_status", "act_node", "act_premerge",
           "act_app_token", "act_barrier")
#: Определения, которые часть B добавляет в существующие модули (полный перечень по
#: листингам Tasks 11–16); изменённые существующие функции — в разделе ревизии плана.
EXISTING_ADDITIONS = {
    "ops": ("_gate_check_exe", "_act_args", "Ops.gate_snapshot_digest",
            "RealOps.gate_snapshot_digest", "Ops.first_parent_changes",
            "RealOps.first_parent_changes", "Ops.pr_body_fact", "RealOps.pr_body_fact",
            "RealOps._snapshot_url", "RealOps._credential_helper", "RealOps._snapshot_env",
            "RealOps._snapshot_git", "Ops.snapshot_open", "RealOps.snapshot_open",
            "Ops.snapshot_reader", "RealOps.snapshot_reader", "Ops.snapshot_close",
            "RealOps.snapshot_close", "SnapshotOps.__init__", "SnapshotOps.__getattr__",
            "SnapshotOps._git", "SnapshotOps.first_parent_changes", "SnapshotOps.show_file",
            "SnapshotOps.show_file_bytes", "SnapshotOps.rev_parse", "SnapshotOps.fetch_branch",
            "_parse_first_parent"),
    "approval_ledger": ("record_review_act", "audit_events", "record_audit", "successor_of"),
    "act_decision": ("find_act", "merger_allowed"),
    "act_envelope": ("expected_envelope", "node_history"),
    "task_bridge": ("_admission_refusal", "_require_admission"),
    "act_settings": ("config_home",),
    "approve_node": ("_legacy_outcome", "_review_debt"),
}
#: Изменённые частью B СУЩЕСТВУЮЩИЕ функции (не дополнения): новая проверка — кортеж имён,
#: каждое — ключ `JUDGMENT` `<функция>#<имя>`; новой проверки нет — причина строкой. Список
#: ведёт ревьюер задачи (детектор формы его не доказывает); сверено с листингами Tasks 11–16.
MODIFIED_EXISTING = {
    "approve_node._propose": ("act_incompatible", "other_pin", "status_unavailable",
                              "successor_joins_live", "mode_unavailable",
                              "legacy_noop_review"),
    "approve_node.read_dag_state": ("mode_unset", "status_unavailable", "review_debt"),
    "approve_node._require_upstream_ready": ("mode_unset", "upstream_unavailable",
                                             "upstream_not_approved"),
    "approve_node._try_agent_merge": ("form",),
    "runner._step_gate": ("act_mode_unset", "completed_admission"),
    "runner._step_s8": ("act_mode_unset", "completed_admission", "run_status"),
    "runner._next_wave": ("admission",),
    "act_envelope.envelope_refusal": ("bytes", "pins"),
    "approve_node._advance": "маршрутизация в `act_node.route` до выбора шага; своей проверки нет",
    "approve_node._reconcile_candidate": "проверки вынесены в `_legacy_outcome` (Task 14 Step 3a), новых нет",
    "approve_node.propose_from_source": "делегирует `act_node.creation` (место суждения); своей проверки нет",
    "approve_node._sync_branch_to_snapshot": "пишет манифест из `act_node.manifest_file`; проверок нет",
    "approve_node.approve_node": "пробрасывает `predecessor` в `_propose`; проверок нет",
    "approval_ledger.start_request": "пишет поле `predecessor`; проверок нет",
    "task_bridge._canonical_dag_hash": "хеш (В-1), не суждение",
    "task_bridge._content_anchor": "пробрасывает `ws_id`; не суждение",
    "task_bridge.deliver_superseded": ("review_noop", "replacement_repeat", "returned_pr",
                                       "returned_merged", "returned_open", "redo_intent",
                                       "returned_v1"),
    "task_bridge.deliver_for_run": ("completed", "adopted"),
    "act_envelope.current_event": "выводится из `node_history` (п. 9 (Б), 01.11); своей проверки нет",
    "act_envelope.next_generation": "выводится из `node_history` (п. 9 (Б), 01.11); своей проверки нет",
    "ops.RealOps.gate_check_s8": "аргументы `--act-mode`/`--node-status`; не суждение",
    "ops.RealOps.gate_check_candidate": "аргументы `--act-mode`/`--node-status`; не суждение",
    "bundle_state.candidate_state": "`act_mode=\"legacy\"` в `collect_bundle`; не суждение",
}

S, N, L = "tests/test_act_status.py", "tests/test_act_node.py", "tests/test_act_legacy.py"
E, TB = "tests/test_act_envelope.py", "tests/test_governance_task_bridge.py"
RN = "tests/test_governance_runner.py"
P, B, C = "tests/test_act_premerge.py", "tests/test_act_barrier.py", "tests/test_act_check.py"
T, J, W = "tests/test_act_trust.py", "tests/test_act_journal.py", "tests/test_act_journal_wire.py"
I, A, G = "tests/test_act_identity.py", "tests/test_act_anon.py", "tests/test_act_gate.py"
X, K, F = "tests/test_act_context.py", "tests/test_act_app_token.py", "tests/test_act_facts.py"
RP, OB = "tests/test_act_repropose.py", "tests/test_act_ops_body.py"
ENVELOPE_FIELDS = ("node", "request_id", "generation", "manifest_path", "head_sha",
                   "self_hash", "upstream_pins", "merge_commit", "review_id", "policy")
ENV_CHECK = {"node": "подставлен для", "request_id": "конверт другого поколения",
             "generation": "конверт другого поколения"}


def r(component, check, lit, twin, tlit=None):
    """Строка: компонент, функция проверки, литерал проверки, двойник, литерал assert."""
    return (component, check, lit, twin, lit if tlit is None else tlit)


JUDGMENT = {
    # --- act_facts: граница библиотеки steward
    "act_facts._call": (r("library_exception", "act_facts._call", "except Exception",
                          f"{F}::test_call_turns_exception_into_unavailable",
                          '"steward read_act_facts: OSError: socket"'),),
    "act_facts._own": (r("steward_unavailable", "act_facts._own", "isinstance(got, _v3().Unavailable)",
                         f"{F}::test_own_translates_steward_unavailable", '"transport"'),),
    # --- act_anon
    "act_anon.AnonTransport.send": (
        r("memo_only_sha", "act_anon.AnonTransport.send", "_IMMUTABLE.match(path)",
          f"{A}::test_memo_only_for_sha_addressed_paths", "t.sent == [commit, pulls, pulls]"),
        r("relative_get", "act_anon.AnonTransport.send", "_UNSAFE_PATH.search(path)",
          f"{A}::test_only_relative_get", '"только GET относительного пути"'),
    ),
    "act_anon._response": (r("size_limit", "act_anon._response", "len(raw) > MAX_BODY",
                             f"{A}::test_oversized_response_refused", '"anon: ответ больше 8 байт"'),),
    "act_anon.AnonTransport._fetch": (r("transport_failure", "act_anon.AnonTransport._fetch",
                                        "except Exception as exc",
                                        f"{A}::test_transport_failure_is_status_none", '"TimeoutError"'),),
    "act_anon.contents_bytes": (
        r("blob_oid", "act_anon.contents_bytes", "содержимое не равно blob",
          f"{A}::test_contents_bytes_checked_against_blob_oid"),
        r("absent", "act_anon.contents_bytes", "resp.status == 404",
          f"{A}::test_contents_absent_vs_unreadable", "is None"),
        r("unreadable", "act_anon.contents_bytes", "resp.status != 200",
          f"{A}::test_contents_absent_vs_unreadable", ": HTTP 403"),
    ),
    # --- act_check / act_decision (Task 12)
    "act_check.check_delivery": (
        r("not_merged", "act_check.check_delivery", "not ctx.delivery.merged",
          f"{C}::test_not_merged_waits", "WaitingDelivery()"),
        r("product", "act_check._pinned_policy", "ctx.request.product != ctx.product",
          f"{C}::test_manifest_product_must_be_delivery_repo", "REQUEST_BINDING"),
        r("act_head", "act_decision.decide", "if act is None",
          f"{C}::test_new_head_after_approve_is_no_act", '"d1_no_act"'),
    ),
    "act_check.live_policy_refusal": (r("current_activation", "act_check.live_policy_refusal",
                                        "pin.activation_commit == current_activation",
                                        f"{C}::test_live_pin_against_current_activation",
                                        "POLICY_CHANGED"),),
    "act_check._admission": (
        r("mode_after_review", "act_check._admission", "isinstance(admission, Refused)",
          f"{C}::test_unmarked_after_review_mode_refused", "ADMISSION"),
        r("marker_before_bootstrap", "act_check._admission", "ctx.request_act is None",
          f"{C}::test_before_bootstrap_marked_rejected", '"marker_before_mode"'),
    ),
    "act_check._epoch_at": (r("interepoch_gap", "act_check._epoch_at", "epoch.valid_until is not None",
                              f"{C}::test_interepoch_gap_refused_for_review", "EPOCH_GAP"),),
    "act_check._span": (r("activation_order", "act_check._span", "later.at < earlier.at",
                          f"{C}::test_activations_out_of_order_unavailable",
                          "merged_at активаций противоречит first-parent"),),
    "act_check._pinned_policy": (
        r("activation_pin", "act_check._pinned_policy",
          "pin.activation_commit != seen[-1].merge_commit",
          f"{C}::test_policy_changed_before_merge", "POLICY_CHANGED"),
        r("mixed_set", "act_check._pinned_policy", "dict(pin.files_sha256) != dict(policy.files_sha256)",
          f"{C}::test_mixed_policy_refused", '"mixed_policy"'),
    ),
    "act_check._decide": (
        r("approver", "act_decision.decide", "if act is None",
          f"{C}::test_approver_outside_list_rejected", '"d1_no_act"'),
        r("dismissal", "act_decision.decide", "if act is None",
          f"{C}::test_dismissed_before_merge_rejected", '"d1_no_act"'),
        r("merger", "act_decision.decide", "not _merger_ok(policy, login",
          f"{C}::test_agent_outside_merger_list", '"d3_merger"'),
        r("bytes", "act_decision._delivery_refusal", "if at_merge != at_head",
          f"{C}::test_bytes_and_content_are_distinct_refusals", '"i_bytes"'),
        r("content", "act_decision._delivery_refusal",
          "self_hashes[entry.path] != entry.content_hash",
          f"{C}::test_bytes_and_content_are_distinct_refusals", '"i_content"'),
    ),
    "act_check._legacy_verdict": (r("legacy_missing", "act_check._legacy_verdict", "ctx.legacy is None",
                                    f"{C}::test_old_reader_without_legacy_verdict_unavailable",
                                    "вердикт старой проверки не вычислен"),),
    # --- act_trust (Task 12a)
    "act_trust.parse_binding": (
        r("transport", "act_trust.parse_binding", 'data["transport"] != "public"',
          f"{T}::test_binding_rejects_bad_text[transport]", '"только transport"'),
        r("keys", "act_trust.parse_binding", 'set(data) - {"schema"} != set(_BINDING_KEYS)',
          f"{T}::test_binding_rejects_bad_text[keys]", '"ключи"'),
        r("ids", "act_trust.parse_binding", "data[k] <= 0",
          f"{T}::test_binding_rejects_bad_text[ids]", '"положительные целые"'),
    ),
    "act_trust.load_binding": (
        r("owner", "act_trust.load_binding", "st.st_uid != owner_uid",
          f"{T}::test_binding_symlink_or_foreign_owner_refused", "не файл root"),
        r("absent", "act_trust.load_binding", "except FileNotFoundError",
          f"{T}::test_binding_absent_is_none", "is None"),
    ),
    "act_trust._get": (r("http_status", "act_trust._get", "resp.status != 200",
                         f"{T}::test_branch_head_unreadable_is_unavailable", "branches/main: HTTP 500"),),
    "act_trust._named": (
        r("full_name", "act_trust._named", 'body.get("full_name") != expected',
          f"{T}::test_root_substitution_refused[df_trust_name]", '"привязка —"'),
        r("public", "act_trust._named", 'body.get("private") is not False',
          f"{T}::test_root_substitution_refused[private_root]", '"только публичные"'),
    ),
    "act_trust._chain": (r("commit_identity", "act_trust._chain", 'body.get("sha") != sha',
                           f"{T}::test_commit_answer_for_other_sha_unavailable", "ответ не о том коммите"),),
    "act_trust._journal_versions": (r("journal_deleted", "act_trust._journal_versions", "if versions:",
                                      f"{T}::test_journal_deleted_after_appearing_unavailable",
                                      "epochs.yaml удалён"),),
    "act_trust._checked_epochs": (
        r("bootstrap_first", "act_trust._checked_epochs",
          "first[0].anchor != source.bootstrap_activation",
          f"{T}::test_first_version_must_be_bootstrap", "не bootstrap"),
        r("transition", "act_trust._checked_epochs", "except JournalError",
          f"{T}::test_rewritten_closed_epoch_in_history_unavailable", "недопустимый переход журнала"),
    ),
    "act_trust.read_trust_snapshot": (r("head_named", "act_trust.read_trust_snapshot",
                                        "len(head) != 40",
                                        f"{T}::test_branch_head_not_named_is_unavailable",
                                        '"голова main не прочитана"'),),
    "act_trust.gh_get": (
        r("gh_failed", "act_trust.gh_get", "done.returncode != 0",
          f"{T}::test_gh_get_failures_are_unavailable", "(profile:agent): boom"),
        r("not_json", "act_trust.gh_get", "except ValueError",
          f"{T}::test_gh_get_failures_are_unavailable", "ответ не JSON"),
    ),
    "act_trust._self_report": (
        r("push_on_policy", "act_trust._self_report", "isinstance(verdict, Violation)",
          f"{T}::test_token_with_push_records_and_blocks", 'startswith("env:GH_TOKEN на ")'),
        r("push_on_df_trust", "act_trust._self_report", "isinstance(verdict, Violation)",
          f"{T}::test_merge_profile_with_push_on_df_trust", 'startswith("profile:merge на ")'),
        r("unreadable", "act_trust._self_report", "isinstance(verdict, Unavailable) and gap is None",
          f"{T}::test_self_report_404_is_unavailable_without_record",
          'startswith("самоотчёт profile:agent")'),
    ),
    "act_trust._rulesets": (r("pull_request_rule", "act_trust._rulesets",
                              "missing := sorted(REQUIRED_RULES - kinds)",
                              f"{T}::test_ruleset_without_pull_request_is_violation", '"нет правил"'),),
    "act_trust._rebase": (
        r("rebase_true", "act_trust._rebase", "if value is True",
          f"{T}::test_rebase_true_is_violation", '"allow_rebase_merge = true"'),
        r("rebase_hidden", "act_trust._rebase", "if value is None",
          f"{T}::test_rebase_hidden_is_unverified_premise", '("allow_rebase_merge",)'),
    ),
    "act_trust._register": (r("violation_event", "act_trust._register", "нарушение периметра: {detail}",
                              f"{T}::test_token_with_push_records_and_blocks",
                              'startswith("нарушение периметра: ")'),),
    "act_trust._snapshot_for_decision": (
        r("blocked", "act_trust._snapshot_for_decision", 'state != "clear"',
          f"{T}::test_blocked_journal_blocks", '"периметр заблокирован"'),
        r("stale", "act_trust._snapshot_for_decision", "state == STALE",
          f"{T}::test_stale_snapshot_reloads", '["clear"]'),
    ),
    "act_trust.load_trust": (r("violation_wins", "act_trust.load_trust", "isinstance(found, tuple)",
                               f"{T}::test_violation_wins_over_other_unavailable", '"df-trust"'),),
    "act_trust.policy_set": (
        r("bytes_vs_activation", "act_trust.policy_set",
          "hashlib.sha256(raw).hexdigest() != digest",
          f"{T}::test_policy_bytes_not_matching_activation_is_mixing", '"смешение версий"'),
        r("actors_file", "act_trust.policy_set", "if digest is None",
          f"{T}::test_activation_without_actors_file_unavailable", '": нет policy/actors.yaml"'),
    ),
    "act_trust.load_ranges": (
        r("history_violation", "act_trust.load_ranges", "isinstance(got, act_facts.HistoryBreak)",
          f"{T}::test_history_violation_records_event", '"нарушение периметра"'),
        r("unknown_anchor", "act_trust.load_ranges", "якорь эпохи {epoch.epoch}",
          f"{T}::test_unknown_anchor_time_makes_range_unavailable", '"якорь эпохи 1: object_missing"'),
    ),
    "act_trust._modes": (r("unread_set", "act_trust._modes", "набор {a.merge_commit[:12]}",
                           f"{T}::test_mode_history_with_unread_set_is_unavailable",
                           'startswith(f"набор {views[1].merge_commit[:12]}")'),),
    "act_trust.product_mode": (
        r("never_entered", "act_trust.product_mode", "if not entered",
          f"{T}::test_mode_never_entered_is_pre_migration", "PRE_MIGRATION"),
        r("removed", "act_trust.product_mode", 'tail[-1] == ""',
          f"{T}::test_mode_removed_or_rolled_back_is_unavailable[merge_removed]",
          '"удалён после первой записи"'),
        r("rolled_back", "act_trust.product_mode", '"review" in tail',
          f"{T}::test_mode_removed_or_rolled_back_is_unavailable[rolled_back]",
          '"обратное переключение review → merge"'),
        r("prior_epoch", "act_trust.product_mode", "история режима, эпоха",
          f"{T}::test_mode_needs_prior_epoch_unless_review", '"история режима, эпоха 1"'),
    ),
    "act_trust.current_activation": (r("open_epoch", "act_trust.current_activation", "if not span",
                                       f"{T}::test_current_activation_is_last_of_open_epoch_or_unavailable",
                                       '"в открытой эпохе нет активаций"'),),
    # --- журналы периметра
    "act_journal.ForgeEpochs.head": (r("root", "act_journal.ForgeEpochs.head",
                                       "snap.root.id_tuple != tuple(root)",
                                       f"{J}::test_forge_epochs_refuse_other_root", '"root_mismatch"'),),
    "act_journal._status_of": (
        r("snapshot_identity", "act_journal._status_of", "ответ не о снимке решения",
          f"{J}::test_status_answer_about_other_snapshot_unavailable"),
        r("state", "act_journal._status_of", 'state not in ("blocked", "clear")',
          f"{J}::test_status_answer_about_other_snapshot_unavailable", '"периметр: x"'),
    ),
    "act_journal.DevJournal._open": (r("store", "act_journal.DevJournal._open",
                                       "except (store.Unavailable, OSError)",
                                       f"{J}::test_corrupted_store_blocks",
                                       'startswith("журнал периметра (dev): ")'),),
    "act_journal.DevJournal.status": (
        r("stale", "act_journal._status_of", '"stale_snapshot"',
          f"{J}::test_stale_snapshot_reported", "act_trust.STALE"),
        r("lost", "act_journal.DevJournal._open", "except (store.Unavailable, OSError)",
          f"{J}::test_deleted_store_with_marker_is_lost", '"lost"'),
    ),
    "act_journal.DevJournal.record": (r("store_refused", "act_journal.DevJournal.record",
                                        "except store.Unavailable as exc",
                                        f"{J}::test_dev_record_refused_by_store_is_unavailable",
                                        '"журнал периметра (dev): integrity"'),),
    "act_journal.InstalledJournal.status": (r("df_run", "act_journal.journal_for", "if not df_run",
                                              f"{J}::test_installed_journal_requires_df_run",
                                              '"DF_RUN_ID"'),),
    "act_journal.InstalledJournal.record": (r("refused", "act_journal.InstalledJournal.record",
                                              'resp.outcome != "done"',
                                              f"{J}::test_installed_record_refused_is_unavailable",
                                              '"record-violation: refused: busy"'),),
    "act_journal.journal_for": (r("df_run", "act_journal.journal_for", "if not df_run",
                                  f"{J}::test_installed_journal_requires_df_run", '"DF_RUN_ID"'),),
    "act_journal_wire.WireJournal._call": (r("garbled", "act_journal_wire.WireJournal._call",
                                             'resp.get("schema") != RESPONSE_SCHEMA',
                                             f"{W}::test_wire_garbled_or_refused_answers_unavailable",
                                             '"ответ издателя не разобран"'),),
    "act_journal_wire.WireJournal.status": (r("snapshot_identity", "act_journal_wire.WireJournal.status",
                                              "ответ не о снимке решения",
                                              f"{W}::test_wire_stale_and_foreign_snapshot"),),
    "act_journal_wire.WireJournal.record": (r("refused", "act_journal_wire.WireJournal.record",
                                              'resp.get("outcome") != "done"',
                                              f"{W}::test_wire_garbled_or_refused_answers_unavailable",
                                              '"record-violation: refused: busy"'),),
    "act_journal_wire.DevStoreJournal._open": (
        r("restore", "act_journal_wire.DevStoreJournal._open", "(self.base / RESTORE_INTENT).exists()",
          f"{W}::test_restore_intent_and_corruption_unavailable", '"restore_incomplete"'),
        r("missing", "act_journal_wire.DevStoreJournal._open", "except FileNotFoundError",
          f"{W}::test_missing_store_is_unavailable", '"не создан"'),
    ),
    "act_journal_wire.DevStoreJournal.status": (r("blocking", "act_journal_wire.DevStoreJournal.status",
                                                  '"blocked" if rows else "clear"',
                                                  f"{W}::test_narrow_pending_blocks_and_full_journal_completes_it",
                                                  '"blocked"'),),
    "act_journal_wire.DevStoreJournal.record": (r("insert_failed", "act_journal_wire.DevStoreJournal.record",
                                                  "except sqlite3.Error as exc",
                                                  f"{W}::test_record_insert_failure_is_unavailable",
                                                  '"событие не записано"'),),
    "act_journal_wire.journal_for_premerge": (r("df_run", "act_journal_wire.journal_for_premerge",
                                                "if not df_run",
                                                f"{W}::test_premerge_selection_follows_publisher_presence",
                                                '"DF_RUN_ID"'),),
    # --- act_context
    "act_context.delivery_verdict": (
        r("not_merged", "act_context.delivery_verdict", "delivery.merged_at is None",
          f"{X}::test_not_merged_waits_without_reading_ranges", "WaitingDelivery()"),
        r("legacy_only_unmarked", "act_context.delivery_verdict", "if request is None and legacy is not None",
          f"{X}::test_legacy_computed_only_for_unmarked", "calls == [1]"),
    ),
    # --- act_identity
    "act_identity.manifest_refusal": tuple(
        r(c, "act_identity.manifest_refusal", "if got != want",
          f"{I}::test_each_identity_component_is_checked[{c}]", 'f"манифест: {field} other"')
        for c in ("product", "ws_id", "request_id")),
    "act_identity.paths_refusal": (
        r("node_in_dag", "act_identity.paths_refusal", "if want is None",
          f"{I}::test_node_outside_dag_and_wrong_path", '"не из активного DAG"'),
        r("node_path", "act_identity.paths_refusal", "if entry.path != want",
          f"{I}::test_node_outside_dag_and_wrong_path", '"путь узла"'),
    ),
    "act_identity.head_manifest_refusal": (
        r("in_head", "act_identity.head_manifest_refusal", "if text_at_head is None",
          f"{I}::test_manifest_only_in_delivery_or_changed", '"не в одобренном head"'),
        r("bytes", "act_identity.head_manifest_refusal", "if text_at_head != text_delivered",
          f"{I}::test_manifest_only_in_delivery_or_changed", '"≠"'),
    ),
    # --- act_status (Task 14)
    "act_status.pr_for_merge": (r("exactly_one", "act_status.pr_for_merge", "len(found) != 1",
                                  f"{S}::test_pr_for_merge_needs_exactly_one", '"нужен ровно один"'),),
    "act_status._pr_head": (r("head_fetched", "act_status._pr_head", "не догружена",
                              f"{S}::test_history_fetches_squash_head_absent_locally"),),
    "act_status._event": (
        r("request:path", "act_status._event", "манифест лежит не по своему пути",
          f"{S}::test_history_manifest_off_its_path_unavailable[ws_id]"),
        r("request:no_pr", "act_status._event", "if isinstance(pr, Unavailable)",
          f"{S}::test_history_manifest_commit_without_pr_unavailable", '"нужен ровно один"'),
        r("immutable", "act_status._event", 'if status != "A"',
          f"{S}::test_manifest_edited_after_delivery_unavailable", '"запись истории изменена после доставки"'),
        r("stop:ws_id", "act_status._event", "if not path.startswith((requests_dir, stops_dir))",
          f"{S}::test_history_stop_of_other_workstream_ignored", '== ()'),
        r("stop:path", "act_status._event", "остановка лежит не по своему пути",
          f"{S}::test_history_stop_off_its_path_unavailable[node]"),
    ),
    "act_status.load_history": (r("base_unread", "act_status.load_history", "if rows is None",
                                  f"{S}::test_history_unreadable_base_unavailable",
                                  '"история master: first-parent не прочитан"'),),
    "act_status.Decider._binding": (
        r("identity", "act_status.Decider._binding", "if identity is not None",
          f"{S}::test_decider_refuses_unbound_manifest[foreign_ws]", '"манифест: ws_id"'),
        r("head_manifest", "act_status.Decider._binding", "if copy is not None",
          f"{S}::test_decider_refuses_unbound_manifest[absent_in_head]", '"не в одобренном head"'),
        r("upstream_pins", "act_status.Decider._binding", "dict(req.upstream_pins[node]) != fact.value",
          f"{S}::test_decider_refuses_unbound_manifest[pins_changed]", '"upstream-пины"'),
    ),
    "act_status.Decider.__call__": (
        r("memo_key", "act_status.Decider.__call__", "key = (*ident.memo_key, event.merge_commit)",
          f"{S}::test_decider_memo_key_carries_full_identity", "decider._memo"),
        r("memo_request", "act_status.Decider.__call__", "key = (*ident.memo_key, event.merge_commit)",
          f"{S}::test_decider_memo_is_per_request_not_per_commit[good_first]",
          "act_status.REQUEST_BINDING"),
    ),
    "act_status.Decider.trust": (r("binding", "act_status.Decider.trust", "if binding is None",
                                   f"{S}::test_decider_without_binding_trust_unavailable",
                                   '"нет привязки корня доверия (§6.1 п. 1)"'),),
    "act_status.Decider._head_hashes": (r("head_bytes", "act_status.Decider._head_hashes", "if text is None",
                                          f"{S}::test_decider_head_bytes_unreadable_unavailable",
                                          "не читается"),),
    "act_status.Decider._decide": (
        r("merge_commit", "act_status.Decider._decide", "delivery.merge_commit != event.merge_commit",
          f"{S}::test_decider_delivery_of_other_merge_commit_unavailable", "доставил"),
        r("head_sha", "act_status.Decider._decide", "head_sha=event.head_sha",
          f"{S}::test_decider_judges_review_on_event_head", '"h" * 40'),
    ),
    "act_status.Decider.legacy": (r("contract_result", "act_status.Decider.legacy", "legacy=contract",
                                    f"{L}::test_decider_legacy_returns_contract_result", "is refused"),),
    "act_status.candidate_identity": (
        r("ssot_roundtrip", "act_status.candidate_identity",
          "approval_branches.candidate_branch(m[\"ws\"], w, k, a) != branch",
          f"{L}::test_candidate_identity_round_trips_ssot_form",
          'act_status.candidate_identity("spec/ws-7-approve-01-0-1") is None'),
    ),
    "act_status.legacy_contract_refusal": (
        r("pending", "act_status.legacy_contract_refusal", "return NOT_PENDING",
          f"{L}::test_node_not_pending_in_candidate_refused", "act_status.NOT_PENDING"),
        r("bytes", "act_status.legacy_contract_refusal", "return BYTES",
          f"{L}::test_node_bytes_changed_by_finalize_refused", "act_status.BYTES"),
        r("hash_field", "act_status.legacy_contract_refusal", "return HASH_FIELD",
          f"{L}::test_hash_field_not_matching_base_bytes_refused", "act_status.HASH_FIELD"),
        r("pins", "act_status.legacy_contract_refusal", "return PINS",
          f"{L}::test_signature_under_changed_pins_refused", "act_status.PINS"),
    ),
    "act_status.LegacyProbe.__call__": (
        r("edit_in_history", "act_status.LegacyProbe.__call__", "_no(NO_EDIT",
          f"{L}::test_no_edit_of_node_in_history_refused", "act_status.NO_EDIT"),
        r("signature_commit", "act_status.LegacyProbe.__call__", "_no(NOT_SIGNATURE",
          f"{L}::test_frontmatter_signature_differs_from_last_edit", "act_status.NOT_SIGNATURE"),
    ),
    "act_status.LegacyProbe._finalize_branch": (
        r("via_pr", "act_status.LegacyProbe._finalize_branch", "_no(NOT_VIA_PR",
          f"{L}::test_signature_not_through_pr_refused", "act_status.NOT_VIA_PR"),
        r("finalize_form", "act_status.LegacyProbe._finalize_branch", "_no(NOT_FINALIZE",
          f"{L}::test_signature_without_finalize_pr_refused", "act_status.NOT_FINALIZE"),
    ),
    "act_status.LegacyProbe._candidate": (
        r("ws_id", "act_status.LegacyProbe._candidate", "_no(FOREIGN_WS",
          f"{L}::test_finalize_of_other_workstream_refused", "act_status.FOREIGN_WS"),
        r("candidate_exists", "act_status.LegacyProbe._candidate", "_no(NO_CANDIDATE",
          f"{L}::test_candidate_absent_or_not_matching_signature[absent]", "act_status.NO_CANDIDATE"),
        r("candidate_merged", "act_status.LegacyProbe._candidate", "_no(CANDIDATE_NOT_MERGED",
          f"{L}::test_candidate_absent_or_not_matching_signature[not_merged]",
          "act_status.CANDIDATE_NOT_MERGED"),
        r("candidate_merger", "act_status.LegacyProbe._candidate", "_no(CANDIDATE_MERGER",
          f"{L}::test_candidate_absent_or_not_matching_signature[other_merger]",
          "act_status.CANDIDATE_MERGER"),
        r("candidate_time", "act_status.LegacyProbe._candidate", "_no(CANDIDATE_TIME",
          f"{L}::test_candidate_absent_or_not_matching_signature[other_time]",
          "act_status.CANDIDATE_TIME"),
        r("ledger_record", "act_status.LegacyProbe._candidate", "_no(NO_LEDGER",
          f"{L}::test_ledger_record_binds_candidate_and_node[absent]", "act_status.NO_LEDGER"),
        r("ledger_candidate", "act_status.LegacyProbe._candidate", "_no(LEDGER_CANDIDATE",
          f"{L}::test_ledger_record_binds_candidate_and_node[other_candidate]",
          "act_status.LEDGER_CANDIDATE"),
        r("ledger_node", "act_status.LegacyProbe._candidate", "_no(LEDGER_NODE",
          f"{L}::test_ledger_record_binds_candidate_and_node[node_absent]", "act_status.LEDGER_NODE"),
    ),
    "act_status.LegacyProbe._offered": (r("node_in_candidate", "act_status.LegacyProbe._offered",
                                          "_no(NOT_IN_CANDIDATE",
                                          f"{L}::test_node_not_in_candidate_refused",
                                          "act_status.NOT_IN_CANDIDATE"),),
    "act_status.LegacyProbe._verdict": (r("admission", "act_status.LegacyProbe._verdict",
                                          'f"legacy_admission:{verdict.code}"',
                                          f"{L}::test_candidate_delivered_after_activation_refused",
                                          '"legacy_admission:"'),),
    "act_status.LegacyProbe._verdict.contract": (r("byte_contract", "act_status.LegacyProbe._verdict.contract",
                                                   "if refusal is not None",
                                                   f"{L}::test_node_bytes_changed_by_finalize_refused",
                                                   "act_status.BYTES"),),
    "act_status.Statuses.of": (r("memo_node", "act_status.Statuses.of", "if node not in self._memo",
                                 f"{S}::test_statuses_memo_is_per_node[approved_first]", '"нет конверта"'),),
    "act_status.Statuses.is_review": (r("no_event", "act_status.Statuses.is_review",
                                        "current_event(self.history, node) is not None",
                                        f"{S}::test_legacy_node_needs_provenance_not_frontmatter",
                                        '== (False, True)'),),
    "act_status.Statuses._legacy": (r("debt_first", "act_status.Statuses._legacy", "if debt is not None",
                                      f"{S}::test_legacy_probe_not_called_for_debt_node",
                                      '"not_approved:debt_status"'),),
    "act_envelope.node_history": (  # п. 9 (Б), решение владельца 01.11
        r("stop_generation", "act_envelope.node_history",
          "isinstance(event, DeliveredStop) and (gen != last or last == 0)",
          f"{E}::test_stop_of_other_generation_is_noop",
          '"узел req: остановка поколения 1, действует 2"'),
        r("stop_without_request", "act_envelope.node_history",
          "isinstance(event, DeliveredStop) and (gen != last or last == 0)",
          f"{E}::test_stop_before_any_request_ignored",
          '"узел req: остановка поколения 1, действует 0"'),
        r("request_generation", "act_envelope.node_history",
          "isinstance(event, DeliveredRequest) and gen <= last",
          f"{E}::test_non_monotonic_request_ignored",
          '"узел req: поколение 2 не больше прежнего 2"'),
    ),
    "act_status.fresh_base": (
        r("snapshot_unavailable", "act_status.fresh_base",
          "fact.outcome is not FactOutcome.FOUND or fact.value is None",
          f"{RN}::test_admission_refused_when_base_not_refreshed[s4]", '"no_success"'),
        r("closed", "act_status.fresh_base", "ops.snapshot_close(repo)",
          f"{S}::test_snapshot_removed_on_exception", "Path(seen[0]).exists() is False"),
    ),
    "ops.RealOps._snapshot_url": (r("canonical", "ops.RealOps._snapshot_url",
                                    'f"https://github.com/{repo_slug}.git"', f"{S}::test_production_snapshot_command_is_isolated",
                                    '"https://github.com/owner/p.git"'),),
    "ops.RealOps._credential_helper": (r("gh_profile", "ops.RealOps._credential_helper",
                                         '"!gh auth git-credential"', f"{S}::test_production_snapshot_command_is_isolated",
                                         '"credential.helper=!gh auth git-credential"'),),
    "ops.RealOps._snapshot_env": (  # d5: изолированное окружение git снимка
        r("allowlist", "ops.RealOps._snapshot_env", "for k in _SNAPSHOT_ENV_KEEP if k in os.environ",
          f"{S}::test_injected_git_config_and_env_have_no_effect", "STOPPED"),
        r("no_global", "ops.RealOps._snapshot_env", "GIT_CONFIG_GLOBAL=os.devnull", f"{S}::test_production_snapshot_command_is_isolated",
          '"0", str(profile)'),
        r("no_prompt", "ops.RealOps._snapshot_env", 'GIT_TERMINAL_PROMPT="0"', f"{S}::test_production_snapshot_command_is_isolated",
          '"0", str(profile)'),
        r("empty_home", "ops.RealOps._snapshot_env", "HOME=str(home)",
          f"{S}::test_foreign_home_credentials_have_no_effect",
          "snapshot_home == expected"),
        r("profile_resolved", "ops.RealOps._snapshot_env", "profile.expanduser().resolve()",
          f"{S}::test_foreign_home_credentials_have_no_effect",
          "snapshot_home == expected"),
        r("runner_profile", "ops.RealOps._snapshot_env", 'os.environ.get("REVIEW_GH_CONFIG_DIR")', f"{S}::test_production_snapshot_command_is_isolated",
          '"0", str(profile)'),
    ),
    "ops.RealOps._snapshot_git": (r("helper_reset", "ops.RealOps._snapshot_git",
                                    '"credential.helper="', f"{S}::test_production_snapshot_command_is_isolated",
                                    '"credential.helper=!gh auth git-credential"'),),
    "ops.SnapshotOps.__getattr__": (r("deny", "ops.SnapshotOps.__getattr__",
                                      "if name in SNAPSHOT_FORGE_METHODS",
                                      f"{S}::test_snapshot_ops_denies_unlisted_methods[blob_in_commit]",
                                      "не разрешён"),),
    "ops.RealOps.snapshot_open": (  # d3–d5: свежий изолированный снимок базы продукта
        r("slug_shape", "ops.RealOps.snapshot_open", "not _REPO_SLUG.fullmatch(repo_slug)",
          f"{S}::test_snapshot_rejects_non_slug_source[local_path]", "не owner/repo"),
        r("fetch_failed", "ops.RealOps.snapshot_open", "done.returncode != 0",
          f"{S}::test_noop_refusal_refuses_unfetchable_product",
          '"свежая база: снимок owner/p@master: fetch owner/p master: код 128"'),
        r("own_repo", "ops.RealOps.snapshot_open", 'tempfile.mkdtemp(prefix="act-snapshot-")',
          f"{S}::test_interleaved_snapshots_are_independent", "len(set(dirs)) == 2"),
        r("refmap", "ops.RealOps.snapshot_open", "*_SNAPSHOT_FETCH, url",
          f"{S}::test_snapshot_leaves_product_repo_untouched", "_refs(clone) == refs_before"),
    ),
    "act_status.noop_refusal": (  # В-1, d1, d2 — решение владельца 01.11
        r("stale_base", "act_status.noop_refusal", "isinstance(snapshot, Unavailable)",
          f"{RN}::test_admission_refused_when_base_not_refreshed[s8]", '"no_success"'),
        r("fresh_ref", "act_status.noop_refusal", "ref=ref",
          f"{RN}::test_stale_clone_remote_stop_end_to_end[next_wave]", '"no_success"'),
        r("history_snapshot", "act_status._statuses", "load_history(ops, state, snapshot)",
          f"{S}::test_noop_refusal_reads_fresh_product_base", "STOPPED"),
        r("legacy_snapshot", "act_status._statuses",
          "LegacyProbe(state, ops, snapshot, decider.legacy, dag)",
          f"{S}::test_admission_reads_only_the_snapshot", "node_reads == snapshot_refs"),
        r("mode_unset", "act_status.noop_refusal", "isinstance(live, Unavailable)",
          f"{TB}::test_review_noop_refused_when_mode_unset",
          '"бесследного no-op нет (режим акта продукта: нет привязки)"'),
        r("stopped", "act_status.noop_refusal", "verdict != APPROVED",
          f"{TB}::test_review_noop_refused_without_fresh_approval[stop_delivered]",
          '"not_approved:stopped:g1"'),
        r("act_revoked", "act_status.noop_refusal", "verdict != APPROVED",
          f"{TB}::test_review_noop_refused_without_fresh_approval[act_revoked]",
          '"not_approved:act:d1_no_act"'),
        r("unavailable", "act_status.noop_refusal", "verdict != APPROVED",
          f"{TB}::test_review_noop_refused_without_fresh_approval[trust_unavailable]",
          '"unavailable:df-trust: HTTP 403"'),
    ),
    "task_bridge._require_admission": (r("refused", "task_bridge._require_admission",
                                         "if refusal is not None",
                                         f"{TB}::test_stored_success_requires_admission[returned_pr]",
                                         '"допуск review/v1 не подтверждён — узел requirements"'),),
    "act_status.Statuses._compute": (
        r("stop", "act_status.Statuses._compute", "isinstance(current, DeliveredStop)",
          f"{S}::test_stop_makes_node_not_approved", '"not_approved:stopped:g1"'),
        r("upstream", "act_status.Statuses._compute", "if status != APPROVED",
          f"{S}::test_stopped_upstream_with_unchanged_bytes_blocks_downstream",
          '"not_approved:upstream_stopped:requirements"'),
        r("delivery_verdict", "act_status.Statuses._compute", "not isinstance(verdict, Accepted)",
          f"{S}::test_rejected_delivery_not_approved", '"not_approved:act:d1_no_act"'),
    ),
    "act_status.Statuses._envelope": (
        *(r(f"envelope:{f}", "act_envelope.envelope_refusal",
            ENV_CHECK.get(f, "конверт не совпадает с манифестом заявки"),
            f"{S}::test_envelope_component_substituted_not_approved[{f}]", "reason in got")
          for f in ENVELOPE_FIELDS),
        r("ws_id", "act_envelope.envelope_refusal", "нет конверта — не одобрен",
          f"{S}::test_envelope_of_other_workstream_not_used", '"нет конверта"'),
        r("node_bytes", "act_envelope.envelope_refusal", "байты узла изменились после одобрения",
          f"{S}::test_provider_twin_changed_candidate_bytes_not_approved"),
        r("current_pins", "act_envelope.envelope_refusal", "upstream-пины изменились после одобрения",
          f"{S}::test_cascade_by_pins_without_a_history_record"),
        r("generation", "act_envelope.envelope_refusal", "конверт другого поколения",
          f"{S}::test_previous_generation_envelope_with_same_bytes_refused"),
    ),
    "act_status._blobs.read": (r("inputs_unread", "act_status._blobs.read", "прямые входы {node}",
                                 f"{S}::test_blobs_reader_unavailable_names_node",
                                 '"прямые входы requirements: объект недоступен"'),),
    "act_status.live_policy": (r("binding", "act_status.live_policy", "deps.binding is None",
                                 f"{G}::test_no_binding_is_unavailable_not_legacy",
                                 '"нет привязки корня доверия"'),),
    "act_status.gate_run": (
        r("legacy_mode", "act_status.gate_run", 'live.mode != "review"',
          f"{G}::test_pre_migration_and_merge_are_legacy", '("legacy", None)'),
        r("snapshot_digest", "act_status.gate_run", 'not digest.startswith("sha256:")',
          f"{G}::test_snapshot_digest_failure_stops", '"gate-check --print-snapshot-digest: код 2: boom"'),
    ),
    # --- act_node (Task 13, 13a)
    "act_node.creation": (
        r("mode_unset", "act_node.creation", "isinstance(live, Unavailable)",
          f"{N}::test_unset_mode_refuses_creation", '"режим акта"'),
        r("draining", "act_node.creation", "if settings.draining",
          f"{N}::test_draining_refuses_unmarked_creation", "draining"),
        r("review_v1_off", "act_node.creation", "if not settings.review_v1",
          f"{N}::test_review_mode_without_review_v1_refuses", "review_v1"),
    ),
    "act_node._manifest": (r("parseable", "act_node._manifest", "except RequestError",
                             f"{N}::test_unreadable_manifest_in_head_invalidates",
                             '"манифест в одобренном коммите не читается"'),),
    "act_node._binding_refusal": (
        r("identity", "act_node._binding_refusal", "if refusal is not None",
          f"{N}::test_foreign_identity_binding_invalidates[ws_id]", '"манифест: ws_id ws-other"'),
        r("upstream_pins", "act_node._binding_refusal", "dict(req.upstream_pins[node]) != fact.value",
          f"{N}::test_foreign_identity_binding_invalidates[upstream_pins]", '"upstream-пины"'),
    ),
    "act_node._check_marked": (r("act_mirror", "act_node._check_marked",
                                 '(op.get("act") == ACT_REVIEW_V1) != (req is not None)',
                                 f"{N}::test_act_removed_from_ledger_with_manifest_in_head",
                                 '"зеркало act в run.json расходится"'),),
    "act_node.route": (r("merged_unmarked_mode", "act_node.route", "live.mode == act_trust.PRE_MIGRATION",
                         f"{N}::test_merged_unmarked_with_unset_mode_stays_alive", '"режим акта"'),),
    "act_node._open": (
        r("mode", "act_node._open", 'live.mode != "review"',
          f"{N}::test_open_candidate_in_merge_mode_refused", '"а режим продукта сейчас — merge"'),
        r("live_pin", "act_node._open", "refusal is not None",
          f"{N}::test_live_request_with_old_pin_invalidated", '"policy_changed"'),
        r("head", "act_node._open", "facts.head_sha != head",
          f"{N}::test_open_candidate_head_moved_between_reads", '"сдвинулась между чтениями"'),
        r("act", "act_node._open", "if act is None",
          f"{N}::test_open_without_approve_waits", "WAITING_HUMAN_APPROVAL"),
    ),
    "act_node._legacy.verdict": (r("halt", "act_node._legacy.verdict", "isinstance(got, an.LegacyHalt)",
                                   f"{N}::test_legacy_halt_is_unresolved", '"политика недоступна"'),),
    "act_node._head_hashes": (r("head_bytes", "act_node._head_hashes", "if text is None",
                                f"{N}::test_head_hashes_unreadable_node_unavailable", "не читается"),),
    "act_node._merged": (r("rejected", "act_node._merged", "isinstance(outcome, Rejected)",
                           f"{N}::test_legacy_candidate_merged_after_review_activation_refused",
                           "STATUS_INVALIDATED"),),
    "act_node._record_act": (r("recorded_act", "act_node._record_act",
                               '(op["review_id"], op["merge_commit"]) != (accepted.review_id',
                               f"{N}::test_recorded_act_differs_from_decision_unresolved",
                               '"записан акт 99/"'),),
    "act_node.envelope_commit_defect": (
        r("paths", "act_node.envelope_commit_defect", "set(touched) != set(want)",
          f"{N}::test_envelope_commit_defect_twins[extra_path]", "is not None"),
        r("bytes", "act_node.envelope_commit_defect", "ops.show_file(state.target_dir, head, path) != text",
          f"{N}::test_envelope_commit_defect_twins[bytes]", "is not None"),
    ),
    "act_node._finalize": (r("commit_made", "act_node._finalize", "if head is None:\n            raise",
                             f"{N}::test_envelope_commit_not_made_is_refused",
                             '"коммит конверта не состоялся"'),),
    "act_node._reconcile_finalize": (
        *(r(f"envelope:{f}", "act_node._reconcile_finalize", "if got != text",
            f"{N}::test_finalize_delivered_envelope_component_substituted_invalidates[{f}]",
            '"не тот конверт"') for f in ENVELOPE_FIELDS),
        r("current_admission", "act_node._reconcile_finalize", "if refusal is not None",
          f"{N}::test_stop_between_act_and_finalize_merge_not_reported_approved",
          '"узлы не одобрены — узел charter: not_approved:stopped:g1"'),
    ),
    "act_node._require_in_base": (r("in_base", "act_node._require_in_base", "if not reached",
                                    f"{N}::test_finalize_merged_outside_base_invalidates", '"не в базу"'),),
    "act_node.reopen": (
        *(r(c, "act_node.reopen", "ops.show_file(state.target_dir, event.value.commit, path) != record",
            f"{N}::test_reopen_refuses_stop_pr_without_record[{c}]", '"записи остановки"')
          for c in ("none", "other_node", "other_generation", "other_reason")),
        r("in_base", "act_node.reopen", "смёржен не в базу — узел",
          f"{N}::test_reopen_refuses_stop_pr_outside_base", '"не в базу"'),
        r("current_event_after", "act_node.reopen", "current_event(after, node) != stopped",
          f"{N}::test_reopen_new_event_between_read_and_stop_check", '"не эта остановка"'),
        r("already_stopped", "act_node.reopen", "not isinstance(current, DeliveredRequest)",
          f"{N}::test_reopen_already_delivered_stop_is_idempotent", "is None"),
    ),
    "act_node.repropose": (
        r("unknown", "act_node.repropose", "if op is None",
          f"{RP}::test_unknown_request_refused", '"нет в леджере"'),
        r("completed", "act_node.repropose", 'op.get("status") == al.STATUS_COMPLETED',
          f"{RP}::test_completed_request_refused", '"--reopen"'),
    ),
    # --- act_premerge (Task 15)
    "act_premerge.request_of_branch": (
        r("form", "act_premerge.request_of_branch", "if match is None",
          f"{P}::test_request_of_branch", '"feat/x"'),
    ),
    "act_premerge._trust": (
        r("binding", "act_premerge._trust", "deps.binding is None",
          f"{P}::test_no_binding_refused", '"без корня доверия"'),
        r("journal", "act_premerge._trust", "isinstance(deps.journal, Unavailable)",
          f"{P}::test_journal_unavailable_is_unset", '"журнал периметра: "'),
        r("app_unknown", "act_premerge._trust", "установки App: ",
          f"{P}::test_app_installations_unknown_is_unset", '"установки App: "'),
        r("app_violation", "act_premerge._trust", "if found is not None",
          f"{P}::test_app_installation_violation_recorded", "act_premerge.REGISTERED"),
    ),
    "act_premerge._live": (
        r("mode_unset", "act_premerge._live", "isinstance(mode, Unavailable)",
          f"{P}::test_product_mode_gate[unset]", '"режим продукта: "'),
        r("not_review", "act_premerge._live", 'if mode != "review"',
          f"{P}::test_product_mode_gate[merge]", '"— не review"'),
    ),
    "act_premerge._check": (
        r("identity", "act_premerge._check", "if refusal is not None",
          f"{P}::test_manifest_of_other_identity_refused[ws_id]", 'f"манифест: {field}"'),
        r("manifest_present", "act_premerge._check", "if text is None",
          f"{P}::test_no_manifest_refused", '"нет манифеста"'),
        r("merger", "act_premerge._check", "not merger_allowed(policy, args.merger",
          f"{P}::test_merger_outside_allowlist_refused", '"не пройдёт D3"'),
        r("skew", "act_premerge._check", "wait > MAX_WAIT",
          f"{P}::test_clock_skew_beyond_ten_seconds_unset", '"часы расходятся"'),
        r("reread", "act_premerge._check", "again != facts or current2 != current",
          f"{P}::test_reread_sees_dismissal_unset", '"изменились за время ожидания"'),
    ),
    "act_premerge._act": (
        r("head_sha", "act_premerge._act", "facts.head_sha != args.head",
          f"{P}::test_review_facts_head_differs_refused", '"≠ --head"'),
        r("act", "act_premerge._act", "find_act(args.head, effective, policy) is None",
          f"{P}::test_no_approve_refused", '"нет approve человека"'),
    ),
    # --- act_app_token (Task 15a)
    "act_app_token._pages": (
        r("completeness", "act_app_token._pages", "status != 200 or not isinstance(chunk, list)",
          f"{K}::test_full_last_page_requires_empty_next", '"стр. 2"'),
        r("total_count", "act_app_token._pages", "field and total != len(items)",
          f"{K}::test_repositories_total_mismatch_is_unavailable", '"total_count"'),
    ),
    "act_app_token._token": (r("token", "act_app_token._token", "status != 201",
                               f"{K}::test_installation_token_refused_is_unavailable",
                               '"токен установки 9: HTTP 502"'),),
    "act_app_token.app_identity": (
        r("app", "act_app_token.app_identity", "status != 200 or not isinstance(slug, str)",
          f"{K}::test_identity_unreadable_is_unavailable", '"GET /app: HTTP 403"'),
        r("installation", "act_app_token.app_identity", "type(installation) is not int",
          f"{K}::test_installation_unreadable_is_unavailable", '"установка App на o/p: HTTP 404"'),
    ),
    "act_app_token.installations_refusal": (
        r("trust_repo", "act_app_token.installations_refusal", "if repo_id in seen",
          f"{K}::test_installation_on_trust_repo_is_violation", '"установка App 9 видит approval-policy (22)"'),
        r("second_page", "act_app_token.installations_refusal", "if repo_id in seen",
          f"{K}::test_violation_on_second_installations_page", '"установка App 9 видит df-trust (11)"'),
    ),
    # --- act_barrier (Task 16)
    "act_barrier._marked": (
        r("absent", "act_barrier._marked", "Unmarked(NO_MANIFEST_FILE)",
          f"{B}::test_open_unmarked_stage_is_live[candidate]", "манифеста нет в голове"),
        r("unparsed", "act_barrier._marked", "Unmarked(f\"{MANIFEST_UNPARSED}",
          f"{B}::test_malformed_or_foreign_manifest_is_not_a_marker[bad_yaml]", '"манифест не разобран"'),
        r("foreign", "act_barrier._marked", "if refusal is not None",
          f"{B}::test_malformed_or_foreign_manifest_is_not_a_marker[ws_id]",
          '"манифест чужой заявки: манифест: ws_id"'),
    ),
    "act_barrier._migrated": (
        r("author", "act_barrier._migrated", "c.author not in humans",
          f"{B}::test_marker_must_be_human_unedited_and_this_request[author]", '"маркер не от human_approvers"'),
        r("edited", "act_barrier._migrated", "c.last_edited_at is not None",
          f"{B}::test_marker_must_be_human_unedited_and_this_request[edited]", '"маркер правлен"'),
        r("identity", "act_barrier._migrated", "c.body.strip() != ident.migration_marker",
          f"{B}::test_marker_must_be_human_unedited_and_this_request[ws_id]", '"маркер не этой заявки"'),
    ),
    "act_barrier._signed": (
        r("status", "act_barrier._signed", 'meta.get("status") != "approved"',
          f"{B}::test_unapproved_node_in_base_is_live", '"статус узла в базе не approved"'),
        r("signer", "act_barrier._signed", "later > at",
          f"{B}::test_merged_legacy_without_signature_in_base_is_live", "подпись другого акта"),
    ),
    "act_barrier._candidate_nodes": (r("pending", "act_barrier._candidate_nodes", 'meta.get("status") == PENDING',
                                       f"{B}::test_candidate_without_pending_nodes_is_live",
                                       '"узлов на одобрении не найдено"'),),
    "act_barrier._legacy_resolved": (
        r("finalize_merged", "act_barrier._legacy_resolved", 'final["state"] != "MERGED"',
          f"{B}::test_merged_candidate_without_finalize_is_live[open]", '"pending-finalize"'),
        r("node_in_base", "act_barrier._legacy_resolved", "text.outcome is not Outcome.FOUND",
          f"{B}::test_partial_finalize_leaves_candidate_live", "узла нет в базе"),
        r("nodes_found", "act_barrier._legacy_resolved", "if not nodes",
          f"{B}::test_candidate_without_pending_nodes_is_live", '"узлов на одобрении не найдено"'),
    ),
    "act_barrier._judge": (
        r("brief_open", "act_barrier._judge", "brief-PR OPEN — заявка v1 без маркера",
          f"{B}::test_open_unmarked_stage_is_live[brief]"),
        r("brief_merged", "act_barrier._judge", "if stamp is None",
          f"{B}::test_merged_brief_needs_human_confirmation", '"--confirm-resolved 7"'),
        r("open_unmarked", "act_barrier._judge", "OPEN без маркера: {marked.reason}",
          f"{B}::test_open_unmarked_stage_is_live[finalize]", "OPEN без маркера: манифеста нет в голове"),
    ),
    "act_barrier._local_brief_stamp": (
        r("run", "act_barrier._local_brief_stamp",
          'approval.get("repo") == product and approval.get("pr") == number',
          f"{B}::test_local_brief_stamp_binds_product_pr_and_file[other_product]",
          '"нет прогона с interview.approval этого PR"'),
        r("file", "act_barrier._local_brief_stamp", "stamped.is_file()",
          f"{B}::test_local_brief_stamp_binds_product_pr_and_file[no_stamp]", '"нет файла штампа брифа"'),
    ),
    "act_barrier._local_items": (
        r("live_local", "act_barrier._local_items", 'op.get("act") is None and op.get("candidate_pr") is None',
          f"{B}::test_local_live_request_without_pr_is_item", '"r1:approve-2-1-1 — заявка без маркера до PR"'),
        r("unreadable", "act_barrier._local_items", "леджер {run_id} не читается",
          f"{B}::test_unreadable_local_ledger_incomplete", '"леджер r1 не читается"'),
    ),
    "act_barrier.live_unmarked": (
        r("draining", "act_barrier.live_unmarked", "if not draining",
          f"{B}::test_draining_off_incomplete", '"draining не включён (act.toml act_migration)"'),
        r("attestation", "act_barrier.live_unmarked", "if not attested",
          f"{B}::test_without_attestation_incomplete", '"draining других установок не подтверждён'),
        r("pagination", "act_barrier.live_unmarked", "список PR {product} неполон",
          f"{B}::test_broken_pagination_incomplete", '"список PR owner/p неполон'),
    ),
    "act_barrier.live_review_requests": (r("product_gaps", "act_barrier.live_review_requests",
                                           "{product}: список PR неполон",
                                           f"{B}::test_review_preview_unavailable_product_incomplete",
                                           '"owner/b: список PR неполон'),),
    "act_barrier.invalidate": (
        r("human", "act_barrier.invalidate", "if caller not in humans",
          f"{B}::test_invalidate_posts_marker_for_human_only", '"human_approvers"'),
        r("ws_id", "act_barrier.invalidate", "(state.repo_slug, state.ws_id) != (ident.product, ident.ws_id)",
          f"{B}::test_invalidate_touches_only_selected_workstream", 'hit == ["ws-7"]'),
    ),
    # --- дополнения части B в существующие модули
    "approve_node._legacy_outcome": (
        r("old_allowlist", "approve_node._legacy_outcome", "signature.outcome is Outcome.FORBIDDEN",
          f"{L}::test_merger_outside_old_allowlist_refused", '"legacy_signature"'),
        r("old_pin_missing", "approve_node._legacy_outcome", "if not pinned",
          f"{L}::test_old_pin_missing_refused", '"legacy_unpinned"'),
        r("old_pin_substituted", "approve_node._legacy_outcome", 'snapshot.fingerprint != pinned["fingerprint"]',
          f"{L}::test_old_pin_substituted_is_unavailable", '"unavailable:политика"'),
        r("old_pin_unavailable", "approve_node._legacy_outcome", "policy.outcome is Outcome.UNAVAILABLE",
          f"{L}::test_old_pin_unavailable_is_unavailable", '"unavailable:политика подписи"'),
        r("old_pin_superseded", "approve_node._legacy_outcome", "refusal.kind == af.POLICY_REFUSAL_SUPERSEDED",
          f"{L}::test_old_pin_superseded_is_policy_changed", '"policy_changed"'),
        r("snapshot_published", "approve_node._legacy_outcome", "if not published",
          f"{L}::test_snapshot_not_published_in_candidate_refused", '"legacy_unpublished"'),
    ),
    "approval_ledger.successor_of": (r("single_successor", "approval_ledger.successor_of", "len(found) > 1",
                                       f"{RP}::test_two_successors_refused", '"несколько преемников"'),),
    "act_barrier._identity": (r("request_form", "act_barrier._identity", "if m is None",
                                f"{B}::test_identity_only_for_request_branches", '"brief/ws-7") is None'),),
    "ops.RealOps.first_parent_changes": (r("git_failed", "ops.RealOps.first_parent_changes",
                                           "if done.returncode != 0",
                                           f"{S}::test_first_parent_unreadable_is_none",
                                           '"master") is None'),),
    # --- новые проверки части B внутри изменённых существующих функций (ключ `<функция>#<проверка>`)
    "approve_node._propose#act_incompatible": (r("act", "approve_node._propose",
                                                 'joined[1].get("act") != made.act',
                                                 f"{N}::test_join_live_legacy_request_refused",
                                                 '"в одну заявку они не соединяются"'),),
    "approve_node._propose#other_pin": (r("policy_pin", "approve_node._propose",
                                          'joined[1]["policy"] != policy_record',
                                          f"{N}::test_join_request_pinned_to_other_activation_refused",
                                          '"закрепила другую активацию"'),),
    "approve_node._propose#status_unavailable": (r("status", "approve_node._propose",
                                                   "статус узла {node_id}: {verdict}",
                                                   f"{N}::test_review_status_unavailable_blocks_proposal",
                                                   '"статус узла charter: unavailable:история"'),),
    "approve_node._propose#successor_joins_live": (r("successor", "approve_node._propose",
                                                     "predecessor is not None and joined is not None",
                                                     f"{RP}::test_successor_refused_when_step_has_live_request",
                                                     '"уже есть — продвиньте её"'),),
    "approve_node.read_dag_state#mode_unset": (r("mode", "approve_node.read_dag_state",
                                                 "unresolved=f\"режим акта продукта: {live.reason}\"",
                                                 f"{N}::test_read_dag_state_mode_unset",
                                                 '"режим акта продукта: нет привязки корня доверия"'),),
    "approve_node.read_dag_state#status_unavailable": (r("status", "approve_node.read_dag_state",
                                                         'if verdict.startswith("unavailable:")',
                                                         f"{N}::test_read_dag_state_status_unavailable",
                                                         '"unavailable:история"'),),
    "approve_node.read_dag_state#review_debt": (r("debt", "approve_node.read_dag_state",
                                                  "debts.append(_review_debt(node, texts[node], verdict))",
                                                  f"{N}::test_read_dag_state_review_debt",
                                                  '"act:d1_no_act"'),),
    "approve_node._require_upstream_ready#mode_unset": (r("mode", "approve_node._require_upstream_ready",
                                                          "режим акта продукта (upstream {node_id})",
                                                          f"{N}::test_upstream_readiness_in_review[mode_unset]",
                                                          "режим акта продукта"),),
    "approve_node._require_upstream_ready#upstream_unavailable": (r("status", "approve_node._require_upstream_ready",
                                                                    "upstream {up} узла {node_id}: {verdict}",
                                                                    f"{N}::test_upstream_readiness_in_review[upstream_unavailable]",
                                                                    "upstream charter узла"),),
    "approve_node._require_upstream_ready#upstream_not_approved": (r("debt", "approve_node._require_upstream_ready",
                                                                     "не одобрен (review/v1)",
                                                                     f"{N}::test_upstream_readiness_in_review[upstream_not_approved]",
                                                                     "не одобрен"),),
    "approve_node._try_agent_merge#form": (r("form", "approve_node._try_agent_merge",
                                             "form() if form is not None",
                                             f"{N}::test_finalize_form_defect_blocks_agent_merge",
                                             '"коммит конверта трогает лишний путь"'),),
    "runner._step_gate#act_mode_unset": (r("mode", "runner._step_gate", "GC-ACT-UNAVAILABLE(devtools)",
                                           "tests/test_governance_runner.py::test_s4_stops_when_act_mode_unset"),),
    "runner._step_s8#act_mode_unset": (r("mode", "runner._step_s8", "S8: режим акта не установлен",
                                         "tests/test_governance_runner.py::test_s8_stops_when_act_mode_unset"),),
    'task_bridge.deliver_superseded#replacement_repeat': (r("admission", 'task_bridge.deliver_superseded', 'f"возврат PR #{repeated.pr} замены первой доставки"',
        f"{TB}::test_stored_success_requires_admission[v1_replacement]",
        '"допуск review/v1 не подтверждён — узел requirements"'),),
    'task_bridge.deliver_superseded#returned_pr': (r("admission", 'task_bridge.deliver_superseded', 'f"возврат PR #{pr} ревизии {n}"',
        f"{TB}::test_stored_success_requires_admission[returned_pr]",
        '"допуск review/v1 не подтверждён — узел requirements"'),),
    'task_bridge.deliver_superseded#returned_merged': (r("admission", 'task_bridge.deliver_superseded', 'f"возврат вмерженного PR #{pr} ревизии {n}"',
        f"{TB}::test_stored_success_requires_admission[returned_merged]",
        '"допуск review/v1 не подтверждён — узел requirements"'),),
    'task_bridge.deliver_superseded#returned_open': (r("admission", 'task_bridge.deliver_superseded', 'f"возврат открытого PR #{pr} ревизии {n}"',
        f"{TB}::test_stored_success_requires_admission[returned_open]",
        '"допуск review/v1 не подтверждён — узел requirements"'),),
    'task_bridge.deliver_superseded#redo_intent': (r("admission", 'task_bridge.deliver_superseded', 'f"повтор доставки ревизии {n} из намерения"',
        f"{TB}::test_stored_success_requires_admission[redo_intent]",
        '"допуск review/v1 не подтверждён — узел requirements"'),),
    'task_bridge.deliver_superseded#returned_v1': (r("admission", 'task_bridge.deliver_superseded', 'f"возврат PR #{v1_pr} первой доставки"',
        f"{TB}::test_stored_success_requires_admission[returned_v1]",
        '"допуск review/v1 не подтверждён — узел requirements"'),),
    'task_bridge.deliver_for_run#completed': (r("admission", 'task_bridge.deliver_for_run', 'f"tasks-спека уже доставлена (PR #{pr_done})"',
        f"{TB}::test_stored_success_requires_admission[for_run_completed]",
        '"допуск review/v1 не подтверждён — узел requirements"'),),
    'task_bridge.deliver_for_run#adopted': (r("admission", 'task_bridge.deliver_for_run', 'f"принят существующий PR #{existing}"',
        f"{TB}::test_stored_success_requires_admission[for_run_adopted]",
        '"допуск review/v1 не подтверждён — узел requirements"'),),
    "runner._step_gate#completed_admission": (r("admission", "runner._step_gate", "refusal is None",
        "tests/test_governance_runner.py::test_s4_completed_gate_rerun_without_admission[stop_delivered]",
        '"гейт S4 заново"'),),
    "runner._step_s8#completed_admission": (r("admission", "runner._step_s8", "refusal is not None",
        "tests/test_governance_runner.py::test_s8_completed_gate_rerun_without_admission[stop_delivered]",
        '"started"'),),
    "runner._step_s8#run_status": (r("status", "runner._step_s8", 'state.status = "running"',
        f"{RN}::test_s8_lost_admission_run_not_completed_when_checkout_fails", '"running"'),),
    "runner._next_wave#admission": (r("admission", "runner._next_wave", "refusal is not None",
        "tests/test_governance_runner.py::test_next_wave_needs_admission[stop_delivered]",
        '"stopped_review"'),),
    "approve_node._propose#mode_unavailable": (r("mode", "approve_node._propose",
        "режим акта продукта (узел {node_id})",
        f"{N}::test_frontmatter_noop_refused_when_mode_unavailable",
        '"режим акта продукта .узел charter.: нет привязки"'),),
    "approve_node._propose#legacy_noop_review": (r("status", "approve_node._propose",
        "debt is None and statuses is not None",
        f"{N}::test_frontmatter_noop_in_review_needs_status",
        "outcome.request in world.state.ops"),),
    "task_bridge.deliver_superseded#review_noop": (r("noop", "task_bridge.deliver_superseded",
                                                     "traceless and unconfirmed is None",
                                                     f"{TB}::test_review_noop_refused_without_fresh_approval[stop_delivered]",
                                                     '"гейт §I12 вызван"'),),
    "act_envelope.envelope_refusal#bytes": (r("bytes", "act_envelope.envelope_refusal",
                                              "байты узла изменились после одобрения",
                                              f"{S}::test_provider_twin_changed_candidate_bytes_not_approved"),),
    "act_envelope.envelope_refusal#pins": (r("pins", "act_envelope.envelope_refusal",
                                             "upstream-пины изменились после одобрения",
                                             f"{S}::test_cascade_by_pins_without_a_history_record"),),
    "ops.RealOps.pr_body_fact": (r("gh_failed", "ops.RealOps.pr_body_fact", "done.returncode != 0",
                                   f"{OB}::test_pr_body_unreadable_is_unavailable", '"тело PR #7: no network"'),),
}


_ADAPTER = "адаптер чтения steward: перевод исхода библиотеки; отказ не порождает (его порождает `_call`/`_own`)"
_HELPER = "вычисляет вход суждения без собственной проверки-отказа; судит вызывающий вход"
_BUILD = "строит значение/запрос; проверок-отказов нет"
_IO = "транспорт/ввод-вывод без проверки-отказа"
NOT_JUDGMENT = {
    "task_bridge._admission_refusal": "делегирует `act_status.noop_refusal` (место суждения)",
    **{f"act_facts.{n}": _ADAPTER for n in (
        "_v3", "gh_transport", "_delivery", "read", "open_review", "effective", "event_stamps",
        "review_requires", "_activation", "history", "activation")},
    "act_anon._NoRedirect.redirect_request": "транспортная политика (3xx не исполняется); отказа об объекте нет",
    "act_anon._default_open": _IO,
    "act_anon.AnonTransport.__init__": _BUILD,
    **{f"act_check.{n}": _HELPER for n in (
        "_activations", "_stamps", "_requires", "_until_t", "_mode_events", "_earlier",
        "_mode_history")},
    "act_trust.TrustRoot.id_tuple": _BUILD, "act_trust.Trust.source": _BUILD,
    "act_trust.Trust.epochs": _BUILD, "act_trust.Trust.root": _BUILD,
    "act_trust.Journal.status": "протокол (Protocol); суждение — у реализаций",
    "act_trust.Journal.record": "протокол (Protocol); запись, не суждение",
    # С-1: `act_trust.default_readers` остаётся `_BUILD` и после Task 11c (`Readers(gh_get)`);
    # места суждения Task 11c — в её Step 7
    "act_trust.default_readers": _BUILD, "act_trust._repo_path": _BUILD,
    "act_trust.credentials": "перечень того, что процесс держит; судит `_self_report`",
    "act_trust._roles": _BUILD,
    "act_trust.load_anchor_times": "собирает времена якорей; недоступность — значение, судит `_epoch_at`",
    "act_journal._now": _BUILD,
    "act_journal._publisher_present": "наличие файла издателя; выбор — `journal_for`",
    "act_journal.ForgeEpochs.__init__": _BUILD, "act_journal._root_id": _BUILD,
    "act_journal.DevJournal.__init__": _BUILD, "act_journal.DevJournal._request": _BUILD,
    "act_journal.InstalledJournal.__init__": _BUILD,
    "act_journal.InstalledJournal.status.reload": "обратный вызов клиента издателя: отмечает сдвиг снимка (STALE решает `status`)",
    "act_identity.RequestIdentity.manifest_path": _BUILD,
    "act_identity.RequestIdentity.migration_marker": _BUILD,
    "act_identity.RequestIdentity.memo_key": _BUILD,
    "act_journal_wire._now": _BUILD, "act_journal_wire._root_id": _BUILD,
    "act_journal_wire.WireJournal.__init__": _BUILD, "act_journal_wire.DevStoreJournal.__init__": _BUILD,
    "act_status._rel": _BUILD, "act_status._node_paths": _BUILD,
    "act_status._no": "форматирует ответ провайдера `not_approved:<код>:<деталь>`; проверок нет",
    "act_status.real_deps": _BUILD, "act_status.real_deps.journal": _BUILD,
    "act_status.Decider.__init__": _BUILD, "act_status.Decider.journal": _BUILD,
    "act_status.LegacyProbe.__init__": _BUILD, "act_status.LegacyProbe._changes": _IO,
    "act_status.Statuses.__init__": _BUILD, "act_status.Statuses._upstreams": _BUILD,
    "act_status.Statuses._stopped": "выбирает текст причины для уже решённого отказа (`_compute`)",
    "act_status._blobs": "фабрика читателя (`_blobs.read` — место суждения)",
    "act_status._statuses": _BUILD, "act_status.statuses_at_base": _BUILD,
    "act_status.mode_probe": "точка подмены для тестов вне act_*; суждение — `live_policy`",
    "act_status._gate_statuses": "собирает статусы для провайдера; суждения — `Statuses`",
    "act_status._gate_statuses.text_of": _IO,
    "act_node.generation_of": _BUILD, "act_node.request_of": _BUILD,
    "act_node.manifest_file": _BUILD, "act_node.label_for": _BUILD,
    "act_node._invalidate": "терминальная запись без условия; причина — у вызывающего",
    "act_node._legacy": "фабрика вердикта старого кода (`_legacy.verdict` — место суждения)",
    "act_node._agent_merge": "оркестровка агентского мержа; форма — `envelope_commit_defect`",
    "act_node._in_base": "отвечает фактом достижимости (`bool | None`); отказ — `_require_in_base`/`reopen`",
    "act_node.audit_late_dismissals": "аудит; решения не меняет (приёмка №10)",
    "act_node._create_successor": "оркестровка: создаёт преемника через `approve_node`; проверок нет",
    "act_premerge._Stop.__init__": _BUILD,
    "act_premerge._boundary": "вычисляет момент границы; расхождение часов судит `_check`",
    "act_premerge._manifest_text": _IO,
    "act_premerge.app_installations": "фабрика проверки; суждение — `installations_refusal`",
    "act_premerge.app_installations.check": "делегирует `act_app_token.installations_refusal`",
    "act_premerge._parser": _BUILD, "act_premerge.real_deps": _BUILD,
    "act_premerge.main": "отображение исхода `_check` в код выхода",
    "act_app_token._NoRedirect.redirect_request": "транспортная политика; отказа об объекте нет",
    "act_app_token._http": _IO, "act_app_token._jwt": _BUILD,
    "act_app_token.main": "CLI вокруг `app_identity`",
    "act_barrier._kind": "классификация формы ветки; суждения — `_judge`",
    "act_barrier._head": _IO,
    "act_barrier.main": "CLI вокруг `live_unmarked`/`invalidate`",
    # дополнения части B в существующие модули
    "ops._gate_check_exe": _BUILD, "ops._act_args": _BUILD,
    "ops.Ops.gate_snapshot_digest": "протокол", "ops.Ops.first_parent_changes": "протокол",
    "ops.Ops.pr_body_fact": "протокол", "ops.Ops.snapshot_open": "протокол",
    "ops.Ops.snapshot_reader": "протокол", "ops.Ops.snapshot_close": "протокол",
    "ops.RealOps.snapshot_reader": _BUILD, "ops.RealOps.snapshot_close": _IO,
    "ops.SnapshotOps.__init__": _BUILD,
    "ops.SnapshotOps._git": _IO, "ops.SnapshotOps.first_parent_changes": _IO,
    "ops.SnapshotOps.show_file": _IO, "ops.SnapshotOps.show_file_bytes": _IO,
    "ops.SnapshotOps.rev_parse": _IO, "ops.SnapshotOps.fetch_branch": _IO,
    "ops._parse_first_parent": "разбор вывода `git log`; не суждение",
    "ops.RealOps.gate_snapshot_digest": "возвращает код и вывод CLI; судит `gate_run`",
    "approval_ledger.record_review_act": "запись акта; проверок нет",
    "approval_ledger.audit_events": _BUILD,
    "approval_ledger.record_audit": "идемпотентная запись события аудита (повтор — `False`, не отказ)",
    "act_decision.find_act": "делегирует `_act` (часть A); `None` — нет акта, судит вызывающий",
    "act_decision.merger_allowed": "делегирует `_merger_ok` (часть A)",
    "act_envelope.expected_envelope": _BUILD,
    "act_settings.config_home": "правило пути XDG; относительный путь — откат на дефолт, не отказ",
    "approve_node._review_debt": _BUILD,
}

#: Ложные срабатывания детектора проверок-отказов: путь, похожий на отказ, отказом
#: о судимом объекте не является (функция остаётся в NOT_JUDGMENT).
GUARD_EXEMPT = {
    "act_status.Statuses._stopped": "`False` — «не остановлен», выбор текста причины",
    "approval_ledger.record_audit": "`False` — событие уже записано (идемпотентность)",
    "act_node.manifest_file": "`None` — заявка без маркера (legacy), не отказ",
}


#: Перекрёстная проверка: функция с признаком положительного суждения — в JUDGMENT.
MARKERS = {"APPROVED", "Accepted", "LegacyAccepted", "complete_request"}
REFUSE_CALLS = {"Unavailable", "Rejected", "Violation", "_Stop", "unavailable", "_unresolved",
                "_invalidate", "_no", "LegacyHalt"}
CONDITIONAL = (ast.If, ast.IfExp, ast.While, ast.For, ast.Try, ast.ExceptHandler, ast.BoolOp)
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
#: Утверждения, которые не называют причину конкретной проверки.
GENERIC = {"Unavailable", "isinstance", "== 2", "== 3", "== 0", "not_approved:", "unavailable:"}


def _walk(module: str) -> dict[str, tuple[ast.AST, str]]:
    """Квалифицированное имя → (определение, исходник модуля), рекурсивно."""
    src = (ROOT / "governance" / f"{module}.py").read_text(encoding="utf-8")
    found: dict[str, tuple[ast.AST, str]] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}.{child.name}"
                if not isinstance(child, ast.ClassDef):
                    found.setdefault(name, (child, src))
                visit(child, name)
            else:
                visit(child, prefix)

    visit(ast.parse(src), module)
    return found


def _definitions() -> dict[str, tuple[ast.AST, str]]:
    found: dict[str, tuple[ast.AST, str]] = {}
    for module in MODULES:
        found.update(_walk(module))
    for module, names in EXISTING_ADDITIONS.items():
        defs = _walk(module)
        for name in names:
            found[f"{module}.{name}"] = defs.get(f"{module}.{name}", (None, ""))
    return found


_ANY: dict[str, tuple[ast.AST, str]] = {}


def _function(qualified: str) -> tuple[ast.AST | None, str]:
    module = qualified.split(".")[0]
    if not any(k.startswith(module + ".") for k in _ANY):
        _ANY.update(_walk(module))
    node, src = _ANY.get(qualified, (None, ""))
    return node, (ast.get_source_segment(src, node) or "") if node is not None else ""


def _refusal_paths(fn: ast.AST) -> list[int]:
    """Строки условных путей к отказу в собственном теле функции."""
    found: list[int] = []

    def refusing(value: ast.AST | None) -> bool:
        if isinstance(value, ast.Call):
            func = value.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            # доменный отказ вида `cls(None, error=…)` (ответ транспорта) — тоже отказ (b5)
            return name in REFUSE_CALLS or any(kw.arg == "error" for kw in value.keywords)
        if isinstance(value, ast.Constant):
            return value.value is False or (isinstance(value.value, str)
                                            and value.value.startswith(("unavailable:", "not_approved:")))
        if isinstance(value, ast.JoinedStr) and value.values and isinstance(value.values[0], ast.Constant):
            return str(value.values[0].value).startswith(("unavailable:", "not_approved:"))
        return isinstance(value, ast.Tuple) and any(refusing(e) for e in value.elts)

    def visit(node: ast.AST, conditional: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, SCOPES):
                continue
            inside = conditional or isinstance(node, CONDITIONAL)
            if inside and (isinstance(child, ast.Raise)
                           or (isinstance(child, ast.Return) and refusing(child.value))
                           # отказ, присвоенный имени/атрибуту и возвращённый позже (b5)
                           or (isinstance(child, (ast.Assign, ast.AnnAssign)) and refusing(child.value))
                           or (isinstance(child, ast.Call) and refusing(child)
                               and isinstance(node, ast.Expr))):
                found.append(child.lineno)
            visit(child, inside)

    visit(fn, False)
    return found + _refusal_or_none(fn)


def _refusal_or_none(fn: ast.AST) -> list[int]:
    """Форма «отказ | None»: функция возвращает и `None`, и значение, и хотя бы один из
    возвратов — условный (отказ-строка, кортеж нарушения, `None` как «не установлено»)."""
    returns: list[tuple[bool, bool, int]] = []

    def visit(node: ast.AST, conditional: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, SCOPES):
                continue
            inside = conditional or isinstance(node, CONDITIONAL)
            if isinstance(child, ast.Return):
                none = child.value is None or (isinstance(child.value, ast.Constant)
                                               and child.value.value is None)
                returns.append((inside, none, child.lineno))
            visit(child, inside)

    visit(fn, False)
    if {none for _c, none, _l in returns} != {True, False}:
        return []
    return [line for inside, _none, line in returns if inside]


ROWS = [(site, *row) for site, rows in JUDGMENT.items() for row in rows]


def _twin(ref: str) -> tuple[ast.FunctionDef | None, str, str]:
    path, name = ref.split("::")
    file = ROOT / path
    if not file.is_file():
        return None, "", ""
    src = file.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef)
               and n.name == name.split("[")[0]), None)
    if fn is None:
        return None, "", src
    start = min([d.lineno for d in fn.decorator_list] + [fn.lineno])
    return fn, "\n".join(src.splitlines()[start - 1:fn.end_lineno]), src


def test_every_definition_classified_exactly_once() -> None:
    defs = _definitions()
    missing = sorted(q for q, (node, _s) in defs.items() if node is None)
    assert missing == [], f"дополнения части B не найдены в коде: {missing}"
    judged = {k for k in JUDGMENT if "#" not in k}
    plain = set(NOT_JUDGMENT)
    assert sorted(set(defs) - judged - plain) == [], "определения вне реестров"
    assert sorted(judged & plain) == [], "определения в обоих реестрах"
    assert sorted((judged | plain) - set(defs)) == [], "реестр называет исчезнувшие определения"


def test_modified_functions_audited() -> None:
    """b5 (B3-9): новые проверки внутри изменённых существующих функций — в `JUDGMENT`."""
    guards = {k for k in JUDGMENT if "#" in k}
    expected = {f"{func}#{g}" for func, entry in MODIFIED_EXISTING.items()
                if isinstance(entry, tuple) for g in entry}
    assert sorted(guards ^ expected) == [], "ключи `<функция>#<проверка>` ≠ MODIFIED_EXISTING"
    missing = sorted(f for f in MODIFIED_EXISTING if _function(f)[0] is None)
    assert missing == [], f"изменённые функции не найдены: {missing}"
    assert [f for f, e in MODIFIED_EXISTING.items() if isinstance(e, str) and not e.strip()] == []


def test_not_judgment_has_no_refusal_guard() -> None:
    """Правило b4: проверка-отказ в собственном теле — значит место суждения."""
    defs = _definitions()
    guarded = {q: _refusal_paths(defs[q][0]) for q in NOT_JUDGMENT}
    assert sorted(q for q, lines in guarded.items() if lines and q not in GUARD_EXEMPT) == []
    assert sorted(q for q in GUARD_EXEMPT if not guarded.get(q)) == [], "устаревшие исключения"


def test_every_judgment_has_rows_and_exemption_a_reason() -> None:
    assert [s for s, rows in JUDGMENT.items() if not rows] == []
    assert [s for s, why in NOT_JUDGMENT.items() if not why.strip()] == []


@pytest.mark.parametrize("site,component,check,lit,twin,tlit", ROWS,
                         ids=[f"{r[0]}:{r[1]}" for r in ROWS])
def test_row_check_literal_belongs_to_one_check(site, component, check, lit, twin, tlit) -> None:
    """(a) литерал проверки — в исходнике функции проверки ровно один раз."""
    node, src = _function(check)
    assert node is not None, f"{site} × {component}: функции проверки {check} нет"
    assert src.count(lit) == 1, f"{check}: литерал {lit!r} встречается {src.count(lit)} раз"


def asserts_reason(fn: ast.FunctionDef, module_src: str, tlit: str) -> bool:
    """Причина участвует в проверяемом условии о результате (b5–b7, B3-10/B6-3/B7-1).

    Разбирается только `Assert.test` (не сообщение `msg`), на конъюнкты `and`. Конъюнкт —
    сравнение `==`/`in`/`is` (или `startswith`/`endswith`) с происхождением сторон:
    наблюдаемая сторона укоренена (база цепочки атрибутов/подписок/вызовов) в локальном
    значении или вызове, а не в ожиданиях; ожидания — литерал и имена, связанные с ним без
    вычисления (присваивание, `parametrize`, константы модуля). Принимается: (а) наблюдаемая
    сторона без литерала против стороны с литералом; (б) литерал называет наблюдаемое
    выражение (`decider._memo`) против чистого ожидания; (в) литерал — всё сравнение
    (`… is None`) с наблюдаемой и ожидаемой сторонами; либо литерал (имя) в
    `pytest.raises(..., match=…)`. Fail closed: сравнение только ожиданий, `or`, `not`, IfExp,
    `!=`/`not in`, тавтология, утверждение только о типе — не засчитываются."""
    def seg(node: ast.AST) -> str:
        return ast.get_source_segment(module_src, node) or ""

    pure = {"str", "repr", "len", "list", "tuple", "set", "frozenset", "sorted", "dict",
            "bool", "int"}

    def no_call(node: ast.AST) -> bool:
        """Нет вычисления вне лямбд и конструкторов данных (`dict(…)` — данные)."""
        stack = [node]
        while stack:
            n = stack.pop()
            if isinstance(n, ast.Call) and not (isinstance(n.func, ast.Name) and n.func.id in pure):
                return False
            stack.extend(c for c in ast.iter_child_nodes(n) if not isinstance(c, ast.Lambda))
        return True

    def names_of(targets: list[ast.AST]) -> set[str]:
        """Только имена (в т. ч. в распаковке), не базы атрибутов/подписок."""
        out: set[str] = set()
        for t in targets:
            if isinstance(t, ast.Name):
                out.add(t.id)
            elif isinstance(t, (ast.Tuple, ast.List)):
                out |= names_of(list(t.elts))
        return out

    def assigns(scope: list[ast.AST]) -> list[tuple[set[str], ast.AST]]:
        return [(names_of(n.targets if isinstance(n, ast.Assign) else [n.target]), n.value)
                for n in scope if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None]

    own, module = assigns(list(ast.walk(fn))), assigns(ast.parse(module_src).body)
    constants = {n for names, _ in module for n in names}  # данные модуля — не наблюдение
    bound: set[str] = set()  # ожидания: связанные с литералом данные (без вычисления)
    for names, value in own + module:
        if tlit in seg(value) and no_call(value):
            bound |= names
    for dec in fn.decorator_list:
        if isinstance(dec, ast.Call) and "parametrize" in ast.unparse(dec.func) and tlit in seg(dec):
            first = dec.args[0]
            names = (first.value if isinstance(first, ast.Constant) else
                     ",".join(e.value for e in getattr(first, "elts", []) if isinstance(e, ast.Constant)))
            bound |= {n.strip() for n in str(names).split(",")}
    changed = True
    while changed:  # имена, выведенные из ожиданий без вычисления (`a, b = TABLE[c]`)
        changed = False
        for names, value in own:
            if no_call(value) and {n.id for n in ast.walk(value) if isinstance(n, ast.Name)} & bound:
                if not names <= bound:
                    bound |= names
                    changed = True

    # локальные данные без вычисления (`commit = "…"`) — ожидание; пустой контейнер — сборщик,
    # который наполняет проверяемый код (`seen = []`), — наблюдение
    empty = (ast.List(elts=[]), ast.Dict(keys=[], values=[]))
    data: set[str] = set()
    changed = True
    while changed:  # данные — из литералов и других данных, не из наблюдаемых имён
        changed = False
        for names, value in own:
            local = {t.id for c in ast.walk(value) if isinstance(c, ast.comprehension)
                     for t in ast.walk(c.target) if isinstance(t, ast.Name)}
            refs = {r.id for r in ast.walk(value) if isinstance(r, ast.Name)} - local
            if (no_call(value) and ast.dump(value) not in {ast.dump(e) for e in empty}
                    and refs <= bound | constants | data | pure and not names <= data):
                data |= names
                changed = True

    def rooted(node: ast.AST) -> bool:
        """Наблюдаемое: корень цепочки — локальное значение/фикстура или вызов, не ожидание."""
        if isinstance(node, ast.Name):
            return node.id not in bound | constants | data
        if isinstance(node, (ast.Tuple, ast.List)):
            return bool(node.elts) and all(rooted(e) for e in node.elts)
        if isinstance(node, (ast.Attribute, ast.Subscript)):
            return rooted(node.value)
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in pure:
                return any(rooted(a) for a in node.args)
            return rooted(node.func)
        return False

    def mentions(node: ast.AST) -> bool:
        return tlit in seg(node) or any(isinstance(n, ast.Name) and n.id in bound
                                        for n in ast.walk(node))

    def root(node: ast.AST) -> str | None:
        while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
            node = node.func if isinstance(node, ast.Call) else node.value
        return node.id if isinstance(node, ast.Name) else None

    def inputs_only(node: ast.AST) -> bool:
        """Ожидания в наблюдаемой стороне — только аргументы вызова (входы), не её корень."""
        args = {id(n) for c in ast.walk(node) if isinstance(c, ast.Call)
                for a in [*c.args, *(k.value for k in c.keywords)] for n in ast.walk(a)}
        return all(id(n) in args for n in ast.walk(node)
                   if isinstance(n, ast.Name) and n.id in bound)

    def observed(node: ast.AST) -> bool:
        return rooted(node) and tlit not in seg(node) and inputs_only(node)

    def expectation(node: ast.AST) -> bool:
        return no_call(node) and not any(isinstance(n, ast.Name) and rooted(n)
                                         for n in ast.walk(node))

    def conjuncts(e: ast.AST) -> list[ast.AST]:
        if isinstance(e, ast.BoolOp) and isinstance(e.op, ast.And):
            return [c for v in e.values for c in conjuncts(v)]
        return [e]

    def checks_reason(e: ast.AST) -> bool:
        if isinstance(e, ast.Compare):
            sides = [e.left, *e.comparators]
            pairs = [(a, b) for a in sides for b in sides if a is not b]
            if tlit in seg(e) and not any(tlit in seg(s) for s in sides):
                # литерал — всё сравнение вместе с оператором (`… is not None`, `x is refused`):
                # наблюдаемая сторона против ожидания или значения с другим корнем
                return any(observed(a) and (expectation(b) or root(b) not in (None, root(a)))
                           for a, b in pairs)
            if not all(isinstance(op, (ast.Eq, ast.In, ast.Is)) for op in e.ops):
                return False
            return (any(observed(a) and mentions(b) and (no_call(b) or tlit in seg(b))
                        for a, b in pairs)
                    or any(rooted(a) and tlit in seg(a) and expectation(b) for a, b in pairs))
        if (isinstance(e, ast.Call) and isinstance(e.func, ast.Attribute)
                and e.func.attr in ("startswith", "endswith")):
            return observed(e.func.value) and (tlit in seg(e) or any(mentions(a) for a in e.args))
        return False

    for node in ast.walk(fn):
        if isinstance(node, ast.Assert) and any(checks_reason(c) for c in conjuncts(node.test)):
            return True
        if isinstance(node, ast.Call) and "raises" in ast.unparse(node.func):
            if any(kw.arg == "match" and mentions(kw.value) for kw in node.keywords):
                return True
    return False


def _assert_src(body: str, module: str = "") -> bool:
    """Вспомогательное для самопроверки механизма: тело теста → asserts_reason."""
    src = module + "\n\ndef test_twin(x):\n" + "\n".join("    " + ln for ln in body.splitlines())
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef))
    return asserts_reason(fn, src, "ПРИЧИНА")


@pytest.mark.parametrize("body,module,expected", [
    ('got = f()\nassert got.reason == "ПРИЧИНА"', "", True),
    ('expected = "ПРИЧИНА"\ngot = f()\nassert got.reason == expected', "", True),
    ('with pytest.raises(E, match="ПРИЧИНА"):\n    f()', "", True),
    ('expected = "ПРИЧИНА"\ngot = f()\nassert isinstance(got, Unavailable)', "", False),
    ('got = f()  # ПРИЧИНА\nassert isinstance(got, Unavailable)', "", False),
    ('got = f()\nassert isinstance(got, Unavailable)', 'REASON = "ПРИЧИНА"', False),
    ('got = f()\nassert got.reason == REASON', 'REASON = "ПРИЧИНА"', True),
    ('unused = "ПРИЧИНА"\nassert f() == 3', "", False),
    ('got = f()\nassert isinstance(got, Unavailable), "ПРИЧИНА"', "", False),
    ('expected = "ПРИЧИНА"\ngot = f()\nassert isinstance(got, Unavailable), expected', "", False),
    ('assert "ПРИЧИНА"', "", False),
    ('expected = {"reason": "ПРИЧИНА"}\ngot = f()\nassert isinstance(got, Unavailable)\n'
     'assert expected["reason"] == expected["reason"]', "", False),
    ('got = f()\nassert True or got.reason == "ПРИЧИНА"', "", False),
    ('got = f()\nassert got.reason != "ПРИЧИНА"', "", False),
    ('got = f()\nassert not got.reason == "ПРИЧИНА"', "", False),
    ('got = f()\nassert got.reason == "ПРИЧИНА" if x else True', "", False),
    ('got = f()\nassert isinstance(got, Unavailable) and got.reason == "ПРИЧИНА"', "", True),
    ('got = f()\nassert got.reason.startswith("ПРИЧИНА")', "", True),
    ('expected = dict(reason="ПРИЧИНА")\nassert expected["reason"] == "ПРИЧИНА"', "", False),
    ('assert REASON == "ПРИЧИНА"', 'REASON = "ПРИЧИНА"', False),
], ids=["literal_in_assert", "bound_name", "raises_match", "assignment_only",
        "comment_only", "module_constant_unused", "module_constant_used", "exit_code_only",
        "message_only", "bound_in_message", "tautology", "expected_vs_expected", "or_true_branch",
        "not_equal", "negated", "ifexp", "and_conjunct", "startswith", "expected_dict_call",
        "constant_vs_literal"])
def test_reason_link_mechanism(body: str, module: str, expected: bool) -> None:
    """B3-10: самопроверка механизма (b) на отрицательных и положительных двойниках."""
    assert _assert_src(body, module) is expected


@pytest.mark.parametrize("site,component,check,lit,twin,tlit", ROWS,
                         ids=[f"{r[0]}:{r[1]}" for r in ROWS])
def test_row_twin_asserts_exact_reason(site, component, check, lit, twin, tlit) -> None:
    """(b) двойник утверждает именно причину своей проверки (связь — по AST)."""
    fn, text, module_src = _twin(twin)
    assert fn is not None, f"{site} × {component}: двойника {twin} нет"
    assert tlit.strip() not in GENERIC, f"{twin}: утверждение {tlit!r} не называет причину"
    assert asserts_reason(fn, module_src, tlit), f"{twin}: {tlit!r} не входит в утверждение"
    if "[" in twin:
        param = twin.split("[", 1)[1].rstrip("]")
        marks = [d for d in fn.decorator_list if "parametrize" in ast.unparse(d)]
        assert marks, f"{twin}: параметр назван, а тест не параметризован"
        literal_ids = [[e.value for e in kw.value.elts if isinstance(e, ast.Constant)]
                       for d in marks if isinstance(d, ast.Call) for kw in d.keywords
                       if kw.arg == "ids" and isinstance(kw.value, ast.List)]
        if literal_ids:
            assert any(param in ids for ids in literal_ids), f"{twin}: такого id нет"


def test_markers_imply_judgment() -> None:
    def body_names(node: ast.AST) -> set:
        body = ast.Module(body=list(node.body), type_ignores=[])  # без аннотаций сигнатуры
        return {getattr(n, "id", getattr(n, "attr", None)) for n in ast.walk(body)}

    marked = sorted(q for q, (node, _s) in _definitions().items() if body_names(node) & MARKERS)
    assert [m for m in marked if m not in JUDGMENT] == []
```

- [ ] **Step 2: Run** `uv run pytest tests/test_act_barrier.py tests/test_act_identity_table.py -v`
  → FAIL (`ImportError`; мета-тест — `act_barrier` нет в коде).

- [ ] **Step 3: Implement** — `governance/act_barrier.py`:

```python
"""Барьер миграции акта по продукту (спека §10 «Единый барьер миграции», Q9).

Полнота выводится из форджи, локальные леджеры — дополнение (другую
установку локально не видно). Пустой отчёт без draining и без аттестации
других установок — не полнота (`gaps`). Вызывает `darkfactory setup-repo
apply` (спека B) в человеческом контексте дважды: перед предложением
активации и перед её мержем.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import SimpleNamespace

from governance import approval_branches as ab
from governance import approval_ledger as al
from governance import run_state as rs
from governance.act_identity import RequestIdentity, manifest_refusal
from governance.act_request import RequestError, parse_manifest
from governance.facts import Outcome
from governance.frontmatter import split_frontmatter

_CANDIDATE = re.compile(r"(?P<ws>.+)-approve-(?P<w>\d+)-(?P<k>\d+)-(?P<a>\d+)$")
PENDING = "approval_pending"


@dataclass(frozen=True)
class BarrierReport:
    """Живые заявки без маркера; `complete` — отчёт полон (нет `gaps`)."""

    items: tuple[str, ...]
    complete: bool
    gaps: tuple[str, ...]


def _kind(branch: str) -> str | None:
    if ab.is_finalize(branch):
        return "finalize"
    if ab.is_candidate(branch):
        return "candidate"
    return "brief" if branch.startswith("brief/") else None


def _identity(product: str, branch: str) -> RequestIdentity | None:
    """Полная идентичность заявки ветки candidate/finalize (B1-14)."""
    stem = branch.removeprefix("spec/").removesuffix(ab.finalize_suffix())
    m = _CANDIDATE.match(stem)
    if m is None:
        return None
    return RequestIdentity(product, m["ws"], f"approve-{m['w']}-{m['k']}-{m['a']}")


def _head(product: str, ops, number: int) -> str | None:
    head = ops.pr_facts(product, number).get("headRefOid")
    return head if isinstance(head, str) else None


@dataclass(frozen=True)
class Unmarked:
    """Не маркер — с причиной ЭТОЙ проверки (ревизия b4: у каждой проверки своя причина,
    двойник проверяет ровно её)."""

    reason: str


NOT_REQUEST_FORM = "ветка не формы заявки"
NO_MANIFEST_FILE = "манифеста нет в голове"
MANIFEST_UNPARSED = "манифест не разобран"
MANIFEST_FOREIGN = "манифест чужой заявки"
NO_MARKER = "маркера миграции нет"
MARKER_AUTHOR = "маркер не от human_approvers"
MARKER_EDITED = "маркер правлен"
MARKER_FOREIGN = "маркер не этой заявки"
SIGNED_STATUS = "статус узла в базе не approved"
SIGNED_OTHER = "подпись другого акта"
NODE_MISSING = "узла нет в базе"
NO_STAMP_RUN = "нет прогона с interview.approval этого PR"
NO_STAMP_FILE = "нет файла штампа брифа"


def _marked(product: str, ops, number: int, branch: str) -> bool | str | Unmarked:
    """Действительный манифест `review/v1` ЭТОЙ заявки в голове PR (B1-13): разобран
    строго и называет её идентичность — `True`; иначе `Unmarked(причина)`. Строка —
    факт не установлен."""
    ident = _identity(product, branch)
    if ident is None:
        return Unmarked(NOT_REQUEST_FORM)
    head = _head(product, ops, number)
    if head is None:
        return f"#{number}: голова не прочитана"
    fact = ops.repo_file_fact(product, head, ident.manifest_path)
    if fact.outcome is Outcome.UNAVAILABLE:
        return f"#{number}: манифест не прочитан — {fact.detail}"
    if fact.outcome is not Outcome.FOUND or fact.value is None:
        return Unmarked(NO_MANIFEST_FILE)
    try:
        req = parse_manifest(fact.value)
    except RequestError as exc:
        return Unmarked(f"{MANIFEST_UNPARSED}: {exc}")
    refusal = manifest_refusal(req, ident)
    if refusal is not None:
        return Unmarked(f"{MANIFEST_FOREIGN}: {refusal}")
    return True


def _migrated(product: str, ops, number: int, ident: RequestIdentity,
              humans: frozenset[str]) -> bool | str | Unmarked:
    """Маркер `act_migrated` именно этой заявки — дословно, от человека, без правок;
    отвергнутый маркер — `Unmarked` с причиной последней отвергнувшей проверки."""
    fact = ops.pr_comments_fact(product, number)
    if fact.outcome is not Outcome.FOUND or fact.value is None:
        return f"#{number}: комментарии не прочитаны — {fact.detail}"
    why = NO_MARKER
    for c in fact.value:
        if "act-migrated" not in c.body:
            continue
        if c.author not in humans:
            why = MARKER_AUTHOR
        elif c.last_edited_at is not None:
            why = MARKER_EDITED
        elif c.body.strip() != ident.migration_marker:
            why = MARKER_FOREIGN
        else:
            return True
    return Unmarked(why)


def _signed(text: str, by: str, at: str) -> str | None:
    """Подпись этого (или более позднего) акта в базе — `None`; иначе причина."""
    meta, _ = split_frontmatter(text)
    if meta.get("status") != "approved":
        return SIGNED_STATUS
    if meta.get("approved_by") == by and meta.get("approved_at") == at:
        return None
    later = meta.get("approved_at")
    if isinstance(later, str) and isinstance(at, str) and later > at:
        return None
    return SIGNED_OTHER


def _candidate_nodes(product: str, ops, number: int) -> list[str] | str:
    """Узлы, которые candidate отдал на одобрение: его файлы со `status:
    approval_pending` в его голове — независимо от того, что выбрал finalize (B1-13)."""
    files = ops.brief_pr_fact(product, number)
    head = _head(product, ops, number)
    if files.outcome is not Outcome.FOUND or files.value is None or head is None:
        return f"#{number}: состав или голова candidate не прочитаны"
    nodes: list[str] = []
    for path, _change in files.value.files:
        text = ops.repo_file_fact(product, head, path)
        if text.outcome is Outcome.UNAVAILABLE:
            return f"#{number}: {path} в голове candidate не прочитан"
        if text.outcome is Outcome.FOUND and text.value and path.endswith(".md"):
            meta, _ = split_frontmatter(text.value)
            if meta.get("status") == PENDING:
                nodes.append(path)
    return nodes


def _legacy_resolved(product: str, ops, number: int, branch: str,
                     by_branch: Mapping[str, dict]) -> str | tuple[str] | None:
    final = by_branch.get(branch + ab.finalize_suffix())
    if final is None or final["state"] != "MERGED":
        return f"#{number} candidate MERGED без смёрженного finalize — pending-finalize"
    nodes = _candidate_nodes(product, ops, number)
    if isinstance(nodes, str):
        return (nodes,)
    if not nodes:
        return f"#{number} candidate MERGED: узлов на одобрении не найдено — разберите вручную"
    facts = ops.pr_facts(product, number)
    by = (facts.get("mergedBy") or {}).get("login")
    at = facts.get("mergedAt")
    tip = ops.default_branch_fact(product)
    if tip.outcome is not Outcome.FOUND or tip.value is None:
        return (f"#{number}: голова базы не прочитана",)
    for path in nodes:
        text = ops.repo_file_fact(product, tip.value.sha, path)
        why = (NODE_MISSING if text.outcome is not Outcome.FOUND or text.value is None
               else _signed(text.value, by, at))
        if why is not None:
            return f"#{number} candidate MERGED: {path} — {why} — merged-but-unrecorded"
    return None


def _judge(product, ops, pr: dict, by_branch, humans, confirmed) -> str | None | tuple[str]:
    """Пункт отчёта, `None` — разобран, `(gap,)` — факт не установлен."""
    branch, state, number = pr["headRefName"], pr["state"], pr["number"]
    kind = _kind(branch)
    if kind is None or state == "CLOSED":
        return None
    ident = _identity(product, branch)
    unmigrated = NO_MARKER
    if ident is not None:
        migrated = _migrated(product, ops, number, ident, humans)
        if isinstance(migrated, str):
            return (migrated,)
        if migrated is True:
            return None
        unmigrated = migrated.reason
    if kind == "brief":
        if state == "OPEN":
            return f"#{number} brief-PR OPEN — заявка v1 без маркера"
        stamp = None if number in confirmed else _local_brief_stamp(product, number)
        if stamp is None:
            return None
        return (f"#{number} brief-PR MERGED — штамп не доказан локально ({stamp}); требует "
                f"подтверждения человека (--confirm-resolved {number})")  # пункт, не пробел
    marked = _marked(product, ops, number, branch)
    if isinstance(marked, str):
        return (marked,)
    if marked is True:
        return None
    if state == "OPEN":
        return f"#{number} {kind} OPEN без маркера: {marked.reason}; {unmigrated}"
    if kind == "finalize":
        return None  # судится вместе со своим candidate
    return _legacy_resolved(product, ops, number, branch, by_branch)


def _local_brief_stamp(product: str, number: int) -> str | None:
    """Локальное доказательство штампа брифа — `None`; иначе причина."""
    for run_id in rs.all_run_ids():
        try:
            state = rs.load(run_id)
        except (OSError, ValueError):
            continue
        approval = (state.interview or {}).get("approval") or {}
        if approval.get("repo") == product and approval.get("pr") == number:
            stamped = rs.run_dir(run_id) / "brief-approval" / "customer-brief.md"
            return None if stamped.is_file() else NO_STAMP_FILE
    return NO_STAMP_RUN


def _local_items(product: str, runs: Iterable[str]) -> tuple[list[str], list[str]]:
    items, gaps = [], []
    for run_id in runs:
        try:
            state = rs.load(run_id)
        except (OSError, ValueError) as exc:
            gaps.append(f"леджер {run_id} не читается: {exc}")
            continue
        if state.repo_slug != product:
            continue
        for nums, op in al.live_requests(state):
            if op.get("act") is None and op.get("candidate_pr") is None:
                items.append(f"{run_id}:{al.request_key(*nums)} — заявка без маркера до PR")
    return items, gaps


def live_unmarked(product: str, ops, *, humans: frozenset[str], confirmed: frozenset[int],
                  attested: Sequence[str], draining: bool, runs: Iterable[str]
                  ) -> BarrierReport:
    """Живые заявки без маркера продукта по форджу и локальным леджерам (§10 п. 2)."""
    items: list[str] = []
    gaps: list[str] = []
    if not draining:
        gaps.append("draining не включён (act.toml act_migration)")
    if not attested:
        gaps.append("draining других установок не подтверждён (--attest-drained)")
    try:
        prs = ops.prs_by_head_prefix(product, "")
    except RuntimeError as exc:
        gaps.append(f"список PR {product} неполон: {exc}")
        prs = []
    by_branch = {p["headRefName"]: p for p in prs}
    for pr in prs:
        verdict = _judge(product, ops, pr, by_branch, humans, confirmed)
        if isinstance(verdict, tuple):
            gaps.append(verdict[0])
        elif verdict is not None:
            items.append(verdict)
    local, local_gaps = _local_items(product, runs)
    return BarrierReport(tuple(items + local), not (gaps or local_gaps),
                         tuple(gaps + local_gaps))


def live_review_requests(ops_by_product: Mapping[str, object]) -> BarrierReport:
    """Живые заявки `review/v1` продуктов источника (предпросмотр Q9, спека B)."""
    items: list[str] = []
    gaps: list[str] = []
    for product, ops in ops_by_product.items():
        try:
            prs = ops.prs_by_head_prefix(product, "")
        except RuntimeError as exc:
            gaps.append(f"{product}: список PR неполон — {exc}")
            continue
        for pr in prs:
            if pr["state"] != "OPEN" or _kind(pr["headRefName"]) != "candidate":
                continue
            marked = _marked(product, ops, pr["number"], pr["headRefName"])
            if isinstance(marked, str):
                gaps.append(f"{product}{marked}")
            elif marked is True:
                items.append(f"{product}#{pr['number']} — живая заявка review/v1")
    return BarrierReport(tuple(items), not gaps, tuple(gaps))


def invalidate(product: str, ops, pr: int, *, humans: frozenset[str], caller: str) -> str:
    """`act_migrated` по форджу: маркер от человека из `human_approvers` (§10 п. 2)."""
    if caller not in humans:
        raise RuntimeError(f"{caller} не в human_approvers текущей активации — маркер не ставится")
    branch = next((p["headRefName"] for p in ops.prs_by_head_prefix(product, "")
                   if p["number"] == pr), None)
    ident = _identity(product, branch or "")
    if ident is None:
        raise RuntimeError(f"PR #{pr} не candidate/finalize заявки узла")
    ops.comment(product, pr, ident.migration_marker)
    for run_id in rs.all_run_ids():
        state = rs.load(run_id)
        if (state.repo_slug, state.ws_id) != (ident.product, ident.ws_id):
            continue  # тот же ключ в другом воркстриме — другая заявка (B1-14)
        op = state.ops.get(ident.request_id)
        if op is not None and al.is_live(op):
            al.invalidate_request(state, ident.request_id, "act_migrated")
    return ident.request_id
```

```python
def main(argv: Sequence[str] | None = None) -> int:
    """CLI барьера: 0 — пусто и полно; 1 — есть живые; 3 — неполно."""
    from governance import act_status
    from governance.act_settings import config_home, load_settings
    from governance.act_time import Unavailable
    from governance.ops import RealOps

    p = argparse.ArgumentParser(prog="act_barrier")
    p.add_argument("--product", required=True)
    p.add_argument("--confirm-resolved", type=int, action="append", default=[])
    p.add_argument("--attest-drained", action="append", default=[])
    p.add_argument("--invalidate", type=int)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    ops = RealOps()
    probe = SimpleNamespace(repo_slug=args.product, run_id="act-barrier", base_ref=None)
    deps = act_status.real_deps(probe)  # type: ignore[arg-type]
    if deps.binding is None or isinstance(deps.binding, Unavailable):
        print("act_barrier: корень доверия не прочитан — барьер неполон", file=sys.stderr)
        return 3
    live = act_status.live_policy(probe, deps)  # type: ignore[arg-type]
    if isinstance(live, Unavailable):
        print(f"act_barrier: текущая активация — {live.reason}", file=sys.stderr)
        return 3
    humans = frozenset(live.policy.approvers)
    if args.invalidate is not None:
        key = invalidate(args.product, ops, args.invalidate, humans=humans,
                         caller=ops.caller_login() or "")
        print(f"act_migrated: {key}")
    report = live_unmarked(
        args.product, ops, humans=humans, confirmed=frozenset(args.confirm_resolved),
        attested=tuple(args.attest_drained),
        draining=load_settings(config_home(os.environ)).draining, runs=rs.all_run_ids(),
    )
    if args.json:
        print(json.dumps({"items": report.items, "complete": report.complete,
                          "gaps": report.gaps}, ensure_ascii=False))
    else:
        print("\n".join([*report.items, *(f"gap: {g}" for g in report.gaps)]))
    if not report.complete:
        return 3
    return 1 if report.items else 0


if __name__ == "__main__":
    sys.exit(main())
```

`human_approvers` — `PolicySet.approvers` текущей активации. `Makefile`: цель `act-barrier` —
`uv run --group governance python -m governance.act_barrier $(ARGS)`.

**Устойчивость барьера до переключения** (§10): draining держится, пока не активирован
`review`; второй вызов барьера с новыми живыми заявками или `complete=False` останавливает мерж
активации (это делает спека B). Заявка, доставленная после переключения в обход draining,
отказывает на admission (Task 12) — второй рубеж, а не замена порядка.

- [ ] **Step 4: Run** → PASS; полный набор; `uv run ruff check . && uv run pyrefly check`.

- [ ] **Step 4a: Обязательный мутационный проход по ВСЕМ строкам реестра** (ревизия b5, B3-12) —
  включая 40+ строк на настоящем стенде `World` (`tests/test_act_node.py`,
  `tests/test_act_repropose.py`), строки проверок внутри изменённых функций
  (`approve_node._propose#…`, `read_dag_state#…`, `_require_upstream_ready#…`,
  `_try_agent_merge#form`, `runner._step_gate#…`, `runner._step_s8#…`) и прогнанные при ревизии
  плана. Сначала — применимость операторов ко ВСЕМ строкам без запуска двойников (b6, B6-4;
  ожидается `303/303`: при ревизии d6 (01.11) — 291 по коду scratch и 12 строк
  `<функция>#<проверка>` по AST фрагментов плана; `n/a` чинится оператором `BYPASS` или записью `RULED`, не пропуском):

  ```bash
  uv run --group governance python tests/act_mutation.py --check --out /tmp/act-mutation-check.json
  uv run --group governance python tests/act_mutation.py \
    --out docs/superpowers/plans/2026-10-12-approval-act-review.runs/act-mutation-$(git rev-parse --short HEAD).json
  ```

  Правило приёмки: каждая строка — `closed` (двойник упал на утверждении при обойдённой
  проверке) или `ruled` (запись в `RULED` с причиной, видна в диффе и принимается ревьюером
  задачи). `survived`, `error`, `n/a` и непрогнанная строка **блокируют коммит**: чинится
  двойник (тип результата — до обращения к полям; изоляция, при которой обход даёт другой
  итог, а не исключение) или добавляется оператор `BYPASS` с корректными данными. Подмена
  причины (O3) строку не закрывает. Файл результата коммитится.

  Обязательные мутации входа решения (Task 11c, C6): M1 — `load_trust` берёт транспорт у
  вызывающего; M2 — пропуск самоотчёта `profile:history`; M3 — второе чтение токена; M4 — снятие
  закрытого набора форм credentials. Каждая — по своим тестам Task 11c и по
  `test_wiring_site_decides_with_history_token[<место>]` для **каждой** строки
  `tests/test_act_wiring.py::WIRING_SITES` (на момент Task 16 — `act_status.real_deps`,
  `act_premerge.real_deps`); выживший мутант блокирует коммит, как строки реестра.

`tests/act_mutation.py`:

```python
"""Обязательный мутационный проход по строкам реестра идентичности (Task 16 Step 4a).

Для КАЖДОЙ строки `tests/test_act_identity_table.py::ROWS`, включая двойники на стенде
`World` (`tests/test_act_node.py`, `tests/test_act_repropose.py`) и проверки внутри
изменённых существующих функций (`<функция>#<проверка>`): обойти проверку и запустить
названный двойник отдельно. Итог строки:

- `closed` — двойник упал на утверждении (`AssertionError`, `DID NOT RAISE`, regex
  `match=`, `pytest.fail`);
- `survived` — двойник прошёл; `error` — упал иным исключением; `n/a` — оператор не
  применим. Любой из трёх БЛОКИРУЕТ коммит Task 16: двойник или оператор чинятся, либо
  строка вносится в `RULED` с причиной (решение ревьюера задачи, видно в диффе).

Операторы обхода: по умолчанию — условие самого внутреннего `if`/IfExp с литералом
проверки → `False`; для проверок, без которых дальнейший код падает (несущие), для
«принимающих» проверок, памяти и замыканий — `BYPASS`: замена, дающая корректные данные
(проверка «пропустила», дальше — штатный путь). Подмена причины («МУТАНТ», O3) —
только проверка причины: инструмент её не применяет, обход ею НЕ закрывается.

Сначала `--check` — только применимость операторов ко ВСЕМ строкам, без двойников
(b6: должно быть N/N; `n/a` чинится оператором `BYPASS` или записью `RULED`). Затем
запуск: `uv run --group governance python tests/act_mutation.py --out <файл.json>`;
байткод не пишется и очищается перед каждым мутантом (иначе мутант той же длины в ту
же секунду исполнит код предыдущего).
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.test_act_identity_table import ROWS  # noqa: E402

#: Обход с корректными данными: `site:component` → (фрагмент в функции проверки, замена).
BYPASS: dict[str, tuple[str, str]] = {
    'act_anon.AnonTransport._fetch:transport_failure': ('return cls(None, error=f"anon: {type(exc).__name__}: {exc}")',
        'return cls(200, body={}, link=None)'),
    'act_app_token._pages:completeness': ('return Unavailable(f"GET {path} (стр. {page}): HTTP {status}")',
        'return items'),
    'act_barrier._candidate_nodes:pending': ('meta.get("status") == PENDING',
        'True'),
    'act_barrier._identity:request_form': ('    if m is None:\n        return None',
        '    if m is None:\n        m = _CANDIDATE.match("ws-9-approve-9-9-9")'),
    'act_barrier._legacy_resolved:node_in_base': ('NODE_MISSING if',
        'None if'),
    'act_barrier._local_brief_stamp:file': ('stamped.is_file()',
        'True'),
    'act_barrier._local_brief_stamp:run': ('approval.get("repo") == product and approval.get("pr") == number',
        'True'),
    'act_barrier._local_items:unreadable': ('gaps.append(f"леджер {run_id} не читается: {exc}")',
        'pass'),
    'act_barrier._marked:absent': ('return Unmarked(NO_MANIFEST_FILE)',
        'return True'),
    'act_barrier._marked:unparsed': ('return Unmarked(f"{MANIFEST_UNPARSED}: {exc}")',
        'return True'),
    'act_barrier._signed:signer': ('later > at',
        'True'),
    'act_barrier.live_review_requests:product_gaps': ('gaps.append(f"{product}: список PR неполон — {exc}")',
        'pass'),
    'act_barrier.live_unmarked:pagination': ('gaps.append(f"список PR {product} неполон: {exc}")',
        'pass'),
    'act_check._admission:marker_before_bootstrap': ('ctx.request_act is None',
        'True'),
    'act_check._admission:mode_after_review': ('return Rejected(ADMISSION, admission.reason)',
        'return own'),
    'act_check._decide:approver': ('        return Rejected(D1_NO_ACT, f"нет approve человека на {head_sha}")',
        '        act = ReviewView("0", "nobody", "User", "APPROVED", head_sha)'),
    'act_check._decide:dismissal': ('        return Rejected(D1_NO_ACT, f"нет approve человека на {head_sha}")',
        '        act = ReviewView("0", "nobody", "User", "APPROVED", head_sha)'),
    'act_check.check_delivery:act_head': ('        return Rejected(D1_NO_ACT, f"нет approve человека на {head_sha}")',
        '        act = ReviewView("0", "nobody", "User", "APPROVED", head_sha)'),
    'act_facts._call:library_exception': ('return Unavailable(f"steward {what}: {type(exc).__name__}: {exc}")',
        'return None'),
    'act_facts._own:steward_unavailable': ('return Unavailable(got.reason)',
        'return got'),
    'act_journal.DevJournal.record:store_refused': ('return Unavailable(f"журнал периметра (dev): {exc.reason}")',
        'return None'),
    'act_journal_wire.DevStoreJournal.record:insert_failed': ('return Unavailable(f"журнал периметра (dev): событие не записано: {exc}")',
        'return None'),
    'act_journal_wire.DevStoreJournal.status:blocking': ('"blocked" if rows else "clear"',
        '"clear"'),
    'act_journal_wire.WireJournal._call:garbled': ('return Unavailable(f"{op}: ответ издателя не разобран (код {done.returncode})")',
        'return {}'),
    'act_node._manifest:parseable': ('_invalidate(state, key, f"манифест в одобренном коммите не читается: {exc}")',
        'return None'),
    'act_premerge._check:manifest_present': ('raise _Stop(EXIT_REFUSED, f"в {args.head[:12]} нет манифеста {ident.request_id}")',
        'return'),
    'act_premerge._trust:app_unknown': ('raise _Stop(EXIT_UNSET, f"установки App: {found.reason}")',
        'found = None'),
    'act_premerge.request_of_branch:form': ('    if match is None:\n        return None',
        '    if match is None:\n        match = re.fullmatch(pattern, "spec/ws-9-approve-9-9-9")'),
    'act_status.Decider.__call__:memo_key': ('key = (*ident.memo_key, event.merge_commit)',
        'key = (event.merge_commit,)'),
    'act_status.Decider.__call__:memo_request': ('key = (*ident.memo_key, event.merge_commit)',
        'key = (event.merge_commit,)'),
    'act_status.Decider._decide:head_sha': ('head_sha=event.head_sha',
        'head_sha="0" * 40'),
    'act_status.Decider._head_hashes:head_bytes': ('            if text is None:\n                return Unavailable(f"{entry.path} в {event.head_sha[:12]} не читается")',
        '            if text is None:\n                text = "---\\nspec_stage: x\\nstatus: approval_pending\\nversion: 1\\n---\\nтело\\n"'),
    'act_status.Decider.legacy:contract_result': ('legacy=contract',
        'legacy=lambda: LegacyAccepted("alice")'),
    'act_status.Decider.trust:binding': ('self._trust = Unavailable("нет привязки корня доверия (§6.1 п. 1)")',
        'self._trust = act_trust.Trust(None, ())'),
    'act_status.LegacyProbe._candidate:ledger_node': ('return _no(LEDGER_NODE, f"заявка {key} леджера не выносила узел {node}")',
        'return APPROVED'),
    'act_status.LegacyProbe._candidate:ledger_record': ('        if op is None:\n',
        '        if op is None:\n            op = {}\n        if False:\n'),
    'act_status.LegacyProbe._finalize_branch:via_pr': ('            return _no(NOT_VIA_PR, "подпись внесена не смёрженным PR")',
        '            merged = [0]'),
    'act_status.Statuses._compute:delivery_verdict': ('return f"not_approved:act:{getattr(verdict, \'code\', type(verdict).__name__)}"',
        'verdict = Accepted(current.merge_commit, "x", "11", "alice")'),
    'act_status.Statuses._envelope:ws_id': ('return f"узел {node}: нет конверта — не одобрен"',
        'return None'),
    'act_status.Statuses.is_review:no_event': ('current_event(self.history, node) is not None',
        'True'),
    'act_status.Statuses.of:memo_node': ('return self._memo[node]',
        'return next(iter(self._memo.values()))'),
    'act_status._event:request:no_pr': ('        if isinstance(pr, Unavailable):\n            return pr',
        '        if isinstance(pr, Unavailable):\n            return None'),
    'act_status.fresh_base:closed': ('ops.snapshot_close(repo)',
        'pass'),
    'act_status.fresh_base:snapshot_unavailable': ('yield Unavailable(f"снимок {state.repo_slug}@{base}: {fact.detail}")',
        'yield base, ops'),
    'act_status.gate_run:snapshot_digest': ('return Unavailable(f"gate-check --print-snapshot-digest: код {rc}: {digest}")',
        'return "review", str(out)'),
    'act_status.load_history:base_unread': ('    if rows is None:\n        return Unavailable',
        '    if rows is None:\n        rows = []\n    if False:\n        return Unavailable'),
    'act_status.noop_refusal:fresh_ref': ('statuses_at_base(state, reader, dag, live, deps, ref=ref)',
        'statuses_at_base(state, ops, dag, live, deps)'),
    'act_status.noop_refusal:history_snapshot': ('load_history(ops, state, snapshot)',
        'load_history(ops, state, state.base_ref or "master")'),
    'act_status.noop_refusal:legacy_snapshot': ('LegacyProbe(state, ops, snapshot, decider.legacy, dag)',
        'LegacyProbe(state, ops, state.base_ref or "master", decider.legacy, dag)'),
    'act_status.noop_refusal:mode_unset': ('return f"режим акта продукта: {live.reason}"',
        'return None'),
    'act_status.noop_refusal:stale_base': ('return f"свежая база: {snapshot.reason}"',
        'snapshot = (state.base_ref or "master", ops)'),
    'act_status.pr_for_merge:exactly_one': ('    if len(found) != 1:',
        '    if not found:\n        found = [7]\n    if False:'),
    'act_trust._checked_epochs:transition': ('return Unavailable(f"недопустимый переход журнала: {exc}")',
        'pass'),
    'act_trust._journal_versions:journal_deleted': ('if versions:',
        'if False:'),
    'act_trust._modes:unread_set': ('return Unavailable(f"набор {a.merge_commit[:12]}: {policy.reason}")',
        'continue'),
    'act_trust._register:violation_event': ('return Unavailable(f"нарушение периметра: {detail}")',
        'return None'),
    'act_trust.current_activation:open_epoch': ('    if not span:\n        return Unavailable("в открытой эпохе нет активаций")',
        '    if not span:\n        span = next(s for s in ranges.values() if isinstance(s, tuple) and s)'),
    'act_trust.gh_get:not_json': ('return unavailable(f"gh api {path} ({cred.label}): ответ не JSON")',
        'return Fact(Outcome.FOUND, None, "ok")'),
    'act_trust.load_binding:absent': ('    except FileNotFoundError:\n        return None\n',
        '    except FileNotFoundError as exc:\n        return Unavailable(f"привязка {path}: {exc}")\n'),
    'act_trust.load_ranges:history_violation': ('return _register(journal, trust.snapshot, ("approval-policy", got.reason))',
        'got = ()'),
    'act_trust.load_ranges:unknown_anchor': ('ranges[epoch.epoch] = Unavailable(f"якорь эпохи {epoch.epoch}: {at.reason}")\n            continue',
        'at = t'),
    'act_trust.product_mode:never_entered': ('if not entered:\n        return PRE_MIGRATION',
        'if not entered:\n        return "merge"'),
    'act_trust.product_mode:prior_epoch': ('return Unavailable(f"история режима, эпоха {epoch.epoch}: {span.reason}")',
        'continue'),
    'act_trust.read_trust_snapshot:head_named': ('return Unavailable(f"{repo}: голова {binding.df_trust_branch} не прочитана")',
        'head = "0" * 40'),
    'approve_node._legacy_outcome:old_pin_missing': ('    if not pinned:\n',
        '    if not pinned:\n        pinned = {"sha": None, "fingerprint": None}\n    if False:\n'),
    'approve_node._propose#mode_unavailable:mode': ('raise _unresolved(f"режим акта продукта (узел {node_id}): {live.reason}")',
        'live = act_status.LivePolicy("merge", None, None, None, None)'),
    'approve_node._try_agent_merge#form:form': ('defect = form() if form is not None else _envelope_form_defect(state, ops, dag, op, nodes, head)',
        'defect = None'),
    'ops.RealOps._credential_helper:gh_profile': ('"!gh auth git-credential"',
        '"!gh auth git-credential --other"'),
    'ops.RealOps._snapshot_env:allowlist': ('{k: os.environ[k] for k in _SNAPSHOT_ENV_KEEP if k in os.environ}',
        'dict(os.environ)'),
    'ops.RealOps._snapshot_env:empty_home': ('HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),',
        'HOME=os.environ.get("HOME", ""),'),
    'ops.RealOps._snapshot_env:no_global': ('GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,',
        'GIT_CONFIG_NOSYSTEM="1",'),
    'ops.RealOps._snapshot_env:no_prompt': ('GIT_TERMINAL_PROMPT="0", ',
        ''),
    'ops.RealOps._snapshot_env:profile_resolved': ('str(profile.expanduser().resolve())',
        'str(profile)'),
    'ops.RealOps._snapshot_env:runner_profile': ('os.environ.get("REVIEW_GH_CONFIG_DIR") or REVIEW_GH_CONFIG_DIR',
        'REVIEW_GH_CONFIG_DIR'),
    'ops.RealOps._snapshot_git:helper_reset': ('"-c", "credential.helper=",\n             ',
        ''),
    'ops.RealOps._snapshot_url:canonical': ('f"https://github.com/{repo_slug}.git"',
        'f"https://example.invalid/{repo_slug}.git"'),
    'ops.RealOps.snapshot_open:own_repo': ('tempfile.mkdtemp(prefix="act-snapshot-")',
        'os.path.join(tempfile.gettempdir(), "act-snapshot-shared")'),
    'ops.SnapshotOps.__getattr__:deny': ('        raise AttributeError(\n',
        '        return getattr(self._base, name)\n        raise AttributeError(\n'),
    'runner._step_gate#completed_admission:admission': ('if refusal is None:',
        'if True:'),
    'runner._step_s8#run_status:status': ('state.status = "running"',
        'pass'),
    'task_bridge.deliver_for_run#adopted:admission': ('_require_admission(state, ops, legacy_bundle, f"принят существующий PR #{existing}")',
        'pass'),
    'task_bridge.deliver_for_run#completed:admission': ('_require_admission(state, ops, legacy_bundle, f"tasks-спека уже доставлена (PR #{pr_done})")',
        'pass'),
    'task_bridge.deliver_superseded#redo_intent:admission': ('_require_admission(state, ops, legacy_bundle, f"повтор доставки ревизии {n} из намерения")',
        'pass'),
    'task_bridge.deliver_superseded#replacement_repeat:admission': ('_require_admission(state, ops, legacy_bundle, f"возврат PR #{repeated.pr} замены первой доставки")',
        'pass'),
    'task_bridge.deliver_superseded#returned_merged:admission': ('_require_admission(state, ops, legacy_bundle, f"возврат вмерженного PR #{pr} ревизии {n}")',
        'pass'),
    'task_bridge.deliver_superseded#returned_open:admission': ('_require_admission(state, ops, legacy_bundle, f"возврат открытого PR #{pr} ревизии {n}")',
        'pass'),
    'task_bridge.deliver_superseded#returned_pr:admission': ('_require_admission(state, ops, legacy_bundle, f"возврат PR #{pr} ревизии {n}")',
        'pass'),
    'task_bridge.deliver_superseded#returned_v1:admission': ('_require_admission(state, ops, legacy_bundle, f"возврат PR #{v1_pr} первой доставки")',
        'pass'),
    'task_bridge.deliver_superseded#review_noop:noop': ('if traceless and unconfirmed is None:',
        'if traceless:'),
}
#: Обход невозможен (без данных продолжить нечем) — решение ревьюера с причиной.
RULED: dict[str, str] = {
    'act_check.check_delivery:not_merged':
        'PR не смёржен (или нет merged_at): нет T — перебор порядков на T невозможен, `parse_ts(None)`; обход с корректными данными подменил бы факт мержа, проверяемый этой же строкой; двойник утверждает `WaitingDelivery()`',
    'act_context.delivery_verdict:not_merged':
        'PR не смёржен: нет T и фактов доставки — продолжить без них нечем; обход с корректными данными невозможен, проверяется причина (O3) и отдельный двойник «ranges не читаются»',
    'act_journal.DevJournal._open:store':
        'хранилище журнала не открывается (битое/утраченное): продолжить без БД нечем; обход невозможен, проверяется причина (O3)',
    'act_journal.DevJournal.status:lost':
        'причина «lost» порождается хранилищем `df.publisher` (exc.reason), обработчик общий; обход невозможен (без БД нечем продолжить); двойник утверждает причину хранилища',
    'act_journal_wire.DevStoreJournal._open:missing':
        'нет файла блокировки — нет хранилища, продолжить без дескриптора нечем; обход невозможен, проверяется причина (O3)',
    'ops.RealOps.snapshot_open:refmap':
        'fetch снимка — по URL и во временный репозиторий без `remote.*`: git не применяет `remote.*.fetch` к fetch по URL (проверено), а `FETCH_HEAD` пишется во временный репозиторий, который удаляется; `--refmap=` и `--no-write-fetch-head` в `_SNAPSHOT_FETCH` — защита на случай смены источника; обход неотличим; неприкосновенность репозитория продукта проверяет двойник `test_snapshot_leaves_product_repo_untouched`',
}


def _locate(path: Path, qualified: str) -> tuple[str, ast.AST | None]:
    src = path.read_text(encoding="utf-8")
    defs = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

    def find(node: ast.AST, names: list[str]) -> ast.AST | None:
        if not names:
            return node
        # все одноимённые определения, а не первое (протокол и реализация, `@overload`)
        return next((got for c in ast.walk(node) if isinstance(c, defs) and c.name == names[0]
                     for got in [find(c, names[1:])] if got is not None), None)

    return src, find(ast.parse(src), qualified.split(".")[1:])


def _offsets(src: str) -> list[int]:
    offs = [0]
    for line in src.splitlines(keepends=True):
        offs.append(offs[-1] + len(line))
    return offs


def _guard_off(src: str, fn: ast.AST, lit: str) -> str | None:
    offs = _offsets(src)

    def pos(n: ast.AST, end: bool = False) -> int:
        return offs[(n.end_lineno if end else n.lineno) - 1] + (n.end_col_offset if end else n.col_offset)

    best = None
    for node in ast.walk(fn):
        if isinstance(node, (ast.If, ast.IfExp)):
            body = node.body if isinstance(node, ast.If) else [node.body]
            test = src[pos(node.test):pos(node.test, True)]
            text = test + "".join(src[pos(b):pos(b, True)] for b in body)
            if lit in text or (lit.startswith("if ") and lit[3:] in test):
                if best is None or pos(node) >= pos(best):
                    best = node
    if best is None:
        return None
    return src[:pos(best.test)] + "False" + src[pos(best.test, True):]


def _run(twin: str) -> str:
    for cache in ROOT.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    done = subprocess.run([sys.executable, "-B", "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", twin],
                          cwd=ROOT, capture_output=True, text=True, timeout=900,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    if done.returncode == 0:
        return "survived"
    tail = done.stdout[-4000:]
    if re.search(r"^E\s+(AssertionError|assert )|DID NOT RAISE|Regex pattern did not match|^E\s+Failed:",
                 tail, re.M):
        return "closed"
    return "error"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="act_mutation")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--only", action="append", default=[])
    p.add_argument("--check", action="store_true", help="только применимость, без двойников")
    args = p.parse_args(argv)
    results = []
    for site, comp, check, lit, twin, _tlit in ROWS:
        key = f"{site}:{comp}"
        if args.only and key not in args.only:
            continue
        path = ROOT / "governance" / f"{check.split('.')[0]}.py"
        src, fn = _locate(path, check)
        if fn is None:
            results.append({"row": key, "status": "n/a", "note": "функция проверки не найдена"})
            continue
        if key in RULED:
            results.append({"row": key, "status": "ruled", "note": RULED[key]})
            continue
        if key in BYPASS:
            old, new = BYPASS[key]
            seg = ast.get_source_segment(src, fn) or ""
            mutated = src.replace(seg, seg.replace(old, new), 1) if seg.count(old) == 1 else None
        else:
            mutated = _guard_off(src, fn, lit)
        if mutated is None:
            results.append({"row": key, "status": "n/a", "note": "оператор обхода не применим"})
            continue
        if args.check:
            results.append({"row": key, "status": "applicable"})
            continue
        path.write_text(mutated, encoding="utf-8")
        try:
            status = _run(twin)
        finally:
            path.write_text(src, encoding="utf-8")
        results.append({"row": key, "status": status, "operator": "BYPASS" if key in BYPASS else "O1"})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    blocking = [r for r in results if r["status"] not in ("closed", "ruled", "applicable")]
    for r in blocking:
        print(f"act_mutation: {r['status']}: {r['row']}", file=sys.stderr)
    print(f"act_mutation: {len(results) - len(blocking)}/{len(results)} закрыто или решено")
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
```
- [ ] **Step 5: Commit**

```bash
git add governance/act_barrier.py Makefile tests/test_act_barrier.py \
  tests/test_act_identity_table.py tests/act_mutation.py \
  docs/superpowers/plans/2026-10-12-approval-act-review.runs/
git commit -m "feat(act): барьер миграции по форджу и предпросмотр затрагиваемых заявок (§10, Q9)"
```

**Конец части B → PR devtools #2** (authority-root, мерж человеком). Перед PR — общий
интеграционный обзор пути утверждения (решение владельца 13.10, п. 3) по актуальному коду.

---

## Часть C — путь брифа (после M2 discovery)

### Task 17: Бриф `review/v1` на стороне devtools — производитель, мерж, проверка

**Сверка с релизом M0 (31.10)** — только интерфейсы, которые Task 17 потребляет от M0 и части B;
детализация задачи — на воротах части C (после M2 discovery):
- факты брифа — `act_facts.read(gh_transport, repo, pr, paths=["<dir>/brief.md",
  "<dir>/approval-request.yaml"])`; не смёржен → `(DeliveryView(merged=False), None)`;
- решение — `act_context.delivery_verdict(...)` (Task 12a) с `request` = заявка v2 (проекция
  self-hash discovery) и `legacy` = `brief_provenance.legacy_outcome`; `trust_root` в `ActV2` —
  `Trust.root` (`TrustRoot`, Task 12a), `unverified_premises` — `Trust.unverified`;
- живой пин и P/W/C — `act_status.live_policy(...)` (Task 14): C — `LivePolicy.activation`;
- агентская доставка — `act_premerge` (Task 15) с видом заявки «бриф» (ветка `brief/*`, манифест
  — `approval-request.yaml` v2); App-режим `merge-pr.sh` (Task 15a) распространяется на
  `brief/*` с меткой `human-approval-required`;
- `brief_tools repropose` — здесь (Р-14);
- вендоренная проекция `discovery-approval` проверяется так же, как `contracts/approval-facts/v3`
  в Task 11a (copy-integrity + совпадение пина с зависимостью discovery у установки).

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
  unverified_premises: tuple[str, ...], trust_root: TrustRoot)` — `unverified_premises` и
  `trust_root` обязательные, без умолчания (`as_record` сериализует оба);
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
`load_trust` читает корень доверия из `df-trust` по привязке (спека A rev 16), поэтому стенд
получает **свой** `df-trust`: репо `andrei-shtanakov/df-roles-probe-trust` с `source.env` на
стенд-источник и своим журналом; правки журнала — только PR с мержем человеком; стендовая
привязка `trust.toml` указывает на него. Первая строка evidence — **фактически прочитанные**
`repository_id` и ветка `df-trust`, SHA снимка, `repo`, `branch`, `bootstrap_activation`, номер
эпохи; расхождение с ожидаемым стендом — стоп до сценариев. Подготовка стенда:
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
- №34/№37 — потребители **той же установки** заблокированы событием общего журнала; **другая**
  установка до закрытия эпохи не обязана (стенд: вторая установка на отдельной машине или
  отдельной копии `/var/lib/df`); №36 — включая `pending`, перезапуск и последующее завершение;
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
  `trust_root` (host, `repository_id`, ветка, SHA снимка — **решения этого сценария**),
  `merge_commit`, `merged_by`, `outcome`, `expected`, `verdict`. Тест: два решения на разных
  снимках `df-trust` дают разные `trust_root` в сериализованных строках. Тест на стабах:
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
- **Ревизия 31.10** (M0 выпущен, steward#211) — хендоффы, которые часть B не исполняет:
  - установщик `df`, этап 3 (этот же репо, свой план): подключить `act_journal.ForgeEpochs` как
    `df.publisher.perimeter.EPOCHS` и передавать `DF_RUN_ID` процессам `df-exec` — до этого в
    установке `df` решения акта `Unavailable: no_binding` (Р-5);
  - discovery (M2, inbox `approve-review-v1`): то же правило `$XDG_CONFIG_HOME` для `act.toml`,
    что `act_settings.config_home` (заметки части A, п. 11);
  - dispatcher, spec-runner: `--act-mode legacy` и gate-catalog v3 при своём подъёме пина steward
    — inbox ведёт steward (§12 п. 2), devtools не дублирует.
