"""Guard of the CI df-stage1 root run (Task 13-A, review I1).

Run by the runner user after the root pytest, on its junit XML:

    python -m tests.df.ci_junit <junit-root.xml> [--expect <collected.txt>]

Exit 1 unless the canary `test_root_gate_open` ran and passed, no test was
skipped by the root gate and (with `--expect`) every node id that
`pytest --collect-only -q -m root` listed in the same environment is in the
junit (13-B review: an empty `-k` once ran the canary alone, green). A closed gate (euid not 0, `DF_ROOT_ACCEPTANCE` lost
on the way through sudo) otherwise yields an all-skipped, green run. An xfail
(`skipped type="pytest.xfail"`, the observer's `unobserved` path) is allowed.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path

CANARY = "test_root_gate_open"
GATE_REASON = "root-приёмка:"


def main(argv: Sequence[str] | None = None) -> int:
    """Print every problem of the junit file; 1 when there is one."""
    args = sys.argv[1:] if argv is None else list(argv)
    expect: Path | None = None
    if len(args) == 3 and args[1] == "--expect":
        expect = Path(args[2])
        args = args[:1]
    if len(args) != 1:
        print(
            "usage: python -m tests.df.ci_junit <junit.xml> [--expect <collected>]",
            file=sys.stderr,
        )
        return 2
    path = Path(args[0])
    try:
        text = path.read_text()
        collected = None if expect is None else expect.read_text()
    except OSError as exc:
        problems = [f"не прочитан ({exc})"]
    else:
        problems = root_run_problems(text)
        if collected is not None:
            problems += missing_problems(collected, text)
    for problem in problems:
        print(f"::error title=root run::{problem}")
    return 1 if problems else 0


def root_run_problems(junit_xml: str) -> list[str]:
    """Why this junit does not prove that the root tests ran as root."""
    try:
        root = ET.fromstring(junit_xml)
    except ET.ParseError as exc:
        return [f"junit не разобран: {exc}"]
    problems: list[str] = []
    canaries = [tc for tc in root.iter("testcase") if tc.get("name") == CANARY]
    if not canaries:
        problems.append(f"{CANARY} не выполнялся")
    for case in canaries:
        bad = [ch.tag for ch in case if ch.tag in {"skipped", "failure", "error"}]
        if bad:
            problems.append(f"{CANARY}: {', '.join(bad)}")
    for case in root.iter("testcase"):
        for skipped in case.iter("skipped"):
            if GATE_REASON in skipped.get("message", ""):
                problems.append(f"{case.get('name')}: пропущен шлюзом root")
    return problems


def collected_ids(text: str) -> list[str]:
    """Node ids of `pytest --collect-only -q` output (lines with `::`)."""
    return [line.strip() for line in text.splitlines() if "::" in line]


def junit_ids(junit_xml: str) -> set[str]:
    """`<path>::<name>` of every testcase (module-level tests: the classname
    is the dotted path of the file)."""
    root = ET.fromstring(junit_xml)
    found: set[str] = set()
    for case in root.iter("testcase"):
        module = (case.get("classname") or "").replace(".", "/")
        found.add(f"{module}.py::{case.get('name')}")
    return found


def missing_problems(collected: str, junit_xml: str) -> list[str]:
    """Every collected root test that the junit does not list."""
    ids = collected_ids(collected)
    if not ids:
        return ["collect-only не перечислил ни одного root-теста"]
    try:
        present = junit_ids(junit_xml)
    except ET.ParseError as exc:
        return [f"junit не разобран: {exc}"]
    return [f"{node}: собран, но не выполнен" for node in ids if node not in present]


if __name__ == "__main__":
    raise SystemExit(main())
