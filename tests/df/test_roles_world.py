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

from df import launch_release, sudoers
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
    PUBLISHER,
    UnjudgedGrant,
    classify_sudo,
    fixture_form,
    found_in,
    grants_cover,
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
        (1, "sudo: a password is required\n", "password-required"),
        (1, "sudo: a terminal is required to read the password\n", "password-required"),
        (1, "sudo: something else\n", None),
        (2, "Sorry, user x is not allowed to execute 'y' as z on h.\n", None),
    ],
)
def test_classify_sudo(rc: int, stderr: str, kind: str | None) -> None:
    assert classify_sudo(rc, stderr) == kind


#: The class of each committed capture (`fixtures/sudo-diag/README.md`).
CAPTURED = {
    "df-review-merge": "password-required",
    "df-exec-publish-review": "password-required",
    "df-exec-extra-argument": "password-required",
    "df-exec-self-check": "password-required",
    "df-exec-sudo-E": "preserve-env",
    "df-exec-preserve-env": "preserve-env",
}
CAPTURES = Path(__file__).parent / "fixtures" / "sudo-diag"


@pytest.mark.parametrize("os_dir", ["macos", "linux"])
def test_classify_sudo_on_captures(os_dir: str) -> None:
    """R14-2: the classifier on the real diagnostics of both platforms;
    every committed capture is listed."""
    here = CAPTURES / os_dir
    assert sorted(p.stem for p in here.glob("*.txt")) == sorted(CAPTURED)
    for case, kind in CAPTURED.items():
        rc = int((here / f"{case}.rc").read_text())
        assert classify_sudo(rc, (here / f"{case}.txt").read_text()) == kind, case


def test_password_wins_over_a_denial() -> None:
    """A diagnostic that asks for a password is never read as a denial."""
    text = "sudo: a password is required\nSorry, user x is not allowed to run y\n"
    assert classify_sudo(1, text) == "password-required"


def test_fixture_form() -> None:
    root = Path("/private/var/tmp-df/df-acc-1/r1")
    text = f"Sorry, ... '{root}/opt/df/libexec/df-publish merge' on runner-7.x.\n"
    form = fixture_form(text, root, ["runner-7.x", "runner-7"])
    assert form == f"Sorry, ... '/opt/df/libexec/df-publish merge' on {HOST}.\n"


# ---------- `no_rule`'s judge of sudo's listing (R14-10a, R14-14 M1) ----------

SUDOERS_FIXTURES = Path(__file__).parent / "fixtures" / "sudoers"
PUBLISH = "/opt/df/libexec/df-publish"


@pytest.mark.parametrize("os_dir", ["linux", "macos"])
@pytest.mark.parametrize(
    ("role", "words", "covered"),
    [
        ("df-exec", "perimeter-status", True),
        ("df-exec", "merge", True),
        ("df-exec", "publish-review", False),
        ("df-exec", "self-check", False),
        ("df-exec", "perimeter-status extra", False),
        ("df-review", "merge", False),
        ("df-review", "read-token", True),
    ],
)
def test_grants_cover_on_captured_listings(
    os_dir: str, role: str, words: str, covered: bool
) -> None:
    """The real listings of the roles (`fixtures/sudoers/`, Task 3): exact
    grants only, judged without a refusal."""
    text = (SUDOERS_FIXTURES / f"sudo-l-{os_dir}-{role}.txt").read_text()
    listing = sudoers.parse_listing(role, text)
    grants = listing.grants | listing.carried
    asked = f"{PUBLISH} {words}"
    assert grants_cover(grants, PUBLISHER, PUBLISH, asked) is covered


def _grant(runas: str, command: str) -> sudoers.Grant:
    return sudoers.Grant(runas, frozenset({"NOPASSWD"}), command)


@pytest.mark.parametrize(
    ("runas", "command"),
    [
        ("root, df-publisher", f"{PUBLISH} merge"),
        ("%admin", f"{PUBLISH} merge"),
        ("#0", f"{PUBLISH} merge"),
        ("df-publisher : wheel", f"{PUBLISH} merge"),
        ("df-publisher", f"{PUBLISH} *"),
        ("df-publisher", f"{PUBLISH} merge?"),
        ("df-publisher", f"{PUBLISH} [mp]erge"),
        ("df-publisher", "/opt/df/libexec/"),
        ("df-publisher", f"sha256:abcd {PUBLISH}"),
        ("df-publisher", f"!{PUBLISH} merge"),
    ],
)
def test_grants_cover_fails_closed(runas: str, command: str) -> None:
    """A grant of any form not judged exactly is `UnjudgedGrant`, never
    «no rule» -- even when the asked command is something else."""
    exact = _grant(PUBLISHER, f"{PUBLISH} perimeter-status")
    with pytest.raises(UnjudgedGrant):
        grants_cover([exact, _grant(runas, command)], PUBLISHER, PUBLISH, "x")


@pytest.mark.parametrize(
    ("grant", "covered"),
    [
        (("ALL", f"{PUBLISH} merge"), True),
        (("df-publisher", "ALL"), True),
        (("df-publisher", PUBLISH), True),
        (("root", f"{PUBLISH} merge"), False),
        (("df-publisher", f"{PUBLISH} merge extra"), False),
    ],
)
def test_grants_cover_exact_forms(grant: tuple[str, str], covered: bool) -> None:
    """`ALL` RunAs or command, and the bare wrapper (any arguments), cover;
    another RunAs or other arguments do not."""
    found = grants_cover([_grant(*grant)], PUBLISHER, PUBLISH, f"{PUBLISH} merge")
    assert found is covered


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


def test_install_production_does_not_prime_the_site_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PR 681: `install_production` (shared with the live stand, which
    installs into the real `/`) runs the real `install_admin`; only the
    roles world's own caller primes the stand-in `site/` sources."""
    from df import admin
    from tests.df import roles_world

    real = admin.install_admin
    seen: list[bool] = []

    class World:
        layout = Layout(tmp_path)

        def profile(self) -> object:
            return None

        def init_argv(self) -> list[str]:
            return []

        def bootstrap(self, _argv: list[str]) -> int:
            seen.append(admin.install_admin is real)
            return 0

    monkeypatch.setattr(roles_world, "assert_committed", lambda _layout: None)
    monkeypatch.setattr(roles_world, "current_id", lambda _layout: "rid")
    roles_world.install_production(World())  # type: ignore[arg-type]
    assert seen == [True]


# ---------- stage 3a: the impersonated `api.github.com` (Task 14) ----------


@needs_openssl
def test_api_certs_and_server_pass_strict_verification(tmp_path: Path) -> None:
    """The leaf of `make_api_certs` verifies for `api.github.com` against its
    CA under the default (strict on 3.13) context, and `serve_api` answers
    the trust sources' REST and the control over it; another name fails."""
    import json
    import socket
    import ssl

    from tests.df.roles_world import API_CONTROL, API_HOST, make_api_certs, serve_api
    from tests.df.trust_sources import build_sources

    sources = build_sources(tmp_path / "sources")
    certs = make_api_certs(tmp_path / "certs", Path(str(OPENSSL)))
    context = ssl.create_default_context(cafile=str(certs.ca))
    assert not (certs.ca.parent / "ca.key").exists()  # only the leaf needed it

    def get(port: int, path: str, name: str = API_HOST) -> tuple[int, object]:
        with socket.create_connection(("127.0.0.1", port), timeout=10) as raw:
            with context.wrap_socket(raw, server_hostname=name) as tls:
                request = f"GET /{path} HTTP/1.1\r\nHost: {name}\r\nConnection: close"
                tls.sendall((request + "\r\n\r\n").encode())
                data = b""
                while chunk := tls.recv(65536):
                    data += chunk
        head, _, body = data.partition(b"\r\n\r\n")
        return int(head.split()[1]), json.loads(body)

    with serve_api(sources.rest, certs, port=0, v6=False) as ports:
        assert get(ports[0], API_CONTROL) == (200, {"control": API_CONTROL})
        status, body = get(ports[0], f"repositories/{sources.trust.id}")
        assert status == 200 and isinstance(body, dict)
        assert body["full_name"] == sources.trust.full_name
        with pytest.raises(ssl.SSLCertVerificationError):
            get(ports[0], API_CONTROL, name="github.com")


def test_api_marker_never_follows_or_overwrites(tmp_path: Path) -> None:
    """Review N1: the marker is made `0600` with `O_EXCL | O_NOFOLLOW`; a
    planted link (dangling too) or a leftover is never written through, is
    seen by the leftover check, and is not removed as ours."""
    import os
    import stat

    from _pytest.outcomes import Failed

    from tests.df.roles_world import marker_left, remove_marker, write_marker

    marker = tmp_path / "marker"
    assert not marker_left(marker)
    write_marker(marker, "adapted\n")
    assert stat.S_IMODE(marker.lstat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        write_marker(marker, "again\n")
    remove_marker(marker)
    assert not marker_left(marker)

    target = tmp_path / "target"
    link = tmp_path / "link"
    link.symlink_to(target)  # dangling
    assert marker_left(link)
    with pytest.raises(FileExistsError):
        write_marker(link, "through the link\n")
    assert not target.exists()
    with pytest.raises(Failed):
        remove_marker(link)
    assert os.path.lexists(link)


def test_restores_confirm_only_a_checked_put_back() -> None:
    """Paid review #709: the marker goes only when every change is
    confirmed restored; a mismatch or a raising restore keeps it."""
    from tests.df.roles_world import Restores

    def boom() -> bool:
        raise OSError("read-only")

    ok = Restores(changed={"store", "hosts"})
    ok.settle("store", lambda: True)
    assert not ok.done()  # hosts not confirmed yet
    ok.settle("hosts", lambda: True)
    assert ok.done()
    bad = Restores(changed={"store", "hosts"})
    bad.settle("store", boom)
    bad.settle("hosts", lambda: False)
    assert not bad.done() and len(bad.problems) == 2
    assert "OSError" in bad.problems[0]
