# Фикс-круг по итогам двух финальных ревью `feat/supersede-code`

Копия: `devtools-supersede-code`, ветка `feat/supersede-code`, старт с `0812f50`.
Прогон: `uv run --frozen --group governance pytest -q` (ожидание 986 passed).

Формат записи: находка → что сделал → как проверил (в т.ч. что тест краснел на
неисправленном коде).

## C-2 (blocker) + C-3 (minor) + F-03 (minor) — `_recover_commit`

(в работе)
</content>
</invoke>
### C-2 (blocker) — `_recover_commit` объявлял чужой собственную ветку ревизии

**Сделал.** В `_recover_commit` (`governance/task_bridge.py`) ветка «коммита ещё
нет» расширена: `if local is None or local == op.get("base_sha"): return`.
Проверил рецепт ревьюера: альтернатива `if op.get("tasks_blob") is None` —
слабее и НЕ эквивалентна (между `before_commit` и `commit_paths` блоб в
намерении уже есть, а коммита ещё нет: ветка снова была бы объявлена чужой).
Взял сверку с `base_sha`; на неё же написан отдельный тест.

**Тесты.**
- `test_recover_commit_branch_at_base_means_no_commit_yet` — окно
  `ensure_branch … commit_paths`, `tasks_blob` пуст;
- `test_recover_commit_branch_at_base_after_blob_written` — то же окно ПОСЛЕ
  `before_commit` (различает мой фикс от предложенного ревьюером);
- `test_supersede_resumes_when_branch_stands_on_base` — прод-путь целиком:
  `deliver_superseded` доводит ревизию (RC-PR 77), v3 не заводится.

**Краснели на старом коде:** да, все три (`RuntimeError: чужой коммит в ветке
spec/WS-alpha-7-tasks-v2` при откате правки кода при сохранённых тестах).

### C-3 (minor) — сообщение обещало remote, сверяется локальный head

**Сделал.** Убрал «remote/» из текста отказа; рядом — комментарий, почему
remote тут не сверяется (non-ff push, осознанное ограничение (б)).

**Тест.** `test_recover_commit_moved_branch_message_claims_only_local_head` —
утверждает `"remote" not in str(exc.value)`. Краснел на старом коде.

### F-03 (minor) — возврат `_recover_commit` в проде мёртв

**Сделал.** Честный гвард: аннотация `-> None`, все не-fail-closed строки
`return` без значения, докстринг переписан. **Выбор в пользу гварда, а не
«использовать возврат»:** после C-2 возврат не становится нужным — все строки
§I3.1, кроме fail-closed, ведут в одну и ту же детерминированную доставку из
намерения. «Принять коммит» из §I3.1 исполняет она сама: `commit_paths` на
неизменившемся файле второго коммита не делает, а `after_commit` durable пишет
`head_sha` именно этого коммита. Push конкретного SHA нашим примитивом
(`push_branch` пушит ветку) невыразим — то есть возвращать SHA было некуда.

**Тест.** Оба существующих теста на принимающую ветку теперь утверждают
`is None` (`test_recover_commit_accepts_matching_local_commit`, хвост
`test_supersede_records_tasks_blob_before_commit`) — мутация M11
(`return "phantom"`) с этими утверждениями краснеет.
