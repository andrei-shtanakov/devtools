"""Тесты attest-vendor.sh — детерминированная аттестация целостности
вендор-копии review-kit на волновом PR ре-вендора (решение владельца
2026-09-19).

Стратегия — по образцу tests/test_review_pr.py: git настоящий (bare-репо в
роли origin демо-репо + обычный git-репо в роли апстрима steward), `gh` —
стаб, управляемый переменными окружения и пишущий свой argv в лог-файл.
Так проверяется именно обвязка: побайтовая сверка члена PIN с апстримом на
названном коммите (ядро задачи — детектирует подмену PIN вместе с
подменёнными файлами, зелёными для checksum.sh, но разошедшимися с
настоящим steward), проброс checksum.sh, guard «голова уехала», и то, что
маркер аттестации не читается как вердикт codex-terminal-review.

Контракт кодов выхода attest-vendor.sh:
  0 — сверка чиста, аттестация опубликована (или dry-run);
  2 — конфигурация/аргументы/состояние PR/публикация;
  3 — сверка НЕ прошла (расхождение с апстримом или красный checksum);
  4 — голова PR уехала между сверкой и публикацией.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "attest-vendor.sh"

GH_STUB = """#!/usr/bin/env bash
# Стаб gh: логирует каждый вызов, отвечает по переменным GH_STUB_*.
echo "GH_CONFIG_DIR=${GH_CONFIG_DIR:-} gh $*" >> "$GH_STUB_LOG"
case "$*" in
  *"api user"*)
    echo "${GH_STUB_LOGIN:-ai-prosto}" ;;
  *headRefOid,state*)
    echo "${GH_STUB_HEADOID:?} ${GH_STUB_STATE:-OPEN}" ;;
  *headRefOid*)
    echo "${GH_STUB_HEADOID2:-${GH_STUB_HEADOID:?}}" ;;
  *"pr review"*)
    prev=""
    for a in "$@"; do
      if [ "$prev" = "--body-file" ] && [ -n "${GH_STUB_BODY_OUT:-}" ]; then
        cp "$a" "$GH_STUB_BODY_OUT"
      fi
      prev="$a"
    done ;;
esac
"""


def _git(*args: str, cwd: Path) -> str:
    """Запустить git и вернуть stdout (строго, с проверкой кода)."""
    res = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return res.stdout.strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


KIT_MEMBERS = (
    "scripts/review/build-prompt.sh",
    "scripts/review/local.sh",
)

# checksum.sh — контролируемый стаб (по образцу LOCAL_SH_STUB кита в
# test_review_pr.py): реальная логика PIN-инвентаря steward требует ровно
# определённый состав файлов, а предмет этого набора тестов — обвязка
# attest-vendor.sh, не сам checksum.sh (у него свой контракт и свои тесты
# выше по течению, в steward).
CHECKSUM_STUB = """#!/bin/sh
echo "checksum.sh $*" >> "$CHECKSUM_STUB_LOG"
if [ -n "${CHECKSUM_STUB_EXIT:-}" ] && [ "${CHECKSUM_STUB_EXIT}" != "0" ]; then
  echo "${CHECKSUM_STUB_ERR:-копия кита разошлась с PIN: стаб}" >&2
  exit "$CHECKSUM_STUB_EXIT"
fi
echo "копия кита совпадает с PIN: ${CHECKSUM_STUB_N:-2} файла(ов)."
exit 0
"""


class Fleet:
    """Синтетический флот: bare-origin демо-репо + апстрим steward."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.origin = tmp_path / "origin.git"
        self.fleet_root = tmp_path / "fleet"
        self.repo = self.fleet_root / "demo"
        self.steward = self.fleet_root / "steward"
        self.stub_bin = tmp_path / "bin"
        self.gh_log = tmp_path / "gh.log"
        self.checksum_log = tmp_path / "checksum.log"
        self.body_out = tmp_path / "posted-body.md"
        self.profile_dir = tmp_path / "gh-profile"
        self.profile_dir.mkdir()
        self.fleet_root.mkdir()

        # --- апстрим steward: обычный (не bare) репо с кит-файлами -------
        _git("init", "-b", "master", cwd=self._mk(self.steward))
        _git("config", "user.email", "t@example.com", cwd=self.steward)
        _git("config", "user.name", "t", cwd=self.steward)
        self._write_kit(self.steward, "base\n")
        _git("add", ".", cwd=self.steward)
        _git("commit", "-m", "vendor base", cwd=self.steward)
        self.upstream_sha = _git("rev-parse", "HEAD", cwd=self.steward)

        # --- origin.git (bare) + demo-репо с PIN, указывающим на steward -
        subprocess.run(
            ["git", "init", "--bare", "-b", "master", str(self.origin)],
            check=True,
            capture_output=True,
        )
        seed = tmp_path / "seed"
        _git("init", "-b", "master", cwd=self._mk(seed))
        _git("config", "user.email", "t@example.com", cwd=seed)
        _git("config", "user.name", "t", cwd=seed)
        (seed / "README.md").write_text("demo\n")
        _git("add", ".", cwd=seed)
        _git("commit", "-m", "base", cwd=seed)
        _git("remote", "add", "origin", str(self.origin), cwd=seed)
        _git("push", "-q", "origin", "master", cwd=seed)

        # Ветка PR: вендор-копия, байт-в-байт равная steward @ upstream_sha,
        # плюс checksum.sh (шаг 4 читает его ИЗ ГОЛОВЫ PR, не из master).
        self._write_kit(seed, "base\n")
        self._write_pin(seed, self.upstream_sha)
        self._write_checksum_stub(seed)
        _git("add", ".", cwd=seed)
        _git("commit", "-m", "vendor: re-vendor review-kit", cwd=seed)
        self.head_sha = _git("rev-parse", "HEAD", cwd=seed)
        _git("push", "-q", "origin", "HEAD:refs/pull/7/head", cwd=seed)

        subprocess.run(
            ["git", "clone", "-q", str(self.origin), str(self.repo)],
            check=True,
            capture_output=True,
        )
        gh_url = "git@github.com:andrei-shtanakov/demo.git"
        _git("remote", "set-url", "origin", gh_url, cwd=self.repo)
        _git("config", f"url.{self.origin}.insteadOf", gh_url, cwd=self.repo)
        # Клон не содержит вендор-ветку до фетча ref'а — как в проде.

        self.stub_bin.mkdir()
        gh = self.stub_bin / "gh"
        gh.write_text(GH_STUB)
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)

    @staticmethod
    def _mk(path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _write_kit(self, root: Path, content: str) -> None:
        for member in KIT_MEMBERS:
            f = root / member
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content)

    def _write_pin(self, root: Path, upstream_sha: str) -> None:
        pin = root / "scripts" / "review" / "PIN"
        pin.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"# SOURCE: steward @ {upstream_sha} (master, test)"]
        for member in KIT_MEMBERS:
            lines.append(f"{_sha256(root / member)}  {member}")
        pin.write_text("\n".join(lines) + "\n")

    def _write_checksum_stub(self, root: Path) -> None:
        checksum = root / "scripts" / "review" / "checksum.sh"
        checksum.parent.mkdir(parents=True, exist_ok=True)
        checksum.write_text(CHECKSUM_STUB)
        checksum.chmod(checksum.stat().st_mode | stat.S_IXUSR)

    def tamper_pr_head(self, member: str, content: str) -> None:
        """Переписать член кита В ГОЛОВЕ PR (ветка refs/pull/7/head в
        origin), пересчитав PIN так, чтобы checksum.sh остался зелёным —
        моделирует подмену PIN вместе с подменёнными файлами."""
        seed = self.tmp / "seed"
        (seed / member).write_text(content)
        self._write_pin(seed, self.upstream_sha)
        _git("add", ".", cwd=seed)
        _git("commit", "-m", "tamper", cwd=seed)
        self.head_sha = _git("rev-parse", "HEAD", cwd=seed)
        _git("push", "-q", "-f", "origin", "HEAD:refs/pull/7/head", cwd=seed)

    def env(self, **extra: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            PATH=f"{self.stub_bin}:{env['PATH']}",
            FLEET_ROOT=str(self.fleet_root),
            REVIEW_GH_CONFIG_DIR=str(self.profile_dir),
            GH_STUB_LOG=str(self.gh_log),
            GH_STUB_HEADOID=self.head_sha,
            GH_STUB_BODY_OUT=str(self.body_out),
            CHECKSUM_STUB_LOG=str(self.checksum_log),
            GIT_TERMINAL_PROMPT="0",
            GIT_SSH_COMMAND="false",
        )
        env.update(extra)
        return env

    def run(self, *args: str, **env_extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", str(SCRIPT), *args],
            env=self.env(**env_extra),
            capture_output=True,
            text=True,
            check=False,
        )

    def gh_calls(self) -> str:
        return self.gh_log.read_text() if self.gh_log.exists() else ""


@pytest.fixture
def fleet(tmp_path: Path) -> Fleet:
    return Fleet(tmp_path)


# --- аргументы / конфигурация -----------------------------------------------


def test_no_args_usage(fleet: Fleet) -> None:
    res = fleet.run()
    assert res.returncode == 2
    assert "usage:" in res.stderr


def test_non_numeric_pr(fleet: Fleet) -> None:
    res = fleet.run("demo", "abc")
    assert res.returncode == 2


def test_unknown_repo(fleet: Fleet) -> None:
    res = fleet.run("nosuch", "7")
    assert res.returncode == 2
    assert "nosuch" in res.stderr


def test_unknown_steward(fleet: Fleet) -> None:
    res = fleet.run(
        "demo", "7", STEWARD_DIR=str(fleet.tmp / "nope-steward"),
    )
    assert res.returncode == 2
    assert "steward" in res.stderr
    assert "pr review" not in fleet.gh_calls()


def test_missing_profile_dir(fleet: Fleet) -> None:
    res = fleet.run("demo", "7", REVIEW_GH_CONFIG_DIR=str(fleet.tmp / "nope"))
    assert res.returncode == 2
    assert "gh auth login" in res.stderr


def test_wrong_login_refuses(fleet: Fleet) -> None:
    res = fleet.run("demo", "7", GH_STUB_LOGIN="somebody-else")
    assert res.returncode == 2
    assert "somebody-else" in res.stderr
    assert "pr review" not in fleet.gh_calls()


def test_pr_not_open(fleet: Fleet) -> None:
    res = fleet.run("demo", "7", GH_STUB_STATE="MERGED")
    assert res.returncode == 2
    assert "MERGED" in res.stderr
    assert "pr review" not in fleet.gh_calls()


# --- сверка чистая -----------------------------------------------------------


def test_clean_verdict_approves(fleet: Fleet) -> None:
    res = fleet.run("demo", "7")
    assert res.returncode == 0, res.stderr
    calls = fleet.gh_calls()
    assert "pr review 7" in calls
    assert "--approve" in calls
    assert "--repo andrei-shtanakov/demo" in calls
    for line in calls.splitlines():
        assert f"GH_CONFIG_DIR={fleet.profile_dir}" in line
    body = fleet.body_out.read_text()
    assert fleet.head_sha in body
    assert fleet.upstream_sha in body
    assert "2 член(ов) кита" in body
    assert "все совпали байт-в-байт" in body
    assert "копия кита совпадает с PIN" in body


def test_body_disclaims_content_review(fleet: Fleet) -> None:
    fleet.run("demo", "7")
    body = fleet.body_out.read_text()
    assert "Это НЕ содержательное ревью" in body
    assert "Модель не вызывалась" in body


def test_marker_is_own_and_not_codex_terminal_review(fleet: Fleet) -> None:
    fleet.run("demo", "7")
    body = fleet.body_out.read_text()
    assert "<!-- codex-terminal-review " not in body
    assert (
        f"<!-- ai-prosto-vendor-attestation version=1 kind=review-kit "
        f"head={fleet.head_sha} upstream={fleet.upstream_sha} -->"
    ) in body


def test_checksum_stub_invoked_with_pin(fleet: Fleet) -> None:
    fleet.run("demo", "7")
    log = fleet.checksum_log.read_text()
    assert "--pin scripts/review/PIN" in log


# --- ядро: расхождение с апстримом при зелёном PIN --------------------------


def test_byte_mismatch_with_upstream_blocks_publication(fleet: Fleet) -> None:
    """Подмена PIN вместе с подменёнными файлами: checksum.sh был бы зелёным
    (PIN пересчитан под новое содержимое), но байты разошлись с настоящим
    steward @ upstream_sha — ядро задачи обязано это поймать."""
    fleet.tamper_pr_head(KIT_MEMBERS[0], "tampered\n")
    res = fleet.run("demo", "7", GH_STUB_HEADOID=fleet.head_sha)
    assert res.returncode == 3, res.stdout
    assert "СВЕРКА С АПСТРИМОМ НЕ ПРОШЛА" in res.stderr
    assert KIT_MEMBERS[0] in res.stderr
    assert "pr review" not in fleet.gh_calls()
    assert not fleet.body_out.exists()


def test_byte_mismatch_does_not_reach_checksum(fleet: Fleet) -> None:
    """Порядок из брифа: побайтовая сверка (шаг 3) — раньше checksum.sh
    (шаг 4); при расхождении checksum.sh не вызывается вовсе."""
    fleet.tamper_pr_head(KIT_MEMBERS[0], "tampered\n")
    res = fleet.run("demo", "7", GH_STUB_HEADOID=fleet.head_sha)
    assert res.returncode == 3
    assert not fleet.checksum_log.exists()


def test_single_byte_diff_is_caught(fleet: Fleet) -> None:
    """Расхождение даже одного байта — отказ, не только полностью другой
    файл."""
    original = (fleet.tmp / "seed" / KIT_MEMBERS[1]).read_text()
    tampered = original[:-1] + "X\n"
    fleet.tamper_pr_head(KIT_MEMBERS[1], tampered)
    res = fleet.run("demo", "7", GH_STUB_HEADOID=fleet.head_sha)
    assert res.returncode == 3
    assert "pr review" not in fleet.gh_calls()


def test_member_absent_upstream_is_mismatch(fleet: Fleet) -> None:
    """Член кита, которого нет в апстриме на названном коммите вовсе, — та
    же категория отказа, что и расхождение байт."""
    seed = fleet.tmp / "seed"
    extra_pin = seed / "scripts" / "review" / "PIN"
    extra_member = "scripts/review/apply-threshold.sh"
    (seed / extra_member).write_text("only in demo\n")
    lines = extra_pin.read_text().splitlines()
    lines.append(f"{_sha256(seed / extra_member)}  {extra_member}")
    extra_pin.write_text("\n".join(lines) + "\n")
    _git("add", ".", cwd=seed)
    _git("commit", "-m", "add unvendored member", cwd=seed)
    fleet.head_sha = _git("rev-parse", "HEAD", cwd=seed)
    _git("push", "-q", "-f", "origin", "HEAD:refs/pull/7/head", cwd=seed)

    res = fleet.run("demo", "7", GH_STUB_HEADOID=fleet.head_sha)
    assert res.returncode == 3
    assert "отсутствует в апстриме" in res.stderr
    assert "pr review" not in fleet.gh_calls()


# --- checksum.sh красный -----------------------------------------------------


def test_red_checksum_blocks_publication(fleet: Fleet) -> None:
    res = fleet.run("demo", "7", CHECKSUM_STUB_EXIT="1")
    assert res.returncode == 3, res.stdout
    assert "checksum.sh --pin вернул 1" in res.stderr
    assert "pr review" not in fleet.gh_calls()
    assert not fleet.body_out.exists()


def test_red_checksum_config_code_also_blocks(fleet: Fleet) -> None:
    """Код 2 (конфигурация) самого checksum.sh — тоже отказ публикации:
    attest-vendor.sh не различает 1 и 2 кита, любой ненулевой — блок."""
    res = fleet.run("demo", "7", CHECKSUM_STUB_EXIT="2")
    assert res.returncode == 3
    assert "pr review" not in fleet.gh_calls()


# --- голова уехала -----------------------------------------------------------


def test_head_moved_aborts_publish(fleet: Fleet) -> None:
    res = fleet.run("demo", "7", GH_STUB_HEADOID2="0" * 40)
    assert res.returncode == 4
    assert "уехала" in res.stderr
    assert "pr review" not in fleet.gh_calls()


# --- dry-run -----------------------------------------------------------------


def test_dry_run_prints_body_and_publishes_nothing(fleet: Fleet) -> None:
    res = fleet.run("demo", "7", "--dry-run")
    assert res.returncode == 0, res.stderr
    assert "pr review" not in fleet.gh_calls()
    assert "Vendor-copy integrity attestation" in res.stdout
    assert "ai-prosto-vendor-attestation" in res.stdout
    assert not fleet.body_out.exists()


def test_dry_run_still_runs_byte_compare(fleet: Fleet) -> None:
    fleet.tamper_pr_head(KIT_MEMBERS[0], "tampered\n")
    res = fleet.run("demo", "7", "--dry-run", GH_STUB_HEADOID=fleet.head_sha)
    assert res.returncode == 3
    assert "СВЕРКА С АПСТРИМОМ НЕ ПРОШЛА" in res.stderr
    assert "pr review" not in fleet.gh_calls()


# --- разбор PIN --------------------------------------------------------------


def test_missing_source_line_is_config_error(fleet: Fleet) -> None:
    seed = fleet.tmp / "seed"
    pin = seed / "scripts" / "review" / "PIN"
    lines = [
        line
        for line in pin.read_text().splitlines()
        if not line.startswith("# SOURCE:")
    ]
    pin.write_text("\n".join(lines) + "\n")
    _git("add", ".", cwd=seed)
    _git("commit", "-m", "drop SOURCE", cwd=seed)
    fleet.head_sha = _git("rev-parse", "HEAD", cwd=seed)
    _git("push", "-q", "-f", "origin", "HEAD:refs/pull/7/head", cwd=seed)

    res = fleet.run("demo", "7", GH_STUB_HEADOID=fleet.head_sha)
    assert res.returncode == 2
    assert "SOURCE" in res.stderr
    assert "pr review" not in fleet.gh_calls()


def test_missing_pin_is_config_error(fleet: Fleet) -> None:
    seed = fleet.tmp / "seed"
    (seed / "scripts" / "review" / "PIN").unlink()
    _git("add", ".", cwd=seed)
    _git("commit", "-m", "drop PIN", cwd=seed)
    fleet.head_sha = _git("rev-parse", "HEAD", cwd=seed)
    _git("push", "-q", "-f", "origin", "HEAD:refs/pull/7/head", cwd=seed)

    res = fleet.run("demo", "7", GH_STUB_HEADOID=fleet.head_sha)
    assert res.returncode == 2
    assert "PIN" in res.stderr
    assert "pr review" not in fleet.gh_calls()


def test_unresolvable_upstream_commit_is_config_error(fleet: Fleet) -> None:
    seed = fleet.tmp / "seed"
    fleet._write_pin(seed, "0" * 40)
    _git("add", ".", cwd=seed)
    _git("commit", "-m", "bogus upstream sha", cwd=seed)
    fleet.head_sha = _git("rev-parse", "HEAD", cwd=seed)
    _git("push", "-q", "-f", "origin", "HEAD:refs/pull/7/head", cwd=seed)

    res = fleet.run("demo", "7", GH_STUB_HEADOID=fleet.head_sha)
    assert res.returncode == 2
    assert "не найден" in res.stderr
    assert "pr review" not in fleet.gh_calls()


# --- изолированность рабочего дерева -----------------------------------------


def test_target_repo_worktree_untouched(fleet: Fleet) -> None:
    fleet.run("demo", "7")
    assert _git("branch", "--show-current", cwd=fleet.repo) == "master"
    worktrees = _git("worktree", "list", "--porcelain", cwd=fleet.repo)
    assert worktrees.count("worktree ") == 1
