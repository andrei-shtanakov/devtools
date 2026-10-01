"""CLI среза 1: поиск эффектов до плана (регрессия P2-3), заметки, trigger."""

from datetime import UTC, datetime, timedelta

import conductor.__main__ as cli
from conductor.gh_write import Mutation
from conductor.writer import PlanRecord, Step
from tests.conductor.test_cli_writer import _live_world, _snap, env  # noqa: F401


def test_run_settles_found_effects(env, monkeypatch) -> None:  # noqa: F811
    """Регрессия P2-3: CLI ищет эффекты незавершённых попыток до плана."""
    tmp, cfg, rep = _live_world(env, monkeypatch)
    client = cli.AppClient(None, None)
    marker = "<!-- conductor:v1 q id=h1-0123456789abcdef -->"
    client.add_comment("own/a", 1, client.bot_login, f"вопрос\n{marker}")
    opened = cli.open_state(tmp / "state", datetime.now(UTC))
    assert opened.state is not None
    opened.state.begin_attempt(
        attempt_id="old",
        mutation_id="m",
        effect_key="e",
        target="own/a#1",
        marker_key=marker,
        action="owner_queue",
        subject="own/ws",
        now=datetime.now(UTC) - timedelta(minutes=5),
        op="comment",
        expected=marker,
    )
    seen: list[str] = []
    monkeypatch.setattr(
        cli,
        "plan_records",
        lambda result, inputs, ctx: (
            seen.append(ctx.state.attempts["old"]["status"]) or []
        ),
    )
    out = tmp / "out"
    argv = ["run", "--replay", str(rep), "--out", str(out), "--config", str(cfg)]
    assert cli.main(argv) == 0
    assert seen == ["ok"]
    assert {"settled": "own/a#1", "op": "comment"} in _snap(out)["writer"]["notes"]


def test_snapshot_carries_trigger_and_notes(env, monkeypatch) -> None:  # noqa: F811
    tmp, cfg, rep = _live_world(env, monkeypatch)
    monkeypatch.setattr(
        cli,
        "plan_records",
        lambda result, inputs, ctx: ctx.notes.append({"note": "x"}) or [],
    )
    out = tmp / "out"
    argv = ["run", "--replay", str(rep), "--out", str(out), "--config", str(cfg)]
    assert cli.main([*argv, "--trigger", "timer"]) == 0
    snap = _snap(out)
    assert snap["trigger"] == "timer" and {"note": "x"} in snap["writer"]["notes"]


def test_snapshot_writer_block_is_redacted_and_counts_executed(
    env,  # noqa: F811
    monkeypatch,
) -> None:
    """Ревью #538: блок writer и журнал действий в снимке проходят redact, как
    journal.jsonl; metrics.actions_executed — число успешных записей."""
    tmp, cfg, rep = _live_world(env, monkeypatch)
    monkeypatch.setattr(cli, "level_cap", lambda args, inputs: 3)
    secret = "ghs_" + "A" * 36
    monkeypatch.setattr(
        cli,
        "plan_records",
        lambda result, inputs, ctx: (
            ctx.notes.append({"note": secret})
            or [
                PlanRecord(
                    "nudge",
                    "own/a#1",
                    "r",
                    1,
                    (Step(Mutation("comment", "own/a", 1, text="x"), "k"),),
                )
            ]
        ),
    )
    out = tmp / "out"
    argv = ["run", "--replay", str(rep), "--out", str(out), "--config", str(cfg)]
    assert cli.main(argv) == 0
    text = (next(out.iterdir()) / "snapshot.json").read_text(encoding="utf-8")
    assert secret not in text and "[REDACTED]" in text
    assert _snap(out)["metrics"]["actions_executed"] == 1
