"""Каталог вызова, профиль Seatbelt и допуск харнесса (TASK-002, TASK-003).

Дизайн `docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md`
§11, §12, §14; сценарии §16 Z6, Z7, Z16. Генератор профиля — чистая функция, его
тесты переносимые; создание каталога вызова идёт через `safe_io`
(`O_NOFOLLOW_ANY`) и проверяется только на macOS. Поведение профиля под
настоящим `sandbox-exec` — TASK-004 (`tests/test_governance_sandbox_live.py`).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from governance import author_launch, sandbox
from governance.sandbox import CallDir, ForbiddenRoots, SandboxError

DEVTOOLS_ROOT = Path(__file__).resolve().parents[1]

darwin_only = pytest.mark.skipif(
    not hasattr(os, "O_NOFOLLOW_ANY"),
    reason="каталог вызова создаётся через O_NOFOLLOW_ANY — только Darwin (macOS)",
)


@pytest.fixture
def base(tmp_path: Path) -> Path:
    # /var/folders → /private/var/folders: корни канонические, как у раннера
    return Path(os.path.realpath(tmp_path))


def _git(*args: str | Path) -> None:
    subprocess.run(["git", *map(str, args)], check=True, capture_output=True)


def _repo(path: Path) -> Path:
    _git("init", "-q", path)
    _git(
        "-C",
        path,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "init",
    )
    return path


def _far(base: Path, **override: Path) -> ForbiddenRoots:
    """Полный набор §12 п.2, заведомо вне `base/calls`; поля — переопределяемы."""
    far = base / "far"
    fields = {
        "target_dir": far / "target",
        "gitdir": far / "target" / ".git",
        "common_dir": far / "target" / ".git",
        "devtools_root": far / "devtools",
        "run_dir": far / "run",
    }
    return ForbiddenRoots(**{**fields, **override})


def _allow_write_terms(profile: str) -> list[tuple[str, str]]:
    """Термы единственного `(allow file-write* …)` — по балансу скобок."""
    start = profile.index("(allow file-write*")
    assert profile.count("(allow file-write*") == 1
    depth, in_str, i = 0, False, start
    while True:
        ch = profile[i]
        if in_str:
            if ch == "\\":
                i += 1
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    clause = profile[start : i + 1]
    return re.findall(r'\((subpath|literal|regex) (#?"(?:[^"\\]|\\.)*")\)', clause)


# --- переносимые: генератор профиля (§12) -----------------------------------


def _call(root: str = "/x/calls/run-1/c1") -> CallDir:
    return CallDir(Path(root))


def test_profile_has_every_section12_rule() -> None:
    profile = sandbox.render_profile(_call(), home=Path("/Users/op"))
    for rule in (
        "(version 1)",
        "(allow default)",
        "(deny file-write*)\n",
        '(subpath "/x/calls/run-1/c1/stage")',
        '(subpath "/x/calls/run-1/c1/tmp")',
        '(subpath "/x/calls/run-1/c1/claude-config")',
        '(literal "/dev/null")',
        '(regex #"^/dev/fd/")',
        '(deny file-write* (regex #"(^|/)\\.git(/|$)"))',
        '(deny file-ioctl (regex #"^/dev/tty"))',
        '(subpath "/Users/op/.config/review")',
        '(subpath "/Users/op/.config/gh")',
        '(subpath "/Users/op/.config/ai-prosto")',
        '(subpath "/Users/op/.ssh")',
        '(global-name "com.apple.SecurityServer")',
        '(global-name "com.apple.securityd.xpc")',
        '(global-name "com.apple.security.agent")',
        '(global-name-prefix "com.apple.coreservices.")',
        '(global-name-prefix "com.apple.lsd.")',
    ):
        assert rule in profile, rule


def test_profile_rule_order_denies_after_allow() -> None:
    # запреты после разрешений сильнее (F15, F17, F18): порядок значим
    profile = sandbox.render_profile(_call(), home=Path("/Users/op"))
    order = [
        profile.index("(allow default)"),
        profile.index("(deny file-write*)\n"),
        profile.index("(allow file-write*"),
        profile.index("(deny file-write* (regex"),
    ]
    assert order == sorted(order)


def test_profile_write_allow_is_exactly_call_dirs_and_devices() -> None:
    terms = _allow_write_terms(sandbox.render_profile(_call(), home=Path("/Users/op")))
    assert terms == [
        ("subpath", '"/x/calls/run-1/c1/stage"'),
        ("subpath", '"/x/calls/run-1/c1/tmp"'),
        ("subpath", '"/x/calls/run-1/c1/claude-config"'),
        ("literal", '"/dev/null"'),
        ("regex", '#"^/dev/fd/"'),
    ]


@pytest.mark.parametrize(
    "forbidden",
    ["/private/tmp", "/private/var/folders", "/dev/tty", "/dev/ttys", "/tmp"],
)
def test_profile_never_allows_shared_temp_or_terminals(forbidden: str) -> None:
    terms = _allow_write_terms(sandbox.render_profile(_call(), home=Path("/Users/op")))
    for _, value in terms:
        path = value.removeprefix("#").strip('"').lstrip("^")
        assert path != forbidden and not path.startswith(f"{forbidden}/"), value
        assert not path.startswith("/dev/tty"), value


def test_profile_does_not_allow_target_dir(base: Path) -> None:
    target = base / "target"
    profile = sandbox.render_profile(
        CallDir(base / "calls" / "r" / "c"), home=Path("/Users/op")
    )
    for _, value in _allow_write_terms(profile):
        assert str(target) not in value


def test_profile_escapes_quote_and_backslash_in_path() -> None:
    profile = sandbox.render_profile(
        _call('/x/a "q" b\\c/run-1/c1'), home=Path('/Users/o "p"')
    )
    assert '(subpath "/x/a \\"q\\" b\\\\c/run-1/c1/stage")' in profile
    assert '(subpath "/Users/o \\"p\\"/.ssh")' in profile


@pytest.mark.parametrize(
    ("call", "home"),
    [("calls/run-1/c1", "/Users/op"), ("/x/calls/run-1/c1", "Users/op")],
)
def test_profile_refuses_relative_paths(call: str, home: str) -> None:
    # относительный путь достроился бы cwd раннера (= target_dir агента)
    with pytest.raises(SandboxError, match="не абсолютный"):
        sandbox.render_profile(_call(call), home=Path(home))


@pytest.mark.parametrize("bad", ["/x/a\nb", "/x/a\x00b", "/x/a\x7fb"])
def test_profile_refuses_control_characters(bad: str) -> None:
    with pytest.raises(SandboxError, match="управляющим"):
        sandbox.render_profile(_call(bad), home=Path("/Users/op"))


def test_profile_refuses_control_character_in_symlink_target(base: Path) -> None:
    (base / "a\nb").mkdir()
    (base / "alias").symlink_to(base / "a\nb")
    with pytest.raises(SandboxError, match="управляющим"):
        sandbox.render_profile(CallDir(base / "alias"), home=Path("/Users/op"))


def test_profile_uses_realpath_of_secret_dirs(base: Path) -> None:
    # ~/.config — ссылка (dotfiles): Seatbelt сверяет конечный путь
    home = base / "home"
    real_config = base / "dotfiles" / "config"
    real_config.mkdir(parents=True)
    home.mkdir()
    (home / ".config").symlink_to(real_config)
    profile = sandbox.render_profile(_call(), home=home)
    assert f'(subpath "{real_config}/gh")' in profile
    assert f'(subpath "{home}/.config/gh")' not in profile


def test_profile_template_sha256_is_stable_and_not_of_rendered() -> None:
    digest = sandbox.profile_template_sha256()
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert sandbox.profile_template_sha256() == digest
    rendered = sandbox.render_profile(_call(), home=Path("/Users/op"))
    assert hashlib.sha256(rendered.encode()).hexdigest() != digest


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PROFILE_TEMPLATE", sandbox.PROFILE_TEMPLATE.replace("(deny file-ioctl", "(")),
        ("SECRET_DIRS", (".ssh",)),
        ("CALL_SUBDIRS", ("stage", "tmp", "claude-config", "extra")),
    ],
)
def test_profile_template_sha256_tracks_profile_drift(
    monkeypatch: pytest.MonkeyPatch, name: str, value: object
) -> None:
    # ослабление любой части профиля снимает допуск (§14)
    before = sandbox.profile_template_sha256()
    monkeypatch.setattr(sandbox, name, value)
    assert sandbox.profile_template_sha256() != before


def test_profile_template_has_only_named_placeholders() -> None:
    names = set(re.findall(r"\{(\w*)\}", sandbox.PROFILE_TEMPLATE))
    assert names == {"stage", "tmp", "claude_config", "secret_reads"}


# --- переносимые: корень вызовов и git-каталоги цели -------------------------


def test_calls_root_from_xdg_state_home(base: Path) -> None:
    root = sandbox.calls_root({"XDG_STATE_HOME": str(base / "state")})
    assert root == base / "state" / "devtools" / "agent-calls"
    assert not (base / "state").exists()  # корень создаёт create_call_dir


def test_calls_root_default_under_home(
    base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(base))
    root = sandbox.calls_root({})
    assert root == base / ".local" / "state" / "devtools" / "agent-calls"


def test_calls_root_is_canonical(base: Path) -> None:
    (base / "real").mkdir()
    (base / "alias").symlink_to(base / "real")
    root = sandbox.calls_root({"XDG_STATE_HOME": str(base / "alias")})
    assert root == base / "real" / "devtools" / "agent-calls"


def test_calls_root_refuses_relative_xdg() -> None:
    with pytest.raises(SandboxError, match="не абсолютный"):
        sandbox.calls_root({"XDG_STATE_HOME": "state"})


def test_target_git_dirs_of_worktree(base: Path) -> None:
    main = _repo(base / "main")
    _git("-C", main, "worktree", "add", "-q", base / "wt")
    git_dir, common = sandbox.target_git_dirs(base / "wt")
    assert common == main / ".git"
    assert git_dir.parent == main / ".git" / "worktrees"


def test_target_git_dirs_refuses_non_repo(base: Path) -> None:
    with pytest.raises(SandboxError, match="gitdir цели"):
        sandbox.target_git_dirs(base)


# --- macOS: создание каталога вызова (Z6, Z7) --------------------------------


@darwin_only
def test_create_call_dir_makes_fresh_empty_subdirs(base: Path) -> None:
    calls = base / "calls"
    calls.mkdir()
    call = sandbox.create_call_dir(calls, "run-1", forbidden=_far(base))
    assert call.root.parent == calls / "run-1"
    assert sorted(p.name for p in call.root.iterdir()) == sorted(sandbox.CALL_SUBDIRS)
    for sub in (call.stage, call.tmp, call.claude_config):
        assert sub.is_dir() and not any(sub.iterdir())


@darwin_only
def test_create_call_dir_ids_are_unique(base: Path) -> None:
    calls = base / "calls"
    calls.mkdir()
    a = sandbox.create_call_dir(calls, "run-1", forbidden=_far(base))
    b = sandbox.create_call_dir(calls, "run-1", forbidden=_far(base))
    assert a.root != b.root


@darwin_only
@pytest.mark.parametrize("planted", [False, True])
def test_create_call_dir_refuses_existing_call(base: Path, planted: bool) -> None:
    # Z6: существующий <call> — отказ на нём самом (и пустом), содержимое цело
    calls = base / "calls"
    (calls / "run-1" / "c1").mkdir(parents=True)
    if planted:
        (calls / "run-1" / "c1" / "stage").mkdir()
        (calls / "run-1" / "c1" / "stage" / "planted").write_text("x")
    with pytest.raises(SandboxError, match="'run-1/c1': уже существует"):
        sandbox.create_call_dir(calls, "run-1", forbidden=_far(base), call_id="c1")
    assert sorted(os.listdir(calls / "run-1" / "c1")) == (["stage"] if planted else [])
    if planted:
        assert (calls / "run-1" / "c1" / "stage" / "planted").read_text() == "x"


@darwin_only
def test_create_call_dir_refuses_symlinked_run_dir(base: Path) -> None:
    calls = base / "calls"
    calls.mkdir()
    (base / "elsewhere").mkdir()
    (calls / "run-1").symlink_to(base / "elsewhere")
    with pytest.raises(SandboxError, match="ссылка в пути"):
        sandbox.create_call_dir(calls, "run-1", forbidden=_far(base))
    assert not any((base / "elsewhere").iterdir())


@darwin_only
def test_create_call_dir_refuses_non_canonical_calls(base: Path) -> None:
    (base / "real").mkdir()
    (base / "alias").symlink_to(base / "real")
    with pytest.raises(SandboxError, match="не канонический"):
        sandbox.create_call_dir(base / "alias", "run-1", forbidden=_far(base))


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", ".hidden", "a b"])
def test_create_call_dir_refuses_bad_ids(base: Path, bad: str) -> None:
    with pytest.raises(SandboxError):
        sandbox.create_call_dir(base, bad, forbidden=_far(base))
    with pytest.raises(SandboxError):
        sandbox.create_call_dir(
            base, "run-1", forbidden=_far(base), call_id=bad or ".."
        )
    assert not any(base.iterdir())


@darwin_only
@pytest.mark.parametrize(
    "where",
    ["ancestor", "same", "descendant", "case-variant"],
)
def test_create_call_dir_refuses_overlap_before_creating(
    base: Path, where: str
) -> None:
    # Z7: пересечение (предок, совпадение, потомок; без учёта регистра —
    # том APFS) — отказ ДО создания чего-либо
    calls = base / "calls"
    calls.mkdir()
    other = {
        "ancestor": base,
        "same": calls / "run-1" / "c1",
        "descendant": calls / "run-1" / "c1" / "stage" / "x",
        "case-variant": calls / "RUN-1",
    }[where]
    with pytest.raises(SandboxError, match="пересекается с run_dir"):
        sandbox.create_call_dir(
            calls, "run-1", forbidden=_far(base, run_dir=other), call_id="c1"
        )
    assert not any(calls.iterdir())


@darwin_only
def test_create_call_dir_overlap_through_symlinked_forbidden(base: Path) -> None:
    # запретный корень сравнивается по realpath
    calls = base / "calls"
    calls.mkdir()
    (base / "alias").symlink_to(calls)
    with pytest.raises(SandboxError, match="пересекается с target_dir"):
        sandbox.create_call_dir(
            calls, "run-1", forbidden=_far(base, target_dir=base / "alias")
        )


@darwin_only
def test_create_call_dir_disjoint_siblings_pass(base: Path) -> None:
    calls = base / "calls"
    calls.mkdir()
    call = sandbox.create_call_dir(
        calls,
        "run-1",
        forbidden=_far(base, target_dir=base / "calls-target", run_dir=base / "run"),
    )
    assert call.stage.is_dir()


@darwin_only
def test_create_call_dir_for_devtools_itself_as_target(base: Path) -> None:
    # цель — сам devtools: корень вызовов вне чекаута — проверка проходит
    calls = sandbox.calls_root({"XDG_STATE_HOME": str(base / "state")})
    forbidden = sandbox.forbidden_roots(
        DEVTOOLS_ROOT,
        run_dir=DEVTOOLS_ROOT / "out" / "governance-runs" / "run-1",
        devtools_root=DEVTOOLS_ROOT,
    )
    assert forbidden.target_dir == DEVTOOLS_ROOT
    assert forbidden.common_dir == DEVTOOLS_ROOT / ".git"
    call = sandbox.create_call_dir(calls, "run-1", forbidden=forbidden)
    assert call.stage.is_dir()


@darwin_only
def test_create_call_dir_refuses_calls_root_inside_target(base: Path) -> None:
    # XDG_STATE_HOME внутри цели: каталог вызова попал бы в дерево цели
    target = _repo(base / "target")
    calls = sandbox.calls_root({"XDG_STATE_HOME": str(target / "state")})
    forbidden = sandbox.forbidden_roots(
        target, run_dir=base / "run", devtools_root=base / "devtools"
    )
    with pytest.raises(SandboxError, match="пересекается с target_dir"):
        sandbox.create_call_dir(calls, "run-1", forbidden=forbidden)
    # отказ не оставляет в цели ни одного каталога, даже корня вызовов
    assert not (target / "state").exists()


@darwin_only
def test_create_call_dir_refuses_calls_inside_worktree_common_dir(
    base: Path,
) -> None:
    main = _repo(base / "main")
    _git("-C", main, "worktree", "add", "-q", base / "wt")
    calls = sandbox.calls_root({"XDG_STATE_HOME": str(main / ".git" / "st")})
    forbidden = sandbox.forbidden_roots(
        base / "wt", run_dir=base / "run", devtools_root=base / "devtools"
    )
    with pytest.raises(SandboxError, match="пересекается с common dir"):
        sandbox.create_call_dir(calls, "run-1", forbidden=forbidden)


# --- допуск исполняемого файла харнесса (§14, TASK-003; Z16) -----------------

CONTRACT = DEVTOOLS_ROOT / "contracts" / "author-sandbox" / "v1" / "admitted.toml"


@pytest.fixture
def harness(base: Path) -> tuple[Path, Path, Path]:
    """Фейковый `claude`, оставляющий маркер при ЛЮБОМ запуске (Z16).

    В PATH — ссылка `bin/claude` на `opt/claude-real`: допуск сверяет
    `realpath`. Возвращает (каталог PATH, realpath, маркер).
    """
    marker = base / "marker"
    real = base / "opt" / "claude-real"
    real.parent.mkdir()
    real.write_text(f'#!/bin/sh\necho "$@" > "{marker}"\n')
    real.chmod(0o755)
    (base / "bin").mkdir()
    (base / "bin" / "claude").symlink_to(real)
    return base / "bin", real, marker


def _entry(real: Path, **override: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "harness": "claude",
        "version": "9.9.9",
        "realpath": str(real),
        "sha256": hashlib.sha256(real.read_bytes()).hexdigest(),
        "macos_major": 26,
        "profile_sha256": sandbox.profile_template_sha256(),
        "evidence": "docs/evidence/x.md",
    }
    return {**entry, **override}


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _contract(base: Path, *entries: dict[str, object]) -> Path:
    # собственный каркас формата контракта + записи; contracts/ не читается
    # и не трогается — тесты не зависят от записей, внесённых шагом H2
    text = "schema = 1\n"
    for entry in entries:
        text += "\n[[admitted]]\n" + "".join(
            f"{k} = {_toml_value(v)}\n" for k, v in entry.items()
        )
    path = base / "admitted.toml"
    path.write_text(text, "utf-8")
    return path


@pytest.fixture
def macos_26(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "_macos_major", lambda: 26)


def test_admission_refuses_missing_contract(base: Path) -> None:
    with pytest.raises(SandboxError, match="не проверен под песочницей"):
        sandbox.admission_check(base / "nope.toml")


def test_admission_refuses_contract_without_records(
    base: Path, harness: tuple[Path, Path, Path]
) -> None:
    # штатное состояние до H2: каркас без записей — отказ, кандидат не запущен
    path_dir, _, marker = harness
    with pytest.raises(SandboxError, match="нет записей"):
        sandbox.admission_check(_contract(base), path=str(path_dir))
    assert not marker.exists()


def test_real_contract_is_readable_before_and_after_h2() -> None:
    # настоящий контракт разбирается читателем: до H2 — «нет записей»,
    # после H2 — валидные записи (формат каркаса совместим с читателем)
    try:
        records = sandbox.load_admitted(CONTRACT)
    except SandboxError as exc:
        assert "нет записей" in str(exc)
    else:
        assert records


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("schema = 2\n", "schema"),
        ("schema = true\n", "schema"),
        ("schema = 1.0\n", "schema"),
        ("admitted = []\n", "schema"),
        ("schema = 1\nadmitted = 3\n", "не массив"),
        ("schema = [\n", "контракт допуска"),
    ],
)
def test_admission_refuses_malformed_contract(
    base: Path, text: str, match: str
) -> None:
    (base / "c.toml").write_text(text)
    with pytest.raises(SandboxError, match=match):
        sandbox.admission_check(base / "c.toml")


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"evidence": None}, "ровно поля"),
        ({"extra": "x"}, "ровно поля"),
        ({"macos_major": "26"}, "macos_major: ждали int"),
        ({"macos_major": True}, "macos_major: ждали int"),
        ({"sha256": "ABC"}, "sha256: ждали 64 hex"),
        ({"profile_sha256": "0" * 63}, "profile_sha256: ждали 64 hex"),
        ({"realpath": "bin/claude"}, "не абсолютный"),
    ],
)
def test_admission_refuses_malformed_record(
    base: Path,
    harness: tuple[Path, Path, Path],
    override: dict[str, object],
    match: str,
) -> None:
    path_dir, real, marker = harness
    entry = {k: v for k, v in _entry(real, **override).items() if v is not None}
    with pytest.raises(SandboxError, match=match):
        sandbox.admission_check(_contract(base, entry), path=str(path_dir))
    assert not marker.exists()


def test_admission_refuses_harness_absent_from_path(
    base: Path, harness: tuple[Path, Path, Path]
) -> None:
    _, real, _ = harness
    with pytest.raises(SandboxError, match="не найден в PATH"):
        sandbox.admission_check(_contract(base, _entry(real)), path=str(base))


def test_admission_refuses_unknown_executable_without_running_it(
    base: Path, harness: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Z16: подменённый в PATH файл, которого нет в допуске — отказ, маркера
    # нет; ни одного запуска процесса до отказа
    path_dir, real, marker = harness
    calls: list[object] = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
    contract = _contract(
        base, _entry(real, realpath=str(base / "opt" / "other-claude"))
    )
    with pytest.raises(SandboxError, match=f"{real} нет в контракте"):
        sandbox.admission_check(contract, path=str(path_dir))
    assert not marker.exists()
    assert calls == []


def test_admission_ignores_records_of_other_harness(
    base: Path, harness: tuple[Path, Path, Path]
) -> None:
    path_dir, real, marker = harness
    contract = _contract(base, _entry(real, harness="codex"))
    with pytest.raises(SandboxError, match="нет в контракте"):
        sandbox.admission_check(contract, path=str(path_dir))
    assert not marker.exists()


@darwin_only
def test_admission_admits_exact_file_without_running_it(
    base: Path, harness: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # совпали все четыре поля: допуск с версией ИЗ ЗАПИСИ; кандидат не
    # исполнялся — маркера нет, единственный процесс — системный sw_vers
    path_dir, real, marker = harness
    argvs: list[list[str]] = []
    real_run = subprocess.run

    def spy(argv: list[str], **kwargs: object) -> object:
        argvs.append(list(argv))
        return real_run(argv, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(subprocess, "run", spy)
    major = int(
        real_run(
            ["/usr/bin/sw_vers", "-productVersion"], capture_output=True, text=True
        ).stdout.split(".")[0]
    )
    admission = sandbox.admission_check(
        _contract(base, _entry(real, macos_major=major)), path=str(path_dir)
    )
    assert admission.realpath == real
    assert admission.version == "9.9.9"
    assert not marker.exists()
    assert argvs == [["/usr/bin/sw_vers", "-productVersion"]]


@darwin_only
@pytest.mark.parametrize("field", ["sha256", "macos_major", "profile_sha256"])
def test_admission_refuses_mismatch_naming_field(
    base: Path, harness: tuple[Path, Path, Path], macos_26: None, field: str
) -> None:
    path_dir, real, marker = harness
    wrong: dict[str, object] = {
        "sha256": "0" * 64,
        "macos_major": 25,
        "profile_sha256": "f" * 64,
    }
    contract = _contract(base, _entry(real, **{field: wrong[field]}))
    with pytest.raises(SandboxError, match=f"не совпали {field}$"):
        sandbox.admission_check(contract, path=str(path_dir))
    assert not marker.exists()


@darwin_only
def test_admission_refuses_changed_content_of_admitted_path(
    base: Path, harness: tuple[Path, Path, Path], macos_26: None
) -> None:
    # тот же realpath, другое содержимое (обновление харнесса на месте)
    path_dir, real, marker = harness
    contract = _contract(base, _entry(real))
    real.write_text(real.read_text() + "# updated\n")
    with pytest.raises(SandboxError, match="не совпали sha256"):
        sandbox.admission_check(contract, path=str(path_dir))
    assert not marker.exists()


@darwin_only
def test_admission_picks_matching_record_among_several(
    base: Path, harness: tuple[Path, Path, Path], macos_26: None
) -> None:
    path_dir, real, _ = harness
    contract = _contract(
        base,
        _entry(real, version="1.0.0", macos_major=25),
        _entry(real, version="2.0.0"),
    )
    assert sandbox.admission_check(contract, path=str(path_dir)).version == "2.0.0"


@darwin_only
def test_admission_refuses_hardlinked_candidate(
    base: Path, harness: tuple[Path, Path, Path], macos_26: None
) -> None:
    # safe_io читает только st_nlink == 1: у двойника содержимое меняется мимо
    path_dir, real, _ = harness
    os.link(real, base / "twin")
    with pytest.raises(SandboxError, match="st_nlink"):
        sandbox.admission_check(_contract(base, _entry(real)), path=str(path_dir))


@pytest.mark.parametrize(
    "fail", [OSError(2, "нет sw_vers"), subprocess.TimeoutExpired("sw_vers", 10)]
)
def test_macos_major_failure_is_sandbox_error(
    monkeypatch: pytest.MonkeyPatch, fail: Exception
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise fail

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(SandboxError, match="версия macOS не определена"):
        sandbox._macos_major()


@darwin_only
def test_macos_major_reads_sw_vers() -> None:
    import platform

    assert sandbox._macos_major() == int(platform.mac_ver()[0].split(".")[0])


# --- оценка исходов пробы (§15, TASK-004) — переносимо ---------------------


def _all_denied() -> dict[str, str]:
    out = {name: "EPERM" for name in sandbox.PROBE_ITEMS}
    out.update({"allow:stage": "ok", "allow:tmp": "ok", "tty:open": "ENXIO"})
    out.update({f"keychain:{i}": "rc=44" for i in range(4)})
    out["keychain:canary"] = "rc=44"
    out.update(
        {
            "launchd:submit": "rc=1",
            "launchd:bootstrap": "rc=5",
            "launchservices:open": "rc=1",
            "apple-events": "timeout",
        }
    )
    return out


def _eval(**override: str) -> sandbox.ProbeResult:
    return sandbox._evaluate(
        {**_all_denied(), **override}, sandbox.PROBE_ITEMS, None, ()
    )


def test_evaluate_all_denied_is_ok_with_apple_events_note() -> None:
    result = _eval()
    assert result.ok, result.failures
    assert result.notes == ("apple-events: нет ответа (закрытие не доказано, §17)",)


@pytest.mark.parametrize(
    ("item", "got", "expected"),
    [
        ("stand:config", "ok", "stand:config: удалось — регрессия профиля"),
        ("stand:config", "ENOENT", "stand:config: ENOENT — сбой подготовки"),
        ("stand:config", "EACCES", "stand:config: EACCES — сбой подготовки"),
        ("allow:stage", "EPERM", "allow:stage: базовая половина не прошла (EPERM)"),
        ("tty:open", "ok", "tty:open: ok"),
        ("cron:exec", "rc=1", "cron:exec: rc=1 — не действует"),
        ("cron:exec", "rc=0", "cron:exec: rc=0 — не действует"),
        ("keychain:2", "rc=0", "keychain:2: rc=0 — не действует"),
        ("keychain:canary", "rc=0", "keychain:canary: rc=0 — не действует"),
        ("launchd:submit", "timeout", "launchd:submit: timeout — не действует"),
        (
            "launchd:bootstrap",
            "prep:EPERM",
            "launchd:bootstrap: EPERM — сбой подготовки",
        ),
        ("apple-events", "rc=0", "apple-events: ответили — не действует"),
    ],
)
def test_evaluate_names_each_failure(item: str, got: str, expected: str) -> None:
    assert _eval(**{item: got}).failures == (expected,)


@pytest.mark.parametrize("got", ["ENXIO", "EPERM", "EACCES"])
def test_evaluate_tty_accepts_each_belt(got: str) -> None:
    assert _eval(**{"tty:open": got}).ok


def test_evaluate_missing_item_env_and_effects() -> None:
    out = _all_denied()
    del out["read:bait"]
    result = sandbox._evaluate(
        out,
        sandbox.PROBE_ITEMS,
        "env: лишние ['GH_TOKEN'], недостающие []",
        ("submit",),
    )
    assert result.failures == (
        "read:bait: исхода нет — пункт не исполнен",
        "env: лишние ['GH_TOKEN'], недостающие []",
        "эффект вне песочницы: submit",
    )


def test_env_mismatch_is_exact_set_equality() -> None:
    env = {"PATH": "/bin", "HOME": "/h"}
    assert sandbox._env_mismatch(0, b"PATH=/bin\0HOME=/h\0", env) is None
    assert sandbox._env_mismatch(0, b"PATH=/bin\0HOME=/h\0X=1\0", env) == (
        "env: лишние ['X'], недостающие []"
    )
    assert sandbox._env_mismatch(0, b"PATH=/bin\0", env) == (
        "env: лишние [], недостающие ['HOME']"
    )
    assert sandbox._env_mismatch(None, b"", env) == (
        "env: проба окружения не прошла (код None)"
    )


def test_probe_inner_timeouts_fit_outer_budget() -> None:
    # сумма внутренних потолков меньше внешнего: отказ называется пунктом
    calls = len(re.findall(r"(?<![\w.])run\(", sandbox.PROBE_SCRIPT)) - 1  # def
    steps = calls + len(sandbox.KEYCHAIN_PROBES) - 1  # цикл связки — один вызов
    assert steps == 10
    inner = (steps - 1) * sandbox.PROBE_STEP_TIMEOUT + sandbox.APPLE_EVENTS_TIMEOUT
    assert inner + 10 < sandbox.PROBE_TIMEOUT


# --- граница запуска авторского агента (TASK-005, §11–§15, §19 п.1) ----------
#
# Переносимые двойники Darwin-условий (ОС, `sandbox-exec`, вложенность) и
# допуска; настоящие Darwin-условия — тесты с `darwin_only` ниже и живые в
# `tests/test_governance_sandbox_live.py`.

_ADMISSION = sandbox.Admission(
    "claude", "2.1.296", Path("/opt/claude/bin/claude"), "0" * 64, 26, "0" * 64, "e"
)


@pytest.fixture
def launch(base: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Граница с двойниками всех условий §13: каждое проходит, пока тест его
    не сломает; запуск агента и проба — шпионы."""
    for name in ("AUTHOR_HARNESS", "AUTHOR_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AUTHOR_HARNESS", "claude")
    monkeypatch.setenv("AI_PROSTO_HARNESS_ENV", str(base / "no-harness.env"))
    monkeypatch.setenv("XDG_STATE_HOME", str(base / "xdg"))
    monkeypatch.setattr(author_launch, "_os_name", lambda: "darwin")
    monkeypatch.setattr(author_launch, "_has_sandbox_exec", lambda: True)
    monkeypatch.setattr(author_launch, "_nested_sandbox", lambda: None)
    monkeypatch.setattr(
        sandbox, "admission_check", lambda contract, harness: _ADMISSION
    )
    monkeypatch.setattr(author_launch, "author_claude_token", lambda: "tok")
    ns = SimpleNamespace(spawned=[], probes=[], probe=sandbox.ProbeResult((), ()))

    def fake_probe(call, profile, env, targets, **kw):
        ns.probes.append((call.root, call.root.is_dir(), dict(env), targets))
        return ns.probe

    def fake_agent(argv, *, env, cwd, timeout):
        ns.spawned.append((argv, dict(env), cwd))
        return 0

    monkeypatch.setattr(sandbox, "run_probe", fake_probe)
    monkeypatch.setattr(author_launch, "_run_agent", fake_agent)
    target = _repo(base / "target")
    run_dir = base / "runs" / "r1"
    run_dir.mkdir(parents=True)
    ns.base, ns.target, ns.run_dir = base, target, run_dir
    ns.calls = base / "xdg" / "devtools" / "agent-calls"
    ns.call = author_launch.AuthorCall(
        "r1", str(run_dir), str(target), "ws/spec", "10-requirements.md"
    )
    return ns


def _launch(ns: SimpleNamespace, **kw: object) -> author_launch.LaunchOutcome:
    return author_launch.launch_author(ns.call, lambda out: f"prompt {out}", **kw)


def _refused_before_call_dir(ns: SimpleNamespace, outcome, text: str) -> None:
    assert outcome.code == author_launch.REFUSED and text in outcome.reason
    assert ns.spawned == [] and ns.probes == []
    assert not ns.calls.exists(), "каталог вызова создан при отказе до пробы"
    assert not (ns.target / "ws").exists(), "граница тронула бандл"


@pytest.mark.parametrize(
    ("attr", "value", "text"),
    [
        ("_os_name", lambda: "linux", "только на macOS"),
        ("_has_sandbox_exec", lambda: False, "нет /usr/bin/sandbox-exec"),
        (
            "_nested_sandbox",
            lambda: "вложенная песочница: sandbox_apply: Operation not permitted",
            "вложенная песочница",
        ),
    ],
    ids=["not-macos", "no-sandbox-exec", "nested"],
)
def test_launch_refuses_unsupported_machine(launch, monkeypatch, attr, value, text):
    monkeypatch.setattr(author_launch, attr, value)
    _refused_before_call_dir(launch, _launch(launch), text)


@pytest.mark.parametrize("source", ["env", "harness.env", "default"])
def test_launch_refuses_non_claude_harness_verbatim(launch, monkeypatch, source):
    """§13 Q1 = (а): харнесс ≠ claude — отказ дословно, откуда бы он ни
    пришёл; вшитое умолчание резолвера — codex и НЕ подменяется claude."""
    monkeypatch.delenv("AUTHOR_HARNESS")
    if source == "env":
        monkeypatch.setenv("AUTHOR_HARNESS", "codex")
    elif source == "harness.env":
        cfg = launch.base / "harness.env"
        cfg.write_text("AUTHOR_HARNESS=codex\n", encoding="utf-8")
        monkeypatch.setenv("AI_PROSTO_HARNESS_ENV", str(cfg))
    outcome = _launch(launch)
    assert outcome.reason == (
        "харнесс `codex` не поддерживается песочницей авторинга v1; "
        "поддерживается claude (`AUTHOR_HARNESS=claude`)"
    )
    _refused_before_call_dir(launch, outcome, "codex")


def test_launch_checks_harness_before_admission(launch, monkeypatch):
    def admission(contract, harness):
        raise AssertionError("допуск проверен раньше харнесса")

    monkeypatch.setattr(sandbox, "admission_check", admission)
    monkeypatch.setenv("AUTHOR_HARNESS", "gemini")
    _refused_before_call_dir(launch, _launch(launch), "харнесс `gemini`")


def test_launch_refuses_empty_admission_contract(launch, monkeypatch):
    """Штатное состояние до шага H2: допуск пуст — отказ для claude тоже."""
    monkeypatch.undo()  # настоящий admission_check, остальные двойники заново
    contract = launch.base / "admitted.toml"
    contract.write_text("schema = 1\n", encoding="utf-8")
    monkeypatch.setenv("AUTHOR_HARNESS", "claude")
    monkeypatch.setenv("AI_PROSTO_HARNESS_ENV", str(launch.base / "none"))
    monkeypatch.setenv("XDG_STATE_HOME", str(launch.base / "xdg"))
    monkeypatch.setattr(author_launch, "_os_name", lambda: "darwin")
    monkeypatch.setattr(author_launch, "_has_sandbox_exec", lambda: True)
    monkeypatch.setattr(author_launch, "_nested_sandbox", lambda: None)
    monkeypatch.setattr(sandbox, "run_probe", _never("проба"))
    monkeypatch.setattr(author_launch, "_run_agent", _never("агент"))
    outcome = _launch(launch, contract=contract)
    _refused_before_call_dir(launch, outcome, "не проверен под песочницей")


def test_launch_refuses_without_keychain_token(launch, monkeypatch):
    def missing() -> str:
        raise author_launch.AgentIsolationError("нет элемента связки")

    monkeypatch.setattr(author_launch, "author_claude_token", missing)
    _refused_before_call_dir(launch, _launch(launch), "нет элемента связки")


@pytest.mark.parametrize("bundle", ["../out", "ws/.git/x", "/abs", ".", ""])
def test_launch_refuses_bad_bundle_dir(launch, bundle):
    launch.call = dataclasses.replace(launch.call, bundle_dir=bundle)
    outcome = _launch(launch)
    assert outcome.code == author_launch.REFUSED and outcome.reason
    assert launch.spawned == [] and launch.probes == []
    assert not launch.calls.exists()


def test_launch_refuses_non_canonical_run_dir(launch):
    launch.call = dataclasses.replace(launch.call, run_dir="runs/r1")
    _refused_before_call_dir(launch, _launch(launch), "не канонический")


def test_refuse_disp_names_v1_scope():
    outcome = author_launch.refuse_disp()
    assert outcome.code == author_launch.REFUSED
    assert "disp вне v1" in outcome.reason


def _never(what: str):
    def boom(*a: object, **k: object) -> None:
        raise AssertionError(f"{what} вызван")

    return boom


@darwin_only
def test_launch_probe_failure_creates_and_removes_call_dir(launch):
    """Отказ пробы: каталог вызова создан ДЛЯ пробы и удалён до возврата;
    агент не запущен, бандл не тронут."""
    launch.probe = sandbox.ProbeResult(("sentinel:target_dir: OK — регрессия",), ())
    outcome = _launch(launch)
    assert outcome.code == author_launch.REFUSED
    assert "проба песочницы не прошла" in outcome.reason
    assert "sentinel:target_dir" in outcome.reason
    root, existed, _, targets = launch.probes[0]
    assert existed and not root.exists()
    assert launch.spawned == []
    assert targets.run_dir == launch.run_dir and targets.target_dir == launch.target
    assert not (launch.target / "ws").exists()


@darwin_only
def test_launch_probe_and_agent_share_profile_env_and_call_dir(launch):
    outcome = _launch(launch)
    assert outcome.code == author_launch.REFUSED, "агент-шпион файла не пишет"
    (root, _, probe_env, targets) = launch.probes[0]
    argv, agent_env, cwd = launch.spawned[0]
    assert probe_env == agent_env
    assert argv[:3] == [sandbox.SANDBOX_EXEC, "-f", str(root / "profile.sb")]
    assert argv[3] == str(_ADMISSION.realpath)
    assert argv[-1] == f"prompt {root / 'stage' / '10-requirements.md'}"
    assert cwd == launch.target
    assert agent_env["TMPDIR"] == agent_env["CLAUDE_CODE_TMPDIR"] == str(root / "tmp")
    assert agent_env["CLAUDE_CONFIG_DIR"] == str(root / "claude-config")
    assert agent_env["CLAUDE_CODE_OAUTH_TOKEN"] == "tok"
    assert agent_env["SSL_CERT_FILE"] == "/etc/ssl/cert.pem"
    assert targets.bundle_dir == PurePosixPath("ws/spec")
    assert root.parent == launch.calls / "r1"
    assert not root.exists()


def _agent_writes(launch, monkeypatch, files: dict[str, bytes], code: int = 0):
    seen: dict[str, object] = {}

    def agent(argv, *, env, cwd, timeout):
        stage = Path(env["CLAUDE_CONFIG_DIR"]).parent / "stage"
        seen["settings"] = (
            Path(env["CLAUDE_CONFIG_DIR"]) / "settings.json"
        ).read_text()
        for rel, data in files.items():
            (stage / rel).parent.mkdir(parents=True, exist_ok=True)
            (stage / rel).write_bytes(data)
        return code

    monkeypatch.setattr(author_launch, "_run_agent", agent)
    return seen


@darwin_only
def test_launch_transfers_the_node_file_into_the_bundle(launch, monkeypatch):
    """N6: бандла и его родителей нет — файл перенесён безопасной записью;
    claude-config несёт свой settings.json; каталог вызова удалён."""
    seen = _agent_writes(launch, monkeypatch, {"10-requirements.md": b"node\n"})
    assert _launch(launch) == author_launch.LaunchOutcome(0)
    assert (launch.target / "ws/spec/10-requirements.md").read_bytes() == b"node\n"
    assert json.loads(str(seen["settings"])) == {
        "permissions": {"defaultMode": "bypassPermissions"}
    }
    assert list((launch.calls / "r1").iterdir()) == []


@darwin_only
def test_launch_extra_in_stage_is_quarantine_list_without_transfer(launch, monkeypatch):
    _agent_writes(
        launch,
        monkeypatch,
        {"10-requirements.md": b"node\n", "x/hook.json": b"{}"},
    )
    outcome = _launch(launch)
    assert outcome.code == author_launch.REFUSED
    assert outcome.quarantine == ("x (dir)", "x/hook.json (file)")
    assert not (launch.target / "ws/spec/10-requirements.md").exists()


@darwin_only
@pytest.mark.parametrize(
    ("code", "files", "text"),
    [
        (3, {"10-requirements.md": b"n"}, "агент вернул 3"),
        (None, {"10-requirements.md": b"n"}, "таймаут"),
        (0, {}, "не записал файл узла"),
    ],
    ids=["nonzero", "timeout", "no-file"],
)
def test_launch_failed_call_does_not_transfer(launch, monkeypatch, code, files, text):
    _agent_writes(launch, monkeypatch, files, code=code)  # type: ignore[arg-type]
    outcome = _launch(launch)
    assert not outcome.ok and text in outcome.reason
    assert not (launch.target / "ws/spec/10-requirements.md").exists()


@darwin_only
def test_two_runs_on_one_target_keep_their_own_call_dirs_and_sentinels(
    launch, monkeypatch
):
    """Два прогона с одинаковыми target_dir/bundle_dir: каталоги вызова,
    часовые пробы и диагностика — каждый своего прогона."""
    runs = {}
    for run_id in ("ra", "rb"):
        run_dir = launch.base / "runs" / run_id
        run_dir.mkdir(parents=True)
        call = dataclasses.replace(launch.call, run_id=run_id, run_dir=str(run_dir))
        launch.probe = sandbox.ProbeResult((f"sentinel:run_dir {run_dir}: OK",), ())
        runs[run_id] = (
            run_dir,
            author_launch.launch_author(call, lambda out: out),
        )
    for (run_dir, outcome), (root, _, _, targets) in zip(
        runs.values(), launch.probes, strict=True
    ):
        assert targets.run_dir == run_dir
        assert root.parent.name == run_dir.name
        assert str(run_dir) in outcome.reason
        assert (run_dir / "edge-check").is_dir()


# --- политика окружения и резолвер харнесса (переехали из ops, TASK-005) ------

_OPERATOR_ENV = {
    "PATH": "/usr/bin",
    "HOME": "/h",
    "USER": "u",
    "LOGNAME": "u",
    "SHELL": "/bin/zsh",
    "LANG": "C.UTF-8",
    "TERM": "xterm",
    "TMPDIR": "/t/",
    "LC_ALL": "C.UTF-8",
    "XDG_STATE_HOME": "/h/.state",
    "ANTHROPIC_API_KEY": "own-model-key",
    # чужая XDG_-переменная — префиксом не проходит (ревью #483)
    "XDG_SESSION_SECRET": "x",
    # то, что агенту попадать НЕ должно (F12 дизайна)
    "GH_TOKEN": "x",
    "GITHUB_TOKEN": "x",
    "GH_CONFIG_DIR": "/h/.config/review",
    "OPENAI_API_KEY": "x",
    "MS_CLIENT_SECRET": "x",
    "SPEC_RUNNER_TELEGRAM_TOKEN": "x",
    "CLAUDECODE": "1",
    "CLAUDE_CODE_MESSAGING_TOKEN": "x",
    "CLAUDE_CODE_OAUTH_TOKEN": "inherited-token",
    "AUTHOR_HARNESS": "codex",
}
_AGENT_SUBSET = {
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "TERM",
    "TMPDIR",
    "LC_ALL",
    "XDG_STATE_HOME",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
}


def test_agent_env_is_exact_allowlist_subset():
    """Секреты сессии оператора агенту не наследуются: env РАВЕН
    allowlist-подмножеству (равенство, не поиск трёх имён)."""
    expected = {k: v for k, v in _OPERATOR_ENV.items() if k in _AGENT_SUBSET}
    assert author_launch.agent_env(_OPERATOR_ENV) == expected


def test_call_env_overrides_tmp_config_token_and_ca(monkeypatch):
    """§12: allowlist + корни каталога вызова, токен — из связки (не
    унаследованный), CA — системный."""
    for name in list(os.environ):
        monkeypatch.delenv(name, raising=False)
    for name, value in _OPERATOR_ENV.items():
        monkeypatch.setenv(name, value)
    call = CallDir(Path("/calls/r/c"))
    env = author_launch.call_env(call, "tok-from-keychain")
    assert env == {
        **{k: v for k, v in _OPERATOR_ENV.items() if k in _AGENT_SUBSET},
        "TMPDIR": "/calls/r/c/tmp",
        "CLAUDE_CODE_TMPDIR": "/calls/r/c/tmp",
        "CLAUDE_CONFIG_DIR": "/calls/r/c/claude-config",
        "CLAUDE_CODE_OAUTH_TOKEN": "tok-from-keychain",
        "SSL_CERT_FILE": "/etc/ssl/cert.pem",
    }
    monkeypatch.setenv("SSL_CERT_FILE", "/opt/corp/ca.pem")
    assert author_launch.call_env(call, "t")["SSL_CERT_FILE"] == "/opt/corp/ca.pem"


def test_agent_env_names_are_pinned():
    """Перечень пинуется литералом (ревью #483): расширение allowlist'а —
    осознанная правка защищённого теста с обоснованием."""
    assert author_launch.AGENT_ENV_NAMES == {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TERM",
        "TMPDIR",
        "LANG",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_MODEL",
        "MAX_THINKING_TOKENS",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "AWS_REGION",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "CLOUD_ML_REGION",
        "ANTHROPIC_VERTEX_PROJECT_ID",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "NODE_EXTRA_CA_CERTS",
    }
    assert author_launch.AGENT_ENV_PREFIXES == ("LC_",)
    assert "OPENAI_API_KEY" not in author_launch.AGENT_ENV_NAMES


def test_agent_env_carries_the_reviewer_model_transport():
    """Транспорт модели авторского агента не отстаёт от ревьюера edge-check."""
    from governance.edge_check import reviewer

    assert set(reviewer._ENV_ALLOWLIST) <= author_launch.AGENT_ENV_NAMES


def _harness_cfg(base: Path, monkeypatch, text: str | None) -> None:
    path = base / "harness.env"
    if text is not None:
        path.write_text(text, encoding="utf-8")
    monkeypatch.setenv("AI_PROSTO_HARNESS_ENV", str(path))
    for name in ("AUTHOR_HARNESS", "AUTHOR_MODEL"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("cfg", "env", "expected"),
    [
        (None, {}, ("codex", "")),
        ("AUTHOR_HARNESS=claude\nAUTHOR_MODEL=m1\n", {}, ("claude", "m1")),
        ("  export AUTHOR_HARNESS=claude\n", {}, ("claude", "")),
        # урок ревью PR #121: харнесс env не наследует модель файла
        (
            "AUTHOR_HARNESS=claude\nAUTHOR_MODEL=m1\n",
            {"AUTHOR_HARNESS": "codex"},
            ("codex", ""),
        ),
        ("AUTHOR_HARNESS=codex\n", {"AUTHOR_MODEL": "m2"}, ("codex", "m2")),
    ],
    ids=["default-codex", "file", "export", "env-harness-own-model", "env-model"],
)
def test_harness_resolver_layers(base, monkeypatch, cfg, env, expected):
    _harness_cfg(base, monkeypatch, cfg)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert author_launch.harness_for("AUTHOR") == expected


def test_author_argv_runs_admitted_binary_with_pinned_flags():
    argv = author_launch.author_argv("/opt/claude", "", "PROMPT")
    assert argv == [
        "/opt/claude",
        "-p",
        "--model",
        "claude-opus-5",
        "--dangerously-skip-permissions",
        "--no-session-persistence",
        "--strict-mcp-config",
        "PROMPT",
    ]


@pytest.mark.parametrize(
    ("rc", "stdout", "ok"),
    [(0, "tok\n", True), (44, "", False), (0, "  \n", False)],
    ids=["found", "missing-item", "empty-value"],
)
def test_author_claude_token_reads_the_named_keychain_item(monkeypatch, rc, stdout, ok):
    """Спрашивается именно элемент AUTHOR_CLAUDE_TOKEN_ITEM; отсутствие и
    пустое значение — AgentIsolationError, а не пустой токен в env агента."""
    seen: list[list[str]] = []

    def fake_run(argv, **kwargs):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, rc, stdout=stdout, stderr="")

    monkeypatch.setattr(author_launch.subprocess, "run", fake_run)
    if ok:
        assert author_launch.author_claude_token() == "tok"
    else:
        with pytest.raises(author_launch.AgentIsolationError):
            author_launch.author_claude_token()
    assert seen == [
        [
            "security",
            "find-generic-password",
            "-s",
            author_launch.AUTHOR_CLAUDE_TOKEN_ITEM,
            "-w",
        ]
    ]


# --- корректность конфигурации на общих входах (§19 п.1) ---------------------


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_check_config_accepts_any_known_harness_without_machine_checks(
    base, monkeypatch, harness
):
    """Общие входы не требуют готовности к авторингу: ни ОС, ни допуск, ни
    «харнесс = claude» не проверяются — на не-git цели тоже."""
    _harness_cfg(base, monkeypatch, None)
    monkeypatch.setenv("AUTHOR_HARNESS", harness)
    monkeypatch.setattr(author_launch, "_os_name", _never("ОС"))
    monkeypatch.setattr(sandbox, "admission_check", _never("допуск"))
    (base / "t").mkdir()
    assert author_launch.check_config(str(base / "t"), "ws/spec", "codex") is None
    assert author_launch.check_config(str(_repo(base / "g")), "ws/spec", "disp") is None


@pytest.mark.parametrize(
    ("harness", "backend", "bundle", "text"),
    [
        ("gemini", "codex", "ws/spec", "неизвестный AUTHOR_HARNESS"),
        ("claude", "magic", "ws/spec", "неизвестный author_backend"),
        ("claude", "codex", "../x", ".."),
        ("claude", "codex", "ws/.git", ".git"),
    ],
)
def test_check_config_refuses_unrecognised_values(
    base, monkeypatch, harness, backend, bundle, text
):
    _harness_cfg(base, monkeypatch, None)
    monkeypatch.setenv("AUTHOR_HARNESS", harness)
    target = _repo(base / "g")
    reason = author_launch.check_config(str(target), bundle, backend)
    assert reason is not None and text in reason


# --- `_run_agent`: новая сессия, SIGKILL группе (§12, Z13) --------------------


def test_run_agent_kills_the_group_after_exit(base):
    pidfile = base / "pid"
    code = author_launch._run_agent(
        ["/bin/sh", "-c", f"sleep 30 & echo $! > {pidfile}; exit 0"],
        env={"PATH": "/usr/bin:/bin"},
        cwd=base,
        timeout=10,
    )
    assert code == 0
    _assert_dead(int(pidfile.read_text()))


def test_run_agent_timeout_kills_the_group(base):
    pidfile = base / "pid"
    started = time.monotonic()
    code = author_launch._run_agent(
        ["/bin/sh", "-c", f"sleep 30 & echo $! > {pidfile}; sleep 30"],
        env={"PATH": "/usr/bin:/bin"},
        cwd=base,
        timeout=1,
    )
    assert code is None and time.monotonic() - started < 10
    _assert_dead(int(pidfile.read_text()))


def _assert_dead(pid: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    raise AssertionError(f"потомок {pid} пережил SIGKILL группе")


# --- процедура допуска `make sandbox-admit` (TASK-006) ------------------------
#
# Фикстура — сырой stream-json claude 2.1.296 (замер 2026-10-12, haiku, без
# песочницы), только события assistant/user; пути заменены на /CALL, /TARGET.
# Форма — та, что разбирает процедура: is_error + «Exit code N» у Bash, у
# удавшегося Write поля is_error нет вовсе.

_STREAM = [
    '{"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_01Aq1Vzx6bCEFXfwHEgHG6MF", "name": "Bash", "input": {"command": "true", "description": "Run the true command"}}]}}',
    '{"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_01Aq1Vzx6bCEFXfwHEgHG6MF", "content": "(Bash completed with no output)", "is_error": false}]}}',
    '{"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_01WCYVP2iUTWB9YiqTUzzg7T", "name": "Bash", "input": {"command": "false", "description": "Run the false command"}}]}}',
    '{"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_01WCYVP2iUTWB9YiqTUzzg7T", "content": "Exit code 1", "is_error": true}]}}',
    '{"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_01XrwJ965LK7YxUaHy2W7TFH", "name": "Bash", "input": {"command": "exit 3", "description": "Run exit 3"}}]}}',
    '{"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_01XrwJ965LK7YxUaHy2W7TFH", "content": "Exit code 3", "is_error": true}]}}',
    '{"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_01RjsmpGqznFhXQpLvNfBhGz", "name": "Bash", "input": {"command": "cd src", "description": "Change directory into src"}}]}}',
    '{"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_01RjsmpGqznFhXQpLvNfBhGz", "content": "(Bash completed with no output)", "is_error": false}]}}',
    '{"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_01L3bgQFZeppvMDwDoHnSYUB", "name": "Bash", "input": {"command": "pwd", "description": "Print current working directory"}}]}}',
    '{"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_01L3bgQFZeppvMDwDoHnSYUB", "content": "/TARGET/src", "is_error": false}]}}',
    '{"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_01784M7joZnauU3UcbEMLbWd", "name": "Write", "input": {"file_path": "/CALL/stage/a/b/node.md", "content": "admit-probe"}}]}}',
    '{"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_01784M7joZnauU3UcbEMLbWd", "content": "File created successfully at: /CALL/stage/a/b/node.md (file state is current in your context — no need to Read it back)"}]}}',
    '{"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_01YakDTLCrqr7VJdmWoPctZq", "name": "Write", "input": {"file_path": "/TARGET/ws/spec/escape.md", "content": "x"}}]}}',
    '{"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_01YakDTLCrqr7VJdmWoPctZq", "content": "EPERM: operation not permitted, open \'/TARGET/ws/spec/escape.md\'", "is_error": true}]}}',
    '{"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "DONE"}]}}',
]
_EXPECT = sandbox.admit_expectations(
    "/CALL/stage/a/b/node.md", "/TARGET/ws/spec/escape.md", "/TARGET/src"
)


def test_parse_stream_json_pairs_uses_with_raw_results():
    calls = sandbox.parse_stream_json(["not json", *_STREAM, '{"type":"result"}'])
    assert [(c.tool, c.is_error) for c in calls] == [
        ("Bash", False),
        ("Bash", True),
        ("Bash", True),
        ("Bash", False),
        ("Bash", False),
        ("Write", False),
        ("Write", True),
    ]
    assert calls[2].content == "Exit code 3"
    assert calls[4].content == "/TARGET/src"


def test_recorded_transcript_passes_the_admission_checks():
    calls = sandbox.parse_stream_json(_STREAM)
    assert sandbox.check_transcript(calls, _EXPECT) == []


def _edited(index: int, **result: object) -> list[str]:
    events = [json.loads(line) for line in _STREAM]
    events[index]["message"]["content"][0].update(result)
    return [json.dumps(e) for e in events]


@pytest.mark.parametrize(
    ("index", "change", "text"),
    [
        (3, {"content": "Exit code 2"}, "Bash false: вывод"),
        (5, {"content": "ok", "is_error": False}, "Bash exit 3: is_error=False"),
        (9, {"content": "/TARGET"}, "Bash pwd: вывод"),
        (
            13,
            {"content": "File created", "is_error": False},
            "escape.md: is_error=False",
        ),
        (11, {"content": "EPERM", "is_error": True}, "node.md: is_error=True"),
    ],
    ids=["false-code", "exit3-ok", "cd-lost", "escape-written", "stage-denied"],
)
def test_transcript_deviation_is_named(index, change, text):
    failures = sandbox.check_transcript(
        sandbox.parse_stream_json(_edited(index, **change)), _EXPECT
    )
    assert any(text in f for f in failures), failures


def test_missing_result_skipped_and_extra_steps_fail():
    # нет tool_result — исход не доказан: считается ошибкой, Write в
    # заготовку тогда «не удался»
    no_result = _STREAM[:11] + _STREAM[12:]
    failures = sandbox.check_transcript(sandbox.parse_stream_json(no_result), _EXPECT)
    assert any("node.md: is_error=True" in f for f in failures), failures
    skipped = sandbox.parse_stream_json(_STREAM[2:])
    assert "Bash true: шаг не исполнен" in sandbox.check_transcript(skipped, _EXPECT)
    extra = [*_STREAM, _STREAM[0].replace("toolu_", "toolu_X")]
    failures = sandbox.check_transcript(sandbox.parse_stream_json(extra), _EXPECT)
    assert any(f.startswith("лишний вызов: Bash true") for f in failures), failures


def _record(evidence: str = "e") -> sandbox.Admission:
    return sandbox.Admission(
        "claude", "2.1.296", Path("/opt/claude"), "a" * 64, 26, "b" * 64, evidence
    )


def test_admission_record_round_trips_through_the_contract_loader(base):
    record = _record("docs/evidence/2026-10-12-author-sandbox-admit-2.1.296.md")
    contract = base / "admitted.toml"
    contract.write_text("schema = 1\n\n" + sandbox.admission_record(record))
    assert sandbox.load_admitted(contract) == (record,)


@pytest.mark.parametrize("failures", [[], ["Bash pwd: вывод '/x'"]])
def test_admit_protocol_proposes_record_only_when_passed(failures):
    text = sandbox.render_admit_protocol(
        _record(),
        date="2026-10-12",
        probe=sandbox.ProbeResult((), ("apple-events: нет ответа",)),
        failures=failures,
        calls=sandbox.parse_stream_json(_STREAM),
    )
    assert "Exit code 3" in text and "нет ответа" in text
    assert ("[[admitted]]" in text) is (not failures)
    assert ("НЕ ПРОШЛО" in text) is bool(failures)


def test_makefile_names_sandbox_admit():
    makefile = (DEVTOOLS_ROOT / "Makefile").read_text()
    target = "sandbox-admit: ; @uv run --frozen python -m governance.sandbox admit"
    assert target in makefile
    assert "make sandbox-admit" in makefile  # справка


def test_admit_refuses_off_macos_without_writing_anything(base, monkeypatch):
    """Процедура только для macOS; отказ — код 2, протокола нет, `contracts/`
    процедура не пишет никогда (запись вносит оператор, шаг H2)."""
    contract = DEVTOOLS_ROOT / "contracts/author-sandbox/v1/admitted.toml"
    before = contract.read_bytes()
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    code = sandbox.admit(
        harness_path=None, model="m", evidence_dir=base / "ev", date="2026-10-12"
    )
    assert code == 2
    assert not (base / "ev").exists()
    assert contract.read_bytes() == before
