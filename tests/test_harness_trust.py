"""Цепочка доверия кода, исполняемого скриптами мержа (ревью #344, #531, #573).

Настоящий репо — инвариант; синтетический репо — отрицательные проверки:
незащищённый прямой и транзитивный импорт, снятие защиты с каждого файла
цепочки (в т.ч. `pyproject.toml`/`uv.lock`) и нераспознанные формы запуска
обязаны ронять проверку.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governance import accept_pr, authority_root
from tests import harness_trust as ht

ROOT = Path(__file__).resolve().parent.parent

#: Ожидаемая цепочка настоящего репо. Литерал сверяется с вычисленной
#: (`test_trust_chain_is_what_we_think`): рост цепочки — находка, а каждый её
#: элемент ниже проверяется снятием защиты.
EXPECTED_CHAIN = [
    "approval_branches.sh",
    "contracts/approval-branches/v1/patterns.env",
    "contracts/approval-policy-source/v1/source.env",
    "contracts/authority-root/v1/paths.env",
    "contracts/discovery-approval/v1",
    "contracts/discovery-approval/v1/approval.py",
    "contracts/discovery-approval/v1/gate_check.py",
    "contracts/discovery-approval/v1/hashing.py",
    "governance/__init__.py",
    "governance/approval_request.py",
    "governance/brief_facts.py",
    "governance/brief_merge_check.py",
    "governance/discovery_approval.py",
    "governance/facts.py",
    "governance/halt_gate.py",
    "governance/policy_rule.py",
    "governance/ssot_env.py",
    "pyproject.toml",
    "ssot_env.sh",
    "uv.lock",
]


def test_merge_scripts_trust_chain_is_protected() -> None:
    """Всё, что исполняют merge-pr.sh и human-merge.sh, и вся цепочка — под
    authority-root и харнесс-гвардом; нераспознанных запусков нет."""
    assert (
        ht.violations(ROOT, authority_root.prefixes(), accept_pr._HARNESS_PREFIXES)
        == []
    )


def test_trust_chain_is_what_we_think() -> None:
    found, problems = ht.launches(ROOT)
    assert problems == []
    assert {(x.kind, x.path) for x in found} == {
        ("source", "ssot_env.sh"),
        ("source", "approval_branches.sh"),
        ("py-stdlib", "governance/halt_gate.py"),
        ("py-uv", "governance/brief_merge_check.py"),
    }
    assert sorted(ht.required_paths(ROOT)) == EXPECTED_CHAIN


def _without(prefixes, path: str) -> tuple[str, ...]:
    return tuple(p for p in prefixes if not path.startswith(p))


@pytest.mark.parametrize("path", EXPECTED_CHAIN)
@pytest.mark.parametrize("which", ["authority-root", "harness"])
def test_removing_protection_of_any_chain_file_fails(path: str, which: str) -> None:
    ar, harness = authority_root.prefixes(), accept_pr._HARNESS_PREFIXES
    if which == "authority-root":
        ar = _without(ar, path)
    else:
        harness = _without(harness, path)
    found = ht.violations(ROOT, ar, harness)
    assert any(v.startswith(f"{path} ") for v in found), found


# --- синтетический репо: формы импорта и запуска ---

_GOOD_LAUNCH = (
    'x=$(cd "$script_dir" && UV_PROJECT_ENVIRONMENT="$env" \\\n'
    "    uv run --frozen --exact --no-config --no-env-file \\\n"
    '    python -I "$script_dir/governance/entry.py" --pr 7) || exit 2\n'
)
#: Загрузка пакета без дерева в sys.path — та же, что в brief_merge_check.
BOOTSTRAP = """import sys
from pathlib import Path
if __package__ in (None, "") and "governance" not in sys.modules:
    import importlib.util
    _d = Path(__file__).resolve().parent
    _s = importlib.util.spec_from_file_location(
        "governance", _d / "__init__.py", submodule_search_locations=[str(_d)]
    )
    _m = importlib.util.module_from_spec(_s)
    sys.modules["governance"] = _m
    _s.loader.exec_module(_m)
"""
ALL = (
    "governance/__init__",
    "governance/entry",
    "governance/a",
    "governance/b",
    "pyproject.toml",
    "uv.lock",
)


def _repo(tmp_path: Path, launch: str = _GOOD_LAUNCH, *, b_lazy: bool = False) -> Path:
    root = tmp_path / "repo"
    (root / "governance").mkdir(parents=True)
    (root / "governance" / "__init__.py").write_text("")
    (root / "governance" / "entry.py").write_text(
        BOOTSTRAP + "from governance import a\n"
    )
    if b_lazy:  # транзитивный ленивый импорт внутри функции
        (root / "governance" / "a.py").write_text(
            "def f():\n    from governance import b\n    return b\n"
        )
    else:
        (root / "governance" / "a.py").write_text("from governance import b\n")
    (root / "governance" / "b.py").write_text("X = 1\n")
    (root / "pyproject.toml").write_text("")
    (root / "uv.lock").write_text("")
    (root / "merge-pr.sh").write_text("#!/bin/sh\n")
    (root / "human-merge.sh").write_text(f"#!/bin/sh\nscript_dir=.\n{launch}")
    return root


def test_synthetic_repo_fully_protected_passes(tmp_path: Path) -> None:  # двойник
    root = _repo(tmp_path)
    assert ht.violations(root, ALL, ALL) == []


@pytest.mark.parametrize(
    ("unprotected", "b_lazy"),
    [
        ("governance/a", False),  # прямой импорт входа
        ("governance/b", False),  # транзитивный импорт
        ("governance/b", True),  # транзитивный ленивый (внутри функции)
        ("governance/__init__", False),  # исполняемый __init__ пакета
        ("pyproject.toml", False),
        ("uv.lock", False),
        ("governance/entry", False),  # сам вход
    ],
)
def test_unprotected_link_fails(tmp_path: Path, unprotected: str, b_lazy: bool) -> None:
    root = _repo(tmp_path, b_lazy=b_lazy)
    prefixes = tuple(p for p in ALL if p != unprotected)
    for ar, harness in ((prefixes, ALL), (ALL, prefixes)):
        found = ht.violations(root, ar, harness)
        assert any(v.startswith(unprotected) for v in found), found


@pytest.mark.parametrize(
    "launch",
    [
        'x=$(cd "$script_dir" && uv run --frozen python -m governance.entry)\n',
        'x=$(uv run --frozen python "$script_dir/governance/entry.py")\n',
        (  # без --no-env-file
            "x=$(UV_PROJECT_ENVIRONMENT=e uv run --frozen --exact --no-config "
            'python -I "$script_dir/governance/entry.py")\n'
        ),
        (  # без своего окружения
            "x=$(uv run --frozen --exact --no-config --no-env-file "
            'python -I "$script_dir/governance/entry.py")\n'
        ),
        'python3 "$script_dir/governance/entry.py"\n',  # без -I
        'bash "$script_dir/tool.sh"\n',
        '"$script_dir/governance/entry.py"\n',
        'echo "итог: $(python3 "$script_dir/governance/entry.py")"\n',  # в кавычках
        'source "$script_dir/ssot_env.sh"\n',
        'eval "$(cat "$script_dir/x")"\n',
        # ревью #573, круг 2: формы, которые пропускал перечень «опасных» слов
        'env python3 -I "$script_dir/governance/entry.py"\n',
        'command python3 -I "$script_dir/governance/entry.py"\n',
        '/usr/bin/python3 -I "$script_dir/governance/entry.py"\n',
        'python3.12 -I "$script_dir/governance/entry.py"\n',
        "$script_dir/governance/entry.py\n",
        'py=python3\n$py -I "$script_dir/governance/entry.py"\n',
        "unknown_helper --pr 7\n",
    ],
)
def test_unrecognized_launch_fails(tmp_path: Path, launch: str) -> None:
    root = _repo(tmp_path, launch)
    assert any(
        "нераспознанный запуск" in v or "UV_PROJECT_ENVIRONMENT" in v
        for v in ht.violations(root, ALL, ALL)
    )


def test_launch_inside_sourced_shell_file_fails(tmp_path: Path) -> None:
    root = _repo(tmp_path, _GOOD_LAUNCH + '. "$script_dir/helper.sh"\n')
    (root / "helper.sh").write_text('python3 "$script_dir/governance/entry.py"\n')
    found = ht.violations(root, (*ALL, "helper.sh"), (*ALL, "helper.sh"))
    assert any(v.startswith("helper.sh: нераспознанный запуск") for v in found), found


def test_case_patterns_and_function_definitions_are_not_launches(
    tmp_path: Path,
) -> None:  # двойник детектора
    script = (
        _GOOD_LAUNCH
        + 'die() {\n    echo "$1" >&2\n    exit 2\n}\n'
        + 'case "$x" in\n    --squash|--merge) m="$1" ;;\n'
        + '    CLEAN|HAS_HOOKS) : ;;\n    ""|*[!0-9]*) die "нет" ;;\n    *) break ;;\nesac\n'
    )
    root = _repo(tmp_path, script)
    assert ht.violations(root, ALL, ALL) == []


_CASE_BLOCK = 'case "$x" in\n    a|b) : ;;\n    *) die "нет" ;;\nesac\n'


def test_launch_right_after_case_block_fails(tmp_path: Path) -> None:
    """Ревью #573, круг 3: команда после `;;`/`esac`, завершённая `|`,
    пропускалась автоматом case как «шаблон»."""
    launch = 'pin=$(python3 -I "$script_dir/governance/new_check.py" | tail -n 1)\n'
    root = _repo(tmp_path, _GOOD_LAUNCH + _CASE_BLOCK + launch)
    (root / "governance" / "new_check.py").write_text("X = 1\n")
    found = ht.violations(root, ALL, ALL)  # new_check — вне обоих списков
    assert any(v.startswith("governance/new_check.py ") for v in found), found


def test_raw_layer_catches_interpreter_the_parser_reads_as_pattern(
    tmp_path: Path,
) -> None:  # второй слой независим от разбора
    root = _repo(tmp_path, _GOOD_LAUNCH + 'case "$x" in\n    python3) : ;;\nesac\n')
    found = ht.violations(root, ALL, ALL)
    assert any("упоминаний интерпретатора" in v for v in found), found


def test_code_ref_outside_launch_fails(tmp_path: Path) -> None:
    root = _repo(tmp_path, _GOOD_LAUNCH + 'x="$script_dir/governance/a.py"\n')
    found = ht.violations(root, ALL, ALL)
    assert any("ссылка на код governance/a.py" in v for v in found), found


def test_unprotected_data_read_by_script_fails(tmp_path: Path) -> None:
    root = _repo(tmp_path, _GOOD_LAUNCH + 'src="$script_dir/conf/x.env"\n')
    found = ht.violations(root, ALL, ALL)
    assert any(v.startswith("conf/x.env ") for v in found), found
    assert ht.violations(root, (*ALL, "conf/"), (*ALL, "conf/")) == []  # двойник


def test_tree_on_sys_path_of_the_process_fails(tmp_path: Path) -> None:
    """Ревью #573, круг 2: корень репо в sys.path впереди stdlib — подмена
    `json.py`/`argparse.py` из незащищённого корня."""
    root = _repo(tmp_path)
    (root / "governance" / "entry.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parent.parent))\n"
        "from governance import a\n"
    )
    found = ht.violations(root, ALL, ALL)
    assert any("каталог дерева в sys.path" in v for v in found), found


def test_shadow_file_in_root_does_not_reach_the_process(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "governance" / "b.py").write_text("import json\n")
    (root / "json.py").write_text('raise SystemExit("SHADOWED")\n')
    assert ht.violations(root, ALL, ALL) == []  # вход грузится, подмены нет


def test_entry_that_needs_tree_on_sys_path_fails(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "governance" / "entry.py").write_text("from governance import a\n")
    found = ht.violations(root, ALL, ALL)
    assert any("не загружается без дерева" in v for v in found), found


def test_text_in_quotes_is_not_a_launch(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        _GOOD_LAUNCH + 'die 3 "мерж — \\\n. это не source и не запуск"\n',
    )
    assert ht.violations(root, ALL, ALL) == []


def test_interpreter_word_in_message_is_conservatively_refused(
    tmp_path: Path,
) -> None:
    """Слой сырого текста осторожен намеренно: слово-интерпретатор даже в
    тексте сообщения — отказ (лишний отказ дешевле пропуска)."""
    root = _repo(tmp_path, _GOOD_LAUNCH + 'die 3 "нужен python3"\n')
    assert any("упоминаний интерпретатора" in v for v in ht.violations(root, ALL, ALL))


def test_stdlib_launch_with_third_party_fails(tmp_path: Path) -> None:
    root = _repo(tmp_path, 'python3 -I "$script_dir/governance/entry.py"\n')
    (root / "governance" / "b.py").write_text("import yaml\n")
    found = ht.violations(root, ALL, ALL)
    assert any("сторонние пакеты" in v for v in found), found


@pytest.mark.parametrize(
    ("code", "message"),
    [
        (  # путь от Path(__file__) — разрешается и требует защиты
            (
                "from pathlib import Path\n"
                'P = Path(__file__).resolve().parent.parent / "data" / "x.env"\n'
            ),
            "data/x.env",
        ),
        (  # основание не от Path(__file__) — нераспознанное обращение
            'from pathlib import Path\nP = Path("/somewhere") / "data" / "x.env"\n',
            "нераспознанное обращение",
        ),
    ],
)
def test_path_reads_of_chain_modules_are_checked(
    tmp_path: Path, code: str, message: str
) -> None:
    root = _repo(tmp_path)
    (root / "data").mkdir()
    (root / "data" / "x.env").write_text("K=v\n")
    (root / "governance" / "b.py").write_text(code)
    found = ht.violations(root, ALL, ALL)
    assert any(message in v for v in found), found


@pytest.mark.skipif(
    not __import__("os").environ.get("DEVTOOLS_UV_SMOKE"),
    reason="opt-in: DEVTOOLS_UV_SMOKE=1 (настоящий uv, сборка окружения)",
)
def test_real_uv_form_builds_env_and_runs_checker(tmp_path: Path) -> None:
    """Ревью #573, круг 4: боевая форма запуска исполняется настоящим `uv`.
    `--help` разбирается ПОСЛЕ импорта цепочки (вкл. yaml) — код 0 доказывает,
    что `--no-config` не потерял зависимости проекта."""
    import re
    import subprocess

    evil = tmp_path / "evil"
    evil.mkdir()
    (evil / "yaml.py").write_text('raise SystemExit("SHADOWED")\n')  # yaml — в цепочке
    script = (ROOT / "human-merge.sh").read_text(encoding="utf-8")
    form = re.search(
        r"uv run ((?:--[a-z-]+ )+)\\?\s*python((?: -[A-Za-z])*) "
        r'"\$script_dir/governance/brief_merge_check\.py"',
        script,
    )
    assert form, "форма запуска в human-merge.sh изменилась — обновите smoke"
    done = subprocess.run(
        [
            "uv",
            "run",
            *form.group(1).split(),
            "python",
            *form.group(2).split(),
            str(ROOT / "governance" / "brief_merge_check.py"),
            "--help",
        ],
        cwd=ROOT,
        env={
            **__import__("os").environ,
            "UV_PROJECT_ENVIRONMENT": str(tmp_path / "venv"),
            "PYTHONPATH": str(evil),  # -I обязан его отсечь
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr  # подменный yaml не исполнен
    assert "SHADOWED" not in done.stderr
    assert "usage: brief_merge_check" in done.stdout
