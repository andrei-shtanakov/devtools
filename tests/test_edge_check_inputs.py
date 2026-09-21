from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from governance.edge_check import inputs as i
from governance.edge_check import rules as r

CONTRACTS = Path("contracts/edge-check/v1")


def _bundle(tmp_path: Path) -> Path:
    b = tmp_path / "spec"
    b.mkdir(parents=True)
    (b / "10-requirements.md").write_text("FR-01 Must\n", encoding="utf-8")
    (b / "15-behaviour-spec.md").write_text("BEH-01 traces FR-01\n", encoding="utf-8")
    return b


def test_hash_is_computed_over_the_prepared_copy(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    prepared = i.prepare_input(
        rs,
        b,
        [b / "15-behaviour-spec.md"],
        [("requirements", b / "10-requirements.md")],
    )
    subject = next(f for f in prepared.files if f.role == "subject")
    # sha256 ровно того текста, что уедет в запрос
    assert (
        subject.sha256 == hashlib.sha256(subject.text.encode("utf-8")).hexdigest()
    )
    assert subject.path == "15-behaviour-spec.md"
    assert prepared.applicable is True


# === I5: хэш считается по сырым байтам файла, не по нормализованному тексту ===


def test_hash_matches_raw_file_bytes_not_universal_newlines(tmp_path: Path) -> None:
    """`Path.read_text()` транслирует CRLF→LF — sha записи не должен зависеть
    от этой трансляции: D13 пересчитывает тот же SHA-256 по содержимому
    блоба на выбранном коммите, а в блобе CRLF остаётся как есть."""
    b = _bundle(tmp_path)
    raw = b"BEH-01\r\nBEH-02 traces FR-01\r\n"
    (b / "15-behaviour-spec.md").write_bytes(raw)
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    prepared = i.prepare_input(
        rs, b, [b / "15-behaviour-spec.md"],
        [("requirements", b / "10-requirements.md")],
    )
    subject = next(f for f in prepared.files if f.role == "subject")
    assert subject.sha256 == hashlib.sha256(raw).hexdigest()
    assert subject.size == len(raw)


def test_hash_matches_independently_computed_sha256(tmp_path: Path) -> None:
    """Ожидание считает `shasum`, а не тот же `hashlib.sha256`, что и код —
    сегодняшний тест самосогласован (§9.1: граница без независимой сверки)."""
    b = _bundle(tmp_path)
    content = (b / "15-behaviour-spec.md").read_text(encoding="utf-8")
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    prepared = i.prepare_input(
        rs, b, [b / "15-behaviour-spec.md"],
        [("requirements", b / "10-requirements.md")],
    )
    subject = next(f for f in prepared.files if f.role == "subject")
    expect = subprocess.run(
        ["shasum", "-a", "256"], input=content, capture_output=True, text=True,
        check=True,
    ).stdout.split()[0]
    assert subject.sha256 == expect


def test_missing_mandatory_basis_is_error_not_na(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    with pytest.raises(r.EdgeCheckError) as exc:
        i.prepare_input(
            rs, b, [b / "15-behaviour-spec.md"], [("requirements", b / "nope.md")]
        )
    assert exc.value.code == "missing_mandatory_input"


def test_symlink_input_is_refused(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("secret\n", encoding="utf-8")
    link = b / "20-design.md"
    link.symlink_to(outside)
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    with pytest.raises(r.EdgeCheckError) as exc:
        i.prepare_input(rs, b, [link], [("requirements", b / "10-requirements.md")])
    assert exc.value.code == "unsafe_input"


def test_unreadable_file_is_not_absence(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    bad = b / "10-requirements.md"
    bad.write_bytes(b"\xff\xfe\x00broken")
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    with pytest.raises(r.EdgeCheckError) as exc:
        i.prepare_input(rs, b, [b / "15-behaviour-spec.md"], [("requirements", bad)])
    assert exc.value.code == "unreadable_input"


def test_file_outside_bundle_is_unsafe(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    outside = tmp_path / "outside-secret.md"
    outside.write_text("secret\n", encoding="utf-8")
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    with pytest.raises(r.EdgeCheckError) as exc:
        i.prepare_input(
            rs, b, [b / "15-behaviour-spec.md"], [("requirements", outside)]
        )
    assert exc.value.code == "unsafe_input"


# === I1: роли входа сверяются с каталогом (basis_roles) ===


def test_extra_basis_role_is_rejected(tmp_path: Path) -> None:
    """Воспроизведение находки: посторонняя роль не должна тихо превратить
    ребро в PASS без реальной проверки по объявленному основанию."""
    b = _bundle(tmp_path)
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    with pytest.raises(r.EdgeCheckError) as exc:
        i.prepare_input(
            rs, b, [b / "15-behaviour-spec.md"], [("z", b / "15-behaviour-spec.md")]
        )
    assert exc.value.code == "basis_role_mismatch"


def test_missing_mandatory_basis_role_is_rejected(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    with pytest.raises(r.EdgeCheckError) as exc:
        i.prepare_input(rs, b, [b / "15-behaviour-spec.md"], [])
    assert exc.value.code == "basis_role_mismatch"


def test_duplicate_basis_role_is_rejected(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    with pytest.raises(r.EdgeCheckError) as exc:
        i.prepare_input(
            rs, b, [b / "15-behaviour-spec.md"],
            [("requirements", b / "10-requirements.md"),
             ("requirements", b / "10-requirements.md")],
        )
    assert exc.value.code == "basis_role_mismatch"
