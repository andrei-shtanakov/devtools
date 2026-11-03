"""Stop-ветка контракта `approval-branches` (Task 1 этапа 2b): python-половина
и shell-читатель выводят форму из одного шаблона `APPROVAL_STOP_TEMPLATE`."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from governance import approval_branches as ab

ROOT = Path(__file__).resolve().parent.parent


def test_stop_branch_renders_template() -> None:
    assert ab.stop_branch("ws1", "design", 3) == "stop/ws1-design-g3"


def test_stop_glob_is_derived_from_the_template() -> None:
    assert ab.stop_glob() == "stop/*-g*"


def test_parse_stop_branch_with_dashed_ws_and_node() -> None:
    ref = ab.stop_branch("a-b", "c-d", 12)
    assert ab.parse_stop_branch(ref, "a-b") == ("c-d", 12)


def test_parse_stop_branch_node_may_look_like_a_generation() -> None:
    ref = ab.stop_branch("ws", "x-g1", 2)
    assert ab.parse_stop_branch(ref, "ws") == ("x-g1", 2)


def test_parse_stop_branch_rejects_other_ws() -> None:
    ref = ab.stop_branch("a-b", "c-d", 1)
    assert ab.parse_stop_branch(ref, "other") is None


@pytest.mark.parametrize(
    "ref",
    [
        "stop/ws-n-g",  # no generation
        "stop/ws--g1",  # empty node
        "stop/ws-n-g01",  # not canonical
        "stop/ws-n-g1x",
        "spec/ws-n-g1",
        "xstop/ws-n-g1",
        "stop/ws-n-g1/extra",
    ],
)
def test_parse_stop_branch_rejects_malformed(ref: str) -> None:
    assert ab.parse_stop_branch(ref, "ws") is None


def test_shell_reader_reads_stop_template(tmp_path: Path) -> None:
    glob = _shell_glob(ab.PATTERNS_PATH)
    assert glob == ab.stop_glob()
    other = tmp_path / "p.env"
    other.write_text(
        ab.PATTERNS_PATH.read_text().replace(
            "APPROVAL_STOP_TEMPLATE=stop/{ws_id}-{node}-g{generation}",
            "APPROVAL_STOP_TEMPLATE=halt/{ws_id}.{node}.n{generation}",
        )
    )
    assert _shell_glob(other) == "halt/*.n*"


def test_shell_reader_refuses_a_missing_stop_key(tmp_path: Path) -> None:
    bare = tmp_path / "p.env"
    bare.write_text("APPROVAL_CANDIDATE_TEMPLATE=spec/{ws_id}-approve-{wave}\n")
    done = subprocess.run(_shell(bare), capture_output=True, text=True, check=False)
    assert done.returncode != 0 and done.stdout == ""


def _shell(patterns: Path) -> list[str]:
    script = (
        'die() { echo "$*" >&2; exit 2; }; '
        f'. "{ROOT}/ssot_env.sh"; . "{ROOT}/approval_branches.sh"; '
        f'g=$(approval_stop_glob "{patterns}") || exit $?; printf "%s" "$g"'
    )
    return ["sh", "-c", script]


def _shell_glob(patterns: Path) -> str:
    done = subprocess.run(_shell(patterns), capture_output=True, text=True, check=True)
    return done.stdout
