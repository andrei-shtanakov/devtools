"""`resolve_graph` не падает на диагностике без `provenance.repo`.

Ключ `repo` в provenance типизирован `| None`; поиск по нему идёт через
`repo_key`, поэтому диагностика без репо обходится молча, а не `KeyError`-ом
или обращением к `None`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "check-plan-fields.py"


@pytest.fixture(scope="module")
def plan_check() -> Any:
    spec = importlib.util.spec_from_file_location("plan_check_fields", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load plan checker from {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["plan_check_fields"] = module
    spec.loader.exec_module(module)
    return module


def test_diagnostic_without_repo_provenance_is_tolerated(
    plan_check, monkeypatch: pytest.MonkeyPatch
) -> None:
    from plan_fields import ManifestIndex, RepoInput

    # line — int, repo нет: диагностика проходит ветку stray_tag_line
    # (подсказка по TODO репо) с пустым ключом и попадает в отчёт, а ключа
    # (repo, line) без repo в by_item нет (прочие записи maestro — от других
    # проверок, например грамматики @owner)
    orphan = {
        "code": "PF-ID-MISSING",
        "message": "no repo",
        "provenance": {"line": 1},
    }
    monkeypatch.setattr(plan_check, "check_fleet", lambda snapshot: [orphan])
    report = plan_check.Report()
    by_item = plan_check.resolve_graph(
        [RepoInput("maestro", "- [ ] x @owner:o @id:x\n")],
        ManifestIndex(frozenset({"maestro"}), {}),
        report,
    )
    assert all(isinstance(repo, str) and repo for repo, _ in by_item)
    assert not any(line == 1 and repo in ("", None) for repo, line in by_item)
    assert any("no repo" in line for line in [*report.errors, *report.warnings])
