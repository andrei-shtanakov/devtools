# Мутационная чувствительность: BEH-08, BEH-19

Материалы TASK-002 (DT-03). Обе записи ниже — временные мутации, снятые и
откаченные в одной сессии; итоговый дифф темы (см. `delivery-diff.md`,
TASK-003) не несёт ни одной их строки (**DEL-32**, проверено `git diff` до
записи этого файла — пусто по обоим тронутым модулям).

Дерево, на котором сняты оба прогона: коммит `735e01f8005a125efdb2eafddea1e4b69d310681`,
ветка `task/task-002`, момент снятия `2026-09-27T18:23:20Z`, Python 3.13.7.
Команда прогона в обоих случаях — `uv run pytest <файл> -q` (там, где нужен
список имён — `--tb=no -q`, чтобы получить чистый `FAILED …` построчно).

## BEH-08: выживший мутант доказывает, что источник имён один

**Подменялось.** `governance/bundle_dag.py:63`, тело `bundle_composition` —
единственный источник правила «какие `.md` каталога бандла суть узлы»
(**DEL-01**). Строка

```python
known = {fname for fname, _ in BUNDLE_DAG}
```

временно заменена на заведомо неполную

```python
known = {fname for fname, _ in BUNDLE_DAG[:-1]}  # MUTATION-BEH-08
```

— терминальный узел (`30-decomposition.md`) выпадает из множества известных
имён. И `check_bundle_composition` (`bundle_dag.py`), и вывод состава для
§I8 (`_previous_dag` → `task_bridge.py`) берут состав именно отсюда
(`bundle_composition`/`_bundle_composition`-алиас) — оба потребителя одной и
той же функции, ничего не пересчитывают сами.

**Прогонялось.**

```
uv run pytest tests/test_governance_bundle_dag.py --tb=no -q
uv run pytest tests/test_governance_task_bridge.py --tb=no -q
```

**Покраснело.** Оба файла сразу, как и требует сценарий:

`tests/test_governance_bundle_dag.py` — 2 упавших из 8 (6 passed, 2 failed
in 0.35s):

- `test_full_mode_is_unchanged`
- `test_new_node_name_moves_both_composition_boundaries`

`tests/test_governance_task_bridge.py` — 160 упавших из 351 (189 passed,
2 skipped, 160 failed in 1.98s). Полный список (среди них — прямо
§I8-сценарии BEH-01…06, 11, 13, 14, 18, а также широкий шлейф сценариев
переиздания/replace/branch-cleanup, которые тоже строят полный
шестиузловой каталог через тот же общий обход):

- `test_abandoned_replacement_hands_its_obligation_to_the_next_revision`
- `test_anchor_carries_its_canonization_epoch`
- `test_base_moved_between_the_gate_and_delivery_refuses`
- `test_beh01_stray_readme_does_not_change_supersede_outcome`
- `test_beh02_author_note_shaped_like_a_node_does_not_change_outcome`
- `test_beh03_i8_check_runs_and_passes_with_stray_file`
- `test_beh04_real_mismatch_still_refuses_with_and_without_stray_file`
- `test_beh05_refusal_is_attributed_to_i8_not_composition_check`
- `test_beh06_no_skip_word_on_either_settled_outcome`
- `test_beh11_word_present_iff_comparison_was_cancelled`
- `test_beh13_reasons_are_distinguishable_and_do_not_claim_a_change`
- `test_beh14_historical_v1_without_dag_reaches_i8`
- `test_beh18_hermetic_and_uses_real_directories`
- `test_branch_delete_failure_is_visible_but_does_not_undo_delivery[local-delete-failed]`
- `test_branch_delete_failure_is_visible_but_does_not_undo_delivery[origin-delete-failed]`
- `test_branch_disappearing_during_delete_is_a_completed_cleanup[local-disappeared]`
- `test_branch_disappearing_during_delete_is_a_completed_cleanup[origin-disappeared]`
- `test_branch_is_dropped_only_where_head_matches_the_revoked_revision[absent]`
- `test_branch_is_dropped_only_where_head_matches_the_revoked_revision[both-diverged]`
- `test_branch_is_dropped_only_where_head_matches_the_revoked_revision[clone-diverged]`
- `test_branch_is_dropped_only_where_head_matches_the_revoked_revision[origin-diverged]`
- `test_branch_is_kept_when_intent_carries_no_head_of_the_revoked`
- `test_branch_replaced_during_failed_delete_is_not_touched[local-replaced]`
- `test_branch_replaced_during_failed_delete_is_not_touched[origin-replaced]`
- `test_branch_state_unavailable_after_failed_delete_is_not_success[local-unavailable]`
- `test_branch_state_unavailable_after_failed_delete_is_not_success[origin-unavailable]`
- `test_branch_state_unavailable_before_delete_skips_the_effect[local-unavailable]`
- `test_branch_state_unavailable_before_delete_skips_the_effect[origin-unavailable]`
- `test_bridge_still_delivers_a_versionless_bundle`
- `test_check_approved_accepts_spec_runner_stamp_unchanged`
- `test_check_approved_refuses_draft`
- `test_check_approved_refuses_foreign_first_trace`
- `test_check_approved_refuses_stale_pin`
- `test_cli_supersede_delivery_is_announced`
- `test_content_anchor_changes_with_node_body`
- `test_content_anchor_does_not_leak_signature_through_pins`
- `test_content_anchor_includes_exact_discovery_source_bytes`
- `test_content_anchor_is_signature_free`
- `test_content_anchor_names_missing_discovery_source`
- `test_debt_status_moves_the_anchor`
- `test_deliver_approve_lays_missing_profile_and_names_next_step`
- `test_deliver_approve_opens_pr`
- `test_deliver_approve_rerun_updates_existing_pr`
- `test_deliver_approve_runs_over_unapproved_dag`
- `test_deliver_approve_unstamped_refuses_before_ops`
- `test_deliver_commits_s8_evidence_with_tasks`
- `test_deliver_default_generated_at_has_utc_offset`
- `test_deliver_does_not_check_verifies_path_existence_on_disk`
- `test_deliver_for_run_carries_nothing`
- `test_deliver_for_run_reconciled_pr_records_anchor_from_head`
- `test_deliver_for_run_records_anchor_of_stamp`
- `test_deliver_for_run_refuses_missing_s8_evidence_before_write_ahead`
- `test_deliver_for_run_write_ahead_op_and_completion`
- `test_deliver_full_dag_embeds_acceptance_section`
- `test_deliver_full_dag_renders_via_render_tasks_dt`
- `test_deliver_keeps_identical_stage_profile_out_of_commit`
- `test_deliver_legacy_5_has_no_acceptance_section`
- `test_deliver_legacy_verify_dt_without_verifies_still_delivers`
- `test_deliver_reads_bundle_only_after_base_checkout`
- `test_deliver_reads_design_only_after_base_checkout`
- `test_deliver_refuses_bare_file_target_for_exunit_before_branch`
- `test_deliver_refuses_hand_edited_stage_profile_before_branch`
- `test_deliver_verify_dt_now_delivers`
- `test_deliver_writes_spec_and_opens_pr`
- `test_delivered_content_anchor_preserves_exact_source_from_pr_head`
- `test_delivery_refuses_when_the_carry_writer_drops_a_deliverable`
- `test_dt_path_skips_merge_featureless`
- `test_explicit_allowlist_unblocks_only_the_named_login`
- `test_forward_link_is_status_agnostic`
- `test_gate_judges_base_not_the_worktree`
- `test_gate_refusal_on_changed_discovery_source_is_not_retryable`
- `test_gate_refusal_on_debt_leaves_the_ledger_byte_identical`
- `test_gate_refusal_on_unresolved_is_not_a_debt`
- `test_i3_does_not_fail_closed_on_replaced_revision`
- `test_i3_forward_link_requires_matching_pr_number`
- `test_i3_still_fails_closed_on_closed_pr_without_forward_link`
- `test_legacy_5_exact_composition`
- `test_legacy_5_goes_dt_path_with_graph_validation`
- `test_legacy_5_invalid_dt_graph_refuses`
- `test_legacy_v1_after_full_approval_delivers_once_and_writes_v2`
- `test_legacy_v1_with_migration_debt_refuses_at_the_gate`
- `test_merged_revoked_pr_mid_flight_names_the_way_out`
- `test_noop_over_honest_dag_says_nothing_extra`
- `test_noop_survives_unreadable_base_without_becoming_a_refusal`
- `test_obligation_is_discharged_when_revoked_pr_gets_merged`
- `test_obligation_survives_abandon_inside_the_same_call`
- `test_pending_review_is_a_draft_not_a_verdict`
- `test_previous_dag_derived_from_bundle_composition`
- `test_prospective_anchor_writes_nothing`
- `test_reapproval_without_content_change_keeps_the_anchor`
- `test_red_checks_and_plain_comments_do_not_block_replacement`
- `test_repeat_with_the_flag_continues_the_same_revision`
- `test_replace_accepts_pr_closed_by_operator`
- `test_replace_close_revalidates_between_windows`
- `test_replace_closes_pr_before_creating_the_new_one`
- `test_replace_delivers_v4_closes_pr_and_drops_branch`
- `test_replace_fails_closed_on_threads_and_unknowns[open-thread]`
- `test_replace_fails_closed_on_threads_and_unknowns[reviews-unknown]`
- `test_replace_fails_closed_on_threads_and_unknowns[threads-unknown]`
- `test_replace_is_the_only_exit_from_the_defective_pr`
- `test_replace_refuses_merged_pr_unconditionally`
- `test_replace_refuses_on_human_review`
- `test_replace_refuses_on_review_from_the_review_contour_too`
- `test_replace_refuses_v1_and_unfinished_and_missing`
- `test_replace_refuses_when_a_later_revision_is_not_this_replacement`
- `test_replace_refuses_when_close_fails`
- `test_replace_refuses_when_head_diverged`
- `test_replace_review_block_ignores_review_login_env`
- `test_replacement_may_repeat_version_and_content_of_the_revoked`
- `test_revoked_revision_with_open_pr_is_never_returned`
- `test_stale_clone_is_refreshed_before_the_gate`
- `test_supersede_abandons_shifted_revision_and_starts_next`
- `test_supersede_after_merged_revision_is_noop_again`
- `test_supersede_after_merged_revision_starts_next_one`
- `test_supersede_asks_github_once_per_revision`
- `test_supersede_carry_source_is_base_not_worktree`
- `test_supersede_changed_anchor_opens_new_branch_and_pr`
- `test_supersede_completes_started_revision_with_merged_pr`
- `test_supersede_equal_content_anchor_is_traceless_noop`
- `test_supersede_fail_closed_leaves_no_branch_commit_or_ledger_entry`
- `test_supersede_fails_when_actual_anchor_differs`
- `test_supersede_ignores_first_delivery_pr_when_revision_is_last`
- `test_supersede_is_noop_when_bundle_and_source_are_unchanged`
- `test_supersede_legacy_bundle_drives_the_whole_path`
- `test_supersede_legacy_without_content_anchor_records_unavailable`
- `test_supersede_noop_reachable_with_draft_node_outside_signed_nodes`
- `test_supersede_preserves_execution_state_of_unchanged_tasks`
- `test_supersede_records_commit_facts_before_push`
- `test_supersede_records_tasks_blob_before_commit`
- `test_supersede_refreshes_base_before_reading_facts`
- `test_supersede_refuses_merged_pr_without_recorded_head_sha`
- `test_supersede_refuses_open_pr_without_recorded_head_sha`
- `test_supersede_refuses_resume_when_branch_missing_locally`
- `test_supersede_refuses_resume_with_different_dag`
- `test_supersede_refuses_when_base_tasks_file_cannot_be_read`
- `test_supersede_reports_zero_when_base_tasks_file_is_absent`
- `test_supersede_restamp_keeps_node_version`
- `test_supersede_resume_refuses_foreign_commit`
- `test_supersede_resume_reproduces_the_same_bytes`
- `test_supersede_resumes_started_revision_with_open_pr`
- `test_supersede_resumes_started_revision_with_open_pr_moved_branch`
- `test_supersede_resumes_started_revision_without_pr`
- `test_supersede_resumes_when_branch_stands_on_base`
- `test_supersede_returns_existing_pr_when_revision_completed`
- `test_supersede_right_after_delivery_is_traceless_noop`
- `test_supersede_skips_abandoned_revision`
- `test_supersede_unavailable_without_content_anchor[ключа-нет]`
- `test_supersede_unavailable_without_content_anchor[старый-anchor-не-в-счёт]`
- `test_supersede_unavailable_without_content_anchor[content-anchor-null]`
- `test_supersede_version_is_monotonic`
- `test_supersede_write_ahead_survives_delivery_failure`
- `test_supersedes_skips_replaced_revision_on_later_reissue`
- `test_traceless_noop_names_the_debt_nodes`
- `test_v2_bundle_is_delivered_with_its_deliverables`
- `test_v2_bundle_without_delivers_is_refused_by_the_guard_not_the_bridge`
- `test_wave_fate_is_recorded_before_any_delivery_effect`
- `test_window_a_intent_written_pr_still_open`
- `test_window_b_pr_closed_no_pr_anywhere`
- `test_window_c_completed_revision_still_drops_branch`
- `test_window_c_pr_created_branch_alive`

**Осталось зелёным.** Ни одного сценария, ни одного файла: оба потребителя
(`check_bundle_composition` и вывод состава §I8 через `_previous_dag`)
покраснели в одном и том же прогоне мутации. Ветки «кандидатов путь A
зелёный, путь B красный» или наоборот — не наблюдалось.

**DEL-30 (по существу).** Путь, который остался бы зелёным при неполном
источнике, означал бы, что источник имён на самом деле не один (у одной из
двух функций — свой, случайно совпадающий перечень). Такого пути на этой
мутации нет: он зафиксирован бы здесь поимённо и как невыполненный критерий
темы (**M-01**, baseline 2 → target 1 не достигнут), а не объяснён прозой о
похожести двух перечней при чтении глазами — прозы этот пункт не несёт,
потому что предмету не за что отвечать.

Реверт мутации — возврат `bundle_dag.py:63` к `known = {fname for fname, _
in BUNDLE_DAG}`; оба набора после реверта зелёные (357 passed, 2 skipped за
3.36s суммарно по обоим файлам — совпадает с чистым деревом до мутации).

## BEH-19: проверки держат смысл сообщения, а не его букву

**Подменялось.** `governance/task_bridge.py:3851`, единственная точка
печати слова о пропуске §I8 (**DEL-12**):

```python
print(f"сверка §I8 не производилась (comparison: unavailable): {dag_reason}")
```

Две независимые редактуры по очереди (каждая — на чистом дереве, снята и
откачена перед следующей).

### Редактура A — переформулировка, упоминание сохранено

```python
print(f"внимание: сверка §I8 не производилась (comparison: unavailable): {dag_reason}")  # MUTATION-BEH-19-A
```

Добавлен вводный маркер `внимание: `; сама форма — «какая сверка не
производилась (§I8), что произошло (`comparison: unavailable`), почему» —
и упоминание §I8, и факт пропуска сохранены дословно.

**Прогонялось.** `uv run pytest tests/test_governance_task_bridge.py -q`.

**Результат.** 349 passed, 2 skipped in 2.95s — **побайтово то же число**,
что и на чистом дереве до правки (проверено отдельным прогоном того же
файла на нетронутом дереве: тоже 349 passed, 2 skipped). Ни один тест не
покраснел.

### Редактура B — упоминание убрано

```python
print(f"note: {dag_reason}")  # MUTATION-BEH-19-B
```

Ни «§I8», ни «не производилась», ни `comparison: unavailable` в строке
больше нет — только причина.

**Прогонялось.** `uv run pytest tests/test_governance_task_bridge.py --tb=no -q`.

**Покраснело.** 5 упавших из 351 (344 passed, 2 skipped, 5 failed in 3.47s):

- `test_beh09_each_unavailable_branch_prints_word_with_reason`
- `test_beh10_skip_does_not_claim_composition_matched`
- `test_beh11_word_present_iff_comparison_was_cancelled`
- `test_beh12_word_names_check_state_and_reason`
- `test_beh13_reasons_are_distinguishable_and_do_not_claim_a_change`

Все пять — сценарии, которые ищут строку по маркеру `_SKIP_I8_PREFIX`
(наличие слова, его форма, причина); тесты `not in` (BEH-01…06, 14…18),
проверяющие ОТСУТСТВИЕ слова на состоявшихся путях, не тронуты — им
удаление маркера ничем не мешает, что ожидаемо и не есть находка.

**Вывод.** Граница проходит по «вывод называет §I8 и говорит, что сверки не
было» (редактура A — сохранено, зелено), а не по точному совпадению строки
(редактура B — убрано, красно): ровно то, что требует **NFR-04**/BEH-19.

Реверт — возврат `task_bridge.py:3851` к исходной строке печати; набор
после реверта снова 357 passed, 2 skipped суммарно по обоим файлам (как и
до BEH-08 выше).

## Проверка чистоты диффа (перед записью этого файла)

```
$ git diff governance/bundle_dag.py governance/task_bridge.py
$ uv run pytest tests/test_governance_bundle_dag.py tests/test_governance_task_bridge.py -q
357 passed, 2 skipped in 3.48s
```

`git diff` по обоим модулям — пуст: ни одна из мутаций BEH-08/BEH-19 не
осталась в дереве (**DEL-32**).
