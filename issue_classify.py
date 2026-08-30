#!/usr/bin/env python3
"""AI-доклассификация типов issues (kind=unknown) через codex exec.

Опциональный контур issue_console (--classify-ai): база — детерминированная
эвристика; сюда попадают только неоднозначные issues. Ответы кэшируются по
ключу owner/repo#number@updatedAt; недоступный codex или битый кэш не ломают
консоль — типы просто остаются unknown.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

CONFIDENCE_THRESHOLD = 0.75
KINDS = ("document", "research", "code", "fix", "unknown")

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["repo", "number", "kind", "confidence"],
                "properties": {
                    "repo": {"type": "string"},
                    "number": {"type": "integer"},
                    "kind": {"enum": list(KINDS)},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        },
    },
}


class ClassifyError(RuntimeError):
    """Codex недоступен или вернул невалидный ответ."""


def cache_key(issue: Any) -> str:
    return f"{issue.owner}/{issue.repo}#{issue.number}@{issue.updated_at}"


def _load_cache(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if v in KINDS}


def _save_cache(path: Path, cache: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(cache, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        Path(tmp).unlink(missing_ok=True)


def run_codex(batch: list[dict]) -> list[dict]:
    """Один batch-вызов codex exec; ClassifyError при любом сбое."""
    prompt = (
        "Classify each GitHub issue into exactly one kind: document "
        "(docs/README/ADR edits), research (investigation/comparison), "
        "code (new functionality), fix (defect repair). Use unknown when "
        "genuinely ambiguous. Return JSON per the supplied schema.\n"
        f"Issues: {json.dumps(batch, ensure_ascii=False)}"
    )
    with tempfile.TemporaryDirectory(prefix="issue-classify-") as tmp:
        schema = Path(tmp) / "schema.json"
        answer = Path(tmp) / "answer.json"
        schema.write_text(json.dumps(SCHEMA))
        done = subprocess.run(
            ["codex", "exec", "--ephemeral", "--output-schema", str(schema),
             "--output-last-message", str(answer), "--sandbox", "read-only",
             prompt],
            capture_output=True, text=True, timeout=300,
        )
        if done.returncode:
            raise ClassifyError(done.stderr.strip() or "codex exec failed")
        try:
            parsed = json.loads(answer.read_text())
            items = parsed["items"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ClassifyError(f"невалидный ответ codex: {exc}") from exc
    if not isinstance(items, list):
        raise ClassifyError("items не список")
    return items


def refine(
    issues: list[Any],
    cache_path: Path,
    run: Callable[[list[dict]], list[dict]] = run_codex,
) -> dict[str, str]:
    """kind для issue.key по кэшу и (при промахах) одному batch-вызову AI.

    Возвращает только уверенные ответы (confidence >= порога и kind != unknown);
    остальное молча остаётся unknown у вызывающего.
    """
    unknowns = [x for x in issues if x.kind == "unknown"]
    if not unknowns:
        return {}
    cache = _load_cache(cache_path)
    result: dict[str, str] = {}
    missing: list[Any] = []
    for issue in unknowns:
        hit = cache.get(cache_key(issue))
        if hit is not None and hit != "unknown":
            result[issue.key] = hit
        elif hit is None:
            missing.append(issue)
    if not missing:
        return result
    batch = [{"repo": x.repo, "number": x.number, "title": x.title,
              "body": x.body[:2000]} for x in missing]
    try:
        answers = run(batch)
    except ClassifyError:
        return result
    by_key = {f"{x.repo}#{x.number}": x for x in missing}
    for item in answers:
        try:
            issue = by_key[f'{item["repo"]}#{int(item["number"])}']
            kind, confidence = str(item["kind"]), float(item["confidence"])
        except (KeyError, TypeError, ValueError):
            continue
        if kind not in KINDS:
            continue
        confident = kind != "unknown" and confidence >= CONFIDENCE_THRESHOLD
        cache[cache_key(issue)] = kind if confident else "unknown"
        if confident:
            result[issue.key] = kind
    _save_cache(cache_path, cache)
    return result
