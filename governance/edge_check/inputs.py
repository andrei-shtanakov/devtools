"""Подготовка объявленного входа: копии, хэши, применимость (спека §4.1, D7).

Хэш считается по ТЕКСТУ, который уедет в запрос, а не по файлу в дереве:
заявленное и прочитанное расходятся молча, вычисленное — нет.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from governance.edge_check.rules import EdgeCheckError, RuleSet


@dataclass(frozen=True)
class InputFile:
    role: str
    path: str
    sha256: str
    size: int
    text: str


@dataclass(frozen=True)
class Absence:
    path: str
    rule_id: str


@dataclass(frozen=True)
class PreparedInput:
    files: tuple[InputFile, ...]
    absences: tuple[Absence, ...]
    applicable: bool


def _read(role: str, path: Path, bundle_dir: Path) -> InputFile:
    if path.is_symlink():
        raise EdgeCheckError(
            "unsafe_input", f"{path}: ссылка, а не обычный файл — вход отклонён"
        )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise EdgeCheckError("unreadable_input", f"{path}: {exc}") from exc
    rel = str(path.resolve().relative_to(bundle_dir.resolve()))
    data = text.encode("utf-8")
    return InputFile(role, rel, hashlib.sha256(data).hexdigest(), len(data), text)


def prepare_input(
    ruleset: RuleSet,
    bundle_dir: Path,
    subject: list[Path],
    bases: list[tuple[str, Path]],
) -> PreparedInput:
    """Прочитать объявленный вход; решение о применимости — ДО вызова модели."""
    optional_roles = {a.role: a.id for a in ruleset.applicability}
    files: list[InputFile] = []
    absences: list[Absence] = []

    for path in subject:
        if not path.exists():
            raise EdgeCheckError(
                "missing_mandatory_input", f"проверяемый объект отсутствует: {path}"
            )
        files.append(_read("subject", path, bundle_dir))

    for role, path in bases:
        if path.exists():
            files.append(_read(role, path, bundle_dir))
            continue
        rule_id = optional_roles.get(role)
        if rule_id is None:
            raise EdgeCheckError(
                "missing_mandatory_input",
                f"обязательное основание {role!r} отсутствует: {path}",
            )
        rel = str((path.resolve()).relative_to(bundle_dir.resolve()))
        absences.append(Absence(rel, rule_id))

    return PreparedInput(tuple(files), tuple(absences), len(absences) == 0)
