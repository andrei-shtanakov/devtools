"""Стадия Need (E2): чистые помощники без побочных эффектов.

Спека: docs/superpowers/specs/2026-09-15-need-stage-design.md.
Транспортный контракт (§6): `code = process.returncode` при валидном
контракте discovery; любая негодная форма — синтетический 1 с каноническим
synthetic envelope формы протокола. Это проверка согласованности границы,
НЕ второй вычислитель `protocol.exit_code` соседа.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

KNOWN_CODES: tuple[int, ...] = (0, 1, 2, 10, 11, 20)
REQUIRED_FIELDS: tuple[str, ...] = (
    "lifecycle", "gate", "readiness", "next_action",
    "findings", "readiness_findings", "operation",
)
SYNTHETIC_REASON_PREFIX = "граница discovery:"


@dataclass(frozen=True)
class DiscoveryReply:
    """Ответ одного вызова discovery CLI после проверки границы."""

    code: int
    envelope: dict
    stderr: str


def synthetic_envelope(reason: str) -> dict:
    """Канонический envelope синтетического кода 1 (форма протокола)."""
    return {
        "lifecycle": "unknown", "gate": "unknown", "readiness": "unknown",
        "next_action": {}, "findings": [], "readiness_findings": [],
        "operation": {
            "status": "unknown",
            "reason": f"{SYNTHETIC_REASON_PREFIX} {reason}",
        },
    }


def parse_reply(returncode: int, stdout: str, stderr: str) -> DiscoveryReply:
    """Проверка границы: валидный контракт → код процесса, иначе синтетический 1."""
    try:
        envelope = json.loads(stdout) if stdout.strip() else None
    except json.JSONDecodeError as exc:
        return DiscoveryReply(1, synthetic_envelope(f"stdout не JSON ({exc})"), stderr)
    if not isinstance(envelope, dict):
        return DiscoveryReply(1, synthetic_envelope("stdout не JSON-object"), stderr)
    missing = [f for f in REQUIRED_FIELDS if f not in envelope]
    if missing:
        return DiscoveryReply(
            1, synthetic_envelope(f"нет обязательных полей {missing}"), stderr
        )
    if returncode not in KNOWN_CODES:
        return DiscoveryReply(
            1, synthetic_envelope(f"неизвестный код {returncode}"), stderr
        )
    if returncode == 20:
        action = envelope.get("next_action")
        if not isinstance(action, dict) or not all(
            isinstance(action.get(k), str) and action.get(k)
            for k in ("session_id", "question_id")
        ):
            return DiscoveryReply(
                1,
                synthetic_envelope(
                    "код 20 без next_action.session_id/question_id"
                ),
                stderr,
            )
    return DiscoveryReply(returncode, envelope, stderr)
