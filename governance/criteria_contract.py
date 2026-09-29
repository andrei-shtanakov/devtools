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


# Путь копии корня у апстрима; вложенная копия называет свой в PIN
# (`UPSTREAM_PATH:`), без него дрейф не проверяется — отказ, а не догадка.
_ROOT_UPSTREAM = "contracts/criteria-closure/v1"


def _copies(contract_dir: Path) -> list[Path]:
    """Вендоренные копии: корень с PIN и вложенные каталоги со своим PIN."""
    return sorted(p.parent for p in contract_dir.rglob("PIN"))


def _where(contract_dir: Path, copy: Path, name: str = "") -> str:
    rel = (copy / name).relative_to(contract_dir).as_posix()
    return f"criteria-closure/v1: {rel}" if rel != "." else "criteria-closure/v1:"


def _pin(copy: Path, *, is_root: bool) -> tuple[str, str | None]:
    """(sha, путь апстрима) из PIN копии."""
    sha, path = "", _ROOT_UPSTREAM if is_root else None
    for line in (copy / "PIN").read_text().splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "SOURCE":
            sha = value.rpartition("@")[2].strip()
        elif key.strip() == "UPSTREAM_PATH":
            path = value.strip().strip("/")
    return sha, path


def integrity_findings(contract_dir: Path = CONTRACT_DIR) -> list[str]:
    """Каждая копия с PIN (корень и вложенные): manifest.json и sha256 сходятся.

    Нет ни одного PIN — не вендорено, пусто.
    """
    return [
        f for copy in _copies(contract_dir) for f in _copy_integrity(contract_dir, copy)
    ]


def _copy_integrity(contract_dir: Path, copy: Path) -> list[str]:
    manifest_path = copy / "manifest.json"
    if not manifest_path.exists():
        return [f"{_where(contract_dir, copy)} есть PIN, но нет manifest.json"]
    manifest = json.loads(manifest_path.read_text())
    out = [
        f"{_where(contract_dir, copy, name)} лежит в копии, но не в manifest"
        for name in _unlisted(contract_dir, copy, manifest)
    ]
    for name, digest in sorted(manifest.items()):
        path = copy / name
        if not path.exists():
            out.append(f"{_where(contract_dir, copy, name)} из manifest отсутствует")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            out.append(f"{_where(contract_dir, copy, name)} не совпал с manifest")
    return out


def _unlisted(contract_dir: Path, copy: Path, manifest: dict) -> list[str]:
    """Файлы вложенной копии вне manifest: иначе строка, убранная из
    манифеста, выводит файл из-под обеих гарантий. Корень не проверяется —
    рядом с вендоренными лежат свои файлы (README.md, min-spec-runner.env)."""
    if copy == contract_dir:
        return []
    names = {p.name for p in copy.iterdir() if p.is_file()} - {"PIN", "manifest.json"}
    return sorted(names - set(manifest))


def vendored(contract_dir: Path = CONTRACT_DIR) -> bool:
    """Схемы вендорены: PIN в корне и корневая копия цела.

    Вложенные копии (фикстуры) оракул не включают и не выключают — их
    целостность ловит гейт CI через `integrity_findings()`.
    """
    root = contract_dir / "PIN"
    return root.exists() and not _copy_integrity(contract_dir, contract_dir)


def _parts(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split(".") if p.isdigit())


def drift_findings(
    contract_dir: Path, upstream: Path | None, *, ci: bool
) -> tuple[list[str], list[str]]:
    """Дрейф каждой копии от апстрима по ref и пути из её PIN."""
    copies = _copies(contract_dir)
    if not copies:
        return [], []
    if upstream is None or not (upstream / ".git").exists():
        msg = "criteria-closure/v1: апстрим spec-runner недоступен"
        return ([msg], []) if ci else ([], [f"{msg} — not-checked"])
    errors: list[str] = []
    for copy in copies:
        errors += _copy_drift(contract_dir, copy, upstream)
    return errors, []


def _copy_drift(contract_dir: Path, copy: Path, upstream: Path) -> list[str]:
    import subprocess

    sha, up_path = _pin(copy, is_root=copy == contract_dir)
    if not sha:  # пустой sha → `:<путь>` читает индекс апстрима, не пин
        return [
            f"{_where(contract_dir, copy, 'PIN')} без SOURCE-ревизии — дрейф не проверить"
        ]
    if up_path is None:
        return [
            f"{_where(contract_dir, copy, 'PIN')} без UPSTREAM_PATH — дрейф не проверить"
        ]
    manifest_path = copy / "manifest.json"
    if not manifest_path.exists():
        return [f"{_where(contract_dir, copy)} есть PIN, но нет manifest.json"]
    probe = ["git", "-C", str(upstream), "cat-file", "-e", f"{sha}^{{commit}}"]
    if subprocess.run(probe, capture_output=True, check=False).returncode != 0:
        return [
            f"{_where(contract_dir, copy, 'PIN')} ревизия {sha[:7]} недоступна в апстриме"
        ]
    errors: list[str] = []
    for name in sorted(json.loads(manifest_path.read_text())):
        proc = subprocess.run(
            ["git", "-C", str(upstream), "show", f"{sha}:{up_path}/{name}"],
            capture_output=True,
            check=False,
        )
        where = _where(contract_dir, copy, name)
        if proc.returncode != 0:
            errors.append(f"{where} нет в апстриме @ {sha[:7]}")
        elif proc.stdout != (copy / name).read_bytes():
            errors.append(f"{where} разошёлся с апстримом @ {sha[:7]}")
    return errors


def oracle_available(
    installed: str | None, minimum: MinVersion, *, is_vendored: bool
) -> bool:
    """Оракул доступен: контракт вендорен и spec-runner машины не ниже."""
    if not is_vendored or installed is None:
        return False
    return _parts(installed) >= _parts(minimum.version)
