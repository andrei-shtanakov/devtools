---
name: issue-executor
description: >
  Принять issue из GitHub, реализовать его и оформить PR. Полный цикл: чтение
  issue → проверка TODO.md → создание ветки → реализация (TDD) → тесты → линт
  → коммит → push → PR → review → merge. Запускать: «прими и выполни #N»,
  «сделай issue #N», «выполни devtools#N».
allowed-tools: Bash, Read, Grep, Glob, Write, Edit
---

# issue-executor — полный цикл выполнения issue

## Вход

Один аргумент: номер issue (например `#69` или `devtools#69`).

## Процедура

### 1. Принять issue

```bash
gh issue view <N> -R andrei-shtanakov/devtools --json number,title,body,labels,state
```

- Проверить, что issue открыт.
- Проверить `TODO.md` на наличие связанного пункта (по slug или id).
- Если пункт уже есть в TODO.md — взять контекст оттуда.
- Если нет — завести пункт в TODO.md с указанным в issue `slug:`.

### 2. Подготовить контекст

- Прочитать `TODO.md` целиком для понимания текущего плана.
- Найти связанные файлы через `grep` и `glob`.
- Прочитать релевантные существующие файлы.
- Понять архитектурный контекст (см. CLAUDE.md, README.md).

### 3. Создать ветку

```bash
git switch -c feat/<slug>
```

- Имя ветки: `feat/<slug>` или `fix/<slug>` в зависимости от типа issue.

### 4. Реализовать (TDD)

1. Написать тесты (RED).
2. Запустить тесты — убедиться, что они падают.
3. Написать минимальный код для прохождения тестов (GREEN).
4. Запустить тесты — убедиться, что проходят.

```bash
uv run --frozen pytest tests/test_<name>.py -q
```

### 5. Проверить код

```bash
uv run --frozen pytest -q                    # все тесты
uvx ruff check <files> --line-length 88      # линт
uvx ruff format --diff <files>               # формат
```

- Длина строк: максимум 88 символов.
- Все тесты должны проходить.
- Нет ошибок линта.

### 6. Закоммитить

```bash
git add <files>
git commit -m "<type>(<scope>): <description>"
```

- Формат: `feat(plan): ...`, `fix(governance): ...`, `chore: ...`
- Описание на русском, если issue на русском.

### 7. Создать PR

```bash
git push -u origin feat/<slug>
gh pr create --title "<title>" --body "<body>"
```

- Тело PR: краткое описание что и зачем, ссылка на issue.

### 8. Дождаться CI и review

```bash
# Дождаться завершения CI
until [ "$(gh pr checks <N> --json state --jq '[.[]|select(.state=="IN_PROGRESS" or .state=="QUEUED" or .state=="PENDING")]|length')" = "0" ]; do sleep 10; done

# Review
sh review-pr.sh devtools <PR> --dry-run --write-verdict <file>
sh review-pr.sh devtools <PR> --use-verdict <file>
```

- Если review нашёл блокирующие находки — исправить и повторить.
- Если approve — мержить.

### 9. Merge

```bash
sh merge-pr.sh devtools <PR>
```

### 10. Пост-мерж

```bash
git switch master && git pull --ff-only
git branch -d feat/<slug>
git push origin --delete feat/<slug>
git fetch --prune
```

- Закрыть issue: `gh issue close <N>`
- Обновить TODO.md (отметить пункт как выполненный).

## Ограничения

- Не редактировать файлы соседних репо напрямую.
- Не делать force-push.
- Не мержить без approve от review контура.
- Бюджет review: 1 полный + 1 адресный recheck.

## Примеры

```
User: прими и выполни devtools#69
Agent: [использует issue-executor skill]

User: сделай issue #67 — salvage-скан флота
Agent: [использует issue-executor skill]
```
