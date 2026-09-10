#!/bin/sh
# merge-pr.sh — единственный разрешённый путь АГЕНТСКОГО мержа PR.
#
# Зачем: по ADR-ECO-011 («DarkFactory») агент мержит сам, от профиля
# ai-prosto. Пока это был голый `gh pr merge`, набранный руками, не
# существовало места, куда поставить проверку и накрыть её тестом. Теперь
# оно есть: прямой `gh pr merge` для агента запрещён политикой, агентский
# мерж идёт только отсюда. Человеческий мерж этой обвязкой не выполняется —
# она отказывает, если активный профиль не агентский.
#
# Три категорических отказа — PR, который агент не вправе мержить НИКОГДА,
# независимо от зелени проверок и вердикта ревью:
#   1. ветка формы candidate-шага одобрения (§I12): её мерж человеком из
#      allowlist И ЕСТЬ акт одобрения узла бандла. Смержи её агент —
#      merger окажется вне allowlist, подпись не создастся, заявка умрёт,
#      а человеческий акт будет потрачен впустую;
#   2. ветка формы финализирующего PR (§I12): тот же довод — второй мерж
#      записывает подпись, и записать её обязан тоже человек;
#   3. лейбл `human-merge-required` — явное «этот PR человеку», ручной
#      рубильник поверх любых форм имён.
# Раньше от автомержа candidate/finalize удерживал draft-статус; его сняли,
# потому что draft нельзя смержить, а мерж здесь — предмет.
#
# Имена веток НЕ вшиты сюда: шаблон читается из
# contracts/approval-branches/v1/patterns.env, тот же файл читает
# governance/approval_branches.py (механика одобрения). Глоб выводится из
# шаблона заменой `{…}` на `*` — так глоб не может отстать от строителя
# имени. Две независимо написанные строки разъезжаются молча.
#
# Отношение к governance/merge_gate.py::decide(): не дубль. `decide()`
# решает, имеет ли ПРОГОН право мержить (authority/safety/review/facts) и в
# сеть не ходит. Здесь — механический последний рубеж на самом вызове:
# отказы по фактам PR, которые не отменяются ничьей зеленью.
#
# Fail-closed везде: любой факт, который не удалось получить или разобрать,
# трактуется против действия. Неполученный факт никогда не читается в
# пользу мержа.
#
# Коды выхода:
#   0 — мерж выполнен (или показан при --dry-run);
#   2 — конфигурация, аргументы, профиль, состояние PR, неразобранные факты;
#   3 — гвард: PR запрещён к агентскому мержу, остаётся человеку;
#   4 — форджа отклонила мерж (в т.ч. голова уехала после проверки).
set -eu

usage() {
    echo "usage: merge-pr.sh <repo> <pr-number>" \
        "[--squash|--merge|--rebase] [--delete-branch] [--dry-run]" >&2
    echo "  <repo> — имя каталога репо во флоте (например dispatcher)" >&2
    echo "  стратегия — только из этого списка, дефолт --squash;" >&2
    echo "    свободного passthrough в gh нет по построению" >&2
    echo "  --delete-branch — удалить ветку после мержа (флаг gh)" >&2
    echo "  --dry-run — показать разрешённую команду мержа, не выполняя" >&2
}

die() {
    _code="$1"; shift
    echo "$*" >&2
    exit "$_code"
}

# Профиль мержа — тот же, что у публикации ревью (review-pr.sh): голый
# `gh pr merge` ушёл бы от ОСНОВНОГО аккаунта и записал агентский мерж
# человеческим, обнулив `merged_by` — наблюдаемый различитель agent/human.
# Логин сверяется с фактическим: опечатка в GH_CONFIG_DIR не должна
# оборачиваться мержем не от того, от кого сказано.
_default_profile="${REVIEW_GH_CONFIG_DIR:-$HOME/.config/review}"
MERGE_GH_CONFIG_DIR="${MERGE_GH_CONFIG_DIR:-$_default_profile}"
AGENT_LOGIN="${AGENT_LOGIN:-${REVIEW_LOGIN:-ai-prosto}}"

#: Лейбл-рубильник «этот PR мержит человек».
HUMAN_MERGE_LABEL="human-merge-required"

gh_a() {
    GH_CONFIG_DIR="$MERGE_GH_CONFIG_DIR" gh "$@"
}

script_dir=$(cd "$(dirname "$0")" && pwd)
FLEET_ROOT="${FLEET_ROOT:-$(dirname "$script_dir")}"
APPROVAL_PATTERNS="${APPROVAL_PATTERNS:-\
$script_dir/contracts/approval-branches/v1/patterns.env}"

repo=""
pr=""
strategy=""
delete_branch=0
dry_run=0
while [ $# -gt 0 ]; do
    case "$1" in
        --squash|--merge|--rebase)
            [ -z "$strategy" ] || die 2 "стратегия мержа задана дважды: \
$strategy и $1"
            strategy="$1"; shift ;;
        --delete-branch) delete_branch=1; shift ;;
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
# Дефолт — squash: домашняя практика флота (git-workflow). Список стратегий
# закрытый: всё, что не из него, отсекается разбором аргументов выше, и в
# `gh pr merge` не попадает ни одного флага, которого здесь не написано.
if [ -z "$strategy" ]; then
    strategy="--squash"
fi

# --- Формы веток одобрения: выводятся из SSOT-шаблона ----------------------
# Файл ПАРСИТСЯ, не исполняется (тот же приём, что harness.env в
# review-pr.sh). Отсутствие файла или ключа — отказ, а не вшитый дефолт:
# молчаливый дефолт и был бы тем вторым определением имён, которое
# разъезжается.
[ -f "$APPROVAL_PATTERNS" ] \
    || die 2 "нет SSOT имён веток одобрения: $APPROVAL_PATTERNS"
candidate_template=$(sed -n \
    's/^[[:space:]]*APPROVAL_CANDIDATE_TEMPLATE=//p' "$APPROVAL_PATTERNS" \
    | tail -1)
finalize_suffix=$(sed -n \
    's/^[[:space:]]*APPROVAL_FINALIZE_SUFFIX=//p' "$APPROVAL_PATTERNS" \
    | tail -1)
[ -n "$candidate_template" ] \
    || die 2 "в $APPROVAL_PATTERNS нет APPROVAL_CANDIDATE_TEMPLATE"
[ -n "$finalize_suffix" ] \
    || die 2 "в $APPROVAL_PATTERNS нет APPROVAL_FINALIZE_SUFFIX"
# Глоб = шаблон, в котором каждый плейсхолдер заменён на `*`. Ровно та же
# операция на python-стороне (approval_branches.candidate_glob).
candidate_glob=$(printf '%s\n' "$candidate_template" \
    | sed 's/{[A-Za-z_][A-Za-z0-9_]*}/*/g')
finalize_glob="$candidate_glob$finalize_suffix"

# --- Репо и slug -----------------------------------------------------------
repo_dir="$FLEET_ROOT/$repo"
[ -d "$repo_dir" ] || die 2 "репо '$repo' не найдено в $FLEET_ROOT"
# Slug — из СЫРОГО remote.origin.url (git config, не `remote get-url`):
# get-url применяет insteadOf-переписывания, и локальное зеркало подменило
# бы owner/name. Не-GitHub origin — отказ, а не догадка.
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

# --- Профиль ---------------------------------------------------------------
# Preflight ДО чтения PR: не тот профиль — мержить нельзя ничего, и узнать
# об этом надо раньше, чем что-либо решено про сам PR.
[ -d "$MERGE_GH_CONFIG_DIR" ] || die 2 "профиля агента нет: \
$MERGE_GH_CONFIG_DIR — выполните:
  GH_CONFIG_DIR=\"$MERGE_GH_CONFIG_DIR\" gh auth login --hostname github.com --web"
if ! login=$(gh_a api user --jq .login 2>&1); then
    die 2 "профиль агента не отвечает: $login"
fi
[ "$login" = "$AGENT_LOGIN" ] || die 2 "профиль отдаёт логин '$login', \
ожидался '$AGENT_LOGIN' — merge-pr.sh выполняет только агентский мерж; \
человеческий мерж делается человеком, не этой обвязкой"

# --- Факты PR --------------------------------------------------------------
# Одним вызовом ровно те четыре факта, на которых стоят гварды. Формат
# вывода — построчный: три скаляра, затем по строке на имя лейбла (имя
# лейбла GitHub перевода строки не содержит, а пробелы содержать может —
# поэтому строки, а не одно поле).
if ! pr_facts=$(gh_a pr view "$pr" --repo "$slug" \
    --json headRefName,headRefOid,labels,state \
    --jq '.headRefName, .headRefOid, .state, (.labels[]?.name)' 2>&1); then
    die 2 "не удалось прочитать факты PR ${slug}#${pr}: $pr_facts"
fi
head_ref=$(printf '%s\n' "$pr_facts" | sed -n '1p')
head_oid=$(printf '%s\n' "$pr_facts" | sed -n '2p')
state=$(printf '%s\n' "$pr_facts" | sed -n '3p')
labels=$(printf '%s\n' "$pr_facts" | sed -n '4,$p')

# Fail-closed на КАЖДОМ факте: пусто и `null` (jq отдаёт его на
# отсутствующем поле) — это «факт не получен», а не «факта нет».
for _pair in "headRefName:$head_ref" "headRefOid:$head_oid" "state:$state"; do
    _name="${_pair%%:*}"
    _value="${_pair#*:}"
    [ -n "$_value" ] && [ "$_value" != "null" ] \
        || die 2 "факт $_name PR ${slug}#${pr} не получен ('$_value') — \
мерж не выполняется"
done
# Голова обязана быть похожа на sha: мержим ПРОВЕРЕННЫЙ oid, и подсунуть
# в --match-head-commit неразобранный мусор значило бы проверить не то.
case "$head_oid" in
    *[!0-9a-f]*) die 2 "headRefOid '$head_oid' не похож на sha — \
факты не разобраны, мерж не выполняется" ;;
esac
[ "${#head_oid}" -ge 7 ] || die 2 "headRefOid '$head_oid' короче sha — \
факты не разобраны, мерж не выполняется"
[ "$state" = "OPEN" ] || die 2 "PR ${slug}#${pr} не открыт (state=$state)"

# --- Гварды ----------------------------------------------------------------
# Порядок — от частного к общему: финализирующая ветка удовлетворяет и форме
# candidate (она candidate плюс суффикс), поэтому спрашивается первой, иначе
# диагностика назвала бы не ту фазу. Для решения «мержить или нет» разницы
# нет: запрещены обе.
#
# Глоб в `case` НЕ закавычен намеренно (SC2254): нам нужно именно сопоставление
# по образцу, а не буквальное равенство — форма ветки, а не одно её имя.
# shellcheck disable=SC2254
case "$head_ref" in
    $finalize_glob)
        die 3 "PR ${slug}#${pr}: ветка '$head_ref' — финализирующий PR шага \
одобрения (§I12). Его мерж записывает подпись одобрения и обязан быть \
человеческим: агентский merger вне allowlist подпись не создаст. Мержит \
человек." ;;
esac
# shellcheck disable=SC2254
case "$head_ref" in
    $candidate_glob)
        die 3 "PR ${slug}#${pr}: ветка '$head_ref' — candidate-PR шага \
одобрения (§I12). Его мерж человеком из allowlist И ЕСТЬ акт одобрения \
узлов; агентский мерж сжёг бы заявку впустую. Мержит человек." ;;
esac
_saved_ifs="$IFS"
IFS='
'
for _label in $labels; do
    if [ "$_label" = "$HUMAN_MERGE_LABEL" ]; then
        IFS="$_saved_ifs"
        die 3 "PR ${slug}#${pr}: лейбл '$HUMAN_MERGE_LABEL' — PR явно \
оставлен человеку. Мержит человек."
    fi
done
IFS="$_saved_ifs"

# --- Мерж ------------------------------------------------------------------
# `--match-head-commit` — и есть «мержим ПРОВЕРЕННОЕ»: между чтением фактов
# и вызовом мержа в ветку могли запушить, и тогда гварды проверяли не тот
# код. Отдаём форджe тот oid, который проверяли, и пусть она откажет при
# расхождении — сверять вторым запросом бессмысленно, гонка осталась бы.
set -- pr merge "$pr" --repo "$slug" "$strategy" --match-head-commit "$head_oid"
[ "$delete_branch" -eq 0 ] || set -- "$@" --delete-branch
if [ "$dry_run" -eq 1 ]; then
    echo "dry-run: GH_CONFIG_DIR=$MERGE_GH_CONFIG_DIR gh $*"
    echo "гварды пройдены: ветка '$head_ref', голова $head_oid, от $login"
    exit 0
fi
if ! merge_err=$(gh_a "$@" 2>&1); then
    echo "$merge_err" >&2
    die 4 "мерж ${slug}#${pr} отклонён (голова на проверке: $head_oid)"
fi
[ -z "$merge_err" ] || echo "$merge_err"
echo "смержено: ${slug}#${pr} ($strategy, ветка '$head_ref', \
голова $head_oid) от $login"
