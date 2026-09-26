"""Task 9 — computed launches and unresolved zones (§3.2.3)."""

from __future__ import annotations

from pathlib import Path

from selfcheck.graph.build import build_graph
from selfcheck.graph.model import EdgeKind, Graph
from selfcheck.roles import role_of
from tests.selfcheck.helpers import write


def graph(tmp: Path, files: dict[str, str]) -> Graph:
    write(tmp, files)
    return build_graph(sorted(files), tmp, role_of, repo_name="r", sched_dir=None)


def exec_targets(g: Graph) -> set[str]:
    return {e.target for e in g.edges if e.kind is EdgeKind.EXEC}


def zone_members(g: Graph) -> set[str]:
    return {m for z in g.zones for m in z.members}


def test_with_name_and_tmux_join(tmp_path: Path) -> None:
    g = graph(
        tmp_path,
        {
            "console.py": (
                "import shlex, subprocess, sys\nfrom pathlib import Path\n\n"
                "def spawn(repo):\n"
                "    worker = Path(__file__).with_name('worker.py')\n"
                "    cmd = [sys.executable, str(worker), '--repo', repo]\n"
                "    shell_cmd = ' '.join(shlex.quote(p) for p in cmd) + '; exec sh'\n"
                "    subprocess.run(['tmux', 'new-session', '-d', shell_cmd])\n"
            ),
            "worker.py": "print(1)\n",
        },
    )
    assert exec_targets(g) == {"file:worker.py"} and g.zones == []


def test_parent_chain_and_os_path(tmp_path: Path) -> None:
    g = graph(
        tmp_path,
        {
            "tools/a.py": (
                "import os, subprocess\nfrom pathlib import Path\n"
                "ROOT = Path(__file__).parent.parent\n"
                "subprocess.run([str(ROOT / 'b.sh')])\n"
                "subprocess.call(os.path.join(os.path.dirname(__file__), 'c.sh'))\n"
            ),
            "b.sh": "echo\n",
            "tools/c.sh": "echo\n",
        },
    )
    assert exec_targets(g) == {"file:b.sh", "file:tools/c.sh"}


def test_shell_script_dir_forms(tmp_path: Path) -> None:
    g = graph(
        tmp_path,
        {
            "bin/run.sh": (
                '#!/bin/sh\nSCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
                'HERE=$(dirname "$0")\n"$SCRIPT_DIR/x.sh" --go\nsh "$HERE/y.sh"\n'
            ),
            "bin/x.sh": "echo\n",
            "bin/y.sh": "echo\n",
        },
    )
    assert exec_targets(g) == {"file:bin/x.sh", "file:bin/y.sh"}


def test_unresolved_shell_launch_suffix_zone(tmp_path: Path) -> None:
    g = graph(
        tmp_path,
        {
            "review.sh": (
                '#!/bin/sh\nkit=$(resolve "$1")\nREVIEW=1 \\\n'
                '    sh "$kit/local.sh" "$@"\n'
            ),
            "scripts/review/local.sh": "echo\n",
            "other.sh": "echo\n",
        },
    )
    assert zone_members(g) == {"file:scripts/review/local.sh"}


def test_dynamic_import_zones_caller_dir(tmp_path: Path) -> None:
    g = graph(
        tmp_path,
        {
            "pkg/loader.py": (
                "import importlib\n\ndef load(name):\n"
                "    return importlib.import_module(name)\n"
            ),
            "pkg/plugin_a.py": "x = 1\n",
            "elsewhere.py": "y = 1\n",
        },
    )
    assert zone_members(g) == {"file:pkg/loader.py", "file:pkg/plugin_a.py"}


def test_non_constant_getattr_zones_caller_dir(tmp_path: Path) -> None:
    g = graph(
        tmp_path,
        {
            "cmd/dispatch.py": "def run(obj, name):\n    return getattr(obj, name)()\n",
            "cmd/sub.py": "x = 1\n",
            "top.py": "y = 1\n",
            "cmd/const.py": "def f(o):\n    return getattr(o, 'attr', None)\n",
        },
    )
    assert zone_members(g) == {
        "file:cmd/dispatch.py",
        "file:cmd/sub.py",
        "file:cmd/const.py",
    }
    assert len(g.zones) == 1


def test_extensionless_shebang_calls(tmp_path: Path) -> None:
    g = graph(
        tmp_path,
        {"harness": '#!/bin/sh\nexec ./helper.sh "$@"\n', "helper.sh": "echo\n"},
    )
    assert exec_targets(g) == {"file:helper.sh"}


def test_same_module_wrapper_resolved_at_call_sites(tmp_path: Path) -> None:
    g = graph(
        tmp_path,
        {
            "runner.py": (
                "import subprocess\n\n\ndef _run(cmd, check=True):\n"
                "    return subprocess.run(cmd, check=check)\n\n\n"
                "_run(['./tool.sh', '--x'])\n"
            ),
            "tool.sh": "echo\n",
            "other.sh": "echo\n",
        },
    )
    assert exec_targets(g) == {"file:tool.sh"} and g.zones == []


def test_constant_import_module_is_an_edge(tmp_path: Path) -> None:
    g = graph(
        tmp_path,
        {"a.py": "import importlib\nimportlib.import_module('b')\n", "b.py": ""},
    )
    assert "file:b.py" in exec_targets(g) and g.zones == []


def test_glob_then_launch_zones_caller_dir(tmp_path: Path) -> None:
    g = graph(
        tmp_path,
        {
            "runner/all.py": (
                "import subprocess\nfrom pathlib import Path\n\n"
                "for script in Path(__file__).parent.glob('*.sh'):\n"
                "    subprocess.run([str(script)])\n"
            ),
            "runner/a.sh": "echo\n",
            "other.sh": "echo\n",
        },
    )
    assert zone_members(g) == {"file:runner/all.py", "file:runner/a.sh"}


def test_shell_launch_without_suffix_zones_caller_dir(tmp_path: Path) -> None:
    g = graph(
        tmp_path,
        {
            "bin/go.sh": '#!/bin/sh\ncmd="$1"\n"$cmd" --x\n',
            "bin/tool.sh": "echo\n",
            "top.sh": "echo\n",
        },
    )
    assert zone_members(g) == {"file:bin/go.sh", "file:bin/tool.sh"}


def test_bash_source_dir_form(tmp_path: Path) -> None:
    g = graph(
        tmp_path,
        {
            "lib/run.sh": (
                "#!/usr/bin/env bash\n"
                'DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n"$DIR/z.sh"\n'
            ),
            "lib/z.sh": "echo\n",
        },
    )
    assert exec_targets(g) == {"file:lib/z.sh"} and g.zones == []


# ---- regressions found by the S1 acceptance run on devtools (2026-09-26) --------


def test_non_subprocess_call_named_like_a_launcher_is_not_a_launch(
    tmp_path: Path,
) -> None:
    g = graph(
        tmp_path,
        {
            "check.py": "def review(call, built):\n    raw = call(built.text)\n    return run(raw)\n",
            "other.py": "x = 1\n",
        },
    )
    assert g.zones == [] and exec_targets(g) == set()


def test_shell_non_command_lines_make_no_zones(tmp_path: Path) -> None:
    g = graph(
        tmp_path,
        {
            "r.sh": (
                "#!/usr/bin/env bash\n"
                'issues+=("${C_YLW}dirty: ${dirty}${C_RESET}")\n'
                'arr=("$x" "$y")\n'
                'case "$1" in\n'
                "    $finalize_glob) echo fin ;;\n"
                '    *" $optional "*) ;;\n'
                "esac\n"
                "body=$(cat <<EOF\n"
                "$pr_info\n"
                "EOF\n"
                ")\n"
                "jq -r '.[] | select(.a)\n"
                '    | ($r.body // "")\' file\n'
            ),
            "tool.sh": "echo\n",
        },
    )
    assert g.zones == [], [(z.caller, sorted(z.members)) for z in g.zones]
