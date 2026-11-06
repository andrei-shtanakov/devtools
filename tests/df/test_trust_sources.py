"""The git-backed trust sources of the root rows meet both real readers.

`tests/df/trust_sources.py` stands for GitHub in the stage-3a root world
(Task 14), where nothing can be patched. This holds its answers to the real
code on both sides, in process: the publisher's `trust-status` (its history
reads through `trust_routes.HISTORY_SEND`) asks the real checker
(`act_precheck.serve`, its `AnonTransport` on `GitRest.open_url`) and the
root becomes `clear` on the head of `df-trust`.
"""

from __future__ import annotations

import dataclasses
import io
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("steward")

from df import platform  # noqa: E402
from df.config import InstallConfig  # noqa: E402
from df.publisher import (
    merge,  # noqa: E402
    trust_routes,  # noqa: E402
)
from df.publisher.request import Response, perimeter_result  # noqa: E402
from df.stage2_config import dump_publisher, parse_publisher  # noqa: E402
from governance import (
    act_anon,  # noqa: E402
    approval_branches,  # noqa: E402
)
from governance.act_precheck import DEFAULT_DEPS, serve  # noqa: E402
from tests.df.candidate_world import Candidate, build_candidate  # noqa: E402
from tests.df.conftest import FakeLayout  # noqa: E402
from tests.df.fake_github import FakeGitHub, serve_fake_github  # noqa: E402
from tests.df.launch_world import (  # noqa: E402
    ADMIN,  # noqa: E402
    put_record,
    record,
)
from tests.df.publisher_world import World, build_world, raw_of  # noqa: E402
from tests.df.roles_world import FULL_NAME, OPENSSL  # noqa: E402

PAT = "github_pat_dfFixturePatNotASecret0000000"
from tests.df.trust_sources import (  # noqa: E402
    API,
    HISTORY_TOKEN,
    TRUST_REPO,
    GitRest,
    TrustSources,
    build_sources,
    fake_gh,
    history_profile,
    new_trust_repo,
)


@pytest.fixture()
def sources(tmp_path: Path) -> TrustSources:
    return build_sources(tmp_path / "sources")


@pytest.fixture()
def world(
    fake_layout: FakeLayout,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sources: TrustSources,
) -> World:
    """The publisher's frame bound to `sources` with a keyed binding; the
    real checker in process behind `run_supervised`."""
    from df.publisher import perimeter

    w = build_world(fake_layout, monkeypatch)
    w.lay.perimeter_dir.mkdir(mode=0o700)
    perimeter.create_db(w.lay)
    gh = fake_gh(tmp_path / "gh-bin")
    w.lay.role_home(trust_routes.PUBLISHER_USER).mkdir(parents=True)
    (w.lay.etc / "trust.toml").write_text(
        sources.trust_toml(history_profile(tmp_path / "history-gh"))
    )
    monkeypatch.setattr(trust_routes, "approved_gh", lambda _s: gh)
    monkeypatch.setattr(trust_routes, "HISTORY_SEND", sources.rest.send)
    real = act_anon._default_open()

    def open_url(req: object, timeout: float) -> object:
        """`api.github.com` is the sources'; the product's loopback is real."""
        if str(getattr(req, "full_url", "")).startswith(API):
            return sources.rest.open_url(req, timeout)
        return real(req, timeout=timeout)

    monkeypatch.setattr(act_anon, "_default_open", lambda: open_url)
    home = tmp_path / "precheck-home"
    home.mkdir()
    monkeypatch.setattr(platform, "run_supervised", checker(home, gh))
    return w


def checker(home: Path, gh: Path):  # noqa: ANN201
    """`run_supervised` of the precheck command: the real `serve`."""

    def run(argv: object, data: bytes, **_kw: object) -> platform.SupervisedResult:
        out = io.StringIO()
        code = serve(
            io.BytesIO(data),
            out,
            admin_digest=ADMIN,
            home=home,
            complete_v2=True,
            gh=gh,
            git=Path("/usr/bin/git"),
            deps=DEFAULT_DEPS,
        )
        return platform.SupervisedResult(
            code, None, out.getvalue().encode("utf-8"), False, 0.1
        )

    return run


def trust_status(world: World, capsys: pytest.CaptureFixture[str]) -> Response:
    raw = raw_of("trust-status", request_id="req-trst-0001")
    return world.run("trust-status", raw, capsys=capsys)[1]


def test_trust_status_clear_on_the_head(
    world: World, sources: TrustSources, capsys: pytest.CaptureFixture[str]
) -> None:
    resp = trust_status(world, capsys)
    assert resp.outcome == "done", resp
    head = sources.trust.head()
    assert resp.result["state"] == "clear", resp.result
    assert resp.result["snapshot_sha"] == head
    assert resp.result["last_epoch"] == 1
    # every trust-source read of both sides carried the history token
    rest: GitRest = sources.rest
    assert rest.calls and all(HISTORY_TOKEN in auth for _p, auth in rest.calls)
    assert perimeter_result("clear", None, head, 1).items() <= resp.result.items()


def test_setup_trust_check_has_no_stop(sources: TrustSources) -> None:
    """The perimeter check of `setup-trust` (the human's PAT) on the two
    sources: no stop, the head and `source.env` read."""
    from df import setup_trust
    from df.publisher import history

    pat = history.history_transport(PAT, send=sources.rest.send)
    report = setup_trust.check_perimeter(
        pat, sources.policy.id, sources.trust.id, trust_branch="main"
    )
    assert report.unverified == tuple(
        u for u in report.unverified if u.startswith("apps@")
    )
    assert report.stops == ()
    assert report.trust_head == sources.trust.head()


def test_trust_status_two_epochs_clear(
    world: World, sources: TrustSources, capsys: pytest.CaptureFixture[str]
) -> None:
    head = sources.add_epochs(2)
    resp = trust_status(world, capsys)
    assert resp.result["state"] == "clear", resp.result
    assert (resp.result["snapshot_sha"], resp.result["last_epoch"]) == (head, 2)


def test_trust_status_65_epochs_clear_without_ranges(
    world: World, sources: TrustSources, capsys: pytest.CaptureFixture[str]
) -> None:
    """65 epochs grown one valid transition per commit: `trust-status` has
    no ranges, so no epoch limit (`unsupported_size: epochs` is the `merge`
    route's, `act_trust_findings.range_descriptor`)."""
    head = sources.add_epochs(65)
    resp = trust_status(world, capsys)
    assert resp.result["state"] == "clear", resp.result
    assert (resp.result["snapshot_sha"], resp.result["last_epoch"]) == (head, 65)


# ---------- the merge route: a candidate through the real checker ----------


@pytest.fixture()
def merge_world(
    world: World, tmp_path: Path, sources: TrustSources
) -> Iterator[tuple[World, Candidate, FakeGitHub]]:
    """The publisher of product 1 against the product's fake GitHub (HTTP,
    the App key, the installation) and its git server, a run of the bound
    root, the release's `patterns.env`, and the open candidate PR."""
    from tests.df.precheck_world import TokenGitServer
    from tests.df.roles_world import (
        APP_ID,
        PRODUCT_ID,
        make_app_key,
        products_text,
        scenario,
    )

    lay = world.lay
    work = tmp_path / "product-side"
    work.mkdir()
    key, public = make_app_key(work)
    with serve_fake_github(scenario(), public_key=public, openssl=OPENSSL) as fake:
        cand = build_candidate(
            work / "product", FULL_NAME, PRODUCT_ID, sources, fake.scenario
        )
        server = TokenGitServer(cand.repo.served_root, lambda t: t in fake.tokens)
        try:
            cfg = parse_publisher((lay.etc / "publisher.toml").read_text())
            cfg = dataclasses.replace(cfg, api_base=fake.url, git_base=server.base)
            (lay.etc / "publisher.toml").write_text(dump_publisher(cfg))
            (lay.etc / "products.toml").write_text(products_text())
            lay.secrets_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
            shutil.copyfile(key, lay.app_key(APP_ID))
            cli = {n: Path(f"/bin/{n}") for n in ("git", "uv", "tar")}
            cli["openssl"] = OPENSSL
            install = InstallConfig("o/m", "a" * 40, "core", cli)
            world.snap = dataclasses.replace(world.snap, install=install)
            patterns = merge.patterns_path(lay, world.snap)
            patterns.parent.mkdir(parents=True, exist_ok=True)
            patterns.write_text(approval_branches.PATTERNS_PATH.read_text())
            rec = dataclasses.replace(
                record("run-cand", "rel-a"),
                product=PRODUCT_ID,
                root_id=("github.com", sources.trust.id, "main"),
            )
            put_record(world.fake, rec)
            yield world, cand, fake
        finally:
            server.close()


def merge_raw(cand: Candidate, rid: str = "req-cand-0001") -> bytes:
    from tests.df.roles_world import PRODUCT_ID

    return raw_of(
        "merge",
        request_id=rid,
        run_id="run-cand",
        repository_id=PRODUCT_ID,
        pr=cand.pr,
        head_sha=cand.head,
    )


def test_candidate_merged_through_the_real_checker(
    merge_world: tuple[World, Candidate, FakeGitHub],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The positive candidate: the root `clear` at X (`trust-status`),
    then `merge` -- the publisher's facts, the `merge` observation, the real
    checker's v2 `allow`, the admission, the one `PUT`: `done`, merged."""
    world, cand, fake = merge_world
    assert trust_status(world, capsys).result["state"] == "clear"
    _code, resp = world.run("merge", merge_raw(cand), capsys=capsys)
    assert (resp.outcome, resp.reason) == ("done", None), resp
    assert cand.merged and fake.scenario.pulls[(FULL_NAME, cand.pr)]["merged"]


def test_finalize_merged_through_the_real_checker(
    merge_world: tuple[World, Candidate, FakeGitHub],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """After the candidate: the finalize PR with exactly the envelope the
    delivery decision implies -- the real checker's v2 `allow`, the App's
    attestation, the one `PUT`: `done`, merged."""
    from tests.df.candidate_world import build_finalize
    from tests.df.roles_world import PRODUCT_ID

    world, cand, fake = merge_world
    assert trust_status(world, capsys).result["state"] == "clear"
    assert world.run("merge", merge_raw(cand), capsys=capsys)[1].outcome == "done"
    pr, head = build_finalize(cand, fake.scenario, PRODUCT_ID)
    raw = raw_of(
        "merge",
        request_id="req-final-0001",
        run_id="run-cand",
        repository_id=PRODUCT_ID,
        pr=pr,
        head_sha=head,
    )
    _code, resp = world.run("merge", raw, capsys=capsys)
    assert (resp.outcome, resp.reason) == ("done", None), resp
    assert fake.scenario.pulls[(FULL_NAME, pr)]["merged"]


def test_candidate_65_epochs_unsupported_then_blocked(
    merge_world: tuple[World, Candidate, FakeGitHub],
    sources: TrustSources,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """65 epochs: `trust-status` stays `clear` (no ranges), the candidate's
    `op: merge` is `unsupported_size: epochs` -- K, the root `blocked`."""
    world, cand, fake = merge_world
    sources.add_epochs(65)
    assert trust_status(world, capsys).result["state"] == "clear"
    _code, resp = world.run("merge", merge_raw(cand), capsys=capsys)
    after = trust_status(world, capsys).result
    assert resp.outcome == "refused" and "unsupported_size: epochs" in str(resp.reason)
    assert after["state"] == "blocked"
    assert not fake.scenario.pulls[(FULL_NAME, cand.pr)]["merged"]
    # the exit: a new `df-trust` of one epoch, bound in place of the old
    new = new_trust_repo(sources, 23, "df-fixture/df-trust-2")
    text = (world.lay.etc / "trust.toml").read_text()
    old = f'df_trust_repository_id = 21\ndf_trust_full_name = "{TRUST_REPO}"'
    rebound = text.replace(
        old, f'df_trust_repository_id = 23\ndf_trust_full_name = "{new.full_name}"'
    )
    (world.lay.etc / "trust.toml").write_text(rebound)
    fresh = trust_status(world, capsys).result
    assert (fresh["state"], fresh["root"]["repository_id"]) == ("clear", 23), fresh
