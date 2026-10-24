"""Task 11-A: model probes (spec 3.6) — admission, parsing, records, status.

No real harness runs here: every profile a probe launches with is built by
the test (the real `HARNESS_PROFILES` entries of 11-B are unverified and are
only checked to refuse), the executable is a fake Mach-O file under
`tmp_path`, and the runner is a spy. Real binaries: `test_probe_isolation`.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import os
import stat
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest

from df import cli, fsutil, models, probe, state
from df import platform as plat
from df.config import ConfigError, InstallConfig, dump_install
from df.journal import Stable, Transitional
from df.models import ModelChoice, ModelsConfig
from df.paths import Layout
from df.platform import RunResult
from df.probe import (
    ADAPTER_VERSION,
    PROBE_PROMPT,
    AuthObservation,
    Exclusion,
    HarnessProfile,
    ManagedSource,
    ProbeRecord,
)

FIXTURES = Path(__file__).parent / "fixtures" / "probe"
MACHO = b"\xcf\xfa\xed\xfe" + bytes(range(60))
CHOICE = ModelChoice("claude", "claude-opus-5-5", "high", {})
CODEX = ModelChoice("codex", "gpt-5.5", None, {})
PLATFORM = plat.detect_platform()
CRED = ".claude/.credentials.json"
UID = os.getuid()
#: The module's real profile table, before any test patches it.
REAL_PROFILES = dict(probe.HARNESS_PROFILES)


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def make_profile(**kw: object) -> HarnessProfile:
    fields: dict[str, object] = {
        "version": "9.9.9",
        "argv": probe.claude_argv,
        "auth_files": (CRED,),
        "managed": (),
        "isolation_verified": True,
    }
    fields.update(kw)
    return HarnessProfile(**fields)  # type: ignore[arg-type]


class Spy:
    """A runner that records every call and answers with `result`."""

    def __init__(self, result: RunResult) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def __call__(
        self,
        user: str,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
        cwd: Path,
        network: bool,
        timeout: float,
    ) -> RunResult:
        self.calls.append(
            {
                "user": user,
                "argv": argv,
                "env": env,
                "cwd": cwd,
                "network": network,
                "timeout": timeout,
            }
        )
        return self.result


def ok_result(name: str = "claude-ok.json", code: int = 0) -> RunResult:
    return RunResult(code, fixture(name), b"", True)


@dataclasses.dataclass
class World:
    """A layout, a role home, a fake harness and its registered profile."""

    lay: Layout
    home: Path
    exe: Path
    sha: str
    install: InstallConfig
    cfg: ModelsConfig
    mp: pytest.MonkeyPatch

    def register(self, prof: HarnessProfile, harness: str = "claude") -> None:
        key = (harness, PLATFORM, self.sha)
        self.mp.setattr(probe, "HARNESS_PROFILES", {key: prof})

    def run(self, spy: Spy, role: str = "author") -> ProbeRecord | str:
        return probe.run_probe(role, None, self.cfg, self.install, self.lay, runner=spy)


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    base = Path(os.path.realpath(tmp_path))
    lay = Layout(base / "root")
    lay.probe_cwd.mkdir(parents=True)
    lay.probes_dir.mkdir(parents=True)
    home = base / "home"
    home.mkdir()
    exe = base / "bin" / "claude"
    exe.parent.mkdir()
    exe.write_bytes(MACHO)
    exe.chmod(0o755)
    monkeypatch.setattr(plat, "ROOT_UID", os.getuid())
    monkeypatch.setattr(
        probe,
        "_getpwnam",
        lambda name: SimpleNamespace(pw_dir=str(home), pw_uid=os.getuid()),
    )
    tools = {n: Path("/usr/bin/true") for n in ("git", "uv", "tar")}
    install = InstallConfig(
        "https://example.invalid/m.git", "a" * 40, "core", {**tools, "claude": exe}
    )
    cfg = ModelsConfig({"author": CHOICE}, {})
    w = World(
        lay, home, exe, hashlib.sha256(MACHO).hexdigest(), install, cfg, monkeypatch
    )
    w.register(make_profile())
    return w


# ---------- argv ----------


def test_argv_claude_and_codex_exact() -> None:
    h = Path("/opt/h/claude")
    assert probe.claude_argv(h, CHOICE) == [
        "/opt/h/claude",
        "-p",
        "--model",
        "claude-opus-5-5",
        "--output-format",
        "json",
        "--max-turns",
        "1",
        "--setting-sources",
        probe.CLAUDE_SETTING_SOURCES,
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--tools",
        "",
        "--effort",
        "high",
        "--",
        PROBE_PROMPT,
    ]
    assert probe.CLAUDE_SETTING_SOURCES == ""
    plain = dataclasses.replace(CHOICE, effort=None)
    assert "--effort" not in probe.claude_argv(h, plain)
    c = Path("/opt/h/codex")
    assert probe.codex_argv(c, CODEX) == [
        "/opt/h/codex",
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "-c",
        'cli_auth_credentials_store="file"',
        "-c",
        "mcp_servers={}",
        "-c",
        "project_doc_max_bytes=0",
        "-m",
        "gpt-5.5",
        "--json",
        "--skip-git-repo-check",
        "--ephemeral",
        "--sandbox",
        "read-only",
        PROBE_PROMPT,
    ]
    high = dataclasses.replace(CODEX, effort="high")
    assert probe.codex_argv(c, high)[-3:] == [
        "-c",
        'model_reasoning_effort="high"',
        PROBE_PROMPT,
    ]
    assert ADAPTER_VERSION == "2"
    assert PROBE_PROMPT == "Reply with the single word OK."


def test_profiles_pin_the_step3_binaries_unverified() -> None:
    # 11-B: the two macOS binaries of R66, both with isolation NOT verified
    # (server-delivered managed settings have no Exclusion; evidence
    # docs/evidence/2026-10-24-df-probe-isolation.md).
    table = REAL_PROFILES
    claude = table[
        (
            "claude",
            "macos-arm64",
            "5eadc0009cdcdbc0d06e58110d7b2baf88ced6088b1c33a60a21217fe410619c",
        )
    ]
    codex = table[
        (
            "codex",
            "macos-arm64",
            "8813367285524ac798dd96d1b071aea30d58708c7226fd236cd275cf1cfdee31",
        )
    ]
    assert len(table) == 2
    assert (claude.version, claude.argv, claude.auth_files) == (
        "2.1.309",
        probe.claude_argv,
        None,
    )
    assert (codex.version, codex.argv, codex.auth_files) == (
        "0.163.0",
        probe.codex_argv,
        (".codex/auth.json",),
    )
    assert not claude.isolation_verified
    assert not codex.isolation_verified
    for prof in (claude, codex):
        servers = [m for m in prof.managed if m.kind == "server"]
        assert servers and all(m.excluded_by is None for m in servers)
        assert all(m.observable for m in prof.managed if m.kind == "file")
        assert all(not m.allowed_digests for m in prof.managed)
    plists = {m.locator for m in claude.managed if m.kind == "mdm"}
    assert plists == {
        "/Library/Managed Preferences/com.anthropic.claudecode.plist",
        "/Library/Managed Preferences/df-exec/com.anthropic.claudecode.plist",
        "/Library/Managed Preferences/df-review/com.anthropic.claudecode.plist",
    }


def test_pinned_profiles_refuse_before_launch(world: World) -> None:
    # Each real (unverified) profile, put under the fake binary's SHA: the
    # probe is «не поддерживается» and the runner is never called.
    for key, prof in REAL_PROFILES.items():
        world.register(prof, key[0])
        world.cfg = ModelsConfig(
            {"author": dataclasses.replace(CHOICE, harness=key[0])}, {}
        )
        world.install = dataclasses.replace(
            world.install,
            approved_cli={**world.install.approved_cli, key[0]: world.exe},
        )
        spy = Spy(ok_result())
        assert world.run(spy) == "не поддерживается: изоляция не подтверждена"
        assert spy.calls == []


def test_param_flags_match_supported_params() -> None:
    assert {h: set(f) for h, f in probe.PARAM_FLAGS.items()} == {
        h: set(p) for h, p in models.SUPPORTED_PARAMS.items()
    }


def test_param_without_adapter_flag_refused(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parameter the schema let through but the adapter has no flag for is
    a `ConfigError`, and the probe is refused before any launch."""
    monkeypatch.setattr(
        models,
        "SUPPORTED_PARAMS",
        {"claude": frozenset({"temperature"}), "codex": frozenset()},
    )
    choice = ModelChoice("claude", "claude-opus-5-5", None, {"temperature": "0"})
    with pytest.raises(ConfigError, match="temperature"):
        probe.param_argv(choice)
    world.cfg = ModelsConfig({"author": choice}, {})
    spy = Spy(ok_result())
    result = world.run(spy)
    assert isinstance(result, str) and result.startswith("не поддерживается: ")
    assert spy.calls == []


# ---------- admission ----------


def test_unverified_version_never_launches(world: World) -> None:
    world.register(make_profile(isolation_verified=False))
    spy = Spy(ok_result())
    assert world.run(spy) == "не поддерживается: изоляция не подтверждена"
    assert spy.calls == []


def test_unknown_executable_never_launched(world: World) -> None:
    world.mp.setattr(probe, "HARNESS_PROFILES", {})
    spy = Spy(ok_result())
    result = world.run(spy)
    assert result == f"не поддерживается: неизвестный исполняемый файл {world.sha}"
    assert spy.calls == []
    argv = probe.claude_argv(world.exe, CHOICE)
    got = probe.admit("claude", world.exe, PLATFORM, argv=argv, env={})
    assert got == result


def test_script_install_unsupported(world: World) -> None:
    world.exe.write_bytes(b"#!/usr/bin/env node\nconsole.log(1)\n")
    argv = probe.claude_argv(world.exe, CHOICE)
    got = probe.admit("claude", world.exe, PLATFORM, argv=argv, env={})
    assert isinstance(got, str) and "установка-скрипт" in got
    spy = Spy(ok_result())
    result = world.run(spy)
    assert isinstance(result, str) and "установка-скрипт" in result
    assert spy.calls == []


def test_admit_resolves_link_and_needs_target_in_argv(world: World) -> None:
    """The approved path may be a link; argv[0] must be its final target."""
    link = world.exe.parent / "claude-link"
    link.symlink_to(world.exe)
    good = probe.claude_argv(world.exe, CHOICE)
    assert probe.admit("claude", link, PLATFORM, argv=good, env={}) == make_profile()
    bad = probe.claude_argv(link, CHOICE)
    got = probe.admit("claude", link, PLATFORM, argv=bad, env={})
    assert isinstance(got, str) and got.startswith("не поддерживается: argv[0]")


def test_not_a_binary_unsupported(world: World) -> None:
    world.exe.write_bytes(b"plain text, not an executable\n")
    argv = probe.claude_argv(world.exe, CHOICE)
    got = probe.admit("claude", world.exe, PLATFORM, argv=argv, env={})
    assert got == "не поддерживается: неизвестный формат исполняемого файла"


@pytest.mark.parametrize(
    "case", ["present-unapproved", "unreadable", "unobservable-not-excluded"]
)
def test_managed_context_blocks_before_launch(world: World, case: str) -> None:
    managed = world.home.parent / "managed-settings.json"
    managed.write_text('{"model": "other"}')
    if case == "present-unapproved":
        source = ManagedSource("file", str(managed), True, frozenset({"0" * 64}), None)
    elif case == "unreadable":
        managed.chmod(0)
        source = ManagedSource("file", str(managed), True, frozenset(), None)
    else:
        source = ManagedSource("server", "policy", False, frozenset(), None)
    world.register(make_profile(managed=(source,)))
    spy = Spy(ok_result())
    result = world.run(spy)
    assert isinstance(result, str) and result.startswith("не поддерживается: ")
    assert spy.calls == []
    digest, problems = probe.managed_context(make_profile(managed=(source,)))
    assert problems and digest is None


def test_managed_context_absent_and_approved(world: World) -> None:
    """An absent observable source and one whose hash is approved both pass;
    the digest changes when the observed state does."""
    path = world.home.parent / "managed.json"
    absent = make_profile(
        managed=(ManagedSource("file", str(path), True, frozenset(), None),)
    )
    digest_absent, problems = probe.managed_context(absent)
    assert problems == [] and digest_absent is not None
    path.write_text("{}")
    approved = make_profile(
        managed=(
            ManagedSource(
                "mdm",
                str(path),
                True,
                frozenset({hashlib.sha256(b"{}").hexdigest()}),
                None,
            ),
        )
    )
    digest_present, problems = probe.managed_context(approved)
    assert problems == [] and digest_present not in (None, digest_absent)


def _drop(flag: str) -> Callable[[Path, ModelChoice], list[str]]:
    def build(h: Path, choice: ModelChoice) -> list[str]:
        return [a for a in probe.claude_argv(h, choice) if a != flag]

    return build


@pytest.mark.parametrize(
    "formed,launched",
    [("with-exclusion", True), ("without-exclusion", False), ("env-differs", False)],
)
def test_exclusion_checked_on_actual_invocation(
    world: World, formed: str, launched: bool
) -> None:
    """One SHA and profile; the exclusion is checked on the argv and env this
    run formed, and the runner is called once (the probe) only if it holds."""
    lang = "C" if formed == "env-differs" else "C.UTF-8"
    ex = Exclusion(("--strict-mcp-config",), {"LANG": lang})
    build = _drop("--strict-mcp-config") if formed == "without-exclusion" else None
    source = ManagedSource("server", "policy", False, frozenset(), ex)
    world.register(make_profile(argv=build or probe.claude_argv, managed=(source,)))
    spy = Spy(ok_result())
    result = world.run(spy)
    assert len(spy.calls) == (1 if launched else 0)
    assert isinstance(result, ProbeRecord) == launched


def test_exclusion_holds_contiguous_subsequence() -> None:
    ex = Exclusion(("-c", "a=1"), {"K": "v"})
    assert probe.exclusion_holds(ex, ["h", "-c", "a=1", "x"], {"K": "v", "L": "w"})
    assert not probe.exclusion_holds(ex, ["h", "-c", "x", "a=1"], {"K": "v"})
    assert not probe.exclusion_holds(ex, ["h", "-c", "a=1"], {"K": "w"})
    assert not probe.exclusion_holds(ex, ["h", "-c", "a=1"], {})


def test_exclusion_type_has_no_free_text() -> None:
    assert [f.name for f in dataclasses.fields(Exclusion)] == ["argv", "env"]
    with pytest.raises(ConfigError):
        Exclusion((), {})


# ---------- the launch ----------


def test_single_launch_without_version_call(world: World) -> None:
    admitted: list[tuple[object, object]] = []
    real = probe.admission

    def spy_admit(*args: object, **kw: object) -> object:
        admitted.append((kw["argv"], kw["env"]))
        return real(*args, **kw)  # type: ignore[arg-type]

    world.mp.setattr(probe, "admission", spy_admit)
    spy = Spy(ok_result())
    rec = world.run(spy)
    assert isinstance(rec, ProbeRecord)
    assert len(spy.calls) == 1 and len(admitted) == 1
    call = spy.calls[0]
    assert call["argv"] is admitted[0][0] and call["env"] is admitted[0][1]
    assert not any("--version" in str(c["argv"]) for c in spy.calls)
    assert rec.harness_version == "9.9.9"


def test_launch_uses_admitted_argv_env(world: World) -> None:
    spy = Spy(ok_result())
    rec = world.run(spy)
    assert isinstance(rec, ProbeRecord)
    call = spy.calls[0]
    assert call["user"] == "df-exec"
    assert call["argv"] == probe.claude_argv(world.exe, CHOICE)
    assert call["env"] == {
        "HOME": str(world.home),
        "PATH": f"{world.exe.parent}:/usr/bin:/bin",
        "LANG": "C.UTF-8",
    }
    assert call["cwd"] == world.lay.probe_cwd
    assert call["network"] is True and call["timeout"] == 120


def test_record_written_and_fields(world: World) -> None:
    cred = world.home / CRED
    cred.parent.mkdir()
    cred.write_text("token")
    rec = world.run(Spy(ok_result()))
    assert isinstance(rec, ProbeRecord)
    path = world.lay.probes_dir / "author.json"
    data = json.loads(path.read_text())
    assert data["schema"] == "df/probe/v1"
    assert data["role"] == "author" and data["product"] is None
    assert data["harness"] == "claude" and data["isolation"] == "verified"
    assert data["adapter_version"] == ADAPTER_VERSION
    assert data["requested_model"] == "claude-opus-5-5"
    assert data["actual_model"] == "claude-opus-5-5"
    assert data["effort"] == "high" and data["params"] == {}
    assert data["config_hash"] == models.config_hash(CHOICE)
    assert data["executable_sha256"] == world.sha
    assert data["auth_tracking"] == "tracked"
    assert data["outcome"] == "confirmed"
    assert os.stat(path).st_mode & 0o777 == 0o644
    assert probe.read_record(world.lay, "author", None) == rec


def test_record_path_with_product(world: World) -> None:
    assert probe.record_path(world.lay, "judge", 42) == (
        world.lay.probes_dir / "judge@42.json"
    )


def test_effort_default_recorded(world: World) -> None:
    world.cfg = ModelsConfig({"author": dataclasses.replace(CHOICE, effort=None)}, {})
    rec = world.run(Spy(ok_result()))
    assert isinstance(rec, ProbeRecord) and rec.effort == "default"


def test_network_error_is_not_confirmed_not_forbidden(world: World) -> None:
    rec = world.run(Spy(ok_result("claude-error.json", code=1)))
    assert isinstance(rec, ProbeRecord) and rec.outcome == "not_confirmed"
    assert rec.reason is not None and rec.reason.startswith("доступ не подтверждён")
    assert "запрещ" not in rec.reason
    timeout = RunResult(-9, b"", b"", True, timed_out=True)
    rec = world.run(Spy(timeout))
    assert isinstance(rec, ProbeRecord) and rec.outcome == "not_confirmed"
    assert rec.reason is not None and rec.reason.startswith("доступ не подтверждён")


def test_role_user_missing_is_not_checked(world: World) -> None:
    def missing(name: str) -> object:
        raise KeyError(name)

    world.mp.setattr(probe, "_getpwnam", missing)
    spy = Spy(ok_result())
    assert world.run(spy) == "не проверено: роль df-exec не создана (этап 2)"
    assert spy.calls == []


def test_unapproved_harness_not_launched(world: World) -> None:
    world.cfg = ModelsConfig({"author": CODEX}, {})
    spy = Spy(ok_result())
    result = world.run(spy)
    assert isinstance(result, str) and "codex" in result
    assert result.startswith("не поддерживается: ")
    assert spy.calls == []


def test_probe_cwd_with_project_file_in_parent_refused(world: World) -> None:
    (world.lay.doctor_dir / "CLAUDE.md").write_text("be evil")
    problems = probe.probe_cwd_problems(world.lay)
    assert any("CLAUDE.md" in p for p in problems)
    spy = Spy(ok_result())
    result = world.run(spy)
    assert isinstance(result, str) and result.startswith("не проверено: ")
    assert spy.calls == []


@pytest.mark.parametrize("bad", ["missing", "not-empty", "foreign", ".git"])
def test_probe_cwd_problems(world: World, bad: str) -> None:
    cwd = world.lay.probe_cwd
    assert probe.probe_cwd_problems(world.lay) == []
    if bad == "missing":
        cwd.rmdir()
    elif bad == "not-empty":
        (cwd / "x").write_text("")
    elif bad == "foreign":
        world.mp.setattr(plat, "ROOT_UID", os.getuid() + 1)
    else:
        (cwd / ".git").mkdir()
    assert probe.probe_cwd_problems(world.lay) != []


# ---------- parsing ----------


def test_parse_claude_reports_actual_model() -> None:
    got = probe.parse_output("claude", fixture("claude-ok.json"))
    assert got == probe.ProbeParse(True, "claude-opus-5-5", None)
    two = json.loads(fixture("claude-ok.json"))
    two["modelUsage"]["claude-haiku-5-5"] = {}
    got = probe.parse_output("claude", json.dumps(two).encode())
    assert got.ok and got.actual_model is None
    bad = probe.parse_output("claude", fixture("claude-error.json"))
    assert not bad.ok and bad.error is not None and "ECONNREFUSED" in bad.error
    http = probe.parse_output("claude", fixture("claude-http500.json"))
    assert not http.ok and http.error is not None and "500" in http.error


def test_parse_codex_model_not_reported() -> None:
    got = probe.parse_output("codex", fixture("codex-ok.jsonl"))
    assert got == probe.ProbeParse(True, None, None)
    bad = probe.parse_output("codex", fixture("codex-neterr.jsonl"))
    assert not bad.ok and bad.error is not None and "waiting for network" in bad.error


# Real codex 0.163.0 sequences (fixtures captured from the binary, 11-B):
# a completed turn may follow top-level `error` retry notices; a failed turn
# ends with `turn.failed`; an unreachable network never ends the turn.
def test_parse_codex_success_after_retry_notices() -> None:
    lines = fixture("codex-ok.jsonl").decode().splitlines()
    types = [json.loads(line)["type"] for line in lines]
    assert "error" in types and types[-1] == "turn.completed"
    assert probe.parse_output("codex", fixture("codex-ok.jsonl")).ok


def test_parse_codex_failed_turn() -> None:
    got = probe.parse_output("codex", fixture("codex-http500.jsonl"))
    assert not got.ok and got.error is not None and "high demand" in got.error


@pytest.mark.parametrize(
    "tail",
    [
        b'{"type":"turn.completed"}\n{"type":"error","message":"late"}\n',
        b'{"type":"turn.completed"}\n{"type":"turn.completed"}\n',
        b'{"type":"turn.failed","error":{"message":"x"}}\n{"type":"turn.completed"}\n',
    ],
)
def test_parse_codex_terminal_event_must_be_single_and_last(tail: bytes) -> None:
    got = probe.parse_output("codex", b'{"type":"turn.started"}\n' + tail)
    assert not got.ok


@pytest.mark.parametrize(
    "harness,stdout",
    [
        ("claude", b"OK"),
        ("claude", b"[1, 2]"),
        ("claude", b'{"is_error": "no"}'),
        ("codex", b"OK\n"),
        ("codex", b'{"type":"turn.started"}\n'),
        ("codex", b'{"type":"turn.completed"}\nnot json\n'),
        ("claude", b"\xff\xfe"),
    ],
)
def test_unparsable_output_not_confirmed(harness: str, stdout: bytes) -> None:
    got = probe.parse_output(harness, stdout)
    assert not got.ok and got.actual_model is None and got.error is not None


def test_unparsable_output_is_named() -> None:
    assert probe.parse_output("claude", b"OK").error == "ответ не разобран"


# ---------- authorization ----------


def test_backend_unknown_ignores_readable_file(tmp_path: Path) -> None:
    cred = tmp_path / CRED
    cred.parent.mkdir()
    cred.write_text("token")
    obs = probe.observe_auth(make_profile(auth_files=None), tmp_path, role_uid=UID)
    assert obs == AuthObservation("unavailable", "backend_unknown", ())


def test_auth_observation_states(tmp_path: Path) -> None:
    prof = make_profile()
    assert probe.observe_auth(prof, tmp_path, role_uid=UID) == AuthObservation(
        "unavailable", "no_file", ()
    )
    cred = tmp_path / CRED
    cred.parent.mkdir()
    cred.write_text("token")
    obs = probe.observe_auth(prof, tmp_path, role_uid=UID)
    assert obs.tracking == "tracked" and obs.reason is None
    assert [f["sha256"] for f in obs.files] == [hashlib.sha256(b"token").hexdigest()]
    assert obs.files[0]["mtime_ns"] == os.stat(cred).st_mtime_ns
    cred.chmod(0)
    assert probe.observe_auth(prof, tmp_path, role_uid=UID) == AuthObservation(
        "unavailable", "unreadable", ()
    )


def test_auth_link_is_unreadable(tmp_path: Path) -> None:
    """A link at the leaf of the pinned path is never followed by root."""
    secret = tmp_path / "secret"
    secret.write_text("root only")
    cred = tmp_path / CRED
    cred.parent.mkdir()
    cred.symlink_to(secret)
    obs = probe.observe_auth(make_profile(), tmp_path, role_uid=UID)
    assert obs.reason == "unreadable"


def test_auth_observation_has_no_content(world: World) -> None:
    cred = world.home / CRED
    cred.parent.mkdir()
    cred.write_text("SECRET-TOKEN-123")
    obs = probe.observe_auth(make_profile(), world.home, role_uid=UID)
    assert "SECRET-TOKEN-123" not in json.dumps(dataclasses.asdict(obs))
    world.run(Spy(ok_result()))
    text = (world.lay.probes_dir / "author.json").read_text()
    assert "SECRET-TOKEN-123" not in text


# ---------- status ----------

TRACKED = AuthObservation(
    "tracked", None, ({"path": "/h/" + CRED, "mtime_ns": 1, "sha256": "a" * 64},)
)
NO_FILE = AuthObservation("unavailable", "no_file", ())
UNREADABLE = AuthObservation("unavailable", "unreadable", ())


def record(auth: AuthObservation = TRACKED, **kw: object) -> ProbeRecord:
    fields: dict[str, object] = {
        "role": "author",
        "product": None,
        "time": "2026-10-24T10:00:00+00:00",
        "harness": "claude",
        "harness_version": "9.9.9",
        "adapter_version": ADAPTER_VERSION,
        "isolation": "verified",
        "requested_model": CHOICE.model,
        "actual_model": None,
        "effort": "high",
        "params": {},
        "config_hash": models.config_hash(CHOICE),
        "executable_sha256": "e" * 64,
        "managed_digest": "m" * 64,
        "auth_tracking": probe.tracking_text(auth),
        "auth_files": auth.files,
        "outcome": "confirmed",
        "reason": None,
    }
    fields.update(kw)
    return ProbeRecord(**fields)  # type: ignore[arg-type]


def status(rec: ProbeRecord | None, **kw: object) -> probe.ProbeStatus:
    facts: dict[str, object] = {
        "harness_version": "9.9.9",
        "auth": TRACKED,
        "executable_sha256": "e" * 64,
        "managed_digest": "m" * 64,
    }
    facts.update(kw)
    return probe.probe_status(rec, CHOICE, **facts)  # type: ignore[arg-type]


def test_confirmed_tracked_is_current() -> None:
    got = status(record())
    assert got == probe.ProbeStatus("подтверждён 2026-10-24T10:00:00+00:00", True)


def test_no_record_and_no_sudo() -> None:
    assert status(None) == probe.ProbeStatus("не проверено", False)
    for kw in ({"auth": None}, {"harness_version": None}):
        assert status(record(), **kw) == probe.ProbeStatus(
            "данные недоступны: нужен sudo", False
        )


@pytest.mark.parametrize(
    "field",
    [
        "config_hash",
        "adapter_version",
        "harness_version",
        "executable_sha256",
        "managed_digest",
        "auth",
    ],
)
def test_status_stale_on_change(field: str) -> None:
    if field in ("config_hash", "adapter_version"):
        got = status(record(**{field: "changed"}))
    elif field == "auth":
        other = dataclasses.replace(
            TRACKED, files=({**TRACKED.files[0], "sha256": "b" * 64},)
        )
        got = status(record(), auth=other)
    else:
        got = status(record(), **{field: "changed"})
    assert got == probe.ProbeStatus(f"устарела: {field}", False)


def test_confirmed_without_auth_file_is_not_current() -> None:
    got = status(record(NO_FILE), auth=NO_FILE)
    assert got == probe.ProbeStatus(
        "успешная проба 2026-10-24T10:00:00+00:00; "
        "актуальность авторизации не установлена",
        False,
    )


@pytest.mark.parametrize(
    "before,after",
    [
        ("tracked", "unavailable: no_file"),
        ("unavailable: no_file", "tracked"),
        ("unavailable: no_file", "unavailable: unreadable"),
    ],
)
def test_tracking_transition_makes_stale(before: str, after: str) -> None:
    states = {
        "tracked": TRACKED,
        "unavailable: no_file": NO_FILE,
        "unavailable: unreadable": UNREADABLE,
    }
    got = status(record(states[before]), auth=states[after])
    assert got == probe.ProbeStatus("устарела: auth", False)


def test_not_confirmed_status() -> None:
    rec = record(outcome="not_confirmed", reason="доступ не подтверждён: таймаут")
    assert status(rec) == probe.ProbeStatus(
        "не подтверждён: доступ не подтверждён: таймаут", False
    )


def test_record_json_round_trip(tmp_path: Path) -> None:
    rec = record()
    data = json.loads(rec.to_json())
    assert data["schema"] == "df/probe/v1"
    assert ProbeRecord.from_json(rec.to_json()) == rec
    with pytest.raises(ConfigError):
        ProbeRecord.from_json(json.dumps({**data, "schema": "df/probe/v2"}))
    with pytest.raises(ConfigError):
        ProbeRecord.from_json(json.dumps({**data, "extra": 1}))


# ---------- CLI ----------


def raw_with(
    install: InstallConfig | None, models_text: str | None
) -> state.RawSnapshot:
    return state.RawSnapshot(
        0,
        None,
        None,
        None if install is None else dump_install(install).encode(),
        None if models_text is None else models_text.encode(),
        False,
    )


MODELS = (
    'schema = "df/models/v1"\n'
    '[roles.author]\nharness = "claude"\nmodel = "claude-opus-5-5"\n'
    '[roles.judge]\nharness = "codex"\nmodel = "gpt-5.5"\n'
)


def doctor(world: World, argv: list[str], read: object | None = None) -> int:
    snap = Stable(raw_with(world.install, MODELS), 0) if read is None else read
    return cli.main(
        ["doctor", *argv],
        mode="reader",
        read=snap,  # type: ignore[arg-type]
        lock_fd=None,
        layout=world.lay,
    )


def test_doctor_probe_models_needs_root(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    world.mp.setattr(cli, "ROOT_UID", os.geteuid() + 1)
    assert doctor(world, ["--probe-models"]) == 2
    assert "нужен sudo" in capsys.readouterr().err


def test_doctor_probe_models_stage1(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """Stage 1: no role users, so every chosen role is «не проверено»; the
    runner is never reached and no record is written."""

    def missing(name: str) -> object:
        raise KeyError(name)

    world.mp.setattr(cli, "ROOT_UID", os.geteuid())
    world.mp.setattr(probe, "_getpwnam", missing)
    world.mp.setattr(probe, "run_as", Spy(ok_result()))
    assert doctor(world, ["--probe-models"]) == 0
    out = capsys.readouterr().out
    assert "author: не проверено: роль df-exec не создана (этап 2)" in out
    assert "judge: не проверено: роль df-review не создана (этап 2)" in out
    assert "interview: модель не задана" in out
    assert list(world.lay.probes_dir.iterdir()) == []


def test_doctor_probe_models_runs_and_records(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    spy = Spy(ok_result())
    world.mp.setattr(cli, "ROOT_UID", os.geteuid())
    world.mp.setattr(probe, "run_as", spy)
    assert doctor(world, ["--probe-models"]) == 0
    out = capsys.readouterr().out
    assert "author: confirmed" in out
    assert (world.lay.probes_dir / "author.json").exists()
    assert len(spy.calls) == 1  # judge's codex is not approved


def test_doctor_probe_models_creates_probe_dirs(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    world.lay.probes_dir.rmdir()
    world.lay.probe_cwd.rmdir()
    world.mp.setattr(cli, "ROOT_UID", os.geteuid())
    world.mp.setattr(probe, "run_as", Spy(ok_result()))
    assert doctor(world, ["--probe-models"]) == 0
    assert world.lay.probe_cwd.is_dir() and world.lay.probes_dir.is_dir()
    assert os.stat(world.lay.probe_cwd).st_mode & 0o777 == 0o755


def test_doctor_probe_models_transitional_and_product(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    world.mp.setattr(cli, "ROOT_UID", os.geteuid())
    assert doctor(world, ["--probe-models"], Transitional(3)) == 2
    assert "идёт или прервана административная операция" in capsys.readouterr().err
    assert doctor(world, ["--probe-models", "--product", "42"]) == 2
    assert "реестр продуктов не поддерживается до этапа 3" in capsys.readouterr().err


def test_plain_doctor_is_still_the_stub(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    assert doctor(world, []) == 2
    assert "Task 12" in capsys.readouterr().err
    assert doctor(world, [], Transitional(3)) == 2
    assert "Task 12" in capsys.readouterr().err


def models_list(world: World, capsys: pytest.CaptureFixture[str]) -> list[dict]:
    code = cli.main(
        ["models", "list", "--json"],
        mode="reader",
        read=Stable(raw_with(world.install, MODELS), 0),
        lock_fd=None,
        layout=world.lay,
    )
    out = capsys.readouterr()
    assert code == 0, out.err
    return json.loads(out.out)


def test_models_list_status_columns(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """R65: no record → «не проверено»; a record read by a non-root reader →
    «данные недоступны: нужен sudo»; JSON has status, current, auth_tracking."""
    world.mp.setattr(cli, "ROOT_UID", os.geteuid() + 1)
    rows = {r["role"]: r for r in models_list(world, capsys)}
    assert "probe" not in rows["author"]
    assert rows["author"]["status"] == "не проверено"
    assert rows["author"]["current"] is False
    assert rows["author"]["auth_tracking"] is None
    choice = ModelChoice("claude", "claude-opus-5-5", None, {})
    rec = record(config_hash=models.config_hash(choice))
    (world.lay.probes_dir / "author.json").write_text(rec.to_json())
    rows = {r["role"]: r for r in models_list(world, capsys)}
    assert rows["author"]["status"] == "данные недоступны: нужен sudo"
    assert rows["author"]["auth_tracking"] == "tracked"
    assert rows["judge"]["status"] == "не проверено"


def test_models_list_status_as_root(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """A root reader observes everything: a fresh probe is current."""
    world.mp.setattr(cli, "ROOT_UID", os.geteuid())
    cred = world.home / CRED
    cred.parent.mkdir()
    cred.write_text("token")
    world.cfg = models.parse_models(MODELS)
    assert isinstance(world.run(Spy(ok_result())), ProbeRecord)
    rows = {r["role"]: r for r in models_list(world, capsys)}
    assert rows["author"]["status"].startswith("подтверждён ")
    assert rows["author"]["current"] is True
    cred.write_text("other token")
    rows = {r["role"]: r for r in models_list(world, capsys)}
    assert rows["author"]["status"] == "устарела: auth"


# ---------- fix round 1 ----------


def test_auth_intermediate_link_is_unreadable(tmp_path: Path) -> None:
    """I1: a link at an intermediate directory below the home (e.g. to
    another role's `.claude`) is never followed: `unreadable`, never that
    file's fingerprint."""
    other = tmp_path / "other-role" / ".claude"
    other.mkdir(parents=True)
    (other / ".credentials.json").write_text("other role's token")
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude").symlink_to(other)
    obs = probe.observe_auth(make_profile(), home, role_uid=UID)
    assert obs == AuthObservation("unavailable", "unreadable", ())


def test_auth_relative_path_must_stay_in_home(tmp_path: Path) -> None:
    obs = probe.observe_auth(make_profile(auth_files=("../x",)), tmp_path, role_uid=UID)
    assert obs.reason == "unreadable"


def test_backend_unknown_reads_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kw: object) -> int:
        raise AssertionError("opened")

    monkeypatch.setattr(os, "open", boom)
    obs = probe.observe_auth(make_profile(auth_files=None), tmp_path, role_uid=UID)
    assert obs.reason == "backend_unknown"


def test_elf_executable_accepted(world: World) -> None:
    elf = plat.ELF_MAGIC + bytes(range(60))
    world.exe.write_bytes(elf)
    world.sha = hashlib.sha256(elf).hexdigest()
    world.register(make_profile())
    argv = probe.claude_argv(world.exe, CHOICE)
    got = probe.admit("claude", world.exe, PLATFORM, argv=argv, env={})
    assert got == make_profile()


def test_record_sha_is_the_admitted_one(world: World) -> None:
    """M3: the record's `executable_sha256` is the SHA admission hashed (a
    different one from forming argv is refused: the addendum's test)."""
    real = probe.admission
    seen: list[str] = []

    def spy(*args: object, **kw: object) -> object:
        got = real(*args, **kw)  # type: ignore[arg-type]
        assert isinstance(got, probe.Admission)
        seen.append(got.executable_sha256)
        return got

    world.mp.setattr(probe, "admission", spy)
    rec = world.run(Spy(ok_result()))
    assert isinstance(rec, ProbeRecord) and rec.executable_sha256 == seen[0]
    assert seen == [world.sha]


@pytest.mark.parametrize(
    "patch",
    [
        {"auth_files": [{"path": 1, "mtime_ns": 1, "sha256": "a"}]},
        {"auth_files": [{"path": "p", "mtime_ns": True, "sha256": "a"}]},
        {"auth_files": [{"path": "p", "mtime_ns": 1, "sha256": "a", "x": 1}]},
        {"params": {"k": 1}},
    ],
)
def test_record_entries_type_checked(world: World, patch: dict[str, object]) -> None:
    """M7: malformed entries are `ConfigError`; `read_record` then reads the
    record as absent («не проверено», never current)."""
    data = json.loads(record().to_json())
    text = json.dumps({**data, **patch})
    with pytest.raises(ConfigError):
        ProbeRecord.from_json(text)
    (world.lay.probes_dir / "author.json").write_text(text)
    assert probe.read_record(world.lay, "author", None) is None


def test_doctor_non_root_checked_before_transitional(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    world.mp.setattr(cli, "ROOT_UID", os.geteuid() + 1)
    assert doctor(world, ["--probe-models"], Transitional(3)) == 2
    err = capsys.readouterr().err
    assert "нужен sudo" in err and "административная операция" not in err


TWO_CLAUDE = (
    'schema = "df/models/v1"\n'
    '[roles.author]\nharness = "claude"\nmodel = "claude-opus-5-5"\n'
    '[roles.reviewer]\nharness = "claude"\nmodel = "claude-opus-5-5"\n'
)


def test_doctor_role_exception_reported_and_loop_continues(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """M4: one role's runner exception is reported for it, the rest are
    probed, and the exit code is 2."""
    calls: list[str] = []

    def runner(user: str, argv: Sequence[str], **kw: object) -> RunResult:
        calls.append(user)
        if len(calls) == 1:
            raise OSError("boom")
        return ok_result()

    world.mp.setattr(cli, "ROOT_UID", os.geteuid())
    world.mp.setattr(probe, "run_as", runner)
    snap = Stable(raw_with(world.install, TWO_CLAUDE), 0)
    assert doctor(world, ["--probe-models"], snap) == 2
    out = capsys.readouterr().out
    assert "author: ошибка пробы: OSError: boom" in out
    assert "reviewer: confirmed" in out
    assert calls == ["df-exec", "df-review"]


def test_root_models_list_hashes_each_binary_once(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """M5: two roles on one harness binary: one `executable_target`."""
    world.mp.setattr(cli, "ROOT_UID", os.geteuid())
    world.cfg = models.parse_models(TWO_CLAUDE)
    for role in ("author", "reviewer"):
        assert isinstance(world.run(Spy(ok_result()), role), ProbeRecord)
    seen: list[Path] = []
    real = probe.executable_target

    def counting(path: Path) -> tuple[Path, str] | str:
        seen.append(path)
        return real(path)

    world.mp.setattr(probe, "executable_target", counting)
    code = cli.main(
        ["models", "list", "--json"],
        mode="reader",
        read=Stable(raw_with(world.install, TWO_CLAUDE), 0),
        lock_fd=None,
        layout=world.lay,
    )
    out = capsys.readouterr()
    assert code == 0, out.err
    assert seen == [world.exe]


# ---------- fix round 2: the role's ownership, not root's reach ----------

UNREADABLE_OBS = AuthObservation("unavailable", "unreadable", ())


def fake_owners(mp: pytest.MonkeyPatch, owners: Mapping[Path, int]) -> None:
    """Give objects (by inode) a virtual owner seen through `owner_uid`."""
    keys = {(os.lstat(p).st_dev, os.lstat(p).st_ino): uid for p, uid in owners.items()}
    real = fsutil.owner_uid
    mp.setattr(
        fsutil, "owner_uid", lambda st: keys.get((st.st_dev, st.st_ino), real(st))
    )


def make_cred(home: Path, text: str = "token") -> Path:
    cred = home / CRED
    cred.parent.mkdir(parents=True, exist_ok=True)
    cred.write_text(text)
    cred.chmod(0o600)
    return cred


def test_auth_all_role_owned_is_tracked(tmp_path: Path) -> None:
    make_cred(tmp_path)
    obs = probe.observe_auth(make_profile(), tmp_path, role_uid=UID)
    assert obs.tracking == "tracked"


def test_auth_root_can_read_role_cannot(tmp_path: Path) -> None:
    """Everything is readable by the reader, but none of it is the role's."""
    make_cred(tmp_path)
    obs = probe.observe_auth(make_profile(), tmp_path, role_uid=UID + 1)
    assert obs == UNREADABLE_OBS


def test_auth_foreign_owned_file_records_nothing(world: World) -> None:
    cred = make_cred(world.home, "FOREIGN-TOKEN")
    fake_owners(world.mp, {cred: UID + 1})
    obs = probe.observe_auth(make_profile(), world.home, role_uid=UID)
    assert obs == UNREADABLE_OBS
    rec = world.run(Spy(ok_result()))
    assert isinstance(rec, ProbeRecord)
    assert rec.auth_tracking == "unavailable: unreadable" and rec.auth_files == ()
    text = (world.lay.probes_dir / "author.json").read_text()
    assert hashlib.sha256(b"FOREIGN-TOKEN").hexdigest() not in text
    assert str(os.stat(cred).st_mtime_ns) not in text


@pytest.mark.parametrize("which", ["home", "intermediate"])
def test_auth_foreign_owned_directory_unreadable(tmp_path: Path, which: str) -> None:
    cred = make_cred(tmp_path)
    directory = tmp_path if which == "home" else cred.parent
    mp = pytest.MonkeyPatch()
    try:
        fake_owners(mp, {directory: UID + 1})
        obs = probe.observe_auth(make_profile(), tmp_path, role_uid=UID)
    finally:
        mp.undo()
    assert obs == UNREADABLE_OBS


def test_auth_hard_link_unreadable(tmp_path: Path) -> None:
    """A hard link (`st_nlink == 2`) may be a foreign file: never counted."""
    cred = make_cred(tmp_path)
    os.link(cred, tmp_path / "elsewhere")
    obs = probe.observe_auth(make_profile(), tmp_path, role_uid=UID)
    assert obs == UNREADABLE_OBS


@pytest.mark.parametrize("replacement", ["role-owned", "foreign"])
def test_auth_directory_swapped_mid_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: str
) -> None:
    """Between opening the home and `.claude`, `.claude` is replaced by
    another real directory with another credential file: its fingerprint
    appears only if the replacement passes every ownership check."""
    home = tmp_path / "home"
    make_cred(home, "original")
    evil = tmp_path / "evil"
    make_cred(evil, "EVIL-TOKEN")
    swapped = evil / ".claude"
    if replacement == "foreign":
        fake_owners(monkeypatch, {swapped: UID + 1})
    real = probe._open_below
    done: list[bool] = []

    def racing(name: str, dir_fd: int, *, directory: bool) -> int:
        if name == ".claude" and not done:
            done.append(True)
            os.rename(home / ".claude", tmp_path / "old")
            os.rename(swapped, home / ".claude")
        return real(name, dir_fd, directory=directory)

    monkeypatch.setattr(probe, "_open_below", racing)
    obs = probe.observe_auth(make_profile(), home, role_uid=UID)
    assert done
    evil_sha = hashlib.sha256(b"EVIL-TOKEN").hexdigest()
    shas = [f["sha256"] for f in obs.files]
    if replacement == "foreign":
        assert obs == UNREADABLE_OBS and evil_sha not in shas
    else:
        assert obs.tracking == "tracked" and shas == [evil_sha]


# ---------- fix round 2 addendum ----------


def test_binary_swapped_between_form_and_admission(world: World) -> None:
    """The SHA argv was formed for must be the one admission hashed; both
    are known (same profile), still refused before launch."""
    real = probe.executable_target
    shas = iter(["1" * 64, "2" * 64])

    def two_hashes(path: Path) -> tuple[Path, str] | str:
        found = real(path)
        assert not isinstance(found, str)
        return found[0], next(shas)

    prof = make_profile()
    world.mp.setattr(
        probe,
        "HARNESS_PROFILES",
        {("claude", PLATFORM, "1" * 64): prof, ("claude", PLATFORM, "2" * 64): prof},
    )
    world.mp.setattr(probe, "executable_target", two_hashes)
    spy = Spy(ok_result())
    result = world.run(spy)
    assert result == "не поддерживается: исполняемый файл изменился во время допуска"
    assert spy.calls == []


def test_home_link_is_unreadable(tmp_path: Path) -> None:
    real_home = tmp_path / "real"
    make_cred(real_home)
    (tmp_path / "home").symlink_to(real_home)
    obs = probe.observe_auth(make_profile(), tmp_path / "home", role_uid=UID)
    assert obs == UNREADABLE_OBS


def _open_fds() -> int:
    return len(os.listdir("/dev/fd"))


@pytest.mark.parametrize(
    "case",
    ["tracked", "missing", "leaf-link", "dir-link", "foreign-file", "foreign-dir"],
)
def test_auth_walk_closes_every_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    if case != "missing":
        cred = make_cred(home if case != "dir-link" else tmp_path / "other")
    if case == "leaf-link":
        cred.rename(home / "real")
        (home / CRED).symlink_to(home / "real")
    elif case == "dir-link":
        (home / ".claude").symlink_to(cred.parent)
    elif case == "foreign-file":
        fake_owners(monkeypatch, {cred: UID + 1})
    elif case == "foreign-dir":
        fake_owners(monkeypatch, {cred.parent: UID + 1})
    before = _open_fds()
    for _ in range(3):
        probe.observe_auth(make_profile(), home, role_uid=UID)
    assert _open_fds() == before


# ---------- fix round 3: only delete-deny ACLs are tolerated ----------

darwin_only = pytest.mark.skipif(sys.platform != "darwin", reason="macOS ACLs")


@contextlib.contextmanager
def acl_on(*items: tuple[Path, str]) -> Iterator[None]:
    """Real ACL entries (`chmod +a`), removed again on exit."""
    try:
        for path, entry in items:
            subprocess.run(["/bin/chmod", "+a", entry, str(path)], check=True)
        yield
    finally:
        for path, _ in items:
            subprocess.run(["/bin/chmod", "-N", str(path)], check=False)


def _where(home: Path, which: str) -> Path:
    return {"home": home, "dir": home / ".claude", "file": home / CRED}[which]


@darwin_only
@pytest.mark.parametrize("which", ["home", "dir", "file"])
def test_acl_deny_delete_tolerated(tmp_path: Path, which: str) -> None:
    """Every real macOS home carries `group:everyone deny delete`."""
    make_cred(tmp_path)
    with acl_on((_where(tmp_path, which), "group:everyone deny delete")):
        obs = probe.observe_auth(make_profile(), tmp_path, role_uid=UID)
    assert obs.tracking == "tracked"


@darwin_only
@pytest.mark.parametrize(
    "entries",
    [
        ("everyone allow read",),
        (f"user:{os.environ.get('USER') or 'root'} deny read",),
        ("group:everyone deny delete", "group:everyone deny read"),
        ("group:everyone deny delete,write",),
    ],
    ids=["allow-read", "user-deny-read", "deny-delete+deny-read", "deny-write"],
)
@pytest.mark.parametrize("which", ["home", "dir", "file"])
def test_acl_reducing_entries_unreadable(
    tmp_path: Path, which: str, entries: tuple[str, ...]
) -> None:
    make_cred(tmp_path)
    path = _where(tmp_path, which)
    with acl_on(*((path, e) for e in entries)):
        obs = probe.observe_auth(make_profile(), tmp_path, role_uid=UID)
    assert obs == UNREADABLE_OBS


@darwin_only
def test_acl_text_failure_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_cred(tmp_path)
    monkeypatch.setattr(probe.acl, "acl_text_fd", lambda fd: None)
    with acl_on((tmp_path / CRED, "group:everyone deny delete")):
        obs = probe.observe_auth(make_profile(), tmp_path, role_uid=UID)
    assert obs == UNREADABLE_OBS


def test_any_acl_off_macos_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Linux (v1 limit): any POSIX ACL is unreadable, never parsed."""
    make_cred(tmp_path)
    monkeypatch.setattr(probe, "_IS_DARWIN", False)
    monkeypatch.setattr(probe.acl, "acl_present_fd", lambda fd: True)
    monkeypatch.setattr(probe.acl, "acl_text_fd", lambda fd: "!#acl 1\n")
    obs = probe.observe_auth(make_profile(), tmp_path, role_uid=UID)
    assert obs == UNREADABLE_OBS


def test_acl_probe_error_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_cred(tmp_path)

    def broken(fd: int) -> bool:
        raise OSError(5, "EIO")

    monkeypatch.setattr(probe.acl, "acl_present_fd", broken)
    obs = probe.observe_auth(make_profile(), tmp_path, role_uid=UID)
    assert obs == UNREADABLE_OBS


class _ModeView:
    """An `os.stat_result` whose `st_mode` is replaced, the rest real."""

    def __init__(self, real: os.stat_result, mode: int) -> None:
        self._real, self.st_mode = real, mode

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)


@pytest.mark.parametrize(
    "which, change",
    [
        ("file", lambda m: m & ~stat.S_IRUSR),
        ("dir", lambda m: m & ~stat.S_IXUSR),
        ("file", lambda m: (m & ~stat.S_IFMT(m)) | stat.S_IFIFO),
    ],
    ids=["file-no-u+r", "dir-no-u+x", "file-not-regular"],
)
def test_owner_mode_bits_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    which: str,
    change: Callable[[int], int],
) -> None:
    """N2: a file without u+r, a directory without u+x, a leaf that is not
    a regular file: unreadable. Root would open each of them, so the mode is
    changed only in what `fstat` reports for the target inode — a normal
    user's open would otherwise fail first and hide the check."""
    cred = make_cred(tmp_path)
    target = os.stat(cred if which == "file" else cred.parent).st_ino
    real = os.fstat

    def fake(fd: int) -> object:
        st = real(fd)
        return _ModeView(st, change(st.st_mode)) if st.st_ino == target else st

    monkeypatch.setattr(probe.os, "fstat", fake)
    obs = probe.observe_auth(make_profile(), tmp_path, role_uid=UID)
    assert obs == UNREADABLE_OBS


def test_missing_home_unreadable(tmp_path: Path) -> None:
    obs = probe.observe_auth(make_profile(), tmp_path / "absent", role_uid=UID)
    assert obs == UNREADABLE_OBS
