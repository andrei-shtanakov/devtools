"""report.md rendering (terminal review of #403)."""

from __future__ import annotations

import pytest

from selfcheck.report import render_markdown


def test_truncated_category_says_so() -> None:
    findings = [
        {
            "id": f"sc-{i:08x}",
            "rule": "ruff/X",
            "category": "quality",
            "confidence": "likely",
            "anchor": f"file:a{i}.py",
            "occurrences": 1,
        }
        for i in range(205)
    ]
    doc = {
        "run": {
            "run_id": "r",
            "host": "h",
            "scope": ["d"],
            "surface": {},
            "env": {},
            "warnings": [],
            "manifest": {"entries_read": 1, "repos": ["d"], "missing": []},
        },
        "probes": [],
        "findings": findings,
        "suppressed": [],
        "suppressed_no_env": {},
        "delta": {"statuses": {}, "gone": []},
        "inventory": {"llm": []},
    }
    text = render_markdown(doc)
    assert "показаны 200 из 205" in text


def _doc(scope: list[str], findings: list[dict], probes: list[dict]) -> dict:
    return {
        "run": {
            "run_id": "r",
            "host": "h",
            "scope": scope,
            "surface": {},
            "env": {},
            "warnings": [],
            "manifest": {"entries_read": 2, "repos": scope, "missing": []},
        },
        "probes": probes,
        "findings": findings,
        "suppressed": [],
        "suppressed_no_env": {},
        "delta": {"statuses": {}, "gone": []},
        "inventory": {
            "llm": [
                {
                    "repo": "b",
                    "path": "x.py",
                    "line": 1,
                    "mechanism": "A",
                    "candidate": False,
                    "features": [],
                }
            ]
        },
    }


def _finding(repo: str, anchor: str) -> dict:
    return {
        "id": f"sc-{repo}",
        "rule": "usage-graph/dead.file",
        "category": "dead",
        "confidence": "likely",
        "anchor": anchor,
        "occurrences": 1,
        "owner_repo": repo,
    }


def test_multi_repo_rows_are_distinguishable() -> None:
    doc = _doc(
        ["a", "b"],
        [_finding("a", "file:setup.sh"), _finding("b", "file:setup.sh")],
        [
            {
                "probe": "radon",
                "repo": "b",
                "status": "partial",
                "reason": "per-file problems",
                "exit_code": 0,
                "tool_version": "6",
                "canary": "hit",
                "coverage": {},
                "findings": 0,
            }
        ],
    )
    text = render_markdown(doc)
    assert "| a | usage-graph/dead.file |" in text
    assert "| b | usage-graph/dead.file |" in text
    assert "radon:partial (per-file problems)" in text
    assert "| b | x.py | 1 |" in text


def test_single_repo_keeps_s1_layout() -> None:
    text = render_markdown(_doc(["a"], [_finding("a", "file:x.sh")], []))
    assert "## Репо" not in text and "| правило | уверенность |" in text


def test_header_carries_surface_counts_not_maps() -> None:
    """#410: the per-file ``history`` map (hundreds of entries) belongs to
    report.json; the header shows its count. The plists stay listed: launchd
    completeness cannot be proven, so the report names them (§3.2.4, review
    of #429)."""
    doc = _doc(["a"], [], [])
    doc["run"]["surface"] = {
        "fleet": "absent",
        "sched_dir": "/s",
        "plists": ["a.plist", "b.plist"],
        "history": {f"f{i}.py": i % 2 == 0 for i in range(300)},
    }
    header = render_markdown(doc).split("## Пробы")[0]
    assert "f7.py" not in header and "history: 300" in header
    assert "plists: a.plist, b.plist" in header
    assert "fleet: absent" in header and "sched_dir: /s" in header


def _lintcov(repo: str, lang: str, s4: bool) -> dict:
    evidence = [{"kind": "workflows", "detail": "нет"}]
    if s4:
        evidence.append({"kind": "s4-return", "detail": "x"})
    return {
        "id": f"sc-{repo}{lang}",
        "rule": "lint-coverage/missing",
        "category": "ci",
        "confidence": "likely",
        "anchor": "file:Cargo.toml",
        "owner_repo": repo,
        "text_key": lang,
        "occurrences": 1,
        "evidence": evidence,
    }


def _lc_probe(repo: str, status: str) -> dict:
    return {
        "probe": "lint-coverage",
        "repo": repo,
        "status": status,
        "reason": "",
        "exit_code": None,
        "tool_version": None,
        "canary": None,
        "coverage": {},
        "findings": 0,
    }


def test_s4_return_condition_is_named_when_met() -> None:
    doc = _doc(
        ["a", "b"],
        [_lintcov("a", "rust", True), _lintcov("b", "python", False)],
        [_lc_probe("a", "ok"), _lc_probe("b", "ok")],
    )
    text = render_markdown(doc)
    assert "S4: условие возврата выполнено — a (rust)" in text


def test_s4_return_condition_not_met() -> None:
    doc = _doc(
        ["a", "b"],
        [_lintcov("b", "python", False)],
        [_lc_probe("a", "ok"), _lc_probe("b", "skipped")],
    )
    assert "S4: условие возврата не выполнено" in render_markdown(doc)


def test_s4_return_condition_unknown_when_probe_failed() -> None:
    doc = _doc(["a"], [], [_lc_probe("a", "failed")])
    assert "S4: условие возврата не установлено — lint-coverage: a" in (
        render_markdown(doc)
    )


def test_no_s4_line_without_the_probe() -> None:
    assert "S4:" not in render_markdown(_doc(["a"], [], []))


def test_s4_not_measured_outside_the_whole_manifest() -> None:
    """#462 review: a one-repo run saw no rust/elixir/ts repo — «no gaps» there
    would print the unmeasured as green."""
    doc = _doc(["a"], [], [_lc_probe("a", "ok")])
    doc["run"]["manifest"]["repos"] = ["a", "b"]
    text = render_markdown(doc)
    assert "S4: условие возврата не измерено — вне прогона: b" in text
    assert "не выполнено" not in text


def test_s4_not_measured_when_manifest_repos_are_missing() -> None:
    doc = _doc(["a"], [], [_lc_probe("a", "ok")])
    doc["run"]["manifest"]["missing"] = ["c"]
    assert "S4: условие возврата не измерено — вне прогона: c" in (render_markdown(doc))


# S4 line forms (#462 review round 2: «measured» is a probe row, not scope):
# (scope, manifest repos, missing, probe rows) → expected state.
S4_FORMS = [
    # --all, a manifest dir missing: in scope, never scanned
    (["a", "p"], ["a"], ["p"], [("a", "ok", "")], "не измерено — вне прогона: p"),
    # --all --path: every row narrowed
    (
        ["a"],
        ["a"],
        [],
        [("a", "skipped", "narrowed-corpus")],
        "не измерено — сужено --path: a",
    ),
    # narrowed and absent together: each named by its own reason (#464)
    (
        ["a", "p"],
        ["a", "b"],
        ["p"],
        [("a", "skipped", "narrowed-corpus")],
        "не измерено — сужено --path: a; вне прогона: b, p",
    ),
    # a repo without languages is measured: nothing to lint there
    (
        ["a", "v"],
        ["a", "v"],
        [],
        [("a", "ok", ""), ("v", "skipped", "no-inputs")],
        "не выполнено",
    ),
    # one-repo run
    (["a"], ["a", "b"], [], [("a", "ok", "")], "не измерено — вне прогона: b"),
    # failed beats unmeasured
    (
        ["a"],
        ["a", "b"],
        [],
        [("a", "failed", "x")],
        "не установлено — lint-coverage: a",
    ),
]


@pytest.mark.parametrize(
    ("scope", "repos", "missing_", "rows", "expected"),
    S4_FORMS,
    ids=[
        "missing-dir",
        "narrowed",
        "narrowed-and-absent",
        "no-languages",
        "one-repo",
        "failed-beats-unmeasured",
    ],
)
def test_s4_line_forms(scope, repos, missing_, rows, expected) -> None:
    doc = _doc(scope, [], [])
    doc["probes"] = [
        {**_lc_probe(r, status), "reason": reason} for r, status, reason in rows
    ]
    doc["run"]["manifest"]["repos"] = repos
    doc["run"]["manifest"]["missing"] = missing_
    assert f"S4: условие возврата {expected}" in render_markdown(doc)


def test_ast_dup_probe_row_points_to_groups_not_zero() -> None:
    """#437.3: группы ast-dup выпускает прогон, а не проба — строка пробы не
    утверждает «0 находок», число групп названо под таблицей."""
    probe = {**_lc_probe("a", "ok"), "probe": "ast-dup"}
    groups = [
        {
            **_finding("a", f"dup:{i}"),
            "id": f"g{i}",
            "rule": "ast-dup/exact",
            "category": "duplicate",
        }
        for i in range(2)
    ]
    text = render_markdown(_doc(["a"], groups, [probe]))
    row = next(line for line in text.splitlines() if line.startswith("| ast-dup |"))
    assert row.rstrip().endswith("| группы ↓ |")
    assert "ast-dup: групп дублей — 2" in text


def test_all_scope_table_shows_every_repo_within_the_cap() -> None:
    """#437.4: под --all таблица ограничена MD_ROWS, но показывает каждое
    репо, а не первые по алфавиту."""
    from selfcheck.report import MD_ROWS

    findings = [
        {**_finding(repo, f"f{i}"), "id": f"{repo}-{i}"}
        for repo in ("a", "b", "c")
        for i in range(MD_ROWS)
    ]
    text = render_markdown(_doc(["a", "b", "c"], findings, []))
    rows = [
        line
        for line in text.splitlines()
        if line.startswith("| ") and "usage-graph/dead.file" in line
    ]
    assert len(rows) == MD_ROWS
    assert {row.split("|")[1].strip() for row in rows} == {"a", "b", "c"}
