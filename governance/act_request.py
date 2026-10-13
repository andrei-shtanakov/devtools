"""Манифест заявки узла `review/v1` (спека §2, §7).

Заявка записана ВНУТРИ одобряемого коммита:
`workstreams/<ws-id>/approvals/requests/<request-id>.yaml`. Она —
единственный источник режима и пинов; `run.json`, тело PR и конверт —
зеркала, расхождение с манифестом — отказ. Неизвестная версия акта —
отказ до любого вычисления.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import yaml

SCHEMA = "node-approval-request/v1"
ACT_REVIEW_V1 = "review/v1"


class RequestError(ValueError):
    """Манифест не читается или не того вида → отказ без утверждения."""


@dataclass(frozen=True)
class PolicyPin:
    """Единый пин набора политик (§6)."""

    repo: str
    branch: str
    activation_commit: str
    files_sha256: Mapping[str, str]

    def as_record(self) -> dict[str, object]:
        """Форма для манифеста и леджера."""
        return {
            "repo": self.repo,
            "branch": self.branch,
            "activation_commit": self.activation_commit,
            "files_sha256": dict(self.files_sha256),
        }


@dataclass(frozen=True)
class NodeEntry:
    """Узел заявки: путь, хеш одобряемых байтов (`self_hash`), поколение."""

    path: str
    content_hash: str
    generation: int


@dataclass(frozen=True)
class NodeRequest:
    """Заявка узлов, как она лежит в одобренном коммите."""

    request_id: str
    ws_id: str
    product: str
    nodes: Mapping[str, NodeEntry]
    upstream_pins: Mapping[str, Mapping[str, str]]
    policy: PolicyPin


def manifest_path(ws_id: str, request_id: str) -> str:
    """Путь манифеста в репо продукта."""
    return f"workstreams/{ws_id}/approvals/requests/{request_id}.yaml"


def render_manifest(req: NodeRequest) -> str:
    """YAML манифеста; порядок ключей фиксирован ради стабильного диффа."""
    data = {
        "schema": SCHEMA,
        "act": ACT_REVIEW_V1,
        "request_id": req.request_id,
        "ws_id": req.ws_id,
        "product": req.product,
        "nodes": {
            node: {
                "path": e.path,
                "content_hash": e.content_hash,
                "generation": e.generation,
            }
            for node, e in sorted(req.nodes.items())
        },
        "upstream_pins": {n: dict(p) for n, p in sorted(req.upstream_pins.items())},
        "policy": req.policy.as_record(),
    }
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _node(name: str, raw: object) -> NodeEntry:
    if not isinstance(raw, dict):
        raise RequestError(f"узел {name}: неизвестная форма")
    path = raw.get("path")
    digest = raw.get("content_hash")
    gen = raw.get("generation")
    if not (isinstance(path, str) and path and isinstance(digest, str) and digest):
        raise RequestError(f"узел {name}: нет path/content_hash")
    # bool — подкласс int: `generation: true` обязан быть отказом (C11).
    if type(gen) is not int or gen < 1:
        raise RequestError(f"узел {name}: generation — целое ≥ 1")
    return NodeEntry(path, digest, gen)


def _pin(raw: object) -> PolicyPin:
    if not isinstance(raw, dict):
        raise RequestError("policy: нет пина")
    files = raw.get("files_sha256")
    repo = raw.get("repo")
    branch = raw.get("branch")
    commit = raw.get("activation_commit")
    if not (
        isinstance(repo, str)
        and repo
        and isinstance(branch, str)
        and branch
        and isinstance(commit, str)
        and commit
        and isinstance(files, dict)
    ):
        raise RequestError("policy: неполный пин")
    return PolicyPin(repo, branch, commit, dict(files))


def _identity(data: dict[object, object]) -> tuple[str, str, str]:
    ids = [data.get(k) for k in ("request_id", "ws_id", "product")]
    request_id, ws_id, product = ids
    if not (
        isinstance(request_id, str)
        and request_id
        and isinstance(ws_id, str)
        and ws_id
        and isinstance(product, str)
        and product
    ):
        raise RequestError("нет request_id/ws_id/product")
    return request_id, ws_id, product


def parse_manifest(text: str) -> NodeRequest:
    """Разбор манифеста; акт проверяется ПЕРВЫМ."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RequestError(f"манифест не читается: {exc}") from exc
    if not isinstance(data, dict):
        raise RequestError("манифест не отображение")
    if data.get("act") != ACT_REVIEW_V1:
        raise RequestError(f"неизвестная версия акта: {data.get('act')!r}")
    if data.get("schema") != SCHEMA:
        raise RequestError(f"схема манифеста не {SCHEMA}")
    nodes_raw = data.get("nodes")
    if not isinstance(nodes_raw, dict) or not nodes_raw:
        raise RequestError("в заявке нет ни одного узла")
    request_id, ws_id, product = _identity(data)
    pins = data.get("upstream_pins")
    if not isinstance(pins, dict) or set(pins) != set(nodes_raw):
        raise RequestError("upstream_pins обязан перечислять каждый узел заявки")
    if not all(isinstance(p, dict) for p in pins.values()):
        raise RequestError("upstream_pins: значение узла — отображение")
    return NodeRequest(
        request_id,
        ws_id,
        product,
        nodes={str(n): _node(str(n), raw) for n, raw in nodes_raw.items()},
        upstream_pins={str(n): dict(p) for n, p in pins.items()},
        policy=_pin(data.get("policy")),
    )


def ledger_view(req: NodeRequest) -> dict[str, object]:
    """Поля заявки в `run.json` — зеркало манифеста."""
    return {
        "act": ACT_REVIEW_V1,
        "nodes": sorted(req.nodes),
        "content_hashes": {n: e.content_hash for n, e in req.nodes.items()},
        "generations": {n: e.generation for n, e in req.nodes.items()},
        "upstream_pins": {n: dict(p) for n, p in req.upstream_pins.items()},
        "policy": req.policy.as_record(),
    }


def _body_values(pr_body: str, key: str) -> list[str]:
    return [
        line.split(":", 1)[1].strip()
        for line in pr_body.splitlines()
        if ":" in line and line.split(":", 1)[0].strip() == key
    ]


def mirror_mismatch(
    req: NodeRequest,
    request_key: str,
    ledger_op: Mapping[str, object],
    pr_body: str,
) -> str | None:
    """Первое расхождение зеркал (`run.json`, тело PR) с манифестом либо `None`."""
    if request_key != req.request_id:
        return f"ключ заявки {request_key} ≠ request_id манифеста {req.request_id}"
    for field, value in ledger_view(req).items():
        got = ledger_op.get(field)
        if field == "nodes":
            # Агентские данные: сортировать можно только проверенные строки.
            if not isinstance(got, list) or not all(isinstance(n, str) for n in got):
                return "run.json: nodes расходится с манифестом"
            got = sorted(got)
        if got != value:
            return f"run.json: {field} расходится с манифестом"
    expected = {
        "act": ACT_REVIEW_V1,
        "policy-activation": req.policy.activation_commit,
    }
    for key, value in expected.items():
        found = _body_values(pr_body, key)
        if found != [value]:
            return (
                f"тело PR: {key} — ожидалась ровно одна строка {value!r}, "
                f"есть {found!r}"
            )
    return None
