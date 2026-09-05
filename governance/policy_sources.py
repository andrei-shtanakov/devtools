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


# Процедура-remediation preflight'а узла design (Task 8 + фикс-раунд ревью):
# одна и та же строка и у `stopped_preflight` раннера (`governance.runner`),
# и у RuntimeError `governance.task_bridge.deliver` — «та же процедура»
# буквально означает совпадающий текст, а не два похожих, но разных.
PREFLIGHT_PROCEDURE_HINT = (
    "доставьте обновлённый профиль в target PR-ом; "
    "profiles/ — authority-root, мерж человеком"
)


def target_profile_declares(target_dir: str, profile: str, node_id: str) -> bool:
    """True, если ``<target_dir>/<profile>`` объявляет узел ``node_id``.

    Preflight (Task 8, design-узел; вынесена из `governance.runner` в
    фикс-раунде ревью — общий источник для раннера И `task_bridge`, не
    приватный кросс-импорт): `gate_check_candidate` (S4 раннера) читает
    профиль ИЗ `target_dir`, не из devtools — соседний репо может нести
    СТАРУЮ копию файла того же имени (`profiles/team-exp.yaml`) без узла
    `design`, и `_step_authoring` молча попытался бы авторить узел, о
    котором target-профиль не просил. Проверка читает РОВНО тот путь, что
    получит `gate_check_candidate` (`state.profile` в раннере) /
    `task_bridge.deliver` (`profile`-аргумент), а не захардкоженное имя —
    решение «авторить ли design» data-driven для ЛЮБОГО профиля, не
    только `profiles/team-exp.yaml` (фикс-раунд ревью: хардкод имени в
    `_step_authoring` снят).

    Отсутствие файла или узла — `False` (тихо, это штатный «профиль
    вообще не про design» случай — не путать с ошибкой). Ошибка
    чтения/парсинга — fail-closed `False` с печатью причины: это
    preflight-проверка без побочных эффектов, не операция, ронять шаг
    исключением которой не стоит.
    """
    path = Path(target_dir) / profile
    if not path.exists():
        return False
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        node_ids = {a["id"] for a in (data or {}).get("artifacts", [])}
    except Exception as exc:  # noqa: BLE001 — fail-closed preflight, не операция
        print(
            f"target_profile_declares: {path} нечитаем/невалиден "
            f"({exc}) — fail-closed False"
        )
        return False
    return node_id in node_ids
