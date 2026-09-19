#!/bin/sh
# attest-vendor.sh — детерминированная аттестация целостности вендор-копии
# review-kit на волновом PR ре-вендора (решение владельца 2026-09-19).
#
# Зачем: волновой PR ре-вендора кита — побайтовая копия файлов из апстрима
# (steward), содержательного ревью там нет и быть не должно. Но правило
# ветки требует одобряющего ревью, а автор PR — учётка оператора, которая
# не может одобрить сама себя.
#
# ПРИНЦИП (ревью 6af24ee, фикс-заход): инструмент публикует одобрение от
# имени машины, значит НИ ОДИН факт, определяющий исход, не приходит из
# дерева проверяемого PR — ни перечень файлов, ни инвентарь, ни код чекера,
# ни текст, попадающий в тело. Всё доверенное — из апстрима (steward) и из
# GitHub API (baseRefName/headRefOid/state/reviews), а не из содержимого
# самого PR-диффа.
#
# Конкретно это значит:
#   - инвентарь и полноту PIN проверяет checksum.sh, ИЗВЛЕЧЁННЫЙ ИЗ
#     АПСТРИМА на коммите SOURCE и исполненный против дерева головы — не
#     checksum.sh из головы PR (тот же чекер, который PR мог ослабить);
#   - заявленный апстрим обязан быть ПРЕДКОМ доверенной ветки steward
#     (origin/master), а не произвольным существующим коммитом;
#   - PR обязан целиком лежать внутри состава кита (диапазон base..head) —
#     иначе аттестация одобрила бы посторонний диф;
#   - побайтовая и режимная сверка каждого члена кита с апстримом идёт по
#     git-объектам (ls-tree: mode+blob-oid), не по чекауту — так же надёжно
#     ловит и расхождение контента, и потерянный/лишний бит исполнения, и
#     подмену обычного файла симлинком;
#   - в тело аттестации идёт ТОЛЬКО наш собственный вычисленный текст —
#     вывод чужого (даже апстримного) кода в тело не копируется, только в
#     stderr для оператора; страж перед публикацией отказывает, если в
#     теле оказалась хоть одна лишняя `<!--`-последовательность;
#   - доставленный CHANGES_REQUESTED от ai-prosto на этом PR не гасится
#     синтетическим approve; повтор на той же голове идемпотентен (дедуп).
#
# Материализация головы PR и профиль публикации — тем же приёмом, что у
# review-pr.sh: служебный ref (свой — refs/attest/pr-<N>, не общий с
# review-pr.sh) + временный detached worktree, рабочее дерево целевого репо
# не трогается; публикация — под отдельным профилем gh (GH_CONFIG_DIR,
# аккаунт ai-prosto), логин сверяется перед публикацией.
#
# Коды выхода:
#   0 — сверка чиста, аттестация опубликована (или dry-run, или уже была
#       опубликована на этой голове — дедуп);
#   2 — конфигурация, аргументы, состояние PR, публикация, невозможность
#       установить факт (fail-closed: неустановленный факт — против
#       публикации);
#   3 — сверка НЕ прошла (апстрим не предок доверенной ветки, PR трогает
#       пути вне кита, расхождение с апстримом, красный checksum.sh),
#       аттестация не публикуется;
#   4 — голова PR уехала между сверкой и публикацией.
set -eu

usage() {
    echo "usage: attest-vendor.sh <repo> <pr> [--dry-run]" >&2
    echo "  <repo> — имя каталога репо во флоте (например dispatcher)" >&2
    echo "  <pr> — номер PR ре-вендора кита в этом репо" >&2
    echo "  --dry-run — напечатать тело аттестации, ничего не публиковать" >&2
}

die() {
    _code="$1"; shift
    echo "$*" >&2
    exit "$_code"
}

# Профиль публикации — тот же, что у review-pr.sh.
REVIEW_GH_CONFIG_DIR="${REVIEW_GH_CONFIG_DIR:-$HOME/.config/review}"
REVIEW_LOGIN="${REVIEW_LOGIN:-ai-prosto}"

gh_r() {
    GH_CONFIG_DIR="$REVIEW_GH_CONFIG_DIR" gh "$@"
}

# Корень флота — родитель devtools/; FLEET_ROOT — оверрайд для тестов.
# Апстрим steward — сосед по флоту; STEWARD_DIR — отдельный оверрайд.
# Доверенная ветка steward, против которой проверяется ancestry (I1).
script_dir=$(cd "$(dirname "$0")" && pwd)
FLEET_ROOT="${FLEET_ROOT:-$(dirname "$script_dir")}"
STEWARD_TRUSTED_REF="${STEWARD_TRUSTED_REF:-origin/master}"
MARKER_NAME="ai-prosto-vendor-attestation"

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
repo_dir=$(cd "$repo_dir" && pwd)

steward_dir="${STEWARD_DIR:-$FLEET_ROOT/steward}"
[ -d "$steward_dir" ] || die 2 "апстрим 'steward' не найден: $steward_dir"
steward_dir=$(cd "$steward_dir" && pwd)

if ! command -v jq >/dev/null 2>&1; then
    die 2 "jq не найден — состояние прошлых ревью (красный вердикт/дедуп)" \
        "не определить, аттестация не публикуется."
fi

# Slug из СЫРОГО remote.origin.url — та же логика, что у review-pr.sh.
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

# Preflight профиля — ДО любых обращений к PR.
[ -d "$REVIEW_GH_CONFIG_DIR" ] || die 2 "профиля аттестатора нет: \
$REVIEW_GH_CONFIG_DIR — выполните:
  GH_CONFIG_DIR=\"$REVIEW_GH_CONFIG_DIR\" gh auth login --hostname github.com --web"
if ! login=$(gh_r api user --jq .login 2>&1); then
    die 2 "профиль аттестатора не отвечает: $login"
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

work=$(mktemp -d)
attest_tree_added=0
attest_ref="refs/attest/pr-$pr"
# shellcheck disable=SC2329  # trap EXIT invokes cleanup indirectly.
cleanup() {
    if [ "$attest_tree_added" -eq 1 ]; then
        git -C "$repo_dir" worktree remove --force "$work/attest-tree" \
            >/dev/null 2>&1 || true
    fi
    git -C "$repo_dir" update-ref -d "$attest_ref" >/dev/null 2>&1 || true
    rm -rf "$work"
}
trap cleanup EXIT

# --- Прошлые ревью $REVIEW_LOGIN: красный вердикт и дедуп (I3) -------------
# Один запрос к /reviews обслуживает оба guard'а ниже. Отказ API/jq здесь
# fail-closed (die 2), а не деградация с предупреждением, как в review-pr.sh:
# там это defense-in-depth поверх бюджетного барьера, здесь — единственная
# защита от гашения красного вердикта и от повторной публикации.
if ! reviews_json=$(gh_r api --paginate "repos/$slug/pulls/$pr/reviews" \
    2>&1); then
    die 2 "не удалось прочитать прошлые ревью ${slug}#${pr}: $reviews_json"
fi
if ! lr_delivered_state=$(printf '%s' "$reviews_json" | jq -rs \
    '([ .[][] | select(.user.login == "'"$REVIEW_LOGIN"'")
       | select(.state == "APPROVED" or .state == "CHANGES_REQUESTED") ]
     | last) as $r | if $r == null then "none" else $r.state end' \
    2>&1); then
    die 2 "не удалось разобрать прошлые ревью ${slug}#${pr} (jq): \
$lr_delivered_state"
fi
[ "$lr_delivered_state" != "CHANGES_REQUESTED" ] || die 2 "последнее \
доставленное ревью $REVIEW_LOGIN на ${slug}#${pr} — CHANGES_REQUESTED: \
аттестация не публикуется (не гасит красный вердикт синтетическим \
approve) — PR остаётся человеку."

# --- Материализация головы PR (review-pr.sh, тот же приём) -----------------
if ! fetch_err=$(git -C "$repo_dir" fetch -q origin \
    "+pull/$pr/head:$attest_ref" 2>&1); then
    echo "$fetch_err" >&2
    die 2 "не удалось зафетчить голову PR ${slug}#${pr}"
fi
head_sha=$(git -C "$repo_dir" rev-parse "$attest_ref")
if [ "$head_sha" != "$head_oid" ]; then
    echo "ЗАМЕТКА: API отдал голову $head_oid, зафетчено $head_sha —" \
        "аттестуем зафетченное." >&2
fi

# Дедуп: наш маркер с ТЕКУЩИМ head_sha уже опубликован — второй approve не
# нужен. Ищем в уже прочитанном reviews_json (второй запрос не идёт).
if ! dedup_seen=$(printf '%s' "$reviews_json" | jq -rs --arg h "$head_sha" \
    --arg m "<!-- $MARKER_NAME version=1 kind=review-kit head=" \
    '([ .[][] | select(.user.login == "'"$REVIEW_LOGIN"'")
       | select((.body // "") | contains($m + $h)) ] | length) > 0' \
    2>&1); then
    die 2 "не удалось проверить дедуп по прошлым ревью (jq): $dedup_seen"
fi
if [ "$dedup_seen" = "true" ]; then
    echo "ЗАМЕТКА: аттестация на этой голове уже опубликована — ничего" \
        "не публикуется."
    exit 0
fi

head_tree="$work/attest-tree"
if ! worktree_err=$(git -C "$repo_dir" worktree add --detach \
    "$head_tree" "$head_sha" 2>&1); then
    echo "$worktree_err" >&2
    die 2 "не удалось материализовать head $head_sha во временный worktree"
fi
attest_tree_added=1

# --- SOURCE из PIN головы ----------------------------------------------------
pin_rel="scripts/review/PIN"
pin_path="$head_tree/$pin_rel"
[ ! -L "$pin_path" ] \
    || die 2 "PIN — симлинк, а обязан быть обычным файлом: $pin_rel"
[ -f "$pin_path" ] || die 2 "в голове PR ${slug}#${pr} нет $pin_rel"
[ -r "$pin_path" ] || die 2 "$pin_rel в голове PR нечитаем"

source_line=$(grep -m1 '^# SOURCE: steward @ ' "$pin_path") || \
    die 2 "в $pin_rel нет строки '# SOURCE: steward @ <sha>' — заявленный \
апстрим не назван"
upstream_sha="${source_line#'# SOURCE: steward @ '}"
upstream_sha="${upstream_sha%% *}"
# Полные 40 hex, не сокращённый префикс: короткий SHA называет коммит
# неполно, и рост steward однажды делает префикс неоднозначным.
case "$upstream_sha" in
    ????????????????????????????????????????) : ;;
    *) die 2 "SHA апстрима обязан быть полными 40 hex, получено из \
SOURCE-строки '$source_line': '$upstream_sha'" ;;
esac
case "$upstream_sha" in
    *[!0-9a-f]*) die 2 "SHA апстрима не hex: '$upstream_sha'" ;;
esac

# --- I1: апстрим обязан существовать И быть предком доверенной ветки -------
# fetch — ОБЯЗАТЕЛЬНЫЙ, не best-effort: без свежей базы ancestry-проверка не
# ловит коммит, который был на steward-мастере и с тех пор вычищен из
# истории (даунгрейд/откат исправления безопасности назад в кит).
if ! fetch_err=$(git -C "$steward_dir" fetch -q origin 2>&1); then
    echo "$fetch_err" >&2
    die 2 "не удалось освежить steward (git fetch origin) — без свежей \
базы ancestry-проверке доверять нельзя."
fi
git -C "$steward_dir" cat-file -e "${upstream_sha}^{commit}" 2>/dev/null \
    || die 2 "апстрим-коммит steward @ $upstream_sha не найден в \
$steward_dir после fetch — сверить не с чем"
if ! git -C "$steward_dir" merge-base --is-ancestor \
    "$upstream_sha" "$STEWARD_TRUSTED_REF"; then
    die 3 "steward @ $upstream_sha НЕ является предком доверенной ветки \
$STEWARD_TRUSTED_REF — заявленный апстрим вне доверенной истории, \
аттестация не публикуется."
fi

# --- Инвентарь и полнота PIN: checksum.sh ИЗ АПСТРИМА против дерева головы -
# Код из PR не исполняется никогда: чекер извлекается из steward на
# доказанно-доверенном коммите upstream_sha и запускается против дерева
# головы. Так подмена/сужение head-копии checksum.sh (и вычищенные под неё
# строки PIN) больше не может ослабить проверку полноты — инвентарь и
# пороги берутся из НАСТОЯЩЕГО checksum.sh, а не из версии, которую мог
# отредактировать автор PR.
upstream_checksum="$work/upstream-checksum.sh"
if ! git -C "$steward_dir" show \
    "${upstream_sha}:scripts/review/checksum.sh" \
    > "$upstream_checksum" 2>"$work/checksum-extract.err"; then
    cat "$work/checksum-extract.err" >&2
    die 2 "не удалось извлечь scripts/review/checksum.sh из апстрима \
steward @ $upstream_sha"
fi
set +e
checksum_out=$(cd "$head_tree" && sh "$upstream_checksum" \
    --pin "$pin_rel" 2>&1)
checksum_code=$?
set -e
# Вывод — только в stderr, для оператора. В тело НЕ попадает никогда, даже
# при коде 0: это защита C2, а не только «убрать грязь при отказе».
echo "$checksum_out" >&2
if [ "$checksum_code" -ne 0 ]; then
    die 3 "checksum.sh (апстрим steward @ $upstream_sha) --pin вернул \
$checksum_code — сверка НЕ прошла, аттестация не публикуется."
fi

# --- Перечень членов кита из ТЕПЕРЬ УЖЕ доверенного PIN головы --------------
# Доверие к этому перечню — не самоданность PIN, а следствие шага выше:
# checksum.sh с АПСТРИМНЫМ (не головы) инвентарём уже подтвердил, что PIN
# покрывает состав кита ровно, без лишних и без недостающих строк (иначе он
# отказал бы кодом 1/2 и мы бы сюда не дошли). Поэтому пути из PIN здесь —
# тот же факт, что и «апстримный инвентарь», просто без повторного парсинга
# синтаксиса чужого шелл-скрипта.
members_file="$work/members.list"
: > "$members_file"
compared=0
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        ''|'#'*) continue ;;
    esac
    path="${line#*  }"
    if [ "$path" = "$line" ] || [ -z "$path" ]; then
        die 2 "битая строка PIN (ожидался '<sha256>  <путь>'): $line"
    fi
    compared=$((compared + 1))
    printf '%s\n' "$path" >> "$members_file"
done < "$pin_path"
[ "$compared" -gt 0 ] || die 2 "$pin_rel не перечисляет ни одного файла — \
сравнивать нечего"

# --- I2: PR обязан целиком лежать внутри состава кита -----------------------
# Аттестация существует только для PR, целиком состоящих из ре-вендора
# кита: посторонний путь в диапазоне base..head одобрялся бы молча.
if ! fetch_err=$(git -C "$repo_dir" fetch -q origin \
    "+refs/heads/$base_ref:refs/remotes/origin/$base_ref" 2>&1); then
    echo "$fetch_err" >&2
    die 2 "не удалось освежить базу origin/$base_ref перед проверкой \
области PR"
fi
if ! merge_base=$(git -C "$repo_dir" merge-base \
    "origin/$base_ref" "$head_sha" 2>"$work/mb.err"); then
    cat "$work/mb.err" >&2
    die 2 "не удалось определить merge-base с origin/$base_ref"
fi
changed_paths="$work/changed.list"
if ! git -C "$repo_dir" diff --no-renames --name-only \
    "$merge_base..$head_sha" > "$changed_paths" 2>"$work/diff.err"; then
    cat "$work/diff.err" >&2
    die 2 "не удалось получить список изменённых путей PR"
fi
out_of_scope=""
while IFS= read -r cp || [ -n "$cp" ]; do
    [ -n "$cp" ] || continue
    [ "$cp" != "$pin_rel" ] || continue
    grep -qxF "$cp" "$members_file" && continue
    out_of_scope="$out_of_scope
  $cp"
done < "$changed_paths"
if [ -n "$out_of_scope" ]; then
    echo "PR ${slug}#${pr} трогает пути вне состава кита:$out_of_scope" >&2
    die 3 "аттестация не публикуется — PR не целиком состоит из ре-вендора \
кита, посторонний диф остаётся предметом обычного ревью."
fi

# --- Ядро: побайтовая и режимная сверка каждого члена с апстримом ----------
# По git-объектам (ls-tree: mode + blob-oid), не по чекауту: совпавший
# blob-oid гарантирует побайтовое совпадение контента (git — content-
# addressable), а mode ловит и потерянный/лишний бит исполнения (PIN,
# требующий 100755 на harness-claude), и подмену обычного файла симлинком
# (mode 120000 не совпадёт ни с одним ожидаемым режимом кита) — без
# отдельной проверки каждого компонента пути на симлинк, которая нужна
# только при чтении с диска.
#
# Хеш из строки PIN здесь не используется: он уже проверен checksum.sh
# (апстримным, шагом выше). Здесь сверяются РЕАЛЬНЫЕ git-объекты головы с
# РЕАЛЬНЫМИ git-объектами апстрима на названном коммите — так подмена PIN
# вместе с подменёнными файлами (перед которой checksum.sh бессилен, ведь
# он сверяет копию с её же PIN) остаётся видна.
tree_entry() {
    # $1 = commit-ish, $2 = path, $3 = репо. Результат — te_mode/te_type/
    # te_oid; возврат 1, если пути на этом коммите нет.
    _te_line=$(git -C "$3" ls-tree "$1" -- "$2" 2>"$work/lstree.err") \
        || { cat "$work/lstree.err" >&2; die 2 "git ls-tree отказал для \
$2 @ $1 в $3"; }
    if [ -z "$_te_line" ]; then
        te_mode=""; te_type=""; te_oid=""
        return 1
    fi
    _te_meta=$(printf '%s' "$_te_line" | cut -f1)
    te_mode=${_te_meta%% *}
    _te_rest=${_te_meta#* }
    te_type=${_te_rest%% *}
    te_oid=${_te_rest#* }
    return 0
}

mismatched=0
mismatch_report=""
while IFS= read -r path || [ -n "$path" ]; do
    [ -n "$path" ] || continue
    if ! tree_entry "$head_sha" "$path" "$repo_dir"; then
        mismatched=$((mismatched + 1))
        mismatch_report="$mismatch_report
  $path — отсутствует в голове PR"
        continue
    fi
    head_mode="$te_mode"; head_type="$te_type"; head_oid="$te_oid"

    if ! tree_entry "$upstream_sha" "$path" "$steward_dir"; then
        mismatched=$((mismatched + 1))
        mismatch_report="$mismatch_report
  $path — отсутствует в апстриме steward @ $upstream_sha"
        continue
    fi
    up_mode="$te_mode"; up_type="$te_type"; up_oid="$te_oid"

    if [ "$head_type" != blob ] || [ "$up_type" != blob ]; then
        mismatched=$((mismatched + 1))
        mismatch_report="$mismatch_report
  $path — не обычный файл (голова: $head_type, апстрим: $up_type)"
        continue
    fi
    if [ "$head_mode" != "$up_mode" ]; then
        mismatched=$((mismatched + 1))
        mismatch_report="$mismatch_report
  $path — режим расходится (голова $head_mode, апстрим $up_mode)"
        continue
    fi
    if [ "$head_oid" != "$up_oid" ]; then
        mismatched=$((mismatched + 1))
        mismatch_report="$mismatch_report
  $path — байты расходятся с апстримом"
    fi
done < "$members_file"

if [ "$mismatched" -gt 0 ]; then
    echo "СВЕРКА С АПСТРИМОМ НЕ ПРОШЛА: $mismatched из $compared член(ов) \
разошлись с steward @ $upstream_sha:$mismatch_report" >&2
    die 3 "аттестация не публикуется — вендор-копия не доказана как копия \
названного апстрима."
fi

# --- Тело аттестации: ТОЛЬКО наш собственный вычисленный текст (C2) --------
# Ни один символ, взятый из PIN/дифа/чужого checksum.sh, в тело не
# попадает — только числа и статические формулировки, вычисленные этим
# скриптом. Путь к этому — сознательный: даже правильные с виду имена
# файлов не копируются из дерева PR в тело.
marker="<!-- $MARKER_NAME version=1 kind=review-kit head=$head_sha \
upstream=$upstream_sha -->"
{
    echo "## Vendor-copy integrity attestation — review-kit"
    echo
    echo "- PR: ${slug}#${pr}, head \`$head_sha\`"
    echo "- заявленный апстрим (из шапки $pin_rel): steward @" \
        "\`$upstream_sha\`, подтверждён как предок" \
        "\`$STEWARD_TRUSTED_REF\` steward"
    echo "- инвентарь и полнота PIN сверены исполнением checksum.sh," \
        "ИЗВЛЕЧЁННОГО ИЗ АПСТРИМА (не из дерева PR), против PIN и дерева" \
        "головы: код 0"
    echo "- диапазон PR (\`${base_ref}\`..head) целиком лежит внутри" \
        "состава кита — посторонних путей нет"
    echo "- побайтовая и режимная сверка по git-объектам (не по чекауту)" \
        "каждого члена кита с тем же путём в апстриме: $compared" \
        "член(ов), все совпали"
    echo
    echo "**Это НЕ содержательное ревью.** Модель не вызывалась, семантика" \
        "изменений не проверялась. Это не \`codex-terminal-review\` —" \
        "детерминированная аттестация целостности вендор-копии. Required" \
        "CI checks остаются обязательным независимым условием мержа."
    echo
    echo "$marker"
} > "$work/body.md"

# Страж C2: тело не должно содержать ничего похожего на HTML-комментарий,
# кроме ровно нашего собственного маркера — на случай, если что-то из
# вышеперечисленного всё же протащило постороннюю `<!--`-последовательность
# (например, будущая правка случайно вставит текст с диска). Отказываем
# fail-closed, а не публикуем сомнительное тело.
body_text=$(cat "$work/body.md")
marker_count=$(printf '%s' "$body_text" | grep -o -- '<!--' | wc -l \
    | tr -d ' ')
[ "$marker_count" -eq 1 ] || die 2 "тело аттестации содержит $marker_count \
последовательностей '<!--', ожидалась ровно одна (собственный маркер) — \
публикация отказана (защита от инъекции маркера, C2)."
case "$body_text" in
    *"$marker"*) : ;;
    *) die 2 "тело аттестации не содержит ожидаемый собственный маркер — \
публикация отказана." ;;
esac

# --- Публикация -------------------------------------------------------------
check_head_current() {
    if ! cur_oid=$(gh_r pr view "$pr" --repo "$slug" \
        --json headRefOid --jq .headRefOid 2>&1); then
        die 2 "не удалось перепроверить голову PR: $cur_oid"
    fi
    [ "$cur_oid" = "$head_sha" ] || die 4 "голова PR уехала: сверен \
$head_sha, сейчас $cur_oid — перегоните аттестацию."
}

if [ "$dry_run" -eq 1 ]; then
    echo "=== dry-run: аттестация не публикуется ==="
    cat "$work/body.md"
    exit 0
fi

check_head_current
if ! gh_r pr review "$pr" --repo "$slug" --approve \
    --body-file "$work/body.md"; then
    die 2 "сверка чиста, но публикация аттестации не удалась."
fi
echo "опубликовано: --approve на ${slug}#${pr} (head $head_sha) от $login"
exit 0
