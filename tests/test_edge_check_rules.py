from __future__ import annotations

from pathlib import Path

import pytest

from governance.edge_check import rules as r

CONTRACTS = Path("contracts/edge-check/v1")


def test_identity_changes_when_items_are_reordered(tmp_path: Path) -> None:
    src = (CONTRACTS / "rules/behaviour-vs-requirements.yaml").read_text(
        encoding="utf-8"
    )
    first = r.load_rules("behaviour-vs-requirements", CONTRACTS)

    swapped_dir = tmp_path / "rules"
    swapped_dir.mkdir(parents=True)
    lines = src.splitlines(keepends=True)

    # Найти индекс начала блока R1 и R2
    r1_idx = next(i for i, ln in enumerate(lines)
                  if ln.strip().startswith("- id: R1"))
    r2_idx = next(i for i, ln in enumerate(lines)
                  if ln.strip().startswith("- id: R2"))

    # Найти конец блока R1 (строка перед R2, но без самой R2)
    r1_end = r2_idx
    # Найти конец блока R2 (первая строка на уровне секции, не пункта)
    r2_end = next((i for i in range(r2_idx + 1, len(lines))
                   if lines[i][0] not in (' ', '\t')),
                  len(lines))

    # Извлечь блоки пунктов целиком
    block_r1 = lines[r1_idx:r1_end]
    block_r2 = lines[r2_idx:r2_end]

    # Поменять местами: R2 идёт на место R1, R1 идёт на место R2
    new_lines = (lines[:r1_idx] + block_r2 + block_r1 + lines[r2_end:])

    (swapped_dir / "behaviour-vs-requirements.yaml").write_text(
        "".join(new_lines), encoding="utf-8"
    )
    (tmp_path / "instruction.md").write_text(
        (CONTRACTS / "instruction.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "response-schema.json").write_text(
        (CONTRACTS / "response-schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    second = r.load_rules("behaviour-vs-requirements", tmp_path)

    assert first.identity != second.identity


def test_unknown_edge_is_config_error() -> None:
    with pytest.raises(r.EdgeCheckError) as exc:
        r.load_rules("no-such-edge", CONTRACTS)
    assert exc.value.code == "unknown_edge"
    assert "no-such-edge" in str(exc.value)


def _copy_contracts(dest: Path, *, rules_yaml: str) -> None:
    """Скопировать инструкцию и схему, положить свой rules-файл (для тестов
    на сломанный каталог правил — C2, I2, I4)."""
    rules_dir = dest / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "behaviour-vs-requirements.yaml").write_text(
        rules_yaml, encoding="utf-8"
    )
    (dest / "instruction.md").write_text(
        (CONTRACTS / "instruction.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (dest / "response-schema.json").write_text(
        (CONTRACTS / "response-schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )


# === C2: отказ каталога правил — код, а не сырой трейсбек ===


def test_malformed_items_structure_is_malformed_rules(tmp_path: Path) -> None:
    """items — не список пунктов, а строка: TypeError не должен уйти наружу
    как FAIL (код 1); ожидаем именованный код конфигурации."""
    _copy_contracts(tmp_path, rules_yaml="edge: x\nitems: не список, а строка\n")
    with pytest.raises(r.EdgeCheckError) as exc:
        r.load_rules("behaviour-vs-requirements", tmp_path)
    assert exc.value.code == "malformed_rules"


def test_broken_yaml_is_malformed_rules(tmp_path: Path) -> None:
    _copy_contracts(tmp_path, rules_yaml="items: [\n  - id: R1\n text: не закрыто")
    with pytest.raises(r.EdgeCheckError) as exc:
        r.load_rules("behaviour-vs-requirements", tmp_path)
    assert exc.value.code == "malformed_rules"


def test_missing_instruction_file_is_missing_instruction(tmp_path: Path) -> None:
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "behaviour-vs-requirements.yaml").write_text(
        (CONTRACTS / "rules/behaviour-vs-requirements.yaml").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    (tmp_path / "response-schema.json").write_text(
        (CONTRACTS / "response-schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    # instruction.md сознательно не кладём
    with pytest.raises(r.EdgeCheckError) as exc:
        r.load_rules("behaviour-vs-requirements", tmp_path)
    assert exc.value.code == "missing_instruction"


# === I4: несколько оснований + applicability — ошибка конфигурации ===


def test_multi_basis_with_applicability_is_rejected(tmp_path: Path) -> None:
    """Семантика частичного отсутствия у нескольких оснований не решена
    (findings I4) — закрываем дыру fail-closed на загрузке каталога."""
    rules_yaml = """
edge: x
subject_role: subject-thing
basis_roles: [a, b]
items:
  - id: R1
    text: правило
severity:
  blocking: [major]
  advisory: [minor]
applicability:
  - id: A1
    role: a
"""
    _copy_contracts(tmp_path, rules_yaml=rules_yaml)
    with pytest.raises(r.EdgeCheckError) as exc:
        r.load_rules("behaviour-vs-requirements", tmp_path)
    assert exc.value.code == "multi_basis_applicability_unsupported"


# === I2: check_identity покрывает схему ответа и версию шаблона промпта ===


def test_identity_changes_when_response_schema_changes(tmp_path: Path) -> None:
    """Правка `response-schema.json` не входила в identity — результаты,
    снятые до правки схемы, молча остались бы действующими."""
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir(parents=True)
    src = CONTRACTS / "rules/behaviour-vs-requirements.yaml"
    rules_dir.joinpath("behaviour-vs-requirements.yaml").write_text(
        src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "instruction.md").write_text(
        (CONTRACTS / "instruction.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "response-schema.json").write_text('{"changed": true}', encoding="utf-8")

    baseline = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    changed = r.load_rules("behaviour-vs-requirements", tmp_path)
    assert baseline.identity != changed.identity


def test_identity_changes_when_prompt_template_version_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Правка `prompt.py`, меняющая сборку запроса, обязана поднять
    `PROMPT_TEMPLATE_VERSION` — иначе identity не заметит смену шаблона."""
    from governance.edge_check import prompt as p

    baseline = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    monkeypatch.setattr(p, "PROMPT_TEMPLATE_VERSION", p.PROMPT_TEMPLATE_VERSION + 1)
    bumped = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    assert baseline.identity != bumped.identity


