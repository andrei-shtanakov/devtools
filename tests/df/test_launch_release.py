"""The release side of the role operations (stage 2a, Task 5)."""

from __future__ import annotations

import ast
import errno
import http.client
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from df import doctor, launch, launch_release, probe
from df.paths import MACHINE_TOKEN
from df.probe import ProbeStatus
from df.stage2_config import (
    ModelChange,
    Product,
    PublisherConfig,
    dump_model_change,
    dump_products,
    dump_publisher,
)
from tests.df.conftest import FakeLayout
from tests.df.launch_world import pin, put_record, record, trust_paths

REPO = Path(__file__).resolve().parents[2]

REL = "rel-a"
OK = ProbeStatus("годен", True)
STALE = ProbeStatus("не поддерживается: x", False)


class World:
    def __init__(self, fake: FakeLayout) -> None:
        self.fake = fake
        self.lay = fake.layout
        self.home = self.lay.root / "home"
        self.seen: list[tuple[str, object]] = []

    @property
    def file(self) -> str:
        return str(
            self.lay.release_dir(REL) / "src" / "devtools" / "df" / "launch_release.py"
        )

    def run(self, role: str, op: str, run_id: str = "-", release: str = REL) -> int:
        out = io.StringIO()
        code = launch_release.main(
            [role, op, release, run_id],
            layout=self.lay,
            file=self.file,
            home=self.home,
            out=out,
            transport=getattr(self, "transport", None),
        )
        self.out = out.getvalue()
        return code


@pytest.fixture()
def w(fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch) -> World:
    fake_layout.make_dirs()
    fake_layout.layout.runs_dir.mkdir(parents=True)
    trust_paths(monkeypatch)
    world = World(fake_layout)

    def pin_ready(layout: object, role: str, p: object) -> ProbeStatus:
        world.seen.append((role, p))
        return world.stale.get(role, OK)

    world.stale = {}  # type: ignore[attr-defined]
    monkeypatch.setattr(probe, "pin_ready", pin_ready)
    return world


# --- identity ---------------------------------------------------------------


def test_launch_release_refuses_foreign_release_id(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    assert w.run("df-exec", "check", release="rel-other") == 2
    assert w.out == ""
    assert "чужой release_id" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["df-exec", "check", REL],
        ["df-exec", "check", REL, "run-1"],
        ["df-exec", "run", REL, "-"],
        ["df-exec", "run", REL, "a/b"],
        ["df-publisher", "check", REL, "-"],
        ["df-review", "check-github", REL, "-"],
        ["df-exec", "review", REL, "run-1"],
    ],
)
def test_launch_release_refuses_bad_arguments(w: World, argv: list[str]) -> None:
    out = io.StringIO()
    code = launch_release.main(argv, layout=w.lay, file=w.file, out=out)
    assert code == 2 and out.getvalue() == ""


# --- check ------------------------------------------------------------------


def test_inherited_fds_seen_before_any_import(tmp_path: Path) -> None:
    """F3 (R14-11): a descriptor the caller left open is reported; the
    measurement is taken in a fresh interpreter at the module's first
    statements."""
    fd = os.open(tmp_path, os.O_RDONLY)
    try:
        code = "import df.launch_release as m; print(list(m.INHERITED_FDS))"
        done = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                f"import sys; sys.path.insert(0, {str(REPO)!r}); {code}",
            ],
            pass_fds=(fd,),
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
    finally:
        os.close(fd)
    assert fd in json.loads(done.stdout)


def test_inherited_fds_measured_before_other_imports() -> None:
    """The measurement precedes every import of the module but the three it
    needs (`os`, `resource`, `sys`): what those later imports open (Linux:
    OpenSSL's `/dev/urandom`) is the release's own, not the caller's."""
    tree = ast.parse(Path(launch_release.__file__).read_text())
    seen: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "INHERITED_FDS" for t in node.targets
        ):
            break
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == (
            "INHERITED_FDS"
        ):
            break
        if isinstance(node, ast.Import):
            seen += [a.name for a in node.names]
        if isinstance(node, ast.ImportFrom):
            seen.append(str(node.module))
    else:
        pytest.fail("INHERITED_FDS не присваивается на верхнем уровне")
    assert sorted(seen) == ["__future__", "os", "resource", "sys"]


def test_check_report(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "environ", {"HOME": "/h", "TZ": "UTC"})
    r, wfd = os.pipe()
    try:
        assert w.run("df-exec", "check") == 0
        first = json.loads(w.out)
        assert w.run("df-exec", "check") == 0
        second = json.loads(w.out)
    finally:
        os.close(r)
        os.close(wfd)
    assert first["nonce"] != second["nonce"] and len(first["nonce"]) == 32
    assert first["uid"] == os.getuid() and first["gid"] == os.getgid()
    assert first["euid"] == os.geteuid()
    assert first["env"] == ["HOME", "TZ"]
    assert first["cwd"] == os.getcwd()
    # opened after the module's first statement: not inherited (F3)
    assert first["fds"] == list(launch_release.INHERITED_FDS)
    assert r not in first["fds"] and wfd not in first["fds"]
    assert set(first["denied"]) >= {
        str(w.lay.secrets_dir),
        str(w.lay.publisher_dir),
        str(w.lay.role_home("df-review")),
    }
    # nothing of the sandbox exists here: opening each fails with ENOENT
    for path in (w.lay.secrets_dir, w.lay.publisher_dir, w.lay.role_home("df-exec")):
        assert first["denied"][str(path)] == errno.ENOENT


def test_check_report_records_an_opened_foreign_path(w: World) -> None:
    w.lay.secrets_dir.mkdir(parents=True)
    report = launch_release.check_report(w.lay)
    assert report["denied"][str(w.lay.secrets_dir)] == 0  # type: ignore[index]


# --- check-github -----------------------------------------------------------


def put_products(w: World, *users: str) -> None:
    rows = {
        100 + i: Product(100 + i, f"o/p{i}", "fork", 1, 2, user, "/c")
        for i, user in enumerate(users)
    }
    w.lay.etc.mkdir(parents=True, exist_ok=True)
    (w.lay.etc / "products.toml").write_text(dump_products(rows))


def put_token(w: World, text: str = "ghp_secret\n", mode: int = 0o600) -> Path:
    path = w.home.joinpath(*MACHINE_TOKEN)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)
    return path


class Transport:
    def __init__(self, login: str = "bot", fail: int | None = None) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.login = login
        self.fail = fail

    def __call__(self, url: str, headers) -> tuple[int, bytes]:  # noqa: ANN001
        self.calls.append((url, dict(headers)))
        if self.fail is not None:
            return self.fail, b"{}"
        if url.endswith("/user"):
            return 200, json.dumps({"login": self.login}).encode()
        body = {"permissions": {"admin": False, "push": True, "pull": True}}
        return 200, json.dumps(body).encode()


def test_check_github_reports_login_and_permissions(w: World) -> None:
    put_products(w, "bot", "bot")
    put_token(w)
    w.transport = Transport()  # type: ignore[attr-defined]
    assert w.run("df-exec", "check-github") == 0
    report = json.loads(w.out)
    assert report["login"] == "bot"
    assert set(report["products"]) == {"100", "101"}
    prod = report["products"]["100"]
    assert prod == {
        "full_name": "o/p0",
        "permissions": {"admin": False, "push": True, "pull": True},
        "mismatch": False,
    }
    urls = [u for u, _h in w.transport.calls]  # type: ignore[attr-defined]
    assert urls == [
        "https://api.github.com/user",
        "https://api.github.com/repos/o/p0",
        "https://api.github.com/repos/o/p1",
    ]
    headers = w.transport.calls[0][1]  # type: ignore[attr-defined]
    assert headers["Authorization"] == "Bearer ghp_secret"
    assert "ghp_secret" not in w.out


def test_check_github_mismatch_exits_1(w: World) -> None:
    put_products(w, "bot", "someone-else")
    put_token(w)
    w.transport = Transport("bot")  # type: ignore[attr-defined]
    assert w.run("df-exec", "check-github") == 1
    report = json.loads(w.out)
    assert report["products"]["100"]["mismatch"] is False
    assert report["products"]["101"]["mismatch"] is True


def test_check_github_uses_api_base_of_publisher_toml(w: World) -> None:
    put_products(w, "bot")
    put_token(w)
    w.lay.etc.mkdir(parents=True, exist_ok=True)
    cfg = PublisherConfig({}, "http://127.0.0.1:8123")
    (w.lay.etc / "publisher.toml").write_text(dump_publisher(cfg))
    w.transport = Transport()  # type: ignore[attr-defined]
    assert w.run("df-exec", "check-github") == 0
    assert w.transport.calls[0][0] == "http://127.0.0.1:8123/user"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "why", ["missing", "mode", "empty", "spaces", "link", "dir", "owner"]
)
def test_check_github_bad_token_makes_no_request(
    w: World, capsys: pytest.CaptureFixture[str], why: str
) -> None:
    put_products(w, "bot")
    if why == "mode":
        put_token(w, mode=0o640)
    elif why == "empty":
        put_token(w, "  \n")
    elif why == "spaces":
        put_token(w, "two words")
    elif why == "link":
        real = put_token(w)
        link = real.with_name("link")
        link.symlink_to(real)
        real.unlink()
        real.symlink_to(link)
    elif why == "owner":
        w.fake.set_owner(put_token(w), os.getuid() + 5)
    elif why == "dir":
        w.home.joinpath(*MACHINE_TOKEN).mkdir(parents=True)
    transport = Transport()
    w.transport = transport  # type: ignore[attr-defined]
    assert w.run("df-exec", "check-github") == 2
    assert transport.calls == [] and w.out == ""
    assert "токен" in capsys.readouterr().err


@pytest.mark.parametrize("status", [401, 403, 404, 301, 500])
def test_check_github_http_error_refuses(
    w: World, capsys: pytest.CaptureFixture[str], status: int
) -> None:
    put_products(w, "bot")
    put_token(w)
    w.transport = Transport(fail=status)  # type: ignore[attr-defined]
    assert w.run("df-exec", "check-github") == 2
    err = capsys.readouterr().err
    assert f"HTTP {status}" in err and "ghp_secret" not in err


def test_check_github_network_error_refuses(w: World) -> None:
    put_products(w, "bot")
    put_token(w)

    def down(url: str, headers) -> tuple[int, bytes]:  # noqa: ANN001
        raise OSError("down")

    w.transport = down  # type: ignore[attr-defined]
    assert w.run("df-exec", "check-github") == 2


def test_check_github_refuses_a_hostile_full_name(w: World) -> None:
    rows = {1: Product(1, "o/p?x=1", "fork", 1, 2, "bot", "/c")}
    w.lay.etc.mkdir(parents=True, exist_ok=True)
    (w.lay.etc / "products.toml").write_text(dump_products(rows))
    put_token(w)
    w.transport = Transport()  # type: ignore[attr-defined]
    assert w.run("df-exec", "check-github") == 2


def test_urllib_transport_never_follows_a_redirect() -> None:
    handler = launch_release._NoRedirect()  # noqa: SLF001
    assert handler.redirect_request() is None


# --- run and review ---------------------------------------------------------


def test_run_uses_record_pins_not_current_config(w: World) -> None:
    put_record(w.fake, record("run-1", REL, author=pin("m-record")))
    w.lay.etc.mkdir(parents=True, exist_ok=True)
    (w.lay.etc / "models.toml").write_text("changed after registration")
    assert w.run("df-exec", "run", "run-1") == 2
    assert dict(w.seen)["author"] == pin("m-record")
    assert not w.lay.runs_dir.joinpath("run-1.events.jsonl").exists()


def test_run_two_records_same_release_distinct(w: World) -> None:
    put_record(w.fake, record("run-1", REL, author=pin("one")))
    put_record(w.fake, record("run-2", REL, author=pin("two")))
    w.run("df-exec", "run", "run-1")
    w.run("df-exec", "run", "run-2")
    assert [p for r, p in w.seen if r == "author"] == [pin("one"), pin("two")]


def put_events(w: World, *lines: str) -> None:
    path = w.lay.runs_dir / "run-1.events.jsonl"
    path.write_text("".join(f"{line}\n" for line in lines))
    path.chmod(0o644)


def change(before: object, after: object, role: str = "author") -> str:
    return dump_model_change(ModelChange("run-1", role, before, after, 3))  # type: ignore[arg-type]


def test_run_resume_accepted_change_applies(w: World) -> None:
    put_record(w.fake, record("run-1", REL, author=pin("one"), interview=pin("i")))
    put_events(w, change(pin("one"), pin("two")), change(pin("two"), pin("three")))
    assert w.run("df-exec", "run", "run-1") == 2
    assert dict(w.seen) == {"interview": pin("i"), "author": pin("three")}


@pytest.mark.parametrize("bad", ["not json", "", "{}", '{"event":"x"}', "[]"])
def test_run_malformed_event_line_refuses(
    w: World, capsys: pytest.CaptureFixture[str], bad: str
) -> None:
    put_record(w.fake, record("run-1", REL, author=pin("one"), interview=pin("i")))
    put_events(w, change(pin("one"), pin("two")), bad)
    assert w.run("df-exec", "run", "run-1") == 2
    assert w.seen == [] and "этап 3" not in capsys.readouterr().err


def test_run_unreadable_events_refuse(w: World) -> None:
    put_record(w.fake, record("run-1", REL, author=pin("one"), interview=pin("i")))
    put_events(w, change(pin("one"), pin("two")))
    w.fake.set_owner(w.lay.runs_dir / "run-1.events.jsonl", os.getuid() + 9)
    assert w.run("df-exec", "run", "run-1") == 2
    assert w.seen == []


def test_run_record_of_another_release_refuses(w: World) -> None:
    put_record(w.fake, record("run-1", "rel-other"))
    assert w.run("df-exec", "run", "run-1") == 2
    assert w.seen == []


def test_run_missing_pin_refuses(w: World) -> None:
    put_record(w.fake, record("run-1", REL, interview=pin("i")))
    assert w.run("df-exec", "run", "run-1") == 2


def test_run_without_a_stale_pin_still_stops_at_stage_3(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    put_record(w.fake, record("run-1", REL, interview=pin("i"), author=pin("a")))
    assert w.run("df-exec", "run", "run-1") == 2
    assert "конвейер — этап 3" in capsys.readouterr().err


def test_review_checks_only_df_review_roles(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    put_record(w.fake, record("run-1", REL))
    w.stale = {"reviewer": STALE}  # type: ignore[attr-defined]
    assert w.run("df-exec", "run", "run-1") == 2
    err = capsys.readouterr().err
    assert "этап 3" in err and "reviewer" not in err  # `run` does not see it
    assert {r for r, _p in w.seen} == {"interview", "author"}
    w.seen.clear()
    assert w.run("df-review", "review", "run-1") == 2
    err = capsys.readouterr().err
    assert "reviewer: не поддерживается" in err and "этап 3" not in err
    assert (
        f"Refused: {launch_release.NO_MODEL_ACCESS}: reviewer: не поддерживается" in err
    )
    assert {r for r, _p in w.seen} == {
        "reviewer",
        "executor-review",
        "edge-check",
        "judge",
    }


def test_main_run_op_table_matches_launch() -> None:
    assert launch.ROLE_OPS["df-review"] == {"check", "review"}


def test_run_dangling_events_link_is_not_absence(w: World) -> None:
    put_record(w.fake, record("run-1", REL))
    (w.lay.runs_dir / "run-1.events.jsonl").symlink_to(w.lay.root / "nowhere")
    assert w.run("df-exec", "run", "run-1") == 2
    assert w.seen == []


def test_run_events_on_an_untrusted_path_refuse(
    w: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    put_record(w.fake, record("run-1", REL))
    put_events(w, change(pin(), pin("x")))
    monkeypatch.setattr(
        launch.plat, "trusted_path_problems", lambda p: [f"{p}: untrusted"]
    )
    assert w.run("df-exec", "run", "run-1") == 2
    assert w.seen == []


def test_run_hard_linked_events_refuse(w: World) -> None:
    put_record(w.fake, record("run-1", REL))
    put_events(w, change(pin(), pin("x")))
    os.link(w.lay.runs_dir / "run-1.events.jsonl", w.lay.runs_dir / "twin")
    assert w.run("df-exec", "run", "run-1") == 2
    assert w.seen == []


def test_bootstrap_imports_df_from_the_release_tree(tmp_path: Path) -> None:
    """I1: `python -I -B <rel>/src/devtools/df/launch_release.py` has only the
    bootstrap line to find `df`; run it for real in a fixture release."""
    import shutil
    import subprocess
    import sys

    repo = Path(launch.__file__).parents[1]
    tree = tmp_path / "rel-a" / "src" / "devtools"
    shutil.copytree(
        repo / "df", tree / "df", ignore=shutil.ignore_patterns("__pycache__")
    )
    script = tree / "df" / "launch_release.py"
    env = {"PATH": "/usr/bin:/bin"}
    probe = (
        "import runpy, sys\n"
        "runpy.run_path(sys.argv[1], run_name='bootstrap')\n"
        "import df\n"
        "print(df.__file__)\n"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-B", "-c", probe, str(script)],
        cwd="/", env=env, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert out.returncode == 0, out.stderr
    assert Path(out.stdout.strip()).is_relative_to(tree)
    run = subprocess.run(
        [sys.executable, "-I", "-B", str(script), "df-exec", "check", "rel-a", "-"],
        cwd="/", env=env, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["cwd"] == "/"


@pytest.mark.parametrize(
    "error",
    [
        http.client.IncompleteRead(b"x"),
        http.client.BadStatusLine("x"),
        http.client.LineTooLong("header line"),
    ],
)
def test_check_github_http_exception_is_exit_2(
    w: World, capsys: pytest.CaptureFixture[str], error: Exception
) -> None:
    """Exit 1 means only «mismatch»: a broken HTTP exchange is a refusal."""
    put_products(w, "bot")
    put_token(w)

    def broken(url: str, headers) -> tuple[int, bytes]:  # noqa: ANN001
        raise error

    w.transport = broken  # type: ignore[attr-defined]
    assert w.run("df-exec", "check-github") == 2
    err = capsys.readouterr().err
    assert w.out == "" and "Traceback" not in err and "данные недоступны" in err


def test_stale_pins_one_prefix_roles_joined(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """F1 (R14-9): «доступ к моделям не подтверждён: <роль>: <причина>»,
    the prefix once and every stale role after it, in `ROLES` order; the
    same words as doctor's `models.access`."""
    put_record(w.fake, record("run-1", REL))
    w.stale = {"interview": STALE, "author": STALE}  # type: ignore[attr-defined]
    assert w.run("df-exec", "run", "run-1") == 2
    err = capsys.readouterr().err.strip().splitlines()[-1]
    assert err == (
        f"Refused: {launch_release.NO_MODEL_ACCESS}: interview: не поддерживается: x; "
        "author: не поддерживается: x"
    )
    assert launch_release.NO_MODEL_ACCESS == doctor.NO_MODEL_ACCESS
