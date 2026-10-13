"""Общие фикстуры тестов act_*: набор политик на одном коммите."""


def code(outcome: object) -> str:
    """Код отказа либо имя типа исхода — для точных assert'ов (код вне `==`)."""
    return getattr(outcome, "code", type(outcome).__name__)


COMMIT = "d" * 40
FILES: dict[str, bytes] = {
    "policy/approvers.env": (
        b"AUTHORIZED_APPROVER_ACCOUNTS=alice\n"
        b"AUTHORIZED_MERGER_ACCOUNTS=df-reviewer[bot]\n"
    ),
    "policy/modes.yaml": (
        b"schema: approval-policy-modes/v1\n"
        b"modes:\n  owner/product-a: merge\n  owner/product-b: review\n"
    ),
    "policy/actors.yaml": (
        b"version: 1\nhuman_identities: [github:alice]\n"
        b"agent_identities: [github:df-reviewer, github:ai-prosto]\n"
    ),
}
