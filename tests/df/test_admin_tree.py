"""Tests for the admin tree's precheck closure and `site/` (2b Task 5).

Network only to fakes: a local HTTP server for the sdist and a local bare
steward repository reached through a `git` wrapper (`tests/df/site_world`).
"""

from __future__ import annotations

import ast
import hashlib
import http.server
import importlib
import io
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from df import admin, admin_tree
from df.admin_tree import (
    CONTRACT_FILES,
    FORBIDDEN_SUFFIXES,
    GOVERNANCE_MODULES,
    SITE_SRC,
    STEWARD_REPO,
    SitePins,
    install_site,
    locked_pins,
)
from df.digest import TreeError
from df.journal import StepFailed
from df.paths import Layout
from tests.df import test_launcher_entry as entry_tests
from tests.df.conftest import FakeLayout
from tests.df.route_world import ID, devtools_ref, setup_layout, write_accounts
from tests.df.site_world import (
    ROOT,
    SDIST_TOP,
    STAND_IN,
    SYSTEM_GIT,
    YAML_URL,
    SiteWorld,
    git,
    git_wrapper,
    prime_cache,
    real_site_world,
    sdist_members,
    site_world,
    stand_in_dir,
    uv_lock,
    walk_files,
    write_checkout,
)
from tests.test_act_precheck import FORBIDDEN, _closure


@pytest.fixture()
def fl(monkeypatch: pytest.MonkeyPatch, fake_layout: FakeLayout) -> FakeLayout:
    layout = setup_layout(monkeypatch, fake_layout)
    write_accounts(layout)
    monkeypatch.setattr(admin.plat, "trusted_path_problems", lambda _p: [])
    return layout


OPENSSL = shutil.which("openssl", path="/usr/bin:/bin")


class Served:
    """A local HTTPS server (a self-signed certificate for 127.0.0.1, trusted
    by `context` only): `/pyyaml.tar.gz` answers `body` (settable);
    `/to-http`, `/to-ftp`, `/to-https` redirect there; `/slow` trickles."""

    def __init__(self, body: bytes, certs: Path) -> None:
        self.body = body
        self.paths: list[str] = []
        cert, key = certs / "c.pem", certs / "k.pem"
        if OPENSSL is None:
            pytest.skip("openssl не найден")
        assert OPENSSL is not None
        subprocess.run(
            [OPENSSL, "req", "-x509", "-newkey", "rsa:2048", "-nodes"]
            + ["-keyout", str(key), "-out", str(cert), "-days", "1"]
            + ["-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"],
            check=True,
            capture_output=True,
            timeout=60,
        )
        served = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib name
                served.paths.append(self.path)
                port = served.server.server_address[1]
                target = {
                    "/to-http": f"http://127.0.0.1:{port}/pyyaml.tar.gz",
                    "/to-ftp": f"ftp://127.0.0.1:{port}/pyyaml.tar.gz",
                    "/to-https": f"https://127.0.0.1:{port}/pyyaml.tar.gz",
                }.get(self.path)
                if target is not None:
                    self.send_response(302)
                    self.send_header("Location", target)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(served.body)))
                self.end_headers()
                if self.path != "/slow":
                    self.wfile.write(served.body)
                    return
                for byte in served.body[:40]:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(0.05)

            def log_message(self, *_args: object) -> None:
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        self.server.socket = tls.wrap_socket(self.server.socket, server_side=True)
        self.base = f"https://127.0.0.1:{self.server.server_address[1]}"
        self.url = f"{self.base}/pyyaml.tar.gz"
        self.context = ssl.create_default_context(cafile=str(cert))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def fetch(self, url: str, **kw: float) -> bytes:
        """`fetch_url` trusting this server's certificate."""
        return admin_tree.fetch_url(url, context=self.context, **kw)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def served(tmp_path: Path) -> Iterator[Served]:
    certs = tmp_path / "certs"
    certs.mkdir()
    server = Served(site_world().sdist, certs)
    yield server
    server.close()


def local_pins(world: SiteWorld, served: Served) -> SitePins:
    return world.pins(yaml_url=served.url)


def wrapper(tmp_path: Path, world: SiteWorld, extra: str = "") -> Path:
    return git_wrapper(tmp_path, world.repo, extra=extra)


def cache(layout: Layout) -> Path:
    return layout.admin_home / SITE_SRC


# ---------- the closure and its contracts ----------


def test_governance_modules_equal_ast_closure() -> None:
    """The constant is the AST closure of `act_precheck`, deferred imports
    included; `ops`, `runner`, `df` stay out; external names are only the
    stdlib, `steward` and `yaml`."""
    local, external = _closure("governance.act_precheck")
    assert sorted(local) == sorted(GOVERNANCE_MODULES)
    assert [
        m for m in (*FORBIDDEN, "governance.ops", "governance.runner") if m in local
    ] == []
    assert not any(m == "df" or m.startswith("df.") for m in local)
    assert sorted(external - set(sys.stdlib_module_names)) == ["steward", "yaml"]
    assert all((ROOT / rel).is_file() for rel in admin_tree.governance_files())


def _uses_file(module: str) -> bool:
    path = ROOT / admin_tree.governance_files()[GOVERNANCE_MODULES.index(module)]
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(isinstance(n, ast.Name) and n.id == "__file__" for n in ast.walk(tree))


def test_contract_files_cover_file_relative_reads() -> None:
    """Every module of the closure that names `__file__` resolves it to
    paths outside `governance/`; those paths are exactly `CONTRACT_FILES`,
    each a regular file of this tree."""
    users = [m for m in GOVERNANCE_MODULES if _uses_file(m)]
    assert users  # positive control: approval_branches reads patterns.env
    found: set[str] = set()
    for name in users:
        module = importlib.import_module(name)
        paths = [v for v in vars(module).values() if isinstance(v, Path)]
        rels = {
            p.resolve().relative_to(ROOT).as_posix()
            for p in paths
            if p.resolve().is_relative_to(ROOT)
            and not p.resolve().is_relative_to(ROOT / "governance")
        }
        assert rels, f"{name}: names __file__ but no path outside governance/"
        found |= rels
    assert sorted(found) == sorted(CONTRACT_FILES)
    assert all((ROOT / rel).is_file() for rel in CONTRACT_FILES)


def test_locked_pins_from_uv_lock() -> None:
    pins = locked_pins((ROOT / "uv.lock").read_bytes())
    assert pins == SitePins(
        "https://files.pythonhosted.org/packages/05/8e/961c0007c59b8dd7729d542c61a4d537"
        "767a59645b82a0b521206e1e25c2/pyyaml-6.0.3.tar.gz",
        "d76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f",
        "https://github.com/andrei-shtanakov/steward",
        "68f46a9b2e5cfe9ecd1f09524e986c3f687f4155",
    )


@pytest.mark.parametrize(
    "spoil",
    ["http-sdist", "no-sdist", "bad-hash", "subdirectory", "no-steward", "two-yaml"],
)
def test_locked_pins_refusals(spoil: str) -> None:
    text = uv_lock(site_world().pins()).decode()
    if spoil == "http-sdist":
        text = text.replace('url = "https://', 'url = "http://')
    elif spoil == "no-sdist":
        text = "\n".join(ln for ln in text.splitlines() if not ln.startswith("sdist"))
    elif spoil == "bad-hash":
        text = text.replace('hash = "sha256:', 'hash = "md5:')
    elif spoil == "subdirectory":
        text = text.replace("?rev=", "?subdirectory=src&rev=")
    elif spoil == "no-steward":
        text = text[: text.index('[[package]]\nname = "steward"')]
    else:
        text += text[
            text.index("[[package]]") : text.index('[[package]]\nname = "steward"')
        ]
    with pytest.raises(TreeError):
        locked_pins(text.encode())


# ---------- the sources ----------


def test_sdist_sha_mismatch_refused(
    fl: FakeLayout, served: Served, tmp_path: Path
) -> None:
    """A download whose sha256 is not the pin is refused and never cached;
    a cached sdist that does not match is removed and downloaded again."""
    world = site_world()
    pins = local_pins(world, served)
    served.body = world.sdist + b"x"
    dest = tmp_path / "site"
    with pytest.raises(TreeError, match="sha256"):
        install_site(
            dest,
            pins,
            layout=fl.layout,
            fetch=served.fetch,
            git=wrapper(tmp_path, world),
        )
    assert not os.path.lexists(dest)
    assert sorted(os.listdir(cache(fl.layout))) == []
    stale = cache(fl.layout) / f"pyyaml-{world.sha256}.tar.gz"
    stale.write_bytes(b"stale")
    served.body = world.sdist
    install_site(
        dest,
        pins,
        layout=fl.layout,
        fetch=served.fetch,
        git=wrapper(tmp_path, world),
    )
    assert stale.read_bytes() == world.sdist
    assert len(served.paths) == 2
    assert sorted(os.listdir(cache(fl.layout))) == [stale.name, STEWARD_REPO]


def test_fetch_url_https_only(served: Served) -> None:
    """Only `https`: an `http` URL, and a redirect to `http` or `ftp`, are
    refused; a redirect to `https` is followed."""
    assert served.fetch(f"{served.base}/to-https") == served.body
    http_url = served.url.replace("https://", "http://")
    with pytest.raises(TreeError, match="не https"):
        served.fetch(http_url)
    for path in ("/to-http", "/to-ftp"):
        with pytest.raises(TreeError, match="перенаправление не на https"):
            served.fetch(f"{served.base}{path}")


def test_fetch_url_whole_download_deadline(served: Served) -> None:
    """A server trickling bytes (each read inside the socket timeout) is
    cut by the deadline of the whole download."""
    with pytest.raises(TreeError, match="срок"):
        served.fetch(f"{served.base}/slow", deadline=0.3)


def test_fetch_url_refuses_an_untrusted_certificate(served: Served) -> None:
    with pytest.raises(TreeError, match="CERTIFICATE_VERIFY_FAILED|certificate"):
        admin_tree.fetch_url(served.url)


def _sdist_with(extra: tuple[str, bytes | None, int, str], *, first: bool) -> bytes:
    """The stand-in sdist with one hostile member `(name, data, mode, kind)`
    placed before or after the legitimate ones."""
    hostile = _tar_info(extra)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        if first:
            tar.addfile(*hostile)
        for name, data, mode in sdist_members(stand_in_dir() / "yaml"):
            kind = "dir" if data is None else "reg"
            tar.addfile(*_tar_info((name, data, mode, kind)))
        if not first:
            tar.addfile(*hostile)
    return buf.getvalue()


def _tar_info(
    member: tuple[str, bytes | None, int, str],
) -> tuple[tarfile.TarInfo, io.BytesIO | None]:
    name, data, mode, kind = member
    info = tarfile.TarInfo(name)
    info.mode = mode
    if kind == "sym":
        info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
    elif kind == "hard":
        info.type, info.linkname = tarfile.LNKTYPE, f"{SDIST_TOP}/setup.py"
    elif kind == "dir":
        info.type = tarfile.DIRTYPE
    else:
        assert data is not None
        info.size = len(data)
    return info, None if data is None else io.BytesIO(data)


NOT_REGULAR = "не файл и не каталог"
DOTDOT = "'..' в архиве"
FORBIDDEN_NAME = "запрещённое имя"
#: Hostile sdist members and the refusal each must get (`match`).
HOSTILE = {
    "symlink": ((f"{SDIST_TOP}/lib/yaml/evil.py", None, 0o644, "sym"), NOT_REGULAR),
    "hardlink": ((f"{SDIST_TOP}/lib/yaml/hard.py", None, 0o644, "hard"), NOT_REGULAR),
    "dotdot": ((f"{SDIST_TOP}/lib/yaml/../../../evil.py", b"x", 0o644, "reg"), DOTDOT),
    "dotdot-outside": ((f"{SDIST_TOP}/../evil.py", b"x", 0o644, "reg"), DOTDOT),
    "absolute": (("/tmp/evil.py", b"x", 0o644, "reg"), "абсолютное имя"),
    "native": (
        (f"{SDIST_TOP}/lib/yaml/_yaml.cpython-312-darwin.so", b"x", 0o755, "reg"),
        FORBIDDEN_NAME,
    ),
    "pth": (
        (f"{SDIST_TOP}/lib/yaml/x.pth", b"import os", 0o644, "reg"),
        FORBIDDEN_NAME,
    ),
    "bytecode": (
        (f"{SDIST_TOP}/lib/yaml/__pycache__/x.py", b"x", 0o644, "reg"),
        FORBIDDEN_NAME,
    ),
}


@pytest.mark.parametrize("first", [True, False], ids=["first", "last"])
@pytest.mark.parametrize("hostile", sorted(HOSTILE))
def test_sdist_link_or_dotdot_refused(
    fl: FakeLayout, tmp_path: Path, hostile: str, first: bool
) -> None:
    """No link, absolute name or `..` (anywhere), no native code, `.pth`
    or bytecode in `lib/yaml`: refused by its own rule, whether the member
    comes before or after the legitimate ones."""
    world = site_world()
    member, why = HOSTILE[hostile]
    sdist = _sdist_with(member, first=first)
    sha = hashlib.sha256(sdist).hexdigest()
    pins = SitePins(YAML_URL, sha, world.pins().steward_url, world.commit)
    with pytest.raises(TreeError, match=re.escape(why)):
        install_site(
            tmp_path / "site",
            pins,
            layout=fl.layout,
            fetch=lambda _url: sdist,
            git=wrapper(tmp_path, world),
        )
    assert not os.path.lexists(tmp_path / "evil.py")


@pytest.mark.parametrize(
    "hostile,why", [("symlink", NOT_REGULAR), ("native", FORBIDDEN_NAME)]
)
def test_steward_commit_hostile_member_refused(
    fl: FakeLayout, tmp_path: Path, hostile: str, why: str
) -> None:
    """A steward commit with a symbolic link or a native module under
    `src/steward`, through the stream path (`git archive` into `r|`):
    refused by its rule; the archive process is gone."""
    work = tmp_path / "steward-work"
    pkg = work / "src" / "steward"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    if hostile == "symlink":
        (pkg / "evil.py").symlink_to("/etc/passwd")
    else:
        (pkg / "_x.cpython-312-darwin.so").write_bytes(b"\x7fELF")
    git("init", "-q", "--template=", str(work))
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", "hostile", cwd=work)
    commit = git("rev-parse", "HEAD", cwd=work).strip()
    world = site_world()
    cache(fl.layout).mkdir(mode=0o700)
    (cache(fl.layout) / f"pyyaml-{world.sha256}.tar.gz").write_bytes(world.sdist)
    git(
        "clone",
        "-q",
        "--bare",
        "--template=",
        str(work),
        str(cache(fl.layout) / STEWARD_REPO),
    )
    pins = SitePins(YAML_URL, world.sha256, world.pins().steward_url, commit)
    with pytest.raises(TreeError, match=re.escape(why)):
        install_site(
            tmp_path / "site", pins, layout=fl.layout, fetch=None, git=SYSTEM_GIT
        )
    assert not (tmp_path / "site" / "steward" / "evil.py").exists()


def test_steward_archive_stalled_mid_stream_is_bounded(
    fl: FakeLayout,
    served: Served,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`git archive` that sends part of the archive and then stalls without
    exiting: the stream read is bounded by `GIT_TIMEOUT` like every other
    git call -- `TreeError` naming the deadline, the process group killed;
    the partial `site/` is left to the caller's recovery."""
    monkeypatch.setattr(admin_tree, "GIT_TIMEOUT", 5.0)
    world = site_world()
    pid_file = tmp_path / "archive.pid"
    stall = (
        'case " $* " in *" archive "*)\n'
        f'  {SYSTEM_GIT} "$@" | head -c 1536\n'
        f"  echo $$ > {pid_file}\n"
        "  exec sleep 120;;\n"
        "esac\n"
    )
    dest = tmp_path / "site"
    started = time.monotonic()
    with pytest.raises(TreeError, match="срок 5 с истёк"):
        install_site(
            dest,
            local_pins(world, served),
            layout=fl.layout,
            fetch=served.fetch,
            git=wrapper(tmp_path, world, extra=stall),
        )
    assert time.monotonic() - started < 60
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
    assert (dest / "steward").is_dir()  # partial, as the protocol expects


def test_steward_cache_never_read_through_a_parent_repo(
    fl: FakeLayout, tmp_path: Path
) -> None:
    """An empty `steward.git` inside another repository: with `--git-dir`
    git never discovers the parent -- the parent's commit is not "cached";
    online the directory is initialised and fetched properly."""
    lay = fl.layout
    world = site_world()
    git("init", "-q", "--template=", str(lay.root))
    (lay.root / "marker").write_text("parent\n")
    git("add", "marker", cwd=lay.root)
    git("commit", "-q", "-m", "parent", cwd=lay.root)
    parent = git("rev-parse", "HEAD", cwd=lay.root).strip()
    cache(lay).mkdir(mode=0o700)
    (cache(lay) / f"pyyaml-{world.sha256}.tar.gz").write_bytes(world.sdist)
    (cache(lay) / STEWARD_REPO).mkdir()
    pins = SitePins(YAML_URL, world.sha256, world.pins().steward_url, parent)
    with pytest.raises(TreeError, match="без сети"):
        admin_tree.site_sources(pins, layout=lay, fetch=None, git=SYSTEM_GIT)
    got = admin_tree.site_sources(
        world.pins(),
        layout=lay,
        fetch=lambda _url: pytest.fail("cached sdist matches"),
        git=wrapper(tmp_path, world),
    )
    assert got.steward_commit == world.commit
    assert (cache(lay) / STEWARD_REPO / "HEAD").is_file()


@pytest.mark.parametrize("body", [b"not a tar", b"\x1f\x8b\x08\x00truncated"])
def test_malformed_sdist_refused(fl: FakeLayout, tmp_path: Path, body: bytes) -> None:
    """A pinned artifact that is no gzip'ed tar is `TreeError`, not a crash."""
    world = site_world()
    sha = hashlib.sha256(body).hexdigest()
    pins = SitePins(YAML_URL, sha, world.pins().steward_url, world.commit)
    with pytest.raises(TreeError, match="архив отказан"):
        install_site(
            tmp_path / "site",
            pins,
            layout=fl.layout,
            fetch=lambda _url: body,
            git=wrapper(tmp_path, world),
        )


def test_steward_fetch_head_mismatch_refused(fl: FakeLayout, tmp_path: Path) -> None:
    """`FETCH_HEAD` other than the pin is refused before any `site/` file."""
    world = site_world()
    other = "0" * 40
    extra = (
        'if [ "$2" = rev-parse ] && [ "$3" = FETCH_HEAD ]; then\n'
        f"  echo {other}; exit 0\n"
        "fi\n"
    )
    with pytest.raises(TreeError, match="FETCH_HEAD"):
        install_site(
            tmp_path / "site",
            world.pins(),
            layout=fl.layout,
            fetch=lambda _url: world.sdist,
            git=wrapper(tmp_path, world, extra),
        )
    assert not os.path.lexists(tmp_path / "site")


def test_steward_cache_without_the_commit_is_fetched(
    fl: FakeLayout, tmp_path: Path
) -> None:
    """A cached `steward.git` lacking the pinned commit is fetched again;
    offline (`fetch=None`) it is a refusal, never a fetch."""
    world = site_world()
    repo = cache(fl.layout) / STEWARD_REPO
    cache(fl.layout).mkdir(mode=0o700)
    git("init", "-q", "--bare", "--template=", str(repo))
    (cache(fl.layout) / f"pyyaml-{world.sha256}.tar.gz").write_bytes(world.sdist)
    with pytest.raises(TreeError, match="без сети"):
        admin_tree.site_sources(
            world.pins(), layout=fl.layout, fetch=None, git=wrapper(tmp_path, world)
        )
    got = admin_tree.site_sources(
        world.pins(),
        layout=fl.layout,
        fetch=lambda _url: pytest.fail("cached sdist matches"),
        git=wrapper(tmp_path, world),
    )
    assert got.steward_commit == world.commit
    assert git("-C", str(repo), "cat-file", "-t", world.commit).strip() == "commit"


# ---------- the installed tree ----------


def expected_site_files(world_files: dict[str, dict[str, str]]) -> list[str]:
    out: list[str] = []
    for package, files in world_files.items():
        out.append(f"site/{package}/")
        for rel in files:
            parts = rel.split("/")
            out += [
                f"site/{package}/{'/'.join(parts[:i])}/" for i in range(1, len(parts))
            ]
            out.append(f"site/{package}/{rel}")
    return out


def test_install_admin_exact_tree(
    fl: FakeLayout, served: Served, tmp_path: Path
) -> None:
    """From an empty cache: exactly `df/`, the closure, the contracts and the
    two `site/` packages (nothing outside `lib/yaml`/`src/steward`); modes
    `0755`/`0644`; the cache then holds only the two source artifacts."""
    lay = fl.layout
    world = site_world()
    ref = devtools_ref(fl)
    for leftover in os.listdir(cache(lay)):
        _remove(cache(lay) / leftover)

    def fetch(url: str) -> bytes:  # the lock's https URL, served locally
        assert url == YAML_URL
        return served.fetch(served.url)

    admin.install_admin(
        lay, ID, devtools_ref=ref, git=wrapper(tmp_path, world), fetch=fetch
    )
    tree = lay.admin_dir(ID)
    want = [
        "df/",
        "df/__init__.py",
        "df/launcher_entry.py",
        "governance/",
        *admin_tree.governance_files(),
        "contracts/",
        "contracts/approval-branches/",
        "contracts/approval-branches/v1/",
        *CONTRACT_FILES,
        "site/",
        *expected_site_files(STAND_IN),
    ]
    assert walk_files(tree) == sorted(set(want))
    for rel in walk_files(tree):
        mode = os.stat(tree / rel).st_mode & 0o7777
        assert mode == (0o755 if rel.endswith("/") else 0o644), rel
    assert sorted(os.listdir(cache(lay))) == [
        f"pyyaml-{world.sha256}.tar.gz",
        STEWARD_REPO,
    ]
    assert len(served.paths) == 1
    assert admin.admin_code_problems(lay, ID) == []


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def installed(fl: FakeLayout) -> Path:
    admin.install_admin(fl.layout, ID, devtools_ref=devtools_ref(fl), git=SYSTEM_GIT)
    return fl.layout.admin_dir(ID)


@pytest.mark.parametrize("suffix", [".so", ".dylib", ".pyd", ".SO"])
@pytest.mark.parametrize("where", ["site/yaml", "site/steward/approvalfacts", "df"])
def test_native_extension_refused(fl: FakeLayout, suffix: str, where: str) -> None:
    tree = installed(fl)
    assert admin.admin_code_problems(fl.layout, ID) == []
    (tree / where / f"_x.cpython-312{suffix}").write_bytes(b"\x7fELF")
    problems = admin.admin_code_problems(fl.layout, ID)
    assert len(problems) == 1 and "нативный модуль" in problems[0]


@pytest.mark.parametrize("where", ["site", "site/yaml", "governance"])
def test_pth_refused(fl: FakeLayout, where: str) -> None:
    tree = installed(fl)
    (tree / where / "evil.pth").write_text("import os\n")
    problems = admin.admin_code_problems(fl.layout, ID)
    assert len(problems) == 1 and ".pth" in problems[0]


def test_forbidden_suffixes_exact() -> None:
    assert FORBIDDEN_SUFFIXES == (".so", ".dylib", ".pyd", ".pth", ".pyc")


def test_digest_covers_site(fl: FakeLayout) -> None:
    """`admin_digest` covers `site/` (a changed file changes it) and
    `expected_admin_digest` builds the same tree offline from the cache;
    a tampered cache is refused, not downloaded."""
    lay = fl.layout
    tree = installed(fl)
    ref = lay.verify_dir / "devtools"
    digest = admin.admin_digest(lay, ID)
    assert admin.expected_admin_digest(ref, layout=lay, git=SYSTEM_GIT) == digest
    init = tree / "site" / "steward" / "__init__.py"
    os.chmod(init, 0o644)
    init.write_text("__version__ = 'other'\n")
    assert admin.admin_digest(lay, ID) != digest
    sdist = cache(lay) / f"pyyaml-{site_world().sha256}.tar.gz"
    sdist.write_bytes(b"tampered")
    with pytest.raises(TreeError, match="без сети"):
        admin.expected_admin_digest(ref, layout=lay, git=SYSTEM_GIT)
    assert not sdist.exists()  # a bad artifact is dropped, never trusted


def test_install_refusal_before_the_tree(fl: FakeLayout, tmp_path: Path) -> None:
    """A source refused (download fails) leaves no `admin/<id>`."""
    lay = fl.layout
    ref = devtools_ref(fl)
    for leftover in os.listdir(cache(lay)):
        _remove(cache(lay) / leftover)

    def offline(url: str) -> bytes:
        raise TreeError(f"{url}: нет сети")

    with pytest.raises(StepFailed) as exc:
        admin.install_admin(lay, ID, devtools_ref=ref, git=SYSTEM_GIT, fetch=offline)
    assert exc.value.step == "admin"
    assert not os.path.lexists(lay.admin_dir(ID))


# ---------- who sees site/ ----------

STUB_CLI = '''\
"""Stub of df.cli: reports sys.path and what site/ would provide."""
import importlib.util
import json
import sys


def main(argv, *, mode, read, lock_fd, layout):
    print("MAIN " + json.dumps({
        "path": sys.path,
        "yaml": importlib.util.find_spec("yaml") is not None,
        "steward": importlib.util.find_spec("steward") is not None,
        "argv": list(argv),
    }))
    return 0
'''


@pytest.mark.parametrize("argv", [["doctor"], ["publisher", "serve", "merge"]])
def test_entry_does_not_import_site(tmp_path: Path, argv: list[str]) -> None:
    """The command's entry (a reader and `publisher serve`) puts only
    `admin/<id>` in front of the stdlib: `site/` -- present in the tree --
    is not on `sys.path`, and neither of its packages is importable."""
    lay = entry_tests._layout(tmp_path)  # noqa: SLF001 - shared harness
    lay.install_dir.mkdir(parents=True)
    lay.generation.write_bytes(b"2\n")
    pkg = lay.admin_dir(entry_tests.ID) / "df"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "state.py").write_text(entry_tests.STUB_STATE)
    (pkg / "paths.py").write_text(entry_tests.STUB_PATHS)
    (pkg / "cli.py").write_text(STUB_CLI)
    for package, files in STAND_IN.items():
        for rel, text in files.items():
            target = lay.admin_dir(entry_tests.ID) / "site" / package / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
    digest = admin.admin_digest(lay, entry_tests.ID)
    entry_tests.write_registry(lay, [entry_tests.record(entry_tests.ID, digest)])
    (lay.etc / "install.toml").write_bytes(entry_tests.INSTALL_TEXT)
    (lay.etc / "models.toml").write_bytes(entry_tests.MODELS_TEXT)
    entry_tests.set_current(lay, entry_tests.ID)
    lay.bin_dir.mkdir(parents=True)
    entry_tests.make_interpreter(lay)
    lay.entry.write_text(entry_tests.entry_text_as_user(lay))
    done = entry_tests.run_entry(lay, *argv)
    lines = [ln for ln in done.stdout.splitlines() if ln.startswith("MAIN ")]
    assert len(lines) == 1, done.stdout + done.stderr
    got = json.loads(lines[0].removeprefix("MAIN "))
    admin_dir = str(lay.admin_dir(entry_tests.ID))
    assert got["argv"] == argv
    assert got["path"][0] == admin_dir
    assert not any(p.startswith(admin_dir) for p in got["path"][1:])
    assert (got["yaml"], got["steward"]) == (False, False)


def test_only_precheck_serve_adds_site_to_sys_path() -> None:
    """Every `sys.path` change in `df/` is a `sys.path.insert(0, …)` of a
    code root -- the entry's `admin/<id>`, bootstrap's checkout, the release
    launcher's tree -- none naming `site`; the one exception is Task 6's
    `precheck serve`, which appends `site/` at the end."""
    changes: list[tuple[str, str]] = []
    for path in sorted((ROOT / "df").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and ast.unparse(node.func.value) == "sys.path"
            ):
                continue
            rel = path.relative_to(ROOT).as_posix()
            changes.append((rel, ast.unparse(node)))
            if node.func.attr == "append":
                assert rel == "df/cli.py", (rel, ast.unparse(node))
                assert ast.unparse(node) == "sys.path.append(str(tree / SITE_DIR))"
                continue
            assert node.func.attr == "insert", (rel, ast.unparse(node))
            assert ast.unparse(node.args[0]) == "0"
            assert "site" not in ast.unparse(node.args[1])
    assert sorted(rel for rel, _ in changes) == [
        "df/bootstrap.py",
        "df/cli.py",
        "df/launch_release.py",
        "df/launcher_entry.py",
    ]


def test_precheck_imports_under_dash_S(fl: FakeLayout, tmp_path: Path) -> None:
    """The admin tree from the real pure-Python `yaml` and `steward` runs
    the precheck program: `-I -B -S`, `sys.path` = stdlib + `admin/<id>` +
    `admin/<id>/site` (at the end), `governance.act_precheck` and the
    steward module it loads lazily import, `yaml` without libyaml."""
    pytest.importorskip("steward", reason="steward — без группы governance")
    world = real_site_world()
    lay = fl.layout
    ref = devtools_ref(fl)
    write_checkout(ref, world, real=True)
    admin.install_admin(
        lay,
        ID,
        devtools_ref=ref,
        git=wrapper(tmp_path, world),
        fetch=lambda _u: world.sdist,
    )
    assert admin.admin_code_problems(lay, ID) == []
    tree = lay.admin_dir(ID)
    code = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(tree)!r})\n"
        f"sys.path.append({str(tree / 'site')!r})\n"
        "import governance.act_precheck, steward.approvalfacts.v3, yaml\n"
        "files = [getattr(m, '__file__', None) or '' for m in sys.modules.values()]\n"
        "print(json.dumps({'libyaml': yaml.__with_libyaml__,\n"
        "    'site_packages': sorted(f for f in files if 'site-packages' in f),\n"
        "    'from': [m.__file__ for m in (governance.act_precheck, steward, yaml)],\n"
        "    'flags': [sys.flags.isolated, sys.flags.no_site,\n"
        "              sys.flags.dont_write_bytecode]}))\n"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-B", "-S", "-c", code],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    got = json.loads(done.stdout)
    assert got == {
        "libyaml": False,
        "site_packages": [],
        "from": [
            str(tree / "governance" / "act_precheck.py"),
            str(tree / "site" / "steward" / "__init__.py"),
            str(tree / "site" / "yaml" / "__init__.py"),
        ],
        "flags": [1, 1, 1],
    }
    assert not list(tree.rglob("__pycache__"))


# ---------- the root worlds' fixture (non-root half) ----------


def test_root_fixture_repo_installs_offline(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout, tmp_path: Path
) -> None:
    """The root worlds' `devtools` fixture (`build_fixture_repo`, as the
    root run builds it) fetched by root's own `fetch_reference`, installed
    by `install_admin` under `prime_on_install` with every download refused:
    a clean admin tree with this checkout's closure and contracts; the
    appended `site/` pins are unreached by the build's lock walk and the
    lock still passes `uv lock --check`."""
    from df import inputs, lockset
    from df.manifest import RepoPin
    from df.markers import marker_env
    from tests.df.root_world import (
        ADMIN_TREE_PATHS,
        PYTHON,
        build_fixture_repo,
        prime_on_install,
    )

    top = tmp_path / "top"
    top.mkdir()
    bare, sha = build_fixture_repo(top)
    lay = fl.layout
    pin = RepoPin("devtools", f"file://{bare}", sha, ())
    ref = inputs.fetch_reference(
        pin, lay.verify_dir / "devtools", git=SYSTEM_GIT, layout=lay
    )
    for rel in ADMIN_TREE_PATHS[1:]:
        assert (ref / rel).read_bytes() == (ROOT / rel).read_bytes(), rel
    lock = (ref / "uv.lock").read_bytes()
    assert locked_pins(lock) == site_world().pins()
    for platform in ("macos-arm64", "linux-x86_64"):
        env = marker_env(platform, PYTHON)
        got = lockset.expected_distributions(lock, groups=(), env=env, root_dir=ref)
        assert sorted(d.name for d in got) == ["dfprobe", "six"]
    uv = shutil.which("uv")
    if uv is not None:
        check = subprocess.run(
            [uv, "lock", "--check", "--offline", "--python", sys.executable],
            cwd=ref,
            env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "UV_NO_CONFIG": "1"},
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert check.returncode == 0, check.stderr
    shutil.rmtree(cache(lay), ignore_errors=True)  # nothing cached yet

    def offline(url: str) -> bytes:
        raise TreeError(f"{url}: сеть запрещена")

    prime_on_install(monkeypatch)
    admin.install_admin(lay, ID, devtools_ref=ref, git=SYSTEM_GIT, fetch=offline)
    tree = lay.admin_dir(ID)
    assert admin.admin_code_problems(lay, ID) == []
    assert (tree / "governance" / "act_precheck.py").is_file()
    assert (tree / CONTRACT_FILES[0]).is_file()
    assert sorted(os.listdir(tree / "site")) == ["steward", "yaml"]


# ---------- legacy releases ----------


#: The closure files devtools 335188f (the umbrella's `core` pin) already has.
OLD_MODULES_335188F = {
    "governance/__init__.py",
    "governance/act_envelope.py",
    "governance/act_request.py",
    "governance/act_time.py",
    "governance/approval_branches.py",
    "governance/ssot_env.py",
}


@pytest.mark.parametrize("shape", ["df-only", "335188f"])
def test_legacy_checkout_installs_df_only(fl: FakeLayout, shape: str) -> None:
    """No `act_precheck.py` (as devtools 335188f, pinned by the umbrella's
    `core`: some closure modules and the patterns, not the program): the
    admin tree is exactly `df/`, no pins read (no `uv.lock`), the cache never
    touched, and `expected_admin_digest` agrees."""
    lay = fl.layout
    ref = devtools_ref(fl)
    (ref / "uv.lock").unlink()
    shutil.rmtree(cache(lay))
    keep = {*OLD_MODULES_335188F, *CONTRACT_FILES}
    for rel in (*admin_tree.governance_files(), *CONTRACT_FILES):
        if shape == "df-only" or rel not in keep:
            (ref / rel).unlink()
    assert not admin_tree.release_has_precheck(ref)

    def offline(url: str) -> bytes:
        raise AssertionError(f"no download for a legacy release: {url}")

    admin.install_admin(lay, ID, devtools_ref=ref, git=SYSTEM_GIT, fetch=offline)
    tree = lay.admin_dir(ID)
    assert walk_files(tree) == ["df/", "df/__init__.py", "df/launcher_entry.py"]
    assert not os.path.lexists(cache(lay))
    assert admin.admin_code_problems(lay, ID) == []
    digest = admin.expected_admin_digest(ref, layout=lay, git=SYSTEM_GIT)
    assert digest == admin.admin_digest(lay, ID)
    assert not os.path.lexists(cache(lay))


@pytest.mark.parametrize(
    "missing", ["governance/act_anon.py", "governance/__init__.py", CONTRACT_FILES[0]]
)
def test_partial_closure_refused(fl: FakeLayout, missing: str) -> None:
    """`act_precheck.py` present but a closure module or a contract file
    missing: refused, naming it -- no admin tree, no fallback to `df/` only."""
    lay = fl.layout
    ref = devtools_ref(fl)
    (ref / missing).unlink()
    with pytest.raises(TreeError, match=f"нет: {re.escape(missing)}"):
        admin_tree.release_has_precheck(ref)
    with pytest.raises(StepFailed, match=re.escape(missing)) as exc:
        admin.install_admin(lay, ID, devtools_ref=ref, git=SYSTEM_GIT)
    assert exc.value.step == "admin"
    assert not os.path.lexists(lay.admin_dir(ID))
    with pytest.raises(TreeError, match=re.escape(missing)):
        admin.expected_admin_digest(ref, layout=lay, git=SYSTEM_GIT)


def test_full_closure_is_a_precheck_release(fl: FakeLayout) -> None:
    assert admin_tree.release_has_precheck(devtools_ref(fl))
    assert admin_tree.PRECHECK_FILE in admin_tree.governance_files()


@pytest.mark.parametrize("case", ["no-df", "unreadable-module"])
def test_expected_admin_digest_raises_only_tree_error(
    fl: FakeLayout, case: str
) -> None:
    """A step's refusal (`df/` missing: `StepFailed`) and an OS error (an
    unreadable module) both reach the caller as `TreeError`, which the
    provenance check turns into its own refusal."""
    lay = fl.layout
    ref = devtools_ref(fl)
    prime_cache(lay)
    if case == "no-df":
        shutil.rmtree(ref / "df")
        why = "не каталог"
    else:
        if os.geteuid() == 0:
            pytest.skip("root reads a 0000 file")
        (ref / "governance" / "act_time.py").chmod(0)
        why = "act_time.py"
    with pytest.raises(TreeError, match=why):
        admin.expected_admin_digest(ref, layout=lay, git=SYSTEM_GIT)
