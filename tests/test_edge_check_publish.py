"""`edge_check/publish.surface_violations`: конвейер берёт поверхность D16 у общей
функции `act_surface` (O6) — ту же, что получает предпроверка издателя."""

from __future__ import annotations

from typing import Any

import pytest

from governance import act_surface, bundle_dag
from governance.act_request import ACT_REVIEW_V1, manifest_path
from governance.edge_check import publish
from governance.run_state import new_run

WS = "WS-1"
BUNDLE = f"workstreams/{WS}/spec"
KEY = "approve-1-0-1"
DAG = bundle_dag.BUNDLE_DAG
EVIDENCE = f"workstreams/{WS}/evidence/edge-check"
HEAD = "c0ffee"


class _Commit:
    """Ops-заглушка: состав коммита заявки — заданный список путей."""

    def __init__(self, files: list[str] | None) -> None:
        self.files = files

    def commit_files(self, target_dir: str, sha: str) -> list[str] | None:
        assert sha == HEAD
        return self.files


def _state() -> Any:
    return new_run("s", "r", "o/r", WS, "/t", BUNDLE, "default", "run-1", mode="strict")


CASES = [
    [f"{BUNDLE}/20-design.md", f"{EVIDENCE}/design--requirements.json"],
    [f"{BUNDLE}/20-design.md", f"{EVIDENCE}/design--requirements.md"],
    [manifest_path(WS, KEY), f"{BUNDLE}/30-decomposition.md"],
    [manifest_path(WS, "approve-9-9-9"), "scripts/foreign.sh"],
    [f"{BUNDLE}/00-discovery/brief.md", f"{EVIDENCE}/nested/x.json"],
]


@pytest.mark.parametrize("paths", CASES)
@pytest.mark.parametrize("nodes", [["design"], ["charter"]])
@pytest.mark.parametrize("act", [ACT_REVIEW_V1, None])
def test_pipeline_and_precheck_same_surface(
    paths: list[str], nodes: list[str], act: str | None, monkeypatch
) -> None:  # noqa: ANN001
    op = {"head_sha": HEAD, "nodes": nodes, "act": act}
    calls: list[list[str]] = []
    shared = act_surface.surface_violations

    def spy(got, **kwargs):  # noqa: ANN001, ANN003, ANN202
        calls.append(list(got))
        return shared(got, **kwargs)

    monkeypatch.setattr(act_surface, "surface_violations", spy)
    pipeline = publish.surface_violations(_state(), _Commit(paths), DAG, op, KEY)
    precheck = shared(
        paths,
        ws_id=WS,
        request_key=KEY,
        nodes=nodes,
        bundle_dir=BUNDLE,
        dag=DAG,
        act=act,
    )
    assert calls == [paths]  # конвейер не считает поверхность сам
    assert pipeline == precheck


def test_pipeline_unknown_commit_is_unresolved() -> None:
    op = {"head_sha": HEAD, "nodes": ["design"], "act": ACT_REVIEW_V1}
    with pytest.raises(RuntimeError, match="факт не установлен"):
        publish.surface_violations(_state(), _Commit(None), DAG, op, KEY)


# --- План 3c-2, Task 1: edge-check комментарием в `test` -----------------------

BOT = "bot"
PASS_RECORDS: dict[tuple[str, str], dict] = {
    ("design", "requirements"): {
        "verdict": "PASS",
        "subject": [{"path": "20-design.md", "sha256": "s1"}],
        "bases": [{"path": "10-requirements.md", "sha256": "b1"}],
    }
}


class _Forge:
    """Ops-заглушка форджи: ревью публикуются и читаются обратно (дедуп)."""

    def __init__(self) -> None:
        self.published: list[str] = []
        self.reviews: list[dict] = []
        self.body: str | None = None

    def pr_reviews(self, repo_slug: str, pr: int) -> list[dict]:
        return list(self.reviews)

    def latest_review_body(self, repo_slug: str, pr: int) -> str | None:
        return self.body

    def publish_review(self, repo_slug, pr, *, event, body, marker) -> bool:  # noqa: ANN001
        self.published.append(event)
        state = {"APPROVE": "APPROVED", "COMMENT": "COMMENTED"}[event]
        self.reviews.append({"login": BOT, "state": state})
        self.body = body
        return True


def _wave_state(mode: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    state = new_run("s", "r", "o/r", WS, "/t", BUNDLE, "default", "run-1", mode=mode)
    state.ops[KEY] = {"status": "proposed", "head_sha": HEAD, "nodes": ["design"]}

    def publish_request(state_, ops_, key, *, legacy_bundle=None):  # noqa: ANN001, ANN202
        state_.ops[key]["candidate_pr"] = 42

    monkeypatch.setattr(publish, "surface_violations", lambda *a, **k: [])
    monkeypatch.setattr(publish, "_already_in_head", lambda *a, **k: True)
    monkeypatch.setattr(publish, "review_login", lambda: BOT)
    monkeypatch.setattr(publish.an, "publish_request", publish_request)
    monkeypatch.setattr(publish.an, "pr_head", lambda *a, **k: HEAD)
    return state


@pytest.mark.parametrize(
    ("mode", "event"), [("test", "COMMENT"), ("strict", "APPROVE")]
)
def test_publish_wave_event_by_mode_and_dedup(
    mode: str, event: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _wave_state(mode, monkeypatch)
    forge = _Forge()
    assert publish.publish_wave(state, forge, KEY, PASS_RECORDS) == publish.CODE_OK
    assert forge.published == [event]
    assert publish.publish_wave(state, forge, KEY, PASS_RECORDS) == publish.CODE_OK
    assert forge.published == [event], "повтор на той же голове — без второй"


def test_publish_wave_test_mode_republishes_on_new_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Дедуп в `test` — по маркеру с ЭТОЙ головой, не по одному COMMENTED."""
    state = _wave_state("test", monkeypatch)
    forge = _Forge()
    forge.reviews = [{"login": BOT, "state": "COMMENTED"}]
    forge.body = f"<!-- {publish.EDGE_MARKER} head=old -->"
    assert publish.publish_wave(state, forge, KEY, PASS_RECORDS) == publish.CODE_OK
    assert forge.published == ["COMMENT"]


def test_publish_wave_fail_publishes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _wave_state("test", monkeypatch)
    forge = _Forge()
    fail = {k: {**v, "verdict": "FAIL"} for k, v in PASS_RECORDS.items()}
    assert publish.publish_wave(state, forge, KEY, fail) == publish.CODE_CHANGES
    assert forge.published == [] and "candidate_pr" not in state.ops[KEY]


def test_edge_verdict_carries_head_verdict_and_input_hashes() -> None:
    assert publish.edge_verdict(HEAD, PASS_RECORDS) == {
        "head": HEAD,
        "verdict": "PASS",
        "inputs": {"design--requirements": ["b1", "s1"]},
    }
