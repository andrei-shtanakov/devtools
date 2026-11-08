"""Страж: учётки владельца не зашиты в коде (план 1 darkfactory-config, Task 5).

Учётки, профили gh и источник политики берутся из `config.toml`; литералы
`"ai-prosto"` / `'ai-prosto'` и `andrei-shtanakov/approval-policy` в
не-тестовом коде запрещены, кроме явного перечня исключений ниже.
Охват намеренно узкий: ловятся только кавычечные формы; безкавычечные
shell-формы и логин человека не проверяются (TODO plan 2).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "templates" / "darkfactory" / "config.strict.example.toml"

#: Каталоги вне охвата стража (префиксы путей) и причина у каждого.
EXCLUDED_PREFIXES = {
    "tests/": "тесты держат значения владельца в данных (df_config_fixture)",
    "docs/": "документация и evidence",
    "df/": "установщик: учётки ролей/владельца — его собственная модель",
    "contracts/steward-actor-policy/": "вендоренный контракт steward",
    "templates/": "образцы конфига; код их не читает",
}
#: Файлы-исключения: путь -> (литерал, который файл обязан ещё содержать; причина).
EXCLUDED_FILES = {
    "governance/author_launch.py": (
        '"ai-prosto"',
        (
            "компонент пути ~/.config/ai-prosto/harness.env (каталог операторских "
            "настроек), не учётка"
        ),
    ),
    "issue_console.py": (
        '"ai-prosto"',
        (
            "TODO plan 2 (darkfactory-config): DEFAULT_INTERNAL — набор «своих» "
            "авторов консоли issue; файл не править "
            "(память: devtools-issue-console-untouchable)"
        ),
    ),
    "tools/gen_discovery_approval_fixture.py": (
        "andrei-shtanakov/approval-policy",
        (
            "TODO plan 2 (darkfactory-config): данные фикстуры discovery "
            "(HUMAN/POLICY_REPO); замена требует решения по дизайну"
        ),
    ),
}
LITERAL = re.compile(r"""["']ai-prosto["']|andrei-shtanakov/approval-policy""")


def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "*.py", "*.sh"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return out.splitlines()


def test_no_owner_identity_literals_in_code() -> None:
    offenders = []
    for rel in _tracked():
        if rel.startswith(tuple(EXCLUDED_PREFIXES)) or rel in EXCLUDED_FILES:
            continue
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for no, line in enumerate(text.splitlines(), 1):
            if LITERAL.search(line):
                offenders.append(f"{rel}:{no}: {line.strip()}")
    assert not offenders, "зашитые учётки владельца:\n" + "\n".join(offenders)


def test_exclusions_are_alive() -> None:
    """Исключение, чей литерал ушёл из файла, — мусор: убрать из перечня."""
    for rel, (needle, _reason) in EXCLUDED_FILES.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert needle in text, f"исключение {rel}: литерала {needle} больше нет"


def test_example_config_prints_agent(tmp_path: Path) -> None:
    cfg = tmp_path / "darkfactory" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    res = subprocess.run(
        [
            sys.executable,
            "-I",
            str(ROOT / "governance" / "df_config.py"),
            "get",
            "accounts.agent",
        ],
        env={"XDG_CONFIG_HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        check=False,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "ai-prosto"


def test_df_config_names_old_python() -> None:
    """Python < 3.11: именованный отказ кодом 2, а не трейсбек `tomllib`."""
    code = (
        "import sys, runpy; sys.version_info = (3, 9, 6, 'final', 0); "
        f"sys.argv = ['df_config.py', 'get', 'mode']; "
        f"runpy.run_path({str(ROOT / 'governance' / 'df_config.py')!r}, "
        "run_name='__main__')"
    )
    res = subprocess.run(
        [sys.executable, "-I", "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 2, res.stderr
    assert "нужен Python ≥ 3.11 (сейчас 3.9)" in res.stderr
    assert "Traceback" not in res.stderr
