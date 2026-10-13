"""Контракт источника политики v2 (спека §6.1 п. 1, §6.2)."""

from __future__ import annotations

import pytest

from governance.act_source import SourceError, parse_source

GOOD = """\
APPROVAL_POLICY_REPO=owner/approval-policy
APPROVAL_POLICY_BRANCH=main
BOOTSTRAP_ACTIVATION=0123456789abcdef0123456789abcdef01234567
POLICY_ADMINS=owner, second-admin
"""

_SHORT_SHA = GOOD.replace(
    "BOOTSTRAP_ACTIVATION=0123456789abcdef", "BOOTSTRAP_ACTIVATION=zz"
)


def test_parses_all_keys() -> None:
    src = parse_source(GOOD)
    assert src.repo == "owner/approval-policy"
    assert src.branch == "main"
    assert src.bootstrap_activation.startswith("0123")
    assert src.policy_admins == frozenset({"owner", "second-admin"})


@pytest.mark.parametrize(
    "broken",
    [
        _SHORT_SHA,
        GOOD.replace("POLICY_ADMINS=owner, second-admin", "POLICY_ADMINS= , "),
        GOOD + "APPROVAL_POLICY_BRANCH=other\n",
        GOOD.replace("APPROVAL_POLICY_REPO=owner/approval-policy\n", ""),
    ],
    ids=["short-sha", "empty-admins", "duplicate-key", "missing-repo"],
)
def test_refuses_broken_source(broken: str) -> None:
    with pytest.raises(SourceError):
        parse_source(broken)
