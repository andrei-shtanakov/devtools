# criteria-closure/v1 — вендоренная копия

Владелец схемы — **spec-runner** (производитель `verify --criteria`; заявка
spec-runner#603). devtools — потребитель: спека
`docs/superpowers/specs/2026-09-28-bundle-criteria-oracle-design.md` §4–§5.

Схемы запроса/ответа v1 и эталоны ответов вендорены @ `3a1b9aa` (B2a):
рядом с `min-spec-runner.env` лежат `PIN` (`SOURCE: spec-runner @ <sha>`),
`request.schema.json`, `response.schema.json` и `manifest.json` с их sha256 —
манифест сходится, `governance/criteria_contract.vendored()` возвращает
`True`. Но оракул **остаётся недоступным**: `MIN_SPEC_RUNNER_VERSION=pending`
в `min-spec-runner.env` — команды `verify --criteria` ещё нет (B2b,
spec-runner#603). `oracle_available()` держит это отдельно от `vendored()`:
`read_min_version()` возвращает `MinVersion(None)` для `pending`, и при
`minimum.version is None` оракул недоступен независимо от установленной
версии spec-runner. `criteria-close` отвечает `not-applicable:
spec-runner-version`, пока число не выставит PR, вендорящий
`min-spec-runner.env` производителя.

Эталонные ответы — отдельная вендоренная копия `fixtures/responses/` со
своими `PIN`/`manifest.json` (апстрим — `tests/fixtures/criteria-closure/v1/responses/`
spec-runner).

Две гарантии вендоринга: **целостность** — `integrity_findings()` локально в
CI без соседа; **дрейф** — `drift_findings()` против апстрима по ref из `PIN`
(в CI отсутствие апстрима — ошибка, локально — `not-checked`).

## `fixtures/ownership/` — общие фикстуры владения токеном

Отдельная вендоренная копия со **своими** `PIN` и `manifest.json` (апстрим —
`tests/fixtures/criteria-closure/v1/ownership/` spec-runner, дизайн #603 §6.3,
devtools#491) — независимая от корневого `PIN` схем выше. Байты — это кейсы
(CRLF, одиночный CR, BOM, NUL,
form feed): `-text` в `.gitattributes`, исключены из ruff. Проверка —
`tests/test_governance_ownership_fixtures.py`: все `owned` совпадают с
`expected.json`, целостность по manifest, дрейф против соседнего чекаута
spec-runner (нет чекаута — `not-checked`).
