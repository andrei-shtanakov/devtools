"""Ops — единственная точка внешних эффектов runner'а (спека §5/§8).

``Ops`` — протокол, закрывающий ВЕСЬ набор внешних вызовов (git/gh/codex/
gate-check), которыми пользуется behaviour runner. ``RealOps`` — тонкая
subprocess-обёртка над ним: каждый метод строит одну команду, разбирает её
результат в примитив (str/int/bool/list/dict) и ничего не интерпретирует —
решения (мерж/нет, готов ли бандл и т.п.) принимает runner (Task 4), не этот
модуль. ``FakeOps`` для тестов runner'а живёт в тестах runner'а, не здесь.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

DEVTOOLS_ROOT = Path(__file__).resolve().parent.parent
REVIEW_GH_CONFIG_DIR = Path.home() / ".config" / "review"

_PR_URL_RE = re.compile(r"/pull/(\d+)")
_ISSUE_URL_RE = re.compile(r"/issues/(\d+)")

#: Логин ревью-контура по умолчанию — канон `review-pr.sh:66`.
REVIEW_LOGIN_DEFAULT = "ai-prosto"


def review_login() -> str:
    """Учётка, от которой публикуется ревью флота (env `REVIEW_LOGIN`).

    Одно определение на весь слой: логин нужен и `latest_review_body`
    (чьё ревью искать), и вызывающему — чтобы отличить ревью
    ревью-контура от человеческого. Два хардкода разъехались бы молча.
    """
    return os.environ.get("REVIEW_LOGIN", REVIEW_LOGIN_DEFAULT)


class Ops(Protocol):
    """Протокол внешних эффектов (спека §5/§8) — сигнатуры дословны."""

    def ensure_branch(self, target_dir: str, branch: str) -> None: ...

    def is_dirty(self, target_dir: str) -> bool: ...

    def current_branch(self, target_dir: str) -> str | None: ...

    def materialize_pr_head(
        self, target_dir: str, pr: int, sha: str
    ) -> None: ...

    def changed_paths(
        self, target_dir: str, base_branch: str
    ) -> tuple[str, list[str]]: ...

    def head_sha(self, target_dir: str, branch: str) -> str: ...

    def push_branch(self, target_dir: str, branch: str) -> None: ...

    def checkout_and_pull(self, target_dir: str, branch: str) -> None: ...

    def find_pr(
        self, repo_slug: str, branch: str, *, any_state: bool = False
    ) -> int | None: ...

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

    def pr_reviews(self, repo_slug: str, pr: int) -> list[dict] | None: ...

    def merge(
        self, repo_name: str, pr: int, sha: str, base: str | None = None
    ) -> bool: ...

    def close_pr(self, repo_slug: str, pr: int, comment: str) -> bool: ...

    def remote_branch_head(
        self, repo_slug: str, branch: str
    ) -> str | None: ...

    def delete_remote_branch(self, repo_slug: str, branch: str) -> bool: ...

    def delete_local_branch(self, target_dir: str, branch: str) -> bool: ...

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

    def collect_gate_verdicts(self, target_dir: str, dest: str) -> bool: ...

    def gate_check_candidate(
        self, target_dir: str, bundle_dir: str, profile: str
    ) -> tuple[int, str]: ...

    def create_issue(self, repo_slug: str, title: str, body: str) -> int: ...

    def find_issue(self, repo_slug: str, body_prefix: str) -> int | None: ...

    def last_commit_touching(
        self, target_dir: str, rel_path: str
    ) -> str | None: ...

    def prs_containing_commit(
        self, repo_slug: str, sha: str
    ) -> list[dict]: ...

    def rev_parse(self, target_dir: str, ref: str) -> str | None: ...

    def blob_in_commit(
        self, target_dir: str, sha: str, rel_path: str
    ) -> str | None: ...

    def commit_parent(self, target_dir: str, sha: str) -> str | None: ...

    def show_file(self, target_dir: str, ref: str, path: str) -> str | None: ...


# --- Харнесс авторинга (лимиты codex, 2026-09-03; парный слой к харнессу
# ревьюера в review-pr.sh). Выбор: env AUTHOR_HARNESS/AUTHOR_MODEL >
# ~/.config/ai-prosto/harness.env > вшитый codex. Модель привязана к слою
# своего харнесса (урок ревью PR #121): харнесс со слоя выше не наследует
# модель слоя ниже. Файл ПАРСИТСЯ (KEY=VALUE, export/отступы терпимы),
# не исполняется.
_HARNESS_ENV_KEYS = ("AUTHOR_HARNESS", "AUTHOR_MODEL")


def _harness_env_values() -> dict[str, str]:
    """AUTHOR_*-строки операторского harness.env; нет файла — пусто."""
    path = Path(
        os.environ.get(
            "AI_PROSTO_HARNESS_ENV",
            str(Path.home() / ".config" / "ai-prosto" / "harness.env"),
        )
    )
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        for key in _HARNESS_ENV_KEYS:
            if line.startswith(f"{key}="):
                values[key] = line[len(key) + 1:]
    return values


def _author_argv(prompt: str) -> list[str]:
    """argv авторинг-агента по выбранному харнессу; неизвестный — ValueError.

    claude-ветка — паритет с флотским пресетом spec-runner для claude
    (`--dangerously-skip-permissions`): авторинг пишет файлы бандла и
    считает `git hash-object` для upstream_hashes, уровень доверия тот же,
    что у codex `--sandbox workspace-write` (свой промпт конвейера в
    целевом чекауте). --strict-mcp-config/--no-session-persistence — не
    тащить MCP оператора и не сорить сессиями в целевом репо.
    """
    cfg = _harness_env_values()
    env_harness = os.environ.get("AUTHOR_HARNESS", "")
    harness = env_harness or cfg.get("AUTHOR_HARNESS", "") or "codex"
    if env_harness:
        model = os.environ.get("AUTHOR_MODEL", "")
    else:
        model = os.environ.get("AUTHOR_MODEL", "") or cfg.get(
            "AUTHOR_MODEL", ""
        )
    if harness == "claude":
        return [
            "claude", "-p", "--model", model or "claude-opus-5",
            "--dangerously-skip-permissions",
            "--no-session-persistence", "--strict-mcp-config",
            prompt,
        ]
    if harness == "codex":
        argv = ["codex", "exec", "--ephemeral",
                "--sandbox", "workspace-write"]
        if model:
            argv += ["-m", model]
        return [*argv, prompt]
    raise ValueError(
        f"неизвестный AUTHOR_HARNESS: {harness!r} (claude|codex)"
    )


# Канонические имена файлов бандла (зеркало runner._AUTHOR_STEPS) и DSL-правила
# гейта для промпта авторинга. Держим в ops: промпт — часть точной команды.
_AUTHOR_FILENAMES = {
    "charter": "00-charter.md",
    "requirements": "10-requirements.md",
    "behaviour-spec": "15-behaviour-spec.md",
    "design": "20-design.md",
    "acceptance": "25-acceptance.md",
    "decomposition": "30-decomposition.md",
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
        "use `#### NFR-NN: <title>` followed by the same `**Priority**: Must` "
        "(or Should) line. Use FR-/NFR- ids consistently "
        "everywhere, including any traceability matrices. Every open "
        "question MUST be a bullet `- **Q-NN · owner_role: <role> · "
        "blocking: true|false.** <text>`; architect-level questions use "
        "owner_role: architects and are the design stage's input set."
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
    "design": (
        "YAML frontmatter (required): spec_stage: design, status: draft, "
        "owner_role: architects, traces_to: [requirements, behaviour-spec], "
        "upstream_hashes: {requirements: \"<hash10>\", behaviour-spec: "
        "\"<hash15>\"} where <hash10> and <hash15> are the outputs of "
        "`git hash-object <bundle_dir>/10-requirements.md` and "
        "`git hash-object <bundle_dir>/15-behaviour-spec.md` respectively. "
        "The document MUST contain these sections: Резолюции открытых "
        "вопросов, Механика, Рамки red-дизайна, Карта затрагиваемых "
        "модулей, Вне объёма. Резолюции открытых вопросов: every "
        "architect-level open question from 10-requirements.md (bullets "
        "`- **Q-NN · owner_role: architects · blocking: true|false.** "
        "<text>`) MUST appear exactly once as a heading `#### Q-NN · "
        "owner_role: architects · resolution: resolved|deferred` followed "
        "by a justification paragraph bounded by the product decisions "
        "already made in requirements; resolution: deferred MUST be "
        "followed by a line `reason: <named reason>`. If the input set is "
        "empty, the document MUST instead carry the exact line "
        "`Открытых архитектурных вопросов нет (входной набор пуст)`. "
        "Механика: точки врезки (модуль/функция), формы инвокаций, формы "
        "коммитов/эвиденции — what requirements deliberately left "
        "unspecified. Рамки red-дизайна: for measurement/artifact/boundary "
        "tasks, what the red test MUST verify live and what it MUST NOT "
        "assert (file existence etc). Карта затрагиваемых модулей: files/"
        "subsystems the implementation will touch — input for the future "
        "decomposition stage. Вне объёма: what design deliberately leaves "
        "to the implementer. Forbidden: do not reopen product decisions "
        "already made in requirements; do not write line-by-line code "
        "(that is the implementer's job under TDD); do not create new "
        "Q-* without owner_role."
    ),
    "acceptance": (
        "YAML frontmatter (required): spec_stage: acceptance, "
        "status: draft, owner_role: qa, traces_to: [requirements, "
        "behaviour-spec], upstream_hashes: {requirements: \"<hash10>\", "
        "behaviour-spec: \"<hash15>\"} where <hash10> and <hash15> are "
        "the outputs of `git hash-object <bundle_dir>/10-requirements.md` "
        "and `git hash-object <bundle_dir>/15-behaviour-spec.md`. "
        "The document MUST contain these sections: Критерии приёмки, "
        "Инварианты покрытия, Порог приёмки, Вне объёма. Критерии "
        "приёмки: every criterion is a heading exactly `#### AC-NN: "
        "<title> · verification: test|manual|metric` followed by "
        "metadata lines `traces: [FR-…|NFR-…]` (>=1, ids from "
        "10-requirements.md, both classes are legal) and `scenarios: "
        "[BEH-…]` (REQUIRED for verification: test — the criterion is "
        "proven by those green scenarios; optional otherwise), then a "
        "prose paragraph naming the observable sign of fulfilment. "
        "verification: manual — the prose MUST name what a human "
        "observes; verification: metric — the prose MUST name the "
        "SOURCE of the number (artifact or named constant), never "
        "hard-code the number in the criterion. Every Must-priority "
        "requirement (FR and NFR alike) MUST be covered by at least one "
        "AC; Should is at qa's discretion. If the input set "
        "of Must requirements is empty, the document MUST instead carry "
        "the exact line `Must-требований во входном наборе нет`. "
        "Порог приёмки: which ACs must hold before the workstream is "
        "declared delivered (default: all with verification: test; "
        "manual/metric — by enumeration). Вне объёма: what is "
        "deliberately not an acceptance criterion. Forbidden: do not "
        "migrate the charter's AC numbering (this node is the single "
        "source of acceptance, authored fresh from requirements/"
        "behaviour); do not invent FR/NFR/BEH ids; do not restate "
        "requirements as criteria without an observable sign."
    ),
    "decomposition": (
        "YAML frontmatter (required): spec_stage: decomposition, "
        "status: draft, owner_role: tech-lead, traces_to: [design, acceptance], "
        "upstream_hashes: {design: \"<hash20>\", acceptance: \"<hash25>\"} where "
        "<hash20> and <hash25> are the outputs of `git hash-object "
        "<bundle_dir>/20-design.md` and `git hash-object "
        "<bundle_dir>/25-acceptance.md`. "
        "The document MUST contain these sections: Задачи, Инварианты "
        "графа, Порядок и параллельность, Вне объёма. Задачи: every task "
        "is a heading exactly `#### DT-NN: <title> · type: "
        "implement|verify · owner: <role>` followed by metadata lines "
        "`scenarios: [BEH-…]` (>=1, BEH ids from 15-behaviour-spec.md), "
        "`depends_on: [DT-…]` (may be empty list; these edges are the "
        "single source of truth for EXECUTION ordering, and every DT "
        "MUST be declared in the document AFTER all DTs it depends_on — "
        "topological declaration order), `delivered_by: [DT-…]` "
        "(REQUIRED for type: verify, FORBIDDEN for type: implement), "
        "`parallel_group: <name|solo>`, then a prose paragraph (subject, "
        "boundaries). `verifies: [<file>, …]` (also acceptable as a block "
        "YAML list under the same key, one `- <file>` per line) is "
        "RECOMMENDED for type: verify when the observation group spans "
        "files edited by OTHER DTs (omitting it is legacy-compatible — a "
        "non-blocking form recommendation, not a delivery failure) and "
        "FORBIDDEN for type: implement. checked_by (carried by scenarios) "
        "remains the single source of EDITING ownership. verifies grants "
        "NO exemption from the single-owner invariant, in any form — "
        "single-owner is decided EXCLUSIVELY by checked_by (via scenarios) "
        "and never looks at verifies at all; list in verifies ONLY files "
        "this DT does not itself edit (owned by other DTs) — a file this "
        "DT covers via its own checked_by is a single-owner claim like any "
        "other, unaffected by whether it also appears in verifies "
        "(ownership always beats observation). Two-tier verifies-content "
        "guarantee — describe it honestly, they are NOT the same "
        "severity: (1) FATAL graph invariant (same rule as delivered_by): "
        "IF a file in verifies is owned (via checked_by) by some DT in the "
        "bundle, that owning DT MUST be in the transitive closure of THIS "
        "task's OWN depends_on — direct or transitive; if the owning DT is "
        "not reachable through depends_on, add an edge to it (or to a DT "
        "that transitively reaches it) — an owner outside the closure is a "
        "graph error and stops delivery. (2) NON-fatal form recommendation: "
        "a file in verifies that matches NO checked_by target anywhere in "
        "the bundle (a typo or an orphaned path) is only a warning shown "
        "at the gate — it does NOT stop delivery on its own; give every "
        "verifies entry a real, existing checked_by owner anyway so the "
        "fatal closure check in (1) can actually apply to it. Every BEH-* "
        "of the bundle "
        "MUST be covered by exactly one DT (no gaps, no duplicates). "
        "delivered_by "
        "MUST be a subset of the transitive closure of depends_on. checked_by "
        "target files of different DTs MUST NOT intersect (single owner "
        "per test file). The graph MUST be acyclic; do NOT add an "
        "artificial linear chain — only real dependencies. Only a task "
        "that depends on members of TWO OR MORE other parallel_groups "
        "(it merges them) MUST depend on ALL sink tasks of EACH such "
        "group; a point edge into a single foreign group is legitimate "
        "and requires no extra edges. Порядок и параллельность: which groups may "
        "run concurrently (operator documentation). Вне объёма: what is "
        "deliberately not decomposed. Forbidden: do not invent BEH ids; "
        "do not reopen design decisions; do not write implementation "
        "code. verify-first is DELIVERED (spec-runner#367 closed): tasks "
        "with type: verify are welcome where behaviour is already "
        "delivered by their delivered_by dependencies — the bridge "
        "renders them as verify_first tasks (live run of the declared "
        "group first; green means no red authoring is bought)."
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

    def changed_paths(
        self, target_dir: str, base_branch: str
    ) -> tuple[str, list[str]]:
        """База вердикта и пути, изменённые HEAD относительно неё.

        Гард путей accept-pr обязан быть привязан к МАТЕРИАЛИЗОВАННОМУ
        head0 (приёмка PR #113, круг 2): API-список файлов PR отражает
        голову ветки на момент запроса — force-push между запросами
        подменил бы проверяемый список (TOCTOU). Здесь дифф считается
        локально по уже переключённому дереву; базой служит FETCH_HEAD
        только что выполненного fetch (приёмка PR #113, круг 4): fetch без
        destination-refspec не обязан обновить refs/remotes/origin/<base>,
        и дифф против протухшего origin/<base> включил бы чужие коммиты
        базы — ложный authority-стоп. Сбой — RuntimeError.

        OID базы возвращается ЗДЕСЬ, а не добывается вызывающим отдельно
        (ревью #183, круг 3): пин мержа обязан назвать ту же базу, от
        которой посчитан гард путей, а `refs/remotes/origin/<base>` — уже
        другой ref, ровно тот протухающий, из-за которого круг 4 и перешёл
        на FETCH_HEAD. Один fetch — одна база — один ответ; читать
        FETCH_HEAD вторым вызовом снаружи значило бы снова развести
        источники, стоило бы кому-то вставить между ними ещё один fetch.
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
        base = subprocess.run(
            ["git", "rev-parse", "FETCH_HEAD"],
            cwd=target_dir, capture_output=True, text=True,
        )
        if base.returncode != 0 or not base.stdout.strip():
            raise RuntimeError(
                f"changed_paths: git rev-parse FETCH_HEAD "
                f"rc={base.returncode}: {base.stderr.strip()}"
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
        return (
            base.stdout.strip(),
            [line for line in diff.stdout.splitlines() if line.strip()],
        )

    def head_sha(self, target_dir: str, branch: str) -> str:
        """SHA головы branch в target_dir."""
        done = subprocess.run(
            ["git", "rev-parse", branch],
            cwd=target_dir, capture_output=True, text=True, check=True,
        )
        return done.stdout.strip()

    def push_branch(self, target_dir: str, branch: str) -> None:
        """`git push -u origin <branch>`; сбой — RuntimeError со stderr.

        Не `check=True`: наружу летел `CalledProcessError`, а `main`
        ловит только `RuntimeError` — оператор получал сырой трейсбек
        вместо сообщения. Ходовой случай — отвергнутый non-ff push
        (ветка на remote ушла вперёд локальной), и текст git'а из stderr
        для него и есть диагностика. Нормализация та же, что у
        `checkout_and_pull`.
        """
        done = subprocess.run(
            ["git", "push", "-u", "origin", branch],
            cwd=target_dir, capture_output=True, text=True,
        )
        if done.returncode != 0:
            raise RuntimeError(
                f"push_branch: git push -u origin {branch} "
                f"rc={done.returncode}: {done.stderr.strip()}"
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

    def find_pr(
        self, repo_slug: str, branch: str, *, any_state: bool = False
    ) -> int | None:
        """Номер PR для branch; None ТОЛЬКО когда таких PR нет.

        Дефолт — только открытые. `any_state=True` (реконсиляция доставки
        tasks-спеки, major терм. ревью #156) видит и вмерженные/закрытые:
        отсутствие ОТКРЫТОГО PR не значит «доставки не было» — спека могла
        быть доставлена и вмержена раньше. Сбой самого запроса — rc != 0,
        битый или неожиданный по форме JSON — не то же самое, что «PR нет»
        (финальное ревью F-5, круг 2): поднимает `RuntimeError`, чтобы
        reconciliation не читала транзиентный сбой `gh` как отсутствие PR
        и не открывала второй PR на ту же ветку.
        """
        done = subprocess.run(
            ["gh", "pr", "list", "-R", repo_slug, "--head", branch,
             "--state", "all" if any_state else "open", "--json", "number"],
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
        login = review_login()
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
             "headRefOid,baseRefName,state,mergedAt,mergedBy"],
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

    def pr_reviews(self, repo_slug: str, pr: int) -> list[dict] | None:
        """Ревью PR как `[{"login": ..., "state": ...}, ...]`; None — сбой.

        Проекция полей, не интерпретация: кто из ревьюеров человек и что
        значит его `state`, решает вызывающий — ops о ревью-контуре
        ничего не знает.

        `--paginate` с ПОСТРОЧНЫМ jq (`.[] | {...}`, без обёртки в
        массив): постраничные массивы пришлось бы склеивать вручную, как
        в `prs_containing_commit`, а поток объектов склеивается сам.

        None вместо исключения — та же форма, что у
        `unresolved_threads`: оба сигнала «в PR уже вмешались» вызывающий
        обязан читать fail-closed, и различать «сбой» от «сбой» ему
        нечем.
        """
        done = subprocess.run(
            ["gh", "api", f"repos/{repo_slug}/pulls/{pr}/reviews",
             "--paginate", "--jq",
             ".[] | {login: .user.login, state: .state}"],
            capture_output=True, text=True,
        )
        if done.returncode != 0:
            return None
        found: list[dict] = []
        for line in done.stdout.splitlines():
            if not line.strip():
                continue
            try:
                found.append(json.loads(line))
            except json.JSONDecodeError:
                return None
        return found

    def close_pr(self, repo_slug: str, pr: int, comment: str) -> bool:
        """`gh pr close --comment` под профилем ai-prosto; rc0->True.

        Профиль тот же, что у `merge`, и по той же причине (ADR-ECO-011
        D3): закрытие PR — действие агента, и оно обязано быть записано
        агентской учёткой. Уйдя от основного аккаунта, механика замены
        выглядела бы в истории решением человека.

        Комментарий обязателен аргументом, а не опционален: закрытие
        чужого предложения без объяснения — то же вмешательство, от
        которого замена защищается сама.
        """
        env = {**os.environ, "GH_CONFIG_DIR": str(REVIEW_GH_CONFIG_DIR)}
        done = subprocess.run(
            ["gh", "pr", "close", str(pr), "-R", repo_slug,
             "--comment", comment],
            env=env, capture_output=True, text=True,
        )
        return done.returncode == 0

    def remote_branch_head(self, repo_slug: str, branch: str) -> str | None:
        """SHA ветки на origin; None — ветки нет либо узнать не удалось.

        Спрашивается СВОЙ ref, а не `headRefOid` PR: после закрытия PR в
        ветку могли дописать, и вопрос «что удаляем прямо сейчас»
        задаётся именно ссылке. Сырой ответ форджи — интерпретация
        (совпал ли SHA с записанным) не входит в ops.
        """
        done = subprocess.run(
            ["gh", "api", f"repos/{repo_slug}/git/ref/heads/{branch}",
             "--jq", ".object.sha"],
            capture_output=True, text=True,
        )
        if done.returncode != 0:
            return None
        return done.stdout.strip() or None

    def delete_remote_branch(self, repo_slug: str, branch: str) -> bool:
        """DELETE refs/heads/<branch> на origin; rc0->True.

        False — и «нет прав», и «ветки уже нет»: различать нечем, а
        вызывающему разница не нужна — шаг удаления идемпотентен по
        смыслу (ветки нет ⇒ шаг состоялся). Коммиты и сам PR остаются:
        удаляется живая ветка, не история.
        """
        env = {**os.environ, "GH_CONFIG_DIR": str(REVIEW_GH_CONFIG_DIR)}
        done = subprocess.run(
            ["gh", "api", "-X", "DELETE",
             f"repos/{repo_slug}/git/refs/heads/{branch}"],
            env=env, capture_output=True, text=True,
        )
        return done.returncode == 0

    def delete_local_branch(self, target_dir: str, branch: str) -> bool:
        """`git branch -D` в клоне; rc0->True (нет ветки -> False).

        Именно `-D`: отзываемая ветка по построению не вмержена, и `-d`
        отказал бы на каждой. Парный шаг к `delete_remote_branch` —
        удаляется ссылка в обеих половинах, коммиты остаются достижимы
        через закрытый PR.
        """
        done = subprocess.run(
            ["git", "-C", target_dir, "branch", "-D", branch],
            capture_output=True, text=True,
        )
        return done.returncode == 0

    def merge(
        self, repo_name: str, pr: int, sha: str, base: str | None = None
    ) -> bool:
        """Мерж через `merge-pr.sh` — единственный путь агентского мержа.

        Раньше здесь стоял прямой `gh api -X PUT …/merge` от профиля
        ai-prosto. Он ходил мимо гвардов обвязки, и утверждение CLAUDE.md
        «агентский мерж только через merge-pr.sh» было ложным ровно на два
        живых пути — `accept-pr` и S7 раннера (оба вызывают этот метод).
        Дублировать гварды в питоне значило бы завести второе место одного
        правила; вместо этого метод стал вызовом обвязки.

        Аргумент — имя КАТАЛОГА репо во флоте, как у `review` (обвязка сама
        выводит slug из сырого origin этого чекаута). `--merge` сохраняет
        прежнюю стратегию (`merge_method=merge`), `--expect-head` — прежний
        пин головы (`sha=`), `--expect-base` — новый пин базы, если
        вызывающий знает, от чего вынесен вердикт.

        Профиль обвязка выставляет и сверяет сама, поэтому GH_CONFIG_DIR
        здесь больше не собирается: сверка логина живёт в одном месте.
        """
        argv = [
            "sh", str(DEVTOOLS_ROOT / "merge-pr.sh"), repo_name, str(pr),
            "--merge", "--expect-head", sha,
        ]
        if base:
            argv += ["--expect-base", base]
        done = subprocess.run(argv, cwd=DEVTOOLS_ROOT)
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
        """Авторинг-агент по выбранному харнессу (см. `_author_argv`).

        Исторически: codex exec --ephemeral --sandbox workspace-write.
        Промпт несёт канонические имена файлов и DSL гейта (боевой прогон
        kapelle#47: без них codex писал в своём диалекте — имена `01-`/`02-`,
        заголовки `### BS-*`/`REQ-*` без `traces:`/`checked_by`, и всё это
        приходилось конвертировать руками до S4).

        Ошибка конфигурации харнесса — код 2 с причиной на stdout, не
        traceback: шаг authoring остаётся resumable после правки конфига.
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
        try:
            argv = _author_argv(prompt)
        except ValueError as exc:
            print(f"author: {exc}")
            return 2
        done = subprocess.run(argv, cwd=target_dir)
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

    def collect_gate_verdicts(self, target_dir: str, dest: str) -> bool:
        """Переносит <target>/.steward/gate_verdicts.jsonl в dest.

        Ретроспектива 2026-09-02 (@id:runner-s8-verdicts-cleanup):
        ``gate-check --emit-verdicts`` пишет verdicts в корень ЦЕЛЕВОГО
        репо — оставленный там файл делает чекаут грязным и спотыкает
        dirty-гард task_bridge на следующем шаге конвейера. Evidence
        переезжает в журнал прогона (dest); пустой ``.steward/``
        прибирается. Возвращает True, если файл был.
        """
        src = Path(target_dir) / ".steward" / "gate_verdicts.jsonl"
        if not src.exists():
            return False
        dest_path = Path(dest)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest_path))
        with contextlib.suppress(OSError):
            src.parent.rmdir()
        return True

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

    def last_commit_touching(self, target_dir: str, rel_path: str) -> str | None:
        """SHA последнего коммита, изменившего rel_path; None — не менялся.

        HEAD проверяется ПЕРВЫМ: в репозитории без коммитов `git log`
        выходит с rc != 0, и трактовать это как сбой значило бы отказывать
        там, где ответ честно «истории нет» (замечание ревью PR #164).
        """
        if self.rev_parse(target_dir, "HEAD") is None:
            return None
        done = subprocess.run(
            ["git", "-C", target_dir, "log", "-1", "--format=%H", "--", rel_path],
            capture_output=True, text=True,
        )
        if done.returncode != 0:
            raise RuntimeError(
                f"last_commit_touching: git log rc={done.returncode}: "
                f"{done.stderr.strip()}"
            )
        sha = done.stdout.strip()
        return sha or None

    def prs_containing_commit(self, repo_slug: str, sha: str) -> list[dict]:
        """PR-ы, содержащие коммит (gh API). Сбой запроса — RuntimeError.

        Эндпоинт отдаёт REST-форму: `state: open|closed`, `merged_at`,
        `merge_commit_sha`, и `merged_by` в нём приходит **null** даже у
        вмерженного PR (проверено на живом ответе, замечание ревью #164).
        Поэтому здесь только нормализация состава: snake_case → ожидаемые
        ключи, `state` → OPEN|CLOSED|MERGED по факту `merged_at`. Подпись
        (`mergedBy`) берётся отдельным запросом `pr_facts` — см. Task 3.

        Запрос идёт `--paginate`: эндпоинт REST-пагинируемый (30 на
        страницу по умолчанию), а `_resolve_correction_pr` (§I7) на
        списке кандидатов держит гвард «ровно один» — усечение по первой
        странице выродило бы его в молчаливый выбор первого. `--slurp`
        для этого непригоден (`gh` 2.98: «the --slurp option is not
        supported with --jq»), поэтому jq-выражение остаётся прежним, а
        `--paginate` печатает ПО МАССИВУ НА СТРАНИЦУ подряд — отсюда
        разбор stdout как последовательности JSON-документов, а не
        одного (проверено живым `gh` на трёхстраничной выдаче).
        """
        done = subprocess.run(
            ["gh", "api", f"repos/{repo_slug}/commits/{sha}/pulls",
             "--paginate",
             "--jq", "[.[] | {number, "
                     "state: (if .merged_at then \"MERGED\" "
                     "else (.state | ascii_upcase) end), "
                     "baseRefName: .base.ref, mergedAt: .merged_at, "
                     "mergeCommit: .merge_commit_sha}]"],
            capture_output=True, text=True,
        )
        if done.returncode != 0:
            raise RuntimeError(
                f"prs_containing_commit: gh api rc={done.returncode}: "
                f"{done.stderr.strip()}"
            )
        found: list[dict] = []
        decoder = json.JSONDecoder()
        text, pos = done.stdout, 0
        while True:
            while pos < len(text) and text[pos].isspace():
                pos += 1
            if pos >= len(text):
                return found
            try:
                page, pos = decoder.raw_decode(text, pos)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"prs_containing_commit: invalid JSON: {done.stdout!r}"
                ) from exc
            if not isinstance(page, list):
                raise RuntimeError(
                    f"prs_containing_commit: unexpected JSON shape: "
                    f"{done.stdout!r}"
                )
            found.extend(page)

    def rev_parse(self, target_dir: str, ref: str) -> str | None:
        """SHA ссылки; None — ссылки нет (нормальный случай, не сбой)."""
        done = subprocess.run(
            ["git", "-C", target_dir, "rev-parse", "--verify", "--quiet", ref],
            capture_output=True, text=True,
        )
        return done.stdout.strip() or None

    def blob_in_commit(
        self, target_dir: str, sha: str, rel_path: str
    ) -> str | None:
        """blob-хеш файла в коммите; None — файла в нём нет."""
        done = subprocess.run(
            ["git", "-C", target_dir, "rev-parse", f"{sha}:{rel_path}"],
            capture_output=True, text=True,
        )
        return done.stdout.strip() if done.returncode == 0 else None

    def commit_parent(self, target_dir: str, sha: str) -> str | None:
        """SHA первого родителя; None — корневой коммит либо нет коммита."""
        done = subprocess.run(
            ["git", "-C", target_dir, "rev-parse", "--verify", "--quiet",
             f"{sha}^1"],
            capture_output=True, text=True,
        )
        return done.stdout.strip() or None

    def show_file(self, target_dir: str, ref: str, path: str) -> str | None:
        """`git show <ref>:<path>` — содержимое файла в ревизии, или None.

        None — и когда ревизии нет, и когда файла в ней нет: у вызывающего
        оба случая означают одно (вывод не удался, §I8), различать нечего.
        """
        done = subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            cwd=target_dir, capture_output=True, text=True,
        )
        return done.stdout if done.returncode == 0 else None
