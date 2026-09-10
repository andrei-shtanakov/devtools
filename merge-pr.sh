#!/bin/sh
# merge-pr.sh — единственный разрешённый путь АГЕНТСКОГО мержа PR.
#
# Зачем: по ADR-ECO-011 («DarkFactory») агент мержит сам, от профиля
# ai-prosto. Пока мерж был голым вызовом — `gh pr merge` руками у оператора,
# `gh api -X PUT …/merge` из `Ops.merge` — не существовало места, куда
# поставить проверку и накрыть её тестом, а живых путей было три. Теперь
# путь один: и оператор, и `accept-pr`, и S7 раннера мержат отсюда
# (`Ops.merge` вызывает этот скрипт). Человеческий мерж этой обвязкой не
# выполняется — она отказывает, если активный профиль не агентский.
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
# шаблона механически — так он не может отстать от строителя имени. Две
# независимо написанные строки разъезжаются молча, и уже разъехались дважды.
#
# Отношение к governance/merge_gate.py::decide(): не дубль. `decide()`
# решает, имеет ли ПРОГОН право мержить (authority/safety/review/facts) и в
# сеть не ходит. Здесь — механический последний рубеж на самом вызове:
# отказы по фактам PR, которые не отменяются ничьей зеленью.
#
# Имя ветки — АДРЕС, не идентичность. Единственное решение, принимаемое
# здесь по имени, — ОТКАЗ (гварды 1 и 2), и это осознанная асимметрия:
# отказать по форме имени можно лишь слишком часто, никогда — слишком редко
# для того, чью форму опознали. Утверждать по имени «это наш PR» запрещено
# (§I12), и обвязка этого не делает: всё, чем она РАЗРЕШАЕТ мерж, — факты
# (`state`, `headRefOid`, `baseRefOid`, метки), а не имя. Идентичность
# approval-PR несёт метка `human-merge-required`, которую §I12 обязывает
# ставить в самом вызове создания; два признака независимы намеренно —
# переименованная ветка сохраняет метку, потерянная метка сохраняет форму.
#
# Про БАЗУ — честно, без изображения проверки. Атомарного пина базы у
# форджи нет: merge-API знает `sha` (голова) и не знает базы. Значит любая
# сверка базы остаётся TOCTOU, и вопрос лишь в том, насколько узок остаток.
# Обвязка сама не знает, от какой базы вынесен вердикт (она приходит на PR
# без истории), поэтому база пинуется вызывающим: `--expect-base <sha>`
# сверяется с `baseRefOid`, и окно сужается до промежутка «сверка → мерж».
# Без этого флага база НЕ проверяется, и обвязка об этом не молчит.
# `mergeStateStatus` BEHIND/DIRTY отбивается сам по себе, но заменой
# --expect-base не является: BEHIND сообщается лишь там, где защита ветки
# требует up-to-date.
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
        "[--squash|--merge|--rebase] [--delete-branch]" \
        "[--expect-head <sha>] [--expect-base <sha>] [--dry-run]" >&2
    echo "  <repo> — имя каталога репо во флоте (например dispatcher)" >&2
    echo "  стратегия — только из этого списка, дефолт --squash;" >&2
    echo "    свободного passthrough в gh нет по построению" >&2
    echo "  --delete-branch — удалить ветку origin после мержа" >&2
    echo "  --expect-head — голова, которую видел вызывающий; расхождение" >&2
    echo "    с фактической — отказ (мерж всё равно идёт с пином головы)" >&2
    echo "  --expect-base — база, ОТ КОТОРОЙ вынесен вердикт; расхождение" >&2
    echo "    с baseRefOid — отказ (см. про базу в шапке скрипта)" >&2
    echo "  --dry-run — показать разрешённую команду мержа, не выполняя" >&2
    echo "  --print-globs — показать выведённые формы веток и выйти" >&2
}

die() {
    _code="$1"; shift
    echo "$*" >&2
    exit "$_code"
}

# Профиль мержа — тот же, что у публикации ревью (review-pr.sh): мерж от
# ОСНОВНОГО аккаунта записал бы агентский мерж человеческим, обнулив
# `merged_by` — наблюдаемый различитель agent/human.
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
print_globs=0
expect_head=""
expect_base=""
while [ $# -gt 0 ]; do
    case "$1" in
        --squash|--merge|--rebase)
            [ -z "$strategy" ] || die 2 "стратегия мержа задана дважды: \
$strategy и $1"
            strategy="$1"; shift ;;
        --delete-branch) delete_branch=1; shift ;;
        --expect-head)
            [ $# -ge 2 ] || die 2 "--expect-head требует sha"
            expect_head="$2"; shift 2 ;;
        --expect-base)
            [ $# -ge 2 ] || die 2 "--expect-base требует sha"
            expect_base="$2"; shift 2 ;;
        --dry-run) dry_run=1; shift ;;
        --print-globs) print_globs=1; shift ;;
        -*) usage; exit 2 ;;
        *)
            if [ -z "$repo" ]; then repo="$1"
            elif [ -z "$pr" ]; then pr="$1"
            else usage; exit 2
            fi
            shift ;;
    esac
done
# --print-globs — чистый зонд над SSOT-файлом: ни репо, ни PR ему не нужны.
if [ "$print_globs" -eq 0 ]; then
    if [ -z "$repo" ] || [ -z "$pr" ]; then
        usage
        exit 2
    fi
    case "$pr" in
        *[!0-9]*|"") die 2 "номер PR должен быть числом, получено: '$pr'" ;;
    esac
fi
# Дефолт — squash: домашняя практика флота (git-workflow). Список стратегий
# закрытый: всё, что не из него, отсекается разбором аргументов выше, и в
# merge-API не уходит ни одного `merge_method`, которого здесь не написано.
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
# Глоб ФОРМЫ имени — два шага, дословно те же, что в
# approval_branches._template_glob:
#   1. каждый плейсхолдер → `*`;
#   2. соседние `*`, разделённые одним разделителем, схлопываются — до
#      неподвижной точки.
# Второй шаг существен: без него глоб был бы привязан к сегодняшней арности
# нумерации (`spec/*-approve-*-*-*`), и ветка ПРЕЖНЕЙ формы `<W>-<K>`, без
# номера заявки, прошла бы мимо гварда. Такие ветки могли остаться в
# природе — дыра ради чистоты шаблона дороже, чем лишняя строка вывода.
candidate_glob=$(printf '%s\n' "$candidate_template" \
    | sed 's/{[A-Za-z_][A-Za-z0-9_]*}/*/g')
while :; do
    collapsed=$(printf '%s\n' "$candidate_glob" | sed 's/\*[-._]\*/*/g')
    if [ "$collapsed" = "$candidate_glob" ]; then
        break
    fi
    candidate_glob="$collapsed"
done
finalize_glob="$candidate_glob$finalize_suffix"
if [ "$print_globs" -eq 1 ]; then
    # Отладочный зонд для тестов (приём --print-review-cmd из review-pr.sh):
    # показать выведённые глобы и выйти, не трогая ни репо, ни GitHub. Тест
    # сверяет их с python-половиной — так расхождение вывода ловится прямо,
    # а не только через поведение гварда.
    echo "candidate=$candidate_glob"
    echo "finalize=$finalize_glob"
    exit 0
fi

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
# Одним вызовом ровно те факты, на которых стоят гварды. Формат вывода —
# построчный: скаляры, затем по строке на имя лейбла (имя лейбла GitHub
# перевода строки не содержит, а пробелы содержать может — поэтому строки,
# а не одно поле).
if ! pr_facts=$(gh_a pr view "$pr" --repo "$slug" \
    --json headRefName,headRefOid,baseRefOid,mergeStateStatus,labels,state \
    --jq '.headRefName, .headRefOid, .state, .baseRefOid,
          .mergeStateStatus, (.labels[]?.name)' 2>&1); then
    die 2 "не удалось прочитать факты PR ${slug}#${pr}: $pr_facts"
fi
head_ref=$(printf '%s\n' "$pr_facts" | sed -n '1p')
head_oid=$(printf '%s\n' "$pr_facts" | sed -n '2p')
state=$(printf '%s\n' "$pr_facts" | sed -n '3p')
base_oid=$(printf '%s\n' "$pr_facts" | sed -n '4p')
merge_state=$(printf '%s\n' "$pr_facts" | sed -n '5p')
labels=$(printf '%s\n' "$pr_facts" | sed -n '6,$p')

# Fail-closed на КАЖДОМ факте: пусто и `null` (jq отдаёт его на
# отсутствующем поле) — это «факт не получен», а не «факта нет».
for _pair in "headRefName:$head_ref" "headRefOid:$head_oid" "state:$state" \
    "baseRefOid:$base_oid"; do
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

# --- Пины вызывающего: голова и база ---------------------------------------
# Вызывающий, который уже вынес вердикт (ревью, гард путей), знает, ОТ ЧЕГО
# и ПО ЧЕМУ он его выносил. Обвязка сама этого знать не может: она приходит
# на PR без истории. Поэтому пины — параметры, а не догадка.
if [ -n "$expect_head" ] && [ "$expect_head" != "$head_oid" ]; then
    die 3 "PR ${slug}#${pr}: голова $head_oid, а вызывающий проверял \
$expect_head — вердикт относится не к этому коду. Мерж не выполняется."
fi
if [ -n "$expect_base" ] && [ "$expect_base" != "$base_oid" ]; then
    die 3 "PR ${slug}#${pr}: база $base_oid, а вердикт вынесен от \
$expect_base — в базу приехали изменения, которых ревью не видело. Мерж \
не выполняется; перегоните вердикт."
fi
# Без --expect-base сверять базу не с чем, и обвязка этого НЕ изображает.
# Единственное, что она может сказать про базу сама, — пересказать мнение
# форджи: BEHIND («ветка отстала, репо требует обновления») и DIRTY
# («конфликт»). Это не доказательство свежести базы: BEHIND сообщается лишь
# там, где защита ветки требует up-to-date, а без такой защиты база может
# уехать сколь угодно далеко при `CLEAN`. Настоящий механизм — --expect-base.
case "$merge_state" in
    BEHIND) die 3 "PR ${slug}#${pr}: форджа сообщает BEHIND — база ушла \
вперёд, ветку надо обновить. Мерж не выполняется." ;;
    DIRTY) die 3 "PR ${slug}#${pr}: форджа сообщает DIRTY — конфликт с \
базой. Мерж не выполняется." ;;
esac

# --- Мерж ------------------------------------------------------------------
# Мерж — прямым `PUT /pulls/{n}/merge`, а НЕ `gh pr merge`. Это решение
# дизайна пайплайна (2026-08-30, §8): `gh pr merge` читает
# `mergeStateStatus` сам и при `BLOCKED` отказывает своим текстом («the base
# branch policy prohibits the merge»), ни разу не проверив, есть ли у актора
# bypass. Обвязка обязана добавлять гварды, а не отбирать у мержа основания,
# которые владелец сознательно оставил.
#
# `sha=` — API-аналог `--match-head-commit`, и в нём смысл «мержим
# ПРОВЕРЕННОЕ»: между чтением фактов и вызовом мержа в ветку могли
# запушить, и тогда гварды проверяли не тот код. Отдаём форджe тот oid,
# который проверяли, и пусть она откажет при расхождении — сверять вторым
# запросом бессмысленно, гонка осталась бы.
#
# Стратегия остаётся закрытым allowlist'ом, просто в терминах API.
case "$strategy" in
    --squash) merge_method="squash" ;;
    --merge)  merge_method="merge" ;;
    --rebase) merge_method="rebase" ;;
    *) die 2 "внутренняя ошибка: неизвестная стратегия '$strategy'" ;;
esac
set -- api -X PUT "repos/$slug/pulls/$pr/merge" \
    -f "merge_method=$merge_method" -f "sha=$head_oid"
# Что именно проверено — говорится вслух: «база не пинована» обязано быть
# видно в журнале прогона, иначе отсутствие проверки не отличить от
# пройденной.
if [ -n "$expect_base" ]; then
    base_note="база пинована ($base_oid)"
else
    base_note="база НЕ пинована (нет --expect-base), merge_state=$merge_state"
fi
if [ "$dry_run" -eq 1 ]; then
    echo "dry-run: GH_CONFIG_DIR=$MERGE_GH_CONFIG_DIR gh $*"
    echo "гварды пройдены: ветка '$head_ref', голова $head_oid, \
$base_note, от $login"
    exit 0
fi
if ! merge_err=$(gh_a "$@" 2>&1); then
    echo "$merge_err" >&2
    die 4 "мерж ${slug}#${pr} отклонён (голова на проверке: $head_oid)"
fi
echo "смержено: ${slug}#${pr} ($merge_method, ветка '$head_ref', \
голова $head_oid, $base_note) от $login"
# Удаление ветки — ОТДЕЛЬНЫЙ вызов: у merge-API его нет (это был флаг
# `gh pr merge -d`). Неудача здесь — предупреждение, а не провал команды:
# мерж уже состоялся, и отдать ненулевой код значило бы соврать про него.
if [ "$delete_branch" -eq 1 ]; then
    if del_err=$(gh_a api -X DELETE \
        "repos/$slug/git/refs/heads/$head_ref" 2>&1); then
        echo "ветка '$head_ref' удалена на origin"
    else
        echo "ЗАМЕТКА: ветку '$head_ref' удалить не удалось: $del_err" >&2
    fi
fi
