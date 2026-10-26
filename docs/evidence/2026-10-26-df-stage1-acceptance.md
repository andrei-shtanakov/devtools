# Приёмка этапа 1 df-installer на macOS arm64 и Ubuntu x86_64 — 2026-10-26

Task 13 плана `docs/superpowers/plans/2026-10-16-df-installer-stage1.md`; спека B rev 8
`docs/superpowers/specs/2026-10-13-df-installer-design.md` §3.8 «Приёмка этапа 1».
Root-тесты: `tests/df/test_platform_root.py`, `tests/df/test_acceptance_root_{install,copy,
account}.py`; мир — `tests/df/root_world.py`; наблюдатель — `tests/df/observe.py`; workflow —
`.github/workflows/df-stage1.yml`.

**Источник.** Всё в разделах 1–6 взято только из артефактов одного прогона — последнего полного
прогона ветки ([40238123506](https://github.com/andrei-shtanakov/devtools/actions/runs/40238123506),
голова `2d2b8c4` — последний коммит, меняющий код, тесты и workflow; коммит этого документа
кода не меняет). Прежние зелёные прогоны доказательством не считаются (решение
владельца 25.10). Факт, которого нет в артефактах, помечен **(не из артефактов)** с
источником. Root-команды исполнялись только в одноразовых runner-ах GitHub, ни разу на
машине человека.

**Итог.**
- фикстурная приёмка механизма: **pass** (macOS arm64, Ubuntu x86_64);
- наблюдатель ОС: **observed** в обеих ячейках — каждое из 104 (macOS) и 106
  (Ubuntu) наблюдений `observed`; гарантия «root не открывает источник» подтверждена **в
  пределах, названных в разделе 3**;
- проверено внедрением сбоев: прерывание внутри каждого действия маршрута, внутри приёма
  (`member`, `mid-copy`), после активации, на каждой команде создания учётки (раздел 2);
- спроектировано, не испытано: **отключение питания** (§3.3);
- находок против спеки B rev 8 нет; решения владельца 26.10 — раздел 7, изменения Task 14
  в приёмке — раздел 8;
- этап 1 по §10 **не закрыт**: `core` live не прогонялся, изоляция харнессов не подтверждена,
  проверки от имени ролей — ворота этапа 2.

## 1. Прогон

| | |
|---|---|
| прогон | [40238123506](https://github.com/andrei-shtanakov/devtools/actions/runs/40238123506) |
| голова | `2d2b8c40d95f5566ed4fa4078bca31cdaea278df` (ветка `feat/df-stage1-task13`, PR #632, событие `pull_request`) |
| workflow | `df-stage1`, `DF_STAGE1_SCOPE: full`, `DF_STAGE1_K: ""` (полный охват, без `-k`); последний шаг `scope guard` — success (иной охват или непустой `-k` — красный) |
| дата | 2026-10-25, 22:00–22:21 UTC (26.10 по Тбилиси) |

| | macOS | Ubuntu |
|---|---|---|
| runner | `macos-15`, macOS 15.7.9 (24G830), arm64 | `ubuntu-24.04`, Ubuntu 24.04.5 LTS, ядро 6.17.0-1022-azure, x86_64 |
| проверка архитектуры | `uname -m` = `arm64` | `uname -m` = `x86_64` |
| `uv` | 0.11.23 (закреплён; = `UV_PROJECT_KEYS_0_11_23`) | 0.11.23 (то же) |
| Python тестов | 3.12.13 | 3.12.13 |
| Python релиза | `cpython-3.12.13-macos-aarch64-none` без Tcl/Tk | `cpython-3.12.13-linux-x86_64-gnu` без Tcl/Tk |
| `sudo` | 1.9.13p2 | 1.9.15p5 |
| утверждённый `tar` | `/usr/bin/tar` → `/usr/bin/bsdtar` (bsdtar 3.5.3, libarchive 3.7.4) | `/usr/bin/tar` (GNU tar 1.35) |
| утверждённый `git` | `/usr/bin/git` (2.39.5 (Apple Git-154); Command Line Tools, R96) | `/usr/bin/git` (2.55.0) |
| `uv` в мире | root-копия `<R>/cli/uv` (R88) | то же |
| наблюдатель | `eslogger` (Endpoint Security), запуск отсоединённый | `auditd` (поставлен шагом Step 0) |
| обычный прогон `tests/df` (`-m 'not slow and not harness'`, с `network`) | 2851: 2731 passed, 120 skipped, 0 failed | 2844: 2704 passed, 140 skipped, 0 failed |
| root-прогон (`-m root`) | 119: 113 passed, 6 skipped, 0 failed | 112: 111 passed, 1 skipped, 0 failed |
| длительность root-прогона | 15:17 (шаг 22:05:56–22:21:13; junit 915 с) | 11:15 (шаг 22:03:28–22:14:43; junit 674 с) |

Пропуски root-прогона — только платформенные `skipif`, ни одного пропуска root-гейтом:
macOS — 6 тестов только для Linux (`test_account_created_through_main_linux` ×2,
`test_gnu_padding_with_small_pipe`, `test_step2_and_lock_under_setgid_parents_real_linux`,
`test_linux_loader_residuals`, `test_posix_acl_makes_auth_unreadable_linux`); Ubuntu — 1 только
для macOS (`test_home_acl_facts_macos`). Сверка `tests/df/ci_junit.py` после прогона:
канарейка `test_root_gate_open` прошла, собранные root-тесты (`root-collected.txt`: macOS
119, Ubuntu 112) все есть в `junit-root.xml`. Пропуски обычного прогона —
root-тесты (гейт закрыт без root), платформенные и по условию хоста (macOS: ФС без учёта
регистра; Ubuntu: системный `python3` ≥ 3.12, ФС различает регистр
и нормализацию); пропуска «uv не закреплён» нет
(`DF_REQUIRE_PINNED_UV=1`).

**Строки Step 4 плана по ячейке.**
- FULLFSYNC (macOS, решение (а), R33.F10): строк `sync-note` в 85 журналах
  root-прогона нет — все каталоги приняли `F_FULLFSYNC`. «Нет `sync-note`» покрывает только
  синхронизации каталогов.
- `distributions-note` шага `verify` (Task 7a), обе ячейки (macOS 22, Ubuntu
  25 строк, текст один): «метаданные распределений согласованы с lock (заявлено); не
  доказывает отсутствия лишнего кода и происхождения файлов релиза».
- Версия `uv` прогона 0.11.23 = версии в имени `UV_PROJECT_KEYS_0_11_23`: список настроек
  `uv` перепроверен для этой версии.
- Python релиза без Tcl/Tk (решение владельца 25.10, спека §3.4): `TCLTK_REMOVED_3_12_13`
  удалён сборкой до `seal`; `import tkinter`, `_tkinter`, `turtle`, `turtledemo`, `idlelib`
  дают `ModuleNotFoundError`, `ssl`, `sqlite3`, `ctypes`, `venv`, `dfprobe --help` работают
  (`test_installed_python_has_no_tk_but_core_works`, pass в обеих ячейках). Состав
  `python/<ключ>/bin` — 12 имён (без `idle3*`), одинаков на обеих ОС
  (`boundary/<platform>.json`).
- Остаточный риск (§3.2, R33.F28): процессы `df-build`, пережившие сбой установщика во время
  `run_build`, могут писать в будущий `releases/<id>`. Root-тест этого окна не исполняет
  (раздел 4).
- Изоляция харнессов (R85, строка предполётной проверки дословно): «изоляция харнессов:
  claude 2.1.309, codex 0.163.0 (macOS) — not verified; Linux — не снималось; живая приёмка
  модельных ролей не пройдена (решение владельца 24.10)».
- `core` live: **not run** (ждёт `df-manifest-profiles`).
- Матрица сбоев Task 9 на настоящей ФС Linux: `test_crash_matrix` и закреплённые двойники
  прошли в обычном прогоне ячейки Ubuntu (`junit-ordinary.xml`). Медленный набор (`-m slow`,
  около 90 минут) в CI не входит и на Linux не прогонялся; полное перечисление прогнано
  локально на macOS в Task 9 (9-D2) **(не из артефактов: `progress.md` Task 9)**.

## 2. Покрытие §3.8 (R95)

Вид: **root** — настоящая ФС, root, `df-build` — настоящая учётка ОС, обе ячейки, если не
сказано иначе; **эмуляция** — `fake_world` + модель сбоя (`crash_model`) на ФС ячейки, обычный
прогон обеих ячеек; **юнит** — модель или синтетический поток в обычном прогоне обеих ячеек;
**не испытано** — нет теста. Прерывание «внутри действия» в root-тестах — первая точка
`fsutil.fault_point` внутри него, остановка `Killed` (исключение в процессе, `finally`
производителя выполняется), состояние ФС настоящее.

*Установка и восстановление*

| строка §3.8 | тесты | вид |
|---|---|---|
| чистая установка | `test_clean_install_reaches_level1` (уровень 1 из фактов) | root |
| повтор `init` после прерывания на каждом шаге и внутри каждой фазы приёма; частичное дерево не принимается | `test_rerun_after_interrupt[build-dir, build, seal, copy, verify, admin, register, activate, launcher, commit-config]`, `test_crash_inside_accept_real_fs[member, mid-copy]`; шаги подготовки — `test_install_steps::test_interrupt_prep_step_then_rerun_completes`; журнал не принимает частичное — `test_scenarios::test_partial_tree_not_accepted_by_journal` | root; эмуляция; юнит |
| обычный проход проходит автомат по порядку | `test_clean_pass_walks_automaton_in_order` | root |
| сбой между `current` и `launcher`/`commit-config` → тот же id, команда есть | `test_crash_after_activation_real_fs[activate, launcher]`; `test_crash_windows::test_crash_after_activation_completes_same_id` | root; эмуляция |
| повреждённый релиз под незавершённой установкой → отказ до изменений, ручное восстановление | `test_install_i9::test_i9_switched_damaged_refused`, `test_i9_registered_damaged_before_activate_refused_early`; ручное восстановление —
`test_install_i9::test_i9_procedure_{reinstall, first_install, after_switch_back, reentrant,
step_undone_by_os_crash}`; `test_install_steps::test_damaged_first_install_names_pending_then_current`, `test_damaged_under_resume_is_refused_with_a_way_out` | эмуляция (root-теста нет) |
| сбой между созданием учётки и `ready`, между временным домашним каталогом и `chown`, после записи пользователя и каждой команды атрибута → повтор до `ready`, UID/GID ожидаемые | `test_partial_account_creation_resumed[intent, attr:1…attr:9 (macOS: 9 команд dscl) / attr:1…attr:2 (Linux: groupadd, useradd), home-mkdir, home-chown, home-rename]` | root |
| чужой каталог на домашнем пути, чужая запись, запись с конфликтующим атрибутом не присваиваются | `test_foreign_home_dir_not_adopted`, `test_conflicting_record_not_adopted[record-before-intent, user-before-intent, partial-with-conflicting-gid]` | root |
| прерывание после `rename` домашнего каталога, повтор до `ready`, затем сбой ОС → каталог на месте (спека l.1757–1758) | Ubuntu: `test_account_created_through_main_linux[process, os]` — настоящий процесс через `main`; половина `os` — `sync`, `drop_caches=3`, новый `boot_id` **без потери питания**; потеря несинхронизированного — `test_crash_windows::test_account_created_through_main[os]`, `test_created_home_is_durable_before_ready` | root (только процесс и перезагрузка кешей); **сбой ОС — только эмуляция** |
| обычный путь `нет → creating → ready` без подмены проверок | каждая чистая установка (`create_build_user`, `verify_build_user` настоящие) | root |
| сбой процесса после `rename` `releases.toml` / последнего файла команды, повтор, сбой ОС; откат через `main → recover_pending` | `test_crash_windows::test_visible_unsynced_then_retry_then_os_crash`, `test_rollback_with_unsynced_fact_then_os_crash`, `test_rollback_durable_before_pending_removed`; `test_crash_matrix::test_crash_matrix` с двойниками | эмуляция |
| сбой процесса между `rename current` и синхронизацией `/opt/df`, затем сбой ОС | `test_crash_windows::test_process_crash_after_current_rename_then_os_crash` | эмуляция |
| читатель и переход установки, чередуемые управляемо | `test_crash_windows::test_reader_interleaved_with_install_commit`; читатель без root не открывает блокировку — `test_journal::test_reader_never_opens_lock` | эмуляция; юнит |
| сбой после активации до записи журнала → уровень 1 по фактам; поддельная запись журнала уровня не даёт | `test_crash_windows::test_crash_after_activate_before_journal_done`; `test_doctor::test_level1_ignores_journal` | эмуляция; юнит |
| два одновременных `init`; два одновременных `models set` | `test_install_steps::test_init_lock_loser_changes_nothing`; `test_models::test_concurrent_sets_of_two_roles_both_persist` | юнит |
| — | отключение питания (§3.3) | **не испытано** |

*Приём*

| строка §3.8 | тесты | вид |
|---|---|---|
| подмена файла в релизе → `doctor` «нарушено» | `test_tamper_after_install_doctor_broken` | root |
| живой писатель в staging: замена каталога ссылкой, смена цели ссылки, FIFO, жёсткая ссылка на root-only файл, сохранённый дескриптор, растущий файл и дерево | `test_live_writer_during_copy[dir-to-link, link-retarget, file-to-fifo, hardlink-root-only-file, saved-fd-writer, growing-file, growing-tree]`, исходы — ниже; `test_build_user_after_seal_cannot_touch_destination` | root, наблюдение |
| настоящий производитель → настоящий приём: пустое и непустое дерево, запись корня, длинные и не-ASCII имена, ссылки; digest = digest источника | `test_real_tar_from_build_user_accepted[full, empty]`; `test_receive::test_real_tar_stream_accepted` | root, наблюдение; юнит |
| поток с `../x`, абсолютным именем, ссылкой как каталогом, устройством, ссылкой на непринятый файл, записью корня не-каталогом или не первой, неизвестным ключом pax → отказ | `test_receive::test_bad_names_refused`, `test_symlink_as_intermediate_dir_refused`, `test_device_fifo_sparse_refused`, `test_hardlink_to_unaccepted_target_refused`, `test_root_record_rules`, `test_unknown_pax_key_refused` | юнит (синтетический поток; root-теста нет) |
| производитель замолчал → отказ за срок, производитель завершён, блокировка свободна, частичная копия root `0700` до следующего запуска, `copy-partial` её убирает | `test_silent_producer_released`, `test_forked_producer_reaped_via_spawn_as` (обе — через `bootstrap init`, отклонение от R89 принято); маршрут `cleanup` (откат) — `test_routes::test_cleanup_removes_both_trees_in_route_order` | root; юнит |
| расширенный заголовок больше предела; испорченная контрольная сумма → отказ | `test_receive::test_oversized_pax_refused_before_body_read`, `test_malformed_stream_is_tree_error[bad-checksum]` | юнит |
| очистка растущего дерева → карантин в бюджете, установка продолжается; следующая установка удаляет остаток; root не открывает дерево | `test_cleanup_of_growing_tree_quarantined_within_budget[writer, deadline]` (лист `cleanup_tree(budget=5)`, R89), `test_quarantine_removed_by_next_install`, `test_cleanup_of_000_tree` | root, наблюдение |
| ссылки: выход через `..`, абсолютная цель, цепочка → отказ | `test_dangerous_link_in_artifact_refused[/etc, ../../.., {LR}/../../../../etc/passwd, chain]` | root |
| ссылки: цикл → отказ | `test_digest::test_link_cycle_refused` | **только юнит** (root-теста нет) |
| внутренняя абсолютная ссылка после переноса и копии принята | `test_internal_absolute_links_survive_rename_and_copy` | root |
| отсутствующий entrypoint; прямой `shebang` со сломанным `--help` → отказ | `test_missing_entrypoint_and_broken_help_refused[missing, broken-help]` | root |
| нативный интерпретатор без `shebang` принят | `test_boundary::test_native_without_shebang_accepted`; неявно — каждая root-установка проходит `verify` со ссылками `venv/devtools/bin/python*` на нативный интерпретатор | юнит; root только неявно |
| запуск после удаления сборочного каталога и кеша | `test_runs_after_build_dir_and_cache_removed` (`dfprobe --help` и `dfprobe` от `df-build` без сети) | root |

*Наблюдение, границы, контроли*

| строка §3.8 | тесты | вид |
|---|---|---|
| root не открывает, не перечисляет и не исполняет объект источника на всём пути | наблюдения тестов приёма, очистки и маршрута (раздел 3) | root, наблюдение |
| двойники: root-потомок `/bin/cat`; root-процесс `ctypes libc.open`; `prepare-dir-reopen` | `test_root_access_observer_twins[root-child-cat, root-ctypes-open, prepare-dir-reopen, exec-source, hardlink-out, moved-out, rename-refused, symlink-out, held-fd-read, held-fd-readdir]` | root |
| предсуществующий `df-build` (UID 0, админ-группа, чужой дом, нет в `accounts.toml`) → отказ до сборки | `test_preexisting_build_user_refused_before_build[uid0, admin-group, foreign-home, not-in-accounts]` | root |
| утверждённый CLI в каталоге `df-build`, через ссылку, с ACL → отказ до исполнения | `test_untrusted_cli_refused_before_exec[builder-owned-parent, intermediate-link, acl-write]` | root |
| `df-build` не создаёт, не пишет, не переименовывает, не удаляет и не меняет права в релизе, журнале, `/etc/df` (и в административном коде) | `test_build_user_cannot_modify[release, journal, etc, admin × create, write, rename, delete, chmod]` (20), парная операция в `build_home` проходит | root |
| положительные контроли; сломанный `run_as` роняет приёмку; сеть доступна без ограничения и недоступна с ним | фикстура `controls`; `test_broken_run_as_fails_suite[always-1, whoami-fails, whoami-mismatch, write-fails, write-as-root, network-code, network-not-launched]`; `test_entrypoint_has_no_network` | root |

Не строки §3.8, а пункты из решений по задачам (R40, R26, Task 8a/9): `test_boundary_on_real_release`,
`test_admin_python_on_real_host`, `test_step2_and_lock_under_setgid_parents_real_linux`
(Ubuntu), `test_linux_loader_residuals` (Ubuntu), `test_gnu_padding_with_small_pipe` (Ubuntu),
`test_tar_failure_as_root`, `test_sudo_rules_real`, `test_posix_acl_makes_auth_unreadable_linux`,
`test_home_acl_facts_macos`, `test_darkfactory_command_works_and_is_immutable`,
`test_no_df_in_path`, `test_installed_python_has_no_tk_but_core_works`; в обычном прогоне —
`test_inputs::test_fetch_reference_sha_from_github` (`network`, R79: выборка sha с GitHub
`--depth=1` утверждённым git), `test_admin::test_uv_refuses_corrupt_download`,
`test_admin::test_admin_python_relocation_precondition`.

**Исходы живого писателя** (`junit-root.xml`, свойство `outcome`; оба исхода разрешены §3.8,
ни в одном root-only содержимое в итог не попало):

| случай | macOS | Ubuntu |
|---|---|---|
| `dir-to-link` | принято; изменение попало в поток 16/16; изменено 16/16 | отказ `copy: producer exited with code 1; stderr: /usr/bin/tar: ./s5: file changed as we read it`; изменено 16/16 |
| `link-retarget` | принято; изменение попало в поток 16/16; изменено 16/16 | отказ `copy: producer exited with code 1; stderr: /usr/bin/tar: ./s5: file changed as we read it`; изменено 16/16 |
| `file-to-fifo` | отказ `copy: unsupported member type b'6'; stderr:`; изменено 16/16 | отказ `copy: unsupported member type b'6'; stderr: /usr/bin/tar: ./s5: file changed as we read it`; изменено 16/16 |
| `hardlink-root-only-file` | отказ `copy: unexpected end of stream; stderr: tar: (null)`; изменено 16/16 | принято; изменено 0/16 |
| `saved-fd-writer` | принято | отказ `copy: producer exited with code 1; stderr: /usr/bin/tar: ./pad-grow: file changed as we read it` |
| `growing-file` | принято | отказ `copy: producer exited with code 1; stderr: /usr/bin/tar: ./pad-grow: file changed as we read it` |
| `growing-tree` | принято | отказ `copy: producer exited with code 1; stderr: /usr/bin/tar: ./grow: file changed as we read it` |

Ubuntu `hardlink-root-only-file`: ни одна из 16 жёстких ссылок не создана
(`protected_hardlinks`: `ln … Operation not permitted`), поэтому утверждение «tar обрывается
на root-only файле» измерено **только на macOS**. Отказ GNU tar `file changed as we read it`
зависит от того, успел ли писатель до чтения файла: исход одного случая может меняться от
прогона к прогону, тест принимает оба.

`test_build_user_after_seal_cannot_touch_destination`: ни одна попытка `df-build` создать или дописать файл в `releases/<id>` после `seal` не открыла его (свойство `tries`: macOS `{'EACCES': 18, 'ENOENT': 38, 'OPENED': 0, 'other': 0}`, Ubuntu `{'EACCES': 28, 'ENOENT': 82, 'OPENED': 0, 'other': 0}`); итог принят в обеих ячейках.

## 3. Наблюдатель

**Статус по ячейке.** macOS: 104 наблюдений, все `observed`; Ubuntu: 106, все
`observed` (`observe/*.json`). Из них маршрут повтора после прерывания (13-E) — 36 на ячейку
(`build-dir` 13 фаз, `build` 8, `seal` 8, `copy` 5, `verify` 2), двойники — 11 файлов на ячейку
(`prepare-dir-reopen` даёт два: `-clean` и `-reopen`), остальное — приём, очистка, `seal`,
сборка листьев (13-D). Ни одного `unobserved`: тест приёма, очистки или маршрута с
`unobserved` падает, а не проходит.

**Механизм (R98).**
- macOS — `eslogger open readdir exec --format json`, запущенный отсоединённо (свой родитель
  им приглушается); каждая строка несёт `global_seq_num`, разрыв — `unobserved`
  (`stats.seq_missing` = 0 во всех наблюдениях).
- Linux — `auditd`, правило `-a always,exit -F arch=b64 -S <вызовы> -F dir=<каталог> -F euid=0`
  на каждый источник и каталог меток; правило `never`/`exclude` на хосте или рост `lost` —
  `unobserved`.
- Сопоставление — по идентичности `(st_dev, st_ino)` объектов источника, перечисленных `lstat`
  до входа и при `watch()`, и по пути в написании на диске (алиасы регистра и нормализации —
  отказ). Цепочка родителей источника, написание и идентичность проверяются на входе и на
  выходе. Положительный контроль — уникальная метка в отдельном каталоге `control`, которую
  наблюдатель создаёт на входе и последним действием на выходе и обязан увидеть.
- Двойники в обеих ячейках дали нарушение там, где обязаны (`root-child-cat`,
  `root-ctypes-open`, `exec-source`, `hardlink-out`, `moved-out`, `prepare-dir-reopen`; на
  macOS ещё `held-fd-readdir`), и чистый `prepare_dir` — без нарушений (`observe/*.json`).

**Пределы, дословно из `Observation.limits` артефактов** (один набор во всех наблюдениях
ячейки):

- macOS (`eslogger`):
  - `before-watch`: «доступы до входа в наблюдение и до watch() не наблюдаются»
  - `created-moved-out`: «по идентичности (inode) сопоставляются только объекты источников, перечисленные при входе, и сам каталог, добавленный через watch(); остальные — созданные в источнике после входа или после watch(), а также бывшие в каталоге watch() ещё до вызова watch() — сопоставляются только по пути: перенесённые df-build в каталог вне источников, они дальше не прослеживаются»
  - `entry-exit-only`: «цепочка родителей, написание и идентичность источника проверяются на входе и на выходе; изменение, отменённое между двумя проверками, не видно»
  - `detached-process`: «проверка переживших процессов идёт по дереву потомков этого процесса; процесс, отсоединившийся от него (двойной fork/setsid с переходом к init/launchd), и его доступы после конечной метки не видны»
  - `held-fd-read`: «read/mmap через дескриптор, открытый до наблюдения, — не событие»
  - `symlink-out`: «open через ссылку источника на объект вне источников сообщается объектом цели — не нарушение»
  - `other-ops`: «наблюдаются только open, readdir, exec; stat/rename/unlink, clonefile/copyfile (копия содержимого без open) — нет»
  - `es-default-mutes`: «процессы из набора приглушения ES по умолчанию (если eslogger его не снимает) не видны — не измерено»
- Linux (`auditd`):
  - `before-watch`: «доступы до входа в наблюдение и до watch() не наблюдаются»
  - `created-moved-out`: «по идентичности (inode) сопоставляются только объекты источников, перечисленные при входе, и сам каталог, добавленный через watch(); остальные — созданные в источнике после входа или после watch(), а также бывшие в каталоге watch() ещё до вызова watch() — сопоставляются только по пути: перенесённые df-build в каталог вне источников, они дальше не прослеживаются»
  - `entry-exit-only`: «цепочка родителей, написание и идентичность источника проверяются на входе и на выходе; изменение, отменённое между двумя проверками, не видно»
  - `detached-process`: «проверка переживших процессов идёт по дереву потомков этого процесса; процесс, отсоединившийся от него (двойной fork/setsid с переходом к init/launchd), и его доступы после конечной метки не видны»
  - `held-fd-read`: «read/mmap через дескриптор, открытый до наблюдения, — не событие»
  - `symlink-out`: «open через ссылку источника на объект вне источников сообщается объектом цели — не нарушение»
  - `identity-scope`: «правила audit следят только за наблюдаемыми каталогами (источники, control, scope): видны доступы root к объектам под ними, и только к ним; объект источника, который df-build связал жёсткой ссылкой или перенёс за их пределы (например, в /var/tmp), root читает там незамеченным; без scope сопоставления по идентичности нет»
  - `other-ops`: «наблюдаются только creat,execve,execveat,getdents,getdents64,open,open_by_handle_at,openat,openat2; stat/rename/unlink, sendfile/copy_file_range по чужому дескриптору — нет»
  - `held-fd-getdents`: «getdents по удерживаемому дескриптору не наблюдается»
  - `b32-io-uring`: «32-битные вызовы (arch=b32) и open через io_uring не наблюдаются»

**Ограничения именно этого прогона.**
- **Linux: маршрут повтора без `scope`** (13-E). 36 наблюдений маршрута на Ubuntu шли без
  каталога `scope`, поэтому для них действует предел `identity-scope` в полном виде: видны
  только доступы под наблюдаемыми каталогами (держатели `releases/`, `staging/`,
  `staging/<id>`, карантин), объект, вынесенный `df-build` за их пределы, не прослеживается,
  сопоставления по идентичности вне них нет. Причина: со `scope=<R>` полная копия и digest
  релиза от root давали столько событий, что `ausearch` терял метки (`unobserved`), а
  переиспользованный inode удалённого дерева давал ложное нарушение. macOS не затронут:
  `eslogger` сопоставляет идентичности где угодно. Ни одна строка §3.8 этим не остаётся без
  доказательства: листовые наблюдения 13-D шли со `scope`.
- **Держатель вместо удалённого дерева** (13-D). Очистка удаляет источник, а удалённый
  источник наблюдатель держать не может, поэтому очистка, `seal` и повтор маршрута
  наблюдаются через каталог-держатель (`staging/<id>`, `releases/`, `.quarantine`,
  `build_home`). Исключение — только доступ, путь которого **ровно** путь держателя, и только
  названная операция (и одна относительная форма, ниже): `open`/`openat` держателя (закреплённый родитель §3.3 п. 2, `seal`),
  перечисление `.quarantine` повтором карантина (§3.3 п. 4); на маршруте держатели root
  (`releases/`, `staging/`, `staging/<id>`, карантин) допускают `open` и перечисление,
  `build_home` — только `open`. Маршрут допускает ещё и голое относительное имя `<id>` — ящик `staging/<id>`, когда
  `releases/<id>` не источник (`test_acceptance_root_install.py`, `RouteObserver`): `openat`
  по дескриптору держателя `staging/`, для которого `auditd` пишет в PATH только имя (на macOS
  `eslogger` даёт полный путь); в этом прогоне таких записей 4, все на
  Ubuntu, путь `c684cfda9cfd-linux-x86_64-1`, в наблюдениях `verify` после прерывания
  `build-dir`, `build`, `copy`, `seal` (`observe/test_rerun_after_interrupt_*-verify.json`).
  Любой иной доступ под держателем — нарушение. Непустые
  `violations` в `observe/*.json` (например, 45 файлов на macOS и 36 на Ubuntu
  вне двойников) — только такие доступы к самим держателям; тесты проверяют это отбором.
- **macOS: `sudo` открывает `build_home` с `euid = 0`.** `sudo` запуска от роли
  (`run_as`/`spawn_as`) открывает свой рабочий каталог `build_home` при каждом запуске
  (`exe` `/usr/bin/sudo` в нарушениях-кандидатах). Это не нарушение §3.8: источник —
  временный каталог **внутри** `build_home`, сам `build_home` — его родитель; на Linux
  (`unshare`/`setpriv`) такого открытия нет. Следствие — правило: рабочий каталог запуска от
  роли никогда не дерево сборщика (спека §3.8 «Наблюдение», раздел 7 п. 5).
- **Отложенные мелочи наблюдателя** (не закрыты, приняты): 13-B повторное ревью 2, m1 —
  `scope` не перепроверяется на выходе (цепочку `scope` может менять только root); m2 —
  `_Audit.watch` создаёт метку и при неудачном `auditctl`, вердикт всё равно `unobserved`.
  13-D повторное ревью, m1 — у непрерывных писателей (`saved-fd-writer`, `growing-file`,
  `growing-tree`) в принятой ветке нет утверждения «изменение попало в поток», только что
  запись шла во время копии; m2 — тест разветвлённого производителя опирается на ровно один
  вызов `end`.

## 4. Пределы (R80) и время

- **Прокси.** `env -i` сборки отбрасывает переменные прокси; runner-ы прокси не имеют — не
  испытано.
- **Подмодули и LFS.** Подмодули выходят пустыми, LFS остаётся указателями; у фикстурного
  проекта нет ни того, ни другого — не испытано.
- **`export-subst` и `filter="data"`** расходятся между сборкой и `fetch_reference`; у
  фикстурного проекта их нет — не испытано.
- **`[tool.uv]`** отбрасывается `UV_NO_CONFIG`; установка такого проекта отказывает (юнит
  `test_install_steps::test_uv_config_refused_before_build_lists_all_repos`); у фикстуры
  `[tool.uv]` нет.
- **Tcl/Tk (F-L1).** Находка 13-B: правило (в) отказало Tcl/Tk python-build-standalone на
  Linux (путь поиска `/tools/deps/lib`); решение владельца 25.10 — Python релиза без Tcl/Tk,
  правило не меняется. Ограничение поставки v1: `tkinter`/`turtle`/`idlelib` в Python релиза
  нет. R99: запускатели `bin/idle3*` и пакеты Tcl `itcl4.3.5`, `thread3.0.4` входят в названные
  компоненты. R100: Python профиля без измеренного списка (любой, кроме 3.12.13) — отказ шага
  `manifest`, строка README.
- **Xcode (R96).** Mac, у которого активный каталог разработчика — `Xcode.app` в
  `/Applications` (на образе — uid 501, `/Applications` с записью группы), получает отказ
  `approved_cli_problems`: `/usr/bin/git` ведёт туда через `xcrun`. Runner настроен на Command
  Line Tools (`step0/xcode-select.txt`: `before: /Applications/Xcode_16.4.app/Contents/Developer`, `after: /Library/Developer/CommandLineTools`), вердикт CLT — `[]` (`step0/root.json`). `make` CLT на runner-е — настоящий бинарь (не шим
  xcode-select), и `/usr/bin/make` принят: `test_o3_real_make_verdict_follows_clt` (обычный
  прогон macOS, pass) утверждает вердикт хоста в обе стороны — отказ «обёртка xcode-select»,
  где CLT `make` связан с libxcselect, и `[]`, где нет.
- **13-C.**
  - сбой ОС после `rename` домашнего каталога (l.1757–1758) испытан только эмуляцией;
    root-половина `os` — процесс, `sync`, сброс кешей и новый `boot_id`, без потери питания;
  - остатки загрузчика (R75) измерены `LD_DEBUG=libs` только на старте `--version`:
    библиотеки, которые CLI подгружает позже (например, `git-remote-https` — libcurl/TLS), не
    измерены;
  - остатки R26 пусты на штатном образе `ubuntu-24.04`: подкаталогов
    `glibc-hwcaps`/`tls` нет, записей `ld.so.cache` вне `ld.so.conf` нет, всё загруженное — в
    `SYSTEM_LIB_DIRS` (`loader/linux-residuals.json`: git — `libc`, `libpcre2-8`, `libz`; tar —
    `libacl`, `libc`, `libpcre2-8`, `libselinux`; uv — `libc`, `libdl`, `libgcc_s`, `libm`,
    `libpthread`, `librt`; плюс `ld-linux-x86-64`). Хост с пакетами hwcaps или иным кешем не
    измерялся; принятое владельцем ограничение остаётся.
- **13-D.**
  - «карантин» в §3.8 — дерево в карантине (`.quarantine/<id>.<n>`), а не каталог
    `.quarantine` (его перечисляет §3.3 п. 4); спека уточнена;
  - Ubuntu `hardlink-root-only-file` — 0/16 ссылок (`protected_hardlinks`), «tar обрывается»
    измерено только на macOS;
  - срок очистки растущего дерева: вариант `writer` — 1.96 с (macOS), 0.76 с (Ubuntu); вариант `deadline` (20 000 каталогов `000`, бюджет 5 с) — 5.13 с и 5.03 с, то есть отказ по сроку с убийством и сбором процессов очистителя; отказ замолчавшего и разветвлённого производителя — за 45.16/47.29 с (macOS) и 45.07/45.10 с (Ubuntu) при сроке 45 с (допуск — срок плюс сбор производителя); GNU-выравнивание при трубе 4096 байт (Ubuntu) — поток 16941056 байт принят за 5.20 с.
- **13-E.**
  - прерывание «внутри `build`» — отметка после настоящей сборки: окно, в котором `df-build`
    ещё пишет `releases/<id>` (повтор как `b-partial`, остаточный риск R33.F28), root-тестом
    не исполнено;
  - настоящий процесс `darkfactory doctor` доказывает только неизменность административного
    кода, административного Python и `bin`; уровень 1 и неизменность релиза — в процессе
    (`doctor.level1_checks` над снимком `<R>`), потому что `df.cli` в процессе команды читает
    `/` (раздел 7 п. 2);
  - проверка настоящего `doctor` сильна равенством «проверка за проверкой» с моделью
    (`id`, статус, причина), а не множеством `SEAM_CHECKS` (9 id: `etc.schemas`,
    `release.active`, `release.digest`, `release.admin`, `release.inputs`, `release.perms`,
    `release.boundary`, `trusted.paths`, `build.user`), которое лишь его верхняя граница.
    Измерено (`install/test_darkfactory_command_works_and_is_immutable.json`, поле
    `doctor_not_ok`, оба запуска, обе ячейки одинаково): не `ok` — 8 из 9: `etc.schemas`,
    `release.admin`, `trusted.paths`, `build.user` — «нарушено»; `release.digest`,
    `release.inputs`, `release.perms` — «данные недоступны»; `release.boundary` — «не
    проверено». `release.active` в списке не-`ok` нет;
  - `build-dir` наблюдается ровно 13 фазами (`OBSERVED_PHASES`) — намеренная растяжка:
    перестановка чтений классификации в `install.py` уронит тест;
  - Linux §3.4 (`boundary/linux-x86_64.json`): интерпретатор PBS с libpython не связан
    (`libpython_linked: false`; NEEDED — системные `libpthread`, `libdl`, `libutil`,
    `librt`, `libm`, `libc`; RUNPATH `$ORIGIN/../lib`); читается ли `lib/libpython3.12.so.1.0._pth`, не проверялось —
    проба выходит сразу, раз интерпретатор с libpython не связан (вывод, не замер); порог `uv`: путь `bin/python` до 124 байт — прямой `#!`, от
    125 — sh-трамплин, на обеих ОС одинаково — это константа `uv`, не предел ядра;
    `$ORIGIN` через ссылки `venv/devtools/bin/python*` остаётся внутри (все зависимости —
    системные sonames, выходов нет); `sys.path` запусков `-I` и `-P` совпадают на каждой ОС;
    состав `python/`, `python/<ключ>`, `python/<ключ>/bin` и тексты ссылок — как
    закреплено, `python/<ключ>/lib/lib` нет; на macOS — то же (интерпретатор с `@executable_path/../lib`),
    кроме `$ORIGIN` через ссылку: `origin_via_link` на macOS пуст — проба ищет только
    `$ORIGIN`/`@loader_path`, через ссылки там ничего не измерено.
- **Время.** Root-прогон: 15:17 (шаг 22:05:56–22:21:13; junit 915 с) (macOS), 11:15 (шаг 22:03:28–22:14:43; junit 674 с) (Ubuntu), предел job — 180 мин.
  Чистая установка от `manifest` до `commit-config done` (переход в `committed`) (`journals/test_clean_install_reaches_level1.jsonl`):
  13,5 с (macOS), 14,2 с (Ubuntu), 10 итераций `drive` (по одному действию от
  `build-dir` до `commit-config`). Время `full`/`intact` digest журнал отдельно не пишет;
  промежуток между `done` одного действия и `start` следующего (классификация с digest
  дерева) — не больше 0,47 с (macOS) и 0,70 с (Ubuntu), наибольший — после
  `register`/`activate`/`launcher`, когда классификация читает принятый релиз целиком.

## 5. Отказ tar от root (R81)

`test_tar_failure_as_root`, файл, нечитаемый для `df-build` (`junit-root.xml`, свойства
`tar_refusal`, `tar_stderr`):

- macOS (bsdtar 3.5.3): отказ `unexpected end of stream; stderr: tar: (null)`; stderr производителя — `tar: (null)`.
- Ubuntu (GNU tar 1.35): отказ `producer exited with code 2`; stderr производителя (две строки):

  ```
  /usr/bin/tar: ./src/secret: Cannot open: Permission denied
  /usr/bin/tar: Exiting with failure status due to previous errors
  ```

Отказ верен у обоих; текст bsdtar беден (`tar: (null)` не называет файла), GNU tar называет
файл и выходит с кодом 2.

## 6. Строка харнессов (R85)

Дословно из предполётной проверки Task 13: «изоляция харнессов: claude 2.1.309, codex 0.163.0
(macOS) — not verified; Linux — не снималось; живая приёмка модельных ролей не пройдена
(решение владельца 24.10)».

## 7. Решения владельца 26.10

Вынесенное владельцу по итогам прогона 40238123506 и решения от 26.10. Источник каждого
исхода — названный коммит ветки `feat/df-stage1-task14` (PR #633) и прогон `df-stage1` на нём,
а не прогон разделов 1–6.

1. **Правило (в) и обычные нативные колёса.** Замер этого прогона
   (`boundary/<platform>.json`, `native_wheel`): `pillow==11.0.0` в окружении Python релиза —
   Linux **10 проблем** (каждая библиотека `pillow.libs`, чья зависимость — другая вложенная
   библиотека: «зависимость … не найдена в системных каталогах»), macOS **1 проблема**
   (`PIL/.dylibs/libjpeg.62.4.0.dylib: путь поиска '/usr/local/lib': вне релиза и системных
   каталогов`). Решение: «сначала инвентаризация, затем решение … не исключать автоматически
   нужные зависимости и не разрешать любой soname, найденный где-нибудь внутри релиза».
   Исход: инвентаризация `core` (Task 14-B, `224c984`, доработка `35cacdf`; прогон
   [40277653777](https://github.com/andrei-shtanakov/devtools/actions/runs/40277653777), обе
   ячейки зелёные; таблица — раздел 8) — у всех 6 членов на обеих ОС правило (в) даёт 0
   проблем. Правило (в) **не меняется**; решение о (в) для текущих входов `core` не нужно.
   Входы замера — master членов на 26.10 (`CORE_PINS`), а не пины манифеста: смена пина
   требует повторной инвентаризации (пункт `TODO.md` `df-installer-stage1-live`).
2. **Раздвоенный корень.** Вход команды подставлял `_ROOT`, а `df.cli` в её процессе брал
   `default_layout()`. Решение: один корень. Исход: D2 (`a8cb485`) — вход передаёт
   `df.cli.main` раскладку своего корня, режимы входа без неё — ошибка программы, `SEAM_CHECKS`
   удалён, отчёт настоящего `doctor` равен уровню 1 проверка за проверкой; прогон
   [40270314652](https://github.com/andrei-shtanakov/devtools/actions/runs/40270314652)
   (голова `b214231`) зелёный в обеих ячейках. Набор ключевых аргументов вызова — замороженный
   межверсионный договор рядом со строкой `_ROOT` (спека §3.9).
3. **Отказ приманки с записью `ready`.** Установка отказывала текстом `build-user:
   постусловие не выполнено` без причины. Решение: отказ называет причину. Исход: D3
   (`57fb69a`) — «постусловие не выполнено: <причины>» (проблемы `verify_build_user`, а при
   их отсутствии — расхождение `whoami df-build` с записью); root-тест приманки сверяет текст
   строки журнала и stderr (раздел 8).
4. **Python релиза от root.** Решение (текст владельца): «Запрет запуска релизного Python от
   root объяснять прежде всего границей доверия: это код артефакта сборщика. Запись
   байткода — дополнительное последствие; -B не превращает такой запуск в допустимый».
   Исход: README, «Правила оператора».
5. **Рабочий каталог запуска от роли — вне дерева сборщика.** Решение: «Рабочий каталог вне
   дерева сборщика должен обеспечиваться запускателем, а README описывает это правило».
   Исход: README «Правила оператора»; спека §3.8 — обязанность запускателя, не оператора;
   план «Вне этого плана» — требование к `df-launch` этапа 2 с тестом этапа 2.
6. **Ограничение Xcode (R96).** Решение: «Ограничение Xcode лучше формулировать через
   фактическую причину: выбранный developer directory или его цепочка путей не проходит
   политику доверия. Не объявлять само имя Xcode.app причиной отказа. Поддерживаемый вариант с
   Command Line Tools описать отдельно». Исход: README «Утверждённые CLI», политика
   доверенных путей не меняется.
7. **Root-команды кода PR в CI.** Решение: «PR-код под sudo на одноразовых GitHub-hosted
   runners — считаю допустимым для этой приёмки».
8. **Правки подписанной спеки B rev 8 в этой ветке (e80295f).** Два разных класса:
   - (а) замеры и формулировки, **не меняющие ни одной проверки**: §3.4 — заметка о замере
     (интерпретатор PBS 3.12.13 на Linux с libpython не связан, запрет имени `lib`
     остаётся), порог трамплина назван порогом `uv` (124/125 байт), а не пределом ядра;
   - (б) уточнения §3.8 «Наблюдение» по реализованной проверке, **не меняющие ни одной
     проверки**: сопоставление по идентичности и написанию на диске (R93/R98), «карантин» =
     дерево в `.quarantine/<id>.<n>`, `build_home` — родитель источника, правило «рабочий
     каталог запуска от роли — не дерево сборщика» (26.10 — обязанность запускателя, п. 5);
   - **R101 — не редакционное уточнение, а явное ограничение того, что доказывает
     приёмка** (принято владельцем 26.10: «R101 принять как явное ограничение доказательства,
     а не редакционное уточнение. Формулировка "ни одна правка не меняет гарантию" здесь
     недостаточно точна»). Абзац «Наблюдатель не полон»: гарантия «root не открывает
     источник» доказана только в пределах `Observation.limits` (раздел 3), а не безусловно —
     по условию владельца 25.10 («I1–I3 закрыты или гарантия ограничена»). В спеке §3.8 это
     сказано одной фразой (26.10).

Замечание ревью 14-A (M1) внесено в спеку §3.9: вызов `df.cli.main` из входа несёт `layout`,
и этот набор ключевых аргументов — замороженный межверсионный договор, как строка `_ROOT`
(M13); входа до D2 быть не может — ни один релиз до D2 не поставлялся.

## 8. Изменения Task 14 в приёмке

Разделы 1–6 — evidence прогона 40238123506 (головы `2d2b8c4`) и не переписываются. Task 14
меняет приёмку так:

- **D2 снял шов** (`a8cb485`, прогон 40270314652). Раздел 4, 13-E описывает прежнее
  состояние: настоящий процесс `doctor` читал `/`, и его отчёт сравнивался с моделью над
  `Layout("/")` в пределах `SEAM_CHECKS`. Теперь `_doctor_reached` требует, чтобы отчёт
  настоящего `doctor --json` над `<R>` был равен уровню 1 в процессе проверка за проверкой
  (`id`, статус, причина), `level1 == "reached"` и код ровно `EXIT_REACHED`; тест подмены — отчёт
  настоящего процесса, код `EXIT_NOT_REACHED`, `level1 == "not reached"`; тест неизменности
  добавил digest всего дерева релиза под двумя настоящими запусками `doctor`/`models list`.
  Ни одно прежнее утверждение не ослаблено.
- **D3 назвал причину** (`57fb69a`). `test_preexisting_build_user_refused_before_build` для
  трёх приманок с записью `ready` сверяет строку журнала `StepFailed: build-user: постусловие
  не выполнено: <verify_build_user>` и тот же отказ в stderr; `not-in-accounts` — прежний
  отказ FOREIGN.
- **Инвентаризация нативных артефактов `core`** (14-B, `224c984` + `35cacdf`; прогон
  [40277653777](https://github.com/andrei-shtanakov/devtools/actions/runs/40277653777), голова
  `35cacdf`, обе ячейки success; первый прогон 40274198061 на `224c984` дал те же вердикты).
  Входы — master членов на 26.10 (`CORE_PINS` в `tests/df/test_acceptance_root_install.py`),
  **не пины манифеста** (его ещё нет). Для каждого члена от `df-build` с сетью: выборка
  маршрутом сборки, вердикты lock (`uv_config_problems`, `expected_distributions`), `uv sync`
  формой установщика с Python релиза 3.12.13 без Tcl/Tk, `distribution_problems`, правило
  (в) над `site-packages`. Правило (в) проверялось над `site-packages` члена в отдельном venv
  на Python релиза, **а не внутри установленной раскладки релиза** (замечание ревью 14-B, m3).
  Это данные артефакта (`boundary/<platform>.json`, ключ `core_inventory`), а не вердикт теста;
  тест падает только на сбое инфраструктуры или пустом замере (`vacuity_problems`).

  | член | SHA | группы | lock | ожид. распр. | macOS arm64: sync / (в) / нативных | Ubuntu x86_64: sync / (в) / нативных | нативные распределения |
  |---|---|---|---|---|---|---|---|
  | devtools | `d2f569eb` | `governance` | без проблем | 20 | ok / 0 / 2 | ok / 0 / 2 | pyyaml, rpds_py |
  | discovery | `3ebed2d9` | — | без проблем | 2 | ok / 0 / 1 | ok / 0 / 1 | pyyaml |
  | discovery-toolkit | `e5faa677` | — | без проблем | 1 | ok / 0 / 1 | ok / 0 / 1 | pyyaml |
  | disputatio | `920b63f4` | — | без проблем | 11 | ok / 0 / 2 | ok / 0 / 2 | pydantic_core, pyyaml |
  | steward | `007d0049` | — | без проблем | 15 | ok / 0 / 2 | ok / 0 / 2 | pyyaml, rpds_py |
  | spec-runner | `d586fbb6` | — | без проблем | 41 | ok / 0 / 5 | ok / 0 / 5 | cffi, cryptography, pydantic_core, pyyaml, rpds_py |

  `site_exists` — истина у всех 6 на обеих ОС; нативных файлов всего 13 на ячейку;
  `distribution_problems` пусты; каждый нативный файл отнесён к распределению через
  `RECORD`. Нативные колёса — опубликованные (теги `macosx_11_0_arm64`, `manylinux_*`; ни
  одного локально собранного `linux_x86_64` или `macosx_15_*`): pyyaml 6.0.3, rpds_py
  2026.6.3 и 0.30.0, pydantic_core 2.46.4 и 2.41.5, cffi 2.0.0, cryptography 50.0.0 (abi3) —
  по одному модулю расширения, без вложенных библиотек (`*.libs`, `.dylibs`) и путей поиска;
  зависимости — системные (macOS — `/usr/lib/*` в кеше dyld; Linux — sonames в
  `/lib/x86_64-linux-gnu`).

  **Контроль:** в том же прогоне образец `pillow` (`native_wheel`) по-прежнему даёт проблемы
  правила (в) — macOS 1, Linux 10, — то есть проверка над venv жива, и ноль у `core` —
  замер, а не слепое пятно метода.
