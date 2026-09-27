"""report.md rendering (terminal review of #403)."""

from __future__ import annotations

from selfcheck.report import render_markdown


def _finding(i: int, anchor: str = "") -> dict:
    return {
        "id": f"sc-{i:08x}",
        "rule": "ruff/X",
        "category": "quality",
        "confidence": "likely",
        "anchor": anchor or f"file:a{i}.py",
        "occurrences": 1,
    }


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
    text = render_markdown(_doc([_finding(i) for i in range(205)]))
    assert "показаны 200 из 205" in text


def test_surrogate_path_does_not_kill_the_report(tmp_path) -> None:
    """#409: a non-UTF-8 corpus path reaches findings as surrogates; the
    report shows it with U+FFFD instead of dying on UnicodeEncodeError."""
    import json

    from selfcheck.report import write_report

    doc = _doc([_finding(0, anchor="file:caf\udce9.py")])
    write_report(tmp_path, doc)
    written = json.loads((tmp_path / "report.json").read_text())
    assert written["findings"][0]["anchor"] == "file:caf�.py"
    assert "caf�.py" in (tmp_path / "report.md").read_text()
