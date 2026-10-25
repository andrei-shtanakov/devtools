"""Guard of the CI df-stage1 root run (Task 13-A, review I1).

Run by the runner user after the root pytest, on its junit XML:

    python -m tests.df.ci_junit <junit-root.xml>

Exit 1 unless the canary `test_root_gate_open` ran and passed and no test was
skipped by the root gate. A closed gate (euid not 0, `DF_ROOT_ACCEPTANCE` lost
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
    if len(args) != 1:
        print("usage: python -m tests.df.ci_junit <junit.xml>", file=sys.stderr)
        return 2
    path = Path(args[0])
    try:
        text = path.read_text()
    except OSError as exc:
        problems = [f"{path}: не прочитан ({exc})"]
    else:
        problems = root_run_problems(text)
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


if __name__ == "__main__":
    raise SystemExit(main())
