"""Provenance старого одобрения: отрицательные двойники (§9.1 (а) п. 3; b1 B1-3, b2 B2-1,
b3 B3-1/B3-3).

Сквозной путь — настоящие `LegacyProbe → Decider.legacy → delivery_verdict →
check_delivery → approve_node._legacy_outcome`; подменены только факты форджи,
снимок старой политики и контекст доверия (до активации — `OLD_READER`). Каждый
двойник ломает ровно один компонент при прочих верных и проверяет ТОЧНЫЙ код
своей проверки.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("steward")

from governance import act_status  # noqa: E402
from governance import approval_branches as ab  # noqa: E402
from governance import approval_facts as af  # noqa: E402
from governance import node_approval as na  # noqa: E402
from governance.act_check import LegacyAccepted  # noqa: E402
from governance.act_decision import Rejected  # noqa: E402
from governance.brief_facts import BriefPrFacts  # noqa: E402
from governance.facts import Fact, Outcome  # noqa: E402
from governance.ops import RealOps  # noqa: E402
from tests.act_cases_facts import PRODUCT, context, sha  # noqa: E402
from tests.test_act_context import NoEvents, _trust, _wire  # noqa: E402

NODE = "spec/10-requirements.md"
DAG = (("10-requirements.md", ()),)
CAND = ab.candidate_branch("ws-7", 1, 0, 1)
FINAL = ab.finalize_branch("ws-7", 1, 0, 1)
AT = "2026-09-10T08:00:00Z"
PSHA, FP = "e" * 40, "fp-1"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _text(
    status: str,
    by: str = "",
    at: str = "",
    body: str = "тело",
    pins: dict | None = None,
) -> str:
    pin_lines = "".join(f"  {k}: '{v}'\n" for k, v in (pins or {}).items())
    pins_block = (
        f"upstream_hashes:\n{pin_lines}" if pin_lines else "upstream_hashes: {}\n"
    )
    text = (
        f"---\nspec_stage: requirements\nstatus: {status}\nversion: 2\n"
        f"approved_by: '{by}'\napproved_at: '{at}'\n{pins_block}"
        f"approved_content_hash: ''\n---\n{body}\n"
    )
    return text.replace(
        "approved_content_hash: ''", f"approved_content_hash: '{na.self_hash(text)}'"
    )


def code(got: str) -> str:
    """Код проверки из ответа провайдера `not_approved:<код>:<деталь>`."""
    assert got.startswith("not_approved:"), got
    rest = got.removeprefix("not_approved:")
    return (
        ":".join(rest.split(":")[:2])
        if rest.startswith("legacy_admission:")
        else rest.split(":")[0]
    )


class World:
    """Настоящий git: candidate (approval_pending) → finalize (подпись) → база;
    леджер прогона с записью заявки и её пином старой политики."""

    def __init__(
        self, tmp: Path, *, final_body: str = "тело", final_pins: dict | None = None
    ) -> None:
        r = self.repo = tmp / "p"
        subprocess.run(["git", "init", "-q", "-b", "master", str(r)], check=True)
        _git(r, "config", "user.email", "t@e.st")
        _git(r, "config", "user.name", "t")
        (r / "spec").mkdir()
        (r / NODE).write_text(_text("draft"))
        _git(r, "add", "-A")
        _git(r, "commit", "-qm", "seed")
        self.seed = _git(r, "rev-parse", "HEAD")
        _git(r, "switch", "-qc", CAND)
        pending = _text(na.STATUS_APPROVAL_PENDING)
        (r / NODE).write_text(pending)
        _git(r, "commit", "-qam", "candidate")
        self.cand_head = _git(r, "rev-parse", "HEAD")
        _git(r, "switch", "-qc", FINAL)
        (r / NODE).write_text(_text("approved", "alice", AT, final_body, final_pins))
        _git(r, "commit", "-qam", "envelope")
        _git(r, "switch", "-q", "master")
        _git(r, "merge", "-q", "--no-ff", CAND, "-m", "Merge candidate")
        self.cand_merge = _git(r, "rev-parse", "HEAD")
        _git(r, "merge", "-q", "--no-ff", FINAL, "-m", "Merge finalize")
        self.signed = _git(r, "rev-parse", "HEAD")
        self.base_text = (r / NODE).read_text()
        self.final_branch = FINAL
        self.cand_files: tuple = ((NODE, "MODIFIED"),)
        self.cand_head_ref = self.cand_head
        self.candidate: dict | None = {
            "state": "MERGED",
            "mergedBy": {"login": "alice"},
            "mergedAt": AT,
        }
        self.signed_by_pr: dict[str, int] = {self.signed: 6}
        self.ledger: dict = {
            "approve-1-0-1": {
                "candidate_pr": 5,
                "nodes": ["requirements"],
                "branch": CAND,
                "content_hashes": {"requirements": na.self_hash(pending)},
                "upstream_pins": {"requirements": {}},
                "policy": {"sha": PSHA, "fingerprint": FP},
            }
        }
        self.allowlist = frozenset({"alice"})
        self.policy: object = None  # иначе — исход policy_snapshot
        self.mode = "merge"  # режим продукта на T мержа candidate (merge — OLD_READER)


class Ops(RealOps):
    """git настоящий; форджа — PR по merge-коммиту, ветки и файлы PR, candidate."""

    def __init__(self, w: World) -> None:
        self.w = w

    def prs_containing_commit(self, slug, sha_):
        pr = self.w.signed_by_pr.get(sha_)
        return (
            []
            if pr is None
            else [{"number": pr, "state": "MERGED", "mergeCommit": sha_}]
        )

    def brief_pr_fact(self, slug, pr):
        branch, files = (
            (self.w.final_branch, ((NODE, "MODIFIED"),))
            if pr == 6
            else (CAND, self.w.cand_files)
        )
        return Fact(
            Outcome.FOUND,
            BriefPrFacts(
                pr, "MERGED", "master", branch, "h" * 40, files, None, None, None
            ),
            "ok",
        )

    def find_pr(self, slug, branch, *, any_state=False):
        return 5 if self.w.candidate is not None and branch == CAND else None

    def pr_facts(self, slug, pr):
        return {
            **(self.w.candidate or {}),
            "headRefOid": self.w.cand_head_ref,
            "mergeCommit": {"oid": self.w.cand_merge},
        }


@pytest.fixture()
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def judge(w: World, monkeypatch: pytest.MonkeyPatch, text: str | None = None) -> str:
    """Сквозной вызов провайдера по узлу без события `review/v1`."""
    snapshot = af.PolicySnapshot(
        "owner/approval-policy", "main", "ALLOWLIST", PSHA, w.allowlist, FP
    )
    monkeypatch.setattr(
        af,
        "policy_snapshot",
        lambda ops, pinned_sha: w.policy or Fact(Outcome.FOUND, snapshot, "ok"),
    )
    ctx = context(modes={sha("p1"): w.mode}, request_act=None)
    _wire(monkeypatch, ctx)
    state = SimpleNamespace(
        repo_slug=PRODUCT,
        ws_id="ws-7",
        target_dir=str(w.repo),
        bundle_dir="spec",
        brief=None,
        ops=w.ledger,
        run_id="r",
        base_ref="master",
    )
    deps = act_status.Deps(
        readers=SimpleNamespace(anon=object()),
        binding=object(),
        journal=None,
        creds=(),
        gh=object(),
        now=None,
        sleep=None,
        settings=None,
    )
    ops = Ops(w)
    decider = act_status.Decider(
        state, ops, deps, DAG, trust=_trust(ctx), journal=NoEvents()
    )
    probe = act_status.LegacyProbe(state, ops, "master", decider.legacy, DAG)
    return probe(NODE, w.base_text if text is None else text)


def test_proven_legacy_act_before_activation_is_approved(world, monkeypatch) -> None:
    assert judge(world, monkeypatch) == "approved"


# --- provenance (LegacyProbe.__call__ / _finalize_branch / _candidate) ---------------


def test_no_edit_of_node_in_history_refused(world, monkeypatch) -> None:
    """B3-3: файла узла нет в истории базы (подпись не из базы) — `__call__`."""
    state = SimpleNamespace(repo_slug=PRODUCT, ws_id="ws-7", target_dir=str(world.repo))
    probe = act_status.LegacyProbe(state, Ops(world), "master", lambda pr, c: c(), DAG)
    assert code(probe("spec/99-absent.md", world.base_text)) == act_status.NO_EDIT


def test_candidate_identity_round_trips_ssot_form() -> None:
    """B3-2: форма candidate — только круговая подстановка в SSOT-шаблон."""
    assert act_status.candidate_identity(CAND) == ("ws-7", "approve-1-0-1")
    assert act_status.candidate_identity(FINAL) is None
    assert act_status.candidate_identity("spec/ws-7-approve-01-0-1") is None
    assert act_status.candidate_identity("feat/ws-7-approve-1-0-1") is None


def test_frontmatter_signature_differs_from_last_edit(world, monkeypatch) -> None:
    """Подпись frontmatter ≠ записи последней правки — отказ `__call__`, не позже."""
    text = world.base_text.replace("approved_by: 'alice'", "approved_by: 'bob'")
    assert code(judge(world, monkeypatch, text)) == act_status.NOT_SIGNATURE


def test_signature_not_through_pr_refused(world, monkeypatch) -> None:
    world.signed_by_pr = {}
    assert code(judge(world, monkeypatch)) == act_status.NOT_VIA_PR


def test_signature_without_finalize_pr_refused(world, monkeypatch) -> None:
    world.final_branch = "feat/forged"
    assert code(judge(world, monkeypatch)) == act_status.NOT_FINALIZE


def test_finalize_of_other_workstream_refused(world, monkeypatch) -> None:
    """B2-1: finalize/candidate чужого воркстрима — не акт этого узла."""
    world.final_branch = ab.finalize_branch("ws-8", 1, 0, 1)
    assert code(judge(world, monkeypatch)) == act_status.FOREIGN_WS


@pytest.mark.parametrize(
    "candidate,expected",
    [
        (None, act_status.NO_CANDIDATE),
        (
            {"state": "CLOSED", "mergedBy": {"login": "alice"}, "mergedAt": AT},
            act_status.CANDIDATE_NOT_MERGED,
        ),
        (
            {"state": "MERGED", "mergedBy": {"login": "bob"}, "mergedAt": AT},
            act_status.CANDIDATE_MERGER,
        ),
        (
            {
                "state": "MERGED",
                "mergedBy": {"login": "alice"},
                "mergedAt": "2026-09-10T09:00:00Z",
            },
            act_status.CANDIDATE_TIME,
        ),
    ],
    ids=["absent", "not_merged", "other_merger", "other_time"],
)
def test_candidate_absent_or_not_matching_signature(
    world, monkeypatch, candidate, expected
) -> None:
    world.candidate = candidate
    assert code(judge(world, monkeypatch)) == expected


def test_node_not_in_candidate_refused(world, monkeypatch) -> None:
    """B2-1: узел не среди файлов candidate — candidate его не выносил."""
    world.cand_files = (("spec/20-design.md", "MODIFIED"),)
    assert code(judge(world, monkeypatch)) == act_status.NOT_IN_CANDIDATE


@pytest.mark.parametrize(
    "case,expected",
    [
        ("absent", act_status.NO_LEDGER),
        ("other_candidate", act_status.LEDGER_CANDIDATE),
        ("node_absent", act_status.LEDGER_NODE),
    ],
    ids=["absent", "other_candidate", "node_absent"],
)
def test_ledger_record_binds_candidate_and_node(
    world, monkeypatch, case, expected
) -> None:
    """B3-1: пин старой политики — только из записи ЭТОЙ заявки леджера прогона."""
    op = world.ledger["approve-1-0-1"]
    if case == "absent":
        world.ledger.clear()
    elif case == "other_candidate":
        op["candidate_pr"] = 9
    else:
        op["nodes"] = ["design"]
    assert code(judge(world, monkeypatch)) == expected


# --- байтовая часть старого контракта (legacy_contract_refusal) ----------------------


def test_node_not_pending_in_candidate_refused(world, monkeypatch) -> None:
    """B2-1: в голове candidate узел не `approval_pending` — не было заявки на него."""
    world.cand_head_ref = world.seed
    assert code(judge(world, monkeypatch)) == act_status.NOT_PENDING


def test_node_bytes_changed_by_finalize_refused(tmp_path, monkeypatch) -> None:
    """B2-1: подписаны не те байты, что выносились (правка тела в finalize)."""
    assert (
        code(judge(World(tmp_path, final_body="другое тело"), monkeypatch))
        == act_status.BYTES
    )


def test_hash_field_not_matching_base_bytes_refused(world, monkeypatch) -> None:
    text = world.base_text.replace(
        "approved_content_hash: '", "approved_content_hash: 'x"
    )
    w = world
    (w.repo / NODE).write_text(text)
    _git(w.repo, "commit", "-qam", "tamper hash field")  # правка = новая подпись?
    w.signed_by_pr = {_git(w.repo, "rev-parse", "HEAD"): 6}
    assert code(judge(w, monkeypatch, text)) == act_status.HASH_FIELD


def test_signature_under_changed_pins_refused(tmp_path, monkeypatch) -> None:
    """B2-1: подпись под другими пинами, чем выносились."""
    w = World(tmp_path, final_pins={"charter": "d" * 40})
    assert code(judge(w, monkeypatch)) == act_status.PINS


# --- старый код авторизации (approve_node._legacy_outcome), B3-1 -------------------


def test_merger_outside_old_allowlist_refused(world, monkeypatch) -> None:
    """Подпись согласована, но мержер не в закреплённой версии allowlist — отказ."""
    world.allowlist = frozenset({"bob"})
    assert code(judge(world, monkeypatch)) == "legacy_signature"


def test_old_pin_missing_refused(world, monkeypatch) -> None:
    world.ledger["approve-1-0-1"].pop("policy")
    assert code(judge(world, monkeypatch)) == "legacy_unpinned"


def test_old_pin_substituted_is_unavailable(world, monkeypatch) -> None:
    """Прочитан не тот снимок, что закрепляла заявка (отпечаток) — не установлено."""
    world.ledger["approve-1-0-1"]["policy"]["fingerprint"] = "fp-other"
    assert judge(world, monkeypatch).startswith("unavailable:политика")


def test_old_pin_unavailable_is_unavailable(world, monkeypatch) -> None:
    world.policy = Fact(Outcome.UNAVAILABLE, None, "approval-policy: 502")
    assert judge(world, monkeypatch).startswith("unavailable:политика подписи")


def test_old_pin_superseded_is_policy_changed(world, monkeypatch) -> None:
    refusal = af.PolicyRefusal(af.POLICY_REFUSAL_SUPERSEDED, "версия сменилась")
    world.policy = Fact(Outcome.FORBIDDEN, refusal, "superseded")
    assert code(judge(world, monkeypatch)) == "policy_changed"


def test_snapshot_not_published_in_candidate_refused(world, monkeypatch) -> None:
    """Старый код: в голове candidate узел не в форме снимка заявки (хеш леджера)."""
    world.ledger["approve-1-0-1"]["content_hashes"]["requirements"] = "sha:other"
    assert code(judge(world, monkeypatch)) == "legacy_unpublished"


# --- admission и сам Decider.legacy ---------------------------------------------------


def test_candidate_delivered_after_activation_refused(world, monkeypatch) -> None:
    """Заявка без манифеста, доставленная после ACT_MODE=review, — отказ admission."""
    world.mode = "review"
    assert code(judge(world, monkeypatch)).startswith("legacy_admission:")


def test_decider_legacy_returns_contract_result(monkeypatch) -> None:
    """B2-1: `Decider.legacy` отдаёт результат старой проверки, не безусловный
    `LegacyAccepted` (настоящий `delivery_verdict`, OLD_READER)."""
    ctx = context(modes={sha("p1"): "merge"}, request_act=None)
    _wire(monkeypatch, ctx)
    state = SimpleNamespace(
        repo_slug=PRODUCT, ws_id="ws-7", target_dir="/t", bundle_dir="spec"
    )
    deps = act_status.Deps(
        readers=SimpleNamespace(anon=object()),
        binding=object(),
        journal=None,
        creds=(),
        gh=object(),
        now=None,
        sleep=None,
        settings=None,
    )
    decider = act_status.Decider(
        state, object(), deps, (), trust=_trust(ctx), journal=NoEvents()
    )
    refused = Rejected(act_status.PINS, "пины")
    assert decider.legacy(5, lambda: refused) is refused
    assert decider.legacy(5, lambda: LegacyAccepted("alice")) == LegacyAccepted("alice")


def test_unsigned_last_edit_is_not_a_signature(world, monkeypatch) -> None:
    """Подпись без `approved_by`/`approved_at` — не подпись, даже если последняя
    правка узла несёт ту же пустоту (`__call__`, до поиска PR)."""
    text = _text("approved")
    (world.repo / NODE).write_text(text)
    _git(world.repo, "commit", "-qam", "unsigned approved")
    world.signed_by_pr = {_git(world.repo, "rev-parse", "HEAD"): 6}
    assert code(judge(world, monkeypatch, text)) == act_status.NOT_SIGNATURE


def test_old_code_not_run_after_activation(world, monkeypatch) -> None:
    """I-1 ревью Task 14: доставка candidate после активации — отказ admission, а
    старый код (`_legacy_outcome`, его `fetch_branch`) не исполняется вовсе."""
    from governance import approve_node

    world.mode = "review"
    monkeypatch.setattr(
        approve_node,
        "_legacy_outcome",
        lambda *a, **k: pytest.fail("старый код после активации"),
    )
    monkeypatch.setattr(
        Ops,
        "fetch_branch",
        lambda self, *a: pytest.fail("fetch_branch после активации"),
    )
    assert code(judge(world, monkeypatch)).startswith("legacy_admission:")


def test_candidate_identity_unknown_placeholder_is_not_candidate(monkeypatch) -> None:
    """M-6 ревью Task 14: шаблон SSOT с неизвестным плейсхолдером — ветка той же
    формы не candidate (`None`), а не `KeyError` из круговой подстановки."""
    monkeypatch.setattr(
        ab,
        "candidate_template",
        lambda: "spec/{ws_id}-approve-{wave}-{step}-{attempt}-{extra}",
    )
    assert act_status.candidate_identity("spec/ws-7-approve-1-0-1-5") is None
