"""Task 7 leaves of the acceptance routes (spec 3.3, "Протоколы примитивов").

Each function is one protocol of `PROTOCOLS`: its effects are the protocol's
steps, in order, through `df.fsutil` (`fault_point`, `sync_dir`) and the
helper that owns its part (`receive_tree`). Every object is reached by name
through the descriptor of its parent. A tree of `df-build` is never opened by
root (spec 3.8): `prepare_dir` opens `releases/<id>` before the transfer and
afterwards only `fsync`s and closes that descriptor; `seal` touches the name
only by `fstatat` and `renameat`. A missing installer directory is a named
refusal (F31); any other `OSError` is the leaf's `StepFailed`.
"""

from __future__ import annotations

import contextlib
import os
import stat
from collections.abc import Callable, Iterator
from pathlib import Path

from df import accept, fsutil, receive
from df import platform as plat
from df.config import ConfigError, InstallConfig, load_accounts
from df.digest import TreeError, tree_digest
from df.journal import StepFailed
from df.paths import Layout
from df.platform import PlatformName
from df.receive import CopyLimits

#: What the digest of a release copy leaves out: `release.json` is written
#: after the copy is accepted. The `copied` mark and the route's check of it
#: are one call (`release_digest`), so they cannot disagree (M6).
RELEASE_DIGEST_EXCLUDE = frozenset({"release.json"})
#: The environment of the copy's producer (`tar` as `df-build`).
TAR_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C"}
#: Bytes of the producer's stderr a refused copy reports (F21).
STDERR_REPORT_BYTES = 4096

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_PREPARE_STEP = "build-dir"
_SEAL_STEP = "seal"
_COPY_STEP = "copy"
_VERIFY_STEP = "verify"


def _noop(point: str, rel: str) -> None:
    """Default test hook of `accept_copy`."""


def release_digest(tree: Path) -> str:
    """`tree_digest` of a release copy, the one exclusion of marks and checks."""
    return tree_digest(tree, exclude=RELEASE_DIGEST_EXCLUDE)


def prepare_dir(layout: Layout, release_id: str) -> None:
    """Protocol `prepare_dir`: `releases/<id>` made by root, handed to
    `df-build` by `fchown` of a descriptor taken while it is root's and empty.

    `df-build`'s uid and gid come from its `accounts.toml` record (state
    `ready`). After the `fchown` the path is never opened again (spec 3.8).
    Effects: `created` -> `chown` -> `sync_dir(release_root)` ->
    `sync_dir(releases)`.
    """
    uid, gid = _build_ids(layout)
    path = layout.release_dir(release_id)
    with _refusing(_PREPARE_STEP), _open_dir(layout.releases, _PREPARE_STEP) as top:
        os.mkdir(path.name, 0o700, dir_fd=top)
        fd = os.open(path.name, _DIR_FLAGS, dir_fd=top)
        try:
            os.fchmod(fd, 0o700)  # the mode is not left to the umask
            fsutil.fault_point("created", path)
            os.fchown(fd, uid, gid)
            fsutil.fault_point("chown", path)
            fsutil.sync_dir(fd)
        finally:
            os.close(fd)
        fsutil.sync_dir(top)


def _build_ids(layout: Layout) -> tuple[int, int]:
    """uid and gid of `df-build` from its ready `accounts.toml` record."""
    try:
        account = load_accounts(layout.accounts).get(plat.BUILD_USER)
    except ConfigError as exc:
        raise StepFailed(_PREPARE_STEP, f"accounts.toml: {exc}") from exc
    if account is None or account.state != "ready":
        raise StepFailed(
            _PREPARE_STEP, f"нет готовой записи {plat.BUILD_USER} в accounts.toml"
        )
    return account.uid, account.gid


def verify_chmod(layout: Layout, release_id: str) -> None:
    """Protocol `verify_chmod`: the root of `releases/<id>` becomes `0755`.

    The name is checked by `fstatat` through `releases/` (a directory of
    root, not a link) before it is opened without following links; the
    mode is set on that descriptor. Effects: `chmod` -> `sync_dir(release_root)`.
    """
    path = layout.release_dir(release_id)
    with _refusing(_VERIFY_STEP), _open_dir(layout.releases, _VERIFY_STEP) as top:
        before = _root_dir_at(top, path, _VERIFY_STEP)
        fd = os.open(path.name, _DIR_FLAGS, dir_fd=top)
        try:
            after = os.fstat(fd)
            if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
                raise StepFailed(_VERIFY_STEP, f"{path} подменён")
            os.fchmod(fd, 0o755)
            fsutil.fault_point("chmod", path)
            fsutil.sync_dir(fd)
        finally:
            os.close(fd)


def seal(layout: Layout, release_id: str) -> None:
    """Protocol `seal`: `releases/<id>` moves to `staging/<id>/tree`.

    Variant `new` (no `staging/<id>`): the container is made root `0711`.
    Variant `reuse` (an empty `staging/<id>` of root, left by a crash after
    its `mkdir`): no `mkdir`. The tree is touched only by `fstatat` and
    `renameat`. Effects (`new`): `created` -> `renamed` -> `sync_dir(releases)`
    -> `sync_dir(staging)`. Effects (`reuse`): `renamed` -> `sync_dir(releases)`
    -> `sync_dir(staging)`; `sync_dir(staging)` is `staging/`, then
    `staging/<id>/`.
    """
    tree = layout.release_dir(release_id)
    box = layout.staging_dir(release_id)
    with (
        _refusing(_SEAL_STEP),
        _open_dir(layout.releases, _SEAL_STEP) as top,
        _open_dir(layout.staging, _SEAL_STEP) as staging,
    ):
        if not stat.S_ISDIR(
            os.stat(tree.name, dir_fd=top, follow_symlinks=False).st_mode
        ):
            raise StepFailed(_SEAL_STEP, f"{tree}: не каталог")
        new = not _exists_at(staging, box.name)
        if new:
            os.mkdir(box.name, 0o711, dir_fd=staging)
        _root_dir_at(staging, box, _SEAL_STEP)
        box_fd = os.open(box.name, _DIR_FLAGS, dir_fd=staging)
        try:
            if new:
                os.fchmod(box_fd, 0o711)
                fsutil.fault_point("created", box)
            elif os.listdir(box_fd):
                raise StepFailed(_SEAL_STEP, f"{box} не пуст")
            os.rename(tree.name, "tree", src_dir_fd=top, dst_dir_fd=box_fd)
            fsutil.fault_point("renamed", layout.staging_tree(release_id))
            fsutil.sync_dir(top)
            fsutil.sync_dir(staging)
            fsutil.sync_dir(box_fd)
        finally:
            os.close(box_fd)


def accept_copy(
    layout: Layout,
    release_id: str,
    *,
    install: InstallConfig,
    platform: PlatformName,
    limits: CopyLimits = CopyLimits(),
    _hook: Callable[[str, str], None] = _noop,
) -> str:
    """Protocol `accept_copy`: `df-build`'s tar of `staging/<id>/tree` received
    by root into `releases/<id>`; returns the copy's `release_digest`.

    The producer, the partial copy and both syncs are `receive_tree`'s; this
    function adds no sync. A refused stream is `StepFailed("copy", "<reason>;
    stderr: <tail>")` and the partial copy stays for the route (F21).
    Effects: `receive_tree()` -> `tree_digest()`.
    """
    release = layout.release_dir(release_id)
    argv = [
        str(install.approved_cli["tar"]),
        *plat.TAR_FLAGS[platform],
        "-cf",
        "-",
        "-C",
        str(layout.staging_tree(release_id)),
        ".",
    ]
    producer = plat.spawn_as(
        plat.BUILD_USER,
        argv,
        env=TAR_ENV,
        cwd=layout.build_home,
        network=False,
        reap_seconds=limits.reap_seconds,
    )
    try:
        receive.receive_tree(
            producer, layout.releases, release.name, limits=limits, _hook=_hook
        )
    except TreeError as exc:
        tail = producer.stderr_tail()[-STDERR_REPORT_BYTES:]
        text = tail.decode("utf-8", "replace").strip()
        raise StepFailed(_COPY_STEP, f"{exc}; stderr: {text}") from exc
    try:
        return release_digest(release)
    except (TreeError, OSError) as exc:
        raise StepFailed(_COPY_STEP, f"{release}: {exc}") from exc


# ---------- helpers ----------


@contextlib.contextmanager
def _refusing(step: str) -> Iterator[None]:
    """An `OSError` of a leaf is its named refusal."""
    try:
        yield
    except OSError as exc:
        raise StepFailed(step, str(exc)) from exc


@contextlib.contextmanager
def _open_dir(path: Path, step: str) -> Iterator[int]:
    """An installer directory, not a link; missing -> `StepFailed` (F31)."""
    try:
        fd = os.open(path, _DIR_FLAGS)
    except OSError as exc:
        raise StepFailed(step, f"нет каталога {path}: {exc.strerror}") from exc
    try:
        yield fd
    finally:
        os.close(fd)


def _exists_at(dir_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _root_dir_at(dir_fd: int, path: Path, step: str) -> os.stat_result:
    """`fstatat` of `path.name`: a directory of root, else `StepFailed`."""
    st = os.stat(path.name, dir_fd=dir_fd, follow_symlinks=False)
    if not stat.S_ISDIR(st.st_mode):
        raise StepFailed(step, f"{path}: не каталог")
    owner = fsutil.owner_uid(st)
    if owner != accept.ROOT_UID:
        raise StepFailed(step, f"{path}: владелец uid {owner}, не root")
    return st
