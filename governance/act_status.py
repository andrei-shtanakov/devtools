"""Статус узла `review/v1`: история доставок базы, вердикт доставки, конверт (§9.1).

Одобрение узла устанавливается историей first-parent базы (заявки и
остановки), а не конвертом: конверт обязан совпасть с действующим событием.
Узел без события `review/v1` в продукте под `ACT_MODE=review` судится старым
предикатом ТОЛЬКО после установленного provenance старого акта и admission
его доставки (§9.1 (а) п. 3; ревизия b1, B1-3). Ответ — в форме провайдера
steward: `approved` | `not_approved:<reason>` | `unavailable:<reason>` (О4).
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from governance import (
    act_context,
    act_facts,
    act_trust,
    approval_branches,
    bundle_dag,
    bundle_inputs,
    df_config,
)
from governance import approval_ledger as al
from governance import node_approval as na
from governance.act_check import LegacyAccepted
from governance.act_decision import Accepted, Rejected
from governance.act_envelope import (
    DeliveredRequest,
    DeliveredStop,
    EnvelopeError,
    History,
    current_event,
    envelope_path,
    envelope_refusal,
    parse_envelope,
)
from governance.act_history import (  # реэкспорт: прежние имена (решение 10)
    _event,  # noqa: F401
    _pr_head,
    _request_event,  # noqa: F401
    load_history,
    pr_for_merge,
)
from governance.act_identity import (
    RequestIdentity,
    head_manifest_refusal,
    manifest_refusal,
    paths_refusal,
)
from governance.act_projection import ProjectionError, self_hash_v
from governance.act_request import ACT_REVIEW_V1
from governance.act_settings import (
    ActSettings,
    SettingsError,
    config_home,
    load_settings,
)
from governance.act_time import OPS_FAILURES, Ambiguous, Unavailable
from governance.facts import Outcome as FactOutcome
from governance.frontmatter import split_frontmatter

if TYPE_CHECKING:
    from governance.ops import Ops
    from governance.run_state import RunState

APPROVED = "approved"
REQUEST_BINDING = "request_binding"
Dag = tuple[tuple[str, tuple[str, ...]], ...]
Rows = list[tuple[str, tuple[str, ...], tuple[tuple[str, str], ...]]]


def _rel(state: RunState, fname: str) -> str:
    return f"{state.bundle_dir}/{fname}"


def _node_paths(state: RunState, dag: Dag) -> dict[str, str]:
    return {bundle_dag.node_id(f): _rel(state, f) for f, _ in dag}


# --- зависимости и вердикт доставки ------------------------------------------------


@dataclass(frozen=True)
class Deps:
    """Внешние чтения пути акта; тесты подменяют целиком."""

    readers: Any
    binding: Any  # act_trust.Binding | None | Unavailable
    journal: Any  # (readers, binding) -> act_trust.Journal | Unavailable
    creds: tuple[act_trust.Credential, ...]
    gh: Any
    now: Any  # () -> datetime
    sleep: Any  # (float) -> None
    settings: ActSettings | Unavailable | None  # Unavailable — act.toml не читается


def real_deps(state: RunState, env: Mapping[str, str] | None = None) -> Deps:
    """Боевые зависимости: привязка, самоотчёт `gh`, журнал (Р-5); транспорт
    источников доверия строит `load_trust` (Task 11c). Профиль агента —
    `REVIEW_GH_CONFIG_DIR`, иначе `accounts.agent_gh_config_dir` конфига
    DarkFactory (`df_config`)."""
    env = dict(os.environ) if env is None else env
    home = Path.home()
    agent = (
        Path(env["REVIEW_GH_CONFIG_DIR"])
        if env.get("REVIEW_GH_CONFIG_DIR")
        else df_config.load(env).accounts.agent_gh_config_dir
    )
    merge = Path(env["MERGE_GH_CONFIG_DIR"]) if env.get("MERGE_GH_CONFIG_DIR") else None
    state_home = Path(env.get("XDG_STATE_HOME") or home / ".local" / "state")

    def journal(readers: Any, binding: act_trust.Binding) -> Any:
        from governance import act_journal

        del readers  # снимок журнал перечитывает транспортом решения (Р-23)
        return act_journal.journal_for(
            state_home=state_home,
            run_id=state.run_id,
            binding=binding,
            env=env,
            policy_repository_id=binding.approval_policy_repository_id,
        )

    binding = act_trust.load_binding()
    bound = isinstance(binding, act_trust.Binding)
    return Deps(
        readers=act_trust.default_readers(),
        binding=binding,
        journal=journal,
        creds=act_trust.credentials(env, agent_dir=agent, merge_dir=merge),
        gh=act_facts.gh_transport(agent) if bound else None,
        now=lambda: datetime.now(UTC).replace(microsecond=0),
        sleep=time.sleep,
        settings=_settings(env),
    )


def _settings(env: Mapping[str, str]) -> ActSettings | Unavailable:
    """Настройки установки (`act.toml`); не читаются — названный отказ, а не
    трассировка `SettingsError` мимо обработчиков `RuntimeError` (ревью PR #667)."""
    try:
        return load_settings(config_home(env))
    except SettingsError as exc:
        return Unavailable(f"настройки акта: {exc}")


def _load_journal(deps: Deps) -> Any:
    """Журнал периметра по привязке; нет привязки — `Unavailable` с причиной."""
    binding = deps.binding
    if binding is None:
        return Unavailable("нет привязки корня доверия (§6.1 п. 1)")
    if isinstance(binding, Unavailable):
        return binding
    if deps.journal is None:
        return Unavailable("журнал периметра не задан")
    journal = deps.journal(deps.readers, binding)
    return Unavailable("журнал периметра не загружен") if journal is None else journal


class Decider:
    """Вердикт доставки заявки из истории; доверие грузится один раз.

    Память — по полной идентичности заявки (`RequestIdentity` + merge-коммит),
    не по коммиту: один коммит может доставить несколько манифестов (B1-2).
    До вердикта steward — идентичность и привязка (B1-1): манифест называет эту
    заявку этого воркстрима продукта прогона, его узлы и пути — из DAG прогона,
    те же байты лежат в одобренном head, upstream-пины = прямым входам в head.

    Журнал периметра (`act_context.delivery_verdict` требует настоящий,
    ревизия b1): переданный, иначе загруженный по привязке `deps`; не
    загружен — `Unavailable`, вердикт не выносится.
    """

    def __init__(
        self,
        state: RunState,
        ops: Ops,
        deps: Deps,
        dag: Dag,
        *,
        trust: act_trust.Trust | None = None,
        journal: Any = None,
    ) -> None:
        self.state, self.ops, self.deps, self.dag = state, ops, deps, dag
        self.paths = _node_paths(state, dag)
        self._trust: act_trust.Trust | Unavailable | None = trust
        self._journal = journal
        self._memo: dict[tuple[str, ...], Any] = {}

    @property
    def journal(self) -> Any:
        """Журнал периметра, на котором загружено доверие (после `trust()`)."""
        return self._journal

    def trust(self) -> act_trust.Trust | Unavailable:
        """Доверенный контекст — один раз на вызов (п. 1–5 Task 12a)."""
        if self._trust is None:
            journal = (
                self._journal
                if self._journal is not None
                else (_load_journal(self.deps))
            )
            if isinstance(journal, Unavailable):
                self._trust = journal
            else:
                self._journal = journal
                self._trust = act_trust.load_trust(
                    self.deps.readers,
                    self.deps.binding,
                    journal,
                    creds=self.deps.creds,
                )
        return self._trust

    def context(self) -> tuple[act_trust.Trust, Any] | Unavailable:
        """Доверие и журнал, на котором оно загружено, — пара для
        `act_context.delivery_verdict` (журнал настоящий, ревизия b1); любое
        недоступное — `Unavailable`."""
        trust = self.trust()
        if isinstance(trust, Unavailable):
            return trust
        if self._journal is None:
            journal = _load_journal(self.deps)
            if isinstance(journal, Unavailable):
                return journal
            self._journal = journal
        return trust, self._journal

    def _head_hashes(self, event: DeliveredRequest) -> dict[str, str] | Unavailable:
        hashes: dict[str, str] = {}
        for entry in event.request.nodes.values():
            text = self.ops.show_file(self.state.target_dir, event.head_sha, entry.path)
            if text is None:
                return Unavailable(f"{entry.path} в {event.head_sha[:12]} не читается")
            try:
                hashes[entry.path] = self_hash_v(text, ACT_REVIEW_V1)
            except ProjectionError as exc:
                return Unavailable(str(exc))
        return hashes

    def _pins_refusal(self, event: DeliveredRequest) -> Rejected | Unavailable | None:
        """upstream-пины манифеста = прямым входам узлов в одобренном head."""
        req = event.request
        for node in req.nodes:
            fact = bundle_inputs.direct_blobs(
                self.state, self.ops, self.dag, node, event.head_sha
            )
            if fact.outcome is FactOutcome.FORBIDDEN:
                return Rejected(REQUEST_BINDING, f"прямые входы {node}: {fact.detail}")
            if fact.outcome is not FactOutcome.FOUND or fact.value is None:
                return Unavailable(
                    f"прямые входы {node} в {event.head_sha[:12]}: {fact.detail}"
                )
            if dict(req.upstream_pins[node]) != fact.value:
                return Rejected(
                    REQUEST_BINDING, f"upstream-пины {node} ≠ прямым входам head"
                )
        return None

    def _binding(
        self, event: DeliveredRequest, ident: RequestIdentity
    ) -> Rejected | Unavailable | None:
        """`Rejected(REQUEST_BINDING)` — установленное несовпадение;
        `Unavailable` — не прочитано; `None` — заявка привязана."""
        req = event.request
        identity = manifest_refusal(req, ident) or paths_refusal(req, self.paths)
        if identity is not None:
            return Rejected(REQUEST_BINDING, identity)
        delivered = self.ops.show_file(
            self.state.target_dir, event.merge_commit, ident.manifest_path
        )
        head = self.ops.repo_file_fact(
            self.state.repo_slug, event.head_sha, ident.manifest_path
        )
        if delivered is None or head.outcome is FactOutcome.UNAVAILABLE:
            return Unavailable(
                f"манифест {req.request_id}: не прочитан ({head.detail})"
            )
        at_head = head.value if head.outcome is FactOutcome.FOUND else None
        copy = head_manifest_refusal(at_head, delivered)
        if copy is not None:
            return Rejected(REQUEST_BINDING, copy)
        return self._pins_refusal(event)

    def __call__(self, event: DeliveredRequest) -> Any:
        """Вердикт доставки события истории (память — по идентичности заявки)."""
        ident = RequestIdentity(
            self.state.repo_slug, self.state.ws_id, event.request.request_id
        )
        key = (*ident.memo_key, event.merge_commit)
        if key not in self._memo:
            self._memo[key] = self._decide(event, ident)
        return self._memo[key]

    def _decide(self, event: DeliveredRequest, ident: RequestIdentity) -> Any:
        refused = self._binding(event, ident)
        if refused is not None:
            return refused
        context = self.context()
        if isinstance(context, Unavailable):
            return context
        trust, journal = context
        pr = pr_for_merge(self.ops, self.state.repo_slug, event.merge_commit)
        if isinstance(pr, Unavailable):
            return pr
        outcome, delivery = act_context.delivery_verdict(
            self.deps.readers,
            trust,
            journal,
            product=self.state.repo_slug,
            pr=pr,
            paths=sorted(e.path for e in event.request.nodes.values()),
            request=event.request,
            request_act=ACT_REVIEW_V1,
            head_sha=event.head_sha,
            facts_transport=self.deps.gh,
            self_hashes=lambda: self._head_hashes(event),
            legacy=None,
        )
        if delivery is not None and delivery.merge_commit != event.merge_commit:
            return Unavailable(
                f"PR #{pr} доставил {delivery.merge_commit}, не {event.merge_commit}"
            )
        return outcome

    def legacy(self, pr: int, contract: Callable[[], Any]) -> Any:
        """Admission старой доставки (`request=None`): до активации — РЕЗУЛЬТАТ
        старой проверки `contract()` (`LegacyAccepted` | `Rejected` |
        `Unavailable`), никогда не безусловный `LegacyAccepted` (ревизия b2,
        B2-1)."""
        context = self.context()
        if isinstance(context, Unavailable):
            return context
        trust, journal = context
        outcome, _ = act_context.delivery_verdict(
            self.deps.readers,
            trust,
            journal,
            product=self.state.repo_slug,
            pr=pr,
            paths=[],
            request=None,
            request_act=None,
            head_sha="",
            facts_transport=self.deps.gh,
            self_hashes=lambda: {},
            legacy=contract,
        )
        return outcome


# --- provenance старого одобрения (§9.1 (а) п. 3) -----------------------------------

#: Коды отказа старого акта — по одному на проверку (ревизия b3: двойник
#: проверяет ровно свой код, вход построен так, что все предыдущие проверки
#: проходят).
NO_EDIT, NOT_SIGNATURE = "legacy_no_edit", "legacy_not_signature_commit"
NOT_VIA_PR, NOT_FINALIZE = "legacy_signature_not_pr", "legacy_signature_not_finalize"
FOREIGN_WS, NO_CANDIDATE = "legacy_foreign_workstream", "legacy_no_candidate"
CANDIDATE_NOT_MERGED = "legacy_candidate_not_merged"
CANDIDATE_MERGER, CANDIDATE_TIME = "legacy_candidate_merger", "legacy_candidate_time"
NOT_IN_CANDIDATE = "legacy_node_not_in_candidate"
NO_LEDGER, LEDGER_CANDIDATE = "legacy_no_ledger_record", "legacy_ledger_other_candidate"
LEDGER_NODE = "legacy_ledger_node_absent"
NOT_PENDING, BYTES = "legacy_not_pending", "legacy_bytes"
HASH_FIELD, PINS = "legacy_hash_field", "legacy_pins"
#: Коды старого кода (`approve_node._legacy_outcome`): опубликованность снимка,
#: закреплённая политика, подпись по прежнему allowlist.
OLD_CODES = (
    "legacy_unpublished",
    "legacy_unpinned",
    "policy_changed",
    "legacy_signature",
)
#: Плейсхолдер SSOT-шаблона ветки и его форма в разборе: `ws_id` — любая
#: непустая строка, номера — цифры.
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _candidate_pattern() -> re.Pattern[str]:
    """Разбор candidate-ветки, выведенный из SSOT-шаблона, а не второй
    литерал формы (`tests/test_merge_pr.py`: второго определения имён нет)."""
    template = approval_branches.candidate_template()
    parts: list[str] = []
    pos = 0
    for m in _PLACEHOLDER.finditer(template):
        parts.append(re.escape(template[pos : m.start()]))
        name = m.group(1)
        parts.append(f"(?P<{name}>.+)" if name == "ws_id" else f"(?P<{name}>\\d+)")
        pos = m.end()
    parts.append(re.escape(template[pos:]))
    return re.compile("".join(parts))


def candidate_identity(branch: str) -> tuple[str, str] | None:
    """`(ws_id, request_key)` ветки candidate; форма сверяется круговой
    подстановкой в SSOT-шаблон (`approval_branches.candidate_branch`)."""
    m = _candidate_pattern().fullmatch(branch)
    if m is None:
        return None
    try:
        ws_id = m["ws_id"]
        w, k, a = int(m["wave"]), int(m["step"]), int(m["attempt"])
        # круговая подстановка внутри `try`: неизвестный плейсхолдер шаблона
        # (`candidate_branch` → `KeyError`) — «не candidate», а не исключение
        round_trip = approval_branches.candidate_branch(ws_id, w, k, a)
    except (IndexError, KeyError):
        return None  # шаблон без ожидаемых плейсхолдеров — форма не опознана
    if round_trip != branch:
        return None
    return ws_id, al.request_key(w, k, a)


def legacy_contract_refusal(
    candidate_text: str, base_text: str
) -> tuple[str, str] | None:
    """Байтовая часть старого контракта (сверки 3–4 `_verify_nodes_in_base`):
    в candidate узел был `approval_pending`; собственные байты в базе — те,
    что выносились, и поле хеша им равно; пины подписи — пины выноса.
    `(код, деталь)` либо `None`."""
    cand, _ = split_frontmatter(candidate_text)
    base, _ = split_frontmatter(base_text)
    if cand.get("status") != na.STATUS_APPROVAL_PENDING:
        return NOT_PENDING, "узел в candidate не был на одобрении (approval_pending)"
    recomputed = na.self_hash(base_text)
    if na.self_hash(candidate_text) != recomputed:
        return BYTES, "байты узла в базе ≠ вынесенным candidate"
    if base.get(na.SELF_HASH_KEY) != recomputed:
        return HASH_FIELD, f"{na.SELF_HASH_KEY} ≠ пересчёту байтов базы"
    if dict(cand.get("upstream_hashes") or {}) != dict(
        base.get("upstream_hashes") or {}
    ):
        return PINS, "пины подписи ≠ пинам, под которыми узел выносился"
    return None


def _no(code: str, detail: str) -> str:
    return f"not_approved:{code}:{detail}"


def _settled(answer: str) -> bool:
    """Ответ звена цепочки уже окончателен (отказ или недоступно)."""
    return answer.startswith(("unavailable:", "not_approved:"))


class LegacyProbe:
    """Узел без события `review/v1` в продукте `review`: доказать старый акт
    ЦЕЛИКОМ.

    Цепочка (решение исполнителя плана Р-9, механизм; правило — спека), каждое
    звено — свой код: последняя правка файла узла в first-parent базы — запись
    ЕГО нынешней подписи; её PR — finalize старой заявки воркстрима прогона;
    candidate этой заявки смёржен этой учёткой в это время; узел — среди
    файлов candidate; запись заявки в леджере прогона называет этот candidate
    и этот узел. Вердикт `legacy` admission (`Decider.legacy`) — ПОЛНЫЙ старый
    контракт: байтовая часть (`legacy_contract_refusal`) и
    `approve_node._legacy_outcome` (опубликованный снимок, закреплённый пин
    `approval-policy-source/v1`, его отпечаток, `authorized_signature` мержера
    по прежнему allowlist; ревизия b3, B3-1). Нет пина или политики —
    отказ/`unavailable`, никогда `approved`.
    """

    def __init__(
        self,
        state: RunState,
        ops: Ops,
        base_ref: str,
        decide_legacy: Callable[[int, Callable[[], Any]], Any],
        dag: Dag,
    ) -> None:
        self.state, self.ops, self.base_ref, self.dag = state, ops, base_ref, dag
        self.decide_legacy = decide_legacy
        self._rows: Rows | None = None

    def _changes(self) -> Rows | None:
        if self._rows is None:
            self._rows = self.ops.first_parent_changes(
                self.state.target_dir, self.base_ref
            )
        return self._rows

    def __call__(self, node_path: str, text: str) -> str:
        """Ответ провайдера по узлу старого пути (`approved` — только с
        доказанным старым актом)."""
        meta = split_frontmatter(text)[0]
        by, at = meta.get("approved_by"), meta.get("approved_at")
        rows = self._changes()
        if rows is None:
            return "unavailable:история базы не прочитана"
        commit = next(
            (
                c
                for c, _p, ch in reversed(rows)
                if any(path == node_path for _s, path in ch)
            ),
            None,
        )
        if commit is None:
            return _no(NO_EDIT, "в истории базы нет правки узла")
        signed = self.ops.show_file(self.state.target_dir, commit, node_path)
        signed_meta = split_frontmatter(signed)[0] if signed is not None else {}
        signature = (
            signed_meta.get("status"),
            signed_meta.get("approved_by"),
            signed_meta.get("approved_at"),
        )
        if not by or not at or signature != (na.STATUS_APPROVED, by, at):
            return _no(NOT_SIGNATURE, "последняя правка узла — не его подпись")
        return self._candidate(node_path, text, commit, str(by), str(at))

    def _finalize_branch(self, commit: str) -> str:
        """Ветка finalize-PR, смёржившего коммит подписи; `unavailable:`/
        `not_approved:`."""
        slug = self.state.repo_slug
        try:
            prs = self.ops.prs_containing_commit(slug, commit)
        except OPS_FAILURES as exc:
            return f"unavailable:PR коммита подписи {commit[:12]}: {exc}"
        merged = [
            p["number"]
            for p in prs
            if p.get("state") == "MERGED" and p.get("mergeCommit") == commit
        ]
        if not merged:
            return _no(NOT_VIA_PR, "подпись внесена не смёрженным PR")
        if len(merged) > 1:
            return f"unavailable:коммит подписи — merge-коммит PR {merged}"
        facts = self.ops.brief_pr_fact(slug, int(merged[0]))
        if facts.outcome is not FactOutcome.FOUND or facts.value is None:
            return f"unavailable:PR подписи #{merged[0]}: {facts.detail}"
        if not approval_branches.is_finalize(facts.value.head_ref):
            return _no(NOT_FINALIZE, f"подпись внесена не finalize-PR (#{merged[0]})")
        return facts.value.head_ref

    def _merged_candidate(
        self, candidate_branch: str, by: str, at: str
    ) -> tuple[int, dict] | str:
        """Candidate заявки: смёржен учёткой подписи во время подписи."""
        slug = self.state.repo_slug
        try:
            candidate = self.ops.find_pr(slug, candidate_branch, any_state=True)
            facts = self.ops.pr_facts(slug, candidate) if candidate is not None else {}
        except OPS_FAILURES as exc:
            return f"unavailable:candidate {candidate_branch}: {exc}"
        if candidate is None:
            return _no(NO_CANDIDATE, f"нет candidate-PR {candidate_branch}")
        if facts.get("state") != "MERGED":
            return _no(CANDIDATE_NOT_MERGED, f"candidate #{candidate} не смёржен")
        if (facts.get("mergedBy") or {}).get("login") != by:
            return _no(CANDIDATE_MERGER, f"candidate #{candidate} смёржил не {by}")
        if facts.get("mergedAt") != at:
            return _no(CANDIDATE_TIME, f"мерж candidate #{candidate} не в {at}")
        return candidate, facts

    def _candidate(
        self, node_path: str, text: str, commit: str, by: str, at: str
    ) -> str:
        branch = self._finalize_branch(commit)
        if _settled(branch):
            return branch
        candidate_branch = branch.removesuffix(approval_branches.finalize_suffix())
        ident = candidate_identity(candidate_branch)
        if ident is None or ident[0] != self.state.ws_id:
            return _no(
                FOREIGN_WS,
                f"finalize не заявки воркстрима {self.state.ws_id} "
                f"({candidate_branch})",
            )
        key = ident[1]
        merged = self._merged_candidate(candidate_branch, by, at)
        if isinstance(merged, str):
            return merged
        candidate, facts_c = merged
        offered = self._offered(candidate, node_path)
        if _settled(offered):
            return offered
        op = self.state.ops.get(key)
        if op is None:
            return _no(
                NO_LEDGER,
                f"в леджере прогона нет заявки {key} — пин старой политики "
                "не установить",
            )
        if op.get("candidate_pr") != candidate:
            return _no(
                LEDGER_CANDIDATE,
                f"заявка {key} леджера — candidate #{op.get('candidate_pr')}, "
                f"не #{candidate}",
            )
        node = bundle_dag.node_id(node_path.rsplit("/", 1)[-1])
        if node not in op.get("nodes", ()):
            return _no(LEDGER_NODE, f"заявка {key} леджера не выносила узел {node}")
        return self._verdict(candidate, offered, text, op, key, facts_c)

    def _verdict(
        self,
        candidate: int,
        offered: str,
        text: str,
        op: Mapping,
        key: str,
        facts_c: Mapping,
    ) -> str:
        from governance import approve_node as an  # ленивый: approve_node → act_status

        def contract() -> Any:
            refusal = legacy_contract_refusal(offered, text)
            if refusal is not None:
                return Rejected(*refusal)
            got = an._legacy_outcome(
                self.state, self.ops, self.dag, dict(op), key, dict(facts_c)
            )
            return Unavailable(got.text) if isinstance(got, an.LegacyHalt) else got

        verdict = self.decide_legacy(candidate, contract)
        if isinstance(verdict, LegacyAccepted):
            # учётка старого кода = подписант: `_legacy_outcome` берёт её из facts_c
            return APPROVED
        if isinstance(verdict, Rejected):
            own = verdict.code.startswith("legacy_") or verdict.code in OLD_CODES
            code = verdict.code if own else f"legacy_admission:{verdict.code}"
            return _no(code, verdict.detail or verdict.code)
        return f"unavailable:{getattr(verdict, 'reason', type(verdict).__name__)}"

    def _offered(self, candidate: int, node_path: str) -> str:
        """Текст узла в голове candidate — только если узел среди файлов
        candidate."""
        files = self.ops.brief_pr_fact(self.state.repo_slug, candidate)
        if files.outcome is not FactOutcome.FOUND or files.value is None:
            return f"unavailable:файлы candidate #{candidate}: {files.detail}"
        if node_path not in {path for path, _change in files.value.files}:
            return _no(NOT_IN_CANDIDATE, f"узел не из candidate #{candidate}")
        head = _pr_head(self.ops, self.state, candidate)
        if isinstance(head, Unavailable):
            return f"unavailable:{head.reason}"
        offered = self.ops.show_file(self.state.target_dir, head, node_path)
        if offered is None:
            return f"unavailable:узел в голове candidate {head[:12]} не читается"
        return offered


# --- статус узла --------------------------------------------------------------------


class Statuses:
    """Статусы узлов одного снимка; память по узлу (рекурсия по upstream)."""

    def __init__(
        self,
        *,
        dag: Dag,
        ws_id: str,
        history: History | Unavailable,
        node_path: Callable[[str], str],
        text_of: Callable[[str], str | None],
        blobs_of: Callable[[str], Mapping[str, str] | Unavailable],
        decide: Callable[[DeliveredRequest], Any],
        legacy: Callable[[str, str], str],
    ) -> None:
        self.dag, self.ws_id, self.history = dag, ws_id, history
        self.node_path, self.text_of, self.blobs_of = node_path, text_of, blobs_of
        self.decide, self.legacy = decide, legacy
        self._memo: dict[str, str] = {}

    def of(self, node: str) -> str:
        """Ответ провайдера по узлу."""
        if node not in self._memo:
            self._memo[node] = self._compute(node)
        return self._memo[node]

    def is_review(self, node: str) -> bool:
        """У узла есть событие `review/v1` в истории (неизвестно — тоже да)."""
        if isinstance(self.history, Unavailable):
            return True
        return current_event(self.history, node) is not None

    def _upstreams(self, node: str) -> tuple[str, ...]:
        for fname, ups in self.dag:
            if bundle_dag.node_id(fname) == node:
                return ups
        return ()

    def _stopped(self, node: str) -> bool:
        if isinstance(self.history, Unavailable):
            return False
        return isinstance(current_event(self.history, node), DeliveredStop)

    def _legacy(self, node: str) -> str:
        text = self.text_of(self.node_path(node))
        if text is None:
            return "not_approved:нет файла узла"
        pins = self.blobs_of(node)
        if isinstance(pins, Unavailable):
            return f"unavailable:{pins.reason}"
        debt = na.node_debt(node, text, dict(pins))
        if debt is not None:
            return f"not_approved:{debt.kind}"
        return self.legacy(self.node_path(node), text)

    def _compute(self, node: str) -> str:
        if isinstance(self.history, Unavailable):
            return f"unavailable:{self.history.reason}"
        current = current_event(self.history, node)
        if current is None:
            return self._legacy(node)
        if isinstance(current, DeliveredStop):
            return f"not_approved:stopped:g{current.stop.generation}"
        verdict = self.decide(current)
        if isinstance(verdict, (Unavailable, Ambiguous)):
            return f"unavailable:{verdict.reason}"
        if not isinstance(verdict, Accepted):
            code = getattr(verdict, "code", type(verdict).__name__)
            return f"not_approved:act:{code}"
        refusal = self._envelope(node, current, verdict.review_id)
        if refusal is not None:
            return refusal
        for up in self._upstreams(node):
            status = self.of(up)
            if status.startswith("unavailable:"):
                return f"unavailable:upstream:{up}"
            if status != APPROVED:
                kind = "upstream_stopped" if self._stopped(up) else "upstream"
                return f"not_approved:{kind}:{up}"
        return APPROVED

    def _envelope(
        self, node: str, current: DeliveredRequest, review_id: str
    ) -> str | None:
        text = self.text_of(self.node_path(node))
        if text is None:
            return "not_approved:нет файла узла"
        raw = self.text_of(envelope_path(self.ws_id, node))
        try:
            env = parse_envelope(raw) if raw is not None else None
            own = self_hash_v(text, ACT_REVIEW_V1)
        except (EnvelopeError, ProjectionError) as exc:
            return f"not_approved:{exc}"
        pins = self.blobs_of(node)
        if isinstance(pins, Unavailable):
            return f"unavailable:{pins.reason}"
        assert not isinstance(self.history, Unavailable)
        refusal = envelope_refusal(env, self.history, node, own, pins, review_id)
        return None if refusal is None else f"not_approved:{refusal}"


def _blobs(
    state: RunState, ops: Ops, dag: Dag, ref: str | None
) -> Callable[[str], Mapping[str, str] | Unavailable]:
    def read(node: str) -> Mapping[str, str] | Unavailable:
        fact = bundle_inputs.direct_blobs(state, ops, dag, node, ref)
        if fact.outcome is not FactOutcome.FOUND or fact.value is None:
            return Unavailable(f"прямые входы {node}: {fact.detail}")
        return fact.value

    return read


def _statuses(
    state: RunState,
    ops: Ops,
    dag: Dag,
    live: LivePolicy,
    deps: Deps,
    *,
    text_of: Callable[[str], str | None],
    ref: str | None,
    snapshot: str,
) -> Statuses:
    """`snapshot` — единственный снимок базы для истории и provenance старого
    акта (d3); `ref` — входы узлов (`None` — рабочее дерево S4), `text_of` —
    байты узлов."""
    decider = Decider(state, ops, deps, dag, trust=live.trust, journal=live.journal)
    paths = _node_paths(state, dag)
    return Statuses(
        dag=dag,
        ws_id=state.ws_id,
        history=load_history(ops, state, snapshot),
        node_path=paths.__getitem__,
        text_of=text_of,
        blobs_of=_blobs(state, ops, dag, ref),
        decide=decider,
        legacy=LegacyProbe(state, ops, snapshot, decider.legacy, dag),
    )


def statuses_at_base(
    state: RunState,
    ops: Ops,
    dag: Dag,
    live: LivePolicy,
    deps: Deps | None = None,
    *,
    ref: str | None = None,
) -> Statuses:
    """Статусы по базе для гейта доставки и предложений (`read_dag_state`).

    Только для продукта в `review` (`live.mode`); иначе путь старый (§10).
    `ref` — снимок базы вместо локальной ветки (SHA от `fresh_base`, d3): им
    читаются история, provenance, байты и входы узлов — всё."""
    base = ref or state.base_ref or "master"
    return _statuses(
        state,
        ops,
        dag,
        live,
        deps or real_deps(state),
        text_of=lambda path: ops.show_file(state.target_dir, base, path),
        ref=base,
        snapshot=base,
    )


# --- режим продукта сейчас (§10; ревизия b1, B1-5) ----------------------------------


@dataclass(frozen=True)
class LivePolicy:
    """Текущая активация открытой эпохи, её набор и режим продукта (живое
    правило §6)."""

    mode: str  # act_trust.PRE_MIGRATION | "merge" | "review"
    trust: Any  # act_trust.Trust
    journal: Any  # act_trust.Journal
    activation: Any  # act_check.ActivationView
    policy: Any  # act_policy.PolicySet


def live_policy(state: RunState, deps: Deps) -> LivePolicy | Unavailable:
    """Режим продукта сейчас по истории режима (`act_trust.product_mode`).

    Нет привязки, удалённая запись, откат `review → merge`, недочитанная
    нужная история — `Unavailable`, а не legacy (§6.1, §10; ревизия b1).
    Нечитаемые настройки установки — такой же отказ конфигурации."""
    if isinstance(deps.settings, Unavailable):
        return deps.settings
    if deps.binding is None:
        return Unavailable(
            "нет привязки корня доверия /etc/df/trust.toml (§6.1 п. 1) — "
            "режим продукта не установлен"
        )
    journal = _load_journal(deps)
    if isinstance(journal, Unavailable):
        return journal
    trust = act_trust.load_trust(deps.readers, deps.binding, journal, creds=deps.creds)
    if isinstance(trust, Unavailable):
        return trust
    if not trust.epochs:
        return Unavailable("журнал эпох не прочитан")
    now = deps.now()
    anchors = act_trust.load_anchor_times(trust, now)
    loaded = act_trust.load_ranges(trust, anchors, now, journal)
    if isinstance(loaded, Unavailable):
        return loaded
    ranges, sets = loaded
    mode = act_trust.product_mode(trust, ranges, sets, state.repo_slug, now)
    if isinstance(mode, Unavailable):
        return mode
    current = act_trust.current_activation(trust, ranges)
    if isinstance(current, Unavailable):
        return current
    policy = sets.get(
        current.merge_commit, Unavailable("набор текущей активации не прочитан")
    )
    if isinstance(policy, Unavailable):
        return policy
    return LivePolicy(mode, trust, journal, current, policy)


def mode_probe(state: RunState, deps: Deps | None = None) -> LivePolicy | Unavailable:
    """Единая точка «режим продукта сейчас» для путей вне `act_*` (гейты, DAG,
    создание).

    Тесты вне `act_*` подменяют её на `PRE_MIGRATION` (`tests/conftest.py`)."""
    return live_policy(state, deps or real_deps(state))


@contextmanager
def fresh_base(state: RunState, ops: Ops) -> Iterator[tuple[str, Ops] | Unavailable]:
    """Свежий изолированный снимок базы продукта для проверки допуска (ревью
    d2–d5): `(SHA ветки <base> репозитория state.repo_slug, Ops чтений из
    снимка)` или `Unavailable`. Снимок — временный репозиторий
    (`Ops.snapshot_open`): живёт до конца блока `with`, то есть до конца всех
    чтений проверки, затем удаляется — и при исключении. Все чтения проверки
    идут через отданные `Ops` и этот SHA."""
    base = state.base_ref or "master"
    fact = ops.snapshot_open(state.target_dir, state.repo_slug, base)
    if fact.outcome is not FactOutcome.FOUND or fact.value is None:
        yield Unavailable(f"снимок {state.repo_slug}@{base}: {fact.detail}")
        return
    repo = fact.value
    try:
        yield repo.sha, ops.snapshot_reader(repo)
    finally:
        ops.snapshot_close(repo)


def noop_refusal(
    state: RunState,
    ops: Ops,
    dag: Dag,
    nodes: Iterable[str] | None = None,
    deps: Deps | None = None,
) -> str | None:
    """Почему успех без полного пути не допускается, или `None` (решение
    владельца 01.11).

    Любой успешный ранний выход — бесследный no-op §I5, повтор шага по
    сохранённому результату, переход волны по завершённой заявке, возврат уже
    доставленного PR — для продукта в `review` требует актуального допуска:
    каждый названный узел (`None` — все узлы DAG) с событием `review/v1` —
    `approved` по `statuses_at_base` на свежем снимке базы продукта
    (доверенный контекст, §9.1). Режим не установлен, снимок не получен, узел
    не `approved` или статус недоступен — причина (fail closed); продукт не в
    `review` — `None`, прежние правила. Все чтения — внутри
    `with fresh_base(...)`, через `Ops` снимка. Цена — квота С-1."""
    live = mode_probe(state, deps)
    if isinstance(live, Unavailable):
        return f"режим акта продукта: {live.reason}"
    if live.mode != "review":
        return None
    wanted = None if nodes is None else set(nodes)
    if wanted is not None and not wanted:
        return None  # судить нечего: без снимка и истории (квота С-1, ревью PR #667)
    with fresh_base(state, ops) as snapshot:  # d2–d5: изолированный свежий снимок
        if isinstance(snapshot, Unavailable):
            return f"свежая база: {snapshot.reason}"
        ref, reader = snapshot
        statuses = statuses_at_base(state, reader, dag, live, deps, ref=ref)
        for fname, _upstreams in dag:
            node = bundle_dag.node_id(fname)
            if wanted is not None and node not in wanted:
                continue
            if not statuses.is_review(node):
                continue
            verdict = statuses.of(node)
            if verdict != APPROVED:
                return f"узел {node}: {verdict}"
    return None


# --- гейты steward (Р-10) -----------------------------------------------------------


def _gate_statuses(
    state: RunState,
    ops: Ops,
    dag: Dag,
    deps: Deps,
    live: LivePolicy,
    *,
    candidate: bool,
) -> dict[str, str]:
    """Статусы для провайдера — по тому снимку, который читает steward (§12
    п. 3): S4 (`candidate`) — байты и входы рабочего дерева волны; S8 — база
    (`state.base_ref`), как `statuses_at_base`."""
    base = state.base_ref or "master"
    worktree = Path(state.target_dir)

    def worktree_text(path: str) -> str | None:
        file = worktree / path
        return file.read_text(encoding="utf-8") if file.is_file() else None

    def base_text(path: str) -> str | None:
        return ops.show_file(state.target_dir, base, path)

    text_of = worktree_text if candidate else base_text
    st = _statuses(
        state,
        ops,
        dag,
        live,
        deps,
        text_of=text_of,
        ref=None if candidate else base,
        snapshot=base,
    )
    paths = _node_paths(state, dag)
    return {
        node: st.of(node) for node, path in paths.items() if text_of(path) is not None
    }


def gate_run(
    state: RunState,
    ops: Ops,
    dag: Dag,
    *,
    candidate: bool,
    out: Path,
    deps: Deps | None = None,
) -> tuple[str, str | None] | Unavailable:
    """Режим `gate-check` по режиму продукта (Р-10) и файл провайдера (§12
    п. 3): `("legacy", None)` до миграции и в `merge`, `("review", путь)` в
    `review`; режим не установлен или дайджест снимка не получен — стоп."""
    live = mode_probe(state, deps)
    if isinstance(live, Unavailable):
        return live
    if live.mode != "review":
        return "legacy", None  # PRE_MIGRATION или merge — старый читатель (§10)
    rc, digest = ops.gate_snapshot_digest(
        state.target_dir, state.bundle_dir, candidate=candidate
    )
    if rc != 0 or not digest.startswith("sha256:"):
        return Unavailable(f"gate-check --print-snapshot-digest: код {rc}: {digest}")
    nodes = _gate_statuses(
        state, ops, dag, deps or real_deps(state), live, candidate=candidate
    )
    out.write_text(
        json.dumps({"snapshot_digest": digest, "nodes": nodes}, sort_keys=True),
        encoding="utf-8",
    )
    return "review", str(out)
