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
# ref refs/review/pr-<N> и раскладывается во временный detached worktree.
# Ревьюер читает дерево exact head, но исполняется доверенная копия review-kit
# из исходного чекаута; код harness из PR до вердикта не запускается.
set -eu

usage() {
    echo "usage: review-pr.sh <repo> <pr-number> [--dry-run] [--fresh]" \
        "[--write-verdict <file> | --use-verdict <file>]" \
        "[--harness claude|codex] [--model <M>]" \
        "[--max-diff-bytes N] [--max-diff-files N]" >&2
    echo "  <repo> — имя каталога репо во флоте (например dispatcher)" >&2
    echo "  --fresh — не наследовать вердикт даже при совпавшем отпечатке" >&2
    echo "  --write-verdict — атомарно сохранить результат dry-run для боевого прогона" >&2
    echo "  --use-verdict — использовать сохранённый результат при точных head + fp" >&2
    echo "  --budget-override <причина> — превысить бюджет платных прогонов" >&2
    echo "  --harness/--model — ревьюер; порядок: флаг > env REVIEW_HARNESS/" >&2
    echo "    REVIEW_MODEL > ~/.config/ai-prosto/harness.env > codex (историч.)" >&2
    echo "  внешний REVIEW_CMD побеждает всё, кроме явных флагов" >&2
    echo "  --max-diff-bytes/--max-diff-files — явно поднять потолки дифа кита" >&2
    echo "    (флаг > env REVIEW_MAX_DIFF_BYTES/REVIEW_MAX_DIFF_FILES); уходят в" >&2
    echo "    оба вызова кита (отпечаток и полный прогон), факт — в шапке вердикта" >&2
    echo "  кит с харнесс-слоем (local.sh --print-review-cmd) получает REVIEW_HARNESS/" >&2
    echo "    REVIEW_MODEL окружением; кит без него умеет только codex" >&2
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
write_verdict=""
use_verdict=""
opt_harness=""
opt_model=""
opt_max_diff_bytes=""
opt_max_diff_files=""
budget_override=""
print_review_cmd=0
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) dry_run=1; shift ;;
        --fresh) fresh=1; shift ;;
        --budget-override)
            # Причина обязательна и не может быть пробелами: перерасход
            # бюджета — решение владельца, и оно обязано быть названным.
            [ $# -ge 2 ] \
                || die 2 "--budget-override требует причину (решение владельца)"
            budget_override=$(printf '%s' "$2" | tr -d '[:space:]')
            [ -n "$budget_override" ] \
                || die 2 "--budget-override требует непустую причину"
            budget_override="$2"; shift 2 ;;
        --write-verdict)
            [ $# -ge 2 ] || die 2 "--write-verdict требует путь"
            write_verdict="$2"; shift 2 ;;
        --use-verdict)
            [ $# -ge 2 ] || die 2 "--use-verdict требует путь"
            use_verdict="$2"; shift 2 ;;
        --harness)
            [ $# -ge 2 ] || die 2 "--harness требует значение (claude|codex)"
            opt_harness="$2"; shift 2 ;;
        --model)
            [ $# -ge 2 ] || die 2 "--model требует значение"
            opt_model="$2"; shift 2 ;;
        --print-review-cmd) print_review_cmd=1; shift ;;
        # Потолки дифа кита. Пустое значение — отказ здесь: проброс ниже
        # гейтится [ -n ], и явно запрошенный оверрайд молча ушёл бы в
        # умолчание кита (тот же довод, что у local.sh --max-diff-bytes "").
        --max-diff-bytes)
            [ $# -ge 2 ] || die 2 "--max-diff-bytes требует целое число байт"
            [ -n "$2" ] || die 2 "--max-diff-bytes передан с пустым значением"
            opt_max_diff_bytes="$2"; shift 2 ;;
        --max-diff-files)
            [ $# -ge 2 ] || die 2 "--max-diff-files требует целое число"
            [ -n "$2" ] || die 2 "--max-diff-files передан с пустым значением"
            opt_max_diff_files="$2"; shift 2 ;;
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

# --- Выбор харнесса ревьюера (devtools#121) --------------------------------
# Разрешение: явный флаг > env сессии > операторский конфиг > вшитый codex.
# Конфиг — свойство машины/подписки (лимиты, тариф), не репозитория: живёт в
# ~/.config/ai-prosto/harness.env строками KEY=VALUE (parse, НЕ source —
# файл не исполняется). Уже выставленный снаружи REVIEW_CMD побеждает всё,
# кроме явных флагов --harness/--model.
#
# Материализация под кит — ниже, в configure_reviewer (devtools#222, кит
# steward @ a2d7e71): кит с харнесс-слоем читает REVIEW_HARNESS/REVIEW_MODEL
# из окружения и зовёт свой scripts/review/harness-claude сам, строку
# ревьюера для тела и отпечатка отдаёт `local.sh --print-review-cmd`. Копия
# кита без этого литерала умеет только codex (REVIEW_CMD как раньше);
# claude на ней — отказ «ре-вендорьте кит»: переходник scripts/harness/
# claude-review снят после волны devtools#228 (шаг 2 devtools#222).
harness_env_file="${AI_PROSTO_HARNESS_ENV:-$HOME/.config/ai-prosto/harness.env}"
cfg_harness=""
cfg_model=""
if [ -f "$harness_env_file" ]; then
    # Толерантность к привычной env-файловой записи (боевое claude-ревью
    # PR #121, круг 2): `export KEY=value` и ведущие пробелы принимаются —
    # иначе строка молча не матчилась бы и прогон тихо уходил на codex,
    # сжигая ровно тот лимит, ради которого файл заведён.
    cfg_harness=$(sed -n \
        's/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}REVIEW_HARNESS=//p' \
        "$harness_env_file" | tail -1)
    cfg_model=$(sed -n \
        's/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}REVIEW_MODEL=//p' \
        "$harness_env_file" | tail -1)
fi
# Модель привязана к слою, из которого пришёл харнесс (боевое claude-ревью
# PR #121, minor): харнесс со слоя выше НЕ наследует модель слоя ниже —
# `--harness codex` при конфиге claude собирал бы `codex exec -m
# claude-opus-5` и умирал на неизвестной модели. Слои: флаг > env > конфиг.
if [ -n "$opt_harness" ]; then
    harness="$opt_harness"
    model="${opt_model:-}"
elif [ -n "${REVIEW_HARNESS:-}" ]; then
    harness="$REVIEW_HARNESS"
    model="${opt_model:-${REVIEW_MODEL:-}}"
else
    harness="${cfg_harness:-codex}"
    model="${opt_model:-${REVIEW_MODEL:-${cfg_model:-}}}"
fi
case "$harness" in
    claude|codex) ;;
    *) die 2 "неизвестный харнесс: '$harness' (claude|codex)" ;;
esac
external_review_cmd=0
if [ -n "${REVIEW_CMD:-}" ] && [ -z "$opt_harness" ] && [ -z "$opt_model" ]; then
    external_review_cmd=1 # внешний REVIEW_CMD — осознанный оверрайд целиком
fi
# Feature-detect по литералу в доверенном local.sh — тот же паттерн, что у
# --fingerprint-only ниже: старая копия кита литерала не содержит.
kit_has_harness_layer() {
    grep -q -- '--print-review-cmd' "$1/local.sh"
}
# Выставить окружение ревьюера под конкретный кит и вычислить reviewer_label.
# Вызывается ровно один раз: зондом --print-review-cmd либо прогоном после
# резолва доверенного кита. $2 — cwd для кита (чекаут репо, как у run_kit).
configure_reviewer() {
    kit_dir="$1"
    kit_cwd="${2:-.}"
    if [ "$external_review_cmd" -eq 1 ]; then
        reviewer_label="$REVIEW_CMD"
        return 0
    fi
    # Явный флаг перекрывает и внешний REVIEW_CMD (боевое claude-ревью
    # PR #121, второй minor): `--harness codex` без модели обязан дать
    # дефолт кита, а не унаследованный из окружения claude-переходник.
    unset REVIEW_CMD || true
    if [ -n "$kit_dir" ] && kit_has_harness_layer "$kit_dir"; then
        REVIEW_HARNESS="$harness"
        export REVIEW_HARNESS
        if [ -n "$model" ]; then
            REVIEW_MODEL="$model"
            export REVIEW_MODEL
        else
            # Пустое значение для кита — отказ (D6), а не умолчание; модель
            # привязана к слою харнесса, чужую из окружения не наследуем.
            unset REVIEW_MODEL || true
        fi
        # stdout кита — строка ревьюера (в тело ревью и отпечаток), stderr —
        # оператору как есть; в reviewer_label он попасть не должен.
        kit_err=$(mktemp)
        if ! reviewer_label=$(cd "$kit_cwd" && REVIEW_KIT_DIR="$kit_dir" \
                sh "$kit_dir/local.sh" --print-review-cmd 2>"$kit_err"); then
            cat "$kit_err" >&2
            rm -f "$kit_err"
            die 2 "кит отказал в резолве ревьюера (local.sh --print-review-cmd)"
        fi
        cat "$kit_err" >&2
        rm -f "$kit_err"
        [ -n "$reviewer_label" ] \
            || die 2 "кит не назвал команду ревьюера (пустой --print-review-cmd)"
        return 0
    fi
    # Копия кита без харнесс-слоя: codex — как раньше; claude — отказ, а не
    # тихий уход на codex (переходник снят волной devtools#228).
    case "$harness" in
        claude)
            die 2 "кит ${kit_dir:-$repo} без харнесс-слоя (нет local.sh --print-review-cmd): \
claude требует кит steward >= a2d7e71 — ре-вендорьте (волна devtools#228)"
            ;;
        codex)
            # Без модели REVIEW_CMD не выставляется вовсе: дефолт кита
            # (`codex exec`) — исторический, и его строка уже лежит в
            # отпечатках опубликованных вердиктов; не инвалидируем их зря.
            if [ -n "$model" ]; then
                REVIEW_CMD="codex exec -m $model"
                export REVIEW_CMD
            fi
            ;;
    esac
    reviewer_label="${REVIEW_CMD:-codex exec}"
}
if [ "$print_review_cmd" -eq 1 ]; then
    # Отладочный зонд для тестов: показать разрешённую команду и выйти,
    # не трогая GitHub. Кит — из чекаута репо, если он есть (feature-detect
    # как в прогоне); без чекаута — ветка старого кита.
    # То же правило, что resolve_from_source в прогоне: абсолютный
    # REVIEW_KIT_DIR берётся как есть, относительный — от чекаута репо.
    case "${REVIEW_KIT_DIR:-scripts/review}" in
        /*) probe_kit="$REVIEW_KIT_DIR" ;;
        *)  probe_kit="$FLEET_ROOT/$repo/${REVIEW_KIT_DIR:-scripts/review}" ;;
    esac
    [ -f "$probe_kit/local.sh" ] || probe_kit=""
    configure_reviewer "$probe_kit" "$FLEET_ROOT/$repo"
    echo "$reviewer_label"
    exit 0
fi
case "$pr" in
    *[!0-9]*|"") die 2 "номер PR должен быть числом, получено: '$pr'" ;;
esac
[ -z "$write_verdict" ] || [ "$dry_run" -eq 1 ] \
    || die 2 "--write-verdict разрешён только вместе с --dry-run"
[ -z "$write_verdict" ] || [ -z "$use_verdict" ] \
    || die 2 "--write-verdict и --use-verdict взаимоисключающие"
[ -z "$use_verdict" ] || [ "$fresh" -eq 0 ] \
    || die 2 "--use-verdict несовместим с --fresh"

repo_dir="$FLEET_ROOT/$repo"
[ -d "$repo_dir" ] || die 2 "репо '$repo' не найдено в $FLEET_ROOT"
# Абсолютный путь нужен после перехода cwd в ephemeral head-worktree: kit
# остаётся в исходном доверенном чекауте и не должен резолвиться относительно
# нового дерева (devtools#166).
repo_dir=$(cd "$repo_dir" && pwd)
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

source_repo_dir="$repo_dir"
# До ephemeral worktree относительные override-пути разрешались от корня
# исходного чекаута. Сохраняем эту семантику явно: после `cd` они не должны
# внезапно указывать внутрь проверяемого PR-head.
resolve_from_source() {
    case "$1" in
        /*) printf '%s\n' "$1" ;;
        *) printf '%s/%s\n' "$source_repo_dir" "$1" ;;
    esac
}
trusted_kit_dir=$(resolve_from_source \
    "${REVIEW_KIT_DIR:-scripts/review}")
trusted_schema=$(resolve_from_source \
    "${REVIEW_SCHEMA:-.github/codex/review-schema.json}")
trusted_prompt=$(resolve_from_source \
    "${REVIEW_PROMPT:-.github/codex/review-prompt.md}")
# Окружение ревьюера — под доверенный кит, до отпечатка и до прогона; cwd —
# исходный чекаут, как у run_kit до материализации head-worktree.
configure_reviewer "$trusted_kit_dir" "$source_repo_dir"
work=$(mktemp -d)
review_tree="$work/review-tree"
review_tree_added=0
# shellcheck disable=SC2329  # trap EXIT invokes cleanup indirectly.
cleanup() {
    if [ "$review_tree_added" -eq 1 ]; then
        git -C "$source_repo_dir" worktree remove --force "$review_tree" \
            >/dev/null 2>&1 || true
    fi
    rm -rf "$work"
}
trap cleanup EXIT
if ! worktree_err=$(git -C "$source_repo_dir" worktree add --detach \
    "$review_tree" "$head_sha" 2>&1); then
    echo "$worktree_err" >&2
    die 2 "не удалось материализовать head $head_sha во временный worktree"
fi
review_tree_added=1
repo_dir="$review_tree"

# Кит и его доверенные инструкции берутся из исходного чекаута, а git/cwd —
# из exact-head worktree. Простое исполнение local.sh из head дало бы PR право
# переписать собственный ревьюер до вынесения вердикта. REVIEW_KIT_DIR нужен
# local.sh для его дочерних скриптов; prompt/schema тоже пинуются доверенным
# деревом, как в CI.
run_kit() {
    (
        cd "$repo_dir"
        REVIEW_KIT_DIR="$trusted_kit_dir" \
        REVIEW_SCHEMA="$trusted_schema" \
        REVIEW_PROMPT="$trusted_prompt" \
            sh "$trusted_kit_dir/local.sh" "$@"
    )
}

# Финальная сверка головы: между прогоном (или вычислением отпечатка) и
# вердиктом в PR могли запушить — тогда вердикт относился бы не к тому коду.
# Общая для публикации И для «наследовать без публикации»: exit 0 обязан
# означать «текущий head проверен», каким бы путём он ни был получен.
check_head_current() {
    if ! cur_oid=$(gh_r pr view "$pr" --repo "$slug" \
        --json headRefOid --jq .headRefOid 2>&1); then
        die 2 "не удалось перепроверить голову PR: $cur_oid"
    fi
    [ "$cur_oid" = "$head_sha" ] || die 4 "голова PR уехала: ревьюирован \
$head_sha, сейчас $cur_oid — перегоните ревью."
}

# Публикация вердикта. $1 — действие.
publish() {
    check_head_current
    if ! gh_r pr review "$pr" --repo "$slug" "--$1" \
        --body-file "$work/body.md"; then
        die 2 "прогон завершён (--$1), но публикация ревью не удалась."
    fi
    echo "опубликовано: --$1 на ${slug}#${pr} (head $head_sha) от $login"
}

# Вердикт-файл — явный одноразовый канал между dry-run и боевым прогоном,
# не кэш. Заголовок построчный и строгий, payload — точное body будущего
# review. Git object id защищает и от усечения, и от незаметной правки body;
# контекст ниже отдельно связывается с repo/pr/head/fp.
write_verdict_file() {
    [ -n "$write_verdict" ] || return 0
    verdict_dir=$(dirname "$write_verdict")
    [ -d "$verdict_dir" ] \
        || die 2 "каталог verdict-файла не существует: $verdict_dir"
    body_oid=$(git hash-object "$work/body.md") \
        || die 2 "не удалось вычислить hash тела verdict-файла"
    old_umask=$(umask)
    umask 077
    verdict_tmp=$(mktemp "$write_verdict.tmp.XXXXXX") \
        || die 2 "не удалось создать временный verdict-файл"
    umask "$old_umask"
    {
        echo "codex-terminal-review-verdict/v1"
        echo "repo=$slug"
        echo "pr=$pr"
        echo "head=$head_sha"
        echo "fp=$fp"
        echo "action=$action"
        echo "code=$kit_code"
        echo "body_oid=$body_oid"
        echo
        cat "$work/body.md"
    } > "$verdict_tmp"
    if ! mv -f "$verdict_tmp" "$write_verdict"; then
        rm -f "$verdict_tmp"
        die 2 "не удалось атомарно записать verdict-файл: $write_verdict"
    fi
    echo "verdict-файл записан: $write_verdict"
}

try_use_verdict_file() {
    [ -n "$use_verdict" ] || return 1
    if [ ! -f "$use_verdict" ]; then
        echo "ЗАМЕТКА: verdict-файл не найден — идёт полный прогон: $use_verdict" >&2
        return 1
    fi
    sed -n '10,$p' "$use_verdict" > "$work/imported-body.md"
    v_format=$(sed -n '1p' "$use_verdict")
    v_repo=$(sed -n '2s/^repo=//p' "$use_verdict")
    v_pr=$(sed -n '3s/^pr=//p' "$use_verdict")
    v_head=$(sed -n '4s/^head=//p' "$use_verdict")
    v_fp=$(sed -n '5s/^fp=//p' "$use_verdict")
    v_action=$(sed -n '6s/^action=//p' "$use_verdict")
    v_code=$(sed -n '7s/^code=//p' "$use_verdict")
    v_oid=$(sed -n '8s/^body_oid=//p' "$use_verdict")
    v_blank=$(sed -n '9p' "$use_verdict")
    imported_oid=$(git hash-object "$work/imported-body.md" 2>/dev/null || true)
    valid=1
    [ "$v_format" = "codex-terminal-review-verdict/v1" ] || valid=0
    [ "$v_repo" = "$slug" ] || valid=0
    [ "$v_pr" = "$pr" ] || valid=0
    [ "$v_head" = "$head_sha" ] || valid=0
    [ "$v_fp" = "$fp" ] || valid=0
    [ -z "$v_blank" ] || valid=0
    [ "$v_oid" = "$imported_oid" ] || valid=0
    case "$v_action:$v_code" in
        approve:0|request-changes:1) : ;;
        *) valid=0 ;;
    esac
    if [ "$valid" -ne 1 ]; then
        echo "ЗАМЕТКА: verdict-файл повреждён, несовместим или не совпал" \
            "по repo/pr/head/fp — идёт полный прогон." >&2
        return 1
    fi
    action="$v_action"
    kit_code="$v_code"
    cp "$work/imported-body.md" "$work/body.md"
    echo "вердикт принят из файла: head + fp совпали, codex не вызывался."
    if [ "$dry_run" -eq 1 ]; then
        echo "=== dry-run: действие --$action, ничего не публикуется ==="
        cat "$work/body.md"
    else
        publish "$action"
    fi
    exit "$kit_code"
}

# --- Отпечаток входа ревью (дедуп, devtools#72) ------------------------------
# Feature-detect по литералу в local.sh ЦЕЛЕВОГО репо — тот же паттерн, каким
# кит сам определяет возможности build-prompt (--generated-list). Нет флага →
# обычный полный прогон без дедупа; ре-вендор флота не пререквизит.
fp=""
fp_supported=0
if grep -q -- '--fingerprint-only' "$trusted_kit_dir/local.sh"; then
    fp_supported=1
fi
# --- Потолки дифа кита (явный оверрайд оператора) ---------------------------
# Кит отказывает на дифе больше потолка (умолчания build-prompt.sh: 400000
# байт / 30 файлов) и сам предлагает поднять его явно локальным прогоном —
# значит, поддерживаемый вызывающий обязан уметь передать потолок, иначе
# обещанный путь восстановления не существует (живой прогон spec-runner#522:
# бандл семи узлов — 447 260 байт). Флаг сильнее env; env — путь для S6
# раннера (`RealOps.review` параметров не имеет, окружение наследуется).
# Один и тот же набор уходит в ОБА вызова кита: отпечаток идёт через ту же
# сборку промпта и отказал бы на том же потолке раньше ревью. Нечисловое
# значение — отказ здесь, до единого вызова кита. Feature-detect по литералу
# в доверенном local.sh — как у --fingerprint-only: кит без потолков не
# может исполнить явный оверрайд, и это отказ с причиной, а не тихий прогон
# с умолчанием.
# Источник значения запоминается отдельно (ревью #250): S6 раннера флагов не
# передаёт, и отказ «--max-diff-bytes не целое» отправил бы оператора искать
# флаг, которого в вызове нет; шапка вердикта тоже обязана назвать слой.
max_diff_bytes="$opt_max_diff_bytes"; src_bytes="флаг"
if [ -z "$max_diff_bytes" ]; then
    max_diff_bytes="${REVIEW_MAX_DIFF_BYTES:-}"; src_bytes="env REVIEW_MAX_DIFF_BYTES"
fi
max_diff_files="$opt_max_diff_files"; src_files="флаг"
if [ -z "$max_diff_files" ]; then
    max_diff_files="${REVIEW_MAX_DIFF_FILES:-}"; src_files="env REVIEW_MAX_DIFF_FILES"
fi
check_cap_int() {
    case "$2" in
        '') ;;
        *[!0-9]*) die 2 "потолок --$1 обязан быть целым числом, получено: $2 \
(источник: $3)" ;;
    esac
}
check_cap_int max-diff-bytes "$max_diff_bytes" "$src_bytes"
check_cap_int max-diff-files "$max_diff_files" "$src_files"
cap_args=""
cap_note=""
if [ -n "$max_diff_bytes" ] || [ -n "$max_diff_files" ]; then
    grep -q -- '--max-diff-bytes' "$trusted_kit_dir/local.sh" \
        || die 2 "кит ${repo} не знает --max-diff-bytes/--max-diff-files — \
явный потолок исполнить нельзя: ре-вендорьте кит (steward) или разбейте PR."
    if [ -n "$max_diff_bytes" ]; then
        cap_args="$cap_args --max-diff-bytes $max_diff_bytes"
        cap_note="$cap_note --max-diff-bytes $max_diff_bytes ($src_bytes)"
    fi
    if [ -n "$max_diff_files" ]; then
        cap_args="$cap_args --max-diff-files $max_diff_files"
        cap_note="$cap_note --max-diff-files $max_diff_files ($src_files)"
    fi
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
    # shellcheck disable=SC2086 — cap_args проверен: только флаги и цифры.
    fp_out=$(run_kit \
        --base "origin/$base_ref" --head "$review_ref" --fingerprint-only \
        $cap_args 2> "$work/fp.err")
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

# Локальный кандидат проверяется после вычисления свежего fp и до GitHub-
# наследования. Старый кит или пустой fp не могут доказать идентичность входа:
# явный miss, затем штатный полный прогон.
if [ -n "$use_verdict" ]; then
    if [ -z "$fp" ]; then
        echo "ЗАМЕТКА: verdict-файл нельзя проверить без fp — идёт полный прогон." >&2
        fresh=1
    else
        # Явный локальный кандидат имеет fail-to-full семантику: после его
        # miss нельзя незаметно уйти в ДРУГОЙ канал наследования (GitHub),
        # иначе обещанный «полный прогон» оказался бы ложью.
        try_use_verdict_file || fresh=1
    fi
fi

# --- Поиск наследуемого вердикта ---------------------------------------------
# Кандидат — ровно НОВЕЙШЕЕ ревью доверенного автора, и оно обязано полностью
# распарситься: один маркер строгого формата, состояние однозначно переводится
# в exit code, отпечаток совпал. Любое отклонение (DISMISSED, неизвестное
# состояние, битый/дублированный маркер, другой fp) — cache miss и полный
# прогон: заглядывать в БОЛЕЕ СТАРЫЕ ревью нельзя — dismissal новейшего мог
# быть человеческим отзывом вердикта, воскрешать его из истории — не дело
# дедупа.
# Фильтр гоняется ВНЕШНИМ jq, не gh --jq: комбинацию --slurp + --jq gh
# отвергает («not supported», gh 2.83.1) — с ней кэш был мёртв на каждом
# прогоне (devtools#75, найдено первой живой проверкой steward). Страницы
# --paginate приходят потоком массивов; jq -s заворачивает их в тот же
# shape [[...],[...]], что давал --slurp. gh и jq вызываются раздельно,
# чтобы отказ каждого был виден со СВОЕЙ причиной, а не маскировался
# пайпом под «нет ревью».
inh_state=""
inh_head=""
if [ -n "$fp" ] && [ "$fresh" -eq 0 ]; then
    if ! command -v jq >/dev/null 2>&1; then
        echo "ЗАМЕТКА: jq не найден — поиск наследуемого вердикта пропущен," \
            "идёт полный прогон." >&2
    elif ! reviews_json=$(gh_r api --paginate \
        "repos/$slug/pulls/$pr/reviews" 2> "$work/reviews.err"); then
        cat "$work/reviews.err" >&2
        echo "ЗАМЕТКА: прошлые ревью не прочитались (gh) — дедуп пропущен," \
            "идёт полный прогон." >&2
    elif ! candidate=$(printf '%s' "$reviews_json" | jq -rs \
        '([ .[][] | select(.user.login == "'"$REVIEW_LOGIN"'") ] | last) as $r
            | if $r == null then "none none none"
              else
                (($r.body // "") | [scan("<!-- codex-terminal-review ")] | length) as $n
                | (($r.body // "") | [match("<!-- codex-terminal-review head=([0-9a-f]{40}) fp=([0-9a-f]{64}) -->")]) as $ms
                | if $n == 1 and ($ms | length) == 1
                     and ($r.state == "APPROVED" or $r.state == "CHANGES_REQUESTED")
                  then $r.state + " " + $ms[0].captures[0].string + " " + $ms[0].captures[1].string
                  else "miss miss miss" end
              end' 2> "$work/reviews.err"); then
        cat "$work/reviews.err" >&2
        echo "ЗАМЕТКА: прошлые ревью не распарсились (jq) — дедуп пропущен," \
            "идёт полный прогон." >&2
    else
        read -r c_state c_head c_fp <<EOF
$candidate
EOF
        if [ "$c_fp" = "$fp" ]; then
            inh_state="$c_state"
            inh_head="$c_head"
        fi
    fi
fi

if [ -n "$inh_state" ]; then
    case "$inh_state" in
        APPROVED)          action="approve";         kit_code=0 ;;
        CHANGES_REQUESTED) action="request-changes"; kit_code=1 ;;
    esac
    if [ "$inh_head" = "$head_sha" ]; then
        # Та же сверка, что перед публикацией: exit 0 без неё объявил бы
        # зелёным head, запушенный ПОСЛЕ вычисления отпечатка (боевая
        # находка codex-ревью №2 этого же PR). dry-run симметричен
        # полному прогону — живой сверки не делает.
        [ "$dry_run" -eq 1 ] || check_head_current
        if [ -n "$write_verdict" ]; then
            {
                echo "## Codex CLI review — терминальный прогон"
                echo
                echo "- PR: ${slug}#${pr}, head \`$head_sha\`"
                echo "- вердикт унаследован от опубликованного review по тому же head," \
                    "отпечаток входа совпал — codex не вызывался"
                echo "- публикация: $REVIEW_LOGIN"
                echo
                echo "<!-- codex-terminal-review head=$head_sha fp=$fp -->"
            } > "$work/body.md"
            write_verdict_file
            echo "=== dry-run: действие --$action (унаследовано)," \
                "ничего не публикуется ==="
            cat "$work/body.md"
            exit "$kit_code"
        fi
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
        write_verdict_file
        echo "=== dry-run: действие --$action (унаследовано)," \
            "ничего не публикуется ==="
        cat "$work/body.md"
        exit "$kit_code"
    fi
    publish "$action"
    exit "$kit_code"
fi

# --- Бюджет платных прогонов (решение владельца 2026-09-18) ---------------
# Всё, что выше, до модели не доходит: отпечаток (`--fingerprint-only`),
# наследование по fp и `--use-verdict` выходят раньше. Поэтому счётчик стоит
# ровно здесь — один раз на один фактический вызов ревьюера.
#
# Зачем барьер, а не правило в CLAUDE.md: формальный порог (blocker/major +
# high confidence) сам по себе перерасход не останавливает — поверх него
# возникает неформальный гейт «чинить любую находку и повторять до чистого
# вердикта». На spec-runner#526 это дало девять платных кругов при том, что
# КАЖДЫЙ новый круг открывала настоящая блокирующая находка, то есть порог не
# нарушался ни разу. Прозаическое правило при этом обходится рационализацией:
# в той же сессии агент дважды нашёл довод превысить порог, и оба раза довод
# звучал разумно. Отказ инструмента таких доводов не слушает.
#
# Это правило и defense-in-depth, а не security boundary: вызвать кит напрямую
# обвязка не мешает. Она делает перерасход осознанным и видимым.
budget_max=2  # 1 полный review + 1 адресный recheck
budget_dir="${REVIEW_BUDGET_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/ai-prosto/review-budget}"
budget_ledger="$budget_dir/$(printf '%s' "$slug" | tr '/' '_')-${pr}.log"
budget_used=0
[ ! -f "$budget_ledger" ] \
    || budget_used=$(wc -l < "$budget_ledger" | tr -d ' ')
budget_exceeded=0
if [ "$budget_used" -ge "$budget_max" ]; then
    if [ -z "$budget_override" ]; then
        die 2 "бюджет платных прогонов исчерпан:" \
            "${slug}#${pr} — $budget_used из $budget_max" \
            "(1 полный review + 1 адресный recheck)." \
            "Не-блокирующие находки нового круга не открывают: чините без" \
            "повторного ревью либо записывайте в debt." \
            "Нужен ещё круг — это решение владельца:" \
            "--budget-override '<причина>'." \
            "Журнал: $budget_ledger"
    fi
    budget_exceeded=1
fi
mkdir -p "$budget_dir" || die 2 "не удалось создать каталог бюджета: $budget_dir"
budget_round=$((budget_used + 1))
{
    printf 'round=%s at=%s head=%s' \
        "$budget_round" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$head_sha"
    [ "$budget_exceeded" -eq 0 ] || printf ' override=%s' "$budget_override"
    printf '\n'
} >> "$budget_ledger" || die 2 "не удалось записать журнал бюджета: $budget_ledger"

# --- Полный прогон -------------------------------------------------------
# Доверенный кит запускается с cwd в exact-head worktree: код, промпт и схема
# приходят из исходного чекаута, а контекст файлов — из проверяемого дерева.
# Свежесть базы: при fp-ките база уже освежена явным fetch выше (и отпечаток
# обязан видеть то же состояние), у старого кита --fetch остаётся его
# собственной заботой.
set -- --base "origin/$base_ref" --head "$review_ref" --format markdown
[ "$fp_supported" -eq 1 ] || set -- "$@" --fetch
# shellcheck disable=SC2086 — cap_args проверен: только флаги и цифры.
[ -z "$cap_args" ] || set -- "$@" $cap_args
set +e
run_kit "$@" \
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
    echo "- ревьюер: \`$reviewer_label\` через review-kit репо;" \
        "публикация: $REVIEW_LOGIN"
    if [ "$budget_exceeded" -eq 1 ]; then
        # Перерасход — факт о прогоне, который читатель вердикта обязан
        # видеть: иначе он остаётся только в прозе агента и в локальном
        # журнале, то есть невидим тому, кто принимает остаточный риск.
        echo "- бюджет платных прогонов превышен: круг $budget_round при" \
            "лимите $budget_max, по решению владельца — $budget_override"
    fi
    if [ -n "$cap_args" ]; then
        # Поднятый потолок — факт о прогоне, который читатель вердикта обязан
        # видеть: умолчания кита на этот диф не действовали.
        echo "- потолки дифа подняты явно:$cap_note" \
            "— умолчания кита не действовали"
    fi
    echo
    cat "$work/verdict.md"
    echo
    echo "$marker"
} > "$work/body.md"

if [ "$dry_run" -eq 1 ]; then
    write_verdict_file
    echo "=== dry-run: действие --$action, ничего не публикуется ==="
    cat "$work/body.md"
    exit "$kit_code"
fi

publish "$action"
exit "$kit_code"
