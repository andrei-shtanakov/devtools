"""Self-report of the App installations at the key holder (spec §6.2; revision b1,
B1-11), moved out of `act_premerge` (df stage 3a, Task 7).

The App mode of the merge harness (`merge-pr.sh --app-id/--app-key`) needs the
JWT of `act_app_token`, hence `jwt`. `act_premerge` (the CLI `merge-pr.sh` runs)
imports this module statically; the precheck program of df imports only
`act_premerge_core`, so its admin tree (`df/precheck_tree.toml`) carries neither
this module nor `jwt`. Its derived product token is covered by the publisher's
own self-report of the installations in the same operation (N3).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from governance import act_app_token, act_trust
from governance.act_premerge_core import Installations


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
