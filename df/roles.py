"""OS service roles: `df-exec`, `df-review`, `df-publisher` (stage 2a) and
`df-precheck` (stage 2b, spec 4.1 rev 10); the group `df-history` (stage 3a,
spec 4.1 rev 11): `df-publisher`, `df-precheck` and the operator.

Spec 4.1. Each role is an account created like `df-build`
(`platform.create_service_user`): nologin, its own `0700` home, no
administrative groups; its sudoers rules are `df.sudoers`'s.
"""

from __future__ import annotations

from df import platform
from df.config import Account
from df.paths import Layout

#: The role that runs the publisher's precheck (stage 2b): no rules of its own.
PRECHECK_USER = "df-precheck"
ROLE_USERS: tuple[str, ...] = ("df-exec", "df-review", "df-publisher", PRECHECK_USER)
#: The roles that run models; `models.ROLE_CONTEXT` points only at these.
MODEL_ROLE_USERS: frozenset[str] = frozenset({"df-exec", "df-review"})
#: The group that reads the history token profile (spec 4.1 rev 11, O3/O13).
HISTORY_GROUP = "df-history"
#: Its roles; the operator is its third member (`history_members`).
HISTORY_ROLES: tuple[str, ...] = ("df-publisher", PRECHECK_USER)


def ensure_roles(layout: Layout) -> dict[str, Account]:
    """Create the missing roles by intent, in `ROLE_USERS` order; all `ready`."""
    return {name: platform.create_service_user(layout, name) for name in ROLE_USERS}


def history_members(operator: str) -> tuple[str, ...]:
    """Exactly the members of `HISTORY_GROUP`: its roles and the operator."""
    return (*HISTORY_ROLES, operator)
