"""Путь узла review/v1 (Task 13; приёмка №1, 8, 10, 13, 24; Review Focus 5)."""

from __future__ import annotations

import subprocess
from dataclasses import fields, replace
from pathlib import Path

import pytest

pytest.importorskip("steward")

from steward.approvalfacts import v3  # noqa: E402

from governance import (  # noqa: E402
    act_context,
    act_facts,
    act_node,
    act_status,
    act_trust,
)
from governance import approval_ledger as al  # noqa: E402
from governance import approve_node as an  # noqa: E402
from governance import node_approval as na  # noqa: E402
from governance.act_check import ActivationView  # noqa: E402
from governance.act_decision import (  # noqa: E402
    Accepted,
    DeliveryView,
    Rejected,
    ReviewView,
)
from governance.act_envelope import (  # noqa: E402
    DeliveredRequest,
    Envelope,
    StopRecord,
    envelope_path,
    parse_envelope,
    render_envelope,
    render_stop,
    stop_path,
)
from governance.act_epochs import Epoch  # noqa: E402
from governance.act_policy import parse_policy_set  # noqa: E402
from governance.act_request import (  # noqa: E402
    NodeEntry,
    NodeRequest,
    PolicyPin,
    manifest_path,
    parse_manifest,
    render_manifest,
)
from governance.act_settings import ActSettings  # noqa: E402
from governance.act_source import SourceV2  # noqa: E402
from governance.act_time import Ambiguous, Unavailable, parse_ts  # noqa: E402
from governance.facts import Fact, Outcome, unavailable  # noqa: E402
from tests.act_cases import FILES  # noqa: E402
from tests.test_act_context import NoEvents  # noqa: E402
from tests.test_governance_approve_node import (  # noqa: E402
    SLUG,
    WS_ID,
    Ops,
    World,
    _git,
    _make_world,
    _show,
    approve,
    drive_to_approved,
    merge_pr,
)

ACT = "a" * 40
NOW = parse_ts("2026-10-12T12:00:00Z")
SOURCE = SourceV2("owner/approval-policy", "main", ACT, frozenset({"owner"}))
MODES = f"schema: approval-policy-modes/v1\nmodes:\n  {SLUG}: review\n".encode()
POLICY = parse_policy_set(ACT, {**FILES, "policy/modes.yaml": MODES})
ROOT = act_trust.TrustRoot("github.com", 11, "main", "s" * 40)


def trust(unverified: tuple[str, ...] = ()) -> act_trust.Trust:
    snap = act_trust.TrustSnapshot(ROOT, "owner/df-trust", SOURCE, (Epoch(1, ACT),))
    return act_trust.Trust(snap, unverified)


class BodyOps(Ops):
    """Стенд approve_node + тело PR и файлы манифестов из origin (форджа стенда
    отдаёт по `repo_file_fact` только файлы политики)."""

    def __init__(self, forge) -> None:
        super().__init__(forge)
        self.merge_bases: list[tuple[int, str | None]] = []

    def merge(self, repo_name: str, pr: int, sha: str, base: str | None = None) -> int:
        self.merge_bases.append((pr, base))
        return super().merge(repo_name, pr, sha, base)

    def pr_facts(self, repo_slug: str, pr: int) -> dict:
        """+ `baseRefName` (запись PR `base`, умолчание `master`; `None` — не назван)."""
        facts = super().pr_facts(repo_slug, pr)
        facts["baseRefName"] = self.forge.prs[pr].get("base", "master")
        return facts

    def remote_branch_head_fact(self, repo_slug: str, branch: str) -> Fact[str]:
        head = self.forge.head_of(branch)
        if head is None:
            return Fact(Outcome.ABSENT, None, "нет ветки")
        return Fact(Outcome.FOUND, head, "ok")

    def pr_body_fact(self, repo_slug: str, pr: int) -> Fact[str]:
        return Fact(Outcome.FOUND, self.forge.prs[pr]["body"], "ok")

    def repo_file_fact(self, repo_slug, sha, path):
        if "/approvals/requests/" not in path:
            return super().repo_file_fact(repo_slug, sha, path)
        done = subprocess.run(
            ["git", "-C", str(self.forge.origin), "show", f"{sha}:{path}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode != 0:
            return Fact(Outcome.ABSENT, None, "нет файла")
        return Fact(Outcome.FOUND, done.stdout, "ok")

    def _snapshot_url(self, repo_slug: str) -> str:
        """Свежий снимок базы (`noop_refusal`) — из origin стенда, не из сети."""
        return str(self.forge.origin)

    def compare_files_fact(self, repo_slug: str, base: str, head: str):
        """REST compare стенда — `git diff --name-status base...head` в origin."""
        done = subprocess.run(
            [
                "git",
                "-C",
                str(self.forge.origin),
                "diff",
                "--name-status",
                "--no-renames",
                f"{base}...{head}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode != 0:
            return unavailable(f"compare {base}...{head}: {done.stderr.strip()}")
        names = {"A": "added", "M": "modified", "D": "removed"}
        rows = (line.split("\t", 1) for line in done.stdout.splitlines() if line)
        return Fact(Outcome.FOUND, tuple((p, names[st]) for st, p in rows), "ok")

    def prs_containing_commit(self, repo_slug, sha):
        return [
            {"number": n, "state": "MERGED", "mergeCommit": sha}
            for n, rec in self.forge.prs.items()
            if (rec.get("mergeCommit") or {}).get("oid") == sha
        ]


class Stand:
    """Подмены доверия и фактов ревью; `verdict` — исход доставки candidate."""

    def __init__(self, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        self.world = world
        self.settings = ActSettings(review_v1=True, draining=False)
        self.binding: object = object()
        self.mode = "review"
        self.mode_unset: str | None = None  # причина Unavailable режима продукта
        self.unverified: tuple[str, ...] = ()
        self.effective: dict[str, ReviewView] = {}
        self.verdict: object = None
        self.dismissals: tuple[v3.Dismissal, ...] = ()
        monkeypatch.setattr(act_node, "DEPS_FACTORY", lambda state: self.deps())
        monkeypatch.setattr(
            act_status, "mode_probe", lambda state, deps=None: self.probe()
        )
        monkeypatch.setattr(
            act_facts, "open_review", lambda t, repo, pr: self.facts(pr)
        )
        monkeypatch.setattr(
            act_facts, "effective", lambda facts, clock: dict(self.effective)
        )
        monkeypatch.setattr(act_context, "delivery_verdict", self.deliver)
        monkeypatch.setattr(
            act_status.Decider, "trust", lambda d: trust(self.unverified)
        )

    def deps(self) -> act_status.Deps:
        return act_status.Deps(
            readers=None,
            binding=self.binding,
            journal=lambda r, b: NoEvents(),
            creds=(),
            gh=None,
            now=lambda: NOW,
            sleep=lambda s: None,
            settings=self.settings,
        )

    def probe(self) -> act_status.LivePolicy | Unavailable:
        return Unavailable(self.mode_unset) if self.mode_unset else self.live()

    def live(self) -> act_status.LivePolicy:
        policy = POLICY if self.mode == "review" else parse_policy_set(ACT, FILES)
        activation = ActivationView(
            ("activation", ACT), ACT, parse_ts("2026-10-12T08:00:00Z")
        )
        return act_status.LivePolicy(
            self.mode, trust(self.unverified), None, activation, policy
        )

    def facts(self, pr: int) -> v3.ReviewFacts:
        head = self.world.forge.head_of(self.world.forge.prs[pr]["branch"])
        return v3.ReviewFacts(SLUG, pr, head, "master", "OPEN", (), self.dismissals)

    def approve_head(self, pr: int) -> None:
        head = self.world.forge.head_of(self.world.forge.prs[pr]["branch"])
        self.effective = {"alice": ReviewView("11", "alice", "User", "APPROVED", head)}

    def deliver(
        self,
        readers,
        trust_,
        journal,
        *,
        product,
        pr,
        paths,
        request,
        request_act,
        head_sha,
        facts_transport,
        self_hashes,
        legacy,
    ):
        rec = self.world.forge.prs[pr]
        delivery = DeliveryView(
            True,
            rec["mergeCommit"]["oid"],
            rec["mergedAt"],
            "df-reviewer",
            "Bot",
            head_sha,
            True,
            {},
        )
        if self.verdict is not None:
            return self.verdict, delivery
        if request is None:
            return legacy(), delivery
        return Accepted(delivery.merge_commit, "df-reviewer", "11", "alice"), delivery


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    w = _make_world(tmp_path, monkeypatch, seed_nodes=True)
    return replace(w, ops=BodyOps(w.forge))


@pytest.fixture()
def stand(world: World, monkeypatch: pytest.MonkeyPatch) -> Stand:
    return Stand(world, monkeypatch)


def _candidate(world: World) -> tuple[str, dict, int]:
    first = approve(world, "charter")
    key = first.request
    op = world.state.ops[key]
    return key, op, op["candidate_pr"]


# --- создание ----------------------------------------------------------------------


def test_review_request_carries_manifest_label_and_body(
    world: World, stand: Stand
) -> None:
    key, op, pr = _candidate(world)
    rec = world.forge.prs[pr]
    assert rec["label"] == act_node.HUMAN_APPROVAL_LABEL
    head = world.forge.head_of(rec["branch"])
    req = parse_manifest(_show(world.target, f"{head}:{manifest_path(WS_ID, key)}"))
    assert req.nodes["charter"].generation == 1
    assert req.policy.activation_commit == ACT
    assert op["act"] == "review/v1" and op["generations"] == {"charter": 1}
    assert (
        "act: review/v1" in rec["body"] and f"policy-activation: {ACT}" in rec["body"]
    )


def test_draining_refuses_unmarked_creation(world: World, stand: Stand) -> None:
    stand.mode, stand.settings = "merge", ActSettings(review_v1=False, draining=True)
    with pytest.raises(RuntimeError, match="draining"):
        approve(world, "charter")
    assert al.requests(world.state) == []


def test_review_mode_without_review_v1_refuses(world: World, stand: Stand) -> None:
    stand.settings = ActSettings(review_v1=False, draining=False)
    with pytest.raises(RuntimeError, match="review_v1"):
        approve(world, "charter")


def test_unset_mode_refuses_creation(world: World, stand: Stand) -> None:
    """B1-5: нет привязки / удалённая запись / откат — не legacy, а отказ создания."""
    stand.mode_unset = "нет привязки корня доверия"
    with pytest.raises(RuntimeError, match="режим акта"):
        approve(world, "charter")
    assert al.requests(world.state) == []


def test_pre_migration_keeps_legacy_creation(world: World, stand: Stand) -> None:
    stand.mode = act_trust.PRE_MIGRATION
    _key, op, pr = _candidate(world)
    assert op.get("act") is None
    assert world.forge.prs[pr]["label"] == an.HUMAN_MERGE_LABEL


# --- открытый PR ---------------------------------------------------------------------


def test_open_without_approve_waits(world: World, stand: Stand) -> None:
    _key, _op, _pr = _candidate(world)
    out = approve(world, "charter")
    assert act_node.WAITING_HUMAN_APPROVAL in out.message
    assert world.forge.merge_calls == []


def test_open_with_approve_merges_by_agent(world: World, stand: Stand) -> None:
    _key, _op, pr = _candidate(world)
    stand.approve_head(pr)
    out = approve(world, "charter")
    assert act_node.WAITING_DELIVERY in out.message
    assert world.forge.merge_calls[0][0] == pr
    assert world.forge.prs[pr]["state"] == "MERGED"


def test_open_candidate_head_moved_between_reads(
    world: World, stand: Stand, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B3-6 (`_open`): факты ревью названы на другой голове, чем голова PR — заявка жива,
    агентский мерж не вызывается."""
    key, _op, pr = _candidate(world)
    stand.approve_head(pr)
    monkeypatch.setattr(
        act_facts,
        "open_review",
        lambda t, repo, n: v3.ReviewFacts(SLUG, n, "0" * 40, "master", "OPEN", (), ()),
    )
    with pytest.raises(RuntimeError, match="сдвинулась между чтениями"):
        approve(world, "charter")
    assert world.forge.prs[pr]["state"] == "OPEN"
    assert world.state.ops[key]["status"] == al.STATUS_STARTED


def test_open_candidate_in_merge_mode_refused(world: World, stand: Stand) -> None:
    """B3-6 (`_open`): заявка review/v1, а режим продукта сейчас не review — не мержится."""
    _key, _op, pr = _candidate(world)
    stand.approve_head(pr)
    stand.mode = "merge"
    with pytest.raises(RuntimeError, match="а режим продукта сейчас — merge"):
        approve(world, "charter")
    assert world.forge.prs[pr]["state"] == "OPEN"


def _level_three(world: World, stand: Stand, monkeypatch: pytest.MonkeyPatch) -> None:
    """Стенд до шага, где `design` и `acceptance` — два узла одного шага с общими
    upstream (`requirements`, `behaviour-spec`): upstream проведены старым путём до
    миграции; их статус в продукте review — «approved» (судят двойники `LegacyProbe`,
    здесь — только проверки присоединения)."""
    stand.mode = act_trust.PRE_MIGRATION
    for node in ("charter", "requirements", "behaviour-spec"):
        drive_to_approved(world, node)
    real_of = act_status.Statuses.of
    monkeypatch.setattr(
        act_status.Statuses,
        "of",
        lambda self, node: (
            act_status.APPROVED
            if node in ("charter", "requirements", "behaviour-spec")
            else real_of(self, node)
        ),
    )


def _child_of_charter() -> str:
    dag = an.bundle_dag.dag_for(None)
    return next(an.bundle_dag.node_id(f) for f, ups in dag if "charter" in ups)


def test_join_live_legacy_request_refused(
    world: World, stand: Stand, monkeypatch: pytest.MonkeyPatch
) -> None:
    """b5/b6 (`approve_node._propose#act_incompatible`): живая заявка шага без маркера
    (`design`, до миграции) и новая `review/v1` (`acceptance`, тот же шаг) в одну не
    соединяются."""
    _level_three(world, stand, monkeypatch)
    first = approve(world, "design")  # заявка без маркера над шагом
    stand.mode = "review"
    with pytest.raises(RuntimeError, match="в одну заявку они не соединяются"):
        approve(world, "acceptance")
    assert world.state.ops[first.request]["status"] == al.STATUS_STARTED
    assert world.state.ops[first.request]["nodes"] == ["design"]


def test_join_request_pinned_to_other_activation_refused(
    world: World, stand: Stand, monkeypatch: pytest.MonkeyPatch
) -> None:
    """b5/b6 (`approve_node._propose#other_pin`): живая заявка шага (`design`, review/v1)
    закрепила другую активацию — `acceptance` того же шага к ней не дописывается."""
    _level_three(world, stand, monkeypatch)
    stand.mode = "review"
    first = approve(world, "design")
    newer = ActivationView(("activation", "b" * 40), "b" * 40, NOW)
    monkeypatch.setattr(
        act_status,
        "mode_probe",
        lambda state, deps=None: replace(stand.live(), activation=newer),
    )
    with pytest.raises(RuntimeError, match="закрепила другую активацию"):
        approve(world, "acceptance")
    assert world.state.ops[first.request]["nodes"] == ["design"]


def test_review_status_unavailable_blocks_proposal(
    world: World, stand: Stand, monkeypatch: pytest.MonkeyPatch
) -> None:
    """b5 (`approve_node._propose#status_unavailable`): статус узла review/v1 не установлен —
    заявка не создаётся, а не «долг по frontmatter»."""
    monkeypatch.setattr(act_status.Statuses, "is_review", lambda self, node: True)
    monkeypatch.setattr(
        act_status.Statuses, "of", lambda self, node: "unavailable:история"
    )
    with pytest.raises(RuntimeError, match="статус узла charter: unavailable:история"):
        approve(world, "charter")
    assert al.requests(world.state) == []


def test_read_dag_state_mode_unset(world: World, stand: Stand) -> None:
    """b5 (`approve_node.read_dag_state#mode_unset`)."""
    stand.mode_unset = "нет привязки корня доверия"
    got = an.read_dag_state(world.state, world.ops, an.bundle_dag.dag_for(None))
    assert got.unresolved == "режим акта продукта: нет привязки корня доверия"


def test_read_dag_state_status_unavailable(
    world: World, stand: Stand, monkeypatch: pytest.MonkeyPatch
) -> None:
    """b5 (`approve_node.read_dag_state#status_unavailable`)."""
    monkeypatch.setattr(
        act_status.Statuses, "of", lambda self, node: "unavailable:история"
    )
    got = an.read_dag_state(world.state, world.ops, an.bundle_dag.dag_for(None))
    assert got.unresolved == "unavailable:история"


def test_read_dag_state_review_debt(
    world: World, stand: Stand, monkeypatch: pytest.MonkeyPatch
) -> None:
    """b5 (`approve_node.read_dag_state#review_debt`): не approved — долг review/v1
    (только у этого узла; прочие одобрены)."""
    monkeypatch.setattr(
        act_status.Statuses,
        "of",
        lambda self, node: (
            "not_approved:act:d1_no_act" if node == "charter" else act_status.APPROVED
        ),
    )
    got = an.read_dag_state(world.state, world.ops, an.bundle_dag.dag_for(None))
    debts = [d for d in got.debts if d.kind == na.DEBT_REVIEW_V1]
    assert len(debts) == 1 and debts[0].reason == "act:d1_no_act"
    assert debts[0].node_id == "charter"


@pytest.mark.parametrize(
    "verdict,match",
    [
        (None, "режим акта продукта \\(upstream"),
        ("unavailable:история", "upstream charter узла .*: unavailable:история"),
        ("not_approved:act:d1_no_act", "upstream charter не одобрен \\(review/v1\\)"),
    ],
    ids=["mode_unset", "upstream_unavailable", "upstream_not_approved"],
)
def test_upstream_readiness_in_review(
    world: World, stand: Stand, monkeypatch: pytest.MonkeyPatch, verdict, match: str
) -> None:
    """b5 (`approve_node._require_upstream_ready#…`): готовность upstream в продукте review —
    по статусу review/v1, не по frontmatter."""
    child = _child_of_charter()
    if verdict is None:
        # D1-1 в `_propose` отказывает раньше (`test_frontmatter_noop_refused_when_
        # mode_unavailable`): собственный отказ гварда upstream — его прямым вызовом
        stand.mode_unset = "нет привязки"
        with pytest.raises(RuntimeError, match=match):
            an._require_upstream_ready(
                world.state, world.ops, an.bundle_dag.dag_for(None), child
            )
        return
    monkeypatch.setattr(act_status.Statuses, "of", lambda self, node: verdict)
    with pytest.raises(RuntimeError, match=match):
        approve(world, child)


def test_finalize_form_defect_blocks_agent_merge(
    world: World, stand: Stand, monkeypatch: pytest.MonkeyPatch
) -> None:
    """b5 (`approve_node._try_agent_merge#form`): дефект формы коммита конверта — агентский
    мерж finalize не выполняется, причина — дефект формы."""
    key, _pr = _deliver(world, stand)
    monkeypatch.setattr(
        act_node,
        "envelope_commit_defect",
        lambda *a: "коммит конверта трогает лишний путь",
    )
    outcome = approve(world, "charter")
    assert "коммит конверта трогает лишний путь" in outcome.message
    final = world.state.ops[key]["finalize_pr"]
    assert world.forge.prs[final]["state"] == "OPEN"


def test_live_request_with_old_pin_invalidated(
    world: World, stand: Stand, monkeypatch
) -> None:
    """Приёмка №8, №9, №44: пин живой заявки ≠ текущей активации."""
    key, _op, _pr = _candidate(world)
    newer = ActivationView(("activation", "b" * 40), "b" * 40, NOW)
    monkeypatch.setattr(
        act_status,
        "mode_probe",
        lambda state, deps=None: replace(stand.live(), activation=newer),
    )
    with pytest.raises(RuntimeError, match="policy_changed"):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED


# --- доставка ------------------------------------------------------------------------


def _deliver(world: World, stand: Stand) -> tuple[str, int]:
    key, _op, pr = _candidate(world)
    stand.approve_head(pr)
    approve(world, "charter")  # агентский мерж candidate
    return key, pr


def test_accepted_records_act_and_completes_with_envelope(
    world: World, stand: Stand
) -> None:
    """Приёмка №1: акт засчитан, конверт доставлен, заявка завершена."""
    stand.unverified = ("allow_rebase_merge",)
    key, _pr = _deliver(world, stand)
    approve(world, "charter")
    op = world.state.ops[key]
    assert op["status"] == al.STATUS_COMPLETED
    assert op["review_id"] == "11" and op["approved_by"] == "alice"
    assert op["unverified_premises"] == ["allow_rebase_merge"]
    assert op["trust_root"]["snapshot_sha"] == ROOT.snapshot_sha
    world.sync()
    assert (world.target / envelope_path(WS_ID, "charter")).is_file()


def test_verified_premise_written_as_empty_list(world: World, stand: Stand) -> None:
    key, _pr = _deliver(world, stand)
    approve(world, "charter")
    assert world.state.ops[key]["unverified_premises"] == []


def test_rejected_delivery_invalidates(world: World, stand: Stand) -> None:
    key, _pr = _deliver(world, stand)
    stand.verdict = Rejected("d3_merger", "мержер вне allowlist")
    with pytest.raises(RuntimeError, match="d3_merger"):
        approve(world, "charter")
    assert world.state.ops[key]["reason"].startswith("d3_merger")


@pytest.mark.parametrize("verdict", [Unavailable("http: 502"), Ambiguous()])
def test_undecided_delivery_keeps_request_alive(
    world: World, stand: Stand, verdict
) -> None:
    key, _pr = _deliver(world, stand)
    stand.verdict = verdict
    with pytest.raises(RuntimeError) as err:
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_STARTED
    if isinstance(verdict, Ambiguous):
        assert "--repropose" in str(err.value)
    stand.verdict = None
    approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_COMPLETED


def test_legacy_candidate_merged_after_review_activation_refused(
    world: World, stand: Stand
) -> None:
    """Приёмка №24: candidate без манифеста после активации review — admission, через
    настоящий диспетчер шага."""
    stand.mode = act_trust.PRE_MIGRATION
    key, _op, pr = _candidate(world)  # заявка без маркера
    merge_pr(world, pr)
    stand.mode = "review"
    stand.verdict = Rejected("admission", "ACT_MODE=review: заявка без маркера")
    with pytest.raises(RuntimeError, match="admission"):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED


def test_frontmatter_noop_refused_when_mode_unavailable(
    world: World, stand: Stand
) -> None:
    """d1 (D1-1): узел честно одобрен по frontmatter, режим акта не установлен — отказ сразу
    после `mode_probe`, до любого no-op (успеха без актуального допуска нет)."""
    stand.mode = act_trust.PRE_MIGRATION
    drive_to_approved(world, "charter")
    stand.mode_unset = "нет привязки корня доверия"
    with pytest.raises(
        RuntimeError, match="режим акта продукта .узел charter.: нет привязки"
    ):
        approve(world, "charter")


def test_frontmatter_noop_in_review_needs_status(
    world: World, stand: Stand, monkeypatch: pytest.MonkeyPatch
) -> None:
    """d1: продукт в `review`, узел без события `review/v1` честно одобрен по frontmatter —
    no-op только при актуальном статусе (provenance старого акта, §9.1 (а) п. 3); иначе —
    новая заявка, не no-op."""
    stand.mode = act_trust.PRE_MIGRATION
    drive_to_approved(world, "charter")
    stand.mode = "review"
    monkeypatch.setattr(
        act_status.Statuses, "of", lambda self, node: act_status.APPROVED
    )
    noop = approve(world, "charter")
    assert noop.message == "charter: честно одобрен — одобрять нечего (no-op)"
    monkeypatch.setattr(
        act_status.Statuses, "of", lambda self, node: "not_approved:legacy_provenance"
    )
    outcome = approve(world, "charter")
    assert outcome.request is not None and outcome.request in world.state.ops


def test_review_noop_needs_current_status(
    world: World, stand: Stand, monkeypatch: pytest.MonkeyPatch
) -> None:
    """d1: no-op узла `review/v1` — только по актуальному `Statuses.of`; остановка,
    доставленная после одобрения, даёт новую заявку, а не no-op."""
    monkeypatch.setattr(
        act_status.Statuses, "is_review", lambda self, node: node == "charter"
    )
    monkeypatch.setattr(
        act_status.Statuses, "of", lambda self, node: act_status.APPROVED
    )
    noop = approve(world, "charter")
    assert noop.message == "charter: честно одобрен (review/v1) — no-op"
    monkeypatch.setattr(
        act_status.Statuses, "of", lambda self, node: "not_approved:stopped:g1"
    )
    outcome = approve(world, "charter")
    assert outcome.request is not None and outcome.request in world.state.ops


def test_merged_unmarked_with_unset_mode_stays_alive(
    world: World, stand: Stand
) -> None:
    """B1-5: смёржен без маркера, режим продукта не установлен (откат review → merge) —
    заявка жива, старый код не продолжает."""
    stand.mode = act_trust.PRE_MIGRATION
    key, _op, pr = _candidate(world)
    merge_pr(world, pr)
    stand.mode_unset = "ACT_MODE: обратное переключение review → merge"
    with pytest.raises(RuntimeError, match="режим акта"):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_STARTED


def test_act_removed_from_ledger_with_manifest_in_head(
    world: World, stand: Stand
) -> None:
    """Приёмка №13 / P1-2: зеркало `act` удалено, манифест в голове — отказ."""
    key, _pr = _deliver(world, stand)
    world.state.ops[key].pop("act")
    with pytest.raises(RuntimeError, match="зеркало act в run.json расходится"):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED


def _rewrite_manifest(world: World, op: dict, key: str, change) -> None:
    """Подменить манифест в голове candidate (ветка на origin) — до approve и мержа."""
    branch = op["branch"]
    _git(world.human, "fetch", "-q", "origin")
    _git(world.human, "switch", "-qC", branch, f"origin/{branch}")
    path = world.human / manifest_path(WS_ID, key)
    path.write_text(render_manifest(change(parse_manifest(path.read_text()))))
    _git(world.human, "commit", "-qam", "tamper manifest")
    _git(world.human, "push", "-q", "origin", branch)


def test_unreadable_manifest_in_head_invalidates(world: World, stand: Stand) -> None:
    """B3-2: файл манифеста в голове есть, но не разбирается — заявка invalidated
    (`_manifest`), а не «без маркера»."""
    key, op, pr = _candidate(world)
    branch = op["branch"]
    _git(world.human, "fetch", "-q", "origin")
    _git(world.human, "switch", "-qC", branch, f"origin/{branch}")
    (world.human / manifest_path(WS_ID, key)).write_text("schema: [битый\n")
    _git(world.human, "commit", "-qam", "broken manifest")
    _git(world.human, "push", "-q", "origin", branch)
    stand.approve_head(pr)
    with pytest.raises(RuntimeError, match="манифест в одобренном коммите не читается"):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED


BINDING_TWINS = {
    # компонент → (подмена манифеста, точная причина `_binding_refusal`)
    "product": (
        lambda r: replace(r, product="owner/other"),
        "манифест: product owner/other",
    ),
    "ws_id": (lambda r: replace(r, ws_id="ws-other"), "манифест: ws_id ws-other"),
    "node_path": (
        lambda r: replace(
            r,
            nodes={
                n: NodeEntry("elsewhere/x.md", e.content_hash, e.generation)
                for n, e in r.nodes.items()
            },
        ),
        "манифест: путь узла",
    ),
    "upstream_pins": (
        lambda r: replace(
            r, upstream_pins={n: {"charter": "f" * 40} for n in r.upstream_pins}
        ),
        "upstream-пины",
    ),
}


@pytest.mark.parametrize("component", sorted(BINDING_TWINS))
def test_foreign_identity_binding_invalidates(
    world: World, stand: Stand, component: str
) -> None:
    """B1-1/B1-14/B3-3: компонент идентичности подменён в САМОМ манифесте головы при
    прочих верных (ключ, зеркала `act`/политики, путь манифеста) — отказ именно
    `_binding_refusal` с причиной этого компонента."""
    change, reason = BINDING_TWINS[component]
    key, op, pr = _candidate(world)
    _rewrite_manifest(world, op, key, change)
    if component == "upstream_pins":
        # зеркало леджера согласовано с подменённым манифестом (B3-7): единственное
        # расхождение — манифест против прямых входов головы, то есть `_binding_refusal`
        world.state.ops[key]["upstream_pins"] = {
            n: {"charter": "f" * 40} for n in op["nodes"]
        }
    stand.approve_head(pr)
    with pytest.raises(RuntimeError, match=reason):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED


def test_recorded_act_differs_from_decision_unresolved(
    world: World, stand: Stand
) -> None:
    """B3-6 (`_record_act`): в леджере записан другой акт, чем решение сейчас — стоп,
    запись не переписывается и заявка не продвигается."""
    key, _pr = _deliver(world, stand)
    world.forge.agent_merge_rc_seq = [
        3
    ]  # finalize не смёржен: акт записан, заявка жива
    approve(world, "charter")
    world.state.ops[key]["review_id"] = "99"
    with pytest.raises(RuntimeError, match="записан акт 99/"):
        approve(world, "charter")
    assert world.state.ops[key]["review_id"] == "99"


def test_legacy_halt_is_unresolved(
    world: World, stand: Stand, monkeypatch: pytest.MonkeyPatch
) -> None:
    """b4 (`_legacy.verdict`): старый код не установил факт — заявка жива, причина его."""
    stand.mode = act_trust.PRE_MIGRATION
    key, _op, pr = _candidate(world)
    merge_pr(world, pr)
    stand.mode = "merge"
    monkeypatch.setattr(
        an, "_legacy_outcome", lambda *a: an.LegacyHalt("политика недоступна")
    )
    with pytest.raises(RuntimeError, match="политика недоступна"):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_STARTED


def test_envelope_commit_not_made_is_refused(
    world: World, stand: Stand, monkeypatch: pytest.MonkeyPatch
) -> None:
    """b4 (`_finalize`): коммит конверта не состоялся (HEAD не разрешается) — отказ,
    `finalize_head_sha` не записывается."""
    key, _pr = _deliver(world, stand)
    real = world.ops.rev_parse
    monkeypatch.setattr(
        world.ops,
        "rev_parse",
        lambda target, ref: None if ref == "HEAD" else real(target, ref),
    )
    with pytest.raises(RuntimeError, match="коммит конверта не состоялся"):
        approve(world, "charter")
    assert world.state.ops[key].get("finalize_head_sha") is None


def test_head_hashes_unreadable_node_unavailable(world: World) -> None:
    """b4 (`act_node._head_hashes`): байты узла в голове не читаются — `Unavailable`."""
    req = NodeRequest(
        "approve-1-0-1",
        WS_ID,
        SLUG,
        {"charter": NodeEntry("spec/00-charter.md", "h", 1)},
        {"charter": {}},
        PolicyPin("owner/approval-policy", "main", ACT, {}),
    )

    class _Ops:
        def show_file(self, target, ref, path):
            return None

    got = act_node._head_hashes(world.state, _Ops(), req, "h" * 40)
    assert isinstance(got, Unavailable) and got.reason.endswith(
        f"в {'h' * 12} не читается"
    )


def test_finalize_resume_reuses_envelope(world: World, stand: Stand) -> None:
    """Review Focus 5: конверт записан, finalize не смёржен — второго конверта нет."""
    key, _pr = _deliver(world, stand)
    world.forge.agent_merge_rc_seq = [3]  # первый агентский мерж finalize отказал
    approve(world, "charter")
    head = world.state.ops[key]["finalize_head_sha"]
    approve(world, "charter")
    op = world.state.ops[key]
    assert op["finalize_head_sha"] == head and op["status"] == al.STATUS_COMPLETED


def test_crash_after_finalize_merge_before_complete(
    world: World, stand: Stand, monkeypatch
) -> None:
    key, _pr = _deliver(world, stand)
    real = al.complete_request
    calls: list[int] = []

    def flaky(state, k):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("упали до complete")
        real(state, k)

    monkeypatch.setattr(al, "complete_request", flaky)
    with pytest.raises(RuntimeError, match="упали"):
        approve(world, "charter")
    approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_COMPLETED
    assert (
        len([n for n, r in world.forge.prs.items() if r["branch"].endswith("-final")])
        == 1
    )


def test_stop_between_act_and_finalize_merge_not_reported_approved(
    world: World, stand: Stand, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Решение владельца 01.11 (гонка stop/finalize): остановка поколения доставлена между
    актом и мержем finalize — заявка завершена (конверт в base), но «одобрены» не
    объявляется: ответ называет фактическое состояние узла."""
    key, _pr = _deliver(world, stand)
    monkeypatch.setattr(act_status.Statuses, "is_review", lambda self, node: True)
    monkeypatch.setattr(
        act_status.Statuses, "of", lambda self, node: "not_approved:stopped:g1"
    )
    with pytest.raises(
        RuntimeError, match="узлы не одобрены — узел charter: not_approved:stopped:g1"
    ):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_COMPLETED


def test_finalize_merged_outside_base_invalidates(world: World, stand: Stand) -> None:
    """B1-15: конверт верен, но merge-коммит finalize недостижим из базы — не «в base»."""
    key, _pr = _deliver(world, stand)
    world.ops.is_ancestor = lambda target, sha, ref: False  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="не в базу"):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED


def _substitute(env: Envelope, field: str) -> object:
    """Другое значение того же типа для компонента конверта."""
    value = getattr(env, field)
    if isinstance(value, int):
        return value + 7
    if isinstance(value, str):
        return value + "-x"
    return {**dict(value), "x": "y"}


@pytest.mark.parametrize("field", [f.name for f in fields(Envelope)])
def test_finalize_delivered_envelope_component_substituted_invalidates(
    world: World, stand: Stand, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    """B2-3: merge-коммит finalize несёт конверт с подменённым компонентом — заявка
    не завершается (вход `_reconcile_finalize`)."""
    key, _pr = _deliver(world, stand)
    real, path = world.ops.show_file, envelope_path(WS_ID, "charter")

    def tampered(target: str, ref: str, p: str) -> str | None:
        text = real(target, ref, p)
        fpr = world.state.ops[key].get("finalize_pr")
        merged = (world.forge.prs.get(fpr) or {}).get("mergeCommit") if fpr else None
        if p != path or text is None or not merged or ref != merged["oid"]:
            return text
        env = parse_envelope(text)
        return render_envelope(replace(env, **{field: _substitute(env, field)}))

    monkeypatch.setattr(world.ops, "show_file", tampered)
    with pytest.raises(RuntimeError, match="не тот конверт"):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED


@pytest.mark.parametrize(
    "touched,content",
    [
        ([envelope_path(WS_ID, "charter"), "spec/00-charter.md"], "x"),  # лишний путь
        ([], "x"),  # конверта в коммите нет
        ([envelope_path("ws-other", "charter")], "x"),  # конверт другого воркстрима
        ([envelope_path(WS_ID, "charter")], "y"),  # не то содержимое
    ],
    ids=["extra_path", "missing", "ws_id", "bytes"],
)
def test_envelope_commit_defect_twins(
    world: World, touched: list[str], content: str
) -> None:
    """B2-3: форма коммита конверта — ровно пути конвертов узлов заявки ЭТОГО
    воркстрима с ожидаемыми байтами (вход `envelope_commit_defect`)."""

    class _Ops:
        def commit_files(self, target, head):
            return touched

        def show_file(self, target, ref, path):
            return content

    assert (
        act_node.envelope_commit_defect(world.state, _Ops(), {"charter": "x"}, "h" * 40)
        is not None
    )
    ok = act_node.envelope_commit_defect(
        world.state,
        type(
            "_Ok",
            (),
            {
                "commit_files": lambda s, t, h: [envelope_path(WS_ID, "charter")],
                "show_file": lambda s, t, r, p: "x",
            },
        )(),
        {"charter": "x"},
        "h" * 40,
    )
    assert ok is None


# --- аудит и переоткрытие ------------------------------------------------------------


def test_late_dismissal_audited_once(world: World, stand: Stand) -> None:
    """Приёмка №10: решение не меняется, событие аудита одно."""
    key, _pr = _deliver(world, stand)
    approve(world, "charter")
    stand.dismissals = (
        v3.Dismissal("11", "alice", "2030-01-01T00:00:00Z", "APPROVED"),
    )
    assert act_node.audit_late_dismissals(world.state, world.ops, key) == 1
    assert act_node.audit_late_dismissals(world.state, world.ops, key) == 0
    assert (
        al.audit_events(world.state, key)[0]["event"]
        == "review_dismissed_after_delivery"
    )
    assert world.state.ops[key]["status"] == al.STATUS_COMPLETED


def test_reopen_writes_stop_of_current_generation(world: World, stand: Stand) -> None:
    _key, _pr = _deliver(world, stand)
    approve(world, "charter")
    msg = act_node.reopen(
        world.state, world.ops, an.bundle_dag.dag_for(None), "charter"
    )
    assert msg is not None and "g1" in msg
    stop_prs = [r for r in world.forge.prs.values() if r["branch"].startswith("stop/")]
    assert len(stop_prs) == 1 and stop_prs[0]["state"] == "MERGED"
    again = act_node.reopen(
        world.state, world.ops, an.bundle_dag.dag_for(None), "charter"
    )
    assert again is None  # действующее событие — уже остановка


def _foreign_stop_pr(world: World, files: dict[str, str]) -> int:
    """ОТКРЫТЫЙ PR на ветке остановки `charter` g1, открытый не `reopen`-ом."""
    branch = f"stop/{WS_ID}-charter-g1"
    _git(world.human, "fetch", "-q", "origin")
    _git(world.human, "switch", "-qC", branch, "origin/master")
    for rel, text in files.items():
        (world.human / rel).parent.mkdir(parents=True, exist_ok=True)
        (world.human / rel).write_text(text)
    _git(world.human, "add", "-A")
    _git(world.human, "commit", "-qm", "stop?")
    _git(world.human, "push", "-q", "origin", branch)
    return world.ops.create_pr(str(world.target), SLUG, branch, "t", "b", "")


_OWN_STOP = render_stop(
    StopRecord("charter", 1, act_node.STOP_REASON.format(node="charter"))
)


@pytest.mark.parametrize(
    "record",
    ["none", "other_node", "other_generation", "other_reason", "extra_file"],
)
def test_reopen_refuses_foreign_stop_pr_before_merge(
    world: World, stand: Stand, record: str
) -> None:
    """Находка безопасности (reopen foreign PR): на ветке stop-PR лежит ОТКРЫТЫЙ чужой
    PR с подменой (файла нет / остановка другого узла / другого поколения / другие
    байты / верная запись плюс лишний файл). Форма сверяется по ВСЕМУ диффу против
    базы ДО мержа — агентский мерж не вызывается, PR остаётся открытым."""
    _deliver(world, stand)
    approve(world, "charter")
    files = {
        "none": {"unrelated.txt": "x\n"},
        "other_node": {
            stop_path(WS_ID, "scope", 1): render_stop(StopRecord("scope", 1, "r"))
        },
        "other_generation": {
            stop_path(WS_ID, "charter", 2): render_stop(StopRecord("charter", 2, "r"))
        },
        "other_reason": {
            stop_path(WS_ID, "charter", 1): render_stop(
                StopRecord("charter", 1, "не тот повод")
            )
        },
        "extra_file": {
            stop_path(WS_ID, "charter", 1): _OWN_STOP,
            "spec/00-charter.md": "подменённый узел\n",
        },
    }[record]
    stop_pr = _foreign_stop_pr(world, files)
    calls = len(world.forge.merge_calls)
    with pytest.raises(RuntimeError, match=f"stop-PR #{stop_pr}.*разберите вручную"):
        act_node.reopen(world.state, world.ops, an.bundle_dag.dag_for(None), "charter")
    assert world.forge.prs[stop_pr]["state"] == "OPEN"
    assert len(world.forge.merge_calls) == calls


def test_reopen_extra_file_in_earlier_commit_refused(
    world: World, stand: Stand
) -> None:
    """Форма — по всем коммитам PR, не по голове: лишний файл в первом коммите,
    голова добавляет только верную запись — мерж не выполняется."""
    _deliver(world, stand)
    approve(world, "charter")
    branch = f"stop/{WS_ID}-charter-g1"
    _git(world.human, "fetch", "-q", "origin")
    _git(world.human, "switch", "-qC", branch, "origin/master")
    (world.human / "smuggled.txt").write_text("x\n")
    _git(world.human, "add", "-A")
    _git(world.human, "commit", "-qm", "smuggle")
    rel = stop_path(WS_ID, "charter", 1)
    (world.human / rel).parent.mkdir(parents=True, exist_ok=True)
    (world.human / rel).write_text(_OWN_STOP)
    _git(world.human, "add", "-A")
    _git(world.human, "commit", "-qm", "stop")
    _git(world.human, "push", "-q", "origin", branch)
    stop_pr = world.ops.create_pr(str(world.target), SLUG, branch, "t", "b", "")
    calls = len(world.forge.merge_calls)
    with pytest.raises(RuntimeError, match="разберите вручную"):
        act_node.reopen(world.state, world.ops, an.bundle_dag.dag_for(None), "charter")
    assert world.forge.prs[stop_pr]["state"] == "OPEN"
    assert len(world.forge.merge_calls) == calls


def test_reopen_well_formed_found_stop_pr_merged_at_verified_head(
    world: World, stand: Stand
) -> None:
    """Найденный stop-PR верной формы мержится агентом с пином проверенной головы."""
    _deliver(world, stand)
    approve(world, "charter")
    stop_pr = _foreign_stop_pr(world, {stop_path(WS_ID, "charter", 1): _OWN_STOP})
    head = world.forge.head_of(world.forge.prs[stop_pr]["branch"])
    base = world.forge.head_of("master")
    msg = act_node.reopen(
        world.state, world.ops, an.bundle_dag.dag_for(None), "charter"
    )
    assert msg is not None and f"PR #{stop_pr}" in msg
    assert world.forge.merge_calls[-1] == (stop_pr, head)
    # база пинуется той верхушкой, против которой сверялась форма (I1, ретаргет)
    assert world.ops.merge_bases[-1] == (stop_pr, base)
    assert world.forge.prs[stop_pr]["state"] == "MERGED"


def test_reopen_stop_pr_into_other_branch_refused(world: World, stand: Stand) -> None:
    """Ревью I1: верной формы stop-PR, но вливается не в базу прогона — агентский
    мерж не вызывается (форма, проверенная против master, — не форма PR в чужую
    ветку)."""
    _deliver(world, stand)
    approve(world, "charter")
    stop_pr = _foreign_stop_pr(world, {stop_path(WS_ID, "charter", 1): _OWN_STOP})
    world.forge.prs[stop_pr]["base"] = "release-x"
    calls = len(world.forge.merge_calls)
    with pytest.raises(RuntimeError, match="вливается в release-x.*разберите вручную"):
        act_node.reopen(world.state, world.ops, an.bundle_dag.dag_for(None), "charter")
    assert world.forge.prs[stop_pr]["state"] == "OPEN"
    assert len(world.forge.merge_calls) == calls


def test_reopen_stop_pr_base_unreadable_unresolved(world: World, stand: Stand) -> None:
    """Ревью I1: база stop-PR не названа — факт не установлен, мержа нет."""
    _deliver(world, stand)
    approve(world, "charter")
    stop_pr = _foreign_stop_pr(world, {stop_path(WS_ID, "charter", 1): _OWN_STOP})
    world.forge.prs[stop_pr]["base"] = None
    calls = len(world.forge.merge_calls)
    with pytest.raises(RuntimeError, match="факт не установлен.*база stop-PR"):
        act_node.reopen(world.state, world.ops, an.bundle_dag.dag_for(None), "charter")
    assert len(world.forge.merge_calls) == calls


def test_reopen_closed_stop_pr_names_remedy(world: World, stand: Stand) -> None:
    """Ревью M1: stop-PR на ветке закрыт без мержа, ветка на месте — отказ называет
    выход (удалить ветку остановки на remote, повторить); после удаления ветки
    `reopen` открывает свой stop-PR и доставляет остановку."""
    _deliver(world, stand)
    approve(world, "charter")
    branch = f"stop/{WS_ID}-charter-g1"
    stop_pr = _foreign_stop_pr(world, {"unrelated.txt": "x\n"})
    world.forge.prs[stop_pr]["state"] = "CLOSED"
    with pytest.raises(
        RuntimeError,
        match=f"stop-PR #{stop_pr} закрыт без мержа.*git push origin --delete {branch}"
        ".*повторите --reopen charter",
    ):
        act_node.reopen(world.state, world.ops, an.bundle_dag.dag_for(None), "charter")
    world.ops.delete_remote_branch(SLUG, branch)
    msg = act_node.reopen(
        world.state, world.ops, an.bundle_dag.dag_for(None), "charter"
    )
    assert msg is not None and f"PR #{stop_pr}" not in msg


def test_reopen_stop_branch_without_pr_names_remedy(world: World, stand: Stand) -> None:
    """Ревью M2: ветка остановки на remote есть, PR нет (упавший заход между push и
    созданием PR) — без усыновления: отказ называет выход, PR не создаётся."""
    _deliver(world, stand)
    approve(world, "charter")
    branch = f"stop/{WS_ID}-charter-g1"
    _git(world.human, "fetch", "-q", "origin")
    _git(world.human, "switch", "-qC", branch, "origin/master")
    rel = stop_path(WS_ID, "charter", 1)
    (world.human / rel).parent.mkdir(parents=True, exist_ok=True)
    (world.human / rel).write_text(_OWN_STOP)
    _git(world.human, "add", "-A")
    _git(world.human, "commit", "-qm", "stop без PR")
    _git(world.human, "push", "-q", "origin", branch)
    before = len(world.forge.prs)
    with pytest.raises(
        RuntimeError,
        match=f"ветка есть без stop-PR.*git push origin --delete {branch}",
    ):
        act_node.reopen(world.state, world.ops, an.bundle_dag.dag_for(None), "charter")
    assert len(world.forge.prs) == before


def test_review_stamp_keys_in_base_fail_closed_on_content(
    world: World, stand: Stand
) -> None:
    """Ревью M3: во frontmatter узла в base — штамп-ключи `review/v1`; хеш заявки
    (проекция старого акта) ≠ хешу головы в проекции `review/v1` — доставка
    отказывает по I_CONTENT, а не засчитывается."""
    from governance import act_decision

    _git(world.human, "fetch", "-q", "origin")
    _git(world.human, "switch", "-q", "master")
    _git(world.human, "reset", "-q", "--hard", "origin/master")
    path = world.human / "spec" / "00-charter.md"
    path.write_text(
        path.read_text().replace(
            "status: draft\n",
            "status: draft\napproval_act: review/v1\napproval_review_id: '5'\n",
        )
    )
    _git(world.human, "commit", "-qam", "stamp keys")
    _git(world.human, "push", "-q", "origin", "master")
    world.sync()
    key, _op, pr = _candidate(world)
    head = world.forge.head_of(world.forge.prs[pr]["branch"])
    req = parse_manifest(_show(world.target, f"{head}:{manifest_path(WS_ID, key)}"))
    hashes = act_node._head_hashes(world.state, world.ops, req, head)
    assert isinstance(hashes, dict)
    entry = req.nodes["charter"]
    # сравнение — против хеша леджера (манифест — его зеркало), заглушек решения нет
    assert world.state.ops[key]["content_hashes"]["charter"] == entry.content_hash
    assert hashes[entry.path] != entry.content_hash
    delivery = DeliveryView(
        True,
        "m" * 40,
        "2026-10-12T12:00:00Z",
        "df-reviewer",
        "Bot",
        head,
        True,
        {entry.path: ("b" * 40, "b" * 40)},
    )
    got = act_decision._delivery_refusal(req, head, delivery, hashes)
    assert isinstance(got, Rejected) and got.code == act_decision.I_CONTENT


def test_reopen_post_merge_record_check_is_independent(
    world: World, stand: Stand, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B1-15 (второй рубеж): даже если предпроверка формы пропустила подмену,
    доставка доказывается базой — запись на merge-коммите не та, узел остаётся
    одобренным."""
    _deliver(world, stand)
    approve(world, "charter")
    wrong = render_stop(StopRecord("charter", 1, "не тот повод"))
    stop_pr = _foreign_stop_pr(world, {stop_path(WS_ID, "charter", 1): wrong})
    monkeypatch.setattr(act_node, "stop_pr_defect", lambda *a: None)
    with pytest.raises(RuntimeError, match="записи остановки"):
        act_node.reopen(world.state, world.ops, an.bundle_dag.dag_for(None), "charter")
    assert world.forge.prs[stop_pr]["state"] == "MERGED"


def test_reopen_already_delivered_stop_is_idempotent(
    world: World, stand: Stand
) -> None:
    """B3-4: остановка уже доставлена — повтор `--reopen` ничего не делает и нового
    stop-PR не создаёт (ранний выход по действующему событию — штатный)."""
    _key, _pr = _deliver(world, stand)
    approve(world, "charter")
    assert act_node.reopen(
        world.state, world.ops, an.bundle_dag.dag_for(None), "charter"
    )
    before = len(world.forge.prs)
    assert (
        act_node.reopen(world.state, world.ops, an.bundle_dag.dag_for(None), "charter")
        is None
    )
    assert len(world.forge.prs) == before


def test_reopen_new_event_between_read_and_stop_check(
    world: World, stand: Stand, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B3-3: между первым чтением истории и проверкой остановки в базу пришло новое
    событие узла (заявка следующего поколения) — `reopen` не сообщает об остановке."""
    _key, _pr = _deliver(world, stand)
    approve(world, "charter")
    real = act_status.load_history
    calls: list[int] = []

    def racing(ops, state, base):
        history = real(ops, state, base)
        calls.append(1)
        if len(calls) < 2 or isinstance(history, Unavailable):
            return history
        current = next(e for e in reversed(history) if isinstance(e, DeliveredRequest))
        entry = current.request.nodes["charter"]
        newer = replace(
            current.request,
            request_id="approve-9-9-9",
            nodes={"charter": replace(entry, generation=entry.generation + 1)},
        )
        return (*history, DeliveredRequest("n" * 40, "h" * 40, newer))

    monkeypatch.setattr(act_status, "load_history", racing)
    with pytest.raises(RuntimeError, match="не эта остановка"):
        act_node.reopen(world.state, world.ops, an.bundle_dag.dag_for(None), "charter")


def test_reopen_refuses_stop_pr_outside_base(world: World, stand: Stand) -> None:
    """B2-3: запись остановки верна, но merge-коммит stop-PR недостижим из базы."""
    _key, _pr = _deliver(world, stand)
    approve(world, "charter")
    world.ops.is_ancestor = lambda target, sha, ref: False  # type: ignore[method-assign]
    branch = f"stop/{WS_ID}-charter-g1"
    with pytest.raises(
        RuntimeError,
        match=f"не в базу master.*разберите вручную.*git push origin --delete {branch}"
        ".*повторите --reopen charter",
    ):
        act_node.reopen(world.state, world.ops, an.bundle_dag.dag_for(None), "charter")


def test_reopen_found_stop_pr_merged_outside_base_names_remedy(
    world: World, stand: Stand
) -> None:
    """Fix round 2: найденный stop-PR смёржен не в базу — остановку он не доставит;
    при ветке на remote отказ называет выход, после удаления ветки `reopen`
    доставляет остановку своим stop-PR."""
    _deliver(world, stand)
    approve(world, "charter")
    branch = f"stop/{WS_ID}-charter-g1"
    stop_pr = _foreign_stop_pr(world, {stop_path(WS_ID, "charter", 1): _OWN_STOP})
    elsewhere = world.forge.head_of(branch)  # «merge-коммит» вне истории master
    world.forge.prs[stop_pr].update(
        state="MERGED",
        mergedBy={"login": "someone"},
        mergedAt="2026-10-12T13:00:00Z",
        mergeCommit={"oid": elsewhere},
    )
    with pytest.raises(
        RuntimeError,
        match=f"stop-PR #{stop_pr} смёржен не в базу master.*git push origin --delete "
        f"{branch}.*повторите --reopen charter",
    ):
        act_node.reopen(world.state, world.ops, an.bundle_dag.dag_for(None), "charter")
    world.ops.delete_remote_branch(SLUG, branch)
    msg = act_node.reopen(
        world.state, world.ops, an.bundle_dag.dag_for(None), "charter"
    )
    assert msg is not None and f"PR #{stop_pr}" not in msg


def test_reopen_stop_pr_retargeted_during_checks_wait_not_merged(
    world: World, stand: Stand, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix round 2: форма прошла, но за время ожидания обязательных проверок PR
    перенацелили на другую ветку — форма перепроверяется прямо перед мержем,
    агентский мерж не вызывается."""
    _deliver(world, stand)
    approve(world, "charter")
    stop_pr = _foreign_stop_pr(world, {stop_path(WS_ID, "charter", 1): _OWN_STOP})
    real_wait = an._await_required_checks

    def retarget_while_waiting(state, ops, pr, head):
        got = real_wait(state, ops, pr, head)
        world.forge.prs[stop_pr]["base"] = "release-x"
        return got

    monkeypatch.setattr(an, "_await_required_checks", retarget_while_waiting)
    calls = len(world.forge.merge_calls)
    with pytest.raises(RuntimeError, match="вливается в release-x.*разберите вручную"):
        act_node.reopen(world.state, world.ops, an.bundle_dag.dag_for(None), "charter")
    assert len(world.forge.merge_calls) == calls
    assert world.forge.prs[stop_pr]["state"] == "OPEN"


def test_reopen_of_legacy_node_is_old_path(world: World, stand: Stand) -> None:
    assert (
        act_node.reopen(world.state, world.ops, an.bundle_dag.dag_for(None), "charter")
        is None
    )


def test_reopen_mode_unset_refused(world: World, stand: Stand) -> None:
    """Режим продукта не установлен — есть ли что отнимать, неизвестно: отказ, а не
    «старый путь» (fail closed)."""
    _deliver(world, stand)
    approve(world, "charter")
    stand.mode_unset = "нет привязки корня доверия"
    before = len(world.forge.prs)
    with pytest.raises(RuntimeError, match="режим акта продукта .--reopen charter."):
        act_node.reopen(world.state, world.ops, an.bundle_dag.dag_for(None), "charter")
    assert len(world.forge.prs) == before


def test_reopen_outside_review_mode_is_old_path(world: World, stand: Stand) -> None:
    """Продукт не в `review`: событие `review/v1` одобрения не даёт (статус — старый
    предикат), отнимать нечего — stop-PR не создаётся."""
    _deliver(world, stand)
    approve(world, "charter")
    stand.mode = "merge"
    before = len(world.forge.prs)
    assert (
        act_node.reopen(world.state, world.ops, an.bundle_dag.dag_for(None), "charter")
        is None
    )
    assert len(world.forge.prs) == before


def test_unreadable_settings_refuse_creation(world: World, stand: Stand) -> None:
    """Настройки установки не читаются — отказ создания, а не заявка без маркера."""
    stand.settings = Unavailable("настройки акта: act.toml битый")  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="настройки акта"):
        approve(world, "charter")
    assert al.requests(world.state) == []


def test_approve_node_audits_late_dismissal(world: World, stand: Stand) -> None:
    """Приёмка №10: вызов `--approve-node` аудирует завершённые заявки `review/v1`;
    решение (no-op по актуальному статусу) не меняется."""
    key, _pr = _deliver(world, stand)
    approve(world, "charter")
    stand.dismissals = (
        v3.Dismissal("11", "alice", "2030-01-01T00:00:00Z", "APPROVED"),
    )
    out = approve(world, "charter")
    assert out.message == "charter: честно одобрен (review/v1) — no-op"
    assert [e["review_id"] for e in al.audit_events(world.state, key)] == ["11"]
    assert world.state.ops[key]["status"] == al.STATUS_COMPLETED


def test_dismissal_before_delivery_not_audited(world: World, stand: Stand) -> None:
    """Отзыв ДО доставки — не поздний: событие аудита не пишется."""
    key, _pr = _deliver(world, stand)
    approve(world, "charter")
    stand.dismissals = (
        v3.Dismissal("11", "alice", "2026-01-01T00:00:00Z", "APPROVED"),
    )
    assert act_node.audit_late_dismissals(world.state, world.ops, key) == 0
    assert al.audit_events(world.state, key) == []


def test_pr_body_mirror_mismatch_invalidates(world: World, stand: Stand) -> None:
    """Зеркало «тело PR» (§7): строка `policy-activation` подменена — заявка
    invalidated (`mirror_mismatch`), агентский мерж не вызывается."""
    key, _op, pr = _candidate(world)
    rec = world.forge.prs[pr]
    rec["body"] = rec["body"].replace(
        f"policy-activation: {ACT}", "policy-activation: x"
    )
    stand.approve_head(pr)
    with pytest.raises(RuntimeError, match="тело PR: policy-activation"):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED
    assert world.forge.merge_calls == []


def test_legacy_accepted_in_merge_mode_continues_old_path(
    world: World, stand: Stand
) -> None:
    """`LegacyAccepted` после admission (продукт в `merge`): старый код продолжает —
    конверт frontmatter, заявка завершена."""
    stand.mode = act_trust.PRE_MIGRATION
    key, _op, pr = _candidate(world)
    merge_pr(world, pr)
    stand.mode = "merge"
    approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_COMPLETED
    assert world.base_meta("00-charter.md")["status"] == "approved"


def test_reopen_stop_pr_without_approving_review_names_stop_procedure(
    world: World, stand: Stand
) -> None:
    """PR #671 (minor 1): у свежего stop-PR одобряющего ревью нет — отказ называет
    stop-PR и одну процедуру: scope-аттестация, затем `--reopen` (не finalize-PR и
    не `--approve-node`)."""
    _deliver(world, stand)
    approve(world, "charter")
    world.forge.default_reviews = []
    calls = len(world.forge.merge_calls)
    with pytest.raises(RuntimeError) as err:
        act_node.reopen(world.state, world.ops, an.bundle_dag.dag_for(None), "charter")
    text = str(err.value)
    stop_pr = next(
        n for n, r in world.forge.prs.items() if r["branch"].startswith("stop/")
    )
    assert f"stop-PR #{stop_pr}" in text and f"review-pr.sh {stop_pr}" in text
    assert "затем повторите `--reopen charter`" in text
    assert "finalize-PR" not in text and "--approve-node" not in text
    assert len(world.forge.merge_calls) == calls
