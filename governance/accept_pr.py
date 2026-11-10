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
4. мерж от учётки агента (`Ops.merge`, PUT с пином head), затем подсказка
   `spec-runner sync`.

CLI: ``python -m governance.accept_pr --repo kapelle --pr 59``
(``make accept-pr ARGS='--repo kapelle --pr 59'``); ``--owner``
необязателен: по умолчанию владелец берётся из origin локального чекаута цели.
Полный цикл «sync + повторный run до remaining=0» — вариант (в), сюда
намеренно не входит.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from governance import authority_root, df_config, ssot_env, user_targets
from governance.approve_node import _NO_CHECKS_GRACE_POLLS
from governance.facts import Outcome
from governance.ops import (
    REVIEW_BARRIER_EXIT,
    REVIEW_BARRIER_STOP,
    Ops,
    RealOps,
)
from governance.pr_checks import NO_CHECKS_TEST, no_checks
from governance.review_verdict import ReviewVerdict, parse_marker

# Харнесс-пути — входы обвязки ревью и мержа, исполняемые или читаемые из
# дерева проверяемого PR: PR, который их трогает, не ревьюится агентом вовсе
# (гард по локальному диффу head0). Перечень и пояснения к каждому входу —
# в SSOT `contracts/harness-paths/v1/paths.env`: его же читает `merge-pr.sh`
# под `--authority-override` (дизайн darkfactory-config §5.8, D10), и второе
# определение в shell разошлось бы с кортежем молча.
#
# Читается ПРИ ИМПОРТЕ и намеренно: модуль импортирован до материализации
# head PR, а для PR в сам devtools материализуется ровно это дерево — ленивое
# чтение взяло бы перечень из проверяемого патча. Битый файл — исключение при
# импорте (fail-closed `ssot_env.read_key`), а не пустой перечень.
# Имя атрибута сохранено: его читает `tests/harness_trust.py`.
HARNESS_PATHS_FILE = (
    Path(__file__).resolve().parent.parent
    / "contracts"
    / "harness-paths"
    / "v1"
    / "paths.env"
)
_HARNESS_PREFIXES: tuple[str, ...] = tuple(
    ssot_env.read_key(
        HARNESS_PATHS_FILE, "HARNESS_PREFIXES", "SSOT харнесс-путей"
    ).split()
)

_PENDING = {"PENDING", "IN_PROGRESS", "QUEUED", "WAITING", "REQUESTED", ""}
_GREEN = {"SUCCESS", "NEUTRAL", "SKIPPED"}

# Потолок ожидания чеков: poll_limit опросов с шагом poll_seconds.
_POLL_LIMIT = 40
_POLL_SECONDS = 30


ACCEPT_NO_MERGE_TEST = "мерж PR с кодом — за вами (test)"


@dataclass(frozen=True)
class NoMergeAccept:
    """Режим `test`: приёмка без мержа, вердикт и итог уходят в журнал."""

    known_positive: Callable[[str], bool]
    record_verdict: Callable[[ReviewVerdict], None]
    record_result: Callable[[dict], None]


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
    states = [(c.get("conclusion") or c.get("status") or "").upper() for c in checks]
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
    test: NoMergeAccept | None = None,
) -> int:
    """Ритуал приёмки; коды: 0 смержено, 1 стоп (причина на stdout).

    С `test` (режим test) мержа нет: 0 значит «PR проверен, не вмержен».

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
        materialized_head = ops.materialize_pr_head(target_dir, pr, head0)
    except RuntimeError as exc:
        print(f"accept-pr: не удалось материализовать head PR ({exc}) — стоп")
        ops.ensure_branch(target_dir, branch0)
        return 1
    # devtools#136: успешный switch — не факт о том, что видит ревьюер.
    # Явный rev-parse возвращает примитив, а вызывающий связывает его с тем же
    # headRefOid, по которому дальше пинуются ревью и мерж. Несовпадение
    # останавливает контур ДО changed_paths и платного вызова review-kit.
    if materialized_head != head0:
        print(
            "accept-pr: материализованный HEAD не совпал с head PR "
            f"({materialized_head[:7]} != {head0[:7]}) — стоп до ревью"
        )
        ops.ensure_branch(target_dir, branch0)
        return 1
    try:
        return _accept_on_head(
            repo,
            repo_slug,
            pr,
            ops,
            head0,
            target_dir,
            base_branch,
            sleep,
            poll_limit,
            test,
        )
    except RuntimeError as exc:
        print(f"accept-pr: {exc} — стоп")
        return 1
    finally:
        ops.ensure_branch(target_dir, branch0)


def _review_step(
    repo: str,
    repo_slug: str,
    pr: int,
    ops: Ops,
    head0: str,
    target_dir: str,
    test: NoMergeAccept | None,
) -> bool:
    """Терминальное ревью; `True` — стоп (причина напечатана)."""
    if test is None:
        review_exit = ops.review(repo, pr)
    else:
        review_exit = ops.review(repo, pr, target_dir=target_dir)
    if review_exit == REVIEW_BARRIER_EXIT:
        # Барьер, а не находки (devtools#258). Общий совет ниже здесь
        # принципиально не работает: журнал бюджета ключуется по `slug#pr`,
        # поэтому фикс-коммиты новую голову дают, а круг — нет, и повтор
        # без override отказал бы снова.
        print(f"accept-pr: {REVIEW_BARRIER_STOP}")
        return True
    if review_exit != 0:
        print(
            f"accept-pr: терминальное ревью вернуло {review_exit} — стоп; "
            "отработайте находки (фикс-коммиты на ветку PR) и повторите"
        )
        return True
    if test is None:
        return False
    return _record_marker_verdict(repo_slug, pr, ops, head0, test)


def _record_marker_verdict(
    repo_slug: str, pr: int, ops: Ops, head0: str, test: NoMergeAccept
) -> bool:
    """Вердикт из маркера ревью (test): нужен `approve` на head0."""
    verdict = parse_marker(ops.latest_review_body(repo_slug, pr))
    if verdict is None:
        print("accept-pr: в ревью нет маркера вердикта (verdict=) — стоп")
        return True
    if verdict.head != head0:
        print(
            f"accept-pr: вердикт вынесен по {verdict.head[:7]}, голова PR "
            f"{head0[:7]} — стоп"
        )
        return True
    if verdict.verdict != "approve":
        print(f"accept-pr: вердикт ревью {verdict.verdict} (не approve) — стоп")
        return True
    test.record_verdict(verdict)
    return False


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
    test: NoMergeAccept | None = None,
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
    # База вердикта приходит ОТТУДА ЖЕ, откуда список путей: тот же fetch,
    # тот же FETCH_HEAD (ревью #183, круг 3). Прежняя редакция брала её
    # отдельным `rev-parse origin/<base>` — а это другой ref, ровно тот
    # протухающий, из-за которого гард путей и перешёл на FETCH_HEAD
    # (приёмка PR #113, круг 4). Пин мержа обязан называть ту базу, от
    # которой посчитан гард, иначе «пин базы вердикта» — фикция.
    base0, changed = ops.changed_paths(target_dir, base_branch)
    harness = [f for f in changed if any(f.startswith(p) for p in _HARNESS_PREFIXES)]
    if harness:
        print(
            "accept-pr: дифф правит ревью-harness "
            f"({', '.join(sorted(set(harness))[:5])}…) — ревью и мерж "
            "только человеком"
        )
        return 1
    # Перечень — из SSOT `contracts/authority-root/v1/paths.env`, того же
    # файла, что читают runner и merge-pr.sh (ревью #183, круг 4): свой
    # кортеж здесь был одним из ДВУХ питоновских списков, живших порознь.
    authority = authority_root.touched(changed)
    if authority:
        print(
            "accept-pr: дифф трогает authority-root пути "
            f"({', '.join(sorted(set(authority))[:5])}…) — мерж только "
            "человеком (ADR-ECO-004 I2)"
        )
        return 1
    if test is None or not test.known_positive(head0):
        stopped = _review_step(repo, repo_slug, pr, ops, head0, target_dir, test)
        if stopped:
            return 1

    facts = ops.pr_facts(repo_slug, pr)
    polls = 0
    empty_polls = 0
    no_ci = False
    # UNKNOWN-mergeability тоже ждём (приёмка PR #109, круг 2): GitHub
    # вычисляет её асинхронно, и «не CONFLICTING» не значит «можно» —
    # fail-closed, мерж только на явном MERGEABLE.
    while (_checks_state(facts) == "pending" and not no_ci) or facts.get(
        "mergeable"
    ) not in ("MERGEABLE", "CONFLICTING"):
        polls += 1
        if polls > poll_limit:
            print(
                "accept-pr: чеки/mergeability не определились за отведённое "
                "время — стоп"
            )
            return 1
        sleep(_POLL_SECONDS)
        facts = ops.pr_facts(repo_slug, pr)
        empty_polls = empty_polls + 1 if not facts.get("statusCheckRollup") else 0
        if test is not None and not no_ci and empty_polls == _NO_CHECKS_GRACE_POLLS:
            no_ci = no_checks(facts, ops.workflow_files(repo_slug, head0))
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
    if test is not None:
        return _finish_no_merge(repo_slug, pr, head, test, no_ci)
    # Пин базы по OID, а не по имени (ревью PR #183, major): сверка
    # `baseRefName` выше ловит РЕТАРГЕТ, но не движение самой базы —
    # `origin/master` мог уехать вперёд между вердиктом и мержем, и агент
    # влил бы код в базу, которой ревьюер не видел. `base0` снят там же,
    # где считался гард путей (`git diff origin/<base>...HEAD`), то есть это
    # ровно та база, от которой вынесен вердикт.
    merge_code = ops.merge(repo, pr, head, base0)
    if merge_code != 0:
        # Причину уже назвал merge-pr.sh — его stderr идёт оператору
        # напрямую. Здесь добавляется то, чего обвязка знать не может:
        # ПРОЦЕДУРА и её цена (ревью #183, круг 5). Прежняя строка «гонка
        # head/base / правило репо» была догадкой сразу обо всём и не
        # говорила, что делать.
        #
        # Единственный лишний запрос — на пути отказа: сверить, уехала ли
        # база. Гонки в нём нет, потому что по нему ничего не решается —
        # только объясняется уже случившийся отказ.
        # Диагноз про базу — ТОЛЬКО на коде 5 («пин разошёлся»). У гварда
        # (3) повтор упрётся в тот же запрет, у форджи (4) причина своя:
        # объявлять там «база уехала» значит увести оператора чинить не то.
        after = ops.pr_facts(repo_slug, pr) if merge_code == 5 else {}
        head_now = after.get("headRefOid")
        # Базу читать ЖИВОЙ верхушкой, не `after["baseRefOid"]`: снимок
        # форджи о PR не двигается без update-branch и сказал бы «база не
        # двигалась» ровно тогда, когда обвязка отказала из-за ушедшей
        # верхушки (devtools#223). Неустановленный факт — не «не двигалась».
        tip = (
            ops.remote_branch_head_fact(repo_slug, base_branch)
            if merge_code == 5
            else None
        )
        # Пригоден для «двигалась/не двигалась» ТОЛЬКО FOUND: ABSENT —
        # установленный факт, но факт об отсутствии ветки, не о её
        # неподвижности (devtools#237).
        base_now = (
            tip.value if tip is not None and tip.outcome is Outcome.FOUND else None
        )
        tip_unknown = tip is not None and not tip.established
        tip_absent = tip is not None and tip.outcome is Outcome.ABSENT
        tip_detail = tip.detail if tip is not None else None
        if head_now and head_now != head:
            # Код 5 общий для обоих пинов, а процедуры разные: у головы
            # заново нужны и чеки. Сказать здесь «голова не менялась» (текст
            # ветки базы) значило бы соврать ровно о том, что разошлось.
            print(
                "accept-pr: голова уехала за время приёмки — вердикт вынесен "
                f"по {head[:7]}, сейчас {head_now[:7]}. Повторите "
                f"`make accept-pr ARGS='--repo {repo} --pr {pr}'`: ревью и "
                "чеки пройдут заново — проверять надо новый код."
            )
        elif base_now and base_now != base0:
            print(
                "accept-pr: база уехала за время приёмки — вердикт вынесен "
                f"от {base0[:7]}, сейчас {base_now[:7]}. Повторите "
                f"`make accept-pr ARGS='--repo {repo} --pr {pr}'`: ревью "
                "пройдёт заново по НОВОЙ базе (диапазон изменился — вердикт "
                "обязан относиться к нему), а чеки заново не ждутся — "
                "голова не менялась."
            )
        elif merge_code != 5:
            # devtools#185: на этих кодах база НЕ проверялась — и не должна:
            # повтор упёрся бы в тот же гвард (3) или тот же ответ форджи
            # (4). Говорить «не двигалась» значило бы утверждать факт,
            # которого никто не устанавливал.
            print(
                "accept-pr: мерж не выполнен — причина названа merge-pr.sh "
                "выше (гвард обвязки либо правило репо). Движение базы не "
                "проверялось: на этом коде оно ни на что не влияет. Стоп."
            )
        elif tip_unknown:
            print(
                "accept-pr: мерж не выполнен — пин разошёлся (код 5), а "
                f"верхушка базы не установлена ({tip_detail}): сказать, "
                "двигалась ли база, нечем. Повторите приёмку, когда GitHub "
                "отвечает."
            )
        elif tip_absent:
            print(
                "accept-pr: мерж не выполнен — пин разошёлся (код 5), а "
                f"ветки нет: origin/{base_branch} ({tip_detail}). Судить о "
                "движении базы нечего — её больше нет; PR остаётся человеку."
            )
        else:
            print(
                "accept-pr: мерж не выполнен — пин разошёлся (код 5), но "
                f"голова ({head[:7]}) и верхушка базы ({base0[:7]}) сейчас "
                "совпадают с проверенными: расхождение было временным либо "
                "обвязка сверяла иную величину. Повторите приёмку."
            )
        return 1
    print(
        f"accept-pr: {repo_slug}#{pr} смержен (head {head[:7]}). "
        f"Дальше в {repo}: `git pull --ff-only`, `spec-runner sync`, "
        "затем следующий run."
    )
    return 0


def _finish_no_merge(
    repo_slug: str, pr: int, head: str, test: NoMergeAccept, no_ci: bool = False
) -> int:
    """Test: вместо мержа — итог проверки в журнал и строка оператору."""
    url = f"https://github.com/{repo_slug}/pull/{pr}"
    checks = NO_CHECKS_TEST if no_ci else "green"
    test.record_result(
        {"pr": pr, "url": url, "head": head, "checks": checks, "merged": False}
    )
    shown = NO_CHECKS_TEST if no_ci else "проверки зелёные"
    print(
        f"accept-pr (test): {url} — голова {head[:7]} проверена, {shown}; "
        f"{ACCEPT_NO_MERGE_TEST}"
    )
    return 0


def _origin_slug(url: str) -> str | None:
    """`owner/name` из origin-URL (ssh/https, .git-суффикс опционален)."""
    match = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$", url.strip())
    return f"{match.group(1)}/{match.group(2)}".lower() if match else None


def _target_dir(repo: str, test_mode: bool) -> str | None:
    """Каталог цели: в test — из списка целей (None — не зарегистрирована)."""
    if not test_mode:
        return str(df_config.workspace_dir(repo))
    target = user_targets.lookup(repo)
    return None if target is None else str(target.path)


def main(argv: list[str] | None = None) -> int:
    """CLI-обвязка; вся политика — в `accept()` (тестируется стабом Ops).

    Гард владельца (приёмка PR #109, круг 3): `review` работает через
    ЛОКАЛЬНЫЙ чекаут `<workspace.root>/<repo>` (так устроен review-pr.sh), а `merge` — по
    слагу `--owner/--repo`; расхождение означало бы «отревьюили один репо,
    смержили другой». Origin чекаута обязан совпасть со слагом.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="имя репо (kapelle)")
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--owner", default=None, help="владелец (иначе из origin)")
    args = parser.parse_args(argv)
    try:
        test_mode = df_config.mode_or_strict() == "test"
        target_dir = _target_dir(args.repo, test_mode)
    except df_config.ConfigError as exc:
        print(f"accept-pr: конфиг DarkFactory: {exc}")
        return 2
    except user_targets.TargetsError as exc:
        print(f"accept-pr: {exc}")
        return 2
    if target_dir is None:
        print(
            f"accept-pr: репо {args.repo} нет в списке целей "
            f"{user_targets.targets_path()} — зарегистрируйте чекаут: "
            "python -m governance.user_targets add <каталог>"
        )
        return 2
    origin = subprocess.run(
        ["git", "-C", target_dir, "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    checkout_slug = _origin_slug(origin.stdout) if origin.returncode == 0 else None
    if args.owner is None:
        if checkout_slug is None:
            print(
                f"accept-pr: у чекаута {target_dir} нет разбираемого origin — "
                "владельца не определить, укажите --owner"
            )
            return 2
        repo_slug = f"{checkout_slug.split('/')[0]}/{args.repo}".lower()
    else:
        repo_slug = f"{args.owner}/{args.repo}".lower()
    if checkout_slug != repo_slug:
        print(
            f"accept-pr: origin локального чекаута {target_dir} = "
            f"{checkout_slug!r}, а мерж адресован {repo_slug!r} — стоп "
            "(ревью и мерж обязаны смотреть в один репозиторий)"
        )
        return 2
    test = (
        NoMergeAccept(lambda _h: False, lambda _v: None, lambda _r: None)
        if test_mode
        else None
    )
    return accept(args.repo, repo_slug, args.pr, RealOps(), target_dir, test=test)


if __name__ == "__main__":
    raise SystemExit(main())
