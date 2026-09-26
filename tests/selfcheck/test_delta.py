"""Task 13 — delta: fates, Δ1–Δ4, statuses, comparability key (§4.3, §2.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from selfcheck.delta import Fate, RunSnapshot, comparability_key, compute_delta, fate
from selfcheck.probes.base import ProbeResult, ProbeStatus
from selfcheck.probes.common import config_hash


def finding(
    fid: str, anchor: str, *, paths=("a.py",), related=None, probe: str = "ruff"
) -> dict:
    return {
        "id": fid,
        "probe": probe,
        "anchor": anchor,
        "owner_repo": "devtools",
        "occurrences": len(paths),
        "locations": [{"path": p, "line": 1} for p in paths],
        "related": related or [],
    }


def snap(
    tmp: Path, *, scope=("devtools",), corpus=None, keys=None, findings=(), missing=()
) -> RunSnapshot:
    corpus = corpus if corpus is not None else {"devtools": ["a.py"]}
    sources = {}
    for repo in scope:
        root = tmp / repo
        root.mkdir(exist_ok=True)
        for rel in corpus.get(repo, []):
            (root / rel).write_text("")
        sources[repo] = str(root)
    return RunSnapshot(
        run_id="r",
        scope=list(scope),
        materialized=[r for r in scope if r not in missing],
        probe_keys=keys
        if keys is not None
        else {f"{p}@{r}": "k" for r in scope for p in ("ruff", "ast-dup")},
        corpus={r: corpus.get(r, []) for r in scope},
        sources=sources,
        findings={f["id"]: f for f in findings},
    )


def test_new_persisting_changed(tmp_path: Path) -> None:
    base = snap(
        tmp_path, findings=[finding("a", "file:a.py"), finding("b", "file:a.py")]
    )
    cur = snap(
        tmp_path,
        findings=[
            finding("a", "file:a.py"),
            finding("b", "file:a.py", paths=("a.py", "b.py")),
            finding("c", "file:a.py"),
        ],
    )
    assert compute_delta(base, cur) == (
        {"a": "persisting", "b": "changed", "c": "new"},
        [],
    )


def test_line_shift_is_not_change(tmp_path: Path) -> None:
    moved = finding("a", "file:a.py")
    moved["locations"][0]["line"] = 40
    statuses, _ = compute_delta(
        snap(tmp_path, findings=[finding("a", "file:a.py")]),
        snap(tmp_path, findings=[moved]),
    )
    assert statuses == {"a": "persisting"}


def member(path: str, name: str, repo: str = "devtools") -> dict:
    return {"owner_repo": repo, "path": path, "line": 1, "member": name}


def test_participant_identity_includes_member(tmp_path: Path) -> None:
    old = finding(
        "d",
        "dup:exact:x",
        probe="ast-dup",
        paths=("a.py", "a.py"),
        related=[member("a.py", "f"), member("a.py", "g")],
    )
    new = finding(
        "d",
        "dup:exact:x",
        probe="ast-dup",
        paths=("a.py", "a.py"),
        related=[member("a.py", "f"), member("a.py", "h")],
    )
    statuses, _ = compute_delta(
        snap(tmp_path, findings=[old]), snap(tmp_path, findings=[new])
    )
    assert statuses == {"d": "changed"}


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ("checked", "resolved"),
        ("deleted", "resolved: file-removed"),
        ("excluded", "not-rechecked"),
        ("out-of-scope", "not-rechecked"),
        ("key-changed", "not-rechecked"),
        ("probe-failed", "not-rechecked"),
        ("repo-missing", "not-rechecked"),
    ],
)
def test_single_anchor_fates(tmp_path: Path, setup: str, expected: str) -> None:
    base = snap(tmp_path, findings=[finding("x", "func:a.py::f")])
    kwargs: dict = {}
    if setup in ("deleted", "excluded"):
        kwargs["corpus"] = {"devtools": []}
    if setup == "out-of-scope":
        kwargs |= {"scope": ("maestro",), "corpus": {"maestro": []}}
    if setup == "key-changed":
        kwargs["keys"] = {"ruff@devtools": "other"}
    if setup == "probe-failed":
        kwargs["keys"] = {"ruff@devtools": None}
    if setup == "repo-missing":
        kwargs["missing"] = ("devtools",)
    cur = snap(tmp_path, **kwargs)
    if setup == "deleted":
        (tmp_path / "devtools" / "a.py").unlink()
    _, gone = compute_delta(base, cur)
    assert gone == [{"id": "x", "status": expected, "anchor": "func:a.py::f"}]


DUP_MEMBERS = [member("a.py", "f"), member("m.py", "g", "maestro")]


@pytest.mark.parametrize(
    ("fates", "expected"),
    [
        (("checked", "checked"), "resolved"),  # Δ1
        (("deleted", "deleted"), "resolved: file-removed"),  # Δ2
        (("checked", "deleted"), "resolved"),  # Δ3
        (("checked", "out-of-scope"), "not-rechecked"),  # Δ4
        (("checked", "excluded"), "not-rechecked"),  # Δ4
        (("unverified", "checked"), "not-rechecked"),  # Δ4
    ],
)
def test_dup_fates(tmp_path: Path, fates: tuple[str, str], expected: str) -> None:
    corpus = {"devtools": ["a.py"], "maestro": ["m.py"]}
    base = snap(
        tmp_path,
        scope=("devtools", "maestro"),
        corpus=corpus,
        findings=[
            finding(
                "d",
                "dup:exact:abc",
                probe="ast-dup",
                paths=("a.py", "m.py"),
                related=DUP_MEMBERS,
            )
        ],
    )
    scope, cur_corpus = ["devtools", "maestro"], {k: list(v) for k, v in corpus.items()}
    keys = {"ast-dup@devtools": "k", "ast-dup@maestro": "k"}
    for m, f in zip(DUP_MEMBERS, fates, strict=True):
        repo = m["owner_repo"]
        if f in ("deleted", "excluded"):
            cur_corpus[repo] = []
        if f == "out-of-scope":
            scope.remove(repo)
            keys.pop(f"ast-dup@{repo}")
        if f == "unverified":
            keys[f"ast-dup@{repo}"] = None
    cur = snap(tmp_path, scope=tuple(scope), corpus=cur_corpus, keys=keys)
    for m, f in zip(DUP_MEMBERS, fates, strict=True):
        if f == "deleted":
            (tmp_path / m["owner_repo"] / m["path"]).unlink()
    assert compute_delta(base, cur)[1][0]["status"] == expected


def test_persisting_dup_marks_unverified_participant(tmp_path: Path) -> None:
    three = DUP_MEMBERS + [member("b.py", "h")]
    corpus = {"devtools": ["a.py", "b.py"], "maestro": ["m.py"]}
    base = snap(
        tmp_path,
        scope=("devtools", "maestro"),
        corpus=corpus,
        findings=[
            finding(
                "d",
                "dup:exact:abc",
                probe="ast-dup",
                paths=("a.py", "b.py", "m.py"),
                related=three,
            )
        ],
    )
    now = finding(
        "d",
        "dup:exact:abc",
        probe="ast-dup",
        paths=("a.py", "b.py"),
        related=DUP_MEMBERS[:1] + [member("b.py", "h")],
    )
    cur = snap(
        tmp_path,
        scope=("devtools",),
        corpus={"devtools": ["a.py", "b.py"]},
        findings=[now],
    )
    statuses, _ = compute_delta(base, cur)
    assert statuses == {"d": "changed"}
    marked = [r for r in cur.findings["d"]["related"] if r.get("unverified")]
    assert [(r["owner_repo"], r["path"], r["member"]) for r in marked] == [
        ("maestro", "m.py", "g")
    ]


def result(**kw) -> ProbeResult:
    return ProbeResult(
        probe=kw.get("probe", "usage-graph"),
        repo="devtools",
        status=ProbeStatus.OK,
        tool_version=kw.get("version"),
        config_hash=kw.get("config", ""),
    )


def test_internal_analyzer_key_tracks_logic_and_roles() -> None:
    base = comparability_key(
        result(version="selfcheck 0.1.0/logic 1", config="roles-a"),
        env_mode="no-env",
        surface={"fleet": "absent"},
        run_dir="/r",
    )
    assert base is not None
    assert base != comparability_key(
        result(version="selfcheck 0.1.0/logic 2", config="roles-a"),
        env_mode="no-env",
        surface={"fleet": "absent"},
        run_dir="/r",
    )
    assert base != comparability_key(
        result(version="selfcheck 0.1.0/logic 1", config="roles-b"),
        env_mode="no-env",
        surface={"fleet": "absent"},
        run_dir="/r",
    )


def test_key_tracks_env_mode_and_repo_config(tmp_path: Path) -> None:
    ruff = ProbeResult(
        "ruff",
        "devtools",
        ProbeStatus.OK,
        tool_version="0.16.9",
        argv=["ruff", "check", "--no-cache"],
    )
    keys = {
        comparability_key(ruff, env_mode=m, surface=None, run_dir="/r")
        for m in ("no-env", "checkout-venv")
    }
    assert len(keys) == 2
    (tmp_path / "ruff.toml").write_text('[lint]\nignore = ["F401"]\n')
    first = config_hash(tmp_path, ("ruff.toml",))
    (tmp_path / "ruff.toml").write_text("[lint]\n")
    assert config_hash(tmp_path, ("ruff.toml",)) != first


@pytest.mark.parametrize(
    ("scope", "keys", "expected"),
    [
        (("devtools",), {"ruff@devtools": "k"}, "resolved"),
        (("devtools",), {"ruff@devtools": None}, "not-rechecked"),
        (("maestro",), {}, "not-rechecked"),
    ],
)
def test_gone_instrument_finding(tmp_path: Path, scope, keys, expected) -> None:
    # Finding.probe of an instrument finding is "selfcheck" (rule prefix, §2.1):
    # the probe under judgement must come from the anchor, not from this field.
    item = {
        "id": "p",
        "probe": "selfcheck",
        "anchor": "probe:devtools#ruff",
        "owner_repo": "devtools",
        "occurrences": 1,
        "locations": [{"path": "-", "line": 1}],
        "related": [],
        "rule": "selfcheck/probe-failed",
    }
    base = snap(tmp_path, findings=[item])
    cur = snap(tmp_path, scope=scope, corpus={r: [] for r in scope}, keys=keys)
    assert compute_delta(base, cur)[1][0]["status"] == expected


def test_fate_values(tmp_path: Path) -> None:
    base = snap(tmp_path)
    assert fate(base, base, "devtools", "a.py", "ruff") is Fate.CHECKED
