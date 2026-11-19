# dfl: скил darkfactory, установка, памятка (#766 срез 5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Коллега без терминала ведёт оба пути DarkFactory (личный и enterprise) разговором в Claude Code Desktop через скил `darkfactory`, который зовёт только входы `dfl`; `install.sh` ставит скил; памятка и чек-лист живых прогонов готовы; долг ревью #822 (4 minor) закрыт.

**Architecture:** Скил — проза (`SKILL.md` + `reference.md`) поверх протокола pending/reply срезов 1–4; все правила и лимиты живут в `dfl`, скил несёт только разговор. Тест-сторож держит скил в согласии с кодом: каждый вид вопроса из `lite/pending.py` упомянут, каждая команда/флаг `dfl` в тексте скила существуют в парсере. `lite/install.py` копирует каталог скила в `~/.claude/skills/darkfactory` с файлом-меткой; чужой каталог не трогает.

**Tech Stack:** Python 3.12, argparse, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-11-17-dfl-skill-desktop-design.md` (§2, §5.3, §5.4, §5.6, §6 п.5, §7). Решения владельца в разговоре 19.11: копия (не ссылка), чужой каталог — отказ, #822 чиним в этом срезе, живые прогоны — чек-лист владельцу.

## Global Constraints

- Не править: `contracts/authority-root/v1/paths.env`, `governance/discovery_contract/*`, `tests/conftest.py`; ничего вне worktree.
- Каталог скила: `skills/darkfactory/` с файлами `SKILL.md` и `reference.md`; frontmatter `name: darkfactory`.
- Цель установки: `~/.claude/skills/darkfactory`; файл-метка `.installed-by-darkfactory` в нём; каталог без метки — отказ установки с понятной причиной, ничего не перезаписано.
- Скил не упоминает команд и флагов, которых нет у `dfl` (`dfl status --json` НЕ существует — состояние: `dfl status`, `dfl pending --json`, `dfl interview --json`).
- Запреты скилу (спека §2 анти-цели, §5.4) — дословно в `SKILL.md`: не писать файлы бандла/узлов/журналов руками; не мержить и не закрывать PR; не звать `make behaviour-run` и модули `governance.*`/`lite.*` напрямую; отказ `dfl` — показать причину человеку, не обходить.
- Чьи слова (§5.3): предложение Claude помечается как предложение; принятое человеком предложение пишется как `принято предложение: <текст>`.
- Двуязычие: скил отвечает на языке человека; новые пользовательские строки `dfl` — через `i18n.t` с записью в `lite/i18n_en.py`.
- Тесты: `uv run --frozen --group governance pytest -q -p no:cacheprovider tests/test_lite_*.py`; ruff: `uv run --frozen --group selfcheck ruff check .` и `ruff format --check .`. Полный набор гоняет контроллер.
- Тесты установки никогда не пишут в настоящий `~` — каждый вызов `install.main` в тестах передаёт `skills_dir`.

## Review Focus

1. Повторный `install.sh` поверх своей прежней копии скила — заменяет целиком (удалённый из исходника файл не остаётся), не отказывает.
2. В `~/.claude/skills/darkfactory` лежит чужой скил (нет метки) — отказ ДО клонирования компонентов, текст называет путь и что делать.
3. Скил ссылается на флаг, которого у подкоманды нет (опечатка `--items` у `reply`) — сторож падает с именем команды и флага.
4. Новый вид вопроса добавлен в `lite/pending.py`, а в `reference.md` нет — сторож падает.
5. `dfl brief` с проблемами (код 1) не пишет запись сбоя и не печатает «отчёт разработчикам»; настоящий сбой `dfl brief` (нет файла, код 2) пишет как раньше.

---

### Task 1: Долг #822 — четыре minor-замечания ревью #821

**Files:**
- Modify: `lite/personal/remote_interview.py` (`status`, `_post_need_brief`), `lite/dfl.py` (`main`, help `--accept-uncovered`, вывод `dfl interview` ~3141), `lite/i18n_en.py`, `README.md` (~471–487), `docs/darkfactory-for-colleagues.md` (абзац `--accept-uncovered`, если есть)
- Test: `tests/test_lite_personal_remote_interview.py`, `tests/test_lite_dfl_personal_remote.py`

**Interfaces:**
- Produces: `dfl interview --json` личного прогона БЕЗ ключа `uncovered`; `lite.dfl.EXPECTED_EXITS: frozenset[tuple[str, int]] = frozenset({("brief", 1)})`.

- [ ] **Step 1: Тесты (падают)**
  - `test_status_has_no_uncovered` — `remote_interview.status(...)` не содержит ключа `"uncovered"`; прежний тест, писавший `_bare()` в черновик ради `uncovered`, удалить.
  - `test_need_brief_text_uses_run_language` — прогон `lang="en"`, `with i18n.language("ru")`: `submit` брифа с проблемой → текст вопроса в `pending.json` английский и совпадает с текстом, который даёт тот же вызов под `i18n.language("en")` (id одинаковы).
  - `test_brief_problems_not_recorded_as_failure` — `dfl brief` с проблемным брифом через `main`: код 1, `failure_log` не записан (файл `last.json` отсутствует / не изменён), в stderr нет строки `REPORT_HINT`.
  - `test_brief_missing_file_still_recorded` — `dfl brief --file <нет файла>`: код ≠ 0 и ≠ 1-протокольного, запись сбоя есть (как раньше).
  - `test_accept_uncovered_help_names_full_scope` — help подкоманды `brief` содержит «все замечания» (см. Step 3).
- [ ] **Step 2: Прогнать — FAIL**
- [ ] **Step 3: Реализация**
  - `status`: убрать `uncovered` и `_covered`, если больше не нужен; в `dfl.py` убрать цикл печати «функция без критерия: {fid}» и запись в `i18n_en.py`; в README убрать обещание «и функции без критерия».
  - `_post_need_brief`: строить `text` внутри `with i18n.language(lang):` (как `_confirm_text`).
  - `main`: `if code != 0 and (args.command, code) not in EXPECTED_EXITS:` перед `record`/`_failure_hint`.
  - help: `"принять бриф как есть: функции без критерия и все замечания проверки уйдут в «Уточнить»"`; та же формулировка — README и памятка.
- [ ] **Step 4: Lite-тесты и ruff — PASS**
- [ ] **Step 5: Commit** `dfl: долг ревью #821 — uncovered, код 1 brief, язык вопроса, help (#822)`

### Task 2: Скил `skills/darkfactory/` и тест-сторож

**Files:**
- Create: `skills/darkfactory/SKILL.md`, `skills/darkfactory/reference.md`
- Create: `tests/test_lite_dfl_skill.py`

**Interfaces:**
- Consumes: парсер `lite.dfl._parser()`; константы видов в `lite/pending.py` (все строковые `UPPER = "kebab"` на уровне модуля, в т.ч. `REVIEW`, `MANUAL`, `REMARKS_CONFIRM`, `EDGE`, `INTERVIEW_QUESTION`, `INTERVIEW_TOPUP`, `INTERVIEW_REPAIR`, `INTERVIEW_PERSONAL`, `BRIEF_CONFIRM`).
- Produces: `SKILL_DIR = Path("skills/darkfactory")` (используется Task 3).

Источник содержания: спека §5.3, §5.4, §5.6; памятка `docs/darkfactory-for-colleagues.md` разделы о `dfl pending`/`reply`/`answer`/`note`/`brief`; точные слова ответов — функции проверки в `lite/pending.py` (`check`, `_check_*`) и `dfl <cmd> --help`. Не выдумывать слов, которых `pending.check` не примет.

- [ ] **Step 1: Тесты-сторожа (падают)** в `tests/test_lite_dfl_skill.py`:
  - `test_skill_frontmatter` — `SKILL.md` начинается с `---`, содержит `name: darkfactory` и непустой `description:`.
  - `test_every_pending_kind_documented` — для каждой строковой константы вида из `lite/pending.py` (собрать: значения из `pending.INTERVIEW_KINDS` ∪ {`REVIEW`, `MANUAL`, `REMARKS_CONFIRM`, `EDGE`, `INTERVIEW_PERSONAL`, `BRIEF_CONFIRM`}) `` `<kind>` `` встречается в `reference.md`.
  - `test_skill_commands_exist` — каждое вхождение `` `dfl <sub>[ …]` `` в обоих файлах (регэксп по коду в обратных кавычках и в блоках ```): `<sub>` — подкоманда парсера, каждый `--flag` в той же строке-команде — опция этой подкоманды (или глобальная). Сообщение падения: `dfl <sub>: нет флага <flag>`.
  - `test_skill_has_prohibitions` — `SKILL.md` содержит подстроки `не мержи`, `make behaviour-run`, `принято предложение:`.
- [ ] **Step 2: FAIL**
- [ ] **Step 3: Написать скил.**
  - `SKILL.md` (≤ ~200 строк): frontmatter (`description` по-русски и по-английски: когда запускать — «новая тема для DarkFactory», «продолжи прогон dfl», «что ждёт dfl», «DarkFactory»); проверка готовности (`dfl` в PATH, каталог — репо цели; иначе — подсказать `install.sh`); цикл: `dfl status` → новая тема: пояснить два пути и спросить тип (`dfl run --subject '<тема>' --track personal|enterprise`) → `dfl run` → `dfl pending --json` → разговор → ответ командой из `reference.md` → `dfl run`; PR на мерж — ссылка человеку, ждать его «вмержил», затем `dfl run`; старт реализации — предупредить, что долго, `dfl run` в фоне, «как дела» — `dfl status -v`; поведение в разговоре (подсказка/варианты помечены как предложение, «зачем вопрос», ответы по теме из памятки, «что отвечено» — `dfl interview --json` / журнал); чьи слова (§5.3); личное интервью: `dfl note` после каждого обмена, бриф JSON по `prompt` из `pending --json`, `dfl brief`, при коде 1 — править и сдать снова, `--accept-uncovered` только с согласия человека; запреты (Global Constraints).
  - `reference.md`: таблица «вид → что спрашивает → чем отвечать (команда и допустимые слова)» по всем видам; поля `dfl pending --json`, которые скил читает (`kind`, `id`, `text`, `data.prompt` и т.п. — по фактическому коду); коды выхода `dfl reply`/`answer`/`brief`/`note`; ссылка на памятку.
- [ ] **Step 4: PASS + ruff**
- [ ] **Step 5: Commit** `dfl: скил darkfactory для Claude Code Desktop и тест-сторож (#766 срез 5)`

### Task 3: `install.sh` ставит скил

**Files:**
- Modify: `lite/install.py`
- Test: `tests/test_lite_install.py` (все существующие вызовы `install.main` — добавить `skills_dir=tmp_path/…`)

**Interfaces:**
- Consumes: `skills/darkfactory/` из Task 2.
- Produces: `SKILL_SOURCE = HERE.parent / "skills" / "darkfactory"`, `SKILL_MARKER = ".installed-by-darkfactory"`; `check_skill_target(dest: Path) -> None` (InstallError, если `dest` существует и в нём нет метки); `write_skill(skills_dir: Path, source: Path = SKILL_SOURCE) -> Path` (удаляет прежний `dest` целиком, копирует `source`, пишет метку, возвращает `dest`); `main(..., skills_dir: Path | None = None)`, умолчание `Path.home() / ".claude" / "skills"`.

- [ ] **Step 1: Тесты (FAIL)**
  - `test_write_skill_fresh` — копия совпадает с источником, метка есть.
  - `test_write_skill_replaces_own_copy` — в прежней копии лишний файл `old.md` + метка → после `write_skill` `old.md` нет.
  - `test_foreign_skill_dir_refused_before_clone` — `skills_dir/darkfactory/SKILL.md` без метки → `main` код 1, stderr содержит путь и «не установлен install.sh», `Recorder` не видел `git clone`.
  - `test_main_full_flow` дополнить: печатает `Скил: <путь>`, каталог скила создан в `skills_dir`.
- [ ] **Step 2: FAIL**
- [ ] **Step 3: Реализация** — `check_skill_target` в начале `_install` (до `check_components`), `write_skill` после обёртки, печать `Скил: {dest}`. Текст отказа: `"{dest} уже есть и не установлен install.sh — переименуйте или удалите его и повторите"` (через обычный `InstallError`).
- [ ] **Step 4: PASS + ruff**
- [ ] **Step 5: Commit** `install: ставить скил darkfactory в ~/.claude/skills (#766 срез 5)`

### Task 4: Памятка, README, чек-лист живых прогонов

**Files:**
- Modify: `docs/darkfactory-for-colleagues.md` (новый раздел «Через Claude Code Desktop»), `README.md` (строка про скил в разделе установки/dfl)
- Create: `docs/evidence/2026-11-19-dfl-skill-live-run.md`

- [ ] **Step 1:** Раздел памятки: что делает скил, как поставить (`install.sh`, перезапуск Desktop), как начать («новая тема для DarkFactory: …»), чего скил не делает (мерж — человек на GitHub по ссылке; долгую реализацию не ведёт — `dfl status -v`), что терминальный путь не меняется.
- [ ] **Step 2:** Чек-лист (статус «ожидает прогона владельцем»): шаг 0 — Desktop видит скил из `~/.claude/skills` (как проверить, что записать); прогон A — личный путь в Desktop до вмерженного spec-PR; прогон B — enterprise в Desktop до одобренной W5; прогон C — терминальная регрессия `dfl run` (личный, короткая тема). Для каждого — что записать: версия Claude Code, коммит devtools, run_id, где пришлось помогать руками, расход, вывод. Отдельно: проверка анти-целей (скил не писал файлы руками, не мержил).
- [ ] **Step 3:** `pytest tests/test_lite_dfl_skill.py` (сторож команд не ловит памятку — проверить вручную, что все `dfl …` в новом разделе существуют) + ruff.
- [ ] **Step 4: Commit** `docs: памятка Claude Code Desktop и чек-лист живых прогонов (#766 срез 5)`
