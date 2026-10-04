"""Исполнитель записей: порядок проверок §5.1 и матрица исходов §5.2."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from conductor.gh_app import CallResult
from conductor.gh_write import Mutation
from conductor.host_config import HostConfig
from conductor.http import Response
from conductor.journal import MutationLog, RunJournal, log_complete
from conductor.opstate import init_state, open_state
from conductor.roadmap import parse_roadmap
from conductor.writer import PlanRecord, Step, StopPoint, Writer, effect_key
from tests.conductor.fake_app import FakeClient, ok
from tests.conductor.fixtures import EPICS, ROADMAP

T_INIT = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
FENCE = frozenset({"own/a", "own/ai-orchestrators-workspace"})
QUEUE = "own/ai-orchestrators-workspace"
CFG = HostConfig(
    app_id=11,
    installation_id=22,
    private_key=Path("/k.pem"),
    profile="fleet",
    shadow=False,
    state_dir=Path("/s"),
)
COMMENTS = ("POST", "/repos/own/a/issues/1/comments")


def roadmap(
    autonomy: int = 1,
    enabled: tuple[str, ...] = ("nudge", "close_shipped"),
    since: str = "2026-09-29T00:00:00Z",
    max_writes: int = 20,
):
    text = (
        ROADMAP.replace(
            "autonomy = 0",
            f"autonomy = {autonomy}\nenabled_actions = {json.dumps(list(enabled))}",
        ).replace('writer_since = "2026-09-29T00:00:00Z"', f'writer_since = "{since}"')
        + f"[limits]\nmax_writes_per_run = {max_writes}\n"
    )
    rm = parse_roadmap(text, EPICS)
    assert rm.valid, rm.findings
    return rm


@pytest.fixture
def w(tmp_path: Path):
    init_state(tmp_path / "state", T_INIT)
    state = open_state(tmp_path / "state", NOW).state
    assert state is not None
    return SimpleNamespace(tmp=tmp_path, state=state, client=FakeClient())


def writer(
    w, cfg=CFG, rms=None, partial=False, hostname="vps", at=NOW, run="r1", cap=3
):
    seq = iter(rms) if rms is not None else None
    return Writer(
        cfg=cfg,
        client=w.client,
        state=w.state,
        log=MutationLog(w.tmp / run),
        journal=RunJournal(w.tmp / run),
        fence=FENCE,
        load_roadmap=(lambda: next(seq)) if seq is not None else roadmap,
        hostname=hostname,
        run_id=run,
        level_cap=cap,
        partial=partial,
        clock=lambda: at,
    )


def comment(n: int = 1, key: str = "k1", repo: str = "own/a") -> Step:
    return Step(Mutation("comment", repo, n, text="hi"), key)


def close(n: int = 1) -> Step:
    return Step(Mutation("close", "own/a", n), "close")


def rec(
    *steps: Step,
    action: str = "close_shipped",
    valid: bool = True,
    authority=None,
    done_before: int = 0,
) -> PlanRecord:
    reason = None if valid else "доказательство изменилось"
    extra = {"authority": authority} if authority is not None else {}
    return PlanRecord(
        action,
        "own/a#1",
        "rev1",
        1,
        steps,
        revalidate=lambda m: reason,
        done_before=done_before,
        **extra,
    )


def outcomes(reports) -> list[str]:
    return [r.outcome for r in reports]


def test_happy_path_and_complete_log(w) -> None:
    reports = writer(w).execute([rec(comment(), close())])
    assert outcomes(reports) == ["success", "success"]
    assert [s[0] for s in w.client.sent] == ["POST", "PATCH"]
    assert log_complete(w.tmp / "r1" / "calls.jsonl")


def test_fence_before_coverage(w) -> None:
    w.client.cover["own/outside"] = False
    reports = writer(w).execute([rec(comment(repo="own/outside"))])
    assert outcomes(reports) == ["fence"] and w.client.covers_calls == []


def test_quarantine_stops_all(w) -> None:
    reports = writer(w, at=T_INIT + timedelta(minutes=10)).execute(
        [rec(comment()), rec(comment(2, "k2"))]
    )
    assert outcomes(reports) == ["revoked_all", "revoked_all"] and w.client.sent == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"rms": [None]},
        {"hostname": "other"},
        {"rms": [roadmap(since="2026-10-01T11:30:00Z")]},
        {"rms": [roadmap(autonomy=0)]},
        {"partial": True},
    ],
)
def test_general_revocation(w, kwargs) -> None:
    reports = writer(w, **kwargs).execute([rec(comment(), close())])
    assert outcomes(reports) == ["revoked_all", "revoked_all"] and w.client.sent == []


def test_key_and_block_revoke_all(w) -> None:
    w.client.key_ok = False
    assert outcomes(writer(w).execute([rec(comment())])) == ["revoked_all"]
    w.client.key_ok, w.client.blocked = True, True
    assert outcomes(writer(w, run="r2").execute([rec(comment())])) == ["revoked_all"]
    assert w.client.sent == []


def test_action_revocation_keeps_other_actions(w) -> None:
    rm = roadmap(enabled=("nudge",))
    reports = writer(w, rms=[rm, rm, rm]).execute(
        [rec(close()), rec(close(3)), rec(comment(2, "k2"), action="nudge")]
    )
    assert outcomes(reports) == ["revoked_action", "revoked_action", "success"]


def test_mid_run_revocation_partial(w) -> None:
    wr = writer(w, rms=[roadmap(), roadmap(enabled=("nudge",))])
    reports = wr.execute([rec(comment(), close())])
    assert outcomes(reports) == ["success", "revoked_action"]
    # §7.6: шаг 1 сделан этим же прогоном — шаг 2 отозван посреди записи
    assert wr.notes == [
        {
            "action": "close_shipped",
            "subject": "own/a#1",
            "status": "частично: шаг 2 (close) — отозван (enabled_actions)",
        }
    ]


def test_partial_note_when_earlier_step_done_in_prior_run(w) -> None:
    """§7.6/§7.5: план несёт только шаг 2 — шаг 1 уже сделан прежним прогоном."""
    rm = roadmap(enabled=("nudge",))
    wr = writer(w, rms=[rm])
    reports = wr.execute([rec(close(), done_before=1)])
    assert outcomes(reports) == ["revoked_action"]
    assert wr.notes == [
        {
            "action": "close_shipped",
            "subject": "own/a#1",
            "status": "частично: шаг 2 (close) — отозван (enabled_actions)",
        }
    ]


def test_no_partial_note_without_earlier_done_step(w) -> None:
    """Двойник: ни один шаг не сделан — частичности нет, запись просто отозвана."""
    rm = roadmap(enabled=("nudge",))
    wr = writer(w, rms=[rm])
    reports = wr.execute([rec(close())])
    assert outcomes(reports) == ["revoked_action"]
    assert wr.notes == []


def test_partial_note_for_removed_mid_sequence(w) -> None:
    """§7.6: сбой шага 8 (ревалидация) посреди записи — тоже частично."""
    record = PlanRecord(
        "close_shipped",
        "own/a#1",
        "rev1",
        1,
        (comment(), close()),
        revalidate=lambda m: "ответ изменился" if m.op == "close" else None,
    )
    wr = writer(w)
    reports = wr.execute([record])
    assert outcomes(reports) == ["success", "removed"]
    assert wr.notes == [
        {
            "action": "close_shipped",
            "subject": "own/a#1",
            "status": "частично: шаг 2 (close) — основание снято (ответ изменился)",
        }
    ]


def test_uncovered_not_sent_and_dependent_skipped(w) -> None:
    w.client.cover["own/a"] = False
    reports = writer(w).execute([rec(comment(), close())])
    assert outcomes(reports) == ["not_sent", "skipped_dependent"]
    assert reports[0].reason == "ID-REPO-UNCOVERED" and w.client.sent == []


def test_evidence_changed_removed_others_continue(w) -> None:
    reports = writer(w).execute([rec(comment(), valid=False), rec(comment(2, "k2"))])
    assert outcomes(reports) == ["removed", "success"]


def test_target_moved_removed(w) -> None:
    w.client.override[("GET", "/repos/own/a/issues/1")] = CallResult(
        "moved", Response(301, {}, b"")
    )
    reports = writer(w).execute([rec(comment())])
    assert outcomes(reports) == ["removed"] and reports[0].reason == "TARGET-MOVED"
    assert w.client.sent == []


def test_delay_by_effect_key_and_after_hour(w) -> None:
    step = comment()
    w.state.begin_attempt(
        attempt_id="old",
        mutation_id="m-old",
        effect_key=effect_key(step, step.mutation),
        target="own/a#1",
        marker_key="k1",
        action="close_shipped",
        subject="own/a#1",
        now=NOW - timedelta(minutes=10),
    )
    assert outcomes(writer(w).execute([rec(step)])) == ["delay"]
    assert w.client.sent == []
    later = writer(w, at=NOW + timedelta(minutes=51), run="r2")
    assert outcomes(later.execute([rec(step)])) == ["success"]


def test_budget_counts_sent_attempts(w) -> None:
    reports = writer(w, rms=[roadmap(max_writes=1)] * 2).execute(
        [rec(comment()), rec(comment(2, "k2"))]
    )
    assert outcomes(reports) == ["success", "revoked_all"]
    assert reports[1].reason == "RUN-BUDGET"


def test_shadow_with_autonomy_zero(w) -> None:
    shadow = HostConfig(**{**CFG.__dict__, "shadow": True})
    [report] = writer(w, cfg=shadow, rms=[roadmap(autonomy=0)]).execute(
        [rec(comment())]
    )
    assert (report.outcome, report.allowed, report.reason) == (
        "shadow",
        False,
        "autonomy",
    )
    assert w.client.sent == []


def test_shadow_with_permissive_roadmap(w) -> None:
    shadow = HostConfig(**{**CFG.__dict__, "shadow": True})
    reports = writer(w, cfg=shadow).execute([rec(comment(), close())])
    assert outcomes(reports) == ["shadow", "shadow"]
    assert all(r.allowed for r in reports) and w.client.sent == []


def test_failed_blocks_dependent_and_counts_series(w) -> None:
    w.client.override[COMMENTS] = CallResult("failed", Response(422, {}, b"{}"))
    reports = writer(w).execute([rec(comment(), close()), rec(comment(2, "k2"))])
    assert outcomes(reports) == ["failed", "skipped_dependent", "success"]
    assert [s["count"] for s in w.state.series.values()] == [1]


def test_uncertain_then_immediate_rerun_is_delayed(w) -> None:
    w.client.override[COMMENTS] = ok(
        {"body": "other", "user": {"login": "conductor[bot]"}}
    )
    assert outcomes(writer(w).execute([rec(comment())])) == ["uncertain"]
    del w.client.override[COMMENTS]
    rerun = writer(w, at=NOW + timedelta(minutes=5), run="r2")
    assert outcomes(rerun.execute([rec(comment())])) == ["delay"]


def test_rate_limited_stops_all(w) -> None:
    w.client.override[COMMENTS] = CallResult("rate_limited", Response(429, {}, b"{}"))
    reports = writer(w).execute([rec(comment()), rec(comment(2, "k2"))])
    assert outcomes(reports) == ["revoked_all", "revoked_all"]


def test_create_then_pin_and_comment_use_new_number(w) -> None:
    steps = (
        Step(Mutation("create", QUEUE, text="body", title="Очередь"), "owner-queue"),
        Step(Mutation("pin", QUEUE), "pin"),
        Step(Mutation("comment", QUEUE, text="q1"), "q1"),
    )
    rms = [roadmap(enabled=("owner_queue",))] * 3
    reports = writer(w, rms=rms).execute(
        [PlanRecord("owner_queue", QUEUE, "p1", 1, steps)]
    )
    assert outcomes(reports) == ["success"] * 3
    assert [p for _, p, _ in w.client.sent] == [
        f"/repos/{QUEUE}/issues",
        "/graphql",
        f"/repos/{QUEUE}/issues/101/comments",
    ]


def test_opstate_write_failure_sends_nothing(w, monkeypatch) -> None:
    def boom(**_: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(w.state, "begin_attempt", boom)
    reports = writer(w).execute([rec(comment())])
    assert outcomes(reports) == ["revoked_all"] and reports[0].reason == "OPSTATE-WRITE"
    assert w.client.sent == []


@pytest.mark.parametrize(("point", "sent"), [("after_intent", 0), ("after_send", 1)])
def test_stop_points_leave_attempt_in_flight(w, point: str, sent: int) -> None:
    acc = HostConfig(
        **{
            **CFG.__dict__,
            "profile": "acceptance",
            "sandbox": "own/a",
            "stop_points": frozenset({point}),
        }
    )
    with pytest.raises(StopPoint):
        writer(w, cfg=acc).execute([rec(comment())])
    assert len(w.client.sent) == sent
    assert [a["status"] for a in w.state.attempts.values()] == ["in_flight"]
    assert not log_complete(w.tmp / "r1" / "calls.jsonl")


def test_stop_points_ignored_in_fleet(w) -> None:
    fleet = HostConfig(**{**CFG.__dict__, "stop_points": frozenset({"after_send"})})
    assert outcomes(writer(w, cfg=fleet).execute([rec(comment())])) == ["success"]


def focus_level(rm, run_level: int) -> int:
    """Уровень позиции фокуса eco.focus1 (О §2.4) по переданному роадмапу."""
    focus = rm.focus_of("eco.focus1")
    return min(run_level, focus.autonomy) if focus is not None else 0


def with_focus_autonomy(rm_text_autonomy: int, focus_autonomy: int):
    text = ROADMAP.replace(
        "autonomy = 0",
        f'autonomy = {rm_text_autonomy}\nenabled_actions = ["nudge", "close_shipped"]',
    ).replace(
        'epic = "eco.focus1"', f'epic = "eco.focus1"\nautonomy = {focus_autonomy}'
    )
    rm = parse_roadmap(text, EPICS)
    assert rm.valid, rm.findings
    return rm


@pytest.mark.parametrize("cap", [0])
def test_cli_level_zero_means_no_mutations(w, cap: int) -> None:
    """Регрессия P1-2: потолок CLI (--level 0, --roadmap, --replay) — 0."""
    reports = writer(w, cap=cap).execute([rec(comment(), close())])
    assert outcomes(reports) == ["revoked_all", "revoked_all"]
    assert reports[0].reason == "run_level" and w.client.sent == []


def test_focus_autonomy_zero_blocks_position(w) -> None:
    """Регрессия P1-2: global autonomy=1, focus.autonomy=0 → позиции 0."""
    rm = with_focus_autonomy(1, 0)
    reports = writer(w, rms=[rm, rm]).execute(
        [rec(comment(), authority=focus_level), rec(comment(2, "k2"))]
    )
    assert outcomes(reports) == ["revoked_action", "success"]
    assert reports[0].reason == "position_level"
    assert [p for _, p, _ in w.client.sent] == ["/repos/own/a/issues/2/comments"]


def test_focus_revoked_between_steps(w) -> None:
    """Отзыв фокуса (focus.autonomy → 0) между шагами снимает второй шаг."""
    rms = [with_focus_autonomy(1, 1), with_focus_autonomy(1, 0)]
    reports = writer(w, rms=rms).execute(
        [rec(comment(), close(), authority=focus_level)]
    )
    assert outcomes(reports) == ["success", "revoked_action"]
    assert [s[0] for s in w.client.sent] == ["POST"]


def test_shadow_names_cli_ceiling(w) -> None:
    shadow = HostConfig(**{**CFG.__dict__, "shadow": True})
    [report] = writer(w, cfg=shadow, cap=0).execute([rec(comment())])
    assert (report.outcome, report.allowed, report.reason) == (
        "shadow",
        False,
        "run_level",
    )


def test_revalidate_sees_resolved_step_and_runs_before_each_step(w) -> None:
    """Регрессия P1-1: основание перепроверяется перед КАЖДЫМ шагом."""
    seen: list[str] = []

    def check(m: Mutation) -> str | None:
        seen.append(m.op)
        return "ответ изменился" if m.op == "close" else None

    record = PlanRecord(
        "close_shipped", "own/a#1", "rev1", 1, (comment(), close()), revalidate=check
    )
    reports = writer(w).execute([record])
    assert outcomes(reports) == ["success", "removed"]
    assert reports[1].reason == "ответ изменился" and seen == ["comment", "close"]
    assert [s[0] for s in w.client.sent] == ["POST"]


def test_unconfirmed_effect_is_uncertain_and_blocks_dependent(w) -> None:
    """Регрессия P2-1: 2xx, но контрольное чтение эффекта не видит."""
    w.client.apply = False
    reports = writer(w).execute([rec(comment(), close())])
    assert outcomes(reports) == ["uncertain", "skipped_dependent"]
    assert reports[0].reason == "комментарий не подтверждён чтением"
    assert [a["status"] for a in w.state.attempts.values()] == ["uncertain"]


def test_control_read_failure_is_uncertain(w) -> None:
    w.client.override[("GET", "/repos/own/a/issues/comments/1001")] = CallResult(
        "uncertain", None
    )
    reports = writer(w).execute([rec(comment(), close())])
    assert outcomes(reports) == ["uncertain", "skipped_dependent"]


def queue_record(body_step: Step) -> PlanRecord:
    steps = (
        body_step,
        Step(Mutation("pin", QUEUE, 5), "pin", "pin", optional=True),
        Step(Mutation("comment", QUEUE, 5, text="q1"), "q1", revision="q1"),
        Step(Mutation("comment", QUEUE, 5, text="q2"), "q2", revision="q2"),
    )
    return PlanRecord("owner_queue", QUEUE, "p1", 1, steps)


@pytest.mark.parametrize("outcome", ["failed", "uncertain"])
def test_queue_body_failure_blocks_questions(w, outcome: str) -> None:
    """Регрессия P2-2: сбой тела очереди — вопросы этого плана не пишутся."""
    path = f"/repos/{QUEUE}/issues/5"
    w.client.override[("PATCH", path)] = CallResult(outcome, Response(500, {}, b""))
    body = Step(Mutation("body", QUEUE, 5, text="b"), "body", revision="p1")
    rms = [roadmap(enabled=("owner_queue",))] * 4
    reports = writer(w, rms=rms).execute([queue_record(body)])
    assert outcomes(reports) == [outcome] + ["skipped_dependent"] * 3
    assert [s[1] for s in w.client.sent] == [path]


def test_queue_pin_failure_does_not_block_questions(w) -> None:
    w.client.override[("POST", "/graphql")] = CallResult(
        "failed", Response(422, {}, b"{}")
    )
    body = Step(Mutation("body", QUEUE, 5, text="b"), "body", revision="p1")
    rms = [roadmap(enabled=("owner_queue",))] * 4
    reports = writer(w, rms=rms).execute([queue_record(body)])
    assert outcomes(reports) == ["success", "failed", "success", "success"]


def test_question_series_keyed_by_step_revision(w) -> None:
    """Вопросы одной записи — разные мутации: серия у каждого своя (§5.4)."""
    w.client.override[("POST", f"/repos/{QUEUE}/issues/5/comments")] = CallResult(
        "failed", Response(422, {}, b"{}")
    )
    steps = (
        Step(Mutation("comment", QUEUE, 5, text="q1"), "q1", revision="q1"),
        Step(Mutation("comment", QUEUE, 5, text="q2"), "q2", revision="q2"),
    )
    rec_a = PlanRecord("owner_queue", QUEUE, "p1", 1, steps)
    rec_b = PlanRecord("owner_queue", QUEUE, "p2", 1, steps)  # проекция сменилась
    rms = [roadmap(enabled=("owner_queue",))] * 4
    writer(w, rms=rms[:2]).execute([rec_a])
    writer(w, rms=rms[2:], run="r2").execute([rec_b])
    assert sorted(s["count"] for s in w.state.series.values()) == [2]


def test_position_sees_run_level_min_of_cap_and_autonomy(w) -> None:
    """О §2.4: run_level = min(--level, autonomy) — его получает уровень позиции."""
    seen: list[int] = []

    def spy(rm, run_level: int) -> int:
        seen.append(run_level)
        return run_level

    writer(w, rms=[roadmap(autonomy=3)], cap=2).execute([rec(comment(), authority=spy)])
    writer(w, rms=[roadmap(autonomy=1)], cap=3, run="r2").execute(
        [rec(comment(2, "k2"), authority=spy)]
    )
    assert seen == [2, 1]
