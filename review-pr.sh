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
#   4 — голова PR уехала между прогоном и публикацией — перегнать;
#   6 — БАРЬЕР отказал: бюджет платных прогонов исчерпан либо stop rule
#       (последнее ревью — approve). Это не сбой прибора и не ошибка
#       конфигурации: прогон возможен, но требует решения владельца
#       (`--budget-override '<причина>'`). Отдельный код — devtools#258:
#       под кодом 2 автоматический контур постил в PR «прибор не
#       отработал», то есть ложную причину остановки. Пятёрка намеренно
#       пропущена: это код КИТА «всё отфильтровано как проза» (срез B),
#       и второй смысл у той же цифры на смежном слое воспроизвёл бы
#       ровно ту путаницу, ради которой код и разводится.
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
        "[--write-verdict <file> | --use-verdict <file>] [--print-scope]" \
        "[--include-prose] [--targeted] [--harness claude|codex]" \
        "[--model <M>]" \
        "[--max-diff-bytes N] [--max-diff-files N]" >&2
    echo "  <repo> — имя каталога репо во флоте (например dispatcher)" >&2
    echo "  --fresh — не наследовать вердикт даже при совпавшем отпечатке" >&2
    echo "  --write-verdict — атомарно сохранить результат dry-run для боевого прогона" >&2
    echo "  --use-verdict — использовать сохранённый результат при точных head + fp" >&2
    echo "  --print-scope — напечатать область ревью (prose|code) и выйти;" >&2
    echo "    операторский флаг, не только тестовый зонд" >&2
    echo "  --include-prose — обойти и раннюю классификацию (scope=prose)," >&2
    echo "    и фильтр области у кита (срез B, код 5): диф идёт модели целиком" >&2
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
print_scope=0
include_prose=0
targeted=0
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) dry_run=1; shift ;;
        --fresh) fresh=1; shift ;;
        --include-prose) include_prose=1; shift ;;
        --targeted) targeted=1; shift ;;
        --budget-override)
            # Причина обязательна и не может быть пробелами: перерасход
            # бюджета — решение владельца, и оно обязано быть названным.
            [ $# -ge 2 ] \
                || die 2 "--budget-override требует причину (решение владельца)"
            budget_override=$(printf '%s' "$2" | tr -d '[:space:]')
            [ -n "$budget_override" ] \
                || die 2 "--budget-override требует непустую причину"
            # Забытая причина съела бы следующий флаг: `--budget-override
            # --dry-run` дало бы боевую публикацию вместо dry-run, то есть
            # необратимый внешний эффект от опечатки. Закрытая форма, как у
            # allowlist стратегий в merge-pr.sh.
            case "$2" in
                -*) die 2 "--budget-override: причина не может начинаться" \
                    "с '-' (похоже на флаг: '$2')" ;;
            esac
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
        --print-scope) print_scope=1; shift ;;
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
#
# REVIEW_SCOPE_RULES — то же правило области, которым классифицировала
# `classify_scope` выше. Без него кит берёт `$kit_dir/prose-paths.env`
# (local.sh:227), то есть вендор-копию ЦЕЛЕВОГО репо, и два решения об одном
# дифе принимаются по разным файлам. Расхождение даёт исход хуже непокрытия:
# кит отвечает `exit 5` («всё отфильтровано»), а он не разобран в fp-ветке
# (`case "$fp_code"`) и вырождается в `die 3` — прогон падает, `accept_pr`
# останавливает приёмку; в полном прогоне тот же 5 уходит в kit-filtered
# аттестацию, то есть approve без единого взгляда модели. Найдено ревью на
# devtools#270; SSOT обязан быть один и для ранней классификации, и для кита.
# Кит без поддержки переменной её игнорирует — но такой кит и фильтра области
# не содержит, значит кода 5 не вернёт и расходиться ему не с чем.
run_kit() {
    (
        cd "$repo_dir"
        REVIEW_KIT_DIR="$trusted_kit_dir" \
        REVIEW_SCHEMA="$trusted_schema" \
        REVIEW_PROMPT="$trusted_prompt" \
        REVIEW_SCOPE_RULES="$prose_paths_file" \
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

# Согласованность диапазона: классификация области, отпечаток и фактическое
# ревью обязаны видеть ОДНО состояние базы — освежаем её явным fetch здесь,
# дальше все вызовы кита идут без --fetch. Destination в refspec делает
# освежение безусловным даже на single-branch клоне (devtools#73).
if ! fetch_err=$(git -C "$repo_dir" fetch -q origin \
    "+refs/heads/$base_ref:refs/remotes/origin/$base_ref" 2>&1); then
    echo "$fetch_err" >&2
    die 2 "не удалось освежить базу origin/$base_ref перед ревью"
fi

# --- Область ревью (contracts/review-scope/v1) -----------------------------
# Правило живёт файлом-контрактом, а не литералом: срез B вендорит ТОТ ЖЕ
# файл в кит, и второго написания правила не возникает. Литерал списка путей
# в этом репо уже однажды разъехался молча — см. governance/runner.py.
prose_paths_file="${REVIEW_SCOPE_CONTRACT:-$script_dir/contracts/review-scope/v1/prose-paths.env}"
# Абсолютизируем СРАЗУ: этот путь уезжает киту через REVIEW_SCOPE_RULES, а кит
# работает из head-worktree. Относительный операторский override после `cd`
# указывал бы внутрь проверяемого PR-head — тот самый режим отказа, от
# которого `resolve_from_source` страхует kit/schema/prompt. База — cwd
# обвязки, а не `$source_repo_dir`: так сохраняется семантика собственного
# чтения контракта, и оба читателя гарантированно берут ОДИН файл.
case "$prose_paths_file" in
    /*) ;;
    *) prose_paths_file="$(pwd)/$prose_paths_file" ;;
esac
prose_globs=""
code_globs=""
[ -f "$prose_paths_file" ] \
    || die 2 "нет контракта области ревью: $prose_paths_file"
# Формат — канон governance/ssot_env.py (эта половина — shell, python читает
# тот же формат для других контрактов): один ключ — одна строка, дубль —
# отказ. Выбирать за человека, какое из двух значений настоящее, нельзя —
# именно такой молчаливый выбор уже однажды развёл литерал и контракт
# (governance/runner.py). Ведущие/хвостовые пробелы обрезаются; пустая
# строка и `#`-комментарий пропускаются.
scope_contract_lines=$(sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
    "$prose_paths_file" | grep -v '^#' | grep -v '^$') || true
# Читает РОВНО ОДНО определение $1 из $scope_contract_lines в scope_key_value.
# Вызывается напрямую (не через `$(...)`), чтобы `die` внутри действительно
# завершал скрипт, а не только подоболочку командной подстановки.
read_scope_key() {
    _rsk_key="$1"
    _rsk_matches=$(printf '%s\n' "$scope_contract_lines" \
        | grep "^${_rsk_key}=") || _rsk_matches=""
    _rsk_count=0
    [ -z "$_rsk_matches" ] \
        || _rsk_count=$(printf '%s\n' "$_rsk_matches" | wc -l | tr -d ' ')
    case "$_rsk_count" in
        0) die 2 "контракт области ревью не называет $_rsk_key: \
$prose_paths_file" ;;
        1) : ;;
        *) die 2 "в $prose_paths_file ключ $_rsk_key определён \
$_rsk_count раз — файл битый; какое значение настоящее, решает человек, \
не разбор" ;;
    esac
    scope_key_value="${_rsk_matches#"${_rsk_key}="}"
    [ -n "$scope_key_value" ] || die 2 "в $prose_paths_file нет непустого \
$_rsk_key"
}
read_scope_key PROSE
prose_globs="$scope_key_value"
# CODE_OVERRIDE обязателен, а не опционален: усечённая вендор-копия без этого
# ключа молча расширила бы прозу ровно на класс путей, который он защищает
# (.github/, contracts/, eval/, fixtures/, schemas/) — read_scope_key
# откажет на нём тем же путём "не называет KEY", что и на пропущенном PROSE.
read_scope_key CODE_OVERRIDE
code_globs="$scope_key_value"

# 0 — путь проза, 1 — код. CODE_OVERRIDE сильнее PROSE: Markdown внутри
# .github/, contracts/, eval/, fixtures/, schemas/ — данные, не проза.
# `set -f` вокруг обоих циклов: `for _g in $code_globs` — обычное unquoted
# расширение параметра, и без noglob шелл подверг бы каждый расщеплённый
# токен (contracts/*, docs/* и т.п.) pathname-expansion от ТЕКУЩЕГО cwd —
# в этом самом воркдереве docs/ и contracts/ реальны, и глоб молча
# подменился бы списком настоящих файлов вместо литерального паттерна для
# `case`. `set +f` перед каждым return восстанавливает поведение до выхода
# из функции — снаружи globbing не тронут.
path_is_prose() {
    set -f
    for _g in $code_globs; do
        # глоб намеренно не в кавычках
        # shellcheck disable=SC2254
        case "$1" in $_g) set +f; return 1 ;; esac
    done
    for _g in $prose_globs; do
        # глоб намеренно не в кавычках
        # shellcheck disable=SC2254
        case "$1" in $_g) set +f; return 0 ;; esac
    done
    set +f
    return 1
}

# Печатает prose либо code. Fail-closed: список файлов не получен, не
# разобран или пуст — PR считается КОДОВЫМ и ревьюится как прежде.
# --no-renames намеренно: переименование приходит парой удаление+добавление,
# и оба пути проходят классификацию; иначе путь-источник остался бы невиден.
classify_scope() {
    if ! _mb=$(git -C "$repo_dir" merge-base "$review_base" "$review_ref" \
        2> "$work/scope.err"); then
        cat "$work/scope.err" >&2
        echo "ЗАМЕТКА: область ревью не определена (merge-base) —" \
            "PR ревьюится как кодовый." >&2
        echo code
        return 0
    fi
    if ! _files=$(git -C "$repo_dir" diff --no-renames --name-only \
        "$_mb..$head_sha" 2> "$work/scope.err"); then
        cat "$work/scope.err" >&2
        echo "ЗАМЕТКА: область ревью не определена (diff) —" \
            "PR ревьюится как кодовый." >&2
        echo code
        return 0
    fi
    _any=0
    _verdict=prose
    # `for _f in $_files` — unquoted расширение параметра: без `set -f` и без
    # IFS=newline классификация пути с глоб-метасимволом или пробелом в имени
    # зависела бы от pathname expansion (от cwd) и от разбиения по пробелу, а
    # не только от переводов строк, которыми `git diff --name-only` разделяет
    # пути. Список слов цикла `for` вычисляется РОВНО ОДИН РАЗ, в момент входа
    # в цикл — правки IFS/`set -f` внутри тела на уже вычисленный список не
    # влияют, поэтому оба восстанавливаются сразу на первой строке тела: сам
    # `path_is_prose` ниже расщепляет `$code_globs`/`$prose_globs` по
    # ПРОБЕЛУ и ему нужны штатные IFS и globbing.
    _old_ifs=$IFS
    IFS="
"
    set -f
    for _f in $_files; do
        set +f
        IFS=$_old_ifs
        _any=1
        path_is_prose "$_f" || { _verdict=code; break; }
    done
    set +f
    IFS=$_old_ifs
    [ "$_any" -eq 1 ] || {
        echo "ЗАМЕТКА: область ревью не определена (пустой диапазон) —" \
            "PR ревьюится как кодовый." >&2
        _verdict=code
    }
    echo "$_verdict"
}

# Вызов классификации — НИЖЕ, после вычисления `review_base`: область
# обязана считаться по тому же диапазону, который уходит киту (находка
# ревью devtools#281, blocker). Пока вызов стоял здесь, `--targeted`
# классифицировал полный диапазон, а кит фильтровал узкий: фикс, тронувший
# только прозу, давал у обвязки scope=code, у кита — «всё отфильтровано»
# (код 5), а тот в fp-ветке не разобран и вырождается в `die 3` «ревьюер не
# отработал», то есть в ложную причину.

# --- Последнее доставленное ревью (источник истины для stop rule) ---------
# Источник истины про «был ли уже вердикт» — опубликованные ревью самого PR, а
# не локальный журнал бюджета: журнал списывает круг сразу по коду кита, то
# есть и там, где вердикт до PR не дошёл (dry-run без публикации, «голова
# уехала», провал публикации), и он локален — на другой машине пуст, а ревью
# лежит в PR, где его видит человек.
#
# Строгость разбора — та же, что у дедупа ниже: ровно ОДИН маркер полного
# формата. Тело ревью содержит вывод порога, то есть текст модели, пришедший
# из недоверенного дифа; маркер оттуда, стоящий раньше настоящего, иначе
# выбирал бы базу адресного ревью — вплоть до пустого диапазона, который кит
# закрывает нулём, а обвязка публикует как approve.
lr_state=""
# lr_head — база адресного recheck (`--targeted`, блок «База ревью» ниже).
# Разбор общий и строгий именно поэтому: тело ревью несёт текст модели из
# недоверенного дифа, и второй, более слабый разбор дал бы подставленному
# маркеру выбирать базу. lr_fp потребителя пока не имеет.
lr_head=""
lr_fp=""
# lr_delivered_state — состояние последнего ДОСТАВЛЕННОГО ревью $REVIEW_LOGIN,
# без требований к маркеру (в отличие от lr_state — марк output stop rule,
# который признаёт только строгий формат `head=…fp=…`). Нужен отдельно: PR,
# на котором лежит красный вердикт от кита без --fingerprint-only (маркер без
# `fp`), для stop rule — miss, но для guard'а ветки прозы (находка 1) это
# ДОСТАВЛЕННЫЙ CHANGES_REQUESTED, и его нельзя молча погасить аттестацией.
lr_delivered_state=""
lr_known=0
# lr_json/lr_fetch_ok кэшируют СЫРОЙ ответ /reviews для повторного
# использования ниже, в блоке поиска наследуемого вердикта по отпечатку
# (находка 12): без кэша тот блок звал бы `gh_r api --paginate` ВТОРОЙ раз на
# каждом прогоне — лишний round-trip и расход rate limit. lr_fetch_ok=1
# означает «запрос состоялся», а не «разбор успешен» — второй потребитель
# сам решает, что делать с сырым JSON.
lr_json=""
lr_fetch_ok=0
# Запрос идёт на КАЖДОМ прогоне, а не только при дедупе: stop rule обязан знать
# про доставленный вердикт независимо от отпечатка и от --fresh. Недоступность
# ответа (нет jq, отказ API) stop rule не отключает молча — она об этом
# ГРОМКО говорит: жёсткий лимит бюджета при этом продолжает работать, а
# уточнение «после approve круга нет» деградирует с предупреждением, а не
# тихо.
#
# lr_known выставляется в 1, только если оба запроса к ОДНОМУ и тому же
# lr_json разобрались: маркерный (lr_state, строгость stop rule не трогаем ни
# на символ) и безмаркерный (lr_delivered_state, находка 1). Это два разных
# вопроса к одному списку ревью, не замена одного другим.
if command -v jq >/dev/null 2>&1; then
    if lr_json=$(gh_r api --paginate "repos/$slug/pulls/$pr/reviews" \
        2> "$work/lastreview.err"); then
        lr_fetch_ok=1
        if lr_line=$(printf '%s' "$lr_json" | jq -rs \
            '([ .[][]
               | select(.user.login == "'"$REVIEW_LOGIN"'")
               | select(((.body // "") | index("<!-- codex-terminal-review ")) != null)
             ] | last) as $r
                | if $r == null then "none none none"
                  else
                    (($r.body // "") | [scan("<!-- codex-terminal-review ")] | length) as $n
                    | (($r.body // "") | [match("<!-- codex-terminal-review head=([0-9a-f]{40}) fp=([0-9a-f]{64}) -->")]) as $ms
                    | if $n == 1 and ($ms | length) == 1
                         and ($r.state == "APPROVED" or $r.state == "CHANGES_REQUESTED")
                      then $r.state + " " + $ms[0].captures[0].string + " " + $ms[0].captures[1].string
                      else "miss miss miss" end
                  end' 2> "$work/lastreview.err") \
            && lr_delivered_state=$(printf '%s' "$lr_json" | jq -rs \
            '([ .[][]
               | select(.user.login == "'"$REVIEW_LOGIN"'")
               | select(.state == "APPROVED" or .state == "CHANGES_REQUESTED")
             ] | last) as $r
            | if $r == null then "none" else $r.state end' \
            2> "$work/lastreview.err"); then
            # shellcheck disable=SC2034 — читается только lr_fp: его
            # потребителя пока нет. lr_head читает блок «База ревью»
            # ниже (адресный recheck, devtools#260).
            read -r lr_state lr_head lr_fp <<EOF
$lr_line
EOF
            lr_known=1
            [ "$lr_state" != "none" ] && [ "$lr_state" != "miss" ] || lr_head=""
            [ "$lr_state" != "miss" ] || lr_state=""
            [ "$lr_state" != "none" ] || lr_state=""
            [ "$lr_delivered_state" != "none" ] || lr_delivered_state=""
        else
            cat "$work/lastreview.err" >&2
        fi
    else
        cat "$work/lastreview.err" >&2
    fi
fi
[ "$lr_known" -eq 1 ] || echo "ВНИМАНИЕ: последнее ревью $REVIEW_LOGIN не" \
    "прочитано (нет jq или отказ API) — stop rule «после approve круга нет»" \
    "на этом прогоне не проверен; лимит бюджета продолжает действовать." >&2

# --- База ревью: полная или адресная (--targeted, devtools#260) ------------
# ОДНА переменная на обе точки вызова кита — отпечаток и полный прогон. Пока
# `--base origin/<base>` стоял литералом дважды, эти два места обязаны были
# совпадать «по договорённости»; вырезанная реализация #259 разошлась ровно
# здесь и дала no-op: fp считался по полному диапазону при вердикте по
# узкому, то есть будущий прогон унаследовал бы адресное ревью как полное, а
# потолок дифа мерился бы не по тому, что ревьюируется.
review_base="origin/$base_ref"
if [ "$targeted" -eq 1 ]; then
    # Факты ревью не получены — неустановленный факт против действия:
    # «маркера нет» неотличимо от «не спросили».
    [ "$lr_known" -eq 1 ] \
        || die 2 "--targeted: последнее ревью не прочитано (нет jq или отказ" \
            "API) — базу адресного прогона установить нечем."
    # Базу даёт ТОЛЬКО строгий разбор выше: ровно один маркер полного
    # формата от $REVIEW_LOGIN. Второй, более слабый разбор здесь означал бы,
    # что подставленный в тело маркер (текст модели приходит из
    # недоверенного дифа) выбирает базу адресного ревью.
    [ -n "$lr_head" ] \
        || die 2 "--targeted: на ${slug}#${pr} нет прошлого вердикта с" \
            "маркером ($REVIEW_LOGIN, ровно один маркер полного формата) —" \
            "сужать не от чего. Прогоните полное ревью."
    # Пустой диапазон: фикс ещё не в PR. Кит на пустом дифе штатно выходит
    # нулём, обвязка маппит 0 в approve и публикует — то есть отменила бы
    # доставленный request-changes, не показав модели ни строки. Отказ
    # обязан быть ДО вызова кита.
    [ "$lr_head" != "$head_sha" ] \
        || die 2 "--targeted: прошлая отревьюированная голова совпадает с" \
            "текущей ($head_sha) — диапазон пуст, ревьюировать нечего." \
            "Запушьте фикс-коммиты и повторите."
    # Предок: после force-push/rebase узкий диф перестаёт быть «что
    # изменилось после ревью» и становится произвольным сравнением веток.
    # Объект обязан быть и локально: голова PR зафетчена выше, и при
    # непрерывной истории прошлая голова приезжает вместе с ней.
    git -C "$repo_dir" cat-file -e "$lr_head^{commit}" 2>/dev/null \
        || die 2 "--targeted: прошлая отревьюированная голова $lr_head" \
            "недоступна локально (перезаписанная история?) — сузить нельзя."
    git -C "$repo_dir" merge-base --is-ancestor "$lr_head" "$head_sha" \
        || die 2 "--targeted: $lr_head не предок текущей головы $head_sha" \
            "(force-push или rebase) — узкий диапазон не означал бы" \
            "«что изменилось после ревью». Прогоните полное ревью."
    # Пустота — свойство СОДЕРЖИМОГО, а не равенства sha (находка ревью
    # devtools#281, blocker). `git commit --allow-empty` — ходовой приём
    # перезапуска чеков — и пара «коммит + revert» дают разные головы при
    # тождественном дереве. Кит на пустом дифе штатно выходит нулём,
    # обвязка маппит 0 в approve и публикует его поверх ДОСТАВЛЕННОГО
    # request-changes. Проверка стоит после гварда предка: объект к этому
    # моменту уже гарантированно есть.
    git -C "$repo_dir" diff --quiet "$lr_head" "$head_sha" 2>/dev/null \
        && die 2 "--targeted: после прошлого ревью ($lr_head) дерево не" \
            "изменилось — диапазон пуст, ревьюировать нечего." \
            "Пустой коммит или коммит с его же revert? Прогоните полное" \
            "ревью или запушьте настоящий фикс."
    review_base="$lr_head"
fi

scope=$(classify_scope)
if [ "$print_scope" -eq 1 ]; then
    echo "$scope"
    exit 0
fi

# --- Прозаическая область ревью (scope=prose, решение владельца 2026-09-18) -
# Прозаический PR модельному ревьюеру не отдаётся: платный ревьюер — только
# код. Публикуется scope-аттестация — отдельная governance-сущность, а НЕ
# вердикт: у неё собственный маркер, которого не знает ни один потребитель
# протокола codex-terminal-review, поэтому появление кода на этом же PR не
# потребует --budget-override.
#
# Красный вердикт аттестацией не гасится. Опубликовать approve поверх
# доставленного CHANGES_REQUESTED значило бы снять его синтетическим
# зелёным; вызвать ревьюера — нарушить правило «модель не видит прозу».
# Поэтому здесь отказ: PR остаётся человеку.
#
# Guard решает по lr_delivered_state (без требований к маркеру), а не по
# lr_state (маркерному): красный вердикт от старого кита без --fingerprint-
# only публикует маркер без `fp`, и для строгого stop-rule разбора это miss —
# но он всё равно ДОСТАВЛЕН и обязан быть виден здесь. Пустой lr_state (и
# пустой lr_delivered_state) означает либо «красного нет», либо «факт не
# получен» — эти два состояния различает именно lr_known, и без него решать
# нечем: fail-closed, а не тихий approve поверх непрочитанного вердикта.
# $1 — kind, ровно два honest-варианта происхождения аттестации:
#   prose-only   — ранняя классификация обвязки (scope=prose): все пути
#                  диффа сама обвязка сочла прозой, кит вообще не вызывался;
#   kit-filtered — код 5 кита (срез B): обвязка сама сочла диф кодовым, но
#                  кит (правило + repo-конфиг целевого репо, его слово
#                  последнее) после СВОЕГО фильтра не нашёл, что ревьюировать
#                  — кит при этом вызывался, вердикта не дал.
# Текст обязан называть настоящее происхождение: PR на kit-filtered читал бы
# «все пути — проза», написанное обвязкой, которая как раз решила иначе.
publish_scope_attestation() {
    _psa_kind="$1"
    case "$_psa_kind" in
        prose-only)
            _psa_no_kit_note="prose-only PR (кит не вызывается)"
            _psa_diff_desc="prose-only диф"
            _psa_no_verdict_reason="ревьюер не вызывается (проза платному\
 ревью не подлежит)"
            _psa_resolution_hint="Появится код в дифе — прогон пойдёт\
 обычным путём."
            ;;
        kit-filtered)
            _psa_no_kit_note="PR с кодом 5 кита (область ревью у кита пуста\
 после её собственного фильтра)"
            _psa_diff_desc="диф с кодом 5 кита (область ревью у кита пуста)"
            _psa_no_verdict_reason="кит вернул код 5 — содержательного\
 вердикта нет"
            _psa_resolution_hint="Изменится правило кита или repo-конфиг\
 области ревью — прогон пойдёт обычным путём."
            ;;
        *) die 2 "publish_scope_attestation: неизвестный kind '$_psa_kind'" ;;
    esac
    # Находка 8: ветка прозы выходит раньше, чем --write-verdict/--use-
    # verdict успевают что-то значить (оба относятся к содержательному
    # вердикту, которого здесь нет) — как и прочие ветки «делаю не то, что
    # просили», печатаем ЗАМЕТКУ, а не молчим.
    [ -z "$write_verdict" ] || echo "ЗАМЕТКА: --write-verdict не применяется" \
        "на $_psa_no_kit_note — verdict-файл не записан." >&2
    [ -z "$use_verdict" ] || echo "ЗАМЕТКА: --use-verdict не применяется на" \
        "$_psa_no_kit_note — verdict-файл не читался." >&2
    [ "$lr_known" -eq 1 ] || die 2 "$_psa_diff_desc, но список ревью" \
        "${slug}#${pr} не прочитан (нет jq или отказ API): решить, нет ли" \
        "доставленного request-changes, не на чем. Аттестация не публикуется —" \
        "PR остаётся человеку."
    if [ "$lr_delivered_state" = "CHANGES_REQUESTED" ]; then
        die 2 "$_psa_diff_desc при доставленном request-changes от" \
            "$REVIEW_LOGIN на ${slug}#${pr}: аттестация не публикуется" \
            "(она погасила бы красный вердикт), $_psa_no_verdict_reason —" \
            "PR остаётся человеку. $_psa_resolution_hint"
    fi
    # Дедуп (находка 4): повторный/resume-прогон на ТОМ ЖЕ head не кладёт в
    # PR ещё одну аттестацию — иначе каждый повтор добавлял бы новый APPROVE.
    # У кодового пути дедуп есть (по отпечатку входа), у прозы его не было.
    # Ищем в уже прочитанном lr_json (второй запрос к API не нужен) маркер
    # `ai-prosto-scope-review` от $REVIEW_LOGIN с ТЕКУЩИМ head_sha — ЛЮБОГО
    # kind: прошлый прогон мог опубликовать другой вид аттестации на этом же
    # head (классификация обвязки и решение кита могут разойтись между
    # прогонами), и это всё равно не повод класть вторую. Guard выше
    # гарантирует lr_known=1, то есть jq доступен и lr_json прочитан.
    if _att_seen=$(printf '%s' "$lr_json" | jq -rs --arg h "$head_sha" \
        '([ .[][]
           | select(.user.login == "'"$REVIEW_LOGIN"'")
           | select((.body // "") | test(
               "<!-- ai-prosto-scope-review version=1 kind=(prose-only|kit-filtered) head="
               + $h + " -->"
             ))
         ] | length) > 0' 2> "$work/att.err") && [ "$_att_seen" = "true" ]; then
        echo "ЗАМЕТКА: аттестация на этой голове уже опубликована — ничего" \
            "не публикуется."
        exit 0
    fi
    # Отказ самого дедупа (не «не нашли», а «проверить не удалось») обязан быть
    # слышен: молчаливый путь здесь стоил бы второго APPROVE в PR. Публикацию он
    # не отменяет — иначе сбой проверки лишал бы PR аттестации вовсе.
    [ ! -s "$work/att.err" ] || {
        cat "$work/att.err" >&2
        echo "ВНИМАНИЕ: проверка «аттестация уже опубликована» не отработала —" \
            "возможен повторный APPROVE на ${slug}#${pr}." >&2
    }
    {
        echo "## Automated scope attestation — $_psa_kind"
        echo
        echo "- PR: ${slug}#${pr}, head \`$head_sha\`"
        case "$_psa_kind" in
            prose-only)
                echo "- classifier: \`review-scope/v1\`"
                echo "- все изменённые пути классифицированы как prose-only"
                ;;
            kit-filtered)
                echo "- classifier: review-kit репо \`${repo}\` (правило" \
                    "кита + repo-конфиг области ревью)"
                echo "- обвязка сама сочла диф кодовым; после фильтра" \
                    "области у кита ревьюировать нечего — решение вынес" \
                    "кит, его слово последнее"
                ;;
        esac
        echo "- модельный ревьюер не вызывался; содержательная корректность" \
            "прозы не проверялась"
        echo "- required CI checks остаются обязательным независимым" \
            "условием мержа"
        echo
        echo "<!-- ai-prosto-scope-review version=1 kind=$_psa_kind" \
            "head=$head_sha -->"
    } > "$work/body.md"
    if [ "$dry_run" -eq 1 ]; then
        echo "=== dry-run: scope-аттестация, ничего не публикуется ==="
        cat "$work/body.md"
        exit 0
    fi
    publish approve
    exit 0
}

# --include-prose снимает и раннюю классификацию (эту ветку), и фильтр
# кита (проброс флага в его вызов ниже) — оператору нужен один флаг,
# чтобы прогнать модельное ревью на диффе, который иначе кит бы отфильтровал.
if [ "$scope" = "prose" ] && [ "$include_prose" -eq 0 ]; then
    publish_scope_attestation prose-only
fi

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

# --include-prose уходит в ОБА вызова кита (отпечаток и полный прогон) — тем
# же путём, что и cap_args: кит фильтрует прозу сам (срез B), и снять фильтр
# для отпечатка, но не для полного прогона (или наоборот), означало бы
# наследовать вердикт с одной областью на PR, ревьюированный с другой.
include_prose_arg=""
[ "$include_prose" -eq 0 ] || include_prose_arg="--include-prose"

if [ "$fp_supported" -eq 1 ]; then
    set +e
    # shellcheck disable=SC2086 — cap_args/include_prose_arg проверены:
    # только флаги и цифры.
    fp_out=$(run_kit \
        --base "$review_base" --head "$review_ref" --fingerprint-only \
        $cap_args $include_prose_arg 2> "$work/fp.err")
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
#
# reviews_json — тот же СЫРОЙ ответ /reviews, что уже прочитан выше в
# lr_json (находка 12): второй запрос к API не идёт, кэш переиспользуется
# lr_fetch_ok различает «запроса не было/отказал» и «пришёл пустой список» —
# поведение при отказе gh не меняется, деградация с предупреждением, не молча.
inh_state=""
inh_head=""
if [ -n "$fp" ] && [ "$fresh" -eq 0 ]; then
    if ! command -v jq >/dev/null 2>&1; then
        echo "ЗАМЕТКА: jq не найден — поиск наследуемого вердикта пропущен," \
            "идёт полный прогон." >&2
    elif [ "$lr_fetch_ok" -ne 1 ]; then
        echo "ЗАМЕТКА: прошлые ревью не прочитались (gh) — дедуп пропущен," \
            "идёт полный прогон." >&2
    # Отбор кандидата и валидация РАЗНЕСЕНЫ намеренно. Кандидат протокола —
    # ревью, в теле которого есть префикс маркера; выбирается ПОСЛЕДНИЙ такой,
    # и только он валидируется. Правило «последнее ВАЛИДНОЕ ревью» пропускало бы
    # новый повреждённый или задублированный маркер и воскрешало перекрытый им
    # вердикт — stop rule решал бы по вердикту, который свежее событие протокола
    # уже отменило. Повреждённый, задублированный и DISMISSED кандидат остаётся
    # miss, поиск назад НЕ ведётся. Не-кандидаты (scope-аттестации, любые ревью
    # $REVIEW_LOGIN без префикса) на результат не влияют.
    elif ! candidate=$(printf '%s' "$lr_json" | jq -rs \
        '([ .[][]
           | select(.user.login == "'"$REVIEW_LOGIN"'")
           | select(((.body // "") | index("<!-- codex-terminal-review ")) != null)
         ] | last) as $r
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
budget_exceeded=0
[ ! -f "$budget_ledger" ] \
    || budget_used=$(wc -l < "$budget_ledger" | tr -d ' ')
budget_override_used=0
if [ "$lr_state" = "APPROVED" ]; then
    if [ -z "$budget_override" ]; then
        die 6 "новый круг ревью открывает только блокирующая находка:" \
            "последнее ревью $REVIEW_LOGIN на ${slug}#${pr} — approve," \
            "то есть по порогу (blocker/major + confidence: high +" \
            "evidence) блокирующих находок не было. Не-блокирующее чините" \
            "без повторного ревью либо записывайте в debt." \
            "Нужен круг вопреки этому (например, на PR приехал новый код)" \
            "— это решение владельца: --budget-override '<причина>'."
    fi
    # Обход stop rule — такое же решение владельца, как перерасход бюджета, и
    # так же обязан быть виден в вердикте: иначе он остаётся только в памяти
    # того, кто его принял.
    budget_override_used=1
fi
if [ "$budget_used" -ge "$budget_max" ]; then
    if [ -z "$budget_override" ]; then
        die 6 "бюджет платных прогонов исчерпан:" \
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
# Списывается СОСТОЯВШИЙСЯ вердикт, а не попытка: отказ прибора (кит 2/3)
# вердикта не даёт, и списывать за него значило бы тратить бюджет на прогоны,
# ни один из которых ревью не принёс. Поэтому запись в журнал — ниже, после
# успешного кода кита. Проверка при этом остаётся здесь, до вызова: барьер
# обязан быть fail-closed.
budget_charge() {
    {
        printf 'round=%s at=%s head=%s code=%s' \
            "$budget_round" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$head_sha" \
            "$kit_code"
        [ "$budget_exceeded" -eq 0 ] && [ "$budget_override_used" -eq 0 ] \
        || printf ' override=%s' "$budget_override"
        printf '\n'
    } >> "$budget_ledger" \
        || die 2 "не удалось записать журнал бюджета: $budget_ledger"
}

# --- Полный прогон -------------------------------------------------------
# Доверенный кит запускается с cwd в exact-head worktree: код, промпт и схема
# приходят из исходного чекаута, а контекст файлов — из проверяемого дерева.
# Свежесть базы: при fp-ките база уже освежена явным fetch выше (и отпечаток
# обязан видеть то же состояние), у старого кита --fetch остаётся его
# собственной заботой.
set -- --base "$review_base" --head "$review_ref" --format markdown
[ "$fp_supported" -eq 1 ] || set -- "$@" --fetch
# shellcheck disable=SC2086 — cap_args проверен: только флаги и цифры.
[ -z "$cap_args" ] || set -- "$@" $cap_args
[ -z "$include_prose_arg" ] || set -- "$@" $include_prose_arg
set +e
run_kit "$@" \
    > "$work/verdict.md" 2> "$work/local.err"
kit_code=$?
set -e
# stderr кита — всегда наружу: там предупреждения о свежести базы и причины
# отказов.
cat "$work/local.err" >&2

case "$kit_code" in
    # Вердикт состоялся — модель отработала и деньги потрачены: круг
    # списывается здесь, до любых дальнейших отказов (в т.ч. «голова уехала»:
    # прогон был платным независимо от того, публикуем ли мы его).
    0) action="approve"; budget_charge ;;
    1) action="request-changes"; budget_charge ;;
    # Кит отфильтровал прозу целиком (срез B). Модель не звалась, круг не
    # списывается. Обвязка могла классифицировать PR как кодовый — репо-конфиг
    # и пиненое правило кита видит именно кит, и его слово здесь последнее.
    5) publish_scope_attestation kit-filtered; exit 0 ;;
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
    if [ "$targeted" -eq 1 ]; then
        # Дефект 4 вырезанной реализации: approve из адресного прогона
        # выглядит как полное ревью головы — так его читает и человек, и
        # всякий, кто вернётся к PR позже. Сужение обязано быть в теле, а
        # не только в argv, которого в PR никто не увидит.
        echo "- **адресный recheck**: база — прошлая отревьюированная" \
            "голова \`$review_base\`, проверены только изменения после" \
            "неё. Полного ревью текущей головы этот вердикт НЕ заменяет."
    fi
    if [ "$budget_exceeded" -eq 1 ]; then
        # Перерасход — факт о прогоне, который читатель вердикта обязан
        # видеть: иначе он остаётся только в прозе агента и в локальном
        # журнале, то есть невидим тому, кто принимает остаточный риск.
        echo "- бюджет платных прогонов превышен: круг $budget_round при" \
            "лимите $budget_max, по решению владельца — $budget_override"
    elif [ "$budget_override_used" -eq 1 ]; then
        echo "- круг открыт вопреки stop rule (прошлое ревью — approve)," \
            "по решению владельца — $budget_override"
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
