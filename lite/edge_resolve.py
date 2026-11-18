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

Без терминала (#766 срез 2) вопрос уходит в `pending.json` (`Scope.ask`), круг
останавливается с `Outcome.WAITING`; продолжение после `dfl reply` дописывает
ту же партию ответов (`Waiting.batch`), сортировка берётся из кэша. Номер
ожидающего круга (и находки личного пути) вызывающий хранит в `WAIT_FILE`.
"""

from __future__ import annotations

import enum
import json
import os
import re
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from governance import run_state as rs
from governance import safe_io
from governance.author_launch import NODE_LIMIT, AuthorCall, LaunchOutcome
from governance.edge_check import override
from governance.edge_check.rules import EdgeCheckError, load_rules
from governance.ops import _AUTHOR_FILENAMES
from lite import edge_journal as ej
from lite import edge_memory as em
from lite import i18n, node_guard, pending
from lite import stop_findings as sf
from lite.edge_triage import Item, Option, triage
from lite.interview_chat import Read, is_skip, read_answer
from lite.output import say
from lite.personal.check import contracts_dir
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
#: ожидающие круги разбора: слот (`wave-<w>` / `node-<вид>`) → круг и находки
WAIT_FILE = "edge-wait.json"
STOPPED_LINE = "разбор остановлен — продолжить: dfl run"
OWN_PREFIX = "своё:"
DISAGREE_PREFIX = "не согласен:"
OWN_PREFIXES = pending.EDGE_OWN_PREFIXES
DISAGREE_PREFIXES = pending.EDGE_DISAGREE_PREFIXES
ANSWER_HELP = (
    "номер варианта — отправится сразу · «своё: …» · «не согласен: <почему>» "
    "(пустая строка — отправить) · «пропустить» — остановиться"
)
#: подсказка записанного вопроса: только ответы, которые принимает `dfl reply`
REMOTE_HELP = "номер варианта · «своё: …» · «не согласен: <почему>»"
REASK = "не понял ответ — номер варианта, «своё: …», «не согласен: …» или «пропустить»"
NO_REASON = "напишите причину после «не согласен:»"
#: строки экрана разбора (шаблоны; перевод — `i18n.t` в месте вывода)
UNRESOLVABLE = "разбор замечаний невозможен: {exc}"
TRIAGE_FAILED = "разбор замечаний не состоялся: {exc}"
OVERRIDDEN_LINE = "замечания приняты вопреки — записано в журнал; проверяю снова"
SAME_RULE_LINE = "  то же правило, исправлю сам: {where}: {statement}"
FINDING_LINE = "замечание {where}: {statement}"
REMOVES = "удалит: {ids}"
DOWNGRADES = "понизит: {ids}"
RECOMMENDED = " (рекомендую)"
FIXED_LINE = "  исправлено: {where}: {statement}"
REWRITTEN = "узел переписан ({names}): замечаний {n}; проверяю снова"
NOT_RESTORED = "файлы не возвращены: {names} — копии в {path}"
REVISE_FAILED = "переписывание {name} не состоялось: {why}"
UNREADABLE_AFTER = "переписанный файл не читается: {exc}"
WEAKENED = (
    "переписывание {name} отклонено — ослабляет узел без вашего решения: "
    "{ids}; файл возвращён"
)
KIND_LABELS = {
    "option": "",
    "own": OWN_PREFIX + " ",
    "disagree": DISAGREE_PREFIX + " ",
    "override": "",
}
#: подписи решений в промпте переписывания по языку прогона
_KIND_LABELS = {
    "ru": KIND_LABELS,
    "en": {"option": "", "own": "own: ", "disagree": "disagree: ", "override": ""},
}
_NO_DECISIONS = {"ru": "нет", "en": "none"}
# `error EDGE-CHECK(<узел>/<ребро>): R1 major: …` — ключ находки файла стопа
_STOP_KEY_RE = re.compile(r"^error EDGE-CHECK\(([^/)]+)/([^)]+)\): (R\d+) ")

Key = tuple[str, str, str]
Revise = Callable[..., LaunchOutcome]


class Outcome(enum.Enum):
    """Итог разбора: что делать вызывающему."""

    REVISED = "revised"  # узел переписан — повторить шаг
    OVERRIDDEN = "overridden"  # все замечания приняты вопреки — повторить шаг
    STOPPED = "stopped"  # человек остановился или сбой — прогон стоит
    WAITING = "waiting"  # вопрос записан (`pending.json`), прогон ждёт
    NOT_APPLICABLE = "not_applicable"  # разбирать нечего — прежний стоп


@dataclass
class PaidCalls:
    """Платные вызовы за `dfl run`: сортировка (без кэша), переписывание,
    повторная проверка (считает вызывающий — по шагу после круга);
    `author` — авторинг узла (личный путь; волновой путь его не считает,
    и его строка прежняя)."""

    triage: int = 0
    revise: int = 0
    check: int = 0
    author: int = 0

    def spent(self) -> bool:
        """Был ли хоть один платный вызов."""
        return bool(self.triage or self.revise or self.check or self.author)

    def line(self) -> str:
        """Строка для человека."""
        head = i18n.t("авторинг {n}, ").format(n=self.author) if self.author else ""
        return i18n.t(
            "платных вызовов: {head}сортировка {triage}, переписывание "
            "{revise}, проверка {check}"
        ).format(head=head, triage=self.triage, revise=self.revise, check=self.check)


@dataclass(frozen=True)
class Scope:
    """Где разбирать замечания — без `RunState` (волновой и личный путь).

    `run_dir` — каталог прогона (журнал, записи рёбер, копии файлов;
    `run_dir.name` — `run_id` для границы автора); `target_dir`/`bundle_dir`
    — чекаут цели и бандл в нём: файлы узлов и оснований читаются и
    переписываются в рабочем дереве; `wave` — ключ журнала; `can_override`
    — можно ли предлагать «принять вопреки» (`test`); `filenames` — вид узла
    → имя файла (узлы находок и основания рёбер); `subject` — тема для
    промпта переписывания; `lang` — язык прогона: каталог правил
    (`lite.personal.check.contracts_dir`), промпт сортировки и подписи
    решений в промпте переписывания.
    """

    run_dir: Path
    target_dir: str
    bundle_dir: str
    wave: int
    can_override: bool
    filenames: Mapping[str, str]
    subject: str = ""
    #: без терминала: вопрос через `dfl reply` (`pending.asker`)
    ask: pending.Ask | None = None
    #: партия ожидающего круга и записанный вопрос (`Waiting`)
    waiting: Waiting | None = None
    lang: str = "ru"


@dataclass
class Waiting:
    """Ожидающий круг: `batch` — партия его ответов (продолжение после
    `dfl reply` дописывает ту же; пусто — новая), `question` — вопрос,
    записанный в `pending.json` (`Outcome.WAITING`)."""

    batch: str = ""
    question: pending.Question | None = None


def wait_load(run_dir: Path, slot: str) -> dict | None:
    """Ожидающий круг слота (`WAIT_FILE`) или `None` (нет, битый)."""
    data = _wait_read(run_dir)
    saved = data.get(slot)
    return saved if isinstance(saved, dict) else None


def wait_save(run_dir: Path, slot: str, data: dict) -> None:
    """Запомнить ожидающий круг слота: вопрос ушёл в `pending.json`."""
    _wait_write(run_dir, {**_wait_read(run_dir), slot: data})


def wait_clear(run_dir: Path, slot: str) -> None:
    """Снять ожидающий круг слота; файла нет — ничего не пишется."""
    data = _wait_read(run_dir)
    if slot not in data:
        return
    del data[slot]
    if data:
        _wait_write(run_dir, data)
    else:
        (run_dir / WAIT_FILE).unlink(missing_ok=True)


def findings_json(findings: Iterable[ej.Finding]) -> list[dict]:
    """Находки для `WAIT_FILE`."""
    return [asdict(f) for f in findings]


def findings_from_json(raw: Iterable[Mapping]) -> list[ej.Finding]:
    """Находки из `WAIT_FILE` (`findings_json`); не по форме — `TypeError`
    или `KeyError`."""
    return [ej.Finding(**{**r, "subject": tuple(r.get("subject") or ())}) for r in raw]


def _wait_read(run_dir: Path) -> dict:
    try:
        data = json.loads((run_dir / WAIT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _wait_write(run_dir: Path, data: dict) -> None:
    path = run_dir / WAIT_FILE
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


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
    ask: pending.Ask | None = None,
    waiting: Waiting | None = None,
) -> Outcome:
    """Один круг разбора замечаний волны: сортировка, вопросы, переписывание.

    `revise(call, kind, subject, current, findings, decisions)` — граница
    автора (`RealOps.revise`); `round_no` — номер круга (на `MAX_ROUNDS`
    в `test` предлагается принять узел вопреки замечанию); `calls` — счётчик
    платных вызовов вызывающего; `ask`/`waiting` — без терминала (`Scope`).
    """
    findings = applicable_findings(state)
    if findings is None:
        return Outcome.NOT_APPLICABLE
    scope = Scope(
        run_dir=rs.run_dir(state.run_id),
        target_dir=state.target_dir,
        bundle_dir=state.bundle_dir,
        wave=state.wave,
        can_override=state.mode == "test",
        filenames=_AUTHOR_FILENAMES,
        subject=state.subject,
        ask=ask,
        waiting=waiting,
    )
    return resolve_scope(
        scope,
        findings,
        read=read,
        agent=agent,
        revise=revise,
        round_no=round_no,
        calls=calls,
    )


def resolve_scope(
    scope: Scope,
    findings: list[ej.Finding],
    *,
    read: Read,
    agent: Agent,
    revise: Revise,
    round_no: int,
    calls: PaidCalls | None = None,
) -> Outcome:
    """Ядро круга разбора по готовым находкам `findings` (без `RunState`).

    Находки отбирает вызывающий (волна — `applicable_findings`); пусто или
    файл находки — не узел её вида по `scope.filenames` (§9 п.4) — разбирать
    нечего. Остальное — как `resolve`.

    Без терминала (`scope.ask`) вопрос без ответа — `Outcome.WAITING`,
    вопрос — в `scope.waiting.question`. Ответы партии `scope.waiting.batch`,
    данные до вопроса, не спрашиваются снова, а сортировка видит журнал
    без них — тот же вход, что в начале круга (кэш, не платный вызов).
    """
    if not findings or not _nodes_match(findings, scope.filenames):
        return Outcome.NOT_APPLICABLE
    calls = calls if calls is not None else PaidCalls()
    try:
        nodes = _read_files(scope, dict.fromkeys(f.path for f in findings if f.path))
        bases = _bases(scope, findings)
    except _Unreadable as exc:
        say(i18n.t(UNRESOLVABLE).format(exc=exc))
        return Outcome.NOT_APPLICABLE
    run_dir = scope.run_dir
    waiting = scope.waiting if scope.waiting is not None else Waiting()
    waiting.batch = waiting.batch or uuid.uuid4().hex
    journal = ej.load(run_dir, scope.wave)
    prior = [d for d in journal if d.batch != waiting.batch]
    answered = [d for d in journal if d.batch == waiting.batch]
    try:
        records = ej.level_records(run_dir, scope.wave)
    except (OSError, ValueError, KeyError, TypeError):
        return Outcome.NOT_APPLICABLE
    refusal = override.non_overridable_reason(records)
    try:
        items = triage(
            findings,
            nodes,
            bases,
            prior,
            _counted(agent, calls),
            cache_dir=run_dir,
            lang=scope.lang,
        )
    except (FormError, AgentUnavailable) as exc:
        say(i18n.t(TRIAGE_FAILED).format(exc=exc))
        return Outcome.STOPPED
    notes: set[str] = set()
    visible = [_visible(i) for i in items]
    asked = _Asked(read, scope.ask, waiting, answered)
    chosen = _ask_all(scope, visible, prior, asked, round_no, refusal, notes)
    if isinstance(chosen, Outcome):
        return chosen
    overrides = ej.overridden(run_dir, scope.wave)
    if {f.digest for f in findings} <= overrides:
        if override.accepted(records, overrides) is not None:
            say(i18n.t(OVERRIDDEN_LINE))
            return Outcome.OVERRIDDEN
        _note(notes, refusal or i18n.t(NOT_COVERED))
        return Outcome.STOPPED
    return _rewrite(scope, findings, nodes, revise, round_no, calls, overrides)


def override_offer(n: int, rule: str) -> str:
    """Вариант «принять вопреки»: одна находка — прежняя строка; несколько
    находок одного правила на том же тексте (общий digest) — все сразу,
    перекрывая ответы по ним."""
    if n == 1:
        return i18n.t(OVERRIDE_OFFER)
    return i18n.t(OVERRIDE_GROUP).format(rule=rule, n=n)


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
    if not _nodes_match(findings, _AUTHOR_FILENAMES):
        return None
    return findings


def _nodes_match(findings: list[ej.Finding], filenames: Mapping[str, str]) -> bool:
    """У каждой находки есть файл, и это файл узла её вида."""
    return all(f.path and f.path == filenames.get(f.node) for f in findings)


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


def _target_root(scope: Scope) -> Path:
    """Канонический корень чекаута цели (требование `safe_io`)."""
    return Path(os.path.realpath(scope.target_dir))


def _rel(scope: Scope, name: str) -> str:
    return f"{scope.bundle_dir.strip('/')}/{name}"


def _read_node(scope: Scope, name: str) -> str:
    """Текст файла бандла через `safe_io`; отказ — `_Unreadable`."""
    try:
        data = safe_io.read_regular(
            _target_root(scope), _rel(scope, name), limit=NODE_LIMIT
        )
        return data.decode("utf-8")
    except (safe_io.SafeIOError, UnicodeDecodeError) as exc:
        raise _Unreadable(f"{name}: {exc}") from exc


def _read_files(scope: Scope, names: Iterable[str]) -> dict[str, str]:
    """Текущие тексты файлов узлов (с ручными правками)."""
    return {n: _read_node(scope, n) for n in names}


def _bases(scope: Scope, findings: list[ej.Finding]) -> dict[str, str]:
    """Тексты оснований рёбер (`basis_roles`); роли без файла и отсутствующие
    файлы пропускаются, файл, который нельзя прочитать безопасно, — отказ."""
    out: dict[str, str] = {}
    for edge in dict.fromkeys(f.edge for f in findings):
        try:
            roles = load_rules(edge, contracts_dir(scope.lang)).basis_roles
        except EdgeCheckError:
            continue
        for role in roles:
            name = scope.filenames.get(role)
            if name is None or name in out:
                continue
            if not os.path.lexists(_target_root(scope) / _rel(scope, name)):
                continue
            out[name] = _read_node(scope, name)
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


@dataclass
class _Asked:
    """Как спрашивать в этом круге: `read` — терминал, `ask` — без него
    (`dfl reply`); `waiting` — партия круга; `answered` — ответы партии,
    данные до записанного вопроса (берутся по порядку, не спрашиваются)."""

    read: Read
    ask: pending.Ask | None
    waiting: Waiting
    answered: list[ej.Decision]

    def replay(self, key: Key) -> ej.Decision | None:
        """Ответ партии на очередной вопрос с ключом `key` или `None`."""
        if self.answered and self.answered[0].key == key:
            return self.answered.pop(0)
        self.answered.clear()  # круг разошёлся с записанным — спросить
        return None


def _ask_all(
    scope: Scope,
    items: list[Item],
    prior: list[ej.Decision],
    asked: _Asked,
    round_no: int,
    refusal: str | None,
    notes: set[str],
) -> list[ej.Decision] | Outcome:
    """Вопросы по `decision` и по тем, что журнал велит спросить снова
    (`edge_memory.recall`); каждое решение сразу в журнал. Человек
    остановился — `Outcome.STOPPED`, вопрос ушёл в `pending.json` —
    `Outcome.WAITING` (записанное остаётся).

    Принятие вопреки снимает блокирующий вердикт правила на тексте узла —
    всю группу находок с общим digest: вариант добавляется к вопросу каждого
    пункта группы, а выбранный — снимает остальные её вопросы. Уровень
    нельзя принять (`refusal`) — варианта нет, причина сказана один раз.
    """
    memory = em.recall(
        prior,
        [i.finding for i in items],
        test=scope.can_override,
        last_round=round_no == MAX_ROUNDS,
    )
    questions = _questions(items, memory, refusal is None)
    if refusal is not None and any(
        memory[q.item.finding.key].offer_override for q in questions
    ):
        _note(notes, refusal)
    say(i18n.t(FIXING).format(n=len(items) - len(questions), m=len(questions)))
    accepted_groups: set[str] = set()
    chosen: list[ej.Decision] = []
    for q in questions:
        if q.group in accepted_groups:
            continue
        question = memory[q.item.finding.key].question or q.item.question
        decision = asked.replay(q.item.finding.key)
        if decision is None:
            got = _ask(scope.run_dir, q, question, asked, round_no)
            if got is Outcome.STOPPED:
                say(i18n.t(STOPPED_LINE))
            if isinstance(got, Outcome):
                return got
            decision = replace(got, batch=asked.waiting.batch)
            ej.append(scope.run_dir, scope.wave, decision)
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


def edge_question(q: _Question, question: str) -> pending.Question:
    """Вопрос для `pending.json`: текст — то, что `_ask` печатает до
    вариантов; варианты — те же строки, «принять вопреки» последним (номер
    ответа сверяется по ним и разбирается `_parse`)."""
    item = q.item
    options = tuple(_option_line(o) for o in item.options)
    return pending.Question(
        kind=pending.EDGE,
        key="/".join(item.finding.key),
        text="\n".join(_head_lines(q, question)),
        options=options + ((q.offer,) if q.offer is not None else ()),
        accepts=i18n.t(REMOTE_HELP),
    )


def _head_lines(q: _Question, question: str) -> list[str]:
    """Замечание, пункты группы, которые исправлю сам, и вопрос."""
    f = q.item.finding
    same = i18n.t(SAME_RULE_LINE)
    listed = [
        same.format(where=_where(one.finding), statement=one.finding.statement)
        for one in q.listed
    ]
    head = i18n.t(FINDING_LINE).format(where=_where(f), statement=f.statement)
    return [head, *listed, question]


def _ask(
    run_dir: Path, q: _Question, question: str, asked: _Asked, round_no: int
) -> ej.Decision | Outcome:
    """Один вопрос; непонятый ответ — переспросить один раз, затем стоп.

    Без терминала (`asked.ask`) вопрос без ответа — `Outcome.WAITING`
    (`asked.waiting.question`). В терминале сначала берётся ответ `dfl reply`
    на записанный вопрос, иначе живой ввод; живой ответ снимает вопрос.
    """
    item, offer = q.item, q.offer
    remote = edge_question(q, question)
    if asked.ask is not None:
        return _ask_remote(q, question, remote, asked.ask, asked.waiting, round_no)
    for line in _head_lines(q, question):
        say(line)
    for no, option in enumerate(item.options, 1):
        say(f"  {no}. {_option_line(option)}")
    if offer is not None:
        say(f"  {len(item.options) + 1}. {offer}")
    say(i18n.t(ANSWER_HELP))
    recorded = pending.take(run_dir, remote)
    if recorded is not None:
        decision = _parse(item, question, recorded, offer, round_no)
        if decision is not None:
            return decision
    for attempt in range(2):
        text = read_answer(asked.read, quick=_quick)
        if text is not None:
            pending.discard(run_dir)  # терминал победил
        if text is None or is_skip(text):
            return Outcome.STOPPED
        decision = _parse(item, question, text, offer, round_no)
        if decision is not None:
            return decision
        if attempt == 0:
            say(
                i18n.t(NO_REASON)
                if _starts_any(text, DISAGREE_PREFIXES)
                else i18n.t(REASK)
            )
    return Outcome.STOPPED


def _ask_remote(
    q: _Question,
    question: str,
    remote: pending.Question,
    ask: pending.Ask,
    waiting: Waiting,
    round_no: int,
) -> ej.Decision | Outcome:
    """Ответ через `dfl reply`; нет ответа — `Outcome.WAITING`. Ответ не
    разобран (`dfl reply` его проверил, но круг мог измениться) — вопрос
    записывается снова."""
    text = ask(remote)
    if text is not None:
        decision = _parse(q.item, question, text, q.offer, round_no)
        if decision is not None:
            return decision
        ask(remote)  # ответ взят и снят: записать вопрос снова
    waiting.question = remote
    return Outcome.WAITING


def _quick(line: str) -> bool:
    """Ответ из одной строки: номер варианта или «пропустить»."""
    return line.isdigit() or is_skip(line)


def _option_line(o: Option) -> str:
    """Вариант с эффектами: что удалит и что понизит — видно до выбора."""
    effects = (
        [i18n.t(REMOVES).format(ids=", ".join(o.removes))] if o.removes else []
    ) + (
        [i18n.t(DOWNGRADES).format(ids=", ".join(o.downgrades))] if o.downgrades else []
    )
    tail = f", {'; '.join(effects)}" if effects else ""
    return f"{o.label}{tail}{i18n.t(RECOMMENDED) if o.recommended else ''}"


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
    for prefixes, kind in ((OWN_PREFIXES, "own"), (DISAGREE_PREFIXES, "disagree")):
        for prefix in prefixes:
            if _starts(text, prefix):
                rest = text[len(prefix) :].strip()
                return made(rest, kind, {}) if rest else None
    return None


def _fold(text: str) -> str:
    """Для сравнения: регистр не важен, «ё» = «е» (длина строки та же)."""
    return text.casefold().replace("ё", "е")


def _starts(text: str, prefix: str) -> bool:
    return _fold(text.strip()).startswith(_fold(prefix))


def _starts_any(text: str, prefixes: tuple[str, ...]) -> bool:
    return any(_starts(text, prefix) for prefix in prefixes)


def _where(f: ej.Finding) -> str:
    return f"{f.rule_id} {f.cls} ({f.path}:{f.lines})"


def _rewrite(
    scope: Scope,
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
    run_dir = scope.run_dir
    todo = [f for f in findings if f.digest not in skipped]
    journal = ej.load(run_dir, scope.wave)
    done: list[str] = []
    try:
        for name in dict.fromkeys(f.path for f in todo if f.path):
            mine = [f for f in todo if f.path == name]
            _backup(run_dir, round_no, name, nodes[name])
            done.append(name)
            calls.revise += 1
            reason = _revise_file(scope, name, nodes[name], mine, journal, revise)
            if reason is None:
                reason = _weakened(scope, name, nodes[name], _allows(journal, mine))
            if reason is not None:
                say(reason)
                _restore(scope, nodes, done)
                return Outcome.STOPPED
    except BaseException:
        _restore(scope, nodes, done)
        raise
    for f in todo:
        say(i18n.t(FIXED_LINE).format(where=_where(f), statement=f.statement), 1)
    say(i18n.t(REWRITTEN).format(names=", ".join(done), n=len(todo)))
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


def _restore(scope: Scope, nodes: dict[str, str], names: list[str]) -> None:
    """Вернуть тексты файлов круга; не вернувшиеся — назвать."""
    failed: list[str] = []
    for name in names:
        try:
            safe_io.publish(
                _target_root(scope), _rel(scope, name), nodes[name].encode("utf-8")
            )
        except safe_io.SafeIOError:
            failed.append(name)
    if failed:
        say(
            i18n.t(NOT_RESTORED).format(
                names=", ".join(failed), path=scope.run_dir / REVISE_DIR
            )
        )


def _revise_file(
    scope: Scope,
    name: str,
    current: str,
    findings: list[ej.Finding],
    journal: list[ej.Decision],
    revise: Revise,
) -> str | None:
    """Вызов автора по файлу; отказ — причина для человека, иначе `None`."""
    call = AuthorCall(
        run_id=scope.run_dir.name,
        run_dir=os.path.realpath(scope.run_dir),
        target_dir=scope.target_dir,
        bundle_dir=scope.bundle_dir,
        filename=name,
    )
    keys = {f.key for f in findings}
    decisions = "\n".join(
        _decision_line(d, scope.lang) for d in journal if d.key in keys
    )
    outcome = revise(
        call,
        findings[0].node,
        scope.subject,
        current,
        "\n".join(f"- {_where(f)}: {f.statement}" for f in findings),
        decisions or _NO_DECISIONS[scope.lang],
    )
    if outcome.code != 0:
        why = outcome.reason or outcome.code
        return i18n.t(REVISE_FAILED).format(name=name, why=why)
    return None


def _decision_line(d: ej.Decision, lang: str = "ru") -> str:
    """Решение дословно: «вопрос → ответ»."""
    return f"- {d.question} → {_KIND_LABELS[lang].get(d.kind, '')}{d.answer}"


def _weakened(
    scope: Scope, name: str, before: str, allows: tuple[set[str], set[str]]
) -> str | None:
    """Сверка до/после (§5); ослаблен или не читается — причина, иначе `None`."""
    try:
        after = _read_node(scope, name)
    except _Unreadable as exc:
        return i18n.t(UNREADABLE_AFTER).format(exc=exc)
    bad = node_guard.weakened(before, after, *allows)
    if not bad:
        return None
    return i18n.t(WEAKENED).format(name=name, ids=", ".join(bad))
