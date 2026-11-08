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
