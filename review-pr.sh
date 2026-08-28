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
#   0 — чисто, approve опубликован (или dry-run / унаследован);
#   1 — blocker/major, request-changes опубликован (или dry-run / унаследован);
#   2 — конфигурация/аргументы/состояние PR/публикация;
#   3 — ревьюер не отработал (проброс из кита);
#   4 — голова PR уехала между прогоном и публикацией — перегнать.
#
# Дедуп по отпечатку входа (devtools#72, кит-половина — steward#126):
# если local.sh целевого репо знает --fingerprint-only (feature-detect по
# литералу, как сам кит определяет возможности build-prompt), перед прогоном
# вычисляется sha256-отпечаток входа ревью. Совпал с отпечатком из маркера
# новейшего доверенного ревью (автор $REVIEW_LOGIN, строгий формат) — вердикт
# наследуется: codex не вызывается; при том же head ничего не публикуется,
# при новом head (update-branch) публикуется то же действие с телом-ссылкой.
# Наследуются и зелёные, и красные. --fresh обходит ТОЛЬКО поиск наследуемого
# вердикта: отпечаток по-прежнему вычисляется и публикуется в маркере.
# Отпечаток и фактическое ревью обязаны видеть одно состояние базы, поэтому
# база освежается явным fetch ДО отпечатка, и оба вызова кита идут без
# --fetch (--fingerprint-only с --fetch несовместим по построению).
#
# Рабочее дерево целевого репо НЕ трогается: голова PR фетчится в служебный
# ref refs/review/pr-<N>, кит работает по ref'ам без checkout.
set -eu

usage() {
    echo "usage: review-pr.sh <repo> <pr-number> [--dry-run] [--fresh]" >&2
    echo "  <repo> — имя каталога репо во флоте (например dispatcher)" >&2
    echo "  --fresh — не наследовать вердикт даже при совпавшем отпечатке" >&2
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
fresh=0
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) dry_run=1; shift ;;
        --fresh) fresh=1; shift ;;
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

# Публикация с финальной сверкой головы: между прогоном и постом в PR могли
# запушить — тогда ревью относилось бы не к тому коду. $1 — действие.
publish() {
    if ! cur_oid=$(gh_r pr view "$pr" --repo "$slug" \
        --json headRefOid --jq .headRefOid 2>&1); then
        die 2 "не удалось перепроверить голову PR перед публикацией: $cur_oid"
    fi
    [ "$cur_oid" = "$head_sha" ] || die 4 "голова PR уехала: ревьюирован \
$head_sha, сейчас $cur_oid — перегоните ревью."
    if ! gh_r pr review "$pr" --repo "$slug" "--$1" \
        --body-file "$work/body.md"; then
        die 2 "прогон завершён (--$1), но публикация ревью не удалась."
    fi
    echo "опубликовано: --$1 на ${slug}#${pr} (head $head_sha) от $login"
}

# --- Отпечаток входа ревью (дедуп, devtools#72) ------------------------------
# Feature-detect по литералу в local.sh ЦЕЛЕВОГО репо — тот же паттерн, каким
# кит сам определяет возможности build-prompt (--generated-list). Нет флага →
# обычный полный прогон без дедупа; ре-вендор флота не пререквизит.
fp=""
fp_supported=0
if grep -q -- '--fingerprint-only' "$repo_dir/scripts/review/local.sh"; then
    fp_supported=1
fi
if [ "$fp_supported" -eq 1 ]; then
    # Согласованность диапазона: отпечаток и фактическое ревью обязаны видеть
    # ОДНО состояние базы — освежаем её явным fetch здесь, дальше оба вызова
    # кита идут без --fetch (fp-режим с --fetch несовместим по построению).
    # Refspec с явным destination: оппортунистическое обновление tracking-ref
    # (git ≥1.8.4) покрывает штатный клон, но зависит от refspec-конфигурации
    # remote'а — single-branch клон с ДРУГОЙ базой PR оставил бы origin/<base>
    # stale, и отпечаток с наследованием считались бы по устаревшему
    # диапазону. Destination делает освежение безусловным (боевая находка
    # codex-ревью этого же PR, devtools#73).
    if ! fetch_err=$(git -C "$repo_dir" fetch -q origin \
        "+refs/heads/$base_ref:refs/remotes/origin/$base_ref" 2>&1); then
        echo "$fetch_err" >&2
        die 2 "не удалось освежить базу origin/$base_ref перед отпечатком"
    fi
    set +e
    fp_out=$( (cd "$repo_dir" && sh scripts/review/local.sh \
        --base "origin/$base_ref" --head "$review_ref" --fingerprint-only) \
        2> "$work/fp.err")
    fp_code=$?
    set -e
    cat "$work/fp.err" >&2
    case "$fp_code" in
        0)
            if [ -z "$fp_out" ]; then
                # «наследовать нечего и ревьюировать нечего» — дедуп мимо,
                # дальше обычный прогон разбирается сам.
                :
            elif [ "$(printf '%s\n' "$fp_out" | wc -l)" -eq 1 ] \
                && printf '%s\n' "$fp_out" | grep -Eqx '[0-9a-f]{64}'; then
                fp="$fp_out"
            else
                echo "ЗАМЕТКА: fp-режим кита нарушил stdout-контракт" \
                    "(ожидалась одна строка 64-hex) — дедуп пропущен." >&2
            fi ;;
        2|3)
            die "$fp_code" "отпечаток не вычислен (кит вернул $fp_code) —" \
                "ничего не опубликовано." ;;
        *)
            die 3 "неожиданный код fp-режима кита: $fp_code —" \
                "ничего не опубликовано." ;;
    esac
fi

# --- Поиск наследуемого вердикта ---------------------------------------------
# Кандидат — ровно НОВЕЙШЕЕ ревью доверенного автора, и оно обязано полностью
# распарситься: один маркер строгого формата, состояние однозначно переводится
# в exit code, отпечаток совпал. Любое отклонение (DISMISSED, неизвестное
# состояние, битый/дублированный маркер, другой fp) — cache miss и полный
# прогон: заглядывать в БОЛЕЕ СТАРЫЕ ревью нельзя — dismissal новейшего мог
# быть человеческим отзывом вердикта, воскрешать его из истории — не дело
# дедупа.
inh_state=""
inh_head=""
if [ -n "$fp" ] && [ "$fresh" -eq 0 ]; then
    if candidate=$(gh_r api --paginate --slurp \
        "repos/$slug/pulls/$pr/reviews" \
        --jq '([ .[][] | select(.user.login == "'"$REVIEW_LOGIN"'") ] | last) as $r
            | if $r == null then "none none none"
              else
                (($r.body // "") | [scan("<!-- codex-terminal-review ")] | length) as $n
                | (($r.body // "") | [match("<!-- codex-terminal-review head=([0-9a-f]{40}) fp=([0-9a-f]{64}) -->")]) as $ms
                | if $n == 1 and ($ms | length) == 1
                     and ($r.state == "APPROVED" or $r.state == "CHANGES_REQUESTED")
                  then $r.state + " " + $ms[0].captures[0].string + " " + $ms[0].captures[1].string
                  else "miss miss miss" end
              end' 2> "$work/reviews.err"); then
        read -r c_state c_head c_fp <<EOF
$candidate
EOF
        if [ "$c_fp" = "$fp" ]; then
            inh_state="$c_state"
            inh_head="$c_head"
        fi
    else
        cat "$work/reviews.err" >&2
        echo "ЗАМЕТКА: прошлые ревью не прочитались — дедуп пропущен," \
            "идёт полный прогон." >&2
    fi
fi

if [ -n "$inh_state" ]; then
    case "$inh_state" in
        APPROVED)          action="approve";         kit_code=0 ;;
        CHANGES_REQUESTED) action="request-changes"; kit_code=1 ;;
    esac
    if [ "$inh_head" = "$head_sha" ]; then
        echo "вердикт унаследован (--$action): отпечаток входа совпал," \
            "head тот же $head_sha — публиковать нечего."
        exit "$kit_code"
    fi
    # head другой (update-branch, close/reopen): публикуем ТО ЖЕ действие с
    # телом-наследованием и маркером с новым head и тем же fp — codex не
    # вызывается. Наследуются и зелёные, и красные.
    {
        echo "## Codex CLI review — терминальный прогон"
        echo
        echo "- PR: ${slug}#${pr}, head \`$head_sha\`"
        echo "- вердикт унаследован от прогона по head \`$inh_head\`," \
            "отпечаток входа совпал — codex не вызывался"
        echo "- публикация: $REVIEW_LOGIN"
        echo
        echo "<!-- codex-terminal-review head=$head_sha fp=$fp -->"
    } > "$work/body.md"
    if [ "$dry_run" -eq 1 ]; then
        echo "=== dry-run: действие --$action (унаследовано)," \
            "ничего не публикуется ==="
        cat "$work/body.md"
        exit "$kit_code"
    fi
    publish "$action"
    exit "$kit_code"
fi

# --- Полный прогон -------------------------------------------------------
# Кит запускается из корня целевого репо его же копией local.sh — промпт,
# схема, пороги и контекст берутся оттуда. Свежесть базы: при fp-ките база
# уже освежена явным fetch выше (и отпечаток обязан видеть то же состояние),
# у старого кита --fetch остаётся его собственной заботой.
set -- --base "origin/$base_ref" --head "$review_ref" --format markdown
[ "$fp_supported" -eq 1 ] || set -- "$@" --fetch
set +e
(cd "$repo_dir" && sh scripts/review/local.sh "$@") \
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

# Маркер аддитивный: с отпечатком, когда кит его умеет, — из него будущие
# прогоны наследуют вердикт; без fp (старый кит) наследование невозможно.
if [ -n "$fp" ]; then
    marker="<!-- codex-terminal-review head=$head_sha fp=$fp -->"
else
    marker="<!-- codex-terminal-review head=$head_sha -->"
fi
{
    echo "## Codex CLI review — терминальный прогон"
    echo
    echo "- PR: ${slug}#${pr}, проревьюирован head \`$head_sha\`"
    echo "- ревьюер: \`codex exec\` через review-kit репо;" \
        "публикация: $REVIEW_LOGIN"
    echo
    cat "$work/verdict.md"
    echo
    echo "$marker"
} > "$work/body.md"

if [ "$dry_run" -eq 1 ]; then
    echo "=== dry-run: действие --$action, ничего не публикуется ==="
    cat "$work/body.md"
    exit "$kit_code"
fi

publish "$action"
exit "$kit_code"
