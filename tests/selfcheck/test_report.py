"""report.md rendering (terminal review of #403)."""

from __future__ import annotations

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
        "не измерено — вне прогона: a",
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


def test_s4_line_forms() -> None:
    for scope, repos, missing_, rows, expected in S4_FORMS:
        doc = _doc(scope, [], [])
        doc["probes"] = [
            {**_lc_probe(r, status), "reason": reason} for r, status, reason in rows
        ]
        doc["run"]["manifest"]["repos"] = repos
        doc["run"]["manifest"]["missing"] = missing_
        assert f"S4: условие возврата {expected}" in render_markdown(doc), (
            scope,
            rows,
        )
