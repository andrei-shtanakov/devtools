# approval_branches.sh — формы веток одобрения (§I12) из SSOT-шаблона.
# Подключать `. "$script_dir/approval_branches.sh"` ПОСЛЕ ssot_env.sh и `die`.
# Читатели: merge-pr.sh (гвард «candidate мержит только человек»),
# human-merge.sh (пин политики обязателен только у candidate). Второе
# определение вывода глоба разошлось бы молча — поэтому файл один, и он
# authority-root + харнесс-путь, как его читатели.
#
# approval_globs <patterns-file> — печатает две строки: candidate-глоб и
# finalize-глоб. Файл ПАРСИТСЯ, не исполняется. Вызывать ТОЛЬКО как
# `globs=$(approval_globs …) || exit $?`.
#
# Глоб ФОРМЫ имени — два шага, дословно те же, что в
# approval_branches._template_glob:
#   1. каждый плейсхолдер → `*`;
#   2. соседние `*`, разделённые одним разделителем, схлопываются — до
#      неподвижной точки (ветка ПРЕЖНЕЙ арности `<W>-<K>` не пройдёт мимо).
approval_globs() {
    _patterns="$1"
    _what="SSOT имён веток одобрения"
    _template=$(ssot_key "$_patterns" APPROVAL_CANDIDATE_TEMPLATE "$_what") || exit $?
    _suffix=$(ssot_key "$_patterns" APPROVAL_FINALIZE_SUFFIX "$_what") || exit $?
    _glob=$(printf '%s\n' "$_template" | sed 's/{[A-Za-z_][A-Za-z0-9_]*}/*/g')
    while :; do
        _collapsed=$(printf '%s\n' "$_glob" | sed 's/\*[-._]\*/*/g')
        if [ "$_collapsed" = "$_glob" ]; then
            break
        fi
        _glob="$_collapsed"
    done
    printf '%s\n%s\n' "$_glob" "$_glob$_suffix"
}
