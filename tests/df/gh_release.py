"""The pinned official `gh` for the root worlds and the live stands.

`install_gh` downloads the release asset of `GH_PINNED_VERSION` for this
platform, checks its sha256 against the release's checksums and writes the
binary as root `0755` under `<top>/gh-bin` (a trusted path for `cli set
gh`). Used by `tests/df/test_root_gh_shared_profile.py` and (through its
`_install_gh`) `tests/df/test_acceptance_root_trust.py` (CI root), and by
`tests/df/live_trust_stand.py` (stage 3a live stand); never by an ordinary
test.
"""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
import urllib.request
import zipfile
from pathlib import Path

from df import platform as plat
from tests.df.root_world import infra

GH_VERSION = plat.GH_PINNED_VERSION
#: From `gh_2.102.1_checksums.txt` of the official release.
GH_ASSETS = {
    "macos-arm64": (
        f"gh_{GH_VERSION}_macOS_arm64.zip",
        "08bfef98b50c553df122bb34ee8dcf415cae7d3b108891488964a0df07aa2aeb",
    ),
    "linux-x86_64": (
        f"gh_{GH_VERSION}_linux_amd64.tar.gz",
        "77b9e297e94c50336823d0f84de7ef1be60cba704601fa0d98f129f47a94987b",
    ),
}
GH_URL = f"https://github.com/cli/cli/releases/download/v{GH_VERSION}/"


def install_gh(top: Path) -> Path:
    """The pinned official `gh`, sha256-checked, as root `0755`."""
    asset, digest = GH_ASSETS[plat.detect_platform()]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(GH_URL + asset, timeout=120) as resp:
            data = resp.read()
    except OSError as exc:
        infra(f"загрузка {asset}: {exc}")
    if hashlib.sha256(data).hexdigest() != digest:
        infra(f"{asset}: sha256 не совпал")
    member = f"{asset.removesuffix('.zip').removesuffix('.tar.gz')}/bin/gh"
    if asset.endswith(".zip"):
        binary = zipfile.ZipFile(io.BytesIO(data)).read(member)
    else:
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            extracted = tar.extractfile(member)
            assert extracted is not None
            binary = extracted.read()
    bin_dir = top / "gh-bin"
    bin_dir.mkdir(mode=0o755)
    gh = bin_dir / "gh"
    gh.write_bytes(binary)
    os.chmod(gh, 0o755)
    return gh
