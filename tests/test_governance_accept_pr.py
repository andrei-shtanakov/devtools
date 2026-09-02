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
    dirty: bool = False
    branch: str | None = "master"
    materialize_error: str | None = None
    calls: list[tuple] = field(default_factory=list)

    def is_dirty(self, target_dir: str) -> bool:
        self.calls.append(("is_dirty",))
        return self.dirty

    def current_branch(self, target_dir: str) -> str | None:
        self.calls.append(("current_branch",))
        return self.branch

    def materialize_pr_head(self, target_dir: str, pr: int, sha: str) -> None:
        self.calls.append(("materialize", pr, sha))
        if self.materialize_error is not None:
            raise RuntimeError(self.materialize_error)

    def ensure_branch(self, target_dir: str, branch: str) -> None:
        self.calls.append(("restore", branch))

    def changed_paths(self, target_dir: str, base_branch: str) -> list[str]:
        self.calls.append(("changed_paths", base_branch))
        return self.files

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
        "baseRefName": "master",
    }
    base.update(over)
    return base


def _no_sleep(_: float) -> None:
    pass


def test_green_path_merges_and_hints_sync(capsys) -> None:
    ops = _Ops(facts_seq=[_facts()])
    rc = accept_pr.accept(
        "kapelle", "o/kapelle", 59, ops, "/tmp/kapelle", sleep=_no_sleep,
    )
    assert rc == 0
    assert ("merge", 59, "cafe" * 10) in ops.calls
    out = capsys.readouterr().out
    assert "spec-runner sync" in out


def test_review_findings_stop_without_merge(capsys) -> None:
    ops = _Ops(review_exit=1, facts_seq=[_facts()])
    rc = accept_pr.accept(
        "kapelle", "o/kapelle", 59, ops, "/tmp/kapelle", sleep=_no_sleep,
    )
    assert rc == 1
    assert not any(c[0] == "merge" for c in ops.calls)
    assert "ревью" in capsys.readouterr().out


def test_red_checks_stop_without_merge(capsys) -> None:
    ops = _Ops(facts_seq=[_facts(
        statusCheckRollup=[{"conclusion": "SUCCESS"},
                           {"conclusion": "FAILURE"}],
    )])
    rc = accept_pr.accept(
        "kapelle", "o/kapelle", 59, ops, "/tmp/kapelle", sleep=_no_sleep,
    )
    assert rc == 1
    assert not any(c[0] == "merge" for c in ops.calls)
    assert "красные чеки" in capsys.readouterr().out


def test_pending_checks_polled_to_completion() -> None:
    pending = _facts(statusCheckRollup=[{"status": "IN_PROGRESS"}])
    ops = _Ops(facts_seq=[pending, pending, _facts()])
    rc = accept_pr.accept(
        "kapelle", "o/kapelle", 59, ops, "/tmp/kapelle", sleep=_no_sleep,
    )
    assert rc == 0
    assert any(c[0] == "merge" for c in ops.calls)


def test_pending_forever_times_out() -> None:
    pending = _facts(statusCheckRollup=[{"status": "QUEUED"}])
    ops = _Ops(facts_seq=[pending])
    rc = accept_pr.accept(
        "kapelle", "o/kapelle", 59, ops, "/tmp/kapelle",
        sleep=_no_sleep, poll_limit=3,
    )
    assert rc == 1
    assert not any(c[0] == "merge" for c in ops.calls)


def test_authority_root_paths_go_to_human(capsys) -> None:
    """Гард путей до ревью (приёмка PR #113): authority-PR не мержится
    и ревью на него не запускается; список путей — по локальному диффу
    материализованного head0, не по API (TOCTOU, круг 2)."""
    ops = _Ops(facts_seq=[_facts()],
               files=["lib/x.ex", ".github/workflows/ci.yml"])
    rc = accept_pr.accept(
        "kapelle", "o/kapelle", 59, ops, "/tmp/kapelle", sleep=_no_sleep,
    )
    assert rc == 1
    assert not any(c[0] in ("merge", "review") for c in ops.calls)
    assert ("changed_paths", "master") in ops.calls
    assert ("restore", "master") in ops.calls
    assert "authority-root" in capsys.readouterr().out


def test_review_harness_paths_stop_before_review(capsys) -> None:
    """Приёмка PR #113 (blocker): review-pr.sh исполняет
    scripts/review/local.sh из локального дерева, переключённого на head
    PR — PR, правящий ревью-harness, получил бы исполнение своего кода у
    оператора до вердикта. Стоп после материализации, но ДО ревью;
    ветка возвращается."""
    ops = _Ops(facts_seq=[_facts()],
               files=["lib/x.ex", "scripts/review/local.sh"])
    rc = accept_pr.accept(
        "kapelle", "o/kapelle", 59, ops, "/tmp/kapelle", sleep=_no_sleep,
    )
    assert rc == 1
    assert not any(c[0] in ("merge", "review") for c in ops.calls)
    names = [c[0] for c in ops.calls]
    assert names.index("materialize") < names.index("changed_paths")
    assert ("restore", "master") in ops.calls
    assert "harness" in capsys.readouterr().out


def test_missing_base_branch_stops_before_materialize(capsys) -> None:
    """Без baseRefName гард путей не к чему привязать — fail-closed стоп."""
    facts = _facts()
    del facts["baseRefName"]
    ops = _Ops(facts_seq=[facts])
    rc = accept_pr.accept(
        "kapelle", "o/kapelle", 59, ops, "/tmp/kapelle", sleep=_no_sleep,
    )
    assert rc == 1
    assert not any(
        c[0] in ("materialize", "review", "merge") for c in ops.calls
    )
    assert "base-ветку" in capsys.readouterr().out


def test_changed_paths_failure_stops_and_restores(capsys) -> None:
    """Сбой локального диффа — RuntimeError → стоп без ревью, с restore."""

    class _FailingDiffOps(_Ops):
        def changed_paths(
            self, target_dir: str, base_branch: str
        ) -> list[str]:
            raise RuntimeError("diff rc=128")

    ops = _FailingDiffOps(facts_seq=[_facts()])
    rc = accept_pr.accept(
        "kapelle", "o/kapelle", 59, ops, "/tmp/kapelle", sleep=_no_sleep,
    )
    assert rc == 1
    assert not any(c[0] in ("review", "merge") for c in ops.calls)
    assert ("restore", "master") in ops.calls
    assert "diff rc=128" in capsys.readouterr().out


def test_conflicting_pr_stops() -> None:
    ops = _Ops(facts_seq=[_facts(mergeable="CONFLICTING")])
    rc = accept_pr.accept(
        "kapelle", "o/kapelle", 59, ops, "/tmp/kapelle", sleep=_no_sleep,
    )
    assert rc == 1
    assert not any(c[0] == "merge" for c in ops.calls)


def test_merge_refusal_is_reported(capsys) -> None:
    ops = _Ops(facts_seq=[_facts()], merge_ok=False)
    rc = accept_pr.accept(
        "kapelle", "o/kapelle", 59, ops, "/tmp/kapelle", sleep=_no_sleep,
    )
    assert rc == 1
    assert "мерж не прошёл" in capsys.readouterr().out


def test_empty_rollup_is_pending_not_green() -> None:
    """Приёмка PR #109: пустой rollup двусмыслен (чеки могли ещё не
    создаться на свежем push) — pending, а после потолка опроса — стоп."""
    ops = _Ops(facts_seq=[_facts(statusCheckRollup=[])])
    rc = accept_pr.accept(
        "kapelle", "o/kapelle", 59, ops, "/tmp/kapelle",
        sleep=_no_sleep, poll_limit=2,
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
    rc = accept_pr.accept(
        "kapelle", "o/kapelle", 59, ops, "/tmp/kapelle", sleep=_no_sleep,
    )
    assert rc == 1
    assert not any(c[0] == "merge" for c in ops.calls)
    assert "уехал" in capsys.readouterr().out


def test_unknown_mergeability_polls_then_stops(capsys) -> None:
    """Приёмка PR #109, круг 2: UNKNOWN-mergeability — не разрешение;
    ждём вычисления, на потолке — стоп fail-closed."""
    unknown = _facts(mergeable="UNKNOWN")
    ops = _Ops(facts_seq=[unknown])
    rc = accept_pr.accept(
        "kapelle", "o/kapelle", 59, ops, "/tmp/kapelle",
        sleep=_no_sleep, poll_limit=2,
    )
    assert rc == 1
    assert not any(c[0] == "merge" for c in ops.calls)


def test_unknown_then_mergeable_proceeds() -> None:
    unknown = _facts(mergeable="UNKNOWN")
    ops = _Ops(facts_seq=[_facts(), unknown, _facts()])
    rc = accept_pr.accept(
        "kapelle", "o/kapelle", 59, ops, "/tmp/kapelle", sleep=_no_sleep,
    )
    assert rc == 0
    assert any(c[0] == "merge" for c in ops.calls)


def test_dirty_tree_stops_before_review(capsys) -> None:
    """Ретроспектива 2026-09-02 (урок «грязное дерево», dispatcher#235):
    review-kit читает локальный чекаут — грязное дерево даёт ревью ложную
    фактуру. Стоп ДО ревью и до материализации."""
    ops = _Ops(dirty=True, facts_seq=[_facts()])
    rc = accept_pr.accept(
        "kapelle", "o/kapelle", 59, ops, "/tmp/kapelle", sleep=_no_sleep,
    )
    assert rc == 1
    assert not any(c[0] in ("review", "materialize") for c in ops.calls)
    assert "грязное" in capsys.readouterr().out


def test_detached_checkout_stops(capsys) -> None:
    """Detached HEAD в чекауте — некуда возвращаться после приёмки; стоп."""
    ops = _Ops(branch=None, facts_seq=[_facts()])
    rc = accept_pr.accept(
        "kapelle", "o/kapelle", 59, ops, "/tmp/kapelle", sleep=_no_sleep,
    )
    assert rc == 1
    assert not any(c[0] in ("review", "materialize") for c in ops.calls)
    assert "detached" in capsys.readouterr().out


def test_materializes_head_before_review_restores_after_merge() -> None:
    """Урок 7 (devtools#110): review-kit считает локальное дерево
    авторитетным — head PR материализуется ДО ревью (пинованный head0),
    исходная ветка возвращается после приёмки."""
    ops = _Ops(facts_seq=[_facts()])
    rc = accept_pr.accept(
        "kapelle", "o/kapelle", 59, ops, "/tmp/kapelle", sleep=_no_sleep,
    )
    assert rc == 0
    names = [c[0] for c in ops.calls]
    assert names.index("materialize") < names.index("review")
    assert ("materialize", 59, "cafe" * 10) in ops.calls
    assert names.index("merge") < names.index("restore")
    assert ("restore", "master") in ops.calls


def test_restore_happens_on_review_stop() -> None:
    """Стоп-пути тоже возвращают исходную ветку — чекаут не остаётся
    в detached HEAD после находок ревью."""
    ops = _Ops(review_exit=1, facts_seq=[_facts()])
    rc = accept_pr.accept(
        "kapelle", "o/kapelle", 59, ops, "/tmp/kapelle", sleep=_no_sleep,
    )
    assert rc == 1
    assert ("restore", "master") in ops.calls


def test_materialize_failure_stops_and_restores(capsys) -> None:
    ops = _Ops(materialize_error="fetch rc=128", facts_seq=[_facts()])
    rc = accept_pr.accept(
        "kapelle", "o/kapelle", 59, ops, "/tmp/kapelle", sleep=_no_sleep,
    )
    assert rc == 1
    assert not any(c[0] == "review" for c in ops.calls)
    assert ("restore", "master") in ops.calls
    assert "материализ" in capsys.readouterr().out


def test_origin_slug_parses_ssh_and_https() -> None:
    """Гард владельца (приёмка PR #109, круг 3): парсер origin-URL."""
    f = accept_pr._origin_slug
    assert f("git@github.com:andrei-shtanakov/kapelle.git") == \
        "andrei-shtanakov/kapelle"
    assert f("https://github.com/Andrei-Shtanakov/kapelle") == \
        "andrei-shtanakov/kapelle"
    assert f("не-url") is None


def test_main_refuses_owner_checkout_mismatch(monkeypatch, capsys) -> None:
    import types

    def fake_run(argv, **kwargs):
        return types.SimpleNamespace(
            returncode=0,
            stdout="git@github.com:someone-else/kapelle.git\n", stderr="",
        )

    monkeypatch.setattr(accept_pr.subprocess, "run", fake_run)
    rc = accept_pr.main(["--repo", "kapelle", "--pr", "1"])
    assert rc == 2
    assert "один репозиторий" in capsys.readouterr().out


def test_base_retarget_after_guard_stops(capsys) -> None:
    """Приёмка PR #113, круг 3: ретаргет base-ветки при том же head меняет
    фактический дифф — гард путей считался против исходной базы. Пин base
    симметричен пину head: смена — стоп без мержа."""
    ops = _Ops(facts_seq=[
        _facts(),                          # base_branch = master
        _facts(baseRefName="release"),     # после чеков — base сменилась
    ])
    rc = accept_pr.accept(
        "kapelle", "o/kapelle", 59, ops, "/tmp/kapelle", sleep=_no_sleep,
    )
    assert rc == 1
    assert not any(c[0] == "merge" for c in ops.calls)
    assert ("restore", "master") in ops.calls
    assert "base-ветка" in capsys.readouterr().out


def test_root_review_pr_sh_is_harness_too(capsys) -> None:
    """Приёмка PR #113, круг 6: для PR в сам devtools target_dir — это
    DEVTOOLS_ROOT, и Ops.review исполняет корневой review-pr.sh из
    материализованного дерева. Его правка — тоже harness-стоп."""
    ops = _Ops(facts_seq=[_facts()], files=["review-pr.sh"])
    rc = accept_pr.accept(
        "devtools", "o/devtools", 113, ops, "/tmp/devtools", sleep=_no_sleep,
    )
    assert rc == 1
    assert not any(c[0] in ("merge", "review") for c in ops.calls)
    assert ("restore", "master") in ops.calls
    assert "harness" in capsys.readouterr().out
