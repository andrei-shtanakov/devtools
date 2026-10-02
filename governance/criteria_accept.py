"""criteria_accept — приёмка закрытия `traced`, срез 2a (спека §7.2a).

Чистые решения без побочных эффектов: текст предложения и штампа (§3.4),
предикат перехода в `stamping` по собранным фактам форджи (§3.5) и проверка
штампа (§3.2). Факты собирает и действия выполняет `criteria_close`.

Правило фактов (как в `approval_facts`): в `rejected`/`superseded` переводит
только положительно установленный факт; `None` — «не установлено» и даёт
`unavailable` (отказ шага без перехода), а не терминал.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from governance.frontmatter import join_frontmatter, split_frontmatter

STATUS_PROPOSED = "proposed"
STATUS_ACCEPTED = "accepted"
TERMINAL = ("rejected", "superseded")
EXIT_WAITING_HUMAN = 4
EXIT_TERMINAL = 5
LABEL = "criteria-close"
HUMAN_LABELS = "criteria-close,human-merge-required"


def text_sha256(text: str) -> str:
    """sha256 текста файла (UTF-8) — идентичность предложения и штампа."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def proposal_text(closure_text: str, policy_source: str) -> str:
    """Файл закрытия + `status: proposed` и источник снимка политики (§7.2a п.1)."""
    meta, body = split_frontmatter(closure_text)
    return join_frontmatter(
        {**meta, "status": STATUS_PROPOSED, "policy_source": policy_source}, body
    )


def stamp_text(proposal: str, *, merge_oid: str, pr: int) -> str:
    """Штамп: ровно предложение со `status: accepted` и ссылкой на мерж (§3.2)."""
    meta, body = split_frontmatter(proposal)
    if meta.get("status") != STATUS_PROPOSED:
        raise ValueError(
            f"штамп выпускается из предложения, а не из {meta.get('status')!r}"
        )
    return join_frontmatter(
        {
            **meta,
            "status": STATUS_ACCEPTED,
            "accepted_merge": merge_oid,
            "accepted_pr": pr,
        },
        body,
    )


@dataclass(frozen=True)
class Proposal:
    """Записанное предложение: что именно и по какому снимку подписывается."""

    head: str | None
    base: str
    text_sha256: str
    human: bool
    accounts: frozenset[str]


@dataclass(frozen=True)
class AcceptFacts:
    """Факты форджи и git для предиката; `None` — факт не установлен."""

    state: str | None
    head: str | None
    base: str | None
    merged_by: str | None
    merge_oid: str | None
    merged_blob_sha256: str | None
    nodes_fresh: bool | None
    approval_pr_open: bool | None
    product_on_tip: bool | None
    agent_login: str | None


@dataclass(frozen=True)
class Verdict:
    """`waiting | stamping | rejected | superseded | unavailable` + причина."""

    kind: str
    reason: str = ""


def _unavailable(what: str) -> Verdict:
    return Verdict("unavailable", f"не установлено: {what}")


def predicate(p: Proposal, f: AcceptFacts) -> Verdict:
    """Предикат §3.5: идентичность (1), актуальность оракула (2), акт (3)."""
    if f.state == "OPEN":
        return Verdict("waiting", "PR предложения open — мержа ещё не было")
    if f.state == "CLOSED":
        return Verdict("rejected", "closed — PR предложения закрыт без мержа")
    if f.state != "MERGED":
        return _unavailable(f"состояние PR ({f.state!r})")
    identity = _identity(p, f)
    if identity is not None:
        return identity
    fresh = _freshness(f)
    if fresh is not None:
        return fresh
    return _act(p, f)


def _identity(p: Proposal, f: AcceptFacts) -> Verdict | None:
    """`merged_blob_sha256 == ""` — файла в мерж-коммите нет (установлено)."""
    if p.head is None:
        return _unavailable("голова предложения не записана")
    if f.head is not None and f.head != p.head:  # голова первой (ревью круга 2)
        return Verdict(
            "rejected", f"head-moved: смержена {f.head}, предложена {p.head}"
        )
    if f.head is None or f.base is None or f.merged_blob_sha256 is None:
        return _unavailable("голова/база PR или файл в мерж-коммите")
    if f.base != p.base:
        return Verdict("rejected", f"base: PR в {f.base}, предложение в {p.base}")
    if f.merged_blob_sha256 != p.text_sha256:
        return Verdict("rejected", "blob: файл в мерж-коммите ≠ предложенному")
    return None


def _freshness(f: AcceptFacts) -> Verdict | None:
    if f.nodes_fresh is None or f.approval_pr_open is None or f.product_on_tip is None:
        return _unavailable("актуальность оракула (узлы, PR одобрения, product_sha)")
    if not f.nodes_fresh:
        return Verdict("superseded", "bundle: узлы на верхушке ≠ байтам bundle_pin")
    if f.approval_pr_open:
        return Verdict("superseded", "approval: открыт PR ветки одобрения воркстрима")
    if not f.product_on_tip:
        return Verdict("superseded", "product: product_sha не предок верхушки")
    return None


def _act(p: Proposal, f: AcceptFacts) -> Verdict:
    if f.merged_by is None or f.merge_oid is None:
        return _unavailable("акт мержа (mergedBy/mergeCommit)")
    if p.human:
        if f.agent_login is None:
            return _unavailable("учётка агента (исключить её из подписантов)")
        if f.merged_by == f.agent_login or f.merged_by not in p.accounts:
            return Verdict(
                "rejected", f"act: {f.merged_by} — не человек из снимка политики"
            )
        return Verdict("stamping")
    if f.merged_by in p.accounts or f.merged_by == f.agent_login:
        return Verdict("stamping")
    if f.agent_login is None:
        return _unavailable("учётка агента (мержер вне снимка)")
    return Verdict("rejected", f"act: {f.merged_by} — ни агент, ни учётка снимка")


def check_stamp(expected: str, actual: str | None) -> str:
    """Проверка штампа §3.2: файл в default ровно ожидаемый штамп."""
    if actual is None:
        return "unavailable"
    return "valid" if actual == expected else "invalid"
