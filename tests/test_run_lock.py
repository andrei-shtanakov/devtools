"""Блокировка прогона (§11.4.5): одна на вход, токен обязателен, наследуется соседом."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from governance import run_lock, runner, spec_loop
from governance import run_state as rs
from tests import locked_runner


@pytest.fixture()
def runs_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "runs"
    monkeypatch.setattr(rs, "RUNS_ROOT", root)
    return root


def test_second_lock_on_same_run_is_busy(runs_root) -> None:
    with run_lock.run_lock("r-1"):
        with pytest.raises(run_lock.LockBusy):
            with run_lock.run_lock("r-1"):
                pass
    with run_lock.run_lock("r-1"):  # отпущена — снова свободна
        pass


def test_other_run_is_independent(runs_root) -> None:
    with run_lock.run_lock("r-1"), run_lock.run_lock("r-2"):
        pass


def test_lock_file_lives_outside_runs_root(runs_root) -> None:
    with run_lock.run_lock("r-1") as lock:
        assert not (runs_root / "r-1").exists()
        assert run_lock.lock_path("r-1").parent.name == "runs-locks"
        assert lock.run_id == "r-1"


def test_require_checks_token_and_run(runs_root) -> None:
    with run_lock.run_lock("r-1") as lock:
        run_lock.require(lock, "r-1")
        with pytest.raises(ValueError):
            run_lock.require(lock, "r-2")
    with pytest.raises(TypeError):
        run_lock.require(None, "r-1")  # type: ignore[arg-type]


def test_runner_functions_refuse_without_token(runs_root) -> None:  # T37a
    with pytest.raises(TypeError):
        runner.resume("r-1", object())  # type: ignore[call-arg]
    with run_lock.run_lock("r-2") as other:
        with pytest.raises(ValueError):
            runner.resume("r-1", object(), lock=other)  # type: ignore[arg-type]


_HOLDER = """
import sys, time, subprocess
from pathlib import Path
from governance import run_lock, run_state as rs
rs.RUNS_ROOT = Path(sys.argv[1])
with run_lock.run_lock("r-1") as lock:
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys,time; print('ready', flush=True); time.sleep(30)"],
        pass_fds=(lock.fd,), stdout=subprocess.PIPE, text=True,
    )
    assert child.stdout.readline().strip() == "ready"
    print(child.pid, flush=True)
    time.sleep(30)
"""


def test_lock_survives_parent_death_via_pass_fds(runs_root, tmp_path) -> None:
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(runs_root)],
        stdout=subprocess.PIPE,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert holder.stdout is not None
    child_pid = int(holder.stdout.readline().strip())
    holder.kill()
    holder.wait()
    try:
        with pytest.raises(run_lock.LockBusy):  # ребёнок унаследовал описание
            with run_lock.run_lock("r-1"):
                pass
    finally:
        os.kill(child_pid, signal.SIGKILL)
    deadline = time.monotonic() + 5
    while True:
        try:
            with run_lock.run_lock("r-1"):
                break
        except run_lock.LockBusy:
            assert time.monotonic() < deadline, "блокировка не отпущена"
            time.sleep(0.05)


def test_other_children_do_not_inherit(runs_root) -> None:
    with run_lock.run_lock("r-1") as lock:
        done = subprocess.run(
            [sys.executable, "-c", f"import os; os.fstat({lock.fd})"],
            capture_output=True,
            check=False,
        )
    assert done.returncode != 0  # без pass_fds дескриптор у потомка закрыт


def _waiting_run(tmp_path: Path) -> str:
    from tests.test_governance_runner import FakeOps, _need_spec, _reply, _start_kwargs

    ops = FakeOps(discovery=[("start", _reply(20))])
    locked_runner.start(
        **_start_kwargs(tmp_path, "r-busy", ops), interview_spec=_need_spec()
    )
    return "r-busy"


def test_second_entry_exits_before_reading_state(  # T37, Review Focus
    tmp_path, runs_root, monkeypatch, capsys
) -> None:
    run_id = _waiting_run(tmp_path)
    state = rs.load(run_id)
    real_load = rs.load
    loads: list[str] = []

    def spy(rid: str) -> rs.RunState:
        loads.append(rid)
        return real_load(rid)

    monkeypatch.setattr(spec_loop, "find_runs", lambda repo, subject: [state])
    monkeypatch.setattr(rs, "load", spy)
    monkeypatch.setattr(spec_loop, "build_interview_spec", lambda a, s: None)
    monkeypatch.setattr(
        spec_loop,
        "manifest_repo_entry",
        lambda text, repo: type(
            "E", (), {"repo_slug": "owner/alpha", "repo": "alpha"}
        )(),
    )
    monkeypatch.setattr(spec_loop, "MANIFEST_PATH", tmp_path / "m.toml")
    (tmp_path / "m.toml").write_text("", encoding="utf-8")
    with run_lock.run_lock(run_id):
        code = spec_loop.main(["--subject", state.subject, "--repo", "alpha"])
    assert code == 1
    assert "другим процессом" in capsys.readouterr().out
    assert loads == []  # под чужой блокировкой run.json не читался


def test_runner_cli_resume_exits_before_reading_state(  # T37 (CLI runner resume)
    tmp_path, runs_root, monkeypatch, capsys
) -> None:
    run_id = _waiting_run(tmp_path)
    before = (rs.run_dir(run_id) / "run.json").read_bytes()

    def no_load(rid: str) -> rs.RunState:
        raise AssertionError("run.json прочитан при занятой блокировке")

    monkeypatch.setattr(rs, "load", no_load)
    monkeypatch.setattr(runner, "load", no_load)
    with run_lock.run_lock(run_id):
        assert runner.main(["resume", "--run-id", run_id]) == 1
    assert "другим процессом" in capsys.readouterr().err
    assert (rs.run_dir(run_id) / "run.json").read_bytes() == before


def test_state_is_reread_under_lock(tmp_path, runs_root, monkeypatch) -> None:  # T37b
    run_id = _waiting_run(tmp_path)
    stale = rs.load(run_id)
    fresh = rs.load(run_id)
    fresh.status = "stopped_interview"
    rs.save(fresh)
    seen: list[str] = []
    monkeypatch.setattr(spec_loop, "find_runs", lambda repo, subject: [stale])
    monkeypatch.setattr(spec_loop, "build_interview_spec", lambda a, s: None)
    monkeypatch.setattr(
        spec_loop,
        "manifest_repo_entry",
        lambda text, repo: type(
            "E", (), {"repo_slug": "owner/alpha", "repo": "alpha"}
        )(),
    )
    monkeypatch.setattr(spec_loop, "MANIFEST_PATH", tmp_path / "m.toml")
    (tmp_path / "m.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        spec_loop, "_origin_url", lambda d: "git@github.com:owner/alpha.git"
    )
    monkeypatch.setattr(spec_loop, "_real_ops", lambda: object())
    monkeypatch.setattr(
        spec_loop, "_dispatch", lambda st, ops, lock: seen.append(st.status) or 0
    )
    assert spec_loop.main(["--subject", stale.subject, "--repo", "alpha"]) == 0
    assert seen == ["stopped_interview"]


# --- гарантии файла блокировки (§11.4.5, уточнение ревизии 7) ---


def test_lock_file_is_not_removed_and_keeps_its_inode(runs_root) -> None:
    with run_lock.run_lock("r-1"):
        inode = run_lock.lock_path("r-1").stat().st_ino
    assert run_lock.lock_path("r-1").exists()  # освобождение файл не удаляет
    with run_lock.run_lock("r-1"):
        assert run_lock.lock_path("r-1").stat().st_ino == inode


def test_crash_between_lock_and_reservation_leaves_no_ledger(runs_root) -> None:
    with pytest.raises(RuntimeError):
        with run_lock.run_lock("r-crash"):
            raise RuntimeError("гибель до _reserve_run_id")
    assert rs.all_run_ids() == []  # каталога прогона нет — битого леджера нет
    assert spec_loop.find_runs("alpha", "s") == []
    with run_lock.run_lock("r-crash") as lock:  # повторный запуск проходит
        assert lock.run_id == "r-crash"


def test_every_entry_uses_the_one_lock_path(runs_root, monkeypatch, tmp_path) -> None:
    opened: list[str] = []
    real_open = os.open

    def spy(path, *args, **kwargs):
        opened.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(run_lock.os, "open", spy)
    with run_lock.run_lock("r-path"):
        pass
    assert opened == [str(run_lock.lock_path("r-path"))]
    for module in (spec_loop, runner):
        assert "lock_path" not in vars(module)  # своих путей у входов нет
