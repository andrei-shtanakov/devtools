#!/usr/bin/env bash
# Правка приёмочного roadmap.toml песочницы ПОЛНОМОЧИЯМИ ВЛАДЕЛЬЦА (спека среза 1, §9.1).
# Запускает владелец на своей машине своей git-учёткой — не conductor и не App.
#   deploy/conductor/acceptance.sh <owner>/conductor-sandbox <autonomy 0..3> <action,action | ->
set -euo pipefail
REPO="${1:?<owner>/conductor-sandbox}"
AUTONOMY="${2:?autonomy 0..3}"
ACTIONS="${3:?список действий через запятую или -}"
case "$REPO" in */conductor-sandbox) ;; *) echo "только <owner>/conductor-sandbox" >&2; exit 2 ;; esac
case "$AUTONOMY" in [0-3]) ;; *) echo "autonomy 0..3" >&2; exit 2 ;; esac
LIST=""
if [ "$ACTIONS" != "-" ]; then
    for a in ${ACTIONS//,/ }; do
        case "$a" in
            owner_queue | notify_satisfied | nudge | pr_nudge | close_shipped) ;;
            *) echo "неизвестное действие: $a" >&2; exit 2 ;;
        esac
        LIST="$LIST\"$a\", "
    done
    LIST="${LIST%, }"
fi
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git clone -q "https://github.com/$REPO.git" "$TMP/s"
python3 - "$TMP/s/roadmap.toml" "$AUTONOMY" "$LIST" <<'PY'
import re
import sys

path, autonomy, items = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(path, encoding="utf-8").read()
text = re.sub(r"(?m)^autonomy\s*=.*$", f"autonomy = {autonomy}", text, count=1)
line = f"enabled_actions = [{items}]"
if re.search(r"(?m)^enabled_actions\s*=", text):
    text = re.sub(r"(?m)^enabled_actions\s*=.*$", line, text, count=1)
else:
    text = text.replace(f"autonomy = {autonomy}", f"autonomy = {autonomy}\n{line}", 1)
open(path, "w", encoding="utf-8").write(text)
PY
git -C "$TMP/s" commit -qam "acceptance: autonomy=$AUTONOMY enabled_actions=[$LIST]"
git -C "$TMP/s" push -q origin HEAD
echo "опубликовано: autonomy=$AUTONOMY enabled_actions=[$LIST]"
