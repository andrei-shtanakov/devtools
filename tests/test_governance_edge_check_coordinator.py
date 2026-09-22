"""Координатор edge-check волны (срез 2): состав рёбер, записи, леджер D9/D10."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governance import run_state as rs
from governance.edge_check import coordinator as co
from governance.edge_check import rules as r
from governance.edge_check.rules import EdgeCheckError

CONTRACTS = Path("contracts/edge-check/v1")
PROFILE = Path("profiles/team-exp.yaml")
NEW_EDGES = (
    "requirements-vs-charter",
    "design-vs-requirements-behaviour",
    "acceptance-vs-requirements-behaviour",
    "decomposition-vs-design-acceptance",
    "charter-vs-customer-brief",
    "charter-vs-engineer-brief",
)
FILES = {
    "charter": "00-charter.md",
    "requirements": "10-requirements.md",
    "behaviour-spec": "15-behaviour-spec.md",
    "design": "20-design.md",
    "acceptance": "25-acceptance.md",
    "decomposition": "30-decomposition.md",
}


def _artifacts() -> list[dict]:
    import yaml

    return yaml.safe_load(PROFILE.read_text(encoding="utf-8"))["artifacts"]


def test_catalogs_of_the_wave_edges_load_and_are_distinct() -> None:
    """Шесть наборов той же формы и того же загрузчика, что срез 1; charter
    разбит на два ребра, у инженерного — applicability."""
    loaded = {eid: r.load_rules(eid, CONTRACTS) for eid in NEW_EDGES}
    assert loaded["requirements-vs-charter"].basis_roles == ("charter",)
    assert loaded["design-vs-requirements-behaviour"].basis_roles == (
        "requirements", "behaviour-spec",
    )
    assert loaded["decomposition-vs-design-acceptance"].subject_role == "decomposition"
    assert loaded["charter-vs-customer-brief"].applicability == ()
    (rule,) = loaded["charter-vs-engineer-brief"].applicability
    assert (rule.id, rule.role) == ("R0-no-engineer-brief", "engineer-brief")
    assert all(len(rs_.items) >= 2 for rs_ in loaded.values())
    assert len({rs_.identity for rs_ in loaded.values()}) == len(NEW_EDGES)


def test_edges_for_level_follow_the_profile_upstream() -> None:
    arts = _artifacts()
    level3 = co.edges_for_level(arts, 3)
    assert [(e.node, e.edge_id) for e in level3] == [
        ("design", "design-vs-requirements-behaviour"),
        ("acceptance", "acceptance-vs-requirements-behaviour"),
    ]
    assert level3[0].bases == {
        "requirements": "requirements", "behaviour-spec": "behaviour-spec",
    }
    assert [(e.node, e.edge_id) for e in co.edges_for_level(arts, 0)] == [
        ("charter", "charter-vs-customer-brief"),
        ("charter", "charter-vs-engineer-brief"),
    ]
    assert [e.edge_id for e in co.edges_for_level(arts, 2)] == [
        "behaviour-vs-requirements"
    ]
    assert co.edges_for_level(arts, 5) == [], "делегат tasks не проверяется"


class _Ops:
    """Только `show_file`: основания читаются из base по ref."""

    def __init__(self, base: dict[str, str]) -> None:
        self.base = base
        self.reads: list[tuple[str, str]] = []

    def show_file(self, target_dir: str, ref: str, path: str) -> str | None:
        self.reads.append((ref, path))
        return self.base.get(path)


def _answer(edge_id: str, verdicts: dict[str, str]) -> str:
    """Ответ модели: `PASS` — все пункты pass; `FAIL` — блокирующая находка."""
    ruleset = r.load_rules(edge_id, CONTRACTS)
    wanted = verdicts.get(edge_id, "PASS")
    findings = []
    if wanted == "FAIL":
        findings = [{
            "rule_id": ruleset.items[0].id, "class": "major",
            "path": FILES[ruleset.subject_role], "lines": [1, 1],
            "statement": "не покрыто",
        }]
    return json.dumps({"structured_output": {
        "criteria": [{"id": it.id, "status": "pass", "reason": "ок"}
                     for it in ruleset.items],
        "findings": findings,
    }})


def _fake_call(verdicts: dict[str, str]):
    """`call` координатора: ребро узнаётся по тексту его первого пункта —
    промпт перечисляет пункты набора дословно."""
    def call(prompt: str) -> str:
        for eid in (*NEW_EDGES, "behaviour-vs-requirements"):
            if r.load_rules(eid, CONTRACTS).items[0].text in prompt:
                if verdicts.get(eid) == "RAISE":
                    raise RuntimeError("ревьюер недоступен")
                return _answer(eid, verdicts)
        raise AssertionError("ребро в промпте не опознано")
    return call


def _state(tmp_path: Path, wave_files: dict[str, str], brief: dict | None) -> rs.RunState:
    target = tmp_path / "target"
    for rel, text in wave_files.items():
        path = target / "spec" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    state = rs.new_run(
        subject="s", repo="r", repo_slug="o/r", ws_id="WS", target_dir=str(target),
        bundle_dir="spec", profile="profiles/team-exp.yaml", run_id="r-edge",
        authoring="waves",
    )
    state.base_ref = "master"
    state.brief = brief
    return state


def _base(*nodes: str) -> dict[str, str]:
    return {f"spec/{FILES[n]}": f"{n} approved\n" for n in nodes}


@pytest.fixture()
def runs_root(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    return tmp_path / "runs"


def test_run_level_writes_a_file_per_edge_and_reads_bases_from_base(
    tmp_path: Path, runs_root: Path,
) -> None:
    state = _state(tmp_path, {
        "20-design.md": "D-1\n", "25-acceptance.md": "AC-1\n",
        # В дереве волны лежат и upstream (ветка от base) — но координатор
        # обязан читать основания из BASE, не отсюда.
        "10-requirements.md": "worktree copy\n",
    }, brief=None)
    ops = _Ops(_base("requirements", "behaviour-spec"))
    run_dir = tmp_path / "run"
    result = co.run_level(state, ops, run_dir, 4, PROFILE, call=_fake_call({}))
    assert result.verdict == "PASS" and result.exit_code == 0
    assert set(result.records) == {
        ("design", "design-vs-requirements-behaviour"),
        ("acceptance", "acceptance-vs-requirements-behaviour"),
    }
    out = run_dir / "edge-check" / "w4"
    assert sorted(p.name for p in out.glob("*.json")) == [
        "acceptance--acceptance-vs-requirements-behaviour.json",
        "design--design-vs-requirements-behaviour.json",
    ]
    record = json.loads((out / "design--design-vs-requirements-behaviour.json").read_text())
    assert record["node"] == "design" and record["wave"] == 4
    assert {b["role"] for b in record["bases"]} == {"requirements", "behaviour-spec"}
    assert ("master", "spec/10-requirements.md") in ops.reads
    req = next(b for b in record["bases"] if b["role"] == "requirements")
    import hashlib
    assert req["sha256"] == hashlib.sha256(b"requirements approved\n").hexdigest()
    assert len(co.effective_results(run_dir)) == 2


def test_run_level_fail_and_error_codes(tmp_path: Path, runs_root: Path) -> None:
    state = _state(tmp_path, {"20-design.md": "D-1\n", "25-acceptance.md": "AC-1\n"}, None)
    ops = _Ops(_base("requirements", "behaviour-spec"))
    failed = co.run_level(
        state, ops, tmp_path / "run-fail", 4, PROFILE,
        call=_fake_call({"design-vs-requirements-behaviour": "FAIL"}),
    )
    assert failed.verdict == "FAIL" and failed.exit_code == 1
    assert failed.records[("design", "design-vs-requirements-behaviour")]["verdict"] == "FAIL"
    assert failed.records[("acceptance", "acceptance-vs-requirements-behaviour")]["verdict"] == "PASS"

    errored = co.run_level(
        state, ops, tmp_path / "run-err", 4, PROFILE,
        call=_fake_call({"acceptance-vs-requirements-behaviour": "RAISE"}),
    )
    assert errored.verdict == "ERROR" and errored.exit_code == 3
    rec = errored.records[("acceptance", "acceptance-vs-requirements-behaviour")]
    assert rec["error_code"] == "reviewer_failed"

    # Основание отсутствует в base — ERROR, не N/A (обязательное).
    missing = co.run_level(
        state, _Ops(_base("requirements")), tmp_path / "run-miss", 4, PROFILE,
        call=_fake_call({}),
    )
    assert missing.verdict == "ERROR"
    assert all(
        r_["error_code"] == "missing_mandatory_input" for r_ in missing.records.values()
    )


def test_w1_charter_has_two_edges_customer_mandatory_engineer_optional(
    tmp_path: Path, runs_root: Path,
) -> None:
    customer = {
        "frame": "customer", "primary": "00-discovery/brief.md",
        "requirements_source": "00-discovery/brief.md",
        "source_paths": ["00-discovery/brief.md"], "source_blobs": {"discovery-brief": "x"},
    }
    state = _state(tmp_path, {
        "00-charter.md": "G-1\n", "00-discovery/brief.md": "customer brief\n",
    }, customer)
    result = co.run_level(state, _Ops({}), tmp_path / "run", 1, PROFILE, call=_fake_call({}))
    assert result.verdict == "PASS" and result.exit_code == 0
    assert result.records[("charter", "charter-vs-customer-brief")]["verdict"] == "PASS"
    na = result.records[("charter", "charter-vs-engineer-brief")]
    assert na["verdict"] == "N/A" and na["absence"][0]["rule_id"] == "R0-no-engineer-brief"
    assert sorted(p.name for p in (tmp_path / "run/edge-check/w1").glob("*.json")) == [
        "charter--charter-vs-customer-brief.json",
        "charter--charter-vs-engineer-brief.json",
    ]

    failed = co.run_level(
        state, _Ops({}), tmp_path / "run2", 1, PROFILE,
        call=_fake_call({"charter-vs-customer-brief": "FAIL"}),
    )
    assert failed.verdict == "FAIL" and len(failed.records) == 2

    # Без брифа вовсе: customer-brief обязателен — ERROR.
    state.brief = None
    bare = co.run_level(state, _Ops({}), tmp_path / "run3", 1, PROFILE, call=_fake_call({}))
    assert bare.verdict == "ERROR"
    assert bare.records[("charter", "charter-vs-customer-brief")]["error_code"] == \
        "missing_mandatory_input"


def test_engineer_frame_checks_charter_against_both_briefs(
    tmp_path: Path, runs_root: Path,
) -> None:
    engineer = {
        "frame": "engineer", "primary": "00-discovery/engineer-brief.md",
        "requirements_source": "00-discovery/brief.md",
        "source_paths": ["00-discovery/engineer-brief.md", "00-discovery/brief.md"],
        "source_blobs": {"discovery-brief": "x", "discovery-customer": "y"},
    }
    state = _state(tmp_path, {
        "00-charter.md": "G-1\n", "00-discovery/brief.md": "customer\n",
        "00-discovery/engineer-brief.md": "engineer\n",
    }, engineer)
    result = co.run_level(state, _Ops({}), tmp_path / "run", 1, PROFILE, call=_fake_call({}))
    assert {k: v["verdict"] for k, v in result.records.items()} == {
        ("charter", "charter-vs-customer-brief"): "PASS",
        ("charter", "charter-vs-engineer-brief"): "PASS",
    }


def test_repeated_run_is_a_new_attempt_and_the_last_one_is_effective(
    tmp_path: Path, runs_root: Path,
) -> None:
    """D9/D10: неизменные входы → тот же ключ, новый `attempt_id`; действующий
    результат — последняя завершённая попытка (ERROR → PASS после повтора)."""
    state = _state(tmp_path, {"15-behaviour-spec.md": "BEH-1\n"}, None)
    ops = _Ops(_base("requirements"))
    run_dir = tmp_path / "run"
    first = co.run_level(
        state, ops, run_dir, 3, PROFILE,
        call=_fake_call({"behaviour-vs-requirements": "RAISE"}),
    )
    second = co.run_level(state, ops, run_dir, 3, PROFILE, call=_fake_call({}))
    a = first.records[("behaviour-spec", "behaviour-vs-requirements")]
    b = second.records[("behaviour-spec", "behaviour-vs-requirements")]
    assert a["result_key"] == b["result_key"] and a["attempt_id"] != b["attempt_id"]
    lines = (run_dir / "edge-check/ledger.jsonl").read_text().splitlines()
    assert len(lines) == 2
    effective = co.effective_results(run_dir)
    assert effective[b["result_key"]]["attempt_id"] == b["attempt_id"]
    assert effective[b["result_key"]]["verdict"] == "PASS"
    # Изменение основания меняет ключ (§6.2): прежний PASS не находится.
    third = co.run_level(
        state, _Ops({"spec/10-requirements.md": "changed\n"}), run_dir, 3, PROFILE,
        call=_fake_call({}),
    )
    c = third.records[("behaviour-spec", "behaviour-vs-requirements")]
    assert c["result_key"] != b["result_key"]


def test_level_without_nodes_is_a_config_error(tmp_path: Path, runs_root: Path) -> None:
    state = _state(tmp_path, {}, None)
    with pytest.raises(EdgeCheckError, match="no_edges|уровня"):
        co.run_level(state, _Ops({}), tmp_path / "run", 9, PROFILE, call=_fake_call({}))
