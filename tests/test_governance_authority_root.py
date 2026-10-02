"""Тесты authority_root — SSOT перечня защищённых путей (ADR-ECO-004 I2).

Перечень читают трое: `accept_pr` (гард приёмки), `runner`
(`touches_authority_root` для merge_gate) и `merge-pr.sh` (категорический
отказ обвязки мержа). До круга 4 ревью #183 питоновских определений было
два, и разойтись они могли молча.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
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
        # Стоп-кран: merge-pr.sh исполняет его из дерева (ревью devtools#531).
        "governance/halt_gate.py",
        # Оракул правила стоп-крана: агент поправил бы векторы под свою
        # реализацию (ревью devtools#531, второй прогон).
        "contracts/halt-admission/",
        # Оракул бандла, срез 2a: правило гейта [x] — подпись человека
        # (решение владельца 2026-10-02). Модули, через которые правило
        # гейта [x] можно ослабить:
        # какие пункты [x] и как проверяются.
        "governance/closure_gate",
        # оракул бандла, срез 2a: правило гейта [x] — подпись человека
        # (решение владельца 2026-10-02). происхождение штампа, пин —
        # текущий бандл, подписант.
        "governance/acceptance_provenance",
        # оракул бандла, срез 2a: правило гейта [x] — подпись человека
        # (решение владельца 2026-10-02). какие поля штамп вправе менять
        # (`stamp_text`).
        "governance/criteria_accept",
        # оракул бандла, срез 2a: правило гейта [x] — подпись человека
        # (решение владельца 2026-10-02). нужен ли человек
        # (`human_criteria`, `build_graph`).
        "governance/criteria_graph",
        # оракул бандла, срез 2a: правило гейта [x] — подпись человека
        # (решение владельца 2026-10-02). приоритеты требований → что
        # Won't и выпадает из счёта.
        "governance/acceptance_guard",
        # оракул бандла, срез 2a: правило гейта [x] — подпись человека
        # (решение владельца 2026-10-02). разбор/сериализация, на которых
        # стоит сверка «штамп = предложение».
        "governance/frontmatter",
        # оракул бандла, срез 2a: правило гейта [x] — подпись человека
        # (решение владельца 2026-10-02). какие charter'ы и пункты плана
        # гейт видит.
        "governance/charter_guard",
        # оракул бандла, срез 2a: правило гейта [x] — подпись человека
        # (решение владельца 2026-10-02). источник политики и состав
        # подписантов (вынесено из `approval_facts`).
        "governance/policy_rule",
        # оракул бандла, срез 2a: правило гейта [x] — подпись человека
        # (решение владельца 2026-10-02). парсер SSOT и файла политики.
        "governance/ssot_env",
        # оракул бандла, срез 2a: правило гейта [x] — подпись человека
        # (решение владельца 2026-10-02). исполняется при любом импорте
        # `governance.*`.
        "governance/__init__",
        # оракул бандла, срез 2a: правило гейта [x] — подпись человека
        # (решение владельца 2026-10-02). кэш байткода: unchecked-hash
        # `.pyc` подменял бы модуль без правки `.py` (M8-1).
        "governance/__pycache__/",
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
        # Порядок работ conductor в зонтике (ai-orchestrators-workspace#48):
        # `autonomy` — полномочия агента-оркестратора; merge-pr.sh читает этот
        # перечень и на PR зонтика, так что поднять их агентским мержем нельзя.
        "roadmap.toml",
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
    monkeypatch.setattr(authority_root, "PATHS_FILE", Path("/no/such/paths.env"))
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
        "AUTHORITY_ROOT_PREFIXES=.github/\nAUTHORITY_ROOT_PREFIXES=profiles/\n"
    )
    monkeypatch.setattr(authority_root, "PATHS_FILE", broken)
    with pytest.raises(RuntimeError, match="определён 2 раз"):
        authority_root.prefixes()


def test_missing_key_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        sourced |= set(re.findall(r'^\. "\$script_dir/([^"]+)"', text, re.MULTILINE))
        # Исполнение, не только подключение (ревью devtools#531): любой
        # `python3 … "$script_dir/…"` — тоже код обвязки из дерева.
        sourced |= set(re.findall(r'python3[^"\n]*"\$script_dir/([^"]+)"', text))
    assert sourced == {
        "ssot_env.sh",
        "approval_branches.sh",
        "governance/halt_gate.py",
    }, sourced
    for module in sourced:
        assert module in authority_root.prefixes(), module
        assert module in accept_pr._HARNESS_PREFIXES, module


def test_roadmap_is_authority_root() -> None:
    """ai-orchestrators-workspace#48: правку `roadmap.toml` мержит человек."""
    assert authority_root.touched(["roadmap.toml", "README.md"]) == ["roadmap.toml"]


#: Сторонние в процессе гейта — замер Task 7b (dev-venv, `-I` пробa): yaml
#: (frontmatter) и plan_fields с его импортами (`plan_fields/__init__` →
#: validator → jsonschema → jsonschema_specifications, referencing, rpds) +
#: attr/attrs, typing_extensions как их транзитивные зависимости.
#: `_virtualenv` — артефакт `.pth` uv-venv, не код правила, исключён до
#: сравнения. `yaml._yaml` — C-расширение внутри пакета `yaml`
#: (`n.split(".")[0]` даёт `yaml`, не отдельный топ-левел `_yaml`) — в
#: этом замере отдельного топ-левел `_yaml` не возникло. Рост набора —
#: находка, не правка литерала.
GATE_THIRD_PARTY = {
    "yaml",
    "plan_fields",
    "jsonschema",
    "jsonschema_specifications",
    "referencing",
    "rpds",
    "attr",
    "attrs",
    "typing_extensions",
}


def _gate_process_modules(root: Path) -> list[tuple[str, str]]:
    """(имя, файл) модулей, загруженных гейтом в форме запуска CI (Task 9):
    `python -I` — без env, user site и cwd в sys.path; корень репо — В КОНЕЦ
    sys.path, чтобы модуль в корне не затенял stdlib/`yaml` (M7-1)."""
    probe = (
        "import sys, runpy\n"
        f"sys.path.append({str(root)!r})\n"
        "import governance.closure_gate\n"
        "for n, m in sorted(sys.modules.items()):\n"
        "    f = getattr(m, '__file__', None)\n"
        "    if f: print(n, f)\n"
    )
    with tempfile.TemporaryDirectory() as pyc:
        out = subprocess.run(
            [sys.executable, "-I", "-X", f"pycache_prefix={pyc}", "-c", probe],
            cwd="/",
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
    return [tuple(line.split(" ", 1)) for line in out]


def test_gate_import_closure_is_authority_root() -> None:
    """Класс «незащищённый код в процессе гейта» (ревью кругов 6–8; модель
    угроз 2a — defense-in-depth): модули репо, загруженные гейтом, ⊆
    authority-root и из `.py`; сторонние — `GATE_THIRD_PARTY`;
    stdlib не затенён модулем из репо. Новый импорт в модуле правила
    краснеет здесь, а не уходит молча."""
    root = Path(__file__).resolve().parent.parent
    mods = _gate_process_modules(root)
    in_repo = sorted(
        os.path.relpath(f, root)
        for _n, f in mods
        if Path(f).resolve().is_relative_to(root)
        and ".venv" not in Path(f).resolve().relative_to(root).parts
    )
    assert "governance/closure_gate.py" in in_repo
    assert "governance/acceptance_provenance.py" in in_repo
    unprotected = sorted(set(in_repo) - set(authority_root.touched(in_repo)))
    assert unprotected == [], unprotected
    assert all(rel.endswith(".py") for rel in in_repo), in_repo  # M8-1: не .so/.pyc
    third = sorted(
        {n.split(".")[0] for n, f in mods if "site-packages" in f} - {"_virtualenv"}
    )
    # Сторонние в процессе гейта: yaml (frontmatter) и plan_fields с его
    # импортами (канонический разбор [x], M8-5). Литерал фиксируется по
    # замеру пробы при исполнении задачи; рост — находка, не правка литерала.
    assert set(third) <= GATE_THIRD_PARTY, third
    # A5 (m7-4): ленивые импорты внутри функций модулей правила — тоже
    for rel in in_repo:
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module == "governance":
                names = [f"governance.{a.name}" for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.startswith("governance."):
                    path = name.replace(".", "/") + ".py"
                    assert authority_root.touched([path]) == [path], (rel, name)


def test_root_module_does_not_shadow_stdlib_in_gate_launch(tmp_path) -> None:
    """M7-1: в форме запуска CI модуль `re.py` в корне не исполняется."""
    (tmp_path / "re.py").write_text("raise SystemExit('SHADOW')\n")
    probe = (
        f"import sys; sys.path.append({str(tmp_path)!r}); import re; print(re.__file__)"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", probe],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert str(tmp_path) not in out


def test_criteria_close_stays_agent_mergeable() -> None:
    """Выпуск предложения/штампа — не правило гейта: его ослабление ловит гейт."""
    assert authority_root.touched(["governance/criteria_close.py"]) == []
