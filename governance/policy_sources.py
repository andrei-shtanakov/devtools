"""Оси merge_gate из реальных источников (спека §6).

Ось 1 (requested authority, ADR-ECO-011): экосистемный конфиг флота ещё не
существует (его схему фиксирует арка issue-runner) — по переходному правилу
ECO-011 отсутствие объявления читается как дефолт "agent". Репо-уровень —
строка «Мерж: человек» в CLAUDE.md целевого репо. Прогон может только
ужесточить (run_override).

Ось 2 (safety, steward): вендоренная пинованная копия среза approval-policy
(contracts/steward-actor-policy/v1/, integrity по sha256 из PIN). Любое
расхождение/отсутствие = Safety(None, "unknown") — fail-closed, merge_gate
сам отправит такой вердикт человеку. yaml — транзитивная зависимость пина
steward, живёт только в uv-группе governance.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

from governance.merge_gate import Authority, Safety

CONTRACT_DIR = (
    Path(__file__).resolve().parent.parent
    / "contracts"
    / "steward-actor-policy"
    / "v1"
)
_HUMAN_LINE = re.compile(r"(?m)^\s*[-*]?\s*Мерж:\s*человек\b")


def load_safety(actor: str = "ai-prosto") -> Safety:
    """Срез steward-политики из вендоренной копии; сбой = unknown."""
    policy = CONTRACT_DIR / "approval-policy.yaml"
    pin = CONTRACT_DIR / "PIN"
    try:
        expected = pin.read_text().split()[0]
        data = policy.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            return Safety(agent_merge_allowed=None, actor_class="unknown")
        doc = yaml.safe_load(data.decode("utf-8"))
        allowed = doc.get("agent_merge_allowed")
        if not isinstance(allowed, bool):
            return Safety(agent_merge_allowed=None, actor_class="unknown")
        agents = {str(x) for x in doc.get("agent_identities") or []}
        humans = {str(x) for x in doc.get("human_identities") or []}
        key = f"github:{actor}"
        if key in humans:
            actor_class = "human"
        elif key in agents:
            actor_class = "agent"
        else:
            actor_class = "unknown"
        return Safety(agent_merge_allowed=allowed, actor_class=actor_class)
    except (OSError, ValueError, IndexError, yaml.YAMLError, AttributeError):
        return Safety(agent_merge_allowed=None, actor_class="unknown")


def repo_authority(target_dir: Path) -> str | None:
    """«Мерж: человек» в CLAUDE.md целевого репо -> human; иначе None."""
    claude = Path(target_dir) / "CLAUDE.md"
    try:
        text = claude.read_text(encoding="utf-8")
    except OSError:
        return None
    return "human" if _HUMAN_LINE.search(text) else None


def ecosystem_authority() -> str:
    """Конфиг флота ещё не существует: отсутствие объявления = agent (ECO-011)."""
    return "agent"


def build_authority(target_dir: Path, run_override: str | None) -> Authority:
    """Собрать Authority из трёх осей; каждый уровень может только ужесточить."""
    return Authority(
        ecosystem=ecosystem_authority(),
        repo=repo_authority(target_dir),
        run=run_override,
    )
