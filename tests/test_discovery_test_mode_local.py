"""Local parity: devtools and discovery agree on `mode = "test"` (plan 3a, Task 8).

Runs only next to a sibling discovery checkout that carries the vendored loader
(`src/discovery/darkfactory/df_config.py`); CI without siblings skips. discovery
runs in its own venv (`uv run --frozen --project`), with only the forge
composition point `cli.build_forge` replaced — as in
`tools/gen_discovery_approval_fixture.py`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from governance import df_config, policy_rule
from tests.df_config_fixture import use_test_real_home, write_config

ROOT = Path(__file__).resolve().parent.parent
VENDORED = Path("src") / "discovery" / "darkfactory" / "df_config.py"
DRAFT = ROOT / "tests" / "fixtures" / "discovery_approval" / "draft-brief.md"
BRIEF_PATH = "workstreams/WS-1/spec/00-discovery/brief.md"

#: Runs in discovery's venv: argv = [mode, brief, merger]; prints one JSON line.
#: TEST-ONLY: `_real_home` → `$DF_TEST_REAL_HOME` (the test-mode anchor).
DISCOVERY_SCRIPT = """
import contextlib, io, json, os, sys
from pathlib import Path
from discovery import cli, policy
from discovery.darkfactory import df_config
from discovery.forge import PullRequest

df_config._real_home = lambda: Path(os.environ["DF_TEST_REAL_HOME"])
mode, brief, merger = sys.argv[1], Path(sys.argv[2]), sys.argv[3]

class Forge:
    def pull_request(self, repo, number):
        return PullRequest("MERGED", merger, "2026-10-08T10:00:00Z", "d1" * 20)
    def pull_request_files(self, repo, number):
        return [sys.argv[4]]
    def file_at(self, repo, commit, path):
        return brief.read_text(encoding="utf-8")
    def latest_commit_touching(self, repo, ref, path):
        return "a1" * 20

if mode == "allowlist":
    try:
        print(json.dumps({"allowlist": sorted(policy.allowlist(Forge()))}))
    except policy.PolicyRefused as exc:
        print(json.dumps({"refused": str(exc)}))
    sys.exit(0)
cli.build_forge = Forge
out = io.StringIO()
with contextlib.redirect_stdout(out):
    code = cli.main(["approve", str(brief), "--repo", "owner/alpha", "--pr", "7",
                     "--path", sys.argv[4]])
print(json.dumps({"code": code, "envelope": json.loads(out.getvalue()),
                  "brief": brief.read_text(encoding="utf-8")}))
"""


def _discovery() -> Path:
    try:
        root = df_config.workspace_dir("discovery")
    except (df_config.ConfigError, OSError) as exc:  # no sibling to compare with
        pytest.skip(f"discovery checkout unavailable: {exc}")
    if not (root / VENDORED).is_file():
        pytest.skip(f"no vendored loader in {root}")
    if shutil.which("uv") is None:
        pytest.skip("uv not installed")
    return root


@pytest.fixture
def discovery_root() -> Path:
    """The sibling discovery checkout, resolved before any test config."""
    return _discovery()


@pytest.fixture
def test_config(monkeypatch, tmp_path):
    """Return a writer of an anchored test-mode config with `human`."""
    home = tmp_path / "home"
    home.mkdir()
    use_test_real_home(monkeypatch, home)
    monkeypatch.delenv("AUTHORIZED_APPROVER_ACCOUNTS", raising=False)
    xdg = tmp_path / "xdg"

    def write(human: str) -> None:
        env = write_config(xdg, human=human, agent=human, mode="test")
        for key, value in env.items():
            monkeypatch.setenv(key, value)

    return write


def _run(root: Path, *argv: str) -> dict:
    proc = subprocess.run(
        ["uv", "run", "--frozen", "--project", str(root), "python", "-I", "-c"]
        + [DISCOVERY_SCRIPT, *argv],
        capture_output=True,
        text=True,
        check=False,
        cwd=root,
        env={k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"},
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _approve(root: Path, tmp_path: Path, merger: str) -> dict:
    brief = tmp_path / merger / BRIEF_PATH
    brief.parent.mkdir(parents=True)
    brief.write_text(DRAFT.read_text(encoding="utf-8"), encoding="utf-8")
    return _run(root, "approve", str(brief), merger, BRIEF_PATH)


def test_vendored_loader_is_byte_identical(discovery_root):
    vendored = (discovery_root / VENDORED).read_bytes()
    assert vendored == (ROOT / "governance" / "df_config.py").read_bytes()


def test_test_mode_approver_parity(discovery_root, test_config, tmp_path):
    test_config("alice")

    synthetic = policy_rule.test_policy()
    assert synthetic is not None
    assert synthetic.content == "AUTHORIZED_APPROVER_ACCOUNTS=alice\n"

    signed = _approve(discovery_root, tmp_path, "alice")
    assert signed["code"] == 0, signed
    assert "approver: alice" in signed["brief"]

    denied = _approve(discovery_root, tmp_path, "bob")
    assert denied["code"] == 2, denied
    assert denied["envelope"]["operation"]["reason"] == "approver_not_authorized"
    assert "status: approved" not in denied["brief"]


def test_two_logins_refused_in_both(discovery_root, test_config):
    test_config("alice,bob")

    with pytest.raises(df_config.ConfigError, match="accounts.human"):
        df_config.load()
    refused = _run(discovery_root, "allowlist", "-", "-", BRIEF_PATH)
    assert refused.get("refused", "").startswith("конфиг DarkFactory: "), refused
    assert "accounts.human" in refused["refused"]
