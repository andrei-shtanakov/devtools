"""Доверенный контекст решения: корень `df-trust`, периметр, эпохи (спека A rev 16 §6).

Перед КАЖДЫМ решением: привязка `/etc/df/trust.toml` → снимок `df-trust`
(SHA фиксируется; `source.env` и все версии `epochs.yaml` — из него) →
журнал событий периметра (обязателен) → самоотчёт каждого credential →
ruleset обоих репо доверия → `allow_rebase_merge`; затем лениво — время
якорей и диапазоны эпох с наборами ≤ T. Недоступное обязательное чтение —
`Unavailable` без события; событие — только для установленного нарушения.
`act_source.load_source()` (файл devtools) здесь не вызывается (§6.5).
Транспорт источников доверия строит сам `load_trust` (`history_transport`:
анонимный либо с read-only токеном истории, С-1, спека rev 18 §6.2); один
объект на решение читает снимок, журнал периметра, якоря и диапазоны.

Модуль в цепочке обвязки мержа (`act_premerge`): только `governance/act_*`,
`governance/facts`, `ssot_env`, stdlib. Журнал периметра — протокол
`Journal`; реализации (`act_journal`) тянут `df/` и сюда не импортируются.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable
from urllib.parse import quote

from governance import act_facts
from governance.act_anon import AnonTransport, contents_bytes
from governance.act_check import ActivationView
from governance.act_epochs import Epoch, JournalError, check_transition, parse_journal
from governance.act_perimeter import Violation, permissions_refusal
from governance.act_policy import (
    POLICY_FILES,
    PolicyError,
    PolicySet,
    mode_for,
    parse_policy_set,
)
from governance.act_source import SourceError, SourceV2, parse_source
from governance.act_time import Unavailable
from governance.facts import Fact, Outcome, unavailable

BINDING_PATH = Path("/etc/df/trust.toml")
BINDING_SCHEMA = "df/trust/v1"
SUPPORTED_HOST = "github.com"
JOURNAL_FILE = "epochs.yaml"
SOURCE_FILE = "source.env"
MAX_CHAIN = 500
MAX_BINDING = 64 << 10
STALE = "stale_snapshot"
REQUIRED_RULES = frozenset({"deletion", "non_fast_forward", "pull_request"})
REBASE_PREMISE = "allow_rebase_merge"
#: HTTP statuses of a commit object that no longer reads (`history_unverifiable`).
GONE = frozenset({404, 422})
#: Окружение `gh`, которое подменило бы credential или хост самоотчёта.
_GH_ENV_DROPPED = frozenset(
    {
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GH_HOST",
        "GH_ENTERPRISE_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
    }
)
#: Закреплённая версия `gh` установки df (спека B §3.10 rev 11); копия
#: `df.platform.GH_PINNED_VERSION` (пакеты не импортируют друг друга, тест держит
#: их равными).
GH_PINNED_VERSION = "2.102.1"
#: Всё окружение запуска `gh` по договору df (спека B §3.10 rev 11) — разрешительный
#: список; копия `df.platform.GH_ENV_KEYS`.
GH_ENV_KEYS = ("PATH", "HOME", "LANG", "GH_CONFIG_DIR", "GH_NO_UPDATE_NOTIFIER")
_BINDING_KEYS = (
    "host",
    "df_trust_repository_id",
    "df_trust_full_name",
    "df_trust_branch",
    "approval_policy_repository_id",
    "transport",
)
HISTORY_KEY = "history_gh_config_dir"
HISTORY_LABEL = "profile:history"
HISTORY_NOT_WIRED = "самоотчёт: credential не из закрытого набора credentials()"
HISTORY_PROFILE_SHARED = "самоотчёт: профиль credential совпадает с профилем истории"
_TOKEN_FORM = re.compile(r"[A-Za-z0-9_]+")
#: Закрытый набор форм `credentials()`: метка → (есть gh_config_dir, есть token).
_CRED_SHAPES = {
    "profile:agent": (True, False),
    "profile:merge": (True, False),
    "env:GH_TOKEN": (False, True),
    "env:GITHUB_TOKEN": (False, True),
}
Span = tuple[ActivationView, ...]
#: Trust repo role; the same literal as `act_trust_types.Role` (a test keeps them
#: equal). Kept out of `act_trust`'s imports until Task 7 decides the closure
#: (ruling 6).
Role = Literal["approval-policy", "df-trust"]


class TrustError(ValueError):
    """Привязка корня доверия не читается — отказ конфигурации."""


@dataclass(frozen=True)
class Binding:
    """Защищённая привязка `/etc/df/trust.toml` (спека B §3.7; Р-6; С-1 — `HISTORY_KEY`)."""

    host: str
    df_trust_repository_id: int
    df_trust_full_name: str
    df_trust_branch: str
    approval_policy_repository_id: int
    transport: str
    history_gh_config_dir: Path | None = None


@dataclass(frozen=True)
class TrustRoot:
    """Идентичность и снимок корня доверия — в evidence каждого решения."""

    host: str
    repository_id: int
    branch: str
    snapshot_sha: str

    @property
    def id_tuple(self) -> tuple[str, int, str]:
        """Пространство журнала событий периметра (спека §6.3)."""
        return (self.host, self.repository_id, self.branch)


@dataclass(frozen=True)
class TrustSnapshot:
    """`source.env` и журнал эпох из ОДНОГО зафиксированного снимка;
    `transport` — транспорт решения, которым снимок прочитан (Р-23)."""

    root: TrustRoot
    full_name: str
    source: SourceV2
    epochs: tuple[Epoch, ...]
    transport: Any = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class Trust:
    """Доверенный контекст решения; `unverified` — непроверенные предпосылки;
    `policy_head` — закреплённая издателем голова P `approval-policy` (спека B
    §6.1, К): история открытой эпохи читается до неё по SHA; `None` — до
    текущей головы ветки (поведение 2b)."""

    snapshot: TrustSnapshot
    unverified: tuple[str, ...]
    policy_head: str | None = None

    @property
    def source(self) -> SourceV2:
        return self.snapshot.source

    @property
    def epochs(self) -> tuple[Epoch, ...]:
        return self.snapshot.epochs

    @property
    def root(self) -> TrustRoot:
        return self.snapshot.root

    @property
    def transport(self) -> Any:
        """Транспорт источников доверия решения — единственный (R7)."""
        return self.snapshot.transport


@dataclass(frozen=True)
class HistoryFault:
    """A reproducible defect of the pinned `df-trust` history: finding `kind`
    (role `df-trust`, spec §6.1) and its detail. `read_trust_snapshot` reports it
    as `Unavailable(detail)`; the pair reader (`act_trust_pair`) as a finding."""

    kind: str
    detail: str


@dataclass(frozen=True)
class Credential:
    """Credential, который процесс ДЕРЖИТ: профиль `gh` либо токен окружения."""

    label: str
    gh_config_dir: Path | None = None
    token: str | None = field(default=None, repr=False)


class Journal(Protocol):
    """Журнал событий периметра установки (реализации — `act_journal`)."""

    def status(self, snap: TrustSnapshot) -> str | Unavailable:
        """`clear` | `blocked` | `STALE` (снимок сдвинулся) | недоступно."""
        ...

    def record(
        self, snap: TrustSnapshot, source: str, detail: str
    ) -> Unavailable | None:
        """Зарегистрировать нарушение; номер эпохи назначает писатель."""
        ...


@runtime_checkable
class FindingSink(Protocol):
    """Journal that keeps the finding kind (the `op: merge` adapter of the precheck
    program v2, spec B §5.4): `_register` hands it the kind of the check that found
    the violation instead of `Journal.record`."""

    def record_finding(
        self, snap: TrustSnapshot, kind: str, role: str, detail: str
    ) -> Unavailable | None:
        """Keep one finding; the publisher registers it."""
        ...


@dataclass(frozen=True)
class KeyHolderCovered:
    """Credential mode of a process that holds only a product token derived from
    the App key, covered by the key holder's self-report of the App installations
    in the same operation (spec B §6.1, N3): no credential of its own is
    self-reported. An explicit mode, not a fictitious credential."""


COVERED_BY_KEY_HOLDER = KeyHolderCovered()
SnapshotReader = Callable[[Any, Binding], TrustSnapshot | Unavailable]


@dataclass(frozen=True)
class Readers:
    """Самоотчёт переданных credentials (`gh api` под каждым). Транспорта источников
    доверия здесь нет: его строит `load_trust` (Task 11c, C6)."""

    as_credential: Callable[[Credential, str], Fact[object]]


def default_readers() -> Readers:
    """Боевой самоотчёт: `gh api` под credential."""
    return Readers(gh_get)


# --- привязка -------------------------------------------------------------------


def parse_binding(text: str) -> Binding:
    """Строгий разбор `trust.toml` (те же ключи, что `df.stage2_config`)."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise TrustError(f"trust.toml не TOML: {exc}") from exc
    if data.get("schema") != BINDING_SCHEMA:
        raise TrustError(f"trust.toml: схема не {BINDING_SCHEMA}")
    keys = set(data) - {"schema"}
    if not set(_BINDING_KEYS) <= keys <= set(_BINDING_KEYS) | {HISTORY_KEY}:
        raise TrustError(
            f"trust.toml: ключи {sorted(data)}, нужны {list(_BINDING_KEYS)}"
        )
    ids = ("df_trust_repository_id", "approval_policy_repository_id")
    if any(type(data[k]) is not int or data[k] <= 0 for k in ids):
        raise TrustError("trust.toml: repository_id — положительные целые")
    texts = ("host", "df_trust_full_name", "df_trust_branch")
    if any(not isinstance(data[k], str) or not data[k] for k in texts):
        raise TrustError("trust.toml: host/имя/ветка — непустые строки")
    if data["transport"] != "public":
        raise TrustError('trust.toml: v1 поддерживает только transport = "public"')
    history = data.get(HISTORY_KEY)
    if history is not None and (
        not isinstance(history, str) or not Path(history).is_absolute()
    ):
        raise TrustError(f"trust.toml: {HISTORY_KEY} — абсолютный путь")
    return Binding(
        **{k: data[k] for k in _BINDING_KEYS},
        history_gh_config_dir=None if history is None else Path(history),
    )


def load_binding(
    path: Path | None = None, *, owner_uid: int = 0
) -> Binding | None | Unavailable:
    """Привязка: обычный файл, без ссылок, владелец `owner_uid`, не пишется
    группой/всеми. Файла нет — `None` (режим продукта не установлен, не
    legacy — ревизия b1); иное — `Unavailable`.

    `path=None` — `BINDING_PATH`, прочитанный в момент вызова (тесты подменяют
    атрибут модуля, чтобы машина разработчика с настоящей привязкой не
    уводила их в сеть)."""
    path = BINDING_PATH if path is None else path
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return Unavailable(f"привязка {path}: {exc}")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != owner_uid or st.st_mode & 0o022:
            return Unavailable(f"привязка {path}: не файл root без записи группы/всех")
        raw = os.read(fd, MAX_BINDING + 1)
    except OSError as exc:
        return Unavailable(f"привязка {path}: {exc}")
    finally:
        os.close(fd)
    if len(raw) > MAX_BINDING:
        return Unavailable(f"привязка {path}: больше {MAX_BINDING} байт")
    try:
        return parse_binding(raw.decode("utf-8"))
    except (TrustError, UnicodeDecodeError) as exc:
        return Unavailable(f"привязка {path}: {exc}")


# --- снимок df-trust --------------------------------------------------------------


def _get(anon: Any, path: str) -> dict | list | Unavailable:
    from steward.approvalfacts.v3 import Request

    resp = anon.send(Request("GET", path))
    if resp.status != 200 or not isinstance(resp.body, (dict, list)):
        return Unavailable(f"{path}: HTTP {resp.status} {resp.error or ''}".rstrip())
    return resp.body


def _repo_path(full_name: str) -> str:
    owner, _, name = full_name.partition("/")
    return f"repos/{quote(owner, safe='')}/{quote(name, safe='')}"


def _named(anon: Any, repo_id: int, expected: str) -> str | Unavailable:
    body = _get(anon, f"repositories/{repo_id}")
    if isinstance(body, Unavailable):
        return body
    if not isinstance(body, dict) or body.get("full_name") != expected:
        got = body.get("full_name") if isinstance(body, dict) else body
        return Unavailable(f"repositories/{repo_id}: {got!r}, привязка — {expected!r}")
    if body.get("private") is not False:
        return Unavailable(f"{expected}: v1 читает только публичные источники доверия")
    return expected


def _commit(anon: Any, repo: str, sha: str) -> dict | Unavailable | HistoryFault:
    """`GET commits/{sha}`: HTTP 404/422 — the object no longer reads
    (`history_unverifiable`, spec §6.1); any other failure is temporary.

    Accepted risk (review of Task 5, M-3): a read replica that has not yet seen
    a freshly pushed head may answer 404/422 for seconds; one such answer is
    taken as "gone" without confirmation and blocks until the next epoch."""
    from steward.approvalfacts.v3 import Request

    path = f"{_repo_path(repo)}/commits/{sha}"
    resp = anon.send(Request("GET", path))
    if resp.status in GONE:
        return HistoryFault(
            "history_unverifiable",
            f"{repo}@{sha[:12]}: коммит не читается (HTTP {resp.status})",
        )
    if resp.status != 200 or not isinstance(resp.body, dict):
        return Unavailable(f"{path}: HTTP {resp.status} {resp.error or ''}".rstrip())
    return resp.body


def _chain(anon: Any, repo: str, head: str) -> list[str] | Unavailable | HistoryFault:
    """First-parent от `head` до корня; звенья от корня к голове."""
    walked = _walk(anon, repo, head)
    return walked if not isinstance(walked, tuple) else walked[0]


def _walk(
    anon: Any, repo: str, head: str, stop: str | None = None, *, room: int | None = None
) -> tuple[list[str], str | None] | Unavailable | HistoryFault:
    """First-parent walk from `head`, links root-to-head, and the first parent of
    the last link read (`None` at the root).

    With `stop` the walk ends on it (inclusive): the first link is `stop` iff
    `head` is `stop` or its first-parent descendant; the walk can go on from the
    returned parent. `room` — links still allowed (default `MAX_CHAIN`)."""
    limit = MAX_CHAIN if room is None else room
    chain: list[str] = []
    sha: str | None = head
    while sha is not None:
        if len(chain) == limit:
            return Unavailable(f"{repo}: цепочка длиннее {MAX_CHAIN}")
        body = _commit(anon, repo, sha)
        if not isinstance(body, dict):
            return body
        parents = body.get("parents")
        if body.get("sha") != sha or not isinstance(parents, list):
            return Unavailable(f"{repo}@{sha[:12]}: ответ не о том коммите")
        chain.append(sha)
        first = (
            parents[0].get("sha") if parents and isinstance(parents[0], dict) else None
        )
        sha = first if isinstance(first, str) else None
        if chain[-1] == stop:
            break
    return chain[::-1], sha


def _journal_versions(
    anon: Any,
    repo: str,
    chain: Sequence[str],
    start: tuple[bytes, tuple[Epoch, ...]] | None = None,
) -> list[tuple[Epoch, ...]] | Unavailable | HistoryFault:
    """Versions of the journal along `chain`, a change at a time. `start` — the
    bytes and parse of an already read version before `chain` (the journal on A,
    incremental path): it is the first version and is not read again."""
    versions: list[tuple[Epoch, ...]] = [] if start is None else [start[1]]
    last: bytes | None = None if start is None else start[0]
    for sha in chain:
        raw = contents_bytes(anon, repo, JOURNAL_FILE, sha)
        if isinstance(raw, Unavailable):
            return raw
        if raw is None:
            if versions:
                return HistoryFault(
                    "journal_transition", f"{repo}@{sha[:12]}: {JOURNAL_FILE} удалён"
                )
            continue
        if raw == last:
            continue
        try:
            versions.append(parse_journal(raw.decode("utf-8")))
        except (JournalError, UnicodeDecodeError) as exc:
            return HistoryFault("journal_parse", f"{repo}@{sha[:12]}: {exc}")
        last = raw
    return versions


def _checked_epochs(
    versions: list[tuple[Epoch, ...]], source: SourceV2, *, bootstrap: bool = True
) -> tuple[Epoch, ...] | HistoryFault:
    """Forward journal transitions. `bootstrap=False`: the first version is the
    accepted one (incremental path from A, spec §6.1) and is not checked against
    `BOOTSTRAP_ACTIVATION`."""
    if not versions:
        return HistoryFault("journal_parse", f"в снимке нет {JOURNAL_FILE}")
    first = versions[0]
    if bootstrap and (
        len(first) != 1
        or first[0].closed
        or first[0].anchor != source.bootstrap_activation
    ):
        return HistoryFault(
            "bootstrap_mismatch",
            "первая версия журнала — не bootstrap с BOOTSTRAP_ACTIVATION",
        )
    try:
        for prev, new in pairwise(versions):
            check_transition(prev, new)
    except JournalError as exc:
        return HistoryFault(
            "journal_transition", f"недопустимый переход журнала: {exc}"
        )
    return versions[-1]


def _source_at(
    anon: Any, repo: str, head: str
) -> tuple[bytes, SourceV2] | Unavailable | HistoryFault:
    """`source.env` bytes and parse at commit `head` (by SHA). The policy source
    identity is the caller's: `_named` here, `not_public`-aware in the pair."""
    raw = contents_bytes(anon, repo, SOURCE_FILE, head)
    if isinstance(raw, Unavailable):
        return raw
    if raw is None:
        return HistoryFault(
            "source_env_invalid", f"{repo}@{head[:12]}: нет {SOURCE_FILE}"
        )
    try:
        source = parse_source(raw.decode("utf-8"))
    except (SourceError, UnicodeDecodeError) as exc:
        return HistoryFault("source_env_invalid", f"{repo}@{head[:12]}: {exc}")
    return raw, source


def _refusal(got: Unavailable | HistoryFault) -> Unavailable:
    """A history fault, for the branch reader, is a refusal with its detail."""
    return got if isinstance(got, Unavailable) else Unavailable(got.detail)


def read_trust_snapshot(anon: Any, binding: Binding) -> TrustSnapshot | Unavailable:
    """Снимок закреплённой ветки `df-trust` (§6.1 п. 1): всё — из одного SHA."""
    if binding.host != SUPPORTED_HOST:
        return Unavailable(f"привязка: хост {binding.host!r} не поддерживается v1")
    repo = _named(anon, binding.df_trust_repository_id, binding.df_trust_full_name)
    if isinstance(repo, Unavailable):
        return repo
    branch = quote(binding.df_trust_branch, safe="")
    body = _get(anon, f"{_repo_path(repo)}/branches/{branch}")
    if isinstance(body, Unavailable):
        return body
    commit = body.get("commit") if isinstance(body, dict) else None
    head = commit.get("sha") if isinstance(commit, dict) else None
    if not isinstance(head, str) or len(head) != 40:
        return Unavailable(f"{repo}: голова {binding.df_trust_branch} не прочитана")
    found = _source_at(anon, repo, head)
    if not isinstance(found, tuple):
        return _refusal(found)
    source = found[1]
    policy = _named(anon, binding.approval_policy_repository_id, source.repo)
    if isinstance(policy, Unavailable):
        return policy
    chain = _chain(anon, repo, head)
    if not isinstance(chain, list):
        return _refusal(chain)
    versions = _journal_versions(anon, repo, chain)
    if not isinstance(versions, list):
        return _refusal(versions)
    epochs = _checked_epochs(versions, source)
    if isinstance(epochs, HistoryFault):
        return _refusal(epochs)
    root = TrustRoot(
        binding.host, binding.df_trust_repository_id, binding.df_trust_branch, head
    )
    return TrustSnapshot(root, repo, source, epochs, transport=anon)


# --- транспорт источников доверия (С-1, спека rev 18 §6.2) ---------------------------


def history_transport(
    binding: Binding,
    *,
    open_url: Callable[..., Any] | None = None,
    token_source: Callable[[Path], str | Unavailable] | None = None,
) -> AnonTransport | Unavailable:
    """Единственное место построения транспорта источников доверия (С-1, C6):
    ключа нет — анонимный; ключ `D` — токен `(token_source or history_token)(D)`
    ровно один раз; сбой — `Unavailable(f"токен истории: {причина}")` без
    анонимного отката (R5)."""
    config_dir = binding.history_gh_config_dir
    if config_dir is None:
        return AnonTransport(open_url=open_url)
    token = (token_source or history_token)(config_dir)
    if isinstance(token, Unavailable):
        return Unavailable(f"токен истории: {token.reason}")
    if not isinstance(token, str) or not _TOKEN_FORM.fullmatch(token):
        return Unavailable("токен истории: значение не в форме токена")
    return AnonTransport(open_url=open_url, token=token)


def history_token(config_dir: Path) -> str | Unavailable:
    """Токен профиля `history` для развёртывания флота (вне договора df):
    `gh` из `PATH`, окружение процесса без `_GH_ENV_DROPPED`; дальше —
    `_auth_token`."""
    env = {k: v for k, v in os.environ.items() if k not in _GH_ENV_DROPPED}
    env["GH_CONFIG_DIR"] = str(config_dir)
    return _auth_token(config_dir, "gh", env)


def history_token_at(
    config_dir: Path, *, gh: Path, role_home: Path
) -> str | Unavailable:
    """Токен профиля `history` по договору запуска df (спека B §3.10 rev 11): `gh`
    только по абсолютному пути `gh` (без отката к `PATH`), окружение — ровно
    `GH_ENV_KEYS` (`GH_TOKEN`/`GITHUB_TOKEN` процесса до `gh` не доходят), `HOME` —
    новый каталог вызова в `role_home`, удаляемый после вызова. df передаёт её
    в `history_transport` как `partial(history_token_at, gh=..., role_home=...)`.
    Причины — в формате `_auth_token`."""
    where = f"gh auth token ({HISTORY_LABEL})"
    if not gh.is_absolute():
        return Unavailable(f"{where}: путь gh не абсолютный")
    try:
        call = tempfile.mkdtemp(prefix="gh-call-", dir=role_home)
    except OSError as exc:
        return Unavailable(f"{where}: каталог вызова: {type(exc).__name__}")
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": call,
        "LANG": "C",
        "GH_CONFIG_DIR": str(config_dir),
        "GH_NO_UPDATE_NOTIFIER": "1",
    }
    try:
        return _auth_token(config_dir, str(gh), env)
    finally:
        shutil.rmtree(call, ignore_errors=True)


def _auth_token(config_dir: Path, gh: str, env: dict[str, str]) -> str | Unavailable:
    """`<gh> auth token --hostname github.com` в окружении `env`, `timeout=60`.
    `<config_dir>/hosts.yml` не файл — `Unavailable` ДО запуска `gh` (Р-20).

    Безопасный формат причин (C1-02): только фиксированные строки
    `f"gh auth token ({HISTORY_LABEL}): " + <хвост>`, где хвост — `код выхода
    {rc}` | `пустой вывод` | `вывод не в форме токена` | `{type(exc).__name__}`
    (`OSError`, `TimeoutExpired`). stdout, stderr и `str(exc)` в причину не
    попадают никогда — ни целиком, ни срезом."""
    if not (config_dir / "hosts.yml").is_file():
        return Unavailable(f"профиль токена истории {config_dir}: нет hosts.yml")
    where = f"gh auth token ({HISTORY_LABEL})"
    try:
        done = subprocess.run(
            [gh, "auth", "token", "--hostname", SUPPORTED_HOST],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Unavailable(f"{where}: {type(exc).__name__}")
    if done.returncode != 0:
        return Unavailable(f"{where}: код выхода {done.returncode}")
    token = done.stdout.strip()
    if not token:
        return Unavailable(f"{where}: пустой вывод")
    if not _TOKEN_FORM.fullmatch(token):
        return Unavailable(f"{where}: вывод не в форме токена")
    return token


def transport_fact(transport: Any, cred: Credential, path: str) -> Fact[object]:
    """`GET <path>` транспортом решения; не 200 или не объект — `unavailable(
    f"GET {path} ({cred.label}): HTTP {status} {error}")`; тело — как есть."""
    from steward.approvalfacts.v3 import Request

    resp = transport.send(Request("GET", path))
    if resp.status != 200 or not isinstance(resp.body, dict):
        why = f"HTTP {resp.status} {resp.error or ''}".rstrip()
        return unavailable(f"GET {path} ({cred.label}): {why}")
    return Fact(Outcome.FOUND, resp.body, "ok")


# --- периметр -----------------------------------------------------------------------


def gh_get(cred: Credential, path: str, *, gh: str = "gh") -> Fact[object]:
    """`gh api <path>` ИМЕННО этим credential на `github.com`: токены и хост
    окружения снимаются, чтобы профиль не подменился чужим токеном, а
    самоотчёт не ушёл на другой хост. `profile:history` сюда не ходит (Р-18):
    его токен не уходит в окружение `gh`. `gh` — исполняемый файл: флот берёт
    его из `PATH`, программа проверяющего df — утверждённый абсолютный путь
    (`partial(gh_get, gh=...)`, договор запуска, спека B §3.10 rev 11)."""
    if cred.label == HISTORY_LABEL:
        return unavailable(
            f"gh api {path} ({HISTORY_LABEL}): самоотчёт токена истории — "
            "только через транспорт истории"
        )
    env = {k: v for k, v in os.environ.items() if k not in _GH_ENV_DROPPED}
    if cred.token is not None:
        env["GH_TOKEN"] = cred.token
    if cred.gh_config_dir is not None:
        env["GH_CONFIG_DIR"] = str(cred.gh_config_dir)
    try:
        done = subprocess.run(
            [gh, "api", "--hostname", SUPPORTED_HOST, path],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return unavailable(f"gh api {path} ({cred.label}): {exc}")
    if done.returncode != 0:
        return unavailable(f"gh api {path} ({cred.label}): {done.stderr.strip()[:200]}")
    try:
        return Fact(Outcome.FOUND, json.loads(done.stdout), "ok")
    except ValueError:
        return unavailable(f"gh api {path} ({cred.label}): ответ не JSON")


def credentials(
    env: Mapping[str, str], *, agent_dir: Path, merge_dir: Path | None = None
) -> tuple[Credential, ...]:
    """Что процесс держит (не что объявлено): профиль агента, профиль мержа,
    `GH_TOKEN`, `GITHUB_TOKEN`; совпадающие — один раз."""
    found = [Credential("profile:agent", gh_config_dir=agent_dir)]
    if merge_dir is not None and merge_dir.resolve() != agent_dir.resolve():
        found.append(Credential("profile:merge", gh_config_dir=merge_dir))
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        token = env.get(var)
        if token and all(c.token != token for c in found):
            found.append(Credential(f"env:{var}", token=token))
    return tuple(found)


def _roles(snap: TrustSnapshot) -> tuple[tuple[Role, str], ...]:
    return (("approval-policy", snap.source.repo), ("df-trust", snap.full_name))


Found = tuple[Role, str] | Unavailable | None
"""Нарушение `(роль репо, деталь)`, недоступность или чисто."""


def _creds_refusal(creds: Sequence[Credential]) -> Unavailable | None:
    """Каждый credential — форма из `_CRED_SHAPES`, иначе `HISTORY_NOT_WIRED`
    (до ввода-вывода: `profile:history` создаёт только `load_trust`)."""
    for cred in creds:
        shape = (cred.gh_config_dir is not None, cred.token is not None)
        if _CRED_SHAPES.get(cred.label) != shape:
            return Unavailable(HISTORY_NOT_WIRED)
    return None


def _shared_profile_refusal(
    creds: Sequence[Credential], binding: Binding
) -> Unavailable | None:
    """Профиль истории отдельный от профилей агента и мержа (§6.2): credential с
    тем же каталогом `gh` — `HISTORY_PROFILE_SHARED` до ввода-вывода (ревью 11c)."""
    history = binding.history_gh_config_dir
    if history is None:
        return None
    own = history.resolve()
    for cred in creds:
        if cred.gh_config_dir is not None and cred.gh_config_dir.resolve() == own:
            return Unavailable(HISTORY_PROFILE_SHARED)
    return None


def _self_report(
    readers: Readers, creds: Sequence[Credential], snap: TrustSnapshot
) -> Found:
    return _report(readers.as_credential, creds, snap)


def _history_self_report(transport: Any, snap: TrustSnapshot) -> Found:
    """Самоотчёт `profile:history` объектом транспорта решения: `GET repos/{repo}`
    обоих репо доверия через `transport_fact`, вердикт — `permissions_refusal`
    (Р-18; `readers.as_credential` не вызывается)."""
    history = Credential(HISTORY_LABEL)
    return _report(
        lambda cred, path: transport_fact(transport, cred, path), (history,), snap
    )


def _history_report_of(transport: Any, snap: TrustSnapshot, role: Role) -> Found:
    """`_history_self_report` for the repo of one role (`op: trust`, spec §6.1:
    one `token_write` per role)."""
    return _report_of(
        lambda cred, path: transport_fact(transport, cred, path),
        Credential(HISTORY_LABEL),
        role,
        dict(_roles(snap))[role],
    )


def _report(
    read: Callable[[Credential, str], Fact[object]],
    creds: Sequence[Credential],
    snap: TrustSnapshot,
) -> Found:
    """First violation over credentials × roles; else the first gap."""
    gap: Unavailable | None = None
    for cred in creds:
        for role, repo in _roles(snap):
            found = _report_of(read, cred, role, repo)
            if isinstance(found, tuple):
                return found
            if isinstance(found, Unavailable) and gap is None:
                gap = found
    return gap


def _report_of(
    read: Callable[[Credential, str], Fact[object]],
    cred: Credential,
    role: Role,
    repo: str,
) -> Found:
    """Self-report of one credential on one trust repo."""
    fact = read(cred, _repo_path(repo))
    body = fact.value if fact.outcome is Outcome.FOUND else None
    perms = body.get("permissions") if isinstance(body, dict) else None
    verdict = permissions_refusal(perms if isinstance(perms, dict) else None)
    if isinstance(verdict, Violation):
        return role, f"{cred.label} на {repo}: {verdict.detail}"
    if isinstance(verdict, Unavailable):
        why = verdict.reason if fact.outcome is Outcome.FOUND else fact.detail
        return Unavailable(f"самоотчёт {cred.label} на {repo}: {why}")
    return None


def _rulesets(anon: Any, snap: TrustSnapshot) -> Found:
    """First violation or failure over both trust repos (`load_trust`)."""
    for role, _repo in _roles(snap):
        found = _ruleset_of(anon, snap, role)
        if found is not None:
            return found
    return None


def _ruleset_of(anon: Any, snap: TrustSnapshot, role: Role) -> Found:
    """Required branch rules on the pinned branch of the repo of `role`."""
    repo = dict(_roles(snap))[role]
    pinned = snap.source.branch if role == "approval-policy" else snap.root.branch
    body = _get(anon, f"{_repo_path(repo)}/rules/branches/{quote(pinned, safe='')}")
    if isinstance(body, Unavailable):
        return body
    if not isinstance(body, list):
        return Unavailable(f"{repo}: правила ветки — не список")
    kinds = {r.get("type") for r in body if isinstance(r, dict)}
    if missing := sorted(REQUIRED_RULES - kinds):
        return role, f"{repo}@{pinned}: нет правил {missing}"
    return None


def _rebase(
    anon: Any, snap: TrustSnapshot, role: Role = "approval-policy"
) -> Found | str:
    """Четыре состояния (решения владельца 13.10, п. 1).

    `role` picks the repo: `load_trust` checks `approval-policy`; `op: trust`
    checks `df-trust` too (spec §6.1).

    `REBASE_PREMISE` — значение скрыто: предпосылка не проверена."""
    repo = dict(_roles(snap))[role]
    body = _get(anon, _repo_path(repo))
    if isinstance(body, Unavailable):
        return body
    if not isinstance(body, dict):
        return Unavailable(f"{repo}: ответ не объект")
    value = body.get("allow_rebase_merge")
    if value is None:
        return REBASE_PREMISE
    if value is True:
        return role, f"{repo}: allow_rebase_merge = true"
    if value is False:
        return None
    return Unavailable(f"{repo}: allow_rebase_merge = {value!r}")


def _register(
    journal: Journal, snap: TrustSnapshot, found: tuple[str, str], kind: str
) -> Unavailable:
    """Обнаруженное нарушение — событие журнала периметра (§6.3), затем отказ.
    `kind` — вид находки проверки (спека B §6.1); его получает только журнал,
    который его хранит (`FindingSink`)."""
    role, detail = found
    if isinstance(journal, FindingSink):
        failed = journal.record_finding(snap, kind, role, detail)
    else:
        failed = journal.record(snap, role, detail)
    if failed is not None:
        return Unavailable(
            f"нарушение периметра ({detail}); регистрация: {failed.reason}"
        )
    return Unavailable(f"нарушение периметра: {detail}")


def _snapshot_for_decision(
    transport: Any,
    binding: Binding,
    journal: Journal,
    read: SnapshotReader | None = None,
) -> TrustSnapshot | Unavailable:
    for _ in range(3):
        snap = (read or read_trust_snapshot)(transport, binding)
        if isinstance(snap, Unavailable):
            return snap
        state = journal.status(snap)
        if state == STALE:
            continue
        if isinstance(state, Unavailable):
            return Unavailable(f"журнал периметра: {state.reason}")
        if state != "clear":
            return Unavailable(
                f"корень {snap.root.id_tuple}: периметр заблокирован (§6.3)"
            )
        return snap
    return Unavailable("снимок df-trust сдвигается быстрее проверки периметра")


def _self_reports(
    readers: Readers, creds: Sequence[Credential], snap: TrustSnapshot, history: Any
) -> Found:
    """Переданные credentials, затем (при ключе) `profile:history` транспортом
    решения; нарушение любого побеждает недоступность другого (как в 12a)."""
    found = _self_report(readers, creds, snap)
    if isinstance(found, tuple) or history is None:
        return found
    own = _history_self_report(history, snap)
    return own if isinstance(own, tuple) or found is None else found


def load_trust(
    readers: Readers,
    binding: Binding,
    journal: Journal,
    *,
    creds: Sequence[Credential] | KeyHolderCovered,
    open_url: Callable[..., Any] | None = None,
    token_source: Callable[[Path], str | Unavailable] | None = None,
    snapshot: SnapshotReader | None = None,
    policy_head: str | None = None,
) -> Trust | Unavailable:
    """Обязательные проверки перед решением (п. 1–5 Task 12a), с ранним выходом,
    на транспорте, который строит сам (Task 11c, C6): снаружи внедряемы только
    швы ввода-вывода `open_url` и `token_source`; токен читается раз на решение,
    тот же объект транспорта читает снимок, журнал периметра и повторы `STALE`.

    Журнал периметра обязателен у КАЖДОГО потребителя, включая предпроверку
    мержа (ревизия b1: `act_journal_wire`): блок, зарегистрированный другим
    потребителем, учитывается, а обнаруженное здесь нарушение сохраняется.

    `creds=COVERED_BY_KEY_HOLDER` — явный режим производного credential (N3):
    своих credentials процесс не самоотчитывает, токен истории (при ключе) —
    как всегда. `snapshot` — читатель снимка тем же транспортом решения
    (проверяющий v2: пара по SHA, `act_trust_pair`); по умолчанию — голова
    ветки. `policy_head` — закреплённая P (`Trust.policy_head`)."""
    if isinstance(creds, KeyHolderCovered):
        held: Sequence[Credential] = ()
    elif not creds:
        return Unavailable("самоотчёт: процесс не назвал ни одного credential")
    else:
        held = creds
    refused = _creds_refusal(held) or _shared_profile_refusal(held, binding)
    if refused is not None:
        return refused
    transport = history_transport(binding, open_url=open_url, token_source=token_source)
    if isinstance(transport, Unavailable):
        return transport
    snap = _snapshot_for_decision(transport, binding, journal, snapshot)
    if isinstance(snap, Unavailable):
        return snap
    keyed = binding.history_gh_config_dir is not None
    unverified: tuple[str, ...] = ()
    for kind, check in (
        (
            "token_write",
            lambda: _self_reports(readers, held, snap, transport if keyed else None),
        ),
        ("ruleset_missing", lambda: _rulesets(transport, snap)),
        ("rebase_allowed", lambda: _rebase(transport, snap)),
    ):
        found = check()
        if found == REBASE_PREMISE:
            unverified = (REBASE_PREMISE,)
        elif isinstance(found, Unavailable):
            return found
        elif isinstance(found, tuple):
            return _register(journal, snap, found, kind)
    return Trust(snap, unverified, policy_head)


# --- якоря, диапазоны, наборы --------------------------------------------------------


def load_anchor_times(trust: Trust, t: datetime) -> dict[str, datetime | Unavailable]:
    """Время якоря КАЖДОЙ эпохи (`merged_at` активации, №19).

    Не лениво (ревизия b1, B1-10): admission при `merge`/без записи на T
    требует истории режима всех прежних эпох (`act_check._mode_history`), а её
    диапазоны читаются от якорей. e7 сохраняется иначе: недоступное время
    старого якоря записывается `Unavailable` и мешает лишь решению, которое от
    него зависит (`_epoch_at` идёт от новой эпохи назад и до старого якоря не
    доходит, если новый уже предшествует T). `t` — для симметрии с
    `load_ranges` и будущего предела обхода; на чтение не влияет. Читает
    транспортом решения `trust.transport` (R7).
    """
    del t
    times: dict[str, datetime | Unavailable] = {}
    for epoch in reversed(trust.epochs):
        got = act_facts.activation(trust.transport, trust.source, epoch.anchor)
        times[epoch.anchor] = got if isinstance(got, Unavailable) else got.merged_at
    return times


def policy_set(
    anon: Any, source: SourceV2, activation: act_facts.ActivationFact
) -> PolicySet | Unavailable:
    """Набор активации: байты из её `merge_commit`, сверенные с `files_sha256`."""
    files: dict[str, bytes] = {}
    for name in POLICY_FILES:
        digest = activation.files_sha256.get(name)
        if digest is None:
            return Unavailable(f"активация {activation.merge_commit[:12]}: нет {name}")
        raw = contents_bytes(anon, source.repo, name, activation.merge_commit)
        if not isinstance(raw, bytes):
            return raw or Unavailable(
                f"{name} в {activation.merge_commit[:12]} не найден"
            )
        if hashlib.sha256(raw).hexdigest() != digest:
            return Unavailable(f"смешение версий: {name} ≠ files_sha256 активации")
        files[name] = raw
    try:
        return parse_policy_set(activation.merge_commit, files)
    except PolicyError as exc:
        return Unavailable(f"набор {activation.merge_commit[:12]}: {exc}")


def load_ranges(
    trust: Trust,
    anchor_at: Mapping[str, datetime | Unavailable],
    t: datetime,
    journal: Journal,
) -> (
    tuple[dict[int, Span | Unavailable], dict[str, PolicySet | Unavailable]]
    | Unavailable
):
    """Диапазоны эпох с якорем ≤ t и наборы активаций ≤ t (§6.4).

    Нарушение формы истории в диапазоне ЛЮБОЙ эпохи — событие периметра и
    отказ (§6.3; ревизия b1 сняла исключение для закрытых эпох). Недочитанный
    диапазон — значение `Unavailable`: решение спросит его, только если оно от
    него зависит (e5, e7). Эпоха с неизвестным временем якоря — `Unavailable`.
    Читает транспортом решения `trust.transport` (R7).
    """
    anon = trust.transport
    ranges: dict[int, Span | Unavailable] = {}
    sets: dict[str, PolicySet | Unavailable] = {}
    for epoch in trust.epochs:
        at = anchor_at.get(epoch.anchor, Unavailable("время якоря не загружено"))
        if isinstance(at, Unavailable):
            ranges[epoch.epoch] = Unavailable(f"якорь эпохи {epoch.epoch}: {at.reason}")
            continue
        if at > t:
            continue
        got = act_facts.history(
            anon,
            trust.source,
            epoch.anchor,
            epoch.last_confirmed,
            head=trust.policy_head,
        )
        if isinstance(got, act_facts.HistoryBreak):
            found = ("approval-policy", got.reason)
            return _register(journal, trust.snapshot, found, "policy_history_break")
        if isinstance(got, Unavailable):
            ranges[epoch.epoch] = got
            continue
        ranges[epoch.epoch] = tuple(
            ActivationView(("activation", a.merge_commit), a.merge_commit, a.merged_at)
            for a in got
        )
        for a in got:
            if a.merged_at <= t and a.merge_commit not in sets:
                sets[a.merge_commit] = policy_set(anon, trust.source, a)
    return ranges, sets


PRE_MIGRATION = "pre_migration"
"""Записи `ACT_MODE[P]` не было ни в одной активации — старый путь (§10)."""


def _modes(
    span: Span, sets: Mapping[str, PolicySet | Unavailable], product: str, now: datetime
) -> list[str] | Unavailable:
    found: list[str] = []
    for a in span:
        if a.at > now:
            continue
        policy = sets.get(a.merge_commit, Unavailable("набор активации не загружен"))
        if isinstance(policy, Unavailable):
            return Unavailable(f"набор {a.merge_commit[:12]}: {policy.reason}")
        found.append(mode_for(policy, product) or "")
    return found


def product_mode(
    trust: Trust,
    ranges: Mapping[int, Span | Unavailable],
    sets: Mapping[str, PolicySet | Unavailable],
    product: str,
    now: datetime,
) -> str | Unavailable:
    """Режим продукта СЕЙЧАС по истории режима (§10).

    `PRE_MIGRATION` | `merge` | `review`.

    Не «текущее значение»: удаление записи после первой (`""`), обратное
    переключение `review → merge` и недочитанная нужная история — `Unavailable`,
    а не legacy (ревизия b1, B1-5). Текущее `review` от прежних эпох не зависит
    (e7); иначе нужна история всех эпох.
    """
    open_span = ranges.get(
        trust.epochs[-1].epoch, Unavailable("диапазон открытой эпохи нет")
    )
    if isinstance(open_span, Unavailable):
        return Unavailable(f"история режима: {open_span.reason}")
    current = _modes(open_span, sets, product, now)
    if isinstance(current, Unavailable):
        return current
    if current and current[-1] == "review":
        return "review"
    history: list[str] = []
    for epoch in trust.epochs:
        span = ranges.get(epoch.epoch)
        if span is None:
            continue  # якорь эпохи позже now — эпоха не наступила
        if isinstance(span, Unavailable):
            return Unavailable(f"история режима, эпоха {epoch.epoch}: {span.reason}")
        got = _modes(span, sets, product, now)
        if isinstance(got, Unavailable):
            return got
        history.extend(got)
    entered = [i for i, m in enumerate(history) if m]
    if not entered:
        return PRE_MIGRATION
    tail = history[entered[0] :]
    if tail[-1] == "":
        return Unavailable(f"ACT_MODE[{product}] удалён после первой записи (§10 п. 2)")
    if "review" in tail:
        return Unavailable(f"ACT_MODE[{product}]: обратное переключение review → merge")
    return "merge"


def current_activation(
    trust: Trust, ranges: Mapping[int, Span | Unavailable]
) -> ActivationView | Unavailable:
    """Последняя активация открытой эпохи — для живой заявки (§6)."""
    span = ranges.get(
        trust.epochs[-1].epoch, Unavailable("диапазон открытой эпохи не прочитан")
    )
    if isinstance(span, Unavailable):
        return span
    if not span:
        return Unavailable("в открытой эпохе нет активаций")
    return span[-1]
