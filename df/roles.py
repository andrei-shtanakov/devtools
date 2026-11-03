"""OS service roles: `df-exec`, `df-review`, `df-publisher` (stage 2a) and
`df-precheck` (stage 2b, spec 4.1 rev 10).

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


def ensure_roles(layout: Layout) -> dict[str, Account]:
    """Create the missing roles by intent, in `ROLE_USERS` order; all `ready`."""
    return {name: platform.create_service_user(layout, name) for name in ROLE_USERS}
