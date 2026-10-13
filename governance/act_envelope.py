"""Конверт утверждения узла `review/v1` и история поколений (спека §9.1 (а), Q5, Q1).

Конверт `workstreams/<ws-id>/approvals/<node>.yaml` лежит вне путей
CODEOWNERS: его пишет агентский finalize. Он НЕ источник полномочий:
режим, продукт, узел и поколение устанавливаются через историю доставок
базовой ветки (заявки и записи остановки); конверт лишь обязан совпасть с
действующим событием. Отсутствующий или несовпадающий конверт — «не
одобрен», без fallback на frontmatter.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass

import yaml

from governance.act_request import NodeRequest, manifest_path

ENVELOPE_SCHEMA = "node-approval-envelope/v1"
STOP_SCHEMA = "node-approval-stop/v1"


class EnvelopeError(ValueError):
    """Конверт или запись остановки не читается → «не одобрен»."""


@dataclass(frozen=True)
class Envelope:
    """Идентичность одобрения узла (§9.1 п. 1) с единым пином политики."""

    node: str
    request_id: str
    generation: int
    manifest_path: str
    head_sha: str
    self_hash: str
    upstream_pins: Mapping[str, str]
    merge_commit: str
    review_id: str
    policy: Mapping[str, object]


@dataclass(frozen=True)
class StopRecord:
    """Явная остановка дальнейшего использования одобрения поколения."""

    node: str
    generation: int
    reason: str


@dataclass(frozen=True)
class DeliveredRequest:
    """Доставленный candidate: merge-коммит, одобренный head и его манифест."""

    merge_commit: str
    head_sha: str
    request: NodeRequest


@dataclass(frozen=True)
class DeliveredStop:
    """Доставленная запись остановки."""

    merge_commit: str
    stop: StopRecord


Event = DeliveredRequest | DeliveredStop
History = Sequence[Event]
"""События first-parent базы, старые первыми."""


def envelope_path(ws_id: str, node: str) -> str:
    """Путь конверта в репо продукта."""
    return f"workstreams/{ws_id}/approvals/{node}.yaml"


def stop_path(ws_id: str, node: str, generation: int) -> str:
    """Путь записи остановки."""
    return f"workstreams/{ws_id}/approvals/stops/{node}-g{generation}.yaml"


def _dump(schema: str, data: dict[str, object]) -> str:
    return yaml.safe_dump(
        {"schema": schema, **data}, sort_keys=False, allow_unicode=True
    )


def _load(text: str, schema: str) -> dict[str, object]:
    data = yaml.safe_load(text)
    if not isinstance(data, dict) or data.pop("schema", None) != schema:
        raise EnvelopeError(f"схема не {schema}")
    return data


def render_envelope(env: Envelope) -> str:
    """YAML конверта."""
    data = asdict(env)
    data["upstream_pins"] = dict(env.upstream_pins)
    data["policy"] = dict(env.policy)
    return _dump(ENVELOPE_SCHEMA, data)


def parse_envelope(text: str) -> Envelope:
    """Разбор конверта; неизвестная форма — `EnvelopeError`."""
    try:
        return Envelope(**_load(text, ENVELOPE_SCHEMA))  # pyrefly: ignore
    except TypeError as exc:
        raise EnvelopeError(f"конверт: {exc}") from exc


def render_stop(rec: StopRecord) -> str:
    """YAML записи остановки."""
    return _dump(STOP_SCHEMA, asdict(rec))


def parse_stop(text: str) -> StopRecord:
    """Разбор записи остановки."""
    try:
        return StopRecord(**_load(text, STOP_SCHEMA))  # pyrefly: ignore
    except TypeError as exc:
        raise EnvelopeError(f"запись остановки: {exc}") from exc


def _touches(event: Event, node: str) -> bool:
    if isinstance(event, DeliveredStop):
        return event.stop.node == node
    return node in event.request.nodes


def _generation(event: Event, node: str) -> int:
    if isinstance(event, DeliveredStop):
        return event.stop.generation
    return event.request.nodes[node].generation


def current_event(history: History, node: str) -> Event | None:
    """Действующее событие узла: последнее по first-parent."""
    for event in reversed(history):
        if _touches(event, node):
            return event
    return None


def generation_refusal(history: History, node: str) -> str | None:
    """Поколения заявок узла строго растут; остановка — того же поколения."""
    last = 0
    for event in history:
        if not _touches(event, node):
            continue
        gen = _generation(event, node)
        if isinstance(event, DeliveredStop):
            if gen != last:
                return f"узел {node}: остановка поколения {gen}, действует {last}"
        elif gen <= last:
            return f"узел {node}: поколение {gen} не больше прежнего {last}"
        last = gen
    return None


def next_generation(history: History, node: str) -> int:
    """Поколение следующей заявки узла."""
    gens = [_generation(e, node) for e in history if _touches(e, node)]
    return max(gens, default=0) + 1


def _expected(current: DeliveredRequest, node: str, review_id: str) -> Envelope:
    req = current.request
    return Envelope(
        node=node,
        request_id=req.request_id,
        generation=req.nodes[node].generation,
        manifest_path=manifest_path(req.ws_id, req.request_id),
        head_sha=current.head_sha,
        self_hash=req.nodes[node].content_hash,
        upstream_pins=dict(req.upstream_pins[node]),
        merge_commit=current.merge_commit,
        review_id=review_id,
        policy=req.policy.as_record(),
    )


def envelope_refusal(
    env: Envelope | None,
    history: History,
    node: str,
    node_self_hash: str,
    upstream_pins: Mapping[str, str],
    review_id: str,
) -> str | None:
    """Причина «не одобрен» либо `None` (§9.1 п. 0a, 1, 2; §8 остановка)."""
    broken = generation_refusal(history, node)
    if broken is not None:
        return broken
    current = current_event(history, node)
    if current is None:
        return f"узел {node}: нет доставленной заявки review/v1"
    if isinstance(current, DeliveredStop):
        gen = current.stop.generation
        return f"узел {node}: одобрение поколения {gen} остановлено"
    if env is None:
        return f"узел {node}: нет конверта — не одобрен"
    expected = _expected(current, node, review_id)
    if env.node != node:
        return f"конверт узла {env.node} подставлен для {node}"
    if env.generation != expected.generation or env.request_id != expected.request_id:
        return f"узел {node}: конверт другого поколения ({env.generation})"
    if env != expected:
        return f"узел {node}: конверт не совпадает с манифестом заявки"
    if (
        node_self_hash != expected.self_hash
        or dict(upstream_pins) != expected.upstream_pins
    ):
        return f"узел {node}: байты или upstream-пины изменились после одобрения"
    return None
