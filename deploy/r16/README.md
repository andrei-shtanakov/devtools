# R16 runner на VPS

Спека: `docs/superpowers/specs/2026-09-25-r16-runner-graduation-design.md`.
Раскладка и права — §1.3; передача — §2.2; откат — §2.3.

## 1. Установка

Первое развёртывание — 2026-09-26 на `pr0sto.net` (тот же хост, что у Robin,
вход `admin@`, sudo без пароля). Разделы ниже — как оно прошло на деле.

**Пакет `gh`.** В образе Ubuntu 24.04 его нет, а `setup.sh` без него
останавливается. Штатного пакета достаточно (2.45):

```bash
sudo apt-get install -y gh
```

**Доступ к GitHub на чтение — по https, без ключа.** Все репо, которые клонирует
`setup.sh` (devtools, `ai-orchestrators-workspace` и всё из
`workspace-manifest.toml`), публичные, поэтому `GIT_BASE` —
`https://github.com/andrei-shtanakov`. Ключ пользователю `r16` не нужен, и
на сервере не появляется ещё один ключ с доступом к GitHub. Если какой-то репо
станет приватным, `setup.sh` оборвётся на его `git clone` — тогда нужен
ssh-ключ для `r16` (`/srv/r16/.ssh/`, 0700/0600) и ssh-`GIT_BASE`.

**`setup.sh` — из временного клона** (постоянную копию в `/srv/r16/devtools`
скрипт кладёт сам):

```bash
rm -rf /tmp/r16-setup
git clone -q --depth 1 https://github.com/andrei-shtanakov/devtools /tmp/r16-setup
sudo GIT_BASE=https://github.com/andrei-shtanakov bash /tmp/r16-setup/deploy/r16/setup.sh
rm -rf /tmp/r16-setup
sudo sed -i "s/^R16_HOST_LABEL=.*/R16_HOST_LABEL=pr0sto.net/" /srv/r16/r16.env
```

Проверка — таблица прав §1.3, 23 клона, `robin` в `r16-readers`, таймер
`disabled`:

```bash
sudo stat -c "%U:%G %a %n" /srv/r16 /srv/r16/devtools /srv/r16/workspace \
  /srv/r16/state /srv/r16/state/r16.lock /srv/r16/state/receipts /srv/r16/gh /srv/r16/r16.env
sudo ls /srv/r16/workspace | wc -l
id robin
systemctl is-enabled r16-kb-freshness.timer
```

`setup.sh` таймер **не включает**: смена исполнителя — только передачей (§3).

**Токен gh ai-prosto.** Токен на Mac хранится в Keychain, а не в
`~/.config/review/hosts.yml`, так что скопировать профиль нельзя. Для
сервера — **отдельный classic PAT от ai-prosto только со scope
`public_repo`**: раннеру нужны issue и метка в одном публичном репо
(prograph-vault), а токен Mac (`repo, workflow, …`) пишет во все репо.
Fine-grained токен не подходит: он не даёт доступа к чужому личному репо, где
ai-prosto — коллаборатор. Создаётся под аккаунтом ai-prosto:
https://github.com/settings/tokens/new → scope только `public_repo`.

`gh auth login --with-token` такой токен **отвергает** (требует `repo` и
`read:org`), поэтому он пишется в `hosts.yml` напрямую — скрытым вводом, чтобы
не попасть ни в историю, ни в командную строку. Из своего терминала:

```bash
ssh -t admin@pr0sto.net "sudo -u r16 bash -c 'umask 077; read -rsp \"token: \" T; echo; printf \"github.com:\n    oauth_token: %s\n    user: ai-prosto\n    git_protocol: https\n\" \"\$T\" > /srv/r16/gh/hosts.yml; echo written'"
```

## 2. Проверка до передачи (§2.2 шаг 1)

```bash
sudo -u r16 -H bash -c 'set -a; . /srv/r16/r16.env; set +a; cd /srv/r16/devtools;
  /usr/local/bin/uv run --script r16_runner.py --dry-run'
sudo -u r16 env GH_CONFIG_DIR=/srv/r16/gh gh auth status
sudo -u r16 env GH_CONFIG_DIR=/srv/r16/gh gh issue list -R andrei-shtanakov/prograph-vault --label kb-freshness --state all --limit 3
```

Ожидается: пробный прогон печатает квитанцию с `"execution": "completed"`,
`producer.host` из `r16.env`, `delivery.action: dry-run` и ничего не
записывает в `/srv/r16/state/receipts/`. `gh auth status` называет ai-prosto
со scope `public_repo`; строка `Missing required token scopes: 'repo',
'read:org'` ожидаема и работе не мешает. Пробный прогон gh не вызывает —
чтение проверяет `gh issue list`; **запись** впервые проверяет первая
настоящая доставка (квитанция покажет `delivery: failed`, если прав мало).

## 3. Передача Mac → VPS (§2.2 шаги 2–5)

В каждый момент активен не больше чем один исполнитель.

На Mac (шаг 2):

```bash
launchctl bootout gui/$UID/dev.atp.r16-kb-freshness
# plist — в резерв, не удалять до приёмки: откат возвращает его обратно
mkdir -p ~/Library/LaunchAgents.disabled
mv ~/Library/LaunchAgents/dev.atp.r16-kb-freshness.plist ~/Library/LaunchAgents.disabled/
# на macOS нет flock(1) — проверка тем же fcntl, что у раннера
python3 -c 'import fcntl,sys; fcntl.flock(open(sys.argv[1]), fcntl.LOCK_EX | fcntl.LOCK_NB)' \
  ~/labs/all_ai_orchestrators/_cowork_output/cadence/r16/receipts/.lock && echo "прогон не идёт"
```

Тем же шагом в `_cowork_output/ops/r2-liveness-check.sh` выключить блок R16
(§2.4).

Снимок (шаг 3) — без прав Mac, затем права явно:

```bash
rsync -rt --no-perms --no-owner --no-group --exclude .lock \
  ~/labs/all_ai_orchestrators/_cowork_output/cadence/r16/receipts/ \
  admin@pr0sto.net:/tmp/r16-receipts/
# на VPS:
sudo rsync -rt /tmp/r16-receipts/ /srv/r16/state/receipts/
sudo chown -R r16:r16-readers /srv/r16/state/receipts
sudo find /srv/r16/state/receipts -type d -exec chmod 2750 {} +
sudo find /srv/r16/state/receipts -type f -exec chmod 0640 {} +
```

Сверка: на Mac `cd <receipts> && find . -type f -name '*.json' | sort | xargs shasum -a 256`,
на VPS — `sudo sha256sum` тех же файлов **по абсолютным путям**
(`/srv/r16/state/receipts/…`): `admin` не входит в `r16-readers` и `cd` в
каталог квитанций ему запрещён — так и задумано. Суммы совпадают. Затем
удалить `/tmp/r16-receipts` и:

```bash
sudo -u robin cat /srv/r16/state/receipts/<последний>.json >/dev/null && echo ok
sudo -u robin cat /srv/r16/state/receipts/legacy/<файл>.json >/dev/null && echo ok
```

Перенесённые выполненные попытки (`completed`/`failed`) — история до
контракта: у них нет `producer.host`, и схеме v1 они не соответствуют;
перенесённые `missed` схеме соответствуют. Раннер их читает как раньше
(`contracts/r16-receipt/v1/README.md`, «Квитанции до контракта»).

Включение (шаг 4) — право на issue переходит к VPS ровно здесь:

```bash
sudo systemctl enable --now r16-kb-freshness.timer
# разовый ручной запуск — проверка юнита (пользователь, env, путь к uv); по
# перенесённой квитанции текущего цикла раннер отвечает `done` и ничего не пишет
sudo systemctl start r16-kb-freshness.service
systemctl show r16-kb-freshness.service -p Result -p ExecMainStatus
sudo journalctl -u r16-kb-freshness.service -n 5 --no-pager -o cat
```

Ожидается `Result=success`, `ExecMainStatus=0`, в журнале
`cycle <текущий>: done`, число файлов квитанций не изменилось.

## 4. Откат (§2.3)

```bash
sudo systemctl disable --now r16-kb-freshness.timer
# ждём, пока прогон не закончится: после ok:false юнит в состоянии `failed`,
# а не `inactive`, поэтому ждём ухода из activating/active
while systemctl is-active -q r16-kb-freshness.service \
   || [ "$(systemctl is-active r16-kb-freshness.service)" = activating ]; do sleep 5; done
sudo -u r16 flock -n /srv/r16/state/r16.lock true && echo "блокировка свободна"
```

Только после обоих условий: квитанции обратно на Mac тем же способом (без
прав, со сверкой sha256), затем вернуть plist из `~/Library/LaunchAgents.disabled/` в
`~/Library/LaunchAgents/` и
`launchctl bootstrap gui/$UID ~/Library/LaunchAgents/dev.atp.r16-kb-freshness.plist`;
блок R16 сторожа вернуть из git-истории `_cowork_output/ops/r2-liveness-check.sh`.

## 5. Приёмка (§4.4)

1. Пробный прогон и `gh auth status` — раздел 2.
2. Права по таблице §1.3 — `stat -c '%U:%G %a %n'` каждой строки. От `robin`
   проходят `cat` квитанции верхнего уровня и из `legacy/`; шесть отказов:

   ```bash
   for c in "ls /srv/r16" "ls /srv/r16/gh" "cat /srv/r16/r16.env" \
            "cat /srv/r16/state/r16.lock" "ls /srv/r16/workspace" "ls /srv/r16/state"; do
     sudo -u robin sh -c "$c" >/dev/null 2>&1 && echo "ДОСТУП: $c" || echo "отказ: $c"
   done
   ```

   Ожидается шесть строк `отказ: …`. Robin увидит группу `r16-readers` только
   после рестарта своих сервисов (`usermod` не меняет группы запущенных
   процессов).
3. Передача — раздел 3, со сверкой sha256.
4. Первая квитанция, записанная на VPS: `"host": "<R16_HOST_LABEL>"` в
   `producer`, группа `r16-readers`, режим 640; если в ней есть issue — его
   автор ai-prosto (первая проверка записи токеном `public_repo`). После
   этого — удалить резервный plist на Mac.

## 6. Обновление кода

```bash
sudo -u r16 git -C /srv/r16/devtools pull --ff-only
```

Раннер сам себя не обновляет.

## 7. Названная дыра (§2.4)

До проверки ожидаемого цикла в Robin (заявка в robin-runtime) живость R16
автоматически не проверяет никто. Пропущенный цикл станет виден квитанцией
`missed` при следующем прогоне.
