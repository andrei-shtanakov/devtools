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
.PHONY: help status fetch pull dirty branches bootstrap drift conformance graph-drift plan-check plan-check-selftest plan-check-fixture inbox morning evening snapshot fleet-report today install arch-freshness arch-freshness-read arch-freshness-schedule arch-freshness-unschedule

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
	@echo "  make arch-freshness       — drift/freshness арх-evidence (steward↔prograph), без эскалации"
	@echo "  make arch-freshness-read  — читатель статуса: просрочка ⇒ unknown (exit 2)"
	@echo "  make arch-freshness-schedule   — launchd-агент (INTERIM-планировщик), ежедневно"
	@echo "  make arch-freshness-unschedule — снять launchd-агент"

status:      ; @./repos.sh status
fetch:       ; @./repos.sh fetch
pull:        ; @./repos.sh pull
dirty:       ; @./repos.sh dirty
branches:    ; @./repos.sh branches
bootstrap:   ; @./repos.sh bootstrap
drift:       ; @./check-contract-drift.sh
conformance: ; @python3 ./check-agent-id-conformance.py
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

arch-freshness:      ; @python3 ./check-arch-evidence-freshness.py --workspace ..
arch-freshness-read: ; @python3 ./check-arch-evidence-freshness.py --read

PLIST_LABEL := com.devtools.arch-evidence-freshness
PLIST_DST   := $(HOME)/Library/LaunchAgents/$(PLIST_LABEL).plist
arch-freshness-schedule:
	@mkdir -p out/arch-evidence-freshness
	@sed -e "s|@DEVTOOLS_DIR@|$(CURDIR)|g" \
	     templates/$(PLIST_LABEL).plist > $(PLIST_DST)
	@launchctl unload $(PLIST_DST) 2>/dev/null || true
	@launchctl load $(PLIST_DST)
	@echo "launchd-агент загружен (INTERIM-планировщик): $(PLIST_DST)"
arch-freshness-unschedule:
	@launchctl unload $(PLIST_DST) 2>/dev/null || true
	@rm -f $(PLIST_DST)
	@echo "launchd-агент снят"
