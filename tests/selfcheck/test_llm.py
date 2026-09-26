"""Task 12 — llm-sites: call sites, AST argv, mechanism D, heuristics (§3.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from selfcheck.corpus import list_corpus, materialize, release
from selfcheck.env import EnvInfo
from selfcheck.llm import LLM_SITES, python_features
from selfcheck.probes.base import (
    ProbeResult,
    ProbeStatus,
    RepoTarget,
    canary_files,
    run_probe,
)
from tests.selfcheck.helpers import NOW, make_repo, require_tool

CLASSIFY = """import json
import subprocess


def classify(items):
    out = []
    for item in items:
        raw = subprocess.run(["claude", "-p", f"label {item}"],
                             capture_output=True, text=True).stdout
        out.append(json.loads(raw)["label"])
    return out
"""
REVIEW = """import subprocess


def review(diff):
    prompt = f"Review this diff: {diff}"
    return subprocess.run(["codex", "exec", prompt], capture_output=True).stdout
"""
VARIABLE_ARGV = """import subprocess


def spawn(prompt):
    cmd = ["codex", "exec", "--json", prompt]
    return subprocess.run(cmd, capture_output=True, text=True)
"""
ENDPOINT_TEXT = """DOCS = []
for name in ("a", "b"):
    DOCS.append("see /v1/messages for " + name)
"""
HARNESS = '#!/bin/sh\nclaude -p "$1" --output-format json --json-schema s.json\n'
SDK_LOOP = """import json

import anthropic


def tag(items):
    client = anthropic.Anthropic()
    out = []
    for item in items:
        msg = client.messages.create(model="m", max_tokens=5,
                                     messages=[{"role": "user", "content": f"tag {item}"}])
        out.append(json.loads(msg.content[0].text)["tag"])
    return out
"""
HTTP_CALL = """import requests


def ask(text):
    return requests.post("https://api.anthropic.com/v1/messages", json={"q": text}).text
"""
CONFIG = '[agents.reviewer]\nbinary = "codex"\nmodel = "claude-opus-5-5"\n'


def test_features_and_exclusion() -> None:
    feats, excluded = python_features(CLASSIFY, 8)
    assert {"fixed-schema", "loop"} <= set(feats) and not excluded
    assert python_features(REVIEW, 6)[1] is True


@pytest.fixture
def result(tmp_path: Path) -> ProbeResult:
    require_tool("uvx")  # semgrep runs as `uvx semgrep@1.178.0` (ledger: Task 1 ruling)
    repo = make_repo(
        tmp_path / "repo",
        {
            "c.py": CLASSIFY,
            "r.py": REVIEW,
            "v.py": VARIABLE_ARGV,
            "e.py": ENDPOINT_TEXT,
            "harness": HARNESS,
            "agents.toml": CONFIG,
            "b.py": SDK_LOOP,
            "h.py": HTTP_CALL,
        },
    )
    corpus = tuple(list_corpus(repo))
    copy = tmp_path / "run" / "src" / "repo"
    materialize(repo, corpus, copy, canary_files([LLM_SITES]))
    target = RepoTarget(
        "repo", repo, copy, frozenset({"python"}), corpus, EnvInfo("no-env"), now=NOW
    )
    try:
        return run_probe(LLM_SITES, target, tmp_path / "run" / "work")
    finally:
        release(copy)


def test_candidates(result: ProbeResult) -> None:
    assert result.status is ProbeStatus.OK and result.canary == "hit"
    assert sorted(f.anchor for f in result.findings) == [
        "llm:b.py::tag",
        "llm:c.py::classify",
    ]
    assert all(f.text_key is None for f in result.findings)
    assert {f.confidence.value for f in result.findings} == {"candidate"}


def test_inventory_mechanisms(result: ProbeResult) -> None:
    rows = {
        (i["path"], i["mechanism"], i["candidate"]) for i in result.extra["inventory"]
    }
    assert {
        ("c.py", "A", True),
        ("r.py", "A", False),
        ("v.py", "A", False),
        ("harness", "A", False),
        ("b.py", "B", True),
        ("h.py", "C", False),
        ("agents.toml", "D", False),
    } <= rows
    assert not any(i["path"] == "e.py" for i in result.extra["inventory"])


def test_python_parse_error_is_partial(tmp_path: Path) -> None:
    require_tool("uvx")  # semgrep runs as `uvx semgrep@1.178.0` (ledger: Task 1 ruling)
    repo = make_repo(tmp_path / "repo", {"c.py": CLASSIFY, "bad.py": "def f(:\n"})
    corpus = tuple(list_corpus(repo))
    copy = tmp_path / "run" / "src" / "repo"
    materialize(repo, corpus, copy, canary_files([LLM_SITES]))
    target = RepoTarget(
        "repo", repo, copy, frozenset({"python"}), corpus, EnvInfo("no-env"), now=NOW
    )
    try:
        res = run_probe(LLM_SITES, target, tmp_path / "run" / "work")
    finally:
        release(copy)
    assert res.status is ProbeStatus.PARTIAL and "bad.py" in res.coverage["skipped"]
