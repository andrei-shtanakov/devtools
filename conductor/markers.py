"""Управляющие маркеры conductor (спека среза 1, §4.4; О §5.10).

Маркер — ровно один блок `<!-- conductor:v1 <kind> k=v … -->` последней
непустой строкой. Значения — `h1-<16 hex>` (хэш канонической сериализации)
или десятичное `n`: сырой id (условие, путь, `repo!N`) в маркер не попадает.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Literal

FIELDS: dict[str, tuple[str, ...]] = {
    "q": ("id",),
    "sat": ("evidence", "wait"),
    "nudge": ("n", "p", "wait"),
    "prnudge": ("n", "pr"),
    "close": ("evidence", "node", "period"),
    "queue": ("projection",),
}
EVENT_KINDS = frozenset(FIELDS) - {"queue"}
NUMERIC = frozenset({"n"})
TAG = "conductor:v1"
H1_RE = re.compile(r"^h1-[0-9a-f]{16}$")
N_RE = re.compile(r"^[1-9][0-9]*$")
LINE_RE = re.compile(r"^<!-- conductor:v1 ([a-z]+)((?: [a-z]+=[a-z0-9-]+)+) -->$")
Kind = Literal["event", "edited", "foreign", "none"]


class MarkerError(ValueError):
    """Маркер нарушает схему вида."""


def h1(kind: str, *parts: str) -> str:
    """Идентификатор поля маркера: h1-<первые 16 hex sha256>."""
    raw = json.dumps([kind, *parts], ensure_ascii=False, separators=(",", ":"))
    return "h1-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Marker:
    """Разобранный маркер: вид и поля в каноническом порядке."""

    kind: str
    fields: tuple[tuple[str, str], ...]

    def get(self, key: str) -> str:
        """Значение поля."""
        return dict(self.fields)[key]


def make(kind: str, **fields: str) -> Marker:
    """Проверенный маркер; нарушение схемы — MarkerError."""
    if kind not in FIELDS:
        raise MarkerError(f"неизвестный вид {kind!r}")
    if tuple(sorted(fields)) != FIELDS[kind]:
        raise MarkerError(f"{kind}: поля {sorted(fields)} != {FIELDS[kind]}")
    for key, value in fields.items():
        pattern = N_RE if key in NUMERIC else H1_RE
        if not pattern.match(value):
            raise MarkerError(f"{kind}.{key}={value!r}")
    return Marker(kind, tuple(sorted(fields.items())))


def render(m: Marker) -> str:
    """Строка маркера."""
    pairs = "".join(f" {k}={v}" for k, v in m.fields)
    return f"<!-- {TAG} {m.kind}{pairs} -->"


def escape(text: str) -> str:
    """Цитата не может стать маркером: `<!--` и `conductor:` обезврежены."""
    return text.replace("<!--", "&lt;!--").replace("conductor:", "conductor&#58;")


def with_marker(text: str, m: Marker) -> str:
    """Тело записи: экранированный текст и маркер последней строкой."""
    return f"{escape(text).rstrip()}\n\n{render(m)}"


def parse_body(body: str) -> Marker | None:
    """Маркер тела или None (нет, не последней строкой, не один, не по схеме)."""
    if body.count(TAG) != 1:
        return None
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    match = LINE_RE.match(lines[-1]) if lines else None
    if match is None:
        return None
    pairs = [p.split("=", 1) for p in match.group(2).split()]
    keys = [k for k, _ in pairs]
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        return None
    try:
        return make(match.group(1), **dict(pairs))
    except MarkerError:
        return None


def classify(comment: dict[str, Any], bot_login: str) -> tuple[Kind, Marker | None]:
    """Событие только от бота и без правки (О §5.10); проекция — не событие."""
    m = parse_body(comment.get("body") or "")
    if m is None or m.kind not in EVENT_KINDS:
        return "none", None
    if comment.get("author") != bot_login:
        return "foreign", None
    if comment.get("updated_at") != comment.get("created_at"):
        return "edited", None
    return "event", m
