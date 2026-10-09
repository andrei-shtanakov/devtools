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
    "contracts/approval-policy-source/v1/source.env",
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
    (root / "governance" / "entry.py").write_text("from governance import a\n")
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
    ],
)
def test_unrecognized_launch_fails(tmp_path: Path, launch: str) -> None:
    root = _repo(tmp_path, launch)
    assert any(
        "нераспознанный запуск" in v or "UV_PROJECT_ENVIRONMENT" in v
        for v in ht.violations(root, ALL, ALL)
    )


def test_text_in_quotes_is_not_a_launch(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        _GOOD_LAUNCH + 'die 3 "мерж — \\\n. это не source и python3 не запуск"\n',
    )
    assert ht.violations(root, ALL, ALL) == []


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
