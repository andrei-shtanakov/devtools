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
