"""Оркестратор шагов личного пути `dfl` (спека §4.4, §8).

`advance` ведёт прогон по `state.STEPS`, пока может: интервью → бриф →
дизайн и декомпозиция (автор, edge-check, разбор замечаний) → spec-PR
(мержит агент через `human_merge`, как `auto_tasks` в `test`) → прогон
spec-runner → integration-PR, который мержит человек. После каждого шага —
`save(run)`; повтор `dfl run` продолжает с `run.step`. Стоп — причина в
`run.stop_reason` и на экране, код 1.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from governance import df_config, run_progress, run_resume
from governance import manual_checks as mc
from governance.author_launch import AuthorCall, config_mode, launch_author
from governance.auto_tasks import RUN_BRANCH_PREFIX, TUI_HINT, notice
from governance.edge_check import override
from governance.edge_check.check import run_check
from governance.facts import Outcome as FactOutcome
from governance.ops import Ops
from lite import edge_journal as ej
from lite import manual_ask as ma
from lite import node_guard, pending
from lite import owner_review as orv
from lite import review_pause as rp
from lite.edge_resolve import MAX_ROUNDS, Outcome, PaidCalls, Scope, resolve_scope
from lite.interview_chat import Read
from lite.output import say
from lite.personal.brief import Brief, brief_from_json, render_brief
from lite.personal.check import WAVES, NodeCheck, check_node, decomposition_problems
from lite.personal.interviewer import Halt, append_correction, interview
from lite.personal.nodes import FILENAMES, Launch, author_node, revise_node
from lite.personal.state import PersonalRun, run_dir, save
from lite.personal.tasks import TASKS_PATH, render_personal_tasks
from lite.structure import Agent, FormError

BRIEF_JSON = "brief.json"
APPROVALS = "approvals.jsonl"
BRIEF_APPROVAL = {"node": "brief", "how": "подтверждено в разговоре"}
RETRY = "продолжить: dfl run"
INTERVIEW_PAUSED = f"интервью прервано — {RETRY}"
#: хвост стопов, после которых прогон может быть проще начать заново
ABANDON = "бросить прогон и начать заново — dfl abandon"
_RECHECK = (Outcome.REVISED, Outcome.OVERRIDDEN)
_GIT_ERRORS = (RuntimeError, subprocess.CalledProcessError, OSError)
#: шаги до spec-PR: впереди платные интервью и авторинг — дерево и ветка
#: проверяются до них (`_guard` без git-эффектов)
_BEFORE_PR = ("interview", "design", "decomposition")
FALLBACK_BASE = "master"
#: пауза на просмотр (#768): этап → подпись для человека и вид узла
STAGE_LABELS = rp.STAGE_LABELS
_STAGE_KINDS = {"design": "personal-design", "decomposition": "personal-decomposition"}
#: хэши узлов, прошедших проверку перед паузой: повтор `dfl run` на паузе не
#: платит за проверку заново, если файл не меняли
REVIEW_PASSED = "review-passed.json"
REVIEW_PAUSED = "просмотр этапа «{label}» прерван — " + RETRY
UNCLEAR_REMARK = orv.UNCLEAR_REMARK
NO_TTY = orv.NO_TTY
BRIEF_EDIT_RESTORED = (
    "бриф правится словами: опишите правку как замечание — файл возвращён "
    "к согласованной версии"
)


def _stdin_is_tty() -> bool:
    """stdin — терминал (как `lite.dfl._is_tty`): умолчание `advance(tty=…)`,
    связано при определении — тесты передают свой `tty`, а не подменяют её."""
    return sys.stdin.isatty()


@dataclass
class _Ctx:
    """Всё, что нужно шагам одного `advance`."""

    run: PersonalRun
    ops: Ops
    read: Read
    agent: Agent
    launch: Launch
    check: Callable
    calls: PaidCalls = field(default_factory=PaidCalls)
    stopped: bool = False
    #: база прогона: `run.base_ref` или (не установлена) `FALLBACK_BASE`
    base: str = FALLBACK_BASE
    #: есть терминал для паузы на просмотр (спрашивается только на паузе)
    tty: Callable[[], bool] = _stdin_is_tty

    def stop(self, reason: str) -> bool:
        """Записать и показать причину стопа; `False` — шаг встал."""
        self.run.stop_reason = reason
        self.stopped = True
        say(reason)
        return False

    @property
    def bundle(self) -> Path:
        return Path(self.run.target_dir) / self.run.bundle_dir


def advance(
    run: PersonalRun,
    ops: Ops,
    *,
    read: Read,
    agent: Agent,
    launch: Launch = launch_author,
    check: Callable = run_check,
    tty: Callable[[], bool] = _stdin_is_tty,
) -> int:
    """Выполнить шаги прогона, пока можно.

    0 — дошёл до `code-pr`/`done` или ждёт человека; 1 — стоп
    (`run.stop_reason` записан и выведен). После каждого шага — `save(run)`.
    `tty` — есть ли терминал для паузы на просмотр этапа (#768).
    """
    ctx = _Ctx(run, ops, read, agent, launch, check, tty=tty)
    run.stop_reason = ""
    save(run)
    ctx.base = _base(run, ops)
    if run.step in _BEFORE_PR and _guard(ctx) is None:
        save(run)
        return 1
    while run.step != "done":
        moved = STEP_HANDLERS[run.step](ctx)
        save(run)
        if not moved:
            break
    if ctx.calls.spent():
        say(ctx.calls.line())
    return 1 if ctx.stopped else 0


def _base(run: PersonalRun, ops: Ops) -> str:
    """База прогона: `run.base_ref`, а пустая — ветка по умолчанию цели.

    Как `governance.runner._resolve_base_ref` (#729): установленный факт
    форджи записывается в прогон до платных шагов; не установлен —
    `FALLBACK_BASE` вслух и БЕЗ записи (следующий `dfl run` переспросит).
    """
    if run.base_ref:
        return run.base_ref
    fact = ops.default_branch_fact(run.repo_slug)
    if not fact.established or fact.value is None:
        say(
            f"ветка по умолчанию {run.repo_slug} не установлена ({fact.detail}) — "
            f"база {FALLBACK_BASE} (не закрепляется)"
        )
        return FALLBACK_BASE
    run.base_ref = fact.value.name
    save(run)
    return run.base_ref


# --- interview ---------------------------------------------------------------


def _step_interview(ctx: _Ctx) -> bool:
    """Интервью → `brief.json` (R8-a), `00-brief.md`, запись одобрения;
    с паузой на `brief` — затем просмотр (подтверждённый бриф не переспрашивается)."""
    run = ctx.run
    rd = run_dir(run.run_id)
    rd.mkdir(parents=True, exist_ok=True)
    paused = "brief" in run.review_pause
    if not (paused and _brief_written(ctx, rd)) and not _interview(ctx, rd):
        return False
    if paused and not _review(ctx, "brief", [FILENAMES["brief"]]):
        return False
    run.step = "design"
    return True


def _interview(ctx: _Ctx, rd: Path) -> bool:
    """Интервью до подтверждённого брифа и его запись; `False` — пауза/стоп."""
    brief = interview(rd, ctx.run.subject, read=ctx.read, agent=ctx.agent)
    if brief is Halt.PAUSED:  # человек прервал — пауза, не сбой (код 0)
        ctx.run.stop_reason = INTERVIEW_PAUSED
        say(INTERVIEW_PAUSED)
        return False
    if isinstance(brief, Halt):
        return ctx.stop(f"интервью не завершено — {RETRY}")
    _write_brief(ctx, rd, brief)
    return True


def _write_brief(ctx: _Ctx, rd: Path, brief: Brief) -> None:
    (rd / BRIEF_JSON).write_text(
        json.dumps(asdict(brief), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    ctx.bundle.mkdir(parents=True, exist_ok=True)
    (ctx.bundle / FILENAMES["brief"]).write_text(render_brief(brief), encoding="utf-8")
    _approve_brief(rd / APPROVALS)


def _brief_written(ctx: _Ctx, rd: Path) -> bool:
    """Бриф уже подтверждён и записан (возобновление паузы на `brief`)."""
    return (rd / BRIEF_JSON).is_file() and (ctx.bundle / FILENAMES["brief"]).is_file()


def _approve_brief(path: Path) -> None:
    """Запись одобрения брифа; та же последняя строка уже есть — не дублировать
    (повтор шага после падения до `save`)."""
    line = json.dumps(BRIEF_APPROVAL, ensure_ascii=False)
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()
        if lines and lines[-1] == line:
            return
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


# --- design / decomposition --------------------------------------------------


def _step_design(ctx: _Ctx) -> bool:
    if not (_checked_before(ctx, "design") or _node(ctx, "personal-design")):
        return False
    if "design" in ctx.run.review_pause and not _review(
        ctx, "design", [FILENAMES["personal-design"]]
    ):
        return False
    ctx.run.step = "decomposition"
    return True


def _step_decomposition(ctx: _Ctx) -> bool:
    """Декомпозиция готова (`_decomposition_ready`), с паузой — просмотр."""
    if not (_checked_before(ctx, "decomposition") or _decomposition_ready(ctx)):
        return False
    if "decomposition" in ctx.run.review_pause and not _review(
        ctx, "decomposition", [FILENAMES["personal-decomposition"]]
    ):
        return False
    ctx.run.step = "spec-pr"
    return True


def _decomposition_ready(ctx: _Ctx) -> bool:
    """Узел декомпозиции и её инварианты (граф DT/BEH, покрытие критериев).

    Нарушения правит автор (спека §4.4/§8: человек отвечает только на
    решения): до `MAX_ROUNDS` кругов правки, после каждого — снова ребро
    edge-check и инварианты; стоп, только если нарушения не сошлись.
    """
    kind = "personal-decomposition"
    if not _node(ctx, kind):
        return False
    brief = _load_brief(ctx)
    if brief is None:
        return False
    path = ctx.bundle / FILENAMES[kind]
    for round_no in range(1, MAX_ROUNDS + 2):
        text = path.read_text(encoding="utf-8")
        problems = decomposition_problems(text, brief)
        if not problems:
            return True
        if round_no > MAX_ROUNDS:
            break
        say(f"декомпозиция нарушает инварианты — правка автором, круг {round_no}")
        if not _fix_invariants(ctx, kind, text, problems):
            return False
        if not _node(ctx, kind):
            return False
    return ctx.stop(
        f"декомпозиция нарушает инварианты после {MAX_ROUNDS} кругов правки: "
        + "; ".join(problems)
        + f" — поправьте {FILENAMES[kind]} вручную и повторите dfl run ({ABANDON})"
    )


def _fix_invariants(ctx: _Ctx, kind: str, before: str, problems: list[str]) -> bool:
    """Один круг правки нарушений автором; сбой или ослабление узла — файл
    возвращается, стоп."""
    run, name = ctx.run, FILENAMES[kind]
    path = ctx.bundle / name
    ctx.calls.revise += 1
    try:
        outcome = revise_node(
            _author_call(run, name),
            kind,
            run.subject,
            before,
            "\n".join(f"- инвариант декомпозиции: {p}" for p in problems),
            "нет",
            launch=ctx.launch,
        )
        reason = None
        if outcome.code != 0:
            reason = f"правка {name} не состоялась: {outcome.reason or outcome.code}"
        else:
            after = path.read_text(encoding="utf-8")
            bad = node_guard.weakened(before, after, set(), set())
            if bad:
                reason = (
                    f"правка {name} отклонена — ослабляет узел: {', '.join(bad)}; "
                    "файл возвращён"
                )
    except BaseException:
        path.write_text(before, encoding="utf-8")
        raise
    if reason is None:
        return True
    path.write_text(before, encoding="utf-8")
    return ctx.stop(f"{reason} — {RETRY} ({ABANDON})")


def _author_call(run: PersonalRun, name: str) -> AuthorCall:
    return AuthorCall(
        run_id=run.run_id,
        run_dir=os.path.realpath(run_dir(run.run_id)),
        target_dir=run.target_dir,
        bundle_dir=run.bundle_dir,
        filename=name,
    )


def _load_brief(ctx: _Ctx) -> Brief | None:
    path = run_dir(ctx.run.run_id) / BRIEF_JSON
    try:
        return brief_from_json(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, FormError) as exc:
        ctx.stop(f"бриф прогона не прочитан ({path}): {exc}")
        return None


def _node(ctx: _Ctx, kind: str) -> bool:
    """Узел написан и прошёл своё ребро (с разбором замечаний); иначе стоп."""
    name = FILENAMES[kind]
    if not (ctx.bundle / name).exists():
        ctx.calls.author += 1
        outcome = author_node(ctx.run, kind, launch=ctx.launch)
        if outcome.code != 0:
            return ctx.stop(
                f"автор {name} не написал узел: {outcome.reason or outcome.code}"
                f" — {RETRY}"
            )
    ctx.calls.check += 1  # каждая проверка ребра платная, и первая
    result = check_node(ctx.run, kind, check=ctx.check)
    round_no = 0
    while True:
        if result.failure:  # RF-5: без разбора и без переписывания
            return ctx.stop(f"проверка ребра не состоялась: {result.failure}")
        if _passed(ctx.run, kind, result):
            return True
        if not result.findings:  # разбирать нечего: resolve_scope — NOT_APPLICABLE
            return ctx.stop(_no_findings_reason(name, result))
        foreign = [f for f in result.findings if f.path != name]
        if foreign:  # разбор правит только узел — разбирать нечего
            return ctx.stop(_foreign_findings_reason(name, foreign))
        if round_no == MAX_ROUNDS:
            return ctx.stop(
                f"замечания edge-check к {name} не сошлись за {MAX_ROUNDS} "
                f"круга — поправьте узел и повторите dfl run ({ABANDON})"
            )
        round_no += 1
        outcome = resolve_scope(
            _scope(ctx.run, kind),
            result.findings,
            read=ctx.read,
            agent=ctx.agent,
            revise=functools.partial(revise_node, launch=ctx.launch),
            round_no=round_no,
            calls=ctx.calls,
        )
        if outcome not in _RECHECK:
            return ctx.stop(
                f"разбор замечаний к {name} не завершён ({outcome.value}) — {RETRY}"
            )
        ctx.calls.check += 1
        result = check_node(ctx.run, kind, check=ctx.check)


def _no_findings_reason(name: str, result: NodeCheck) -> str:
    """Стоп по вердикту без блокирующих находок: что не прошло и что делать."""
    unmet = "; ".join(result.unmet) or "критерии в записи не названы"
    return (
        f"edge-check {name}: вердикт {result.verdict} без блокирующих находок — "
        f"не пройдены критерии: {unmet}. Поправьте {name} по этим причинам и "
        f"повторите dfl run (повтор платный; {ABANDON})"
    )


def _foreign_findings_reason(name: str, foreign: list[ej.Finding]) -> str:
    """Стоп по находкам не на узле (на основании, обычно `00-brief.md`):
    разбор переписывает только `name`, поэтому называем файл, замечание и
    честный выход."""
    shown = "; ".join(
        f"{f.rule_id} {f.cls} ({f.path or 'без файла'}:{f.lines}): {f.statement}"
        for f in foreign
    )
    paths = sorted({f.path or "без файла" for f in foreign})
    brief = FILENAMES["brief"]
    nodes = [p for p in paths if p != brief and p in FILENAMES.values()]
    advice = [f"если замечание на деле о {name} — поправьте его вручную"]
    advice += [
        f"{p} можно поправить вручную (его собственное ребро не перепроверяется)"
        for p in nodes
    ]
    if brief in paths:
        advice.append(
            f"{brief} подтверждён в разговоре и меняется только новым интервью"
        )
    return (
        f"edge-check {name}: замечания указывают не на {name}, а на "
        f"{', '.join(paths)} — {shown}. Разбор правит только {name}: "
        f"{'; '.join(advice)}; затем повторите dfl run (повтор платный) — "
        f"{ABANDON}"
    )


def _scope(run: PersonalRun, kind: str) -> Scope:
    """Scope разбора (R8-c): роли оснований — ключи `FILENAMES`."""
    return Scope(
        run_dir=run_dir(run.run_id),
        target_dir=run.target_dir,
        bundle_dir=run.bundle_dir,
        wave=WAVES[kind],
        can_override=True,
        filenames=FILENAMES,
        subject=run.subject,
    )


def _passed(run: PersonalRun, kind: str, result: NodeCheck) -> bool:
    """PASS ребра либо FAIL, все блокирующие находки которого приняты вопреки."""
    if result.verdict == "PASS":
        return True
    if result.verdict != "FAIL" or not result.findings:
        return False
    return _accepted(run, WAVES[kind]) is not None


def _accepted(run: PersonalRun, wave: int) -> set | None:
    rd = run_dir(run.run_id)
    try:
        return override.accepted(ej.level_records(rd, wave), ej.overridden(rd, wave))
    except (OSError, ValueError, KeyError, TypeError):
        return None


# --- review pause (#768) -----------------------------------------------------


def _review(ctx: _Ctx, stage: str, files: list[str]) -> bool:
    """Пауза на просмотр этапа: `True` — «дальше», `False` — стоп или пауза.

    Круг: недоигранные замечания (RF-1: применяются без вопроса) или новые
    с экрана паузы → правка (бриф — интервьюером, узел — автором с гвардом
    удалений) → «применено» → для узла проверка заново → снова пауза.
    «Дальше» после ручной правки файла на экране — тоже проверка заново
    и снова пауза (файл сверяется с хэшем, снятым при показе); бриф правится
    только словами: файл, разошедшийся с `brief.json`, возвращается к его
    рендеру с подсказкой, и пауза снова (дальше читают `brief.json`).
    `/стоп`/EOF — пауза (код 0); без терминала — тоже пауза (код 0)
    с подсказкой-ссылкой: ждёт человека, не сбой. Замечания к дизайну и
    декомпозиции приходят и через `dfl reply` (#766 срез 2); к брифу — только
    в терминале.
    """
    rd = run_dir(ctx.run.run_id)
    paths = [ctx.bundle / name for name in files]
    label = STAGE_LABELS[stage]
    awaiting = _awaiting_reply(rd, stage)
    if awaiting and stage in _STAGE_KINDS and _edited_on_pause(ctx, stage):
        # правка руками, пока пауза ждала `dfl reply`: шаг этапа уже
        # перепроверил узел — ответ был о старом содержимом, пауза заново
        pending.discard(rd)
        awaiting = False
    if not awaiting:
        _remember_checked(ctx, stage)
    round_no = 0
    while True:
        batch = rp.pending(rd, stage)
        if batch and stage == "brief" and not ctx.tty():
            # бриф правится интервьюером — только в терминале (`dfl reply`
            # замечания к брифу не принимает)
            return _stop_wait(ctx, NO_TTY.format(label=label, links=orv.links(paths)))
        if batch:
            round_no += 1
            say(f"замечания к этапу «{label}» — правка, круг {round_no}")
            if not _apply_remarks(ctx, stage, batch):
                return False
            rp.mark_applied(rd, stage, len(batch))
        else:
            answer = _pause_answer(ctx, stage, label, paths)
            if answer is None and not ctx.tty():
                return False  # вопрос записан, `stop_reason` — «ждёт ответа»
            if answer is None:
                ctx.run.stop_reason = REVIEW_PAUSED.format(label=label)
                say(ctx.run.stop_reason)
                return False
            if answer:
                rp.record(rd, stage, answer)
                continue
            if stage == "brief":
                agreed = _load_brief(ctx)
                if agreed is None:
                    return False
                if not _restore_brief(ctx, agreed):
                    return True
                say(BRIEF_EDIT_RESTORED)
                _remember_checked(ctx, stage)
                continue
            if not _edited_on_pause(ctx, stage):
                return True
            # «дальше» после ручной правки на экране — как правленный узел
        if stage in _STAGE_KINDS and not _recheck(ctx, stage):
            return False
        _remember_checked(ctx, stage)


def _awaiting_reply(rd: Path, stage: str) -> bool:
    """Пауза этого узла уже показана без терминала и ждёт `dfl reply`: хэш
    «проверено» остаётся прежним — правка руками между вызовами видна (#766)."""
    rec = pending.load(rd)
    return (
        rec is not None
        and rec.get("kind") == pending.REVIEW
        and rec.get("key") == stage
        and stage in _passed_marks_of(rd)
    )


def _stop_wait(ctx: _Ctx, reason: str) -> bool:
    """Стоп «ждёт человека» (код 0, не сбой): причина записана и показана."""
    ctx.run.stop_reason = reason
    say(reason)
    return False


def _pause_answer(ctx: _Ctx, stage: str, label: str, paths: list[Path]) -> str | None:
    """Ответ на паузе: `""` — «дальше»; текст — замечания; `None` — стоп
    или ввод прерван.

    Без терминала вопрос записывается для `dfl reply` (`stop_reason` — строка
    ожидания); в терминале — `_ask_pause`."""
    if ctx.tty():
        return _ask_pause(ctx, stage, label, paths)
    rd = run_dir(ctx.run.run_id)
    answer = rp.ask_remote(stage, label, paths, ask=pending.asker(rd))
    if answer is None:
        _stop_wait(ctx, pending.wait_line(rp.question(stage, label, paths)))
    return answer


def _ask_pause(ctx: _Ctx, stage: str, label: str, paths: list[Path]) -> str | None:
    """Экран паузы в терминале: ответ, записанный `dfl reply`, иначе живой
    (терминал побеждает: после живого ответа вопрос снят)."""
    rd = run_dir(ctx.run.run_id)
    q = rp.question(stage, label, paths)
    if pending.take(rd, q) is not None:
        return ""
    answer = rp.ask(label, paths, read=ctx.read)
    if answer is not None:
        pending.discard(rd)
    return answer


def _recheck(ctx: _Ctx, stage: str) -> bool:
    """Проверка узла заново после правки (edge-check с разбором #762)."""
    if stage == "decomposition":
        return _decomposition_ready(ctx)
    return _node(ctx, _STAGE_KINDS[stage])


def _apply_remarks(ctx: _Ctx, stage: str, batch: list[rp.Remark]) -> bool:
    if stage == "brief":
        return _brief_by_owner(ctx, batch)
    return _revise_by_owner(ctx, stage, batch)


def _brief_by_owner(ctx: _Ctx, batch: list[rp.Remark]) -> bool:
    """Замечание к брифу — правка интервьюеру («поправить: …» в журнал
    интервью), бриф пересобирается и снова подтверждается «Верно?»."""
    rd = run_dir(ctx.run.run_id)
    append_correction(rd, "\n".join(r.text for r in batch))
    return _interview(ctx, rd)


def _revise_by_owner(ctx: _Ctx, stage: str, batch: list[rp.Remark]) -> bool:
    """Правка узла автором по замечаниям владельца (`owner_review`); не
    принята — файл возвращён: сбой — стоп; `/стоп` на вопросе удаления —
    пауза (замечание остаётся недоигранным); правка всё равно ослабляет узел
    — замечание отклоняется (`mark_dropped`, повторно не переигрывается) и
    стоп с просьбой сформулировать иначе. Без терминала вопрос об удалении
    ждёт `dfl reply` (`WAITING` — код 0); кандидат правки — в каталоге
    прогона, следующий `dfl run` (и в терминале) автора не зовёт."""
    kind = _STAGE_KINDS[stage]
    rd = run_dir(ctx.run.run_id)
    outcome, detail = orv.revise_by_owner(
        ctx.bundle / FILENAMES[kind],
        orv.decisions(batch),
        revise=functools.partial(_owner_revise, ctx, kind),
        read=ctx.read,
        ask=None if ctx.tty() else pending.asker(rd),
        store=rd,
        stage=stage,
    )
    if outcome == orv.ACCEPTED:
        return True
    if outcome == orv.WAITING:
        return _stop_wait(ctx, detail)
    if outcome == orv.PAUSED:
        ctx.run.stop_reason = REVIEW_PAUSED.format(label=STAGE_LABELS[stage])
        say(ctx.run.stop_reason)
        return False
    if outcome == orv.UNCLEAR:
        rp.mark_dropped(run_dir(ctx.run.run_id), stage, len(batch))
        return ctx.stop(UNCLEAR_REMARK.format(ids=detail))
    return ctx.stop(f"{detail} — {RETRY}")


def _owner_revise(ctx: _Ctx, kind: str, before: str, decisions: str) -> str | None:
    """Один вызов автора; сбой — причина, иначе `None`."""
    name = FILENAMES[kind]
    ctx.calls.revise += 1
    outcome = revise_node(
        _author_call(ctx.run, name),
        kind,
        ctx.run.subject,
        before,
        "нет",
        decisions,
        launch=ctx.launch,
    )
    if outcome.code != 0:
        return f"правка {name} не состоялась: {outcome.reason or outcome.code}"
    return None


def _node_digest(ctx: _Ctx, stage: str) -> str:
    kind = _STAGE_KINDS.get(stage)
    path = ctx.bundle / FILENAMES[kind or stage]
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _passed_marks(ctx: _Ctx) -> dict[str, str]:
    return _passed_marks_of(run_dir(ctx.run.run_id))


def _passed_marks_of(rd: Path) -> dict[str, str]:
    path = rd / REVIEW_PASSED
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _remember_checked(ctx: _Ctx, stage: str) -> None:
    """Запомнить хэш узла, прошедшего проверку перед паузой."""
    marks = _passed_marks(ctx)
    marks[stage] = _node_digest(ctx, stage)
    path = run_dir(ctx.run.run_id) / REVIEW_PASSED
    path.write_text(json.dumps(marks, ensure_ascii=False) + "\n", encoding="utf-8")


def _restore_brief(ctx: _Ctx, agreed: Brief) -> bool:
    """`00-brief.md` разошёлся с рендером согласованного `brief.json` —
    переписать рендером; `True` — файл отличался и возвращён."""
    path = ctx.bundle / FILENAMES["brief"]
    text = render_brief(agreed)
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    path.write_text(text, encoding="utf-8")
    return True


def _edited_on_pause(ctx: _Ctx, stage: str) -> bool:
    """Файл этапа изменён с показа паузы (хэш ≠ отметки `_remember_checked`):
    ручная правка владельца — проверяется заново, пауза показывается снова."""
    try:
        return _passed_marks(ctx).get(stage) != _node_digest(ctx, stage)
    except OSError:
        return True


def _checked_before(ctx: _Ctx, stage: str) -> bool:
    """Узел этапа с паузой уже проверен и не менялся (возобновление паузы)."""
    if stage not in ctx.run.review_pause:
        return False
    mark = _passed_marks(ctx).get(stage)
    try:
        return mark is not None and mark == _node_digest(ctx, stage)
    except OSError:
        return False


# --- spec-pr -----------------------------------------------------------------


def _step_spec_pr(ctx: _Ctx) -> bool:
    """Ветка прогона, коммит бандла и `tasks.md`, spec-PR, мерж агентом."""
    if ctx.run.spec_pr is None and not _open_spec_pr(ctx):
        return False
    return _merge_spec_pr(ctx)


def _tasks_rel(run: PersonalRun) -> str:
    return TASKS_PATH.format(ws_id=run.ws_id)


def _spec_branch(run: PersonalRun) -> str:
    return f"spec/{run.ws_id}-personal"


def _guard(ctx: _Ctx) -> str | None:
    """RF-3: чужая грязь или не та ветка — стоп до любого git-эффекта.

    Зовётся и в начале `advance` на шагах до spec-PR — до платных интервью
    и авторинга (сам git-эффектов не делает).

    Узлы бандла и `tasks.md` — свои файлы прогона (написаны в рабочее
    дерево до ветки), грязью не считаются. Ветка прогона допустима
    (возобновление после падения между веткой и PR). Возвращает текущую
    ветку; стоп — `None`.
    """
    run, ops = ctx.run, ctx.ops
    foreign = _foreign(run, ops.dirty_paths(run.target_dir))
    if foreign:
        ctx.stop(
            f"в {run.target_dir} незакоммиченные чужие изменения: {foreign} — "
            f"уберите их и повторите dfl run"
        )
        return None
    branch = ops.current_branch(run.target_dir)
    if branch not in (ctx.base, _spec_branch(run)):
        ctx.stop(
            f"{run.target_dir} на ветке {branch or '(detached)'}, ожидалась "
            f"{ctx.base} (или ветка прогона {_spec_branch(run)}) — "
            "переключитесь и повторите dfl run"
        )
        return None
    return branch


def _foreign(run: PersonalRun, paths: list[str]) -> str:
    """Пути вне своих файлов прогона (бандл и `tasks.md`) строкой; нет — ""."""
    own, tasks = run.bundle_dir.strip("/") + "/", _tasks_rel(run)
    foreign = [p for p in paths if not p.startswith(own) and p != tasks]
    return ", ".join(foreign[:5]) + (" …" if len(foreign) > 5 else "")


def _open_spec_pr(ctx: _Ctx) -> bool:
    run, ops = ctx.run, ctx.ops
    current = _guard(ctx)
    if current is None:
        return False
    try:
        tasks_text = render_personal_tasks(
            run,
            (ctx.bundle / FILENAMES["personal-decomposition"]).read_text("utf-8"),
            (ctx.bundle / FILENAMES["personal-design"]).read_text("utf-8"),
            datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return ctx.stop(f"tasks.md не построен: {exc}")
    target, base = run.target_dir, ctx.base
    branch = _spec_branch(run)
    tasks = _tasks_rel(run)
    try:
        # на ветке прогона — возобновление, без switch
        if current != branch and not _enter_run_branch(ctx, branch):
            return False
        path = Path(target) / tasks
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tasks_text, encoding="utf-8")
        run.spec_committed = True
        save(run)  # write-ahead: ветка может нести коммит прогона
        # пустой индекс (уже закоммичено до падения) — commit_paths не коммитит
        ops.commit_paths(
            target, [run.bundle_dir, tasks], f"spec({run.ws_id}): личный путь"
        )
        # ветка прогона могла получить чужой коммит (возобновление): push
        # и автомерж увезли бы его в базу под видом спеки (ревью #774)
        foreign = _foreign(run, ops.changed_paths(target, base)[1])
        if foreign:
            return ctx.stop(
                f"ветка прогона {branch} несёт чужие изменения относительно "
                f"origin/{base}: {foreign} — это коммит в ветке прогона или "
                f"незапушенный коммит локальной {base}; уберите его из ветки "
                f"прогона (или запушьте {base}) и повторите dfl run ({ABANDON})"
            )
        ops.push_branch(target, branch)
        run.spec_head = ops.head_sha(target, branch)
        save(run)  # write-ahead: мерж пинуется на запушенную голову
        pr = ops.find_pr(run.repo_slug, branch) or ops.create_pr(
            target,
            run.repo_slug,
            branch,
            f"spec({run.ws_id}): {run.subject[:60]}",
            _pr_body(run),
            label="",
            base=base,
        )
    except _GIT_ERRORS as exc:
        return ctx.stop(f"spec-PR не создан: {exc} — {RETRY}")
    run.spec_pr = pr
    save(run)  # write-ahead: повтор не создаёт второй PR
    return True


def _enter_run_branch(ctx: _Ctx, branch: str) -> bool:
    """С базы — на ветку прогона; `False` — стоп до любого git-эффекта.

    Ветки нет — создать от свежей базы. Ветка есть — перейти на неё КАК ЕСТЬ
    (`ensure_branch`), не пересоздавать от базы: `switch_to` (`-C`) сбросил
    бы уже сделанный коммит бандла (возобновление после упавшего push, когда
    человек переключился на базу). Ветка, в которую прогон не коммитил, но
    ушедшая от базы, — чужая история: стоп. Ошибки git летят вызывающему.
    """
    run, ops = ctx.run, ctx.ops
    target, base = run.target_dir, ctx.base
    fact = ops.local_branch_head_fact(target, branch)
    if fact.outcome is FactOutcome.ABSENT:
        if run.spec_committed:
            return ctx.stop(
                f"ветка прогона {branch} с коммитом бандла пропала — верните её "
                f"(git reflog) и повторите dfl run ({ABANDON})"
            )
        ops.checkout_and_pull(target, base)
        ops.switch_to(target, branch, base)
        return True
    if fact.outcome is not FactOutcome.FOUND or not fact.value:
        return ctx.stop(f"ветка прогона {branch} не прочитана: {fact.detail} — {RETRY}")
    if not run.spec_committed and ops.is_ancestor(target, fact.value, base) is not True:
        return ctx.stop(
            f"ветка прогона {branch} уже есть и несёт коммиты, которых прогон не "
            f"делал (или это не установлено), — разберите её вручную (удалите или "
            f"переименуйте) и повторите dfl run ({ABANDON})"
        )
    ops.ensure_branch(target, branch)
    return True


def _pr_body(run: PersonalRun) -> str:
    lines = [
        (f"Личный путь `dfl`: бриф, дизайн, декомпозиция и задачи — «{run.subject}»."),
        "",
        (
            "Бриф одобрен владельцем в разговоре; дизайн и декомпозиция прошли "
            "edge-check своих рёбер."
        ),
    ]
    accepted = sorted(
        f"{node}/{edge} {rule}"
        for wave in WAVES.values()
        for node, edge, rule in (_accepted(run, wave) or set())
    )
    if accepted:
        lines += ["", "Принято вопреки замечаниям edge-check (журнал прогона):"]
        lines += [f"- {key}" for key in accepted]
    remarks = rp.pr_section(run_dir(run.run_id))
    if remarks:
        lines += ["", remarks.rstrip("\n")]
    return "\n".join(lines) + "\n"


def _merge_spec_pr(ctx: _Ctx) -> bool:
    """Мерж spec-PR как `auto_tasks._merge_spec_pr` в `test`; затем pull."""
    run, ops = ctx.run, ctx.ops
    pr = run.spec_pr
    assert pr is not None
    facts = _facts(ctx, pr)
    if facts is None:
        return False
    pr_state = facts.get("state")
    if pr_state == "CLOSED":
        return ctx.stop(f"spec-PR #{pr} закрыт без мержа — решите вручную ({ABANDON})")
    if pr_state != "MERGED":
        refusal = _merge_refusal(run, str(facts.get("headRefOid") or ""))
        if refusal is not None:
            return ctx.stop(f"spec-PR #{pr} не вмержен — {refusal}")
        rc = ops.human_merge(run.repo, pr, run.spec_head, target_dir=run.target_dir)
        if rc != 0:
            return ctx.stop(
                f"spec-PR #{pr} не вмержен — human-merge.sh вернул {rc}: "
                "проверки PR ещё идут или не прошли — повторите dfl run позже"
            )
        say(f"spec-PR #{pr} вмержен")
    try:
        ops.checkout_and_pull(run.target_dir, ctx.base)
    except _GIT_ERRORS as exc:
        return ctx.stop(f"checkout/pull {ctx.base} не удался: {exc} — {RETRY}")
    run.step = "code-run"
    return True


def _merge_refusal(run: PersonalRun, head: str) -> str | None:
    """Причина не мержить spec-PR агентом; можно — `None`.

    Мерж от имени человека — только в режиме `test` конфига DarkFactory и
    только по голове, которую запушил сам прогон (`run.spec_head`).
    """
    try:
        mode = config_mode()
    except df_config.ConfigError as exc:
        return f"конфиг DarkFactory не прочитан: {exc}"
    if mode != "test":
        return f"мерж агентом только в режиме test, конфиг: {mode} — смержите сами"
    if not run.spec_head:
        return f"запушенная голова не записана — {RETRY}"
    if head != run.spec_head:
        return (
            f"голова PR {head[:8] or '(нет)'} ≠ запушенной прогоном "
            f"{run.spec_head[:8]} — ветку меняли снаружи; разберите вручную"
        )
    return None


def _facts(ctx: _Ctx, pr: int) -> dict | None:
    try:
        facts = ctx.ops.pr_facts(ctx.run.repo_slug, pr)
    except Exception as exc:  # noqa: BLE001 - любая ошибка форджи = стоп
        ctx.stop(f"факты PR #{pr} не прочитаны: {exc} — {RETRY}")
        return None
    if not isinstance(facts, dict):
        ctx.stop(f"факты PR #{pr} не прочитаны: ответ форджи не объект")
        return None
    return facts


# --- code-run ----------------------------------------------------------------


def _run_args(run: PersonalRun) -> list[str]:
    return [
        "run",
        "--all",
        "--no-strict",
        f"--spec-prefix={run.ws_id}-",
        "--integration-pr",
    ]


def _run_prs(ctx: _Ctx) -> list[dict] | None:
    try:
        return ctx.ops.prs_by_head_prefix(ctx.run.repo_slug, RUN_BRANCH_PREFIX)
    except RuntimeError as exc:
        ctx.stop(f"список PR {RUN_BRANCH_PREFIX}* не прочитан: {exc} — {RETRY}")
        return None


def _open_numbers(prs: list[dict]) -> list[int]:
    return sorted(int(p["number"]) for p in prs if p.get("state") == "OPEN")


def _open_run_prs(ctx: _Ctx) -> list[int] | None:
    prs = _run_prs(ctx)
    return None if prs is None else _open_numbers(prs)


def _partial_stop(ctx: _Ctx, prs: list[dict], before: list[int]) -> bool:
    """Открыт частичный PR упавшей попытки — плашка и стоп (#767).

    `True` — шаг встал; `False` — частичного PR нет (человек закрыл его,
    чтобы начать заново), раннер перезапускается как раньше.
    """
    run = ctx.run
    found = run_resume.resume_candidates(prs, before)
    if len(found) > 1:
        ctx.stop(run_resume.several_stop([n for n, _head in found]))
        return True
    if not found:
        return False
    pr, branch = found[0]
    progress = run_resume.read_progress(
        ctx.ops, run.target_dir, branch, TASKS_PATH.format(ws_id=run.ws_id)
    )
    say(run_resume.banner(run.subject, run.repo_slug, branch, progress))
    ctx.stop(run_resume.resume_stop(branch, pr))
    return True


def _step_code_run(ctx: _Ctx) -> bool:
    """spec-runner → новый integration-PR (семантика `auto_tasks._spec_run`)."""
    run = ctx.run
    prs = _run_prs(ctx)
    if prs is None:
        return False
    current = _open_numbers(prs)
    before = current
    if run.before_prs is not None:
        pending = [n for n in current if n not in run.before_prs]
        if pending and run.run_exit == 0:
            return _take_new_pr(ctx, pending)
        if pending and run.run_exit is None:
            partial = ", ".join(f"#{n}" for n in pending)
            return ctx.stop(
                f"исход spec-runner run не наблюдён, открыт integration-PR "
                f"{partial} — он может быть частичным; закройте его и "
                f"повторите dfl run (повтор перезапустит раннер; {ABANDON})"
            )
        if run.run_exit not in (None, 0) and _partial_stop(ctx, prs, run.before_prs):
            return False
        before = sorted(set(run.before_prs) | set(current))
    run.before_prs, run.run_exit = before, None
    save(run)  # write-ahead: PR, открытые до запуска, не примутся
    notice(TUI_HINT.format(ws=run.ws_id))
    rc = run_progress.watched(
        lambda: ctx.ops.spec_runner(run.target_dir, _run_args(run)),
        target_dir=run.target_dir,
        prefix=f"{run.ws_id}-",
        tasks_rel=TASKS_PATH.format(ws_id=run.ws_id),
        emit=notice,
    )  # события задач и живой отсчёт на экране (#765)
    run.run_exit = rc
    save(run)
    after = _open_run_prs(ctx)
    if rc != 0:
        # причина — код раннера, даже если список PR не прочитан
        partial = [n for n in after or [] if n not in before]
        promise = (
            "повтор покажет, что сделано, и не создаст новую ветку"
            if partial
            else "повтор перезапустит раннер"
        )
        return ctx.stop(
            f"spec-runner run вернул {rc} — разберите и повторите dfl run ({promise})"
        )
    if after is None:
        return False
    return _take_new_pr(ctx, [n for n in after if n not in before])


def _incomplete_stop(ctx: _Ctx, pr: int) -> bool:
    """PR прогона открыт, а задачи на его ветке сделаны не все — плашка и стоп.

    spec-runner, остановленный штатно (SIGINT/SIGTERM), выходит кодом 0 и
    открывает частичный PR; без сверки dfl принял бы его за готовый.
    Запись переводится в «упавшую попытку» (`code-run`, `INCOMPLETE_EXIT`),
    дальше — та же развилка, что после ненулевого кода (#767); ручные
    проверки, уже пройденные по этому PR, для следующего задаются заново
    (запись ответов сбрасывается целиком). Счётчик не прочитан — стоп без
    смены шага: неизвестность не значит «готов». `True` — стоп.
    """
    run = ctx.run
    prs = _run_prs(ctx)
    if prs is None:
        return True
    branch = run_resume.pr_branch(prs, pr)
    if branch is None:
        return False
    progress = run_resume.read_progress(
        ctx.ops, run.target_dir, branch, TASKS_PATH.format(ws_id=run.ws_id)
    )
    if progress is None:  # готовность не установлена — запись не трогаем
        ctx.stop(run_resume.unread_stop(branch, pr))
        return True
    if not run_resume.incomplete(progress):
        return False
    run.step, run.code_pr = "code-run", None
    run.run_exit = run_resume.INCOMPLETE_EXIT
    # ответы давались по брошенному PR — следующему зададим заново; issue
    # «не выполнено» не задвоится: `mc.publish` ищет его по маркеру прогона
    run.manual = {}
    save(run)
    say(run_resume.banner(run.subject, run.repo_slug, branch, progress))
    ctx.stop(run_resume.resume_stop(branch, pr))
    return True


def _take_new_pr(ctx: _Ctx, new: list[int]) -> bool:
    if len(new) != 1:
        found = f"найдено {len(new)}: {new}" if new else "не найден"
        return ctx.stop(
            f"spec-runner run: ожидался один новый integration-PR, {found} — {RETRY}"
        )
    ctx.run.code_pr = new[0]
    ctx.run.step = "code-pr"
    say(f"integration-PR #{new[0]} открыт")
    return True


# --- code-pr -----------------------------------------------------------------


def _manual(ctx: _Ctx, pr: int) -> bool:
    """Ручные проверки перед «конвейер закончен» (#771); `False` — стоп
    (ожидание человека — код 0, как пауза на просмотр; сбой — `ctx.stop`)."""
    run = ctx.run
    if not mc.pending(run.manual_checks, run.manual):
        return True

    def texts() -> tuple[str, str]:
        whole = (ctx.bundle / FILENAMES["personal-decomposition"]).read_text(
            encoding="utf-8"
        )
        return whole, whole

    reason = ma.settle(
        run.manual,
        load_texts=texts,
        tty=ctx.tty(),
        read=ctx.read,
        ops=ctx.ops,
        repo_slug=run.repo_slug,
        pr=pr,
        pr_url=f"https://github.com/{run.repo_slug}/pull/{pr}",
        run_id=run.run_id,
        save=lambda: save(run),
        run_dir=run_dir(run.run_id),
        workdir=Path(run.target_dir),
    )
    if reason is None:
        return True
    if ma.is_wait(reason):
        run.stop_reason = reason
        say(reason)
        return False
    return ctx.stop(reason)


def _step_code_pr(ctx: _Ctx) -> bool:
    """Ручные проверки (#771), затем MERGED → `done`; иначе ждём человека
    (код 0)."""
    run = ctx.run
    pr = run.code_pr
    if pr is None:
        return ctx.stop("integration-PR прогона не известен — повторите dfl run")
    if _incomplete_stop(ctx, pr):  # первая сверка нового PR (и старых записей)
        return False
    if not _manual(ctx, pr):
        return False
    facts = _facts(ctx, pr)
    if facts is None:
        return False
    if facts.get("state") == "MERGED":
        run.step = "done"
        done = f"integration-PR #{pr} вмержен — конвейер закончен"
        tail = mc.summary(mc.checks_of(run.manual), run.manual.get("answers") or {})
        say(f"{done}; {tail}" if tail else done)
        return True
    # `pr_facts` не отдаёт url — ссылка строится из слага и номера
    say(f"смержите https://github.com/{run.repo_slug}/pull/{pr} — затем dfl run")
    return False


STEP_HANDLERS: dict[str, Callable[[_Ctx], bool]] = {
    "interview": _step_interview,
    "design": _step_design,
    "decomposition": _step_decomposition,
    "spec-pr": _step_spec_pr,
    "code-run": _step_code_run,
    "code-pr": _step_code_pr,
}
