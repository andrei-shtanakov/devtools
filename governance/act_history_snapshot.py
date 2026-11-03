"""Deny-by-default `Ops` for `act_history` without a `gh` profile (plan decision 10).

The precheck program runs as an unprivileged OS role. It reads delivery history from
a temporary bare repository fetched over HTTP(S) and forge facts from GitHub REST
through `TokenTransport`. `SnapshotHistory` implements exactly the `Ops` methods
`act_history` calls; any other attribute is `AttributeError`.

git auth: the header travels only in the git process environment (`GIT_CONFIG_*`,
`Authorization: Basic base64("x-access-token:<token>")`) — never in argv or a config
file; Bearer stays REST-only. git runs with an explicit minimal environment (no
system/global config, no prompts, http(s) only, no redirects) and every call honours
an absolute `time.monotonic()` deadline: on expiry the whole git process group is
killed and `DeadlineExceeded` is raised. REST calls honour the same deadline (each
call's transport timeout is capped by the time left). `DeadlineExceeded` is not in
`act_time.OPS_FAILURES`, so `act_history` never turns it into `Unavailable`: it
propagates out of `load_history` to the caller (`unset: deadline`).

Proxy and CA variables (`HTTPS_PROXY`, `SSL_CERT_FILE`, `GIT_SSL_CAINFO`, …) are
deliberately not passed to git, matching `TokenTransport`, which ignores proxies:
a role host behind a proxy gets a git/transport failure, not a proxied fetch
(operator docs, Tasks 4/7).
"""

from __future__ import annotations

import base64
import contextlib
import os
import re
import shutil
import signal
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from governance.act_forge_http import TokenTransport
from governance.act_git_rows import (
    FIRST_PARENT_LOG,
    FirstParentRows,
    parse_first_parent,
)

_FULL_NAME = re.compile(r"[A-Za-z0-9-]+/(?!\.\.?$)[A-Za-z0-9_.-]+")
_REF = re.compile(r"refs/[A-Za-z0-9_./-]+")
#: Repository-local key naming the product; `fetch_branch` builds its URL from it.
NAME_KEY = "df.fullname"
_FETCH = ("fetch", "--quiet", "--no-tags", "--no-write-fetch-head", "--refmap=")
#: Not secret, so argv: the auth header must not follow a redirect to another host.
_NO_REDIRECTS = ("-c", "http.followRedirects=false")


Clock = Callable[[], float]


class DeadlineExceeded(Exception):
    """The absolute deadline passed (deliberately outside `OPS_FAILURES`)."""


@dataclass(frozen=True)
class Scope:
    """`act_history.HistoryScope` of the precheck (no `RunState`)."""

    ws_id: str
    repo_slug: str
    target_dir: str


def git_auth_env(token: str) -> dict[str, str]:
    """`GIT_CONFIG_*` carrying `http.extraHeader` with Basic `x-access-token`."""
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
    }


def _env(home: Path, token: str | None) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(home),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "http:https",
    }
    return env | git_auth_env(token) if token is not None else env


def _timeout(deadline: float, clock: Clock, what: str) -> float:
    """Seconds left before `deadline`; none left — `DeadlineExceeded`."""
    left = deadline - clock()
    if left <= 0:
        raise DeadlineExceeded(f"{what}: deadline passed")
    return left


def _git(
    git_dir: Path,
    *args: str,
    home: Path,
    deadline: float,
    clock: Clock,
    token: str | None = None,
    text: bool = True,
) -> subprocess.CompletedProcess:
    """One git call over `git_dir`; the token, if any, only via environment."""
    timeout = _timeout(deadline, clock, "git")
    proc = subprocess.Popen(
        ["git", f"--git-dir={git_dir}", *_NO_REDIRECTS, *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        env=_env(home, token),
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except BaseException as exc:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        if isinstance(exc, subprocess.TimeoutExpired):
            raise DeadlineExceeded("git: deadline passed") from None
        raise
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


def _require(done: subprocess.CompletedProcess, what: str) -> None:
    if done.returncode != 0:
        tail = (done.stderr or "").strip().splitlines()[-1:]
        raise OSError(f"snapshot: {what}: exit {done.returncode}: {''.join(tail)}")


def open_snapshot(
    home: Path,
    full_name: str,
    refs: Sequence[str],
    token: str,
    *,
    git_base: str,
    deadline: float,
    clock: Clock = time.monotonic,
) -> Path:
    """Fetch `refs` (each `+ref:ref`, no `--filter`) of `full_name` into a new bare
    repository `home/repo.git` and return its path; the caller removes it.

    `home` is an existing caller-owned directory and git's `HOME`; `deadline` is an
    absolute `clock()` (default `time.monotonic`) instant. Invalid input —
    `ValueError`; git failure — `OSError`; deadline — `DeadlineExceeded`. On any
    failure the repository is removed.
    """
    if not _FULL_NAME.fullmatch(full_name):
        raise ValueError(f"snapshot: full_name {full_name!r} is not owner/repo")
    bad = [r for r in refs if not _full_ref(r)]
    if bad or not refs:
        raise ValueError(f"snapshot: refs {list(refs)!r} are not full ref names")
    git_dir = home / "repo.git"
    git_dir.mkdir(mode=0o700)
    try:
        for what, args, secret in (
            ("init", ("init", "--quiet", "--bare", "--template="), None),
            ("config", ("config", NAME_KEY, full_name), None),
            (
                f"fetch {full_name}",
                (*_FETCH, _url(git_base, full_name), *(f"+{r}:{r}" for r in refs)),
                token,
            ),
        ):
            done = _git(
                git_dir, *args, home=home, deadline=deadline, clock=clock, token=secret
            )
            _require(done, what)
    except BaseException:
        shutil.rmtree(git_dir, ignore_errors=True)
        raise
    return git_dir


def _full_ref(ref: str) -> bool:
    return _REF.fullmatch(ref) is not None and ".." not in ref


def _url(git_base: str, full_name: str) -> str:
    return f"{git_base.rstrip('/')}/{full_name}.git"


class SnapshotHistory:
    """`Ops` of `act_history` over a snapshot from `open_snapshot`.

    git methods read `git_dir` (`target_dir` arguments are ignored); forge methods
    read REST through `transport`. `deadline` (absolute `clock()` instant, default
    clock `time.monotonic`) bounds every git and REST call: past it every method
    raises `DeadlineExceeded`. Methods not defined here do not exist.
    """

    def __init__(
        self,
        git_dir: Path,
        transport: TokenTransport,
        token: str,
        *,
        git_base: str,
        deadline: float,
        clock: Clock = time.monotonic,
    ) -> None:
        self._git_dir, self._transport, self._token = git_dir, transport, token
        self._git_base, self._deadline, self._clock = git_base, deadline, clock

    def __repr__(self) -> str:
        return f"SnapshotHistory({self._git_dir!s})"

    def _run(
        self, *args: str, token: str | None = None, text: bool = True
    ) -> subprocess.CompletedProcess:
        return _git(
            self._git_dir,
            *args,
            home=self._git_dir.parent,
            deadline=self._deadline,
            clock=self._clock,
            token=token,
            text=text,
        )

    def first_parent_changes(self, target_dir: str, ref: str) -> FirstParentRows | None:
        """First-parent `ref` of the snapshot (as `RealOps.first_parent_changes`)."""
        done = self._run(*FIRST_PARENT_LOG, "--end-of-options", ref)
        return parse_first_parent(done.stdout) if done.returncode == 0 else None

    def show_file(self, target_dir: str, ref: str, path: str) -> str | None:
        """`git show <ref>:<path>` of the snapshot."""
        done = self._run("show", "--end-of-options", f"{ref}:{path}")
        return done.stdout if done.returncode == 0 else None

    def show_file_bytes(self, target_dir: str, ref: str, path: str) -> bytes | None:
        """`git show <ref>:<path>` of the snapshot, exact bytes."""
        done = self._run("show", "--end-of-options", f"{ref}:{path}", text=False)
        return done.stdout if done.returncode == 0 else None

    def rev_parse(self, target_dir: str, ref: str) -> str | None:
        """SHA of `ref` in the snapshot; `None` — absent."""
        done = self._run("rev-parse", "--verify", "--quiet", "--end-of-options", ref)
        return done.stdout.strip() or None

    def fetch_branch(self, target_dir: str, branch: str) -> bool:
        """Fetch one more ref (squash head `refs/pull/N/head`) into the snapshot
        from the product named at `open_snapshot`."""
        ref = branch if branch.startswith("refs/") else f"refs/heads/{branch}"
        if not _full_ref(ref):
            return False
        name = self._run("config", "--get", NAME_KEY).stdout.strip()
        url = _url(self._git_base, name)
        done = self._run(*_FETCH, url, f"+{ref}:{ref}", token=self._token)
        return done.returncode == 0

    def prs_containing_commit(self, repo_slug: str, sha: str) -> list[dict]:
        """`GET repos/{slug}/commits/{sha}/pulls` in the `gh` row shape
        (`number`, `state` OPEN|CLOSED|MERGED by `merged_at`, `mergeCommit`)."""
        body = self._rest(repo_slug, f"commits/{sha}/pulls")
        if not isinstance(body, list) or not all(_is_pull(p) for p in body):
            raise ValueError(f"forge: commits/{sha[:12]}/pulls: unexpected shape")
        return [
            {
                "number": p["number"],
                "state": "MERGED"
                if p.get("merged_at") is not None
                else str(p.get("state", "")).upper(),
                "mergeCommit": p.get("merge_commit_sha"),
            }
            for p in body
        ]

    def pr_facts(self, repo_slug: str, pr: int) -> dict:
        """`GET repos/{slug}/pulls/{pr}` → `{"headRefOid"}` (`None` if not named)."""
        body = self._rest(repo_slug, f"pulls/{int(pr)}")
        head = body.get("head") if isinstance(body, dict) else None
        return {"headRefOid": head.get("sha") if isinstance(head, dict) else None}

    def _rest(self, repo_slug: str, tail: str) -> object:
        if not _FULL_NAME.fullmatch(repo_slug):
            raise ValueError(f"forge: repo_slug {repo_slug!r} is not owner/repo")
        left = _timeout(self._deadline, self._clock, "forge")
        transport, saved = self._transport, self._transport._timeout
        transport._timeout = min(saved, left)  # each request of this call
        try:
            status, body = transport.get_json(f"repos/{repo_slug}/{tail}")
        except OSError:
            # a transport failure past the deadline is the deadline, not a fact
            _timeout(self._deadline, self._clock, "forge")
            raise
        finally:
            transport._timeout = saved
        if status != 200:
            raise OSError(f"forge: HTTP {status}: repos/{repo_slug}/{tail}")
        return body


def _is_pull(item: object) -> bool:
    return (
        isinstance(item, dict)
        and isinstance(item.get("number"), int)
        and not isinstance(item.get("number"), bool)
    )
