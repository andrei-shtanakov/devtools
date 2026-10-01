"""Стоп-кран DarkFactory: можно ли начинать НОВУЮ работу агента в репо (D2).

Правило — `contracts/halt-admission/v1` (продюсер — github-checker,
вендорено с пином; `decide` проверяется векторами оттуда). Решает одно:
enforcement набора правил `darkfactory-halt`, видимый токеном уровня write.

- `disabled` → пускать; набора с таким именем нет → пускать (решение
  владельца 2026-10-01: снять или удалить набор может только admin, а admin
  и так обходит стоп — пропущенный взвод это отклонение, которое показывает
  dispatcher, а не обход);
- `active`, дубликат имени, иное enforcement, любое непрочитанное → отказ.

Только stdlib: модуль зовёт и `merge-pr.sh` (`python3 -m governance.halt_gate
<owner/name>`, профиль — переданный GH_CONFIG_DIR), и раннер.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

HALT_RULESET = "darkfactory-halt"
#: Код выхода «стоп-кран»: тот же, что у merge-pr.sh (6).
EXIT_HALTED = 6

Decision = tuple[bool, str, str]


class HaltedError(ValueError):
    """Отказ стоп-крана: новая работа не начинается (код выхода 6).

    ValueError — как прочие отказы допуска раннера (`start` бросает их до
    `_reserve_run_id`), чтобы любой вызывающий, ловящий отказы, ловил и этот.
    """


def decide(
    listing: list[dict[str, Any]] | None, detail: dict[str, Any] | None
) -> Decision:
    """(admit, code, reason) — таблица контракта halt-admission/v1."""
    if listing is None:
        return False, "refuse_unknown", "rulesets could not be listed"
    named = [r for r in listing if r.get("name") == HALT_RULESET]
    if not named:
        return True, "admit_missing", "no darkfactory-halt ruleset (not armed)"
    if len(named) > 1:
        return False, "refuse_duplicate", f"{len(named)} rulesets named {HALT_RULESET}"
    if detail is None:
        return False, "refuse_unknown", "the halt ruleset could not be read"
    enforcement = detail.get("enforcement")
    if enforcement == "disabled":
        return True, "admit_off", "halt is off"
    if enforcement == "active":
        return False, "refuse_on", "the DarkFactory halt is ON for this repository"
    return False, "refuse_enforcement", f"halt enforcement {enforcement!r}"


def _gh_json(*args: str) -> Any | None:
    try:
        done = subprocess.run(
            ["gh", "api", *args],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError:
        return None


def check(slug: str) -> Decision:
    """Прочитать стоп репо *slug* (`owner/name`) и решить. Не бросает."""
    pages = _gh_json(
        "--paginate", "--slurp", f"repos/{slug}/rulesets?includes_parents=false"
    )
    listing: list[dict[str, Any]] | None
    try:
        listing = (
            [dict(r) for page in pages for r in page] if pages is not None else None
        )
    except (TypeError, ValueError):
        listing = None
    detail = None
    if listing is not None:
        named = [r for r in listing if r.get("name") == HALT_RULESET]
        if len(named) == 1:
            body = _gh_json(f"repos/{slug}/rulesets/{named[0].get('id')}")
            detail = body if isinstance(body, dict) else None
    return decide(listing, detail)


def refusal(slug: str) -> str | None:
    """None — пускать; иначе текст отказа с кодом."""
    admit, code, reason = check(slug)
    return None if admit else f"стоп-кран DarkFactory ({code}): {reason}"


def main(argv: list[str] | None = None) -> int:
    """`python3 -m governance.halt_gate owner/name` → 0 пускать, 6 отказ."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or "/" not in args[0]:
        print("usage: python3 -m governance.halt_gate <owner/name>", file=sys.stderr)
        return 2
    admit, code, reason = check(args[0])
    print(f"{code}: {reason}")
    return 0 if admit else EXIT_HALTED


if __name__ == "__main__":
    raise SystemExit(main())
