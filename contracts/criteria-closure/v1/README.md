# criteria-closure/v1 — вендоренная копия

Владелец схемы — **spec-runner** (производитель `verify --criteria`; заявка
spec-runner#603). devtools — потребитель: спека
`docs/superpowers/specs/2026-09-28-bundle-criteria-oracle-design.md` §4–§5.

Схемы запроса/ответа v1, эталоны ответов и `min-spec-runner.env`
вендорены @ spec-runner **v4.5.0** (`1de715c`, release X: B2a — схемы, B2b —
`verify --criteria`; схемы и эталоны побайтно те же, что в B2a @ `3a1b9aa`).
`PIN` (`SOURCE: spec-runner @ <sha>`) и `manifest.json` (sha256 обеих схем и
`min-spec-runner.env`) сходятся — `vendored()` возвращает `True`;
`MIN_SPEC_RUNNER_VERSION=4.5.0` — байты производителя, не наша правка.
**Оракул доступен**, когда установленный spec-runner не ниже 4.5.0
(`oracle_available()`); ниже — `criteria-close` отвечает `not-applicable:
spec-runner-version`. Начиная со среза 2a `closure_gate` не читает
доступность оракула вовсе: `not_applicable_reason: spec-runner-version`
всегда красный (оракул уже выпущен @ 4.5.0 — закрытие нужно перегнать на
машине с установленным spec-runner ≥ min, не ждать от гейта зачёта).
Значение `pending` в `min-spec-runner.env` (до релиза) `read_min_version()`
по-прежнему читает как «команда не выпущена».

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
