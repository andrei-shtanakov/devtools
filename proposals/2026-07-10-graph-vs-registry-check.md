# Proposal: graph-vs-registry drift check (fleet-агент)

**Дата:** 2026-07-10
**Статус:** предложение
**Семья:** та же, что `check-contract-drift.sh` / `check-agent-id-conformance.py` —
сверка derived-артефакта с authored-источником, уровнем выше.

## Кейс-первопричина

Граф prograph (derived) показывает proctor полностью изолированным — 0 рёбер.
Карта интеграций в COWORK_CONTEXT (authored) говорит: dispatcher ↔ proctor связаны
с 2026-07-05 (`dispatcher/core/collectors/proctor.py` читает `config/proctor.yaml`,
`data/state.db`, логи proctor). Связь реализована чтением файлов с диска — ни импорта,
ни MCP-вызова, ни общего контракт-файла, поэтому все три детектора prograph её не видят.
Dispatcher так задуман («reads on-disk artifacts, проекты не должны быть даже
запущены») — значит, невидимы ВСЕ его рёбра к подопечным, не только proctor.
Расхождение нашлось глазами на схеме; должно ловиться механически.

## Инвариант

> Каждая связь из карты интеграций (COWORK_CONTEXT → `prograph-vault/authored/registry/`)
> имеет соответствующее ребро в графе prograph (любого типа), и наоборот: рёбра графа
> между проектами, отсутствующие в карте, репортятся как недокументированные.

## Эскиз чекера (`check-graph-registry-drift.py`)

1. Источник A (authored): распарсить карту интеграций из registry
   (`prograph-vault/authored/registry/registry.md` / COWORK_CONTEXT) → множество пар
   `{a, b}`.
2. Источник B (derived): рёбра графа — `prograph mcp`-тул `find_edges` либо
   `GET /api/graph`, либо напрямую `.prograph/graph.db` → множество пар.
3. Diff в обе стороны:
   - в карте, но не в графе → `undetected integration` (кандидат на declared-edge
     декларацию в манифесте проекта — см. prograph/TODO.md «Declared edges»);
   - в графе, но не в карте → `undocumented integration` (обновить карту).
4. Выход: exit 1 при непустом diff + отчёт; известные допущения — в allowlist-файле
   рядом (как у contract-drift).

## Зависимости и порядок

- Уже можно строить: обе стороны читаются сегодня (registry + graph.db snapshot 4).
- Ложные срабатывания на файловых интеграциях исчезнут, когда prograph научится
  declared edges (`[tool.prograph] reads = [...]`, дальний план M12) — до тех пор такие
  пары живут в allowlist с комментарием.
- Нюанс namespace-ов: карта и граф оперируют именами репо; runtime service-id
  (`proctor-a`, ADR 2026-07-07) в сверке не участвуют.
