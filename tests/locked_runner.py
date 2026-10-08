"""Вызовы раннера под блокировкой прогона — как у точек входа (§11.4.5).

Функции раннера требуют токен `RunLock`; тесты, которые зовут их напрямую,
берут блокировку здесь, ровно как spec-loop и CLI раннера.
"""

from __future__ import annotations

from typing import Any

from governance import run_lock as rl
from governance import runner


def start(**kwargs: Any) -> Any:
    with rl.run_lock(kwargs["run_id"]) as lock:
        return runner.start(**kwargs, lock=lock)


def resume(run_id: str, ops: Any) -> Any:
    with rl.run_lock(run_id) as lock:
        return runner.resume(run_id, ops, lock=lock)


def advance(state: Any, ops: Any) -> Any:
    with rl.run_lock(state.run_id) as lock:
        return runner.advance(state, ops, lock=lock)


def verify(parent_run_id: str, ops: Any, run_id: str | None = None) -> Any:
    with rl.run_lock(parent_run_id) as lock:
        return runner.verify(parent_run_id, ops, run_id, lock=lock)


def reopen(run_id: str, node: str, ops: Any, **kwargs: Any) -> Any:
    with rl.run_lock(run_id) as lock:
        return runner.reopen(run_id, node, ops, lock=lock, **kwargs)


def attach_session(run_id: str, session_id: str, ops: Any) -> Any:
    with rl.run_lock(run_id) as lock:
        return runner.attach_session(run_id, session_id, ops, lock=lock)
