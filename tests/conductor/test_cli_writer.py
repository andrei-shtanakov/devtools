"""CLI: init-state и run --config (срез 1, часть A: пустой план)."""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import conductor.__main__ as cli
from conductor.app_calls import AppCalls, init_host, journal_path
from conductor.gh_write import Mutation
from conductor.inputs import save_inputs
from conductor.opstate import OpState, init_state
from conductor.roadmap import parse_roadmap
from conductor.writer import PlanRecord, Step
from tests.conductor.fake_app import FakeClient
from tests.conductor.fixtures import EPICS, ROADMAP, inputs

CONFIG = """
[app]
app_id = 11
installation_id = 22
private_key = "/k.pem"
[run]
profile = "fleet"
shadow = {shadow}
state_dir = "{state}"
lock = "{lock}"
"""


def _config(tmp_path: Path, name: str = "state", shadow: bool = True) -> str:
    return CONFIG.format(
        state=tmp_path / name,
        lock=tmp_path / "lock" / "conductor.lock",
        shadow=str(shadow).lower(),
    )


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cli, "SHARED_DIR", tmp_path / "shared")
    client = FakeClient()
    monkeypatch.setattr(cli, "AppClient", lambda cfg, calls: client)
    cfg = tmp_path / "c.toml"
    cfg.write_text(_config(tmp_path), encoding="utf-8")
    rep = tmp_path / "inputs.json"
    save_inputs(inputs({"a": "- [ ] x @owner:TBD @id:x\n"}), rep)
    return tmp_path, cfg, rep


def _snap(out: Path) -> dict:
    run_dir = next(out.iterdir())
    return json.loads((run_dir / "snapshot.json").read_text(encoding="utf-8"))


def test_init_state_once(env) -> None:
    tmp, cfg, _ = env
    assert cli.main(["init-state", "--config", str(cfg)]) == 0
    assert (tmp / "state" / "INIT").is_file()
    assert (tmp / "shared" / "HOST_INIT").is_file()
    assert cli.main(["init-state", "--config", str(cfg)]) == 4  # не пуст


def test_recover_after_lost_init(env) -> None:
    tmp, cfg, _ = env
    assert cli.main(["init-state", "--config", str(cfg)]) == 0
    (tmp / "state" / "INIT").unlink()
    assert cli.main(["init-state", "--config", str(cfg), "--recover"]) == 0
    assert (tmp / "state" / "corrupt").is_dir()


def test_run_with_config_empty_plan(env) -> None:
    tmp, cfg, rep = env
    assert cli.main(["init-state", "--config", str(cfg)]) == 0
    out = tmp / "out"
    code = cli.main(
        ["run", "--replay", str(rep), "--out", str(out), "--config", str(cfg)]
    )
    assert code == 0
    snap = _snap(out)
    assert snap["writer"]["is_writer"] is True and snap["actions"]["journal"] == []


def test_run_without_init_is_degraded_but_snapshot_written(env) -> None:
    tmp, cfg, rep = env
    out = tmp / "out"
    code = cli.main(
        ["run", "--replay", str(rep), "--out", str(out), "--config", str(cfg)]
    )
    assert code == 4
    snap = _snap(out)
    assert snap["writer"]["is_writer"] is False
    assert "OPSTATE-UNINITIALIZED" in snap["writer"]["reason"]


def test_run_with_bad_config(env) -> None:
    tmp, cfg, rep = env
    cfg.write_text("x = [", encoding="utf-8")
    out = tmp / "out"
    code = cli.main(
        ["run", "--replay", str(rep), "--out", str(out), "--config", str(cfg)]
    )
    assert code == 4 and "CFG-INVALID" in _snap(out)["writer"]["reason"]


def test_init_state_requires_config() -> None:
    assert cli.main(["init-state"]) == 2


def _hold_lock(tmp_path: Path) -> subprocess.Popen:
    """Другой процесс держит lock хоста (как таймер во время ручного запуска)."""
    lock = tmp_path / "lock" / "conductor.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    code = (
        "import fcntl, sys\n"
        f"h = open({str(lock)!r}, 'a')\n"
        "fcntl.flock(h.fileno(), fcntl.LOCK_EX)\n"
        "print('held', flush=True)\n"
        "sys.stdin.read()\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None and proc.stdout.readline().strip() == "held"
    return proc


def test_run_and_init_state_take_host_lock(env) -> None:
    """Регрессия P1-3: ручной run и init-state не идут при занятом lock."""
    tmp, cfg, rep = env
    holder = _hold_lock(tmp)
    try:
        assert cli.main(["init-state", "--config", str(cfg)]) == 4
        assert not (tmp / "state").exists()
        out = tmp / "out"
        argv = ["run", "--replay", str(rep), "--out", str(out), "--config", str(cfg)]
        assert cli.main(argv) == 0
        assert not out.exists()  # прогон пропущен целиком: ни снимка, ни записей
        assert cli.main(["init-state", "--config", str(cfg), "--recover"]) == 4
    finally:
        holder.communicate("")
    assert cli.main(["init-state", "--config", str(cfg)]) == 0


def test_lock_is_released_after_run(env) -> None:
    tmp, cfg, rep = env
    assert cli.main(["init-state", "--config", str(cfg)]) == 0
    out = tmp / "out"
    argv = ["run", "--replay", str(rep), "--out", str(out), "--config", str(cfg)]
    assert cli.main(argv) == 0
    with cli.host_lock(tmp / "lock" / "conductor.lock") as held:
        assert held


def _live_world(env, monkeypatch, shadow: bool = False):
    """Состояние без карантина, роадмап разрешает nudge, хост — писатель."""
    tmp, cfg, rep = env
    cfg.write_text(_config(tmp, shadow=shadow), encoding="utf-8")
    past = datetime.now(UTC) - timedelta(hours=3)
    init_state(tmp / "state", past)
    init_host(tmp / "shared", 11, 22, past)
    text = ROADMAP.replace("autonomy = 0", 'autonomy = 1\nenabled_actions = ["nudge"]')
    monkeypatch.setattr(cli, "_origin_roadmap", lambda *a: parse_roadmap(text, EPICS))
    monkeypatch.setattr(cli.socket, "gethostname", lambda: "vps")
    step = Step(Mutation("comment", "own/a", 1, text="x"), "k")
    record = PlanRecord("nudge", "own/a#1", "r", 1, (step,))
    monkeypatch.setattr(cli, "plan_records", lambda *a: [record])
    return tmp, cfg, rep


def test_replay_never_writes_live(env, monkeypatch) -> None:
    """Регрессия P1-2: --replay — потолок 0, даже с --level 3 и не в тени."""
    tmp, cfg, rep = _live_world(env, monkeypatch)
    out = tmp / "out"
    argv = ["run", "--replay", str(rep), "--out", str(out), "--config", str(cfg)]
    cli.main([*argv, "--level", "3"])
    snap = _snap(out)
    assert snap["writer"]["level_cap"] == 0
    assert [j["reason"] for j in snap["actions"]["journal"]] == ["run_level"]
    assert cli.AppClient(None, None).sent == []


def test_live_cap_twin_writes(env, monkeypatch) -> None:
    """Двойник: тот же мир с потолком 3 пишет — запрет выше дал именно потолок."""
    tmp, cfg, rep = _live_world(env, monkeypatch)
    monkeypatch.setattr(cli, "level_cap", lambda args, inputs: 3)
    out = tmp / "out"
    argv = ["run", "--replay", str(rep), "--out", str(out), "--config", str(cfg)]
    assert cli.main(argv) == 0
    assert [j["outcome"] for j in _snap(out)["actions"]["journal"]] == ["success"]
    assert len(cli.AppClient(None, None).sent) == 1


def test_level_cap_rules() -> None:
    args = cli._parser().parse_args(["run", "--level", "3"])
    live = inputs({"a": ""})
    assert cli.level_cap(args, live) == 3
    local = inputs({"a": ""}, roadmap_source="file:/tmp/r.toml")
    assert cli.level_cap(args, local) == 0
    replay = cli._parser().parse_args(["run", "--level", "3", "--replay", "x"])
    assert cli.level_cap(replay, live) == 0
    default = cli._parser().parse_args(["run"])
    assert cli.level_cap(default, live) == 0


def test_init_state_of_new_profile_keeps_lost_ban(env) -> None:
    """Регрессия P2-5: потерянный общий журнал не «чистится» init-state."""
    tmp, cfg, _ = env
    assert cli.main(["init-state", "--config", str(cfg)]) == 0
    shared = tmp / "shared"
    calls, _ = AppCalls.open(shared, 11, 22, datetime.now(UTC))
    assert calls is not None
    seq = calls.begin("create", datetime.now(UTC))  # обрыв: begin без end
    assert calls.blocked_until() is not None and seq
    journal_path(shared, 11, 22).unlink()  # журнал утрачен
    acc = tmp / "acc.toml"
    acc.write_text(_config(tmp, name="acc-state"), encoding="utf-8")
    assert cli.main(["init-state", "--config", str(acc)]) == 0
    reopened, _ = AppCalls.open(shared, 11, 22, datetime.now(UTC))
    assert reopened is not None
    until = reopened.blocked_until()
    assert until is not None and until > datetime.now(UTC) + timedelta(minutes=55)
    assert [r["t"] for r in reopened.rows] == ["lost"]


def test_opstate_write_failure_is_finding_and_exit_4(env, monkeypatch) -> None:
    """Ревью #538: общий отзыв по OPSTATE-WRITE — находка в снимке и код 4
    (§4.6), а не «успешный» прогон."""
    tmp, cfg, rep = _live_world(env, monkeypatch)
    monkeypatch.setattr(cli, "level_cap", lambda args, inputs: 3)

    def boom(self, **_: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(OpState, "begin_attempt", boom)
    out = tmp / "out"
    argv = ["run", "--replay", str(rep), "--out", str(out), "--config", str(cfg)]
    assert cli.main(argv) == 4
    snap = _snap(out)
    assert "OPSTATE-WRITE" in snap["writer"]["findings"]
    assert [j["reason"] for j in snap["actions"]["journal"]] == ["OPSTATE-WRITE"]
