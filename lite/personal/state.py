"""Постоянное состояние личного прогона `dfl` (вне `RUNS_ROOT` wave-раннера)."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from dataclasses import asdict, dataclass, fields
from datetime import date
from pathlib import Path

from governance import run_state as rs
from governance import spec_loop

PERSONAL_ROOT = rs.RUNS_ROOT.parent / "personal-runs"

STEPS = (
    "interview",
    "design",
    "decomposition",
    "spec-pr",
    "code-run",
    "code-pr",
    "done",
)


@dataclass
class PersonalRun:
    """Состояние личного прогона; `step` — текущий шаг из `STEPS`."""

    run_id: str
    ws_id: str
    subject: str
    repo: str
    repo_slug: str
    target_dir: str
    bundle_dir: str
    step: str = "interview"
    base_ref: str = "master"
    spec_pr: int | None = None
    code_pr: int | None = None
    stop_reason: str = ""


def _ws_id(subject: str, today: date) -> str:
    """ws-id из subject; для не-ASCII темы (кириллица) — `personal-<дата>-<hex>`."""
    try:
        return spec_loop.ws_id_for(subject, today)
    except spec_loop.SpecLoopError:
        return f"personal-{today:%Y%m%d}-{secrets.token_hex(2)}"


def new_personal_run(
    subject: str, repo: str, repo_slug: str, target_dir: str, today: date
) -> PersonalRun:
    """Новый прогон: `run_id` = ws-id + случайный суффикс."""
    ws_id = _ws_id(subject, today)
    return PersonalRun(
        run_id=f"{ws_id}-{secrets.token_hex(3)}",
        ws_id=ws_id,
        subject=subject,
        repo=repo,
        repo_slug=repo_slug,
        target_dir=target_dir,
        bundle_dir=f"workstreams/{ws_id}/spec",
    )


def run_dir(run_id: str) -> Path:
    """Каталог прогона под `PERSONAL_ROOT` (run_id валидируется)."""
    rs.validate_id_component(run_id, label="run_id")
    return PERSONAL_ROOT / run_id


def save(run: PersonalRun) -> None:
    """Атомарная запись `run.json`: temp в том же каталоге + `os.replace`."""
    target = run_dir(run.run_id)
    target.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(run), ensure_ascii=False, indent=2, sort_keys=True)
    fd, tmp_name = tempfile.mkstemp(dir=target, prefix=".run.json.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_name, target / "run.json")
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def load(run_id: str) -> PersonalRun:
    """Читает `run.json`; незнакомые ключи игнорируются."""
    raw = json.loads((run_dir(run_id) / "run.json").read_text(encoding="utf-8"))
    known = {f.name for f in fields(PersonalRun)}
    return PersonalRun(**{k: v for k, v in raw.items() if k in known})


def active_for(repo_slug: str) -> PersonalRun | None:
    """Последний по mtime незавершённый прогон репо или `None`."""
    if not PERSONAL_ROOT.is_dir():
        return None
    files = sorted(
        PERSONAL_ROOT.glob("*/run.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for path in files:
        run = load(path.parent.name)
        if run.repo_slug == repo_slug and run.step != "done":
            return run
    return None
