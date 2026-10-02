# Песочница conductor, срез 1 (спека среза 1, §9)

## Что создаёт владелец

1. Репо `<owner>/conductor-sandbox` (приватное) и `<owner>/conductor-sandbox-outside`.
2. GitHub App `conductor` (пошагово — `github-app.md`): права `issues: write`,
   `pull_requests: write`, `metadata: read` — минимум по коду (сужено 2026-10-03:
   `contents`/`checks`/`statuses` App не использует); установка **только** на
   `conductor-sandbox`. Ключ App — на VPS в `/srv/conductor/keys/conductor.pem`
   (владелец `conductor`, `0600`).
3. Учётные данные чтения хоста (`/srv/conductor/gh/hosts.yml`) видят оба репо.

## Содержимое `conductor-sandbox` (он же зонтик и единственный репо-цель)

- `workspace-manifest.toml`:

  ```toml
  [cores.conductor-sandbox]
  repo_url = "https://github.com/<owner>/conductor-sandbox.git"
  git_dir = "conductor-sandbox"
  [cores.conductor-sandbox-outside]
  repo_url = "https://github.com/<owner>/conductor-sandbox-outside.git"
  git_dir = "conductor-sandbox-outside"
  ```

- `epics.toml`: эпики `acc.focus` (active) и `acc.bg` (active).
- `roadmap.toml`: `schema_version = 1`, `autonomy = 0`, `writer_host = "vmi3423913"`,
  `writer_since` — момент установки ключа, `[[focus]] epic = "acc.focus"`,
  `[limits] stale_after_days = 1`, `renudge_after_days = 1`.
- `TODO.md` (каждая строка — под свой сценарий):

  ```
  - [ ] Цель A4 @owner:github:<owner> @id:goal-a4 @epic:acc.focus @blocked_by:todo://conductor-sandbox/pre-a4
  - [ ] Предпосылка A4 @owner:TBD @id:pre-a4 @epic:acc.bg
  - [ ] Цель A5 @owner:github:<owner> @id:goal-a5 @epic:acc.focus @blocked_by:todo://conductor-sandbox/pre-a5
  - [ ] Предпосылка A5 @owner:TBD @id:pre-a5 @epic:acc.bg
  - [ ] Цель A10 @owner:github:<owner> @id:goal-a10 @epic:acc.focus @blocked_by:conductor-sandbox-outside#1
  ```

- Issues (метка `inbox`, тело начинается шапкой `slug:`/`from:`):
  - `#1 slug: goal-a4` и `#2 slug: goal-a4` — два адреса цели A4 (A4);
  - `#3 slug: pre-a5` — адрес продюсера A5 (A5);
  - `#4`, `#5`, `#6` — issues для A3a/A3b/A3d, к каждому заранее влит PR в `master`
    с `Fixes #N`, после чего issue **переоткрыт** владельцем (основание — только ответ);
- PR `#7` с нарочно красной проверкой (workflow `exit 1`), реализует `@id:goal-a4`,
  открыт за сутки до прогона (A6).
- `conductor-sandbox-outside#1` — открытый issue (A10: адрес вне забора).

Изоляция (ревью P2-4): в профиле `acceptance` зонтик — сама песочница (`umbrella_name(cfg)`):
манифест, роадмап, эпики, очередь и состав флота читаются только из неё, настоящий зонтик
владельца в граф не добавляется и не читается (`test_collect_slice1.py`). Внешние чтения
приёмки — только `conductor-sandbox` и объявленный `conductor-sandbox-outside`.

## Выдержка времени

За сутки до прогона: коммит рёбер `goal-a5 → pre-a5` и последнее движение по `#3`
(комментарий владельца); открытие PR `#7`. К моменту прогона — полные 24 часа.

## Клон на VPS

```bash
sudo -u conductor git clone https://github.com/<owner>/conductor-sandbox.git /srv/conductor/acceptance/workspace/conductor-sandbox
sudo -u conductor git clone https://github.com/<owner>/conductor-sandbox-outside.git /srv/conductor/acceptance/workspace/conductor-sandbox-outside
sudo -u conductor uv run --frozen python -m conductor init-state --config /srv/conductor/acceptance.toml
```

Прогон сценария (после 65 минут карантина; без `--level` потолок прогона — 0 и записей нет):

```bash
sudo -u conductor uv run --frozen python -m conductor run --root /srv/conductor/acceptance/workspace --out /srv/conductor/acceptance/runs --level 3 --config /srv/conductor/acceptance.toml
```

`init-state` и `run --config` берут lock хоста `/srv/conductor/state/conductor.lock` — тот
же, что у прогона флота: приёмка и таймер не идут одновременно (занят — прогон пропущен,
повторить позже).

## Сценарии

Таблица A1–A13 — спека среза 1, §9.3. Для каждого: `acceptance_dump.py` до,
`acceptance.sh` (если сценарий меняет роадмап), прогон, `acceptance_dump.py` после,
выдержка `runs/<id>/calls.jsonl` и `journal.jsonl`; всё — в квитанцию.
