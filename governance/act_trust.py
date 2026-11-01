"""Доверенный контекст решения: корень `df-trust`, периметр, эпохи (спека A rev 16 §6).

Перед КАЖДЫМ решением: привязка `/etc/df/trust.toml` → снимок `df-trust`
(SHA фиксируется; `source.env` и все версии `epochs.yaml` — из него) →
журнал событий периметра (если передан) → самоотчёт каждого credential →
ruleset обоих репо доверия → `allow_rebase_merge`; затем лениво — время
якорей и диапазоны эпох с наборами ≤ T. Недоступное обязательное чтение —
`Unavailable` без события; событие — только для установленного нарушения.
`act_source.load_source()` (файл devtools) здесь не вызывается (§6.5).

Модуль в цепочке обвязки мержа (`act_premerge`): только `governance/act_*`,
`governance/facts`, `ssot_env`, stdlib. Журнал периметра — протокол
`Journal`; реализации (`act_journal`) тянут `df/` и сюда не импортируются.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from typing import Any, Protocol
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
_BINDING_KEYS = (
    "host",
    "df_trust_repository_id",
    "df_trust_full_name",
    "df_trust_branch",
    "approval_policy_repository_id",
    "transport",
)
Span = tuple[ActivationView, ...]


class TrustError(ValueError):
    """Привязка корня доверия не читается — отказ конфигурации."""


@dataclass(frozen=True)
class Binding:
    """Защищённая привязка `/etc/df/trust.toml` (спека B §3.7; Р-6)."""

    host: str
    df_trust_repository_id: int
    df_trust_full_name: str
    df_trust_branch: str
    approval_policy_repository_id: int
    transport: str


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
    """`source.env` и журнал эпох из ОДНОГО зафиксированного снимка."""

    root: TrustRoot
    full_name: str
    source: SourceV2
    epochs: tuple[Epoch, ...]


@dataclass(frozen=True)
class Trust:
    """Доверенный контекст решения; `unverified` — непроверенные предпосылки."""

    snapshot: TrustSnapshot
    unverified: tuple[str, ...]

    @property
    def source(self) -> SourceV2:
        return self.snapshot.source

    @property
    def epochs(self) -> tuple[Epoch, ...]:
        return self.snapshot.epochs

    @property
    def root(self) -> TrustRoot:
        return self.snapshot.root


@dataclass(frozen=True)
class Credential:
    """Credential, который процесс ДЕРЖИТ: профиль `gh` либо токен окружения."""

    label: str
    gh_config_dir: Path | None = None
    token: str | None = None


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


@dataclass(frozen=True)
class Readers:
    """Инжектируемые чтения: анонимный REST и GET от имени credential."""

    anon: Any
    as_credential: Callable[[Credential, str], Fact[object]]


def default_readers() -> Readers:
    """Боевые чтения: `AnonTransport` и `gh api` под credential."""
    return Readers(AnonTransport(), gh_get)


# --- привязка -------------------------------------------------------------------


def parse_binding(text: str) -> Binding:
    """Строгий разбор `trust.toml` (те же ключи, что `df.stage2_config`)."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise TrustError(f"trust.toml не TOML: {exc}") from exc
    if data.get("schema") != BINDING_SCHEMA:
        raise TrustError(f"trust.toml: схема не {BINDING_SCHEMA}")
    if set(data) - {"schema"} != set(_BINDING_KEYS):
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
    return Binding(**{k: data[k] for k in _BINDING_KEYS})


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


def _chain(anon: Any, repo: str, head: str) -> list[str] | Unavailable:
    """First-parent от `head` до корня; звенья от корня к голове."""
    chain: list[str] = []
    sha: str | None = head
    while sha is not None:
        if len(chain) == MAX_CHAIN:
            return Unavailable(f"{repo}: цепочка длиннее {MAX_CHAIN}")
        body = _get(anon, f"{_repo_path(repo)}/commits/{sha}")
        if isinstance(body, Unavailable):
            return body
        parents = body.get("parents") if isinstance(body, dict) else None
        if (
            not isinstance(body, dict)
            or body.get("sha") != sha
            or not isinstance(parents, list)
        ):
            return Unavailable(f"{repo}@{sha[:12]}: ответ не о том коммите")
        chain.append(sha)
        first = (
            parents[0].get("sha") if parents and isinstance(parents[0], dict) else None
        )
        sha = first if isinstance(first, str) else None
    return chain[::-1]


def _journal_versions(
    anon: Any, repo: str, chain: Sequence[str]
) -> list[tuple[Epoch, ...]] | Unavailable:
    versions: list[tuple[Epoch, ...]] = []
    last: bytes | None = None
    for sha in chain:
        raw = contents_bytes(anon, repo, JOURNAL_FILE, sha)
        if isinstance(raw, Unavailable):
            return raw
        if raw is None:
            if versions:
                return Unavailable(f"{repo}@{sha[:12]}: {JOURNAL_FILE} удалён")
            continue
        if raw == last:
            continue
        try:
            versions.append(parse_journal(raw.decode("utf-8")))
        except (JournalError, UnicodeDecodeError) as exc:
            return Unavailable(f"{repo}@{sha[:12]}: {exc}")
        last = raw
    return versions


def _checked_epochs(
    versions: list[tuple[Epoch, ...]], source: SourceV2
) -> tuple[Epoch, ...] | Unavailable:
    if not versions:
        return Unavailable(f"в снимке нет {JOURNAL_FILE}")
    first = versions[0]
    if (
        len(first) != 1
        or first[0].closed
        or first[0].anchor != source.bootstrap_activation
    ):
        return Unavailable(
            "первая версия журнала — не bootstrap с BOOTSTRAP_ACTIVATION"
        )
    try:
        for prev, new in pairwise(versions):
            check_transition(prev, new)
    except JournalError as exc:
        return Unavailable(f"недопустимый переход журнала: {exc}")
    return versions[-1]


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
    raw_source = contents_bytes(anon, repo, SOURCE_FILE, head)
    if not isinstance(raw_source, bytes):
        return raw_source or Unavailable(f"{repo}@{head[:12]}: нет {SOURCE_FILE}")
    try:
        source = parse_source(raw_source.decode("utf-8"))
    except (SourceError, UnicodeDecodeError) as exc:
        return Unavailable(f"{repo}@{head[:12]}: {exc}")
    policy = _named(anon, binding.approval_policy_repository_id, source.repo)
    if isinstance(policy, Unavailable):
        return policy
    chain = _chain(anon, repo, head)
    if isinstance(chain, Unavailable):
        return chain
    versions = _journal_versions(anon, repo, chain)
    if isinstance(versions, Unavailable):
        return versions
    epochs = _checked_epochs(versions, source)
    if isinstance(epochs, Unavailable):
        return epochs
    root = TrustRoot(
        binding.host, binding.df_trust_repository_id, binding.df_trust_branch, head
    )
    return TrustSnapshot(root, repo, source, epochs)


# --- периметр -----------------------------------------------------------------------


def gh_get(cred: Credential, path: str) -> Fact[object]:
    """`gh api <path>` ИМЕННО этим credential: токены окружения снимаются,
    чтобы профиль не подменился чужим токеном."""
    env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
    if cred.token is not None:
        env["GH_TOKEN"] = cred.token
    if cred.gh_config_dir is not None:
        env["GH_CONFIG_DIR"] = str(cred.gh_config_dir)
    try:
        done = subprocess.run(
            ["gh", "api", path],
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


def _roles(snap: TrustSnapshot) -> tuple[tuple[str, str], ...]:
    return (("approval-policy", snap.source.repo), ("df-trust", snap.full_name))


Found = tuple[str, str] | Unavailable | None
"""Нарушение `(роль репо, деталь)`, недоступность или чисто."""


def _self_report(
    readers: Readers, creds: Sequence[Credential], snap: TrustSnapshot
) -> Found:
    gap: Unavailable | None = None
    for cred in creds:
        for role, repo in _roles(snap):
            fact = readers.as_credential(cred, _repo_path(repo))
            body = fact.value if fact.outcome is Outcome.FOUND else None
            perms = body.get("permissions") if isinstance(body, dict) else None
            verdict = permissions_refusal(perms if isinstance(perms, dict) else None)
            if isinstance(verdict, Violation):
                return role, f"{cred.label} на {repo}: {verdict.detail}"
            if isinstance(verdict, Unavailable) and gap is None:
                why = verdict.reason if fact.outcome is Outcome.FOUND else fact.detail
                gap = Unavailable(f"самоотчёт {cred.label} на {repo}: {why}")
    return gap


def _rulesets(anon: Any, snap: TrustSnapshot) -> Found:
    branches = {"approval-policy": snap.source.branch, "df-trust": snap.root.branch}
    for role, repo in _roles(snap):
        branch = quote(branches[role], safe="")
        body = _get(anon, f"{_repo_path(repo)}/rules/branches/{branch}")
        if isinstance(body, Unavailable):
            return body
        if not isinstance(body, list):
            return Unavailable(f"{repo}: правила ветки — не список")
        kinds = {r.get("type") for r in body if isinstance(r, dict)}
        if missing := sorted(REQUIRED_RULES - kinds):
            return role, f"{repo}@{branches[role]}: нет правил {missing}"
    return None


def _rebase(anon: Any, snap: TrustSnapshot) -> Found | str:
    """Четыре состояния (решения владельца 13.10, п. 1).

    `REBASE_PREMISE` — значение скрыто: предпосылка не проверена."""
    body = _get(anon, _repo_path(snap.source.repo))
    if isinstance(body, Unavailable):
        return body
    if not isinstance(body, dict):
        return Unavailable(f"{snap.source.repo}: ответ не объект")
    value = body.get("allow_rebase_merge")
    if value is None:
        return REBASE_PREMISE
    if value is True:
        return "approval-policy", f"{snap.source.repo}: allow_rebase_merge = true"
    if value is False:
        return None
    return Unavailable(f"{snap.source.repo}: allow_rebase_merge = {value!r}")


def _register(
    journal: Journal, snap: TrustSnapshot, found: tuple[str, str]
) -> Unavailable:
    """Обнаруженное нарушение — событие журнала периметра (§6.3), затем отказ."""
    role, detail = found
    failed = journal.record(snap, role, detail)
    if failed is not None:
        return Unavailable(
            f"нарушение периметра ({detail}); регистрация: {failed.reason}"
        )
    return Unavailable(f"нарушение периметра: {detail}")


def _snapshot_for_decision(
    readers: Readers, binding: Binding, journal: Journal
) -> TrustSnapshot | Unavailable:
    for _ in range(3):
        snap = read_trust_snapshot(readers.anon, binding)
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


def load_trust(
    readers: Readers,
    binding: Binding,
    journal: Journal,
    *,
    creds: Sequence[Credential],
) -> Trust | Unavailable:
    """Обязательные проверки перед решением (п. 1–5 Task 12a), с ранним выходом.

    Журнал периметра обязателен у КАЖДОГО потребителя, включая предпроверку
    мержа (ревизия b1: `act_journal_wire`): блок, зарегистрированный другим
    потребителем, учитывается, а обнаруженное здесь нарушение сохраняется."""
    snap = _snapshot_for_decision(readers, binding, journal)
    if isinstance(snap, Unavailable):
        return snap
    unverified: tuple[str, ...] = ()
    for check in (
        lambda: _self_report(readers, creds, snap),
        lambda: _rulesets(readers.anon, snap),
        lambda: _rebase(readers.anon, snap),
    ):
        found = check()
        if found == REBASE_PREMISE:
            unverified = (REBASE_PREMISE,)
        elif isinstance(found, Unavailable):
            return found
        elif isinstance(found, tuple):
            return _register(journal, snap, found)
    return Trust(snap, unverified)


# --- якоря, диапазоны, наборы --------------------------------------------------------


def load_anchor_times(
    anon: Any, trust: Trust, t: datetime
) -> dict[str, datetime | Unavailable]:
    """Время якоря КАЖДОЙ эпохи (`merged_at` активации, №19).

    Не лениво (ревизия b1, B1-10): admission при `merge`/без записи на T
    требует истории режима всех прежних эпох (`act_check._mode_history`), а её
    диапазоны читаются от якорей. e7 сохраняется иначе: недоступное время
    старого якоря записывается `Unavailable` и мешает лишь решению, которое от
    него зависит (`_epoch_at` идёт от новой эпохи назад и до старого якоря не
    доходит, если новый уже предшествует T). `t` — для симметрии с
    `load_ranges` и будущего предела обхода; на чтение не влияет.
    """
    del t
    times: dict[str, datetime | Unavailable] = {}
    for epoch in reversed(trust.epochs):
        got = act_facts.activation(anon, trust.source, epoch.anchor)
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
    anon: Any,
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
    """
    ranges: dict[int, Span | Unavailable] = {}
    sets: dict[str, PolicySet | Unavailable] = {}
    for epoch in trust.epochs:
        at = anchor_at.get(epoch.anchor, Unavailable("время якоря не загружено"))
        if isinstance(at, Unavailable):
            ranges[epoch.epoch] = Unavailable(f"якорь эпохи {epoch.epoch}: {at.reason}")
            continue
        if at > t:
            continue
        got = act_facts.history(anon, trust.source, epoch.anchor, epoch.last_confirmed)
        if isinstance(got, act_facts.HistoryBreak):
            return _register(journal, trust.snapshot, ("approval-policy", got.reason))
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
