"""Task 11-B, Step 3: isolation of the probe on the REAL harness binaries.

Every run goes through `harness_world.run_harness`: the current user only,
`sandbox-exec` (network to localhost only, keychain services denied, the
owner's harness homes unreadable), an explicit environment, a fresh HOME,
the fake key and the localhost stub. No paid call can be made.

The argv and the base environment come from `probe._form` — the production
formation from the pinned profile. Test-only additions are named where they
are made: the stub's address and the fake key (claude: env; codex: one
`-c openai_base_url=…` before the prompt, since the pinned argv ignores
`OPENAI_BASE_URL`), and `--debug-file` where a debug log is the evidence.

Each scenario has a twin without the load-bearing flag, so a sentinel that
does not fire is shown to be live. Findings that keep a profile unverified
are pinned as they are (a fix upstream must turn such a test red and send
the version back to Step 3). Evidence: docs/evidence/2026-10-24-df-probe-
isolation.md.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from df import platform as plat
from df import probe
from df.models import ModelChoice
from df.paths import Layout
from tests.df import harness_world as hw
from tests.df.harness_stub import FAKE_KEY, Stub, running_stub

pytestmark = [
    pytest.mark.harness,
    pytest.mark.skipif(sys.platform != "darwin", reason="macOS binaries (R66)"),
]

CLAUDE_CHOICE = ModelChoice("claude", "claude-opus-5-5", "high", {})
CODEX_CHOICE = ModelChoice("codex", "gpt-5.5", "high", {})
PLATFORM = "macos-arm64"


def _binary(spec: tuple[Path, str, str]) -> Path:
    if not hw.SANDBOX_EXEC.exists():
        pytest.skip("no /usr/bin/sandbox-exec")
    path = hw.pinned(spec)
    if path is None:
        pytest.skip(f"{spec[0]} with SHA-256 {spec[1]} is absent")
    return path


@pytest.fixture()
def claude_bin() -> Path:
    return _binary(hw.CLAUDE)


@pytest.fixture()
def codex_bin() -> Path:
    return _binary(hw.CODEX)


@pytest.fixture()
def stub() -> Iterator[Stub]:
    with running_stub() as running:
        running.markers = hw.MARKS
        yield running


@pytest.fixture()
def tree(tmp_path: Path) -> hw.Tree:
    return hw.make_tree(Path(os.path.realpath(tmp_path)))


def formed(
    choice: ModelChoice, path: Path, home: Path
) -> tuple[list[str], dict[str, str]]:
    """The production argv and env of the probe (from the pinned profile)."""
    got = probe._form(choice, path, home)
    assert not isinstance(got, str), got
    argv, env, sha = got
    assert probe.executable_target(path) == (path, sha)
    assert argv[0] == str(path)
    return argv, env


def _settle() -> None:
    """Let a harness's detached children (MCP, hooks) touch their files."""
    time.sleep(1.5)


# ---------- the pinned profiles: admission without any launch ----------


@pytest.mark.parametrize("spec", [hw.CLAUDE, hw.CODEX], ids=["claude", "codex"])
def test_real_binary_admission_refuses(spec: tuple[Path, str, str]) -> None:
    path = _binary(spec)
    name = "claude" if spec is hw.CLAUDE else "codex"
    choice = CLAUDE_CHOICE if spec is hw.CLAUDE else CODEX_CHOICE
    argv, env = formed(choice, path, Path("/nonexistent-home"))
    prof = probe.HARNESS_PROFILES[(name, PLATFORM, spec[1])]
    assert prof.version == spec[2]
    got = probe.admit(name, path, PLATFORM, argv=argv, env=env)
    assert got == "не поддерживается: изоляция не подтверждена"


@pytest.mark.parametrize("spec", [hw.CLAUDE, hw.CODEX], ids=["claude", "codex"])
def test_verified_flag_alone_would_not_admit(
    spec: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Even with isolation_verified forced, the unexcluded server source
    # refuses: no Exclusion exists for it under every auth method.
    path = _binary(spec)
    name = "claude" if spec is hw.CLAUDE else "codex"
    choice = CLAUDE_CHOICE if spec is hw.CLAUDE else CODEX_CHOICE
    key = (name, PLATFORM, spec[1])
    forced = dataclasses.replace(probe.HARNESS_PROFILES[key], isolation_verified=True)
    monkeypatch.setattr(probe, "HARNESS_PROFILES", {key: forced})
    argv, env = formed(choice, path, Path("/nonexistent-home"))
    got = probe.admit(name, path, PLATFORM, argv=argv, env=env)
    assert isinstance(got, str)
    assert "server" in got and "не наблюдаем и не исключён вызовом" in got


def test_neutral_probe_cwd_has_no_problems(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plat, "ROOT_UID", os.getuid())
    lay = Layout(Path(os.path.realpath(tmp_path)) / "root")
    lay.probe_cwd.mkdir(parents=True, mode=0o755)
    assert probe.probe_cwd_problems(lay) == []


def test_sandbox_denies_owner_credential_places(tmp_path: Path) -> None:
    # The profile itself: every existing denied place of the owner is
    # unreadable in the sandbox (listing only; output dropped), and the
    # login keychain cannot be queried.
    if not hw.SANDBOX_EXEC.exists():
        pytest.skip("no /usr/bin/sandbox-exec")
    profile = tmp_path / "p.sb"
    profile.write_text(hw.sandbox_profile())

    def sandboxed(*argv: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [str(hw.SANDBOX_EXEC), "-f", str(profile), *argv],
            env={"PATH": "/usr/bin:/bin"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )

    places = [hw.OWNER_HOME / d for d in hw.DENIED_DIRS] + [
        hw.OWNER_HOME / f for f in hw.DENIED_FILES
    ]
    present = [p for p in places if p.exists()]
    assert present  # at least the keychains directory
    for place in present:
        if place.is_file():
            got = sandboxed("/usr/bin/head", "-c", "1", str(place))
        else:
            got = sandboxed("/bin/ls", "-1", str(place))
        assert got.returncode != 0, place
        assert b"Operation not permitted" in got.stderr, place
    login = hw.OWNER_HOME / "Library/Keychains/login.keychain-db"
    if login.exists():
        got = sandboxed("/usr/bin/security", "show-keychain-info", str(login))
        assert got.returncode != 0


def test_stub_judges_secret_headers_by_name() -> None:
    from tests.df.harness_stub import _credentials, is_secret_header

    for name in ("x-api-key", "Authorization", "Cookie", "X-Auth-Token", "Secret"):
        assert is_secret_header(name)
    assert not is_secret_header("Content-Type")
    assert _credentials([("Cookie", "session=1")]) == (False, True)
    assert _credentials([("Authorization", f"Bearer {FAKE_KEY}")]) == (True, False)
    assert _credentials([("Sec-WebSocket-Key", "dGhlIG5vbmNl")]) == (False, False)
    # only the exact handshake nonce is exempt, never a look-alike
    assert _credentials([("Sec-WebSocket-Auth-Token", "t")]) == (False, True)


# ---------- claude 2.1.309 ----------


def _claude_env(env: dict[str, str], stub: Stub) -> dict[str, str]:
    return {**env, "ANTHROPIC_API_KEY": FAKE_KEY, "ANTHROPIC_BASE_URL": stub.url}


def test_claude_pinned_invocation_is_isolated(
    claude_bin: Path, stub: Stub, tree: hw.Tree
) -> None:
    hw.plant_claude(tree, stub.url)
    argv, env = formed(CLAUDE_CHOICE, claude_bin, tree.home)
    ran = hw.run_harness(argv, _claude_env(env, stub), tree)
    _settle()
    assert ran.returncode == 0, ran.stderr[-500:]
    assert tree.fired() == []
    [req] = stub.requests()
    assert (req.path, req.model, req.tools, req.markers) == (
        "/v1/messages",
        "claude-opus-5-5",
        0,
        frozenset(),
    )
    assert req.effort == '{"output_config": {"effort": "high"}}'
    assert req.fake_key and not stub.leaked()
    parsed = probe.parse_output("claude", ran.stdout)
    assert parsed == probe.ProbeParse(True, "claude-opus-5-5", None)


def test_claude_setting_sources_is_load_bearing(
    claude_bin: Path, stub: Stub, tree: hw.Tree
) -> None:
    # Twin: the same tree without `--setting-sources ""` — the sentinels
    # are live (hooks fire, the env override and memory reach the request).
    hw.plant_claude(tree, stub.url)
    argv, env = formed(CLAUDE_CHOICE, claude_bin, tree.home)
    i = argv.index("--setting-sources")
    assert argv[i + 1] == probe.CLAUDE_SETTING_SOURCES == ""
    ran = hw.run_harness(argv[:i] + argv[i + 2 :], _claude_env(env, stub), tree)
    _settle()
    assert ran.returncode == 0
    assert {"user-hook-SessionStart", "local-hook-SessionStart"} <= set(tree.fired())
    [req] = stub.requests()
    assert req.path.endswith("-settings-env/v1/messages")
    assert req.markers == frozenset(hw.MARKS)


def test_claude_mcp_sentinels_are_live_without_isolation(
    claude_bin: Path, stub: Stub, tree: hw.Tree
) -> None:
    # Twin for MCP: without `--setting-sources ""` AND the strict empty MCP
    # config, the user (~/.claude.json) and project (.mcp.json) MCP servers
    # start; with either flag present they do not (pinned run, previous twin).
    hw.plant_claude(tree, stub.url)
    argv, env = formed(CLAUDE_CHOICE, claude_bin, tree.home)
    i = argv.index("--setting-sources")
    argv = argv[:i] + argv[i + 2 :]
    j = argv.index("--strict-mcp-config")
    assert argv[j : j + 3] == [
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
    ]
    ran = hw.run_harness(argv[:j] + argv[j + 3 :], _claude_env(env, stub), tree)
    _settle()
    assert ran.returncode == 0
    assert {"user-mcp", "project-mcp"} <= set(tree.fired())


def test_claude_reports_a_rewritten_model(
    claude_bin: Path, stub: Stub, tree: hw.Tree
) -> None:
    # claude rewrites some model IDs; the request and `modelUsage` carry the
    # model actually sent, which is what the record's `actual_model` keeps.
    choice = dataclasses.replace(CLAUDE_CHOICE, model="claude-opus-4-1")
    argv, env = formed(choice, claude_bin, tree.home)
    ran = hw.run_harness(argv, _claude_env(env, stub), tree)
    [req] = stub.requests()
    assert req.model == "claude-opus-5-5"
    parsed = probe.parse_output("claude", ran.stdout)
    assert parsed == probe.ProbeParse(True, "claude-opus-5-5", None)


def test_claude_prompt_after_empty_tools_needs_separator(
    claude_bin: Path, stub: Stub, tree: hw.Tree
) -> None:
    # `--tools <tools...>` is variadic: without `--` it swallows the prompt.
    argv, env = formed(
        dataclasses.replace(CLAUDE_CHOICE, effort=None), claude_bin, tree.home
    )
    assert argv[-4:] == ["--tools", "", "--", probe.PROBE_PROMPT]
    ran = hw.run_harness(argv[:-2] + argv[-1:], _claude_env(env, stub), tree)
    assert ran.returncode != 0 and stub.requests() == []
    assert b"Input must be provided" in ran.stderr


@pytest.fixture(params=[(), ("--bare",)], ids=["pinned", "pinned+bare"])
def claude_debug_log(
    request: pytest.FixtureRequest, claude_bin: Path, stub: Stub, tree: hw.Tree
) -> str:
    """One pinned run as with a first-party base URL (the production
    situation), its debug log as the evidence of what was read and what
    was eligible. Test-only: `--debug-file` and the first-party override.
    Also run with `--bare` (documented to skip background prefetches): the
    most obvious candidate Exclusion."""
    argv, env = formed(CLAUDE_CHOICE, claude_bin, tree.home)
    log = tree.root / "debug.log"
    argv = [argv[0], *request.param, *argv[1:-2], "--debug-file", str(log), *argv[-2:]]
    env = {**_claude_env(env, stub), "_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL": "1"}
    ran = hw.run_harness(argv, env, tree)
    assert ran.returncode == 0, ran.stderr[-500:]
    assert not stub.leaked()
    return log.read_text(errors="replace")


def test_claude_server_managed_settings_are_not_excluded(claude_debug_log: str) -> None:
    # Under API-key auth the pinned invocation (with or without `--bare`)
    # leaves remote managed settings eligible and fetches policy limits.
    assert "remote managed settings eligible" in claude_debug_log
    assert "Policy limits: fetch failed" in claude_debug_log


def test_claude_managed_sources_are_read(
    claude_debug_log: str, claude_bin: Path
) -> None:
    # The file source and the MDM plists are read at start (debug log), and
    # every locator of the profile is in the binary's own code.
    root = "/Library/Application Support/ClaudeCode"
    assert f"{root}/managed-settings.json" in claude_debug_log
    assert "MDM settings load completed" in claude_debug_log
    assert f"{root}/.claude/skills" in claude_debug_log
    code = claude_bin.read_bytes()
    for name in (
        b"managed-settings.json",
        b"managed-settings.d",
        b"managed-mcp.json",
        b"/Library/Application Support/ClaudeCode",
        b"/Library/Managed Preferences/",
        b"com.anthropic.claudecode",
        b"/api/claude_code/settings",
        b"/api/claude_code/policy_limits",
        b"remote-settings.json",
    ):
        assert name in code, name


# ---------- codex 0.163.0 ----------


def _codex_argv(argv: list[str], stub: Stub) -> list[str]:
    """The pinned argv with the stub as the OpenAI base URL (test-only),
    inserted before the prompt."""
    return [*argv[:-1], "-c", f'openai_base_url="{stub.url}/v1"', argv[-1]]


def _auth_file(tree: hw.Tree, payload: dict[str, object]) -> Path:
    path = tree.home / ".codex" / "auth.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


def test_codex_pinned_invocation(codex_bin: Path, stub: Stub, tree: hw.Tree) -> None:
    hw.plant_codex(tree, stub.url)
    _auth_file(tree, {"OPENAI_API_KEY": FAKE_KEY})
    argv, env = formed(CODEX_CHOICE, codex_bin, tree.home)
    ran = hw.run_harness(_codex_argv(argv, stub), env, tree)
    _settle()
    assert ran.returncode == 0, ran.stderr[-500:]
    assert tree.fired() == []  # hooks (toml, json), MCP, notify: none
    [req] = stub.requests()
    assert (req.path, req.model) == ("/v1/responses", "gpt-5.5")
    assert req.effort == '{"reasoning": {"effort": "high"}}'
    assert hw.MARK_PROJECT not in req.markers  # project_doc_max_bytes=0
    assert req.fake_key and not stub.leaked()
    assert probe.parse_output("codex", ran.stdout).ok


def test_codex_user_instructions_and_tools_reach_the_request(
    codex_bin: Path, stub: Stub, tree: hw.Tree
) -> None:
    # Finding that keeps codex 0.163.0 unverified: the role HOME's
    # ~/.codex/AGENTS.md reaches the request under the pinned argv (no flag
    # disables it), and built-in tools are offered.
    hw.plant_codex(tree, stub.url)
    _auth_file(tree, {"OPENAI_API_KEY": FAKE_KEY})
    argv, env = formed(CODEX_CHOICE, codex_bin, tree.home)
    hw.run_harness(_codex_argv(argv, stub), env, tree)
    [req] = stub.requests()
    assert hw.MARK_USER in req.markers
    assert req.tools > 0


#: Features whose tools could be switched off by `--disable` (0.163.0).
TOOL_FEATURES = (
    "hooks shell_tool unified_exec view_image multi_agent apps plugins "
    "sleep_tool image_generation browser_use computer_use in_app_browser "
    "tool_suggest skill_search goals remote_plugin standalone_web_search "
    "memories code_mode_host"
).split()


def test_codex_tools_remain_with_features_disabled(
    codex_bin: Path, stub: Stub, tree: hw.Tree
) -> None:
    _auth_file(tree, {"OPENAI_API_KEY": FAKE_KEY})
    argv, env = formed(CODEX_CHOICE, codex_bin, tree.home)
    off = [x for f in TOOL_FEATURES for x in ("--disable", f)]
    extra = [*off, "-c", 'web_search="disabled"']
    hw.run_harness(_codex_argv([*argv[:-1], *extra, argv[-1]], stub), env, tree)
    [req] = stub.requests()
    assert req.tools > 0


def test_codex_forced_api_login_logs_a_chatgpt_role_out(
    codex_bin: Path, stub: Stub, tree: hw.Tree
) -> None:
    # The rejected Exclusion candidate: it would log the role out.
    _fake_chatgpt_auth(tree)
    argv, env = formed(CODEX_CHOICE, codex_bin, tree.home)
    forced = ["-c", 'forced_login_method="api"']
    ran = hw.run_harness(_codex_argv([*argv[:-1], *forced, argv[-1]], stub), env, tree)
    assert ran.returncode != 0 and stub.requests() == []
    assert b"Logging out" in ran.stderr


def test_codex_ignore_user_config_is_load_bearing(
    codex_bin: Path, stub: Stub, tree: hw.Tree
) -> None:
    hw.plant_codex(tree, stub.url)
    _auth_file(tree, {"OPENAI_API_KEY": FAKE_KEY})
    argv, env = formed(CODEX_CHOICE, codex_bin, tree.home)
    argv.remove("--ignore-user-config")
    ran = hw.run_harness(_codex_argv(argv, stub), env, tree)
    _settle()
    assert ran.returncode == 0
    # user MCP and notify, and the project layer the user config trusts
    # (its MCP server) — so the project scenario of the pinned run is live.
    assert {"user-mcp", "user-notify", "project-mcp"} <= set(tree.fired())
    [req] = stub.requests()
    assert req.path == "/user-provider/v1/responses"


def test_codex_auth_file_is_the_fingerprinted_source(
    codex_bin: Path, stub: Stub, tree: hw.Tree
) -> None:
    prof = probe.HARNESS_PROFILES[("codex", PLATFORM, hw.CODEX[1])]
    auth = _auth_file(tree, {"OPENAI_API_KEY": FAKE_KEY})
    before = probe.observe_auth(prof, tree.home, role_uid=os.getuid())
    argv, env = formed(CODEX_CHOICE, codex_bin, tree.home)
    hw.run_harness(_codex_argv(argv, stub), env, tree)
    [req] = stub.requests()
    assert req.fake_key  # the key came from the pinned file store
    after_run = probe.observe_auth(prof, tree.home, role_uid=os.getuid())
    assert before.tracking == "tracked" and after_run.files == before.files
    auth.write_text(json.dumps({"OPENAI_API_KEY": FAKE_KEY, "last_refresh": None}))
    swapped = probe.observe_auth(prof, tree.home, role_uid=os.getuid())
    assert swapped.tracking == "tracked"
    assert swapped.files[0]["sha256"] != before.files[0]["sha256"]


def test_codex_without_auth_file_presents_no_credential(
    codex_bin: Path, stub: Stub, tree: hw.Tree
) -> None:
    # Store pinned to `file`: no auth.json → the request carries nothing
    # (the keychain is also unreachable in the sandbox; see evidence).
    argv, env = formed(CODEX_CHOICE, codex_bin, tree.home)
    hw.run_harness(_codex_argv(argv, stub), env, tree)
    [req] = stub.requests()
    assert not req.fake_key and not req.other_credential


def _fake_chatgpt_auth(tree: hw.Tree) -> None:
    def b64(data: dict[str, object]) -> str:
        raw = json.dumps(data).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    now = int(time.time())
    claims = {
        "email": "df@example.invalid",
        "exp": now + 3600,
        "iat": now,
        "https://api.openai.com/auth": {
            "chatgpt_plan_type": "enterprise",
            "chatgpt_account_id": "acc-df",
            "chatgpt_user_id": "user-df",
        },
    }
    jwt = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(claims)}.c2ln"
    tokens = {
        "id_token": jwt,
        "access_token": FAKE_KEY,
        "refresh_token": "r",
        "account_id": "acc-df",
    }
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _auth_file(tree, {"OPENAI_API_KEY": None, "tokens": tokens, "last_refresh": stamp})


def test_codex_cloud_config_is_not_excluded(
    codex_bin: Path, stub: Stub, tree: hw.Tree
) -> None:
    # Under ChatGPT auth the pinned invocation fetches the workspace cloud
    # config bundle before any model request (and fails closed without it):
    # server delivery is not excluded by the pinned argv/env.
    _fake_chatgpt_auth(tree)
    argv, env = formed(CODEX_CHOICE, codex_bin, tree.home)
    base = f'chatgpt_base_url="{stub.url}/backend-api/"'
    ran = hw.run_harness([*argv[:-1], "-c", base, argv[-1]], env, tree)
    assert ran.returncode != 0
    assert any(s.path == "/backend-api/wham/config/bundle" for s in stub.seen)
    assert stub.requests() == []
    assert b"cloud config bundle" in ran.stderr
