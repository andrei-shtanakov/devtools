"""`darkfactory setup-trust` (installer spec 6.2 rev 11; stage 3a, Task 12).

Two phases, two contexts (spec 6.1 «Операции и их сверка»):

- `check` -- the human, never root, with a temporary read-only PAT of their
  own (fine-grained, the two trust repositories; read from the terminal or
  stdin, never stored or printed). The publishable templates are shown first
  (fixed text, spec 2.3); then the perimeter of both trust sources is read
  over stdlib REST JSON (`check_perimeter`): every answer's status goes to
  the report, a 403/404 is «не проверено», never «ок». The report -- the
  draft of the perimeter evidence -- goes to a file of the human's, and its
  digest is printed for the write phase.
- `write` -- root, under the writer lock (`WRITER_COMMANDS`): the binding is
  built from the human's arguments and the report whose digest the human
  names (`load_report`, `check_report`); the history token profile
  `/etc/df/history-gh` (`root:df-history`, `0750`/`0640`), then
  `trust.toml` (`write_binding`), then the root's own `trust-status`
  (`verify_after_write`); the evidence goes to
  `/var/lib/df/evidence/perimeter-<time>.json`. Without a history token the
  binding is not written (O14); an `unavailable` (or `blocked`) root is
  «установка не завершена».

The stops are the checker's own settings findings
(`governance.act_trust_pair.settings_findings`: `ruleset_missing`,
`rebase_allowed`, `policy_branch_mismatch`; `not_public` of `read_pair`)
plus the installer's preconditions (a tariff without rulesets, an unreadable
repository or `source.env`, a policy repository that is not the one
`source.env` names). `df` never imports `governance`: `REQUIRED_RULES` is a
mirror, pinned by a test. Root builds no path or command from what a reader
answered: names go into `trust.toml` as data, within the publisher's own
limits (`trust_routes.binding_problem`).
"""

from __future__ import annotations

import base64
import binascii
import getpass
import hashlib
import json
import os
import re
import stat
import sys
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from df import fsutil, inputs, journal, perimeter_client, setup_agent
from df import platform as plat
from df.config import ConfigError
from df.digest import TreeError
from df.journal import StepFailed
from df.paths import Layout
from df.publisher import github, history, request
from df.publisher.github import Transport
from df.publisher.request import BadResponse, TrustStatus
from df.publisher.trust_routes import binding_problem, policy_problem
from df.roles import HISTORY_GROUP
from df.stage2_config import TrustBinding, dump_trust, load_trust

STEP = "setup-trust"
#: The only host v1 binds (`trust.toml`, transport `public`).
HOST = "github.com"
REPORT_SCHEMA = "df/setup-trust-report/v1"
EVIDENCE_SCHEMA = "df/perimeter-evidence/v1"
#: Bytes a report may have when root reads it back.
REPORT_CAP = 1 << 20
#: How old a report root still takes (the PAT is short-lived, the facts move).
REPORT_MAX_AGE = timedelta(hours=1)
PROFILE_DIR_MODE = 0o750
PROFILE_FILE_MODE = 0o640
#: `trust.toml` is read by `df-publisher` (and the human's `doctor`).
TRUST_MODE = 0o644
EVIDENCE_DIR_MODE = 0o700
EVIDENCE_MODE = 0o600
#: The account label of the profile's `hosts.yml` (the form measured by
#: draft devtools#693, `tests/df/test_root_gh_shared_profile.py`).
PROFILE_LOGIN = "df-history"
#: Seconds root waits for `trust-status`: up to `MAX_EVALUATIONS` (3) checker
#: runs of `PRECHECK_WAIT` (200 s) each, and the reads around them.
TRUST_STATUS_TIMEOUT = 720.0
#: Mirror of `governance.act_trust.REQUIRED_RULES` (pinned by a test).
REQUIRED_RULES = frozenset({"deletion", "non_fast_forward", "pull_request"})
ROLES = ("approval-policy", "df-trust")
#: The checker's names (`settings_findings`, `read_pair`) and the installer's.
STOP_KINDS = frozenset(
    {
        "ruleset_missing",
        "rebase_allowed",
        "policy_branch_mismatch",
        "not_public",
        "tariff_without_rulesets",
        "repository_unreadable",
        "source_env_invalid",
        "policy_repository_mismatch",
        "unsupported_size",
    }
)
SOURCE_KEYS = (
    "APPROVAL_POLICY_REPO",
    "APPROVAL_POLICY_BRANCH",
    "BOOTSTRAP_ACTIVATION",
    "POLICY_ADMINS",
)
_SHA40 = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
#: GitHub's answer for rulesets on a plan without them (private, personal).
_UPGRADE = "upgrade to github pro"
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC

CHECK_AS_ROOT = (
    "setup-trust check: проверка периметра — от человека без root (спека §6.2): "
    "запустите без sudo"
)
NO_HISTORY_PROFILE = (
    "нет профиля токена истории: привязка не записана, установка не завершена "
    "(O14). Создайте отдельный read-only токен (classic без scopes или "
    "fine-grained «public repositories, read-only»; не PAT проверки периметра) и "
    "повторите с --history-token-file <файл>"
)
NOT_FINISHED = "установка не завершена"
#: Spec 6.1 «Размер вердикта»: the exit of `unsupported_size: epochs`, named
#: verbatim by every refusal text that carries it (and by `doctor`).
EPOCHS_REASON = "unsupported_size: epochs"
EPOCHS_EXIT = (
    "журнал эпох df-trust длиннее 64 эпох; следующая эпоха его не сократит. "
    "Выход (спека §6.1): (1) новый репо df-trust с начальным журналом из одной "
    "эпохи (bootstrap-порядок §6.2); (2) darkfactory setup-trust check --rebind и "
    "sudo darkfactory setup-trust write --rebind — полная проверка периметра, "
    "неизвестный корень принимается холодно; (3) новые прогоны — прогоны прежнего "
    "корня операций над PR не получают"
)

# --- the publishable templates (spec 2.3, 6.2 item 4) --------------------------

#: The files the human publishes by PR, by `<repo>/<path>`: fixed text with
#: `<...>` placeholders the human fills (`PLACEHOLDERS`).
TEMPLATES: Mapping[str, str] = {
    "df-trust/source.env": (
        "# Координаты источника политики (спека установщика §6.2, п. 4).\n"
        "# Authority-root: PR, меняющий этот файл, мержит человек.\n"
        "APPROVAL_POLICY_REPO=<OWNER>/<POLICY_REPO>\n"
        "APPROVAL_POLICY_BRANCH=<POLICY_BRANCH>\n"
        "BOOTSTRAP_ACTIVATION=<BOOTSTRAP_ACTIVATION>\n"
        "POLICY_ADMINS=<POLICY_ADMINS>\n"
    ),
    "df-trust/epochs.yaml": (
        "# Журнал эпох доверия: одна открытая эпоха, якорь = BOOTSTRAP_ACTIVATION.\n"
        "schema: approval-policy-epochs/v1\n"
        "epochs:\n"
        "  - epoch: 1\n"
        '    anchor: "<BOOTSTRAP_ACTIVATION>"\n'
    ),
    "approval-policy/policy/approvers.env": (
        "# Кто утверждает и кто мержит (логины GitHub через запятую).\n"
        "AUTHORIZED_APPROVER_ACCOUNTS=<APPROVERS>\n"
        "AUTHORIZED_MERGER_ACCOUNTS=<MERGERS>\n"
    ),
    "approval-policy/policy/actors.yaml": (
        "version: 1\n"
        "human_identities:\n"
        '  - "github:<HUMAN>"\n'
        "agent_identities:\n"
        '  - "github:<AGENT>"\n'
    ),
    "approval-policy/policy/modes.yaml": (
        '# Режим каждого продукта: "<owner>/<repo>": merge | review.\n'
        "schema: approval-policy-modes/v1\n"
        "modes: {}\n"
    ),
}
PLACEHOLDERS = (
    "<OWNER>",
    "<POLICY_REPO>",
    "<POLICY_BRANCH>",
    "<BOOTSTRAP_ACTIVATION>",
    "<POLICY_ADMINS>",
    "<APPROVERS>",
    "<MERGERS>",
    "<HUMAN>",
    "<AGENT>",
)


def show_publishable(templates: Mapping[str, str] = TEMPLATES) -> str:
    """The publishable content, shown before the first PR (spec 2.3): each
    file's exact bytes between marker lines, with its size and sha256."""
    lines = [
        (
            "Публикуемое содержимое (спека §2.3): точные байты каждого файла "
            "между строками-разделителями; <...> заполняет человек."
        ),
        f"Заполнители: {', '.join(PLACEHOLDERS)}.",
    ]
    out = "\n".join(lines) + "\n"
    for name, text in templates.items():
        data = text.encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        out += f"----- {name} ({len(data)} байт, sha256 {digest}) -----\n"
        out += text
        out += f"----- конец {name} -----\n"
    return out


def explain(text: str) -> str:
    """`text`, followed by the exit of spec 6.1 when it carries
    `unsupported_size: epochs`."""
    return f"{text} — {EPOCHS_EXIT}" if EPOCHS_REASON in text else text


# --- the read phase: the perimeter check (the human, a temporary PAT) ----------


@dataclass(frozen=True)
class Stop:
    """A condition that stops the installation (spec 6.2 item 2, С-2)."""

    kind: str
    role: str
    detail: str


@dataclass(frozen=True)
class PerimeterReport:
    """The read phase's facts: the draft of the perimeter evidence.

    `facts` and `calls` are data for the evidence only; root takes from the
    report just the canonical names, the heads and the verdict (`stops`)."""

    checked_at: str
    checker: str | None
    rebind: bool
    policy_repository_id: int
    trust_repository_id: int
    trust_branch: str
    policy_full_name: str | None
    trust_full_name: str | None
    policy_default_branch: str | None
    trust_head: str | None
    source: Mapping[str, object] | None
    facts: Mapping[str, object]
    calls: tuple[tuple[str, int | None], ...]
    stops: tuple[Stop, ...]
    unverified: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """No stop, and both repositories read by id."""
        named = self.policy_full_name is not None and self.trust_full_name is not None
        return named and not self.stops

    def to_json(self) -> str:
        """The report file's text: sorted keys, indented, one final newline."""
        data = {"schema": REPORT_SCHEMA, **asdict(self)}
        data["calls"] = [list(c) for c in self.calls]
        return json.dumps(data, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


@dataclass
class _Reader:
    """One transport, every call's path and status recorded."""

    transport: Transport
    calls: list[tuple[str, int | None]] = field(default_factory=list)
    stops: list[Stop] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)

    def get(self, path: str) -> tuple[int | None, object]:
        """`GET <API>/<path>`: the status (`None`: not delivered) and the
        JSON body (`None`: none). No exception text is kept."""
        try:
            status, _headers, raw = self.transport(
                "GET", f"{github.API}/{path}", {}, None
            )
        except Exception:  # noqa: BLE001 - not delivered: «не проверено»
            self.calls.append((path, None))
            return None, None
        self.calls.append((path, status))
        try:
            return status, json.loads(raw) if raw else None
        except (ValueError, RecursionError):
            return status, None

    def stop(self, kind: str, role: str, detail: str) -> None:
        self.stops.append(Stop(kind, role, detail))

    def gap(self, what: str, status: int | None) -> None:
        self.unverified.append(f"{what}: не проверено ({_status(status)})")


def pat_transport(token: str) -> Transport:
    """The read phase's transport: `GET` on the API host only, the PAT as
    `Authorization` (`history.history_transport`'s rules; the PAT is never
    the history token)."""
    return history.history_transport(token)


def read_pat() -> str:
    """The temporary PAT: from the terminal without echo, else one line of
    stdin. Its form is a GitHub token's; it is never printed."""
    if sys.stdin.isatty():
        token = getpass.getpass("PAT проверки периметра (только чтение): ")
    else:
        token = sys.stdin.readline(setup_agent.TOKEN_CAP + 2)
    token = token.strip()
    if setup_agent.TOKEN_FORM.fullmatch(token.encode("utf-8", "replace")) is None:
        raise StepFailed(STEP, "PAT: не токен GitHub (github_pat_… или ghp_…)")
    return token


def check_perimeter(
    pat: Transport,
    policy_repo: int,
    trust_repo: int,
    *,
    trust_branch: str,
    rebind: bool = False,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> PerimeterReport:
    """The full perimeter check of both trust sources with the human's PAT
    (spec 6.2 item 6, the API table of 6.2): identities by id, `source.env`
    on the pinned `df-trust` branch, then for each repository the required
    branch rules, rulesets with `bypass_actors`, merge settings, branch
    protection, deploy keys, `actions/permissions`, collaborators, teams,
    hooks and environments; the account's plan. Never raises on an answer:
    a failed read is a stop where the check needs it, else unverified."""
    r = _Reader(pat)
    facts: dict[str, object] = {}
    repos = {
        role: _repository(r, role, repo_id, facts)
        for role, repo_id in (
            ("approval-policy", policy_repo),
            ("df-trust", trust_repo),
        )
    }
    checker = _account(r, facts)
    policy, trust = repos["approval-policy"], repos["df-trust"]
    source: Mapping[str, object] | None = None
    head: str | None = None
    if policy is not None and trust is not None:
        head = _head(r, trust.full_name, trust_branch)
        source = _source(r, trust.full_name, trust_branch)
        branch = _policy_branch(r, policy, source)
        for role, repo, pinned in (
            ("approval-policy", policy, branch),
            ("df-trust", trust, trust_branch),
        ):
            facts[role] = {**_facts_of(facts, role), **_settings(r, role, repo, pinned)}
    for role in ROLES:  # spec 6.2: third-party App installations are invisible
        r.unverified.append(
            f"apps@{role}: установки сторонних App PAT не видит — подтверждает "
            "человек в интерфейсе GitHub"
        )
    return PerimeterReport(
        checked_at=now().isoformat(timespec="seconds"),
        checker=checker,
        rebind=rebind,
        policy_repository_id=policy_repo,
        trust_repository_id=trust_repo,
        trust_branch=trust_branch,
        policy_full_name=None if policy is None else policy.full_name,
        trust_full_name=None if trust is None else trust.full_name,
        policy_default_branch=None if policy is None else policy.default_branch,
        trust_head=head,
        source=source,
        facts=facts,
        calls=tuple(r.calls),
        stops=tuple(r.stops),
        unverified=tuple(r.unverified),
    )


@dataclass(frozen=True)
class _Repo:
    full_name: str
    default_branch: str


def _facts_of(facts: Mapping[str, object], role: str) -> dict[str, object]:
    found = facts.get(role)
    return dict(found) if isinstance(found, dict) else {}


def _repository(
    r: _Reader, role: str, repo_id: int, facts: dict[str, object]
) -> _Repo | None:
    """`GET /repositories/{id}`: the canonical name (as GitHub returns it,
    never the operator's spelling), the default branch, visibility and
    `allow_rebase_merge`. Unreadable -- a stop."""
    path = f"repositories/{repo_id}"
    status, body = r.get(path)
    if status != 200 or not isinstance(body, dict) or body.get("id") != repo_id:
        r.stop("repository_unreadable", role, f"{path}: {_status(status)}")
        return None
    name, branch = body.get("full_name"), body.get("default_branch")
    if not isinstance(name, str) or not isinstance(branch, str) or not branch:
        r.stop("repository_unreadable", role, f"{path}: нет имени или default branch")
        return None
    private, rebase = body.get("private"), body.get("allow_rebase_merge")
    facts[role] = {
        "repository": {
            "id": repo_id,
            "full_name": name,
            "default_branch": branch,
            "private": private,
            "allow_rebase_merge": rebase,
        }
    }
    if private is True:
        r.stop("not_public", role, f"{name}: репо не публичный (private = true)")
    elif private is not False:
        r.unverified.append(f"visibility@{role}: не прочитана ({private!r})")
    if rebase is True:
        r.stop("rebase_allowed", role, f"{name}: allow_rebase_merge = true")
    elif rebase is None:
        r.unverified.append(f"allow_rebase_merge@{role}")
    elif rebase is not False:
        r.stop("rebase_allowed", role, f"{name}: allow_rebase_merge = {rebase!r}")
    return _Repo(name, branch)


def _account(r: _Reader, facts: dict[str, object]) -> str | None:
    """`GET /user`: the checker's login and the plan (Plan: read)."""
    status, body = r.get("user")
    if status != 200 or not isinstance(body, dict):
        r.gap("user", status)
        return None
    login = body.get("login")
    plan = body.get("plan")
    name = plan.get("name") if isinstance(plan, dict) else None
    facts["account"] = {"login": login, "plan": name}
    if not isinstance(name, str):
        r.unverified.append("plan: тариф не прочитан (нужно Plan: read)")
    return login if isinstance(login, str) else None


def _head(r: _Reader, full_name: str, branch: str) -> str | None:
    """The SHA of the pinned `df-trust` branch (into the evidence)."""
    path = f"{_repo_path(full_name)}/branches/{quote(branch, safe='')}"
    status, body = r.get(path)
    commit = body.get("commit") if isinstance(body, dict) else None
    sha = commit.get("sha") if isinstance(commit, dict) else None
    if status != 200 or not isinstance(sha, str) or not _SHA40.fullmatch(sha):
        r.stop("repository_unreadable", "df-trust", f"{path}: {_status(status)}")
        return None
    return sha


def _source(r: _Reader, full_name: str, branch: str) -> Mapping[str, object] | None:
    """`source.env` on the pinned branch (contents API), parsed as
    `governance.act_source.parse_source` does: each key exactly once and not
    empty, a full `BOOTSTRAP_ACTIVATION`, a non-empty `POLICY_ADMINS`."""
    path = f"{_repo_path(full_name)}/contents/source.env?ref={quote(branch, safe='')}"
    status, body = r.get(path)
    raw = _content(body) if status == 200 else None
    if raw is None:
        r.stop("source_env_invalid", "df-trust", f"{path}: {_status(status)}")
        return None
    try:
        values = parse_source_env(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        r.stop("source_env_invalid", "df-trust", f"source.env: {exc}")
        return None
    return {
        "repo": values["APPROVAL_POLICY_REPO"],
        "branch": values["APPROVAL_POLICY_BRANCH"],
        "bootstrap_activation": values["BOOTSTRAP_ACTIVATION"],
        "policy_admins": sorted(_admins(values["POLICY_ADMINS"])),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def parse_source_env(text: str) -> dict[str, str]:
    """The four keys of `source.env` (`ValueError` names the first problem)."""
    values: dict[str, str] = {}
    for key in SOURCE_KEYS:
        found = [
            line.strip()[len(key) + 1 :].strip()
            for line in text.splitlines()
            if line.strip().startswith(f"{key}=")
        ]
        if len(found) != 1 or not found[0]:
            raise ValueError(f"{key}: ожидалась ровно одна непустая строка")
        values[key] = found[0]
    if not _SHA40.fullmatch(values["BOOTSTRAP_ACTIVATION"]):
        raise ValueError("BOOTSTRAP_ACTIVATION: нужен полный 40-символьный SHA")
    if not _admins(values["POLICY_ADMINS"]):
        raise ValueError("POLICY_ADMINS: пустой allowlist администраторов")
    return values


def _admins(text: str) -> set[str]:
    return {part.strip() for part in text.split(",") if part.strip()}


def _content(body: object) -> bytes | None:
    if not isinstance(body, dict) or body.get("encoding") != "base64":
        return None
    try:
        return base64.b64decode("".join(str(body.get("content", "")).split()))
    except (binascii.Error, ValueError):
        return None


def _policy_branch(
    r: _Reader, policy: _Repo, source: Mapping[str, object] | None
) -> str:
    """The pinned policy branch: `source.env`'s, which must be the default
    branch of the repository it names (С-2) and that repository the bound
    one (names compared case-folded; the id decides)."""
    if source is None:
        return policy.default_branch
    repo, branch = str(source["repo"]), str(source["branch"])
    if repo.casefold() != policy.full_name.casefold():
        r.stop(
            "policy_repository_mismatch",
            "approval-policy",
            f"source.env: {repo!r}, репо по id — {policy.full_name!r}",
        )
    if branch != policy.default_branch:
        r.stop(
            "policy_branch_mismatch",
            "approval-policy",
            f"{policy.full_name}: ветка source.env {branch!r} ≠ default branch "
            f"{policy.default_branch!r}",
        )
    problem = policy_problem(policy.full_name, branch)
    if problem is not None:
        r.stop("unsupported_size", "approval-policy", problem)
    return branch


def _settings(r: _Reader, role: str, repo: _Repo, branch: str) -> dict[str, object]:
    """The settings of one trust repository on its pinned `branch`."""
    base = _repo_path(repo.full_name)
    pinned = quote(branch, safe="")
    found: dict[str, object] = {"rules": _rules(r, role, repo, branch)}
    found["rulesets"] = _rulesets(r, role, base)
    reads: tuple[tuple[str, str, Callable[[object], object]], ...] = (
        ("protection", f"{base}/branches/{pinned}/protection", _as_is),
        ("deploy_keys", f"{base}/keys?per_page=100", _keys),
        ("actions_permissions", f"{base}/actions/permissions", _as_is),
        (
            "collaborators",
            f"{base}/collaborators?affiliation=all&per_page=100",
            _people,
        ),
        ("teams", f"{base}/teams?per_page=100", _teams),
        ("hooks", f"{base}/hooks?per_page=100", _hooks),
        ("environments", f"{base}/environments?per_page=100", _environments),
    )
    for name, path, shape in reads:
        status, body = r.get(path)
        if name == "protection" and status == 404 and _not_protected(body):
            found[name] = {"status": 404, "value": "not protected"}
            continue
        found[name] = {
            "status": status,
            "value": shape(body) if status == 200 else None,
        }
        if status != 200:
            r.gap(f"{name}@{role}", status)
    return found


def _rules(r: _Reader, role: str, repo: _Repo, branch: str) -> object:
    """The rules in force on the pinned branch: `REQUIRED_RULES` or a stop;
    a tariff without rulesets is a stop of its own (spec 6.2 item 2)."""
    path = f"{_repo_path(repo.full_name)}/rules/branches/{quote(branch, safe='')}"
    status, body = r.get(path)
    if _upgrade(status, body):
        r.stop(
            "tariff_without_rulesets", role, f"{path}: rulesets недоступны на тарифе"
        )
        return None
    if status != 200 or not isinstance(body, list):
        r.stop(
            "ruleset_missing", role, f"{path}: правила не прочитаны ({_status(status)})"
        )
        return None
    kinds = sorted({str(x.get("type")) for x in body if isinstance(x, dict)})
    missing = sorted(REQUIRED_RULES - set(kinds))
    if missing:
        r.stop(
            "ruleset_missing", role, f"{repo.full_name}@{branch}: нет правил {missing}"
        )
    return kinds


def _rulesets(r: _Reader, role: str, base: str) -> object:
    """Every ruleset with its `bypass_actors`; a non-empty list is a premise
    the human confirms (only `POLICY_ADMINS`, spec 6.2 item 2)."""
    status, body = r.get(f"{base}/rulesets?includes_parents=true&per_page=100")
    if _upgrade(status, body):
        r.stop(
            "tariff_without_rulesets", role, f"{base}/rulesets: недоступны на тарифе"
        )
        return None
    if status != 200 or not isinstance(body, list):
        r.gap(f"rulesets@{role}", status)
        return None
    found: list[object] = []
    for item in body:
        ruleset_id = item.get("id") if isinstance(item, dict) else None
        if not isinstance(ruleset_id, int):
            continue
        status, detail = r.get(f"{base}/rulesets/{ruleset_id}")
        if status != 200 or not isinstance(detail, dict):
            r.gap(f"rulesets/{ruleset_id}@{role}", status)
            continue
        bypass = detail.get("bypass_actors")
        found.append(
            {
                "id": ruleset_id,
                "name": detail.get("name"),
                "target": detail.get("target"),
                "enforcement": detail.get("enforcement"),
                "bypass_actors": bypass,
            }
        )
        if bypass != []:
            r.unverified.append(
                f"bypass_actors@{role}: ruleset {ruleset_id} — {bypass!r}; "
                "допустимы только POLICY_ADMINS — подтверждает человек"
            )
    return found


def _upgrade(status: int | None, body: object) -> bool:
    message = body.get("message") if isinstance(body, dict) else None
    return status == 403 and isinstance(message, str) and _UPGRADE in message.casefold()


def _not_protected(body: object) -> bool:
    message = body.get("message") if isinstance(body, dict) else None
    return message == "Branch not protected"


def _as_is(body: object) -> object:
    return body


def _keys(body: object) -> object:
    return [
        {"id": k.get("id"), "title": k.get("title"), "read_only": k.get("read_only")}
        for k in _items(body)
    ]


def _people(body: object) -> object:
    return [{"login": p.get("login"), "role": p.get("role_name")} for p in _items(body)]


def _teams(body: object) -> object:
    return [
        {"slug": t.get("slug"), "permission": t.get("permission")} for t in _items(body)
    ]


def _hooks(body: object) -> object:
    """Ids, state and events only: a hook's URL may carry a secret."""
    return [
        {"id": h.get("id"), "active": h.get("active"), "events": h.get("events")}
        for h in _items(body)
    ]


def _environments(body: object) -> object:
    envs = body.get("environments") if isinstance(body, dict) else None
    return [e.get("name") for e in _items(envs)]


def _items(body: object) -> list[dict[str, object]]:
    return [x for x in body if isinstance(x, dict)] if isinstance(body, list) else []


def _repo_path(full_name: str) -> str:
    owner, _, name = full_name.partition("/")
    return f"repos/{quote(owner, safe='')}/{quote(name, safe='')}"


def _status(status: int | None) -> str:
    return "не доставлен" if status is None else f"HTTP {status}"


def report_text(report: PerimeterReport) -> str:
    """The human's view of the report: identities, stops, unverified."""
    policy = (
        f"approval-policy: {report.policy_full_name} (id "
        f"{report.policy_repository_id}, default branch "
        f"{report.policy_default_branch})"
    )
    trust = (
        f"df-trust: {report.trust_full_name} (id {report.trust_repository_id}, "
        f"ветка {report.trust_branch}, голова {report.trust_head})"
    )
    lines = [
        policy,
        trust,
        f"проверяющий: {report.checker}; запросов {len(report.calls)}",
    ]
    lines += [f"СТОП {s.kind} ({s.role}): {s.detail}" for s in report.stops]
    lines += [f"не проверено: {u}" for u in report.unverified]
    return "\n".join(lines) + "\n"


def save_report(report: PerimeterReport, out: Path) -> str:
    """Write the report (the human's file, `0600`); its `sha256:` digest."""
    text = report.to_json()
    fsutil.write_atomic(out, text, 0o600)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- the write phase (root, under the writer lock) -------------------------------


@dataclass(frozen=True)
class Refused:
    """`verify_after_write` got no trust status it could accept."""

    reason: str


def load_report(path: Path, digest: str) -> PerimeterReport:
    """The report the human names, read by root as data (absolute, a regular
    file, never through a link, at most `REPORT_CAP`), whose sha256 must be
    `digest`, parsed strictly."""
    if not _DIGEST.fullmatch(digest):
        raise StepFailed(STEP, f"--report-digest {digest!r}: нужно sha256:<64 hex>")
    raw = _read_human_file(path, REPORT_CAP, "--report")
    got = "sha256:" + hashlib.sha256(raw).hexdigest()
    if got != digest:
        raise StepFailed(
            STEP,
            f"--report {path}: digest {got} ≠ --report-digest {digest}: отчёт не "
            "тот, что показала проверка; ничего не записано",
        )
    try:
        return parse_report(raw)
    except (ValueError, TypeError) as exc:
        raise StepFailed(STEP, f"--report {path}: {exc}") from exc


_REPORT_KEYS = frozenset({"schema", *PerimeterReport.__dataclass_fields__})


def parse_report(raw: bytes) -> PerimeterReport:
    """The report file, strictly: exactly its keys, each of its type."""
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or set(data) != _REPORT_KEYS:
        raise ValueError("не отчёт setup-trust: другой набор ключей")
    if data["schema"] != REPORT_SCHEMA:
        raise ValueError(f"schema {data['schema']!r}")
    for key in ("policy_repository_id", "trust_repository_id"):
        if type(data[key]) is not int:
            raise ValueError(f"{key}: не целое")
    for key in ("checked_at", "trust_branch"):
        if not isinstance(data[key], str):
            raise ValueError(f"{key}: не строка")
    for key in ("checker", "policy_full_name", "trust_full_name"):
        if data[key] is not None and not isinstance(data[key], str):
            raise ValueError(f"{key}: не строка")
    for key in ("policy_default_branch", "trust_head"):
        if data[key] is not None and not isinstance(data[key], str):
            raise ValueError(f"{key}: не строка")
    if type(data["rebind"]) is not bool:
        raise ValueError("rebind: не bool")
    if data["source"] is not None and not isinstance(data["source"], dict):
        raise ValueError("source: не объект")
    if not isinstance(data["facts"], dict):
        raise ValueError("facts: не объект")
    return PerimeterReport(
        checked_at=data["checked_at"],
        checker=data["checker"],
        rebind=data["rebind"],
        policy_repository_id=data["policy_repository_id"],
        trust_repository_id=data["trust_repository_id"],
        trust_branch=data["trust_branch"],
        policy_full_name=data["policy_full_name"],
        trust_full_name=data["trust_full_name"],
        policy_default_branch=data["policy_default_branch"],
        trust_head=data["trust_head"],
        source=data["source"],
        facts=data["facts"],
        calls=tuple(_call(c) for c in _list(data["calls"], "calls")),
        stops=tuple(_stop(s) for s in _list(data["stops"], "stops")),
        unverified=tuple(
            _str(u, "unverified") for u in _list(data["unverified"], "unverified")
        ),
    )


def _list(value: object, key: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{key}: не список")
    return value


def _str(value: object, key: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{key}: не строка")
    return value


def _call(value: object) -> tuple[str, int | None]:
    ok = isinstance(value, list) and len(value) == 2 and isinstance(value[0], str)
    if not ok or not (value[1] is None or type(value[1]) is int):  # type: ignore[index]
        raise ValueError("calls: не [путь, статус]")
    assert isinstance(value, list)
    return value[0], value[1]


def _stop(value: object) -> Stop:
    if not isinstance(value, dict) or set(value) != {"kind", "role", "detail"}:
        raise ValueError("stops: не {kind, role, detail}")
    kind, role, detail = (_str(value[k], "stops") for k in ("kind", "role", "detail"))
    if kind not in STOP_KINDS:
        raise ValueError(f"stops: неизвестный вид {kind!r}")
    return Stop(kind, role, detail)


def check_report(
    report: PerimeterReport,
    *,
    policy_repo: int,
    trust_repo: int,
    trust_branch: str,
    rebind: bool,
    now: datetime,
) -> None:
    """The report is of these arguments (ids, branch, `--rebind`), fresh
    (`REPORT_MAX_AGE`) and without a stop; else `StepFailed`."""
    asked = (policy_repo, trust_repo, trust_branch, rebind)
    found = (
        report.policy_repository_id,
        report.trust_repository_id,
        report.trust_branch,
        report.rebind,
    )
    if asked != found:
        raise StepFailed(
            STEP,
            f"отчёт проверки другого вызова: в отчёте {found}, аргументы {asked} "
            "(id политики, id df-trust, ветка, --rebind); повторите setup-trust check",
        )
    try:
        checked = datetime.fromisoformat(report.checked_at)
    except ValueError as exc:
        raise StepFailed(STEP, f"отчёт: checked_at {report.checked_at!r}") from exc
    if checked.tzinfo is None or not now - REPORT_MAX_AGE <= checked <= now:
        raise StepFailed(
            STEP, f"отчёт от {report.checked_at} устарел: повторите setup-trust check"
        )
    if report.stops or not report.ok:
        stops = "; ".join(f"{s.kind} ({s.role}): {s.detail}" for s in report.stops)
        raise StepFailed(STEP, f"проверка периметра не пройдена: {stops or 'нет имён'}")


def binding_from(report: PerimeterReport, layout: Layout) -> TrustBinding:
    """The binding: ids and branch from the arguments (equal to the report's),
    the canonical `df-trust` name GitHub returned for the id, the history
    profile of this layout; within the publisher's limits."""
    name = report.trust_full_name
    if name is None:
        raise StepFailed(STEP, "отчёт без имени df-trust")
    try:
        binding = TrustBinding(
            host=HOST,
            df_trust_repository_id=report.trust_repository_id,
            df_trust_full_name=name,
            df_trust_branch=report.trust_branch,
            approval_policy_repository_id=report.policy_repository_id,
            transport="public",
            history_gh_config_dir=str(layout.history_gh),
        )
    except ConfigError as exc:
        raise StepFailed(STEP, f"привязка: {exc}") from exc
    problem = binding_problem(binding)
    if problem is not None:
        raise StepFailed(STEP, f"привязка вне пределов издателя: {problem}")
    return binding


def check_rebind(layout: Layout, binding: TrustBinding, *, rebind: bool) -> None:
    """A changed root needs `--rebind` (spec 6.2 item 5); `--rebind` needs a
    binding to change. The same binding again is a repeat."""
    try:
        current = load_trust(layout.etc / "trust.toml")
    except ConfigError as exc:
        raise StepFailed(STEP, f"trust.toml не читается: {exc}") from exc
    if rebind and current is None:
        raise StepFailed(STEP, "--rebind без привязки: первая привязка — без --rebind")
    if not rebind and current is not None and current != binding:
        raise StepFailed(
            STEP,
            f"привязка уже есть (корень {current.root_id}): смена корня — "
            "darkfactory setup-trust check --rebind, затем write --rebind",
        )


def read_history_token(path: Path) -> str:
    """The history token file, read as data: one GitHub token line."""
    raw = _read_human_file(path, setup_agent.TOKEN_CAP + 1, "--history-token-file")
    token = raw.removesuffix(b"\n")
    if setup_agent.TOKEN_FORM.fullmatch(token) is None:
        raise StepFailed(
            STEP,
            f"--history-token-file {path}: не токен (одна строка github_pat_… или "
            "ghp_…); ничего не записано",
        )
    return token.decode("ascii")


def profile_files(token: str) -> dict[str, str]:
    """The history profile: `config.yml` (no migration) and `hosts.yml` with
    the token (the form `gh auth token` reads, devtools#693)."""
    hosts = (
        f"{HOST}:\n"
        "    users:\n"
        f"        {PROFILE_LOGIN}:\n"
        f"            oauth_token: {token}\n"
        "    git_protocol: https\n"
        f"    oauth_token: {token}\n"
        f"    user: {PROFILE_LOGIN}\n"
    )
    return {"config.yml": 'version: "1"\ngit_protocol: https\n', "hosts.yml": hosts}


def write_binding(layout: Layout, binding: TrustBinding, token_file: Path) -> None:
    """The write phase (root, the caller holds the writer lock): the history
    profile `/etc/df/history-gh` (`root:df-history`, `0750`, files `0640`),
    then `trust.toml`, inside one generation bracket (as `publisher.toml`).
    Everything that can be refused is refused before the first write."""
    token = read_history_token(token_file)
    if binding.history_gh_config_dir != str(layout.history_gh):
        raise StepFailed(STEP, f"history_gh_config_dir ≠ {layout.history_gh}")
    gid = plat.group_gid(HISTORY_GROUP)
    if gid is None:
        raise StepFailed(
            STEP, f"нет группы {HISTORY_GROUP}: выполните sudo darkfactory roles setup"
        )
    journal.generation_begin(layout)
    try:
        _write_profile(layout, token, gid)
        fsutil.write_atomic(layout.etc / "trust.toml", dump_trust(binding), TRUST_MODE)
    except OSError as exc:
        raise StepFailed(STEP, f"запись: {type(exc).__name__}: {exc}") from exc
    finally:  # a refusal leaves each file old or new, whole; a crash, odd
        journal.generation_end(layout)


def _write_profile(layout: Layout, token: str, gid: int) -> None:
    profile = layout.history_gh
    parent = os.open(layout.etc, _DIR_FLAGS)
    try:
        fsutil.make_dir(parent, profile.name, PROFILE_DIR_MODE, path=profile)
        fd = os.open(profile.name, _DIR_FLAGS, dir_fd=parent)
    finally:
        os.close(parent)
    try:
        _settle_profile_dir(fd, profile, gid)
        for name, text in profile_files(token).items():
            fsutil.write_under(
                fd,
                name,
                text.encode("ascii"),
                mode=PROFILE_FILE_MODE,
                uid=plat.ROOT_UID,
                gid=gid,
                path=profile / name,
            )
    finally:
        os.close(fd)


def _settle_profile_dir(fd: int, path: Path, gid: int) -> None:
    """The profile directory is root's at exactly `PROFILE_DIR_MODE`; its
    group becomes `df-history` (a found one of another owner or a wider mode
    is refused, never changed)."""
    st = os.fstat(fd)
    mode = stat.S_IMODE(st.st_mode)
    if fsutil.owner_uid(st) != plat.ROOT_UID or mode != PROFILE_DIR_MODE:
        raise StepFailed(
            STEP,
            f"{path}: владелец uid {fsutil.owner_uid(st)}, права {mode:04o}; нужен "
            f"root и {PROFILE_DIR_MODE:04o}: удалите каталог и повторите",
        )
    if st.st_gid != gid:
        os.fchown(fd, plat.ROOT_UID, gid)
        fsutil.sync_dir(fd)


def verify_after_write(layout: Layout) -> TrustStatus | Refused:
    """Root's `trust-status` after the write (spec 6.2 item 5): the binding
    root read itself from `trust.toml`, the publisher's answer parsed by
    `request.parse_trust_status` (schema, `result.root` = that binding)."""
    try:
        binding = load_trust(layout.etc / "trust.toml")
    except ConfigError as exc:
        return Refused(f"trust.toml: {exc}")
    if binding is None:
        return Refused("trust.toml нет")
    try:
        raw = trust_status_call(layout)
    except OSError as exc:
        return Refused(f"trust-status: нет ответа: {exc}")
    lines = raw.decode("utf-8", "replace").splitlines()
    if len(lines) != 1:
        return Refused(f"trust-status: нет ответа ({len(lines)} строк)")
    try:
        return request.parse_trust_status(lines[0], binding.root_id)
    except BadResponse as exc:
        return Refused(f"trust-status: {exc}")


def trust_status_call(layout: Layout) -> bytes:
    """`df-publish trust-status` from root (`sudo -n -u df-publisher`); its
    stdout. The tests' seam."""
    payload = {
        "schema": request.REQUEST_SCHEMA,
        "request_id": f"{STEP}-{uuid.uuid4().hex}",
    }
    data = json.dumps(payload, sort_keys=True).encode("utf-8")
    return perimeter_client.sudo_call(
        data, layout, operation=request.TRUST_STATUS, timeout=TRUST_STATUS_TIMEOUT
    )


def status_lines(status: TrustStatus | Refused) -> list[str]:
    """The human's view of `trust-status` (the epochs exit named)."""
    if isinstance(status, Refused):
        return [explain(f"trust-status: {status.reason}")]
    reason = "" if status.reason is None else f" ({status.reason})"
    lines = [
        explain(
            f"trust-status: {status.state}{reason}; снимок {status.snapshot_sha}, "
            f"эпоха {status.last_epoch}"
        )
    ]
    lines += [f"не проверено: {code}" for code in status.unverified]
    lines += [
        explain(f"находка {f['kind']} ({f['role']}): {f['detail']}")
        for f in status.findings
    ]
    return lines


def finished(status: TrustStatus | Refused) -> bool:
    """Only a `clear` root finishes the installation."""
    return isinstance(status, TrustStatus) and status.state == "clear"


def write_evidence(
    layout: Layout,
    report: PerimeterReport,
    digest: str,
    binding: TrustBinding,
    status: TrustStatus | Refused,
    *,
    now: datetime,
) -> Path:
    """`/var/lib/df/evidence/perimeter-<time>.json` (spec 6.2 «Evidence
    периметра»): the report (facts, time, checker, unverified premises), the
    bound identity, the head read, `activation_commit` and the trust status
    (SHA, epoch). A record of the check, not a source of authority."""
    source = report.source or {}
    data = {
        "schema": EVIDENCE_SCHEMA,
        "written_at": now.isoformat(timespec="seconds"),
        "binding": dump_trust(binding),
        "root": list(binding.root_id),
        "checker": report.checker,
        "trust_head": report.trust_head,
        "activation_commit": source.get("bootstrap_activation"),
        "report_digest": digest,
        "report": json.loads(report.to_json()),
        "trust_status": _status_record(status),
        "unverified": sorted({*report.unverified, *getattr(status, "unverified", ())}),
    }
    parent = os.open(layout.var, _DIR_FLAGS)
    try:
        fsutil.make_dir(
            parent,
            layout.evidence_dir.name,
            EVIDENCE_DIR_MODE,
            path=layout.evidence_dir,
        )
    finally:
        os.close(parent)
    stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    path = layout.evidence_dir / f"perimeter-{stamp}.json"
    text = json.dumps(data, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    fsutil.write_atomic(path, text, EVIDENCE_MODE)
    return path


def _status_record(status: TrustStatus | Refused) -> Mapping[str, object]:
    if isinstance(status, Refused):
        return {"refused": status.reason}
    return {
        "state": status.state,
        "reason": status.reason,
        "snapshot_sha": status.snapshot_sha,
        "last_epoch": status.last_epoch,
        "unverified": list(status.unverified),
        "findings": [dict(f) for f in status.findings],
    }


def run_write(
    layout: Layout,
    *,
    policy_repo: int,
    trust_repo: int,
    trust_branch: str,
    report_path: Path,
    report_digest: str,
    token_file: Path | None,
    rebind: bool,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> list[str]:
    """The whole write phase; the output lines. A refusal before the write is
    `StepFailed` (nothing written, not journalled); after the write the
    outcome is journalled and an unfinished one is `NeedsHuman`."""
    report = load_report(report_path, report_digest)
    check_report(
        report,
        policy_repo=policy_repo,
        trust_repo=trust_repo,
        trust_branch=trust_branch,
        rebind=rebind,
        now=now(),
    )
    binding = binding_from(report, layout)
    check_rebind(layout, binding, rebind=rebind)
    if token_file is None:
        raise StepFailed(STEP, NO_HISTORY_PROFILE)
    write_binding(layout, binding, token_file)
    status = verify_after_write(layout)
    evidence = write_evidence(layout, report, report_digest, binding, status, now=now())
    written = (
        f"записаны {layout.history_gh} и {layout.etc / 'trust.toml'} "
        f"(корень {binding.root_id})"
    )
    lines = [
        written,
        *status_lines(status),
        f"evidence: {evidence}",
    ]
    done = finished(status)
    journal.journal_event(
        layout.journal,
        run_id=uuid.uuid4().hex,
        step=STEP,
        event="done" if done else "needs-human",
        detail="; ".join(lines),
    )
    if not done:
        raise journal.NeedsHuman(
            f"{NOT_FINISHED}: " + "; ".join(lines) + "; отзовите PAT проверки "
            "после приёмки"
        )
    return [*lines, "установка доверия завершена; отзовите PAT проверки периметра"]


def _read_human_file(path: Path, cap: int, flag: str) -> bytes:
    """A file of the human's, read by root as data: absolute, a regular file
    (never a device, FIFO or link), at most `cap` bytes; never deleted."""
    if not path.is_absolute():
        raise StepFailed(STEP, f"{flag} {str(path)!r}: путь не абсолютный")
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise StepFailed(STEP, f"{flag} {path}: {exc.strerror or exc}") from exc
    if not stat.S_ISREG(st.st_mode):
        raise StepFailed(STEP, f"{flag} {path}: не обычный файл")
    try:
        data = inputs.read_capped(path, cap)
    except TreeError as exc:
        raise StepFailed(STEP, f"{flag}: {exc}") from exc
    if len(data) > cap:
        raise StepFailed(STEP, f"{flag} {path}: больше {cap} байт")
    return data


def run_check(
    *,
    policy_repo: int,
    trust_repo: int,
    trust_branch: str,
    out: Path,
    rebind: bool,
    token: str,
) -> tuple[PerimeterReport, str]:
    """The read phase with the PAT `token`: the report written to `out`
    and its digest."""
    report = check_perimeter(
        pat_transport(token),
        policy_repo,
        trust_repo,
        trust_branch=trust_branch,
        rebind=rebind,
    )
    return report, save_report(report, out)


def next_command(report: PerimeterReport, out: Path, digest: str) -> str:
    """The write command for this report (the human adds the token file)."""
    words = [
        "sudo darkfactory setup-trust write",
        f"--policy-repo-id {report.policy_repository_id}",
        f"--trust-repo-id {report.trust_repository_id}",
        f"--trust-branch {report.trust_branch}",
        f"--report {out}",
        f"--report-digest {digest}",
        "--history-token-file <файл токена истории>",
    ]
    if report.rebind:
        words.append("--rebind")
    return " ".join(words)
