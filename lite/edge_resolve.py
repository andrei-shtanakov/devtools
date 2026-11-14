"""Разбор блокирующих замечаний edge-check разговором (спека
`2026-11-14-dfl-edge-findings-resolution-design.md` §3, §5, §6, §9).

Прогон стоит в `stopped_review` с находками к узлам волны: агент сортирует
их (`lite.edge_triage`), человека спрашивают только о решениях по существу,
ответы сразу пишутся в журнал (`lite.edge_journal`), автор переписывает файл
узла через границу `launch_author` (`ops.revise`), а `lite.node_guard`
отклоняет переписывание, ослабившее узел. Повтор проверки — обычный шаг
`dfl run` (resume), его делает вызывающий.

Что разбирать, решает файл стопа раннера (`edge-findings.txt`): записи рёбер
на диске после отказа прибора остаются от прошлой попытки. Файлы узлов и
оснований читаются и пишутся только через `governance.safe_io` (без ссылок,
только обычные файлы, с потолком размера).
"""

from __future__ import annotations

import enum
import os
import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from governance import run_state as rs
from governance import safe_io
from governance.author_launch import NODE_LIMIT, AuthorCall, LaunchOutcome
from governance.edge_check import override
from governance.edge_check.author_rules import DEFAULT_CONTRACTS
from governance.edge_check.rules import EdgeCheckError, load_rules
from governance.ops import _AUTHOR_FILENAMES
from lite import edge_journal as ej
from lite import edge_memory as em
from lite import node_guard
from lite import stop_findings as sf
from lite.edge_triage import Item, Option, triage
from lite.interview_chat import Read, is_skip, read_answer
from lite.output import say
from lite.structure import Agent, AgentUnavailable, FormError

MAX_ROUNDS = 2
FIXING = "исправлю сам: {n}; вопросов к вам: до {m}"
OVERRIDE_OFFER = "принять узел вопреки замечанию (запишется в журнал и в PR)"
OVERRIDE_GROUP = (
    "принять узел вопреки всем {n} замечаниям правила {rule} "
    "(перекрывает ответы по ним; запишется в журнал и в PR)"
)
NOT_COVERED = "принятие вопреки не покрывает все замечания уровня — разбор остановлен"
REVIEW_STATUS = sf.EDGE
REVISE_DIR = "edge-revise"
OWN_PREFIX = "своё:"
DISAGREE_PREFIX = "не согласен:"
ANSWER_HELP = "«своё: …» · «не согласен: <почему>» · «пропустить» — остановиться"
REASK = "не понял ответ — номер варианта, «своё: …», «не согласен: …» или «пропустить»"
NO_REASON = "напишите причину после «не согласен:»"
KIND_LABELS = {
    "option": "",
    "own": OWN_PREFIX + " ",
    "disagree": DISAGREE_PREFIX + " ",
    "override": "",
}
# `error EDGE-CHECK(<узел>/<ребро>): R1 major: …` — ключ находки файла стопа
_STOP_KEY_RE = re.compile(r"^error EDGE-CHECK\(([^/)]+)/([^)]+)\): (R\d+) ")

Key = tuple[str, str, str]
Revise = Callable[..., LaunchOutcome]


class Outcome(enum.Enum):
    """Итог разбора: что делать вызывающему."""

    REVISED = "revised"  # узел переписан — повторить шаг
    OVERRIDDEN = "overridden"  # все замечания приняты вопреки — повторить шаг
    STOPPED = "stopped"  # человек остановился или сбой — прогон стоит
    NOT_APPLICABLE = "not_applicable"  # разбирать нечего — прежний стоп


@dataclass
class PaidCalls:
    """Платные вызовы за `dfl run`: сортировка (без кэша), переписывание,
    повторная проверка (считает вызывающий — по шагу после круга)."""

    triage: int = 0
    revise: int = 0
    check: int = 0

    def spent(self) -> bool:
        """Был ли хоть один платный вызов."""
        return bool(self.triage or self.revise or self.check)

    def line(self) -> str:
        """Строка для человека."""
        return (
            f"платных вызовов: сортировка {self.triage}, переписывание "
            f"{self.revise}, проверка {self.check}"
        )


class _Unreadable(Exception):
    """Файл узла или основания не прочитан безопасно; текст — причина."""


def resolve(
    state: rs.RunState,
    *,
    read: Read,
    agent: Agent,
    revise: Revise,
    round_no: int,
    calls: PaidCalls | None = None,
) -> Outcome:
    """Один круг разбора замечаний волны: сортировка, вопросы, переписывание.

    `revise(call, kind, subject, current, findings, decisions)` — граница
    автора (`RealOps.revise`); `round_no` — номер круга в этом `dfl run`
    (на `MAX_ROUNDS` в `test` предлагается принять узел вопреки замечанию);
    `calls` — счётчик платных вызовов вызывающего.
    """
    calls = calls if calls is not None else PaidCalls()
    findings = applicable_findings(state)
    if findings is None:
        return Outcome.NOT_APPLICABLE
    try:
        nodes = _read_files(state, dict.fromkeys(f.path for f in findings if f.path))
        bases = _bases(state, findings)
    except _Unreadable as exc:
        say(f"разбор замечаний невозможен: {exc}")
        return Outcome.NOT_APPLICABLE
    run_dir = rs.run_dir(state.run_id)
    prior = ej.load(run_dir, state.wave)
    try:
        records = ej.level_records(run_dir, state.wave)
    except (OSError, ValueError, KeyError, TypeError):
        return Outcome.NOT_APPLICABLE
    refusal = override.non_overridable_reason(records)
    try:
        items = triage(
            findings, nodes, bases, prior, _counted(agent, calls), cache_dir=run_dir
        )
    except (FormError, AgentUnavailable) as exc:
        say(f"разбор замечаний не состоялся: {exc}")
        return Outcome.STOPPED
    notes: set[str] = set()
    visible = [_visible(i) for i in items]
    chosen = _ask_all(state, visible, prior, read, round_no, refusal, notes)
    if chosen is None:
        return Outcome.STOPPED
    overrides = ej.overridden(run_dir, state.wave)
    if {f.digest for f in findings} <= overrides:
        if override.accepted(records, overrides) is not None:
            say("замечания приняты вопреки — записано в журнал; проверяю снова")
            return Outcome.OVERRIDDEN
        _note(notes, refusal or NOT_COVERED)
        return Outcome.STOPPED
    return _rewrite(state, findings, nodes, revise, round_no, calls, overrides)


def override_offer(n: int, rule: str) -> str:
    """Вариант «принять вопреки»: одна находка — прежняя строка; несколько
    находок одного правила на том же тексте (общий digest) — все сразу,
    перекрывая ответы по ним."""
    return OVERRIDE_OFFER if n == 1 else OVERRIDE_GROUP.format(rule=rule, n=n)


def _note(notes: set[str], text: str) -> None:
    """Сказать один раз за круг."""
    if text not in notes:
        notes.add(text)
        say(text)


def applicable_findings(state: rs.RunState) -> list[ej.Finding] | None:
    """Блокирующие находки волны, если их можно разобрать; иначе `None`.

    Нечего разбирать: прогон не в `stopped_review`; в файле стопа нет
    блокирующих находок, есть отказ прибора (ошибка ребра) или FAIL ребра
    без находок (`insufficient`, `runner.EDGE_UNRATED`); записи не
    читаются; у находки нет файла или файл — не узел её вида (§9 п.4).
    Из записей рёбер берутся только находки, чей ключ назван в файле стопа:
    остальное — след прошлой попытки.
    """
    keys = stop_keys(state)
    if not keys:
        return None
    try:
        found = ej.blocking_findings(rs.run_dir(state.run_id), state.wave)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    findings = [f for f in found if f.key in keys]
    if not findings:
        return None
    if any(not f.path or f.path != _AUTHOR_FILENAMES.get(f.node) for f in findings):
        return None
    return findings


def stop_keys(state: rs.RunState) -> set[Key]:
    """Ключи блокирующих находок файла стопа; стоп не разбираемый — пусто."""
    if state.status != REVIEW_STATUS or not state.wave:
        return set()
    stop = sf.load(state.run_id, state.status)
    if stop is None or stop.failures or stop.unrated:
        return set()
    blocking, _advisory = sf.split(stop.findings)
    keys: set[Key] = set()
    for line in blocking:
        m = _STOP_KEY_RE.match(line)
        if m is None:
            return set()  # строку не понять — не гадаем
        keys.add((m.group(1), m.group(2), m.group(3)))
    return keys


def _counted(agent: Agent, calls: PaidCalls) -> Agent:
    """Агент, считающий вызовы: попадание в кэш сортировки его не зовёт."""

    def call(prompt: str) -> str:
        calls.triage += 1
        return agent(prompt)

    return call


def _target_root(state: rs.RunState) -> Path:
    """Канонический корень чекаута цели (требование `safe_io`)."""
    return Path(os.path.realpath(state.target_dir))


def _rel(state: rs.RunState, name: str) -> str:
    return f"{state.bundle_dir.strip('/')}/{name}"


def _read_node(state: rs.RunState, name: str) -> str:
    """Текст файла бандла через `safe_io`; отказ — `_Unreadable`."""
    try:
        data = safe_io.read_regular(
            _target_root(state), _rel(state, name), limit=NODE_LIMIT
        )
        return data.decode("utf-8")
    except (safe_io.SafeIOError, UnicodeDecodeError) as exc:
        raise _Unreadable(f"{name}: {exc}") from exc


def _read_files(state: rs.RunState, names: Iterable[str]) -> dict[str, str]:
    """Текущие тексты файлов узлов (с ручными правками)."""
    return {n: _read_node(state, n) for n in names}


def _bases(state: rs.RunState, findings: list[ej.Finding]) -> dict[str, str]:
    """Тексты оснований рёбер (`basis_roles`); роли без файла и отсутствующие
    файлы пропускаются, файл, который нельзя прочитать безопасно, — отказ."""
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
            if not os.path.lexists(_target_root(state) / _rel(state, name)):
                continue
            out[name] = _read_node(state, name)
    return out


def _visible(item: Item) -> Item:
    """Эффекты вариантов — только id, которые человек видит (в тексте
    находки, вопросе или самом варианте); прочие отбрасываются."""
    if not item.options:
        return item
    shown = f"{item.finding.statement}\n{item.question}"
    options = [
        replace(
            o,
            removes=_mentioned(o.removes, f"{shown}\n{o.label}"),
            downgrades=_mentioned(o.downgrades, f"{shown}\n{o.label}"),
        )
        for o in item.options
    ]
    return replace(item, options=options)


def _mentioned(ids: list[str], text: str) -> list[str]:
    return [i for i in ids if re.search(rf"(?<![\w-]){re.escape(i)}(?![\w-])", text)]


def _ask_all(
    state: rs.RunState,
    items: list[Item],
    prior: list[ej.Decision],
    read: Read,
    round_no: int,
    refusal: str | None,
    notes: set[str],
) -> list[ej.Decision] | None:
    """Вопросы по `decision` и по тем, что журнал велит спросить снова
    (`edge_memory.recall`); каждое решение сразу в журнал. Человек
    остановился — `None` (записанное остаётся).

    Принятие вопреки снимает блокирующий вердикт правила на тексте узла —
    всю группу находок с общим digest: вариант добавляется к вопросу каждого
    пункта группы, а выбранный — снимает остальные её вопросы. Уровень
    нельзя принять (`refusal`) — варианта нет, причина сказана один раз.
    """
    memory = em.recall(
        prior,
        [i.finding for i in items],
        test=state.mode == "test",
        last_round=round_no == MAX_ROUNDS,
    )
    questions = _questions(items, memory, refusal is None)
    if refusal is not None and any(
        memory[q.item.finding.key].offer_override for q in questions
    ):
        _note(notes, refusal)
    say(FIXING.format(n=len(items) - len(questions), m=len(questions)))
    batch = uuid.uuid4().hex
    accepted_groups: set[str] = set()
    chosen: list[ej.Decision] = []
    for q in questions:
        if q.group in accepted_groups:
            continue
        question = memory[q.item.finding.key].question or q.item.question
        decision = _ask(q, question, read, round_no)
        if decision is None:
            say("разбор остановлен — продолжить: dfl run")
            return None
        decision = replace(decision, batch=batch)
        ej.append(rs.run_dir(state.run_id), state.wave, decision)
        chosen.append(decision)
        if decision.kind == "override":
            accepted_groups.add(q.group)
    return chosen


@dataclass(frozen=True)
class _Question:
    """Вопрос по пункту `item`; `listed` — `fix`-пункты группы, показанные
    при нём; `offer` — вариант принятия группы `group` вопреки или `None`."""

    item: Item
    listed: list[Item]
    offer: str | None
    group: str


def _questions(
    items: list[Item], memory: dict[Key, em.Recall], can_override: bool
) -> list[_Question]:
    """Вопрос на каждый спрашиваемый пункт; группа находок с общим digest
    (одно правило на том же тексте) получает в каждом вопросе общий вариант
    принятия, а её `fix`-пункты видны при первом вопросе."""
    groups: dict[str, list[Item]] = {}
    for no, item in enumerate(items):
        groups.setdefault(item.finding.digest or f"#{no}", []).append(item)
    out: list[_Question] = []
    for gid, group in groups.items():
        recall = memory[group[0].finding.key]  # ключ у группы общий
        asked = [i for i in group if i.kind == "decision" or recall.ask]
        offered = recall.offer_override and can_override and bool(asked)
        offer = (
            override_offer(len(group), group[0].finding.rule_id) if offered else None
        )
        fixes = [i for i in group if i.kind != "decision" and not recall.ask]
        for no, item in enumerate(asked):
            listed = fixes if offered and no == 0 else []
            out.append(_Question(item, listed, offer, gid))
    return out


def _ask(q: _Question, question: str, read: Read, round_no: int) -> ej.Decision | None:
    """Один вопрос; непонятый ответ — переспросить один раз, затем стоп."""
    item, offer = q.item, q.offer
    say(f"замечание {_where(item.finding)}: {item.finding.statement}")
    for one in q.listed:
        say(
            f"  то же правило, исправлю сам: {_where(one.finding)}: {one.finding.statement}"
        )
    say(question)
    for no, option in enumerate(item.options, 1):
        say(f"  {no}. {_option_line(option)}")
    if offer is not None:
        say(f"  {len(item.options) + 1}. {offer}")
    say(ANSWER_HELP)
    for attempt in range(2):
        text = read_answer(read)
        if text is None or is_skip(text):
            return None
        decision = _parse(item, question, text, offer, round_no)
        if decision is not None:
            return decision
        if attempt == 0:
            say(NO_REASON if _starts(text, DISAGREE_PREFIX) else REASK)
    return None


def _option_line(o: Option) -> str:
    """Вариант с эффектами: что удалит и что понизит — видно до выбора."""
    effects = ([f"удалит: {', '.join(o.removes)}"] if o.removes else []) + (
        [f"понизит: {', '.join(o.downgrades)}"] if o.downgrades else []
    )
    tail = f", {'; '.join(effects)}" if effects else ""
    return f"{o.label}{tail}{' (рекомендую)' if o.recommended else ''}"


def _parse(
    item: Item, question: str, text: str, offer: str | None, round_no: int
) -> ej.Decision | None:
    """Ответ → решение; не разобран (или «не согласен» без причины) — `None`."""
    finding, text = item.finding, text.strip()

    def made(answer: str, kind: str, allows: dict[str, list[str]]) -> ej.Decision:
        return ej.Decision(
            finding.key,
            question,
            answer,
            kind,
            round_no,
            allows,
            digest=finding.digest,
            subject=list(finding.subject),
        )

    if text.isdigit():
        no = int(text)
        if 1 <= no <= len(item.options):
            o = item.options[no - 1]
            allows = {"removes": o.removes, "downgrades": o.downgrades}
            return made(o.label, "option", allows)
        if offer is not None and no == len(item.options) + 1:
            return made(offer, "override", {})
        return None
    for prefix, kind in ((OWN_PREFIX, "own"), (DISAGREE_PREFIX, "disagree")):
        if _starts(text, prefix):
            rest = text[len(prefix) :].strip()
            return made(rest, kind, {}) if rest else None
    return None


def _fold(text: str) -> str:
    """Для сравнения: регистр не важен, «ё» = «е» (длина строки та же)."""
    return text.casefold().replace("ё", "е")


def _starts(text: str, prefix: str) -> bool:
    return _fold(text.strip()).startswith(_fold(prefix))


def _where(f: ej.Finding) -> str:
    return f"{f.rule_id} {f.cls} ({f.path}:{f.lines})"


def _rewrite(
    state: rs.RunState,
    findings: list[ej.Finding],
    nodes: dict[str, str],
    revise: Revise,
    round_no: int,
    calls: PaidCalls,
    skipped: set[str],
) -> Outcome:
    """Переписать каждый файл с неснятыми находками (`skipped` — digest'ы
    групп, принятых вопреки последним решением); сбой, ослабление или
    прерывание — вернуть тексты всех файлов круга."""
    run_dir = rs.run_dir(state.run_id)
    todo = [f for f in findings if f.digest not in skipped]
    journal = ej.load(run_dir, state.wave)
    done: list[str] = []
    try:
        for name in dict.fromkeys(f.path for f in todo if f.path):
            mine = [f for f in todo if f.path == name]
            _backup(run_dir, round_no, name, nodes[name])
            done.append(name)
            calls.revise += 1
            reason = _revise_file(state, name, nodes[name], mine, journal, revise)
            if reason is None:
                reason = _weakened(state, name, nodes[name], _allows(journal, mine))
            if reason is not None:
                say(reason)
                _restore(state, nodes, done)
                return Outcome.STOPPED
    except BaseException:
        _restore(state, nodes, done)
        raise
    for f in todo:
        say(f"  исправлено: {_where(f)}: {f.statement}", 1)
    say(f"узел переписан ({', '.join(done)}): замечаний {len(todo)}; проверяю снова")
    return Outcome.REVISED


def _allows(
    journal: list[ej.Decision], findings: list[ej.Finding]
) -> tuple[set[str], set[str]]:
    """Разрешённые удаления/понижения по находкам этого файла: журнал волны
    (выбор мог быть до остановки), последнее решение на неизменённом тексте."""
    memory = em.recall(journal, findings).values()
    return (
        {i for r in memory for i in r.removes},
        {i for r in memory for i in r.downgrades},
    )


def _backup(run_dir: Path, round_no: int, name: str, text: str) -> None:
    """Копия файла до переписывания: `edge-revise/<круг>/<файл>` (§9 п.5)."""
    root = Path(os.path.realpath(run_dir))
    safe_io.publish(root, f"{REVISE_DIR}/{round_no}/{name}", text.encode("utf-8"))


def _restore(state: rs.RunState, nodes: dict[str, str], names: list[str]) -> None:
    """Вернуть тексты файлов круга; не вернувшиеся — назвать."""
    failed: list[str] = []
    for name in names:
        try:
            safe_io.publish(
                _target_root(state), _rel(state, name), nodes[name].encode("utf-8")
            )
        except safe_io.SafeIOError:
            failed.append(name)
    if failed:
        say(
            f"файлы не возвращены: {', '.join(failed)} — копии в "
            f"{rs.run_dir(state.run_id) / REVISE_DIR}"
        )


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
    state: rs.RunState, name: str, before: str, allows: tuple[set[str], set[str]]
) -> str | None:
    """Сверка до/после (§5); ослаблен или не читается — причина, иначе `None`."""
    try:
        after = _read_node(state, name)
    except _Unreadable as exc:
        return f"переписанный файл не читается: {exc}"
    bad = node_guard.weakened(before, after, *allows)
    if not bad:
        return None
    return (
        f"переписывание {name} отклонено — ослабляет узел без вашего решения: "
        f"{', '.join(bad)}; файл возвращён"
    )
