"""`act_epochs --check-transition`: переход журнала в git-чекауте df-trust."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "governance" / "act_epochs.py"
A1 = "a" * 40
A2 = "b" * 40
LC = "c" * 40
JOURNAL = "epochs.yaml"
SOURCE = "source.env"

OPEN_1 = f"schema: approval-policy-epochs/v1\nepochs:\n  - epoch: 1\n    anchor: {A1}\n"
CLOSED_1 = (
    "schema: approval-policy-epochs/v1\nepochs:\n"
    f"  - epoch: 1\n    anchor: {A1}\n    last_confirmed: {LC}\n"
    '    valid_until: "2026-10-20T10:00:00Z"\n    closed_reason: "rotation"\n'
)
OPEN_2 = f"  - epoch: 2\n    anchor: {A2}\n"


def _source(activation: str = A1) -> str:
    return (
        "APPROVAL_POLICY_REPO=o/approval-policy\nAPPROVAL_POLICY_BRANCH=main\n"
        f"BOOTSTRAP_ACTIVATION={activation}\nPOLICY_ADMINS=admin\n"
    )


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "README").write_text("x\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def _base_with(repo: Path, journal: str | None) -> str:
    if journal is not None:
        (repo / JOURNAL).write_text(journal)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "journal")
    return _git(repo, "rev-parse", "HEAD")


def _invoke(
    repo: Path, base: str, cwd: Path | None = None, use_flag: bool = True
) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable, str(SCRIPT), "--check-transition", base,
        "--file", JOURNAL, "--source-env", SOURCE,
    ]  # fmt: skip
    if use_flag:
        cmd += ["--repo", str(repo)]
    return subprocess.run(
        cmd, cwd=cwd or repo.parent, capture_output=True, text=True, check=False
    )


def _run(
    repo: Path, base: str, head: str | None, source: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Закоммитить голову PR (журнал + source.env) и проверить."""
    if head is not None:
        (repo / JOURNAL).write_text(head)
    (repo / SOURCE).write_text(source if source is not None else _source())
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "head", "--allow-empty")
    return _invoke(repo, base)


def test_anchor_change_forbidden(repo: Path) -> None:
    base = _base_with(repo, OPEN_1)
    assert _run(repo, base, OPEN_1.replace(A1, A2)).returncode == 1


def test_closed_epoch_edit_forbidden(repo: Path) -> None:
    base = _base_with(repo, CLOSED_1 + OPEN_2)
    edited = (CLOSED_1 + OPEN_2).replace('"rotation"', '"other"')
    assert _run(repo, base, edited).returncode == 1


def test_legal_atomic_close_allowed(repo: Path) -> None:
    base = _base_with(repo, OPEN_1)
    assert _run(repo, base, CLOSED_1 + OPEN_2).returncode == 0


def test_unchanged_journal_allowed(repo: Path) -> None:
    base = _base_with(repo, OPEN_1)
    assert _run(repo, base, OPEN_1).returncode == 0


def test_bootstrap_without_file_allowed(repo: Path) -> None:
    base = _base_with(repo, None)
    assert _run(repo, base, OPEN_1).returncode == 0


def test_bootstrap_wrong_anchor(repo: Path) -> None:
    base = _base_with(repo, None)
    assert _run(repo, base, OPEN_1, _source(A2)).returncode == 1


def test_bootstrap_two_epochs(repo: Path) -> None:
    base = _base_with(repo, None)
    assert _run(repo, base, CLOSED_1 + OPEN_2).returncode == 1


def test_bootstrap_malformed_source(repo: Path) -> None:
    base = _base_with(repo, None)
    assert _run(repo, base, OPEN_1, "garbage\n").returncode == 1


def test_missing_head_file(repo: Path) -> None:
    base = _base_with(repo, OPEN_1)
    _git(repo, "rm", "-q", JOURNAL)
    assert _run(repo, base, None).returncode == 1


def test_later_pr_rewriting_bootstrap_activation(repo: Path) -> None:
    base = _base_with(repo, OPEN_1)
    assert _run(repo, base, OPEN_1, _source(A2)).returncode == 1


def test_symlinked_journal_rejected(repo: Path) -> None:
    base = _base_with(repo, OPEN_1)
    (repo / JOURNAL).unlink()
    (repo / "real.yaml").write_text(OPEN_1)
    (repo / JOURNAL).symlink_to("real.yaml")
    assert _run(repo, base, None).returncode == 1


def test_uncommitted_edit_ignored(repo: Path) -> None:
    base = _base_with(repo, OPEN_1)
    assert _run(repo, base, OPEN_1).returncode == 0
    (repo / JOURNAL).write_text(OPEN_1.replace(A1, A2))
    assert _invoke(repo, base).returncode == 0


def test_not_a_git_checkout(tmp_path: Path) -> None:
    assert _invoke(tmp_path, "f" * 40).returncode == 1


def test_repo_defaults_to_cwd(repo: Path) -> None:
    base = _base_with(repo, OPEN_1)
    assert _run(repo, base, OPEN_1).returncode == 0
    assert _invoke(repo, base, cwd=repo, use_flag=False).returncode == 0


def test_nonexistent_base_sha(repo: Path) -> None:
    _base_with(repo, OPEN_1)
    assert _run(repo, "f" * 40, OPEN_1).returncode == 2


def test_bad_arguments(repo: Path) -> None:
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--check-transition"],
        cwd=repo, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert done.returncode == 2
