"""Filesystem layout (spec 2.2). All paths are derived from one root."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from df.config import ConfigError

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _check_id(release_id: str) -> str:
    if not _ID.fullmatch(release_id):
        raise ConfigError(f"invalid release id: {release_id!r}")
    return release_id


@dataclass(frozen=True)
class Layout:
    """Every installer path; tests substitute `root`."""

    root: Path

    def __post_init__(self) -> None:
        if not self.root.is_absolute() or self.root != Path(
            os.path.realpath(self.root)
        ):
            raise ConfigError(f"layout root is not canonical: {self.root}")

    @property
    def opt(self) -> Path:
        """`/opt/df`."""
        return self.root / "opt" / "df"

    @property
    def etc(self) -> Path:
        """`/etc/df`."""
        return self.root / "etc" / "df"

    @property
    def var(self) -> Path:
        """`/var/lib/df`."""
        return self.root / "var" / "lib" / "df"

    @property
    def install_dir(self) -> Path:
        """Installer state directory."""
        return self.var / "install"

    @property
    def releases(self) -> Path:
        """Registered release trees."""
        return self.opt / "releases"

    @property
    def staging(self) -> Path:
        """Builder staging area."""
        return self.opt / "staging"

    @property
    def quarantine(self) -> Path:
        """Quarantine for discarded staging trees."""
        return self.staging / ".quarantine"

    @property
    def current(self) -> Path:
        """Symlink to the active release."""
        return self.opt / "current"

    @property
    def admin(self) -> Path:
        """Administrative code per release."""
        return self.opt / "admin"

    @property
    def admin_python(self) -> Path:
        """Administrative Python installs."""
        return self.opt / "admin-python"

    @property
    def bin_dir(self) -> Path:
        """Launcher directory."""
        return self.opt / "bin"

    @property
    def launcher(self) -> Path:
        """Public command."""
        return self.bin_dir / "darkfactory"

    @property
    def entry(self) -> Path:
        """Launcher entry script."""
        return self.bin_dir / "darkfactory-entry.py"

    @property
    def local_bin(self) -> Path:
        """`/usr/local/bin`: the public command's link, if one is created."""
        return self.root / "usr" / "local" / "bin"

    @property
    def journal(self) -> Path:
        """Audit journal."""
        return self.install_dir / "journal.jsonl"

    @property
    def lock(self) -> Path:
        """Writer lock."""
        return self.install_dir / "lock"

    @property
    def generation(self) -> Path:
        """Generation counter."""
        return self.install_dir / "generation"

    @property
    def pending(self) -> Path:
        """Unfinished installation intent."""
        return self.install_dir / "pending.toml"

    @property
    def accounts(self) -> Path:
        """Service accounts."""
        return self.install_dir / "accounts.toml"

    @property
    def admin_home(self) -> Path:
        """`HOME` and cache of root's `uv` for the admin Python (scratch, `0700`)."""
        return self.install_dir / "admin-home"

    @property
    def marks_dir(self) -> Path:
        """Acceptance marks."""
        return self.install_dir / "accept"

    @property
    def verify_dir(self) -> Path:
        """Verification evidence."""
        return self.var / "verify"

    @property
    def doctor_dir(self) -> Path:
        """Doctor working directory."""
        return self.var / "doctor"

    @property
    def probes_dir(self) -> Path:
        """Doctor probes."""
        return self.doctor_dir / "probes"

    @property
    def probe_cwd(self) -> Path:
        """Working directory for probes."""
        return self.doctor_dir / "probe-cwd"

    @property
    def build_home(self) -> Path:
        """Home of the `df-build` account."""
        base = "Users" if sys.platform == "darwin" else "home"
        return self.root / base / "df-build"

    def release_dir(self, release_id: str) -> Path:
        """Registered release tree."""
        return self.releases / _check_id(release_id)

    def admin_dir(self, release_id: str) -> Path:
        """Administrative code of a release."""
        return self.admin / _check_id(release_id)

    def staging_dir(self, release_id: str) -> Path:
        """Staging directory of a release."""
        return self.staging / _check_id(release_id)

    def mark_file(self, release_id: str) -> Path:
        """Acceptance mark of a release (`df/accept/v1`)."""
        return self.marks_dir / f"{_check_id(release_id)}.toml"

    def staging_tree(self, release_id: str) -> Path:
        """Builder tree inside staging."""
        return self.staging_dir(release_id) / "tree"


def default_layout() -> Layout:
    """Production layout rooted at `/`."""
    return Layout(Path("/"))
