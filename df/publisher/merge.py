"""`merge` in stage 2a (spec 5.4; Task 10): the kind of a PR is routed, a
`Precheck` hook is asked, and nothing is ever merged -- there is no
`PUT .../merge` in 2a, so an accepted precheck ends in
`refused: merge_not_in_stage_2a`.

The branch patterns are the release's own copy of
`contracts/approval-branches/v1/patterns.env` (parsed as `KEY=VALUE`, never
executed); `df` does not import `governance`, so the glob rule of the
contract header is repeated here and a test holds it equal to
`governance.approval_branches`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from fnmatch import fnmatchcase
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NoReturn, Protocol
from urllib.parse import quote

from df.config import ConfigError, read_optional
from df.publisher import github, ops
from df.publisher.github import GitHubRefused, GitHubUnavailable
from df.publisher.ops import OpContext
from df.publisher.request import Refused, Response

if TYPE_CHECKING:
    from df.publisher.main import Context

Kind = Literal["candidate", "brief", "finalize", "stop"]

#: Per-segment path templates (`*` never crosses `/`); a test holds them equal
#: to `governance.act_envelope.envelope_path` and `stop_path`.
ENVELOPE_PATH = ("workstreams", "*", "approvals", "*.yaml")
STOP_PATH = ("workstreams", "*", "approvals", "stops", "*-g*.yaml")
BRIEF_GLOB = "brief/*"
#: `compare` lists at most 300 files and does not say when it cut the list:
#: that many or more is refused (the rule of `merge-pr.sh`).
COMPARE_FILE_CAP = 300
_CANDIDATE_KEY = "APPROVAL_CANDIDATE_TEMPLATE"
_FINALIZE_KEY = "APPROVAL_FINALIZE_SUFFIX"
_LINE = re.compile(r"([A-Z][A-Z0-9_]*)=(.*)", re.ASCII)
_SHA = re.compile(r"[0-9a-f]{40}", re.ASCII)
_PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
_ADJACENT_STARS = re.compile(r"\*[-._]\*")


class Precheck(Protocol):
    """Decides whether a PR of `kind` may go on: `None` lets it, a `Refused`
    stops it. `pr` is GitHub's own object of the PR."""

    def __call__(
        self, kind: Kind, ctx: OpContext, pr: Mapping[str, object]
    ) -> Refused | None: ...


def _unavailable(kind: Kind, ctx: OpContext, pr: Mapping[str, object]) -> Refused:
    return Refused("precheck_unavailable")


#: The precheck of 2a: it never lets a PR on.
UNAVAILABLE: Precheck = _unavailable


# --- patterns -----------------------------------------------------------------


def patterns_path() -> Path:
    """`patterns.env` of the release the publisher runs from: the parent of
    the directory that holds the `df` package."""
    root = Path(__file__).resolve().parent.parent.parent
    return root / "contracts" / "approval-branches" / "v1" / "patterns.env"


def read_patterns() -> dict[str, str]:
    """The two keys of `patterns.env`; anything else than a whole file with
    both keys is `patterns_unavailable`."""
    try:
        text = read_optional(patterns_path())
    except ConfigError:
        raise Refused("patterns_unavailable") from None
    if text is None:
        raise Refused("patterns_unavailable")
    return _parse_patterns(text)


def _parse_patterns(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _LINE.fullmatch(stripped)
        value = "" if match is None else match[2].strip()
        if match is None or match[1] in found or not value:
            raise Refused("patterns_unavailable")
        found[match[1]] = value
    if not {_CANDIDATE_KEY, _FINALIZE_KEY} <= found.keys():
        raise Refused("patterns_unavailable")
    return found


def _template_glob(template: str) -> str:
    """The contract's rule: every `{...}` becomes `*`, then `*`s apart by one
    separator collapse into one, until nothing changes."""
    glob = _PLACEHOLDER.sub("*", template)
    while True:
        collapsed = _ADJACENT_STARS.sub("*", glob)
        if collapsed == glob:
            return glob
        glob = collapsed


# --- routing ------------------------------------------------------------------


def branch_kinds(head_ref: str, patterns: Mapping[str, str]) -> set[Kind]:
    """The kinds whose branch form `head_ref` has. A finalize branch is also
    of the candidate form; it is only a finalize one. `stop` has no branch
    form in 2a."""
    candidate = _template_glob(patterns[_CANDIDATE_KEY])
    finalize = candidate + patterns[_FINALIZE_KEY]
    kinds: set[Kind] = set()
    if fnmatchcase(head_ref, finalize):
        kinds.add("finalize")
    elif fnmatchcase(head_ref, candidate):
        kinds.add("candidate")
    if fnmatchcase(head_ref, BRIEF_GLOB):
        kinds.add("brief")
    return kinds


def _matches(path: str, template: Sequence[str]) -> bool:
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    return len(parts) == len(template) and all(
        fnmatchcase(part, pattern)
        for part, pattern in zip(parts, template, strict=True)
    )


def route(
    head_ref: str, paths: Sequence[str], patterns: Mapping[str, str]
) -> Kind | Refused:
    """The kind of the PR, or `ambiguous_kind`: a branch outside the forms or
    of two, a stop record among the paths (whatever the branch), a finalize
    branch with any path but an envelope or with no path at all."""
    if any(_matches(p, STOP_PATH) for p in paths):
        return Refused("ambiguous_kind")
    kinds = branch_kinds(head_ref, patterns)
    if len(kinds) != 1:
        return Refused("ambiguous_kind")
    (kind,) = kinds
    if kind == "brief" and any(_matches(p, ENVELOPE_PATH) for p in paths):
        return Refused("ambiguous_kind")
    if kind == "finalize" and not (
        paths and all(_matches(p, ENVELOPE_PATH) for p in paths)
    ):
        return Refused("ambiguous_kind")
    return kind


# --- the operation ------------------------------------------------------------


def _head_ref(pr: Mapping[str, object]) -> str:
    head = pr.get("head")
    ref = head.get("ref") if isinstance(head, dict) else None
    if not isinstance(ref, str) or not ref:
        raise GitHubUnavailable("pr: no head ref")
    return ref


def _check_head_repo(oc: OpContext, pr: Mapping[str, object]) -> None:
    """A fork picks its own branch names: only the product's own branches
    are routed by name (`head_repo_foreign`)."""
    head = pr.get("head")
    repo = head.get("repo") if isinstance(head, dict) else None
    ident = repo.get("id") if isinstance(repo, dict) else None
    if isinstance(ident, bool) or ident != oc.product.repository_id:
        raise Refused("head_repo_foreign")


def _paths_of(item: object) -> list[str]:
    """The paths a file entry puts in play: the old path of a rename counts
    too, or a rename out of `approvals/stops/` would escape the stop check."""
    if not isinstance(item, dict) or not isinstance(item.get("filename"), str):
        raise GitHubUnavailable("files: unexpected item")
    previous = item.get("previous_filename")
    if previous is None:
        return [item["filename"]]
    if not isinstance(previous, str):
        raise GitHubUnavailable("files: unexpected item")
    return [item["filename"], previous]


def _get(oc: OpContext, token: str, path: str) -> object:
    _, data = github.call(
        "GET", path, token, transport=oc.transport, api_base=oc.api_base
    )
    return data


def _base_ref(pr: Mapping[str, object]) -> str:
    base = pr.get("base")
    ref = base.get("ref") if isinstance(base, dict) else None
    if not isinstance(ref, str) or not ref:
        raise GitHubUnavailable("pr: no base ref")
    return ref


def base_tip(oc: OpContext, token: str, ref: str) -> str:
    """The live tip of the PR's base branch (the commit the compare starts
    at, as in `merge-pr.sh`)."""
    name = quote(ref, safe="")
    data = _get(oc, token, f"/repos/{oc.product.full_name}/branches/{name}")
    commit = data.get("commit") if isinstance(data, dict) else None
    sha = commit.get("sha") if isinstance(commit, dict) else None
    if not isinstance(sha, str) or _SHA.fullmatch(sha) is None:
        raise GitHubUnavailable("branch: no commit sha")
    return sha


def pr_paths(oc: OpContext, token: str, tip: str, head_sha: str) -> list[str]:
    """Every path of the diff `tip...head_sha`: two fixed commits, so the list
    cannot change under us (the PR's own files listing follows the live head).
    `COMPARE_FILE_CAP` files or more is `too_many_files`."""
    path = f"/repos/{oc.product.full_name}/compare/{tip}...{head_sha}"
    data = _get(oc, token, path)
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, list):
        raise GitHubUnavailable("compare: no files")
    if len(files) >= COMPARE_FILE_CAP:
        raise Refused("too_many_files")
    return [p for item in files for p in _paths_of(item)]


def _decide(ctx: Context, precheck: Precheck) -> NoReturn:
    oc = ops.build_context(ctx)
    patterns = read_patterns()
    ops.self_check_first(oc)
    token = ops.authorize(oc, {"pull_requests": "read"}).value
    pr_number = int(str(ctx.request.fields["pr"]))
    head_sha = str(ctx.request.fields["head_sha"])
    first = ops.read_pr(oc, token, pr_number, head_sha)
    head_ref = _head_ref(first)
    _check_head_repo(oc, first)
    tip = base_tip(oc, token, _base_ref(first))
    paths = pr_paths(oc, token, tip, head_sha)
    pr = ops.read_pr(oc, token, pr_number, head_sha)  # head and base did not move
    if _base_ref(pr) != _base_ref(first):
        raise Refused("pr_state")
    kind = route(head_ref, paths, patterns)
    if isinstance(kind, Refused):
        raise kind
    refused = precheck(kind, oc, pr)
    if refused is not None:
        raise refused
    raise Refused("merge_not_in_stage_2a")


def merge(ctx: Context, precheck: Precheck = UNAVAILABLE) -> Response:
    """`merge` (stage 2a): route the PR and ask `precheck`; always a refusal,
    for nothing is merged in 2a. Nothing external is changed, so no intent is
    written and every refusal is truthful."""
    try:
        return _decide(ctx, precheck)
    except ops.Answer as answer:
        return answer.response
    except GitHubUnavailable:
        return Response("unknown", "github_unavailable")
    except GitHubRefused as exc:
        return Response("refused", ops.refusal_reason(exc))
    except Refused as exc:
        return Response("refused", exc.reason)


def merge_op(ctx: Context) -> Response:
    """The `OPERATION_TABLE` entry of `merge`: the 2a precheck."""
    return merge(ctx)


__all__ = [
    "ENVELOPE_PATH",
    "STOP_PATH",
    "UNAVAILABLE",
    "Kind",
    "Precheck",
    "branch_kinds",
    "merge",
    "merge_op",
    "route",
]
