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


def _compute_relative_path(path: Path, bundle_dir: Path) -> str:
    """Вычислить путь относительно bundle_dir с проверкой границы.

    Проверяет, что путь лежит ВНУТРИ bundle_dir.
    """
    resolved_path = path.resolve()
    resolved_bundle = bundle_dir.resolve()

    if not resolved_path.is_relative_to(resolved_bundle):
        raise EdgeCheckError(
            "unsafe_input",
            f"{path}: путь вне каталога бандла {bundle_dir} — вход отклонён",
        )

    return str(resolved_path.relative_to(resolved_bundle))


def _read(role: str, path: Path, bundle_dir: Path) -> InputFile:
    # Проверка границы и ссылки ДО чтения
    if path.is_symlink():
        raise EdgeCheckError(
            "unsafe_input", f"{path}: ссылка, а не обычный файл — вход отклонён"
        )
    rel = _compute_relative_path(path, bundle_dir)

    # I5: хэш — по сырым байтам файла, а не по `read_text()`, который
    # транслирует CRLF→LF. D13 пересчитывает тот же SHA-256 по содержимому
    # блоба на коммите — там CRLF остаётся как есть, и нормализованный хэш
    # никогда бы не сошёлся.
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise EdgeCheckError("unreadable_input", f"{path}: {exc}") from exc
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EdgeCheckError("unreadable_input", f"{path}: {exc}") from exc
    return InputFile(role, rel, hashlib.sha256(data).hexdigest(), len(data), text)


def _validate_basis_roles(
    ruleset: RuleSet, bases: list[tuple[str, Path]]
) -> None:
    """Роли переданных оснований обязаны совпасть с `ruleset.basis_roles`
    (находка I1): без этого посторонняя или пропущенная роль тихо
    проходит, и запись `PASS` утверждает проверку сочетания, которое не
    проверялось (спека §5.2)."""
    got = [role for role, _ in bases]
    dup = sorted({role for role in got if got.count(role) > 1})
    if dup:
        raise EdgeCheckError(
            "basis_role_mismatch",
            f"{ruleset.edge_id}: роль основания продублирована: "
            f"{', '.join(dup)}",
        )
    expected, got_set = set(ruleset.basis_roles), set(got)
    if got_set != expected:
        missing = sorted(expected - got_set)
        extra = sorted(got_set - expected)
        detail = []
        if missing:
            detail.append(f"не хватает: {', '.join(missing)}")
        if extra:
            detail.append(f"лишние: {', '.join(extra)}")
        raise EdgeCheckError(
            "basis_role_mismatch",
            f"{ruleset.edge_id}: роли оснований не совпадают с набором "
            f"{sorted(expected)}; {'; '.join(detail)}",
        )


def prepare_input(
    ruleset: RuleSet,
    bundle_dir: Path,
    subject: list[Path],
    bases: list[tuple[str, Path]],
) -> PreparedInput:
    """Прочитать объявленный вход; решение о применимости — ДО вызова модели."""
    _validate_basis_roles(ruleset, bases)
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
        rel = _compute_relative_path(path, bundle_dir)
        absences.append(Absence(rel, rule_id))

    return PreparedInput(tuple(files), tuple(absences), len(absences) == 0)
