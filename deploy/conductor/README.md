# conductor на VPS — срез 0 (советчик, уровень 0)

Срез 0 **ничего не пишет** во флот: прогон читает клоны и GitHub и пишет снимок
в `/srv/conductor/state/runs/<run_id>/`. GitHub App не нужен.

1. `sudo GIT_BASE=https://github.com/andrei-shtanakov deploy/conductor/setup.sh` —
   клоны по https без ключа (как `deploy/r16/`) и с **полной** историей: у мелкого
   клона возраст ожиданий и история удалений неверны, conductor считает его ошибкой
   чтения. Репо, добавленные в манифест позже, — повторный запуск `setup.sh`.
   `gh` — **не ниже 2.48** (нужен `gh api --paginate --slurp`): пакет Ubuntu
   старше, ставить из официального репозитория https://cli.github.com/packages;
   `setup.sh` проверяет версию и останавливается на старой.
2. Авторизация `gh` **только на чтение**: classic PAT **без единого scope** от
   `ai-prosto` (https://github.com/settings/tokens/new) — весь флот публичный,
   такой токен читает только публичное и ничего не пишет; отдельный аккаунт не
   тратит лимит API владельца. Fine-grained не подходит: он не даёт доступа к
   репо чужого аккаунта (урок `deploy/r16/`). `gh auth login --with-token`
   токен без scope отвергает, поэтому он пишется в `hosts.yml` скрытым вводом:

   ```bash
   ssh -t admin@<vps> "sudo -u conductor bash -c 'umask 077; read -rsp \"token: \" T; echo; printf \"github.com:\n    oauth_token: %s\n    user: ai-prosto\n    git_protocol: https\n\" \"\$T\" > /srv/conductor/gh/hosts.yml; echo written'"
   ```

   Проверка: `sudo -u conductor env GH_CONFIG_DIR=/srv/conductor/gh gh api
   rate_limit -q .resources.core` — `limit` 5000 (60 — токен не подхвачен).
3. Пробный прогон: `sudo systemctl start conductor.service`,
   `journalctl -u conductor -n 50`, снимок — в `state/runs/`.
4. Включить таймер: `sudo systemctl enable --now conductor.timer`.
5. `hostname` VPS — значение `writer_host` в `roadmap.toml` зонтика (в срезе 0
   используется только для отчёта; писать начнёт срез 1).

Обновление кода: `sudo -u conductor git -C /srv/conductor/devtools pull --ff-only`.

## Срез 1 — запись (шаги владельца)

1. Создать GitHub App и ключ (права — `docs/conductor/acceptance/sandbox.md`, п. 2),
   положить ключ в `/srv/conductor/keys/conductor.pem` (`conductor:conductor`, `0600`).
2. Пройти приёмку в песочнице (`docs/conductor/acceptance/sandbox.md`), заполнить
   квитанцию по шаблону `docs/conductor/acceptance/TEMPLATE-slice1.md`.
3. Установить App на репо флота; скопировать `deploy/conductor/conductor.toml.example`
   в `/srv/conductor/conductor.toml` (`0600`), вписать `app_id` и `installation_id`.
4. `sudo -u conductor uv run --frozen python -m conductor init-state --config /srv/conductor/conductor.toml`
   (запускать из `/srv/conductor/devtools`; карантин записей — 65 минут).
   `init-state`, `run --config` и `--recover` сами берут lock хоста
   `/srv/conductor/state/conductor.lock` (`[run] lock` в конфиге): занят — команда
   не выполняется (`run` — пропуск прогона с кодом 0, `init-state` — код 4).
5. Тень: установить drop-in `deploy/conductor/writer.conf` (команда — в его шапке).
   Он запускает `run --level 3 --config …` без внешнего `flock` (lock берёт сам
   процесс). Без `--level` потолок прогона — 0 (О §2.4): ручной прогон для записи —
   тоже с `--level`. Неделя ежечасных прогонов с `shadow = true`, `autonomy = 0`.
6. Ступени: снять тень (`shadow = false`), затем правками `roadmap.toml` зонтика:
   `autonomy = 1`, `enabled_actions` = `["owner_queue"]` → `+ notify_satisfied` →
   `+ nudge, pr_nudge` → `+ close_shipped`. Перед каждой — показатели:
   `uv run --frozen python -m conductor stage-report --out /srv/conductor/state/runs --since <ISO> --until <ISO>`.
7. Откат: убрать действие или `autonomy = 0` (правка роадмапа), либо удалить drop-in,
   либо удалить ключ App. Сделанные записи не отменяются.
