# ============================================================
#  Makefile — дружелюбные алиасы поверх repos.sh / drift-checker.
#  Живёт в devtools/; цели работают над workspace-родителем.
#  Запуск:  make            (= make help)
#           make status / fetch / pull / dirty / branches
#           make bootstrap  (uv sync + cargo build)
#           make drift      (проверка вендоренных контрактов)
#           make morning    (fetch + status + inbox — утренний ритуал)
# ============================================================
SHELL := /usr/bin/env bash

# Корень workspace и манифест командного воркспейса (SSOT набора — дом в
# зонтике, не в devtools). Переопределяются для ephemeral-workspace:
#   make plan-check WORKSPACE=/path/to/ws [MANIFEST=/path/to/manifest.toml]
WORKSPACE ?= ..
MANIFEST ?= $(WORKSPACE)/ai-orchestrators-workspace/workspace-manifest.toml

.DEFAULT_GOAL := help
.PHONY: help status fetch pull dirty branches bootstrap drift conformance catalog-fixtures graph-drift plan-check plan-check-selftest plan-check-fixture inbox morning evening snapshot fleet-report today install arch-freshness arch-freshness-read

help:
	@echo "Цели:"
	@echo "  make status      — ветка / ahead-behind / грязь по каждому репо"
	@echo "  make fetch       — git fetch --all --prune везде"
	@echo "  make pull        — git pull --ff-only по текущей ветке (грязные пропускает)"
	@echo "  make dirty       — показать только репо с незакоммиченным"
	@echo "  make branches    — сводка веток"
	@echo "  make bootstrap   — uv sync (python) + cargo build (arbiter)"
	@echo "  make drift       — diff вендоренных obs.py и report_benchmark schema"
	@echo "  make conformance — agent-id caталог ↔ ATP/arbiter/Maestro (ADR-ECO-003)"
	@echo "  make catalog-fixtures — SSOT conformance-фикстуры каталога: expectations + manifest (devtools#43)"
	@echo "  make graph-drift — граф prograph ↔ карта интеграций registry"
	@echo "  make plan-check  — cross-repo TODO/@blocked_by граф (uv + Python 3.12; WORKSPACE=/path для другого workspace)"
	@echo "  make plan-check-selftest — самопроверка политики чекера, без workspace"
	@echo "  make plan-check-fixture  — вердикт чекера на синтетическом workspace (clean=0, stale=1)"
	@echo "  make inbox       — входящие кросс-репные запросы (issues с лейблом inbox; uv + Python 3.12)"
	@echo "  make morning     — fetch + status + inbox (утренний ритуал; uv + Python 3.12)"
	@echo "  make evening     — вечерний чек: незакоммиченное / фича-ветки / незапушенное"
	@echo "  make snapshot    — полный JSON состояния флота (github-checker snapshot)"
	@echo "  make fleet-report— markdown-отчёт о флоте в stdout (fleet_report.py)"
	@echo "  make today       — что изменилось с полуночи: коммиты + незакоммиченное"
	@echo "  make install     — доклонировать недостающие репо набора по манифесту зонтика"
	@echo "  make release-drift — набор из манифеста зонтика ↔ факт на диске"
	@echo "  make arch-freshness       — локальная диагностика drift/freshness арх-evidence (вахта — CI steward)"
	@echo "  make arch-freshness-read  — читатель локального статуса: просрочка ⇒ unknown (exit 2)"

status:      ; @./repos.sh status
fetch:       ; @./repos.sh fetch
pull:        ; @./repos.sh pull
dirty:       ; @./repos.sh dirty
branches:    ; @./repos.sh branches
bootstrap:   ; @./repos.sh bootstrap
drift:       ; @./check-contract-drift.sh
conformance: ; @python3 ./check-agent-id-conformance.py
catalog-fixtures: ; @python3 ./check-catalog-fixtures.py --check
graph-drift: ; @python3 ./check-graph-registry-drift.py
plan-check:  ; @uv run --frozen python ./check-plan-fields.py --root $(WORKSPACE) --manifest $(MANIFEST)
plan-check-selftest: ; @uv run --frozen python ./check-plan-fields.py --selftest
plan-check-fixture:  ; @uv run --frozen pytest tests/test_plan_check_fixture.py -q
inbox:       ; @uv run --frozen python ./inbox.py --root ..
morning:     ; @./repos.sh fetch && echo && ./repos.sh status && echo && uv run --frozen python ./inbox.py --root ..
evening:     ; @./repos.sh evening
snapshot:    ; @uv run --project ../github-checker github-checker snapshot --workspace ..
fleet-report:; @uv run --project ../github-checker github-checker snapshot --workspace .. | python3 ./fleet_report.py
today:       ; @python3 ./recent_changes.py

.PHONY: release-drift
release-drift: ; @python3 ./check-release-drift.py --workspace .. --manifest $(MANIFEST)

install: ; @./repos.sh install --manifest $(MANIFEST)

# Локальная ДИАГНОСТИКА. Авторитетная вахта — scheduled-workflow
# arch-evidence-freshness в steward (переход launchd→CI завершён 2026-08-08;
# независимый читатель свежести runs — robin-runtime#42).
arch-freshness:      ; @python3 ./check-arch-evidence-freshness.py --workspace ..
arch-freshness-read: ; @python3 ./check-arch-evidence-freshness.py --read
