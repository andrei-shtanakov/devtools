"""The trust sources of the stage-3a root rows, backed by real git (Task 14).

`df-trust` and `approval-policy` are git repositories built here with
`/usr/bin/git`; `GitRest` answers the GitHub REST reads both sides make of
them -- the publisher (`df.publisher.history`, `trust_routes`) and the
checker (`governance.act_trust_pair`, steward's `read_policy_history`) --
from the git objects themselves, so every SHA, tree, blob id and content
agrees by construction. Every commit on a branch is the squash merge of a
PR into it (`commits/<sha>/pulls`), as the policy's history rules require.

Three ways to serve the same answers: `send` (the publisher's `Transport`),
`open_url` (`urllib`'s `open` of the checker's `AnonTransport`) and, in the
root world only, HTTPS for `api.github.com` (`roles_world.impersonate_api`).
Every request is recorded with its `Authorization` header (`calls`). No
credential is real: the history token is a fixture string.
"""

from __future__ import annotations

import base64
import io
import json
import subprocess
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from email.message import Message
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

API = "https://api.github.com/"
SYSTEM_GIT = "/usr/bin/git"
#: The rules `governance.act_trust.REQUIRED_RULES` asks of the pinned branch.
RULES = ("deletion", "non_fast_forward", "pull_request")
#: The date of the first commit; each next one is a minute later.
EPOCH_SECONDS = 1_791_800_000  # 2026-10-12T08:53:20Z
_GIT_ENV: Mapping[str, str] = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C",
}

Reply = tuple[int, object]


def git(work: Path, *args: str, env: Mapping[str, str] | None = None) -> str:
    """`git -C <work> <args>`; its stdout, a failure is `OSError`."""
    done = subprocess.run(
        [SYSTEM_GIT, "-C", str(work), *args],
        env={**_GIT_ENV, **(env or {})},
        capture_output=True,
        timeout=120,
        check=False,
    )
    if done.returncode != 0:
        raise OSError(f"git {' '.join(args)}: {done.stderr.decode().strip()}")
    return done.stdout.decode("utf-8", "replace")


def stamp(seconds: int) -> str:
    """`YYYY-MM-DDTHH:MM:SSZ` of a Unix time."""
    from datetime import UTC, datetime

    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class SourceRepo:
    """One trust source: its identity, settings and a work repo whose
    `main` history is a chain of PR squash merges (`merges`)."""

    id: int
    full_name: str
    work: Path
    private: bool = False
    allow_rebase_merge: bool = False
    rules: list[str] = field(default_factory=lambda: list(RULES))
    default_branch: str = "main"
    #: commit -> the PR whose squash merge it is.
    merges: dict[str, dict[str, object]] = field(default_factory=dict)
    clock: int = EPOCH_SECONDS

    @classmethod
    def create(cls, root: Path, repo_id: int, full_name: str) -> SourceRepo:
        """An empty repository on `main` under `root/<name>`."""
        work = root / full_name.replace("/", "--")
        work.mkdir(parents=True)
        git(work, "init", "-q", "--template=", "-b", "main")
        return cls(repo_id, full_name, work)

    def commit(self, files: Mapping[str, bytes | None], message: str) -> str:
        """A commit on `main` writing (`None`: removing) `files`, recorded as
        the squash merge of the next PR; its SHA."""
        for rel, data in files.items():
            path = self.work / rel
            if data is None:
                path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.clock += 60
        when = f"@{self.clock} +0000"
        env = {
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
            "GIT_AUTHOR_DATE": when,
            "GIT_COMMITTER_DATE": when,
        }
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "--allow-empty", "-m", message, env=env)
        sha = self.head()
        number = len(self.merges) + 1
        self.merges[sha] = {
            "number": number,
            "state": "closed",
            "merged_at": stamp(self.clock),
            "merge_commit_sha": sha,
            "base": {"ref": self.default_branch},
        }
        return sha

    def head(self, branch: str = "main") -> str:
        """The head of `branch`."""
        return git(self.work, "rev-parse", f"refs/heads/{branch}").strip()

    def reset(self, sha: str) -> None:
        """`main` back on `sha` (a rewritten history: later commits stay
        readable by SHA, as on GitHub)."""
        git(self.work, "update-ref", "refs/heads/main", sha)

    def body(self) -> dict[str, object]:
        """`/repos/<full_name>` and `/repositories/<id>` of a reader with
        read access only."""
        owner, name = self.full_name.split("/")
        return {
            "id": self.id,
            "name": name,
            "full_name": self.full_name,
            "owner": {"login": owner, "type": "User"},
            "private": self.private,
            "visibility": "private" if self.private else "public",
            "default_branch": self.default_branch,
            "allow_rebase_merge": self.allow_rebase_merge,
            "permissions": {
                "admin": False,
                "maintain": False,
                "push": False,
                "triage": False,
                "pull": True,
            },
        }


@dataclass
class GitRest:
    """The REST answers of the trust sources, from their git objects."""

    repos: list[SourceRepo]
    calls: list[tuple[str, str]] = field(default_factory=list)
    #: path -> status answered in place of the real answer.
    status: dict[str, int] = field(default_factory=dict)
    #: `/user`: the human checking the perimeter with a PAT.
    login: str = "df-fixture"
    lock: threading.Lock = field(default_factory=threading.Lock)

    def answer(self, path: str, auth: str) -> Reply:
        """`(status, JSON body)` of `GET <path>` (relative, with its query)."""
        with self.lock:
            self.calls.append((path, auth))
        if path in self.status:
            return self.status[path], {"message": "fixture status"}
        try:
            return self._answer(path)
        except OSError:
            return 404, {"message": "Not Found"}

    def _answer(self, path: str) -> Reply:
        parts = urlsplit(path)
        words = [unquote(w) for w in parts.path.strip("/").split("/")]
        query = parse_qs(parts.query)
        if words == ["user"]:
            return 200, {"login": self.login, "plan": {"name": "pro"}}
        if words[:1] == ["repositories"] and len(words) == 2:
            repo = next((r for r in self.repos if str(r.id) == words[1]), None)
            return (200, repo.body()) if repo else (404, {"message": "Not Found"})
        if words[:1] != ["repos"] or len(words) < 3:
            return 404, {"message": "Not Found"}
        repo = next(
            (r for r in self.repos if r.full_name == "/".join(words[1:3])), None
        )
        if repo is None:
            return 404, {"message": "Not Found"}
        return _repo_answer(repo, words[3:], query)

    # --- the three ways to serve it ---

    def send(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, Mapping[str, str], bytes]:
        """The publisher's `Transport` (`df.publisher.github`)."""
        if method != "GET" or not url.startswith(API):
            return 404, {}, b'{"message": "Not Found"}'
        status, answer = self.answer(url.removeprefix(API), _auth(headers))
        return status, {}, json.dumps(answer).encode("utf-8")

    def open_url(self, req: object, timeout: float) -> _Answer:
        """`urllib`'s `open` for `governance.act_anon.AnonTransport`."""
        url = str(getattr(req, "full_url", ""))
        headers = dict(getattr(req, "header_items")())
        status, answer = self.answer(url.removeprefix(API), _auth(headers))
        return _Answer(status, json.dumps(answer).encode("utf-8"))

    def paths(self) -> list[str]:
        """The paths asked, in order."""
        with self.lock:
            return [p for p, _a in self.calls]


def _auth(headers: Mapping[str, str]) -> str:
    return next((v for k, v in headers.items() if k.lower() == "authorization"), "")


def _repo_answer(
    repo: SourceRepo, rest: list[str], query: Mapping[str, list[str]]
) -> Reply:
    """`/repos/<owner>/<name>/<rest>`."""
    if not rest:
        return 200, repo.body()
    head, tail = rest[0], rest[1:]
    if head == "branches" and len(tail) == 1:
        sha = repo.head(tail[0])
        return 200, {"name": tail[0], "commit": {"sha": sha}, "protected": True}
    if head == "rules" and tail[:1] == ["branches"] and len(tail) == 2:
        return 200, [{"type": rule} for rule in repo.rules]
    if head == "rulesets":
        return _ruleset(repo, tail)
    settings = _settings(repo, head, tail)
    if settings is not None:
        return settings
    if head == "commits" and len(tail) == 1:
        return 200, commit_body(repo.work, tail[0])
    if head == "commits" and tail[1:] == ["pulls"]:
        merge = repo.merges.get(tail[0])
        return 200, [] if merge is None else [merge]
    if head == "git" and tail[:1] == ["trees"] and len(tail) == 2:
        recursive = query.get("recursive") == ["1"]
        return 200, tree_body(repo.work, tail[1], recursive=recursive)
    if head == "git" and tail[:2] == ["ref", "heads"] and len(tail) > 2:
        name = "/".join(tail[2:])
        sha = repo.head(name)
        return 200, {"ref": f"refs/heads/{name}", "object": {"sha": sha}}
    if head == "compare" and len(tail) == 1 and "..." in tail[0]:
        base, _, top = tail[0].partition("...")
        return 200, compare_body(repo.work, base, top)
    if head == "git" and tail[:1] == ["blobs"] and len(tail) == 2:
        return 200, blob_body(repo.work, tail[1])
    if head == "contents" and tail:
        ref = query.get("ref", [repo.default_branch])[0]
        return contents_body(repo.work, "/".join(tail), ref)
    return 404, {"message": "Not Found"}


#: The one ruleset of each source, without bypass actors.
RULESET_ID = 1


def _ruleset(repo: SourceRepo, tail: list[str]) -> Reply:
    """`/rulesets` (one ruleset) and `/rulesets/<id>`."""
    if not tail:
        return 200, [{"id": RULESET_ID, "name": "main", "target": "branch"}]
    if tail == [str(RULESET_ID)]:
        return 200, {
            "id": RULESET_ID,
            "name": "main",
            "target": "branch",
            "enforcement": "active",
            "bypass_actors": [],
            "rules": [{"type": rule} for rule in repo.rules],
        }
    return 404, {"message": "Not Found"}


def _settings(repo: SourceRepo, head: str, tail: list[str]) -> Reply | None:
    """The perimeter reads of `setup-trust check` beyond the rules: the
    owner is the only collaborator, nothing else may write."""
    owner = repo.full_name.split("/")[0]
    if head == "branches" and tail[1:] == ["protection"]:
        return 404, {"message": "Branch not protected"}
    if head == "keys" and not tail:
        return 200, []
    if head == "actions" and tail == ["permissions"]:
        return 200, {"enabled": False}
    if head == "collaborators" and not tail:
        perms = {"admin": True, "maintain": True, "push": True, "triage": True}
        person = {"login": owner, "role_name": "admin", "permissions": perms}
        return 200, [{**person, "type": "User"}]
    if head == "hooks" and not tail:
        return 200, []
    if head == "environments" and not tail:
        return 200, {"total_count": 0, "environments": []}
    return None


_STATUS = {"A": "added", "M": "modified", "D": "removed", "R": "renamed"}


def compare_body(work: Path, base: str, top: str) -> dict[str, object]:
    """`/compare/<base>...<top>`: the files of the diff from their merge base."""
    if _kind(work, base) != "commit" or _kind(work, top) != "commit":
        raise OSError("compare: no such commits")
    files: list[dict[str, object]] = []
    ahead = int(git(work, "rev-list", "--count", f"{base}..{top}").strip())
    behind = int(git(work, "rev-list", "--count", f"{top}..{base}").strip())
    status = {(0, 0): "identical"}.get((ahead, behind))
    if status is None:
        status = "ahead" if not behind else "behind" if not ahead else "diverged"
    out = git(work, "diff", "--name-status", "-M", f"{base}...{top}")
    for line in out.splitlines():
        words = line.split("\t")
        item: dict[str, object] = {
            "status": _STATUS[words[0][0]],
            "filename": words[-1],
        }
        if words[0].startswith("R"):
            item["previous_filename"] = words[1]
        files.append(item)
    return {"status": status, "ahead_by": ahead, "behind_by": behind, "files": files}


def _kind(work: Path, oid: str) -> str:
    return git(work, "cat-file", "-t", oid).strip()


def commit_body(work: Path, sha: str) -> dict[str, object]:
    """`/commits/<sha>`: the full SHA asked for only (`OSError` otherwise)."""
    if len(sha) != 40 or _kind(work, sha) != "commit":
        raise OSError(f"no commit {sha}")
    lines = git(work, "cat-file", "-p", sha).split("\n")
    tree = next(line.split()[1] for line in lines if line.startswith("tree "))
    parents = [line.split()[1] for line in lines if line.startswith("parent ")]
    when = next(line for line in lines if line.startswith("committer ")).split()[-2]
    person = {"name": "t", "email": "t@example.invalid", "date": stamp(int(when))}
    message = "\n".join(lines[lines.index("") + 1 :]).strip()
    return {
        "sha": sha,
        "commit": {
            "tree": {"sha": tree},
            "author": person,
            "committer": person,
            "message": message,
        },
        "parents": [{"sha": p} for p in parents],
    }


def tree_body(work: Path, sha: str, *, recursive: bool) -> dict[str, object]:
    """`/git/trees/<sha>[?recursive=1]`: the complete listing."""
    if _kind(work, sha) != "tree":
        raise OSError(f"no tree {sha}")
    flags = ["-r", "-t"] if recursive else []
    entries = []
    for line in git(work, "ls-tree", "-l", *flags, sha).splitlines():
        meta, path = line.split("\t", 1)
        mode, kind, oid, size = meta.split()
        entry: dict[str, object] = {
            "path": path,
            "mode": mode,
            "type": kind,
            "sha": oid,
        }
        if kind == "blob":
            entry["size"] = int(size)
        entries.append(entry)
    return {"sha": sha, "tree": entries, "truncated": False}


def _blob_bytes(work: Path, oid: str) -> bytes:
    if _kind(work, oid) != "blob":
        raise OSError(f"no blob {oid}")
    done = subprocess.run(
        [SYSTEM_GIT, "-C", str(work), "cat-file", "blob", oid],
        env=dict(_GIT_ENV),
        capture_output=True,
        timeout=60,
        check=True,
    )
    return done.stdout


def blob_body(work: Path, oid: str) -> dict[str, object]:
    """`/git/blobs/<oid>`: base64 content."""
    raw = _blob_bytes(work, oid)
    return {
        "sha": oid,
        "size": len(raw),
        "encoding": "base64",
        "content": base64.encodebytes(raw).decode("ascii"),
    }


def contents_body(work: Path, path: str, ref: str) -> Reply:
    """`/contents/<path>?ref=<ref>`: a file, or `404`."""
    try:
        oid = git(work, "rev-parse", f"{ref}:{path}").strip()
    except OSError:
        return 404, {"message": "Not Found"}
    if _kind(work, oid) != "blob":
        return 404, {"message": "Not Found"}
    raw = _blob_bytes(work, oid)
    return 200, {
        "type": "file",
        "name": path.rsplit("/", 1)[-1],
        "path": path,
        "sha": oid,
        "size": len(raw),
        "encoding": "base64",
        "content": base64.encodebytes(raw).decode("ascii"),
    }


# ---------- the two sources of the world ----------

TRUST_ID, POLICY_ID = 21, 22
TRUST_REPO = "df-fixture/df-trust"
POLICY_REPO = "df-fixture/approval-policy"
POLICY_ADMIN = "df-fixture"
#: A fixture string of the history token's form; not a credential.
HISTORY_TOKEN = "ghp_dfFixtureHistoryTokenNotASecret0"
#: The policy set of the first activation (`act_policy.POLICY_FILES`).
POLICY_FILES: Mapping[str, bytes] = {
    "policy/approvers.env": (
        b"AUTHORIZED_APPROVER_ACCOUNTS=alice\n"
        b"AUTHORIZED_MERGER_ACCOUNTS=df-fixture-app[bot]\n"
    ),
    "policy/modes.yaml": (
        b"schema: approval-policy-modes/v1\nmodes:\n  df-fixture/product: review\n"
    ),
    "policy/actors.yaml": (
        b"version: 1\nhuman_identities: [github:alice]\n"
        b"agent_identities: [github:df-fixture-app]\n"
    ),
}


def source_env(boot: str) -> bytes:
    """`source.env` of `df-trust` naming the policy and its activation."""
    return (
        f"APPROVAL_POLICY_REPO={POLICY_REPO}\nAPPROVAL_POLICY_BRANCH=main\n"
        f"BOOTSTRAP_ACTIVATION={boot}\nPOLICY_ADMINS={POLICY_ADMIN}\n"
    ).encode()


def epochs_yaml(anchors: list[str]) -> bytes:
    """The epochs journal: one open epoch per anchor, the earlier closed."""
    lines = ["schema: approval-policy-epochs/v1", "epochs:"]
    for n, anchor in enumerate(anchors, start=1):
        lines += [f"  - epoch: {n}", f"    anchor: {anchor}"]
        if n < len(anchors):
            lines += [
                f"    last_confirmed: {anchor}",
                "    valid_until: '2026-10-12T00:00:00Z'",
                "    closed_reason: fixture",
            ]
    return ("\n".join(lines) + "\n").encode()


@dataclass
class TrustSources:
    """`df-trust` (`source.env`, `epochs.yaml`) and `approval-policy` (one
    activation), and their REST answers."""

    trust: SourceRepo
    policy: SourceRepo
    rest: GitRest
    boot: str

    def trust_toml(self, history: Path | None) -> str:
        """`trust.toml` binding the two sources (with the history key)."""
        text = (
            'schema = "df/trust/v1"\n'
            'host = "github.com"\n'
            f"df_trust_repository_id = {self.trust.id}\n"
            f'df_trust_full_name = "{self.trust.full_name}"\n'
            'df_trust_branch = "main"\n'
            f"approval_policy_repository_id = {self.policy.id}\n"
            'transport = "public"\n'
        )
        if history is not None:
            text += f'history_gh_config_dir = "{history}"\n'
        return text

    def add_epochs(self, count: int) -> str:
        """`df-trust` grown to `count` epochs, one valid transition per
        commit (the last epoch closed, a new one opened on a fresh policy
        activation committed first); the new head."""
        anchors = epoch_anchors(self.trust)
        while len(anchors) < count:
            n = len(anchors) + 1
            text = POLICY_FILES["policy/approvers.env"] + f"# epoch {n}\n".encode()
            anchors.append(
                self.policy.commit({"policy/approvers.env": text}, f"epoch {n}")
            )
            journal = {"epochs.yaml": epochs_yaml(anchors)}
            self.trust.commit(journal, f"epoch {n}")
        return self.trust.head()


def epoch_anchors(trust: SourceRepo) -> list[str]:
    """The anchors of the journal on the head of `trust`."""
    text = (trust.work / "epochs.yaml").read_text()
    return [line.split()[1] for line in text.splitlines() if "anchor:" in line]


def build_sources(root: Path) -> TrustSources:
    """The policy's first activation, then `df-trust` C0 (`source.env` and
    the journal of one epoch anchored there) and its child X."""
    policy = SourceRepo.create(root, POLICY_ID, POLICY_REPO)
    boot = policy.commit(dict(POLICY_FILES), "activation 1")
    trust = SourceRepo.create(root, TRUST_ID, TRUST_REPO)
    files = {"source.env": source_env(boot), "epochs.yaml": epochs_yaml([boot])}
    trust.commit(files, "C0")
    trust.commit({"README.md": b"df-trust\n"}, "X")
    return TrustSources(trust, policy, GitRest([trust, policy]), boot)


def new_trust_repo(sources: TrustSources, repo_id: int, full_name: str) -> SourceRepo:
    """A fresh `df-trust` (spec 6.1, the exit of 65 epochs): `source.env`
    and a journal of one epoch, both on the policy's current activation;
    served next to the others and made `sources.trust`."""
    anchor = sources.policy.head()
    root = sources.trust.work.parent
    trust = SourceRepo.create(root, repo_id, full_name)
    files = {"source.env": source_env(anchor), "epochs.yaml": epochs_yaml([anchor])}
    trust.commit(files, "C0")
    with sources.rest.lock:
        sources.rest.repos.append(trust)
    sources.trust = trust
    return trust


def history_profile(path: Path) -> Path:
    """A history profile directory (`hosts.yml` with the fixture token)."""
    path.mkdir(parents=True)
    (path / "hosts.yml").write_text(
        "github.com:\n"
        "    users:\n"
        f"        df-history:\n            oauth_token: {HISTORY_TOKEN}\n"
        "    git_protocol: https\n"
        f"    oauth_token: {HISTORY_TOKEN}\n"
        "    user: df-history\n"
    )
    return path


def fake_gh(directory: Path) -> Path:
    """A `gh` whose `auth token` prints the fixture token (local tests;
    the root world uses the pinned official `gh`)."""
    directory.mkdir(parents=True)
    gh = directory / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = auth ]; then echo {HISTORY_TOKEN}; exit 0; fi\nexit 1\n'
    )
    gh.chmod(0o755)
    return gh


class _Answer(io.BytesIO):
    """A response of `urllib`'s `open` (status, headers, body)."""

    def __init__(self, status: int, data: bytes) -> None:
        super().__init__(data)
        self.status = status
        self.headers = Message()
