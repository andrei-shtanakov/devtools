"""Одна проверка целиком: вход → запрос → ревьюер → валидация → запись.

Вердикт вычисляется механически по объявленной политике серьёзности: модель
классифицирует находку, итог выводит инструмент (спека §4.2, §5.4).
"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from governance.edge_check import inputs as inputs_mod
from governance.edge_check import prompt as prompt_mod
from governance.edge_check import response as response_mod
from governance.edge_check import reviewer as reviewer_mod
from governance.edge_check import rules as rules_mod
from governance.edge_check.rules import EdgeCheckError

SCHEMA_VERSION = 1


def decide(ruleset: rules_mod.RuleSet, response: response_mod.Response) -> str:
    """`FAIL` при блокирующей находке или проваленном пункте, иначе `PASS`."""
    if any(f.cls in ruleset.severity.blocking for f in response.findings):
        return "FAIL"
    if any(c.status != "pass" for c in response.criteria):
        return "FAIL"
    return "PASS"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_check(
    edge_id: str,
    bundle_dir: Path,
    subject: list[Path],
    bases: list[tuple[str, Path]],
    *,
    contracts_dir: Path,
    model: str,
    effort: str | None = None,
    timeout: int = 600,
    call: Callable[[str], str] | None = None,
) -> dict:
    """Провести одну проверку ребра и вернуть запись результата (спека §4.3).

    Неизвестное ребро (`unknown_edge`) вылетает наружу — его ловит задача 7.
    Любой другой `EdgeCheckError` превращается в запись с `verdict: "ERROR"`.
    Параметр `call` — публичный, и контракт не зависит от того, что именно
    он бросает: сбой вызова ревьюера любого типа тоже даёт `ERROR` (код
    `reviewer_failed`), как и сбой разбора его ответа (код `invalid_response`).
    """
    started = _now()
    attempt_id = uuid.uuid4().hex
    ruleset = rules_mod.load_rules(edge_id, contracts_dir)

    record: dict = {
        "schema_version": SCHEMA_VERSION,
        "edge": edge_id,
        "attempt_id": attempt_id,
        "started_at": started,
        "check_identity": ruleset.identity,
        "rules": {
            "id": edge_id,
            "items": [[i.id, i.text] for i in ruleset.items],
        },
        "reviewer": {"harness": "claude", "model": model, "effort": effort},
        "subject": [],
        "bases": [],
        "absence": [],
        "criteria": [],
        "findings": [],
    }

    def finish(verdict: str, *, code: str = "", reason: str = "") -> dict:
        record["verdict"] = verdict
        record["finished_at"] = _now()
        if code:
            record["error_code"] = code
        if reason:
            record["reason"] = reason
        return record

    try:
        prepared = inputs_mod.prepare_input(ruleset, bundle_dir, subject, bases)
    except EdgeCheckError as exc:
        return finish("ERROR", code=exc.code, reason=str(exc))

    record["subject"] = [
        {"role": f.role, "path": f.path, "sha256": f.sha256, "size": f.size}
        for f in prepared.files
        if f.role == "subject"
    ]
    record["bases"] = [
        {"role": f.role, "path": f.path, "sha256": f.sha256, "size": f.size}
        for f in prepared.files
        if f.role != "subject"
    ]
    record["absence"] = [
        {"path": a.path, "rule_id": a.rule_id} for a in prepared.absences
    ]

    if not prepared.applicable:
        ids = ", ".join(a.rule_id for a in prepared.absences)
        return finish(
            "N/A", reason=f"основание отсутствует, разрешено правилом {ids}"
        )

    try:
        built = prompt_mod.build_prompt(ruleset, prepared)
    except EdgeCheckError as exc:
        return finish("ERROR", code=exc.code, reason=str(exc))

    record["context"] = {
        "unit": built.measure.unit,
        "method": built.measure.method,
        "size": built.measure.size,
        "estimate_tokens": built.measure.estimate_tokens,
        "reserve_tokens": built.measure.reserve_tokens,
        "limit_tokens": built.measure.limit_tokens,
    }

    if call is None:
        schema = contracts_dir / "response-schema.json"
        argv = reviewer_mod.reviewer_argv(model, schema, effort)

        def call_real(text: str) -> str:
            with tempfile.TemporaryDirectory(prefix="edge-check-") as tmp:
                return reviewer_mod.run_reviewer(text, argv, Path(tmp), timeout)

        call = call_real

    try:
        raw = call(built.text)
    except EdgeCheckError as exc:
        return finish("ERROR", code=exc.code, reason=str(exc))
    except Exception as exc:  # noqa: BLE001 — контракт `call` произволен
        return finish(
            "ERROR",
            code="reviewer_failed",
            reason=f"ревьюер упал ({type(exc).__name__}): {exc}",
        )

    try:
        parsed = response_mod.parse_response(raw, ruleset, prepared)
    except EdgeCheckError as exc:
        return finish("ERROR", code=exc.code, reason=str(exc))
    except Exception as exc:  # noqa: BLE001 — контракт разбора не гарантирован
        return finish(
            "ERROR",
            code="invalid_response",
            reason=f"разбор ответа упал ({type(exc).__name__}): {exc}",
        )

    record["criteria"] = [
        {"id": c.id, "status": c.status, "reason": c.reason} for c in parsed.criteria
    ]
    record["findings"] = [
        {
            "rule_id": f.rule_id,
            "class": f.cls,
            "location": {"path": f.path, "lines": list(f.lines)},
            "statement": f.statement,
        }
        for f in parsed.findings
    ]
    return finish(decide(ruleset, parsed))
