"""Единственный исполнитель записей (спека среза 1, §5; О §5.0).

Перед КАЖДОЙ мутацией — проверки §5.1 в фиксированном порядке; исход —
по матрице §5.2. Неопределённость и сбой блокируют зависимые шаги той же
записи плана (шаг `optional` — нет), независимые записи продолжаются. Тень —
всё, кроме отправки. Полномочия (О §2.4) — по свежему роадмапу перед каждым
шагом: потолок прогона `min(level_cap, autonomy)` и уровень позиции записи.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from conductor.gh_app import Blocked, CallResult, JournalLost
from conductor.gh_write import (
    KLASS,
    Mutation,
    TargetCheck,
    check_target,
    request_of,
    verify,
)
from conductor.host_config import HostConfig
from conductor.journal import MutationLog, RunJournal
from conductor.markers import h1, parse_body, render
from conductor.opstate import OpState, parse_ts
from conductor.roadmap import Roadmap

WRITER_MARGIN = timedelta(minutes=65)
CONTINUE = frozenset({"success", "shadow"})


class Client(Protocol):
    """То, что исполнителю нужно от клиента App."""

    bot_login: str | None

    def check_key(self) -> str | None: ...

    def covers(self, repo: str) -> bool | None: ...

    def call(
        self,
        klass: str,
        method: str,
        path: str,
        *,
        auth: Any,
        body: dict | None = None,
        graphql: bool = False,
    ) -> CallResult: ...


@dataclass(frozen=True)
class Step:
    """Шаг записи: мутация и ключ события (ключ маркера или имя операции).

    revision — значимая ревизия шага (§5.4), если она уже записи: вопросы
    одной очереди — разные мутации со своими сериями. optional — сбой или
    неопределённость шага не блокируют следующие (закрепление очереди, §6.1).
    authority — уровень позиции шага, если он не уровень записи (вопрос
    очереди о своём субъекте).
    """

    mutation: Mutation
    event_key: str
    revision: str = ""
    optional: bool = False
    authority: Callable[[Roadmap, int], int] | None = field(default=None, compare=False)


def _valid(m: Mutation) -> str | None:
    return None


def _run_level(roadmap: Roadmap, run_level: int) -> int:
    return run_level


@dataclass(frozen=True)
class PlanRecord:
    """Запись плана (О §5.0): действие над субъектом одной ревизии.

    level — уровень, которого требует действие (все действия среза — 1).
    authority(roadmap, run_level) — уровень позиции по СВЕЖЕМУ роадмапу
    (О §2.4: класс, фокус, `focus.autonomy`); по умолчанию — потолок прогона.
    revalidate(m) — шаг 8 §5.1: свежее чтение основания перед мутацией m;
    None — основание в силе, иначе причина снятия.
    """

    action: str
    subject: str
    revision: str
    level: int
    steps: tuple[Step, ...]
    revalidate: Callable[[Mutation], str | None] = field(default=_valid, compare=False)
    authority: Callable[[Roadmap, int], int] = field(default=_run_level, compare=False)


@dataclass(frozen=True)
class StepReport:
    """Исход шага для журнала и выдачи."""

    action: str
    subject: str
    op: str
    target: str
    outcome: str
    reason: str = ""
    admissible: bool | None = None
    allowed: bool | None = None


class StopPoint(Exception):
    """Именованная точка остановки профиля acceptance (§9.2)."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


def mutation_id(rec: PlanRecord, step: Step, m: Mutation) -> str:
    """Ключ попытки, учёта и серии: с действием, субъектом и ревизией."""
    return h1(
        "mutation",
        rec.action,
        rec.subject,
        step.revision or rec.revision,
        m.op,
        m.target(),
        step.event_key,
    )


def effect_key(step: Step, m: Mutation) -> str:
    """Ключ задержки повтора: только то, что останется в GitHub (§5.4)."""
    return h1("effect", m.op, m.target(), step.event_key)


def expected_of(m: Mutation) -> str:
    """Идентичность эффекта для поиска после обрыва (§5.3): маркер
    комментария или sha256 тела; для закрытия и закрепления — состояние."""
    if m.op == "comment":
        marker = parse_body(m.text)
        return render(marker) if marker is not None else ""
    if m.op in ("body", "create"):
        return hashlib.sha256(m.text.encode("utf-8")).hexdigest()
    return m.op


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Writer:
    """Проводит записи плана через §5.1 и пишет исходы."""

    def __init__(
        self,
        *,
        cfg: HostConfig,
        client: Client,
        state: OpState,
        log: MutationLog,
        journal: RunJournal,
        fence: frozenset[str],
        load_roadmap: Callable[[], Roadmap | None],
        hostname: str,
        run_id: str,
        level_cap: int = 0,
        partial: bool = False,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._cfg = cfg
        self._client = client
        self._state = state
        self._log = log
        self._journal = journal
        self._fence = fence
        self._load_roadmap = load_roadmap
        self._hostname = hostname
        self._run_id = run_id
        self._level_cap = level_cap
        self._partial = partial
        self._clock = clock
        self.sent = 0
        self.stopped: str | None = None
        self.revoked: set[str] = set()
        self.created: dict[str, int] = {}

    def execute(self, records: list[PlanRecord]) -> list[StepReport]:
        """Все записи плана; StopPoint пробрасывается (имитация обрыва)."""
        reports: list[StepReport] = []
        for rec in records:
            reports += self._record(rec)
        self._state.end_run(self._clock())
        self._log.end_run()
        return reports

    def _record(self, rec: PlanRecord) -> list[StepReport]:
        out: list[StepReport] = []
        blocked_by = ""
        for step in rec.steps:
            if self.stopped:
                rep = self._rep(rec, step.mutation, "revoked_all", self.stopped)
            elif blocked_by:
                rep = self._rep(rec, step.mutation, "skipped_dependent", blocked_by)
            elif rec.action in self.revoked:
                rep = self._rep(rec, step.mutation, "revoked_action", "enabled_actions")
            else:
                rep = self._step(rec, step)
                if rep.outcome not in CONTINUE and not step.optional:
                    blocked_by = rep.outcome
            self._journal.write(asdict(rep))
            out.append(rep)
        return out

    def _permission(self, rec: PlanRecord, step: Step, roadmap: Roadmap) -> str | None:
        """Шаг 6 по свежему роадмапу: имя первого нарушенного условия."""
        if self._partial:
            return "partial"
        if self._level_cap < rec.level:  # --level, --roadmap, --replay → 0
            return "run_level"
        run_level = min(self._level_cap, roadmap.autonomy)
        if run_level < rec.level:
            return "autonomy"
        if rec.action not in roadmap.enabled_actions:
            return "enabled_actions"
        if (step.authority or rec.authority)(roadmap, run_level) < rec.level:
            return "position_level"
        return None

    def _step(self, rec: PlanRecord, step: Step) -> StepReport:
        m = self._resolve(step.mutation)
        if m is None:
            return self._rep(
                rec, step.mutation, "skipped_dependent", "нет созданного объекта"
            )
        now = self._clock()
        if m.repo not in self._fence:  # 1 — до покрытия
            return self._rep(rec, m, "fence", "FENCE", admissible=False)
        if self._state.quarantined(now):  # 2
            return self._stop_all(rec, m, "карантин")
        roadmap = self._load_roadmap()  # 3
        if roadmap is None or not roadmap.valid:
            return self._stop_all(rec, m, "роадмап не прочитан или RM-INVALID")
        if (  # 4
            roadmap.writer_host != self._hostname
            or now < parse_ts(roadmap.writer_since) + WRITER_MARGIN
        ):
            return self._stop_all(rec, m, "не управляющий писатель")
        denied: str | None = None
        try:
            if self._client.check_key() is None:  # 5
                return self._stop_all(rec, m, "ключ App не подтверждён")
            denied = self._permission(rec, step, roadmap)  # 6
            if denied is not None and not self._cfg.shadow:
                if denied == "enabled_actions":
                    self.revoked.add(rec.action)
                    return self._rep(rec, m, "revoked_action", denied)
                if denied == "position_level":  # только эта запись
                    return self._rep(rec, m, "revoked_action", denied)
                return self._stop_all(rec, m, denied)
            covered = self._client.covers(m.repo)  # 7
            if covered is not True:
                reason = (
                    "ID-REPO-UNCOVERED" if covered is False else "покрытие не прочитано"
                )
                return self._rep(rec, m, "not_sent", reason, admissible=False)
            changed = rec.revalidate(m)  # 8
            if changed is not None:
                return self._rep(rec, m, "removed", changed, admissible=False)
            target = check_target(self._client, m)
        except (Blocked, JournalLost) as exc:  # 8a
            return self._stop_all(rec, m, f"запрет или журнал установки: {exc}")
        if not target.ok:
            return self._rep(rec, m, "removed", target.reason, admissible=False)
        mid, eff = mutation_id(rec, step, m), effect_key(step, m)
        if self._state.delayed(eff, now):  # 9
            self._state.series_event(mid, "delay", self._run_id, now)
            return self._rep(
                rec,
                m,
                "delay",
                "неопределённая попытка моложе 60 мин",
                admissible=False,
            )
        if self.sent >= roadmap.limits["max_writes_per_run"]:  # 10
            return self._stop_all(rec, m, "RUN-BUDGET")
        if self._cfg.shadow:
            return self._rep(
                rec, m, "shadow", denied or "", admissible=True, allowed=denied is None
            )
        return self._send(rec, step, m, target, mid, eff)

    def _send(
        self,
        rec: PlanRecord,
        step: Step,
        m: Mutation,
        target: TargetCheck,
        mid: str,
        eff: str,
    ) -> StepReport:
        if m.op == "close":
            self._stop_point("before_close")
        method, path, body, graphql = request_of(m, target.node_id)
        attempt_id = uuid.uuid4().hex
        try:
            self._state.begin_attempt(
                attempt_id=attempt_id,
                mutation_id=mid,
                effect_key=eff,
                target=m.target(),
                marker_key=step.event_key,
                action=rec.action,
                subject=rec.subject,
                now=self._clock(),
                op=m.op,
                expected=expected_of(m),
            )
            seq = self._log.intent(
                attempt_id=attempt_id,
                method=method,
                endpoint=path,
                repo=m.repo,
                marker_key=step.event_key,
                body=json.dumps(body).encode("utf-8"),
            )
        except OSError:
            return self._stop_all(rec, m, "OPSTATE-WRITE")
        self._stop_point("after_intent")
        try:
            result = self._client.call(
                KLASS[m.op], method, path, auth="token", body=body, graphql=graphql
            )
        except Blocked as exc:
            return self._abort(
                rec, m, attempt_id, seq, "not_sent", False, f"не отправлено: {exc}"
            )
        except JournalLost:
            self.sent += 1
            return self._abort(
                rec,
                m,
                attempt_id,
                seq,
                "uncertain",
                True,
                "журнал установки не записан",
            )
        self.sent += 1
        self._stop_point("after_send")
        return self._settle(rec, m, result, attempt_id, seq, mid, target.node_id)

    def _settle(
        self,
        rec: PlanRecord,
        m: Mutation,
        result: CallResult,
        attempt_id: str,
        seq: int,
        mid: str,
        node_id: str | None,
    ) -> StepReport:
        outcome, reason = result.outcome, ""
        if outcome == "ok":
            try:  # verify — независимое чтение цели (О §5.0 шаг 3)
                problem = verify(
                    self._client,
                    m,
                    result.response,
                    self._client.bot_login or "",
                    node_id,
                )
            except (Blocked, JournalLost) as exc:
                problem = f"контрольное чтение не выполнено: {exc}"
            if problem is not None:
                outcome, reason = "uncertain", problem
        if outcome == "moved":
            outcome, reason = "failed", "TARGET-MOVED"
        status = result.response.status if result.response is not None else None
        try:
            self._state.finish_attempt(attempt_id, outcome, self._clock())
            self._log.result(seq, sent=True, outcome=outcome, status=status)
            self._state.series_event(
                mid,
                outcome,
                self._run_id,
                self._clock(),
                error=reason or str(status),
                detail={"op": m.op, "target": m.target()},
            )
        except OSError:
            return self._stop_all(rec, m, "OPSTATE-WRITE")
        if outcome == "rate_limited":
            return self._stop_all(rec, m, "лимит")
        if outcome != "ok":
            return self._rep(rec, m, outcome, reason)
        if m.op == "create" and result.response is not None:
            self.created[m.repo] = result.response.json()["number"]
        report = self._rep(rec, m, "success")
        if m.op == "comment":
            self._stop_point("after_comment")
        return report

    def _abort(
        self,
        rec: PlanRecord,
        m: Mutation,
        attempt_id: str,
        seq: int,
        outcome: str,
        sent: bool,
        reason: str,
    ) -> StepReport:
        try:
            self._state.finish_attempt(attempt_id, outcome, self._clock())
            self._log.result(seq, sent=sent, outcome=outcome, status=None)
        except OSError:
            reason = f"{reason}; OPSTATE-WRITE"
        return self._stop_all(rec, m, reason)

    def _resolve(self, m: Mutation) -> Mutation | None:
        if m.number is not None or m.op == "create":
            return m
        number = self.created.get(m.repo)
        return replace(m, number=number) if number is not None else None

    def _stop_all(self, rec: PlanRecord, m: Mutation, reason: str) -> StepReport:
        self.stopped = reason
        return self._rep(rec, m, "revoked_all", reason, admissible=False)

    def _stop_point(self, name: str) -> None:
        if self._cfg.profile == "acceptance" and name in self._cfg.stop_points:
            self._journal.write({"stop_point": name})
            raise StopPoint(name)

    def _rep(
        self,
        rec: PlanRecord,
        m: Mutation,
        outcome: str,
        reason: str = "",
        admissible: bool | None = None,
        allowed: bool | None = None,
    ) -> StepReport:
        return StepReport(
            rec.action,
            rec.subject,
            m.op,
            m.target(),
            outcome,
            reason,
            admissible,
            allowed,
        )
