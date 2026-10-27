"""OS service roles of stage 2a: `df-exec`, `df-review`, `df-publisher`.

Spec 4.1. Each role is an account created like `df-build`
(`platform.create_service_user`); sudoers rules come with Task 3.
"""

from __future__ import annotations

from df import platform
from df.config import Account
from df.paths import Layout

ROLE_USERS: tuple[str, ...] = ("df-exec", "df-review", "df-publisher")
#: The roles that run models; `models.ROLE_CONTEXT` points only at these.
MODEL_ROLE_USERS: frozenset[str] = frozenset({"df-exec", "df-review"})


def ensure_roles(layout: Layout) -> dict[str, Account]:
    """Create the missing roles by intent, in `ROLE_USERS` order; all `ready`."""
    return {name: platform.create_service_user(layout, name) for name in ROLE_USERS}
