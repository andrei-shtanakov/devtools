#!/usr/bin/env python3
"""fleet_config_policy — сенсор политики флота для конфига spec-runner.

Конфиг spec-runner (`spec-runner.config.yaml`) живёт в каждом репо: большая
часть — свойства репо, но часть ключей — политика флота, записанная копиями,
и копии разъезжаются молча (замер 2026-10-04: `harness_guard: strict` только
у spec-runner и devtools; у целей конвейера — умолчание `warn`). SSOT
политики — контракт `contracts/fleet-config-policy/v1/policy.toml`; сенсор
сверяет с ним каждый репо манифеста и называет отклонения. Ничего не пишет.

Конфиг ищется ровно как у spec-runner (`config._resolve_config_path`):
`spec-runner.config.yaml` в корне, иначе legacy `spec/executor.config.yaml`;
значение — из обёртки `executor:`, если она есть, иначе с верхнего уровня;
нет ключа — действует умолчание spec-runner (поле `default` контракта).
Читается `origin/<default>` — состояние флота, а не рабочее дерево клона;
ref определяется только по remote-ссылкам, отката на HEAD нет: не
резолвится — `unreadable`.

Состояния находки:
  ok           — значение равно требуемому (не печатается);
  violation    — конфиг есть, значение (или умолчание) не то;
  unconfigured — конфига нет, но spec-runner в репо гоняется (есть
                 `spec/*tasks*.md`) — действуют умолчания;
  unreadable   — конфиг не разобран (YAML, не mapping, `executor:` не mapping)
                 либо `origin/<default>` не резолвится.
Репо без конфига и без tasks-спек — неприменимо, находок нет.

Режим Maestro: если в `project.yaml` есть блок `spec_runner`, конфиг
генерирует Maestro (в git он не живёт), и значение берётся из оверлея
`spec_runner.extra_executor_config.executor` — как его вычислит Maestro.

Защита ≠ значение (решение владельца 2026-10-04): `ok` по значению
считается защитой только при версии spec-runner, закреплённой в `uv.lock`
репо и не ниже `protected_since` контракта (первая выпущенная версия с
исправлениями). Ниже `effective_since` ключ spec-runner молча игнорирует.

Exit: 0 — всё соблюдено и защита доказана (вывод пуст); 1 — нарушения или
нечитаемое; 3 — значения соблюдены, но защита не подтверждена; 2 — не
разобраны манифест/контракт. Python 3.11+, pyyaml.

Использование:
    ./fleet_config_policy.py --workspace .. [--manifest path] [--contract path]
    make config-policy
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from clone_fleet import manifest_set
from salvage_scan import _run_git

CONTRACT = (
    Path(__file__).resolve().parent / "contracts/fleet-config-policy/v1/policy.toml"
)
# Порядок — приоритет spec-runner (`config.py`: CONFIG_FILE, LEGACY_CONFIG_FILE).
CONFIG_PATHS = ("spec-runner.config.yaml", "spec/executor.config.yaml")
TASKS_RE = re.compile(r"^spec/[^/]*tasks[^/]*\.md$")


@dataclass(frozen=True)
class PolicyKey:
    """Ключ политики: требуемое значение и умолчание spec-runner."""

    name: str
    required: str
    default: str
    reason: str
    #: версия spec-runner, с которой ключ существует; ниже — молча игнорируется
    effective_since: str = ""
    #: первая выпущенная версия, где защита по ключу полна; пусто — не выпущена
    protected_since: str = ""


@dataclass(frozen=True)
class Finding:
    """Итог сверки одного ключа в одном репо.

    `state` — соответствие ЗНАЧЕНИЯ политике; `protected` — доказана ли
    работающая защита закреплённой версией spec-runner (решение владельца
    2026-10-04: наличие `strict` и версии ≥ effective_since этого не доказывает).
    """

    repo: str
    key: str
    state: str
    value: str
    source: str
    detail: str
    protected: bool = False
    protection: str = ""


def load_policy(path: Path) -> list[PolicyKey]:
    """Ключи политики из контракта; битый контракт — ValueError/OSError."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1:
        raise ValueError(f"{path}: schema {data.get('schema')!r}, ожидалась 1")
    return [
        PolicyKey(
            k["name"],
            str(k["required"]),
            str(k["default"]),
            k.get("reason", ""),
            str(k.get("effective_since", "")),
            str(k.get("protected_since", "")),
        )
        for k in data.get("key", [])
    ]


_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(.*)$")


def _vkey(version: str) -> tuple[int, int, int, int] | None:
    """Ключ сравнения; любой суффикс (rc, dev, …) — ниже своего релиза."""
    m = _VERSION_RE.match(version.strip())
    if m is None:
        return None
    major, minor, patch, rest = m.groups()
    return int(major), int(minor), int(patch), 0 if rest else 1


def pinned_version(repo: Path, ref: str) -> str | None:
    """Версия spec-runner, закреплённая в `uv.lock` репо; None — не закреплена."""
    text = _show(repo, ref, "uv.lock")
    if text is None:
        return None
    try:
        packages = tomllib.loads(text).get("package", [])
    except tomllib.TOMLDecodeError:
        return None
    for pkg in packages:
        if pkg.get("name") == "spec-runner":
            return str(pkg.get("version", "")) or None
    return None


def _protection(key: PolicyKey, pinned: str | None) -> tuple[bool, str]:
    """Доказана ли защита: значение уже `ok`, вопрос только в версии."""
    if pinned is None:
        return False, "не подтверждено: версия spec-runner не закреплена в репо"
    have = _vkey(pinned)
    floor = _vkey(key.effective_since) if key.effective_since else None
    if have is None:
        return False, f"не подтверждено: версия {pinned!r} не разобрана"
    if floor is not None and have < floor:
        return False, (
            f"не подтверждено: закреплена {pinned} < {key.effective_since} — "
            "ключ молча игнорируется"
        )
    if not key.protected_since:
        return False, "не подтверждено: исправления не выпущены (protected_since пуст)"
    fixed = _vkey(key.protected_since)
    if fixed is None or have < fixed:
        return False, f"не подтверждено: закреплена {pinned} < {key.protected_since}"
    return True, f"защищено: закреплена {pinned}"


def _show(repo: Path, ref: str, path: str) -> str | None:
    done = _run_git(repo, "show", f"{ref}:{path}")
    return done.stdout if done.returncode == 0 else None


def _tracked(repo: Path, ref: str) -> list[str] | None:
    done = _run_git(repo, "ls-tree", "-r", "--name-only", ref)
    return done.stdout.split() if done.returncode == 0 else None


def _section(text: str) -> dict:
    """Секция настроек как у spec-runner; не mapping — ValueError."""
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError(f"корень не mapping ({type(data).__name__})")
    section = data.get("executor", data)
    if not isinstance(section, dict):
        raise ValueError(f"executor: не mapping ({type(section).__name__})")
    return section


def fleet_ref(repo: Path) -> str | None:
    """`refs/remotes/origin/<default>` — ТОЛЬКО по remote-ссылкам, полным
    именем; None — не установлен.

    Локальные `master`/`main` и `HEAD` сюда не годятся (терм. ревью #562):
    это состояние клона, а не флота, и незапушенная правка выглядела бы
    соблюдённой политикой.
    """
    head = _run_git(repo, "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
    target = head.stdout.strip()
    # только внутрь refs/remotes/origin/: origin/HEAD, перенаправленный на
    # локальную ветку, снова читал бы клон вместо флота (ревью #562)
    ok = head.returncode == 0 and target.startswith("refs/remotes/origin/")
    candidates = [target] if ok else []
    candidates += ["refs/remotes/origin/master", "refs/remotes/origin/main"]
    for ref in candidates:
        if (
            _run_git(
                repo, "rev-parse", "-q", "--verify", f"{ref}^{{commit}}"
            ).returncode
            == 0
        ):
            # полное имя: короткое `origin/master` git резолвит через
            # `refs/heads/` раньше `refs/remotes/` — локальная ветка с таким
            # именем затенила бы флот (ревью #562)
            return ref
    return None


def check_repo(repo: Path, policy: list[PolicyKey]) -> list[Finding]:
    """Находки по всем ключам политики для одного клона, с пометкой защиты."""
    findings = _check_values(repo, policy)
    ref = fleet_ref(repo)
    pinned = pinned_version(repo, ref) if ref is not None else None
    keys = {k.name: k for k in policy}
    out = []
    for f in findings:
        if f.state != "ok":
            out.append(f)
            continue
        protected, note = _protection(keys[f.key], pinned)
        out.append(replace(f, protected=protected, protection=note))
    return out


def _maestro_findings(
    name: str, text: str, policy: list[PolicyKey]
) -> list[Finding] | None:
    """Режим Maestro: конфиг генерируется из `project.yaml` (`spec_runner`) и
    не трекается; None — `project.yaml` не про spec-runner.

    Значение вычисляется как у Maestro (`SpecRunnerConfig.to_executor_config`):
    произвольные ключи приходят только оверлеем `extra_executor_config`, и
    валидны лишь под `executor:` (плоский ключ рядом с обёрткой spec-runner
    отвергает как смешанную форму).
    """
    source = "project.yaml (Maestro)"
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        return [
            Finding(name, k.name, "unreadable", "?", source, f"не разобран: {exc}")
            for k in policy
        ]
    block = data.get("spec_runner") if isinstance(data, dict) else None
    if not isinstance(block, dict):
        return None
    extra = block.get("extra_executor_config") or {}
    section = extra.get("executor") if isinstance(extra, dict) else None
    section = section if isinstance(section, dict) else {}
    out = []
    for key in policy:
        present = key.name in section
        value = str(section[key.name]) if present else key.default
        detail = (
            ""
            if present
            else f"нет spec_runner.extra_executor_config.executor.{key.name} — "
            f"умолчание spec-runner {key.default!r}"
        )
        state = "ok" if value == key.required else "violation"
        out.append(Finding(name, key.name, state, value, source, detail))
    return out


def _check_values(repo: Path, policy: list[PolicyKey]) -> list[Finding]:
    """Соответствие значений политике (без вопроса о версии)."""
    name = repo.name
    ref = fleet_ref(repo)
    if ref is None:
        # неизвестность — не «неприменимо»: молчание зарезервировано за
        # прочитанным и соответствующим политике конфигом (ревью #562)
        return [
            Finding(
                name,
                key.name,
                "unreadable",
                "?",
                "-",
                "origin/<default> не резолвится — конфиг флота не прочитан",
            )
            for key in policy
        ]
    project = _show(repo, ref, "project.yaml")
    if project is not None:
        maestro = _maestro_findings(name, project, policy)
        if maestro is not None:
            return maestro
    for path in CONFIG_PATHS:
        text = _show(repo, ref, path)
        if text is not None:
            return _check_config(name, path, text, policy)
    tracked = _tracked(repo, ref)
    if tracked is None:
        return [
            Finding(
                name, key.name, "unreadable", "?", "-", f"дерево {ref} не прочитано"
            )
            for key in policy
        ]
    if not any(TASKS_RE.match(p) for p in tracked):
        return []
    return [
        Finding(
            name,
            key.name,
            "ok" if key.default == key.required else "unconfigured",
            key.default,
            "-",
            f"конфига spec-runner нет ({' / '.join(CONFIG_PATHS)}), а tasks-спеки "
            f"есть — действует умолчание {key.default!r}",
        )
        for key in policy
    ]


def _check_config(
    name: str, path: str, text: str, policy: list[PolicyKey]
) -> list[Finding]:
    try:
        section = _section(text)
    except (yaml.YAMLError, ValueError) as exc:
        return [
            Finding(
                name, key.name, "unreadable", "?", path, f"конфиг не разобран: {exc}"
            )
            for key in policy
        ]
    out = []
    for key in policy:
        present = key.name in section
        value = str(section[key.name]) if present else key.default
        detail = "" if present else f"ключа нет — умолчание spec-runner {key.default!r}"
        state = "ok" if value == key.required else "violation"
        out.append(Finding(name, key.name, state, value, path, detail))
    return out


def render(findings: list[Finding], policy: list[PolicyKey]) -> str:
    """Таблица отклонений; требуемое значение — из контракта."""
    required = {k.name: k.required for k in policy}
    rows = [
        f"{f.repo}\t{f.key}\t{f.state}\t{f.value} (нужно {required[f.key]})\t"
        f"{f.source}\t{f.protection or f.detail}"
        for f in findings
        if f.state != "ok" or not f.protected
    ]
    header = "репо\tключ\tсостояние\tзначение\tисточник\tзащита / примечание"
    return "\n".join([header, *rows])


def main(argv: list[str] | None = None) -> int:
    """CLI: сверка всех репо манифеста с контрактом политики."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace", type=Path, default=Path(__file__).resolve().parent.parent
    )
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--contract", type=Path, default=CONTRACT)
    args = parser.parse_args(argv)
    manifest = args.manifest or (
        args.workspace / "ai-orchestrators-workspace" / "workspace-manifest.toml"
    )
    try:
        repos = manifest_set(manifest)
        policy = load_policy(args.contract)
    except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError) as err:
        print(f"config-policy: манифест/контракт не разобран: {err}", file=sys.stderr)
        return 2
    findings: list[Finding] = []
    for git_dir in sorted(repos):
        repo = args.workspace / git_dir
        if not (repo / ".git").exists():
            print(f"config-policy: {git_dir}: клона нет — пропущен", file=sys.stderr)
            continue
        findings += check_repo(repo, policy)
    if all(f.state == "ok" and f.protected for f in findings):
        return 0
    print(render(findings, policy))
    return 1 if any(f.state != "ok" for f in findings) else 3


if __name__ == "__main__":
    sys.exit(main())
