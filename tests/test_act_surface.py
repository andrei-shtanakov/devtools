"""`act_surface`: поверхность заявки D16 — одна функция конвейера и издателя.

Спека df-installer §5.4 (rev 11): поверхность candidate — манифест `review/v1`
своей заявки, файлы её узлов и их downstream-замыкания, `00-discovery/*` для W1,
`workstreams/<ws>/evidence/edge-check/*.json` того же `ws` (O6). Функция чистая:
ей отдают пути (полный состав PR или один коммит), она возвращает чужие.
"""

from __future__ import annotations

import sys

from governance import approve_node, bundle_dag
from governance.act_request import ACT_REVIEW_V1, manifest_path
from governance.act_surface import EVIDENCE_RE, surface_violations
from tests.test_act_precheck import _closure

WS = "WS-1"
BUNDLE = f"workstreams/{WS}/spec"
KEY = "approve-1-0-1"
DAG = bundle_dag.BUNDLE_DAG
EVIDENCE = f"workstreams/{WS}/evidence/edge-check"


def _check(paths: list[str], nodes: list[str], act: str | None = ACT_REVIEW_V1):
    return surface_violations(
        paths,
        ws_id=WS,
        request_key=KEY,
        nodes=nodes,
        bundle_dir=BUNDLE,
        dag=DAG,
        act=act,
    )


def test_surface_full_pr_foreign_file_in_early_commit_refused() -> None:
    # полный состав PR = объединение коммитов: посторонний файл раннего коммита
    # остаётся в нём, даже когда последний (evidence) коммит чист
    early = [f"{BUNDLE}/20-design.md", "scripts/foreign.sh"]
    last = [f"{EVIDENCE}/design--requirements.json"]
    assert _check(sorted(set(early) | set(last)), ["design"]) == ["scripts/foreign.sh"]
    assert _check(last, ["design"]) == []


def test_surface_manifest_included() -> None:
    own = "workstreams/WS-1/approvals/requests/approve-1-0-1.yaml"
    assert own == manifest_path(WS, KEY)
    paths = [own, f"{BUNDLE}/20-design.md"]
    assert _check(paths, ["design"]) == []
    # манифест допускается только своей заявке и только у `review/v1`
    assert _check(paths, ["design"], act=None) == [own]
    foreign = manifest_path(WS, "approve-9-9-9")
    assert _check([foreign], ["design"]) == [foreign]


def test_surface_w1_discovery_allowed() -> None:
    brief = "workstreams/WS-1/spec/00-discovery/brief.md"
    assert _check([brief, f"{BUNDLE}/00-charter.md"], ["charter"]) == []
    # discovery несёт только W1 (charter)
    assert _check([brief], ["design"]) == [brief]


def test_surface_stale_downstream_allowed() -> None:
    # переодобрение requirements: каскад `stale` меняет файлы всего замыкания
    stale = [
        f"{BUNDLE}/10-requirements.md",
        f"{BUNDLE}/15-behaviour-spec.md",
        f"{BUNDLE}/20-design.md",
        f"{BUNDLE}/25-acceptance.md",
        f"{BUNDLE}/30-decomposition.md",
    ]
    assert _check(stale, ["requirements"]) == []
    # downstream design (decomposition) допущен, upstream (requirements) — нет
    upstream = "workstreams/WS-1/spec/10-requirements.md"
    design = [f"{BUNDLE}/20-design.md", f"{BUNDLE}/30-decomposition.md", upstream]
    assert _check(design, ["design"]) == [upstream]


def test_evidence_only_json() -> None:
    ok = f"{EVIDENCE}/design--requirements.json"
    bad = [
        "workstreams/WS-1/evidence/edge-check/design--requirements.md",
        "workstreams/WS-1/evidence/edge-check/nested/design--requirements.json",
        "workstreams/WS-1/evidence/edge-check/.json",
        "workstreams/WS-1/evidence/edge-check/design--requirements.json.bak",
        "workstreams/WS-OTHER/evidence/edge-check/design--requirements.json",
    ]
    assert EVIDENCE_RE.fullmatch(ok) is not None
    assert _check([ok, *bad], ["design"]) == sorted(bad)


def test_surface_closure_is_narrow() -> None:
    local, external = _closure("governance.act_surface")
    assert sorted(local) == [
        "governance",
        "governance.act_request",
        "governance.act_surface",
        "governance.bundle_dag",
    ]
    assert sorted(external - set(sys.stdlib_module_names) - {"yaml"}) == []
    # замыкание узла живёт в bundle_dag одно: копии в approve_node не осталось
    assert not hasattr(approve_node, "_downstream_closure")
    assert approve_node.downstream_closure is bundle_dag.downstream_closure
