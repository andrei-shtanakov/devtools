"""Тело PR через `gh` (`RealOps.pr_body_fact`, Task 13; ревизия b4)."""

from __future__ import annotations

from governance import ops as ops_mod
from governance.facts import Outcome
from governance.ops import RealOps


class _Done:
    def __init__(self, rc: int, out: str = "", err: str = "") -> None:
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_pr_body_unreadable_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        ops_mod.subprocess, "run", lambda *a, **k: _Done(1, err="no network")
    )
    got = RealOps().pr_body_fact("owner/p", 7)
    assert got.outcome is Outcome.UNAVAILABLE and got.detail == "тело PR #7: no network"


def test_pr_body_read(monkeypatch) -> None:
    monkeypatch.setattr(
        ops_mod.subprocess, "run", lambda *a, **k: _Done(0, out="act: review/v1\n")
    )
    got = RealOps().pr_body_fact("owner/p", 7)
    assert got.outcome is Outcome.FOUND and got.value == "act: review/v1"
