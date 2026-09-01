"""Приёмка integration-PR spec-runner'а одним вызовом (решение владельца
2026-09-01, вариант «а»).

spec-runner, завершив цикл, открывает integration-PR и останавливается
(«Merge it before the next run, then `spec-runner sync`») — между его PR и
следующим прогоном стоял ручной ритуал: терминальное ревью → ожидание чеков
→ DarkFactory-мерж. Эта команда кодирует ритуал:

1. терминальное ревью (`review-pr.sh` через `Ops.review`; fp-дедуп кита
   делает повторные вызовы дешёвыми); находки → стоп, отработка — человеком
   или фикс-коммитами на ветку PR, затем повторный вызов;
2. полное ожидание завершения ВСЕХ чеков (урок инцидента kapelle#57: «sleep
   и глазами» — не ритуал); любой красный → стоп без мержа;
3. гарды перед агентским мержем: authority-root пути (`.github/`,
   `profiles/`) — всегда человеку (ADR-ECO-004 I2); немержабельный PR — стоп;
4. мерж от ai-prosto (`Ops.merge`, PUT с пином head), затем подсказка
   `spec-runner sync`.

CLI: ``python -m governance.accept_pr --repo kapelle --pr 59``
(``make accept-pr ARGS='--repo kapelle --pr 59'``); ``--owner`` — дефолт
andrei-shtanakov. Полный цикл «sync + повторный run до remaining=0» —
вариант (в), сюда намеренно не входит.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable

from governance.ops import Ops, RealOps

_AUTHORITY_PREFIXES = (".github/", "profiles/")
_PENDING = {"PENDING", "IN_PROGRESS", "QUEUED", "WAITING", "REQUESTED", ""}
_GREEN = {"SUCCESS", "NEUTRAL", "SKIPPED"}

# Потолок ожидания чеков: poll_limit опросов с шагом poll_seconds.
_POLL_LIMIT = 40
_POLL_SECONDS = 30


def _checks_state(pr_facts: dict) -> str:
    """`green` | `red` | `pending` по statusCheckRollup (fail-closed).

    Пустой rollup — `pending`, не green (приёмка PR #109, major): пустота
    двусмысленна — «чеков нет вовсе» неотличимо от «чеки ещё не создались»
    на свежем push, и green-чтение мержило бы до старта CI. Репо совсем без
    чеков упрётся в потолок опроса и уйдёт на человека — fail-closed.
    """
    checks = pr_facts.get("statusCheckRollup") or []
    if not checks:
        return "pending"
    states = [
        (c.get("conclusion") or c.get("status") or "").upper() for c in checks
    ]
    if any(s in _PENDING for s in states):
        return "pending"
    if all(s in _GREEN for s in states):
        return "green"
    return "red"


def accept(
    repo: str,
    repo_slug: str,
    pr: int,
    ops: Ops,
    sleep: Callable[[float], None] = time.sleep,
    poll_limit: int = _POLL_LIMIT,
) -> int:
    """Ритуал приёмки; коды: 0 смержено, 1 стоп (причина на stdout).

    Порядок «ревью до ожидания чеков» намеренный: находки дороже минут CI,
    и красное ревью не должно ждать зелёного rollup, чтобы быть увиденным.
    """
    # Head фиксируется ДО ревью (приёмка PR #109, major): пуш между ревью
    # и мержем подменил бы содержимое, которого ревью не видело. Совпадение
    # проверяется после ожидания чеков; мерж идёт с пином именно этого head
    # (PUT sha= — вторая линия той же гарантии).
    head0 = ops.pr_facts(repo_slug, pr).get("headRefOid")
    if not head0:
        print("accept-pr: не удалось определить head PR — стоп")
        return 1
    review_exit = ops.review(repo, pr)
    if review_exit != 0:
        print(
            f"accept-pr: терминальное ревью вернуло {review_exit} — стоп; "
            "отработайте находки (фикс-коммиты на ветку PR) и повторите"
        )
        return 1

    facts = ops.pr_facts(repo_slug, pr)
    polls = 0
    while _checks_state(facts) == "pending":
        polls += 1
        if polls > poll_limit:
            print("accept-pr: чеки не завершились за отведённое время — стоп")
            return 1
        sleep(_POLL_SECONDS)
        facts = ops.pr_facts(repo_slug, pr)
    if _checks_state(facts) == "red":
        print("accept-pr: красные чеки — мержа не будет (урок kapelle#57)")
        return 1

    files = ops.pr_files(repo_slug, pr)
    authority = [
        f for f in files
        if any(f.startswith(p) for p in _AUTHORITY_PREFIXES)
    ]
    if authority:
        print(
            "accept-pr: дифф трогает authority-root пути "
            f"({', '.join(sorted(set(authority))[:5])}…) — мерж только "
            "человеком (ADR-ECO-004 I2)"
        )
        return 1
    if facts.get("mergeable") == "CONFLICTING":
        print("accept-pr: PR конфликтует с базой — стоп")
        return 1

    if facts.get("headRefOid") != head0:
        print(
            "accept-pr: head PR уехал после ревью "
            f"({str(facts.get('headRefOid'))[:7]} != {head0[:7]}) — "
            "повторите приёмку"
        )
        return 1
    head = head0
    if not ops.merge(repo_slug, pr, head):
        print("accept-pr: мерж не прошёл (гонка head / правило репо) — стоп")
        return 1
    print(
        f"accept-pr: {repo_slug}#{pr} смержен (head {head[:7]}). "
        f"Дальше в {repo}: `spec-runner sync`, затем следующий run."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI-обвязка; вся политика — в `accept()` (тестируется стабом Ops)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="имя репо (kapelle)")
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--owner", default="andrei-shtanakov")
    args = parser.parse_args(argv)
    return accept(
        args.repo, f"{args.owner}/{args.repo}", args.pr, RealOps(),
    )


if __name__ == "__main__":
    raise SystemExit(main())
