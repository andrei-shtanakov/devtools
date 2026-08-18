# TODO — devtools (заведён 2026-07-30)

> Роль в экосистеме: **дом fleet-агента** — сводит состояние polyrepo-workspace и
> планирует действия. Действует только **косвенно**: PR-ами в другие репо и
> `tasks.md`-спеками для spec-runner. READ-ONLY к соседним репо — инвариант №1 в
> `CLAUDE.md`. Здесь живут governance-чекеры флота (`check-plan-fields.py`,
> `check-contract-drift.sh`, `check-agent-id-conformance.py`,
> `check-graph-registry-drift.py`, `inbox.py`) и цели `make` поверх них.
> Обзор проекта и инварианты — `CLAUDE.md`, `README.md`.
>
> Файл заведён точечно, под два принятых владельцем пункта (ниже). Это **не** полный
> бэклог репо: остальная работа сюда не переносилась и не выводилась из кода — каждый
> следующий пункт требует отдельного решения владельца.

## Правила ведения

- Из инварианта №1 следует ограничение на **содержимое** этого файла: пункт здесь —
  работа над **собственным** инструментарием devtools. Работа, которую fleet-агент
  собирается отправить в соседний репо, пунктом этого плана не является: это PR или
  `inbox`-issue **там** (ADR-ECO-006).
- После выполненной задачи — маркер task-list `- [x]` (именно так, не голое `[x]`: и
  парсер, и `--selftest` распознают пункт только как элемент списка) плюс номер PR /
  хеш коммита.
- Задача стала неактуальной — зачеркнуть `~~...~~` с пометкой **почему**, не удалять:
  дельта-счётчики читают исчезновение строки как «закрыто».
- Поля пункта — инлайн-теги `@owner:` / `@blocked_by:` / `@trigger:"…"`, все
  опциональны: пустое поле означает «неизвестно» и это честнее выдуманного значения.
- Блокер пишем **канонической** формой `@blocked_by:todo://<repo>/<id>` — она
  единственная порождает ребро в графе зависимостей. Форма `<repo>#<slug>` —
  переходная: она остаётся legacy при любом написании (в том числе когда `<repo>` —
  канонический ключ манифеста), несёт только `legacy_blocker_ref`, не получает
  `resolved_target` и ребром не становится. Здесь живёт сам чекер, поэтому переходную
  форму этот файл примером не показывает.
- `@id:<node-id>` — канонический идентификатор пункта (ADR-ECO-005 PF-2B): строчная
  грамматика `[a-z0-9][a-z0-9._-]{0,63}`, из него строится URI `todo://devtools/<id>`.
- **Теги и суть пункта — на одной строке с `- [ ]`**: парсер разбирает пункт строго
  построчно и продолжений ниже не видит. Отступленные строки под пунктом — контекст
  для человека.

---

## CI собственного инструментария

- [x] Завести CI этого репо: selftest/characterization-сьют и `make plan-check` в подходящем этому репо режиме @owner:github:andrei-shtanakov @id:ci-selftest-and-plan-check — закрыт PR-ом этой ветки; приёмка = зелёный CI на самом PR
      Резолюция «подходящего режима» (2026-08-06, ревью перехода launchd→CI):
      РАЗНЫЕ режимы доказывают разное, нужны не-один. (1) PR-CI (.github/
      workflows/ci.yml): pytest (characterization + wrapper + сенсор + fixture)
      + `plan-check-selftest` — доказывает работоспособность ИНСТРУМЕНТА и
      пина plan-fields, не состояние флота. Fixture-режим — pytest-тест на
      синтетическом workspace во временном каталоге (каталог `.git` — маркер
      чекаута для discovery — незакоммитим), доказывает ОБЕ стороны детектора:
      clean=0 и stale=1 с PF-BLOCKER-STALE. (2) Локальный `make plan-check` —
      диагностика конкретного workspace; `WORKSPACE ?=` параметризован.
      (3) Scheduled fleet check (клон набора по манифесту в ephemeral
      workspace) — ОТДЕЛЬНАЯ операция и отдельный будущий пункт по решению
      владельца, сюда не входит. Actions запинены полными SHA.
      Обоснование: governance-инструмент флота обязан проверять свой пин и свой
      продакшн-путь автоматически. Иначе он гейтит соседей строже, чем себя.
      Факт на 2026-07-30: `.github/workflows/` не существует вовсе (в `.github/` лежит
      только `CODEOWNERS`), при этом проверять есть что — `tests/test_characterization.py`,
      `tests/test_wrapper_smoke.py` и `check-plan-fields.py --selftest`.
      Это не гипотетика: бамп пина `plan-fields` до 0.7.0 сломал characterization-сьют
      (5 failed, 2 passed) — API семейства `fleet` стал принимать `ManifestIndex` там, где
      тесты передавали голый `set[str]`. Поймал расхождение только ручной прогон
      (починено в PR #21, `8964aaf` + `55d044f`).
      «Подходящий этому репо режим» — не оговорка, а суть работы. `make plan-check`
      захардкожен на `--root ..` (`Makefile:49`) и читает **весь** workspace: каждое
      соседнее репо, вычекаученное рядом, плюс манифест зонтика
      `ai-orchestrators-workspace/workspace-manifest.toml`. В голом CI-чекауте одного
      `devtools` ничего этого нет, поэтому workflow, который просто зовёт
      `make plan-check`, работать не может. Проектирование фикстуры-workspace (или
      эквивалента) — работа самого пункта, а не предусловие к нему.

## Freshness-сенсор арх-evidence

- [x] Freshness/drift-сенсор: upstream-drift вендоренных prograph-схем steward + свежесть conformance-evidence WS-005 @owner:github:andrei-shtanakov @id:arch-evidence-freshness-watch — PR #26; приёмка в контексте ниже
      Исполняет scheduled-обязательство `todo://steward/arch-evidence-freshness-watch`
      («вне CI этого репо» — здесь). Семантика — из пункта steward: обе вендоренные
      схемы + пара манифест/отчёт WS-005; свежесть по `snapshot.indexed_at`, не
      `generated_at`.
      Сравнение пересчётное — prograph манифест поверхности не публикует, значит
      состав поверхности наш: до пересчёта отдельная проверка «апстрим добавил файл
      под неучтённым именем» (урок two-contract-guarantees / dispatcher#110).
      READ-ONLY (инвариант №1): при красном — inbox-issue в steward (ADR-ECO-006)
      с устойчивым ключом дедупликации `arch-evidence-freshness-watch:<status-class>`,
      чтобы ежедневный прогон не плодил новые issue; никакого автоматического
      ре-вендора.
      Результат прогона — долговечный статус-файл: `host`, `started_at`,
      `completed_at`, `next_expected_at`, разрешённые пины/манифест, статус
      прогона `clean|drift|stale|unavailable` (`stale` — evidence
      отсутствует/просрочено, `unavailable` — сравнение не состоялось),
      версия сенсора. Два слоя статусов, не смешивать: `unknown` сенсор не
      пишет никогда — это ВЫВОДИМЫЙ статус читателя. Fail-closed живёт у
      потребителя, не в факте запуска: потребитель (steward-гейт / дайджест)
      читает как unknown и `stale`/`unavailable`, и просроченный или
      отсутствующий статус-файл по `next_expected_at` — выключенный ноутбук
      не способен сам сообщить, что launchd не сработал. Так семантика
      steward «отсутствует/просрочено ⇒ unknown, не clean» выполняется на
      обоих уровнях: evidence и сам сенсор.
      Планировщик — launchd на машине-хосте, ЯВНО interim; гибрид с GitHub Actions
      отложен до появления CI у этого репо (см. `@id:ci-selftest-and-plan-check`).
      Приёмка: два штатных прогона по расписанию + четыре синтетических кейса
      (drift / stale / unavailable / added-under-excluded-name) дают различимые
      статусы; отдельно проверка стороны чтения — просроченный статус читается
      потребителем как unknown, не как последний зелёный.
      Приёмка пройдена 2026-08-06: два штатных launchd-прогона (08-05 и 08-06,
      в 09:40 по местному времени) в launchd.log, оба clean; читатель clean/exit 0;
      синтетика — 35 тестов PR #26. Показательно: prograph master уехал с пина
      (8deb730 → efb4a5d), статус остался clean — сенсор сравнивает файлы
      контрактной поверхности, а не коммиты (two-contract-guarantees, «files,
      not manifests»). Обязательство steward закрыто встречным PR там же.

## Fleet plan-check по расписанию

- [ ] Scheduled fleet plan-check: DAILY-прогон настоящего кросс-репного чекера над свежим клоном флота @owner:github:andrei-shtanakov @trigger:"после появления общего scheduled-run status/freshness-контракта в переходе launchd→CI" @id:scheduled-fleet-plan-check
      Решение владельца 2026-08-06. Роль ≠ Robin (инвариант №2 соблюдён,
      различие названо): Robin ежедневно ОБЪЯСНЯЕТ потенциальный застой
      прозой; plan-check МАШИННО проверяет полный PF-контракт и выдаёт
      воспроизводимый verdict/exit code — observability → enforcement
      evidence. DAILY, не weekly: недельное окно слишком велико для
      PF-BLOCKER-STALE (ошибочная зависимость тормозила бы работу шесть
      дней); клоны публичных репо и прогон дёшевы.
      Механика: workflow читает workspace-manifest.toml, клонирует
      заявленный набор в ephemeral workspace, гоняет
      `make plan-check WORKSPACE=$tmp` (параметризация уже есть).
      Ограничители v1: красный Actions run + job summary + artifact, БЕЗ
      автоматических кросс-репных issues; workflow_dispatch для ручного
      прогона; concurrency cancel-in-progress; независимый reader проверяет
      freshness последнего run и выводит unknown при молчании schedule;
      Robin читает этот машинный verdict, а не пересчитывает PF-семантику.
      Триггер НАМЕРЕННО не привязан к снятию launchd-plist: это разные
      обязательства; общим должен быть способ публикации и контроля
      молчания (status/freshness-контракт из перехода launchd→CI), а не
      искусственная зависимость от удаления plist.
      Триггер СРАБОТАЛ 2026-08-08 (контракт envelope + паттерн workflow —
      переход launchd→CI завершён). Реализация: `clone_fleet.py` (клон
      набора по манифесту; общие git_dir дедуплицируются; недоступный
      remote = громкий exit 3 — молча уменьшенная поверхность читалась бы
      как чистая) + `.github/workflows/fleet-plan-check.yml` (cron 06:20
      UTC, envelope `fleet-plan-check-run/v1`, verdict только по exit
      чекера). Живой ephemeral-смоук на реальном манифесте: 15 клонов,
      0 failed, plan-check 0 err/0 warn. Приёмка до закрытия пункта:
      dispatch-smoke + synthetic=stale (красный run + PF-BLOCKER-STALE в
      логе + envelope/artifact) + два штатных cron-прогона; затем
      reader-запрос в robin (расширение паттерна robin-runtime#42).

## Состояние issue-блокеров (обратное плечо ADR-ECO-006)

- [x] Резолвить состояние issue-блокеров `@blocked_by:<repo>#<number>` через GitHub: закрытый/вмерженный target у открытого пункта → PF-BLOCKER-STALE @owner:github:andrei-shtanakov @id:blocker-issue-state-resolution — PR #41; приёмка в контексте ниже
      Принят из inbox-issue devtools#40 (слаг совпадает; инициатор —
      prograph-vault, правило cross-repo-waits, там PR #72). До этого
      issue-форма матчилась только текстуально slug-графом — её СОСТОЯНИЕ не
      резолвилось, и закрытие inbox-issue получателем не будило инициатора:
      обратное плечо ADR-ECO-006 существовало только для todo://-рёбер.
      Реализация — в самом wrapper-е `check-plan-fields.py`, не в пакете
      (plan-fields живёт в dispatcher, сосед read-only): numeric-фрагмент
      рефа = issue; owner/name — из `repo_url` манифеста (SSOT, никаких
      догадок); состояние — `gh issue view --json state`. Issue-рефы изъяты
      из legacy slug-графа (exclude) и из канонического
      PF-LEGACY-AMBIGUOUS-шума: форма принадлежит issue-резолверу. Живой
      смоук показал, что gh резолвит и номера PR (state MERGED) — MERGED
      тоже завершает ожидание, stale = всё, что не OPEN.
      Fail-honest (two-contract-guarantees): недоступный резолв — нет gh /
      auth / сети / repo_url — даёт явный warning
      [DT-ISSUE-STATE-UNAVAILABLE], не clean. Открытый issue →
      waiting-by-blocker; закрытый → ERROR класса PF-BLOCKER-STALE +
      movement stale-condition, наравне с todo://-ребром.
      Приёмка (синтетика из issue): closed→ERROR, open→waiting-by-blocker,
      unavailable→явный UNAVAILABLE — tests/test_issue_blockers.py +
      расширенный `--selftest`; резолвер инжектируется, сеть в тестах не
      используется. Закрыть devtools#40 после мержа — это и есть сигнал
      инициатору.

## Грамматика полей плана

- [x] Нормализовать `@owner`: типизированная operational-семантика и раздельный fleet-reporting @owner:github:andrei-shtanakov @id:owner-grammar-semantics — ADR-ECO-005a + plan-fields v2/v0.8.1 + этот PR
      Канон различает `github:*`/`github-team:*` (`human-owned`),
      `repo:<manifest-key>` (`repo-owned`), `TBD`, отсутствие, invalid owner и
      неизвестный repo owner. Governance `owner_role` DEC-007 остаётся отдельной
      плоскостью. Devtools удалил локальный `STRICT_OWNER`, использует публичный
      `plan_fields.parse_owner()` и публикует ownership/movement totals плюс
      полную матрицу. Trigger/blocker не меняет owner bucket; сумма матрицы равна
      числу открытых пунктов. Массовое назначение личного handle не выполнялось:
      честный `missing + actionable` сохранён для следующего triage; известные
      legacy-значения мигрируются отдельными малыми PR после доставки Robin.

## Гейт Check 2 на dev-зеркале agents-catalog

- [x] Решить судьбу Check 2 по `_cowork_output/contracts/agents-catalog.toml` — зеркала без репо-владельца @owner:github:andrei-shtanakov @id:check2-cowork-mirror-gate — PR #37, вариант (б)
      Решение владельца 2026-08-07: **вариант (б)** — Check 2 сужен до
      `arbiter/config/agents-catalog.toml`. Обоснование выбора: зеркало лежит вне
      репозитория atp-platform, поэтому его релизный шаг не может владеть файлом в
      чужом workspace надёжно и атомарно — вариант (а) создал бы фиктивного
      владельца и вернул то же «красное без адресата».
      Итоговый контракт: гейтится только vendored-копия, принадлежащая arbiter;
      `_cowork_output/contracts/` остаётся коммуникационным snapshot БЕЗ гарантии
      свежести; его дрейф больше не роняет conformance. Отставшую копию можно
      обновить отдельно — это уже не условие зелёного чекера.
      Заведён по решению владельца 2026-08-07 (правило ведения: каждый пункт —
      отдельное решение). Пункт про СВОЙ инструмент — `check-agent-id-conformance.py`,
      не про правку соседнего репо.
      Факт на 2026-08-07: чекер красный на `[2] workspace-mirror (contracts/)
      drifted from SSOT`, и это ВЕРНОЕ срабатывание. Зеркало отстало: SSOT
      `atp-platform/method/agents-catalog.toml` обновлён 2026-07-16
      (atp-platform#253), копия в `_cowork_output/` — 2026-07-14. Расхождение
      содержательное, не косметическое: `opencode` в SSOT уже `routable = true`
      (PROMOTED 2026-07-03, gate 003a D4, rank 0.915), в зеркале ещё `false` с
      комментарием «флип только после кроссовера» — то есть зеркало показывает
      снятое ограничение как действующее.
      Ре-вендоринг снимает красноту на один раз, но не проблему. По ADR-ECO-003b
      (строка 111) `_cowork_output/contracts/` — «a dev mirror, NOT the source»;
      живёт в корневом репо, где PR-потока нет, и ни за кем не закреплён. Значит
      отстанет снова при следующей правке каталога, и Check 2 будет краснеть
      циклически — сигнал обесценится, а обесцененный гейт хуже отсутствующего.
      Развилка: (а) закрепить ре-вендоринг зеркала за релизным шагом каталога на
      стороне atp-platform — у артефакта появляется владелец, гейт осмыслен;
      (б) убрать dev-зеркало из Check 2, проверять только `arbiter/config/`, у
      которого владелец есть — зеркало остаётся коммуникационным артефактом без
      гейта. Вариант (б) сужает Check 2 до одной копии: это осознанная потеря
      покрытия, а не упрощение.
      Приёмка: Check 2 либо гейтит артефакт с названным владельцем и порядком
      обновления, либо перестаёт его гейтить — но не остаётся в состоянии
      «красный без адресата».
      Приёмка пройдена: `check-agent-id-conformance.py` даёт exit 0, все пять
      проверок зелёные, `[2] vendored copies match SSOT byte-for-byte (arbiter)`.
      Сужение ничего не замаскировало: копия arbiter совпадает с SSOT
      побайтово — красным был исключительно дрейф зеркала.

## SSOT-фикстуры conformance каталога (PP-103 acceptance (b))

- [x] SSOT-набор conformance-фикстур каталога для трёх загрузчиков (Maestro / ATP / arbiter) под единым owner-путём @owner:github:andrei-shtanakov @id:catalog-conformance-single-owner — PR #44 (набор, merge 2a5c154) + PR #45 (ожидание) + PR #46 (закрытие); приёмка в контексте ниже
      Принят из inbox-issue devtools#43 (инициатор — impresario, PP-103
      acceptance (b), слаг совпадает). Формализует уже записанного владельца:
      arbiter пометил свой пункт `@owner:repo:devtools`
      (`@id:catalog-conformance-fixtures`); Maestro shared-PyPI-lib
      (`@id:catalog-loader-shared-lib`) остаётся отдельной опцией и этим
      путём не блокируется.
      Реализация: `contracts/catalog-conformance-fixtures/v1/` — фикстуры
      (valid / invalid V1–V5 / warn V6–V7 / parse-error), машинные
      `expectations.toml` (+ pathres-сценарии слоя `$ATP_CATALOG` по
      ADR-ECO-003b D2), `manifest.json` (sha256 + tree_sha256 — пин-поверхность
      для copy-integrity потребителей), README с семантикой классов и
      зафиксированными дивергенциями загрузчиков на 2026-08-17.
      Owner-side QA: `check-catalog-fixtures.py --check` — stdlib
      референс-валидатор V1–V7 исполняет каждое ожидание локально;
      tests/test_catalog_fixtures.py доказывает и невакуумность чекера.
      Вне v1 намеренно: XDG-слои (до maestro xdg-catalog-path),
      негативный кейс one-vendor-per-model (Maestro
      models-duplicate-vendor-detection), alias/precedence (shared-lib).
      Условия готовности (из issue): (1) набор опубликован с версией/пином —
      PR #44, merge 2a5c154 (2026-08-17); (2) три сьюта зелёные на одном
      наборе — wiring-issues maestro#188 / atp-platform#292 / arbiter#74
      (слаг catalog-conformance-wiring) закрыты COMPLETED 2026-08-18:
      maestro PR #189, atp-platform PR #293, arbiter PR #75 — все смержены с
      зелёным CI, вендоренные копии с PIN на 2a5c154 + manifest.json
      сверены на master всех трёх; (3) fail-closed наблюдаем — owner-side
      tests/test_catalog_fixtures.py; arbiter дополнительно проверил кейсы
      мутациями (поочерёдное отключение V1–V7 краснит ровно свой кейс).
      Бонус приёмки: набор сразу нашёл реальное расхождение — обязательный
      `harnesses.*.shim` в схеме arbiter (не входит в V1–V7; стал Option,
      arbiter#74). Обратное плечо тоже показало себя: закрытие трёх
      wiring-issues дало здесь ровно 3×PF-BLOCKER-STALE — «ожидание
      доставлено», теги сняты этим PR-ом. devtools#43 закрыт как сигнал
      инициатору (ADR-ECO-006).

- [ ] Аддитивное расширение v1: кейсы на пустую плоскость harnesses и V7-only-kind @owner:github:andrei-shtanakov @id:catalog-conformance-v1-gaps @blocked_by:maestro#192 @blocked_by:atp-platform#294 @blocked_by:arbiter#76
      Принят из inbox-issue devtools#47 (инициатор — maestro, из его
      wiring-разбора maestro#189; слаг совпадает). Две развилки, где v1 не
      выносил решения и загрузчики могли разойтись молча.
      Канон-решения (обоснование — README набора, раздел «Пограничные
      решения»): (1) пустая плоскость `[harnesses]` при непустых `[[agents]]`
      → V1 fail-closed (совпадает с фактическим поведением ATP и arbiter;
      формулировка V1 не оставляет пустой таблице ничего объявленного;
      `harnesses` опционален — scaffolding-хедер не нужен); пустая плоскость
      без агентов валидна. (2) V7 получает kind-only фикстуру: старая
      варьирует status+kind разом и pydantic-загрузчики проходят её схемой
      по status, не проверив kind.
      Фикстуры: invalid/v1-empty-harnesses.toml, warn/v7-unknown-kind.toml;
      manifest перегенерён (14 файлов); канон закреплён регресс-тестом
      test_empty_harnesses_plane_is_fail_closed. Maestro заранее заявил обе
      фикстуры у себя красными — это рабочий режим набора.
      Приёмка (из issue): кейсы в v1 + manifest — ВЫПОЛНЕНО: PR #48,
      merge 2533ff7 (2026-08-18); потребители обновляют пин осознанным
      PR-ом и показывают новые негативные кейсы красными при принятии —
      ожидание = @blocked_by-теги выше: pin-bump issues maestro#192 /
      atp-platform#294 / arbiter#76 (слаг у всех
      catalog-conformance-pin-bump-v1-gaps, пин 2533ff7) заведены
      2026-08-18. Закрытый issue у соседа даст PF-BLOCKER-STALE — проверить
      его сьют и снять тег; после всех трёх закрыть пункт и devtools#47.
