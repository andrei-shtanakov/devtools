# dfl: discovery v1.2 — перепин, перевод предпочтений, show вместо зонда — план

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** `dfl` работает с discovery master `91543b1` и контрактом брифа v1.2 (discovery-toolkit `cb89725`): интервью не задаёт вопросов-инструкций; ответ стейкхолдера раскладывается как «предпочтение дословно → гипотеза агента → открытый `Q-NN` для чисел»; починка GC-08 после интервью предлагает перевод на подтверждение вместо «Как проверить?»; собственный зонд сессии заменён публичной `discovery show`.

**Spec (одобрено владельцем в разговоре 14.11):** discovery#71 (постановка + закрывающий комментарий — форма записи), discovery-toolkit#29 (контракт v1.2), ответ владельца «да, начинай follow-up».

Форма записи (из discovery#71, канон — контракт v1.2):
- `FR`/`NFR`/`M` несут необязательное поле `Preference: «…»` — слова стейкхолдера дословно (в рендере `**Preference**`).
- `Acceptance`/`Target` — гипотеза-ограничение составителя; у человека подтверждается смысл.
- Недостающее число — открытый `Q-NN` (`owner_role: analyst|engineer`, `blocking: false`); требование ссылается без болда: «… не хуже значения из Q-NN». GC-08 не менялся; GC-17 — ссылка на `Q-NN` из FR/NFR/M обязана разрешаться в `Q` брифа (error), на закрытый `Q` — warning.
- Банк: инструкции составителю — строки `> `, не выпускаются; engineer — 13 вопросов вместо 15.

## Global Constraints

- Соседние репо (discovery, discovery-toolkit) не править; вендор — побайтовые копии по пину.
- authority-root (`contracts/authority-root/v1/paths.env`) не трогать.
- Числа у стейкхолдера не спрашивать: агент заводит `Q-NN`.
- Тесты: `uv run pytest tests/test_lite_*.py tests/test_df_config.py tests/test_engineer_route_smoke.py -q`; затронутые governance — `uv run --group governance pytest …`; `tools/check_discovery_vendor.py` — ok; ruff/pyrefly чисто.
- Ветка `feat/dfl-discovery-v1.2` от master; после мержа devtools#762 — влить master.

## Review Focus

1. Ответ стейкхолдера без чисел («чтобы было бесшовно») → запись с `Preference` дословно и гипотезой, без вопроса «какое значение?».
2. Гипотеза ссылается на `Q-NN`, которого нет в брифе → GC-17 error; агент обязан завести `Q` в том же ответе (тест).
3. Человек отвечает «нет, имелось в виду …» на предложенный перевод GC-08 → его слова становятся `Preference`, агент предлагает новую гипотезу (не дословный `Acceptance`).
4. Сессия, начатая на старом пине (зонд), читается `discovery show` без падения.
5. Агент недоступен при починке GC-08 → прежний вопрос «как проверить» не возвращается; пункт остаётся, «не исправлено: N».

---

### Task 1: Перепин discovery и ре-вендор контракта v1.2

**Files:** `lite/components.toml` (discovery `sha` → `91543b1…`, полный SHA из `git -C ../discovery rev-parse 91543b1`), `governance/discovery_contract/{DISCOVERY-BRIEF-CONTRACT.md,gate_check.py,PINNED.txt}` (байты из discovery-toolkit `cb89725`, digests), тесты, сломанные новыми правилами/банком (GC-17, 13 вопросов engineer, отсутствие `feasibility_review.02/.03`).

- [ ] Обновить вендор побайтово; `uv run python tools/check_discovery_vendor.py` → consistency/provenance ok.
- [ ] Обновить пин; прогнать полные наборы; починить тесты, завязанные на старый банк/правила (не ослабляя проверок).
- [ ] Commit `chore(vendor): discovery 91543b1, контракт брифа v1.2 (GC-17, Preference)`.

### Task 2: `discovery show` вместо зонда

**Files:** `lite/interview_state.py` (`load_session` и `Runner`), удалить `lite/interview_probe.py`, тесты `tests/test_lite_interview_state.py` (или где тестируется зонд).

- [ ] Выяснить формат вывода `discovery show --session <id>` (запуск в `../discovery`, `uv run discovery show --help`, код `src/discovery/cli*`), сопоставить с полями `SessionView`.
- [ ] RED-тест на разбор вывода `show` (фикстура вывода), затем `load_session` через `discovery show` (тот же способ запуска закреплённого discovery, что у `discovery status`).
- [ ] Удалить зонд и его упоминания; полные наборы зелёные.
- [ ] Commit `refactor(dfl): состояние сессии через discovery show вместо зонда`.

### Task 3: Перевод предпочтений в интервью

**Files:** `lite/structure.py` (промпт разбора ответа и проверка формы), тесты `tests/test_lite_structure.py`.

- [ ] Тесты: (a) ответ без чисел → FR/NFR/M с `Preference` дословно и гипотезой `Acceptance`/`Target`; (b) нужно число → запись `Q-NN` (`owner_role: analyst`, `blocking: false`) в том же ответе и ссылка «не хуже значения из Q-NN»; (c) ссылка на несуществующий `Q` → форма отвергнута (как GC-17), агент переспрашивается; (d) уточняющий вопрос человеку о числе не задаётся (`_item_gaps` не просит Acceptance-число).
- [ ] Промпт: роль аналитика-переводчика; правила формы v1.2 из контракта.
- [ ] Commit `feat(dfl): интервью — предпочтение дословно, гипотеза агента, числа в Q-NN`.

### Task 4: GC-08 — перевод на подтверждение

**Files:** `lite/gate_repair.py` (`_fill_criteria`), тесты `tests/test_lite_gate_repair.py`, `docs/darkfactory-for-colleagues.md` («Критерии проверки», раздел интервью).

- [ ] Тесты: агент предлагает `Acceptance` (+ `Q-NN` при нужде) по пункту и держащему ответу; человек: «да» → записано (+ `Q` в тот же держащий ответ); «нет, …» → слова человека в `Preference`, новая гипотеза (один повтор), затем записано; «пропустить» → не исправлено; агент недоступен → не исправлено, без вопроса «как проверить».
- [ ] Реализация через `ask`/`agent`/`resend` как у `_fill_traces`.
- [ ] Доку обновить.
- [ ] Commit `feat(dfl): GC-08 — гипотеза агента на подтверждение вместо «как проверить»`.
