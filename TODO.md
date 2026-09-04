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

- [ ] Scheduled fleet plan-check: DAILY-прогон настоящего кросс-репного чекера над свежим клоном флота @owner:github:andrei-shtanakov @trigger:"после появления общего scheduled-run status/freshness-контракта в переходе launchd→CI" @id:scheduled-fleet-plan-check @epic:eco.tooling
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

- [x] Аддитивное расширение v1: кейсы на пустую плоскость harnesses и V7-only-kind @owner:github:andrei-shtanakov @id:catalog-conformance-v1-gaps — PR #48 (кейсы, merge 2533ff7) + PR #49 (ожидание) + PR этого пункта (закрытие); devtools#47 закрыт
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
      arbiter#76 ПРИНЯТ 2026-08-18 (PF-BLOCKER-STALE отработал, тег снят):
      PR #77 merged 84114ef, CI зелёный, PIN на master = 2533ff7, оба новых
      кейса проверены мутацией, загрузчик не правился.
      maestro#192 и atp-platform#294 ПРИНЯТЫ 2026-08-18 (второй
      PF-BLOCKER-STALE ×2, теги сняты): maestro PR #193 merged 0285bcd —
      выбрал ПОЧИНИТЬ обе развилки, не остаться красным (пустая плоскость →
      V1 по «ключ объявлен»; kind — против интерим HARNESS_KINDS до
      vocabulary.toml); atp-platform PR #295 merged 6509727 — V7-kind стал
      CatalogWarning. PIN у обоих 2533ff7; maestro CI зелёный полностью,
      у atp на смерженном коммите пара integration-джобов ещё бежала в
      момент проверки (остальное зелёное). Все три сьюта на пиненом наборе
      без известных дивергенций, кроме maestro missing-file (осознанная,
      зафиксирована в README). devtools#47 закрыт как сигнал инициатору.

- [x] README набора: раздел дивергенций — штампы «дата + коммит наблюдения», обновить устаревший статус ATP и Maestro @owner:github:andrei-shtanakov @id:catalog-conformance-readme-atp-divergences-stale — PR #52 (merge 2f39946); devtools#50 закрыт

- [x] Машиночитаемый словарь enum'ов ADR-ECO-003 в наборе (vocabulary.toml) @owner:github:andrei-shtanakov @id:catalog-enum-vocabulary-machine-readable — PR #54 (merge 070acdc); devtools#51 и devtools#47 закрыты

## Слепые зоны plan-check (umbrella-замер 2026-08-18)

- [x] Legacy-slug блокер на @id'd пункте: закрытая цель молчала — сделать находку @owner:github:andrei-shtanakov @id:legacy-blocker-stale-silent — PR #59 (merge 7245b4b); devtools#56 закрыт, maestro уведомлён (maestro#196)
      Принят из inbox devtools#56 (umbrella-сессия; слаг совпадает). Причина
      найдена в разделении труда двух пайплайнов: пакетный legacy-граф
      намеренно пропускает @id-источники («их рефы — дело канонического»),
      а канонический из legacy-формы ребра не строит — квадрант
      «@id-источник × legacy-реф × закрытая цель» не проверял никто,
      вопреки докстрингу враппера. Фикс на стороне враппера (прецедент
      devtools#41): check_id_source_legacy_stale, stale-only (остальные
      исходы уже даёт канонический PF-LEGACY-AMBIGUOUS nudge), severity
      warning — как у всех legacy-находок (решение владельца: НЕ error,
      форма без стабильной identity толкает миграцию на @id, не валит
      билд). Матчер слага переиспользован из пакета (_slug_hits_item),
      чтобы семантика «слаг называет пункт» не разъехалась.
      Приёмка (из issue): двусторонняя синтетика (цель закрыта → находка,
      открыта → нет) — tests/test_plan_check_detectors.py + selftest;
      докстринг теперь честен, cross-repo-waits трогать не нужно.
      Боевой прогон сразу дал два реальных срабатывания: maestro:131,132
      ждут `arbiter#R-07`, который arbiter давно завершил — ровно тот
      класс молчаливо протухших ожиданий, о котором issue.

- [x] Детектор тега не на строке чекбокса + разорванный @trigger @owner:github:andrei-shtanakov @id:plan-tag-on-continuation-line — PR #59 (merge 7245b4b); devtools#57 закрыт, находки соседям: disputatio#21, kapelle#29
      Принят из inbox devtools#57 (инцидент impresario: два доставленных
      ожидания PP-103 были невидимы всей машинерии, резолвер devtools#41
      не получал ни одного входа при честных «0 errors»). Фикс:
      check_tag_placement — warning DT-TAG-ON-CONTINUATION на строку-
      продолжение, НАЧИНАЮЩУЮСЯ с одного из четырёх тегов (упоминания в
      прозе не флагуются — иначе шум на половине флота); разорванный тег
      `@trigger:"…"` без закрывающей кавычки на строке чекбокса — своя
      находка DT-TRIGGER-UNTERMINATED. Боевой прогон сразу нашёл 5
      реальных невидимых @id (disputatio ×3, kapelle ×2) и два
      false-positive переноса прозы в нашем же TODO.md (перечитаны
      бэктиками в этом же PR — включая перенос в тексте этого пункта).
      Приёмка (из issue): синтетика тег-на-продолжении/тег-на-чекбоксе,
      все четыре тега, разорванный trigger — покрыто тестами + selftest.

- [x] Ложный PF-LEGACY-AMBIGUOUS на закрытом пункте с issue-form блокером @owner:github:andrei-shtanakov @id:blocker-issue-form-on-closed-item — PR #59 (merge 7245b4b); devtools#58 закрыт
      Принят из inbox devtools#58. Диагноз из issue подтверждён: исключения
      для slug-матчера строились из collect_issue_refs (только открытые
      пункты — для резолюции это верно), закрытый пункт выпадал и его
      числовой реф читался как legacy-slug. Фикс: issue_ref_exclusions
      сканирует ВСЕ пункты (конвенция флота хранит @blocked_by на [x] как
      историю происхождения — прибор не смеет толкать чистить леджер);
      резолюция состояния по-прежнему только для открытых. Настоящий
      slug-реф (нечисловой) на закрытом пункте по-прежнему даёт
      PF-LEGACY-AMBIGUOUS. Приёмка (из issue): три стороны покрыты
      tests/test_plan_check_detectors.py + selftest; флот после фикса —
      0 errors (warnings — только реальные находки нового детектора #57).
      Принят из inbox-issue devtools#51 (инициатор — maestro, из их pin-bump
      работы; слаг совпадает — maestro уже ждёт по тегу
      `@blocked_by:devtools#catalog-enum-vocabulary-machine-readable`).
      Проблема: словарь status/kind существовал только прозой (инлайн-
      комментарий в ADR-ECO-003 + README набора), три загрузчика копируют
      руками — риск №1 ADR-ECO-003b этажом выше, на уровне вокабуляра;
      выпавшее значение ненаблюдаемо (v7-кейсы ловят только лишние).
      Реализация: vocabulary.toml в v1 (производный от ADR, источник в
      комментарии; в пин-поверхности manifest — 16 файлов); референс-
      валидатор читает файл вместо констант (второй копии в devtools
      больше нет); valid/vocabulary-roundtrip.toml использует каждое
      значение словаря — выпавшее значение красит valid-кейс, лишние ловят
      v7-*; вместе множество наблюдаемо как точное. Аддитивность:
      добавление значения — v1, удаление/переименование — v2 (в README).
      Приёмка (из issue): словарь в v1 + в манифесте + потребитель может
      заменить рукописную константу чтением вендоренного файла — закрыть с
      мержем PR; devtools#51 закрыть тогда же (это сигнал maestro удалить
      их интерим HARNESS_KINDS/MODEL_STATUSES — у них свой пункт с этим же
      слагом). Отдельных pin-bump issues не заводить: открытые maestro#192
      и atp-platform#294 подхватят свежий пин естественно.
      Принят из inbox-issue devtools#50 (инициатор — atp-platform, из их
      pin-bump работы; слаг совпадает). Раздел кэшировал чужой статус без
      возраста утверждений и протух молча за сутки: ATP реализовал V1–V6 в
      wiring #293 (проверено по atp-platform@6678f3e), Maestro — V1–V6 в
      wiring #189 (проверено по maestro@b244e89); обе записи 2026-08-17
      стали ложными. Принято предложение инициатора: каждое утверждение
      несёт дату наблюдения + коммит потребителя; раздел явно объявлен
      наблюдательным снапшотом (контракт — только expectations.toml),
      устаревание = дефект документации, welcome inbox-issue.
      README — часть пин-поверхности: manifest перегенерён.
      Ничего не блокирует (сьют ATP зелёный на 2533ff7) — закрыть с мержем
      PR; devtools#50 закрыть тогда же.

## Догоняющая волна re-vendor промпта review-kit

- [x] Догоняющая волна re-vendor промпта кита — линза ослабления тестов (steward#129): промпт → 22 репо, caller-yml → 6 @owner:github:andrei-shtanakov @id:review-kit-prompt-lens-wave — PR #70 (приём + своя копия) + 21 PR соседей, все вмержены 2026-08-28; devtools#69 закрыт
      Принят из inbox-issue devtools#69 (инициатор — steward, слаг совпадает;
      встречное ожидание — steward#130). Волна 2026-08-27 разнесла кит
      @ e4c43cc ДО мержа steward#129; штатная drift-вахта разъезд не ловит —
      сверяет только 6 файлов PIN, промпт и caller-yml вне перечня по
      конструкции. Источник волны: steward @ ee6d85a (master, 2026-08-28).
      Содержимое: линза ослабления тестов в §4 промпта (снятый assertion /
      skip на живом тесте / сужение параметризации / выключенная проверка в
      конфиге без равноценной замены = минимум major; скоуп — охраняемое
      поведение остаётся в дереве; line:0 для чистого удаления; утрата
      покрытия живого поведения в определении major) + в caller-yml довод
      steward#124 «потолок timeout-minutes ≠ гарантия в аварию Actions»
      (только комментарий). PIN и схема НЕ трогаются: скрипты/схема между
      e4c43cc и ee6d85a не менялись (решение «не расширять состав PIN» —
      за steward, не за волной).
      PR-ы волны (22): deployer#46, atp-platform-testing#2,
      discovery-toolkit#9, discovery#26, disputatio#46, github-checker#28,
      impresario#41, libretto#33, proctor#57, prograph-vault#107,
      prograph#39, research-bench#28, robin-runtime#57, robin-toolkit#8,
      spec-runner-vscode#31; с caller-yml: arbiter#95, atp-platform#310,
      dispatcher#212, kapelle#40, maestro#230, spec-runner#323; devtools —
      PR этой ветки.
      Приёмка (из issue): после мержей вендор-копии промпта байт-совпадают
      со steward HEAD (sha256 27792de2…); закрыть пункт и devtools#69 после
      посадки волны на default-ветки.
      Приёмка ПРОЙДЕНА 2026-08-28: все 22 PR вмержены; сверка по
      origin/<default> всех 22 репо — prompt 22/22 и caller-yml 6/6
      байт-совпадают со steward HEAD (steward origin/master = ee6d85a,
      не уехал). Ветки волны удалены на origin, локальные клоны обновлены
      ff-only (prograph-vault master — известное waived-расхождение,
      не трогалось; arbiter оставлен на своей рабочей ветке, fetch only).
      Известные красные чеки НЕ волны: review/report у 6 caller-репо —
      пустой баланс OpenAI; impresario governance/gate — pre-existing.

## Salvage-скан флота

- [x] Salvage-скан флота: orphan worktrees / ветки без PR / unpushed default / stale locks @owner:github:andrei-shtanakov @id:fleet-salvage-scan — PR #68; devtools#67 закрыть после мержа
      Принят из inbox-issue devtools#67 (инициатор — ecosystem-kb,
      harvesting-волна №2; слаг совпадает). Детерминированный read-only
      `salvage_scan.py` (`make salvage`) по набору манифеста зонтика:
      четыре класса обломков, ловившихся руками 25–28.08. Таблица
      «репо · класс · объект · возраст» + host (инвариант 5); нормальный
      пустой результат молчит (stdout пуст, exit 0).
      Осознанные исключения — WAIVERS (repo, класс, префикс объекта):
      скан ПОМЕЧАЕТ `[waived: …]`, не чинит и не скрывает; только waived →
      exit 0. Записаны оба лица исключения волта: unpushed master
      (снапшот-коммиты, ждёт dispatcher#199) и delivery-ветка
      `derived-snapshots` без PR by design (резолюция ecosystem-kb#98).
      Fail-honest: gh недоступен → ветки показываются с пометкой
      «PR state unknown», не пропадают; ненайденный чекаут — заметка в
      stderr (судьба набора — check-release-drift).
      Приёмка: синтетика всех четырёх классов + waiver-семантика + молчание
      на чистом флоте — tests/test_salvage_scan.py (23 теста); живой прогон
      сразу дал реальные находки: orphan worktree research-bench (32d),
      6 веток-кандидатов в 4 репо, оба waived-лица волта помечены.

## Дедуп терминального ревью (steward#126, обвязка)

- [x] review-pr.sh: наследование вердикта по отпечатку входа (fp-режим кита) @owner:github:andrei-shtanakov @id:review-pr-fp-inherit — PR #73 (merge 6d3604f); devtools#72 закрыт
      Боевое крещение при приёмке: инструмент отревьюировал СВОЙ PR тремя
      прогонами и нашёл в себе две реальные дыры (fetch базы без
      destination-refspec — зависимость от refspec-конфигурации remote'а;
      same-head-наследование без финальной сверки головы — exit 0 для
      неревьюенного head в гонке с пушем). Обе закрыты с регрессионными
      тестами до мержа; финальный вердикт --approve опубликован от
      ai-prosto с fp-маркером — первый наследуемый вердикт флота.
      Follow-up отдельным решением владельца: pin-bump вендоренного кита
      devtools до fp-версии steward (fee3159+), чтобы дедуп работал и на
      наших PR; замер экономии — за steward.
      Принят из inbox devtools#72 (инициатор — steward, кит-половина влита
      steward PR #132, fee3159; слаг совпадает; дизайн-контракт из 7 пунктов
      утверждён владельцем 2026-08-28 в теле issue). Реализация — целиком по
      контракту: (1) feature-detect по литералу --fingerprint-only в
      local.sh целевого репо (паттерн --generated-list), нет флага → полный
      прогон без дедупа, ре-вендор флота не пререквизит; (2) база освежается
      явным fetch ДО отпечатка, оба вызова кита без --fetch; (3) stdout-
      контракт fp-режима: 64-hex → дедуп, пусто/0 → полный прогон, 2/3 →
      отказ без публикации; (4) маркер аддитивно расширен fp=<64hex>;
      (5) наследование только из НОВЕЙШЕГО ревью $REVIEW_LOGIN, полностью
      распарсенного (один строгий маркер, APPROVED→0/CHANGES_REQUESTED→1);
      DISMISSED/неизвестное/битый маркер — miss ЦЕЛИКОМ, в более старые
      ревью не заглядываем (dismissal — человеческий отзыв вердикта,
      воскрешать его из истории дедуп не имеет права); (6) fp+head совпали →
      exit унаследованным кодом без публикации; fp совпал, head новый →
      публикация того же действия телом-наследованием, codex не зовётся;
      наследуются и зелёные, и красные; (7) --fresh обходит только поиск,
      fp вычисляется и публикуется всегда.
      Приёмка (из issue): повторный прогон по байт-идентичному входу не
      зовёт codex и печатает «вердикт унаследован» — синтетика
      tests/test_review_pr.py (15 новых тестов, фильтр jq гоняется через
      настоящий jq в стабе gh). Фаза замера экономии — за steward.

- [x] review-pr.sh: кэш дедупа мёртв на живом gh (--slurp+--jq отвергается) @owner:github:andrei-shtanakov @id:review-pr-fp-slurp-compat — PR #76 (merge 87b7e76); devtools#75 закрыт после живого инцидента наследования (steward#135: полный прогон → approve с fp-маркером; повторный dry-run на том же head → «вердикт унаследован» за 10.3 сек без codex)
      Принят из inbox devtools#75 (инициатор — steward, первая живая
      проверка их стороной, steward PR #134; слаг совпадает). Дефект
      интеграции #72: gh 2.83.1 отвергает комбинацию --slurp + --jq,
      поиск наследуемого вердикта падал ВСЕГДА — fail-open в полный прогон
      (направление верное), но кэш мёртв и это молчаливо. Синтетика не
      поймала: стаб gh игнорировал флаги. Воспроизведено на живом gh
      дословно.
      Фикс: --slurp убран, фильтр гоняет внешний jq (jq -s даёт тот же
      shape страниц); gh и jq вызываются раздельно — отказ каждого виден
      со своей причиной (gh/jq в тексте заметки), не маскируется пайпом
      под «нет ревью»; нет jq → явная заметка и полный прогон. Стаб gh
      теперь сам отвергает --slurp, как живой gh — регресс назад красит
      сьют. Предложение «различать мёртвый кэш от разового промаха
      счётчиком N прогонов» отклонено: требует состояния между прогонами,
      инструмент намеренно stateless; вместо этого причины отказа стали
      явными и различимыми.
      Приёмка (из issue): «повторный dry-run на неизменном head отвечает
      "вердикт унаследован" без codex» — синтетика есть; вживую пайплайн
      проверен на реальных данных: steward#134 → APPROVED+head+fp
      извлечены, devtools#73 (старый маркер без fp) → законный miss.
      Полный живой инцидент наследования — на первом открытом PR репо с
      fp-китом (сейчас открытых PR во флоте нет).

## Волна fp-кита терминального ревью

- [x] Re-vendor fp-версию review-kit по флоту, чтобы дедуп `review-pr.sh` не отключался feature-detect'ом @owner:github:andrei-shtanakov @id:review-kit-fp-wave — devtools PR #83 + 22 PR волны, все вмержены 2026-08-28; devtools#79 закрыть после мержа ledger-PR
      Принят из inbox devtools#79 (инициатор — steward, слаг совпадает).
      Источник минимальной волны — steward master не ниже fee3159: только
      `scripts/review/local.sh` с `--fingerprint-only`; схема вердикта,
      apply-threshold и прочие части кита не меняются. Изменение caller-yml
      из steward#137 в эту волну не входит: opt-in автотриггера — отдельное
      policy-решение, не условие работоспособности fp-дедупа.
      Из собственного репо devtools изменение уходит обычным PR; соседние
      репо остаются READ-ONLY и получают отдельные PR волны. Приёмка: все
      vendored-копии `local.sh` байт-совпадают с выбранным steward source;
      в каждом репо fp-команда печатает ровно одну строку 64-hex, а первый
      терминальный review публикует маркер с `fp=`.
      Приёмка ПРОЙДЕНА: 23 потребителя + steward на origin/default имеют
      byte-identical `local.sh` sha256 32d1e084…; PIN каждого потребителя
      обновлён только для этой поверхности. PR-ы волны: workspace#29,
      arbiter#99, atp-platform#313, atp-platform-testing#3, deployer#48,
      discovery#28, discovery-toolkit#11, dispatcher#219, disputatio#48,
      github-checker#30, impresario#45, kapelle#43, libretto#35,
      maestro#232, proctor#60, prograph#41, prograph-vault#114,
      research-bench#30, robin-runtime#59, robin-toolkit#10,
      spec-runner#325, spec-runner-vscode#33. Ветки волны удалены локально
      и на origin; чистые default-checkout обновлены. Dirty checkout
      atp-platform-testing-en намеренно не тронут, его origin/master уже
      содержит мерж.

## Передача dry-run вердикта в боевой прогон

- [x] review-pr.sh: opt-in перенос проверенного dry-run verdict без второго вызова codex @owner:github:andrei-shtanakov @id:review-pr-inherit-dry-run — PR #82 (merge bf89814); devtools#80 закрыт автоматически
      Принят из inbox devtools#80 (инициатор — steward, слаг совпадает).
      Выбран явный файловый канал: `--dry-run --write-verdict <file>`
      атомарно пишет версионированный машиночитаемый результат, а
      `--use-verdict <file>` рассматривает его как кандидата только при
      точном совпадении PR head и полного fp. Повреждённый файл,
      неизвестная версия или любое несовпадение дают явный miss и обычный
      полный прогон; без новых флагов инструмент остаётся stateless и
      обратно совместимым. Файл не публикуется, не становится глобальным
      кэшем и не переиспользуется неявно.
      Приёмка: неизменные head+fp публикуют тот же review без второго
      вызова codex; изменение головы или любого байта fingerprint-входа
      гарантированно вызывает полный прогон; обе ветви покрыты тестами.
      Приёмка ПРОЙДЕНА: envelope v1 атомарно пишется mode 0600 и связан с
      repo/pr/head/fp, action/code и hash тела; повреждение или mismatch
      принудительно отключают также GitHub-наследование и ведут в полный
      прогон. 187 тестов зелёные; терминальное ревью нашло две реальные
      major-дыры в ранних выходах, обе закрыты регрессиями до approve.

## Fleet issue console (PR-1)

- [x] Fleet issue console (PR-1): TUI открытых issues флота (acceptance/kind/группировка) + запуск изолированных tmux-worker-ов без publish @owner:github:andrei-shtanakov @id:fleet-issue-console — спека docs/superpowers/specs/2026-08-30-fleet-issue-console-design.md; PR #85

## Behaviour-spec pipeline

- [x] Governance-ядро (этап A): пин steward + characterization, merge_gate, stale-адаптер, bundle_state @owner:github:andrei-shtanakov @id:behaviour-governance-core — спека docs/superpowers/specs/2026-08-30-behaviour-spec-pipeline-design.md (v4, GO); PR #87
- [x] Runner + console (этап B): S0–S8, waiting_human_merge/merged_unverified, textual-TUI @owner:github:andrei-shtanakov @id:behaviour-runner — B1 PR #88, B2 PR #89
- [x] inbox-issue в disputatio: режим полировки одиночного документа (OQ-1; спека называла --mode document) @owner:github:andrei-shtanakov @id:disp-document-mode-issue — заведён disputatio#52 (slug: single-document-polish-mode), 2026-08-31
- [x] Pin-bump вендоренной steward actor-policy (contracts/steward-actor-policy/v1) после появления github:ai-prosto в agent_identities — включает agent-мерж S7 runner'а @owner:github:andrei-shtanakov @id:behaviour-s7-actor-policy-pin-bump — PR этой ветки; steward#139 доставлен PR-ом steward#142 (merge 2c71ed7)
      Заведён 2026-08-31 вместе с inbox-issue steward#139 (slug:
      agent-identities-ai-prosto). Steward доставил в тот же день И включил
      agent_merge_allowed: true; перепин на steward@6a70d15ba58 даёт
      Safety(agent_merge_allowed=True, actor_class='agent') — S7 мержит
      document-PR сам; authority-root и fail-closed evidence — по-прежнему
      человек (ADR-ECO-011).
- [x] Мигрировать S4 runner'а с internal content-check API на публичный prospective-режим gate-check @owner:github:andrei-shtanakov @id:behaviour-s4-candidate-mode-migration — PR #101 (merge 549638a): CLI --candidate + пин steward 2c71ed7; локальные гарды GC-UNPINNED/GC-STALE/GC-DSL-EMPTY поверх (5 кругов приёмки)
      Заведён 2026-08-31 вместе с inbox-issue steward#140 (slug:
      gate-check-candidate-mode). ОЖИДАНИЕ ДОСТАВЛЕНО тем же днём (steward
      PR #142, merge 2c71ed7): candidate-режим появился (blob_hash_of —
      SHA-1-формат; arch-policy.yaml в candidate-режиме обязателен) — тег
      @blocked_by снят, пункт actionable. Обходной путь — три пинованных
      символа steward (collect_bundle / check_behaviour_spec /
      build_trace_matrix, спека §3); миграция снимет зависимость от
      внутренней структуры пакета. Требует бампа ПАКЕТНОГО пина steward
      (pyproject rev 4a1c7c44) — отдельным осознанным PR-ом с прогоном
      characterization-сьюта.
- [x] Включить авто-опровержение ложного класса «файлов нет» в S6 runner'а по машинному типу находки кита @owner:github:andrei-shtanakov @id:behaviour-s6-file-missing-auto-refute — PR этой ветки: ре-вендор кита @ 2c71ed7 (схема v2, kind), одна авто-попытка на цикл, смешанный вердикт — человеку
      Заведён 2026-08-31 вместе с inbox-issue steward#141 (slug:
      review-kit-file-missing-finding-type). ОЖИДАНИЕ ДОСТАВЛЕНО тем же
      днём (steward PR #142: file-missing kind в вердикте, line:0) — тег
      @blocked_by снят, пункт actionable; включение требует ре-вендора
      fp-кита review до версии с типом находки. До включения runner
      останавливается на человеке с evidence-подсказкой
      `git cat-file -e <head>:<путь>` (спека §7) — это штатный режим.
- [ ] Переключить авторинг behaviour-узла с `disp run --mode develop` на вид пайплайна `document` (полировка одиночного документа) @owner:github:andrei-shtanakov @id:behaviour-authoring-document-mode @blocked_by:disputatio#68
      Заведён 2026-08-31 вместе с inbox-issue disputatio#52 (slug:
      single-document-polish-mode). ОЖИДАНИЕ ДОСТАВЛЕНО 2026-09-01
      (disputatio PR #64, issue закрыт COMPLETED; уведомление — inbox
      devtools#103) — тег @blocked_by снят, пункт actionable.
      Доставлена форма (а): вид пайплайна выводится из ФОРМЫ секции
      `[pipeline]` (`document_path` вместо `spec_path`, взаимоисключающе),
      отдельной подкоманды и `--mode document` не появилось — команды
      прежние `disp pipeline run` / `resume` / `status` / `export`
      (SPEC-002 v0.2, сквозной пример — `docs/document-pipeline.md` у
      соседа). Переключение НЕ однострочное: `author_disp`
      (`governance/ops.py`) зовёт команду без конфига и с задачей в
      аргументе, а `pipeline` работает от состояния на диске. Решить
      перед работой: (1) откуда берётся `disputatio.toml` в целевом репо
      и как вычисляется `document_path` узла behaviour-spec; (2) кто
      пишет `[pipeline.checklists.doc]` — вендоренного набора у соседа
      нет намеренно, «что такое сошедшаяся behaviour-спека» знает
      владелец DSL, то есть мы (обязателен `findings_item`);
      (3) `anchor_path` обязан лежать ВНЕ рабочего дерева, иначе отказ
      старта — в прогоне governance задать явно. Гейт под наш DSL
      (`#### BEH-NN`, `traces:`, `checked_by`) сосед держит вне объёма
      (SPEC-002 §11, приедет своим PR): после переключения авторинг
      идёт по baseline-чеклисту без проверки нашей разметки.
- [ ] Эксперимент: одна LLM-петля конвейера как `.libretto`-программа — ритуал обработки ревью-стопа (разбор находки → правка бандла → перепиновка → resume) @owner:github:andrei-shtanakov @id:libretto-review-stop-loop
      Заведён 2026-09-02 решением владельца («да, заведи пункт»). НЕ замена
      скриптов: детерминированное ядро (гейты, пины, ожидание чеков,
      DarkFactory-мерж) остаётся кодом — libretto пробуем только на слое,
      который сейчас исполняется руками сессии без реплея и аудита.
      Мотивация: run-ledger, независимые critic-сессии, изоляция контекста
      (совпадает с evidence-культурой экосистемы). Цена по замерам самого
      libretto (`evaluation/results/final-verdict.md`): 2–6× токенов,
      ~46K контекстного пола на границу сессии. Критерий результата:
      петля прожита на живом ревью-стопе, замер токенов против ручного
      прогона той же петли, вердикт «стоит/не стоит» с числами в
      docs/. Запускать после закрытия текущих прогонов WS-dispatcher-229
      и WS-disputatio-57 (уроки лейна — devtools#110).

## Харнесс ревьюера (лимиты codex, 2026-09-03)

- [x] Переходник claude для терминального ревьюера ai-prosto + операторский конфиг харнесса @owner:github:andrei-shtanakov @id:review-harness-claude — PR этой ветки
      Срочный контур (лимиты codex): scripts/harness/claude-review говорит
      codex-диалектом кита снаружи и claude-диалектом внутри; выбор —
      флаг > env > ~/.config/ai-prosto/harness.env > вшитый codex; fp
      машинно-независим (голое имя через PATH). Кит steward не тронут ни
      байтом (24 вендора). Боевой смоук: полный structured-вердикт claude
      по devtools#106. Возврат на codex через неделю — правка двух строк
      конфига, кода не требует.
- [x] Ручка харнесса авторинга бандлов (governance/ops.author): AUTHOR_HARNESS/AUTHOR_MODEL из env/конфига оператора @owner:github:andrei-shtanakov @id:author-harness-claude — PR этой ветки
      Тот же срочный контур: авторинг S1–S3 был прибит к codex exec и
      умер бы с лимитами вместе с ревьюером. Тот же операторский файл
      (~/.config/ai-prosto/harness.env, ключи AUTHOR_*), та же слоевая
      привязка модели к харнессу (урок ревью PR #121). claude-ветка —
      паритет с флотским пресетом spec-runner (skip-permissions: авторинг
      пишет бандл и считает git hash-object). issue_classify/issue_worker
      не в критическом пути — ждут общего agent-runner из архитектурной
      части.
- [ ] Удалить переходник claude-review после канонизации харнесс-слоя в ките steward @owner:github:andrei-shtanakov @id:review-harness-shim-removal @blocked_by:steward#147
      Когда steward доставит REVIEW_HARNESS в самом local.sh (и ре-вендор
      доедет до devtools), переходник удаляется, review-pr.sh переходит с
      REVIEW_CMD на REVIEW_HARNESS. Миграция односторонняя, без вилки.
      До тех пор непокрытыми остаются pre-push хук и ручной запуск
      local.sh в соседях — они на codex (осознанно: не под лимитом).

## Ретроспектива 2026-09-02 — скриптовый лейн (в)

Разбор ошибок/находок трёх полных циклов конвейера (kapelle#47,
disputatio#57, dispatcher#229): уроки 1–8 — devtools#110; журнал KB —
записи 2026-09-02. Все пункты ниже — код лейна (в) в этом репо; upstream —
spec-runner#334/#335/#336/#337; соседям — dispatcher#251 (lint-хук).

- [x] accept-pr: материализация head PR в чекауте цели перед ревью + гард чистого дерева @owner:github:andrei-shtanakov @id:accept-pr-materialize-head — PR этой ветки
      Уроки 7 и «грязное дерево» (devtools#110): review-kit считает
      локальное дерево цели авторитетным — чекаут на master даёт ложное
      «реализации нет» (dispatcher#236 круг 2), грязное дерево — ложную
      фактуру находок (#235 круг 1). accept-pr перед ревью обязан:
      отказ при грязном дереве, fetch + switch на head PR
      (review-ветка), возврат master после приёмки. Сейчас — руками.
- [x] Мост bundle→tasks: frontmatter под активный профиль, штамп статусов бандла, перепиновка YAML-парсером @owner:github:andrei-shtanakov @id:spec-bridge-approve-conformance — PR этой ветки; upstream-плечо (репо-локальные stage-профили) — spec-runner#338
      Уроки 1–2 (devtools#110) + инцидент stale-пина (журнал 07:10):
      (1) `spec approve` пишет traces_to из вшитого lite-профиля — мост
      должен выдавать форму активного профиля (traces_to:
      [behaviour-spec] + upstream_hashes на approved-блоб, SpecMeta v2);
      (2) после мержа бандла charter/requirements/behaviour-spec
      остаются draft, открытые blocking-вопросы требуют decision-record
      — согласование статусов как явный шаг; (3) sed по инлайн-YAML
      форме пина `{requirements: "…"}` молча промахнулся и пустил
      stale-пин в коммит — перепиновку делать YAML-парсером, не
      текстовой заменой.
- [x] Преflight прогона spec-runner в целевом репо @owner:github:andrei-shtanakov @id:spec-run-preflight — PR этой ветки
      Уроки 4–5 (devtools#110) + закрытые классы дня (журнал 22:00):
      перед запуском раннера проверять/готовить: (1) конфиг — по
      эталону репо, если он есть (example.yaml / workstream-setup);
      голый `config --preset` без TDD-режима валит tdd-evidence;
      (2) среду live-smoke как в CI — те же install-шаги (dispatcher:
      pinned checker/steward/impresario), иначе попытки горят о чужую
      красноту (3 попытки, $2.82); (3) insteadOf https против
      ssh-зависаний пушей (git-receive-pack держал pipe 2 часа);
      (4) отсутствие беспрефиксной `spec/.executor-state.db` —
      пустая дефолтная база ломает tdd-evidence (upstream:
      spec-runner#337).
- [x] task_bridge: группировка геометрически связанных BEH в одну задачу @owner:github:andrei-shtanakov @id:task-bridge-beh-grouping — PR этой ветки; симуляция на живом WS-disputatio-57: 4 задачи вместо 15, все 7 red-unverifiable внутри слитой TASK-001
      Урок 8 (devtools#110): нарезка «один BEH — одна задача» дала
      7 red-unverifiable задач из 15 в WS-disputatio-57 — поведение уже
      геометрически покрыто соседней реализацией, red невозможен,
      TDD-гейт стопит прогон до waiver-ритуала. Группировать BEH по
      общему файлу/автомату состояний. Upstream-плечо (операторский
      waive одной командой) — spec-runner#335.
- [x] Runner S8: не оставлять .steward/gate_verdicts.jsonl в чекауте цели @owner:github:andrei-shtanakov @id:runner-s8-verdicts-cleanup — PR этой ветки
      Шероховатость прогонов 2026-09-02 (журнал 07:10): S8 пишет
      gate_verdicts.jsonl в target-репо → dirty-гард task_bridge
      отказывает следующему шагу конвейера. Чистить после верификации
      или писать вне рабочего дерева цели.
- [x] review-context.txt: семантика пер-таскового инкремента для репо конвейера @owner:github:andrei-shtanakov @id:review-context-increment-wave — PR этой ветки (devtools#116) + волна disputatio#84 / dispatcher#252 / kapelle#61; шаблон согласован владельцем 2026-09-03 (правки: later-TASK — не находка; red-тест только атрибутированно), мерж всех четырёх — человеком (.github/ authority-root)
      Урок 6 (devtools#110): терминальное ревью меряет пер-тасковый
      инкремент против ПОЛНОЙ спеки — законные находки «это объём
      следующей задачи» блокируют мерж (disputatio#72), а слепой фикс
      ломает TDD-red следующей задачи. Механизм у кита штатный:
      `.github/codex/review-context.txt` (все прогоны дня печатали
      «контекст не настроен»). Сделать шаблон в devtools + PR-волна в
      репо, где живёт конвейер; дополнение в accept-pr — уметь принять
      обоснованный ответ на находку этого класса.

## Context-pack по пункту плана (todo-context)

- [x] todo-context: свести всё, что флот уже знает об ОДНОМ пункте `TODO.md`, и честно назвать непрочитанное @owner:github:andrei-shtanakov @id:todo-context-pack @epic:eco.plan-fields — PR этой ветки
      Пункт плана — одна строка: `@id`, `@epic`, иногда `@defect`/`@blocked_by`,
      и больше ничего (правило этого файла: «теги и суть — на одной строке»,
      парсер продолжений не видит by design). Отдать такую строку агенту как
      задание — попросить его выдумать требование. Тело при этом не
      отсутствует: оно разложено по источникам, которые уже есть и которые
      никто не сводит — `goal` эпика в `epics.toml`, design-док, названный
      секцией, соседи по `@blocked_by`, `CLAUDE.md` репо и (для принятого через
      inbox пункта, ADR-ECO-006 D9) тело исходного issue. Главный источник —
      отступные строки продолжения: правила выше называют их «контекст для
      человека», и по флоту в них уже написаны целые абзацы обоснования. Замер
      на 264 открытых пунктах: 169 rich (execute разрешён), 95 thin, 0 bare —
      причём 157 из 169 дают требование телом, и лишь 12 — одними доками,
      поэтому расширение грамматики полей под `@spec:` не нужно. Чтение
      продолжений семантику плана не меняет: это devtools-side синтаксический
      разбор того же файла, в граф и в теги ничего не возвращается. Каждый
      источник отчитывается `read`/`absent`/`not_queried`/`error`, и grade
      считается по состояниям, а не по непустому тексту: «не смотрели» и «там
      пусто» выглядят одинаково пусто, и исполнитель, который их не различает,
      уверенно работает с контекстом, которого не имел.
      Круг 1 ревью (терминальный прогон + Copilot независимо) снял три
      fail-open: (1) грep по `@id` матчил голую подстроку, и упоминание в
      коде/тесте/имени ветки давало `execute_allowed` — теперь требованием
      считается только попадание в markdown под `docs/spec/plans/workstreams`,
      и grade читает найденное, а не состояние источника («смотрели ли» —
      не «есть ли требование»); по флоту это сняло исполнимость с 7 пунктов,
      у которых доказательством были `db.rs`, схема и README фикстур;
      (2) `match_origin_issue` не сверял репо issue с репо пункта, а флот
      пишет ИСХОДЯЩИЕ запросы в строку своего пункта — чужой запрос приходил
      обратно как собственное требование с обращённым направлением;
      (3) `_RULES_CAP` объявлен в байтах, а резался по символам — на
      кириллице это 1.4x от заявленного и ложный `truncated`.
      Круг 2 (request-changes, один major с confidence high): сужение по
      каталогу не закрывало дыру, потому что `git grep --fixed-strings` матчит
      подстроку — id попадает внутрь более длинного id, имени компонента, ветки
      и файла. Теперь упоминание считается требованием, только если оно
      РАЗМЕЧЕНО как ссылка: `@id:`, `todo://<repo>/<id>` или код-спан ровно с
      этим id. Первый вариант правила брал только тег и URI — и выбрасывал
      настоящее требование `## P1 — сократить промпт (`review-kit-prompt-diet`)`
      из роадмапа steward; поймано замером, а не рассуждением. По сегодняшнему
      флоту сужение не меняет ни одного вердикта (169/95/0 до и после) — оно
      закрывает механизм, а не цифру. Плюс два minor: `read_graph` рапортовал
      `read`, хотя нескленированные репо делают обратную сторону графа молча
      неполной (теперь `unread_repos` и оговорка в отчёте), и ветка
      `read_docs(None, …)` возвращала `named` строками вместо словарей —
      объявленная деградация уронила бы grade и рендер.
      Круг 3 (approve, пять находок): порог написанного требования был только у
      тела — пустой named-док (путь закоммичен вперёд написания, обычная
      практика) разрешал `execute`; теперь `_DOC_SUBSTANTIAL` симметричен
      `_BODY_SUBSTANTIAL`. Шапка модуля и README продолжали утверждать правило,
      которое круг 1 заменил («grade считается по состояниям источников») —
      согласовано с реализацией. `AmbiguousIdentityError` ловилась только на
      `manifest_index`, хотя `checkout_map` решает идентичность тоже. Написание
      репо не нормализовалось: `todo://Maestro/x` отказывал «пункта нет», хотя
      пункт есть — теперь через `index.resolve_ref`, как в контракте. И `rules`
      инлайнились с пометкой «(обрезано)» в отчёт, где самого текста нет.
      Круг 4 (approve, четыре находки — все той же семьи): порога не было у
      исходного issue (`inbox` намеренно не требует тела, поэтому `slug:` +
      `from:` — валидный запрос, но не требование); `_GREP_CAP` резал выдачу ДО
      классификации, и ссылка за 40-м хитом была невидима вердикту, пока
      источник рапортовал обычный `read`; неизвестный `@epic` рапортовался как
      `error` и попадал в `unknown_sources` под оговоркой «источник не читали»,
      хотя реестр прочитан и эпика в нём нет; слаг issue матчился подстрокой
      (`benchmark-2` ⊂ `benchmark-20`) — в `inbox.py` это метка в отчёте, здесь
      было бы тело требования исполнителю. Сам, до находки, закрыл ту же схему
      у эпика: `epic: read` — присутствие, а эпик с пустым `goal` не отличает
      thin от bare. Порог теперь один на все три источника требования
      (`_BODY_/_DOC_/_ISSUE_SUBSTANTIAL`).
      Круг 5 (approve, четыре находки): порог не был применён к упоминающему
      доку (заглушка с одной строкой `@id:` — не требование); при нескольких
      подходящих issue телом требования становился первый по порядку выдачи
      `gh` — монетка, напечатанная как факт; `git grep` декодировался строго и
      один не-UTF-8 файл у соседа ронял пак вместо `docs: error`. Главная —
      четвёртая: ужесточение слага круга 4 было **приватным правилом поверх
      выводимого факта ADR-ECO-006 D9**, о котором `inbox.is_accepted` прямо
      пишет, что это и есть расхождение, убираемое ADR: `make inbox` говорил
      «принят» там, где `todo-context` — «issue нет». Пара снова ищется общим
      правилом и отвечает так же; строгость осталась только у вердикта
      (`exact`), а нестрогое совпадение печатается контекстом с оговоркой.
      Ужесточение самого правила — в пакете, см. пункт ниже.
      Круг 6 (approve, впервые без major, две находки): док, названный в
      ЗАГОЛОВКЕ СЕКЦИИ, раздавал написанное требование каждому пункту секции —
      теперь `named_in` различает «названо в строке пункта» (считается) и
      «названо в секции» (контекст); по флоту на секционном доке держалась одна
      позиция. И оговорка про кап печати утверждала «ссылки на пункт показаны
      все» даже когда ссылок больше капа — теперь считает и называет
      выброшенные.
      Круг 7 (request-changes, два major с confidence high — первый красный
      после круга 2): (1) порог для упоминания мерил размер ВСЕГО файла, а для
      упоминания направление обратное — док указывает на пункт, и любой живой
      док порог проходит; реальный вход — `behaviour-console.md:156-159`, где
      внутри плана ДРУГОГО пункта процитирована будущая строка TODO.md с
      `@id:`. Теперь считается проза секции вокруг ссылки, а ссылка внутри
      чеклист-пункта (включая его строки-продолжения) требованием не считается:
      это шаг чужого плана. Проверено на обоих живых случаях — контрпример
      False, роадмап steward True. (2) Переходная форма `<repo>#<slug>` ребром
      не становится никогда и при однозначном совпадении не даёт даже
      диагностики — «нет рёбер» утверждалось как факт там, где ожидание есть.
      Теперь `legacy_waits` + оговорка в detail; сам слаг здесь НЕ резолвится —
      пара слаг↔пункт правило пакета (`check_legacy_fleet`), приватное было бы
      повторением ошибки круга 5.
- [ ] Слаг приёмки как токен — в пакете `plan-fields`, а не приватно в devtools @owner:github:andrei-shtanakov @id:inbox-slug-token-match
      `inbox.is_accepted` матчит слаг подстрокой и документирует слабость:
      `benchmark-2` совпадает с пунктом про `benchmark-20`. Ужесточение —
      «the package's call» (ADR-ECO-005 D9), приватное правило в скрипте есть
      расхождение, которое ADR убирает; поймано ревью PR #125, круг 5, когда
      такое правило завелось в `todo_context.py` и было снято. Апстрим-плечо —
      issue в dispatcher (владелец `packages/plan-fields`); пока запрос НЕ
      заведён, поэтому `@blocked_by` не проставлен: тег появится вместе с
      номером. Здесь после этого — снять локальный `slug_re` и читать пакетное
      правило.

## Исполнитель пункта плана (todo-worker)

- [x] todo-worker: прогон ОДНОГО пункта поверх context-pack, execute только при `execute_allowed` @owner:github:andrei-shtanakov @id:todo-worker @epic:eco.plan-fields — PR этой ветки
      Брат `issue_worker.py` по плоскости планов: политика считается кодом до
      вызова модели, модель может поднять `needs_human`, но не перевернуть
      гейт; publish-фаз (commit/push/PR/merge) нет by design; результат —
      структурированный JSON в `out/`, не в целевом репо. Отличие в источнике
      полномочия: там оператор печатает `--internal` и видит, что напечатал,
      здесь гейт выводится из контекста, который оператор не собирал.
      Отсюда три решения, каждое против симметрии с issue_worker:
      (1) `--mode execute` при `execute_allowed: false` — ОТКАЗ (код 4), а не
      тихий даунгрейд, как `issue_worker.effective_execute`: тихий откат
      спрятал бы сам гейт, ради которого инструмент и сделан; печатаются
      `completeness.reason` и `unknown_sources`, чтобы оператор видел, чего
      не хватило и что не спрашивали;
      (2) санитайзер id: `todo_context.parse_uri` принимает `(.+)`, поэтому id
      со слэшем или `..` увёл бы `result_path` за пределы `out/`;
      (3) неполный `--pack` — ответ, а не трейсбек: файл называет оператор,
      значит урезанный или правленый руками пак — штатный вход.
      Спека для spec-runner ось режима НЕ занимает: это выход `plan`, и для
      неё уже есть `skills/spec-bridge` (инвариант 4).
      Круг 1 ревью снял блокер, который стоил бы дорого: `subprocess.run`
      наследует CWD, то есть execute для пункта СОСЕДА правил бы дерево
      devtools под `workspace-write` — гейт разрешал менять один репо, а
      двигался бы другой. `issue_worker` от этого спасён лишь тем, что
      `issue_console` стартует его через `tmux -c <repo_path>`; эту половину
      я не перенёс. Пак теперь несёт `checkout`, харнесс идёт с `cwd=` в нём, а
      неизвестный или не-git путь — отказ, а не работа в своём дереве. Плюс:
      сырое имя репо больше не санитайзится (`--repo Maestro` нормализует
      контракт, а отказ читался бы как «нет такого пункта»), не-объект в
      ответе харнесса — ответ, а не трейсбек, и `require_id` проверяет ТИП
      (числовой `item.id` в правленом паке давал TypeError мимо всех гардов —
      находка Copilot).
      Не сделано осознанно: гарда на грязное дерево цели нет. Он нужен —
      правки воркера смешиваются с правками оператора, и `changed_files`
      становится непроверяемым — но это отдельное решение, не фикс находки;
      см. `@id:todo-worker-dirty-tree-guard`.
- [x] Гард грязного дерева перед execute-прогоном воркера @owner:github:andrei-shtanakov @id:todo-worker-dirty-tree-guard — PR этой ветки
      `todo_worker --mode execute` правит чекаут цели, и если дерево уже
      грязное, `changed_files` в результате не отличить от того, что оператор
      не закоммитил до прогона. Это ровно тот класс, который уже закрыт в
      `accept-pr` (урок «грязное дерево — ложная фактура находок»,
      ретроспектива 2026-09-02). Нужен отказ до вызова харнесса, с тем же
      кодом 2 и внятной причиной.
      Сделано вместе с тремя minor круга 2: гард отказывает до вызова харнесса
      и печатает, ЧТО именно грязно; проверяется и в `--dry-run` — превью,
      которое зеленеет там, где боевой прогон откажет, врёт. `plan` дерево не
      проверяет: он read-only, спутать его правки с чужими нельзя.
- [ ] Харнесс-слой воркеров: один резолвер с обеими песочницами и structured output @owner:github:andrei-shtanakov @id:worker-harness-layer
      В `todo_worker.py` вшит `codex`, как в `issue_worker.py` — осознанно, а не
      по инерции: готовые разрешители харнесса не покрывают то, что нужен
      воркеру. `governance/ops.py::_author_argv` собирает claude write-only и
      БЕЗ `--output-schema` (результат перестал бы быть структурированным —
      молча, и enforce-инвариант проверять было бы нечего);
      `scripts/harness/claude-review` требует `--sandbox read-only` жёстко
      (отказ кодом 2 на всё прочее). Воркеру нужны обе оси разом — read-only и
      workspace-write, обе со схемой. Ручка `WORKER_HARNESS` поверх этого либо
      собрала бы claude без схемы, либо потянула третью реализацию разбора
      конверта structured output. Это уже третья точка вызова после
      `review-pr.sh` и `ops.author`, поэтому слой — общий, а не в новом файле.
