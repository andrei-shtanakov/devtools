"""Final-review regressions (2026-09-26): every form below once produced a
false dead / false broken-root. Zone is the default for a launch that did not
resolve completely; an edge must be proven (spec §3.2.3, §2.3 step 2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from selfcheck.graph.build import build_graph
from selfcheck.graph.classify import klass_of
from selfcheck.graph.model import Graph
from selfcheck.roles import role_of
from tests.selfcheck.helpers import write


def graph(tmp: Path, files: dict[str, str]) -> Graph:
    write(tmp, files)
    return build_graph(sorted(files), tmp, role_of, repo_name="r", sched_dir=None)


def protected(g: Graph, anchor: str) -> bool:
    """Not dead-eligible: live, or inside an unresolved-launch zone."""
    in_zone = any(anchor in z.members for z in g.zones)
    return klass_of(g, anchor) == "live" or in_zone


C1_FORMS = {
    "a-var-then-param": (
        {
            "Makefile": "all: ; ./plugins/dispatch.sh x\n",
            "plugins/dispatch.sh": '#!/bin/sh\nSCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"\n"$SCRIPT_DIR/$1"\n',
            "plugins/other.sh": "echo\n",
        },
        "file:plugins/other.sh",
    ),
    "b-literal-around-param": (
        {
            "Makefile": "all: ; ./plugins/run2.sh a\n",
            "plugins/run2.sh": '#!/bin/sh\nhere=$(dirname "$0")\nbash "$here/step-$1.sh"\n',
            "plugins/step-a.sh": "echo\n",
        },
        "file:plugins/step-a.sh",
    ),
    "c-python-fstring-name": (
        {
            "Makefile": "all: ; python3 jobs/runner.py\n",
            "jobs/runner.py": (
                "import subprocess, sys\nfrom pathlib import Path\n"
                "HERE = Path(__file__).parent\n\n\ndef go(name):\n"
                "    subprocess.run([sys.executable, str(HERE / f'job_{name}.py')])\n\n\n"
                "if __name__ == '__main__':\n    go(sys.argv[1])\n"
            ),
            "jobs/job_a.py": "print(1)\n",
        },
        "file:jobs/job_a.py",
    ),
    "d-subprocess-alias": (
        {
            "Makefile": "all: ; python3 tools/t.py\n",
            "tools/t.py": (
                "import subprocess as sp\nfrom pathlib import Path\n"
                "sp.run([str(Path(__file__).parent / 'aliased.sh')])\n"
            ),
            "tools/aliased.sh": "echo\n",
        },
        "file:tools/aliased.sh",
    ),
    "e-wrapper-called-from-another-module": (
        {
            "Makefile": "all: ; python3 main.py\n",
            "shellutil.py": "import subprocess\n\n\ndef sh(cmd):\n    return subprocess.run(cmd)\n",
            "main.py": "from shellutil import sh\n\nsh(['./tools/x.sh'])\n",
            "tools/x.sh": "echo\n",
        },
        "file:tools/x.sh",
    ),
    "f-reassigned-variable": (
        {
            "Makefile": "all: ; ./plugins/p.sh\n",
            "plugins/p.sh": (
                '#!/bin/sh\nDIR="$(dirname "$0")"\nTOOL="$DIR/default.sh"\n'
                'if [ -n "$1" ]; then\n    TOOL="$1"\nfi\n"$TOOL"\n'
            ),
            "plugins/default.sh": "echo\n",
            "plugins/other.sh": "echo\n",
        },
        "file:plugins/other.sh",
    ),
    "g-command-substitution": (
        {
            "Makefile": "all: ; ./scripts/v.sh\n",
            "scripts/v.sh": '#!/bin/sh\nSCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"\nx=$("$SCRIPT_DIR/version.sh")\n',
            "scripts/version.sh": "echo\n",
        },
        "file:scripts/version.sh",
    ),
    "h-heredoc-fed-to-shell": (
        {
            "Makefile": "all: ; ./scripts/h.sh\n",
            "scripts/h.sh": "#!/bin/sh\nsh <<EOF\n./scripts/inner.sh\nEOF\n",
            "scripts/inner.sh": "echo\n",
        },
        "file:scripts/inner.sh",
    ),
    "i-keyword-args": (
        {
            "Makefile": "all: ; python3 k.py\n",
            "k.py": "import subprocess\nsubprocess.run(args=['./kw.sh'])\n",
            "kw.sh": "echo\n",
        },
        "file:kw.sh",
    ),
    "j-makefile-comment-between-recipe-lines": (
        {
            "Makefile": "run:\n\tpython tools/a.py\n# second step\n\tpython tools/b.py\n",
            "tools/a.py": "",
            "tools/b.py": "",
        },
        "file:tools/b.py",
    ),
    "k-makefile-conditional": (
        {
            "Makefile": (
                "cond:\nifeq ($(X),1)\n\t./scripts/c.sh\nelse\n\t./scripts/d.sh\nendif\n"
            ),
            "scripts/c.sh": "echo\n",
            "scripts/d.sh": "echo\n",
        },
        "file:scripts/d.sh",
    ),
    "l-cd-in-recipe": (
        {
            "Makefile": "sub: ; cd deploy && ./install.sh\n",
            "deploy/install.sh": "echo\n",
        },
        "file:deploy/install.sh",
    ),
    "m-workflow-working-directory": (
        {
            ".github/workflows/ci.yml": (
                "on: push\njobs:\n  build:\n    runs-on: x\n    steps:\n"
                "      - run: ./build.sh\n        working-directory: sub\n"
            ),
            "sub/build.sh": "echo\n",
        },
        "file:sub/build.sh",
    ),
    "n-sibling-import-of-a-script": (
        {
            "Makefile": "all: ; python3 tools/runner.py\n",
            "tools/runner.py": "import helper\n\nif __name__ == '__main__':\n    helper.go()\n",
            "tools/helper.py": "def go(): ...\n",
        },
        "file:tools/helper.py",
    ),
}


@pytest.mark.parametrize("form", sorted(C1_FORMS))
def test_form_is_never_a_false_dead(form: str, tmp_path: Path) -> None:
    files, anchor = C1_FORMS[form]
    g = graph(tmp_path, files)
    assert protected(g, anchor), (
        form,
        [(z.caller, sorted(z.members)) for z in g.zones],
    )


def test_cd_and_working_directory_are_not_broken_roots(tmp_path: Path) -> None:
    g = graph(
        tmp_path,
        {
            **C1_FORMS["l-cd-in-recipe"][0],
            **C1_FORMS["m-workflow-working-directory"][0],
            "Makefile": 'help:\n\t@echo "make sub"\nsub: ; cd deploy && ./install.sh\n',
        },
    )
    assert g.broken == []


def test_glob_suffix_narrows_zone_to_matching_names(tmp_path: Path) -> None:
    g = graph(
        tmp_path,
        {**C1_FORMS["b-literal-around-param"][0], "plugins/unrelated.sh": "echo\n"},
    )
    members = {m for z in g.zones for m in z.members}
    assert (
        "file:plugins/step-a.sh" in members
        and "file:plugins/unrelated.sh" not in members
    )
