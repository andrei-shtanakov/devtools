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
2. `CODEOWNERS`: `/.github/`, `/source.env` и `/epochs.yaml` — `@<human>`, и
   «Require review from Code Owners»: workflow и файлы корня доверия ревьюит
   человек (CI `source.env` сам не охраняет, кроме якоря эпохи 1);
3. триггер включает `edited`, чтобы смена base у PR перезапускала проверку.

Весь шаблон — authority-root в devtools (`templates/df-trust/`).
