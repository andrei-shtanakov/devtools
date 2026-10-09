"""The approval envelope of a brief — the mirror of a human merge.

The contract (§1, mirror rule) says `status`/`approved_by`/`approver` are
a machine reflection of git state, git being the source of truth. This
module is the reflecting half and has no effect of its own: it knows what
the envelope is, how a brief's own bytes are hashed *around* it, how it is
stamped on and withdrawn, and what an honest approval looks like to a
reader. Establishing the fact being mirrored — that a PR carrying these
bytes was merged by an authorised human — is `cli.cmd_approve` over a
`forge.Forge`; nothing here talks to a forge or reads a file.

The self-hash follows `devtools/governance/node_approval.py`: the text
with the whole approval envelope removed from its frontmatter, so the hash
computed before the signature is still the hash after it, and so a
`stale`/`draft` brief keeps the record of what its signature covered. It is
the `"sha256:<hex>"` convention of `discovery.hashing`, over the
line-ending-folded bytes, not devtools' git blob sha1: the two answer the
same question for different artefacts and are not meant to be compared.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import yaml

from discovery.contract.gate_check import split_frontmatter
from discovery.hashing import canonical_answer_bytes
from discovery.render import GENERATED_BY

STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"

#: Where a brief remembers its own approved bytes (node_approval condition 4).
SELF_HASH_KEY = "approved_content_hash"

#: The whole approval envelope, in the order `stamp` writes it after
#: `status`. `self_hash` cuts every one of these, `status` included:
#: otherwise approving the bytes would be indistinguishable from
#: rewriting the envelope.
ENVELOPE_KEYS = (
    "status",
    "approved_by",
    "approved_at",
    "approver",
    SELF_HASH_KEY,
)

#: Why a brief is not honestly approved — a value, not a substring of the
#: message, because `admit` and `approve` branch on it.
DEBT_STATUS = "debt_status"
DEBT_UNSIGNED = "unsigned"
DEBT_MIGRATION = "migration"
DEBT_SELF_HASH = "self_hash"


class NotABrief(Exception):
    """The text has no readable frontmatter, so it has no envelope to mirror."""


@dataclass(frozen=True)
class MergeEvent:
    """The act being mirrored: who merged, when, and which commit resulted."""

    login: str
    merged_at: str
    commit: str


def _split(text: str) -> tuple[dict[str, Any], str]:
    """Frontmatter and body of `text`, line endings folded to LF first:
    `split_frontmatter` recognises only `---\n`, and a CRLF checkout of the
    same brief is the same brief."""
    meta, body = split_frontmatter(canonical_answer_bytes(text).decode("utf-8"))
    if meta is None:
        raise NotABrief("no readable frontmatter")
    return meta, body


def _join(meta: dict[str, Any], body: str) -> str:
    """The renderer's own serialisation (`render_brief`), so a brief the
    runtime rendered round-trips byte-for-byte outside the envelope."""
    dumped = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)
    return f"---\n{dumped}---\n{body}"


def self_hash(text: str) -> str:
    """Hash of the brief's own bytes with the approval envelope cut out."""
    meta, body = _split(text)
    for key in ENVELOPE_KEYS:
        meta.pop(key, None)
    digest = hashlib.sha256(canonical_answer_bytes(_join(meta, body)))
    return f"sha256:{digest.hexdigest()}"


def _with_envelope(meta: dict[str, Any], envelope: dict[str, Any]) -> dict[str, Any]:
    """`meta` with the envelope keys replaced as a block at `status`'s place.

    Rebuilt rather than assigned in place so the four fields sit together
    after `status` whatever the file held before — an envelope scattered
    through the frontmatter reads as several claims, not one act.
    """
    rebuilt: dict[str, Any] = {}
    placed = False
    for key, value in meta.items():
        if key in ENVELOPE_KEYS:
            if not placed:
                rebuilt.update(envelope)
                placed = True
            continue
        rebuilt[key] = value
    if not placed:
        rebuilt.update(envelope)
    return rebuilt


def stamp(text: str, merge: MergeEvent) -> str:
    """The brief with the approval envelope mirroring `merge`.

    `approved_by` is this runtime (the actor that wrote the record, per the
    contract's field comment); `approver` is the human's git handle;
    `approved_at` is the merge time, not the time of this call — the act
    happened at the forge, this only reflects it.
    """
    meta, body = _split(text)
    envelope = {
        "status": STATUS_APPROVED,
        "approved_by": GENERATED_BY,
        "approved_at": merge.merged_at,
        "approver": merge.login,
        SELF_HASH_KEY: self_hash(text),
    }
    return _join(_with_envelope(meta, envelope), body)


def withdraw(text: str) -> str:
    """The brief back in `draft`, signature fields removed."""
    meta, body = _split(text)
    return _join(_with_envelope(meta, {"status": STATUS_DRAFT}), body)


def verify(text: str) -> str | None:
    """Why `text` is not honestly approved; `None` when it is.

    The reader-side half of the predicate: it cannot ask the forge whether
    the recorded act happened, but it can tell whether the bytes it holds
    are the bytes the signature covered. `approved` without a self-hash is
    a signature written by hand, before this module existed — migration
    debt, refused rather than trusted.
    """
    meta, _ = _split(text)
    if meta.get("status") != STATUS_APPROVED:
        return DEBT_STATUS
    # A recorded hash that does not match is judged before the signature's
    # completeness: it is the one fact that survives any edit of the other
    # envelope fields, and a reader that let a blanked `approver` mask it
    # would admit exactly the edited brief the hash exists to catch.
    recorded = meta.get(SELF_HASH_KEY)
    if recorded and recorded != self_hash(text):
        return DEBT_SELF_HASH
    if not all(meta.get(key) for key in ("approved_by", "approved_at", "approver")):
        return DEBT_UNSIGNED
    if not recorded:
        return DEBT_MIGRATION
    return None
