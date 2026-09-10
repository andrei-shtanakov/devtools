"""Формат SSOT-файлов KEY=VALUE — и то, что ОБЕ половины читают его одинаково.

Половин две: `governance/ssot_env.py` и функция `ssot_key` в `merge-pr.sh`.
Поведение на битом входе — часть формата, а не деталь реализации: разойдясь
на нём, половины разойдутся молча, и SSOT перестанет быть SSOT ровно там, где
заводился. Именно это и случилось (ревью #183, круг 6): python брал ПЕРВОЕ
вхождение дублированного ключа, shell — ПОСЛЕДНЕЕ.

Поэтому главный тест здесь не «python делает X» и не «shell делает X», а
«обе делают ОДНО И ТО ЖЕ» — на одной таблице входов, прогнанной через обе.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from governance import ssot_env

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "merge-pr.sh"
KEY = "APPROVAL_CANDIDATE_TEMPLATE"

#: (имя случая, содержимое файла, ожидаемое значение либо None = отказ).
#: Один и тот же набор гоняется через обе половины.
CASES: list[tuple[str, str, str | None]] = [
    ("обычное определение", f"{KEY}=spec/x\n", "spec/x"),
    ("комментарии и пустые строки", f"# c\n\n{KEY}=spec/x\n\n# ещё\n", "spec/x"),
    ("ведущие пробелы у строки", f"   {KEY}=spec/x\n", "spec/x"),
    ("пробелы вокруг значения", f"{KEY}=  spec/x  \n", "spec/x"),
    ("пробелы ВНУТРИ значения сохраняются", f"{KEY}=a/ b/ c/\n", "a/ b/ c/"),
    ("без завершающего перевода строки", f"{KEY}=spec/x", "spec/x"),
    ("значение с `=` внутри", f"{KEY}=a=b\n", "a=b"),
    # --- отказы --------------------------------------------------------
    ("дубль ключа — отказ обеих", f"{KEY}=one\n{KEY}=two\n", None),
    ("дубль с разными пробелами — тоже дубль", f"{KEY}=one\n  {KEY}=two\n", None),
    ("ключа нет", "OTHER=1\n", None),
    ("файл пуст", "", None),
    ("только комментарии", f"# {KEY}=spec/x\n", None),
    ("ключ без значения", f"{KEY}=\n", None),
    ("значение из одних пробелов", f"{KEY}=   \n", None),
    ("пробел перед `=` — не определение", f"{KEY} =spec/x\n", None),
    ("`export` — не определение", f"export {KEY}=spec/x\n", None),
    ("другой регистр — не определение", f"{KEY.lower()}=spec/x\n", None),
    ("ключ как суффикс чужого — не определение", f"X{KEY}=spec/x\n", None),
]

GH_STUB = """#!/bin/sh
# Стаб gh для этих тестов: до сети дело не доходит — разбор SSOT идёт раньше.
echo "gh $*" >> "$GH_STUB_LOG"
case "$*" in
  *"api user"*) echo ai-prosto ;;
esac
"""


def _python_read(path: Path) -> str | None:
    """Значение по python-половине; None — отказ."""
    try:
        return ssot_env.read_key(path, KEY, "тестовый SSOT")
    except RuntimeError:
        return None


def _shell_read(path: Path, tmp_path: Path) -> str | None:
    """Значение по shell-половине; None — отказ.

    Гоняется НАСТОЯЩИЙ merge-pr.sh с зондом `--print-globs`: он читает тот же
    файл той же функцией и печатает выведенные глобы. Стаб shell-функции был
    бы второй реализацией — ровно тем, что этот тест и стережёт.
    """
    stub_bin = tmp_path / "bin"
    if not stub_bin.exists():
        stub_bin.mkdir()
        gh = stub_bin / "gh"
        gh.write_text(GH_STUB)
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
    env = os.environ.copy()
    env.update(
        PATH=f"{stub_bin}:{env['PATH']}",
        APPROVAL_PATTERNS=str(path),
        GH_STUB_LOG=str(tmp_path / "gh.log"),
    )
    res = subprocess.run(
        ["sh", str(SCRIPT), "--print-globs"],
        env=env, capture_output=True, text=True, check=False,
    )
    if res.returncode != 0:
        return None
    # `candidate=<глоб>`; глоб выводится из значения, поэтому по нему видно,
    # какое значение было прочитано — при значениях без плейсхолдеров это
    # само значение.
    return res.stdout.splitlines()[0].split("=", 1)[1]


@pytest.mark.parametrize(
    ("name", "content", "expected"),
    CASES,
    ids=[c[0] for c in CASES],
)
def test_both_halves_agree(
    tmp_path: Path, name: str, content: str, expected: str | None
) -> None:
    """Обе половины дают ОДИН результат на каждом входе таблицы."""
    path = tmp_path / "patterns.env"
    # Второй ключ — валидный и ПЕРВОЙ строкой: зонд shell-половины требует
    # оба, а случай «без завершающего перевода строки» обязан остаться
    # последней строкой файла. Python читает только KEY, ему он безразличен.
    path.write_text(
        f"APPROVAL_FINALIZE_SUFFIX=-final\n{content}", encoding="utf-8"
    )

    from_python = _python_read(path)
    from_shell = _shell_read(path, tmp_path)

    assert from_python == expected, f"python разошёлся с ожиданием: {name}"
    assert from_shell == expected, f"shell разошёлся с ожиданием: {name}"
    assert from_python == from_shell, (
        f"ПОЛОВИНЫ РАЗОШЛИСЬ на «{name}»: python={from_python!r}, "
        f"shell={from_shell!r} — формат читают двое, поведение на этом входе "
        "обязано быть общим"
    )


def test_duplicate_key_message_names_the_problem(tmp_path: Path) -> None:
    """Отказ на дубле объясняет, почему выбор не делается за человека."""
    path = tmp_path / "patterns.env"
    path.write_text(f"{KEY}=one\n{KEY}=two\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="определён 2 раз"):
        ssot_env.read_key(path, KEY, "тестовый SSOT")


def test_missing_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="недоступен"):
        ssot_env.read_key(tmp_path / "nope.env", KEY, "тестовый SSOT")


def test_shipped_ssot_files_parse(tmp_path: Path) -> None:
    """Оба боевых SSOT-файла читаются обеими половинами без отказа."""
    from governance import approval_branches, authority_root

    assert approval_branches.candidate_template().startswith("spec/")
    assert authority_root.prefixes()
    shell = _shell_read(approval_branches.PATTERNS_PATH, tmp_path)
    assert shell == approval_branches.candidate_glob()
