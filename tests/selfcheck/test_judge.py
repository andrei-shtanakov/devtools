"""S5 — the judge (spec §5, §11)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from selfcheck.config import load_config
from selfcheck.corpus import list_corpus
from selfcheck.judge import (
    DEFAULT_MODEL,
    MAX_SLICE,
    SCHEMA,
    build_slice,
    call_judge,
    clone_lines,
    is_candidate,
    judge_argv,
    judge_env,
    order_key,
)
from selfcheck.model import Confidence, Finding, Location
from tests.selfcheck.helpers import make_repo


def _f(
    rule: str,
    conf: Confidence,
    repos: tuple[str, ...] = ("a",),
    path: str = "x.py",
    line: int = 1,
    lines: int | None = None,
) -> Finding:
    category = "llm-replaceable" if rule.startswith("llm") else "duplicate"
    members = repos * (2 if len(repos) == 1 else 1)
    related = [
        {"owner_repo": r, "path": path, "line": line + i, "member": str(i)}
        for i, r in enumerate(members)
    ]
    return Finding(
        rule=rule,
        category=category,
        severity="medium",
        confidence=conf,
        owner_repo=repos[0],
        anchor=f"{rule}:{path}:{line}:{lines}:{'-'.join(repos)}",
        locations=[Location(path, line)],
        related=related if category == "duplicate" else [],
        evidence=[{"kind": "lines", "detail": str(lines)}] if lines else [],
    )


def test_candidates_are_llm_and_dups_below_confirmed() -> None:
    assert is_candidate(_f("llm-sites/replaceable", Confidence.CANDIDATE))
    assert is_candidate(_f("jscpd/clone", Confidence.LIKELY, lines=9))
    assert not is_candidate(_f("ast-dup/exact", Confidence.CONFIRMED))
    other = _f("ruff/F401", Confidence.LIKELY)
    other.category = "quality"
    assert not is_candidate(other)


def test_order_is_normative() -> None:
    fs = [
        _f("jscpd/clone", Confidence.LIKELY, lines=10),
        _f("jscpd/clone", Confidence.LIKELY, lines=40),
        _f("cli-overlap/argparse", Confidence.CANDIDATE),
        _f("ast-dup/structural", Confidence.CANDIDATE),
        _f("llm-sites/replaceable", Confidence.CANDIDATE),
        _f("jscpd/clone", Confidence.LIKELY, repos=("a", "b"), lines=5),
        _f("ast-dup/structural", Confidence.CANDIDATE, repos=("a", "b")),
    ]
    got = [(f.rule, clone_lines(f)) for f in sorted(fs, key=order_key)]
    assert got == [
        ("ast-dup/structural", 0),  # cross-repo, kind before level
        ("jscpd/clone", 5),
        ("llm-sites/replaceable", 0),  # intra: llm → ast → cli → jscpd
        ("ast-dup/structural", 0),
        ("cli-overlap/argparse", 0),
        ("jscpd/clone", 40),  # longest clone first
        ("jscpd/clone", 10),
    ]


def test_obsidian_plugins_are_out_of_the_corpus(tmp_path: Path) -> None:
    config = load_config(Path(__file__).parents[2] / "selfcheck.toml")
    repo = make_repo(
        tmp_path / "vault",
        {
            ".obsidian/plugins/p/main.js": "x();\n",
            ".obsidian/app.json": "{}",
            "a.md": "#\n",
        },
    )
    corpus = list_corpus(repo, config.corpus_exclude)
    assert ".obsidian/plugins/p/main.js" not in corpus and "a.md" in corpus


FUNC = "".join(f"# pad {i}\n" for i in range(50)) + (
    "def f(x):\n"
    + "".join(f"    y{i} = x + {i}\n" for i in range(10))
    + "    return x\n"
)  # line k (1-based) of the pad block is "# pad {k-1}"; `def f` is line 51


def _src(tmp_path: Path, files: dict[str, str]) -> dict[str, Path]:
    root = tmp_path / "a"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return {"a": root}


def test_llm_slice_is_the_line_pm_40(tmp_path: Path) -> None:
    f = _f("llm-sites/replaceable", Confidence.CANDIDATE, line=55)
    s = build_slice(f, _src(tmp_path, {"x.py": FUNC}))
    assert s is not None and not s.truncated
    assert "# pad 14" in s.text and "# pad 13" not in s.text  # lines 15..95
    assert "a:x.py lines 15-" in s.text


def test_ast_dup_slice_takes_whole_functions(tmp_path: Path) -> None:
    f = _f("ast-dup/structural", Confidence.CANDIDATE, line=51)
    f.related = [{"owner_repo": "a", "path": "x.py", "line": 51, "member": "f"}]
    s = build_slice(f, _src(tmp_path, {"x.py": FUNC}))
    assert s is not None and "    return x" in s.text and "# pad 45" in s.text


def test_jscpd_slice_is_clone_pm_5(tmp_path: Path) -> None:
    f = _f("jscpd/clone", Confidence.LIKELY, line=20, lines=3)  # clone 20..22
    f.related = [{"owner_repo": "a", "path": "x.py", "line": 20, "member": "20"}]
    s = build_slice(f, _src(tmp_path, {"x.py": FUNC}))
    assert s is not None and "# pad 14" in s.text and "# pad 26" in s.text
    assert "# pad 13" not in s.text and "# pad 27" not in s.text


def test_form_feed_does_not_shift_lines(tmp_path: Path) -> None:
    text = "L1\x0c\n" + "".join(f"L{i}\n" for i in range(2, 21))  # \f: splitlines
    f = _f("jscpd/clone", Confidence.LIKELY, line=3, lines=1)  # window 1..8
    f.related = [{"owner_repo": "a", "path": "x.py", "line": 3, "member": "3"}]
    s = build_slice(f, _src(tmp_path, {"x.py": text}))
    assert s is not None and "L8" in s.text and "L9" not in s.text


def test_non_utf8_name_is_read_by_raw_path(tmp_path: Path, monkeypatch) -> None:
    """#420: the finding carries the shown name; the read goes by the raw one
    (APFS refuses non-UTF-8 names, so the mapping is patched as in test_corpus)."""
    from selfcheck import judge as judge_module

    src = _src(tmp_path, {"raw_name.py": "y = 2\n"})
    monkeypatch.setattr(
        judge_module,
        "raw_path",
        lambda root, rel: "raw_name.py" if rel == "n\ufffd.py" else rel,
    )
    f = _f("llm-sites/replaceable", Confidence.CANDIDATE, path="n\ufffd.py")
    s = build_slice(f, src)
    assert s is not None and "y = 2" in s.text


def test_symlink_is_not_read(tmp_path: Path) -> None:
    src = _src(tmp_path, {"real.py": FUNC})
    (src["a"] / "link.py").symlink_to(src["a"] / "real.py")
    f = _f("llm-sites/replaceable", Confidence.CANDIDATE, path="link.py")
    assert build_slice(f, src) is None


def test_missing_file_gives_none(tmp_path: Path) -> None:
    f = _f("llm-sites/replaceable", Confidence.CANDIDATE, path="gone.py")
    assert build_slice(f, {"a": tmp_path}) is None


def test_huge_slice_is_truncated(tmp_path: Path) -> None:
    f = _f("jscpd/clone", Confidence.LIKELY, line=1, lines=30000)
    f.related = [{"owner_repo": "a", "path": "x.py", "line": 1, "member": "1"}]
    s = build_slice(f, _src(tmp_path, {"x.py": "x = 1\n" * 40000}))
    assert s is not None and s.truncated
    assert len(s.text.encode()) <= MAX_SLICE + 32 and s.text.endswith("[truncated]")


ENV = {
    "PATH": "/bin",
    "HOME": "/h",
    "USER": "u",
    "LOGNAME": "u",
    "GH_TOKEN": "secret",
    "REVIEW_MODEL": "x",
    "CLAUDE_CODE_OAUTH_TOKEN": "t",
    "ANTHROPIC_API_KEY": "k",
}
GOOD = {"rationale": "r", "verdict": "replace", "replacement": "merge"}


@pytest.mark.parametrize(
    ("platform", "keys"),
    [
        ("darwin", {"PATH", "HOME", "USER"}),
        ("linux", {"PATH", "HOME", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"}),
    ],
)
def test_env_allowlist_per_platform(platform: str, keys: set[str]) -> None:
    assert set(judge_env(platform, ENV)) == keys


def test_argv_is_tool_less_and_pinned() -> None:
    argv = judge_argv("claude", DEFAULT_MODEL)
    for flag in ("--restricted", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--model") + 1] == DEFAULT_MODEL
    assert json.loads(argv[argv.index("--json-schema") + 1]) == SCHEMA
    assert next(iter(SCHEMA["properties"])) == "rationale"


def _runner(stdout: str = "", code: int = 0, exc: BaseException | None = None):
    def run(argv, **kw):
        assert kw["stdin"].read() == "slice"  # the slice comes on stdin, as a file
        if exc is not None:
            raise exc
        return subprocess.CompletedProcess(argv, code, stdout, "")

    return run


@pytest.mark.parametrize(
    ("stdout", "code", "exc", "expected"),
    [
        (
            json.dumps({"is_error": False, "structured_output": GOOD}),
            0,
            None,
            "replace",
        ),
        (json.dumps({"is_error": True, "result": "Not logged in"}), 1, None, "error"),
        (
            json.dumps({"structured_output": {**GOOD, "verdict": "maybe"}}),
            0,
            None,
            "error",
        ),
        (json.dumps({"structured_output": {**GOOD, "id": "sc-x"}}), 0, None, "error"),
        ("not json", 0, None, "error"),
        ("x" * (64 * 1024 + 1), 0, None, "error"),
        ("", 0, subprocess.TimeoutExpired(["claude"], 120), "error"),
        ("", 0, ValueError("boom"), "error"),
    ],
)
def test_call_judge_outcomes(
    stdout: str, code: int, exc: BaseException | None, expected: str
) -> None:
    got = call_judge(
        "claude",
        "slice",
        DEFAULT_MODEL,
        runner=_runner(stdout, code, exc),
        platform="darwin",
        environ=ENV,
    )
    assert got["verdict"] == expected
    if expected == "error":
        assert got["detail"]
