"""Каталог правил edge-check и его identity (спека §5).

Identity — хэш упорядоченного набора: перестановка пунктов обязана менять её,
иначе результаты, снятые по прежней редакции, молча остались бы действующими.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml


class EdgeCheckError(Exception):
    """Отказ с машинным кодом; код — часть контракта результата."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RuleItem:
    id: str
    text: str


@dataclass(frozen=True)
class SeverityPolicy:
    blocking: frozenset[str]
    advisory: frozenset[str]

    def known(self) -> frozenset[str]:
        return self.blocking | self.advisory


@dataclass(frozen=True)
class ApplicabilityRule:
    id: str
    role: str


@dataclass(frozen=True)
class RuleSet:
    edge_id: str
    subject_role: str
    basis_roles: tuple[str, ...]
    instruction: str
    items: tuple[RuleItem, ...]
    severity: SeverityPolicy
    applicability: tuple[ApplicabilityRule, ...]
    identity: str


def load_rules(edge_id: str, contracts_dir: Path) -> RuleSet:
    """Прочитать набор правил ребра; неизвестное ребро — `unknown_edge`.

    Сбой самого каталога правил (битый YAML, негодная структура, нет
    инструкции) — именованная ошибка конфигурации, а не сырое исключение:
    иначе оно долетает до CLI как код 1, неотличимый от `FAIL` документа
    (находка C2, спека §8).
    """
    path = contracts_dir / "rules" / f"{edge_id}.yaml"
    if not path.is_file():
        raise EdgeCheckError(
            "unknown_edge", f"нет набора правил для ребра {edge_id!r}: {path}"
        )

    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(doc, dict):
            raise TypeError(f"корень документа — {type(doc).__name__}, не словарь")
        items = tuple(
            RuleItem(str(it["id"]), str(it["text"]).strip())
            for it in doc.get("items", [])
        )
        severity_doc = doc.get("severity") or {}
        if not isinstance(severity_doc, dict):
            raise TypeError(f"severity — {type(severity_doc).__name__}, не словарь")
        severity = SeverityPolicy(
            frozenset(severity_doc.get("blocking", [])),
            frozenset(severity_doc.get("advisory", [])),
        )
        applicability = tuple(
            ApplicabilityRule(str(a["id"]), str(a["role"]))
            for a in doc.get("applicability", [])
        )
        basis_roles = tuple(str(x) for x in doc.get("basis_roles", []))
        subject_role = str(doc.get("subject_role", ""))
    except yaml.YAMLError as exc:
        raise EdgeCheckError("malformed_rules", f"{path}: битый YAML: {exc}") from exc
    except OSError as exc:
        raise EdgeCheckError("malformed_rules", f"{path}: не читается: {exc}") from exc
    except (TypeError, KeyError, AttributeError, ValueError) as exc:
        raise EdgeCheckError(
            "malformed_rules", f"{path}: негодная структура: {exc}"
        ) from exc

    if not items:
        raise EdgeCheckError("unknown_edge", f"{path}: пустой items")

    instr_path = contracts_dir / "instruction.md"
    try:
        instruction = instr_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EdgeCheckError(
            "missing_instruction", f"{instr_path}: инструкция не найдена: {exc}"
        ) from exc

    canon = json.dumps(
        {
            "edge": edge_id,
            "instruction": instruction,
            "items": [[i.id, i.text] for i in items],
            "severity": {
                "blocking": sorted(severity.blocking),
                "advisory": sorted(severity.advisory),
            },
            "applicability": [[a.id, a.role] for a in applicability],
        },
        ensure_ascii=False,
        sort_keys=False,
        separators=(",", ":"),
    )
    identity = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    return RuleSet(
        edge_id=edge_id,
        subject_role=subject_role,
        basis_roles=basis_roles,
        instruction=instruction,
        items=items,
        severity=severity,
        applicability=applicability,
        identity=identity,
    )
