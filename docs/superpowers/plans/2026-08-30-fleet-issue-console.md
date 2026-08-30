# Fleet Issue Console (PR-1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Довести черновик fleet issue console (TUI всех открытых issues флота + изолированные tmux-worker-ы без publish-фаз) до состояния спеки и одного PR.

**Architecture:** Три единицы: `issue_console.py` (curses-TUI: сбор через gh, фильтр до локального флота, acceptance-enum через plan-fields, эвристическая классификация, группировка/выбор/запуск), `issue_worker.py` (обработчик одного issue в tmux-сессии: policy-гейт decision до Codex, результат в `devtools/out/issues/...`), `issue_classify.py` (опциональная AI-доклассификация unknown-типов с кэшем). Консоль никогда не пишет в целевые репо; эффекты — tmux-сессии и артефакты в `devtools/out/`.

**Tech Stack:** Python ≥3.12, stdlib (curses, subprocess, json) + уже запиненный пакет `plan-fields` (через `uv run --frozen`), gh CLI, tmux, `codex exec` (только для AI-опций).

**Spec:** `docs/superpowers/specs/2026-08-30-fleet-issue-console-design.md`

## Global Constraints

- Ветка: `feat/fleet-issue-console` (уже создана, спека закоммичена). Прямые коммиты в master запрещены.
- Без новых third-party зависимостей; ruff НЕ вводить (проверки: `python3 -m py_compile`, `uv run --frozen pytest`, line length 88).
- Разбор TODO.md — ТОЛЬКО `plan_fields.scrape_items`; частный regex недопустим.
- Консоль не пишет в целевые репозитории; допустимые эффекты — tmux-сессии и файлы под `devtools/out/` (в .gitignore, проверено).
- `decision` детерминирован политикой (internal → accept, external → reject) и вычисляется кодом ДО Codex; LLM может вернуть `needs_human`, но не перевернуть accept↔reject.
- Порог AI-классификации: `confidence < 0.75` → `unknown`. Ключ кэша: `owner/repo#number@updatedAt`.
- Тесты запускать: `uv run --frozen pytest tests/test_issue_console.py tests/test_issue_classify.py tests/test_issue_worker.py -q` (плюс полный прогон в финале).
- Коммиты завершать трейлером: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Зафиксировать черновик как baseline + пункт TODO.md

Черновик (`issue_console.py`, `issue_worker.py`, `tests/test_issue_console.py`, правки Makefile/README) сейчас незакоммичен. Коммитим его КАК ЕСТЬ первым коммитом, чтобы дальнейшие изменения были читаемым диффом.

**Files:**
- Commit as-is: `issue_console.py`, `issue_worker.py`, `tests/test_issue_console.py`, `Makefile`, `README.md`
- Modify: `TODO.md` (добавить пункт)

**Interfaces:**
- Produces: baseline-коммит; пункт плана `@id:fleet-issue-console`.

- [ ] **Step 1: Проверить, что baseline-тесты проходят**

Run: `cd ~/labs/all_ai_orchestrators/devtools && uv run --frozen pytest tests/test_issue_console.py -q`
Expected: 3 passed (если нет — зафиксировать фактический вывод, но НЕ чинить в этом коммите; чинится в следующих тасках).

- [ ] **Step 2: Добавить пункт в TODO.md**

В `TODO.md`, в конец актуального раздела задач (рядом с другими открытыми пунктами), одной строкой-чекбоксом:

```markdown
- [ ] Fleet issue console (PR-1): TUI открытых issues флота (acceptance/kind/группировка) + запуск изолированных tmux-worker-ов без publish @owner:github:andrei-shtanakov @id:fleet-issue-console — спека docs/superpowers/specs/2026-08-30-fleet-issue-console-design.md
```

- [ ] **Step 3: Commit**

```bash
git add issue_console.py issue_worker.py tests/test_issue_console.py Makefile README.md TODO.md
git commit -m "feat(issues): черновик fleet issue console как baseline (@id:fleet-issue-console)

Незакоммиченный прототип другой сессии принят владельцем за основу PR-1.
Доводка до спеки — следующими коммитами.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Acceptance-enum через plan-fields

Заменить `accepted: bool | None` на enum-строку `accepted | not-accepted | unverifiable | n/a`; разбор TODO.md — через `plan_fields.scrape_items`.

**Files:**
- Modify: `issue_console.py` (импорт-гард, `_acceptance()`, поле `Issue.accepted: str`, `parse_issues`, отрисовка колонки)
- Test: `tests/test_issue_console.py`

**Interfaces:**
- Consumes: `plan_fields.scrape_items(text: str) -> list[ScrapedItem]` (у элемента есть `.raw_text`); паттерн импорт-гарда — как в `inbox.py` (exit 2 при отсутствии пакета).
- Produces: `ACCEPTANCE = ("accepted", "not-accepted", "unverifiable", "n/a")`; `_acceptance(body: str, repo_path: Path | None) -> str` (для inbox-issue); `Issue.accepted: str`. Таски 3–8 полагаются на строковый enum.

- [ ] **Step 1: Написать падающие тесты**

Заменить в `tests/test_issue_console.py` тест `test_parse_groups_internal_and_inbox` и добавить кейсы:

```python
def _raw(repo: str = "alpha", body: str = "slug: slug-one",
         labels: tuple[str, ...] = ("inbox",), author: str = "owner") -> dict:
    return {
        "repository": {"name": repo, "nameWithOwner": f"owner/{repo}"},
        "number": 7, "title": "Research queue", "body": body,
        "author": {"login": author}, "createdAt": "2026-08-30T10:00:00Z",
        "updatedAt": "2026-08-30T11:00:00Z", "url": "https://example/7",
        "labels": [{"name": x} for x in labels],
    }


def _fleet(tmp_path: Path, todo: str | None = "- [ ] do thing slug-one\n") -> Path:
    repo = tmp_path / "alpha"
    (repo / ".git").mkdir(parents=True)
    if todo is not None:
        (repo / "TODO.md").write_text(todo)
    return tmp_path


def test_classify_covers_each_kind() -> None:
    assert issue_console.classify("Update README documentation", "", ()) == "document"
    assert issue_console.classify("Implement feature support", "", ()) == "code"


def test_acceptance_accepted(tmp_path: Path) -> None:
    root = _fleet(tmp_path)
    issue = issue_console.parse_issues([_raw()], root, {"owner"})[0]
    assert issue.accepted == "accepted"
    assert issue.internal and issue.inbox


def test_acceptance_not_accepted(tmp_path: Path) -> None:
    root = _fleet(tmp_path, todo="- [ ] unrelated item\n")
    issue = issue_console.parse_issues([_raw()], root, {"owner"})[0]
    assert issue.accepted == "not-accepted"


def test_acceptance_unverifiable_without_slug(tmp_path: Path) -> None:
    root = _fleet(tmp_path)
    issue = issue_console.parse_issues([_raw(body="просто текст")], root, {"owner"})[0]
    assert issue.accepted == "unverifiable"


def test_acceptance_unverifiable_without_todo(tmp_path: Path) -> None:
    root = _fleet(tmp_path, todo=None)
    issue = issue_console.parse_issues([_raw()], root, {"owner"})[0]
    assert issue.accepted == "unverifiable"


def test_acceptance_na_for_non_inbox(tmp_path: Path) -> None:
    root = _fleet(tmp_path)
    issue = issue_console.parse_issues([_raw(labels=("research",))], root, {"owner"})[0]
    assert issue.accepted == "n/a"


def test_acceptance_ignores_slug_in_prose(tmp_path: Path) -> None:
    root = _fleet(tmp_path, todo="в прозе упомянут slug-one, но пункта нет\n")
    issue = issue_console.parse_issues([_raw()], root, {"owner"})[0]
    assert issue.accepted == "not-accepted"
```

- [ ] **Step 2: Прогнать — убедиться, что падают**

Run: `uv run --frozen pytest tests/test_issue_console.py -q`
Expected: FAIL (accepted сейчас bool/None; updatedAt не разбирается).

- [ ] **Step 3: Реализация**

В `issue_console.py`:

1. После существующих импортов — гард plan-fields (паттерн inbox.py):

```python
try:
    from plan_fields import scrape_items
except ImportError:  # pragma: no cover - защита запуска вне uv-окружения
    scrape_items = None  # type: ignore[assignment]

ACCEPTANCE = ("accepted", "not-accepted", "unverifiable", "n/a")
```

2. Заменить `_accepted` на:

```python
def _acceptance(body: str, repo_path: Path | None) -> str:
    """Acceptance inbox-issue: derived от slug: в теле и чекбоксов TODO.md."""
    if scrape_items is None:
        raise RuntimeError(
            "пакет plan-fields недоступен — запускайте через `uv run --frozen` "
            "(make issues)"
        )
    slug = _field(body, "slug")
    if not slug or repo_path is None:
        return "unverifiable"
    todo = repo_path / "TODO.md"
    if not todo.is_file():
        return "unverifiable"
    items = scrape_items(todo.read_text(errors="ignore"))
    return "accepted" if any(slug in item.raw_text for item in items) else "not-accepted"
```

3. В `Issue` поле `accepted: str` (вместо `bool | None`); добавить поля `owner: str` и `updated_at: str` (нужны таскам 3 и 5).

4. В `parse_issues` (внутри цикла): `inbox = "inbox" in labels`; owner из `nameWithOwner`:

```python
        name_with_owner = str(repo_obj.get("nameWithOwner") or "")
        owner = name_with_owner.split("/")[0] if "/" in name_with_owner else "?"
```

и в конструкторе `Issue(...)`:

```python
            owner=owner, updated_at=str(item.get("updatedAt") or ""),
            accepted=("n/a" if not inbox
                      else _acceptance(body, repos.get(repo.lower()))),
```

(локальную переменную `inbox` использовать и для поля `inbox=inbox`).

5. В `run_tui` строку отрисовки acceptance заменить на:

```python
            acc = {"accepted": "A", "not-accepted": "N",
                   "unverifiable": "U", "n/a": "-"}[issue.accepted]
```

и использовать `acc` вместо старого `accepted` в f-строке.

6. В `fetch_issues` добавить `updatedAt` в список `--json`-полей.

- [ ] **Step 4: Прогнать тесты**

Run: `uv run --frozen pytest tests/test_issue_console.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add issue_console.py tests/test_issue_console.py
git commit -m "feat(issues): acceptance-enum через plan-fields вместо bool

accepted | not-accepted | unverifiable | n/a; разбор TODO.md — только
plan_fields.scrape_items (контракт inbox.py), плюс owner/updatedAt в модели.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Фильтр до флота + сортировка/группировка чистыми функциями

Показывать только issues репозиториев, у которых есть локальный клон под `--root`; дефолтная сортировка — по дате, новые сверху; `sort_issues()`/`group_key()` — чистые функции.

**Files:**
- Modify: `issue_console.py` (`parse_issues` — фильтр; новые `sort_issues`, `group_key`; `run_tui` использует их)
- Test: `tests/test_issue_console.py`

**Interfaces:**
- Consumes: `Issue` из Task 2 (поля `created_at`, `author`, `repo`).
- Produces: `sort_issues(issues: list[Issue], grouped: bool) -> list[Issue]`; `group_key(issue: Issue, grouped: bool) -> str`. Task 8 (TUI-доводка) их использует.

- [ ] **Step 1: Написать падающие тесты**

```python
def _issue(repo: str = "alpha", number: int = 1, author: str = "owner",
           created: str = "2026-08-30T10:00:00Z") -> issue_console.Issue:
    return issue_console.Issue(
        repo=repo, number=number, title="t", body="", author=author,
        created_at=created, url="", labels=(), inbox=False, accepted="n/a",
        kind="unknown", internal=True, owner="owner",
        updated_at="2026-08-30T11:00:00Z",
    )


def test_fleet_filter_drops_repos_without_clone(tmp_path: Path) -> None:
    root = _fleet(tmp_path)  # клон есть только у alpha
    raw = [_raw(), {**_raw(repo="ghost"), "repository": {
        "name": "ghost", "nameWithOwner": "owner/ghost"}}]
    issues = issue_console.parse_issues(raw, root, {"owner"})
    assert [x.repo for x in issues] == ["alpha"]


def test_sort_issues_newest_first() -> None:
    older = _issue(number=1, created="2026-08-01T00:00:00Z")
    newer = _issue(number=2, created="2026-08-29T00:00:00Z")
    assert issue_console.sort_issues([older, newer], grouped=False) == [newer, older]


def test_sort_issues_grouped_by_author_then_newest() -> None:
    a_old = _issue(number=1, author="bob", created="2026-08-01T00:00:00Z")
    a_new = _issue(number=2, author="bob", created="2026-08-29T00:00:00Z")
    b_new = _issue(number=3, author="alice", created="2026-08-28T00:00:00Z")
    assert issue_console.sort_issues([a_old, b_new, a_new], grouped=True) == [
        b_new, a_new, a_old]


def test_group_key() -> None:
    issue = _issue(author="bob", repo="alpha")
    assert issue_console.group_key(issue, grouped=True) == "bob"
    assert issue_console.group_key(issue, grouped=False) == "alpha"
```

- [ ] **Step 2: Прогнать — убедиться, что падают**

Run: `uv run --frozen pytest tests/test_issue_console.py -q`
Expected: FAIL (`sort_issues` не определён; ghost не отфильтрован).

- [ ] **Step 3: Реализация**

1. В `parse_issues` фильтр: в начале цикла, после вычисления `repo`:

```python
        if repo.lower() not in repos:
            continue  # спека: таблица — только флот с локальным клоном
```

2. Новые чистые функции (перед `run_tui`):

```python
def sort_issues(issues: list[Issue], grouped: bool) -> list[Issue]:
    """Дефолт — новые сверху; grouped — по инициатору, внутри новые сверху."""
    by_date = sorted(issues, key=lambda x: x.created_at, reverse=True)
    if not grouped:
        return by_date
    return sorted(by_date, key=lambda x: x.author.lower())


def group_key(issue: Issue, grouped: bool) -> str:
    return issue.author if grouped else issue.repo
```

3. В `run_tui` заменить inline-`sorted(...)` на `ordered = sort_issues(issues, grouped)`.

- [ ] **Step 4: Прогнать тесты**

Run: `uv run --frozen pytest tests/test_issue_console.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add issue_console.py tests/test_issue_console.py
git commit -m "feat(issues): фильтр до локального флота + sort_issues/group_key

Таблица показывает только репо с клоном под --root; сортировка по дате
(новые сверху), группировка по инициатору — чистыми функциями.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Семантика --internal (замена дефолтного набора)

**Files:**
- Modify: `issue_console.py` (`main`)
- Test: `tests/test_issue_console.py`

**Interfaces:**
- Produces: `DEFAULT_INTERNAL = frozenset({"andrei-shtanakov", "ai-prosto"})`; `resolve_internal(flags: list[str]) -> set[str]`.

- [ ] **Step 1: Написать падающие тесты**

```python
def test_internal_default_set() -> None:
    assert issue_console.resolve_internal([]) == {"andrei-shtanakov", "ai-prosto"}


def test_internal_flag_replaces_default() -> None:
    assert issue_console.resolve_internal(["Alice", "bob"]) == {"alice", "bob"}
```

- [ ] **Step 2: Прогнать — убедиться, что падают**

Run: `uv run --frozen pytest tests/test_issue_console.py -q`
Expected: FAIL (`resolve_internal` не определён).

- [ ] **Step 3: Реализация**

```python
DEFAULT_INTERNAL = frozenset({"andrei-shtanakov", "ai-prosto"})


def resolve_internal(flags: list[str]) -> set[str]:
    """--internal ЗАМЕНЯЕТ дефолтный набор целиком (спека), не дополняет."""
    return {x.lower() for x in flags} if flags else set(DEFAULT_INTERNAL)
```

В `main()` заменить `internal = {args.owner.lower(), *(...)}` на
`internal = resolve_internal(args.internal)`; help флага:
`parser.add_argument("--internal", action="append", default=[], help="internal-логин; повтор флага; заменяет дефолтный набор целиком")`.

- [ ] **Step 4: Прогнать тесты**

Run: `uv run --frozen pytest tests/test_issue_console.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add issue_console.py tests/test_issue_console.py
git commit -m "feat(issues): --internal заменяет дефолтный набор {andrei-shtanakov, ai-prosto}

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: issue_classify.py — AI-доклассификация с кэшем

Новый модуль: доклассификация issues с `kind=unknown` через `codex exec` с JSON-схемой; кэш `out/issue-kind-cache.json` с атомарной записью; отказоустойчивость.

**Files:**
- Create: `issue_classify.py`
- Test: `tests/test_issue_classify.py`

**Interfaces:**
- Consumes: `Issue` из issue_console (поля `owner`, `repo`, `number`, `updated_at`, `title`, `body`, `kind`); `KINDS`.
- Produces: `cache_key(issue) -> str` (= `owner/repo#number@updatedAt`); `refine(issues: list[Issue], cache_path: Path, run: Callable[[list[dict]], list[dict]] = run_codex) -> dict[str, str]` — маппинг `issue.key -> kind` ТОЛЬКО для уверенных ответов; `CONFIDENCE_THRESHOLD = 0.75`; `run_codex(batch: list[dict]) -> list[dict]`. Task 6 вызывает `refine`.

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/test_issue_classify.py`:

```python
import json
from pathlib import Path

import issue_classify
from test_issue_console import _issue


def _unknown(number: int = 1) -> object:
    issue = _issue(number=number)
    assert issue.kind == "unknown"
    return issue


def _answer(number: int = 1, kind: str = "fix", confidence: float = 0.9) -> dict:
    return {"repo": "alpha", "number": number, "kind": kind,
            "confidence": confidence}


def test_new_issue_calls_ai_and_caches(tmp_path: Path) -> None:
    cache = tmp_path / "cache.json"
    calls: list[list[dict]] = []

    def fake_run(batch: list[dict]) -> list[dict]:
        calls.append(batch)
        return [_answer()]

    kinds = issue_classify.refine([_unknown()], cache, run=fake_run)
    assert kinds == {"alpha#1": "fix"}
    assert len(calls) == 1
    saved = json.loads(cache.read_text())
    assert saved["owner/alpha#1@2026-08-30T11:00:00Z"] == "fix"


def test_unchanged_updated_at_is_cache_hit(tmp_path: Path) -> None:
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"owner/alpha#1@2026-08-30T11:00:00Z": "fix"}))

    def fail_run(batch: list[dict]) -> list[dict]:
        raise AssertionError("AI не должен вызываться при cache hit")

    assert issue_classify.refine([_unknown()], cache, run=fail_run) == {
        "alpha#1": "fix"}


def test_changed_updated_at_reclassifies(tmp_path: Path) -> None:
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"owner/alpha#1@2026-08-01T00:00:00Z": "code"}))
    kinds = issue_classify.refine([_unknown()], cache, run=lambda b: [_answer()])
    assert kinds == {"alpha#1": "fix"}


def test_low_confidence_stays_unknown(tmp_path: Path) -> None:
    kinds = issue_classify.refine(
        [_unknown()], tmp_path / "c.json",
        run=lambda b: [_answer(confidence=0.5)])
    assert kinds == {}


def test_broken_cache_and_dead_codex_keep_console_alive(tmp_path: Path) -> None:
    cache = tmp_path / "cache.json"
    cache.write_text("{битый json")

    def dead_run(batch: list[dict]) -> list[dict]:
        raise issue_classify.ClassifyError("codex недоступен")

    assert issue_classify.refine([_unknown()], cache, run=dead_run) == {}


def test_cache_write_is_atomic_no_partial_file(tmp_path: Path) -> None:
    cache = tmp_path / "sub" / "cache.json"
    issue_classify.refine([_unknown()], cache, run=lambda b: [_answer()])
    assert json.loads(cache.read_text())  # валидный json, каталог создан
    assert not list(cache.parent.glob("*.tmp*"))
```

- [ ] **Step 2: Прогнать — убедиться, что падают**

Run: `uv run --frozen pytest tests/test_issue_classify.py -q`
Expected: FAIL (модуля нет).

- [ ] **Step 3: Реализация — создать `issue_classify.py`**

```python
#!/usr/bin/env python3
"""AI-доклассификация типов issues (kind=unknown) через codex exec.

Опциональный контур issue_console (--classify-ai): база — детерминированная
эвристика; сюда попадают только неоднозначные issues. Ответы кэшируются по
ключу owner/repo#number@updatedAt; недоступный codex или битый кэш не ломают
консоль — типы просто остаются unknown.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

CONFIDENCE_THRESHOLD = 0.75
KINDS = ("document", "research", "code", "fix", "unknown")

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["repo", "number", "kind", "confidence"],
                "properties": {
                    "repo": {"type": "string"},
                    "number": {"type": "integer"},
                    "kind": {"enum": list(KINDS)},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        },
    },
}


class ClassifyError(RuntimeError):
    """Codex недоступен или вернул невалидный ответ."""


def cache_key(issue: Any) -> str:
    return f"{issue.owner}/{issue.repo}#{issue.number}@{issue.updated_at}"


def _load_cache(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if v in KINDS}


def _save_cache(path: Path, cache: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(cache, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        Path(tmp).unlink(missing_ok=True)


def run_codex(batch: list[dict]) -> list[dict]:
    """Один batch-вызов codex exec; ClassifyError при любом сбое."""
    prompt = (
        "Classify each GitHub issue into exactly one kind: document "
        "(docs/README/ADR edits), research (investigation/comparison), "
        "code (new functionality), fix (defect repair). Use unknown when "
        "genuinely ambiguous. Return JSON per the supplied schema.\n"
        f"Issues: {json.dumps(batch, ensure_ascii=False)}"
    )
    with tempfile.TemporaryDirectory(prefix="issue-classify-") as tmp:
        schema = Path(tmp) / "schema.json"
        answer = Path(tmp) / "answer.json"
        schema.write_text(json.dumps(SCHEMA))
        done = subprocess.run(
            ["codex", "exec", "--ephemeral", "--output-schema", str(schema),
             "--output-last-message", str(answer), "--sandbox", "read-only",
             prompt],
            capture_output=True, text=True, timeout=300,
        )
        if done.returncode:
            raise ClassifyError(done.stderr.strip() or "codex exec failed")
        try:
            parsed = json.loads(answer.read_text())
            items = parsed["items"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ClassifyError(f"невалидный ответ codex: {exc}") from exc
    if not isinstance(items, list):
        raise ClassifyError("items не список")
    return items


def refine(
    issues: list[Any],
    cache_path: Path,
    run: Callable[[list[dict]], list[dict]] = run_codex,
) -> dict[str, str]:
    """kind для issue.key по кэшу и (при промахах) одному batch-вызову AI.

    Возвращает только уверенные ответы (confidence >= порога и kind != unknown);
    остальное молча остаётся unknown у вызывающего.
    """
    unknowns = [x for x in issues if x.kind == "unknown"]
    if not unknowns:
        return {}
    cache = _load_cache(cache_path)
    result: dict[str, str] = {}
    missing: list[Any] = []
    for issue in unknowns:
        hit = cache.get(cache_key(issue))
        if hit is not None and hit != "unknown":
            result[issue.key] = hit
        elif hit is None:
            missing.append(issue)
    if not missing:
        return result
    batch = [{"repo": x.repo, "number": x.number, "title": x.title,
              "body": x.body[:2000]} for x in missing]
    try:
        answers = run(batch)
    except ClassifyError:
        return result
    by_key = {f"{x.repo}#{x.number}": x for x in missing}
    for item in answers:
        try:
            issue = by_key[f'{item["repo"]}#{int(item["number"])}']
            kind, confidence = str(item["kind"]), float(item["confidence"])
        except (KeyError, TypeError, ValueError):
            continue
        if kind not in KINDS:
            continue
        confident = kind != "unknown" and confidence >= CONFIDENCE_THRESHOLD
        cache[cache_key(issue)] = kind if confident else "unknown"
        if confident:
            result[issue.key] = kind
    _save_cache(cache_path, cache)
    return result
```

- [ ] **Step 4: Прогнать тесты**

Run: `uv run --frozen pytest tests/test_issue_classify.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add issue_classify.py tests/test_issue_classify.py
git commit -m "feat(issues): AI-доклассификация unknown-типов с кэшем (issue_classify)

Batch через codex exec + JSON-схема; порог confidence 0.75; кэш
owner/repo#number@updatedAt с атомарной записью; сбой codex/битый кэш
не ломают вызывающего.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Флаг --classify-ai в консоли

**Files:**
- Modify: `issue_console.py` (`main`; применение уточнённых kind)
- Test: `tests/test_issue_console.py`

**Interfaces:**
- Consumes: `issue_classify.refine(issues, cache_path, run=...)` из Task 5.
- Produces: `apply_kinds(issues: list[Issue], kinds: dict[str, str]) -> list[Issue]`; CLI-флаг `--classify-ai`; путь кэша `OUT_ROOT / "issue-kind-cache.json"`, где `OUT_ROOT = Path(__file__).resolve().parent / "out"`.

- [ ] **Step 1: Написать падающие тесты**

```python
def test_apply_kinds_replaces_only_listed() -> None:
    a, b = _issue(number=1), _issue(number=2)
    updated = issue_console.apply_kinds([a, b], {"alpha#1": "fix"})
    assert [x.kind for x in updated] == ["fix", "unknown"]
    assert updated[1] is b


def test_classify_ai_flag_wires_refine(tmp_path: Path, monkeypatch) -> None:
    root = _fleet(tmp_path)
    raw = [_raw(body="просто текст", labels=("misc",))]
    called = {}

    def fake_refine(issues, cache_path, run=None):
        called["keys"] = [x.key for x in issues]
        called["cache"] = cache_path
        return {"alpha#7": "research"}

    monkeypatch.setattr(issue_console.issue_classify, "refine", fake_refine)
    import json as _json
    fixture = tmp_path / "issues.json"
    fixture.write_text(_json.dumps(raw))
    monkeypatch.setattr(
        "sys.argv",
        ["issue_console.py", "--root", str(root), "--input", str(fixture),
         "--json", "--classify-ai"])
    import io, contextlib
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert issue_console.main() == 0
    data = _json.loads(out.getvalue())
    assert data[0]["kind"] == "research"
    assert called["cache"] == issue_console.OUT_ROOT / "issue-kind-cache.json"
```

- [ ] **Step 2: Прогнать — убедиться, что падают**

Run: `uv run --frozen pytest tests/test_issue_console.py -q`
Expected: FAIL (`apply_kinds`, `--classify-ai`, `OUT_ROOT` нет).

- [ ] **Step 3: Реализация**

В `issue_console.py`:

1. Импорт: `import issue_classify` (после stdlib-импортов); модульная константа `OUT_ROOT = Path(__file__).resolve().parent / "out"`.

2. Чистая функция:

```python
def apply_kinds(issues: list[Issue], kinds: dict[str, str]) -> list[Issue]:
    from dataclasses import replace
    return [replace(x, kind=kinds[x.key]) if x.key in kinds else x
            for x in issues]
```

3. В `main()`: `parser.add_argument("--classify-ai", action="store_true", help="доклассифицировать unknown через codex (кэш в out/)")`; после `parse_issues`:

```python
    if args.classify_ai:
        kinds = issue_classify.refine(
            issues, OUT_ROOT / "issue-kind-cache.json")
        issues = apply_kinds(issues, kinds)
```

- [ ] **Step 4: Прогнать тесты**

Run: `uv run --frozen pytest tests/test_issue_console.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add issue_console.py tests/test_issue_console.py
git commit -m "feat(issues): опция --classify-ai — уточнение unknown через issue_classify

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Worker — policy-гейт decision + --output-root

`decision` вычисляется политикой ДО Codex; результат — в `<output-root>/issues/<repo>/<number>/result.json`; LLM не может перевернуть политику.

**Files:**
- Modify: `issue_worker.py`
- Test: `tests/test_issue_worker.py` (новый)

**Interfaces:**
- Consumes: вызывается консолью с аргументами Task 8.
- Produces: `policy_decision(internal: bool) -> str`; `enforce_policy(result: dict, decision: str) -> dict`; `result_path(output_root: Path, repo: str, number: int) -> Path`; обязательный CLI-флаг `--output-root`.

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/test_issue_worker.py`:

```python
import json
from pathlib import Path

import issue_worker


def test_policy_decision_is_deterministic() -> None:
    assert issue_worker.policy_decision(True) == "accept"
    assert issue_worker.policy_decision(False) == "reject"


def test_enforce_policy_blocks_llm_flip() -> None:
    flipped = {"decision": "accept", "kind": "fix", "summary": "s",
               "todo": "t", "next_step": "n", "changed_files": []}
    fixed = issue_worker.enforce_policy(dict(flipped), "reject")
    assert fixed["decision"] == "reject"


def test_enforce_policy_allows_needs_human() -> None:
    result = {"decision": "needs_human", "kind": "fix", "summary": "s",
              "todo": "t", "next_step": "n", "changed_files": []}
    assert issue_worker.enforce_policy(dict(result), "accept")["decision"] == (
        "needs_human")


def test_result_path_layout(tmp_path: Path) -> None:
    path = issue_worker.result_path(tmp_path, "alpha", 7)
    assert path == tmp_path / "issues" / "alpha" / "7" / "result.json"


def test_external_execute_degrades_to_read_only() -> None:
    assert issue_worker.effective_execute(mode="execute", internal=False) is False
    assert issue_worker.effective_execute(mode="execute", internal=True) is True
    assert issue_worker.effective_execute(mode="plan", internal=True) is False


def test_schema_keeps_decision_enum() -> None:
    assert issue_worker.SCHEMA["properties"]["decision"]["enum"] == [
        "accept", "reject", "needs_human"]
```

- [ ] **Step 2: Прогнать — убедиться, что падают**

Run: `uv run --frozen pytest tests/test_issue_worker.py -q`
Expected: FAIL (функций нет).

- [ ] **Step 3: Реализация**

Переписать `issue_worker.py` (сохранив SCHEMA и структуру main):

```python
#!/usr/bin/env python3
"""Run one issue analysis/implementation with a structured Codex result.

Policy-гейт: decision (accept/reject) детерминирован инициатором и вычислен
кодом до вызова Codex; модель может поднять needs_human, но не перевернуть
политику. Publish-фазы (commit/push/PR/merge) намеренно отсутствуют.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "kind", "summary", "todo", "next_step", "changed_files"],
    "properties": {
        "decision": {"enum": ["accept", "reject", "needs_human"]},
        "kind": {"enum": ["document", "research", "code", "fix", "unknown"]},
        "summary": {"type": "string"},
        "todo": {"type": "string"},
        "next_step": {"type": "string"},
        "changed_files": {"type": "array", "items": {"type": "string"}},
    },
}


def policy_decision(internal: bool) -> str:
    """Детерминированная политика: внутренний → accept, внешний → reject."""
    return "accept" if internal else "reject"


def enforce_policy(result: dict, decision: str) -> dict:
    """LLM не может перевернуть политику; needs_human — единственное исключение."""
    if result.get("decision") != "needs_human":
        result["decision"] = decision
    return result


def result_path(output_root: Path, repo: str, number: int) -> Path:
    return output_root / "issues" / repo / str(number) / "result.json"


def effective_execute(mode: str, internal: bool) -> bool:
    """External requests are never allowed to cross the read-only boundary."""
    return mode == "execute" and internal


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True)
    p.add_argument("--number", required=True, type=int)
    p.add_argument("--author", required=True)
    p.add_argument("--kind", required=True)
    p.add_argument("--mode", choices=("plan", "execute"), default="plan")
    p.add_argument("--internal", choices=("yes", "no"), required=True)
    p.add_argument("--output-root", required=True, type=Path,
                   help="абсолютный devtools/out (результат не в целевом репо)")
    p.add_argument("--url", default="")
    args = p.parse_args()
    issue = subprocess.run(
        ["gh", "issue", "view", str(args.number),
         "--json", "title,body,author,labels,url"], capture_output=True, text=True,
    )
    if issue.returncode:
        print(issue.stderr.strip())
        return 2
    payload = json.loads(issue.stdout)
    internal = args.internal == "yes"
    execute = effective_execute(args.mode, internal)
    decision = policy_decision(internal)
    prompt = f"""You are the worker for GitHub issue {args.repo}#{args.number}.
The initiator is {args.author}; the deterministic preliminary kind is {args.kind}.
Issue data: {json.dumps(payload, ensure_ascii=False)}

Acceptance policy is decided by code, not by you: decision={decision}
(initiator is {'internal' if internal else 'external'}). Keep that decision in
your structured result; you may return needs_human instead only when the issue
data is too incomplete to analyze.
{'Implement the issue in the current repository. Update TODO.md when its existing contract calls for it. Run relevant tests. Do not commit, push, open a PR, or merge.' if execute else 'Read and analyze only. Do not edit files or execute mutating commands.'}
Return only the structured result required by the supplied JSON schema.
"""
    final = result_path(args.output_root, args.repo, args.number)
    final.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="issue-worker-") as tmp:
        schema = Path(tmp) / "schema.json"
        raw = Path(tmp) / "raw-result.json"
        schema.write_text(json.dumps(SCHEMA))
        cmd = ["codex", "exec", "--ephemeral", "--output-schema", str(schema),
               "--output-last-message", str(raw), "--sandbox",
               "workspace-write" if execute else "read-only", prompt]
        done = subprocess.run(cmd)
        if done.returncode:
            return done.returncode
        try:
            result = json.loads(raw.read_text())
        except (OSError, ValueError) as exc:
            print(f"issue-worker: невалидный результат codex: {exc}")
            return 3
    final.write_text(json.dumps(enforce_policy(result, decision),
                                ensure_ascii=False, indent=2))
    print(f"\nStructured result: {final}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Прогнать тесты**

Run: `uv run --frozen pytest tests/test_issue_worker.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add issue_worker.py tests/test_issue_worker.py
git commit -m "feat(issues): policy-гейт decision до Codex + результат в out/issues/

internal→accept, external→reject вычисляется кодом; LLM может лишь
needs_human. Результат — <output-root>/issues/<repo>/<number>/result.json,
рабочее дерево целевого репо не загрязняется.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Консоль — tmux-дедуп с подсказкой attach + передача --output-root

**Files:**
- Modify: `issue_console.py` (`launch`)
- Test: `tests/test_issue_console.py`

**Interfaces:**
- Consumes: CLI worker-а из Task 7 (`--output-root` обязателен); `OUT_ROOT` из Task 6.
- Produces: `launch(issue, root, mode) -> str` — человекочитаемый статус: `"started <session>"` либо `"exists: tmux attach -t <session>"`.

- [ ] **Step 1: Написать падающие тесты**

```python
def test_launch_skips_existing_tmux_session(tmp_path: Path, monkeypatch) -> None:
    root = _fleet(tmp_path)
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ["tmux", "has-session"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"неожиданная команда: {cmd}")

    monkeypatch.setattr(issue_console.subprocess, "run", fake_run)
    status = issue_console.launch(_issue(repo="alpha", number=7), root, "plan")
    assert status == "exists: tmux attach -t issue-alpha-7"
    assert not any(c[:2] == ["tmux", "new-session"] for c in calls)


def test_launch_passes_output_root(tmp_path: Path, monkeypatch) -> None:
    root = _fleet(tmp_path)
    captured: dict[str, str] = {}

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["tmux", "has-session"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        captured["shell"] = cmd[-1]
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(issue_console.subprocess, "run", fake_run)
    status = issue_console.launch(_issue(repo="alpha", number=7), root, "plan")
    assert status == "started issue-alpha-7"
    assert "--output-root" in captured["shell"]
    assert str(issue_console.OUT_ROOT) in captured["shell"]
```

- [ ] **Step 2: Прогнать — убедиться, что падают**

Run: `uv run --frozen pytest tests/test_issue_console.py -q`
Expected: FAIL (has-session нет; --output-root не передаётся; launch возвращает голое имя).

- [ ] **Step 3: Реализация**

Переписать `launch`:

```python
def launch(issue: Issue, root: Path, mode: str) -> str:
    """Одна tmux-сессия на issue; повторный запуск — подсказка attach."""
    repo_path = discover_repos(root).get(issue.repo.lower())
    if repo_path is None:
        raise RuntimeError(f"local clone for {issue.repo} not found")
    session = re.sub(r"[^a-zA-Z0-9_-]", "-", f"issue-{issue.repo}-{issue.number}")[:80]
    exists = subprocess.run(["tmux", "has-session", "-t", session],
                            capture_output=True, text=True)
    if exists.returncode == 0:
        return f"exists: tmux attach -t {session}"
    worker = Path(__file__).with_name("issue_worker.py")
    cmd = [sys.executable, str(worker), "--repo", issue.repo,
           "--number", str(issue.number), "--author", issue.author,
           "--kind", issue.kind, "--mode", mode, "--url", issue.url,
           "--internal", "yes" if issue.internal else "no",
           "--output-root", str(OUT_ROOT)]
    shell_cmd = " ".join(shlex.quote(part) for part in cmd) + "; exec ${SHELL:-/bin/sh}"
    done = subprocess.run(
        ["tmux", "new-session", "-d", "-s", session, "-c", str(repo_path), shell_cmd],
        capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(done.stderr.strip() or "tmux failed")
    return f"started {session}"
```

- [ ] **Step 4: Прогнать тесты**

Run: `uv run --frozen pytest tests/test_issue_console.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add issue_console.py tests/test_issue_console.py
git commit -m "feat(issues): tmux-дедуп сессий с подсказкой attach + --output-root worker-у

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: Обвязка (Makefile/README), финальный прогон

**Files:**
- Modify: `Makefile` (цель `issues` → uv), `README.md` (секция консоли — актуализировать)
- Проверка: py_compile + полный pytest.

**Interfaces:**
- Consumes: всё выше.

- [ ] **Step 1: Makefile — uv вместо python3**

Заменить строку цели:

```makefile
issues:      ; @uv run --frozen python ./issue_console.py --root $(WORKSPACE)
```

(plan-fields нужен для acceptance — см. спека; help-строку `make issues` дополнить: `(uv + Python 3.12)`).

- [ ] **Step 2: README — актуализировать секцию «Issue console»**

В существующей секции README (добавленной черновиком) поправить:
- запуск требует `gh`, `tmux`, `uv` (plan-fields для acceptance);
- результат worker-а: `out/issues/<repo>/<number>/result.json` (не `.issue-<n>-result.json` в целевом репо);
- acceptance-колонка: `A/N/U/-` = accepted / not-accepted / unverifiable / n/a;
- `--classify-ai`: доклассификация unknown через codex, кэш `out/issue-kind-cache.json`, порог 0.75; без флага Codex не нужен; полностью offline — `--input <json> --json`;
- `--internal` заменяет дефолтный набор `{andrei-shtanakov, ai-prosto}`;
- строку таблицы инструментов для `issue_console.py` привести к тому же описанию.

- [ ] **Step 3: Финальные проверки**

```bash
python3 -m py_compile issue_console.py issue_worker.py issue_classify.py
uv run --frozen pytest -q
awk 'length > 88 {print FILENAME": "FNR; bad=1} END {exit bad}' \
  issue_console.py issue_worker.py issue_classify.py
```

Expected: компиляция чистая; ВСЕ тесты репо зелёные; строк длиннее 88 нет.

- [ ] **Step 4: Commit**

```bash
git add Makefile README.md
git commit -m "docs(issues): make issues через uv (plan-fields) + актуализация README

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Push + PR**

```bash
git push -u origin feat/fleet-issue-console
gh pr create --title "feat: fleet issue console (PR-1) — TUI + изолированные workers без publish" --body "$(cat <<'EOF'
Спека: docs/superpowers/specs/2026-08-30-fleet-issue-console-design.md (в этом же PR).

- TUI (curses/stdlib) всех открытых issues флота: фильтр до локальных клонов,
  acceptance-enum через plan-fields, эвристическая классификация типов,
  сортировка по дате, группировка repo/инициатор, выбор и запуск.
- issue_worker: policy-гейт decision до Codex (internal→accept,
  external→reject; LLM может только needs_human), режимы plan/execute без
  publish-фаз, результат в devtools/out/issues/<repo>/<number>/result.json.
- issue_classify (--classify-ai): доклассификация unknown батчем через codex,
  кэш owner/repo#number@updatedAt, порог 0.75, отказоустойчивость.
- tmux: одна сессия на issue, повтор — подсказка attach.

Закрывает пункт @id:fleet-issue-console (TODO.md).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Ревью — по регламенту: `sh review-pr.sh devtools <pr> --dry-run`, затем без `--dry-run` (публикация от ai-prosto). Мерж — человек.
