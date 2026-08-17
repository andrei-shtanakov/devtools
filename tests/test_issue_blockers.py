"""Резолв состояния issue-блокеров `@blocked_by:<repo>#<number>` (issue #40).

Обратное плечо ADR-ECO-006: закрытие inbox-issue получателем должно будить
инициатора. Каноническое `todo://`-ребро уже резолвится пакетом plan-fields;
issue-форма до этого PR матчилась только текстуально (legacy slug-граф), её
СОСТОЯНИЕ не резолвилось. Синтетика из приёмки inbox-issue devtools#40:

  * пункт с блокером-закрытым-issue  -> ERROR класса PF-BLOCKER-STALE;
  * с открытым issue                 -> движение waiting-by-blocker, находок нет;
  * резолв недоступен (gh/API)       -> явный UNAVAILABLE-статус, не clean
    (правило two-contract-guarantees: «неизвестность как зелёное» = дефект).

Резолвер инжектируется — сеть в тестах не нужна и не используется.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "check-plan-fields.py"


@pytest.fixture(scope="module")
def plan_check() -> Any:
    spec = importlib.util.spec_from_file_location("plan_check_issue_blockers", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load plan checker from {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["plan_check_issue_blockers"] = module
    spec.loader.exec_module(module)
    return module


def _index(plan_check: Any, *keys: str) -> Any:
    from plan_fields import ManifestIndex

    return ManifestIndex(frozenset(keys), {})


def _inputs(plan_check: Any, todo: str) -> list[Any]:
    from plan_fields import RepoInput

    return [
        RepoInput("alpha", "- [ ] base work @owner:o @id:base\n"),
        RepoInput("beta", todo),
    ]


WAITER = "- [ ] wait for alpha @owner:github:x @blocked_by:alpha#7 @id:waiter\n"


def _resolve(plan_check: Any, todo: str, state: str | None) -> tuple[Any, dict]:
    """Прогнать issue-блокеры через инжектированный резолвер; вернуть отчёт."""
    index = _index(plan_check, "alpha", "beta")
    inputs = _inputs(plan_check, todo)
    report = plan_check.Report()
    refs = plan_check.collect_issue_refs(inputs, index)

    def resolver(owner_repo: str, number: int) -> str:
        assert owner_repo == "owner/alpha"
        assert number == 7
        if state is None:
            raise plan_check.IssueStateUnavailable("gh exited 4: not logged in")
        return state

    codes = plan_check.check_issue_blockers(
        refs, {"alpha": "owner/alpha"}, report, resolver=resolver
    )
    return report, codes


class TestCollect:
    def test_numeric_ref_collected_slug_ref_ignored(self, plan_check: Any) -> None:
        index = _index(plan_check, "alpha", "beta")
        inputs = _inputs(
            plan_check,
            "- [ ] a @blocked_by:alpha#7\n- [ ] b @blocked_by:alpha#base\n",
        )
        refs = plan_check.collect_issue_refs(inputs, index)
        assert [(r.target_repo, r.number, r.raw_ref) for r in refs] == [
            ("alpha", 7, "alpha#7")
        ]

    def test_checked_item_not_collected(self, plan_check: Any) -> None:
        index = _index(plan_check, "alpha", "beta")
        inputs = _inputs(plan_check, "- [x] done @blocked_by:alpha#7\n")
        assert plan_check.collect_issue_refs(inputs, index) == []


class TestStates:
    def test_closed_issue_is_stale_error(self, plan_check: Any) -> None:
        report, codes = _resolve(plan_check, WAITER, "CLOSED")
        assert any("PF-BLOCKER-STALE" in e for e in report.errors), report.errors
        assert not report.warnings
        assert codes[("beta", 1)] == {"PF-BLOCKER-STALE"}

    def test_merged_pr_ref_is_stale_too(self, plan_check: Any) -> None:
        """gh резолвит и номера PR; state MERGED — ожидание тоже окончено."""
        report, codes = _resolve(plan_check, WAITER, "MERGED")
        assert any("PF-BLOCKER-STALE" in e for e in report.errors), report.errors
        assert codes[("beta", 1)] == {"PF-BLOCKER-STALE"}

    def test_open_issue_is_clean_waiting(self, plan_check: Any) -> None:
        report, codes = _resolve(plan_check, WAITER, "OPEN")
        assert not report.errors and not report.warnings
        assert codes == {}

    def test_unavailable_is_explicit_not_clean(self, plan_check: Any) -> None:
        report, codes = _resolve(plan_check, WAITER, None)
        assert not report.errors
        assert any("UNAVAILABLE" in w for w in report.warnings), report.warnings
        assert codes == {}

    def test_repo_without_github_url_is_unavailable(self, plan_check: Any) -> None:
        index = _index(plan_check, "alpha", "beta")
        inputs = _inputs(plan_check, WAITER)
        report = plan_check.Report()
        refs = plan_check.collect_issue_refs(inputs, index)

        def resolver(owner_repo: str, number: int) -> str:
            raise AssertionError("resolver must not be called without a repo url")

        plan_check.check_issue_blockers(refs, {}, report, resolver=resolver)
        assert any("UNAVAILABLE" in w for w in report.warnings), report.warnings


class TestMovement:
    def _movement(self, plan_check: Any, state: str) -> str:
        """Полный поток main(): exclude issue-рефов из legacy + слияние кодов."""
        index = _index(plan_check, "alpha", "beta")
        inputs = _inputs(plan_check, WAITER)
        report = plan_check.Report()
        refs = plan_check.collect_issue_refs(inputs, index)
        condition_codes = plan_check.resolve_graph(
            inputs,
            index,
            report,
            extra_exclude={(r.source_repo, r.raw_ref) for r in refs},
        )
        _report, issue_codes = _resolve(plan_check, WAITER, state)
        for key, codes in issue_codes.items():
            condition_codes.setdefault(key, set()).update(codes)
        plan_check.check_reporting(inputs, index, condition_codes, report)
        return next(n for n in report.notes if n.startswith("movement:"))

    def test_stale_issue_moves_item_to_stale_condition(self, plan_check: Any) -> None:
        movement = self._movement(plan_check, "CLOSED")
        assert "stale-condition=1" in movement, movement

    def test_open_issue_stays_waiting_by_blocker(self, plan_check: Any) -> None:
        movement = self._movement(plan_check, "OPEN")
        assert "waiting-by-blocker=1" in movement, movement


class TestLegacyHandoff:
    def test_issue_refs_excluded_from_legacy_graph(self, plan_check: Any) -> None:
        """Numeric-реф — собственность issue-резолвера: legacy-граф его не трогает.

        Без exclude `alpha#7` ушёл бы в текстуальный slug-матчинг и дал бы
        шумный PF-BLOCKER-DANGLING (или ложный substring-hit по цифре).
        """
        index = _index(plan_check, "alpha", "beta")
        inputs = _inputs(plan_check, WAITER)
        report = plan_check.Report()
        refs = plan_check.collect_issue_refs(inputs, index)
        exclude = {(r.source_repo, r.raw_ref) for r in refs}
        plan_check.resolve_graph(inputs, index, report, extra_exclude=exclude)
        assert not any("alpha#7" in w for w in report.warnings), report.warnings


class TestRepoMap:
    def test_ssh_and_https_urls_parse_members_skipped(
        self, plan_check: Any, tmp_path: Path
    ) -> None:
        manifest = tmp_path / "workspace-manifest.toml"
        manifest.write_text(
            "[cores.alpha]\n"
            'repo_url = "git@github.com:owner/alpha.git"\ngit_dir = "alpha"\n'
            "[cores.beta]\n"
            'repo_url = "https://github.com/owner/beta"\ngit_dir = "beta"\n'
            "[cores.gamma-sdk]\nmember = true\n"
            'repo_url = "git@github.com:owner/gamma.git"\ngit_dir = "gamma"\n'
            "[tools.delta]\n"
            'repo_url = "https://git.example.com/owner/delta.git"\ngit_dir = "delta"\n',
            encoding="utf-8",
        )
        assert plan_check.github_repo_map(manifest) == {
            "alpha": "owner/alpha",
            "beta": "owner/beta",
        }


class TestGhResolver:
    def test_missing_gh_binary_is_unavailable(
        self, plan_check: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def raise_missing(*args: Any, **kwargs: Any) -> Any:
            raise FileNotFoundError("gh")

        monkeypatch.setattr(plan_check.subprocess, "run", raise_missing)
        with pytest.raises(plan_check.IssueStateUnavailable):
            plan_check.gh_issue_state("owner/alpha", 7)
