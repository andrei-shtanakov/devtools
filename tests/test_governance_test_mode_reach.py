"""Недостижимость пути `review/v1` в прогоне `test` (ревью #721, план 3c-3)."""

from __future__ import annotations

from typing import Any

from governance import act_node, act_status
from governance import run_state as rs
from governance.act_settings import ActSettings


def test_test_run_creates_no_review_v1_requests() -> None:
    state = rs.new_run(
        mode="test",
        subject="s",
        repo="alpha",
        repo_slug="owner/alpha",
        ws_id="WS-T1",
        target_dir="/tmp/alpha",
        bundle_dir="workstreams/WS-T1/spec",
        profile="profiles/team-exp.yaml",
        run_id="r-reach",
    )
    deps: Any = act_status.Deps(
        readers=None,
        binding=None,
        journal=None,
        creds=(),
        gh=None,
        now=None,
        sleep=None,
        settings=ActSettings(True, False),
    )
    made = act_node.creation(state, object(), deps, act_status.TEST_LIVE_POLICY)  # type: ignore[arg-type]
    assert made.act is None
