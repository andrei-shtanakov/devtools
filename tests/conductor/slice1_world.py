"""Мир для тестов последовательностей среза 1: планировщик → настоящий
Writer → фейковый GitHub и локальные git-репо (спека среза 1, §11).

Повторная проверка основания (§5.1 шаг 8, регрессия P1-1 ревью планов):
между планированием и отправкой, а также между двумя шагами меняется
ответ, период, факт, тег или движение — мутаций после изменения нет.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from conductor.fresh import FreshReader
from conductor.host_config import HostConfig
from conductor.journal import MutationLog, RunJournal
from conductor.opstate import init_state, open_state
from conductor.roadmap import parse_roadmap
from conductor.writer import PlanRecord, StepReport, Writer
from tests.conductor.fake_app import FakeClient
from tests.conductor.fixtures import EPICS, ROADMAP
from tests.conductor.slice1_fixtures import NOW

CFG = HostConfig(
    app_id=1,
    installation_id=2,
    private_key=Path("/k"),
    profile="fleet",
    shadow=False,
    state_dir=Path("/s"),
)
UMB = "own/ai-orchestrators-workspace"
FENCE = frozenset({"own/a", "own/b", UMB})
GOAL = "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 @blocked_by:todo://b/b\n"
GOAL_FREE = "- [ ] g @owner:github:own @id:goal @epic:eco.focus1\n"
B_OPEN = "- [ ] b @owner:TBD @id:b @epic:eco.bg\n"
B_DONE = "- [x] b @owner:TBD @id:b @epic:eco.bg\n"


def git(repo: Path, *args: str) -> str:
    """git в репо теста; ошибка — исключение."""
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(repo: Path, todo: str, msg: str = "c") -> str:
    """Коммит TODO.md (репо создаётся при первом вызове); вернуть SHA."""
    if not (repo / ".git").exists():
        repo.mkdir(parents=True, exist_ok=True)
        git(repo, "init", "-q", "-b", "master")
        git(repo, "config", "user.email", "t@t")
        git(repo, "config", "user.name", "t")
    (repo / "TODO.md").write_text(todo, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


def roadmap(*actions: str):
    """Роадмап autonomy=1 с включёнными actions."""
    text = ROADMAP.replace(
        "autonomy = 0", f"autonomy = 1\nenabled_actions = {json.dumps(list(actions))}"
    )
    rm = parse_roadmap(text, EPICS)
    assert rm.valid, rm.findings
    return rm


class World:
    """FakeClient, git-репо a и b, состояние и исполнитель."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.client = FakeClient()
        init_state(tmp / "state", NOW - timedelta(hours=3))
        opened = open_state(tmp / "state", NOW)
        assert opened.state is not None
        self.state = opened.state
        self.fresh = FreshReader(self.client, self.clone)

    def clone(self, key: str) -> tuple[Path, str] | None:
        repo = self.tmp / key
        return (repo, "master") if (repo / ".git").exists() else None

    def run(
        self,
        records: list[PlanRecord],
        action: str,
        between: Callable[[], None] | None = None,
    ) -> list[StepReport]:
        """Исполнить; between — изменение мира перед ВТОРЫМ шагом."""
        calls = {"n": 0}

        def load():
            calls["n"] += 1
            if calls["n"] == 2 and between is not None:
                between()
            return roadmap(action)

        writer = Writer(
            cfg=CFG,
            client=self.client,
            state=self.state,
            log=MutationLog(self.tmp / "run"),
            journal=RunJournal(self.tmp / "run"),
            fence=FENCE,
            load_roadmap=load,
            hostname="vps",
            run_id="r1",
            level_cap=3,
            clock=lambda: NOW,
        )
        return writer.execute(records)


def outcomes(reports: list[StepReport]) -> list[tuple[str, str]]:
    """(исход, причина) по шагам."""
    return [(r.outcome, r.reason) for r in reports]
