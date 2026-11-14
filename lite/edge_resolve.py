"""Разбор блокирующих замечаний edge-check разговором (спека
`2026-11-14-dfl-edge-findings-resolution-design.md` §3, §5, §6, §9).

Прогон стоит в `stopped_review` с находками к узлам волны: агент сортирует
их (`lite.edge_triage`), человека спрашивают только о решениях по существу,
ответы сразу пишутся в журнал (`lite.edge_journal`), автор переписывает файл
узла через границу `launch_author` (`ops.revise`), а `lite.node_guard`
отклоняет переписывание, ослабившее узел. Повтор проверки — обычный шаг
`dfl run` (resume), его делает вызывающий.
"""

from __future__ import annotations

import enum
import os
from collections.abc import Callable
from pathlib import Path

from governance import run_state as rs
from governance.author_launch import AuthorCall, LaunchOutcome
from governance.edge_check.author_rules import DEFAULT_CONTRACTS
from governance.edge_check.rules import EdgeCheckError, load_rules
from governance.ops import _AUTHOR_FILENAMES
from lite import edge_journal as ej
from lite import node_guard
from lite.edge_triage import Item, triage
from lite.interview_chat import Read, is_skip, read_answer
from lite.output import say
from lite.structure import Agent, AgentUnavailable, FormError

MAX_ROUNDS = 2
FIXING = "исправлю сам: {n}; вопросов к вам: {m}"
OVERRIDE_OFFER = "принять узел вопреки замечанию (запишется в журнал и в PR)"
REVIEW_STATUS = "stopped_review"
REVISE_DIR = "edge-revise"
OWN_PREFIX = "своё:"
DISAGREE_PREFIX = "не согласен:"
ANSWER_HELP = "«своё: …» · «не согласен: <почему>» · «пропустить» — остановиться"
REASK = "не понял ответ — номер варианта, «своё: …», «не согласен: …» или «пропустить»"
NO_REASON = "напишите причину после «не согласен:»"
REPEAT_QUESTION = "Замечание вернулось после вашего несогласия. Что делаем?"
KIND_LABELS = {
    "option": "",
    "own": OWN_PREFIX + " ",
    "disagree": DISAGREE_PREFIX + " ",
    "override": "",
}

Key = tuple[str, str, str]
Revise = Callable[..., LaunchOutcome]


class Outcome(enum.Enum):
    """Итог разбора: что делать вызывающему."""

    REVISED = "revised"  # узел переписан — повторить шаг
    OVERRIDDEN = "overridden"  # все замечания приняты вопреки — повторить шаг
    STOPPED = "stopped"  # человек остановился или сбой — прогон стоит
    NOT_APPLICABLE = "not_applicable"  # разбирать нечего — прежний стоп


def resolve(
    state: rs.RunState,
    *,
    read: Read,
    agent: Agent,
    revise: Revise,
    round_no: int,
) -> Outcome:
    """Один круг разбора замечаний волны: сортировка, вопросы, переписывание.

    `revise(call, kind, subject, current, findings, decisions)` — граница
    автора (`RealOps.revise`); `round_no` — номер круга в этом `dfl run`
    (на `MAX_ROUNDS` в `test` предлагается принять узел вопреки замечанию).
    """
    findings = applicable_findings(state)
    if findings is None:
        return Outcome.NOT_APPLICABLE
    run_dir = rs.run_dir(state.run_id)
    nodes = _read_files(state, {f.path for f in findings if f.path})
    if nodes is None:
        return Outcome.NOT_APPLICABLE
    prior = ej.load(run_dir, state.wave)
    try:
        items = triage(
            findings, nodes, _bases(state, findings), prior, agent, cache_dir=run_dir
        )
    except (FormError, AgentUnavailable) as exc:
        say(f"разбор замечаний не состоялся: {exc}")
        return Outcome.STOPPED
    chosen = _ask_all(state, items, prior, read, round_no)
    if chosen is None:
        return Outcome.STOPPED
    keys = {f.key for f in findings}
    if keys <= ej.overridden(run_dir, state.wave):
        say("замечания приняты вопреки — записано в журнал; проверяю снова")
        return Outcome.OVERRIDDEN
    return _rewrite(state, findings, nodes, chosen, revise, round_no)


def applicable_findings(state: rs.RunState) -> list[ej.Finding] | None:
    """Блокирующие находки волны, если их можно разобрать; иначе `None`.

    Нечего разбирать: прогон не в `stopped_review`, нет блокирующих находок
    (отказ прибора, `insufficient`), записи не читаются, у находки нет файла
    или файл — не узел её вида (§9 п.4).
    """
    if state.status != REVIEW_STATUS or not state.wave:
        return None
    try:
        findings = ej.blocking_findings(rs.run_dir(state.run_id), state.wave)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not findings:
        return None
    if any(not f.path or f.path != _AUTHOR_FILENAMES.get(f.node) for f in findings):
        return None
    return findings


def _node_path(state: rs.RunState, filename: str) -> Path:
    return Path(state.target_dir) / state.bundle_dir / filename


def _read_files(state: rs.RunState, names: set[str]) -> dict[str, str] | None:
    """Текущие тексты файлов узлов (с ручными правками); не читается — `None`."""
    try:
        return {
            n: _node_path(state, n).read_text(encoding="utf-8") for n in sorted(names)
        }
    except (OSError, UnicodeDecodeError):
        return None


def _bases(state: rs.RunState, findings: list[ej.Finding]) -> dict[str, str]:
    """Тексты оснований рёбер (`basis_roles`); роли без файла пропускаются."""
    out: dict[str, str] = {}
    for edge in dict.fromkeys(f.edge for f in findings):
        try:
            roles = load_rules(edge, DEFAULT_CONTRACTS).basis_roles
        except EdgeCheckError:
            continue
        for role in roles:
            name = _AUTHOR_FILENAMES.get(role)
            if name is None or name in out:
                continue
            try:
                out[name] = _node_path(state, name).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
    return out


def _ask_all(
    state: rs.RunState,
    items: list[Item],
    prior: list[ej.Decision],
    read: Read,
    round_no: int,
) -> list[ej.Decision] | None:
    """Вопросы по `decision` и по вернувшимся после несогласия; каждое решение
    сразу в журнал. Человек остановился — `None` (записанное остаётся)."""
    disagreed = {d.key for d in prior if d.kind == "disagree"}
    test = state.mode == "test"
    asked = [
        i for i in items if i.kind == "decision" or (test and _repeat(i, disagreed))
    ]
    say(FIXING.format(n=len(items) - len(asked), m=len(asked)))
    chosen: list[ej.Decision] = []
    for item in asked:
        allow = test and (item.finding.key in disagreed or round_no == MAX_ROUNDS)
        decision = _ask(item, read, allow, round_no)
        if decision is None:
            say("разбор остановлен — продолжить: dfl run")
            return None
        ej.append(rs.run_dir(state.run_id), state.wave, decision)
        chosen.append(decision)
    return chosen


def _repeat(item: Item, disagreed: set[Key]) -> bool:
    return item.kind == "fix" and item.finding.key in disagreed


def _ask(
    item: Item, read: Read, allow_override: bool, round_no: int
) -> ej.Decision | None:
    """Один вопрос; непонятый ответ — переспросить один раз, затем стоп."""
    question = item.question or REPEAT_QUESTION
    labels = [o.label for o in item.options]
    say(f"замечание {_where(item.finding)}: {item.finding.statement}")
    say(question)
    for no, option in enumerate(item.options, 1):
        say(f"  {no}. {option.label}{' (рекомендую)' if option.recommended else ''}")
    if allow_override:
        say(f"  {len(labels) + 1}. {OVERRIDE_OFFER}")
    say(ANSWER_HELP)
    for attempt in range(2):
        text = read_answer(read)
        if text is None or is_skip(text):
            return None
        decision = _parse(item, question, text, allow_override, round_no)
        if decision is not None:
            return decision
        if attempt == 0:
            say(NO_REASON if _is_disagree(text) else REASK)
    return None


def _parse(
    item: Item, question: str, text: str, allow_override: bool, round_no: int
) -> ej.Decision | None:
    """Ответ → решение; не разобран (или «не согласен» без причины) — `None`."""
    key, text = item.finding.key, text.strip()

    def made(answer: str, kind: str, allows: dict[str, list[str]]) -> ej.Decision:
        return ej.Decision(key, question, answer, kind, round_no, allows)

    if text.isdigit():
        no = int(text)
        if 1 <= no <= len(item.options):
            o = item.options[no - 1]
            allows = {"removes": o.removes, "downgrades": o.downgrades}
            return made(o.label, "option", allows)
        if allow_override and no == len(item.options) + 1:
            return made(OVERRIDE_OFFER, "override", {})
        return None
    for prefix, kind in ((OWN_PREFIX, "own"), (DISAGREE_PREFIX, "disagree")):
        if text.casefold().startswith(prefix):
            rest = text[len(prefix) :].strip()
            return made(rest, kind, {}) if rest else None
    return None


def _is_disagree(text: str) -> bool:
    return text.strip().casefold().startswith(DISAGREE_PREFIX)


def _where(f: ej.Finding) -> str:
    return f"{f.rule_id} {f.cls} ({f.path}:{f.lines})"


def _rewrite(
    state: rs.RunState,
    findings: list[ej.Finding],
    nodes: dict[str, str],
    chosen: list[ej.Decision],
    revise: Revise,
    round_no: int,
) -> Outcome:
    """Переписать каждый файл с неснятыми находками; сбой или ослабление —
    вернуть копии всех файлов круга и стоп."""
    run_dir = rs.run_dir(state.run_id)
    skipped = ej.overridden(run_dir, state.wave)
    todo = [f for f in findings if f.key not in skipped]
    journal = ej.load(run_dir, state.wave)
    allows = (
        {i for d in chosen for i in d.allows.get("removes", [])},
        {i for d in chosen for i in d.allows.get("downgrades", [])},
    )
    done: list[str] = []
    for name in dict.fromkeys(f.path for f in todo if f.path):
        mine = [f for f in todo if f.path == name]
        _backup(run_dir, round_no, name, nodes[name])
        done.append(name)
        reason = _revise_file(state, name, nodes[name], mine, journal, revise)
        if reason is None:
            reason = _weakened(state, name, nodes[name], *allows)
        if reason is not None:
            for restored in done:
                _node_path(state, restored).write_text(nodes[restored], "utf-8")
            say(reason)
            return Outcome.STOPPED
    for f in todo:
        say(f"  исправлено: {_where(f)}: {f.statement}", 1)
    say(f"узел переписан ({', '.join(done)}): замечаний {len(todo)}; проверяю снова")
    return Outcome.REVISED


def _backup(run_dir: Path, round_no: int, name: str, text: str) -> None:
    """Копия файла до переписывания: `edge-revise/<круг>/<файл>` (§9 п.5)."""
    path = run_dir / REVISE_DIR / str(round_no) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _revise_file(
    state: rs.RunState,
    name: str,
    current: str,
    findings: list[ej.Finding],
    journal: list[ej.Decision],
    revise: Revise,
) -> str | None:
    """Вызов автора по файлу; отказ — причина для человека, иначе `None`."""
    call = AuthorCall(
        run_id=state.run_id,
        run_dir=os.path.realpath(rs.run_dir(state.run_id)),
        target_dir=state.target_dir,
        bundle_dir=state.bundle_dir,
        filename=name,
    )
    keys = {f.key for f in findings}
    decisions = "\n".join(_decision_line(d) for d in journal if d.key in keys)
    outcome = revise(
        call,
        findings[0].node,
        state.subject,
        current,
        "\n".join(f"- {_where(f)}: {f.statement}" for f in findings),
        decisions or "нет",
    )
    if outcome.code != 0:
        return f"переписывание {name} не состоялось: {outcome.reason or outcome.code}"
    return None


def _decision_line(d: ej.Decision) -> str:
    """Решение дословно: «вопрос → ответ»."""
    return f"- {d.question} → {KIND_LABELS.get(d.kind, '')}{d.answer}"


def _weakened(
    state: rs.RunState, name: str, before: str, removes: set[str], downgrades: set[str]
) -> str | None:
    """Сверка до/после (§5); ослаблен или не читается — причина, иначе `None`."""
    try:
        after = _node_path(state, name).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return f"переписанный {name} не читается: {exc}"
    bad = node_guard.weakened(before, after, removes, downgrades)
    if not bad:
        return None
    return (
        f"переписывание {name} отклонено — ослабляет узел без вашего решения: "
        f"{', '.join(bad)}; файл возвращён"
    )
