"""Explicit model probes (spec 3.6): admission, the one launch, the record.

`darkfactory doctor --probe-models` runs `run_probe` for each role with a
choice. Admission (`admit`) happens before any execution of the harness:
the executable is identified by the SHA-256 of its final target, the
profile pinned to `(harness, platform, sha256)` must have verified
isolation, and the managed context must be observable and approved, or
excluded by the pinned invocation itself. The harness then runs exactly
once, with the argv and environment that passed admission; there is no
separate `--version` call (rev 8, r4). The adapter parses the provider's
answer itself; the role's output is never taken as «checked».

`HARNESS_PROFILES` pins the binaries checked on real harnesses (Task 11,
Step 3, evidence `docs/evidence/2026-10-24-df-probe-isolation.md`); a
profile whose isolation is not verified refuses every probe («не
поддерживается»).
"""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import stat
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from df import acl, fsutil
from df import platform as plat
from df.config import ConfigError, InstallConfig
from df.models import ROLE_CONTEXT, ROLES, ModelChoice, ModelsConfig, config_hash
from df.models import resolve as resolve_choice
from df.paths import Layout
from df.platform import PlatformName, RunResult, detect_platform, run_as
from df.stage2_config import ModelPin

ADAPTER_VERSION = "2"
PROBE_PROMPT = "Reply with the single word OK."
PROBE_TIMEOUT = 120
SCHEMA = "df/probe/v1"
#: The value of claude's `--setting-sources` that disables user, project and
#: local settings (sentinels did not fire with it on 2.1.309, did without).
CLAUDE_SETTING_SOURCES = ""
#: Harness project files that must not be in the probe cwd or its parents.
PROJECT_FILES = (".claude", "CLAUDE.md", "AGENTS.md", ".codex", ".git")
#: A profile's version when no profile is pinned to the current executable.
UNKNOWN_VERSION = "неизвестна"
NOT_PARSED = "ответ не разобран"
NO_ACCESS = "доступ не подтверждён"
UNSUPPORTED = "не поддерживается"
NOT_CHECKED = "не проверено"

_READ_LIMIT = 1 << 20
_IS_DARWIN = sys.platform == "darwin"
_REASON_LIMIT = 300
_PEEK_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
_NO_WRITE_OTHERS = stat.S_IWGRP | stat.S_IWOTH

# Seams for tests: the role user lookup and the clock.
_getpwnam = pwd.getpwnam


def _now() -> datetime:
    return datetime.now(UTC)


Runner = Callable[..., RunResult]
ArgvBuilder = Callable[[Path, ModelChoice], list[str]]

# ---------- the pinned invocation ----------

#: Flags the adapter passes for each significant parameter, per harness: the
#: same keys as `models.SUPPORTED_PARAMS` (empty in v1).
PARAM_FLAGS: Mapping[str, Mapping[str, Callable[[str], list[str]]]] = MappingProxyType(
    {"claude": MappingProxyType({}), "codex": MappingProxyType({})}
)


def param_argv(choice: ModelChoice) -> list[str]:
    """The flags of `choice.params`; a parameter the adapter has no flag for
    is `ConfigError` (it would make the probe check another configuration)."""
    flags = PARAM_FLAGS.get(choice.harness, {})
    argv: list[str] = []
    for key in sorted(choice.params):
        flag = flags.get(key)
        if flag is None:
            raise ConfigError(
                f"params.{key}: адаптер {choice.harness} не умеет передать параметр"
            )
        argv += flag(choice.params[key])
    return argv


def claude_argv(harness: Path, choice: ModelChoice) -> list[str]:
    """The probe argv of claude, verified on 2.1.309 (evidence 2026-10-24)."""
    effort = ["--effort", choice.effort] if choice.effort else []
    return [
        str(harness),
        "-p",
        "--model",
        choice.model,
        "--output-format",
        "json",
        "--max-turns",
        "1",
        "--setting-sources",
        CLAUDE_SETTING_SOURCES,
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--tools",
        "",
        *effort,
        *param_argv(choice),
        # `--tools <tools...>` is variadic: without `--` an empty tool list
        # swallows the prompt (claude 2.1.309, evidence 2026-10-24).
        "--",
        PROBE_PROMPT,
    ]


def codex_argv(harness: Path, choice: ModelChoice) -> list[str]:
    """The probe argv of codex, run on 0.163.0 (evidence 2026-10-24): the
    auth store is pinned to `file`; project instruction files are off."""
    effort = (
        ["-c", f'model_reasoning_effort="{choice.effort}"'] if choice.effort else []
    )
    return [
        str(harness),
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
        choice.model,
        "--json",
        "--skip-git-repo-check",
        "--ephemeral",
        "--sandbox",
        "read-only",
        *effort,
        *param_argv(choice),
        PROBE_PROMPT,
    ]


# ---------- profiles ----------


@dataclass(frozen=True)
class Exclusion:
    """Elements of the pinned invocation that exclude an unobservable managed
    source: a contiguous run of argv and environment pairs. Nothing else is
    representable — a condition on the auth method, the account or the
    organisation's settings cannot be written, and such a profile is not
    supported."""

    argv: tuple[str, ...]
    env: Mapping[str, str]

    def __post_init__(self) -> None:
        if not self.argv and not self.env:
            raise ConfigError("exclusion: пустое условие ничего не исключает")
        object.__setattr__(self, "argv", tuple(self.argv))
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))


@dataclass(frozen=True)
class ManagedSource:
    """One managed-configuration source of a harness version on a platform.

    An observable source (`file`, `mdm`: a file at `locator`) must be absent
    or hash into `allowed_digests`; an unobservable one (e.g. `server`) is
    allowed only with `excluded_by` holding on the actual invocation.
    """

    kind: Literal["file", "mdm", "server"]
    locator: str
    observable: bool
    allowed_digests: frozenset[str]
    excluded_by: Exclusion | None


@dataclass(frozen=True)
class HarnessProfile:
    """The adapter's profile of one executable (`(harness, platform, sha)`).

    `auth_files` are paths relative to the role's home that the harness is
    pinned to; `None` means the adapter cannot establish the auth source
    (`backend_unknown`, e.g. the macOS keychain).
    """

    version: str
    argv: ArgvBuilder
    auth_files: tuple[str, ...] | None
    managed: tuple[ManagedSource, ...]
    isolation_verified: bool


def _absent_or_refused(kind: Literal["file", "mdm"], locator: str) -> ManagedSource:
    """An observable source with no approved content: absent is admitted,
    anything present (a directory included: it is not a regular file) is a
    refusal until its digest is approved in the profile."""
    return ManagedSource(kind, locator, True, frozenset(), None)


def _unexcluded(kind: Literal["mdm", "server"], locator: str) -> ManagedSource:
    """An unobservable source for which Step 3 found no `Exclusion` that
    holds under every auth method: admission always refuses it."""
    return ManagedSource(kind, locator, False, frozenset(), None)


_CLAUDE_ROOT = "/Library/Application Support/ClaudeCode"
_CLAUDE_PLIST = "com.anthropic.claudecode.plist"
#: claude 2.1.309 on macOS: the managed sources, closed as far as found in
#: the binary's strings and its debug log (evidence 2026-10-24). MDM is read
#: as plist files with `plutil` (device level and per user — the role's).
CLAUDE_MANAGED_MACOS: tuple[ManagedSource, ...] = (
    _absent_or_refused("file", f"{_CLAUDE_ROOT}/managed-settings.json"),
    _absent_or_refused("file", f"{_CLAUDE_ROOT}/managed-settings.d"),
    _absent_or_refused("file", f"{_CLAUDE_ROOT}/managed-mcp.json"),
    _absent_or_refused("file", f"{_CLAUDE_ROOT}/CLAUDE.md"),
    _absent_or_refused("file", f"{_CLAUDE_ROOT}/.claude"),
    _absent_or_refused("mdm", f"/Library/Managed Preferences/{_CLAUDE_PLIST}"),
    *(
        _absent_or_refused("mdm", f"/Library/Managed Preferences/{u}/{_CLAUDE_PLIST}")
        for u in sorted(set(ROLE_CONTEXT.values()))
    ),
    _unexcluded("server", "api.anthropic.com/api/claude_code/settings"),
    _unexcluded("server", "api.anthropic.com/api/claude_code/policy_limits"),
    # The server payload's on-disk cache in the role's config dir: a vehicle
    # of the same server delivery, seeded from disk at start.
    _unexcluded("server", "<role home>/.claude/remote-settings.json"),
)
#: codex 0.163.0 on macOS (evidence 2026-10-24). MDM goes through
#: CFPreferences (domain `com.openai.codex`, keys `config_toml_base64`,
#: `requirements_toml_base64`): cfprefsd, not one file, so not observable.
CODEX_MANAGED_MACOS: tuple[ManagedSource, ...] = (
    _absent_or_refused("file", "/etc/codex/config.toml"),
    _absent_or_refused("file", "/etc/codex/requirements.toml"),
    _absent_or_refused("file", "/etc/codex/managed_config.toml"),
    _unexcluded("mdm", "cfprefs:com.openai.codex"),
    _unexcluded("server", "chatgpt.com/backend-api/wham/config/bundle"),
)

#: Profiles keyed by `(harness, platform, SHA-256 of the executable)`. Only
#: the macOS arm64 binaries of R66 are pinned here; Linux SHAs come with the
#: native acceptance (Task 13). `isolation_verified` is set only for a
#: version that passed every check of Step 3 (evidence 2026-10-24).
HARNESS_PROFILES: Mapping[tuple[str, PlatformName, str], HarnessProfile] = (
    MappingProxyType(
        {
            (
                "claude",
                "macos-arm64",
                "5eadc0009cdcdbc0d06e58110d7b2baf88ced6088b1c33a60a21217fe410619c",
            ): HarnessProfile(
                version="2.1.309",
                argv=claude_argv,
                auth_files=None,  # the macOS keychain: backend_unknown
                managed=CLAUDE_MANAGED_MACOS,
                isolation_verified=False,
            ),
            (
                "codex",
                "macos-arm64",
                "8813367285524ac798dd96d1b071aea30d58708c7226fd236cd275cf1cfdee31",
            ): HarnessProfile(
                version="0.163.0",
                argv=codex_argv,
                auth_files=(".codex/auth.json",),
                managed=CODEX_MANAGED_MACOS,
                isolation_verified=False,
            ),
        }
    )
)


def exclusion_holds(ex: Exclusion, argv: Sequence[str], env: Mapping[str, str]) -> bool:
    """`ex.argv` is a contiguous run of `argv` and every pair of `ex.env` is
    in `env` with the same value."""
    return _contains(argv, ex.argv) and all(
        env.get(key) == value for key, value in ex.env.items()
    )


def _contains(argv: Sequence[str], run: Sequence[str]) -> bool:
    items, want = list(argv), list(run)
    size = len(want)
    return any(items[i : i + size] == want for i in range(len(items) - size + 1))


# ---------- admission (nothing is executed) ----------


@dataclass(frozen=True)
class Admission:
    """A passed admission: the profile and the SHA-256 that was hashed."""

    profile: HarnessProfile
    executable_sha256: str


def admit(
    harness: str,
    path: Path,
    platform: PlatformName,
    *,
    argv: Sequence[str],
    env: Mapping[str, str],
) -> HarnessProfile | str:
    """Admission before any launch (spec 3.6); a string is the refusal.

    `path` must already be the approved CLI of `harness` (spec 3.10,
    `InstallConfig.approved_cli`): that approval is the caller's
    precondition, not checked here. `argv` and `env` are exactly the ones
    the probe will run with: argv[0] must be the final target of `path`, and
    every exclusion is checked on them. Nothing is executed here.
    """
    found = admission(harness, path, platform, argv=argv, env=env)
    return found if isinstance(found, str) else found.profile


def admission(
    harness: str,
    path: Path,
    platform: PlatformName,
    *,
    argv: Sequence[str],
    env: Mapping[str, str],
) -> Admission | str:
    """`admit`, also naming the SHA-256 it hashed (the record's
    `executable_sha256` is the admitted file's)."""
    found = executable_target(path)
    if isinstance(found, str):
        return found
    target, sha = found
    profile = profile_for(harness, platform, sha)
    if isinstance(profile, str):
        return profile
    if not profile.isolation_verified:
        return f"{UNSUPPORTED}: изоляция не подтверждена"
    if not argv or argv[0] != str(target):
        return f"{UNSUPPORTED}: argv[0] не цель утверждённого пути {target}"
    _, problems = managed_context(profile)
    for src in profile.managed:
        ex = src.excluded_by
        if not src.observable and ex is not None and not exclusion_holds(ex, argv, env):
            problems.append(f"{src.kind} {src.locator}: исключение не в этом вызове")
    if problems:
        return f"{UNSUPPORTED}: " + "; ".join(problems)
    return Admission(profile, sha)


def profile_for(harness: str, platform: PlatformName, sha: str) -> HarnessProfile | str:
    """The profile pinned to `(harness, platform, sha)`, else the refusal."""
    profile = HARNESS_PROFILES.get((harness, platform, sha))
    if profile is None:
        return f"{UNSUPPORTED}: неизвестный исполняемый файл {sha}"
    return profile


def executable_target(path: Path) -> tuple[Path, str] | str:
    """`(final target, SHA-256)` of an approved path; a string is the refusal.

    Only a single native executable (Mach-O, ELF) is supported: a `#!`
    script install's behaviour is not defined by one file. The magic and the
    hash are read from one open of the target.
    """
    if not path.is_absolute():
        return f"{UNSUPPORTED}: {path}: путь не абсолютный"
    target = Path(os.path.realpath(path))
    try:
        fd = os.open(target, _PEEK_FLAGS | os.O_NOFOLLOW)
    except OSError as exc:
        return f"{UNSUPPORTED}: {target}: {exc.strerror or exc}"
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return f"{UNSUPPORTED}: {target}: не обычный файл"
        head = os.read(fd, 4)
        if head.startswith(b"#!"):
            return f"{UNSUPPORTED}: установка-скрипт ({target})"
        if head not in plat.MACHO_MAGIC and head != plat.ELF_MAGIC:
            return f"{UNSUPPORTED}: неизвестный формат исполняемого файла"
        digest = hashlib.sha256(head)
        while chunk := os.read(fd, 1 << 20):
            digest.update(chunk)
        return target, digest.hexdigest()
    except OSError as exc:
        return f"{UNSUPPORTED}: {target}: {exc.strerror or exc}"
    finally:
        os.close(fd)


def managed_context(profile: HarnessProfile) -> tuple[str | None, list[str]]:
    """`(digest of the observable sources, problems)`.

    Each observable source is absent or its hash is approved; a present
    unapproved or unreadable one is a problem, and so is an unobservable
    source without an exclusion (whether it holds is the caller's check on
    the actual invocation). The digest is `None` when there are problems.
    """
    seen: list[list[str]] = []
    problems: list[str] = []
    for src in profile.managed:
        name = f"{src.kind} {src.locator}"
        if not src.observable or src.kind == "server":
            if src.observable or src.excluded_by is None:
                problems.append(f"{name}: не наблюдаем и не исключён вызовом")
            continue
        try:
            found = _hash_path(Path(src.locator))
        except FileNotFoundError:
            seen.append([src.kind, src.locator, "absent"])
            continue
        except (OSError, ValueError) as exc:
            problems.append(f"{name}: не прочитан ({_os_text(exc)})")
            continue
        if found[1] not in src.allowed_digests:
            problems.append(f"{name}: неподтверждённое содержимое {found[1]}")
            continue
        seen.append([src.kind, src.locator, found[1]])
    if problems:
        return None, problems
    canonical = json.dumps(seen, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), []


def probe_cwd_problems(layout: Layout) -> list[str]:
    """`layout.probe_cwd` exists, is a directory of root nobody else writes,
    and is empty; neither it nor a parent holds a harness project file."""
    cwd = layout.probe_cwd
    try:
        st = os.lstat(cwd)
    except OSError as exc:
        return [f"{cwd}: {exc.strerror or exc}"]
    if not stat.S_ISDIR(st.st_mode):
        return [f"{cwd}: не каталог"]
    problems: list[str] = []
    if fsutil.owner_uid(st) != plat.ROOT_UID:
        problems.append(f"{cwd}: владелец uid {fsutil.owner_uid(st)}, не root")
    if st.st_mode & _NO_WRITE_OTHERS:
        problems.append(f"{cwd}: запись для группы или всех")
    try:
        if os.listdir(cwd):
            problems.append(f"{cwd}: не пуст")
    except OSError as exc:
        problems.append(f"{cwd}: {exc.strerror or exc}")
    for directory in (cwd, *cwd.parents):
        problems += [
            f"{directory / name}: проектный файл харнесса"
            for name in PROJECT_FILES
            if os.path.lexists(directory / name)
        ]
    return problems


def prepare_dirs(layout: Layout) -> None:
    """Create the doctor directory, the probes directory and the empty probe
    cwd (`0755`, root) durably; existing ones are settled, not replaced."""
    for path in (layout.doctor_dir, layout.probes_dir, layout.probe_cwd):
        fd = os.open(path.parent, _DIR_FLAGS)
        try:
            fsutil.make_dir(fd, path.name, 0o755, path=path)
        finally:
            os.close(fd)


# ---------- parsing the answer ----------


@dataclass(frozen=True)
class ProbeParse:
    """The adapter's reading of the harness output."""

    ok: bool
    actual_model: str | None
    error: str | None


_UNPARSED = ProbeParse(False, None, NOT_PARSED)


def parse_output(harness: str, stdout: bytes) -> ProbeParse:
    """claude: one JSON result object, the model is the only `modelUsage`
    key; codex: JSONL, `turn.completed` is success, no model reported.
    Anything else is not parsed (`ok=False`)."""
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError:
        return _UNPARSED
    if harness == "claude":
        return _parse_claude(text)
    if harness == "codex":
        return _parse_codex(text)
    return _UNPARSED


def _parse_claude(text: str) -> ProbeParse:
    try:
        data = json.loads(text)
    except ValueError:
        return _UNPARSED
    if not isinstance(data, dict) or data.get("type") != "result":
        return _UNPARSED
    is_error = data.get("is_error")
    if not isinstance(is_error, bool):
        return _UNPARSED
    if is_error:
        return ProbeParse(False, None, _clip(data.get("result") or data.get("subtype")))
    usage = data.get("modelUsage")
    model = next(iter(usage)) if isinstance(usage, dict) and len(usage) == 1 else None
    return ProbeParse(True, model, None)


def _parse_codex(text: str) -> ProbeParse:
    """codex 0.163.0 `exec --json` (sequences pinned in the fixtures from
    the real binary, evidence 2026-10-24): a turn ends with exactly one
    `turn.completed` or `turn.failed`, and that event is the last one. A
    completed turn may follow top-level `error` events — the harness's
    «Reconnecting… n/5» retry notices — so `error` alone decides nothing;
    a failed turn names its error. No terminal event (e.g. the harness
    retrying an unreachable network until the timeout) is not success."""
    events: list[dict[str, object]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            return _UNPARSED
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            return _UNPARSED
        events.append(event)
    ends = [e for e in events if e["type"] in ("turn.completed", "turn.failed")]
    if not ends:
        errors = [e for e in events if e["type"] == "error"]
        return (
            ProbeParse(False, None, _clip(_codex_error(errors[-1])))
            if errors
            else _UNPARSED
        )
    if len(ends) != 1 or ends[0] is not events[-1]:
        return _UNPARSED
    if ends[0]["type"] == "turn.failed":
        return ProbeParse(False, None, _clip(_codex_error(ends[0])))
    return ProbeParse(True, None, None)


def _codex_error(event: Mapping[str, object]) -> object:
    error = event.get("error")
    if isinstance(error, dict):
        return error.get("message")
    return event.get("message")


def _clip(value: object) -> str:
    """A harness-supplied message, one printable line of bounded length."""
    if not isinstance(value, str) or not value.strip():
        return "ошибка харнесса без сообщения"
    line = "".join(ch if ch.isprintable() else " " for ch in value).strip()
    return line[:_REASON_LIMIT]


# ---------- authorization ----------


@dataclass(frozen=True)
class AuthObservation:
    """What the adapter can observe of the role's authorization: the pinned
    files' mtime and hash, never their content."""

    tracking: Literal["tracked", "unavailable"]
    reason: Literal["no_file", "unreadable", "backend_unknown"] | None
    files: tuple[dict[str, object], ...]


def observe_auth(
    profile: HarnessProfile, home: Path, *, role_uid: int
) -> AuthObservation:
    """Fingerprint the auth files the profile pins, under `home`.

    `backend_unknown` reads nothing; `tracked` when at least one file was
    read; `no_file` when none exists. `unreadable` when one exists but is
    not provably the role's own: root's reach is not the role's, so `home`,
    every directory below it and the file are checked on the opened fd —
    owned by `role_uid`, an ACL only if it denies nothing but deleting
    (macOS), directories searchable and the file
    readable by its owner, the file regular with one link; a link at any
    component is never followed. Nothing of such a file is recorded. An
    unpinned readable file never gives `tracked`.
    """
    if profile.auth_files is None:
        return AuthObservation("unavailable", "backend_unknown", ())
    files: list[dict[str, object]] = []
    unreadable = False
    for rel in profile.auth_files:
        path = home / rel
        try:
            mtime_ns, sha = _hash_under(home, rel, role_uid)
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            unreadable = True
            continue
        files.append({"path": str(path), "mtime_ns": mtime_ns, "sha256": sha})
    if files:
        return AuthObservation("tracked", None, tuple(files))
    return AuthObservation("unavailable", "unreadable" if unreadable else "no_file", ())


def tracking_text(obs: AuthObservation) -> str:
    """`tracked` or `unavailable: <reason>` (the record's `auth_tracking`)."""
    return "tracked" if obs.tracking == "tracked" else f"unavailable: {obs.reason}"


def _hash_path(path: Path) -> tuple[int, str]:
    """`(mtime_ns, sha256)` of a managed file (links followed: its place is
    the administrator's)."""
    fd = os.open(path, _PEEK_FLAGS)
    try:
        return _hash_fd(fd, path)
    finally:
        os.close(fd)


def _hash_under(home: Path, rel: str, role_uid: int) -> tuple[int, str]:
    """`(mtime_ns, sha256)` of `home/rel`, opened component by component with
    `O_NOFOLLOW`, each opened fd checked by `_role_owns` before the next
    step: a link or a component that is not the role's own raises (unreadable),
    never another file's fingerprint. A missing component is
    `FileNotFoundError`."""
    parts = Path(rel).parts
    if not parts or Path(rel).is_absolute() or ".." in parts:
        raise ValueError(f"{rel}: путь не внутри домашнего каталога")
    # `home` itself is not followed either: a home that is a link is not
    # provably the role's own place (its target may be another role's)
    try:
        fd = os.open(home, _DIR_FLAGS | os.O_NOFOLLOW)
    except FileNotFoundError as exc:  # a role without its home proves nothing
        raise ValueError(f"{home}: нет домашнего каталога") from exc
    try:
        _role_owns(fd, home, role_uid, directory=True)
        here = home
        for part in parts[:-1]:
            here = here / part
            child = _open_below(part, fd, directory=True)
            os.close(fd)
            fd = child
            _role_owns(fd, here, role_uid, directory=True)
        leaf = _open_below(parts[-1], fd, directory=False)
    finally:
        os.close(fd)
    try:
        _role_owns(leaf, home / rel, role_uid, directory=False)
        return _hash_fd(leaf, home / rel)
    finally:
        os.close(leaf)


def _open_below(name: str, dir_fd: int, *, directory: bool) -> int:
    """Open one component of the auth walk, never following a link (the
    tests' seam to model a race between two steps)."""
    if directory:
        return os.open(name, _DIR_FLAGS | os.O_NOFOLLOW, dir_fd=dir_fd)
    return os.open(name, _PEEK_FLAGS | os.O_NOFOLLOW, dir_fd=dir_fd)


def _role_owns(fd: int, path: Path, role_uid: int, *, directory: bool) -> None:
    """The object open as `fd` is the role's own: owner `role_uid`, an ACL
    that cannot reduce the owner's read or search (`_acl_harmless`), a
    directory its owner can search or a regular one-link file its owner can
    read. Else `ValueError`."""
    st = os.fstat(fd)
    if fsutil.owner_uid(st) != role_uid:
        raise ValueError(f"{path}: владелец uid {fsutil.owner_uid(st)}, не роль")
    if directory:
        ok = stat.S_ISDIR(st.st_mode) and bool(st.st_mode & stat.S_IXUSR)
    else:
        ok = (
            stat.S_ISREG(st.st_mode)
            and bool(st.st_mode & stat.S_IRUSR)
            and st.st_nlink == 1
        )
    if not ok:
        raise ValueError(f"{path}: не доступен роли как её собственный")
    try:
        has_acl = acl.acl_present_fd(fd)
    except OSError as exc:
        raise ValueError(f"{path}: ACL не определён ({_os_text(exc)})") from exc
    if has_acl and not _acl_harmless(fd):
        raise ValueError(f"{path}: ACL может ограничить чтение ролью")


def _acl_harmless(fd: int) -> bool:
    """An ACL is tolerated only on macOS and only if every entry denies
    nothing but deleting (every real home carries `group:everyone deny
    delete`); an ACL text that cannot be read or parsed, and any POSIX ACL
    on Linux (v1 limit), are not."""
    if not _IS_DARWIN:
        return False
    text = acl.acl_text_fd(fd)
    return text is not None and acl.acl_only_denies_delete(text)


def _hash_fd(fd: int, path: Path) -> tuple[int, str]:
    """`(mtime_ns, sha256)` of an open regular file up to `_READ_LIMIT`."""
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{path}: не обычный файл")
    if st.st_size > _READ_LIMIT:
        raise ValueError(f"{path}: слишком большой")
    digest = hashlib.sha256()
    while chunk := os.read(fd, 1 << 16):
        digest.update(chunk)
    return st.st_mtime_ns, digest.hexdigest()


def _os_text(exc: BaseException) -> str:
    return (exc.strerror if isinstance(exc, OSError) else None) or str(exc)


# ---------- the record ----------


@dataclass(frozen=True)
class ProbeRecord:
    """One probe's result (`df/probe/v1`). `outcome` is only the result of
    the call; `auth_tracking` is whether its authorization is observable."""

    role: str
    product: int | None
    time: str
    harness: str
    harness_version: str
    adapter_version: str
    isolation: str
    requested_model: str
    actual_model: str | None
    effort: str
    params: Mapping[str, str]
    config_hash: str
    executable_sha256: str
    managed_digest: str
    auth_tracking: str
    auth_files: tuple[dict[str, object], ...]
    outcome: Literal["confirmed", "not_confirmed"]
    reason: str | None = None

    def to_json(self) -> str:
        """The record as `df/probe/v1` JSON."""
        data: dict[str, object] = {
            "schema": SCHEMA,
            **{f: getattr(self, f) for f in _FIELDS},
        }
        data["params"] = dict(self.params)
        data["auth_files"] = list(self.auth_files)
        return json.dumps(data, sort_keys=True, ensure_ascii=False, indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str) -> ProbeRecord:
        """Strictly parse `df/probe/v1` JSON; anything else is `ConfigError`."""
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ConfigError(f"{SCHEMA}: не JSON: {exc}") from exc
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            raise ConfigError(f"запись пробы: ожидалась схема {SCHEMA}")
        if set(data) != {"schema", *_FIELDS}:
            raise ConfigError(f"{SCHEMA}: неожиданный набор ключей")
        for name, kinds in _FIELD_TYPES.items():
            if not isinstance(data[name], kinds) or isinstance(data[name], bool):
                raise ConfigError(f"{SCHEMA}: {name}: неверный тип")
        if data["outcome"] not in ("confirmed", "not_confirmed"):
            raise ConfigError(f"{SCHEMA}: outcome: {data['outcome']!r}")
        files = data["auth_files"]
        if not all(_auth_file_ok(f) for f in files):
            raise ConfigError(f"{SCHEMA}: auth_files: неверная запись")
        if not all(_word_pair(k, v) for k, v in data["params"].items()):
            raise ConfigError(f"{SCHEMA}: params: неверный тип")
        values = {f: data[f] for f in _FIELDS}
        values["auth_files"] = tuple(files)
        return cls(**values)


_FIELDS: tuple[str, ...] = tuple(f.name for f in fields(ProbeRecord))
_STR = (str,)
_OPT_STR = (str, type(None))
_FIELD_TYPES: Mapping[str, tuple[type, ...]] = MappingProxyType(
    {
        **{f: _STR for f in _FIELDS},
        "product": (int, type(None)),
        "actual_model": _OPT_STR,
        "reason": _OPT_STR,
        "params": (dict,),
        "auth_files": (list,),
    }
)


def _auth_file_ok(entry: object) -> bool:
    """`{path: str, mtime_ns: int, sha256: str}` and nothing else."""
    if not isinstance(entry, dict) or set(entry) != {"path", "mtime_ns", "sha256"}:
        return False
    mtime = entry["mtime_ns"]
    return (
        isinstance(entry["path"], str)
        and isinstance(entry["sha256"], str)
        and isinstance(mtime, int)
        and not isinstance(mtime, bool)
    )


def _word_pair(key: object, value: object) -> bool:
    return isinstance(key, str) and isinstance(value, str)


def record_path(layout: Layout, role: str, product: int | None) -> Path:
    """`probes_dir/<role>.json`, or `<role>@<repository_id>.json`."""
    if role not in ROLES:
        raise ConfigError(f"role.{role}: unknown role")
    name = role if product is None else f"{role}@{product}"
    return layout.probes_dir / f"{name}.json"


def read_record(layout: Layout, role: str, product: int | None) -> ProbeRecord | None:
    """The role's last record; absent, unreadable or invalid is `None` (the
    status is then «не проверено», never current)."""
    try:
        fd = os.open(record_path(layout, role, product), _PEEK_FLAGS | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        data = os.read(fd, _READ_LIMIT + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    try:
        return ProbeRecord.from_json(data.decode("utf-8"))
    except (ConfigError, UnicodeDecodeError, TypeError):
        return None


# ---------- the probe ----------


def run_probe(
    role: str,
    product: int | None,
    cfg: ModelsConfig,
    install: InstallConfig,
    layout: Layout,
    *,
    runner: Runner = run_as,
) -> ProbeRecord | str:
    """Probe `role`'s choice: the record (written to `probes_dir`), or a
    string «не проверено: …» / «не поддерживается: …» when the harness was
    not launched at all.

    Order: the role's OS user exists; the choice; the harness is approved;
    the probe cwd is neutral; argv and env are formed (the profile is taken
    by SHA as data only); `admit` on exactly them; then the one launch with
    the same objects.
    """
    user = ROLE_CONTEXT[role]
    try:
        entry = _getpwnam(user)
    except KeyError:
        return f"{NOT_CHECKED}: роль {user} не создана (этап 2)"
    home, role_uid = Path(entry.pw_dir), entry.pw_uid
    try:
        choice, _ = resolve_choice(cfg, role, product)
    except ConfigError:
        return f"{NOT_CHECKED}: модель не задана"
    path = install.approved_cli.get(choice.harness)
    if path is None:
        return f"{UNSUPPORTED}: харнесс {choice.harness} не утверждён (approved_cli)"
    problems = probe_cwd_problems(layout)
    if problems:
        return f"{NOT_CHECKED}: " + "; ".join(problems)
    formed = _form(choice, path, home)
    if isinstance(formed, str):
        return formed
    argv, env, formed_sha = formed
    admitted = admission(choice.harness, path, detect_platform(), argv=argv, env=env)
    if isinstance(admitted, str):
        return admitted
    if admitted.executable_sha256 != formed_sha:  # argv came from another file
        return f"{UNSUPPORTED}: исполняемый файл изменился во время допуска"
    profile = admitted.profile
    managed_digest, problems = managed_context(profile)
    if managed_digest is None:  # changed since admission: still before launch
        return f"{UNSUPPORTED}: " + "; ".join(problems)
    result = runner(
        user, argv, env=env, cwd=layout.probe_cwd, network=True, timeout=PROBE_TIMEOUT
    )
    rec = _record(
        role,
        product,
        choice,
        profile,
        sha=admitted.executable_sha256,
        managed_digest=managed_digest,
        result=result,
        auth=observe_auth(profile, home, role_uid=role_uid),
    )
    fsutil.write_atomic(record_path(layout, role, product), rec.to_json(), 0o644)
    return rec


def _form(
    choice: ModelChoice, path: Path, home: Path
) -> tuple[list[str], dict[str, str], str] | str:
    """The argv, env and executable SHA of the probe, from the profile
    pinned to that SHA (data only: admission, which hashes again and must
    name the same SHA, comes after)."""
    found = executable_target(path)
    if isinstance(found, str):
        return found
    target, sha = found
    profile = profile_for(choice.harness, detect_platform(), sha)
    if isinstance(profile, str):
        return profile
    try:
        params = param_argv(choice)
        argv = profile.argv(target, choice)
    except ConfigError as exc:
        return f"{UNSUPPORTED}: {exc}"
    if not _contains(argv, params):
        return f"{UNSUPPORTED}: профиль не передаёт значимые параметры"
    env = {
        "HOME": str(home),
        "PATH": f"{target.parent}:/usr/bin:/bin",
        "LANG": "C.UTF-8",
    }
    return argv, env, sha


def _record(
    role: str,
    product: int | None,
    choice: ModelChoice,
    profile: HarnessProfile,
    *,
    sha: str,
    managed_digest: str,
    result: RunResult,
    auth: AuthObservation,
) -> ProbeRecord:
    outcome, actual, reason = _outcome(choice.harness, result)
    return ProbeRecord(
        role=role,
        product=product,
        time=_now().isoformat(timespec="seconds"),
        harness=choice.harness,
        harness_version=profile.version,
        adapter_version=ADAPTER_VERSION,
        isolation="verified",
        requested_model=choice.model,
        actual_model=actual,
        effort=choice.effort or "default",
        params=dict(choice.params),
        config_hash=config_hash(choice),
        executable_sha256=sha,
        managed_digest=managed_digest,
        auth_tracking=tracking_text(auth),
        auth_files=auth.files,
        outcome=outcome,
        reason=reason,
    )


def _outcome(
    harness: str, result: RunResult
) -> tuple[Literal["confirmed", "not_confirmed"], str | None, str | None]:
    """`(outcome, actual model, reason)`: a timeout, a network or any other
    failure is «доступ не подтверждён», never «модель запрещена»."""
    if result.timed_out:
        return "not_confirmed", None, f"{NO_ACCESS}: таймаут {PROBE_TIMEOUT} с"
    if not result.launched:
        return "not_confirmed", None, f"{NO_ACCESS}: харнесс не запущен"
    if result.stdout_truncated:
        return "not_confirmed", None, f"{NO_ACCESS}: ответ превысил лимит"
    parsed = parse_output(harness, result.stdout)
    if parsed.ok and result.returncode == 0:
        return "confirmed", parsed.actual_model, None
    detail = parsed.error if not parsed.ok else f"код {result.returncode}"
    return "not_confirmed", None, f"{NO_ACCESS}: {detail}"


# ---------- status ----------


@dataclass(frozen=True)
class ProbeStatus:
    """The state of a role's probe as `models list` and `doctor` show it."""

    text: str
    current: bool


def probe_status(
    rec: ProbeRecord | None,
    choice: ModelChoice,
    *,
    harness_version: str | None,
    auth: AuthObservation | None,
    executable_sha256: str | None,
    managed_digest: str | None,
) -> ProbeStatus:
    """The first matching rule of spec 3.6 over the record and the current
    facts (`None`: not observable without root)."""
    if rec is None:
        return ProbeStatus(NOT_CHECKED, False)
    if harness_version is None or auth is None:
        return ProbeStatus("данные недоступны: нужен sudo", False)
    now = {
        "config_hash": config_hash(choice),
        "adapter_version": ADAPTER_VERSION,
        "harness_version": harness_version,
        "executable_sha256": executable_sha256,
        "managed_digest": managed_digest,
    }
    for name, value in now.items():
        if getattr(rec, name) != value:
            return ProbeStatus(f"устарела: {name}", False)
    if rec.auth_tracking != tracking_text(auth):
        return ProbeStatus("устарела: auth", False)
    if auth.tracking == "tracked" and list(rec.auth_files) != list(auth.files):
        return ProbeStatus("устарела: auth", False)
    if rec.outcome == "not_confirmed":
        return ProbeStatus(f"не подтверждён: {rec.reason}", False)
    if auth.tracking != "tracked":
        return ProbeStatus(
            f"успешная проба {rec.time}; актуальность авторизации не установлена",
            False,
        )
    return ProbeStatus(f"подтверждён {rec.time}", True)


def current_status(
    layout: Layout,
    role: str,
    choice: ModelChoice,
    install: InstallConfig | None,
    *,
    root: bool,
    targets: dict[Path, tuple[Path, str] | str] | None = None,
) -> tuple[ProbeStatus, ProbeRecord | None]:
    """The status of `role`'s last record against the facts observable now.

    A non-root reader cannot observe the role's auth: with a record, rule 2
    («нужен sudo»). Root observes the executable, its profile, the managed
    context and the auth files; nothing is executed. `targets` caches
    `executable_target` per approved path across the roles of one command
    (each binary is hashed once).
    """
    rec = read_record(layout, role, None)
    if rec is None or not root:
        return probe_status(rec, choice, **_UNOBSERVED), rec
    user = ROLE_CONTEXT[role]
    try:
        entry = _getpwnam(user)
    except KeyError:
        return ProbeStatus(
            f"{NOT_CHECKED}: роль {user} не создана (этап 2)", False
        ), rec
    home, role_uid = Path(entry.pw_dir), entry.pw_uid
    path = None if install is None else install.approved_cli.get(choice.harness)
    found = UNSUPPORTED if path is None else _target_cached(path, targets)
    sha = None if isinstance(found, str) else found[1]
    profile = profile_for(choice.harness, detect_platform(), sha or "")
    if isinstance(profile, str):  # the version is unknown: stale, not «нужен sudo»
        return probe_status(
            rec,
            choice,
            harness_version=UNKNOWN_VERSION,
            auth=AuthObservation("unavailable", "backend_unknown", ()),
            executable_sha256=sha,
            managed_digest=None,
        ), rec
    return probe_status(
        rec,
        choice,
        harness_version=profile.version,
        auth=observe_auth(profile, home, role_uid=role_uid),
        executable_sha256=sha,
        managed_digest=managed_context(profile)[0],
    ), rec


def pin_ready(layout: Layout, role: str, pin: ModelPin) -> ProbeStatus:
    """Admission of a run's pin for `role`, without running a harness.

    Decision 9: no launcher isolation is confirmed in v1, so every pin is
    «не поддерживается: изоляция не подтверждена». The full admission
    (`executable_target`, the profile, `managed_context`, the role's auth
    fingerprint, then `probe_status` of the last record against those facts
    and the pin) arrives with the first profile whose isolation is verified;
    until then nothing is read, hashed or executed.
    """
    return ProbeStatus(f"{UNSUPPORTED}: изоляция не подтверждена", False)


def _target_cached(
    path: Path, cache: dict[Path, tuple[Path, str] | str] | None
) -> tuple[Path, str] | str:
    if cache is None:
        return executable_target(path)
    if path not in cache:
        cache[path] = executable_target(path)
    return cache[path]


_UNOBSERVED: Mapping[str, None] = MappingProxyType(
    {
        "harness_version": None,
        "auth": None,
        "executable_sha256": None,
        "managed_digest": None,
    }
)
