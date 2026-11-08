# df_config.sh — shell-доступ к конфигу DarkFactory
# ($XDG_CONFIG_HOME/darkfactory/config.toml, дизайн darkfactory-config §5.1).
# Формат и проверки — ТОЛЬКО в governance/df_config.py (один источник правды);
# здесь лишь вызов его CLI. Подключать: `. "$script_dir/df_config.sh"` — нужен
# `script_dir` (корень devtools).
#
# Вызывать ТОЛЬКО как `v=$(df_cfg <ключ>) || exit $?` (как `ssot_key`): отказ
# загрузчика (код 2, сообщение с файлом и ключом в stderr) без проверки статуса
# дал бы пустое значение — fail-open. Ключи — `get_value` в df_config.py.
df_cfg() {
    if [ -z "${script_dir:-}" ]; then
        echo "df_cfg: script_dir не задан — подключать из скрипта devtools" >&2
        return 2
    fi
    python3 -I "$script_dir/governance/df_config.py" get "$1"
}
