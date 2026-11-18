"""Английский режим личного пути `dfl`: детерминированные артефакты.

`tasks.md` (весь, а не только шапка), тело/заголовок spec-PR, вопрос,
issue и комментарий ручных проверок, хвост итога и `status -v` при
`language="en"` — без кириллицы из английских входов; машинная грамматика
та же, потребители `tasks.md` его разбирают; при `ru` — байт в байт как без
параметра (#769 срез 1).
"""

from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path

import pytest

from governance import decomposition_guard, run_progress, run_resume, task_bridge
from governance import manual_checks as mc
from governance import review_remarks as rr
from governance.facts import Fact, Outcome
from lite import dfl, i18n
from lite import manual_ask as ma
from lite.output import set_verbosity, verbosity
from lite.personal import check as personal_check
from lite.personal import flow, state, tasks
from tests.personal_en import EN_DECOMPOSITION, EN_DESIGN, cyrillic
from tests.test_lite_personal_flow import SPEC_PR, FakeOps, checker, launch

SUBJECT = "Notes from the terminal"
GENERATED_AT = "2026-11-18T10:00:00Z"
FIXTURES = Path(__file__).parent / "fixtures" / "personal"
#: та же декомпозиция, но BEH-02 проверяет человек по документу шагов
MANUAL_DECOMPOSITION = EN_DECOMPOSITION.replace(
    "`kind: pytest` `target: tests/test_find.py::test_find_prints_matches`",
    "`kind: manual` `target: docs/check-find.md`",
)
CHECK = mc.ManualCheck("BEH-02", "find prints matches", "TASK-002", "docs/check.md")


@pytest.fixture(autouse=True)
def personal_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "personal-runs"
    monkeypatch.setattr(state, "PERSONAL_ROOT", root)
    monkeypatch.setattr(flow, "config_mode", lambda: "test")
    return root


def _run(tmp_path: Path, language: str = "en") -> state.PersonalRun:
    target = tmp_path / "target"
    target.mkdir(exist_ok=True)
    run = state.new_personal_run(
        SUBJECT, "repo", "o/repo", str(target), date(2026, 11, 18), language=language
    )
    state.save(run)
    return run


def _tasks_en(tmp_path: Path) -> str:
    return tasks.render_personal_tasks(
        _run(tmp_path), EN_DECOMPOSITION, EN_DESIGN, GENERATED_AT
    )


# --- tasks.md ------------------------------------------------------------------


def test_tasks_md_en_no_cyrillic(tmp_path: Path) -> None:
    text = _tasks_en(tmp_path)
    assert cyrillic(text) == []
    assert "Personal path: the brief was approved by the owner" in text
    assert "Draft:" not in text
    assert "Implement scenarios BEH-01 (DT-01, group solo)." in text
    assert "- [ ] implement BEH-01: add appends a note" in text
    assert (
        "- [ ] group check: tests/test_add.py::test_add_appends_line "
        "(kind: pytest) green on BEH-01"
    ) in text


def test_tasks_md_en_parsed(tmp_path: Path) -> None:
    """Потребители читают английский tasks.md так же, как русский."""
    text = _tasks_en(tmp_path)
    assert run_progress.read_tasks(text) == [
        ("TASK-001", "The add command"),
        ("TASK-002", "The find command"),
    ]
    assert run_resume.task_progress(text) == (0, 2)
    # машинная грамматика spec-runner — та же
    assert text.count("P2 | TODO   Est: 0.5d") == 2
    assert "Source: workstreams/" in text
    assert "**Depends on:** [TASK-001]" in text
    # пункты чек-листа — той же формы, что переносит §I11
    assert task_bridge._checklist_texts(text.splitlines()) == [
        "implement BEH-01: add appends a note",
        (
            "group check: tests/test_add.py::test_add_appends_line (kind: pytest) "
            "green on BEH-01"
        ),
        "implement BEH-02: find prints matches",
        (
            "group check: tests/test_find.py::test_find_prints_matches "
            "(kind: pytest) green on BEH-02"
        ),
    ]


def test_tasks_md_ru_byte_identical(tmp_path: Path) -> None:
    decomposition = (FIXTURES / "30-decomposition.md").read_text(encoding="utf-8")
    design = (FIXTURES / "20-design.md").read_text(encoding="utf-8")
    _, whole = personal_check.split_decomposition(decomposition)
    scenarios = task_bridge.parse_behaviour(whole)
    dt_tasks, _ = decomposition_guard.parse_dt_tasks(whole)
    args = ("ws", "subj", "b/30-decomposition.md", scenarios, dt_tasks, GENERATED_AT)
    plain = task_bridge.render_tasks_dt(*args, anchor_blob=None, design_text=design)
    ru = task_bridge.render_tasks_dt(
        *args, anchor_blob=None, design_text=design, lang="ru"
    )
    assert ru == plain
    assert "Реализовать сценарии" in plain and "проверка группы" in plain
    run = _run(tmp_path, language="ru")
    personal = tasks.render_personal_tasks(run, decomposition, design, GENERATED_AT)
    assert "Личный путь: бриф одобрен владельцем в разговоре" in personal


def test_tasks_md_en_header_sentence_must_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Подмена строки шапки и по-английски падает, если строки нет."""
    monkeypatch.setattr(tasks, "_DRAFT_SENTENCE_EN", "no such line")
    with pytest.raises(RuntimeError):
        _tasks_en(tmp_path)


def test_tasks_md_en_waiver_and_restated_items() -> None:
    """Пункты waiver и повтора обязательства — английские; id и маркеры те же."""
    owner = decomposition_guard.Deliverable(
        "DEL-01", "capability", "search works", (), None, None
    )
    restated = decomposition_guard.Deliverable(
        "DEL-02", "capability", "search works", (), None, "DEL-01"
    )
    waiver = decomposition_guard.DtWaiver(
        "characterisation", "o/r#1", "tests/test_find.py::test_find_prints_matches"
    )
    _, whole = personal_check.split_decomposition(EN_DECOMPOSITION)
    scenarios = task_bridge.parse_behaviour(whole)
    first, second = decomposition_guard.parse_dt_tasks(whole)[0]
    dt_tasks = [
        dataclasses.replace(first, delivers=(owner,)),
        dataclasses.replace(second, delivers=(restated,), waiver=waiver),
    ]
    text = task_bridge.render_tasks_dt(
        "ws", "subj", "b/30.md", scenarios, dt_tasks, GENERATED_AT, None, lang="en"
    )
    assert cyrillic(text) == []
    assert "- [ ] TDD-waiver DT-02, class characterisation" in text
    assert "confirm ALL conditions of the class: the behaviour is already" in text
    assert (
        "- [ ] DEL-02 (capability): verify that search works — "
        "a restatement of obligation DEL-01 from TASK-001"
    ) in text
    assert "**TDD-waiver:** characterisation · sanction: o/r#1" in text


def test_waiver_conditions_en_mirror_ru() -> None:
    ru = decomposition_guard.waiver_conditions("ru")
    en = decomposition_guard.waiver_conditions("en")
    assert ru is decomposition_guard.WAIVER_CONDITIONS
    assert {k: len(v) for k, v in en.items()} == {k: len(v) for k, v in ru.items()}


# --- PR ------------------------------------------------------------------------


def test_pr_section_en(tmp_path: Path) -> None:
    rr.record(tmp_path, "design", "name the file")
    rr.record(tmp_path, "decomposition", "drop the test")
    rr.mark_applied(tmp_path, "design", 1)
    rr.mark_dropped(tmp_path, "decomposition", 1)
    text = rr.pr_section(tmp_path, lang="en")
    assert cyrillic(text) == []
    assert text.startswith("## Owner's remarks\n\n- design: name the file\n")
    assert "not applied (the revision removed or weakened items): " in text
    assert rr.pr_section(tmp_path, lang="ru") == rr.pr_section(tmp_path)
    assert rr.pr_section(tmp_path).startswith("## Замечания владельца")


def test_personal_pr_body_en(tmp_path: Path) -> None:
    """spec-PR английского прогона: заголовок, тело и коммит без кириллицы."""
    run = _run(tmp_path)
    bundle = Path(run.target_dir) / run.bundle_dir
    bundle.mkdir(parents=True)
    (bundle / "20-design.md").write_text(EN_DESIGN, encoding="utf-8")
    (bundle / "30-decomposition.md").write_text(EN_DECOMPOSITION, encoding="utf-8")
    rr.record(state.run_dir(run.run_id), "design", "name the file")
    rr.mark_applied(state.run_dir(run.run_id), "design", 1)
    run.step = "spec-pr"
    ops = FakeOps(run)
    created: list[tuple[str, str]] = []
    messages: list[str] = []
    original_pr, original_commit = ops.create_pr, ops.commit_paths

    def create_pr(target_dir, repo_slug, branch, title, body, label, **kw):
        created.append((title, body))
        return original_pr(target_dir, repo_slug, branch, title, body, label, **kw)

    def commit_paths(target_dir, paths, message, force_paths=()):
        messages.append(message)
        original_commit(target_dir, paths, message, force_paths)

    ops.create_pr = create_pr  # type: ignore[method-assign]
    ops.commit_paths = commit_paths  # type: ignore[method-assign]
    flow.advance(
        run,
        ops,
        read=lambda _p: None,
        agent=lambda p: "",
        launch=launch,
        check=checker("PASS"),
    )
    assert run.spec_pr == SPEC_PR
    [(title, body)] = created
    assert cyrillic(title) == [] and cyrillic(body) == []
    assert body.startswith("Personal `dfl` path: brief, design, decomposition")
    assert "## Owner's remarks" in body and "- design: name the file" in body
    assert messages and all(cyrillic(m) == [] for m in messages)
    tasks_md = Path(run.target_dir) / tasks.TASKS_PATH.format(ws_id=run.ws_id)
    assert cyrillic(tasks_md.read_text(encoding="utf-8")) == []


# --- ручные проверки -----------------------------------------------------------


def test_manual_question_en() -> None:
    text = mc.question(CHECK, lang="en")
    assert cyrillic(text) == []
    assert text.splitlines() == [
        'Manual check BEH-02 (TASK-002): "find prints matches"',
        "steps: docs/check.md",
        "1. passed · 2. failed · 3. passed with caveat",
    ]
    assert mc.question(CHECK, lang="ru") == mc.question(CHECK)


def test_manual_issue_and_comment_en() -> None:
    answer = {"verdict": mc.NOT_DONE, "note": "nothing found", "at": "2026-11-18"}
    title = mc.issue_title(CHECK, lang="en")
    body = mc.issue_body(CHECK, answer, run_id="r1", pr_url="https://x/1", lang="en")
    comment = mc.pr_comment(
        [CHECK], {"BEH-02": {**answer, "issue": 5}}, run_id="r1", lang="en"
    )
    for text in (title, body, comment):
        assert cyrillic(text) == []
    assert title == "Manual check failed: BEH-02 — find prints matches"
    assert body.splitlines()[0] == mc.issue_marker("r1", "BEH-02")
    assert body.splitlines()[0] == "<!-- dfl-manual-check run=r1 beh=BEH-02 -->"
    assert comment.splitlines()[0] == "<!-- dfl-manual-checks run=r1 -->"
    assert "| BEH-02 | TASK-002 | failed | nothing found | #5 |" in comment
    # русский — как без параметра
    assert mc.issue_title(CHECK, lang="ru") == mc.issue_title(CHECK)
    assert mc.issue_body(CHECK, answer, run_id="r1", pr_url="u", lang="ru") == (
        mc.issue_body(CHECK, answer, run_id="r1", pr_url="u")
    )
    assert mc.pr_comment([CHECK], {}, run_id="r1", lang="ru") == mc.pr_comment(
        [CHECK], {}, run_id="r1"
    )


def test_manual_summary_en() -> None:
    answers = {
        "BEH-01": {"verdict": mc.NOT_DONE, "issue": 3},
        "BEH-02": {"verdict": mc.NOT_DONE, "issue": 4},
        "BEH-03": {"verdict": mc.CAVEAT, "note": "slow"},
    }
    checks = [mc.ManualCheck(b, "t", "TASK-001", None) for b in answers]
    tail = mc.summary(checks, answers, frozenset({4}), lang="en")
    assert tail == (
        "manual checks — failed: 1 (BEH-01 → issue #3); "
        "done later: 1 (BEH-02, issue #4 closed); with caveat: 1"
    )
    assert mc.summary(checks, answers, frozenset({4}), lang="ru") == mc.summary(
        checks, answers, frozenset({4})
    )


class _ForgeOps:
    """Форджа для публикации ответов: записывает issue и комментарий."""

    def __init__(self) -> None:
        self.issues: list[tuple[str, str]] = []
        self.comments: list[str] = []

    def pr_facts(self, repo_slug: str, pr: int) -> dict:
        return {"state": "OPEN", "headRefOid": "h"}

    def find_issue(self, repo_slug, marker, *, labels, state, author):
        return None

    def create_issue(self, repo_slug, title, body, *, labels) -> int:
        self.issues.append((title, body))
        return 9

    def caller_login(self) -> str:
        return "me"

    def pr_comments_fact(self, repo_slug: str, pr: int) -> Fact:
        return Fact(Outcome.FOUND, [], "")

    def comment(self, repo_slug: str, pr: int, body: str) -> None:
        self.comments.append(body)


def test_manual_settle_en_publishes_english(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Проход ручных проверок английского прогона: вопрос, issue и комментарий
    — по-английски, маркеры прежние."""
    _, whole = personal_check.split_decomposition(MANUAL_DECOMPOSITION)
    answers = iter(["failed", "", "nothing printed", ""])
    record: dict = {}
    ops = _ForgeOps()
    reason = ma.settle(
        record,
        load_texts=lambda: (whole, whole),
        tty=True,
        read=lambda _p: next(answers),
        ops=ops,
        repo_slug="o/r",
        pr=77,
        pr_url="https://github.com/o/r/pull/77",
        run_id="r1",
        save=lambda: None,
        lang="en",
    )
    assert reason is None and record["status"] == "completed"
    out = capsys.readouterr().out
    assert 'Manual check BEH-02 (TASK-002): "find prints matches"' in out
    [(title, body)] = ops.issues
    [comment] = ops.comments
    for text in (title, body, comment):
        assert cyrillic(text) == []
    assert body.startswith("<!-- dfl-manual-check run=r1 beh=BEH-02 -->")
    assert comment.startswith("<!-- dfl-manual-checks run=r1 -->")


def test_status_verbose_en_verdict_words(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`dfl status -v` законченного английского прогона: итог и слова
    вердиктов — по-английски (ответ владельца — как записан)."""
    done = _run(tmp_path)
    done.manual = {
        "checks": [mc.as_dict(CHECK)],
        "answers": {"BEH-02": {"verdict": mc.CAVEAT, "note": "slow"}},
    }
    before = verbosity()
    set_verbosity(1)
    try:
        with i18n.language("en"):  # как в `dfl status`
            dfl._finished_status(done)
    finally:
        set_verbosity(before)
    out = capsys.readouterr().out
    assert "manual checks — with caveat: 1" in out
    assert "  BEH-02 (TASK-002): passed with caveat — slow" in out
