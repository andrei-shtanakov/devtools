"""Stage-2 configuration and the run record (spec 3.7, 5.2, 5.8).

`publisher.toml` (UID -> allowed operations), `products.toml`, `trust.toml`
and the JSON run record `/var/lib/df/runs/<run_id>.json`. Parsed as strictly
as `df.config`: an unknown schema, key or value is a `ConfigError`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from df.config import (
    KNOWN_SCHEMAS,
    STATE_SCHEMAS,
    Account,
    ConfigError,
    _check_keys,
    _need,
    _nonempty,
    _parse_toml,
    read_optional,
)
from df.models import _WORD, _word
from df.roles import ROLE_USERS
from df.toml_emit import dumps

OPERATIONS: tuple[str, ...] = (
    "publish-review",
    "read-token",
    "merge",
    "record-violation",
    "perimeter-status",
)
DEFAULT_API_BASE = "https://api.github.com"
_LOOPBACK_API = re.compile(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})", re.ASCII)
_MAX_PORT = 65535
#: Spec 5.2: what each model role's UID may ask the publisher for.
_ROLE_OPERATIONS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "df-exec": frozenset({"merge", "record-violation", "perimeter-status"}),
        "df-review": frozenset(
            {"publish-review", "read-token", "record-violation", "perimeter-status"}
        ),
    }
)
_PUBLISHER = KNOWN_SCHEMAS["publisher.toml"]
_PRODUCTS = KNOWN_SCHEMAS["products.toml"]
_TRUST = KNOWN_SCHEMAS["trust.toml"]
_RUN = STATE_SCHEMAS["runs/<id>.json"]
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}", re.ASCII)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _rows(data: Mapping[str, object], key: str) -> list[dict[str, object]]:
    rows = data.get(key, [])
    _need(
        isinstance(rows, list) and all(isinstance(r, dict) for r in rows),
        key,
        "must be an array of tables",
    )
    assert isinstance(rows, list)
    return rows


# --- publisher.toml ---------------------------------------------------------


@dataclass(frozen=True)
class PublisherConfig:
    """`/etc/df/publisher.toml`: sender UID -> operations, and the API base."""

    senders: Mapping[int, frozenset[str]]
    api_base: str = DEFAULT_API_BASE

    def __post_init__(self) -> None:
        _check_api_base(self.api_base)
        for uid, ops in self.senders.items():
            _need(_is_int(uid) and uid >= 0, "uid", "must be a non-negative integer")
            _need(bool(ops), f"uid {uid}", "operations must not be empty")
            unknown = sorted(set(ops) - set(OPERATIONS))
            _need(not unknown, f"uid {uid}", f"unknown operation {unknown[:1]}")
        object.__setattr__(
            self,
            "senders",
            MappingProxyType({u: frozenset(o) for u, o in self.senders.items()}),
        )


def _check_api_base(value: object) -> None:
    _need(isinstance(value, str), "api_base", "must be a string")
    assert isinstance(value, str)
    if value == DEFAULT_API_BASE:
        return
    found = _LOOPBACK_API.fullmatch(value)
    ok = found is not None and int(found[1]) <= _MAX_PORT
    _need(
        ok,
        "api_base",
        f"must be {DEFAULT_API_BASE!r} or http://127.0.0.1:<port>, got {value!r}",
    )


def expected_publisher(accounts: Mapping[str, Account]) -> PublisherConfig:
    """The table of spec 5.2 for the model roles.

    Refuses (`ConfigError`) unless every role of `ROLE_USERS` is present and
    `ready` and the three UIDs are distinct: a shared UID would silently
    merge two roles' rights, and the publisher is never a sender.
    """
    for name in ROLE_USERS:
        _need(name in accounts, name, "role account is missing")
        _need(accounts[name].state == "ready", name, "role account is not ready")
    by_uid: dict[int, str] = {}
    for name in ROLE_USERS:
        uid = accounts[name].uid
        _need(uid not in by_uid, name, f"uid {uid} is shared with {by_uid.get(uid)}")
        by_uid[uid] = name
    senders = {accounts[name].uid: ops for name, ops in _ROLE_OPERATIONS.items()}
    return PublisherConfig(senders, DEFAULT_API_BASE)


def parse_publisher(text: str) -> PublisherConfig:
    """Strictly parse the text of `publisher.toml`."""
    data = _parse_toml(text, _PUBLISHER)
    _check_keys(data, set(), {"api_base", "sender"}, "publisher.toml")
    senders: dict[int, frozenset[str]] = {}
    for row in _rows(data, "sender"):
        uid, ops = _parse_sender(row)
        _need(uid not in senders, "sender", f"duplicate uid {uid}")
        senders[uid] = ops
    base = data.get("api_base", DEFAULT_API_BASE)
    _check_api_base(base)
    assert isinstance(base, str)
    return PublisherConfig(senders, base)


def _parse_sender(row: dict[str, object]) -> tuple[int, frozenset[str]]:
    _check_keys(row, {"uid", "operations"}, set(), "sender", allow_schema=False)
    uid, ops = row["uid"], row["operations"]
    _need(_is_int(uid), "uid", "must be an integer")
    _need(
        isinstance(ops, list) and all(isinstance(o, str) for o in ops),
        "operations",
        "must be a list of strings",
    )
    assert isinstance(uid, int) and isinstance(ops, list)
    _need(uid >= 0, "uid", "must be non-negative")
    _need(bool(ops), "operations", "must not be empty")
    _need(len(set(ops)) == len(ops), "operations", "duplicate operation")
    unknown = sorted(set(ops) - set(OPERATIONS))
    _need(not unknown, "operations", f"unknown operation {unknown[:1]}")
    return uid, frozenset(ops)


def dump_publisher(cfg: PublisherConfig) -> str:
    """Render `publisher.toml`: senders by UID, operations sorted."""
    data: dict[str, object] = {"schema": _PUBLISHER}
    if cfg.api_base != DEFAULT_API_BASE:
        data["api_base"] = cfg.api_base
    rows = [
        {"uid": uid, "operations": sorted(cfg.senders[uid])}
        for uid in sorted(cfg.senders)
    ]
    if rows:
        data["sender"] = rows
    return dumps(data)


# --- products.toml ----------------------------------------------------------

_PRODUCT_KEYS = {
    "repository_id",
    "full_name",
    "profile",
    "product_app_id",
    "product_installation_id",
    "machine_user",
    "checkout",
}


@dataclass(frozen=True)
class Product:
    """One registered product (spec 3.7)."""

    repository_id: int
    full_name: str
    profile: Literal["fork", "write"]
    app_id: int
    installation_id: int
    machine_user: str
    checkout: str

    def __post_init__(self) -> None:
        for key in ("repository_id", "app_id", "installation_id"):
            value = getattr(self, key)
            _need(_is_int(value) and value > 0, key, "must be a positive integer")
        _nonempty(self.full_name, "full_name")
        _need(self.profile in ("fork", "write"), "profile", "must be fork or write")
        _nonempty(self.machine_user, "machine_user")
        _nonempty(self.checkout, "checkout")


def parse_products(text: str) -> dict[int, Product]:
    """Parse the text of `products.toml`; keys are `repository_id`."""
    data = _parse_toml(text, _PRODUCTS)
    _check_keys(data, set(), {"product"}, "products.toml")
    result: dict[int, Product] = {}
    for row in _rows(data, "product"):
        prod = _parse_product(row)
        _need(
            prod.repository_id not in result,
            "product",
            f"duplicate repository_id {prod.repository_id}",
        )
        result[prod.repository_id] = prod
    return result


def _parse_product(row: dict[str, object]) -> Product:
    _check_keys(row, _PRODUCT_KEYS, set(), "product", allow_schema=False)
    return Product(
        row["repository_id"],  # type: ignore[arg-type]
        row["full_name"],  # type: ignore[arg-type]
        row["profile"],  # type: ignore[arg-type]
        row["product_app_id"],  # type: ignore[arg-type]
        row["product_installation_id"],  # type: ignore[arg-type]
        row["machine_user"],  # type: ignore[arg-type]
        row["checkout"],  # type: ignore[arg-type]
    )


def load_products(path: Path) -> dict[int, Product]:
    """Read `products.toml` through the hardened reader; absent -> empty."""
    text = read_optional(path)
    return {} if text is None else parse_products(text)


def dump_products(products: Mapping[int, Product]) -> str:
    """Render `products.toml` sorted by `repository_id`."""
    rows = [
        {
            "repository_id": p.repository_id,
            "full_name": p.full_name,
            "profile": p.profile,
            "product_app_id": p.app_id,
            "product_installation_id": p.installation_id,
            "machine_user": p.machine_user,
            "checkout": p.checkout,
        }
        for _, p in sorted(products.items())
    ]
    data: dict[str, object] = {"schema": _PRODUCTS}
    if rows:
        data["product"] = rows
    return dumps(data)


# --- trust.toml -------------------------------------------------------------

_TRUST_KEYS = {
    "host",
    "df_trust_repository_id",
    "df_trust_full_name",
    "df_trust_branch",
    "approval_policy_repository_id",
    "transport",
}


@dataclass(frozen=True)
class TrustBinding:
    """The binding of the root of trust (spec A rev 16 section 6.1)."""

    host: str
    df_trust_repository_id: int
    df_trust_full_name: str
    df_trust_branch: str
    approval_policy_repository_id: int
    transport: Literal["public"]

    def __post_init__(self) -> None:
        _nonempty(self.host, "host")
        _nonempty(self.df_trust_full_name, "df_trust_full_name")
        _nonempty(self.df_trust_branch, "df_trust_branch")
        for key in ("df_trust_repository_id", "approval_policy_repository_id"):
            value = getattr(self, key)
            _need(_is_int(value) and value > 0, key, "must be a positive integer")
        _need(self.transport == "public", "transport", "only 'public' in v1")

    @property
    def root_id(self) -> tuple[str, int, str]:
        """The identity of the root: host, repository id and branch."""
        return (self.host, self.df_trust_repository_id, self.df_trust_branch)


def parse_trust(text: str) -> TrustBinding:
    """Parse the text of `trust.toml`."""
    data = _parse_toml(text, _TRUST)
    _check_keys(data, _TRUST_KEYS, set(), "trust.toml")
    return TrustBinding(**{k: data[k] for k in _TRUST_KEYS})  # type: ignore[arg-type]


def load_trust(path: Path) -> TrustBinding | None:
    """Read `trust.toml` through the hardened reader; absent -> None."""
    text = read_optional(path)
    return None if text is None else parse_trust(text)


# --- run record -------------------------------------------------------------

_PIN_STR = ("harness", "harness_version", "model", "actual_model", "config_hash")
_RUN_KEYS = {
    "run_id",
    "release_id",
    "release_digest",
    "admin_digest",
    "product",
    "models",
    "root_id",
    "started_at",
    "started_by_uid",
}


@dataclass(frozen=True)
class ModelPin:
    """The model facts pinned for one role at run start."""

    harness: str
    harness_version: str
    model: str
    actual_model: str
    effort: str | None
    params: Mapping[str, str]
    config_hash: str

    def __post_init__(self) -> None:
        for key in _PIN_STR:
            _nonempty(getattr(self, key), key)
        if self.effort is not None:
            _need(_word(self.effort), "effort", _WORD)
        _need(
            all(
                isinstance(k, str) and isinstance(v, str)
                for k, v in self.params.items()
            ),
            "params",
            "must map strings to strings",
        )
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))

    def __hash__(self) -> int:
        params = tuple(sorted(self.params.items()))
        return hash((*(getattr(self, k) for k in _PIN_STR), self.effort, params))


@dataclass(frozen=True)
class RunRecord:
    """`/var/lib/df/runs/<run_id>.json` (spec 5.8)."""

    run_id: str
    release_id: str
    release_digest: str
    admin_digest: str
    product: int
    models: Mapping[str, ModelPin]
    root_id: tuple[str, int, str] | None
    started_at: str
    started_by_uid: int

    def __post_init__(self) -> None:
        for key in ("run_id", "release_id"):
            _nonempty(getattr(self, key), key)
        for key in ("release_digest", "admin_digest"):
            value = getattr(self, key)
            ok = isinstance(value, str) and bool(_DIGEST.fullmatch(value))
            _need(ok, key, "must be sha256:<64 lowercase hex>")
        _check_timestamp(self.started_at)
        for role, pin in self.models.items():
            _need(bool(role) and isinstance(role, str), "models", "bad role name")
            _need(isinstance(pin, ModelPin), f"models.{role}", "must be a ModelPin")
        _need(_is_int(self.product) and self.product > 0, "product", "must be positive")
        _need(
            _is_int(self.started_by_uid) and self.started_by_uid >= 0,
            "started_by_uid",
            "must be a non-negative integer",
        )
        _check_root_id(self.root_id)
        object.__setattr__(self, "models", MappingProxyType(dict(self.models)))


def _check_timestamp(value: object) -> None:
    """An ISO 8601 instant with an offset, like the journal's `ts`."""
    _nonempty(value, "started_at")
    assert isinstance(value, str)
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError(f"started_at: not an ISO 8601 time: {exc}") from exc
    _need(moment.tzinfo is not None, "started_at", "must carry a UTC offset")


def _check_root_id(root_id: object) -> None:
    if root_id is None:
        return
    ok = (
        isinstance(root_id, tuple)
        and len(root_id) == 3
        and isinstance(root_id[0], str)
        and bool(root_id[0])
        and _is_int(root_id[1])
        and root_id[1] > 0
        and isinstance(root_id[2], str)
        and bool(root_id[2])
    )
    _need(ok, "root_id", "must be null or [host, repository id > 0, branch]")


def dump_run(rec: RunRecord) -> str:
    """Render the run record as canonical JSON (sorted keys, no spaces)."""
    data: dict[str, object] = {
        "schema": _RUN,
        "run_id": rec.run_id,
        "release_id": rec.release_id,
        "release_digest": rec.release_digest,
        "admin_digest": rec.admin_digest,
        "product": rec.product,
        "models": {role: _pin_data(p) for role, p in rec.models.items()},
        "root_id": None if rec.root_id is None else list(rec.root_id),
        "started_at": rec.started_at,
        "started_by_uid": rec.started_by_uid,
    }
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _pin_data(pin: ModelPin) -> dict[str, object]:
    return {
        **{k: getattr(pin, k) for k in _PIN_STR},
        "effort": pin.effort,
        "params": dict(pin.params),
    }


def parse_run(text: str) -> RunRecord:
    """Strictly parse a run record; any deviation is a `ConfigError`."""
    try:
        data = json.loads(
            text, parse_constant=_no_constant, object_pairs_hook=_no_duplicates
        )
    except (ValueError, RecursionError) as exc:
        raise ConfigError(f"schema {_RUN}: invalid JSON: {exc}") from exc
    _need(isinstance(data, dict), "run", "must be a JSON object")
    _need(data.get("schema") == _RUN, "schema", f"expected {_RUN!r}")
    _check_keys(data, _RUN_KEYS, set(), "run")
    models = data["models"]
    _need(isinstance(models, dict), "models", "must be an object")
    return RunRecord(
        run_id=data["run_id"],
        release_id=data["release_id"],
        release_digest=data["release_digest"],
        admin_digest=data["admin_digest"],
        product=data["product"],
        models={str(r): _parse_pin(p) for r, p in models.items()},
        root_id=_parse_root_id(data["root_id"]),
        started_at=data["started_at"],
        started_by_uid=data["started_by_uid"],
    )


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _no_constant(name: str) -> object:
    raise ValueError(f"constant {name} is not allowed")


def _parse_root_id(value: object) -> tuple[str, int, str] | None:
    if value is None:
        return None
    _need(isinstance(value, list), "root_id", "must be null or a list")
    assert isinstance(value, list)
    return tuple(value)  # type: ignore[return-value]  # checked by RunRecord


def _parse_pin(value: object) -> ModelPin:
    _need(isinstance(value, dict), "models", "each pin must be an object")
    assert isinstance(value, dict)
    _check_keys(
        value, {*_PIN_STR, "effort", "params"}, set(), "model pin", allow_schema=False
    )
    params = value["params"]
    _need(isinstance(params, dict), "params", "must be an object")
    return ModelPin(
        **{k: value[k] for k in _PIN_STR}, effort=value["effort"], params=params
    )


# --- model-change events ----------------------------------------------------

_CHANGE_KEYS = {"event", "run_id", "role", "before", "after", "from_step"}
_CHANGE_EVENT = "model_change"


@dataclass(frozen=True)
class ModelChange:
    """One accepted change of a role's pin in `runs/<run_id>.events.jsonl`.

    `before` and `after` are pins in the canonical form of the run record;
    `from_step` is the first step (a non-negative index) that runs on `after`.
    """

    run_id: str
    role: str
    before: ModelPin
    after: ModelPin
    from_step: int

    def __post_init__(self) -> None:
        _nonempty(self.run_id, "run_id")
        _nonempty(self.role, "role")
        for key in ("before", "after"):
            _need(isinstance(getattr(self, key), ModelPin), key, "must be a ModelPin")
        ok = _is_int(self.from_step) and self.from_step >= 0
        _need(ok, "from_step", "must be a non-negative integer")


def dump_model_change(ev: ModelChange) -> str:
    """Render one event line as canonical JSON (sorted keys, no spaces)."""
    data: dict[str, object] = {
        "event": _CHANGE_EVENT,
        "run_id": ev.run_id,
        "role": ev.role,
        "before": _pin_data(ev.before),
        "after": _pin_data(ev.after),
        "from_step": ev.from_step,
    }
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def parse_model_change(line: str) -> ModelChange:
    """Strictly parse one event line; any deviation is a `ConfigError`."""
    try:
        data = json.loads(
            line, parse_constant=_no_constant, object_pairs_hook=_no_duplicates
        )
    except (ValueError, RecursionError) as exc:
        raise ConfigError(f"model_change: invalid JSON: {exc}") from exc
    _need(isinstance(data, dict), "model_change", "must be a JSON object")
    _check_keys(data, _CHANGE_KEYS, set(), "model_change", allow_schema=False)
    _need(data["event"] == _CHANGE_EVENT, "event", f"expected {_CHANGE_EVENT!r}")
    return ModelChange(
        run_id=data["run_id"],
        role=data["role"],
        before=_parse_pin(data["before"]),
        after=_parse_pin(data["after"]),
        from_step=data["from_step"],
    )


def effective_pins(rec: RunRecord, events: Iterable[str]) -> dict[str, ModelPin]:
    """The pins in force: the record's, then each event line in file order.

    Fail closed: a malformed line, another run's event, an unknown role or a
    `before` that is not the pin in force is a `ConfigError`, never skipped.
    """
    pins = dict(rec.models)
    for line in events:
        ev = parse_model_change(line)
        _need(ev.run_id == rec.run_id, "model_change", "event of another run")
        _need(ev.role in pins, "model_change", f"no pin for role {ev.role!r}")
        _need(
            pins[ev.role] == ev.before,
            "model_change",
            f"before is not the pin in force for {ev.role!r}",
        )
        pins[ev.role] = ev.after
    return pins
