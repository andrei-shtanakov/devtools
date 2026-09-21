"""Runner: шаговая машина S0–S7 governance-конвейера (спека §4/§5).

Единственная точка внешних эффектов — переданный `Ops` (T3): этот модуль не
делает ни одного `subprocess`/сетевого вызова сам. Каждый шаг с внешним
эффектом ведётся как `pending → started → completed` со стабильным operation
key в `RunState.ops` (T2); resume (повторный `advance()` над загруженным
состоянием) всегда начинается с reconciliation по фактическому состоянию,
описанной для каждого шага ниже (спека §4, дословно перенесено в код).

S8 (authoritative-фиксация после мержа) продолжает `advance()` сразу после
успешного `merge` в рамках того же вызова (агентский мерж). Когда S7 оставил
PR человеку (`waiting_human_merge`), S8 не запускается сам — явный `resume()`
проверяет факт мержа и выполняет только его. Провал S8 — терминально:
`merged_unverified` навсегда, с remediation-issue по ADR-ECO-006; дальнейшее
продвижение — только через `verify()`, дочерний run с `remediated_by`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from governance import (
    acceptance_guard,
    authority_root,
    brief_input,
    bundle_dag,
    bundle_inputs,
    decomposition_guard,
    design_guard,
)
from governance import interview as iv
from governance.merge_gate import PrFacts, decide
from governance.facts import Outcome
from governance.stale_adapter import blob_sha1
from governance.ops import (
    _AUTHOR_DSL, ENGINEER_BLOCKED, REVIEW_BARRIER_EXIT, REVIEW_BARRIER_STOP,
    Ops, RealOps, disp_agent,
)
from governance.policy_sources import (
    PREFLIGHT_PROCEDURE_HINT,
    build_authority,
    load_safety,
    target_profile_declares,
)
from governance.run_state import (
    RunState,
    all_run_ids,
    load,
    new_run,
    op_complete,
    op_start,
    op_status,
    run_dir,
    save,
    validate_author_backend,
    validate_id_component,
    validate_merge_authority,
)
from governance.spec_runner_contract import target_selector_policy

_ROLLUP_GREEN = {"SUCCESS", "NEUTRAL", "SKIPPED"}

# Operation keys стадии Need (E2, спека §4/§5): S0.5 `_step_interview`.
INTERVIEW_START = "interview-start"
INTERVIEW_BRIEF = "interview-brief"
# Файл findings, который `_interview_after_reply` пишет при 10/11 (§5.1).
# Он описывает стоп ТЕКУЩЕГО захода: чистка стоит на входе в стадию
# (`_step_interview`), поэтому следующий заход по любому маршруту — 20,
# 1/2, отказ публикации после `status` 0, шорткат сразу в brief — застаёт
# файл снятым (devtools#247).
INTERVIEW_FINDINGS = "interview-findings.txt"

# (op key, kind для ops.author, ожидаемое имя файла в bundle_dir) — S2/S3.
_AUTHOR_STEPS = (
    ("author-charter", "charter", "00-charter.md"),
    ("author-requirements", "requirements", "10-requirements.md"),
    ("author-behaviour", "behaviour-spec", "15-behaviour-spec.md"),
    ("author-design", "design", "20-design.md"),
    ("author-acceptance", "acceptance", "25-acceptance.md"),
    ("author-decomposition", "decomposition", "30-decomposition.md"),
)

# `target_profile_declares`/`PREFLIGHT_PROCEDURE_HINT` — в
# `governance.policy_sources` (фикс-раунд ревью, minor #2): общий источник
# для раннера И `governance.task_bridge`, не приватный кросс-импорт между
# двумя модулями.

# Рёбра S4 prospective-гарда GC-UNPINNED/GC-STALE (`_step_gate`) — MINOR-3
# финального ревью: раньше жили ТРЕМЯ несинхронизированными копиями (этот
# инлайн-кортеж, `task_bridge._BUNDLE_DAG`, `profiles/team-exp.yaml`),
# вынесены в модульную константу здесь; тест согласованности
# (`test_gate_edges_derived_from_bundle_dag`) выводит те же рёбра из
# `task_bridge._BUNDLE_DAG` и ловит расхождение. 4-й элемент — `required`:
# True — все шесть рёбер design/acceptance/decomposition (MAJOR-1 плюс
# Task 6 плана decomposition-node — ребро decomposition→design; Task 6
# плана acceptance-node — оба ребра acceptance и ребро
# decomposition→acceptance) — необъявленное ребро стопит S4 находкой, а не
# тихо пропускается; остальные рёбра остаются необязательными (их
# traces_to не входит в prospective-контракт гарда). Порядок — обход
# `_BUNDLE_DAG`: два ребра acceptance ПОСЛЕ рёбер design (design авторится
# раньше acceptance) и ПЕРЕД decomposition→design; ребро
# decomposition→acceptance — сразу за decomposition→design (тест
# согласованности сравнивает кортежи на равенство, не на множество —
# `test_gate_edges_derived_from_bundle_dag`; узел acceptance довезён в
# `task_bridge._BUNDLE_DAG` Task 7 плана acceptance-node).
_GATE_EDGES: tuple[tuple[str, str, str, bool], ...] = (
    ("10-requirements.md", "charter", "00-charter.md", False),
    ("15-behaviour-spec.md", "requirements", "10-requirements.md", False),
    ("20-design.md", "requirements", "10-requirements.md", True),
    ("20-design.md", "behaviour-spec", "15-behaviour-spec.md", True),
    ("25-acceptance.md", "requirements", "10-requirements.md", True),
    ("25-acceptance.md", "behaviour-spec", "15-behaviour-spec.md", True),
    ("30-decomposition.md", "design", "20-design.md", True),
    ("30-decomposition.md", "acceptance", "25-acceptance.md", True),
)


def _reserve_run_id(run_id: str) -> None:
    """Атомарно резервирует `run_id`, отказывая на занятом (круг 4/7).

    Круг 4: занятый `run_id` молча перезаписывался (codex-ревью PR #88,
    major) — `new_run`+`save` пишут `run.json` через `os.replace`
    (атомарно), но БЕЗ проверки, что там уже есть чужой леджер. Первая
    починка (`exists()`-проверка, отдельно от записи) сама была TOCTOU
    (круг 7, codex-major): между проверкой и `save()` могла проскочить
    гонка двух параллельных `start()`/`verify()` с одним `run_id`.

    Починка: эксклюзивное создание файла (`Path.touch(exist_ok=False)` —
    `O_CREAT|O_EXCL` под капотом, атомарно на уровне ОС) резервирует слот
    за один системный вызов; `save()` дальше по коду штатно перезаписывает
    этот пустой файл тем же атомарным `os.replace`, что и всегда.
    """
    target = run_dir(run_id)
    target.mkdir(parents=True, exist_ok=True)
    try:
        (target / "run.json").touch(exist_ok=False)
    except FileExistsError as exc:
        raise ValueError(
            f"run {run_id!r} уже существует — используйте resume(...), "
            "не start(...)/verify(...) с тем же run_id"
        ) from exc


def _load_all_runs() -> dict[str, RunState]:
    """Все читаемые прогоны под `RUNS_ROOT`, id -> `RunState`.

    Общий обход для WS-lock (`_blocking_merged_unverified`) и
    verify-дедупа (`_has_green_child`, round 3 codex-ревью): битые/
    нечитаемые `run.json` (не тот формат, отсутствующие поля, невалидный
    JSON) пропускаются молча — сосед с испорченным леджером не должен
    мешать чужому прогону.
    """
    states: dict[str, RunState] = {}
    for run_id in all_run_ids():
        try:
            states[run_id] = load(run_id)
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return states


def _blocking_merged_unverified(ws_id: str) -> str | None:
    """WS-lock (спека §5, финальное ревью круг 5): найти блокирующий run_id.

    Пока по ``ws_id`` висит ``merged_unverified`` без зелёного (``completed``)
    потомка (``remediated_by`` == его run_id), новый авторинг-прогон по этому
    же ``ws_id`` не стартует — fail-closed: непроверенное не читается как
    проверенное. Возвращает run_id блокирующего прогона либо ``None``.
    """
    states = _load_all_runs()
    verified_parents = {
        s.remediated_by
        for s in states.values()
        if s.remediated_by and s.status == "completed"
    }
    for run_id, s in states.items():
        if s.ws_id == ws_id and s.status == "merged_unverified":
            if run_id not in verified_parents:
                return run_id
    return None


def _has_green_child(parent_run_id: str) -> bool:
    """`True`, если у `parent_run_id` уже есть завершённый (`completed`)
    verify-потомок (`remediated_by == parent_run_id`) — «уже верифицирован»
    (round 3, codex-ревью, продолжение I-7): tmux-дедуп в консоли защищает
    только пока сессия жива (round 2 её ещё и самозакрыл), но `verify()`
    сам по себе не проверял, есть ли у родителя уже подтверждающий потомок
    — второй `verify()`-вызов (например руками, мимо консоли) на уже
    зелёном потомке создавал бы ЕЩЁ ОДИН, хотя родитель уже подтверждён.
    Провальный потомок (`merged_unverified`) НЕ считается — повтор
    verify после провала разрешён и остаётся штатным путём."""
    return any(
        s.remediated_by == parent_run_id and s.status == "completed"
        for s in _load_all_runs().values()
    )


# Дольше типичного времени между _reserve_run_id и save(child) (доли
# секунды в норме) на порядки — но короче того, за что реальный S8-прогон
# (checkout/gate-check/create_issue) успевает продвинуться дальше "running"
# в первый же заход (round 7): грейс-период для "это свежий конкурент,
# ещё пишет свой RunState", не гарантия реального времени S8.
_ACTIVE_VERIFY_GRACE_SECONDS = 120


def _active_verify_child(parent_run_id: str) -> str | None:
    """`run_id` активного (нетерминального) verify-потомка `parent_run_id`,
    если такой есть, — иначе `None` (round 7, codex-major).

    Round 6 починил зависание счётчика `attempt` на оборванной резервации,
    но тем самым ПЕРЕОТКРЫЛ гонку round 5: второй конкурентный `verify()`
    теперь видит каталог `<parent>-v1` (пусть даже пустой) и спокойно
    резервирует `v2` — коллизии на `_reserve_run_id` больше нет, поэтому
    round-5 сериализация через ValueError на занятом id не срабатывает.
    Сериализация должна опираться на СОСТОЯНИЕ потомков, а не на коллизию
    id: пока у родителя есть потомок, который ещё не дошёл до терминального
    статуса, новый `verify()` не должен стартовать параллельный S8 в том
    же `target_dir`.

    Обходит каталоги по паттерну `^<parent>-v\\d+$` (`all_run_ids()`, как
    `_next_verify_run_id`):

    - валидный `run.json` со `status` НЕ в `{"completed",
      "merged_unverified"}` (например `"running"` — S8 ещё идёт) —
      активный потомок, возвращаем его `run_id`;
    - нечитаемый/пустой `run.json` — либо труп round 6 (процесс умер между
      `_reserve_run_id` и `save()`), либо конкурент, который только что
      зарезервировал слот и ЕЩЁ пишет свой `RunState` (та самая гонка).
      Различаем по `mtime` файла: моложе `_ACTIVE_VERIFY_GRACE_SECONDS` —
      трактуем как «только что зарезервировано конкурентом», активный;
      старше — труп, игнорируем (нумерация `_next_verify_run_id` его
      перешагнёт).

    Терминальные потомки (`completed`/`merged_unverified`) не блокируют —
    зелёный уже отловлен `_has_green_child` отдельной проверкой с другим
    сообщением, провальный разрешает повторный verify.
    """
    pattern = re.compile(rf"^{re.escape(parent_run_id)}-v(\d+)$")
    now = time.time()
    for candidate_id in all_run_ids():
        if not pattern.match(candidate_id):
            continue
        try:
            state = load(candidate_id)
        except (OSError, ValueError, TypeError, KeyError):
            try:
                mtime = (run_dir(candidate_id) / "run.json").stat().st_mtime
            except OSError:
                continue
            if now - mtime < _ACTIVE_VERIFY_GRACE_SECONDS:
                return candidate_id
            continue
        if state.status not in ("completed", "merged_unverified"):
            return candidate_id
    return None


def start(
    subject: str,
    repo: str,
    repo_slug: str,
    ws_id: str,
    target_dir: str,
    bundle_dir: str,
    profile: str,
    run_id: str,
    ops: Ops,
    merge_authority: str | None = None,
    author_backend: str = "codex",
    brief_source: brief_input.BriefSource | None = None,
    interview_spec: iv.InterviewSpec | None = None,
    allow_legacy_dt: bool = False,
) -> RunState:
    """S0: новый прогон, затем сразу `advance()` до стопа/завершения.

    `merge_authority`/`author_backend` валидируются ПЕРВЫМИ, ДО
    резервирования `run_id` (B2 follow-up приёмки B1, minor из #88 —
    `author_backend` внесла заново Task 2, финальное ревью I-3): чистая
    проверка входа без побочных эффектов, как и WS-lock ниже — невалидное
    значение раньше навсегда резервировало `run_id` пустым `run.json`,
    потому что `new_run()` (единственное место валидации до этой правки)
    вызывался ПОСЛЕ `_reserve_run_id`. Через CLI `author_backend`
    недостижимо (`choices=["codex", "disp"]`), но `start()` — публичный
    API, и симметрия проверок здесь — инвариант.

    `interview_spec`/`brief_source` взаимоисключающи (E2, спека §5) — стадия
    Need стартует либо с готовым brief'ом, либо с discovery-интервью, но не
    с обоими сразу; проверяется той же группой, что и координаты выше — до
    любого побочного эффекта.

    WS-lock проверяется ДО резервирования `run_id` (круг 7): у неё тоже нет
    побочных эффектов, а `_reserve_run_id` создаёт файл — так отказ по
    WS-lock не оставляет пустой `run.json`-заглушку под несостоявшимся
    `run_id`.
    """
    validate_merge_authority(merge_authority)
    validate_author_backend(author_backend)
    if interview_spec is not None and brief_source is not None:
        raise ValueError("--need и --brief взаимоисключающи")
    blocker = _blocking_merged_unverified(ws_id)
    if blocker is not None:
        raise ValueError(
            f"WS-id {ws_id!r} заблокирован merged_unverified-прогоном "
            f"{blocker!r} без зелёного потомка — создайте verification-run "
            "(verify(...)) прежде чем начинать новый авторинг-прогон"
        )
    _reserve_run_id(run_id)
    brief_descriptor = None
    if brief_source is not None:
        # Durable local intake copy: run.json remains portable, while a crash
        # before the target materialization step cannot make resume depend on
        # the caller machine's original path still existing.
        intake_root = run_dir(run_id) / "brief-input"
        brief_input.materialize(brief_source, intake_root, ".")
        staged = brief_input.inspect_materialized(intake_root, ".")
        brief_descriptor = staged.as_state()
        if brief_descriptor != brief_source.as_state():
            raise brief_input.BriefInputError(
                "discovery source descriptor изменился при durable intake"
            )
    state = new_run(
        subject=subject,
        repo=repo,
        repo_slug=repo_slug,
        ws_id=ws_id,
        target_dir=target_dir,
        bundle_dir=bundle_dir,
        profile=profile,
        run_id=run_id,
        merge_authority=merge_authority,
        author_backend=author_backend,
        brief=brief_descriptor,
        interview=interview_spec.as_state() if interview_spec else None,
        allow_legacy_dt=allow_legacy_dt,
    )
    save(state)
    return advance(state, ops)


def advance(state: RunState, ops: Ops) -> RunState:
    """Выполняет шаги S1..S8 до стопа (не-``running`` статус) либо конца.

    Каждая шаг-функция сама решает, продолжать ли (``True``) или прервать
    этот вызов ``advance()`` (``False``) — не только по смене статуса
    (`stopped_*`/`waiting_human_merge`), но и когда шаг намеренно откладывает
    продолжение на следующий вызов (S6, exit=4 — «повторить весь S6»).

    ``merged_unverified`` — терминально и навсегда (спека §5): повторный
    ``advance()`` над таким состоянием отвергается явно, продвижение — только
    через дочерний run (`verify`).

    Op ``merge`` уже ``completed`` — весь пред-мержевой конвейер (review,
    verdict, ...) переигрывать нельзя: это платный повтор на PR, который
    уже смержен (ревью #253). Единственная оставшаяся работа — S8, и
    только она выполняется; `_step_s8` идемпотентен и на уже терминальном
    ``state.status`` (комментарий у самой функции). Без этой проверки
    нетерминальный отказ `_step_s8` (например, `checkout_and_pull` не
    удался) оставлял бы `state.status == "running"` с незавершёнными
    пред-мержевыми op'ами (`review`, `author-*`) — следующий resume падал
    бы в общий шаговый цикл и переигрывал их вхолостую на смерженном PR.
    """
    if state.status == "merged_unverified":
        raise ValueError(
            f"run {state.run_id!r} — merged_unverified навсегда; создайте "
            "verification-run через verify(...)"
        )
    if op_status(state, "merge") == "completed":
        _step_s8(state, ops)
        return state
    steps = (
        _step_interview,
        _step_branch,
        _step_materialize_brief,
        _step_authoring,
        _step_commit,
        _step_gate,
        _step_push,
        _step_pr,
        _step_ready,
        _step_review,
        _step_verdict,
        _step_s8,
    )
    for step in steps:
        if state.status != "running":
            break
        if not step(state, ops):
            break
    return state


# stopped_author/stopped_gate/stopped_review: между стопом и resume человек
# правит файлы бандла в worktree (устраняет gate-findings, отрабатывает
# review-находки) — сброс ТОЛЬКО op'а, отвечавшего за сам стоп, оставлял бы
# `commit` completed со СТАРЫМ, докоррекционным деревом, и `push` уносил бы
# уже неактуальный (возможно красный) коммит дальше по конвейеру (круг 9,
# codex-ревью PR #88). Сбрасывается весь диапазон commit→review; `pr` НЕ
# входит — существующий PR переиспользуется (push обновит его ветку,
# `_step_pr`-реконсиляция не создаёт второй, F-5); `commit_paths` на чистом
# дереве не падает (`git diff --cached --quiet`), так что повторный коммит
# без реальных правок — no-op, не ошибка.
_BUNDLE_EDIT_RESET_OPS: tuple[str, ...] = (
    # review-refute — лимит «одна авто-попытка опровержения file-missing на
    # ревью-цикл» (спека §7): новый цикл (правка бандла/resume) получает
    # свежую попытку вместе со свежим review.
    "commit", "gate-candidate", "push", "ready", "review", "review-refute",
)

# Статус stopped_* -> op'ы, которые reconciliation обязан сбросить в pending
# перед повторным advance() (финальное ревью F-1). stopped_author не входит:
# у него дополнительно нужно найти НЕЗАВЕРШЁННЫЙ author-* узел (см.
# `_reset_stopped_author`).
_STOPPED_RESET_OPS: dict[str, tuple[str, ...]] = {
    "stopped_gate": _BUNDLE_EDIT_RESET_OPS,
    "stopped_review": _BUNDLE_EDIT_RESET_OPS,
    "stopped_merge_refused": ("verdict",),
    # stopped_dirty: `branch` ещё не стартовала (проверка идёт до
    # `_ensure_started`), сбрасывать нечего — только статус обратно в
    # running, чтобы `_step_branch` перепроверил `is_dirty` (круг 5).
    "stopped_dirty": (),
    # stopped_preflight (Task 8): проверка тоже до `_ensure_started` узла
    # design — сбрасывать нечего, только статус обратно в `running`, чтобы
    # `_step_authoring` перепроверил `target_profile_declares` по
    # ТЕКУЩЕМУ содержимому target-профиля (человек мог доставить
    # обновлённый профиль PR-ом между стопом и resume). Пустой кортеж —
    # без него `resume()` был бы тихим no-op (инвариант F-1).
    "stopped_preflight": (),
}


def _reset_stopped_author(state: RunState) -> None:
    """stopped_author: незавершённые ``author-*`` + диапазон commit→review
    (круг 9) — та же логика, что `_BUNDLE_EDIT_RESET_OPS`, на случай, если
    контент бандла успел измениться после починки."""
    for key, _kind, _filename in _AUTHOR_STEPS:
        if op_status(state, key) != "completed":
            state.ops.pop(key, None)
    for key in _BUNDLE_EDIT_RESET_OPS:
        state.ops.pop(key, None)


def resume(run_id: str, ops: Ops) -> RunState:
    """Явный подхват сохранённого run'а (спека §5).

    ``merged_unverified`` — отказ (навсегда, см. `advance`). Из
    ``waiting_human_merge`` — reconciliation по факту мержа: PR ``MERGED`` →
    фиксирует op ``merge`` (если ещё не зафиксирован) и выполняет только S8;
    PR всё ещё ``OPEN`` — состояние не меняется, run продолжает ждать
    человека.

    ``waiting_interview``/``stopped_interview`` (E2, спека §5.1/§5.2) — без
    записанного ``session_id`` (сирота) discovery не зовётся, run возвращается
    как есть; иначе статус ставится в ``running`` и зовётся `advance()`,
    который в `_step_interview` опрашивает `discovery status` (`_interview_poll`).

    Из любого ``stopped_*`` — reconciliation вместо слепого no-op (финальное
    ревью F-1/M-1): op(ы), на которых прогон встал, сбрасываются в pending, и
    только после этого зовётся `advance()`. Но сперва — реконсиляция «человек
    смержил PR напрямую, минуя раннер» (`_reconcile_pr_merged_out_of_band`,
    живой прогон spec-runner#480/#522, круг 9→10: PR смержен из
    ``stopped_review`` в обход S7, GitHub удалил ветку `--delete-branch`, и
    слепой сброс `commit`→`review` попытался бы `push` несуществующей ветки).
    Реконсиляция применяется к ЛЮБОМУ статусу с уже созданным PR — не только
    к ``waiting_human_merge``/``stopped_merge_refused``, где она была раньше,
    но и к ``stopped_gate``/``stopped_review``, если у run'а уже есть ``pr``
    (S4 в конвейере идёт ДО ``pr`` — на первом заходе его ещё нет, но после
    хотя бы одного успешного прохода S4→S5 PR остаётся и при повторном
    `stopped_gate` на следующем цикле реконсиляция применима). Если PR ``MERGED``
    — фиксирует op ``merge`` и выполняет только
    S8, возврат сразу. Иначе — обычный сброс диапазона:
    ``stopped_gate``/``stopped_review`` — весь диапазон ``commit``→``review``
    (``commit``, ``gate-candidate``, ``push``, ``ready``, ``review``, но не
    ``pr`` — круг 9: человек мог поправить бандл в worktree между стопом и
    resume, и старый `commit`/`push` унесли бы докоррекционное дерево дальше
    по конвейеру); ``stopped_author`` — реконсиляция ПРИМЕНИМА (brief-
    coverage в `_step_authoring`, E1, останавливает run этим статусом на
    каждом заходе, до проверки завершённости узлов — PR к этому моменту
    вполне может уже существовать с прошлого прохода S5), затем
    незавершённые ``author-*`` плюс тот же диапазон;
    ``stopped_merge_refused`` (S7 `refuse`, отдельный от ``stopped_gate``
    статус — M-1) — ``verdict``, хотя фактическая пересверка вердикта
    теперь происходит на каждом заходе в S7 независимо от этого сброса (см.
    `_step_verdict`, F-2); ``stopped_dirty`` (S1 fail-closed dirty-гард,
    круг 5) — сбрасывать нечего (``branch`` не стартовала, PR тем более),
    только статус обратно в ``running``. Любой другой статус — обычный
    `advance()`.
    """
    state = load(run_id)
    if state.status == "merged_unverified":
        raise ValueError(
            f"run {run_id!r} — merged_unverified навсегда; создайте "
            "verification-run через verify(...)"
        )
    if state.status == "waiting_human_merge":
        _reconcile_pr_merged_out_of_band(state, ops)
        return state
    if state.status in ("waiting_interview", "stopped_interview"):
        if state.interview and state.interview.get("session_id") is None:
            # Сирота — координаты стадии Need без сессии: discovery не
            # зовём, оператор присоединяется вручную (спека §5.2).
            print(
                "resume: стадия Need без записанной сессии — "
                "присоедините её: --session <id>, либо --new-run "
                "--ws-id <fresh-id>"
            )
            return state
        state.status = "running"
        save(state)
        return advance(state, ops)
    if state.status == "stopped_author":
        # Ревью #253: `stopped_author` НЕ гарантирует отсутствие PR — brief-
        # coverage внутри `_step_authoring` (E1) выполняется на каждом
        # заходе, ДО проверки `op_status(key) == "completed"`, и может
        # остановить run этим статусом уже после того, как PR создан
        # (resume из stopped_review/stopped_gate доходит сюда повторно).
        # `state.pr is None` внутри реконсиляции делает вызов безопасным и
        # для «настоящего» stopped_author без PR.
        if _reconcile_pr_merged_out_of_band(state, ops):
            return state
        _reset_stopped_author(state)
        state.status = "running"
        save(state)
        return advance(state, ops)
    if state.status in _STOPPED_RESET_OPS:
        if _reconcile_pr_merged_out_of_band(state, ops):
            return state
        for key in _STOPPED_RESET_OPS[state.status]:
            state.ops.pop(key, None)
        state.status = "running"
        save(state)
        return advance(state, ops)
    return advance(state, ops)


def _reconcile_pr_merged_out_of_band(state: RunState, ops: Ops) -> bool:
    """PR уже ``MERGED`` — человек смержил напрямую, минуя раннер.

    Общая реконсиляция для ``waiting_human_merge`` и для любого
    ``stopped_*`` с уже созданным PR (``stopped_gate``, ``stopped_review``,
    ``stopped_merge_refused``): фиксирует op ``merge`` (если ещё не
    зафиксирован) и переводит run сразу на S8. Без неё `resume()` пытался
    бы переиграть ``commit``→``review`` на ветке, которую GitHub уже удалил
    после мержа (`merge-pr.sh --delete-branch` или ручной мерж с той же
    опцией), и падал бы на `push` несуществующей ветки (живой прогон
    spec-runner#480, bundle-PR #522: смержен вручную из `stopped_review`).

    Возвращает ``True``, если реконсиляция сработала — вызывающий должен
    вернуть ``state`` как есть и ничего больше не делать. S8 может при этом
    остаться незавершённым: либо `target_dir` грязный — гард ниже (ревью
    #253 круг 3) останавливается ДО перехода на S8 с явным комментарием, op
    `merge` не фиксируется, следующий resume заходит сюда же; либо
    нетерминальный отказ самого `_step_s8` (`checkout_and_pull` не удался
    и т.п.) — за это отвечает НЕ эта функция, а общая проверка `op "merge"
    == "completed"` в начале `advance()` (ревью #253, круг 2), которая не
    даст следующему resume переиграть пред-мержевые op'ы вхолостую.
    ``False`` — PR ещё не создан (``state.pr is None``) либо остаётся
    ``OPEN``; вызывающий продолжает обычной веткой.
    """
    if state.pr is None:
        return False
    pr_facts_now = ops.pr_facts(state.repo_slug, state.pr)
    if pr_facts_now.get("state") != "MERGED":
        return False
    # Ревью #253 круг 3: контракт `_BUNDLE_EDIT_RESET_OPS`/`stopped_author`
    # разрешает оператору держать незакоммиченные правки бандла в
    # target_dir между стопом и resume — `stopped_gate`/`stopped_review`/
    # `stopped_author` именно этот случай. S8 ниже переключает дерево на
    # `base_ref` (`checkout_and_pull`); тот же гард, что `deliver_for_run`
    # ставит перед СВОИМ `checkout_and_pull` (`task_bridge.py`, ревью
    # #191 круг 2, дословная мотивация оттуда): чекаут по грязному дереву —
    # сам по себе эффект, конфликтует посреди переключения либо молча
    # уносит чужую незакоммиченную работу. Статус НЕ меняется (в отличие
    # от нормального пути ниже) — PR смержен, это уже факт GitHub, но
    # `merge` op не фиксируется, пока дерево грязное: повторный resume
    # после уборки должен зайти сюда же, а не в короткое замыкание
    # `advance()` по `merge completed`, которое эту проверку обходит.
    if ops.is_dirty(state.target_dir):
        _stop_with_comment(
            state, ops, state.status,
            "PR уже смержен вручную, но в target_dir остались "
            "незакоммиченные правки бандла — переход на S8 (переключение "
            "дерева на базовую ветку) потерял бы их молча либо отказал бы "
            "посреди чекаута. Сохраните/закоммитьте правки (в смерженный "
            "PR они уже не попадут) и повторите resume.",
        )
        return True
    # Ревью #253: реконсиляция может сработать до того, как `_step_verdict`
    # хоть раз записал `state.base_ref` (stopped_review/stopped_gate — он
    # не выполнялся ни разу), а `_step_s8` ниже по стеку фолбэкается на
    # литерал "master" при пустом `base_ref`. Тот же фолбэк, что у
    # `_step_verdict` (F-5): молчаливый на пустом/отсутствующем
    # `baseRefName`, но здесь — явное поле факта, не догадка.
    state.base_ref = pr_facts_now.get("baseRefName") or "master"
    if op_status(state, "merge") != "completed":
        op_complete(state, "merge", merged=True)
    state.status = "running"
    save(state)
    advance(state, ops)
    return True


def _next_verify_run_id(parent_run_id: str) -> str:
    """Детерминированный `run_id` следующей verify-попытки (round 5,
    codex-major — TOCTOU): `f"{parent_run_id}-v{attempt}"`.

    Раньше id потомка был случайным (`os.urandom`), и `_has_green_child`
    сам по себе не сериализовал конкурентные вызовы: два одновременных
    `verify()` на одном родителе оба проходили проверку «нет зелёного
    потомка» (ни один ещё не сохранён), получали РАЗНЫЕ случайные id и оба
    успевали запустить параллельный S8 в одном `target_dir`. Детерминизм
    чинит это через уже существующий атомарный резерв: конкурентные вызовы
    без явного `run_id` вычисляют ОДИН И ТОТ ЖЕ id — `_reserve_run_id`
    (`O_CREAT|O_EXCL`) пропускает ровно одного, остальные получают
    `ValueError` вместо параллельного запуска.

    `attempt` считается по ИМЕНАМ каталогов под `RUNS_ROOT`
    (`all_run_ids()`, `^<parent>-v(\\d+)$`) — `max(N) + 1`, а не по числу
    успешно ЗАГРУЖЕННЫХ `RunState` (round 6, codex-major): гибель между
    `_reserve_run_id` и `save(child)` оставляет ПУСТОЙ
    `<parent>-v<N>/run.json` (сам `_reserve_run_id` уже создал файл через
    `touch`, `save()` его ещё не заполнил). Пустой файл не парсится
    `json.loads` — подсчёт по `_load_all_runs()` (которая тихо пропускает
    нечитаемые леджеры) видел бы то же число потомков навсегда, поэтому
    каждый следующий `verify()` без `run_id` вычислял бы ТОТ ЖЕ `N`, а
    `_reserve_run_id` навсегда отвечал бы «уже существует» на уже занятом
    (хоть и оборванном) каталоге — постоянный deadlock на этом родителе.
    Валидность JSON тут не важна: сам факт существования каталога с
    подходящим именем уже занимает номер попытки, следующий `verify()`
    обязан взять следующий за ним, не спорить с ним же.
    """
    pattern = re.compile(rf"^{re.escape(parent_run_id)}-v(\d+)$")
    existing_attempts = [
        int(match.group(1))
        for run_id in all_run_ids()
        if (match := pattern.match(run_id))
    ]
    attempt = max(existing_attempts, default=0) + 1
    return f"{parent_run_id}-v{attempt}"


def verify(
    parent_run_id: str, ops: Ops, run_id: str | None = None
) -> RunState:
    """Дочерний verification-run для `merged_unverified`-родителя (спека §5).

    Новый ``RunState`` с теми же координатами (repo/ws_id/target_dir/
    bundle_dir/profile/branch/pr/head), полем ``remediated_by`` на родителя и
    выполняет только S8. Успех фиксируется у ПОТОМКА (``completed``);
    родитель не трогается — он остаётся `merged_unverified` навсегда.

    Отказывает, если у родителя уже есть ЗЕЛЁНЫЙ (``completed``) потомок
    (round 3, codex-ревью): «уже верифицирован», повторный `verify()`
    создавал бы ещё один verification-run поверх уже подтверждённого.
    Провальный потомок не блокирует — повтор после него штатный.

    Отказывает, если у родителя уже есть АКТИВНЫЙ (нетерминальный) потомок
    (`_active_verify_child`, round 7): round 6 починил зависание счётчика
    попыток на оборванной резервации, но тем самым переоткрыл гонку
    round 5 — второй конкурентный `verify()` видел уже занятый каталог и
    спокойно резервировал следующий номер, коллизии на `_reserve_run_id`
    больше не было. Сериализация теперь держится на состоянии потомков (с
    поправкой на mtime для трупов round 6), не на коллизии id — см.
    докстринг `_active_verify_child`.

    Все три проверки (`parent.status`, `_has_green_child`,
    `_active_verify_child`) — ДО `_reserve_run_id`, тот же порядок, что у
    `start()` (валидация без побочных эффектов перед резервированием id).

    `run_id=None` (дефолт) выводит ДЕТЕРМИНИРОВАННЫЙ id через
    `_next_verify_run_id` (round 5) — при отсутствии активного/зелёного
    потомка резервирует его атомарным `_reserve_run_id`. Явный `run_id`
    остаётся для CLI-совместимости (`--run-id`, теперь опционален) и
    валидируется как раньше (`_reserve_run_id` → `run_dir()` →
    `validate_id_component`).
    """
    parent = load(parent_run_id)
    if parent.status != "merged_unverified":
        raise ValueError(
            f"verify: parent-run {parent_run_id!r} не merged_unverified "
            f"(текущий статус {parent.status!r})"
        )
    if _has_green_child(parent_run_id):
        raise ValueError(
            f"verify: parent-run {parent_run_id!r} уже верифицирован — "
            "у него есть завершённый (completed) потомок"
        )
    active_child = _active_verify_child(parent_run_id)
    if active_child is not None:
        raise ValueError(
            f"verify уже идёт: {active_child} (resume или дождитесь)"
        )
    if run_id is None:
        run_id = _next_verify_run_id(parent_run_id)
    _reserve_run_id(run_id)
    child = new_run(
        subject=parent.subject,
        repo=parent.repo,
        repo_slug=parent.repo_slug,
        ws_id=parent.ws_id,
        target_dir=parent.target_dir,
        bundle_dir=parent.bundle_dir,
        profile=parent.profile,
        run_id=run_id,
        brief=parent.brief,
        merge_authority=parent.merge_authority,
        author_backend=parent.author_backend,
        allow_legacy_dt=parent.allow_legacy_dt,
    )
    child.remediated_by = parent_run_id
    child.branch = parent.branch
    child.pr = parent.pr
    child.head = parent.head
    child.base_ref = parent.base_ref
    child.status = "running"
    save(child)
    _step_s8(child, ops)
    return child


def facts_from(
    pr_facts: dict[str, Any],
    files: list[str],
    threads: bool | None,
    bundle_dir: str,
) -> PrFacts:
    """Fail-closed маппинг сырых gh-фактов PR в `PrFacts` (спека §8).

    Отклонение от produces-сигнатуры брифа (три параметра): без явного
    `bundle_dir` `diff_class` не может отличить document-дифф от прочего —
    поле у функции нет способа "узнать" его иначе. Добавлен четвёртым
    обязательным параметром; вызывающая сторона (`_step_verdict` этого же
    модуля) передаёт `state.bundle_dir`.
    """
    checks = pr_facts.get("statusCheckRollup") or []
    if not checks:
        checks_rollup = "empty"
    else:
        conclusions = [(c.get("conclusion") or "") for c in checks]
        if all(c in _ROLLUP_GREEN for c in conclusions):
            checks_rollup = "green"
        elif any(c and c not in _ROLLUP_GREEN for c in conclusions):
            # Любой FAILURE = red, БЕЗ поблажки на mergeStateStatus=UNSTABLE
            # (приёмка PR #99, major): в rulesets флота нет required-чеков,
            # поэтому UNSTABLE у GitHub означает «упало что угодно, хоть
            # тесты» — читать его как «красное только advisory» значило бы
            # мержить агентом PR с красным test. Мотивация послабления
            # умерла со снятием CI-контура codex-review (devtools#98 +
            # волна): advisory-чеков во флоте больше нет.
            checks_rollup = "red"
        else:
            checks_rollup = "unknown"

    raw_mergeable = pr_facts.get("mergeable")
    if raw_mergeable == "MERGEABLE":
        mergeable = "mergeable"
    elif raw_mergeable == "CONFLICTING":
        mergeable = "conflicting"
    else:
        mergeable = "unknown"

    behind_base = pr_facts.get("mergeStateStatus") == "BEHIND"
    unresolved_threads = True if threads is None else bool(threads)

    prefix = bundle_dir.rstrip("/") + "/"
    # Пустой `files` — не "все файлы про документацию" (круг 11, codex-major):
    # `all()` на пустом списке истинно вакуумно, и без явной проверки пустой
    # PR читался бы как "document" — недоказанный дифф мог пройти в agent-мерж.
    # Fail-closed: пусто -> "code", decide() отправит человеку.
    diff_class = (
        "document"
        if files and all(f.startswith(prefix) or f.startswith("docs/") for f in files)
        else "code"
    )
    # Перечень — из SSOT `contracts/authority-root/v1/paths.env` (ревью
    # #183, круг 4): литерал здесь был вторым определением того же списка,
    # и разойтись с accept_pr он мог молча.
    touches_authority_root = bool(authority_root.touched(files))
    return PrFacts(
        checks_rollup=checks_rollup,
        mergeable=mergeable,
        behind_base=behind_base,
        unresolved_threads=unresolved_threads,
        diff_class=diff_class,
        touches_authority_root=touches_authority_root,
    )


def _ensure_started(state: RunState, key: str) -> None:
    """`op_start`, если операция ещё `new`; `started`/`completed` не трогает."""
    if op_status(state, key) == "new":
        op_start(state, key)


def _stop_with_comment(state: RunState, ops: Ops, status: str, body: str) -> None:
    """Фиксирует стоп-статус ДО best-effort комментария (круг 10, codex-major).

    Раньше `ops.comment` звался ПЕРЕД `state.status = ...`/`save()` во всех
    стоп-с-комментарием путях (S6 exit 1/2/3, S7 human/refuse, merge False):
    гибель между вызовом комментария и фиксацией статуса оставляла run в
    `"running"`, и следующий `advance()`/`resume()` переигрывал этот же шаг
    с нуля — включая повторный `ops.comment`, дублируя комментарий в PR на
    каждом таком падении. Порядок инвертирован: статус сохраняется ПЕРВЫМ
    (после него шаг уже не переигрывается — `advance()` останавливается по
    `state.status != "running"` до следующего явного `resume()` со сбросом
    op'ов); комментарий — best-effort ПОСЛЕ: его сбой не откатывает уже
    зафиксированный статус, но и не глотается совсем молча — печатается
    предупреждение.
    """
    state.status = status
    save(state)
    try:
        ops.comment(state.repo_slug, state.pr, body)
    except Exception as exc:  # noqa: BLE001 — best-effort, не должен ронять шаг
        print(f"_stop_with_comment: comment ({status!r}) не удался: {exc}")


def _interview_stop(state: RunState, reason: str) -> bool:
    """Персистентный стоп стадии Need; координаты и session_id не трогаются."""
    print(f"_step_interview: {reason}")
    state.status = "stopped_interview"
    save(state)
    return False


def _print_answer_hint(state: RunState, reply: iv.DiscoveryReply) -> None:
    """Подсказка стейкхолдеру: на какой вопрос и какой командой отвечать."""
    action = reply.envelope.get("next_action", {})
    print(
        f"_step_interview: вопрос {action.get('question_id')}: "
        f"{action.get('question_text', '')}"
    )
    print("ответьте вне spec-loop и повторите команду:")
    print(
        "  " + iv.answer_command(
            state.interview["session_id"], state.interview["stakeholder_role"]
        )
    )


def _upstream_path(state: RunState, spec: iv.InterviewSpec) -> str | None:
    """Путь upstream-blob'а для engineer-фрейма; `None` для остальных."""
    if spec.frame != "engineer":
        return None
    return str(
        run_dir(state.run_id) / "brief-input" / "00-discovery" / spec.traces_to
    )


def _interview_poll(
    state: RunState, ops: Ops, spec: iv.InterviewSpec, cwd: str
) -> bool:
    """S0.5 опрос статуса (`discovery status`, §5.1).

    ``interview-brief`` не ``new`` значит brief уже запрошен/получен на
    прошлом заходе — управление сразу передаётся `_interview_publish`
    (Task 7/8), повторный `status` не нужен.

    Интеграционно это эквивалентно повтору `status`: после стопа на
    `brief` повтор команды перерендеривает бриф, а не опрашивает `status`,
    но сосед считает тот же lifecycle в обоих `cmd_brief` и `cmd_status`
    (discovery `cli.py:266-288`) и выдаёт тот же следующий вопрос —
    поэтому run не может зависнуть, застряв между двумя опросами.
    """
    session_id = state.interview["session_id"]
    if op_status(state, INTERVIEW_BRIEF) != "new":
        return _interview_publish(state, ops, spec, cwd)
    reply = ops.discovery_status(session_id, cwd)
    return _interview_after_reply(state, ops, spec, cwd, reply, "status")


def _interview_after_reply(
    state: RunState,
    ops: Ops,
    spec: iv.InterviewSpec,
    cwd: str,
    reply: iv.DiscoveryReply,
    call: str,
) -> bool:
    """Общая таблица переходов для `status` и `brief` (§5.1).

    Файл findings пишется только здесь (ветка 10/11), а снимается на входе
    в стадию — `_step_interview`, см. комментарий там (devtools#247).

    20 — стейкхолдер ждёт следующий вопрос: ту же записанную сессию
    возвращает `waiting_interview`, другую — стоп (координаты интервью
    разъехались). 10/11 — discovery отверг ответ: findings сохраняются
    файлом, печатается шаблон повторного ответа с `--supersede`, run
    стопится. 1/2 — операционный отказ discovery: run стопится, но сессия
    остаётся записанной — печатается подсказка восстановить ЕЁ ЖЕ, а не
    начинать новую.
    """
    session_id = state.interview["session_id"]
    findings_file = run_dir(state.run_id) / INTERVIEW_FINDINGS
    reason = reply.envelope.get("operation", {}).get("reason", "")
    if reply.code == 20:
        if reply.envelope["next_action"].get("session_id") != session_id:
            return _interview_stop(
                state, "next_action.session_id ≠ записанной сессии"
            )
        state.ops.pop(INTERVIEW_BRIEF, None)
        state.status = "waiting_interview"
        save(state)
        _print_answer_hint(state, reply)
        return False
    if reply.code in (10, 11):
        findings_file.write_text(
            json.dumps(
                {
                    "findings": reply.envelope["findings"],
                    "readiness_findings": reply.envelope["readiness_findings"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print("повторный ответ по findings (question_id подставьте сами):")
        print("  " + iv.supersede_template(session_id, spec.stakeholder_role))
        return _interview_stop(
            state, f"{call} вернул {reply.code}: findings в {findings_file}"
        )
    if reply.code in (1, 2):
        print(
            "_step_interview: восстановите ту же сессию "
            f"{shlex.quote(session_id)} в $DISCOVERY_HOME и повторите, "
            "либо новый прогон: --new-run --ws-id <fresh-id>"
        )
        return _interview_stop(state, f"{call} вернул {reply.code}: {reason}")
    if reply.code == 0 and call == "status":
        return _interview_publish(state, ops, spec, cwd)
    # `call == "brief"` с кодом 0 сюда не попадает: `_interview_publish`
    # маршрутизирует в эту функцию только НЕнулевые коды brief (Task 7) —
    # остаётся только неизвестный код у любого из вызовов.
    return _interview_stop(state, f"{call}: неизвестный код {reply.code}")


def _interview_publish(
    state: RunState, ops: Ops, spec: iv.InterviewSpec, cwd: str
) -> bool:
    """S0.5 публикация brief'а (§5.1/§7): tmp → координаты/gate → replace.

    `status` 0 запрашивает `discovery brief` во временный файл рядом с
    итоговым путём; код ≠ 0 обрабатывается общей таблицей
    `_interview_after_reply`. Код 0 проверяется дважды до необратимого
    `os.replace`: координаты (`brief_coordinate_findings`, без ролей — D3)
    и полный `inspect_brief` (discovery gate). Любой отказ стопит run,
    tmp НЕ переименовывается. Реконсиляционное окно Task 8 вставляется
    сразу после `_ensure_started` ниже.
    """
    session_id = state.interview["session_id"]
    out_dir = run_dir(state.run_id) / "brief-input" / "00-discovery"
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / ".brief.tmp"
    final = out_dir / "brief.md"
    _ensure_started(state, INTERVIEW_BRIEF)
    if (
        op_status(state, INTERVIEW_BRIEF) == "started"
        and final.exists()
        and state.brief is None
    ):
        return _interview_reconcile_published(state, ops, spec, cwd, final)
    tmp.unlink(missing_ok=True)
    reply = ops.discovery_brief(session_id, str(tmp), cwd)
    if reply.code != 0:
        tmp.unlink(missing_ok=True)
        return _interview_after_reply(state, ops, spec, cwd, reply, "brief")
    try:
        text = tmp.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return _interview_stop(state, f"brief tmp нечитаем: {exc}")
    findings = iv.brief_coordinate_findings(text, spec)
    if findings:
        return _interview_stop(state, "координаты брифа: " + "; ".join(findings))
    try:
        brief_input.inspect_brief(tmp)
    except brief_input.BriefInputError as exc:
        return _interview_stop(state, f"бриф не проходит inspect_brief: {exc}")
    os.replace(tmp, final)
    source = brief_input.inspect_brief(final)  # дескриптор — по durable-пути
    state.brief = source.as_state()
    state.interview["completed_at"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )
    state.status = "running"
    op_complete(state, INTERVIEW_BRIEF, brief_blob=dict(source.source_blobs))
    return True


def _interview_reconcile_published(
    state: RunState, ops: Ops, spec: iv.InterviewSpec, cwd: str, final: Path
) -> bool:
    """§5.5: реконсиляционное окно после гибели между `replace` и `op_complete`.

    `brief.md` уже лежит durable, но op `interview-brief` остался ``started``
    и `state.brief` пуст — descriptor не успел зафиксироваться. Повторный
    рендер ТОЙ ЖЕ сессии во второй, отдельный tmp обязан вернуть код 0 и
    побайтово совпасть с уже опубликованным `brief.md`; расхождение или
    ненулевой код значит, что сессия ушла от опубликованного состояния —
    стоп без повторного `os.replace`. При совпадении координаты и
    `inspect_brief` перепроверяются заново (durable-файл мог быть подменён
    вручную между гибелью и recovery), и op завершается с ``reconciled=True``
    вместо второго replace.
    """
    probe = final.with_name(".brief.reconcile.tmp")
    probe.unlink(missing_ok=True)
    reply = ops.discovery_brief(state.interview["session_id"], str(probe), cwd)
    try:
        if reply.code != 0:
            return _interview_stop(
                state,
                f"recovery: повторный рендер вернул {reply.code}, "
                "сессия ушла от опубликованного состояния",
            )
        if not probe.exists():
            return _interview_stop(
                state, "recovery: повторный рендер не записал артефакт"
            )
        if probe.read_bytes() != final.read_bytes():
            return _interview_stop(
                state, "recovery: повторный рендер не совпадает с durable brief.md"
            )
    finally:
        probe.unlink(missing_ok=True)
    try:
        text = final.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return _interview_stop(state, f"recovery: brief.md нечитаем: {exc}")
    findings = iv.brief_coordinate_findings(text, spec)
    if findings:
        return _interview_stop(state, "recovery: координаты: " + "; ".join(findings))
    try:
        source = brief_input.inspect_brief(final)
    except brief_input.BriefInputError as exc:
        return _interview_stop(state, f"recovery: inspect_brief: {exc}")
    state.brief = source.as_state()
    state.interview["completed_at"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )
    state.status = "running"
    op_complete(
        state, INTERVIEW_BRIEF, brief_blob=dict(source.source_blobs), reconciled=True
    )
    return True


def attach_session(run_id: str, session_id: str, ops: Ops) -> RunState:
    """§5.3: присоединение сироты — сессия, созданная без записи `session_id`.

    Допустимо только когда `interview-start` остался ``started`` и
    `session_id` ещё не записан (иначе замена сессии запрещена —
    `ValueError`). Кандидат проверяется рендером брифа в одноразовый probe:
    коды 0/10/11/20 (сосед пишет артефакт при любом из них) принимаются,
    1/2 — операционный отказ рендера, тоже `ValueError`. Отсутствующий файл
    после «успешного» кода — тоже отказ, не traceback. Найденный бриф
    сверяется `iv.attach_findings` (H1/frame/traces_to + роли участников
    ⊆ {stakeholder_role}); расхождение — отказ, сессия не записывается.
    При успехе `session_id` фиксируется, run переходит в
    ``waiting_interview``, `interview-start` завершается с
    ``attached=True``.
    """
    state = load(run_id)
    if state.interview is None:
        raise ValueError("у прогона нет стадии Need")
    if state.interview.get("session_id") is not None:
        raise ValueError("session_id уже записан — замена сессии запрещена")
    if op_status(state, INTERVIEW_START) != "started":
        raise ValueError(
            "присоединение допустимо только при interview-start == started"
        )
    spec = iv.InterviewSpec.from_state(state.interview)
    probe = run_dir(run_id) / "brief-input" / ".attach.tmp"
    probe.parent.mkdir(parents=True, exist_ok=True)
    reply = ops.discovery_brief(session_id, str(probe), str(run_dir(run_id)))
    # `cmd_brief` соседа пишет артефакт (`write_artifact`) ДО `_emit` при
    # любом коде, кроме отказа загрузки сессии (discovery `cli.py:267-283`);
    # 1/2 — отказ присоединения, отсутствующий файл — тоже отказ, не
    # traceback.
    try:
        if reply.code not in (0, 10, 11, 20):
            raise ValueError(f"рендер сессии {session_id} вернул {reply.code}")
        if not probe.exists():
            raise ValueError(f"рендер сессии {session_id} не записал артефакт")
        try:
            probe_text = probe.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValueError(
                f"рендер сессии {session_id} нечитаем: {exc}"
            ) from exc
        findings = iv.attach_findings(probe_text, spec)
    finally:
        probe.unlink(missing_ok=True)
    if findings:
        raise ValueError(
            "сессия не совпадает с координатами прогона: " + "; ".join(findings)
        )
    state.interview["session_id"] = session_id
    state.status = "waiting_interview"
    op_complete(state, INTERVIEW_START, session_id=session_id, attached=True)
    return state


def _step_interview(state: RunState, ops: Ops) -> bool:
    """S0.5 — стадия Need (спека §5). Прогон без interview проходит насквозь."""
    if state.interview is None or state.interview.get("completed_at"):
        return True
    spec = iv.InterviewSpec.from_state(state.interview)
    cwd = str(run_dir(state.run_id))
    # Инвариант (devtools#247): файл findings описывает стоп ТЕКУЩЕГО
    # захода, потому что `spec_loop` по его существованию и решает, что
    # причина стопа — findings. Чистка стоит здесь, на входе в стадию, а
    # запись — только в ветке 10/11 `_interview_after_reply`.
    #
    # Почему не в `_interview_after_reply` и не списком веток. Список
    # обесценивающих переходов — опись: заявка #247 просила «как минимум
    # `status` 0», и следующий забылся бы так же. Вход в разбор ответа
    # тоже мимо: `_interview_poll` при `interview-brief != new` уходит в
    # `_interview_publish` НАПРЯМУЮ (шорткат после brief 10/11), и разбор
    # на этом заходе не вызывается вовсе — найдено ревью на devtools#274.
    # Заход в стадию — единственная точка, через которую проходят все
    # маршруты.
    (run_dir(state.run_id) / INTERVIEW_FINDINGS).unlink(missing_ok=True)
    if op_status(state, INTERVIEW_START) != "completed":
        if (
            state.interview.get("session_id") is None
            and op_status(state, INTERVIEW_START) == "started"
        ):
            # Сирота: сессия могла быть создана без записи — discovery не
            # зовём, оператор разбирается вручную.
            state.status = "stopped_interview"
            save(state)
            print(
                "_step_interview: сессия могла быть создана без записи — "
                "присоедините её: --session <id>, либо --new-run --ws-id "
                "<fresh-id>"
            )
            return False
        _ensure_started(state, INTERVIEW_START)
        if spec.frame == "engineer" and spec.upstream_blob is None:
            return _interview_stop(state, ENGINEER_BLOCKED)
        reply = ops.discovery_start(
            spec.frame, spec.target, spec.traces_to,
            _upstream_path(state, spec), cwd,
        )
        if reply.code != 20:
            return _interview_stop(
                state,
                f"start вернул {reply.code}: "
                f"{reply.envelope['operation'].get('reason', '')}",
            )
        state.interview["session_id"] = reply.envelope["next_action"]["session_id"]
        state.status = "waiting_interview"
        op_complete(state, INTERVIEW_START, session_id=state.interview["session_id"])
        _print_answer_hint(state, reply)
        return False
    return _interview_poll(state, ops, spec, cwd)


def _step_branch(state: RunState, ops: Ops) -> bool:
    """S1: ветка `spec/<ws_id>-behaviour`; `ensure_branch` идемпотентен.

    Fail-closed dirty-гард (финальное ревью, круг 5): `target_dir` грязный
    (`git status --porcelain` непуст) ДО начала прогона — дальше по
    конвейеру `commit_paths` закоммитил бы рядом с чужими незакоммиченными
    правками. Проверяется, только пока `branch` ещё не заведена: ветка уже
    создана значит проверка на ЭТОМ прогоне уже пройдена, а грязь внутри
    неё — уже наши же авторенные файлы (S2/S3), не чужие. Resume после
    ручной очистки — обычный `advance()`: `branch` так и не стартовала,
    проверка просто повторяется.
    """
    key = "branch"
    state.branch = f"spec/{state.ws_id}-behaviour"
    if op_status(state, key) == "completed":
        return True
    if op_status(state, key) == "new" and ops.is_dirty(state.target_dir):
        print(
            f"_step_branch: target_dir {state.target_dir!r} грязный "
            "(git status --porcelain непуст) — прогон не начат"
        )
        state.status = "stopped_dirty"
        save(state)
        return False
    _ensure_started(state, key)
    ops.ensure_branch(state.target_dir, state.branch)
    op_complete(state, key)
    return True


def _brief_stop(state: RunState, message: str) -> bool:
    """Persist a named, resumable source failure before paid authoring."""
    (run_dir(state.run_id) / "brief-findings.txt").write_text(
        f"error GC-BRIEF-SOURCE: {message}\n", encoding="utf-8"
    )
    state.status = "stopped_author"
    save(state)
    return False


def _step_materialize_brief(state: RunState, ops: Ops) -> bool:
    """Copy durable intake bytes into the target branch before S2.

    The machine-local original path is intentionally absent from ``run.json``.
    ``runner.start`` has already copied and re-gated it under the run directory;
    every initial/resumed pass compares those bytes with the ledger descriptor.
    A completed op is not blindly trusted: destination bytes are checked again
    so manual tampering cannot flow into authoring or a commit.
    """
    if state.brief is None:
        return True
    key = "materialize-brief"
    intake_root = run_dir(state.run_id) / "brief-input"
    try:
        source = brief_input.inspect_materialized(intake_root, ".")
    except brief_input.BriefInputError as exc:
        return _brief_stop(state, f"durable intake не читается: {exc}")
    if source.as_state() != state.brief:
        return _brief_stop(
            state,
            "durable intake bytes не совпадают с descriptor в run.json",
        )

    if op_status(state, key) == "completed":
        try:
            destination = brief_input.inspect_materialized(
                Path(state.target_dir), state.bundle_dir
            )
        except brief_input.BriefInputError as exc:
            return _brief_stop(state, f"source layer в bundle повреждён: {exc}")
        if destination.as_state() != state.brief:
            return _brief_stop(
                state,
                "source layer в bundle не совпадает с descriptor в run.json",
            )
        return True

    _ensure_started(state, key)
    try:
        brief_input.materialize(
            source, Path(state.target_dir), state.bundle_dir
        )
        destination = brief_input.inspect_materialized(
            Path(state.target_dir), state.bundle_dir
        )
    except brief_input.BriefInputError as exc:
        return _brief_stop(state, f"materialization отказала: {exc}")
    if destination.as_state() != state.brief:
        return _brief_stop(
            state, "materialized source не совпадает с descriptor в run.json"
        )
    op_complete(state, key, source_blobs=state.brief["source_blobs"])
    return True


def _disp_behaviour_task(subject: str, bundle_path: str) -> str:
    """Task-текст для `ops.author_disp` (behaviour-spec узел, B2 Task 2).

    Требует DSL поведенческого узла (иначе `gate-candidate`, S4, не признаёт
    узел валидным): заголовок `#### BEH-NN`, поле `traces:`, пункт
    `- **checked_by**:`.
    """
    return (
        f"subject={subject!r} bundle={bundle_path}\n"
        "Author the behaviour-spec bundle node. Каждый пункт поведения — "
        "заголовок `#### BEH-NN`, поле `traces:` и пункт "
        "`- **checked_by**:`."
    )


#: Операторский чеклист контура `doc` — зеркало того, что S4
#: (`gate-candidate`) требует от DSL поведенческого узла. Состав объявляет
#: оператор (disputatio SPEC-002 §5.3), поэтому он живёт здесь, а не у
#: соседа: сойдётся документ или нет, решают НАШИ условия.
#:
#: Судья DSL — по-прежнему ТОЛЬКО S4 (devtools#204 п.1): чеклист не
#: заменяет гейт, а ведёт авторинг к тому, что гейт примет. Поэтому B0 —
#: не пересказ, а тот же текст `_AUTHOR_DSL["behaviour-spec"]`, которым
#: промпт авторинга требует frontmatter (`spec_stage`, `status`) и пин
#: `upstream_hashes`: у правила одно место, здесь — ссылка на него.
#:
#: `findings_item` обязателен и называет пункт-роль «нет blocker/major»
#: (§5.2 V8); остальные пункты — предметные.
_DOC_CHECKLIST: dict[str, str] = {
    "B0": _AUTHOR_DSL["behaviour-spec"],
    "B1": "каждый пункт поведения — заголовок `#### BEH-NN`",
    "B2": "у каждого BEH есть поле `traces:` и пункт `- **checked_by**:`",
    "B3": "нет blocker/major-находок",
}
_DOC_FINDINGS_ITEM = "B3"

#: `[limits]` сессии disputatio — обязательные целые (SPEC-002 §3.2, пример
#: `docs/document-pipeline.md` соседа). Значения примера, не наши догадки.
_DOC_LIMITS: dict[str, int] = {
    "max_rounds": 6,
    "max_total_tokens": 2_000_000,
    "max_wall_seconds": 7200,
    "schema_retries": 2,
}

#: Допустимые символы слага после первого (§4.1).
_SLUG_ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyz0123456789._-")


def _write_disp_doc_config(
    state: RunState, document_path: str, anchor_dir: Path
) -> str:
    """Конфиг вида `document` для прогона; возвращает путь к нему.

    Вид пайплайна disputatio выводит из ФОРМЫ секции `[pipeline]`
    (SPEC-002 §3.2), а не из флага: `document_path` без `spec_path`/
    `plan_path` и без `max_architectural_returns` — иначе `ConfigError` до
    любой мутации. Пишем ровно эту форму; пример в §3.2 буквально такой.

    Откуда каждый ключ (§3.2, форма вида `document`):

    * `document_path` — **`RelativePath`** (`contracts/pipeline.py`,
      `DocumentPipeline.document_path`), то есть путь ОТ КОРНЯ репозитория
      цели, который disp получает флагом `--root`. То, что сам конфиг лежит
      вне этого дерева, на резолв не влияет: относительные пути конфига
      резолвятся по `--root`, а не по каталогу конфига;
    * `soft_max_pipeline_tokens` / `soft_max_pipeline_wall_seconds` —
      мягкие лимиты пайплайна (§7.2), `0` = выключено;
    * `protected_branches` — как в примере §3.2;
    * `anchor_path` — КАТАЛОГ журналов целостности P9. Задаётся **явно**, и
      это условие (3) самого пункта плана: анкер обязан резолвиться вне
      рабочего дерева цели, иначе старт отказывает
      (`validate_anchor_path`: containment проверяется против toplevel репо
      цели, совпадение с границей — тоже нарушение). Выбор каталога —
      `_disp_anchor_dir`: каталог прогона, а при self-target (цель — сам
      devtools, `RUNS_ROOT` внутри её дерева) — пользовательский
      state-каталог;
    * `[agents.author]` / `[agents.reviewer]` / `[limits]` — секции сессии
      SPEC-001, общие для обоих видов. Они НЕ дефолтные (живой прогон
      2026-09-15, spec-runner#480: «нет обязательного ключа
      agents.author.adapter»): `adapter`/`model` обязательны у обеих ролей и
      берутся из харнесс-слоя оператора (`ops.disp_agent`), `[limits]` —
      четыре обязательных целых, значения — пример §3.2 (`_DOC_LIMITS`).

    Каталог прогона, а не рабочее дерево цели: конфиг — артефакт прогона,
    он не должен попадать в коммит узла (doc-scope контура ограничивает
    правки файлом узла, и лишний файл в дереве цели этот контур бы и
    заметил).
    """
    directory = run_dir(state.run_id)
    lines = [
        "[pipeline]",
        f'document_path = "{document_path}"',
        "soft_max_pipeline_tokens = 0",
        "soft_max_pipeline_wall_seconds = 0",
        'protected_branches = ["master", "main"]',
        f'anchor_path = "{anchor_dir}"',
        "",
        "[pipeline.checklists.doc]",
        f'findings_item = "{_DOC_FINDINGS_ITEM}"',
        "",
        "[pipeline.checklists.doc.items]",
    ]
    # TOML-экранирование: текст DSL несёт кавычки (`{requirements: "<hash>"}`).
    lines += [
        f'{key} = "{_toml_str(text)}"' for key, text in _DOC_CHECKLIST.items()
    ]
    for role in ("author", "reviewer"):
        adapter, model = disp_agent(role)
        lines += [
            "", f"[agents.{role}]",
            f'adapter = "{_toml_str(adapter)}"',
            f'model = "{_toml_str(model)}"',
        ]
    lines += ["", "[limits]"]
    lines += [f"{key} = {value}" for key, value in _DOC_LIMITS.items()]
    lines.append("")
    path = directory / "disp-doc.toml"
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _toml_str(text: str) -> str:
    """Тело TOML basic string: экранировать `\\` и `"`."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _disp_anchor_dir(state: RunState) -> Path | None:
    """Каталог анкера P9 — ВНЕ дерева цели, иначе disp откажет на старте.

    По умолчанию — каталог прогона (`run_dir/disp-anchors`): свой, рядом с
    конфигом. «Вне дерева цели по построению» это верно для любой цели,
    кроме одной (devtools#204 п.2): цель — сам devtools, и `RUNS_ROOT`
    лежит внутри её дерева. Тогда анкер — в пользовательском state-каталоге
    (`XDG_STATE_HOME`, иначе `~/.local/state`; тот же выбор, что у дефолта
    disp `_default_anchor_root`), под именем прогона. `None` — и он внутри
    цели (цель = домашний каталог?): анкер положить некуда, старт
    отказывает вслух, а не отдаёт соседу заведомо негодный путь.
    Containment — той же канонизацией, что у `validate_anchor_path`
    (`resolve`, совпадение с корнем — тоже нарушение).
    """
    target = Path(state.target_dir).expanduser().resolve()
    candidates = [run_dir(state.run_id) / "disp-anchors"]
    xdg = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    candidates.append(
        Path(xdg) / "devtools" / "disp-anchors" / state.run_id
    )
    for candidate in candidates:
        # Пинуется и уходит в конфиг КАНОНИЗИРОВАННЫЙ путь (ревью #242,
        # круг 5): относительный или `~`-`XDG_STATE_HOME` прошёл бы наш
        # containment по CWD раннера, а disp резолвил бы сырую строку от
        # `cwd=target_dir` — внутри цели — и отказал бы на старте.
        canonical = candidate.expanduser().resolve()
        if not canonical.is_relative_to(target):
            return canonical
    return None


def _pinned_disp_anchor_dir(state: RunState) -> Path | None:
    """Каталог анкера — из `run.json`, а не из окружения (ревью #242).

    Тот же принцип, что у слага: координата начатого пайплайна выбирается
    один раз write-ahead. `_disp_anchor_dir` читает `XDG_STATE_HOME`/`HOME`
    при каждом вызове, а retry из другого шелла/cron получил бы другой
    каталог — и `disp pipeline resume` искал бы журнал целостности там, где
    его нет. `None` — положить некуда, вызывающий останавливает прогон.
    """
    if state.disp_anchor_dir:
        return Path(state.disp_anchor_dir)
    chosen = _disp_anchor_dir(state)
    if chosen is None:
        return None
    state.disp_anchor_dir = str(chosen)
    save(state)
    return chosen


def _pinned_disp_slug(state: RunState) -> str:
    """Слаг пайплайна — из `run.json`, а не заново (devtools#204 п.3).

    Решение владельца в issue: слаг вычисляется нормализацией, и смена её
    правил осиротила бы уже начатый пайплайн (retry искал бы другой
    каталог). Пин — поле состояния `disp_slug`, записанное write-ahead до
    первого вызова соседа; операция авторинга его не несёт, потому что
    `_reset_stopped_author` снимает её целиком при retry.
    """
    if state.disp_slug:
        return state.disp_slug
    state.disp_slug = _disp_doc_slug(state)
    save(state)
    return state.disp_slug


#: Грамматика слага пайплайна disputatio (§4.1, `events/pipeline_paths.py`
#: `validate_slug`): `[a-z0-9][a-z0-9._-]{0,63}`, `fullmatch`. Наш `ws_id`
#: под неё НЕ подходит как есть — он обычно в верхнем регистре
#: (`WS-spec-runner-341`), и слаг `beh-WS-…` disp отверг бы `ValueError` →
#: `ConfigError` ещё до старта.
_SLUG_MAX = 64


def _disp_doc_slug(state: RunState) -> str:
    """Слаг пайплайна документа — `beh-<ws_id>`, приведённый к грамматике §4.1.

    Нормализация, а не проверка: отказывать на валидном для НАС `ws_id`
    (верхний регистр — обычная форма) значило бы упереться в чужую
    грамматику там, где приведение однозначно. Регистр вниз, недопустимые
    символы — в `-`, длина обрезается до 64.
    """
    raw = f"beh-{state.ws_id}".lower()
    slug = "".join(ch if ch in _SLUG_ALPHABET else "-" for ch in raw)
    return slug[:_SLUG_MAX]


def _step_authoring(state: RunState, ops: Ops) -> bool:
    """S2/S3: charter/requirements/behaviour-spec — файл есть → пропустить.

    B1-рулинг (финальное ревью F-6): все три узла авторятся общим
    `ops.author` (`codex exec --ephemeral`), а не циклом `disp --mode
    document` до сходимости, как буквально описывает спека §5 S3/§2. Замена
    осознанная для этапа B1; `disp`-цикл и критерий сходимости — предмет B2
    (OQ-1, `docs/superpowers/specs/2026-08-30-behaviour-spec-pipeline-design.md`).

    B2 Task 2: `state.author_backend == "disp"` переключает ТОЛЬКО
    behaviour-spec узел на `ops.author_disp`. Спека §5 называла «режим
    `document`»; режима с таким именем у disp нет — OQ-1 закрыт видом
    пайплайна `document` (disputatio#52 → их PR #64, 2026-09-01), который
    выводится из формы конфига, а не из флага. Раннер пишет этот конфиг в
    каталог прогона (`_write_disp_doc_config`) и передаёт слаг
    `beh-<ws_id>`; команда — `disp pipeline run`.
    charter/requirements всегда остаются на `ops.author` (codex) независимо
    от `author_backend` — цикл полировки осмыслен для документа, который
    доводят до сходимости, не для одноразовых артефактов.

    Preflight (Task 8; хардкод имени профиля снят фикс-раундом ревью) — ДО
    фактического авторинга узлов `design`/`decomposition` (Task 5 обобщила
    одиночную проверку на кортеж узлов): решение «авторить ли узел»
    data-driven для ЛЮБОГО профиля, не только `profiles/team-exp.yaml` —
    `target_profile_declares(state.target_dir, state.profile, node)`
    читает ФАКТИЧЕСКОЕ содержимое target-профиля. Декларирует оба узла ⇒
    авторим как обычно; не декларирует хотя бы один ⇒ `stopped_preflight`
    с той же rollout-процедурой — ни молчаливого авторинга (старая копия
    team-exp.yaml у соседнего репо, Task 8), ни молчаливого скипа (профиль,
    у которого узла нет вовсе). Конвейер СЕЙЧАС не поддерживает профили
    «сознательно без design/decomposition» — для него отсутствие узла в
    target-профиле всегда останов, не тихий skip; такой профиль либо
    доставляет обновлённый файл (rollout), либо вообще не идёт через этот
    раннер. Проверка стоит ПЕРЕД циклом (план Task 8 Step 2: «вызов в
    start (до S2)»; minor PR-ревью #145 — проверка на итерации design
    оставляла три оплаченных вызова авторинга и три файла в worktree до
    останова).
    Преflight охраняет ПРЕДСТОЯЩИЙ авторинг: когда все author-шаги уже
    завершены (resume после S2, вручную собранный бандл), проверять
    нечего — несоответствие профиля поймает S4 (`gate-check --candidate`).
    """
    authoring_pending = any(
        op_status(state, key) != "completed" for key, _, _ in _AUTHOR_STEPS
    )
    if authoring_pending:
        for node in ("design", "acceptance", "decomposition"):
            if not target_profile_declares(
                state.target_dir, state.profile, node
            ):
                print(
                    f"_step_authoring: {state.target_dir}/{state.profile} "
                    f"не декларирует узел {node!r} — "
                    f"{PREFLIGHT_PROCEDURE_HINT}"
                )
                state.status = "stopped_preflight"
                save(state)
                return False
    for key, kind, filename in _AUTHOR_STEPS:
        if kind == "behaviour-spec" and state.brief is not None:
            descriptor = state.brief
            source_rel = descriptor.get("requirements_source")
            if not isinstance(source_rel, str):
                return _brief_stop(
                    state, "run.json не несёт requirements_source"
                )
            bundle = Path(state.target_dir) / state.bundle_dir
            try:
                source_text = (bundle / source_rel).read_text(encoding="utf-8")
                requirements_text = (bundle / "10-requirements.md").read_text(
                    encoding="utf-8"
                )
            except (OSError, UnicodeError) as exc:
                return _brief_stop(
                    state, f"source/requirements не читаются: {exc}"
                )
            findings = brief_input.requirements_findings(
                source_text, requirements_text
            )
            if findings:
                (run_dir(state.run_id) / "brief-findings.txt").write_text(
                    "\n".join(
                        f"error GC-BRIEF-COVERAGE: {finding}"
                        for finding in findings
                    ) + "\n",
                    encoding="utf-8",
                )
                state.status = "stopped_author"
                save(state)
                return False
            (run_dir(state.run_id) / "brief-findings.txt").unlink(
                missing_ok=True
            )
        if op_status(state, key) == "completed":
            continue
        target = Path(state.target_dir) / state.bundle_dir / filename
        disp_node = kind == "behaviour-spec" and state.author_backend == "disp"
        started_here = disp_node and bool(state.disp_slug)
        slug = state.disp_slug or (_disp_doc_slug(state) if disp_node else "")
        pipeline_dir = (
            Path(state.target_dir) / ".disputatio" / "pipelines" / slug
        )
        # Черновик, который написал НАЧАТЫЙ этим прогоном пайплайн соседа, —
        # не готовый узел (ревью #242): пока каталог пайплайна жив, retry
        # идёт в `resume`, а не глотает черновик как `skipped`. Признак
        # «начат этим прогоном» — пин слага в run.json (write-ahead до
        # первого вызова соседа). Операторский выход из пинованного
        # состояния (ревью #242, круг 3) — тот же файл без каталога
        # пайплайна: убрал каталог (или сосед его не создал) и положил/
        # починил узел руками ⇒ узел принимается как есть, как у codex.
        if disp_node and pipeline_dir.is_dir() and not started_here:
            # Ревью #242: продолжать чужой/заброшенный пайплайн с нашим
            # конфигом и анкером нельзя — `resume` соседа доверял бы чужому
            # манифесту, а его черновик узла — не наш готовый узел. Поэтому
            # проверка стоит ДО skip-ветки (круг 4) и до пинов: первый
            # старт не пинует координаты пайплайна, который не наш. Стоп с
            # подсказкой, решение — оператору.
            print(
                f"_step_authoring: каталог пайплайна {pipeline_dir} уже "
                "существует, но этот прогон его не начинал — продолжите "
                f"его вручную (`disp pipeline resume --slug {slug}`) "
                "либо уберите каталог и повторите resume"
            )
            state.status = "stopped_author"
            save(state)
            return False
        if target.exists() and not (started_here and pipeline_dir.is_dir()):
            op_complete(state, key, skipped=True)
            continue
        _ensure_started(state, key)
        if disp_node:
            bundle_path = f"{state.bundle_dir}/{filename}"
            task = _disp_behaviour_task(state.subject, bundle_path)
            anchor_dir = _pinned_disp_anchor_dir(state)
            if anchor_dir is None:
                print(
                    "_step_authoring: анкер P9 некуда положить вне дерева "
                    f"цели {state.target_dir!r} — ни каталог прогона, ни "
                    "XDG_STATE_HOME не лежат снаружи; задайте "
                    "XDG_STATE_HOME вне цели и повторите resume"
                )
                state.status = "stopped_author"
                save(state)
                return False
            try:
                config_path = _write_disp_doc_config(
                    state, bundle_path, anchor_dir
                )
            except ValueError as exc:
                # Харнесс-слой не даёт adapter/model для disp (например codex
                # без модели) — стоп с причиной до вызова соседа.
                print(f"_step_authoring: конфиг disp не собрать: {exc}")
                state.status = "stopped_author"
                save(state)
                return False
            slug = _pinned_disp_slug(state)
            # Ровно один путь retry (devtools#204 п.3): пайплайн начат этим
            # прогоном и его каталог есть ⇒ `resume` (на нём `run`
            # отказывает по коду соседа); каталога нет ⇒ `run`.
            resume = started_here and pipeline_dir.is_dir()
            if not resume:
                # Сосед стартует только на чистом дереве (document-pipeline.md
                # §2: первый PROPOSING делает `reset --hard` + `clean`, потому
                # untracked-путь вне `.disputatio/` — отказ, не потеря). Узлы
                # до behaviour и source-слой E1 лежат в бандле untracked —
                # коммитим их той же процедурой, что S3 (живой прогон
                # spec-runner#480: без этого disp вернул 2). Перед `resume`
                # не коммитим: черновик узла живого пайплайна — не наш.
                _commit_bundle(
                    state, ops,
                    f"docs(governance): behaviour bundle {state.ws_id} — "
                    f"{state.subject}: узлы до disp-авторинга",
                )
            exit_code = ops.author_disp(
                state.target_dir, task, config_path, slug, resume=resume,
            )
            if exit_code != 0:
                # Процедура, а не только факт (ревью #242, круг 3): как
                # выйти из пинованного состояния руками.
                print(
                    f"_step_authoring: disp вернул {exit_code} по слагу "
                    f"{slug}. Повтор — `resume` (продолжит пайплайн "
                    f"{pipeline_dir}); принять узел руками — уберите этот "
                    "каталог, положите/почините файл узла и повторите "
                    "resume: файл будет принят как есть. Тот же выход, если "
                    "пайплайн уже терминален (сошёлся, а прогон упал до "
                    "записи результата): `resume` соседа на терминальном "
                    "пайплайне отказывает (PipelineNotResumable)"
                )
        else:
            author_args = (
                state.target_dir, kind, state.subject, state.bundle_dir
            )
            if state.brief is not None and kind in (
                "charter", "requirements",
            ):
                exit_code = ops.author(
                    *author_args, brief_context=state.brief
                )
            else:
                # Preserve the exact classic API call and prompts. In
                # particular, third-party test/fake Ops implementations with
                # the pre-E1 signature remain valid for no-brief runs.
                exit_code = ops.author(*author_args)
        if exit_code != 0:
            state.status = "stopped_author"
            save(state)
            return False
        op_complete(state, key, skipped=False, exit=exit_code)
    return True


def _step_commit(state: RunState, ops: Ops) -> bool:
    """S3 (продолжение): коммит авторенного контента до push (F-6).

    Между авторингом и push не было коммита: `ops.author` (`codex exec`)
    не гарантированно коммитит сам, поэтому push уходил пустым и
    `create_draft_pr` падал неперехваченным `CalledProcessError`. Отдельный
    op `commit`, идемпотентный (пустой индекс — не ошибка, см.
    `RealOps.commit_paths`).

    Коммитится ТОЛЬКО `bundle_dir` (круг 5, codex-ревью PR #88): `git add
    -A` сгребал бы в этот коммит и чужие незакоммиченные изменения где
    угодно в `target_dir` — заменено на явный список путей.
    """
    key = "commit"
    if op_status(state, key) == "completed":
        return True
    _ensure_started(state, key)
    _commit_bundle(
        state, ops,
        f"docs(governance): behaviour bundle {state.ws_id} — {state.subject}",
    )
    if state.brief is not None and not _source_layer_committed(state, ops):
        return False
    op_complete(state, key)
    return True


def _commit_bundle(state: RunState, ops: Ops, subject_line: str) -> None:
    """Коммит `bundle_dir` (и source-слоя E1 с `-f`) под заданной темой.

    Одна процедура на два входа: S3 (`_step_commit`) и коммит перед первым
    `run` disp-авторинга, которому сосед требует чистое дерево. Идемпотентна:
    пустой индекс — не ошибка (`RealOps.commit_paths`).
    """
    message = (
        f"{subject_line}\n\n"
        "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
    )
    force: tuple[str, ...] = ()
    if state.brief is not None:
        # Source-слой E1 лежит в подкаталоге bundle_dir; ignore-правила
        # репо-цели (`workstreams/*/spec/*` + `!*.md`) его не пускают —
        # добавляем поштучно с `-f`; S3 ниже сверяет, что он в HEAD.
        force = tuple(
            f"{state.bundle_dir}/{rel}" for rel in state.brief["source_paths"]
        )
    ops.commit_paths(
        state.target_dir, [state.bundle_dir], message, force_paths=force,
    )


def _source_layer_committed(state: RunState, ops: Ops) -> bool:
    """Fail-closed после S3: каждый source-файл лежит в HEAD своими байтами.

    Сверка по blob-ам descriptor'а, не по факту вызова `git add`: ignore-
    правило цели, чужой хук или усечённый add оставляют файл вне коммита
    молча, а bundle-PR без источника — это charter с пинами на blob-ы,
    которых у читателя нет (spec-runner#490, круг 1 ревью).
    """
    assert state.brief is not None
    head = ops.rev_parse(state.target_dir, "HEAD")
    if head is None:
        return _brief_stop(state, "HEAD не резолвится после коммита S3")
    paths = list(state.brief["source_paths"])
    blobs = list(state.brief["source_blobs"].values())
    if len(paths) != len(blobs):
        return _brief_stop(
            state, "descriptor brief: source_paths и source_blobs разной длины"
        )
    for rel, expected in zip(paths, blobs):
        full = f"{state.bundle_dir}/{rel}"
        actual = ops.blob_in_commit(state.target_dir, head, full)
        if actual != expected:
            return _brief_stop(
                state,
                f"source layer не в коммите S3: {full} — в HEAD "
                f"{actual or 'отсутствует'}, ожидался blob {expected} "
                "(ignore-правило .gitignore цели? add без -f?)",
            )
    return True


def _frontmatter(text: str) -> str:
    """YAML-frontmatter между первыми двумя `---`-строками (или пусто)."""
    match = re.match(r"^---\n(.*?)\n---", text, re.S)
    return match.group(1) if match else ""


def _upstream_pin(front: str, upstream: str) -> str | None:
    """Пин `upstream_hashes[upstream]` СТРОГО внутри этого YAML-поля.

    Обе формы — inline (`upstream_hashes: {requirements: "<hash>"}`) и
    блочная; блочная читается только как непрерывный блок отступленных
    строк сразу за ключом (приёмка PR #101, круг 4: срез «до конца
    frontmatter» принимал одноимённый ПОСТОРОННИЙ верхнеуровневый ключ за
    пин — fail-open). Пустой mapping → None → GC-UNPINNED у вызывающего.
    """
    match = re.search(r"^upstream_hashes:(.*)$", front, re.M)
    if not match:
        return None
    inline = match.group(1).strip()
    if inline:
        pin = re.search(rf"\b{upstream}:\s*[\"']?([0-9a-f]{{40}})", inline)
        return pin.group(1) if pin else None
    block: list[str] = []
    for line in front[match.end():].lstrip("\n").splitlines():
        if line.startswith((" ", "\t")):
            block.append(line)
        else:
            break
    pin = re.search(
        rf"^\s+{upstream}:\s*[\"']?([0-9a-f]{{40}})",
        "\n".join(block), re.M,
    )
    return pin.group(1) if pin else None


def _step_gate(state: RunState, ops: Ops) -> bool:
    """S4: prospective-гейт публичным `gate-check --candidate` (steward#140).

    Миграция с internal content-check API (candidate_state поверх трёх
    пинованных символов) на CLI-контракт steward @ 2c71ed7
    (docs/gate-check-candidate.md): коды 0 чисто / 1 error-находки /
    2 config error; ref-зависимые гейты честно объявляются not_evaluated.
    Оба ненулевых кода — стоп: config error (2) не тише находок, он значит
    «гейт не смог судить» и уплыть зелёным не имеет права (fail-closed).
    candidate_state остаётся у консоли (bundle_summary) — её view-model этот
    шаг не трогает.
    """
    key = "gate-candidate"
    if op_status(state, key) == "completed":
        return True
    _ensure_started(state, key)
    rc, output = ops.gate_check_candidate(
        state.target_dir, state.bundle_dir, state.profile
    )
    if rc != 0:
        (run_dir(state.run_id) / "gate-findings.txt").write_text(
            output if output.endswith("\n") or not output else output + "\n",
            encoding="utf-8",
        )
        state.status = "stopped_gate"
        save(state)
        return False
    # Гард отсутствия design/decomposition (спека Task 4, обобщено Task 6):
    # required-узел — локальный (не через bundle_state.candidate_state — та
    # остаётся у консоли, runner без импорта steward). MINOR-1 финального
    # ревью: посылка «без чтения/парсинга YAML профиля» истекла с Task 8 —
    # `target_profile_declares` уже читает ФАКТИЧЕСКОЕ содержимое
    # target-профиля (тот же источник, что и preflight в `_step_authoring`
    # выше по файлу), не сравнивает имя файла со строкой `profiles/
    # team-exp.yaml`. Прочие профили (например, fixture `mini.yaml` в
    # интеграционном тесте шва CLI ниже по файлу — design/decomposition туда
    # сознательно не входят) не затрагиваются — они просто не декларируют
    # эти узлы. Стоит ДО цикла рёбер ниже — иначе отсутствующий узел молча
    # читался бы как «нечего проверять по рёбрам» вместо явной находки;
    # design проверяется раньше decomposition — тот же порядок, что и раньше
    # (единственный required-узел до Task 6), останов на первом отсутствующем.
    node_paths: dict[str, Path] = {}
    for node, node_file in (
        ("design", "20-design.md"),
        ("acceptance", "25-acceptance.md"),
        ("decomposition", "30-decomposition.md"),
    ):
        node_paths[node] = Path(state.target_dir) / state.bundle_dir / node_file
        node_required = target_profile_declares(
            state.target_dir, state.profile, node
        )
        if node_required and not node_paths[node].exists():
            (run_dir(state.run_id) / "gate-findings.txt").write_text(
                f"error GC-COMPLETENESS({node}): required-узел {node} "
                f"отсутствует в бандле ({node_file})\n",
                encoding="utf-8",
            )
            state.status = "stopped_gate"
            save(state)
            return False
    # Гарды GC-UNPINNED/GC-STALE(prospective) поверх CLI (приёмка PR #101,
    # оба круга major): stale-каскад gate-check исполняется только на
    # status: approved артефактах, поэтому DRAFT-узел с объявленным ребром
    # проходит CLI молча — и без пина вовсе, и с синтаксически похожим, но
    # ЛОЖНЫМ пином. Контракт конвейера — «пин в том же PR И пин верен»
    # (спека §S4: prospective-сравнение upstream_hashes с blob-хешами
    # worktree; первый прогон WS-kapelle-47 встал именно на GC-UNPINNED).
    # Всё stdlib-ом (blob_sha1 — свой), runner остаётся без импорта steward.
    local_findings: list[str] = []
    for fname, upstream, upstream_fname, required in _GATE_EDGES:
        path = Path(state.target_dir) / state.bundle_dir / fname
        if not path.exists():
            continue
        front = _frontmatter(path.read_text(encoding="utf-8"))
        declares = re.search(
            rf"^\s*-\s+{upstream}\s*$|traces_to:.*\b{upstream}\b",
            front, re.M,
        )
        if not declares:
            # required=True (оба ребра design — MAJOR-1, финальное ревью):
            # design c `traces_to: [requirements]` (ребро behaviour-spec не
            # объявлено) раньше проходило S4 молча — `continue` читал
            # необъявленное required-ребро как «нечего проверять», хотя
            # спека требует prospective-проверку ОБОИХ рёбер design.
            # Необязательные рёбра (requirements→charter,
            # behaviour-spec→requirements) остаются как раньше: их
            # traces_to не входит в prospective-контракт этого гарда.
            if required:
                local_findings.append(
                    f"error GC-UNPINNED(prospective): {fname} — ребро "
                    f"{upstream} не объявлено в traces_to"
                )
            continue
        pin = _upstream_pin(front, upstream)
        if pin is None:
            local_findings.append(
                f"error GC-UNPINNED(prospective): {fname} — объявленное "
                f"ребро {upstream} без пина upstream_hashes (пин обязан "
                "ехать в том же PR)"
            )
            continue
        upstream_path = Path(state.target_dir) / state.bundle_dir / upstream_fname
        actual = (
            blob_sha1(upstream_path.read_text(encoding="utf-8"))
            if upstream_path.exists()
            else None
        )
        if actual != pin:
            local_findings.append(
                f"error GC-STALE(prospective): {fname} — пин {upstream} "
                f"({pin[:8]}…) не совпадает с blob-хешем "
                f"{upstream_fname} в worktree "
                f"({actual[:8] + '…' if actual else 'файла нет'})"
            )
    if state.brief is not None:
        source_inputs = bundle_inputs.direct_blobs(
            state, ops, bundle_dag.BUNDLE_DAG, "charter", None
        )
        if source_inputs.outcome is not Outcome.FOUND or source_inputs.value is None:
            local_findings.append(
                "error GC-BRIEF-SOURCE(prospective): direct inputs charter "
                f"не установлены — {source_inputs.detail}"
            )
        else:
            charter = Path(state.target_dir) / state.bundle_dir / "00-charter.md"
            front = _frontmatter(charter.read_text(encoding="utf-8"))
            for source_name, actual in source_inputs.value.items():
                declares = re.search(
                    rf"^\s*-\s+{re.escape(source_name)}\s*$|"
                    rf"traces_to:.*\b{re.escape(source_name)}\b",
                    front,
                    re.M,
                )
                if not declares:
                    local_findings.append(
                        "error GC-UNPINNED(prospective): 00-charter.md — "
                        f"source-ребро {source_name} не объявлено в traces_to"
                    )
                    continue
                pin = _upstream_pin(front, source_name)
                if pin is None:
                    local_findings.append(
                        "error GC-UNPINNED(prospective): 00-charter.md — "
                        f"source-ребро {source_name} без upstream_hashes"
                    )
                elif pin != actual:
                    local_findings.append(
                        "error GC-STALE(prospective): 00-charter.md — source "
                        f"пин {source_name} ({pin[:8]}…) != blob "
                        f"({actual[:8]}…)"
                    )
    # Гард вакуумного зелёного (боевой прогон kapelle#47): узел может быть
    # candidate_valid при НУЛЕ распознаваемых DSL-заголовков — гейту steward
    # нечего флагать, когда автор писал в своём диалекте (`### BS-*`/`REQ-*`),
    # и пустая по сути спека уплывала бы в PR зелёной. Файл читается только
    # если существует: отсутствие — территория required_absent выше.
    # MINOR-2 финального ревью: 4-й элемент — ожидаемая форма ДЛЯ
    # СООБЩЕНИЯ конкретного файла (design несёт свою грамматику Q, не
    # чужую FR-NN/BEH-NN requirements/behaviour-spec — сообщение раньше
    # хардкодило последнюю для всех трёх). Паттерн design синхронизирован
    # с `design_guard._DESIGN_Q_RE` (`\s*·\s*`, не буквальный один пробел
    # с каждой стороны «·») — иначе гейт стопил бы заголовок, который
    # реальный парсер УЖЕ признаёт валидным.
    dsl_empty: list[str] = list(local_findings)
    for fname, pattern, label, expected_form in (
        ("10-requirements.md", r"^#### FR-\d", "FR-требований", "#### FR-NN:"),
        (
            "15-behaviour-spec.md", r"^#### BEH-\d", "BEH-сценариев",
            "#### BEH-NN:",
        ),
        (
            "20-design.md",
            r"^####\s+Q-\d+\s*·\s*|^Открытых архитектурных вопросов нет",
            "резолюций design",
            "#### Q-NN · owner_role: … · resolution: …",
        ),
        (
            "25-acceptance.md",
            r"^####\s+AC-\d+[a-z]?:|^Must-требований во входном наборе нет",
            "критериев приёмки",
            "#### AC-NN: <название> · verification: test|manual|metric",
        ),
        (
            "30-decomposition.md",
            r"^####\s+DT-\d+:",
            "DT-задач",
            "#### DT-NN: <название> · type: implement|verify · owner: <роль>",
        ),
    ):
        path = Path(state.target_dir) / state.bundle_dir / fname
        if path.exists() and not re.search(
            pattern, path.read_text(encoding="utf-8"), re.M
        ):
            dsl_empty.append(
                f"error GC-DSL-EMPTY(prospective): {fname} — 0 распознаваемых "
                f"{label} (ожидается DSL `{expected_form}`)"
            )
    if dsl_empty:
        (run_dir(state.run_id) / "gate-findings.txt").write_text(
            "\n".join(dsl_empty) + "\n", encoding="utf-8"
        )
        state.status = "stopped_gate"
        save(state)
        return False
    # Гард покрытия Q (спека Task 4): architects-вопросы requirements без
    # резолюции в design — отдельная находка от UNPINNED/STALE/DSL-EMPTY
    # выше. Оба файла проверяются на существование явно: node_paths["design"]
    # гарантирован гардом отсутствия только когда профиль несёт узел design
    # (`node_required` выше, узел `"design"`) — профиль без него (мимо этого
    # шва) сюда доходит с design отсутствующим, и читать его было бы TOCTOU.
    req_path = Path(state.target_dir) / state.bundle_dir / "10-requirements.md"
    design_path = node_paths["design"]
    if req_path.exists() and design_path.exists():
        coverage = [
            f"error GC-DESIGN-COVERAGE: {finding}"
            for finding in design_guard.coverage_findings(
                req_path.read_text(encoding="utf-8"),
                design_path.read_text(encoding="utf-8"),
            )
        ]
        if coverage:
            (run_dir(state.run_id) / "gate-findings.txt").write_text(
                "\n".join(coverage) + "\n", encoding="utf-8"
            )
            state.status = "stopped_gate"
            save(state)
            return False
    # Гард графа DT (спека Task 6): инварианты `decomposition_guard.
    # graph_findings` (сюръекция BEH, single-owner, verify-контракт,
    # ацикличность, стоки групп) — отдельная находка от
    # UNPINNED/STALE/DSL-EMPTY/GC-DESIGN-COVERAGE выше. Оба файла
    # проверяются на существование явно, тем же паттерном: decomposition
    # отсутствующий (профиль без узла) сюда доходит без стопа выше только
    # когда узел не required — читать его было бы TOCTOU.
    # warnings — накапливается через ВЕСЬ остаток _step_gate (round 7
    # ревью PR #161, минор: давний баг рядом с этим кодом — каждая
    # находка ниже писала gate-findings.txt через write_text, ЗАТИРАЯ
    # предыдущую запись целиком; для fatal-веток это было безопасно (стоп
    # сразу же), но non-fatal warning, введённый round 6, — единственная
    # НЕ останавливающая запись в этой функции, и более поздний fatal-стоп
    # (например, GC-AC-COVERAGE) стирал её молча). Каждая ветка write_text
    # НИЖЕ обязана включать `warnings` в свою запись, а не только
    # собственные находки.
    warnings: list[str] = []
    beh_path = Path(state.target_dir) / state.bundle_dir / "15-behaviour-spec.md"
    decomp_path = node_paths["decomposition"]
    if beh_path.exists() and decomp_path.exists():
        decomp_text = decomp_path.read_text(encoding="utf-8")
        beh_text = beh_path.read_text(encoding="utf-8")
        # Non-fatal находки про verifies (round 7 ревью PR #161, минор;
        # комментарий исправлен round 10 — прежняя версия обещала фильтр
        # внутри `graph_findings`, которого там нет): `graph_findings`
        # (ниже) про эти находки НЕ ЗНАЕТ ВООБЩЕ — она их не вычисляет и
        # не фильтрует, каждый вызов парсит decomposition сам по себе
        # (`non_fatal_findings` и `graph_findings` — два независимых
        # прохода `parse_dt_tasks`, decomposition читается дважды за этот
        # гейт; общий у них только сам модуль-источник,
        # `_orphan_verifies_findings`, round 13 переименован из
        # `_verify_group_and_orphan_findings` — единственная non-fatal
        # находка теперь одна: опечатка/осиротевший путь). НЕ fatal
        # (легаси-бандл с checked_by-целью, но без verifies, обязан пройти
        # гейт), но обязаны быть видимы оператору. Пишутся В ТОТ ЖЕ
        # gate-findings.txt
        # как `warning GC-DT-GRAPH:` (не `error`) и НЕ останавливают
        # прогон: `console_model` просто конкатенирует файл как показ
        # оператору, не как управляющий сигнал (state.status — единственный
        # авторитетный источник «гейт стоп/прошёл»).
        warnings = [
            f"warning GC-DT-GRAPH: {f}"
            for f in decomposition_guard.non_fatal_findings(
                beh_text, decomp_text
            )
        ]
        try:
            selector_policy = target_selector_policy(state.target_dir)
        except ValueError as exc:
            graph_findings = [f"selector dictionary: {exc}"]
        else:
            graph_findings = decomposition_guard.graph_findings(
                beh_text,
                decomp_text,
                selector_policy=selector_policy,
            )
        graph = [
            f"error GC-DT-GRAPH: {finding}"
            for finding in graph_findings
        ]
        if graph:
            (run_dir(state.run_id) / "gate-findings.txt").write_text(
                "\n".join(warnings + graph) + "\n", encoding="utf-8"
            )
            state.status = "stopped_gate"
            save(state)
            return False
    # Гард контракта DT (#282, срез 1): версия DT-диалекта и форма
    # `delivers`. Отдельная находка от GC-DT-GRAPH выше и, в отличие от
    # неё, НЕ обусловлена наличием behaviour-spec: предмет проверки —
    # один документ decomposition, и требовать соседа значило бы молча
    # пропускать контракт в профиле без behaviour-spec.
    #
    # Разделение ответственности после среза 2 (правка minor ревью #290 —
    # прежняя редакция описывала барьер моста как действующий, хотя этот
    # же срез его снял; в репо, где комментарии несут контракт, это была
    # ложная посылка для следующей правки).
    #
    # Гейт — ЕДИНСТВЕННЫЙ судья формы `delivers` и разрешимости `sources`:
    # мост на форму не полагается и второго её судьи не заводит. Что
    # объявленное реально доехало до tasks.md, гейт не проверяет и не
    # может — это доказывают пост-условия `task_bridge`, дважды: после
    # рендера и после переноса состояния §I11. Зелёный гейт здесь
    # означает «форма верна и ссылки разрешимы», а не «результаты
    # поставки доставлены».
    if decomp_path.exists():
        # Индекс узлов бандла для разрешения `sources` (#282, срез 2).
        # Файлы читает гейт, а не гвард: тот остаётся чистой функцией над
        # строками, иначе его нельзя звать из гейта и моста одним
        # способом. Ключ индекса — имя узла, как его пишет ссылка
        # (`acceptance#AC-07`), а не имя файла.
        #
        # Индексируются ТОЛЬКО существующие файлы, и отсутствующий узел
        # остаётся вне индекса намеренно: ссылка на него получит отказ
        # «узла нет в бандле» — честную причину, отличную от «пункт не
        # найден». Пустой индекс (бандл без единого узла) — законный
        # вход, на котором не разрешается ни одна ссылка.
        node_index = {
            node: decomposition_guard.node_ids(
                path.read_text(encoding="utf-8")
            )
            for node, path in (
                ("requirements", req_path),
                ("design", node_paths["design"]),
                ("behaviour-spec", beh_path),
                ("acceptance", node_paths["acceptance"]),
                ("decomposition", decomp_path),
            )
            if path.exists()
        }
        dt_errors, dt_warnings = decomposition_guard.dt_contract_findings(
            decomp_path.read_text(encoding="utf-8"),
            node_index=node_index,
            allow_legacy_dt=state.allow_legacy_dt,
        )
        warnings.extend(
            f"warning GC-DT-CONTRACT: {finding}" for finding in dt_warnings
        )
        contract = [
            f"error GC-DT-CONTRACT: {finding}" for finding in dt_errors
        ]
        if contract:
            (run_dir(state.run_id) / "gate-findings.txt").write_text(
                "\n".join(warnings + contract) + "\n", encoding="utf-8"
            )
            state.status = "stopped_gate"
            save(state)
            return False
    # Гард Must-покрытия acceptance (Task 6 плана acceptance-node):
    # инварианты `acceptance_guard.coverage_findings` (Must-FR/NFR без
    # покрывающего AC, ссылочная целостность AC на requirements/
    # behaviour-spec, достоверность декларации пустого Must-множества) —
    # отдельная находка от UNPINNED/STALE/DSL-EMPTY/GC-DESIGN-COVERAGE/
    # GC-DT-GRAPH выше. Оба входа и acceptance проверяются на существование
    # явно, тем же паттерном, что и GC-DT-GRAPH: acceptance отсутствующий
    # (профиль без узла) сюда доходит без стопа выше только когда узел не
    # required — читать его было бы TOCTOU.
    acc_path = node_paths["acceptance"]
    if req_path.exists() and beh_path.exists() and acc_path.exists():
        ac_cov = [
            f"error GC-AC-COVERAGE: {finding}"
            for finding in acceptance_guard.coverage_findings(
                req_path.read_text(encoding="utf-8"),
                beh_path.read_text(encoding="utf-8"),
                acc_path.read_text(encoding="utf-8"),
            )
        ]
        if ac_cov:
            (run_dir(state.run_id) / "gate-findings.txt").write_text(
                "\n".join(warnings + ac_cov) + "\n", encoding="utf-8"
            )
            state.status = "stopped_gate"
            save(state)
            return False
    # Гейт целиком прошёл (ни одна fatal-ветка не остановила) — накопленные
    # warning'и (если есть) пишутся ЗДЕСЬ, одной записью, а не сразу после
    # GC-DT-GRAPH: та ранняя запись стиралась бы любым fatal-стопом ниже
    # (см. комментарий про `warnings` выше).
    if warnings:
        (run_dir(state.run_id) / "gate-findings.txt").write_text(
            "\n".join(warnings) + "\n", encoding="utf-8"
        )
    op_complete(state, key, exit=rc)
    return True


def _step_push(state: RunState, ops: Ops) -> bool:
    """S5a: push черновой ветки."""
    key = "push"
    if op_status(state, key) == "completed":
        return True
    _ensure_started(state, key)
    ops.push_branch(state.target_dir, state.branch)
    op_complete(state, key)
    return True


def _step_pr(state: RunState, ops: Ops) -> bool:
    """S5b: PR черновиком; started → find_pr первым — второй PR не открывать.

    ``find_pr`` поднимает ``RuntimeError`` на транзиентном сбое `gh` (не
    отличимом от «PR нет» иначе — финальное ревью F-5): реконсиляция обязана
    остановиться, а не читать сбой опроса как «PR не создан» и открывать
    второй PR на ту же ветку. Op остаётся ``started``, статус run'а не
    меняется — следующий `advance()`/`resume()` попробует снова.
    """
    key = "pr"
    status = op_status(state, key)
    if status == "completed":
        return True
    if status == "started":
        try:
            existing = ops.find_pr(state.repo_slug, state.branch)
        except RuntimeError as exc:
            print(f"_step_pr: реконсиляция find_pr не удалась: {exc}")
            return False
        if existing is not None:
            state.pr = existing
            op_complete(state, key, number=existing)
            return True
    else:
        op_start(state, key)
    title = f"{state.subject} — behaviour bundle {state.ws_id}"
    body = f"Автоматический прогон governance runner'а ({state.run_id})."
    pr_number = ops.create_draft_pr(
        state.target_dir,
        state.repo_slug,
        state.branch,
        title,
        body,
        "",
    )
    state.pr = pr_number
    op_complete(state, key, number=pr_number)
    return True


def _step_ready(state: RunState, ops: Ops) -> bool:
    """S6a: снять draft-статус перед ревью."""
    key = "ready"
    if op_status(state, key) == "completed":
        return True
    _ensure_started(state, key)
    ops.mark_ready(state.repo_slug, state.pr)
    op_complete(state, key)
    return True


def _file_missing_refute_candidates(body: str) -> list[str] | None:
    """Пути «отсутствующих» файлов, если ВСЕ блокирующие находки — file-missing.

    Разбирает markdown-рендер вердикта кита (steward @ 2c71ed7, схема v2):
    секция находки начинается `### [severity] title — \\`file:line\\``,
    блокирующая несёт строку «БЛОКИРУЕТ», file-missing — kindline
    «Тип: `file-missing`». None — авто-опровержение неприменимо (нет
    блокирующих, либо среди них есть содержательная находка): смешанный
    вердикт останавливается на человеке целиком, опровергать «половину»
    машина не имеет права (спека §7).
    """
    sections = re.split(r"(?m)^### ", body)[1:]
    paths: list[str] = []
    blocking_seen = False
    for section in sections:
        if "БЛОКИРУЕТ" not in section:
            continue
        blocking_seen = True
        # Точная kindline рендера apply-threshold, не подстрока по секции
        # (приёмка PR #102, major): defect-находка, чей title/сценарий
        # УПОМИНАЕТ литерал file-missing (например, дефект обработки этого
        # типа), не имеет права классифицироваться как file-missing.
        if not re.search(r"(?m)^- Тип: `file-missing`", section):
            return None
        # Путь — из ХВОСТА первой строки секции (приёмка PR #102, круг 3):
        # рендерер ставит настоящий `file:line` в конец заголовка, а title —
        # модельный текст и может сам содержать поддельный фрагмент
        # «— `x:0`»; ленивый матч слева брал бы его. Плюс форма пути:
        # относительный, без `..` — иначе кандидат не признаётся.
        first_line = section.splitlines()[0]
        header = re.search(r"—\s+`([^:`]+):\d+`\s*$", first_line)
        if not header:
            return None
        path = header.group(1)
        if path.startswith("/") or ".." in path.split("/"):
            return None
        paths.append(path)
    return paths if blocking_seen else None


def _step_review(state: RunState, ops: Ops) -> bool:
    """S6b: review-pr.sh; started → перезапустить (дедуп по fp — дёшево)."""
    key = "review"
    _ensure_started(state, key)
    if op_status(state, key) == "completed":
        return True
    exit_code = ops.review(state.repo, state.pr)
    if exit_code == 0:
        op_complete(state, key, exit=exit_code)
        return True
    if exit_code == 1:
        # Evidence-подсказка (B2 follow-up приёмки B1, спека §7): известный
        # ложный класс находок «файлов нет» опровергается прямой проверкой
        # `git cat-file -e <head>:<путь>` — до машинного типа находки в ките
        # steward перегон такого false positive не автоматизирован, но
        # подсказка сокращает ручной цикл проверки. Голова берётся живьём
        # (`ops.head_sha`), а не из `state.head` — то поле заполняется только
        # на S7 (`_step_merge`), на S6 оно ещё пусто.
        #
        # `head_sha` — единственный git-вызов на СТОП-пути (финальное ревью
        # I-6): `RealOps.head_sha` зовёт `git rev-parse` с `check=True`, и
        # если ветки нет локально/`target_dir` уехал, штатная остановка
        # «ревью нашло находки» превращалась в необработанный
        # `CalledProcessError` — комментарий не постился, `state.status`
        # оставался `running` на диске, и `resume()` заходил с неверным
        # состоянием. Подсказка чисто косметическая и не стоит того, чтобы
        # ронять стоп — сбой глотается, литерал `<head>` вместо реальной sha.
        try:
            head = ops.head_sha(state.target_dir, state.branch)
        except Exception:  # noqa: BLE001 — косметика не должна ронять стоп
            head = "<head>"
        # Авто-опровержение ложного класса «файлов нет» (спека §7; машинный
        # тип kind: file-missing доставлен steward#141/#142). Ровно ОДНА
        # попытка на ревью-цикл (op review-refute): все блокирующие находки
        # file-missing И каждый названный файл существует на head → комментарий
        # с evidence + пере-прогон --fresh (обычный прогон унаследовал бы тот
        # же красный вердикт по fp). Смешанный вердикт — человеку целиком.
        if op_status(state, "review-refute") == "new" and head != "<head>":
            body = ops.latest_review_body(state.repo_slug, state.pr)
            candidates = (
                _file_missing_refute_candidates(body) if body else None
            )
            if candidates and all(
                ops.file_exists_at(state.target_dir, head, p)
                for p in candidates
            ):
                op_start(state, "review-refute")
                proofs = "\n".join(
                    f"- `git cat-file -e {head}:{p}` — файл существует"
                    for p in candidates
                )
                ops.comment(
                    state.repo_slug, state.pr,
                    "Авто-опровержение находок класса `file-missing` "
                    f"(спека §7): все блокирующие находки заявляют "
                    "отсутствие файлов, опровергнутое по дереву head:\n"
                    f"{proofs}\n\nПере-прогон ревью с --fresh.",
                )
                op_complete(state, "review-refute", files=candidates)
                fresh_exit = ops.review_fresh(state.repo, state.pr)
                if fresh_exit == 0:
                    op_complete(state, key, exit=fresh_exit)
                    return True
                # Коды fresh-прогона маршрутизируются как у первичного
                # (приёмка PR #102, minor): 2/3 — отказ прибора, не
                # «сохранившиеся находки»; 4 — голова уехала, тот же
                # reset-путь, что и внизу функции.
                if fresh_exit == REVIEW_BARRIER_EXIT:
                    # Барьер, а не сбой прибора (devtools#258). Op'ы не
                    # трогаем: содержимое ветки ни при чём.
                    _stop_with_comment(
                        state, ops, "stopped_review",
                        REVIEW_BARRIER_STOP,
                    )
                    return False
                if fresh_exit in (2, 3):
                    _stop_with_comment(
                        state, ops, "stopped_review", "прибор не отработал"
                    )
                    return False
                if fresh_exit != 1:
                    state.ops.pop("gate-candidate", None)
                    state.ops.pop("push", None)
                    state.ops.pop("ready", None)
                    state.ops.pop(key, None)
                    # Новая голова = новый ревью-цикл: попытка
                    # авто-опровержения возвращается (приёмка PR #102, круг 2).
                    state.ops.pop("review-refute", None)
                    save(state)
                    return False
        _stop_with_comment(
            state, ops, "stopped_review",
            "ревью нашло находки, прогон остановлен\n\n"
            "Известный ложный класс находок «файлов нет» опровергается "
            f"прямой проверкой `git cat-file -e {head}:<путь>`.",
        )
        return False
    if exit_code == REVIEW_BARRIER_EXIT:
        # Барьер бюджета/stop rule (devtools#258): прогон возможен, но
        # требует решения владельца. Ветка обязана стоять ДО `in (2, 3)`:
        # под кодом 2 в PR уходила ложная причина («прибор не отработал»).
        # И до reset-ветки ниже — та оставляет статус `running`, то есть
        # стоп не персистентен и шаг переигрывается на следующем заходе.
        #
        # Чего эта ветка НЕ даёт (находка ревью devtools#275): сохранения
        # контентного гейта. Op'ы здесь действительно не трогаются, но
        # единственный путь продолжения из `stopped_review` — `resume()`, а
        # он безусловно попает весь `_BUNDLE_EDIT_RESET_OPS`, включая
        # `gate-candidate`/`push`/`ready`. Это осознанно: между стопом и
        # resume бандл мог быть отредактирован человеком, и сброс
        # fail-safe. Довести сохранение до конца — devtools#276.
        _stop_with_comment(
            state, ops, "stopped_review", REVIEW_BARRIER_STOP
        )
        return False
    if exit_code in (2, 3):
        _stop_with_comment(state, ops, "stopped_review", "прибор не отработал")
        return False
    # exit_code == 4 (или иной неопознанный) — голова PR уехала: в ветку
    # пришло новое содержимое, поэтому контентный гейт S4 (отработавший по
    # СТАРОЙ голове) обязан переиграться, не только весь S6 (финальное
    # ревью F-7) — иначе прошедший когда-то gate-candidate молча продолжает
    # покрывать содержимое, которого он не видел. push сбрасывается вместе с
    # gate-candidate: новый коммит после правок ещё не запушен.
    state.ops.pop("gate-candidate", None)
    state.ops.pop("push", None)
    state.ops.pop("ready", None)
    state.ops.pop(key, None)
    state.ops.pop("review-refute", None)
    save(state)
    return False


def _step_verdict(state: RunState, ops: Ops) -> bool:
    """S7: merge_gate → agent продолжает мержем, human/refuse — стоп.

    ``verdict`` — только аудит-запись, НЕ кэш решения (финальное ревью F-2):
    пока op ``merge`` не ``completed``, факты PR пересобираются и `decide()`
    вызывается заново на КАЖДОМ заходе в этот шаг. Достижимо штатным
    write-ahead-сценарием: `_step_verdict` зафиксировал ``agent``,
    `_step_merge` начал op ``merge`` (``started``) и процесс умер раньше
    самого мержа — статус run'а остаётся ``running``, и без пересверки
    следующий `advance()` домержил бы по кэшированному вердикту, хотя за
    время простоя на том же sha мог покраснеть rollup, открыться review
    thread или PR — отстать от base (спека §8: «resume без reconciliation
    запрещён» — единственный неотменяемый шаг). Мерж стартует только на
    свежем ``agent``.
    """
    if op_status(state, "merge") == "completed":
        return True

    _ensure_started(state, "verdict")
    authority = build_authority(Path(state.target_dir), state.merge_authority)
    safety = load_safety()
    review_exit = state.ops.get("review", {}).get("exit")
    pr_facts_raw = ops.pr_facts(state.repo_slug, state.pr)
    # S8 гейтит default-ветку целевого репо, не feature-ветку прогона — ей
    # нужно имя (круг 5); фолбэк "master" на пустой/отсутствующий baseRefName.
    state.base_ref = pr_facts_raw.get("baseRefName") or "master"
    files = ops.pr_files(state.repo_slug, state.pr)
    threads = ops.unresolved_threads(state.repo_slug, state.pr)
    facts = facts_from(pr_facts_raw, files, threads, state.bundle_dir)
    verdict = decide(authority, safety, review_exit, facts)
    decision, reason = verdict.decision, verdict.reason
    op_complete(state, "verdict", decision=decision, reason=reason)

    if decision == "agent":
        return _step_merge(state, ops)

    # refuse получает свой статус, отдельный от stopped_gate (S4): причины и
    # починки разные, и resume() должен различать их (финальное ревью M-1).
    status = (
        "stopped_merge_refused" if decision == "refuse" else "waiting_human_merge"
    )
    _stop_with_comment(state, ops, status, f"merge_gate: {decision} — {reason}")
    return False


def _step_merge(state: RunState, ops: Ops) -> bool:
    """S7 (продолжение): merge; started → pr_facts.state==MERGED → complete."""
    key = "merge"
    status = op_status(state, key)
    if status == "completed":
        return True
    if status == "new":
        state.head = ops.head_sha(state.target_dir, state.branch)
        op_start(state, key)
    else:
        pr_facts_now = ops.pr_facts(state.repo_slug, state.pr)
        if pr_facts_now.get("state") == "MERGED":
            op_complete(state, key, merged=True)
            return True
    # Имя КАТАЛОГА репо, не slug: мерж идёт через `merge-pr.sh` (единственный
    # путь агентского мержа), а обвязка адресуется каталогом во флоте и
    # выводит slug из его сырого origin — так же, как `ops.review` выше.
    merged = ops.merge(state.repo, state.pr, state.head) == 0
    if merged:
        op_complete(state, key, merged=True)
        return True
    _stop_with_comment(
        state, ops, "waiting_human_merge", "мерж не удался, ждёт человека"
    )
    return False


def _s8_findings_text(exit_code: int | None, output: str) -> str:
    """Текст `s8-findings.txt`, собранный из журнала `gate-authoritative`
    (`exit`/`output`) — файл производный, журнал источник истины (круг 8)."""
    return f"gate-check (S8, authoritative) завершился с кодом {exit_code}\n\n{output}"


def _step_s8(state: RunState, ops: Ops) -> bool:
    """S8: authoritative-гейт на дефолтной ветке после мержа (спека §5).

    exit 0 → `completed`, run завершается. Не-0 → `merged_unverified`
    НАВСЕГДА: findings в `run_dir/s8-findings.txt`, remediation-issue в
    целевом репо по inbox-контракту ADR-ECO-006 (тело начинается
    `slug:`/`from:`).

    `gate-authoritative` завершается (`op_complete`, аудит-запись с `exit`)
    сразу после `gate_check_s8` — успех И провал одинаково (круг 3,
    codex-ревью PR #88): run на любом исходе S8 становится терминальным
    (либо `completed`, либо `merged_unverified` навсегда — `advance()`/
    `resume()` отказывают на `merged_unverified` явно), так что
    `completed` здесь не путается с «прошёл» — прошёл или нет, отличает
    сохранённый `exit`, не сам статус op'а.

    Создание remediation-issue — отдельный write-ahead op
    `remediation-issue`: `create_issue` раньше не имел собственного op'а,
    и гибель между вызовом `create_issue` (эффект состоялся) и фиксацией
    результата на resume приводила к дубль-issue (`op_start` пишется ДО
    эффекта). Реконсиляция через `ops.find_issue` по `slug:`-префиксу
    выполняется БЕЗУСЛОВНО перед `create_issue` (codex-ревью, round 3), не
    только когда у ЭТОГО прогона op уже `started`.

    Slug строится от ЦИКЛА (`beh-remediation-<cycle_id>`,
    `cycle_id = state.remediated_by or state.run_id`), не от `ws_id`
    (codex-major, round 4): раньше общий `ws_id`-slug означал, что НЕЗАВИСИМЫЙ
    провал того же `ws_id` (новый родитель, новый цикл — например ПОСЛЕ того,
    как предыдущий цикл был зелёно верифицирован и его issue закрыт)
    реконсилировался на СТАРЫЙ issue по общему `ws_id` — свежие findings
    молча терялись под чужим (обычно уже закрытым) issue. `cycle_id` — это
    сам `merged_unverified`-родитель: у родителя `remediated_by` ещё `None`
    (первый провал цикла) → `cycle_id = run_id` собственный; у его
    verify-потомков `remediated_by` указывает на того же родителя →
    `cycle_id` совпадает, и родитель с ЛЮБЫМ числом потомков делят ОДИН
    issue. `run_id` уже несёт `ws_id`-префикс (`<ws_id>-...`) — уникальность
    и читаемость slug'а сохраняются без явного `ws_id` в нём.

    Перед самим `gate_check_s8` — `sync-default` (круг 5, codex-ревью
    PR #88): `target_dir` без явного чекаута мог стоять на feature-ветке
    прогона, и authoritative-срез гейтил бы не default-ветку. Выполняется
    БЕЗУСЛОВНО на каждом заходе в этот шаг, пока `gate-authoritative` не
    `completed` — не только когда сам `sync-default` ещё не `completed`
    (круг 6, codex-ревью PR #88): между попытками мог пройти произвольный
    отрезок времени, за который default-ветка целевого репо могла уехать
    дальше, а локальный чекаут — устареть; `checkout_and_pull` дёшев и
    идемпотентен, так что журнал `sync-default` держится только для аудита,
    не как ветка пропуска.

    `s8-findings.txt` — ПРОИЗВОДНЫЙ от журнала, не источник истины (круг 8,
    codex-ревью PR #88): op `gate-authoritative` несёт `output` в результате
    (`op_complete(..., exit=N, output=...)`), и findings-текст всегда
    пересобирается из этих полей и перезаписывается на диск — и на свежем
    провале, и на resume. Раньше файл писался ОДИН раз, после
    `op_complete`; гибель между этими двумя шагами оставляла op `completed`
    на диске без файла, и resume падал на `read_text()`
    (`FileNotFoundError`) вместо того, чтобы довести fail-путь до конца.
    """
    key = "gate-authoritative"
    findings_path = run_dir(state.run_id) / "s8-findings.txt"
    gate_op = state.ops.get(key)

    if gate_op is not None and gate_op["status"] == "completed":
        if gate_op.get("exit") == 0:
            # Резюме между write-ahead `op_complete(gate-authoritative)` и
            # финальным `state.status = "completed"` (круг 6, codex-ревью
            # PR #88): op на диске уже `completed`, но статус ещё "running" —
            # довести до конца, не оставлять прогон вечно "running".
            if state.status != "completed":
                state.status = "completed"
                save(state)
            return True
        findings = _s8_findings_text(gate_op.get("exit"), gate_op.get("output", ""))
        findings_path.write_text(findings, encoding="utf-8")
    else:
        sync_key = "sync-default"
        _ensure_started(state, sync_key)
        base_ref = state.base_ref or "master"
        try:
            ops.checkout_and_pull(state.target_dir, base_ref)
        except RuntimeError as exc:
            print(f"_step_s8: checkout_and_pull({base_ref!r}) не удался: {exc}")
            return False
        op_complete(state, sync_key)
        _ensure_started(state, key)
        # Прибрать verdict-файл ПРЕДЫДУЩЕЙ попытки до запуска гейта
        # (приёмка PR #114, круг 2): иначе harvested=True после гейта мог
        # бы означать «нашёлся старый файл», маскируя отсутствие артефакта
        # текущего вызова. Старый файл сохраняется в run_dir как *.stale.
        ops.collect_gate_verdicts(
            state.target_dir,
            str(run_dir(state.run_id) / "s8-gate-verdicts.stale.jsonl"),
        )
        exit_code, output = ops.gate_check_s8(
            state.target_dir, state.bundle_dir, state.profile
        )
        # Ретроспектива 2026-09-02 (@id:runner-s8-verdicts-cleanup):
        # --emit-verdicts оставляет .steward/gate_verdicts.jsonl в корне
        # целевого репо — грязный чекаут спотыкает dirty-гард task_bridge
        # на следующем шаге конвейера. Evidence переезжает в run_dir —
        # и на успехе, и на провале, до ветвления по exit_code. После
        # pre-clean выше True доказуемо означает «файл создан ЭТИМ
        # вызовом гейта».
        harvested = ops.collect_gate_verdicts(
            state.target_dir,
            str(run_dir(state.run_id) / "s8-gate-verdicts.jsonl"),
        )
        if exit_code == 0:
            # Verdicts — обязательный артефакт authoritative-фиксации
            # (спека §5; приёмка PR #114): зелёный exit без файла — не
            # успех, а неполный результат гейта. Fail-closed стоп до
            # op_complete — шаг остаётся resumable для разбирательства.
            if not harvested:
                print(
                    "_step_s8: gate-check вернул 0, но "
                    ".steward/gate_verdicts.jsonl не создан — стоп "
                    "(verdicts — обязательный артефакт S8)"
                )
                return False
            op_complete(state, key, exit=exit_code)
            state.status = "completed"
            save(state)
            return True
        op_complete(state, key, exit=exit_code, output=output)
        findings = _s8_findings_text(exit_code, output)
        findings_path.write_text(findings, encoding="utf-8")

    # cycle_id — идентичность remediation-цикла (round 4): сам
    # merged_unverified-родитель, не ws_id (см. докстринг выше).
    cycle_id = state.remediated_by or state.run_id
    issue_title = f"beh-remediation: {state.subject} ({state.ws_id})"
    body_prefix = f"slug: beh-remediation-{cycle_id}"
    issue_body = f"{body_prefix}\nfrom: devtools#{state.run_id}\n\n{findings}"

    issue_key = "remediation-issue"
    issue_status = op_status(state, issue_key)
    if issue_status != "completed":
        if issue_status != "started":
            op_start(state, issue_key)
        try:
            existing = ops.find_issue(state.repo_slug, body_prefix)
        except RuntimeError as exc:
            print(f"_step_s8: реконсиляция find_issue не удалась: {exc}")
            return False
        if existing is not None:
            op_complete(state, issue_key, number=existing)
        else:
            number = ops.create_issue(state.repo_slug, issue_title, issue_body)
            op_complete(state, issue_key, number=number)

    # Безусловный фолл-through сюда с обеих веток выше (свежий провал и
    # резюме на уже-completed(exit!=0) gate-authoritative) — второе окно
    # круга 6: гибель между `op_complete(gate-authoritative)` и этой строкой
    # оставляла бы run вечно "running", если бы тут был досрочный return;
    # его нет, так что fail-путь всегда доводится до конца независимо от
    # того, каким статус пришёл на вход функции.
    state.status = "merged_unverified"
    save(state)
    return False


def _print_status(state: RunState) -> None:
    """Человекочитаемый дамп `RunState` для `start`/`resume`/`verify`/`status`."""
    print(f"run_id:        {state.run_id}")
    print(f"status:        {state.status}")
    print(f"subject:       {state.subject}")
    print(f"repo:          {state.repo} ({state.repo_slug})")
    print(f"ws_id:         {state.ws_id}")
    print(f"branch:        {state.branch or '-'}")
    print(f"pr:            {state.pr if state.pr is not None else '-'}")
    if state.remediated_by:
        print(f"remediated_by: {state.remediated_by}")
    print("ops:")
    for key in sorted(state.ops):
        op = state.ops[key]
        details = ", ".join(f"{k}={v}" for k, v in op.items() if k != "status")
        suffix = f" ({details})" if details else ""
        print(f"  {key}: {op['status']}{suffix}")


def main(argv: list[str] | None = None) -> int:
    """CLI: `python -m governance.runner start|resume|verify|status ...`.

    Все команды, кроме `status` (только читает `run.json`), строят `RealOps` —
    единственную точку внешних эффектов (git/gh/codex/gate-check, спека §5/§8).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    start_p = sub.add_parser("start", help="новый прогон, сразу advance() (S0..)")
    start_p.add_argument("--subject", required=True)
    start_p.add_argument("--repo", required=True)
    start_p.add_argument("--repo-slug", required=True)
    start_p.add_argument("--ws-id", required=True)
    start_p.add_argument("--target-dir", required=True)
    start_p.add_argument(
        "--bundle-dir", default=None, help="дефолт workstreams/<ws-id>/spec"
    )
    start_p.add_argument("--profile", default="profiles/team-exp.yaml")
    start_p.add_argument("--merge-authority", default=None, choices=["human"])
    start_p.add_argument(
        "--author-backend", default="codex", choices=["codex", "disp"],
    )
    # Обе формы заведены с самого начала (BooleanOptionalAction) — и
    # именно поэтому флип дефолта срезом 3 не осиротил ни одной уже
    # записанной команды: переименования не потребовалось.
    start_p.add_argument(
        "--allow-legacy-dt",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="принимать decomposition без dt_contract_version (по "
        "умолчанию выключено: авторинг выпускает версию 2, и её "
        "отсутствие значит чужой или старый бандл)",
    )
    start_p.add_argument(
        "--run-id", default=None, help="дефолт <ws-id>-<3 случайных байта hex>"
    )

    resume_p = sub.add_parser("resume", help="подхватить сохранённый прогон")
    resume_p.add_argument("--run-id", required=True)

    verify_p = sub.add_parser(
        "verify", help="verification-run для merged_unverified родителя"
    )
    verify_p.add_argument("--parent", required=True, help="run_id родителя")
    verify_p.add_argument(
        "--run-id", default=None,
        help="run_id потомка; по умолчанию детерминированный "
        "<parent>-v<N> (round 5) — сериализует конкурентные verify",
    )

    status_p = sub.add_parser("status", help="человекочитаемый дамп run.json")
    status_p.add_argument("--run-id", required=True)

    args = parser.parse_args(argv)

    if args.command == "status":
        _print_status(load(args.run_id))
        return 0

    ops = RealOps()
    if args.command == "start":
        bundle_dir = args.bundle_dir or f"workstreams/{args.ws_id}/spec"
        # Дефолтный run_id строится из ws_id — валидируем ws_id ДО генерации
        # (круг 12): битый ws_id иначе протащил бы `../`/`/` дальше в
        # автосгенерированный run_id (там его тоже поймает `run_dir()`, но
        # с менее внятным сообщением об ошибке).
        if args.run_id is None:
            validate_id_component(args.ws_id, label="ws_id")
        run_id = args.run_id or f"{args.ws_id}-{os.urandom(3).hex()}"
        state = start(
            subject=args.subject,
            repo=args.repo,
            repo_slug=args.repo_slug,
            ws_id=args.ws_id,
            target_dir=args.target_dir,
            bundle_dir=bundle_dir,
            profile=args.profile,
            run_id=run_id,
            ops=ops,
            merge_authority=args.merge_authority,
            author_backend=args.author_backend,
            allow_legacy_dt=args.allow_legacy_dt,
        )
    elif args.command == "resume":
        state = resume(args.run_id, ops)
    else:
        state = verify(args.parent, ops, args.run_id)

    _print_status(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
