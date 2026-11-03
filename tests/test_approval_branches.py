"""Stop-ветка контракта `approval-branches` (Task 1 этапа 2b): python-читатель
(`governance.approval_branches`) выводит имя и разбор из шаблона
`APPROVAL_STOP_TEMPLATE`. Shell-читателя ключа нет: `merge-pr.sh` stop-PR не
маршрутизирует; шаблон читают издатель df и `act_node`."""

from __future__ import annotations

from pathlib import Path

import pytest

from governance import approval_branches as ab


def test_stop_branch_renders_template() -> None:
    assert ab.stop_branch("ws1", "design", 3) == "stop/ws1-design-g3"


def test_stop_glob_is_derived_from_the_template() -> None:
    assert ab.stop_glob() == "stop/*-g*"


def test_parse_stop_branch_with_dashed_ws_and_node() -> None:
    ref = ab.stop_branch("a-b", "c-d", 12)
    assert ab.parse_stop_branch(ref, "a-b") == ("c-d", 12)


def test_parse_stop_branch_node_may_look_like_a_generation() -> None:
    ref = ab.stop_branch("ws", "x-g1", 2)
    assert ab.parse_stop_branch(ref, "ws") == ("x-g1", 2)


def test_parse_stop_branch_rejects_other_ws() -> None:
    ref = ab.stop_branch("a-b", "c-d", 1)
    assert ab.parse_stop_branch(ref, "other") is None


@pytest.mark.parametrize(
    "ref",
    [
        "stop/ws-n-g",  # no generation
        "stop/ws--g1",  # empty node
        "stop/ws-n-g01",  # not canonical
        "stop/ws-n-g1x",
        "spec/ws-n-g1",
        "xstop/ws-n-g1",
        "stop/ws-n-g1/extra",
        "stop/ws-a/b-g1",  # node is one path segment
    ],
)
def test_parse_stop_branch_rejects_malformed(ref: str) -> None:
    assert ab.parse_stop_branch(ref, "ws") is None


@pytest.mark.parametrize(
    "template",
    [
        "stop/{ws_id}-{node}",  # no generation
        "stop/{ws_id}-{node}-g{generation}-{node}",  # repeated
        "stop/{ws_id}-{node}-g{generation}-{extra}",  # unknown
        "stop/fixed",
    ],
)
def test_stop_template_of_wrong_shape_is_refused(
    template: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = tmp_path / "patterns.env"
    bad.write_text(
        ab.PATTERNS_PATH.read_text().replace(
            "stop/{ws_id}-{node}-g{generation}", template
        )
    )
    monkeypatch.setattr(ab, "PATTERNS_PATH", bad)
    for call in (
        ab.stop_template,
        ab.stop_glob,
        lambda: ab.stop_branch("ws", "n", 1),
        lambda: ab.parse_stop_branch("stop/ws-n-g1", "ws"),
    ):
        with pytest.raises(RuntimeError, match="плейсхолдеры"):
            call()
