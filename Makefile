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

.DEFAULT_GOAL := help
.PHONY: help status fetch pull dirty branches bootstrap drift conformance morning snapshot fleet-report

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
	@echo "  make morning     — fetch + status (утренний ритуал)"
	@echo "  make snapshot    — полный JSON состояния флота (github-checker snapshot)"
	@echo "  make fleet-report— markdown-отчёт о флоте в stdout (fleet_report.py)"

status:      ; @./repos.sh status
fetch:       ; @./repos.sh fetch
pull:        ; @./repos.sh pull
dirty:       ; @./repos.sh dirty
branches:    ; @./repos.sh branches
bootstrap:   ; @./repos.sh bootstrap
drift:       ; @./check-contract-drift.sh
conformance: ; @python3 ./check-agent-id-conformance.py
morning:     ; @./repos.sh fetch && echo && ./repos.sh status
snapshot:    ; @uv run --project ../github-checker github-checker snapshot --workspace ..
fleet-report:; @uv run --project ../github-checker github-checker snapshot --workspace .. | python3 ./fleet_report.py
