"""Root acceptance of `/etc/sudoers.d/df` on the real host (stage 2a, Task 3).

R7: the parser of `df.sudoers` is proven on the real `sudo -n -l -U <name>`
of the three roles and the runner's own account, after the real `visudo`
and `install`. The captures are kept as evidence under
`$DF_STAGE1_OUT/sudoers/` and replace the provisional fixtures of
`tests/df/fixtures/sudoers/` (README there). R9: the test refuses a host
that already has the file or a role, never creates `df-build`, and removes
the file and the roles in `finally`. Runs only in CI df-stage1.
"""

from __future__ import annotations

import os
import pwd
import re
import shutil
import stat
from pathlib import Path

import pytest

from df import platform as plat
from df import roles, sudoers
from df.paths import Layout
from tests.df.conftest import stage1_out
from tests.df.root_world import infra, remove_service_user, write_evidence

pytestmark = pytest.mark.root

#: The CI runner's account: the human operator of the generated rules.
OPERATOR = "runner"
PROBED = (*roles.ROLE_USERS, OPERATOR)
#: Host name of the fixtures (as in `fixtures/sudo-l/README.md`).
HOST = "mac-mini" if plat.detect_platform() == "macos-arm64" else "ubuntu-ci"
_HOST_LINE = re.compile(r"( on )\S+([:.])$", re.MULTILINE)


def test_sudoers_real(acceptance_top: Path) -> None:
    """The real `visudo` accepts the text; after `install` the real `sudo -l`
    of every role and of the operator has no policy problem and the output
    of `df-publish` is not I/O-logged."""
    _require_clean_host()
    layout = _world(acceptance_top)
    try:
        roles.ensure_roles(layout)
        text = sudoers.render(layout, OPERATOR)
        assert sudoers.validate(text) == []
        sudoers.install(text)
        st = os.stat(sudoers.SUDOERS_FILE)
        assert (st.st_uid, stat.S_IMODE(st.st_mode)) == (0, 0o440)
        write_evidence(stage1_out(), "sudoers/df", text)
        for name in PROBED:
            _capture(layout, name)
        problems = {
            name: sudoers.policy_problems(name, layout=layout, operator=OPERATOR)
            for name in PROBED
        }
        assert problems == dict.fromkeys(PROBED, [])
        assert sudoers.io_log_problems(layout) == []
    finally:
        _discard(sudoers.SUDOERS_FILE)
        for name in roles.ROLE_USERS:
            remove_service_user(name)
        shutil.rmtree(layout.root, ignore_errors=True)


def _require_clean_host() -> None:
    """A leftover file or role is someone else's: refuse, touch nothing."""
    if os.path.lexists(sudoers.SUDOERS_FILE):
        infra(f"{sudoers.SUDOERS_FILE} уже есть")
    leftover = [name for name in roles.ROLE_USERS if _known(name)]
    if leftover:
        infra(f"роли остались от прошлого прогона: {leftover}")
    if not _known(OPERATOR):
        infra(f"нет учётки оператора {OPERATOR}")


def _world(top: Path) -> Layout:
    """`<R>` with the directories `create_service_user` writes into."""
    root = top / "sudoers"
    os.mkdir(root, 0o755)
    layout = Layout(Path(os.path.realpath(root)))
    for path in (layout.install_dir, layout.quarantine, layout.build_home.parent):
        os.makedirs(path, mode=0o755)
    return layout


def _capture(layout: Layout, name: str) -> None:
    """`LC_ALL=C sudo -n -l -U <name>` as evidence, raw and as a fixture."""
    result = plat.run_command([sudoers._SUDO, "-n", "-l", "-U", name])
    text = result.stdout.decode("utf-8", "replace")
    out = stage1_out()
    write_evidence(out, f"sudoers/{name}.txt", text)
    write_evidence(out, f"sudoers/{name}.rc", f"{result.returncode}\n")
    write_evidence(out, f"sudoers/{name}.err", result.stderr.decode("utf-8", "replace"))
    write_evidence(out, f"sudoers/{name}.fixture.txt", _as_fixture(layout, text))
    assert result.launched and result.returncode == 0, result


def _as_fixture(layout: Layout, text: str) -> str:
    """`<R>` paths as production ones, the host name as the fixtures'."""
    text = text.replace(f"{layout.root}/opt/df/", "/opt/df/")
    return _HOST_LINE.sub(rf"\g<1>{HOST}\g<2>", text)


def _known(name: str) -> bool:
    try:
        pwd.getpwnam(name)
    except KeyError:
        return False
    return True


def _discard(path: Path) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
