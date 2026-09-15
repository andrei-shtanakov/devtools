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
- если локальных леджеров нет, повтор восстанавливает минимальный леджер
  из durable-фактов GitHub: MERGED bundle-PR по head-ветке, run-id из его
  тела, bundle-dir из списка файлов; S8 безопасно переисполняется, а
  tasks-PR реконсилируется существующим мостом по своей ветке; OPEN не
  доказывает прохождение review/verdict и потому отказывает;
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
import shlex
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from governance import brief_input, run_state as rs
from governance import runner, task_bridge
from governance import interview as iv
from governance import ops as ops_mod
from governance.ops import DEVTOOLS_ROOT, RealOps

WORKSPACE_ROOT = DEVTOOLS_ROOT.parent
MANIFEST_PATH = (
    WORKSPACE_ROOT / "ai-orchestrators-workspace" / "workspace-manifest.toml"
)

_SLUG_MAX = 40

_BUNDLE_FILENAMES = frozenset(
    {
        "00-charter.md",
        "10-requirements.md",
        "15-behaviour-spec.md",
        "20-design.md",
        "25-acceptance.md",
        "30-decomposition.md",
    }
)
_RUN_BODY_RE = re.compile(
    r"^Автоматический прогон governance runner'а "
    r"\((?P<run_id>[A-Za-z0-9][A-Za-z0-9._-]*)\)\.$"
)


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


NEED_ONLY = ("frame", "stakeholder", "traces_to", "session", "new_run")
STAKEHOLDER_RULE = (
    "стадия Need запускается только при наличии реального стейкхолдера — "
    "укажите --stakeholder <role> (декларация, не проверка); без "
    "стейкхолдера используйте вход из готового брифа: --brief <path>"
)


def build_interview_spec(args, repo_slug: str) -> iv.InterviewSpec | None:
    """Preflight need-флагов — весь до run-id (спека §3).

    Чистая функция: только разбор аргументов, никаких побочных эффектов
    и никакого обращения к леджерам/GitHub. Взаимоисключение `--need`/
    `--brief` проверяется здесь же, по аргументам, ДО чтения файла брифа
    в `main` — иначе несуществующий путь брифа отказал бы чтением файла,
    а не preflight-правилом.
    """
    given = [f for f in NEED_ONLY if getattr(args, f)]
    if not args.need:
        if given:
            flags = [f"--{g.replace('_', '-')}" for g in given]
            raise SpecLoopError(
                f"флаги {flags} существуют только вместе с --need"
            )
        return None
    if args.brief:
        raise SpecLoopError("--need и --brief взаимоисключающи")
    if not args.frame:
        raise SpecLoopError("--need требует явный --frame customer|engineer")
    if not args.stakeholder:
        raise SpecLoopError(STAKEHOLDER_RULE)
    if args.new_run and (args.run_id or args.session):
        raise SpecLoopError(
            "--new-run взаимоисключающ с --run-id и --session"
        )
    if args.new_run and not args.ws_id:
        raise SpecLoopError("--new-run требует --ws-id <fresh-id>")
    if args.frame == "customer":
        if args.traces_to:
            raise SpecLoopError("customer-фрейм не принимает --traces-to")
        return iv.InterviewSpec("customer", args.stakeholder, repo_slug, None, None)
    if not args.traces_to:
        raise SpecLoopError(
            "engineer-фрейм требует --traces-to <approved customer-brief>"
        )
    raise SpecLoopError(ops_mod.ENGINEER_BLOCKED)  # D6, до discovery#49


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


def _bundle_dir_from_pr_files(files: list[str]) -> str:
    """Единственный каталог, содержащий полный шестиузловой бандл PR.

    Не подставляем современный дефолт молча: исходный запуск мог передать
    ``--bundle-dir``. Список файлов PR — durable-факт, позволяющий вернуть
    точное значение даже для такого запуска.
    """
    by_parent: dict[str, set[str]] = {}
    for raw in files:
        path = Path(raw)
        if path.name in _BUNDLE_FILENAMES and str(path.parent) not in (
            "", ".",
        ):
            by_parent.setdefault(str(path.parent), set()).add(path.name)
    complete = sorted(
        parent for parent, names in by_parent.items()
        if names == _BUNDLE_FILENAMES
    )
    if len(complete) != 1:
        detail = (
            complete if complete else "нет полного шестиузлового каталога"
        )
        raise SpecLoopError(
            "bundle-dir не восстанавливается из файлов bundle-PR: "
            f"{detail!r}; нужен ровно один каталог с "
            f"{sorted(_BUNDLE_FILENAMES)!r}"
        )
    return complete[0]


def _remote_branch_pattern(
    subject: str, ws_id: str | None
) -> tuple[str, re.Pattern]:
    """Префикс запроса и точная грамматика bundle-ветки восстановления."""
    if ws_id is not None:
        try:
            rs.validate_id_component(ws_id, label="ws_id")
        except ValueError as exc:
            raise SpecLoopError(str(exc)) from exc
        branch = f"spec/{ws_id}-behaviour"
        return branch, re.compile(rf"^{re.escape(branch)}$")
    slug = slug_from_subject(subject)
    prefix = f"spec/{slug}-"
    return prefix, re.compile(
        rf"^{re.escape(prefix)}(?P<started>\d{{8}})-behaviour$"
    )


def recover_run_from_github(
    *,
    subject: str,
    repo: str,
    repo_slug: str,
    target_dir: str,
    profile: str,
    author_backend: str,
    requested_ws_id: str | None,
    requested_bundle_dir: str | None,
    ops,
) -> rs.RunState | None:
    """Восстановить минимальный resumable ledger из durable GitHub-фактов.

    Внутренние op-записи S1–S7 из GitHub доказать нельзя и мы их не
    выдумываем. Восстановленный run ставится ровно на человеческую границу
    ``waiting_human_merge`` только при доказанном MERGED:
    ``runner.resume`` повторно сверит этот факт и идемпотентно выполнит
    authoritative S8. Дальнейший мост сам читает статусы узлов из
    frontmatter base и реконсилирует tasks-PR по
    ``spec/<ws-id>-tasks``. OPEN отказывает: по нему не восстановить,
    прошли ли S6 review и S7 verdict.
    """
    prefix, pattern = _remote_branch_pattern(subject, requested_ws_id)
    try:
        discovered = ops.prs_by_head_prefix(repo_slug, prefix)
    except RuntimeError as exc:
        raise SpecLoopError(
            f"поиск bundle-PR для восстановления не удался: {exc}"
        ) from exc

    candidates: list[tuple[dict, str]] = []
    for item in discovered:
        head = item.get("head")
        branch = head.get("ref") if isinstance(head, dict) else None
        if not isinstance(branch, str):
            raise SpecLoopError(
                f"GitHub вернул PR без head.ref: {item!r}"
            )
        match = pattern.fullmatch(branch)
        if match is None:
            continue
        ws_id = branch.removeprefix("spec/").removesuffix("-behaviour")
        candidates.append((item, ws_id))

    if not candidates:
        return None
    if len(candidates) > 1:
        rendered = ", ".join(
            f"#{item.get('number')} ({item['head']['ref']})"
            for item, _ws_id in candidates
        )
        distinct_ws_ids = {candidate_ws for _item, candidate_ws in candidates}
        hint = (
            "задайте --ws-id"
            if requested_ws_id is None and len(distinct_ws_ids) > 1
            else "одна head-ветка соответствует нескольким PR; "
                 "восстановите ledger вручную"
        )
        raise SpecLoopError(
            f"несколько bundle-PR подходят для восстановления: {rendered}; "
            f"{hint}"
        )

    item, ws_id = candidates[0]
    number = item.get("number")
    title = item.get("title")
    body = item.get("body")
    branch = item["head"]["ref"]
    if not isinstance(number, int):
        raise SpecLoopError(
            f"bundle-PR по ветке {branch!r} не несёт числовой number"
        )
    expected_title = f"{subject} — behaviour bundle {ws_id}"
    if title != expected_title:
        raise SpecLoopError(
            f"bundle-PR #{number} по ветке {branch!r} несёт title "
            f"{title!r}, ожидался {expected_title!r}; связь с subject не "
            "доказана, новый workstream не создаётся"
        )
    body_match = _RUN_BODY_RE.fullmatch(body or "")
    if body_match is None:
        raise SpecLoopError(
            f"bundle-PR #{number} не несёт канонический run-id в body — "
            "идентичность прежнего прогона не восстановлена"
        )
    run_id = body_match.group("run_id")

    try:
        facts = ops.pr_facts(repo_slug, number)
        files = ops.pr_files(repo_slug, number)
    except Exception as exc:
        raise SpecLoopError(
            f"факты bundle-PR #{number} недоступны: {exc}"
        ) from exc
    pr_state = facts.get("state")
    if pr_state == "OPEN":
        raise SpecLoopError(
            f"bundle-PR #{number} по ветке {branch!r} ещё OPEN, а GitHub "
            "не доказывает, были ли пройдены S6 review и S7 verdict; "
            "восстановите исходный run.json либо вручную решите судьбу PR. "
            "Автоматически объявлять его waiting_human_merge запрещено"
        )
    if pr_state != "MERGED":
        raise SpecLoopError(
            f"bundle-PR #{number} по ветке {branch!r} закрыт без мержа "
            f"(state={pr_state!r}); судьба workstream не выводится, новый "
            "не создаётся"
        )
    base_ref = facts.get("baseRefName")
    if not isinstance(base_ref, str) or not base_ref:
        raise SpecLoopError(
            f"bundle-PR #{number} не несёт baseRefName — S8 не знает, "
            "какую authoritative-ветку проверять"
        )
    bundle_dir = _bundle_dir_from_pr_files(files)
    if requested_bundle_dir is not None and requested_bundle_dir != bundle_dir:
        raise SpecLoopError(
            f"--bundle-dir={requested_bundle_dir!r} расходится с GitHub-"
            f"фактом bundle-PR #{number}: {bundle_dir!r}"
        )

    file_set = set(files)
    source_prefix = f"{bundle_dir}/00-discovery/"
    source_files = {path for path in file_set if path.startswith(source_prefix)}
    primary_path = f"{bundle_dir}/{brief_input.PRIMARY_REL}"
    brief_descriptor = None
    if source_files:
        if primary_path not in source_files:
            raise SpecLoopError(
                f"bundle-PR #{number} несёт неполный discovery source layer: "
                f"есть {sorted(source_files)!r}, но нет {primary_path!r}"
            )
        head = facts.get("headRefOid")
        if not isinstance(head, str) or not head:
            raise SpecLoopError(
                f"bundle-PR #{number} не несёт headRefOid — immutable "
                "discovery source восстановить нельзя"
            )
        try:
            with tempfile.TemporaryDirectory(prefix="brief-recovery-") as tmp:
                snapshot = Path(tmp)
                for source_path in sorted(source_files):
                    data = ops.show_repo_file_bytes(
                        repo_slug, head, source_path
                    )
                    if data is None:
                        raise brief_input.BriefInputError(
                            f"{head}:{source_path} не читается из head bundle-PR"
                        )
                    destination = snapshot / source_path
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(data)
                recovered_source = brief_input.inspect_materialized(
                    snapshot, bundle_dir
                )
            ops.checkout_and_pull(target_dir, base_ref)
            current_source = brief_input.inspect_materialized(
                Path(target_dir), bundle_dir
            )
        except (brief_input.BriefInputError, OSError, RuntimeError) as exc:
            raise SpecLoopError(
                f"discovery source bundle-PR #{number} не восстанавливается "
                f"из immutable head {head!r}: {exc}"
            ) from exc
        expected_source_files = {
            f"{bundle_dir}/{rel}" for rel in recovered_source.source_paths
        }
        missing = expected_source_files - source_files
        if missing:
            raise SpecLoopError(
                f"bundle-PR #{number} несёт неполный discovery source layer: "
                f"в его diff отсутствуют {sorted(missing)!r}"
            )
        if current_source.as_state() != recovered_source.as_state():
            raise SpecLoopError(
                f"discovery source после bundle-PR #{number} изменён в "
                f"{base_ref!r}; восстановите bytes из head {head} либо "
                "создайте новый workstream с другим ws-id"
            )
        brief_descriptor = recovered_source.as_state()

    # Не перезаписывать валидный локальный журнал с тем же run-id, если он
    # относится к другой работе. Пустой stub безопасно заменяется: GitHub
    # доказывает точную идентичность созданного runner'ом PR.
    if run_id in rs.all_run_ids():
        ledger_path = rs.run_dir(run_id) / "run.json"
        raw = ledger_path.read_text(encoding="utf-8")
        if raw.strip():
            try:
                existing = rs.load(run_id)
            except Exception as exc:
                raise SpecLoopError(
                    f"локальный ledger {run_id!r} нечитаем и совпал с "
                    "run-id bundle-PR; почините его вручную"
                ) from exc
            if (existing.repo, existing.subject, existing.ws_id) != (
                repo, subject, ws_id,
            ):
                raise SpecLoopError(
                    f"run-id {run_id!r} из bundle-PR занят другим локальным "
                    "ledger; автоматическое восстановление запрещено"
                )
            return existing

    state = rs.new_run(
        subject=subject,
        repo=repo,
        repo_slug=repo_slug,
        ws_id=ws_id,
        target_dir=target_dir,
        bundle_dir=bundle_dir,
        profile=profile,
        run_id=run_id,
        merge_authority="human",
        author_backend=author_backend,
        brief=brief_descriptor,
    )
    # Только MERGED — достаточный durable-факт для этой границы. OPEN мог
    # быть создан как draft до S6 либо остановлен красным review/verdict;
    # считать его waiting_human_merge означало бы навсегда пропустить S6/S7.
    state.status = "waiting_human_merge"
    state.branch = branch
    state.pr = number
    state.head = facts.get("headRefOid")
    state.base_ref = base_ref
    state.ops["ledger-recovery"] = {
        "status": "completed",
        "source": "github",
        "bundle_pr": number,
        # Исторический profile PR не хранит. Это конфигурация ТЕКУЩЕГО
        # authoritative S8, взятая из текущего CLI/default, а не выданная
        # за восстановленный факт. После MERGED author_backend не
        # исполняется вовсе, но его источник фиксируется симметрично.
        "profile": profile,
        "profile_source": "current-invocation-not-github",
        "author_backend": author_backend,
        "author_backend_effective": False,
    }
    rs.save(state)
    print(
        f"spec-loop: локальный ledger отсутствовал; восстановлен из "
        f"bundle-PR #{number} ({branch}), run-id={run_id}"
    )
    print(
        f"spec-loop: profile={profile!r} взят из текущего вызова "
        "(GitHub исторический profile не хранит); author_backend после "
        "MERGED не исполняется"
    )
    return state


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
    if state.status in ("waiting_interview", "stopped_interview"):
        if state.interview and state.interview.get("session_id") is None:
            # Сирота — координаты стадии Need без записанной сессии.
            # `runner.resume` тоже отказался бы звать discovery, но кнопка
            # проверяет это САМА и не делает вызов вовсе (ruling 2, Task 10):
            # диагностика orphan-состояния не должна зависеть от того,
            # дошёл ли вызов до runner.
            print(
                "spec-loop: стадия Need без записанной сессии — "
                "присоедините её: повторите команду с --session <id>, "
                "либо новый прогон: --new-run --ws-id <fresh-id>"
            )
            return 1
        after = runner.resume(state.run_id, ops)
        if after.status == "waiting_interview":
            return 0
        if after.status == "stopped_interview":
            findings_path = rs.run_dir(after.run_id) / runner.INTERVIEW_FINDINGS
            if findings_path.exists():
                print(
                    f"spec-loop: findings: {findings_path} — ответьте и "
                    "повторите команду"
                )
            else:
                session_id = (after.interview or {}).get("session_id")
                print(
                    "spec-loop: стоп стадии Need: см. причину выше; "
                    f"восстановите ту же сессию {shlex.quote(session_id)} "
                    "и повторите либо --new-run --ws-id <fresh-id>"
                )
            return 1
        if after.status == "waiting_human_merge":
            print(
                f"бандл-PR #{after.pr} создан ({after.repo_slug}) — "
                "смержьте его и повторите make spec-loop"
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
    parser.add_argument(
        "--brief", help="gate-passed discovery-brief для нового прогона"
    )
    parser.add_argument(
        "--need", action="store_true",
        help="стадия Need: интервью discovery из прогона (E2)"
    )
    parser.add_argument("--frame", choices=["customer", "engineer"])
    parser.add_argument(
        "--stakeholder", help="роль реального стейкхолдера (декларация)"
    )
    parser.add_argument(
        "--traces-to", help="approved customer-brief для engineer"
    )
    parser.add_argument(
        "--session", help="recovery: присоединить сессию discovery"
    )
    parser.add_argument(
        "--new-run", action="store_true",
        help="новый прогон при существующем (только до S1), требует --ws-id"
    )
    parser.add_argument("--profile", default="profiles/team-exp.yaml")
    parser.add_argument(
        "--author-backend", choices=["codex", "disp"], default="codex"
    )
    parser.add_argument("--target-dir", help="override деривации из манифеста")
    # --merge-authority НАМЕРЕННО отсутствует: кнопка всегда передаёт
    # "human" (решение владельца 2026-09-07) — argparse отвергнет попытку.
    args = parser.parse_args(argv)

    try:
        # Intake обязан завершиться до генерации run-id, GitHub-вызовов и
        # любых runner effects. В ledger уйдёт descriptor, не этот Path.
        # Порядок намеренный (контроллер, Task 9): entry раньше —
        # build_interview_spec нужен repo_slug; она же раньше supplied_brief
        # — взаимоисключение --need/--brief проверяется по аргументам ДО
        # чтения файла брифа (иначе несуществующий путь отказал бы чтением
        # файла, а не preflight-правилом).
        entry = manifest_repo_entry(
            MANIFEST_PATH.read_text(encoding="utf-8"), args.repo
        )
        interview_spec = build_interview_spec(args, entry.repo_slug)
        supplied_brief = (
            brief_input.inspect_brief(Path(args.brief)) if args.brief else None
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

        if args.new_run:
            # --new-run отменяет гвард неоднозначности «--run-id» ниже:
            # запросили новый прогон явно, а старые (сколько бы их ни
            # было) остаются нетронутыми — но только пока НИ ОДИН не
            # прошёл дальше S1 (waiting_interview/stopped_interview).
            # Прогон, достигший S1, уже произвёл бандл-PR/эффекты — молча
            # плодить рядом с ним второй workstream с тем же (repo,
            # subject) запрещено (ruling 4, controller Task 10).
            past_s1 = [
                st for st in matches
                if st.status not in ("waiting_interview", "stopped_interview")
            ]
            if past_s1:
                raise SpecLoopError(
                    "--new-run запрещён: прогон(ы) с этими (repo, subject) "
                    "уже достигли S1: "
                    + ", ".join(
                        f"--run-id {st.run_id} [{st.status}]"
                        for st in past_s1
                    )
                )
            for st in matches:
                print(
                    f"spec-loop: прежний прогон {st.run_id} остаётся "
                    f"({st.status}), сессия discovery: "
                    f"{(st.interview or {}).get('session_id')}"
                )
            state = None
        else:
            if len(matches) > 1:
                print(
                    "spec-loop: несколько прогонов с этими (repo, subject) "
                    "— выберите явно через --run-id:"
                )
                for st in matches:
                    print(
                        f"  --run-id {st.run_id}  [{st.status}] "
                        f"ws={st.ws_id}"
                    )
                return 1
            state = matches[0] if matches else None

        if state is not None and supplied_brief is not None:
            if state.brief != supplied_brief.as_state():
                raise SpecLoopError(
                    "--brief не совпадает с discovery source этого "
                    "прогона; прогон с этими (repo, subject) уже есть — "
                    "продолжите его без --brief либо выберите --run-id; "
                    "новый прогон с тем же subject без --need невозможен, "
                    "уберите или переименуйте леджер вручную"
                )
        target_dir = (
            state.target_dir
            if state is not None
            else args.target_dir or str(WORKSPACE_ROOT / entry.repo)
        )
        self_check_slug = (
            state.repo_slug if state is not None else entry.repo_slug
        )
        origin_slug = repo_slug_from_url(_origin_url(target_dir))
        if origin_slug != self_check_slug:
            raise SpecLoopError(
                f"origin целевого чекаута {target_dir!r} = "
                f"{origin_slug!r}, манифест/леджер ждёт "
                f"{self_check_slug!r} — расхождение origin"
            )

        ops = _real_ops()
        recovered = False
        if state is None and args.run_id is None:
            state = recover_run_from_github(
                subject=args.subject,
                repo=args.repo,
                repo_slug=entry.repo_slug,
                target_dir=target_dir,
                profile=args.profile,
                author_backend=args.author_backend,
                requested_ws_id=args.ws_id,
                requested_bundle_dir=args.bundle_dir,
                ops=ops,
            )
            recovered = state is not None
        if recovered and supplied_brief is not None:
            raise SpecLoopError(
                "восстановленный из GitHub прогон не принимает --brief "
                "задним числом; прогон с этими (repo, subject) уже есть "
                "— продолжите его без --brief либо выберите --run-id; "
                "новый прогон с тем же subject без --need невозможен, "
                "уберите или переименуйте леджер вручную"
            )
        if state is not None and interview_spec is not None:
            if state.interview is None:
                raise SpecLoopError(
                    "у прогона нет стадии Need (создан через --brief/legacy "
                    "или восстановлен из GitHub) — новый: --need … "
                    "--new-run --ws-id <fresh-id>"
                )
            recorded = iv.InterviewSpec.from_state(state.interview)
            if (recorded.frame, recorded.stakeholder_role, recorded.traces_to) != (
                interview_spec.frame, interview_spec.stakeholder_role,
                interview_spec.traces_to,
            ):
                raise SpecLoopError(
                    "координаты интервью зафиксированы стартом "
                    f"(frame={recorded.frame}, "
                    f"stakeholder={recorded.stakeholder_role!r}, "
                    f"traces_to={recorded.traces_to!r}) — сменить их: "
                    "--new-run --ws-id"
                )

        if args.session:
            # Позиция намеренная (ruling 3, controller Task 10): ПОСЛЕ
            # ops = _real_ops() и после recover_run_from_github — state
            # уже окончателен — и ДО таблицы разрешённых значений/
            # _dispatch, чтобы они увидели уже присоединённую сессию.
            if state is None:
                raise SpecLoopError(
                    "--session присоединяет сессию к существующему "
                    "прогону, а прогон с этими (repo, subject) не найден"
                )
            try:
                state = runner.attach_session(state.run_id, args.session, ops)
            except ValueError as exc:
                raise SpecLoopError(str(exc)) from exc

        if state is not None:
            values = {
                "subject": state.subject,
                "repo": state.repo,
                "repo-slug": state.repo_slug,
                "ws-id": state.ws_id,
                "run-id": state.run_id,
                "target-dir": state.target_dir,
                "bundle-dir": state.bundle_dir,
                "profile": state.profile,
                "brief-frame": (
                    state.brief.get("frame") if state.brief else "нет"
                ),
                "brief-source": (
                    state.brief.get("source_paths") if state.brief else "нет"
                ),
                "need-frame": (
                    state.interview.get("frame") if state.interview else "нет"
                ),
                "stakeholder": (
                    state.interview.get("stakeholder_role")
                    if state.interview else "нет"
                ),
                "действие": f"продолжение ({state.status})",
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
            # run-id материализуется ДО таблицы разрешённых значений.
            run_id = f"{ws_id}-{os.urandom(3).hex()}"
            bundle_dir = args.bundle_dir or f"workstreams/{ws_id}/spec"
            values = {
                "subject": args.subject,
                "repo": args.repo,
                "repo-slug": entry.repo_slug,
                "ws-id": ws_id,
                "run-id": run_id,
                "target-dir": target_dir,
                "bundle-dir": bundle_dir,
                "profile": args.profile,
                "brief-frame": (
                    supplied_brief.frame if supplied_brief else "нет"
                ),
                "brief-source": (
                    list(supplied_brief.source_paths)
                    if supplied_brief else "нет"
                ),
                "need-frame": (
                    interview_spec.frame if interview_spec else "нет"
                ),
                "stakeholder": (
                    interview_spec.stakeholder_role
                    if interview_spec else "нет"
                ),
                "merge-authority": "human (жёстко, без override)",
                "действие": "start (новый прогон)",
            }

        _print_values(values)
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
            brief_source=supplied_brief,
            interview_spec=interview_spec,
        )
        print(f"статус прогона: {started.status}")
        if started.status == "waiting_interview":
            print(
                "интервью начато — ответьте стейкхолдеру вне spec-loop и "
                f"повторите: make spec-loop … ARGS='--need … --run-id "
                f"{started.run_id}'"
            )
            return 0
        if started.status == "waiting_human_merge":
            print(
                f"бандл-PR #{started.pr} создан ({started.repo_slug}) — "
                "смержьте его и повторите make spec-loop"
            )
            return 0
        if started.status == "completed":
            return _deliver_phase(started, ops)
        return _report_state(started)
    except (SpecLoopError, brief_input.BriefInputError, FileNotFoundError) as exc:
        print(f"spec-loop: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
