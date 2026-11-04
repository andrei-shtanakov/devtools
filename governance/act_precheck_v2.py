"""Program v2 of the df precheck (stage 3a; spec 5.4 rev 11, "Протокол v2").

`act_precheck.serve` imports it only for a `df/precheck-request/v2` and only after
`precheck serve` found every file of the tree's `df/precheck_tree.toml` -- a tree an
older installer built lacks this module and still serves v1. Until stage 3a Task 7
(`op: trust`, `op: merge`) the program refuses every request `v2_unavailable`.
"""

from __future__ import annotations

from governance.act_precheck import REFUSE, RequestV2, verdict_v2

REASON_V2_UNAVAILABLE = "v2_unavailable: protocol v2 is not served yet"


def answer(request: RequestV2, admin_digest: str) -> dict[str, object]:
    """The verdict of `request` (stage 3a Task 1: the refusal `v2_unavailable`)."""
    return verdict_v2(request, REFUSE, REASON_V2_UNAVAILABLE, admin_digest)
