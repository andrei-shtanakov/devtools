"""Model choice per role: `/etc/df/models.toml` (spec 3.6).

The file holds the common choice of each model role (`[roles.<role>]`) and
the human's overrides for registered products
(`[products."<repository_id>".roles.<role>]`). Readers parse the snapshot's
text (`parse_models`); only the writer `models set` reads under its lock.
Environment and flags never override the choice (rev 8: an explicit
replacement of the former CLI > env > product > machine order).
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from df.config import KNOWN_SCHEMAS, TOML_ERRORS, ConfigError, read_optional
from df.toml_emit import dumps

ROLES: tuple[str, ...] = (
    "interview",
    "author",
    "reviewer",
    "executor-review",
    "edge-check",
    "judge",
)
#: The OS context of each role (decision 3): the one table probes and the
#: stage-2 launchers import.
ROLE_CONTEXT: Mapping[str, str] = MappingProxyType(
    {
        "interview": "df-exec",
        "author": "df-exec",
        "reviewer": "df-review",
        "executor-review": "df-review",
        "edge-check": "df-review",
        "judge": "df-review",
    }
)
HARNESSES: tuple[str, ...] = ("claude", "codex")
#: Parameters the trusted adapter (Task 11) can pass, per harness; any other
#: is a schema refusal, so a probe never checks another configuration.
SUPPORTED_PARAMS: Mapping[str, frozenset[str]] = MappingProxyType(
    {"claude": frozenset(), "codex": frozenset()}
)
COMMON = "общий"

_SCHEMA = KNOWN_SCHEMAS["models.toml"]
_PRODUCT_ID = re.compile(r"[1-9][0-9]*", re.ASCII)
_CHOICE_KEYS = frozenset({"harness", "model", "effort", "params"})


@dataclass(frozen=True)
class ModelChoice:
    """One role's harness, model, effort and significant parameters."""

    harness: str
    model: str
    effort: str | None
    params: Mapping[str, str]

    def __post_init__(self) -> None:
        _need(self.harness in HARNESSES, "harness", f"unknown {self.harness!r}")
        _need(_text(self.model), "model", "must be a non-empty string")
        if self.effort is not None:
            _need(_text(self.effort), "effort", "must be a non-empty string")
        supported = SUPPORTED_PARAMS[self.harness]
        for key, value in self.params.items():
            _need(
                key in supported,
                f"params.{key}",
                f"the {self.harness} adapter cannot pass it",
            )
            _need(isinstance(value, str), f"params.{key}", "must be a string")


@dataclass(frozen=True)
class ModelsConfig:
    """`models.toml`: the common choices and the per-product overrides
    (keyed by `repository_id`)."""

    default: Mapping[str, ModelChoice]
    products: Mapping[int, Mapping[str, ModelChoice]]


EMPTY = ModelsConfig({}, {})


def parse_models(text: str) -> ModelsConfig:
    """Strictly parse the text of `models.toml` (schema `df/models/v1`)."""
    try:
        data = tomllib.loads(text)
    except TOML_ERRORS as exc:
        raise ConfigError(f"schema {_SCHEMA}: invalid TOML: {exc}") from exc
    _need(data.get("schema") == _SCHEMA, "schema", f"expected {_SCHEMA!r}")
    _only(data, {"schema", "roles", "products"}, "models.toml")
    products = _table(data.get("products", {}), "products")
    return ModelsConfig(
        _roles(data.get("roles", {}), "roles"),
        {_product_id(k): _product(v, k) for k, v in products.items()},
    )


def load_models(path: Path) -> ModelsConfig:
    """Read `path` through the hardened reader and parse it (the writer)."""
    text = read_optional(path)
    if text is None:
        raise ConfigError(f"{path}: cannot read: file does not exist")
    return parse_models(text)


def dump_models(cfg: ModelsConfig) -> str:
    """Render `models.toml` deterministically (roles in `ROLES` order)."""
    data: dict[str, object] = {"schema": _SCHEMA}
    if cfg.default:
        data["roles"] = _dump_roles(cfg.default)
    if cfg.products:
        data["products"] = {
            str(pid): {"roles": _dump_roles(cfg.products[pid])}
            for pid in sorted(cfg.products)
        }
    return dumps(data)


def with_choice(
    cfg: ModelsConfig, role: str, choice: ModelChoice, product: int | None = None
) -> ModelsConfig:
    """`cfg` with `role`'s choice replaced (common, or `product`'s)."""
    _role(role)
    if product is None:
        return ModelsConfig({**cfg.default, role: choice}, cfg.products)
    roles = {**cfg.products.get(product, {}), role: choice}
    return ModelsConfig(cfg.default, {**cfg.products, product: roles})


def resolve(
    cfg: ModelsConfig, role: str, product: int | None
) -> tuple[ModelChoice, str]:
    """`(choice, source)`: the product's override wins (`продукт <id>`),
    else the common choice (`общий`); a role without one is `ConfigError`."""
    _role(role)
    override = cfg.products.get(product, {}) if product is not None else {}
    if role in override:
        return override[role], f"продукт {product}"
    if role in cfg.default:
        return cfg.default[role], COMMON
    raise ConfigError(f"{role}: модель не задана")


def config_hash(choice: ModelChoice) -> str:
    """SHA-256 of the choice's canonical JSON (sorted keys)."""
    canonical = json.dumps(
        {
            "harness": choice.harness,
            "model": choice.model,
            "effort": choice.effort,
            "params": dict(choice.params),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _product(value: object, key: str) -> dict[str, ModelChoice]:
    where = f"products.{key}"
    table = _table(value, where)
    _only(table, {"roles"}, where)
    return _roles(table.get("roles", {}), f"{where}.roles")


def _product_id(key: str) -> int:
    _need(bool(_PRODUCT_ID.fullmatch(key)), f"products.{key}", "not a repository id")
    return int(key)


def _roles(value: object, where: str) -> dict[str, ModelChoice]:
    table = _table(value, where)
    return {_role(r, where): _choice(v, f"{where}.{r}") for r, v in table.items()}


def _role(role: str, where: str = "role") -> str:
    _need(role in ROLES, f"{where}.{role}", "unknown role")
    return role


def _choice(value: object, where: str) -> ModelChoice:
    row = _table(value, where)
    _only(row, _CHOICE_KEYS, where)
    for key in ("harness", "model"):
        _need(key in row, where, f"missing key {key!r}")
    for key in ("harness", "model", "effort"):
        if key in row:
            _need(isinstance(row[key], str), f"{where}.{key}", "must be a string")
    params = _table(row.get("params", {}), f"{where}.params")
    try:
        return ModelChoice(
            row["harness"], row["model"], row.get("effort"), dict(params)
        )
    except ConfigError as exc:
        raise ConfigError(f"{where}: {exc}") from exc


def _dump_roles(roles: Mapping[str, ModelChoice]) -> dict[str, object]:
    return {r: _dump_choice(roles[r]) for r in ROLES if r in roles}


def _dump_choice(choice: ModelChoice) -> dict[str, object]:
    row: dict[str, object] = {"harness": choice.harness, "model": choice.model}
    if choice.effort is not None:
        row["effort"] = choice.effort
    if choice.params:
        row["params"] = {k: choice.params[k] for k in sorted(choice.params)}
    return row


def _table(value: object, where: str) -> dict[str, object]:
    _need(isinstance(value, dict), where, "must be a table")
    assert isinstance(value, dict)
    return value


def _only(
    data: Mapping[str, object], keys: set[str] | frozenset[str], where: str
) -> None:
    for key in sorted(set(data) - keys):
        raise ConfigError(f"{where}: unknown key {key!r}")


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _need(ok: bool, key: str, what: str) -> None:
    if not ok:
        raise ConfigError(f"{key}: {what}")
