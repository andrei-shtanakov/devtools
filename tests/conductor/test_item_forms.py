"""Таблица форм записи пункта: проекция `facts.item_state` и история фактов
совпадают с узлом ядра для любой формы (раунды 3–4 ревью: класс «свежая
проекция пункта расходится с ядром» закрывается механизмом, не случаем).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conductor.facts import AmbiguousItem, first_done_commit, item_state
from tests.conductor.slice1_fixtures import world

OPEN = "- [ ] g @owner:TBD @id:goal\n"
FORMS = {
    "bare-done": "- [x] g @owner:TBD @id:goal\n",
    "upper-X": "- [X] g @owner:TBD @id:goal\n",
    "star-bullet": "* [x] g @owner:TBD @id:goal\n",
    "nested": "- [ ] parent @owner:TBD @id:p\n  - [x] g @owner:TBD @id:goal\n",
    "quoted-id": '- [x] g @owner:TBD @id:"goal"\n',
    "backtick-id": "- [x] g @owner:TBD `@id:goal`\n",
    "glued-id": "- [x] g @owner:TBD x@id:goal\n",
    "trailing-dot": "- [x] g @owner:TBD @id:goal.\n",
    "code-fence": "```\n- [x] g @owner:TBD @id:goal\n```\n",
    "html-comment": "<!-- - [x] g @owner:TBD @id:goal -->\n",
    "renamed": "- [x] g @owner:TBD @id:other\n",
    "deleted": "# пунктов нет\n",
    "dup-done-open": "- [x] g @owner:TBD @id:goal\n- [ ] g @owner:TBD @id:goal\n",
    "dup-open-done": "- [ ] g @owner:TBD @id:goal\n- [x] g @owner:TBD @id:goal\n",
}
NODE = "todo://a/goal"


def _core_node(text: str):
    result, _ = world({"a": text}, [])
    return result.graph.nodes.get(NODE)


def _duplicate(text: str) -> bool:
    return any(f.code == "PF-ID-DUPLICATE" for f in world({"a": text}, [])[0].findings)


@pytest.mark.parametrize("form", sorted(FORMS))
def test_projection_matches_core(form: str) -> None:
    """Есть ли пункт и выполнен ли он — как у ядра; дубликат — отказ."""
    text = FORMS[form]
    node = _core_node(text)
    if form.startswith("dup-"):
        with pytest.raises(AmbiguousItem):
            item_state(text, "a", "goal")
        return
    state = item_state(text, "a", "goal")
    assert (state is None) == (node is None), form
    if node is not None and state is not None:
        assert state.done == (not node.is_open), form


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.mark.parametrize("form", sorted(FORMS))
def test_history_sees_exactly_core_transitions(tmp_path: Path, form: str) -> None:
    """Переход open → форма: факт выполнения находится тогда и только тогда,
    когда ядро на новой версии видит выполненный однозначный пункт; правка
    формы без смены состояния не меняет факт."""
    _git(tmp_path, "init", "-q", "-b", "master")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    for msg, text in (("open", OPEN), ("form", FORMS[form])):
        (tmp_path / "TODO.md").write_text(text, encoding="utf-8")
        _git(tmp_path, "add", "-A")
        _git(tmp_path, "commit", "-q", "--allow-empty", "-m", msg)
    head = _git(tmp_path, "rev-parse", "HEAD")
    node = _core_node(FORMS[form])
    sees_done = node is not None and not node.is_open and not form.startswith("dup-")
    expected = head if sees_done else None
    assert first_done_commit(tmp_path, "master", "a", "goal") == expected, form
    if sees_done:  # переписать форму обратно в bare — тот же факт, нового нет
        (tmp_path / "TODO.md").write_text(FORMS["bare-done"], encoding="utf-8")
        _git(tmp_path, "commit", "-q", "--allow-empty", "-am", "respell")
        assert first_done_commit(tmp_path, "master", "a", "goal") == head, form
