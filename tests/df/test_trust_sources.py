"""The git-backed trust sources of the root rows meet both real readers.

`tests/df/trust_sources.py` stands for GitHub in the stage-3a root world
(Task 14), where nothing can be patched. This holds its answers to the real
code on both sides, in process: the publisher's `trust-status` (its history
reads through `trust_routes.HISTORY_SEND`) asks the real checker
(`act_precheck.serve`, its `AnonTransport` on `GitRest.open_url`) and the
root becomes `clear` on the head of `df-trust`.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

pytest.importorskip("steward")

from df import platform  # noqa: E402
from df.publisher import trust_routes  # noqa: E402
from df.publisher.request import Response, perimeter_result  # noqa: E402
from governance import act_anon  # noqa: E402
from governance.act_precheck import DEFAULT_DEPS, serve  # noqa: E402
from tests.df.conftest import FakeLayout  # noqa: E402
from tests.df.launch_world import ADMIN  # noqa: E402
from tests.df.publisher_world import World, build_world, raw_of  # noqa: E402

PAT = "github_pat_dfFixturePatNotASecret0000000"
from tests.df.trust_sources import (  # noqa: E402
    HISTORY_TOKEN,
    GitRest,
    TrustSources,
    build_sources,
    fake_gh,
    history_profile,
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
    monkeypatch.setattr(act_anon, "_default_open", lambda: sources.rest.open_url)
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
