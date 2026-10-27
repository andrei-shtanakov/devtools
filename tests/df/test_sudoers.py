"""`/etc/sudoers.d/df` of the stage-2 roles (stage 2a, Task 3).

The `sudo -n -l -U` texts of `fixtures/sudoers/` are provisional until the CI
capture of `test_acceptance_root_sudoers.py` replaces them (README there).
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Sequence
from pathlib import Path

import pytest

from df import fsutil, roles, sudoers
from df import platform as plat
from df.journal import StepFailed
from df.paths import Layout
from df.platform import RunResult
from tests.df.account_world import ok

FIXTURES = Path(__file__).parent / "fixtures" / "sudoers"
SUDO_L = Path(__file__).parent / "fixtures" / "sudo-l"
OSES = ["linux", "macos"]
OP = "runner"
ROOT = Layout(Path("/"))
LIBEXEC = "/opt/df/libexec"
VISUDO_USABLE = os.access(sudoers.VISUDO, os.X_OK)


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _sudo(
    monkeypatch: pytest.MonkeyPatch, outputs: dict[str, str], code: int = 0
) -> list[list[str]]:
    """`run_command` answering `sudo -n -l -U <name>` with `outputs[name]`."""
    seen: list[list[str]] = []

    def run(argv: Sequence[str], **_kw: object) -> RunResult:
        seen.append(list(argv))
        assert list(argv[:4]) == ["/usr/bin/sudo", "-n", "-l", "-U"]
        return ok(outputs[argv[4]].encode(), code)

    monkeypatch.setattr(plat, "run_command", run)
    return seen


def _role_outputs(os_name: str) -> dict[str, str]:
    names = (*roles.ROLE_USERS, OP)
    return {name: _fixture(f"sudo-l-{os_name}-{name}.txt") for name in names}


def _problems(name: str) -> list[str]:
    return sudoers.policy_problems(name, layout=ROOT, operator=OP)


# --- render ------------------------------------------------------------------


def test_render_exact() -> None:
    assert sudoers.render(ROOT, OP) == _fixture("expected.txt")


def test_render_under_r_root(tmp_path: Path) -> None:
    root = Path(os.path.realpath(tmp_path))
    text = sudoers.render(Layout(root), OP)
    expected = _fixture("expected.txt").replace("/opt/df/", f"{root}/opt/df/")
    assert text == expected
    assert text.count(f"{root}/opt/df/libexec/") == 12


def test_render_no_wildcard_all_only_as_host() -> None:
    text = sudoers.render(ROOT, OP)
    assert "*" not in text
    joined = text.replace("\\\n", " ")
    for line in joined.splitlines():
        if line.startswith(("#", "Defaults")):
            assert "ALL" not in line
            continue
        assert re.findall(r"\bALL\b", line) == ["ALL"], line
        assert re.match(r"\S+ ALL=\(", line), line


@pytest.mark.parametrize("operator", [*roles.ROLE_USERS, "root", "df-build"])
def test_render_refuses_reserved_operator(operator: str) -> None:
    with pytest.raises(ValueError, match="operator"):
        sudoers.render(ROOT, operator)


@pytest.mark.parametrize(
    "operator",
    ["", "a b", "a,b", "a:b", "a=b", "a\\b", "ALL", "OPS", "%admin", "#0", "!x"],
)
def test_render_refuses_invalid_operator(operator: str) -> None:
    with pytest.raises(ValueError, match="operator"):
        sudoers.render(ROOT, operator)


def test_render_refuses_unsafe_root(tmp_path: Path) -> None:
    root = Path(os.path.realpath(tmp_path)) / "a b"
    root.mkdir()
    with pytest.raises(ValueError, match="path"):
        sudoers.render(Layout(root), OP)


# --- validate ----------------------------------------------------------------


def _visudo(
    monkeypatch: pytest.MonkeyPatch, result: RunResult
) -> list[tuple[list[str], str, int]]:
    """`run_command` as visudo: records argv, the checked text and its mode."""
    seen: list[tuple[list[str], str, int]] = []

    def run(argv: Sequence[str], **_kw: object) -> RunResult:
        path = Path(argv[-1])
        mode = stat.S_IMODE(path.stat().st_mode)
        seen.append((list(argv), path.read_text(), mode))
        return result

    monkeypatch.setattr(plat, "run_command", run)
    return seen


def test_validate_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _visudo(monkeypatch, ok(b"x: parsed OK\n"))
    assert sudoers.validate("text\n") == []
    [(argv, text, _mode)] = seen
    assert argv[:3] == ["/usr/sbin/visudo", "-c", "-f"]
    assert text == "text\n"
    assert not os.path.exists(argv[3])


def test_validate_reports_problems(monkeypatch: pytest.MonkeyPatch) -> None:
    out = b"x:1:21: syntax error\n"
    _visudo(monkeypatch, RunResult(1, b"", out, launched=True))
    assert sudoers.validate("broken (\n") == ["x:1:21: syntax error"]


def test_validate_not_launched(monkeypatch: pytest.MonkeyPatch) -> None:
    _visudo(monkeypatch, RunResult(127, b"", b"", launched=False))
    assert sudoers.validate("text\n") == ["/usr/sbin/visudo: не запустился"]


@pytest.mark.skipif(not VISUDO_USABLE, reason="нет /usr/sbin/visudo")
def test_validate_real_visudo() -> None:
    assert sudoers.validate(sudoers.render(ROOT, OP)) == []
    broken = sudoers.render(ROOT, OP).replace("ALL=(df-exec)", "ALL=(df-exec")
    assert sudoers.validate(broken) != []


# --- install -----------------------------------------------------------------


@pytest.fixture()
def target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "sudoers.d"
    directory.mkdir()
    path = directory / "df"
    monkeypatch.setattr(sudoers, "SUDOERS_FILE", path)
    return path


def test_install_validates_temp_then_renames(
    target: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _visudo(monkeypatch, ok(b"parsed OK\n"))
    sudoers.install("rules\n")
    [(argv, text, mode)] = seen
    checked = Path(argv[-1])
    assert checked.parent == target.parent and checked != target
    assert "." in checked.name  # sudo's includedir skips names with a dot
    assert (text, mode) == ("rules\n", 0o440)
    assert target.read_text() == "rules\n"
    st = target.stat()
    assert stat.S_IMODE(st.st_mode) == 0o440 and st.st_uid == os.getuid()
    assert sorted(p.name for p in target.parent.iterdir()) == ["df"]


def test_install_problems_never_rename(
    target: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target.write_text("old\n")
    _visudo(monkeypatch, RunResult(1, b"", b"x:1:3: syntax error\n", launched=True))
    renames: list[object] = []
    monkeypatch.setattr(os, "rename", lambda *a, **k: renames.append(a))
    with pytest.raises(StepFailed) as info:
        sudoers.install("broken (\n")
    assert info.value.step == "sudoers"
    assert "syntax error" in info.value.detail
    assert renames == []
    assert target.read_text() == "old\n"
    assert sorted(p.name for p in target.parent.iterdir()) == ["df"]


def test_install_sync_error_is_step_failure(
    target: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review M2: an `OSError` after the rename is the step's failure too."""
    _visudo(monkeypatch, ok(b"parsed OK\n"))

    def sync_dir(_path: object) -> None:
        if target.exists():  # only the sync after the rename fails
            raise OSError(5, "EIO")

    monkeypatch.setattr(fsutil, "sync_dir", sync_dir)
    with pytest.raises(StepFailed) as info:
        sudoers.install("rules\n")
    assert info.value.step == "sudoers"


def test_install_os_error_is_step_failure(
    target: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _visudo(monkeypatch, ok(b"parsed OK\n"))
    target.parent.rmdir()
    with pytest.raises(StepFailed) as info:
        sudoers.install("rules\n")
    assert info.value.step == "sudoers"


# --- policy_problems ---------------------------------------------------------


@pytest.mark.parametrize("os_name", OSES)
@pytest.mark.parametrize("name", [*roles.ROLE_USERS, OP])
def test_policy_clean_on_captures(
    monkeypatch: pytest.MonkeyPatch, os_name: str, name: str
) -> None:
    seen = _sudo(monkeypatch, _role_outputs(os_name))
    assert _problems(name) == []
    assert seen == [["/usr/bin/sudo", "-n", "-l", "-U", name]]


def test_policy_reads_wrapped_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    _sudo(monkeypatch, {"df-review": _fixture("sudo-l-wrapped-df-review.txt")})
    assert _problems("df-review") == []


def _edited(monkeypatch: pytest.MonkeyPatch, name: str, old: str, new: str) -> None:
    text = _fixture(f"sudo-l-linux-{name}.txt")
    assert old in text
    _sudo(monkeypatch, {name: text.replace(old, new)})


@pytest.mark.parametrize(
    "old,new,named",
    [
        # an extra tag: SETENV instead of NOSETENV
        ("NOPASSWD: NOSETENV: /opt", "NOPASSWD: SETENV: /opt", "SETENV"),
        # an extra command
        ("perimeter-status", "perimeter-status, /opt/df/libexec/df-publish x", "x"),
        # a wider RunAs
        ("(df-review)", "(df-review, df-publisher)", "df-review, df-publisher"),
        ("(df-review)", "(ALL)", "(ALL)"),
        # arguments of df-review-run no longer empty-only
        ('df-review-run ""', "df-review-run", "df-review-run"),
    ],
)
def test_policy_role_differs(
    monkeypatch: pytest.MonkeyPatch, old: str, new: str, named: str
) -> None:
    _edited(monkeypatch, "df-exec", old, new)
    problems = _problems("df-exec")
    assert problems and all(p.startswith("sudo -l -U df-exec: ") for p in problems)
    assert any(named in p for p in problems), problems


def test_policy_role_missing_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    line = '    (df-review) NOPASSWD: NOSETENV: /opt/df/libexec/df-review-run ""\n'
    _edited(monkeypatch, "df-exec", line, "")
    assert _problems("df-exec") == [
        (
            "sudo -l -U df-exec: нет правила "
            '(df-review) NOPASSWD: NOSETENV: /opt/df/libexec/df-review-run ""'
        )
    ]


def test_policy_tags_carry_within_a_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """sudo prints RunAs and tags only when they change along a line."""
    text = _fixture("sudo-l-linux-df-exec.txt")
    first, second = (
        '    (df-review) NOPASSWD: NOSETENV: /opt/df/libexec/df-review-run ""\n',
        "    (df-publisher) NOPASSWD: NOSETENV: ",
    )
    merged = text.replace(first + second, first.rstrip("\n") + ", (df-publisher) ")
    assert merged != text
    _sudo(monkeypatch, {"df-exec": merged})
    assert _problems("df-exec") == []


def test_policy_tags_reset_on_a_new_line(monkeypatch: pytest.MonkeyPatch) -> None:
    _edited(
        monkeypatch,
        "df-exec",
        "    (df-publisher) NOPASSWD: NOSETENV: ",
        "    (df-publisher) ",
    )
    problems = _problems("df-exec")
    assert len([p for p in problems if "нет правила" in p]) == 3


def test_policy_operator_without_launch_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    line = (
        "    (df-review) NOPASSWD: NOSETENV: "
        "/opt/df/libexec/df-launch df-review check\n"
    )
    _edited(monkeypatch, OP, line, "")
    assert _problems(OP) == [
        (
            "sudo -l -U runner: нет правила "
            "(df-review) NOPASSWD: NOSETENV: "
            "/opt/df/libexec/df-launch df-review check"
        )
    ]


def test_policy_operator_extra_rules_unchecked(monkeypatch: pytest.MonkeyPatch) -> None:
    _edited(monkeypatch, OP, "(ALL) NOPASSWD: ALL", "(ALL : ALL) SETENV: /bin/sh")
    assert _problems(OP) == []


@pytest.mark.parametrize("name", ["df-publisher", "df-build"])
def test_policy_no_rule_role_with_a_rule(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    text = (
        f"User {name} may run the following commands on ubuntu-ci:\n"
        "    (ALL) NOPASSWD: ALL\n"
    )
    _sudo(monkeypatch, {name: text})
    assert _problems(name) == [f"sudo -l -U {name}: some"]


RULES_HEAD = "User df-exec may run the following commands on ubuntu-ci:\n"


@pytest.mark.parametrize(
    "text",
    [
        "garbage\n",
        "    indented first\n",
        (
            "User df-exec is not allowed to run sudo on ubuntu-ci.\n"
            "User df-exec may run the following commands on ubuntu-ci:\n"
        ),
        f"{RULES_HEAD}    NOPASSWD: /bin/sh\n",
        f"{RULES_HEAD}        (df-review) continuation without an entry\n",
        "User df-other may run the following commands on ubuntu-ci:\n",
    ],
)
def test_policy_unrecognized_listing(
    monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    _sudo(monkeypatch, {"df-exec": text})
    [problem] = _problems("df-exec")
    assert problem.startswith("sudo -l -U df-exec: ")


def test_policy_sudo_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _sudo(monkeypatch, {"df-exec": "sudo: a password is required\n"}, code=1)
    assert _problems("df-exec") == ["sudo -l -U df-exec: sudo: код 1"]


def test_policy_unknown_name() -> None:
    with pytest.raises(ValueError, match="nobody"):
        _problems("nobody")


def _agreement_cases() -> list[tuple[str, int, str]]:
    cases = [
        (name, int((SUDO_L / f"{name}.rc").read_text()), (SUDO_L / f"{name}.out"))
        for name in ("macos-none", "linux-none", "garbage", "exit-1")
    ]
    found = [(n, c, p.read_text()) for n, c, p in cases]
    for os_name in OSES:
        text = _fixture(f"sudo-l-{os_name}-df-publisher.txt")
        found.append((f"{os_name}-publisher", 0, text))
        some = _fixture(f"sudo-l-{os_name}-runner.txt").replace("runner", "{name}")
        found.append((f"{os_name}-some", 0, some))
    return found


@pytest.mark.parametrize("case,code,text", _agreement_cases())
@pytest.mark.parametrize("name", ["df-build", "df-publisher"])
def test_policy_no_rule_roles_agree_with_platform(
    monkeypatch: pytest.MonkeyPatch, case: str, code: int, text: str, name: str
) -> None:
    """R8: `df-build` / `df-publisher` reuse `platform.sudo_rules` verbatim."""
    renamed = re.sub(r"df-build|df-publisher|\{name\}", name, text)
    _sudo(monkeypatch, {name: renamed}, code=code)
    rules = plat.sudo_rules(name)
    expected = [] if rules == "none" else [f"sudo -l -U {name}: {rules}"]
    assert _problems(name) == expected
    if name == plat.BUILD_USER:
        assert _problems(name) == plat._sudo_policy_problems(name)


# --- io_log_problems ---------------------------------------------------------


def _io(
    monkeypatch: pytest.MonkeyPatch, edit: tuple[str, str] | None = None
) -> list[str]:
    outputs = _role_outputs("linux")
    if edit is not None:
        old, new = edit
        for name in ("df-exec", "df-review"):
            assert old in outputs[name]
            outputs[name] = outputs[name].replace(old, new)
    seen = _sudo(monkeypatch, outputs)
    problems = sudoers.io_log_problems(ROOT)
    assert [argv[4] for argv in seen] == ["df-exec", "df-review"]
    return problems


@pytest.mark.parametrize("os_name", OSES)
def test_io_log_clean_on_captures(
    monkeypatch: pytest.MonkeyPatch, os_name: str
) -> None:
    _sudo(monkeypatch, _role_outputs(os_name))
    assert sudoers.io_log_problems(ROOT) == []


def test_io_log_clean_on_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    outputs = _role_outputs("linux")
    outputs["df-review"] = _fixture("sudo-l-wrapped-df-review.txt")
    _sudo(monkeypatch, outputs)
    assert sudoers.io_log_problems(ROOT) == []


USE_PTY = ("use_pty, env_reset", "use_pty, {entry}, env_reset")
BINDING = "    Defaults!/opt/df/libexec/df-publish !log_output\n"


def _defaults(entry: str) -> tuple[str, str]:
    return USE_PTY[0], USE_PTY[1].format(entry=entry)


def _binding(extra: str) -> tuple[str, str]:
    return BINDING, BINDING + extra + "\n"


@pytest.mark.parametrize(
    "edit",
    [
        _defaults("log_output"),  # the command binding turns it off
        _defaults("log_input"),
        _defaults("log_stdin, log_ttyin"),
        _defaults("!log_stdout"),
        _binding("    Defaults!/usr/bin/other log_output"),
        _binding("    Defaults>root log_stdout"),
        _binding("    Defaults>df-publisher log_output"),  # runas before cmnd
        _binding("    Defaults!/usr/bin/a, /usr/bin/b log_output"),
        _binding(
            f"    Defaults!/usr/bin/a, {LIBEXEC}/df-publish log_stdout, !log_stdout"
        ),
        _binding("    Defaults!/opt/df/libexec/df-publish env_reset"),
    ],
)
def test_io_log_off(monkeypatch: pytest.MonkeyPatch, edit: tuple[str, str]) -> None:
    assert _io(monkeypatch, edit) == []


@pytest.mark.parametrize(
    "edit,key",
    [
        (_defaults("log_stdout"), "log_stdout"),
        (_defaults("log_stderr"), "log_stderr"),
        (_defaults("log_ttyout"), "log_ttyout"),
        (_binding("    Defaults!ALL log_output"), "log_output"),
        (_binding("    Defaults!/opt/df/libexec/df-publish log_output"), "log_output"),
        (_binding("    Defaults>df-publisher log_stdout"), "log_stdout"),
        (
            _binding(f"    Defaults!/usr/bin/a, {LIBEXEC}/df-publish log_stdout"),
            "log_stdout",
        ),
        ((BINDING, ""), None),  # no `!log_output`: default off
    ],
)
def test_io_log_on(
    monkeypatch: pytest.MonkeyPatch, edit: tuple[str, str], key: str | None
) -> None:
    problems = _io(monkeypatch, edit)
    if key is None:
        assert problems == []
        return
    assert problems == [
        f"sudo -l -U {name}: {LIBEXEC}/df-publish: {key} включён"
        for name in ("df-exec", "df-review")
    ]


#: review M1: a RunAs change inside one privilege starts a line that does not
#: reprint the tags it inherits.
INHERITED = "    (df-review) PASSWD: SETENV: LOG_OUTPUT: /bin/true\n"


def test_io_log_tag_inherited_across_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    edit = ("    (df-publisher) NOPASSWD:", INHERITED + "    (df-publisher) NOPASSWD:")
    assert _io(monkeypatch, edit) == [
        f"sudo -l -U {name}: {LIBEXEC}/df-publish: правило с тегом LOG_OUTPUT"
        for name in ("df-exec", "df-review")
    ]


def test_io_log_tag_reset_by_explicit_nolog(monkeypatch: pytest.MonkeyPatch) -> None:
    new = INHERITED + "    (df-publisher) NOLOG_OUTPUT: NOPASSWD:"
    assert _io(monkeypatch, ("    (df-publisher) NOPASSWD:", new)) == []


def test_io_log_tag_on_publish_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    edit = ("(df-publisher) NOPASSWD:", "(df-publisher) LOG_OUTPUT: NOPASSWD:")
    assert _io(monkeypatch, edit) == [
        f"sudo -l -U {name}: {LIBEXEC}/df-publish: правило с тегом LOG_OUTPUT"
        for name in ("df-exec", "df-review")
    ]


@pytest.mark.parametrize(
    "edit",
    [
        _defaults("log_servers=10.0.0.1:30344"),
        _defaults("!log_servers"),
        _defaults("log_output=yes"),
        _defaults("log_output, !log_output"),  # order of levels unknown
        _binding("    Defaults!/opt/df/libexec/df-publish merge log_output"),
        _binding("    Defaults!/opt/df/libexec/ log_output"),
        _binding("    Defaults!/usr/local/bin/df-publish log_output"),
        _binding("    Defaults!PUBLISH log_output"),
        _binding("    Defaults>%wheel log_stdout"),
        _binding("    Defaults>DF log_stdout"),
        _binding("    Defaults@ubuntu-ci log_output"),
        _binding("    Defaults:df-exec log_output"),
        _binding("    Defaults!/usr/bin/other log_servers=h"),
        # review I1: a member with arguments (an expanded Cmnd_Alias) cannot be
        # told from entries; the rest of the binding is never dropped
        _binding(f"    Defaults!/usr/bin/foo bar, {LIBEXEC}/df-publish log_output"),
        _binding(f'    Defaults!/usr/bin/foo "", {LIBEXEC}/df-publish log_output'),
        _binding(f"    Defaults!{LIBEXEC}/df-publish merge, /usr/bin/x log_output"),
        _binding("    Defaults!/usr/bin/foo bar, /usr/bin/baz log_output"),
        _binding(f"    Defaults!{LIBEXEC}/df-publish merge !log_output"),
        _binding("    Defaults!/usr/bin/other log_output=1"),  # before scope
        ("Matching Defaults", "Unknown Defaults"),
    ],
)
def test_io_log_unverifiable(
    monkeypatch: pytest.MonkeyPatch, edit: tuple[str, str]
) -> None:
    problems = _io(monkeypatch, edit)
    assert len(problems) == 2, problems
    for name, problem in zip(("df-exec", "df-review"), problems, strict=True):
        assert problem.startswith(
            f"sudo -l -U {name}: нарушено: конфигурация I/O-журнала не проверяема"
        )
