"""Пинованный набор политик акта `review/v1` (спека §6, Q3, Q8).

Утверждающие, мержеры, режимы продуктов и классификация акторов читаются
из ОДНОГО коммита активации источника. Локальные копии политики акторов
(`contracts/steward-actor-policy/v1`, `steward/profiles/…`) этот модуль не
читает ни при каких условиях. `files_sha256` — контроль целостности
прочитанного набора, не идентичность версии.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import yaml

from governance import ssot_env

APPROVERS_FILE = "policy/approvers.env"
MODES_FILE = "policy/modes.yaml"
ACTORS_FILE = "policy/actors.yaml"
POLICY_FILES: tuple[str, ...] = (APPROVERS_FILE, MODES_FILE, ACTORS_FILE)
MODES_SCHEMA = "approval-policy-modes/v1"
MODES: tuple[str, ...] = ("merge", "review")


class PolicyError(ValueError):
    """Набор политик неполон или противоречив → отказ (`unavailable`)."""


@dataclass(frozen=True)
class PolicySet:
    """Политика на одном `activation_commit`."""

    activation_commit: str
    approvers: frozenset[str]
    mergers: frozenset[str]
    modes: Mapping[str, str]
    humans: frozenset[str]
    agents: frozenset[str]
    files_sha256: Mapping[str, str]


def _load_yaml(text: str, name: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PolicyError(f"{name}: не разбирается как YAML") from exc


def _accounts(text: str, key: str) -> frozenset[str]:
    lines = ssot_env.definition_lines(text, key)
    if len(lines) != 1:
        raise PolicyError(f"{APPROVERS_FILE}: {key} — ожидалась ровно одна строка")
    accounts = frozenset(p.strip() for p in lines[0].split(",") if p.strip())
    if not accounts:
        raise PolicyError(f"{APPROVERS_FILE}: {key} пуст")
    return accounts


def _modes(text: str) -> dict[str, str]:
    data = _load_yaml(text, MODES_FILE)
    if not isinstance(data, dict) or data.get("schema") != MODES_SCHEMA:
        raise PolicyError(f"{MODES_FILE}: схема не {MODES_SCHEMA}")
    modes = data.get("modes")
    if not isinstance(modes, dict) or any(
        not isinstance(k, str) or v not in MODES for k, v in modes.items()
    ):
        raise PolicyError(f"{MODES_FILE}: modes — отображение repo → merge|review")
    return dict(modes)


def _identities(data: dict[str, Any], field: str) -> frozenset[str]:
    value = data.get(field)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise PolicyError(f"{ACTORS_FILE}: {field} — список строк")
    return frozenset(value)


def _decode(files: Mapping[str, bytes]) -> dict[str, str]:
    try:
        return {name: files[name].decode("utf-8") for name in POLICY_FILES}
    except UnicodeDecodeError as exc:
        raise PolicyError("набор политик не в UTF-8") from exc


def parse_policy_set(activation_commit: str, files: Mapping[str, bytes]) -> PolicySet:
    """Разбор набора; любой отсутствующий файл или противоречие — `PolicyError`."""
    missing = [name for name in POLICY_FILES if name not in files]
    if missing:
        raise PolicyError(f"в {activation_commit} нет {', '.join(missing)}")
    text = _decode(files)
    actors = _load_yaml(text[ACTORS_FILE], ACTORS_FILE)
    if not isinstance(actors, dict) or actors.get("version") != 1:
        raise PolicyError(f"{ACTORS_FILE}: ожидалась version: 1")
    humans = _identities(actors, "human_identities")
    agents = _identities(actors, "agent_identities")
    if humans & agents:
        both = sorted(humans & agents)
        raise PolicyError(f"{ACTORS_FILE}: пересечение human/agent {both}")
    return PolicySet(
        activation_commit,
        _accounts(text[APPROVERS_FILE], "AUTHORIZED_APPROVER_ACCOUNTS"),
        _accounts(text[APPROVERS_FILE], "AUTHORIZED_MERGER_ACCOUNTS"),
        _modes(text[MODES_FILE]),
        humans,
        agents,
        {name: hashlib.sha256(files[name]).hexdigest() for name in POLICY_FILES},
    )


def _identity(login: str) -> str:
    return f"github:{login.removesuffix('[bot]')}"


def classify(ps: PolicySet, login: str, type_hint: str | None) -> str:
    """Класс актора по набору (steward `classify_actor`): human|agent|unknown."""
    from steward.gatecheck.approval import ApprovalPolicy, classify_actor

    policy = ApprovalPolicy(1, ps.humans, ps.agents)
    return classify_actor(_identity(login), type_hint, policy)


def mode_for(ps: PolicySet, product: str) -> str | None:
    """`ACT_MODE[product]`; записи нет — `None` (режима по умолчанию нет)."""
    return ps.modes.get(product)


def same_login(a: str, b: str) -> bool:
    """Логины равны с точностью до формы бота (`slug[bot]` ≡ `slug`)."""
    return a.removesuffix("[bot]") == b.removesuffix("[bot]")
