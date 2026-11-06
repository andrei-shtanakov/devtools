"""The 2b → 3a upgrade of the stage-3a root rows (Task 14, owner decision 7 (a)).

The 2b code is devtools at `CODE_2B` (`291cb30`, the `core` pin of the
umbrella manifest `be2c76f`), taken from this checkout's history (the job
checks out the full history) into `<top>/devtools-2b`. It installs as
stage 1's `installed_world` does -- `bootstrap init` with the profile
substituted for the install only (R91) -- but in its own process
(`DRIVER_2B`: the 2b checkout first on `sys.path`, `install._profile`
replaced), since the 2b `df` and this one cannot share a process. The same
driver runs the 2b `install` of a pin of this branch (the installed entry's
writer half, as `RootWorld.entry`).

`candidate_v2_request` is a well-formed protocol-v2 `op: merge` request of
a candidate built by this checkout's publisher code: the 2b-built admin
tree answers it `tree_incomplete` before importing v2; a complete one
answers from the v2 program.
"""

from __future__ import annotations

import io
import json
import os
import secrets
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from df.paths import Layout
from df.publisher import precheck, trust_routes
from df.publisher.precheck import PrFacts
from df.publisher.trust_routes import MergeTrust, Session
from df.publisher.trust_state import Observation, Pinned
from df.publisher.trust_types import PolicyPin
from df.stage2_config import TrustBinding
from tests.df.root_world import (
    PROFILE_NAME,
    PYTHON,
    REPO_ROOT,
    InstalledWorld,
    RootWorld,
    assert_committed,
    current_id,
    infra,
)

#: devtools of stage 2b: the `core` pin of the umbrella manifest `be2c76f`.
CODE_2B = "291cb30cb9807ffc90d0775e7ae9b2e534694474"
SYSTEM_GIT = "/usr/bin/git"

#: `argv[1]`: the 2b checkout, the pin and the `darkfactory` words. Runs
#: them with the 2b code as bootstrap (`init`) or as the installed entry's
#: writer half (anything else), the profile substituted (R91).
DRIVER_2B = """
import json, os, sys
from pathlib import Path
spec = json.loads(sys.argv[1])
sys.path.insert(0, spec["src"])
from df import cli, install, launcher_entry, state
from df.manifest import Profile, RepoPin
from df.paths import Layout
pin = RepoPin("devtools", spec["url"], spec["sha"], ())
profile = Profile(spec["profile"], spec["python"], (pin,))
install._profile = lambda _cfg: profile
lay = Layout(Path("/"))
argv = spec["argv"]
if argv[0] == "init":
    code = cli.main(argv, mode="bootstrap", read=None, lock_fd=None, layout=lay)
else:
    paths = launcher_entry.EntryPaths("/")
    fd = launcher_entry.acquire(paths)
    try:
        snap = launcher_entry.take_snapshot(paths, "writer")
        read = launcher_entry.typed_read(state, snap)
        code = cli.main(argv, mode="writer", read=read, lock_fd=fd, layout=lay)
    finally:
        os.close(fd)
sys.exit(code)
"""

#: The 2b fixture repo's paths: the 2b `ADMIN_TREE_PATHS` (its own list).
PATHS_2B = """
import json, sys
sys.path.insert(0, sys.argv[1])
from tests.df.root_world import ADMIN_TREE_PATHS
print(json.dumps(list(ADMIN_TREE_PATHS)))
"""


def checkout_2b(top: Path) -> Path:
    """`CODE_2B`'s tree under `<top>/devtools-2b` (root reads the runner's
    checkout: `safe.directory`)."""
    target = top / "devtools-2b"
    target.mkdir(mode=0o755)
    argv = [SYSTEM_GIT, "-c", f"safe.directory={REPO_ROOT}", "-C", str(REPO_ROOT)]
    done = subprocess.run(
        [*argv, "archive", "--format=tar", CODE_2B],
        capture_output=True,
        timeout=300,
        check=False,
        env={"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": str(top)},
    )
    if done.returncode != 0:
        infra(f"git archive {CODE_2B[:12]}: {done.stderr.decode()[-400:]}")
    with tarfile.open(fileobj=io.BytesIO(done.stdout)) as tar:
        tar.extractall(target, filter="data")
    return target


def paths_2b(src: Path) -> list[str]:
    """The admin tree's paths of the 2b checkout, by its own code."""
    done = _python(["-I", "-c", PATHS_2B, str(src)])
    try:
        found = json.loads(done.stdout)
    except ValueError:
        found = None
    if not isinstance(found, list):
        infra(f"ADMIN_TREE_PATHS 2b: {done.returncode} {done.stderr[-600:]!r}")
    return [str(p) for p in cast(list[object], found)]


def run_2b(src: Path, world: RootWorld, argv: list[str], url: str, sha: str) -> int:
    """`darkfactory <argv>` by the 2b code with the pin `url@sha`."""
    spec = {
        "src": str(src),
        "url": url,
        "sha": sha,
        "profile": PROFILE_NAME,
        "python": PYTHON,
        "argv": argv,
    }
    done = _python(["-I", "-B", "-c", DRIVER_2B, json.dumps(spec)])
    if done.returncode not in (0, 1, 2, 3, 4, 5):
        infra(f"2b {argv[0]}: {done.returncode} {done.stderr[-800:]!r}")
    sys.stderr.write(done.stderr.decode("utf-8", "replace")[-4000:])
    return done.returncode


def install_2b(src: Path):  # noqa: ANN201 - the `roles_world` install hook
    """The hook of `roles_world`: the 2b `bootstrap init` of the world's pin
    (the fixture repo of the 2b code)."""

    def install(world: RootWorld) -> InstalledWorld:
        code = run_2b(src, world, world.init_argv(), world.repo.url, world.repo.sha)
        if code != 0:
            infra(f"установка кодом 2b: код {code}")
        assert_committed(world.layout)
        rid = current_id(world.layout)
        if rid is None:
            infra("установка кодом 2b: нет current")
        assert rid is not None
        return InstalledWorld(world, rid)

    return install


def _python(args: list[str]) -> subprocess.CompletedProcess[bytes]:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, *args],
        capture_output=True,
        timeout=3600,
        check=False,
        env=env,
        cwd="/",
    )


def candidate_v2_request(layout: Layout, full_name: str, product: int) -> bytes:
    """A protocol-v2 `op: merge` request of a candidate, as the publisher
    builds it (`precheck._request` and `trust_routes.merge_fields`), on a
    keyless binding: the v2 program answers it by name
    (`history_token_required`), an incomplete tree never imports it."""
    del layout
    binding = TrustBinding(
        "github.com", 21, "df-fixture/df-trust", "main", 22, "public"
    )
    admin = "sha256:" + "a" * 64
    sess = Session(cast(Any, None), binding, admin, cast(Any, None), None)
    x = "1" * 40
    pinned = Pinned(x, 1, "d" * 64, trust_routes.RULES_VERSION, admin)
    policy = PolicyPin("df-fixture/approval-policy", "main", "f" * 40)
    obs = Observation(7, binding.root_id, "c" * 64, "merge", x, x, policy, 1, pinned)
    oc = SimpleNamespace(
        product=SimpleNamespace(
            repository_id=product, full_name=full_name, dag_variant="full"
        ),
        api_base="http://127.0.0.1:9",
        git_base="http://127.0.0.1:9",
    )
    head = secrets.token_hex(20)
    facts = PrFacts(product, "spec/ws-up-approve-1-1-1", "master", "c" * 40, ())
    trust = MergeTrust(sess, obs, "df-fixture-app[bot]", "workstreams/ws-up/spec")
    v1 = precheck._request(  # noqa: SLF001 - the publisher's own builder
        "candidate", cast(Any, oc), 7, head, facts, "ghs_fixtureNotAToken0"
    )
    data = {**v1, **trust_routes.merge_fields(trust, "full")}
    return json.dumps(data).encode("utf-8")
