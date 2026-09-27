"""report.md rendering (terminal review of #403)."""

from __future__ import annotations

from selfcheck.report import render_markdown


def _doc(findings: list[dict]) -> dict:
    return {
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
    text = render_markdown(_doc(findings))
    assert "показаны 200 из 205" in text


def test_header_carries_surface_counts_not_maps() -> None:
    """#410: the per-file ``history`` map (hundreds of entries) and the plist
    list belong to report.json; the header shows counts."""
    doc = _doc([])
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
