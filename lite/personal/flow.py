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
import json
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from governance import df_config
from governance.author_launch import config_mode, launch_author
from governance.auto_tasks import RUN_BRANCH_PREFIX, TUI_HINT, notice
from governance.edge_check import override
from governance.edge_check.check import run_check
from governance.ops import Ops
from lite import edge_journal as ej
from lite.edge_resolve import MAX_ROUNDS, Outcome, PaidCalls, Scope, resolve_scope
from lite.interview_chat import Read
from lite.output import say
from lite.personal.brief import Brief, brief_from_json, render_brief
from lite.personal.check import WAVES, NodeCheck, check_node, decomposition_problems
from lite.personal.interviewer import interview
from lite.personal.nodes import FILENAMES, Launch, author_node, revise_node
from lite.personal.state import PersonalRun, run_dir, save
from lite.personal.tasks import TASKS_PATH, render_personal_tasks
from lite.structure import Agent, FormError

BRIEF_JSON = "brief.json"
APPROVALS = "approvals.jsonl"
BRIEF_APPROVAL = {"node": "brief", "how": "подтверждено в разговоре"}
RETRY = "продолжить: dfl run"
_RECHECK = (Outcome.REVISED, Outcome.OVERRIDDEN)
_GIT_ERRORS = (RuntimeError, subprocess.CalledProcessError, OSError)


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
) -> int:
    """Выполнить шаги прогона, пока можно.

    0 — дошёл до `code-pr`/`done` или ждёт человека; 1 — стоп
    (`run.stop_reason` записан и выведен). После каждого шага — `save(run)`.
    """
    ctx = _Ctx(run, ops, read, agent, launch, check)
    run.stop_reason = ""
    save(run)
    while run.step != "done":
        moved = STEP_HANDLERS[run.step](ctx)
        save(run)
        if not moved:
            break
    if ctx.calls.spent():
        say(ctx.calls.line())
    return 1 if ctx.stopped else 0


# --- interview ---------------------------------------------------------------


def _step_interview(ctx: _Ctx) -> bool:
    """Интервью → `brief.json` (R8-a), `00-brief.md`, запись одобрения."""
    run = ctx.run
    rd = run_dir(run.run_id)
    rd.mkdir(parents=True, exist_ok=True)
    brief = interview(rd, run.subject, read=ctx.read, agent=ctx.agent)
    if brief is None:
        return ctx.stop(f"интервью не завершено — {RETRY}")
    (rd / BRIEF_JSON).write_text(
        json.dumps(asdict(brief), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    ctx.bundle.mkdir(parents=True, exist_ok=True)
    (ctx.bundle / FILENAMES["brief"]).write_text(render_brief(brief), encoding="utf-8")
    _approve_brief(rd / APPROVALS)
    run.step = "design"
    return True


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
    if not _node(ctx, "personal-design"):
        return False
    ctx.run.step = "decomposition"
    return True


def _step_decomposition(ctx: _Ctx) -> bool:
    kind = "personal-decomposition"
    if not _node(ctx, kind):
        return False
    brief = _load_brief(ctx)
    if brief is None:
        return False
    text = (ctx.bundle / FILENAMES[kind]).read_text(encoding="utf-8")
    problems = decomposition_problems(text, brief)
    if problems:
        return ctx.stop(
            "декомпозиция нарушает инварианты: "
            + "; ".join(problems)
            + f" — поправьте {FILENAMES[kind]} и повторите dfl run"
        )
    ctx.run.step = "spec-pr"
    return True


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
        outcome = author_node(ctx.run, kind, launch=ctx.launch)
        if outcome.code != 0:
            return ctx.stop(
                f"автор {name} не написал узел: {outcome.reason or outcome.code}"
                f" — {RETRY}"
            )
    result = check_node(ctx.run, kind, check=ctx.check)
    round_no = 0
    while True:
        if result.failure:  # RF-5: без разбора и без переписывания
            return ctx.stop(f"проверка ребра не состоялась: {result.failure}")
        if _passed(ctx.run, kind, result):
            return True
        if round_no == MAX_ROUNDS:
            return ctx.stop(
                f"замечания edge-check к {name} не сошлись за {MAX_ROUNDS} "
                f"круга — поправьте узел и повторите dfl run"
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

    Узлы бандла и `tasks.md` — свои файлы прогона (написаны в рабочее
    дерево до ветки), грязью не считаются. Ветка прогона допустима
    (возобновление после падения между веткой и PR). Возвращает текущую
    ветку; стоп — `None`.
    """
    run, ops = ctx.run, ctx.ops
    own = run.bundle_dir.strip("/") + "/"
    tasks = _tasks_rel(run)
    foreign = [
        p
        for p in ops.dirty_paths(run.target_dir)
        if not p.startswith(own) and p != tasks
    ]
    if foreign:
        shown = ", ".join(foreign[:5]) + (" …" if len(foreign) > 5 else "")
        ctx.stop(
            f"в {run.target_dir} незакоммиченные чужие изменения: {shown} — "
            f"уберите их и повторите dfl run"
        )
        return None
    branch = ops.current_branch(run.target_dir)
    if branch not in (run.base_ref, _spec_branch(run)):
        ctx.stop(
            f"{run.target_dir} на ветке {branch or '(detached)'}, ожидалась "
            f"{run.base_ref} — переключитесь и повторите dfl run"
        )
        return None
    return branch


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
    target, base = run.target_dir, run.base_ref
    branch = _spec_branch(run)
    tasks = _tasks_rel(run)
    try:
        if current != branch:  # на ветке прогона — возобновление, без switch
            ops.checkout_and_pull(target, base)
            ops.switch_to(target, branch, base)
        path = Path(target) / tasks
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tasks_text, encoding="utf-8")
        # пустой индекс (уже закоммичено до падения) — commit_paths не коммитит
        ops.commit_paths(
            target, [run.bundle_dir, tasks], f"spec({run.ws_id}): личный путь"
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
        return ctx.stop(f"spec-PR #{pr} закрыт без мержа — решите вручную")
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
        ops.checkout_and_pull(run.target_dir, run.base_ref)
    except _GIT_ERRORS as exc:
        return ctx.stop(f"checkout/pull {run.base_ref} не удался: {exc} — {RETRY}")
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


def _open_run_prs(ctx: _Ctx) -> list[int] | None:
    try:
        prs = ctx.ops.prs_by_head_prefix(ctx.run.repo_slug, RUN_BRANCH_PREFIX)
    except RuntimeError as exc:
        ctx.stop(f"список PR {RUN_BRANCH_PREFIX}* не прочитан: {exc} — {RETRY}")
        return None
    return sorted(int(p["number"]) for p in prs if p.get("state") == "OPEN")


def _step_code_run(ctx: _Ctx) -> bool:
    """spec-runner → новый integration-PR (семантика `auto_tasks._spec_run`)."""
    run = ctx.run
    current = _open_run_prs(ctx)
    if current is None:
        return False
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
                "повторите dfl run (повтор перезапустит раннер)"
            )
        before = sorted(set(run.before_prs) | set(current))
    run.before_prs, run.run_exit = before, None
    save(run)  # write-ahead: PR, открытые до запуска, не примутся
    notice(TUI_HINT.format(ws=run.ws_id))
    rc = ctx.ops.spec_runner(run.target_dir, _run_args(run))
    run.run_exit = rc
    save(run)
    if rc != 0:
        return ctx.stop(
            f"spec-runner run вернул {rc} — разберите и повторите dfl run "
            "(повтор перезапустит раннер)"
        )
    after = _open_run_prs(ctx)
    if after is None:
        return False
    return _take_new_pr(ctx, [n for n in after if n not in before])


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


def _step_code_pr(ctx: _Ctx) -> bool:
    """MERGED → `done`; иначе ждём человека (код 0)."""
    run = ctx.run
    pr = run.code_pr
    if pr is None:
        return ctx.stop("integration-PR прогона не известен — повторите dfl run")
    facts = _facts(ctx, pr)
    if facts is None:
        return False
    if facts.get("state") == "MERGED":
        run.step = "done"
        say(f"integration-PR #{pr} вмержен — конвейер закончен")
        return True
    say(f"смержите {facts.get('url') or f'PR #{pr}'} — затем dfl run")
    return False


STEP_HANDLERS: dict[str, Callable[[_Ctx], bool]] = {
    "interview": _step_interview,
    "design": _step_design,
    "decomposition": _step_decomposition,
    "spec-pr": _step_spec_pr,
    "code-run": _step_code_run,
    "code-pr": _step_code_pr,
}
