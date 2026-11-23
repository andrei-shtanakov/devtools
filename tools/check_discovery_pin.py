"""Verify that the installer's discovery pin carries the vendored brief contract.

`lite/components.toml` pins the discovery commit that `install.sh` installs;
`governance/discovery_contract/` holds the brief contract the devtools gate
enforces. On different contract versions discovery calls a brief complete
that the gate then refuses at publication (devtools#830).

Exit: 0 ok · 1 failed · 3 unknown — the contract of check_discovery_vendor.py;
unreachable upstream is unknown, never ok.
"""

from __future__ import annotations

import re
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_discovery_vendor as base  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
COMPONENTS = ROOT / "lite" / "components.toml"
VENDORED = ROOT / "governance" / "discovery_contract" / "DISCOVERY-BRIEF-CONTRACT.md"
REPO = "andrei-shtanakov/discovery"
UPSTREAM_CONTRACT = "src/discovery/contract/DISCOVERY-BRIEF-CONTRACT.md"
_HEADER = re.compile(r"^# Discovery Brief .*\((v\d+\.\d+)\)", re.MULTILINE)

Fetcher = Callable[[str, str], bytes | None]


def contract_version(text: str) -> str | None:
    """Version from the contract's title line (`… (v1.3)`); none — `None`."""
    found = _HEADER.search(text)
    return found.group(1) if found else None


def pinned_sha(path: Path = COMPONENTS) -> str:
    """Discovery commit pinned for the installer."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return str(data["repos"]["discovery"]["sha"])


def github_fetch(commit: str, rel: str) -> bytes | None:
    """Blob `rel` of upstream discovery at `commit` (base fetcher, other repo)."""
    saved = base.CONTENTS_API
    base.CONTENTS_API = (
        f"https://api.github.com/repos/{REPO}/contents/{{rel}}?ref={{commit}}"
    )
    try:
        return base.github_fetch(commit, rel)
    finally:
        base.CONTENTS_API = saved


def verify(fetch: Fetcher = github_fetch) -> base.Verdict:
    """Contract version at the pinned discovery commit vs the vendored one."""
    vendored = contract_version(VENDORED.read_text(encoding="utf-8"))
    if vendored is None:
        return base.Verdict("failed", f"{VENDORED.name}: no version in the title")
    sha = pinned_sha()
    blob = fetch(sha, UPSTREAM_CONTRACT)
    if blob is None:
        return base.Verdict("unknown", f"discovery {sha[:8]} unreachable")
    pinned = contract_version(blob.decode("utf-8", errors="replace"))
    if pinned != vendored:
        return base.Verdict(
            "failed",
            f"discovery {sha[:8]} carries contract {pinned}, gate is {vendored}",
        )
    return base.Verdict("ok", f"discovery {sha[:8]} carries contract {vendored}")


def main() -> int:
    """CLI: `check_discovery_pin.py`."""
    try:
        verdict = verify()
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        verdict = base.Verdict("unknown", f"could not read the pin: {exc}")
    print(f"discovery-pin: {verdict.status} — {verdict.detail}")
    return {"ok": 0, "failed": 1, "unknown": 3}[verdict.status]


if __name__ == "__main__":
    raise SystemExit(main())
