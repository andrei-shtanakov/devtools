"""The product side of a candidate and its finalize (stage 3a, Task 14).

On a `precheck_world.ProductRepo` (the product's git repository, served
over dumb HTTP to the fake GitHub's tokens): node `requirements` on
`master`, then the candidate PR of request `REQUEST` of workstream `WS` --
the node changed, its review manifest pinned to the current policy
activation, the edge-check evidence. The fake GitHub learns the PR, the
human's approve on its head and the product's git objects
(`trust_sources.GitRest`), and performs the one `PUT .../merge` as a
squash (`on_merge`), republishing the served copy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from governance.act_envelope import (
    DeliveredRequest,
    envelope_path,
    expected_envelope,
    render_envelope,
)
from governance.act_policy import parse_policy_set
from governance.act_projection import self_hash_v
from governance.act_request import (
    NodeEntry,
    NodeRequest,
    PolicyPin,
    manifest_path,
    parse_manifest,
    render_manifest,
)
from tests.df.fake_github import Scenario
from tests.df.precheck_world import ProductRepo, _git
from tests.df.trust_sources import (
    POLICY_REPO,
    GitRest,
    SourceRepo,
    TrustSources,
    git,
)

WS = "ws-acc3"
NODE = "requirements"
REQUEST = "approve-1-1-1"
CANDIDATE_BRANCH = f"spec/{WS}-{REQUEST}"
FINAL_BRANCH = f"{CANDIDATE_BRANCH}-final"
NODE_FILE = f"workstreams/{WS}/spec/10-requirements.md"
EVIDENCE = f"workstreams/{WS}/evidence/edge-check/requirements--charter.json"
APPROVER = "alice"
APPROVE_ID = 501


@dataclass
class Candidate:
    """A candidate PR of the product and what the fake knows of it."""

    repo: ProductRepo
    rest: GitRest
    pr: int
    head: str
    base_tip: str
    scenario: Scenario
    merged: list[str] = field(default_factory=list)


def node_text(version: int) -> str:
    """The node's file at a version."""
    return f"---\nspec_stage: requirements\n---\nтребования v{version}\n"


def manifest_text(full_name: str, activation: str, files: dict[str, bytes]) -> str:
    """The review manifest of `REQUEST`, pinned to the policy `activation`."""
    policy = parse_policy_set(activation, files)
    if not hasattr(policy, "files_sha256"):
        raise ValueError(f"policy set not parsed: {policy!r}")
    entry = NodeEntry(NODE_FILE, self_hash_v(node_text(1), "review/v1"), 1)
    req = NodeRequest(
        REQUEST,
        WS,
        full_name,
        {NODE: entry},
        {NODE: {}},
        PolicyPin(POLICY_REPO, "main", activation, dict(policy.files_sha256)),
    )
    return render_manifest(req)


def build_candidate(
    root: Path,
    full_name: str,
    repository_id: int,
    sources: TrustSources,
    scenario: Scenario,
    *,
    pr: int = 21,
) -> Candidate:
    """The product with node v0 on `master` and the candidate PR `pr`
    (head: v1, the manifest, the evidence) open against it; the fake
    GitHub's scenario learns the PR, the approve and the git objects."""
    repo = ProductRepo.create(root, full_name)
    _commit_file(repo, NODE_FILE, node_text(0), "node v0")
    activation = sources.policy.head()
    files = {
        path: _git_show(sources.policy.work, activation, path)
        for path in ("policy/approvers.env", "policy/modes.yaml", "policy/actors.yaml")
    }
    head = repo.branch(
        CANDIDATE_BRANCH,
        {
            NODE_FILE: node_text(1),
            manifest_path(WS, REQUEST): manifest_text(full_name, activation, files),
            EVIDENCE: '{"edge": "requirements--charter"}\n',
        },
    )
    repo.pulls[pr] = head
    repo.publish()
    product = SourceRepo(repository_id, full_name, repo.work, default_branch="master")
    rest = GitRest([product])
    cand = Candidate(repo, rest, pr, head, repo.tip(), scenario)
    scenario.rest = rest
    scenario.pulls[(full_name, pr)] = pull_body(cand, repository_id)
    submitted = (datetime.now(UTC) - timedelta(minutes=5)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    scenario.reviews[(full_name, pr)] = [
        {
            "id": APPROVE_ID,
            "user": {"login": APPROVER, "type": "User"},
            "state": "APPROVED",
            "commit_id": head,
            "submitted_at": submitted,
            "body": "",
        }
    ]
    scenario.on_merge = lambda _name, number: squash(cand, number)
    return cand


def pull_body(
    cand: Candidate,
    repository_id: int,
    *,
    pr: int | None = None,
    head: str | None = None,
    ref: str = CANDIDATE_BRANCH,
) -> dict[str, object]:
    """`/pulls/<n>` of an open PR (default: the candidate)."""
    repo = {"id": repository_id, "full_name": cand.repo.full_name}
    return {
        "number": cand.pr if pr is None else pr,
        "state": "open",
        "merged": False,
        "title": f"{WS}: {REQUEST}",
        "user": {"login": "df-fixture-machine", "type": "User"},
        "head": {"sha": cand.head if head is None else head, "ref": ref, "repo": repo},
        "base": {"ref": "master", "repo": repo},
    }


def build_finalize(
    cand: Candidate, scenario: Scenario, repository_id: int, *, pr: int = 22
) -> tuple[int, str]:
    """After the candidate's squash: the finalize PR `pr` off `master`
    adding exactly the envelope the delivery decision implies (the act is
    the approve `APPROVE_ID`); `(pr, head)`."""
    squash_sha = cand.merged[-1]
    text = (cand.repo.work / manifest_path(WS, REQUEST)).read_text()
    delivered = DeliveredRequest(squash_sha, cand.head, parse_manifest(text))
    envelope = render_envelope(expected_envelope(delivered, NODE, str(APPROVE_ID)))
    head = cand.repo.branch(FINAL_BRANCH, {envelope_path(WS, NODE): envelope})
    cand.repo.pulls[pr] = head
    push(cand)
    full_name = cand.repo.full_name
    body = pull_body(cand, repository_id, pr=pr, head=head, ref=FINAL_BRANCH)
    scenario.pulls[(full_name, pr)] = body
    return pr, head


def push(cand: Candidate) -> None:
    """The served copy of the product: `master` and every PR ref."""
    work = cand.repo.work
    served = cand.repo.served_root / f"{cand.repo.full_name}.git"
    refs = [f"{sha}:refs/pull/{n}/head" for n, sha in cand.repo.pulls.items()]
    _git(work, "push", "-q", "-f", str(served), "master:refs/heads/master", *refs)
    _git(served, "repack", "-a", "-d", "-q")
    _git(served, "update-server-info")


def squash(cand: Candidate, number: int) -> str:
    """The squash merge of PR `number` into `master`, republished; its SHA."""
    work = cand.repo.work
    head = cand.repo.pulls[number]
    _git(work, "merge", "-q", "--squash", head)
    _git(work, "commit", "-qm", f"PR #{number} (squash)")
    sha = _git(work, "rev-parse", "HEAD")
    cand.merged.append(sha)
    push(cand)
    merged_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    cand.scenario.commit_pulls[(cand.repo.full_name, sha)] = [
        {
            "number": number,
            "state": "closed",
            "merged_at": merged_at,
            "merge_commit_sha": sha,
            "base": {"ref": "master"},
        }
    ]
    return sha


def _commit_file(repo: ProductRepo, rel: str, text: str, message: str) -> None:
    path = repo.work / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    _git(repo.work, "add", "-A")
    _git(repo.work, "commit", "-qm", message)


def _git_show(work: Path, sha: str, path: str) -> bytes:
    return git(work, "show", f"{sha}:{path}").encode("utf-8")
