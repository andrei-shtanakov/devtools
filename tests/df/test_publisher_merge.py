"""`merge` (stage 2a routing, Task 10; stage 2b stop merge, Task 8), through
the publisher frame with a fake GitHub and a fake `df-precheck` (the command's
supervised run answers in process with a verdict bound to the request).

Crashes are `Crash` (a `BaseException`: the frame does not catch it, nothing
after it runs), raised at a `fault_point` of a state file or by the fake
GitHub; the next attempt is a new `main` call on the same disk."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from df import doctor, fsutil, platform
from df.publisher import github, merge, precheck, request, store
from df.publisher.merge import Kind
from df.publisher.request import Refused, Response
from governance import act_envelope, approval_branches
from tests.df.launch_world import entry, snapshot
from tests.df.publisher_world import EXEC, RID, SHA, SHA_B, World, raw_of
from tests.df.test_publisher_ops import (
    BOT,
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
STOP_REF = "stop/ws-n1-g2"
STAGE3 = ("refused", "kind_unavailable_until_stage3: candidate")


TIP = "c" * 40
TIP_2 = "d" * 40
TIP_3 = "f" * 40
COMMIT = "e" * 40
COMPARE = f"/repos/{REPO}/compare/{TIP}...{SHA}"
MERGE_URL = f"{PR_URL}/merge"
ADMIN = "sha256:" + "d" * 64
WRITE = {"contents": "write", "pull_requests": "write"}


class Crash(BaseException):
    """The process dies here: nothing after it runs, no answer is written."""


class MergeGitHub(FakeGitHub):
    """The fake GitHub with a head ref, a base branch and a compare, the
    PR's merge state, `PUT .../merge` and token revocation."""

    def __init__(self, world: World) -> None:
        super().__init__(world)
        self.head_ref = CAND
        self.base_ref = "master"
        self.files: list[object] = [{"filename": "README.md", "status": "modified"}]
        self.file_gets = 0  # compare requests
        self.head_repo: object = 7
        self.title = "t"
        self.tips: list[str] = []  # base tips to answer first, then `tip`
        self.tip = TIP
        self.merged = False
        self.merged_by: object = None
        self.merge_commit: str | None = None
        # ok | 405 | 409 | 422 | 500 | 403 | lost_after | hold | crash
        self.put_mode = "ok"
        self.puts: list[dict[str, object]] = []
        self.held: dict[str, object] | None = None
        self.armed_seen: list[bool] = []
        self.before_put: Callable[[], None] = lambda: None
        self.after_post: Callable[[], None] = lambda: None
        self.revoked = 0

    def pr(self) -> Reply:
        _, _, raw = super().pr()
        data = json.loads(raw)
        data["number"] = 12
        data["head"]["ref"] = self.head_ref
        data["head"]["repo"] = (
            None if self.head_repo is None else {"id": self.head_repo}
        )
        data["base"]["ref"] = self.base_ref
        data["title"] = self.title
        data["merged"] = self.merged
        data["merged_by"] = self.merged_by
        data["merge_commit_sha"] = self.merge_commit
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
            tip = self.tips.pop(0) if self.tips else self.tip
            return _reply({"object": {"sha": tip}})
        if method == "GET" and path.startswith(f"/repos/{REPO}/compare/"):
            self.file_gets += 1
            if path != COMPARE:  # any other pair of commits shows other files
                return _reply({"files": [{"filename": STOP, "status": "added"}]})
            return _reply({"files": self.files})
        if (method, path) == ("PUT", MERGE_URL):
            return self.put(data or {})
        if (method, path) == ("DELETE", "/installation/token"):
            self.revoked += 1
            return 204, {}, b""
        return super().route(method, path, query, headers, data)

    def post(self, data: dict[str, object]) -> Reply:
        if self.post_mode == "crash_created":
            self.post_mode = "ok"
            super().post(data)
            raise Crash
        if self.post_mode == "duplicate":
            self.post_mode = "ok"
            super().post(data)
            self.post_mode = "timeout_created"
        reply = super().post(data)
        self.after_post()
        return reply

    def put(self, data: dict[str, object]) -> Reply:
        self.armed_seen.append(
            any(n.endswith(".armed.json") for n in self.world.files("intents"))
        )
        self.before_put()
        self.puts.append(data)
        mode = self.put_mode
        if mode == "crash":
            raise Crash
        if mode == "hold":
            self.held = data
            raise TimeoutError("held")
        if mode in ("401", "403", "404", "405", "409", "422", "500"):
            return _reply({"message": mode}, int(mode))
        reply = self.execute(data)
        if mode == "lost_after":
            raise TimeoutError("answer lost")
        return reply

    def execute(self, data: Mapping[str, object], by: object = None) -> Reply:
        """What GitHub does with a `PUT` (now or when a held one goes on)."""
        if self.pr_state != "open":
            return _reply({"message": "not open"}, 405)
        if data.get("sha") != self.pr_head:
            return _reply({"message": "Head branch was modified"}, 409)
        self.pr_state, self.merged, self.merge_commit = "closed", True, COMMIT
        self.merged_by = by or {"login": BOT, "type": "Bot"}
        return _reply({"sha": COMMIT, "merged": True})

    def release_held(self) -> None:
        assert self.held is not None
        self.execute(self.held)
        self.held = None

    def stop_pr(self) -> MergeGitHub:
        """Make the PR a stop PR of one added record."""
        self.head_ref = STOP_REF
        self.files = [{"filename": STOP, "status": "added"}]
        return self


class FakePrecheck:
    """`platform.run_supervised` of the `df-precheck` command: answers with a
    verdict bound to the request it got (`outcome`, `reason`)."""

    def __init__(self) -> None:
        self.outcome = "allow"
        self.reason = ""
        self.requests: list[dict[str, object]] = []
        self.on_call: Callable[[int], None] = lambda n: None

    def __call__(
        self,
        argv: object,
        data: bytes,
        *,
        limit: int,
        wait: float,
        env: Mapping[str, str],
    ) -> platform.SupervisedResult:
        req = json.loads(data)
        self.requests.append(req)
        self.on_call(len(self.requests))
        verdict = {
            "schema": precheck.VERDICT_SCHEMA,
            "nonce": req["nonce"],
            "request_digest": precheck.request_digest(req),
            "repository_id": req["repository_id"],
            "pr": req["pr"],
            "head_sha": req["head_sha"],
            "kind": req["kind"],
            "outcome": self.outcome,
            "reason": self.reason,
            "version": "df-precheck/1",
            "admin_digest": ADMIN,
        }
        out = (json.dumps(verdict) + "\n").encode()
        return platform.SupervisedResult(0, None, out, False, 0.1)


@pytest.fixture()
def checker(monkeypatch: pytest.MonkeyPatch) -> FakePrecheck:
    fake = FakePrecheck()
    monkeypatch.setattr(platform, "run_supervised", fake)
    return fake


@pytest.fixture()
def mgh(
    world: World,
    gh: FakeGitHub,  # noqa: F811
    checker: FakePrecheck,
    monkeypatch: pytest.MonkeyPatch,
) -> MergeGitHub:
    fake = MergeGitHub(world)
    monkeypatch.setattr(github, "default_transport", fake)
    put_patterns(world, approval_branches.PATTERNS_PATH.read_text())
    return fake


@pytest.fixture()
def sgh(mgh: MergeGitHub) -> MergeGitHub:
    """`mgh` with a stop PR."""
    return mgh.stop_pr()


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
        "APPROVAL_STOP_TEMPLATE": "stop/{ws_id}-{node}-g{generation}",
    }
    ref = "brief/a-approve-1"
    assert merge.branch_kinds(ref, patterns) == {"candidate", "brief"}
    assert isinstance(merge.route(ref, mod(ENVELOPE), patterns), Refused)


# --- route --------------------------------------------------------------------


def mod(*paths: str) -> list[merge.Change]:
    """Modified files: the changes of a PR that is not a stop one."""
    return [("modified", p, None) for p in paths]


def amb(result: object) -> bool:
    return isinstance(result, Refused) and result.reason == "ambiguous_kind"


def test_route_kinds() -> None:
    assert merge.route(CAND, mod("any/file.py"), REAL) == "candidate"
    assert merge.route(CAND, mod(ENVELOPE), REAL) == "candidate"
    assert merge.route("spec/ws-approve-1-2", mod("x"), REAL) == "candidate"
    assert merge.route(FINAL, mod(ENVELOPE), REAL) == "finalize"
    assert (
        merge.route(FINAL, mod(ENVELOPE, "workstreams/b/approvals/m.yaml"), REAL)
        == "finalize"
    )
    assert merge.route("brief/x", mod("docs/a.md"), REAL) == "brief"


def test_route_stop_file_is_ambiguous_on_any_branch() -> None:
    for ref in (CAND, FINAL, "brief/x", "feat/x"):
        assert amb(merge.route(ref, mod(STOP), REAL)), ref
        assert amb(merge.route(ref, mod(ENVELOPE, STOP), REAL)), ref


STOP_REF = "stop/ws-n1-g2"


def test_stop_routes_on_one_added_file() -> None:
    added = [("added", STOP, None)]
    assert merge.route(STOP_REF, added, REAL) == "stop"
    assert merge.branch_kinds(STOP_REF, REAL) == {"stop"}
    dashed = "workstreams/a-b/approvals/stops/c-d-g12.yaml"
    assert merge.route("stop/a-b-c-d-g12", [("added", dashed, None)], REAL) == "stop"


def test_stop_modified_or_renamed_file_ambiguous() -> None:
    for change in (
        ("modified", STOP, None),
        ("removed", STOP, None),
        ("renamed", STOP, "docs/old.md"),
        ("renamed", STOP, STOP.replace("n1", "n2")),
        ("added", STOP, "docs/old.md"),
        ("", STOP, None),
    ):
        assert amb(merge.route(STOP_REF, [change], REAL)), change
    added = ("added", STOP, None)
    assert amb(merge.route(STOP_REF, [], REAL))
    assert amb(merge.route(STOP_REF, [added, added], REAL))
    assert amb(merge.route(STOP_REF, [added, *mod("docs/a.md")], REAL))
    assert amb(merge.route(STOP_REF, [("added", ENVELOPE, None)], REAL))


def test_stop_branch_path_mismatch_ambiguous() -> None:
    added = [("added", STOP, None)]
    for ref in (
        "stop/ws-n1-g3",  # generation differs
        "stop/ws-n2-g2",  # node differs
        "stop/other-n1-g2",  # workstream differs
        "stop/ws-n1-g02",  # not canonical
        "stop/ws-n1-g",
    ):
        assert amb(merge.route(ref, added, REAL)), ref
    # a stop record on another kind of branch is still ambiguous
    assert amb(merge.route(CAND, added, REAL))
    # the old path of a rename out of the stop directory counts
    moved = [("renamed", "docs/moved.md", STOP)]
    assert amb(merge.route(CAND, moved, REAL))
    assert amb(merge.route(STOP_REF, moved, REAL))


def _shapes(template: str) -> str:
    return "APPROVAL_STOP_TEMPLATE=" + template


@pytest.mark.parametrize(
    "template",
    [
        "stop/{ws_id}-{node}",  # no generation
        "stop/{ws_id}-g{generation}",  # no node
        "stop/{node}-g{generation}",  # no workstream
        "stop/{ws_id}-{node}-g{generation}-{ws_id}",  # twice
        "stop/{ws_id}-{node}-g{generation}-{extra}",  # unknown
    ],
)
def test_stop_template_of_another_shape_is_unavailable(template: str) -> None:
    text = GOOD.replace(
        "APPROVAL_STOP_TEMPLATE=stop/{ws_id}-{node}-g{generation}",
        _shapes(template),
    )
    with pytest.raises(Refused) as info:
        merge.parse_patterns(text)
    assert info.value.reason == "patterns_unavailable"


@pytest.mark.parametrize(
    ("ws", "node", "ref"),
    [
        ("ws", "x-g1", "stop/ws-x-g1-g2"),  # node that looks like a generation
        ("ws", "n", "stop/ws-n-g02"),  # not canonical
        ("ws", "n", "stop/ws--g2"),  # empty node
        ("ws", "n", "stop/ws-n-g2/extra"),  # extra segment
        ("ws", "n", "stop/ws-a/b-g1"),  # node spans a slash
        ("a-b", "c-d", "stop/a-b-c-d-g12"),
        ("ws", "n", "stop/ws-n-g2"),
        ("ws", "n", "stop/ws-n-g"),
    ],
)
def test_df_route_agrees_with_governance_parse(ws: str, node: str, ref: str) -> None:
    """Both copies of the branch parser take or refuse the same branches, the
    record path being the one the branch names."""
    parsed = approval_branches.parse_stop_branch(ref, ws)
    gen = parsed[1] if parsed else 2
    name = parsed[0] if parsed else node
    path = act_envelope.stop_path(ws, name, gen)
    routed = merge.route(ref, [("added", path, None)], REAL)
    assert (routed == "stop") == (parsed is not None), (ref, routed, parsed)


def test_stop_glob_equals_the_contract() -> None:
    template = REAL["APPROVAL_STOP_TEMPLATE"]
    assert template == approval_branches.stop_template()
    assert merge._template_glob(template) == approval_branches.stop_glob()
    ws, node, gen = "a-b", "c-d", 4
    ref = approval_branches.stop_branch(ws, node, gen)
    path = act_envelope.stop_path(ws, node, gen)
    assert merge.route(ref, [("added", path, None)], REAL) == "stop"
    assert approval_branches.parse_stop_branch(ref, ws) == (node, gen)


def test_route_finalize_with_other_paths_is_ambiguous() -> None:
    assert amb(merge.route(FINAL, mod(ENVELOPE, "src/x.py"), REAL))
    assert amb(merge.route(FINAL, mod(), REAL))
    assert amb(merge.route(FINAL, mod("workstreams/ws/approvals/stops/a.yaml"), REAL))
    assert amb(merge.route(FINAL, mod("workstreams/ws/approvals/x/y.yaml"), REAL))
    assert amb(merge.route(FINAL, mod("workstreams/ws/approvals/n1.yml"), REAL))


def test_route_outside_the_forms_is_ambiguous() -> None:
    assert amb(merge.route("feat/x", mod(ENVELOPE), REAL))
    assert amb(merge.route("", mod(ENVELOPE), REAL))


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
    "APPROVAL_STOP_TEMPLATE=stop/{ws_id}-{node}-g{generation}\n"
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
        "APPROVAL_STOP_TEMPLATE": "stop/{ws_id}-{node}-g{generation}",
    }


def test_merge_routes_through_the_release_tree(
    world: World, mgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    put_patterns(world, GOOD.replace("approve", "okay"))
    mgh.head_ref = "spec/ws-okay-1"
    assert reason(run(world, capsys)) == STAGE3
    mgh.head_ref = CAND  # a name of the real contract's form, not this tree's
    resp = run(world, capsys, request_id="req-00000002")
    assert reason(resp) == ("refused", "ambiguous_kind")


def test_merge_patterns_unavailable_is_refused(
    world: World, mgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    put_patterns(world, None)
    assert reason(run(world, capsys)) == ("refused", "patterns_unavailable")


# --- the operation ------------------------------------------------------------


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
    mgh.files = [{"filename": STOP, "status": "modified"}]
    assert reason(run(world, capsys)) == ("refused", "ambiguous_kind")


def test_merge_stop_pr_reaches_the_precheck(
    world: World,
    sgh: MergeGitHub,
    checker: FakePrecheck,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checker.outcome, checker.reason = "refuse", "stale_generation"
    assert reason(run(world, capsys)) == ("refused", "stale_generation")
    assert len(checker.requests) == 1


def test_rename_out_of_stops_does_not_escape(
    world: World, mgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    mgh.files = [
        {"filename": "docs/moved.md", "status": "renamed", "previous_filename": STOP}
    ]
    assert reason(run(world, capsys)) == ("refused", "ambiguous_kind")
    mgh.head_ref = FINAL
    mgh.files = [
        {"filename": ENVELOPE, "status": "renamed", "previous_filename": "src/old.py"}
    ]
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
    assert reason(resp) == STAGE3
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
    mgh.files = [{"filename": f"f{i}", "status": "added"} for i in range(299)]
    assert reason(run(world, capsys)) == STAGE3
    mgh.files = [{"filename": f"f{i}", "status": "added"} for i in range(300)]
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


def test_brief_branch_with_an_envelope_is_ambiguous() -> None:
    assert amb(merge.route("brief/x", mod(ENVELOPE), REAL))
    assert amb(merge.route("brief/x", mod("docs/a.md", ENVELOPE), REAL))
    assert merge.route("brief/x", mod("workstreams/ws/notes.md"), REAL) == "brief"


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

    keys = (
        "APPROVAL_CANDIDATE_TEMPLATE",
        "APPROVAL_FINALIZE_SUFFIX",
        "APPROVAL_STOP_TEMPLATE",
    )
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
    mgh.files = [{"filename": ENVELOPE, "status": "modified"}]
    mgh.head_repo = head_repo
    assert reason(run(world, capsys)) == ("refused", "head_repo_foreign")
    assert mgh.file_gets == 0


@pytest.mark.parametrize(
    "files",
    [
        [{"nofilename": 1}],
        [{"filename": 3}],
        ["str"],
        [{"filename": "a", "status": "modified", "previous_filename": 5}],
        [{"filename": "a"}],  # status is required
        [{"filename": "a", "status": 3}],
        [{"filename": "a", "status": ""}],
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


# --- stage 2b: kinds that are not merged -----------------------------------------


def _no_mutation(mgh: MergeGitHub) -> None:
    """Only tokens were minted (all read-only for the product); nothing was
    reviewed or merged."""
    posts = mgh.paths("POST")
    assert all(p == "/app/installations/9/access_tokens" for p in posts)
    scoped = [t for t in mgh.tokens if "repository_ids" in t]
    read = {"contents": "read", "pull_requests": "read"}
    assert scoped and all(t["permissions"] == read for t in scoped)
    assert all(m in ("GET", "POST", "DELETE") for m, _, _ in mgh.calls)
    assert not any(p.endswith("/merge") for _, p, _ in mgh.calls)
    assert not any(p.endswith("/reviews") for _, p, _ in mgh.calls)


def test_candidate_finalize_refused_stage3(
    world: World,
    mgh: MergeGitHub,
    checker: FakePrecheck,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for i, (ref, files, kind) in enumerate(
        ((CAND, ["a.py"], "candidate"), (FINAL, [ENVELOPE], "finalize"))
    ):
        mgh.head_ref = ref
        mgh.files = [{"filename": f, "status": "modified"} for f in files]
        resp = run(world, capsys, request_id=f"req-{i:08d}")
        assert reason(resp) == ("refused", f"kind_unavailable_until_stage3: {kind}")
        assert resp.evidence["kind"] == kind
    assert checker.requests == [] and world.files("intents") == []
    _no_mutation(mgh)


def test_brief_refused(
    world: World,
    mgh: MergeGitHub,
    checker: FakePrecheck,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mgh.head_ref = "brief/x"
    mgh.files = [{"filename": "a.md", "status": "modified"}]
    assert reason(run(world, capsys)) == ("refused", "brief_precheck_unavailable")
    assert checker.requests == []
    _no_mutation(mgh)


def test_no_mutating_calls(
    world: World,
    sgh: MergeGitHub,
    checker: FakePrecheck,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutating calls only for stop after a positive verdict: refused by
    the precheck, nothing but the read token's revocation; allowed, exactly
    one attestation POST and one PUT."""
    checker.outcome, checker.reason = "refuse", "no"
    assert reason(run(world, capsys)) == ("refused", "no")
    _no_mutation(sgh)
    assert sgh.revoked == 1
    checker.outcome, checker.reason = "allow", ""
    sgh.calls.clear()
    assert run(world, capsys, request_id="req-00000002").outcome == "done"
    mutating = [(m, p) for m, p, _ in sgh.calls if m in ("POST", "PUT")]
    mutating = [x for x in mutating if not x[1].endswith("/access_tokens")]
    assert mutating == [("POST", f"{PR_URL}/reviews"), ("PUT", MERGE_URL)]


def test_precheck_refusal_writes_no_state(
    world: World,
    sgh: MergeGitHub,
    checker: FakePrecheck,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checker.outcome, checker.reason = "refuse", "stale_generation"
    resp = run(world, capsys)
    assert reason(resp) == ("refused", "stale_generation")
    assert resp.evidence["precheck"]["admin_digest"] == ADMIN  # type: ignore[index]
    for name in ("intents", "results", "owners"):
        assert world.files(name) == [], name
    checker.outcome = "unset"
    resp = run(world, capsys, request_id="req-00000002")
    assert resp.outcome == "refused"
    assert (resp.reason or "").startswith("precheck_unset: ")
    assert world.files("intents") == [] and sgh.puts == []


def test_precheck_gets_the_publishers_facts(
    world: World,
    sgh: MergeGitHub,
    checker: FakePrecheck,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run(world, capsys)
    (got,) = checker.requests
    assert (got["kind"], got["pr"], got["head_sha"]) == ("stop", 12, SHA)
    assert (got["head_ref"], got["base_ref"], got["base_tip"]) == (
        STOP_REF,
        "master",
        TIP,
    )
    assert got["changes"] == [["added", STOP, None]]


@pytest.mark.parametrize(
    "case", ["too_many_files", "head_repo_foreign", "ambiguous_kind", "self_check"]
)
def test_precheck_allow_cannot_bypass_publisher_refusal(
    world: World,
    sgh: MergeGitHub,
    checker: FakePrecheck,
    capsys: pytest.CaptureFixture[str],
    case: str,
) -> None:
    """Requirement 4: the publisher's own checks run whatever the precheck
    would say (here it would allow anything) -- it is never even asked."""
    if case == "too_many_files":
        sgh.files = [{"filename": f"f{i}", "status": "added"} for i in range(300)]
    elif case == "head_repo_foreign":
        sgh.head_repo = 8
    elif case == "ambiguous_kind":
        sgh.files = [{"filename": STOP, "status": "modified"}]
    else:
        sgh.denied_apps = {42}
    resp = run(world, capsys)
    assert resp.outcome == "refused"
    if case != "self_check":
        assert resp.reason == case
    assert checker.requests == [] and sgh.puts == []
    assert world.files("intents") == []


# --- stage 2b: the stop merge ---------------------------------------------------


def phases(world: World) -> list[str]:
    """The phase names on disk, in the order of decision 8."""
    names = [n for n in world.files("intents") if n.count(".") > 1]
    found = {n.split(".", 1)[1].removesuffix(".json"): {} for n in names}
    order = [*store.PHASES[:2], *store.rechecks(found), *store.PHASES[2:]]
    return [n for n in order if n in found]


def owner_file(world: World) -> dict[str, object] | None:
    path = store.owner_path(world.lay, 7, 12)
    return json.loads(path.read_text()) if path.exists() else None


def key_of(world: World, **over: object) -> str:
    req = request.parse("merge", raw_of("merge", **over))
    return store.idempotency_key(EXEC, "merge", request.digest(req))


def crashes(world: World, capsys: pytest.CaptureFixture[str], **over: object) -> None:
    """One attempt that dies: `Crash` leaves the frame, nothing is answered."""
    with pytest.raises(Crash):
        run(world, capsys, **over)
    capsys.readouterr()


def crash_at(monkeypatch: pytest.MonkeyPatch, suffix: str) -> None:
    """Die once, right after the state file named `*<suffix>` is renamed in."""
    real = fsutil.fault_point

    def point(kind: fsutil.FaultKind, path: Path) -> None:
        real(kind, path)
        if kind == "renamed" and path.name.endswith(suffix):
            monkeypatch.setattr(fsutil, "fault_point", real)
            raise Crash

    monkeypatch.setattr(fsutil, "fault_point", point)


def test_stop_attested_then_merged_with_server_sha(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    resp = run(world, capsys)
    assert resp.outcome == "done", resp
    assert resp.result == {
        "merge_commit": COMMIT,
        "head_sha": SHA,
        "attestation_review_id": 100,
    }
    assert sgh.puts == [{"merge_method": "squash", "sha": SHA}]
    assert merge.MERGE_METHOD == "squash"
    assert sgh.intent_seen == [True] and sgh.armed_seen == [True]
    (review,) = sgh.reviews
    marker = merge.ATTESTATION_MARKER.format(kind="stop", key=key_of(world), sha=SHA)
    assert (review["body"], review["state"], review["commit_id"]) == (
        marker,
        "APPROVED",
        SHA,
    )
    writes = [t for t in sgh.tokens if t.get("permissions") == WRITE]
    assert writes == [{"repository_ids": [7], "permissions": WRITE}]
    assert phases(world) == ["attest", "attested", "armed", "put_answer"]
    assert owner_file(world) is None
    assert "premise" not in resp.evidence


def test_identical_retry_replays(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    first = run(world, capsys)
    again = run(world, capsys)
    assert (again.outcome, again.result) == ("done", first.result)
    assert again.evidence["replayed"] is True
    assert len(sgh.puts) == 1 and len(sgh.reviews) == 1


def test_crash_after_post_before_attested_recovers(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    sgh.post_mode = "crash_created"
    crashes(world, capsys)
    assert phases(world) == ["attest"] and len(sgh.reviews) == 1
    resp = run(world, capsys)
    assert resp.outcome == "done", resp
    assert len(sgh.reviews) == 1  # found, not posted again
    assert len(sgh.paths("POST")) - len(sgh.tokens) == 1
    assert len(sgh.puts) == 1


def test_exception_after_post_is_unknown_not_internal_refusal(
    world: World,
    sgh: MergeGitHub,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = store.phase

    def failing(res: store.Reservation, name: str, payload: Mapping) -> None:
        if name == "attested":
            raise RuntimeError("disk said no")
        real(res, name, payload)

    monkeypatch.setattr(store, "phase", failing)
    resp = run(world, capsys)
    assert reason(resp) == ("unknown", "state_exists: internal_error")
    monkeypatch.setattr(store, "phase", real)
    assert run(world, capsys).outcome == "done"
    assert len(sgh.reviews) == 1 and len(sgh.puts) == 1


def test_revoked_run_with_state_is_unknown(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    sgh.put_mode = "hold"
    assert reason(run(world, capsys)) == ("unknown", "merge_not_confirmed")
    revoked = snapshot("rel-a", entry("rel-a", "revoked"))
    _, resp = world.run("merge", raw_of("merge"), snap=revoked, capsys=capsys)
    assert resp.outcome == "unknown"
    assert (resp.reason or "").startswith("state_exists: release_revoked")
    assert owner_file(world) is not None and len(sgh.puts) == 1


def test_corrupt_phase_counts_as_state(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    sgh.put_mode = "hold"
    run(world, capsys)
    key = key_of(world)
    armed = store.phase_path(world.lay, key, "armed")
    armed.write_text("{not json")
    resp = run(world, capsys)
    assert reason(resp) == ("unknown", "state_exists: internal_error")
    (world.lay.publisher_dir / "intents" / f"{key}.json").unlink()
    store.owner_path(world.lay, 7, 12).unlink()
    assert store.has_state(world.lay, key)  # a damaged phase alone
    revoked = snapshot("rel-a", entry("rel-a", "revoked"))
    _, resp = world.run("merge", raw_of("merge"), snap=revoked, capsys=capsys)
    assert resp.outcome == "unknown"
    resp = run(world, capsys)  # phases without an intent: never a fresh start
    assert reason(resp) == ("unknown", "state_exists: internal_error")
    assert len(sgh.puts) == 1 and len(sgh.reviews) == 1


def test_attested_without_armed_continues(
    world: World,
    sgh: MergeGitHub,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crash_at(monkeypatch, ".attested.json")
    crashes(world, capsys)
    assert phases(world) == ["attest", "attested"] and sgh.puts == []
    resp = run(world, capsys)
    assert resp.outcome == "done", resp
    assert len(sgh.reviews) == 1 and len(sgh.puts) == 1


def test_crash_after_armed_before_put_then_other_request_busy(
    world: World,
    sgh: MergeGitHub,
    checker: FakePrecheck,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crash_at(monkeypatch, ".armed.json")
    crashes(world, capsys)
    assert phases(world)[-1] == "armed" and sgh.puts == []
    asked = len(checker.requests)
    other = run(world, capsys, request_id="req-B0000001")
    assert reason(other) == ("refused", "pr_busy")
    assert not store.has_state(world.lay, key_of(world, request_id="req-B0000001"))
    assert len(checker.requests) == asked  # refused before the precheck
    resp = run(world, capsys)
    assert reason(resp) == ("unknown", "merge_not_confirmed")
    assert sgh.puts == []  # no PUT ever after armed
    assert owner_file(world)["request_key"] == key_of(world)  # type: ignore[index]


def test_held_put_head_moves_and_returns_then_lands(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    """r4 N1: a held PUT₁ times out; the head moves to H₂ (unknown, owner
    kept), comes back to H, PUT₁ goes through; the repeat is done. No step
    ends in a final refusal or releases the ownership before that."""
    sgh.put_mode = "hold"
    answers = [run(world, capsys)]
    sgh.pr_head = SHA_B
    answers.append(run(world, capsys))
    sgh.pr_head = SHA
    answers.append(run(world, capsys))
    assert [reason(a) for a in answers] == [("unknown", "merge_not_confirmed")] * 3
    assert owner_file(world) is not None and world.files("results") == []
    sgh.release_held()
    resp = run(world, capsys)
    assert resp.outcome == "done", resp
    assert resp.evidence["premise"] == merge.PREMISE
    assert len(sgh.puts) == 1 and owner_file(world) is None


def test_put_in_flight_open_is_unknown_then_done(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    sgh.put_mode = "hold"
    assert reason(run(world, capsys)) == ("unknown", "merge_not_confirmed")
    sgh.release_held()
    assert run(world, capsys).outcome == "done"
    assert len(sgh.puts) == 1


SCENARIOS = [
    ("crash", ".attest.json"),
    ("crash", ".attested.json"),
    ("crash", ".armed.json"),
    ("crash", ".put_answer.json"),
    ("crash", "results"),
    ("put", "hold"),
    ("put", "lost_after"),
    ("put", "500"),
    ("put", "403"),
    ("put", "crash"),
    ("put", "409"),
    ("put", "405"),
]


@pytest.mark.parametrize(("what", "where"), SCENARIOS)
def test_single_put_ever(
    world: World,
    sgh: MergeGitHub,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    what: str,
    where: str,
) -> None:
    """Whatever fails and however often the request is repeated, the fake's
    journal shows at most one PUT."""
    if what == "crash" and where == "results":
        sgh.put_mode = "409"
        monkeypatch.setattr(store, "result", _dying_result(monkeypatch, store.result))
    elif what == "crash":
        crash_at(monkeypatch, where)
    else:
        sgh.put_mode = where
    outcomes = []
    for _ in range(4):
        try:
            outcomes.append(run(world, capsys).outcome)
        except Crash:
            capsys.readouterr()
            outcomes.append("crash")
        sgh.put_mode = "ok" if sgh.put_mode == "crash" else sgh.put_mode
    assert len(sgh.puts) <= 1, outcomes
    assert len(sgh.reviews) <= 1, outcomes


def _dying_result(
    monkeypatch: pytest.MonkeyPatch, real: Callable[..., bool]
) -> Callable[..., bool]:
    """`store.result` that dies once after the file is in place."""

    def result(res: store.Reservation, response: Response) -> bool:
        real(res, response)
        monkeypatch.setattr(store, "result", real)
        raise Crash

    return result


def test_single_attempt_409_is_verified_refusal(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    sgh.put_mode = "409"
    resp = run(world, capsys)
    assert reason(resp) == ("refused", "head_changed")
    assert world.files("results") != [] and owner_file(world) is None
    again = run(world, capsys)
    assert reason(again) == ("refused", "head_changed")
    assert again.evidence["replayed"] is True
    assert len(sgh.puts) == 1


def test_put_409_head_changed(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review focus 2: the head moves after `armed`; the PUT's own 409."""

    def move() -> None:
        sgh.pr_head = SHA_B

    sgh.before_put = move
    resp = run(world, capsys)
    assert reason(resp) == ("refused", "head_changed")
    assert phases(world)[-1] == "put_answer" and len(sgh.puts) == 1
    assert not sgh.merged


def test_head_change_after_precheck(
    world: World,
    sgh: MergeGitHub,
    checker: FakePrecheck,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Review focus 2: the head moves between the verdict and the final
    read: no `armed`, no PUT, `head_changed`; the repeat gives the same."""

    def move(n: int) -> None:
        sgh.pr_head = SHA_B

    checker.on_call = move
    resp = run(world, capsys)
    assert reason(resp) == ("refused", "head_changed")
    assert "armed" not in phases(world) and sgh.puts == []
    assert reason(run(world, capsys)) == ("refused", "head_changed")
    assert sgh.puts == [] and owner_file(world) is None


def test_put_405_open_not_mergeable(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    sgh.put_mode = "405"
    resp = run(world, capsys)
    assert reason(resp) == ("refused", "not_mergeable")
    assert world.files("results") != [] and owner_file(world) is None


def test_put_422_merged_otherwise_is_unknown(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    def human() -> None:
        sgh.execute({"sha": SHA}, by={"login": "human", "type": "User"})

    sgh.put_mode, sgh.before_put = "422", human
    assert reason(run(world, capsys)) == ("unknown", "merge_not_confirmed")
    assert owner_file(world) is not None


def test_lost_put_reconciles_done(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    sgh.put_mode = "lost_after"
    resp = run(world, capsys)
    assert resp.outcome == "done" and resp.result["merge_commit"] == COMMIT
    assert resp.evidence["premise"] == merge.PREMISE
    assert "put_answer" not in phases(world) and len(sgh.puts) == 1


def test_merged_by_human_is_unknown(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    sgh.put_mode = "500"
    assert reason(run(world, capsys)) == ("unknown", "merge_not_confirmed")
    sgh.execute({"sha": SHA}, by={"login": "human", "type": "User"})
    assert reason(run(world, capsys)) == ("unknown", "merge_not_confirmed")
    assert owner_file(world) is not None and len(sgh.puts) == 1


def test_other_tool_same_app_is_outside_guarantee(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    """r3 N3, the premise as a contract: a merge by the same App around the
    publisher in the ownership window is counted for the request -- and the
    evidence says what that rests on."""
    sgh.put_mode = "500"  # the publisher's PUT did nothing
    assert reason(run(world, capsys)) == ("unknown", "merge_not_confirmed")
    sgh.execute({"sha": SHA})  # another tool, the same App's bot
    resp = run(world, capsys)
    assert resp.outcome == "done"
    assert resp.evidence["premise"] == "app_merges_only_through_this_publisher"


def test_clock_jump_does_not_change_outcome(
    world: World,
    sgh: MergeGitHub,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sgh.put_mode = "hold"
    assert reason(run(world, capsys)) == ("unknown", "merge_not_confirmed")
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 40 * 86400)
    assert reason(run(world, capsys)) == ("unknown", "merge_not_confirmed")
    assert owner_file(world) is not None and world.files("results") == []
    sgh.release_held()
    assert run(world, capsys).outcome == "done"
    assert len(sgh.puts) == 1


def test_crash_after_result_before_release_replay_releases(
    world: World,
    sgh: MergeGitHub,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """r3 N2: A's final result is kept, the process dies before the
    release; A's repeat releases, then B goes through."""
    sgh.put_mode = "409"
    monkeypatch.setattr(store, "result", _dying_result(monkeypatch, store.result))
    crashes(world, capsys)
    assert world.files("results") != []
    assert owner_file(world)["request_key"] == key_of(world)  # type: ignore[index]
    again = run(world, capsys)
    assert reason(again) == ("refused", "head_changed")
    assert again.evidence["replayed"] is True and owner_file(world) is None
    sgh.pr_head, sgh.put_mode = SHA_B, "ok"
    resp = run(world, capsys, request_id="req-B0000001", head_sha=SHA_B)
    assert resp.outcome == "done", resp


def test_stale_owner_with_final_result_taken_over(
    world: World,
    sgh: MergeGitHub,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sgh.put_mode = "409"
    monkeypatch.setattr(store, "result", _dying_result(monkeypatch, store.result))
    crashes(world, capsys)
    stale = key_of(world)
    assert owner_file(world)["request_key"] == stale  # type: ignore[index]
    b_key = key_of(world, request_id="req-B0000001", head_sha=SHA_B)
    holders: list[object] = []

    def holder() -> None:
        found = owner_file(world)
        holders.append(None if found is None else found["request_key"])

    sgh.before_put = holder
    sgh.pr_head, sgh.put_mode = SHA_B, "ok"
    resp = run(world, capsys, request_id="req-B0000001", head_sha=SHA_B)
    assert resp.outcome == "done", resp
    assert holders == [b_key] and owner_file(world) is None


def test_owner_with_unknown_never_released(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    sgh.put_mode = "hold"
    run(world, capsys)
    key = key_of(world)
    for i in range(2):
        other = run(world, capsys, request_id=f"req-B000000{i}")
        assert reason(other) == ("refused", "pr_busy")
        assert reason(run(world, capsys)) == ("unknown", "merge_not_confirmed")
    assert owner_file(world)["request_key"] == key  # type: ignore[index]
    assert not store.release_pr(world.lay, 7, 12, "f" * 64)  # another key
    assert owner_file(world) is not None


def test_late_owner_loss_after_attested_is_verified_busy(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    """r5 N1: both passed the early check and reached `attested`; B took
    the PR first. A has no `armed` and no PUT, its refusal is final and
    replayed, B's ownership is intact; one PUT in all (B's)."""
    b_answers: list[Response] = []

    def b_runs() -> None:
        sgh.after_post = lambda: None
        sgh.put_mode = "hold"  # B stops past its PUT, still owning the PR
        b_answers.append(run(world, capsys, request_id="req-B0000001"))

    sgh.after_post = b_runs  # A's attestation is posted; B runs in between
    resp = run(world, capsys)
    assert reason(b_answers[0]) == ("unknown", "merge_not_confirmed")
    assert reason(resp) == ("refused", "pr_busy")
    a_key, b_key = key_of(world), key_of(world, request_id="req-B0000001")
    a_phases = [n for n in world.files("intents") if n.startswith(f"{a_key}.")]
    assert sorted(a_phases) == sorted(
        f"{a_key}.{n}" for n in ("json", "attest.json", "attested.json")
    )
    again = run(world, capsys)
    assert reason(again) == ("refused", "pr_busy") and again.evidence["replayed"]
    assert owner_file(world)["request_key"] == b_key  # type: ignore[index]
    assert len(sgh.puts) == 1 and len(sgh.reviews) == 2


def test_rechecked_twice_evidence_shows_decisive_and_initial(
    world: World,
    sgh: MergeGitHub,
    checker: FakePrecheck,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """r3 N5: two moves of the base tip, two rechecks; the evidence names
    the last as decisive and the first check as initial."""
    sgh.tips = [TIP, TIP_2, TIP_3, TIP_3]
    resp = run(world, capsys)
    assert resp.outcome == "done", resp
    assert phases(world) == [
        "attest",
        "attested",
        "rechecked.1",
        "rechecked.2",
        "armed",
        "put_answer",
    ]
    evidence = resp.evidence
    assert evidence["precheck"]["base_tip"] == TIP_3  # type: ignore[index]
    assert evidence["precheck_initial"]["base_tip"] == TIP  # type: ignore[index]
    assert [r["base_tip"] for r in checker.requests] == [TIP, TIP_2, TIP_3]


def test_base_tip_moved_rechecks(
    world: World,
    sgh: MergeGitHub,
    checker: FakePrecheck,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def advance(n: int) -> None:
        sgh.tip = TIP_2

    checker.on_call = advance
    resp = run(world, capsys)
    assert resp.outcome == "done", resp
    assert "rechecked.1" in phases(world) and len(checker.requests) == 2
    assert checker.requests[1]["base_tip"] == TIP_2
    assert resp.evidence["precheck"]["base_tip"] == TIP_2  # type: ignore[index]


def test_base_tip_moved_recheck_refused_is_verified(
    world: World,
    sgh: MergeGitHub,
    checker: FakePrecheck,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def advance(n: int) -> None:
        sgh.tip = TIP_2
        if n == 2:
            checker.outcome, checker.reason = "refuse", "stale_generation"

    checker.on_call = advance
    resp = run(world, capsys)
    assert reason(resp) == ("refused", "stale_generation")
    assert sgh.puts == [] and world.files("results") != []


def test_retarget_between_precheck_and_final_read_refused(
    world: World,
    sgh: MergeGitHub,
    checker: FakePrecheck,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """r2 N4 / Task 7 carry-over: `base.ref` of the final read is compared
    with the facts; a retarget is a verified refusal, no PUT."""

    def retarget(n: int) -> None:
        sgh.base_ref = "release"

    checker.on_call = retarget
    resp = run(world, capsys)
    assert reason(resp) == ("refused", "base_changed")
    assert sgh.puts == [] and world.files("results") != []
    assert owner_file(world) is None


def test_head_ref_renamed_before_final_read_refused(
    world: World,
    sgh: MergeGitHub,
    checker: FakePrecheck,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def rename(n: int) -> None:
        sgh.head_ref = "stop/ws-n1-g3"

    checker.on_call = rename
    assert reason(run(world, capsys)) == ("refused", "head_changed")
    assert sgh.puts == []


def test_lost_attestation_post_found_once_continues(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    sgh.post_mode = "timeout_created"
    resp = run(world, capsys)
    assert resp.outcome == "done", resp
    assert len(sgh.reviews) == 1 and resp.result["attestation_review_id"] == 100


def test_lost_attestation_post_not_found_unknown_no_repost(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    sgh.post_mode = "timeout_lost"
    first = run(world, capsys)
    assert reason(first) == ("unknown", "attestation_not_confirmed")
    sgh.post_mode = "ok"
    assert reason(run(world, capsys)) == ("unknown", "attestation_not_confirmed")
    posts = [p for m, p, _ in sgh.calls if m == "POST" and p.endswith("/reviews")]
    assert len(posts) == 1 and sgh.reviews == [] and sgh.puts == []
    # README step 4: a new request id attests again and merges
    assert run(world, capsys, request_id="req-00000002").outcome == "done"


def test_duplicate_attestations_unknown(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    sgh.post_mode = "duplicate"
    assert reason(run(world, capsys)) == ("unknown", "duplicate_attestations")
    assert sgh.puts == [] and owner_file(world) is None


def test_attestation_post_refused_is_verified(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    sgh.post_mode = "422"
    resp = run(world, capsys)
    assert resp.outcome == "refused" and (resp.reason or "").startswith("github_422")
    assert world.files("results") != [] and sgh.puts == []


def test_verified_refusal_kept_through_merge_wrapper(
    world: World,
    sgh: MergeGitHub,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: `merge()` must not turn a verified refusal into a plain
    one (the frame would then answer `unknown: state_exists`)."""
    sgh.put_mode = "405"
    resp = run(world, capsys)
    assert reason(resp) == ("refused", "not_mergeable")
    assert store.has_state(world.lay, key_of(world))

    def verified(ctx: object) -> Response:
        raise Refused("x", verified_not_done=True)

    monkeypatch.setattr(merge, "_decide", verified)
    with pytest.raises(Refused) as info:
        merge.merge(None)  # type: ignore[arg-type]
    assert info.value.verified_not_done


def test_owner_released_after_final_result(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    holders: list[object] = []
    sgh.before_put = lambda: holders.append(owner_file(world))
    assert run(world, capsys).outcome == "done"
    assert holders[0]["request_key"] == key_of(world)  # type: ignore[index]
    assert owner_file(world) is None and world.files("owners") == []


def test_evidence_complete_and_recovered_from_phase(
    world: World,
    sgh: MergeGitHub,
    checker: FakePrecheck,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sgh.put_mode = "hold"
    first = run(world, capsys)
    assert "precheck" in first.evidence
    sgh.release_held()
    resp = run(world, capsys)
    assert resp.outcome == "done"
    attest = store.phase_path(world.lay, key_of(world), "attest")
    stored = json.loads(attest.read_text())["payload"]["precheck"]
    evidence = resp.evidence
    assert evidence["precheck"] == stored == first.evidence["precheck"]
    assert set(stored) == {
        "version",
        "admin_digest",
        "revoke",
        "request_digest",
        "base_tip",
    }
    assert stored["revoke"] == "ok" and stored["version"] == "df-precheck/1"
    assert evidence["kind"] == "stop"
    assert evidence["attestation_review_id"] == 100
    assert evidence["merge_commit"] == COMMIT
    assert evidence["trust_root"] == {"unavailable": "no_binding"}
    assert "precheck_initial" not in evidence
    assert len(checker.requests) == 1  # metadata from the phase, not anew


def test_has_state_sees_phases_not_ownerships(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    sgh.put_mode = "hold"
    run(world, capsys)
    key = key_of(world)
    (world.lay.publisher_dir / "intents" / f"{key}.json").unlink()
    assert store.has_state(world.lay, key)  # the phases alone
    for name in ("attest", "attested", "armed"):
        store.phase_path(world.lay, key, name).unlink()
    assert owner_file(world) is not None
    assert not store.has_state(world.lay, key)  # an ownership is not scanned
    store.owner_path(world.lay, 7, 12).write_text("{broken")
    assert not store.has_state(world.lay, key)


def test_damaged_owner_of_another_pr_changes_nothing(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review M3: a damaged ownership of PR 99 neither turns a plain refusal
    of `publish-review` into `unknown` nor stops a merge of PR 12."""
    owners = world.lay.publisher_dir / "owners"
    owners.mkdir(mode=0o700)
    (owners / "7-99.json").write_text("{broken")
    sgh.pr_state = "closed"
    _, refused = world.run("publish-review", raw_of("publish-review"), capsys=capsys)
    assert reason(refused) == ("refused", "pr_state")
    sgh.pr_state = "open"
    resp = run(world, capsys, request_id="req-00000002")
    assert resp.outcome == "done", resp
    assert (owners / "7-99.json").read_text() == "{broken"


def test_damaged_owner_of_the_pr_keeps_it_busy(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    owners = world.lay.publisher_dir / "owners"
    owners.mkdir(mode=0o700)
    (owners / "7-12.json").write_text("{broken")
    assert reason(run(world, capsys)) == ("refused", "pr_busy")
    assert sgh.puts == [] and world.files("intents") == []


def test_retarget_after_final_read_merged_is_unknown(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review M1 (S2): the PR is retargeted between the final read and the
    PUT, and the merge lands on the other base: "merged otherwise"."""

    def retarget() -> None:
        sgh.base_ref = "release"

    sgh.before_put = retarget
    resp = run(world, capsys)
    assert reason(resp) == ("unknown", "merge_not_confirmed")
    assert sgh.merged and len(sgh.puts) == 1
    assert owner_file(world) is not None


@pytest.mark.parametrize("status", ["401", "403", "404"])
def test_put_4xx_open_is_verified_refusal(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str], status: str
) -> None:
    """Review M5: a definite negative answer to the one PUT (spec 5.4)."""
    sgh.put_mode = status
    resp = run(world, capsys)
    assert reason(resp) == ("refused", f"put_refused: {status}")
    assert phases(world)[-1] == "put_answer"
    assert world.files("results") != [] and owner_file(world) is None
    again = run(world, capsys)
    assert again.evidence["replayed"] is True and len(sgh.puts) == 1


def test_put_403_merged_otherwise_is_unknown(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    def human() -> None:
        sgh.execute({"sha": SHA}, by={"login": "human", "type": "User"})

    sgh.put_mode, sgh.before_put = "403", human
    assert reason(run(world, capsys)) == ("unknown", "merge_not_confirmed")


def test_resume_after_tip_move_rechecks(
    world: World,
    sgh: MergeGitHub,
    checker: FakePrecheck,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review M6: only the intent on disk, the base tip moved: a recheck on
    the current compare, not `pr_facts_changed`."""
    crash_at(monkeypatch, f"{key_of(world)}.json")  # the intent itself
    crashes(world, capsys)
    assert phases(world) == [] and world.files("intents") != []
    sgh.tip = TIP_2
    resp = run(world, capsys)
    assert resp.outcome == "done", resp
    assert checker.requests[-1]["base_tip"] == TIP_2
    assert resp.evidence["precheck"]["base_tip"] == TIP_2  # type: ignore[index]


def test_resume_after_retarget_is_refused(
    world: World,
    sgh: MergeGitHub,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crash_at(monkeypatch, f"{key_of(world)}.json")
    crashes(world, capsys)
    sgh.base_ref = "release"
    resp = run(world, capsys)
    assert reason(resp) == ("refused", "pr_facts_changed")
    assert world.files("results") != [] and sgh.puts == []


def test_unknown_past_intent_carries_evidence(
    world: World,
    sgh: MergeGitHub,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review M7: GitHub unavailable at the final read (past `attested`):
    the `unknown` names the kind, the precheck and the attestation."""
    real = sgh.route
    refs: list[int] = []

    def route(method: str, path: str, *rest: object) -> Reply:
        if "/git/ref/heads/" in path:
            refs.append(1)
            if len(refs) == 2:
                return 503, {}, b""
        return real(method, path, *rest)  # type: ignore[arg-type]

    monkeypatch.setattr(sgh, "route", route)
    resp = run(world, capsys)
    assert reason(resp) == ("unknown", "github_unavailable")
    assert resp.evidence["kind"] == "stop"
    assert resp.evidence["attestation_review_id"] == 100
    assert resp.evidence["precheck"]["base_tip"] == TIP  # type: ignore[index]


def test_doctor_lists_unresolved_owners(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    sgh.put_mode = "hold"
    run(world, capsys)
    ctx = doctor._Level2(world.snap, world.lay, True, product=7)
    text = doctor._owners(ctx)
    assert doctor.UNRESOLVED_OWNERS in text
    assert "PR 12" in text and RID in text and "фаза armed" in text
    assert doctor._owners(doctor._Level2(world.snap, world.lay, True, product=8)) == ""
    assert "publisher.owners" in doctor.LEVEL2_IDS
    sgh.release_held()
    assert run(world, capsys).outcome == "done"
    assert doctor._owners(ctx) == ""


def test_doctor_damaged_owner_is_broken(world: World) -> None:
    owners = world.lay.publisher_dir / "owners"
    owners.mkdir(mode=0o700)
    (owners / "7-12.json").write_text("{broken")
    ctx = doctor._Level2(world.snap, world.lay, True, product=7)
    check = doctor._run("publisher.owners", doctor._owners, ctx)
    assert check.status == doctor.BROKEN
    assert doctor.DAMAGED_OWNERS in check.reason and "PR 12" in check.reason
    other = doctor._Level2(world.snap, world.lay, True, product=8)
    assert doctor._run("publisher.owners", doctor._owners, other).status == "ok"


def test_doctor_damaged_phases_are_named(
    world: World, sgh: MergeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    sgh.put_mode = "hold"
    run(world, capsys)
    store.phase_path(world.lay, key_of(world), "armed").write_text("{bad")
    text = doctor._owners(doctor._Level2(world.snap, world.lay, True, product=7))
    assert "фазы нарушены" in text and "фаза intent" not in text


def test_readme_documents_unknown_procedure() -> None:
    """Owner decision 03.11: the operator's five steps of spec 5.4 are in the
    README, and the stage-2 section lists the four roles."""
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text()
    head = "### Издатель: исход `unknown` мержа"
    assert head in readme
    section = readme.split(head, 1)[1].split("\n### ", 1)[0]
    steps = [
        line
        for line in section.splitlines()
        if line[:3] in {f"{n}. " for n in range(1, 6)}
    ]
    assert [s[:2] for s in steps] == ["1.", "2.", "3.", "4.", "5."]
    for needle in (
        "darkfactory doctor",
        "publisher.owners",
        "**тот же** запрос",
        "**новым PR**",
        "attestation_not_confirmed",
        "вручную не удалять",
        "**только через один издатель**",
    ):
        assert needle in section, needle
    stage2 = readme.split("## Установка DarkFactory: этап 2", 1)[1].split("\n## ", 1)[0]
    for role in ("df-exec", "df-review", "df-publisher", "df-precheck"):
        assert f"`{role}`" in stage2.split("### ", 1)[0], role
    assert "/opt/df/libexec/df-precheck" in stage2
