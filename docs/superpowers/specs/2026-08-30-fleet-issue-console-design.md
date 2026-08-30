# Fleet issue console — дизайн PR-1 (TUI-вьювер + запуск worker-ов)

Дата: 2026-08-30. Статус: одобрен владельцем (сессия brainstorming), готов к плану.

## Контекст и место в декомпозиции

Большая цель — флотский inbox-оркестратор: увидеть все issues по репозиториям
воркспейса, сгруппировать, отметить и запустить обработку каждого в своём
терминале по конвейеру «принятие → тип → (документ/исследование/код/fix) →
ревью → мерж». Декомпозиция, принятая владельцем:

1. **fleet issue console (этот документ, PR-1)** — TUI-вьювер + запуск
   изолированных worker-ов без publish-фаз.
2. issue-runner — конвейер одного issue с publish (TODO/PR/ревью/мерж);
   пререквизит — экосистемный конфиг и политика DarkFactory.
3. fanout/spaces — частично входит в PR-1 (tmux-запуск).
4. Автоматика по типам (исследование → discovery/…, код → spec-runner/maestro)
   — отдельные дизайны позже; в PR-1 типы дают только маршрут/атрибут.

Политика DarkFactory (решение владельца 2026-08-30): по умолчанию мерж делает
агент, человеческий мерж — настраиваемая опция (уровень подпроекта или
экосистемы через общий конфиг). В PR-1 **не реализуется** (worker не публикует);
поправка в конституцию (ADR в prograph-vault + зеркало в CLAUDE.md) — отдельный
таск. До принятия поправки действует старое правило «мерж делает человек».

База кода: незакоммиченный черновик `issue_console.py` / `issue_worker.py`
(+ `make issues`, README-секция, `tests/test_issue_console.py`) принят
владельцем за основу; стек — **curses/stdlib**, не Textual (веб-слой позже
придёт через dispatcher GUI, поэтому TUI остаётся терминальным; pyproject
devtools существует только ради пина plan-fields, новых зависимостей не
добавляем).

## Компоненты

### issue_console.py — TUI (curses, stdlib)

- Сбор: `gh search issues` по owner (все открытые), затем **фильтр до флота**:
  показываются только issues репозиториев, обнаруженных `discover_repos(--root)`
  (динамика по локальным клонам, не workspace-manifest — вьюверу нужны и репо
  вне манифеста). Локальный клон одновременно и определяет состав таблицы,
  и даёт возможность запуска worker-а. `inbox` — колонка-атрибут (есть лейбл
  или нет). В запрос gh включается `updatedAt` (нужен для AI-кэша).
- Колонки: дата (createdAt; сортировка по ней по умолчанию, новые сверху),
  repo#number, inbox?, acceptance, инициатор (+internal/external), kind, title.
- `accepted` — enum, не bool:
  - `accepted` — inbox-issue, slug найден в строке-чекбоксе TODO.md целевого
    репо;
  - `not-accepted` — inbox-issue, slug есть, в TODO.md не найден;
  - `unverifiable` — inbox-issue, но нет `slug:` в теле, нет локального клона
    или нет TODO.md;
  - `n/a` — не-inbox issue (принятость неприменима).

  Разбор TODO.md — **общим пакетом `plan-fields`** (`scrape_items`), как в
  `inbox.py`: он единственная реализация контракта acceptance, возврат к
  частному regex недопустим. Следствие для утверждения «stdlib»: TUI-код —
  stdlib, но acceptance-подсистема зависит от уже запиненного plan-fields,
  поэтому консоль запускается через `uv run --frozen` (make-цель `issues`
  меняется с `python3` на `uv run --frozen python`).
- Классификация типа (`document | research | code | fix | unknown`):
  детерминированная слово-эвристика (неоднозначность → `unknown`);
  AI-доклассификация — только по флагу `--classify-ai` (см. issue_classify).
- Internal-инициаторы: дефолт `{andrei-shtanakov, ai-prosto}`; флаг
  `--internal` (список логинов) **заменяет** дефолтный набор целиком, а не
  дополняет его. Экосистемный конфиг — подпроект 2.
- TUI: группировка repo/инициатор (`g`), навигация (`j/k`/стрелки), выбор
  (`space`), режим worker (`x`: plan↔execute), запуск выбранных (`enter`),
  выход (`q`). Non-TUI режим `--json` для CI/диагностики.
- Побочные эффекты: консоль **никогда не пишет в целевые репозитории**;
  допустимые локальные эффекты — tmux-сессии и cache/result-артефакты внутри
  `devtools/out/`.
- tmux: имя сессии `issue-<repo>-<number>`; если сессия уже существует —
  не запускать вторую, показать сообщение с командой подключения
  `tmux attach -t <session>`. Отсутствие tmux/gh — понятная ошибка.

### issue_worker.py — обработчик одного issue

- Запускается консолью в tmux-сессии, cwd = директория целевого репо
  (execution workspace — целевой репозиторий).
- Режимы: `plan` (дефолт) — read-only анализ; `execute` — может править файлы
  и запускать тесты, но **без** commit/push/PR/merge (publish — подпроект 2).
- Гейт безопасности: `execute` для внешнего инициатора принудительно
  деградирует в `plan` **на уровне worker**, независимо от аргументов TUI
  (закреплено тестом).
- **Policy-гейт `decision`:** internal/external-политика детерминирована и
  вычисляется **кодом до вызова Codex**: внутренний инициатор → `accept`,
  внешний → `reject`. LLM не может отменить это решение; модель даёт анализ
  (`kind`, `summary`, `todo`, `next_step`) и вправе поднять `needs_human`
  при неполных данных — но не переворачивать accept↔reject.
- Результат — JSON по схеме
  `{decision: accept|reject|needs_human, kind, summary, todo, next_step,
  changed_files}` — пишется в
  `devtools/out/issues/<repo>/<number>/result.json` (НЕ в корень целевого
  репо: его рабочее дерево не загрязняется). Путь консоль передаёт worker-у
  явным абсолютным `--output-root` (из cwd целевого репо devtools/out иначе
  надёжно не найти). `out/` — в `.gitignore`.

### issue_classify.py — AI-доклассификация (новый, маленький)

- Вход: issues с `kind=unknown`; бэкенд — `codex exec` с JSON-схемой ответа.
- Формат ответа на элемент: `{"repo": "...", "number": N, "kind": "...",
  "confidence": 0.0–1.0}` (repo обязателен — batch по флоту).
- Порог: `confidence < 0.75` → тип остаётся `unknown`.
- Кэш: `out/issue-kind-cache.json`, ключ `owner/repo#number@updatedAt`
  (repo нормализован как `owner/name` — защита от коллизий имён при
  нескольких owners; изменился updatedAt → повторная классификация).
  Запись атомарная (temp-файл + rename); повреждённый кэш не ломает
  консоль — игнорируется и перезаписывается.
- Недоступный codex → типы остаются `unknown`, консоль продолжает работать.
  Без `--classify-ai` доступ к Codex не нужен (gh-сеть нужна всегда для
  загрузки issues); полностью offline работает режим `--input <json> --json`.

## Качество и стандарты PR-1

- Python ≥3.12; без новых third-party зависимостей (TUI — stdlib/curses;
  acceptance — уже запиненный plan-fields через `uv run --frozen`); типы,
  docstrings у публичных API, line length 88.
- Проверки PR-1: `py_compile`, `uv run --frozen pytest`, существующие
  make-цели. **ruff не вводим** — он не установленный стандарт этого репо;
  добавление ruff в dev-deps + конфиг — отдельное решение вне PR-1.
- Логика вынесена из curses-цикла в чистые функции (в т.ч. `sort_issues()`,
  `group_key()`), TUI-цикл в CI не гоняется.

## Тесты (acceptance)

- Классификатор-эвристика: по кейсу на каждый тип + `unknown` при
  неоднозначности.
- Acceptance-enum: все четыре значения против фикстурных TODO.md/тел issue.
- Сортировка/группировка: `sort_issues()`, `group_key()`.
- `--json` режим для CI.
- AI-кэш: новый issue без кэша → вызов AI; неизменившийся updatedAt → cache
  hit (AI не вызывается); изменившийся updatedAt → повторная классификация;
  битый кэш/недоступный codex → `unknown`, консоль работает.
- tmux: повторная сессия не запускается, выводится `tmux attach -t <...>`.
- Фильтр флота: issue репозитория без локального клона под `--root`
  не попадает в таблицу.
- Worker: схема результата валидна; путь результата —
  `<output-root>/issues/<repo>/<number>/result.json`; внешний инициатор +
  `execute` фактически получает read-only (`plan`); `decision` вычислен
  политикой до Codex (internal → accept, external → reject), LLM не может
  его перевернуть — только поднять `needs_human`.

## Оформление

Пункт TODO.md `@id:fleet-issue-console`, ветка `feat/fleet-issue-console`,
один PR. Ревью — терминальный прогон `review-pr.sh` от ai-prosto; мерж —
человек (до принятия DarkFactory-поправки).
