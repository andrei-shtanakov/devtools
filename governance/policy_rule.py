"""policy_rule — источник и состав политики подписи (правило гейта [x]).

Вынесено из `approval_facts` (ревью круга 6 M6-1): тот на уровне модуля
импортирует `ops`/`facts`, и гейт, читая политику через него, исполнял бы
незащищённый код. Здесь — только то, что решает доверие: координаты
источника (SSOT под authority-root; `[policy]` конфига DarkFactory обязан с
ним совпасть; в `mode = "test"` — синтетическая политика из `accounts.human`)
и разбор состава.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
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
#: репо-источник (в `test` репо политики нет вовсе — `TEST_POLICY_SOURCE`):
#: `[policy]` конфига DarkFactory (домашний каталог, `XDG_CONFIG_HOME`
#: вызывающего) его не перенаправляет, а обязан совпасть.
POLICY_SOURCE_FILE = (
    Path(__file__).resolve().parent.parent
    / "contracts"
    / "approval-policy-source"
    / "v1"
    / "source.env"
)


#: Служебные координаты синтетической политики `mode = "test"` (план 3a):
#: утверждающий — `accounts.human` конфига, репо политики нет. `repo` без `/`
#: — не слаг GitHub: факты отвечают на него из конфига, не ходя в сеть.
TEST_POLICY_SOURCE = ("darkfactory-config", "test", "accounts.human")

#: Отказ `test_approver_changed`: синтетический источник знает только текущую
#: версию, пин предложения после смены `accounts.human` неизвестен.
TEST_APPROVER_CHANGED = (
    "утверждающий в конфиге сменился после предложения брифа — сделайте "
    "новое предложение (brief-propose), при необходимости новый прогон"
)


@dataclass(frozen=True)
class TestPolicy:
    """Синтетическая политика test: содержимое и его версия (sha256[:40]).

    Версия — идентификатор, не защита: конфиг — данные человека."""

    __test__ = False  # не тестовый класс pytest

    sha: str
    content: str


def test_policy() -> TestPolicy | None:
    """Текущая синтетическая политика; None — конфига нет или `strict`.

    Битый конфиг — RuntimeError, как у `policy_source`."""
    cfg = _config()
    if cfg is None or cfg.mode != "test":
        return None
    content = f"{APPROVER_ALLOWLIST_ENV}={cfg.accounts.human}\n"
    sha = hashlib.sha256(content.encode("utf-8")).hexdigest()[:40]
    return TestPolicy(sha=sha, content=content)


test_policy.__test__ = False  # type: ignore[attr-defined]


def policy_source() -> tuple[str, str, str]:
    """(repo, ref, path) источника политики подписи; RuntimeError на отказе.

    `mode = "test"` — `TEST_POLICY_SOURCE`: ни SSOT, ни `[policy]` не
    читаются (репо политики не нужен, план 3a). Иначе источник — SSOT под
    authority-root: режим доверие не выбирает. `[policy]` конфига
    DarkFactory (`df_config`, читается при каждом вызове), если задан, обязан
    совпасть с SSOT — расхождение есть отказ, а не выбор стороны. Файла
    конфига нет (даже висячей ссылки) — сверять нечего (гейт CI).
    Конфиг есть, но битый, или битый SSOT — тот же RuntimeError, что ловят
    вызывающие (порядок отказов — прежний: сперва SSOT)."""
    try:
        cfg = _config()
    except RuntimeError as exc:
        cfg, config_error = None, exc
    else:
        config_error = None
    if cfg is not None and cfg.mode == "test":
        return TEST_POLICY_SOURCE
    ssot = _ssot_source()
    if config_error is not None:
        raise config_error
    if cfg is not None and cfg.policy is not None:
        configured = (cfg.policy.repo, cfg.policy.ref, cfg.policy.path)
        if configured != ssot:
            raise RuntimeError(
                f"источник политики подписи: config [policy] расходится с "
                f"authority-root SSOT: конфиг {configured}, "
                f"{POLICY_SOURCE_FILE} {ssot}"
            )
    return ssot


def _config() -> df_config.DfConfig | None:
    """Конфиг DarkFactory; файла нет (даже висячей ссылки) — None."""
    if not os.path.lexists(df_config.config_path(os.environ)):
        return None
    try:
        return df_config.load()
    except df_config.ConfigError as exc:
        raise RuntimeError(f"источник политики подписи: {exc}") from exc


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
