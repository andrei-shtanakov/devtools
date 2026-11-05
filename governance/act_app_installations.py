"""Self-report of the App installations at the key holder (spec §6.2; revision b1,
B1-11), moved out of `act_premerge` (df stage 3a, Task 7).

The App mode of the merge harness (`merge-pr.sh --app-id/--app-key`) needs the
JWT of `act_app_token`, hence `jwt`. The precheck program of df (`act_precheck`
and its v2) imports `act_premerge`, and its import closure is the admin tree
(`df/precheck_tree.toml`, every import of every module, deferred ones included):
`act_premerge` therefore names this module only by string
(`act_premerge.real_deps`), so the tree carries neither `act_app_token` nor
`jwt`. Its derived product token is covered by the publisher's own self-report
of the installations in the same operation (N3).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from governance import act_app_token, act_trust

#: `act_premerge.Installations` (not imported: the CLI runs `act_premerge` as
#: `__main__` and loads this module by name).
Installations = Callable[[act_trust.Binding], act_trust.Found]


def app_installations(app_id: int, key: Path, *, http: Any = None) -> Installations:
    """Self-report of the App installations at the key holder (§6.2; B1-11).

    The precheck does not hold the derived installation token (`merge-pr.sh`
    runs it with `GH_TOKEN=""`), so `_self_report` does not ask it; the other
    credentials of the process (`GITHUB_TOKEN`, profiles) are checked as
    usual."""

    def check(binding: act_trust.Binding) -> act_trust.Found:
        repos = {
            "approval-policy": binding.approval_policy_repository_id,
            "df-trust": binding.df_trust_repository_id,
        }
        extra = {} if http is None else {"http": http}
        return act_app_token.installations_refusal(app_id, key, repos, **extra)

    return check
