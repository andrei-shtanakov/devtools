# Роутер области ревью — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PR, все пути которого классифицированы как проза, не доходит до
модельного ревьюера; вместо вердикта публикуется scope-аттестация.

**Architecture:** Классификатор живёт в `review-pr.sh` и читает список путей из
файла-контракта `contracts/review-scope/v1/prose-paths.env` (срез B перенесёт
тот же файл в кит вендорингом). Проза перечислена, код — всё остальное;
`CODE_OVERRIDE` сильнее `PROSE`. Прозаический PR получает APPROVE от ai-prosto с
собственным маркером `ai-prosto-scope-review`, который ни один потребитель
протокола `codex-terminal-review` не читает как вердикт.

**Tech Stack:** POSIX `sh` (`set -eu`, без bash-измов и `pipefail`), `git`, `jq`,
`gh`; тесты — `pytest` со стабами `gh`/кита и настоящим git-репо.

**Spec:** `docs/superpowers/specs/2026-09-18-review-scope-router-design.md`

## Global Constraints

- POSIX `sh`: никаких `[[`, массивов, `pipefail`. Файл уже под `set -eu`.
- Fail-closed везде: факт, который не удалось получить, читается **против**
  пропуска — PR ревьюится как сегодня.
- Два jq-выражения (дедуп и stop rule) обязаны остаться **побайтово
  одинаковыми**: один разбор на всех потребителей.
- Бюджет и stop rule не трогаем: scope-аттестация выходит до `budget_charge`.
- Тесты Python — длина строки ≤ 88 символов, стиль существующего файла.
- Коммиты завершаются трейлером
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- Ветка → PR; прямые коммиты в `master` запрещены.

---

## Этап 1 — в открытый PR #259 (ветка `feat/review-loop-limit-enforcement`)

Причина границы: D4 правит парсер, который вводит сам #259; на `master` этого
кода нет. Фикс-коммит нового круга ревью не открывает — круг открывает
блокирующая находка, а не любой коммит.

### Task 1: Отбор кандидата протокола отдельно от валидации

**Files:**
- Modify: `review-pr.sh` (два jq-выражения: блок дедупа ~`:671`, блок stop rule ~`:783`)
- Test: `tests/test_review_pr.py`

**Interfaces:**
- Consumes: существующие `$REVIEW_LOGIN`, `$work`, `gh_r`.
- Produces: поведение `lr_state`/`c_state` — `"none"` (кандидатов нет),
  `"miss"` (кандидат есть, но негоден), `APPROVED`/`CHANGES_REQUESTED`.

- [ ] **Step 1: Написать падающие тесты**

В `tests/test_review_pr.py`, рядом с существующими stop-rule тестами:

```python
def _scope_review(head: str, login: str = "ai-prosto") -> dict:
    body = "## Automated scope attestation — prose-only\n\n"
    body += f"<!-- ai-prosto-scope-review version=1 kind=prose-only head={head} -->\n"
    return {"user": {"login": login}, "state": "APPROVED", "body": body}


@needs_jq
def test_unmarked_review_does_not_hide_terminal_verdict(fleet: Fleet) -> None:
    """Ревью без префикса маркера — не кандидат протокола: более ранний
    terminal-вердикт остаётся виден stop rule."""
    reviews = fleet.write_reviews(
        _review("APPROVED", OLD_HEAD, FP),
        _scope_review(OLD_HEAD),
    )
    res = fleet.run("demo", "7", GH_STUB_REVIEWS_JSON=reviews)
    assert res.returncode == 2, res.stdout
    assert "блокирующ" in res.stderr.lower()


@needs_jq
def test_malformed_terminal_review_does_not_resurrect_older(fleet: Fleet) -> None:
    """Новый кандидат с задублированным маркером — miss; предыдущий
    валидный approve НЕ воскресает, прогон разрешён."""
    reviews = fleet.write_reviews(
        _review("APPROVED", OLD_HEAD, FP),
        _review("APPROVED", OLD_HEAD, FP, markers=2),
    )
    assert fleet.run("demo", "7", GH_STUB_REVIEWS_JSON=reviews).returncode == 0


@needs_jq
def test_dismissed_terminal_review_does_not_resurrect_older(fleet: Fleet) -> None:
    """DISMISSED — кандидат протокола, не прошедший валидацию: miss,
    поиск назад не ведётся."""
    reviews = fleet.write_reviews(
        _review("APPROVED", OLD_HEAD, FP),
        _review("DISMISSED", OLD_HEAD, FP),
    )
    assert fleet.run("demo", "7", GH_STUB_REVIEWS_JSON=reviews).returncode == 0
```

- [ ] **Step 2: Прогнать — убедиться, что падают**

Run: `uv run pytest tests/test_review_pr.py -k "unmarked or resurrect" -v`
Expected: `test_unmarked_review_does_not_hide_terminal_verdict` FAIL
(returncode 0 вместо 2 — scope-ревью съело вердикт). Два теста про
воскрешение на текущем коде проходят случайно: `last` берёт негодного
кандидата и даёт miss. Это не повод их не писать — они фиксируют
инвариант, который новый отбор обязан сохранить.

- [ ] **Step 3: Поменять отбор в обоих jq-выражениях**

В **обоих** местах заменить первую строку выражения. Было:

```jq
([ .[][] | select(.user.login == "'"$REVIEW_LOGIN"'") ] | last) as $r
```

Стало:

```jq
([ .[][]
   | select(.user.login == "'"$REVIEW_LOGIN"'")
   | select(((.body // "") | index("<!-- codex-terminal-review ")) != null)
 ] | last) as $r
```

Остальная часть выражения (проверка `$n == 1`, полного формата и `state`)
не меняется ни на символ.

`index()`, а не `test()`: искомое — литеральная подстрока, и регексп здесь
дал бы лишнюю поверхность толкования на тексте, пришедшем из недоверенного
дифа.

- [ ] **Step 4: Дописать комментарий у первого вхождения**

Над первым jq (блок дедупа) добавить:

```sh
# Отбор кандидата и валидация РАЗНЕСЕНЫ намеренно. Кандидат протокола —
# ревью, в теле которого есть префикс маркера; выбирается ПОСЛЕДНИЙ такой,
# и только он валидируется. Правило «последнее ВАЛИДНОЕ ревью» пропускало бы
# новый повреждённый или задублированный маркер и воскрешало перекрытый им
# вердикт — stop rule решал бы по вердикту, который свежее событие протокола
# уже отменило. Повреждённый, задублированный и DISMISSED кандидат остаётся
# miss, поиск назад НЕ ведётся. Не-кандидаты (scope-аттестации, любые ревью
# $REVIEW_LOGIN без префикса) на результат не влияют.
```

- [ ] **Step 5: Прогнать все тесты файла**

Run: `uv run pytest tests/test_review_pr.py -v`
Expected: PASS, включая три новых.

- [ ] **Step 6: Проверить, что выражения не разъехались**

Run:
```bash
grep -c 'index("<!-- codex-terminal-review ")' review-pr.sh
```
Expected: `2`.

- [ ] **Step 7: Коммит в ветку #259**

```bash
git switch feat/review-loop-limit-enforcement
git add review-pr.sh tests/test_review_pr.py
git commit -m "$(cat <<'EOF'
fix(review-pr): кандидат протокола отбирается до валидации маркера

Разбор брал последнее ревью ai-prosto ВООБЩЕ и лишь затем требовал маркер:
любое ревью без маркера давало miss и ослепляло stop rule в обе стороны —
скрывало и красный вердикт, и зелёный (лишний оплаченный круг).

Теперь выбирается последнее ревью-КАНДИДАТ (в теле есть префикс маркера), и
валидируется только оно. «Последнее валидное» было бы хуже: новый
повреждённый или задублированный маркер пропускался бы, воскрешая
перекрытый вердикт. Повреждённый, задублированный и DISMISSED — miss, поиск
назад не ведётся.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
git push
```

- [ ] **Step 8: Сказать владельцу, что фикс в #259, и дождаться мержа**

Этап 2 начинается только после мержа #259 в `master`: до этого кода
`lr_*` в `master` нет.

---

## Этап 2 — отдельный PR после мержа #259

### Task 2: Контракт путей и классификатор области

**Files:**
- Create: `contracts/review-scope/v1/prose-paths.env`
- Modify: `review-pr.sh` (хойст fetch базы; функции разбора и классификации; зонд `--print-scope`)
- Modify: `tests/test_review_pr.py` (переименование файла фикстуры + тесты классификатора)

**Interfaces:**
- Produces: функция `classify_scope()` печатает `prose` либо `code`;
  переменная `scope` с тем же значением; отладочный зонд
  `review-pr.sh <repo> <pr> --print-scope` печатает это значение и выходит `0`.

- [ ] **Step 1: Завести файл-контракт**

Create `contracts/review-scope/v1/prose-paths.env`:

```sh
# Область ревью v1 — какие пути НЕ отправляются модельному ревьюеру.
#
# Забор ставится ПО ПРОЗЕ: код — всё, что здесь не перечислено. Обратная
# формулировка («ревьюить только .py/.sh/.ex») вывела бы из-под ревью
# .github/workflows/*.yml, approval-policy.yaml, patterns.env — класс, где
# уже был живой дефект merge-broker[bot] в approval-policy.yaml.
#
# Формат: строки KEY=VALUE, значения — глобы через пробел, сопоставляются
# оболочечным `case` (в нём `*` покрывает и `/`, поэтому docs/* — это всё
# дерево docs). Ключ можно повторять: значения СКЛЕИВАЮТСЯ, а не
# перекрывают друг друга — так список переносится по строкам и остаётся
# читаемым. Файл ПАРСИТСЯ, не исполняется.
#
# Читатели: devtools/review-pr.sh. Срез B вендорит этот же файл в
# scripts/review/ целевых репо — правило не переписывается второй раз.
PROSE=*.md *.txt TODO.md docs/* workstreams/*/spec/*

# Сильнее PROSE: эти пути остаются кодом при любом расширении. Markdown
# внутри них — данные, а не проза: на steward#170 оба блокирующих
# gold-дефекта лежали именно в таком Markdown.
CODE_OVERRIDE=.github/* */.github/*
CODE_OVERRIDE=contracts/* */contracts/*
CODE_OVERRIDE=eval/* */eval/*
CODE_OVERRIDE=fixtures/* */fixtures/*
CODE_OVERRIDE=schemas/* */schemas/*
```

- [ ] **Step 2: Написать падающие тесты классификатора**

В `tests/test_review_pr.py`:

```python
def _seed_files(fleet: Fleet, *paths: str) -> None:
    """Переложить голову PR так, чтобы диф трогал ровно эти пути."""
    work = fleet.tmp / "reseed"
    if work.exists():
        shutil.rmtree(work)
    subprocess.run(
        ["git", "clone", "-q", str(fleet.origin), str(work)],
        check=True, capture_output=True,
    )
    _git("config", "user.email", "t@example.com", cwd=work)
    _git("config", "user.name", "t", cwd=work)
    for p in paths:
        target = work / p
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("changed\n")
    _git("add", "-A", cwd=work)
    _git("commit", "-m", "pr change", cwd=work)
    fleet.head_sha = _git("rev-parse", "HEAD", cwd=work)
    _git("push", "-qf", "origin", "HEAD:refs/pull/7/head", cwd=work)


def test_scope_prose_only_when_every_path_is_prose(fleet: Fleet) -> None:
    _seed_files(fleet, "docs/guide.md", "TODO.md", "notes.txt")
    res = fleet.run("demo", "7", "--print-scope")
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "prose"


def test_scope_code_when_any_path_is_code(fleet: Fleet) -> None:
    _seed_files(fleet, "docs/guide.md", "src/tool.py")
    res = fleet.run("demo", "7", "--print-scope")
    assert res.stdout.strip() == "code"


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/ci.md",
        "contracts/review-scope/v1/notes.md",
        "eval/corpus/case.md",
        "fixtures/sample.md",
        "schemas/readme.md",
    ],
)
def test_markdown_in_code_dirs_is_code(fleet: Fleet, path: str) -> None:
    _seed_files(fleet, path)
    assert fleet.run("demo", "7", "--print-scope").stdout.strip() == "code"


def test_rename_from_code_to_prose_stays_code(fleet: Fleet) -> None:
    """Переименование проверяется по ОБОИМ путям: --no-renames показывает
    и удаление старого, и добавление нового."""
    work = fleet.tmp / "rename"
    subprocess.run(
        ["git", "clone", "-q", str(fleet.origin), str(work)],
        check=True, capture_output=True,
    )
    _git("config", "user.email", "t@example.com", cwd=work)
    _git("config", "user.name", "t", cwd=work)
    (work / "tool.py").write_text("x\n")
    _git("add", "-A", cwd=work)
    _git("commit", "-m", "add code", cwd=work)
    _git("push", "-q", "origin", "master", cwd=work)
    _git("mv", "tool.py", "docs/tool.md", cwd=work)
    _git("commit", "-m", "rename to prose", cwd=work)
    fleet.head_sha = _git("rev-parse", "HEAD", cwd=work)
    _git("push", "-qf", "origin", "HEAD:refs/pull/7/head", cwd=work)
    assert fleet.run("demo", "7", "--print-scope").stdout.strip() == "code"


def test_unreadable_file_list_falls_back_to_code(fleet: Fleet) -> None:
    """Fail-closed: merge-base не вычисляется — PR считается кодовым."""
    res = fleet.run("demo", "7", "--print-scope", GH_STUB_BASEREF="no-such-base")
    assert res.stdout.strip() == "code"
    assert "область ревью не определена" in res.stderr
```

`shutil` в шапке файла уже импортирован (строка 22) — добавлять не нужно.

- [ ] **Step 3: Прогнать — убедиться, что падают**

Run: `uv run pytest tests/test_review_pr.py -k scope -v`
Expected: FAIL — `--print-scope` неизвестный флаг, код 2.

- [ ] **Step 4: Переименовать файл фикстуры в кодовый путь**

`a.txt` по контракту — проза, и на нём все существующие full-run тесты
превратились бы в аттестации. Заменить на `a.py` в четырёх местах
`tests/test_review_pr.py`:

```bash
sed -i '' 's/a\.txt/a.py/g' tests/test_review_pr.py
grep -c 'a\.py' tests/test_review_pr.py   # ожидается 4
```

Прогнать весь файл — поведение существующих тестов не меняется:

Run: `uv run pytest tests/test_review_pr.py -v`
Expected: прежние тесты PASS, scope-тесты по-прежнему FAIL.

- [ ] **Step 5: Вынести fetch базы из `fp_supported`-блока**

Сейчас база освежается только при `fp_supported -eq 1`. Классификатор
обязан видеть то же состояние базы, что отпечаток и ревью, поэтому fetch
поднимается выше и становится безусловным. В `review-pr.sh` вырезать блок
`if ! fetch_err=$(git -C "$repo_dir" fetch -q origin ...)` из-под
`if [ "$fp_supported" -eq 1 ]; then` и поставить его перед классификацией,
заменив в комментарии «перед отпечатком» на «перед классификацией и
отпечатком»:

```sh
# Согласованность диапазона: классификация области, отпечаток и фактическое
# ревью обязаны видеть ОДНО состояние базы — освежаем её явным fetch здесь,
# дальше все вызовы кита идут без --fetch. Destination в refspec делает
# освежение безусловным даже на single-branch клоне (devtools#73).
if ! fetch_err=$(git -C "$repo_dir" fetch -q origin \
    "+refs/heads/$base_ref:refs/remotes/origin/$base_ref" 2>&1); then
    echo "$fetch_err" >&2
    die 2 "не удалось освежить базу origin/$base_ref перед ревью"
fi
```

- [ ] **Step 6: Реализовать разбор контракта и классификацию**

Сразу после хойстнутого fetch:

```sh
# --- Область ревью (contracts/review-scope/v1) -----------------------------
# Правило живёт файлом-контрактом, а не литералом: срез B вендорит ТОТ ЖЕ
# файл в кит, и второго написания правила не возникает. Литерал списка путей
# в этом репо уже однажды разъехался молча — см. governance/runner.py.
prose_paths_file="${REVIEW_SCOPE_CONTRACT:-$script_dir/contracts/review-scope/v1/prose-paths.env}"
prose_globs=""
code_globs=""
[ -f "$prose_paths_file" ] \
    || die 2 "нет контракта области ревью: $prose_paths_file"
# Повторы ключа склеиваются (список переносится по строкам), а не
# перекрывают друг друга: `tail -1`, как в harness.env, здесь молча терял бы
# все группы, кроме последней.
prose_globs=$(sed -n 's/^PROSE=//p' "$prose_paths_file" | tr '\n' ' ')
code_globs=$(sed -n 's/^CODE_OVERRIDE=//p' "$prose_paths_file" | tr '\n' ' ')
[ -n "$prose_globs" ] \
    || die 2 "контракт области ревью не называет PROSE: $prose_paths_file"

# 0 — путь проза, 1 — код. CODE_OVERRIDE сильнее PROSE: Markdown внутри
# .github/, contracts/, eval/, fixtures/, schemas/ — данные, не проза.
path_is_prose() {
    for _g in $code_globs; do
        # shellcheck disable=SC2254 — глоб намеренно не в кавычках
        case "$1" in $_g) return 1 ;; esac
    done
    for _g in $prose_globs; do
        # shellcheck disable=SC2254 — глоб намеренно не в кавычках
        case "$1" in $_g) return 0 ;; esac
    done
    return 1
}

# Печатает prose либо code. Fail-closed: список файлов не получен, не
# разобран или пуст — PR считается КОДОВЫМ и ревьюится как прежде.
# --no-renames намеренно: переименование приходит парой удаление+добавление,
# и оба пути проходят классификацию; иначе путь-источник остался бы невиден.
classify_scope() {
    if ! _mb=$(git -C "$repo_dir" merge-base "origin/$base_ref" "$review_ref" \
        2> "$work/scope.err"); then
        cat "$work/scope.err" >&2
        echo "ЗАМЕТКА: область ревью не определена (merge-base) —" \
            "PR ревьюится как кодовый." >&2
        echo code
        return 0
    fi
    if ! _files=$(git -C "$repo_dir" diff --no-renames --name-only \
        "$_mb..$head_sha" 2> "$work/scope.err"); then
        cat "$work/scope.err" >&2
        echo "ЗАМЕТКА: область ревью не определена (diff) —" \
            "PR ревьюится как кодовый." >&2
        echo code
        return 0
    fi
    _any=0
    _verdict=prose
    for _f in $_files; do
        _any=1
        path_is_prose "$_f" || { _verdict=code; break; }
    done
    [ "$_any" -eq 1 ] || {
        echo "ЗАМЕТКА: область ревью не определена (пустой диапазон) —" \
            "PR ревьюится как кодовый." >&2
        _verdict=code
    }
    echo "$_verdict"
}

scope=$(classify_scope)
```

Оговорка про `for _f in $_files`: пути с пробелами такой цикл разорвёт, и
разорванный кусок почти наверняка не совпадёт ни с одним глобом прозы, то
есть результат сместится в сторону **кода** — в безопасную сторону. Во
флоте путей с пробелами нет; закрывать это `while IFS= read -r` нельзя без
подоболочки, которая съела бы `_verdict`.

- [ ] **Step 7: Добавить зонд `--print-scope`**

К разбору аргументов, рядом с `--print-review-cmd` (тот же жанр —
отладочный зонд для тестов):

```sh
        --print-scope) print_scope=1; shift ;;
```

Инициализация `print_scope=0` рядом с `print_review_cmd=0`; сразу после
`scope=$(classify_scope)`:

```sh
if [ "$print_scope" -eq 1 ]; then
    echo "$scope"
    exit 0
fi
```

- [ ] **Step 8: Прогнать тесты**

Run: `uv run pytest tests/test_review_pr.py -v`
Expected: PASS целиком.

- [ ] **Step 9: Коммит**

```bash
git add contracts/review-scope/v1/prose-paths.env review-pr.sh tests/test_review_pr.py
git commit -m "$(cat <<'EOF'
feat(review-scope): классификатор области ревью и контракт путей

Проза перечислена в contracts/review-scope/v1/prose-paths.env, код — всё
остальное; CODE_OVERRIDE сильнее PROSE (Markdown внутри .github/, contracts/,
eval/, fixtures/, schemas/ — данные, не проза). Правило данными, чтобы срез B
вендорил тот же файл в кит, а не писал правило второй раз.

Fetch базы вынесен из fp-блока: классификация, отпечаток и ревью обязаны
видеть одно состояние базы. Переименования классифицируются по обоим путям
(--no-renames). Нечитаемый список файлов, пустой диапазон — PR кодовый.

Файл фикстуры a.txt переименован в a.py: .txt по контракту проза, и на нём
существующие full-run тесты превратились бы в аттестации.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 3: Scope-аттестация вместо вызова модели

**Files:**
- Modify: `review-pr.sh` (ветка `scope = prose` после классификации)
- Modify: `tests/test_review_pr.py`
- Modify: `CLAUDE.md` (секция Git workflow), `TODO.md`

**Interfaces:**
- Consumes: `scope` из Task 2; `publish`, `check_head_current`, `$work/body.md`.
- Produces: опубликованное ревью с маркером
  `<!-- ai-prosto-scope-review version=1 kind=prose-only head=<sha> -->`.

- [ ] **Step 1: Написать падающие тесты**

```python
def test_prose_only_publishes_attestation_without_kit(fleet: Fleet) -> None:
    _seed_files(fleet, "docs/guide.md", "TODO.md")
    res = fleet.run("demo", "7")
    assert res.returncode == 0, res.stderr
    assert _kit_calls(fleet) == []
    body = fleet.body_out.read_text()
    assert "Automated scope attestation — prose-only" in body
    assert "review-scope/v1" in body
    assert "проверяется required CI перед мержем" in body
    assert f"kind=prose-only head={fleet.head_sha}" in body
    assert "codex-terminal-review" not in body
    assert "--approve" in fleet.gh_calls()


def test_prose_only_does_not_charge_budget(fleet: Fleet) -> None:
    _seed_files(fleet, "docs/guide.md")
    fleet.run("demo", "7")
    ledger = fleet.tmp / "review-budget" / "andrei-shtanakov_demo-7.log"
    assert not ledger.exists()


def test_mixed_diff_runs_full_review(fleet: Fleet) -> None:
    _seed_files(fleet, "docs/guide.md", "src/tool.py")
    res = fleet.run("demo", "7")
    assert res.returncode == 0, res.stderr
    assert _kit_calls(fleet) != []
    assert "Automated scope attestation" not in fleet.body_out.read_text()


@needs_jq
def test_prose_only_after_changes_requested_stays_with_human(fleet: Fleet) -> None:
    """Красный модельный вердикт аттестацией не гасится: не публикуется
    ничего, модель не вызывается, PR остаётся человеку."""
    _seed_files(fleet, "docs/guide.md")
    reviews = fleet.write_reviews(_review("CHANGES_REQUESTED", OLD_HEAD, FP))
    res = fleet.run("demo", "7", GH_STUB_REVIEWS_JSON=reviews)
    assert res.returncode == 2, res.stdout
    assert _kit_calls(fleet) == []
    assert not fleet.body_out.exists()
    assert "остаётся человеку" in res.stderr


def test_prose_only_is_idempotent_on_same_head(fleet: Fleet) -> None:
    _seed_files(fleet, "docs/guide.md")
    assert fleet.run("demo", "7").returncode == 0
    first = fleet.body_out.read_text()
    assert fleet.run("demo", "7").returncode == 0
    assert fleet.body_out.read_text() == first


def test_prose_only_aborts_when_head_moved(fleet: Fleet) -> None:
    _seed_files(fleet, "docs/guide.md")
    res = fleet.run("demo", "7", GH_STUB_HEADOID2="f" * 40)
    assert res.returncode == 4, res.stdout
    assert not fleet.body_out.exists()
```

- [ ] **Step 2: Прогнать — убедиться, что падают**

Run: `uv run pytest tests/test_review_pr.py -k "prose_only or mixed" -v`
Expected: FAIL — кит вызывается, аттестации нет.

- [ ] **Step 3: Реализовать ветку прозы**

Сразу после зонда `--print-scope`. Место важно: блок обязан стоять **до**
вычисления отпечатка и до счётчика бюджета — вызова модели здесь не
происходит, и списывать нечего.

```sh
if [ "$scope" = "prose" ]; then
    # Прозаический PR модельному ревьюеру не отдаётся (решение владельца
    # 2026-09-18): платный ревьюер — только код. Публикуется scope-аттестация
    # — отдельная governance-сущность, а НЕ вердикт: у неё собственный маркер,
    # которого не знает ни один потребитель протокола codex-terminal-review,
    # поэтому появление кода на этом же PR не потребует --budget-override.
    #
    # Красный вердикт аттестацией не гасится. Опубликовать approve поверх
    # доставленного CHANGES_REQUESTED значило бы снять его синтетическим
    # зелёным; вызвать ревьюера — нарушить правило «модель не видит прозу».
    # Поэтому здесь отказ: PR остаётся человеку.
    if [ "$lr_state" = "CHANGES_REQUESTED" ]; then
        die 2 "prose-only диф при доставленном request-changes от" \
            "$REVIEW_LOGIN на ${slug}#${pr}: аттестация не публикуется" \
            "(она погасила бы красный вердикт), ревьюер не вызывается" \
            "(проза платному ревью не подлежит) — PR остаётся человеку." \
            "Появится код в дифе — прогон пойдёт обычным путём."
    fi
    {
        echo "## Automated scope attestation — prose-only"
        echo
        echo "- PR: ${slug}#${pr}, head \`$head_sha\`"
        echo "- classifier: \`review-scope/v1\`"
        echo "- все изменённые пути классифицированы как prose-only"
        echo "- модельный ревьюер не вызывался; содержательная корректность" \
            "прозы не проверялась"
        echo "- required CI checks остаются обязательным независимым" \
            "условием мержа"
        echo
        echo "<!-- ai-prosto-scope-review version=1 kind=prose-only" \
            "head=$head_sha -->"
    } > "$work/body.md"
    if [ "$dry_run" -eq 1 ]; then
        echo "=== dry-run: scope-аттестация, ничего не публикуется ==="
        cat "$work/body.md"
        exit 0
    fi
    publish approve
    exit 0
fi
```

Формулировка «проверено формальными гейтами» здесь запрещена: в момент
публикации зелёность чеков ещё не установлена — отсюда «проверяется
required CI перед мержем».

- [ ] **Step 4: Перенести чтение последнего вердикта выше**

Ветка прозы читает `lr_state`, поэтому блок «Последнее доставленное ревью»
обязан стоять **до** неё. Переместить его целиком (вместе с комментарием и
предупреждением про `lr_known`) выше блока `if [ "$scope" = "prose" ]`.
Порядок внутри блока и его строгость не меняются.

- [ ] **Step 5: Прогнать все тесты**

Run: `uv run pytest tests/test_review_pr.py -v`
Expected: PASS целиком.

- [ ] **Step 6: Прогнать линтеры репо**

Run: `uv run ruff check . && uv run ruff format --check . && shellcheck review-pr.sh`
Expected: чисто (`shellcheck` — если установлен; иначе пропустить и сказать об этом вслух).

- [ ] **Step 7: Обновить `CLAUDE.md`**

В секции «Git workflow», сразу после абзаца про бюджет, добавить:

```markdown
- **Проза модельному ревьюеру не отдаётся** (решение владельца 2026-09-18):
  PR, все пути которого классифицированы как проза
  (`contracts/review-scope/v1/prose-paths.env` — SSOT; код сильнее прозы для
  `.github/`, `contracts/`, `eval/`, `fixtures/`, `schemas/`), получает
  **scope-аттестацию** от ai-prosto вместо вердикта: модель не вызывается,
  бюджет не списывается. Аттестация — отдельная governance-сущность с
  собственным маркером `ai-prosto-scope-review`; шаг 3 она закрывает **только
  вместе с независимо зелёными required checks**. Прозаический PR с
  доставленным `CHANGES_REQUESTED` остаётся человеку: аттестация не
  публикуется. Спека: `docs/superpowers/specs/2026-09-18-review-scope-router-design.md`.
```

- [ ] **Step 8: Завести пункт в `TODO.md` про срез B**

```markdown
- [ ] Срез B: перенести правило области ревью в review-kit — `prose-paths.env`
      вендорится в `scripts/review/`, фильтр живёт в `local.sh` и накрывает три
      канала (local.sh, pre-push хук, review-pr.sh); там же становится
      возможна фильтрация кодового подмножества внутри смешанного дифа
      (кит строит диф без pathspec, `local.sh:528`). Цена — волна ре-вендора
      по флоту @owner:github:andrei-shtanakov @id:review-scope-kit-wave
```

- [ ] **Step 9: Коммит и PR**

```bash
git add review-pr.sh tests/test_review_pr.py CLAUDE.md TODO.md
git commit -m "$(cat <<'EOF'
feat(review-scope): prose-only PR получает scope-аттестацию вместо вердикта

Замер: ~19 PR/день по флоту, ~85% прозаические. Платный ревьюер — только код
(решение владельца 2026-09-18); формат спек и планов проверяется скриптом.

Аттестация — отдельная governance-сущность с маркером ai-prosto-scope-review,
не вердикт: потребители протокола codex-terminal-review её не видят, поэтому
появление кода на том же PR не требует --budget-override. Шаг 3 она закрывает
только вместе с независимо зелёными required checks.

Прозаический PR с доставленным CHANGES_REQUESTED остаётся человеку: approve
поверх красного вердикта погасил бы его, а вызвать ревьюера нельзя — проза
платному ревью не подлежит.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
git push -u origin HEAD
gh pr create --fill
```

- [ ] **Step 10: Живая приёмка**

На первом прозаическом PR флота после мержа проверить:
`gh pr view <n> --json reviews` содержит аттестацию с маркером
`ai-prosto-scope-review`, а `~/.local/state/ai-prosto/review-budget/` не
получил файла для этого PR.

---

## Самопроверка плана

**Покрытие спеки.** D1 — Task 2 Step 1; D2 — Task 2 Steps 6 (глобы,
`--no-renames`, fail-closed); D3 — Task 3 Steps 3 (тело, маркер, исход при
красном вердикте), бюджет не трогается по месту вставки; D4 — Task 1; D5 —
запрет, кода не требует, зафиксирован в спеке (реализации нет **намеренно**).
Инварианты 1–7 закрыты тестами Task 2 и Task 3. Все девять приёмочных
сценариев §5 спеки разложены: 1 → `test_prose_only_publishes_attestation…`;
2 → `test_mixed_diff_runs_full_review`; 3 →
`test_unreadable_file_list_falls_back_to_code`; 4 →
`test_markdown_in_code_dirs_is_code`; 5 → маркер аттестации не читается
протоколом (Task 1 Step 1 `_scope_review` + Task 3 assert
`"codex-terminal-review" not in body`); 6 →
`test_prose_only_after_changes_requested_stays_with_human`; 6a →
`test_unmarked_review_does_not_hide_terminal_verdict`; 6b → два теста про
воскрешение; 7 → `test_prose_only_is_idempotent_on_same_head`; 8 →
`test_rename_from_code_to_prose_stays_code`; 9 →
`test_prose_only_aborts_when_head_moved`.

**Плейсхолдеры.** Нет: каждый шаг несёт итоговый текст кода, теста или
коммита.

**Согласованность имён.** `classify_scope` / `path_is_prose` / `scope` /
`--print-scope` / `prose_paths_file` — одни и те же во всех задачах;
`_seed_files` объявлен в Task 2 и используется в Task 3; `_review`,
`_kit_calls`, `OLD_HEAD`, `FP`, `needs_jq` — существующие имена файла тестов.
