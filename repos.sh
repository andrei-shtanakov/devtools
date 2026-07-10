#!/usr/bin/env bash
# ============================================================
#  repos.sh — операции над всеми репозиториями экосистемы сразу.
#  Структуру НЕ меняет: это polyrepo-обёртка, не submodules.
#
#  Живёт в devtools/ (репо-обёртка над workspace-родителем).
#  Примеры:
#     ./repos.sh status      # ветка / ahead-behind / грязь по каждому
#     ./repos.sh fetch       # git fetch --all --prune везде
#     ./repos.sh pull        # git pull --ff-only по ТЕКУЩЕЙ ветке
#     ./repos.sh dirty       # только репо с незакоммиченным
#     ./repos.sh branches    # сводка веток
#     ./repos.sh bootstrap   # uv sync (python) + cargo build (arbiter)
#     ./repos.sh exec 'git log --oneline -3'
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Автодискавери: все каталоги верхнего уровня workspace с .git (dir или file).
REPOS=()
while IFS= read -r _g; do
  REPOS+=("$(basename "$(dirname "$_g")")")
done < <(find "$ROOT" -mindepth 2 -maxdepth 2 -name .git \( -type d -o -type f \) 2>/dev/null | sort)

# Цвета только в интерактивном терминале.
if [ -t 1 ]; then
  C_RESET=$'\033[0m'; C_DIM=$'\033[2m'; C_RED=$'\033[31m'
  C_GRN=$'\033[32m'; C_YLW=$'\033[33m'; C_CYN=$'\033[36m'; C_BLD=$'\033[1m'
else
  C_RESET=""; C_DIM=""; C_RED=""; C_GRN=""; C_YLW=""; C_CYN=""; C_BLD=""
fi

_is_repo() { [ -d "$ROOT/$1/.git" ]; }

cmd_status() {
  printf "%b%-26s %-26s %-10s %s%b\n" "$C_BLD" "REPO" "BRANCH" "↑↓" "DIRTY" "$C_RESET"
  for r in "${REPOS[@]}"; do
    if ! _is_repo "$r"; then printf "%b%-26s missing%b\n" "$C_RED" "$r" "$C_RESET"; continue; fi
    local br up ab dirty marker
    br=$(git -C "$ROOT/$r" rev-parse --abbrev-ref HEAD 2>/dev/null)
    up=$(git -C "$ROOT/$r" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)
    if [ -n "$up" ]; then
      read -r behind ahead < <(git -C "$ROOT/$r" rev-list --left-right --count "${up}...HEAD" 2>/dev/null || echo "0 0")
      ab="↑${ahead} ↓${behind}"
    else
      ab="${C_DIM}no-upstream${C_RESET}"
    fi
    dirty=$(git -C "$ROOT/$r" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
    if [ "$dirty" -gt 0 ]; then marker="${C_YLW}${dirty} changed${C_RESET}"; else marker="${C_GRN}clean${C_RESET}"; fi
    printf "%-26s %b%-26s%b %-10b %b\n" "$r" "$C_CYN" "$br" "$C_RESET" "$ab" "$marker"
  done
}

cmd_fetch() {
  for r in "${REPOS[@]}"; do _is_repo "$r" || continue
    printf "%b== fetch %s ==%b\n" "$C_BLD" "$r" "$C_RESET"
    git -C "$ROOT/$r" fetch --all --prune
  done
}

cmd_pull() {
  for r in "${REPOS[@]}"; do _is_repo "$r" || continue
    local br up dirty
    br=$(git -C "$ROOT/$r" rev-parse --abbrev-ref HEAD 2>/dev/null)
    up=$(git -C "$ROOT/$r" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)
    dirty=$(git -C "$ROOT/$r" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
    if [ "$br" = "HEAD" ]; then printf "%b! %-22s detached HEAD — skip%b\n" "$C_YLW" "$r" "$C_RESET"; continue; fi
    if [ -z "$up" ]; then printf "%b! %-22s нет upstream (%s) — skip%b\n" "$C_YLW" "$r" "$br" "$C_RESET"; continue; fi
    if [ "$dirty" -gt 0 ]; then printf "%b! %-22s грязный (%s changed) — skip, разрули вручную%b\n" "$C_RED" "$r" "$dirty" "$C_RESET"; continue; fi
    printf "%b== pull %s (%s) ==%b\n" "$C_BLD" "$r" "$br" "$C_RESET"
    git -C "$ROOT/$r" pull --ff-only
  done
}

cmd_dirty() {
  local any=0
  for r in "${REPOS[@]}"; do _is_repo "$r" || continue
    local n; n=$(git -C "$ROOT/$r" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
    if [ "$n" -gt 0 ]; then
      any=1
      printf "%b== %s (%s changed) ==%b\n" "$C_YLW" "$r" "$n" "$C_RESET"
      git -C "$ROOT/$r" status --short
      echo
    fi
  done
  [ "$any" -eq 0 ] && printf "%bвсё чисто%b\n" "$C_GRN" "$C_RESET"
}

cmd_branches() {
  for r in "${REPOS[@]}"; do _is_repo "$r" || continue
    printf "%-26s %s\n" "$r" "$(git -C "$ROOT/$r" rev-parse --abbrev-ref HEAD 2>/dev/null)"
  done
}

cmd_bootstrap() {
  for r in "${REPOS[@]}"; do _is_repo "$r" || continue
    if [ -f "$ROOT/$r/pyproject.toml" ] && command -v uv >/dev/null 2>&1; then
      printf "%b== uv sync %s ==%b\n" "$C_BLD" "$r" "$C_RESET"
      (cd "$ROOT/$r" && uv sync)
    fi
    if [ -f "$ROOT/$r/Cargo.toml" ] && command -v cargo >/dev/null 2>&1; then
      printf "%b== cargo build %s ==%b\n" "$C_BLD" "$r" "$C_RESET"
      (cd "$ROOT/$r" && cargo build)
    fi
  done
}

cmd_exec() {
  local cmdline="$*"
  [ -z "$cmdline" ] && { echo "usage: ./repos.sh exec '<command>'"; exit 2; }
  for r in "${REPOS[@]}"; do _is_repo "$r" || continue
    printf "%b== %s ==%b\n" "$C_BLD" "$r" "$C_RESET"
    (cd "$ROOT/$r" && eval "$cmdline")
  done
}

action="${1:-status}"; shift || true
case "$action" in
  status)    cmd_status ;;
  fetch)     cmd_fetch ;;
  pull)      cmd_pull ;;
  dirty)     cmd_dirty ;;
  branches)  cmd_branches ;;
  bootstrap) cmd_bootstrap ;;
  exec)      cmd_exec "$@" ;;
  *) echo "usage: ./repos.sh {status|fetch|pull|dirty|branches|bootstrap|exec '<cmd>'}"; exit 2 ;;
esac
