#!/bin/sh
# attest-vendor.sh — детерминированная аттестация целостности вендор-копии
# review-kit на волновом PR ре-вендора (решение владельца 2026-09-19).
#
# Зачем: волновой PR ре-вендора кита — побайтовая копия файлов из апстрима
# (steward), содержательного ревью там нет и быть не должно. Но правило
# ветки требует одобряющего ревью, а автор PR — учётка оператора, которая
# не может одобрить сама себя. Зелёный `checksum.sh` НЕ достаточен: он
# доказывает лишь, что файлы соответствуют своему же PIN, а не то, что PIN
# указывает на нужный апстрим — подменённый PIN вместе с подменёнными
# файлами прошёл бы эту проверку зелёным. Ядро этого инструмента —
# НЕЗАВИСИМАЯ побайтовая сверка каждого члена кита с апстримом на коммите,
# названном в шапке PIN (`git -C <steward> show <sha>:<путь>`), а не только
# сверка копии со своим PIN.
#
# Подход к материализации PR и публикации — тот же, что у review-pr.sh:
# голова PR фетчится в служебный ref и раскладывается во временный detached
# worktree, рабочее дерево целевого репо не трогается; публикация — под
# отдельным профилем gh (GH_CONFIG_DIR, аккаунт ai-prosto), логин сверяется
# перед публикацией.
#
# Порядок проверок (в этом порядке, fail-closed на каждом шаге):
#   1. материализовать голову PR во временный worktree;
#   2. прочитать `# SOURCE: steward @ <sha>` из scripts/review/PIN головы —
#      заявленный апстрим;
#   3. побайтово сравнить каждый член кита из PIN с тем же файлом в
#      апстриме на названном коммите — ЯДРО задачи;
#   4. выполнить scripts/review/checksum.sh --pin scripts/review/PIN в
#      дереве головы;
#   5. только если оба шага чисты — опубликовать --approve от ai-prosto.
#
# Тело аттестации называет: коммит головы и коммит апстрима, результат
# побайтовой сверки, результат checksum.sh и явный отказ от содержательного
# ревью — модель не вызывалась, это НЕ codex-terminal-review, семантика
# изменений не проверялась. Маркер — собственный, без подстроки
# `<!-- codex-terminal-review `, чтобы потребители протокола не прочли
# аттестацию как модельный вердикт.
#
# Коды выхода:
#   0 — сверка чиста, аттестация опубликована (или dry-run);
#   2 — конфигурация, аргументы, состояние PR, публикация;
#   3 — сверка НЕ прошла (расхождение с апстримом или красный checksum),
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

# Профиль публикации — тот же, что у review-pr.sh: REVIEW_LOGIN сверяется
# с фактическим логином профиля, опечатка в GH_CONFIG_DIR молча постила бы
# аттестацию от основного аккаунта — «независимая» от самого автора PR.
REVIEW_GH_CONFIG_DIR="${REVIEW_GH_CONFIG_DIR:-$HOME/.config/review}"
REVIEW_LOGIN="${REVIEW_LOGIN:-ai-prosto}"

gh_r() {
    GH_CONFIG_DIR="$REVIEW_GH_CONFIG_DIR" gh "$@"
}

# Корень флота — родитель devtools/, где лежит этот скрипт; FLEET_ROOT —
# явный оверрайд для тестов и ephemeral-workspace (та же логика, что у
# review-pr.sh). Апстрим steward — сосед по флоту; STEWARD_DIR — отдельный
# оверрайд на случай нестандартной раскладки.
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
repo_dir=$(cd "$repo_dir" && pwd)

steward_dir="${STEWARD_DIR:-$FLEET_ROOT/steward}"
[ -d "$steward_dir" ] || die 2 "апстрим 'steward' не найден: $steward_dir"
steward_dir=$(cd "$steward_dir" && pwd)

# Slug из СЫРОГО remote.origin.url (не `remote get-url`): insteadOf-
# переписывание локального зеркала не должно подменить owner/name — та же
# логика, что у review-pr.sh.
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
# называться сразу, а не после материализации головы.
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
    --json headRefOid,state \
    --jq '.headRefOid + " " + .state' 2>&1); then
    die 2 "не удалось прочитать PR ${slug}#${pr}: $pr_info"
fi
read -r head_oid state <<EOF
$pr_info
EOF
[ "$state" = "OPEN" ] || die 2 "PR ${slug}#${pr} не открыт (state=$state)"

# --- Шаг 1: материализация головы PR (review-pr.sh, тот же приём) ----------
# Собственный ref: не делим служебное пространство с review-pr.sh — оба
# инструмента могут прогоняться по одному PR независимо.
attest_ref="refs/attest/pr-$pr"
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

work=$(mktemp -d)
attest_tree="$work/attest-tree"
attest_tree_added=0
# shellcheck disable=SC2329  # trap EXIT invokes cleanup indirectly.
cleanup() {
    if [ "$attest_tree_added" -eq 1 ]; then
        git -C "$repo_dir" worktree remove --force "$attest_tree" \
            >/dev/null 2>&1 || true
    fi
    rm -rf "$work"
}
trap cleanup EXIT
if ! worktree_err=$(git -C "$repo_dir" worktree add --detach \
    "$attest_tree" "$head_sha" 2>&1); then
    echo "$worktree_err" >&2
    die 2 "не удалось материализовать head $head_sha во временный worktree"
fi
attest_tree_added=1
head_tree="$attest_tree"

# --- Шаг 2: SOURCE из PIN головы --------------------------------------------
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
case "$upstream_sha" in
    ''|*[!0-9a-fA-F]*)
        die 2 "не удалось разобрать SHA апстрима из строки SOURCE: \
$source_line" ;;
esac

# Апстрим-коммит обязан существовать локально; отсутствующий после
# попытки освежить — конфигурация, не дрейф (fail-closed: факт не
# получен — против публикации). Отказ fetch НЕ фатален сам по себе: важен
# только итоговый факт наличия коммита (локальный steward без remote —
# легальная тестовая/офлайн конфигурация).
if ! git -C "$steward_dir" cat-file -e "${upstream_sha}^{commit}" \
    2>/dev/null; then
    git -C "$steward_dir" fetch -q origin >/dev/null 2>&1 || true
fi
git -C "$steward_dir" cat-file -e "${upstream_sha}^{commit}" 2>/dev/null \
    || die 2 "апстрим-коммит steward @ $upstream_sha не найден в \
$steward_dir (после попытки fetch) — сверить не с чем"

# --- Шаг 3: побайтовая сверка каждого члена кита с апстримом (ЯДРО) --------
# Хеш из строки PIN здесь не используется: он проверяется checksum.sh
# (шаг 4). Здесь сверяются РЕАЛЬНЫЕ байты локального файла с РЕАЛЬНЫМИ
# байтами того же пути в апстриме на названном коммите — так подмена PIN
# вместе с подменёнными файлами (зелёный checksum.sh) остаётся видна.
compared=0
mismatched=0
mismatch_report=""
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        ''|'#'*) continue ;;
    esac
    path="${line#*  }"
    if [ "$path" = "$line" ] || [ -z "$path" ]; then
        die 2 "битая строка PIN (ожидался '<sha256>  <путь>'): $line"
    fi
    compared=$((compared + 1))
    local_file="$head_tree/$path"
    if [ -L "$local_file" ]; then
        mismatched=$((mismatched + 1))
        mismatch_report="$mismatch_report
  $path — СИМЛИНК в копии, обязан быть обычным файлом"
        continue
    fi
    if [ ! -f "$local_file" ] || [ ! -r "$local_file" ]; then
        mismatched=$((mismatched + 1))
        mismatch_report="$mismatch_report
  $path — отсутствует или нечитаем в копии"
        continue
    fi
    upstream_blob="$work/upstream-blob"
    rm -f "$upstream_blob" "$work/show.err"
    if ! git -C "$steward_dir" show "${upstream_sha}:${path}" \
        > "$upstream_blob" 2> "$work/show.err"; then
        show_err=$(cat "$work/show.err")
        mismatched=$((mismatched + 1))
        mismatch_report="$mismatch_report
  $path — отсутствует в апстриме steward @ $upstream_sha ($show_err)"
        continue
    fi
    if ! cmp -s "$local_file" "$upstream_blob"; then
        mismatched=$((mismatched + 1))
        mismatch_report="$mismatch_report
  $path — байты РАСХОДЯТСЯ с апстримом"
    fi
done < "$pin_path"
rm -f "$work/upstream-blob"

[ "$compared" -gt 0 ] || die 2 "$pin_rel не перечисляет ни одного файла — \
сравнивать нечего"

if [ "$mismatched" -gt 0 ]; then
    echo "СВЕРКА С АПСТРИМОМ НЕ ПРОШЛА: $mismatched из $compared член(ов) \
разошлись с steward @ $upstream_sha:$mismatch_report" >&2
    die 3 "аттестация не публикуется — вендор-копия не доказана как копия \
названного апстрима."
fi

# --- Шаг 4: checksum.sh --pin (копия соответствует своему же PIN) ---------
checksum_bin="$head_tree/scripts/review/checksum.sh"
[ -f "$checksum_bin" ] || die 2 "в голове PR нет scripts/review/checksum.sh"
set +e
checksum_out=$(cd "$head_tree" && sh scripts/review/checksum.sh \
    --pin "$pin_rel" 2>&1)
checksum_code=$?
set -e
echo "$checksum_out" >&2
if [ "$checksum_code" -ne 0 ]; then
    die 3 "checksum.sh --pin вернул $checksum_code — сверка НЕ прошла, \
аттестация не публикуется."
fi

# --- Тело аттестации --------------------------------------------------------
{
    echo "## Vendor-copy integrity attestation — review-kit"
    echo
    echo "- PR: ${slug}#${pr}, head \`$head_sha\`"
    echo "- заявленный апстрим (из шапки $pin_rel): steward @" \
        "\`$upstream_sha\`"
    echo "- побайтовая сверка с апстримом: $compared член(ов) кита из" \
        "$pin_rel, все совпали байт-в-байт"
    echo "- $checksum_out"
    echo
    echo "**Это НЕ содержательное ревью.** Модель не вызывалась, семантика" \
        "изменений не проверялась. Это не \`codex-terminal-review\` —" \
        "детерминированная аттестация целостности вендор-копии: сверка" \
        "байтов копии одновременно с апстримом (на коммите из шапки PIN)" \
        "и с собственным PIN копии. Required CI checks остаются" \
        "обязательным независимым условием мержа."
    echo
    echo "<!-- ai-prosto-vendor-attestation version=1 kind=review-kit" \
        "head=$head_sha upstream=$upstream_sha -->"
} > "$work/body.md"

# --- Шаг 5: публикация -----------------------------------------------------
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
