# Усиление тестов по находкам финального ревью (F-01…F-12)

Копия: worktree `devtools-supersede-code`, ветка `feat/supersede-code`.
База: `uv run --frozen --group governance pytest -q` → **1007 passed** (62 c);
цикл мутаций гоняется по `tests/test_governance_task_bridge.py` (156 тестов, 2.5 c)
с `PYTHONDONTWRITEBYTECODE=1` и очисткой `__pycache__` перед каждым прогоном.

Правится ТОЛЬКО `tests/test_governance_task_bridge.py`; поведение не меняется.
Мутации применяются точечной подменой строки и откатываются
`git checkout -- governance/` (после каждого цикла сверяется `git diff governance/`).

## Прогресс

| находка | статус |
|---|---|
| F-01 | закрылась раньше (фикс-круг) |
| F-02 | **закрыта** |
| F-04 | **закрыта** |
| F-05 | **закрыта** |
| F-06 | **закрыта** |
| F-07 | **закрыта** |
| F-08 | **закрыта** |
| F-09 | **закрыта** |
| F-10 | **закрыта** |
| F-11 | **закрыта** |
| F-12 | **закрыта** |

## Журнал

### F-01 (major) — закрылась предыдущим фикс-кругом

Мутация M08 (`if local and local != head:` → `if False and ...`) на текущем HEAD
**краснеет**: падает `test_recover_commit_moved_branch_message_claims_only_local_head`
(«DID NOT RAISE RuntimeError»). Тест появился в фикс-круге вместе с гвардом
«ветка стоит на base ⇒ коммита ещё нет» и бьёт ровно по этой строке.
Ничего не дописывал.

### F-02 (major) — закрыта

- Мутация **M09** (`if parent == base_sha and blob == tasks_blob:` → без условия
  по блобу) на HEAD — **зелёная**, 156 passed. Мнимое покрытие подтверждено.
- Добавлен `test_recover_commit_refuses_right_parent_wrong_blob`: родитель СОВПАЛ
  (`base1`), блоб чужой (`blobX`) ⇒ `raises(match="чужой коммит")`.
- С тестом M09 **краснеет** (`DID NOT RAISE`), падает ровно новый тест.

### F-04 (blocker) — закрыта

Стаб `_ProvOps` перестал быть одноответным: у него появился параметр `facts`
(дефолт — прежний полный `_FULL_FACTS`, вынесен в модульную константу).

- **M33** (проверка вмерженности найденного PR → `if False`) на HEAD — зелёная.
  Закрыта `test_provenance_found_pr_not_merged_refuses`
  (`facts={"state": "OPEN", ...}` ⇒ `match="найден по коммиту"`); с ним M33 красная.
- **M35** (проверка полноты подписи → `if False`) на HEAD — зелёная.
  Закрыта параметризованным `test_provenance_incomplete_signature_refuses`
  (`mergedBy=None, mergedAt=None`; оба входа — поиск и явный `--approval-pr`)
  ⇒ `match="подпись штампа неполна"`; с ним M35 красная **дважды** (обе ветки).

### F-05 (major) — закрыта

- **M30** (снят `state == "MERGED"` из фильтра) и **M31** (снят
  `baseRefName == base_ref`) на HEAD — обе зелёные.
- Добавлены `test_provenance_filter_ignores_open_pr_on_same_commit`
  (второй кандидат — OPEN) и
  `test_provenance_filter_ignores_pr_merged_into_other_base`
  (второй кандидат вмержен в `release`): оба обязаны ПРОЙТИ и вернуть 403.
- На мутантах вход даёт двух кандидатов ⇒ отказ: M30 роняет первый тест,
  M31 — второй. Каждая мутация роняет ровно свой.

### F-06 (major) — закрыта

- **M34** (проверка base у явного `--approval-pr` → `if False`) на HEAD — зелёная.
- Добавлен `test_provenance_explicit_flag_refuses_other_base`
  (`facts` вмержен, но `baseRefName="release"`) ⇒ `match="нацелен в"`;
  с ним M34 красная.

Прогон группы: `1014 passed`. `git diff governance/` пуст.
Линт: два E501/F841 — **предсуществующие** (строки 292 и 4048), проверено
прогоном ruff по HEAD без моих правок; новых нарушений нет.

### F-07 (major) — закрыта

Общий корень находки: ни в одном тесте в леджере не было ДВУХ ревизий сразу.

- **M22** (`reversed(_revisions(state))` → прямой порядок) и **M23**
  (`revs[-1][0] + 1` → `revs[0][0] + 1`) на HEAD — обе зелёные.
- В `test_last_delivery_prefers_highest_revision` добавлена третья запись
  (v3, PR 11) — теперь `[-1]` и `[0]` различимы. Добавлен
  `test_last_delivery_prefers_highest_completed_over_abandoned`
  (v2/v3 completed + v4 abandoned ⇒ ожидается v3): попутно закрывает
  половину принятого ограничения (г).
- В `test_revision_numbering_starts_at_two_and_grows` — v2, v3 ⇒ 4, и
  abandoned v4 ⇒ 5 (терминальная запись тоже занимает номер).
- M22 роняет оба `_last_delivery`-теста, M23 — тест нумерации.

### F-08 (blocker) — закрыта

Операторский контур `--abandon-revision N` → `--supersede` теперь исполняется.

- Канарейки на HEAD: **M50** (`raise` в начале ветки `abandon_and_next`),
  **M49** (`_abandon_revision` → `_complete_revision`), **M51** (`raise` вместо
  пропуска abandoned-ревизии в цикле) — все три зелёные, ветки мертвы.
- `test_supersede_abandons_shifted_revision_and_starts_next`: started-ревизия v2
  с позапрошлым `base_sha` и без PR ⇒ v2 становится `abandoned` с причиной
  «base сдвинулся», заводится v3 (`supersedes: 1`, `ensure_branch` её ветки),
  возвращается её PR. Роняет M49 и M50.
- `test_supersede_skips_abandoned_revision`: v2 уже `abandoned`, а на её ветке
  подан открытый PR #999. Пропуск наблюдаем: разбирайся запись — `find_pr`
  нашёл бы PR и переиздание отказало бы «решите судьбу PR явно». Утверждается
  и то, что `find_pr` по ветке v2 не звался вовсе. Роняет M51.

### F-09 (major) — закрыта

- **M55** (удалён `ops.checkout_and_pull` — первый шаг `deliver_superseded`) на
  HEAD — зелёная.
- Предложенное ревьюером `ops.calls[0] == ("checkout_and_pull", "master")` —
  ЛОВУШКА: `deliver()` делает свой `checkout_and_pull` с теми же аргументами,
  и на мутанте он просто становится нулевым. Тест бы прошёл.
- Сделано иначе, по наблюдаемому факту: `test_supersede_refreshes_base_before_
  reading_facts` даёт стаб, чей `checkout_and_pull` ПРИВОДИТ спеку в дереве к
  base (`version: 7`) — как настоящий `git pull`. С освежением
  `_previous_tasks_version` читает 7 и доставка идёт версией 8; без него — 1 и 2.
  M55 роняет ровно этот тест.

### F-12 (major) — закрыта

- **M54** (подменены и ref, и путь у `show_file`) и контрольная **M54b**
  (подменён ТОЛЬКО ref) на HEAD — обе зелёные: оба стаба игнорировали аргументы.
- `_ShowFileOps` и `_SupersedeOps.show_file` перестали быть одноответными:
  пишут вызов в `calls` и отдают текст ТОЛЬКО за спеку в base
  (`(base_sha, "spec/WS-alpha-7-tasks.md")`), иначе `None` — как `git show`.
- В `test_previous_dag_derived_from_bundle_composition` добавлено
  `assert ops.calls == [("show_file", _BASE_SHA, _SPEC_REL)]`.
- M54 и M54b роняют по два теста каждая (юнит `_previous_dag` + сквозной
  `test_supersede_changed_anchor_opens_new_branch_and_pr`).

Прогон группы: `1018 passed`. `git diff governance/` пуст.

### F-10 (major) — закрыта

- **M63** (`legacy_bundle=args.legacy_bundle` → `None`), **M64**
  (`approval_pr=args.approval_pr` → `None`), **M65** (`args.reason` → литерал)
  на HEAD — все зелёные.
- `test_cli_supersede_calls_deliver_superseded` теперь записывает ВСЕ три
  аргумента (`("r-recon", None, None)`) — голая форма по-прежнему покрыта.
- Новый `test_cli_supersede_passes_flags_through`: `--approval-pr 500
  --legacy-bundle 5` ⇒ `("r-recon", 5, 500)`. Роняет M63 и M64.
- В `test_cli_abandon_revision_marks_and_returns_zero` добавлено
  `saved["reason"] == "PR закрыт вручную"`. Роняет M65.
- Побочно снят предсуществующий F841 (неиспользуемая `state` в CLI-тесте) —
  строка всё равно переписывалась.

### F-11 (major) — закрыта

- **M60** (`active = _dag_for(legacy_bundle)` → `_dag_for(None)`) на HEAD —
  зелёная: `deliver_superseded` ни разу не звался с `legacy_bundle != None`.
- `test_supersede_legacy_bundle_uses_its_own_dag`: бандл эры до acceptance
  (00/10/15/20/30, `DECOMPOSITION_MD_LEGACY5`), вызов с `legacy_bundle=5` ⇒
  в намерении лежит `_BUNDLE_DAG_LEGACY5`, `dag_source: derived_from_spec`,
  провенанс спрошен по `30-decomposition.md` этого бандла. На мутанте активным
  становится полный DAG, §I8 не сходится и переиздание отказывает.
- Попутно `_ProvOps.last_commit_touching` перестал игнорировать аргумент
  (список `touched`) — это удерживает и вывод `anchor_rel` из терминального
  узла активного DAG: контрольная мутация `anchor_rel` → `00-charter.md`
  краснеет (раньше — нет).

Прогон группы: `1020 passed`. `git diff governance/` пуст.
Линт: остался ОДИН предсуществующий E501 (строка 292, чужой тест);
предсуществующий F841 снят попутно.

## Итоговый сводный прогон мутаций (по committed-состоянию тестов)

Каждая мутация применялась к чистому HEAD, гонялся весь
`tests/test_governance_task_bridge.py`, затем `git checkout -- governance/`.

| мутация | находка | итог |
|---|---|---|
| M08 guard «двигали снаружи» удалён | F-01 | КРАСНАЯ (1) |
| M09 условие по `tasks_blob` удалено | F-02 | КРАСНАЯ (1) |
| M22 `reversed(_revisions)` → прямой порядок | F-07 | КРАСНАЯ (2) |
| M23 `revs[-1][0]+1` → `revs[0][0]+1` | F-07 | КРАСНАЯ (1) |
| M30 снят `state == "MERGED"` из фильтра | F-05 | КРАСНАЯ (1) |
| M31 снят `baseRefName == base_ref` | F-05 | КРАСНАЯ (1) |
| M33 вмерженность найденного PR → `if False` | F-04 | КРАСНАЯ (1) |
| M34 base у `--approval-pr` → `if False` | F-06 | КРАСНАЯ (1) |
| M35 полнота подписи → `if False` | F-04 | КРАСНАЯ (2) |
| M49 `_abandon_revision` → `_complete_revision` | F-08 | КРАСНАЯ (1) |
| M50 канарейка в ветке `abandon_and_next` | F-08 | КРАСНАЯ (1) |
| M51 канарейка на пропуске abandoned | F-08 | КРАСНАЯ (1) |
| M54 ref+путь `show_file` подменены | F-12 | КРАСНАЯ (3) |
| M55 `ops.checkout_and_pull` удалён | F-09 | КРАСНАЯ (1) |
| M60 `_dag_for(legacy_bundle)` → `_dag_for(None)` | F-11 | КРАСНАЯ (1) |
| M63 `legacy_bundle=args...` → `None` | F-10 | КРАСНАЯ (1) |
| M64 `approval_pr=args...` → `None` | F-10 | КРАСНАЯ (1) |
| M65 `args.reason` → литерал | F-10 | КРАСНАЯ (1) |

Дополнительно (не находка, побочный эффект F-11): контрольная мутация
`anchor_rel` → `00-charter.md` теперь тоже красная (3 теста); до правки —
зелёная.

Финальный прогон: **1020 passed**. `git status` чист, `git diff governance/` пуст.
Дефектов ПОВЕДЕНИЯ (в отличие от покрытия) ни одна находка не вскрыла.
