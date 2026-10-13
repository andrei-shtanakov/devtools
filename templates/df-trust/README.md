# df-trust workflow template

`.github/workflows/epochs.yml` проверяет PR в журнал эпох `epochs.yaml`
(`governance/act_epochs.py --check-transition`). Перед использованием замените
`DEVTOOLS_REPO` и `DEVTOOLS_REF` (полный SHA релиза devtools с Task 10a).
Коды выхода: 0 допустимо, 1 недопустимый переход/bootstrap, 2 `BASE_SHA` не
коммит либо ошибка аргументов. Если `github.event.before` нулевой (создание
ветки), fetch падает — проверка отказывает (fail-closed).
