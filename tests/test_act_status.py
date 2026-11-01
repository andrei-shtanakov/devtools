"""Статус узла review/v1 (§9.1; приёмка №17, 23, 25, 31; каскад, supersede; b1)."""

from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("steward")

from governance import act_context, act_status, bundle_inputs  # noqa: E402
from governance.act_decision import Accepted, Rejected  # noqa: E402
from governance.act_envelope import (  # noqa: E402
    DeliveredRequest,
    DeliveredStop,
    Envelope,
    StopRecord,
    envelope_path,
    parse_envelope,
    render_envelope,
    render_stop,
    stop_path,
)
from governance.act_projection import self_hash_v  # noqa: E402
from governance.act_request import (  # noqa: E402
    NodeEntry,
    NodeRequest,
    PolicyPin,
    manifest_path,
    render_manifest,
)
from governance.act_time import Ambiguous, Unavailable  # noqa: E402
from governance.facts import Fact, Outcome  # noqa: E402
from governance.ops import RealOps  # noqa: E402
from tests.test_act_context import NoEvents  # noqa: E402

WS = "ws-7"
SLUG = "owner/p"
DAG = (("10-requirements.md", ()), ("15-behaviour-spec.md", ("requirements",)))
FILES = {"requirements": "10-requirements.md", "behaviour-spec": "15-behaviour-spec.md"}
PIN = PolicyPin(
    "owner/approval-policy", "main", "p" * 40, {"policy/modes.yaml": "0" * 64}
)


def node_text(name: str, body: str = "тело", status: str = "approval_pending") -> str:
    return f"---\nspec_stage: {name}\nstatus: {status}\nversion: 2\n---\n{body}\n"


def request(
    rid: str,
    nodes: dict[str, tuple[str, int]],
    pins=None,
    *,
    path_of=None,
    product: str = SLUG,
    ws_id: str = WS,
) -> NodeRequest:
    path_of = path_of or (lambda n: f"spec/{FILES[n]}")
    entries = {n: NodeEntry(path_of(n), h, g) for n, (h, g) in nodes.items()}
    return NodeRequest(
        rid, ws_id, product, entries, pins or {n: {} for n in nodes}, PIN
    )


def envelope(event: DeliveredRequest, node: str, review_id: str = "11") -> str:
    req = event.request
    return render_envelope(
        Envelope(
            node=node,
            request_id=req.request_id,
            generation=req.nodes[node].generation,
            manifest_path=manifest_path(req.ws_id, req.request_id),
            head_sha=event.head_sha,
            self_hash=req.nodes[node].content_hash,
            upstream_pins=dict(req.upstream_pins[node]),
            merge_commit=event.merge_commit,
            review_id=review_id,
            policy=req.policy.as_record(),
        )
    )


class Snapshot:
    """Байты снимка: путь → текст; прямые входы узла — словарь."""

    def __init__(self, files: dict[str, str], blobs: dict[str, dict[str, str]]) -> None:
        self.files, self.blobs = files, blobs

    def text(self, path: str) -> str | None:
        return self.files.get(path)


def statuses(history, snap: Snapshot, decide=None, legacy=None) -> act_status.Statuses:
    return act_status.Statuses(
        dag=DAG,
        ws_id=WS,
        history=history,
        node_path=lambda n: f"spec/{FILES[n]}",
        text_of=snap.text,
        blobs_of=lambda n: snap.blobs.get(n, {}),
        decide=decide
        or (lambda event: Accepted("m" * 40, "df-reviewer", "11", "alice")),
        legacy=legacy or (lambda path, text: "approved"),
    )


REQ_TEXT = node_text("requirements")
BEH_TEXT = node_text("behaviour-spec")
H_REQ, H_BEH = self_hash_v(REQ_TEXT, "review/v1"), self_hash_v(BEH_TEXT, "review/v1")


def delivered(rid: str, merge: str, nodes: dict, pins=None) -> DeliveredRequest:
    return DeliveredRequest(merge, "h" * 40, request(rid, nodes, pins))


def both_approved() -> tuple[tuple, Snapshot]:
    r1 = delivered("approve-1-0-1", "a" * 40, {"requirements": (H_REQ, 1)})
    r2 = delivered(
        "approve-2-1-1",
        "b" * 40,
        {"behaviour-spec": (H_BEH, 1)},
        {"behaviour-spec": {"requirements": "blob-req"}},
    )
    files = {
        "spec/10-requirements.md": REQ_TEXT,
        "spec/15-behaviour-spec.md": BEH_TEXT,
        envelope_path(WS, "requirements"): envelope(r1, "requirements"),
        envelope_path(WS, "behaviour-spec"): envelope(r2, "behaviour-spec"),
    }
    snap = Snapshot(files, {"behaviour-spec": {"requirements": "blob-req"}})
    return (r1, r2), snap


def test_review_nodes_approved_from_envelopes() -> None:
    history, snap = both_approved()
    st = statuses(history, snap)
    assert (st.of("requirements"), st.of("behaviour-spec")) == ("approved", "approved")


def test_missing_envelope_not_approved_even_if_frontmatter_says_approved() -> None:
    """Приёмка №23: без fallback на frontmatter."""
    history, snap = both_approved()
    del snap.files[envelope_path(WS, "behaviour-spec")]
    snap.files["spec/15-behaviour-spec.md"] = node_text(
        "behaviour-spec", status="approved"
    )
    assert statuses(history, snap).of("behaviour-spec").startswith("not_approved:")


def test_envelope_of_other_node_substituted() -> None:
    """Приёмка №17."""
    history, snap = both_approved()
    snap.files[envelope_path(WS, "behaviour-spec")] = snap.files[
        envelope_path(WS, "requirements")
    ]
    assert statuses(history, snap).of("behaviour-spec").startswith("not_approved:")


def test_previous_generation_envelope_with_same_bytes_refused() -> None:
    """Приёмка №31, supersede: вторая заявка с тем же содержимым — нужен её конверт."""
    (r1, r2), snap = both_approved()
    r3 = delivered(
        "approve-3-1-1",
        "c" * 40,
        {"behaviour-spec": (H_BEH, 2)},
        {"behaviour-spec": {"requirements": "blob-req"}},
    )
    assert "конверт другого поколения" in statuses((r1, r2, r3), snap).of(
        "behaviour-spec"
    )
    snap.files[envelope_path(WS, "behaviour-spec")] = envelope(r3, "behaviour-spec")
    assert statuses((r1, r2, r3), snap).of("behaviour-spec") == "approved"


def test_stop_makes_node_not_approved() -> None:
    """Приёмка №25: остановка — явное действие, акт не отменяется ретроактивно."""
    (r1, r2), snap = both_approved()
    stop = DeliveredStop("d" * 40, StopRecord("behaviour-spec", 1, "переоткрыт"))
    assert (
        statuses((r1, r2, stop), snap).of("behaviour-spec") == "not_approved:stopped:g1"
    )


def test_stopped_upstream_with_unchanged_bytes_blocks_downstream() -> None:
    (r1, r2), snap = both_approved()
    stop = DeliveredStop("d" * 40, StopRecord("requirements", 1, "переоткрыт"))
    st = statuses((r1, r2, stop), snap)
    assert st.of("behaviour-spec") == "not_approved:upstream_stopped:requirements"


def test_cascade_by_pins_without_a_history_record() -> None:
    history, snap = both_approved()
    snap.blobs["behaviour-spec"] = {"requirements": "blob-req-v2"}
    assert statuses(history, snap).of("behaviour-spec") == (
        "not_approved:узел behaviour-spec: upstream-пины изменились после одобрения"
    )


@pytest.mark.parametrize("verdict", [Unavailable("http: 502"), Ambiguous()])
def test_undecided_delivery_is_unavailable(verdict) -> None:
    history, snap = both_approved()
    st = statuses(history, snap, decide=lambda event: verdict)
    assert st.of("requirements").startswith("unavailable:")


def test_rejected_delivery_not_approved() -> None:
    history, snap = both_approved()
    st = statuses(history, snap, decide=lambda event: Rejected("d1_no_act"))
    assert st.of("requirements") == "not_approved:act:d1_no_act"


def test_history_unavailable_is_unavailable() -> None:
    _, snap = both_approved()
    assert (
        statuses(Unavailable("git"), snap).of("requirements").startswith("unavailable:")
    )


def test_legacy_node_needs_provenance_not_frontmatter() -> None:
    """B1-3 (§9.1 (а) п. 3): узел без события review/v1 с честным frontmatter
    одобрен ТОЛЬКО если provenance+admission старого акта установлены."""
    r2 = delivered(
        "approve-2-1-1",
        "b" * 40,
        {"behaviour-spec": (H_BEH, 1)},
        {"behaviour-spec": {"requirements": "blob-req"}},
    )
    legacy = node_text("requirements", status="approved").replace(
        "version: 2\n",
        "version: 2\napproved_by: bob\napproved_at: '2026-01-01T00:00:00Z'\n"
        f"approved_content_hash: {H_REQ}\n",
    )
    files = {
        "spec/10-requirements.md": legacy,
        "spec/15-behaviour-spec.md": BEH_TEXT,
        envelope_path(WS, "behaviour-spec"): envelope(r2, "behaviour-spec"),
    }
    snap = Snapshot(files, {"behaviour-spec": {"requirements": "blob-req"}})
    refused = statuses(
        (r2,), snap, legacy=lambda path, text: "not_approved:legacy_admission:x"
    )
    assert refused.of("requirements") == "not_approved:legacy_admission:x"
    assert refused.of("behaviour-spec") == "not_approved:upstream:requirements"
    proven = statuses((r2,), snap, legacy=lambda path, text: "approved")
    assert (proven.of("requirements"), proven.of("behaviour-spec")) == (
        "approved",
        "approved",
    )
    # `is_review` — то же различение для путей вне провайдера (approve_node)
    assert (proven.is_review("requirements"), proven.is_review("behaviour-spec")) == (
        False,
        True,
    )


def test_legacy_probe_not_called_for_debt_node() -> None:
    _, snap = both_approved()
    snap.files["spec/10-requirements.md"] = node_text("requirements", status="draft")
    st = statuses((), snap, legacy=lambda path, text: pytest.fail("provenance"))
    assert st.of("requirements") == "not_approved:debt_status"


ENVELOPE_SUBSTITUTES = {
    "node": "behaviour-spec",
    "request_id": "approve-9-9-9",
    "generation": 7,
    "manifest_path": "workstreams/ws-8/approvals/requests/approve-1-0-1.yaml",
    "head_sha": "e" * 40,
    "self_hash": "sha256:" + "0" * 64,
    "upstream_pins": {"charter": "f" * 40},
    "merge_commit": "f" * 40,
    "review_id": "99",
}


@pytest.mark.parametrize("field", [*sorted(ENVELOPE_SUBSTITUTES), "policy"])
def test_envelope_component_substituted_not_approved(field: str) -> None:
    """B2-3: подмена ЛЮБОГО компонента конверта при прочем верном — не approved
    (вход `Statuses.of` → `_envelope`)."""
    history, snap = both_approved()
    path = envelope_path(WS, "requirements")
    env = parse_envelope(snap.files[path])
    value = (
        ENVELOPE_SUBSTITUTES[field]
        if field != "policy"
        else {k: ("z" * 40 if isinstance(v, str) else v) for k, v in env.policy.items()}
    )
    snap.files[path] = render_envelope(replace(env, **{field: value}))
    reason = (
        "подставлен"
        if field == "node"
        else "другого поколения"
        if field in ("generation", "request_id")
        else "не совпадает с манифестом"
    )
    got = statuses(history, snap).of("requirements")
    assert (
        got.startswith("not_approved:") and reason in got
    )  # причина ЭТОЙ проверки (b3)


def test_envelope_of_other_workstream_not_used() -> None:
    """B2-3: конверт узла лежит только в approvals/ другого воркстрима — не approved."""
    history, snap = both_approved()
    snap.files[envelope_path("ws-8", "requirements")] = snap.files.pop(
        envelope_path(WS, "requirements")
    )
    assert "нет конверта" in statuses(history, snap).of("requirements")


@pytest.mark.parametrize(
    "order",
    [("requirements", "behaviour-spec"), ("behaviour-spec", "requirements")],
    ids=["approved_first", "refused_first"],
)
def test_statuses_memo_is_per_node(order) -> None:
    """B2-3: память `Statuses.of` — по узлу: вердикт одного не отдаётся другому."""
    history, snap = both_approved()
    del snap.files[envelope_path(WS, "behaviour-spec")]
    st = statuses(history, snap)
    got = {node: st.of(node) for node in order}
    assert got["requirements"] == "approved"
    assert "нет конверта" in got["behaviour-spec"]


def test_provider_twin_changed_candidate_bytes_not_approved() -> None:
    """§12 п. 3 steward: тот же ответ при изменённых байтах кандидата — не approved."""
    history, snap = both_approved()
    snap.files["spec/15-behaviour-spec.md"] = node_text("behaviour-spec", body="другое")
    assert statuses(history, snap).of("behaviour-spec") == (
        "not_approved:узел behaviour-spec: байты узла изменились после одобрения"
    )


# --- исторический Decider: идентичность и привязка (B1-1, B1-2) ---------------------


class _DecideOps:
    """Ops для Decider: манифест в merge-коммите и в head, PR коммита."""

    def __init__(self, delivered: dict[str, str], head: dict[str, str | None]) -> None:
        self.delivered, self.head = delivered, head

    def show_file(self, target, ref, path):
        return self.delivered.get(path) if ref == "m" * 40 else None

    def repo_file_fact(self, slug, sha, path):
        text = self.head.get(path)
        return (
            Fact(Outcome.FOUND, text, "ok")
            if text
            else Fact(Outcome.ABSENT, None, "нет")
        )

    def prs_containing_commit(self, slug, sha):
        return [{"number": 7, "state": "MERGED", "mergeCommit": sha}]


def _decider(
    monkeypatch,
    ops,
    *,
    blobs=None,
    delivered_by: str = "m" * 40,
    seen: list | None = None,
) -> act_status.Decider:
    state = SimpleNamespace(
        repo_slug=SLUG,
        ws_id=WS,
        target_dir="/t",
        bundle_dir="spec",
        run_id="r",
        base_ref="master",
    )
    monkeypatch.setattr(
        bundle_inputs,
        "direct_blobs",
        lambda state, ops, dag, node, ref: Fact(Outcome.FOUND, blobs or {}, "ok"),
    )

    def verdict(*a, **k):
        if seen is not None:
            seen.append(k)
        return (
            Accepted("m" * 40, "df-reviewer", "11", "alice"),
            SimpleNamespace(merge_commit=delivered_by),
        )

    monkeypatch.setattr(act_context, "delivery_verdict", verdict)
    deps = act_status.Deps(
        readers=None,
        binding=object(),
        journal=None,
        creds=(),
        gh=None,
        now=None,
        sleep=None,
        settings=None,
    )
    return act_status.Decider(state, ops, deps, DAG, trust=object(), journal=NoEvents())


def _event(req: NodeRequest) -> DeliveredRequest:
    return DeliveredRequest("m" * 40, "h" * 40, req)


def test_decider_accepts_bound_manifest(monkeypatch) -> None:
    req = request("approve-1-0-1", {"requirements": (H_REQ, 1)})
    text = render_manifest(req)
    path = manifest_path(WS, req.request_id)
    got = _decider(monkeypatch, _DecideOps({path: text}, {path: text}))(_event(req))
    assert isinstance(got, Accepted)


@pytest.mark.parametrize(
    "case",
    [
        "absent_in_head",
        "changed_in_head",
        "foreign_path",
        "foreign_product",
        "foreign_ws",
        "head_other_request",
        "pins_changed",
    ],
)
def test_decider_refuses_unbound_manifest(monkeypatch, case: str) -> None:
    """B1-1/B2-3: манифест только в merge-коммите / иной в head / путь узла не из DAG /
    чужой продукт / чужой воркстрим / в head манифест другой заявки / upstream-пины ≠
    прямым входам head — установленный отказ через исторический Decider."""
    kw = {
        "foreign_path": {"path_of": lambda n: "elsewhere/x.md"},
        "foreign_product": {"product": "owner/other"},
        "foreign_ws": {"ws_id": "ws-8"},
    }.get(case, {})
    reason = {
        "absent_in_head": "не в одобренном head",
        "changed_in_head": "≠ манифесту одобренного head",
        "foreign_path": "манифест: путь узла",
        "foreign_product": "манифест: product",
        "foreign_ws": "манифест: ws_id",
        "head_other_request": "≠ манифесту одобренного head",
        "pins_changed": "upstream-пины",
    }[case]
    req = request("approve-1-0-1", {"requirements": (H_REQ, 1)}, **kw)
    text = render_manifest(req)
    path = manifest_path(WS, req.request_id)
    other = render_manifest(request("approve-1-0-2", {"requirements": (H_REQ, 1)}))
    head = {
        "absent_in_head": {path: None},
        "changed_in_head": {path: text + "# x\n"},
        "head_other_request": {path: other},
    }.get(case, {path: text})
    blobs = {"charter": "f" * 40} if case == "pins_changed" else None
    got = _decider(monkeypatch, _DecideOps({path: text}, head), blobs=blobs)(
        _event(req)
    )
    assert isinstance(got, Rejected) and got.code == act_status.REQUEST_BINDING
    assert reason in got.detail  # причина ЭТОЙ проверки (b4)


def test_decider_judges_review_on_event_head(monkeypatch) -> None:
    """B2-3: ревью судится на голове доставленного события, не на чём-то ещё."""
    req = request("approve-1-0-1", {"requirements": (H_REQ, 1)})
    text, path = render_manifest(req), manifest_path(WS, req.request_id)
    seen: list = []
    _decider(monkeypatch, _DecideOps({path: text}, {path: text}), seen=seen)(
        _event(req)
    )
    assert seen[0]["head_sha"] == "h" * 40 and seen[0]["request"] == req


def test_decider_delivery_of_other_merge_commit_unavailable(monkeypatch) -> None:
    """B2-3: PR доставил другой merge-коммит, чем событие истории — не Accepted."""
    req = request("approve-1-0-1", {"requirements": (H_REQ, 1)})
    text, path = render_manifest(req), manifest_path(WS, req.request_id)
    got = _decider(
        monkeypatch, _DecideOps({path: text}, {path: text}), delivered_by="n" * 40
    )(_event(req))
    assert isinstance(got, Unavailable) and f"доставил {'n' * 40}" in got.reason


def test_decider_without_binding_trust_unavailable() -> None:
    """b5 (`Decider.trust`): нет привязки — доверие не грузится, `Unavailable` с причиной."""
    state = SimpleNamespace(
        repo_slug=SLUG, ws_id=WS, target_dir="/t", bundle_dir="spec"
    )
    deps = act_status.Deps(
        readers=None,
        binding=None,
        journal=None,
        creds=(),
        gh=None,
        now=None,
        sleep=None,
        settings=None,
    )
    got = act_status.Decider(state, object(), deps, DAG).trust()
    assert isinstance(got, Unavailable)
    assert got.reason == "нет привязки корня доверия (§6.1 п. 1)"


def test_decider_head_bytes_unreadable_unavailable(monkeypatch) -> None:
    """b4: байты узла в одобренном head не читаются — хеш проекции не вычислен."""
    req = request("approve-1-0-1", {"requirements": (H_REQ, 1)})
    decider = _decider(monkeypatch, _DecideOps({}, {}))
    got = decider._head_hashes(_event(req))
    assert (
        isinstance(got, Unavailable)
        and got.reason == f"spec/10-requirements.md в {'h' * 12} не читается"
    )


def test_blobs_reader_unavailable_names_node(monkeypatch) -> None:
    """b4: прямые входы узла не прочитаны — причина называет узел."""
    monkeypatch.setattr(
        bundle_inputs,
        "direct_blobs",
        lambda *a: Fact(Outcome.UNAVAILABLE, None, "объект недоступен"),
    )
    got = act_status._blobs(SimpleNamespace(), object(), DAG, "master")("requirements")
    assert isinstance(got, Unavailable)
    assert got.reason == "прямые входы requirements: объект недоступен"


def test_decider_memo_key_carries_full_identity(monkeypatch) -> None:
    """B2-3: ключ памяти — продукт, воркстрим, заявка и merge-коммит; другой merge-коммит
    той же заявки судится заново."""
    req = request("approve-1-0-1", {"requirements": (H_REQ, 1)})
    text, path = render_manifest(req), manifest_path(WS, req.request_id)
    seen: list = []
    ops = _DecideOps({path: text}, {path: text})
    ops.show_file = lambda target, ref, p: text  # манифест доставлен обоими коммитами
    decider = _decider(monkeypatch, ops, seen=seen)
    decider(_event(req))
    decider(DeliveredRequest("n" * 40, "h" * 40, req))
    assert set(decider._memo) == {
        (SLUG, WS, "approve-1-0-1", "m" * 40),
        (SLUG, WS, "approve-1-0-1", "n" * 40),
    }
    assert len(seen) == 2


@pytest.mark.parametrize("order", [(0, 1), (1, 0)], ids=["good_first", "bad_first"])
def test_decider_memo_is_per_request_not_per_commit(monkeypatch, order) -> None:
    """B1-2: два манифеста одного merge-коммита — вердикт каждого свой."""
    good = request("approve-1-0-1", {"requirements": (H_REQ, 1)})
    bad = request(
        "approve-1-0-2",
        {"requirements": (H_REQ, 2)},
        path_of=lambda n: "elsewhere/x.md",
    )
    texts = {manifest_path(WS, r.request_id): render_manifest(r) for r in (good, bad)}
    decider = _decider(monkeypatch, _DecideOps(texts, texts))
    events = [_event(good), _event(bad)]
    got = {events[i].request.request_id: decider(events[i]) for i in order}
    assert isinstance(got["approve-1-0-1"], Accepted)
    assert isinstance(got["approve-1-0-2"], Rejected)
    assert got["approve-1-0-2"].code == act_status.REQUEST_BINDING


# --- история базы (merge и squash, B1-9) — настоящий git ----------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


class GitOps(RealOps):
    """Настоящий git + форджа стенда: PR по merge-коммиту и его голова."""

    def __init__(self, prs: dict[str, tuple[int, str]]) -> None:
        self.prs = prs  # merge-коммит → (номер PR, голова)

    def prs_containing_commit(self, slug, sha):
        hit = self.prs.get(sha)
        return (
            []
            if hit is None
            else [{"number": hit[0], "state": "MERGED", "mergeCommit": sha}]
        )

    def pr_facts(self, slug, pr):
        head = next(h for n, h in self.prs.values() if n == pr)
        return {"headRefOid": head}


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "p"
    subprocess.run(["git", "init", "-q", "-b", "master", str(r)], check=True)
    _git(r, "config", "user.email", "t@e.st")
    _git(r, "config", "user.name", "t")
    (r / "README.md").write_text("x\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "init")
    return r


def _state(repo: Path) -> SimpleNamespace:
    return SimpleNamespace(repo_slug=SLUG, ws_id=WS, target_dir=str(repo))


def _branch(repo: Path, branch: str, files: dict[str, str]) -> str:
    _git(repo, "switch", "-qc", branch)
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", branch)
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "switch", "-q", "master")
    return head


def _merge(repo: Path, branch: str) -> str:
    _git(repo, "merge", "-q", "--no-ff", branch, "-m", f"Merge {branch}")
    return _git(repo, "rev-parse", "HEAD")


def _squash(repo: Path, branch: str) -> str:
    _git(repo, "merge", "-q", "--squash", branch)
    _git(repo, "commit", "-qm", f"{branch} (squash)")
    return _git(repo, "rev-parse", "HEAD")


def test_history_from_first_parent(repo: Path) -> None:
    req = request("approve-1-0-1", {"requirements": (H_REQ, 1)})
    head = _branch(
        repo, "cand", {manifest_path(WS, req.request_id): render_manifest(req)}
    )
    m1 = _merge(repo, "cand")
    _branch(repo, "final", {envelope_path(WS, "requirements"): "ignored: true\n"})
    _merge(repo, "final")
    stop = StopRecord("requirements", 1, "r")
    _branch(repo, "stop", {stop_path(WS, "requirements", 1): render_stop(stop)})
    m3 = _merge(repo, "stop")
    got = act_status.load_history(GitOps({m1: (7, head)}), _state(repo), "master")
    assert got == (DeliveredRequest(m1, head, req), DeliveredStop(m3, stop))


def test_history_supports_squash_delivery(repo: Path) -> None:
    """B1-9: голова одобренного коммита — из фактов PR, второй родитель не нужен."""
    req = request("approve-1-0-1", {"requirements": (H_REQ, 1)})
    head = _branch(
        repo, "cand", {manifest_path(WS, req.request_id): render_manifest(req)}
    )
    sq = _squash(repo, "cand")
    got = act_status.load_history(GitOps({sq: (7, head)}), _state(repo), "master")
    assert got == (DeliveredRequest(sq, head, req),)


@pytest.mark.parametrize("pull_ref", [True, False])
def test_history_fetches_squash_head_absent_locally(
    tmp_path: Path, pull_ref: bool
) -> None:
    """B2-4: squash-голова есть только в `refs/pull/N/head` форджи — в клоне её нет;
    читатель догружает её; без такого ref — `Unavailable`, а не «голова есть»."""
    origin = tmp_path / "origin"
    subprocess.run(["git", "init", "-q", "-b", "master", str(origin)], check=True)
    _git(origin, "config", "user.email", "t@e.st")
    _git(origin, "config", "user.name", "t")
    (origin / "README.md").write_text("x\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", "init")
    req = request("approve-1-0-1", {"requirements": (H_REQ, 1)})
    head = _branch(
        origin, "cand", {manifest_path(WS, req.request_id): render_manifest(req)}
    )
    sq = _squash(origin, "cand")
    if pull_ref:
        _git(origin, "update-ref", "refs/pull/7/head", head)
    _git(origin, "branch", "-D", "cand")
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", "--no-local", f"file://{origin}", str(clone)], check=True
    )
    absent = subprocess.run(
        ["git", "-C", str(clone), "cat-file", "-e", f"{head}^{{commit}}"], check=False
    )
    assert absent.returncode != 0  # головы в клоне действительно нет
    got = act_status.load_history(GitOps({sq: (7, head)}), _state(clone), "master")
    if pull_ref:
        assert got == (DeliveredRequest(sq, head, req),)
        _git(clone, "cat-file", "-e", f"{head}^{{commit}}")
    else:
        assert isinstance(got, Unavailable) and "не догружена" in got.reason


def test_history_manifest_commit_without_pr_unavailable(repo: Path) -> None:
    req = request("approve-1-0-1", {"requirements": (H_REQ, 1)})
    _branch(repo, "cand", {manifest_path(WS, req.request_id): render_manifest(req)})
    _squash(repo, "cand")
    got = act_status.load_history(GitOps({}), _state(repo), "master")
    assert isinstance(got, Unavailable) and "нужен ровно один" in got.reason


def test_manifest_edited_after_delivery_unavailable(repo: Path) -> None:
    req = request("approve-1-0-1", {"requirements": (H_REQ, 1)})
    path = manifest_path(WS, req.request_id)
    head = _branch(repo, "cand", {path: render_manifest(req)})
    m1 = _merge(repo, "cand")
    (repo / path).write_text(render_manifest(req) + "# x\n")
    _git(repo, "commit", "-qam", "edit")
    got = act_status.load_history(GitOps({m1: (7, head)}), _state(repo), "master")
    assert (
        isinstance(got, Unavailable)
        and "запись истории изменена после доставки" in got.reason
    )


@pytest.mark.parametrize("component", ["request_id", "ws_id"])
def test_history_manifest_off_its_path_unavailable(repo: Path, component: str) -> None:
    """B2-3: манифест лежит по пути другой заявки / манифест чужого воркстрима в
    approvals/ этого — событие не принимается (вход `_event`)."""
    req = request(
        "approve-1-0-1",
        {"requirements": (H_REQ, 1)},
        ws_id="ws-8" if component == "ws_id" else WS,
    )
    path = manifest_path(
        WS, "approve-1-0-2" if component == "request_id" else "approve-1-0-1"
    )
    head = _branch(repo, "cand", {path: render_manifest(req)})
    m1 = _merge(repo, "cand")
    got = act_status.load_history(GitOps({m1: (7, head)}), _state(repo), "master")
    assert (
        isinstance(got, Unavailable)
        and "манифест лежит не по своему пути" in got.reason
    )


@pytest.mark.parametrize("component", ["node", "generation"])
def test_history_stop_off_its_path_unavailable(repo: Path, component: str) -> None:
    """B2-3: запись остановки не по пути своего узла/поколения — не остановка."""
    stop = StopRecord("requirements", 1, "r")
    path = (
        stop_path(WS, "behaviour-spec", 1)
        if component == "node"
        else stop_path(WS, "requirements", 2)
    )
    _branch(repo, "stop", {path: render_stop(stop)})
    _merge(repo, "stop")
    got = act_status.load_history(GitOps({}), _state(repo), "master")
    assert (
        isinstance(got, Unavailable)
        and "остановка лежит не по своему пути" in got.reason
    )


def test_history_stop_edited_after_delivery_unavailable(repo: Path) -> None:
    stop = StopRecord("requirements", 1, "r")
    path = stop_path(WS, "requirements", 1)
    _branch(repo, "stop", {path: render_stop(stop)})
    _merge(repo, "stop")
    (repo / path).write_text(render_stop(StopRecord("requirements", 1, "другая")))
    _git(repo, "commit", "-qam", "edit stop")
    got = act_status.load_history(GitOps({}), _state(repo), "master")
    assert (
        isinstance(got, Unavailable)
        and "запись истории изменена после доставки" in got.reason
    )


def test_history_unreadable_base_unavailable() -> None:
    """b4: first-parent базы не прочитан — история `Unavailable`, не пустая."""

    class _Ops:
        def first_parent_changes(self, target_dir, ref):
            return None

    got = act_status.load_history(
        _Ops(), SimpleNamespace(target_dir="/t", ws_id=WS), "master"
    )
    assert (
        isinstance(got, Unavailable)
        and got.reason == "история master: first-parent не прочитан"
    )


def test_history_stop_of_other_workstream_ignored(repo: Path) -> None:
    """B2-3: остановка в approvals/ другого воркстрима — не событие этого."""
    stop = StopRecord("requirements", 1, "r")
    _branch(repo, "stop", {stop_path("ws-8", "requirements", 1): render_stop(stop)})
    _merge(repo, "stop")
    assert act_status.load_history(GitOps({}), _state(repo), "master") == ()


def test_first_parent_unreadable_is_none(tmp_path: Path) -> None:
    """b4: `git log` не прочитан (не репозиторий) — `None`, а не пустая история."""
    assert RealOps().first_parent_changes(str(tmp_path), "master") is None


def test_pr_for_merge_needs_exactly_one() -> None:
    class _Ops:
        def __init__(self, prs):
            self.prs = prs

        def prs_containing_commit(self, slug, sha):
            return self.prs

    m = "m" * 40
    assert (
        act_status.pr_for_merge(
            _Ops([{"number": 7, "state": "MERGED", "mergeCommit": m}]), "o/p", m
        )
        == 7
    )
    got = act_status.pr_for_merge(_Ops([]), "o/p", m)
    assert isinstance(got, Unavailable) and "нужен ровно один" in got.reason


def test_config_home_resolution() -> None:
    """Заметки части A, п. 11: то же правило, что заявлено discovery (M2)."""
    from governance.act_settings import config_home

    assert config_home({"XDG_CONFIG_HOME": "/cfg"}) == Path("/cfg")
    assert config_home({"XDG_CONFIG_HOME": "relative"}) == Path.home() / ".config"
    assert config_home({}) == Path.home() / ".config"


class ProductOps(GitOps):
    """Git и форджа стенда; источник снимка базы — локальный репозиторий продукта.
    Единственный путь к локальному источнику — переопределение `_snapshot_url` в тесте."""

    def __init__(self, prs, product: Path) -> None:
        super().__init__(prs)
        self.product = product

    def _snapshot_url(self, repo_slug: str) -> str:
        return str(self.product)


def remote_stop_world(
    tmp_path: Path, node: str = "requirements", fname: str = "10-requirements.md"
) -> tuple[Path, Path, ProductOps]:
    """d3–d4: репозиторий продукта с доставленной заявкой `review/v1` узла и его конвертом;
    локальный клон снят ДО того, как в продукт доставлена остановка (база клона устарела).
    Возвращает (продукт, клон, ops с форджей стенда и источником снимка — продуктом)."""
    origin = tmp_path / "owner" / "p"
    origin.parent.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "master", str(origin)], check=True)
    _git(origin, "config", "user.email", "t@e.st")
    _git(origin, "config", "user.name", "t")
    (origin / "README.md").write_text("x\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", "init")
    text = node_text(node)
    req = request(
        "approve-1-0-1",
        {node: (self_hash_v(text, "review/v1"), 1)},
        path_of=lambda n: f"spec/{fname}",
    )
    head = _branch(
        origin,
        "cand",
        {
            f"spec/{fname}": text,
            manifest_path(WS, req.request_id): render_manifest(req),
        },
    )
    merge = _merge(origin, "cand")
    _branch(
        origin,
        "final",
        {envelope_path(WS, node): envelope(DeliveredRequest(merge, head, req), node)},
    )
    _merge(origin, "final")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    _branch(
        origin,
        "stop",
        {stop_path(WS, node, 1): render_stop(StopRecord(node, 1, "переоткрыт"))},
    )
    _merge(origin, "stop")
    return origin, clone, ProductOps({merge: (11, head)}, origin)


def review_live(monkeypatch) -> None:
    """Продукт в `review`; доверенный вердикт доставки — «принят» (стенд без форджи).
    `statuses_at_base` и чтения git — настоящие."""
    live = SimpleNamespace(mode="review", trust=None, journal=None)
    monkeypatch.setattr(act_status, "mode_probe", lambda state, deps=None: live)
    monkeypatch.setattr(act_status, "real_deps", lambda state, env=None: None)
    monkeypatch.setattr(
        act_status.Decider,
        "__call__",
        lambda self, event: Accepted("m" * 40, "df-reviewer", "11", "alice"),
    )


def clone_state(clone: Path, **over) -> SimpleNamespace:
    fields = {
        "repo_slug": SLUG,
        "ws_id": WS,
        "target_dir": str(clone),
        "bundle_dir": "spec",
        "base_ref": "master",
        "brief": None,
    }
    return SimpleNamespace(**{**fields, **over})


def _refs(repo: Path) -> str:
    return _git(repo, "for-each-ref", "--format=%(refname) %(objectname)")


STOPPED = "узел requirements: not_approved:stopped:g1"


def test_noop_refusal_reads_fresh_product_base(tmp_path: Path, monkeypatch) -> None:
    """d3 (D3-1): локальная база клона устарела — по ней узел «approved»; проверка допуска
    берёт свежий снимок продукта и видит остановку, без подмены `statuses_at_base`."""
    _origin, clone, ops = remote_stop_world(tmp_path)
    review_live(monkeypatch)
    state = clone_state(clone)
    stale = act_status.statuses_at_base(
        state, ops, DAG, act_status.mode_probe(state), None
    )
    assert stale.of("requirements") == "approved"
    assert act_status.noop_refusal(state, ops, DAG, ["requirements"]) == STOPPED


def test_snapshot_leaves_product_repo_untouched(tmp_path: Path, monkeypatch) -> None:
    """d4–d5: снимок — во временном репозитории: ref'ы клона (в т. ч. `local-keep` из
    mapping `remote.origin.fetch`), его `config` и `FETCH_HEAD` не тронуты; снимок свежий."""
    _origin, clone, ops = remote_stop_world(tmp_path)
    _git(clone, "branch", "local-keep")
    _git(
        clone,
        "config",
        "remote.origin.fetch",
        "+refs/heads/master:refs/heads/local-keep",
    )
    config = clone / ".git" / "config"
    refs_before, config_before = _refs(clone), config.read_text()
    review_live(monkeypatch)
    assert (
        act_status.noop_refusal(clone_state(clone), ops, DAG, ["requirements"])
        == STOPPED
    )
    assert _refs(clone) == refs_before
    assert config.read_text() == config_before
    fetch_head = clone / ".git" / "FETCH_HEAD"
    assert fetch_head.exists() is False


def test_snapshot_ignores_origin_of_the_clone(tmp_path: Path, monkeypatch) -> None:
    """d4 (D3-3): `origin` клона не читается — отстающая копия с совпадающим суффиксом пути
    (`…/stale/owner/p`) свежести не подменяет; снимок — из источника продукта."""
    _origin, clone, ops = remote_stop_world(tmp_path)
    stale = tmp_path / "stale" / "owner" / "p"
    subprocess.run(["git", "clone", "-q", "--bare", str(clone), str(stale)], check=True)
    _git(clone, "remote", "set-url", "origin", str(stale))
    review_live(monkeypatch)
    assert (
        act_status.noop_refusal(clone_state(clone), ops, DAG, ["requirements"])
        == STOPPED
    )


def test_injected_git_config_and_env_have_no_effect(
    tmp_path: Path, monkeypatch
) -> None:
    """d5 (TOCTOU D3-3): `url.*.insteadOf` на отстающую копию — в config клона, в
    глобальном (`GIT_CONFIG_GLOBAL`), через `GIT_CONFIG_COUNT/KEY/VALUE`, и в config клона
    МЕЖДУ созданием снимка и fetch; унаследованный `GIT_DIR` — ничего не меняет: снимок
    свежий, источник — продукт."""
    from governance import ops as ops_mod

    origin, clone, ops = remote_stop_world(tmp_path)
    stale = tmp_path / "stale" / "owner" / "p"
    subprocess.run(["git", "clone", "-q", "--bare", str(clone), str(stale)], check=True)
    rule = (f"url.{stale}.insteadOf", str(origin))
    _git(clone, "config", *rule)
    glob = tmp_path / "global.gitconfig"
    glob.write_text(f'[url "{stale}"]\n\tinsteadOf = {origin}\n')
    for key, value in {
        "GIT_CONFIG_GLOBAL": str(glob),
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": rule[0],
        "GIT_CONFIG_VALUE_0": rule[1],
        "GIT_DIR": str(stale),
    }.items():
        monkeypatch.setenv(key, value)
    real_run = subprocess.run

    def run(cmd, **kw):
        done = real_run(cmd, **kw)
        if "init" in cmd:  # между созданием снимка и fetch
            real_run(
                ["git", "-C", str(clone), "config", "--add", *rule],
                env={"PATH": os.environ["PATH"]},
                check=True,
            )
        return done

    monkeypatch.setattr(ops_mod.subprocess, "run", run)
    review_live(monkeypatch)
    assert (
        act_status.noop_refusal(clone_state(clone), ops, DAG, ["requirements"])
        == STOPPED
    )


def test_production_snapshot_command_is_isolated(tmp_path: Path, monkeypatch) -> None:
    """d4–d5: боевой источник — только `https://github.com/<repo_slug>.git`; `origin` не
    спрашивается; fetch — во временном репозитории, credential helper сброшен и задан
    явно (`gh` профиля runner'а), окружение — из allowlist без `GIT_*` извне, запросов нет."""
    from governance import ops as ops_mod

    _origin, clone, _ops = remote_stop_world(tmp_path)
    profile = tmp_path / "review-profile"
    monkeypatch.setenv("REVIEW_GH_CONFIG_DIR", str(profile))
    monkeypatch.setenv("GIT_DIR", "/elsewhere")
    calls: list[tuple[list[str], dict]] = []
    real_run = subprocess.run

    def run(cmd, **kw):
        calls.append((cmd, kw.get("env") or {}))
        if "fetch" in cmd:
            return subprocess.CompletedProcess(cmd, 128, "", "")
        return real_run(cmd, **kw)

    monkeypatch.setattr(ops_mod.subprocess, "run", run)
    got = RealOps().snapshot_open(str(clone), "owner/p", "master")
    cmd, env = next((c, e) for c, e in calls if "fetch" in c)
    assert cmd[cmd.index("--refmap=") + 1] == "https://github.com/owner/p.git"
    assert cmd[2:6] == [
        "-c",
        "credential.helper=",
        "-c",
        "credential.helper=!gh auth git-credential",
    ]
    isolation = tuple(
        env.get(k)
        for k in (
            "GIT_CONFIG_NOSYSTEM",
            "GIT_CONFIG_GLOBAL",
            "GIT_TERMINAL_PROMPT",
            "GH_CONFIG_DIR",
            "GIT_ASKPASS",
            "SSH_ASKPASS",
            "GH_PROMPT_DISABLED",
        )
    )
    assert isolation == ("1", os.devnull, "0", str(profile), "", "", "1")
    assert [k for k in env if k.startswith("GIT_") and k not in ISOLATION] == []
    assert not any("origin" in c for c, _e in calls)
    assert got.detail == "fetch owner/p master: код 128"


ISOLATION = {
    "GIT_CONFIG_NOSYSTEM",
    "GIT_CONFIG_GLOBAL",
    "GIT_TERMINAL_PROMPT",
    "GIT_ASKPASS",
}


@pytest.mark.parametrize(
    "slug",
    [
        "file:///tmp/stale/owner/p",
        "/tmp/stale/owner/p",
        "../owner/p",
        "owner/p/extra",
        "-uowner/p",
        "owner/..",
    ],
    ids=["file_url", "local_path", "dotdot", "extra", "dash", "dot_repo"],
)
def test_snapshot_rejects_non_slug_source(
    tmp_path: Path, monkeypatch, slug: str
) -> None:
    """d4: в источник снимка не попадает ни локальный путь, ни `file://`, ни что-либо,
    кроме `owner/repo`; git не запускается."""
    from governance import ops as ops_mod

    monkeypatch.setattr(ops_mod.subprocess, "run", lambda *a, **k: pytest.fail("git"))
    got = RealOps().snapshot_open(str(tmp_path), slug, "master")
    assert got.detail == f"repo_slug {slug!r} — не owner/repo"


def test_noop_refusal_refuses_unfetchable_product(tmp_path: Path, monkeypatch) -> None:
    """d3: репозиторий продукта не отвечает (путь исчез) — отказ, а не локальная база;
    временный снимок удалён."""
    origin, clone, ops = remote_stop_world(tmp_path)
    origin.rename(tmp_path / "owner" / "gone")
    review_live(monkeypatch)
    got = act_status.noop_refusal(clone_state(clone), ops, DAG, ["requirements"])
    assert got == "свежая база: снимок owner/p@master: fetch owner/p master: код 128"


def test_interleaved_snapshots_are_independent(tmp_path: Path) -> None:
    """d4–d5 (D3-4): два чередующихся вызова на одном клоне — один из отстающего источника;
    у каждого свой временный репозиторий и свой SHA; после выхода оба удалены."""
    origin, clone, fresh_ops = remote_stop_world(tmp_path)
    stale = tmp_path / "stale" / "owner" / "p"
    subprocess.run(["git", "clone", "-q", "--bare", str(clone), str(stale)], check=True)
    stale_ops = ProductOps(fresh_ops.prs, stale)
    state = clone_state(clone)
    with (
        act_status.fresh_base(state, fresh_ops) as a,
        act_status.fresh_base(state, stale_ops) as b,
    ):
        dirs = [a[1].repo.git_dir, b[1].repo.git_dir]
        assert (a[0], b[0]) == (
            _git(origin, "rev-parse", "master"),
            _git(stale, "rev-parse", "master"),
        )
        assert len(set(dirs)) == 2
    assert [Path(d).exists() for d in dirs] == [False, False]


def test_snapshot_removed_on_exception(tmp_path: Path) -> None:
    """d5: исключение внутри проверки — временный репозиторий снимка всё равно удалён."""
    _origin, clone, ops = remote_stop_world(tmp_path)
    seen: list[str] = []
    with pytest.raises(RuntimeError, match="сбой чтения"):
        with act_status.fresh_base(clone_state(clone), ops) as snapshot:
            seen.append(snapshot[1].repo.git_dir)
            raise RuntimeError("сбой чтения")
    assert Path(seen[0]).exists() is False


class CredentialOps(ProductOps):
    """Шов теста: credential helper — скрипт, отдающий токен; никакого другого пути."""

    def __init__(self, prs, product: Path, helper: str) -> None:
        super().__init__(prs, product)
        self.helper = helper

    def _credential_helper(self) -> str:
        return self.helper


TOKEN = "ghs_TEST_TOKEN_SECRET"


def token_helper(tmp_path: Path) -> str:
    """Helper-скрипт: токен — из файла (как у `gh`), не в собственном тексте helper'а."""
    secret = tmp_path / "token"
    secret.write_text(TOKEN)
    return (
        f'!f() {{ test "$1" = get && echo username=x-access-token && '
        f'echo "password=$(cat {secret})"; }}; f'
    )


def test_private_product_credentials_come_only_from_the_helper(
    tmp_path: Path, monkeypatch
) -> None:
    """d5 (D3-6): приватный продукт — учётные данные отдаёт только явно заданный helper,
    без интерактивного запроса; helper глобальной конфигурации не участвует."""
    _origin, _clone, ops = remote_stop_world(tmp_path)
    glob = tmp_path / "global.gitconfig"
    glob.write_text('[credential]\n\thelper = "!echo password=FOREIGN"\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(glob))
    cred = CredentialOps(ops.prs, ops.product, token_helper(tmp_path))
    git_dir = tmp_path / "probe.git"
    subprocess.run(["git", "init", "-q", "--bare", str(git_dir)], check=True)
    done = cred._snapshot_git(
        str(git_dir),
        "credential",
        "fill",
        stdin="protocol=https\nhost=github.com\npath=owner/p.git\n\n",
    )
    assert f"password={TOKEN}" in done.stdout


def test_token_never_in_argv_or_diagnostics(tmp_path: Path, monkeypatch) -> None:
    """d5: fetch приватного продукта не удался — токена нет ни в argv, ни в диагностике."""
    from governance import ops as ops_mod

    _origin, clone, ops = remote_stop_world(tmp_path)
    cred = CredentialOps(ops.prs, ops.product, token_helper(tmp_path))
    cred._snapshot_url = lambda slug: (
        "https://127.0.0.1:9/owner/p.git"
    )  # отказ соединения
    argv: list[str] = []
    real_run = subprocess.run
    monkeypatch.setattr(
        ops_mod.subprocess,
        "run",
        lambda cmd, **kw: argv.extend(cmd) or real_run(cmd, **kw),
    )
    got = cred.snapshot_open(str(clone), "owner/p", "master")
    assert got.outcome is Outcome.UNAVAILABLE
    assert TOKEN not in " ".join(argv) + got.detail


def test_public_product_works_without_gh_token(tmp_path: Path, monkeypatch) -> None:
    """d5: профиль `gh` без токена — публичный (здесь локальный) продукт читается анонимно,
    helper не мешает; запрос учётных данных не интерактивен и завершается отказом."""
    _origin, clone, ops = remote_stop_world(tmp_path)
    monkeypatch.setenv("REVIEW_GH_CONFIG_DIR", str(tmp_path / "empty-profile"))
    review_live(monkeypatch)
    assert (
        act_status.noop_refusal(clone_state(clone), ops, DAG, ["requirements"])
        == STOPPED
    )
    git_dir = tmp_path / "probe.git"
    subprocess.run(["git", "init", "-q", "--bare", str(git_dir)], check=True)
    failing = CredentialOps(ops.prs, ops.product, "!false")
    done = failing._snapshot_git(
        str(git_dir),
        "credential",
        "fill",
        stdin="protocol=https\nhost=github.com\npath=owner/p.git\n\n",
    )
    assert done.returncode != 0


class RecordingGitOps(ProductOps):
    """Git стенда: любое чтение git мимо снимка (по `target_dir` клона) — записывается."""

    def __init__(self, prs, product: Path) -> None:
        super().__init__(prs, product)
        self.outside: list[str] = []

    def first_parent_changes(self, target_dir, ref):
        self.outside.append(f"first_parent {ref}")
        return super().first_parent_changes(target_dir, ref)

    def show_file(self, target_dir, ref, path):
        self.outside.append(f"show {ref}:{path}")
        return super().show_file(target_dir, ref, path)


def test_admission_reads_only_the_snapshot(tmp_path: Path, monkeypatch) -> None:
    """d3–d5: внутри проверки допуска история, provenance старого акта, байты узла,
    конверт и прямые входы читаются из временного репозитория снимка по ОДНОМУ SHA; мимо
    снимка (клоном продукта) не читается ничего."""
    from governance import ops as ops_mod

    origin, clone, base_ops = remote_stop_world(tmp_path)
    ops = RecordingGitOps(base_ops.prs, origin)
    reads: list[tuple[str, str]] = []
    for name in ("first_parent_changes", "show_file", "show_file_bytes"):
        real = getattr(ops_mod.SnapshotOps, name)

        def record(self, target_dir, ref, *rest, _real=real):
            reads.append((ref, rest[0] if rest else ""))
            return _real(self, target_dir, ref, *rest)

        monkeypatch.setattr(ops_mod.SnapshotOps, name, record)
    review_live(monkeypatch)
    state = clone_state(clone)
    with act_status.fresh_base(state, ops) as (snapshot, reader):
        statuses = act_status.statuses_at_base(
            state, reader, DAG, act_status.mode_probe(state), None, ref=snapshot
        )
        statuses.of("requirements")
        statuses.blobs_of("behaviour-spec")  # прямой вход — requirements
        statuses.legacy("spec/15-behaviour-spec.md", BEH_TEXT)  # provenance
    assert ops.outside == []
    snapshot_refs = {snapshot}
    node_reads = {
        ref
        for ref, path in reads
        if path in ("", "spec/10-requirements.md", envelope_path(WS, "requirements"))
    }
    assert node_reads == snapshot_refs
    assert all(
        len(ref) == 40 and set(ref) <= set("0123456789abcdef") for ref, _p in reads
    )


@pytest.mark.parametrize(
    "name",
    [
        "blob_in_commit",
        "commit_files",
        "checkout_and_pull",
        "is_ancestor",
        "show_repo_file_bytes",
    ],
)
def test_snapshot_ops_denies_unlisted_methods(tmp_path: Path, name: str) -> None:
    """d5 (deny-by-default): метод вне пяти git-методов снимка и allowlist форджи —
    `AttributeError` с именем метода; клон не читается."""
    _origin, clone, ops = remote_stop_world(tmp_path)
    with act_status.fresh_base(clone_state(clone), ops) as (_sha, reader):
        with pytest.raises(AttributeError, match=f"{name!r} не разрешён"):
            getattr(reader, name)


def test_snapshot_forge_allowlist_reads_no_clone() -> None:
    """d5: каждый метод allowlist форджи `SnapshotOps` — метод `RealOps` без `target_dir` и
    без запуска `git`: локальный клон он прочитать не может."""
    import inspect

    from governance import ops as ops_mod

    readers = [
        name
        for name in sorted(ops_mod.SNAPSHOT_FORGE_METHODS)
        if "target_dir" in inspect.signature(getattr(RealOps, name)).parameters
        or '"git"' in inspect.getsource(getattr(RealOps, name))
    ]
    assert readers == []


def test_foreign_home_credentials_have_no_effect(tmp_path: Path, monkeypatch) -> None:
    """d6 (D6-2): чужие `~/.netrc` и `~/.gitconfig` настоящего HOME не участвуют — процессы
    git снимка получают пустой `HOME` (и `XDG_CONFIG_HOME`) внутри временного каталога
    снимка; профиль `gh` передаётся абсолютным путём, разрешённым ДО подмены HOME."""
    from governance import ops as ops_mod

    _origin, clone, ops = remote_stop_world(tmp_path)
    home = tmp_path / "real-home"
    home.mkdir()
    (home / ".netrc").write_text(
        "machine github.com login x-access-token password FOREIGN\n"
    )
    (home / ".gitconfig").write_text(
        '[credential]\n\thelper = "!echo password=FOREIGN"\n'
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("REVIEW_GH_CONFIG_DIR", "~/.config/review")
    seen: list[tuple[str, list[str], bool, str]] = []
    real_run = subprocess.run

    def run(cmd, **kw):
        if "fetch" in cmd:
            env = kw["env"]
            seen.append(
                (
                    Path(env["HOME"]).name,
                    sorted(os.listdir(env["HOME"])),
                    Path(env.get("XDG_CONFIG_HOME", "")).parent == Path(env["HOME"]),
                    env["GH_CONFIG_DIR"],
                )
            )
        return real_run(cmd, **kw)

    monkeypatch.setattr(ops_mod.subprocess, "run", run)
    review_live(monkeypatch)
    assert (
        act_status.noop_refusal(clone_state(clone), ops, DAG, ["requirements"])
        == STOPPED
    )
    expected = [("home", [], True, str((home / ".config" / "review").resolve()))]
    snapshot_home = list(seen)
    assert snapshot_home == expected


# --- журнал, эпохи, снимок: собственные отказы исполнителя ----------------------------


def test_decider_without_journal_unavailable(monkeypatch) -> None:
    """Ревизия b1 (M7): доверие передано, журнала нет и загрузить его нечем —
    вердикт не выносится, `delivery_verdict` не зовётся."""
    monkeypatch.setattr(
        act_context,
        "delivery_verdict",
        lambda *a, **k: pytest.fail("вердикт без журнала"),
    )
    state = SimpleNamespace(
        repo_slug=SLUG, ws_id=WS, target_dir="/t", bundle_dir="spec"
    )
    deps = act_status.Deps(
        readers=None,
        binding=object(),
        journal=None,
        creds=(),
        gh=None,
        now=None,
        sleep=None,
        settings=None,
    )
    decider = act_status.Decider(state, object(), deps, DAG, trust=object())
    got = decider.legacy(5, lambda: pytest.fail("старый контракт"))
    assert isinstance(got, Unavailable) and got.reason == "журнал периметра не задан"


def test_live_policy_without_epochs_unavailable(monkeypatch) -> None:
    """Пустой журнал эпох — режим не установлен, а не исключение и не legacy."""
    from governance import act_trust

    monkeypatch.setattr(
        act_trust,
        "load_trust",
        lambda readers, binding, journal, creds: SimpleNamespace(epochs=()),
    )
    monkeypatch.setattr(
        act_trust, "load_anchor_times", lambda *a: pytest.fail("якоря без эпох")
    )
    deps = act_status.Deps(
        readers=None,
        binding=object(),
        journal=lambda readers, binding: NoEvents(),
        creds=(),
        gh=None,
        now=None,
        sleep=None,
        settings=None,
    )
    got = act_status.live_policy(SimpleNamespace(repo_slug=SLUG), deps)
    assert isinstance(got, Unavailable) and got.reason == "журнал эпох не прочитан"


def test_snapshot_of_non_repository_unavailable(tmp_path: Path) -> None:
    """`target_dir` — не репозиторий: снимок не создаётся, fetch не зовётся."""
    got = RealOps().snapshot_open(str(tmp_path), "owner/p", "master")
    assert got.outcome is Outcome.UNAVAILABLE
    assert got.detail == "репозиторий продукта не читается"


# --- читатели вне act_*: read_dag_state и _require_upstream_ready (Step 5) ------------


class _BaseOps:
    """Байты узлов в base — словарь; другое не читается."""

    def __init__(self, texts: dict[str, str]) -> None:
        self.texts = texts

    def show_file(self, target, ref, path):
        return self.texts.get(path)


def _reader_state() -> SimpleNamespace:
    return SimpleNamespace(
        repo_slug=SLUG,
        ws_id=WS,
        target_dir="/t",
        bundle_dir="spec",
        base_ref="master",
        brief=None,
        ops={},
    )


def _review_statuses(monkeypatch, verdicts: dict[str, str]) -> None:
    monkeypatch.setattr(
        act_status,
        "mode_probe",
        lambda state, deps=None: SimpleNamespace(mode="review"),
    )
    monkeypatch.setattr(
        act_status,
        "statuses_at_base",
        lambda state, ops, dag, live, deps=None, *, ref=None: SimpleNamespace(
            of=verdicts.__getitem__
        ),
    )


BASE_TEXTS = {
    "spec/10-requirements.md": REQ_TEXT,
    "spec/15-behaviour-spec.md": BEH_TEXT,
}


def test_read_dag_state_mode_unset_unresolved(monkeypatch) -> None:
    from governance import approve_node

    monkeypatch.setattr(
        act_status, "mode_probe", lambda state, deps=None: Unavailable("нет привязки")
    )
    got = approve_node.read_dag_state(_reader_state(), _BaseOps(BASE_TEXTS), DAG)
    assert (
        got.evidence is None and got.unresolved == "режим акта продукта: нет привязки"
    )


def test_read_dag_state_review_statuses(monkeypatch) -> None:
    """`review`: долг — ответ статуса (`review_v1`), `unavailable:` — не установлено,
    одобрены все — свидетельство."""
    from governance import approve_node

    ops = _BaseOps(BASE_TEXTS)
    _review_statuses(
        monkeypatch,
        {"requirements": "approved", "behaviour-spec": "not_approved:stopped:g1"},
    )
    debt = approve_node.read_dag_state(_reader_state(), ops, DAG)
    assert [(d.node_id, d.kind, d.reason) for d in debt.debts] == [
        ("behaviour-spec", "review_v1", "stopped:g1")
    ]
    _review_statuses(
        monkeypatch,
        {"requirements": "unavailable:df-trust: 502", "behaviour-spec": "approved"},
    )
    unresolved = approve_node.read_dag_state(_reader_state(), ops, DAG)
    assert unresolved.unresolved == "unavailable:df-trust: 502"
    _review_statuses(
        monkeypatch, {"requirements": "approved", "behaviour-spec": "approved"}
    )
    assert approve_node.read_dag_state(_reader_state(), ops, DAG).evidence is not None


@pytest.mark.parametrize(
    "case", ["mode_unset", "upstream_unavailable", "upstream_not_approved", "approved"]
)
def test_require_upstream_ready_in_review(monkeypatch, case: str) -> None:
    """`approve_node._require_upstream_ready#mode_unset|upstream_unavailable|
    upstream_not_approved`: у каждой ветки своя причина; одобренный upstream — готов."""
    from governance import approve_node

    verdict = {
        "upstream_unavailable": "unavailable:df-trust: 502",
        "upstream_not_approved": "not_approved:stopped:g1",
    }.get(case, "approved")
    _review_statuses(monkeypatch, {"requirements": verdict})
    if case == "mode_unset":
        monkeypatch.setattr(
            act_status,
            "mode_probe",
            lambda state, deps=None: Unavailable("нет привязки"),
        )
    call = lambda: approve_node._require_upstream_ready(  # noqa: E731
        _reader_state(), _BaseOps(BASE_TEXTS), DAG, "behaviour-spec"
    )
    reason = {
        "mode_unset": "режим акта продукта (upstream behaviour-spec): нет привязки",
        "upstream_unavailable": "upstream requirements узла behaviour-spec: "
        "unavailable:df-trust: 502",
        "upstream_not_approved": "upstream requirements не одобрен (review/v1): "
        "stopped:g1",
    }.get(case)
    if reason is None:
        assert call() is None
        return
    with pytest.raises(RuntimeError) as exc:
        call()
    assert reason in str(exc.value)


class _OpenPrTransport:
    """Транспорт фактов стенда: PR не смёржен (GraphQL и REST согласны)."""

    def __init__(self) -> None:
        self.sent: list[object] = []

    def send(self, request):
        from steward.approvalfacts import v3

        self.sent.append(request)
        if request.op is not None:
            pull = {"merged": False, "state": "OPEN"}
            return v3.Response(200, {"data": {"repository": {"pullRequest": pull}}})
        return v3.Response(200, {"merged": False, "state": "open"})


def test_decider_legacy_empty_paths_through_real_facts_read() -> None:
    """M-4 ревью Task 14: `Decider.legacy` зовёт настоящий `act_facts.read` с
    `paths=()` — steward принимает пустой набор (без `ValueError` → `Unavailable`),
    blob'ы путей не запрашиваются; несмёрженный PR — ожидание, старый код не зовётся."""
    from governance.act_decision import WaitingDelivery

    transport = _OpenPrTransport()
    state = SimpleNamespace(
        repo_slug=SLUG, ws_id=WS, target_dir="/t", bundle_dir="spec"
    )
    deps = act_status.Deps(
        readers=None,
        binding=object(),
        journal=None,
        creds=(),
        gh=transport,
        now=None,
        sleep=None,
        settings=None,
    )
    decider = act_status.Decider(
        state, object(), deps, DAG, trust=object(), journal=NoEvents()
    )
    got = decider.legacy(5, lambda: pytest.fail("старый код у несмёржённого PR"))
    assert got == WaitingDelivery()
    ops = [getattr(r, "op", None) for r in transport.sent]
    assert "StewardV3Delivery" in ops and "StewardV3Blobs" not in ops


# --- раунд 2 (ревью PR #667) ----------------------------------------------------------


@pytest.fixture()
def bad_settings(tmp_path: Path, monkeypatch) -> None:
    """`act.toml` с неизвестным ключом; `mode_probe` — настоящий (`real_deps`)."""
    cfg = tmp_path / "cfg"
    (cfg / "darkfactory").mkdir(parents=True)
    (cfg / "darkfactory" / "act.toml").write_text("review_v1 = true\nlol = 1\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.setattr(
        act_status,
        "mode_probe",
        lambda state, deps=None: act_status.live_policy(
            state, deps or act_status.real_deps(state)
        ),
    )


def test_bad_settings_is_a_named_refusal(bad_settings, tmp_path: Path) -> None:
    """Нечитаемый `act.toml` — названный fail-closed отказ на каждом читателе
    режима, а не трассировка `SettingsError`."""
    from governance import approve_node

    state = _reader_state()
    got = approve_node.read_dag_state(state, _BaseOps(BASE_TEXTS), DAG)
    assert got.evidence is None
    assert got.unresolved.startswith("режим акта продукта: настройки акта: ")
    refusal = act_status.noop_refusal(state, _BaseOps(BASE_TEXTS), DAG)
    assert refusal is not None
    assert refusal.startswith("режим акта продукта: настройки акта: ")
    gate = act_status.gate_run(
        state, object(), DAG, candidate=True, out=tmp_path / "ns.json"
    )
    assert isinstance(gate, Unavailable) and gate.reason.startswith("настройки акта: ")
    assert not (tmp_path / "ns.json").exists()


class DigestOps(ProductOps):
    """Продукт стенда + дайджест снимка gate-check."""

    def gate_snapshot_digest(self, target_dir, bundle_dir, *, candidate):
        return 0, "sha256:" + "a" * 64


@pytest.mark.parametrize("edit", ["envelope", "node"])
def test_s8_provider_reads_base_not_worktree(
    tmp_path: Path, monkeypatch, edit: str
) -> None:
    """S4 — рабочее дерево волны, S8 — база: незакоммиченная правка конверта или
    узла в клоне меняет ответ провайдера S4, но не S8."""
    import json

    _origin, clone, ops = remote_stop_world(tmp_path)
    review_live(monkeypatch)
    state = clone_state(clone)
    path = clone / (
        envelope_path(WS, "requirements")
        if edit == "envelope"
        else "spec/10-requirements.md"
    )
    if edit == "envelope":  # подменённый конверт лежит только в рабочем дереве
        env = parse_envelope(path.read_text())
        path.write_text(render_envelope(replace(env, review_id="99")))
    else:
        path.write_text(path.read_text() + "правка рабочего дерева\n")
    digest_ops = DigestOps(ops.prs, ops.product)
    deps = act_status.Deps(
        readers=None,
        binding=None,
        journal=None,
        creds=(),
        gh=None,
        now=None,
        sleep=None,
        settings=None,
    )

    def provider(candidate: bool) -> dict:
        out = tmp_path / f"ns-{candidate}.json"
        got = act_status.gate_run(
            state, digest_ops, DAG, candidate=candidate, out=out, deps=deps
        )
        assert got == ("review", str(out))
        return json.loads(out.read_text())["nodes"]

    assert provider(False) == {"requirements": "approved"}
    assert provider(True)["requirements"].startswith("not_approved:")


def test_noop_refusal_empty_nodes_opens_no_snapshot(monkeypatch) -> None:
    """Пустой набор узлов — `None` без снимка базы и без истории (квота С-1)."""
    review_live(monkeypatch)

    class _NoSnapshot:
        def snapshot_open(self, *a):
            pytest.fail("снимок для пустого набора узлов")

        def first_parent_changes(self, *a):
            pytest.fail("история для пустого набора узлов")

    assert (
        act_status.noop_refusal(clone_state(Path("/t")), _NoSnapshot(), DAG, []) is None
    )


def test_s8_provider_reads_inputs_from_base(tmp_path: Path, monkeypatch) -> None:
    """S8: прямые входы узла — из базы. Upstream есть только untracked-файлом в
    рабочем дереве: S8 его не видит (вход не прочитан — `unavailable:`), S4 видит."""
    import json

    _origin, clone, ops = remote_stop_world(
        tmp_path, node="behaviour-spec", fname="15-behaviour-spec.md"
    )
    review_live(monkeypatch)
    (clone / "spec/10-requirements.md").write_text(REQ_TEXT)  # только в рабочем дереве
    digest_ops = DigestOps(ops.prs, ops.product)
    deps = act_status.Deps(
        readers=None,
        binding=None,
        journal=None,
        creds=(),
        gh=None,
        now=None,
        sleep=None,
        settings=None,
    )

    def provider(candidate: bool) -> dict:
        out = tmp_path / f"ns-{candidate}.json"
        act_status.gate_run(
            clone_state(clone), digest_ops, DAG, candidate=candidate, out=out, deps=deps
        )
        return json.loads(out.read_text())["nodes"]

    s8, s4 = provider(False), provider(True)
    assert set(s8) == {"behaviour-spec"}
    assert s8["behaviour-spec"].startswith("unavailable:прямые входы behaviour-spec")
    assert not s4["behaviour-spec"].startswith("unavailable:")
