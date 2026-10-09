"""Заявка на одобрение discovery-брифа (спека need-stage §11.3 п.3–3a).

Один нормативный разбор для всех потребителей: brief-propose, human-merge.sh
(через `brief_tools check-merge`), brief-approve и engineer-preflight.
Неоднозначная заявка не доказывает назначения акта, поэтому разбор строже
YAML: ровно один документ, только строковые ключи, дубли ключей запрещены на
всех уровнях (`safe_load` молча берёт последний), набор ключей — ровно схема v1.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

FILE_NAME = "approval-request.yaml"
SCHEMA = "discovery-brief-approval-request/v1"
PURPOSE = "discovery-brief-approval"
BRIEF = "brief.md"
_TOP = ("schema", "purpose", "brief", "brief_self_hash", "policy", "run_id", "ws_id")
_POLICY = ("repo", "ref", "path", "sha")
_SHA = re.compile(r"\A[0-9a-f]{40}\Z")


class RequestError(ValueError):
    """Заявка некорректна или неоднозначна."""


@dataclass(frozen=True)
class ApprovalRequest:
    """Разобранная заявка; `policy_*` — координаты и пин политики подписи."""

    brief_self_hash: str
    policy_repo: str
    policy_ref: str
    policy_path: str
    policy_sha: str
    run_id: str
    ws_id: str


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader: в любом mapping ключ — строка и встречается один раз."""


def _strict_mapping(loader: _StrictLoader, node: yaml.MappingNode) -> dict:
    seen: set[str] = set()
    for key_node, _ in node.value:
        if not isinstance(key_node, yaml.ScalarNode):
            raise RequestError("ключ — не скаляр")
        key = loader.construct_object(key_node)
        if not isinstance(key, str):
            raise RequestError(f"ключ {key!r} — не строка")
        if key in seen:
            raise RequestError(f"дублирующийся ключ {key!r}")
        seen.add(key)
    return loader.construct_mapping(node)


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _strict_mapping,  # type: ignore[arg-type]
)


def _q(value: str) -> str:
    """Строка как YAML-скаляр в двойных кавычках (JSON-строка — валидный YAML):
    `"123"`, `"yes"`, `"null"` остаются строками, а не числом/bool/None."""
    return json.dumps(value, ensure_ascii=False)


def render(req: ApprovalRequest) -> str:
    """Детерминированные байты заявки (фиксированный порядок ключей)."""
    return (
        f"schema: {SCHEMA}\n"
        f"purpose: {PURPOSE}\n"
        f"brief: {BRIEF}\n"
        f"brief_self_hash: {_q(req.brief_self_hash)}\n"
        "policy:\n"
        f"  repo: {_q(req.policy_repo)}\n"
        f"  ref: {_q(req.policy_ref)}\n"
        f"  path: {_q(req.policy_path)}\n"
        f"  sha: {_q(req.policy_sha)}\n"
        f"run_id: {_q(req.run_id)}\n"
        f"ws_id: {_q(req.ws_id)}\n"
    )


def _string(mapping: dict, key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise RequestError(f"{key}: ожидалась непустая строка, получено {value!r}")
    return value


def _exact_keys(mapping: object, keys: tuple[str, ...], where: str) -> dict:
    if not isinstance(mapping, dict):
        raise RequestError(f"{where}: ожидался mapping")
    if set(mapping) != set(keys):
        raise RequestError(f"{where}: ключи {sorted(mapping)} ≠ схеме {sorted(keys)}")
    return mapping


def parse(text: str) -> ApprovalRequest:
    """Строгий разбор; `RequestError` на любой некорректности или неоднозначности."""
    if "\r" in text:
        raise RequestError("CR в заявке: допускается только LF")
    try:
        docs = list(yaml.load_all(text, Loader=_StrictLoader))  # noqa: S506
    except yaml.YAMLError as exc:
        raise RequestError(f"YAML не разбирается: {exc}") from exc
    if len(docs) != 1:
        raise RequestError(f"ожидался ровно один YAML-документ, получено {len(docs)}")
    top = _exact_keys(docs[0], _TOP, "заявка")
    policy = _exact_keys(top["policy"], _POLICY, "policy")
    for key, expected in (("schema", SCHEMA), ("purpose", PURPOSE), ("brief", BRIEF)):
        if _string(top, key) != expected:
            raise RequestError(f"{key} {top[key]!r} ≠ {expected!r}")
    sha = _string(policy, "sha")
    if not _SHA.match(sha):
        raise RequestError(f"policy.sha {sha!r}: нужно 40 hex")
    return ApprovalRequest(
        brief_self_hash=_string(top, "brief_self_hash"),
        policy_repo=_string(policy, "repo"),
        policy_ref=_string(policy, "ref"),
        policy_path=_string(policy, "path"),
        policy_sha=sha,
        run_id=_string(top, "run_id"),
        ws_id=_string(top, "ws_id"),
    )


def main(argv: list[str] | None = None) -> int:
    """`check <file>`: 0 — заявка корректна, 3 — отказ (причина в stderr)."""
    parser = argparse.ArgumentParser(prog="approval_request")
    parser.add_argument("command", choices=["check"])
    parser.add_argument("file")
    args = parser.parse_args(argv)
    try:
        # Байты, не `read_text`: тот нормализует CRLF/CR до `parse`, и запрет
        # CR обходился бы именно на файловой границе.
        parse(Path(args.file).read_bytes().decode("utf-8"))
    except (RequestError, OSError, UnicodeError) as exc:
        print(f"заявка отклонена: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
