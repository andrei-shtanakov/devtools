"""`tasks.md` личного пути: рендер из декомпозиции без frontmatter."""

from __future__ import annotations

from governance import decomposition_guard, task_bridge
from lite.personal.check import split_decomposition
from lite.personal.state import PersonalRun

TASKS_PATH = "spec/{ws_id}-tasks.md"


def _strip_frontmatter(text: str) -> str:
    """Срезает ведущий блок `---…---` (Ruling 3: файл не управляемый)."""
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 3)
    return text if end == -1 else text[end + 5 :].lstrip("\n")


def render_personal_tasks(
    run: PersonalRun,
    decomposition_text: str,
    design_text: str,
    generated_at: str,
) -> str:
    """tasks.md для spec-runner: 1 DT = 1 задача, без frontmatter."""
    _, whole = split_decomposition(decomposition_text)
    scenarios = task_bridge.parse_behaviour(whole)
    dt_tasks, _ = decomposition_guard.parse_dt_tasks(whole)
    text = task_bridge.render_tasks_dt(
        run.ws_id,
        run.subject,
        f"{run.bundle_dir}/30-decomposition.md",
        scenarios,
        dt_tasks,
        generated_at,
        anchor_blob=None,
        design_text=design_text,
    )
    return _strip_frontmatter(text)
