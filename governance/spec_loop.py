"""Операторская кнопка `make spec-loop` (пункт 3 роадмапа, 2026-09-07).

Одна и та же команда ведёт workstream через весь конвейер, останавливаясь
на каждой человеческой границе:

- прогона нет → `runner.start` (авторинг 6 узлов → гейты → PR бандла) и
  стоп на `waiting_human_merge`;
- прогон ждёт мерж → `runner.resume`: мерж не подтверждён — стоим; мерж
  подтверждён — S8, затем `task_bridge.deliver_for_run` (draft
  tasks-спека PR-ом) и стоп на следующей границе — approve спеки;
- человеческих границ теперь ТРИ, а не две (§I12, 2026-09-11): между
  мержем бандл-PR и доставкой встала ещё одна — одобрение узлов DAG.
  Мерж бандл-PR одобрением НЕ является: он вносит байты в base и только.
  Узел одобряет человек мержем candidate-PR, который готовит
  `make behaviour-tasks ARGS='--run-id … --approve-node <node-id>'`, и
  пока хоть один узел не одобрен честно, доставка отказывает. Отказ
  кнопки на свежем воркстриме поэтому ШТАТЕН — бандл лежит в base
  целиком `draft`, потому что штамповать его больше некому;
- всё остальное (`stopped_*`, `merged_unverified`, оборванный `running`,
  неизвестный статус) — отчёт и ненулевой RC без скрытых ретраев и
  платных вызовов.

Инварианты кнопки (дизайн-решения владельца, 2026-09-07):

- `merge_authority` жёстко `"human"` и НЕ переопределяется флагом —
  никаких автоматических мержей продуктовых PR кнопкой;
- повтор ищет прогон по точным (repo, subject) среди ВСЕХ леджеров;
  дата участвует только в генерации нового ws-id — запуск на следующий
  день продолжает существующий workstream, а не открывает новый;
- любая неоднозначность (несколько кандидатов, битый леджер, ws-id-
  коллизия с чужим subject, расхождение манифеста и origin) — fail-closed
  с перечислением кандидатов и подсказкой `--run-id`/`--ws-id`.

Сам модуль внешних эффектов не делает: только диспетчеризация в
`runner`/`task_bridge` (их эффекты идут через Ops) плюс один локальный
`git remote get-url origin` для сверки target-dir с манифестом.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from governance import run_state as rs
from governance import runner, task_bridge
from governance.ops import DEVTOOLS_ROOT, RealOps

WORKSPACE_ROOT = DEVTOOLS_ROOT.parent
MANIFEST_PATH = (
    WORKSPACE_ROOT / "ai-orchestrators-workspace" / "workspace-manifest.toml"
)

_SLUG_MAX = 40


class SpecLoopError(RuntimeError):
    """Fail-closed отказ кнопки: неоднозначность или расхождение фактов."""


def _real_ops() -> RealOps:
    """Фабрика Ops — отдельной функцией, чтобы тесты не строили RealOps."""
    return RealOps()


# --- деривации (чистые функции) --------------------------------------------


def slug_from_subject(subject: str) -> str:
    """ASCII-слаг subject'а для ws-id; пустой результат — fail-closed.

    Не-ASCII символы (кириллица) не транслитерируются — исчезают; если
    после фильтра не осталось ничего, оператор обязан задать `--ws-id`
    явно (валидатор id всё равно пускает только [A-Za-z0-9._-]).
    """
    slug = re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-")
    slug = slug[:_SLUG_MAX].rstrip("-")
    if not slug:
        raise SpecLoopError(
            f"из subject {subject!r} не выводится ASCII-слаг — задайте "
            "--ws-id явно"
        )
    return slug


def ws_id_for(subject: str, today: date) -> str:
    """ws-id нового прогона: слаг + дата. Только при СОЗДАНИИ (повтор
    ищет прогон по (repo, subject), не по ws-id с сегодняшней датой)."""
    return f"{slug_from_subject(subject)}-{today.strftime('%Y%m%d')}"


def repo_slug_from_url(url: str) -> str:
    """`owner/name` из remote-URL (формы git@ / https / ssh)."""
    for prefix in (
        "git@github.com:",
        "https://github.com/",
        "ssh://git@github.com/",
    ):
        if url.startswith(prefix):
            return url.removeprefix(prefix).removesuffix(".git")
    raise SpecLoopError(f"неизвестная форма remote-URL: {url!r}")


@dataclass(frozen=True)
class RepoEntry:
    """Репо из workspace-манифеста: каталог + канонический slug."""

    repo: str
    repo_slug: str
    repo_url: str


def manifest_repo_entry(manifest_text: str, repo: str) -> RepoEntry:
    """Ровно один репо по `git_dir == repo` из манифеста (SSOT состава).

    Несколько секций могут делить один git_dir (umbrella-дистрибутивы) —
    это один репо, если repo_url у них совпадает; разные repo_url под
    одним git_dir — fail-closed.
    """
    try:
        data = tomllib.loads(manifest_text)
    except tomllib.TOMLDecodeError as exc:
        raise SpecLoopError(
            f"манифест {MANIFEST_PATH} не парсится как TOML: {exc}"
        ) from exc
    urls: set[str] = set()

    def walk(node: object) -> None:
        if not isinstance(node, dict):
            return
        if node.get("git_dir") == repo and "repo_url" in node:
            urls.add(node["repo_url"])
        for value in node.values():
            walk(value)

    walk(data)
    if not urls:
        raise SpecLoopError(
            f"репо {repo!r} не найден в манифесте {MANIFEST_PATH} — "
            "кнопка работает только по составу флота (SSOT)"
        )
    if len(urls) > 1:
        raise SpecLoopError(
            f"репо {repo!r} в манифесте неоднозначен: git_dir делят "
            f"разные repo_url {sorted(urls)!r} — почините манифест"
        )
    url = urls.pop()
    return RepoEntry(repo=repo, repo_slug=repo_slug_from_url(url), repo_url=url)


def _origin_url(target_dir: str | Path) -> str:
    """origin целевого чекаута — для сверки с манифестом (fail-closed)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(target_dir), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        # minor терм. ревью #156: манифест — SSOT флота, не локальных
        # клонов; не склонированный репо — fail-closed сообщение, не
        # traceback.
        raise SpecLoopError(
            f"{target_dir}: не git-чекаут или репо не склонирован "
            "(git remote get-url origin отказал) — склонируйте репо "
            "либо задайте --target-dir"
        ) from exc
    return out.stdout.strip()


def find_runs(repo: str, subject: str) -> list[rs.RunState]:
    """Все прогоны с точными (repo, subject) среди леджеров RUNS_ROOT.

    Битый run.json — fail-closed: нечитаемый леджер мог бы быть искомым
    прогоном, и молчаливый пропуск открыл бы дубль-workstream.
    """
    matches: list[rs.RunState] = []
    broken: list[str] = []
    for run_id in rs.all_run_ids():
        try:
            state = rs.load(run_id)
        except Exception:
            raw = (rs.run_dir(run_id) / "run.json").read_text(
                encoding="utf-8"
            )
            if not raw.strip():
                # Пустой run.json — штатный труп runner'а (падение между
                # _reserve_run_id и save; см. _next_verify_run_id) — не
                # повод глушить кнопку (minor терм. ревью #156).
                continue
            broken.append(run_id)
            continue
        if state.repo == repo and state.subject == subject:
            matches.append(state)
    if broken:
        raise SpecLoopError(
            "нечитаемые леджеры под RUNS_ROOT: "
            + ", ".join(broken)
            + " — почините или уберите их прежде, чем продолжать "
            "(битый леджер мог быть искомым прогоном); обойти можно "
            "явным --run-id"
        )
    return matches


# --- маршрутизация ----------------------------------------------------------


def _print_values(values: dict[str, object]) -> None:
    width = max(len(k) for k in values)
    for key, value in values.items():
        print(f"{key + ':':<{width + 1}} {value}")


_APPROVE_NODE_HINT = (
    "человеческая граница — одобрение узлов DAG (§I12): мерж бандл-PR "
    "одобрением не является, и штамповать узлы доставке больше нечем. "
    "По каждому долговому узлу из отказа выше, в топологическом порядке: "
    "`make behaviour-tasks ARGS='--run-id {run_id} --approve-node "
    "<node-id>'` → мерж candidate-PR ЧЕЛОВЕКОМ (это и есть акт) → "
    "повтор той же команды дописывает подпись. Когда DAG одобрен "
    "целиком — повторите `make spec-loop`"
)

_APPROVE_HINT = (
    "следующая человеческая граница — approve tasks-спеки: "
    "`spec approve tasks` в репо-владельце, затем "
    "`make behaviour-tasks ARGS='--run-id {run_id} --conform-approve'`"
)


def _deliver_phase(state: rs.RunState, ops) -> int:
    """status == completed: идемпотентная доставка tasks-спеки мостом."""
    try:
        pr = task_bridge.deliver_for_run(state, ops)
    except RuntimeError as exc:
        print(f"spec-loop: доставка tasks-спеки отказала: {exc}")
        # Подсказка про одобрение узлов — ТОЛЬКО на отказе гейта §I12.
        # Отказов у доставки много (грязное дерево, отклонённый PR,
        # уехавшая база), и общая подсказка уводила бы оператора
        # одобрять узлы там, где мешает совсем другое. Различитель —
        # маркер параграфа в самом сообщении: его ставит гейт и только он.
        if "§I12" in str(exc):
            print(_APPROVE_NODE_HINT.format(run_id=state.run_id))
        return 1
    print(f"tasks-спека доставлена: PR #{pr} ({state.repo_slug})")
    print(_APPROVE_HINT.format(run_id=state.run_id))
    return 0


def _report_state(state: rs.RunState) -> int:
    """Не-продолжаемые статусы: отчёт + подсказка, ненулевой RC."""
    print(
        f"spec-loop: прогон {state.run_id!r} в статусе {state.status!r} — "
        "кнопка автоматически продолжает только waiting_human_merge/"
        "completed; без скрытых ретраев."
    )
    hints = {
        "running": (
            "прогон, вероятно, оборван — продолжение: "
            f"make behaviour-run ARGS='resume --run-id {state.run_id}'"
        ),
        "merged_unverified": (
            "терминально; verification-run: "
            f"make behaviour-run ARGS='verify --run-id {state.run_id}'"
        ),
    }
    hint = hints.get(state.status)
    if hint is None and state.status.startswith("stopped_"):
        hint = (
            f"findings: out/governance-runs/{state.run_id}/ — после правки "
            f"make behaviour-run ARGS='resume --run-id {state.run_id}'"
        )
    if hint:
        print(f"spec-loop: {hint}")
    return 1


def _dispatch(state: rs.RunState, ops) -> int:
    """Действие по фактическому статусу найденного прогона."""
    if state.status == "waiting_human_merge":
        after = runner.resume(state.run_id, ops)
        if after.status == "waiting_human_merge":
            print(
                f"ждём мерж бандл-PR #{after.pr} ({after.repo_slug}) — "
                "после мержа повторите make spec-loop"
            )
            return 0
        if after.status == "completed":
            return _deliver_phase(after, ops)
        return _report_state(after)
    if state.status == "completed":
        return _deliver_phase(state, ops)
    return _report_state(state)


def main(argv: list[str] | None = None) -> int:
    """CLI кнопки: `--subject` + `--repo`, остальное выводится."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--ws-id", help="override деривации slug+дата")
    parser.add_argument("--run-id", help="явный выбор при неоднозначности")
    parser.add_argument("--bundle-dir", help="дефолт workstreams/<ws-id>/spec")
    parser.add_argument("--profile", default="profiles/team-exp.yaml")
    parser.add_argument(
        "--author-backend", choices=["codex", "disp"], default="codex"
    )
    parser.add_argument("--target-dir", help="override деривации из манифеста")
    # --merge-authority НАМЕРЕННО отсутствует: кнопка всегда передаёт
    # "human" (решение владельца 2026-09-07) — argparse отвергнет попытку.
    args = parser.parse_args(argv)

    try:
        entry = manifest_repo_entry(
            MANIFEST_PATH.read_text(encoding="utf-8"), args.repo
        )
        if args.run_id:
            state = rs.load(args.run_id)
            if (state.repo, state.subject) != (args.repo, args.subject):
                print(
                    f"spec-loop: run {args.run_id!r} несёт "
                    f"(repo={state.repo!r}, subject={state.subject!r}), "
                    f"задано (repo={args.repo!r}, "
                    f"subject={args.subject!r}) — отказ"
                )
                return 1
            matches: list[rs.RunState] = [state]
        else:
            matches = find_runs(args.repo, args.subject)
        if len(matches) > 1:
            print(
                "spec-loop: несколько прогонов с этими (repo, subject) — "
                "выберите явно через --run-id:"
            )
            for st in matches:
                print(f"  --run-id {st.run_id}  [{st.status}] ws={st.ws_id}")
            return 1

        if matches:
            state = matches[0]
            target_dir = state.target_dir
            self_check_slug = state.repo_slug
            action = state.status
            values = {
                "subject": state.subject,
                "repo": state.repo,
                "repo-slug": state.repo_slug,
                "ws-id": state.ws_id,
                "run-id": state.run_id,
                "target-dir": state.target_dir,
                "bundle-dir": state.bundle_dir,
                "profile": state.profile,
                "действие": f"продолжение ({action})",
            }
        else:
            ws_id = args.ws_id or ws_id_for(args.subject, date.today())
            rs.validate_id_component(ws_id, label="ws_id")
            collisions = []
            for rid in rs.all_run_ids():
                try:
                    if rs.load(rid).ws_id == ws_id:
                        collisions.append(rid)
                except Exception:
                    # Нечитаемые леджеры уже отвергнуты/пропущены
                    # find_runs выше (пустые трупы runner'а — штатны).
                    continue
            if collisions:
                raise SpecLoopError(
                    f"ws-id {ws_id!r} уже занят прогонами "
                    f"{collisions!r} с другой парой (repo, subject) — "
                    "задайте --ws-id"
                )
            target_dir = args.target_dir or str(WORKSPACE_ROOT / entry.repo)
            self_check_slug = entry.repo_slug
            # run-id материализуется ДО таблицы разрешённых значений.
            run_id = f"{ws_id}-{os.urandom(3).hex()}"
            bundle_dir = args.bundle_dir or f"workstreams/{ws_id}/spec"
            state = None
            values = {
                "subject": args.subject,
                "repo": args.repo,
                "repo-slug": entry.repo_slug,
                "ws-id": ws_id,
                "run-id": run_id,
                "target-dir": target_dir,
                "bundle-dir": bundle_dir,
                "profile": args.profile,
                "merge-authority": "human (жёстко, без override)",
                "действие": "start (новый прогон)",
            }

        origin_slug = repo_slug_from_url(_origin_url(target_dir))
        if origin_slug != self_check_slug:
            raise SpecLoopError(
                f"origin целевого чекаута {target_dir!r} = "
                f"{origin_slug!r}, манифест/леджер ждёт "
                f"{self_check_slug!r} — расхождение origin"
            )

        _print_values(values)
        ops = _real_ops()
        if state is not None:
            return _dispatch(state, ops)
        started = runner.start(
            subject=args.subject,
            repo=args.repo,
            repo_slug=entry.repo_slug,
            ws_id=ws_id,
            target_dir=target_dir,
            bundle_dir=bundle_dir,
            profile=args.profile,
            run_id=run_id,
            ops=ops,
            merge_authority="human",
            author_backend=args.author_backend,
        )
        print(f"статус прогона: {started.status}")
        if started.status == "waiting_human_merge":
            print(
                f"бандл-PR #{started.pr} создан ({started.repo_slug}) — "
                "смержьте его и повторите make spec-loop"
            )
            return 0
        if started.status == "completed":
            return _deliver_phase(started, ops)
        return _report_state(started)
    except (SpecLoopError, FileNotFoundError) as exc:
        print(f"spec-loop: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
