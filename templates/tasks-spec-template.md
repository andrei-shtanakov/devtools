---
spec_stage: tasks
status: draft
version: 1
generated_by: fleet-agent
generated_at: <YYYY-MM-DDTHH:MM:SS>
source_prompt_version: ""
validation: ""
approved_by: ""
---

<!--
  Шаблон tasks.md-спеки для spec-runner — мост fleet-агента к исполнению.
  Формат: spec-runner/spec/FORMAT.md (авторитет). Скилл: skills/spec-bridge/.

  Правила заполнения:
  - Файл кладётся PR-ом в РЕПО-ВЛАДЕЛЕЦ изменений как spec/<prefix->tasks.md
    (--spec-prefix изолирует от основного tasks.md репо).
  - Frontmatter выше делает спеку managed: status: draft НЕ исполняется при
    spec_governance strict, пока человек не переведёт в approved — это и есть
    гейт «агент предлагает, человек утверждает».
  - Provenance обязателен: каждая задача трассируется к источнику — кластеру
    self-review (robin var/selfreview/YYYY-MM-DD.md), находке fleet-отчёта
    (prograph-vault derived/fleet/...) или записи gaps.jsonl.
  - Чеклист-пункты с колонки 0 (отступ = пункт молча игнорируется парсером!).
  - ID задач уникальны в пределах файла; зависимости без циклов.
-->

## Milestone 1: <краткое имя цели>

<!-- 1-3 предложения: какую проблему закрывает спека и откуда она пришла.
     Пример: «Кластер self-review 2026-07-17: zero_retrieval × temporal, 4 провала —
     сенсор recent_changes не видит <источник>». -->

### TASK-001: <императивное имя задачи>
P1 | TODO   Est: 1d

<Что сделать и почему — 1-3 предложения. Обязательная строка провенанса:>
Source: <prograph-vault/derived/fleet/fleet-...md | robin var/selfreview/....md кластер N | gaps.jsonl запись>

**Checklist:**
- [ ] <конкретный проверяемый шаг>
- [ ] <тесты: какой тест докажет, что готово>
- [ ] <док/CLAUDE.md обновлён, если поведение видно снаружи>

**Traces to:** [REQ-001]

### TASK-002: <следующая задача>
P2 | TODO   Est: 0.5d

<описание>
Source: <провенанс>

**Checklist:**
- [ ] <шаг>

**Depends on:** [TASK-001]
