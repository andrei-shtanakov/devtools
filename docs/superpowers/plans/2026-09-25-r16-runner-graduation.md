# R16 runner graduation — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** выпустить еженедельный раннер R16 из `_cowork_output/cadence/r16/run.py`
в devtools (`r16_runner.py`) с контрактом квитанции v1 и артефактами
развёртывания на VPS.

**Architecture:** перенос почти дословно (подход A спеки). Stdlib-скрипт в корне
репо; корни — из CLI/окружения (`Config`); зона цикла закреплена
(`Asia/Tbilisi`); перед аудитом клон vault приводится к опубликованной ветке и
это проверяется; `flock` держится весь запуск. Контракт —
`contracts/r16-receipt/v1/` (JSON Schema + README + примеры). Развёртывание —
`deploy/r16/` по образцу `robin-runtime/deploy/`.

**Tech Stack:** Python ≥3.11 stdlib (`zoneinfo`, `fcntl`, `tomllib` в
`setup.sh`), git CLI, gh CLI, systemd, bash; тесты — pytest, `jsonschema`
(только dev-группа).

**Spec:** `docs/superpowers/specs/2026-09-25-r16-runner-graduation-design.md`
(влит #397, `89b87c9`).

## Global Constraints

- runtime-код не читает и не импортирует `_cowork_output/` (спека §0 п.1).
- `r16_runner.py` — stdlib-only; `jsonschema` — только в группе `dev`
  (спека §4.2).
- Зона цикла — константа `CYCLE_TZ = "Asia/Tbilisi"`, не настройка окружения
  (спека §1.2 п.2).
- Обязательные значения: `--workspace`/`R16_WORKSPACE`,
  `--state-dir`/`R16_STATE_DIR`, `--gh-config-dir`/`R16_GH_CONFIG_DIR`,
  `--host-label`/`R16_HOST_LABEL`; CLI побеждает окружение; проверка до любого
  действия, отказ — exit 2 без квитанции (спека §1.2 п.1, §3.3).
- Квитанции — `<state-dir>/receipts/`, блокировка — `<state-dir>/r16.lock`
  (спека §1.2 п.1).
- Схема закрыта (`additionalProperties: false`); любое изменение формы — v2
  (спека §3.2).
- Команды гейта CI: `uv run --frozen pytest -q`,
  `uv run --frozen --group governance pytest tests/test_governance_steward_surface.py tests/test_governance_stale_adapter.py tests/test_governance_bundle_state.py -q`,
  `make plan-check-selftest`.
- Мутации — в worktree, `PYTHONDONTWRITEBYTECODE=1`.

## Review Focus

1. **Системная зона VPS — UTC.** `datetime.now(zone)` в `main` и
   `cycle_start` обязаны давать один и тот же цикл при любой `TZ` процесса;
   наивная `datetime` на входе `cycle_start` не должна молча трактоваться как
   системное время. Тест: Task 3, `test_cycle_is_tbilisi_under_utc_system_zone`
   и `test_naive_now_is_refused`.
2. **Перенесённые квитанции Mac без смещения.** `load_receipt`, `latest_cycle`,
   `last_issue` читают их как раньше; новая квитанция того же цикла пишется
   поверх с `attempt+1`. Тест: Task 3,
   `test_legacy_naive_receipt_carries_attempt`.
3. **Клон vault на не той ветке / с локальным коммитом / с неотслеживаемым
   файлом** — pull проходит, но аудит запускаться не должен. Тест: Task 4,
   параметризованный `test_sync_vault_refuses`.
4. **Второй запуск во время доставки** — не пишет ничего и не трогает issue.
   Тест: Task 2, `test_lock_is_held_through_delivery` и
   `test_concurrent_run_writes_nothing`.
5. **`--dry-run` без каталога квитанций / без профиля gh** — это конфигурация,
   exit 2 и до блокировки, а не traceback. Тест: Task 2,
   `test_missing_setting_exits_2_before_lock` (параметризован по всем четырём
   значениям и `hosts.yml`).

---

## Файловая структура

| файл | ответственность |
|---|---|
| `r16_runner.py` (create) | раннер: конфигурация, цикл, vault, аудит, доставка, квитанция |
| `tests/test_r16_runner.py` (create) | тесты раннера |
| `tests/test_r16_receipt_contract.py` (create) | схема против квитанций раннера и примеров |
| `contracts/r16-receipt/v1/schema.json`, `README.md`, `examples/*.json` (create) | контракт |
| `deploy/r16/setup.sh`, `r16-kb-freshness.service`, `.timer`, `env.example`, `README.md` (create) | развёртывание |
| `tests/test_r16_deploy.py` (create) | статические свойства юнитов и setup.sh |
| `pyproject.toml`, `uv.lock` (modify) | `jsonschema` в группе dev |
| `CLAUDE.md` (modify) | строка таблицы инструментов |
| `TODO.md` (modify) | пункт `r16-runner-graduation`: код влит, ждёт приёмки §4.4 |

Рабочее место: worktree `../.wt-dt-382c` на ветке `feat/r16-runner-graduation`
от `master`; после `git worktree add` — `uv sync --frozen`.

---

### Task 1: Перенос без ослабления

Дословная копия `run.py` и его 27 тестов, адаптированных только по импорту.
Доказывает, что последующие правки меряются от того же поведения.

**Files:**
- Create: `r16_runner.py` (копия `../_cowork_output/cadence/r16/run.py`)
- Create: `tests/test_r16_runner.py` (копия `../_cowork_output/cadence/r16/test_run.py`)

**Interfaces:**
- Produces: модуль `r16_runner` с теми же именами, что `run`.

- [ ] **Step 1: Скопировать файлы**

```bash
cp ../_cowork_output/cadence/r16/run.py r16_runner.py
cp ../_cowork_output/cadence/r16/test_run.py tests/test_r16_runner.py
chmod +x r16_runner.py
```

- [ ] **Step 2: Адаптировать импорт в тесте**

В `tests/test_r16_runner.py` заменить шапку:

```python
"""Tests for the R16 weekly runner (pure parts; gh and git stay at the edges).

Run: uv run --frozen pytest tests/test_r16_runner.py
"""

import json
from datetime import date, datetime
from pathlib import Path

import pytest

import r16_runner as run
```

(удалить `import sys`, `sys.path.insert(...)` и `import run  # noqa: E402`;
алиас `run` сохраняет тела тестов байт-в-байт).

- [ ] **Step 3: Прогнать — ожидается 27 passed**

Run: `uv run --frozen pytest tests/test_r16_runner.py -q`
Expected: `27 passed`

- [ ] **Step 4: Commit**

```bash
git add r16_runner.py tests/test_r16_runner.py
git commit -m "feat(r16): дословный перенос run.py и 27 тестов (#382, база)"
```

---

### Task 2: Конфигурация, блокировка, корни из `Config`

Убирает вычисление корней от `__file__` (`WORKSPACE`, `AUDIT`, `RECEIPTS`,
`REVIEW_PROFILE`) и вводит `Config`, `ConfigError`, `resolve_config`,
`open_lock`. Функции, которые читали глобалы, получают `cfg`/`receipts`
параметром.

**Files:**
- Modify: `r16_runner.py`
- Modify: `tests/test_r16_runner.py`

**Interfaces:**
- Produces:
  - `class ConfigError(Exception)`
  - `@dataclass(frozen=True) class Config(workspace: Path, state_dir: Path, gh_config_dir: Path, host_label: str)` со свойствами `vault`, `audit`, `receipts`, `lock` (все `Path`)
  - `SETTINGS: tuple[tuple[str, str], ...]` — `(attr, env_name)`
  - `resolve_config(cli: Mapping[str, object], env: Mapping[str, str]) -> Config`
  - `open_lock(path: Path) -> IO[str]`
  - `main(argv: list[str] | None = None) -> int`
  - `run_cycle(cfg: Config, now: datetime, dry_run: bool) -> int`
  - `load_receipt(receipts: Path, cid: str) -> dict | None`
  - `write_receipt(receipts: Path, record: dict) -> None`
  - `producer(cfg: Config) -> dict[str, str | None]`
  - `run_audit(cfg: Config) -> Audit`
  - `deliver(cfg: Config, found: Problems, today: date, known: int | None = None) -> Delivery`
  - `open_issue(cfg: Config, known: int | None = None) -> dict | None`
  - `ensure_label(cfg: Config) -> None`, `gh(cfg: Config, *args: str) -> str`, `gh_env(cfg: Config) -> dict[str, str]`

- [ ] **Step 1: Тестовый помощник и падающие тесты**

Добавить в `tests/test_r16_runner.py` после импортов:

```python
import fcntl
import os

ENV_NAMES = {
    "workspace": "R16_WORKSPACE",
    "state_dir": "R16_STATE_DIR",
    "gh_config_dir": "R16_GH_CONFIG_DIR",
    "host_label": "R16_HOST_LABEL",
}


def layout(root: Path) -> dict[str, str]:
    """A complete on-disk layout; returns CLI values for resolve_config."""
    audit = root / "ws" / "prograph-vault" / "scripts" / "kb_freshness.py"
    audit.parent.mkdir(parents=True)
    audit.write_text("# auditor stub\n")
    (root / "state" / "receipts").mkdir(parents=True)
    (root / "gh").mkdir()
    (root / "gh" / "hosts.yml").write_text("github.com: {}\n")
    return {
        "workspace": str(root / "ws"),
        "state_dir": str(root / "state"),
        "gh_config_dir": str(root / "gh"),
        "host_label": "vps-test",
    }


def cfg_for(root: Path) -> "run.Config":
    return run.resolve_config(layout(root), {})


def argv_for(values: dict[str, str]) -> list[str]:
    out: list[str] = []
    for attr, value in values.items():
        out += [f"--{attr.replace('_', '-')}", value]
    return out


def lock_is_held(path: Path) -> bool:
    """True when another open file description holds the flock on `path`."""
    with path.open("r") as probe:
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(probe, fcntl.LOCK_UN)
        return False


def test_cli_wins_over_environment(tmp_path: Path) -> None:
    values = layout(tmp_path)
    env = {name: "/nowhere" for name in ENV_NAMES.values()}
    cfg = run.resolve_config(values, env)
    assert cfg.workspace == Path(values["workspace"])
    assert cfg.receipts == Path(values["state_dir"]) / "receipts"
    assert cfg.lock == Path(values["state_dir"]) / "r16.lock"


def test_environment_fills_what_cli_leaves_out(tmp_path: Path) -> None:
    values = layout(tmp_path)
    env = {ENV_NAMES[k]: v for k, v in values.items()}
    assert run.resolve_config({}, env).host_label == "vps-test"


@pytest.mark.parametrize("missing", [*ENV_NAMES, "hosts.yml", "receipts", "auditor"])
def test_missing_setting_exits_2_before_lock(
    tmp_path: Path, missing: str, capsys: pytest.CaptureFixture[str]
) -> None:
    values = layout(tmp_path)
    if missing in ENV_NAMES:
        del values[missing]
    elif missing == "hosts.yml":
        (tmp_path / "gh" / "hosts.yml").unlink()
    elif missing == "receipts":
        (tmp_path / "state" / "receipts").rmdir()
    else:
        (tmp_path / "ws" / "prograph-vault" / "scripts" / "kb_freshness.py").unlink()
    assert run.main(["--dry-run", *argv_for(values)]) == 2
    assert not (tmp_path / "state" / "r16.lock").exists()
    assert "r16:" in capsys.readouterr().err


def test_config_check_does_not_touch_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Token validity is not a start-up check: no subprocess at all here."""
    def no_subprocess(*_a: object, **_k: object) -> None:
        raise AssertionError("resolve_config must stay local")

    monkeypatch.setattr(run.subprocess, "run", no_subprocess)
    cfg_for(tmp_path)


def test_lock_is_created_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "r16.lock"
    old = os.umask(0)
    try:
        with run.open_lock(path):
            pass
    finally:
        os.umask(old)
    assert path.stat().st_mode & 0o777 == 0o600
```

Тесты, где уже стоит `monkeypatch.setattr(run, "RECEIPTS", tmp_path)` и
`run.last_issue(tmp_path)`, переписываются под новые сигнатуры в Step 3.

- [ ] **Step 2: Запустить — ожидается падение**

Run: `uv run --frozen pytest tests/test_r16_runner.py -q`
Expected: FAIL — `AttributeError: module 'r16_runner' has no attribute 'resolve_config'`

- [ ] **Step 3: Реализация**

В `r16_runner.py`:

1. Удалить константы `WORKSPACE`, `AUDIT`, `RECEIPTS`, `REVIEW_PROFILE`.
   `HERE` оставить (им продюсер называет собственный репо). Добавить импорты
   `from collections.abc import Mapping` и `from typing import IO`.
2. Добавить после констант:

```python
VAULT_DIR = "prograph-vault"
AUDIT_REL = Path("scripts") / "kb_freshness.py"
SETTINGS = (
    ("workspace", "R16_WORKSPACE"),
    ("state_dir", "R16_STATE_DIR"),
    ("gh_config_dir", "R16_GH_CONFIG_DIR"),
    ("host_label", "R16_HOST_LABEL"),
)


class ConfigError(Exception):
    """A setting is missing or wrong: exit 2, no receipt, no attempt spent."""


@dataclass(frozen=True)
class Config:
    """Where the runner reads and writes; every root comes from outside."""

    workspace: Path
    state_dir: Path
    gh_config_dir: Path
    host_label: str

    @property
    def vault(self) -> Path:
        return self.workspace / VAULT_DIR

    @property
    def audit(self) -> Path:
        return self.vault / AUDIT_REL

    @property
    def receipts(self) -> Path:
        return self.state_dir / "receipts"

    @property
    def lock(self) -> Path:
        return self.state_dir / "r16.lock"


def resolve_config(cli: Mapping[str, object], env: Mapping[str, str]) -> Config:
    """CLI over environment; every root checked locally before anything runs.

    Only presence is checked here — no network: a dead gh token is GitHub's
    state, not configuration, and surfaces as `delivery: failed`.
    """
    values: dict[str, str] = {}
    for attr, name in SETTINGS:
        value = cli.get(attr) or env.get(name)
        if not value:
            raise ConfigError(f"--{attr.replace('_', '-')} / ${name} не задан")
        values[attr] = str(value)
    cfg = Config(
        Path(values["workspace"]),
        Path(values["state_dir"]),
        Path(values["gh_config_dir"]),
        values["host_label"],
    )
    if not cfg.workspace.is_dir():
        raise ConfigError(f"нет каталога workspace {cfg.workspace}")
    if not cfg.receipts.is_dir():
        raise ConfigError(f"нет каталога квитанций {cfg.receipts}")
    if not cfg.audit.is_file():
        raise ConfigError(f"нет аудитора {cfg.audit}")
    hosts = cfg.gh_config_dir / "hosts.yml"
    if not (hosts.is_file() and os.access(hosts, os.R_OK)):
        raise ConfigError(f"профиль gh: нет читаемого {hosts}")
    return cfg


def open_lock(path: Path) -> IO[str]:
    """The lock file, opened without touching its mode (created 0600 if absent)."""
    return os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT, 0o600), "r+")
```

3. Заменить `main`:

```python
def main(argv: list[str] | None = None) -> int:
    """Run the current cycle if it still needs a run; write its receipt."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="no receipt, no issue")
    for attr, name in SETTINGS:
        parser.add_argument(
            f"--{attr.replace('_', '-')}", dest=attr, help=f"falls back to ${name}"
        )
    args = parser.parse_args(argv)
    try:
        cfg = resolve_config(vars(args), os.environ)
    except ConfigError as exc:
        print(f"r16: {exc}", file=sys.stderr)
        return 2
    # The lock spans the whole run — vault, audit, GitHub delivery, receipt:
    # decide() guards sequential re-runs, only the lock guards concurrent ones.
    with open_lock(cfg.lock) as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another run holds the lock")
            return 0
        return run_cycle(cfg, datetime.now().replace(microsecond=0), args.dry_run)
```

4. Протянуть `cfg`/`receipts` (тела не меняются, кроме источника путей):

```python
def run_cycle(cfg: Config, now: datetime, dry_run: bool) -> int:
    """Decide, mark missed cycles, audit, deliver, write the receipt."""
    cid = cycle_start(now).date().isoformat()
    existing = load_receipt(cfg.receipts, cid)
    action = "run" if dry_run else decide(existing)
    if action != "run":
        print(f"cycle {cid}: {action}")
        return 0
    if not dry_run:
        for missed in missed_cycles(latest_cycle(cfg.receipts, before=cid), cid):
            write_receipt(cfg.receipts, missed_receipt(missed, now.isoformat()))
    audit = run_audit(cfg)
    found = problems(audit)
    if not audit.completed:
        delivery = Delivery("skipped", None, "audit did not complete")
    elif dry_run:
        delivery = Delivery("dry-run")
    else:
        delivery = deliver(cfg, found, now.date(), last_issue(cfg.receipts))
    record = receipt(audit, found, delivery, now.isoformat())
    attempt = (existing or {}).get("attempt", 0) + 1
    record |= {"cycle_id": cid, "attempt": attempt, "producer": producer(cfg)}
    print(json.dumps(record, ensure_ascii=False))
    if dry_run:  # a trial run must not look like this cycle's receipt
        return 0 if audit.completed else 1
    write_receipt(cfg.receipts, record)
    return 0 if record["ok"] else 1


def load_receipt(receipts: Path, cid: str) -> dict | None:
    """This cycle's receipt, if any."""
    try:
        return json.loads((receipts / f"{cid}.json").read_text())
    except (FileNotFoundError, ValueError):
        return None


def write_receipt(receipts: Path, record: dict) -> None:
    """<receipts>/<cycle_id>.json, replaced atomically in the same directory."""
    path = receipts / f"{record['cycle_id']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def producer(cfg: Config) -> dict[str, str | None]:
    """SHAs of the code that produced the receipt (runner and auditor)."""
    return {
        "runner": head_sha(HERE, Path(__file__).resolve()),
        "auditor": head_sha(cfg.vault, cfg.audit),
    }


def run_audit(cfg: Config) -> Audit:
    """Run the published-target audit and parse what it printed."""
    out = subprocess.run(
        [
            "uv", "run", str(cfg.audit), "--json", "--target", "published",
            "--workspace", str(cfg.workspace),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return parse_audit(out.stdout, out.stderr)
```

`deliver`, `open_issue`, `ensure_label`, `gh`, `gh_env` получают первым
параметром `cfg: Config` и передают его дальше; `gh_env`:

```python
def gh_env(cfg: Config) -> dict[str, str]:
    """Environment that points gh at the ai-prosto profile."""
    return os.environ | {"GH_CONFIG_DIR": str(cfg.gh_config_dir)}
```

- [ ] **Step 4: Перевести сквозной тест и добавить тесты блокировки**

Заменить `test_run_cycle_marks_missed_cycles_and_caps_attempts` целиком:

```python
def test_run_cycle_marks_missed_cycles_and_caps_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end over the cycle logic, with audit and delivery stubbed."""
    cfg = cfg_for(tmp_path)
    monkeypatch.setattr(run, "producer", lambda _cfg: {"runner": "r", "auditor": "a"})
    monkeypatch.setattr(run, "deliver", lambda *_: run.Delivery("not-needed"))
    (cfg.receipts / "2026-09-01.json").write_text(json.dumps({"ok": True, "attempt": 1}))
    broken = run.parse_audit("boom\n")
    monkeypatch.setattr(run, "run_audit", lambda _cfg: broken)
    now = datetime(2026, 9, 23, 17, 0)

    codes = [run.run_cycle(cfg, now, dry_run=False) for _ in range(4)]

    assert codes == [1, 1, 1, 0]  # three failed attempts, then gave-up quietly
    current = json.loads((cfg.receipts / "2026-09-22.json").read_text())
    assert (current["attempt"], current["execution"], current["ok"]) == (
        3,
        "failed",
        False,
    )
    for cid in ("2026-09-08", "2026-09-15"):
        missed = json.loads((cfg.receipts / f"{cid}.json").read_text())
        assert (missed["execution"], missed["ok"]) == ("missed", False)
```

И добавить:

```python
def test_lock_is_held_through_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit and GitHub delivery both run under the lock, not after it."""
    values = layout(tmp_path)
    lock = tmp_path / "state" / "r16.lock"
    seen: list[str] = []

    def audit(_cfg: "run.Config") -> "run.Audit":
        seen.append(f"audit:{lock_is_held(lock)}")
        return run.parse_audit(json.dumps({"summary": {"unchanged": 1, "with_evidence": 1}}))

    def deliver(*_a: object) -> "run.Delivery":
        seen.append(f"deliver:{lock_is_held(lock)}")
        return run.Delivery("not-needed")

    monkeypatch.setattr(run, "run_audit", audit)
    monkeypatch.setattr(run, "deliver", deliver)
    monkeypatch.setattr(run, "producer", lambda _cfg: {"runner": "r", "auditor": "a"})
    assert run.main(argv_for(values)) == 0
    assert seen == ["audit:True", "deliver:True"]


def test_concurrent_run_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = layout(tmp_path)
    receipts = tmp_path / "state" / "receipts"
    (receipts / "2026-09-15.json").write_text('{"ok": true, "attempt": 1}\n')
    before = {p.name: p.stat().st_mtime_ns for p in receipts.iterdir()}

    def forbidden(*_a: object) -> None:
        raise AssertionError("a second run must not reach the audit")

    monkeypatch.setattr(run, "run_audit", forbidden)
    with run.open_lock(tmp_path / "state" / "r16.lock") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert run.main(argv_for(values)) == 0
    assert {p.name: p.stat().st_mtime_ns for p in receipts.iterdir()} == before
```

Тесты `test_last_issue_*` не меняются (`last_issue(receipts)` уже принимает
каталог).

- [ ] **Step 5: Прогнать**

Run: `uv run --frozen pytest tests/test_r16_runner.py -q`
Expected: all passed; число сверить с выводом и записать в сообщение
коммита (правило «числа называют своё дерево»).

- [ ] **Step 6: Commit**

```bash
git add r16_runner.py tests/test_r16_runner.py
git commit -m "feat(r16): корни из Config (CLI > env), отказ exit 2 до блокировки, lock на весь запуск"
```

---

### Task 3: Зона цикла `Asia/Tbilisi`

**Files:**
- Modify: `r16_runner.py`
- Modify: `tests/test_r16_runner.py`

**Interfaces:**
- Consumes: `ConfigError`, `main`, `run_cycle` (Task 2).
- Produces:
  - `CYCLE_TZ: str = "Asia/Tbilisi"`
  - `cycle_zone() -> ZoneInfo` (raises `ConfigError`)
  - `cycle_start(now: datetime) -> datetime` — принимает только aware `now`
  - `receipt(audit, found, delivery, run_at: str, finished_at: str) -> dict`

- [ ] **Step 1: Падающие тесты**

Добавить в тест:

```python
import time
from datetime import timezone
from zoneinfo import ZoneInfo

TBILISI = ZoneInfo("Asia/Tbilisi")


@pytest.fixture
def utc_system(monkeypatch: pytest.MonkeyPatch):
    """The VPS case: process zone is UTC."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.mark.parametrize(
    ("utc", "start"),
    [
        (datetime(2026, 9, 22, 5, 30, tzinfo=timezone.utc), "2026-09-22T09:30:00+04:00"),
        (datetime(2026, 9, 22, 5, 29, tzinfo=timezone.utc), "2026-09-15T09:30:00+04:00"),
    ],
)
def test_cycle_is_tbilisi_under_utc_system_zone(
    utc_system: None, utc: datetime, start: str
) -> None:
    assert run.cycle_start(utc).isoformat() == start


def test_naive_now_is_refused() -> None:
    with pytest.raises(ValueError, match="aware"):
        run.cycle_start(datetime(2026, 9, 22, 9, 30))


def test_missing_zone_exits_2_before_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = layout(tmp_path)
    monkeypatch.setattr(run, "CYCLE_TZ", "Nowhere/Nothing")
    assert run.main(["--dry-run", *argv_for(values)]) == 2
    assert not (tmp_path / "state" / "r16.lock").exists()


def test_receipt_timestamps_carry_the_offset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = cfg_for(tmp_path)
    monkeypatch.setattr(run, "producer", lambda _cfg: {"runner": "r", "auditor": "a"})
    monkeypatch.setattr(run, "deliver", lambda *_: run.Delivery("not-needed"))
    monkeypatch.setattr(
        run, "run_audit",
        lambda _cfg: run.parse_audit(json.dumps({"summary": {"unchanged": 1, "with_evidence": 1}})),
    )
    run.run_cycle(cfg, datetime(2026, 9, 23, 17, 0, tzinfo=TBILISI), dry_run=False)
    record = json.loads((cfg.receipts / "2026-09-22.json").read_text())
    assert record["started_at"].endswith("+04:00")
    assert record["finished_at"].endswith("+04:00")


def test_legacy_naive_receipt_carries_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A receipt copied from the Mac (no offset) still counts its attempts."""
    cfg = cfg_for(tmp_path)
    (cfg.receipts / "2026-09-22.json").write_text(json.dumps({
        "cycle_id": "2026-09-22", "attempt": 2, "ok": False,
        "started_at": "2026-09-22T10:00:00", "execution": "failed",
    }))
    monkeypatch.setattr(run, "producer", lambda _cfg: {"runner": "r", "auditor": "a"})
    monkeypatch.setattr(run, "run_audit", lambda _cfg: run.parse_audit("boom\n"))
    run.run_cycle(cfg, datetime(2026, 9, 23, 17, 0, tzinfo=TBILISI), dry_run=False)
    record = json.loads((cfg.receipts / "2026-09-22.json").read_text())
    assert record["attempt"] == 3
```

Существующие `test_cycle_starts_on_tuesday_morning` и сквозные тесты
переводятся на aware-время: в параметрах и в `now = datetime(2026, 9, 23, 17, 0)`
добавить `tzinfo=TBILISI`; в `test_cycle_starts_on_tuesday_morning` ожидаемые
значения — тоже с `tzinfo=TBILISI`. Тесты `run.receipt(...)` получают пятый
аргумент `"2026-09-29T09:31:00+04:00"`.

- [ ] **Step 2: Запустить — ожидается падение**

Run: `uv run --frozen pytest tests/test_r16_runner.py -q`
Expected: FAIL (`AttributeError: ... cycle_zone`, наивное время не отвергается).

- [ ] **Step 3: Реализация**

```python
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CYCLE_TZ = "Asia/Tbilisi"  # fixed by the owner 2026-09-25 — not an env setting


def cycle_zone() -> ZoneInfo:
    """The cycle's zone; a host without tzdata is a configuration error."""
    try:
        return ZoneInfo(CYCLE_TZ)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(
            f"зона цикла {CYCLE_TZ} не найдена — поставьте tzdata"
        ) from exc


def cycle_start(now: datetime) -> datetime:
    """Start of the cycle `now` falls in: the latest Tuesday 09:30 Tbilisi not after it.

    A naive `now` is refused: on a UTC host it would silently shift Tuesday.
    """
    if now.tzinfo is None:
        raise ValueError("cycle_start needs an aware datetime")
    now = now.astimezone(cycle_zone())
    days = (now.weekday() - CYCLE_WEEKDAY) % 7
    start = datetime.combine(
        now.date() - timedelta(days=days), CYCLE_START, tzinfo=now.tzinfo
    )
    return start if start <= now else start - timedelta(days=7)
```

В `main` внутри `try` после `resolve_config`: `zone = cycle_zone()`; вызов
цикла — `run_cycle(cfg, datetime.now(zone).replace(microsecond=0), args.dry_run)`.

В `run_cycle` перед `record = receipt(...)`:

```python
    finished = datetime.now(now.tzinfo).replace(microsecond=0)
    record = receipt(audit, found, delivery, now.isoformat(), finished.isoformat())
```

`receipt` получает параметр `finished_at: str` и пишет его вместо
`datetime.now()...`. Докстринг модуля: «цикл — вторник 09:30 Asia/Tbilisi».

- [ ] **Step 4: Прогнать**

Run: `uv run --frozen pytest tests/test_r16_runner.py -q`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add r16_runner.py tests/test_r16_runner.py
git commit -m "feat(r16): цикл по Asia/Tbilisi при любой зоне хоста; метки со смещением"
```

---

### Task 4: Клон vault приводится к опубликованной ветке

**Files:**
- Modify: `r16_runner.py`
- Modify: `tests/test_r16_runner.py`

**Interfaces:**
- Consumes: `Config.vault`, `run_cycle` (Task 2–3).
- Produces: `git_out(repo: Path, *args: str) -> str | None`,
  `sync_vault(vault: Path) -> str | None` (None — опубликовано и чисто, иначе
  причина).

- [ ] **Step 1: Падающие тесты на настоящих git-репо**

```python
import subprocess as sp

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}


def git(repo: Path, *args: str) -> str:
    return sp.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True,
        text=True, env=os.environ | GIT_ENV,
    ).stdout


@pytest.fixture
def published(tmp_path: Path) -> tuple[Path, Path]:
    """(origin bare repo, clone on its default branch `main`, clean)."""
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "-b", "main")
    (seed / "note.md").write_text("v1\n")
    git(seed, "add", ".")
    git(seed, "commit", "-qm", "v1")
    origin = tmp_path / "origin.git"
    sp.run(["git", "clone", "-q", "--bare", str(seed), str(origin)], check=True)
    clone = tmp_path / "clone"
    sp.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    return origin, clone


def advance_origin(tmp_path: Path, origin: Path) -> None:
    work = tmp_path / "pusher"
    sp.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    (work / "note.md").write_text("v2\n")
    git(work, "commit", "-qam", "v2")
    git(work, "push", "-q", "origin", "main")


def test_sync_vault_fast_forwards_to_origin(
    tmp_path: Path, published: tuple[Path, Path]
) -> None:
    origin, clone = published
    advance_origin(tmp_path, origin)
    assert run.sync_vault(clone) is None
    assert (clone / "note.md").read_text() == "v2\n"


@pytest.mark.parametrize(
    "case", ["unreachable", "wrong-branch", "ahead", "tracked-edit", "untracked"]
)
def test_sync_vault_refuses(
    tmp_path: Path, published: tuple[Path, Path], case: str
) -> None:
    origin, clone = published
    if case == "unreachable":
        git(clone, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    elif case == "wrong-branch":
        git(clone, "checkout", "-qb", "feature")
    elif case == "ahead":
        (clone / "note.md").write_text("local\n")
        git(clone, "commit", "-qam", "local")
    elif case == "tracked-edit":
        (clone / "note.md").write_text("edited\n")
    else:
        (clone / "stray.md").write_text("not published\n")
    assert run.sync_vault(clone) is not None


def test_unpublished_vault_fails_the_attempt_without_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = cfg_for(tmp_path)
    monkeypatch.setattr(run, "sync_vault", lambda _v: "HEAD ahead of origin/main")

    def forbidden(_cfg: object) -> None:
        raise AssertionError("audit must not run over an unpublished vault")

    monkeypatch.setattr(run, "run_audit", forbidden)
    monkeypatch.setattr(run, "producer", lambda _cfg: {"runner": "r", "auditor": "a"})
    code = run.run_cycle(cfg, datetime(2026, 9, 23, 17, 0, tzinfo=TBILISI), dry_run=False)
    record = json.loads((cfg.receipts / "2026-09-22.json").read_text())
    assert code == 1
    assert (record["execution"], record["delivery"]["action"]) == ("failed", "skipped")
    assert "HEAD ahead of origin/main" in record["audit_tail"]
```

Во все прежние сквозные тесты (`test_run_cycle_marks_missed_…`,
`test_lock_is_held_through_delivery`, `test_receipt_timestamps_…`,
`test_legacy_naive_receipt_…`) добавить
`monkeypatch.setattr(run, "sync_vault", lambda _v: None)`.

- [ ] **Step 2: Запустить — ожидается падение**

Run: `uv run --frozen pytest tests/test_r16_runner.py -q -k vault`
Expected: FAIL — `AttributeError: ... sync_vault`.

- [ ] **Step 3: Реализация**

```python
DEFAULT_BRANCH = re.compile(r"^ref: refs/heads/(\S+)\tHEAD$", re.MULTILINE)


def git_out(repo: Path, *args: str) -> str | None:
    """stdout of a git command in `repo`, or None when it failed."""
    out = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    return out.stdout if out.returncode == 0 else None


def sync_vault(vault: Path) -> str | None:
    """Bring the vault clone to origin's default branch and prove it got there.

    A successful `pull --ff-only` is not proof: local commits ahead of
    upstream and uncommitted or untracked files survive it, and the audit
    reads notes from disk. None means published and clean; otherwise the
    reason, and the audit must not run.
    """
    symref = git_out(vault, "ls-remote", "--symref", "origin", "HEAD")
    if symref is None:
        return "origin unreachable"
    match = DEFAULT_BRANCH.search(symref)
    if match is None:
        return "origin did not name its default branch"
    branch = match.group(1)
    current = (git_out(vault, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    if current != branch:
        return f"clone is on {current or '?'}, origin publishes {branch}"
    if git_out(vault, "pull", "--ff-only", "--quiet", "origin", branch) is None:
        return f"pull --ff-only origin {branch} failed"
    head = (git_out(vault, "rev-parse", "HEAD") or "").strip()
    remote = (git_out(vault, "rev-parse", f"refs/remotes/origin/{branch}") or "").strip()
    if not head or head != remote:
        return f"HEAD {head[:12]} is not origin/{branch} {remote[:12]}"
    dirty = git_out(vault, "status", "--porcelain", "--untracked-files=all")
    if dirty is None or dirty.strip():
        first = (dirty or "").strip().splitlines()[:3]
        return "working tree not clean: " + "; ".join(first)
    return None
```

В `run_cycle` заменить `audit = run_audit(cfg)` на:

```python
    unpublished = sync_vault(cfg.vault)
    audit = (
        Audit([], [], None, f"vault not published: {unpublished}")
        if unpublished
        else run_audit(cfg)
    )
```

(`--dry-run` проходит тот же путь — спека §1.2 п.3.)

- [ ] **Step 4: Прогнать**

Run: `uv run --frozen pytest tests/test_r16_runner.py -q`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add r16_runner.py tests/test_r16_runner.py
git commit -m "feat(r16): vault приводится к опубликованной ветке и это проверяется до аудита"
```

---

### Task 5: `producer.host` и указатель в issue

**Files:**
- Modify: `r16_runner.py`
- Modify: `tests/test_r16_runner.py`

**Interfaces:**
- Consumes: `Config.host_label`, `Config.receipts`.
- Produces:
  - `producer(cfg) -> {"runner", "auditor", "host"}`
  - `receipt_pointer(cfg: Config, cid: str) -> str`
  - `deliver(cfg, found, today, known, pointer: str) -> Delivery`
  - `issue_body(found, first, today, pointer: str) -> str`

- [ ] **Step 1: Падающие тесты**

```python
def test_producer_names_the_host(tmp_path: Path) -> None:
    assert run.producer(cfg_for(tmp_path))["host"] == "vps-test"


def test_issue_points_at_the_receipt_on_the_host(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    pointer = run.receipt_pointer(cfg, "2026-09-29")
    assert pointer == f"vps-test:{tmp_path / 'state' / 'receipts' / '2026-09-29.json'}"
    body = run.issue_body(run.Problems([verdict("changed")], [], []), TODAY, TODAY, pointer)
    assert body.rstrip().endswith(f"`{pointer}`.")
    assert "_cowork_output" not in body
```

Три существующих вызова `run.issue_body(...)` получают четвёртый аргумент
`"host:/p.json"`.

- [ ] **Step 2: Запустить — ожидается падение**

Run: `uv run --frozen pytest tests/test_r16_runner.py -q -k "host or points"`
Expected: FAIL.

- [ ] **Step 3: Реализация**

```python
def receipt_pointer(cfg: Config, cid: str) -> str:
    """Where the operator finds this cycle's receipt — a pointer, not a public link."""
    return f"{cfg.host_label}:{cfg.receipts / f'{cid}.json'}"
```

`producer` добавляет `"host": cfg.host_label`. `issue_body(found, first, today,
pointer)`: последняя строка —
`parts += ["", f"Квитанции прогонов: `{pointer}`."]` вместо строки про
`_cowork_output`. `deliver(cfg, found, today, known, pointer)` передаёт
`pointer` в `issue_body`; в `run_cycle` вызов —
`deliver(cfg, found, now.date(), last_issue(cfg.receipts), receipt_pointer(cfg, cid))`.
Докстринг модуля — без упоминаний `_cowork_output` и launchd, со ссылкой на
спеку.

- [ ] **Step 4: Прогнать и проверить отсутствие `_cowork_output` в runtime-коде**

Run: `uv run --frozen pytest tests/test_r16_runner.py -q && ! grep -n "_cowork_output" r16_runner.py`
Expected: all passed; grep ничего не находит.

- [ ] **Step 5: Commit**

```bash
git add r16_runner.py tests/test_r16_runner.py
git commit -m "feat(r16): producer.host и указатель <host>:<путь> на квитанцию в issue"
```

---

### Task 6: Контракт квитанции v1

**Files:**
- Create: `contracts/r16-receipt/v1/schema.json`, `README.md`,
  `examples/completed-ok.json`, `examples/failed.json`, `examples/missed.json`
- Create: `tests/test_r16_receipt_contract.py`
- Modify: `pyproject.toml` (`dev = ["pytest>=8", "jsonschema>=4"]`), `uv.lock`

**Interfaces:**
- Consumes: `run_cycle`, `missed_receipt` (Task 2–5).
- Produces: `contracts/r16-receipt/v1/schema.json` — то, что вендорит Robin.

- [ ] **Step 1: Зависимость**

```bash
uv add --dev "jsonschema>=4"
```

- [ ] **Step 2: Падающий тест контракта**

```python
"""The receipt contract (contracts/r16-receipt/v1) against what the runner writes."""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import jsonschema
import pytest

import r16_runner as run
from tests.test_r16_runner import cfg_for

V1 = Path(__file__).resolve().parents[1] / "contracts" / "r16-receipt" / "v1"
SCHEMA = json.loads((V1 / "schema.json").read_text())
TBILISI = ZoneInfo("Asia/Tbilisi")


def validate(record: dict) -> None:
    jsonschema.Draft202012Validator(SCHEMA).validate(record)


@pytest.mark.parametrize("name", ["completed-ok", "failed", "missed"])
def test_examples_are_valid(name: str) -> None:
    validate(json.loads((V1 / "examples" / f"{name}.json").read_text()))


@pytest.mark.parametrize(
    ("audit_out", "delivery"),
    [
        ('{"summary": {"unchanged": 1, "with_evidence": 1, "notes": 3}}', "not-needed"),
        ("boom", None),
    ],
)
def test_runner_receipts_are_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_out: str, delivery: str | None
) -> None:
    cfg = cfg_for(tmp_path)
    (cfg.receipts / "2026-09-01.json").write_text('{"ok": true, "attempt": 1}')
    monkeypatch.setattr(run, "sync_vault", lambda _v: None)
    monkeypatch.setattr(run, "run_audit", lambda _cfg: run.parse_audit(audit_out))
    monkeypatch.setattr(run, "deliver", lambda *_: run.Delivery(delivery or "x"))
    monkeypatch.setattr(run, "head_sha", lambda *_: "a" * 40)
    run.run_cycle(cfg, datetime(2026, 9, 23, 17, 0, tzinfo=TBILISI), dry_run=False)
    for path in sorted(cfg.receipts.glob("2026-09-[0-9][0-9].json")):
        if path.stem != "2026-09-01":
            validate(json.loads(path.read_text()))


def test_unknown_field_is_rejected() -> None:
    record = json.loads((V1 / "examples" / "completed-ok.json").read_text())
    with pytest.raises(jsonschema.ValidationError):
        validate(record | {"extra": 1})


def test_unknown_execution_is_rejected() -> None:
    record = json.loads((V1 / "examples" / "failed.json").read_text())
    with pytest.raises(jsonschema.ValidationError):
        validate(record | {"execution": "partial"})


def test_producer_host_is_required() -> None:
    record = json.loads((V1 / "examples" / "completed-ok.json").read_text())
    record["producer"] = {"runner": "a" * 40, "auditor": "b" * 40}
    with pytest.raises(jsonschema.ValidationError):
        validate(record)
```

Run: `uv run --frozen pytest tests/test_r16_receipt_contract.py -q`
Expected: FAIL — нет `schema.json`.

- [ ] **Step 3: `schema.json`**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://github.com/andrei-shtanakov/devtools/contracts/r16-receipt/v1/schema.json",
  "title": "R16 receipt v1",
  "oneOf": [{"$ref": "#/$defs/executed"}, {"$ref": "#/$defs/missed"}],
  "$defs": {
    "cycleId": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
    "timestamp": {
      "type": "string",
      "pattern": "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}([+-]\\d{2}:\\d{2}|Z)?$"
    },
    "sha": {"type": ["string", "null"], "pattern": "^[0-9a-f]{7,40}(\\+dirty)?$"},
    "count": {"type": "integer", "minimum": 0},
    "delivery": {
      "type": "object",
      "additionalProperties": false,
      "required": ["action", "issue", "issue_url", "error"],
      "properties": {
        "action": {
          "enum": ["created", "updated", "closed", "not-needed", "skipped",
                   "failed", "not-run", "dry-run"]
        },
        "issue": {"type": ["integer", "null"]},
        "issue_url": {"type": ["string", "null"]},
        "error": {"type": ["string", "null"]}
      }
    },
    "executed": {
      "type": "object",
      "additionalProperties": false,
      "required": ["schema_version", "check_id", "cycle_id", "attempt", "started_at",
                   "finished_at", "target", "producer", "revisions", "coverage",
                   "statuses", "problems", "execution", "delivery", "audit_tail", "ok"],
      "properties": {
        "schema_version": {"const": 1},
        "check_id": {"const": "r16-kb-freshness"},
        "cycle_id": {"$ref": "#/$defs/cycleId"},
        "attempt": {"type": "integer", "minimum": 1, "maximum": 3},
        "started_at": {"$ref": "#/$defs/timestamp"},
        "finished_at": {"$ref": "#/$defs/timestamp"},
        "target": {"const": "published"},
        "producer": {
          "type": "object",
          "additionalProperties": false,
          "required": ["runner", "auditor", "host"],
          "properties": {
            "runner": {"$ref": "#/$defs/sha"},
            "auditor": {"$ref": "#/$defs/sha"},
            "host": {"type": "string", "minLength": 1}
          }
        },
        "revisions": {"type": "object", "additionalProperties": {"type": ["string", "null"]}},
        "coverage": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "notes": {"$ref": "#/$defs/count"},
            "with_evidence": {"$ref": "#/$defs/count"},
            "unparsed_frontmatter": {"$ref": "#/$defs/count"}
          }
        },
        "statuses": {"type": "object", "additionalProperties": {"$ref": "#/$defs/count"}},
        "problems": {
          "type": "object",
          "additionalProperties": false,
          "required": ["claims", "revisions", "coverage"],
          "properties": {
            "claims": {"$ref": "#/$defs/count"},
            "revisions": {"$ref": "#/$defs/count"},
            "coverage": {"$ref": "#/$defs/count"}
          }
        },
        "execution": {"enum": ["completed", "failed"]},
        "delivery": {"$ref": "#/$defs/delivery"},
        "audit_tail": {"type": ["string", "null"]},
        "ok": {"type": "boolean"}
      }
    },
    "missed": {
      "type": "object",
      "additionalProperties": false,
      "required": ["schema_version", "check_id", "cycle_id", "attempt", "noted_at",
                   "execution", "revisions", "delivery", "ok"],
      "properties": {
        "schema_version": {"const": 1},
        "check_id": {"const": "r16-kb-freshness"},
        "cycle_id": {"$ref": "#/$defs/cycleId"},
        "attempt": {"const": 0},
        "noted_at": {"$ref": "#/$defs/timestamp"},
        "execution": {"const": "missed"},
        "revisions": {"type": "object", "maxProperties": 0},
        "delivery": {"$ref": "#/$defs/delivery"},
        "ok": {"const": false}
      }
    }
  }
}
```

- [ ] **Step 4: Примеры**

`examples/completed-ok.json`:

```json
{
  "schema_version": 1,
  "check_id": "r16-kb-freshness",
  "cycle_id": "2026-09-29",
  "attempt": 1,
  "started_at": "2026-09-29T09:30:12+04:00",
  "finished_at": "2026-09-29T09:31:40+04:00",
  "target": "published",
  "producer": {
    "runner": "89b87c9f0000000000000000000000000000abcd",
    "auditor": "4170bc6f0000000000000000000000000000abcd",
    "host": "vps"
  },
  "revisions": {"steward": "a3f1c2d40000000000000000000000000000abcd"},
  "coverage": {"notes": 120, "with_evidence": 14, "unparsed_frontmatter": 0},
  "statuses": {"unchanged": 31, "changed": 0, "missing": 0, "unverified": 0, "invalid": 0},
  "problems": {"claims": 0, "revisions": 0, "coverage": 0},
  "execution": "completed",
  "delivery": {"action": "not-needed", "issue": null, "issue_url": null, "error": null},
  "audit_tail": null,
  "ok": true
}
```

`examples/failed.json`:

```json
{
  "schema_version": 1,
  "check_id": "r16-kb-freshness",
  "cycle_id": "2026-09-29",
  "attempt": 2,
  "started_at": "2026-09-29T10:30:03+04:00",
  "finished_at": "2026-09-29T10:30:05+04:00",
  "target": "published",
  "producer": {
    "runner": "89b87c9f0000000000000000000000000000abcd",
    "auditor": "4170bc6f0000000000000000000000000000abcd",
    "host": "vps"
  },
  "revisions": {},
  "coverage": {},
  "statuses": {},
  "problems": {"claims": 0, "revisions": 0, "coverage": 0},
  "execution": "failed",
  "delivery": {
    "action": "skipped",
    "issue": null,
    "issue_url": null,
    "error": "audit did not complete"
  },
  "audit_tail": "vault not published: HEAD 1a2b3c4d5e6f is not origin/main 6f5e4d3c2b1a",
  "ok": false
}
```

`examples/missed.json`:

```json
{
  "schema_version": 1,
  "check_id": "r16-kb-freshness",
  "cycle_id": "2026-09-15",
  "attempt": 0,
  "noted_at": "2026-09-23T17:00:00+04:00",
  "execution": "missed",
  "revisions": {},
  "delivery": {"action": "not-run", "issue": null, "issue_url": null, "error": null},
  "ok": false
}
```

- [ ] **Step 5: `README.md` контракта**

````markdown
# Квитанция R16, v1

Каждая попытка раннера `r16_runner.py` оставляет файл
`<state-dir>/receipts/<cycle_id>.json`, где `cycle_id` — дата вторника, с
которого начинается цикл (вторник 09:30 → следующий вторник 09:30,
`Asia/Tbilisi`). Форма — `schema.json` (JSON Schema 2020-12, закрытая);
примеры — `examples/`. Дизайн: `docs/superpowers/specs/2026-09-25-r16-runner-graduation-design.md`.

Две формы:
- **выполненная попытка** (`execution: completed | failed`) — с `producer`,
  `revisions`, `coverage`, `statuses`, `problems`, `audit_tail`;
- **пропуск** (`execution: missed`, `attempt: 0`) — цикл, который никто не
  запускал; данных о нём нет.

## Правила чтения

- Цикл закрыт = файл `<cycle_id>.json` с `ok: true`.
- `missed` означает «цикл никто не запускал», а не «прошёл без находок».
- `ok: false` при `attempt < 3` — цикл ещё может закрыться; при
  `attempt = 3` — раннер сдался до следующего цикла.
- `problems` > 0 при `ok: true` — нормальный исход: находки доставлены в
  issue `kb-freshness` в prograph-vault.
- `producer.host` — метка исполнителя, не SHA; по ней видно, что квитанции
  писал один исполнитель.
- Метка времени без смещения (квитанции, перенесённые с Mac до передачи)
  означает `Asia/Tbilisi`. Раннер devtools пишет только метки со смещением.
- `delivery.action: dry-run` в файлах не встречается: пробный прогон
  квитанцию не записывает.

## Эволюция

Схема закрыта, и потребители держат закреплённую копию. Поэтому **любое**
изменение формы, даже новое необязательное поле, — новая версия:
1. рядом с `v1/` появляется `v2/`; `v1/` не меняется;
2. потребитель вендорит `v2/` и принимает обе версии;
3. только после подтверждения потребителя раннер пишет `schema_version: 2`.

`schema_version` в квитанции всегда равен номеру каталога, по которому она
валидна.
````

- [ ] **Step 6: Прогнать**

Run: `uv run --frozen pytest tests/test_r16_receipt_contract.py tests/test_r16_runner.py -q`
Expected: all passed.

- [ ] **Step 7: Commit**

```bash
git add contracts/r16-receipt pyproject.toml uv.lock tests/test_r16_receipt_contract.py
git commit -m "feat(r16): контракт квитанции v1 — схема, правила чтения, примеры"
```

---

### Task 7: Артефакты развёртывания `deploy/r16/`

**Files:**
- Create: `deploy/r16/setup.sh`, `deploy/r16/r16-kb-freshness.service`,
  `deploy/r16/r16-kb-freshness.timer`, `deploy/r16/env.example`,
  `deploy/r16/README.md`
- Create: `tests/test_r16_deploy.py`

**Interfaces:**
- Consumes: CLI и переменные `r16_runner.py` (Task 2), таблица прав спеки §1.3.

- [ ] **Step 1: Падающий статический тест**

```python
"""Static properties of deploy/r16 that the spec relies on (§1.3, §2.2)."""

from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1] / "deploy" / "r16"


def test_service_runs_as_r16_with_umask_0027() -> None:
    unit = (DEPLOY / "r16-kb-freshness.service").read_text()
    for line in ("User=r16", "UMask=0027", "Type=oneshot",
                 "EnvironmentFile=/srv/r16/r16.env"):
        assert line in unit.splitlines(), line


def test_timer_is_hourly_and_persistent() -> None:
    timer = (DEPLOY / "r16-kb-freshness.timer").read_text().splitlines()
    assert "OnCalendar=hourly" in timer
    assert "Persistent=true" in timer


def test_setup_installs_but_does_not_enable_the_timer() -> None:
    """Switching executors is the handover (spec §2.2), not a setup side effect."""
    setup = (DEPLOY / "setup.sh").read_text()
    commands = [
        line.strip() for line in setup.splitlines()
        if line.strip() and not line.strip().startswith("#")
        and not line.strip().startswith("echo")
    ]
    assert "systemctl daemon-reload" in commands
    assert not [c for c in commands if c.startswith("systemctl") and "enable" in c]


def test_setup_sets_the_permission_table() -> None:
    setup = (DEPLOY / "setup.sh").read_text()
    for fragment in (
        'install -d -o r16 -g r16-readers -m 0710 "$R16_HOME"',
        'install -d -o r16 -g r16-readers -m 0710 "$R16_HOME/state"',
        'install -d -o r16 -g r16-readers -m 2750 "$R16_HOME/state/receipts"',
        'install -d -o r16 -g r16 -m 0700 "$R16_HOME/gh"',
        'install -o r16 -g r16 -m 0600 /dev/null "$R16_HOME/state/r16.lock"',
    ):
        assert fragment in setup, fragment


def test_env_example_names_every_setting() -> None:
    env = (DEPLOY / "env.example").read_text()
    for name in ("R16_WORKSPACE", "R16_STATE_DIR", "R16_GH_CONFIG_DIR", "R16_HOST_LABEL"):
        assert f"{name}=" in env
```

Run: `uv run --frozen pytest tests/test_r16_deploy.py -q`
Expected: FAIL — файлов нет.

- [ ] **Step 2: `r16-kb-freshness.service`**

```ini
[Unit]
Description=R16: weekly KB claim freshness (devtools r16_runner.py)
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=r16
Group=r16
UMask=0027
EnvironmentFile=/srv/r16/r16.env
WorkingDirectory=/srv/r16/devtools
ExecStart=/usr/local/bin/uv run --script /srv/r16/devtools/r16_runner.py
```

- [ ] **Step 3: `r16-kb-freshness.timer`**

```ini
[Unit]
Description=R16: hourly catch-up; the runner itself decides run/done/gave-up

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 4: `env.example`**

```bash
# /srv/r16/r16.env — EnvironmentFile of r16-kb-freshness.service (chmod 600).
# systemd does NOT strip inline comments: keep comments on their own lines.
R16_WORKSPACE=/srv/r16/workspace
R16_STATE_DIR=/srv/r16/state
R16_GH_CONFIG_DIR=/srv/r16/gh
# Name of this host as the operator knows it; goes into producer.host and the issue.
R16_HOST_LABEL=
```

- [ ] **Step 5: `setup.sh`**

```bash
#!/usr/bin/env bash
# One-time VPS bring-up for the R16 runner (Ubuntu/Debian). Idempotent.
# Run as root from a devtools checkout: sudo GIT_BASE=git@github.com:<org> deploy/r16/setup.sh
# Installs units but does NOT enable the timer: switching executors is the
# handover in deploy/r16/README.md (spec §2.2), never a side effect of setup.
set -euo pipefail

R16_HOME=/srv/r16
UNIT_DIR=/etc/systemd/system
GIT_BASE="${GIT_BASE:?set GIT_BASE, e.g. git@github.com:your-org}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "== packages =="
apt-get update -q
apt-get install -y -q git tzdata python3
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
command -v gh >/dev/null || { echo ">>> install gh (https://cli.github.com) and re-run"; exit 1; }

echo "== user, group =="
getent group r16-readers >/dev/null || groupadd --system r16-readers
id r16 &>/dev/null || useradd --system --home-dir "$R16_HOME" --shell /usr/sbin/nologin r16
if id robin &>/dev/null; then usermod -aG r16-readers robin; fi

echo "== layout and permissions (spec §1.3) =="
install -d -o r16 -g r16-readers -m 0710 "$R16_HOME"
install -d -o r16 -g r16 -m 0700 "$R16_HOME/devtools" "$R16_HOME/workspace"
install -d -o r16 -g r16-readers -m 0710 "$R16_HOME/state"
install -d -o r16 -g r16-readers -m 2750 "$R16_HOME/state/receipts"
install -d -o r16 -g r16 -m 0700 "$R16_HOME/gh"
[ -f "$R16_HOME/state/r16.lock" ] || install -o r16 -g r16 -m 0600 /dev/null "$R16_HOME/state/r16.lock"

echo "== code (updated by hand later: pull --ff-only) =="
[ -d "$R16_HOME/devtools/.git" ] || sudo -u r16 git clone -q "$GIT_BASE/devtools.git" "$R16_HOME/devtools"

echo "== workspace clones: canonical names from workspace-manifest.toml =="
WS="$R16_HOME/workspace"
[ -d "$WS/ai-orchestrators-workspace/.git" ] || sudo -u r16 git clone -q "$GIT_BASE/ai-orchestrators-workspace.git" "$WS/ai-orchestrators-workspace"
mapfile -t REPOS < <(python3 - "$WS/ai-orchestrators-workspace/workspace-manifest.toml" <<'PY'
import sys, tomllib
m = tomllib.load(open(sys.argv[1], "rb"))
dirs = {
    v["git_dir"]
    for sec in m.values() if isinstance(sec, dict)
    for v in sec.values()
    if isinstance(v, dict) and "git_dir" in v and "repo_url" in v and not v.get("member")
}
print("\n".join(sorted(dirs)))
PY
)
for repo in "${REPOS[@]}"; do
    [ -d "$WS/$repo/.git" ] || sudo -u r16 git clone -q "$GIT_BASE/$repo.git" "$WS/$repo"
done

echo "== env =="
if [ ! -f "$R16_HOME/r16.env" ]; then
    install -o r16 -g r16 -m 0600 "$HERE/env.example" "$R16_HOME/r16.env"
    echo ">>> EDIT $R16_HOME/r16.env (R16_HOST_LABEL) before the handover"
fi

echo "== systemd units (installed, NOT enabled) =="
cp "$HERE/r16-kb-freshness.service" "$HERE/r16-kb-freshness.timer" "$UNIT_DIR"/
systemctl daemon-reload
echo ">>> gh profile: put ai-prosto's hosts.yml into $R16_HOME/gh (0600, r16:r16)"
echo ">>> then follow deploy/r16/README.md: dry-run, handover steps 2-4"
```

Тест `test_setup_installs_but_does_not_enable_the_timer` смотрит только на
команды (не на комментарии и `echo`): ни одна `systemctl`-команда не содержит
`enable`.

- [ ] **Step 6: `README.md` развёртывания**

````markdown
# R16 runner на VPS

Спека: `docs/superpowers/specs/2026-09-25-r16-runner-graduation-design.md`.
Раскладка и права — §1.3; передача — §2.2; откат — §2.3.

## 1. Установка

```bash
sudo GIT_BASE=git@github.com:andrei-shtanakov deploy/r16/setup.sh
sudo -e /srv/r16/r16.env            # R16_HOST_LABEL=<имя VPS>
```

Пользователю `r16` нужен ssh-доступ на чтение к `GIT_BASE` (deploy key или
ключ машины). Профиль gh ai-prosto:

```bash
sudo install -o r16 -g r16 -m 0600 <hosts.yml ai-prosto> /srv/r16/gh/hosts.yml
```

`setup.sh` таймер **не включает**: смена исполнителя — только передачей (§3).

## 2. Проверка до передачи (§2.2 шаг 1)

```bash
sudo -u r16 bash -c 'set -a; . /srv/r16/r16.env; set +a;
  /usr/local/bin/uv run --script /srv/r16/devtools/r16_runner.py --dry-run'
sudo -u r16 GH_CONFIG_DIR=/srv/r16/gh gh auth status
sudo -u r16 GH_CONFIG_DIR=/srv/r16/gh gh issue list -R andrei-shtanakov/prograph-vault --label kb-freshness
```

Ожидается: пробный прогон печатает квитанцию с `"execution": "completed"` и
ничего не записывает в `/srv/r16/state/receipts/`; `gh auth status` называет
ai-prosto. Пробный прогон gh не вызывает — авторизацию проверяют две
последние команды.

## 3. Передача Mac → VPS (§2.2 шаги 2–5)

В каждый момент активен не больше чем один исполнитель.

На Mac (шаг 2):

```bash
launchctl bootout gui/$UID/dev.atp.r16-kb-freshness
rm ~/Library/LaunchAgents/dev.atp.r16-kb-freshness.plist
flock -n ~/labs/all_ai_orchestrators/_cowork_output/cadence/r16/receipts/.lock true && echo "прогон не идёт"
```

Тем же шагом в `_cowork_output/ops/r2-liveness-check.sh` выключить блок R16
(§2.4).

Снимок (шаг 3) — без прав Mac, затем права явно:

```bash
rsync -rt --no-perms --no-owner --no-group   ~/labs/all_ai_orchestrators/_cowork_output/cadence/r16/receipts/   <vps>:/tmp/r16-receipts/
# на VPS:
sudo rsync -rt /tmp/r16-receipts/ /srv/r16/state/receipts/
sudo chown -R r16:r16-readers /srv/r16/state/receipts
sudo find /srv/r16/state/receipts -type d -exec chmod 2750 {} +
sudo find /srv/r16/state/receipts -type f -exec chmod 0640 {} +
```

Сверка: `cd <receipts> && find . -type f -name '*.json' | sort | xargs shasum -a 256`
на Mac и `sha256sum` того же списка на VPS — выводы совпадают. Затем:

```bash
sudo -u robin cat /srv/r16/state/receipts/<последний>.json >/dev/null && echo ok
sudo -u robin cat /srv/r16/state/receipts/legacy/<файл>.json >/dev/null && echo ok
```

Включение (шаг 4) — право на issue переходит к VPS ровно здесь:

```bash
sudo systemctl enable --now r16-kb-freshness.timer
```

## 4. Откат (§2.3)

```bash
sudo systemctl disable --now r16-kb-freshness.timer
until [ "$(systemctl is-active r16-kb-freshness.service)" = inactive ]; do sleep 5; done
sudo -u r16 flock -n /srv/r16/state/r16.lock true && echo "блокировка свободна"
```

Только после обоих условий: квитанции обратно на Mac тем же способом (без
прав, со сверкой sha256), затем вернуть plist и
`launchctl bootstrap gui/$UID ~/Library/LaunchAgents/dev.atp.r16-kb-freshness.plist`.

## 5. Приёмка (§4.4)

1. Пробный прогон и `gh auth status` — раздел 2.
2. Права по таблице §1.3 — `stat -c '%U:%G %a %n'` каждой строки. От `robin`
   проходят `cat` квитанции верхнего уровня и из `legacy/`; отказывают
   `ls /srv/r16`, `ls /srv/r16/gh`, `cat /srv/r16/r16.env`,
   `cat /srv/r16/state/r16.lock`, `ls /srv/r16/workspace`.
3. Передача — раздел 3, со сверкой sha256.
4. Первая квитанция, записанная на VPS: `"host": "<R16_HOST_LABEL>"` в
   `producer`, группа `r16-readers`, режим 640.

## 6. Обновление кода

```bash
sudo -u r16 git -C /srv/r16/devtools pull --ff-only
```

Раннер сам себя не обновляет.

## 7. Названная дыра (§2.4)

До проверки ожидаемого цикла в Robin (заявка в robin-runtime) живость R16
автоматически не проверяет никто. Пропущенный цикл станет виден квитанцией
`missed` при следующем прогоне.
````

- [ ] **Step 7: Прогнать и проверить shell**

Run: `uv run --frozen pytest tests/test_r16_deploy.py -q && bash -n deploy/r16/setup.sh && (command -v shellcheck >/dev/null && shellcheck deploy/r16/setup.sh || echo "shellcheck не установлен — назвать в отчёте")`
Expected: passed; `bash -n` молчит.

- [ ] **Step 8: Commit**

```bash
chmod +x deploy/r16/setup.sh
git add deploy/r16 tests/test_r16_deploy.py
git commit -m "feat(r16): deploy/r16 — setup, systemd, env, runbook передачи и приёмки"
```

---

### Task 8: Мутации, документация, поставка

**Files:**
- Modify: `CLAUDE.md` (таблица «Инструменты»), `TODO.md` (пункт `r16-runner-graduation`)

- [ ] **Step 1: Мутации (в worktree, `PYTHONDONTWRITEBYTECODE=1`)**

Каждая мутация применяется к `r16_runner.py`, прогоняется
`uv run --frozen pytest tests/test_r16_runner.py tests/test_r16_receipt_contract.py -q`,
файл восстанавливается из копии; ожидаемый исход — хотя бы один FAIL:

| # | мутация | ловит |
|---|---|---|
| M1 | в `cycle_start` `now.astimezone(cycle_zone())` → `now.astimezone()` | `test_cycle_is_tbilisi_under_utc_system_zone` |
| M2 | `run_cycle`: `sync_vault` вызывается после `run_audit` (аудит всегда) | `test_unpublished_vault_fails_the_attempt_without_audit` |
| M3 | `sync_vault`: снята проверка `head != remote` | `test_sync_vault_refuses[ahead]` |
| M4 | `sync_vault`: `--untracked-files=all` → `--untracked-files=no` | `test_sync_vault_refuses[untracked]` |
| M5 | `sync_vault`: снята проверка `dirty` целиком | `[tracked-edit]` и `[untracked]` |
| M6 | `main`: `return run_cycle(...)` вынесен из-под `with open_lock` (lock отпущен) | `test_lock_is_held_through_delivery` |
| M7 | `run_cycle`: `attempt = 1` вместо переноса | `test_run_cycle_marks_missed_…`, `test_legacy_naive_receipt_…` |
| M8 | `producer`: без `"host"` | `test_producer_names_the_host`, контракт |
| M9 | `resolve_config`: `cli.get(attr) or env.get(name)` → `env.get(name) or cli.get(attr)` | `test_cli_wins_over_environment` |
| M10 | `cycle_zone`: `except` возвращает `ZoneInfo("UTC")` | `test_missing_zone_exits_2_before_lock` |

Результат — таблица «мутация → число упавших тестов» в описание PR.

- [ ] **Step 2: `CLAUDE.md` — строка в таблицу инструментов**

После строки `salvage_scan.py`:

```markdown
| `r16_runner.py` | еженедельная проверка свежести утверждений KB (R16, devtools#382): аудит `kb_freshness.py --target published` над клонами workspace, квитанция по контракту `contracts/r16-receipt/v1/`, issue `kb-freshness` в prograph-vault от ai-prosto. Исполняется на VPS (`deploy/r16/`), не локально; корни — `--workspace/--state-dir/--gh-config-dir/--host-label` или `R16_*`, зона цикла `Asia/Tbilisi` закреплена |
```

- [ ] **Step 3: `TODO.md`**

В теле пункта `r16-runner-graduation` дописать строку
`**Код влит:** PR <n> (<sha>); пункт закрывается после приёмки §4.4 владельцем`
(номер и SHA — после мержа, отдельным коммитом не нужен: пишется в этой же
ветке как «PR этой ветки», по конвенции файла). Чекбокс остаётся `[ ]`.

- [ ] **Step 4: Гейт**

Run:
```bash
uv run --frozen pytest -q
uv run --frozen --group governance pytest tests/test_governance_steward_surface.py tests/test_governance_stale_adapter.py tests/test_governance_bundle_state.py -q
make plan-check-selftest
```
Expected: всё зелёное; числа записать в PR с SHA головы.

- [ ] **Step 5: Commit, push, PR, ревью**

```bash
git add CLAUDE.md TODO.md
git commit -m "docs(r16): строка инструмента, пункт TODO — код влит, ждёт приёмки"
git push -u origin feat/r16-runner-graduation
```

PR — `feat(r16): раннер R16 в devtools + контракт квитанции v1 + deploy/r16 (#382)`;
ревью — штатный флоу (`--dry-run --write-verdict`, затем `--use-verdict`),
мерж — `merge-pr.sh` с `--expect-head`/`--expect-base` после зелёного CI.
Пункт `TODO.md` не закрывается до приёмки §4.4. После мержа — inbox-issue в
robin-runtime (спека §5 п.5): чтение квитанций по
`contracts/r16-receipt/v1/`, проверка ожидаемого цикла, Telegram.
