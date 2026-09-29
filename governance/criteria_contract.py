"""criteria_contract — вендоренная копия criteria-closure/v1 (спека §5.4).

«Выпущено» выводится из данных: есть PIN и манифест сходится. Отдельного
флага нет — рассогласоваться нечему.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

CONTRACT_DIR = Path(__file__).resolve().parent.parent / "contracts/criteria-closure/v1"


@dataclass(frozen=True)
class MinVersion:
    version: str


def read_min_version(path: Path = CONTRACT_DIR / "min-spec-runner.env") -> MinVersion:
    for line in path.read_text().splitlines():
        key, _, value = line.strip().partition("=")
        if key == "MIN_SPEC_RUNNER_VERSION":
            return MinVersion(value.strip())
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


def _parts(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split(".") if p.isdigit())


def drift_findings(
    contract_dir: Path,
    upstream: Path | None,
    *,
    ci: bool,
    upstream_path: str = "contracts/criteria-closure/v1",
) -> tuple[list[str], list[str]]:
    """Дрейф копии от апстрима по ref из PIN (вторая гарантия вендоринга).

    `upstream_path` — где копия лежит у производителя: схемы — в
    `contracts/…`, общие фикстуры владения — в `tests/fixtures/…`.
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
    """Оракул доступен: контракт вендорен и spec-runner машины не ниже."""
    if not is_vendored or installed is None:
        return False
    return _parts(installed) >= _parts(minimum.version)
