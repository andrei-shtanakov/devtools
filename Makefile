# ============================================================
#  Makefile — дружелюбные алиасы поверх repos.sh / drift-checker.
#  Живёт в devtools/; цели работают над workspace-родителем.
#  Запуск:  make            (= make help)
#           make status / fetch / pull / dirty / branches
#           make bootstrap  (uv sync + cargo build)
#           make drift      (проверка вендоренных контрактов)
#           make morning    (fetch + status — утренний ритуал)
# ============================================================
SHELL := /usr/bin/env bash

# Манифест командного воркспейса (SSOT набора) — дом в зонтике, не в devtools.
# Переопредели при другом расположении:  make release-drift MANIFEST=/path/to/workspace-manifest.toml
MANIFEST ?= ../ai-orchestrators-workspace/workspace-manifest.toml

.DEFAULT_GOAL := help
.PHONY: help status fetch pull dirty branches bootstrap drift conformance graph-drift morning evening snapshot fleet-report today install

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
	@echo "  make graph-drift — граф prograph ↔ карта интеграций registry"
	@echo "  make morning     — fetch + status (утренний ритуал)"
	@echo "  make evening     — вечерний чек: незакоммиченное / фича-ветки / незапушенное"
	@echo "  make snapshot    — полный JSON состояния флота (github-checker snapshot)"
	@echo "  make fleet-report— markdown-отчёт о флоте в stdout (fleet_report.py)"
	@echo "  make today       — что изменилось с полуночи: коммиты + незакоммиченное"
	@echo "  make install     — доклонировать недостающие репо набора по манифесту зонтика"
	@echo "  make release-drift — набор из манифеста зонтика ↔ факт на диске"

status:      ; @./repos.sh status
fetch:       ; @./repos.sh fetch
pull:        ; @./repos.sh pull
dirty:       ; @./repos.sh dirty
branches:    ; @./repos.sh branches
bootstrap:   ; @./repos.sh bootstrap
drift:       ; @./check-contract-drift.sh
conformance: ; @python3 ./check-agent-id-conformance.py
graph-drift: ; @python3 ./check-graph-registry-drift.py
morning:     ; @./repos.sh fetch && echo && ./repos.sh status
evening:     ; @./repos.sh evening
snapshot:    ; @uv run --project ../github-checker github-checker snapshot --workspace ..
fleet-report:; @uv run --project ../github-checker github-checker snapshot --workspace .. | python3 ./fleet_report.py
today:       ; @python3 ./recent_changes.py

.PHONY: release-drift
release-drift: ; @python3 ./check-release-drift.py --workspace .. --manifest $(MANIFEST)

install: ; @./repos.sh install --manifest $(MANIFEST)
