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
