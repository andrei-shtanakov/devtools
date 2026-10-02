"""Факты по first-parent истории (спека среза 1, §7.1, §7.3)."""

import subprocess
from pathlib import Path

import pytest

from conductor import facts
from conductor.facts import (
    AmbiguousItem,
    edge_period,
    first_done_commit,
    item_state,
    path_added,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "master")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


def commit(repo: Path, todo: str, msg: str, extra: dict[str, str] | None = None) -> str:
    (repo / "TODO.md").write_text(todo, encoding="utf-8")
    for path, text in (extra or {}).items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


def test_first_done_commit_is_latest_transition(repo: Path) -> None:
    commit(repo, "- [ ] x @id:x\n", "open")
    done1 = commit(repo, "- [x] x @id:x\n", "done")
    commit(repo, "- [x] x правка заголовка @id:x\n", "title")
    assert first_done_commit(repo, "master", "a", "x") == done1
    commit(repo, "- [ ] x @id:x\n", "reopen")
    done2 = commit(repo, "- [x] x @id:x\n", "done again")
    assert first_done_commit(repo, "master", "a", "x") == done2


def test_first_done_ignores_other_items(repo: Path) -> None:
    commit(repo, "- [ ] x @id:x\n- [ ] x2 @id:x2\n", "open")
    done = commit(repo, "- [x] x @id:x\n- [ ] x2 @id:x2\n", "x done")
    commit(repo, "- [x] x @id:x\n- [x] x2 @id:x2\n", "x2 done")
    assert first_done_commit(repo, "master", "a", "x") == done


def test_edge_period_survives_title_edit_and_resets_on_readd(repo: Path) -> None:
    commit(repo, "- [ ] c @id:c\n", "no edge")
    added = commit(repo, "- [ ] c @id:c @blocked_by:todo://p/y\n", "edge")
    commit(repo, "- [ ] c новый заголовок @id:c @blocked_by:todo://p/y\n", "title")
    period = edge_period(repo, "master", "a", "c", "todo://p/y")
    assert period is not None and period[0] == added
    commit(repo, "- [ ] c @id:c\n", "drop")
    readded = commit(repo, "- [ ] c @id:c @blocked_by:todo://p/y\n", "readd")
    period = edge_period(repo, "master", "a", "c", "todo://p/y")
    assert period is not None and period[0] == readded


def test_edge_period_ignores_prefix_collision(repo: Path) -> None:
    commit(repo, "- [ ] c @id:c @blocked_by:todo://p/yy\n", "other edge")
    added = commit(
        repo, "- [ ] c @id:c @blocked_by:todo://p/yy @blocked_by:todo://p/y\n", "edge"
    )
    period = edge_period(repo, "master", "a", "c", "todo://p/y")
    assert period is not None and period[0] == added


def test_path_added_last_appearance(repo: Path) -> None:
    commit(repo, "x\n", "base")
    first = commit(repo, "x\n", "add", {"docs/путь.md": "1"})
    assert path_added(repo, "master", "docs/путь.md") == first
    (repo / "docs" / "путь.md").unlink()
    _git(repo, "commit", "-qam", "rm")
    again = commit(repo, "x\n", "readd", {"docs/путь.md": "2"})
    assert path_added(repo, "master", "docs/путь.md") == again
    assert path_added(repo, "master", "absent.md") is None


def test_backticked_id_is_not_an_item(repo: Path) -> None:
    """Канонический разбор (как узлы ядра): `@id` в бэктиках — не пункт,
    ни в истории фактов, ни в свежей перепроверке (раунд 3 ревью, F1)."""
    commit(repo, "- [ ] x @id:x\n", "open")
    commit(repo, "- [x] x `@id:x`\n", "quoted, done")
    assert first_done_commit(repo, "master", "a", "x") is None
    assert item_state("- [x] x `@id:x`\n", "a", "x") is None
    state = item_state("- [x] x @id:x @blocked_by:todo://p/y\n", "a", "x")
    assert state is not None and state.done and state.blocked_by == {"todo://p/y"}


def test_history_covers_quoted_id_and_refuses_duplicates(repo: Path) -> None:
    """Раунд 4 (R4-2, R4-1): история перебирает ВСЕ версии TODO (не отбор по
    `@id:x`): квотированный id находит новый факт; дубликат id — не факт."""
    commit(repo, "- [ ] x @id:x\n", "open")
    commit(repo, "- [x] x @id:x\n", "done")
    commit(repo, '- [x] x @id:"x"\n', "quote")
    commit(repo, '- [ ] x @id:"x"\n', "reopen")
    latest = commit(repo, '- [x] x @id:"x"\n', "done again")
    assert first_done_commit(repo, "master", "a", "x") == latest
    commit(repo, '- [x] x @id:"x"\n- [ ] x @id:x\n', "duplicate")
    assert first_done_commit(repo, "master", "a", "x") == latest  # не новый факт
    with pytest.raises(AmbiguousItem):
        item_state("- [x] x @id:x\n- [ ] x @id:x\n", "a", "x")


def test_history_reads_are_lazy_and_cached(repo: Path, monkeypatch) -> None:
    """Раунд 5 (Codex, R5-1): полнота истории без повторного чтения всех
    версий — голова фиксируется по SHA, журнал и тексты кэшируются, версии
    читаются до найденного перехода. 40 версий, 10 запросов к той же голове."""
    for k in range(40):
        commit(repo, f"# заголовок {k}\n", f"title {k}")
    done = commit(repo, "# заголовок\n- [x] done @id:goal\n", "done")
    calls: list[tuple[str, ...]] = []
    real = facts.git

    def counted(repo_dir: Path, *args: str) -> tuple[int, str, str]:
        calls.append(args)
        return real(repo_dir, *args)

    monkeypatch.setattr(facts, "git", counted)
    for _ in range(10):
        assert first_done_commit(repo, "master", "a", "goal") == done
    assert sum(a[0] == "log" for a in calls) == 1  # журнал — один раз
    assert sum(a[0] == "show" for a in calls) <= 2  # HEAD и его родитель
    assert len(calls) <= 13
