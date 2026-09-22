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
# Fail-closed: логин обязан входить в политику подписи — файл
# `policy/approvers.env` репозитория approval-policy (координаты — SSOT
# contracts/approval-policy-source/v1/source.env), прочитанный по SHA из тела
# candidate-PR (`policy: <repo>@<sha>`; у finalize и прочих PR человеческого
# мержа пина нет — судится актуальная версия); у candidate актуальная версия обязана совпасть с пином —
# иначе мерж не создаст подписи и человеческий акт сгорел бы (спека
# 2026-09-22-approver-policy-trusted-source, §4.5). Переменная окружения
# AUTHORIZED_APPROVER_ACCOUNTS больше не источник: выставленная — отказ.
# PR открыт; mergeStateStatus из разрешающих (тот же перечень, что у
# merge-pr.sh); голова пинуется `sha=` (--expect-head — пин вызывающего).
# Стратегия по умолчанию — merge-коммит: подпись читается из фактов мержа,
# не из формы, а merge-коммит сохраняет историю candidate-ветки видимой.
#
# Коды выхода: 0 — мерж выполнен (или показан при --dry-run); 2 —
# аргументы/профиль/состояние PR (в т.ч. тело без строки `policy:`, версия
# политики не прочитана); 3 — актор не авторизован (логин вне политики,
# политика сменилась после candidate, выставленная переменная); 4 — форджа
# отклонила мерж.
set -eu

usage() {
    echo "usage: human-merge.sh <repo> <pr-number> [--expect-head <sha>]" \
        "[--merge|--squash] [--dry-run]" >&2
    echo "  <repo> — имя каталога репо во флоте; мерж от учётки ОПЕРАТОРА" >&2
    echo "  (HUMAN_GH_CONFIG_DIR — иной профиль gh; ~/.config/review — отказ)" >&2
    echo "  логин обязан входить в политику approval-policy (версия — пин из тела PR)" >&2
    echo "  переменная AUTHORIZED_APPROVER_ACCOUNTS больше не читается — выставленная даёт отказ" >&2
}

die() { _code="$1"; shift; echo "$*" >&2; exit "$_code"; }

script_dir=$(cd "$(dirname "$0")" && pwd)
. "$script_dir/ssot_env.sh"
. "$script_dir/approval_branches.sh"
FLEET_ROOT="${FLEET_ROOT:-$(dirname "$script_dir")}"
APPROVAL_PATTERNS="${APPROVAL_PATTERNS:-\
$script_dir/contracts/approval-branches/v1/patterns.env}"

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

# Источник политики подписи — репозиторий approval-policy (спека
# 2026-09-22-approver-policy-trusted-source, §4.5). Переменная окружения
# больше не источник: выставленная — отказ, чтобы старое правило не
# исполнялось молча не так, как задумано.
[ -z "${AUTHORIZED_APPROVER_ACCOUNTS+x}" ] || die 3 "AUTHORIZED_APPROVER_ACCOUNTS выставлена, но переменная больше не источник политики — источник репозиторий approval-policy; снимите её"
src="$script_dir/contracts/approval-policy-source/v1/source.env"
p_repo=$(ssot_key "$src" APPROVAL_POLICY_REPO "SSOT источника политики") || exit $?
p_ref=$(ssot_key "$src" APPROVAL_POLICY_REF "SSOT источника политики") || exit $?
p_path=$(ssot_key "$src" APPROVAL_POLICY_PATH "SSOT источника политики") || exit $?
p_owner="${p_repo%%/*}"; p_name="${p_repo#*/}"

login=$(gh_h api user --jq .login 2>&1) || die 2 "профиль оператора не отвечает: $login"

if ! facts=$(gh_h pr view "$pr" --repo "$slug" \
        --json state,headRefOid,mergeStateStatus,body,headRefName \
        --jq '.state, .headRefOid, .mergeStateStatus, (.body // "" | gsub("\n";" ")), .headRefName' 2>&1); then
    die 2 "не удалось прочитать PR ${slug}#${pr}: $facts"
fi
state=$(printf '%s\n' "$facts" | sed -n '1p')
head_oid=$(printf '%s\n' "$facts" | sed -n '2p')
merge_state=$(printf '%s\n' "$facts" | sed -n '3p')
body=$(printf '%s\n' "$facts" | sed -n '4p')
head_ref=$(printf '%s\n' "$facts" | sed -n '5p')
[ "$state" = "OPEN" ] || die 2 "PR ${slug}#${pr} не открыт (state=$state)"
[ -n "$head_oid" ] || die 2 "PR ${slug}#${pr}: голова не установлена"
# Имя ветки — вход классификации ниже; пустое или `null` ушло бы в ветвь
# «прочий PR» и сняло проверку пина (major адресного ревью PR #344).
[ -n "$head_ref" ] && [ "$head_ref" != "null" ] \
    || die 2 "PR ${slug}#${pr}: имя head-ветки не установлено — тип PR неизвестен, мерж не выполняется"

# Версия политики, по которой судится логин. У candidate-PR (форма ветки —
# из того же SSOT, что у merge-pr.sh) версия ЗАКРЕПЛЕНА заявкой и написана
# в теле (`policy: <repo>@<sha>`, пишет approve_node): актуальная обязана
# совпасть с пином, иначе мерж не создаст подписи и акт сгорит. У любого
# другого PR человеческого мержа (finalize-PR под «Мерж: человек», лейбл
# human-merge-required) пина нет и быть не должно — судится актуальная
# версия.
_globs=$(approval_globs "$APPROVAL_PATTERNS") || exit $?
candidate_glob=$(printf '%s\n' "$_globs" | sed -n '1p')
finalize_glob=$(printf '%s\n' "$_globs" | sed -n '2p')
current=$(gh_h api graphql -f 'query=query($o:String!,$n:String!,$q:String!,$p:String!){repository(owner:$o,name:$n){ref(qualifiedName:$q){target{... on Commit{history(first:1,path:$p){nodes{oid}}}}}}}' \
    -F "o=$p_owner" -F "n=$p_name" -F "q=refs/heads/$p_ref" -F "p=$p_path" \
    --jq '.data.repository.ref.target.history.nodes[0].oid' 2>&1) \
    || die 2 "версия политики $p_repo не прочитана: $current"
case "$head_ref" in
    $finalize_glob)
        # finalize — суффикс candidate-формы, проверяется ПЕРВЫМ: иначе
        # candidate-глоб накрыл бы и его (тот же порядок, что у merge-pr.sh).
        version="$current" ;;
    $candidate_glob)
        pin=$(printf '%s\n' "$body" | sed -n 's/.*policy: [^@ ]*@\([0-9a-f]\{40\}\).*/\1/p' | head -n 1)
        [ -n "$pin" ] || die 2 "PR ${slug}#${pr}: candidate без строки 'policy: <repo>@<sha>' в теле — candidate старого формата; новый candidate"
        [ "$current" = "$pin" ] || die 3 "политика сменилась после candidate (закреплена $pin, актуальная $current) — мерж не создаст подписи; новый candidate"
        version="$pin" ;;
    *)
        version="$current" ;;
esac
text=$(gh_h api graphql -f 'query=query($o:String!,$n:String!,$s:GitObjectID!,$p:String!){repository(owner:$o,name:$n){object(oid:$s){... on Commit{file(path:$p){object{... on Blob{text}}}}}}}' \
    -F "o=$p_owner" -F "n=$p_name" -F "s=$version" -F "p=$p_path" \
    --jq '.data.repository.object.file.object.text' 2>&1) \
    || die 2 "политика $p_repo@$version не прочитана: $text"
# `file: null` печатается как строка `null` → ниже отказ «без ключа» (код 3):
# для скрипта это приемлемо и названо здесь.
allow=$(printf '%s\n' "$text" | sed -n 's/^[[:space:]]*AUTHORIZED_APPROVER_ACCOUNTS=//p' | head -n 1 | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
[ -n "$allow" ] || die 3 "политика $p_repo@$version без AUTHORIZED_APPROVER_ACCOUNTS — подписать не может никто"
_ok=0
_saved_ifs="$IFS"; IFS=','
for _acc in $allow; do
    _acc=$(printf '%s' "$_acc" | tr -d ' ')
    [ "$_acc" = "$login" ] && _ok=1
done
IFS="$_saved_ifs"
[ "$_ok" -eq 1 ] || die 3 "логин '$login' не входит в политику $p_repo@$version ($allow)"
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
