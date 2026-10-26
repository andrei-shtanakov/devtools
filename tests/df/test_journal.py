"""Tests for df.journal: audit journal, writer lock, generation counter."""

from __future__ import annotations

import json
import os
import select
import signal
import stat
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


def test_postcheck_false_names_the_reason(tmp_path: Path) -> None:
    """D3: a step's `why()` supplies the refusal's reasons; the type, the
    step name and the journal events stay those of a bare failure."""
    journal = tmp_path / "j"
    seen: list[str] = []

    def why() -> list[str]:
        seen.append("why")
        return ["uid 0", "чужой дом"]

    step = Step("a", lambda: False, lambda: None, why)
    with pytest.raises(StepFailed) as info:
        run_steps([step], journal, run_id="r")
    assert info.value.step == "a"
    assert info.value.detail == "постусловие не выполнено: uid 0; чужой дом"
    assert seen == ["why"]  # asked only once the postcondition failed
    bare = tmp_path / "bare"
    with pytest.raises(StepFailed) as plain:
        run_steps([Step("a", lambda: False, lambda: None)], bare, run_id="r")
    assert plain.value.detail == "постусловие не выполнено"
    assert _events(journal) == _events(bare)


def test_postcheck_why_failing_keeps_the_bare_refusal(tmp_path: Path) -> None:
    """A `why()` probe that raises (final review 14, n5) leaves the named
    refusal: the bare text, the same type, step and events."""
    journal = tmp_path / "j"

    def why() -> list[str]:
        raise OSError(5, "whoami")

    with pytest.raises(StepFailed) as info:
        run_steps([Step("a", lambda: False, lambda: None, why)], journal, run_id="r")
    assert info.value.step == "a"
    assert info.value.detail == "постусловие не выполнено"
    bare = tmp_path / "bare"
    with pytest.raises(StepFailed):
        run_steps([Step("a", lambda: False, lambda: None)], bare, run_id="r")
    assert _events(journal) == _events(bare)


def test_postcheck_why_not_asked_when_ready(tmp_path: Path) -> None:
    """`why()` is the failure path only: a skip or a done never calls it."""
    box = Box("a")

    def why() -> list[str]:
        raise AssertionError("why() called")

    steps = [Step("a", lambda: "a" in box.facts, lambda: None, why)]
    steps.append(Step("b", lambda: "b" in box.facts, lambda: box.facts.add("b"), why))
    run_steps(steps, tmp_path / "j", run_id="r")
    assert _events(tmp_path / "j") == [("a", "skip"), ("b", "start"), ("b", "done")]


def test_needs_human_stops_without_done(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"

    def do() -> None:
        raise NeedsHuman("run the thing")

    steps = [Step("h", lambda: False, do), Box().step("z")]
    with pytest.raises(NeedsHuman) as info:
        run_steps(steps, journal, run_id="r")
    assert info.value.instruction == "run the thing"
    assert _events(journal) == [("h", "start"), ("h", "stopped")]


def test_journal_format_and_mode(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    journal_event(journal, run_id="r", step="s", event="start", detail="d")
    row = json.loads(journal.read_text())
    assert set(row) == {"ts", "run_id", "step", "event", "detail"}
    assert row["ts"].endswith("+00:00") or row["ts"].endswith("Z")
    assert (journal.stat().st_mode & 0o777) == 0o600


def test_new_journal_is_0600_under_umask_277(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    old = os.umask(0o277)
    try:
        journal_event(journal, run_id="r", step="s", event="a")
    finally:
        os.umask(old)
    assert (journal.stat().st_mode & 0o777) == 0o600
    journal_event(journal, run_id="r", step="s", event="b")
    assert [e for _, e in _events(journal)] == ["a", "b"]


def test_existing_journal_with_wrong_mode_refused(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    journal.write_text("")
    journal.chmod(0o644)
    with pytest.raises(StepFailed, match="0644"):
        journal_event(journal, run_id="r", step="s", event="e")


def test_journal_refuses_symlink(tmp_path: Path) -> None:
    target = tmp_path / "t"
    target.write_text("")
    link = tmp_path / "j"
    link.symlink_to(target)
    with pytest.raises(StepFailed):
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
        ready, _, _ = select.select([proc.stdout], [], [], 10)
        assert ready, "holder did not start"
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


def test_writer_lock_follows_system_link_above_df(tmp_path: Path) -> None:
    """macOS: /var -> private/var is a system link and must be followed."""
    lay = _layout(tmp_path)
    (tmp_path / "private").mkdir()
    (tmp_path / "var").symlink_to("private")
    with writer_lock(lay):
        pass
    assert (tmp_path / "private" / "lib" / "df" / "install" / "lock").exists()


def test_writer_lock_refuses_symlinked_var_lib_df(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "var" / "lib").mkdir(parents=True)
    (tmp_path / "var" / "lib" / "df").symlink_to(elsewhere)
    with pytest.raises(StepFailed, match="не каталог|символическая") as info:
        with writer_lock(lay):
            pass
    assert info.value.step == "lock"
    assert list(elsewhere.iterdir()) == []


def test_writer_lock_refuses_symlinked_install(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    lay.var.mkdir(parents=True)
    lay.install_dir.symlink_to(elsewhere)
    with pytest.raises(StepFailed, match="не каталог|символическая"):
        with writer_lock(lay):
            pass
    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize("which", ["var", "lib", "df", "install"])
@pytest.mark.parametrize("bit", [0o020, 0o002])
def test_writer_lock_refuses_untrusted_dir_mode(
    tmp_path: Path, which: str, bit: int
) -> None:
    lay = _layout(tmp_path)
    lay.install_dir.mkdir(parents=True)
    target = {
        "var": tmp_path / "var",
        "lib": tmp_path / "var" / "lib",
        "df": lay.var,
        "install": lay.install_dir,
    }[which]
    target.chmod(0o755 | bit)
    with pytest.raises(StepFailed, match="права") as info:
        with writer_lock(lay):
            pass
    assert info.value.step == "lock"


def test_writer_lock_refuses_foreign_dir_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lay = _layout(tmp_path)
    lay.install_dir.mkdir(parents=True)
    monkeypatch.setattr(os, "geteuid", lambda: os.getuid() + 1)
    with pytest.raises(StepFailed, match="владелец"):
        with writer_lock(lay):
            pass


def test_writer_lock_syncs_created_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every directory of the chain, root included, created or found, is
    synced exactly once per acquisition, after its child exists (its mode
    and the child's name), `install/` last; a created lock then syncs
    `install/` again (`fsutil.create_file`). A found chain is settled again
    on every acquisition (spec 3.3 r6)."""
    from df import fsutil
    from tests.df.crash_model import fd_path

    lay = _layout(tmp_path)
    calls: list[Path] = []
    real = fsutil.sync_dir

    def spy(fd: int | Path) -> fsutil.SyncNote:
        calls.append(fd_path(fd) if isinstance(fd, int) else fd)
        return real(fd)

    monkeypatch.setattr(fsutil, "sync_dir", spy)
    root = Path(os.path.realpath(lay.root))
    chain = [
        root / "var",
        root / "var/lib",
        root / "var/lib/df",
        root / "var/lib/df/install",
    ]
    settled = [root, *chain]  # each once, after its child exists
    with writer_lock(lay):
        pass
    assert [Path(os.path.realpath(p)) for p in calls] == [*settled, chain[-1]]
    calls.clear()
    with writer_lock(lay):
        pass
    assert [Path(os.path.realpath(p)) for p in calls] == settled


def test_journal_hard_link_refused(tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.write_text("keep\n")
    journal = tmp_path / "j"
    os.link(victim, journal)
    with pytest.raises(StepFailed, match="жёстких") as info:
        journal_event(journal, run_id="r", step="s", event="e")
    assert info.value.step == "journal"
    assert victim.read_text() == "keep\n"


def test_logging_failure_keeps_original_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import df.journal as journal_mod

    def do() -> None:
        raise OSError("disk")

    calls: list[str] = []

    def fake(journal: Path, *, event: str, **kw: object) -> None:
        calls.append(event)
        if event == "failed":
            raise StepFailed("journal", "full")

    monkeypatch.setattr(journal_mod, "journal_event", fake)
    with pytest.raises(OSError) as info:
        run_steps([Step("x", lambda: False, do)], tmp_path / "j", run_id="r")
    assert any("не записано failed" in n for n in info.value.__notes__)


def test_read_consistent_rejects_zero_attempts(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        read_consistent(_layout(tmp_path), lambda: 1, attempts=0)


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


#: Generation file contents (shared with the entry's copy, Task 8a carry FM5).
GENERATION_MALFORMED = (
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
)
GENERATION_CANONICAL = (b"0", b"0\n", b"12", b"12\n")


@pytest.mark.parametrize("content", GENERATION_MALFORMED)
def test_generation_malformed_rejected(tmp_path: Path, content: bytes) -> None:
    lay = _layout(tmp_path)
    lay.install_dir.mkdir(parents=True)
    lay.generation.write_bytes(content)
    with pytest.raises(StepFailed) as info:
        read_consistent(lay, lambda: 1)
    assert info.value.step == "snapshot"


@pytest.mark.parametrize("content", GENERATION_CANONICAL)
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
    import os
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
    if any(os.path.basename(p) == lay.lock.name for p in opened):
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


@pytest.fixture
def alarm():
    def boom(*_: object) -> None:
        raise TimeoutError("blocked in open()")

    old = signal.signal(signal.SIGALRM, boom)
    signal.alarm(5)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, old)


def test_fifo_at_lock_refused_not_hung(tmp_path: Path, alarm: None) -> None:
    lay = _layout(tmp_path)
    lay.install_dir.mkdir(parents=True)
    os.mkfifo(lay.lock, 0o600)
    with pytest.raises(StepFailed) as info:
        with writer_lock(lay):
            pass
    assert info.value.step == "lock"
    assert str(lay.lock) in info.value.detail


def test_fifo_at_journal_refused_not_hung(tmp_path: Path, alarm: None) -> None:
    journal = tmp_path / "j"
    os.mkfifo(journal, 0o600)
    with pytest.raises(StepFailed) as info:
        journal_event(journal, run_id="r", step="s", event="e")
    assert info.value.step == "journal"
    assert str(journal) in info.value.detail
    with pytest.raises(StepFailed):
        read_journal(journal)


def test_fifo_at_generation_refused_not_hung(tmp_path: Path, alarm: None) -> None:
    lay = _layout(tmp_path)
    lay.install_dir.mkdir(parents=True)
    os.mkfifo(lay.generation, 0o600)
    with pytest.raises(StepFailed) as info:
        read_consistent(lay, lambda: 1)
    assert info.value.step == "snapshot"


def test_created_dirs_are_0755_under_umask_077(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    old = os.umask(0o077)
    try:
        with writer_lock(lay):
            pass
    finally:
        os.umask(old)
    for d in (lay.var.parent, lay.var, lay.install_dir):
        assert (d.stat().st_mode & 0o777) == 0o755
    assert (lay.lock.stat().st_mode & 0o777) == 0o600


def test_new_lock_is_0600_under_umask_277(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    old = os.umask(0o277)
    try:
        with writer_lock(lay):
            pass
    finally:
        os.umask(old)
    assert (lay.lock.stat().st_mode & 0o777) == 0o600


def test_existing_lock_with_wrong_mode_refused(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    lay.install_dir.mkdir(parents=True)
    lay.lock.write_text("")
    lay.lock.chmod(0o644)
    with pytest.raises(StepFailed, match="0644"):
        with writer_lock(lay):
            pass


def test_existing_lock_with_extra_link_refused(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    lay.install_dir.mkdir(parents=True)
    lay.lock.write_text("")
    lay.lock.chmod(0o600)
    os.link(lay.lock, tmp_path / "other")
    with pytest.raises(StepFailed) as info:
        with writer_lock(lay):
            pass
    assert info.value.step == "lock"


def test_existing_lock_with_foreign_owner_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lay = _layout(tmp_path)
    lay.install_dir.mkdir(parents=True)
    lay.lock.write_text("")
    lay.lock.chmod(0o600)
    import df.journal as journal_mod

    monkeypatch.setattr(journal_mod, "ROOT_UID", os.getuid())
    monkeypatch.setattr(os, "geteuid", lambda: os.getuid() + 1)
    with pytest.raises(StepFailed, match="lock") as info:
        with writer_lock(lay):
            pass
    assert "lock: " in str(info.value) and str(lay.lock) in info.value.detail


def test_mkdir_race_reopens_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lay = _layout(tmp_path)
    real = os.mkdir

    def racing(name, mode=0o777, *, dir_fd=None):  # type: ignore[no-untyped-def]
        real(name, mode, dir_fd=dir_fd)  # the "other process" wins
        real(name, mode, dir_fd=dir_fd)  # ours: FileExistsError

    monkeypatch.setattr(os, "mkdir", racing)
    with writer_lock(lay):
        pass
    assert lay.install_dir.is_dir()


def test_error_messages_name_the_kind(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    lay.install_dir.mkdir(parents=True)
    lay.lock.mkdir()
    with pytest.raises(StepFailed, match="не обычный файл"):
        with writer_lock(lay):
            pass
    lay.lock.rmdir()
    (tmp_path / "t").write_text("")
    lay.lock.symlink_to(tmp_path / "t")
    with pytest.raises(StepFailed, match="символическая ссылка"):
        with writer_lock(lay):
            pass


def test_failed_do_is_journaled(tmp_path: Path) -> None:
    journal = tmp_path / "j"

    def do() -> None:
        raise OSError("disk")

    with pytest.raises(OSError):
        run_steps([Step("x", lambda: False, do)], journal, run_id="r")
    rows = read_journal(journal)
    assert rows[-1]["event"] == "failed"
    assert "OSError: disk" in str(rows[-1]["detail"])


def test_read_journal_non_utf8_named(tmp_path: Path) -> None:
    journal = tmp_path / "j"
    journal.write_bytes(b"\xff\xfe\n")
    with pytest.raises(ValueError, match="line 1"):
        read_journal(journal)


def test_writer_lock_repairs_only_dfs_own_components(tmp_path: Path) -> None:
    """m-D: by position in the path, `var` (`/var/lib/df`) and `install` are
    df's own and a narrower mode of theirs is repaired to `0755`; the system
    components above them (`/var`, `/var/lib`) are settled, never changed."""
    lay = _layout(tmp_path)
    root = lay.root
    for rel in ("var", "var/lib", "var/lib/df", "var/lib/df/install"):
        (root / rel).mkdir(mode=0o700)
        os.chmod(root / rel, 0o700)
    with writer_lock(lay):
        pass
    modes = {
        rel: stat.S_IMODE(os.stat(root / rel).st_mode)
        for rel in ("var", "var/lib", "var/lib/df", "var/lib/df/install")
    }
    assert modes == {
        "var": 0o700,
        "var/lib": 0o700,
        "var/lib/df": 0o755,
        "var/lib/df/install": 0o755,
    }
