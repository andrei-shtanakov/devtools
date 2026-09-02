"""Ops — единственная точка внешних эффектов runner'а (спека §5/§8).

``Ops`` — протокол, закрывающий ВЕСЬ набор внешних вызовов (git/gh/codex/
gate-check), которыми пользуется behaviour runner. ``RealOps`` — тонкая
subprocess-обёртка над ним: каждый метод строит одну команду, разбирает её
результат в примитив (str/int/bool/list/dict) и ничего не интерпретирует —
решения (мерж/нет, готов ли бандл и т.п.) принимает runner (Task 4), не этот
модуль. ``FakeOps`` для тестов runner'а живёт в тестах runner'а, не здесь.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Protocol

DEVTOOLS_ROOT = Path(__file__).resolve().parent.parent
REVIEW_GH_CONFIG_DIR = Path.home() / ".config" / "review"

_PR_URL_RE = re.compile(r"/pull/(\d+)")
_ISSUE_URL_RE = re.compile(r"/issues/(\d+)")


class Ops(Protocol):
    """Протокол внешних эффектов (спека §5/§8) — сигнатуры дословны."""

    def ensure_branch(self, target_dir: str, branch: str) -> None: ...

    def is_dirty(self, target_dir: str) -> bool: ...

    def current_branch(self, target_dir: str) -> str | None: ...

    def materialize_pr_head(
        self, target_dir: str, pr: int, sha: str
    ) -> None: ...

    def changed_paths(self, target_dir: str, base_branch: str) -> list[str]: ...

    def head_sha(self, target_dir: str, branch: str) -> str: ...

    def push_branch(self, target_dir: str, branch: str) -> None: ...

    def checkout_and_pull(self, target_dir: str, branch: str) -> None: ...

    def find_pr(self, repo_slug: str, branch: str) -> int | None: ...

    def create_draft_pr(
        self,
        target_dir: str,
        repo_slug: str,
        branch: str,
        title: str,
        body: str,
        label: str,
    ) -> int: ...

    def mark_ready(self, repo_slug: str, pr: int) -> None: ...

    def review(self, repo_name: str, pr: int) -> int: ...

    def review_fresh(self, repo_name: str, pr: int) -> int: ...

    def latest_review_body(self, repo_slug: str, pr: int) -> str | None: ...

    def file_exists_at(
        self, target_dir: str, head: str, path: str
    ) -> bool: ...

    def pr_facts(self, repo_slug: str, pr: int) -> dict: ...

    def pr_files(self, repo_slug: str, pr: int) -> list[str]: ...

    def unresolved_threads(self, repo_slug: str, pr: int) -> bool | None: ...

    def merge(self, repo_slug: str, pr: int, sha: str) -> bool: ...

    def comment(self, repo_slug: str, pr: int, body: str) -> None: ...

    def author(
        self, target_dir: str, kind: str, subject: str, bundle_dir: str
    ) -> int: ...

    def author_disp(self, target_dir: str, task: str) -> int: ...

    def commit_paths(
        self, target_dir: str, paths: list[str], message: str
    ) -> None: ...

    def gate_check_s8(
        self, target_dir: str, bundle_dir: str, profile: str
    ) -> tuple[int, str]: ...

    def gate_check_candidate(
        self, target_dir: str, bundle_dir: str, profile: str
    ) -> tuple[int, str]: ...

    def create_issue(self, repo_slug: str, title: str, body: str) -> int: ...

    def find_issue(self, repo_slug: str, body_prefix: str) -> int | None: ...


# Канонические имена файлов бандла (зеркало runner._AUTHOR_STEPS) и DSL-правила
# гейта для промпта авторинга. Держим в ops: промпт — часть точной команды.
_AUTHOR_FILENAMES = {
    "charter": "00-charter.md",
    "requirements": "10-requirements.md",
    "behaviour-spec": "15-behaviour-spec.md",
}
_AUTHOR_DSL = {
    "charter": (
        "YAML frontmatter (required): spec_stage: charter, status: draft, "
        "owner_role: product."
    ),
    "requirements": (
        "YAML frontmatter (required): spec_stage: requirements, status: "
        "draft, owner_role: product, traces_to: [charter], upstream_hashes: "
        "{charter: \"<hash>\"} where <hash> is the output of "
        "`git hash-object <bundle_dir>/00-charter.md`. Every functional "
        "requirement MUST be a heading `#### FR-NN: <title>` followed by a "
        "`**Priority**: Must` (or Should) line. Non-functional requirements "
        "use `#### NFR-NN: <title>`. Use FR-/NFR- ids consistently "
        "everywhere, including any traceability matrices."
    ),
    "behaviour-spec": (
        "YAML frontmatter (required): spec_stage: behaviour-spec, status: "
        "draft, owner_role: product, traces_to: [requirements], "
        "upstream_hashes: {requirements: \"<hash>\"} where <hash> is the "
        "output of `git hash-object <bundle_dir>/10-requirements.md`. Every "
        "scenario MUST be a heading `#### BEH-NN: <title>` and contain a "
        "line `` `traces: [FR-NN, ...]` `` (ids must exist in requirements) "
        "and a line `- **checked_by**: `status: planned` `kind: "
        "<atp|contract|integration|e2e|manual>` `owner: qa` `target: <test "
        "path>``. Use BEH-/FR- ids consistently everywhere, including any "
        "traceability matrices."
    ),
}


class RealOps:
    """RealOps: точные команды внешних эффектов (спека §5/§8)."""

    def ensure_branch(self, target_dir: str, branch: str) -> None:
        """Переключиться на branch, создав её, если ещё нет."""
        exists = subprocess.run(
            ["git", "rev-parse", "--verify", branch],
            cwd=target_dir, capture_output=True, text=True,
        )
        flag = [] if exists.returncode == 0 else ["-c"]
        subprocess.run(["git", "switch", *flag, branch], cwd=target_dir, check=True)

    def is_dirty(self, target_dir: str) -> bool:
        """`git status --porcelain` непуст → есть незакоммиченные изменения.

        Fail-closed гард S1 (финальное ревью, круг 5): грязный target_dir ДО
        начала прогона означает, что дальнейший `commit_paths` закоммитит
        рядом с чужими незакоммиченными правками (не сотрёт их, но перемешает
        историю) — прогон обязан остановиться раньше, а не молча продолжить.
        """
        done = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=target_dir, capture_output=True, text=True, check=True,
        )
        return bool(done.stdout.strip())

    def current_branch(self, target_dir: str) -> str | None:
        """Имя текущей ветки в target_dir; None — detached HEAD."""
        done = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=target_dir, capture_output=True, text=True, check=True,
        )
        return done.stdout.strip() or None

    def materialize_pr_head(self, target_dir: str, pr: int, sha: str) -> None:
        """fetch pull/<pr>/head + detached switch на пин sha; сбой — RuntimeError.

        Ретроспектива 2026-09-02 (урок 7, devtools#110): review-kit считает
        локальное дерево авторитетным — перед ревью чекаут цели обязан стоять
        на проверяемом head. Detach на пинованный sha (не на ветку PR): гонка
        с параллельным push либо не влияет, либо валит switch — fail-closed.
        --no-overwrite-ignore (приёмка PR #113, круг 5): git status
        --porcelain не видит ignored-файлы, а голый switch молча перезаписал
        бы локальный ignored-файл оператора версией из PR — конфликт обязан
        валить switch, не терять данные.
        """
        fetch = subprocess.run(
            ["git", "fetch", "origin", f"pull/{pr}/head"],
            cwd=target_dir, capture_output=True, text=True,
        )
        if fetch.returncode != 0:
            raise RuntimeError(
                f"materialize_pr_head: git fetch pull/{pr}/head "
                f"rc={fetch.returncode}: {fetch.stderr.strip()}"
            )
        switch = subprocess.run(
            ["git", "switch", "--no-overwrite-ignore", "--detach", sha],
            cwd=target_dir, capture_output=True, text=True,
        )
        if switch.returncode != 0:
            raise RuntimeError(
                f"materialize_pr_head: git switch --detach {sha[:7]} "
                f"rc={switch.returncode}: {switch.stderr.strip()}"
            )

    def changed_paths(self, target_dir: str, base_branch: str) -> list[str]:
        """Пути, изменённые HEAD относительно merge-base с origin/<base>.

        Гард путей accept-pr обязан быть привязан к МАТЕРИАЛИЗОВАННОМУ
        head0 (приёмка PR #113, круг 2): API-список файлов PR отражает
        голову ветки на момент запроса — force-push между запросами
        подменил бы проверяемый список (TOCTOU). Здесь дифф считается
        локально по уже переключённому дереву; базой служит FETCH_HEAD
        только что выполненного fetch (приёмка PR #113, круг 4): fetch без
        destination-refspec не обязан обновить refs/remotes/origin/<base>,
        и дифф против протухшего origin/<base> включил бы чужие коммиты
        базы — ложный authority-стоп. Сбой — RuntimeError.
        """
        fetch = subprocess.run(
            ["git", "fetch", "origin", base_branch],
            cwd=target_dir, capture_output=True, text=True,
        )
        if fetch.returncode != 0:
            raise RuntimeError(
                f"changed_paths: git fetch origin {base_branch} "
                f"rc={fetch.returncode}: {fetch.stderr.strip()}"
            )
        diff = subprocess.run(
            ["git", "diff", "--name-only", "FETCH_HEAD...HEAD"],
            cwd=target_dir, capture_output=True, text=True,
        )
        if diff.returncode != 0:
            raise RuntimeError(
                f"changed_paths: git diff FETCH_HEAD...HEAD "
                f"rc={diff.returncode}: {diff.stderr.strip()}"
            )
        return [line for line in diff.stdout.splitlines() if line.strip()]

    def head_sha(self, target_dir: str, branch: str) -> str:
        """SHA головы branch в target_dir."""
        done = subprocess.run(
            ["git", "rev-parse", branch],
            cwd=target_dir, capture_output=True, text=True, check=True,
        )
        return done.stdout.strip()

    def push_branch(self, target_dir: str, branch: str) -> None:
        """git push -u origin branch из target_dir."""
        subprocess.run(
            ["git", "push", "-u", "origin", branch], cwd=target_dir, check=True,
        )

    def checkout_and_pull(self, target_dir: str, branch: str) -> None:
        """`git switch <branch>` + `git pull --ff-only`; сбой — RuntimeError.

        S8 обязан гейтить authoritative-срез на default-ветке, не на
        feature-ветке прогона (финальное ревью, круг 5): без явного чекаута
        `gate_check_s8` унаследовал бы содержимое той ветки, на которой
        случайно стоит worktree.
        """
        switch = subprocess.run(
            ["git", "switch", branch],
            cwd=target_dir, capture_output=True, text=True,
        )
        if switch.returncode != 0:
            raise RuntimeError(
                f"checkout_and_pull: git switch {branch} "
                f"rc={switch.returncode}: {switch.stderr.strip()}"
            )
        pull = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=target_dir, capture_output=True, text=True,
        )
        if pull.returncode != 0:
            raise RuntimeError(
                f"checkout_and_pull: git pull --ff-only rc={pull.returncode}: "
                f"{pull.stderr.strip()}"
            )

    def find_pr(self, repo_slug: str, branch: str) -> int | None:
        """Номер открытого PR для branch; None ТОЛЬКО когда открытых PR нет.

        Сбой самого запроса — rc != 0, битый или неожиданный по форме JSON —
        не то же самое, что «PR нет» (финальное ревью F-5, круг 2): поднимает
        `RuntimeError`, чтобы reconciliation в runner'е не читала транзиентный
        сбой `gh` как отсутствие PR и не открывала второй PR на ту же ветку.
        """
        done = subprocess.run(
            ["gh", "pr", "list", "-R", repo_slug, "--head", branch,
             "--state", "open", "--json", "number"],
            capture_output=True, text=True,
        )
        if done.returncode != 0:
            raise RuntimeError(
                f"find_pr: gh pr list rc={done.returncode}: {done.stderr.strip()}"
            )
        try:
            found = json.loads(done.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"find_pr: invalid JSON: {done.stdout!r}") from exc
        if not isinstance(found, list):
            raise RuntimeError(f"find_pr: unexpected JSON shape: {done.stdout!r}")
        return found[0]["number"] if found else None

    def create_draft_pr(
        self,
        target_dir: str,
        repo_slug: str,
        branch: str,
        title: str,
        body: str,
        label: str,
    ) -> int:
        """gh pr create --draft [--label <label>] -R <slug>; номер из URL stdout.

        Пустой ``label`` не передаётся вовсе (решение владельца 2026-08-31:
        лейбл `codex-review` больше не вешается — он триггерил платный
        CI-контур codex поверх Actions-лимита; терминальное ревью
        review-pr.sh остаётся дефолтом и от лейбла не зависит).
        """
        label_args = ["--label", label] if label else []
        done = subprocess.run(
            ["gh", "pr", "create", "--draft", "-R", repo_slug,
             "--head", branch, "--title", title, "--body", body,
             *label_args],
            cwd=target_dir, capture_output=True, text=True, check=True,
        )
        match = _PR_URL_RE.search(done.stdout)
        if not match:
            raise RuntimeError(f"create_draft_pr: no PR URL in {done.stdout!r}")
        return int(match.group(1))

    def mark_ready(self, repo_slug: str, pr: int) -> None:
        """gh pr ready — снять draft-статус."""
        subprocess.run(["gh", "pr", "ready", str(pr), "-R", repo_slug], check=True)

    def review(self, repo_name: str, pr: int) -> int:
        """Прогон review-pr.sh из корня devtools; возврат = returncode как есть."""
        done = subprocess.run(
            ["sh", str(DEVTOOLS_ROOT / "review-pr.sh"), repo_name, str(pr)],
            cwd=DEVTOOLS_ROOT,
        )
        return done.returncode

    def review_fresh(self, repo_name: str, pr: int) -> int:
        """review-pr.sh --fresh — обход fp-наследования (авто-опровержение S6).

        Без --fresh пере-прогон по неизменному входу унаследовал бы тот же
        красный вердикт по отпечатку; --fresh обходит только поиск
        наследуемого, отпечаток вычисляется и публикуется как обычно.
        """
        done = subprocess.run(
            ["sh", str(DEVTOOLS_ROOT / "review-pr.sh"), repo_name, str(pr),
             "--fresh"],
            cwd=DEVTOOLS_ROOT,
        )
        return done.returncode

    def latest_review_body(self, repo_slug: str, pr: int) -> str | None:
        """Тело НОВЕЙШЕГО ревью $REVIEW_LOGIN на PR; нет/сбой -> None.

        Личность ревьюера — env `REVIEW_LOGIN` с дефолтом ai-prosto (канон —
        `review-pr.sh:66`; запаркованный minor приёмки PR #102): хардкод
        расходился бы с конфигурируемым каноном молча — публикация ушла бы
        под новый логин, поиск тела остался бы на старом, и авто-опровержение
        беззвучно умерло бы. None читается вызывающим как «опровергать
        нечего» (fail-closed в сторону стопа на человеке).
        """
        login = os.environ.get("REVIEW_LOGIN", "ai-prosto")
        done = subprocess.run(
            ["gh", "api", f"repos/{repo_slug}/pulls/{pr}/reviews",
             "--jq",
             f'[.[] | select(.user.login == "{login}")] | last | .body'],
            capture_output=True, text=True,
        )
        if done.returncode != 0:
            return None
        body = done.stdout.strip()
        return body if body and body != "null" else None

    def file_exists_at(self, target_dir: str, head: str, path: str) -> bool:
        """`git cat-file -e <head>:<path>` — файл существует в этой ревизии."""
        done = subprocess.run(
            ["git", "cat-file", "-e", f"{head}:{path}"],
            cwd=target_dir, capture_output=True, text=True,
        )
        return done.returncode == 0

    def pr_facts(self, repo_slug: str, pr: int) -> dict:
        """Сырой gh-JSON PR — интерпретация полей не входит в ops."""
        done = subprocess.run(
            ["gh", "pr", "view", str(pr), "-R", repo_slug, "--json",
             "mergeable,mergeStateStatus,statusCheckRollup,isDraft,"
             "headRefOid,baseRefName,state,mergedAt"],
            capture_output=True, text=True, check=True,
        )
        return json.loads(done.stdout)

    def pr_files(self, repo_slug: str, pr: int) -> list[str]:
        """Список путей файлов PR."""
        done = subprocess.run(
            ["gh", "pr", "view", str(pr), "-R", repo_slug,
             "--json", "files", "--jq", ".files[].path"],
            capture_output=True, text=True, check=True,
        )
        return [line for line in done.stdout.splitlines() if line]

    def unresolved_threads(self, repo_slug: str, pr: int) -> bool | None:
        """Есть ли непогашенный review thread; None = не смогли узнать.

        `first:100` без `pageInfo` был fail-open (финальное ревью, круг 7,
        codex-major): у PR со 101+ threads сотый и далее были невидимы, и
        PR мог прочитаться как «чисто» при непогашенном thread за первой
        страницей. `pageInfo.hasNextPage` запрашивается явно: при `True` за
        первой страницей может скрываться неразрешённый thread — результат
        `None` (unknown), не оптимистичное `False`; `facts_from` в runner'е
        уже трактует `None` как `unresolved_threads=True` (fail-closed).
        """
        owner, name = repo_slug.split("/", 1)
        query = (
            "query($o:String!,$n:String!,$p:Int!){repository(owner:$o,name:$n)"
            "{pullRequest(number:$p){reviewThreads(first:100)"
            "{pageInfo{hasNextPage}nodes{isResolved}}}}}"
        )
        done = subprocess.run(
            ["gh", "api", "graphql", "-f", f"query={query}",
             "-F", f"o={owner}", "-F", f"n={name}", "-F", f"p={pr}"],
            capture_output=True, text=True,
        )
        if done.returncode != 0:
            return None
        try:
            data = json.loads(done.stdout)
            review_threads = data["data"]["repository"]["pullRequest"]
            review_threads = review_threads["reviewThreads"]
            has_next_page = review_threads["pageInfo"]["hasNextPage"]
            nodes = review_threads["nodes"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return None
        if has_next_page:
            return None
        return any(not node["isResolved"] for node in nodes)

    def merge(self, repo_slug: str, pr: int, sha: str) -> bool:
        """PUT merge под профилем ai-prosto (ADR-ECO-011 D3); rc0->True."""
        env = {**os.environ, "GH_CONFIG_DIR": str(REVIEW_GH_CONFIG_DIR)}
        done = subprocess.run(
            ["gh", "api", "-X", "PUT", f"repos/{repo_slug}/pulls/{pr}/merge",
             "-f", "merge_method=merge", "-f", f"sha={sha}"],
            env=env, capture_output=True, text=True,
        )
        return done.returncode == 0

    def comment(self, repo_slug: str, pr: int, body: str) -> None:
        """gh pr comment."""
        subprocess.run(
            ["gh", "pr", "comment", str(pr), "-R", repo_slug, "--body", body],
            check=True,
        )

    def author(
        self, target_dir: str, kind: str, subject: str, bundle_dir: str
    ) -> int:
        """codex exec --ephemeral --sandbox workspace-write <prompt>.

        Промпт несёт канонические имена файлов и DSL гейта (боевой прогон
        kapelle#47: без них codex писал в своём диалекте — имена `01-`/`02-`,
        заголовки `### BS-*`/`REQ-*` без `traces:`/`checked_by`, и всё это
        приходилось конвертировать руками до S4).
        """
        rules = _AUTHOR_DSL.get(kind, "")
        target_file = _AUTHOR_FILENAMES.get(kind, "")
        prompt = (
            f"kind={kind} subject={subject!r} bundle_dir={bundle_dir}\n"
            f"Write EXACTLY one file: {bundle_dir}/{target_file} "
            "(this exact name; create parent dirs as needed).\n"
            f"{rules}\n"
            "Author the governance bundle content for this kind/subject."
        )
        done = subprocess.run(
            ["codex", "exec", "--ephemeral", "--sandbox", "workspace-write",
             prompt],
            cwd=target_dir,
        )
        return done.returncode

    def author_disp(self, target_dir: str, task: str) -> int:
        """disp `run --mode develop` — opt-in авторинг-бэкенд behaviour-spec узла.

        Спека §5 называла `disp --mode document`; такого РЕЖИМА у disp нет и
        не появилось. OQ-1 закрыт иначе (disputatio#52 → PR #64, 2026-09-01):
        приехал ВИД пайплайна `document`, выводимый из формы секции
        `[pipeline]` (`document_path`), команды прежние — `disp pipeline run`.
        Переключение на него — @id:behaviour-authoring-document-mode в
        `TODO.md` (нужен конфиг с оператор-чеклистом `doc`), не предмет
        этого коммита; до него используется `run --mode develop`.
        """
        done = subprocess.run(
            ["uv", "run", "--project", str(DEVTOOLS_ROOT.parent / "disputatio"),
             "disp", "run", "--mode", "develop", "--root", target_dir, task],
            cwd=target_dir,
        )
        return done.returncode

    def commit_paths(self, target_dir: str, paths: list[str], message: str) -> None:
        """`git add -- <paths>` (явный список, не `-A`) + коммит.

        Круг 5 (codex-ревью PR #88): `git add -A` сгребал в коммит прогона
        чужие незакоммиченные изменения где угодно в `target_dir` — заменено
        на явный список путей (runner передаёт ровно `[bundle_dir]`). Пустой
        индекс после `add` (нечего коммитить) — не ошибка.
        """
        subprocess.run(["git", "add", "--", *paths], cwd=target_dir, check=True)
        clean = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=target_dir,
        )
        if clean.returncode == 0:
            return
        subprocess.run(["git", "commit", "-m", message], cwd=target_dir, check=True)

    def gate_check_s8(
        self, target_dir: str, bundle_dir: str, profile: str
    ) -> tuple[int, str]:
        """gate-check <bundle_dir> --profile <profile> --emit-verdicts.

        Фактическая сигнатура CLI (``gate-check --help``, пинованный
        steward, прогнано вручную при реализации): ``Usage: gate-check
        [OPTIONS] [spec_dir]`` — bundle-путь ПОЗИЦИОННЫЙ (``[spec_dir]``,
        default ``spec``), флага ``--bundle`` не существует. Опции:
        ``--profile <str>`` (default ``lite``), ``--emit-verdicts`` (пишет
        ``<repo-root>/.steward/gate_verdicts.jsonl``, contract
        gate-verdicts/v1, требует live git provenance).

        Возвращает ``(returncode, combined_output)`` (финальное ревью M-2):
        §5 требует, чтобы findings S8 сохранялись в леджере прогона и в теле
        remediation-issue, а не только код возврата.
        """
        exe = DEVTOOLS_ROOT / ".venv" / "bin" / "gate-check"
        cmd = str(exe) if exe.exists() else "gate-check"
        done = subprocess.run(
            [cmd, bundle_dir, "--profile", profile, "--emit-verdicts"],
            cwd=target_dir, capture_output=True, text=True,
        )
        output = done.stdout + done.stderr
        return done.returncode, output

    def gate_check_candidate(
        self, target_dir: str, bundle_dir: str, profile: str
    ) -> tuple[int, str]:
        """gate-check --candidate <bundle_dir> --profile <profile>.

        Публичный prospective-режим steward#140 (steward @ 2c71ed7,
        docs/gate-check-candidate.md): проверяет содержимое каталога БЕЗ
        git-фактов; ref-зависимые гейты объявляются `not_evaluated` на
        stderr. Коды прежние: 0 чисто, 1 error-находки, 2 ошибка
        конфигурации. cwd=target_dir — профиль резолвится от репо, как в S8.
        """
        exe = DEVTOOLS_ROOT / ".venv" / "bin" / "gate-check"
        cmd = str(exe) if exe.exists() else "gate-check"
        done = subprocess.run(
            [cmd, "--candidate", bundle_dir, "--profile", profile],
            cwd=target_dir, capture_output=True, text=True,
        )
        return done.returncode, done.stdout + done.stderr

    def create_issue(self, repo_slug: str, title: str, body: str) -> int:
        """gh issue create -R <slug> --label inbox; номер из URL stdout."""
        done = subprocess.run(
            ["gh", "issue", "create", "-R", repo_slug, "--label", "inbox",
             "--title", title, "--body", body],
            capture_output=True, text=True, check=True,
        )
        match = _ISSUE_URL_RE.search(done.stdout)
        if not match:
            raise RuntimeError(f"create_issue: no issue URL in {done.stdout!r}")
        return int(match.group(1))

    def find_issue(self, repo_slug: str, body_prefix: str) -> int | None:
        """Номер открытого inbox-issue, чьё body начинается с body_prefix.

        `None` ТОЛЬКО когда такого issue нет; сбой самого запроса — rc != 0,
        битый или неожиданный по форме JSON — поднимает `RuntimeError` (как
        `find_pr`, F-5). Реконсиляция remediation-issue на S8 (круг 3,
        codex-ревью PR #88): гибель между `create_issue` и фиксацией op'а не
        должна читаться как «issue нет» и плодить дубликат.
        """
        done = subprocess.run(
            ["gh", "issue", "list", "-R", repo_slug, "--label", "inbox",
             "--state", "open", "--json", "number,body"],
            capture_output=True, text=True,
        )
        if done.returncode != 0:
            raise RuntimeError(
                f"find_issue: gh issue list rc={done.returncode}: "
                f"{done.stderr.strip()}"
            )
        try:
            found = json.loads(done.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"find_issue: invalid JSON: {done.stdout!r}") from exc
        if not isinstance(found, list):
            raise RuntimeError(f"find_issue: unexpected JSON shape: {done.stdout!r}")
        for item in found:
            body = item.get("body") or ""
            if body.startswith(body_prefix):
                return item["number"]
        return None
