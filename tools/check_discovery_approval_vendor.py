"""Verify the vendored discovery approval copy (`contracts/discovery-approval/v1`).

consistency — files match the digests recorded in PINNED.txt (NOT their origin).
provenance  — files are the bytes of upstream discovery at the pinned commit;
              unreachable upstream is unknown, never ok.
drift       — has upstream discovery moved past the pin?

Exit: 0 ok · 1 failed · 3 unknown. Same contract as check_discovery_vendor.py;
the network layer is reused from it, only the surface and the paths differ.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_discovery_vendor as base  # noqa: E402

REPO = "andrei-shtanakov/discovery"
#: Manifest key → path inside upstream discovery.
UPSTREAM_PATH = {
    "approval.py": "src/discovery/approval.py",
    "hashing.py": "src/discovery/hashing.py",
    "gate_check.py": "src/discovery/contract/gate_check.py",
}
EXPECTED_SURFACE = frozenset(UPSTREAM_PATH)


def resolve_dest() -> Path:
    """Configured destination (DISCOVERY_APPROVAL_VENDOR_DEST) or devtools' copy."""
    env_dest = os.environ.get("DISCOVERY_APPROVAL_VENDOR_DEST")
    if env_dest:
        return Path(env_dest)
    root = Path(__file__).resolve().parent.parent
    return root / "contracts" / "discovery-approval" / "v1"


CONTRACT = resolve_dest()


def read_pinned() -> tuple[str, dict[str, str]]:
    """Commit and per-file digests from PINNED.txt."""
    commit, manifest = "", {}
    for line in (CONTRACT / "PINNED.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("commit:"):
            commit = line.split(":", 1)[1].strip()
        elif line and not line.startswith("upstream:"):
            rel, digest = line.rsplit(" ", 1)
            manifest[rel] = digest
    return commit, manifest


def github_fetch(commit: str, rel: str) -> bytes | None:
    """Upstream blob of manifest key `rel` at `commit`."""
    saved = base.CONTENTS_API
    base.CONTENTS_API = (
        f"https://api.github.com/repos/{REPO}/contents/{{rel}}?ref={{commit}}"
    )
    try:
        return base.github_fetch(commit, UPSTREAM_PATH[rel])
    finally:
        base.CONTENTS_API = saved


def github_head_fetch(commit: str, rel: str) -> bytes | None:
    """Upstream discovery default-branch HEAD sha."""
    saved = (base.REPO_API, base.COMMITS_API)
    base.REPO_API = f"https://api.github.com/repos/{REPO}"
    base.COMMITS_API = f"https://api.github.com/repos/{REPO}/commits/{{ref}}"
    try:
        return base.github_head_fetch(commit, rel)
    finally:
        base.REPO_API, base.COMMITS_API = saved


def verify(
    mode: Literal["consistency", "provenance"], fetch: base.Fetcher | None = None
) -> base.Verdict:
    """Consistency against PINNED.txt or provenance against upstream."""
    commit, manifest = read_pinned()
    missing = EXPECTED_SURFACE - set(manifest)
    if missing:
        return base.Verdict(
            "failed", "PINNED.txt does not cover: " + ", ".join(sorted(missing))
        )
    if mode == "consistency":
        drifted = [
            rel
            for rel, digest in manifest.items()
            if hashlib.sha256((CONTRACT / rel).read_bytes()).hexdigest() != digest
        ]
        if drifted:
            return base.Verdict("failed", f"files differ from PINNED.txt: {drifted}")
        return base.Verdict("ok", f"{len(manifest)} files match their digests")
    fetch = fetch or github_fetch
    mismatched: list[str] = []
    for rel in manifest:
        blob = fetch(commit, rel)
        if blob is None:
            return base.Verdict("unknown", f"upstream unreachable while reading {rel}")
        if blob != (CONTRACT / rel).read_bytes():
            mismatched.append(rel)
    if mismatched:
        return base.Verdict(
            "failed", f"differ from upstream@{commit[:8]}: {mismatched}"
        )
    return base.Verdict("ok", f"bytes identical to upstream@{commit[:8]}")


def drift(fetch: base.Fetcher | None = None) -> base.Verdict:
    """Has upstream discovery moved past the pin?"""
    commit, _ = read_pinned()
    head = (fetch or github_head_fetch)("HEAD", "HEAD")
    if head is None:
        return base.Verdict("unknown", "upstream HEAD unreachable")
    head_sha = head.decode("utf-8").strip()
    if head_sha == commit:
        return base.Verdict("ok", f"pin matches upstream HEAD {head_sha[:8]}")
    return base.Verdict("failed", f"pin {commit[:8]} is behind upstream {head_sha[:8]}")


def main() -> int:
    """CLI: `check_discovery_approval_vendor.py {consistency|provenance|drift}`."""
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["consistency", "provenance", "drift"])
    args = parser.parse_args()
    try:
        verdict = drift() if args.mode == "drift" else verify(args.mode)
    except OSError as exc:
        verdict = base.Verdict("failed", f"cannot read vendored copy: {exc}")
    print(f"{verdict.status}: {verdict.detail}")
    return {"ok": 0, "failed": 1}.get(verdict.status, 3)


if __name__ == "__main__":
    raise SystemExit(main())
