"""Тесты accept_pr — приёмка integration-PR spec-runner (вариант «а»)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from governance import accept_pr


@dataclass
class _Ops:
    review_exit: int = 0
    facts_seq: list[dict] = field(default_factory=list)
    files: list[str] = field(default_factory=lambda: ["lib/x.ex"])
    merge_ok: bool = True
    calls: list[tuple] = field(default_factory=list)

    def review(self, repo: str, pr: int) -> int:
        self.calls.append(("review", repo, pr))
        return self.review_exit

    def pr_facts(self, repo_slug: str, pr: int) -> dict:
        self.calls.append(("pr_facts",))
        return self.facts_seq.pop(0) if len(self.facts_seq) > 1 \
            else self.facts_seq[0]

    def pr_files(self, repo_slug: str, pr: int) -> list[str]:
        self.calls.append(("pr_files",))
        return self.files

    def merge(self, repo_slug: str, pr: int, sha: str) -> bool:
        self.calls.append(("merge", pr, sha))
        return self.merge_ok


def _facts(**over: Any) -> dict:
    base = {
        "statusCheckRollup": [{"conclusion": "SUCCESS"}],
        "mergeable": "MERGEABLE",
        "headRefOid": "cafe" * 10,
    }
    base.update(over)
    return base


def _no_sleep(_: float) -> None:
    pass


def test_green_path_merges_and_hints_sync(capsys) -> None:
    ops = _Ops(facts_seq=[_facts()])
    rc = accept_pr.accept("kapelle", "o/kapelle", 59, ops, sleep=_no_sleep)
    assert rc == 0
    assert ("merge", 59, "cafe" * 10) in ops.calls
    out = capsys.readouterr().out
    assert "spec-runner sync" in out


def test_review_findings_stop_without_merge(capsys) -> None:
    ops = _Ops(review_exit=1, facts_seq=[_facts()])
    rc = accept_pr.accept("kapelle", "o/kapelle", 59, ops, sleep=_no_sleep)
    assert rc == 1
    assert not any(c[0] == "merge" for c in ops.calls)
    assert "ревью" in capsys.readouterr().out


def test_red_checks_stop_without_merge(capsys) -> None:
    ops = _Ops(facts_seq=[_facts(
        statusCheckRollup=[{"conclusion": "SUCCESS"},
                           {"conclusion": "FAILURE"}],
    )])
    rc = accept_pr.accept("kapelle", "o/kapelle", 59, ops, sleep=_no_sleep)
    assert rc == 1
    assert not any(c[0] == "merge" for c in ops.calls)
    assert "красные чеки" in capsys.readouterr().out


def test_pending_checks_polled_to_completion() -> None:
    pending = _facts(statusCheckRollup=[{"status": "IN_PROGRESS"}])
    ops = _Ops(facts_seq=[pending, pending, _facts()])
    rc = accept_pr.accept("kapelle", "o/kapelle", 59, ops, sleep=_no_sleep)
    assert rc == 0
    assert any(c[0] == "merge" for c in ops.calls)


def test_pending_forever_times_out() -> None:
    pending = _facts(statusCheckRollup=[{"status": "QUEUED"}])
    ops = _Ops(facts_seq=[pending])
    rc = accept_pr.accept(
        "kapelle", "o/kapelle", 59, ops, sleep=_no_sleep, poll_limit=3,
    )
    assert rc == 1
    assert not any(c[0] == "merge" for c in ops.calls)


def test_authority_root_paths_go_to_human(capsys) -> None:
    ops = _Ops(facts_seq=[_facts()],
               files=["lib/x.ex", ".github/workflows/ci.yml"])
    rc = accept_pr.accept("kapelle", "o/kapelle", 59, ops, sleep=_no_sleep)
    assert rc == 1
    assert not any(c[0] == "merge" for c in ops.calls)
    assert "authority-root" in capsys.readouterr().out


def test_conflicting_pr_stops() -> None:
    ops = _Ops(facts_seq=[_facts(mergeable="CONFLICTING")])
    rc = accept_pr.accept("kapelle", "o/kapelle", 59, ops, sleep=_no_sleep)
    assert rc == 1
    assert not any(c[0] == "merge" for c in ops.calls)


def test_merge_refusal_is_reported(capsys) -> None:
    ops = _Ops(facts_seq=[_facts()], merge_ok=False)
    rc = accept_pr.accept("kapelle", "o/kapelle", 59, ops, sleep=_no_sleep)
    assert rc == 1
    assert "мерж не прошёл" in capsys.readouterr().out


def test_empty_rollup_is_pending_not_green() -> None:
    """Приёмка PR #109: пустой rollup двусмыслен (чеки могли ещё не
    создаться на свежем push) — pending, а после потолка опроса — стоп."""
    ops = _Ops(facts_seq=[_facts(statusCheckRollup=[])])
    rc = accept_pr.accept(
        "kapelle", "o/kapelle", 59, ops, sleep=_no_sleep, poll_limit=2,
    )
    assert rc == 1
    assert not any(c[0] == "merge" for c in ops.calls)


def test_head_moved_after_review_stops(capsys) -> None:
    """Приёмка PR #109: ревью привязано к head — пуш между ревью и мержем
    останавливает приёмку, мержится только проревьюенное."""
    ops = _Ops(facts_seq=[
        _facts(),                         # head0 до ревью
        _facts(headRefOid="beef" * 10),   # после чеков — head уехал
    ])
    rc = accept_pr.accept("kapelle", "o/kapelle", 59, ops, sleep=_no_sleep)
    assert rc == 1
    assert not any(c[0] == "merge" for c in ops.calls)
    assert "уехал" in capsys.readouterr().out


def test_unknown_mergeability_polls_then_stops(capsys) -> None:
    """Приёмка PR #109, круг 2: UNKNOWN-mergeability — не разрешение;
    ждём вычисления, на потолке — стоп fail-closed."""
    unknown = _facts(mergeable="UNKNOWN")
    ops = _Ops(facts_seq=[unknown])
    rc = accept_pr.accept(
        "kapelle", "o/kapelle", 59, ops, sleep=_no_sleep, poll_limit=2,
    )
    assert rc == 1
    assert not any(c[0] == "merge" for c in ops.calls)


def test_unknown_then_mergeable_proceeds() -> None:
    unknown = _facts(mergeable="UNKNOWN")
    ops = _Ops(facts_seq=[_facts(), unknown, _facts()])
    rc = accept_pr.accept("kapelle", "o/kapelle", 59, ops, sleep=_no_sleep)
    assert rc == 0
    assert any(c[0] == "merge" for c in ops.calls)
