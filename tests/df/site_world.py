"""Local stand-ins of the `site/` sources of the admin tree (2b Task 5).

No real PyPI or GitHub: a test PyYAML sdist (`<top>/lib/yaml/...`), a bare
steward repository (`src/steward/...`), a `uv.lock` pinning both, and a
`git` wrapper that sends the lock's https URL to the local repository
(`url.<file>.insteadOf`). `site_world` uses two tiny stand-in packages (any
lane); `real_site_world` the pure-Python files of the installed `yaml` and
`steward` (the same files as the sdist's `lib/yaml` and the commit's
`src/steward`; the caller skips without the `governance` group). The
templates are built once per process; tests copy them.
"""

from __future__ import annotations

import atexit
import functools
import gzip
import hashlib
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from df.admin_tree import (
    LEGACY_2B,
    SITE_SRC,
    STEWARD_REPO,
    SitePins,
)
from df.paths import Layout

ROOT = Path(__file__).resolve().parents[2]
SYSTEM_GIT = Path("/usr/bin/git")
#: The lock's steward URL; the wrapper sends it to the local repository.
STEWARD_URL = "https://github.com/andrei-shtanakov/steward"
YAML_URL = "https://files.pythonhosted.org/packages/xx/pyyaml-6.0.3.tar.gz"
SDIST_TOP = "pyyaml-6.0.3"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
}


@dataclass(frozen=True)
class SiteWorld:
    """The built sources: sdist bytes and the template bare repository."""

    sdist: bytes
    repo: Path
    commit: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.sdist).hexdigest()

    def pins(
        self, yaml_url: str = YAML_URL, steward_url: str = STEWARD_URL
    ) -> SitePins:
        return SitePins(yaml_url, self.sha256, steward_url, self.commit)


def _pure_files(package: Path) -> list[Path]:
    return sorted(
        p
        for p in package.rglob("*")
        if p.is_file()
        and "__pycache__" not in p.parts
        and p.suffix not in (".so", ".pyc", ".pyd", ".dylib")
    )


def tar_bytes(members: list[tuple[str, bytes | None, int]], *, gz: bool) -> bytes:
    """A tar (gzip'ed if `gz`) of `(name, data or None for a dir, mode)`,
    mtime 0 (the gzip header's too), so the bytes are deterministic."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, data, mode in members:
            info = tarfile.TarInfo(name)
            info.mtime = 0
            info.mode = mode
            if data is None:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    if not gz:
        return buf.getvalue()
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as packed:
        packed.write(buf.getvalue())
    return out.getvalue()


def sdist_members(package: Path) -> list[tuple[str, bytes | None, int]]:
    """The test sdist: `package` as `lib/yaml`, plus files outside it the
    extraction must ignore."""
    members: list[tuple[str, bytes | None, int]] = [
        (f"{SDIST_TOP}/", None, 0o755),
        (f"{SDIST_TOP}/setup.py", b"raise SystemExit('never run')\n", 0o644),
        (f"{SDIST_TOP}/lib/_yaml/__init__.py", b"# outside lib/yaml\n", 0o644),
    ]
    for path in _pure_files(package):
        rel = path.relative_to(package).as_posix()
        members.append((f"{SDIST_TOP}/lib/yaml/{rel}", path.read_bytes(), 0o644))
    return members


def git(*args: str, cwd: Path | None = None) -> str:
    done = subprocess.run(
        [str(SYSTEM_GIT), *args],
        cwd=cwd,
        env=GIT_ENV,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return done.stdout


def make_steward_repo(base: Path, package: Path) -> tuple[Path, str]:
    """A bare repo with `package` as `src/steward` and a README."""
    work = base / "steward-work"
    for path in _pure_files(package):
        target = work / "src" / "steward" / path.relative_to(package)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    (work / "README.md").write_text("steward test copy\n")
    git("init", "-q", "--template=", str(work))
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", "steward", cwd=work)
    commit = git("rev-parse", "HEAD", cwd=work).strip()
    bare = base / "steward.git"
    git("clone", "-q", "--bare", "--template=", str(work), str(bare))
    git("repack", "-a", "-d", "-q", cwd=bare)
    shutil.rmtree(work)
    return bare, commit


def build_world(yaml_package: Path, steward_package: Path) -> SiteWorld:
    """Sources from the two package directories (removed at exit)."""
    base = Path(tempfile.mkdtemp(prefix="df-site-world-"))
    atexit.register(shutil.rmtree, base, True)
    repo, commit = make_steward_repo(base, steward_package)
    return SiteWorld(tar_bytes(sdist_members(yaml_package), gz=True), repo, commit)


#: The stand-in packages of `site_world`: `{package: {relative file: text}}`.
STAND_IN = {
    "yaml": {
        "__init__.py": "__version__ = '6.0.3'\nfrom yaml.nodes import Node\n",
        "nodes.py": "class Node:\n    pass\n",
    },
    "steward": {
        "__init__.py": "__version__ = '0.2.0'\n",
        "approvalfacts/__init__.py": "",
        "approvalfacts/v3/__init__.py": "VERSION = 3\n",
    },
}


@functools.cache
def stand_in_dir() -> Path:
    """The stand-in packages (`STAND_IN`) as directories, once per process."""
    base = Path(tempfile.mkdtemp(prefix="df-site-pkgs-"))
    atexit.register(shutil.rmtree, base, True)
    for package, files in STAND_IN.items():
        for rel, text in files.items():
            target = base / package / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
    return base


@functools.cache
def site_world() -> SiteWorld:
    """Sources of the two stand-in packages, once per process."""
    return build_world(stand_in_dir() / "yaml", stand_in_dir() / "steward")


@functools.cache
def real_site_world() -> SiteWorld:
    """Sources of the installed `yaml` and `steward` (governance group)."""
    import steward
    import yaml

    return build_world(Path(yaml.__file__).parent, Path(steward.__file__).parent)


def lock_packages(pins: SitePins) -> str:
    """The `[[package]]` entries of the two `site/` sources, as uv writes them
    (appended to a project's lock they stay unreached: nothing installs them)."""
    commit = pins.steward_commit
    git_source = f"{pins.steward_url}?rev={commit}#{commit}"
    return f"""
[[package]]
name = "pyyaml"
version = "6.0.3"
source = {{ registry = "https://pypi.org/simple" }}
sdist = {{ url = "{pins.yaml_url}", hash = "sha256:{pins.yaml_sha256}", size = 1 }}

[[package]]
name = "steward"
version = "0.2.0"
source = {{ git = "{git_source}" }}
"""


def uv_lock(pins: SitePins) -> bytes:
    """A `uv.lock` pinning `pins` the way uv writes them."""
    return ("version = 1\nrevision = 3\n" + lock_packages(pins)).encode()


def git_wrapper(directory: Path, repo: Path, *, extra: str = "") -> Path:
    """`git` that sends `STEWARD_URL` to `repo` (and runs `extra` first)."""
    path = directory / "git-wrapper"
    path.write_text(
        "#!/bin/sh\n"
        "export GIT_CONFIG_COUNT=1\n"
        f"export GIT_CONFIG_KEY_0='url.file://{repo}.insteadOf'\n"
        f"export GIT_CONFIG_VALUE_0='{STEWARD_URL}'\n"
        f"{extra}"
        f'exec {SYSTEM_GIT} "$@"\n'
    )
    path.chmod(0o755)
    return path


def prime_cache(layout: Layout, world: SiteWorld | None = None) -> None:
    """`admin-home/site-src` as an earlier install leaves it (idempotent)."""
    world = world or site_world()
    cache = layout.admin_home / SITE_SRC
    cache.mkdir(mode=0o700, exist_ok=True)
    sdist = cache / f"pyyaml-{world.sha256}.tar.gz"
    if not sdist.exists():
        sdist.write_bytes(world.sdist)
    if not (cache / STEWARD_REPO).exists():
        shutil.copytree(world.repo, cache / STEWARD_REPO, symlinks=True)


def write_checkout(
    ref: Path, world: SiteWorld | None = None, *, real: bool = False
) -> None:
    """The admin-tree part of a trusted `devtools` checkout at `ref`: the
    governance closure and contract files -- one-line stand-ins, or with
    `real` this tree's own (the crash model reads every byte of a tree on
    each capture, so route worlds keep them small) -- and a `uv.lock`
    pinning the test sources (`df/` is the caller's)."""
    world = world or site_world()
    for rel in LEGACY_2B.files():
        target = ref / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if real:
            shutil.copyfile(ROOT / rel, target)
        else:
            target.write_text(f"# stand-in of {rel}\n")
    (ref / "uv.lock").write_bytes(uv_lock(world.pins()))


def walk_files(tree: Path) -> list[str]:
    """Every object under `tree`, relative, directories with a trailing `/`."""
    out: list[str] = []
    for dirpath, dirs, files in os.walk(tree):
        rel = Path(dirpath).relative_to(tree)
        out += [f"{(rel / d).as_posix()}/" for d in dirs]
        out += [(rel / f).as_posix() for f in files]
    return sorted(out)
