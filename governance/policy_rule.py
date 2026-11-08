"""policy_rule — источник и состав политики подписи (правило гейта [x]).

Вынесено из `approval_facts` (ревью круга 6 M6-1): тот на уровне модуля
импортирует `ops`/`facts`, и гейт, читая политику через него, исполнял бы
незащищённый код. Здесь — только то, что решает доверие: координаты
источника (SSOT под authority-root; `[policy]` конфига DarkFactory обязан с
ним совпасть) и разбор состава.
"""

from __future__ import annotations

import os
from pathlib import Path

from governance import df_config, ssot_env

#: Ключ политики в `policy/approvers.env` репозитория `approval-policy` — И имя
#: переменной окружения, выставление которой теперь есть ОТКАЗ (спека
#: approval-policy S7): переменная больше не источник, а молчаливое
#: игнорирование оставило бы оператора, действующего по старому правилу, в
#: уверенности, что его намерение исполняется. Одно имя в двух местах —
#: намеренно: правило волта и файл политики читаются одним словарём. Окружение
#: здесь не читается: отказ S7 — `approval_facts.env_override_refusal`.
APPROVER_ALLOWLIST_ENV = "AUTHORIZED_APPROVER_ACCOUNTS"


#: Координаты источника политики — SSOT под authority-root (S8): константу
#: в модуле агент перенаправил бы своим PR под агентским мержем. Единственный
#: источник в любом режиме: `[policy]` конфига DarkFactory (домашний каталог,
#: `XDG_CONFIG_HOME` вызывающего) его не перенаправляет, а обязан совпасть.
POLICY_SOURCE_FILE = (
    Path(__file__).resolve().parent.parent
    / "contracts"
    / "approval-policy-source"
    / "v1"
    / "source.env"
)


def policy_source() -> tuple[str, str, str]:
    """(repo, ref, path) из SSOT `POLICY_SOURCE_FILE`; RuntimeError на отказе.

    Режим конфига доверие не выбирает: источник — всегда SSOT под
    authority-root. `[policy]` конфига DarkFactory (`df_config`, читается при
    каждом вызове), если задан, обязан совпасть с SSOT — расхождение есть
    отказ, а не выбор стороны. Файла конфига нет (даже висячей ссылки) — сверять нечего (гейт CI).
    Конфиг есть, но битый, или битый SSOT — тот же RuntimeError, что ловят
    вызывающие."""
    ssot = _ssot_source()
    if not os.path.lexists(df_config.config_path(os.environ)):
        return ssot  # конфига нет — нет и `[policy]`, сверять нечего
    try:
        cfg = df_config.load()
    except df_config.ConfigError as exc:
        raise RuntimeError(f"источник политики подписи: {exc}") from exc
    if cfg.policy is not None:
        configured = (cfg.policy.repo, cfg.policy.ref, cfg.policy.path)
        if configured != ssot:
            raise RuntimeError(
                f"источник политики подписи: config [policy] расходится с "
                f"authority-root SSOT: конфиг {configured}, "
                f"{POLICY_SOURCE_FILE} {ssot}"
            )
    return ssot


def _ssot_source() -> tuple[str, str, str]:
    what = "SSOT источника политики подписи"
    return (
        ssot_env.read_key(POLICY_SOURCE_FILE, "APPROVAL_POLICY_REPO", what),
        ssot_env.read_key(POLICY_SOURCE_FILE, "APPROVAL_POLICY_REF", what),
        ssot_env.read_key(POLICY_SOURCE_FILE, "APPROVAL_POLICY_PATH", what),
    )


def policy_accounts(content: str) -> frozenset[str] | None:
    """Состав политики из `approvers.env` (тем же правилом, что `policy_snapshot`):
    ключа нет, дубль или ни одного логина — None."""
    lines = ssot_env.definition_lines(content, APPROVER_ALLOWLIST_ENV)
    if len(lines) != 1 or not lines[0]:
        return None
    accounts = frozenset(p.strip() for p in lines[0].split(",") if p.strip())
    return accounts or None
