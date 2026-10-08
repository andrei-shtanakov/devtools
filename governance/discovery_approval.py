"""Self-hash discovery-брифа — пиненая копия кода соседа (спека §11.4.2).

Копии в `contracts/discovery-approval/v1/` байт в байт равны файлам discovery
на коммите из `PINNED.txt`. Пакета `discovery` у devtools нет и не будет,
поэтому копии не импортируются как пакет: загрузчик сверяет sha256 каждой
копии с пином, подменяет РОВНО три известные строки импорта на модули-копии
и исполняет исходник в изолированном модуле. Любая иная строка
`from discovery…`/`import discovery…` — отказ: новый импорт у соседа значит,
что копия больше не замкнута, и считать хэш молча нельзя.
"""

from __future__ import annotations

import hashlib
import re
import sys
import types
from pathlib import Path
from typing import Any

VENDOR = (
    Path(__file__).resolve().parent.parent / "contracts" / "discovery-approval" / "v1"
)
#: Значение `discovery.render.GENERATED_BY`; `render.py` не вендорится (тянет
#: весь рендер), расхождение ловит тест на фикстуре, подписанной соседом.
GENERATED_BY = "discovery-runtime"
_IMPORTS = {
    "from discovery.contract.gate_check import split_frontmatter": "gate_check",
    "from discovery.hashing import canonical_answer_bytes": "hashing",
    "from discovery.render import GENERATED_BY": "render",
}
_DISCOVERY_IMPORT = re.compile(r"^\s*(from|import)\s+discovery\b", re.MULTILINE)


class VendorError(RuntimeError):
    """Копия не совпала с пином либо перестала быть замкнутой."""


def _alias(name: str) -> str:
    return f"governance._discovery_vendor_{name}"


def pins() -> dict[str, str]:
    """Пин sha256 по имени файла копии (из `PINNED.txt`)."""
    found: dict[str, str] = {}
    for line in (VENDOR / "PINNED.txt").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].endswith(".py"):
            found[parts[0]] = parts[1]
    return found


def load_module(path: Path, expected_digest: str) -> types.ModuleType:
    """Исполнить копию `path` после сверки sha256 и подмены известных импортов."""
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected_digest:
        raise VendorError(f"{path.name}: sha256 {digest} ≠ пина {expected_digest}")
    source = data.decode("utf-8")
    for line, name in _IMPORTS.items():
        imported = line.rsplit(" ", 1)[-1]
        source = source.replace(line, f"from {_alias(name)} import {imported}")
    if _DISCOVERY_IMPORT.search(source):
        raise VendorError(
            f"{path.name}: неизвестный import discovery — копия не замкнута"
        )
    module = types.ModuleType(_alias(path.stem))
    module.__file__ = str(path)
    sys.modules[module.__name__] = module
    exec(compile(source, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


def _bootstrap() -> Any:
    expected = pins()
    for name in ("approval.py", "hashing.py", "gate_check.py"):
        if name not in expected:
            raise VendorError(f"PINNED.txt не пинует {name}")
    render = types.ModuleType(_alias("render"))
    setattr(render, "GENERATED_BY", GENERATED_BY)
    sys.modules[render.__name__] = render
    load_module(VENDOR / "hashing.py", expected["hashing.py"])
    load_module(VENDOR / "gate_check.py", expected["gate_check.py"])
    return load_module(VENDOR / "approval.py", expected["approval.py"])


_approval: Any = _bootstrap()
NotABrief: type[Exception] = _approval.NotABrief
SELF_HASH_KEY: str = _approval.SELF_HASH_KEY
DEBT_STATUS: str = _approval.DEBT_STATUS
DEBT_UNSIGNED: str = _approval.DEBT_UNSIGNED
DEBT_MIGRATION: str = _approval.DEBT_MIGRATION
DEBT_SELF_HASH: str = _approval.DEBT_SELF_HASH


def self_hash(text: str) -> str:
    """Self-hash соседа (`approval.self_hash`) над `text`; `NotABrief` без frontmatter."""
    return str(_approval.self_hash(text))


def verify(text: str) -> str | None:
    """Почему `text` не честно одобрен по правилу соседа; `None` — одобрен."""
    result = _approval.verify(text)
    return None if result is None else str(result)
