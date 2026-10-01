"""Стоп-кран DarkFactory: можно ли начинать НОВУЮ работу агента в репо (D2).

Правило — `contracts/halt-admission/v1` (продюсер — github-checker,
вендорено с пином; `decide` проверяется векторами оттуда). Решает одно:
enforcement набора правил `darkfactory-halt`, видимый токеном уровня write.

- `disabled` → пускать; набора с таким именем нет → пускать (решение
  владельца 2026-10-01: снять или удалить набор может только admin, а admin
  и так обходит стоп — пропущенный взвод это отклонение, которое показывает
  dispatcher, а не обход);
- `active`, дубликат имени, иное enforcement, любое непрочитанное → отказ.

Только stdlib и никаких импортов из дерева: `merge-pr.sh` исполняет ЭТОТ
ФАЙЛ изолированно — `python3 -I governance/halt_gate.py <owner/name>`, профиль
— переданный GH_CONFIG_DIR (безопасность вызова держится на `-I`: без
PYTHONPATH и user site, без каталогов дерева в sys.path). Раннер импортирует
модуль обычным образом. Коды выхода: 0 — пускать, 6 — стоп (включён, дубликат,
иное enforcement), 2 — стоп не прочитан (повтор уместен, как у прочих
неустановленных фактов merge-pr.sh).
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

HALT_RULESET = "darkfactory-halt"
#: Код выхода «стоп-кран»: тот же, что у merge-pr.sh (6).
EXIT_HALTED = 6
#: Стоп не прочитан: факт не установлен, повтор уместен (код 2 merge-pr.sh).
EXIT_UNREAD = 2

Decision = tuple[bool, str, str]


class HaltedError(ValueError):
    """Отказ стоп-крана: новая работа не начинается.

    ValueError — как прочие отказы допуска раннера (`start` бросает их до
    `_reserve_run_id`), чтобы любой вызывающий, ловящий отказы, ловил и этот.
    `unread` — стоп не прочитан (код 2), иначе стоп действует (код 6).
    """

    def __init__(self, message: str, *, unread: bool = False) -> None:
        super().__init__(message)
        self.unread = unread

    @property
    def exit_code(self) -> int:
        return EXIT_UNREAD if self.unread else EXIT_HALTED


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


def _gh(*args: str) -> str | None:
    """stdout of `gh api …`, or None when the call failed."""
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
    return done.stdout if done.returncode == 0 else None


def _gh_json(*args: str) -> Any | None:
    out = _gh(*args)
    if out is None:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def _listing(slug: str) -> list[dict[str, Any]] | None:
    """Every ruleset as `{id, name}`, or None when unread.

    `--paginate` with gh's built-in `--jq`, one JSON line per ruleset —
    not `--slurp`, which needs a recent gh: an old gh on the merge path would
    have read as "unknown" on every repo (review devtools#531).
    """
    out = _gh(
        "--paginate",
        f"repos/{slug}/rulesets?includes_parents=false",
        "--jq",
        ".[] | [.id, .name] | @json",
    )
    if out is None:
        return None
    try:
        pairs = [json.loads(line) for line in out.splitlines() if line.strip()]
        return [{"id": rid, "name": name} for rid, name in pairs]
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def check(slug: str) -> Decision:
    """Прочитать стоп репо *slug* (`owner/name`) и решить. Не бросает."""
    listing = _listing(slug)
    detail = None
    if listing is not None:
        named = [r for r in listing if r.get("name") == HALT_RULESET]
        if len(named) == 1:
            body = _gh_json(f"repos/{slug}/rulesets/{named[0].get('id')}")
            detail = body if isinstance(body, dict) else None
    return decide(listing, detail)


def refusal(slug: str) -> HaltedError | None:
    """None — пускать; иначе отказ (с признаком «не прочитан»)."""
    admit, code, reason = check(slug)
    if admit:
        return None
    return HaltedError(
        f"стоп-кран DarkFactory ({code}): {reason}", unread=code == "refuse_unknown"
    )


def main(argv: list[str] | None = None) -> int:
    """`python3 -I governance/halt_gate.py owner/name` → 0 / 6 / 2."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or "/" not in args[0]:
        print("usage: python3 -I governance/halt_gate.py <owner/name>", file=sys.stderr)
        return 2
    admit, code, reason = check(args[0])
    print(f"{code}: {reason}")
    if admit:
        return 0
    return EXIT_UNREAD if code == "refuse_unknown" else EXIT_HALTED


if __name__ == "__main__":
    raise SystemExit(main())
