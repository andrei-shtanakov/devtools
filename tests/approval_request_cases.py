"""Общий набор случаев заявки (T21a/T21b): каждый потребитель гоняет ВСЕ.

`GOOD` — корректная заявка; `DEFECTS` — (id, функция порчи текста, фрагмент
причины). Двойник каждой порчи — сам `GOOD`: он обязан проходить там же.
"""

from __future__ import annotations

from collections.abc import Callable

from governance import approval_request as ar

POLICY_SHA = "a1" * 20

GOOD = ar.ApprovalRequest(
    brief_self_hash="sha256:" + "b" * 64,
    policy_repo="andrei-shtanakov/approval-policy",
    policy_ref="main",
    policy_path="policy/approvers.env",
    policy_sha=POLICY_SHA,
    run_id="WS-1-abc123",
    ws_id="WS-1",
)

Mutation = Callable[[str], str]

DEFECTS: list[tuple[str, Mutation, str]] = [
    ("dup-top", lambda t: t + "purpose: discovery-brief-approval\n", "дубл"),
    ("dup-policy", lambda t: t.replace("  sha:", "  ref: main\n  sha:"), "дубл"),
    ("second-doc", lambda t: t + "---\nschema: x\n", "документ"),
    ("extra-key", lambda t: t + "extra: '1'\n", "ключ"),
    ("missing-key", lambda t: t.replace('run_id: "WS-1-abc123"\n', ""), "ключ"),
    ("short-sha", lambda t: t.replace(POLICY_SHA, POLICY_SHA[:39]), "40 hex"),
    ("crlf", lambda t: t.replace("\n", "\r\n"), "CR"),
    (
        "purpose",
        lambda t: t.replace("purpose: discovery-brief-approval", "purpose: x"),
        "purpose",
    ),
    ("brief", lambda t: t.replace("brief: brief.md", "brief: x.md"), "brief"),
    (
        "schema",
        lambda t: t.replace("discovery-brief-approval-request/v1", "v0"),
        "schema",
    ),
    (
        "list-value",
        lambda t: t.replace('run_id: "WS-1-abc123"', "run_id: [1]"),
        "строк",
    ),
    ("non-str-key", lambda t: t + "? [a, b]\n: x\n", "скаляр"),
    ("int-key", lambda t: t + "1: x\n", "строка"),
    ("not-yaml", lambda t: t + "policy: [\n", "YAML"),
]
