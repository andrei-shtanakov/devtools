"""Task 8 — graph nodes, roots, edges from structured sources (§3.2.1–3.2.2)."""

from __future__ import annotations

from pathlib import Path

from selfcheck.graph.build import build_graph
from selfcheck.graph.commands import build_index, scan_command
from selfcheck.graph.model import EdgeKind, Graph, NodeKind
from selfcheck.roles import role_of
from tests.selfcheck.helpers import plist_dir, write


def graph(tmp: Path, files: dict[str, str], sched_dir: Path | None = None) -> Graph:
    write(tmp / "repo", files)
    return build_graph(
        sorted(files), tmp / "repo", role_of, repo_name="repo", sched_dir=sched_dir
    )


def kinds(g: Graph, anchor: str) -> set[EdgeKind]:
    return {e.kind for e in g.incoming(anchor)}


INDEX = build_index(
    [
        "a.py",
        "deploy/r16/setup.sh",
        "gov/__init__.py",
        "gov/runner.py",
        "x.sh",
        "gov/__main__.py",
    ],
    None,
)


def test_scan_python_m_module() -> None:
    scan = scan_command(
        "@uv run --frozen --group g python -m gov.runner --x",
        "",
        INDEX,
        shell_vars=False,
    )
    assert scan.targets == ["gov/__init__.py", "gov/runner.py"]
    assert scan_command("python3 -m gov", "", INDEX, shell_vars=False).targets == [
        "gov/__init__.py",
        "gov/__main__.py",
    ]


def test_scan_wrappers_and_assignments() -> None:
    scan = scan_command(
        "sudo GIT_BASE=u deploy/r16/setup.sh", "", INDEX, shell_vars=True
    )
    assert scan.targets == ["deploy/r16/setup.sh"]


def test_scan_argument_is_mention_not_target() -> None:
    scan = scan_command("cat a.py && shellcheck x.sh", "", INDEX, shell_vars=True)
    assert scan.targets == [] and scan.mentions == ["a.py", "x.sh"]


def test_scan_unresolved_and_missing() -> None:
    assert scan_command(
        'sh "$kit/local.sh" "$@"', "", INDEX, shell_vars=True
    ).unresolved == ["$kit/local.sh"]
    scan = scan_command("./missing.py --flag && ./x.sh", "", INDEX, shell_vars=True)
    assert (scan.targets, scan.missing) == (["x.sh"], ["./missing.py"])
    assert scan_command("$(PYTHON) ./a.py", "", INDEX, shell_vars=False).targets == [
        "a.py"
    ]


def test_makefile_roots_continuations_and_make_edges(tmp_path: Path) -> None:
    g = graph(
        tmp_path,
        {
            "Makefile": (
                'help:\n\t@echo "make run — запуск"\n'
                "run: ; @python3 ./a.py $(ARGS)\n"
                "multi:\n\t@./b.sh \\\n\t  && ./c.sh\n"
                "all:\n\t$(MAKE) multi\n"
            ),
            "a.py": "print(1)\n",
            "b.sh": "echo\n",
            "c.sh": "echo\n",
        },
    )
    assert kinds(g, "file:a.py") == {EdgeKind.MAKE}
    assert kinds(g, "file:c.sh") == {EdgeKind.MAKE}
    assert g.nodes["make:Makefile#run"].root and not g.nodes["make:Makefile#multi"].root
    assert kinds(g, "make:Makefile#multi") == {EdgeKind.MAKE}


def test_broken_roots(tmp_path: Path) -> None:
    g = graph(
        tmp_path,
        {
            "Makefile": 'help:\n\t@echo "make gone"\ngone:\n\t@./gone.py\nquiet:\n\t@./gone2.py\n',
            "skills/s/SKILL.md": "```bash\n./missing_tool.sh --x\n```\n",
            "pyproject.toml": '[project.scripts]\nghost = "pkg.nothere:main"\n',
        },
    )
    assert {(a, tok) for a, _, tok in g.broken} == {
        ("make:Makefile#gone", "./gone.py"),
        ("skill:skills/s/SKILL.md", "./missing_tool.sh"),
        ("cli:ghost", "pkg.nothere"),
    }


def test_imports_absolute_relative_and_packages(tmp_path: Path) -> None:
    g = graph(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/a.py": "from pkg import b\nfrom . import c\n",
            "pkg/b.py": "",
            "pkg/c.py": "",
            "main.py": "import pkg.a\n",
        },
    )
    assert kinds(g, "file:pkg/b.py") == {EdgeKind.IMPORT}
    assert kinds(g, "file:pkg/c.py") == {EdgeKind.IMPORT}
    assert kinds(g, "file:pkg/a.py") == {EdgeKind.IMPORT}


def test_ci_skill_runbook_doc_test_edges(tmp_path: Path) -> None:
    g = graph(
        tmp_path,
        {
            "main.py": "x = 1\n",
            ".github/workflows/ci.yml": (
                "on: push\njobs:\n  t:\n    runs-on: x\n    steps:\n"
                "      - run: python3 main.py ${{ github.sha }}\n"
            ),
            "skills/s/SKILL.md": "Запусти `./tool.sh --now`.\n",
            "tool.sh": "#!/bin/sh\necho\n",
            "deploy/README.md": (
                "```bash\nsudo X=1 deploy/setup.sh\n```\nсм. [doc](../only_doc.py)\n"
            ),
            "deploy/setup.sh": "echo\n",
            "only_doc.py": "x = 1\n",
            "tests/test_x.py": "import helper_mod\nSCRIPT = 'tested.py'\n",
            "helper_mod.py": "",
            "tested.py": "",
        },
    )
    assert kinds(g, "file:main.py") == {EdgeKind.CI}
    assert kinds(g, "file:tool.sh") == {EdgeKind.SKILL}
    assert kinds(g, "file:deploy/setup.sh") == {EdgeKind.RUNBOOK}
    assert kinds(g, "file:only_doc.py") == {EdgeKind.DOC}
    assert kinds(g, "file:helper_mod.py") == {EdgeKind.TEST}
    assert kinds(g, "file:tested.py") == {EdgeKind.TEST}
    assert g.nodes["skill:skills/s/SKILL.md"].root
    assert g.nodes["workflow:.github/workflows/ci.yml#t"].root


def test_cli_entry_units_and_launchd(tmp_path: Path) -> None:
    sched = plist_dir(tmp_path, ["/bin/sh", "-c", "cd /home/u/ws/repo && ./nightly.sh"])
    g = graph(
        tmp_path,
        {
            "pyproject.toml": '[project.scripts]\nmytool = "pkg.cli:main"\n',
            "pkg/__init__.py": "",
            "pkg/cli.py": "def main(): ...\n",
            "nightly.sh": "echo\n",
            "unit.sh": "echo\n",
            "deploy/x.service": "[Service]\nExecStart=/srv/repo/unit.sh --go\n",
            "deploy/x.timer": "[Timer]\nOnCalendar=daily\n",
        },
        sched_dir=sched,
    )
    assert g.nodes["cli:mytool"].root
    assert kinds(g, "file:pkg/cli.py") == {EdgeKind.ENTRY}
    assert kinds(g, "file:nightly.sh") == {EdgeKind.SCHED}
    assert kinds(g, "file:unit.sh") == {EdgeKind.SCHED}
    assert g.nodes["unit:deploy/x.timer"].root and g.nodes["unit:deploy/x.service"].root
    assert kinds(g, "unit:deploy/x.service") == {EdgeKind.SCHED}
    assert g.plists == ["dev.atp.x.plist"]


def test_diagnostic_output_gives_nothing(tmp_path: Path) -> None:
    g = graph(
        tmp_path, {"reports/old.md": "ran `./lonely.py`\n", "lonely.py": "x = 1\n"}
    )
    assert (
        g.incoming("file:lonely.py") == []
        and g.mentions.get("file:lonely.py", []) == []
    )


def test_mentions_and_node_flags(tmp_path: Path) -> None:
    g = graph(
        tmp_path,
        {
            "a.py": "# see helper.py\n",
            "helper.py": "x = 1\n",
            "run": "#!/usr/bin/env bash\necho\n",
            "Makefile": "lint:\n\tshellcheck run\n",
        },
    )
    assert g.mentions["file:helper.py"] == ["a.py"]
    assert "Makefile" in g.mentions["file:run"] and g.incoming("file:run") == []
    assert g.nodes["file:run"].kind is NodeKind.FILE and g.nodes["file:run"].executable
    assert not g.nodes["file:helper.py"].executable


def test_local_composite_action_and_timer_unit(tmp_path: Path) -> None:
    g = graph(
        tmp_path,
        {
            ".github/workflows/ci.yml": (
                "on: push\njobs:\n  t:\n    runs-on: x\n    steps:\n"
                "      - uses: ./scripts/act\n"
            ),
            "scripts/act/action.yml": (
                "runs:\n  using: composite\n  steps:\n"
                "    - run: ./scripts/act/go.sh\n      shell: bash\n"
            ),
            "scripts/act/go.sh": "echo\n",
            "deploy/x.timer": "[Timer]\nOnCalendar=daily\nUnit=y.service\n",
            "deploy/y.service": "[Service]\nExecStart=/bin/true\n",
        },
    )
    assert kinds(g, "file:scripts/act/go.sh") == {EdgeKind.CI}
    assert kinds(g, "unit:deploy/y.service") == {EdgeKind.SCHED}


def test_runbook_console_and_uv_project_and_entry_points(tmp_path: Path) -> None:
    g = graph(
        tmp_path,
        {
            "README.md": "```console\n$ uv run --project sub python sub/x.py --go\n```\n",
            "sub/x.py": "print(1)\n",
            "pyproject.toml": '[project.entry-points."maestro.spawners"]\nplug = "pkg.plug:P"\n',
            "pkg/__init__.py": "",
            "pkg/plug.py": "class P: ...\n",
        },
    )
    assert kinds(g, "file:sub/x.py") == {EdgeKind.RUNBOOK}
    assert kinds(g, "file:pkg/plug.py") == {EdgeKind.ENTRY}
    assert g.zones == []
