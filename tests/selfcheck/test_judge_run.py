"""S5 — --judge wired into the run, the delta and the report (§11.6, §11.7)."""

from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

import pytest

from selfcheck import judge as judge_module
from selfcheck.run import main
from tests.selfcheck.helpers import make_repo, require_tool, workspace
from tests.selfcheck.test_run import args, reports

CLASSIFY = (
    "import json\nimport subprocess\n\n\ndef classify(items):\n    out = []\n"
    "    for item in items:\n"
    '        raw = subprocess.run(["claude", "-p", f"label {item}"],\n'
    "                             capture_output=True, text=True).stdout\n"
    '        out.append(json.loads(raw)["label"])\n    return out\n'
)
GOOD = {"rationale": "r", "verdict": "replace", "replacement": "rules"}


def fake_claude(tmp: Path, answer: object) -> Path:
    """A `claude` that logs argv/env/stdin and prints ``answer`` (a dict or raw)."""
    bin_dir = tmp / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp / "calls.jsonl"
    body = (
        json.dumps({"structured_output": answer})
        if isinstance(answer, dict)
        else answer
    )
    script = bin_dir / "claude"
    script.write_text(
        "#!/usr/bin/env python3\nimport json, os, sys\n"
        f"open({str(log)!r}, 'a').write(json.dumps({{'argv': sys.argv, "
        "'env': sorted(os.environ), 'stdin': sys.stdin.read()}) + '\\n')\n"
        f"print({body!r})\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def _calls(tmp: Path) -> list[dict]:
    log = tmp / "calls.jsonl"
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


def _use(monkeypatch: pytest.MonkeyPatch, bin_dir: Path) -> None:
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    assert shutil.which("claude") == str(bin_dir / "claude")  # never the real one


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    require_tool("uvx")
    _use(monkeypatch, fake_claude(tmp_path, GOOD))
    monkeypatch.setenv("GH_TOKEN", "must-not-leak")
    return workspace(tmp_path, {"c.py": CLASSIFY})


def _judge_row(doc: dict) -> dict:
    return next(p for p in doc["probes"] if p["probe"] == "judge")


def test_no_judge_flag_no_harness_call(ws: Path, tmp_path: Path) -> None:
    main([*args(ws), "--probe", "llm-sites"])
    assert _calls(tmp_path) == []
    assert all(p["probe"] != "judge" for p in reports(ws)[-1]["probes"])


def test_judge_runs_once_and_caches(ws: Path, tmp_path: Path) -> None:
    main([*args(ws), "--probe", "llm-sites", "--judge"])
    (call,) = _calls(tmp_path)
    assert "--restricted" in call["argv"] and "GH_TOKEN" not in call["env"]
    assert "def classify" in call["stdin"]
    doc = reports(ws)[-1]
    (f,) = [x for x in doc["findings"] if x["rule"] == "llm-sites/replaceable"]
    assert f["judge"]["verdict"] == "replace" and f["confidence"] == "likely"
    assert _judge_row(doc)["status"] == "ok"
    main([*args(ws), "--probe", "llm-sites", "--judge"])
    assert len(_calls(tmp_path)) == 1
    assert reports(ws)[-1]["judge"]["cached"] == 1


def test_judge_max_zero_lists_not_judged(ws: Path) -> None:
    main([*args(ws), "--probe", "llm-sites", "--judge", "--judge-max", "0"])
    doc = reports(ws)[-1]
    assert [x["rule"] for x in doc["judge"]["not_judged"]] == ["llm-sites/replaceable"]
    md = next(p for p in (ws / "out").iterdir() if p.is_dir()) / "report.md"
    assert "не судились 1 из 1" in md.read_text()


def test_judge_error_exit_2_then_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    require_tool("uvx")
    ws = workspace(tmp_path, {"c.py": CLASSIFY})
    _use(monkeypatch, fake_claude(tmp_path, "not json"))
    assert main([*args(ws), "--probe", "llm-sites", "--judge"]) == 2
    doc = reports(ws)[-1]
    assert _judge_row(doc)["status"] == "failed"
    assert any(f["anchor"] == "probe:devtools#judge" for f in doc["findings"])
    good = tmp_path / "good"
    good.mkdir()
    _use(monkeypatch, fake_claude(good, GOOD))
    assert main([*args(ws), "--probe", "llm-sites", "--judge"]) == 0
    gone = reports(ws)[-1]["delta"]["gone"]
    assert {"anchor": "probe:devtools#judge", "status": "resolved"}.items() <= next(
        g for g in gone if g["anchor"] == "probe:devtools#judge"
    ).items()


def test_judge_finding_resolves_outside_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§11.6: the judge is run-level — an ok --judge run resolves it even when
    devtools is not in scope (mutation guard for delta `run_level`)."""
    require_tool("uvx")
    ws = workspace(tmp_path, {"c.py": CLASSIFY})
    make_repo(tmp_path / "other", {"o.py": CLASSIFY})  # something to judge (M2)
    (ws / "m.toml").write_text(
        '[tools.devtools]\ngit_dir = "devtools"\n[tools.other]\ngit_dir = "other"\n'
    )
    _use(monkeypatch, fake_claude(tmp_path, "not json"))
    assert main([*args(ws), "--probe", "llm-sites", "--judge"]) == 2
    good = tmp_path / "good"
    good.mkdir()
    _use(monkeypatch, fake_claude(good, GOOD))
    main([*args(ws), "--repo", "other", "--probe", "llm-sites", "--judge"])
    gone = {g["anchor"]: g["status"] for g in reports(ws)[-1]["delta"]["gone"]}
    assert gone["probe:devtools#judge"] == "resolved"


def test_keep_is_shown_under_keep_not_in_its_category() -> None:
    from selfcheck.report import render_markdown

    keep = {
        "id": "sc-keep0001",
        "rule": "jscpd/clone",
        "category": "duplicate",
        "confidence": "likely",
        "anchor": "dup:text:keepme",
        "occurrences": 1,
        "owner_repo": "a",
        "judge": {"verdict": "keep", "replacement": "none", "rationale": "on purpose"},
    }
    doc = {
        "run": {
            "run_id": "r",
            "host": "h",
            "scope": ["a"],
            "surface": {},
            "env": {},
            "warnings": [],
            "manifest": {"entries_read": 1, "repos": ["a"], "missing": []},
        },
        "probes": [],
        "findings": [keep],
        "suppressed": [],
        "suppressed_no_env": {},
        "delta": {"statuses": {}, "gone": []},
        "inventory": {"llm": []},
        "judge": {
            "model": "m",
            "calls": 1,
            "cached": 0,
            "errors": 0,
            "candidates": 1,
            "not_judged": [],
        },
    }
    text = render_markdown(doc)
    assert "### Судья: оставить" in text and "on purpose" in text
    assert "## duplicate" not in text


def test_missing_claude_is_unavailable_exit_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    require_tool("uvx")
    ws = workspace(tmp_path, {"c.py": CLASSIFY})
    only = tmp_path / "only"
    only.mkdir()
    for tool in ("uv", "uvx", "git"):
        found = shutil.which(tool)
        assert found, tool
        (only / tool).symlink_to(found)
    monkeypatch.setenv("PATH", str(only))
    assert shutil.which("claude") is None  # the real claude is unreachable
    monkeypatch.setattr(judge_module.shutil, "which", lambda _: None)  # belt and braces
    code = main([*args(ws), "--probe", "llm-sites", "--judge"])
    assert _judge_row(reports(ws)[-1])["status"] == "unavailable"
    assert code == 3


def test_broken_cache_is_a_warning_not_exit_4(ws: Path) -> None:
    (ws / "out").mkdir(exist_ok=True)
    (ws / "out" / "judge-cache.json").write_text("{broken")
    assert main([*args(ws), "--probe", "llm-sites", "--judge"]) in (0, 2)
    assert any("judge-cache" in w for w in reports(ws)[-1]["run"]["warnings"])


def test_replace_and_unsure_are_shown_with_the_replacement() -> None:
    """Final review I1: a reader of report.md sees what the judge proposes."""
    from selfcheck.report import render_markdown

    def finding(i: int, verdict: str, replacement: str) -> dict:
        return {
            "id": f"sc-{i:08d}",
            "rule": "jscpd/clone",
            "category": "duplicate",
            "confidence": "likely",
            "anchor": f"dup:text:{i}",
            "occurrences": 1,
            "owner_repo": "a",
            "judge": {
                "verdict": verdict,
                "replacement": replacement,
                "rationale": f"why {i}",
            },
        }

    doc = {
        "run": {
            "run_id": "r",
            "host": "h",
            "scope": ["a"],
            "surface": {},
            "env": {},
            "warnings": [],
            "manifest": {"entries_read": 1, "repos": ["a"], "missing": []},
        },
        "probes": [],
        "findings": [finding(1, "replace", "merge"), finding(2, "unsure", "none")],
        "suppressed": [],
        "suppressed_no_env": {},
        "delta": {"statuses": {}, "gone": []},
        "inventory": {"llm": []},
        "judge": {
            "model": "m",
            "calls": 2,
            "cached": 0,
            "errors": 0,
            "candidates": 2,
            "not_judged": [],
        },
    }
    text = render_markdown(doc)
    assert "### Судья: заменить" in text and "| merge |" in text and "why 1" in text
    assert "### Судья: не уверен" in text and "why 2" in text


def test_judge_instrument_finding_obeys_the_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#442 review: `probe:devtools#judge` is suppressible like any probe row."""
    require_tool("uvx")
    ws = workspace(tmp_path, {"c.py": CLASSIFY})
    _use(monkeypatch, fake_claude(tmp_path, "not json"))
    cfg = ws / "allow.toml"
    cfg.write_text(
        '[[allow]]\nanchor = "probe:devtools#judge"\nrepo = "devtools"\n'
        'reason = "no claude on this runner"\nuntil = 2099-01-01\n'
    )
    main([*args(ws), "--config", str(cfg), "--probe", "llm-sites", "--judge"])
    doc = reports(ws)[-1]
    assert not any(f["anchor"] == "probe:devtools#judge" for f in doc["findings"])
    assert any(f["anchor"] == "probe:devtools#judge" for f in doc["suppressed"])


def test_ok_run_without_attempts_does_not_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """devtools#444 M2: `ok` with nothing judged proves nothing about a failure."""
    require_tool("uvx")
    ws = workspace(tmp_path, {"c.py": CLASSIFY})
    _use(monkeypatch, fake_claude(tmp_path, "not json"))
    assert main([*args(ws), "--probe", "llm-sites", "--judge"]) == 2
    good = tmp_path / "good"
    good.mkdir()
    _use(monkeypatch, fake_claude(good, GOOD))
    main([*args(ws), "--probe", "llm-sites", "--judge", "--judge-max", "0"])
    gone = {g["anchor"]: g["status"] for g in reports(ws)[-1]["delta"]["gone"]}
    assert gone["probe:devtools#judge"] == "not-rechecked"


def test_negative_judge_max_is_refused(ws: Path) -> None:
    with pytest.raises(SystemExit):
        main([*args(ws), "--judge", "--judge-max", "-1"])
