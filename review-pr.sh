#!/bin/sh
# review-pr.sh — терминальный прогон codex-ревью PR с публикацией вердикта
# как PR review от отдельного аккаунта-ревьюера.
#
# Зачем: прогон в GitHub Actions (codex-review.yml) фейлился примерно в
# половине запусков; локальный `codex exec` под живой авторизацией оператора
# надёжнее. Сам ревьюер НЕ дублируется: используется review-kit целевого репо
# (`scripts/review/local.sh`) — та же схема, тот же промпт, те же пороги.
# Эта обвязка добавляет только выбор диапазона PR и публикацию.
#
# Публикация — единственное место, где используется отдельный профиль gh
# (GH_CONFIG_DIR, по умолчанию ~/.config/review, аккаунт ai-prosto):
# ревью «мнение» (вариант (а) дизайна) — approve/request-changes видны в PR,
# но формально мерж не блокируют; авторитетом ревью станет отдельным решением.
#
# Маппинг вердикта кита на действие (тот же порог, что в CI):
#   кит 0 (чисто или только minor/nit) → --approve, находки в теле;
#   кит 1 (blocker/major)              → --request-changes;
#   кит 2/3 (конфигурация/ревьюер)     → НИЧЕГО не публикуем — молчаливый
#                                        approve при сломанном ревьюере
#                                        невозможен по построению.
#
# Коды выхода:
#   0 — чисто, approve опубликован (или dry-run);
#   1 — blocker/major, request-changes опубликован (или dry-run);
#   2 — конфигурация/аргументы/состояние PR/публикация;
#   3 — ревьюер не отработал (проброс из кита);
#   4 — голова PR уехала между прогоном и публикацией — перегнать.
#
# Рабочее дерево целевого репо НЕ трогается: голова PR фетчится в служебный
# ref refs/review/pr-<N>, кит работает по ref'ам без checkout.
set -eu

usage() {
    echo "usage: review-pr.sh <repo> <pr-number> [--dry-run]" >&2
    echo "  <repo> — имя каталога репо во флоте (например dispatcher)" >&2
}

die() {
    _code="$1"; shift
    echo "$*" >&2
    exit "$_code"
}

# Профиль публикации. REVIEW_LOGIN сверяется с фактическим логином профиля:
# опечатка в GH_CONFIG_DIR молча постила бы ревью от ОСНОВНОГО аккаунта —
# «независимое ревью» от самого автора PR. Отказ дешевле такого конфуза.
REVIEW_GH_CONFIG_DIR="${REVIEW_GH_CONFIG_DIR:-$HOME/.config/review}"
REVIEW_LOGIN="${REVIEW_LOGIN:-ai-prosto}"

gh_r() {
    GH_CONFIG_DIR="$REVIEW_GH_CONFIG_DIR" gh "$@"
}

# Корень флота — родитель devtools/, где лежит этот скрипт; FLEET_ROOT —
# явный оверрайд для тестов и ephemeral-workspace (та же логика, что
# WORKSPACE в Makefile).
script_dir=$(cd "$(dirname "$0")" && pwd)
FLEET_ROOT="${FLEET_ROOT:-$(dirname "$script_dir")}"

repo=""
pr=""
dry_run=0
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) dry_run=1; shift ;;
        -*) usage; exit 2 ;;
        *)
            if [ -z "$repo" ]; then repo="$1"
            elif [ -z "$pr" ]; then pr="$1"
            else usage; exit 2
            fi
            shift ;;
    esac
done
if [ -z "$repo" ] || [ -z "$pr" ]; then
    usage
    exit 2
fi
case "$pr" in
    *[!0-9]*|"") die 2 "номер PR должен быть числом, получено: '$pr'" ;;
esac

repo_dir="$FLEET_ROOT/$repo"
[ -d "$repo_dir" ] || die 2 "репо '$repo' не найдено в $FLEET_ROOT"
[ -f "$repo_dir/scripts/review/local.sh" ] \
    || die 2 "в '$repo' нет scripts/review/local.sh — review-kit не завендорен"

# Slug выводится из СЫРОГО remote.origin.url (git config, не `remote
# get-url`): get-url применяет insteadOf-переписывания, и локальное зеркало
# подменило бы owner/name. Не-GitHub origin — отказ, а не догадка.
if ! origin_url=$(git -C "$repo_dir" config --get remote.origin.url); then
    die 2 "у '$repo' не настроен remote origin"
fi
case "$origin_url" in
    git@github.com:*)        slug="${origin_url#git@github.com:}" ;;
    https://github.com/*)    slug="${origin_url#https://github.com/}" ;;
    ssh://git@github.com/*)  slug="${origin_url#ssh://git@github.com/}" ;;
    *) die 2 "origin '$origin_url' не похож на GitHub — slug не вывести" ;;
esac
slug="${slug%.git}"
case "$slug" in
    */*) : ;;
    *) die 2 "не удалось разобрать owner/name из origin '$origin_url'" ;;
esac

# Preflight профиля — ДО любых обращений к PR: протухший токен должен
# называться сразу, а не после платного прогона ревьюера.
[ -d "$REVIEW_GH_CONFIG_DIR" ] || die 2 "профиля ревьюера нет: \
$REVIEW_GH_CONFIG_DIR — выполните:
  GH_CONFIG_DIR=\"$REVIEW_GH_CONFIG_DIR\" gh auth login --hostname github.com --web"
if ! login=$(gh_r api user --jq .login 2>&1); then
    die 2 "профиль ревьюера не отвечает: $login"
fi
[ "$login" = "$REVIEW_LOGIN" ] \
    || die 2 "профиль отдаёт логин '$login', ожидался '$REVIEW_LOGIN' — \
не тот аккаунт, публиковать нельзя"

if ! pr_info=$(gh_r pr view "$pr" --repo "$slug" \
    --json baseRefName,headRefOid,state \
    --jq '.baseRefName + " " + .headRefOid + " " + .state' 2>&1); then
    die 2 "не удалось прочитать PR ${slug}#${pr}: $pr_info"
fi
read -r base_ref head_oid state <<EOF
$pr_info
EOF
[ "$state" = "OPEN" ] || die 2 "PR ${slug}#${pr} не открыт (state=$state)"

# Голова PR — в служебный ref, рабочее дерево не трогаем. `+` — форс:
# перегон того же PR после нового пуша обязан обновить ref.
review_ref="refs/review/pr-$pr"
if ! fetch_err=$(git -C "$repo_dir" fetch -q origin \
    "+pull/$pr/head:$review_ref" 2>&1); then
    echo "$fetch_err" >&2
    die 2 "не удалось зафетчить голову PR ${slug}#${pr}"
fi
head_sha=$(git -C "$repo_dir" rev-parse "$review_ref")
if [ "$head_sha" != "$head_oid" ]; then
    # Не отказ: фетч свежее ответа API. Сверка перед публикацией ниже
    # решит, совпало ли в итоге.
    echo "ЗАМЕТКА: API отдал голову $head_oid, зафетчено $head_sha —" \
        "ревьюим зафетченное." >&2
fi

work=$(mktemp -d)
# shellcheck disable=SC2064
trap "rm -rf '$work'" EXIT

# Кит запускается из корня целевого репо его же копией local.sh — промпт,
# схема, пороги и контекст берутся оттуда. --fetch освежает базу: локальный
# origin/<base> без него мог бы отставать от PR.
set +e
(cd "$repo_dir" && sh scripts/review/local.sh \
    --base "origin/$base_ref" --fetch \
    --head "$review_ref" --format markdown) \
    > "$work/verdict.md" 2> "$work/local.err"
kit_code=$?
set -e
# stderr кита — всегда наружу: там предупреждения о свежести базы и причины
# отказов.
cat "$work/local.err" >&2

case "$kit_code" in
    0) action="approve" ;;
    1) action="request-changes" ;;
    2|3)
        cat "$work/verdict.md" >&2
        die "$kit_code" "ревью не состоялось (кит вернул $kit_code) —" \
            "ничего не опубликовано." ;;
    *)
        cat "$work/verdict.md" >&2
        die 3 "неожиданный код кита: $kit_code — ничего не опубликовано." ;;
esac

{
    echo "## Codex CLI review — терминальный прогон"
    echo
    echo "- PR: ${slug}#${pr}, проревьюирован head \`$head_sha\`"
    echo "- ревьюер: \`codex exec\` через review-kit репо;" \
        "публикация: $REVIEW_LOGIN"
    echo
    cat "$work/verdict.md"
    echo
    echo "<!-- codex-terminal-review head=$head_sha -->"
} > "$work/body.md"

if [ "$dry_run" -eq 1 ]; then
    echo "=== dry-run: действие --$action, ничего не публикуется ==="
    cat "$work/body.md"
    exit "$kit_code"
fi

# Сверка головы непосредственно перед публикацией: между прогоном и постом
# в PR могли запушить — тогда ревью относилось бы не к тому коду.
if ! cur_oid=$(gh_r pr view "$pr" --repo "$slug" \
    --json headRefOid --jq .headRefOid 2>&1); then
    die 2 "не удалось перепроверить голову PR перед публикацией: $cur_oid"
fi
[ "$cur_oid" = "$head_sha" ] || die 4 "голова PR уехала: ревьюирован \
$head_sha, сейчас $cur_oid — перегоните ревью."

if ! gh_r pr review "$pr" --repo "$slug" "--$action" \
    --body-file "$work/body.md"; then
    die 2 "прогон завершён (--$action), но публикация ревью не удалась."
fi
echo "опубликовано: --$action на ${slug}#${pr} (head $head_sha) от $login"
exit "$kit_code"
