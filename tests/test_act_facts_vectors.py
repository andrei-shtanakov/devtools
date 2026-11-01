"""Векторы сбора approval-facts v3 (steward §5): строгий прогон и адаптер devtools.

Строгий прогон — только `v3.run_vector` (решение M0-J, MUST): точный исход
(включая код `unavailable`), полное потребление обменов и общий порядок
запросов. Сверка адаптера — те же обмены через `act_facts`: перекладка полей
не теряет и не придумывает фактов.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("steward")

from steward.approvalfacts import v3  # noqa: E402

from governance import act_facts  # noqa: E402
from governance.act_policy import POLICY_FILES  # noqa: E402
from governance.act_source import SourceV2  # noqa: E402
from governance.act_time import Clock, Unavailable, parse_ts  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "contracts/approval-facts/v3"
VECTORS = CONTRACT / "vectors"
ALL = sorted(p for p in VECTORS.rglob("*.json"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _kind(kind: str) -> list[Path]:
    return [p for p in ALL if _load(p)["kind"] == kind]


def _id(path: Path) -> str:
    return path.relative_to(VECTORS).as_posix()


def test_policy_files_are_stewards() -> None:
    assert POLICY_FILES == v3.POLICY_FILES


@pytest.mark.parametrize("path", ALL, ids=_id)
def test_vector_strict(path: Path) -> None:
    outcome = v3.run_vector(_load(path))
    assert outcome.ok, outcome.problems


def _code(got: object) -> str:
    assert isinstance(got, Unavailable), got
    return got.reason.split(":", 1)[0]


@pytest.mark.parametrize("path", _kind("act"), ids=_id)
def test_adapter_act(path: Path) -> None:
    spec = _load(path)
    expect = spec["expect"]
    transport = v3.replay_transport(spec["transport"])
    got = act_facts.read(transport, spec["repo"], spec["pr"], spec["paths"])
    if "unavailable" in expect:
        assert _code(got) == expect["unavailable"]
        return
    assert not isinstance(got, Unavailable), got
    delivery, facts = got
    if expect.get("waiting_delivery"):
        assert facts is None and delivery.merged is False
        assert delivery.merge_commit is None
        return
    assert facts is not None
    clock = Clock(parse_ts(spec["t"]), frozenset(tuple(k) for k in spec["before"]))
    eff = act_facts.effective(facts, clock)
    assert not isinstance(eff, Unavailable), eff
    assert {login: r.state for login, r in eff.items()} == expect["effective"]
    for login, commit in expect.get("effective_commit", {}).items():
        assert eff[login].commit_id == commit
    for name, want in expect.get("delivery", {}).items():
        if name == "paths":
            pairs = {p["path"]: (p["blob_at_merge"], p["blob_at_head"]) for p in want}
            assert dict(delivery.blobs) == pairs
        else:
            assert getattr(delivery, name) == want, name


@pytest.mark.parametrize("path", _kind("open_review"), ids=_id)
def test_adapter_open_review(path: Path) -> None:
    spec = _load(path)
    got = act_facts.open_review(
        v3.replay_transport(spec["transport"]), spec["repo"], spec["pr"]
    )
    if "unavailable" in spec["expect"]:
        assert _code(got) == spec["expect"]["unavailable"]
        return
    clock = Clock(parse_ts(spec["t"]), frozenset(tuple(k) for k in spec["before"]))
    eff = act_facts.effective(got, clock)
    assert {k: r.state for k, r in eff.items()} == spec["expect"]["effective"]


def _source(spec: dict) -> SourceV2:
    branch = spec.get("branch", "main")
    return SourceV2(
        spec["repo"], branch, spec.get("anchor", "0" * 40), frozenset({"x"})
    )


def _as_record(a: act_facts.ActivationFact) -> dict:
    return {
        "pr": a.pr,
        "merge_commit": a.merge_commit,
        "merged_at": a.merged_at.strftime(act_facts.STAMP),
        "files_sha256": dict(a.files_sha256),
    }


@pytest.mark.parametrize("path", _kind("history"), ids=_id)
def test_adapter_history(path: Path) -> None:
    spec = _load(path)
    assert tuple(spec["files"]) == POLICY_FILES  # адаптер всегда читает один набор
    got = act_facts.history(
        v3.replay_transport(spec["transport"]),
        _source(spec),
        spec["anchor"],
        spec["until"],
    )
    expect = spec["expect"]
    if "unavailable" in expect:
        assert _code(got) == expect["unavailable"]
    elif "violation" in expect:
        assert isinstance(got, act_facts.HistoryBreak)
        assert got.reason.split(":", 1)[0] == expect["violation"]
    else:
        assert isinstance(got, tuple)
        assert [_as_record(a) for a in got] == expect["activations"]


@pytest.mark.parametrize("path", _kind("activation"), ids=_id)
def test_adapter_activation(path: Path) -> None:
    spec = _load(path)
    assert tuple(spec["files"]) == POLICY_FILES  # адаптер всегда читает один набор
    got = act_facts.activation(
        v3.replay_transport(spec["transport"]), _source(spec), spec["merge_commit"]
    )
    if "unavailable" in spec["expect"]:
        assert _code(got) == spec["expect"]["unavailable"]
    else:
        assert isinstance(got, act_facts.ActivationFact)
        assert _as_record(got) == spec["expect"]["activation"]


def test_review_focus_3_latest_review_commit_wins() -> None:
    """Review Focus 3 векторами M0 (заметки части A, п. 10)."""
    spec = _load(VECTORS / "login_approved_old_then_new.json")
    _, facts = act_facts.read(
        v3.replay_transport(spec["transport"]), spec["repo"], spec["pr"], spec["paths"]
    )
    eff = act_facts.effective(facts, Clock(parse_ts(spec["t"])))
    for login, commit in spec["expect"]["effective_commit"].items():
        assert eff[login].commit_id == commit


def test_review_focus_2_bot_login_two_forms() -> None:
    spec = _load(VECTORS / "bot_login_two_forms.json")
    assert v3.run_vector(spec).ok
    got = act_facts.read(
        v3.replay_transport(spec["transport"]), spec["repo"], spec["pr"], spec["paths"]
    )
    assert not isinstance(got, Unavailable)
    assert not got[0].merged_by_login.endswith("[bot]")  # О9: GraphQL-форма
