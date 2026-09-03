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
    echo "usage: review-pr.sh <repo> <pr-number> [--dry-run] [--fresh]" \
        "[--write-verdict <file> | --use-verdict <file>]" \
        "[--harness claude|codex] [--model <M>]" >&2
    echo "  <repo> — имя каталога репо во флоте (например dispatcher)" >&2
    echo "  --fresh — не наследовать вердикт даже при совпавшем отпечатке" >&2
    echo "  --write-verdict — атомарно сохранить результат dry-run для боевого прогона" >&2
    echo "  --use-verdict — использовать сохранённый результат при точных head + fp" >&2
    echo "  --harness/--model — ревьюер; порядок: флаг > env REVIEW_HARNESS/" >&2
    echo "    REVIEW_MODEL > ~/.config/ai-prosto/harness.env > codex (историч.)" >&2
    echo "  внешний REVIEW_CMD побеждает всё, кроме явных флагов" >&2
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
print_review_cmd=0
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) dry_run=1; shift ;;
        --fresh) fresh=1; shift ;;
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
# Для claude отпечаток входа обязан быть машинно-независимым: review_cmd —
# компонента fp кита, и абсолютный путь к переходнику развёл бы отпечатки
# между машинами. Поэтому в REVIEW_CMD идёт голое имя `claude-review`, а
# каталог переходника добавляется в PATH сабшелла кита.
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
if [ -n "${REVIEW_CMD:-}" ] && [ -z "$opt_harness" ] && [ -z "$opt_model" ]; then
    : # внешний REVIEW_CMD — осознанный оверрайд целиком, не трогаем
else
    # Явный флаг перекрывает и внешний REVIEW_CMD (то же ревью, второй
    # minor): `--harness codex` без модели обязан дать дефолт кита, а не
    # оставить унаследованный из окружения claude-переходник.
    unset REVIEW_CMD || true
    case "$harness" in
        claude)
            PATH="$script_dir/scripts/harness:$PATH"
            export PATH
            REVIEW_CMD="claude-review --model ${model:-claude-opus-5}"
            export REVIEW_CMD
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
        *) die 2 "неизвестный харнесс: '$harness' (claude|codex)" ;;
    esac
fi
reviewer_label="${REVIEW_CMD:-codex exec}"
if [ "$print_review_cmd" -eq 1 ]; then
    # Отладочный зонд для тестов: показать разрешённую команду и выйти,
    # не трогая ни репо, ни GitHub.
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
    echo "- ревьюер: \`$reviewer_label\` через review-kit репо;" \
        "публикация: $REVIEW_LOGIN"
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
