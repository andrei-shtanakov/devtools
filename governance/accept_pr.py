"""Приёмка integration-PR spec-runner'а одним вызовом (решение владельца
2026-09-01, вариант «а»).

spec-runner, завершив цикл, открывает integration-PR и останавливается
(«Merge it before the next run, then `spec-runner sync`») — между его PR и
следующим прогоном стоял ручной ритуал: терминальное ревью → ожидание чеков
→ DarkFactory-мерж. Эта команда кодирует ритуал:

0. материализация head PR в локальном чекауте цели (ретроспектива
   2026-09-02, уроки 7 и «грязное дерево»): review-kit считает локальное
   дерево авторитетным — чекаут на master даёт ложное «реализации нет»,
   грязное дерево — ложную фактуру находок. Перед ревью: гард чистого
   дерева → fetch pull/<n>/head → detached switch на пинованный head →
   гард путей по ЛОКАЛЬНОМУ диффу материализованного head0 (ревью-harness
   `scripts/review/` и authority-root — стоп ДО запуска ревью: PR не
   должен получить исполнение своего `local.sh` у оператора, а API-список
   файлов не привязан к head0 — TOCTOU; приёмка PR #113, blocker + круг 2);
   исходная ветка возвращается после приёмки на любом исходе;
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
import re
import subprocess
import time
from collections.abc import Callable

from governance.ops import DEVTOOLS_ROOT, Ops, RealOps

_AUTHORITY_PREFIXES = (".github/", "profiles/")
# Исполняемый ревью-harness целевого репо: review-pr.sh запускает
# scripts/review/local.sh из локального дерева, которое материализация
# переключает на head PR (приёмка PR #113, blocker) — PR, правящий эти
# пути, не ревьюится агентом вовсе (гард по локальному диффу head0).
_HARNESS_PREFIXES = ("scripts/review/",)
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
    target_dir: str,
    sleep: Callable[[float], None] = time.sleep,
    poll_limit: int = _POLL_LIMIT,
) -> int:
    """Ритуал приёмки; коды: 0 смержено, 1 стоп (причина на stdout).

    Порядок «ревью до ожидания чеков» намеренный: находки дороже минут CI,
    и красное ревью не должно ждать зелёного rollup, чтобы быть увиденным.
    """
    # Гард чистого дерева ДО материализации: review-kit читает локальный
    # чекаут, и незакоммиченные правки стали бы «фактурой» ревью
    # (dispatcher#235, круг 1); к тому же switch мог бы их потерять.
    if ops.is_dirty(target_dir):
        print(
            f"accept-pr: рабочее дерево {target_dir} грязное — ревью по "
            "локальному чекауту дало бы ложную фактуру — стоп"
        )
        return 1
    branch0 = ops.current_branch(target_dir)
    if branch0 is None:
        print(
            f"accept-pr: чекаут {target_dir} в detached HEAD — не определить "
            "ветку возврата — стоп"
        )
        return 1
    # Head фиксируется ДО ревью (приёмка PR #109, major): пуш между ревью
    # и мержем подменил бы содержимое, которого ревью не видело. Совпадение
    # проверяется после ожидания чеков; мерж идёт с пином именно этого head
    # (PUT sha= — вторая линия той же гарантии).
    facts0 = ops.pr_facts(repo_slug, pr)
    head0 = facts0.get("headRefOid")
    if not head0:
        print("accept-pr: не удалось определить head PR — стоп")
        return 1
    base_branch = facts0.get("baseRefName")
    if not base_branch:
        print("accept-pr: не удалось определить base-ветку PR — стоп")
        return 1
    # Урок 7 (devtools#110): ревью обязано смотреть на дерево именно этого
    # head — detached switch на пинованный sha, а не на ветку PR, чтобы
    # гонка с параллельным push не подменила проверяемое содержимое.
    try:
        ops.materialize_pr_head(target_dir, pr, head0)
    except RuntimeError as exc:
        print(f"accept-pr: не удалось материализовать head PR ({exc}) — стоп")
        ops.ensure_branch(target_dir, branch0)
        return 1
    try:
        return _accept_on_head(
            repo, repo_slug, pr, ops, head0,
            target_dir, base_branch, sleep, poll_limit,
        )
    except RuntimeError as exc:
        print(f"accept-pr: {exc} — стоп")
        return 1
    finally:
        ops.ensure_branch(target_dir, branch0)


def _accept_on_head(
    repo: str,
    repo_slug: str,
    pr: int,
    ops: Ops,
    head0: str,
    target_dir: str,
    base_branch: str,
    sleep: Callable[[float], None],
    poll_limit: int,
) -> int:
    """Ревью → чеки → гарды → мерж; чекаут цели уже стоит на head0."""
    # Гард путей по МАТЕРИАЛИЗОВАННОМУ head0, до исполнения harness
    # (приёмка PR #113, blocker + круг 2): review-pr.sh исполняет
    # scripts/review/local.sh из локального дерева — PR, правящий
    # ревью-harness или authority-root, получил бы исполнение своего кода
    # у оператора до вердикта. API-список файлов PR отражает голову ветки
    # на момент запроса и НЕ привязан к head0 (TOCTOU при force-push
    # между запросами) — изменённые пути считаются локально по
    # переключённому дереву: git diff origin/<base>...HEAD.
    changed = ops.changed_paths(target_dir, base_branch)
    harness = [
        f for f in changed
        if any(f.startswith(p) for p in _HARNESS_PREFIXES)
    ]
    if harness:
        print(
            "accept-pr: дифф правит ревью-harness "
            f"({', '.join(sorted(set(harness))[:5])}…) — ревью и мерж "
            "только человеком"
        )
        return 1
    authority = [
        f for f in changed
        if any(f.startswith(p) for p in _AUTHORITY_PREFIXES)
    ]
    if authority:
        print(
            "accept-pr: дифф трогает authority-root пути "
            f"({', '.join(sorted(set(authority))[:5])}…) — мерж только "
            "человеком (ADR-ECO-004 I2)"
        )
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
    # UNKNOWN-mergeability тоже ждём (приёмка PR #109, круг 2): GitHub
    # вычисляет её асинхронно, и «не CONFLICTING» не значит «можно» —
    # fail-closed, мерж только на явном MERGEABLE.
    while (
        _checks_state(facts) == "pending"
        or facts.get("mergeable") not in ("MERGEABLE", "CONFLICTING")
    ):
        polls += 1
        if polls > poll_limit:
            print(
                "accept-pr: чеки/mergeability не определились за отведённое "
                "время — стоп"
            )
            return 1
        sleep(_POLL_SECONDS)
        facts = ops.pr_facts(repo_slug, pr)
    if _checks_state(facts) == "red":
        print("accept-pr: красные чеки — мержа не будет (урок kapelle#57)")
        return 1

    if facts.get("mergeable") != "MERGEABLE":
        print(
            f"accept-pr: mergeability = {facts.get('mergeable')!r} — стоп "
            "(мерж только на явном MERGEABLE)"
        )
        return 1

    if facts.get("headRefOid") != head0:
        print(
            "accept-pr: head PR уехал после ревью "
            f"({str(facts.get('headRefOid'))[:7]} != {head0[:7]}) — "
            "повторите приёмку"
        )
        return 1
    # Пин base симметричен пину head (приёмка PR #113, круг 3): ретаргет
    # PR на другую base-ветку меняет фактический дифф при том же head —
    # гард путей считался относительно base_branch и к новой базе
    # неприменим.
    if facts.get("baseRefName") != base_branch:
        print(
            "accept-pr: base-ветка PR сменилась после гарда путей "
            f"({facts.get('baseRefName')!r} != {base_branch!r}) — "
            "повторите приёмку"
        )
        return 1
    head = head0
    if not ops.merge(repo_slug, pr, head):
        print("accept-pr: мерж не прошёл (гонка head / правило репо) — стоп")
        return 1
    print(
        f"accept-pr: {repo_slug}#{pr} смержен (head {head[:7]}). "
        f"Дальше в {repo}: `git pull --ff-only`, `spec-runner sync`, "
        "затем следующий run."
    )
    return 0


def _origin_slug(url: str) -> str | None:
    """`owner/name` из origin-URL (ssh/https, .git-суффикс опционален)."""
    match = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$", url.strip())
    return f"{match.group(1)}/{match.group(2)}".lower() if match else None


def main(argv: list[str] | None = None) -> int:
    """CLI-обвязка; вся политика — в `accept()` (тестируется стабом Ops).

    Гард владельца (приёмка PR #109, круг 3): `review` работает через
    ЛОКАЛЬНЫЙ чекаут `../<repo>` (так устроен review-pr.sh), а `merge` — по
    слагу `--owner/--repo`; расхождение означало бы «отревьюили один репо,
    смержили другой». Origin чекаута обязан совпасть со слагом.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="имя репо (kapelle)")
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--owner", default="andrei-shtanakov")
    args = parser.parse_args(argv)
    repo_slug = f"{args.owner}/{args.repo}".lower()
    target_dir = str(DEVTOOLS_ROOT.parent / args.repo)
    origin = subprocess.run(
        ["git", "-C", target_dir, "remote", "get-url", "origin"],
        capture_output=True, text=True,
    )
    checkout_slug = (
        _origin_slug(origin.stdout) if origin.returncode == 0 else None
    )
    if checkout_slug != repo_slug:
        print(
            f"accept-pr: origin локального чекаута ../{args.repo} = "
            f"{checkout_slug!r}, а мерж адресован {repo_slug!r} — стоп "
            "(ревью и мерж обязаны смотреть в один репозиторий)"
        )
        return 2
    return accept(args.repo, repo_slug, args.pr, RealOps(), target_dir)


if __name__ == "__main__":
    raise SystemExit(main())
