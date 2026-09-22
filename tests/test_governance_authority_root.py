"""Тесты authority_root — SSOT перечня защищённых путей (ADR-ECO-004 I2).

Перечень читают трое: `accept_pr` (гард приёмки), `runner`
(`touches_authority_root` для merge_gate) и `merge-pr.sh` (категорический
отказ обвязки мержа). До круга 4 ревью #183 питоновских определений было
два, и разойтись они могли молча.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governance import accept_pr, authority_root


def test_prefixes_read_from_the_ssot_file() -> None:
    assert set(authority_root.prefixes()) == {
        ".github/",
        "profiles/",
        # Перечень защищает сам себя: менять границу полномочий — тоже
        # authority-акт, иначе агент вынес бы путь из-под защиты и следом
        # смержил правку в нём.
        "contracts/authority-root/",
        # Входы гвардов мержа — по той же причине: PR, меняющий шаблон
        # имён или сам скрипт, обезоруживает обвязку, и агентский мерж
        # такого PR открыл бы дорогу следующему уже без гварда.
        "contracts/approval-branches/",
        "merge-pr.sh",
        # Вход человеческого акта (D6) — той же категории (ревью #233).
        "human-merge.sh",
        # Координаты источника политики подписи (спека approval-policy S8):
        # константу в governance/ агент перенаправил бы своим PR.
        "contracts/approval-policy-source/",
        # Подключаемые модули merge-pr.sh/human-merge.sh — исполняемая часть
        # защищённой обвязки (blocker ревью PR #344).
        "ssot_env.sh",
        "approval_branches.sh",
        # Правило области ревью (devtools#271): решает, дойдёт ли диф до
        # модели вообще. Агентски смерженная правка `PROSE` сняла бы
        # ревью-гейт со всех последующих PR.
        "contracts/review-scope/",
        # Драйвер ревью-контура (devtools#273): исполняется из дерева и,
        # в отличие от кита, собственного детерминированного гейта не
        # имеет. 7 из 7 таких PR человек мержил руками по ручному лейблу —
        # теперь это механика, а не память агента.
        "review-pr.sh",
        # Инструмент, публикующий одобрение вместо содержательного ревью
        # (находка ревью на devtools#279). Довод буквально тот же, что у
        # review-pr.sh: он решает, дойдёт ли approve до PR. Отдельно важно,
        # что на нём держится ЕДИНСТВЕННОЕ исключение из инварианта ниже —
        # ослабь его агентски смерженным PR, и аттестации кита перестанут
        # что-либо доказывать, а PR ре-вендора останутся агентски
        # мержимыми по этому же исключению.
        "attest-vendor.sh",
    }


def test_every_harness_input_is_authority_root_but_the_kit() -> None:
    """devtools#273: соотношение двух перечней — множествами, не литералами.

    `_HARNESS_PREFIXES` держит стадию ПРИЁМКИ (accept_pr отказывает до
    ревью), authority-root — стадию МЕРЖА (`merge-pr.sh` и merge_gate
    раннера). Канон в самом SSOT (`paths.env`, ревью #183 круг 9): вход
    обвязки обязан быть в обоих, иначе агент смержит PR, снимающий гвард.
    Пробел и жил ровно здесь: оба перечня закреплены литералами в РАЗНЫХ
    тестах, и расхождение между ними прогон не краснил.

    Исключение ровно одно и оно решение владельца (2026-09-20), а не
    недосмотр: у вендор-копии кита есть собственный детерминированный
    гейт — `attest-vendor.sh` сверяет байты с текущим апстримом и
    отказывает на любом пути вне инвентаря. Объявить `scripts/review/`
    authority-root значило бы сделать волну ре-вендора (≈23 PR по флоту)
    человеко-мержимой и обесценить инструмент, построенный ради их
    дешевизны.

    Проверяется РАВЕНСТВО разности, а не вхождение: новый харнесс-вход без
    authority краснит прогон, и исчезновение исключения — тоже (значит
    довод выше пора переписать, а не терять молча).
    """
    harness = set(accept_pr._HARNESS_PREFIXES)
    authority = set(authority_root.prefixes())

    assert harness - authority == {"scripts/review/"}


def test_touched_returns_matching_paths_in_order() -> None:
    files = ["lib/x.ex", "profiles/steward.yaml", "docs/a.md", ".github/ci.yml"]
    assert authority_root.touched(files) == [
        "profiles/steward.yaml",
        ".github/ci.yml",
    ]


def test_touched_is_a_literal_prefix_not_a_pattern() -> None:
    """`.github/` — префикс, а не regexp: `xgithub/` под него не попадает."""
    assert authority_root.touched(["xgithub/ci.yml", "myprofiles/a"]) == []


def test_missing_ssot_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Недоступный файл — исключение, а не пустой кортеж.

    Пустой кортеж означал бы «authority-root путей нет», то есть снятие
    защиты молчанием.
    """
    monkeypatch.setattr(
        authority_root, "PATHS_FILE", Path("/no/such/paths.env")
    )
    with pytest.raises(RuntimeError, match="недоступен"):
        authority_root.prefixes()


def test_authority_root_prefixes_empty_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пустое значение ключа — тоже отказ, а не «путей нет»."""
    broken = tmp_path / "paths.env"
    broken.write_text("# только комментарий\nAUTHORITY_ROOT_PREFIXES=\n")
    monkeypatch.setattr(authority_root, "PATHS_FILE", broken)
    with pytest.raises(RuntimeError, match="AUTHORITY_ROOT_PREFIXES"):
        authority_root.prefixes()


def test_authority_root_rejects_duplicate_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Дубль ключа — отказ, а не выбор одного из двух значений за человека.

    Разбор общий (`ssot_env`), и это существенно: собственный разбор здесь
    брал ПЕРВОЕ вхождение, тогда как shell-половина брала последнее —
    половины расходились на дубле (ревью #183, круг 6).
    """
    broken = tmp_path / "paths.env"
    broken.write_text(
        "AUTHORITY_ROOT_PREFIXES=.github/\n"
        "AUTHORITY_ROOT_PREFIXES=profiles/\n"
    )
    monkeypatch.setattr(authority_root, "PATHS_FILE", broken)
    with pytest.raises(RuntimeError, match="определён 2 раз"):
        authority_root.prefixes()


def test_missing_key_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = tmp_path / "paths.env"
    broken.write_text("SOMETHING_ELSE=x\n")
    monkeypatch.setattr(authority_root, "PATHS_FILE", broken)
    with pytest.raises(RuntimeError, match="AUTHORITY_ROOT_PREFIXES"):
        authority_root.prefixes()


def test_approval_policy_source_is_authority_root() -> None:
    """Координаты источника политики подписи агент не вправе перенаправить
    своим PR под агентским мержем (спека approval-policy S8)."""
    assert "contracts/approval-policy-source/" in authority_root.prefixes()
    assert authority_root.touched(
        ["contracts/approval-policy-source/v1/source.env"]
    ) == ["contracts/approval-policy-source/v1/source.env"]


def test_every_module_sourced_by_the_merge_scripts_is_protected() -> None:
    """Функция, вынесенная из merge-pr.sh в подключаемый файл, не вправе
    выйти из-под authority-root и харнесс-гварда: иначе агент менял бы
    логику гвардов своим PR (blocker ревью PR #344)."""
    import re
    from governance import accept_pr
    root = Path(__file__).resolve().parent.parent
    sourced: set[str] = set()
    for script in ("merge-pr.sh", "human-merge.sh"):
        text = (root / script).read_text(encoding="utf-8")
        sourced |= set(re.findall(r'^\. "\$script_dir/([^"]+)"', text, re.M))
    assert sourced == {"ssot_env.sh", "approval_branches.sh"}, sourced
    for module in sourced:
        assert module in authority_root.prefixes(), module
        assert module in accept_pr._HARNESS_PREFIXES, module
