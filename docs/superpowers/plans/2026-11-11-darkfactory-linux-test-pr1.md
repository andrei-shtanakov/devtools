# DarkFactory на Linux — PR1: переносимый safe_io. План реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** режим `test` DarkFactory проходит авторинг на Linux: `safe_io` даёт ту же гарантию «ни одной ссылки в пути» без `O_NOFOLLOW_ANY`, и это доказано тестами, которые выполняются в Linux CI.

**Architecture:** единственная точка, зависящая от Darwin, — `safe_io._open`. При наличии `os.O_NOFOLLOW_ANY` она работает как сейчас; иначе — покомпонентный обход (`openat` с `O_NOFOLLOW` от `/` или от закреплённого `dir_fd`). Выбор — по `safe_io.NOFOLLOW_ANY is None`, что позволяет тестам принудительно включать обход и на macOS. Остальной код (`sandbox`, `author_launch`) не меняется: вызывающие уже канонизируют корни.

**Tech Stack:** Python 3.12+, `os.open(dir_fd=…)`, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-11-11-darkfactory-linux-test-design.md` (PR1 = разделы «Решения» 1–3; раздел 4 и прогон — PR2, вне этого плана).

## Global Constraints

- `governance/safe_io.py`, `tests/test_governance_safe_io*`, `tests/test_governance_sandbox*`, `governance/sandbox.py`, `governance/author_launch.py` — authority-root: **PR мержит владелец (D10), агент не мержит.**
- Ветка macOS (`O_NOFOLLOW_ANY`) — без изменения поведения.
- Ни одной новой проверки ОС (`sys.platform`, `uname`) в пути режима `test`; различия — только внутри `safe_io`, выбор реализации — по `NOFOLLOW_ANY is None`.
- Контракт `safe_io` сохраняется: только обычный файл, `st_nlink == 1`, `O_NONBLOCK` при чтении, потолок и таймаут чтения, публикация `O_EXCL`-временным файлом + `rename`, режим 0644 мимо umask, единственный тип отказа `SafeIOError`, дескрипторы закрываются на любом исходе.
- Прикладные тексты отказов одинаковы между реализациями: ссылка в пути → подстрока `ссылка в пути`; системный `strerror` внутри текста может отличаться.
- Компоненты `""`, `.`, `..` в любой части пути (включая корень) — отказ в покомпонентной реализации.
- Linux-ядро: `openat(…, O_DIRECTORY|O_NOFOLLOW)` на ссылке даёт `ENOTDIR`, без `O_DIRECTORY` — `ELOOP` (замер 6.16, 11.11). Отказ — на обоих; `fstatat(…, follow_symlinks=False)` после отказа выбирает только текст.
- Не трогать: `install.sh`, `docs/darkfactory-for-colleagues.md`, `README.md` (это PR2); песочницу strict (`sandbox-exec`, профиль, допуск).
- Каждый `subprocess.run` в `governance/` — с литеральной первой программой из git/gh/tmux/spec-runner/security (в этом плане новых нет).
- Тесты со steward: `PYTHONPATH=/Users/Andrei_Shtanakov/labs/all_ai_orchestrators/steward/src uv run pytest …`.

## Review Focus

1. Ссылка в **предке корня** (`/home → /var/home`): покомпонентный обход обязан отказать (не открыть корень обычным `open`) — тест в Task 1.
2. **Висячая ссылка в середине пути** при открытии каталога (Linux даёт `ENOTDIR`): отказ с текстом `ссылка в пути`, а не «не каталог» — тест в Task 1.
3. **FIFO на месте файла** в покомпонентной реализации: открытие не виснет (флаги вызывающего, включая `O_NONBLOCK`, доходят до последнего `openat`) — существующий тест, параметризованный в Task 1.
4. **Утечка дескрипторов** при отказе на середине обхода: число открытых fd до и после равно — тест в Task 1.
5. **HOME/XDG за ссылкой** у коллеги: авторинг в режиме `test` всё равно идёт, потому что вызывающие канонизируют корни (`calls_root`, `run_dir`, `target`) — тест в Task 3.

---

### Task 1: покомпонентная реализация `safe_io._open` и тесты обеих реализаций

**Files:**
- Modify: `governance/safe_io.py` (модульный docstring; `_flag` удалить; `_open`; новая `_open_walk`)
- Modify: `tests/test_governance_safe_io.py`

**Interfaces:**
- Consumes: —
- Produces: `safe_io.NOFOLLOW_ANY: int | None` (как сейчас; `None` → обход); `safe_io._open(path, flags, dir_fd=None, *, any_component=True, mode=0o777) -> int` — сигнатура без изменений; новая `safe_io._open_walk(path: str, flags: int, dir_fd: int | None, mode: int) -> int`. Публичные функции — без изменений сигнатур.

- [ ] **Step 1: Параметризовать тесты по реализации**

В `tests/test_governance_safe_io.py`: autouse-фикстура `impl`, параметризованная `["nofollow_any", "walk"]`. `nofollow_any` — `pytest.skip`, если `not hasattr(os, "O_NOFOLLOW_ANY")`; `walk` — `monkeypatch.setattr(safe_io, "NOFOLLOW_ANY", None)`. Снять `@darwin_only` со всех тестов файла (25 мест); маркер `darwin_only` удалить. Модульный docstring теста: «обе реализации; `walk` выполняется на любой ОС». Тест `test_without_nofollow_any_every_call_refuses` заменить:

```python
def test_walk_every_call_works(root: Path, impl: str) -> None:
    (root / "f").write_bytes(b"x")
    assert safe_io.read_regular(root, "f", limit=10) == b"x"
    assert [e.path for e in safe_io.list_tree(root, ".")] == ["f"]
    safe_io.ensure_dirs(root, "a/b")
    safe_io.create_dir(root, "c")
    safe_io.publish(root, "a/b/g", b"y")
    assert (root / "a" / "b" / "g").read_bytes() == b"y"
```

- [ ] **Step 2: Добавить тесты обхода (Review Focus 1, 2, 4)**

```python
def test_symlinked_ancestor_of_root_refuses(tmp_path: Path, impl: str) -> None:
    real = Path(os.path.realpath(tmp_path))
    (real / "home" / "u").mkdir(parents=True)
    (real / "home" / "u" / "f").write_bytes(b"x")
    (real / "link").symlink_to(real / "home")
    with pytest.raises(safe_io.SafeIOError, match="ссылка в пути"):
        safe_io.read_regular(real / "link" / "u", "f", limit=10)
    with pytest.raises(safe_io.SafeIOError, match="ссылка в пути"):
        safe_io.ensure_dirs(real / "link" / "u", "d")
    assert not (real / "home" / "u" / "d").exists()


def test_dangling_symlink_mid_path_refuses_as_link(root: Path, impl: str) -> None:
    (root / "gone").symlink_to(root / "nowhere")
    with pytest.raises(safe_io.SafeIOError, match="ссылка в пути"):
        safe_io.publish(root, "gone/f", b"x")
    with pytest.raises(safe_io.SafeIOError, match="ссылка в пути"):
        safe_io.list_tree(root, "gone")


def test_walk_refuses_dotdot_in_root(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `Path` сам схлопывает `.` и `//`; `..` он сохраняет — его и проверяем
    monkeypatch.setattr(safe_io, "NOFOLLOW_ANY", None)
    (root / "a").mkdir()
    with pytest.raises(safe_io.SafeIOError, match="компонент"):
        safe_io.ensure_dirs(root / "a" / ".." / "a", "x")
    assert not (root / "a" / "x").exists()


def test_refusal_does_not_leak_descriptors(root: Path, impl: str) -> None:
    (root / "a").mkdir()
    (root / "a" / "link").symlink_to(root)
    before = len(os.listdir("/dev/fd"))
    for _ in range(20):
        with pytest.raises(safe_io.SafeIOError):
            safe_io.read_regular(root, "a/link/x", limit=10)
    assert len(os.listdir("/dev/fd")) == before
```

- [ ] **Step 3: Прогнать — RED на `walk`**

Run: `uv run pytest -q -p no:cache tests/test_governance_safe_io.py`
Expected: все `[walk]` падают с `SafeIOError … только macOS`; `[nofollow_any]` на macOS проходят (кроме `test_walk_refuses_dotdot_in_root` — он только `walk`).

- [ ] **Step 4: Реализовать `_open_walk` в `governance/safe_io.py`**

`_open`: при `any_component and NOFOLLOW_ANY is not None` — как сейчас (`flags | NOFOLLOW_ANY`); при `any_component and NOFOLLOW_ANY is None` — `return _open_walk(path, flags, dir_fd, mode)`; при `any_component=False` — как сейчас. `_flag` удалить. Обработка `OSError` (`ELOOP` → «ссылка в пути», иначе `strerror`) — общая для обеих веток; формат текста — как сейчас: `f"{path!r}: ссылка в пути"`, `f"{path!r}: {exc.strerror}"`.

`_open_walk`, алгоритм:

```text
comps = path.split("/")
if path начинается с "/": start = os.open("/", O_RDONLY|O_DIRECTORY); own = True; comps = comps[1:]
else: start = dir_fd (обязателен; не закрывать); own = False
если comps пуст или любой из comps ∈ {"", ".", ".."}: отказ SafeIOError(f"{path!r}: путь с пустым, `.` или `..` компонентом")
fd = start
для каждого промежуточного c: nxt = openat(fd, c, O_RDONLY|O_DIRECTORY|O_NOFOLLOW); закрыть fd, если он свой; fd = nxt
последний: res = openat(fd, last, flags | O_NOFOLLOW, mode)
на OSError с errno ENOTDIR или ELOOP на любом шаге: если fstatat(fd, имя, follow_symlinks=False) — ссылка → errno.ELOOP (→ «ссылка в пути»); иначе пробросить как есть
finally: закрыть все свои дескрипторы, кроме возвращаемого
```

Модульный docstring: абзац про `O_NOFOLLOW_ANY` дополнить «на ОС без флага — покомпонентный обход от `/` или закреплённого `dir_fd` с `O_NOFOLLOW` на каждом шаге; гарантия та же».

- [ ] **Step 5: GREEN + линт**

Run: `uv run pytest -q -p no:cache tests/test_governance_safe_io.py && uv run ruff check governance/safe_io.py tests/test_governance_safe_io.py && uv run ruff format --check governance/safe_io.py tests/test_governance_safe_io.py`
Expected: все pass (на macOS обе реализации), ruff чистый. Если `ruff` нет в `.venv` worktree — `/Users/Andrei_Shtanakov/labs/all_ai_orchestrators/devtools/.venv/bin/ruff`.

- [ ] **Step 6: Commit**

```bash
git add governance/safe_io.py tests/test_governance_safe_io.py
git commit -m "feat(safe_io): component-wise no-symlink walk where O_NOFOLLOW_ANY is absent"
```

---

### Task 2: каталог вызова и раннер — тесты выполняются вне Darwin

**Files:**
- Modify: `tests/test_governance_sandbox.py` (маркер `darwin_only`, 40 применений; `test_test_mode_runs_off_darwin`)
- Modify: `tests/test_governance_runner.py:10646-10700` (`_darwin_only`, 2 теста)
- Modify: `tests/test_governance_sandbox_live.py` — только если какой-то тест пропускается из-за `O_NOFOLLOW_ANY`, а не `sandbox-exec`

**Interfaces:**
- Consumes: Task 1 — `safe_io` работает на любой ОС.
- Produces: —

- [ ] **Step 1: Снять `darwin_only` там, где причина — только каталог вызова**

В `tests/test_governance_sandbox.py`: маркер с причиной «каталог вызова создаётся через O_NOFOLLOW_ANY» удалить со всех тестов, которым нужен только каталог вызова / `safe_io`. Тестам, которым действительно нужен `sandbox-exec` (запуск под профилем, проба допуска) — заменить на маркер с причиной про `sandbox-exec`. Решение по каждому тесту — по тому, что он вызывает. Модульный docstring (строка 6) поправить. В `test_test_mode_runs_off_darwin` убрать маркер и оговорку в комментарии. То же для 2 тестов `_darwin_only` в `test_governance_runner.py` и любых тестов `test_governance_sandbox_live.py` с причиной про `O_NOFOLLOW_ANY`.

- [ ] **Step 2: Прогнать на macOS**

Run: `PYTHONPATH=…/steward/src uv run pytest -q -p no:cache tests/test_governance_sandbox.py tests/test_governance_runner.py tests/test_governance_sandbox_live.py`
Expected: pass; число пропусков в этих файлах на macOS не выросло.

- [ ] **Step 3: Прогнать на Linux (контейнер)**

Run (из корня worktree; `S` — каталог в scratchpad):
```bash
git archive --format=tar HEAD | tar -x -C "$S/src"
podman run --rm -v "$S/src:/src:ro" docker.io/library/debian:bookworm sh -c '
  apt-get -qq update >/dev/null && apt-get -qq install -y git curl ca-certificates make procps >/dev/null
  curl -LsSf https://astral.sh/uv/0.11.23/install.sh | sh >/dev/null 2>&1; export PATH=$HOME/.local/bin:$PATH
  useradd -m u && cp -a /src /home/u/w && chown -R u /home/u/w
  su u -c "cd ~/w && git init -q && git add -A && git -c user.email=t@t -c user.name=t commit -qm s && export PATH=$PATH && uv python install 3.12 >/dev/null 2>&1 && uv sync -q --frozen --group governance && uv run --frozen --group governance pytest -q -rs -p no:cache tests/test_governance_safe_io.py tests/test_governance_sandbox.py tests/test_governance_runner.py tests/test_governance_sandbox_live.py"'
```
Expected: 0 failed; в `-rs` нет причин про `O_NOFOLLOW_ANY` / «каталог вызова»; остались только причины про `sandbox-exec`. Записать в отчёт счётчики passed/skipped.

- [ ] **Step 4: Commit**

```bash
git add tests/test_governance_sandbox.py tests/test_governance_runner.py tests/test_governance_sandbox_live.py
git commit -m "test(sandbox): call-dir tests run off Darwin; darwin_only kept only for sandbox-exec"
```

---

### Task 3: сквозной тест режима test и отказ strict вне Darwin

**Files:**
- Modify: `tests/test_governance_sandbox.py` (рядом с `test_test_mode_runs_off_darwin`, фикстуры `plain`, `_agent_writes_plain`, `_launch`)

**Interfaces:**
- Consumes: Task 1, Task 2; существующие фикстуры `plain`, `_agent_writes_plain(plain, monkeypatch)`, `_launch(plain)`.
- Produces: —

- [ ] **Step 1: Тесты**

```python
def test_test_mode_walk_publishes_node(plain, monkeypatch):
    """Обход вместо O_NOFOLLOW_ANY: каталог вызова создан, узел перенесён."""
    monkeypatch.setattr(safe_io, "NOFOLLOW_ANY", None)
    monkeypatch.setattr(author_launch, "_os_name", lambda: "linux")
    seen = _agent_writes_plain(plain, monkeypatch)
    out = _launch(plain)
    assert out.ok, out
    assert "call" in seen
    # узел опубликован в бандл цели — путь по фикстуре `plain`


def test_test_mode_through_symlinked_home(plain, tmp_path, monkeypatch):
    """HOME/XDG за ссылкой (Silverblue): вызывающие канонизируют корни."""
    monkeypatch.setattr(safe_io, "NOFOLLOW_ANY", None)
    real_state = Path(os.path.realpath(tmp_path)) / "real-state"
    real_state.mkdir()
    (tmp_path / "state-link").symlink_to(real_state)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-link"))
    _agent_writes_plain(plain, monkeypatch)
    assert _launch(plain).ok
    assert any(real_state.rglob("*"))


def test_strict_off_darwin_refuses_before_call_dir(strict_fixture, monkeypatch):
    """strict вне macOS: отказ до каталога вызова и запуска агента; в test не уходит."""
    monkeypatch.setattr(author_launch, "_os_name", lambda: "linux")
    ran = []
    monkeypatch.setattr(author_launch, "_run_agent", lambda *a, **k: ran.append(a))
    out = _launch(strict_fixture)
    assert not out.ok
    assert "только на macOS" in out.reason
    assert ran == []
    assert not sandbox.calls_root().exists()
```

`strict_fixture` — существующая фикстура strict-конфига этого файла (найти по `mode = "strict"` / отсутствию конфига); если подходящей нет — сделать по образцу `plain`, без `[harness]` test. Последняя строка проверки узла в первом тесте — дописать по фикстуре `plain` (какой файл агент пишет и куда он публикуется). Имя поля причины отказа — по фактическому типу результата `_launch`.

- [ ] **Step 2: RED/GREEN**

Run: `PYTHONPATH=…/steward/src uv run pytest -q -p no:cache tests/test_governance_sandbox.py -k "walk_publishes or symlinked_home or strict_off_darwin"`
Expected: на ветке с Task 1 — pass сразу (это тесты-закрепители, не новый код). Проверить, что они **ловят** поломку: временно вернуть в `_open_walk` открытие корня обычным `os.open(root)` — `test_test_mode_through_symlinked_home` должен остаться зелёным (канонизация у вызывающих), а Task 1 `test_symlinked_ancestor_of_root_refuses` — покраснеть; временно убрать канонизацию в `sandbox.calls_root` — `test_test_mode_through_symlinked_home` краснеет. Откатить оба изменения. Записать в отчёт.

- [ ] **Step 3: Полный набор на macOS и Linux**

Run (macOS): `PYTHONPATH=…/steward/src uv run pytest -q -p no:cache --deselect tests/test_governance_sandbox.py::test_create_call_dir_for_devtools_itself_as_target`
Expected: 0 failed. Если `test_runner_z14_*` падают с `FileNotFoundError: gate-check` — это окружение worktree (`ln -s /Users/Andrei_Shtanakov/labs/all_ai_orchestrators/devtools/.venv/bin/gate-check .venv/bin/`), не регрессия.

Run (Linux): контейнер из Task 2 Step 3, весь `tests/` кроме `tests/df` (`--ignore=tests/df`), под непривилегированным пользователем.
Expected: 0 failed; пропусков с причиной про `O_NOFOLLOW_ANY`/каталог вызова — 0.

- [ ] **Step 4: Commit**

```bash
git add tests/test_governance_sandbox.py
git commit -m "test(author): test-mode authoring via walk, symlinked HOME, strict refusal off Darwin"
```

---

## После задач

- Финальное ревью ветки (opus), один круг правок.
- PR в devtools: «DarkFactory на Linux — PR1: переносимый safe_io»; в описании — что `install.sh` и инструкция не тронуты (PR2), замеры Linux (passed/skipped до и после), **мержит владелец**.
- `sh review-pr.sh devtools <pr> --dry-run --write-verdict <file>` → `--use-verdict <file>`.
- Ссылку владельцу; агент **не мержит**.
