"""Ordinary tests of the stage-2a acceptance helpers (Task 14).

The sudo-diagnostic classifier, the fixture form of a capture, the reader
of sudo's I/O log and the fake GitHub, the last driven by the production
clients (`df.publisher.github.self_check`, `launch_release.check_github`)
with a key generated here and the host's `openssl`.
"""

from __future__ import annotations

import gzip
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from df import launch_release
from df.paths import MACHINE_TOKEN, Layout
from df.publisher import github
from df.stage2_config import (
    PublisherConfig,
    dump_publisher,
    parse_products,
)
from tests.df.fake_github import (
    FakeGitHub,
    Installation,
    jwt_valid,
    serve_fake_github,
)
from tests.df.roles_world import (
    APP_ID,
    HOST,
    INSTALLATION_ID,
    MACHINE_TOKEN_TEXT,
    MACHINE_USER,
    PRODUCT_ID,
    classify_sudo,
    fixture_form,
    found_in,
    io_sessions,
    products_text,
    scenario,
)

OPENSSL = shutil.which("openssl")
needs_openssl = pytest.mark.skipif(OPENSSL is None, reason="нет openssl")

# ---------- sudo diagnostics ----------


@pytest.mark.parametrize(
    ("rc", "stderr", "kind"),
    [
        (
            1,
            (
                "Sorry, user df-review is not allowed to execute "
                "'/opt/df/libexec/df-publish merge' as df-publisher on h.\n"
            ),
            "not-allowed",
        ),
        (
            1,
            "sudo: sorry, you are not allowed to preserve the environment\n",
            "preserve-env",
        ),
        (1, "sudo: a password is required\n", "password"),
        (1, "sudo: a terminal is required to read the password\n", "password"),
        (1, "sudo: something else\n", None),
        (2, "Sorry, user x is not allowed to execute 'y' as z on h.\n", None),
    ],
)
def test_classify_sudo(rc: int, stderr: str, kind: str | None) -> None:
    assert classify_sudo(rc, stderr) == kind


def test_password_wins_over_a_denial() -> None:
    """A diagnostic that asks for a password is never read as a denial."""
    text = "sudo: a password is required\nSorry, user x is not allowed to run y\n"
    assert classify_sudo(1, text) == "password"


def test_fixture_form() -> None:
    root = Path("/private/var/tmp-df/df-acc-1/r1")
    text = f"Sorry, ... '{root}/opt/df/libexec/df-publish merge' on runner-7.x.\n"
    form = fixture_form(text, root, ["runner-7.x", "runner-7"])
    assert form == f"Sorry, ... '/opt/df/libexec/df-publish merge' on {HOST}.\n"


# ---------- the I/O log reader ----------


def _session(base: Path, streams: dict[str, bytes], *, packed: bool) -> Path:
    path = base / "00" / "00" / "01"
    path.mkdir(parents=True)
    (path / "log").write_text("1:runner:df-exec::\n/\n/opt/df/libexec/df-launch\n")
    (path / "timing").write_text("")
    for name, data in streams.items():
        (path / name).write_bytes(gzip.compress(data) if packed else data)
    return path


@pytest.mark.parametrize("packed", [False, True])
def test_io_sessions_reads_streams(tmp_path: Path, packed: bool) -> None:
    (tmp_path / "seq").write_text("000001")
    path = _session(tmp_path, {"stdout": b"nonce-1", "stdin": b"req"}, packed=packed)
    sessions = io_sessions(tmp_path)
    assert [s.path for s in sessions] == [path]
    assert "df-launch" in sessions[0].meta
    assert found_in(sessions, "nonce-1", ("stdout",)) == [path]
    assert found_in(sessions, "nonce-1", ("stderr", "ttyout")) == []
    assert found_in(sessions, "req", ("stdin",)) == [path]


def test_io_sessions_unknown_file_is_infrastructure(tmp_path: Path) -> None:
    path = _session(tmp_path, {"stdout": b"x"}, packed=False)
    (path / "mystery").write_bytes(b"?")
    with pytest.raises(pytest.fail.Exception, match="инфраструктура"):
        io_sessions(tmp_path)


def test_io_sessions_absent_dir(tmp_path: Path) -> None:
    assert io_sessions(tmp_path / "none") == []


# ---------- the fake GitHub, through the production clients ----------


@pytest.fixture()
def keys(tmp_path: Path) -> tuple[Path, Path]:
    assert OPENSSL is not None
    key, pub = tmp_path / "app.pem", tmp_path / "app.pub"
    argv = [OPENSSL, "genpkey", "-algorithm", "RSA", "-out", str(key)]
    subprocess.run(
        [*argv, "-pkeyopt", "rsa_keygen_bits:2048"], check=True, capture_output=True
    )
    done = subprocess.run(
        [OPENSSL, "pkey", "-in", str(key), "-pubout"], check=True, capture_output=True
    )
    pub.write_bytes(done.stdout)
    return key, pub


@pytest.fixture()
def fake(keys: tuple[Path, Path]) -> Iterator[FakeGitHub]:
    assert OPENSSL is not None
    with serve_fake_github(
        scenario(), public_key=keys[1], openssl=Path(OPENSSL)
    ) as server:
        yield server


@needs_openssl
def test_jwt_checked(keys: tuple[Path, Path]) -> None:
    assert OPENSSL is not None
    key, pub = keys
    now = int(time.time())
    token = github.app_jwt(APP_ID, key, openssl=Path(OPENSSL), now=now)
    ok = {"public_key": pub, "openssl": Path(OPENSSL), "now": now}
    assert jwt_valid(token, app_id=APP_ID, **ok)
    assert not jwt_valid(token, app_id=APP_ID + 1, **ok)
    assert not jwt_valid(token[:-4] + "AAAA", app_id=APP_ID, **ok)
    assert not jwt_valid(token, app_id=APP_ID, **{**ok, "now": now + 3600})


@needs_openssl
def test_self_check_against_fake(fake: FakeGitHub, keys: tuple[Path, Path]) -> None:
    assert OPENSSL is not None
    products = parse_products(products_text())
    found = github.self_check(
        products, APP_ID, keys[0], openssl=Path(OPENSSL), api_base=fake.url
    )
    assert [(i.id, i.repository_ids) for i in found.installations] == [
        (INSTALLATION_ID, (PRODUCT_ID,))
    ]
    assert found.problems == ()
    assert fake.paths("bad") == []
    fake.scenario.installations.append(Installation(12, "o", (99,), {}))
    again = github.self_check(
        products, APP_ID, keys[0], openssl=Path(OPENSSL), api_base=fake.url
    )
    assert again.problems == ("app_installation_outside_products: 12",)


@needs_openssl
def test_wrong_key_is_refused(fake: FakeGitHub, tmp_path: Path) -> None:
    assert OPENSSL is not None
    other = tmp_path / "other.pem"
    subprocess.run(
        [OPENSSL, "genpkey", "-algorithm", "RSA", "-out", str(other)],
        check=True,
        capture_output=True,
    )
    with pytest.raises(github.GitHubRefused):
        github.self_check(
            parse_products(products_text()),
            APP_ID,
            other,
            openssl=Path(OPENSSL),
            api_base=fake.url,
        )
    assert fake.paths("bad") == ["GET /app/installations"]


def test_check_github_against_fake(fake: FakeGitHub, tmp_path: Path) -> None:
    root = tmp_path.resolve() / "root"
    lay = Layout(root)
    lay.etc.mkdir(parents=True)
    cfg = PublisherConfig({}, fake.url)
    (lay.etc / "publisher.toml").write_text(dump_publisher(cfg))
    (lay.etc / "products.toml").write_text(products_text())
    home = tmp_path / "home"
    token = home.joinpath(*MACHINE_TOKEN)
    token.parent.mkdir(parents=True)
    token.write_text(MACHINE_TOKEN_TEXT + "\n")
    token.chmod(0o600)
    report, code = launch_release.check_github(
        lay, home, launch_release.urllib_transport
    )
    assert code == 0
    assert report["login"] == MACHINE_USER
    row = report["products"][str(PRODUCT_ID)]  # type: ignore[index]
    assert row["permissions"]["push"] is True  # type: ignore[index]
    assert fake.paths("machine") == ["GET /user", "GET /repos/df-fixture/product"]
