"""Tests for the owner-review pause on the personal path (#768, Task 4)."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path

import pytest

from governance.author_launch import LaunchOutcome
from lite import review_pause as rp
from lite.personal import flow, interviewer, state
from lite.personal.brief import Goal, brief_from_json, render_brief
from lite.personal.interviewer import TRANSCRIPT, Halt
from tests.test_lite_personal_flow import BRIEF, NODE_TEXT, SPEC_PR, FakeOps, checker

DESIGN = NODE_TEXT["20-design.md"]
DECOMP = NODE_TEXT["30-decomposition.md"]
DESIGN_EDGE = "personal-design-vs-brief"
DECOMP_EDGE = "personal-decomposition-vs-design-brief"
NO_D02 = DESIGN.split("#### D-02")[0]
PAUSE_HEAD = "этап «{}» готов и прошёл проверку"


@pytest.fixture(autouse=True)
def personal_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "personal-runs"
    monkeypatch.setattr(state, "PERSONAL_ROOT", root)
    monkeypatch.setattr(flow, "interview", lambda *a, **k: BRIEF)
    monkeypatch.setattr(flow, "config_mode", lambda: "test")
    return root


def _run(tmp_path: Path, pause: tuple[str, ...]) -> state.PersonalRun:
    target = tmp_path / "target"
    target.mkdir()
    r = state.new_personal_run(
        "Заметки", "repo", "o/repo", str(target), date(2026, 11, 15), pause
    )
    state.save(r)
    return r


def _lines(*lines: str):
    """Scripted terminal: lines in order, then EOF (`None`); records prompts."""
    queue = list(lines)
    seen: list[str] = []

    def read(prompt: str) -> str | None:
        seen.append(prompt)
        return queue.pop(0) if queue else None

    read.seen = seen  # type: ignore[attr-defined]
    read.left = queue  # type: ignore[attr-defined]
    return read


def _author(*revisions: str):
    """Author: first write — fixture; each revise call — next of `revisions`."""
    queue = list(revisions)
    prompts: list[str] = []

    def fake(call, prompt) -> LaunchOutcome:
        text = prompt("/tmp/out.md")
        body = NODE_TEXT[call.filename]
        if "## Режим правки" in text:
            prompts.append(text)
            body = queue.pop(0)
        path = Path(call.target_dir) / call.bundle_dir / call.filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return LaunchOutcome(0)

    fake.prompts = prompts  # type: ignore[attr-defined]
    return fake


def _advance(run, ops, *, read, launch=None, check=None, tty=True, agent=None) -> int:
    return flow.advance(
        run,
        ops,
        read=read,
        agent=agent or (lambda p: ""),
        launch=launch or _author(),
        check=check or checker("PASS"),
        tty=lambda: tty,
    )


def _bundle(run: state.PersonalRun) -> Path:
    return Path(run.target_dir) / run.bundle_dir


def _no_read(prompt: str) -> str | None:
    raise AssertionError("no pause expected")


def test_no_pause_when_stage_not_selected(tmp_path, capsys) -> None:
    run = _run(tmp_path, ())
    check = checker("PASS")
    assert _advance(run, FakeOps(run), read=_no_read, check=check, tty=False) == 0
    assert run.step == "code-pr"
    assert check.calls == [DESIGN_EDGE, DECOMP_EDGE]  # type: ignore[attr-defined]
    rd = state.run_dir(run.run_id)
    assert not (rd / rp.REMARKS).exists()
    assert not (rd / flow.REVIEW_PASSED).exists()
    assert "готов и прошёл проверку" not in capsys.readouterr().out


def test_done_word_continues(tmp_path, capsys) -> None:
    run = _run(tmp_path, ("design",))
    read = _lines("Дальше")
    assert _advance(run, FakeOps(run), read=read) == 0
    assert run.step == "code-pr"
    out = capsys.readouterr().out
    assert PAUSE_HEAD.format("дизайн") in out
    assert f"file://{(_bundle(run) / '20-design.md').resolve()}" in out
    assert rp.all_remarks(state.run_dir(run.run_id)) == []


def test_remark_revises_and_rechecks(tmp_path, capsys) -> None:
    run = _run(tmp_path, ("design",))
    revised = DESIGN + "\nЛоги пишутся в stderr.\n"
    author = _author(revised)
    check = checker("PASS")
    read = _lines("добавь логи", "", "дальше")
    assert _advance(run, FakeOps(run), read=read, launch=author, check=check) == 0
    assert run.step == "code-pr"
    [prompt] = author.prompts  # type: ignore[attr-defined]
    assert "--- ЗАМЕЧАНИЯ ---\nнет\n" in prompt
    assert "- замечание владельца: добавь логи" in prompt
    assert check.calls == [  # type: ignore[attr-defined]
        DESIGN_EDGE,
        DESIGN_EDGE,
        DECOMP_EDGE,
    ]
    assert (_bundle(run) / "20-design.md").read_text(encoding="utf-8") == revised
    assert capsys.readouterr().out.count(PAUSE_HEAD.format("дизайн")) == 2
    remarks = rp.all_remarks(state.run_dir(run.run_id))
    assert [(r.stage, r.text, r.applied) for r in remarks] == [
        ("design", "добавь логи", True)
    ]


def test_decomposition_remark_rechecks_edge_and_invariants(tmp_path) -> None:
    run = _run(tmp_path, ("decomposition",))
    revised = DECOMP + "\nПримечание: проверять вручную.\n"
    author = _author(revised)
    check = checker("PASS")
    read = _lines("добавь примечание", "", "ок")
    assert _advance(run, FakeOps(run), read=read, launch=author, check=check) == 0
    assert run.step == "code-pr"
    assert check.calls == [  # type: ignore[attr-defined]
        DESIGN_EDGE,
        DECOMP_EDGE,
        DECOMP_EDGE,
    ]


def test_removal_asked_and_restored_on_no(tmp_path, capsys) -> None:
    """RF-2: «нет» — файл возвращён, повтор с «D-02 не удалять»."""
    run = _run(tmp_path, ("design",))
    kept = DESIGN + "\nПоиск без учёта регистра.\n"
    author = _author(NO_D02, kept)
    read = _lines("убери поиск", "", "2", "дальше")
    assert _advance(run, FakeOps(run), read=read, launch=author) == 0
    assert run.step == "code-pr"
    first, second = author.prompts  # type: ignore[attr-defined]
    assert "D-02 не удалять" not in first
    assert f"--- ТЕКУЩИЙ УЗЕЛ ---\n{DESIGN}\n" in second  # restored before retry
    assert "- D-02 не удалять" in second
    assert "- замечание владельца: убери поиск" in second
    assert (_bundle(run) / "20-design.md").read_text(encoding="utf-8") == kept
    assert (
        "Удалить D-02 — «Поиск — построчный фильтр по подстроке»? 1. удалить · 2. оставить"
        in capsys.readouterr().out
    )


def test_removal_confirmed_is_accepted(tmp_path) -> None:
    run = _run(tmp_path, ("design",))
    author = _author(NO_D02)
    read = _lines("убери поиск", "", "1", "дальше")
    assert _advance(run, FakeOps(run), read=read, launch=author) == 0
    assert (_bundle(run) / "20-design.md").read_text(encoding="utf-8") == NO_D02
    assert len(author.prompts) == 1  # type: ignore[attr-defined]


def test_unclear_remark_not_replayed(tmp_path) -> None:
    """Повтор снова ослабляет — стоп, замечание погашено: следующий прогон
    не переигрывает его, а спрашивает новое."""
    run = _run(tmp_path, ("design",))
    author = _author(NO_D02, NO_D02)
    read = _lines("убери поиск", "", "нет")
    assert _advance(run, FakeOps(run), read=read, launch=author) == 1
    assert run.step == "design"
    assert (
        "замечание не применено: правка удаляла D-02 — сформулируйте иначе "
        "(dfl run)" in run.stop_reason
    )
    assert (_bundle(run) / "20-design.md").read_text(encoding="utf-8") == DESIGN
    rd = state.run_dir(run.run_id)
    assert rp.pending(rd, "design") == []
    assert [r.status for r in rp.all_remarks(rd)] == ["dropped"]
    assert "- дизайн: убери поиск" not in rp.pr_section(rd)
    assert "не применены (правка удаляла пункты): дизайн: убери поиск" in (
        rp.pr_section(rd)
    )

    author2 = _author()
    check = checker("PASS")
    read2 = _lines("дальше")
    assert _advance(run, FakeOps(run), read=read2, launch=author2, check=check) == 0
    assert author2.prompts == []  # type: ignore[attr-defined]
    assert read2.seen == ["> "]  # type: ignore[attr-defined]
    assert run.step == "code-pr"


def test_stop_at_removal_question_is_pause_without_retry(tmp_path) -> None:
    """`/стоп` на вопросе удаления — пауза (код 0), файл возвращён, без
    платного повтора; замечание остаётся недоигранным."""
    run = _run(tmp_path, ("design",))
    author = _author(NO_D02)
    read = _lines("убери поиск", "", "/стоп")
    assert _advance(run, FakeOps(run), read=read, launch=author) == 0
    assert len(author.prompts) == 1  # type: ignore[attr-defined]
    assert run.step == "design" and run.stop_reason.endswith(flow.RETRY)
    assert (_bundle(run) / "20-design.md").read_text(encoding="utf-8") == DESIGN
    rd = state.run_dir(run.run_id)
    assert [r.text for r in rp.pending(rd, "design")] == ["убери поиск"]


class _Crash(Exception):
    pass


def test_pending_remark_applied_on_resume_without_asking(tmp_path) -> None:
    """RF-1: падение после записи замечания — следующий прогон доигрывает
    круг по нему без вопроса и без повторной проверки до правки."""
    run = _run(tmp_path, ("design",))

    def crashing(call, prompt) -> LaunchOutcome:
        if "## Режим правки" in prompt("/tmp/out.md"):
            raise _Crash
        return _author()(call, prompt)

    with pytest.raises(_Crash):
        _advance(run, FakeOps(run), read=_lines("добавь логи", ""), launch=crashing)
    assert (_bundle(run) / "20-design.md").read_text(encoding="utf-8") == DESIGN
    rd = state.run_dir(run.run_id)
    assert [r.text for r in rp.pending(rd, "design")] == ["добавь логи"]

    revised = DESIGN + "\nЛоги.\n"
    author = _author(revised)
    check = checker("PASS")
    read = _lines("дальше")
    assert _advance(run, FakeOps(run), read=read, launch=author, check=check) == 0
    [prompt] = author.prompts  # type: ignore[attr-defined]
    assert "- замечание владельца: добавь логи" in prompt
    assert read.seen == ["> "]  # type: ignore[attr-defined]
    assert check.calls == [DESIGN_EDGE, DECOMP_EDGE]  # type: ignore[attr-defined]
    assert rp.pending(rd, "design") == []


def test_brief_remark_goes_to_interviewer(tmp_path, monkeypatch) -> None:
    run = _run(tmp_path, ("brief",))
    wider = replace(BRIEF, goals=(*BRIEF.goals, Goal("G-02", "ещё", "текст")))
    seen: list[list[dict]] = []

    def fake_interview(run_dir: Path, subject: str, *, read, agent):
        path = run_dir / TRANSCRIPT
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        seen.append([json.loads(x) for x in lines])
        return BRIEF if len(seen) == 1 else wider

    monkeypatch.setattr(flow, "interview", fake_interview)
    read = _lines("цель шире", "", "дальше")
    assert _advance(run, FakeOps(run), read=read) == 0
    assert run.step == "code-pr"
    assert seen[1][-1] == {"role": "human", "text": "поправить: цель шире"}
    rd = state.run_dir(run.run_id)
    assert brief_from_json(json.loads((rd / "brief.json").read_text())) == wider
    brief_md = (_bundle(run) / "00-brief.md").read_text(encoding="utf-8")
    assert brief_md == render_brief(wider)
    assert rp.pending(rd, "brief") == []


def test_brief_pause_resume_skips_interview(tmp_path, monkeypatch) -> None:
    run = _run(tmp_path, ("brief",))
    assert _advance(run, FakeOps(run), read=_lines()) == 0
    assert run.step == "interview" and run.stop_reason.endswith(flow.RETRY)

    def no_interview(*a, **k):
        raise AssertionError("brief is already confirmed")

    monkeypatch.setattr(flow, "interview", no_interview)
    assert _advance(run, FakeOps(run), read=_lines("дальше")) == 0
    assert run.step == "code-pr"


def test_brief_remark_interview_paused_keeps_remark(tmp_path, monkeypatch) -> None:
    run = _run(tmp_path, ("brief",))
    assert _advance(run, FakeOps(run), read=_lines()) == 0  # pause at brief
    monkeypatch.setattr(flow, "interview", lambda *a, **k: Halt.PAUSED)
    assert _advance(run, FakeOps(run), read=_lines("цель шире", "")) == 0
    rd = state.run_dir(run.run_id)
    assert [r.text for r in rp.pending(rd, "brief")] == ["цель шире"]
    # resume: the correction is not appended twice
    monkeypatch.setattr(flow, "interview", lambda *a, **k: BRIEF)
    assert _advance(run, FakeOps(run), read=_lines("дальше")) == 0
    log = (rd / TRANSCRIPT).read_text(encoding="utf-8").splitlines()
    assert (
        log.count(
            json.dumps(
                {"role": "human", "text": "поправить: цель шире"}, ensure_ascii=False
            )
        )
        == 1
    )
    assert rp.pending(rd, "brief") == []


def test_stop_on_pause_is_code_0(tmp_path, capsys) -> None:
    run = _run(tmp_path, ("design",))
    check = checker("PASS")
    assert _advance(run, FakeOps(run), read=_lines("/стоп"), check=check) == 0
    assert run.step == "design"
    assert "дизайн" in run.stop_reason and run.stop_reason.endswith(flow.RETRY)
    assert state.load(run.run_id).stop_reason == run.stop_reason
    # the next run shows the same link again without a new paid check
    check2 = checker("PASS")
    capsys.readouterr()
    assert _advance(run, FakeOps(run), read=_lines("дальше"), check=check2) == 0
    assert check2.calls == [DECOMP_EDGE]  # type: ignore[attr-defined]
    out = capsys.readouterr().out
    assert f"file://{(_bundle(run) / '20-design.md').resolve()}" in out


def test_changed_file_after_pause_is_rechecked(tmp_path) -> None:
    run = _run(tmp_path, ("design",))
    assert _advance(run, FakeOps(run), read=_lines()) == 0
    node = _bundle(run) / "20-design.md"
    node.write_text(DESIGN + "\nправка руками\n", encoding="utf-8")
    check = checker("PASS")
    assert _advance(run, FakeOps(run), read=_lines("дальше"), check=check) == 0
    assert check.calls == [DESIGN_EDGE, DECOMP_EDGE]  # type: ignore[attr-defined]


def test_no_tty_stops_with_link_hint(tmp_path) -> None:
    """Без терминала пауза ждёт человека — не сбой: код 0 (ruling Task 6)."""
    run = _run(tmp_path, ("design",))
    assert _advance(run, FakeOps(run), read=_no_read, tty=False) == 0
    assert run.step == "design"
    link = f"file://{(_bundle(run) / '20-design.md').resolve()}"
    assert "ждёт просмотра этапа «дизайн»" in run.stop_reason
    assert f"откройте {link}; замечания — dfl run в терминале" in run.stop_reason


class _BodyOps(FakeOps):
    def create_pr(self, target_dir, repo_slug, branch, title, body, label, **kw):
        self.body = body
        return super().create_pr(
            target_dir, repo_slug, branch, title, body, label, **kw
        )


def test_spec_pr_body_has_owner_remarks(tmp_path) -> None:
    run = _run(tmp_path, ("design",))
    ops = _BodyOps(run)
    author = _author(DESIGN + "\nЛоги.\n")
    read = _lines("добавь логи", "", "дальше")
    assert _advance(run, ops, read=read, launch=author) == 0
    assert run.spec_pr == SPEC_PR
    assert "## Замечания владельца\n\n- дизайн: добавь логи\n" in ops.body


def test_spec_pr_body_unchanged_without_remarks(tmp_path) -> None:
    run = _run(tmp_path, ())
    ops = _BodyOps(run)
    assert _advance(run, ops, read=_no_read) == 0
    assert "Замечания владельца" not in ops.body


def test_brief_reinterview_paused_after_answer_resumes_open_question(
    tmp_path, monkeypatch, capsys
) -> None:
    """Переинтервью прервано после ответа на вопрос: продолжение показывает
    открытый вопрос, правка в журнале одна."""
    run = _run(tmp_path, ("brief",))
    assert _advance(run, FakeOps(run), read=_lines()) == 0  # pause at brief
    monkeypatch.setattr(flow, "interview", interviewer.interview)
    turns = [json.dumps({"ask": "вопрос-один"}), json.dumps({"ask": "вопрос-два"})]

    def agent(prompt: str) -> str:
        return turns.pop(0) if turns else json.dumps({"brief": asdict(BRIEF)})

    read = _lines("цель шире", "", "ответ один", "")
    assert _advance(run, FakeOps(run), read=read, agent=agent) == 0
    rd = state.run_dir(run.run_id)
    assert [r.text for r in rp.pending(rd, "brief")] == ["цель шире"]
    capsys.readouterr()
    read2 = _lines("ответ два", "", "1", "дальше")
    assert _advance(run, FakeOps(run), read=read2, agent=agent) == 0
    assert "вопрос-два" in capsys.readouterr().out
    assert run.step == "code-pr"
    entries = [
        json.loads(x)
        for x in (rd / TRANSCRIPT).read_text(encoding="utf-8").splitlines()
    ]
    fix = {"role": "human", "text": "поправить: цель шире"}
    assert entries.count(fix) == 1
    texts = [e["text"] for e in entries]
    assert texts.index("ответ два") == texts.index("вопрос-два") + 1
    assert rp.pending(rd, "brief") == []


def test_brief_correction_not_duplicated_on_plain_resume(tmp_path) -> None:
    rd = tmp_path / "rd"
    interviewer.append_correction(rd, "цель шире")
    interviewer.append_correction(rd, "цель шире")
    lines = (rd / TRANSCRIPT).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_duplicate_id_rewrite_rejected_without_removal_question(
    tmp_path, capsys
) -> None:
    """Дубль id — не удаление: вопроса нет, правка отклонена, повтор с
    названной проблемой."""
    run = _run(tmp_path, ("design",))
    dup = DESIGN + "\n#### D-01: дубль\nтекст\n"
    fixed = DESIGN + "\nЛоги.\n"
    author = _author(dup, fixed)
    read = _lines("добавь логи", "", "дальше")
    assert _advance(run, FakeOps(run), read=read, launch=author) == 0
    assert "удалить" not in capsys.readouterr().out
    _, second = author.prompts  # type: ignore[attr-defined]
    assert "D-01" in second.split("--- РЕШЕНИЯ ЧЕЛОВЕКА ---")[1]
    assert f"--- ТЕКУЩИЙ УЗЕЛ ---\n{DESIGN}\n" in second
    assert (_bundle(run) / "20-design.md").read_text(encoding="utf-8") == fixed


def test_duplicate_id_retry_still_broken_is_unclear(tmp_path) -> None:
    run = _run(tmp_path, ("design",))
    dup = DESIGN + "\n#### D-01: дубль\nтекст\n"
    author = _author(dup, dup)
    read = _lines("добавь логи", "")
    assert _advance(run, FakeOps(run), read=read, launch=author) == 1
    assert "замечание не применено" in run.stop_reason and "D-01" in run.stop_reason
    assert (_bundle(run) / "20-design.md").read_text(encoding="utf-8") == DESIGN
    rd = state.run_dir(run.run_id)
    assert [r.status for r in rp.all_remarks(rd)] == ["dropped"]


def test_decomposition_remark_breaking_invariants_runs_fix_invariants(
    tmp_path,
) -> None:
    run = _run(tmp_path, ("decomposition",))
    broken = DECOMP.replace("**Критерии:** FR-02/1", "**Критерии:** FR-01/1")
    author = _author(broken, DECOMP)
    check = checker("PASS")
    read = _lines("перепиши задачи", "", "дальше")
    assert _advance(run, FakeOps(run), read=read, launch=author, check=check) == 0
    assert run.step == "code-pr"
    owner, invariants = author.prompts  # type: ignore[attr-defined]
    assert "- замечание владельца: перепиши задачи" in owner
    assert "- инвариант декомпозиции:" in invariants
    assert (_bundle(run) / "30-decomposition.md").read_text(encoding="utf-8") == DECOMP


def test_manual_edit_on_pause_rechecked_before_done(tmp_path, capsys) -> None:
    """Final review I1: ручная правка файла на экране паузы + «дальше» — узел
    проверяется заново и пауза показывается снова, а не уходит без проверки."""
    run = _run(tmp_path, ("design",))
    path = _bundle(run) / "20-design.md"
    edited = DESIGN + "\nПравка владельца руками.\n"
    queue = ["дальше", "дальше"]

    def read(prompt: str) -> str | None:
        if len(queue) == 2:
            path.write_text(edited, encoding="utf-8")
        return queue.pop(0) if queue else None

    check = checker("PASS")
    assert _advance(run, FakeOps(run), read=read, check=check) == 0
    assert run.step == "code-pr"
    assert check.calls == [  # type: ignore[attr-defined]
        DESIGN_EDGE,
        DESIGN_EDGE,
        DECOMP_EDGE,
    ]
    assert path.read_text(encoding="utf-8") == edited
    assert capsys.readouterr().out.count(PAUSE_HEAD.format("дизайн")) == 2


def test_paid_calls_line_printed_once(tmp_path, capsys) -> None:
    """Final review minor: строка платных вызовов — одна, в конце `advance`."""
    run = _run(tmp_path, ("design",))
    author = _author(DESIGN + "\nЛоги пишутся в stderr.\n")
    read = _lines("добавь логи", "", "дальше")
    assert _advance(run, FakeOps(run), read=read, launch=author) == 0
    assert capsys.readouterr().out.count("платных вызовов:") == 1


def test_brief_hand_edit_restored_and_explained(tmp_path, capsys) -> None:
    """Ruling on concern 2: ручная правка `00-brief.md` на паузе + «дальше» —
    файл возвращён к рендеру `brief.json`, подсказка, пауза снова, без
    платных вызовов на этапе брифа."""
    run = _run(tmp_path, ("brief",))
    path = _bundle(run) / "00-brief.md"
    queue = ["дальше", "дальше"]

    def read(prompt: str) -> str | None:
        if len(queue) == 2:
            path.write_text(render_brief(BRIEF) + "\nправка руками\n", "utf-8")
        return queue.pop(0) if queue else None

    check = checker("PASS")
    assert _advance(run, FakeOps(run), read=read, check=check) == 0
    assert run.step == "code-pr"
    assert path.read_text(encoding="utf-8") == render_brief(BRIEF)
    out = capsys.readouterr().out
    assert (
        "бриф правится словами: опишите правку как замечание — файл возвращён "
        "к согласованной версии"
    ) in out
    assert out.count(PAUSE_HEAD.format("бриф")) == 2
    assert check.calls == [DESIGN_EDGE, DECOMP_EDGE]  # type: ignore[attr-defined]
