"""`darkfactory setup-agent` (stage 2a, Task 12): the App key and the
machine user's token, written by root through descriptors, then checked.

Root is the current uid (`fake_layout`); `df-publisher` and `df-exec` are
virtual owners (`fsutil.owner_uid`, a substituted `os.fchown`). The checks
after the write are seams (`publisher_self_check`, `exec_check_github`):
real `sudo` exists only in Task 14's root cells.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from df import cli, fsutil, journal, launch_release, launcher_entry, setup_agent
from df import paths as df_paths
from df import platform as plat
from df.config import Account, dump_accounts
from df.journal import Stable, StepFailed
from df.paths import MACHINE_TOKEN, Layout
from df.publisher import ops, request
from df.stage2_config import Product, dump_products
from df.state import RawSnapshot
from tests.df.conftest import FakeLayout

ME = os.getuid()
PUB_UID = ME + 70003
EXEC_UID = ME + 70001
APP_ID = 4242
KEY = (
    b"-----BEGIN RSA PRIVATE KEY-----\n"
    b"TUlJRXBRSUJBQUtDQVFFQXNlY3JldGtleWZha2VmYWtlZmFrZWZha2VmYWtl\n"
    b"ZmFrZVNFQ1JFVEtFWUJZVEVTZmFrZWZha2U=\n"
    b"-----END RSA PRIVATE KEY-----\n"
)
#: A recognisable fragment of the key body: never in any output.
KEY_MARK = b"TUlJRXBRSUJBQUtDQVFFQXNlY3JldGtleW"
TOKEN = b"github_pat_11FAKEFAKEFAKE0123456789_secretTOKENbody"
TOKEN2 = b"ghp_FAKEsecondTOKEN0123456789abcdef"
RAW = RawSnapshot(0, None, None, None, None, False)


@dataclass
class W:
    fake: FakeLayout
    mp: pytest.MonkeyPatch
    src: Path
    outside: Path
    self_check: list[bytes | Exception]
    github: list[plat.RunResult]

    @property
    def lay(self) -> Layout:
        return self.fake.layout

    @property
    def home(self) -> Path:
        return self.lay.role_home("df-exec")

    @property
    def token_file(self) -> Path:
        return self.home.joinpath(*MACHINE_TOKEN)

    def source(self, data: bytes, name: str = "secret") -> Path:
        path = self.src / name
        path.write_bytes(data)
        return path

    def main(self, argv: list[str]) -> int:
        return cli.main(
            argv, mode="writer", read=Stable(RAW, 0), lock_fd=99, layout=self.lay
        )

    def journal_text(self) -> str:
        path = self.lay.journal
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def rows(self) -> list[tuple[str, str]]:
        rows = journal.read_journal(self.lay.journal)
        return [(str(r["step"]), str(r["event"])) for r in rows]


def response(problems: list[str] | None = None, outcome: str = "done") -> bytes:
    result = {} if problems is None else {"problems": problems}
    line = {
        "schema": request.RESPONSE_SCHEMA,
        "outcome": outcome,
        "reason": None if outcome == "done" else "app_key_unusable",
        "result": result,
        "evidence": {},
    }
    return (json.dumps(line) + "\n").encode()


def github(code: int = 0, login: str = "df-bot", err: bytes = b"") -> plat.RunResult:
    report = {"login": login, "products": {}}
    if code == 1:
        report["products"] = {
            "100": {"full_name": "o/p", "permissions": {}, "mismatch": True}
        }
    out = json.dumps(report).encode() + b"\n" if code in (0, 1) else b""
    return plat.RunResult(code, out, err, True)


@pytest.fixture()
def w(fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> W:
    """Roles set up: accounts, `secrets/` of `df-publisher` (`0700`), the
    home of `df-exec` (`0700`, its own); the checks answer as told."""
    fake = fake_layout
    fake.make_dirs()
    lay = fake.layout
    accounts = {
        name: Account(name, uid, uid, "ready", "2026-10-28T00:00:00Z")
        for name, uid in (("df-publisher", PUB_UID), ("df-exec", EXEC_UID))
    }
    fsutil.write_atomic(lay.accounts, dump_accounts(accounts))
    lay.secrets_dir.mkdir(mode=0o700)
    fake.set_owner(lay.secrets_dir, PUB_UID)
    home = lay.role_home("df-exec")
    home.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    home.mkdir(mode=0o700)
    fake.set_owner(home, EXEC_UID)
    monkeypatch.setattr(plat, "trusted_path_problems", lambda _path: [])
    monkeypatch.setattr(cli, "ROOT_UID", ME)
    src = tmp_path / "human"
    src.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    world = W(fake, monkeypatch, src, outside, [response([])], [github()])

    def self_check(_layout: Layout) -> bytes:
        answer = world.self_check.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(setup_agent, "publisher_self_check", self_check)
    monkeypatch.setattr(
        setup_agent, "exec_check_github", lambda _l: world.github.pop(0)
    )
    return world


def owner(w: W, path: Path) -> int:
    return w.fake.owner_of(os.lstat(path))


def mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def tree(path: Path) -> list[tuple[str, int, int]]:
    """Every name under `path` with its mode and inode (a change shows)."""
    found = []
    for dirpath, dirs, files in os.walk(path):
        for name in [*dirs, *files]:
            st = os.lstat(Path(dirpath) / name)
            found.append((str(Path(dirpath, name)), st.st_mode, st.st_ino))
    return sorted(found)


# ---------- the command's shape ----------


@pytest.mark.parametrize(
    "argv",
    [
        ["setup-agent", "app-key", "--app-id", "7", "--file", "/k.pem"],
        ["setup-agent", "machine-token", "--file", "/t"],
    ],
)
def test_setup_agent_is_a_writer(argv: list[str]) -> None:
    assert launcher_entry.mode_of(argv) == "writer"
    assert cli.parsed_mode(cli._parse(argv)) == "writer"  # noqa: SLF001


@pytest.mark.parametrize(
    "argv",
    [
        ["setup-agent"],
        ["setup-agent", "app-key", "--file", "/k"],
        ["setup-agent", "app-key", "--app-id", "7"],
        ["setup-agent", "app-key", "--app-id", "0", "--file", "/k"],
        ["setup-agent", "app-key", "--app-id", "-3", "--file", "/k"],
        ["setup-agent", "app-key", "--app-id", "07", "--file", "/k"],
        ["setup-agent", "app-key", "--app-id", "x", "--file", "/k"],
        ["setup-agent", "machine-token"],
        ["setup-agent", "machine-token", "--file", "/t", "extra"],
        ["setup-agent", "other", "--file", "/t"],
    ],
)
def test_wrong_shape_is_a_usage_error(argv: list[str]) -> None:
    with pytest.raises(cli.UsageError):
        cli._parse(argv)  # noqa: SLF001


def test_app_id_must_be_positive(w: W) -> None:
    with pytest.raises(StepFailed, match="целое > 0"):
        setup_agent.install_app_key(w.lay, 0, w.source(KEY))
    assert os.listdir(w.lay.secrets_dir) == []


# ---------- app-key ----------


def test_app_key_written_as_publisher_0400(w: W) -> None:
    source = w.source(KEY)
    setup_agent.install_app_key(w.lay, APP_ID, source)
    target = w.lay.app_key(APP_ID)
    assert target == w.lay.secrets_dir / f"app-{APP_ID}.pem"
    assert target.read_bytes() == KEY
    assert owner(w, target) == PUB_UID and mode(target) == 0o400
    assert os.listdir(w.lay.secrets_dir) == [target.name]
    assert source.read_bytes() == KEY  # the source is never deleted
    chowns = [e for e in w.fake.events if e[0] == "fchown"]
    assert len(chowns) == 1 and chowns[0][2] == PUB_UID
    assert chowns[0][1].parent == w.lay.secrets_dir  # the temp, not the target
    assert ("renamed", target) in w.fake.events
    assert w.fake.events[-1] == ("sync_dir", w.lay.secrets_dir)


def test_app_key_path_is_the_publishers(w: W) -> None:
    assert ops._key_of(w.lay, APP_ID) == w.lay.app_key(APP_ID)  # noqa: SLF001


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not a key\n",
        KEY.replace(b"RSA PRIVATE", b"ENCRYPTED PRIVATE"),
        KEY.replace(b"END RSA", b"END"),  # BEGIN and END must agree
        KEY.replace(b"-----BEGIN RSA PRIVATE KEY-----\n", b""),
        KEY.replace(b"\n", b"\nProc-Type: 4,ENCRYPTED\n", 1),
        KEY + b"trailing",
        b"-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----\n",
        KEY[:-30] + b"\xff" * 20 + KEY[-30:],
    ],
)
def test_bad_pem_refused_without_write(w: W, data: bytes) -> None:
    with pytest.raises(StepFailed, match="не PEM-ключ") as exc:
        setup_agent.install_app_key(w.lay, APP_ID, w.source(data))
    assert KEY_MARK.decode() not in str(exc.value)
    assert os.listdir(w.lay.secrets_dir) == []
    assert not [e for e in w.fake.events if e[0] == "fchown"]


def test_pkcs8_key_accepted(w: W) -> None:
    key = KEY.replace(b"RSA PRIVATE", b"PRIVATE")
    setup_agent.install_app_key(w.lay, APP_ID, w.source(key))
    assert w.lay.app_key(APP_ID).read_bytes() == key


def test_oversize_key_refused(w: W) -> None:
    body = b"QUFB" * 16 + b"\n"
    big = KEY.replace(b"-----END", body * 300 + b"-----END")
    assert len(big) > setup_agent.KEY_CAP
    with pytest.raises(StepFailed, match="больше"):
        setup_agent.install_app_key(w.lay, APP_ID, w.source(big))
    assert os.listdir(w.lay.secrets_dir) == []


@pytest.mark.parametrize("kind", ["link", "fifo", "dir", "relative", "missing"])
def test_source_must_be_a_regular_file(w: W, kind: str) -> None:
    real = w.source(KEY, "real.pem")
    path = w.src / "k.pem"
    if kind == "link":
        path.symlink_to(real)
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "dir":
        path.mkdir()
    elif kind == "relative":
        path = Path("k.pem")
    with pytest.raises(StepFailed):
        setup_agent.install_app_key(w.lay, APP_ID, path)
    assert os.listdir(w.lay.secrets_dir) == []


@pytest.mark.parametrize(
    "case", ["missing", "mode", "root-owned", "link", "file", "untrusted"]
)
def test_secrets_dir_preconditions(w: W, case: str) -> None:
    secrets = w.lay.secrets_dir
    if case == "missing":
        secrets.rmdir()
    elif case == "mode":
        secrets.chmod(0o750)
    elif case == "root-owned":
        w.fake.set_owner(secrets, ME)
    elif case in ("link", "file"):
        secrets.rmdir()
        if case == "link":
            secrets.symlink_to(w.outside)
        else:
            secrets.write_text("")
    else:
        w.mp.setattr(plat, "trusted_path_problems", lambda p: [f"{p}: не root"])
    with pytest.raises(StepFailed, match="roles setup|ссылка или не каталог") as exc:
        setup_agent.install_app_key(w.lay, APP_ID, w.source(KEY))
    if case != "link" and case != "file":
        assert "roles setup" in str(exc.value)
    assert os.listdir(w.outside) == []
    assert not [e for e in w.fake.events if e[0] == "fchown"]
    if secrets.is_dir() and not secrets.is_symlink():
        assert os.listdir(secrets) == []


def test_secrets_link_to_a_publisher_dir_refused(w: W) -> None:
    """A link whose target would pass every other check: `O_NOFOLLOW` alone
    refuses it."""
    w.lay.secrets_dir.rmdir()
    w.outside.chmod(0o700)
    w.fake.set_owner(w.outside, PUB_UID)
    w.lay.secrets_dir.symlink_to(w.outside)
    with pytest.raises(StepFailed, match="ссылка или не каталог"):
        setup_agent.install_app_key(w.lay, APP_ID, w.source(KEY))
    assert os.listdir(w.outside) == []


def test_secrets_parents_judged_by_trusted_path(w: W) -> None:
    seen: list[Path] = []
    w.mp.setattr(plat, "trusted_path_problems", lambda p: seen.append(p) or [])
    setup_agent.install_app_key(w.lay, APP_ID, w.source(KEY))
    assert w.lay.secrets_dir.parent in seen


def test_app_key_repeat_replaces(w: W) -> None:
    setup_agent.install_app_key(w.lay, APP_ID, w.source(KEY))
    first = os.lstat(w.lay.app_key(APP_ID)).st_ino
    other = KEY.replace(b"ZmFrZVNF", b"b3RoZXJr")
    setup_agent.install_app_key(w.lay, APP_ID, w.source(other))
    target = w.lay.app_key(APP_ID)
    assert target.read_bytes() == other and os.lstat(target).st_ino != first
    assert os.listdir(w.lay.secrets_dir) == [target.name]
    assert owner(w, target) == PUB_UID and mode(target) == 0o400


# ---------- machine-token ----------


def test_token_written_with_dirs_of_df_exec(w: W) -> None:
    source = w.source(TOKEN + b"\n")
    setup_agent.install_machine_token(w.lay, source)
    config, df_dir = w.home / ".config", w.home / ".config" / "df"
    for path in (config, df_dir):
        assert stat.S_ISDIR(os.lstat(path).st_mode)
        assert owner(w, path) == EXEC_UID and mode(path) == 0o700
    assert w.token_file.read_bytes() == TOKEN + b"\n"
    assert owner(w, w.token_file) == EXEC_UID and mode(w.token_file) == 0o600
    assert os.listdir(df_dir) == ["github-token"]
    assert source.read_bytes() == TOKEN + b"\n"
    chowned = [e[1] for e in w.fake.events if e[0] == "fchown"]
    assert chowned[:2] == [config, df_dir]  # through the descriptors
    assert chowned[2].parent == df_dir and chowned[2].name != "github-token"
    assert ("renamed", w.token_file) in w.fake.events


def test_token_without_final_newline_accepted(w: W) -> None:
    setup_agent.install_machine_token(w.lay, w.source(TOKEN2))
    assert w.token_file.read_bytes() == TOKEN2 + b"\n"


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\n",
        TOKEN[:20] + b"\n" + TOKEN[20:],  # a newline inside
        TOKEN + b"\n\n",
        TOKEN + b"\r\n",
        b" " + TOKEN,
        b"gho_" + TOKEN[11:],
        TOKEN + b" extra",
        b"ghp_" + b"a" * 252,  # 256 bytes
        b"github_pat_" + b"a" * 245,
    ],
)
def test_bad_token_refused_without_write(w: W, data: bytes) -> None:
    with pytest.raises(StepFailed) as exc:
        setup_agent.install_machine_token(w.lay, w.source(data))
    assert TOKEN.decode() not in str(exc.value)
    assert os.listdir(w.home) == []


def test_longest_token_accepted(w: W) -> None:
    token = b"ghp_" + b"a" * 251
    setup_agent.install_machine_token(w.lay, w.source(token + b"\n"))
    assert w.token_file.read_bytes() == token + b"\n"


def test_token_repeat_replaces_atomically(w: W) -> None:
    setup_agent.install_machine_token(w.lay, w.source(TOKEN))
    first = os.lstat(w.token_file).st_ino
    w.fake.clear()
    setup_agent.install_machine_token(w.lay, w.source(TOKEN2))
    assert w.token_file.read_bytes() == TOKEN2 + b"\n"
    assert os.lstat(w.token_file).st_ino != first
    assert os.listdir(w.token_file.parent) == ["github-token"]
    # the second run found both directories: their owner is not touched
    chowned = [e[1] for e in w.fake.events if e[0] == "fchown"]
    assert len(chowned) == 1 and chowned[0].parent == w.token_file.parent


def test_found_dirs_kept_as_they_are(w: W) -> None:
    """Root never changes the owner or mode of what it found."""
    config = w.home / ".config"
    config.mkdir(mode=0o755)
    w.fake.set_owner(config, EXEC_UID)
    (config / "df").mkdir(mode=0o750)
    w.fake.set_owner(config / "df", EXEC_UID)
    w.fake.clear()
    setup_agent.install_machine_token(w.lay, w.source(TOKEN))
    assert mode(config) == 0o755 and mode(config / "df") == 0o750
    chowned = [e[1] for e in w.fake.events if e[0] == "fchown"]
    assert config not in chowned and config / "df" not in chowned
    assert ("chmod", config) not in w.fake.events


@pytest.mark.parametrize("which", [".config", ".config/df"])
@pytest.mark.parametrize("found", ["root", "other", "file"])
def test_found_parent_not_df_exec_dir_refused(w: W, which: str, found: str) -> None:
    config = w.home / ".config"
    config.mkdir(mode=0o700)
    w.fake.set_owner(config, EXEC_UID)
    path = w.home / which
    if path != config:
        path.mkdir(mode=0o700)
    if found == "file":
        path.rmdir()
        path.write_text("")
        w.fake.set_owner(path, EXEC_UID)
    else:
        w.fake.set_owner(path, ME if found == "root" else ME + 5)
    before = tree(w.home)
    w.fake.clear()
    with pytest.raises(StepFailed, match="root не меняет|ссылка или не каталог"):
        setup_agent.install_machine_token(w.lay, w.source(TOKEN))
    assert tree(w.home) == before
    assert not [e for e in w.fake.events if e[0] == "fchown"]


@pytest.mark.parametrize("where", ["home", ".config", ".config/df"])
def test_token_parent_symlink_refused(w: W, where: str) -> None:
    """A link in place of the home, `.config` or `df`: a refusal; nothing is
    made or changed outside the home (nor in it)."""
    home = w.home
    if where == "home":
        home.rmdir()
        home.symlink_to(w.outside)
    else:
        if where == ".config/df":
            (home / ".config").mkdir(mode=0o700)
            w.fake.set_owner(home / ".config", EXEC_UID)
        (home / where).symlink_to(w.outside)
    outside_before = tree(w.outside)
    before = tree(home) if where != "home" else []
    with pytest.raises(StepFailed, match="ссылка или не каталог"):
        setup_agent.install_machine_token(w.lay, w.source(TOKEN))
    assert tree(w.outside) == outside_before == []
    if where != "home":
        assert tree(home) == before


def test_token_parent_swapped_during_write(w: W) -> None:
    """`df` is replaced by a link between `openat` and `renameat`: the write
    goes into the open descriptor; the link's target stays untouched."""
    df_dir = w.home / ".config" / "df"
    moved = w.home / ".config" / "df-moved"
    fake_fchown = os.fchown
    swapped: list[bool] = []

    def fchown(fd: int, uid: int, gid: int) -> None:
        fake_fchown(fd, uid, gid)
        if not swapped and stat.S_ISREG(os.fstat(fd).st_mode):
            os.rename(df_dir, moved)
            df_dir.symlink_to(w.outside)
            swapped.append(True)

    w.mp.setattr(os, "fchown", fchown)
    setup_agent.install_machine_token(w.lay, w.source(TOKEN))
    assert swapped
    assert (moved / "github-token").read_bytes() == TOKEN + b"\n"
    assert os.listdir(moved) == ["github-token"]
    assert df_dir.is_symlink() and os.listdir(w.outside) == []


@pytest.mark.parametrize("case", ["other-owner", "group-write"])
def test_home_parents_must_be_roots(w: W, case: str) -> None:
    parent = w.home.parent
    if case == "other-owner":
        w.fake.set_owner(parent, ME + 5)
    else:
        parent.chmod(0o775)
    with pytest.raises(StepFailed, match="не каталог root.*roles setup"):
        setup_agent.install_machine_token(w.lay, w.source(TOKEN))
    assert os.listdir(w.home) == []


def test_layout_root_judged_by_trusted_path(w: W) -> None:
    w.mp.setattr(
        plat,
        "trusted_path_problems",
        lambda p: [f"{p}: не root"] if p == w.lay.root else [],
    )
    with pytest.raises(StepFailed, match="не root"):
        setup_agent.install_machine_token(w.lay, w.source(TOKEN))
    assert os.listdir(w.home) == []


@pytest.mark.parametrize("case", ["root-owned", "missing"])
def test_home_must_be_df_execs(w: W, case: str) -> None:
    if case == "missing":
        w.home.rmdir()
    else:
        w.fake.set_owner(w.home, ME)
    with pytest.raises(StepFailed, match="roles setup"):
        setup_agent.install_machine_token(w.lay, w.source(TOKEN))
    assert not w.home.exists() or os.listdir(w.home) == []


@pytest.mark.parametrize("name", ["df-exec", "df-publisher"])
def test_account_not_ready_refused(w: W, name: str) -> None:
    accounts = {
        n: Account(n, uid, uid, "creating" if n == name else "ready", "t")
        for n, uid in (("df-publisher", PUB_UID), ("df-exec", EXEC_UID))
    }
    fsutil.write_atomic(w.lay.accounts, dump_accounts(accounts))
    install: Callable[[], None] = (
        (lambda: setup_agent.install_machine_token(w.lay, w.source(TOKEN)))
        if name == "df-exec"
        else (lambda: setup_agent.install_app_key(w.lay, APP_ID, w.source(KEY)))
    )
    with pytest.raises(StepFailed, match=f"{name}.*roles setup"):
        install()
    assert os.listdir(w.home) == [] and os.listdir(w.lay.secrets_dir) == []


def test_one_spelling_of_the_token_path() -> None:
    """`check-github` reads what `setup-agent` writes: one constant, and no
    other spelling of the file name in `df/`."""
    assert MACHINE_TOKEN == (".config", "df", "github-token")
    assert launch_release.MACHINE_TOKEN is df_paths.MACHINE_TOKEN
    root = Path(fsutil.__file__).parent
    spelled = [
        p.relative_to(root).as_posix()
        for p in root.rglob("*.py")
        if '"github-token"' in p.read_text(encoding="utf-8")
    ]
    assert spelled == ["paths.py"]


def test_check_github_reads_the_written_token(w: W) -> None:
    setup_agent.install_machine_token(w.lay, w.source(TOKEN))
    seen: list[str] = []

    def transport(url: str, headers: dict[str, str]) -> tuple[int, bytes]:
        seen.append(headers["Authorization"])
        return 200, json.dumps({"login": "df-bot"}).encode()

    w.mp.setattr(os, "geteuid", lambda: EXEC_UID)
    report, code = launch_release.check_github(w.lay, w.home, transport)
    assert code == 0 and report["login"] == "df-bot"
    assert seen == [f"Bearer {TOKEN.decode()}"]


# ---------- the command: checks, journal, exit codes ----------


def key_argv(path: Path) -> list[str]:
    return ["setup-agent", "app-key", "--app-id", str(APP_ID), "--file", str(path)]


def token_argv(path: Path) -> list[str]:
    return ["setup-agent", "machine-token", "--file", str(path)]


def assert_no_secret(w: W, captured: pytest.CaptureFixture[str]) -> None:
    out = captured.readouterr()
    for text in (out.out, out.err, w.journal_text()):
        assert KEY_MARK.decode() not in text
        assert TOKEN.decode() not in text and TOKEN2.decode() not in text


def test_cli_app_key_done(w: W, capsys: pytest.CaptureFixture[str]) -> None:
    assert w.main(key_argv(w.source(KEY))) == 0
    out = capsys.readouterr()
    assert f"записан app-key {w.lay.app_key(APP_ID)}" in out.out
    assert "self-check издателя: проблем нет" in out.out
    assert w.rows() == [("setup-agent", "done")]
    assert w.self_check == [] and w.github == [github()]  # self-check only
    assert KEY_MARK.decode() not in out.out + out.err + w.journal_text()


def test_cli_app_key_unused_app_warns(w: W, capsys: pytest.CaptureFixture[str]) -> None:
    assert w.main(key_argv(w.source(KEY))) == 0
    want = f"предупреждение: не проверено: нет продукта с App {APP_ID}"
    assert want in capsys.readouterr().err
    product = Product(100, "o/p", "fork", APP_ID, 2, "df-bot", "/c")
    (w.lay.etc / "products.toml").write_text(dump_products({100: product}))
    w.self_check = [response([])]
    assert w.main(key_argv(w.source(KEY))) == 0
    assert "не проверено" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "answer",
    [
        response(["app_key_unusable: 4242"]),
        response(["app_unreadable: 4242: github_404: Not Found"]),
        response(["app_installation_outside_products: 9"]),
        response(["app_installation_unreadable: 9"]),
        response(["perimeter_unavailable: lost", "app_key_unusable: 4242"]),
        response(None, outcome="refused"),
        response(None),  # no `problems`
        OSError("self-check: the call failed (code 1)"),
        b"",
        b"not json\n",
        response([]) * 2,
    ],
)
def test_cli_app_key_failed_check_needs_human(
    w: W, capsys: pytest.CaptureFixture[str], answer: bytes | Exception
) -> None:
    w.self_check = [answer]
    assert w.main(key_argv(w.source(KEY))) == 3
    err = capsys.readouterr().err
    assert "секрет записан, проверка не прошла: self-check" in err
    assert w.rows() == [("setup-agent", "needs-human")]
    assert w.lay.app_key(APP_ID).read_bytes() == KEY  # the secret stays


@pytest.mark.parametrize(
    "problems", [["perimeter_unavailable: lost"], ["other_subsystem: x", "y"]]
)
def test_cli_app_key_non_app_problem_is_a_warning(
    w: W, capsys: pytest.CaptureFixture[str], problems: list[str]
) -> None:
    """Not the key's fault: printed as a warning, exit 0, journal `done`."""
    w.self_check = [response(problems)]
    assert w.main(key_argv(w.source(KEY))) == 0
    out = capsys.readouterr()
    for problem in problems:
        assert f"предупреждение: self-check: {problem}" in out.err
    assert "self-check издателя: проблем App нет" in out.out
    assert "проверка не прошла" not in out.err
    assert w.rows() == [("setup-agent", "done")]


def test_cli_token_done_prints_login(w: W, capsys: pytest.CaptureFixture[str]) -> None:
    assert w.main(token_argv(w.source(TOKEN))) == 0
    out = capsys.readouterr()
    assert "machine user: df-bot" in out.out
    assert w.rows() == [("setup-agent", "done")]
    assert w.github == [] and w.self_check == [response([])]  # check-github only
    assert TOKEN.decode() not in out.out + out.err + w.journal_text()


def test_cli_token_login_mismatch_is_a_warning(
    w: W, capsys: pytest.CaptureFixture[str]
) -> None:
    w.github = [github(1, login="someone")]
    assert w.main(token_argv(w.source(TOKEN))) == 0
    out = capsys.readouterr()
    assert "machine user: someone" in out.out
    assert "предупреждение: логин someone не совпадает" in out.err
    assert "o/p" in out.err
    assert w.rows() == [("setup-agent", "done")]


@pytest.mark.parametrize(
    "result",
    [
        github(2, err=b"Refused: GET https://api.github.com/user: HTTP 401\n"),
        plat.RunResult(0, b"{}\n", b"", True),  # no login
        plat.RunResult(0, b"garbage", b"", True),
        plat.RunResult(1, b"", b"sudo: a password is required\n", False),
        plat.RunResult(-9, b"", b"", True, timed_out=True),
        github(2, err=b"echo " + TOKEN + b"\n"),  # masked if it ever shows
        plat.RunResult(2, b'{"login": "df-bot", "products": {}}\n', b"", True),
    ],
)
def test_cli_token_failed_check_needs_human(
    w: W, capsys: pytest.CaptureFixture[str], result: plat.RunResult
) -> None:
    w.github = [result]
    assert w.main(token_argv(w.source(TOKEN))) == 3
    out = capsys.readouterr()
    assert "секрет записан, проверка не прошла: check-github" in out.err
    assert TOKEN.decode() not in out.out + out.err + w.journal_text()
    assert w.rows() == [("setup-agent", "needs-human")]
    assert w.token_file.read_bytes() == TOKEN + b"\n"


@pytest.mark.parametrize("kind", ["key", "token"])
def test_cli_refusal_changes_nothing(
    w: W, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    """A source of the wrong form: exit 2, nothing written, nothing
    journalled, no check run -- and the near-secret is not echoed."""
    if kind == "key":
        argv = key_argv(w.source(KEY.replace(b"END RSA", b"END")))
    else:
        argv = token_argv(w.source(TOKEN + b"\n" + TOKEN2))
    assert w.main(argv) == 2
    assert w.journal_text() == ""
    assert os.listdir(w.home) == [] and os.listdir(w.lay.secrets_dir) == []
    assert w.self_check and w.github  # neither check was asked
    assert_no_secret(w, capsys)


@pytest.mark.parametrize("kind", ["key", "token"])
def test_cli_failed_write_is_journalled(
    w: W, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    real = os.rename

    def rename(src: object, dst: object, **kw: object) -> None:
        if str(dst) in ("github-token", f"app-{APP_ID}.pem"):
            raise OSError(28, "No space left on device")
        real(src, dst, **kw)  # type: ignore[arg-type]

    w.mp.setattr(os, "rename", rename)
    argv = key_argv(w.source(KEY)) if kind == "key" else token_argv(w.source(TOKEN))
    assert w.main(argv) == 2
    assert w.rows() == [("setup-agent", "failed")]
    assert os.listdir(w.lay.secrets_dir) == []
    token_dir = w.token_file.parent
    assert not token_dir.exists() or os.listdir(token_dir) == []
    assert_no_secret(w, capsys)


def test_cli_without_root_refused(w: W, capsys: pytest.CaptureFixture[str]) -> None:
    w.mp.setattr(cli, "ROOT_UID", ME + 1)
    assert w.main(token_argv(w.source(TOKEN))) == 2
    assert "требует root" in capsys.readouterr().err
    assert os.listdir(w.home) == []


# ---------- the check seams ----------


def test_self_check_seam_asks_df_publish_as_root(
    fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], bytes]] = []

    def run_with_input(argv: list[str], data: bytes, **_kw: object) -> plat.RunResult:
        calls.append((list(argv), data))
        return plat.RunResult(0, response([]), b"", True)

    monkeypatch.setattr(plat, "run_with_input", run_with_input)
    lay = fake_layout.layout
    assert setup_agent.publisher_self_check(lay) == response([])
    ((argv, data),) = calls
    wrapper = str(lay.libexec / "df-publish")
    assert argv == ["/usr/bin/sudo", "-n", "-u", "df-publisher", wrapper, "self-check"]
    req = request.parse("self-check", data)
    assert req.request_id.startswith("setup-agent-")


def test_check_github_seam_runs_df_launch_as_df_exec(
    fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def run_command(argv: list[str], **_kw: object) -> plat.RunResult:
        calls.append(list(argv))
        return github()

    monkeypatch.setattr(plat, "run_command", run_command)
    lay = fake_layout.layout
    assert setup_agent.exec_check_github(lay) == github()
    wrapper = str(lay.libexec / "df-launch")
    sudo = ["/usr/bin/sudo", "-n", "-u", "df-exec"]
    assert calls == [[*sudo, wrapper, "df-exec", "check-github"]]


def test_fd_path_names_the_temp(w: W) -> None:
    """The recorded `fchown` of the token is the temp's (a guard of the
    owner assertions above)."""
    setup_agent.install_machine_token(w.lay, w.source(TOKEN))
    temp = [e[1] for e in w.fake.events if e[0] == "fchown"][-1]
    assert fsutil.WRITE_TEMP.fullmatch(temp.name)


# ---------- review round 1 ----------


def test_app_key_through_a_system_etc_link(w: W) -> None:
    """B1: macOS `/etc -> private/etc`. The system parents follow links
    (vetted by the trusted-path policy); `app-key` succeeds."""
    root = w.lay.root
    (root / "private").mkdir(mode=0o755)
    os.rename(root / "etc", root / "private" / "etc")
    (root / "etc").symlink_to("private/etc")
    setup_agent.install_app_key(w.lay, APP_ID, w.source(KEY))
    target = root / "private" / "etc" / "df" / "secrets" / f"app-{APP_ID}.pem"
    assert target.read_bytes() == KEY and owner(w, target) == PUB_UID


@pytest.mark.parametrize("where", ["df", "secrets"])
def test_installer_components_never_follow(w: W, where: str) -> None:
    """B1: below the system `etc`, `df` and `secrets` are still no-follow,
    also when `etc` itself is a link."""
    root = w.lay.root
    (root / "private").mkdir(mode=0o755)
    os.rename(root / "etc", root / "private" / "etc")
    (root / "etc").symlink_to("private/etc")
    real = root / "private" / "etc" / "df"
    moved = real if where == "secrets" else root / "df-real"
    if where == "df":
        os.rename(real, moved)
        real.symlink_to(moved)
    else:
        os.rename(moved / "secrets", root / "secrets-real")
        (moved / "secrets").symlink_to(root / "secrets-real")
    with pytest.raises(StepFailed, match="ссылка или не каталог"):
        setup_agent.install_app_key(w.lay, APP_ID, w.source(KEY))
    assert not [e for e in w.fake.events if e[0] == "fchown"]


@pytest.mark.parametrize(
    "swap",
    ["df-exec", "df-exec-empty", "other", "other-empty", "root-full", "root-mode"],
)
def test_created_dir_swapped_before_open(w: W, swap: str) -> None:
    """M1: `.config` is made, then swapped before root opens it: root hands
    over only its own fresh directory; a swapped one is a found object."""
    real_make_dir = fsutil.make_dir
    config = w.home / ".config"

    def make_dir(parent: int, name: str, mode_: int, **kw: object) -> bool:
        created = real_make_dir(parent, name, mode_, **kw)  # type: ignore[arg-type]
        if name == ".config":
            os.rename(config, w.home / ".config-root")
            config.mkdir(mode=0o755 if swap == "root-mode" else 0o700)
            if swap in ("df-exec", "other", "root-full"):
                (config / "kept").write_text("x")
            uid = {"df-exec": EXEC_UID, "other": ME + 5}.get(
                swap.removesuffix("-empty"), ME
            )
            w.fake.set_owner(config, uid)
        return created

    w.mp.setattr(fsutil, "make_dir", make_dir)
    if swap.startswith("df-exec"):
        setup_agent.install_machine_token(w.lay, w.source(TOKEN))
        assert w.token_file.read_bytes() == TOKEN + b"\n"
        assert mode(config) == 0o700
    else:
        with pytest.raises(StepFailed, match="root не меняет"):
            setup_agent.install_machine_token(w.lay, w.source(TOKEN))
        assert owner(w, config) == (ME + 5 if swap.startswith("other") else ME)
    assert config not in [e[1] for e in w.fake.events if e[0] == "fchown"]


@pytest.mark.parametrize("planted", ["file", "hardlink"])
def test_planted_temp_name_refused(
    w: W, capsys: pytest.CaptureFixture[str], planted: str
) -> None:
    """M2: `O_EXCL` on the temp: a planted name (a hard link to another
    file too) is never opened, chowned or written."""
    hexed = "0" * 32
    w.mp.setattr(fsutil.uuid, "uuid4", lambda: type("U", (), {"hex": hexed})())
    temp = w.lay.secrets_dir / f".app-{APP_ID}.pem.{os.getpid()}.{hexed}.tmp"
    victim = w.outside / "victim"
    victim.write_text("victim")
    if planted == "file":
        temp.write_text("planted")
    else:
        os.link(victim, temp)
    before = os.lstat(victim)
    assert w.main(key_argv(w.source(KEY))) == 2
    assert w.rows() == [("setup-agent", "failed")]
    assert not [e for e in w.fake.events if e[0] == "fchown"]
    assert victim.read_text() == "victim"
    assert w.fake.owner_of(os.lstat(victim)) == w.fake.owner_of(before)
    assert temp.exists() and not w.lay.app_key(APP_ID).exists()
    assert "FileExistsError" in capsys.readouterr().err


def _record_io(w: W) -> None:
    """`fchmod`, the data write and `sync_file` join the fake's events."""
    from tests.df.crash_model import fd_path

    real_fchmod, real_write = os.fchmod, fsutil._write_all  # noqa: SLF001
    real_sync_file = fsutil.sync_file

    def fchmod(fd: int, mode_: int) -> None:
        w.fake.events.append(("fchmod", fd_path(fd)))
        real_fchmod(fd, mode_)

    def write_all(fd: int, data: bytes) -> None:
        w.fake.events.append(("write", fd_path(fd)))
        real_write(fd, data)

    def sync_file(fd: int) -> None:
        w.fake.events.append(("sync_file", fd_path(fd)))
        real_sync_file(fd)

    w.mp.setattr(os, "fchmod", fchmod)
    w.mp.setattr(fsutil, "_write_all", write_all)
    w.mp.setattr(fsutil, "sync_file", sync_file)


def _in_order(events: list[tuple[object, ...]], want: list[tuple[object, ...]]) -> bool:
    it = iter(events)
    return all(any(e[:2] == x for e in it) for x in want)


def test_token_events_in_order(w: W) -> None:
    """M2/N4: each directory is handed over and synced (itself, then its
    parent) before the next; the temp is owned and moded before its first
    byte, synced before the rename; the directory synced last."""
    _record_io(w)
    setup_agent.install_machine_token(w.lay, w.source(TOKEN))
    config, df_dir = w.home / ".config", w.home / ".config" / "df"
    events = w.fake.events
    temp = next(e[1] for e in events if e[0] == "write")
    assert isinstance(temp, Path) and temp.parent == df_dir
    assert _in_order(
        events,
        [
            ("fchown", config),
            ("fchmod", config),
            ("sync_dir", config),
            ("sync_dir", w.home),
            ("fchown", df_dir),
            ("fchmod", df_dir),
            ("sync_dir", df_dir),
            ("sync_dir", config),
            ("fchown", temp),
            ("fchmod", temp),
            ("write", temp),
            ("sync_file", temp),
            ("renamed", w.token_file),
            ("sync_dir", df_dir),
        ],
    )
    assert events[-1] == ("sync_dir", df_dir)


def test_key_events_in_order(w: W) -> None:
    _record_io(w)
    setup_agent.install_app_key(w.lay, APP_ID, w.source(KEY))
    secrets = w.lay.secrets_dir
    temp = next(e[1] for e in w.fake.events if e[0] == "write")
    assert _in_order(
        w.fake.events,
        [
            ("fchown", temp),
            ("fchmod", temp),
            ("write", temp),
            ("sync_file", temp),
            ("renamed", w.lay.app_key(APP_ID)),
            ("sync_dir", secrets),
        ],
    )


def test_token_target_planted_as_dir(w: W, capsys: pytest.CaptureFixture[str]) -> None:
    """M2: `github-token` is a directory: the rename fails, the directory and
    its content are untouched, no temp is left; journalled `failed`."""
    df_dir = w.token_file.parent
    df_dir.mkdir(parents=True, mode=0o700)
    for path in (df_dir.parent, df_dir):
        w.fake.set_owner(path, EXEC_UID)
    w.token_file.mkdir()
    (w.token_file / "inside").write_text("x")
    before = tree(df_dir)
    assert w.main(token_argv(w.source(TOKEN))) == 2
    assert tree(df_dir) == before
    assert w.rows() == [("setup-agent", "failed")]
    assert_no_secret(w, capsys)


@pytest.mark.parametrize("kind", ["key", "token"])
def test_not_durable_after_rename_needs_human(
    w: W, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    """M4: the directory's sync fails after the rename: the secret is
    already replaced, so NeedsHuman (exit 3), never `failed`."""
    from tests.df.crash_model import fd_path

    target = w.lay.app_key(APP_ID) if kind == "key" else w.token_file
    real_sync_dir = fsutil.sync_dir

    def sync_dir(fd_or_path: int | Path) -> fsutil.SyncNote:
        path = fd_path(fd_or_path) if isinstance(fd_or_path, int) else fd_or_path
        if path == target.parent and target.exists():
            raise OSError(5, "Input/output error")
        return real_sync_dir(fd_or_path)

    w.mp.setattr(fsutil, "sync_dir", sync_dir)
    argv = key_argv(w.source(KEY)) if kind == "key" else token_argv(w.source(TOKEN))
    assert w.main(argv) == 3
    err = capsys.readouterr().err
    assert "записан, но не закреплён: повторите команду" in err
    assert w.rows() == [("setup-agent", "needs-human")]
    assert target.exists()
    assert w.self_check and w.github  # no check after an unsettled write
    text = err + w.journal_text()
    assert TOKEN.decode() not in text and KEY_MARK.decode() not in text


def two_apps(w: W, *extra: Product) -> None:
    rows = [
        Product(100, "o/a", "fork", 1, 11, "df-bot", "/c"),
        Product(200, "o/b", "fork", 2, 21, "df-bot", "/c"),
        *extra,
    ]
    (w.lay.etc / "products.toml").write_text(
        dump_products({p.repository_id: p for p in rows})
    )


@pytest.mark.parametrize(
    ("problem", "code"),
    [
        ("app_key_unusable: 2", 0),
        ("app_unreadable: 2: github_404: Not Found", 0),
        ("app_installation_unreadable: 21", 0),
        ("app_installation_outside_products: 21", 0),
        ("app_key_unusable: 1", 3),
        ("app_unreadable: 1: github_401: Bad credentials", 3),
        ("app_installation_unreadable: 11", 3),
        ("app_installation_outside_products: 99", 3),  # not mapped
        ("app_unreadable: x", 3),  # not attributable
    ],
)
def test_only_this_apps_problems_fail(
    w: W, capsys: pytest.CaptureFixture[str], problem: str, code: int
) -> None:
    """M3: two Apps, the key of App 1 installed; another App's problem is a
    warning."""
    two_apps(w)
    w.self_check = [response([problem])]
    argv = ["setup-agent", "app-key", "--app-id", "1", "--file", str(w.source(KEY))]
    assert w.main(argv) == code
    err = capsys.readouterr().err
    if code == 0:
        assert f"предупреждение: self-check: {problem}" in err
        assert w.rows() == [("setup-agent", "done")]
    else:
        assert f"проверка не прошла: self-check: {problem}" in err


def test_installation_claimed_by_two_apps_is_this_apps(
    w: W, capsys: pytest.CaptureFixture[str]
) -> None:
    """An installation id two Apps claim is ambiguous: judged as this App's
    (conservative), whatever the file order."""
    two_apps(w, Product(300, "o/c", "fork", 2, 11, "df-bot", "/c"))
    w.self_check = [response(["app_installation_unreadable: 11"])]
    argv = ["setup-agent", "app-key", "--app-id", "1", "--file", str(w.source(KEY))]
    assert w.main(argv) == 3
    assert "проверка не прошла" in capsys.readouterr().err


def test_unused_app_with_another_apps_problem(
    w: W, capsys: pytest.CaptureFixture[str]
) -> None:
    """M3 with c1: App N unused, another App broken: both warnings, exit 0."""
    two_apps(w)
    w.self_check = [response(["app_key_unusable: 2"])]
    assert w.main(key_argv(w.source(KEY))) == 0
    err = capsys.readouterr().err
    assert f"не проверено: нет продукта с App {APP_ID}" in err
    assert "предупреждение: self-check: app_key_unusable: 2" in err


def test_home_parent_judged_by_trusted_path(w: W) -> None:
    """M5: the home's parent goes through the trusted-path policy (ACLs)."""
    seen: list[Path] = []

    def policy(path: Path) -> list[str]:
        seen.append(path)
        return [f"{path}: ACL"] if path == w.home.parent else []

    w.mp.setattr(plat, "trusted_path_problems", policy)
    with pytest.raises(StepFailed, match="ACL.*roles setup"):
        setup_agent.install_machine_token(w.lay, w.source(TOKEN))
    assert w.home.parent in seen and os.listdir(w.home) == []


@pytest.mark.parametrize("kind", ["fifo", "dir", "link"])
def test_source_not_opened_unless_regular(w: W, kind: str) -> None:
    """N1: `lstat` first: a FIFO, a directory or a link is never opened."""
    path = w.src / "k.pem"
    if kind == "fifo":
        os.mkfifo(path)
    elif kind == "dir":
        path.mkdir()
    else:
        path.symlink_to(w.source(KEY, "real.pem"))
    opened: list[Path] = []
    w.mp.setattr(setup_agent.inputs, "read_capped", lambda p, _c: opened.append(p))
    with pytest.raises(StepFailed, match="не обычный файл"):
        setup_agent.install_app_key(w.lay, APP_ID, path)
    assert opened == []


@pytest.mark.parametrize("which", [".config", ".config/df"])
def test_found_writable_dir_warns(
    w: W, capsys: pytest.CaptureFixture[str], which: str
) -> None:
    """N2: a found `df-exec` directory writable by others is kept, with a
    warning."""
    df_dir = w.home / ".config" / "df"
    df_dir.mkdir(parents=True, mode=0o700)
    for path in (df_dir.parent, df_dir):
        w.fake.set_owner(path, EXEC_UID)
    (w.home / which).chmod(0o777)
    assert w.main(token_argv(w.source(TOKEN))) == 0
    err = capsys.readouterr().err
    assert f"предупреждение: {w.home / which}: права 0777" in err
    assert mode(w.home / which) == 0o777


def test_found_root_empty_dir_names_rmdir(w: W) -> None:
    """N3: a crash's leftover (root's empty `.config`) is named with how to
    remove it."""
    config = w.home / ".config"
    config.mkdir(mode=0o700)
    with pytest.raises(StepFailed, match=f"sudo rmdir {config}"):
        setup_agent.install_machine_token(w.lay, w.source(TOKEN))


# ---------- the home's system parents ----------


def link_home_parent(w: W) -> Path:
    """`<root>/Users` (or `home`) becomes a link to `var/home`, as a Linux
    `/home -> var/home`; returns the real parent."""
    parent = w.home.parent
    real = w.lay.root / "var" / "home"
    os.rename(parent, real)
    parent.symlink_to("var/home")
    return real


def test_token_through_a_system_home_link(w: W) -> None:
    """The home's parent is a system link: followed (vetted by the
    trusted-path policy); the token is written in the real home."""
    real = link_home_parent(w)
    setup_agent.install_machine_token(w.lay, w.source(TOKEN))
    token = real / "df-exec" / Path(*MACHINE_TOKEN)
    assert token.read_bytes() == TOKEN + b"\n"
    assert owner(w, token) == EXEC_UID and mode(token) == 0o600


@pytest.mark.parametrize("where", ["home", ".config", ".config/df"])
def test_own_components_never_follow_under_a_home_link(w: W, where: str) -> None:
    """Below the followed system parent, the home, `.config` and `df` are
    still no-follow."""
    real = link_home_parent(w) / "df-exec"
    if where == "home":
        os.rename(real, w.outside / "home")
        real.symlink_to(w.outside / "home")
    else:
        if where == ".config/df":
            (real / ".config").mkdir(mode=0o700)
            w.fake.set_owner(real / ".config", EXEC_UID)
        (real / where).symlink_to(w.lay.root)
    with pytest.raises(StepFailed, match="ссылка или не каталог"):
        setup_agent.install_machine_token(w.lay, w.source(TOKEN))
    assert not [e for e in w.fake.events if e[0] == "fchown"]


def test_open_own_never_follows_the_path_itself(w: W) -> None:
    with pytest.raises(ValueError, match="not a parent"):
        setup_agent.open_own(w.lay, w.home, EXEC_UID, system=w.home)
