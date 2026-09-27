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
    """#410: the per-file ``history`` map (hundreds of entries) and the plist
    list belong to report.json; the header shows counts."""
    doc = _doc(["a"], [], [])
    doc["run"]["surface"] = {
        "fleet": "absent",
        "sched_dir": "/s",
        "plists": ["a.plist", "b.plist"],
        "history": {f"f{i}.py": i % 2 == 0 for i in range(300)},
    }
    header = render_markdown(doc).split("## Пробы")[0]
    assert "f7.py" not in header and "a.plist" not in header
    assert "history: 300" in header and "plists: 2" in header
    assert "fleet: absent" in header and "sched_dir: /s" in header
