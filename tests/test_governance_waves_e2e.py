"""Сквозной прогон волн на настоящем git (devtools#479, пункт 2).

`FakeOps.commit_paths` git не трогает — поэтому класс «в `commit_paths` уходит
путь, который обычный `git add` не добавит» (игнорирован `.gitignore` цели
либо отсутствует на свежей base следующей волны) прошёл мимо всех
тестов раннера: `workstreams/codes.toml` (ревью среза 1), D1 (профиль стадии
в доставке), D2 (source-слой 00-discovery в candidate).

Здесь подменено ТОЛЬКО платное и внешнее: автор, гейты steward, ревьюер
edge-check (координатор настоящий, `call` — фальшивая модель), форджа (PR,
ревью, мерж — стенд `test_governance_approve_node`; листинг открытых PR —
`gh pr list`, подменён один он). Всё, что делает git, — настоящее: ветки
волн, коммиты бандла, candidate/finalize, мержи в origin, S8, доставка
tasks-спеки, fetch кодов charter'ов перед штампом (#481 C-a). Цель несёт `.gitignore` spec-runner (дословная
выдержка, см. `_GITIGNORE_*`) — в нынешнем виде и в прежнем, до
разыгнорирования `00-discovery/` (spec-runner 22.09).

Мутационная проверка при заведении (2026-09-29): откат D1/D2 в
`RealOps.commit_paths` валит оба варианта (`current` — на доставке, профиль
стадии под `/spec/*`; `pre-discovery` — на W1, 00-discovery); откат только D2
валит `pre-discovery`; прежний реестр `workstreams/codes.toml` в
`_commit_bundle` валит оба на W2 (путь отсутствует на свежей base).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from governance import approval_facts as af
from governance import approve_node as an
from governance import brief_input, runner, task_bridge
from governance import run_state as rs
from governance.charter_guard import read_charter
from governance.edge_check import coordinator as co
from governance.edge_check import rules as edge_rules
from governance.frontmatter import split_frontmatter
from governance.stale_adapter import blob_sha1
from tests.test_governance_approve_node import (
    HUMAN,
    Forge,
    _merge_into_origin,
)
from tests.test_governance_approve_node import Ops as ForgeOps
from tests.test_governance_brief_input import customer_brief, engineer_brief

WS_ID = "WS-T1"
BUNDLE = f"workstreams/{WS_ID}/spec"
SLUG = "owner/alpha"
CODE = "ENC"
#: Открытый candidate W1 чужого воркстрима в origin стенда: профилактика
#: коллизии кода (спека оракула §1.2) забирает его настоящим fetch.
FOREIGN_W1 = "spec/WS-T2-approve-1-1-1"
PLAN_ITEM = "todo://alpha/enc-oracle"
EDGE_CONTRACTS = Path("contracts/edge-check/v1")
_PROFILE = Path(__file__).resolve().parent.parent / "profiles" / "team-exp.yaml"

#: `.gitignore` spec-runner @ 059e8d8, строки про `spec/` — дословно.
_GITIGNORE_COMMON = (
    "spec/\n"
    "!docs/plans/*/spec/\n"
    "!docs/plans/*/spec/**\n"
    "!workstreams/*/spec/\n"
    "workstreams/*/spec/*\n"
    "!workstreams/*/spec/*.md\n"
)
_GITIGNORE_DISCOVERY = (
    "!workstreams/*/spec/00-discovery/\n"
    "workstreams/*/spec/00-discovery/*\n"
    "!workstreams/*/spec/00-discovery/*.md\n"
)
_GITIGNORE_ROOT_SPEC = (
    "!/spec/\n"
    "/spec/*\n"
    "!/spec/*-tasks.md\n"
    "!/spec/negative-controls/\n"
    "!/spec/negative-controls/*.patch\n"
)
GITIGNORES = {
    "current": _GITIGNORE_COMMON + _GITIGNORE_DISCOVERY + _GITIGNORE_ROOT_SPEC,
    # До 22.09: 00-discovery/ не разыгнорирован — бриф едет только force-add.
    "pre-discovery": _GITIGNORE_COMMON + _GITIGNORE_ROOT_SPEC,
}


def _git(where: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(where), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _edge_answer(prompt: str) -> str:
    """Фальшивая модель edge-check: все пункты набора — pass, находок нет.
    Ребро узнаётся по тексту первого пункта (промпт перечисляет их дословно)."""
    for path in sorted((EDGE_CONTRACTS / "rules").glob("*.yaml")):
        ruleset = edge_rules.load_rules(path.stem, EDGE_CONTRACTS)
        if ruleset.items[0].text in prompt:
            return json.dumps(
                {
                    "structured_output": {
                        "criteria": [
                            {"id": it.id, "status": "pass", "reason": "ок"}
                            for it in ruleset.items
                        ],
                        "findings": [],
                    }
                }
            )
    raise AssertionError("ребро в промпте edge-check не опознано")


#: Узел → (файл, основания для `upstream_hashes`, тело). Гейты steward на
#: стенде подменены, поэтому тела — минимум, который читает мост доставки
#: (BEH → AC → DT), а пины — настоящие blob'ы оснований из worktree.
_NODES: dict[str, tuple[str, tuple[str, ...], str]] = {
    "requirements": (
        "10-requirements.md",
        ("charter",),
        (
            "#### FR-01: x\n**Priority**: Must\n\n#### NFR-01: Safety\n"
            "**Priority**: Should\n"
        ),
    ),
    "behaviour-spec": (
        "15-behaviour-spec.md",
        ("requirements",),
        (
            "#### BEH-01: Первый\n`traces: [FR-01]`\n- **checked_by**: "
            "`status: planned` `kind: integration` `owner: qa` "
            "`target: tests/test_a.py`\n"
        ),
    ),
    "design": (
        "20-design.md",
        ("requirements", "behaviour-spec"),
        "Открытых архитектурных вопросов нет (входной набор пуст)\n",
    ),
    "acceptance": (
        "25-acceptance.md",
        ("requirements", "behaviour-spec"),
        (
            "## Критерии приёмки\n\n#### AC-01: x · verification: manual\n"
            "traces: [FR-01]\nscenarios: [BEH-01]\nНаблюдаемый признак: x.\n\n"
            "## Инварианты покрытия\n\nПокрыты.\n\n## Порог приёмки\n\nAC-01.\n\n"
            "## Вне объёма\n\nНичего.\n"
        ),
    ),
    "decomposition": (
        "30-decomposition.md",
        ("design", "acceptance"),
        (
            "## Задачи\n\n#### DT-01: Ядро · type: implement · owner: dev\n"
            "scenarios: [BEH-01]\ndepends_on: []\nparallel_group: core\n"
            "delivers: []\nРеализовать ядро.\n\n## Инварианты графа\n\nСоблюдены.\n\n"
            "## Порядок и параллельность\n\nОдна задача.\n\n## Вне объёма\n\nНичего.\n"
        ),
    ),
}
_FILES = {"charter": "00-charter.md", **{k: v[0] for k, v in _NODES.items()}}


def _frontmatter(stage: str, pins: dict[str, str]) -> str:
    extra = "dt_contract_version: 2\n" if stage == "decomposition" else ""
    return (
        f"---\nspec_stage: {stage}\n{extra}status: draft\nowner_role: product\n"
        f"traces_to: [{', '.join(pins)}]\nupstream_hashes:\n"
        + "".join(f'  {name}: "{blob}"\n' for name, blob in pins.items())
        + "---\n"
    )


class E2EOps(ForgeOps):
    """Настоящий git + форджа стенда; платное и внешнее — подменено."""

    def __init__(self, forge: Forge) -> None:
        super().__init__(forge)
        self.comments: list[tuple[int, str]] = []
        self.issues: list[tuple[str, str]] = []

    def author(self, target_dir, kind, subject, bundle_dir, brief_context=None):
        bundle = Path(target_dir) / bundle_dir
        if kind == "charter":
            # source-гард брифа: charter пинует оба source-файла.
            assert brief_context is not None
            text = _frontmatter("charter", dict(brief_context["source_blobs"]))
            (bundle / _FILES[kind]).write_text(
                text + "# Charter\n\n#### CON-01: x\n", encoding="utf-8"
            )
            return 0
        fname, bases, body = _NODES[kind]
        pins = {
            b: blob_sha1((bundle / _FILES[b]).read_text(encoding="utf-8"))
            for b in bases
        }
        (bundle / fname).write_text(_frontmatter(kind, pins) + body, encoding="utf-8")
        return 0

    def gate_check_candidate(self, target_dir, bundle_dir, profile):
        return 0, ""

    def gate_check_s8(self, target_dir, bundle_dir, profile):
        return 0, ""

    def collect_gate_verdicts(self, target_dir: str, dest: str) -> bool:
        Path(dest).write_text('{"verdict": "PASS"}\n', encoding="utf-8")
        return True

    def edge_check_level(self, state, run_dir, wave, profile_path):
        return co.run_level(state, self, run_dir, wave, profile_path, call=_edge_answer)

    def comment(self, repo_slug: str, pr: int, body: str) -> None:
        self.comments.append((pr, body))

    def create_issue(self, repo_slug: str, title: str, body: str) -> int:
        self.issues.append((title, body))
        return 900 + len(self.issues)

    def find_issue(self, repo_slug: str, body_prefix: str) -> int | None:
        return None

    def create_draft_pr(self, target_dir, repo_slug, branch, title, body, label):
        return self.create_pr(
            target_dir, repo_slug, branch, title, body, label, draft=True
        )


@pytest.fixture(autouse=True)
def _hermetic(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("claude-home")
    monkeypatch.setattr(runner, "_claude_home", lambda: home)
    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path_factory.mktemp("runs"))
    monkeypatch.delenv(af.APPROVER_ALLOWLIST_ENV, raising=False)
    monkeypatch.setattr(an, "_SLEEP", lambda seconds: None)
    monkeypatch.setenv("AI_PROSTO_HARNESS_ENV", "/nonexistent")


def _world(tmp_path: Path, gitignore: str) -> tuple[Path, Forge, E2EOps]:
    """origin + target (раннер) + human (мержи), identity репо-локальная."""
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "master", str(origin)], check=True
    )
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "master", str(seed)], check=True)
    (seed / ".gitignore").write_text(gitignore, encoding="utf-8")
    profiles = seed / "profiles"
    profiles.mkdir()
    (profiles / "team-exp.yaml").write_bytes(_PROFILE.read_bytes())
    for sib in ("roles.yaml", "gate-catalog.yaml"):
        (profiles / sib).write_text(f"# {sib}\n", encoding="utf-8")
    (seed / "README.md").write_text("alpha\n", encoding="utf-8")
    for args in (
        ("config", "user.email", "t@e.st"),
        ("config", "user.name", "test"),
        ("add", "-A"),
        ("commit", "-qm", "seed"),
        ("remote", "add", "origin", str(origin)),
        ("push", "-q", "-u", "origin", "master"),
    ):
        _git(seed, *args)
    _push_foreign_w1(seed, "OTH")
    target, human = tmp_path / "target", tmp_path / "human"
    for clone in (target, human):
        subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
        _git(clone, "config", "user.email", "t@e.st")
        _git(clone, "config", "user.name", "test")
    forge = Forge(origin=origin, merger=human)
    return target, forge, E2EOps(forge)


def _push_foreign_w1(seed: Path, code: str) -> None:
    """Ветка `FOREIGN_W1` в origin: charter схемы 2 воркстрима WS-T2."""
    _git(seed, "switch", "-q", "-C", FOREIGN_W1, "master")
    charter = seed / "workstreams" / "WS-T2" / "spec" / "00-charter.md"
    charter.parent.mkdir(parents=True, exist_ok=True)
    charter.write_text(
        f"---\nschema: 2\ncode: {code}\nplan_item: todo://alpha/t2\n---\n# C\n",
        encoding="utf-8",
    )
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", f"charter WS-T2 {code}")
    _git(seed, "push", "-q", "--force", "origin", FOREIGN_W1)
    _git(seed, "switch", "-q", "master")


@pytest.fixture
def gh_open_prs(monkeypatch) -> list[list[str]]:
    """`gh pr list` отвечает открытым `FOREIGN_W1`; остальное — настоящее.

    Подменён ровно листинг: fetch candidate и `charters_at` в
    `RealOps.charter_codes_elsewhere` исполняются против origin стенда.
    """
    real_run = subprocess.run
    calls: list[list[str]] = []

    def run(argv, *args, **kwargs):
        if list(argv[:3]) != ["gh", "pr", "list"]:
            return real_run(argv, *args, **kwargs)
        calls.append(list(argv))
        out = json.dumps([{"headRefName": FOREIGN_W1}])
        return subprocess.CompletedProcess(argv, 0, out, "")

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def _criteria_refs(target: Path) -> set[str]:
    refs = _git(target, "for-each-ref", "--format=%(refname)", "refs/criteria-codes")
    return set(refs.split())


def _brief(tmp_path: Path):
    src = tmp_path / "brief-src"
    src.mkdir()
    (src / "customer.md").write_text(
        customer_brief(status="approved"), encoding="utf-8"
    )
    (src / "brief.md").write_text(engineer_brief("customer.md"), encoding="utf-8")
    return brief_input.inspect_brief(src / "brief.md")


def _assert_clean(target: Path, where: str) -> None:
    dirty = _git(target, "status", "--porcelain")
    assert dirty == "", f"{where}: дерево цели грязное:\n{dirty}"


def _drive(state: rs.RunState, ops: E2EOps, target: Path) -> rs.RunState:
    """advance → человек мержит candidate → resume (finalize агентом) → …"""
    for _ in range(12):
        if state.status != "waiting_human_merge":
            return state
        _assert_clean(target, f"пауза волны {state.wave}")
        request = state.ops[f"candidate-{state.wave}"]["request"]
        candidate = state.ops[request]["candidate_pr"]
        _merge_into_origin(
            ops.forge, candidate, login=HUMAN, when="2026-09-29T08:00:00Z"
        )
        state = runner.resume(state.run_id, ops)
    raise AssertionError(f"прогон не вышел из цикла волн: {state.status}")


@pytest.mark.parametrize("rules", sorted(GITIGNORES))
def test_waves_run_end_to_end_on_real_git(
    tmp_path: Path, rules: str, gh_open_prs: list[list[str]]
) -> None:
    target, forge, ops = _world(tmp_path, GITIGNORES[rules])
    state = runner.start(
        subject="сквозной прогон",
        repo="alpha",
        repo_slug=SLUG,
        ws_id=WS_ID,
        target_dir=str(target),
        bundle_dir=BUNDLE,
        profile="profiles/team-exp.yaml",
        run_id=f"r-e2e-{rules}",
        ops=ops,
        brief_source=_brief(tmp_path),
        code=CODE,
        plan_item=PLAN_ITEM,
    )
    state = _drive(state, ops, target)
    assert state.status == "completed", (
        state.status,
        [p.read_text() for p in rs.run_dir(state.run_id).glob("*.txt")],
    )
    human_merged = [
        p
        for p in forge.prs.values()
        if p["state"] == "MERGED" and p["mergedBy"]["login"] == HUMAN
    ]
    assert state.wave == 5 and len(human_merged) == 5, "по candidate на волну"
    _git(target, "fetch", "-q", "origin")
    base = set(_git(target, "ls-tree", "-r", "--name-only", "origin/master").split())
    for name in (
        "00-charter.md",
        "10-requirements.md",
        "15-behaviour-spec.md",
        "20-design.md",
        "25-acceptance.md",
        "30-decomposition.md",
    ):
        text = _git(target, "show", f"origin/master:{BUNDLE}/{name}")
        assert split_frontmatter(text)[0]["status"] == "approved", name
    assert {
        f"{BUNDLE}/00-discovery/brief.md",
        f"{BUNDLE}/00-discovery/customer.md",
    } <= base
    assert any(p.startswith(f"workstreams/{WS_ID}/evidence/edge-check/") for p in base)
    charter = _git(target, "show", f"origin/master:{BUNDLE}/00-charter.md")
    assert read_charter(charter).code == CODE
    assert gh_open_prs and all(c[3:5] == ["-R", SLUG] for c in gh_open_prs)
    assert _criteria_refs(target) == {
        "refs/criteria-codes/master",
        f"refs/criteria-codes/{FOREIGN_W1}",
    }

    number = task_bridge.deliver_for_run(state, ops)
    _assert_clean(target, "после доставки")
    pr = forge.prs[number]
    assert pr["draft"] and pr["branch"] == f"spec/{WS_ID}-tasks"
    delivered = set(
        _git(
            forge.origin, "ls-tree", "-r", "--name-only", f"refs/heads/{pr['branch']}"
        ).split()
    )
    assert f"spec/{WS_ID}-tasks.md" in delivered
    assert any(p.startswith("spec/profiles/") for p in delivered), delivered


def test_code_taken_by_foreign_w1_candidate_stops_before_stamp(
    tmp_path: Path, gh_open_prs: list[list[str]]
) -> None:
    """Коллизия, видимая только через настоящий fetch чужого candidate W1:
    стоп `stopped_preflight` до штампа, charter остаётся схемой 1 (#481 C-a)."""
    target, forge, ops = _world(tmp_path, GITIGNORES["current"])
    _push_foreign_w1(tmp_path / "seed", CODE)
    state = runner.start(
        subject="коллизия кода",
        repo="alpha",
        repo_slug=SLUG,
        ws_id=WS_ID,
        target_dir=str(target),
        bundle_dir=BUNDLE,
        profile="profiles/team-exp.yaml",
        run_id="r-e2e-taken",
        ops=ops,
        brief_source=_brief(tmp_path),
        code=CODE,
        plan_item=PLAN_ITEM,
    )
    assert state.status == "stopped_preflight", state.status
    assert not forge.prs, "W1 не открыт"
    charter = (target / BUNDLE / "00-charter.md").read_text(encoding="utf-8")
    assert read_charter(charter).schema == 1
