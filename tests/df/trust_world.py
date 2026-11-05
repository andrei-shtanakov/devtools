"""A fake trust world for the publisher's perimeter routes (stage 3a, Task 10).

The trust sources answer through `trust_routes.HISTORY_SEND` (a route table
by URL, no network); the checker is `platform.run_supervised` replaced by a
function of the request that answers a canonical `df/precheck-verdict/v2`,
built here the way `governance.act_precheck_v2._wire` builds it (the
contract test against the real checker is `tests/test_act_trust_routes.py`).
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from df import platform
from df.paths import Layout
from df.publisher import github, perimeter, precheck, trust_routes, trust_state
from df.publisher.trust_routes import RULES_VERSION, canonical, range_digest
from df.publisher.trust_types import Finding
from df.stage2_config import TrustBinding
from tests.df.launch_world import ADMIN

API = github.API
TRUST_REPO, POLICY_REPO = "o/df-trust", "o/approval-policy"
TRUST_ID, POLICY_ID = 5, 6
BINDING = TrustBinding("github.com", TRUST_ID, TRUST_REPO, "main", POLICY_ID, "public")
S1, S2, S3 = "1" * 40, "2" * 40, "3" * 40
P = "f" * 40
TREE = "e" * 40
SOURCE_ENV = b"POLICY_REPO=o/approval-policy\nPOLICY_BRANCH=main\n"
SOURCE_DIGEST = hashlib.sha256(SOURCE_ENV).hexdigest()
TRUST_TOML = (
    'schema = "df/trust/v1"\n'
    'host = "github.com"\n'
    "df_trust_repository_id = {repo}\n"
    'df_trust_full_name = "o/df-trust"\n'
    'df_trust_branch = "{branch}"\n'
    "approval_policy_repository_id = 6\n"
    'transport = "public"\n'
)
Reply = tuple[int, Mapping[str, str], bytes]


def _json(obj: object, status: int = 200) -> Reply:
    return status, {}, json.dumps(obj).encode()


def _oid(raw: bytes) -> str:
    return hashlib.sha1(b"blob %d\x00" % len(raw) + raw).hexdigest()


def source_descriptor(digest: str = SOURCE_DIGEST) -> dict[str, object]:
    return {
        "digest": digest,
        "policy_full_name": POLICY_REPO,
        "policy_branch": "main",
        "bootstrap_activation": "c" * 40,
        "admins_count": 1,
        "admins_digest": "d" * 64,
    }


def ranges_of(epochs: int, p_sha: str) -> dict[str, object]:
    rows = [
        (n, "a" * 40, "b" * 40 if n < epochs else p_sha) for n in range(1, epochs + 1)
    ]
    return {"ranges": [list(r) for r in rows], "digest": range_digest(rows)}


def verdict(
    req: Mapping[str, object],
    *,
    outcome: str = "allow",
    relation: str | None = "descendant",
    last_epoch: int | None | str = "default",
    source: Mapping[str, object] | None | str = "default",
    findings: Sequence[Finding] = (),
    ranges: object = "default",
    unverified: Sequence[str] | None = None,
    reason: str | None = None,
    admin: str = ADMIN,
    **over: object,
) -> dict[str, object]:
    """The verdict object the checker would answer for `req` (op: trust)."""
    binding = req["binding"]
    policy = req["policy"]
    assert isinstance(binding, dict) and isinstance(policy, dict)
    accepted = req["trust_accepted"]
    if last_epoch == "default":  # A re-checked repeats A's epoch, else 1
        same = accepted is not None and accepted["sha"] == req["trust_head"]  # type: ignore[index]
        last_epoch = accepted["last_epoch"] if same else 1  # type: ignore[index]
    assert last_epoch is None or isinstance(last_epoch, int)
    if unverified is None:
        keyed = binding["history_gh_config_dir"] is not None
        unverified = [] if keyed else ["history_token_required"]
    if source == "default":
        source = None if relation is None else source_descriptor()
    if ranges == "default":
        merge = req["origin_route"] == "merge" and last_epoch is not None
        ranges = ranges_of(last_epoch, str(policy["p_sha"])) if merge else None
    if reason is None:
        names = sorted(f"{f.kind}@{f.role}" for f in findings)
        reason = "perimeter_findings: " + ", ".join(names) if findings else ""
    rows = sorted(
        ({"kind": f.kind, "role": f.role, "detail": f.detail} for f in findings),
        key=lambda r: (r["kind"], r["role"]),
    )
    data: dict[str, object] = {
        "schema": trust_routes.VERDICT_SCHEMA_V2,
        "nonce": req["nonce"],
        "request_digest": precheck.request_digest(req),
        "ctx_id": req["ctx_id"],
        "observation_id": req["observation_id"],
        "op": "trust",
        "origin_route": req["origin_route"],
        "root": {
            "host": binding["host"],
            "df_trust_repository_id": binding["df_trust_repository_id"],
            "branch": binding["branch"],
        },
        "outcome": outcome,
        "reason": reason,
        "relation": relation,
        "last_epoch": last_epoch,
        "source": source,
        "ranges": ranges,
        "policy": policy,
        "unverified": sorted(unverified),
        "version": trust_routes.PRECHECK_VERSION_V2,
        "admin_digest": admin,
        "snapshot_sha": req["trust_head"],
        "accepted_sha": None if accepted is None else accepted["sha"],  # type: ignore[index]
        "validation": f"{RULES_VERSION}:{admin}",
        "findings": rows,
        "diagnostics": "",
    }
    return {**data, **over}


def wire(data: Mapping[str, object]) -> bytes:
    """The checker's stdout for a verdict object: canonical bytes, newline."""
    return canonical(data) + b"\n"


Answer = Callable[[dict[str, object]], bytes | platform.SupervisedResult]


@dataclass
class Checker:
    """`platform.run_supervised` of the precheck command: `answer` maps the
    request to stdout (or a whole result); every request is kept."""

    answer: Answer = field(default_factory=lambda: lambda req: wire(verdict(req)))
    requests: list[dict[str, object]] = field(default_factory=list)

    def __call__(
        self, argv: Sequence[str], data: bytes, **_kw: object
    ) -> platform.SupervisedResult:
        req = json.loads(data)
        self.requests.append(req)
        out = self.answer(req)
        if isinstance(out, platform.SupervisedResult):
            return out
        return platform.SupervisedResult(0, None, out, False, 0.1)


@dataclass
class Sources:
    """The trust sources over the history transport: the branch heads, the
    `source.env` of each commit, the policy repository and the permissions
    the history token reports."""

    heads: dict[str, str] = field(default_factory=lambda: {"main": S1})
    source_env: dict[str, bytes] = field(default_factory=dict)
    policy: dict[str, object] = field(
        default_factory=lambda: {
            "id": POLICY_ID,
            "full_name": POLICY_REPO,
            "default_branch": "main",
        }
    )
    policy_head: str = P
    permissions: dict[int, object] = field(default_factory=dict)
    status: dict[str, int] = field(default_factory=dict)
    calls: list[tuple[str, Mapping[str, str]]] = field(default_factory=list)

    def __call__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> Reply:
        self.calls.append((url, dict(headers)))
        path = url.removeprefix(f"{API}/")
        if path in self.status:
            return _json({"message": "no"}, self.status[path])
        return self._answer(path)

    def _answer(self, path: str) -> Reply:
        trust = f"repos/{TRUST_REPO}"
        if path.startswith(f"{trust}/branches/"):
            head = self.heads.get(path.rsplit("/", 1)[1])
            return _json({"commit": {"sha": head}}) if head else _json({}, 404)
        if path.startswith(f"{trust}/commits/"):
            sha = path.rsplit("/", 1)[1]
            return _json({"sha": sha, "commit": {"tree": {"sha": TREE[:-2] + sha[:2]}}})
        if path.startswith(f"{trust}/git/trees/"):
            raw = self._env_of_tree(path.rsplit("/", 1)[1])
            entry = {"path": "source.env", "type": "blob", "mode": "100644"}
            return _json({"truncated": False, "tree": [{**entry, "sha": _oid(raw)}]})
        if path.startswith(f"{trust}/git/blobs/"):
            oid = path.rsplit("/", 1)[1]
            raw = next(r for r in self._all_env() if _oid(r) == oid)
            return _json(
                {"encoding": "base64", "content": base64.b64encode(raw).decode()}
            )
        if path == f"repositories/{POLICY_ID}":
            return _json({**self.policy, **self._perms(POLICY_ID)})
        if path == f"repositories/{TRUST_ID}":
            return _json({"id": TRUST_ID, **self._perms(TRUST_ID)})
        if path.startswith(f"repos/{POLICY_REPO}/branches/"):
            return _json({"commit": {"sha": self.policy_head}})
        return _json({"message": "Not Found"}, 404)

    def _perms(self, repo: int) -> dict[str, object]:
        clean = {"push": False, "maintain": False, "admin": False}
        return {"permissions": self.permissions.get(repo, clean)}

    def _all_env(self) -> list[bytes]:
        return [SOURCE_ENV, *self.source_env.values()]

    def _env_of_tree(self, tree: str) -> bytes:
        for sha, raw in self.source_env.items():
            if TREE[:-2] + sha[:2] == tree:
                return raw
        return SOURCE_ENV


@dataclass
class TrustWorld:
    """The fakes of one test, installed by `install`."""

    sources: Sources
    checker: Checker


def install(monkeypatch: pytest.MonkeyPatch) -> TrustWorld:
    """Route the history reads to `Sources` and the precheck to `Checker`."""
    world = TrustWorld(Sources(), Checker())
    monkeypatch.setattr(trust_routes, "HISTORY_SEND", world.sources)
    monkeypatch.setattr(platform, "run_supervised", world.checker)
    return world


def accept(
    lay: Layout,
    head: str,
    epoch: int,
    *,
    source_digest: str = SOURCE_DIGEST,
    binding: TrustBinding = BINDING,
    admin: str = ADMIN,
) -> None:
    """An accepted head `head` at `epoch`, as a clean `trust-status` pair
    accepts it (the check version of the world's release)."""
    with perimeter.open_db(lay) as db:
        root = binding.root_id
        found = trust_state.accepted_head(db.conn, root)
        accepted = None if found is None else found.sha
        ctx = trust_state.ctx_id(
            "trust-status", binding, admin, RULES_VERSION, accepted, head, None
        )
        obs = trust_state.open_observation(
            db,
            root,
            ctx,
            route="trust-status",
            binding=binding,
            admin_digest=admin,
            rules=RULES_VERSION,
            accepted=accepted,
            head=head,
            policy=None,
        )
        out = trust_state.advance_head(
            db,
            root,
            obs.id,
            lambda: head,
            last_epoch=epoch,
            source_digest=source_digest,
        )
        assert out == "accepted"


def write_trust(lay: Layout, *, repo: int = TRUST_ID, branch: str = "main",
                history: Path | None = None) -> None:  # fmt: skip
    """`trust.toml` of the world (with `history_gh_config_dir` if given)."""
    text = TRUST_TOML.format(repo=repo, branch=branch)
    if history is not None:
        text += f'history_gh_config_dir = "{history}"\n'
    (lay.etc / "trust.toml").write_text(text)
