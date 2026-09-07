"""Тесты операторской кнопки `make spec-loop` (governance.spec_loop).

Кнопка — тонкая маршрутизация над runner/task_bridge по состоянию
`run.json`: ни одного платного вызова здесь нет — runner.start/resume и
deliver_for_run подменяются, проверяется ТОЛЬКО деривация значений,
поиск прогона и выбор действия (fail-closed на любой неоднозначности).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from governance import run_state as rs
from governance import spec_loop


@pytest.fixture()
def runs_root(tmp_path: Path, monkeypatch):
    root = tmp_path / "runs"
    monkeypatch.setattr(rs, "RUNS_ROOT", root)
    return root


MANIFEST = """\
schema_version = "0.3.0"

[cores.alpha]
repo_url = "git@github.com:owner/alpha.git"
git_dir  = "alpha"

[cores.beta]
repo_url = "https://github.com/owner/beta.git"
git_dir  = "beta"

[tools.beta-dist]
repo_url = "https://github.com/owner/beta.git"
git_dir  = "beta"

[cores.gamma-a]
repo_url = "git@github.com:owner/gamma-one.git"
git_dir  = "gamma"

[tools.gamma-b]
repo_url = "git@github.com:owner/gamma-two.git"
git_dir  = "gamma"
"""


# --- деривации -------------------------------------------------------------


def test_slug_from_subject_ascii() -> None:
    assert spec_loop.slug_from_subject("Fleet Inbox: v2 (MVP)") == "fleet-inbox-v2-mvp"


def test_slug_from_subject_empty_fails_closed() -> None:
    with pytest.raises(spec_loop.SpecLoopError, match="--ws-id"):
        spec_loop.slug_from_subject("Только кириллица")


def test_ws_id_carries_date_only_at_creation() -> None:
    assert (
        spec_loop.ws_id_for("Fleet Inbox", date(2026, 9, 7))
        == "fleet-inbox-20260907"
    )


def test_repo_slug_from_url_forms() -> None:
    assert spec_loop.repo_slug_from_url("git@github.com:o/n.git") == "o/n"
    assert spec_loop.repo_slug_from_url("https://github.com/o/n.git") == "o/n"
    assert spec_loop.repo_slug_from_url("ssh://git@github.com/o/n") == "o/n"


def test_manifest_single_match() -> None:
    entry = spec_loop.manifest_repo_entry(MANIFEST, "alpha")
    assert entry.repo == "alpha"
    assert entry.repo_slug == "owner/alpha"


def test_manifest_shared_git_dir_same_url_is_one_repo() -> None:
    entry = spec_loop.manifest_repo_entry(MANIFEST, "beta")
    assert entry.repo_slug == "owner/beta"


def test_manifest_zero_matches_fails_closed() -> None:
    with pytest.raises(spec_loop.SpecLoopError, match="не найден"):
        spec_loop.manifest_repo_entry(MANIFEST, "nope")


def test_manifest_conflicting_urls_fail_closed() -> None:
    with pytest.raises(spec_loop.SpecLoopError, match="gamma"):
        spec_loop.manifest_repo_entry(MANIFEST, "gamma")


# --- поиск прогона ---------------------------------------------------------


def _mk_run(run_id: str, subject: str, repo: str = "alpha", **kw) -> rs.RunState:
    state = rs.new_run(
        subject=subject,
        repo=repo,
        repo_slug="owner/alpha",
        ws_id=kw.get("ws_id", f"{run_id}-ws"),
        target_dir=kw.get("target_dir", "/tmp/alpha"),
        bundle_dir="workstreams/x/spec",
        profile="profiles/team-exp.yaml",
        run_id=run_id,
    )
    state.status = kw.get("status", "running")
    rs.save(state)
    return state


def test_find_runs_exact_repo_subject(runs_root) -> None:
    _mk_run("r1", "Subject A")
    _mk_run("r2", "Subject B")
    _mk_run("r3", "Subject A", repo="beta")
    found = spec_loop.find_runs("alpha", "Subject A")
    assert [s.run_id for s in found] == ["r1"]


def test_find_runs_broken_ledger_fails_closed(runs_root) -> None:
    _mk_run("r1", "Subject A")
    bad = runs_root / "r-broken"
    bad.mkdir(parents=True)
    (bad / "run.json").write_text("{не json", encoding="utf-8")
    with pytest.raises(spec_loop.SpecLoopError, match="r-broken"):
        spec_loop.find_runs("alpha", "Subject A")


# --- CLI: жёсткий merge_authority ------------------------------------------


def test_cli_rejects_merge_authority_override(capsys) -> None:
    with pytest.raises(SystemExit):
        spec_loop.main(
            ["--subject", "s", "--repo", "alpha", "--merge-authority", "human"]
        )
    assert "merge-authority" in capsys.readouterr().err


# --- маршрутизация ---------------------------------------------------------


class _LoopEnv:
    """Подмены runner.start/resume и deliver_for_run с записью вызовов."""

    def __init__(self, monkeypatch, tmp_path: Path, resume_result=None):
        self.calls: list[tuple] = []
        self.resume_result = resume_result
        target = tmp_path / "alpha"
        (target / ".git").mkdir(parents=True)
        self.target = target
        monkeypatch.setattr(
            spec_loop, "MANIFEST_PATH", tmp_path / "manifest.toml"
        )
        (tmp_path / "manifest.toml").write_text(MANIFEST, encoding="utf-8")
        monkeypatch.setattr(spec_loop, "WORKSPACE_ROOT", tmp_path)
        monkeypatch.setattr(
            spec_loop, "_origin_url", lambda d: "git@github.com:owner/alpha.git"
        )
        monkeypatch.setattr(spec_loop.runner, "start", self._start)
        monkeypatch.setattr(spec_loop.runner, "resume", self._resume)
        monkeypatch.setattr(
            spec_loop.task_bridge, "deliver_for_run", self._deliver
        )
        monkeypatch.setattr(spec_loop, "_real_ops", lambda: object())

    def _start(self, **kwargs):
        self.calls.append(("start", kwargs))
        state = rs.new_run(
            subject=kwargs["subject"],
            repo=kwargs["repo"],
            repo_slug=kwargs["repo_slug"],
            ws_id=kwargs["ws_id"],
            target_dir=kwargs["target_dir"],
            bundle_dir=kwargs["bundle_dir"],
            profile=kwargs["profile"],
            run_id=kwargs["run_id"],
            merge_authority=kwargs["merge_authority"],
        )
        state.status = "waiting_human_merge"
        return state

    def _resume(self, run_id, ops):
        self.calls.append(("resume", run_id))
        return self.resume_result

    def _deliver(self, state, ops, legacy_bundle=None):
        self.calls.append(("deliver", state.run_id))
        return 42


def test_no_run_starts_with_human_authority_and_prints_values(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    rc = spec_loop.main(["--subject", "Fleet Inbox", "--repo", "alpha"])
    assert rc == 0
    kinds = [c[0] for c in env.calls]
    assert kinds == ["start"]
    kwargs = env.calls[0][1]
    assert kwargs["merge_authority"] == "human"
    assert kwargs["ws_id"].startswith("fleet-inbox-")
    assert kwargs["run_id"].startswith(kwargs["ws_id"])
    out = capsys.readouterr().out
    # Таблица разрешённых значений печатается ДО действия и несёт run-id.
    assert kwargs["run_id"] in out
    assert "owner/alpha" in out
    assert "waiting_human_merge" in out


def test_repeat_finds_run_without_date_and_resumes(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    """Повтор ищет по (repo, subject), а не по ws-id с сегодняшней датой."""
    state = _mk_run(
        "fleet-inbox-20260901-abc123", "Fleet Inbox",
        ws_id="fleet-inbox-20260901", status="waiting_human_merge",
        target_dir=str(tmp_path / "alpha"),
    )
    env = _LoopEnv(monkeypatch, tmp_path, resume_result=state)
    rc = spec_loop.main(["--subject", "Fleet Inbox", "--repo", "alpha"])
    assert rc == 0
    assert env.calls == [("resume", "fleet-inbox-20260901-abc123")]
    assert "ждём" in capsys.readouterr().out


def test_resume_completed_delivers_and_stops_at_approve(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    state = _mk_run(
        "r-w", "S", status="waiting_human_merge",
        target_dir=str(tmp_path / "alpha"),
    )
    merged = rs.load("r-w")
    merged.status = "completed"
    env = _LoopEnv(monkeypatch, tmp_path, resume_result=merged)
    rc = spec_loop.main(["--subject", "S", "--repo", "alpha"])
    assert rc == 0
    assert [c[0] for c in env.calls] == ["resume", "deliver"]
    assert "approve" in capsys.readouterr().out


def test_completed_run_goes_straight_to_deliver(
    runs_root, tmp_path, monkeypatch
) -> None:
    _mk_run("r-c", "S", status="completed", target_dir=str(tmp_path / "alpha"))
    env = _LoopEnv(monkeypatch, tmp_path)
    rc = spec_loop.main(["--subject", "S", "--repo", "alpha"])
    assert rc == 0
    assert [c[0] for c in env.calls] == ["deliver"]


@pytest.mark.parametrize(
    "status", ["stopped_gate", "merged_unverified", "running", "что-то"]
)
def test_non_continuable_statuses_report_without_calls(
    runs_root, tmp_path, monkeypatch, capsys, status
) -> None:
    """Автоматически продолжается ТОЛЬКО waiting_human_merge/completed."""
    _mk_run("r-s", "S", status=status, target_dir=str(tmp_path / "alpha"))
    env = _LoopEnv(monkeypatch, tmp_path)
    rc = spec_loop.main(["--subject", "S", "--repo", "alpha"])
    assert rc == 1
    assert env.calls == []
    assert status in capsys.readouterr().out


def test_resume_landing_on_stopped_reports_nonzero(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    state = _mk_run(
        "r-w2", "S", status="waiting_human_merge",
        target_dir=str(tmp_path / "alpha"),
    )
    after = rs.load("r-w2")
    after.status = "merged_unverified"
    env = _LoopEnv(monkeypatch, tmp_path, resume_result=after)
    rc = spec_loop.main(["--subject", "S", "--repo", "alpha"])
    assert rc == 1
    assert [c[0] for c in env.calls] == ["resume"]
    assert "merged_unverified" in capsys.readouterr().out


def test_multiple_matches_fail_closed_with_candidates(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    _mk_run("r-a", "S", target_dir=str(tmp_path / "alpha"))
    _mk_run("r-b", "S", target_dir=str(tmp_path / "alpha"))
    env = _LoopEnv(monkeypatch, tmp_path)
    rc = spec_loop.main(["--subject", "S", "--repo", "alpha"])
    assert rc == 1
    assert env.calls == []
    out = capsys.readouterr().out
    assert "r-a" in out and "r-b" in out and "--run-id" in out


def test_run_id_override_with_subject_mismatch_fails(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    _mk_run("r-x", "Другой subject", target_dir=str(tmp_path / "alpha"))
    env = _LoopEnv(monkeypatch, tmp_path)
    rc = spec_loop.main(
        ["--subject", "S", "--repo", "alpha", "--run-id", "r-x"]
    )
    assert rc == 1
    assert env.calls == []
    assert "subject" in capsys.readouterr().out


def test_ws_id_collision_with_other_subject_fails_closed(
    runs_root, tmp_path, monkeypatch, capsys,
) -> None:
    today = date.today().strftime("%Y%m%d")
    _mk_run(
        "r-coll", "Другой subject", ws_id=f"s-{today}",
        target_dir=str(tmp_path / "alpha"),
    )
    env = _LoopEnv(monkeypatch, tmp_path)
    rc = spec_loop.main(["--subject", "s", "--repo", "alpha"])
    assert rc == 1
    assert env.calls == []
    assert "ws-id" in capsys.readouterr().out.lower()


def test_origin_mismatch_fails_closed(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    monkeypatch.setattr(
        spec_loop, "_origin_url", lambda d: "git@github.com:other/fork.git"
    )
    rc = spec_loop.main(["--subject", "S", "--repo", "alpha"])
    assert rc == 1
    assert env.calls == []
    assert "origin" in capsys.readouterr().out
