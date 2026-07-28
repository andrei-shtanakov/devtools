"""Smoke test for the wrapper CLI itself — argparse + the severity projection.

The characterization tests exercise the `plan_fields` package directly; this runs
`check-plan-fields.py --selftest` as a subprocess so an integration break in the
wrapper's own glue (argument parsing, projecting canonical/legacy diagnostics into
errors vs warnings) fails the suite, not just a silent no-op.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "check-plan-fields.py"


def test_wrapper_selftest_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--selftest"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "selftest OK" in result.stdout


def test_wrapper_reports_missing_workspace_cleanly() -> None:
    # a nonexistent root is a clean exit-1 error, not a traceback
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", "/no/such/workspace/here"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "no such workspace directory" in result.stderr
