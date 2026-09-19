#!/bin/sh
# attest-vendor.sh — детерминированная аттестация целостности вендор-копии
# review-kit на волновом PR ре-вендора (решение владельца 2026-09-19).
#
# Зачем: волновой PR ре-вендора кита — побайтовая копия файлов из апстрима
# (steward), содержательного ревью там нет и быть не должно. Но правило
# ветки требует одобряющего ревью, а автор PR — учётка оператора, которая
# не может одобрить сама себя.
#
# ПРИНЦИП (ревью 6af24ee, фикс-заход; уточнение 1ce441b..HEAD, решение
# владельца): инструмент публикует одобрение от имени машины, значит НИ
# ОДИН факт, определяющий исход, не приходит из дерева проверяемого PR —
# ни перечень файлов, ни инвентарь, ни код чекера, ни текст, попадающий в
# тело. Всё доверенное — из апстрима (steward) и из GitHub API
# (baseRefName/headRefOid/state/reviews), а не из содержимого самого
# PR-диффа.
#
# Конкретно это значит:
#   - SOURCE-коммит из PIN головы (`upstream_sha`) — только САНИТАРНАЯ
#     проверка провенанса: обязан существовать и быть ПРЕДКОМ доверенной
#     ветки steward (origin/master), а не произвольным/чужим/выдуманным
#     коммитом. Дальше он в сверке НЕ участвует;
#   - и инвентарь (checksum.sh), и побайтовая/режимная сверка идут против
#     ТЕКУЩЕГО $STEWARD_TRUSTED_REF steward, а не против названного в PIN
#     коммита. Так откат на старый (но всё ещё валидный) коммит steward
#     закрывается ПО СУЩЕСТВУ — содержимое той эпохи просто не совпадёт с
#     сегодняшним апстримом — а не слежкой за направлением истории; PR,
#     не менявший кит, остаётся валиден при любом движении мастера
#     steward, а ре-вендор, отставший от реального изменения кита,
#     обязан покраснеть. Побочный эффект назван явно: пока волна едет,
#     движение кита в steward делает уже открытые PR волны недействи-
#     тельными до повторного прогона — это осознанное свойство, не дефект;
#   - инвентарь и полноту PIN проверяет checksum.sh, ИЗВЛЕЧЁННЫЙ ИЗ
#     ТЕКУЩЕГО АПСТРИМА и исполненный против дерева головы — не checksum.sh
#     из головы PR (тот же чекер, который PR мог ослабить);
#   - PR обязан целиком лежать внутри состава кита (диапазон base..head) —
#     иначе аттестация одобрила бы посторонний диф;
#   - тело называет ЧЕСТНОЕ число — N из M членов апстрима, где M читается
#     из ДОВЕРЕННОГО (апстримного) инвентаря, а не подгоняется под то, что
#     нашлось в PIN головы: легально отсутствующий переходный член
#     (`?path`, двухшаговый ре-вендор состава кита) называется поимённо, а
#     не растворяется в формулировке «все совпали»;
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
    echo "  ОГРАНИЧЕНИЕ: аттестуется только PR, состоящий РОВНО из членов" >&2
    echo "  кита и его PIN. PR, везущий что-то ещё — например" >&2
    echo "  .github/codex/review-prompt.md, который намеренно вне инвентаря" >&2
    echo "  (репо-данные, не член кита), — получит отказ кодом 3. Это не" >&2
    echo "  дефект: одобрять весь PR, проверив только кит, нельзя. Такой PR" >&2
    echo "  остаётся предметом обычного ревью." >&2
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
# Хвостовой \r (чекаут с autocrlf) срезается везде, где читаются строки
# PIN — как в апстримном checksum.sh (тот же класс правки: зелёный чекер и
# красный аттестатор не должны расходиться на одном и том же чекауте).
cr=$(printf '\r')

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

# --- SOURCE из PIN головы ---------------------------------------------------
pin_rel="scripts/review/PIN"
pin_path="$head_tree/$pin_rel"
[ ! -L "$pin_path" ] \
    || die 2 "PIN — симлинк, а обязан быть обычным файлом: $pin_rel"
[ -f "$pin_path" ] || die 2 "в голове PR ${slug}#${pr} нет $pin_rel"
[ -r "$pin_path" ] || die 2 "$pin_rel в голове PR нечитаем"

# Извлекает SHA (возможно, СОКРАЩЁННЫЙ) апстрима из строки "# SOURCE:
# steward @ <sha> ..." файла $1 в parsed_sha; $2 — человекочитаемое имя
# источника для сообщений об ошибке. Формат во флоте — сокращённый SHA (7
# hex, как `git log --oneline`; боевые примеры: devtools a2d7e71, maestro
# 1634af7): полный 40-hex здесь НЕ требуется — единственность и
# существование коммита проверяются НИЖЕ, разрешением против доверенного
# steward (`git rev-parse --verify`), а не строковой длиной. Git сам
# откажет на неоднозначном префиксе и на несуществующем коммите — строгость
# не теряется, просто переносится с формата на факт.
parse_source_sha() {
    _pss_line=$(grep -m1 '^# SOURCE: steward @ ' "$1") || \
        die 2 "в $2 нет строки '# SOURCE: steward @ <sha>' — заявленный \
апстрим не назван"
    _pss_line=${_pss_line%"$cr"}
    parsed_sha="${_pss_line#'# SOURCE: steward @ '}"
    parsed_sha="${parsed_sha%% *}"
    case "$parsed_sha" in
        ''|*[!0-9a-f]*) die 2 "SHA апстрима не hex, получено из \
SOURCE-строки '$_pss_line' ($2): '$parsed_sha'" ;;
    esac
}
parse_source_sha "$pin_path" "$pin_rel в голове PR"
upstream_sha_raw="$parsed_sha"

# --- I1: провенанс SOURCE обязан быть вменяемым — резолвиться в ЕДИНСТВЕННЫЙ
# коммит И быть предком доверенной ветки steward. Дальше резолвленный
# upstream_sha в сверке НЕ используется (решение владельца): именованный
# коммит — это только санитарная проверка происхождения («это настоящий, не
# выдуманный и не посторонний коммит steward»), а не якорь сравнения. Сверка
# ниже идёт против ТЕКУЩЕГО $STEWARD_TRUSTED_REF — так откат закрывается ПО
# СУЩЕСТВУ (старое содержимое просто не совпадёт с нынешним апстримом), не
# слежкой за направлением движения истории: PR, назвавший прошлогодний
# коммит, но фактически везущий байты, совпадающие с сегодняшним апстримом,
# легален; PR, совпадающий только со своим устаревшим SOURCE, — нет.
# Побочный эффект, названный явно: пока волна ре-вендора едет, движение
# кита в steward делает уже открытые PR волны недействительными до
# обновления — это осознанное свойство правила, не дефект.
if ! fetch_err=$(git -C "$steward_dir" fetch -q origin 2>&1); then
    echo "$fetch_err" >&2
    die 2 "не удалось освежить steward (git fetch origin) — без свежей \
базы ancestry-проверке и сверке с апстримом доверять нельзя."
fi
if ! upstream_sha=$(git -C "$steward_dir" rev-parse --verify \
    "${upstream_sha_raw}^{commit}" 2>"$work/resolve-source.err"); then
    cat "$work/resolve-source.err" >&2
    die 2 "SHA апстрима '$upstream_sha_raw' из $pin_rel в голове PR не \
резолвится в единственный коммит steward (неизвестен или неоднозначен \
после fetch) — провенанс не подтверждён"
fi
if ! git -C "$steward_dir" merge-base --is-ancestor \
    "$upstream_sha" "$STEWARD_TRUSTED_REF"; then
    die 3 "steward @ $upstream_sha НЕ является предком доверенной ветки \
$STEWARD_TRUSTED_REF — заявленный апстрим вне доверенной истории, \
аттестация не публикуется."
fi
# Коммит, с которым реально идёт сверка — ПИННУЕТСЯ здесь же (не просто имя
# ветки): фетч и разрешение $STEWARD_TRUSTED_REF в одну и ту же строку —
# извлечение checksum.sh и ls-tree ниже обязаны видеть ОДНО и то же
# состояние апстрима, а не ветку, которая теоретически могла бы уехать
# между двумя git-командами.
current_upstream_sha=$(git -C "$steward_dir" rev-parse \
    "$STEWARD_TRUSTED_REF") \
    || die 2 "не удалось разрешить $STEWARD_TRUSTED_REF в steward"

# Фетч базы PR нужен для I2 (диапазон диффа) ниже.
if ! fetch_err=$(git -C "$repo_dir" fetch -q origin \
    "+refs/heads/$base_ref:refs/remotes/origin/$base_ref" 2>&1); then
    echo "$fetch_err" >&2
    die 2 "не удалось освежить базу origin/$base_ref"
fi

# --- Инвентарь и полнота PIN: checksum.sh ИЗ ТЕКУЩЕГО АПСТРИМА -------------
# Код из PR не исполняется никогда: чекер извлекается из steward на
# $current_upstream_sha (пиненный $STEWARD_TRUSTED_REF, НЕ upstream_sha из
# PIN — решение владельца, см. комментарий у I1 выше) и запускается против
# дерева головы. Так подмена/сужение head-копии checksum.sh (и вычищенные
# под неё строки PIN) больше не может ослабить проверку полноты — инвентарь
# и пороги берутся из НАСТОЯЩЕГО, ТЕКУЩЕГО checksum.sh, а не из версии,
# которую мог отредактировать автор PR, и не из версии на момент, когда кит
# вендорился.
upstream_checksum="$work/upstream-checksum.sh"
if ! git -C "$steward_dir" show \
    "${current_upstream_sha}:scripts/review/checksum.sh" \
    > "$upstream_checksum" 2>"$work/checksum-extract.err"; then
    cat "$work/checksum-extract.err" >&2
    die 2 "не удалось извлечь scripts/review/checksum.sh из апстрима \
steward @ $current_upstream_sha ($STEWARD_TRUSTED_REF)"
fi
# CHECKSUM_KIT_EXTRA — операторский хук ДОБАВЛЕНИЯ членов инвентаря (см.
# заголовок апстримного checksum.sh); явно не наследуем из окружения
# аттестатора, иначе введение в теле (M-раздел ниже) описывало бы не тот
# инвентарь, что реально применялся.
unset CHECKSUM_KIT_EXTRA || true
set +e
checksum_out=$(cd "$head_tree" && sh "$upstream_checksum" \
    --pin "$pin_rel" 2>&1)
checksum_code=$?
set -e
# Вывод — только в stderr, для оператора. В тело НЕ попадает никогда, даже
# при коде 0: это защита C2, а не только «убрать грязь при отказе».
echo "$checksum_out" >&2
if [ "$checksum_code" -ne 0 ]; then
    die 3 "checksum.sh (апстрим steward @ $current_upstream_sha, \
$STEWARD_TRUSTED_REF) --pin вернул $checksum_code — сверка НЕ прошла, \
аттестация не публикуется."
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
# Хвостовой \r срезается ровно как в апстримном checksum.sh (:152-156):
# чекаут с autocrlf не должен краснить валидную копию. Без этого пути в
# members.list получают \r, сверка I2 ниже не находит совпадений, и оператор
# получает ложное обвинение «PR трогает пути вне состава кита» на побайтово
# корректной копии — то есть зелёный апстримный чекер и красный аттестатор
# расходились бы ровно на этом входе. $cr задан один раз ближе к шапке.
while IFS= read -r line || [ -n "$line" ]; do
    line=${line%"$cr"}
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

# --- Честное число для тела: N из M членов апстрима ------------------------
# required_kit_default читается из УЖЕ ИЗВЛЕЧЁННОГО апстримного checksum.sh
# (доверенный текст, ancestry уже проверен выше) — единственный способ
# узнать ПОЛНЫЙ инвентарь, не исполняя чужой скрипт с произвольными
# аргументами и не паря его как код. Без этого шага тело говорило бы
# «сверено N член(ов), все совпали» и в случае, когда апстримный checksum.sh
# зелёный лишь потому, что отсутствующий ПЕРЕХОДНЫЙ член (`?path`) легален
# без строки PIN (двухшаговый ре-вендор состава кита, devtools#228) — то
# есть заявляло бы больше, чем доказано.
inv_line=$(grep -m1 '^required_kit_default=' "$upstream_checksum") \
    || die 2 "не удалось определить полный инвентарь апстрима — нет \
required_kit_default в checksum.sh steward @ $current_upstream_sha"
inv_tokens="${inv_line#required_kit_default=\"}"
inv_tokens="${inv_tokens%\"}"
case "$inv_tokens" in
    *'"'*) die 2 "неожиданный формат required_kit_default в checksum.sh \
апстрима — не удалось разобрать полный инвентарь" ;;
esac
total_members=0
missing_optional=""
set -f
for tok in $inv_tokens; do
    total_members=$((total_members + 1))
    case "$tok" in
        '?'*)
            bare="${tok#?}"
            grep -qxF "$bare" "$members_file" || missing_optional="\
$missing_optional
  $bare"
            ;;
    esac
done
set +f
[ "$total_members" -gt 0 ] || die 2 "апстримный инвентарь пуст — \
required_kit_default в checksum.sh не назвал ни одного члена"
# Дедуп: дублирующая строка PIN не должна раздувать число в теле — кит
# считает её отдельным «проверенным» файлом, тело обязано быть честнее.
distinct_compared=$(sort -u "$members_file" | wc -l | tr -d ' ')

# --- I2: PR обязан целиком лежать внутри состава кита -----------------------
# Аттестация существует только для PR, целиком состоящих из ре-вендора
# кита: посторонний путь в диапазоне base..head одобрялся бы молча. База
# origin/$base_ref уже освежена выше (для чтения её PIN) — второй fetch не
# нужен.
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

# --- Ядро: побайтовая и режимная сверка каждого члена с ТЕКУЩИМ апстримом -
# По git-объектам (ls-tree: mode + blob-oid), не по чекауту: совпавший
# blob-oid гарантирует побайтовое совпадение контента (git — content-
# addressable), а mode ловит и потерянный/лишний бит исполнения (PIN,
# требующий 100755 на harness-claude), и подмену обычного файла симлинком
# (mode 120000 не совпадёт ни с одним ожидаемым режимом кита) — без
# отдельной проверки каждого компонента пути на симлинк, которая нужна
# только при чтении с диска.
#
# Хеш из строки PIN здесь не используется: он уже проверен checksum.sh
# (апстримным, шагом выше). Сверка идёт против $current_upstream_sha —
# ТЕКУЩЕГО апстрима, НЕ upstream_sha из PIN (решение владельца, см. I1
# выше): так подмена PIN вместе с подменёнными файлами (перед которой
# checksum.sh бессилен, он сверяет копию с её же PIN) остаётся видна, И
# заодно откат на старый, но валидный коммит steward закрывается по
# существу — старое содержимое просто не совпадёт с сегодняшним апстримом,
# а не отдельной проверкой направления истории.
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

    if ! tree_entry "$current_upstream_sha" "$path" "$steward_dir"; then
        mismatched=$((mismatched + 1))
        mismatch_report="$mismatch_report
  $path — отсутствует в текущем апстриме steward @ $current_upstream_sha"
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
разошлись с ТЕКУЩИМ steward @ $current_upstream_sha ($STEWARD_TRUSTED_REF):\
$mismatch_report" >&2
    die 3 "аттестация не публикуется — вендор-копия не доказана как копия \
ТЕКУЩЕГО апстрима ($STEWARD_TRUSTED_REF steward); заявленный в PIN SOURCE \
коммит здесь не при чём — сверка всегда идёт с сегодняшним апстримом."
fi

# --- Тело аттестации: ТОЛЬКО наш собственный вычисленный текст (C2) --------
# Ни один символ, взятый из PIN/дифа/чужого (даже апстримного) вывода
# checksum.sh, в тело не попадает — только числа и статические
# формулировки, вычисленные этим скриптом. Путь к этому — сознательный:
# даже правильные с виду имена файлов из дерева PR не копируются в тело.
# Исключение — имена ПЕРЕХОДНЫХ членов ниже: они читаются из ДОВЕРЕННОГО
# (ancestry-проверенного) апстримного checksum.sh, не из PR, и поэтому не
# нарушают этот принцип — та же категория факта, что upstream_sha.
marker="<!-- $MARKER_NAME version=1 kind=review-kit head=$head_sha \
upstream=$current_upstream_sha -->"
{
    echo "## Vendor-copy integrity attestation — review-kit"
    echo
    echo "- PR: ${slug}#${pr}, head \`$head_sha\`"
    echo "- заявленный апстрим (из шапки $pin_rel: \`$upstream_sha_raw\`," \
        "резолвлен в единственный коммит steward): steward @" \
        "\`$upstream_sha\`, подтверждён как предок" \
        "\`$STEWARD_TRUSTED_REF\` steward (санитарная проверка" \
        "провенанса, не якорь сверки)"
    echo "- сверка идёт с ТЕКУЩИМ апстримом: steward @" \
        "\`$current_upstream_sha\` (\`$STEWARD_TRUSTED_REF\` на момент" \
        "прогона) — движение кита в steward после этого прогона делает" \
        "аттестацию неактуальной до повторного прогона"
    echo "- инвентарь и полнота PIN сверены исполнением checksum.sh," \
        "ИЗВЛЕЧЁННОГО ИЗ ТЕКУЩЕГО АПСТРИМА (не из дерева PR и не из" \
        "коммита SOURCE), против PIN и дерева головы: код 0"
    echo "- диапазон PR (\`${base_ref}\`..head) целиком лежит внутри" \
        "состава кита — посторонних путей нет"
    echo "- побайтовая и режимная сверка по git-объектам (не по чекауту)" \
        "с ТЕКУЩИМ апстримом:" \
        "$distinct_compared из $total_members член(ов) апстрима" \
        "присутствуют в этой копии, все присутствующие совпали"
    if [ -n "$missing_optional" ]; then
        echo "- переходные члены апстрима, отсутствующие в этой копии" \
            "(легально для промежуточного состояния двухшаговой раскатки" \
            "состава кита — сверка их не покрывает):$missing_optional"
    fi
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
