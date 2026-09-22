"""Тесты human-merge.sh — триггер человеческого мержа по команде (ADR-ECO-011 D6).

Стратегия та же, что у merge-pr.sh: `gh` — стаб в PATH с журналом вызовов;
«merge API не вызван» доказывается отсутствием вызова в журнале.

Контракт кодов выхода: 0 — мерж (или dry-run); 2 — аргументы/профиль/
состояние PR; 3 — актор не авторизован; 4 — форджа отклонила.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "human-merge.sh"
HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"
HUMAN = "andrei-shtanakov"

GH_STUB = """#!/usr/bin/env bash
echo "GH_CONFIG_DIR=${GH_CONFIG_DIR:-} gh $*" >> "$GH_STUB_LOG"
case "$*" in
  *"api user"*)
    if [ -n "${GH_STUB_USER_FAIL:-}" ]; then
      echo "gh: authentication token expired" >&2; exit 1
    fi
    echo "${GH_STUB_LOGIN:-andrei-shtanakov}" ;;
  *"pr view"*)
    printf '%s\\n' "${GH_STUB_STATE:-OPEN}"
    printf '%s\\n' "${GH_STUB_HEADOID-$GH_STUB_DEFAULT_OID}"
    printf '%s\\n' "${GH_STUB_MERGESTATE-CLEAN}"
    printf '%s\\n' "${GH_STUB_BODY-policy: andrei-shtanakov/approval-policy@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa}"
    printf '%s\\n' "${GH_STUB_HEADREF-spec/WS-T1-approve-1-0-1}" ;;
  *"api graphql"*)
    # Стаб игнорирует --jq и печатает уже «отжатые» значения — как pr view.
    case "$*" in
      *history*) printf '%s\\n' "${GH_STUB_POLICY_SHA:-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa}" ;;
      *)         printf 'AUTHORIZED_APPROVER_ACCOUNTS=%s\\n' "${GH_STUB_POLICY_ACCOUNTS:-andrei-shtanakov}" ;;
    esac ;;
  *"/merge"*)
    if [ -n "${GH_STUB_MERGE_FAIL:-}" ]; then
      echo "Head branch was modified. Review and try the merge again." >&2
      exit 1
    fi
    echo '{"merged":true}' ;;
esac
"""


class Fleet:
    def __init__(self, tmp_path: Path) -> None:
        self.fleet_root = tmp_path / "fleet"
        self.repo = self.fleet_root / "demo"
        self.stub_bin = tmp_path / "bin"
        self.gh_log = tmp_path / "gh.log"
        self.repo.mkdir(parents=True)
        subprocess.run(
            ["git", "init", "-q", "-b", "master", str(self.repo)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "remote", "add", "origin",
             "git@github.com:andrei-shtanakov/demo.git"],
            check=True, capture_output=True,
        )
        self.stub_bin.mkdir()
        gh = self.stub_bin / "gh"
        gh.write_text(GH_STUB)
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)

    def env(self, **extra: str) -> dict[str, str]:
        env = os.environ.copy()
        for key in ("GH_CONFIG_DIR", "HUMAN_GH_CONFIG_DIR", "MERGE_GH_CONFIG_DIR"):
            env.pop(key, None)
        env.update(
            PATH=f"{self.stub_bin}:{env['PATH']}",
            FLEET_ROOT=str(self.fleet_root),
            GH_STUB_LOG=str(self.gh_log),
            GH_STUB_DEFAULT_OID=HEAD_SHA,
        )
        env.update(extra)
        return env

    def run(self, *args: str, **env_extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", str(SCRIPT), "demo", "7", *args],
            env=self.env(**env_extra), capture_output=True, text=True, check=False,
        )

    def calls(self) -> list[str]:
        return self.gh_log.read_text().splitlines() if self.gh_log.exists() else []

    def merge_calls(self) -> list[str]:
        return [ln for ln in self.calls() if "/merge" in ln]


@pytest.fixture()
def fleet(tmp_path: Path) -> Fleet:
    return Fleet(tmp_path)


def test_merges_from_operator_profile_with_head_pin(fleet: Fleet) -> None:
    """Ровно один вызов мержа: merge-коммит, пин головы, БЕЗ GH_CONFIG_DIR —
    профиль оператора, не агентский."""
    res = fleet.run()
    assert res.returncode == 0, res.stderr
    assert fleet.merge_calls() == [
        "GH_CONFIG_DIR= gh api -X PUT repos/andrei-shtanakov/demo/pulls/7/merge "
        f"-f merge_method=merge -f sha={HEAD_SHA}"
    ]
    assert f"от {HUMAN}" in res.stdout


def test_explicit_human_profile_is_passed_through(fleet: Fleet, tmp_path: Path) -> None:
    profile = tmp_path / "gh-human"
    profile.mkdir()
    res = fleet.run("--squash", HUMAN_GH_CONFIG_DIR=str(profile))
    assert res.returncode == 0, res.stderr
    assert fleet.merge_calls() == [
        f"GH_CONFIG_DIR={profile} gh api -X PUT "
        "repos/andrei-shtanakov/demo/pulls/7/merge "
        f"-f merge_method=squash -f sha={HEAD_SHA}"
    ]


def test_agent_profile_named_explicitly_is_refused(
    fleet: Fleet, tmp_path: Path
) -> None:
    """Профиль агента человеческим актом быть не может — отказ до сети."""
    agent = tmp_path / "review"
    res = fleet.run(HUMAN_GH_CONFIG_DIR=str(agent), MERGE_GH_CONFIG_DIR=str(agent))
    assert res.returncode == 2, res.stderr
    assert "агентский профиль" in res.stderr
    assert fleet.calls() == []


def test_login_outside_policy_is_refused(fleet: Fleet) -> None:
    res = fleet.run(GH_STUB_POLICY_ACCOUNTS="someone-else")
    assert res.returncode == 3, res.stderr
    assert "approval-policy" in res.stderr
    assert "/merge" not in fleet.gh_log.read_text()


def test_policy_list_is_comma_separated_with_spaces(fleet: Fleet) -> None:
    res = fleet.run(GH_STUB_POLICY_ACCOUNTS=f"someone, {HUMAN} ,other")
    assert res.returncode == 0, res.stderr


def test_policy_changed_after_candidate_is_refused_before_merge(fleet: Fleet) -> None:
    """Смена версии политики после candidate не сжигает человеческий акт:
    отказ до мержа (спека approval-policy §4.5)."""
    res = fleet.run(GH_STUB_POLICY_SHA="b" * 40)
    assert res.returncode == 3, res.stderr
    assert "сменилась" in res.stderr
    assert "/merge" not in fleet.gh_log.read_text()


def test_body_without_policy_line_is_refused(fleet: Fleet) -> None:
    res = fleet.run(GH_STUB_BODY="без пина")
    assert res.returncode == 2, res.stderr
    assert "policy:" in res.stderr


def test_finalize_pr_without_pin_is_judged_by_current_version(fleet: Fleet) -> None:
    """Finalize-PR под «Мерж: человек» пина не несёт — и не обязан: логин
    судится по актуальной версии политики (major ревью PR #344)."""
    res = fleet.run(GH_STUB_HEADREF="spec/WS-T1-approve-1-0-1-final", GH_STUB_BODY="без пина")
    assert res.returncode == 0, res.stderr
    assert "/merge" in fleet.gh_log.read_text()
    res = fleet.run(
        GH_STUB_HEADREF="spec/WS-T1-approve-1-0-1-final", GH_STUB_BODY="без пина",
        GH_STUB_POLICY_ACCOUNTS="someone-else",
    )
    assert res.returncode == 3, res.stderr


def test_missing_head_ref_refuses_before_pin_classification(fleet: Fleet) -> None:
    """Пустое/`null` имя ветки не должно уводить в ветвь «прочий PR» и снимать
    проверку пина (major адресного ревью PR #344)."""
    for ref in ("", "null"):
        res = fleet.run(GH_STUB_HEADREF=ref, GH_STUB_POLICY_SHA="b" * 40)
        assert res.returncode == 2, res.stderr
        assert "имя head-ветки" in res.stderr
        assert "/merge" not in fleet.gh_log.read_text()


def test_non_approval_branch_needs_no_pin(fleet: Fleet) -> None:
    res = fleet.run(GH_STUB_HEADREF="feat/anything", GH_STUB_BODY="обычный PR с лейблом human-merge-required")
    assert res.returncode == 0, res.stderr


def test_env_variable_is_refused(fleet: Fleet) -> None:
    """S7: переменная больше не источник; выставленная — отказ, не молчание."""
    res = fleet.run(AUTHORIZED_APPROVER_ACCOUNTS=HUMAN)
    assert res.returncode == 3, res.stderr
    assert "больше не источник" in res.stderr


def test_head_pin_mismatch_refuses(fleet: Fleet) -> None:
    res = fleet.run("--expect-head", "f" * 40)
    assert res.returncode == 2, res.stderr
    assert "перепроверьте" in res.stderr
    assert fleet.merge_calls() == []


@pytest.mark.parametrize(
    ("env_key", "value"),
    [("GH_STUB_STATE", "MERGED"), ("GH_STUB_MERGESTATE", "DIRTY"),
     ("GH_STUB_MERGESTATE", "")],
)
def test_pr_not_mergeable_refuses(fleet: Fleet, env_key: str, value: str) -> None:
    res = fleet.run(**{env_key: value})
    assert res.returncode == 2, res.stderr
    assert fleet.merge_calls() == []


def test_blocked_by_base_policy_is_allowed_like_merge_pr(fleet: Fleet) -> None:
    """BLOCKED — политика базы для обычного актора; человек с bypass мержит."""
    res = fleet.run(GH_STUB_MERGESTATE="BLOCKED")
    assert res.returncode == 0, res.stderr
    assert len(fleet.merge_calls()) == 1


def test_dry_run_shows_and_does_not_merge(fleet: Fleet) -> None:
    res = fleet.run("--dry-run")
    assert res.returncode == 0, res.stderr
    assert "dry-run" in res.stdout and HEAD_SHA in res.stdout
    assert fleet.merge_calls() == []


def test_forge_rejection_is_code_4(fleet: Fleet) -> None:
    res = fleet.run(GH_STUB_MERGE_FAIL="1")
    assert res.returncode == 4, res.stderr
    assert "форджа отклонила" in res.stderr


def test_profile_failure_is_code_2(fleet: Fleet) -> None:
    res = fleet.run(GH_STUB_USER_FAIL="1")
    assert res.returncode == 2, res.stderr
    assert fleet.merge_calls() == []


def test_unknown_repo_and_bad_args(fleet: Fleet) -> None:
    res = subprocess.run(
        ["sh", str(SCRIPT), "nope", "7"], env=fleet.env(),
        capture_output=True, text=True, check=False,
    )
    assert res.returncode == 2
    res = subprocess.run(
        ["sh", str(SCRIPT), "demo", "x7"], env=fleet.env(),
        capture_output=True, text=True, check=False,
    )
    assert res.returncode == 2
