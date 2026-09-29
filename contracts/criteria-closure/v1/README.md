# criteria-closure/v1 — вендоренная копия

Владелец схемы — **spec-runner** (производитель `verify --criteria`; заявка
spec-runner#603). devtools — потребитель: спека
`docs/superpowers/specs/2026-09-28-bundle-criteria-oracle-design.md` §4–§5.

Сейчас здесь только `min-spec-runner.env`. Контракт считается **вендоренным**
(оракул доступен), когда рядом лежат `PIN` (`SOURCE: spec-runner @ <sha>`),
схемы запроса/ответа и `manifest.json` с их sha256, и манифест сходится —
отдельного флага «выпущено» нет (`governance/criteria_contract.vendored()`).
До этого `criteria-close` отвечает `not-applicable: spec-runner-version`.

Две гарантии вендоринга: **целостность** — `integrity_findings()` локально в
CI без соседа; **дрейф** — `drift_findings()` против апстрима по ref из `PIN`
(в CI отсутствие апстрима — ошибка, локально — `not-checked`).
