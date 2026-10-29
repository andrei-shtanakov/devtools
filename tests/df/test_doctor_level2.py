"""Task 13: `darkfactory doctor --product P`, level 2 «роли» (spec 2.4, 8).

The world is `test_doctor`'s installed world (level 1 reached) with the
roles, P in `products.toml` and every model role's choice. Every external call of level 2 is a seam answering as the role's or the
publisher's real operation would: `check` reports (the shape of
`launch_release.check_report`), `check-github` JSON and refusals (the
shape `launch_release.main` prints), `self-check` and `perimeter-status`
response lines (`request.dump_response` of a `SelfCheckResult`), parsed by
`doctor` with the production parsers. Real `sudo` runs only in Task 14.
"""

from __future__ import annotations

import dataclasses
import errno
import json
import os
from pathlib import Path

import pytest

from df import build as build_mod
from df import (
    cli,
    doctor,
    fsutil,
    journal,
    launch,
    libexec,
    models,
    probe,
    setup_agent,
    state,
    sudoers,
)
from df import platform as plat
from df.config import Account, ConfigError, dump_accounts, load_accounts
from df.doctor import Check, level1_checks
from df.journal import Stable, Transitional
from df.models import ROLES
from df.paths import Layout
from df.publisher import request
from df.publisher.github import Installation, PerimeterStats, SelfCheckResult
from df.publisher.request import Response
from df.stage2_config import Product, dump_products
from tests.df.conftest import FakeLayout
from tests.df.install_world import World, installed, make_world
from tests.df.route_world import BOOT
from tests.df.test_doctor import SUDO, audit_opens, by_id, run_doctor, snap


@pytest.fixture(autouse=True)
def _boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fsutil, "boot_id", lambda: BOOT)


@pytest.fixture()
def fake_installed(monkeypatch: pytest.MonkeyPatch, fake_layout: FakeLayout) -> World:
    """`test_doctor`'s world: a committed first install; the current user
    plays root for `doctor`. Durability is not what level 2 judges, so the
    flushes are skipped (about half of the install's time on macOS)."""
    monkeypatch.setattr(fsutil, "sync_file", lambda _fd: None)
    monkeypatch.setattr(fsutil, "_sync_dir_fd", lambda _fd: fsutil.SyncNote.OK)
    w = make_world(fake_layout, monkeypatch)
    installed(w)
    monkeypatch.setattr(doctor, "ROOT_UID", os.geteuid())
    return w


PRODUCT = 4711
APP_ID = 31
INSTALLATION = 77
SIBLING = 4712
SIBLING_APP = 32
SIBLING_INSTALLATION = 78
BOT = "df-bot"
OPERATOR = "alice"
UIDS = {"df-exec": 71001, "df-review": 71002, "df-publisher": 71003}
LEVEL2_IDS = [
    "roles.users",
    "roles.sudoers",
    "roles.libexec",
    "roles.isolation",
    "publisher.answers",
    "publisher.foreign",
    "product.registered",
    "github.machine_user",
    "github.app",
    "models.access",
]
FRESH = "подтверждён 2026-10-29T00:00:00+00:00"


def answer(outcome: str, reason: str | None, result: dict[str, object]) -> bytes:
    """A publisher response line as `df-publish` prints it."""
    resp = Response(outcome, reason, result)  # type: ignore[arg-type]
    return (request.dump_response(resp) + "\n").encode()


def self_check_answer(
    *,
    problems: tuple[str, ...] = (),
    repos: tuple[int, ...] = (PRODUCT,),
    rights: dict[str, str] | None = None,
    installation: int = INSTALLATION,
    stats: PerimeterStats | None = PerimeterStats(12, 1 << 20, 1 << 30),
) -> bytes:
    perms = {"contents": "write", "pull_requests": "write", "metadata": "read"}
    inst = Installation(installation, "acme", repos, rights or perms)
    return answer("done", None, SelfCheckResult((inst,), problems, stats).to_json())


def github_report(
    *, login: str = BOT, push: bool = True, admin: bool = False, code: int = 0
) -> plat.RunResult:
    row = {
        "full_name": "acme/product",
        "permissions": {"admin": admin, "push": push, "pull": True},
        "mismatch": login != BOT,
    }
    report = {"login": login, "products": {str(PRODUCT): row}}
    return plat.RunResult(code, json.dumps(report).encode() + b"\n", b"", True)


def refused(text: str) -> plat.RunResult:
    """`check-github` refused: `launch_release.main` prints the reason."""
    return plat.RunResult(2, b"", f"Refused: {text}\n".encode(), True)


@dataclasses.dataclass
class L2:
    """The level-2 world: the installed world, roles, the product, every
    seam's next answer, and the calls the seams saw."""

    w: World
    gh_dir: Path
    reports: dict[str, dict[str, object] | plat.RunResult]
    gh: dict[str, plat.RunResult]
    status: dict[str, bytes | plat.RunResult]
    merge: plat.RunResult
    github: plat.RunResult
    self_check: bytes | Exception
    probe_current: bool = True
    calls: list[tuple[object, ...]] = dataclasses.field(default_factory=list)

    @property
    def lay(self) -> Layout:
        return self.w.lay

    def level2(self, operator: str | None = OPERATOR) -> dict[str, Check]:
        return by_id(level2_of(self, operator=operator).checks)

    def doctor(self, argv: list[str]) -> int:
        return run_doctor(self.w, ["--product", str(PRODUCT), *argv])

    def write_products(self, profile: str = "write", *, sibling: bool = False) -> None:
        """P (and, with `sibling`, Q on another App) in `products.toml`."""
        rows = {PRODUCT: (profile, APP_ID, INSTALLATION)}
        if sibling:
            rows[SIBLING] = ("write", SIBLING_APP, SIBLING_INSTALLATION)
        products = {
            rid: Product(rid, f"acme/p{rid}", prof, app, inst, BOT, "/srv/p")  # type: ignore[arg-type]
            for rid, (prof, app, inst) in rows.items()
        }
        fsutil.write_atomic(self.lay.etc / "products.toml", dump_products(products))


def level2_of(
    world: L2, *, euid: int | None = None, operator: str | None = OPERATOR
) -> doctor.Level2:
    """`doctor.level2` over this world's level 1, as `doctor_main` runs it."""
    s = snap(world.w)
    uid = os.geteuid() if euid is None else euid
    level1 = level1_checks(s, world.lay, euid=uid)
    return doctor.level2(
        s, world.lay, PRODUCT, euid=uid, operator=operator, level1=level1
    )


def green_report(lay: Layout, role: str) -> dict[str, object]:
    """What `launch_release.check_report` prints for a well-isolated role."""
    foreign = [lay.secrets_dir, *(lay.role_home(r) for r in UIDS if r != role)]
    return {
        "nonce": "0" * 32,
        "uid": UIDS[role],
        "euid": UIDS[role],
        "gid": UIDS[role],
        "groups": [UIDS[role]],
        "env": ["HOME", "LANG", "PATH", "TZ"],
        "cwd": str(lay.role_home(role) / "work"),
        "fds": [],
        "denied": {str(p): errno.EACCES for p in foreign},
    }


def _no_sudo(monkeypatch: pytest.MonkeyPatch) -> None:
    """No call of level 2 may reach a real `sudo` (every one is a seam)."""
    real = plat.run_command

    def run_command(argv: list[str], **kwargs: object) -> plat.RunResult:
        assert argv[0] != launch.SUDO or argv[1:3] == ["-n", "-l"], argv
        return real(argv, **kwargs)  # type: ignore[arg-type]

    def run_with_input(argv: list[str], *_a: object, **_k: object) -> plat.RunResult:
        raise AssertionError(argv)

    monkeypatch.setattr(plat, "run_command", run_command)
    monkeypatch.setattr(plat, "run_with_input", run_with_input)


def seed_models(w: World) -> None:
    """Every model role's common choice, in one bracketed write (what `models
    set` leaves, without a writer run per role)."""
    cfg = models.EMPTY
    for role in ROLES:
        cfg = models.with_choice(cfg, role, models.ModelChoice("claude", "m", None, {}))
    journal.generation_begin(w.lay)
    fsutil.write_atomic(w.lay.etc / "models.toml", models.dump_models(cfg))
    journal.generation_end(w.lay)


@pytest.fixture()
def l2(fake_installed: World, tmp_path: Path) -> L2:
    """Level 1 reached; roles ready, P registered (`write`), every model role
    set, and every level-2 operation answering green."""
    w = fake_installed
    mp = w.mp
    _no_sudo(mp)
    accounts = load_accounts(w.lay.accounts)
    for name, uid in UIDS.items():
        accounts[name] = Account(name, uid, uid, "ready", "2026-10-29T00:00:00Z")
    fsutil.write_atomic(w.lay.accounts, dump_accounts(accounts))
    seed_models(w)
    sudoers_file = tmp_path / "sudoers.d-df"
    sudoers_file.write_text(sudoers.render(w.lay, OPERATOR))
    sudoers_file.chmod(0o440)
    w.lay.libexec.mkdir(mode=0o755, exist_ok=True)
    w.lay.run_start.parent.mkdir(mode=0o755, exist_ok=True)
    libexec.install_wrappers(w.lay, libexec.admin_python_of(w.lay, snap(w)))
    gh_dir = tmp_path / "operator-home" / ".config" / "gh"
    gh_dir.mkdir(parents=True)
    world = L2(
        w,
        gh_dir,
        {role: green_report(w.lay, role) for role in doctor.MODEL_ROLES},
        dict.fromkeys(doctor.GH_PROBES, plat.RunResult(1, b"", b"", True)),
        {r: answer("refused", "run_unknown", {}) for r in doctor.MODEL_ROLES},
        plat.RunResult(1, b"", b"Sorry, user df-review is not allowed\n", True),
        github_report(),
        self_check_answer(),
    )
    world.write_products()
    mp.setattr(plat, "verify_service_user", lambda _l, _n: [])
    mp.setattr(sudoers, "SUDOERS_FILE", sudoers_file)
    mp.setattr(sudoers, "validate", lambda _t: [])
    mp.setattr(sudoers, "policy_problems", lambda *_a, **_k: [])
    mp.setattr(sudoers, "io_log_problems", lambda _l: [])
    _seams(world, mp)
    return world


def _seams(world: L2, mp: pytest.MonkeyPatch) -> None:
    def role_check(layout: Layout, role: str) -> plat.RunResult:
        world.calls.append(("check", role))
        report = world.reports[role]
        if isinstance(report, plat.RunResult):
            return report
        return plat.RunResult(0, json.dumps(report).encode() + b"\n", b"", True)

    def role_access(role: str, flag: str, path: Path) -> plat.RunResult:
        world.calls.append(("test", role, flag, path))
        return world.gh[flag]

    def role_perimeter_status(layout: Layout, role: str, data: bytes) -> plat.RunResult:
        world.calls.append(("perimeter-status", role, data))
        out = world.status[role]
        if isinstance(out, plat.RunResult):
            return out
        return plat.RunResult(2, out, b"", True)

    def review_merge(layout: Layout) -> plat.RunResult:
        world.calls.append(("merge",))
        return world.merge

    def self_check(layout: Layout, *, purpose: str = "") -> bytes:
        world.calls.append(("self-check", purpose))
        if isinstance(world.self_check, Exception):
            raise world.self_check
        return world.self_check

    def exec_check_github(layout: Layout) -> plat.RunResult:
        world.calls.append(("check-github",))
        return world.github

    def current_status(
        layout: Layout, role: str, choice: object, install: object, **_k: object
    ) -> tuple[probe.ProbeStatus, None]:
        if world.probe_current:
            return probe.ProbeStatus(FRESH, True), None
        return probe.ProbeStatus("не подтверждён: нет пробы", False), None

    mp.setattr(doctor, "role_check", role_check)
    mp.setattr(doctor, "role_access", role_access)
    mp.setattr(doctor, "role_perimeter_status", role_perimeter_status)
    mp.setattr(doctor, "review_merge", review_merge)
    mp.setattr(doctor, "operator_gh", lambda _o: world.gh_dir)
    mp.setattr(setup_agent, "publisher_self_check", self_check)
    mp.setattr(setup_agent, "exec_check_github", exec_check_github)
    mp.setattr(probe, "current_status", current_status)


def status_of(world: L2, check_id: str, **kwargs: str | None) -> tuple[str, str]:
    c = world.level2(**kwargs)[check_id]  # type: ignore[arg-type]
    return c.status, c.reason


# --- the positive control and the command ---


def test_level2_reached_on_a_green_set(
    l2: L2, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control of the mechanism: every operation answers green
    (a fixture probe for every model role) -> level 2 reached, exit 0."""
    monkeypatch.setenv("SUDO_USER", OPERATOR)
    checks = l2.level2()
    assert list(checks) == LEVEL2_IDS
    assert {k: c.status for k, c in checks.items()} == dict.fromkeys(LEVEL2_IDS, "ok")
    assert checks["github.app"].reason == "источники доверия: не проверено (этап 3)"
    assert checks["product.registered"].reason == "профиль write"
    capsys.readouterr()
    assert l2.doctor([]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[:5] == [
        "уровень 1: достигнут",
        "уровень 2: достигнут",
        "уровень 3: не проверено (этап 3)",
        "уровень 4: не проверено (этап 4)",
        "профиль: write",
    ]
    assert any(line.startswith("  perimeter.journal: ok: событий 12") for line in out)


def test_level2_json_carries_everything(
    l2: L2, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUDO_USER", OPERATOR)
    l2.github = github_report(login="someone-else")
    capsys.readouterr()
    assert l2.doctor(["--json"]) == 2
    data = json.loads(capsys.readouterr().out)
    assert data["level1"] == "reached"
    assert data["level2"] == "not reached"
    assert (data["product"], data["profile"]) == (PRODUCT, "write")
    assert [c["id"] for c in data["level2_checks"]] == LEVEL2_IDS
    assert data["perimeter"]["id"] == "perimeter.journal"
    assert (data["level3"], data["level4"]) == (
        "not checked (stage 3)",
        "not checked (stage 4)",
    )
    machine = next(c for c in data["level2_checks"] if c["id"] == "github.machine_user")
    assert machine["status"] == "нарушено"
    assert any("someone-else" in p for p in machine["problems"])


def test_doctor_without_product_computes_no_level2(
    l2: L2, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    assert run_doctor(l2.w, []) == 0
    out = capsys.readouterr().out
    assert "уровень 2" not in out.replace("модели (уровень 2", "")
    assert l2.calls == []
    assert run_doctor(l2.w, ["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert "level2" not in data and "level2_checks" not in data
    assert l2.calls == []


def test_probe_models_with_product_still_refused(
    fake_installed: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--probe-models --product` keeps its refusal before any read (R64)."""
    fake_installed.mp.setattr(cli, "ROOT_UID", os.geteuid())
    raw = state.read_raw(fake_installed.lay)
    with audit_opens() as opens:
        code = run_doctor(
            fake_installed, ["--probe-models", "--product", "42"], Stable(raw, 2)
        )
    assert code == 2
    assert "реестр продуктов не поддерживается до этапа 3" in capsys.readouterr().err
    assert [p for p in opens if p.startswith(str(fake_installed.lay.root))] == []


def test_level2_not_reached_names_first_blocking(
    l2: L2, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUDO_USER", OPERATOR)
    l2.write_products("fork")
    capsys.readouterr()
    assert l2.doctor([]) == 2
    out = capsys.readouterr().out.splitlines()
    assert out[1].startswith("уровень 2: не достигнут — github.machine_user: нарушено")
    assert out[4] == "профиль: fork"


def test_level1_not_reached_blocks_level2(
    l2: L2, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUDO_USER", OPERATOR)
    monkeypatch.setattr(build_mod, "verify_build_user", lambda _l: ["uid 0"])
    capsys.readouterr()
    assert l2.doctor([]) == 2
    out = capsys.readouterr().out.splitlines()
    assert out[1] == "уровень 2: не достигнут — уровень 1 не достигнут"


def test_level1_not_reached_asks_nothing(
    l2: L2, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R13-11 (as R72): over a broken level 1 -- here a tampered release, whose
    code `df-launch` would run as the roles -- level 2 calls no role and no
    publisher: every check and the line are «не проверено»."""
    rid = os.readlink(l2.lay.current).removeprefix("releases/")
    target = l2.lay.release_dir(rid) / "python" / ".lock"
    target.write_text("changed")
    second = level2_of(l2)
    assert [(c.id, c.status, c.reason) for c in second.checks] == [
        (i, "не проверено", "уровень 1 не достигнут") for i in LEVEL2_IDS
    ]
    assert (second.perimeter.status, second.perimeter.reason) == (
        "не проверено",
        "уровень 1 не достигнут",
    )
    assert l2.calls == []


def test_non_root_level2_needs_sudo_and_asks_nothing(l2: L2) -> None:
    """R13-3: without root every level-2 check is «нужен sudo»."""
    second = level2_of(l2, euid=os.geteuid() + 1)
    assert [(c.status, c.reason) for c in second.checks] == [
        ("данные недоступны", SUDO)
    ] * len(LEVEL2_IDS)
    assert (second.perimeter.status, second.perimeter.reason) == (
        "данные недоступны",
        SUDO,
    )
    assert l2.calls == []
    assert not doctor.level2_reached([], second.checks)


# --- 1-3 ---


def test_users_broken(l2: L2, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        plat,
        "verify_service_user",
        lambda _l, n: ["uid 0"] if n == "df-review" else [],
    )
    assert status_of(l2, "roles.users") == ("нарушено", "df-review: uid 0")


def test_sudoers_without_operator_unavailable(l2: L2) -> None:
    """R13-2: `SUDO_USER` unset -> «данные недоступны», no guess."""
    checks = l2.level2(operator=None)
    assert (checks["roles.sudoers"].status, checks["roles.sudoers"].reason) == (
        "данные недоступны",
        "оператор неизвестен (нет --operator, SUDO_USER не задан)",
    )
    assert checks["roles.isolation"].status == "данные недоступны"
    assert "SUDO_USER" in checks["roles.isolation"].reason


def test_sudoers_installed_text_differs_broken(l2: L2) -> None:
    path = sudoers.SUDOERS_FILE
    path.chmod(0o640)
    path.write_text(path.read_text() + "ALL ALL=(ALL) NOPASSWD: ALL\n")
    path.chmod(0o440)
    status, reason = status_of(l2, "roles.sudoers")
    assert status == "нарушено"
    assert "текст не совпадает" in reason


def test_sudoers_validate_and_policy_problems_broken(
    l2: L2, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def policy(name: str, *, layout: Layout, operator: str) -> list[str]:
        seen.append(name)
        return [f"sudo -l -U {name}: лишнее правило"] if name == "df-review" else []

    monkeypatch.setattr(sudoers, "validate", lambda _t: ["parse error"])
    monkeypatch.setattr(sudoers, "policy_problems", policy)
    monkeypatch.setattr(sudoers, "io_log_problems", lambda _l: ["log_output"])
    c = l2.level2()["roles.sudoers"]
    assert c.status == "нарушено"
    assert seen == ["df-exec", "df-review", "df-publisher", "df-build", OPERATOR]
    assert len(c.problems) == 3
    assert "parse error" in c.problems[0]


def test_libexec_broken(l2: L2) -> None:
    wrapper = l2.lay.libexec / "df-launch"
    wrapper.write_text("#!/bin/sh\nexec /bin/sh\n")
    status, reason = status_of(l2, "roles.libexec")
    assert status == "нарушено" and "текст не совпадает" in reason


# --- 4: isolation ---


@pytest.mark.parametrize(
    "role,key,value,needle",
    [
        ("df-exec", "uid", 0, "uid 0"),
        ("df-review", "gid", UIDS["df-exec"], "gid"),
        ("df-exec", "fds", [3], "дескрипторы"),
        ("df-review", "env", ["HOME", "SSH_AUTH_SOCK"], "SSH_AUTH_SOCK"),
    ],
)
def test_isolation_report_broken(
    l2: L2, role: str, key: str, value: object, needle: str
) -> None:
    report = green_report(l2.lay, role)
    report[key] = value
    l2.reports[role] = report
    status, reason = status_of(l2, "roles.isolation")
    assert status == "нарушено" and needle in reason


@pytest.mark.parametrize(
    "role,which,got",
    [
        ("df-exec", "secrets", 0),
        ("df-exec", "home", errno.ENOENT),
        ("df-review", "secrets", None),
        ("df-review", "home", errno.EPERM),
    ],
)
def test_isolation_denied_must_be_eacces(
    l2: L2, role: str, which: str, got: int | None
) -> None:
    """Each path of the role's list must be EACCES exactly (R13-1): opened,
    another errno or absent from the report is broken."""
    other = "df-review" if role == "df-exec" else "df-exec"
    path = l2.lay.secrets_dir if which == "secrets" else l2.lay.role_home(other)
    report = green_report(l2.lay, role)
    denied = dict(report["denied"])  # type: ignore[call-overload]
    if got is None:
        del denied[str(path)]
    else:
        denied[str(path)] = got
    report["denied"] = denied
    l2.reports[role] = report
    status, reason = status_of(l2, "roles.isolation")
    assert status == "нарушено" and str(path) in reason


def test_isolation_check_not_started_unavailable(l2: L2) -> None:
    l2.reports["df-review"] = plat.RunResult(-1, b"", b"", False)
    assert status_of(l2, "roles.isolation") == (
        "данные недоступны",
        "df-review check: не стартовал",
    )


def test_isolation_check_refused_broken(l2: L2) -> None:
    l2.reports["df-exec"] = plat.RunResult(
        2, b"", b"StepFailed: launch: release_revoked\n", True
    )
    status, reason = status_of(l2, "roles.isolation")
    assert status == "нарушено" and "release_revoked" in reason


def test_isolation_gh_denied_asks_read_and_search(l2: L2) -> None:
    assert status_of(l2, "roles.isolation") == ("ok", "")
    asked = [c for c in l2.calls if c[0] == "test"]
    assert asked == [("test", "df-exec", f, l2.gh_dir) for f in ("-r", "-x")]


@pytest.mark.parametrize("flag", ["-r", "-x"])
def test_isolation_gh_readable_or_searchable_broken(l2: L2, flag: str) -> None:
    """m2: a `0711` directory fails `-r` but passes `-x` -- a file inside
    opens by name, so it is not a denial (negative twin of `-r` alone)."""
    l2.gh[flag] = plat.RunResult(0, b"", b"", True)
    assert status_of(l2, "roles.isolation") == (
        "нарушено",
        f"df-exec: test {flag} {l2.gh_dir} проходит",
    )


def test_isolation_gh_sudo_refusal_is_not_a_denial(l2: L2) -> None:
    """Negative twin: sudo's own refusal is exit 1 too, but it speaks; it
    must not pass as «df-exec cannot read»."""
    l2.gh["-x"] = plat.RunResult(1, b"", b"sudo: unknown user df-exec\n", True)
    status, reason = status_of(l2, "roles.isolation")
    assert status == "данные недоступны" and "unknown user" in reason


def test_isolation_gh_absent_is_ok_with_note(l2: L2) -> None:
    l2.gh_dir.rmdir()
    assert status_of(l2, "roles.isolation") == ("ok", f"нет {l2.gh_dir}")
    assert not any(c[0] == "test" for c in l2.calls)


def test_isolation_publisher_key_unusable_broken(l2: L2) -> None:
    l2.self_check = self_check_answer(problems=(f"app_key_unusable: {APP_ID}",))
    status, reason = status_of(l2, "roles.isolation")
    assert status == "нарушено"
    assert str(l2.lay.app_key(APP_ID)) in reason


def test_isolation_other_apps_key_is_not_ps(l2: L2) -> None:
    l2.self_check = self_check_answer(problems=("app_key_unusable: 999",))
    assert status_of(l2, "roles.isolation")[0] == "ok"


def test_isolation_publisher_silent_unavailable(l2: L2) -> None:
    l2.self_check = OSError("the call failed (code -1)")
    status, reason = status_of(l2, "roles.isolation")
    assert status == "данные недоступны" and reason.startswith("df-publisher:")


# --- 5-6: the publisher ---


def test_answers_fresh_install_ok(l2: L2) -> None:
    """r2 N3: no run at all -- `refused: run_unknown` from both roles is the
    publisher answering; the probe request is a valid perimeter-status."""
    assert status_of(l2, "publisher.answers") == ("ok", "")
    sent = [c for c in l2.calls if c[0] == "perimeter-status"]
    assert [c[1] for c in sent] == ["df-exec", "df-review"]
    for call in sent:
        data = call[2]
        assert isinstance(data, bytes)
        req = request.parse("perimeter-status", data)
        assert req.run_id == "doctor-probe"
        assert req.request_id.startswith("doctor-")


@pytest.mark.parametrize(
    "out,status",
    [
        (answer("refused", "sender_unknown", {}), "нарушено"),
        (answer("done", None, {"state": "clear"}), "нарушено"),
        (b"not json\n", "нарушено"),
        (plat.RunResult(1, b"", b"sudo: a password is required\n", True), "нарушено"),
        (plat.RunResult(-1, b"", b"", False), "данные недоступны"),
        (plat.RunResult(-9, b"", b"", True, timed_out=True), "данные недоступны"),
    ],
)
def test_answers_not_ok(l2: L2, out: bytes | plat.RunResult, status: str) -> None:
    l2.status["df-review"] = out
    assert status_of(l2, "publisher.answers")[0] == status


def test_foreign_refused_by_sudo_ok(l2: L2) -> None:
    assert status_of(l2, "publisher.foreign") == ("ok", "")
    assert ("merge",) in l2.calls


@pytest.mark.parametrize(
    "result",
    [
        plat.RunResult(2, answer("refused", "bad_request", {}), b"", True),
        plat.RunResult(0, b"", b"", True),
        plat.RunResult(1, b'{"x": 1}\n', b"", True),
    ],
)
def test_foreign_let_through_broken(l2: L2, result: plat.RunResult) -> None:
    """R13-7: any stdout or another exit code is the publisher reached."""
    l2.merge = result
    assert status_of(l2, "publisher.foreign")[0] == "нарушено"


def test_foreign_without_review_reaching_publisher_not_checked(l2: L2) -> None:
    """Negative twin: a sudo that cannot run as df-review at all also exits
    1 silently; without df-review's answer the refusal proves nothing."""
    l2.status["df-review"] = plat.RunResult(1, b"", b"sudo: unknown user\n", True)
    status, _reason = status_of(l2, "publisher.foreign")
    assert status == "не проверено"
    assert ("merge",) not in l2.calls


# --- 7-10: the product ---


def test_unregistered_product(l2: L2) -> None:
    """R13-4: 8-10 are «не проверено: продукт не зарегистрирован»."""
    (l2.lay.etc / "products.toml").unlink()
    checks = l2.level2()
    assert checks["product.registered"].status == "нарушено"
    assert str(PRODUCT) in checks["product.registered"].reason
    for name in ("github.machine_user", "github.app", "models.access"):
        assert (checks[name].status, checks[name].reason) == (
            "не проверено",
            "продукт не зарегистрирован",
        )
    assert list(checks) == LEVEL2_IDS
    assert ("check-github",) not in l2.calls


@pytest.mark.parametrize(
    "profile,push,admin,ok",
    [
        ("write", True, False, True),
        ("write", False, False, False),  # a fork-profile PAT on a write product
        ("write", True, True, False),
        ("fork", False, False, True),
        ("fork", True, False, False),
    ],
)
def test_machine_user_rights_by_profile(
    l2: L2, profile: str, push: bool, admin: bool, ok: bool
) -> None:
    l2.write_products(profile)
    l2.github = github_report(push=push, admin=admin)
    status, reason = status_of(l2, "github.machine_user")
    assert (status == "ok") == ok, reason


def test_machine_user_wrong_login_broken(l2: L2) -> None:
    l2.github = github_report(login="intruder", code=1)
    status, reason = status_of(l2, "github.machine_user")
    assert status == "нарушено" and "intruder" in reason


@pytest.mark.parametrize(
    "result,status,needle",
    [
        (refused("GET https://api.github.com/user: HTTP 401"), "нарушено", "отозван"),
        (
            refused("GET https://api.github.com/user: данные недоступны: URLError()"),
            "данные недоступны",
            "GitHub",
        ),
        (
            refused("GET https://api.github.com/user: HTTP 503"),
            "данные недоступны",
            "503",
        ),
        (refused("GET https://api.github.com/user: HTTP 403"), "нарушено", "403"),
        (refused("токен /h/.config/df/github-token: не читается"), "нарушено", "токен"),
        (plat.RunResult(-1, b"", b"", False), "данные недоступны", "не стартовал"),
    ],
)
def test_machine_user_refusals(
    l2: L2, result: plat.RunResult, status: str, needle: str
) -> None:
    l2.github = result
    got, reason = status_of(l2, "github.machine_user")
    assert got == status and needle in reason


@pytest.mark.parametrize(
    "kwargs,needle",
    [
        ({"repos": (1,)}, "без репозитория"),
        ({"installation": 5}, "нет установки"),
        ({"rights": {"contents": "read", "pull_requests": "write"}}, "contents"),
        ({"rights": {"contents": "write"}}, "pull_requests"),
        ({"problems": ("app_installation_outside_products: 9",)}, "outside"),
    ],
)
def test_app_not_ok(l2: L2, kwargs: dict[str, object], needle: str) -> None:
    l2.self_check = self_check_answer(**kwargs)  # type: ignore[arg-type]
    status, reason = status_of(l2, "github.app")
    assert status == "нарушено" and needle in reason


@pytest.mark.parametrize(
    "out,status",
    [
        (answer("unknown", "github_unavailable", {}), "данные недоступны"),
        (answer("refused", "openssl_not_configured", {}), "нарушено"),
        (answer("done", None, {"installations": []}), "нарушено"),
        (OSError("the call failed"), "данные недоступны"),
    ],
)
def test_app_self_check_failures(l2: L2, out: bytes | Exception, status: str) -> None:
    l2.self_check = out
    assert status_of(l2, "github.app")[0] == status


def test_self_check_asked_once_per_run(l2: L2) -> None:
    second = level2_of(l2)
    assert second.perimeter.status == "ok"
    assert [c for c in l2.calls if c[0] == "self-check"] == [("self-check", "doctor")]


def test_models_access_v1_not_confirmed(l2: L2) -> None:
    """Decision 9: in v1 no model role has a current probe -> level 2 not
    reached, «доступ к моделям не подтверждён»."""
    l2.probe_current = False
    status, reason = status_of(l2, "models.access")
    assert status == "не проверено"
    assert reason.startswith("доступ к моделям не подтверждён: interview:")


def test_models_access_unset_role(l2: L2) -> None:
    """A role without a choice for P is not confirmed either."""
    w = l2.w
    original = doctor.models.resolve

    def resolve(cfg: object, role: str, product: int | None) -> object:
        assert product == PRODUCT
        if role == "judge":
            raise ConfigError("judge: модель не задана")
        return original(cfg, role, product)  # type: ignore[arg-type]

    w.mp.setattr(doctor.models, "resolve", resolve)
    status, reason = status_of(l2, "models.access")
    assert status == "не проверено" and "judge: модель не задана" in reason


# --- the perimeter line ---


@pytest.mark.parametrize(
    "stats,status,needle",
    [
        (PerimeterStats(12, 3 << 20, 1 << 30), "ok", "событий 12, база 3 МиБ"),
        (PerimeterStats(9000, 1 << 20, 1 << 30), "ok", "приближается к пределу 10 000"),
        (PerimeterStats(10_000, 1 << 20, 1 << 30), "ok", "приближается"),
        (PerimeterStats(10_001, 1 << 20, 1 << 30), "нарушено", "отвечают unavailable"),
        (PerimeterStats(5, 1 << 20, 10 << 20), "ok", "свободно меньше 64 МиБ"),
        (None, "данные недоступны", "perimeter_unavailable: lost"),
    ],
)
def test_perimeter_line(
    l2: L2, stats: PerimeterStats | None, status: str, needle: str
) -> None:
    problems = ("perimeter_unavailable: lost",) if stats is None else ()
    l2.self_check = self_check_answer(stats=stats, problems=problems)
    second = level2_of(l2)
    line = second.perimeter
    assert line.status == status and needle in line.reason, line


def test_perimeter_line_normal_has_no_warning(l2: L2) -> None:
    second = level2_of(l2)
    assert "предупреждение" not in second.perimeter.reason


def test_perimeter_line_does_not_block_level2(l2: L2) -> None:
    """The line is operational, not a level-2 check (r4 N5)."""
    l2.self_check = self_check_answer(stats=PerimeterStats(10_001, 1, 1 << 30))
    second = level2_of(l2)
    assert second.perimeter.status == "нарушено"
    level1 = level1_checks(snap(l2.w), l2.lay, euid=os.geteuid())
    assert doctor.level2_reached(level1, second.checks)


def test_perimeter_line_self_check_silent(l2: L2) -> None:
    l2.self_check = OSError("the call failed")
    second = level2_of(l2)
    assert second.perimeter.status == "данные недоступны"
    assert "нет ответа" in second.perimeter.reason


# --- the seams ---


def test_role_check_seam_argv(
    fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def run_command(argv: list[str], **_kw: object) -> plat.RunResult:
        calls.append(list(argv))
        return plat.RunResult(0, b"{}", b"", True)

    monkeypatch.setattr(plat, "run_command", run_command)
    lay = fake_layout.layout
    doctor.role_check(lay, "df-review")
    doctor.role_access("df-exec", "-x", Path("/home/alice/.config/gh"))
    assert calls == [
        ["/usr/bin/sudo", "-n", "-u", "df-review", str(lay.libexec / "df-launch")]
        + ["df-review", "check"],
        ["/usr/bin/sudo", "-n", "-u", "df-exec", "/bin/test", "-x"]
        + ["/home/alice/.config/gh"],
    ]


def test_nested_seams_go_through_the_roles_sudo(
    fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], bytes]] = []

    def run_with_input(argv: list[str], data: bytes, **_kw: object) -> plat.RunResult:
        calls.append((list(argv), data))
        return plat.RunResult(1, b"", b"", True)

    monkeypatch.setattr(plat, "run_with_input", run_with_input)
    lay = fake_layout.layout
    publish = str(lay.libexec / "df-publish")
    doctor.role_perimeter_status(lay, "df-exec", b"{}")
    doctor.review_merge(lay)
    inner = ["/usr/bin/sudo", "-n", "-u", "df-publisher", publish]
    assert calls == [
        (["/usr/bin/sudo", "-n", "-u", "df-exec", *inner, "perimeter-status"], b"{}"),
        (["/usr/bin/sudo", "-n", "-u", "df-review", *inner, "merge"], b""),
    ]


def test_self_check_seam_names_doctor(
    fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[bytes] = []

    def run_with_input(argv: list[str], data: bytes, **_kw: object) -> plat.RunResult:
        sent.append(data)
        return plat.RunResult(0, b"", b"", True)

    monkeypatch.setattr(plat, "run_with_input", run_with_input)
    setup_agent.publisher_self_check(fake_layout.layout, purpose="doctor")
    (data,) = sent
    assert request.parse("self-check", data).request_id.startswith("doctor-")


# --- fix round 1: R13-8 (M1), m1, m3, nits ---


def test_app_sibling_apps_problem_is_a_note(l2: L2) -> None:
    """R13-8: Q's broken key does not fail P's `github.app`; it is a note."""
    l2.write_products(sibling=True)
    problem = f"app_key_unusable: {SIBLING_APP}"
    l2.self_check = self_check_answer(problems=(problem,))
    status, reason = status_of(l2, "github.app")
    assert status == "ok"
    assert reason == f"источники доверия: не проверено (этап 3); другие App: {problem}"


@pytest.mark.parametrize(
    "problem",
    [
        f"app_installation_unreadable: {INSTALLATION}",
        f"app_unreadable: {APP_ID}: HTTP 401",
        "app_installation_outside_products: 999",  # no App known: P's
        "app_unreadable: x: garbled",  # not attributable: P's
        "something_new: 1",  # not an App problem, not the perimeter's: P's
    ],
)
def test_app_own_or_unattributable_problem_broken(l2: L2, problem: str) -> None:
    l2.write_products(sibling=True)
    l2.self_check = self_check_answer(problems=(problem,))
    status, reason = status_of(l2, "github.app")
    assert status == "нарушено" and problem in reason


def test_app_sibling_installation_problem_is_a_note(l2: L2) -> None:
    l2.write_products(sibling=True)
    problem = f"app_installation_unreadable: {SIBLING_INSTALLATION}"
    l2.self_check = self_check_answer(problems=(problem,))
    assert status_of(l2, "github.app")[0] == "ok"


def test_perimeter_problem_only_on_the_perimeter_line(l2: L2) -> None:
    """R13-8: `perimeter_unavailable` is the line's, not `github.app`'s."""
    problem = "perimeter_unavailable: lost"
    l2.self_check = self_check_answer(problems=(problem,), stats=None)
    second = level2_of(l2)
    checks = by_id(second.checks)
    assert (checks["github.app"].status, checks["github.app"].reason) == (
        "ok",
        "источники доверия: не проверено (этап 3)",
    )
    assert (second.perimeter.status, second.perimeter.reason) == (
        "данные недоступны",
        problem,
    )


def test_isolation_key_needs_positive_evidence(l2: L2) -> None:
    """m1: a `self-check` that never looked at P's App proves no key."""
    l2.self_check = answer("done", None, SelfCheckResult((), (), None).to_json())
    status, reason = status_of(l2, "roles.isolation")
    assert status == "не проверено"
    assert reason == f"df-publisher: self-check не проверял App {APP_ID}"


def test_isolation_key_signed_but_refused_is_evidence(l2: L2) -> None:
    """`app_unreadable: <P's App>` means the publisher signed with the key."""
    problem = f"app_unreadable: {APP_ID}: HTTP 401"
    l2.self_check = answer(
        "done", None, SelfCheckResult((), (problem,), None).to_json()
    )
    assert status_of(l2, "roles.isolation")[0] == "ok"


def test_isolation_sibling_installation_is_no_evidence(l2: L2) -> None:
    l2.write_products(sibling=True)
    l2.self_check = self_check_answer(installation=SIBLING_INSTALLATION)
    assert status_of(l2, "roles.isolation")[0] == "не проверено"


def _chmod_000(path: Path) -> None:
    path.chmod(0o000)


def _fifo(path: Path) -> None:
    path.unlink()
    os.mkfifo(path)


def _directory(path: Path) -> None:
    path.unlink()
    path.mkdir()


@pytest.mark.parametrize(
    "spoil",
    [
        pytest.param(
            _chmod_000,
            marks=pytest.mark.skipif(
                os.geteuid() == 0, reason="root reads a 0o000 file: not unreadable"
            ),
        ),
        _fifo,
        _directory,
    ],
)
def test_unreadable_products_is_unavailable(l2: L2, spoil: object) -> None:
    """R13-12, with real objects: a `products.toml` that cannot be read is
    «данные недоступны» for 7 and 8-10, never «не зарегистрирован»."""
    path = l2.lay.etc / "products.toml"
    spoil(path)  # type: ignore[operator]
    try:
        checks = l2.level2()
    finally:
        if path.is_file():
            path.chmod(0o644)
    registered = checks["product.registered"]
    assert registered.status == "данные недоступны"
    assert registered.reason.startswith("products.toml:")
    for name in ("github.machine_user", "github.app", "models.access"):
        assert checks[name].status == "данные недоступны"
        assert checks[name].reason.startswith("product.registered: products.toml:")


@pytest.mark.parametrize("text", ["schema = 1\n", b"\xff\xfe"])
def test_malformed_products_is_broken(l2: L2, text: str | bytes) -> None:
    """R13-12: text that does not parse (or decode) is «нарушено»; 8-10 are
    «не проверено» with 7's reason."""
    path = l2.lay.etc / "products.toml"
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text)
    checks = l2.level2()
    assert checks["product.registered"].status == "нарушено"
    for name in ("github.machine_user", "github.app", "models.access"):
        assert checks[name].status == "не проверено"
        assert checks[name].reason.startswith("product.registered: products.toml:")


def test_machine_user_rate_limited_unavailable(l2: L2) -> None:
    l2.github = refused("GET https://api.github.com/user: HTTP 429")
    assert status_of(l2, "github.machine_user")[0] == "данные недоступны"


def test_reserved_operator_unavailable(l2: L2) -> None:
    """n5: `SUDO_USER=root` is no operator: «данные недоступны»."""
    status, reason = status_of(l2, "roles.sudoers", operator="root")
    assert status == "данные недоступны" and reason.startswith("оператор недопустим")


def test_check_not_json_keeps_sudos_words(l2: L2) -> None:
    l2.reports["df-exec"] = plat.RunResult(
        0, b"", b"sudo: a password is required\n", True
    )
    _status, reason = status_of(l2, "roles.isolation")
    assert "a password is required" in reason


@pytest.mark.parametrize("as_json", [False, True])
def test_transitional_with_product(
    l2: L2, capsys: pytest.CaptureFixture[str], as_json: bool
) -> None:
    """n7: a transitional state with `--product`: level 2 not reached too."""
    raw = state.read_raw(l2.lay)
    argv = ["--product", str(PRODUCT), *(["--json"] if as_json else [])]
    capsys.readouterr()
    assert run_doctor(l2.w, argv, Transitional(raw.generation + 1)) == 2
    out = capsys.readouterr().out
    if as_json:
        data = json.loads(out)
        assert (data["product"], data["level2"]) == (PRODUCT, "not reached")
    else:
        assert out.splitlines()[1] == "уровень 2: не достигнут — уровень 1 не достигнут"
    assert l2.calls == []


# --- fix round 2: the operator of the installed sudoers file (R13-10) ---


def test_installed_operator_named_by_its_check_rule(l2: L2) -> None:
    assert sudoers.installed_operator(sudoers.render(l2.lay, "alice"), l2.lay) == (
        "alice"
    )
    assert sudoers.installed_operator("alice ALL=(ALL) ALL\n", l2.lay) is None


def test_file_of_another_operator_is_unavailable_with_the_hint(
    l2: L2, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`roles setup --operator alice`, then `sudo doctor` by bob: the file is
    not broken, the check cannot judge it for bob."""
    monkeypatch.setenv("SUDO_USER", "bob")
    capsys.readouterr()
    assert l2.doctor(["--json"]) == 2
    data = json.loads(capsys.readouterr().out)
    sudo = next(c for c in data["level2_checks"] if c["id"] == "roles.sudoers")
    hint = (
        f"файл установлен для оператора {OPERATOR}, проверка для bob — "
        f"укажите --operator {OPERATOR}"
    )
    assert (sudo["status"], sudo["reason"]) == ("данные недоступны", hint)


def test_operator_flag_wins_over_sudo_user(
    l2: L2, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """R13-10: `--operator alice` > `SUDO_USER=bob`; the gh probe is alice's."""
    monkeypatch.setenv("SUDO_USER", "bob")
    asked: list[str] = []

    def operator_gh(operator: str) -> Path:
        asked.append(operator)
        return l2.gh_dir

    monkeypatch.setattr(doctor, "operator_gh", operator_gh)
    capsys.readouterr()
    assert l2.doctor(["--operator", OPERATOR]) == 0
    assert capsys.readouterr().out.splitlines()[1] == "уровень 2: достигнут"
    assert asked == [OPERATOR]


def test_corrupt_file_of_the_same_operator_is_broken(l2: L2) -> None:
    """The negative twin: the file names this operator but differs."""
    path = sudoers.SUDOERS_FILE
    path.chmod(0o640)
    text = path.read_text()
    assert " merge," in text
    path.write_text(text.replace(" merge,", " merge-any,", 1))
    path.chmod(0o440)
    status, reason = status_of(l2, "roles.sudoers")
    assert status == "нарушено" and "текст не совпадает" in reason


# --- fix round 3: recheck minors ---


def _planted(lay: Layout, caller: str) -> str:
    launch_cmd = lay.libexec / "df-launch"
    return f"{caller} ALL=(df-exec) NOPASSWD: NOSETENV: {launch_cmd} df-exec check\n"


def _rewrite_sudoers(text: str) -> None:
    path = sudoers.SUDOERS_FILE
    path.chmod(0o640)
    path.write_text(text)
    path.chmod(0o440)


def test_installed_operator_needs_exactly_one_rule(l2: L2) -> None:
    """A planted second `df-exec check` rule makes the name ambiguous."""
    text = sudoers.render(l2.lay, OPERATOR)
    head, sep, rest = text.partition(f"{OPERATOR} ALL=(df-exec)")
    planted = head + _planted(l2.lay, "mallory") + sep + rest
    assert sudoers.installed_operator(planted, l2.lay) is None
    assert sudoers.installed_operator(text + _planted(l2.lay, OPERATOR), l2.lay) is None


@pytest.mark.parametrize("caller", [OPERATOR, "bob"])
def test_planted_rule_is_broken_not_another_operator(
    l2: L2, monkeypatch: pytest.MonkeyPatch, caller: str
) -> None:
    """mallory's rule before alice's: «нарушено», never «укажите --operator
    mallory», whoever runs doctor."""
    text = sudoers.render(l2.lay, OPERATOR)
    head, sep, rest = text.partition(f"{OPERATOR} ALL=(df-exec)")
    _rewrite_sudoers(head + _planted(l2.lay, "mallory") + sep + rest)
    status, reason = status_of(l2, "roles.sudoers", operator=caller)
    assert status == "нарушено"
    assert "mallory" not in reason and "текст не совпадает" in reason


def test_other_operator_keeps_operator_free_problems(
    l2: L2, monkeypatch: pytest.MonkeyPatch
) -> None:
    """alice's file checked by bob, with the publisher's output in sudo's I/O
    log: «нарушено» with that problem and the operator note."""
    io = "sudo -l -U df-exec: /opt/df/libexec/df-publish: log_output"
    monkeypatch.setattr(sudoers, "io_log_problems", lambda _l: [io])
    asked: list[tuple[str, str]] = []

    def policy(name: str, *, layout: Layout, operator: str) -> list[str]:
        asked.append((name, operator))
        return []

    monkeypatch.setattr(sudoers, "policy_problems", policy)
    c = l2.level2(operator="bob")["roles.sudoers"]
    assert c.status == "нарушено"
    note = (
        f"файл установлен для оператора {OPERATOR}, проверка для bob — "
        f"укажите --operator {OPERATOR}"
    )
    assert c.problems == (io, note)
    names = ["df-exec", "df-review", "df-publisher", "df-build"]
    assert asked == [(n, OPERATOR) for n in names]


def test_other_operator_keeps_policy_and_visudo_problems(
    l2: L2, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sudoers, "validate", lambda _t: ["syntax error"])
    monkeypatch.setattr(
        sudoers,
        "policy_problems",
        lambda n, **_k: ["лишнее правило"] if n == "df-review" else [],
    )
    c = l2.level2(operator="bob")["roles.sudoers"]
    assert c.status == "нарушено"
    assert any("syntax error" in p for p in c.problems)
    assert "лишнее правило" in c.problems
    assert "укажите --operator" in c.problems[-1]


def test_other_operator_read_is_bounded(
    l2: L2, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file beyond the expected text plus the margin is not read: the
    name is unknown (`None`), so the check stays «нарушено»."""
    path = sudoers.SUDOERS_FILE
    text = sudoers.render(l2.lay, OPERATOR)
    other = doctor._other_operator  # noqa: SLF001
    assert other(l2.lay, path, "bob") == OPERATOR
    big = text + "#" * (doctor.SUDOERS_MARGIN + 4096) + "\n"
    _rewrite_sudoers(big)
    read: list[int] = []
    real = os.read

    def counting(fd: int, n: int) -> bytes:
        read.append(n)
        return real(fd, n)

    monkeypatch.setattr(os, "read", counting)
    assert other(l2.lay, path, "bob") is None
    monkeypatch.setattr(os, "read", real)
    limit = len(sudoers.render(l2.lay, "bob").encode()) + doctor.SUDOERS_MARGIN + 1
    assert sum(read) <= limit
    status, reason = status_of(l2, "roles.sudoers", operator="bob")
    assert status == "нарушено" and "укажите" not in reason


@pytest.mark.parametrize(
    "argv",
    [["--operator", OPERATOR], ["--probe-models", "--operator", OPERATOR]],
)
def test_operator_without_product_is_a_usage_error(
    fake_installed: World, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    assert run_doctor(fake_installed, argv) == 2
    assert "doctor: --operator только с --product" in capsys.readouterr().err
