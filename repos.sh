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
#     ./repos.sh evening     # вечерний чек: грязь / фича-ветки / незапушенное
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

cmd_evening() {
  # Вечерний чек: не осталось ли незакоммиченного, фича-веток, незапушенного.
  local any=0
  for r in "${REPOS[@]}"; do _is_repo "$r" || continue
    local issues=() br dirty up ahead default
    br=$(git -C "$ROOT/$r" rev-parse --abbrev-ref HEAD 2>/dev/null)
    dirty=$(git -C "$ROOT/$r" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
    [ "$dirty" -gt 0 ] && issues+=("${C_YLW}незакоммичено: ${dirty} файл(ов)${C_RESET}")

    # Дефолтная ветка: origin/HEAD, иначе эвристика main/master (локальный или remote-tracking ref).
    default=$(git -C "$ROOT/$r" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||')
    if [ -z "$default" ]; then
      for cand in main master; do
        if git -C "$ROOT/$r" show-ref --verify --quiet "refs/heads/$cand" \
           || git -C "$ROOT/$r" show-ref --verify --quiet "refs/remotes/origin/$cand"; then
          default="$cand"; break
        fi
      done
    fi
    if [ "$br" = "HEAD" ]; then
      issues+=("${C_RED}detached HEAD${C_RESET}")
    elif [ -n "$default" ] && [ "$br" != "$default" ]; then
      issues+=("${C_CYN}фича-ветка: ${br} (дефолт: ${default})${C_RESET}")
    fi

    up=$(git -C "$ROOT/$r" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)
    if [ -n "$up" ]; then
      ahead=$(git -C "$ROOT/$r" rev-list --count "${up}..HEAD" 2>/dev/null || echo 0)
      [ "$ahead" -gt 0 ] && issues+=("${C_YLW}незапушено: ↑${ahead} коммит(ов) относительно ${up}${C_RESET}")
    elif [ "$br" != "HEAD" ] && [ -n "$(git -C "$ROOT/$r" remote 2>/dev/null)" ]; then
      issues+=("${C_YLW}нет upstream у ветки ${br} — коммиты не уходят на remote${C_RESET}")
    fi

    if [ "${#issues[@]}" -gt 0 ]; then
      any=1
      printf "%b== %s ==%b\n" "$C_BLD" "$r" "$C_RESET"
      local i; for i in "${issues[@]}"; do printf "   %b\n" "$i"; done
    fi
  done
  if [ "$any" -eq 0 ]; then printf "%bвсё чисто — можно закрывать день%b\n" "$C_GRN" "$C_RESET"; fi
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

# Досинхрон недостающих репо набора по манифесту зонтика (день-2, а не первичный bootstrap).
# Клонирует только отсутствующие git_dir; существующие пропускает. Один манифест с зонтиком.
cmd_install() {
  local manifest=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --manifest)
        manifest="${2:-}"
        [ -n "$manifest" ] || { echo "нужен путь после --manifest"; return 2; }
        shift 2 ;;
      *) echo "usage: ./repos.sh install --manifest <path-to-workspace-manifest.toml>"; return 2 ;;
    esac
  done
  [ -n "$manifest" ] || { echo "нужен --manifest <path>"; return 2; }
  [ -f "$manifest" ] || { echo "манифест не найден: $manifest"; return 2; }
  command -v python3 >/dev/null 2>&1 || { echo "нужен python3"; return 1; }

  python3 - "$manifest" <<'PYEOF' | while IFS=$'\t' read -r gd url ref; do
import sys
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
data = tomllib.loads(open(sys.argv[1], encoding="utf-8").read())
seen = set()
for section in ("cores", "apps", "tools"):
    for cid, m in (data.get(section) or {}).items():
        if m.get("member"):
            continue                       # member делит git_dir с родителем — не клоним отдельно
        gd = m.get("git_dir")
        if not gd or gd in seen:
            continue
        seen.add(gd)
        ref = m.get("sha") or ""
        if not ref:
            t = m.get("tag", "-")
            ref = t if t not in ("-", None) else "@HEAD"
        print("\t".join([gd, m.get("repo_url", ""), ref]))
PYEOF
    [ -n "$gd" ] || continue
    if [ -z "$url" ]; then
      printf "%b!! %-22s пустой repo_url в манифесте — заполни%b\n" "$C_RED" "$gd" "$C_RESET"
      continue
    fi
    if [ -e "$ROOT/$gd/.git" ]; then    # dir ИЛИ file (.git-file у worktree) — как в автодискавери
      printf "%b== %-22s есть, пропуск (обновляй через ./repos.sh pull) ==%b\n" "$C_DIM" "$gd" "$C_RESET"
      continue
    fi
    printf "%b== clone %s <= %s ==%b\n" "$C_BLD" "$gd" "$url" "$C_RESET"
    if git clone "$url" "$ROOT/$gd"; then
      [ "$ref" != "@HEAD" ] && git -C "$ROOT/$gd" checkout --quiet "$ref"
    else
      printf "%b!! clone %s не удался%b\n" "$C_RED" "$gd" "$C_RESET"
    fi
  done
}

action="${1:-status}"; shift || true
case "$action" in
  status)    cmd_status ;;
  fetch)     cmd_fetch ;;
  pull)      cmd_pull ;;
  dirty)     cmd_dirty ;;
  evening)   cmd_evening ;;
  branches)  cmd_branches ;;
  bootstrap) cmd_bootstrap ;;
  install)   cmd_install "$@" ;;
  exec)      cmd_exec "$@" ;;
  *) echo "usage: ./repos.sh {status|fetch|pull|dirty|evening|branches|bootstrap|install --manifest <path>|exec '<cmd>'}"; exit 2 ;;
esac
