"""Проба-канарейка на настоящем `sandbox-exec` (TASK-004).

Дизайн `docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md`
§4, §15, F14–F21, F28; сценарии §16 Z1–Z5, Z8–Z10, Z18, Z19, N6. Только
macOS с `/usr/bin/sandbox-exec`; иначе — skip с названной причиной.

Каждый запрет доказан двумя половинами: под профилем `run_probe` зелёная, а
та же проба без обёртки (контроль) — красная по тому же пункту, с эффектом
снаружи (задание launchd исполнилось, `.app` открылся, Apple Events
ответили). Пробы трогают машину оператора: задания launchd, приманка в
`~/.config/ai-prosto/`, часовые с UUID-именами в `$HOME` — всё убирается в
`finally` самой пробы, и тесты это проверяют.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

import pytest

from governance import sandbox
from governance.sandbox import CallDir, ProbeResult, ProbeTargets

DEVTOOLS_ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    not (sys.platform == "darwin" and os.path.exists(sandbox.SANDBOX_EXEC)),
    reason="проба песочницы — только macOS с /usr/bin/sandbox-exec",
)


def _git(*args: str | Path) -> None:
    subprocess.run(["git", *map(str, args)], check=True, capture_output=True)


class Bench:
    """Цель (worktree: gitdir ≠ common dir), каталог прогона, вызов."""

    def __init__(self, base: Path, target: Path | None = None) -> None:
        self.base = base
        if target is None:
            main = base / "main"
            _git("init", "-q", main)
            _git(
                "-C",
                main,
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
            target = base / "target"
            _git("-C", main, "worktree", "add", "-q", "--detach", target)
        self.target = target
        self.git_dir, self.common_dir = sandbox.target_git_dirs(target)
        self.run_dir = base / "run"
        (self.run_dir / "edge-check").mkdir(parents=True)
        (self.run_dir / "ledger.jsonl").write_text("")
        self.venv = base / "venv-disputatio"
        self.venv.mkdir()
        calls = sandbox.calls_root({"XDG_STATE_HOME": str(base / "state")})
        self.call: CallDir = sandbox.create_call_dir(
            calls,
            "run-1",
            forbidden=sandbox.forbidden_roots(
                target, run_dir=self.run_dir, devtools_root=base / "devtools"
            ),
        )
        # часовые прошлых упавших прогонов — не след этой пробы
        self.stale = set(_sentinels_under(*self.sentinel_dirs()))
        self.env = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(Path.home()),
            "LANG": "en_US.UTF-8",
            "TMPDIR": str(self.call.tmp),
        }

    def sentinel_dirs(self) -> tuple[Path, ...]:
        return (
            Path.home(),
            self.target,
            self.run_dir,
            self.run_dir / "edge-check",
            self.git_dir,
            self.common_dir,
            self.venv,
            self.target / "workstreams" / "w1" / "bundle",
        )

    def profile(self) -> str:
        return sandbox.render_profile(self.call, home=Path.home())

    def targets(
        self, bundle: str = "workstreams/w1/bundle", extra: tuple[Path, ...] = ()
    ) -> ProbeTargets:
        return ProbeTargets(
            target_dir=self.target,
            bundle_dir=PurePosixPath(bundle),
            git_dir=self.git_dir,
            common_dir=self.common_dir,
            run_dir=self.run_dir,
            home=Path.home(),
            extra=(self.venv, *extra),
        )

    def probe(self, profile: str | None = None, **kw: object) -> ProbeResult:
        return sandbox.run_probe(
            self.call,
            self.profile() if profile is None else profile,
            self.env,
            self.targets(),
            **kw,  # type: ignore[arg-type]
        )


@pytest.fixture
def bench(tmp_path: Path) -> Bench:
    return Bench(Path(os.path.realpath(tmp_path)))


def _names(result: ProbeResult) -> set[str]:
    return {f.split(": ", 1)[0] for f in result.failures}


def _probe_labels(wait: float = 10.0) -> list[str]:
    """Записи launchd пробы (задания и процессы `.app`), ждём их ухода.

    Снятие задания асинхронно, а открытый в контроле `.app` стартует и
    завершается с задержкой (§15: эффект может прийти позже).
    """
    deadline = time.monotonic() + wait
    while True:
        out = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, check=False
        ).stdout
        left = [line for line in out.splitlines() if "sandbox-probe." in line]
        if not left or time.monotonic() > deadline:
            return left
        time.sleep(0.2)


def _sentinels_under(*dirs: Path) -> list[Path]:
    return [
        d / n
        for d in dirs
        if d.is_dir()
        for n in os.listdir(d)
        if n.startswith(".sandbox-sentinel-")
    ]


def _canaries() -> int:
    out = subprocess.run(
        ["/usr/bin/security", "dump-keychain"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return out.count(sandbox.KEYCHAIN_CANARY + "-")


def _call_processes(call: CallDir, wait: float = 6.0) -> str:
    """Процессы, чья командная строка содержит каталог вызова: пробный
    `.app` сорванной пробы (стартует с задержкой, живёт 3 с) — не дольше."""
    deadline = time.monotonic() + wait
    while True:
        out = subprocess.run(
            ["/usr/bin/pgrep", "-fl", str(call.root)],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if not out or time.monotonic() > deadline:
            return out
        time.sleep(0.1)


def _assert_no_traces(bench: Bench) -> None:
    call = bench.call
    assert _canaries() == 0
    assert _call_processes(call) == ""
    assert sorted(os.listdir(call.root)) == [
        "claude-config",
        "profile.sb",
        "stage",
        "tmp",
    ]
    for sub in (call.stage, call.tmp, call.claude_config):
        assert os.listdir(sub) == []
    assert not (Path.home() / sandbox.BAIT).exists()
    assert _probe_labels() == []
    assert set(_sentinels_under(*bench.sentinel_dirs())) == bench.stale


# --- под профилем: всё запрещённое запрещено, базовая половина проходит ----


def test_probe_passes_under_real_profile(bench: Bench) -> None:
    # Z1–Z5, Z8–Z10 и N6 одним прогоном: цель без бандла и его родителей
    assert not (bench.target / "workstreams").exists()
    result = bench.probe()
    assert result.ok, result.failures
    assert result.notes == ("apple-events: нет ответа (закрытие не доказано, §17)",)
    assert (bench.target / "workstreams" / "w1" / "bundle").is_dir()
    _assert_no_traces(bench)


def test_probe_with_devtools_itself_as_target(tmp_path: Path) -> None:
    # Z2 при цели «сам devtools»: часовые в корне (рядом с merge-pr.sh) и в
    # governance/ — запрещены; бандл — существующий каталог, ничего не создаём
    bench = Bench(Path(os.path.realpath(tmp_path)), target=DEVTOOLS_ROOT)
    targets = ProbeTargets(
        target_dir=DEVTOOLS_ROOT,
        bundle_dir=PurePosixPath("governance"),
        git_dir=bench.git_dir,
        common_dir=bench.common_dir,
        run_dir=bench.run_dir,
        home=Path.home(),
        extra=(DEVTOOLS_ROOT / "governance",),
    )
    before = subprocess.run(
        ["git", "-C", DEVTOOLS_ROOT, "status", "--porcelain", "--ignored"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    result = sandbox.run_probe(bench.call, bench.profile(), bench.env, targets)
    assert result.ok, result.failures
    assert not _sentinels_under(DEVTOOLS_ROOT, DEVTOOLS_ROOT / "governance")
    after = subprocess.run(
        ["git", "-C", DEVTOOLS_ROOT, "status", "--porcelain", "--ignored"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert after == before


# --- контроль: та же проба без обёртки краснеет (Z18 мутация 3) -------------


def test_probe_without_wrapper_is_red_on_every_denial(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sandbox, "_wrap", lambda _profile: [])
    result = bench.probe()
    names = _names(result)
    for item in (
        "sentinel:run_dir/edge-check",
        "sentinel:run_dir",
        "sentinel:home",
        "sentinel:target_dir",
        "sentinel:bundle_dir",
        "sentinel:gitdir",
        "sentinel:common dir",
        f"sentinel:extra:{bench.venv}",
        "stand:unlink-gitfile",
        "stand:rename-gitfile",
        "stand:replace-gitfile",
        "stand:config",
        "stand:hook",
        "stand:gitdir-write",
        "stage:nested-git-dir",
        "link:outside",
        "link:gitfile",
        "move:outside",
        "symlink:runner-made",
        "symlink:agent-made",
        "read:bait",
        # F20/F21: снаружи задания и .app исполняются, Apple Events отвечают
        "launchd:submit",
        "launchd:bootstrap",
        "cron:exec",  # снаружи crontab исполняется (код 0 или 1 — не EPERM)
        "launchservices:open",
        "keychain:canary",  # канарейка находится без обёртки — связка открыта
        "apple-events",
        "внешний файл изменён",
    ):
        assert item in names, (item, result.failures)
    effects = [f for f in result.failures if f.startswith("эффект вне песочницы")]
    assert any(f.endswith(": submit") for f in effects), effects
    # основной признак F21: .app запущен LaunchServices (lsappinfo), не файл
    assert any(f.startswith("эффект вне песочницы: lsappinfo ") for f in effects)
    # уборка — и после удавшихся «побегов»
    _assert_no_traces(bench)


# --- мутации профиля (Z18) и отрицательные контроли -------------------------


def test_mutation_allow_stand_gitdir_after_denies_is_red(bench: Bench) -> None:
    # (1) после запретов разрешена запись в gitdir/common dir стенда
    stand = bench.call.root / "stand" / "main" / ".git"
    profile = bench.profile() + f'(allow file-write* (subpath "{stand}"))\n'
    names = _names(bench.probe(profile))
    assert {
        "sentinel:stand gitdir",
        "sentinel:stand common dir",
        "stand:config",
        "stand:hook",
        "stand:gitdir-write",
    } <= names
    # настоящая цель не задета: её часовые запрещены и в мутанте
    assert not {"sentinel:gitdir", "sentinel:common dir"} & names
    _assert_no_traces(bench)


def test_mutation_without_dotgit_regex_is_red_only_on_nested_git(
    bench: Bench,
) -> None:
    # (2) снят regex `.git`: базовый запрет стенд и цель держит, вложенный
    # `.git` в заготовке — только regex
    regex = '(deny file-write* (regex #"(^|/)\\.git(/|$)"))\n'
    profile = bench.profile()
    assert regex in profile
    result = bench.probe(profile.replace(regex, ""))
    assert _names(result) == {"stage:nested-git-dir", "stage:nested-gitfile"}


def test_mutation_inherited_env_is_red(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    # (4) окружение наследованием, а не allowlist вызова
    original = sandbox._spawn

    def inherit(argv: list[str], **kw: object) -> tuple[int | None, bytes]:
        return original(argv, **{**kw, "env": {**os.environ, **bench.env}})  # type: ignore[arg-type]

    monkeypatch.setattr(sandbox, "_spawn", inherit)
    result = bench.probe()
    assert [f for f in result.failures if f.startswith("env: лишние")], result


def test_bundle_writable_profile_is_red(bench: Bench) -> None:
    # N6, отрицательный контроль: запись в бандл разрешена — часовой краснеет
    bundle = bench.target / "workstreams" / "w1" / "bundle"
    profile = bench.profile() + f'(allow file-write* (subpath "{bundle}"))\n'
    assert "sentinel:bundle_dir" in _names(bench.probe(profile))


def test_deny_all_profile_fails_base_half(bench: Bench) -> None:
    # проба, у которой «всё не удалось», не проходит на профиле-заглушке
    profile = "(version 1)\n(allow default)\n(deny file-write*)\n"
    names = _names(bench.probe(profile))
    assert {"allow:stage", "allow:tmp"} <= names
    _assert_no_traces(bench)


# --- исходы прибора ----------------------------------------------------------


@pytest.mark.parametrize("timeout", [10.0, 0.5])
def test_spawn_kills_process_group_after_exit_and_timeout(
    tmp_path: Path, timeout: float
) -> None:
    # гигиена §12: потомок в группе убит и после штатного выхода, и по таймауту
    # потомок без stdout (держи он канал — `communicate` ждал бы таймаута);
    # его pid — в файл: по таймауту вывод не возвращается
    pidfile = tmp_path / "pid"
    script = f"sleep 30 >/dev/null 2>&1 & echo $! > {pidfile}; " + (
        "" if timeout > 1 else "sleep 30"
    )
    code, _ = sandbox._spawn(
        ["/bin/sh", "-c", script],
        env={"PATH": "/bin"},
        cwd=tmp_path,
        timeout=timeout,
    )
    assert (code is None) == (timeout < 1)
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"потомок {pid} жив")


def test_timeout_is_failure_and_cleans_up(bench: Bench) -> None:
    result = bench.probe(timeout=0.01)
    assert result.failures == ("проба: таймаут — не действует",)
    _assert_no_traces(bench)


def test_cleanup_on_exception(bench: Bench, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> tuple[int | None, bytes]:
        raise RuntimeError("сбой посреди пробы")

    monkeypatch.setattr(sandbox, "_spawn", boom)
    with pytest.raises(RuntimeError):
        bench.probe()
    _assert_no_traces(bench)


def test_missing_forbidden_dir_is_preparation_failure(bench: Bench) -> None:
    # ENOENT часового — сбой подготовки, а не «защита работает» (§15)
    (bench.run_dir / "edge-check").rmdir()
    failures = bench.probe().failures
    assert "sentinel:run_dir/edge-check: ENOENT — сбой подготовки" in failures


def test_target_metadata_unchanged(bench: Bench) -> None:
    def snapshot() -> dict[str, bytes]:
        out = {}
        for root in (bench.common_dir,):
            for dirpath, _dirs, files in os.walk(root):
                for name in files:
                    p = Path(dirpath) / name
                    out[str(p)] = p.read_bytes()
        out[".git"] = (bench.target / ".git").read_bytes()
        return out

    before = snapshot()
    assert bench.probe().ok
    assert snapshot() == before


# --- Z19: подкладка ввода в терминал (TIOCSTI), одноразовый PTY -------------

_INJECT = r"""
import fcntl, os, sys, termios
try:
    fd = os.open(os.environ["TTYPATH"], os.O_RDONLY | os.O_NOCTTY)
except OSError as exc:
    print("open", exc.errno); sys.exit(3)
try:
    for ch in os.environ["PAYLOAD"].encode():
        fcntl.ioctl(fd, termios.TIOCSTI, bytes([ch]))
    print("injected")
except OSError as exc:
    print("ioctl", exc.errno); sys.exit(4)
"""


_PTY_HARNESS = r"""
import json, os, pty, select, subprocess, sys, termios, time

steps = json.loads(sys.argv[1])
pid, master = pty.fork()
if pid == 0:
    tty = os.ttyname(0)
    for name, argv, kw in steps:
        env = {**os.environ, "TTYPATH": kw.pop("tty", tty), "PAYLOAD": name}
        r = subprocess.run(argv, capture_output=True, text=True, env=env, **kw)
        os.write(1, f"[{name.lower()}] {r.returncode} {r.stdout.strip()}\n".encode())
        time.sleep(0.3)
        termios.tcflush(0, termios.TCIFLUSH)
    os._exit(0)
out = b""
deadline = time.time() + 30
while time.time() < deadline:
    ready, _, _ = select.select([master], [], [], 0.5)
    if not ready:
        continue
    try:
        chunk = os.read(master, 4096)
    except OSError:
        break
    if not chunk:
        break
    out += chunk
os.waitpid(pid, 0)
sys.stdout.write(out.decode(errors="replace"))
"""


def _pty_run(steps: list[tuple[str, list[str], dict[str, object]]]) -> str:
    """Шаги в ребёнке-лидере сессии с PTY; вывод мастера.

    `pty.fork` — в отдельном однопоточном процессе: из многопоточного pytest
    `forkpty()` может заклинить ребёнка (DeprecationWarning Python 3.13).
    """
    proc = subprocess.run(
        [sys.executable, "-I", "-c", _PTY_HARNESS, json.dumps(steps)],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return proc.stdout


def test_tiocsti_blocked_by_each_belt_separately(bench: Bench) -> None:
    profile = bench.call.root / "z19.sb"
    profile.write_text(bench.profile())
    py = [sys.executable, "-I", "-c", _INJECT]
    try:
        out = _pty_run(
            [
                # контроль: та же сессия, без песочницы — ввод подложен
                ("XYZ", py, {}),
                # пояс (а): новая сессия без песочницы — /dev/tty → ENXIO
                ("QQA", py, {"start_new_session": True, "tty": "/dev/tty"}),
                # пояс (а): новая сессия, терминал по имени — TIOCSTI EACCES
                ("QQB", py, {"start_new_session": True}),
                # пояс (б): та же сессия, под профилем — file-ioctl EPERM
                ("QQC", [sandbox.SANDBOX_EXEC, "-f", str(profile), *py], {}),
            ]
        )
    finally:
        profile.unlink()
    assert "[xyz] 0 injected" in out and "XYZ" in out.replace("[xyz]", "")
    assert "[qqa] 3 open 6" in out  # ENXIO
    assert "[qqb] 4 ioctl 13" in out  # EACCES
    assert "[qqc] 4 ioctl 1" in out  # EPERM
    for payload in ("QQA", "QQB", "QQC"):
        assert payload not in out


# --- Apple Events: контроль обязан отвечать (§16 Z9) ------------------------


def test_apple_events_control_answers_without_sandbox() -> None:
    proc = subprocess.run(
        ["/usr/bin/osascript", "-e", 'tell application "Finder" to get name'],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0 and proc.stdout.strip() == "Finder"


def test_launchd_control_runs_job_without_sandbox(tmp_path: Path) -> None:
    # F20, контроль: без песочницы `launchctl submit` исполняет задание
    effect = Path(os.path.realpath(tmp_path)) / "effect"
    label = "dev.devtools.sandbox-probe.control"
    try:
        rc = subprocess.run(
            [
                "/bin/launchctl",
                "submit",
                "-l",
                label,
                "--",
                "/bin/sh",
                "-c",
                f"echo x > {effect}",
            ],
            capture_output=True,
            check=False,
        ).returncode
        deadline = time.time() + 10
        while not effect.exists() and time.time() < deadline:
            time.sleep(0.2)
    finally:
        subprocess.run(
            ["/bin/launchctl", "remove", label], capture_output=True, check=False
        )
    assert rc == 0 and effect.exists()


def test_missing_probe_item_is_failure(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    # пункт, выпавший из скрипта пробы, — отказ, а не молчаливый пропуск
    line = 'attempt("symlink:runner-made", lambda: write(cfg["runner_link"]))\n'
    assert line in sandbox.PROBE_SCRIPT
    monkeypatch.setattr(sandbox, "PROBE_SCRIPT", sandbox.PROBE_SCRIPT.replace(line, ""))
    assert bench.probe().failures == (
        "symlink:runner-made: исхода нет — пункт не исполнен",
    )


# --- граница запуска: фейковый агент под настоящим профилем (TASK-005) -------
#
# Исполняемый файл `claude` — скрипт теста, допущенный временным контрактом
# (настоящие realpath, sha256, macOS, шаблон профиля); связка ключей — двойник
# токена. Всё остальное — настоящее: каталог вызова, профиль, проба, запуск
# под `sandbox-exec`, перенос.

_PARSE = r"""
for prompt; do :; done
out=$(printf '%s\n' "$prompt" | sed -n 's/^Write EXACTLY one file: \([^ ]*\) (.*/\1/p')
bundle=$(printf '%s\n' "$prompt" | sed -n 's/^kind=[^ ]* subject=.* bundle_dir=\(.*\)$/\1/p')
"""


class Live:
    """Цель (git), каталог прогона, допущенный фейковый `claude`."""

    def __init__(self, base: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from governance import author_launch

        self.base = base
        self.target = base / "target"
        _git("init", "-q", self.target)
        self.run_dir = base / "runs" / "r1"
        self.run_dir.mkdir(parents=True)
        (self.run_dir / "ledger.jsonl").write_text("")
        self.bin = base / "bin"
        self.bin.mkdir()
        self.contract = base / "admitted.toml"
        monkeypatch.setenv("PATH", f"{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin")
        monkeypatch.setenv("XDG_STATE_HOME", str(base / "xdg"))
        monkeypatch.setenv("AUTHOR_HARNESS", "claude")
        monkeypatch.delenv("AUTHOR_MODEL", raising=False)
        monkeypatch.setenv("AI_PROSTO_HARNESS_ENV", str(base / "none"))
        monkeypatch.setattr(author_launch, "author_claude_token", lambda: "tok")
        monkeypatch.setattr(author_launch, "ADMITTED_CONTRACT", self.contract)
        self.launch = author_launch

    def agent(self, body: str) -> None:
        script = self.bin / "claude"
        script.write_text("#!/bin/sh\n" + _PARSE + body + "exit 0\n", encoding="utf-8")
        script.chmod(0o755)
        real = os.path.realpath(script)
        import hashlib

        self.contract.write_text(
            "schema = 1\n\n[[admitted]]\n"
            'harness = "claude"\nversion = "fake"\n'
            f'realpath = "{real}"\n'
            f'sha256 = "{hashlib.sha256(Path(real).read_bytes()).hexdigest()}"\n'
            f"macos_major = {sandbox._macos_major()}\n"
            f'profile_sha256 = "{sandbox.profile_template_sha256()}"\n'
            'evidence = "test"\n',
            encoding="utf-8",
        )

    def call(self, bundle: str, filename: str):
        return self.launch.AuthorCall(
            "r1", str(self.run_dir), str(self.target), bundle, filename
        )


@pytest.fixture
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Live:
    return Live(Path(os.path.realpath(tmp_path)), monkeypatch)


def _hash_object(repo: Path, rel: str) -> str:
    return subprocess.run(
        ["git", "-C", repo, "hash-object", rel],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_launch_n6_first_run_without_bundle_transfers_node(live: Live) -> None:
    """N6: цель без brief, бандла и его родителей — проба зелёная, файл
    перенесён; агент пишет только в заготовку (запись в цель — EPERM)."""
    from governance.ops import RealOps

    live.agent(
        'printf "node\\n" > "$out"\n'
        'printf "x" > "$bundle/escape.md" 2>/dev/null && echo escaped >> "$out"\n'
    )
    outcome = RealOps().author(
        live.call("ws/new/spec", "10-requirements.md"), "requirements", "s"
    )
    assert outcome.ok, outcome
    assert (live.target / "ws/new/spec/10-requirements.md").read_bytes() == b"node\n"
    assert not (live.target / "ws/new/spec/escape.md").exists()
    assert list((live.base / "xdg/devtools/agent-calls/r1").iterdir()) == []


def test_launch_n1_upstream_hashes_from_the_real_bundle(live: Live) -> None:
    """N1: `git hash-object <bundle_dir>/…` под профилем считается по
    настоящему бандлу цели — чтение дерева цели агенту открыто."""
    from governance.ops import RealOps

    bundle = live.target / "ws/spec"
    bundle.mkdir(parents=True)
    (bundle / "00-charter.md").write_text("# charter\n\n#### CON-01: x\n")
    live.agent(
        'h=$(git hash-object "$bundle/00-charter.md")\n'
        'printf "upstream_hashes: {charter: \\"%s\\"}\\n" "$h" > "$out"\n'
    )
    outcome = RealOps().author(
        live.call("ws/spec", "10-requirements.md"), "requirements", "s"
    )
    assert outcome.ok, outcome
    expected = _hash_object(live.target, "ws/spec/00-charter.md")
    published = (bundle / "10-requirements.md").read_text()
    assert published == f'upstream_hashes: {{charter: "{expected}"}}\n'


def test_launch_detached_descendant_cannot_change_transferred_bytes(
    live: Live,
) -> None:
    """Z12: отсоединённый потомок (`setsid`) переживает вызов и переписывает
    заготовку и бандл — перенесённые байты неизменны."""
    from governance.ops import RealOps

    live.agent(
        'printf "node\\n" > "$out"\n'
        f"{sys.executable} -I -c '"
        "import os, sys, time\n"
        "os.setsid(); time.sleep(1.5)\n"
        "for p in sys.argv[1:]:\n"
        '    try: open(p, "w").write("evil")\n'
        "    except OSError: pass\n"
        '\' "$out" "$bundle/10-requirements.md" </dev/null >/dev/null 2>&1 &\n'
    )
    outcome = RealOps().author(
        live.call("ws/spec", "10-requirements.md"), "requirements", "s"
    )
    assert outcome.ok, outcome
    node = live.target / "ws/spec/10-requirements.md"
    assert node.read_bytes() == b"node\n"
    time.sleep(3)  # потомок отработал
    assert node.read_bytes() == b"node\n"


@pytest.mark.parametrize("swap", ["symlink", "fifo", "grow"])
def test_launch_race_during_transfer_refuses_or_transfers_own_bytes(
    live: Live, swap: str
) -> None:
    """Z12, гонки до/во время переноса: потомок подменяет файл узла ссылкой
    на внешний, FIFO или растит его — исход: отказ или собственные байты
    агента; внешний файл неизменен, наружу ничего не уходит."""
    from governance.ops import RealOps

    outside = live.base / "outside.txt"
    outside.write_text("OUTSIDE\n")
    action = {
        "symlink": f"os.unlink(p); os.symlink({str(outside)!r}, p)",
        "fifo": "os.unlink(p); os.mkfifo(p)",
        "grow": "open(p, 'a').write('x' * (1 << 20))",
    }[swap]
    live.agent(
        'printf "node\\n" > "$out"\n'
        f"{sys.executable} -I -c '"
        "import os, sys, time\n"
        "os.setsid(); p = sys.argv[1]; end = time.time() + 8\n"
        "while time.time() < end:\n"
        "    try:\n"
        f"        {action}\n"
        "    except OSError: pass\n"
        "    time.sleep(0.01)\n"
        '\' "$out" </dev/null >/dev/null 2>&1 &\n'
    )
    outcome = RealOps().author(
        live.call("ws/spec", "10-requirements.md"), "requirements", "s"
    )
    node = live.target / "ws/spec/10-requirements.md"
    if outcome.ok:
        data = node.read_bytes()
        assert data.startswith(b"node\n") and b"OUTSIDE" not in data
        assert not node.is_symlink()
    else:
        assert not node.exists()
    assert outside.read_text() == "OUTSIDE\n"
    time.sleep(1)


@pytest.mark.parametrize(
    "swap",
    [
        'ln -s "$OUTSIDE" "$out"',
        'mkfifo "$out"',
        'head -c 5000000 /dev/zero > "$out"',
    ],
    ids=["symlink", "fifo", "over-limit"],
)
def test_launch_substitution_before_transfer_is_refused(
    live: Live, monkeypatch: pytest.MonkeyPatch, swap: str
) -> None:
    """Z12, подмена до переноса (детерминированно, до выхода агента): файл
    узла — ссылка на внешний, FIFO или сверх потолка — отказ без зависания,
    наружу ничего не уходит, внешний файл неизменен."""
    from governance.ops import RealOps

    outside = live.base / "outside.txt"
    outside.write_text("OUTSIDE\n")
    monkeypatch.setenv("OUTSIDE", str(outside))
    monkeypatch.setattr(
        live.launch, "AGENT_ENV_NAMES", live.launch.AGENT_ENV_NAMES | {"OUTSIDE"}
    )
    live.agent(f"{swap}\n")
    started = time.monotonic()
    outcome = RealOps().author(
        live.call("ws/spec", "10-requirements.md"), "requirements", "s"
    )
    assert time.monotonic() - started < 60
    assert not outcome.ok and "перенос файла узла" in outcome.reason, outcome
    assert not (live.target / "ws/spec/10-requirements.md").exists()
    assert outside.read_text() == "OUTSIDE\n"


# --- через подключение к runner: вход гейта и опасное назначение (Z12, Z14) --


@pytest.fixture
def runner_live(live: Live, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("steward")
    from governance import run_state as rs

    monkeypatch.setattr(rs, "RUNS_ROOT", live.base / "runs")
    return live


def _runner_ops(gate_real: bool):
    import types

    from governance.ops import RealOps
    from tests.test_governance_runner import FakeOps

    class Ops(FakeOps):
        gate_inputs: list[bytes]

        def gate_check_candidate(self, target_dir, bundle_dir, profile):
            node = Path(target_dir) / bundle_dir / "00-charter.md"
            self.gate_inputs = [*getattr(self, "gate_inputs", []), node.read_bytes()]
            if gate_real:
                return RealOps().gate_check_candidate(target_dir, bundle_dir, profile)
            return super().gate_check_candidate(target_dir, bundle_dir, profile)

    ops = Ops()
    ops.author = types.MethodType(RealOps.author, ops)  # type: ignore[method-assign]
    return ops


def _start(live: Live, ops, monkeypatch, run_id: str):
    from tests import locked_runner
    from tests.test_governance_runner import _fake_wave_adapters, _waves_kwargs

    _fake_wave_adapters(monkeypatch, ops)
    kwargs = _waves_kwargs(live.base, run_id, ops, target_dir=str(live.target))
    return locked_runner.start(**kwargs)


_CHARTER = "# charter\\n\\n#### CON-01: x\\n"


def test_runner_gate_input_equals_transferred_bytes(
    runner_live: Live, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Z12 интеграционно: при claude и непустом допуске авторинг проходит
    штатно; потомок агента переписывает заготовку после выхода — гейт
    читает перенесённые байты."""
    live = runner_live
    live.agent(
        f'printf "{_CHARTER}" > "$out"\n'
        f"{sys.executable} -I -c '"
        "import os, sys, time\n"
        "os.setsid(); time.sleep(0.2)\n"
        'try: open(sys.argv[1], "w").write("evil")\n'
        "except OSError: pass\n"
        '\' "$out" </dev/null >/dev/null 2>&1 &\n'
    )
    ops = _runner_ops(gate_real=False)
    state = _start(live, ops, monkeypatch, "r-live-gate")
    assert state.status == "waiting_human_merge", state.status
    assert ops.gate_inputs == [b"# charter\n\n#### CON-01: x\n"]
    assert state.ops["author-charter"]["skipped"] is False


@pytest.mark.parametrize("trap", ["dangling-symlink", "hardlink"])
def test_runner_z14_dangerous_destination_gets_new_inode_and_gate_refuses(
    runner_live: Live, monkeypatch: pytest.MonkeyPatch, trap: str
) -> None:
    """Z14: опасное назначение ставит ДОВЕРЕННЫЙ код теста между выходом
    агента и публикацией (заранее нельзя: существующий узел раннер штатно
    пропускает, а агент в цель писать не может); агент пишет JSON-«хук»
    вместо узла. Публикация заменяет само имя (новый inode), цель ссылки
    не создаётся, двойник неизменен; настоящий гейт читает опубликованные
    байты и отказывает."""
    from governance import safe_io

    live = runner_live
    live.agent('printf \'{"hooks": {"x": "sh -c evil"}}\\n\' > "$out"\n')
    hook_target = live.target / ".claude" / "settings.local.json"
    twin = live.base / "twin.md"
    twin.write_text("twin\n")
    real_publish = safe_io.publish
    trapped: list[tuple[int, int]] = []

    def publish(root, rel, data):
        dest = Path(root) / rel
        if str(rel).endswith("00-charter.md") and Path(root) == live.target:
            if trap == "dangling-symlink":
                os.symlink(hook_target, dest)
            else:
                os.link(twin, dest)
            before = os.lstat(dest).st_ino
            real_publish(root, rel, data)
            trapped.append((before, os.lstat(dest).st_ino))
            return
        real_publish(root, rel, data)

    monkeypatch.setattr(safe_io, "publish", publish)
    ops = _runner_ops(gate_real=True)
    state = _start(live, ops, monkeypatch, f"r-live-z14-{trap}")
    assert len(trapped) == 1, "публикация выполнена поверх ловушки"
    before, after = trapped[0]
    assert before != after, "назначение получило новый inode"
    node = live.target / "workstreams/WS-1/spec/00-charter.md"
    assert not node.is_symlink()
    assert ops.gate_inputs == [b'{"hooks": {"x": "sh -c evil"}}\n']
    assert state.status == "stopped_gate", state.status
    assert not hook_target.exists() and not hook_target.parent.exists()
    assert twin.read_text() == "twin\n"
