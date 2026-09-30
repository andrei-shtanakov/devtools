# conductor на VPS — срез 0 (советчик, уровень 0)

Срез 0 **ничего не пишет** во флот: прогон читает клоны и GitHub и пишет снимок
в `/srv/conductor/state/runs/<run_id>/`. GitHub App не нужен.

1. `sudo GIT_BASE=https://github.com/andrei-shtanakov deploy/conductor/setup.sh` —
   клоны по https без ключа (как `deploy/r16/`) и с **полной** историей: у мелкого
   клона возраст ожиданий и история удалений неверны, conductor считает его ошибкой
   чтения. Репо, добавленные в манифест позже, — повторный запуск `setup.sh`.
2. Авторизация `gh` **только на чтение** (fine-grained токен владельца:
   `contents`, `issues`, `pull_requests`, `metadata`, `checks`, `statuses` —
   read) в `GH_CONFIG_DIR=/srv/conductor/gh` пользователя `conductor`.
3. Пробный прогон: `sudo systemctl start conductor.service`,
   `journalctl -u conductor -n 50`, снимок — в `state/runs/`.
4. Включить таймер: `sudo systemctl enable --now conductor.timer`.
5. `hostname` VPS — значение `writer_host` в `roadmap.toml` зонтика (в срезе 0
   используется только для отчёта; писать начнёт срез 1).

Обновление кода: `sudo -u conductor git -C /srv/conductor/devtools pull --ff-only`.
