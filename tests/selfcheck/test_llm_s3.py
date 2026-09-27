"""S3 Task 5 — llm-sites: launch construction, harness resolve, TS (§10.7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from selfcheck.corpus import list_corpus, materialize, release
from selfcheck.env import EnvInfo
from selfcheck.llm import LLM_SITES, MAX_TARGET_BYTES, code_endpoint
from selfcheck.probes.base import (
    ProbeResult,
    ProbeStatus,
    RepoTarget,
    canary_files,
    run_probe,
)
from tests.selfcheck.helpers import NOW, make_repo, require_tool

SPAWNER = (
    'def argv(model, prompt):\n    return ["claude", "--model", model, "-p", prompt]\n'
)
TUPLE = 'def argv(prompt):\n    return ("codex", "exec", prompt)\n'
CONFIGURED = (
    "def run(config, prompt, runner):\n"
    '    return runner([config.claude_command, "-p", prompt])\n'
)
PRIVATE_CMD = (
    "class D:\n    def cmd(self, prompt):\n"
    '        return [self._claude_command, "--print", "-p", prompt]\n'
)
RESOLVE = (
    "import os\nimport shutil\n\n"
    'BIN = os.environ.get("CLAUDE_BIN", "claude")\n'
    'ALT = os.getenv("OPENCODE_BIN", "opencode")\n\n\n'
    'def find():\n    return shutil.which("codex")\n'
)
URL_F = "def url(host):\n    return f\"{host.rstrip('/')}/v1/chat/completions\"\n"
URL_OLLAMA = 'def url(host):\n    return host + "/api/chat"\n'
DOC_URL = (
    '"""Calls the HTTP `/api/chat` endpoint of ollama."""\n\n\n'
    "def f(base):\n"
    "    # POST http://localhost:11434/api/chat\n"
    '    note = "see /v1/messages for details"\n'
    '    return note + f"see the docs at {base}/v1/messages for details"\n'
)
NOT_ARGV = (
    'NAMES = {"codex_cli": "codex", "claude_code": "claude"}\n'
    'BACKENDS = ("codex", "disp")\n\n\n'
    "def add(parser):\n"
    '    parser.add_argument("--author", choices=["codex", "disp"], default="codex")\n'
)
SHELL_URL = (
    "#!/bin/sh\n"
    "curl -s http://localhost:11434/api/generate -d '{}'\n"
    "# curl http://h/api/chat\n"
)
TS_SPAWN = (
    'import { spawn } from "node:child_process";\n'
    'export const go = (p: string) => spawn("claude", ["-p", p]);\n'
)
TS_ARGV = 'export const argv = (p: string) => ["codex", "exec", p];\n'
TS_SDK_OPENAI = 'import OpenAI from "openai";\nexport const c = new OpenAI();\n'
TS_SDK_ANTHROPIC = (
    'import Anthropic from "@anthropic-ai/sdk";\n'
    "export const a = new Anthropic();\n"
    'export const r = (x: any) => x.messages.create({ model: "m" });\n'
)
TS_AGENTS = 'import { Agent, run } from "@openai/agents";\nexport { Agent, run };\n'
TS_URL = "export const u = (h: string) => `${h}/v1/chat/completions`;\n"
TS_COMMENT = "// fetch(`${h}/v1/chat/completions`)\nexport const x = 1;\n"
SPLIT = (
    "import json\nimport subprocess\n\n\n"
    "def go(items, prompt):\n"
    "    out = []\n"
    "    for item in items:\n"
    '        cmd = ["claude", "-p", prompt]\n'
    "        raw = subprocess.run(cmd, capture_output=True, text=True).stdout\n"
    '        out.append(json.loads(raw)["label"])\n'
    "    return out\n"
)
TS_STAR = 'import * as sdk from "@anthropic-ai/sdk";\nexport default sdk;\n'
BIG_JS = "// bundle\n" + "x" * MAX_TARGET_BYTES + '\nspawn("claude", ["-p", q]);\n'

PROSE_TS = (
    'export const m = "see /v1/messages for x";\n'
    "export const n = 1; /* fetch(`${h}/v1/chat/completions`) */\n"
)
PROSE_SH = (
    "#!/bin/sh\n"
    'echo "see /v1/messages for x"\n'
    "echo hi # curl http://h/api/chat\n"
    'curl "$HOST/v1/chat/completions"\n'
)
LOOP_OUTSIDE = (
    "import subprocess\n\n\n"
    "def label(items):\n"
    '    cmd = ["claude", "-p", "label the next item"]\n'
    "    out = []\n"
    "    for item in items:\n"
    "        raw = subprocess.run(cmd, input=item, capture_output=True).stdout\n"
    "        out.append(raw)\n"
    "    return out\n"
)

FILES = {
    "prose.ts": PROSE_TS,
    "prose.sh": PROSE_SH,
    "loop_outside.py": LOOP_OUTSIDE,
    "spawner.py": SPAWNER,
    "tuple.py": TUPLE,
    "configured.py": CONFIGURED,
    "private.py": PRIVATE_CMD,
    "resolve.py": RESOLVE,
    "url_f.py": URL_F,
    "url_ollama.py": URL_OLLAMA,
    "doc_url.py": DOC_URL,
    "not_argv.py": NOT_ARGV,
    "fetch.sh": SHELL_URL,
    "spawn.ts": TS_SPAWN,
    "argv.ts": TS_ARGV,
    "sdk_openai.ts": TS_SDK_OPENAI,
    "sdk_anthropic.ts": TS_SDK_ANTHROPIC,
    "agents.ts": TS_AGENTS,
    "url.ts": TS_URL,
    "comment.ts": TS_COMMENT,
    "big.js": BIG_JS,
    "split.py": SPLIT,
    "star.ts": TS_STAR,
}


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> ProbeResult:
    require_tool("uvx")
    tmp = tmp_path_factory.mktemp("s3llm")
    repo = make_repo(tmp / "repo", FILES)
    corpus = tuple(list_corpus(repo))
    copy = tmp / "run" / "src" / "repo"
    materialize(repo, corpus, copy, canary_files([LLM_SITES]))
    target = RepoTarget(
        "repo", repo, copy, frozenset({"python"}), corpus, EnvInfo("no-env"), now=NOW
    )
    try:
        return run_probe(LLM_SITES, target, tmp / "run" / "work")
    finally:
        release(copy)


def _rows(result: ProbeResult) -> set[tuple[str, str, str]]:
    return {(i["path"], i["mechanism"], i["rule"]) for i in result.extra["inventory"]}


@pytest.mark.parametrize(
    ("path", "mechanism", "rule"),
    [
        ("spawner.py", "A", "argv-literal"),
        ("tuple.py", "A", "argv-literal"),
        ("configured.py", "A", "argv-literal"),
        ("private.py", "A", "argv-literal"),
        ("resolve.py", "A", "harness-resolve"),
        ("url_f.py", "C", "endpoint"),
        ("url_ollama.py", "C", "endpoint"),
        ("fetch.sh", "C", "endpoint"),
        ("spawn.ts", "A", "spawn-ts"),
        ("argv.ts", "A", "argv-literal-ts"),
        ("sdk_openai.ts", "B", "sdk-ts"),
        ("sdk_anthropic.ts", "B", "sdk-ts"),
        ("agents.ts", "B", "sdk-ts"),
        ("star.ts", "B", "sdk-ts"),
        ("url.ts", "C", "endpoint"),
        ("split.py", "A", "argv-literal"),
    ],
)
def test_each_rule_fires(
    result: ProbeResult, path: str, mechanism: str, rule: str
) -> None:
    assert result.status is ProbeStatus.OK and result.canary == "hit"
    assert (path, mechanism, rule) in _rows(result)


def test_resolve_counts_getenv_and_which(result: ProbeResult) -> None:
    lines = {i["line"] for i in result.extra["inventory"] if i["path"] == "resolve.py"}
    assert lines == {4, 5, 9}


@pytest.mark.parametrize("path", ["doc_url.py", "not_argv.py", "comment.ts", "big.js"])
def test_negative_twins(result: ProbeResult, path: str) -> None:
    assert not any(r[0] == path for r in _rows(result))


def test_shell_comment_url_is_not_a_point(result: ProbeResult) -> None:
    lines = {i["line"] for i in result.extra["inventory"] if i["path"] == "fetch.sh"}
    assert lines == {2}


def test_big_file_named_in_notes(result: ProbeResult) -> None:
    assert any("big.js" in n for n in result.coverage.get("notes", []))


def test_ts_is_inventory_only(result: ProbeResult) -> None:
    ts = [i for i in result.extra["inventory"] if i["path"].endswith(".ts")]
    assert ts and not any(i["candidate"] or i["features"] for i in ts)
    assert not any(f.anchor.endswith(".ts") for f in result.findings)


def test_point_is_candidate_only_if_every_row_is(result: ProbeResult) -> None:
    # the prompt comes from a parameter: invisible on both the list and the launch
    assert not any(f.anchor == "llm:split.py::go" for f in result.findings)
    rows = [i for i in result.extra["inventory"] if i["path"] == "split.py"]
    assert rows and not any(i["candidate"] for i in rows)


def test_rows_deduplicated(result: ProbeResult) -> None:
    keys = [(i["path"], i["line"]) for i in result.extra["inventory"]]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize(
    ("text", "line", "hit"),
    [
        (URL_F, 2, True),
        (URL_OLLAMA, 2, True),
        (DOC_URL, 1, False),  # docstring
        (DOC_URL, 5, False),  # comment
        (DOC_URL, 6, False),  # prose with whitespace before the path
        (DOC_URL, 7, False),  # constant piece of an f-string
    ],
)
def test_code_endpoint(text: str, line: int, hit: bool) -> None:
    assert code_endpoint(text, line) is hit


def test_logic_version_bumped() -> None:
    assert LLM_SITES.logic_version == 3  # + shell default rule (acceptance)


def test_prose_and_trailing_comments_are_not_points(result: ProbeResult) -> None:
    """Final review I1: TS/shell prose and trailing comments (§10.7)."""
    assert not any(r[0] == "prose.ts" for r in _rows(result))
    lines = {i["line"] for i in result.extra["inventory"] if i["path"] == "prose.sh"}
    assert lines == {4}  # only the quoted URL built from $HOST


def test_candidate_heuristics_span_the_function(result: ProbeResult) -> None:
    """Final review I2: argv built before the loop, launched inside it (§3.4)."""
    assert any(f.anchor == "llm:loop_outside.py::label" for f in result.findings)


def test_no_syntax_warning_from_fleet_code() -> None:
    """Final smoke: someone else's escape sequences are not our output."""
    import warnings

    from selfcheck.llm import python_features

    text = 'def f(host):\n    p = "\\`x"\n    return f"{host}/v1/messages"\n'
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        code_endpoint(text, 3)
        python_features(text, 3)
    assert not [w for w in caught if issubclass(w.category, SyntaxWarning)]


SH_DEFAULT = (
    "#!/bin/sh\n"
    'review_cmd="${REVIEW_CMD:-codex exec}"\n'
    '$review_cmd --sandbox read-only - < "$work/prompt.txt"\n'
)
PY_PROSE = 'DOC = """\n    codex exec --help lists the flags\n"""\n'


def _probe(
    tmp_path_factory: pytest.TempPathFactory, files: dict[str, str]
) -> ProbeResult:
    require_tool("uvx")
    tmp = tmp_path_factory.mktemp("s3acc")
    repo = make_repo(tmp / "repo", files)
    corpus = tuple(list_corpus(repo))
    copy = tmp / "run" / "src" / "repo"
    materialize(repo, corpus, copy, canary_files([LLM_SITES]))
    target = RepoTarget(
        "repo", repo, copy, frozenset({"python"}), corpus, EnvInfo("no-env"), now=NOW
    )
    try:
        return run_probe(LLM_SITES, target, tmp / "run" / "work")
    finally:
        release(copy)


def test_shell_harness_default_expansion_is_a_point(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Acceptance 2026-09-27: review-kit local.sh — `${REVIEW_CMD:-codex exec}`."""
    res = _probe(tmp_path_factory, {"local.sh": SH_DEFAULT, "doc.py": PY_PROSE})
    rows = {(i["path"], i["line"], i["rule"]) for i in res.extra["inventory"]}
    assert ("local.sh", 2, "harness-resolve-sh") in rows
    assert not any(p == "doc.py" for p, _, _ in rows)  # cli-shell: shell files only


def test_unparseable_bash_is_not_partial(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Acceptance 2026-09-27: regex rules must not make semgrep parse bash —
    review-pr.sh is PartialParsing for semgrep's bash parser (partial since #424)."""
    text = (Path(__file__).parents[2] / "review-pr.sh").read_text()
    res = _probe(tmp_path_factory, {"review-pr.sh": text})
    assert res.status is ProbeStatus.OK, res.diagnostics


def test_pure_regex_rules_do_not_parse() -> None:
    import yaml

    from selfcheck.llm import RULES_PATH

    rules = yaml.safe_load(RULES_PATH.read_text())["rules"]
    regex_only = [r for r in rules if "pattern-regex" in r]
    assert regex_only and all(r["languages"] == ["regex"] for r in regex_only)
