"""Предпроверка агентской доставки candidate `review/v1` (спека §8.2, §9.1).

Этот файл ИСПОЛНЯЕТ `merge-pr.sh` — файлом, в своём окружении:
`uv run --frozen --exact --group governance --no-config --no-env-file python -I
<этот файл> …` (форму распознаёт `tests/harness_trust.py`). Цепочка импортов —
только защищённые `governance/act_*`, `facts`, `ssot_env`, `df_config`; журнал периметра —
узкий клиент `act_journal_wire` (без `df/`, ревизия b1): уже зарегистрированный
блок останавливает мерж, обнаруженное здесь нарушение регистрируется. Коды:
0 — мерж разрешён (граница секунды выдержана), 3 — отказ, 2 — не установлено.
Предпроверка не подпись: окончательно решает `check_delivery` (Task 13).

`node_approval` и `stale_adapter` в цепочку не входят: проекция self-hash
(`act_projection`) здесь не импортируется. Если правка их втянет, инвариант
`tests/harness_trust.py` потребует внести их в authority-root и
`_HARNESS_PREFIXES` (п. 8 заметок части A).

The check itself lives in `act_premerge_core` (df stage 3a, Task 7, review D1):
the precheck program of df imports the core and never this CLI, whose App mode
(`act_app_installations` → `act_app_token`) pulls `jwt`. The names below are
re-exported for the callers of this module.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, "") and "governance" not in sys.modules:
    # Исполнение файлом под `python -I`: пакет — явно по пути, без дерева в
    # sys.path (тот же приём, что `brief_merge_check.py`).
    import importlib.util

    _pkg_dir = Path(__file__).resolve().parent
    _spec = importlib.util.spec_from_file_location(
        "governance",
        _pkg_dir / "__init__.py",
        submodule_search_locations=[str(_pkg_dir)],
    )
    assert _spec is not None and _spec.loader is not None
    _pkg = importlib.util.module_from_spec(_spec)
    sys.modules["governance"] = _pkg
    _spec.loader.exec_module(_pkg)

import argparse  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402
from collections.abc import Mapping, Sequence  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from typing import Any  # noqa: E402

from governance import (  # noqa: E402
    act_app_installations,
    act_facts,
    act_journal_wire,
    act_trust,
    df_config,
)
from governance.act_premerge_core import (  # noqa: E402
    EXIT_OK,
    EXIT_REFUSED,
    EXIT_UNSET,
    REGISTERED,
    Installations,
    PremergeDeps,
    Surface,
    decide,
    request_of_branch,
)
from governance.act_time import Unavailable  # noqa: E402

__all__ = [
    "EXIT_OK",
    "EXIT_REFUSED",
    "EXIT_UNSET",
    "REGISTERED",
    "Installations",
    "PremergeDeps",
    "Surface",
    "decide",
    "main",
    "real_deps",
    "request_of_branch",
]


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="act_premerge")
    for name in ("--repo", "--head", "--head-ref", "--merge-profile", "--merger"):
        p.add_argument(name, required=True)
    p.add_argument("--pr", required=True, type=int)
    p.add_argument("--merger-type", required=True, choices=("User", "Bot"))
    p.add_argument("--app-id", type=int, default=None)
    p.add_argument("--app-key", type=Path, default=None)
    return p


def real_deps(
    args: argparse.Namespace, env: Mapping[str, str] | None = None
) -> PremergeDeps:
    """Боевые чтения: GraphQL под профилем агента, привязка, журнал периметра,
    самоотчёт `gh`; транспорт источников доверия строит `load_trust` (Task 11c).
    Профиль агента — `REVIEW_GH_CONFIG_DIR`, иначе `accounts.agent_gh_config_dir`
    конфига DarkFactory (`df_config`); битый конфиг — `ConfigError`."""
    env = dict(os.environ) if env is None else env
    home = Path.home()
    agent = df_config.agent_gh_config_dir(env)
    binding = act_trust.load_binding()
    journal: Any = Unavailable("нет привязки")
    if isinstance(binding, act_trust.Binding):
        journal = act_journal_wire.journal_for_premerge(
            env=env,
            state_home=Path(env.get("XDG_STATE_HOME") or home / ".local" / "state"),
            run_id=f"merge-pr:{args.repo}#{args.pr}",
            policy_repository_id=binding.approval_policy_repository_id,
        )
    return PremergeDeps(
        readers=act_trust.default_readers(),
        gh=act_facts.gh_transport(agent),
        binding=binding,
        journal=journal,
        now=lambda: datetime.now(UTC).replace(microsecond=0),
        sleep=time.sleep,
        creds=act_trust.credentials(
            env, agent_dir=agent, merge_dir=Path(args.merge_profile)
        ),
        installations=(
            None
            if args.app_id is None
            else act_app_installations.app_installations(args.app_id, args.app_key)
        ),
    )


def main(argv: Sequence[str] | None = None, deps: PremergeDeps | None = None) -> int:
    """CLI предпроверки; см. докстринг модуля."""
    parser = _parser()
    try:
        args = parser.parse_args(argv)
        if (args.app_id is None) != (args.app_key is None):
            parser.error("App-режим: нужны оба --app-id и --app-key")
    except SystemExit:
        # argparse (и `--help`): предпроверки не было — отказ 3, «не мержить и не
        # повторять»; 2 `merge-pr.sh` читает как «повтор уместен», а расхождение
        # версий обвязки повтор не лечит (интеграционное ревью части B, m-7).
        return EXIT_REFUSED
    if deps is None:
        try:
            deps = real_deps(args)
        except df_config.ConfigError as exc:
            # Конфиг установки битый — тот же код 2 (конфигурация), что у
            # скриптов, а не 3 («предпроверка отказала»). merge-pr.sh передаёт
            # профиль мержа аргументом `--merge-profile` и REVIEW_GH_CONFIG_DIR
            # не экспортирует: профиль агента здесь — env, иначе конфиг.
            print(f"act_premerge: {exc}", file=sys.stderr)
            return EXIT_UNSET
    code, reason = decide(args, deps)
    if code != EXIT_OK:
        print(f"act_premerge: {reason}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
