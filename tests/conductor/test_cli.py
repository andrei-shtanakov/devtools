import json
from pathlib import Path

from conductor.__main__ import main
from conductor.inputs import save_inputs
from tests.conductor.fixtures import ROADMAP, inputs

TODOS = {
    "a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 @blocked_by:todo://b/b\n",
    "b": "- [ ] b @owner:TBD @id:b @epic:eco.bg\n",
}


def _replay(tmp: Path, todos=TODOS, **kw) -> Path:
    path = tmp / "inputs.json"
    save_inputs(inputs(todos, **kw), path)
    return path


def test_status_why_plan_from_replay_without_manifest_file(
    tmp_path: Path, capsys
) -> None:
    rep = _replay(tmp_path)
    assert main(["status", "--replay", str(rep)]) == 0
    out = capsys.readouterr().out
    assert "todo://b/b" in out and "вопросы владельцу" in out
    assert main(["why", "todo://b/b", "--replay", str(rep)]) == 0
    out = capsys.readouterr().out
    assert "todo://a/goal" in out and "need=implement" in out
    assert main(["plan", "--level", "3", "--replay", str(rep)]) == 0
    assert "план на уровне 0" in capsys.readouterr().out  # autonomy = 0
    rep3 = _replay(tmp_path, roadmap=ROADMAP.replace("autonomy = 0", "autonomy = 3"))
    assert main(["plan", "--level", "3", "--replay", str(rep3)]) == 0
    assert "план на уровне 3" in capsys.readouterr().out


def test_run_writes_snapshot_and_inputs(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert (
        main(
            [
                "run",
                "--replay",
                str(_replay(tmp_path)),
                "--out",
                str(out),
                "--level",
                "3",
            ]
        )
        == 0
    )
    run_dir = next(out.iterdir())
    snap = json.loads((run_dir / "snapshot.json").read_text(encoding="utf-8"))
    assert snap["run_level"] == 0 and snap["actions"]["journal"] == []
    assert (run_dir / "inputs.json").is_file()


def test_invalid_roadmap_exit_4_for_every_command(tmp_path: Path) -> None:
    rep, out = _replay(tmp_path, roadmap="x = ["), tmp_path / "out"
    assert main(["run", "--replay", str(rep), "--out", str(out)]) == 4
    assert (next(out.iterdir()) / "snapshot.json").is_file()
    assert main(["status", "--replay", str(rep)]) == 4
    assert main(["record", str(tmp_path / "rec"), "--replay", str(rep)]) == 4
    assert (tmp_path / "rec" / "inputs.json").is_file()


def test_status_prints_reason_specific_questions(tmp_path: Path, capsys) -> None:
    todos = {
        "a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 "
        "@blocked_by:todo://b/gone\n",
        "b": "- [ ] y @owner:TBD @id:y\n",
    }
    rep = _replay(tmp_path, todos, history={"todo://b/gone": "deadbeef"})
    assert main(["status", "--replay", str(rep)]) == 0
    out = capsys.readouterr().out
    assert "вопросы владельцу: 1" in out and "cancelled — drop-wait" in out


def test_gh_error_still_exit_0(tmp_path: Path, capsys) -> None:
    assert main(["status", "--replay", str(_replay(tmp_path, gh_state="error"))]) == 0
    assert "partial" in capsys.readouterr().out


def test_missing_manifest_exit_3(tmp_path: Path) -> None:
    assert main(["status", "--manifest", str(tmp_path / "nope.toml")]) == 3


def test_bad_args_exit_2() -> None:
    assert main(["frobnicate"]) == 2
    assert main(["why"]) == 2


def test_selftest() -> None:
    assert main(["--selftest"]) == 0


# ревью рубежа 3 (2026-09-30)


def test_corrupt_previous_snapshot_does_not_stop_the_run(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    (out / "2026-01-01T000000Z").mkdir(parents=True)
    (out / "2026-01-01T000000Z" / "snapshot.json").write_text("{", encoding="utf-8")
    rep = _replay(tmp_path)
    assert main(["run", "--replay", str(rep), "--out", str(out)]) == 0
    written = json.loads(
        next(p for p in out.glob("*-replay/snapshot.json")).read_text("utf-8")
    )
    assert written["changes_since_previous"] == {"first_run": True}
    assert not list(out.rglob("*.tmp"))


def test_old_runs_are_pruned(tmp_path: Path) -> None:
    from conductor.__main__ import KEEP_RUNS

    out = tmp_path / "runs"
    for k in range(KEEP_RUNS + 5):
        run = out / f"2026-01-01T{k:06d}Z"
        run.mkdir(parents=True)
        (run / "snapshot.json").write_text("{}", encoding="utf-8")
    assert main(["run", "--replay", str(_replay(tmp_path)), "--out", str(out)]) == 0
    assert len([d for d in out.iterdir() if d.is_dir()]) == KEEP_RUNS


def test_replay_honours_an_explicit_roadmap(tmp_path: Path) -> None:
    draft = tmp_path / "roadmap.toml"
    draft.write_text("schema_version = 99\n", encoding="utf-8")
    rep = _replay(tmp_path)
    assert main(["status", "--replay", str(rep), "--roadmap", str(draft)]) == 4


def test_why_canonicalizes_old_names_and_refuses_unknown_nodes(
    tmp_path: Path, capsys
) -> None:
    rep = _replay(tmp_path, {"ecosystem-kb": "- [ ] z @owner:github:own @id:z\n"})
    assert main(["why", "todo://prograph-vault/z", "--replay", str(rep)]) == 0
    assert capsys.readouterr().out.startswith("todo://ecosystem-kb/z")
    assert main(["why", "todo://a/typo", "--replay", str(rep)]) == 2


# долг среза 0 (devtools#511, кластер D)


def test_status_prints_warning_findings(tmp_path: Path, capsys) -> None:
    # п. 17: опечатка в @blocked_by видна не только как « [условие]»
    todos = {"a": "- [ ] g @owner:github:own @id:goal @blocked_by:spec-runner-rel\n"}
    assert main(["status", "--replay", str(_replay(tmp_path, todos))]) == 0
    out = capsys.readouterr().out
    assert "GR-BLOCKER-UNRESOLVABLE todo://a/goal: spec-runner-rel" in out


def test_missing_or_broken_replay_exit_3_with_message(tmp_path: Path, capsys) -> None:
    # п. 18: не трассировка и не код 1
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    wrong = tmp_path / "wrong.json"
    wrong.write_text('{"version": 1, "todos": [], "x": 1}', encoding="utf-8")
    for path in (tmp_path / "nope.json", broken, wrong):
        assert main(["status", "--replay", str(path)]) == 3, path
        err = capsys.readouterr().err
        assert "входы не прочитаны" in err and len(err.strip().splitlines()) == 1


def test_broken_manifest_exit_3_with_message(tmp_path: Path, capsys) -> None:
    bad_toml, no_owner = tmp_path / "bad.toml", tmp_path / "noowner.toml"
    bad_toml.write_text("[cores.a\n", encoding="utf-8")
    no_owner.write_text('[cores.a]\ngit_dir = "a"\n', encoding="utf-8")
    for path in (bad_toml, no_owner):
        argv = ["status", "--no-fetch", "--root", str(tmp_path), "--manifest"]
        assert main([*argv, str(path)]) == 3, path
        assert "входы не прочитаны" in capsys.readouterr().err


def test_bad_args_print_usage(capsys) -> None:
    for argv in (["frobnicate"], ["why"], ["record"], ["--level", "9", "plan"]):
        assert main(argv) == 2, argv
        assert capsys.readouterr().err.startswith("usage: conductor"), argv


def test_replay_run_does_not_overwrite_the_recorded_run(tmp_path: Path) -> None:
    # п. 19: run_id не наследуется от записи — иначе её файлы перезаписаны
    out, rep = tmp_path / "runs", _replay(tmp_path)
    recorded = out / "2026-09-29T120000Z"
    recorded.mkdir(parents=True)
    (recorded / "snapshot.json").write_text('{"mine": 1}', encoding="utf-8")
    assert main(["run", "--replay", str(rep), "--out", str(out)]) == 0
    assert (recorded / "snapshot.json").read_text(encoding="utf-8") == '{"mine": 1}'
    fresh = [d for d in out.iterdir() if d != recorded]
    assert len(fresh) == 1 and fresh[0].name.endswith("-replay")
    snap = json.loads((fresh[0] / "snapshot.json").read_text(encoding="utf-8"))
    assert snap["run_id"] == fresh[0].name


def test_status_repo_is_canonicalised_and_unknown_refused(
    tmp_path: Path, capsys
) -> None:
    # п. 20: GitHub-имя → канонический ключ; незнакомое — код 2
    rep = _replay(tmp_path, {"ecosystem-kb": "- [ ] z @owner:github:own @id:z\n"})
    assert main(["status", "prograph-vault", "--replay", str(rep)]) == 0
    out = capsys.readouterr().out
    assert "все позиции ecosystem-kb:" in out and "todo://ecosystem-kb/z" in out
    assert main(["status", "no-such-repo", "--replay", str(rep)]) == 2
    assert "no-such-repo" in capsys.readouterr().err


def test_run_refuses_to_write_a_snapshot_violating_the_contract(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # п. 21: схема проверяется в _run, не только в тестах
    import conductor.__main__ as cli

    real = cli.to_snapshot

    def broken(*a, **kw):
        snap = real(*a, **kw)
        snap["run_level"] = 9
        return snap

    monkeypatch.setattr(cli, "to_snapshot", broken)
    out = tmp_path / "runs"
    assert main(["run", "--replay", str(_replay(tmp_path)), "--out", str(out)]) == 1
    assert not list(out.glob("*/snapshot.json"))
    assert list(out.glob("*/snapshot.invalid.json"))
    assert "SNAPSHOT-INVALID" in capsys.readouterr().err


def test_runs_cut_before_the_snapshot_are_pruned_too(tmp_path: Path) -> None:
    """#551: прогон, убитый таймаутом юнита до записи снимка, оставляет
    inputs.json — ротация считает и такой каталог."""
    from conductor.__main__ import KEEP_RUNS

    out = tmp_path / "runs"
    for k in range(KEEP_RUNS + 5):
        run = out / f"2026-01-01T{k:06d}Z"
        run.mkdir(parents=True)
        (run / "inputs.json").write_text("{}", encoding="utf-8")
    assert main(["run", "--replay", str(_replay(tmp_path)), "--out", str(out)]) == 0
    assert len([d for d in out.iterdir() if d.is_dir()]) == KEEP_RUNS


def test_invalid_runs_are_pruned_too(tmp_path: Path, monkeypatch) -> None:
    """Терм. ревью #550: прогон, отвергнутый по контракту, тоже занимает диск
    (inputs.json) — ротация учитывает его и на пути отказа."""
    import conductor.__main__ as cli

    real = cli.to_snapshot

    def broken(*a, **kw):
        snap = real(*a, **kw)
        snap["run_level"] = 9
        return snap

    monkeypatch.setattr(cli, "to_snapshot", broken)
    out = tmp_path / "runs"
    for k in range(cli.KEEP_RUNS + 5):
        run = out / f"2026-01-01T{k:06d}Z"
        run.mkdir(parents=True)
        (run / "snapshot.invalid.json").write_text("{}", encoding="utf-8")
    assert main(["run", "--replay", str(_replay(tmp_path)), "--out", str(out)]) == 1
    assert len([d for d in out.iterdir() if d.is_dir()]) == cli.KEEP_RUNS


def test_why_prints_wait_evidence(tmp_path: Path, capsys) -> None:
    # п. 22: свидетельство, возраст строки и последнее движение
    rep = _replay(
        tmp_path,
        wait_since={"todo://a/goal|todo://b/b": "2026-09-20T00:00:00Z"},
        movement={"todo://b/b": "2026-09-21T00:00:00Z"},
    )
    assert main(["why", "todo://a/goal", "--replay", str(rep)]) == 0
    out = capsys.readouterr().out
    assert "строка с 2026-09-20T00:00:00Z" in out
    assert "движение 2026-09-21T00:00:00Z" in out
