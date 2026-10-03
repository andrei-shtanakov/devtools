"""criteria_contract — вендоренная копия criteria-closure/v1 (спека §5.4).

«Выпущено» выводится из данных: есть PIN и манифест сходится. Отдельного
флага нет — рассогласоваться нечему.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

CONTRACT_DIR = Path(__file__).resolve().parent.parent / "contracts/criteria-closure/v1"
RESPONSES_DIR = CONTRACT_DIR / "fixtures/responses"


@dataclass(frozen=True)
class MinVersion:
    version: str | None  # None — команда ещё не выпущена (`pending`)


def read_min_version(path: Path = CONTRACT_DIR / "min-spec-runner.env") -> MinVersion:
    for line in path.read_text().splitlines():
        key, _, value = line.strip().partition("=")
        if key == "MIN_SPEC_RUNNER_VERSION":
            value = value.strip()
            return MinVersion(None if value == "pending" else value)
    raise ValueError(f"{path}: нет MIN_SPEC_RUNNER_VERSION")


def integrity_findings(contract_dir: Path = CONTRACT_DIR) -> list[str]:
    """Нет PIN — не вендорен (пусто); есть — manifest.json и sha256 сходятся."""
    if not (contract_dir / "PIN").exists():
        return []
    manifest_path = contract_dir / "manifest.json"
    if not manifest_path.exists():
        return ["criteria-closure/v1: есть PIN, но нет manifest.json"]
    out: list[str] = []
    for name, digest in sorted(json.loads(manifest_path.read_text()).items()):
        path = contract_dir / name
        if not path.exists():
            out.append(f"criteria-closure/v1: {name} из manifest отсутствует")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            out.append(f"criteria-closure/v1: {name} не совпал с manifest")
    return out


def vendored(contract_dir: Path = CONTRACT_DIR) -> bool:
    return (contract_dir / "PIN").exists() and not integrity_findings(contract_dir)


_RELEASE_RE = re.compile(r"(\d+(?:\.\d+)*)(.*)", re.DOTALL)
_PRE_RE = re.compile(
    r"[-._]?(dev|a|alpha|b|beta|c|rc|pre|preview)[-._]?(\d*)", re.IGNORECASE
)
_POST_OR_LOCAL_RE = re.compile(
    r"(?:[-._]?(?:post|rev|r)[-._]?\d*)?(?:\+.*)?", re.IGNORECASE
)
_PRE_RANK = {"dev": 0, "a": 1, "alpha": 1, "b": 2, "beta": 2}
_RELEASE_RANK = 9


def _version_key(version: str) -> tuple[tuple[int, ...], int, int]:
    """Ключ сравнения версий: номер релиза, затем фаза суффикса (#481 M-12).

    Pre-release (PEP 440 `rc1`/`a2`/`.dev3`, semver `-rc.1`) ниже своего
    релиза; post-release и local-метка — на уровне релиза. Нераспознанный
    суффикс ниже любого распознанного: доказать «не ниже релиза» нечем.
    """
    m = _RELEASE_RE.match(version.strip())
    if m is None:
        return ((), -1, 0)
    release = tuple(int(p) for p in m.group(1).split("."))
    suffix = m.group(2)
    if _POST_OR_LOCAL_RE.fullmatch(suffix):
        return (release, _RELEASE_RANK, 0)
    pre = _PRE_RE.fullmatch(suffix)
    if pre is None:
        return (release, -1, 0)
    rank = _PRE_RANK.get(pre.group(1).lower(), 3)  # c/rc/pre/preview
    return (release, rank, int(pre.group(2) or 0))


def drift_findings(
    contract_dir: Path,
    upstream: Path | None,
    *,
    ci: bool,
    upstream_path: str = "schemas/criteria-closure/v1",
) -> tuple[list[str], list[str]]:
    """Дрейф копии от апстрима по ref из PIN (вторая гарантия вендоринга).

    `upstream_path` — где копия лежит у производителя: схемы — в
    `schemas/…`, эталоны и фикстуры владения — в `tests/fixtures/…`.
    """
    import subprocess

    if not (contract_dir / "PIN").exists():
        return [], []
    sha = (contract_dir / "PIN").read_text().split("@")[-1].strip()
    if upstream is None or not (upstream / ".git").exists():
        msg = "criteria-closure/v1: апстрим spec-runner недоступен"
        return ([msg], []) if ci else ([], [f"{msg} — not-checked"])
    errors: list[str] = []
    manifest = json.loads((contract_dir / "manifest.json").read_text())
    for name in sorted(manifest):
        proc = subprocess.run(
            ["git", "-C", str(upstream), "show", f"{sha}:{upstream_path}/{name}"],
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            errors.append(f"criteria-closure/v1: {name} нет в апстриме @ {sha[:7]}")
        elif proc.stdout != (contract_dir / name).read_bytes():
            errors.append(
                f"criteria-closure/v1: {name} разошёлся с апстримом @ {sha[:7]}"
            )
    return errors, []


def oracle_available(
    installed: str | None, minimum: MinVersion, *, is_vendored: bool
) -> bool:
    """Оракул доступен: контракт вендорен, команда выпущена и spec-runner
    машины не ниже."""
    if not is_vendored or installed is None or minimum.version is None:
        return False
    return _version_key(installed) >= _version_key(minimum.version)
