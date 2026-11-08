"""Страж: учётки владельца не зашиты в коде (план 1 darkfactory-config, Task 5).

Учётки, профили gh и источник политики берутся из `config.toml`; литералы
`"ai-prosto"` / `'ai-prosto'` и `andrei-shtanakov/approval-policy` в
не-тестовом коде запрещены, кроме явного перечня исключений ниже.
Ловятся также логин человека `andrei-shtanakov` и безкавычечный
`ai-prosto` (комментарии и докстринги тоже) — кроме протокольных маркеров
`ai-prosto-<слово>` и компонентов путей `.config/ai-prosto`.
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
#: Файлы-исключения: путь -> [(литерал; причина)]. Освобождены только строки с
#: литералом, прочие строки файла проверяются; литерал обязан быть в файле.
EXCLUDED_FILES: dict[str, list[tuple[str, str]]] = {
    "governance/author_launch.py": [
        (
            '"ai-prosto"',
            (
                "компонент пути ~/.config/ai-prosto/harness.env (каталог "
                "операторских настроек), не учётка"
            ),
        )
    ],
    "issue_console.py": [
        (
            '"ai-prosto"',
            (
                "DEFAULT_INTERNAL — набор «своих» авторов консоли issue; файл "
                "неприкосновенен (правило репо: devtools-issue-console-untouchable)"
            ),
        ),
        (
            '"andrei-shtanakov"',
            "ISSUE_CONSOLE_OWNER по умолчанию; файл неприкосновенен",
        ),
    ],
    "inbox.py": [
        (
            '"andrei-shtanakov"',
            "DEFAULT_OWNER — инструмент флота инстанса владельца, не конвейер",
        )
    ],
    "r16_runner.py": [
        ("andrei-shtanakov/prograph-vault", "задача VPS владельца (R16), не конвейер"),
        ("the ai-prosto profile", "докстринги инструмента R16 владельца"),
    ],
    "tools/check_discovery_vendor.py": [
        ("andrei-shtanakov/discovery-toolkit", "апстрим вендоринга владельца")
    ],
    "tools/check_discovery_approval_vendor.py": [
        ("andrei-shtanakov/discovery", "апстрим вендоринга владельца")
    ],
    "tools/gen_discovery_approval_fixture.py": [
        (
            "andrei-shtanakov/approval-policy",
            (
                "данные подписанной фикстуры (байты подписаны настоящим discovery "
                "на пине), не учётки конвейера"
            ),
        ),
        ('HUMAN = "andrei-shtanakov"', "то же: данные фикстуры discovery"),
    ],
}
LITERAL = re.compile(
    r"""["']ai-prosto["']"""
    r"""|(?<![\w./~-])ai-prosto(?![\w-])"""
    r"""|andrei-shtanakov"""
)


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
        if rel.startswith(tuple(EXCLUDED_PREFIXES)):
            continue
        needles = [n for n, _ in EXCLUDED_FILES.get(rel, [])]
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for no, line in enumerate(text.splitlines(), 1):
            if any(n in line for n in needles):
                continue
            if LITERAL.search(line):
                offenders.append(f"{rel}:{no}: {line.strip()}")
    assert not offenders, "зашитые учётки владельца:\n" + "\n".join(offenders)


def test_exclusions_are_alive() -> None:
    """Исключение, чей литерал ушёл из файла, — мусор: убрать из перечня."""
    for rel, entries in EXCLUDED_FILES.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        for needle, _reason in entries:
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
