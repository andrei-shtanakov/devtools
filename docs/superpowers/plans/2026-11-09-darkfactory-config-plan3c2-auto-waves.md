# DarkFactory config — план 3c-2: автоматические волны, вердикт в журнале

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** в прогоне `test` волны W1–W5 проходят без человека: edge-check публикуется
комментарием, вердикты (голова + отпечаток/хэши) пишутся в журнал прогона, candidate-PR мержит
сам раннер через `human-merge.sh` с именованным пропуском «акт человека не требуется (test)»,
finalize мержит агент с пропуском «класс актора не применяется (test)»; цели из `targets.toml`
вне `workspace.root` работают в скриптах.

**Результат точно:** прогон `test` от подписанного брифа доходит до `completed` (S8 после W5) без
человеческих действий, на репо, правила целевой ветки которого не требуют ревью. Задачи и
`spec-runner run` — план 3c-3. `strict` не меняется.

**Architecture:** решения — по `state.mode` прогона (вход прогона уже сверил его с конфигом).
Транспорт ревью в `review-pr.sh` уже комментарием (3c-1, devtools#720); раннер читает вердикт из
маркера последнего ревью агента (`ops.latest_review_body`) и пишет его в операцию журнала. Мерж
candidate — существующим `human-merge.sh` (его test-ветка уже пускает одну учётку). Все файлы —
не authority-root: PR мержит агент без флага обхода.

**Tech Stack:** Python 3.12 (uv), pytest.

**Spec:** `docs/superpowers/specs/2026-11-07-darkfactory-config-design.md` §5.3 п.3, п.7
(«положительный вердикт обязателен… на актуальной голове, записанный в журнал»), п.8, §5.3 абзац 1.
План 3c-1: `docs/superpowers/plans/2026-11-09-darkfactory-config-plan3c1-transport-act-mode.md`.

## Решения исполнителя (проверить владельцу)

1. **Мерж candidate в `test`** — раннер вызывает `human-merge.sh <repo> <pr> --expect-head <head>`
   сразу после публикации edge-check, если edge-вердикт положительный и записан. `merge-pr.sh`
   (candidate-глоб — отказ) не трогаем.
2. **Подпись узла.** Конверт frontmatter узла (`status, approved_by, approved_at`) не меняется:
   `approved_by` = фактический мержер (`accounts.human`). Отметка «акт человека не требуется
   (test)» — поле операции заявки `act_skip = "test"` и строка в выводе; режим прогона `test` в
   `run.json`. Фиктивного «человеческого approve» нет: такого поля нигде не пишется.
3. **Finalize в `test`:** `finalize_merge_policy(target_dir, mode="test")` пропускает ось Safety
   (класс актора) с причиной «класс актора не применяется (test)»; ось Authority (`Мерж: человек`
   в CLAUDE.md цели, экосистема) остаётся. `_missing_approving_review` в `test` вместо
   «APPROVED в ревью форджи» требует записанной в журнале успешной аттестации finalize-PR на его
   голове (scope-аттестация, `review_exit == 0`).
4. **Вердикт в журнале** — функция `review_verdict.parse_marker(body) -> ReviewVerdict | None`;
   отрицательный/отсутствующий/чужой-головы вердикт — стоп с названной причиной, не успех.
5. **`FLEET_ROOT`:** `Ops.review`/`Ops.merge`/`Ops.human_merge` принимают необязательный
   `target_dir`; если он задан и лежит вне `workspace.root`, скрипт получает
   `FLEET_ROOT=<родитель>` и имя каталога как `<repo>`.

## Global Constraints

- `strict`: существующие тесты зелёные без правок ожиданий; без новых вызовов форджи на strict-пути.
- Режим — `state.mode` прогона; неизвестное значение трактуется как `strict` (сравнение `== "test"`).
- Не трогать authority-root/харнесс-пути (`governance/act_*`, `review-pr.sh`, `merge-pr.sh`,
  `human-merge.sh`, `governance/df_config.py`, `policy_rule.py`…); проверка:
  `python -c "from governance import authority_root"` — PR не должен их задевать.
- Именованные пропуски — дословно: «акт человека не требуется (test)», «класс актора не
  применяется (test)»; в выводе И в операции журнала.
- Полный набор — один раз в конце ветки, с `PYTHONPATH=<…>/steward/src`; тесты, которые без
  steward скипаются, гонять с ним.
- Строки ≤ 88, ruff + pyrefly, docstring у публичных функций; CI — Linux (`darwin_only` где нужен).

## Review Focus

1. Edge-check в `test` с вердиктом FAIL → candidate не публикуется/не мержится, прогон `stopped_review` с причиной (Task 1).
2. Голова candidate-PR сдвинулась между публикацией и мержем → `human-merge.sh` отказывает (`--expect-head`), прогон останавливается с названной причиной, не мержит чужую голову (Task 2).
3. Последнее ревью агента на finalize-PR — `verdict=request-changes` или маркер другой головы → finalize не мержится (Task 1/3).
4. Цель из `targets.toml` вне `workspace.root`: `review-pr.sh`/`merge-pr.sh`/`human-merge.sh` находят чекаут (Task 3).
5. `resume` прогона `test` на той же голове после остановки → записанный вердикт используется, платный вызов не повторяется (Task 1).

---

## PR (devtools, агент без флага обхода): Tasks 1–3

Ветка `feat/df-config-3c2`.

### Task 1: вердикт ревью и edge-check в журнале; edge-check комментарием в `test`

**Files:**
- Create: `governance/review_verdict.py`
- Modify: `governance/edge_check/publish.py` (`publish_wave` ~157-236: event, дедуп)
- Modify: `governance/runner.py` (`_step_edge` ~3857: запись головы и хэшей входа; `_finalize_wave` ~1210: запись вердикта аттестации)
- Test: `tests/test_governance_review_verdict.py` (новый), `tests/test_edge_check_publish.py`, `tests/test_governance_runner*.py` (где тестируются `_step_edge`/`_finalize_wave`)

**Interfaces:**
- Produces: `@dataclass(frozen=True) ReviewVerdict(head: str, fp: str, verdict: str)`;
  `parse_marker(body: str | None) -> ReviewVerdict | None` — ровно один маркер
  `<!-- codex-terminal-review head=<40hex> fp=<hex> verdict=approve|request-changes -->` в теле,
  последней строкой; иначе `None` (ноль/несколько маркеров, нет `verdict=`).
- Produces: `record_review(state, key: str, verdict: ReviewVerdict) -> None` — пишет в операцию
  `state.ops[key]["verdict"] = {"head":…, "fp":…, "verdict":…}` и сохраняет леджер.
- Produces: `positive_on(state, key: str, head: str) -> bool` — записанный вердикт `approve` на этой голове.
- Edge (`publish_wave`): в `state.mode == "test"` — `event="COMMENT"`; дедуп «уже опубликовано»
  по маркеру `ai-prosto-edge-check` и голове при `COMMENTED`. В операцию candidate —
  `edge_verdict = {"head": <голова>, "verdict": <PASS>, "inputs": <хэши входа из records>}`.
  `strict` — `APPROVE`, как сейчас.

- [ ] **Step 1: тесты.** `parse_marker`: strict-маркер без `verdict=` → None; один test-маркер →
  значения; два маркера → None; маркер не последней строкой → None. `publish_wave` в test →
  `ops.publish_review(event="COMMENT")`, повтор на той же голове не публикует второй раз;
  strict → `APPROVE` (без изменений). `_step_edge`/candidate в test → `edge_verdict` записан с
  головой; FAIL → `stopped_review`, candidate не создан. `_finalize_wave` в test: аттестация rc 0 →
  в `att_key` записаны `finalize_pr`, `review_exit=0` и голова finalize-PR; resume на той же
  голове не вызывает `ops.review` повторно.
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** focused tests (с steward PYTHONPATH) — PASS.
- [ ] **Step 5:** commit `feat(runner): review and edge verdicts in the run journal; edge-check as comment in test`.

### Task 2: раннер сам мержит candidate в `test`; пропуск класса актора на finalize

**Files:**
- Modify: `governance/ops.py` (новый `Ops.human_merge`, протокол + `RealOps`)
- Modify: `governance/runner.py` (`_step_candidate` ~3938-4057 хвост с `_wave_pause`; связь с `_resume_wave`/`_finalize_wave`)
- Modify: `governance/approve_node.py` (`finalize_merge_policy(target_dir, *, mode="strict")` ~71; вызовы ~2384, ~2685 передают `state.mode`; `_missing_approving_review` ~2783)
- Test: `tests/test_governance_runner*.py`, `tests/test_governance_approve_node*.py`, `tests/test_governance_ops*.py`

**Interfaces:**
- Consumes: `review_verdict.positive_on`, `edge_verdict` (Task 1).
- Produces: `Ops.human_merge(repo_name: str, pr: int, sha: str, *, target_dir: str | None = None) -> int`
  — `sh human-merge.sh <repo> <pr> --expect-head <sha>` из корня devtools; код возврата как есть.
- Produces: `runner.ACT_SKIP_TEST = "акт человека не требуется (test)"`,
  `approve_node.ACTOR_SKIP_TEST = "класс актора не применяется (test)"`.

Поведение `test`: после успешной публикации edge-вердикта `_step_candidate` при
`state.mode == "test"` и `edge_verdict` с PASS на голове candidate вызывает `ops.human_merge`; код 0 →
поле операции заявки `act_skip = "test"`, вывод `ACT_SKIP_TEST`, затем тот же путь, что `resume`
проходит для вмерженного candidate (`_finalize_wave`), в том же вызове под тем же локом; код ≠ 0 →
`stopped_merge_refused` с кодом и командой повтора (`resume`). `finalize_merge_policy(..., mode="test")`
после оси Authority возвращает `("agent", ACTOR_SKIP_TEST)` без чтения Safety.
`_missing_approving_review` в `test` возвращает `None`, если в журнале у аттестации finalize-PR
`review_exit == 0` на текущей голове PR, иначе — отказ с названной причиной.

- [ ] **Step 1: тесты.** candidate в test → `human_merge` вызван с головой candidate, `act_skip`
  записан, `ACT_SKIP_TEST` в выводе, далее finalize и следующая волна; голова уехала (`human_merge`
  вернул 3) → `stopped_merge_refused`, finalize не начат; strict → прежняя пауза `waiting_human_merge`,
  `human_merge` не вызывается. `finalize_merge_policy` test → `("agent", ACTOR_SKIP_TEST)` при
  authority `agent`, `("human", …)` при `Мерж: человек`; strict — без изменений.
  `_missing_approving_review` test: есть запись rc 0 на голове → None; запись на старой голове → отказ.
  Сквозной: прогон test W1→W5 с фейковыми ops доходит до `completed` без `waiting_human_*`.
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** focused tests — PASS.
- [ ] **Step 5:** commit `feat(runner): auto-merge candidates in test mode; actor class skipped on finalize`.

### Task 3: `FLEET_ROOT` для целей вне `workspace.root`

**Files:**
- Modify: `governance/ops.py` (`review`, `merge`, `human_merge` — необязательный `target_dir`; общий помощник env/аргумента)
- Modify: `governance/runner.py`, `governance/approve_node.py` (вызовы передают `state.target_dir`)
- Test: `tests/test_governance_ops*.py`

**Interfaces:**
- Produces: `ops.script_target(repo_name: str, target_dir: str | None) -> tuple[str, dict[str, str]]`
  — `(имя для скрипта, доп. env)`: `target_dir` None или внутри `workspace.root` → `(repo_name, {})`;
  иначе → `(Path(target_dir).name, {"FLEET_ROOT": str(Path(target_dir).parent)})`.

- [ ] **Step 1: тесты.** `script_target` — оба случая; `RealOps.review(..., target_dir=<вне корня>)`
  запускает скрипт с `FLEET_ROOT=<родитель>` и именем каталога (подмена `subprocess.run`); без
  `target_dir` — argv и env прежние.
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** focused tests — PASS; затем полный набор (контроллер).
- [ ] **Step 5:** commit `feat(ops): FLEET_ROOT for targets outside workspace.root`.

**Мерж PR:** ревью по бюджету → `merge-pr.sh` агентом (authority-root не задет).
