"""Live stand acceptance of stages 2a and 2b: the GitHub rows of spec 4.5
(2a Task 15; the merge rows on the stop route, 2b Task 11).

Run only by `.github/workflows/df-stage2-live.yml` (R15-6), as root, in the
disposable runners of the environment `df-stage2-live` (the owner's approval,
`master` only), in three phases:

    python -m tests.df.live_stand install --source-sha SHA --out DIR
    python -m tests.df.live_stand stand --source-sha SHA --out DIR \\
        --secrets DIR --repo-id N --app-id N --installation-id N --tag TAG
    python -m tests.df.live_stand wipe --out DIR --secrets DIR \\
        [--repo-id N --tag TAG]

`install` exists before any secret does (the workflow writes them in a later
step): the implementation under test, installed as in Task 14 -- the fixture
repo of `FIXTURE_FROM_CHECKOUT` built from this checkout at `github.sha`, one
committed `bootstrap init` into `/` -- then the provenance check (r3 N2) of
two identities never equated: `source_sha` (the workflow's checkout) and
`fixture_sha` (the commit the install took). `stand` checks provenance again
before it reads a secret, then `roles setup`, the stand's `products.toml`
from the variables (a fixture, noted), `setup-agent` from the secret files,
the publisher's `self-check` (an extra App installation is an infrastructure
failure, step H2), a fixture run record (as Task 14: `run start` stops at
the probes in v1), the rows, the cleanup (row 9), the revocation of the
installation tokens it was given (`DELETE /installation/token`, best
effort, logged) and the host teardown; the evidence is scanned for every
in-process secret whatever happens. `wipe` (always) scans the evidence for
the file secrets, sweeps the stand of a run cut short before row 9
(`tag_branches`; a failure of it never skips the local removals), removes
the installed copies and the secret files.

Rows (each with its mechanism of refusal and a feasibility control, r1 N12):
1 the machine user opens `brief/stand-<tag>` and `stand/<tag>`; 2
`publish-review` and its identical repeat; 3 reconciliation after a lost
result; 4 a moved head: (a) the old head is `pr_state`, (b) through
`tests/df/gate_proxy.py` the review stays on the head read before the push;
5 `read-token`: (a) a private clone passes; (b) a push, a review, a PATCH
and a merge with it are refused by GitHub `403`, each with its control
(R15-1: the merge control is a deliberately conflicting PR, `405` with the
full installation token); 6 the token in no `ps` snapshot (at least one
strictly inside the read), publisher file, sudo I/O log or `.git/config`; 7 sudo refuses the other role's operation; 8 `merge` routes
`brief/…` to `brief_precheck_unavailable` and `stand/…` to `ambiguous_kind`;
stage 2b (plan decision 13): 10 the seed of `stand-<tag>` -- a technical PR
with a `review/v1` manifest of node `n1`, squash-merged by the machine user
(not an acceptance of candidate, no human act), seen by the checker's
`load_history` as `DeliveredRequest` g1 -- and the stop-PR on
`stop_branch("stand-<tag>", "n1", 1)`; 11 `merge` of it with the `PUT` held
by the proxy while the head moves: `refused: head_changed`, the repeat the
same with one `PUT`; 12 a new seed and stop-PR (`stand-<tag>-b`), the `PUT`'s
answer cut by the proxy: `done` by reconciliation, the repeat `done`
replayed with one `PUT`; 9 every open PR closed and branch deleted (the
seeds' and row 12's merges stay on the default branch, named in the
evidence). A row runs unless a row it needs (`Row.needs`) did not pass; a
skipped row is recorded and fails the run.

Exit codes: 0 every row passed, 1 a row failed, 2 infrastructure. No pytest
test runs a phase or reads `DF_STAND_*`; `tests/df/test_live_stand.py` tests
the pure helpers offline.
"""

from __future__ import annotations

import argparse
import base64
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from df import launch, models, run_start, setup_agent
from df import platform as plat
from df.admin import expected_admin_digest
from df.admin_tree import locked_pins
from df.build import GIT_ISOLATION
from df.config import parse_releases
from df.paths import MACHINE_TOKEN, Layout
from df.publisher import github, merge, ops, request, store
from df.publisher.github import GitHubRefused, GitHubUnavailable
from df.publisher.request import BadResponse, Response
from df.release import RELEASE_JSON
from df.stage2_config import (
    DEFAULT_API_BASE,
    DEFAULT_GIT_BASE,
    Product,
    RunRecord,
    dump_products,
    dump_publisher,
    parse_publisher,
)
from governance.act_envelope import (
    DeliveredStop,
    History,
    StopRecord,
    node_history,
    render_stop,
    stop_path,
)
from governance.act_forge_http import TokenTransport
from governance.act_history import load_history
from governance.act_history_snapshot import (
    DeadlineExceeded,
    Scope,
    SnapshotHistory,
    open_snapshot,
)
from governance.act_request import (
    NodeEntry,
    NodeRequest,
    PolicyPin,
    manifest_path,
    render_manifest,
)
from governance.act_time import Unavailable
from governance.approval_branches import stop_branch
from tests.df import gate_proxy
from tests.df.ci_step0 import BASES, make_base
from tests.df.fake_github import FakeGitHub
from tests.df.gate_proxy import Seen, serve_gate_proxy
from tests.df.roles_world import (
    FIXTURE_FROM_CHECKOUT,
    OPENSSL,
    OPERATOR,
    OS_DIR,
    PRODUCTION,
    SUDO,
    RolesWorld,
    approved_uv,
    as_operator,
    as_user,
    check,
    install_production,
    new_request_id,
    require_clean_host,
    shown,
    sudo_refused,
    teardown_install,
    teardown_roles,
    write_etc,
)
from tests.df.root_world import (
    INFRA,
    PIN_NAME,
    REPO_ROOT,
    SYSTEM_GIT,
    FixtureRepo,
    InstalledWorld,
    RootWorld,
    build_fixture_repo,
    current_id,
    infra,
    serve_repo,
)

EXIT_PASS = 0
EXIT_FAILED = 1
EXIT_INFRA = 2
#: Where the install and the stand phase meet: root `0755` under the R83 base.
LIVE_DIR = "df-live"
STATE_FILE = "state.json"
APP_KEY_FILE = "app-key.pem"
TOKEN_FILE = "machine-token"
GITHUB_WEB = "https://github.com"
#: sudo's default `iolog_dir` on both OSes; df's sudoers sets `!log_output`.
IOLOG_DIRS = (Path("/var/log/sudo-io"),)
#: `ps` every 100 ms during the read (row 6); BSD syntax works on both OSes.
PS_ARGV = ("/bin/ps", "axww", "-o", "args")
PS_PERIOD = 0.1
#: Seconds to wait for the held review POST, for the head to move while it
#: is held (below `gate_proxy.HOLD_LIMIT`), and for `mergeable`.
HELD_WAIT = 180.0
HEAD_WAIT = 10.0
#: Row 4(b) polls the head this often while the POST is held.
HEAD_POLL = 0.5
MERGEABLE_WAIT = 120.0
POLL = 2.0
#: Rows 11 and 12: the publisher reaches its `PUT` after the precheck (whose
#: client waits `PRECHECK_WAIT` = 200 s at most) and the attestation.
PUT_WAIT = 300.0
#: Row 10's control: how long GitHub may take to list the seed's merge
#: commit under its PR (`commits/{sha}/pulls`), and the snapshot's deadline.
DELIVERY_WAIT = 60.0
SNAPSHOT_DEADLINE = 120.0
#: The node of the seeded request and of the stop record (rows 10-12).
NODE = "n1"
#: Row 12: answers of the identical request (`POLL` apart) while GitHub's
#: read still lags the cut `PUT` (spec 5.4, the operator's step 2); the
#: replay after the settled answer is not counted.
RECONCILE_TRIES = 4
#: The one answer a repeat may give while the read lags.
LAGGING = (3, "unknown", "merge_not_confirmed")
FORBIDDEN = "Resource not accessible by integration"
_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,63}", re.ASCII)
_LS_TREE = re.compile(r"([0-7]{6}) (blob|tree|commit) ([0-9a-f]{40,64})\t(.+)")
#: Git of the stand: no system or global config (no credential helper), no
#: prompt, a fixed identity for the empty commits.
GIT_ENV: Mapping[str, str] = {
    **GIT_ISOLATION,
    "GIT_AUTHOR_NAME": "df-live-stand",
    "GIT_AUTHOR_EMAIL": "df-live-stand@acceptance.invalid",
    "GIT_COMMITTER_NAME": "df-live-stand",
    "GIT_COMMITTER_EMAIL": "df-live-stand@acceptance.invalid",
}


class RowFailed(Exception):
    """A row's mechanism did not hold: the acceptance fails (not infra)."""


class ProvenanceError(Exception):
    """The installed code is not provably the checkout's: infrastructure."""


def expect(condition: bool, what: str) -> None:
    """A row's assertion: `RowFailed(what)` unless `condition`."""
    if not condition:
        raise RowFailed(what)


# ---------- secrets never written anywhere ----------


class SecretGuard:
    """The secret values of this process; evidence that holds one is refused."""

    def __init__(self) -> None:
        self._values: list[str] = []

    def add(self, value: str) -> None:
        """Remember `value` (and its base64 forms are added by the caller)."""
        if len(value) >= 16 and value not in self._values:
            self._values.append(value)

    def values(self) -> list[str]:
        return list(self._values)

    def found(self, data: str | bytes) -> bool:
        """Whether `data` holds any remembered value."""
        blob = data.encode("utf-8", "replace") if isinstance(data, str) else data
        return any(v.encode("utf-8") in blob for v in self._values)


GUARD = SecretGuard()


def key_needles(pem: str) -> list[str]:
    """The base64 body lines of a PEM key: what a leak of it would contain."""
    return [line for line in pem.splitlines() if line and not line.startswith("-")]


def auth_header(user: str, token: str) -> str:
    """`Authorization: Basic` for git over HTTPS (`user:token`)."""
    pair = base64.b64encode(f"{user}:{token}".encode()).decode("ascii")
    return f"Authorization: Basic {pair}"


def guard_token(token: str, user: str) -> None:
    """Remember a token and the header git would carry it in."""
    GUARD.add(token)
    GUARD.add(auth_header(user, token).split(" ", 2)[2])


def scan_tree(root: Path, needles: Sequence[str]) -> list[Path]:
    """Files under `root` holding any of `needles` (bytes)."""
    if not root.exists():
        return []
    wanted = [n.encode("utf-8") for n in needles if n]
    found: list[Path] = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            path = Path(dirpath) / name
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if any(n in data for n in wanted):
                found.append(path)
    return sorted(found)


def record(out: Path, name: str, data: object) -> None:
    """`<out>/live/<os>/<name>`, refused if it holds a secret of `GUARD`."""
    text = data if isinstance(data, str) else json.dumps(data, indent=2, default=str)
    if GUARD.found(text):
        infra(f"evidence {name}: в тексте секрет; не записано")
    path = out / "live" / OS_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


# ---------- provenance (r3 N2) ----------


@dataclass(frozen=True)
class Provenance:
    """The two identities and the digest, after they were checked."""

    source_sha: str
    fixture_sha: str
    release_id: str
    admin_digest: str
    blobs: Mapping[str, str]

    def to_json(self) -> dict[str, object]:
        return {
            "source_sha": self.source_sha,
            "fixture_sha": self.fixture_sha,
            "release_id": self.release_id,
            "admin_digest": self.admin_digest,
            "paths": list(FIXTURE_FROM_CHECKOUT),
            "blobs": dict(self.blobs),
        }


def git_out(repo: Path, *args: str) -> str:
    """`git -C repo args` (read only, no config); a failure is a
    `ProvenanceError`."""
    env = {"PATH": "/usr/bin:/bin", "LANG": "C", **GIT_ISOLATION}
    argv = [str(SYSTEM_GIT), "-c", "safe.directory=*", "-C", str(repo), *args]
    done = subprocess.run(
        argv, capture_output=True, text=True, env=env, timeout=60, check=False
    )
    if done.returncode != 0:
        why = done.stderr.strip()[:300]
        raise ProvenanceError(f"git {' '.join(args)} в {repo}: {why}")
    return done.stdout


def parse_ls_tree(text: str) -> dict[str, str]:
    """`git ls-tree -r -z` output as `{path: object}` (blobs only)."""
    pairs: dict[str, str] = {}
    for item in text.split("\0"):
        if not item:
            continue
        found = _LS_TREE.fullmatch(item)
        if found is None:
            raise ProvenanceError(f"ls-tree: нераспознанная строка {item[:120]!r}")
        if found[2] == "blob":
            pairs[found[4]] = found[3]
    return pairs


def tree_blobs(repo: Path, sha: str, paths: Sequence[str]) -> dict[str, str]:
    """`(path, blob)` of `paths` at `sha` in `repo`; a path with nothing
    under it is a `ProvenanceError`."""
    pairs = parse_ls_tree(git_out(repo, "ls-tree", "-r", "-z", sha, "--", *paths))
    for wanted in paths:
        if not any(p == wanted or p.startswith(f"{wanted}/") for p in pairs):
            raise ProvenanceError(f"{repo}@{sha}: нет {wanted}")
    return pairs


def release_member_sha(text: str, name: str) -> str:
    """The `sha` of the one repo named `name` in `release.json`."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ProvenanceError(f"release.json: не JSON: {exc}") from exc
    repos = data.get("repos") if isinstance(data, dict) else None
    named = [r for r in repos or [] if isinstance(r, dict) and r.get("name") == name]
    if len(named) != 1 or not isinstance(named[0].get("sha"), str):
        raise ProvenanceError(f"release.json: член {name!r} не один или без sha")
    return str(named[0]["sha"])


def check_provenance(
    checkout: Path,
    source_sha: str,
    fixture_repo: Path,
    fixture_sha: str,
    layout: Layout,
) -> Provenance:
    """r3 N2: (0) the checkout is at `source_sha`; (1) the `(path, blob)`
    pairs of `FIXTURE_FROM_CHECKOUT` at `fixture_sha` in the fixture repo
    equal those of the checkout at `source_sha`; (2) the active release's
    `release.json` names member `devtools` at `fixture_sha`; (3) its
    `admin_digest` in `releases.toml` equals `expected_admin_digest` of the
    checkout (its whole admin tree, `site/` from the install's cache). Any difference is a `ProvenanceError`."""
    head = git_out(checkout, "rev-parse", "HEAD").strip()
    if head != source_sha:
        raise ProvenanceError(f"чекаут на {head}, а source_sha {source_sha}")
    want = tree_blobs(checkout, source_sha, FIXTURE_FROM_CHECKOUT)
    have = tree_blobs(fixture_repo, fixture_sha, FIXTURE_FROM_CHECKOUT)
    if have != want:
        differ = sorted(p for p in {*want, *have} if want.get(p) != have.get(p))
        raise ProvenanceError(f"blob-ы фикстуры и чекаута расходятся: {differ[:10]}")
    rid = current_id(layout)
    if rid is None:
        raise ProvenanceError("нет current")
    try:
        text = (layout.release_dir(rid) / RELEASE_JSON).read_text()
        registry = parse_releases((layout.etc / "releases.toml").read_text())
    except OSError as exc:
        raise ProvenanceError(f"запись релиза {rid}: {exc}") from exc
    member = release_member_sha(text, PIN_NAME)
    if member != fixture_sha:
        raise ProvenanceError(f"release.json: {PIN_NAME} {member}, а не {fixture_sha}")
    entry = next((e for e in registry.releases if e.id == rid), None)
    expected = expected_admin_digest(checkout, layout=layout, git=SYSTEM_GIT)
    if entry is None or entry.admin_digest != expected:
        found = None if entry is None else entry.admin_digest
        raise ProvenanceError(f"admin_digest {found}, а из чекаута {expected}")
    return Provenance(source_sha, fixture_sha, rid, expected, want)


# ---------- the secrets ----------


@dataclass(frozen=True)
class StandSecrets:
    """The secret files the workflow wrote (`0600`) and the token's value."""

    app_key: Path
    token_file: Path
    machine_token: str = field(repr=False)

    @property
    def token_type(self) -> str:
        """The token's type for the evidence (R15-2), never its value."""
        if self.machine_token.startswith("ghp_"):
            return "classic PAT (ghp_)"
        if self.machine_token.startswith("github_pat_"):
            return "fine-grained PAT (github_pat_)"
        return "неизвестный"


def read_secrets(directory: Path) -> StandSecrets:
    """The two secret files; their values join `GUARD` at once."""
    key, token_file = directory / APP_KEY_FILE, directory / TOKEN_FILE
    try:
        pem = key.read_text()
        token = token_file.read_text().strip()
    except OSError as exc:
        infra(f"секреты стенда в {directory}: {type(exc).__name__}")
        raise
    for line in key_needles(pem):
        GUARD.add(line)
    GUARD.add(token)
    return StandSecrets(key, token_file, token)


def load_state(top: Path) -> dict[str, str]:
    """What the install phase left for the stand phase."""
    try:
        data = json.loads((top / STATE_FILE).read_text())
    except (OSError, ValueError) as exc:
        raise ProvenanceError(f"{top / STATE_FILE}: {exc}") from exc
    if not isinstance(data, dict):
        raise ProvenanceError(f"{top / STATE_FILE}: не объект")
    return {str(k): str(v) for k, v in data.items()}


def gate(
    source_sha: str,
    secrets_dir: Path,
    *,
    top: Path,
    layout: Layout,
    checkout: Path = REPO_ROOT,
    read: Callable[[Path], StandSecrets] = read_secrets,
) -> tuple[Provenance, StandSecrets]:
    """Provenance first; the secrets are read only once it passed."""
    state = load_state(top)
    prov = check_provenance(
        checkout,
        source_sha,
        Path(state["fixture_repo"]),
        state["fixture_sha"],
        layout,
    )
    return prov, read(secrets_dir)


# ---------- GitHub as the machine user ----------


@dataclass
class MachineGitHub:
    """REST calls of the machine user (its token) on the stand."""

    token: str = field(repr=False)
    full_name: str = ""

    def call(self, method: str, path: str, body: object | None = None) -> object:
        """One call; 4xx is `GitHubRefused`, other failures `GitHubUnavailable`."""
        return github.call(method, path, self.token, body)[1]

    def repo(self, rest: str) -> str:
        return f"/repos/{self.full_name}{rest}"

    def pull(self, number: int) -> dict[str, object]:
        data = self.call("GET", self.repo(f"/pulls/{number}"))
        if not isinstance(data, dict):
            raise GitHubUnavailable(f"pulls/{number}: не объект")
        return data

    def reviews(self, number: int) -> list[dict[str, object]]:
        items = github.paged_array(self.repo(f"/pulls/{number}/reviews"), self.token)
        return [i for i in items if isinstance(i, dict)]

    def ref_sha(self, branch: str) -> str | None:
        """The tip of `branch`, `None` if GitHub has no such ref."""
        try:
            data = self.call("GET", self.repo(f"/git/ref/heads/{branch}"))
        except GitHubRefused as exc:
            if exc.status == 404:
                return None
            raise
        obj = data.get("object") if isinstance(data, dict) else None
        sha = obj.get("sha") if isinstance(obj, dict) else None
        if not isinstance(sha, str):
            raise GitHubUnavailable(f"ref {branch}: нет sha")
        return sha


def head_of(pr: Mapping[str, object]) -> str:
    """`head.sha` of a PR object."""
    head = pr.get("head")
    sha = head.get("sha") if isinstance(head, dict) else None
    if not isinstance(sha, str):
        raise GitHubUnavailable("pr: нет head.sha")
    return sha


def login_of(item: Mapping[str, object]) -> tuple[object, object]:
    """`(user.login, user.type)` of a PR or a review."""
    user = item.get("user")
    if not isinstance(user, dict):
        return None, None
    return user.get("login"), user.get("type")


def refusal(call: Callable[[], object]) -> GitHubRefused | None:
    """The `GitHubRefused` of `call`, `None` if it passed."""
    try:
        call()
    except GitHubRefused as exc:
        return exc
    return None


# ---------- the stand ----------


@dataclass(frozen=True)
class StandArgs:
    """The stand phase's arguments (the workflow's variables)."""

    out: Path
    secrets: Path
    source_sha: str
    repo_id: int
    app_id: int
    installation_id: int
    tag: str


@dataclass
class Stand:
    """The installed world with the stand's GitHub facts and what the rows
    made (for the cleanup)."""

    rw: RolesWorld
    args: StandArgs
    secrets: StandSecrets
    gh: MachineGitHub
    product: Product
    default_branch: str
    bot: str
    run_id: str = ""
    prs: dict[str, int] = field(default_factory=dict)
    heads: dict[str, str] = field(default_factory=dict)
    branches: list[str] = field(default_factory=list)
    merges: dict[str, str] = field(default_factory=dict)
    carried: dict[str, object] = field(default_factory=dict, repr=False)
    revocable: dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def layout(self) -> Layout:
        return self.rw.layout

    @property
    def full_name(self) -> str:
        return self.product.full_name

    def record(self, name: str, data: object) -> None:
        record(self.args.out, name, data)

    def ask(self, role: str, op: str, data: bytes) -> tuple[int, Response]:
        """`role` → `sudo -u df-publisher df-publish <op>`; exit code and
        the one response line (its text never shown: it may hold a token)."""
        return self.answer(op, self.rw.publish(role, op, data))

    def answer(
        self, op: str, done: subprocess.CompletedProcess[bytes]
    ) -> tuple[int, Response]:
        """The exit code and the one response line of a finished `op`."""
        lines = done.stdout.decode("utf-8", "replace").splitlines()
        try:
            if len(lines) != 1:
                raise BadResponse(f"{len(lines)} строк")
            return done.returncode, request.parse_response(lines[0])
        except BadResponse as exc:
            err = done.stderr.decode("utf-8", "replace").strip()[-400:]
            if GUARD.found(err):
                err = "<скрыто: секрет>"
            raise RowFailed(
                f"{op}: ответа нет ({exc}); код {done.returncode}; {err}"
            ) from None

    def make_branch(self, name: str, sha: str) -> None:
        self.gh.call(
            "POST", self.gh.repo("/git/refs"), {"ref": f"refs/heads/{name}", "sha": sha}
        )
        self.branches.append(name)

    def put_file(self, branch: str, path: str, text: str) -> str:
        """A commit of `path` = `text` on `branch` (contents API); its sha."""
        body: dict[str, object] = {
            "message": f"df live stand {self.args.tag}: {path}",
            "content": base64.b64encode(text.encode()).decode("ascii"),
            "branch": branch,
        }
        try:
            data = self.gh.call("GET", self.gh.repo(f"/contents/{path}?ref={branch}"))
        except GitHubRefused as exc:
            if exc.status != 404:
                raise
            data = None
        if isinstance(data, dict) and isinstance(data.get("sha"), str):
            body["sha"] = data["sha"]
        made = self.gh.call("PUT", self.gh.repo(f"/contents/{path}"), body)
        commit = made.get("commit") if isinstance(made, dict) else None
        sha = commit.get("sha") if isinstance(commit, dict) else None
        if not isinstance(sha, str):
            raise GitHubUnavailable(f"contents {path}: нет commit.sha")
        return sha

    def open_pr(self, name: str, head: str, base: str) -> int:
        data = self.gh.call(
            "POST",
            self.gh.repo("/pulls"),
            {
                "title": f"df live stand {self.args.tag}: {name}",
                "head": head,
                "base": base,
                "body": "df stage-2a live stand; closed by the run itself (row 9).",
            },
        )
        number = data.get("number") if isinstance(data, dict) else None
        if not isinstance(number, int):
            raise GitHubUnavailable("pulls: нет number")
        self.prs[name] = number
        return number

    def wait_head(
        self, number: int, sha: str, deadline: float, *, period: float = 1.0
    ) -> bool:
        """Until the PR's head is `sha` or `deadline` (monotonic), polled
        every `period` seconds."""
        while True:
            if head_of(self.gh.pull(number)) == sha:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(period)

    def bot_reviews(self, number: int) -> list[dict[str, object]]:
        """The PR's reviews by the App's bot."""
        return [r for r in self.gh.reviews(number) if login_of(r) == (self.bot, "Bot")]


def review_payload(st: Stand, pr: int, head_sha: str, tag: str) -> bytes:
    """A `publish-review` request (`comment`) of the fixture run."""
    data = pr_fields(st, pr, head_sha, tag)
    data |= {"decision": "comment", "body": f"df live stand {st.args.tag}: {tag}"}
    return json.dumps(data, sort_keys=True).encode("utf-8")


def merge_payload(st: Stand, pr: int, head_sha: str, tag: str) -> bytes:
    """A `merge` request of the fixture run."""
    return json.dumps(pr_fields(st, pr, head_sha, tag), sort_keys=True).encode()


def pr_fields(st: Stand, pr: int, head_sha: str, tag: str) -> dict[str, object]:
    return {
        "schema": request.REQUEST_SCHEMA,
        "request_id": new_request_id(tag),
        "run_id": st.run_id,
        "repository_id": st.args.repo_id,
        "pr": pr,
        "head_sha": head_sha,
    }


def review_key(st: Stand, data: bytes) -> str:
    """The idempotency key the publisher derives for `df-review`'s `data`."""
    req = request.parse("publish-review", data)
    return store.idempotency_key(
        st.rw.uid("df-review"), "publish-review", request.digest(req)
    )


def marked(reviews: Sequence[Mapping[str, object]], key: str, sha: str) -> list[int]:
    """Ids of the reviews carrying the publisher's marker of `key` at `sha`."""
    suffix = "\n\n" + ops.MARKER.format(key=key, sha=sha)
    return [
        int(str(r.get("id")))
        for r in reviews
        if str(r.get("body") or "").endswith(suffix)
    ]


# ---------- rows ----------


def row1_open_prs(st: Stand) -> dict[str, object]:
    """Row 1: the machine user (the token `setup-agent` gave `df-exec`) opens
    `brief/stand-<tag>` and `stand/<tag>`, one file each. Control: both are
    open, authored by `machine_user`, from the stand repo, on the default
    branch."""
    tip = st.gh.ref_sha(st.default_branch)
    if tip is None:
        infra(f"нет ветки {st.default_branch}")
    assert tip is not None
    names = tag_branches(st.args.tag)
    branches = {"brief": names["brief"], "stand": names["stand"]}
    for name, branch in branches.items():
        st.make_branch(branch, tip)
        st.heads[name] = st.put_file(branch, f"df-stand/{st.args.tag}/{name}.md", "1\n")
        st.open_pr(name, branch, st.default_branch)
    seen = {}
    for name, number in st.prs.items():
        pr = st.gh.pull(number)
        base = pr.get("base")
        head = pr.get("head")
        repo = head.get("repo") if isinstance(head, dict) else None
        seen[name] = {
            "number": number,
            "state": pr.get("state"),
            "author": login_of(pr)[0],
            "head_sha": head_of(pr),
            "head_repo_id": repo.get("id") if isinstance(repo, dict) else None,
            "base": base.get("ref") if isinstance(base, dict) else None,
        }
    st.record("row1-prs.json", {"default_branch": st.default_branch, "prs": seen})
    for name, item in seen.items():
        expect(item["state"] == "open", f"{name}: PR не открыт")
        expect(item["author"] == st.product.machine_user, f"{name}: автор {item}")
        expect(item["head_repo_id"] == st.args.repo_id, f"{name}: не из стенда")
        expect(item["base"] == st.default_branch, f"{name}: база {item['base']}")
        expect(item["head_sha"] == st.heads[name], f"{name}: голова {item}")
    return {"prs": dict(st.prs)}


def row2_review_once(st: Stand) -> dict[str, object]:
    """Row 2: `df-review` → `publish-review` `comment` on `brief/…`: a review
    of the App's bot with the marker on `head_sha`; the identical repeat is
    answered with the same `review_id` (the stored result) and no second
    review exists. Control: exactly one marked review after the first."""
    pr, head = st.prs["brief"], st.heads["brief"]
    data = review_payload(st, pr, head, "row2")
    key = review_key(st, data)
    rc1, first = st.ask("df-review", "publish-review", data)
    after_first = marked(st.bot_reviews(pr), key, head)
    rc2, again = st.ask("df-review", "publish-review", data)
    after_again = marked(st.bot_reviews(pr), key, head)
    st.record(
        "row2-review.json",
        {
            "first": [rc1, first.outcome, first.reason, dict(first.result)],
            "repeat": [rc2, again.outcome, again.reason, dict(again.result)],
            "repeat_evidence": dict(again.evidence),
            "bot": st.bot,
            "marked_after_first": after_first,
            "marked_after_repeat": after_again,
        },
    )
    expect((rc1, first.outcome) == (0, "done"), f"первый запрос: {first}")
    review_id = first.result.get("review_id")
    expect(first.result.get("commit_id") == head, f"commit_id {first.result}")
    expect(after_first == [review_id], f"контроль: ревью бота {after_first}")
    expect((rc2, again.outcome) == (0, "done"), f"повтор: {again}")
    expect(again.result.get("review_id") == review_id, f"повтор: {again.result}")
    expect(after_again == [review_id], f"второе ревью: {after_again}")
    st.carried["row2"] = (data, key, review_id)
    return {"review_id": review_id}


def row3_reconcile(st: Stand) -> dict[str, object]:
    """Row 3: root removes `results/<key>.json` (a crash after the action,
    before the result); the repeat reconciles with GitHub: `done` with the
    same `review_id`, no second POST (one marked review), and the result
    stored again. Control: the intent and the result existed before."""
    data, key, review_id = cast(tuple[bytes, str, int], st.carried["row2"])
    pr, head = st.prs["brief"], st.heads["brief"]
    base = st.layout.publisher_dir
    result = base / "results" / f"{key}.json"
    intent = base / "intents" / f"{key}.json"
    if not (result.is_file() and intent.is_file()):
        infra(f"контроль: нет {result} или {intent}")
    os.unlink(result)
    rc, resp = st.ask("df-review", "publish-review", data)
    after = marked(st.bot_reviews(pr), key, head)
    rewritten = result.is_file()
    st.record(
        "row3-reconcile.json",
        {
            "removed": str(result),
            "answer": [rc, resp.outcome, resp.reason, dict(resp.result)],
            "evidence": dict(resp.evidence),
            "marked": after,
            "result_rewritten": rewritten,
        },
    )
    expect((rc, resp.outcome) == (0, "done"), f"сверка: {resp}")
    expect(resp.result.get("review_id") == review_id, f"сверка: {resp.result}")
    expect(after == [review_id], f"второй POST: {after}")
    expect(rewritten, f"сверка не записала результат: {result}")
    return {"review_id": review_id}


def row4a_old_head(st: Stand) -> dict[str, object]:
    """Row 4(a): after a push by the machine user, a request with the old
    `head_sha` is `refused: pr_state`. Control: row 2 (a prerequisite) --
    a `publish-review` with the PR's current head is `done`, so the refusal
    is about the head, not every request."""
    pr, old = st.prs["brief"], st.heads["brief"]
    branch = tag_branches(st.args.tag)["brief"]
    new = st.put_file(branch, f"df-stand/{st.args.tag}/brief.md", "2\n")
    if not st.wait_head(pr, new, time.monotonic() + 60):
        infra(f"голова PR {pr} не стала {new}")
    st.heads["brief"] = new
    rc, resp = st.ask(
        "df-review", "publish-review", review_payload(st, pr, old, "row4a")
    )
    st.record(
        "row4a-old-head.json",
        {"old": old, "new": new, "answer": [rc, resp.outcome, resp.reason]},
    )
    expect((rc, resp.outcome, resp.reason) == (2, "refused", "pr_state"), str(resp))
    return {"old": old, "new": new}


@contextmanager
def api_base(st: Stand, url: str) -> Iterator[None]:
    """`publisher.toml`'s `api_base` = `url` (root key, as Task 14), back
    to the default after."""
    lay = st.layout
    cfg = parse_publisher((lay.etc / "publisher.toml").read_text())
    write_etc(lay, "publisher.toml", dump_publisher(replace(cfg, api_base=url)))
    try:
        yield
    finally:
        write_etc(lay, "publisher.toml", dump_publisher(cfg))
        back = parse_publisher((lay.etc / "publisher.toml").read_text()).api_base
        if back != DEFAULT_API_BASE:
            infra(f"api_base не вернулся: {back}")


def row4b_window(st: Stand) -> dict[str, object]:
    """Row 4(b): the window between the publisher's read and its POST,
    through the real publisher and sudo: `api_base` is `gate_proxy` (a
    non-standard API, a proxy to api.github.com), which holds the review
    POST; the machine user pushes Y meanwhile; released, the publisher
    answers `done` with `commit_id = X`; on GitHub the bot's review of this
    request (its marker) is at X, and the bot has no review at Y. Controls:
    the POST was held, and the PR's head was Y before it was released."""
    pr, x = st.prs["brief"], st.heads["brief"]
    branch = tag_branches(st.args.tag)["brief"]
    data = review_payload(st, pr, x, "row4b")
    box: dict[str, subprocess.CompletedProcess[bytes]] = {}

    def publish() -> None:
        box["done"] = st.rw.publish("df-review", "publish-review", data)

    with serve_gate_proxy() as proxy, api_base(st, proxy.url):
        worker = threading.Thread(target=publish, daemon=True)
        worker.start()
        held = wait_held(proxy.held, worker.is_alive, HELD_WAIT)
        y, moved = "", False
        if held:
            deadline = time.monotonic() + HEAD_WAIT
            y = st.put_file(branch, f"df-stand/{st.args.tag}/brief.md", "3\n")
            moved = st.wait_head(pr, y, deadline, period=HEAD_POLL)
        proxy.release()
        worker.join(600)
        seen = proxy.requests()
    done = box.get("done")
    if done is None or not held:
        why = "нет ответа" if done is None else shown(done)
        infra(f"окно не открылось (POST не дошёл до прокси): {why}")
    assert done is not None
    st.heads["brief"] = y
    lines = done.stdout.decode("utf-8", "replace").splitlines()
    resp = request.parse_response(lines[0]) if len(lines) == 1 else None
    reviews = st.bot_reviews(pr)
    at_y = [r.get("id") for r in reviews if r.get("commit_id") == y]
    at_x = marked(
        [r for r in reviews if r.get("commit_id") == x], review_key(st, data), x
    )
    st.record(
        "row4b-window.json",
        {
            "api_base": {
                "value": proxy.url,
                "note": "нештатный API: прокси к api.github.com (root-ключ publisher.toml)",
            },
            "x": x,
            "y": y,
            "head_was_y_before_release": moved,
            "proxy": [asdict(s) for s in seen],
            "answer": None
            if resp is None
            else [done.returncode, resp.outcome, resp.reason, dict(resp.result)],
            "bot_reviews_at_x_with_marker": at_x,
            "bot_reviews_at_y": at_y,
        },
    )
    held_items = [s for s in seen if s.held]
    if not moved or not held_items or held_items[0].expired:
        infra(f"окно не удержано: голова Y {moved}, удержание {held_items}")
    expect(resp is not None, f"ответа издателя нет: {shown(done)}")
    assert resp is not None
    expect((done.returncode, resp.outcome) == (0, "done"), str(resp))
    expect(resp.result.get("commit_id") == x, f"commit_id {resp.result}")
    expect(at_x == [resp.result.get("review_id")], f"ревью на X в GitHub: {at_x}")
    expect(at_y == [], f"ревью бота на новой голове: {at_y}")
    return {"review_id": resp.result.get("review_id"), "x": x, "y": y}


def wait_held(held: threading.Event, alive: Callable[[], bool], limit: float) -> bool:
    """Whether `held` is set within `limit` seconds; gives up at once when
    the publisher (`alive`) ended without its POST reaching the proxy."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if held.wait(1.0):
            return True
        if not alive():
            return held.is_set()
    return held.is_set()


class PsSampler:
    """`argv` (`ps`) every `period` seconds in a thread: each snapshot with
    the monotonic times its run started and ended; `error` if a run failed
    (the loop then stops)."""

    def __init__(
        self, argv: Sequence[str] = PS_ARGV, period: float = PS_PERIOD
    ) -> None:
        self.argv = list(argv)
        self.period = period
        self.snapshots: list[tuple[float, float, str]] = []
        self.error: str | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            began = time.monotonic()
            try:
                done = subprocess.run(
                    self.argv, capture_output=True, timeout=30, check=False
                )
            except (OSError, subprocess.SubprocessError) as exc:
                self.error = f"{type(exc).__name__}: {exc}"[:200]
                return
            if done.returncode != 0:
                err = done.stderr.decode("utf-8", "replace")[:200]
                self.error = f"код {done.returncode}: {err}"
                return
            text = done.stdout.decode("utf-8", "replace")
            self.snapshots.append((began, time.monotonic(), text))
            self._stop.wait(self.period)

    def __enter__(self) -> PsSampler:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join(60)

    def saw(self, marker: str, timeout: float) -> bool:
        """Whether a snapshot holds `marker` within `timeout` seconds."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(marker in s for _b, _e, s in list(self.snapshots)):
                return True
            if self.error is not None:
                return False
            time.sleep(self.period)
        return False


def inside(
    snapshots: Sequence[tuple[float, float, str]], start: float, end: float
) -> list[str]:
    """The snapshots taken strictly inside `(start, end)`."""
    return [text for began, ended, text in snapshots if start < began and ended < end]


def window_problem(
    error: str | None,
    snapshots: Sequence[tuple[float, float, str]],
    start: float,
    end: float,
) -> str | None:
    """Why the sampler does not cover the read window (row 6's control):
    a failed `ps` run, or no snapshot strictly inside the window."""
    if error is not None:
        return f"ps не отработал: {error}"
    if not inside(snapshots, start, end):
        return "ни одного снимка ps внутри окна чтения"
    return None


def mask(text: str, needles: Sequence[str]) -> str:
    """`text` with every non-empty needle replaced by `<token>`."""
    for needle in needles:
        if needle:
            text = text.replace(needle, "<token>")
    return text


def role_lines(lines: Sequence[str]) -> list[str]:
    """The lines of the roles' processes of the read: the publisher's
    `df-publish` and the reviewer's `git … clone` (not any `git…` word)."""
    return [line for line in lines if "df-publish" in line or " clone " in line]


def seen_lines(texts: Sequence[str], needles: Sequence[str]) -> list[str]:
    """`git` and `df-publish` lines of the snapshots, the needles masked:
    what the evidence keeps of `ps` (never whole snapshots)."""
    found: list[str] = []
    for text in texts:
        for line in text.splitlines():
            if "git" not in line and "df-publish" not in line:
                continue
            masked = mask(line, needles)
            if masked not in found:
                found.append(masked)
    return found[:50]


def review_git(st: Stand, argv: Sequence[str], header: str) -> tuple[int, str]:
    """`git argv` as `df-review` in its work dir, the authorization header in
    the environment only (`DF_READ_AUTH`, spec 4.4); exit code and stderr."""
    env = {**GIT_ENV, "DF_READ_AUTH": header}
    git = [str(SYSTEM_GIT), "--config-env=http.extraHeader=DF_READ_AUTH"]
    done = as_user(
        "df-review",
        [*git, *argv],
        env=env,
        cwd=launch.role_cwd(st.layout, "df-review"),
    )
    err = done.stderr.decode("utf-8", "replace").strip()[-600:]
    return done.returncode, err


#: GitHub's own refusal of a push for rights, as git prints it (never a
#: bare «denied»: a local `Permission denied` is not GitHub's answer).
PUSH_REFUSED = (
    "The requested URL returned error: 403",
    "Write access to repository not granted",
)
_PERMISSION_TO = re.compile(r"remote: Permission to \S+ denied to ")


def github_refused_push(stderr: str) -> bool:
    """Whether git's `stderr` is GitHub's refusal of the push for rights."""
    if any(text in stderr for text in PUSH_REFUSED):
        return True
    return _PERMISSION_TO.search(stderr) is not None


def tag_branches(tag: str) -> dict[str, str]:
    """Every branch a run with `tag` may create (rows and `wipe`'s sweep)."""
    return {
        "brief": f"brief/stand-{tag}",
        "stand": f"stand/{tag}",
        "conflict-base": f"df-stand-conflict/{tag}-base",
        "conflict-head": f"df-stand-conflict/{tag}-head",
        "readtoken": f"df-stand-readtoken/{tag}",
        "control": f"df-stand-control/{tag}",
        "seed": f"df-stand-seed/{tag}",
        "seed-b": f"df-stand-seed/{tag}-b",
        "stop": stop_branch(stand_ws(tag), NODE, 1),
        "stop-b": stop_branch(stand_ws(tag, "b"), NODE, 1),
    }


def stand_ws(tag: str, suffix: str = "") -> str:
    """The workstream of rows 10-11 (`stand-<tag>`) or of row 12 (`-b`)."""
    return f"stand-{tag}-{suffix}" if suffix else f"stand-{tag}"


def conflicting_pr(st: Stand) -> tuple[int, object]:
    """R15-1: a PR made deliberately unmergeable -- two branches from the
    default branch's tip add the same new file with different content, the
    PR merges one into the other -- and its `mergeable` once GitHub has
    computed it (polled; `None` after `MERGEABLE_WAIT`)."""
    tip = st.gh.ref_sha(st.default_branch)
    assert tip is not None
    names = tag_branches(st.args.tag)
    base, head = names["conflict-base"], names["conflict-head"]
    path = f"df-stand/{st.args.tag}/conflict.md"
    for branch, text in ((base, "base\n"), (head, "head\n")):
        st.make_branch(branch, tip)
        st.put_file(branch, path, text)
    number = st.open_pr("conflict", head, base)
    return number, wait_mergeable(st, number)


def wait_mergeable(st: Stand, number: int) -> object:
    """The PR's `mergeable` once GitHub has computed it (polled; `None`
    after `MERGEABLE_WAIT`)."""
    deadline = time.monotonic() + MERGEABLE_WAIT
    while True:
        mergeable = st.gh.pull(number).get("mergeable")
        if mergeable is not None or time.monotonic() >= deadline:
            return mergeable
        time.sleep(POLL)


def row5a_read(st: Stand) -> dict[str, object]:
    """Row 5(a): `df-review` → `read-token` (the repository and the rights
    the publisher asked for); `git --config-env=http.extraHeader=
    DF_READ_AUTH clone` of the private stand as `df-review` passes. The `ps`
    sampler of row 6 runs around it: its feasibility control (a marker
    process seen) before the read, its window (start of the read, end of the
    clone) recorded for row 6."""
    marker = f"df-live-ps-{uuid.uuid4().hex}"
    data = json.dumps(
        {
            "schema": request.REQUEST_SCHEMA,
            "request_id": new_request_id("row5"),
            "run_id": st.run_id,
            "repository_id": st.args.repo_id,
        },
        sort_keys=True,
    ).encode()
    clone = launch.role_cwd(st.layout, "df-review") / "df-live-clone"
    url = f"{GITHUB_WEB}/{st.full_name}.git"
    with PsSampler() as ps:
        # the control is a process of a role (df-review), not root's child:
        # the sampler must see other users' argv, as row 6 judges them
        probe = threading.Thread(
            target=as_user,
            args=("df-review", ["/bin/sh", "-c", "sleep 3; exit 0", marker]),
            daemon=True,
        )
        probe.start()
        seen = ps.saw(marker, 10.0)
        probe.join(30)
        if not seen:
            infra(f"контроль ps: маркер процесса df-review не виден ({ps.error})")
        start = time.monotonic()
        rc, resp = st.ask("df-review", "read-token", data)
        token = str(resp.result.get("token") or "")
        if token:
            guard_token(token, "x-access-token")
            st.revocable["read-token"] = token
        header = auth_header("x-access-token", token)
        clone_rc, clone_err = review_git(
            st, ["clone", "--quiet", url, str(clone)], header
        )
        end = time.monotonic()
    st.carried["row6"] = {
        "token": token,
        "request_id": json.loads(data)["request_id"],
        "snapshots": list(ps.snapshots),
        "sampler_error": ps.error,
        "window": (start, end),
        "clone": clone,
    }
    granted = {k: v for k, v in resp.result.items() if k != "token"}
    found: dict[str, object] = {
        "read_token": granted,
        "clone": {"rc": clone_rc, "stderr": clone_err},
    }
    st.record("row5a-read.json", found)
    expect((rc, resp.outcome) == (0, "done") and bool(token), f"read-token: {granted}")
    expect(granted.get("repository_ids") == [st.args.repo_id], f"репо {granted}")
    perms = granted.get("permissions")
    expect(
        isinstance(perms, dict)
        and {k: v for k, v in perms.items() if k != "metadata"} == ops.READ_PERMISSIONS,
        f"права {perms}",
    )
    expect(clone_rc == 0 and (clone / ".git").is_dir(), f"clone: {clone_err}")
    st.carried["row5"] = (token, clone)
    return {"repository_ids": granted.get("repository_ids")}


def row5b_rights(st: Stand) -> dict[str, object]:
    """Row 5(b): with the read token, a git push of a new branch, `POST
    …/reviews`, `PATCH …/pulls/{n}` and `PUT …/pulls/{n}/merge` are refused
    by GitHub for rights: `403` «Resource not accessible by integration»
    (a push: GitHub's 403 as git prints it, `github_refused_push`, and the
    branch absent). Controls: the same push from the same clone as
    `df-review` with the machine user's header passes; the PATCH with the
    machine user's token passes; `POST …/reviews` passes through
    `publish-review` (row 2); `PUT …/merge` with the App's full
    installation token on the same, deliberately conflicting PR is `405`
    (R15-1: `mergeable == false` first, else infrastructure and no PUT)."""
    token, clone = cast(tuple[str, Path], st.carried["row5"])
    found: dict[str, object] = {}
    rights_refusals(st, token, clone, found)
    return {"refusals": sorted(k for k in found if k.startswith("refused"))}


def rights_refusals(
    st: Stand, token: str, clone: Path, found: dict[str, object]
) -> None:
    """Row 5(b)'s refusals of the read token, each next to its control;
    `found` (the row's evidence) is recorded before every verdict."""
    tag = st.args.tag
    brief = st.prs["brief"]
    names = tag_branches(tag)
    branch, ctl_branch = names["readtoken"], names["control"]
    st.branches += [branch, ctl_branch]
    read_header = auth_header("x-access-token", token)
    machine_header = auth_header(st.product.machine_user, st.secrets.machine_token)

    def keep() -> None:
        st.record("row5b-rights.json", found)

    commit = ["-C", str(clone), "commit", "--allow-empty", "-q", "-m", "read-token"]
    rc, err = review_git(st, commit, read_header)
    if rc != 0:
        infra(f"коммит в клоне: {err}")
    push = ["-C", str(clone), "push", "-q", "origin", f"HEAD:refs/heads/{branch}"]
    rc, err = review_git(st, push, read_header)
    exists = st.gh.ref_sha(branch) is not None
    found["refused push"] = {"rc": rc, "stderr": err, "branch_exists": exists}
    control = [
        "-C",
        str(clone),
        "push",
        "-q",
        "origin",
        f"HEAD:refs/heads/{ctl_branch}",
    ]
    ctl_rc, ctl_err = review_git(st, control, machine_header)
    ctl_exists = st.gh.ref_sha(ctl_branch) is not None
    found["control push (same clone, df-review, machine user's header)"] = {
        "rc": ctl_rc,
        "stderr": ctl_err,
        "branch_exists": ctl_exists,
    }
    keep()
    if ctl_rc != 0 or not ctl_exists:
        infra(f"контроль push токеном machine user: {ctl_rc} {ctl_err}")
    number, mergeable = conflicting_pr(st)
    found["conflict pr"] = {"number": number, "mergeable": mergeable}
    keep()
    if mergeable is not False:
        infra(f"контроль 405: PR {number} mergeable={mergeable!r}; PUT не выполнялся")
    gh_read = MachineGitHub(token, st.full_name)
    calls = {
        "refused POST reviews": (
            "POST",
            f"/pulls/{brief}/reviews",
            {"event": "COMMENT", "body": "df live stand: read token must not review"},
        ),
        "refused PATCH pull": (
            "PATCH",
            f"/pulls/{brief}",
            {"title": "df live stand: read token must not edit"},
        ),
        "refused PUT merge": ("PUT", f"/pulls/{number}/merge", {}),
    }
    for name, (method, rest, body) in calls.items():
        exc = refusal(
            lambda m=method, r=rest, b=body: gh_read.call(m, gh_read.repo(r), b)
        )
        found[name] = (
            None if exc is None else {"status": exc.status, "reason": exc.reason}
        )
    title = f"df live stand {tag}: brief (patched by the machine user)"
    st.gh.call("PATCH", st.gh.repo(f"/pulls/{brief}"), {"title": title})
    patched = st.gh.pull(brief).get("title") == title
    found["control PATCH (machine user)"] = patched
    full = full_installation_token(st)
    gh_full = MachineGitHub(full, st.full_name)
    exc = refusal(
        lambda: gh_full.call("PUT", gh_full.repo(f"/pulls/{number}/merge"), {})
    )
    full_put = None if exc is None else {"status": exc.status, "reason": exc.reason}
    found["control PUT merge (full installation token)"] = full_put
    found["control POST reviews"] = "row 2: publish-review done"
    keep()
    if not patched:
        infra("контроль PATCH токеном machine user не прошёл")
    if exc is None or exc.status != 405:
        infra(f"контроль PUT полным токеном: {full_put}")
    expect(rc != 0 and not exists, f"push токеном чтения: {rc} {err}")
    expect(github_refused_push(err), f"push: не отказ GitHub по правам: {err}")
    for name in calls:
        item = found[name]
        expect(
            isinstance(item, dict)
            and item.get("status") == 403
            and FORBIDDEN in str(item.get("reason")),
            f"{name}: {item}",
        )


def full_installation_token(st: Stand) -> str:
    """The App's installation token without a narrowed scope (row 5's
    control only); its value joins `GUARD` and is revoked at the end."""
    return app_token(st, "full installation token", None, None)


def app_token(
    st: Stand,
    label: str,
    repository_ids: list[int] | None,
    permissions: Mapping[str, str] | None,
) -> str:
    """An installation token of the App (`label` in the evidence of the
    revocation); its value joins `GUARD` and is revoked at the end."""
    jwt = github.app_jwt(
        st.args.app_id, st.secrets.app_key, openssl=OPENSSL, now=int(time.time())
    )
    GUARD.add(jwt)
    token = github.installation_token(
        jwt,
        st.args.installation_id,
        repository_ids=repository_ids,
        permissions=permissions,
    )
    guard_token(token.value, "x-access-token")
    st.revocable[label] = token.value
    return token.value


def revoke(token: str) -> str:
    """`DELETE /installation/token` with `token` itself (best effort): the
    outcome as a word, never the token."""
    try:
        github.call("DELETE", "/installation/token", token)
    except GitHubRefused as exc:
        return f"отказ {exc.status}: {exc.reason}"
    except GitHubUnavailable as exc:
        return f"недоступно: {exc}"
    return "отозван"


def row6_no_token(st: Stand) -> dict[str, object]:
    """Row 6: the read token is in no `ps` snapshot (every 100 ms around the
    read), no file of the publisher's state (its journal included), no
    session of sudo's I/O log, and not in the clone's `.git/config`.
    Controls (infrastructure if not): a marker process was seen by the
    sampler (row 5a); the sampler did not fail and took at least one
    snapshot strictly inside the read window (from the start of
    `read-token` to the end of the clone); the read-token's request id is
    in the journal; `.git/config` names the stand's remote."""
    carried = cast(dict[str, object], st.carried["row6"])
    token = str(carried["token"])
    snapshots = cast(list[tuple[float, float, str]], carried["snapshots"])
    start, end = cast(tuple[float, float], carried["window"])
    clone = cast(Path, carried["clone"])
    needles = [token, auth_header("x-access-token", token).split(" ", 2)[2]]
    texts = [text for _b, _e, text in snapshots]
    window = inside(snapshots, start, end)
    lines = seen_lines(window, needles)
    in_ps = [i for i, s in enumerate(texts) if any(n in s for n in needles)]
    state_files = scan_tree(st.layout.publisher_dir, needles)
    journal = (st.layout.publisher_dir / store.JOURNAL).read_text()
    iolog = {
        str(d): (
            [str(p) for p in scan_tree(d, needles)] if d.exists() else "каталога нет"
        )
        for d in IOLOG_DIRS
    }
    config = (clone / ".git" / "config").read_text()
    sampler = window_problem(
        cast(str | None, carried["sampler_error"]), snapshots, start, end
    )
    st.record(
        "row6-no-token.json",
        {
            "ps_snapshots": len(snapshots),
            "ps_snapshots_in_read_window": len(window),
            "read_window_seconds": round(end - start, 3),
            "ps_sampler_problem": sampler,
            "ps_with_token": in_ps,
            "ps_git_and_publish_lines_in_window": lines,
            "publisher_files_with_token": [str(p) for p in state_files],
            "iolog": iolog,
            "git_config_has_token": any(n in config for n in needles),
            "controls": {
                "request_id_in_journal": str(carried["request_id"]) in journal,
                "remote_in_git_config": st.full_name in config,
            },
        },
    )
    if sampler is None and not role_lines(lines):
        sampler = "в окне чтения ни одной строки df-publish или git clone"
    if sampler is not None:
        infra(f"контроль строки 6: {sampler}")
    if str(carried["request_id"]) not in journal or st.full_name not in config:
        infra(
            "контроль строки 6: request_id нет в журнале или remote нет в .git/config"
        )
    expect(in_ps == [], f"токен в снимках ps: {in_ps}")
    expect(state_files == [], f"токен в файлах издателя: {state_files}")
    leaked = [v for v in iolog.values() if isinstance(v, list) and v]
    expect(leaked == [], f"токен в I/O-журнале sudo: {leaked}")
    expect(not any(n in config for n in needles), "токен в .git/config")
    return {"ps_snapshots": len(snapshots), "in_window": len(window)}


def row7_sudo_refusals(st: Stand) -> dict[str, object]:
    """Row 7: `df-exec` → `publish-review` and `df-review` → `merge` are
    refused by sudo; the publisher never runs (Task 14's `sudo_refused`).
    Controls: `df-review` → `publish-review` (row 2) and `df-exec` →
    `merge` (row 8) pass sudo."""
    pr, head = st.prs["brief"], st.heads["brief"]
    found = {
        "df-exec publish-review": sudo_refused(
            st.rw,
            "live-df-exec-publish-review",
            "df-exec",
            "publish-review",
            review_payload(st, pr, head, "row7"),
            "not-allowed",
        ),
        "df-review merge": sudo_refused(
            st.rw,
            "live-df-review-merge",
            "df-review",
            "merge",
            merge_payload(st, pr, head, "row7"),
            "not-allowed",
        ),
    }
    st.record("row7-sudo.json", found)
    return found


def row8_merge_routes(st: Stand) -> dict[str, object]:
    """Row 8: `merge` from `df-exec`: `brief/…` → `refused:
    brief_precheck_unavailable`, `stand/…` → `refused: ambiguous_kind` (r1 N11).
    Control: both answers come from past the sender check (`df-exec`)."""
    wants = {"brief": "brief_precheck_unavailable", "stand": "ambiguous_kind"}
    found: dict[str, object] = {}
    answers: dict[str, tuple[int, Response]] = {}
    for name in wants:
        number = st.prs[name]
        head = head_of(st.gh.pull(number))
        rc, resp = st.ask("df-exec", "merge", merge_payload(st, number, head, "row8"))
        answers[name] = (rc, resp)
        found[name] = [rc, resp.outcome, resp.reason, resp.evidence.get("sender")]
    st.record("row8-merge.json", found)
    sender = st.rw.uid("df-exec")
    for name, want in wants.items():
        rc, resp = answers[name]
        expect(resp.evidence.get("sender") == sender, f"{name}: {found[name]}")
        expect((rc, resp.outcome, resp.reason) == (2, "refused", want), str(found))
    return found


def row9_cleanup(st: Stand) -> dict[str, object]:
    """Row 9: every open PR closed and every branch deleted by the machine
    user (a merged PR -- the seeds, row 12's stop-PR -- is closed already);
    then each PR is `closed` and each branch absent. A failure fails the
    run. The seeds' squash commits and row 12's merge stay on the default
    branch: the evidence names them."""
    problems: list[str] = []
    for name, number in st.prs.items():
        try:
            if st.gh.pull(number).get("state") != "open":
                continue
            st.gh.call("PATCH", st.gh.repo(f"/pulls/{number}"), {"state": "closed"})
        except (GitHubRefused, GitHubUnavailable) as exc:
            problems.append(f"PR {name} #{number}: {exc}")
    for branch in st.branches:
        try:
            if st.gh.ref_sha(branch) is not None:
                st.gh.call("DELETE", st.gh.repo(f"/git/refs/heads/{branch}"))
        except (GitHubRefused, GitHubUnavailable) as exc:
            problems.append(f"ветка {branch}: {exc}")
    states = {}
    for name, number in st.prs.items():
        try:
            states[name] = st.gh.pull(number).get("state")
        except (GitHubRefused, GitHubUnavailable) as exc:
            states[name] = f"не прочитан: {exc}"
    left = []
    for branch in st.branches:
        try:
            if st.gh.ref_sha(branch) is not None:
                left.append(branch)
        except (GitHubRefused, GitHubUnavailable) as exc:
            left.append(f"{branch}: {exc}")
    found = {
        "prs": states,
        "branches": st.branches,
        "left": left,
        "problems": problems,
        "kept_on_default_branch": dict(st.merges),
        "note": "коммиты засева и мерж строки 12 остаются в истории ветки "
        "по умолчанию стенда",
    }
    st.record("row9-cleanup.json", found)
    expect(problems == [], f"уборка: {problems}")
    expect(set(states.values()) <= {"closed"}, f"PR не закрыты: {states}")
    expect(left == [], f"ветки остались: {left}")
    return {"closed": len(states), "deleted": len(st.branches)}


# ---------- the merge rows on the stop route (stage 2b, Task 11) ----------


def seed_manifest(ws: str, full_name: str) -> tuple[str, str]:
    """The seed's one file (path, text): a `review/v1` manifest of node `n1`
    at g1 of `ws`. A technical seed (plan decision 13): not an acceptance of
    candidate and no human act; its node hash and policy pin are fixtures
    (the stop route reads neither)."""
    req = NodeRequest(
        f"{ws}-seed",
        ws,
        full_name,
        {NODE: NodeEntry(f"workstreams/{ws}/{NODE}.md", "sha256:" + "0" * 64, 1)},
        {NODE: {}},
        PolicyPin("fixture/none", "main", "0" * 40, {}),
    )
    return manifest_path(ws, req.request_id), render_manifest(req)


def stop_record(ws: str, reason: str) -> tuple[str, str]:
    """The stop-PR's one file (path, text): the record stopping `n1` g1."""
    return stop_path(ws, NODE, 1), render_stop(StopRecord(NODE, 1, reason))


def seed_problem(history: History | Unavailable, commit: str, head: str) -> str | None:
    """Why `history` does not hold the seed (merge `commit`, PR `head`) as
    the current request of `n1` at g1; `None` when it does."""
    if isinstance(history, Unavailable):
        return history.reason
    current = node_history(history, NODE).current
    if current is None:
        return f"нет доставленной заявки узла {NODE}"
    if isinstance(current, DeliveredStop):
        return f"узел {NODE} остановлен: {current.merge_commit[:12]}"
    gen = current.request.nodes[NODE].generation
    if (current.merge_commit, current.head_sha, gen) != (commit, head, 1):
        return (
            f"действует заявка {current.merge_commit[:12]} "
            f"с головой {current.head_sha[:12]} g{gen}"
        )
    return None


def checker_history(st: Stand, ws: str, token: str) -> History | Unavailable:
    """The checker's own reading of the default branch: `load_history` over
    `SnapshotHistory` (as `act_precheck` reads the base), with a read token
    of the App; the snapshot is removed after."""
    home = Path(tempfile.mkdtemp(prefix="df-live-history-", dir=st.rw.work))
    ref = f"refs/heads/{st.default_branch}"
    deadline = time.monotonic() + SNAPSHOT_DEADLINE
    try:
        git_dir = open_snapshot(
            home,
            st.full_name,
            [ref],
            token,
            git_base=DEFAULT_GIT_BASE,
            deadline=deadline,
        )
        history = SnapshotHistory(
            git_dir,
            TokenTransport(token, api_base=DEFAULT_API_BASE),
            token,
            git_base=DEFAULT_GIT_BASE,
            deadline=deadline,
        )
        return load_history(history, Scope(ws, st.full_name, ""), ref)
    except (OSError, ValueError, DeadlineExceeded) as exc:
        return Unavailable(f"снимок: {type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def seed(st: Stand, name: str, ws: str, evidence: str) -> dict[str, object]:
    """Rows 10 and 12: the machine user opens the seed PR `name` (one
    manifest, `seed_manifest`) and merges it with its own token (squash,
    `sha` = head). Control (infrastructure if not, polled up to
    `DELIVERY_WAIT`): the checker's `load_history` sees the seed as the
    current request of `n1` g1. Preconditions (infrastructure): squash
    merges allowed on the stand (H7), the seed PR mergeable."""
    squash_problem(st.gh.call("GET", st.gh.repo("")))
    tip = st.gh.ref_sha(st.default_branch)
    if tip is None:
        infra(f"нет ветки {st.default_branch}")
    assert tip is not None
    branch = tag_branches(st.args.tag)[name]
    st.make_branch(branch, tip)
    path, text = seed_manifest(ws, st.full_name)
    head = st.put_file(branch, path, text)
    st.heads[name] = head
    number = st.open_pr(name, branch, st.default_branch)
    if not st.wait_head(number, head, time.monotonic() + 60):
        infra(f"голова PR засева {number} не стала {head}")
    require_mergeable(st, number, "PR засева")
    body = {"merge_method": "squash", "sha": head}
    try:
        merged = st.gh.call("PUT", st.gh.repo(f"/pulls/{number}/merge"), body)
    except GitHubRefused as exc:
        if squash_refused(exc):
            infra(f"H7: squash-мерж засева {number} отклонён: {exc.reason}")
        raise
    commit = merged.get("sha") if isinstance(merged, dict) else None
    if not isinstance(commit, str):
        raise GitHubUnavailable(f"merge засева {number}: нет sha")
    st.merges[name] = commit
    token = app_token(st, f"read token {ws}", [st.args.repo_id], ops.READ_PERMISSIONS)
    deadline = time.monotonic() + DELIVERY_WAIT
    while True:
        problem = seed_problem(checker_history(st, ws, token), commit, head)
        if problem is None or time.monotonic() >= deadline:
            break
        time.sleep(POLL)
    found: dict[str, object] = {
        "ws": ws,
        "pr": number,
        "head_sha": head,
        "merge_commit": commit,
        "manifest": path,
        "load_history": problem or f"DeliveredRequest {NODE} g1",
        "note": "технический засев machine user (squash): не приёмка candidate "
        "и не акт человека",
    }
    st.record(evidence, found)
    if problem is not None:
        infra(f"засев {ws}: load_history проверяющего не видит заявку g1: {problem}")
    return found


def squash_problem(repo: object) -> None:
    """H7 preflight: an explicit `allow_squash_merge: false` of `GET
    /repos/{full_name}` is infrastructure naming the owner's step. GitHub
    may leave the merge settings out for a token without admin rights (the
    machine user's): an absent field is unknown, and the seed's own squash
    `PUT` decides (`squash_refused`)."""
    allowed = repo.get("allow_squash_merge") if isinstance(repo, dict) else None
    if allowed is False:
        infra("H7: squash-мерж на стенде не разрешён (allow_squash_merge=false)")


def squash_refused(exc: GitHubRefused) -> bool:
    """Whether the seed's squash `PUT` was refused for its merge method
    (405 with a message about squash or the merge method): H7 not done."""
    reason = exc.reason.lower()
    return exc.status == 405 and ("squash" in reason or "merge method" in reason)


def require_mergeable(st: Stand, number: int, what: str) -> None:
    """Before a merge (the seed's, the publisher's in rows 11 and 12): the
    PR's `mergeable` is `True` within `MERGEABLE_WAIT`, else infrastructure
    (a mergeability GitHub has not computed is the stand's timing, not the
    mechanism)."""
    mergeable = wait_mergeable(st, number)
    if mergeable is not True:
        infra(f"{what} {number}: mergeable={mergeable!r}; мержа не было")


def open_stop_pr(st: Stand, name: str, ws: str) -> dict[str, object]:
    """The machine user opens the stop-PR `name` of `ws` from the default
    branch's tip (past the seed): the branch `stop_branch(ws, n1, 1)`, one
    added `stop_path(ws, n1, 1)`. Its facts as GitHub shows them."""
    tip = st.gh.ref_sha(st.default_branch)
    assert tip is not None
    branch = tag_branches(st.args.tag)[name]
    st.make_branch(branch, tip)
    path, text = stop_record(ws, f"df live stand {st.args.tag}: stop of the seed")
    st.heads[name] = st.put_file(branch, path, text)
    number = st.open_pr(name, branch, st.default_branch)
    pr = st.gh.pull(number)
    base = pr.get("base")
    return {
        "pr": number,
        "branch": branch,
        "path": path,
        "state": pr.get("state"),
        "head_sha": head_of(pr),
        "base": base.get("ref") if isinstance(base, dict) else None,
    }


def merge_put_re(full_name: str, number: int) -> re.Pattern[str]:
    """The path of the PR's `PUT …/merge` (the proxy's gate)."""
    return re.compile(re.escape(f"/repos/{full_name}/pulls/{number}/merge"))


def merge_puts(seen: Sequence[Seen], full_name: str, number: int) -> list[Seen]:
    """The proxy's log of the PR's merge `PUT`s: every one that reached the
    proxy, answered upstream or not."""
    path = merge_put_re(full_name, number)
    return [s for s in seen if s.method == "PUT" and path.fullmatch(s.path)]


def merge_key(st: Stand, data: bytes) -> str:
    """The idempotency key the publisher derives for `df-exec`'s `merge`."""
    req = request.parse("merge", data)
    return store.idempotency_key(st.rw.uid("df-exec"), "merge", request.digest(req))


def attestations(st: Stand, number: int, key: str, head: str) -> list[int]:
    """Ids of the bot's `APPROVED` reviews at `head` with the publisher's
    attestation marker of `key` (plan decision 9)."""
    marker = merge.ATTESTATION_MARKER.format(kind="stop", key=key, sha=head)
    return [
        int(str(r.get("id")))
        for r in st.bot_reviews(number)
        if str(r.get("body") or "").endswith(marker)
        and r.get("state") == "APPROVED"
        and r.get("commit_id") == head
    ]


def check_head_changed(
    first: tuple[int, Response],
    again: tuple[int, Response],
    pr: Mapping[str, object],
    puts: Sequence[Seen],
) -> None:
    """Row 11's verdict: `refused: head_changed`, the PR open and not
    merged, the identical repeat the same (a replay), one `PUT` upstream
    and GitHub's answer to it 409."""
    want = (2, "refused", "head_changed")
    rc, resp = first
    expect((rc, resp.outcome, resp.reason) == want, f"merge: {rc} {resp}")
    state = (pr.get("state"), pr.get("merged"))
    expect(state == ("open", False), f"PR после отказа: {state}")
    rc, resp = again
    expect((rc, resp.outcome, resp.reason) == want, f"повтор: {rc} {resp}")
    expect(resp.evidence.get("replayed") is True, f"повтор не replay: {resp}")
    expect(len(puts) == 1, f"PUT выше: {len(puts)} ({puts})")
    put = puts[0]
    expect(put.upstream_answered and put.status == 409, f"PUT выше: {put}")


def lagging(answer: tuple[int, Response]) -> bool:
    """Whether `answer` is the one a repeat may give while GitHub's read
    lags the cut `PUT` (`LAGGING`)."""
    rc, resp = answer
    return (rc, resp.outcome, resp.reason) == LAGGING


def settle_merge(
    ask: Callable[[], tuple[int, Response]],
    tries: int = RECONCILE_TRIES,
    *,
    wait: Callable[[float], None] = time.sleep,
) -> list[tuple[int, Response]]:
    """Row 12's answers of one identical request: repeated (`POLL` apart, at
    most `tries` in all) while it is `LAGGING`; once it is anything else,
    one more repeat (the replay). The last answer still lagging means the
    bound passed."""
    answers = [ask()]
    while lagging(answers[-1]) and len(answers) < tries:
        wait(POLL)
        answers.append(ask())
    if not lagging(answers[-1]):
        answers.append(ask())
    return answers


def check_reconciled(
    answers: Sequence[tuple[int, Response]],
    pr: Mapping[str, object],
    puts: Sequence[Seen],
    *,
    bot: str,
    head: str,
) -> None:
    """Row 12's verdict over `settle_merge`'s answers: only `LAGGING` before
    the settled one; the settled one `done` found by reconciliation (the
    premise named, not a replay), the PR merged by the App's bot at `head`
    with the answered merge commit; then the same `done`, a replay; one
    `PUT` upstream over all of them, its answer not written downstream."""
    waited = list(itertools.takewhile(lagging, answers))
    settled = list(answers[len(waited) :])
    expect(
        settled != [],
        f"сверка не сошлась за {len(waited)} повторов: {LAGGING[1]}: {LAGGING[2]}",
    )
    expect(len(settled) == 2, f"после сверки ответов {len(settled)}: {settled}")
    rc, resp = settled[0]
    expect((rc, resp.outcome) == (0, "done"), f"merge: {rc} {resp}")
    expect(resp.evidence.get("premise") == merge.PREMISE, f"не сверка: {resp}")
    expect(resp.evidence.get("replayed") is not True, f"сверка — replay: {resp}")
    commit = resp.result.get("merge_commit")
    expect(resp.result.get("head_sha") == head, f"голова ответа: {resp.result}")
    head_obj = pr.get("head")
    expect(
        pr.get("merged") is True
        and ops.is_bot({"user": pr.get("merged_by")}, bot)
        and isinstance(head_obj, dict)
        and head_obj.get("sha") == head
        and pr.get("merge_commit_sha") == commit,
        f"PR на GitHub: merged {pr.get('merged')}, merged_by {pr.get('merged_by')}, "
        f"merge_commit {pr.get('merge_commit_sha')}, ответ {commit}",
    )
    rc, resp = settled[1]
    expect((rc, resp.outcome) == (0, "done"), f"повтор: {rc} {resp}")
    expect(resp.evidence.get("replayed") is True, f"повтор не replay: {resp}")
    expect(resp.result.get("merge_commit") == commit, f"повтор: {resp.result}")
    expect(len(puts) == 1, f"PUT выше: {len(puts)} ({puts})")
    put = puts[0]
    expect(put.upstream_answered and not put.answered_downstream, f"PUT выше: {put}")


def _answers(answers: Sequence[tuple[int, Response]]) -> list[list[object]]:
    """Every answer of a row's request, in order, for the evidence."""
    return [
        [rc, resp.outcome, resp.reason, dict(resp.result), dict(resp.evidence)]
        for rc, resp in answers
    ]


def keep_merge(st: Stand, name: str, number: int) -> dict[str, object]:
    """The PR as it is now; if merged, its merge commit is kept for row 9's
    evidence (it stays on the default branch)."""
    pull = st.gh.pull(number)
    commit = pull.get("merge_commit_sha")
    if pull.get("merged") is True and isinstance(commit, str):
        st.merges[name] = commit
    return pull


def row10_seed(st: Stand) -> dict[str, object]:
    """Row 10: the seed of `stand-<tag>` (`seed`: the machine user's squash
    merge of a `review/v1` manifest of `n1`; control: the checker's
    `load_history` sees `DeliveredRequest` g1), then the stop-PR on
    `stop_branch("stand-<tag>", "n1", 1)` with the one file `stop_path(…)`.
    Control: the stop-PR is open on the default branch at its commit."""
    ws = stand_ws(st.args.tag)
    seeded = seed(st, "seed", ws, "row10-seed.json")
    stop = open_stop_pr(st, "stop", ws)
    st.record("row10-stop-pr.json", stop)
    if (stop["state"], stop["base"], stop["head_sha"]) != (
        "open",
        st.default_branch,
        st.heads["stop"],
    ):
        infra(f"stop-PR: {stop}")
    return {"seed_pr": seeded["pr"], "merge_commit": seeded["merge_commit"], **stop}


def row11_head_change(st: Stand) -> dict[str, object]:
    """Row 11: `df-exec` → `merge` of row 10's stop-PR with `api_base` =
    `gate_proxy` holding the `PUT …/merge` (`hold`); while it is held the
    machine user pushes a second commit; released, GitHub refuses the `PUT`
    (409) and the publisher answers `refused: head_changed`, the PR not
    merged; the identical repeat (through the proxy) is the same, a replay,
    and the proxy saw one `PUT`. The attestation is the bot's review at the
    old head. Controls (infrastructure): the `PUT` was held, not expired,
    and the PR's head was the new commit before the release."""
    pr, head = st.prs["stop"], st.heads["stop"]
    ws = stand_ws(st.args.tag)
    branch = tag_branches(st.args.tag)["stop"]
    require_mergeable(st, pr, "stop-PR")
    data = merge_payload(st, pr, head, "row11")
    box: dict[str, subprocess.CompletedProcess[bytes]] = {}

    def send() -> None:
        box["done"] = st.rw.publish("df-exec", "merge", data)

    gate = gate_proxy.hold("PUT", merge_put_re(st.full_name, pr))
    again: tuple[int, Response] | None = None
    with serve_gate_proxy(gate=gate) as proxy, api_base(st, proxy.url):
        worker = threading.Thread(target=send, daemon=True)
        worker.start()
        held = wait_held(proxy.held, worker.is_alive, PUT_WAIT)
        new, moved = "", False
        if held:
            deadline = time.monotonic() + HEAD_WAIT
            path, text = stop_record(ws, f"df live stand {st.args.tag}: second")
            new = st.put_file(branch, path, text)
            moved = st.wait_head(pr, new, deadline, period=HEAD_POLL)
        proxy.release()
        worker.join(600)
        if held and "done" in box:
            again = st.ask("df-exec", "merge", data)
        seen = proxy.requests()
    if new:
        st.heads["stop"] = new
    pull = keep_merge(st, "stop", pr)
    done = box.get("done")
    if done is None or not held:
        why = "нет ответа" if done is None else shown(done)
        infra(f"PUT не дошёл до прокси: {why}")
    assert done is not None and again is not None
    first = st.answer("merge", done)
    puts = merge_puts(seen, st.full_name, pr)
    attested = attestations(st, pr, merge_key(st, data), head)
    st.record(
        "row11-head-change.json",
        {
            "api_base": {"value": proxy.url, "note": "нештатный API: прокси"},
            "head": head,
            "pushed": new,
            "head_was_new_before_release": moved,
            "proxy": [asdict(s) for s in seen],
            "answers": _answers([first, again]),
            "pr": {"state": pull.get("state"), "merged": pull.get("merged")},
            "attestations_at_head": attested,
        },
    )
    held_puts = [s for s in puts if s.held]
    if not moved or not held_puts or held_puts[0].expired:
        infra(f"окно PUT не удержано: голова {moved}, удержание {held_puts}")
    check_head_changed(first, again, pull, puts)
    expect(len(attested) == 1, f"аттестации на голове {head}: {attested}")
    return {"head": head, "pushed": new, "puts": len(puts)}


def row12_dropped_answer(st: Stand) -> dict[str, object]:
    """Row 12: a new seed and stop-PR (`stand-<tag>-b`); `df-exec` → `merge`
    with `gate_proxy` passing the `PUT …/merge` on and cutting its answer
    (`drop_response`): the publisher reconciles to `done` (the PR merged by
    the App's bot at the head) -- while GitHub's read lags, the identical
    request is repeated (`settle_merge`, spec 5.4 step 2) and may only be
    `unknown: merge_not_confirmed`; then the same `done`, replayed; the
    proxy saw one `PUT` over all of them. (A `PUT` still executing is
    tested offline: `test_put_in_flight_open_is_unknown_then_done`.)
    Control (infrastructure): the `PUT` reached GitHub, was executed (200)
    and its answer was not written downstream."""
    ws = stand_ws(st.args.tag, "b")
    seed(st, "seed-b", ws, "row12-seed.json")
    stop = open_stop_pr(st, "stop-b", ws)
    pr, head = st.prs["stop-b"], st.heads["stop-b"]
    require_mergeable(st, pr, "stop-PR")
    data = merge_payload(st, pr, head, "row12")
    gate = gate_proxy.drop_response("PUT", merge_put_re(st.full_name, pr))
    with serve_gate_proxy(gate=gate) as proxy, api_base(st, proxy.url):
        answers = settle_merge(lambda: st.ask("df-exec", "merge", data))
        seen = proxy.requests()
    pull = keep_merge(st, "stop-b", pr)
    puts = merge_puts(seen, st.full_name, pr)
    attested = attestations(st, pr, merge_key(st, data), head)
    st.record(
        "row12-dropped-answer.json",
        {
            "stop_pr": stop,
            "api_base": {"value": proxy.url, "note": "нештатный API: прокси"},
            "proxy": [asdict(s) for s in seen],
            "answers": _answers(answers),
            "reconcile_tries": RECONCILE_TRIES,
            "pr": {
                "state": pull.get("state"),
                "merged": pull.get("merged"),
                "merged_by": login_of({"user": pull.get("merged_by")}),
                "head_sha": head_of(pull),
                "merge_commit_sha": pull.get("merge_commit_sha"),
            },
            "attestations_at_head": attested,
        },
    )
    cut = [s for s in puts if not s.answered_downstream]
    if not cut or not cut[0].upstream_answered or cut[0].status != 200:
        infra(f"контроль: PUT не исполнен выше или ответ не оборван: {puts}")
    check_reconciled(answers, pull, puts, bot=st.bot, head=head)
    expect(len(attested) == 1, f"аттестации на голове {head}: {attested}")
    return {"merge_commit": st.merges.get("stop-b"), "puts": len(puts)}


@dataclass(frozen=True)
class Row:
    """A row: its id, title, function and the rows it needs to have passed."""

    id: str
    title: str
    fn: Callable[[Stand], dict[str, object]]
    needs: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return f"{self.id} {self.title}"


#: The rows and what each needs (R15 fix round 1, finding 3): a row whose
#: prerequisite did not pass is skipped, never passed; the others run. A
#: control held by another row is a prerequisite too (R1-1): 4а's and 5б's
#: review control is row 2, row 7's controls are rows 2 and 8 (so 8 runs
#: first). The merge rows of stage 2b: 11 merges row 10's stop-PR, 12 seeds
#: its own workstream.
ROWS: tuple[Row, ...] = (
    Row("1", "PR от machine user", row1_open_prs),
    Row("2", "publish-review и повтор", row2_review_once, ("1",)),
    Row("3", "сверка после потерянного ответа", row3_reconcile, ("2",)),
    Row("4а", "прежний head", row4a_old_head, ("1", "2")),
    Row("4б", "окно чтение→публикация", row4b_window, ("4а",)),
    Row("5а", "read-token и clone", row5a_read),
    Row("5б", "отказы GitHub токену чтения", row5b_rights, ("1", "2", "5а")),
    Row("6", "токен нигде", row6_no_token, ("5а",)),
    Row("8", "маршрутизация merge", row8_merge_routes, ("1",)),
    Row("7", "отказ sudo", row7_sudo_refusals, ("1", "2", "8")),
    Row("10", "засев и stop-PR", row10_seed),
    Row("11", "смена head при удержанном PUT", row11_head_change, ("10",)),
    Row("12", "оборванный ответ PUT и повтор", row12_dropped_answer),
)
CLEANUP = Row("9", "уборка", row9_cleanup)
SKIPPED = "не выполнялась"


def run_row(st: Stand, fn: Callable[[Stand], dict[str, object]]) -> dict[str, object]:
    """One row's outcome: `pass`, `fail` (its mechanism) or `инфраструктура`."""
    try:
        return {"status": "pass", **fn(st)}
    except (RowFailed, AssertionError) as exc:
        return {"status": "fail", "reason": str(exc)[:1000]}
    except pytest.fail.Exception as exc:
        text = str(exc)
        kind = "инфраструктура" if text.startswith(INFRA) else "fail"
        return {"status": kind, "reason": text[:1000]}
    except Exception as exc:  # noqa: BLE001 - any other error is not a verdict
        return {"status": "инфраструктура", "reason": f"{type(exc).__name__}: {exc}"}


def run_rows(
    st: Stand, rows: Sequence[Row] = ROWS, cleanup: Row = CLEANUP
) -> dict[str, dict[str, object]]:
    """Every row in order: one whose prerequisite did not pass is recorded
    `не выполнялась (зависит от N)`, the others run; the cleanup always."""
    results: dict[str, dict[str, object]] = {}
    passed: set[str] = set()
    for row in rows:
        missing = [n for n in row.needs if n not in passed]
        if missing:
            why = f"{SKIPPED} (зависит от {', '.join(missing)})"
            results[row.name] = {"status": SKIPPED, "reason": why}
        else:
            results[row.name] = run_row(st, row.fn)
            if results[row.name]["status"] == "pass":
                passed.add(row.id)
        print(f"{row.name}: {results[row.name]['status']}", flush=True)
    results[cleanup.name] = run_row(st, cleanup.fn)
    print(f"{cleanup.name}: {results[cleanup.name]['status']}", flush=True)
    return results


def verdict(
    results: Mapping[str, Mapping[str, object]], expected: int = len(ROWS) + 1
) -> int:
    """`EXIT_PASS` only when every row and the cleanup passed; a skipped or
    missing row is a failure, an infrastructure failure anywhere is 2."""
    statuses = [str(r["status"]) for r in results.values()]
    if len(results) != expected:
        statuses.append("fail")
    if "инфраструктура" in statuses:
        return EXIT_INFRA
    return EXIT_PASS if set(statuses) == {"pass"} else EXIT_FAILED


# ---------- the phases ----------


def live_top() -> Path:
    """`<R83 base>/df-live` (root `0755`, readable by `df-build` on Linux,
    where the build fetches the fixture `file://`)."""
    base, _ = make_base(BASES[plat.detect_platform()])
    return base / LIVE_DIR


def require_ci_root() -> None:
    """Only as root in GitHub Actions: a workstation never runs a phase."""
    if os.geteuid() != 0 or os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("live_stand: только от root в GitHub Actions (R15-6)")


def install_phase(source_sha: str, out: Path) -> int:
    """`install` (module doc); exit 0 with `provenance.json`, else 2."""
    require_ci_root()
    require_clean_host()
    top = live_top()
    os.mkdir(top, 0o755)
    os.chmod(top, 0o755)
    # The real `site/` pins of this checkout: the stand's install fetches the
    # PyYAML sdist and the steward commit over the network (2b Task 5), so
    # the precheck of later rows runs the real code.
    pins = locked_pins((REPO_ROOT / "uv.lock").read_bytes())
    bare, fixture_sha = build_fixture_repo(
        top, FIXTURE_FROM_CHECKOUT, name="live", site=pins
    )
    with serve_repo(top, bare) as url:
        world = RootWorld(
            PRODUCTION, FixtureRepo(url, fixture_sha), approved_uv(), top, ""
        )
        installed = install_production(world)
    state = {"fixture_repo": str(bare), "fixture_sha": fixture_sha}
    fd = os.open(top / STATE_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(state, fh)
    prov = check_provenance(REPO_ROOT, source_sha, bare, fixture_sha, PRODUCTION)
    record(
        out,
        "provenance.json",
        {**prov.to_json(), "installed_release": installed.release_id},
    )
    return EXIT_PASS


def stand_phase(args: StandArgs) -> int:
    """`stand` (module doc). The scan of the evidence for every in-process
    secret runs whatever happens (an outer `finally`)."""
    require_ci_root()
    top = live_top()
    prov, secrets = gate(args.source_sha, args.secrets, top=top, layout=PRODUCTION)
    rid = current_id(PRODUCTION)
    assert rid is not None
    installed = InstalledWorld(
        RootWorld(PRODUCTION, FixtureRepo("", prov.fixture_sha), Path(), top, ""), rid
    )
    work = top / "stand"
    work.mkdir(mode=0o755)
    rw = RolesWorld(installed, cast(FakeGitHub, None), work)
    code = EXIT_INFRA
    try:
        try:
            st = set_up(rw, args, secrets)
            results = run_rows(st)
            revoked = {label: revoke(t) for label, t in st.revocable.items()}
            print(f"отзыв токенов: {revoked}", flush=True)
            summary = {
                "tag": args.tag,
                "provenance": prov.to_json(),
                "token_type": secrets.token_type,
                "run_record": {"run_id": st.run_id, "fixture": True},
                "products_toml": "фикстура (переменные стенда)",
                "rows": results,
                "tokens_revoked": revoked,
            }
            record(args.out, "summary.json", summary)
            code = verdict(results)
        finally:
            teardown_roles(PRODUCTION)
            teardown_install(PRODUCTION, args.out / "journals" / "live-installed.jsonl")
            shutil.rmtree(top, ignore_errors=True)
    finally:
        leaked = scan_tree(args.out, GUARD.values())
        for path in leaked:
            path.unlink()
        if leaked:
            print(
                f"evidence с секретом удалено: {[str(p) for p in leaked]}", flush=True
            )
            code = EXIT_INFRA
    return code


def set_up(rw: RolesWorld, args: StandArgs, secrets: StandSecrets) -> Stand:
    """Roles, the stand's product, the credentials, `self-check`, models and
    the fixture run record."""
    lay = rw.layout
    darkfactory = [SUDO, "-n", str(lay.launcher)]
    check(as_operator([*darkfactory, "cli", "set", "openssl", str(OPENSSL)]), "cli set")
    check(
        as_operator([*darkfactory, "roles", "setup", "--operator", OPERATOR]), "roles"
    )
    gh = MachineGitHub(secrets.machine_token)
    user = gh.call("GET", "/user")
    repo = gh.call("GET", f"/repositories/{args.repo_id}")
    if not isinstance(user, dict) or not isinstance(repo, dict):
        infra("GET /user или /repositories: не объект")
    assert isinstance(user, dict) and isinstance(repo, dict)
    if repo.get("private") is not True:
        infra(f"стенд {repo.get('full_name')} не приватный")
    gh.full_name = str(repo["full_name"])
    # the header git carries the PAT in (row 5б's control push)
    guard_token(secrets.machine_token, str(user["login"]))
    product = Product(
        args.repo_id,
        gh.full_name,
        "write",
        args.app_id,
        args.installation_id,
        str(user["login"]),
        "/var/empty",
    )
    write_etc(lay, "products.toml", dump_products({args.repo_id: product}))
    key = ["setup-agent", "app-key", "--app-id", str(args.app_id)]
    check(as_operator([*darkfactory, *key, "--file", str(secrets.app_key)]), "app-key")
    machine = ["setup-agent", "machine-token", "--file", str(secrets.token_file)]
    check(as_operator([*darkfactory, *machine]), "machine-token")
    self_check = self_check_clean(lay, args)
    for i, role in enumerate(models.ROLES):
        choice = [
            "models",
            "set",
            role,
            "--harness",
            "claude",
            "--model",
            f"fixture-{i}",
        ]
        check(as_operator([*darkfactory, *choice]), f"models {role}")
    installed_token = lay.role_home("df-exec").joinpath(*MACHINE_TOKEN).read_text()
    jwt = github.app_jwt(
        args.app_id, secrets.app_key, openssl=OPENSSL, now=int(time.time())
    )
    GUARD.add(jwt)
    st = Stand(
        rw,
        args,
        secrets,
        MachineGitHub(installed_token.strip(), gh.full_name),
        product,
        str(repo["default_branch"]),
        f"{github.app_slug(jwt)}[bot]",
    )
    st.run_id = register_run(st)
    st.record(
        "setup.json",
        {
            "repository": {"id": args.repo_id, "full_name": gh.full_name},
            "default_branch": st.default_branch,
            "machine_user": product.machine_user,
            "token_type": secrets.token_type,
            "bot": st.bot,
            "self_check": self_check,
            "products_toml": "фикстура: из переменных стенда",
            "run_record": {"run_id": st.run_id, "fixture": True},
        },
    )
    return st


def self_check_clean(lay: Layout, args: StandArgs) -> dict[str, object]:
    """Root's `self-check` (the real chain): no problem, exactly the stand's
    installation on the stand repo; an extra installation of the App is an
    infrastructure failure (step H2), not a bypass."""
    raw = setup_agent.publisher_self_check(lay, purpose="live-stand")
    resp = request.parse_response(raw.decode("utf-8").strip())
    result = dict(resp.result)
    installs = result.get("installations")
    ids = [
        (i.get("id"), i.get("repository_ids"))
        for i in installs or []
        if isinstance(i, dict)
    ]
    if (
        resp.outcome != "done"
        or result.get("problems")
        or ids != [(args.installation_id, [args.repo_id])]
    ):
        infra(f"self-check: {resp.outcome} {result.get('problems')} {ids}")
    return result


def register_run(st: Stand) -> str:
    """A fixture run record of `current` for the stand's product (Task 14's
    `register_run`, product = the stand)."""
    lay = st.layout
    rid = current_id(lay)
    registry = parse_releases((lay.etc / "releases.toml").read_text())
    entry = next(e for e in registry.releases if e.id == rid)
    cfg = models.parse_models((lay.etc / "models.toml").read_text())
    rec = RunRecord(
        run_start.new_run_id(),
        entry.id,
        entry.digest,
        entry.admin_digest,
        st.args.repo_id,
        run_start.pins_from_data(lay, st.args.repo_id, cfg),
        None,
        datetime.now(UTC).isoformat(),
        0,
    )
    run_start.register(lay, rec)
    return rec.run_id


def wipe_phase(out: Path, secrets: Path, repo_id: int | None, tag: str | None) -> int:
    """`wipe` (always): with `repo_id` and `tag`, the stand swept of what a
    run cut short left (`sweep`, best effort, logged); evidence holding a
    secret deleted (exit 2); the installed copies and the secret files
    removed."""
    require_ci_root()
    needles: list[str] = []
    token = ""
    if secrets.is_dir():
        for path in sorted(secrets.iterdir()):
            text = path.read_text(errors="replace")
            if path.name == TOKEN_FILE:
                token = text.strip()
            needles += (
                key_needles(text) if path.name == APP_KEY_FILE else [text.strip()]
            )
    leaked = scan_tree(out, [n for n in needles if len(n) >= 16])
    for path in leaked:
        path.unlink()
    if token and repo_id is not None and tag:
        try:
            swept = sweep(MachineGitHub(token), repo_id, tag)
        except Exception as exc:  # noqa: BLE001 - the local removals still run
            swept = {"стенд": f"уборка упала: {type(exc).__name__}"}
        print(f"уборка стенда: {swept}", flush=True)
    shutil.rmtree(PRODUCTION.secrets_dir, ignore_errors=True)
    try:
        exec_home = PRODUCTION.role_home("df-exec")
        exec_home.joinpath(*MACHINE_TOKEN).unlink(missing_ok=True)
    except OSError:
        pass
    shutil.rmtree(secrets, ignore_errors=True)
    if leaked:
        print(f"evidence с секретом удалено: {[str(p) for p in leaked]}", flush=True)
        return EXIT_INFRA
    return EXIT_PASS


def sweep(gh: MachineGitHub, repo_id: int, tag: str) -> dict[str, str]:
    """Close the open PRs from and delete the branches of `tag_branches(tag)`
    (a run cancelled before row 9); every step best effort, its outcome a
    word (nothing secret). A completed run leaves nothing to sweep."""
    names = set(tag_branches(tag).values())
    done: dict[str, str] = {}
    try:
        repo = gh.call("GET", f"/repositories/{repo_id}")
        gh.full_name = str(repo["full_name"]) if isinstance(repo, dict) else ""
        pulls = github.paged_array(gh.repo("/pulls"), gh.token)
    except (GitHubRefused, GitHubUnavailable, KeyError) as exc:
        return {"стенд": f"не прочитан: {type(exc).__name__}"}
    for pr in pulls:
        if not isinstance(pr, dict):
            continue
        head = pr.get("head")
        ref = head.get("ref") if isinstance(head, dict) else None
        if ref in names:
            done[f"PR #{pr.get('number')}"] = _best_effort(
                lambda n=pr.get("number"): gh.call(
                    "PATCH", gh.repo(f"/pulls/{n}"), {"state": "closed"}
                )
            )
    for branch in sorted(names):
        try:
            present = gh.ref_sha(branch) is not None
        except (GitHubRefused, GitHubUnavailable) as exc:
            done[branch] = f"не прочитана: {type(exc).__name__}"
            continue
        if present:
            done[branch] = _best_effort(
                lambda b=branch: gh.call("DELETE", gh.repo(f"/git/refs/heads/{b}"))
            )
    return done or {"стенд": "чисто"}


def _best_effort(call: Callable[[], object]) -> str:
    try:
        call()
    except (GitHubRefused, GitHubUnavailable) as exc:
        return f"не удалось: {type(exc).__name__}"
    return "сделано"


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="live_stand")
    sub = parser.add_subparsers(dest="phase", required=True)
    install = sub.add_parser("install")
    install.add_argument("--source-sha", required=True)
    install.add_argument("--out", type=Path, required=True)
    stand = sub.add_parser("stand")
    stand.add_argument("--source-sha", required=True)
    stand.add_argument("--out", type=Path, required=True)
    stand.add_argument("--secrets", type=Path, required=True)
    for name in ("--repo-id", "--app-id", "--installation-id"):
        stand.add_argument(name, type=int, required=True)
    stand.add_argument("--tag", required=True)
    wipe = sub.add_parser("wipe")
    wipe.add_argument("--out", type=Path, required=True)
    wipe.add_argument("--secrets", type=Path, required=True)
    wipe.add_argument("--repo-id", type=int)
    wipe.add_argument("--tag")
    return parser.parse_args(list(argv))


def main(argv: Sequence[str]) -> int:
    """The phase of `argv`; an infrastructure failure is exit 2."""
    ns = parse_args(argv)
    try:
        if ns.phase == "install":
            return install_phase(ns.source_sha, ns.out)
        if ns.phase == "wipe":
            return wipe_phase(ns.out, ns.secrets, ns.repo_id, ns.tag)
        if _TAG.fullmatch(ns.tag) is None:
            raise SystemExit(f"--tag {ns.tag!r}: нужен [A-Za-z0-9-]")
        args = StandArgs(
            ns.out,
            ns.secrets,
            ns.source_sha,
            ns.repo_id,
            ns.app_id,
            ns.installation_id,
            ns.tag,
        )
        return stand_phase(args)
    except ProvenanceError as exc:
        message = f"{INFRA}: происхождение: {exc}"
        record(ns.out, f"{ns.phase}-provenance-refused.json", {"refused": message})
        print(message, flush=True)
        return EXIT_INFRA
    except pytest.fail.Exception as exc:
        print(str(exc), flush=True)
        return EXIT_INFRA
    except Exception:  # noqa: BLE001 - a crash of the script is infrastructure
        traceback.print_exc()
        return EXIT_INFRA


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
