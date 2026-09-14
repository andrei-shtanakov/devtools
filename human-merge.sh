#!/bin/sh
# human-merge.sh — ТРИГГЕР человеческого мержа по команде (ADR-ECO-011 D6).
#
# Человеческий акт (candidate-PR §I12 — всегда; финализирующий и любой PR
# по human-политике — лейбл `human-merge-required`) не обязан совершаться в
# браузере: цель DarkFactory — мерж по команде, а не по клику. Команда
# исполняет мерж ОТ УЧЁТКИ ЧЕЛОВЕКА — профилем оператора (`gh` по
# умолчанию, либо HUMAN_GH_CONFIG_DIR), НИКОГДА не ~/.config/review:
# агентский профиль записал бы человеческий акт агентским, и подпись §I12
# (merged_by ∈ allowlist) не сложилась бы. Это зеркало merge-pr.sh: тот
# сверяет, что мержит ai-prosto, этот — что мержит человек из allowlist.
#
# Fail-closed: логин обязан входить в AUTHORIZED_APPROVER_ACCOUNTS (пустой
# список — отказ, как у §I12: «подписать не может никто»); PR открыт;
# mergeStateStatus из разрешающих (тот же перечень, что у merge-pr.sh);
# голова пинуется `sha=` (--expect-head — пин вызывающего). Стратегия по
# умолчанию — merge-коммит: подпись читается из фактов мержа, не из формы,
# а merge-коммит сохраняет историю candidate-ветки видимой.
#
# Коды выхода: 0 — мерж выполнен (или показан при --dry-run); 2 —
# аргументы/профиль/состояние PR; 3 — актор не авторизован; 4 — форджа
# отклонила мерж.
set -eu

usage() {
    echo "usage: human-merge.sh <repo> <pr-number> [--expect-head <sha>]" \
        "[--merge|--squash] [--dry-run]" >&2
    echo "  <repo> — имя каталога репо во флоте; мерж от учётки ОПЕРАТОРА" >&2
    echo "  (HUMAN_GH_CONFIG_DIR — иной профиль gh; ~/.config/review — отказ)" >&2
    echo "  логин обязан входить в AUTHORIZED_APPROVER_ACCOUNTS (пусто — отказ)" >&2
}

die() { _code="$1"; shift; echo "$*" >&2; exit "$_code"; }

script_dir=$(cd "$(dirname "$0")" && pwd)
FLEET_ROOT="${FLEET_ROOT:-$(dirname "$script_dir")}"

[ $# -ge 2 ] || { usage; exit 2; }
repo="$1"; pr="$2"; shift 2
expect_head=""; method="merge"; dry_run=0
while [ $# -gt 0 ]; do
    case "$1" in
        --expect-head)
            [ $# -ge 2 ] || die 2 "--expect-head требует sha"
            expect_head="$2"; shift 2 ;;
        --merge) method="merge"; shift ;;
        --squash) method="squash"; shift ;;
        --dry-run) dry_run=1; shift ;;
        *) usage; exit 2 ;;
    esac
done
case "$pr" in *[!0-9]*|"") die 2 "номер PR должен быть числом: '$pr'" ;; esac

# Профиль человека: явный HUMAN_GH_CONFIG_DIR либо дефолтный gh оператора.
# Агентский профиль (`~/.config/review`, он же MERGE_GH_CONFIG_DIR у
# merge-pr.sh) здесь отказ по построению — сверка логина ниже его не
# пропустит, но названный явно он отбивается раньше и понятнее.
human_profile="${HUMAN_GH_CONFIG_DIR:-}"
agent_profile="${MERGE_GH_CONFIG_DIR:-$HOME/.config/review}"
if [ -n "$human_profile" ] && [ "$human_profile" = "$agent_profile" ]; then
    die 2 "HUMAN_GH_CONFIG_DIR указывает на агентский профиль ($agent_profile) — \
человеческий мерж от учётки агента невозможен"
fi
gh_h() {
    if [ -n "$human_profile" ]; then
        GH_CONFIG_DIR="$human_profile" gh "$@"
    else
        gh "$@"
    fi
}

repo_dir="$FLEET_ROOT/$repo"
[ -d "$repo_dir" ] || die 2 "репо '$repo' не найдено в $FLEET_ROOT"
origin_url=$(git -C "$repo_dir" config --get remote.origin.url) \
    || die 2 "у '$repo' не настроен remote origin"
case "$origin_url" in
    git@github.com:*)       slug="${origin_url#git@github.com:}" ;;
    https://github.com/*)   slug="${origin_url#https://github.com/}" ;;
    ssh://git@github.com/*) slug="${origin_url#ssh://git@github.com/}" ;;
    *) die 2 "origin '$origin_url' не похож на GitHub" ;;
esac
slug="${slug%.git}"

# Allowlist — тот же env, что читает механика одобрения (§I12): пустой
# список означает «подписать не может никто», и мерж под таким списком
# был бы актом, который заявка не признает.
allow="${AUTHORIZED_APPROVER_ACCOUNTS:-}"
[ -n "$allow" ] || die 3 "AUTHORIZED_APPROVER_ACCOUNTS пуст — человеческий акт не авторизован"
login=$(gh_h api user --jq .login 2>&1) || die 2 "профиль оператора не отвечает: $login"
_ok=0
_saved_ifs="$IFS"; IFS=','
for _acc in $allow; do
    _acc=$(printf '%s' "$_acc" | tr -d ' ')
    [ "$_acc" = "$login" ] && _ok=1
done
IFS="$_saved_ifs"
[ "$_ok" -eq 1 ] || die 3 "логин '$login' не входит в AUTHORIZED_APPROVER_ACCOUNTS ($allow)"

if ! facts=$(gh_h pr view "$pr" --repo "$slug" \
        --json state,headRefOid,mergeStateStatus \
        --jq '.state, .headRefOid, .mergeStateStatus' 2>&1); then
    die 2 "не удалось прочитать PR ${slug}#${pr}: $facts"
fi
state=$(printf '%s\n' "$facts" | sed -n '1p')
head_oid=$(printf '%s\n' "$facts" | sed -n '2p')
merge_state=$(printf '%s\n' "$facts" | sed -n '3p')
[ "$state" = "OPEN" ] || die 2 "PR ${slug}#${pr} не открыт (state=$state)"
[ -n "$head_oid" ] || die 2 "PR ${slug}#${pr}: голова не установлена"
if [ -n "$expect_head" ] && [ "$expect_head" != "$head_oid" ]; then
    die 2 "PR ${slug}#${pr}: голова $head_oid, вызывающий ждал $expect_head — перепроверьте"
fi
# Тот же allowlist состояний, что у merge-pr.sh: BLOCKED — политика базы
# для обычного актора, человек с bypass мержит; DIRTY/BEHIND/UNKNOWN — нет.
case "$merge_state" in
    CLEAN|HAS_HOOKS|UNSTABLE|BLOCKED) : ;;
    *) die 2 "PR ${slug}#${pr}: mergeStateStatus='$merge_state' не в числе \
разрешающих мерж (CLEAN|HAS_HOOKS|UNSTABLE|BLOCKED)" ;;
esac

if [ "$dry_run" -eq 1 ]; then
    echo "human-merge (dry-run): $login мержит ${slug}#${pr} ($method, sha=$head_oid, merge_state=$merge_state)"
    exit 0
fi
if ! out=$(gh_h api -X PUT "repos/$slug/pulls/$pr/merge" \
        -f merge_method="$method" -f sha="$head_oid" 2>&1); then
    die 4 "форджа отклонила мерж ${slug}#${pr}: $out"
fi
echo "смержено человеком: ${slug}#${pr} ($method, голова $head_oid, merge_state=$merge_state) от $login"
