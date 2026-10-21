"""Tests for df.journal: audit journal, writer lock, generation counter."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from df.journal import (
    NeedsHuman,
    Stable,
    Step,
    StepFailed,
    Transitional,
    generation_begin,
    generation_end,
    journal_event,
    read_consistent,
    read_journal,
    run_steps,
    writer_lock,
)
from df.paths import Layout

REPO = Path(__file__).resolve().parents[2]


def _layout(tmp_path: Path) -> Layout:
    return Layout(tmp_path.resolve())


class Box:
    """Tiny model of external facts."""

    def __init__(self, *done: str) -> None:
        self.facts = set(done)
        self.calls: list[str] = []

    def step(self, name: str, *, effective: bool = True) -> Step:
        def do() -> None:
            self.calls.append(name)
            if effective:
                self.facts.add(name)

        return Step(name, lambda: name in self.facts, do)


def _events(journal: Path) -> list[tuple[str, str]]:
    return [(r["step"], r["event"]) for r in read_journal(journal)]  # type: ignore


def test_rerun_after_interrupt_resumes_by_fact(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    box = Box()
    steps = [box.step("a"), box.step("b"), box.step("c")]
    with pytest.raises(Exception) as info:
        run_steps(steps, journal, run_id="r1", fail_after="b")
    assert type(info.value).__name__ == "_Interrupted"
    assert box.facts == {"a", "b"}
    box.calls.clear()
    run_steps(steps, journal, run_id="r2")
    assert box.calls == ["c"]
    second = [
        (r["step"], r["event"]) for r in read_journal(journal) if r["run_id"] == "r2"
    ]
    assert second == [
        ("a", "skip"),
        ("b", "skip"),
        ("c", "start"),
        ("c", "done"),
    ]


def test_journal_record_alone_does_not_skip(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    journal_event(journal, run_id="old", step="a", event="done")
    box = Box()
    run_steps([box.step("a")], journal, run_id="new")
    assert box.calls == ["a"]


def test_postcheck_false_fails(tmp_path: Path) -> None:
    box = Box()
    with pytest.raises(StepFailed) as info:
        run_steps([box.step("a", effective=False)], tmp_path / "j", run_id="r")
    assert info.value.step == "a"
    assert "done" not in [e for _, e in _events(tmp_path / "j")]


def test_needs_human_stops_without_done(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"

    def do() -> None:
        raise NeedsHuman("run the thing")

    steps = [Step("h", lambda: False, do), Box().step("z")]
    with pytest.raises(NeedsHuman) as info:
        run_steps(steps, journal, run_id="r")
    assert info.value.instruction == "run the thing"
    assert _events(journal) == [("h", "start")]


def test_journal_format_and_mode(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    journal_event(journal, run_id="r", step="s", event="start", detail="d")
    row = json.loads(journal.read_text())
    assert set(row) == {"ts", "run_id", "step", "event", "detail"}
    assert row["ts"].endswith("+00:00") or row["ts"].endswith("Z")
    assert (journal.stat().st_mode & 0o777) == 0o600


def test_journal_refuses_symlink(tmp_path: Path) -> None:
    target = tmp_path / "t"
    target.write_text("")
    link = tmp_path / "j"
    link.symlink_to(target)
    with pytest.raises(OSError):
        journal_event(link, run_id="r", step="s", event="e")


def test_read_journal_malformed_line_named(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    journal_event(journal, run_id="r", step="s", event="e")
    with journal.open("a") as fh:
        fh.write("not json\n")
    with pytest.raises(ValueError, match="line 2"):
        read_journal(journal)


def test_read_journal_absent_is_empty(tmp_path: Path) -> None:
    assert read_journal(tmp_path / "nope") == []


HOLDER = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from df.journal import writer_lock
    from df.paths import Layout

    with writer_lock(Layout(Path(sys.argv[1]))):
        print("held", flush=True)
        sys.stdin.read()
    """
)


def test_writer_lock_second_process_refused(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    proc = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(lay.root)],
        cwd=REPO,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "held"
        with pytest.raises(StepFailed) as info:
            with writer_lock(lay):
                pass
        assert info.value.step == "lock"
        assert "другая административная операция" in info.value.detail
    finally:
        assert proc.stdin is not None
        proc.stdin.close()
        proc.wait(timeout=10)
    with writer_lock(lay) as fd:
        assert isinstance(fd, int)


def test_writer_lock_creates_dirs_and_file(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    with writer_lock(lay):
        pass
    assert lay.install_dir.is_dir()
    assert (lay.lock.stat().st_mode & 0o777) == 0o600
    assert (lay.install_dir.stat().st_mode & 0o777) == 0o755


def test_writer_lock_closes_fd(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    with writer_lock(lay) as fd:
        pass
    with pytest.raises(OSError):
        os.fstat(fd)


def test_writer_lock_refuses_symlinked_ancestor(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "var").symlink_to(elsewhere)
    with pytest.raises(StepFailed) as info:
        with writer_lock(lay):
            pass
    assert info.value.step == "lock"
    assert list(elsewhere.iterdir()) == []


def test_writer_lock_refuses_symlinked_lockfile(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    lay.install_dir.mkdir(parents=True)
    (tmp_path / "t").write_text("")
    lay.lock.symlink_to(tmp_path / "t")
    with pytest.raises(StepFailed) as info:
        with writer_lock(lay):
            pass
    assert info.value.step == "lock"


def test_writer_lock_refuses_non_regular_lockfile(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    lay.install_dir.mkdir(parents=True)
    lay.lock.mkdir()
    with pytest.raises(StepFailed) as info:
        with writer_lock(lay):
            pass
    assert info.value.step == "lock"


def _gen(lay: Layout) -> str:
    return lay.generation.read_text()


def test_generation_parity(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    lay.install_dir.mkdir(parents=True)
    generation_begin(lay)
    assert _gen(lay) == "1\n"
    generation_begin(lay)
    assert _gen(lay) == "1\n"
    generation_end(lay)
    assert _gen(lay) == "2\n"
    assert (lay.generation.stat().st_mode & 0o777) == 0o644


def test_generation_end_on_even_is_programming_error(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    lay.install_dir.mkdir(parents=True)
    with pytest.raises(RuntimeError):
        generation_end(lay)


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"\n",
        b"01\n",
        b"-1\n",
        b"+1\n",
        b"1\n\n",
        b" 1\n",
        b"1 \n",
        b"x\n",
        b"\xd9\xa1\n",
        b"1" * 100,
        b"00",
    ],
)
def test_generation_malformed_rejected(tmp_path: Path, content: bytes) -> None:
    lay = _layout(tmp_path)
    lay.install_dir.mkdir(parents=True)
    lay.generation.write_bytes(content)
    with pytest.raises(StepFailed) as info:
        read_consistent(lay, lambda: 1)
    assert info.value.step == "snapshot"


@pytest.mark.parametrize("content", [b"0", b"0\n", b"12", b"12\n"])
def test_generation_canonical_accepted(tmp_path: Path, content: bytes) -> None:
    lay = _layout(tmp_path)
    lay.install_dir.mkdir(parents=True)
    lay.generation.write_bytes(content)
    n = int(content)
    result = read_consistent(lay, lambda: "v")
    assert result == (Stable("v", n) if n % 2 == 0 else Transitional(n))


def test_generation_symlink_and_dir_rejected(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    lay.install_dir.mkdir(parents=True)
    (tmp_path / "t").write_text("2\n")
    lay.generation.symlink_to(tmp_path / "t")
    with pytest.raises(StepFailed):
        read_consistent(lay, lambda: 1)
    lay.generation.unlink()
    lay.generation.mkdir()
    with pytest.raises(StepFailed):
        read_consistent(lay, lambda: 1)


def test_reader_absent_generation_is_zero(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    assert read_consistent(lay, lambda: "x") == Stable("x", 0)


def _bump(lay: Layout) -> None:
    generation_begin(lay)
    generation_end(lay)


def test_reader_rejects_changed_generation(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    lay.install_dir.mkdir(parents=True)
    calls: list[int] = []

    def read() -> int:
        calls.append(1)
        _bump(lay)
        return len(calls)

    with pytest.raises(StepFailed) as info:
        read_consistent(lay, read)
    assert info.value.step == "snapshot"
    assert info.value.detail == "состояние меняется"
    assert len(calls) == 3


def test_reader_retries_then_succeeds(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    lay.install_dir.mkdir(parents=True)
    calls: list[int] = []

    def read() -> int:
        calls.append(1)
        if len(calls) == 1:
            _bump(lay)
        return len(calls)

    assert read_consistent(lay, read) == Stable(2, 2)


def test_reader_aba_not_accepted(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    lay.install_dir.mkdir(parents=True)
    state = {"v": "A"}

    def read() -> str:
        seen = state["v"]
        generation_begin(lay)
        state["v"] = "B"
        generation_end(lay)
        generation_begin(lay)
        state["v"] = "A"
        generation_end(lay)
        return seen

    with pytest.raises(StepFailed):
        read_consistent(lay, read, attempts=1)


def test_reader_odd_generation_is_transitional(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    lay.install_dir.mkdir(parents=True)
    generation_begin(lay)
    called: list[int] = []
    result = read_consistent(lay, lambda: called.append(1))
    assert result == Transitional(1)
    assert called == []


READER = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from df.journal import read_consistent
    from df.paths import Layout

    lay = Layout(Path(sys.argv[1]))
    opened = []

    def hook(event, args):
        if event == "open":
            opened.append(str(args[0]))

    sys.addaudithook(hook)
    read_consistent(lay, lambda: 1)
    sys.addaudithook(lambda *a: None)
    if any(p == str(lay.lock) for p in opened):
        print("OPENED-LOCK")
        sys.exit(1)
    if not any(p == str(lay.generation) for p in opened):
        print("NO-GENERATION-READ")
        sys.exit(1)
    """
)


def test_reader_never_opens_lock(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    lay.install_dir.mkdir(parents=True)
    lay.generation.write_text("2\n")
    lay.lock.write_text("")
    done = subprocess.run(
        [sys.executable, "-c", READER, str(lay.root)],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stdout + done.stderr
