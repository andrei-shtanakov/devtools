"""The world of the harness isolation tests (11-B): pinned binaries, the
sentinel trees planted in a role HOME and a project parent, and one safe
run of a real harness.

Safety (R67): a harness runs only as the current user, under
`/usr/bin/sandbox-exec` with a profile that denies network except
localhost, the keychain services and reads of the owner's credential
places (harness homes, gh/review/ai-prosto configs, ~/.ssh, keychains);
with an explicit minimal environment (nothing inherited), HOME in a fresh
temporary directory and a fake key; requests go to the localhost stub.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import pwd
import subprocess
from dataclasses import dataclass
from pathlib import Path

#: The owner's real home (not $HOME): the sandbox denies reads below it.
OWNER_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)


def _bin(env_name: str, default: Path) -> Path:
    """A binary's path: `env_name` overrides the default install location."""
    return Path(os.environ.get(env_name) or default)


#: Binaries of Step 3 (R66): (path, SHA-256, version). The path can be
#: overridden by `DF_TEST_CLAUDE_BIN` / `DF_TEST_CODEX_BIN`; the SHA cannot.
CLAUDE = (
    _bin(
        "DF_TEST_CLAUDE_BIN",
        OWNER_HOME / ".local/share/claude/versions/2.1.309",
    ),
    "5eadc0009cdcdbc0d06e58110d7b2baf88ced6088b1c33a60a21217fe410619c",
    "2.1.309",
)
CODEX = (
    _bin("DF_TEST_CODEX_BIN", Path("/opt/homebrew/Caskroom/codex/0.163.0/bin/codex")),
    "8813367285524ac798dd96d1b071aea30d58708c7226fd236cd275cf1cfdee31",
    "0.163.0",
)
SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
RUN_TIMEOUT = 120
#: `Ran.returncode` of a run that hit the timeout.
TIMED_OUT = -1000
#: Marker strings planted in memory/instruction files; the stub records
#: which of them reached a request body.
MARK_USER = "DFMARK-USER-7f3a"
MARK_PROJECT = "DFMARK-PROJECT-91c2"
MARKS = (MARK_USER, MARK_PROJECT)
#: A model no pinned invocation asks for: seeing it means config won.
SENTINEL_MODEL = "df-sentinel-model"
#: The owner's credential places a harness run must not read (R67).
DENIED_DIRS = (
    ".claude",
    ".codex",
    ".config/gh",
    ".config/review",
    ".config/ai-prosto",
    ".ssh",
    "Library/Keychains",
)
DENIED_FILES = (".claude.json",)


def pinned(binary: tuple[Path, str, str]) -> Path | None:
    """The binary's final target when it is present with the pinned SHA-256
    (hashed once per path per session)."""
    path, sha, _ = binary
    target = Path(os.path.realpath(path))
    return target if _sha256(str(target)) == sha else None


@functools.cache
def _sha256(path: str) -> str | None:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while chunk := fh.read(1 << 20):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def sandbox_profile() -> str:
    """Deny network except localhost, the keychain services and reads of
    the owner's credential places (`DENIED_DIRS`, `DENIED_FILES`)."""
    dirs = "".join(f'\n  (subpath "{OWNER_HOME / d}")' for d in DENIED_DIRS)
    files = "".join(f'\n  (literal "{OWNER_HOME / f}")' for f in DENIED_FILES)
    return f"""(version 1)
(allow default)
(deny network-outbound)
(allow network-outbound (remote ip "localhost:*"))
(allow network-outbound (remote unix-socket))
(deny mach-lookup
  (global-name "com.apple.SecurityServer")
  (global-name "com.apple.securityd")
  (global-name "com.apple.security.agent")
  (global-name "com.apple.secd"))
(deny file-read*{dirs}{files})
"""


@dataclass(frozen=True)
class Tree:
    """A scenario's directories: role HOME, project parent, cwd, sentinels."""

    root: Path
    home: Path
    project: Path
    cwd: Path
    sentinels: Path

    def fired(self) -> list[str]:
        """Names of the sentinel files that something created."""
        return sorted(p.name for p in self.sentinels.iterdir())


def make_tree(root: Path) -> Tree:
    """Empty HOME, a project directory with cwd two levels below it."""
    home, project, sentinels = root / "home", root / "project", root / "sentinels"
    cwd = project / "sub" / "cwd"
    for d in (home, cwd, sentinels):
        d.mkdir(parents=True)
    return Tree(root, home, project, cwd, sentinels)


def _touch(tree: Tree, name: str) -> str:
    return f"/usr/bin/touch {tree.sentinels / name}"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _claude_settings(tree: Tree, scope: str, base_url: str) -> str:
    hook = {
        event: [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": _touch(tree, f"{scope}-hook-{event}"),
                    }
                ]
            }
        ]
        for event in ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd")
    }
    return json.dumps(
        {
            "model": SENTINEL_MODEL,
            "effortLevel": "low",
            "env": {"ANTHROPIC_BASE_URL": f"{base_url}/{scope}-settings-env"},
            "hooks": hook,
            "apiKeyHelper": f"{_touch(tree, f'{scope}-apikeyhelper')}; echo k",
            "enableAllProjectMcpServers": True,
        }
    )


def _mcp(tree: Tree, scope: str) -> dict[str, object]:
    return {
        f"sentinel-{scope}": {
            "type": "stdio",
            "command": "/bin/sh",
            "args": ["-c", f"{_touch(tree, f'{scope}-mcp')}; sleep 3"],
        }
    }


def plant_claude(tree: Tree, base_url: str) -> None:
    """User and project claude config that would fire sentinels, switch the
    model, effort and provider, add MCP servers, memory, a skill and an
    agent carrying markers."""
    _write(
        tree.home / ".claude" / "settings.json",
        _claude_settings(tree, "user", base_url),
    )
    _write(tree.home / ".claude" / "CLAUDE.md", f"{MARK_USER}\n")
    for kind, path in (
        ("skill", "skills/df-sentinel/SKILL.md"),
        ("agent", "agents/df.md"),
    ):
        _write(
            tree.home / ".claude" / path,
            f"---\nname: df-sentinel-{kind}\ndescription: {MARK_USER}\n---\n{MARK_USER}\n",
        )
    _write(
        tree.home / ".claude.json",
        json.dumps({"mcpServers": _mcp(tree, "user"), "hasCompletedOnboarding": True}),
    )
    proj = tree.project
    _write(
        proj / ".claude" / "settings.json", _claude_settings(tree, "project", base_url)
    )
    _write(
        proj / ".claude" / "settings.local.json",
        _claude_settings(tree, "local", base_url),
    )
    _write(proj / "CLAUDE.md", f"{MARK_PROJECT}\n")
    _write(proj / ".mcp.json", json.dumps({"mcpServers": _mcp(tree, "project")}))
    (proj / ".git").mkdir()


def _codex_config(tree: Tree, scope: str, base_url: str) -> str:
    return f"""model = "{SENTINEL_MODEL}"
model_reasoning_effort = "minimal"
model_provider = "sentinel"
notify = ["/bin/sh", "-c", "{_touch(tree, f"{scope}-notify")}"]

[model_providers.sentinel]
name = "sentinel"
base_url = "{base_url}/{scope}-provider/v1"
requires_openai_auth = true
wire_api = "responses"

[mcp_servers.sentinel-{scope}]
command = "/bin/sh"
args = ["-c", "{_touch(tree, f"{scope}-mcp")}; sleep 3"]

[[hooks.SessionStart]]
[[hooks.SessionStart.hooks]]
type = "command"
command = "{_touch(tree, f"{scope}-hook-toml-SessionStart")}"
"""


def _codex_hooks(tree: Tree, scope: str) -> str:
    return json.dumps(
        {
            "hooks": {
                event: [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": _touch(tree, f"{scope}-hook-json-{event}"),
                            }
                        ]
                    }
                ]
                for event in ("SessionStart", "UserPromptSubmit", "Stop")
            }
        }
    )


def plant_codex(tree: Tree, base_url: str) -> None:
    """User and project codex config: model, effort, provider, MCP server,
    notify, hooks (toml and json) and AGENTS.md markers. The project is a
    real git repository that the user config trusts, so the project layer
    is live whenever the user config is read."""
    codex_home = tree.home / ".codex"
    trust = "".join(
        f'\n[projects."{p}"]\ntrust_level = "trusted"\n'
        for p in (tree.project, tree.cwd)
    )
    _write(codex_home / "config.toml", _codex_config(tree, "user", base_url) + trust)
    _write(codex_home / "hooks.json", _codex_hooks(tree, "user"))
    _write(codex_home / "AGENTS.md", f"{MARK_USER}\n")
    proj = tree.project
    _write(proj / ".codex" / "config.toml", _codex_config(tree, "project", base_url))
    _write(proj / ".codex" / "hooks.json", _codex_hooks(tree, "project"))
    _write(proj / "AGENTS.md", f"{MARK_PROJECT}\n")
    _git_init(proj, tree.root)


def _git_init(path: Path, home: Path) -> None:
    """A real repository (codex finds the project root and its layer only
    through one), created without the owner's git configuration."""
    subprocess.run(
        ["/usr/bin/git", "init", "-q", str(path)],
        env={"HOME": str(home), "PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1"},
        check=True,
        capture_output=True,
    )


@dataclass(frozen=True)
class Ran:
    """The outcome of one sandboxed harness run."""

    returncode: int
    stdout: bytes
    stderr: bytes


def run_harness(
    argv: list[str], env: dict[str, str], tree: Tree, *, timeout: int = RUN_TIMEOUT
) -> Ran:
    """Run `argv` under the sandbox with exactly `env` (nothing inherited),
    cwd = the tree's cwd, stdin closed."""
    profile = tree.root / "isolation.sb"
    profile.write_text(sandbox_profile())
    try:
        proc = subprocess.run(
            [str(SANDBOX_EXEC), "-f", str(profile), *argv],
            env=env,
            cwd=tree.cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:  # partial output for diagnosis
        return Ran(TIMED_OUT, _bytes(exc.stdout), _bytes(exc.stderr))
    return Ran(proc.returncode, proc.stdout, proc.stderr)


def _bytes(data: bytes | str | None) -> bytes:
    if data is None:
        return b""
    return data.encode() if isinstance(data, str) else data
