"""Vendored selector-capability boundary of spec-runner.

Runtime devtools must not import a neighbouring checkout. This small table is
therefore a pinned copy of the capability facts that affect task delivery;
the integration probe in ``tests/test_governance_task_bridge.py`` compares it
with spec-runner's real adapters when that neighbour is available.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

# spec-runner origin/master at acceptance of devtools#201. The relevant
# source is src/spec_runner/tdd_runners.py: TddRunnerAdapter,
# PytestAdapter.supports_file_targets and ExUnitAdapter.supports_file_targets.
SOURCE_REVISION = "2763e3f4baa681ec72c14736df60a53044ec18ad"

_CONFIG_FILES = (
    Path("spec-runner.config.yaml"),
    Path("spec/executor.config.yaml"),
)
_GLOB_CHARS = frozenset("*?[]")


@dataclass(frozen=True)
class SelectorPolicy:
    """The part of a runner adapter's selector dictionary used upstream."""

    name: str
    supports_file_targets: bool
    node_id_hint: str


SELECTOR_POLICIES = {
    "pytest": SelectorPolicy("pytest", True, "path::test"),
    "exunit": SelectorPolicy("exunit", False, "path:line"),
}


def is_file_target_form(raw: str) -> bool:
    """Mirror spec-runner's class dispatch, not filesystem validation."""

    value = (raw or "").strip()
    if not value or value.startswith("-") or ":" in value:
        return False
    return not any(ch in value for ch in _GLOB_CHARS)


def target_selector_policy(target_dir: str | Path) -> SelectorPolicy | None:
    """Resolve an explicitly configured runner, or ``None`` when inferred.

    An absent runner declaration is safe to leave to spec-runner: its only
    unambiguous inference today is pytest, which supports file targets.
    ExUnit cannot be inferred from ``mix`` and must be declared, so its
    no-file-target boundary is always visible here.

    Broken or ambiguous configuration refuses rather than silently disabling
    the early guard. Unknown adapters likewise require this vendored table to
    be updated before devtools can claim compatibility with their dictionary.
    """

    root = Path(target_dir)
    existing = [root / rel for rel in _CONFIG_FILES if (root / rel).is_file()]
    if not existing:
        return None
    if len(existing) > 1:
        names = ", ".join(str(path.relative_to(root)) for path in existing)
        raise ValueError(f"найдены оба конфига spec-runner ({names})")
    path = existing[0]
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(
            f"{path}: конфиг spec-runner не читается ({exc})"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path}: корень конфига spec-runner не mapping")
    raw = data.get("executor", data)
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: executor в конфиге spec-runner не mapping")
    declared = raw.get("tdd_runner")
    if declared is None or not str(declared).strip():
        return None
    name = str(declared).strip()
    policy = SELECTOR_POLICIES.get(name)
    if policy is None:
        known = ", ".join(sorted(SELECTOR_POLICIES))
        raise ValueError(
            f"{path}: tdd_runner {name!r} отсутствует в вендоренном "
            f"словаре ({known}); обновите контракт от spec-runner"
        )
    return policy
