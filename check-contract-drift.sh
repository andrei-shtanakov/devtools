#!/usr/bin/env bash
# ============================================================
#  check-contract-drift.sh — READ-ONLY проверка рассинхрона
#  вендоренных контрактов между репозиториями.
#
#  Зачем: контракты сейчас держатся копированием (vendoring),
#  а COWORK_CONTEXT.md уже фиксирует дрейф ("доки arbiter ещё
#  не обновлены под 6-й tool"). Этот скрипт ловит расхождение
#  ДО того, как оно дойдёт до прод-инцидента.
#
#  Живёт в devtools/; запускай
#  руками или через `make drift`. Ничего не меняет, только diff.
#  Exit 1 — если разошлась хотя бы одна СХЕМА (жёсткий инвариант).
# ============================================================
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -t 1 ]; then R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; B=$'\033[1m'; Z=$'\033[0m'
else R=""; G=""; Y=""; B=""; Z=""; fi

fail=0

# --- $1 reference, $2 copy, $3 строгость(hard|soft), $4 ярлык ---
compare() {
  local ref="$1" copy="$2" mode="$3" label="$4"
  if [ ! -f "$ROOT/$ref" ]; then printf "%b? %s: reference нет (%s)%b\n" "$Y" "$label" "$ref" "$Z"; return; fi
  if [ ! -f "$ROOT/$copy" ]; then printf "%b? %s: копии нет (%s)%b\n" "$Y" "$label" "$copy" "$Z"; return; fi
  if cmp -s "$ROOT/$ref" "$ROOT/$copy"; then
    printf "%b✓ %s — идентично%b\n" "$G" "$label" "$Z"
  else
    local n; n=$(diff "$ROOT/$ref" "$ROOT/$copy" | grep -cE '^[<>]')
    if [ "$mode" = "hard" ]; then
      printf "%b✗ %s — РАСХОЖДЕНИЕ (%s строк), инвариант byte-for-byte нарушен%b\n" "$R" "$label" "$n" "$Z"
      fail=1
    else
      printf "%b⚠ %s — отличается (%s строк) — проверь, не дрейф ли логики%b\n" "$Y" "$label" "$n" "$Z"
    fi
    printf "    %sref:%s  %s\n    %scopy:%s %s\n" "$B" "$Z" "$ref" "$B" "$Z" "$copy"
  fi
}

echo "${B}== observability obs.py (reference: spec-runner@fa6b106) ==${Z}"
compare "spec-runner/src/spec_runner/obs.py" "Maestro/maestro/_vendor/obs.py"        soft "Maestro/_vendor/obs.py"
compare "spec-runner/src/spec_runner/obs.py" "arbiter/orchestrator/_vendor/obs.py"   soft "arbiter/orchestrator/_vendor/obs.py"

echo
echo "${B}== report_benchmark schema (arbiter ↔ Maestro, byte-for-byte) ==${Z}"
compare "arbiter/arbiter-mcp/tests/contract/report_benchmark-v1.schema.json" \
        "Maestro/contracts/benchmark/report_benchmark-v1.schema.json" \
        hard "report_benchmark-v1.schema.json"

echo
if [ "$fail" -eq 0 ]; then
  printf "%bРезультат: жёсткие контракты в синхроне.%b\n" "$G" "$Z"
else
  printf "%bРезультат: есть нарушение byte-for-byte контракта — синхронизируй прежде чем мержить.%b\n" "$R" "$Z"
fi
exit "$fail"
