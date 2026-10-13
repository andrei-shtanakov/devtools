# df-trust workflow template

`.github/workflows/epochs.yml` проверяет PR в журнал эпох `epochs.yaml`
(`governance/act_epochs.py --check-transition`): допустим только атомарный
переход (закрыть последнюю эпоху + добавить следующую) либо корректный
bootstrap; `epochs[0].anchor` всегда обязан равняться `BOOTSTRAP_ACTIVATION`
из `source.env`. Файлы читаются из закоммиченного HEAD (`git show`), не из
рабочего дерева; симлинк/не-обычный файл — отказ.

Перед использованием замените `DEVTOOLS_REPO` и `DEVTOOLS_REF` (полный SHA
релиза devtools с Task 10a). CLI запускается из checkout devtools по SHA в
доверенной форме (`uv run --frozen --exact --no-config --no-env-file`,
`python -I`, версия uv закреплена `UV_VERSION`); дерево PR (`df-trust/`)
передаётся только как `--repo`.

Коды выхода: 0 допустимо, 1 недопустимый переход/bootstrap/нечитаемый вход,
2 `BASE_SHA` не коммит либо ошибка аргументов. Если `github.event.before`
нулевой (создание ветки), fetch падает — проверка отказывает (fail-closed).

## Обязательная защита (иначе проверка только советует)

На `pull_request` GitHub берёт файл workflow из самого PR: PR может переписать
`epochs.yml`, `DEVTOOLS_REF` или заменить шаг на `true`. Поэтому в df-trust:

1. job с контекстом `check-transition` (имя job; `epochs / check-transition` — лишь подпись в UI) — **required status check** в ruleset
   дефолтной ветки с «Require branches to be up to date before merging»
   (иначе проверка может идти против устаревшего base);
2. по умолчанию (один человек): в df-trust пишут только люди (у агентов
   записи нет, ruleset — только PR); мержащий человек **обязан** сам
   ревьюить любой PR, трогающий `/.github/`, `/source.env`, `/epochs.yaml`,
   особенно PR из форков: в публичном репо открыть PR может кто угодно (в т.ч.
   machine user), workflow такого PR исполняется на `pull_request`, поэтому
   зелёный CI для него недостаточен (CI `source.env` сам не охраняет, кроме
   якоря эпохи 1). Усиление — явным решением (спека B §6.2): при >=2 людях
   `CODEOWNERS` на эти пути и «Require review from Code Owners»; число
   `POLICY_ADMINS` его автоматически не включает. Как обязательное правило
   для одного человека оно заблокировало бы bootstrap и каждую правку журнала;
3. триггер включает `edited`, чтобы смена base у PR перезапускала проверку.

Весь шаблон — authority-root в devtools (`templates/df-trust/`).
