"""`merge` in stage 2a (Task 10): kind routing and the `Precheck` hook,
through the publisher frame with a fake GitHub. Nothing is ever merged."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from df.publisher import github, merge
from df.publisher.merge import UNAVAILABLE, Kind
from df.publisher.request import Refused, Response
from governance import act_envelope, approval_branches
from tests.df.publisher_world import SHA, World, raw_of
from tests.df.test_publisher_ops import (
    PR_URL,
    REPO,
    FakeGitHub,
    Reply,
    _reply,
    gh,  # noqa: F401  (fixture)
    key_text,  # noqa: F401  (fixture)
    pytestmark,  # noqa: F401  (skip without openssl)
)

REAL = merge.parse_patterns(approval_branches.PATTERNS_PATH.read_text())
ENVELOPE = "workstreams/ws/approvals/n1.yaml"
STOP = "workstreams/ws/approvals/stops/n1-g2.yaml"
FINAL = "spec/ws-approve-1-2-3-final"
CAND = "spec/ws-approve-1-2-3"


TIP = "c" * 40
COMPARE = f"/repos/{REPO}/compare/{TIP}...{SHA}"


class MergeGitHub(FakeGitHub):
    """The fake GitHub with a head ref, a base branch and a compare."""

    def __init__(self, world: World) -> None:
        super().__init__(world)
        self.head_ref = CAND
        self.base_ref = "master"
        self.files: list[object] = [{"filename": "README.md"}]
        self.file_gets = 0  # compare requests
        self.head_repo: object = 7
        self.title = "t"

    def pr(self) -> Reply:
        _, _, raw = super().pr()
        data = json.loads(raw)
        data["head"]["ref"] = self.head_ref
        data["head"]["repo"] = (
            None if self.head_repo is None else {"id": self.head_repo}
        )
        data["base"]["ref"] = self.base_ref
        data["title"] = self.title
        return _reply(data)

    def route(
        self,
        method: str,
        path: str,
        query: str,
        headers: Mapping[str, str],
        data: dict[str, object] | None,
    ) -> Reply:
        if method == "GET" and path.startswith(f"/repos/{REPO}/git/ref/heads/"):
            return _reply({"object": {"sha": TIP}})
        if method == "GET" and path.startswith(f"/repos/{REPO}/compare/"):
            self.file_gets += 1
            if path != COMPARE:  # any other pair of commits shows other files
                return _reply({"files": [{"filename": STOP}]})
            return _reply({"files": self.files})
        return super().route(method, path, query, headers, data)


@pytest.fixture()
def mgh(
    world: World,
    gh: FakeGitHub,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> MergeGitHub:
    fake = MergeGitHub(world)
    monkeypatch.setattr(github, "default_transport", fake)
    put_patterns(world, approval_branches.PATTERNS_PATH.read_text())
    return fake


def put_patterns(world: World, text: str | None) -> Path:
    """`patterns.env` in the world's active release tree (`None`: absent)."""
    path = merge.patterns_path(world.lay, world.snap)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    if text is not None:
        path.write_text(text)
    return path


def run(world: World, capsys: pytest.CaptureFixture[str], **over: object) -> Response:
    return world.run("merge", raw_of("merge", **over), capsys=capsys)[1]


def reason(resp: Response) -> tuple[str, str | None]:
    return resp.outcome, resp.reason


# --- patterns and the glob rule ---------------------------------------------


def test_glob_equals_the_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    template = REAL["APPROVAL_CANDIDATE_TEMPLATE"]
    assert merge._template_glob(template) == approval_branches.candidate_glob()
    assert (
        merge._template_glob(template) + REAL["APPROVAL_FINALIZE_SUFFIX"]
        == approval_branches.finalize_glob()
    )
    # the older arity gives the same glob
    older = "spec/{ws_id}-approve-{wave}-{step}"
    assert merge._template_glob(older) == approval_branches.candidate_glob()


def test_patterns_path_is_in_the_release_tree(world: World) -> None:
    path = merge.patterns_path(world.lay, world.snap)
    release = world.lay.release_dir("rel-a")
    assert path.relative_to(release / "src" / "devtools") == (
        approval_branches.PATTERNS_PATH.relative_to(
            Path(approval_branches.__file__).resolve().parent.parent
        )
    )
    assert not path.is_relative_to(Path(merge.__file__).resolve().parent.parent)


def test_patterns_no_release_is_unavailable(world: World) -> None:
    snap = replace(world.snap, release_id=None)
    with pytest.raises(Refused) as info:
        merge.read_patterns(world.lay, snap)
    assert info.value.reason == "patterns_unavailable"


def test_patterns_next_to_the_package_are_not_read(
    world: World,
    mgh: MergeGitHub,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression: the publisher runs from the admin tree, which holds `df/`
    only; the file beside the package (the repo's own) must not be used."""
    beside = Path(merge.__file__).resolve().parent.parent.parent
    assert (beside / "contracts/approval-branches/v1/patterns.env").is_file()
    put_patterns(world, None)
    assert reason(run(world, capsys)) == ("refused", "patterns_unavailable")
    assert mgh.calls == []


@pytest.mark.parametrize(
    ("ref", "kinds"),
    [
        (CAND, {"candidate"}),
        (FINAL, {"finalize"}),
        ("spec/ws-approve-1-2", {"candidate"}),  # older arity
        ("brief/some-idea", {"brief"}),
        ("feat/x", set()),
        ("spec/ws-bundle", set()),
    ],
)
def test_branch_kinds(ref: str, kinds: set[Kind]) -> None:
    assert merge.branch_kinds(ref, REAL) == kinds


def test_branch_kinds_two_forms_are_ambiguous() -> None:
    patterns = {
        "APPROVAL_CANDIDATE_TEMPLATE": "brief/{x}-approve-{y}",
        "APPROVAL_FINALIZE_SUFFIX": "-final",
    }
    ref = "brief/a-approve-1"
    assert merge.branch_kinds(ref, patterns) == {"candidate", "brief"}
    assert isinstance(merge.route(ref, [ENVELOPE], patterns), Refused)


# --- route --------------------------------------------------------------------


def amb(result: object) -> bool:
    return isinstance(result, Refused) and result.reason == "ambiguous_kind"


def test_route_kinds() -> None:
    assert merge.route(CAND, ["any/file.py"], REAL) == "candidate"
    assert merge.route(CAND, [ENVELOPE], REAL) == "candidate"
    assert merge.route("spec/ws-approve-1-2", ["x"], REAL) == "candidate"
    assert merge.route(FINAL, [ENVELOPE], REAL) == "finalize"
    assert merge.route(FINAL, [ENVELOPE, "workstreams/b/approvals/m.yaml"], REAL) == (
        "finalize"
    )
    assert merge.route("brief/x", ["docs/a.md"], REAL) == "brief"


def test_route_stop_file_is_ambiguous_on_any_branch() -> None:
    for ref in (CAND, FINAL, "brief/x", "feat/x"):
        assert amb(merge.route(ref, [STOP], REAL)), ref
        assert amb(merge.route(ref, [ENVELOPE, STOP], REAL)), ref


def test_route_finalize_with_other_paths_is_ambiguous() -> None:
    assert amb(merge.route(FINAL, [ENVELOPE, "src/x.py"], REAL))
    assert amb(merge.route(FINAL, [], REAL))
    assert amb(merge.route(FINAL, ["workstreams/ws/approvals/stops/a.yaml"], REAL))
    assert amb(merge.route(FINAL, ["workstreams/ws/approvals/x/y.yaml"], REAL))
    assert amb(merge.route(FINAL, ["workstreams/ws/approvals/n1.yml"], REAL))


def test_route_outside_the_forms_is_ambiguous() -> None:
    assert amb(merge.route("feat/x", [ENVELOPE], REAL))
    assert amb(merge.route("", [ENVELOPE], REAL))


def test_path_templates_per_segment() -> None:
    m = merge._matches
    assert m(ENVELOPE, merge.ENVELOPE_PATH)
    assert not m("workstreams/a/b/approvals/n.yaml", merge.ENVELOPE_PATH)
    assert not m("workstreams/approvals/n.yaml", merge.ENVELOPE_PATH)
    assert not m(STOP, merge.ENVELOPE_PATH)  # '*' never crosses '/'
    assert not m(ENVELOPE + "/extra", merge.ENVELOPE_PATH)  # longer
    assert not m("workstreams/ws/approvals", merge.ENVELOPE_PATH)  # shorter
    assert m(STOP, merge.STOP_PATH)
    assert not m("workstreams/ws/approvals/stops/x/n-g1.yaml", merge.STOP_PATH)
    assert not m("workstreams/ws/approvals/stops/n.yaml", merge.STOP_PATH)


def test_path_templates_match_act_envelope() -> None:
    env = act_envelope.envelope_path("ws", "n")
    stop = act_envelope.stop_path("ws", "n", 3)
    assert merge._matches(env, merge.ENVELOPE_PATH)
    assert not merge._matches(env, merge.STOP_PATH)
    assert merge._matches(stop, merge.STOP_PATH)
    assert not merge._matches(stop, merge.ENVELOPE_PATH)
    assert env.split("/")[0] == merge.ENVELOPE_PATH[0]
    assert stop.split("/")[:4] == [
        "workstreams",
        "ws",
        "approvals",
        "stops",
    ]
    assert len(merge.STOP_PATH) == len(stop.split("/"))
    assert len(merge.ENVELOPE_PATH) == len(env.split("/"))


# --- patterns.env -------------------------------------------------------------


GOOD = (
    "# c\n\nAPPROVAL_CANDIDATE_TEMPLATE=spec/{ws_id}-approve-{wave}\n"
    "APPROVAL_FINALIZE_SUFFIX=-final\n"
)


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "APPROVAL_CANDIDATE_TEMPLATE=spec/{a}-approve-{b}\n",
        GOOD + "APPROVAL_FINALIZE_SUFFIX=-x\n",
        GOOD + "  APPROVAL_CANDIDATE_TEMPLATE=spec/x-approve-1\n",
        GOOD.replace("-final", ""),
        GOOD.replace("-final", "  "),
        GOOD.replace("APPROVAL_FINALIZE_SUFFIX", "approval_finalize_suffix"),
        GOOD.replace("APPROVAL_FINALIZE_SUFFIX", "export APPROVAL_FINALIZE_SUFFIX"),
    ],
)
def test_patterns_unavailable(world: World, text: str | None) -> None:
    put_patterns(world, text)
    with pytest.raises(Refused) as info:
        merge.read_patterns(world.lay, world.snap)
    assert info.value.reason == "patterns_unavailable"


def test_patterns_parsed_not_executed(world: World) -> None:
    put_patterns(world, GOOD)
    assert merge.read_patterns(world.lay, world.snap) == {
        "APPROVAL_CANDIDATE_TEMPLATE": "spec/{ws_id}-approve-{wave}",
        "APPROVAL_FINALIZE_SUFFIX": "-final",
    }


def test_merge_routes_through_the_release_tree(
    world: World, mgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    put_patterns(world, GOOD.replace("approve", "okay"))
    mgh.head_ref = "spec/ws-okay-1"
    assert reason(run(world, capsys)) == ("refused", "precheck_unavailable")
    mgh.head_ref = CAND  # a name of the real contract's form, not this tree's
    resp = run(world, capsys, request_id="req-00000002")
    assert reason(resp) == ("refused", "ambiguous_kind")


def test_merge_patterns_unavailable_is_refused(
    world: World, mgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    put_patterns(world, None)
    assert reason(run(world, capsys)) == ("refused", "patterns_unavailable")


# --- the operation ------------------------------------------------------------


def test_merge_always_precheck_unavailable_in_2a(
    world: World, mgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    for ref, files in ((CAND, ["a.py"]), (FINAL, [ENVELOPE]), ("brief/x", ["a.md"])):
        mgh.head_ref = ref
        mgh.files = [{"filename": f} for f in files]
        resp = run(world, capsys, request_id=f"req-{len(ref):08d}")
        assert reason(resp) == ("refused", "precheck_unavailable"), ref


def test_unavailable_never_returns_none() -> None:
    for kind in ("candidate", "brief", "finalize", "stop"):
        out = UNAVAILABLE(kind, None, {})  # type: ignore[arg-type]
        assert isinstance(out, Refused) and out.reason == "precheck_unavailable"


def _no_mutation(mgh: MergeGitHub) -> None:
    posts = mgh.paths("POST")
    assert posts and all(p == "/app/installations/9/access_tokens" for p in posts)
    assert len(mgh.tokens) == len(posts)
    scoped = [t for t in mgh.tokens if "repository_ids" in t]
    read = {"contents": "read", "pull_requests": "read"}
    assert scoped and all(t["permissions"] == read for t in scoped)
    for method, path, _ in mgh.calls:
        if method == "POST":
            continue
        assert method == "GET", (method, path)
    assert not any(p.endswith("/merge") for _, p, _ in mgh.calls)
    assert not any(p.endswith("/reviews") for _, p, _ in mgh.calls)


def test_no_mutating_calls(
    world: World,
    mgh: MergeGitHub,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resp = run(world, capsys)
    assert reason(resp) == ("refused", "precheck_unavailable")
    _no_mutation(mgh)
    assert mgh.intent_seen == [] and world.files("intents") == []
    mgh.calls.clear()
    mgh.tokens.clear()
    seen: list[Kind] = []

    def lets(kind: Kind, oc: object, pr: Mapping[str, object]) -> None:
        seen.append(kind)

    resp = merge.merge(_frame_ctx(world, capsys), lets)
    assert reason(resp) == ("refused", "merge_not_in_stage_2a")
    assert seen == ["candidate"]
    _no_mutation(mgh)


def _frame_ctx(world: World, capsys: pytest.CaptureFixture[str]) -> object:
    """A frame `Context` as the handler gets it: the table's handler is
    swapped for one that keeps it, then the frame runs it."""
    from df.publisher import main as pmain

    kept: list[object] = []

    def keep(ctx: object) -> Response:
        kept.append(ctx)
        return Response("refused", "kept")

    original = pmain.OPERATION_TABLE["merge"]
    pmain.OPERATION_TABLE["merge"] = keep  # type: ignore[assignment]
    try:
        world.run("merge", raw_of("merge", request_id="req-keep-0001"), capsys=capsys)
    finally:
        pmain.OPERATION_TABLE["merge"] = original
    return kept[0]


def test_precheck_refusal_is_returned(
    world: World, mgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    def no(kind: Kind, oc: object, pr: Mapping[str, object]) -> Refused:
        return Refused("nope")

    resp = merge.merge(_frame_ctx(world, capsys), no)  # type: ignore[arg-type]
    assert reason(resp) == ("refused", "nope")


def test_precheck_gets_kind_and_pr(
    world: World, mgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    mgh.head_ref = "brief/x"
    got: list[tuple[Kind, Mapping[str, object]]] = []

    def spy(kind: Kind, oc: object, pr: Mapping[str, object]) -> Refused:
        got.append((kind, pr))
        return Refused("stop")

    merge.merge(_frame_ctx(world, capsys), spy)  # type: ignore[arg-type]
    assert got[0][0] == "brief" and got[0][1]["state"] == "open"


@pytest.mark.parametrize(
    ("attr", "value"),
    [("pr_head", "b" * 40), ("pr_repo", 8), ("pr_state", "closed")],
)
def test_merge_pr_state_refused(
    world: World,
    mgh: MergeGitHub,
    capsys: pytest.CaptureFixture[str],
    attr: str,
    value: object,
) -> None:
    setattr(mgh, attr, value)
    assert reason(run(world, capsys)) == ("refused", "pr_state")
    assert mgh.file_gets == 0


def test_merge_run_product_mismatch_before_any_call(
    world: World, mgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    resp = run(world, capsys, repository_id=8)
    assert reason(resp) == ("refused", "run_product_mismatch")
    assert mgh.calls == []


def test_merge_stop_file_in_pr_is_ambiguous(
    world: World, mgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    mgh.files = [{"filename": STOP}]
    assert reason(run(world, capsys)) == ("refused", "ambiguous_kind")


def test_rename_out_of_stops_does_not_escape(
    world: World, mgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    mgh.files = [{"filename": "docs/moved.md", "previous_filename": STOP}]
    assert reason(run(world, capsys)) == ("refused", "ambiguous_kind")
    mgh.head_ref = FINAL
    mgh.files = [{"filename": ENVELOPE, "previous_filename": "src/old.py"}]
    assert reason(run(world, capsys, request_id="req-00000002")) == (
        "refused",
        "ambiguous_kind",
    )


def test_compare_is_pinned_and_pulls_files_is_never_called(
    world: World, mgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    """The listing of the PR follows its live head (ABA); the compare of two
    fixed commits does not. Here `pulls/files` would show a stop file."""
    resp = run(world, capsys)
    assert reason(resp) == ("refused", "precheck_unavailable")
    gets = mgh.paths("GET")
    assert COMPARE in gets and mgh.file_gets == 1
    assert not any(p.endswith("/files") for p in gets)
    assert f"/repos/{REPO}/git/ref/heads/master" in gets


def test_base_branch_name_is_url_encoded(
    world: World, mgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    mgh.base_ref = "rel/1.0 x"
    run(world, capsys)
    assert f"/repos/{REPO}/git/ref/heads/rel/1.0%20x" in mgh.paths("GET")


def test_base_branch_with_a_slash_keeps_it(
    world: World, mgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    mgh.base_ref = "release/1.0"
    run(world, capsys)
    assert f"/repos/{REPO}/git/ref/heads/release/1.0" in mgh.paths("GET")


def test_299_files_are_fine_300_are_too_many(
    world: World, mgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    mgh.files = [{"filename": f"f{i}"} for i in range(299)]
    assert reason(run(world, capsys)) == ("refused", "precheck_unavailable")
    mgh.files = [{"filename": f"f{i}"} for i in range(300)]
    resp = run(world, capsys, request_id="req-00000002")
    assert reason(resp) == ("refused", "too_many_files")


@pytest.mark.parametrize("bad", [None, [], "x", {"files": "x"}, {"nofiles": 1}])
def test_compare_without_files_is_unavailable(
    world: World,
    mgh: MergeGitHub,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    bad: object,
) -> None:
    real = mgh.route

    def route(method: str, path: str, *rest: object) -> Reply:
        if path == COMPARE:
            return _reply(bad)
        return real(method, path, *rest)  # type: ignore[arg-type]

    monkeypatch.setattr(mgh, "route", route)
    assert reason(run(world, capsys)) == ("unknown", "github_unavailable")


@pytest.mark.parametrize("tip", [None, "short", 5, "G" * 40])
def test_bad_base_tip_is_unavailable(
    world: World,
    mgh: MergeGitHub,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tip: object,
) -> None:
    real = mgh.route

    def route(method: str, path: str, *rest: object) -> Reply:
        if "/git/ref/heads/" in path:
            return _reply({"object": {"sha": tip}})
        return real(method, path, *rest)  # type: ignore[arg-type]

    monkeypatch.setattr(mgh, "route", route)
    assert reason(run(world, capsys)) == ("unknown", "github_unavailable")
    assert mgh.file_gets == 0


def test_base_ref_changes_between_the_reads(
    world: World, mgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    reads: list[int] = []

    def barrier() -> None:
        reads.append(1)
        if len(reads) == 1:
            mgh.base_ref = "other"

    mgh.after_pr_get = barrier
    assert reason(run(world, capsys)) == ("refused", "pr_state")
    assert len(reads) == 2


def test_head_ref_changes_between_the_reads(
    world: World, mgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    """A branch rename keeps the sha; the routed kind must match read 2."""
    reads: list[int] = []

    def barrier() -> None:
        reads.append(1)
        if len(reads) == 1:
            mgh.head_ref = "brief/renamed"

    mgh.after_pr_get = barrier
    assert reason(run(world, capsys)) == ("refused", "pr_state")
    assert len(reads) == 2


def test_precheck_gets_the_second_read(
    world: World, mgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    reads: list[int] = []

    def barrier() -> None:
        reads.append(1)
        if len(reads) == 1:
            mgh.title = "edited"  # visible only in the second read

    mgh.after_pr_get = barrier
    got: list[Mapping[str, object]] = []

    def spy(kind: Kind, oc: object, pr: Mapping[str, object]) -> Refused:
        got.append(pr)
        return Refused("stop")

    merge.merge(_frame_ctx(world, capsys), spy)  # type: ignore[arg-type]
    assert got[0]["title"] == "edited"


def test_brief_branch_with_an_envelope_is_ambiguous() -> None:
    assert amb(merge.route("brief/x", [ENVELOPE], REAL))
    assert amb(merge.route("brief/x", ["docs/a.md", ENVELOPE], REAL))
    assert merge.route("brief/x", ["workstreams/ws/notes.md"], REAL) == "brief"


PARITY = [
    GOOD,
    GOOD.replace("-final", "-final  "),
    GOOD.replace("APPROVAL_FINALIZE", "  APPROVAL_FINALIZE"),
    GOOD.replace("-final\n", "-final\r\n"),
    GOOD.replace("=-final", "=  -final"),
    "\t" + GOOD,
    GOOD + "export OTHER=1\n",
    GOOD + "Stop_Template=x\n",
    GOOD + "a bare line of prose\n",
    GOOD + "garbage line\n",
    GOOD + "APPROVAL_FINALIZE_SUFFIX=-x\n",
    GOOD.replace("-final", ""),
    GOOD.replace("APPROVAL_FINALIZE_SUFFIX", "export APPROVAL_FINALIZE_SUFFIX"),
    "",
    approval_branches.PATTERNS_PATH.read_text(encoding="utf-8"),
]


@pytest.mark.parametrize("text", PARITY)
def test_patterns_parse_like_the_contract(text: str) -> None:
    """The publisher and `governance.ssot_env` agree: same values, or both
    refuse (a missing key, a duplicate, an empty value)."""
    from governance import ssot_env

    keys = ("APPROVAL_CANDIDATE_TEMPLATE", "APPROVAL_FINALIZE_SUFFIX")
    found = [ssot_env.definition_lines(text, k) for k in keys]
    good = all(len(v) == 1 and v[0] for v in found)
    if not good:
        with pytest.raises(Refused):
            merge.parse_patterns(text)
        return
    assert merge.parse_patterns(text) == {
        k: v[0] for k, v in zip(keys, found, strict=True)
    }


@pytest.mark.parametrize("path", ["", ".", "..", "workstreams//approvals/n.yaml"])
def test_bad_segments_never_match(path: str) -> None:
    assert not merge._matches(path, merge.ENVELOPE_PATH)
    assert not merge._matches("workstreams/ws/approvals/../x.yaml", merge.ENVELOPE_PATH)
    assert not merge._matches("workstreams/./approvals/n.yaml", merge.ENVELOPE_PATH)
    assert not merge._matches(
        "workstreams/ws/approvals/stops/../n-g1.yaml", merge.STOP_PATH
    )


def test_head_moves_during_the_listing(
    world: World, mgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    reads: list[int] = []

    def barrier() -> None:
        reads.append(1)
        if len(reads) == 1:  # between the first read and the second
            mgh.pr_head = "b" * 40

    mgh.after_pr_get = barrier
    assert reason(run(world, capsys)) == ("refused", "pr_state")
    assert mgh.file_gets == 1 and len(reads) == 2


@pytest.mark.parametrize("head_repo", [8, None, "7", True])
@pytest.mark.parametrize("ref", [CAND, FINAL])
def test_fork_pr_is_foreign(
    world: World,
    mgh: MergeGitHub,
    capsys: pytest.CaptureFixture[str],
    ref: str,
    head_repo: object,
) -> None:
    mgh.head_ref = ref
    mgh.files = [{"filename": ENVELOPE}]
    mgh.head_repo = head_repo
    assert reason(run(world, capsys)) == ("refused", "head_repo_foreign")
    assert mgh.file_gets == 0


@pytest.mark.parametrize(
    "files",
    [
        [{"nofilename": 1}],
        [{"filename": 3}],
        ["str"],
        [{"filename": "a", "previous_filename": 5}],
    ],
)
def test_bad_file_item_is_unavailable(
    world: World,
    mgh: MergeGitHub,
    capsys: pytest.CaptureFixture[str],
    files: list[object],
) -> None:
    mgh.files = files
    assert reason(run(world, capsys)) == ("unknown", "github_unavailable")


def test_bad_head_ref_is_unavailable(
    world: World,
    mgh: MergeGitHub,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mgh.head_ref = ""  # type: ignore[assignment]
    assert reason(run(world, capsys)) == ("unknown", "github_unavailable")


def test_self_check_comes_first(
    world: World, mgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    mgh.denied_apps = {42}
    resp = run(world, capsys)
    assert resp.outcome == "refused"
    assert not any(PR_URL in p for _, p, _ in mgh.calls)


def test_table_wires_merge() -> None:
    from df.publisher import main as pmain

    assert pmain.OPERATION_TABLE["merge"] is merge.merge_op
    assert pmain.OPERATION_TABLE["perimeter-status"] is not merge.merge_op
