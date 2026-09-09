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
| F-07 | — |
| F-08 | — |
| F-09 | — |
| F-10 | — |
| F-11 | — |
| F-12 | — |

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
