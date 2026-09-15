"""Стадия Need (E2): чистые помощники без побочных эффектов.

Спека: docs/superpowers/specs/2026-09-15-need-stage-design.md.
Транспортный контракт (§6): `code = process.returncode` при валидном
контракте discovery; любая негодная форма — синтетический 1 с каноническим
synthetic envelope формы протокола. Это проверка согласованности границы,
НЕ второй вычислитель `protocol.exit_code` соседа.
"""
from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone

import yaml

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


BRIEF_REL = "brief-input/00-discovery/brief.md"
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)


@dataclass(frozen=True)
class InterviewSpec:
    """Координаты интервью, фиксируемые при старте (спека §4)."""

    frame: str
    stakeholder_role: str
    target: str
    traces_to: str | None
    upstream_blob: str | None

    def as_state(self) -> dict:
        return {
            "session_id": None,
            "frame": self.frame,
            "stakeholder_role": self.stakeholder_role,
            "target": self.target,
            "traces_to": self.traces_to,
            "upstream_blob": self.upstream_blob,
            "brief_rel": BRIEF_REL,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "completed_at": None,
        }

    @classmethod
    def from_state(cls, st: dict) -> "InterviewSpec":
        return cls(
            frame=st["frame"], stakeholder_role=st["stakeholder_role"],
            target=st["target"], traces_to=st.get("traces_to"),
            upstream_blob=st.get("upstream_blob"),
        )


def h1_line(target: str, frame: str) -> str:
    """Точная форма H1 рендера discovery (`render.py:371`)."""
    return f"# Discovery Brief — {target} ({frame}-фрейм)"


def answer_command(session_id: str, role: str, answer_file: str = "answer.yaml") -> str:
    """Точная команда ответа стейкхолдера (D1); shell-safe."""
    return " ".join([
        "discovery", "answer", "--session", shlex.quote(session_id),
        "--role", shlex.quote(role), "--file", shlex.quote(answer_file),
    ])


def supersede_template(session_id: str, role: str) -> str:
    """Шаблон повторного ответа при 10/11: question_id подставляет человек."""
    return " ".join([
        "discovery", "answer", "--session", shlex.quote(session_id),
        "--role", shlex.quote(role), "--question", "<QUESTION_ID>",
        "--supersede", "--file", shlex.quote("answer.yaml"),
    ])


def _parse_brief(text: str) -> tuple[dict, str | None]:
    """(frontmatter, первая строка H1) — без валидации контракта."""
    m = _FRONTMATTER_RE.match(text)
    meta = yaml.safe_load(m.group(1)) if m else {}
    if not isinstance(meta, dict):
        meta = {}
    h1 = next((ln for ln in text.splitlines() if ln.startswith("# ")), None)
    return meta, h1


def _traces(meta: dict) -> list[str]:
    raw = meta.get("traces_to") or []
    return [raw] if isinstance(raw, str) else [t for t in raw if isinstance(t, str)]


def _is_path_ref(ref: str) -> bool:
    return ref.endswith(".md") and not ref.startswith("[[")


def brief_coordinate_findings(brief_text: str, spec: InterviewSpec) -> list[str]:
    """Сверка H1 / frame / traces_to — штатный путь `brief` 0 (ролей нет: D3)."""
    meta, h1 = _parse_brief(brief_text)
    findings: list[str] = []
    expected_h1 = h1_line(spec.target, spec.frame)
    if h1 != expected_h1:
        findings.append(f"H1 {h1!r} ≠ {expected_h1!r}")
    frame = (meta.get("interview") or {}).get("frame") if isinstance(
        meta.get("interview"), dict) else None
    if frame != spec.frame:
        findings.append(f"interview.frame {frame!r} ≠ {spec.frame!r}")
    paths = [t for t in _traces(meta) if _is_path_ref(t)]
    if spec.frame == "customer" and paths:
        findings.append(f"customer-бриф с путевыми traces_to {paths!r}")
    if spec.frame == "engineer" and paths != [spec.traces_to]:
        findings.append(f"traces_to {paths!r} ≠ [{spec.traces_to!r}]")
    return findings


def attach_findings(brief_text: str, spec: InterviewSpec) -> list[str]:
    """Сверка при присоединении `--session`: координаты + роли (§5.3)."""
    findings = brief_coordinate_findings(brief_text, spec)
    meta, _ = _parse_brief(brief_text)
    sessions = (meta.get("interview") or {}).get("sessions") or []
    roles = {
        s.get("participant_role") for s in sessions if isinstance(s, dict)
    }
    if not roles <= {spec.stakeholder_role}:
        findings.append(
            f"роли участников {sorted(r for r in roles if r)!r} ⊄ "
            f"{{{spec.stakeholder_role!r}}}"
        )
    return findings
