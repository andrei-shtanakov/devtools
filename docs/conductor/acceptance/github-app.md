# GitHub App conductor — создание, ключ, установка (срез 1, шаги владельца)

Права и место установки — `sandbox.md` п. 2 и спека среза 1
(`docs/superpowers/specs/2026-09-30-conductor-slice-1-design.md`, §4.3, §9).
Здесь — пошагово, что нажать и куда положить. Все шаги делает владелец:
App, ключ и установка — акт владельца, агент их не создаёт.

## 1. Создать App

github.com → аватар → **Settings** → **Developer settings** → **GitHub Apps** →
**New GitHub App** (https://github.com/settings/apps/new).

| поле | значение |
|---|---|
| GitHub App name | любое уникальное, например `conductor-pr0sto`. Имя `conductor` в GitHub может быть занято. conductor берёт логин бота из `GET /app` (`<slug>[bot]`), поэтому имя в коде не зашито |
| Homepage URL | `https://github.com/andrei-shtanakov/devtools` |
| Callback URL, Setup URL | пусто |
| Webhook → Active | **снять галочку**: conductor ходит по расписанию и вебхуки не принимает |
| Where can this GitHub App be installed? | **Only on this account** |

**Repository permissions** — ровно эти, остальное `No access`:

| право | уровень | зачем |
|---|---|---|
| Issues | Read and write | очередь владельца, напоминания, комментарии, закрытие/переоткрытие issues, timeline |
| Pull requests | Read and write | `GET pulls/{n}` и комментарии на PR (`pr_nudge`) |
| Metadata | Read-only | обязательное, ставится само |

Почему не больше (сверено по коду 2026-10-03): вызовы App в `conductor/` — только
`/repos/{r}`, `/repos/{r}/installation`, `/repos/{r}/issues…` (создание,
комментарий, `PATCH`, timeline) и `/repos/{r}/pulls/{n}`. В `contents`, `checks`,
`statuses` App не ходит — чтения идут токеном чтения хоста (`gh`). Срез 1 «ни
веток, ни PR» (спека §0), поэтому `contents: write` не нужен: прежний список в
`sandbox.md` п. 2 (`contents: write`, `checks: read`, `statuses: read`) сужен
этим документом. Права `actions` нет намеренно: логи CI conductor не читает.
**Organization / Account permissions** — ничего. **Subscribe to events** — ничего.

**Create GitHub App** → на странице App запомнить **App ID** (число).

## 2. Ключ

На странице App → **Private keys** → **Generate a private key** — скачается
`<slug>.<дата>.private-key.pem`. Ключ — только на хосте-писателе (VPS
`vmi3423913`, `ssh admin@pr0sto.net`), в репо и на Mac не хранить:

```bash
scp ~/Downloads/<slug>.*.private-key.pem admin@pr0sto.net:/tmp/conductor.pem
ssh admin@pr0sto.net 'sudo install -d -o conductor -g conductor -m 0700 /srv/conductor/keys \
  && sudo install -o conductor -g conductor -m 0600 /tmp/conductor.pem /srv/conductor/keys/conductor.pem \
  && rm /tmp/conductor.pem'
rm ~/Downloads/<slug>.*.private-key.pem
```

Отзыв ключа — удалить его в **Private keys** (сценарий A11 приёмки проверяет,
что после этого записей нет).

## 3. Установка — сначала только песочница

Песочница (`sandbox.md`): владелец создаёт `andrei-shtanakov/conductor-sandbox`
(приватное) и `andrei-shtanakov/conductor-sandbox-outside`.

**Видимость песочницы — решить до создания.** `sandbox.md` п. 1 называет
`conductor-sandbox` приватным, а п. 3 требует, чтобы учётные данные чтения хоста
(`/srv/conductor/gh/hosts.yml` — classic PAT `ai-prosto` **без scope**,
`deploy/conductor/README.md`) видели оба репо. Токен без scope приватный репо не
читает. Варианты: (а) песочница **публичная**, как весь флот, — токен не меняется;
(б) приватная — `ai-prosto` добавить коллаборатором с правом чтения и выпустить
токен со scope `repo` (он даст и запись — шире, чем у хоста сейчас).
Рекомендация — (а).

Страница App → **Install App** → аккаунт `andrei-shtanakov` → **Only select
repositories** → **только** `conductor-sandbox` (не `-outside`: сценарий A10
проверяет адрес вне забора). После установки URL вида
`https://github.com/settings/installations/<число>` — это **Installation ID**.

## 4. Конфиг приёмки на VPS

```bash
ssh admin@pr0sto.net
sudo -u conductor cp /srv/conductor/devtools/deploy/conductor/acceptance.toml.example /srv/conductor/acceptance.toml
sudo -u conductor chmod 0600 /srv/conductor/acceptance.toml
sudo -u conductor ${EDITOR:-nano} /srv/conductor/acceptance.toml   # app_id, installation_id, OWNER → andrei-shtanakov
```

Проверка ключа и установки (без записей):

```bash
cd /srv/conductor/devtools && sudo -u conductor uv run --frozen python -m conductor init-state --config /srv/conductor/acceptance.toml
```

Дальше — `sandbox.md` (содержимое песочницы, выдержка суток, прогон A1–A13) и
квитанция по `TEMPLATE-slice1.md`.

## 5. Флот — только после принятой квитанции

`deploy/conductor/README.md`, «Срез 1 — запись», пп. 3–7: установка App на репо
флота (**Only select repositories**, по составу манифеста), `conductor.toml` с тем
же `app_id` и `installation_id` **этой** установки, неделя тени
(`shadow = true`, `autonomy = 0`), затем ступени `enabled_actions` правками
`roadmap.toml` зонтика.
