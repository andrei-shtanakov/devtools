# conductor срез 1, часть A — инфраструктура записи. Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** дать conductor безопасный путь записи в GitHub от имени App — от разрешения до учёта, — который в этой части исполняет пустой план (тень) и проверяется фейком GitHub и локальным HTTP-сервером.

**Architecture:** новые модули рядом с ядром среза 0 (`conductor/`), ядро не меняется, кроме поля роадмапа `enabled_actions`. Все вызовы токеном App/JWT идут через один HTTP-адаптер без следования перенаправлениям (`conductor/http.py`) и один журнал вызовов установки (`conductor/app_calls.py`), выводящий запрет по лимиту. Единственный исполнитель `conductor/writer.py` проводит каждую запись плана через проверки §5.1 в фиксированном порядке: полномочия — по свежему роадмапу перед каждым шагом с потолком прогона из CLI и уровнем позиции (О §2.4), основание — повторной проверкой записи (`revalidate`), эффект — независимым чтением цели после мутации (verify, О §5.0 шаг 3); ведёт операционное состояние (`conductor/opstate.py`) и учёт мутаций (`conductor/journal.py`). Все пути записи (`run --config`, `init-state`, `--recover`) сами берут lock хоста. Планировщики действий — часть B; здесь хук `conductor/actions/__init__.py` возвращает пустой список.

**Tech Stack:** Python ≥ 3.12, stdlib (`urllib.request`, `tomllib`, `hashlib`, `json`), PyJWT[crypto] (группа `conductor`, входит в `default-groups`), pytest, ruff, pyrefly.

**Spec:** `docs/superpowers/specs/2026-09-30-conductor-slice-1-design.md` (rev 17; до переноса в devtools — `_cowork_output/2026-09-30-conductor-slice1/2026-09-30-conductor-slice-1-design.md`) и общая `docs/superpowers/specs/2026-09-29-conductor-design.md` (rev 12). Ссылки «§N» ниже — спека среза, «О §N» — общая.

**Где исполнять:** devtools, отдельный worktree от `origin/master` (не основной чекаут — там чужая сессия), ветка `feat/conductor-slice1-a`. Все команды — из корня этого worktree.

## Global Constraints

- Каталог операционного состояния флота — `/srv/conductor/opstate` (не `/srv/conductor/state`: там `runs/` и `conductor.lock` среза 0, а `init-state` требует пустой каталог). Решение владельца 1 (2026-10-01); пример §4.2 спеки исправлен.
- Lock хоста — `/srv/conductor/state/conductor.lock` (`[run] lock`; один на все профили, вне любого `state_dir` — конфиг с lock внутри `state_dir` отвергается). Его берут сами `run --config`, `init-state` и `init-state --recover` (`flock -n`); занят — `run` пропускается с кодом 0, `init-state` — код 4. Внешний `flock` вокруг этих команд не используется (ревью P1-3).
- Полномочия шага 6 — по роадмапу, перечитанному перед шагом: `run_level = min(level_cap, autonomy)`, где `level_cap` — `--level` CLI (по умолчанию 0), 0 при `--replay` и при роадмапе не с origin (`--roadmap`); уровень позиции — `PlanRecord.authority(roadmap, run_level)` (О §2.4: класс, фокус, `focus.autonomy`). Таймер-писатель передаёт `--level 3` (ревью P1-2).
- Шаг 8 — `PlanRecord.revalidate(m) -> str | None` перед КАЖДОЙ мутацией: свежее чтение основания записи (ревизия, доказательство, период, ответ), а не только «цель открыта»; причина — в журнал (ревью P1-1, наполнение — часть B).
- verify (О §5.0 шаг 3) — независимое чтение цели после успешной мутации; ответ самой мутации эффекта не доказывает; не прочитано или не совпало → `uncertain`, зависимые шаги не выполняются (ревью P2-1).

- Токен App и JWT — только в памяти процесса и только в заголовке `Authorization` HTTP-адаптера; ни в env дочерних процессов, ни в файлы, ни в `gh` (§4.3, §13 п. 14).
- Перенаправления не выполняются: любой 3xx — исход `moved` (§4.5).
- Порядок проверок перед каждой мутацией строго: 1 забор → 2 состояние → 3 роадмап → 4 писатель → 5 ключ → 6 разрешение → 7 покрытие → 8 субъект и цель → 8a лимит → 9 задержка → 10 бюджет (§5.1).
- Исходы вызова: `ok` (2xx, тело разобрано, GraphQL без `errors`); `failed` — 400, 401, 403, 404, 410, 422 и GraphQL `NOT_FOUND`/`FORBIDDEN`/`UNPROCESSABLE`; `rate_limited` — 403/429 с `x-ratelimit-remaining: 0` или `retry-after` или «secondary rate limit», GraphQL `RATE_LIMITED`; `moved` — 3xx; `uncertain` — всё прочее (§4.5).
- Значения полей маркера — `h1-` + 16 hex `sha256` от `json.dumps([тип, *поля], ensure_ascii=False, separators=(",", ":"))`; `n` — десятичное целое без ведущих нулей; алфавит значения `[a-z0-9-]+` (§4.4).
- Карантин — 65 минут; задержка повтора неопределённой попытки — 60 минут по `effect_key` (§4.6, §5.3).
- Неизвестный срок лимита — `1 ч · 2^k`, `k` = 0..3 (1, 2, 4, 8 ч), `k` по классу `create`/`update`/`service`, сброс только успехом своего класса (§5.5).
- Устойчивая запись: временный файл в том же каталоге → `fsync(file)` → `os.replace` → `fsync(dir)`; строки jsonl — `fsync` после каждой (§4.6).
- Секреты (`ghs_…`, `ghp_…`, `github_pat_…`, `eyJ…`-JWT, PEM) вырезаются из любого текста до записи в журнал (§4.3).
- Код: type hints, docstrings публичных функций, строки ≤ 88. После каждой задачи: `uv run --group selfcheck ruff format conductor tests/conductor`, `uv run --group selfcheck ruff check --fix conductor tests/conductor`, `uv run --group selfcheck pyrefly check conductor tests/conductor` (0 ошибок; полный `pyrefly check` по репо даёт прежние ошибки вне conductor — не трогать).
- Тесты: `uv run pytest tests/conductor -q` (группа `conductor` с PyJWT входит в `default-groups` с Task A8 — CI и служба ставят её без правок `.github/`; фактические команды CI проверены, решение владельца 6). Новые тесты НЕ импортировать из `tests/test_governance_runner.py`.
- Код плана: блоки ```python — файлы целиком (Create), блоки ```diff — правки существующих файлов (`git apply` из корня worktree). Проверка плана — `_cowork_output/2026-09-30-conductor-slice1/tools/build_plan.py` в чистый клон.

## Review Focus

1. **Повреждённое или усечённое операционное состояние в момент шага** — запись не должна уйти «потому что файл не прочитался как пустой». Тест: каждый файл `state_dir` по отдельности удалён / обрезан посередине / с чужим поколением → `open_state` даёт карантин или `OPSTATE-UNINITIALIZED`, `Writer` — ноль отправок (Task A5, A11).
2. **Сбой записи `begin` журнала установки или попытки** (диск полон, права) — вызов не уходит. Тест: `append_line` бросает `OSError` → ноль запросов транспорта (Task A8, A11).
3. **Ответ 2xx с битым JSON или пустым телом на мутацию** — не `ok`. Тест: `classify` на `b"{"`/`b""` для POST → `uncertain` (Task A7).
4. **Часы хоста уходят назад между прогонами** — задержка и карантин не должны сократиться: сравнение «моложе 60 минут» и «до конца карантина» считается и при `now < started_at`. Тест: `now = started_at − 5 мин` → `delayed` и `quarantined` истинны (Task A5).
5. **Чужой `Location` на ответе мутации (переименованный/перенесённый репо)** — ни одного второго запроса. Тест: локальный сервер, 307/308 на POST → один запрос, исход `moved` (Task A7).
6. **Запись без полномочий позиции или потолка CLI** — `--level 0`, `--replay`, `--roadmap`, `focus.autonomy = 0` при `autonomy = 1`, отзыв фокуса между шагами → ноль мутаций (Task A11, A12).
7. **2xx без видимого эффекта** — `uncertain`, зависимый шаг не идёт (Task A10, A11).
8. **Два процесса записи одновременно** (таймер + ручной, run + `--recover`) — второй не начинает (Task A12).

## Ревью владельца 2026-10-01: где закрыто в части A

| Пункт | Что изменено | Тест, краснеющий на прежнем коде плана |
|---|---|---|
| P1-2 | `Writer(level_cap=…)`; `_permission` по свежему роадмапу: `run_level`, `autonomy`, `enabled_actions`, `position_level` (`PlanRecord.authority`, `Step.authority`); CLI `level_cap` (0 при `--replay`/`--roadmap`); drop-in — `--level 3` (B11) | `test_writer.py::test_cli_level_zero_means_no_mutations`, `test_focus_autonomy_zero_blocks_position`, `test_focus_revoked_between_steps`, `test_shadow_names_cli_ceiling`; `test_cli_writer.py::test_replay_never_writes_live` (+ двойник `test_live_cap_twin_writes`), `test_level_cap_rules` |
| P1-3 | `host_lock` в `init-state`, `--recover` и `run --config`; `[run] lock` | `test_cli_writer.py::test_run_and_init_state_take_host_lock` (lock держит другой процесс), `test_lock_is_released_after_run` |
| P2-1 | `gh_write.verify(client, m, resp, bot, node_id) -> str | None` — контрольное чтение цели (комментарий по id, issue, `isPinned`) | `test_gh_write.py::test_verify_2xx_without_visible_effect_is_uncertain`, `test_verify_unreadable_control_read_is_uncertain`; `test_writer.py::test_unconfirmed_effect_is_uncertain_and_blocks_dependent` |
| P2-2 (механизм) | `Step.optional` (сбой не блокирует следующие), `Step.revision` (серия у вопроса своя) | `test_writer.py::test_queue_body_failure_blocks_questions`, `test_queue_pin_failure_does_not_block_questions`, `test_question_series_keyed_by_step_revision` |
| P2-3 (механизм) | попытка хранит `op`/`expected`; `OpState.open_attempts()` | `test_opstate.py::test_open_attempts_one_per_effect_with_identity` (поиск и подключение — B3, B9) |
| P2-5 | `init_host`: журнала нет при существующем `HOST_INIT` → событие `lost` | `test_app_calls.py::test_init_host_after_lost_journal_keeps_ban`; `test_cli_writer.py::test_init_state_of_new_profile_keeps_lost_ban` |
| P1-1 (механизм) | `PlanRecord.revalidate(m) -> str | None` вызывается перед каждой мутацией с разрешённой целью | `test_writer.py::test_revalidate_sees_resolved_step_and_runs_before_each_step` (наполнение по действиям — B5–B8) |

Мутационная проверка (Task A11, шаг 5) расширена: удаление любого из условий выше по одному даёт хотя бы один FAIL.

---

## Файлы части A

| Файл | Ответственность |
|---|---|
| `conductor/roadmap.py` (изм.) | поле `enabled_actions`, константа `ACTIONS` |
| `conductor/host_config.py` | разбор `/srv/conductor/*.toml`, `ConfigError`, lock хоста, зонтик профиля |
| `conductor/markers.py` | `h1`, кодер/декодер маркера, экранирование, классификация комментария |
| `conductor/durable.py` | устойчивая атомарная запись и jsonl |
| `conductor/opstate.py` | `INIT`, попытки, серии и эпизоды, карантин, `init_state`/`recover_state`/`open_state` |
| `conductor/app_calls.py` | журнал вызовов установки, вывод `blocked_until` |
| `conductor/http.py` | транспорт `urllib` без перенаправлений, `classify`, `rate_info` |
| `conductor/gh_app.py` | JWT, токен установки, `check_key`, `check_installation`, `covers`, единый `call` |
| `conductor/journal.py` | журнал прогона, учёт мутаций `calls.jsonl`, `redact` |
| `conductor/gh_write.py` | белый список мутаций, проверка фактической цели, `verify` — контрольное чтение |
| `conductor/writer.py` | `Step`, `PlanRecord`, `Writer` — проверки §5.1 (полномочия по свежему роадмапу, `revalidate`), матрица §5.2 |
| `conductor/actions/__init__.py` | хук `plan_records` (часть A: пустой) |
| `conductor/__main__.py` (изм.) | `init-state [--recover]`, `run --config` под lock хоста, потолок `level_cap` |
| `pyproject.toml` (изм.) | группа `conductor = ["pyjwt[crypto]>=2.8"]` в `default-groups` |
| `tests/conductor/fake_app.py` | фейковый GitHub (мир issues/комментариев) для тестов исполнителя |
| `tests/conductor/test_*.py` | по файлу на модуль |

---

### Task A1: поле роадмапа `enabled_actions`

**Files:**
- Modify: `conductor/roadmap.py` (константы вверху; `Roadmap`; `parse_roadmap`)
- Test: `tests/conductor/test_roadmap_enabled.py`

**Interfaces:**
- Produces: `conductor.roadmap.ACTIONS: tuple[str, ...]`; `Roadmap.enabled_actions: frozenset[str]` (пусто по умолчанию).

- [ ] **Step 1: Write the failing test**

```python
"""enabled_actions роадмапа (спека среза 1, §2.1)."""

from conductor.roadmap import ACTIONS, parse_roadmap
from tests.conductor.fixtures import EPICS, ROADMAP


def _parse(extra: str):
    return parse_roadmap(
        ROADMAP.replace("autonomy = 0", f"autonomy = 1\n{extra}"), EPICS
    )


def test_absent_field_means_nothing_enabled() -> None:
    rm = parse_roadmap(ROADMAP, EPICS)
    assert rm.valid and rm.enabled_actions == frozenset()


def test_known_actions_enabled_separately() -> None:
    rm = _parse('enabled_actions = ["owner_queue", "nudge"]')
    assert rm.valid
    assert rm.enabled_actions == {"owner_queue", "nudge"}
    assert "pr_nudge" not in rm.enabled_actions


def test_unknown_name_is_rm_invalid() -> None:
    rm = _parse('enabled_actions = ["owner_queue", "todo_hygiene_pr"]')
    assert not rm.valid
    assert any("todo_hygiene_pr" in f.detail for f in rm.findings)


def test_duplicate_and_wrong_type_are_rm_invalid() -> None:
    assert not _parse('enabled_actions = ["nudge", "nudge"]').valid
    assert not _parse('enabled_actions = "nudge"').valid
    assert not _parse("enabled_actions = [1]").valid


def test_actions_constant_is_slice_scope() -> None:
    assert ACTIONS == (
        "owner_queue",
        "notify_satisfied",
        "nudge",
        "pr_nudge",
        "close_shipped",
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/conductor/test_roadmap_enabled.py -q`
Expected: FAIL — `ImportError: cannot import name 'ACTIONS'`.

- [ ] **Step 3: Write minimal implementation**

Применить к `conductor/roadmap.py` (`git apply`; константа `ACTIONS`, поле `Roadmap.enabled_actions`, разбор `_enabled`):

```diff
diff --git a/conductor/roadmap.py b/conductor/roadmap.py
index 5c3f1ad..a5ca62e 100644
--- a/conductor/roadmap.py
+++ b/conductor/roadmap.py
@@ -22,6 +22,14 @@ LIMIT_DEFAULTS: dict[str, tuple[int, int, int]] = {
     "stale_after_days": (3, 1, 60),
     "renudge_after_days": (7, 1, 60),
 }
+# Действия среза 1 (спека среза 1, §1): только они включаются роадмапом.
+ACTIONS: tuple[str, ...] = (
+    "owner_queue",
+    "notify_satisfied",
+    "nudge",
+    "pr_nudge",
+    "close_shipped",
+)
 Klass = Literal["focus", "parked", "background"]
 
 
@@ -48,6 +56,7 @@ class Roadmap:
     limits: dict[str, int] = field(
         default_factory=lambda: {k: v[0] for k, v in LIMIT_DEFAULTS.items()}
     )
+    enabled_actions: frozenset[str] = frozenset()
     valid: bool = False
     findings: tuple[Finding, ...] = ()
 
@@ -153,6 +162,20 @@ def _parked(raw: Any, epics: dict[str, dict], errors: list[Finding]) -> list[str
     return items
 
 
+def _enabled(raw: Any, errors: list[Finding]) -> frozenset[str]:
+    """enabled_actions (срез 1, §2.1): нет поля — ничего не включено."""
+    if raw is None:
+        return frozenset()
+    if not isinstance(raw, list) or not all(isinstance(a, str) for a in raw):
+        errors.append(_invalid("enabled_actions должен быть массивом строк"))
+        return frozenset()
+    for name in sorted(set(raw) - set(ACTIONS)):
+        errors.append(_invalid(f"enabled_actions: неизвестное действие {name!r}"))
+    for name in sorted({a for a in raw if raw.count(a) > 1}):
+        errors.append(_invalid(f"enabled_actions: {name!r} дважды"))
+    return frozenset(a for a in raw if a in ACTIONS)
+
+
 def parse_roadmap(text: str | None, epics: dict[str, dict]) -> Roadmap:
     """Разбор и валидация (§2.1–2.2); ошибки становятся RM-INVALID."""
     if text is None:
@@ -182,6 +205,7 @@ def parse_roadmap(text: str | None, epics: dict[str, dict]) -> Roadmap:
     for epic in sorted({e for e in seen if seen.count(e) > 1}):
         errors.append(_invalid(f"эпик {epic} встречается дважды"))
     limits = _limits(data.get("limits"), errors)
+    enabled = _enabled(data.get("enabled_actions"), errors)
     return Roadmap(
         autonomy=top,
         writer_host=writer,
@@ -189,6 +213,7 @@ def parse_roadmap(text: str | None, epics: dict[str, dict]) -> Roadmap:
         focus=focus,
         parked=frozenset(parked),
         limits=limits,
+        enabled_actions=enabled,
         valid=not errors,
         findings=tuple(errors + warnings),
     )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/conductor/test_roadmap_enabled.py tests/conductor/test_roadmap.py -q`
Expected: PASS (все).

- [ ] **Step 5: Commit**

```bash
git add conductor/roadmap.py tests/conductor/test_roadmap_enabled.py
git commit -m "conductor: enabled_actions в роадмапе (срез 1, §2.1)"
```

---

### Task A2: конфиг хоста

**Files:**
- Create: `conductor/host_config.py`
- Test: `tests/conductor/test_host_config.py`

**Interfaces:**
- Produces: `HostConfig` (поля ниже, в том числе `lock: Path`), `ConfigError`, `load_host_config(path: Path) -> HostConfig`, `STOP_POINTS: frozenset[str]`, `FLEET_STATE_DIR: Path`, `SHARED_DIR: Path`, `LOCK_PATH: Path` (`/srv/conductor/state/conductor.lock`; `[run] lock` необязателен, должен быть абсолютным и вне `state_dir`).

- [ ] **Step 1: Write the failing test**

```python
"""Конфиг хоста (спека среза 1, §4.2)."""

from pathlib import Path

import pytest

from conductor.host_config import LOCK_PATH, ConfigError, load_host_config

FLEET = """
[app]
app_id = 11
installation_id = 22
private_key = "/srv/conductor/keys/c.pem"
[run]
profile = "fleet"
shadow = true
state_dir = "/srv/conductor/opstate"
"""
ACCEPTANCE = """
[app]
app_id = 11
installation_id = 22
private_key = "/srv/conductor/keys/c.pem"
[run]
profile = "acceptance"
shadow = false
state_dir = "/srv/conductor/acceptance"
[acceptance]
sandbox = "own/conductor-sandbox"
outside = ["own/conductor-sandbox-outside"]
stop_points = ["after_send"]
"""


def _load(tmp_path: Path, text: str):
    path = tmp_path / "c.toml"
    path.write_text(text, encoding="utf-8")
    return load_host_config(path)


def test_fleet_config(tmp_path: Path) -> None:
    cfg = _load(tmp_path, FLEET)
    assert (cfg.app_id, cfg.installation_id, cfg.profile, cfg.shadow) == (
        11,
        22,
        "fleet",
        True,
    )
    assert cfg.state_dir == Path("/srv/conductor/opstate")
    assert cfg.sandbox is None and cfg.stop_points == frozenset()


def test_acceptance_config(tmp_path: Path) -> None:
    cfg = _load(tmp_path, ACCEPTANCE)
    assert cfg.sandbox == "own/conductor-sandbox"
    assert cfg.outside == ("own/conductor-sandbox-outside",)
    assert cfg.stop_points == {"after_send"}


@pytest.mark.parametrize(
    "text",
    [
        FLEET + '[acceptance]\nsandbox = "own/x"\n',  # acceptance в fleet
        FLEET.replace('profile = "fleet"', 'profile = "prod"'),
        FLEET.replace("shadow = true", 'shadow = "yes"'),
        FLEET.replace("app_id = 11", "app_id = 0"),
        FLEET + "[extra]\nx = 1\n",
        FLEET.replace("[run]", "[run]\nunknown = 1"),
        ACCEPTANCE.replace('"/srv/conductor/acceptance"', '"/srv/conductor/opstate"'),
        ACCEPTANCE.replace('["after_send"]', '["anywhere"]'),
        ACCEPTANCE.replace('sandbox = "own/conductor-sandbox"\n', ""),
        FLEET.replace('state_dir = "/srv/conductor/opstate"', 'state_dir = "rel"'),
        FLEET.replace("[run]", '[run]\nlock = "/srv/conductor/opstate/x.lock"'),
        FLEET.replace("[run]", '[run]\nlock = "rel.lock"'),
        "x = [",
    ],
)
def test_invalid_configs_raise(tmp_path: Path, text: str) -> None:
    with pytest.raises(ConfigError):
        _load(tmp_path, text)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_host_config(tmp_path / "absent.toml")


def test_lock_shared_by_profiles_and_outside_state(tmp_path: Path) -> None:
    fleet, acc = _load(tmp_path, FLEET), _load(tmp_path, ACCEPTANCE)
    assert fleet.lock == acc.lock == LOCK_PATH
    assert not LOCK_PATH.is_relative_to(fleet.state_dir)
    custom = _load(tmp_path, FLEET.replace("[run]", '[run]\nlock = "/tmp/c.lock"'))
    assert custom.lock == Path("/tmp/c.lock")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/conductor/test_host_config.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor.host_config'`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Конфиг хоста conductor (спека среза 1, §4.2).

Ошибка конфига — `ConfigError` (находка CFG-INVALID): записей нет, чтение и
выдача — как в срезе 0.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

# state/ среза 0 занят runs/ и conductor.lock — init-state требует пустой каталог
FLEET_STATE_DIR = Path("/srv/conductor/opstate")
SHARED_DIR = Path("/srv/conductor/shared")
# Один lock хоста на все профили: они делят журнал установки (§5.5). Он вне
# восстанавливаемых каталогов состояния — recover/init-state его не трогают.
LOCK_PATH = Path("/srv/conductor/state/conductor.lock")
STOP_POINTS = frozenset({"after_comment", "before_close", "after_intent", "after_send"})
REPO_RE = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
Profile = Literal["fleet", "acceptance"]

_TOP = {"app", "run", "acceptance"}
_APP = {"app_id", "installation_id", "private_key"}
_RUN = {"profile", "shadow", "state_dir", "lock"}
_OPTIONAL = {"lock"}
_ACC = {"sandbox", "outside", "stop_points"}


class ConfigError(Exception):
    """CFG-INVALID: конфиг хоста не принят."""


@dataclass(frozen=True)
class HostConfig:
    """Разобранный конфиг хоста."""

    app_id: int
    installation_id: int
    private_key: Path
    profile: Profile
    shadow: bool
    state_dir: Path
    lock: Path = LOCK_PATH
    sandbox: str | None = None
    outside: tuple[str, ...] = ()
    stop_points: frozenset[str] = frozenset()


def _table(data: dict[str, Any], name: str, keys: set[str]) -> dict[str, Any]:
    raw = data.get(name)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"[{name}] не таблица")
    if unknown := set(raw) - keys:
        raise ConfigError(f"[{name}]: неизвестные ключи {sorted(unknown)}")
    if missing := keys - _OPTIONAL - set(raw) if name in ("app", "run") else set():
        raise ConfigError(f"[{name}]: нет ключей {sorted(missing)}")
    return raw


def _positive(value: Any, key: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{key}={value!r}: нужно целое > 0")
    return value


def _abs_path(value: Any, key: str) -> Path:
    if not isinstance(value, str) or not value.startswith("/"):
        raise ConfigError(f"{key}={value!r}: нужен абсолютный путь")
    return Path(value)


def _repos(value: Any, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(v, str) and REPO_RE.match(v) for v in value
    ):
        raise ConfigError(f"{key}: нужен список owner/name")
    return tuple(value)


def load_host_config(path: Path) -> HostConfig:
    """Прочитать и проверить конфиг хоста; любая ошибка — ConfigError."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"{path}: {exc.strerror or exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: TOML: {exc}") from exc
    if unknown := set(data) - _TOP:
        raise ConfigError(f"неизвестные таблицы {sorted(unknown)}")
    if "app" not in data or "run" not in data:
        raise ConfigError("нужны таблицы [app] и [run]")
    app, run = _table(data, "app", _APP), _table(data, "run", _RUN)
    acc = _table(data, "acceptance", _ACC)
    profile = run["profile"]
    if profile not in ("fleet", "acceptance"):
        raise ConfigError(f"profile={profile!r}")
    if not isinstance(run["shadow"], bool):
        raise ConfigError(f"shadow={run['shadow']!r}: нужно true/false")
    base = {
        "app_id": _positive(app["app_id"], "app_id"),
        "installation_id": _positive(app["installation_id"], "installation_id"),
        "private_key": _abs_path(app["private_key"], "private_key"),
        "shadow": run["shadow"],
        "state_dir": _abs_path(run["state_dir"], "state_dir"),
        "lock": _abs_path(run.get("lock", str(LOCK_PATH)), "lock"),
    }
    if base["lock"].is_relative_to(base["state_dir"]):
        raise ConfigError("lock внутри state_dir: init-state --recover его перенёс бы")
    if profile == "fleet":
        if "acceptance" in data:
            raise ConfigError("[acceptance] в профиле fleet")
        return HostConfig(profile="fleet", **base)
    sandbox = acc.get("sandbox")
    if not isinstance(sandbox, str) or not REPO_RE.match(sandbox):
        raise ConfigError(f"sandbox={sandbox!r}: нужен owner/name")
    stops = acc.get("stop_points", [])
    if not isinstance(stops, list) or not set(stops) <= STOP_POINTS:
        raise ConfigError(f"stop_points={stops!r}: допустимы {sorted(STOP_POINTS)}")
    if base["state_dir"] == FLEET_STATE_DIR:
        raise ConfigError("state_dir acceptance совпадает с боевым")
    return HostConfig(
        profile="acceptance",
        sandbox=sandbox,
        outside=_repos(acc.get("outside", []), "outside"),
        stop_points=frozenset(stops),
        **base,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/conductor/test_host_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add conductor/host_config.py tests/conductor/test_host_config.py
git commit -m "conductor: конфиг хоста fleet/acceptance (срез 1, §4.2)"
```

---

### Task A3: маркеры

**Files:**
- Create: `conductor/markers.py`
- Test: `tests/conductor/test_markers.py`

**Interfaces:**
- Produces: `h1(kind: str, *parts: str) -> str`; `Marker(kind: str, fields: tuple[tuple[str, str], ...])` с `.get(key) -> str`; `MarkerError`; `make(kind: str, **fields: str) -> Marker`; `render(m: Marker) -> str`; `escape(text: str) -> str`; `with_marker(text: str, m: Marker) -> str`; `parse_body(body: str) -> Marker | None`; `classify(comment: dict, bot_login: str) -> tuple[Literal["event", "edited", "foreign", "none"], Marker | None]`; `FIELDS: dict[str, tuple[str, ...]]`.
- Comment dict (часть B дочитывает): `{"id": int, "author": str, "body": str, "created_at": str, "updated_at": str}`.

- [ ] **Step 1: Write the failing test**

```python
"""Маркеры (спека среза 1, §4.4)."""

import pytest

from conductor.markers import (
    MarkerError,
    classify,
    escape,
    h1,
    make,
    parse_body,
    render,
    with_marker,
)

BOT = "conductor[bot]"


def test_h1_is_stable_and_alphabet_safe() -> None:
    a = h1("wait", "devtools", "x", "date>=2026-10-01")
    assert a == h1("wait", "devtools", "x", "date>=2026-10-01")
    assert a.startswith("h1-") and len(a) == 19
    assert a != h1("wait", "devtools", "x", "date>=2026-10-02")
    assert h1("pr", "devtools!42") != h1("pr", "devtools!43")
    assert h1("path", "repo", "docs/путь с пробелом.md").startswith("h1-")


def test_render_parse_round_trip_every_kind() -> None:
    v = h1("x", "1")
    cases = {
        "q": {"id": v},
        "sat": {"evidence": v, "wait": v},
        "nudge": {"n": "2", "p": v, "wait": v},
        "prnudge": {"n": "1", "pr": v},
        "close": {"evidence": v, "node": v, "period": v},
        "queue": {"projection": v},
    }
    for kind, fields in cases.items():
        m = make(kind, **fields)
        assert parse_body(with_marker("текст", m)) == m


def test_make_rejects_bad_values() -> None:
    v = h1("x", "1")
    with pytest.raises(MarkerError):
        make("sat", evidence=v)  # нет поля wait
    with pytest.raises(MarkerError):
        make("sat", evidence=v, wait="devtools!42")  # сырой id
    with pytest.raises(MarkerError):
        make("nudge", n="01", p=v, wait=v)
    with pytest.raises(MarkerError):
        make("other", id=v)


def test_marker_must_be_single_and_last() -> None:
    m = make("q", id=h1("q", "a"))
    body = with_marker("текст", m)
    assert parse_body(body + "\nхвост") is None
    assert parse_body(render(m) + "\n" + body) is None
    assert parse_body("текст") is None


def test_quoted_marker_is_escaped_and_never_parsed() -> None:
    m = make("q", id=h1("q", "a"))
    quoted = "цитата: " + render(make("q", id=h1("q", "b")))
    body = with_marker(quoted, m)
    assert parse_body(body) == m
    assert "<!--" not in escape(quoted) and "conductor:" not in escape(quoted)
    assert parse_body(escape(render(m))) is None


def test_classify_comment() -> None:
    m = make("q", id=h1("q", "a"))
    base = {"id": 1, "body": with_marker("x", m), "created_at": "t", "updated_at": "t"}
    assert classify({**base, "author": BOT}, BOT) == ("event", m)
    assert classify({**base, "author": "owner"}, BOT) == ("foreign", None)
    assert classify({**base, "author": BOT, "updated_at": "t2"}, BOT) == (
        "edited",
        None,
    )
    assert classify({**base, "author": BOT, "body": "x"}, BOT) == ("none", None)
    queue = {
        **base,
        "author": BOT,
        "body": with_marker("x", make("queue", projection=h1("p"))),
    }
    assert classify(queue, BOT) == ("none", None)  # проекция — не событие
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/conductor/test_markers.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor.markers'`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Управляющие маркеры conductor (спека среза 1, §4.4; О §5.10).

Маркер — ровно один блок `<!-- conductor:v1 <kind> k=v … -->` последней
непустой строкой. Значения — `h1-<16 hex>` (хэш канонической сериализации)
или десятичное `n`: сырой id (условие, путь, `repo!N`) в маркер не попадает.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Literal

FIELDS: dict[str, tuple[str, ...]] = {
    "q": ("id",),
    "sat": ("evidence", "wait"),
    "nudge": ("n", "p", "wait"),
    "prnudge": ("n", "pr"),
    "close": ("evidence", "node", "period"),
    "queue": ("projection",),
}
EVENT_KINDS = frozenset(FIELDS) - {"queue"}
NUMERIC = frozenset({"n"})
TAG = "conductor:v1"
H1_RE = re.compile(r"^h1-[0-9a-f]{16}$")
N_RE = re.compile(r"^[1-9][0-9]*$")
LINE_RE = re.compile(r"^<!-- conductor:v1 ([a-z]+)((?: [a-z]+=[a-z0-9-]+)+) -->$")
Kind = Literal["event", "edited", "foreign", "none"]


class MarkerError(ValueError):
    """Маркер нарушает схему вида."""


def h1(kind: str, *parts: str) -> str:
    """Идентификатор поля маркера: h1-<первые 16 hex sha256>."""
    raw = json.dumps([kind, *parts], ensure_ascii=False, separators=(",", ":"))
    return "h1-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Marker:
    """Разобранный маркер: вид и поля в каноническом порядке."""

    kind: str
    fields: tuple[tuple[str, str], ...]

    def get(self, key: str) -> str:
        """Значение поля."""
        return dict(self.fields)[key]


def make(kind: str, **fields: str) -> Marker:
    """Проверенный маркер; нарушение схемы — MarkerError."""
    if kind not in FIELDS:
        raise MarkerError(f"неизвестный вид {kind!r}")
    if tuple(sorted(fields)) != FIELDS[kind]:
        raise MarkerError(f"{kind}: поля {sorted(fields)} != {FIELDS[kind]}")
    for key, value in fields.items():
        pattern = N_RE if key in NUMERIC else H1_RE
        if not pattern.match(value):
            raise MarkerError(f"{kind}.{key}={value!r}")
    return Marker(kind, tuple(sorted(fields.items())))


def render(m: Marker) -> str:
    """Строка маркера."""
    pairs = "".join(f" {k}={v}" for k, v in m.fields)
    return f"<!-- {TAG} {m.kind}{pairs} -->"


def escape(text: str) -> str:
    """Цитата не может стать маркером: `<!--` и `conductor:` обезврежены."""
    return text.replace("<!--", "&lt;!--").replace("conductor:", "conductor&#58;")


def with_marker(text: str, m: Marker) -> str:
    """Тело записи: экранированный текст и маркер последней строкой."""
    return f"{escape(text).rstrip()}\n\n{render(m)}"


def parse_body(body: str) -> Marker | None:
    """Маркер тела или None (нет, не последней строкой, не один, не по схеме)."""
    if body.count(TAG) != 1:
        return None
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    match = LINE_RE.match(lines[-1]) if lines else None
    if match is None:
        return None
    pairs = [p.split("=", 1) for p in match.group(2).split()]
    keys = [k for k, _ in pairs]
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        return None
    try:
        return make(match.group(1), **dict(pairs))
    except MarkerError:
        return None


def classify(comment: dict[str, Any], bot_login: str) -> tuple[Kind, Marker | None]:
    """Событие только от бота и без правки (О §5.10); проекция — не событие."""
    m = parse_body(comment.get("body") or "")
    if m is None or m.kind not in EVENT_KINDS:
        return "none", None
    if comment.get("author") != bot_login:
        return "foreign", None
    if comment.get("updated_at") != comment.get("created_at"):
        return "edited", None
    return "event", m
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/conductor/test_markers.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add conductor/markers.py tests/conductor/test_markers.py
git commit -m "conductor: маркеры v1 с h1-кодированием (срез 1, §4.4)"
```

---

### Task A4: устойчивая запись

**Files:**
- Create: `conductor/durable.py`
- Test: `tests/conductor/test_durable.py`

**Interfaces:**
- Produces: `write_atomic(path: Path, text: str) -> None`; `append_line(path: Path, row: dict) -> None`; `JsonlRead(rows: list[dict], truncated_tail: bool)`; `read_jsonl(path: Path) -> JsonlRead` (битая строка не в конце — `ValueError`); `move_to_corrupt(directory: Path, names: list[str], stamp: str) -> Path`.

- [ ] **Step 1: Write the failing test**

```python
"""Устойчивая запись (спека среза 1, §4.6)."""

from pathlib import Path

import pytest

from conductor.durable import append_line, move_to_corrupt, read_jsonl, write_atomic


def test_write_atomic_replaces_and_leaves_no_tmp(tmp_path: Path) -> None:
    path = tmp_path / "f.json"
    write_atomic(path, "1")
    write_atomic(path, "2")
    assert path.read_text(encoding="utf-8") == "2"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.json"]


def test_append_and_read(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    append_line(path, {"a": 1})
    append_line(path, {"b": "ю"})
    got = read_jsonl(path)
    assert got.rows == [{"a": 1}, {"b": "ю"}] and not got.truncated_tail


def test_truncated_tail_is_reported_not_raised(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    append_line(path, {"a": 1})
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"b": ')
    got = read_jsonl(path)
    assert got.rows == [{"a": 1}] and got.truncated_tail


def test_bad_middle_line_raises(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    path.write_text('{"a": 1}\nnot json\n{"b": 2}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        read_jsonl(path)


def test_move_to_corrupt_keeps_bytes(tmp_path: Path) -> None:
    (tmp_path / "x.json").write_text("bad", encoding="utf-8")
    dest = move_to_corrupt(tmp_path, ["x.json", "absent.json"], "20261001T000000Z")
    assert (dest / "x.json").read_text(encoding="utf-8") == "bad"
    assert not (tmp_path / "x.json").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/conductor/test_durable.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Устойчивая запись: атомарная замена и jsonl с fsync (спека среза 1, §4.6)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_atomic(path: Path, text: str) -> None:
    """tmp в том же каталоге → fsync → replace → fsync каталога."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def append_line(path: Path, row: dict[str, Any]) -> None:
    """Дописать строку jsonl и дождаться fsync; сбой — OSError вызывающему."""
    line = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


@dataclass
class JsonlRead:
    """Строки jsonl; truncated_tail — последняя строка оборвана записью."""

    rows: list[dict[str, Any]]
    truncated_tail: bool


def read_jsonl(path: Path) -> JsonlRead:
    """Битая строка не в конце — ValueError (файл повреждён)."""
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    rows: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            if i == len(lines) - 1:  # нет завершающего \n — оборванная запись
                return JsonlRead(rows, True)
            raise
        if not isinstance(row, dict):
            raise ValueError(f"{path.name}: строка {i + 1} не объект")
        rows.append(row)
    return JsonlRead(rows, False)


def move_to_corrupt(directory: Path, names: list[str], stamp: str) -> Path:
    """Перенести файлы в corrupt/<stamp>/ (не удалять — разбор владельцем)."""
    dest = directory / "corrupt" / stamp
    dest.mkdir(parents=True, exist_ok=True)
    for name in names:
        if (directory / name).exists():
            os.replace(directory / name, dest / name)
    _fsync_dir(directory)
    return dest
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/conductor/test_durable.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add conductor/durable.py tests/conductor/test_durable.py
git commit -m "conductor: устойчивая запись с fsync (срез 1, §4.6)"
```

---
### Task A5: операционное состояние

**Files:**
- Create: `conductor/opstate.py`
- Test: `tests/conductor/test_opstate.py`

**Interfaces:**
- Consumes: `conductor.durable.{write_atomic, append_line, read_jsonl, move_to_corrupt}` (Task A4).
- Produces: `iso(dt: datetime) -> str`; `parse_ts(s: str) -> datetime`; `StateError`; `Episode`; `Opened(state: OpState | None, finding: str | None)`; `init_state(state_dir: Path, now: datetime) -> None`; `recover_state(state_dir: Path, now: datetime) -> None`; `open_state(state_dir: Path, now: datetime) -> Opened`; класс `OpState` с методами `quarantined(now) -> bool`, `begin_attempt(*, attempt_id, mutation_id, effect_key, target, marker_key, action, subject, now, op="", expected="") -> None` (`op`/`expected` — идентичность эффекта для поиска после обрыва, §5.3), `finish_attempt(attempt_id, outcome, now) -> None`, `delayed(effect_key, now) -> bool`, `open_attempts() -> list[dict]` (по одной незавершённой на эффект), `settle(effect_key, now) -> None`, `series_event(mutation_id, event, run_id, now, error="", detail=None) -> int`, `end_run(now) -> None`, `episodes() -> list[Episode]`. Константы `QUARANTINE = timedelta(minutes=65)`, `RETRY_DELAY = timedelta(minutes=60)`.

- [ ] **Step 1: Write the failing test**

```python
"""Операционное состояние (спека среза 1, §4.6, §5.3–5.4)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from conductor.opstate import (
    StateError,
    init_state,
    open_state,
    recover_state,
)

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
LATER = T0 + timedelta(hours=2)


def _state(tmp_path: Path):
    init_state(tmp_path, T0)
    opened = open_state(tmp_path, LATER)
    assert opened.finding is None and opened.state is not None
    return opened.state


def _begin(state, aid: str, eff: str, now: datetime) -> None:
    state.begin_attempt(
        attempt_id=aid,
        mutation_id="m-" + aid,
        effect_key=eff,
        target="own/r#1",
        marker_key="k",
        action="nudge",
        subject="todo://r/x",
        now=now,
    )


def test_init_writes_init_last_and_quarantines(tmp_path: Path) -> None:
    init_state(tmp_path, T0)
    assert {p.name for p in tmp_path.iterdir()} == {
        "INIT",
        "attempts.jsonl",
        "failures.json",
        "quarantine.json",
    }
    opened = open_state(tmp_path, T0 + timedelta(minutes=10))
    assert opened.state is not None and opened.state.quarantined(
        T0 + timedelta(minutes=64)
    )
    assert not opened.state.quarantined(T0 + timedelta(minutes=66))


def test_init_refuses_non_empty_dir(tmp_path: Path) -> None:
    (tmp_path / "x").write_text("1")
    with pytest.raises(StateError):
        init_state(tmp_path, T0)


def test_missing_init_is_uninitialized(tmp_path: Path) -> None:
    assert open_state(tmp_path, T0).finding == "OPSTATE-UNINITIALIZED"
    init_state(tmp_path, T0)
    (tmp_path / "INIT").unlink()
    opened = open_state(tmp_path, LATER)
    assert opened.state is None and opened.finding == "OPSTATE-UNINITIALIZED"


def test_recover_moves_files_and_quarantines(tmp_path: Path) -> None:
    init_state(tmp_path, T0)
    (tmp_path / "INIT").unlink()
    recover_state(tmp_path, LATER)
    assert (tmp_path / "corrupt").is_dir()
    opened = open_state(tmp_path, LATER + timedelta(minutes=1))
    assert opened.state is not None and opened.state.quarantined(
        LATER + timedelta(minutes=1)
    )
    assert opened.state.generation == 2


def test_recover_refuses_with_init_or_empty(tmp_path: Path) -> None:
    with pytest.raises(StateError):
        recover_state(tmp_path, T0)
    init_state(tmp_path, T0)
    with pytest.raises(StateError):
        recover_state(tmp_path, T0)


@pytest.mark.parametrize(
    "damage",
    [
        lambda d: (d / "failures.json").unlink(),
        lambda d: (d / "quarantine.json").write_text("{"),
        lambda d: (d / "attempts.jsonl").write_text('x\n{"t": "header"}\n'),
        lambda d: (d / "failures.json").write_text(
            json.dumps({"generation": 9, "series": {}})
        ),
    ],
)
def test_damage_quarantines_with_new_generation(tmp_path: Path, damage) -> None:
    init_state(tmp_path, T0)
    damage(tmp_path)
    opened = open_state(tmp_path, LATER)
    assert opened.finding == "OPSTATE-LOST" and opened.state is not None
    assert opened.state.quarantined(LATER + timedelta(minutes=64))
    assert any((tmp_path / "corrupt").iterdir())
    assert opened.state.generation >= 2


def test_truncated_attempt_tail_is_repaired(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _begin(state, "a1", "e1", LATER)
    with open(tmp_path / "attempts.jsonl", "a", encoding="utf-8") as fh:
        fh.write('{"t": "be')
    opened = open_state(tmp_path, LATER)
    assert opened.finding is None and opened.state is not None
    assert opened.state.delayed("e1", LATER + timedelta(minutes=1))
    _begin(opened.state, "a2", "e2", LATER)  # файл снова дописывается
    assert open_state(tmp_path, LATER).state is not None


def test_delay_by_effect_key(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _begin(state, "a1", "e1", LATER)
    assert state.delayed("e1", LATER + timedelta(minutes=59))
    assert not state.delayed("e2", LATER)
    assert not state.delayed("e1", LATER + timedelta(minutes=61))
    assert state.delayed("e1", LATER - timedelta(minutes=5))  # часы назад
    state.finish_attempt("a1", "uncertain", LATER)
    assert state.delayed("e1", LATER + timedelta(minutes=30))
    state.settle("e1", LATER)
    assert not state.delayed("e1", LATER + timedelta(minutes=30))


def test_in_flight_survives_reload(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _begin(state, "a1", "e1", LATER)
    reloaded = open_state(tmp_path, LATER).state
    assert reloaded is not None and reloaded.delayed("e1", LATER)


def test_finished_failed_or_ok_does_not_delay(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _begin(state, "a1", "e1", LATER)
    state.finish_attempt("a1", "failed", LATER)
    assert not state.delayed("e1", LATER)


def test_series_table(tmp_path: Path) -> None:
    state = _state(tmp_path)
    assert state.series_event("m", "failed", "r1", LATER, error="500") == 1
    assert state.series_event("m", "failed", "r1", LATER) == 1  # +1 за прогон
    state.end_run(LATER)
    assert state.series_event("m", "failed", "r2", LATER) == 2
    state.end_run(LATER)
    [episode] = state.episodes()
    assert episode.first_run == "r1" and episode.runs == ("r1", "r2")
    assert state.series_event("m", "uncertain", "r3", LATER) == 2
    state.end_run(LATER)
    assert state.series_event("m", "delay", "r4", LATER) == 2
    state.end_run(LATER)
    assert state.series_event("m", "rate_limited", "r5", LATER) == 2
    state.end_run(LATER)
    state.end_run(LATER)  # r6: шаг не отправлялся — серия рвётся
    assert state.episodes() == []
    assert state.series_event("m", "failed", "r7", LATER) == 1
    assert state.series_event("m", "ok", "r8", LATER) == 0


def test_series_persist_across_reload(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.series_event("m", "failed", "r1", LATER)
    state.end_run(LATER)
    reloaded = open_state(tmp_path, LATER).state
    assert reloaded is not None
    assert reloaded.series_event("m", "failed", "r2", LATER) == 2


def test_open_attempts_one_per_effect_with_identity(tmp_path: Path) -> None:
    """Поиск эффекта (§5.3) получает незавершённые попытки с op/expected."""
    state = _state(tmp_path)
    state.begin_attempt(
        attempt_id="a1",
        mutation_id="m1",
        effect_key="e1",
        target="own/r#1",
        marker_key="k",
        action="nudge",
        subject="s",
        now=LATER,
        op="comment",
        expected="<!-- conductor:v1 q id=h1-0 -->",
    )
    _begin(state, "a2", "e1", LATER)  # тот же эффект
    _begin(state, "a3", "e3", LATER)
    state.finish_attempt("a3", "failed", LATER)
    reloaded = open_state(tmp_path, LATER).state
    assert reloaded is not None
    [found] = reloaded.open_attempts()
    assert (found["op"], found["expected"]) == (
        "comment",
        "<!-- conductor:v1 q id=h1-0 -->",
    )
    reloaded.settle("e1", LATER)
    assert reloaded.open_attempts() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/conductor/test_opstate.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor.opstate'`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Операционное состояние писателя (спека среза 1, §3, §4.6, §5.3–5.4).

I7: состояние не даёт полномочий и не доказывает выполнения — только
защитные запреты и задержки и вопросы об операционных сбоях.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from conductor.durable import append_line, move_to_corrupt, read_jsonl, write_atomic

INIT = "INIT"
ATTEMPTS = "attempts.jsonl"
FAILURES = "failures.json"
QUARANTINE_FILE = "quarantine.json"
FILES = [ATTEMPTS, FAILURES, QUARANTINE_FILE]
QUARANTINE = timedelta(minutes=65)
RETRY_DELAY = timedelta(minutes=60)
OPEN = frozenset({"in_flight", "uncertain"})
STATE_ERRORS = (OSError, ValueError, KeyError, TypeError)


def iso(dt: datetime) -> str:
    """RFC 3339 UTC с суффиксом Z."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: str) -> datetime:
    """Разбор RFC 3339 (Z допустим)."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _stamp(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


class StateError(Exception):
    """Команда над состоянием отвергнута (init на непустом, recover при INIT)."""


@dataclass(frozen=True)
class Episode:
    """Эпизод сбоя (§5.4): серия ≥ 2; id — первый прогон серии."""

    mutation_id: str
    first_run: str
    runs: tuple[str, ...]
    error: str
    times: tuple[str, ...]
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Opened:
    """Результат открытия: state=None — записей нет; finding — находка."""

    state: OpState | None
    finding: str | None


def _fresh(directory: Path, generation: int, now: datetime, unknown: bool) -> None:
    """Шаги 2–3 восстановления: карантин, затем пустые файлы поколения."""
    write_atomic(
        directory / QUARANTINE_FILE,
        json.dumps(
            {
                "generation": generation,
                "started_at": iso(now),
                "unknown_previous": unknown,
            }
        ),
    )
    header = json.dumps({"t": "header", "generation": generation})
    write_atomic(directory / ATTEMPTS, header + "\n")
    write_atomic(
        directory / FAILURES, json.dumps({"generation": generation, "series": {}})
    )


def _write_init(directory: Path, now: datetime) -> None:
    write_atomic(directory / INIT, json.dumps({"created_at": iso(now)}))


def _previous_generation(directory: Path) -> int | None:
    for name in (QUARANTINE_FILE, FAILURES):
        try:
            value = json.loads((directory / name).read_text(encoding="utf-8"))
            generation = value["generation"]
        except STATE_ERRORS:
            continue
        if isinstance(generation, int):
            return generation
    return None


def init_state(state_dir: Path, now: datetime) -> None:
    """Первый запуск: пустое состояние поколения 1 и карантин; INIT последним."""
    state_dir.mkdir(parents=True, exist_ok=True)
    if any(state_dir.iterdir()):
        raise StateError(f"{state_dir} не пуст: нужен init-state --recover")
    _fresh(state_dir, 1, now, unknown=False)
    _write_init(state_dir, now)


def recover_state(state_dir: Path, now: datetime) -> None:
    """Непустой каталог без INIT: всё в corrupt/, новое поколение, карантин."""
    if (state_dir / INIT).exists():
        raise StateError("INIT есть: восстановление выполняет обычный run")
    if not state_dir.is_dir() or not any(state_dir.iterdir()):
        raise StateError(f"{state_dir} пуст: нужен init-state")
    previous = _previous_generation(state_dir)
    names = [p.name for p in state_dir.iterdir() if p.name != "corrupt"]
    move_to_corrupt(state_dir, names, _stamp(now))
    _fresh(state_dir, (previous or 0) + 1, now, unknown=previous is None)
    _write_init(state_dir, now)


def open_state(state_dir: Path, now: datetime) -> Opened:
    """Открыть состояние; порча — восстановление с карантином (§4.6)."""
    if not (state_dir / INIT).is_file():
        return Opened(None, "OPSTATE-UNINITIALIZED")
    try:
        return Opened(OpState.load(state_dir), None)
    except STATE_ERRORS:
        pass
    previous = _previous_generation(state_dir)
    try:
        move_to_corrupt(state_dir, FILES, _stamp(now))
        _fresh(state_dir, (previous or 0) + 1, now, unknown=previous is None)
        return Opened(OpState.load(state_dir), "OPSTATE-LOST")
    except STATE_ERRORS:
        return Opened(None, "OPSTATE-LOST")


def _dump_rows(rows: list[dict[str, Any]]) -> str:
    return "".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows
    )


class OpState:
    """Попытки, серии сбоев и карантин одного профиля."""

    def __init__(
        self,
        directory: Path,
        generation: int,
        quarantine_started: datetime,
        attempts: dict[str, dict[str, Any]],
        series: dict[str, dict[str, Any]],
    ) -> None:
        self.directory = directory
        self.generation = generation
        self.quarantine_started = quarantine_started
        self.attempts = attempts
        self.series = series
        self._touched: set[str] = set()

    @classmethod
    def load(cls, directory: Path) -> OpState:
        """Прочитать и сверить поколения; любая порча — исключение."""
        json.loads((directory / INIT).read_text(encoding="utf-8"))
        quarantine = json.loads(
            (directory / QUARANTINE_FILE).read_text(encoding="utf-8")
        )
        failures = json.loads((directory / FAILURES).read_text(encoding="utf-8"))
        read = read_jsonl(directory / ATTEMPTS)
        header = read.rows[0]
        generation = quarantine["generation"]
        if header.get("t") != "header" or not (
            header["generation"] == failures["generation"] == generation
        ):
            raise ValueError("поколения состояния не совпадают")
        if not isinstance(failures["series"], dict):
            raise ValueError("series не объект")
        if read.truncated_tail:  # оборванная строка: попытка не отправлялась
            write_atomic(directory / ATTEMPTS, _dump_rows(read.rows))
        attempts: dict[str, dict[str, Any]] = {}
        for row in read.rows[1:]:
            if row["t"] == "begin":
                attempts[row["attempt_id"]] = {**row, "status": "in_flight"}
            elif row["t"] == "end":
                attempts[row["attempt_id"]]["status"] = row["outcome"]
            else:
                raise ValueError(f"неизвестная строка {row['t']!r}")
        return cls(
            directory,
            generation,
            parse_ts(quarantine["started_at"]),
            attempts,
            failures["series"],
        )

    def quarantined(self, now: datetime) -> bool:
        """Карантин 65 мин от начала; часы назад его не сокращают."""
        return now < self.quarantine_started + QUARANTINE

    def begin_attempt(
        self,
        *,
        attempt_id: str,
        mutation_id: str,
        effect_key: str,
        target: str,
        marker_key: str,
        action: str,
        subject: str,
        now: datetime,
        op: str = "",
        expected: str = "",
    ) -> None:
        """Устойчивая запись попытки ДО отправки; сбой — OSError (мутации нет).

        op и expected — идентичность эффекта для поиска после обрыва (§5.3).
        """
        row = {
            "t": "begin",
            "attempt_id": attempt_id,
            "mutation_id": mutation_id,
            "effect_key": effect_key,
            "target": target,
            "marker_key": marker_key,
            "action": action,
            "subject": subject,
            "op": op,
            "expected": expected,
            "started_at": iso(now),
        }
        append_line(self.directory / ATTEMPTS, row)
        self.attempts[attempt_id] = {**row, "status": "in_flight"}

    def finish_attempt(self, attempt_id: str, outcome: str, now: datetime) -> None:
        """Исход попытки после проверки."""
        append_line(
            self.directory / ATTEMPTS,
            {"t": "end", "attempt_id": attempt_id, "outcome": outcome, "at": iso(now)},
        )
        self.attempts[attempt_id]["status"] = outcome

    def delayed(self, effect_key: str, now: datetime) -> bool:
        """Есть in_flight/uncertain попытка этого эффекта моложе 60 мин (§5.3)."""
        return any(
            a["effect_key"] == effect_key
            and a["status"] in OPEN
            and now < parse_ts(a["started_at"]) + RETRY_DELAY
            for a in self.attempts.values()
        )

    def open_attempts(self) -> list[dict[str, Any]]:
        """Незавершённые попытки (in_flight/uncertain), по одной на эффект."""
        seen: dict[str, dict[str, Any]] = {}
        for a in self.attempts.values():
            if a["status"] in OPEN:
                seen.setdefault(a["effect_key"], a)
        return list(seen.values())

    def settle(self, effect_key: str, now: datetime) -> None:
        """Результат найден по идентичности: открытые попытки эффекта — ok."""
        for attempt_id, a in list(self.attempts.items()):
            if a["effect_key"] == effect_key and a["status"] in OPEN:
                self.finish_attempt(attempt_id, "ok", now)

    def series_event(
        self,
        mutation_id: str,
        event: str,
        run_id: str,
        now: datetime,
        error: str = "",
        detail: dict[str, Any] | None = None,
    ) -> int:
        """Переход серии по таблице §5.4; возвращает длину серии."""
        self._touched.add(mutation_id)
        current = self.series.get(mutation_id)
        if event == "ok":
            if self.series.pop(mutation_id, None) is not None:
                self._persist()
            return 0
        if event != "failed":  # uncertain / rate_limited / delay — без изменений
            return current["count"] if current else 0
        entry = current or {"count": 0, "runs": [], "times": [], "error": ""}
        if run_id not in entry["runs"]:
            entry["count"] += 1
            entry["runs"].append(run_id)
            entry["times"].append(iso(now))
        entry["error"], entry["detail"] = error, detail or {}
        self.series[mutation_id] = entry
        self._persist()
        return entry["count"]

    def end_run(self, now: datetime) -> None:
        """Ключи, которых прогон не касался, рвут серию (§5.4)."""
        for mutation_id in list(self.series):
            if mutation_id not in self._touched:
                del self.series[mutation_id]
        self._touched = set()
        self._persist()

    def episodes(self) -> list[Episode]:
        """Открытые эпизоды сбоев (серия ≥ 2)."""
        return [
            Episode(
                mid,
                s["runs"][0],
                tuple(s["runs"]),
                s.get("error", ""),
                tuple(s["times"]),
                s.get("detail") or {},
            )
            for mid, s in sorted(self.series.items())
            if s["count"] >= 2
        ]

    def _persist(self) -> None:
        write_atomic(
            self.directory / FAILURES,
            json.dumps(
                {"generation": self.generation, "series": self.series},
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/conductor/test_opstate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add conductor/opstate.py tests/conductor/test_opstate.py
git commit -m "conductor: операционное состояние — попытки, серии, карантин (срез 1, §4.6)"
```

---

### Task A6: журнал вызовов установки и запрет по лимиту

**Files:**
- Create: `conductor/app_calls.py`
- Test: `tests/conductor/test_app_calls.py`

**Interfaces:**
- Consumes: `durable.*` (A4), `opstate.iso`, `opstate.parse_ts` (A5).
- Produces: `RateInfo(retry_after_s: int | None = None, reset: int | None = None, remaining: int | None = None)`; `CLASSES = ("create", "update", "service")`; `journal_path(shared: Path, app_id: int, installation_id: int) -> Path`; `init_host(shared: Path, app_id: int, installation_id: int, now: datetime) -> None` (пустой журнал — только при первой регистрации хоста; журнал, пропавший при существующем `HOST_INIT`, начинается событием `lost` — ревью P2-5); `derive(rows: list[dict]) -> datetime | None`; класс `AppCalls` с `AppCalls.open(shared, app_id, installation_id, now) -> tuple[AppCalls | None, str | None]`, `.blocked_until() -> datetime | None`, `.begin(klass: str, now: datetime) -> int`, `.end(seq: int, now: datetime, outcome: str, status: int | None, rate: RateInfo, klass: str) -> None`.

- [ ] **Step 1: Write the failing test**

```python
"""Журнал вызовов установки и запрет по лимиту (спека среза 1, §5.5)."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from conductor.app_calls import AppCalls, RateInfo, init_host, journal_path

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
H = timedelta(hours=1)


def _calls(tmp_path: Path) -> AppCalls:
    init_host(tmp_path, 1, 2, T0)
    calls, finding = AppCalls.open(tmp_path, 1, 2, T0)
    assert calls is not None and finding is None
    return calls


def _call(calls: AppCalls, klass: str, at: datetime, outcome: str, rate=RateInfo()):
    seq = calls.begin(klass, at)
    calls.end(seq, at, outcome, 200 if outcome == "ok" else 403, rate, klass)


def test_empty_journal_has_no_block(tmp_path: Path) -> None:
    assert _calls(tmp_path).blocked_until() is None


def test_retry_after_is_kept_fully_beyond_65_min(tmp_path: Path) -> None:
    calls = _calls(tmp_path)
    _call(calls, "create", T0, "rate_limited", RateInfo(retry_after_s=7200))
    assert calls.blocked_until() == T0 + 2 * H


def test_primary_reset_and_ok_with_zero_remaining(tmp_path: Path) -> None:
    calls = _calls(tmp_path)
    reset = int((T0 + 30 * timedelta(minutes=1)).timestamp())
    _call(calls, "update", T0, "ok", RateInfo(remaining=0, reset=reset))
    assert calls.blocked_until() == datetime.fromtimestamp(reset, UTC)


def test_unknown_term_escalates_per_class_to_8h(tmp_path: Path) -> None:
    calls = _calls(tmp_path)
    expected = [1, 2, 4, 8, 8]
    for i, hours in enumerate(expected):
        at = T0 + timedelta(days=i)
        _call(calls, "create", at, "rate_limited")
        assert calls.blocked_until() == at + hours * H


def test_service_success_does_not_reset_create(tmp_path: Path) -> None:
    calls = _calls(tmp_path)
    _call(calls, "create", T0, "rate_limited")
    _call(calls, "service", T0 + 2 * H, "ok")
    _call(calls, "create", T0 + 3 * H, "rate_limited")
    assert calls.blocked_until() == T0 + 3 * H + 2 * H


def test_same_class_success_resets(tmp_path: Path) -> None:
    calls = _calls(tmp_path)
    _call(calls, "create", T0, "rate_limited")
    _call(calls, "create", T0 + 2 * H, "ok")
    _call(calls, "create", T0 + 3 * H, "rate_limited")
    assert calls.blocked_until() == T0 + 4 * H


def test_begin_without_end_blocks_one_hour(tmp_path: Path) -> None:
    calls = _calls(tmp_path)
    calls.begin("create", T0)
    reopened, _ = AppCalls.open(tmp_path, 1, 2, T0)
    assert reopened is not None and reopened.blocked_until() == T0 + H


def test_truncated_tail_becomes_orphan_and_file_stays_appendable(
    tmp_path: Path,
) -> None:
    calls = _calls(tmp_path)
    _call(calls, "service", T0, "ok")
    path = journal_path(tmp_path, 1, 2)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"t": "be')
    stamp = (T0 + H).timestamp()
    os.utime(path, (stamp, stamp))  # оборванная строка «случилась» в T0 + 1 ч
    reopened, finding = AppCalls.open(tmp_path, 1, 2, T0)
    assert reopened is not None and finding is None
    assert reopened.blocked_until() == T0 + 2 * H
    _call(reopened, "service", T0 + 3 * H, "ok")
    assert AppCalls.open(tmp_path, 1, 2, T0)[0] is not None


def test_lost_journal_blocks_with_finding(tmp_path: Path) -> None:
    _calls(tmp_path)
    journal_path(tmp_path, 1, 2).unlink()
    calls, finding = AppCalls.open(tmp_path, 1, 2, T0)
    assert finding == "RATE-STATE-LOST" and calls is not None
    assert calls.blocked_until() == T0 + H


def test_corrupt_middle_line_is_moved_and_blocks(tmp_path: Path) -> None:
    _calls(tmp_path)
    journal_path(tmp_path, 1, 2).write_text('x\n{"t": "lost"}\n', encoding="utf-8")
    calls, finding = AppCalls.open(tmp_path, 1, 2, T0)
    assert finding == "RATE-STATE-LOST" and calls is not None
    assert (tmp_path / "corrupt").is_dir()


def test_without_host_init_no_calls(tmp_path: Path) -> None:
    calls, finding = AppCalls.open(tmp_path, 1, 2, T0)
    assert calls is None and finding == "OPSTATE-UNINITIALIZED"


def test_block_is_shared_by_profiles(tmp_path: Path) -> None:
    fleet = _calls(tmp_path)
    _call(fleet, "create", T0, "rate_limited", RateInfo(retry_after_s=600))
    acceptance, _ = AppCalls.open(tmp_path, 1, 2, T0)
    assert acceptance is not None
    assert acceptance.blocked_until() == T0 + timedelta(seconds=600)


def test_init_host_after_lost_journal_keeps_ban(tmp_path: Path) -> None:
    """Регрессия P2-5: HOST_INIT есть, журнала нет → событие lost, не чистый лист."""
    init_host(tmp_path, 1, 2, T0)
    journal_path(tmp_path, 1, 2).unlink()
    init_host(tmp_path, 1, 2, T0 + H)  # init-state другого профиля
    calls, finding = AppCalls.open(tmp_path, 1, 2, T0 + H)
    assert calls is not None and finding is None
    assert calls.blocked_until() == T0 + 2 * H
    assert [r["t"] for r in calls.rows] == ["lost"]


def test_first_init_host_is_clean(tmp_path: Path) -> None:
    init_host(tmp_path, 1, 2, T0)
    calls, _ = AppCalls.open(tmp_path, 1, 2, T0)
    assert calls is not None and calls.rows == [] and calls.blocked_until() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/conductor/test_app_calls.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Журнал вызовов установки App и запрет по лимиту (спека среза 1, §5.5).

Запрет не хранится отдельным значением — выводится из журнала: каждый вызов
токеном App/JWT — строки `begin` (до отправки) и `end` (после). Неизвестный
срок — 1 ч · 2^k, k по классу операций, сброс только успехом своего класса.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from conductor.durable import append_line, move_to_corrupt, read_jsonl, write_atomic
from conductor.opstate import iso, parse_ts

CLASSES = ("create", "update", "service")
ANY_CLASS = "*"
HOUR = timedelta(hours=1)
MAX_K = 3
HOST_INIT = "HOST_INIT"


@dataclass(frozen=True)
class RateInfo:
    """Числовые параметры лимита из заголовков ответа."""

    retry_after_s: int | None = None
    reset: int | None = None
    remaining: int | None = None


def journal_path(shared: Path, app_id: int, installation_id: int) -> Path:
    """Путь журнала установки — общий для всех профилей хоста."""
    return shared / f"app-calls-{app_id}-{installation_id}.jsonl"


def _dump(rows: list[dict[str, Any]]) -> str:
    return "".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows
    )


def init_host(shared: Path, app_id: int, installation_id: int, now: datetime) -> None:
    """init-state: журнал установки и HOST_INIT; существующее не перезаписывает.

    Пустой журнал создаётся только при ПЕРВОЙ регистрации хоста (нет
    HOST_INIT). Журнал, пропавший после неё, — потеря, а не новая установка:
    новый журнал начинается событием `lost` (запрет неизвестного срока, §5.5),
    иначе init-state нового профиля стёр бы действующий запрет флота.
    """
    shared.mkdir(parents=True, exist_ok=True)
    path = journal_path(shared, app_id, installation_id)
    registered = (shared / HOST_INIT).exists()
    if not path.exists():
        lost = [{"t": "lost", "at": iso(now)}] if registered else []
        write_atomic(path, _dump(lost))
    if not registered:
        write_atomic(shared / HOST_INIT, json.dumps({"created_at": iso(now)}))


def _known_term(now: datetime, outcome: str, rate: RateInfo) -> datetime | None:
    if outcome == "rate_limited" and rate.retry_after_s is not None:
        return now + timedelta(seconds=rate.retry_after_s)
    if outcome in ("rate_limited", "ok") and rate.remaining == 0 and rate.reset:
        return datetime.fromtimestamp(rate.reset, UTC)
    return None


def derive(rows: list[dict[str, Any]]) -> datetime | None:
    """Действующий запрет: максимум известных сроков и сроков неизвестных."""
    k = dict.fromkeys(CLASSES, 0)
    until: datetime | None = None
    opened: dict[int, dict[str, Any]] = {}

    def push(term: datetime) -> None:
        nonlocal until
        until = term if until is None else max(until, term)

    def unknown(at: str, klass: str) -> None:
        classes = CLASSES if klass not in k else (klass,)
        level = max(k[c] for c in classes)
        for c in classes:
            k[c] = min(k[c] + 1, MAX_K)
        push(parse_ts(at) + HOUR * 2 ** min(level, MAX_K))

    for row in rows:
        kind = row["t"]
        if kind == "begin":
            opened[row["seq"]] = row
        elif kind == "end":
            begin = opened.pop(row["seq"], None) or {}
            klass = row.get("class") or begin.get("class", ANY_CLASS)
            if row["outcome"] == "ok" and klass in k:
                k[klass] = 0
            if row.get("blocked_until"):
                push(parse_ts(row["blocked_until"]))
            if row.get("unknown_term"):
                unknown(row["at"], klass)
        elif kind in ("lost", "orphan"):
            unknown(row["at"], ANY_CLASS)
    for begin in opened.values():
        unknown(begin["at"], begin["class"])
    return until


class AppCalls:
    """Журнал вызовов одной установки App на хосте."""

    def __init__(self, path: Path, rows: list[dict[str, Any]]) -> None:
        self.path = path
        self.rows = rows
        self._seq = max((r.get("seq", 0) for r in rows), default=0)

    @classmethod
    def open(
        cls, shared: Path, app_id: int, installation_id: int, now: datetime
    ) -> tuple[AppCalls | None, str | None]:
        """Открыть журнал; утрата/порча — новый журнал с событием `lost`."""
        path = journal_path(shared, app_id, installation_id)
        if not (shared / HOST_INIT).is_file():
            return None, "OPSTATE-UNINITIALIZED"
        try:
            if not path.is_file():
                return cls._restart(path, now)
            read = read_jsonl(path)
        except (OSError, ValueError):
            try:
                move_to_corrupt(shared, [path.name], now.strftime("%Y%m%dT%H%M%SZ"))
                return cls._restart(path, now)
            except OSError:
                return None, "RATE-STATE-LOST"
        rows = read.rows
        if read.truncated_tail:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            rows = [*rows, {"t": "orphan", "at": iso(mtime)}]
            write_atomic(path, _dump(rows))
        return cls(path, rows), None

    @classmethod
    def _restart(cls, path: Path, now: datetime) -> tuple[AppCalls | None, str]:
        rows = [{"t": "lost", "at": iso(now)}]
        try:
            write_atomic(path, _dump(rows))
        except OSError:
            return None, "RATE-STATE-LOST"
        return cls(path, rows), "RATE-STATE-LOST"

    def blocked_until(self) -> datetime | None:
        """Срок действующего запрета или None."""
        return derive(self.rows)

    def begin(self, klass: str, now: datetime) -> int:
        """Строка begin до отправки; сбой — OSError (вызова нет)."""
        self._seq += 1
        row = {"t": "begin", "seq": self._seq, "class": klass, "at": iso(now)}
        append_line(self.path, row)
        self.rows.append(row)
        return self._seq

    def end(
        self,
        seq: int,
        now: datetime,
        outcome: str,
        status: int | None,
        rate: RateInfo,
        klass: str,
    ) -> None:
        """Строка end: исход и числовые параметры лимита (без заголовков)."""
        term = _known_term(now, outcome, rate)
        row = {
            "t": "end",
            "seq": seq,
            "class": klass,
            "at": iso(now),
            "outcome": outcome,
            "status": status,
            "retry_after_s": rate.retry_after_s,
            "ratelimit_reset": rate.reset,
            "ratelimit_remaining": rate.remaining,
            "blocked_until": iso(term) if term else None,
            "unknown_term": outcome == "rate_limited" and term is None,
        }
        append_line(self.path, row)
        self.rows.append(row)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/conductor/test_app_calls.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add conductor/app_calls.py tests/conductor/test_app_calls.py
git commit -m "conductor: журнал вызовов установки, запрет по лимиту 1/2/4/8 ч (срез 1, §5.5)"
```

---

### Task A7: HTTP-адаптер без перенаправлений

**Files:**
- Create: `conductor/http.py`
- Test: `tests/conductor/test_http.py`

**Interfaces:**
- Consumes: `app_calls.RateInfo` (A6).
- Produces: `Response(status: int, headers: dict[str, str], body: bytes)` с `.json() -> Any`; `TransportError`; `Transport = Callable[[str, str, dict[str, str], bytes | None], Response]`; `urllib_transport(method, url, headers, body) -> Response`; `Outcome = Literal["ok", "failed", "rate_limited", "uncertain", "moved"]`; `classify(resp: Response, graphql: bool) -> Outcome`; `rate_info(headers: dict[str, str]) -> RateInfo`.

- [ ] **Step 1: Write the failing test**

```python
"""HTTP-адаптер App: без перенаправлений, классификация исходов (§4.5)."""

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conductor.http import (
    Response,
    TransportError,
    classify,
    rate_info,
    urllib_transport,
)


class _Handler(BaseHTTPRequestHandler):
    hits: list[tuple[str, str]] = []

    def _respond(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        type(self).hits.append((self.command, self.path))
        if self.path.startswith("/redirect/"):
            self.send_response(int(self.path.rsplit("/", 1)[1]))
            self.send_header("Location", "/other")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body, code = (b'{"a": 1}', 200) if self.path == "/ok" else (b"{}", 404)
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PATCH = _respond

    def log_message(self, *args: object) -> None:
        return None


@pytest.fixture
def server() -> Iterator[str]:
    _Handler.hits = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.mark.parametrize("code", [301, 302, 307, 308])
@pytest.mark.parametrize("method", ["GET", "POST", "PATCH"])
def test_redirect_is_never_followed(server: str, code: int, method: str) -> None:
    body = b"{}" if method != "GET" else None
    resp = urllib_transport(method, f"{server}/redirect/{code}", {}, body)
    assert resp.status == code
    assert classify(resp, graphql=False) == "moved"
    assert _Handler.hits == [(method, f"/redirect/{code}")]  # /other не запрошен


def test_ok_and_404(server: str) -> None:
    assert urllib_transport("GET", f"{server}/ok", {}, None).json() == {"a": 1}
    assert urllib_transport("GET", f"{server}/missing", {}, None).status == 404


def test_no_server_is_transport_error() -> None:
    with pytest.raises(TransportError):
        urllib_transport("GET", "http://127.0.0.1:9/x", {}, None)


def _r(status: int, body: bytes = b"{}", **headers: str) -> Response:
    return Response(status, {k.replace("_", "-"): v for k, v in headers.items()}, body)


@pytest.mark.parametrize(
    ("resp", "graphql", "outcome"),
    [
        (_r(200, b'{"id": 1}'), False, "ok"),
        (_r(201, b'{"id": 1}'), False, "ok"),
        (_r(200, b"{"), False, "uncertain"),
        (_r(200, b""), False, "uncertain"),
        (_r(404), False, "failed"),
        (_r(422), False, "failed"),
        (_r(403), False, "failed"),
        (_r(403, x_ratelimit_remaining="0"), False, "rate_limited"),
        (_r(429, retry_after="60"), False, "rate_limited"),
        (
            _r(403, b'{"message": "You have exceeded a secondary rate limit"}'),
            False,
            "rate_limited",
        ),
        (_r(500), False, "uncertain"),
        (_r(502), False, "uncertain"),
        (_r(408), False, "uncertain"),
        (_r(301), False, "moved"),
        (_r(200, b'{"data": {"x": {"id": 1}}}'), True, "ok"),
        (
            _r(200, b'{"data": null, "errors": [{"type": "RATE_LIMITED"}]}'),
            True,
            "rate_limited",
        ),
        (_r(200, b'{"data": null, "errors": [{"type": "NOT_FOUND"}]}'), True, "failed"),
        (
            _r(200, b'{"data": null, "errors": [{"type": "INTERNAL"}]}'),
            True,
            "uncertain",
        ),
        (_r(200, b'{"data": null}'), True, "uncertain"),
    ],
)
def test_classify(resp: Response, graphql: bool, outcome: str) -> None:
    assert classify(resp, graphql) == outcome


def test_rate_info() -> None:
    info = rate_info(
        {
            "retry-after": "30",
            "x-ratelimit-reset": "1700000000",
            "x-ratelimit-remaining": "0",
        }
    )
    assert (info.retry_after_s, info.reset, info.remaining) == (30, 1700000000, 0)
    assert rate_info({"retry-after": "x"}).retry_after_s is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/conductor/test_http.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor.http'`.

- [ ] **Step 3: Write minimal implementation**

```python
"""HTTP-адаптер вызовов App/JWT (спека среза 1, §4.5; решение владельца 14).

Перенаправления не выполняются: любой 3xx возвращается как ответ (исход
`moved`), повторного запроса по Location нет. `gh api` этого запретить не
даёт, поэтому для вызовов App не используется.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from conductor.app_calls import RateInfo

TIMEOUT = 30
FAILED_STATUS = frozenset({400, 401, 403, 404, 410, 422})
GQL_FAILED = frozenset({"NOT_FOUND", "FORBIDDEN", "UNPROCESSABLE"})
Outcome = Literal["ok", "failed", "rate_limited", "uncertain", "moved"]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(  # type: ignore[override]
        self, req, fp, code, msg, headers, newurl
    ):
        return None  # urllib поднимет HTTPError с кодом 3xx


_OPENER = urllib.request.build_opener(_NoRedirect)


@dataclass(frozen=True)
class Response:
    """Ответ сервера: статус, заголовки (нижний регистр), байты тела."""

    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        """Тело как JSON; пустое или битое — None."""
        try:
            return json.loads(self.body) if self.body else None
        except ValueError:
            return None


class TransportError(Exception):
    """Ответа нет (таймаут, обрыв, отказ соединения): исход неизвестен."""


Transport = Callable[[str, str, dict[str, str], bytes | None], Response]


def _lower(headers: Any) -> dict[str, str]:
    return {k.lower(): v for k, v in (headers or {}).items()}


def urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> Response:
    """Один запрос без следования перенаправлениям."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as resp:
            return Response(resp.status, _lower(resp.headers), resp.read())
    except urllib.error.HTTPError as exc:
        data = exc.read() if exc.fp is not None else b""
        return Response(exc.code, _lower(exc.headers), data)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise TransportError(type(exc).__name__) from exc


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def rate_info(headers: dict[str, str]) -> RateInfo:
    """Числовые параметры лимита (без остальных заголовков)."""
    return RateInfo(
        retry_after_s=_int(headers.get("retry-after")),
        reset=_int(headers.get("x-ratelimit-reset")),
        remaining=_int(headers.get("x-ratelimit-remaining")),
    )


def _rate_limited(resp: Response) -> bool:
    if resp.status not in (403, 429):
        return False
    info = rate_info(resp.headers)
    text = resp.body.decode("utf-8", "replace").lower()
    return (
        info.remaining == 0
        or info.retry_after_s is not None
        or "secondary rate limit" in text
    )


def classify(resp: Response, graphql: bool) -> Outcome:
    """Исход вызова по §4.5 (приоритет: moved → лимит → failed → прочее)."""
    if 300 <= resp.status < 400:
        return "moved"
    if _rate_limited(resp):
        return "rate_limited"
    if resp.status in FAILED_STATUS:
        return "failed"
    if not 200 <= resp.status < 300:
        return "uncertain"
    data = resp.json()
    if data is None:
        return "uncertain"
    if graphql:
        if not isinstance(data, dict):
            return "uncertain"
        errors = [e for e in data.get("errors") or [] if isinstance(e, dict)]
        types = {e.get("type") for e in errors}
        if "RATE_LIMITED" in types:
            return "rate_limited"
        if errors:
            return "failed" if types and types <= GQL_FAILED else "uncertain"
        if not data.get("data"):
            return "uncertain"
    return "ok"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/conductor/test_http.py -q`
Expected: PASS (на 3xx в журнале сервера ровно один запрос).

- [ ] **Step 5: Commit**

```bash
git add conductor/http.py tests/conductor/test_http.py
git commit -m "conductor: HTTP-адаптер App без перенаправлений (срез 1, §4.5)"
```

---

### Task A8: клиент App

**Files:**
- Create: `conductor/gh_app.py`
- Modify: `pyproject.toml` (группа `conductor`, `default-groups`), `uv.lock`
- Test: `tests/conductor/test_gh_app.py`

**Interfaces:**
- Consumes: `HostConfig` (A2), `AppCalls`, `RateInfo` (A6), `Response`, `Transport`, `TransportError`, `classify`, `rate_info`, `urllib_transport`, `Outcome` (A7), `iso`, `parse_ts` (A5).
- Produces: `Blocked` (вызов не отправлен), `JournalLost` (вызов мог уйти, `end` не записан), `CallResult(outcome: Outcome, response: Response | None)`, класс `AppClient(cfg, calls, transport=urllib_transport, clock=..., read_key=...)` с `.jwt() -> str`, `.token() -> str | None`, `.call(klass, method, path, *, auth: Literal["jwt", "token"], body: dict | None = None, graphql: bool = False) -> CallResult`, `.check_key() -> str | None` (логин бота `<slug>[bot]`, также в `.bot_login`), `.check_installation(owner: str) -> bool`, `.covers(repo: str) -> bool | None`.

- [ ] **Step 1: Add the dependency group**

Применить к `pyproject.toml` (группа `conductor` в `default-groups`), затем `uv lock`:

```diff
diff --git a/pyproject.toml b/pyproject.toml
index 0d5add7..975c343 100644
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -14,6 +14,7 @@ dependencies = [
 
 [tool.uv]
 package = false
+default-groups = ["dev", "conductor"]
 
 [tool.uv.sources]
 # Pinned to an IMMUTABLE dispatcher commit, fetched by git — never a
@@ -39,6 +40,9 @@ dev = [
 # включения автономной ветки (§6, §12 п.7), правка одного из двух мест либо
 # уронит резолв, либо тихо выберет одну сторону (финальное ревью M-1).
 governance = ["steward", "textual>=1"]
+# conductor срез 1: JWT App (RS256). Входит в default-groups — CI (`uv sync`)
+# и служба (`uv run --frozen`) ставят её без правок .github/ и unit-файла.
+conductor = ["pyjwt[crypto]>=2.8"]
 selfcheck = [
     "actionlint-py==1.7.12.25",
     "deptry==0.25.1",
```

Проверено по фактическим командам CI (решение владельца 6, 2026-10-01): `.github/workflows/*.yml`
вызывают только `uv sync --frozen [--group governance|selfcheck]` и `uv run --frozen [--group …]` —
ни `--no-default-groups`, ни `--only-group`, ни `UV_NO_DEFAULT_GROUPS`; `--group X` добавляет группу
к группам по умолчанию, поэтому `conductor` (PyJWT) ставится во всех джобах без правок `.github/`.
Служба (`uv run --frozen`) — так же. Если такая команда появится — правка CI, а не откат группы.

- [ ] **Step 2: Write the failing test**

```python
"""Клиент App (спека среза 1, §4.3, §5.1 шаги 5 и 7, §5.5)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from conductor.app_calls import AppCalls, RateInfo, init_host
from conductor.gh_app import AppClient, Blocked
from conductor.host_config import HostConfig
from conductor.http import Response, TransportError

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
CFG = HostConfig(
    app_id=11,
    installation_id=22,
    private_key=Path("/k.pem"),
    profile="fleet",
    shadow=False,
    state_dir=Path("/s"),
)


class FakeTransport:
    """Скрипт ответов по (метод, путь); записывает запросы."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.routes: dict[tuple[str, str], Response] = {
            ("GET", "/app"): Response(
                200, {}, json.dumps({"id": 11, "slug": "conductor"}).encode()
            ),
            ("POST", "/app/installations/22/access_tokens"): Response(
                201,
                {},
                json.dumps(
                    {"token": "ghs_" + "a" * 36, "expires_at": "2026-10-01T13:00:00Z"}
                ).encode(),
            ),
            ("GET", "/app/installations/22"): Response(
                200, {}, json.dumps({"account": {"login": "own"}}).encode()
            ),
            ("GET", "/repos/own/a/installation"): Response(200, {}, b'{"id": 22}'),
            ("GET", "/repos/own/b/installation"): Response(404, {}, b"{}"),
            ("GET", "/repos/own/c/installation"): Response(200, {}, b'{"id": 99}'),
            ("GET", "/repos/own/d/installation"): Response(500, {}, b"{}"),
        }
        self.fail: set[str] = set()

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> Response:
        path = url.removeprefix("https://api.github.com")
        self.requests.append((method, path, headers))
        if path in self.fail:
            raise TransportError("timeout")
        return self.routes[(method, path)]


@pytest.fixture
def env(tmp_path: Path):
    init_host(tmp_path, 11, 22, T0)
    calls, _ = AppCalls.open(tmp_path, 11, 22, T0)
    assert calls is not None
    transport, clock = FakeTransport(), [T0]
    client = AppClient(
        CFG, calls, transport, clock=lambda: clock[0], read_key=lambda _: PEM
    )
    return client, transport, calls, clock, tmp_path


def test_jwt_claims(env) -> None:
    client = env[0]
    claims = pyjwt.decode(
        client.jwt(),
        KEY.public_key(),
        algorithms=["RS256"],
        options={"verify_exp": False, "verify_iat": False, "verify_nbf": False},
    )
    assert claims["iss"] == "11" and claims["exp"] - claims["iat"] == 600


def test_check_key_returns_bot_login(env) -> None:
    client, transport = env[0], env[1]
    assert client.check_key() == "conductor[bot]" == client.bot_login
    transport.routes[("GET", "/app")] = Response(200, {}, b'{"id": 99, "slug": "x"}')
    assert client.check_key() is None


def test_token_cached_and_refreshed(env) -> None:
    client, transport, _, clock, _ = env
    first = client.token()
    assert first and client.token() == first
    assert sum(p.endswith("access_tokens") for _, p, _ in transport.requests) == 1
    clock[0] = T0 + timedelta(minutes=51)  # до истечения < 10 мин
    client.token()
    assert sum(p.endswith("access_tokens") for _, p, _ in transport.requests) == 2


def test_installation_and_coverage(env) -> None:
    client = env[0]
    assert client.check_installation("own") is True
    assert client.check_installation("other") is False
    assert client.covers("own/a") is True
    assert client.covers("own/b") is False
    assert client.covers("own/c") is False
    assert client.covers("own/d") is None


def test_block_stops_call_before_transport(env) -> None:
    client, transport, calls, _, _ = env
    seq = calls.begin("create", T0)
    calls.end(seq, T0, "rate_limited", 429, RateInfo(retry_after_s=600), "create")
    with pytest.raises(Blocked):
        client.check_key()
    assert transport.requests == []


def test_begin_failure_stops_call(env, monkeypatch) -> None:
    client, transport, calls, _, _ = env

    def boom(*_: object) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(calls, "begin", boom)
    with pytest.raises(Blocked):
        client.check_key()
    assert transport.requests == []


def test_transport_error_is_uncertain_and_journaled(env) -> None:
    client, transport, calls, _, _ = env
    transport.fail.add("/app")
    assert client.call("service", "GET", "/app", auth="jwt").outcome == "uncertain"
    assert calls.rows[-1]["outcome"] == "uncertain"


def test_token_never_lands_in_journal(env) -> None:
    client, _, calls, _, _ = env
    token = client.token()
    assert token
    text = calls.path.read_text(encoding="utf-8")
    assert token not in text and "eyJ" not in text
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/conductor/test_gh_app.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor.gh_app'`.

- [ ] **Step 4: Write minimal implementation**

```python
"""Клиент GitHub App (спека среза 1, §4.3; О §8.2).

Единственный путь вызовов токеном App или JWT: перед каждым — запрет по
лимиту (§5.5), строка begin журнала установки, затем HTTP-адаптер без
перенаправлений (§4.5) и строка end. Секреты живут только в памяти.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import jwt as pyjwt

from conductor.app_calls import AppCalls, RateInfo
from conductor.host_config import HostConfig
from conductor.http import (
    Outcome,
    Response,
    Transport,
    TransportError,
    classify,
    rate_info,
    urllib_transport,
)
from conductor.opstate import iso, parse_ts

API = "https://api.github.com"
TOKEN_MARGIN = timedelta(minutes=10)
Auth = Literal["jwt", "token"]


class Blocked(Exception):
    """Вызов не отправлен: запрет по лимиту, нет токена или журнала."""


class JournalLost(Exception):
    """Вызов мог уйти, но строка end не записана: исход неизвестен."""


@dataclass(frozen=True)
class CallResult:
    """Исход вызова и ответ (None — ответа нет)."""

    outcome: Outcome
    response: Response | None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _read_key(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class AppClient:
    """Вызовы GitHub API от имени App."""

    def __init__(
        self,
        cfg: HostConfig,
        calls: AppCalls,
        transport: Transport = urllib_transport,
        clock: Callable[[], datetime] = _utcnow,
        read_key: Callable[[Path], str] = _read_key,
    ) -> None:
        self._cfg = cfg
        self._calls = calls
        self._transport = transport
        self._clock = clock
        self._read_key = read_key
        self._token: str | None = None
        self._expires: datetime | None = None
        self.bot_login: str | None = None

    def jwt(self) -> str:
        """Свежий JWT RS256 (iat − 60 с, exp + 9 мин)."""
        try:
            key = self._read_key(self._cfg.private_key)
        except OSError as exc:
            raise Blocked("ключ App не прочитан") from exc
        now = int(self._clock().timestamp())
        claims = {"iat": now - 60, "exp": now + 540, "iss": str(self._cfg.app_id)}
        return pyjwt.encode(claims, key, algorithm="RS256")

    def token(self) -> str | None:
        """Токен установки из памяти; обновление, если до истечения < 10 мин."""
        now = self._clock()
        if self._token and self._expires and self._expires - now > TOKEN_MARGIN:
            return self._token
        self._token = None
        path = f"/app/installations/{self._cfg.installation_id}/access_tokens"
        result = self.call("service", "POST", path, auth="jwt")
        data = (
            result.response.json()
            if result.outcome == "ok" and result.response
            else None
        )
        if not isinstance(data, dict) or "token" not in data:
            return None
        self._token, self._expires = data["token"], parse_ts(data["expires_at"])
        return self._token

    def call(
        self,
        klass: str,
        method: str,
        path: str,
        *,
        auth: Auth,
        body: dict | None = None,
        graphql: bool = False,
    ) -> CallResult:
        """Один вызов: запрет → учётные данные → begin → транспорт → end."""
        now = self._clock()
        until = self._calls.blocked_until()
        if until is not None and now < until:
            raise Blocked(f"запрет по лимиту до {iso(until)}")
        credential = self.jwt() if auth == "jwt" else self.token()
        if credential is None:
            raise Blocked("нет токена установки")
        headers = {
            "Authorization": f"Bearer {credential}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "conductor",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            seq = self._calls.begin(klass, now)
        except OSError as exc:
            raise Blocked("журнал вызовов установки не записан") from exc
        try:
            resp: Response | None = self._transport(method, API + path, headers, data)
        except TransportError:
            resp = None
        outcome: Outcome = classify(resp, graphql) if resp is not None else "uncertain"
        info = rate_info(resp.headers) if resp is not None else RateInfo()
        try:
            self._calls.end(
                seq, self._clock(), outcome, resp.status if resp else None, info, klass
            )
        except OSError as exc:
            raise JournalLost("строка end не записана") from exc
        return CallResult(outcome, resp)

    def check_key(self) -> str | None:
        """Шаг 5 §5.1: свежий JWT и GET /app; логин бота или None."""
        result = self.call("service", "GET", "/app", auth="jwt")
        data = (
            result.response.json()
            if result.outcome == "ok" and result.response
            else None
        )
        if not isinstance(data, dict) or data.get("id") != self._cfg.app_id:
            return None
        self.bot_login = f"{data['slug']}[bot]"
        return self.bot_login

    def check_installation(self, owner: str) -> bool:
        """О §8.2 на старте: установка существует и принадлежит владельцу."""
        path = f"/app/installations/{self._cfg.installation_id}"
        result = self.call("service", "GET", path, auth="jwt")
        data = (
            result.response.json()
            if result.outcome == "ok" and result.response
            else None
        )
        return (
            isinstance(data, dict) and (data.get("account") or {}).get("login") == owner
        )

    def covers(self, repo: str) -> bool | None:
        """Шаг 7 §5.1: свежий запрос покрытия репо установкой."""
        result = self.call("service", "GET", f"/repos/{repo}/installation", auth="jwt")
        if (
            result.outcome == "failed"
            and result.response
            and result.response.status == 404
        ):
            return False
        if result.outcome != "ok" or result.response is None:
            return None
        data = result.response.json()
        return isinstance(data, dict) and data.get("id") == self._cfg.installation_id
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/conductor/test_gh_app.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock conductor/gh_app.py tests/conductor/test_gh_app.py
git commit -m "conductor: клиент App — JWT, токен, ключ, установка, покрытие (срез 1, §4.3)"
```

---
### Task A9: журнал прогона и учёт мутаций

**Files:**
- Create: `conductor/journal.py`
- Test: `tests/conductor/test_journal.py`

**Interfaces:**
- Consumes: `durable.append_line`, `durable.read_jsonl` (A4).
- Produces: `redact(value: Any) -> Any`; `RunJournal(run_dir: Path)` с `.write(row: dict) -> None`; `MutationLog(run_dir: Path)` с `.intent(*, attempt_id, method, endpoint, repo, marker_key, body: bytes | None) -> int`, `.result(seq: int, *, sent: bool, outcome: str, status: int | None) -> None`, `.end_run() -> None`; `log_complete(path: Path) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
"""Журнал прогона и учёт мутаций (спека среза 1, §4.7)."""

from pathlib import Path

from conductor.durable import read_jsonl
from conductor.journal import MutationLog, RunJournal, log_complete, redact

TOKEN = "ghs_" + "A" * 36
JWT = "eyJhbGciOiJSUzI1NiJ9.eyJpc3MiOiIxMSJ9.c2lnbmF0dXJlLWJ5dGVz"
PEM = "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----"


def test_redact_strings_and_nested() -> None:
    row = {"err": f"401 for {TOKEN}", "n": [JWT, {"k": PEM}], "x": 1}
    out = redact(row)
    text = str(out)
    assert TOKEN not in text and JWT not in text and "PRIVATE KEY" not in text
    assert out["x"] == 1 and "[REDACTED]" in out["err"]


def test_run_journal_redacts(tmp_path: Path) -> None:
    j = RunJournal(tmp_path)
    j.write({"reason": f"bad {TOKEN}"})
    assert TOKEN not in (tmp_path / "journal.jsonl").read_text(encoding="utf-8")


def test_mutation_log_complete_only_with_results_and_run_end(tmp_path: Path) -> None:
    log = MutationLog(tmp_path)
    seq = log.intent(
        attempt_id="a1",
        method="POST",
        endpoint="/repos/o/r/issues/1/comments",
        repo="o/r",
        marker_key="k",
        body=b'{"body": "x"}',
    )
    assert not log_complete(tmp_path / "calls.jsonl")
    log.result(seq, sent=True, outcome="ok", status=201)
    assert not log_complete(tmp_path / "calls.jsonl")
    log.end_run()
    assert log_complete(tmp_path / "calls.jsonl")
    rows = read_jsonl(tmp_path / "calls.jsonl").rows
    assert "body" not in rows[0] and len(rows[0]["body_sha256"]) == 64


def test_intent_without_result_is_incomplete(tmp_path: Path) -> None:
    log = MutationLog(tmp_path)
    log.intent(
        attempt_id="a1",
        method="POST",
        endpoint="/x",
        repo="o/r",
        marker_key="k",
        body=None,
    )
    log.end_run()
    assert not log_complete(tmp_path / "calls.jsonl")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/conductor/test_journal.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor.journal'`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Журнал прогона и учёт мутаций (спека среза 1, §4.7).

Журнал — диагностика, не операционное состояние: из него ничего не читается
при принятии решений. calls.jsonl — только интерфейс мутаций: строка intent
до отправки и result после; intent без result — «мог быть отправлен».
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from conductor.durable import append_line, read_jsonl

SECRET_RE = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{16,}"
    r"|github_pat_[A-Za-z0-9_]{16,}"
    r"|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
    r"|-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----",
    re.DOTALL,
)


def redact(value: Any) -> Any:
    """Вырезать токены, JWT и PEM из любых строк (рекурсивно)."""
    if isinstance(value, str):
        return SECRET_RE.sub("[REDACTED]", value)
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


class RunJournal:
    """journal.jsonl прогона."""

    def __init__(self, run_dir: Path) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        self.path = run_dir / "journal.jsonl"

    def write(self, row: dict[str, Any]) -> None:
        """Строка журнала без секретов."""
        append_line(self.path, redact(row))


class MutationLog:
    """calls.jsonl: intent/result каждой мутации, run-end в конце прогона."""

    def __init__(self, run_dir: Path) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        self.path = run_dir / "calls.jsonl"
        self._seq = 0

    def intent(
        self,
        *,
        attempt_id: str,
        method: str,
        endpoint: str,
        repo: str,
        marker_key: str,
        body: bytes | None,
    ) -> int:
        """Строка до отправки; сбой — OSError (мутации нет)."""
        self._seq += 1
        append_line(
            self.path,
            {
                "t": "intent",
                "seq": self._seq,
                "attempt_id": attempt_id,
                "method": method,
                "endpoint": endpoint,
                "repo": repo,
                "marker_key": marker_key,
                "body_sha256": hashlib.sha256(body).hexdigest() if body else None,
            },
        )
        return self._seq

    def result(self, seq: int, *, sent: bool, outcome: str, status: int | None) -> None:
        """Строка после вызова."""
        append_line(
            self.path,
            {
                "t": "result",
                "seq": seq,
                "sent": sent,
                "outcome": outcome,
                "status": status,
            },
        )

    def end_run(self) -> None:
        """Учёт прогона завершён."""
        append_line(self.path, {"t": "run-end"})


def log_complete(path: Path) -> bool:
    """Завершён: последняя строка run-end и у каждого intent есть result."""
    try:
        read = read_jsonl(path)
    except (OSError, ValueError):
        return False
    if read.truncated_tail or not read.rows or read.rows[-1]["t"] != "run-end":
        return False
    intents = {r["seq"] for r in read.rows if r["t"] == "intent"}
    results = {r["seq"] for r in read.rows if r["t"] == "result"}
    return intents <= results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/conductor/test_journal.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add conductor/journal.py tests/conductor/test_journal.py
git commit -m "conductor: журнал прогона и учёт мутаций с фильтром секретов (срез 1, §4.7)"
```

---

### Task A10: белый список мутаций и фактическая цель

**Files:**
- Create: `conductor/gh_write.py`
- Test: `tests/conductor/test_gh_write.py`
- Create: `tests/conductor/fake_app.py` (фейковый клиент; переиспользуется в A11)

**Interfaces:**
- Consumes: `AppClient.call` и `CallResult` (A8) — через утиную типизацию (фейк в тестах), `Response` (A7).
- Produces: `Op = Literal["comment", "create", "body", "pin", "close"]`; `KLASS: dict[str, str]`; `Mutation(op, repo, number=None, text="", title="", labels=())` с `.target() -> str`; `TargetCheck(ok: bool, reason: str = "", node_id: str | None = None)`; `check_target(client, m: Mutation) -> TargetCheck`; `request_of(m: Mutation, node_id: str | None) -> tuple[str, str, dict, bool]`; `PINNED_QUERY`; `verify(client, m: Mutation, resp: Response | None, bot_login: str, node_id: str | None = None) -> str | None` — None: эффект подтверждён НЕЗАВИСИМЫМ чтением цели (комментарий — `GET …/issues/comments/{id}`: тело, автор, тред; тело/закрытие/создание — `GET …/issues/{n}`; закрепление — GraphQL-чтение `isPinned`); иначе причина неопределённости (ревью P2-1).
- `tests/conductor/fake_app.py`: `FakeClient` — фейковый GitHub с миром issues: поля `bot_login`, `key_ok`, `cover`, `blocked`, `apply` (False — GitHub «принял», но эффект не виден в чтении), `override: dict[tuple[str, str], CallResult]`, `sent` (только мутации), `reads`, `covers_calls`, `issues`, `pulls`; методы `issue(repo, n, **fields)`, `pull(repo, n, **fields)` (REST-поля и `checks`/`reviews`/`closing` для GraphQL-чтения PR), `add_comment(repo, n, author, body, **fields)`, `check_key()`, `check_installation(owner)`, `covers(repo)`, `call(...)` (GET issue/комментариев/комментария/timeline/pulls/search, GraphQL `pinIssue`/`isPinned`/`pullRequest`, мутации применяются к миру).

- [ ] **Step 1: Write the fake client**

```python
"""Фейковый GitHub для исполнителя: мир issues и комментариев (тесты).

Мутации применяются к миру и отвечают как GitHub; чтения (GET и GraphQL-
запросы) отдают текущее состояние мира — verify и свежие сверки шага
проверяются против того же мира. `apply = False` — GitHub «принял», но
эффект не виден в чтении. `sent` — только мутации (не чтения).
"""

from __future__ import annotations

import json
import re
from typing import Any

from conductor.gh_app import Blocked, CallResult
from conductor.http import Response

ISSUE_RE = re.compile(r"^/repos/([^/]+/[^/]+)/issues/(\d+)$")
COMMENTS_RE = re.compile(r"^/repos/([^/]+/[^/]+)/issues/(\d+)/comments(?:\?.*)?$")
COMMENT_RE = re.compile(r"^/repos/([^/]+/[^/]+)/issues/comments/(\d+)$")
TIMELINE_RE = re.compile(r"^/repos/([^/]+/[^/]+)/issues/(\d+)/timeline(?:\?.*)?$")
PULL_RE = re.compile(r"^/repos/([^/]+/[^/]+)/pulls/(\d+)$")
REPO_RE = re.compile(r"^/repos/([^/]+/[^/]+)$")
CREATE_RE = re.compile(r"^/repos/([^/]+/[^/]+)/issues$")
SEARCH_RE = re.compile(r"^/search/issues\?q=repo:([^+]+)\+label:owner-queue")
PAGE_RE = re.compile(r"[?&]page=(\d+)")
STAMP = "2026-10-01T12:00:00Z"


def ok(data: Any, status: int = 200) -> CallResult:
    """Успешный ответ с JSON-телом."""
    return CallResult("ok", Response(status, {}, json.dumps(data).encode()))


def _page(items: list[Any], path: str) -> list[Any]:
    match = PAGE_RE.search(path)
    page = int(match.group(1)) if match else 1
    return items[(page - 1) * 100 : page * 100]


class FakeClient:
    """Двойник AppClient над миром issues (repo, N)."""

    def __init__(self) -> None:
        self.bot_login = "conductor[bot]"
        self.key_ok = True
        self.cover: dict[str, bool | None] = {}
        self.blocked = False
        self.apply = True
        self.override: dict[tuple[str, str], CallResult] = {}
        self.sent: list[tuple[str, str, dict | None]] = []
        self.reads: list[str] = []
        self.covers_calls: list[str] = []
        self.issues: dict[tuple[str, int], dict[str, Any]] = {}
        self.pulls: dict[tuple[str, int], dict[str, Any]] = {}
        self._next_number = 100
        self._next_comment = 1000

    # --- мир ---------------------------------------------------------------

    def issue(self, repo: str, number: int, **fields: Any) -> dict[str, Any]:
        """Issue мира (создаётся открытым по умолчанию); fields — правка."""
        data = self.issues.setdefault(
            (repo, number),
            {
                "state": "open",
                "state_reason": None,
                "body": "",
                "title": "",
                "labels": [],
                "user": "own",
                "pinned": False,
                "comments": [],
                "timeline": [],
            },
        )
        data.update(fields)
        return data

    def add_comment(
        self, repo: str, number: int, author: str, body: str, **fields: Any
    ) -> dict[str, Any]:
        """Комментарий в тред мира; fields — created_at/updated_at."""
        self._next_comment += 1
        c = {
            "id": self._next_comment,
            "user": {"login": author},
            "body": body,
            "created_at": STAMP,
            "updated_at": STAMP,
            "issue_url": f"https://api.github.com/repos/{repo}/issues/{number}",
            **fields,
        }
        self.issue(repo, number)["comments"].append(c)
        return c

    def _issue_json(self, repo: str, number: int) -> dict[str, Any]:
        i = self.issue(repo, number)
        return {
            "number": number,
            "repository_url": f"https://api.github.com/repos/{repo}",
            "node_id": f"I_{number}",
            "state": i["state"],
            "state_reason": i["state_reason"],
            "body": i["body"],
            "title": i["title"],
            "labels": [{"name": n} for n in i["labels"]],
            "user": {"login": i["user"]},
        }

    # --- интерфейс клиента -------------------------------------------------

    def check_key(self) -> str | None:
        if self.blocked:
            raise Blocked("лимит")
        return self.bot_login if self.key_ok else None

    def check_installation(self, owner: str) -> bool:
        return True

    def covers(self, repo: str) -> bool | None:
        if self.blocked:
            raise Blocked("лимит")
        self.covers_calls.append(repo)
        return self.cover.get(repo, True)

    def call(
        self,
        klass: str,
        method: str,
        path: str,
        *,
        auth: str,
        body: dict | None = None,
        graphql: bool = False,
    ) -> CallResult:
        if self.blocked:
            raise Blocked("лимит")
        query = (body or {}).get("query", "") if graphql else ""
        read = method == "GET" or (graphql and not query.startswith("mutation"))
        if read:
            self.reads.append(path)
        else:
            self.sent.append((method, path, body))
        if (method, path) in self.override:
            return self.override[(method, path)]
        if graphql:
            return self._graphql(query, (body or {}).get("variables") or {})
        if method == "GET":
            return self._get(path)
        return self._mutate(method, path, body or {})

    def _get(self, path: str) -> CallResult:
        if m := ISSUE_RE.match(path):
            return ok(self._issue_json(m.group(1), int(m.group(2))))
        if m := COMMENTS_RE.match(path):
            comments = self.issue(m.group(1), int(m.group(2)))["comments"]
            return ok(_page(comments, path))
        if m := COMMENT_RE.match(path):
            cid = int(m.group(2))
            for i in self.issues.values():
                for c in i["comments"]:
                    if c["id"] == cid:
                        return ok(c)
            return CallResult("failed", Response(404, {}, b"{}"))
        if m := TIMELINE_RE.match(path):
            events = self.issue(m.group(1), int(m.group(2)))["timeline"]
            return ok(_page(events, path))
        if m := PULL_RE.match(path):
            pull = self.pulls.get((m.group(1), int(m.group(2))))
            if pull is None:
                return CallResult("failed", Response(404, {}, b"{}"))
            return ok(pull)
        if m := SEARCH_RE.match(path):
            repo = m.group(1)
            items = [
                {"number": n, **self._issue_json(r, n)}
                for (r, n), i in sorted(self.issues.items())
                if r == repo and "owner-queue" in i["labels"]
            ]
            return ok({"incomplete_results": False, "items": items})
        if m := REPO_RE.match(path):
            return ok({"full_name": m.group(1)})
        raise AssertionError(f"неожиданный GET {path}")

    def pull(self, repo: str, number: int, **fields: Any) -> dict[str, Any]:
        """PR мира (REST-поля + checks/reviews/closing для GraphQL)."""
        data = self.pulls.setdefault(
            (repo, number),
            {
                "state": "open",
                "merged": False,
                "merge_commit_sha": None,
                "draft": False,
                "head": {"sha": "h1"},
                "updated_at": STAMP,
                "checks": [],
                "reviews": [],
                "closing": [],
            },
        )
        data.update(fields)
        return data

    def _pr_graphql(self, variables: dict[str, Any]) -> CallResult:
        key = (f"{variables['o']}/{variables['n']}", int(variables["k"]))
        pr = self.pulls.get(key)
        if pr is None:
            return ok({"data": {"repository": {"pullRequest": None}}})
        checks, reviews = pr.get("checks", []), pr.get("reviews", [])
        closing = [
            {
                "number": int(r.rsplit("#", 1)[1]),
                "repository": {"nameWithOwner": r.rsplit("#", 1)[0]},
            }
            for r in pr.get("closing", [])
        ]
        node = {
            "state": "MERGED" if pr.get("merged") else pr.get("state", "open").upper(),
            "merged": bool(pr.get("merged")),
            "isDraft": bool(pr.get("draft")),
            "headRefOid": (pr.get("head") or {}).get("sha"),
            "updatedAt": pr.get("updated_at"),
            "mergeCommit": {"oid": pr["merge_commit_sha"]}
            if pr.get("merge_commit_sha")
            else None,
            "closingIssuesReferences": {"totalCount": len(closing), "nodes": closing},
            "latestReviews": {"totalCount": len(reviews), "nodes": reviews},
            "commits": {
                "nodes": [
                    {
                        "commit": {
                            "statusCheckRollup": {
                                "contexts": {"totalCount": len(checks), "nodes": checks}
                            }
                        }
                    }
                ]
            },
        }
        return ok({"data": {"repository": {"pullRequest": node}}})

    def _graphql(self, query: str, variables: dict[str, Any]) -> CallResult:
        if "pullRequest" in query:
            return self._pr_graphql(variables)
        node = variables.get("id")
        found = next(
            (i for (_, n), i in sorted(self.issues.items()) if f"I_{n}" == node),
            None,
        )
        if query.startswith("mutation"):
            if found is not None and self.apply:
                found["pinned"] = True
            return ok({"data": {"pinIssue": {"issue": {"id": node}}}})
        pinned = bool(found and found["pinned"])
        return ok({"data": {"node": {"isPinned": pinned} if found else None}})

    def _mutate(self, method: str, path: str, body: dict) -> CallResult:
        if (m := COMMENTS_RE.match(path)) and method == "POST":
            repo, number = m.group(1), int(m.group(2))
            if self.apply:
                c = self.add_comment(repo, number, self.bot_login, body["body"])
            else:
                self._next_comment += 1
                c = {
                    "id": self._next_comment,
                    "body": body["body"],
                    "user": {"login": self.bot_login},
                }
            return ok(c, 201)
        if (m := CREATE_RE.match(path)) and method == "POST":
            self._next_number += 1
            repo, number = m.group(1), self._next_number
            if self.apply:
                self.issue(
                    repo,
                    number,
                    body=body["body"],
                    title=body.get("title", ""),
                    labels=list(body.get("labels", [])),
                    user=self.bot_login,
                )
            return ok({"number": number, "body": body["body"]}, 201)
        if (m := ISSUE_RE.match(path)) and method == "PATCH":
            repo, number = m.group(1), int(m.group(2))
            if self.apply:
                self.issue(repo, number, **body)
            return ok({**self._issue_json(repo, number), **body})
        raise AssertionError(f"неожиданная мутация {method} {path}")
```

- [ ] **Step 2: Write the failing test**

```python
"""Белый список мутаций и фактическая цель (спека среза 1, §4.5, §5.1 шаг 8)."""

from conductor.gh_app import CallResult
from conductor.gh_write import (
    KLASS,
    Mutation,
    check_target,
    request_of,
    verify,
)
from conductor.http import Response
from tests.conductor.fake_app import FakeClient, ok

ALLOWED = {
    ("POST", "/repos/o/r/issues/1/comments"),
    ("POST", "/repos/o/r/issues"),
    ("PATCH", "/repos/o/r/issues/1"),
    ("POST", "/graphql"),
}


def test_whitelist_is_closed() -> None:
    seen = set()
    for op in KLASS:
        number = None if op == "create" else 1
        m = Mutation(op, "o/r", number, text="t", title="q")  # type: ignore[arg-type]
        method, path, _, _ = request_of(m, "I_1")
        seen.add((method, path))
    assert seen == ALLOWED
    assert KLASS == {
        "comment": "create",
        "create": "create",
        "body": "update",
        "close": "update",
        "pin": "update",
    }


def test_close_body_is_completed_only() -> None:
    _, _, body, _ = request_of(Mutation("close", "o/r", 1), None)
    assert body == {"state": "closed", "state_reason": "completed"}


def test_check_target_existing_ok_and_moved() -> None:
    client = FakeClient()
    assert (
        check_target(client, Mutation("comment", "o/r", 1, text="x")).node_id == "I_1"
    )
    client.override[("GET", "/repos/o/r/issues/1")] = CallResult(
        "moved", Response(301, {"location": "/repos/o/z/issues/9"}, b"")
    )
    assert check_target(client, Mutation("comment", "o/r", 1)).reason == "TARGET-MOVED"
    client.override[("GET", "/repos/o/r/issues/1")] = ok(
        {"number": 1, "repository_url": "https://api.github.com/repos/o/other"}
    )
    assert check_target(client, Mutation("comment", "o/r", 1)).reason == "TARGET-MOVED"


def test_check_target_create_checks_repo_name() -> None:
    client = FakeClient()
    assert check_target(client, Mutation("create", "o/r", text="b", title="q")).ok
    client.override[("GET", "/repos/o/r")] = ok({"full_name": "o/renamed"})
    assert not check_target(client, Mutation("create", "o/r")).ok


def _sent(client: FakeClient, m: Mutation, node_id: str | None = None):
    method, path, body, graphql = request_of(m, node_id)
    return client.call(
        KLASS[m.op], method, path, auth="token", body=body, graphql=graphql
    )


def test_verify_rereads_target_for_every_op() -> None:
    bot = "conductor[bot]"
    client = FakeClient()
    comment = Mutation("comment", "o/r", 1, text="x")
    assert verify(client, comment, _sent(client, comment).response, bot) is None
    body = Mutation("body", "o/r", 1, text="b")
    assert verify(client, body, _sent(client, body).response, bot) is None
    close = Mutation("close", "o/r", 1)
    assert verify(client, close, _sent(client, close).response, bot) is None
    pin = Mutation("pin", "o/r", 1)
    assert verify(client, pin, _sent(client, pin, "I_1").response, bot, "I_1") is None
    create = Mutation("create", "o/r", text="q", title="t", labels=("owner-queue",))
    assert verify(client, create, _sent(client, create).response, bot) is None


def test_verify_2xx_without_visible_effect_is_uncertain() -> None:
    """Регрессия P2-1: ответ мутации 2xx эффекта не доказывает."""
    bot = "conductor[bot]"
    client = FakeClient()
    client.apply = False  # GitHub «принял», но чтение эффекта не показывает
    for m, node in (
        (Mutation("comment", "o/r", 1, text="x"), None),
        (Mutation("body", "o/r", 1, text="b"), None),
        (Mutation("close", "o/r", 1), None),
        (Mutation("pin", "o/r", 1), "I_1"),
    ):
        resp = _sent(client, m, node).response
        assert verify(client, m, resp, bot, node) is not None, m.op


def test_verify_unreadable_control_read_is_uncertain() -> None:
    bot = "conductor[bot]"
    client = FakeClient()
    close = Mutation("close", "o/r", 1)
    resp = _sent(client, close).response
    client.override[("GET", "/repos/o/r/issues/1")] = CallResult("uncertain", None)
    assert verify(client, close, resp, bot) == "цель не прочитана после записи"
    assert verify(client, close, None, bot) == "ответ мутации не разобран"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/conductor/test_gh_write.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor.gh_write'`.

- [ ] **Step 4: Write minimal implementation**

```python
"""Мутации App: закрытый белый список и фактическая цель (спека среза 1, §4.5).

Цель — существующий объект: свежее чтение без перенаправлений, репо и номер
совпадают с планом; иначе TARGET-MOVED (снято). Цель — создание в репо:
каноническое имя репо совпадает. Endpoint строится только из сверенных
репо и номера. verify (О §5.0 шаг 3) — НЕЗАВИСИМОЕ чтение цели после
мутации: ответ самой мутации эффекта не доказывает.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from conductor.gh_app import CallResult
from conductor.http import Response

Op = Literal["comment", "create", "body", "pin", "close"]
KLASS: dict[str, str] = {
    "comment": "create",
    "create": "create",
    "body": "update",
    "close": "update",
    "pin": "update",
}
PIN_QUERY = "mutation($id:ID!){pinIssue(input:{issueId:$id}){issue{id}}}"
PINNED_QUERY = "query($id:ID!){node(id:$id){... on Issue{isPinned}}}"


class Caller(Protocol):
    """То, что нужно от клиента App для проверки цели."""

    def call(
        self,
        klass: str,
        method: str,
        path: str,
        *,
        auth: Any,
        body: dict | None = None,
        graphql: bool = False,
    ) -> CallResult: ...


@dataclass(frozen=True)
class Mutation:
    """Одна мутация: операция, репо owner/name, номер (None — создание)."""

    op: Op
    repo: str
    number: int | None = None
    text: str = ""
    title: str = ""
    labels: tuple[str, ...] = ()

    def target(self) -> str:
        """Каноническая цель: repo#N или repo для создания."""
        return self.repo if self.number is None else f"{self.repo}#{self.number}"


@dataclass(frozen=True)
class TargetCheck:
    """Итог сверки цели; node_id нужен для pinIssue."""

    ok: bool
    reason: str = ""
    node_id: str | None = None


def _data(result: CallResult) -> dict[str, Any] | None:
    if result.outcome != "ok" or result.response is None:
        return None
    data = result.response.json()
    return data if isinstance(data, dict) else None


def check_target(client: Caller, m: Mutation) -> TargetCheck:
    """Шаг 8 §5.1: фактическая идентичность цели перед мутацией."""
    path = (
        f"/repos/{m.repo}" if m.op == "create" else f"/repos/{m.repo}/issues/{m.number}"
    )
    result = client.call("service", "GET", path, auth="token")
    if result.outcome == "moved":
        return TargetCheck(False, "TARGET-MOVED")
    data = _data(result)
    if data is None:
        return TargetCheck(False, "TARGET-UNREAD")
    if m.op == "create":
        same = data.get("full_name") == m.repo
        return TargetCheck(same, "" if same else "TARGET-MOVED")
    url = str(data.get("repository_url", "")).lower()
    if data.get("number") != m.number or not url.endswith(f"/repos/{m.repo.lower()}"):
        return TargetCheck(False, "TARGET-MOVED")
    return TargetCheck(True, node_id=data.get("node_id"))


def request_of(m: Mutation, node_id: str | None) -> tuple[str, str, dict, bool]:
    """(метод, путь, тело, graphql) — единственный источник endpoint'ов."""
    base = f"/repos/{m.repo}/issues"
    if m.op == "comment":
        return "POST", f"{base}/{m.number}/comments", {"body": m.text}, False
    if m.op == "create":
        payload = {"title": m.title, "body": m.text, "labels": list(m.labels)}
        return "POST", base, payload, False
    if m.op == "body":
        return "PATCH", f"{base}/{m.number}", {"body": m.text}, False
    if m.op == "close":
        payload = {"state": "closed", "state_reason": "completed"}
        return "PATCH", f"{base}/{m.number}", payload, False
    return "POST", "/graphql", {"query": PIN_QUERY, "variables": {"id": node_id}}, True


def _read(client: Caller, path: str) -> dict[str, Any] | None:
    return _data(client.call("service", "GET", path, auth="token"))


def _same_issue(data: dict[str, Any], repo: str, number: int) -> bool:
    url = str(data.get("repository_url", "")).lower()
    return data.get("number") == number and url.endswith(f"/repos/{repo.lower()}")


def _pinned(client: Caller, node_id: str | None) -> bool:
    result = client.call(
        "service",
        "POST",
        "/graphql",
        auth="token",
        body={"query": PINNED_QUERY, "variables": {"id": node_id}},
        graphql=True,
    )
    node = ((_data(result) or {}).get("data") or {}).get("node") or {}
    return node.get("isPinned") is True


def verify(
    client: Caller,
    m: Mutation,
    resp: Response | None,
    bot_login: str,
    node_id: str | None = None,
) -> str | None:
    """verify О §5.0 шаг 3: перечитать цель и сверить с ожидаемым.

    None — эффект подтверждён чтением; иначе причина неопределённости
    (ответ мутации не разобран, контрольное чтение не удалось или не совпало).
    """
    data = resp.json() if resp is not None else None
    if not isinstance(data, dict):
        return "ответ мутации не разобран"
    if m.op == "pin":
        return None if _pinned(client, node_id) else "закрепление не подтверждено"
    if m.op == "comment":
        cid = data.get("id")
        if not isinstance(cid, int):
            return "в ответе нет id комментария"
        c = _read(client, f"/repos/{m.repo}/issues/comments/{cid}")
        url = str((c or {}).get("issue_url", "")).lower()
        same = c is not None and url.endswith(
            f"/repos/{m.repo.lower()}/issues/{m.number}"
        )
        author = ((c or {}).get("user") or {}).get("login")
        if not same or (c or {}).get("body") != m.text or author != bot_login:
            return "комментарий не подтверждён чтением"
        return None
    number = data.get("number") if m.op == "create" else m.number
    if not isinstance(number, int):
        return "в ответе нет номера"
    issue = _read(client, f"/repos/{m.repo}/issues/{number}")
    if issue is None or not _same_issue(issue, m.repo, number):
        return "цель не прочитана после записи"
    if m.op == "close":
        done = issue.get("state") == "closed"
        if not done or issue.get("state_reason") != "completed":
            return "закрытие не подтверждено чтением"
        return None
    if issue.get("body") != m.text:
        return "тело не подтверждено чтением"
    if m.op == "create":
        labels = {lab.get("name") for lab in issue.get("labels") or []}
        author = (issue.get("user") or {}).get("login")
        if author != bot_login or not set(m.labels) <= labels:
            return "созданная очередь не подтверждена чтением"
    return None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/conductor/test_gh_write.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add conductor/gh_write.py tests/conductor/test_gh_write.py tests/conductor/fake_app.py
git commit -m "conductor: белый список мутаций и сверка фактической цели (срез 1, §4.5)"
```

---
### Task A11: исполнитель записей

**Files:**
- Create: `conductor/writer.py`
- Test: `tests/conductor/test_writer.py`

**Interfaces:**
- Consumes: `HostConfig` (A2), `h1` (A3), `OpState`, `parse_ts` (A5), `Blocked`, `JournalLost`, `CallResult` (A8), `RunJournal`, `MutationLog` (A9), `Mutation`, `KLASS`, `check_target`, `request_of`, `verify` (A10), `Roadmap` (A1); фейк `tests/conductor/fake_app.FakeClient` (A10).
- Produces: `Step(mutation: Mutation, event_key: str, revision: str = "", optional: bool = False, authority: Callable[[Roadmap, int], int] | None = None)`; `PlanRecord(action, subject, revision, level, steps: tuple[Step, ...], revalidate: Callable[[Mutation], str | None] = всегда None, authority: Callable[[Roadmap, int], int] = потолок прогона)`; `expected_of(m) -> str`; `StepReport(action, subject, op, target, outcome, reason="", admissible=None, allowed=None)`; `StopPoint(name)`; `mutation_id(rec, step, m) -> str`; `effect_key(step, m) -> str`; `Writer(*, cfg, client, state, log, journal, fence, load_roadmap, hostname, run_id, level_cap=0, partial=False, clock=...)` с `.execute(records: list[PlanRecord]) -> list[StepReport]`.
- Шаг 6 (`_permission`, по роадмапу, перечитанному перед шагом), первое нарушенное: `partial` → `run_level` (`level_cap` < уровня действия) → `autonomy` (`min(level_cap, autonomy)` < уровня) → `enabled_actions` → `position_level` (`(step.authority or rec.authority)(roadmap, run_level)` < уровня). Не в тени: `enabled_actions` — отзыв действия; `position_level` — `revoked_action` только этой записи; прочее — общий отзыв.
- Шаг 8: `rec.revalidate(m)` с разрешённой целью (номер созданной очереди подставлен) — причина ≠ None → `removed` с этой причиной. Шаг `optional` при сбое не блокирует следующие шаги записи; `mutation_id` берёт `step.revision or rec.revision`.
- Исходы `StepReport.outcome`: `success`, `shadow`, `fence`, `revoked_all`, `revoked_action`, `not_sent`, `removed`, `delay`, `failed`, `uncertain`, `skipped_dependent`.

- [ ] **Step 1: Write the failing test**

```python
"""Исполнитель записей: порядок проверок §5.1 и матрица исходов §5.2."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from conductor.gh_app import CallResult
from conductor.gh_write import Mutation
from conductor.host_config import HostConfig
from conductor.http import Response
from conductor.journal import MutationLog, RunJournal, log_complete
from conductor.opstate import init_state, open_state
from conductor.roadmap import parse_roadmap
from conductor.writer import PlanRecord, Step, StopPoint, Writer, effect_key
from tests.conductor.fake_app import FakeClient, ok
from tests.conductor.fixtures import EPICS, ROADMAP

T_INIT = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
FENCE = frozenset({"own/a", "own/ai-orchestrators-workspace"})
QUEUE = "own/ai-orchestrators-workspace"
CFG = HostConfig(
    app_id=11,
    installation_id=22,
    private_key=Path("/k.pem"),
    profile="fleet",
    shadow=False,
    state_dir=Path("/s"),
)
COMMENTS = ("POST", "/repos/own/a/issues/1/comments")


def roadmap(
    autonomy: int = 1,
    enabled: tuple[str, ...] = ("nudge", "close_shipped"),
    since: str = "2026-09-29T00:00:00Z",
    max_writes: int = 20,
):
    text = (
        ROADMAP.replace(
            "autonomy = 0",
            f"autonomy = {autonomy}\nenabled_actions = {json.dumps(list(enabled))}",
        ).replace('writer_since = "2026-09-29T00:00:00Z"', f'writer_since = "{since}"')
        + f"[limits]\nmax_writes_per_run = {max_writes}\n"
    )
    rm = parse_roadmap(text, EPICS)
    assert rm.valid, rm.findings
    return rm


@pytest.fixture
def w(tmp_path: Path):
    init_state(tmp_path / "state", T_INIT)
    state = open_state(tmp_path / "state", NOW).state
    assert state is not None
    return SimpleNamespace(tmp=tmp_path, state=state, client=FakeClient())


def writer(
    w, cfg=CFG, rms=None, partial=False, hostname="vps", at=NOW, run="r1", cap=3
):
    seq = iter(rms) if rms is not None else None
    return Writer(
        cfg=cfg,
        client=w.client,
        state=w.state,
        log=MutationLog(w.tmp / run),
        journal=RunJournal(w.tmp / run),
        fence=FENCE,
        load_roadmap=(lambda: next(seq)) if seq is not None else roadmap,
        hostname=hostname,
        run_id=run,
        level_cap=cap,
        partial=partial,
        clock=lambda: at,
    )


def comment(n: int = 1, key: str = "k1", repo: str = "own/a") -> Step:
    return Step(Mutation("comment", repo, n, text="hi"), key)


def close(n: int = 1) -> Step:
    return Step(Mutation("close", "own/a", n), "close")


def rec(
    *steps: Step, action: str = "close_shipped", valid: bool = True, authority=None
) -> PlanRecord:
    reason = None if valid else "доказательство изменилось"
    extra = {"authority": authority} if authority is not None else {}
    return PlanRecord(
        action, "own/a#1", "rev1", 1, steps, revalidate=lambda m: reason, **extra
    )


def outcomes(reports) -> list[str]:
    return [r.outcome for r in reports]


def test_happy_path_and_complete_log(w) -> None:
    reports = writer(w).execute([rec(comment(), close())])
    assert outcomes(reports) == ["success", "success"]
    assert [s[0] for s in w.client.sent] == ["POST", "PATCH"]
    assert log_complete(w.tmp / "r1" / "calls.jsonl")


def test_fence_before_coverage(w) -> None:
    w.client.cover["own/outside"] = False
    reports = writer(w).execute([rec(comment(repo="own/outside"))])
    assert outcomes(reports) == ["fence"] and w.client.covers_calls == []


def test_quarantine_stops_all(w) -> None:
    reports = writer(w, at=T_INIT + timedelta(minutes=10)).execute(
        [rec(comment()), rec(comment(2, "k2"))]
    )
    assert outcomes(reports) == ["revoked_all", "revoked_all"] and w.client.sent == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"rms": [None]},
        {"hostname": "other"},
        {"rms": [roadmap(since="2026-10-01T11:30:00Z")]},
        {"rms": [roadmap(autonomy=0)]},
        {"partial": True},
    ],
)
def test_general_revocation(w, kwargs) -> None:
    reports = writer(w, **kwargs).execute([rec(comment(), close())])
    assert outcomes(reports) == ["revoked_all", "revoked_all"] and w.client.sent == []


def test_key_and_block_revoke_all(w) -> None:
    w.client.key_ok = False
    assert outcomes(writer(w).execute([rec(comment())])) == ["revoked_all"]
    w.client.key_ok, w.client.blocked = True, True
    assert outcomes(writer(w, run="r2").execute([rec(comment())])) == ["revoked_all"]
    assert w.client.sent == []


def test_action_revocation_keeps_other_actions(w) -> None:
    rm = roadmap(enabled=("nudge",))
    reports = writer(w, rms=[rm, rm, rm]).execute(
        [rec(close()), rec(close(3)), rec(comment(2, "k2"), action="nudge")]
    )
    assert outcomes(reports) == ["revoked_action", "revoked_action", "success"]


def test_mid_run_revocation_partial(w) -> None:
    reports = writer(w, rms=[roadmap(), roadmap(enabled=("nudge",))]).execute(
        [rec(comment(), close())]
    )
    assert outcomes(reports) == ["success", "revoked_action"]


def test_uncovered_not_sent_and_dependent_skipped(w) -> None:
    w.client.cover["own/a"] = False
    reports = writer(w).execute([rec(comment(), close())])
    assert outcomes(reports) == ["not_sent", "skipped_dependent"]
    assert reports[0].reason == "ID-REPO-UNCOVERED" and w.client.sent == []


def test_evidence_changed_removed_others_continue(w) -> None:
    reports = writer(w).execute([rec(comment(), valid=False), rec(comment(2, "k2"))])
    assert outcomes(reports) == ["removed", "success"]


def test_target_moved_removed(w) -> None:
    w.client.override[("GET", "/repos/own/a/issues/1")] = CallResult(
        "moved", Response(301, {}, b"")
    )
    reports = writer(w).execute([rec(comment())])
    assert outcomes(reports) == ["removed"] and reports[0].reason == "TARGET-MOVED"
    assert w.client.sent == []


def test_delay_by_effect_key_and_after_hour(w) -> None:
    step = comment()
    w.state.begin_attempt(
        attempt_id="old",
        mutation_id="m-old",
        effect_key=effect_key(step, step.mutation),
        target="own/a#1",
        marker_key="k1",
        action="close_shipped",
        subject="own/a#1",
        now=NOW - timedelta(minutes=10),
    )
    assert outcomes(writer(w).execute([rec(step)])) == ["delay"]
    assert w.client.sent == []
    later = writer(w, at=NOW + timedelta(minutes=51), run="r2")
    assert outcomes(later.execute([rec(step)])) == ["success"]


def test_budget_counts_sent_attempts(w) -> None:
    reports = writer(w, rms=[roadmap(max_writes=1)] * 2).execute(
        [rec(comment()), rec(comment(2, "k2"))]
    )
    assert outcomes(reports) == ["success", "revoked_all"]
    assert reports[1].reason == "RUN-BUDGET"


def test_shadow_with_autonomy_zero(w) -> None:
    shadow = HostConfig(**{**CFG.__dict__, "shadow": True})
    [report] = writer(w, cfg=shadow, rms=[roadmap(autonomy=0)]).execute(
        [rec(comment())]
    )
    assert (report.outcome, report.allowed, report.reason) == (
        "shadow",
        False,
        "autonomy",
    )
    assert w.client.sent == []


def test_shadow_with_permissive_roadmap(w) -> None:
    shadow = HostConfig(**{**CFG.__dict__, "shadow": True})
    reports = writer(w, cfg=shadow).execute([rec(comment(), close())])
    assert outcomes(reports) == ["shadow", "shadow"]
    assert all(r.allowed for r in reports) and w.client.sent == []


def test_failed_blocks_dependent_and_counts_series(w) -> None:
    w.client.override[COMMENTS] = CallResult("failed", Response(422, {}, b"{}"))
    reports = writer(w).execute([rec(comment(), close()), rec(comment(2, "k2"))])
    assert outcomes(reports) == ["failed", "skipped_dependent", "success"]
    assert [s["count"] for s in w.state.series.values()] == [1]


def test_uncertain_then_immediate_rerun_is_delayed(w) -> None:
    w.client.override[COMMENTS] = ok(
        {"body": "other", "user": {"login": "conductor[bot]"}}
    )
    assert outcomes(writer(w).execute([rec(comment())])) == ["uncertain"]
    del w.client.override[COMMENTS]
    rerun = writer(w, at=NOW + timedelta(minutes=5), run="r2")
    assert outcomes(rerun.execute([rec(comment())])) == ["delay"]


def test_rate_limited_stops_all(w) -> None:
    w.client.override[COMMENTS] = CallResult("rate_limited", Response(429, {}, b"{}"))
    reports = writer(w).execute([rec(comment()), rec(comment(2, "k2"))])
    assert outcomes(reports) == ["revoked_all", "revoked_all"]


def test_create_then_pin_and_comment_use_new_number(w) -> None:
    steps = (
        Step(Mutation("create", QUEUE, text="body", title="Очередь"), "owner-queue"),
        Step(Mutation("pin", QUEUE), "pin"),
        Step(Mutation("comment", QUEUE, text="q1"), "q1"),
    )
    rms = [roadmap(enabled=("owner_queue",))] * 3
    reports = writer(w, rms=rms).execute(
        [PlanRecord("owner_queue", QUEUE, "p1", 1, steps)]
    )
    assert outcomes(reports) == ["success"] * 3
    assert [p for _, p, _ in w.client.sent] == [
        f"/repos/{QUEUE}/issues",
        "/graphql",
        f"/repos/{QUEUE}/issues/101/comments",
    ]


def test_opstate_write_failure_sends_nothing(w, monkeypatch) -> None:
    def boom(**_: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(w.state, "begin_attempt", boom)
    reports = writer(w).execute([rec(comment())])
    assert outcomes(reports) == ["revoked_all"] and reports[0].reason == "OPSTATE-WRITE"
    assert w.client.sent == []


@pytest.mark.parametrize(("point", "sent"), [("after_intent", 0), ("after_send", 1)])
def test_stop_points_leave_attempt_in_flight(w, point: str, sent: int) -> None:
    acc = HostConfig(
        **{
            **CFG.__dict__,
            "profile": "acceptance",
            "sandbox": "own/a",
            "stop_points": frozenset({point}),
        }
    )
    with pytest.raises(StopPoint):
        writer(w, cfg=acc).execute([rec(comment())])
    assert len(w.client.sent) == sent
    assert [a["status"] for a in w.state.attempts.values()] == ["in_flight"]
    assert not log_complete(w.tmp / "r1" / "calls.jsonl")


def test_stop_points_ignored_in_fleet(w) -> None:
    fleet = HostConfig(**{**CFG.__dict__, "stop_points": frozenset({"after_send"})})
    assert outcomes(writer(w, cfg=fleet).execute([rec(comment())])) == ["success"]


def focus_level(rm, run_level: int) -> int:
    """Уровень позиции фокуса eco.focus1 (О §2.4) по переданному роадмапу."""
    focus = rm.focus_of("eco.focus1")
    return min(run_level, focus.autonomy) if focus is not None else 0


def with_focus_autonomy(rm_text_autonomy: int, focus_autonomy: int):
    text = ROADMAP.replace(
        "autonomy = 0",
        f'autonomy = {rm_text_autonomy}\nenabled_actions = ["nudge", "close_shipped"]',
    ).replace(
        'epic = "eco.focus1"', f'epic = "eco.focus1"\nautonomy = {focus_autonomy}'
    )
    rm = parse_roadmap(text, EPICS)
    assert rm.valid, rm.findings
    return rm


@pytest.mark.parametrize("cap", [0])
def test_cli_level_zero_means_no_mutations(w, cap: int) -> None:
    """Регрессия P1-2: потолок CLI (--level 0, --roadmap, --replay) — 0."""
    reports = writer(w, cap=cap).execute([rec(comment(), close())])
    assert outcomes(reports) == ["revoked_all", "revoked_all"]
    assert reports[0].reason == "run_level" and w.client.sent == []


def test_focus_autonomy_zero_blocks_position(w) -> None:
    """Регрессия P1-2: global autonomy=1, focus.autonomy=0 → позиции 0."""
    rm = with_focus_autonomy(1, 0)
    reports = writer(w, rms=[rm, rm]).execute(
        [rec(comment(), authority=focus_level), rec(comment(2, "k2"))]
    )
    assert outcomes(reports) == ["revoked_action", "success"]
    assert reports[0].reason == "position_level"
    assert [p for _, p, _ in w.client.sent] == ["/repos/own/a/issues/2/comments"]


def test_focus_revoked_between_steps(w) -> None:
    """Отзыв фокуса (focus.autonomy → 0) между шагами снимает второй шаг."""
    rms = [with_focus_autonomy(1, 1), with_focus_autonomy(1, 0)]
    reports = writer(w, rms=rms).execute(
        [rec(comment(), close(), authority=focus_level)]
    )
    assert outcomes(reports) == ["success", "revoked_action"]
    assert [s[0] for s in w.client.sent] == ["POST"]


def test_shadow_names_cli_ceiling(w) -> None:
    shadow = HostConfig(**{**CFG.__dict__, "shadow": True})
    [report] = writer(w, cfg=shadow, cap=0).execute([rec(comment())])
    assert (report.outcome, report.allowed, report.reason) == (
        "shadow",
        False,
        "run_level",
    )


def test_revalidate_sees_resolved_step_and_runs_before_each_step(w) -> None:
    """Регрессия P1-1: основание перепроверяется перед КАЖДЫМ шагом."""
    seen: list[str] = []

    def check(m: Mutation) -> str | None:
        seen.append(m.op)
        return "ответ изменился" if m.op == "close" else None

    record = PlanRecord(
        "close_shipped", "own/a#1", "rev1", 1, (comment(), close()), revalidate=check
    )
    reports = writer(w).execute([record])
    assert outcomes(reports) == ["success", "removed"]
    assert reports[1].reason == "ответ изменился" and seen == ["comment", "close"]
    assert [s[0] for s in w.client.sent] == ["POST"]


def test_unconfirmed_effect_is_uncertain_and_blocks_dependent(w) -> None:
    """Регрессия P2-1: 2xx, но контрольное чтение эффекта не видит."""
    w.client.apply = False
    reports = writer(w).execute([rec(comment(), close())])
    assert outcomes(reports) == ["uncertain", "skipped_dependent"]
    assert reports[0].reason == "комментарий не подтверждён чтением"
    assert [a["status"] for a in w.state.attempts.values()] == ["uncertain"]


def test_control_read_failure_is_uncertain(w) -> None:
    w.client.override[("GET", "/repos/own/a/issues/comments/1001")] = CallResult(
        "uncertain", None
    )
    reports = writer(w).execute([rec(comment(), close())])
    assert outcomes(reports) == ["uncertain", "skipped_dependent"]


def queue_record(body_step: Step) -> PlanRecord:
    steps = (
        body_step,
        Step(Mutation("pin", QUEUE, 5), "pin", "pin", optional=True),
        Step(Mutation("comment", QUEUE, 5, text="q1"), "q1", revision="q1"),
        Step(Mutation("comment", QUEUE, 5, text="q2"), "q2", revision="q2"),
    )
    return PlanRecord("owner_queue", QUEUE, "p1", 1, steps)


@pytest.mark.parametrize("outcome", ["failed", "uncertain"])
def test_queue_body_failure_blocks_questions(w, outcome: str) -> None:
    """Регрессия P2-2: сбой тела очереди — вопросы этого плана не пишутся."""
    path = f"/repos/{QUEUE}/issues/5"
    w.client.override[("PATCH", path)] = CallResult(outcome, Response(500, {}, b""))
    body = Step(Mutation("body", QUEUE, 5, text="b"), "body", revision="p1")
    rms = [roadmap(enabled=("owner_queue",))] * 4
    reports = writer(w, rms=rms).execute([queue_record(body)])
    assert outcomes(reports) == [outcome] + ["skipped_dependent"] * 3
    assert [s[1] for s in w.client.sent] == [path]


def test_queue_pin_failure_does_not_block_questions(w) -> None:
    w.client.override[("POST", "/graphql")] = CallResult(
        "failed", Response(422, {}, b"{}")
    )
    body = Step(Mutation("body", QUEUE, 5, text="b"), "body", revision="p1")
    rms = [roadmap(enabled=("owner_queue",))] * 4
    reports = writer(w, rms=rms).execute([queue_record(body)])
    assert outcomes(reports) == ["success", "failed", "success", "success"]


def test_question_series_keyed_by_step_revision(w) -> None:
    """Вопросы одной записи — разные мутации: серия у каждого своя (§5.4)."""
    w.client.override[("POST", f"/repos/{QUEUE}/issues/5/comments")] = CallResult(
        "failed", Response(422, {}, b"{}")
    )
    steps = (
        Step(Mutation("comment", QUEUE, 5, text="q1"), "q1", revision="q1"),
        Step(Mutation("comment", QUEUE, 5, text="q2"), "q2", revision="q2"),
    )
    rec_a = PlanRecord("owner_queue", QUEUE, "p1", 1, steps)
    rec_b = PlanRecord("owner_queue", QUEUE, "p2", 1, steps)  # проекция сменилась
    rms = [roadmap(enabled=("owner_queue",))] * 4
    writer(w, rms=rms[:2]).execute([rec_a])
    writer(w, rms=rms[2:], run="r2").execute([rec_b])
    assert sorted(s["count"] for s in w.state.series.values()) == [2]


def test_position_sees_run_level_min_of_cap_and_autonomy(w) -> None:
    """О §2.4: run_level = min(--level, autonomy) — его получает уровень позиции."""
    seen: list[int] = []

    def spy(rm, run_level: int) -> int:
        seen.append(run_level)
        return run_level

    writer(w, rms=[roadmap(autonomy=3)], cap=2).execute([rec(comment(), authority=spy)])
    writer(w, rms=[roadmap(autonomy=1)], cap=3, run="r2").execute(
        [rec(comment(2, "k2"), authority=spy)]
    )
    assert seen == [2, 1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/conductor/test_writer.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor.writer'`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Единственный исполнитель записей (спека среза 1, §5; О §5.0).

Перед КАЖДОЙ мутацией — проверки §5.1 в фиксированном порядке; исход —
по матрице §5.2. Неопределённость и сбой блокируют зависимые шаги той же
записи плана (шаг `optional` — нет), независимые записи продолжаются. Тень —
всё, кроме отправки. Полномочия (О §2.4) — по свежему роадмапу перед каждым
шагом: потолок прогона `min(level_cap, autonomy)` и уровень позиции записи.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from conductor.gh_app import Blocked, CallResult, JournalLost
from conductor.gh_write import (
    KLASS,
    Mutation,
    TargetCheck,
    check_target,
    request_of,
    verify,
)
from conductor.host_config import HostConfig
from conductor.journal import MutationLog, RunJournal
from conductor.markers import h1, parse_body, render
from conductor.opstate import OpState, parse_ts
from conductor.roadmap import Roadmap

WRITER_MARGIN = timedelta(minutes=65)
CONTINUE = frozenset({"success", "shadow"})


class Client(Protocol):
    """То, что исполнителю нужно от клиента App."""

    bot_login: str | None

    def check_key(self) -> str | None: ...

    def covers(self, repo: str) -> bool | None: ...

    def call(
        self,
        klass: str,
        method: str,
        path: str,
        *,
        auth: Any,
        body: dict | None = None,
        graphql: bool = False,
    ) -> CallResult: ...


@dataclass(frozen=True)
class Step:
    """Шаг записи: мутация и ключ события (ключ маркера или имя операции).

    revision — значимая ревизия шага (§5.4), если она уже записи: вопросы
    одной очереди — разные мутации со своими сериями. optional — сбой или
    неопределённость шага не блокируют следующие (закрепление очереди, §6.1).
    authority — уровень позиции шага, если он не уровень записи (вопрос
    очереди о своём субъекте).
    """

    mutation: Mutation
    event_key: str
    revision: str = ""
    optional: bool = False
    authority: Callable[[Roadmap, int], int] | None = field(default=None, compare=False)


def _valid(m: Mutation) -> str | None:
    return None


def _run_level(roadmap: Roadmap, run_level: int) -> int:
    return run_level


@dataclass(frozen=True)
class PlanRecord:
    """Запись плана (О §5.0): действие над субъектом одной ревизии.

    level — уровень, которого требует действие (все действия среза — 1).
    authority(roadmap, run_level) — уровень позиции по СВЕЖЕМУ роадмапу
    (О §2.4: класс, фокус, `focus.autonomy`); по умолчанию — потолок прогона.
    revalidate(m) — шаг 8 §5.1: свежее чтение основания перед мутацией m;
    None — основание в силе, иначе причина снятия.
    """

    action: str
    subject: str
    revision: str
    level: int
    steps: tuple[Step, ...]
    revalidate: Callable[[Mutation], str | None] = field(default=_valid, compare=False)
    authority: Callable[[Roadmap, int], int] = field(default=_run_level, compare=False)


@dataclass(frozen=True)
class StepReport:
    """Исход шага для журнала и выдачи."""

    action: str
    subject: str
    op: str
    target: str
    outcome: str
    reason: str = ""
    admissible: bool | None = None
    allowed: bool | None = None


class StopPoint(Exception):
    """Именованная точка остановки профиля acceptance (§9.2)."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


def mutation_id(rec: PlanRecord, step: Step, m: Mutation) -> str:
    """Ключ попытки, учёта и серии: с действием, субъектом и ревизией."""
    return h1(
        "mutation",
        rec.action,
        rec.subject,
        step.revision or rec.revision,
        m.op,
        m.target(),
        step.event_key,
    )


def effect_key(step: Step, m: Mutation) -> str:
    """Ключ задержки повтора: только то, что останется в GitHub (§5.4)."""
    return h1("effect", m.op, m.target(), step.event_key)


def expected_of(m: Mutation) -> str:
    """Идентичность эффекта для поиска после обрыва (§5.3): маркер
    комментария или sha256 тела; для закрытия и закрепления — состояние."""
    if m.op == "comment":
        marker = parse_body(m.text)
        return render(marker) if marker is not None else ""
    if m.op in ("body", "create"):
        return hashlib.sha256(m.text.encode("utf-8")).hexdigest()
    return m.op


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Writer:
    """Проводит записи плана через §5.1 и пишет исходы."""

    def __init__(
        self,
        *,
        cfg: HostConfig,
        client: Client,
        state: OpState,
        log: MutationLog,
        journal: RunJournal,
        fence: frozenset[str],
        load_roadmap: Callable[[], Roadmap | None],
        hostname: str,
        run_id: str,
        level_cap: int = 0,
        partial: bool = False,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._cfg = cfg
        self._client = client
        self._state = state
        self._log = log
        self._journal = journal
        self._fence = fence
        self._load_roadmap = load_roadmap
        self._hostname = hostname
        self._run_id = run_id
        self._level_cap = level_cap
        self._partial = partial
        self._clock = clock
        self.sent = 0
        self.stopped: str | None = None
        self.revoked: set[str] = set()
        self.created: dict[str, int] = {}

    def execute(self, records: list[PlanRecord]) -> list[StepReport]:
        """Все записи плана; StopPoint пробрасывается (имитация обрыва)."""
        reports: list[StepReport] = []
        for rec in records:
            reports += self._record(rec)
        self._state.end_run(self._clock())
        self._log.end_run()
        return reports

    def _record(self, rec: PlanRecord) -> list[StepReport]:
        out: list[StepReport] = []
        blocked_by = ""
        for step in rec.steps:
            if self.stopped:
                rep = self._rep(rec, step.mutation, "revoked_all", self.stopped)
            elif blocked_by:
                rep = self._rep(rec, step.mutation, "skipped_dependent", blocked_by)
            elif rec.action in self.revoked:
                rep = self._rep(rec, step.mutation, "revoked_action", "enabled_actions")
            else:
                rep = self._step(rec, step)
                if rep.outcome not in CONTINUE and not step.optional:
                    blocked_by = rep.outcome
            self._journal.write(asdict(rep))
            out.append(rep)
        return out

    def _permission(self, rec: PlanRecord, step: Step, roadmap: Roadmap) -> str | None:
        """Шаг 6 по свежему роадмапу: имя первого нарушенного условия."""
        if self._partial:
            return "partial"
        if self._level_cap < rec.level:  # --level, --roadmap, --replay → 0
            return "run_level"
        run_level = min(self._level_cap, roadmap.autonomy)
        if run_level < rec.level:
            return "autonomy"
        if rec.action not in roadmap.enabled_actions:
            return "enabled_actions"
        if (step.authority or rec.authority)(roadmap, run_level) < rec.level:
            return "position_level"
        return None

    def _step(self, rec: PlanRecord, step: Step) -> StepReport:
        m = self._resolve(step.mutation)
        if m is None:
            return self._rep(
                rec, step.mutation, "skipped_dependent", "нет созданного объекта"
            )
        now = self._clock()
        if m.repo not in self._fence:  # 1 — до покрытия
            return self._rep(rec, m, "fence", "FENCE", admissible=False)
        if self._state.quarantined(now):  # 2
            return self._stop_all(rec, m, "карантин")
        roadmap = self._load_roadmap()  # 3
        if roadmap is None or not roadmap.valid:
            return self._stop_all(rec, m, "роадмап не прочитан или RM-INVALID")
        if (  # 4
            roadmap.writer_host != self._hostname
            or now < parse_ts(roadmap.writer_since) + WRITER_MARGIN
        ):
            return self._stop_all(rec, m, "не управляющий писатель")
        denied: str | None = None
        try:
            if self._client.check_key() is None:  # 5
                return self._stop_all(rec, m, "ключ App не подтверждён")
            denied = self._permission(rec, step, roadmap)  # 6
            if denied is not None and not self._cfg.shadow:
                if denied == "enabled_actions":
                    self.revoked.add(rec.action)
                    return self._rep(rec, m, "revoked_action", denied)
                if denied == "position_level":  # только эта запись
                    return self._rep(rec, m, "revoked_action", denied)
                return self._stop_all(rec, m, denied)
            covered = self._client.covers(m.repo)  # 7
            if covered is not True:
                reason = (
                    "ID-REPO-UNCOVERED" if covered is False else "покрытие не прочитано"
                )
                return self._rep(rec, m, "not_sent", reason, admissible=False)
            changed = rec.revalidate(m)  # 8
            if changed is not None:
                return self._rep(rec, m, "removed", changed, admissible=False)
            target = check_target(self._client, m)
        except (Blocked, JournalLost) as exc:  # 8a
            return self._stop_all(rec, m, f"запрет или журнал установки: {exc}")
        if not target.ok:
            return self._rep(rec, m, "removed", target.reason, admissible=False)
        mid, eff = mutation_id(rec, step, m), effect_key(step, m)
        if self._state.delayed(eff, now):  # 9
            self._state.series_event(mid, "delay", self._run_id, now)
            return self._rep(
                rec,
                m,
                "delay",
                "неопределённая попытка моложе 60 мин",
                admissible=False,
            )
        if self.sent >= roadmap.limits["max_writes_per_run"]:  # 10
            return self._stop_all(rec, m, "RUN-BUDGET")
        if self._cfg.shadow:
            return self._rep(
                rec, m, "shadow", denied or "", admissible=True, allowed=denied is None
            )
        return self._send(rec, step, m, target, mid, eff)

    def _send(
        self,
        rec: PlanRecord,
        step: Step,
        m: Mutation,
        target: TargetCheck,
        mid: str,
        eff: str,
    ) -> StepReport:
        if m.op == "close":
            self._stop_point("before_close")
        method, path, body, graphql = request_of(m, target.node_id)
        attempt_id = uuid.uuid4().hex
        try:
            self._state.begin_attempt(
                attempt_id=attempt_id,
                mutation_id=mid,
                effect_key=eff,
                target=m.target(),
                marker_key=step.event_key,
                action=rec.action,
                subject=rec.subject,
                now=self._clock(),
                op=m.op,
                expected=expected_of(m),
            )
            seq = self._log.intent(
                attempt_id=attempt_id,
                method=method,
                endpoint=path,
                repo=m.repo,
                marker_key=step.event_key,
                body=json.dumps(body).encode("utf-8"),
            )
        except OSError:
            return self._stop_all(rec, m, "OPSTATE-WRITE")
        self._stop_point("after_intent")
        try:
            result = self._client.call(
                KLASS[m.op], method, path, auth="token", body=body, graphql=graphql
            )
        except Blocked as exc:
            return self._abort(
                rec, m, attempt_id, seq, "not_sent", False, f"не отправлено: {exc}"
            )
        except JournalLost:
            self.sent += 1
            return self._abort(
                rec,
                m,
                attempt_id,
                seq,
                "uncertain",
                True,
                "журнал установки не записан",
            )
        self.sent += 1
        self._stop_point("after_send")
        return self._settle(rec, m, result, attempt_id, seq, mid, target.node_id)

    def _settle(
        self,
        rec: PlanRecord,
        m: Mutation,
        result: CallResult,
        attempt_id: str,
        seq: int,
        mid: str,
        node_id: str | None,
    ) -> StepReport:
        outcome, reason = result.outcome, ""
        if outcome == "ok":
            try:  # verify — независимое чтение цели (О §5.0 шаг 3)
                problem = verify(
                    self._client,
                    m,
                    result.response,
                    self._client.bot_login or "",
                    node_id,
                )
            except (Blocked, JournalLost) as exc:
                problem = f"контрольное чтение не выполнено: {exc}"
            if problem is not None:
                outcome, reason = "uncertain", problem
        if outcome == "moved":
            outcome, reason = "failed", "TARGET-MOVED"
        status = result.response.status if result.response is not None else None
        try:
            self._state.finish_attempt(attempt_id, outcome, self._clock())
            self._log.result(seq, sent=True, outcome=outcome, status=status)
            self._state.series_event(
                mid,
                outcome,
                self._run_id,
                self._clock(),
                error=reason or str(status),
                detail={"op": m.op, "target": m.target()},
            )
        except OSError:
            return self._stop_all(rec, m, "OPSTATE-WRITE")
        if outcome == "rate_limited":
            return self._stop_all(rec, m, "лимит")
        if outcome != "ok":
            return self._rep(rec, m, outcome, reason)
        if m.op == "create" and result.response is not None:
            self.created[m.repo] = result.response.json()["number"]
        report = self._rep(rec, m, "success")
        if m.op == "comment":
            self._stop_point("after_comment")
        return report

    def _abort(
        self,
        rec: PlanRecord,
        m: Mutation,
        attempt_id: str,
        seq: int,
        outcome: str,
        sent: bool,
        reason: str,
    ) -> StepReport:
        try:
            self._state.finish_attempt(attempt_id, outcome, self._clock())
            self._log.result(seq, sent=sent, outcome=outcome, status=None)
        except OSError:
            reason = f"{reason}; OPSTATE-WRITE"
        return self._stop_all(rec, m, reason)

    def _resolve(self, m: Mutation) -> Mutation | None:
        if m.number is not None or m.op == "create":
            return m
        number = self.created.get(m.repo)
        return replace(m, number=number) if number is not None else None

    def _stop_all(self, rec: PlanRecord, m: Mutation, reason: str) -> StepReport:
        self.stopped = reason
        return self._rep(rec, m, "revoked_all", reason, admissible=False)

    def _stop_point(self, name: str) -> None:
        if self._cfg.profile == "acceptance" and name in self._cfg.stop_points:
            self._journal.write({"stop_point": name})
            raise StopPoint(name)

    def _rep(
        self,
        rec: PlanRecord,
        m: Mutation,
        outcome: str,
        reason: str = "",
        admissible: bool | None = None,
        allowed: bool | None = None,
    ) -> StepReport:
        return StepReport(
            rec.action,
            rec.subject,
            m.op,
            m.target(),
            outcome,
            reason,
            admissible,
            allowed,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/conductor/test_writer.py -q`
Expected: PASS.

- [ ] **Step 5: Mutation check of the order**

По одному удалите (закомментируйте) каждую из проверок 1, 2, 3, 4, 5, 6, 7, 8, 8a, 9, 10 в `Writer._step`, каждое условие `_permission` (`run_level`, `min` с потолком, `position_level`), ветку `optional` в `_record` и подтверждение verify в `_settle` и прогоните `uv run pytest tests/conductor/test_writer.py tests/conductor/test_gh_write.py -q`; каждое удаление должно дать хотя бы один FAIL. Верните код. Если какое-то удаление проходит зелёным — добавьте тест, который его ловит, и повторите. Проверка шага 8 здесь — на механизм (`revalidate` вызывается перед каждым шагом); настоящие проверки основания удаляются по одной в части B (Task B9, шаг мутационной проверки).

- [ ] **Step 6: Commit**

```bash
git add conductor/writer.py tests/conductor/test_writer.py
git commit -m "conductor: исполнитель записей — порядок §5.1 и матрица §5.2 (срез 1)"
```

---

### Task A12: CLI — `init-state`, `run --config`, lock хоста и потолок уровня

**Files:**
- Modify: `conductor/host_config.py` (`umbrella_name`, `umbrella_repo`, `profile_fence`)
- Create: `conductor/actions/__init__.py`
- Modify: `conductor/__main__.py`
- Test: `tests/conductor/test_cli_writer.py`, дополнение `tests/conductor/test_host_config.py`

**Interfaces:**
- Consumes: всё из A1–A11.
- Produces: `umbrella_name(cfg: HostConfig | None) -> str` (каталог/имя зонтика профиля; в `acceptance` — песочница); `umbrella_repo(cfg: HostConfig, owner: str) -> str`; `profile_fence(cfg: HostConfig, owner: str, github_names: list[str]) -> frozenset[str]`; `conductor.actions.PlanContext(cfg, umbrella, bot_login, state)`; `conductor.actions.plan_records(result, inputs, ctx) -> list[PlanRecord]` (часть A: `[]`); в `conductor.__main__`: `host_lock(path) -> ContextManager[bool]`, `level_cap(args, inputs) -> int`; CLI `init-state --config P [--recover]`; `run ... --config P`; код выхода 5 — точка остановки.
- Поведение (ревью владельца 2026-10-01):
  - **P1-3 — lock хоста.** `run --config`, `init-state` и `init-state --recover` сами берут `flock -n` на `cfg.lock` (по умолчанию `/srv/conductor/state/conductor.lock`, один на все профили: они делят журнал установки). Занят → `run` пропускается целиком (код 0, ни снимка, ни записей — как `flock -n -E 0`), `init-state` — код 4. Внешний `flock` вокруг этих команд больше не нужен и не используется (drop-in B11), иначе процесс заблокировал бы сам себя вторым захватом того же файла.
  - **P1-2 — потолок прогона.** `level_cap` = `--level` (по умолчанию 0), но 0 при `--replay` (офлайн-входы никогда не источник живых мутаций) и при роадмапе не с origin (`--roadmap`). Его получает `Writer(level_cap=…)`; `run` оценивает ядро с тем же потолком. Таймер-писатель передаёт `--level 3` (О §7.3).
  - Роадмап перед каждым шагом читается с origin зонтика ПРОФИЛЯ (`umbrella_name(cfg)`): в `acceptance` — песочница (P2-4; чтения графа переводятся на зонтик профиля в B2/B9).

- [ ] **Step 1: Write the failing tests**

Применить к `tests/conductor/test_host_config.py`:

```diff
diff --git a/tests/conductor/test_host_config.py b/tests/conductor/test_host_config.py
index fa14a90..d9e7ccd 100644
--- a/tests/conductor/test_host_config.py
+++ b/tests/conductor/test_host_config.py
@@ -4,7 +4,14 @@ from pathlib import Path
 
 import pytest
 
-from conductor.host_config import LOCK_PATH, ConfigError, load_host_config
+from conductor.host_config import (
+    LOCK_PATH,
+    ConfigError,
+    load_host_config,
+    profile_fence,
+    umbrella_name,
+    umbrella_repo,
+)
 
 FLEET = """
 [app]
@@ -91,3 +98,23 @@ def test_lock_shared_by_profiles_and_outside_state(tmp_path: Path) -> None:
     assert not LOCK_PATH.is_relative_to(fleet.state_dir)
     custom = _load(tmp_path, FLEET.replace("[run]", '[run]\nlock = "/tmp/c.lock"'))
     assert custom.lock == Path("/tmp/c.lock")
+
+
+def test_fence_and_umbrella(tmp_path: Path) -> None:
+    fleet, acc = _load(tmp_path, FLEET), _load(tmp_path, ACCEPTANCE)
+    assert umbrella_repo(fleet, "own") == "own/ai-orchestrators-workspace"
+    assert umbrella_repo(acc, "own") == "own/conductor-sandbox"
+    assert profile_fence(fleet, "own", ["devtools", "prograph-vault"]) == {
+        "own/devtools",
+        "own/prograph-vault",
+        "own/ai-orchestrators-workspace",
+    }
+    assert profile_fence(acc, "own", ["conductor-sandbox-outside"]) == {
+        "own/conductor-sandbox"
+    }
+
+
+def test_umbrella_name_of_profile(tmp_path: Path) -> None:
+    assert umbrella_name(_load(tmp_path, FLEET)) == "ai-orchestrators-workspace"
+    assert umbrella_name(_load(tmp_path, ACCEPTANCE)) == "conductor-sandbox"
+    assert umbrella_name(None) == "ai-orchestrators-workspace"
```

Создать `tests/conductor/test_cli_writer.py` (в том числе регрессии P1-3 — конкурирующий процесс держит lock; P1-2 — `--replay` с `--level 3` не пишет, двойник с потолком 3 пишет; P2-5 — `init-state` нового профиля не стирает потерю общего журнала):

```python
"""CLI: init-state и run --config (срез 1, часть A: пустой план)."""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import conductor.__main__ as cli
from conductor.app_calls import AppCalls, init_host, journal_path
from conductor.gh_write import Mutation
from conductor.inputs import save_inputs
from conductor.opstate import init_state
from conductor.roadmap import parse_roadmap
from conductor.writer import PlanRecord, Step
from tests.conductor.fake_app import FakeClient
from tests.conductor.fixtures import EPICS, ROADMAP, inputs

CONFIG = """
[app]
app_id = 11
installation_id = 22
private_key = "/k.pem"
[run]
profile = "fleet"
shadow = {shadow}
state_dir = "{state}"
lock = "{lock}"
"""


def _config(tmp_path: Path, name: str = "state", shadow: bool = True) -> str:
    return CONFIG.format(
        state=tmp_path / name,
        lock=tmp_path / "lock" / "conductor.lock",
        shadow=str(shadow).lower(),
    )


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cli, "SHARED_DIR", tmp_path / "shared")
    client = FakeClient()
    monkeypatch.setattr(cli, "AppClient", lambda cfg, calls: client)
    cfg = tmp_path / "c.toml"
    cfg.write_text(_config(tmp_path), encoding="utf-8")
    rep = tmp_path / "inputs.json"
    save_inputs(inputs({"a": "- [ ] x @owner:TBD @id:x\n"}), rep)
    return tmp_path, cfg, rep


def _snap(out: Path) -> dict:
    run_dir = next(out.iterdir())
    return json.loads((run_dir / "snapshot.json").read_text(encoding="utf-8"))


def test_init_state_once(env) -> None:
    tmp, cfg, _ = env
    assert cli.main(["init-state", "--config", str(cfg)]) == 0
    assert (tmp / "state" / "INIT").is_file()
    assert (tmp / "shared" / "HOST_INIT").is_file()
    assert cli.main(["init-state", "--config", str(cfg)]) == 4  # не пуст


def test_recover_after_lost_init(env) -> None:
    tmp, cfg, _ = env
    assert cli.main(["init-state", "--config", str(cfg)]) == 0
    (tmp / "state" / "INIT").unlink()
    assert cli.main(["init-state", "--config", str(cfg), "--recover"]) == 0
    assert (tmp / "state" / "corrupt").is_dir()


def test_run_with_config_empty_plan(env) -> None:
    tmp, cfg, rep = env
    assert cli.main(["init-state", "--config", str(cfg)]) == 0
    out = tmp / "out"
    code = cli.main(
        ["run", "--replay", str(rep), "--out", str(out), "--config", str(cfg)]
    )
    assert code == 0
    snap = _snap(out)
    assert snap["writer"]["is_writer"] is True and snap["actions"]["journal"] == []


def test_run_without_init_is_degraded_but_snapshot_written(env) -> None:
    tmp, cfg, rep = env
    out = tmp / "out"
    code = cli.main(
        ["run", "--replay", str(rep), "--out", str(out), "--config", str(cfg)]
    )
    assert code == 4
    snap = _snap(out)
    assert snap["writer"]["is_writer"] is False
    assert "OPSTATE-UNINITIALIZED" in snap["writer"]["reason"]


def test_run_with_bad_config(env) -> None:
    tmp, cfg, rep = env
    cfg.write_text("x = [", encoding="utf-8")
    out = tmp / "out"
    code = cli.main(
        ["run", "--replay", str(rep), "--out", str(out), "--config", str(cfg)]
    )
    assert code == 4 and "CFG-INVALID" in _snap(out)["writer"]["reason"]


def test_init_state_requires_config() -> None:
    assert cli.main(["init-state"]) == 2


def _hold_lock(tmp_path: Path) -> subprocess.Popen:
    """Другой процесс держит lock хоста (как таймер во время ручного запуска)."""
    lock = tmp_path / "lock" / "conductor.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    code = (
        "import fcntl, sys\n"
        f"h = open({str(lock)!r}, 'a')\n"
        "fcntl.flock(h.fileno(), fcntl.LOCK_EX)\n"
        "print('held', flush=True)\n"
        "sys.stdin.read()\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None and proc.stdout.readline().strip() == "held"
    return proc


def test_run_and_init_state_take_host_lock(env) -> None:
    """Регрессия P1-3: ручной run и init-state не идут при занятом lock."""
    tmp, cfg, rep = env
    holder = _hold_lock(tmp)
    try:
        assert cli.main(["init-state", "--config", str(cfg)]) == 4
        assert not (tmp / "state").exists()
        out = tmp / "out"
        argv = ["run", "--replay", str(rep), "--out", str(out), "--config", str(cfg)]
        assert cli.main(argv) == 0
        assert not out.exists()  # прогон пропущен целиком: ни снимка, ни записей
        assert cli.main(["init-state", "--config", str(cfg), "--recover"]) == 4
    finally:
        holder.communicate("")
    assert cli.main(["init-state", "--config", str(cfg)]) == 0


def test_lock_is_released_after_run(env) -> None:
    tmp, cfg, rep = env
    assert cli.main(["init-state", "--config", str(cfg)]) == 0
    out = tmp / "out"
    argv = ["run", "--replay", str(rep), "--out", str(out), "--config", str(cfg)]
    assert cli.main(argv) == 0
    with cli.host_lock(tmp / "lock" / "conductor.lock") as held:
        assert held


def _live_world(env, monkeypatch, shadow: bool = False):
    """Состояние без карантина, роадмап разрешает nudge, хост — писатель."""
    tmp, cfg, rep = env
    cfg.write_text(_config(tmp, shadow=shadow), encoding="utf-8")
    past = datetime.now(UTC) - timedelta(hours=3)
    init_state(tmp / "state", past)
    init_host(tmp / "shared", 11, 22, past)
    text = ROADMAP.replace("autonomy = 0", 'autonomy = 1\nenabled_actions = ["nudge"]')
    monkeypatch.setattr(cli, "_origin_roadmap", lambda *a: parse_roadmap(text, EPICS))
    monkeypatch.setattr(cli.socket, "gethostname", lambda: "vps")
    step = Step(Mutation("comment", "own/a", 1, text="x"), "k")
    record = PlanRecord("nudge", "own/a#1", "r", 1, (step,))
    monkeypatch.setattr(cli, "plan_records", lambda *a: [record])
    return tmp, cfg, rep


def test_replay_never_writes_live(env, monkeypatch) -> None:
    """Регрессия P1-2: --replay — потолок 0, даже с --level 3 и не в тени."""
    tmp, cfg, rep = _live_world(env, monkeypatch)
    out = tmp / "out"
    argv = ["run", "--replay", str(rep), "--out", str(out), "--config", str(cfg)]
    cli.main([*argv, "--level", "3"])
    snap = _snap(out)
    assert snap["writer"]["level_cap"] == 0
    assert [j["reason"] for j in snap["actions"]["journal"]] == ["run_level"]
    assert cli.AppClient(None, None).sent == []


def test_live_cap_twin_writes(env, monkeypatch) -> None:
    """Двойник: тот же мир с потолком 3 пишет — запрет выше дал именно потолок."""
    tmp, cfg, rep = _live_world(env, monkeypatch)
    monkeypatch.setattr(cli, "level_cap", lambda args, inputs: 3)
    out = tmp / "out"
    argv = ["run", "--replay", str(rep), "--out", str(out), "--config", str(cfg)]
    assert cli.main(argv) == 0
    assert [j["outcome"] for j in _snap(out)["actions"]["journal"]] == ["success"]
    assert len(cli.AppClient(None, None).sent) == 1


def test_level_cap_rules() -> None:
    args = cli._parser().parse_args(["run", "--level", "3"])
    live = inputs({"a": ""})
    assert cli.level_cap(args, live) == 3
    local = inputs({"a": ""}, roadmap_source="file:/tmp/r.toml")
    assert cli.level_cap(args, local) == 0
    replay = cli._parser().parse_args(["run", "--level", "3", "--replay", "x"])
    assert cli.level_cap(replay, live) == 0
    default = cli._parser().parse_args(["run"])
    assert cli.level_cap(default, live) == 0


def test_init_state_of_new_profile_keeps_lost_ban(env) -> None:
    """Регрессия P2-5: потерянный общий журнал не «чистится» init-state."""
    tmp, cfg, _ = env
    assert cli.main(["init-state", "--config", str(cfg)]) == 0
    shared = tmp / "shared"
    calls, _ = AppCalls.open(shared, 11, 22, datetime.now(UTC))
    assert calls is not None
    seq = calls.begin("create", datetime.now(UTC))  # обрыв: begin без end
    assert calls.blocked_until() is not None and seq
    journal_path(shared, 11, 22).unlink()  # журнал утрачен
    acc = tmp / "acc.toml"
    acc.write_text(_config(tmp, name="acc-state"), encoding="utf-8")
    assert cli.main(["init-state", "--config", str(acc)]) == 0
    reopened, _ = AppCalls.open(shared, 11, 22, datetime.now(UTC))
    assert reopened is not None
    until = reopened.blocked_until()
    assert until is not None and until > datetime.now(UTC) + timedelta(minutes=55)
    assert [r["t"] for r in reopened.rows] == ["lost"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/conductor/test_cli_writer.py tests/conductor/test_host_config.py -q`
Expected: FAIL — `ImportError: cannot import name 'profile_fence'` и коды выхода 2 для `init-state`.

- [ ] **Step 3: Write minimal implementation**

Применить к `conductor/host_config.py`:

```diff
diff --git a/conductor/host_config.py b/conductor/host_config.py
index d5c2765..61aef59 100644
--- a/conductor/host_config.py
+++ b/conductor/host_config.py
@@ -12,6 +12,8 @@ from dataclasses import dataclass
 from pathlib import Path
 from typing import Any, Literal
 
+from conductor.manifest import UMBRELLA
+
 # state/ среза 0 занят runs/ и conductor.lock — init-state требует пустой каталог
 FLEET_STATE_DIR = Path("/srv/conductor/opstate")
 SHARED_DIR = Path("/srv/conductor/shared")
@@ -130,3 +132,31 @@ def load_host_config(path: Path) -> HostConfig:
         stop_points=frozenset(stops),
         **base,
     )
+
+
+def umbrella_name(cfg: HostConfig | None) -> str:
+    """Имя зонтика профиля (каталог клона и репо): в acceptance — песочница.
+
+    Мини-флот приёмки не читает настоящий зонтик: роадмап, эпики, манифест,
+    очередь и состав флота — из песочницы (§9.1).
+    """
+    if cfg is not None and cfg.profile == "acceptance" and cfg.sandbox:
+        return cfg.sandbox.split("/", 1)[1]
+    return UMBRELLA
+
+
+def umbrella_repo(cfg: HostConfig, owner: str) -> str:
+    """Репо очереди владельца: зонтик флота или песочница (§4.2, §9.1)."""
+    if cfg.profile == "acceptance" and cfg.sandbox:
+        return cfg.sandbox
+    return f"{owner}/{UMBRELLA}"
+
+
+def profile_fence(
+    cfg: HostConfig, owner: str, github_names: list[str]
+) -> frozenset[str]:
+    """Забор профиля (§5.1 шаг 1): fleet — репо манифеста ∪ зонтик;
+    acceptance — песочница."""
+    if cfg.profile == "acceptance":
+        return frozenset({cfg.sandbox}) if cfg.sandbox else frozenset()
+    return frozenset({f"{owner}/{n}" for n in github_names} | {f"{owner}/{UMBRELLA}"})
```

Создать `conductor/actions/__init__.py`:

```python
"""Планировщики действий среза 1 (часть B). Часть A: план пуст."""

from __future__ import annotations

from dataclasses import dataclass

from conductor.host_config import HostConfig
from conductor.inputs import Inputs
from conductor.opstate import OpState
from conductor.snapshot import Result
from conductor.writer import PlanRecord


@dataclass(frozen=True)
class PlanContext:
    """Что планировщикам нужно сверх результата ядра."""

    cfg: HostConfig
    umbrella: str
    bot_login: str
    state: OpState


def plan_records(result: Result, inputs: Inputs, ctx: PlanContext) -> list[PlanRecord]:
    """Записи плана прогона (часть B наполняет)."""
    return []
```

Применить к `conductor/__main__.py` (`init-state`, `host_lock`, `level_cap`, фаза записи `_write_phase`, `_config`/`_command` под lock; `_run` оценивает ядро с `level_cap`):

```diff
diff --git a/conductor/__main__.py b/conductor/__main__.py
index c4c3934..91df227 100644
--- a/conductor/__main__.py
+++ b/conductor/__main__.py
@@ -3,25 +3,50 @@
 from __future__ import annotations
 
 import argparse
+import fcntl
 import json
 import os
 import shutil
 import socket
 import sys
+from collections.abc import Iterator
+from contextlib import contextmanager
+from dataclasses import asdict
 from datetime import UTC, datetime
 from pathlib import Path
 from typing import Any
 
+from conductor.actions import PlanContext, plan_records
+from conductor.app_calls import AppCalls, init_host
 from conductor.collect import collect, read_manifest
+from conductor.gh_app import AppClient, Blocked, JournalLost
 from conductor.graph import canonical_id, normalizer
+from conductor.host_config import (
+    SHARED_DIR,
+    ConfigError,
+    HostConfig,
+    load_host_config,
+    profile_fence,
+    umbrella_name,
+    umbrella_repo,
+)
 from conductor.inputs import Inputs, RepoTodo, load_inputs, save_inputs
+from conductor.journal import MutationLog, RunJournal
+from conductor.opstate import (
+    StateError,
+    init_state,
+    open_state,
+    recover_state,
+)
 from conductor.render import render_plan, render_status, render_why
-from conductor.roadmap import parse_roadmap
-from conductor.snapshot import evaluate, to_snapshot
+from conductor.roadmap import Roadmap, parse_roadmap
+from conductor.snapshot import Result, evaluate, to_snapshot
 from conductor.sources_gh import run_gh
+from conductor.sources_git import fetch, read_file_at_origin
+from conductor.writer import StopPoint, Writer
 
-EXIT_OK, EXIT_ARGS, EXIT_NO_SOURCE, EXIT_CONFIG = 0, 2, 3, 4
-COMMANDS = ("status", "why", "plan", "run", "record")
+EXIT_OK, EXIT_ARGS, EXIT_NO_SOURCE, EXIT_CONFIG, EXIT_STOP = 0, 2, 3, 4, 5
+COMMANDS = ("status", "why", "plan", "run", "record", "init-state")
 # ежечасный таймер: неделя прогонов (~3 МБ каждый) — не растить диск общего VPS
 KEEP_RUNS = 168
 
@@ -36,6 +61,8 @@ def _parser() -> argparse.ArgumentParser:
     p.add_argument("--no-fetch", action="store_true")
     p.add_argument("--out", type=Path, default=Path("out/conductor"))
     p.add_argument("--level", type=int, choices=range(4), default=0)
+    p.add_argument("--config", type=Path)
+    p.add_argument("--recover", action="store_true")
     p.add_argument("command", nargs="?")
     p.add_argument("target", nargs="?")
     return p
@@ -104,20 +131,167 @@ def _write_atomic(path: Path, text: str) -> None:
     os.replace(tmp, path)
 
 
-def _run(args: argparse.Namespace, inputs: Inputs) -> int:
-    result = evaluate(inputs, 0)
+def _init_state(args: argparse.Namespace) -> int:
+    """init-state [--recover]: явная инициализация состояния (срез 1, §4.6).
+
+    Под тем же lock хоста, что и run: инициализация не пересекается с
+    прогоном таймера или ручным прогоном другого профиля.
+    """
+    if args.config is None:
+        return EXIT_ARGS
+    try:
+        cfg = load_host_config(args.config)
+    except ConfigError as exc:
+        print(f"CFG-INVALID: {exc}", file=sys.stderr)
+        return EXIT_CONFIG
+    with host_lock(cfg.lock) as held:
+        if not held:
+            print(f"init-state: занято ({cfg.lock})", file=sys.stderr)
+            return EXIT_CONFIG
+        now = datetime.now(UTC)
+        try:
+            (recover_state if args.recover else init_state)(cfg.state_dir, now)
+            init_host(SHARED_DIR, cfg.app_id, cfg.installation_id, now)
+        except (StateError, OSError) as exc:
+            print(f"init-state: {exc}", file=sys.stderr)
+            return EXIT_CONFIG
+    print(f"состояние {cfg.state_dir} готово; карантин записей 65 мин")
+    return EXIT_OK
+
+
+@contextmanager
+def host_lock(path: Path) -> Iterator[bool]:
+    """flock -n на lock хоста (§4.6, §8): True — взят, False — занят.
+
+    Все пути записи (run --config, init-state, --recover) берут его сами;
+    внешний `flock` вокруг них не нужен (он занял бы тот же файл).
+    """
+    path.parent.mkdir(parents=True, exist_ok=True)
+    with path.open("a") as handle:
+        try:
+            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
+        except BlockingIOError:
+            yield False
+            return
+        try:
+            yield True
+        finally:
+            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
+
+
+def _origin_roadmap(
+    root: Path, epics: dict[str, dict[str, Any]], umbrella_dir: str
+) -> Roadmap | None:
+    """Роадмап с origin зонтика профиля перед каждым шагом (§5.1 шаг 3)."""
+    umbrella = root / umbrella_dir
+    if fetch(umbrella) is not None:
+        return None
+    text, _, state, _ = read_file_at_origin(umbrella, "roadmap.toml")
+    return parse_roadmap(text, epics) if state == "read" else None
+
+
+def _start_checks(client: Any, umbrella: str) -> str | None:
+    """О §8.2 на старте: ключ, установка владельца, покрытие зонтика профиля."""
+    try:
+        if client.check_key() is None:
+            return "ID-MISMATCH: ключ App"
+        if not client.check_installation(umbrella.split("/")[0]):
+            return "ID-MISMATCH: установка"
+        if client.covers(umbrella) is not True:
+            return "ID-MISMATCH: зонтик не покрыт"
+    except (Blocked, JournalLost) as exc:
+        return f"App недоступен: {exc}"
+    return None
+
+
+def level_cap(args: argparse.Namespace, inputs: Inputs) -> int:
+    """Потолок прогона из CLI (О §2.4): `--level`; 0 при `--replay` (офлайн-
+    входы не источник живых мутаций) и при роадмапе не с origin (`--roadmap`)."""
+    if args.replay is not None or inputs.roadmap_source != "origin":
+        return 0
+    return args.level
+
+
+def _write_phase(
+    args: argparse.Namespace,
+    cfg: HostConfig,
+    result: Result,
+    inputs: Inputs,
+    run_dir: Path,
+    run_id: str,
+) -> tuple[int, dict[str, Any], list[dict[str, Any]]]:
+    """Фаза записи (срез 1): (код, блок writer снимка, журнал действий)."""
+    now = datetime.now(UTC)
+    opened = open_state(cfg.state_dir, now)
+    calls, rate_finding = AppCalls.open(
+        SHARED_DIR, cfg.app_id, cfg.installation_id, now
+    )
+    findings = [f for f in (opened.finding, rate_finding) if f]
+    if opened.state is None or calls is None:
+        return EXIT_CONFIG, {"is_writer": False, "reason": ", ".join(findings)}, []
+    client = AppClient(cfg, calls)
+    umbrella = umbrella_repo(cfg, inputs.owner)
+    if (problem := _start_checks(client, umbrella)) is not None:
+        opened.state.end_run(now)
+        reason = ", ".join([*findings, problem])
+        return EXIT_CONFIG, {"is_writer": False, "reason": reason}, []
+    umbrella_dir = umbrella_name(cfg)
+    writer = Writer(
+        cfg=cfg,
+        client=client,
+        state=opened.state,
+        log=MutationLog(run_dir),
+        journal=RunJournal(run_dir),
+        fence=profile_fence(cfg, inputs.owner, list(inputs.repo_names)),
+        load_roadmap=lambda: _origin_roadmap(args.root, inputs.epics, umbrella_dir),
+        hostname=socket.gethostname(),
+        run_id=run_id,
+        level_cap=level_cap(args, inputs),
+        partial=result.graph_state == "partial",
+    )
+    ctx = PlanContext(cfg, umbrella, client.bot_login or "", opened.state)
+    try:
+        reports = writer.execute(plan_records(result, inputs, ctx))
+    except StopPoint as stop:
+        block = {"is_writer": True, "reason": f"точка остановки {stop.name}"}
+        return EXIT_STOP, block, [{"stop_point": stop.name}]
+    block = {
+        "is_writer": True,
+        "reason": "тень" if cfg.shadow else "запись",
+        "level_cap": level_cap(args, inputs),
+        "findings": findings,
+    }
+    return (EXIT_CONFIG if findings else EXIT_OK), block, [asdict(r) for r in reports]
+
+
+def _run(
+    args: argparse.Namespace,
+    inputs: Inputs,
+    cfg: HostConfig | None,
+    cfg_error: str | None,
+) -> int:
+    result = evaluate(inputs, level_cap(args, inputs))
     run_id = inputs.captured_at.replace(":", "")
     snap = to_snapshot(result, inputs, run_id, _previous(args.out))
     run_dir = args.out / run_id
     run_dir.mkdir(parents=True, exist_ok=True)
     save_inputs(inputs, run_dir / "inputs.json")
+    code = EXIT_OK if result.roadmap.valid else EXIT_CONFIG
+    if cfg_error is not None:
+        snap["writer"] = {"is_writer": False, "reason": f"CFG-INVALID: {cfg_error}"}
+        code = EXIT_CONFIG
+    elif cfg is not None:
+        write_code, snap["writer"], snap["actions"]["journal"] = _write_phase(
+            args, cfg, result, inputs, run_dir, run_id
+        )
+        code = max(code, write_code)
     _write_atomic(
         run_dir / "snapshot.json", json.dumps(snap, ensure_ascii=False, indent=1)
     )
     for old in _runs(args.out)[:-KEEP_RUNS]:
         shutil.rmtree(old, ignore_errors=True)
     print(render_status(result))
-    return EXIT_OK if result.roadmap.valid else EXIT_CONFIG
+    return code
 
 
 def _selftest() -> int:
@@ -144,6 +318,16 @@ def _selftest() -> int:
     return EXIT_OK if ok else 1
 
 
+def _config(args: argparse.Namespace) -> tuple[HostConfig | None, str | None]:
+    """Конфиг хоста для run --config: (cfg, None) | (None, ошибка) | (None, None)."""
+    if args.config is None or args.command != "run":
+        return None, None
+    try:
+        return load_host_config(args.config), None
+    except ConfigError as exc:
+        return None, str(exc)
+
+
 def main(argv: list[str] | None = None) -> int:
     """Точка входа; коды выхода — §7.2 (rev 10)."""
     try:
@@ -152,10 +336,25 @@ def main(argv: list[str] | None = None) -> int:
         return EXIT_ARGS
     if args.selftest:
         return _selftest()
+    if args.command == "init-state":
+        return _init_state(args)
     if args.command not in COMMANDS or (
         args.command in ("why", "record") and not args.target
     ):
         return EXIT_ARGS
+    cfg, cfg_error = _config(args)
+    if cfg is None:
+        return _command(args, cfg, cfg_error)
+    with host_lock(cfg.lock) as held:  # §8: один писатель на хосте за раз
+        if not held:
+            print(f"run: занято ({cfg.lock}) — прогон пропущен", file=sys.stderr)
+            return EXIT_OK
+        return _command(args, cfg, cfg_error)
+
+
+def _command(
+    args: argparse.Namespace, cfg: HostConfig | None, cfg_error: str | None
+) -> int:
     inputs = _inputs(args)
     if inputs is None:
         return EXIT_NO_SOURCE
@@ -167,7 +366,7 @@ def main(argv: list[str] | None = None) -> int:
         valid = parse_roadmap(inputs.roadmap_text, inputs.epics).valid
         return EXIT_OK if valid else EXIT_CONFIG
     if args.command == "run":
-        return _run(args, inputs)
+        return _run(args, inputs, cfg, cfg_error)
     result = evaluate(inputs, args.level if args.command == "plan" else 0)
     if args.command == "status":
         print(render_status(result, repo=args.target))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/conductor -q`
Expected: PASS — новые тесты и весь прежний набор conductor (включая `test_cli.py`, `test_deploy.py`).

- [ ] **Step 5: Full suite, lint, types**

Run: `uv run pytest -q && uv run --group selfcheck ruff format conductor tests/conductor && uv run --group selfcheck ruff check --fix conductor tests/conductor && uv run --group selfcheck pyrefly check conductor tests/conductor`
Expected: pytest зелёный; ruff — замечаний нет; pyrefly — 0 ошибок в `conductor/` и `tests/conductor/`.

Примечание (проверено сборкой кода плана `tools/build_plan.py` в чистый клон devtools, 2026-10-01: база `57ad631`, повторно — на `179f9b8`): `tests/conductor` — 308 passed, pyrefly по `conductor tests/conductor` — 0 ошибок.

- [ ] **Step 6: Commit**

```bash
git add conductor/host_config.py conductor/actions/__init__.py conductor/__main__.py \
    tests/conductor/test_cli_writer.py tests/conductor/test_host_config.py
git commit -m "conductor: init-state и run --config под lock хоста, потолок --level (срез 1, часть A)"
```

---

## Что часть A доказывает и чего не делает

- Доказывает: на пустом плане и фейке — порядок проверок §5.1, матрицу исходов §5.2, полномочия по свежему роадмапу с потолком CLI и уровнем позиции, контрольное чтение эффекта, один процесс записи на хосте (lock), устойчивость состояния и журнала установки (потеря журнала не стирается `init-state`), запрет по лимиту по политике владельца, отсутствие перенаправлений на настоящем транспорте, отсутствие секретов в журналах.
- Не делает (часть B): планировщики пяти действий, идентичность фактов, дочитывания GitHub (комментарии с `id`/`updated_at`, timeline, `closedByPullRequestsReferences`), ответы и производный вопрос, эпизоды сбоев в очереди, показатели ступеней, деплой и песочница. Служба на VPS остаётся без `--config` — поведение среза 0 не меняется, пока владелец не включит фазу записи.
