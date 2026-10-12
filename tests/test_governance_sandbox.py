"""Каталог вызова, профиль Seatbelt и допуск харнесса (TASK-002, TASK-003).

Дизайн `docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md`
§11, §12, §14; сценарии §16 Z6, Z7, Z16. Генератор профиля — чистая функция, его
тесты переносимые; создание каталога вызова идёт через `safe_io`
(`O_NOFOLLOW_ANY`) и проверяется только на macOS. Поведение профиля под
настоящим `sandbox-exec` — TASK-004 (`tests/test_governance_sandbox_live.py`).
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

import pytest

from governance import sandbox
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
