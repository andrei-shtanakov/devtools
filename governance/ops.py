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

    def head_sha(self, target_dir: str, branch: str) -> str: ...

    def push_branch(self, target_dir: str, branch: str) -> None: ...

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

    def pr_facts(self, repo_slug: str, pr: int) -> dict: ...

    def pr_files(self, repo_slug: str, pr: int) -> list[str]: ...

    def unresolved_threads(self, repo_slug: str, pr: int) -> bool | None: ...

    def merge(self, repo_slug: str, pr: int, sha: str) -> bool: ...

    def comment(self, repo_slug: str, pr: int, body: str) -> None: ...

    def author(
        self, target_dir: str, kind: str, subject: str, bundle_dir: str
    ) -> int: ...

    def commit_all(self, target_dir: str, message: str) -> None: ...

    def gate_check_s8(
        self, target_dir: str, bundle_dir: str, profile: str
    ) -> tuple[int, str]: ...

    def create_issue(self, repo_slug: str, title: str, body: str) -> int: ...


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
        """gh pr create --draft --label <label> -R <slug>; номер из URL stdout."""
        done = subprocess.run(
            ["gh", "pr", "create", "--draft", "-R", repo_slug,
             "--head", branch, "--title", title, "--body", body,
             "--label", label],
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
        """Есть ли непогашенный review thread; None = не смогли узнать."""
        owner, name = repo_slug.split("/", 1)
        query = (
            "query($o:String!,$n:String!,$p:Int!){repository(owner:$o,name:$n)"
            "{pullRequest(number:$p){reviewThreads(first:100)"
            "{nodes{isResolved}}}}}"
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
            nodes = data["data"]["repository"]["pullRequest"]
            nodes = nodes["reviewThreads"]["nodes"]
        except (json.JSONDecodeError, KeyError, TypeError):
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
        """codex exec --ephemeral --sandbox workspace-write <prompt>."""
        prompt = (
            f"kind={kind} subject={subject!r} bundle_dir={bundle_dir}\n"
            "Author the governance bundle content for this kind/subject."
        )
        done = subprocess.run(
            ["codex", "exec", "--ephemeral", "--sandbox", "workspace-write",
             prompt],
            cwd=target_dir,
        )
        return done.returncode

    def commit_all(self, target_dir: str, message: str) -> None:
        """`git add -A` + коммит; пустой индекс (нечего коммитить) — не ошибка."""
        subprocess.run(["git", "add", "-A"], cwd=target_dir, check=True)
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
