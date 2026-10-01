"""Маркеры (спека среза 1, §4.4)."""

import pytest

from conductor.markers import (
    MarkerError,
    classify,
    escape,
    h1,
    make,
    parse_body,
    render,
    with_marker,
)

BOT = "conductor[bot]"


def test_h1_is_stable_and_alphabet_safe() -> None:
    a = h1("wait", "devtools", "x", "date>=2026-10-01")
    assert a == h1("wait", "devtools", "x", "date>=2026-10-01")
    assert a.startswith("h1-") and len(a) == 19
    assert a != h1("wait", "devtools", "x", "date>=2026-10-02")
    assert h1("pr", "devtools!42") != h1("pr", "devtools!43")
    assert h1("path", "repo", "docs/путь с пробелом.md").startswith("h1-")


def test_render_parse_round_trip_every_kind() -> None:
    v = h1("x", "1")
    cases = {
        "q": {"id": v},
        "sat": {"evidence": v, "wait": v},
        "nudge": {"n": "2", "p": v, "wait": v},
        "prnudge": {"n": "1", "pr": v},
        "close": {"evidence": v, "node": v, "period": v},
        "queue": {"projection": v},
    }
    for kind, fields in cases.items():
        m = make(kind, **fields)
        assert parse_body(with_marker("текст", m)) == m


def test_make_rejects_bad_values() -> None:
    v = h1("x", "1")
    with pytest.raises(MarkerError):
        make("sat", evidence=v)  # нет поля wait
    with pytest.raises(MarkerError):
        make("sat", evidence=v, wait="devtools!42")  # сырой id
    with pytest.raises(MarkerError):
        make("nudge", n="01", p=v, wait=v)
    with pytest.raises(MarkerError):
        make("other", id=v)


def test_marker_must_be_single_and_last() -> None:
    m = make("q", id=h1("q", "a"))
    body = with_marker("текст", m)
    assert parse_body(body + "\nхвост") is None
    assert parse_body(render(m) + "\n" + body) is None
    assert parse_body("текст") is None


def test_quoted_marker_is_escaped_and_never_parsed() -> None:
    m = make("q", id=h1("q", "a"))
    quoted = "цитата: " + render(make("q", id=h1("q", "b")))
    body = with_marker(quoted, m)
    assert parse_body(body) == m
    assert "<!--" not in escape(quoted) and "conductor:" not in escape(quoted)
    assert parse_body(escape(render(m))) is None


def test_classify_comment() -> None:
    m = make("q", id=h1("q", "a"))
    base = {"id": 1, "body": with_marker("x", m), "created_at": "t", "updated_at": "t"}
    assert classify({**base, "author": BOT}, BOT) == ("event", m)
    assert classify({**base, "author": "owner"}, BOT) == ("foreign", None)
    assert classify({**base, "author": BOT, "updated_at": "t2"}, BOT) == (
        "edited",
        None,
    )
    assert classify({**base, "author": BOT, "body": "x"}, BOT) == ("none", None)
    queue = {
        **base,
        "author": BOT,
        "body": with_marker("x", make("queue", projection=h1("p"))),
    }
    assert classify(queue, BOT) == ("none", None)  # проекция — не событие
