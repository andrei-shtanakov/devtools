"""Fixture builders for selfcheck tests.

Deliberately free of ``selfcheck`` imports: ``test_fixtures.py`` checks these
builders before any implementation exists (red phase, plan Task 0).
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REQUIRE_TOOLS = os.environ.get("SELFCHECK_REQUIRE_TOOLS") == "1"
NOW_DT = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
NOW = NOW_DT.timestamp()


def require_tool(name: str) -> None:
    """Skip when ``name`` is absent; fail instead when tools are required."""
    if shutil.which(name) is not None:
        return
    if REQUIRE_TOOLS:
        pytest.fail(f"{name} missing while SELFCHECK_REQUIRE_TOOLS=1")
    pytest.skip(f"{name} not installed (uv run --group selfcheck)")


def ago(days: int) -> str:
    """ISO date ``days`` before the fixed test clock NOW."""
    return (NOW_DT - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")


def git(repo: Path, *args: str, date: str | None = None) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@x.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x.invalid",
    }
    if date is not None:
        env |= {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    return proc.stdout


def write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        if text.startswith("#!"):
            path.chmod(0o755)


def commit(repo: Path, files: dict[str, str], *, date: str) -> None:
    """Write and commit exactly ``files`` (never ``git add -A``)."""
    write(repo, files)
    git(repo, "add", "--", *files, date=date)
    git(repo, "commit", "-q", "-m", "fixture", date=date)


def make_repo(root: Path, files: dict[str, str], *, date: str = "") -> Path:
    """git repo at ``root`` with one commit of ``files`` (default: 90 days old)."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    commit(root, files, date=date or ago(90))
    return root


def tracked(repo: Path) -> set[str]:
    """Tracked paths, NUL-separated (spaces and non-ASCII names intact)."""
    return {p for p in git(repo, "ls-files", "-z").split("\0") if p}


# ---- named fixtures ------------------------------------------------------------


def corpus_repo(tmp: Path) -> Path:
    """Task 3: every corpus membership case in one repo."""
    root = make_repo(
        tmp / "r",
        {
            "a.py": "print(1)\n",
            "with space.sh": "#!/bin/sh\necho hi\n",
            "юникод.py": "x = 1\n",
            "gone.py": "x = 2\n",
            ".gitignore": ".venv/\nout/\n",
            "vendor/lib.py": "y = 1\n",
        },
    )
    (root / "gone.py").unlink()
    (root / "untracked.py").write_text("z = 1\n")
    (root / ".venv").mkdir()
    (root / ".venv" / "x.py").write_text("")
    (root / "link.py").symlink_to(root / "a.py")
    return root


def fake_venv(
    repo: Path, *, version: str = "3.12.1", evil_marker: Path | None = None
) -> Path:
    """A .venv with PyYAML metadata only; optional startup hooks writing a marker."""
    site = (
        repo / ".venv" / "lib" / f"python{version.rsplit('.', 1)[0]}" / "site-packages"
    )
    dist = site / "PyYAML-6.0.3.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: PyYAML\n\nbody\n")
    (dist / "top_level.txt").write_text("_yaml\nyaml\n")
    rec = site / "tomli-2.0.dist-info"
    rec.mkdir()
    (rec / "METADATA").write_text("Name: tomli\n")
    (rec / "RECORD").write_text("tomli/__init__.py,,\ntomli-2.0.dist-info/METADATA,,\n")
    if evil_marker is not None:
        (site / "sitecustomize.py").write_text(f"open({str(evil_marker)!r}, 'w')\n")
        (site / "evil.pth").write_text(f"import os; open({str(evil_marker)!r}, 'w')\n")
    (repo / ".venv" / "pyvenv.cfg").write_text(f"home = /x\nversion_info = {version}\n")
    return site


def fake_tool(
    folder: Path,
    *,
    version: str = "1.2.3",
    code: int = 1,
    emit_repo: bool = True,
    emit_canary: bool = True,
    canary_line: int = 1,
    stdout: str | None = None,
    sleep: float = 0.0,
    version_sleep: float = 0.0,
    write_copy: bool = False,
    unprocessed: str | None = None,
) -> Path:
    """A scriptable stand-in for an external linter (Task 5).

    Emits one JSON item per input; canary inputs get code ``CAN`` at
    ``canary_line``; ``processed`` lists every input except those ending with
    ``unprocessed`` (``"*"`` drops all) — independent of argument order.
    """
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / "fake-tool"
    script.write_text(
        textwrap.dedent(f"""\
        #!{sys.executable}
        import json, pathlib, sys, time
        args = sys.argv[1:]
        if args == ["--version"]:
            time.sleep({version_sleep})
            print("fake {version}")
            sys.exit(0)
        time.sleep({sleep})
        if {write_copy!r}:
            try:
                pathlib.Path(args[0]).parent.joinpath("new.py").write_text("")
            except PermissionError as exc:
                print(f"Permission denied: {{exc}}", file=sys.stderr)
                sys.exit(2)
        out = []
        for a in args:
            can = ".selfcheck-canary" in a
            if (can and {emit_canary!r}) or (not can and {emit_repo!r}):
                out.append({{"path": a, "line": {canary_line} if can else 1,
                            "code": "CAN" if can else "X"}})
        drop = {unprocessed!r}
        processed = [a for a in args if not (drop == "*" or (drop and a.endswith(drop)))]
        text = {stdout!r}
        print(text if text is not None else json.dumps(
            {{"items": out, "processed": processed}}))
        sys.exit({code})
        """)
    )
    script.chmod(0o755)
    return script


def require_npx_package(spec: str) -> None:
    """Skip/fail unless ``npx`` can actually run ``spec`` (network, cache)."""
    require_tool("npx")
    try:
        ok = (
            subprocess.run(
                ["npx", "--yes", spec, "--version"], capture_output=True, timeout=180
            ).returncode
            == 0
        )
    except subprocess.TimeoutExpired:
        ok = False
    if not ok:
        if REQUIRE_TOOLS:
            pytest.fail(f"npx cannot run {spec} while SELFCHECK_REQUIRE_TOOLS=1")
        pytest.skip(f"npx cannot run {spec}")


def plist_dir(tmp: Path, args: list[str], name: str = "dev.atp.x.plist") -> Path:
    folder = tmp / "agents"
    folder.mkdir(exist_ok=True)
    with (folder / name).open("wb") as handle:
        plistlib.dump({"ProgramArguments": args}, handle)
    return folder


USAGE_FILES = {
    "Makefile": 'help:\n\t@echo "make go"\ngo: ; @python3 ./live.py\n',
    "live.py": 'if __name__ == "__main__":\n    pass\n',
    "orphan.py": 'if __name__ == "__main__":\n    pass\n',
    "skills/s/SKILL.md": "no calls\n",
}


def workspace(
    tmp: Path, files: dict[str, str] | None = None, *, date: str = ""
) -> Path:
    """Task 14: a workspace with one-repo manifest and a devtools repo."""
    make_repo(
        tmp / "devtools",
        {
            ".gitignore": "out/\n",
            "pyproject.toml": '[project]\nname = "d"\nversion = "0"\n[tool.ruff]\n',
            **USAGE_FILES,
            **(files or {}),
        },
        date=date,
    )
    (tmp / "m.toml").write_text('[tools.devtools]\ngit_dir = "devtools"\n')
    return tmp


def mi_rank_c_source() -> str:
    """A function radon 6.0.1 ranks MI 'C' (checked: mi 0.0)."""
    lines = ["def tangled(a, b, c, d):", "    total = 0"]
    for i in range(45):
        lines.append(
            f"    if a * {i} + b > c - {i} and d != {i} or a % {i + 1} == b // {i + 2}:"
        )
        body = (
            f"        total += (a ** 2 + b * {i}) / (c + {i + 1}) - d * {i}"
            f" + (a - b) * (c + d) % {i + 3}"
        )
        lines += [body] * 6
        lines.append(f"    elif b << 1 > {i} ^ c:")
        lines.append(f"        total -= (a | b) & (c ^ d) + {i}")
    lines.append("    return total")
    return "\n".join(lines) + "\n"
