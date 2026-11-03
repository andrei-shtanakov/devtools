"""`act_git_rows`: разбор first-parent строк и синонимы в `ops`."""

from __future__ import annotations

from governance import act_git_rows, ops


def test_ops_reexports_rows() -> None:
    assert ops._parse_first_parent is act_git_rows.parse_first_parent
    assert ops._FIRST_PARENT_LOG is act_git_rows.FIRST_PARENT_LOG
    assert ops.FirstParentRows is act_git_rows.FirstParentRows


def test_parse_first_parent_unchanged() -> None:
    out = "\x00aaa bbb\nM\tx.py\nA\ty.py\n\x00bbb\n\x00ccc bbb ddd\nnote\n"
    assert act_git_rows.parse_first_parent(out) == [
        ("aaa", ("bbb",), (("M", "x.py"), ("A", "y.py"))),
        ("bbb", (), ()),
        ("ccc", ("bbb", "ddd"), ()),
    ]
    assert act_git_rows.parse_first_parent("") == []


def test_narrow_import_closure() -> None:
    import subprocess
    import sys

    code = (
        "import sys; import governance.act_git_rows, governance.act_forge_http;"
        "bad=[m for m in ('governance.ops','df') if m in sys.modules];"
        "print(bad)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"
