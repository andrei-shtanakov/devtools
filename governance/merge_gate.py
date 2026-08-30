"""Право мержа: чистая конъюнкция осей (спека §6) и деградаций (§8).

Порядок проверок = порядок причин в вердикте: сначала то, что «нельзя никому»
(refuse), затем то, что оставляет PR человеку (human), и только при полностью
зелёном наборе — agent. Функция не ходит в сеть и не читает диск.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Authority:
    """Ось 1 (ADR-ECO-011): каждый уровень может только ужесточить до human."""

    ecosystem: str = "agent"
    repo: str | None = None
    run: str | None = None

    def effective(self) -> str:
        if "human" in (self.ecosystem, self.repo, self.run):
            return "human"
        return "agent"


@dataclass(frozen=True)
class Safety:
    """Ось 2: срез steward-политики из вендоренной копии; None = unknown."""

    agent_merge_allowed: bool | None
    actor_class: str


@dataclass(frozen=True)
class PrFacts:
    checks_rollup: str
    mergeable: str
    behind_base: bool
    unresolved_threads: bool
    diff_class: str
    touches_authority_root: bool


@dataclass(frozen=True)
class MergeVerdict:
    decision: str
    reason: str


def decide(
    authority: Authority,
    safety: Safety,
    review_exit: int | None,
    facts: PrFacts,
) -> MergeVerdict:
    """Вердикт agent | human | refuse; причина обязательна (спека §4)."""
    if facts.checks_rollup == "red":
        return MergeVerdict("refuse", "красный rollup обязательных проверок")

    if facts.touches_authority_root:
        return MergeVerdict(
            "human", "дифф затрагивает authority-root пути (ADR-ECO-004 I2)"
        )
    if authority.effective() == "human":
        return MergeVerdict("human", "requested authority ужесточена до human")
    if safety.agent_merge_allowed is not True:
        return MergeVerdict(
            "human",
            "safety-гейт: agent_merge_allowed не поднят или копия недоступна",
        )
    if safety.actor_class != "agent":
        return MergeVerdict(
            "human", f"актор класса {safety.actor_class!r} — fail-closed"
        )
    if review_exit != 0:
        return MergeVerdict(
            "human", f"ревью не дало явного approve (exit={review_exit!r})"
        )
    if facts.diff_class != "document":
        return MergeVerdict(
            "human", f"дифф класса {facts.diff_class!r} — предохранитель runner'а"
        )
    if facts.checks_rollup != "green":
        return MergeVerdict(
            "human", f"rollup {facts.checks_rollup!r} не читается как зелёный"
        )
    if facts.mergeable != "mergeable":
        return MergeVerdict("human", f"mergeable={facts.mergeable!r}")
    if facts.behind_base:
        return MergeVerdict("human", "PR отстал от base — нужен update-branch")
    if facts.unresolved_threads:
        return MergeVerdict("human", "есть неразрешённые review threads")
    return MergeVerdict("agent", "все оси зелёные: authority+safety+review+facts")
