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
найден/не git. Зависимость: PyYAML (есть в uv-окружении devtools;
без него сверка с эталоном отказывает fail-closed); Python 3.11+.

Использование:
    make preflight ARGS='--repo dispatcher'
    uv run --frozen python spec_run_preflight.py --repo dispatcher
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml
except ImportError:  # plain python3 без uv-окружения — fail-closed в чеке
    yaml = None

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


_MISSING = object()


def _critical_paths(
    node: object, prefix: tuple[str, ...] = ()
) -> dict[tuple[str, ...], object]:
    """Пути критических ключей в разобранном YAML → их значения.

    Путь (например ``executor.execution_mode``) — часть требования:
    spec-runner читает ключи в конкретной секции (config.py выбирает
    mapping ``executor``), и одноимённый ключ в посторонней секции режим
    не задаёт (приёмка PR #115, круг 7).
    """
    found: dict[tuple[str, ...], object] = {}
    if not isinstance(node, dict):
        return found
    for key, value in node.items():
        path = (*prefix, str(key))
        if key in _CRITICAL_KEYS:
            found[path] = value
        found.update(_critical_paths(value, path))
    return found


def _dig(node: object, path: tuple[str, ...]) -> object:
    """Значение по пути в разобранном YAML; _MISSING — пути нет."""
    for part in path:
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def check_config_etalon(target: Path) -> list[Finding]:
    """Урок 4: конфиг — по эталону репо, если эталон существует.

    Наличие файла — не соответствие (приёмка PR #115): конфиг от голого
    `config --preset` существует, но без TDD-цепочки эталона валит
    tdd-evidence. Сверка — настоящим YAML-парсером (круги 2–7 приёмки
    показали, что регулярки по тексту — это пере-изобретение парсера:
    кавычные ключи, пустые скаляры, комментарии в списках, секции):
    критический ключ эталона обязан присутствовать в конфиге ПО ТОМУ ЖЕ
    ПУТИ и с тем же скаляром; списки (harness_files — защитная
    поверхность spec-runner) — надмножеством элементов эталона.
    docs/workstream-setup.md — проза, по ней возможен только
    presence-чек самого конфига.
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
    if yaml is None:
        return [Finding(
            "config-etalon", "FAIL",
            "PyYAML недоступен — сверка с эталоном невозможна; запускай "
            "через `make preflight` (uv-окружение devtools)",
        )]
    try:
        etalon_data = yaml.safe_load(example.read_text(encoding="utf-8"))
        config_data = yaml.safe_load(config.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [Finding(
            "config-etalon", "FAIL",
            f"эталон или {_CONFIG_NAME} не парсится как YAML ({exc}) — "
            "сверка невозможна, готовность заявить нельзя",
        )]
    required = _critical_paths(etalon_data)
    missing: list[str] = []
    mismatched: list[str] = []
    for path, expected in sorted(required.items()):
        dotted = ".".join(path)
        actual = _dig(config_data, path)
        if actual is _MISSING:
            missing.append(dotted)
        elif isinstance(expected, list):
            # harness_files: элементы эталона обязаны присутствовать
            # (guard строится из фактического списка, круг 5); лишние
            # элементы — усиление, не FAIL.
            got_items = actual if isinstance(actual, list) else []
            dropped = [str(i) for i in expected if i not in got_items]
            if dropped:
                mismatched.append(
                    f"{dotted}: нет элементов эталона: {', '.join(dropped)}"
                )
        elif actual is None and expected is not None:
            # `key:` без значения не задаёт режим так же, как отсутствие
            # ключа (круг 4).
            mismatched.append(
                f"{dotted}: пустое/блочное значение (эталон {expected!r})"
            )
        elif actual != expected:
            mismatched.append(f"{dotted}={actual!r} (эталон {expected!r})")
    if not missing and not mismatched:
        return []
    parts = []
    if missing:
        parts.append(f"нет ключей: {', '.join(missing)}")
    if mismatched:
        parts.append(f"расходятся значения: {'; '.join(mismatched)}")
    return [Finding(
        "config-etalon", "FAIL",
        f"{_CONFIG_NAME} не соответствует эталону — {'; '.join(parts)} — "
        "конфиг без TDD-цепочки эталона валит tdd-evidence "
        f"(урок 4 devtools#110); пересобери от {_ETALON_MARKERS[0]}",
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
    """Незакоммиченное в целевом клоне перемешается с коммитами раннера.

    Сбой самого `git status` — не «чисто» (приёмка PR #115, круг 2):
    неопределимое состояние дерева закрывает preflight как FAIL.
    """
    done = subprocess.run(
        ["git", "-C", str(target), "status", "--porcelain"],
        capture_output=True, text=True,
    )
    if done.returncode != 0:
        return [Finding(
            "dirty-tree", "FAIL",
            f"git status в {target} не удался "
            f"(rc={done.returncode}: {done.stderr.strip()}) — состояние "
            "дерева неопределимо, готовность заявить нельзя",
        )]
    if not done.stdout.strip():
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
