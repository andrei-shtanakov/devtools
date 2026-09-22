"""`update_frontmatter`: побайтовая правка названных ключей и откат на канон.

Предмет — @id:approval-stamp-frontmatter-roundtrip: штамп меняет по смыслу
две-четыре величины, остальной авторский текст обязан остаться прежним; а
там, где построчная правка формы не умеет, результат обязан быть ВЕРНЫМ,
а не отказом (major ревью PR #338: flow-маппинг верхнего уровня прежний
путь принимал).
"""

from __future__ import annotations

from governance.frontmatter import (
    join_frontmatter,
    split_frontmatter,
    update_frontmatter,
)

BLOCK = (
    "---\n"
    "node: charter\n"
    "# авторский комментарий\n"
    "status: draft\n"
    "version: 1\n"
    "traces_to: [discovery-brief]\n"
    'brief_sha256: "e3b0c44298fc1c149afbfbf4c8996fb92427ae41e4649b934ca495991b7852b8"\n'
    'upstream_hashes: {requirements: "0123456789abcdef0123456789abcdef01234567"}\n'
    "approved_by: ''\n"
    "---\n"
    "\n"
    "Тело узла.\n"
)


def _expected(text: str, updates: dict) -> dict:
    meta, _ = split_frontmatter(text)
    return {**meta, **updates}


def test_only_changed_keys_are_rewritten_authored_lines_survive() -> None:
    updates = {"status": "approved", "version": 2, "approved_by": "andrei"}
    out = update_frontmatter(BLOCK, updates)
    lines = out.splitlines()
    assert "traces_to: [discovery-brief]" in lines
    assert (
        'brief_sha256: "e3b0c44298fc1c149afbfbf4c8996fb92427ae41e4649b934ca495991b7852b8"'
        in lines
    )
    assert "# авторский комментарий" in lines
    assert "status: approved" in lines
    assert "version: 2" in lines
    assert "approved_by: andrei" in lines
    assert out.endswith("---\n\nТело узла.\n"), "тело и разделитель как были"
    assert split_frontmatter(out)[0] == _expected(BLOCK, updates)


def test_unchanged_value_keeps_its_authored_form_byte_for_byte() -> None:
    pins = {"requirements": "0123456789abcdef0123456789abcdef01234567"}
    out = update_frontmatter(BLOCK, {"upstream_hashes": pins, "status": "stale"})
    assert (
        'upstream_hashes: {requirements: "0123456789abcdef0123456789abcdef01234567"}'
        in out.splitlines()
    ), "совпавшее по значению значение не переписывается"


def test_missing_key_is_appended_inside_the_block() -> None:
    out = update_frontmatter(BLOCK, {"approved_at": "2026-09-22T00:00:00Z"})
    head = out.split("\n---\n", 1)[0]
    assert head.endswith("approved_at: '2026-09-22T00:00:00Z'")
    assert split_frontmatter(out)[0] == _expected(
        BLOCK, {"approved_at": "2026-09-22T00:00:00Z"}
    )


def test_top_level_flow_mapping_falls_back_to_canonical_render() -> None:
    """Major ревью PR #338: `{node: …}` одной строкой парсер принимает, а
    построчная правка не видит ключей — результат обязан быть верным
    каноном, а не невалидной смесью и не отказом."""
    flow = "---\n{node: charter, status: draft, version: 1}\n---\n\nТекст\n"
    updates = {"status": "approval_pending", "version": 2, "hash": "abc"}
    out = update_frontmatter(flow, updates)
    assert split_frontmatter(out)[0] == _expected(flow, updates)
    assert out == join_frontmatter(_expected(flow, updates), "Текст\n")


def test_duplicate_key_falls_back_to_canonical_render() -> None:
    dup = "---\nstatus: a\nstatus: b\nnode: x\n---\n\nТ\n"
    out = update_frontmatter(dup, {"status": "c"})
    assert split_frontmatter(out)[0] == {"status": "c", "node": "x"}


def test_multiline_authored_value_is_replaced_whole() -> None:
    """Диапазон ключа — до следующего ключа верхнего уровня: блочный список
    заменяется целиком, а не первой строкой."""
    text = (
        "---\nnode: x\ntraces_to:\n- a\n- b\nstatus: draft\n---\n\nТ\n"
    )
    out = update_frontmatter(text, {"traces_to": ["c"]})
    assert split_frontmatter(out)[0] == {
        "node": "x", "traces_to": ["c"], "status": "draft",
    }
    assert "- a" not in out and "- b" not in out
