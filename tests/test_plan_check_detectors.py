"""Три находки umbrella-сессии 2026-08-18 (devtools#56/#57/#58).

* #56 — legacy `<repo>#<slug>` блокер на @id'd ОТКРЫТОМ пункте: пакетный
  legacy-граф пропускает @id-источники, канонический пайплайн из legacy-формы
  ребра не строит — закрытая цель молчала. Теперь warning (stale-only).
* #57 — тег на строке-продолжении невидим построчным парсерам (инцидент
  impresario: два доставленных ожидания были невидимы всему флоту);
  разорванный `@trigger:"…` — своя находка.
* #58 — issue-form реф на ЗАКРЫТОМ пункте падал в slug-матчер как ложный
  PF-LEGACY-AMBIGUOUS; конвенция флота хранит тег на [x] как историю,
  поэтому исключения обязаны покрывать и закрытые пункты.
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
    spec = importlib.util.spec_from_file_location("plan_check_detectors", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load plan checker from {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["plan_check_detectors"] = module
    spec.loader.exec_module(module)
    return module


def _index(*keys: str) -> Any:
    from plan_fields import ManifestIndex

    return ManifestIndex(frozenset(keys), {})


def _repo(name: str, todo: str) -> Any:
    from plan_fields import RepoInput

    return RepoInput(name, todo)


WAITER = "- [ ] wait @owner:o @blocked_by:maestro#done-slug @id:waiter\n"


# --- devtools#56: legacy slug ref on an @id'd open source -------------------


def test_legacy_stale_on_id_source_warns(plan_check) -> None:
    report = plan_check.Report()
    codes = plan_check.check_id_source_legacy_stale(
        [
            _repo("maestro", "- [x] shipped done-slug @owner:o @id:done-slug\n"),
            _repo("proctor", WAITER),
        ],
        _index("maestro", "proctor"),
        report,
    )
    assert codes == {("proctor", 1): {"PF-BLOCKER-STALE"}}
    assert len(report.warnings) == 1
    assert "legacy ref on @id'd source" in report.warnings[0]
    assert not report.errors  # warning, never build-failing


def test_legacy_open_target_is_silent(plan_check) -> None:
    report = plan_check.Report()
    codes = plan_check.check_id_source_legacy_stale(
        [
            _repo("maestro", "- [ ] shipped done-slug @owner:o @id:done-slug\n"),
            _repo("proctor", WAITER),
        ],
        _index("maestro", "proctor"),
        report,
    )
    assert codes == {} and report.warnings == []


def test_legacy_stale_only_other_outcomes_stay_canonical(plan_check) -> None:
    """No match / unknown repo — уже покрыто каноническим nudge, не дублируем."""
    report = plan_check.Report()
    codes = plan_check.check_id_source_legacy_stale(
        [
            _repo("maestro", "- [x] unrelated @owner:o @id:other\n"),
            _repo(
                "proctor",
                "- [ ] a @owner:o @blocked_by:maestro#ghost-slug @id:a\n"
                "- [ ] b @owner:o @blocked_by:nowhere#slug @id:b\n"
                "- [ ] c @owner:o @blocked_by:maestro#7 @id:c\n"  # issue form
                "- [ ] d @owner:o @blocked_by:todo://maestro/other @id:d\n",
            ),
        ],
        _index("maestro", "proctor"),
        report,
    )
    assert codes == {} and report.warnings == []


def test_unidded_sources_stay_with_package_graph(plan_check) -> None:
    """Без @id источник — территория пакетного legacy-графа, не наша."""
    report = plan_check.Report()
    codes = plan_check.check_id_source_legacy_stale(
        [
            _repo("maestro", "- [x] shipped done-slug @owner:o @id:done-slug\n"),
            _repo("proctor", "- [ ] w @owner:o @blocked_by:maestro#done-slug\n"),
        ],
        _index("maestro", "proctor"),
        report,
    )
    assert codes == {} and report.warnings == []


# --- devtools#57: tags the line-based parsers never read --------------------


def test_tag_on_continuation_line_warns(plan_check) -> None:
    report = plan_check.Report()
    plan_check.check_tag_placement(
        [_repo("m", "- [ ] x @owner:o\n      @id:hidden\n")], report
    )
    assert len(report.warnings) == 1
    assert "DT-TAG-ON-CONTINUATION" in report.warnings[0]
    assert "m/TODO.md:2" in report.warnings[0]


@pytest.mark.parametrize(
    "tag", ["@owner:o", "@blocked_by:r#s", '@trigger:"t"', "@id:i"]
)
def test_all_four_tags_are_covered(plan_check, tag: str) -> None:
    report = plan_check.Report()
    plan_check.check_tag_placement(
        [_repo("m", f"- [ ] x @owner:o @id:x\n      {tag}\n")], report
    )
    assert len(report.warnings) == 1, (tag, report.warnings)


def test_checkbox_tags_and_prose_mentions_are_silent(plan_check) -> None:
    """Многострочные пункты без тегов и упоминания в прозе — ноль находок."""
    report = plan_check.Report()
    plan_check.check_tag_placement(
        [
            _repo(
                "m",
                '- [ ] ok @owner:o @trigger:"fine" @blocked_by:r#s @id:ok\n'
                "      контекст для человека, упоминающий @id:ok в прозе\n"
                "      и `@blocked_by:todo://r/s` в бэктиках\n"
                "      просто длинное продолжение без тегов\n",
            )
        ],
        report,
    )
    assert report.warnings == []


def test_torn_trigger_quote_is_its_own_finding(plan_check) -> None:
    report = plan_check.Report()
    plan_check.check_tag_placement(
        [_repo("m", '- [ ] y @owner:o @trigger:"broken\n      value" tail\n')],
        report,
    )
    torn = [w for w in report.warnings if "DT-TRIGGER-UNTERMINATED" in w]
    assert len(torn) == 1 and "m/TODO.md:1" in torn[0]


# --- devtools#58: issue-form ref on a closed item ---------------------------


def test_closed_item_issue_ref_gives_no_findings(plan_check) -> None:
    inputs = [
        _repo("maestro", "- [ ] base @owner:o @id:b\n"),
        _repo("proctor", "- [x] done @owner:o @blocked_by:maestro#7 @id:dw\n"),
    ]
    index = _index("maestro", "proctor")
    # закрытый пункт не резолвится по состоянию (ожидания нет)…
    assert plan_check.collect_issue_refs(inputs, index) == []
    # …но его реф исключён из slug-матчера
    excl = plan_check.issue_ref_exclusions(inputs)
    assert ("proctor", "maestro#7") in excl
    report = plan_check.Report()
    plan_check.resolve_graph(inputs, index, report, extra_exclude=excl)
    assert not any("maestro#7" in w for w in report.warnings), report.warnings
    assert not report.errors


def test_without_exclusion_the_false_positive_reproduces(plan_check) -> None:
    """Характеризация бага: без фикса реф читался как legacy-slug."""
    inputs = [
        _repo("maestro", "- [ ] base @owner:o @id:b\n"),
        _repo("proctor", "- [x] done @owner:o @blocked_by:maestro#7 @id:dw\n"),
    ]
    report = plan_check.Report()
    plan_check.resolve_graph(inputs, _index("maestro", "proctor"), report)
    assert any("maestro#7" in w for w in report.warnings), report.warnings


def test_real_legacy_slug_still_ambiguous_on_closed_item(plan_check) -> None:
    """Настоящий slug-реф (не числовой) без цели — по-прежнему находка."""
    inputs = [
        _repo("maestro", "- [ ] base @owner:o @id:b\n"),
        _repo("proctor", "- [x] done @owner:o @blocked_by:maestro#gone-slug @id:dw\n"),
    ]
    excl = plan_check.issue_ref_exclusions(inputs)
    assert excl == set()  # non-numeric — не issue-форма, не исключается
    report = plan_check.Report()
    plan_check.resolve_graph(
        inputs, _index("maestro", "proctor"), report, extra_exclude=excl
    )
    assert any("gone-slug" in w for w in report.warnings), report.warnings
