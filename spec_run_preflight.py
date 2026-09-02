#!/usr/bin/env python3
"""spec_run_preflight — преflight-чеки перед прогоном spec-runner в соседе.

Ретроспектива 2026-09-02 (@id:spec-run-preflight, уроки 4–5 devtools#110 +
закрытые классы дня): каждый пункт ниже ловился в бою и стоил сгоревших
попыток либо многочасового зависания.

Чеки (FAIL — стоп, WARN — совет):
  * config-etalon   — у репо есть свой эталон (`spec-runner.config.example.
                      yaml` / `docs/workstream-setup.md`), а рабочего
                      `spec-runner.config.yaml` нет → FAIL: голый `config
                      --preset` без TDD-режима валит tdd-evidence (урок 4).
  * insteadof-https — в клоне не настроен `url."https://github.com/".
                      insteadOf git@github.com:` → FAIL: ssh-пуш раннера
                      зависал на git-receive-pack на часы (класс закрыт
                      2026-09-02 локальным insteadOf).
  * prefixless-db   — рядом с префиксными `spec/.executor-<ws>-state.db`
                      лежит беспрефиксная `spec/.executor-state.db` → FAIL:
                      tdd-evidence падает на «неоднозначной state db»
                      (upstream spec-runner#337); пустую безопасно удалить.
  * live-smoke-env  — у репо есть `scripts/install_pinned_*.sh` (среда
                      live-smoke как в CI, урок 5) → WARN-напоминание
                      выполнить те же install-шаги до запуска раннера
                      (проверить установленность извне надёжно нельзя —
                      venvs живут в $TMPDIR терминала прогона).
  * dirty-tree      — незакоммиченные изменения в целевом клоне → WARN:
                      раннер коммитит рядом с ними и перемешает историю.

Exit: 0 — чисто либо только WARN; 1 — есть FAIL; 2 — целевой репо не
найден/не git. Только stdlib; Python 3.11+.

Использование:
    ./spec_run_preflight.py --repo dispatcher [--workspace ..]
    make preflight ARGS='--repo dispatcher'
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_ETALON_MARKERS = (
    "spec-runner.config.example.yaml",
    "docs/workstream-setup.md",
)
_CONFIG_NAME = "spec-runner.config.yaml"
# Ключи, чьё объявление в эталоне делает их обязательными в рабочем
# конфиге: именно их отсутствие валило tdd-evidence в бою (урок 4).
_CRITICAL_KEYS = (
    "execution_mode", "tdd_runner", "review_policy", "harness_files",
)
# Ключ БЕЗ кавычек: в argv subprocess нет шелла, который бы их снял;
# кавычки в ключе делают lookup вечно пустым (пойман живым прогоном
# по dispatcher при написании чека). Кавычная форма — только в hint,
# который человек копипастит в шелл.
_INSTEADOF_KEY = "url.https://github.com/.insteadOf"
_INSTEADOF_VALUE = "git@github.com:"


@dataclass(frozen=True)
class Finding:
    """Одна находка преflight: чек, уровень (FAIL/WARN), объяснение."""

    check: str
    level: str
    detail: str


def _keys_present(text: str, keys: tuple[str, ...]) -> set[str]:
    """Какие из keys встречаются в YAML-тексте как ключи (на любом уровне)."""
    return {
        k for k in keys
        if re.search(rf"^[ \t]*{re.escape(k)}[ \t]*:", text, re.MULTILINE)
    }


def check_config_etalon(target: Path) -> list[Finding]:
    """Урок 4: конфиг — по эталону репо, если эталон существует.

    Наличие файла — не соответствие (приёмка PR #115): конфиг от голого
    `config --preset` существует, но без TDD-цепочки эталона валит
    tdd-evidence. Критические ключи, объявленные в example.yaml, обязаны
    присутствовать и в рабочем конфиге; docs/workstream-setup.md — проза,
    по ней сверка невозможна — там остаётся только presence-чек.
    """
    markers = [m for m in _ETALON_MARKERS if (target / m).is_file()]
    if not markers:
        return []
    config = target / _CONFIG_NAME
    if not config.is_file():
        return [Finding(
            "config-etalon", "FAIL",
            f"у репо есть эталон ({', '.join(markers)}), а {_CONFIG_NAME} "
            "отсутствует — собери конфиг ОТ ЭТАЛОНА, не голым "
            "`spec-runner config --preset` (урок 4 devtools#110)",
        )]
    example = target / _ETALON_MARKERS[0]
    if not example.is_file():
        return []
    required = _keys_present(
        example.read_text(encoding="utf-8"), _CRITICAL_KEYS
    )
    missing = required - _keys_present(
        config.read_text(encoding="utf-8"), _CRITICAL_KEYS
    )
    if not missing:
        return []
    return [Finding(
        "config-etalon", "FAIL",
        f"{_CONFIG_NAME} существует, но не несёт критических ключей "
        f"эталона: {', '.join(sorted(missing))} — конфиг от голого "
        "`--preset` без TDD-цепочки валит tdd-evidence "
        "(урок 4 devtools#110); пересобери от "
        f"{_ETALON_MARKERS[0]}",
    )]


def check_insteadof(target: Path) -> list[Finding]:
    """Класс ssh-зависаний: пуши раннера обязаны идти HTTPS."""
    done = subprocess.run(
        ["git", "-C", str(target), "config", "--get", _INSTEADOF_KEY],
        capture_output=True, text=True,
    )
    if done.returncode == 0 and done.stdout.strip() == _INSTEADOF_VALUE:
        return []
    return [Finding(
        "insteadof-https", "FAIL",
        "ssh-пуш раннера зависал на git-receive-pack часами "
        f"(класс 2026-09-02); выполни: git -C {target} config "
        f'url."https://github.com/".insteadOf "{_INSTEADOF_VALUE}"',
    )]


def check_prefixless_db(target: Path) -> list[Finding]:
    """Беспрефиксная state-DB рядом с префиксными — spec-runner#337."""
    spec = target / "spec"
    prefixless = spec / ".executor-state.db"
    if not prefixless.exists():
        return []
    prefixed = [
        p for p in spec.glob(".executor-*state.db") if p != prefixless
    ]
    if not prefixed:
        return []
    hint = (
        "пустая — безопасно удалить"
        if prefixless.stat().st_size == 0
        else "НЕ пустая — разберись, чья она, прежде чем удалять"
    )
    return [Finding(
        "prefixless-db", "FAIL",
        f"{prefixless} лежит рядом с префиксными "
        f"({', '.join(p.name for p in sorted(prefixed))}) — tdd-evidence "
        f"упадёт на «неоднозначной state db» (spec-runner#337); {hint}",
    )]


def check_live_smoke_env(target: Path) -> list[Finding]:
    """Урок 5: среда live-smoke как в CI — напомнить install-шаги."""
    scripts = sorted((target / "scripts").glob("install_pinned_*.sh"))
    if not scripts:
        return []
    names = ", ".join(f"scripts/{s.name}" for s in scripts)
    return [Finding(
        "live-smoke-env", "WARN",
        f"у репо есть pinned-install шаги ({names}) — выполни их (и "
        "экспорты, которые они печатают) В ТЕРМИНАЛЕ ПРОГОНА до запуска "
        "раннера, иначе попытки сгорят о локально-красный live-smoke "
        "(урок 5 devtools#110: 3 попытки, $2.82)",
    )]


def check_dirty_tree(target: Path) -> list[Finding]:
    """Незакоммиченное в целевом клоне перемешается с коммитами раннера."""
    done = subprocess.run(
        ["git", "-C", str(target), "status", "--porcelain"],
        capture_output=True, text=True,
    )
    if done.returncode != 0 or not done.stdout.strip():
        return []
    lines = done.stdout.strip().splitlines()
    return [Finding(
        "dirty-tree", "WARN",
        f"в клоне {len(lines)} незакоммиченных путей — коммиты раннера "
        "лягут рядом с ними",
    )]


def preflight(target: Path) -> list[Finding]:
    """Все чеки по целевому клону; порядок — стабильный для вывода."""
    findings: list[Finding] = []
    findings += check_config_etalon(target)
    findings += check_insteadof(target)
    findings += check_prefixless_db(target)
    findings += check_live_smoke_env(target)
    findings += check_dirty_tree(target)
    return findings


def main(argv: list[str] | None = None) -> int:
    """CLI: --repo <имя соседа>, --workspace (default: родитель devtools)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="имя репо (dispatcher)")
    parser.add_argument(
        "--workspace", default=str(Path(__file__).resolve().parent.parent),
    )
    args = parser.parse_args(argv)
    target = Path(args.workspace) / args.repo
    if not (target / ".git").exists():
        print(f"spec-run-preflight: {target} не найден или не git-репо")
        return 2

    findings = preflight(target)
    for f in findings:
        print(f"[{f.level}] {f.check}: {f.detail}")
    fails = [f for f in findings if f.level == "FAIL"]
    if fails:
        print(f"spec-run-preflight: {len(fails)} FAIL — прогон не готов")
        return 1
    print(f"spec-run-preflight: {target.name} готов к прогону spec-runner")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
