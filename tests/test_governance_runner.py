"""Поведенческие тесты runner'а S0–S7: FakeOps + reconciliation (спека §4)."""

from __future__ import annotations

import json
import os
import re
from types import SimpleNamespace
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("steward")

from governance import (
    brief_input,
    bundle_dag,
    bundle_state,
    merge_gate,
    runner,
    task_bridge,
)
from governance import ops as ops_mod
from governance import interview as iv
from governance import run_state as rs
from governance.stale_adapter import blob_sha1, blob_sha1_bytes
from tests.governance_fixtures.bundles import make_bundle, make_profile

GREEN_PR_FACTS: dict[str, Any] = {
    "statusCheckRollup": [{"conclusion": "SUCCESS"}],
    "mergeable": "MERGEABLE",
    "mergeStateStatus": "CLEAN",
    "isDraft": False,
    "headRefOid": "deadbeef",
    "baseRefName": "master",
    "state": "OPEN",
    "mergedAt": None,
}
BUNDLE_DIR = "workstreams/WS-1/spec"
GREEN_BUNDLE_FILES = [f"{BUNDLE_DIR}/15-behaviour-spec.md"]

# Тела по умолчанию `FakeOps.author` для requirements/behaviour-spec —
# вынесены в константы, потому что design (S4 Task 4) обязан пиновать их
# blob-хешем в своих upstream_hashes, иначе собственный же гард
# GC-UNPINNED(prospective) остановит каждый дефолтный S4-прогон.
_DEFAULT_REQUIREMENTS_BODY = "#### FR-01: x\n**Priority**: Must\n"
_DEFAULT_BEHAVIOUR_BODY = (
    "#### BEH-01: x\n`traces: [FR-01]`\n- **checked_by**: x\n"
)

_FR_ID_RE = re.compile(r"^####\s+((?:FR|NFR)-\d+[a-z]?):", re.M)
_PRIORITY_RE = re.compile(r"^\*\*Priority\*\*:\s*(\S+)", re.M)


def _acceptance_body(req_text: str, req_pin: str, beh_pin: str) -> str:
    """25-acceptance.md валидный наперёд для S4-гардов acceptance (Task 6,
    `governance/acceptance_guard.py`): два пина upstream по ФАКТИЧЕСКОМУ
    содержимому requirements/behaviour-spec (паттерн design), один
    `#### AC-01: … · verification: manual` с `traces:` на реальный Must-FR
    фикстуры; если Must-требований нет — traces на любой существующий
    FR/NFR + строка-декларация `Must-требований во входном наборе нет`
    (§3 спеки); все 4 обязательные секции DSL
    (`governance/ops.py::_AUTHOR_DSL["acceptance"]`)."""
    heads = list(_FR_ID_RE.finditer(req_text))
    must_id: str | None = None
    any_id: str | None = None
    for idx, m in enumerate(heads):
        end = heads[idx + 1].start() if idx + 1 < len(heads) else len(req_text)
        block = req_text[m.end():end]
        if any_id is None:
            any_id = m.group(1)
        pr = _PRIORITY_RE.search(block)
        if must_id is None and pr is not None and pr.group(1) == "Must":
            must_id = m.group(1)
    trace_id = must_id or any_id or "FR-01"
    declaration = (
        "" if must_id is not None
        else "Must-требований во входном наборе нет\n\n"
    )
    return (
        "---\n"
        "spec_stage: acceptance\n"
        "status: draft\n"
        "owner_role: qa\n"
        "traces_to: [requirements, behaviour-spec]\n"
        "upstream_hashes:\n"
        f'  requirements: "{req_pin}"\n'
        f'  behaviour-spec: "{beh_pin}"\n'
        "---\n"
        "## Критерии приёмки\n\n"
        f"{declaration}"
        "#### AC-01: x · verification: manual\n"
        f"traces: [{trace_id}]\n"
        "Наблюдаемый признак: человек видит x.\n\n"
        "## Инварианты покрытия\n\nMust-требования покрыты хотя бы одним AC.\n\n"
        "## Порог приёмки\n\nAC-01 обязателен к выполнению.\n\n"
        "## Вне объёма\n\nНичего не исключено.\n"
    )


def test_author_steps_include_decomposition_after_design() -> None:
    keys = [k for k, _, _ in runner._AUTHOR_STEPS]
    assert keys.index("author-design") < keys.index("author-decomposition")
    assert runner._AUTHOR_STEPS[-1] == (
        "author-decomposition", "decomposition", "30-decomposition.md",
    )


def test_author_steps_include_acceptance_between_design_and_decomposition() -> None:
    keys = [k for k, _, _ in runner._AUTHOR_STEPS]
    assert keys.index("author-design") < keys.index("author-acceptance")
    assert keys.index("author-acceptance") < keys.index("author-decomposition")


@dataclass
class FakeOps:
    """Ops-сценарий для тестов runner'а: журнал вызовов + управляемый исход."""

    existing_branches: set[str] = field(default_factory=set)
    existing_prs: dict[str, int] = field(default_factory=dict)
    review_exit: int = 0
    review_fresh_exit: int = 0
    review_body: str | None = None
    existing_files: set[str] = field(default_factory=set)
    ignored_paths: set[str] = field(default_factory=set)
    forced_paths: list[str] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)
    threads: bool | None = False
    merge_ok: bool = True
    # Код отказа обвязки: 4 — форджа отклонила, 3 — гвард.
    merge_code: int = 4
    head: str = "deadbeef"
    s8_exit: int = 0
    s8_output: str = ""
    collect_verdicts_ok: bool = True
    # Очередь ответов collect_gate_verdicts; пустая -> collect_verdicts_ok
    collect_verdicts_queue: list[bool] = field(default_factory=list)
    # Очередь ответов S4 `gate_check_candidate`; пустая/исчерпанная -> (0, "")
    gate_candidate: list[tuple[int, str]] = field(default_factory=list)
    find_pr_error: str | None = None
    dirty: bool = False
    checkout_and_pull_error: str | None = None
    head_sha_error: str | None = None
    authored: list[str] = field(default_factory=list)
    author_contexts: list[tuple[str, dict[str, object] | None]] = field(
        default_factory=list
    )
    author_disp_calls: list[tuple[str, str, str, str]] = field(default_factory=list)
    author_disp_resume: list[bool] = field(default_factory=list)
    author_disp_exit: int = 0
    comments: list[str] = field(default_factory=list)
    merged: list[tuple[int, str]] = field(default_factory=list)
    #: Чем адресован мерж: обвязка `merge-pr.sh` берёт имя КАТАЛОГА репо
    #: во флоте, а не slug — иначе она не найдёт чекаут и не выведет slug.
    merge_targets: list[str] = field(default_factory=list)
    issues: list[tuple[str, str, str]] = field(default_factory=list)
    committed: list[tuple[str, list[str], str]] = field(default_factory=list)
    checked_out: list[tuple[str, str]] = field(default_factory=list)
    calls: list[tuple] = field(default_factory=list)
    # Очередь ответов discovery: ("start"|"status"|"brief", DiscoveryReply).
    discovery: list[tuple[str, Any]] = field(default_factory=list)
    discovery_calls: list[tuple] = field(default_factory=list)
    # Текст, который `discovery_brief` пишет в `out_path` при кодах 0/10/11/20.
    brief_text: str = ""
    # Байты вместо `brief_text`, если заданы (напр. невалидный UTF-8 —
    # finding 3 финальной волны ревью: UnicodeDecodeError вместо traceback).
    brief_bytes: bytes | None = None

    def ensure_branch(self, target_dir: str, branch: str) -> None:
        self.calls.append(("ensure_branch", branch))
        self.existing_branches.add(branch)

    def is_dirty(self, target_dir: str) -> bool:
        self.calls.append(("is_dirty",))
        return self.dirty

    def head_sha(self, target_dir: str, branch: str) -> str:
        self.calls.append(("head_sha", branch))
        if self.head_sha_error is not None:
            raise RuntimeError(self.head_sha_error)
        return self.head

    def push_branch(self, target_dir: str, branch: str) -> None:
        self.calls.append(("push_branch", branch))

    def checkout_and_pull(self, target_dir: str, branch: str) -> None:
        self.calls.append(("checkout_and_pull", branch))
        if self.checkout_and_pull_error is not None:
            raise RuntimeError(self.checkout_and_pull_error)
        self.checked_out.append((target_dir, branch))

    def find_pr(self, repo_slug: str, branch: str) -> int | None:
        self.calls.append(("find_pr", branch))
        if self.find_pr_error is not None:
            raise RuntimeError(self.find_pr_error)
        return self.existing_prs.get(branch)

    def create_draft_pr(
        self, target_dir: str, repo_slug: str, branch: str, title: str,
        body: str, label: str,
    ) -> int:
        self.calls.append(("create_draft_pr", branch, label))
        number = 100 + len(self.existing_prs)
        self.existing_prs[branch] = number
        return number

    def mark_ready(self, repo_slug: str, pr: int) -> None:
        self.calls.append(("mark_ready", pr))

    def review(self, repo_name: str, pr: int) -> int:
        self.calls.append(("review", pr))
        return self.review_exit

    def pr_facts(self, repo_slug: str, pr: int) -> dict:
        self.calls.append(("pr_facts", pr))
        return self.facts

    def pr_files(self, repo_slug: str, pr: int) -> list[str]:
        self.calls.append(("pr_files", pr))
        return self.files

    def unresolved_threads(self, repo_slug: str, pr: int) -> bool | None:
        self.calls.append(("unresolved_threads", pr))
        return self.threads

    def merge(
        self, repo_name: str, pr: int, sha: str, base: str | None = None
    ) -> int:
        self.calls.append(("merge", pr, sha))
        self.merge_targets.append(repo_name)
        if self.merge_ok:
            self.merged.append((pr, sha))
        return 0 if self.merge_ok else self.merge_code

    def comment(self, repo_slug: str, pr: int, body: str) -> None:
        self.calls.append(("comment", pr, body))
        self.comments.append(body)

    def author(
        self, target_dir: str, kind: str, subject: str, bundle_dir: str,
        brief_context: dict[str, object] | None = None,
    ) -> int:
        self.calls.append(("author", kind))
        self.authored.append(kind)
        self.author_contexts.append((kind, brief_context))
        filename = {
            "charter": "00-charter.md",
            "requirements": "10-requirements.md",
            "behaviour-spec": "15-behaviour-spec.md",
            "design": "20-design.md",
            "acceptance": "25-acceptance.md",
            "decomposition": "30-decomposition.md",
        }[kind]
        path = Path(target_dir) / bundle_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        if kind == "design":
            # design пинует requirements/behaviour-spec по их ФАКТИЧЕСКОМУ
            # содержимому worktree на момент авторинга (_AUTHOR_STEPS гонит
            # design последним) — не по статической константе: тесты,
            # переопределяющие тело behaviour-spec через `super().author()`
            # + собственную дозапись, обязаны пиновать design верно без
            # знания об этом override (иначе свой же GC-STALE(prospective)
            # для ребра design→behaviour-spec стопил бы их несвязанные
            # сценарии).
            bundle = Path(target_dir) / bundle_dir

            def _blob_of(upstream_filename: str) -> str:
                upstream_path = bundle / upstream_filename
                text = (
                    upstream_path.read_text(encoding="utf-8")
                    if upstream_path.exists()
                    else ""
                )
                return blob_sha1(text)

            req_pin = _blob_of("10-requirements.md")
            beh_pin = _blob_of("15-behaviour-spec.md")
            path.write_text(
                "---\n"
                "spec_stage: design\n"
                "status: draft\n"
                "owner_role: architects\n"
                "traces_to: [requirements, behaviour-spec]\n"
                "upstream_hashes:\n"
                f'  requirements: "{req_pin}"\n'
                f'  behaviour-spec: "{beh_pin}"\n'
                "---\n"
                "Открытых архитектурных вопросов нет (входной набор пуст)\n",
                encoding="utf-8",
            )
            return 0
        if kind == "acceptance":
            # acceptance пинует requirements/behaviour-spec по их
            # ФАКТИЧЕСКОМУ содержимому worktree на момент авторинга
            # (_AUTHOR_STEPS гонит acceptance после design, до
            # decomposition) — тот же паттерн, что design использует для
            # своих upstream'ов. S4-гарды acceptance (Task 6,
            # `governance/acceptance_guard.py`) ещё не заведены в этой
            # задаче, но фикстура готовится валидной наперёд.
            bundle = Path(target_dir) / bundle_dir

            def _blob_of_acc(upstream_filename: str) -> str:
                upstream_path = bundle / upstream_filename
                text = (
                    upstream_path.read_text(encoding="utf-8")
                    if upstream_path.exists()
                    else ""
                )
                return blob_sha1(text)

            req_text = (
                (bundle / "10-requirements.md").read_text(encoding="utf-8")
                if (bundle / "10-requirements.md").exists()
                else ""
            )
            req_pin = _blob_of_acc("10-requirements.md")
            beh_pin = _blob_of_acc("15-behaviour-spec.md")
            path.write_text(
                _acceptance_body(req_text, req_pin, beh_pin),
                encoding="utf-8",
            )
            return 0
        if kind == "decomposition":
            # decomposition пинует design И acceptance по их ФАКТИЧЕСКОМУ
            # содержимому worktree на момент авторинга (_AUTHOR_STEPS гонит
            # decomposition последним) — тот же паттерн, что design
            # использует для requirements/behaviour-spec выше.
            # DT-01 покрывает BEH-01 из `_DEFAULT_BEHAVIOUR_BODY` — S4-гарды
            # decomposition (GC-COMPLETENESS/рёбра/DSL-empty, Task 6) ещё не
            # заведены в этой задаче, но фикстура готовится валидной наперёд.
            bundle = Path(target_dir) / bundle_dir
            design_path = bundle / "20-design.md"
            design_text = (
                design_path.read_text(encoding="utf-8")
                if design_path.exists()
                else ""
            )
            design_pin = blob_sha1(design_text)
            acceptance_path = bundle / "25-acceptance.md"
            acceptance_text = (
                acceptance_path.read_text(encoding="utf-8")
                if acceptance_path.exists()
                else ""
            )
            acceptance_pin = blob_sha1(acceptance_text)
            path.write_text(
                "---\n"
                "spec_stage: decomposition\n"
                "dt_contract_version: 2\n"
                "status: draft\n"
                "owner_role: tech-lead\n"
                "traces_to: [design, acceptance]\n"
                "upstream_hashes:\n"
                f'  design: "{design_pin}"\n'
                f'  acceptance: "{acceptance_pin}"\n'
                "---\n"
                "## Задачи\n\n"
                "#### DT-01: x · type: implement · owner: dev\n"
                "scenarios: [BEH-01]\n"
                "depends_on: []\n"
                "parallel_group: solo\n"
                "delivers: []\n"
                "Проза предмета.\n\n"
                "## Инварианты графа\n\nСоблюдены.\n\n"
                "## Порядок и параллельность\n\n"
                "DT-01 — единственная задача, зависимостей нет.\n\n"
                "## Вне объёма\n\nНичего не исключено.\n",
                encoding="utf-8",
            )
            return 0
        # Минимально DSL-корректное содержимое (зеркало обогащённого промпта
        # RealOps.author): гард GC-DSL-EMPTY в S4 читает эти файлы.
        body = {
            # `#### CON-01` — не украшение фикстуры: charter обязан быть
            # АДРЕСУЕМЫМ узлом (`charter#CON-01` в `sources`), и без
            # заголовка проверить это нечем.
            "charter": "# charter\n\n#### CON-01: ограничение\n\nтекст\n",
            "requirements": _DEFAULT_REQUIREMENTS_BODY,
            "behaviour-spec": _DEFAULT_BEHAVIOUR_BODY,
        }[kind]
        path.write_text(body, encoding="utf-8")
        return 0

    def author_disp(
        self, target_dir: str, task: str, config_path: str, slug: str,
        resume: bool = False,
    ) -> int:
        self.calls.append(("author_disp", task))
        self.author_disp_calls.append((target_dir, task, config_path, slug))
        self.author_disp_resume.append(resume)
        return self.author_disp_exit

    def review_fresh(self, repo_name: str, pr: int) -> int:
        self.calls.append(("review_fresh", pr))
        return self.review_fresh_exit

    def latest_review_body(self, repo_slug: str, pr: int) -> str | None:
        self.calls.append(("latest_review_body", pr))
        return self.review_body

    def file_exists_at(self, target_dir: str, head: str, path: str) -> bool:
        self.calls.append(("file_exists_at", head, path))
        return path in self.existing_files

    def gate_check_candidate(
        self, target_dir: str, bundle_dir: str, profile: str
    ) -> tuple[int, str]:
        self.calls.append(("gate_check_candidate", bundle_dir))
        if self.gate_candidate:
            return self.gate_candidate.pop(0)
        return (0, "")

    def gate_check_s8(
        self, target_dir: str, bundle_dir: str, profile: str
    ) -> tuple[int, str]:
        self.calls.append(("gate_check_s8", bundle_dir))
        return self.s8_exit, self.s8_output

    def collect_gate_verdicts(self, target_dir: str, dest: str) -> bool:
        self.calls.append(("collect_gate_verdicts", target_dir, dest))
        if self.collect_verdicts_queue:
            return self.collect_verdicts_queue.pop(0)
        return self.collect_verdicts_ok

    def create_issue(self, repo_slug: str, title: str, body: str) -> int:
        self.calls.append(("create_issue", repo_slug, title))
        self.issues.append((repo_slug, title, body))
        return 900 + len(self.issues)

    def find_issue(self, repo_slug: str, body_prefix: str) -> int | None:
        self.calls.append(("find_issue", body_prefix))
        for idx, (slug, _title, body) in enumerate(self.issues):
            if slug == repo_slug and body.startswith(body_prefix):
                return 900 + idx + 1
        return None

    def commit_paths(
        self, target_dir: str, paths: list[str], message: str,
        force_paths: tuple[str, ...] = (),
    ) -> None:
        self.calls.append(("commit_paths", tuple(paths)))
        self.committed.append((target_dir, paths, message))
        self.forced_paths.extend(force_paths)

    def rev_parse(self, target_dir: str, ref: str) -> str | None:
        return "fakehead"

    def blob_in_commit(
        self, target_dir: str, sha: str, rel_path: str
    ) -> str | None:
        """Модель git: файл под ignore-правилом попадает в коммит только
        через `-f` (`forced_paths`); остальное — байты рабочего дерева."""
        if rel_path in self.ignored_paths and rel_path not in self.forced_paths:
            return None
        path = Path(target_dir) / rel_path
        return blob_sha1_bytes(path.read_bytes()) if path.exists() else None

    def _discovery_reply(self, kind: str):
        assert self.discovery and self.discovery[0][0] == kind, (
            f"неожиданный вызов discovery {kind!r}; очередь {self.discovery!r}"
        )
        return self.discovery.pop(0)[1]

    def discovery_start(self, frame, target, traces_to, upstream_path, cwd):
        self.discovery_calls.append(
            ("start", frame, target, traces_to, upstream_path)
        )
        return self._discovery_reply("start")

    def discovery_status(self, session_id, cwd):
        self.discovery_calls.append(("status", session_id))
        return self._discovery_reply("status")

    def discovery_brief(self, session_id, out_path, cwd):
        self.discovery_calls.append(("brief", session_id, out_path))
        reply = self._discovery_reply("brief")
        # Стенд пишет артефакт при кодах 0/10/11/20, как сосед.
        if reply.code in (0, 10, 11, 20):
            if self.brief_bytes is not None:
                Path(out_path).write_bytes(self.brief_bytes)
            else:
                Path(out_path).write_text(self.brief_text, encoding="utf-8")
        return reply


@pytest.fixture(autouse=True)
def _disp_harness_env(monkeypatch):
    """Детерминированный харнесс-слой для disp-конфига во всём модуле.

    `ops.disp_agent` читает AUTHOR_*/REVIEW_* из окружения и harness.env
    машины; на CI файла нет, и codex без модели останавливал бы каждый
    disp-тест `stopped_author`. Тест, которому нужен иной харнесс, ставит
    свой env поверх (monkeypatch в теле теста побеждает autouse).
    """
    monkeypatch.setenv("AI_PROSTO_HARNESS_ENV", "/nonexistent")
    monkeypatch.setenv("AUTHOR_HARNESS", "claude")
    monkeypatch.setenv("REVIEW_HARNESS", "claude")
    monkeypatch.delenv("AUTHOR_MODEL", raising=False)
    monkeypatch.delenv("REVIEW_MODEL", raising=False)


@pytest.fixture()
def runs_root(tmp_path: Path, monkeypatch):
    root = tmp_path / "runs"
    monkeypatch.setattr(rs, "RUNS_ROOT", root)
    return root


# Task 8 (preflight): `_step_authoring` читает РЕАЛЬНЫЙ файл профиля в
# target_dir перед авторингом узлов design/acceptance/decomposition —
# devtools-канонический profiles/team-exp.yaml (6 авторимых узлов:
# charter/requirements/behaviour-spec/design/acceptance/decomposition,
# Task 1/Task 5), тот же, что реально несёт этот репо в проде.
_TEAM_EXP_PROFILE_TEXT = (
    Path(__file__).resolve().parent.parent / "profiles" / "team-exp.yaml"
).read_text(encoding="utf-8")


def _start_kwargs(tmp_path: Path, run_id: str, ops: FakeOps, **overrides):
    target_dir = tmp_path / f"target-{run_id}"
    target_dir.mkdir(exist_ok=True)
    kwargs = dict(
        subject="тестовый функционал",
        repo="alpha",
        repo_slug="owner/alpha",
        ws_id="WS-1",
        target_dir=str(target_dir),
        bundle_dir=BUNDLE_DIR,
        profile="profiles/team-exp.yaml",
        run_id=run_id,
        ops=ops,
    )
    kwargs.update(overrides)
    # Без материализации файла preflight (Task 8) стопил бы статусом
    # `stopped_preflight` ВСЕ существующие start()-тесты этого модуля —
    # раньше `profile` был только строкой в kwargs, ни один реальный файл
    # под ней не лежал. Пишем ровно тогда, когда итоговый `profile` —
    # канонический team-exp (иначе, например, `profiles/mini.yaml`-тесты
    # ниже по файлу сами несут свою фикстуру через `make_profile`).
    if kwargs["profile"] == "profiles/team-exp.yaml":
        profile_dir = Path(kwargs["target_dir"]) / "profiles"
        profile_dir.mkdir(parents=True, exist_ok=True)
        profile_path = profile_dir / "team-exp.yaml"
        if not profile_path.exists():
            profile_path.write_text(_TEAM_EXP_PROFILE_TEXT, encoding="utf-8")
    return kwargs


def _reply(code: int, **over) -> iv.DiscoveryReply:
    env = {
        "lifecycle": "awaiting_input", "gate": "unknown", "readiness": "unknown",
        "next_action": {"session_id": "s-1", "question_id": "Q-01"},
        "findings": [], "readiness_findings": [],
        "operation": {"status": "ok", "reason": ""},
    }
    if code == 0:
        env.update(lifecycle="complete", gate="pass", readiness="ready",
                    next_action={})
    if code in (10, 11):
        env.update(
            lifecycle="complete", gate="fail" if code == 10 else "pass",
            readiness="incomplete", next_action={},
            findings=[{"rule": "GC-04", "message": "x"}],
        )
    if code in (1, 2):
        env.update(
            lifecycle="unknown",
            operation={"status": "refused", "reason": "boom"},
        )
    env.update(over)
    return iv.DiscoveryReply(code, env, "")


def _need_spec(**over) -> iv.InterviewSpec:
    base = dict(
        frame="customer", stakeholder_role="po", target="owner/alpha",
        traces_to=None, upstream_blob=None,
    )
    base.update(over)
    return iv.InterviewSpec(**base)


def test_need_start_20_waits_without_branch(tmp_path: Path, runs_root) -> None:
    ops = FakeOps(discovery=[("start", _reply(20))])
    state = runner.start(
        **_start_kwargs(tmp_path, "r-need-1", ops), interview_spec=_need_spec()
    )
    assert state.status == "waiting_interview"
    assert state.interview["session_id"] == "s-1"
    assert state.ops["interview-start"]["status"] == "completed"
    assert ops.discovery_calls == [("start", "customer", "owner/alpha", None, None)]
    assert not any(c[0] in ("is_dirty", "ensure_branch") for c in ops.calls)
    assert state.branch == ""
    assert rs.load("r-need-1").status == "waiting_interview"


@pytest.mark.parametrize("code", [1, 2, 0, 10, 11])
def test_need_start_non_20_stops_without_session(
    tmp_path: Path, runs_root, code
) -> None:
    ops = FakeOps(discovery=[("start", _reply(code))])
    state = runner.start(
        **_start_kwargs(tmp_path, f"r-need-{code}", ops),
        interview_spec=_need_spec(),
    )
    assert state.status == "stopped_interview"
    assert state.interview["session_id"] is None
    assert state.ops["interview-start"]["status"] == "started"
    assert not any(c[0] in ("is_dirty", "ensure_branch") for c in ops.calls)
    persisted = rs.load(f"r-need-{code}")
    assert persisted.status == "stopped_interview"
    assert persisted.ops["interview-start"]["status"] == "started"
    assert persisted.interview["session_id"] is None


def _waiting_run(tmp_path: Path, runs_root, run_id: str, extra_replies: list):
    ops = FakeOps(discovery=[("start", _reply(20)), *extra_replies])
    state = runner.start(
        **_start_kwargs(tmp_path, run_id, ops), interview_spec=_need_spec()
    )
    assert state.status == "waiting_interview"
    return ops, state


def test_status_20_from_waiting_keeps_ledger_bytes(
    tmp_path: Path, runs_root, capsys: pytest.CaptureFixture[str]
) -> None:
    ops, _ = _waiting_run(
        tmp_path, runs_root, "r-w20", [("status", _reply(20))]
    )
    before = (rs.run_dir("r-w20") / "run.json").read_bytes()
    state = runner.resume("r-w20", ops)
    assert state.status == "waiting_interview"
    assert (rs.run_dir("r-w20") / "run.json").read_bytes() == before
    out = capsys.readouterr().out
    assert "discovery answer --session s-1 --role po" in out


def test_status_20_with_foreign_session_id_stops(
    tmp_path: Path, runs_root
) -> None:
    ops, _ = _waiting_run(
        tmp_path, runs_root, "r-w-foreign",
        [(
            "status",
            _reply(20, next_action={"session_id": "s-9", "question_id": "Q-02"}),
        )],
    )
    assert runner.resume("r-w-foreign", ops).status == "stopped_interview"


@pytest.mark.parametrize("code", [10, 11])
def test_status_10_11_stops_with_findings_and_template(
    tmp_path: Path, runs_root, code, capsys: pytest.CaptureFixture[str]
) -> None:
    ops, _ = _waiting_run(
        tmp_path, runs_root, f"r-w{code}", [("status", _reply(code))]
    )
    state = runner.resume(f"r-w{code}", ops)
    assert state.status == "stopped_interview"
    findings_path = rs.run_dir(f"r-w{code}") / "interview-findings.txt"
    assert "GC-04" in findings_path.read_text()
    out = capsys.readouterr().out
    assert "--question <QUESTION_ID> --supersede" in out


def test_stopped_then_status_20_returns_to_waiting(
    tmp_path: Path, runs_root
) -> None:
    ops, _ = _waiting_run(
        tmp_path, runs_root, "r-s20",
        [("status", _reply(10)), ("status", _reply(20))],
    )
    assert runner.resume("r-s20", ops).status == "stopped_interview"
    state = runner.resume("r-s20", ops)
    assert state.status == "waiting_interview"
    assert not (rs.run_dir("r-s20") / "interview-findings.txt").exists()


@pytest.mark.parametrize("code", [1, 2])
def test_findings_do_not_survive_a_later_operational_refusal(
    tmp_path: Path, runs_root, code
) -> None:
    """devtools#247: файл findings описывает ПОСЛЕДНИЙ ответ discovery.

    Прежде он удалялся только в ветке кода 20, поэтому переживал стоп по
    другой причине — и `spec-loop` печатал его путь как причину ТЕКУЩЕГО
    стопа. Перечислять переходы, которые его обесценивают, значит вести
    опись: инвариант дешевле и не забывается.
    """
    ops, _ = _waiting_run(
        tmp_path, runs_root, f"r-stale{code}",
        [("status", _reply(10)), ("status", _reply(code))],
    )
    assert runner.resume(f"r-stale{code}", ops).status == "stopped_interview"
    assert (rs.run_dir(f"r-stale{code}") / "interview-findings.txt").exists()

    state = runner.resume(f"r-stale{code}", ops)

    assert state.status == "stopped_interview"
    assert not (
        rs.run_dir(f"r-stale{code}") / "interview-findings.txt"
    ).exists()


def test_findings_do_not_survive_a_later_brief_refusal(
    tmp_path: Path, runs_root
) -> None:
    """Буквальный сценарий devtools#247: 10 → ответ с --supersede → status 0
    → brief-отказ по координатам. Стоп текущего захода — НЕ findings-стоп,
    и файл прошлого захода обязан исчезнуть до него."""
    ops = FakeOps(
        discovery=[
            ("start", _reply(20)),
            ("status", _reply(10)),
            ("status", _reply(0)),
            ("brief", _reply(0)),
        ],
        brief_text=_need_brief_text(target="owner/beta"),
    )
    runner.start(
        **_start_kwargs(tmp_path, "r-stale-brief", ops),
        interview_spec=_need_spec(),
    )
    findings = rs.run_dir("r-stale-brief") / "interview-findings.txt"
    assert runner.resume("r-stale-brief", ops).status == "stopped_interview"
    assert findings.exists()

    state = runner.resume("r-stale-brief", ops)

    assert state.status == "stopped_interview" and state.brief is None
    assert not findings.exists()


def test_findings_do_not_survive_the_brief_shortcut(
    tmp_path: Path, runs_root
) -> None:
    """Находка ревью на devtools#274: шорткат `_interview_poll` обходит
    разбор ответа.

    После brief 10/11 op `interview-brief` остаётся `started`, и следующий
    заход идёт СРАЗУ в `_interview_publish`, минуя `_interview_after_reply`.
    Пока чистка стояла там, файл прошлого захода переживал стоп по отказу
    координат — то есть исходный дефект #247 на этом пути сохранялся, а
    докстроки заявляли инвариант шире, чем он держался.
    """
    ops = FakeOps(
        discovery=[
            ("start", _reply(20)),
            ("status", _reply(0)),
            ("brief", _reply(11)),
            ("brief", _reply(0)),
        ],
        brief_text=_need_brief_text(target="owner/beta"),
    )
    runner.start(
        **_start_kwargs(tmp_path, "r-shortcut", ops),
        interview_spec=_need_spec(),
    )
    findings = rs.run_dir("r-shortcut") / "interview-findings.txt"
    assert runner.resume("r-shortcut", ops).status == "stopped_interview"
    assert findings.exists(), "предусловие: brief 11 записал findings"

    state = runner.resume("r-shortcut", ops)

    assert state.status == "stopped_interview" and state.brief is None
    assert not findings.exists()


@pytest.mark.parametrize("code", [1, 2])
def test_status_1_2_stops_and_keeps_session(
    tmp_path: Path, runs_root, code, capsys: pytest.CaptureFixture[str]
) -> None:
    ops, _ = _waiting_run(
        tmp_path, runs_root, f"r-e{code}", [("status", _reply(code))]
    )
    state = runner.resume(f"r-e{code}", ops)
    assert state.status == "stopped_interview"
    assert state.interview["session_id"] == "s-1"
    out = capsys.readouterr().out
    assert "--new-run --ws-id" in out
    assert "s-1" in out


def test_orphan_stop_resume_does_not_call_discovery(
    tmp_path: Path, runs_root, capsys: pytest.CaptureFixture[str]
) -> None:
    ops = FakeOps(discovery=[("start", _reply(2))])
    runner.start(
        **_start_kwargs(tmp_path, "r-orphan", ops), interview_spec=_need_spec()
    )
    state = runner.resume("r-orphan", ops)
    assert state.status == "stopped_interview"
    assert [c for c in ops.discovery_calls if c[0] != "start"] == []
    assert "--session" in capsys.readouterr().out


def _customer_brief_text() -> str:
    return """\
---
spec_stage: discovery
status: draft
version: 1
generated_by: discovery-agent@test
generated_at: 2026-09-13
validation: pass
owner_role: product
schema: discovery-brief
schema_version: 1
feeds: [charter, requirements]
interview:
  frame: customer
  sessions:
    - participant_role: product-owner
coverage:
  goals: covered
  personas: covered
  jobs: covered
  functions: covered
  nfr: covered
  constraints: covered
  success_metrics: covered
  out_of_scope: covered
  gate_passed: true
open_questions: 0
blocking_open_questions: 0
conflicts: 0
traces_to: []
---

- **G-01** Goal
- **P-01** Persona
- **J-01** `traces: [G-01]` Job
#### FR-01: Feature `traces: [G-01, J-01]`
**Priority**: Must
**Acceptance**: works
#### NFR-01: Safety `traces: [CON-01]`
**Target**: zero writes
- **CON-01** Constraint
- **M-01** `traces: [G-01]` Metric
- **OUT-01** Not in scope
"""


def _need_brief_text(target: str = "owner/alpha", roles=("po",)) -> str:
    """Бриф, каким его рендерит discovery: `_customer_brief_text()` + H1
    после frontmatter + блок `sessions`, ЗАМЕНЁННЫЙ на заданные роли (пустой
    кортеж → `sessions: []`)."""
    base = _customer_brief_text()
    old_sessions = "  sessions:\n    - participant_role: product-owner\n"
    assert base.count(old_sessions) == 1
    new_sessions = (
        "  sessions:\n"
        + "".join(f"    - participant_role: {r}\n" for r in roles)
        if roles
        else "  sessions: []\n"
    )
    text = base.replace(old_sessions, new_sessions)
    head, body = text.split("---\n\n", 1)
    return head + "---\n\n" + iv.h1_line(target, "customer") + "\n\n" + body


def test_status_0_brief_0_publishes_and_continues_by_e1(
    tmp_path: Path, runs_root
) -> None:
    ops = FakeOps(
        discovery=[
            ("start", _reply(20)), ("status", _reply(0)), ("brief", _reply(0)),
        ],
        brief_text=_need_brief_text(),
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES,
    )
    runner.start(
        **_start_kwargs(tmp_path, "r-pub", ops), interview_spec=_need_spec()
    )
    state = runner.resume("r-pub", ops)
    assert state.interview["completed_at"]
    assert state.brief and state.brief["frame"] == "customer"
    brief = rs.run_dir("r-pub") / "brief-input" / "00-discovery" / "brief.md"
    assert brief.exists() and not brief.with_name(".brief.tmp").exists()
    assert state.ops["interview-brief"]["status"] == "completed"
    # E1: source layer материализован в бандл и charter получил brief_context
    assert state.ops["materialize-brief"]["status"] == "completed"
    assert any(c[0] == "author" and c[1] == "charter" for c in ops.calls)


def test_brief_20_returns_to_waiting_without_publish(
    tmp_path: Path, runs_root
) -> None:
    ops = FakeOps(
        discovery=[
            ("start", _reply(20)), ("status", _reply(0)), ("brief", _reply(20)),
        ],
        brief_text=_need_brief_text(),
    )
    runner.start(
        **_start_kwargs(tmp_path, "r-b20", ops), interview_spec=_need_spec()
    )
    state = runner.resume("r-b20", ops)
    assert state.status == "waiting_interview" and state.brief is None
    d = rs.run_dir("r-b20") / "brief-input" / "00-discovery"
    assert not (d / "brief.md").exists() and not (d / ".brief.tmp").exists()
    assert "interview-brief" not in state.ops


@pytest.mark.parametrize("code", [10, 11, 1, 2])
def test_brief_non_zero_stops_without_publish(
    tmp_path: Path, runs_root, code
) -> None:
    ops = FakeOps(
        discovery=[
            ("start", _reply(20)), ("status", _reply(0)), ("brief", _reply(code)),
        ],
        brief_text=_need_brief_text(),
    )
    runner.start(
        **_start_kwargs(tmp_path, f"r-b{code}", ops), interview_spec=_need_spec()
    )
    state = runner.resume(f"r-b{code}", ops)
    assert state.status == "stopped_interview" and state.brief is None
    assert not (
        rs.run_dir(f"r-b{code}") / "brief-input" / "00-discovery" / "brief.md"
    ).exists()


def test_brief_0_failing_inspect_or_coordinates_stops(
    tmp_path: Path, runs_root
) -> None:
    ops = FakeOps(
        discovery=[
            ("start", _reply(20)), ("status", _reply(0)), ("brief", _reply(0)),
        ],
        brief_text=_need_brief_text(target="owner/beta"),
    )
    runner.start(
        **_start_kwargs(tmp_path, "r-bad", ops), interview_spec=_need_spec()
    )
    state = runner.resume("r-bad", ops)
    assert state.status == "stopped_interview" and state.brief is None
    assert not (
        rs.run_dir("r-bad") / "brief-input" / "00-discovery" / "brief.md"
    ).exists()
    assert not any(c[0] in ("is_dirty", "ensure_branch") for c in ops.calls)


def test_brief_0_non_utf8_stops_without_traceback(
    tmp_path: Path, runs_root
) -> None:
    """Finding 3 финальной волны: `UnicodeDecodeError` из `tmp.read_text`
    не должен утекать наружу traceback'ом — рефузный путь стопит run."""
    ops = FakeOps(
        discovery=[
            ("start", _reply(20)), ("status", _reply(0)), ("brief", _reply(0)),
        ],
        brief_bytes=b"\xff\xfe invalid utf-8 brief",
    )
    runner.start(
        **_start_kwargs(tmp_path, "r-badutf8", ops), interview_spec=_need_spec()
    )
    state = runner.resume("r-badutf8", ops)
    assert state.status == "stopped_interview" and state.brief is None
    assert not (
        rs.run_dir("r-badutf8") / "brief-input" / "00-discovery" / "brief.md"
    ).exists()


def test_brief_0_with_second_participant_role_is_accepted(
    tmp_path: Path, runs_root
) -> None:
    """D3: роль — декларация; второй участник законен на штатном пути."""
    ops = FakeOps(
        discovery=[
            ("start", _reply(20)), ("status", _reply(0)), ("brief", _reply(0)),
        ],
        brief_text=_need_brief_text(roles=("po", "qa")),
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES,
    )
    runner.start(
        **_start_kwargs(tmp_path, "r-two", ops), interview_spec=_need_spec()
    )
    assert runner.resume("r-two", ops).brief is not None


def _published_run(
    tmp_path: Path, runs_root, run_id: str, brief_text: str | None = None
) -> FakeOps:
    """Прогон до опубликованного брифа (`interview-brief` completed)."""
    ops = FakeOps(
        discovery=[
            ("start", _reply(20)), ("status", _reply(0)), ("brief", _reply(0)),
        ],
        brief_text=brief_text or _need_brief_text(),
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES,
    )
    runner.start(
        **_start_kwargs(tmp_path, run_id, ops), interview_spec=_need_spec()
    )
    state = runner.resume(run_id, ops)  # status 0 → brief 0 → replace → brief.md
    assert state.brief is not None
    assert (rs.run_dir(run_id) / "brief-input/00-discovery/brief.md").exists()
    return ops


def _crash_after(state_run_id: str, brief_present: bool, tmp_present: bool) -> None:
    """Имитация гибели: op ``started``, дескриптора (``state.brief``) нет."""
    st = rs.load(state_run_id)
    st.brief = None
    st.interview["completed_at"] = None
    st.status = "running"
    st.ops["interview-brief"] = {"status": "started"}
    rs.save(st)
    d = rs.run_dir(state_run_id) / "brief-input" / "00-discovery"
    d.mkdir(parents=True, exist_ok=True)
    if not brief_present:
        (d / "brief.md").unlink(missing_ok=True)
    if tmp_present:
        (d / ".brief.tmp").write_text("stale", encoding="utf-8")


def test_crash_without_tmp_or_brief_re_renders(tmp_path: Path, runs_root) -> None:
    ops = _published_run(tmp_path, runs_root, "r-c0")
    _crash_after("r-c0", brief_present=False, tmp_present=False)
    ops.discovery = [("brief", _reply(0))]
    state = runner.resume("r-c0", ops)
    assert state.brief is not None
    assert state.ops["interview-brief"]["status"] == "completed"


def test_crash_with_stale_tmp_re_renders(tmp_path: Path, runs_root) -> None:
    ops = _published_run(tmp_path, runs_root, "r-c1")
    _crash_after("r-c1", brief_present=False, tmp_present=True)
    ops.discovery = [("brief", _reply(0))]
    state = runner.resume("r-c1", ops)
    assert state.brief is not None
    assert not (
        rs.run_dir("r-c1") / "brief-input/00-discovery/.brief.tmp"
    ).exists()


def test_crash_after_replace_reconciles_by_re_render_equality(
    tmp_path: Path, runs_root
) -> None:
    ops = _published_run(tmp_path, runs_root, "r-c2")
    before = (
        rs.run_dir("r-c2") / "brief-input/00-discovery/brief.md"
    ).read_bytes()
    _crash_after("r-c2", brief_present=True, tmp_present=False)
    ops.discovery = [("brief", _reply(0))]  # тот же brief_text ⇒ байты равны
    state = runner.resume("r-c2", ops)
    assert state.brief is not None
    assert (
        rs.run_dir("r-c2") / "brief-input/00-discovery/brief.md"
    ).read_bytes() == before
    # повторный рендер — во ВТОРОЙ tmp (§5.5), не в brief.md и не в .brief.tmp
    assert [c for c in ops.discovery_calls if c[0] == "brief"][-1][2].endswith(
        ".brief.reconcile.tmp"
    )


def test_crash_after_replace_with_diverged_render_stops(
    tmp_path: Path, runs_root
) -> None:
    ops = _published_run(tmp_path, runs_root, "r-c3")
    _crash_after("r-c3", brief_present=True, tmp_present=False)
    ops.brief_text = _need_brief_text(roles=("po", "qa"))  # другие байты
    ops.discovery = [("brief", _reply(0))]
    state = runner.resume("r-c3", ops)
    assert state.status == "stopped_interview" and state.brief is None


def test_crash_after_replace_requires_code_0_on_re_render(
    tmp_path: Path, runs_root
) -> None:
    ops = _published_run(tmp_path, runs_root, "r-c4")
    _crash_after("r-c4", brief_present=True, tmp_present=False)
    ops.discovery = [("brief", _reply(20))]
    assert runner.resume("r-c4", ops).status == "stopped_interview"


def test_attach_session_only_for_orphans_and_verifies_brief(
    tmp_path: Path, runs_root
) -> None:
    ops = FakeOps(
        discovery=[("start", _reply(2))], brief_text=_need_brief_text(roles=())
    )
    runner.start(
        **_start_kwargs(tmp_path, "r-att", ops), interview_spec=_need_spec()
    )
    ops.discovery = [("brief", _reply(20))]
    state = runner.attach_session("r-att", "s-77", ops)
    assert state.interview["session_id"] == "s-77"
    assert state.ops["interview-start"]["status"] == "completed"
    # повторное присоединение при записанном id — отказ
    with pytest.raises(ValueError):
        runner.attach_session("r-att", "s-78", ops)


def test_attach_session_rejects_foreign_role(tmp_path: Path, runs_root) -> None:
    ops = FakeOps(
        discovery=[("start", _reply(1))], brief_text=_need_brief_text(roles=("qa",))
    )
    runner.start(
        **_start_kwargs(tmp_path, "r-att-bad", ops), interview_spec=_need_spec()
    )
    ops.discovery = [("brief", _reply(20))]
    with pytest.raises(ValueError):
        runner.attach_session("r-att-bad", "s-77", ops)
    assert rs.load("r-att-bad").interview["session_id"] is None


@pytest.mark.parametrize("code", [1, 2])
def test_attach_session_rejects_render_codes_1_2(
    tmp_path: Path, runs_root, code
) -> None:
    ops = FakeOps(
        discovery=[("start", _reply(1))], brief_text=_need_brief_text(roles=())
    )
    runner.start(
        **_start_kwargs(tmp_path, f"r-att-{code}", ops), interview_spec=_need_spec()
    )
    ops.discovery = [("brief", _reply(code))]
    with pytest.raises(ValueError):
        runner.attach_session(f"r-att-{code}", "s-77", ops)


def _brief_source(tmp_path: Path) -> brief_input.BriefSource:
    path = tmp_path / "discovery-input.md"
    path.write_text(_customer_brief_text(), encoding="utf-8")
    return brief_input.inspect_brief(path)


class CoveredBriefOps(FakeOps):
    """FakeOps, чей author пишет charter с source-пинами и requirements с
    NFR-01 — так brief-прогон проходит source-гард и доходит до S3."""

    def author(
        self, target_dir, kind, subject, bundle_dir, brief_context=None,
    ):
        rc = super().author(
            target_dir, kind, subject, bundle_dir,
            brief_context=brief_context,
        )
        if kind == "charter":
            assert brief_context is not None
            pins = brief_context["source_blobs"]
            names = list(pins)
            path = Path(target_dir) / bundle_dir / "00-charter.md"
            path.write_text(
                "---\n"
                "spec_stage: charter\n"
                "status: draft\n"
                "owner_role: product\n"
                f"traces_to: [{', '.join(names)}]\n"
                "upstream_hashes:\n"
                + "".join(
                    f'  {name}: "{blob}"\n'
                    for name, blob in pins.items()
                )
                + "---\n# charter\n",
                encoding="utf-8",
            )
        if kind == "requirements":
            path = Path(target_dir) / bundle_dir / "10-requirements.md"
            path.write_text(
                path.read_text(encoding="utf-8")
                + "\n#### NFR-01: Safety\n**Priority**: Should\n",
                encoding="utf-8",
            )
        return rc


def test_brief_materializes_after_branch_and_reaches_two_author_prompts(
    tmp_path: Path, runs_root,
) -> None:
    ops = CoveredBriefOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES
    )
    source = _brief_source(tmp_path)
    kwargs = _start_kwargs(
        tmp_path, "r-brief-source", ops, brief_source=source,
        merge_authority="human",
    )

    state = runner.start(**kwargs)

    destination = (
        Path(state.target_dir) / state.bundle_dir / brief_input.PRIMARY_REL
    )
    assert destination.read_bytes() == source.primary_input.read_bytes()
    assert state.ops["branch"]["status"] == "completed"
    assert state.ops["materialize-brief"] == {
        "status": "completed",
        "source_blobs": dict(source.source_blobs),
    }
    contexts = dict(ops.author_contexts)
    assert contexts["charter"] == source.as_state()
    assert contexts["requirements"] == source.as_state()
    for kind in ("behaviour-spec", "design", "acceptance", "decomposition"):
        assert contexts[kind] is None


def test_brief_source_layer_is_force_added_and_verified_in_s3_commit(
    tmp_path: Path, runs_root,
) -> None:
    """Живой прогон 2026-09-14 (spec-runner#490): `.gitignore` цели держит
    `workstreams/*/spec/*` с carve-out только `!*.md`, `00-discovery/` под него
    не попадает — `git add -- <bundle_dir>` молча пропускал source-слой, и
    bundle-PR уезжал без файлов, на blob-ы которых пинуется charter."""
    source_full = f"{BUNDLE_DIR}/{brief_input.PRIMARY_REL}"
    ops = CoveredBriefOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES,
        ignored_paths={source_full},
    )
    source = _brief_source(tmp_path)
    kwargs = _start_kwargs(
        tmp_path, "r-brief-commit", ops, brief_source=source,
        merge_authority="human",
    )

    state = runner.start(**kwargs)

    assert state.ops["commit"]["status"] == "completed"
    assert ops.forced_paths == [source_full]
    assert [paths for _t, paths, _m in ops.committed] == [[BUNDLE_DIR]]
    assert "push_branch" in [c[0] for c in ops.calls]


def test_brief_source_layer_missing_from_commit_stops_before_push(
    tmp_path: Path, runs_root,
) -> None:
    """Подсадка: ops, который добавляет bundle_dir без `-f` (прежнее
    поведение) — гвард обязан остановить прогон до push и назвать файл."""

    class NoForceOps(CoveredBriefOps):
        def commit_paths(
            self, target_dir, paths, message, force_paths=(),
        ):
            super().commit_paths(target_dir, paths, message)  # -f потерян

    source_full = f"{BUNDLE_DIR}/{brief_input.PRIMARY_REL}"
    ops = NoForceOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES,
        ignored_paths={source_full},
    )
    source = _brief_source(tmp_path)
    kwargs = _start_kwargs(
        tmp_path, "r-brief-noforce", ops, brief_source=source,
        merge_authority="human",
    )

    state = runner.start(**kwargs)

    assert state.status == "stopped_author"
    assert state.ops["commit"]["status"] != "completed"
    assert "push_branch" not in [c[0] for c in ops.calls]
    findings = (rs.run_dir(state.run_id) / "brief-findings.txt").read_text(
        encoding="utf-8"
    )
    assert "source layer не в коммите S3" in findings
    assert source_full in findings


def test_completed_brief_materialization_detects_destination_tamper_before_author(
    tmp_path: Path, runs_root,
) -> None:
    ops = FakeOps(review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)
    source = _brief_source(tmp_path)
    state = runner.start(**_start_kwargs(
        tmp_path, "r-brief-tamper", ops, brief_source=source,
        merge_authority="human",
    ))
    destination = (
        Path(state.target_dir) / state.bundle_dir / brief_input.PRIMARY_REL
    )
    destination.write_text(
        _customer_brief_text().replace("Goal", "Changed"), encoding="utf-8"
    )
    state.status = "running"
    rs.save(state)
    calls_before = len(ops.calls)

    result = runner.advance(state, ops)

    assert result.status == "stopped_author"
    assert len(ops.calls) == calls_before
    findings = rs.run_dir(state.run_id) / "brief-findings.txt"
    assert "не совпадает" in findings.read_text(encoding="utf-8")


def test_brief_materialization_refuses_changed_durable_intake(
    tmp_path: Path, runs_root,
) -> None:
    source = _brief_source(tmp_path)
    state = rs.new_run(
        subject="brief", repo="alpha", repo_slug="owner/alpha", ws_id="WS-1",
        target_dir=str(tmp_path / "target"), bundle_dir=BUNDLE_DIR,
        profile="profiles/team-exp.yaml", run_id="r-brief-intake",
        brief=source.as_state(),
    )
    Path(state.target_dir).mkdir()
    intake = rs.run_dir(state.run_id) / "brief-input"
    brief_input.materialize(source, intake, ".")
    staged = intake / brief_input.PRIMARY_REL
    staged.write_text(
        _customer_brief_text().replace("Goal", "Changed"), encoding="utf-8"
    )
    state.ops["branch"] = {"status": "completed"}
    rs.save(state)
    ops = FakeOps()

    result = runner.advance(state, ops)

    assert result.status == "stopped_author"
    assert ops.authored == []
    assert not (Path(state.target_dir) / state.bundle_dir).exists()


def test_brief_coverage_stops_after_requirements_before_next_paid_author(
    tmp_path: Path, runs_root,
) -> None:
    source = _brief_source(tmp_path)
    ops = FakeOps()

    state = runner.start(**_start_kwargs(
        tmp_path, "r-brief-coverage", ops, brief_source=source,
        merge_authority="human",
    ))

    assert state.status == "stopped_author"
    assert ops.authored == ["charter", "requirements"]
    assert "author-behaviour" not in state.ops
    findings = (
        rs.run_dir(state.run_id) / "brief-findings.txt"
    ).read_text(encoding="utf-8")
    assert "GC-BRIEF-COVERAGE" in findings
    assert "NFR-01" in findings


def test_brief_source_pin_is_required_by_prospective_gate(
    tmp_path: Path, runs_root,
) -> None:
    source = _brief_source(tmp_path)

    class MissingSourcePinOps(FakeOps):
        def author(
            self, target_dir, kind, subject, bundle_dir, brief_context=None,
        ):
            rc = super().author(
                target_dir, kind, subject, bundle_dir,
                brief_context=brief_context,
            )
            path = Path(target_dir) / bundle_dir
            if kind == "charter":
                (path / "00-charter.md").write_text(
                    "---\nspec_stage: charter\nstatus: draft\n"
                    "traces_to: [discovery-brief]\n---\n# charter\n",
                    encoding="utf-8",
                )
            if kind == "requirements":
                req = path / "10-requirements.md"
                req.write_text(
                    req.read_text(encoding="utf-8")
                    + "\n#### NFR-01: Safety\n**Priority**: Should\n",
                    encoding="utf-8",
                )
            return rc

    state = runner.start(**_start_kwargs(
        tmp_path, "r-brief-unpinned", MissingSourcePinOps(),
        brief_source=source, merge_authority="human",
    ))

    assert state.status == "stopped_gate"
    findings = (
        rs.run_dir(state.run_id) / "gate-findings.txt"
    ).read_text(encoding="utf-8")
    assert "source-ребро discovery-brief без upstream_hashes" in findings


def test_real_candidate_gate_accepts_materialized_brief_source(
    tmp_path: Path, runs_root,
) -> None:
    """The real steward CLI accepts the source layer and charter source edge."""
    from governance.ops import DEVTOOLS_ROOT, RealOps

    if not (DEVTOOLS_ROOT / ".venv" / "bin" / "gate-check").exists():
        pytest.skip("gate-check CLI недоступен (uv sync без группы governance)")

    class CliBriefOps(_DtSmokeOps):
        def author(
            self, target_dir, kind, subject, bundle_dir, brief_context=None,
        ):
            rc = _DtSmokeOps.author(
                self, target_dir, kind, subject, bundle_dir
            )
            bundle = Path(target_dir) / bundle_dir
            if kind == "charter":
                assert brief_context is not None
                pins = brief_context["source_blobs"]
                (bundle / "00-charter.md").write_text(
                    "---\nspec_stage: charter\nstatus: draft\n"
                    "owner_role: product\n"
                    f"traces_to: [{', '.join(pins)}]\n"
                    "upstream_hashes:\n"
                    + "".join(
                        f'  {name}: "{blob}"\n'
                        for name, blob in pins.items()
                    )
                    + "---\n# Charter\n",
                    encoding="utf-8",
                )
            if kind == "requirements":
                path = bundle / "10-requirements.md"
                path.write_text(
                    path.read_text(encoding="utf-8")
                    + "\n#### NFR-01: Safety\n**Priority**: Should\n",
                    encoding="utf-8",
                )
            return rc

        def gate_check_candidate(
            self, target_dir: str, bundle_dir: str, profile: str
        ) -> tuple[int, str]:
            self.calls.append(("gate_check_candidate", bundle_dir))
            return RealOps().gate_check_candidate(
                target_dir, bundle_dir, profile
            )

    source = _brief_source(tmp_path)
    kwargs = _start_kwargs(
        tmp_path,
        "r-real-brief-gate",
        CliBriefOps(review_exit=1),
        brief_source=source,
        merge_authority="human",
    )
    roles = Path(kwargs["target_dir"]) / "profiles/roles.yaml"
    roles.write_text(
        (Path(__file__).parents[1] / "profiles/roles.yaml").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    state = runner.start(**kwargs)

    assert state.status != "stopped_gate", (
        (rs.run_dir(state.run_id) / "gate-findings.txt").read_text(
            encoding="utf-8"
        )
    )
    assert state.ops["gate-candidate"]["status"] == "completed"


def _green_bundle(profile, bundle) -> bundle_state.BundleState:
    return bundle_state.BundleState((), 0, None, (), ())


def _repin_bundle(bundle_dir: Path) -> None:
    """Пересчитывает `upstream_hashes` ВСЕЙ цепочки design→acceptance→
    decomposition по ТЕКУЩЕМУ содержимому requirements/behaviour-spec, в
    топологическом порядке — правка бандла человеком после стопа
    (resume-тесты) обязана перепиновать design (иначе GC-STALE(prospective)
    на ребре design→behaviour-spec), ЗАТЕМ acceptance (иначе GC-STALE на
    рёбрах acceptance→requirements/behaviour-spec), ЗАТЕМ decomposition
    (иначе design/acceptance протухли под decomposition — GC-STALE на
    рёбрах decomposition→design/acceptance, — а DT старой фикстуры
    ссылаются на исчезнувшие BEH ПОСТправочного behaviour-spec —
    GC-DT-GRAPH). Переименован из `_repin_design` (Task 6): бывший хелпер
    трогал только design, оставляя decomposition протухшим за ним;
    расширен acceptance-пином (Task 5 плана acceptance-node)."""
    req_text = (bundle_dir / "10-requirements.md").read_text(encoding="utf-8")
    req_pin = blob_sha1(req_text)
    beh_text = (bundle_dir / "15-behaviour-spec.md").read_text(encoding="utf-8")
    beh_pin = blob_sha1(beh_text)
    (bundle_dir / "20-design.md").write_text(
        "---\n"
        "spec_stage: design\n"
        "status: draft\n"
        "owner_role: architects\n"
        "traces_to: [requirements, behaviour-spec]\n"
        "upstream_hashes:\n"
        f'  requirements: "{req_pin}"\n'
        f'  behaviour-spec: "{beh_pin}"\n'
        "---\n"
        "Открытых архитектурных вопросов нет (входной набор пуст)\n",
        encoding="utf-8",
    )
    design_pin = blob_sha1(
        (bundle_dir / "20-design.md").read_text(encoding="utf-8")
    )
    (bundle_dir / "25-acceptance.md").write_text(
        _acceptance_body(req_text, req_pin, beh_pin), encoding="utf-8",
    )
    acceptance_pin = blob_sha1(
        (bundle_dir / "25-acceptance.md").read_text(encoding="utf-8")
    )
    beh_ids = re.findall(r"^####\s+(BEH-\d+):", beh_text, re.M)
    (bundle_dir / "30-decomposition.md").write_text(
        "---\n"
        "spec_stage: decomposition\n"
        "dt_contract_version: 2\n"
        "status: draft\n"
        "owner_role: tech-lead\n"
        "traces_to: [design, acceptance]\n"
        "upstream_hashes:\n"
        f'  design: "{design_pin}"\n'
        f'  acceptance: "{acceptance_pin}"\n'
        "---\n"
        "## Задачи\n\n"
        "#### DT-01: x · type: implement · owner: dev\n"
        f"scenarios: [{', '.join(beh_ids)}]\n"
        "depends_on: []\n"
        "parallel_group: solo\n"
        "delivers: []\n"
        "Проза предмета.\n\n"
        "## Инварианты графа\n\nСоблюдены.\n\n"
        "## Порядок и параллельность\n\n"
        "DT-01 — единственная задача, зависимостей нет.\n\n"
        "## Вне объёма\n\nНичего не исключено.\n",
        encoding="utf-8",
    )


def test_happy_path_agent_merge(tmp_path: Path, runs_root, monkeypatch) -> None:
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)

    state = runner.start(**_start_kwargs(tmp_path, "r-happy", ops))

    assert state.ops["merge"]["status"] == "completed"
    assert ops.merged == [(state.pr, ops.head)]
    # Мерж адресован каталогом репо, а не слагом: S7 ходит через
    # `merge-pr.sh` — единственный путь агентского мержа, — и обвязка
    # выводит slug из сырого origin этого чекаута сама.
    assert ops.merge_targets == [state.repo]
    assert "/" not in ops.merge_targets[0]


def test_today_reality_agent_merges(tmp_path: Path, runs_root, monkeypatch) -> None:
    """Без monkeypatch safety: вендоренная копия @ steward 6a70d15 —
    allowed=True, ai-prosto=agent → зелёный document-PR мержится агентом."""
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=0,
    )

    state = runner.start(**_start_kwargs(tmp_path, "r-human", ops))

    assert state.ops["merge"]["status"] == "completed"
    assert ops.merged == [(state.pr, ops.head)]
    assert state.status == "completed"


def test_merge_authority_human_still_waits(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Run-override merge_authority=human обгоняет разрешающую safety."""
    ops = FakeOps(review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)

    state = runner.start(
        **_start_kwargs(tmp_path, "r-human-override", ops, merge_authority="human")
    )

    assert "merge" not in state.ops
    assert ops.merged == []
    assert ops.comments
    assert state.status == "waiting_human_merge"


def test_review_request_changes_stops(tmp_path: Path, runs_root, monkeypatch) -> None:
    ops = FakeOps(review_exit=1)

    state = runner.start(**_start_kwargs(tmp_path, "r-review", ops))

    assert state.status == "stopped_review"
    assert ops.merged == []
    assert "verdict" not in state.ops


def test_resume_does_not_duplicate_pr(tmp_path: Path, runs_root) -> None:
    ops = FakeOps()
    kwargs = _start_kwargs(tmp_path, "r-resume", ops)
    branch = "spec/WS-1-behaviour"
    state = rs.new_run(
        subject=kwargs["subject"], repo=kwargs["repo"],
        repo_slug=kwargs["repo_slug"], ws_id=kwargs["ws_id"],
        target_dir=kwargs["target_dir"], bundle_dir=kwargs["bundle_dir"],
        profile=kwargs["profile"], run_id=kwargs["run_id"],
    )
    state.branch = branch
    state.ops = {
        "branch": {"status": "completed"},
        "author-charter": {"status": "completed", "skipped": True},
        "author-requirements": {"status": "completed", "skipped": True},
        "author-behaviour": {"status": "completed", "skipped": True},
        "gate-candidate": {
            "status": "completed", "exit": 0,
        },
        "push": {"status": "completed"},
        "pr": {"status": "started"},
    }
    rs.save(state)
    # Симулируем: PR реально создан до "гибели" прогона.
    ops.existing_prs[branch] = 42

    result = runner.advance(state, ops)

    assert "create_draft_pr" not in [c[0] for c in ops.calls]
    assert result.pr == 42
    assert result.ops["pr"] == {"status": "completed", "number": 42}


def test_gate_red_stops(tmp_path: Path, runs_root, monkeypatch) -> None:
    ops = FakeOps(gate_candidate=[(1, "error GC-X: bad\n")])

    state = runner.start(**_start_kwargs(tmp_path, "r-gate", ops))

    assert state.status == "stopped_gate"
    assert "pr" not in state.ops
    findings_file = rs.run_dir("r-gate") / "gate-findings.txt"
    assert findings_file.exists()
    assert "GC-X" in findings_file.read_text(encoding="utf-8")


def test_author_skips_existing_files(tmp_path: Path, runs_root, monkeypatch) -> None:
    ops = FakeOps(review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)
    kwargs = _start_kwargs(tmp_path, "r-skip", ops)
    bundle_dir = Path(kwargs["target_dir"]) / kwargs["bundle_dir"]
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "00-charter.md").write_text("# charter\n", encoding="utf-8")
    (bundle_dir / "10-requirements.md").write_text(
        "# requirements\n", encoding="utf-8"
    )
    (bundle_dir / "15-behaviour-spec.md").write_text(
        "# behaviour\n", encoding="utf-8"
    )
    (bundle_dir / "20-design.md").write_text("# design\n", encoding="utf-8")
    (bundle_dir / "25-acceptance.md").write_text(
        "# acceptance\n", encoding="utf-8"
    )
    (bundle_dir / "30-decomposition.md").write_text(
        "# decomposition\n", encoding="utf-8"
    )

    state = runner.start(**kwargs)

    assert ops.authored == []
    assert state.ops["author-charter"]["skipped"] is True
    assert state.ops["author-requirements"]["skipped"] is True
    assert state.ops["author-behaviour"]["skipped"] is True
    assert state.ops["author-design"]["skipped"] is True
    assert state.ops["author-acceptance"]["skipped"] is True
    assert state.ops["author-decomposition"]["skipped"] is True


def test_facts_from_fail_closed() -> None:
    empty_rollup = runner.facts_from(
        {"statusCheckRollup": [], "mergeable": "UNKNOWN", "mergeStateStatus": "CLEAN"},
        [], None, BUNDLE_DIR,
    )
    assert empty_rollup.checks_rollup == "empty"
    assert empty_rollup.unresolved_threads is True
    # Круг 11 (codex-major): пустой files — не "все файлы про документацию"
    # (`all()` вакуумно истинно на пустом) — fail-closed на "code".
    assert empty_rollup.diff_class == "code"

    outside_bundle = runner.facts_from(
        {
            "statusCheckRollup": [{"conclusion": "SUCCESS"}],
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
        },
        ["src/main.py"], False, BUNDLE_DIR,
    )
    assert outside_bundle.diff_class == "code"


def _agent_merge_kwargs(tmp_path: Path, run_id: str, ops: FakeOps, **overrides):
    return _start_kwargs(tmp_path, run_id, ops, **overrides)


def test_s8_success_completes(tmp_path: Path, runs_root, monkeypatch) -> None:
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=0,
    )

    state = runner.start(**_agent_merge_kwargs(tmp_path, "r-s8-ok", ops))

    assert state.status == "completed"
    assert state.ops["gate-authoritative"] == {"status": "completed", "exit": 0}
    assert ops.issues == []
    # Ретроспектива 2026-09-02 (@id:runner-s8-verdicts-cleanup): verdicts
    # --emit-verdicts не остаются в чекауте цели — уборка и на успехе;
    # pre-clean stale-файла предыдущей попытки — ДО запуска гейта
    # (приёмка PR #114, круг 2).
    stale = str(rs.run_dir("r-s8-ok") / "s8-gate-verdicts.stale.jsonl")
    dest = str(rs.run_dir("r-s8-ok") / "s8-gate-verdicts.jsonl")
    seq = [
        c for c in ops.calls
        if c[0] in ("collect_gate_verdicts", "gate_check_s8")
    ]
    assert seq == [
        ("collect_gate_verdicts", state.target_dir, stale),
        ("gate_check_s8", BUNDLE_DIR),
        ("collect_gate_verdicts", state.target_dir, dest),
    ]


def test_s8_stale_verdicts_do_not_mask_missing_artifact(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Приёмка PR #114, круг 2: verdict-файл прерванной ПРЕДЫДУЩЕЙ попытки
    не должен сойти за артефакт текущего вызова гейта. Pre-clean уносит
    его (True), текущий гейт файла не создал (False) — fail-closed стоп."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES,
        s8_exit=0, collect_verdicts_queue=[True, False],
    )

    state = runner.start(**_agent_merge_kwargs(tmp_path, "r-s8-stale", ops))

    assert state.status != "completed"
    gate_op = state.ops.get("gate-authoritative")
    assert gate_op is not None and gate_op["status"] != "completed"


def test_s8_success_without_verdicts_is_not_completed(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Приёмка PR #114: verdicts — обязательный артефакт authoritative-
    фиксации (спека §5). Зелёный exit gate-check без gate_verdicts.jsonl —
    неполный результат: fail-closed стоп ДО op_complete, шаг resumable,
    completed не выставляется."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES,
        s8_exit=0, collect_verdicts_ok=False,
    )

    state = runner.start(**_agent_merge_kwargs(tmp_path, "r-s8-noverd", ops))

    assert state.status != "completed"
    gate_op = state.ops.get("gate-authoritative")
    assert gate_op is not None and gate_op["status"] != "completed"
    assert ops.issues == []


def test_s8_fail_marks_merged_unverified_and_opens_issue(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    run_id = "r-s8-fail"

    state = runner.start(**_agent_merge_kwargs(tmp_path, run_id, ops))

    assert state.status == "merged_unverified"
    # Круг 3 (codex-ревью PR #88): gate-authoritative — аудит-запись, тоже
    # completed на провале (exit хранит исход); отличает «прошёл» от «нет»
    # exit, не сам статус op'а — run терминален в обоих случаях. `output`
    # (круг 8) — источник для s8-findings.txt на resume, файл производный.
    assert state.ops["gate-authoritative"] == {
        "status": "completed", "exit": 1, "output": "",
    }
    assert state.ops["remediation-issue"] == {"status": "completed", "number": 901}
    # Уборка verdicts и на провале — dirty-чекаут не должен пережить S8.
    dest = str(rs.run_dir(run_id) / "s8-gate-verdicts.jsonl")
    assert ("collect_gate_verdicts", state.target_dir, dest) in ops.calls

    findings_file = rs.run_dir(run_id) / "s8-findings.txt"
    assert findings_file.exists()
    assert "1" in findings_file.read_text(encoding="utf-8")

    assert len(ops.issues) == 1
    repo_slug, _title, body = ops.issues[0]
    assert repo_slug == "owner/alpha"
    # Round 4: slug — от cycle_id (`remediated_by or run_id`), не от
    # ws_id; для родителя (remediated_by ещё None) cycle_id == его run_id.
    assert body.startswith(f"slug: beh-remediation-{run_id}")
    assert f"from: devtools#{run_id}" in body

    with pytest.raises(ValueError):
        runner.advance(state, ops)
    with pytest.raises(ValueError):
        runner.resume(run_id, ops)


def test_resume_after_death_between_create_issue_and_op_complete_reuses_issue(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Круг 3 (codex-ревью PR #88): `create_issue` не имел собственного
    write-ahead op'а — гибель между вызовом `create_issue` (эффект
    состоялся) и фиксацией результата дублировала issue на resume. Op
    `remediation-issue` + `find_issue`-реконсиляция: issue уже существует
    (найден по `slug:`-префиксу тела) → берётся его номер, второй не
    создаётся."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    run_id = "r-s8-issue-died"
    kwargs = _agent_merge_kwargs(tmp_path, run_id, ops)

    # Состояние сразу после дохлого прогона: gate-authoritative уже
    # зафиксирован неуспехом, findings уже на диске, remediation-issue —
    # started (write-ahead отработал), а сам issue РЕАЛЬНО создан (эффект
    # состоялся), но op_complete не успел записаться.
    state = rs.new_run(
        subject=kwargs["subject"], repo=kwargs["repo"],
        repo_slug=kwargs["repo_slug"], ws_id=kwargs["ws_id"],
        target_dir=kwargs["target_dir"], bundle_dir=kwargs["bundle_dir"],
        profile=kwargs["profile"], run_id=run_id,
    )
    state.branch = "spec/WS-1-behaviour"
    state.pr = 100
    state.head = "deadbeef"
    state.ops = {
        "branch": {"status": "completed"},
        "author-charter": {"status": "completed", "skipped": True},
        "author-requirements": {"status": "completed", "skipped": True},
        "author-behaviour": {"status": "completed", "skipped": True},
        "commit": {"status": "completed"},
        "gate-candidate": {
            "status": "completed", "exit": 0,
        },
        "push": {"status": "completed"},
        "pr": {"status": "completed", "number": 100},
        "ready": {"status": "completed"},
        "review": {"status": "completed", "exit": 0},
        "verdict": {"status": "completed", "decision": "agent", "reason": "ok"},
        "merge": {"status": "completed", "merged": True},
        "gate-authoritative": {"status": "completed", "exit": 1},
        "remediation-issue": {"status": "started"},
    }
    run_dir_path = rs.run_dir(run_id)
    run_dir_path.mkdir(parents=True, exist_ok=True)
    # Round 4: slug — от cycle_id (`remediated_by or run_id`); у этого
    # состояния remediated_by ещё None (родитель), cycle_id == run_id.
    body_prefix = f"slug: beh-remediation-{run_id}"
    findings_text = "gate-check (S8, authoritative) завершился с кодом 1\n\nGC-X\n"
    (run_dir_path / "s8-findings.txt").write_text(findings_text, encoding="utf-8")
    rs.save(state)
    # Issue РЕАЛЬНО создан прошлым (дохлым) вызовом ops.create_issue.
    ops.issues.append((
        state.repo_slug,
        "beh-remediation: тестовый функционал (WS-1)",
        f"{body_prefix}\nfrom: devtools#{run_id}\n\n{findings_text}",
    ))

    result = runner.advance(state, ops)

    assert result.status == "merged_unverified"
    assert len(ops.issues) == 1  # второй не создан
    assert result.ops["remediation-issue"] == {"status": "completed", "number": 901}
    assert "create_issue" not in [c[0] for c in ops.calls]
    assert ("find_issue", body_prefix) in ops.calls


def test_s8_fail_does_not_reuse_issue_from_different_cycle(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Round 4, codex-major: slug строится от `cycle_id` (`remediated_by or
    run_id`), не от `ws_id`. Старый открытый issue ДРУГОГО цикла того же
    `ws_id` (например прошлый цикл, уже зелёно верифицированный и закрытый,
    или просто параллельный независимый прогон по тому же `ws_id`) НЕ
    должен реконсилироваться на текущий провал — иначе свежие findings
    молча терялись бы под чужим issue. `find_issue` всё равно вызывается
    (реконсиляция остаётся безусловной, round 3), но не находит совпадение
    по своему префиксу -> `create_issue` создаёт НОВЫЙ, отдельный issue."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    run_id = "r-s8-independent-failure"
    # Issue от ДРУГОГО цикла того же ws_id (другой parent run_id) уже
    # открыт — например прошлый цикл, уже верифицированный и закрытый.
    other_cycle_prefix = "slug: beh-remediation-r-earlier-cycle"
    ops.issues.append((
        "owner/alpha",
        "beh-remediation: прошлый цикл (WS-1)",
        f"{other_cycle_prefix}\nfrom: devtools#r-earlier-cycle\n\nGC-OLD\n",
    ))

    state = runner.start(**_agent_merge_kwargs(tmp_path, run_id, ops))

    own_prefix = f"slug: beh-remediation-{run_id}"
    assert state.status == "merged_unverified"
    assert len(ops.issues) == 2  # чужой issue не переиспользован — создан новый
    new_repo_slug, _title, new_body = ops.issues[-1]
    assert new_repo_slug == "owner/alpha"
    assert new_body.startswith(own_prefix)
    assert state.ops["remediation-issue"] == {"status": "completed", "number": 902}
    assert ("find_issue", own_prefix) in ops.calls
    assert "create_issue" in [c[0] for c in ops.calls]


def test_verify_child_reuses_parent_remediation_issue_same_cycle(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Round 4: `cycle_id = state.remediated_by or state.run_id` — у
    verify-потомка `remediated_by` указывает на родителя, поэтому
    `cycle_id` совпадает с собственным `run_id` родителя (у которого
    `remediated_by` ещё `None`). Потомок с ФРЕШ `remediation-issue`
    ("new", round 3: реконсиляция безусловна) должен найти и переиспользовать
    issue родителя, а не открыть второй под тем же циклом."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    parent_id = "r-s8-cycle-parent"
    parent = runner.start(**_agent_merge_kwargs(tmp_path, parent_id, ops))
    assert parent.status == "merged_unverified"
    assert len(ops.issues) == 1
    parent_issue_number = parent.ops["remediation-issue"]["number"]

    child = runner.verify(parent_id, ops, "r-s8-cycle-child")

    assert child.status == "merged_unverified"  # тоже проваливается
    assert child.remediated_by == parent_id
    assert len(ops.issues) == 1  # НЕ второй issue — тот же цикл
    assert child.ops["remediation-issue"] == {
        "status": "completed", "number": parent_issue_number,
    }


def test_verify_child_completes_parent_stays_merged_unverified(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    parent_id = "r-s8-parent"
    parent = runner.start(**_agent_merge_kwargs(tmp_path, parent_id, ops))
    assert parent.status == "merged_unverified"
    assert len(ops.issues) == 1  # родитель открыл ровно одно remediation-issue

    ops.s8_exit = 0  # находки устранены фикс-PR'ом в целевом репо
    child = runner.verify(parent_id, ops, "r-s8-child")

    assert child.status == "completed"
    assert child.remediated_by == parent_id
    assert child.ops["gate-authoritative"] == {"status": "completed", "exit": 0}
    assert "remediation-issue" not in child.ops  # gate прошёл — issue не нужен
    assert len(ops.issues) == 1  # потомок не плодит второй issue

    reloaded_parent = rs.load(parent_id)
    assert reloaded_parent.status == "merged_unverified"


def test_verify_refuses_when_parent_already_has_green_child(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Round 3, codex-major: tmux-дедуп в консоли защищает только пока
    сессия жива (и после round 2 она ещё и самозакрывается) — `verify()`
    сам по себе не проверял, есть ли у родителя уже подтверждающий
    (`completed`) потомок. Второй `verify()`-вызов (руками, мимо консоли,
    или после того как сессия уже закрылась) на уже зелёном потомке
    создавал бы ЕЩЁ ОДИН verification-run поверх уже верифицированного
    родителя."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    parent_id = "r-s8-parent-already-verified"
    parent = runner.start(**_agent_merge_kwargs(tmp_path, parent_id, ops))
    assert parent.status == "merged_unverified"

    ops.s8_exit = 0  # находки устранены фикс-PR'ом
    child = runner.verify(parent_id, ops, "r-s8-child-green")
    assert child.status == "completed"

    with pytest.raises(ValueError, match="уже верифицирован"):
        runner.verify(parent_id, ops, "r-s8-child-second")

    # Второй потомок не зарезервирован — отказ ДО _reserve_run_id.
    assert not (rs.run_dir("r-s8-child-second") / "run.json").exists()


def test_verify_allowed_again_after_failed_child(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Провальный (`merged_unverified`) потомок НЕ блокирует повторный
    `verify()` — только ЗЕЛЁНЫЙ (`completed`) значит «уже верифицирован»;
    цикл «verify → всё ещё красный → verify снова» остаётся штатным."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    parent_id = "r-s8-parent-retry"
    parent = runner.start(**_agent_merge_kwargs(tmp_path, parent_id, ops))
    assert parent.status == "merged_unverified"

    failed_child = runner.verify(parent_id, ops, "r-s8-child-failed")
    assert failed_child.status == "merged_unverified"  # тоже провалился

    ops.s8_exit = 0  # находки устранены вторым фикс-PR'ом
    second_child = runner.verify(parent_id, ops, "r-s8-child-retry-green")
    assert second_child.status == "completed"
    assert second_child.remediated_by == parent_id


def test_verify_without_run_id_serializes_when_ids_collide(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Round 5 (TOCTOU): сериализация конкурентных `verify()` без `run_id`
    держится на атомарном `_reserve_run_id` (`O_CREAT|O_EXCL`), а не на
    конкретном способе счёта `attempt` внутри `_next_verify_run_id` (тот
    менялся в round 6 — см. `test_next_verify_run_id_skips_dangling_
    reservation` ниже). Настоящую гонку потоков/процессов синхронный тест
    воспроизвести не может — форсируем через monkeypatch общий результат
    вычисления id для "обоих конкурентов" (то, что в реальной гонке дало
    бы им одно и то же значение из одного стартового снапшота каталогов).
    Первый вызов резервирует и создаёт потомка, второй с тем же
    вычисленным id получает `ValueError` вместо параллельного запуска S8
    в одном `target_dir`."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    parent_id = "r-s8-parent-race"
    parent = runner.start(**_agent_merge_kwargs(tmp_path, parent_id, ops))
    assert parent.status == "merged_unverified"

    monkeypatch.setattr(runner, "_next_verify_run_id", lambda pid: f"{pid}-v1")

    winner = runner.verify(parent_id, ops)
    assert winner.run_id == f"{parent_id}-v1"

    with pytest.raises(ValueError, match="уже существует"):
        runner.verify(parent_id, ops)  # "проигравший" вычисляет тот же id


def test_next_verify_run_id_skips_dangling_reservation(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Round 6, codex-major: гибель между `_reserve_run_id` и
    `save(child)` оставляет ПУСТОЙ `<parent>-v1/run.json` (сам
    `_reserve_run_id` уже создал файл через `touch`). Счёт `attempt` по
    успешно ЗАГРУЖЕННЫМ `RunState` (round 5) молча пропускал бы этот
    каталог — `_next_verify_run_id` вечно вычислял бы `v1` снова, а
    `_reserve_run_id` вечно отвечал бы «уже существует» на уже занятом
    (хоть и оборванном) слоте — постоянный deadlock на этом родителе.
    Счёт по ИМЕНАМ каталогов (`all_run_ids()`) видит `v1` независимо от
    валидности JSON внутри и корректно берёт следующий номер.

    Файл состарен (`os.utime`, round 7): свежий пустой `run.json` теперь
    трактуется `_active_verify_child` как «только что зарезервировано
    конкурентом» и блокирует `verify()` (см. `test_verify_refuses_when_
    dangling_reservation_is_fresh`) — этот тест про труп round 6, который
    старше грейс-периода, поэтому его нужно состарить явно."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    parent_id = "r-s8-parent-dangling"
    parent = runner.start(**_agent_merge_kwargs(tmp_path, parent_id, ops))
    assert parent.status == "merged_unverified"

    # Оборванная резервация: процесс умер между _reserve_run_id и
    # save(child) — каталог и пустой run.json есть, RunState — нет.
    # Состарена на -300с (> _ACTIVE_VERIFY_GRACE_SECONDS=120) — труп, не
    # свежий конкурент.
    dangling_id = f"{parent_id}-v1"
    rs.run_dir(dangling_id).mkdir(parents=True, exist_ok=True)
    dangling_json = rs.run_dir(dangling_id) / "run.json"
    dangling_json.touch()
    old_time = time.time() - 300
    os.utime(dangling_json, (old_time, old_time))

    assert runner._next_verify_run_id(parent_id) == f"{parent_id}-v2"
    assert runner._active_verify_child(parent_id) is None

    ops.s8_exit = 0  # находки устранены фикс-PR'ом
    child = runner.verify(parent_id, ops)
    assert child.run_id == f"{parent_id}-v2"
    assert child.status == "completed"


def test_verify_refuses_when_child_is_running(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Round 7, codex-major: round 6 переоткрыл гонку round 5 (второй
    конкурентный `verify()` видел уже занятый каталог и спокойно
    резервировал следующий номер вместо коллизии). Сериализация теперь
    держится на СОСТОЯНИИ потомков: валидный `run.json` со `status` не в
    `{"completed", "merged_unverified"}` (например `"running"` — S8 ещё
    не отработал) — активный потомок, второй `verify()` отказывает."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    parent_id = "r-s8-parent-active-child"
    parent = runner.start(**_agent_merge_kwargs(tmp_path, parent_id, ops))
    assert parent.status == "merged_unverified"

    # Валидный, но ещё не терминальный потомок (S8 в процессе).
    child_id = f"{parent_id}-v1"
    child_state = rs.new_run(
        subject=parent.subject, repo=parent.repo, repo_slug=parent.repo_slug,
        ws_id=parent.ws_id, target_dir=parent.target_dir,
        bundle_dir=parent.bundle_dir, profile=parent.profile, run_id=child_id,
    )
    child_state.remediated_by = parent_id
    child_state.status = "running"
    rs.save(child_state)

    assert runner._active_verify_child(parent_id) == child_id
    with pytest.raises(ValueError, match="verify уже идёт"):
        runner.verify(parent_id, ops)


def test_verify_refuses_when_dangling_reservation_is_fresh(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Round 7: свежий (только что созданный) пустой `<parent>-v1/run.json`
    трактуется как «конкурент только что зарезервировал слот, ещё пишет
    свой RunState» — активный, а не труп round 6. mtime моложе
    `_ACTIVE_VERIFY_GRACE_SECONDS` (тест не состаривает файл, в отличие от
    `test_next_verify_run_id_skips_dangling_reservation`)."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    parent_id = "r-s8-parent-fresh-dangling"
    parent = runner.start(**_agent_merge_kwargs(tmp_path, parent_id, ops))
    assert parent.status == "merged_unverified"

    dangling_id = f"{parent_id}-v1"
    rs.run_dir(dangling_id).mkdir(parents=True, exist_ok=True)
    (rs.run_dir(dangling_id) / "run.json").touch()  # свежий -> "прямо сейчас"

    assert runner._active_verify_child(parent_id) == dangling_id
    with pytest.raises(ValueError, match="verify уже идёт"):
        runner.verify(parent_id, ops)


def test_active_verify_child_ignores_merged_unverified_child(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Провальный (`merged_unverified`) потомок — терминальный статус, НЕ
    активный: не блокирует повторный `verify()` (round 3/round 7 согласны
    друг с другом — только `_has_green_child` реагирует на `completed`,
    `_active_verify_child` реагирует на нетерминальные статусы)."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    parent_id = "r-s8-parent-failed-not-active"
    parent = runner.start(**_agent_merge_kwargs(tmp_path, parent_id, ops))
    assert parent.status == "merged_unverified"

    failed_child = runner.verify(parent_id, ops)
    assert failed_child.status == "merged_unverified"  # тоже провалился

    assert runner._active_verify_child(parent_id) is None

    ops.s8_exit = 0  # находки устранены вторым фикс-PR'ом
    second_child = runner.verify(parent_id, ops)
    assert second_child.status == "completed"


def test_verify_without_run_id_increments_attempt_after_failed_child(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """`attempt = 1 + число существующих потомков` (любой статус) — после
    провального (`merged_unverified`) потомка следующий `verify()` без
    `run_id` вычисляет НОВЫЙ id (`-v2`), а не повторяет `-v1` (что упёрлось
    бы в уже занятый `run_id` того же провального потомка)."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    parent_id = "r-s8-parent-attempts"
    parent = runner.start(**_agent_merge_kwargs(tmp_path, parent_id, ops))
    assert parent.status == "merged_unverified"

    first_child = runner.verify(parent_id, ops)
    assert first_child.run_id == f"{parent_id}-v1"
    assert first_child.status == "merged_unverified"

    ops.s8_exit = 0  # находки устранены вторым фикс-PR'ом
    second_child = runner.verify(parent_id, ops)
    assert second_child.run_id == f"{parent_id}-v2"
    assert second_child.status == "completed"


def test_resume_waiting_human_merge_open_still_waits(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """merge_authority=human (safety с пина 6a70d15 разрешает агентский
    мерж, ждущее состояние достигается run-override-ом)."""
    ops = FakeOps(review_exit=0, facts=dict(GREEN_PR_FACTS), files=GREEN_BUNDLE_FILES)
    run_id = "r-resume-open"

    state = runner.start(
        **_start_kwargs(tmp_path, run_id, ops, merge_authority="human")
    )
    assert state.status == "waiting_human_merge"

    result = runner.resume(run_id, ops)

    assert result.status == "waiting_human_merge"
    assert "gate_check_s8" not in [c[0] for c in ops.calls]


def test_resume_waiting_human_merge_merged_runs_s8(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    ops = FakeOps(
        review_exit=0, facts=dict(GREEN_PR_FACTS), files=GREEN_BUNDLE_FILES, s8_exit=0,
    )
    run_id = "r-resume-merged"

    state = runner.start(
        **_start_kwargs(tmp_path, run_id, ops, merge_authority="human")
    )
    assert state.status == "waiting_human_merge"
    assert "merge" not in state.ops

    ops.facts = {**ops.facts, "state": "MERGED"}
    result = runner.resume(run_id, ops)

    assert result.ops["merge"] == {"status": "completed", "merged": True}
    assert result.status == "completed"
    assert result.ops["gate-authoritative"]["status"] == "completed"


def test_cli_status_prints_run_state(
    tmp_path: Path, runs_root, capsys: pytest.CaptureFixture[str],
) -> None:
    """`python -m governance.runner status --run-id …` — argparse-проводка жива.

    Только `load()` из диска (fixture `runs_root` → `rs.RUNS_ROOT`), без Ops и
    без внешних вызовов — `status` не строит `RealOps`.
    """
    run_id = "r-cli-status"
    state = rs.new_run(
        subject="тестовый функционал", repo="alpha", repo_slug="owner/alpha",
        ws_id="WS-1", target_dir=str(tmp_path / "target-cli"),
        bundle_dir=BUNDLE_DIR, profile="profiles/team-exp.yaml", run_id=run_id,
    )
    state.branch = "spec/WS-1-behaviour"
    state.pr = 7
    state.ops = {"branch": {"status": "completed"}}
    rs.save(state)

    exit_code = runner.main(["status", "--run-id", run_id])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert f"run_id:        {run_id}" in out
    assert "status:        running" in out
    assert "pr:            7" in out
    assert "branch: completed" in out


def test_cli_start_rejects_traversal_ws_id_before_default_run_id(
    tmp_path: Path, runs_root,
) -> None:
    """Круг 12 (codex-major): CLI `start` без `--run-id` строит дефолтный
    `run_id` из `--ws-id` — грязный `ws_id` (`../`) обязан отказать ДО
    генерации/резервирования, а не протащить traversal в автосгенерированный
    `run_id`."""
    with pytest.raises(ValueError):
        runner.main([
            "start",
            "--subject", "s",
            "--repo", "alpha",
            "--repo-slug", "owner/alpha",
            "--ws-id", "../../escape",
            "--target-dir", str(tmp_path),
        ])
    assert not runs_root.exists()  # ничего не зарезервировано/создано


def test_start_rejects_explicit_traversal_run_id(tmp_path: Path, runs_root) -> None:
    """Круг 12: `start()` с явным `--run-id` вне разрешённого алфавита —
    отказ через `run_dir()` (единая точка валидации, `_reserve_run_id`)."""
    target_dir = tmp_path / "target-traversal"
    target_dir.mkdir()
    kwargs = dict(
        subject="s", repo="alpha", repo_slug="owner/alpha", ws_id="WS-1",
        target_dir=str(target_dir), bundle_dir=BUNDLE_DIR,
        profile="profiles/team-exp.yaml", run_id="../../outside",
        ops=FakeOps(),
    )
    with pytest.raises(ValueError):
        runner.start(**kwargs)


# --- F-1/M-1: resume из stopped_* — reconciliation, не no-op ---------------


def test_resume_from_stopped_gate_reruns_gate_candidate(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """F-1: resume из stopped_gate переигрывает S4, не остаётся no-op'ом."""
    ops = FakeOps(gate_candidate=[(1, "error GC-X: bad\n"), (0, "")])
    run_id = "r-resume-gate"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))
    assert state.status == "stopped_gate"

    result = runner.resume(run_id, ops)

    gate_calls = [c for c in ops.calls if c[0] == "gate_check_candidate"]
    assert len(gate_calls) == 2  # S4 реально переигран, не пропущен
    assert result.status != "stopped_gate"
    assert result.ops["gate-candidate"]["status"] == "completed"


def test_green_gate_removes_findings_of_the_previous_round(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """`gate-findings.txt` отражает ПОСЛЕДНИЙ прогон гейта
    (@id:gate-findings-stale-on-green).

    Прогон S7 2026-09-21: круг 1 записал пять находок, круг 2 прошёл чисто,
    файл остался прежним — читающий видел красный там, где гейт зелёный.
    Класс «артефакт переживает состояние, которое описывал».
    """
    ops = FakeOps(gate_candidate=[(1, "error GC-X: bad\n"), (0, "")])
    run_id = "r-gate-stale"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))
    assert state.status == "stopped_gate"
    findings_file = rs.run_dir(run_id) / "gate-findings.txt"
    assert "GC-X" in findings_file.read_text(encoding="utf-8")

    result = runner.resume(run_id, ops)

    assert result.ops["gate-candidate"]["status"] == "completed"
    assert not findings_file.exists(), (
        "зелёный гейт оставил находки прошлого круга: "
        + findings_file.read_text(encoding="utf-8")
    )


def test_resume_from_stopped_gate_recommits_edited_bundle(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Круг 9 (codex-ревью PR #88): resume из `stopped_gate` сбрасывает не
    только `gate-candidate`, но и `commit`+`push`+`ready`+`review` — человек
    мог поправить бандл в worktree между стопом и resume, и старый `commit`
    (уже `completed` с первого прохода — конвейер коммитит ДО гейта) не
    должен уехать в PR со СТАРЫМ, докоррекционным деревом."""
    ops = FakeOps(
        gate_candidate=[(1, "error GC-X: bad\n"), (0, "")],
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES,
    )
    run_id = "r-resume-gate-recommit"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))
    assert state.status == "stopped_gate"
    # commit шёл ДО гейта в конвейере — на первом проходе он уже completed
    # со СТАРЫМ (красным по гейту) содержимым.
    assert state.ops["commit"]["status"] == "completed"
    calls_before_resume = len(ops.calls)

    # Человек правит бандл в worktree, устраняя находку гейта
    # (DSL-корректно — иначе стоп повторит гард GC-DSL-EMPTY).
    bundle_dir = Path(state.target_dir) / state.bundle_dir
    (bundle_dir / "15-behaviour-spec.md").write_text(
        "#### BEH-01: fixed\n`traces: [FR-01]`\n- **checked_by**: x\n",
        encoding="utf-8",
    )
    _repin_bundle(bundle_dir)

    result = runner.resume(run_id, ops)

    new_calls = [c[0] for c in ops.calls[calls_before_resume:]]
    assert "commit_paths" in new_calls  # новый коммит, не пропущен по кэшу
    assert "push_branch" in new_calls
    assert new_calls.index("commit_paths") < new_calls.index("push_branch")
    assert new_calls.count("gate_check_candidate") == 1  # гейт переигран
    assert result.status != "stopped_gate"
    assert result.ops["gate-candidate"]["status"] == "completed"
    committed_paths = [paths for _t, paths, _m in ops.committed]
    assert committed_paths  # commit_paths реально вызван с путями бандла


def test_resume_from_stopped_review_reruns_ready_and_review(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """F-1: resume из stopped_review переигрывает ready+review."""
    ops = FakeOps(review_exit=1)
    run_id = "r-resume-review"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))
    assert state.status == "stopped_review"
    calls_before = len(ops.calls)

    ops.review_exit = 0
    result = runner.resume(run_id, ops)

    review_calls_after = [c for c in ops.calls[calls_before:] if c[0] == "review"]
    assert review_calls_after  # review реально перезапустился
    assert result.status != "stopped_review"
    assert result.ops["review"]["status"] == "completed"


def test_resume_from_stopped_review_pr_merged_out_of_band_runs_s8(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Живой прогон spec-runner#480/#522: PR смержен вручную из
    `stopped_review`, в обход S7 — GitHub удалил ветку (`--delete-branch`).
    Слепой сброс `commit`→`review` до этой правки пытался бы `push`
    несуществующую ветку; реконсиляция обязана заметить `MERGED` ПЕРЕД
    сбросом и пойти прямо на S8, не трогая commit/push/review."""
    ops = FakeOps(review_exit=1, facts=dict(GREEN_PR_FACTS), s8_exit=0)
    run_id = "r-resume-review-merged"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))
    assert state.status == "stopped_review"
    calls_before = len(ops.calls)

    ops.facts = {**ops.facts, "state": "MERGED"}
    result = runner.resume(run_id, ops)

    calls_after = [c[0] for c in ops.calls[calls_before:]]
    assert "push_branch" not in calls_after
    assert "review" not in calls_after
    assert "commit_paths" not in calls_after
    assert result.ops["merge"] == {"status": "completed", "merged": True}
    assert result.status == "completed"
    assert result.ops["gate-authoritative"]["status"] == "completed"


def test_resume_from_stopped_review_pr_merged_records_base_ref_from_facts(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Ревью #253: `_step_verdict` (единственная другая точка записи
    `state.base_ref`) не выполнялся на пути `stopped_review` — реконсиляция
    обязана взять `baseRefName` из тех же фактов PR, что уже прочитала,
    иначе S8 молча гейтит захардкоженный фолбэк "master" на репо с другой
    дефолтной веткой."""
    ops = FakeOps(
        review_exit=1, facts={**GREEN_PR_FACTS, "baseRefName": "main"}, s8_exit=0,
    )
    run_id = "r-resume-review-merged-baseref"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))
    assert state.status == "stopped_review"
    assert state.base_ref is None

    ops.facts = {**ops.facts, "state": "MERGED"}
    result = runner.resume(run_id, ops)

    assert result.base_ref == "main"
    assert ("checkout_and_pull", "main") in ops.calls
    assert result.status == "completed"


def test_resume_from_stopped_author_pr_merged_out_of_band_runs_s8(
    tmp_path: Path, runs_root,
) -> None:
    """Ревью #253: `stopped_author` НЕ гарантирует отсутствие PR — brief-
    coverage внутри `_step_authoring` (E1) выполняется на каждом заходе, до
    проверки завершённости узлов, и может остановить run этим статусом уже
    после того, как PR создан (resume из stopped_review/stopped_gate
    доходит сюда повторно). Реконсиляция обязана сработать и здесь, не
    только `_reset_stopped_author`."""
    run_id = "r-resume-author-merged"
    state = rs.new_run(
        subject="s", repo="alpha", repo_slug="owner/alpha", ws_id="WS-1",
        target_dir=str(tmp_path / "target"), bundle_dir=BUNDLE_DIR,
        profile="profiles/team-exp.yaml", run_id=run_id,
    )
    state.branch = "spec/WS-1-behaviour"
    state.pr = 9
    state.ops = {
        "branch": {"status": "completed"},
        "author-charter": {"status": "completed", "exit": 0, "skipped": False},
    }
    state.status = "stopped_author"
    rs.save(state)

    ops = FakeOps(facts={**GREEN_PR_FACTS, "state": "MERGED"}, s8_exit=0)
    result = runner.resume(run_id, ops)

    assert ("author", "charter") not in ops.calls
    assert not any(c[0] == "push_branch" for c in ops.calls)
    assert result.ops["merge"] == {"status": "completed", "merged": True}
    assert result.status == "completed"


def test_resume_after_merged_reconciliation_with_nonterminal_s8_does_not_replay_review(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Ревью #253, круг 2: `_step_s8` может отказать нетерминально
    (`checkout_and_pull` не удался) и не меняет `state.status` — при
    реконсиляции из `stopped_review` это оставляло бы `review` op
    `started`, и следующий resume() падал бы в общий шаговый цикл,
    переигрывая платный `review` на уже смерженном PR. Общая проверка
    ``merge completed`` в начале `advance()` обязана перехватить это
    раньше, чем цикл дойдёт до `_step_review`."""
    ops = FakeOps(
        review_exit=1, facts=dict(GREEN_PR_FACTS),
        checkout_and_pull_error="ff-only diverged",
    )
    run_id = "r-resume-review-merged-s8-nonterminal"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))
    assert state.status == "stopped_review"
    assert state.ops["review"]["status"] == "started"

    ops.facts = {**ops.facts, "state": "MERGED"}
    result = runner.resume(run_id, ops)

    assert result.status == "running"
    assert result.ops["merge"] == {"status": "completed", "merged": True}
    assert "gate_check_s8" not in [c[0] for c in ops.calls]
    calls_before = len(ops.calls)

    # Второй resume — тот же нетерминальный отказ, но review НЕ должен
    # переиграться: ни разу за оба захода.
    result = runner.resume(run_id, ops)

    calls_after = [c[0] for c in ops.calls[calls_before:]]
    assert "review" not in calls_after
    assert "push_branch" not in calls_after
    assert result.status == "running"
    review_calls_total = sum(1 for c in ops.calls if c[0] == "review")

    # Убрать отказ — S8 доходит до конца, review за весь путь звался
    # ровно один раз (изначальный прогон, давший stopped_review).
    ops.checkout_and_pull_error = None
    result = runner.resume(run_id, ops)
    assert result.status == "completed"
    assert sum(1 for c in ops.calls if c[0] == "review") == review_calls_total


def test_resume_from_stopped_review_pr_merged_dirty_tree_stops_before_s8(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Ревью #253, круг 3: `stopped_review` разрешает оператору держать
    незакоммиченные правки бандла в `target_dir` между стопом и resume
    (`_BUNDLE_EDIT_RESET_OPS`). Если PR тем временем смержен вручную,
    переход на S8 без гарда чекаутил бы грязное дерево на base_ref — тот
    же гард, что `deliver_for_run` ставит перед своим `checkout_and_pull`
    (task_bridge.py, ревью #191 круг 2). `merge` op не фиксируется, пока
    дерево грязное: следующий resume обязан зайти в ту же проверку, не
    в короткое замыкание `advance()` по `merge completed`."""
    # dirty=False на start(): S1 (`_step_branch`) проверяет is_dirty только
    # на первом заходе (op "branch" ещё "new") — грязным дерево становится
    # ПОСЛЕ, во время правки бандла между стопом и resume.
    ops = FakeOps(review_exit=1, facts=dict(GREEN_PR_FACTS))
    run_id = "r-resume-review-merged-dirty"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))
    assert state.status == "stopped_review"

    ops.facts = {**ops.facts, "state": "MERGED"}
    ops.dirty = True
    result = runner.resume(run_id, ops)

    assert result.status == "stopped_review"
    assert "merge" not in result.ops
    assert "gate_check_s8" not in [c[0] for c in ops.calls]
    assert "незакоммиченные правки" in ops.comments[-1]

    # Дерево очищено — тот же resume теперь реконсилирует до конца.
    ops.dirty = False
    result = runner.resume(run_id, ops)
    assert result.ops["merge"] == {"status": "completed", "merged": True}
    assert result.status == "completed"


def test_resume_from_stopped_review_pr_still_open_resets_as_before(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Регрессия: PR ``OPEN`` (обычный случай) — реконсиляция не должна
    менять существовавшее поведение F-1 (сброс commit→review, повтор
    review)."""
    ops = FakeOps(review_exit=1, facts=dict(GREEN_PR_FACTS))
    run_id = "r-resume-review-still-open"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))
    assert state.status == "stopped_review"
    calls_before = len(ops.calls)

    ops.review_exit = 0
    result = runner.resume(run_id, ops)

    review_calls_after = [c for c in ops.calls[calls_before:] if c[0] == "review"]
    assert review_calls_after
    assert "merge" not in result.ops
    assert result.status != "stopped_review"
    assert result.ops["review"]["status"] == "completed"


def test_resume_from_stopped_gate_pr_merged_out_of_band_runs_s8(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Тот же класс реконсиляции для `stopped_gate`. S4 идёт ДО `pr` в
    конвейере, так что на первом заходе PR ещё нет — сценарий строится в два
    шага, как в бою: сперва обычный `stopped_review` (PR уже создан), затем
    resume с гейтом, вновь красным на пересбросе `_BUNDLE_EDIT_RESET_OPS`
    (`gate-candidate` в нём сбрасывается вместе с `review`) — это и есть
    `stopped_gate` с уже существующим PR."""
    ops = FakeOps(review_exit=1, facts=dict(GREEN_PR_FACTS))
    run_id = "r-resume-gate-merged"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))
    assert state.status == "stopped_review"
    assert state.pr is not None

    ops.gate_candidate = [(1, "снова красный")]
    state = runner.resume(run_id, ops)
    assert state.status == "stopped_gate"
    calls_before = len(ops.calls)

    ops.facts = {**ops.facts, "state": "MERGED"}
    ops.s8_exit = 0
    result = runner.resume(run_id, ops)

    calls_after = [c[0] for c in ops.calls[calls_before:]]
    assert "push_branch" not in calls_after
    assert "gate_check_candidate" not in calls_after
    assert result.ops["merge"] == {"status": "completed", "merged": True}
    assert result.status == "completed"


def test_resume_from_stopped_review_recommits_edited_bundle(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Круг 9 (codex-ревью PR #88): resume из `stopped_review` тоже
    сбрасывает `commit`+`gate-candidate`+`push`, не только `ready`+`review`
    — человек мог отработать находки ревью правкой бандла; старый коммит
    (уже `completed` с первого прохода) не должен уехать дальше со старым
    деревом."""
    ops = FakeOps(review_exit=1)
    run_id = "r-resume-review-recommit"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))
    assert state.status == "stopped_review"
    assert state.ops["commit"]["status"] == "completed"
    gate_first = [c for c in ops.calls if c[0] == "gate_check_candidate"]
    assert len(gate_first) == 1  # гейт уже прогнан на первом проходе
    calls_before_resume = len(ops.calls)

    # Человек правит бандл в worktree, отрабатывая находки ревью.
    bundle_dir = Path(state.target_dir) / state.bundle_dir
    (bundle_dir / "15-behaviour-spec.md").write_text(
        "#### BEH-01: review fix\n`traces: [FR-01]`\n- **checked_by**: x\n",
        encoding="utf-8",
    )
    _repin_bundle(bundle_dir)
    ops.review_exit = 0
    result = runner.resume(run_id, ops)

    new_calls = [c[0] for c in ops.calls[calls_before_resume:]]
    assert "commit_paths" in new_calls  # новый коммит, не пропущен по кэшу
    assert "push_branch" in new_calls
    assert new_calls.index("commit_paths") < new_calls.index("push_branch")
    assert new_calls.count("gate_check_candidate") == 1  # переигран, не кэш
    assert result.status != "stopped_review"
    assert result.ops["review"]["status"] == "completed"
    assert result.ops["gate-candidate"]["status"] == "completed"


def test_resume_from_stopped_author_reruns_unfinished_author(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """F-1: resume из stopped_author переигрывает незавершённый author-*."""
    ops = FakeOps(review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)
    original_author = ops.author
    fail_once = {"on": True}

    def flaky_author(target_dir, kind, subject, bundle_dir):
        if kind == "requirements" and fail_once["on"]:
            fail_once["on"] = False
            ops.calls.append(("author", kind))
            return 1
        return original_author(target_dir, kind, subject, bundle_dir)

    ops.author = flaky_author  # type: ignore[method-assign]
    run_id = "r-resume-author"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))
    assert state.status == "stopped_author"
    assert state.ops["author-charter"]["status"] == "completed"
    assert state.ops["author-requirements"]["status"] == "started"
    assert "author-behaviour" not in state.ops

    result = runner.resume(run_id, ops)

    assert result.ops["author-requirements"]["status"] == "completed"
    assert result.ops["author-behaviour"]["status"] == "completed"
    assert result.status != "stopped_author"


def test_verdict_refuse_status_is_distinct_from_stopped_gate(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """M-1: S7 `refuse` получает свой статус, не путается с S4 stopped_gate —
    у них разные причины и разная починка."""
    ops = FakeOps(
        review_exit=0,
        facts={**GREEN_PR_FACTS, "statusCheckRollup": [{"conclusion": "FAILURE"}]},
        files=GREEN_BUNDLE_FILES,
    )

    state = runner.start(**_start_kwargs(tmp_path, "r-refuse", ops))

    assert state.status == "stopped_merge_refused"
    assert state.status != "stopped_gate"


def test_resume_from_stopped_merge_refused_reverdicts(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """F-1: resume из stopped_merge_refused пересверяет вердикт, не no-op."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0,
        facts={**GREEN_PR_FACTS, "statusCheckRollup": [{"conclusion": "FAILURE"}]},
        files=GREEN_BUNDLE_FILES,
    )
    run_id = "r-resume-refused"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))
    assert state.status == "stopped_merge_refused"

    ops.facts = dict(GREEN_PR_FACTS)  # rollup зазеленел
    result = runner.resume(run_id, ops)

    assert result.status != "stopped_merge_refused"
    assert result.ops["merge"]["status"] == "completed"
    assert ops.merged == [(result.pr, ops.head)]


# --- F-2: verdict — аудит, не кэш решения -----------------------------------


def test_stale_cached_agent_verdict_does_not_merge_on_fresh_red_facts(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """F-2: op verdict уже completed=agent в run.json (write-ahead между
    _step_verdict и _step_merge), но свежий опрос PR даёт красный rollup —
    merge НЕ вызывается, decide() пересчитывается заново на этом заходе."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0,
        facts={**GREEN_PR_FACTS, "statusCheckRollup": [{"conclusion": "FAILURE"}]},
        files=GREEN_BUNDLE_FILES,
    )
    kwargs = _start_kwargs(tmp_path, "r-verdict-stale", ops)
    state = rs.new_run(
        subject=kwargs["subject"], repo=kwargs["repo"],
        repo_slug=kwargs["repo_slug"], ws_id=kwargs["ws_id"],
        target_dir=kwargs["target_dir"], bundle_dir=kwargs["bundle_dir"],
        profile=kwargs["profile"], run_id=kwargs["run_id"],
    )
    state.branch = "spec/WS-1-behaviour"
    state.pr = 100
    state.head = "deadbeef"
    state.ops = {
        "branch": {"status": "completed"},
        "author-charter": {"status": "completed", "skipped": True},
        "author-requirements": {"status": "completed", "skipped": True},
        "author-behaviour": {"status": "completed", "skipped": True},
        "commit": {"status": "completed"},
        "gate-candidate": {
            "status": "completed", "exit": 0,
        },
        "push": {"status": "completed"},
        "pr": {"status": "completed", "number": 100},
        "ready": {"status": "completed"},
        "review": {"status": "completed", "exit": 0},
        # Кэшированный вердикт с прошлого захода — устарел за время простоя.
        "verdict": {"status": "completed", "decision": "agent", "reason": "stale"},
    }
    rs.save(state)

    result = runner.advance(state, ops)

    assert ops.merged == []
    assert result.ops.get("merge", {}).get("status") != "completed"
    assert result.status == "stopped_merge_refused"
    assert result.ops["verdict"]["decision"] == "refuse"


# --- F-5: find_pr транзиентный сбой -----------------------------------------


def test_pr_reconciliation_find_pr_failure_stops_without_duplicate(
    tmp_path: Path, runs_root,
) -> None:
    """F-5: `find_pr` поднимает RuntimeError на сбое gh — не читать как
    "PR нет", не открывать второй; op остаётся started, run продолжает ждать."""
    ops = FakeOps(find_pr_error="gh pr list: transient network error")
    kwargs = _start_kwargs(tmp_path, "r-pr-transient", ops)
    state = rs.new_run(
        subject=kwargs["subject"], repo=kwargs["repo"],
        repo_slug=kwargs["repo_slug"], ws_id=kwargs["ws_id"],
        target_dir=kwargs["target_dir"], bundle_dir=kwargs["bundle_dir"],
        profile=kwargs["profile"], run_id=kwargs["run_id"],
    )
    state.branch = "spec/WS-1-behaviour"
    state.ops = {
        "branch": {"status": "completed"},
        "author-charter": {"status": "completed", "skipped": True},
        "author-requirements": {"status": "completed", "skipped": True},
        "author-behaviour": {"status": "completed", "skipped": True},
        "commit": {"status": "completed"},
        "gate-candidate": {
            "status": "completed", "exit": 0,
        },
        "push": {"status": "completed"},
        "pr": {"status": "started"},
    }
    rs.save(state)

    result = runner.advance(state, ops)

    assert "create_draft_pr" not in [c[0] for c in ops.calls]
    assert result.ops["pr"] == {"status": "started"}
    assert result.status == "running"


# --- F-6: commit перед push --------------------------------------------------


def test_commit_paths_called_between_author_and_push(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """F-6: `ops.commit_paths` вызывается между авторингом и push, только с
    `bundle_dir` (круг 5: не `git add -A`, явный список путей)."""
    ops = FakeOps(review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)

    state = runner.start(**_start_kwargs(tmp_path, "r-commit", ops))

    call_names = [c[0] for c in ops.calls]
    assert call_names.index("author") < call_names.index("commit_paths")
    assert call_names.index("commit_paths") < call_names.index("push_branch")
    assert state.ops["commit"]["status"] == "completed"
    assert len(ops.committed) == 1
    _target_dir, paths, message = ops.committed[0]
    assert paths == [BUNDLE_DIR]
    assert "Co-Authored-By" in message


# --- F-7: exit 4 переигрывает и prospective-гейт -----------------------------


def test_review_exit4_resets_gate_candidate_and_push_too(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """F-7: голова PR уехала (exit 4) — S4 обязан переиграться, не только S6."""
    ops = FakeOps(review_exit=4)

    state = runner.start(**_start_kwargs(tmp_path, "r-review-moved", ops))

    assert state.status == "running"
    assert "gate-candidate" not in state.ops
    assert "push" not in state.ops
    assert "ready" not in state.ops
    assert "review" not in state.ops
    assert state.ops["pr"]["status"] == "completed"  # PR не переоткрывается


# --- F-4 → CLI: шов runner ↔ gate-check --candidate, без мока ---------------


def test_gate_seam_required_absent_blocks_without_mock(
    tmp_path: Path, runs_root,
) -> None:
    """F-4 после миграции S4: интеграционный тест шва — РЕАЛЬНЫЙ
    `gate-check --candidate` (публичный CLI steward#140), не мок.

    Хороший бандл проходит S4 зелёным; бандл без единого frontmatter-узла
    обязан остановить S4 (GC-COMPLETENESS у CLI), а не пройти насквозь.
    """
    from governance.ops import DEVTOOLS_ROOT, RealOps

    if not (DEVTOOLS_ROOT / ".venv" / "bin" / "gate-check").exists():
        pytest.skip("gate-check CLI недоступен (uv sync без группы governance)")

    class CliGateOps(FakeOps):
        def gate_check_candidate(
            self, target_dir: str, bundle_dir: str, profile: str
        ) -> tuple[int, str]:
            self.calls.append(("gate_check_candidate", bundle_dir))
            return RealOps().gate_check_candidate(
                target_dir, bundle_dir, profile
            )

    def _run_to_gate(run_id: str, build_bundle) -> rs.RunState:
        target_dir = tmp_path / run_id
        target_dir.mkdir()
        make_profile(target_dir)
        build_bundle(target_dir)
        state = rs.new_run(
            subject="s", repo="alpha", repo_slug="owner/alpha", ws_id="WS-1",
            target_dir=str(target_dir), bundle_dir="spec",
            profile="profiles/mini.yaml", run_id=run_id,
        )
        state.branch = "spec/WS-1-behaviour"
        state.ops = {
            "branch": {"status": "completed"},
            "author-charter": {"status": "completed", "skipped": True},
            "author-requirements": {"status": "completed", "skipped": True},
            "author-behaviour": {"status": "completed", "skipped": True},
            # mini.yaml (fixture-профиль этого теста) не несёт узлы design/
            # acceptance/decomposition — все три шага обязаны быть
            # завершены-пропущены явно в фикстуре: этот тест конструирует
            # state вручную и зовёт advance() напрямую (минуя
            # _step_authoring целиком), а не через start(), поэтому
            # preflight design/acceptance/decomposition-узлов (Task 8 +
            # Task 5, `governance.policy_sources.target_profile_declares`)
            # сюда вовсе не попадает — вызывать его нечем без реального
            # profiles/mini.yaml-файла в target_dir. Цель теста — S4
            # (реальный `gate-check --candidate`), не S2/preflight; когда бы
            # шаг остался НЕзавершённым, `_step_authoring` либо авторил бы
            # узел (до фикс-раунда), либо теперь стопил бы
            # `stopped_preflight` (после) — оба исхода мимо сценария этого
            # теста, поэтому все три шага пропущены явно.
            "author-design": {"status": "completed", "skipped": True},
            "author-acceptance": {"status": "completed", "skipped": True},
            "author-decomposition": {"status": "completed", "skipped": True},
            "commit": {"status": "completed"},
        }
        rs.save(state)
        return runner.advance(state, CliGateOps())

    good_result = _run_to_gate(
        "r-gate-good-seam", lambda d: make_bundle(d, behaviour_ok=True),
    )
    assert good_result.status != "stopped_gate"
    assert good_result.ops["gate-candidate"]["status"] == "completed"

    def _no_frontmatter(target_dir: Path) -> None:
        bundle = target_dir / "spec"
        bundle.mkdir()
        (bundle / "notes.md").write_text("без frontmatter\n", encoding="utf-8")

    red_result = _run_to_gate("r-gate-empty-seam", _no_frontmatter)

    assert red_result.status == "stopped_gate"
    findings_file = rs.run_dir("r-gate-empty-seam") / "gate-findings.txt"
    assert findings_file.exists()
    assert "GC-COMPLETENESS" in findings_file.read_text(encoding="utf-8")


# --- M-2: s8-findings.txt несёт вывод gate-check, не только код -------------


def test_s8_findings_include_gate_check_output(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES,
        s8_exit=1, s8_output="error GC-BEH-TRACE: BEH-01 не трейсит ничего\n",
    )
    run_id = "r-s8-output"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))

    assert state.status == "merged_unverified"
    findings_file = rs.run_dir(run_id) / "s8-findings.txt"
    assert "GC-BEH-TRACE" in findings_file.read_text(encoding="utf-8")
    _repo_slug, _title, body = ops.issues[0]
    assert "GC-BEH-TRACE" in body


# --- Круг 4: start/verify не перезаписывают занятый run_id -----------------


def test_start_with_existing_run_id_raises_and_does_not_overwrite(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Codex-ревью PR #88 (major): `start()` с занятым `run_id` молча
    перезаписывала `run.json` (`os.replace` — атомарно, но без проверки
    занятости) — уничтожение чужого леджера. Отказ ДО каких-либо эффектов;
    существующий файл не тронут ни байтом."""
    run_id = "r-taken"
    kwargs = _start_kwargs(tmp_path, run_id, FakeOps())
    original = runner.start(**kwargs)
    assert original.status != "merged_unverified"  # леджер реально живёт
    before = rs.run_dir(run_id).joinpath("run.json").read_text(encoding="utf-8")

    other_ops = FakeOps()
    with pytest.raises(ValueError):
        runner.start(**_start_kwargs(tmp_path, run_id, other_ops))

    after = rs.run_dir(run_id).joinpath("run.json").read_text(encoding="utf-8")
    assert after == before  # ни байта не изменилось
    assert other_ops.calls == []  # отказ ДО каких-либо эффектов


def test_reserve_run_id_is_atomic_touch_not_exists_check(
    tmp_path: Path, runs_root,
) -> None:
    """Круг 7 (codex-major): первая починка (F-1/круг 4) была `exists()`
    отдельно от записи — TOCTOU между двумя параллельными `start()`/
    `verify()` с одним `run_id`. Атомарное резервирование —
    `Path.touch(exist_ok=False)` (`O_CREAT|O_EXCL`, один системный вызов):
    второй вызов отказывает БЕЗ предварительного `load()`, даже когда
    зарезервированный файл ещё пуст и `load()` прочитать бы его не смог
    (`json.JSONDecodeError` на пустой строке)."""
    run_id = "r-reserve-atomic"
    runner._reserve_run_id(run_id)
    raw = rs.run_dir(run_id).joinpath("run.json").read_text(encoding="utf-8")
    assert raw == ""  # только резерв, ещё не настоящий run.json

    with pytest.raises(ValueError):
        runner._reserve_run_id(run_id)


def test_verify_with_existing_run_id_raises(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """`verify()` — та же защита для дочернего run_id."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    parent_id = "r-verify-parent-taken"
    parent = runner.start(**_agent_merge_kwargs(tmp_path, parent_id, ops))
    assert parent.status == "merged_unverified"

    # Занятый child run_id — например, случайно совпал с чужим прогоном.
    # Другой ws_id, чтобы не наткнуться на WS-lock того же ws_id — здесь
    # проверяется отдельно занятость run_id, не WS-lock (круг 5).
    taken_child_id = "r-verify-child-taken"
    runner.start(
        **_start_kwargs(tmp_path, taken_child_id, FakeOps(), ws_id="WS-9")
    )
    before = rs.run_dir(taken_child_id).joinpath("run.json").read_text(
        encoding="utf-8"
    )

    with pytest.raises(ValueError):
        runner.verify(parent_id, ops, taken_child_id)

    after = rs.run_dir(taken_child_id).joinpath("run.json").read_text(
        encoding="utf-8"
    )
    assert after == before


# --- Круг 5, часть 1: S8 на default-ветке -----------------------------------


def test_s8_syncs_to_default_branch_before_gate_check(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """S8 чекаутит default-ветку (`base_ref` из `pr_facts.baseRefName`,
    зафиксированный на S7) и подтягивает merge-коммит ПЕРЕД `gate_check_s8` —
    иначе authoritative-срез читал бы feature-ветку прогона."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0, facts={**GREEN_PR_FACTS, "baseRefName": "main"},
        files=GREEN_BUNDLE_FILES, s8_exit=0,
    )

    state = runner.start(**_agent_merge_kwargs(tmp_path, "r-s8-sync", ops))

    assert state.status == "completed"
    assert state.base_ref == "main"
    assert ops.checked_out == [(state.target_dir, "main")]
    call_names = [c[0] for c in ops.calls]
    assert call_names.index("checkout_and_pull") < call_names.index("gate_check_s8")
    assert state.ops["sync-default"]["status"] == "completed"


def test_s8_sync_falls_back_to_master_when_base_ref_missing(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0, facts={**GREEN_PR_FACTS, "baseRefName": ""},
        files=GREEN_BUNDLE_FILES, s8_exit=0,
    )

    state = runner.start(
        **_agent_merge_kwargs(tmp_path, "r-s8-sync-fallback", ops)
    )

    assert state.base_ref == "master"
    assert ops.checked_out == [(state.target_dir, "master")]


def test_s8_sync_failure_stops_without_touching_status_or_gate(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """`checkout_and_pull` падает (например, локальные правки/дивергенция) —
    S8 останавливается ДО `gate_check_s8`, статус run'а не меняется (retry
    на следующем `advance()`/`resume()`, тот же паттерн, что `_step_pr`)."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES,
        checkout_and_pull_error="ff-only diverged",
    )

    state = runner.start(**_agent_merge_kwargs(tmp_path, "r-s8-sync-fail", ops))

    assert state.status == "running"
    assert state.ops["sync-default"]["status"] == "started"
    assert "gate_check_s8" not in [c[0] for c in ops.calls]


def test_verify_child_reuses_parent_base_ref(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0, facts={**GREEN_PR_FACTS, "baseRefName": "main"},
        files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    parent_id = "r-s8-sync-parent"
    parent = runner.start(**_agent_merge_kwargs(tmp_path, parent_id, ops))
    assert parent.status == "merged_unverified"
    assert parent.base_ref == "main"

    ops.s8_exit = 0
    calls_before_verify = len(ops.calls)
    child = runner.verify(parent_id, ops, "r-s8-sync-child")
    child_calls = ops.calls[calls_before_verify:]

    assert child.base_ref == "main"
    assert ("checkout_and_pull", "main") in child_calls


# --- Круг 5, часть 2: dirty-гард S1 -----------------------------------------


def test_dirty_target_dir_stops_before_branch_created(
    tmp_path: Path, runs_root,
) -> None:
    """Грязный `target_dir` ДО начала прогона → `stopped_dirty`, ничего не
    создано: `ensure_branch` не вызван, `commit_paths` тем более."""
    ops = FakeOps(dirty=True)

    state = runner.start(**_start_kwargs(tmp_path, "r-dirty", ops))

    assert state.status == "stopped_dirty"
    assert "branch" not in state.ops
    call_names = [c[0] for c in ops.calls]
    assert "ensure_branch" not in call_names
    assert "commit_paths" not in call_names


def test_resume_after_cleanup_from_stopped_dirty_proceeds(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Resume после ручной очистки — `branch` так и не стартовала, проверка
    просто повторяется и на этот раз проходит."""
    ops = FakeOps(
        dirty=True, review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES,
    )
    run_id = "r-dirty-resume"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))
    assert state.status == "stopped_dirty"

    ops.dirty = False  # человек прибрался
    result = runner.resume(run_id, ops)

    assert result.status != "stopped_dirty"
    assert result.ops["branch"]["status"] == "completed"


# --- Круг 5, часть 3: WS-lock по merged_unverified --------------------------


def test_start_blocked_by_merged_unverified_without_green_child(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    blocked_ws = "WS-LOCK-1"
    parent = runner.start(
        **_agent_merge_kwargs(tmp_path, "r-lock-parent", ops, ws_id=blocked_ws)
    )
    assert parent.status == "merged_unverified"

    blocked_run_id = "r-lock-blocked-attempt"
    with pytest.raises(ValueError, match="WS-LOCK-1"):
        runner.start(**_start_kwargs(
            tmp_path, blocked_run_id, FakeOps(), ws_id=blocked_ws,
        ))
    # WS-lock проверяется до резервирования run_id (круг 7) — отказ не
    # оставляет пустую run.json-заглушку под несостоявшимся прогоном.
    assert not (rs.run_dir(blocked_run_id) / "run.json").exists()


def test_start_unblocked_after_verify_child_completes(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    ws = "WS-LOCK-2"
    parent = runner.start(
        **_agent_merge_kwargs(tmp_path, "r-lock-parent2", ops, ws_id=ws)
    )
    assert parent.status == "merged_unverified"

    ops.s8_exit = 0  # находки устранены фикс-PR'ом
    child = runner.verify(parent.run_id, ops, "r-lock-child2")
    assert child.status == "completed"

    # Разблокировано зелёным потомком — новый прогон стартует без ValueError.
    unblocked = runner.start(**_start_kwargs(
        tmp_path, "r-lock-after-fix", FakeOps(), ws_id=ws,
    ))
    assert unblocked.run_id == "r-lock-after-fix"


def test_start_broken_neighbor_run_json_is_skipped(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Битый (не-JSON) `run.json` среди соседей не мешает обходу WS-lock."""
    broken_dir = rs.run_dir("r-broken-neighbor")
    broken_dir.mkdir(parents=True)
    (broken_dir / "run.json").write_text("not json at all", encoding="utf-8")

    state = runner.start(**_start_kwargs(tmp_path, "r-after-broken", FakeOps()))

    assert state.run_id == "r-after-broken"


# --- Круг 6: два окна S8 (codex-ревью PR #88) -------------------------------


def _s8_preset_ops(**gate_authoritative: object) -> dict:
    """Общий журнал операций до захода в S8 — только gate-authoritative
    (и опционально sync-default) варьируется между тестами круга 6."""
    ops: dict = {
        "branch": {"status": "completed"},
        "author-charter": {"status": "completed", "skipped": True},
        "author-requirements": {"status": "completed", "skipped": True},
        "author-behaviour": {"status": "completed", "skipped": True},
        "commit": {"status": "completed"},
        "gate-candidate": {
            "status": "completed", "exit": 0,
        },
        "push": {"status": "completed"},
        "pr": {"status": "completed", "number": 100},
        "ready": {"status": "completed"},
        "review": {"status": "completed", "exit": 0},
        "verdict": {"status": "completed", "decision": "agent", "reason": "ok"},
        "merge": {"status": "completed", "merged": True},
    }
    if gate_authoritative:
        ops["gate-authoritative"] = gate_authoritative
    return ops


def test_resume_completes_when_gate_authoritative_done_but_status_stuck_running(
    tmp_path: Path, runs_root,
) -> None:
    """Окно 1 (успех): op `gate-authoritative` уже `completed(exit=0)` на
    диске, но `status` ещё `"running"` (гибель между `op_complete` и
    финальным `save`) — resume обязан довести до `completed`, не оставлять
    run вечно `running`."""
    ops = FakeOps()
    run_id = "r-s8-stuck-ok"
    kwargs = _agent_merge_kwargs(tmp_path, run_id, ops)
    state = rs.new_run(
        subject=kwargs["subject"], repo=kwargs["repo"],
        repo_slug=kwargs["repo_slug"], ws_id=kwargs["ws_id"],
        target_dir=kwargs["target_dir"], bundle_dir=kwargs["bundle_dir"],
        profile=kwargs["profile"], run_id=run_id,
    )
    state.branch = "spec/WS-1-behaviour"
    state.pr = 100
    state.head = "deadbeef"
    state.base_ref = "master"
    state.ops = {
        **_s8_preset_ops(status="completed", exit=0),
        "sync-default": {"status": "completed"},
    }
    rs.save(state)
    assert state.status == "running"

    result = runner.advance(state, ops)

    assert result.status == "completed"
    assert "checkout_and_pull" not in [c[0] for c in ops.calls]
    assert "gate_check_s8" not in [c[0] for c in ops.calls]


def test_resume_completes_fail_path_when_status_stuck_running_after_gate_fail(
    tmp_path: Path, runs_root,
) -> None:
    """Окно 1 (провал): op `gate-authoritative` уже `completed(exit=1)` на
    диске, но `status` ещё `"running"` и `remediation-issue` ещё не начат —
    resume обязан довести fail-путь до конца: завести issue, выставить
    `merged_unverified`, не выйти на полпути."""
    ops = FakeOps()
    run_id = "r-s8-stuck-fail"
    kwargs = _agent_merge_kwargs(tmp_path, run_id, ops)
    state = rs.new_run(
        subject=kwargs["subject"], repo=kwargs["repo"],
        repo_slug=kwargs["repo_slug"], ws_id=kwargs["ws_id"],
        target_dir=kwargs["target_dir"], bundle_dir=kwargs["bundle_dir"],
        profile=kwargs["profile"], run_id=run_id,
    )
    state.branch = "spec/WS-1-behaviour"
    state.pr = 100
    state.head = "deadbeef"
    state.base_ref = "master"
    state.ops = {
        **_s8_preset_ops(status="completed", exit=1),
        "sync-default": {"status": "completed"},
    }
    run_dir_path = rs.run_dir(run_id)
    run_dir_path.mkdir(parents=True, exist_ok=True)
    (run_dir_path / "s8-findings.txt").write_text(
        "gate-check (S8, authoritative) завершился с кодом 1\n\nGC-X\n",
        encoding="utf-8",
    )
    rs.save(state)
    assert state.status == "running"

    result = runner.advance(state, ops)

    assert result.status == "merged_unverified"
    assert len(ops.issues) == 1
    assert result.ops["remediation-issue"]["status"] == "completed"
    assert "checkout_and_pull" not in [c[0] for c in ops.calls]
    assert "gate_check_s8" not in [c[0] for c in ops.calls]


def test_sync_default_always_rechecked_even_if_already_completed(
    tmp_path: Path, runs_root,
) -> None:
    """Окно 2: `sync-default` уже `completed` в журнале (из прошлой
    попытки), но `gate-authoritative` ещё не заведён — гибель случилась
    между sync'ом и самим `gate_check_s8`. Resume обязан перезапустить
    `checkout_and_pull`, а не пропустить его по журналу — default-ветка
    могла уехать дальше за то время, что прогон простоял."""
    ops = FakeOps(s8_exit=0)
    run_id = "r-s8-resync"
    kwargs = _agent_merge_kwargs(tmp_path, run_id, ops)
    state = rs.new_run(
        subject=kwargs["subject"], repo=kwargs["repo"],
        repo_slug=kwargs["repo_slug"], ws_id=kwargs["ws_id"],
        target_dir=kwargs["target_dir"], bundle_dir=kwargs["bundle_dir"],
        profile=kwargs["profile"], run_id=run_id,
    )
    state.branch = "spec/WS-1-behaviour"
    state.pr = 100
    state.head = "deadbeef"
    state.base_ref = "main"
    state.ops = {
        **_s8_preset_ops(),
        "sync-default": {"status": "completed"},  # из ПРОШЛОЙ попытки
    }
    rs.save(state)

    result = runner.advance(state, ops)

    assert ("checkout_and_pull", "main") in ops.calls
    assert result.status == "completed"
    assert result.ops["gate-authoritative"] == {"status": "completed", "exit": 0}


# --- Круг 8: s8-findings.txt производный от журнала, не источник истины ----


def test_resume_rebuilds_missing_s8_findings_from_op_output(
    tmp_path: Path, runs_root,
) -> None:
    """Круг 8 (codex-ревью PR #88): op `gate-authoritative` уже
    `completed(exit=1, output=...)` на диске, но `s8-findings.txt` НЕТ
    (гибель между `op_complete` и `write_text`) и статус ещё `"running"` —
    resume обязан довести до `merged_unverified`, восстановив findings-файл
    из журнала (не упасть на `read_text()` c `FileNotFoundError`), и
    завести remediation-issue."""
    ops = FakeOps()
    run_id = "r-s8-findings-missing"
    kwargs = _agent_merge_kwargs(tmp_path, run_id, ops)
    state = rs.new_run(
        subject=kwargs["subject"], repo=kwargs["repo"],
        repo_slug=kwargs["repo_slug"], ws_id=kwargs["ws_id"],
        target_dir=kwargs["target_dir"], bundle_dir=kwargs["bundle_dir"],
        profile=kwargs["profile"], run_id=run_id,
    )
    state.branch = "spec/WS-1-behaviour"
    state.pr = 100
    state.head = "deadbeef"
    state.base_ref = "master"
    state.ops = {
        **_s8_preset_ops(
            status="completed", exit=1, output="error GC-X: bad\n",
        ),
        "sync-default": {"status": "completed"},
    }
    rs.save(state)
    findings_file = rs.run_dir(run_id) / "s8-findings.txt"
    assert not findings_file.exists()  # окно круга 8: файл не успел записаться
    assert state.status == "running"

    result = runner.advance(state, ops)

    assert result.status == "merged_unverified"
    assert findings_file.exists()
    restored = findings_file.read_text(encoding="utf-8")
    assert "1" in restored
    assert "GC-X" in restored
    assert len(ops.issues) == 1
    _repo_slug, _title, body = ops.issues[0]
    assert "GC-X" in body
    assert result.ops["remediation-issue"]["status"] == "completed"
    assert "checkout_and_pull" not in [c[0] for c in ops.calls]
    assert "gate_check_s8" not in [c[0] for c in ops.calls]


# --- Круг 10: статус фиксируется до стоп-комментария -----------------------


def test_stop_with_comment_saves_status_before_commenting(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Круг 10 (codex-major): статус сохраняется на диске ДО best-effort
    комментария во всех стоп-с-комментарием путях (S6 exit 1/2/3, S7
    human/refuse, merge False) — иначе гибель между `ops.comment` и
    `save()` оставляла run в `"running"`, и следующий `advance()` переигрывал
    этот же шаг с нуля, включая повторный (дублирующий) комментарий.
    Проверка через "шпиона": `ops.comment`, вызванный, читает `run.json` с
    диска в момент своего вызова — статус там уже обязан быть терминальным."""
    ops = FakeOps(review_exit=1)
    run_id = "r-comment-order"
    seen_status_at_comment_time: dict[str, str] = {}
    original_comment = ops.comment

    def spying_comment(repo_slug: str, pr: int, body: str) -> None:
        seen_status_at_comment_time["status"] = rs.load(run_id).status
        return original_comment(repo_slug, pr, body)

    ops.comment = spying_comment  # type: ignore[method-assign]

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))

    assert state.status == "stopped_review"
    assert seen_status_at_comment_time["status"] == "stopped_review"


def test_resume_from_stopped_review_does_not_repost_comment_when_fixed(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Круг 10: resume из уже сохранённого `stopped_review`, когда причина
    устранена (review теперь проходит) — второго комментария нет.
    Комментарий — часть самого стоп-пути (`_stop_with_comment` в
    `_step_review`), не безусловный побочный эффект `resume()` — он
    срабатывает, только если review реально проваливается СНОВА."""
    ops = FakeOps(review_exit=1)
    run_id = "r-comment-no-repost"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))
    assert state.status == "stopped_review"
    assert len(ops.comments) == 1

    ops.review_exit = 0  # находки устранены
    result = runner.resume(run_id, ops)

    # Пайплайн продолжается дальше и мог легитимно оставить свой комментарий
    # (например S7 human/refuse без мока безопасности) — важно, что комментарий
    # ИМЕННО про этот стоп на review не задублирован.
    assert result.status != "stopped_review"
    review_stop_comments = [c for c in ops.comments if "ревью нашло находки" in c]
    assert len(review_stop_comments) == 1


# --- B2 Task 1: follow-ups приёмки B1 ---------------------------------------


def test_start_rejects_invalid_merge_authority_before_reserving_run_id(
    tmp_path: Path, runs_root,
) -> None:
    """Minor из приёмки #88: невалидный `merge_authority` валидируется ДО
    `_reserve_run_id` — раньше он навсегда резервировал `run_id` пустым
    `run.json`, потому что единственная валидация жила в `new_run()`,
    вызываемом ПОСЛЕ резервирования."""
    run_id = "r-bad-authority"
    kwargs = _start_kwargs(
        tmp_path, run_id, FakeOps(), merge_authority="agent",
    )

    with pytest.raises(ValueError):
        runner.start(**kwargs)

    assert not (rs.run_dir(run_id) / "run.json").exists()


def test_start_rejects_invalid_author_backend_before_reserving_run_id(
    tmp_path: Path, runs_root,
) -> None:
    """Тот же класс minor, что и merge_authority выше (I-3, финальное
    ревью): Task 1 чинил его для merge_authority (приёмка #88), Task 2
    внесла заново для author_backend — `validate_author_backend` жила
    только внутри `new_run()`, вызываемом ПОСЛЕ `_reserve_run_id`. Через
    CLI недостижимо (`choices=["codex", "disp"]`), но `start()` —
    публичный API."""
    run_id = "r-bad-author-backend"
    kwargs = _start_kwargs(
        tmp_path, run_id, FakeOps(), author_backend="claude",
    )

    with pytest.raises(ValueError):
        runner.start(**kwargs)

    assert not (rs.run_dir(run_id) / "run.json").exists()


def test_stop_review_comment_includes_evidence_hint(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Спека §7: стоп-комментарий S6 (exit 1) дополняется evidence-подсказкой
    про известный ложный класс находок «файлов нет» — `git cat-file -e
    <head>:<путь>` с реальной подставленной головой."""
    ops = FakeOps(review_exit=1)
    run_id = "r-review-evidence"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))

    assert state.status == "stopped_review"
    assert ops.comments
    assert f"git cat-file -e {ops.head}" in ops.comments[-1]


def test_stop_review_comment_survives_head_sha_failure(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """I-6, финальное ревью: `head_sha` — чисто косметическая evidence-
    подсказка, вызывается на СТОП-пути ДО `_stop_with_comment`. Если ветки
    нет локально/`target_dir` уехал (`RealOps.head_sha` зовёт `git
    rev-parse` с `check=True`), штатная остановка «ревью нашло находки» не
    должна превращаться в необработанное исключение вместо
    comment+`stopped_review` — сбой глотается, в подсказку идёт литерал
    `<head>`."""
    ops = FakeOps(review_exit=1, head_sha_error="fatal: bad revision")
    run_id = "r-review-evidence-head-fails"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))

    assert state.status == "stopped_review"
    assert ops.comments
    assert "git cat-file -e <head>" in ops.comments[-1]


# --- B2 Task 2: авторинг-бэкенд codex|disp ----------------------------------


def test_default_author_backend_is_codex_author_disp_not_called(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Дефолт `author_backend="codex"` не меняет поведение B1: все узлы
    идут через `ops.author`, `ops.author_disp` не вызывается вовсе."""
    ops = FakeOps(review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)

    state = runner.start(**_start_kwargs(tmp_path, "r-disp-default", ops))

    assert ops.authored == [
        "charter", "requirements", "behaviour-spec", "design", "acceptance",
        "decomposition",
    ]
    assert ops.author_disp_calls == []
    assert state.author_backend == "codex"


def test_disp_backend_used_only_for_behaviour_node(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """`author_backend="disp"` переключает ТОЛЬКО behaviour-spec узел на
    `ops.author_disp`; charter/requirements остаются на `ops.author` (codex)
    — disp-цикл осмыслен только для полируемого документа."""
    # Форма конфига не должна зависеть от harness.env машины (на CI его нет):
    # харнесс задаётся окружением явно.
    monkeypatch.setenv("AUTHOR_HARNESS", "claude")
    monkeypatch.setenv("REVIEW_HARNESS", "claude")
    monkeypatch.setenv("AI_PROSTO_HARNESS_ENV", "/nonexistent")
    ops = FakeOps(review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)
    run_id = "r-disp-behaviour"

    state = runner.start(**_start_kwargs(
        tmp_path, run_id, ops, author_backend="disp",
    ))

    assert ops.authored == [
        "charter", "requirements", "design", "acceptance", "decomposition",
    ]
    assert len(ops.author_disp_calls) == 1
    target_dir, task, config_path, slug = ops.author_disp_calls[0]
    assert target_dir == str(tmp_path / f"target-{run_id}")
    assert "#### BEH-NN" in task
    assert "traces:" in task
    assert "checked_by" in task
    assert state.ops["author-behaviour"]["status"] == "completed"
    assert state.author_backend == "disp"
    # Сосед стартует только на чистом дереве (document-pipeline.md §2: первый
    # PROPOSING делает reset --hard + clean): charter/requirements и source-
    # слой обязаны быть в коммите ДО `run`. Живой прогон spec-runner#480:
    # без этого disp вернул 2 «рабочее дерево не готово к run».
    commit_at = ops.calls.index(("commit_paths", (BUNDLE_DIR,)))
    disp_at = ops.calls.index(("author_disp", task))
    assert commit_at < disp_at, ops.calls

    # Конфиг вида `document`: форма секции `[pipeline]` и есть объявление
    # вида (disputatio SPEC-002 §3.2), поэтому проверяется форма, а не факт
    # существования файла.
    # Нормализованный, а не сырой: грамматика соседа — только нижний регистр.
    assert slug == f"beh-{state.ws_id}".lower()
    assert re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", slug), slug
    assert config_path == str(rs.run_dir(run_id) / "disp-doc.toml")
    config = Path(config_path).read_text(encoding="utf-8")
    assert f'document_path = "{BUNDLE_DIR}/15-behaviour-spec.md"' in config
    assert "[pipeline.checklists.doc]" in config
    assert 'findings_item = "B3"' in config
    assert "[pipeline.checklists.doc.items]" in config
    assert "B3 = " in config
    # Взаимоисключающая форма: ключи вида `pair` здесь — `ConfigError` у
    # disputatio ещё до любой мутации, поэтому их отсутствие — часть контракта.
    assert "spec_path" not in config
    assert "plan_path" not in config
    assert "max_architectural_returns" not in config

    # Условие (3) пункта плана: анкер обязан резолвиться ВНЕ рабочего дерева
    # цели, иначе disp отказывает на старте (`validate_anchor_path`
    # сверяет containment против toplevel репо цели). Задаём явно, а не
    # полагаемся на дефолт соседа.
    anchor_line = [ln for ln in config.splitlines() if ln.startswith("anchor_path")]
    assert len(anchor_line) == 1, config
    anchor = Path(anchor_line[0].split('"')[1])
    assert anchor.is_absolute()
    assert not anchor.resolve().is_relative_to(Path(target_dir).resolve()), (
        f"анкер {anchor} лежит внутри дерева цели {target_dir} — disp откажет на старте"
    )

    # Живой прогон 2026-09-15 (spec-runner#480): disputatio отверг конфиг —
    # «нет обязательного ключа agents.author.adapter». Секции `[agents.*]`
    # и `[limits]` у соседа НЕ дефолтные: adapter/model и четыре целых
    # обязательны (SPEC-002 §3.2, pipeline_config._agent/_session_profile).
    import tomllib
    parsed = tomllib.loads(config)
    for role in ("author", "reviewer"):
        agent = parsed["agents"][role]
        assert agent["adapter"] in ("claude_code", "codex"), agent
        assert isinstance(agent["model"], str) and agent["model"], agent
    limits = parsed["limits"]
    for key in ("max_rounds", "max_total_tokens", "max_wall_seconds", "schema_retries"):
        assert isinstance(limits[key], int) and not isinstance(limits[key], bool)


@pytest.mark.parametrize(
    ("ws_id", "expected"),
    [
        ("WS-1", "beh-ws-1"),
        ("WS-spec-runner-341", "beh-ws-spec-runner-341"),
        ("ws.with_underscore", "beh-ws.with_underscore"),
        ("WS/slash", "beh-ws-slash"),
    ],
)
def test_disp_doc_slug_matches_disputatio_grammar(ws_id: str, expected: str) -> None:
    """Слаг обязан пройти грамматику §4.1 соседа: `[a-z0-9][a-z0-9._-]{0,63}`.

    Не косметика: наш `ws_id` обычно в ВЕРХНЕМ регистре, а `validate_slug`
    зовётся `fullmatch`, так что `beh-WS-…` отвергается `ValueError` →
    `ConfigError` ещё до старта пайплайна. Приведение однозначно, поэтому
    нормализуем, а не отказываем.
    """
    state = SimpleNamespace(ws_id=ws_id)
    slug = runner._disp_doc_slug(state)

    assert slug == expected
    assert re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", slug), slug


def test_disp_doc_slug_is_truncated_to_the_grammar_limit() -> None:
    """Длина слага ограничена 64 символами той же грамматикой."""
    state = SimpleNamespace(ws_id="W" * 200)

    slug = runner._disp_doc_slug(state)

    assert len(slug) == 64
    assert re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", slug), slug


def test_disp_doc_checklist_carries_the_dsl_frontmatter_and_pin(
    tmp_path: Path, runs_root,
) -> None:
    """devtools#204 п.1: чеклист сходимости `doc` зеркалит ВЕСЬ DSL узла,
    включая frontmatter (`spec_stage`/`status`) и пин `upstream_hashes` —
    иначе «сошедшийся» документ стопит S4 `gate-candidate`. Пункт берётся из
    того же `_AUTHOR_DSL`, что и промпт авторинга: одно место, не пересказ.
    """
    import tomllib
    from governance.ops import _AUTHOR_DSL

    ops = FakeOps(review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)
    runner.start(**_start_kwargs(
        tmp_path, "r-disp-dsl", ops, author_backend="disp",
    ))
    _, _, config_path, _ = ops.author_disp_calls[0]
    config = tomllib.loads(Path(config_path).read_text(encoding="utf-8"))
    items = config["pipeline"]["checklists"]["doc"]["items"]
    assert items["B0"] == _AUTHOR_DSL["behaviour-spec"]
    assert "upstream_hashes" in items["B0"] and "spec_stage" in items["B0"]
    assert config["pipeline"]["checklists"]["doc"]["findings_item"] in items


def test_disp_doc_anchor_leaves_the_target_when_runs_root_is_inside_it(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """devtools#204 п.2: цель прогона — сам devtools ⇒ `RUNS_ROOT` лежит
    внутри дерева цели, и анкер в каталоге прогона отказал бы на старте
    (`validate_anchor_path`). Тогда анкер уходит в пользовательский
    state-каталог (`XDG_STATE_HOME`), как дефолт самого disp."""
    xdg = tmp_path.parent / f"xdg-{tmp_path.name}"
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))
    ops = FakeOps(review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)
    # runs_root = tmp_path/"runs" — внутри цели, если цель = tmp_path.
    runner.start(**_start_kwargs(
        tmp_path, "r-disp-self", ops, author_backend="disp",
        target_dir=str(tmp_path),
    ))
    target_dir, _, config_path, _ = ops.author_disp_calls[0]
    config = Path(config_path).read_text(encoding="utf-8")
    anchor_line = [ln for ln in config.splitlines() if ln.startswith("anchor_path")]
    anchor = Path(anchor_line[0].split('"')[1])
    assert not anchor.resolve().is_relative_to(Path(target_dir).resolve()), anchor
    assert anchor.resolve().is_relative_to(xdg.resolve()), anchor
    assert "r-disp-self" in str(anchor)


def test_disp_anchor_dir_is_canonical_even_for_relative_xdg_state_home(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Ревью #242, круг 5: относительный `XDG_STATE_HOME` резолвится по CWD
    раннера, а disp резолвил бы сырую строку от `cwd=target_dir`. В конфиг и
    в пин уходит абсолютный канонизированный путь."""
    outside = tmp_path.parent / f"cwd-{tmp_path.name}"
    outside.mkdir()
    monkeypatch.chdir(outside)
    monkeypatch.setenv("XDG_STATE_HOME", "state-rel")
    ops = FakeOps(review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)
    run_id = "r-disp-rel-xdg"
    state = runner.start(**_start_kwargs(
        tmp_path, run_id, ops, author_backend="disp", target_dir=str(tmp_path),
    ))
    _, _, config_path, _ = ops.author_disp_calls[0]
    config = Path(config_path).read_text(encoding="utf-8")
    line = [ln for ln in config.splitlines() if ln.startswith("anchor_path")]
    anchor = Path(line[0].split('"')[1])
    assert anchor.is_absolute(), anchor
    assert anchor == (outside / "state-rel" / "devtools" / "disp-anchors" / run_id).resolve()
    assert Path(state.disp_anchor_dir) == anchor


def test_disp_anchor_dir_is_pinned_and_survives_an_environment_change(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Ревью #242: анкер — координата начатого пайплайна, как слаг. Retry
    из другого окружения (иной XDG_STATE_HOME) обязан отдать соседу ТОТ ЖЕ
    каталог: `resume` ищет журнал целостности по живому `anchor_path`."""
    first = tmp_path.parent / f"xdg-a-{tmp_path.name}"
    monkeypatch.setenv("XDG_STATE_HOME", str(first))
    ops = FakeOps(author_disp_exit=1)
    run_id = "r-disp-anchor-pin"
    state = runner.start(**_start_kwargs(
        tmp_path, run_id, ops, author_backend="disp", target_dir=str(tmp_path),
    ))
    assert state.status == "stopped_author"

    def anchor_of(config_path: str) -> Path:
        config = Path(config_path).read_text(encoding="utf-8")
        line = [ln for ln in config.splitlines() if ln.startswith("anchor_path")]
        return Path(line[0].split('"')[1])

    pinned = anchor_of(ops.author_disp_calls[0][2])
    assert pinned.resolve().is_relative_to(first.resolve())
    assert Path(rs.load(run_id).disp_anchor_dir) == pinned

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path.parent / f"xdg-b-{tmp_path.name}"))
    ops.author_disp_exit = 0
    runner.resume(run_id, ops)
    assert anchor_of(ops.author_disp_calls[1][2]) == pinned


def test_disp_slug_is_pinned_in_run_state_and_reused_on_retry(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """devtools#204 п.3 (решение владельца в issue): слаг пинуется в
    `run.json` при первом старте — смена правил нормализации не осиротит
    начатый пайплайн; retry читает пин, а не считает заново."""
    ops = FakeOps(author_disp_exit=1)
    run_id = "r-disp-pin"
    state = runner.start(**_start_kwargs(
        tmp_path, run_id, ops, author_backend="disp",
    ))
    assert state.status == "stopped_author"
    first_slug = ops.author_disp_calls[0][3]
    assert state.disp_slug == first_slug
    assert rs.load(run_id).disp_slug == first_slug

    monkeypatch.setattr(runner, "_disp_doc_slug", lambda _s: "rules-changed")
    ops.author_disp_exit = 0
    state = runner.resume(run_id, ops)
    assert ops.author_disp_calls[1][3] == first_slug
    assert state.ops["author-behaviour"]["status"] == "completed"


def test_disp_retry_resumes_an_existing_pipeline_dir_instead_of_run(
    tmp_path: Path, runs_root,
) -> None:
    """devtools#204 п.3: `disp pipeline run` на существующем
    `.disputatio/pipelines/<slug>/` отказывает (`_check_pipeline_dir_absent`
    у соседа: «продолжите через resume»). Ровно один путь retry: каталог
    есть ⇒ `resume`, нет ⇒ `run`."""
    ops = FakeOps(author_disp_exit=1)
    run_id = "r-disp-resume"
    state = runner.start(**_start_kwargs(
        tmp_path, run_id, ops, author_backend="disp",
    ))
    assert ops.author_disp_resume == [False]
    target_dir, _, _, slug = ops.author_disp_calls[0]
    (Path(target_dir) / ".disputatio" / "pipelines" / slug).mkdir(parents=True)
    # Первый авторский раунд соседа уже написал черновик узла в дереве
    # цели (ревью #242): это НЕ готовый узел и не повод для skip.
    draft = Path(target_dir) / BUNDLE_DIR / "15-behaviour-spec.md"
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text("# черновик первого раунда\n", encoding="utf-8")

    ops.author_disp_exit = 0
    commits_before = ops.calls.count(("commit_paths", (BUNDLE_DIR,)))
    state = runner.resume(run_id, ops)
    assert ops.author_disp_resume == [False, True]
    assert state.ops["author-behaviour"]["status"] == "completed"
    assert state.ops["author-behaviour"].get("skipped") is False
    # Коммит бандла — только перед `run`; `resume` продолжает живой пайплайн
    # соседа, и его черновик узла коммитить под нашим сообщением нельзя.
    # Следующий commit_paths — штатный S3 после авторинга.
    disp_resume_at = len(ops.calls) - 1 - ops.calls[::-1].index(
        ("author_disp", ops.author_disp_calls[1][1])
    )
    before_resume = ops.calls[:disp_resume_at]
    assert before_resume.count(("commit_paths", (BUNDLE_DIR,))) == commits_before


def test_hand_fixed_node_without_pipeline_dir_is_accepted_after_pin(
    tmp_path: Path, runs_root, capsys,
) -> None:
    """Ревью #242, круг 3: операторский выход из пинованного состояния.
    Стоп → оператор убирает каталог пайплайна соседа и кладёт/чинит файл
    узла руками → resume принимает файл как есть, без вызова соседа."""
    ops = FakeOps(author_disp_exit=1)
    run_id = "r-disp-handfix"
    state = runner.start(**_start_kwargs(
        tmp_path, run_id, ops, author_backend="disp",
    ))
    assert state.status == "stopped_author"
    assert "принять узел руками" in capsys.readouterr().out
    target_dir, _, _, slug = ops.author_disp_calls[0]
    # Каталог пайплайна сосед не создал (или оператор убрал); файл — руками.
    assert not (Path(target_dir) / ".disputatio" / "pipelines" / slug).exists()
    node = Path(target_dir) / BUNDLE_DIR / "15-behaviour-spec.md"
    node.parent.mkdir(parents=True, exist_ok=True)
    node.write_text("#### BEH-01\n", encoding="utf-8")

    state = runner.resume(run_id, ops)
    assert len(ops.author_disp_calls) == 1
    assert state.ops["author-behaviour"] == {"status": "completed", "skipped": True}


def test_foreign_pipeline_dir_on_first_start_stops_instead_of_resuming(
    tmp_path: Path, runs_root,
) -> None:
    """Ревью #242: каталог `.disputatio/pipelines/<slug>/` от чужого или
    заброшенного прогона на ПЕРВОМ старте — стоп с подсказкой, а не `resume`
    с нашим конфигом и анкером поверх чужого манифеста."""
    ops = FakeOps(review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)
    run_id = "r-disp-foreign"
    kwargs = _start_kwargs(tmp_path, run_id, ops, author_backend="disp")
    foreign = Path(kwargs["target_dir"]) / ".disputatio" / "pipelines" / "beh-ws-1"
    foreign.mkdir(parents=True)

    state = runner.start(**kwargs)

    assert state.status == "stopped_author"
    assert ops.author_disp_calls == []
    assert state.disp_slug is None and state.disp_anchor_dir is None


def test_foreign_pipeline_dir_with_its_draft_still_stops(
    tmp_path: Path, runs_root,
) -> None:
    """Ревью #242, круг 4: чужой каталог пайплайна уже написал черновик
    узла — черновик не наш готовый узел, стоп-гард стоит ДО skip-ветки."""
    ops = FakeOps(review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)
    run_id = "r-disp-foreign-draft"
    kwargs = _start_kwargs(tmp_path, run_id, ops, author_backend="disp")
    target = Path(kwargs["target_dir"])
    (target / ".disputatio" / "pipelines" / "beh-ws-1").mkdir(parents=True)
    draft = target / BUNDLE_DIR / "15-behaviour-spec.md"
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text("#### BEH-01 чужой черновик\n", encoding="utf-8")

    state = runner.start(**kwargs)

    assert state.status == "stopped_author"
    assert ops.author_disp_calls == []
    assert state.ops.get("author-behaviour", {}).get("skipped") is not True
    assert state.disp_slug is None


def test_disp_config_without_model_stops_author_with_reason(
    tmp_path: Path, runs_root, monkeypatch, capsys,
) -> None:
    """codex без модели: disputatio требует agents.*.model — стоп с причиной
    до вызова соседа, не traceback."""
    monkeypatch.setenv("AUTHOR_HARNESS", "codex")
    monkeypatch.delenv("AUTHOR_MODEL", raising=False)
    monkeypatch.setenv("AI_PROSTO_HARNESS_ENV", "/nonexistent")
    ops = FakeOps()
    state = runner.start(**_start_kwargs(
        tmp_path, "r-disp-nomodel", ops, author_backend="disp",
    ))
    assert state.status == "stopped_author"
    assert ops.author_disp_calls == []
    assert "AUTHOR_MODEL" in capsys.readouterr().out


def test_disp_backend_author_disp_failure_stops_author(
    tmp_path: Path, runs_root,
) -> None:
    """Провал `author_disp` (rc != 0) останавливает прогон так же, как
    провал `ops.author` — `stopped_author`, статус не подменяется бэкендом."""
    ops = FakeOps(author_disp_exit=1)
    run_id = "r-disp-fail"

    state = runner.start(**_start_kwargs(
        tmp_path, run_id, ops, author_backend="disp",
    ))

    assert state.status == "stopped_author"
    assert state.ops["author-behaviour"]["status"] == "started"


def test_new_run_rejects_unknown_author_backend() -> None:
    with pytest.raises(ValueError):
        rs.new_run(
            subject="s", repo="alpha", repo_slug="owner/alpha", ws_id="WS-1",
            target_dir="/tmp/x", bundle_dir="spec",
            profile="profiles/team-exp.yaml", run_id="r-bad-backend",
            author_backend="claude",
        )


def test_gate_stops_on_dsl_empty_bundle(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Гард GC-DSL-EMPTY (боевой прогон kapelle#47): candidate_valid при нуле
    распознаваемых DSL-заголовков — стоп, а не вакуумный зелёный."""

    class DialectOps(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            rc = super().author(target_dir, kind, subject, bundle_dir)
            if kind == "behaviour-spec":
                path = Path(target_dir) / bundle_dir / "15-behaviour-spec.md"
                path.write_text("### BS-001 — диалект без DSL\n")
            return rc

    ops = DialectOps(review_exit=0, facts=GREEN_PR_FACTS)
    state = runner.start(**_start_kwargs(tmp_path, "r-dsl-empty", ops))

    assert state.status == "stopped_gate"
    findings = (runner.run_dir("r-dsl-empty") / "gate-findings.txt").read_text()
    assert "GC-DSL-EMPTY" in findings and "15-behaviour-spec.md" in findings
    assert "push" not in state.ops


def test_gate_dsl_empty_design_message_names_design_grammar(
    tmp_path: Path, runs_root,
) -> None:
    """MINOR-2: GC-DSL-EMPTY для 20-design.md обязан называть СВОЮ
    грамматику (`#### Q-NN · owner_role: … · resolution: …`), не чужую
    `#### FR-NN:` / `#### BEH-NN:` requirements/behaviour-spec."""

    class _Ops(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            rc = super().author(target_dir, kind, subject, bundle_dir)
            if kind != "design":
                return rc
            bundle = Path(target_dir) / bundle_dir
            req_pin = blob_sha1(
                (bundle / "10-requirements.md").read_text(encoding="utf-8")
            )
            beh_pin = blob_sha1(
                (bundle / "15-behaviour-spec.md").read_text(encoding="utf-8")
            )
            (bundle / "20-design.md").write_text(
                "---\n"
                "spec_stage: design\n"
                "status: draft\n"
                "owner_role: architects\n"
                "traces_to: [requirements, behaviour-spec]\n"
                "upstream_hashes:\n"
                f'  requirements: "{req_pin}"\n'
                f'  behaviour-spec: "{beh_pin}"\n'
                "---\n"
                "# design\nПросто текст без единого DSL-заголовка.\n",
                encoding="utf-8",
            )
            return rc

    ops = _Ops(facts=GREEN_PR_FACTS)
    state = runner.start(
        **_start_kwargs(tmp_path, "r-design-dsl-empty-msg", ops)
    )

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-design-dsl-empty-msg") / "gate-findings.txt"
    ).read_text()
    assert "GC-DSL-EMPTY" in findings and "20-design.md" in findings
    assert "Q-NN" in findings
    assert "FR-NN" not in findings and "BEH-NN" not in findings


def test_gate_accepts_design_heading_with_nonstandard_middot_spacing(
    tmp_path: Path, runs_root,
) -> None:
    """MINOR-2: паттерн GC-DSL-EMPTY для design синхронизирован с
    `design_guard._DESIGN_Q_RE` (`\\s*·\\s*`) — заголовок, который
    ПАРСЕР принимает (нестандартные пробелы вокруг «·»), гейт не флагает
    как пустой; старый паттерн требовал ровно один пробел с каждой
    стороны и ложно стопил бы такой, реально распознаваемый, заголовок."""

    class _Ops(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            rc = super().author(target_dir, kind, subject, bundle_dir)
            bundle = Path(target_dir) / bundle_dir
            if kind == "requirements":
                path = bundle / "10-requirements.md"
                path.write_text(
                    _DEFAULT_REQUIREMENTS_BODY
                    + "- **Q-01 · owner_role: architects · "
                    "blocking: false.** Какой протокол?\n",
                    encoding="utf-8",
                )
                return rc
            if kind != "design":
                return rc
            req_pin = blob_sha1(
                (bundle / "10-requirements.md").read_text(encoding="utf-8")
            )
            beh_pin = blob_sha1(
                (bundle / "15-behaviour-spec.md").read_text(encoding="utf-8")
            )
            (bundle / "20-design.md").write_text(
                "---\n"
                "spec_stage: design\n"
                "status: draft\n"
                "owner_role: architects\n"
                "traces_to: [requirements, behaviour-spec]\n"
                "upstream_hashes:\n"
                f'  requirements: "{req_pin}"\n'
                f'  behaviour-spec: "{beh_pin}"\n'
                "---\n"
                "####  Q-01  ·  owner_role: architects  ·  "
                "resolution: resolved\n"
                "Обоснование решения.\n",
                encoding="utf-8",
            )
            return rc

    ops = _Ops(facts=GREEN_PR_FACTS)
    state = runner.start(
        **_start_kwargs(tmp_path, "r-design-dsl-nonstd-space", ops)
    )

    findings_file = (
        runner.run_dir("r-design-dsl-nonstd-space") / "gate-findings.txt"
    )
    findings = findings_file.read_text() if findings_file.exists() else ""
    assert "GC-DSL-EMPTY" not in findings
    assert state.ops["gate-candidate"]["status"] == "completed"


def test_rollup_unstable_failure_still_refuses(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Любой FAILURE = red даже при mergeStateStatus=UNSTABLE (приёмка
    PR #99): в rulesets флота нет required-чеков, UNSTABLE означает «упало
    что угодно, хоть тесты» — поблажка мержила бы агентом красный test."""
    facts = {
        **GREEN_PR_FACTS,
        "statusCheckRollup": [
            {"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"},
        ],
        "mergeStateStatus": "UNSTABLE",
    }
    ops = FakeOps(review_exit=0, facts=facts, files=GREEN_BUNDLE_FILES)
    state = runner.start(**_start_kwargs(tmp_path, "r-unstable", ops))

    assert state.status == "stopped_merge_refused"
    assert ops.merged == []


def test_rollup_red_blocked_still_refuses(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    facts = {
        **GREEN_PR_FACTS,
        "statusCheckRollup": [{"conclusion": "FAILURE"}],
        "mergeStateStatus": "BLOCKED",
    }
    ops = FakeOps(review_exit=0, facts=facts, files=GREEN_BUNDLE_FILES)
    state = runner.start(**_start_kwargs(tmp_path, "r-blocked", ops))

    assert state.status == "stopped_merge_refused"
    assert ops.merged == []


def test_resume_merge_refused_after_human_merge_runs_s8(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Reconciliation refuse→merged (боевой прогон kapelle#51): человек
    смержил отказанный PR — resume фиксирует мерж и гонит S8."""
    facts = {
        **GREEN_PR_FACTS,
        "statusCheckRollup": [{"conclusion": "FAILURE"}],
        "mergeStateStatus": "BLOCKED",
    }
    ops = FakeOps(
        review_exit=0, facts=facts, files=GREEN_BUNDLE_FILES, s8_exit=0,
    )
    run_id = "r-refused-merged"
    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))
    assert state.status == "stopped_merge_refused"

    ops.facts = {**ops.facts, "state": "MERGED"}
    result = runner.resume(run_id, ops)

    assert result.ops["merge"] == {"status": "completed", "merged": True}
    assert result.ops["gate-authoritative"]["status"] == "completed"
    assert result.status == "completed"


def test_gate_unpinned_draft_edge_stops_locally(
    tmp_path: Path, runs_root,
) -> None:
    """Гард GC-UNPINNED(prospective) поверх CLI (приёмка PR #101, major):
    stale-каскад gate-check живёт только на approved — draft-узел с
    объявленным ребром без пина upstream_hashes обязан стопить S4 локально,
    даже когда CLI вернул 0."""

    class UnpinnedAuthorOps(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            rc = super().author(target_dir, kind, subject, bundle_dir)
            if kind == "behaviour-spec":
                path = Path(target_dir) / bundle_dir / "15-behaviour-spec.md"
                path.write_text(
                    "---\n"
                    "spec_stage: behaviour-spec\n"
                    "status: draft\n"
                    "traces_to:\n  - requirements\n"
                    "---\n"
                    "#### BEH-01: x\n`traces: [FR-01]`\n"
                    "- **checked_by**: x\n"
                )
            return rc

    ops = UnpinnedAuthorOps(facts=GREEN_PR_FACTS)  # CLI-гейт (FakeOps) даёт 0
    state = runner.start(**_start_kwargs(tmp_path, "r-unpinned", ops))

    assert state.status == "stopped_gate"
    findings = (runner.run_dir("r-unpinned") / "gate-findings.txt").read_text()
    assert "GC-UNPINNED" in findings and "requirements" in findings
    assert "push" not in state.ops


def test_gate_pinned_draft_edge_passes_local_guard(
    tmp_path: Path, runs_root,
) -> None:
    class PinnedAuthorOps(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            rc = super().author(target_dir, kind, subject, bundle_dir)
            if kind == "behaviour-spec":
                path = Path(target_dir) / bundle_dir / "15-behaviour-spec.md"
                path.write_text(
                    "---\n"
                    "spec_stage: behaviour-spec\n"
                    "status: draft\n"
                    "traces_to:\n  - requirements\n"
                    "upstream_hashes:\n"
                    '  requirements: "'
                    + blob_sha1("#### FR-01: x\n**Priority**: Must\n")
                    + '"\n'
                    "---\n"
                    "#### BEH-01: x\n`traces: [FR-01]`\n"
                    "- **checked_by**: x\n"
                )
            return rc

    ops = PinnedAuthorOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES,
        s8_exit=0,
    )
    state = runner.start(**_start_kwargs(tmp_path, "r-pinned", ops))
    assert state.ops["gate-candidate"]["status"] == "completed"


def test_gate_stale_draft_pin_stops_locally(tmp_path: Path, runs_root) -> None:
    """GC-STALE(prospective) поверх CLI (приёмка PR #101, круг 2): пин
    присутствует, но НЕ равен blob-хешу upstream в worktree — стоп, не
    fail-open по одному лишь наличию 40 hex."""

    class StalePinOps(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            rc = super().author(target_dir, kind, subject, bundle_dir)
            if kind == "behaviour-spec":
                path = Path(target_dir) / bundle_dir / "15-behaviour-spec.md"
                path.write_text(
                    "---\n"
                    "spec_stage: behaviour-spec\n"
                    "status: draft\n"
                    "traces_to:\n  - requirements\n"
                    "upstream_hashes:\n"
                    '  requirements: "' + "a" * 40 + '"\n'
                    "---\n"
                    "#### BEH-01: x\n`traces: [FR-01]`\n"
                    "- **checked_by**: x\n"
                )
            return rc

    ops = StalePinOps(facts=GREEN_PR_FACTS)
    state = runner.start(**_start_kwargs(tmp_path, "r-stale-pin", ops))

    assert state.status == "stopped_gate"
    findings = (runner.run_dir("r-stale-pin") / "gate-findings.txt").read_text()
    assert "GC-STALE" in findings and "не совпадает" in findings
    assert "push" not in state.ops


def test_gate_inline_upstream_hashes_form_passes(
    tmp_path: Path, runs_root,
) -> None:
    """Inline-форма `upstream_hashes: {requirements: "<hash>"}` — ровно та,
    что предписывает авторский промпт (приёмка PR #101, круг 3) — обязана
    проходить локальный гард наравне с блочной."""

    class InlinePinOps(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            rc = super().author(target_dir, kind, subject, bundle_dir)
            if kind == "behaviour-spec":
                pin = blob_sha1("#### FR-01: x\n**Priority**: Must\n")
                path = Path(target_dir) / bundle_dir / "15-behaviour-spec.md"
                path.write_text(
                    "---\n"
                    "spec_stage: behaviour-spec\n"
                    "status: draft\n"
                    "traces_to: [requirements]\n"
                    'upstream_hashes: {requirements: "' + pin + '"}\n'
                    "---\n"
                    "#### BEH-01: x\n`traces: [FR-01]`\n"
                    "- **checked_by**: x\n"
                )
            return rc

    ops = InlinePinOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES,
        s8_exit=0,
    )
    state = runner.start(**_start_kwargs(tmp_path, "r-inline-pin", ops))
    assert state.ops["gate-candidate"]["status"] == "completed"


def test_gate_foreign_toplevel_key_is_not_a_pin(
    tmp_path: Path, runs_root,
) -> None:
    """Приёмка PR #101, круг 4: пустой `upstream_hashes: {}` + посторонний
    верхнеуровневый ключ `requirements: <верный hash>` ниже — это НЕ пин;
    обязан быть GC-UNPINNED, не fail-open."""

    class ForeignKeyOps(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            rc = super().author(target_dir, kind, subject, bundle_dir)
            if kind == "behaviour-spec":
                real = blob_sha1("#### FR-01: x\n**Priority**: Must\n")
                path = Path(target_dir) / bundle_dir / "15-behaviour-spec.md"
                path.write_text(
                    "---\n"
                    "spec_stage: behaviour-spec\n"
                    "status: draft\n"
                    "traces_to: [requirements]\n"
                    "upstream_hashes: {}\n"
                    'requirements: "' + real + '"\n'
                    "---\n"
                    "#### BEH-01: x\n`traces: [FR-01]`\n"
                    "- **checked_by**: x\n"
                )
            return rc

    ops = ForeignKeyOps(facts=GREEN_PR_FACTS)
    state = runner.start(**_start_kwargs(tmp_path, "r-foreign-key", ops))

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-foreign-key") / "gate-findings.txt"
    ).read_text()
    assert "GC-UNPINNED" in findings


_FM_BODY = """## Ревью Codex — независимый чек

### [major] governance/foo.py отсутствует — `governance/foo.py:0`
- Тип: `file-missing` — находка утверждает, что файла нет; проверяется по дереву
- Сценарий: x
- confidence: high → БЛОКИРУЕТ

### [minor] стилистика — `README.md:3`
- Сценарий: y
- confidence: high → не блокирует по severity
"""

_MIXED_BODY = """## Ревью

### [major] настоящая дыра — `governance/bar.py:10`
- Сценарий: z
- confidence: high → БЛОКИРУЕТ

### [major] файла нет — `governance/foo.py:0`
- Тип: `file-missing` — находка утверждает, что файла нет
- confidence: high → БЛОКИРУЕТ
"""


def test_file_missing_parser() -> None:
    assert runner._file_missing_refute_candidates(_FM_BODY) == [
        "governance/foo.py"
    ]
    assert runner._file_missing_refute_candidates(_MIXED_BODY) is None
    assert runner._file_missing_refute_candidates("### [minor] x — `a:1`\n") is None


def test_review_auto_refutes_file_missing(
    tmp_path: Path, runs_root,
) -> None:
    """Спека §7: все блокирующие — file-missing, файлы существуют →
    комментарий с evidence, пере-прогон --fresh, зелёный — прогон едет дальше."""
    ops = FakeOps(
        review_exit=1, review_fresh_exit=0, review_body=_FM_BODY,
        existing_files={"governance/foo.py"},
        facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=0,
    )
    state = runner.start(**_start_kwargs(tmp_path, "r-refute-ok", ops))

    assert state.ops["review-refute"]["status"] == "completed"
    assert state.ops["review"] == {"status": "completed", "exit": 0}
    assert any(c[0] == "review_fresh" for c in ops.calls)
    assert any("Авто-опровержение" in c for c in ops.comments)
    assert state.status == "completed"


def test_review_mixed_verdict_goes_to_human(
    tmp_path: Path, runs_root,
) -> None:
    ops = FakeOps(
        review_exit=1, review_body=_MIXED_BODY,
        existing_files={"governance/foo.py", "governance/bar.py"},
        facts=GREEN_PR_FACTS,
    )
    state = runner.start(**_start_kwargs(tmp_path, "r-refute-mixed", ops))

    assert state.status == "stopped_review"
    assert not any(c[0] == "review_fresh" for c in ops.calls)
    assert "review-refute" not in state.ops


def test_review_truly_missing_file_goes_to_human(
    tmp_path: Path, runs_root,
) -> None:
    ops = FakeOps(review_exit=1, review_body=_FM_BODY, facts=GREEN_PR_FACTS)
    state = runner.start(**_start_kwargs(tmp_path, "r-refute-real", ops))

    assert state.status == "stopped_review"
    assert not any(c[0] == "review_fresh" for c in ops.calls)


def test_review_refute_is_single_attempt(
    tmp_path: Path, runs_root,
) -> None:
    """Fresh-прогон снова красный → стоп; вторая авто-попытка не делается."""
    ops = FakeOps(
        review_exit=1, review_fresh_exit=1, review_body=_FM_BODY,
        existing_files={"governance/foo.py"},
        facts=GREEN_PR_FACTS,
    )
    state = runner.start(**_start_kwargs(tmp_path, "r-refute-again", ops))

    assert state.status == "stopped_review"
    assert [c[0] for c in ops.calls].count("review_fresh") == 1
    assert state.ops["review-refute"]["status"] == "completed"


def test_parser_defect_mentioning_file_missing_literal_is_not_refutable() -> None:
    """Приёмка PR #102: defect-находка, чей текст лишь УПОМИНАЕТ литерал
    `file-missing` (без kindline рендера), не классифицируется как
    file-missing — авто-опровержение неприменимо."""
    body = (
        "### [major] дефект обработки `file-missing` — `governance/x.py:10`\n"
        "- Сценарий: обработчик типа `file-missing` теряет путь\n"
        "- confidence: high → БЛОКИРУЕТ\n"
    )
    assert runner._file_missing_refute_candidates(body) is None


def test_review_fresh_instrument_failure_routed_honestly(
    tmp_path: Path, runs_root,
) -> None:
    """Приёмка PR #102, minor: fresh exit 2/3 — «прибор не отработал»,
    не «сохранившиеся находки»."""
    ops = FakeOps(
        review_exit=1, review_fresh_exit=2, review_body=_FM_BODY,
        existing_files={"governance/foo.py"},
        facts=GREEN_PR_FACTS,
    )
    state = runner.start(**_start_kwargs(tmp_path, "r-refute-instr", ops))

    assert state.status == "stopped_review"
    assert any("прибор не отработал" in c for c in ops.comments)


def test_barrier_stop_is_named_and_persistent(tmp_path: Path, runs_root) -> None:
    """devtools#258: код 6 — барьер, а не сбой прибора.

    Под кодом 2 контур постил в PR «прибор не отработал» — ложную причину:
    прогон возможен, но требует решения владельца.

    Второе утверждение — ПЕРСИСТЕНТНОСТЬ стопа: неопознанный код падал бы в
    ветку «голова уехала», а она оставляет статус `running`, то есть шаг
    переигрывается на следующем заходе, и барьерный стоп человеку не
    виден. Заодно эта ветка не трогает op'ы — но гарантией сохранения
    контентного гейта это НЕ является, и раньше докстринг утверждал
    обратное (находка ревью devtools#275): единственный путь продолжения,
    `resume()`, безусловно попает `_BUNDLE_EDIT_RESET_OPS`. Проверка ниже
    фиксирует состояние в момент стопа, а не после resume. Довести
    сохранение до конца — devtools#276.
    """
    ops = FakeOps(
        review_exit=ops_mod.REVIEW_BARRIER_EXIT, facts=GREEN_PR_FACTS,
        files=GREEN_BUNDLE_FILES,
    )
    state = runner.start(**_start_kwargs(tmp_path, "r-budget", ops))

    assert state.status == "stopped_review"
    assert any(ops_mod.REVIEW_BARRIER_STOP in c for c in ops.comments)
    assert not any("прибор не отработал" in c for c in ops.comments)
    # Совет «повторите обычный запуск» помочь не может: журнал ключуется по
    # slug#pr, новая голова круга не открывает.
    assert not any("повторите обычный" in c for c in ops.comments)
    # Находка ревью PR #275 (major): код 6 несёт ДВЕ причины — бюджет и
    # stop rule, — и комментарий в PR не вправе утверждать одну из них как
    # факт. Обе обязаны быть названы.
    assert any("stop rule" in c for c in ops.comments)
    # Эта ветка их не трогает — в отличие от reset-ветки, с которой её и
    # надо различать. Что `resume()` их всё равно попает — сказано в
    # докстринге; здесь проверяется момент стопа.
    for op in ("gate-candidate", "push", "ready"):
        assert op in state.ops, f"{op} сброшен барьерным стопом"


def test_barrier_stop_on_fresh_path_is_named_too(
    tmp_path: Path, runs_root,
) -> None:
    """Тот же барьер на пути авто-опровержения (`review_fresh`).

    Две точки маршрутизации кодов ревью живут порознь (первичный прогон и
    fresh после опровержения) — правка одной оставила бы вторую врущей.
    """
    ops = FakeOps(
        review_exit=1, review_fresh_exit=ops_mod.REVIEW_BARRIER_EXIT,
        review_body=_FM_BODY, existing_files={"governance/foo.py"},
        facts=GREEN_PR_FACTS,
    )
    state = runner.start(**_start_kwargs(tmp_path, "r-budget-fresh", ops))

    assert state.status == "stopped_review"
    assert any(ops_mod.REVIEW_BARRIER_STOP in c for c in ops.comments)
    assert not any("прибор не отработал" in c for c in ops.comments)


def test_head_move_after_refute_restores_attempt(
    tmp_path: Path, runs_root,
) -> None:
    """Приёмка PR #102, круг 2: fresh exit 4 (голова уехала) сбрасывает и
    review-refute — новый цикл получает свежую авто-попытку."""
    ops = FakeOps(
        review_exit=1, review_fresh_exit=4, review_body=_FM_BODY,
        existing_files={"governance/foo.py"},
        facts=GREEN_PR_FACTS,
    )
    state = runner.start(**_start_kwargs(tmp_path, "r-refute-head", ops))

    assert "review-refute" not in state.ops
    assert "review" not in state.ops  # весь цикл переигрывается


def test_parser_takes_path_from_header_tail_not_forged_title() -> None:
    """Приёмка PR #102, круг 3: title с поддельным фрагментом «— `x:0`» не
    подменяет путь — берётся хвост заголовка (настоящее поле file рендера);
    небезопасная форма пути дисквалифицирует кандидата."""
    forged = (
        "### [major] ошибка — `README.md:0` и прочее — `governance/miss.py:0`\n"
        "- Тип: `file-missing` — находка утверждает, что файла нет\n"
        "- confidence: high → БЛОКИРУЕТ\n"
    )
    assert runner._file_missing_refute_candidates(forged) == [
        "governance/miss.py"
    ]
    traversal = (
        "### [major] нет файла — `../outside.py:0`\n"
        "- Тип: `file-missing` — x\n"
        "- confidence: high → БЛОКИРУЕТ\n"
    )
    assert runner._file_missing_refute_candidates(traversal) is None


# --- Task 4: S4-гарды design (отсутствие, UNPINNED/STALE рёбер, покрытие Q) --


def test_gate_stops_when_design_node_missing_from_bundle(
    tmp_path: Path, runs_root,
) -> None:
    """Гард отсутствия design (спека Task 4): required-узел design профиля
    team-exp, но файла 20-design.md в бандле нет ⇒ `stopped_gate` локально
    — ДО цикла рёбер, даже когда FakeOps `gate_check_candidate` вернула бы
    0 молча.

    MINOR-1 финального ревью: `design_required` теперь читает ФАКТИЧЕСКИЙ
    файл target-профиля (`target_profile_declares`), не сравнивает имя
    профиля со строкой — без материализации `profiles/team-exp.yaml` в
    `target_dir` гард молча не сработал бы (fail-closed False у
    `target_profile_declares` на отсутствующем файле)."""
    ops = FakeOps(facts=GREEN_PR_FACTS)
    run_id = "r-design-absent"
    target_dir = tmp_path / f"target-{run_id}"
    target_dir.mkdir()
    profile_dir = target_dir / "profiles"
    profile_dir.mkdir(parents=True)
    (profile_dir / "team-exp.yaml").write_text(
        _TEAM_EXP_PROFILE_TEXT, encoding="utf-8"
    )
    bundle_dir = target_dir / BUNDLE_DIR
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "00-charter.md").write_text("# charter\n", encoding="utf-8")
    (bundle_dir / "10-requirements.md").write_text(
        _DEFAULT_REQUIREMENTS_BODY, encoding="utf-8"
    )
    (bundle_dir / "15-behaviour-spec.md").write_text(
        _DEFAULT_BEHAVIOUR_BODY, encoding="utf-8"
    )
    state = rs.new_run(
        subject="s", repo="alpha", repo_slug="owner/alpha", ws_id="WS-1",
        target_dir=str(target_dir), bundle_dir=BUNDLE_DIR,
        profile="profiles/team-exp.yaml", run_id=run_id,
    )
    state.branch = "spec/WS-1-behaviour"
    state.ops = {
        "branch": {"status": "completed"},
        "author-charter": {"status": "completed", "skipped": True},
        "author-requirements": {"status": "completed", "skipped": True},
        "author-behaviour": {"status": "completed", "skipped": True},
        # design намеренно НЕ авторен и не пропущен — файла нет вовсе.
        "author-design": {"status": "completed", "skipped": True},
        "commit": {"status": "completed"},
    }
    rs.save(state)

    result = runner.advance(state, ops)

    assert result.status == "stopped_gate"
    findings = (runner.run_dir(run_id) / "gate-findings.txt").read_text()
    assert "GC-COMPLETENESS(design)" in findings
    # Гард отсутствия стоит ДО цикла рёбер: находок по рёбрам design
    # (UNPINNED/STALE) в том же файле нет — иначе завтрашняя правка
    # порядка кода тихо перепутала бы, что стопнуло прогон.
    assert "GC-UNPINNED" not in findings
    assert "GC-STALE" not in findings
    assert "push" not in result.ops


def _design_pin_ops(bad_edge: str) -> type:
    """Фабрика Ops-подкласса: design запинован верно ВЕЗДЕ, кроме
    `bad_edge` (`"requirements"`/`"behaviour-spec"`) — там пин вовсе не
    записан (GC-UNPINNED-сценарий)."""

    class _Ops(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            rc = super().author(target_dir, kind, subject, bundle_dir)
            if kind != "design":
                return rc
            bundle = Path(target_dir) / bundle_dir
            pins = {
                "requirements": blob_sha1(
                    (bundle / "10-requirements.md").read_text(encoding="utf-8")
                ),
                "behaviour-spec": blob_sha1(
                    (bundle / "15-behaviour-spec.md").read_text(encoding="utf-8")
                ),
            }
            del pins[bad_edge]
            hashes_block = "".join(
                f'  {upstream}: "{pin}"\n' for upstream, pin in pins.items()
            )
            path = bundle / "20-design.md"
            path.write_text(
                "---\n"
                "spec_stage: design\n"
                "status: draft\n"
                "owner_role: architects\n"
                "traces_to: [requirements, behaviour-spec]\n"
                "upstream_hashes:\n"
                f"{hashes_block}"
                "---\n"
                "Открытых архитектурных вопросов нет (входной набор пуст)\n",
                encoding="utf-8",
            )
            return rc

    return _Ops


def test_gate_design_missing_requirements_pin_is_unpinned(
    tmp_path: Path, runs_root,
) -> None:
    """GC-UNPINNED(prospective) на ребре design→requirements: пин
    behaviour-spec корректен, requirements — не запинен вовсе."""
    ops = _design_pin_ops("requirements")(facts=GREEN_PR_FACTS)
    state = runner.start(**_start_kwargs(tmp_path, "r-design-unpinned-req", ops))

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-design-unpinned-req") / "gate-findings.txt"
    ).read_text()
    assert "GC-UNPINNED" in findings and "requirements" in findings
    assert "push" not in state.ops


def test_gate_design_missing_behaviour_pin_is_unpinned(
    tmp_path: Path, runs_root,
) -> None:
    """GC-UNPINNED(prospective) на ребре design→behaviour-spec: пин
    requirements корректен, behaviour-spec — не запинен вовсе."""
    ops = _design_pin_ops("behaviour-spec")(facts=GREEN_PR_FACTS)
    state = runner.start(**_start_kwargs(tmp_path, "r-design-unpinned-beh", ops))

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-design-unpinned-beh") / "gate-findings.txt"
    ).read_text()
    assert "GC-UNPINNED" in findings and "behaviour-spec" in findings
    assert "push" not in state.ops


def _design_undeclared_edge_ops(missing_edge: str) -> type:
    """Фабрика Ops-подкласса: design объявляет `traces_to` только для
    ОДНОГО ребра — `missing_edge` ("requirements"/"behaviour-spec") в
    `traces_to` вовсе нет (MAJOR-1: раньше необъявленное ребро тихо
    пропускалось `continue` внутри цикла гарда рёбер, вместо стопа S4
    prospective-находкой)."""

    class _Ops(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            rc = super().author(target_dir, kind, subject, bundle_dir)
            if kind != "design":
                return rc
            bundle = Path(target_dir) / bundle_dir
            declared = [
                e for e in ("requirements", "behaviour-spec")
                if e != missing_edge
            ]
            pins = {
                "requirements": blob_sha1(
                    (bundle / "10-requirements.md").read_text(encoding="utf-8")
                ),
                "behaviour-spec": blob_sha1(
                    (bundle / "15-behaviour-spec.md").read_text(encoding="utf-8")
                ),
            }
            hashes_block = "".join(
                f'  {e}: "{pins[e]}"\n' for e in declared
            )
            path = bundle / "20-design.md"
            path.write_text(
                "---\n"
                "spec_stage: design\n"
                "status: draft\n"
                "owner_role: architects\n"
                f"traces_to: [{', '.join(declared)}]\n"
                "upstream_hashes:\n"
                f"{hashes_block}"
                "---\n"
                "Открытых архитектурных вопросов нет (входной набор пуст)\n",
                encoding="utf-8",
            )
            return rc

    return _Ops


def test_gate_design_undeclared_requirements_edge_stops(
    tmp_path: Path, runs_root,
) -> None:
    """MAJOR-1: design `traces_to` несёт только `behaviour-spec` — ребро
    requirements не объявлено ВООБЩЕ (не «не запинено», а отсутствует в
    traces_to) — S4 обязан стопить prospective-находкой, не молча
    пропускать необъявленное required-ребро."""
    ops = _design_undeclared_edge_ops("requirements")(facts=GREEN_PR_FACTS)
    state = runner.start(
        **_start_kwargs(tmp_path, "r-design-undeclared-req", ops)
    )

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-design-undeclared-req") / "gate-findings.txt"
    ).read_text()
    assert "GC-UNPINNED" in findings and "requirements" in findings
    assert "не объявлено в traces_to" in findings
    assert "push" not in state.ops


def test_gate_design_undeclared_behaviour_edge_stops(
    tmp_path: Path, runs_root,
) -> None:
    """MAJOR-1: design `traces_to` несёт только `requirements` — ребро
    behaviour-spec не объявлено ВООБЩЕ — S4 обязан стопить, не
    пропускать."""
    ops = _design_undeclared_edge_ops("behaviour-spec")(facts=GREEN_PR_FACTS)
    state = runner.start(
        **_start_kwargs(tmp_path, "r-design-undeclared-beh", ops)
    )

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-design-undeclared-beh") / "gate-findings.txt"
    ).read_text()
    assert "GC-UNPINNED" in findings and "behaviour-spec" in findings
    assert "не объявлено в traces_to" in findings
    assert "push" not in state.ops


def test_gate_edges_derived_from_bundle_dag() -> None:
    """MINOR-3: `runner._GATE_EDGES` не расходится с `task_bridge._BUNDLE_DAG`
    — рёбра S4 выводятся из ОДНОГО источника (не трёх несинхронизированных
    копий: этот кортеж, `_BUNDLE_DAG`, `profiles/team-exp.yaml`).
    node-id ↔ имя файла — через `task_bridge._node_id`; required — те
    рёбра, чей target-узел design, acceptance ИЛИ decomposition (MAJOR-1;
    Task 6/7 плана acceptance-node — рёбра acceptance и
    decomposition→acceptance обязательные, понижать флаг нельзя: fail-open
    на необъявленном ребре).

    Task 7 плана acceptance-node довозит узел acceptance в
    `task_bridge._BUNDLE_DAG` (Step 3 этой задачи) и снимает xfail-отметку
    Task 6."""
    filename_by_node_id = {
        task_bridge._node_id(fname): fname
        for fname, _upstreams in task_bridge._BUNDLE_DAG
    }
    expected = tuple(
        (
            fname,
            upstream,
            filename_by_node_id[upstream],
            task_bridge._node_id(fname)
            in {"design", "decomposition", "acceptance"},
        )
        for fname, upstreams in task_bridge._BUNDLE_DAG
        for upstream in upstreams
    )
    assert runner._GATE_EDGES == expected


def _design_stale_ops(stale_edge: str) -> type:
    """Фабрика Ops-подкласса: design запинован верно ВЕЗДЕ, кроме
    `stale_edge` — там пин синтаксически валиден (40 hex), но неверен
    (GC-STALE-сценарий)."""

    class _Ops(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            rc = super().author(target_dir, kind, subject, bundle_dir)
            if kind != "design":
                return rc
            bundle = Path(target_dir) / bundle_dir
            pins = {
                "requirements": blob_sha1(
                    (bundle / "10-requirements.md").read_text(encoding="utf-8")
                ),
                "behaviour-spec": blob_sha1(
                    (bundle / "15-behaviour-spec.md").read_text(encoding="utf-8")
                ),
            }
            pins[stale_edge] = "a" * 40
            hashes_block = "".join(
                f'  {upstream}: "{pin}"\n' for upstream, pin in pins.items()
            )
            path = bundle / "20-design.md"
            path.write_text(
                "---\n"
                "spec_stage: design\n"
                "status: draft\n"
                "owner_role: architects\n"
                "traces_to: [requirements, behaviour-spec]\n"
                "upstream_hashes:\n"
                f"{hashes_block}"
                "---\n"
                "Открытых архитектурных вопросов нет (входной набор пуст)\n",
                encoding="utf-8",
            )
            return rc

    return _Ops


def test_gate_design_stale_requirements_pin(tmp_path: Path, runs_root) -> None:
    """GC-STALE(prospective) на ребре design→requirements: пин
    синтаксически валиден, но не совпадает с blob-хешем в worktree."""
    ops = _design_stale_ops("requirements")(facts=GREEN_PR_FACTS)
    state = runner.start(**_start_kwargs(tmp_path, "r-design-stale-req", ops))

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-design-stale-req") / "gate-findings.txt"
    ).read_text()
    assert "GC-STALE" in findings and "requirements" in findings
    assert "push" not in state.ops


def test_gate_design_stale_behaviour_pin(tmp_path: Path, runs_root) -> None:
    """GC-STALE(prospective) на ребре design→behaviour-spec: пин
    синтаксически валиден, но не совпадает с blob-хешем в worktree."""
    ops = _design_stale_ops("behaviour-spec")(facts=GREEN_PR_FACTS)
    state = runner.start(**_start_kwargs(tmp_path, "r-design-stale-beh", ops))

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-design-stale-beh") / "gate-findings.txt"
    ).read_text()
    assert "GC-STALE" in findings and "behaviour-spec" in findings
    assert "push" not in state.ops


def test_gate_stops_on_uncovered_architect_question(
    tmp_path: Path, runs_root,
) -> None:
    """GC-DESIGN-COVERAGE (спека Task 4): architects-Q из requirements без
    резолюции в design — стоп, отдельно от DSL-EMPTY/UNPINNED/STALE (оба
    пина design корректны, design DSL-непуст своим Q-99)."""
    q03_requirements = (
        _DEFAULT_REQUIREMENTS_BODY
        + "- **Q-03 · owner_role: architects · blocking: false.** Как?\n"
    )

    class UncoveredQOps(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            self.calls.append(("author", kind))
            self.authored.append(kind)
            bundle = Path(target_dir) / bundle_dir
            bundle.mkdir(parents=True, exist_ok=True)
            if kind == "requirements":
                (bundle / "10-requirements.md").write_text(
                    q03_requirements, encoding="utf-8"
                )
                return 0
            if kind == "design":
                req_pin = blob_sha1(q03_requirements)
                beh_pin = blob_sha1(_DEFAULT_BEHAVIOUR_BODY)
                (bundle / "20-design.md").write_text(
                    "---\n"
                    "spec_stage: design\n"
                    "status: draft\n"
                    "owner_role: architects\n"
                    "traces_to: [requirements, behaviour-spec]\n"
                    "upstream_hashes:\n"
                    f'  requirements: "{req_pin}"\n'
                    f'  behaviour-spec: "{beh_pin}"\n'
                    "---\n"
                    "#### Q-99 · owner_role: architects · resolution: "
                    "resolved\nне то\n",
                    encoding="utf-8",
                )
                return 0
            return super().author(target_dir, kind, subject, bundle_dir)

    ops = UncoveredQOps(facts=GREEN_PR_FACTS)
    state = runner.start(**_start_kwargs(tmp_path, "r-design-uncovered-q", ops))

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-design-uncovered-q") / "gate-findings.txt"
    ).read_text()
    assert "GC-DESIGN-COVERAGE" in findings and "Q-03" in findings
    assert "push" not in state.ops


# --- Task 8: preflight профиля target — design-узел обязателен -----------

_STALE_TEAM_EXP_PROFILE = """\
profile: team-exp
artifacts:
  - {id: charter, template: charter.md, owner_role: product}
  - id: requirements
    template: requirements.md
    owner_role: product
    upstream: [charter]
  - id: behaviour-spec
    template: behaviour-spec.md
    owner_role: product
    upstream: [requirements]
  - id: tasks
    owner_role: stream-owner
    upstream: [behaviour-spec]
    delegate: spec-runner
"""


def _write_stale_profile(target_dir: Path) -> Path:
    """Старая копия profiles/team-exp.yaml (3 узла, БЕЗ design) — как
    несут соседние репо до раскатки design-узла (мотивация Task 8)."""
    profile_path = target_dir / "profiles" / "team-exp.yaml"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(_STALE_TEAM_EXP_PROFILE, encoding="utf-8")
    return profile_path


# 4-узловая копия profiles/team-exp.yaml (charter..design), БЕЗ decomposition
# — как несут соседние репо до раскатки decomposition-узла (Task 5).
_FOUR_NODE_TEAM_EXP_PROFILE = """\
profile: team-exp
solo_auto_approve: true
artifacts:
  - {id: charter, template: charter.md, owner_role: product, upstream: []}
  - id: requirements
    template: requirements.md
    owner_role: product
    upstream: [charter]
  - id: behaviour-spec
    template: behaviour-spec.md
    owner_role: product
    upstream: [requirements]
  - {id: design, template: design.md, owner_role: architects,
     upstream: [requirements, behaviour-spec]}
  - id: tasks
    owner_role: stream-owner
    upstream: [design]
    delegate: spec-runner
"""


def _write_four_node_profile(target_dir: Path) -> Path:
    """Копия `_write_stale_profile` с узлом design, но БЕЗ decomposition —
    состояние соседних репо до раскатки decomposition-узла (Task 5,
    preflight обязан охранять оба узла)."""
    profile_path = target_dir / "profiles" / "team-exp.yaml"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(_FOUR_NODE_TEAM_EXP_PROFILE, encoding="utf-8")
    return profile_path


# 5-узловая копия profiles/team-exp.yaml (charter..design + decomposition,
# upstream: [design]), БЕЗ acceptance — как несут соседние репо до раскатки
# acceptance-узла (Task 5 плана acceptance-node).
_FIVE_NODE_TEAM_EXP_PROFILE = """\
profile: team-exp
solo_auto_approve: true
artifacts:
  - {id: charter, template: charter.md, owner_role: product, upstream: []}
  - id: requirements
    template: requirements.md
    owner_role: product
    upstream: [charter]
  - id: behaviour-spec
    template: behaviour-spec.md
    owner_role: product
    upstream: [requirements]
  - {id: design, template: design.md, owner_role: architects,
     upstream: [requirements, behaviour-spec]}
  - id: decomposition
    template: decomposition.md
    owner_role: tech-lead
    upstream: [design]
  - id: tasks
    owner_role: stream-owner
    upstream: [decomposition]
    delegate: spec-runner
"""


def _write_five_node_profile(target_dir: Path) -> Path:
    """Копия `_write_four_node_profile` + узел decomposition (upstream
    [design]), БЕЗ acceptance — состояние соседних репо до раскатки
    acceptance-узла (Task 5, preflight обязан охранять все три узла)."""
    profile_path = target_dir / "profiles" / "team-exp.yaml"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(_FIVE_NODE_TEAM_EXP_PROFILE, encoding="utf-8")
    return profile_path


# 5-узловая копия profiles/team-exp.yaml (charter..design + acceptance,
# upstream: [requirements, behaviour-spec]), БЕЗ decomposition — зеркало
# `_FIVE_NODE_TEAM_EXP_PROFILE`: та несёт decomposition и не несёт
# acceptance, эта — наоборот (Task 7 плана acceptance-node: preflight-
# порядок design→acceptance→decomposition обязан ловить недостающий
# decomposition именно на decomposition, не на более раннем acceptance).
_FIVE_NODE_WITH_ACCEPTANCE_PROFILE = """\
profile: team-exp
solo_auto_approve: true
artifacts:
  - {id: charter, template: charter.md, owner_role: product, upstream: []}
  - id: requirements
    template: requirements.md
    owner_role: product
    upstream: [charter]
  - id: behaviour-spec
    template: behaviour-spec.md
    owner_role: product
    upstream: [requirements]
  - {id: design, template: design.md, owner_role: architects,
     upstream: [requirements, behaviour-spec]}
  - {id: acceptance, template: acceptance.md, owner_role: qa,
     upstream: [requirements, behaviour-spec]}
  - id: tasks
    owner_role: stream-owner
    upstream: [design, acceptance]
    delegate: spec-runner
"""


def _write_five_node_with_acceptance_profile(target_dir: Path) -> Path:
    """`_write_four_node_profile` + узел acceptance, БЕЗ decomposition —
    состояние соседних репо между раскаткой acceptance- и
    decomposition-узла (Task 7 плана acceptance-node)."""
    profile_path = target_dir / "profiles" / "team-exp.yaml"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(
        _FIVE_NODE_WITH_ACCEPTANCE_PROFILE, encoding="utf-8"
    )
    return profile_path


def test_start_stops_preflight_when_target_profile_lacks_design(
    tmp_path: Path, runs_root,
) -> None:
    """Step 1(а): target несёт СТАРУЮ копию profiles/team-exp.yaml без
    узла design ⇒ `start` останавливается статусом `stopped_preflight`
    (до фактического авторинга design), сообщение несёт процедуру."""
    ops = FakeOps(facts=GREEN_PR_FACTS)
    run_id = "r-preflight-missing-design"
    kwargs = _start_kwargs(tmp_path, run_id, ops)
    _write_stale_profile(Path(kwargs["target_dir"]))

    state = runner.start(**kwargs)

    assert state.status == "stopped_preflight"
    # Преflight стоит ДО S2 (план Task 8 Step 2): ни одного оплаченного
    # вызова авторинга, не только «design не авторился» (minor PR-ревью
    # #145 — старый ассерт разрешал три вызова до останова).
    assert ops.authored == []
    assert "gate-candidate" not in state.ops
    assert "push" not in state.ops


def test_start_stops_preflight_when_target_profile_lacks_decomposition(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """target с 5-узловым профилем (design И acceptance есть,
    decomposition нет) ⇒ stopped_preflight ДО единого вызова авторинга —
    останов должен произойти именно на decomposition (порядок preflight
    "design", "acceptance", "decomposition"), не раньше на acceptance."""
    ops = FakeOps(facts=GREEN_PR_FACTS)
    kwargs = _start_kwargs(tmp_path, "r-preflight-no-decomp", ops)
    _write_five_node_with_acceptance_profile(Path(kwargs["target_dir"]))

    state = runner.start(**kwargs)

    assert state.status == "stopped_preflight"
    assert ops.authored == []


def test_start_stops_preflight_when_target_profile_lacks_acceptance(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """target с 5-узловым профилем (design+decomposition есть, acceptance
    нет) ⇒ stopped_preflight ДО единого вызова авторинга (Task 5 плана
    acceptance-node, канон плана decomposition)."""
    ops = FakeOps(facts=GREEN_PR_FACTS)
    kwargs = _start_kwargs(tmp_path, "r-preflight-no-acc", ops)
    _write_five_node_profile(Path(kwargs["target_dir"]))

    state = runner.start(**kwargs)

    assert state.status == "stopped_preflight"
    assert ops.authored == []


def test_start_preflight_silent_on_six_node_profile(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Step 1(б): target с актуальным 6-узловым профилем (материализован
    `_start_kwargs`, T1; acceptance добавлен Task 1 плана acceptance-node)
    ⇒ preflight молчит, прогон доходит до мержа как прежде — регрессия
    отсутствует."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)

    state = runner.start(**_start_kwargs(tmp_path, "r-preflight-ok", ops))

    assert state.status != "stopped_preflight"
    assert state.ops["merge"]["status"] == "completed"


def test_start_stops_preflight_for_non_team_exp_profile_lacking_design(
    tmp_path: Path, runs_root,
) -> None:
    """Фикс-раунд ревью, major #1: хардкод имени профиля снят —
    `_step_authoring` решает «авторить ли design» data-driven для ЛЮБОГО
    профиля. `profiles/mini.yaml` (фикстура `make_profile`, узла design не
    несёт) через ПОЛНЫЙ `start()` (не ручной bypass state.ops) стопится
    `stopped_preflight` — не молчаливым `author-design`, не молчаливым
    skip. Конвейер сейчас не поддерживает профили «сознательно без
    design»: единственный статус для такого расхождения — останов."""
    ops = FakeOps(facts=GREEN_PR_FACTS)
    run_id = "r-preflight-mini-full"
    target_dir = tmp_path / f"target-{run_id}"
    target_dir.mkdir()
    make_profile(target_dir)  # profiles/mini.yaml — без узла design

    state = runner.start(
        subject="s", repo="alpha", repo_slug="owner/alpha", ws_id="WS-1",
        target_dir=str(target_dir), bundle_dir=BUNDLE_DIR,
        profile="profiles/mini.yaml", run_id=run_id, ops=ops,
    )

    assert state.status == "stopped_preflight"
    assert "design" not in ops.authored
    assert "gate-candidate" not in state.ops


def test_target_profile_declares_fail_closed_on_broken_yaml(
    tmp_path: Path, runs_root,
) -> None:
    """Фикс-раунд ревью, minor #3: битый YAML в target-профиле ⇒
    `target_profile_declares` fail-closed False (не traceback), и `start`
    стопится `stopped_preflight`, а не роняет прогон исключением."""
    from governance import policy_sources

    ops = FakeOps(facts=GREEN_PR_FACTS)
    run_id = "r-preflight-broken-yaml"
    kwargs = _start_kwargs(tmp_path, run_id, ops)
    profile_path = Path(kwargs["target_dir"]) / "profiles" / "team-exp.yaml"
    profile_path.write_text(
        "artifacts:\n  - {id: design, template: [unterminated\n",
        encoding="utf-8",
    )

    assert policy_sources.target_profile_declares(
        kwargs["target_dir"], kwargs["profile"], "design"
    ) is False

    state = runner.start(**kwargs)

    assert state.status == "stopped_preflight"


def test_deliver_refuses_when_target_profile_lacks_design(
    tmp_path: Path,
) -> None:
    """Step 1(в): `deliver` с тем же расхождением (профиль target без
    design) ⇒ RuntimeError с той же процедурой, что у `stopped_preflight`
    раннера — проверяется ДО ensure_branch/стампа.

    Бандл дополнен до полного 6-узлового состава (Task 7 плана
    acceptance-node — `_check_bundle_composition` теперь требует точное
    совпадение до profile-preflight'а; узел acceptance довезён в
    `_BUNDLE_DAG`)."""
    target = tmp_path / "alpha"
    bundle = target / "workstreams/WS-alpha-7/spec"
    bundle.mkdir(parents=True)
    (bundle / "00-charter.md").write_text("# charter\n", encoding="utf-8")
    (bundle / "10-requirements.md").write_text("# requirements\n", encoding="utf-8")
    (bundle / "15-behaviour-spec.md").write_text("# behaviour\n", encoding="utf-8")
    (bundle / "20-design.md").write_text("# design\n", encoding="utf-8")
    (bundle / "25-acceptance.md").write_text("# acceptance\n", encoding="utf-8")
    (bundle / "30-decomposition.md").write_text("# decomposition\n", encoding="utf-8")
    _write_stale_profile(target)

    class _MiniOps:
        def is_dirty(self, target_dir: str) -> bool:
            return False

        def checkout_and_pull(self, target_dir: str, branch: str) -> None:
            pass

    with pytest.raises(RuntimeError) as exc_info:
        task_bridge.deliver(
            target_dir=str(target),
            repo_slug="owner/alpha",
            ws_id="WS-alpha-7",
            subject="s",
            bundle_dir="workstreams/WS-alpha-7/spec",
            base_ref="master",
            ops=_MiniOps(),
            profile="profiles/team-exp.yaml",
        )
    message = str(exc_info.value)
    assert "design" in message.lower()
    assert "authority-root" in message
    assert "мерж человеком" in message


def test_deliver_refuses_when_target_profile_lacks_decomposition(
    tmp_path: Path,
) -> None:
    """Зеркальный тест design-варианта (Task 7 плана acceptance-node):
    профиль target несёт design И acceptance, но НЕ decomposition
    (5-узловой профиль, `_write_five_node_with_acceptance_profile`) ⇒
    `deliver` отказывает по preflight decomposition-узла (не более
    раннего acceptance в порядке проверки) — та же процедура, что у
    `stopped_preflight` раннера, проверяется ДО ensure_branch/стампа."""
    target = tmp_path / "alpha"
    bundle = target / "workstreams/WS-alpha-7/spec"
    bundle.mkdir(parents=True)
    (bundle / "00-charter.md").write_text("# charter\n", encoding="utf-8")
    (bundle / "10-requirements.md").write_text("# requirements\n", encoding="utf-8")
    (bundle / "15-behaviour-spec.md").write_text("# behaviour\n", encoding="utf-8")
    (bundle / "20-design.md").write_text("# design\n", encoding="utf-8")
    (bundle / "25-acceptance.md").write_text("# acceptance\n", encoding="utf-8")
    (bundle / "30-decomposition.md").write_text("# decomposition\n", encoding="utf-8")
    _write_five_node_with_acceptance_profile(target)

    class _MiniOps:
        def is_dirty(self, target_dir: str) -> bool:
            return False

        def checkout_and_pull(self, target_dir: str, branch: str) -> None:
            pass

    with pytest.raises(RuntimeError) as exc_info:
        task_bridge.deliver(
            target_dir=str(target),
            repo_slug="owner/alpha",
            ws_id="WS-alpha-7",
            subject="s",
            bundle_dir="workstreams/WS-alpha-7/spec",
            base_ref="master",
            ops=_MiniOps(),
            profile="profiles/team-exp.yaml",
        )
    message = str(exc_info.value)
    assert "decomposition" in message.lower()
    assert "authority-root" in message
    assert "мерж человеком" in message


def test_deliver_refuses_when_target_profile_lacks_acceptance(
    tmp_path: Path,
) -> None:
    """Зеркальный тест decomposition-варианта (Task 7 плана
    acceptance-node): профиль target несёт design И decomposition, но НЕ
    acceptance (5-узловой профиль, `_write_five_node_profile`) ⇒ `deliver`
    отказывает по preflight acceptance-узла — та же процедура, что у
    `stopped_preflight` раннера, проверяется ДО ensure_branch/стампа."""
    target = tmp_path / "alpha"
    bundle = target / "workstreams/WS-alpha-7/spec"
    bundle.mkdir(parents=True)
    (bundle / "00-charter.md").write_text("# charter\n", encoding="utf-8")
    (bundle / "10-requirements.md").write_text("# requirements\n", encoding="utf-8")
    (bundle / "15-behaviour-spec.md").write_text("# behaviour\n", encoding="utf-8")
    (bundle / "20-design.md").write_text("# design\n", encoding="utf-8")
    (bundle / "25-acceptance.md").write_text("# acceptance\n", encoding="utf-8")
    (bundle / "30-decomposition.md").write_text("# decomposition\n", encoding="utf-8")
    _write_five_node_profile(target)

    class _MiniOps:
        def is_dirty(self, target_dir: str) -> bool:
            return False

        def checkout_and_pull(self, target_dir: str, branch: str) -> None:
            pass

    with pytest.raises(RuntimeError) as exc_info:
        task_bridge.deliver(
            target_dir=str(target),
            repo_slug="owner/alpha",
            ws_id="WS-alpha-7",
            subject="s",
            bundle_dir="workstreams/WS-alpha-7/spec",
            base_ref="master",
            ops=_MiniOps(),
            profile="profiles/team-exp.yaml",
        )
    message = str(exc_info.value)
    assert "acceptance" in message.lower()
    assert "authority-root" in message
    assert "мерж человеком" in message


def test_resume_after_profile_delivered_continues_run(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Step 1(г): «доставили» обновлённый профиль (дописали design в
    target-профиль) ⇒ `resume(run_id)` ПРОДОЛЖАЕТ прогон, не тихий no-op —
    `stopped_preflight` обязан быть в `_STOPPED_RESET_OPS`."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)
    run_id = "r-preflight-resume"
    kwargs = _start_kwargs(tmp_path, run_id, ops)
    profile_path = _write_stale_profile(Path(kwargs["target_dir"]))

    stopped = runner.start(**kwargs)
    assert stopped.status == "stopped_preflight"

    # «Доставка» обновлённого профиля PR-ом (человеческий мерж authority-root):
    # дописываем узел design в target-профиль.
    profile_path.write_text(_TEAM_EXP_PROFILE_TEXT, encoding="utf-8")

    resumed = runner.resume(run_id, ops)

    assert resumed.status != "stopped_preflight"
    assert resumed.ops["merge"]["status"] == "completed"


# --- Task 9: сквозной смоук design-узла -------------------------------------


def test_design_node_end_to_end_smoke(tmp_path: Path, runs_root) -> None:
    """Сквозной смоук S2→S4 design-узла (Task 9), один прогон `start()`.

    Не дублирует то, что уже проверено по частям:
    - порядок всех четырёх author-шагов —
      `test_default_author_backend_is_codex_author_disp_not_called`;
    - зелёный happy path до мержа (без architects-Q в requirements) —
      `test_happy_path_agent_merge`/`test_today_reality_agent_merges`;
    - RED-ветка GC-DESIGN-COVERAGE (Q без резолюции) —
      `test_gate_stops_on_uncovered_architect_question`;
    - RED-ветки GC-UNPINNED/GC-STALE на рёбрах design —
      `test_gate_design_missing_*_pin_is_unpinned`/
      `test_gate_design_stale_*_pin`.

    Недостающий кусок, который собирает этот тест: ОДИН сквозной прогон,
    где (а) `ops.author` пишет канонический `20-design.md` с валидными
    пинами upstream И НЕПУСТЫМ, но покрытым (не вакуумно) architects-Q —
    S2 проходит все четыре author-шага по порядку, `author-design`
    завершён без skip, файл физически лежит в бандле; (б) тот же прогон
    идёт дальше и S4-гейт на этом полном 4-узловом бандле — зелёный,
    прогон доходит до мержа."""
    q03_requirements = (
        _DEFAULT_REQUIREMENTS_BODY
        + "- **Q-03 · owner_role: architects · blocking: false.** Как?\n"
    )

    class CoveredQOps(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            if kind not in ("requirements", "design"):
                return super().author(target_dir, kind, subject, bundle_dir)
            self.calls.append(("author", kind))
            self.authored.append(kind)
            bundle = Path(target_dir) / bundle_dir
            bundle.mkdir(parents=True, exist_ok=True)
            if kind == "requirements":
                (bundle / "10-requirements.md").write_text(
                    q03_requirements, encoding="utf-8"
                )
                return 0
            req_pin = blob_sha1(q03_requirements)
            beh_pin = blob_sha1(_DEFAULT_BEHAVIOUR_BODY)
            (bundle / "20-design.md").write_text(
                "---\n"
                "spec_stage: design\n"
                "status: draft\n"
                "owner_role: architects\n"
                "traces_to: [requirements, behaviour-spec]\n"
                "upstream_hashes:\n"
                f'  requirements: "{req_pin}"\n'
                f'  behaviour-spec: "{beh_pin}"\n'
                "---\n"
                "#### Q-03 · owner_role: architects · resolution: "
                "resolved\nОтвет на вопрос архитектуры.\n",
                encoding="utf-8",
            )
            return 0

    ops = CoveredQOps(review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)
    run_id = "r-design-e2e-smoke"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))

    # S2: все шесть author-шагов прошли РОВНО в этом порядке.
    assert ops.authored == [
        "charter", "requirements", "behaviour-spec", "design", "acceptance",
        "decomposition",
    ]
    assert state.ops["author-design"]["status"] == "completed"
    assert state.ops["author-design"]["skipped"] is False
    bundle_dir = Path(state.target_dir) / state.bundle_dir
    assert (bundle_dir / "20-design.md").exists()

    # S4: гейт зелёный на полном 6-узловом бандле (валидные пины + покрытый
    # architects-Q) — прогон дошёл до мержа, не остановился на gate/review.
    assert state.status == "completed"
    assert state.ops["merge"]["status"] == "completed"
    assert ops.merged == [(state.pr, ops.head)]


# --- Task 6: S4-гарды decomposition (отсутствие, ребро design, DSL, граф DT) --


def test_gate_stops_when_decomposition_missing_from_bundle(
    tmp_path: Path, runs_root,
) -> None:
    """Зеркало `test_gate_stops_when_design_node_missing_from_bundle` на
    узел decomposition: required-узел decomposition профиля team-exp, но
    файла 30-decomposition.md в бандле нет ⇒ `stopped_gate` локально — ДО
    цикла рёбер, даже когда design физически присутствует и валиден."""
    ops = FakeOps(facts=GREEN_PR_FACTS)
    run_id = "r-decomposition-absent"
    target_dir = tmp_path / f"target-{run_id}"
    target_dir.mkdir()
    profile_dir = target_dir / "profiles"
    profile_dir.mkdir(parents=True)
    (profile_dir / "team-exp.yaml").write_text(
        _TEAM_EXP_PROFILE_TEXT, encoding="utf-8"
    )
    bundle_dir = target_dir / BUNDLE_DIR
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "00-charter.md").write_text("# charter\n", encoding="utf-8")
    (bundle_dir / "10-requirements.md").write_text(
        _DEFAULT_REQUIREMENTS_BODY, encoding="utf-8"
    )
    (bundle_dir / "15-behaviour-spec.md").write_text(
        _DEFAULT_BEHAVIOUR_BODY, encoding="utf-8"
    )
    req_pin = blob_sha1(_DEFAULT_REQUIREMENTS_BODY)
    beh_pin = blob_sha1(_DEFAULT_BEHAVIOUR_BODY)
    (bundle_dir / "20-design.md").write_text(
        "---\n"
        "spec_stage: design\n"
        "status: draft\n"
        "owner_role: architects\n"
        "traces_to: [requirements, behaviour-spec]\n"
        "upstream_hashes:\n"
        f'  requirements: "{req_pin}"\n'
        f'  behaviour-spec: "{beh_pin}"\n'
        "---\n"
        "Открытых архитектурных вопросов нет (входной набор пуст)\n",
        encoding="utf-8",
    )
    state = rs.new_run(
        subject="s", repo="alpha", repo_slug="owner/alpha", ws_id="WS-1",
        target_dir=str(target_dir), bundle_dir=BUNDLE_DIR,
        profile="profiles/team-exp.yaml", run_id=run_id,
    )
    state.branch = "spec/WS-1-behaviour"
    state.ops = {
        "branch": {"status": "completed"},
        "author-charter": {"status": "completed", "skipped": True},
        "author-requirements": {"status": "completed", "skipped": True},
        "author-behaviour": {"status": "completed", "skipped": True},
        "author-design": {"status": "completed", "skipped": True},
        # decomposition намеренно НЕ авторен и не пропущен — файла нет вовсе.
        "author-decomposition": {"status": "completed", "skipped": True},
        "commit": {"status": "completed"},
    }
    rs.save(state)

    result = runner.advance(state, ops)

    assert result.status == "stopped_gate"
    findings = (runner.run_dir(run_id) / "gate-findings.txt").read_text()
    assert "GC-COMPLETENESS(decomposition)" in findings
    # Гард отсутствия design/decomposition стоит ДО цикла рёбер — иначе
    # завтрашняя правка порядка кода тихо перепутала бы, что стопнуло
    # прогон.
    assert "GC-UNPINNED" not in findings
    assert "GC-STALE" not in findings
    assert "push" not in result.ops


def test_gate_decomposition_unpinned_edge_stops(
    tmp_path: Path, runs_root,
) -> None:
    """GC-UNPINNED(prospective) на ребре decomposition→design: `traces_to`
    объявляет design, но `upstream_hashes` пуст."""

    class _Ops(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            rc = super().author(target_dir, kind, subject, bundle_dir)
            if kind != "decomposition":
                return rc
            path = Path(target_dir) / bundle_dir / "30-decomposition.md"
            path.write_text(
                "---\n"
                "spec_stage: decomposition\n"
                "dt_contract_version: 2\n"
                "status: draft\n"
                "owner_role: tech-lead\n"
                "traces_to: [design]\n"
                "upstream_hashes: {}\n"
                "---\n"
                "#### DT-01: x · type: implement · owner: dev\n"
                "scenarios: [BEH-01]\n"
                "depends_on: []\n"
                "parallel_group: solo\n",
                encoding="utf-8",
            )
            return rc

    ops = _Ops(facts=GREEN_PR_FACTS)
    state = runner.start(
        **_start_kwargs(tmp_path, "r-decomposition-unpinned", ops)
    )

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-decomposition-unpinned") / "gate-findings.txt"
    ).read_text()
    assert "GC-UNPINNED" in findings and "design" in findings
    assert "push" not in state.ops


def test_gate_decomposition_stale_pin_stops(tmp_path: Path, runs_root) -> None:
    """GC-STALE(prospective) на ребре decomposition→design: пин
    синтаксически валиден (40 hex), но не совпадает с blob-хешем design в
    worktree."""

    class _Ops(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            rc = super().author(target_dir, kind, subject, bundle_dir)
            if kind != "decomposition":
                return rc
            path = Path(target_dir) / bundle_dir / "30-decomposition.md"
            path.write_text(
                "---\n"
                "spec_stage: decomposition\n"
                "dt_contract_version: 2\n"
                "status: draft\n"
                "owner_role: tech-lead\n"
                "traces_to: [design]\n"
                "upstream_hashes:\n"
                f'  design: "{"a" * 40}"\n'
                "---\n"
                "#### DT-01: x · type: implement · owner: dev\n"
                "scenarios: [BEH-01]\n"
                "depends_on: []\n"
                "parallel_group: solo\n",
                encoding="utf-8",
            )
            return rc

    ops = _Ops(facts=GREEN_PR_FACTS)
    state = runner.start(**_start_kwargs(tmp_path, "r-decomposition-stale", ops))

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-decomposition-stale") / "gate-findings.txt"
    ).read_text()
    assert "GC-STALE" in findings and "design" in findings
    assert "push" not in state.ops


def test_gate_decomposition_undeclared_design_edge_stops(
    tmp_path: Path, runs_root,
) -> None:
    """MAJOR-1-аналог на decomposition: `traces_to` не несёт design вовсе
    (не «не запинено» — отсутствует в traces_to) — required-ребро
    decomposition→design обязано стопить S4 находкой, не пропускаться
    молча."""

    class _Ops(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            rc = super().author(target_dir, kind, subject, bundle_dir)
            if kind != "decomposition":
                return rc
            path = Path(target_dir) / bundle_dir / "30-decomposition.md"
            path.write_text(
                "---\n"
                "spec_stage: decomposition\n"
                "dt_contract_version: 2\n"
                "status: draft\n"
                "owner_role: tech-lead\n"
                "traces_to: []\n"
                "---\n"
                "#### DT-01: x · type: implement · owner: dev\n"
                "scenarios: [BEH-01]\n"
                "depends_on: []\n"
                "parallel_group: solo\n",
                encoding="utf-8",
            )
            return rc

    ops = _Ops(facts=GREEN_PR_FACTS)
    state = runner.start(
        **_start_kwargs(tmp_path, "r-decomposition-undeclared", ops)
    )

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-decomposition-undeclared") / "gate-findings.txt"
    ).read_text()
    assert "GC-UNPINNED" in findings and "design" in findings
    assert "не объявлено в traces_to" in findings
    assert "push" not in state.ops


def test_gate_decomposition_dsl_empty_stops(tmp_path: Path, runs_root) -> None:
    """Гард GC-DSL-EMPTY на 30-decomposition.md: пин design корректен,
    ребро объявлено, но ни одного распознаваемого DT-заголовка."""

    class _Ops(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            rc = super().author(target_dir, kind, subject, bundle_dir)
            if kind != "decomposition":
                return rc
            bundle = Path(target_dir) / bundle_dir
            design_pin = blob_sha1(
                (bundle / "20-design.md").read_text(encoding="utf-8")
            )
            path = bundle / "30-decomposition.md"
            path.write_text(
                "---\n"
                "spec_stage: decomposition\n"
                "dt_contract_version: 2\n"
                "status: draft\n"
                "owner_role: tech-lead\n"
                "traces_to: [design]\n"
                "upstream_hashes:\n"
                f'  design: "{design_pin}"\n'
                "---\n"
                "### Задачи в вольном стиле, без машинной грамматики.\n",
                encoding="utf-8",
            )
            return rc

    ops = _Ops(facts=GREEN_PR_FACTS)
    state = runner.start(
        **_start_kwargs(tmp_path, "r-decomposition-dsl-empty", ops)
    )

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-decomposition-dsl-empty") / "gate-findings.txt"
    ).read_text()
    assert "GC-DSL-EMPTY" in findings and "30-decomposition.md" in findings
    assert "DT-NN" in findings
    assert "push" not in state.ops


def test_gate_dt_graph_finding_stops(tmp_path: Path, runs_root) -> None:
    """GC-DT-GRAPH (спека Task 6): DSL decomposition валиден, ребро
    design запинено верно, но граф несюръективен — BEH-02 не покрыт ни
    одной DT-задачей (decomposition остаётся дефолтным, покрывающим только
    BEH-01 — см. `FakeOps.author`)."""
    beh_two = (
        _DEFAULT_BEHAVIOUR_BODY
        + "\n#### BEH-02: y\n`traces: [FR-01]`\n- **checked_by**: y\n"
    )

    class _Ops(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            if kind == "behaviour-spec":
                self.calls.append(("author", kind))
                self.authored.append(kind)
                path = Path(target_dir) / bundle_dir / "15-behaviour-spec.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(beh_two, encoding="utf-8")
                return 0
            return super().author(target_dir, kind, subject, bundle_dir)

    ops = _Ops(facts=GREEN_PR_FACTS)
    state = runner.start(
        **_start_kwargs(tmp_path, "r-decomposition-dt-graph", ops)
    )

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-decomposition-dt-graph") / "gate-findings.txt"
    ).read_text()
    assert "GC-DT-GRAPH" in findings and "BEH-02" in findings
    assert "push" not in state.ops


def test_gate_dt_graph_non_fatal_finding_is_surfaced_as_warning(
    tmp_path: Path, runs_root,
) -> None:
    """Round 7 ревью PR #161 (минор, контракт владельца), фикстура
    обновлена в round 13 (major того же раунда промотировал «группа
    наблюдения не выводится» в fatal — прежняя фикстура теперь стопит
    гейт, см. test_gate_dt_graph_finding_stops_on_underivable_group):
    осиротевший/опечатанный путь в verifies — единственный ОСТАВШИЙСЯ
    non-fatal класс. DT-02 несёт СОБСТВЕННУЮ checked_by-цель (BEH-02
    биндится), значит группа выводится и рендер не падает; verifies
    указывает на путь, которого нет ни у одной checked_by-цели бандла.
    Граф иначе валиден — гейт ПРОХОДИТ, но gate-findings.txt несёт
    `warning GC-DT-GRAPH:` строку с опечаткой."""
    beh_two = (
        _DEFAULT_BEHAVIOUR_BODY
        + "\n#### BEH-02: y\n`traces: [FR-01]`\n- **checked_by**: "
        "`kind: integration` `target: tests/test_y.py`\n"
    )

    class _Ops(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            if kind == "behaviour-spec":
                self.calls.append(("author", kind))
                self.authored.append(kind)
                path = Path(target_dir) / bundle_dir / "15-behaviour-spec.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(beh_two, encoding="utf-8")
                return 0
            if kind == "decomposition":
                self.calls.append(("author", kind))
                self.authored.append(kind)
                bundle = Path(target_dir) / bundle_dir
                design_pin = blob_sha1(
                    (bundle / "20-design.md").read_text(encoding="utf-8")
                )
                acceptance_pin = blob_sha1(
                    (bundle / "25-acceptance.md").read_text(encoding="utf-8")
                )
                path = bundle / "30-decomposition.md"
                path.write_text(
                    "---\n"
                    "spec_stage: decomposition\n"
                    "dt_contract_version: 2\n"
                    "status: draft\n"
                    "owner_role: tech-lead\n"
                    "traces_to: [design, acceptance]\n"
                    "upstream_hashes:\n"
                    f'  design: "{design_pin}"\n'
                    f'  acceptance: "{acceptance_pin}"\n'
                    "---\n"
                    "## Задачи\n\n"
                    "#### DT-01: x · type: implement · owner: dev\n"
                    "scenarios: [BEH-01]\n"
                    "depends_on: []\n"
                    "parallel_group: solo\n\n"
                    "delivers: []\n"
                    "#### DT-02: y · type: verify · owner: qa\n"
                    "scenarios: [BEH-02]\n"
                    "depends_on: [DT-01]\n"
                    "delivered_by: [DT-01]\n"
                    "parallel_group: solo\n"
                    "delivers: []\n"
                    "verifies:\n  - tests/test_typo.py\n\n"
                    "## Инварианты графа\n\nСоблюдены.\n\n"
                    "## Порядок и параллельность\n\nПоследовательно.\n\n"
                    "## Вне объёма\n\nНичего не исключено.\n",
                    encoding="utf-8",
                )
                return 0
            return super().author(target_dir, kind, subject, bundle_dir)

    ops = _Ops(facts=GREEN_PR_FACTS)
    state = runner.start(
        **_start_kwargs(tmp_path, "r-dt-graph-warning", ops)
    )

    assert state.status != "stopped_gate"
    findings_path = (
        runner.run_dir("r-dt-graph-warning") / "gate-findings.txt"
    )
    assert findings_path.exists()
    findings = findings_path.read_text()
    assert "warning GC-DT-GRAPH" in findings
    assert "tests/test_typo.py" in findings
    assert "опечатка либо осиротевший путь" in findings


def test_gate_dt_graph_finding_stops_on_underivable_group(
    tmp_path: Path, runs_root,
) -> None:
    """Round 13 ревью PR #161, минор (контракт владельца): «группа
    наблюдения не выводится вовсе» промотирована в FATAL — гейт теперь
    останавливается на этом входе, не молча пропускает его как warning.
    #162: orphan verifies объясняет причину warning-строкой, но не отменяет
    fatal underivable — именно error управляет исходом гейта."""
    beh_two = (
        _DEFAULT_BEHAVIOUR_BODY
        + "\n#### BEH-02: y\n`traces: [FR-01]`\n"
        "- **checked_by**: без бэктиков, не биндится\n"
    )

    class _Ops(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            if kind == "behaviour-spec":
                self.calls.append(("author", kind))
                self.authored.append(kind)
                path = Path(target_dir) / bundle_dir / "15-behaviour-spec.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(beh_two, encoding="utf-8")
                return 0
            if kind == "decomposition":
                self.calls.append(("author", kind))
                self.authored.append(kind)
                bundle = Path(target_dir) / bundle_dir
                design_pin = blob_sha1(
                    (bundle / "20-design.md").read_text(encoding="utf-8")
                )
                acceptance_pin = blob_sha1(
                    (bundle / "25-acceptance.md").read_text(encoding="utf-8")
                )
                path = bundle / "30-decomposition.md"
                path.write_text(
                    "---\n"
                    "spec_stage: decomposition\n"
                    "dt_contract_version: 2\n"
                    "status: draft\n"
                    "owner_role: tech-lead\n"
                    "traces_to: [design, acceptance]\n"
                    "upstream_hashes:\n"
                    f'  design: "{design_pin}"\n'
                    f'  acceptance: "{acceptance_pin}"\n'
                    "---\n"
                    "## Задачи\n\n"
                    "#### DT-01: x · type: implement · owner: dev\n"
                    "scenarios: [BEH-01]\n"
                    "depends_on: []\n"
                    "parallel_group: solo\n\n"
                    "delivers: []\n"
                    "#### DT-02: y · type: verify · owner: qa\n"
                    "scenarios: [BEH-02]\n"
                    "depends_on: [DT-01]\n"
                    "delivered_by: [DT-01]\n"
                    "parallel_group: solo\n"
                    "delivers: []\n"
                    "verifies:\n  - tests/test_typo.py\n\n"
                    "## Инварианты графа\n\nСоблюдены.\n\n"
                    "## Порядок и параллельность\n\nПоследовательно.\n\n"
                    "## Вне объёма\n\nНичего не исключено.\n",
                    encoding="utf-8",
                )
                return 0
            return super().author(target_dir, kind, subject, bundle_dir)

    ops = _Ops(facts=GREEN_PR_FACTS)
    state = runner.start(
        **_start_kwargs(tmp_path, "r-dt-graph-underivable-stop", ops)
    )

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-dt-graph-underivable-stop") / "gate-findings.txt"
    ).read_text()
    assert "error GC-DT-GRAPH" in findings
    assert "warning GC-DT-GRAPH" in findings
    assert "опечатка либо осиротевший путь" in findings
    assert "группа наблюдения не выводится" in findings
    assert "DT-02" in findings and "verifies" in findings


# --- Task 6 (план acceptance-node): S4-гарды acceptance (отсутствие, два
# ребра acceptance, ребро decomposition→acceptance, DSL, Must-покрытие) ----


def test_gate_stops_when_acceptance_missing_from_bundle(
    tmp_path: Path, runs_root,
) -> None:
    """Зеркало `test_gate_stops_when_decomposition_missing_from_bundle` на
    узел acceptance: required-узел acceptance профиля team-exp, но файла
    25-acceptance.md в бандле нет ⇒ `stopped_gate` локально — ДО цикла
    рёбер и ДО проверки decomposition, даже когда design физически
    присутствует и валиден."""
    ops = FakeOps(facts=GREEN_PR_FACTS)
    run_id = "r-acceptance-absent"
    target_dir = tmp_path / f"target-{run_id}"
    target_dir.mkdir()
    profile_dir = target_dir / "profiles"
    profile_dir.mkdir(parents=True)
    (profile_dir / "team-exp.yaml").write_text(
        _TEAM_EXP_PROFILE_TEXT, encoding="utf-8"
    )
    bundle_dir = target_dir / BUNDLE_DIR
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "00-charter.md").write_text("# charter\n", encoding="utf-8")
    (bundle_dir / "10-requirements.md").write_text(
        _DEFAULT_REQUIREMENTS_BODY, encoding="utf-8"
    )
    (bundle_dir / "15-behaviour-spec.md").write_text(
        _DEFAULT_BEHAVIOUR_BODY, encoding="utf-8"
    )
    req_pin = blob_sha1(_DEFAULT_REQUIREMENTS_BODY)
    beh_pin = blob_sha1(_DEFAULT_BEHAVIOUR_BODY)
    (bundle_dir / "20-design.md").write_text(
        "---\n"
        "spec_stage: design\n"
        "status: draft\n"
        "owner_role: architects\n"
        "traces_to: [requirements, behaviour-spec]\n"
        "upstream_hashes:\n"
        f'  requirements: "{req_pin}"\n'
        f'  behaviour-spec: "{beh_pin}"\n'
        "---\n"
        "Открытых архитектурных вопросов нет (входной набор пуст)\n",
        encoding="utf-8",
    )
    state = rs.new_run(
        subject="s", repo="alpha", repo_slug="owner/alpha", ws_id="WS-1",
        target_dir=str(target_dir), bundle_dir=BUNDLE_DIR,
        profile="profiles/team-exp.yaml", run_id=run_id,
    )
    state.branch = "spec/WS-1-behaviour"
    state.ops = {
        "branch": {"status": "completed"},
        "author-charter": {"status": "completed", "skipped": True},
        "author-requirements": {"status": "completed", "skipped": True},
        "author-behaviour": {"status": "completed", "skipped": True},
        "author-design": {"status": "completed", "skipped": True},
        # acceptance намеренно НЕ авторен и не пропущен — файла нет вовсе.
        "author-acceptance": {"status": "completed", "skipped": True},
        "author-decomposition": {"status": "completed", "skipped": True},
        "commit": {"status": "completed"},
    }
    rs.save(state)

    result = runner.advance(state, ops)

    assert result.status == "stopped_gate"
    findings = (runner.run_dir(run_id) / "gate-findings.txt").read_text()
    assert "GC-COMPLETENESS(acceptance)" in findings
    # Гард отсутствия проверяет узлы по порядку design → acceptance →
    # decomposition — останов на acceptance, до decomposition вообще.
    assert "GC-COMPLETENESS(decomposition)" not in findings
    assert "GC-UNPINNED" not in findings
    assert "GC-STALE" not in findings
    assert "push" not in result.ops


def test_gate_acceptance_unpinned_requirements_edge_stops(
    tmp_path: Path, runs_root,
) -> None:
    """GC-UNPINNED(prospective) на ребре acceptance→requirements:
    `traces_to` объявляет requirements, но `upstream_hashes` не несёт его
    пин (только behaviour-spec)."""

    class _Ops(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            rc = super().author(target_dir, kind, subject, bundle_dir)
            if kind != "acceptance":
                return rc
            bundle = Path(target_dir) / bundle_dir
            beh_pin = blob_sha1(
                (bundle / "15-behaviour-spec.md").read_text(encoding="utf-8")
            )
            path = bundle / "25-acceptance.md"
            path.write_text(
                "---\n"
                "spec_stage: acceptance\n"
                "status: draft\n"
                "owner_role: qa\n"
                "traces_to: [requirements, behaviour-spec]\n"
                "upstream_hashes:\n"
                f'  behaviour-spec: "{beh_pin}"\n'
                "---\n"
                "#### AC-01: x · verification: manual\n"
                "traces: [FR-01]\n"
                "Наблюдаемый признак: человек видит x.\n",
                encoding="utf-8",
            )
            return rc

    ops = _Ops(facts=GREEN_PR_FACTS)
    state = runner.start(
        **_start_kwargs(tmp_path, "r-acceptance-unpinned", ops)
    )

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-acceptance-unpinned") / "gate-findings.txt"
    ).read_text()
    assert "GC-UNPINNED" in findings and "requirements" in findings
    assert "push" not in state.ops


def test_gate_acceptance_stale_behaviour_pin_stops(
    tmp_path: Path, runs_root,
) -> None:
    """GC-STALE(prospective) на ребре acceptance→behaviour-spec: пин
    синтаксически валиден (40 hex), но не совпадает с blob-хешем
    behaviour-spec в worktree."""

    class _Ops(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            rc = super().author(target_dir, kind, subject, bundle_dir)
            if kind != "acceptance":
                return rc
            bundle = Path(target_dir) / bundle_dir
            req_pin = blob_sha1(
                (bundle / "10-requirements.md").read_text(encoding="utf-8")
            )
            path = bundle / "25-acceptance.md"
            path.write_text(
                "---\n"
                "spec_stage: acceptance\n"
                "status: draft\n"
                "owner_role: qa\n"
                "traces_to: [requirements, behaviour-spec]\n"
                "upstream_hashes:\n"
                f'  requirements: "{req_pin}"\n'
                f'  behaviour-spec: "{"a" * 40}"\n'
                "---\n"
                "#### AC-01: x · verification: manual\n"
                "traces: [FR-01]\n"
                "Наблюдаемый признак: человек видит x.\n",
                encoding="utf-8",
            )
            return rc

    ops = _Ops(facts=GREEN_PR_FACTS)
    state = runner.start(**_start_kwargs(tmp_path, "r-acceptance-stale", ops))

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-acceptance-stale") / "gate-findings.txt"
    ).read_text()
    assert "GC-STALE" in findings and "behaviour-spec" in findings
    assert "push" not in state.ops


def test_gate_acceptance_undeclared_edge_stops(
    tmp_path: Path, runs_root,
) -> None:
    """MAJOR-1-аналог на acceptance: `traces_to` не несёт ни requirements,
    ни behaviour-spec — оба required-ребра acceptance обязаны стопить S4
    находкой, не пропускаться молча."""

    class _Ops(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            rc = super().author(target_dir, kind, subject, bundle_dir)
            if kind != "acceptance":
                return rc
            path = Path(target_dir) / bundle_dir / "25-acceptance.md"
            path.write_text(
                "---\n"
                "spec_stage: acceptance\n"
                "status: draft\n"
                "owner_role: qa\n"
                "traces_to: []\n"
                "---\n"
                "#### AC-01: x · verification: manual\n"
                "traces: [FR-01]\n"
                "Наблюдаемый признак: человек видит x.\n",
                encoding="utf-8",
            )
            return rc

    ops = _Ops(facts=GREEN_PR_FACTS)
    state = runner.start(
        **_start_kwargs(tmp_path, "r-acceptance-undeclared", ops)
    )

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-acceptance-undeclared") / "gate-findings.txt"
    ).read_text()
    assert "GC-UNPINNED" in findings
    assert "не объявлено в traces_to" in findings
    assert "push" not in state.ops


def test_gate_decomposition_acceptance_edge_unpinned_stops(
    tmp_path: Path, runs_root,
) -> None:
    """GC-UNPINNED(prospective) на ребре decomposition→acceptance:
    `traces_to` объявляет acceptance, но `upstream_hashes` не несёт его
    пин (design запинован верно)."""

    class _Ops(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            rc = super().author(target_dir, kind, subject, bundle_dir)
            if kind != "decomposition":
                return rc
            bundle = Path(target_dir) / bundle_dir
            design_pin = blob_sha1(
                (bundle / "20-design.md").read_text(encoding="utf-8")
            )
            path = bundle / "30-decomposition.md"
            path.write_text(
                "---\n"
                "spec_stage: decomposition\n"
                "dt_contract_version: 2\n"
                "status: draft\n"
                "owner_role: tech-lead\n"
                "traces_to: [design, acceptance]\n"
                "upstream_hashes:\n"
                f'  design: "{design_pin}"\n'
                "---\n"
                "#### DT-01: x · type: implement · owner: dev\n"
                "scenarios: [BEH-01]\n"
                "depends_on: []\n"
                "parallel_group: solo\n",
                encoding="utf-8",
            )
            return rc

    ops = _Ops(facts=GREEN_PR_FACTS)
    state = runner.start(
        **_start_kwargs(tmp_path, "r-decomposition-acceptance-unpinned", ops)
    )

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-decomposition-acceptance-unpinned")
        / "gate-findings.txt"
    ).read_text()
    assert "GC-UNPINNED" in findings and "acceptance" in findings
    assert "push" not in state.ops


def test_gate_acceptance_dsl_empty_stops(tmp_path: Path, runs_root) -> None:
    """Гард GC-DSL-EMPTY на 25-acceptance.md: пины requirements/
    behaviour-spec корректны, оба ребра объявлены, но ни одного
    распознаваемого AC-заголовка и без строки-декларации пустого
    Must-множества."""

    class _Ops(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            rc = super().author(target_dir, kind, subject, bundle_dir)
            if kind != "acceptance":
                return rc
            bundle = Path(target_dir) / bundle_dir
            req_pin = blob_sha1(
                (bundle / "10-requirements.md").read_text(encoding="utf-8")
            )
            beh_pin = blob_sha1(
                (bundle / "15-behaviour-spec.md").read_text(encoding="utf-8")
            )
            path = bundle / "25-acceptance.md"
            path.write_text(
                "---\n"
                "spec_stage: acceptance\n"
                "status: draft\n"
                "owner_role: qa\n"
                "traces_to: [requirements, behaviour-spec]\n"
                "upstream_hashes:\n"
                f'  requirements: "{req_pin}"\n'
                f'  behaviour-spec: "{beh_pin}"\n'
                "---\n"
                "### Критерии в вольном стиле, без машинной грамматики.\n",
                encoding="utf-8",
            )
            return rc

    ops = _Ops(facts=GREEN_PR_FACTS)
    state = runner.start(
        **_start_kwargs(tmp_path, "r-acceptance-dsl-empty", ops)
    )

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-acceptance-dsl-empty") / "gate-findings.txt"
    ).read_text()
    assert "GC-DSL-EMPTY" in findings and "25-acceptance.md" in findings
    assert "AC-NN" in findings
    assert "push" not in state.ops


def test_gate_acceptance_dsl_declaration_line_passes_dsl_empty(
    tmp_path: Path, runs_root,
) -> None:
    """Минор круга 3 ревью спеки: файл ТОЛЬКО со строкой-декларацией
    (Must-множество требований пусто, `#### AC-` не нужен) НЕ стопится
    GC-DSL-EMPTY — requirements намеренно без единого Must-требования, так
    что декларация правдива и GC-AC-COVERAGE тоже не стопит; прогон
    доходит до `push` (доезжает до `completed`, тот же паттерн зелёного
    прогона, что `test_today_reality_agent_merges`)."""

    class _Ops(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            if kind == "requirements":
                self.calls.append(("author", kind))
                self.authored.append(kind)
                path = Path(target_dir) / bundle_dir / "10-requirements.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    "#### FR-01: x\n**Priority**: Should\n", encoding="utf-8"
                )
                return 0
            if kind == "acceptance":
                self.calls.append(("author", kind))
                self.authored.append(kind)
                bundle = Path(target_dir) / bundle_dir
                req_pin = blob_sha1(
                    (bundle / "10-requirements.md").read_text(encoding="utf-8")
                )
                beh_pin = blob_sha1(
                    (bundle / "15-behaviour-spec.md").read_text(encoding="utf-8")
                )
                path = bundle / "25-acceptance.md"
                path.write_text(
                    "---\n"
                    "spec_stage: acceptance\n"
                    "status: draft\n"
                    "owner_role: qa\n"
                    "traces_to: [requirements, behaviour-spec]\n"
                    "upstream_hashes:\n"
                    f'  requirements: "{req_pin}"\n'
                    f'  behaviour-spec: "{beh_pin}"\n'
                    "---\n"
                    "Must-требований во входном наборе нет\n",
                    encoding="utf-8",
                )
                return 0
            return super().author(target_dir, kind, subject, bundle_dir)

    ops = _Ops(review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)
    state = runner.start(
        **_start_kwargs(tmp_path, "r-acceptance-declaration-only", ops)
    )

    findings_path = (
        runner.run_dir("r-acceptance-declaration-only") / "gate-findings.txt"
    )
    # Утверждение сужено с «файла находок нет» до «нет ни одной ошибки»
    # (#282, врезка GC-DT-CONTRACT в гейт). Прежняя форма была прокси
    # СИЛЬНЕЕ того, что тест называет: с переходным дефолтом
    # `allow_legacy_dt=True` каждый бандл без объявленной версии
    # получает НЕ останавливающий warning, и «файла нет» стало ложным
    # для всех зелёных прогонов разом. Авторитетный признак «гейт
    # прошёл» — `state.status`, как и говорит комментарий к `warnings`
    # в `_step_gate`; предмет самого теста — GC-DSL-EMPTY и
    # GC-AC-COVERAGE, и он проверяется теперь поимённо, а не через
    # отсутствие файла.
    findings = (
        findings_path.read_text(encoding="utf-8")
        if findings_path.exists()
        else ""
    )
    assert "error" not in findings, findings
    assert "GC-DSL-EMPTY" not in findings, findings
    assert "GC-AC-COVERAGE" not in findings, findings
    assert "push" in state.ops
    assert state.status == "completed"


def test_gate_ac_coverage_finding_stops(tmp_path: Path, runs_root) -> None:
    """GC-AC-COVERAGE (Task 6 плана acceptance-node,
    `governance/acceptance_guard.coverage_findings`): валидный DSL, оба
    ребра корректно запинованы, но Must-FR остаётся непокрытым ни одним
    AC — отдельная находка от UNPINNED/STALE/DSL-EMPTY выше (зеркало
    GC-DESIGN-COVERAGE/GC-DT-GRAPH)."""
    req_two = (
        "#### FR-01: x\n**Priority**: Must\n"
        "#### NFR-01: y\n**Priority**: Should\n"
    )

    class _Ops(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            if kind == "requirements":
                self.calls.append(("author", kind))
                self.authored.append(kind)
                path = Path(target_dir) / bundle_dir / "10-requirements.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(req_two, encoding="utf-8")
                return 0
            if kind == "acceptance":
                self.calls.append(("author", kind))
                self.authored.append(kind)
                bundle = Path(target_dir) / bundle_dir
                req_pin = blob_sha1(
                    (bundle / "10-requirements.md").read_text(encoding="utf-8")
                )
                beh_pin = blob_sha1(
                    (bundle / "15-behaviour-spec.md").read_text(encoding="utf-8")
                )
                path = bundle / "25-acceptance.md"
                path.write_text(
                    "---\n"
                    "spec_stage: acceptance\n"
                    "status: draft\n"
                    "owner_role: qa\n"
                    "traces_to: [requirements, behaviour-spec]\n"
                    "upstream_hashes:\n"
                    f'  requirements: "{req_pin}"\n'
                    f'  behaviour-spec: "{beh_pin}"\n'
                    "---\n"
                    "#### AC-01: x · verification: manual\n"
                    "traces: [NFR-01]\n"
                    "Наблюдаемый признак: человек видит x.\n",
                    encoding="utf-8",
                )
                return 0
            return super().author(target_dir, kind, subject, bundle_dir)

    ops = _Ops(facts=GREEN_PR_FACTS)
    state = runner.start(
        **_start_kwargs(tmp_path, "r-acceptance-ac-coverage", ops)
    )

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-acceptance-ac-coverage") / "gate-findings.txt"
    ).read_text()
    assert "GC-AC-COVERAGE" in findings and "FR-01" in findings
    assert "push" not in state.ops


def test_gate_dt_graph_warning_survives_later_ac_coverage_stop(
    tmp_path: Path, runs_root,
) -> None:
    """Round 7 ревью PR #161, минор (контракт владельца, точка 3): давний
    баг рядом с нашим кодом — каждая находка в `_step_gate` писала
    gate-findings.txt через `write_text`, ЗАТИРАЯ любую предыдущую запись
    целиком. Non-fatal GC-DT-GRAPH warning (round 6) — первая НЕ
    останавливающая запись в этой функции, и более поздний fatal-стоп
    (GC-AC-COVERAGE) стирал её молча. Теперь находки НАКАПЛИВАЮТСЯ: warning
    остаётся в файле рядом с error, даже когда прогон в итоге стопится
    позже по другой причине.

    Фикстура обновлена в round 13 (осиротевший путь в verifies — теперь
    единственный non-fatal класс; «группа наблюдения не выводится» стала
    fatal и сама стопила бы гейт раньше, чем дело дошло бы до
    GC-AC-COVERAGE)."""
    beh_two = (
        _DEFAULT_BEHAVIOUR_BODY
        + "\n#### BEH-02: y\n`traces: [FR-01]`\n- **checked_by**: "
        "`kind: integration` `target: tests/test_y.py`\n"
    )
    req_two = (
        "#### FR-01: x\n**Priority**: Must\n"
        "#### NFR-01: y\n**Priority**: Should\n"
    )

    class _Ops(FakeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            if kind == "behaviour-spec":
                self.calls.append(("author", kind))
                self.authored.append(kind)
                path = Path(target_dir) / bundle_dir / "15-behaviour-spec.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(beh_two, encoding="utf-8")
                return 0
            if kind == "requirements":
                self.calls.append(("author", kind))
                self.authored.append(kind)
                path = Path(target_dir) / bundle_dir / "10-requirements.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(req_two, encoding="utf-8")
                return 0
            if kind == "decomposition":
                self.calls.append(("author", kind))
                self.authored.append(kind)
                bundle = Path(target_dir) / bundle_dir
                design_pin = blob_sha1(
                    (bundle / "20-design.md").read_text(encoding="utf-8")
                )
                acceptance_pin = blob_sha1(
                    (bundle / "25-acceptance.md").read_text(encoding="utf-8")
                )
                path = bundle / "30-decomposition.md"
                path.write_text(
                    "---\n"
                    "spec_stage: decomposition\n"
                    "dt_contract_version: 2\n"
                    "status: draft\n"
                    "owner_role: tech-lead\n"
                    "traces_to: [design, acceptance]\n"
                    "upstream_hashes:\n"
                    f'  design: "{design_pin}"\n'
                    f'  acceptance: "{acceptance_pin}"\n'
                    "---\n"
                    "## Задачи\n\n"
                    "#### DT-01: x · type: implement · owner: dev\n"
                    "scenarios: [BEH-01]\n"
                    "depends_on: []\n"
                    "parallel_group: solo\n\n"
                    "delivers: []\n"
                    "#### DT-02: y · type: verify · owner: qa\n"
                    "scenarios: [BEH-02]\n"
                    "depends_on: [DT-01]\n"
                    "delivered_by: [DT-01]\n"
                    "parallel_group: solo\n"
                    "delivers: []\n"
                    "verifies:\n  - tests/test_typo.py\n\n"
                    "## Инварианты графа\n\nСоблюдены.\n\n"
                    "## Порядок и параллельность\n\nПоследовательно.\n\n"
                    "## Вне объёма\n\nНичего не исключено.\n",
                    encoding="utf-8",
                )
                return 0
            if kind == "acceptance":
                self.calls.append(("author", kind))
                self.authored.append(kind)
                bundle = Path(target_dir) / bundle_dir
                req_pin = blob_sha1(
                    (bundle / "10-requirements.md").read_text(encoding="utf-8")
                )
                beh_pin = blob_sha1(
                    (bundle / "15-behaviour-spec.md").read_text(encoding="utf-8")
                )
                path = bundle / "25-acceptance.md"
                path.write_text(
                    "---\n"
                    "spec_stage: acceptance\n"
                    "status: draft\n"
                    "owner_role: qa\n"
                    "traces_to: [requirements, behaviour-spec]\n"
                    "upstream_hashes:\n"
                    f'  requirements: "{req_pin}"\n'
                    f'  behaviour-spec: "{beh_pin}"\n'
                    "---\n"
                    "#### AC-01: x · verification: manual\n"
                    "traces: [NFR-01]\n"
                    "Наблюдаемый признак: человек видит x.\n",
                    encoding="utf-8",
                )
                return 0
            return super().author(target_dir, kind, subject, bundle_dir)

    ops = _Ops(facts=GREEN_PR_FACTS)
    state = runner.start(
        **_start_kwargs(tmp_path, "r-dt-graph-warning-then-ac-stop", ops)
    )

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-dt-graph-warning-then-ac-stop")
        / "gate-findings.txt"
    ).read_text()
    assert "GC-AC-COVERAGE" in findings and "FR-01" in findings
    assert "warning GC-DT-GRAPH" in findings and "DT-02" in findings


# --- Task 9: сквозной смоук decomposition-узла + deliver ---------------------


_DT_SMOKE_BEHAVIOUR_SCENARIOS = (
    "#### BEH-01: Первый\n"
    "`traces: [FR-01]`\n"
    "- **checked_by**: `status: planned` `kind: integration` `owner: qa` "
    "`target: tests/test_a.py`\n\n"
    "#### BEH-02: Второй\n"
    "`traces: [FR-01]`\n"
    "- **checked_by**: `status: planned` `kind: e2e` `owner: qa` "
    "`target: tests/test_b.py`\n"
)

# `FakeOps.author` (общий фикстур) пишет charter/requirements БЕЗ
# frontmatter — ни один прежний тест этого не замечал, потому что ни один
# не доходил до `task_bridge.deliver()`/`stamp_bundle_approved` после
# runner-прогона: тот штампует ВЕСЬ DAG (devtools#110, урок 2 —
# «после мержа charter/requirements/behaviour-spec остаются status:
# draft»), а не только design/decomposition, и требует frontmatter на
# КАЖДОМ узле. Локальный фикстур смоука ниже несёт реалистичное
# содержимое charter/requirements (та же DSL-форма, что `governance/
# ops.py::_AUTHOR_DSL["charter"|"requirements"]` требует от реального
# author-бэкенда) — правка ограничена этим тестовым модулем, общий
# `FakeOps.author` не тронут (используется ~сотней других тестов, не
# упирающихся в deliver()).
_DT_SMOKE_CHARTER_BODY = (
    "---\n"
    "spec_stage: charter\n"
    "status: draft\n"
    "owner_role: product\n"
    "---\n"
    "# Charter\n\nТекст charter.\n"
)


def _dt_smoke_requirements_body(charter_pin: str, extra: str = "") -> str:
    return (
        "---\n"
        "spec_stage: requirements\n"
        "status: draft\n"
        "owner_role: product\n"
        "traces_to: [charter]\n"
        "upstream_hashes:\n"
        f'  charter: "{charter_pin}"\n'
        "---\n"
        "#### FR-01: x\n**Priority**: Must\n" + extra
    )


def _dt_smoke_behaviour_body(requirements_pin: str, extra: str = "") -> str:
    return (
        "---\n"
        "spec_stage: behaviour-spec\n"
        "status: draft\n"
        "owner_role: product\n"
        "traces_to: [requirements]\n"
        "upstream_hashes:\n"
        f'  requirements: "{requirements_pin}"\n'
        "---\n"
        "# Behaviour\n\n" + _DT_SMOKE_BEHAVIOUR_SCENARIOS + extra
    )


def _dt_smoke_decomposition_body(design_pin: str, acceptance_pin: str) -> str:
    """Два DT (DT-02 зависит от DT-01), сюръективно покрывающие BEH-01/02
    (общая фикстура позитивного и негативного полукруга смоука ниже).
    Двухпиновый frontmatter (design+acceptance, Task 5 плана
    acceptance-node) — тот же паттерн, что и общий `FakeOps.author`."""
    return (
        "---\n"
        "spec_stage: decomposition\n"
        "dt_contract_version: 2\n"
        "status: draft\n"
        "owner_role: tech-lead\n"
        "traces_to: [design, acceptance]\n"
        "upstream_hashes:\n"
        f'  design: "{design_pin}"\n'
        f'  acceptance: "{acceptance_pin}"\n'
        "---\n"
        "## Задачи\n\n"
        "#### DT-01: Ядро · type: implement · owner: dev\n"
        "scenarios: [BEH-01]\n"
        "depends_on: []\n"
        "parallel_group: core\n"
        "delivers: []\n"
        "Реализовать ядро.\n\n"
        "#### DT-02: Расширение · type: implement · owner: dev\n"
        "scenarios: [BEH-02]\n"
        "depends_on: [DT-01]\n"
        "parallel_group: core\n"
        "delivers: []\n"
        "Реализовать расширение.\n\n"
        "## Инварианты графа\n\nСоблюдены.\n\n"
        "## Порядок и параллельность\n\n"
        "DT-02 зависит от DT-01.\n\n"
        "## Вне объёма\n\nНичего не исключено.\n"
    )


class _DtSmokeOps(FakeOps):
    """behaviour-spec с двумя сценариями + decomposition с двумя DT-
    задачами (DT-02 зависит от DT-01), сюръективно покрывающими BEH-01/02
    — в отличие от дефолтного `FakeOps.author` (DT-01-solo), несёт ребро
    Depends on, нужное смоуку ниже для проверки рендера моста."""

    def author(
        self, target_dir: str, kind: str, subject: str, bundle_dir: str
    ) -> int:
        if kind == "charter":
            self.calls.append(("author", kind))
            self.authored.append(kind)
            path = Path(target_dir) / bundle_dir / "00-charter.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_DT_SMOKE_CHARTER_BODY, encoding="utf-8")
            return 0
        if kind == "requirements":
            self.calls.append(("author", kind))
            self.authored.append(kind)
            bundle = Path(target_dir) / bundle_dir
            charter_pin = blob_sha1(
                (bundle / "00-charter.md").read_text(encoding="utf-8")
            )
            path = bundle / "10-requirements.md"
            path.write_text(
                _dt_smoke_requirements_body(charter_pin), encoding="utf-8"
            )
            return 0
        if kind == "behaviour-spec":
            self.calls.append(("author", kind))
            self.authored.append(kind)
            bundle = Path(target_dir) / bundle_dir
            requirements_pin = blob_sha1(
                (bundle / "10-requirements.md").read_text(encoding="utf-8")
            )
            path = bundle / "15-behaviour-spec.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                _dt_smoke_behaviour_body(requirements_pin), encoding="utf-8"
            )
            return 0
        if kind == "decomposition":
            self.calls.append(("author", kind))
            self.authored.append(kind)
            bundle = Path(target_dir) / bundle_dir
            design_pin = blob_sha1(
                (bundle / "20-design.md").read_text(encoding="utf-8")
            )
            acceptance_pin = blob_sha1(
                (bundle / "25-acceptance.md").read_text(encoding="utf-8")
            )
            path = bundle / "30-decomposition.md"
            path.write_text(
                _dt_smoke_decomposition_body(design_pin, acceptance_pin),
                encoding="utf-8",
            )
            return 0
        # kind == "acceptance" (и design) идут через общий `FakeOps.author`
        # (super): пишет валидный 25-acceptance.md по ФАКТИЧЕСКОМУ
        # requirements/behaviour-spec этого фикстура (FR-01 Must из
        # `_dt_smoke_requirements_body`) — Task 5 плана acceptance-node.
        return super().author(target_dir, kind, subject, bundle_dir)


def test_decomposition_node_end_to_end_smoke_and_deliver(
    tmp_path: Path, runs_root,
) -> None:
    """Сквозной смоук 6-узлового профиля (Task 9 плана acceptance-node,
    прежде — Task 9 плана decomposition-node на 5 узлах; профиль вырос
    на узел acceptance в Task 1/5 того же плана, `_DtSmokeOps.author`
    авторит его через общий `FakeOps.author`, см. класс ниже): один
    прогон `start()` до мержа на бандле с валидным графом DT (BEH-01/
    BEH-02 покрыты DT-01/DT-02, DT-02 зависит от DT-01), затем
    `task_bridge.deliver()` на том же `target_dir` — tasks-спека несёт
    `traces_to: [decomposition]`, ровно по задаче на DT, ребро Depends on
    между ними и справочную секцию критериев приёмки уровня acceptance.

    Не дублирует то, что уже проверено по частям:
    - happy path шести author-шагов и мерж на дефолтной (DT-01-solo)
      decomposition — `test_design_node_end_to_end_smoke`;
    - render_tasks_dt изолированно (биндинги, Depends on, frontmatter) —
      `test_render_dt_one_task_per_dt_with_bindings_and_edges`/
      `test_render_dt_frontmatter_traces_decomposition_from_birth`
      (tests/test_governance_task_bridge.py);
    - рендер секции критериев приёмки изолированно —
      `test_acceptance_section_lists_criteria`/
      `test_acceptance_section_empty_input_renders_nothing`
      (tests/test_governance_task_bridge.py);
    - deliver на DT-пути изолированно, без предшествующего runner-прогона —
      `test_deliver_full_dag_renders_via_render_tasks_dt`.

    Недостающий кусок: ОДИН сквозной прогон runner → deliver на бандле,
    физически материализованном самим прогоном (не тестовой фикстурой
    напрямую), с графом из ≥2 DT-задач и рёбер depends_on, где секция AC
    материализуется из РЕАЛЬНОГО 25-acceptance.md того же прогона.
    Негативный полукруг на графе DT —
    `test_decomposition_node_smoke_bundle_with_uncovered_beh_stops_gate`
    ниже; негативный полукруг на покрытии Must-требований acceptance —
    `test_acceptance_node_smoke_bundle_with_uncovered_must_fr_stops_gate`
    ещё ниже."""
    ops = _DtSmokeOps(facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)
    run_id = "r-decomposition-e2e-smoke"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))

    # S2: все шесть author-шагов прошли РОВНО в этом порядке.
    assert ops.authored == [
        "charter", "requirements", "behaviour-spec", "design", "acceptance",
        "decomposition",
    ]
    # S4: гейт зелёный на бандле с валидным графом DT — прогон дошёл до
    # мержа, не остановился на gate/review.
    assert state.status == "completed"
    assert state.ops["merge"]["status"] == "completed"
    assert ops.merged == [(state.pr, ops.head)]

    pr = task_bridge.deliver(
        target_dir=state.target_dir,
        repo_slug=state.repo_slug,
        ws_id=state.ws_id,
        subject=state.subject,
        bundle_dir=state.bundle_dir,
        base_ref="master",
        ops=ops,
        profile=state.profile,
    )
    assert isinstance(pr, int)
    tasks_path = Path(state.target_dir) / f"spec/{state.ws_id}-tasks.md"
    text = tasks_path.read_text(encoding="utf-8")
    meta, _body = task_bridge.split_frontmatter(text)
    assert meta["traces_to"] == ["decomposition"]

    # Ровно по задаче на DT, не на BEH/Feature.
    assert "### TASK-001: Ядро" in text
    assert "### TASK-002: Расширение" in text
    assert "### TASK-003:" not in text
    assert "- [ ] реализовать BEH-01: Первый" in text
    assert "- [ ] реализовать BEH-02: Второй" in text
    # Ребро Depends on переведено из depends_on: [DT-01] задачи DT-02.
    assert "**Depends on:** [TASK-001]" in text
    task1_block = text.split("### TASK-001:")[1].split("### TASK-002:")[0]
    assert "Depends on" not in task1_block

    # Секция AC (Task 8 плана acceptance-node) материализована из
    # РЕАЛЬНОГО 25-acceptance.md, авторенного этим же прогоном (общий
    # `FakeOps.author` пишет `#### AC-01: x · verification: manual`,
    # `traces: [FR-01]`, покрывая единственное Must-требование фикстуры).
    assert "## Критерии приёмки (уровень acceptance)" in text
    assert "- **AC-01** (manual): x" in text


def test_decomposition_node_smoke_bundle_with_uncovered_beh_stops_gate(
    tmp_path: Path, runs_root,
) -> None:
    """Негативный полукруг того же смоука: ТОТ ЖЕ валидный 2-DT граф
    (`_dt_smoke_decomposition_body` — DT-01/DT-02, DT-02 зависит от
    DT-01), но behaviour-spec несёт ТРЕТИЙ сценарий (BEH-03), которым ни
    одна DT-задача не покрывает ⇒ гейт стопит `stopped_gate` с
    `GC-DT-GRAPH`, до deliver дело не доходит."""
    beh_gap_extra = (
        "\n#### BEH-03: Третий\n`traces: [FR-01]`\n"
        "- **checked_by**: `status: planned` `kind: e2e` `owner: qa` "
        "`target: tests/test_c.py`\n"
    )

    class _GapOps(_DtSmokeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            if kind == "behaviour-spec":
                self.calls.append(("author", kind))
                self.authored.append(kind)
                bundle = Path(target_dir) / bundle_dir
                requirements_pin = blob_sha1(
                    (bundle / "10-requirements.md").read_text(
                        encoding="utf-8"
                    )
                )
                path = bundle / "15-behaviour-spec.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    _dt_smoke_behaviour_body(
                        requirements_pin, extra=beh_gap_extra
                    ),
                    encoding="utf-8",
                )
                return 0
            return super().author(target_dir, kind, subject, bundle_dir)

    ops = _GapOps(facts=GREEN_PR_FACTS)
    run_id = "r-decomposition-e2e-smoke-gap"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))

    assert state.status == "stopped_gate"
    findings = (runner.run_dir(run_id) / "gate-findings.txt").read_text()
    assert "GC-DT-GRAPH" in findings and "BEH-03" in findings
    assert "push" not in state.ops


def test_acceptance_node_smoke_bundle_with_uncovered_must_fr_stops_gate(
    tmp_path: Path, runs_root,
) -> None:
    """Второй негативный полукруг того же 6-узлового смоука (Task 9 плана
    acceptance-node): граф DT валиден РОВНО как у позитивного смоука
    (BEH-01/BEH-02 покрыты DT-01/DT-02 — GC-DT-GRAPH зелен, гейт доходит
    до проверки acceptance), но requirements несёт ВТОРОЕ требование
    FR-02 (Should), а AC-01 acceptance трассирует ТОЛЬКО на него — Must-
    требование FR-01 остаётся не покрытым ни одним AC ⇒ все шесть author-
    шагов отрабатывают (S2 не зависит от S4), но гейт S4 стопит
    `stopped_gate` с `GC-AC-COVERAGE` — до deliver дело не доходит."""
    req_gap_extra = "#### FR-02: y\n**Priority**: Should\n"

    class _AcGapOps(_DtSmokeOps):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            if kind == "requirements":
                self.calls.append(("author", kind))
                self.authored.append(kind)
                bundle = Path(target_dir) / bundle_dir
                charter_pin = blob_sha1(
                    (bundle / "00-charter.md").read_text(encoding="utf-8")
                )
                path = bundle / "10-requirements.md"
                path.write_text(
                    _dt_smoke_requirements_body(
                        charter_pin, extra=req_gap_extra
                    ),
                    encoding="utf-8",
                )
                return 0
            if kind == "acceptance":
                self.calls.append(("author", kind))
                self.authored.append(kind)
                bundle = Path(target_dir) / bundle_dir
                req_pin = blob_sha1(
                    (bundle / "10-requirements.md").read_text(
                        encoding="utf-8"
                    )
                )
                beh_pin = blob_sha1(
                    (bundle / "15-behaviour-spec.md").read_text(
                        encoding="utf-8"
                    )
                )
                path = bundle / "25-acceptance.md"
                path.write_text(
                    "---\n"
                    "spec_stage: acceptance\n"
                    "status: draft\n"
                    "owner_role: qa\n"
                    "traces_to: [requirements, behaviour-spec]\n"
                    "upstream_hashes:\n"
                    f'  requirements: "{req_pin}"\n'
                    f'  behaviour-spec: "{beh_pin}"\n'
                    "---\n"
                    "## Критерии приёмки\n\n"
                    "#### AC-01: y · verification: manual\n"
                    "traces: [FR-02]\n"
                    "Наблюдаемый признак: человек видит y.\n\n"
                    "## Инварианты покрытия\n\n"
                    "Must-требования покрыты хотя бы одним AC.\n\n"
                    "## Порог приёмки\n\nAC-01 обязателен к выполнению.\n\n"
                    "## Вне объёма\n\nНичего не исключено.\n",
                    encoding="utf-8",
                )
                return 0
            return super().author(target_dir, kind, subject, bundle_dir)

    ops = _AcGapOps(facts=GREEN_PR_FACTS)
    run_id = "r-acceptance-e2e-smoke-gap"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))

    assert state.status == "stopped_gate"
    findings = (runner.run_dir(run_id) / "gate-findings.txt").read_text()
    assert "GC-AC-COVERAGE" in findings and "FR-01" in findings
    # S2 отрабатывает ВСЕ шесть author-шагов безусловно (гейт S4 идёт
    # только после — `_step_authoring` в governance/runner.py); граф DT
    # валиден, поэтому GC-DT-GRAPH пропускает, и стоп приходит только на
    # GC-AC-COVERAGE.
    assert ops.authored == [
        "charter", "requirements", "behaviour-spec", "design", "acceptance",
        "decomposition",
    ]
    assert "push" not in state.ops


def test_gate_reports_dt_contract_findings(tmp_path: Path, runs_root) -> None:
    """Находка ревью #289: проверка среза 1 не звалась ни на одном живом пути.

    `dt_contract_findings` была достижима только из тестов: S4-гейт знал про
    `graph_findings`/`non_fatal_findings`, а мост отказывал барьером по
    ДРУГОЙ причине и о дефектах `delivers` молчал. То есть оператор не
    получал ни одной новой находки формы, а сам гейт оставался зелёным.
    """
    ops = _strip_dt_contract(
        FakeOps, drop_version=False, drop_delivers=True
    )(facts=GREEN_PR_FACTS)
    state = runner.start(**_start_kwargs(tmp_path, "r-dt-contract", ops))

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-dt-contract") / "gate-findings.txt"
    ).read_text()
    assert "delivers" in findings, findings


def _strip_dt_contract(ops_cls, *, drop_version: bool, drop_delivers: bool):
    """FakeOps, чей decomposition ОТКАТЫВАЕТ часть контракта среза 3.

    Фикстура теперь пост-состояние нового авторинга — версия и `delivers`
    в ней есть. Документы, нужные тестам контракта (легаси; версия без
    `delivers`), строятся СНЯТИЕМ строки из того же тела, а не вторым
    телом: два тела разъехались бы с фикстурой на первой же её правке, и
    тест проверял бы документ, которого конвейер не производит.
    """

    class _Ops(ops_cls):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            rc = super().author(target_dir, kind, subject, bundle_dir)
            if kind != "decomposition":
                return rc
            path = Path(target_dir) / bundle_dir / "30-decomposition.md"
            text = path.read_text(encoding="utf-8")
            if drop_version:
                assert "dt_contract_version: 2\n" in text, text
                text = text.replace("dt_contract_version: 2\n", "", 1)
            if drop_delivers:
                assert "delivers: []\n" in text, text
                text = text.replace("delivers: []\n", "", 1)
            path.write_text(text, encoding="utf-8")
            return rc

    return _Ops


def test_gate_passes_legacy_dt_but_says_guarantee_is_absent(
    tmp_path: Path, runs_root
) -> None:
    """Базовая половина к стопу ниже: режим совместимости НЕ красит гейт.

    Без неё утверждение «гейт краснеет на документе без версии»
    удовлетворял бы и гвард, красящий всё подряд. Здесь же вторая
    половина: пропуск обязан быть ГРОМКИМ — оператор видит, что гарантии
    переноса нет.

    Со среза 3 дефолт строгий, и режим включает ТОЛЬКО оператор явным
    `allow_legacy_dt=True`. Тем самым тест пинует и то, что решение
    оператора доезжает до гварда: зашей врезка `False`, легаси-документ
    краснел бы и здесь.
    """
    ops = _strip_dt_contract(
        FakeOps, drop_version=True, drop_delivers=True
    )(facts=GREEN_PR_FACTS)
    state = runner.start(
        **_start_kwargs(tmp_path, "r-dt-legacy", ops, allow_legacy_dt=True)
    )

    assert state.status != "stopped_gate"
    findings = (
        runner.run_dir("r-dt-legacy") / "gate-findings.txt"
    ).read_text(encoding="utf-8")
    assert "GC-DT-CONTRACT" in findings, findings
    assert "ГАРАНТИЯ ПЕРЕНОСА" in findings, findings


def test_gate_refuses_a_versionless_bundle_by_default(
    tmp_path: Path, runs_root
) -> None:
    """Со среза 3 дефолт строгий: документ без версии краснеет сам.

    Авторинг выпускает `dt_contract_version: 2`, поэтому отсутствие поля
    значит чужой или старый бандл, а не «конвейер так умеет». Отсутствие
    версии режим совместимости НЕ включает — его включает оператор,
    явно; иначе новый документ с забытым полем молча обошёл бы контракт,
    то есть барьер отключался бы ровно тем, от чего защищает.

    Парой с базовой половиной выше этот тест пинует и проводку решения
    оператора: зашей врезка любое из двух значений, одна из двух половин
    покраснеет.
    """
    ops = _strip_dt_contract(
        FakeOps, drop_version=True, drop_delivers=True
    )(facts=GREEN_PR_FACTS)
    state = runner.start(**_start_kwargs(tmp_path, "r-dt-strict", ops))

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-dt-strict") / "gate-findings.txt"
    ).read_text(encoding="utf-8")
    assert "error GC-DT-CONTRACT" in findings, findings
    assert "dt_contract_version" in findings, findings


def test_allow_legacy_dt_survives_resume(tmp_path: Path, runs_root) -> None:
    """Решение оператора переживает перезапуск: поле состояния, не аргумент.

    Иначе прогон, начатый в строгом режиме, после `resume` судил бы тот же
    документ переходным дефолтом — и зеленел бы на том, на чём встал.
    """
    ops = _strip_dt_contract(
        FakeOps, drop_version=True, drop_delivers=True
    )(facts=GREEN_PR_FACTS)
    runner.start(
        **_start_kwargs(
            tmp_path, "r-dt-resume", ops, allow_legacy_dt=True
        )
    )

    # Пинуется НЕдефолтное значение: `False` совпало бы с дефолтом, и тест
    # проходил бы, даже если поле не сохраняется вовсе.
    assert runner.load("r-dt-resume").allow_legacy_dt is True


def _with_delivers(ops_cls, source_ref: str):
    """FakeOps, чей DT-01 объявляет версию 2 и один результат поставки.

    Правится УЖЕ написанный фикстурой документ — тест не заводит второго
    тела decomposition и потому не разъезжается с ней на первой же правке.
    """

    class _Ops(ops_cls):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            rc = super().author(target_dir, kind, subject, bundle_dir)
            if kind != "decomposition":
                return rc
            path = Path(target_dir) / bundle_dir / "30-decomposition.md"
            text = path.read_text(encoding="utf-8")
            assert "delivers: []\n" in text, text
            text = text.replace(
                "delivers: []\n",
                "delivers:\n"
                "  - id: DEL-01\n"
                "    kind: capability\n"
                '    statement: "x отвергает пустой ввод"\n'
                "    sources:\n"
                f'      - "{source_ref}"\n',
                1,
            )
            path.write_text(text, encoding="utf-8")
            return rc

    return _Ops


def test_gate_resolves_delivers_sources_against_bundle_nodes(
    tmp_path: Path, runs_root
) -> None:
    """Базовая половина: разрешимая ссылка проходит гейт.

    Без неё «гейт краснеет на неразрешимой ссылке» удовлетворялось бы и
    гейтом, который не построил индекс вовсе: пустой индекс отвергает
    ЛЮБУЮ ссылку, и красный был бы одинаков для верной и для битой.
    """
    ops = _with_delivers(FakeOps, "acceptance#AC-01")(facts=GREEN_PR_FACTS)
    state = runner.start(**_start_kwargs(tmp_path, "r-del-ok", ops))

    findings_path = runner.run_dir("r-del-ok") / "gate-findings.txt"
    findings = (
        findings_path.read_text(encoding="utf-8")
        if findings_path.exists()
        else ""
    )
    assert state.status != "stopped_gate", findings
    assert "error GC-DT-CONTRACT" not in findings, findings


def test_gate_stops_on_unresolvable_delivers_source(
    tmp_path: Path, runs_root
) -> None:
    """Ссылка на несуществующий пункт — стоп, а не молчание.

    Неразрешимая ссылка значит, что обязательство привязано к тексту,
    которого в бандле нет: ревьюер не сможет свериться с источником, а
    редактура, которая его «переименовала», не оставит следа.
    """
    ops = _with_delivers(FakeOps, "acceptance#AC-99")(facts=GREEN_PR_FACTS)
    state = runner.start(**_start_kwargs(tmp_path, "r-del-bad", ops))

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-del-bad") / "gate-findings.txt"
    ).read_text(encoding="utf-8")
    assert "GC-DT-CONTRACT" in findings, findings
    assert "AC-99" in findings, findings


def test_gate_resolves_a_delivers_source_in_charter(
    tmp_path: Path, runs_root
) -> None:
    """charter — адресуемый узел наравне с остальными.

    Классы `CON-*`/`M-*`/`OUT-*` живут ТОЛЬКО в charter: ниже по
    конвейеру их нет вовсе (requirements несёт FR/NFR, design — Q,
    acceptance — AC, behaviour-spec — BEH). Не будь charter в индексе, у
    результата поставки, происходящего от ограничения, не было бы в
    бандле ни одного законного адреса — ровно это и случилось на боевом
    прогоне review-pr-unreachable-base-coverage-20260921.
    """
    ops = _with_delivers(FakeOps, "charter#CON-01")(facts=GREEN_PR_FACTS)
    state = runner.start(**_start_kwargs(tmp_path, "r-del-charter", ops))

    findings_path = runner.run_dir("r-del-charter") / "gate-findings.txt"
    findings = (
        findings_path.read_text(encoding="utf-8")
        if findings_path.exists()
        else ""
    )
    assert state.status != "stopped_gate", findings
    assert "error GC-DT-CONTRACT" not in findings, findings


def test_gate_index_covers_exactly_the_declared_bundle_composition(
    tmp_path: Path, runs_root
) -> None:
    """Состав индекса = объявленный состав бандла, не список гейта.

    Проверяется через наблюдаемое поведение, а не чтением кода: ссылка на
    КАЖДЫЙ узел активного DAG обязана разрешаться, а ссылка на нулевой
    узел discovery (`00-discovery/brief.md`, в `BUNDLE_DAG` не входит) —
    отвергаться как «узла нет». Иначе граница «что адресуемо» держалась
    бы на том, что читатель заметил кортеж в гейте.
    """
    declared = set(bundle_dag.composition(bundle_dag.dag_for(None)))
    assert "charter" in declared and "discovery-brief" not in declared

    ops = _with_delivers(FakeOps, "discovery-brief#G-01")(
        facts=GREEN_PR_FACTS
    )
    state = runner.start(**_start_kwargs(tmp_path, "r-del-brief", ops))

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-del-brief") / "gate-findings.txt"
    ).read_text(encoding="utf-8")
    assert "discovery-brief" in findings, findings
    assert "узел" in findings, findings


def test_gate_rejects_an_ambiguous_delivers_source(
    tmp_path: Path, runs_root
) -> None:
    """Дважды определённый пункт не разрешается молча.

    Кратность обязана дожить от чтения файла до проверки ссылки: собери
    гейт индекс множеством — и повтор исчез бы раньше, чем его проверили,
    а ссылка указала бы на одно из двух определений без права на выбор.
    """

    class _Doubled(_with_delivers(FakeOps, "charter#CON-01")):
        def author(
            self, target_dir: str, kind: str, subject: str, bundle_dir: str
        ) -> int:
            rc = super().author(target_dir, kind, subject, bundle_dir)
            if kind == "charter":
                path = Path(target_dir) / bundle_dir / "00-charter.md"
                path.write_text(
                    path.read_text(encoding="utf-8")
                    + "\n#### CON-01: оно же второй раз\n\nдругой текст\n",
                    encoding="utf-8",
                )
            return rc

    ops = _Doubled(facts=GREEN_PR_FACTS)
    state = runner.start(**_start_kwargs(tmp_path, "r-del-dup", ops))

    assert state.status == "stopped_gate"
    findings = (
        runner.run_dir("r-del-dup") / "gate-findings.txt"
    ).read_text(encoding="utf-8")
    assert "CON-01" in findings, findings
    assert "неоднозначн" in findings, findings


def test_run_json_without_the_compat_field_resumes_strictly(
    tmp_path: Path, runs_root
) -> None:
    """Дефолт поля судит СТАРЫЕ run.json — те, что записаны до среза 1.

    `RunState(**json.loads(raw))` берёт значение из дефолта дата-класса,
    когда ключа в файле нет. На пути `start()` этот дефолт не виден вовсе
    (координатор всегда передаёт значение явно), поэтому мутант,
    откативший его в `True`, выживал: ни один тест не читал run.json без
    поля.

    Правило одно и то же на обоих входах: ОТСУТСТВИЕ не включает режим
    совместимости. Ни отсутствие версии в документе, ни отсутствие ключа
    в состоянии — иначе барьер отключался бы ровно тем, от чего защищает,
    и достаточно было бы предъявить файл постарше.
    """
    ops = FakeOps(facts=GREEN_PR_FACTS)
    runner.start(**_start_kwargs(tmp_path, "r-old-state", ops))
    path = runner.run_dir("r-old-state") / "run.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["allow_legacy_dt"]
    path.write_text(json.dumps(raw), encoding="utf-8")

    assert runner.load("r-old-state").allow_legacy_dt is False
