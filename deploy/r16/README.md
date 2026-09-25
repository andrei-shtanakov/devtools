# R16 runner на VPS

Спека: `docs/superpowers/specs/2026-09-25-r16-runner-graduation-design.md`.
Раскладка и права — §1.3; передача — §2.2; откат — §2.3.

## 1. Установка

```bash
sudo GIT_BASE=git@github.com:andrei-shtanakov deploy/r16/setup.sh
sudo -e /srv/r16/r16.env            # R16_HOST_LABEL=<имя VPS>
```

Пользователю `r16` нужен ssh-доступ на чтение к `GIT_BASE` (deploy key или
ключ машины). Профиль gh ai-prosto:

```bash
sudo install -o r16 -g r16 -m 0600 <hosts.yml ai-prosto> /srv/r16/gh/hosts.yml
```

`setup.sh` таймер **не включает**: смена исполнителя — только передачей (§3).

## 2. Проверка до передачи (§2.2 шаг 1)

```bash
sudo -u r16 bash -c 'set -a; . /srv/r16/r16.env; set +a;
  /usr/local/bin/uv run --script /srv/r16/devtools/r16_runner.py --dry-run'
sudo -u r16 GH_CONFIG_DIR=/srv/r16/gh gh auth status
sudo -u r16 GH_CONFIG_DIR=/srv/r16/gh gh issue list -R andrei-shtanakov/prograph-vault --label kb-freshness
```

Ожидается: пробный прогон печатает квитанцию с `"execution": "completed"` и
ничего не записывает в `/srv/r16/state/receipts/`; `gh auth status` называет
ai-prosto. Пробный прогон gh не вызывает — авторизацию проверяют две
последние команды.

## 3. Передача Mac → VPS (§2.2 шаги 2–5)

В каждый момент активен не больше чем один исполнитель.

На Mac (шаг 2):

```bash
launchctl bootout gui/$UID/dev.atp.r16-kb-freshness
rm ~/Library/LaunchAgents/dev.atp.r16-kb-freshness.plist
flock -n ~/labs/all_ai_orchestrators/_cowork_output/cadence/r16/receipts/.lock true && echo "прогон не идёт"
```

Тем же шагом в `_cowork_output/ops/r2-liveness-check.sh` выключить блок R16
(§2.4).

Снимок (шаг 3) — без прав Mac, затем права явно:

```bash
rsync -rt --no-perms --no-owner --no-group   ~/labs/all_ai_orchestrators/_cowork_output/cadence/r16/receipts/   <vps>:/tmp/r16-receipts/
# на VPS:
sudo rsync -rt /tmp/r16-receipts/ /srv/r16/state/receipts/
sudo chown -R r16:r16-readers /srv/r16/state/receipts
sudo find /srv/r16/state/receipts -type d -exec chmod 2750 {} +
sudo find /srv/r16/state/receipts -type f -exec chmod 0640 {} +
```

Сверка: `cd <receipts> && find . -type f -name '*.json' | sort | xargs shasum -a 256`
на Mac и `sha256sum` того же списка на VPS — выводы совпадают. Затем:

```bash
sudo -u robin cat /srv/r16/state/receipts/<последний>.json >/dev/null && echo ok
sudo -u robin cat /srv/r16/state/receipts/legacy/<файл>.json >/dev/null && echo ok
```

Перенесённые файлы — история до контракта: у них нет `producer.host`, и
схеме v1 они не соответствуют. Раннер их читает как раньше
(`contracts/r16-receipt/v1/README.md`, «Квитанции до контракта»).

Включение (шаг 4) — право на issue переходит к VPS ровно здесь:

```bash
sudo systemctl enable --now r16-kb-freshness.timer
```

## 4. Откат (§2.3)

```bash
sudo systemctl disable --now r16-kb-freshness.timer
until [ "$(systemctl is-active r16-kb-freshness.service)" = inactive ]; do sleep 5; done
sudo -u r16 flock -n /srv/r16/state/r16.lock true && echo "блокировка свободна"
```

Только после обоих условий: квитанции обратно на Mac тем же способом (без
прав, со сверкой sha256), затем вернуть plist и
`launchctl bootstrap gui/$UID ~/Library/LaunchAgents/dev.atp.r16-kb-freshness.plist`.

## 5. Приёмка (§4.4)

1. Пробный прогон и `gh auth status` — раздел 2.
2. Права по таблице §1.3 — `stat -c '%U:%G %a %n'` каждой строки. От `robin`
   проходят `cat` квитанции верхнего уровня и из `legacy/`; отказывают
   `ls /srv/r16`, `ls /srv/r16/gh`, `cat /srv/r16/r16.env`,
   `cat /srv/r16/state/r16.lock`, `ls /srv/r16/workspace`.
3. Передача — раздел 3, со сверкой sha256.
4. Первая квитанция, записанная на VPS: `"host": "<R16_HOST_LABEL>"` в
   `producer`, группа `r16-readers`, режим 640.

## 6. Обновление кода

```bash
sudo -u r16 git -C /srv/r16/devtools pull --ff-only
```

Раннер сам себя не обновляет.

## 7. Названная дыра (§2.4)

До проверки ожидаемого цикла в Robin (заявка в robin-runtime) живость R16
автоматически не проверяет никто. Пропущенный цикл станет виден квитанцией
`missed` при следующем прогоне.
