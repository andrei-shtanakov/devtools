"""Барьер миграции по форджу (§10, Q9; Task 16)."""

from __future__ import annotations

import pytest

from governance import act_barrier
from governance import approval_branches as ab
from governance import run_state as rs
from governance.act_identity import RequestIdentity
from governance.act_request import NodeEntry, NodeRequest, PolicyPin, render_manifest
from governance.brief_facts import BriefPrFacts, DefaultBranch, PrComment
from governance.facts import Fact, Outcome

P = "owner/p"
HEAD = "1" * 40
TIP = "9" * 40
CAND = ab.candidate_branch("ws-7", 2, 1, 1)
FINAL = ab.finalize_branch("ws-7", 2, 1, 1)
NODE = "workstreams/ws-7/spec/10-requirements.md"
NODE2 = "workstreams/ws-7/spec/20-design.md"
IDENT = RequestIdentity(P, "ws-7", "approve-2-1-1")
MARK = IDENT.migration_marker
PENDING = "---\nstatus: approval_pending\n---\nтело\n"


@pytest.fixture(autouse=True)
def _runs(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Локальные леджеры — только стенда, не настоящего out/governance-runs."""
    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")


def manifest(
    ws_id: str = "ws-7", request_id: str = "approve-2-1-1", product: str = P
) -> str:
    return render_manifest(
        NodeRequest(
            request_id,
            ws_id,
            product,
            {"requirements": NodeEntry(NODE, "h", 1)},
            {"requirements": {}},
            PolicyPin("owner/approval-policy", "main", "a" * 40, {}),
        )
    )


def signed(by: str = "alice", at: str = "2026-10-01T10:00:00Z") -> str:
    return f"---\nstatus: approved\napproved_by: {by}\napproved_at: '{at}'\n---\nтело\n"


class Ops:
    def __init__(self, prs: list[dict], **kw: object) -> None:
        self.prs = prs
        self.files: dict[tuple[str, str], str] = {
            (HEAD, NODE): PENDING,
            **kw.get("files", {}),
        }
        self.manifests: dict[str, str] = kw.get("manifests", {})  # head sha -> текст
        self.comments: dict[int, list[PrComment]] = kw.get("comments", {})
        self.pr_files: dict[int, tuple] = kw.get("pr_files", {})
        self.fail_list = kw.get("fail_list", False)
        self.posted: list[tuple[int, str]] = []

    def prs_by_head_prefix(self, slug: str, prefix: str) -> list[dict]:
        if self.fail_list:
            raise RuntimeError("graphql: pagination broken")
        return self.prs

    def pr_facts(self, slug: str, pr: int) -> dict:
        rec = next(p for p in self.prs if p["number"] == pr)
        return {
            "headRefOid": rec.get("head", HEAD),
            "mergedBy": {"login": "alice"},
            "mergedAt": "2026-10-01T10:00:00Z",
            "state": rec["state"],
        }

    def repo_file_fact(self, slug: str, sha: str, path: str) -> Fact[str]:
        if "/approvals/requests/" in path:
            text = self.manifests.get(sha)
        else:
            text = self.files.get((sha, path))
        return (
            Fact(Outcome.FOUND, text, "ok")
            if text
            else Fact(Outcome.ABSENT, None, "нет")
        )

    def brief_pr_fact(self, slug: str, pr: int) -> Fact[BriefPrFacts]:
        files = self.pr_files.get(pr, ((NODE, "MODIFIED"),))
        return Fact(
            Outcome.FOUND,
            BriefPrFacts(
                pr,
                "MERGED",
                "master",
                CAND,
                HEAD,
                files,
                "ai-prosto",
                "2026-10-01T10:05:00Z",
                "f" * 40,
            ),
            "ok",
        )

    def default_branch_fact(self, slug: str) -> Fact[DefaultBranch]:
        return Fact(Outcome.FOUND, DefaultBranch("master", TIP), "ok")

    def pr_comments_fact(self, slug: str, pr: int) -> Fact[list[PrComment]]:
        return Fact(Outcome.FOUND, self.comments.get(pr, []), "ok")

    def comment(self, slug: str, pr: int, body: str) -> None:
        self.posted.append((pr, body))


def run(ops: Ops, **kw: object) -> act_barrier.BarrierReport:
    args = {
        "humans": frozenset({"alice"}),
        "confirmed": frozenset(),
        "attested": ("fleet",),
        "draining": True,
        "runs": (),
    }
    args.update(kw)
    return act_barrier.live_unmarked(P, ops, **args)


def pr(number: int, branch: str, state: str, **kw: object) -> dict:
    return {
        "number": number,
        "headRefName": branch,
        "state": state,
        "title": "",
        "body": "",
        **kw,
    }


def test_identity_only_for_request_branches() -> None:
    """b4: идентичность заявки — только у веток формы candidate/finalize."""
    assert act_barrier._identity(P, CAND) == IDENT
    assert act_barrier._identity(P, FINAL) == IDENT
    assert act_barrier._identity(P, "brief/ws-7") is None


def test_without_attestation_incomplete() -> None:
    got = run(Ops([]), attested=())
    assert got.items == () and not got.complete
    assert got.gaps == ("draining других установок не подтверждён (--attest-drained)",)


def test_clean_and_attested_complete() -> None:
    got = run(Ops([]))
    assert got == act_barrier.BarrierReport((), True, ())


def test_draining_off_incomplete() -> None:
    got = run(Ops([]), draining=False)
    assert not got.complete and got.gaps == (
        "draining не включён (act.toml act_migration)",
    )


@pytest.mark.parametrize(
    "branch,item",
    [
        (
            CAND,
            "#5 candidate OPEN без маркера: манифеста нет в голове; маркера миграции нет",
        ),
        (
            FINAL,
            "#5 finalize OPEN без маркера: манифеста нет в голове; маркера миграции нет",
        ),
        ("brief/ws-7", "#5 brief-PR OPEN — заявка v1 без маркера"),
    ],
    ids=["candidate", "finalize", "brief"],
)
def test_open_unmarked_stage_is_live(branch: str, item: str) -> None:
    """`_marked` × NO_MANIFEST_FILE (candidate/finalize); `_judge` × brief OPEN."""
    assert run(Ops([pr(5, branch, "OPEN")])).items == (item,)


def test_open_review_candidate_is_not_a_barrier() -> None:
    got = run(Ops([pr(5, CAND, "OPEN")], manifests={HEAD: manifest()}))
    assert got.items == ()


@pytest.mark.parametrize(
    "text,reason",
    [
        ("act: review/v1\n", "манифест не разобран"),  # не манифест: строгий разбор
        ("schema: [не yaml\n", "манифест не разобран"),
        (manifest(ws_id="ws-8"), "манифест чужой заявки: манифест: ws_id"),
        (
            manifest(request_id="approve-2-1-2"),
            "манифест чужой заявки: манифест: request_id",
        ),
        (manifest(product="owner/q"), "манифест чужой заявки: манифест: product"),
    ],
    ids=["not_manifest", "bad_yaml", "ws_id", "request_id", "product"],
)
def test_malformed_or_foreign_manifest_is_not_a_marker(text: str, reason: str) -> None:
    """B1-13/B1-14/b4: найденный файл — ещё не манифест этой заявки; причина — своя."""
    got = run(Ops([pr(5, CAND, "OPEN")], manifests={HEAD: text}))
    assert len(got.items) == 1 and reason in got.items[0]


def test_merged_legacy_with_signed_node_is_resolved() -> None:
    ops = Ops(
        [pr(5, CAND, "MERGED"), pr(6, FINAL, "MERGED")], files={(TIP, NODE): signed()}
    )
    assert run(ops).items == ()


def test_merged_legacy_without_signature_in_base_is_live() -> None:
    ops = Ops(
        [pr(5, CAND, "MERGED"), pr(6, FINAL, "MERGED")],
        files={(TIP, NODE): signed(by="bob", at="2026-09-01T00:00:00Z")},
    )
    assert run(ops).items == (
        (f"#5 candidate MERGED: {NODE} — подпись другого акта — merged-but-unrecorded"),
    )


def test_unapproved_node_in_base_is_live() -> None:
    """B3-3: подпись этого акта есть, но статус узла в базе — не `approved` (`_signed`)."""
    text = signed().replace("status: approved", "status: approval_pending")
    ops = Ops(
        [pr(5, CAND, "MERGED"), pr(6, FINAL, "MERGED")], files={(TIP, NODE): text}
    )
    items = run(ops).items
    assert len(items) == 1 and "статус узла в базе не approved" in items[0]


def test_node_covered_later_is_resolved() -> None:
    ops = Ops(
        [pr(5, CAND, "MERGED"), pr(6, FINAL, "MERGED")],
        files={(TIP, NODE): signed(by="bob", at="2026-10-05T00:00:00Z")},
    )
    assert run(ops).items == ()


def test_partial_finalize_leaves_candidate_live() -> None:
    """B1-13: candidate отдал два узла; finalize подписал один — заявка не разобрана."""
    ops = Ops(
        [pr(5, CAND, "MERGED"), pr(6, FINAL, "MERGED")],
        pr_files={
            5: ((NODE, "MODIFIED"), (NODE2, "MODIFIED")),
            6: ((NODE, "MODIFIED"),),
        },
        files={(HEAD, NODE2): PENDING, (TIP, NODE): signed()},
    )
    got = run(ops)
    assert got.items == (
        (f"#5 candidate MERGED: {NODE2} — узла нет в базе — merged-but-unrecorded"),
    )


def test_empty_finalize_does_not_resolve() -> None:
    """B1-13: состав берётся из candidate, а не из (пустого) finalize."""
    ops = Ops([pr(5, CAND, "MERGED"), pr(6, FINAL, "MERGED")], pr_files={6: ()})
    assert run(ops).items == (
        (f"#5 candidate MERGED: {NODE} — узла нет в базе — merged-but-unrecorded"),
    )


def test_candidate_without_pending_nodes_is_live() -> None:
    ops = Ops(
        [pr(5, CAND, "MERGED"), pr(6, FINAL, "MERGED")], files={(HEAD, NODE): signed()}
    )
    got = run(ops)
    assert len(got.items) == 1 and "узлов на одобрении не найдено" in got.items[0]


@pytest.mark.parametrize(
    "finalize", [None, "OPEN", "CLOSED"], ids=["absent", "open", "closed"]
)
def test_merged_candidate_without_finalize_is_live(finalize: str | None) -> None:
    """B3-3: finalize нет / открыт / закрыт без мержа — заявка не разобрана."""
    prs = [pr(5, CAND, "MERGED")] + ([pr(6, FINAL, finalize)] if finalize else [])
    got = run(Ops(prs, files={(TIP, NODE): signed()}))
    assert got.items[0] == (
        "#5 candidate MERGED без смёрженного finalize — pending-finalize"
    )


def test_human_migration_marker_resolves() -> None:
    marker = PrComment("c1", "alice", MARK, "2026-10-10T00:00:00Z", None)
    assert run(Ops([pr(5, CAND, "OPEN")], comments={5: [marker]})).items == ()


@pytest.mark.parametrize(
    "comment,reason",
    [
        (PrComment("c1", "ai-prosto", MARK, "t", None), "маркер не от human_approvers"),
        (PrComment("c1", "alice", MARK, "t", "2026-10-11"), "маркер правлен"),
        (
            PrComment("c1", "alice", "<!-- act-migrated: approve-2-1-1 -->", "t", None),
            "маркер не этой заявки",
        ),
        (
            PrComment(
                "c1",
                "alice",
                RequestIdentity(P, "ws-8", "approve-2-1-1").migration_marker,
                "t",
                None,
            ),
            "маркер не этой заявки",
        ),
        (
            PrComment(
                "c1",
                "alice",
                RequestIdentity(P, "ws-7", "approve-9-9-9").migration_marker,
                "t",
                None,
            ),
            "маркер не этой заявки",
        ),
        (
            PrComment(
                "c1",
                "alice",
                RequestIdentity("owner/q", "ws-7", "approve-2-1-1").migration_marker,
                "t",
                None,
            ),
            "маркер не этой заявки",
        ),
    ],
    ids=["author", "edited", "old_form", "ws_id", "request_id", "product"],
)
def test_marker_must_be_human_unedited_and_this_request(
    comment: PrComment, reason: str
) -> None:
    """B1-14/b4: маркер агента, правленый, старой формы или с чужой идентичностью —
    отвергнут с причиной своей проверки."""
    got = run(Ops([pr(5, CAND, "OPEN")], comments={5: [comment]}))
    assert len(got.items) == 1 and got.items[0].endswith(reason)


def test_merged_brief_needs_human_confirmation() -> None:
    ops = Ops([pr(7, "brief/ws-7", "MERGED")])
    got = run(ops)
    assert len(got.items) == 1 and "--confirm-resolved 7" in got.items[0]
    assert run(ops, confirmed=frozenset({7})).items == ()


def _brief_runs(monkeypatch, tmp_path, runs: dict[str, tuple[dict, bool]]) -> None:
    """Локальные прогоны: `interview.approval` и наличие штампа брифа."""
    from types import SimpleNamespace

    states = {
        rid: SimpleNamespace(
            interview={"approval": approval}, repo_slug=P, ws_id="ws-7", ops={}
        )
        for rid, (approval, _s) in runs.items()
    }
    for rid, (_a, stamped) in runs.items():
        if stamped:
            (tmp_path / rid / "brief-approval").mkdir(parents=True)
            (tmp_path / rid / "brief-approval" / "customer-brief.md").write_text("x")
    monkeypatch.setattr(rs, "all_run_ids", lambda: list(states))
    monkeypatch.setattr(rs, "load", lambda rid: states[rid])
    monkeypatch.setattr(rs, "run_dir", lambda rid: tmp_path / rid)


@pytest.mark.parametrize(
    "approval,stamped,reason",
    [
        ({"repo": P, "pr": 7}, True, None),
        (
            {"repo": "owner/q", "pr": 7},
            True,
            "нет прогона с interview.approval этого PR",
        ),
        ({"repo": P, "pr": 8}, True, "нет прогона с interview.approval этого PR"),
        ({"repo": P, "pr": 7}, False, "нет файла штампа брифа"),
    ],
    ids=["match", "other_product", "other_pr", "no_stamp"],
)
def test_local_brief_stamp_binds_product_pr_and_file(
    monkeypatch, tmp_path, approval, stamped, reason
) -> None:
    """B3-2/B3-3: штамп брифа доказывает разбор только своего продукта, своего PR и при
    наличии файла штампа (`_local_brief_stamp`); причина отказа — своя."""
    _brief_runs(monkeypatch, tmp_path, {"r1": (approval, stamped)})
    got = run(Ops([pr(7, "brief/ws-7", "MERGED")]))
    if reason is None:
        assert got.items == ()
    else:
        assert len(got.items) == 1 and reason in got.items[0]


def test_local_live_request_without_pr_is_item(monkeypatch) -> None:
    """B3-2: живая заявка локального леджера без маркера, не дошедшая до PR — пункт
    (`_local_items`); заявка другого продукта — нет."""
    from types import SimpleNamespace

    from governance import approval_ledger as al

    states = {
        "r1": SimpleNamespace(repo_slug=P),
        "r2": SimpleNamespace(repo_slug="owner/q"),
    }
    monkeypatch.setattr(rs, "load", lambda rid: states[rid])
    monkeypatch.setattr(al, "live_requests", lambda state: [((2, 1, 1), {})])
    got = run(Ops([]), runs=("r1", "r2"))
    assert got.items == ("r1:approve-2-1-1 — заявка без маркера до PR",)


def test_unreadable_local_ledger_incomplete(monkeypatch) -> None:
    """B3-2: нечитаемый леджер прогона — пробел полноты, а не «пусто» (`live_unmarked`)."""

    def broken(run_id):
        raise ValueError("битый run.json")

    monkeypatch.setattr(rs, "load", broken)
    got = run(Ops([]), runs=("r1",))
    assert not got.complete and "леджер r1 не читается" in got.gaps[0]


def test_broken_pagination_incomplete() -> None:
    got = run(Ops([], fail_list=True))
    assert not got.complete and got.items == ()
    assert got.gaps == ("список PR owner/p неполон: graphql: pagination broken",)


def test_invalidate_posts_marker_for_human_only() -> None:
    ops = Ops([pr(5, CAND, "OPEN")])
    act_barrier.invalidate(P, ops, 5, humans=frozenset({"alice"}), caller="alice")
    assert ops.posted == [(5, MARK)]
    with pytest.raises(RuntimeError, match="human_approvers"):
        act_barrier.invalidate(
            P, ops, 5, humans=frozenset({"alice"}), caller="ai-prosto"
        )


def test_invalidate_touches_only_selected_workstream(monkeypatch) -> None:
    """B1-14: тот же ключ approve-2-1-1 в двух воркстримах — инвалидируется один."""
    from types import SimpleNamespace

    from governance import approval_ledger as al

    states = {
        "r7": SimpleNamespace(
            repo_slug=P,
            ws_id="ws-7",
            ops={"approve-2-1-1": {"status": al.STATUS_STARTED}},
        ),
        "r8": SimpleNamespace(
            repo_slug=P,
            ws_id="ws-8",
            ops={"approve-2-1-1": {"status": al.STATUS_STARTED}},
        ),
    }
    hit: list[str] = []
    monkeypatch.setattr(rs, "all_run_ids", lambda: list(states))
    monkeypatch.setattr(rs, "load", lambda run_id: states[run_id])
    monkeypatch.setattr(
        al, "invalidate_request", lambda state, key, reason: hit.append(state.ws_id)
    )
    act_barrier.invalidate(
        P, Ops([pr(5, CAND, "OPEN")]), 5, humans=frozenset({"alice"}), caller="alice"
    )
    assert hit == ["ws-7"]


def test_review_preview_unavailable_product_incomplete() -> None:
    good = Ops([pr(5, CAND, "OPEN")], manifests={HEAD: manifest()})
    bad = Ops([], fail_list=True)
    got = act_barrier.live_review_requests({"owner/p": good, "owner/b": bad})
    assert len(got.items) == 1 and not got.complete
    assert got.gaps == ("owner/b: список PR неполон — graphql: pagination broken",)


class _Unreadable(Ops):
    """Факты форджи не установлены (сеть) — пробел полноты, а не «пусто»."""

    def __init__(self, prs: list[dict], *, what: str, **kw: object) -> None:
        super().__init__(prs, **kw)
        self.what = what

    def pr_facts(self, slug: str, pr: int) -> dict:
        facts = super().pr_facts(slug, pr)
        return {**facts, "headRefOid": None} if self.what == "head" else facts

    def repo_file_fact(self, slug: str, sha: str, path: str) -> Fact[str]:
        if self.what == "manifest" and "/approvals/requests/" in path:
            return Fact(Outcome.UNAVAILABLE, None, "сеть")
        if self.what == "file" and sha == HEAD and path == NODE:
            return Fact(Outcome.UNAVAILABLE, None, "сеть")
        return super().repo_file_fact(slug, sha, path)

    def pr_comments_fact(self, slug: str, pr: int) -> Fact[list[PrComment]]:
        if self.what == "comments":
            return Fact(Outcome.UNAVAILABLE, None, "сеть")
        return super().pr_comments_fact(slug, pr)


@pytest.mark.parametrize(
    "what,gap",
    [
        ("head", "#5: голова не прочитана"),
        ("manifest", "#5: манифест не прочитан — сеть"),
    ],
    ids=["head", "manifest"],
)
def test_unreadable_head_or_manifest_is_a_gap(what: str, gap: str) -> None:
    """`_marked`: голова PR или манифест не прочитаны — пробел, а не «без маркера»."""
    got = run(_Unreadable([pr(5, CAND, "OPEN")], what=what))
    assert got.items == () and not got.complete and got.gaps == (gap,)


def test_unreadable_comments_are_a_gap() -> None:
    """`_migrated`: комментарии PR не прочитаны — маркер не установлен, пробел."""
    got = run(_Unreadable([pr(5, CAND, "OPEN")], what="comments"))
    assert got.items == () and got.gaps == ("#5: комментарии не прочитаны — сеть",)


def test_unreadable_candidate_file_is_a_gap() -> None:
    """`_candidate_nodes`: файл диффа candidate в его голове не прочитан — состав не
    установлен, пробел (а не «узлов нет»)."""
    ops = _Unreadable([pr(5, CAND, "MERGED"), pr(6, FINAL, "MERGED")], what="file")
    got = run(ops)
    assert got.items == ()
    assert got.gaps == (f"#5: {NODE} в голове candidate не прочитан",)


def test_node_without_frontmatter_in_base_is_live() -> None:
    """`_signed`: узел в базе без разбираемого frontmatter — не одобрен (пункт), а не
    исключение."""
    ops = Ops(
        [pr(5, CAND, "MERGED"), pr(6, FINAL, "MERGED")],
        files={(TIP, NODE): "тело без frontmatter\n"},
    )
    items = run(ops).items
    assert len(items) == 1 and "статус узла в базе не approved" in items[0]


def test_unnamed_candidate_merger_is_a_gap() -> None:
    """`_legacy_resolved`: кто/когда смёржил candidate не прочитано — подпись не с чем
    сверять, пробел."""

    class Anonymous(Ops):
        def pr_facts(self, slug: str, pr: int) -> dict:
            return {**super().pr_facts(slug, pr), "mergedBy": None}

    ops = Anonymous(
        [pr(5, CAND, "MERGED"), pr(6, FINAL, "MERGED")], files={(TIP, NODE): signed()}
    )
    got = run(ops)
    assert got.items == ()
    assert got.gaps == ("#5: кто и когда смёржил candidate — не прочитано",)


def test_merged_finalize_found_among_closed_duplicates() -> None:
    """Ветка finalize с закрытым и смёрженным PR — смёрженный не теряется (порядок
    списка PR не решает)."""
    prs = [pr(5, CAND, "MERGED"), pr(6, FINAL, "MERGED"), pr(7, FINAL, "CLOSED")]
    assert run(Ops(prs, files={(TIP, NODE): signed()})).items == ()


def test_brief_stamp_proved_by_any_matching_run(monkeypatch, tmp_path) -> None:
    """Штамп доказывает любой прогон этого PR с файлом штампа, а не первый попавшийся."""
    _brief_runs(
        monkeypatch,
        tmp_path,
        {"r1": ({"repo": P, "pr": 7}, False), "r2": ({"repo": P, "pr": 7}, True)},
    )
    assert run(Ops([pr(7, "brief/ws-7", "MERGED")])).items == ()


def test_invalidate_refuses_non_request_pr() -> None:
    """Инвалидация — только PR формы candidate/finalize заявки; комментарий не пишется."""
    ops = Ops([pr(7, "brief/ws-7", "OPEN")])
    with pytest.raises(RuntimeError, match="не candidate/finalize заявки узла"):
        act_barrier.invalidate(P, ops, 7, humans=frozenset({"alice"}), caller="alice")
    assert ops.posted == []


class _CliOps(Ops):
    def caller_login(self) -> str | None:
        return "ai-prosto"


def _cli(monkeypatch, ops: Ops, live: object, argv: list[str]) -> int:
    from types import SimpleNamespace

    from governance import act_settings, act_status
    from governance import ops as ops_module

    monkeypatch.setattr(ops_module, "RealOps", lambda: ops)
    monkeypatch.setattr(act_status, "real_deps", lambda probe: object())
    monkeypatch.setattr(act_status, "live_policy", lambda probe, deps: live)
    monkeypatch.setattr(
        act_settings, "load_settings", lambda home: SimpleNamespace(draining=True)
    )
    return act_barrier.main(["--product", P, *argv])


def _live() -> object:
    from types import SimpleNamespace

    return SimpleNamespace(policy=SimpleNamespace(approvers=frozenset({"alice"})))


@pytest.mark.parametrize(
    "prs,argv,code",
    [
        ([], ["--attest-drained", "fleet"], 0),
        ([pr(5, CAND, "OPEN")], ["--attest-drained", "fleet"], 1),
        ([], [], 3),
    ],
    ids=["clean", "live", "unattested"],
)
def test_cli_exit_codes(monkeypatch, capsys, prs, argv, code) -> None:
    """Выходы CLI: 0 — пусто и полно; 1 — есть живые; 3 — неполно."""
    assert _cli(monkeypatch, _CliOps(prs), _live(), argv) == code


def test_cli_unavailable_activation_is_incomplete(monkeypatch, capsys) -> None:
    """Текущая активация не прочитана — `human_approvers` неизвестны: 3."""
    from governance.act_time import Unavailable

    got = _cli(monkeypatch, _CliOps([]), Unavailable("нет привязки"), ["--json"])
    assert got == 3 and "текущая активация — нет привязки" in capsys.readouterr().err


def test_cli_invalidate_by_agent_refused(monkeypatch, capsys) -> None:
    """`--invalidate` из контекста не человека из `human_approvers` — отказ 2, маркер
    не пишется."""
    ops = _CliOps([pr(5, CAND, "OPEN")])
    got = _cli(
        monkeypatch, ops, _live(), ["--invalidate", "5", "--attest-drained", "f"]
    )
    assert got == 2 and ops.posted == []
    assert "human_approvers" in capsys.readouterr().err


def test_cli_forge_failure_is_incomplete(monkeypatch, capsys) -> None:
    """Сбой чтения форджи (исключение `gh`) — 3 «неполно», а не 1 «есть живые»."""

    class Broken(_CliOps):
        def pr_facts(self, slug: str, pr: int) -> dict:
            raise RuntimeError("gh: сеть")

    got = _cli(
        monkeypatch, Broken([pr(5, CAND, "OPEN")]), _live(), ["--attest-drained", "f"]
    )
    assert got == 3 and "gh: сеть" in capsys.readouterr().err
