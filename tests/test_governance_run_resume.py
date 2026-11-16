"""Продолжение прогона dfl (#767): кандидат, счётчик задач, тексты."""

from __future__ import annotations

from governance import run_resume as rr

BRANCH = "spec-runner/run-20261114-173445"


def _pr(number: int, state: str = "OPEN", head: str = "spec-runner/run-1") -> dict:
    return {"number": number, "state": state, "headRefName": head}


def test_candidates_open_new_run_prs() -> None:
    prs = [_pr(5, head="spec-runner/run-1"), _pr(3, head="spec-runner/run-0")]
    assert rr.resume_candidates(prs, [3]) == [(5, "spec-runner/run-1")]


def test_candidates_sorted_by_number() -> None:
    prs = [_pr(9, head="spec-runner/run-b"), _pr(7, head="spec-runner/run-a")]
    assert rr.resume_candidates(prs, []) == [
        (7, "spec-runner/run-a"),
        (9, "spec-runner/run-b"),
    ]


def test_candidates_skip_closed() -> None:
    prs = [_pr(5, state="CLOSED"), _pr(6, state="MERGED")]
    assert rr.resume_candidates(prs, []) == []


def test_candidates_ignore_foreign_head() -> None:
    assert rr.resume_candidates([_pr(5, head="feature/x")], []) == []


_TASKS = (
    "# Tasks\n\n"
    "### TASK-001: a\n🔴 P1 | ✅ DONE   Est: 1d\n- [x] сделано\n\n"
    "### TASK-002: b\nP1 | DONE\n\n"
    "### TASK-003: c\nP2 | TODO   Est: 0.5d\n- [ ] не сделано\n"
)


def test_task_progress_counts_done() -> None:
    assert rr.task_progress(_TASKS) == (2, 3)


def test_task_progress_first_meta_line_only() -> None:
    text = "### TASK-001: a\nP1 | TODO\nP1 | DONE\n"
    assert rr.task_progress(text) == (0, 1)


def test_task_progress_no_tasks() -> None:
    assert rr.task_progress("# пусто\n") is None


class _Ops:
    def __init__(self, fetched: bool = True, text: str | None = _TASKS) -> None:
        self.fetched, self.text = fetched, text
        self.seen: list[tuple[str, str, str]] = []

    def fetch_branch(self, target_dir: str, branch: str) -> bool:
        return self.fetched

    def show_file(self, target_dir: str, ref: str, path: str) -> str | None:
        self.seen.append((target_dir, ref, path))
        return self.text


def test_read_progress_reads_origin_ref() -> None:
    ops = _Ops()
    got = rr.read_progress(ops, "/t", "spec-runner/run-1", "spec/ws-tasks.md")
    assert got == (2, 3)
    assert ops.seen == [("/t", "origin/spec-runner/run-1", "spec/ws-tasks.md")]


def test_read_progress_fetch_false_is_none() -> None:
    ops = _Ops(fetched=False)
    assert rr.read_progress(ops, "/t", "b", "p") is None
    assert ops.seen == []


def test_read_progress_missing_file_is_none() -> None:
    assert rr.read_progress(_Ops(text=None), "/t", "b", "p") is None


def test_read_progress_ops_error_is_none() -> None:
    class _Boom(_Ops):
        def show_file(self, target_dir: str, ref: str, path: str) -> str | None:
            raise RuntimeError("git down")

    assert rr.read_progress(_Boom(), "/t", "b", "p") is None


def test_banner_with_and_without_progress() -> None:
    subject, repo = "аналог sprc-runner на Claude Agent SDK", "ai-prosto/ssd-runner"
    assert rr.banner(subject, repo, BRANCH, (14, 16)) == (
        "▶ продолжаю проект «аналог sprc-runner на Claude Agent SDK» · "
        "ai-prosto/ssd-runner\n"
        "  ветка spec-runner/run-20261114-173445 · сделано 14 из 16 задач"
    )
    assert rr.banner(subject, repo, BRANCH, None).endswith(
        "\n  ветка spec-runner/run-20261114-173445 · счётчик задач не прочитан"
    )


def test_resume_stop_names_branch_pr_and_issue() -> None:
    text = rr.resume_stop(BRANCH, 7)
    assert BRANCH in text and "spec-runner#678" in text
    assert "влейте PR #7 в main" in text and "закройте PR #7" in text


def test_several_stop_lists_prs() -> None:
    text = rr.several_stop([9, 10])
    assert "#9, #10" in text and "закройте лишние" in text
