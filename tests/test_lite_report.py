"""Отчёт `dfl report` (lite/report.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governance import df_config
from governance import run_state as rs
from lite import report as rp
from lite.failure_log import FailureRecord


def _rec(**kw: object) -> FailureRecord:
    base: dict[str, object] = {
        "command": "run",
        "argv": ["run"],
        "exit_code": 2,
        "time": "2026-11-12T00:00:00Z",
        "output_tail": ["строка 1", "строка 2"],
        "run_ids": [],
        "runs_note": "",
        "cwd_repo": "o/r",
        "cause": "незаконченного прогона нет",
    }
    base.update(kw)
    return FailureRecord(**base)  # type: ignore[arg-type]


FACTS = rp.Facts(
    runs=[], versions={"devtools": "abc", "os": "Linux"}, config="mode = test"
)


def test_fingerprint_stable_across_sha_path_number_quote() -> None:
    a = _rec(cause="PR #12 abcdef1234 в /home/a/x.py: 'тема 1' https://x/y 5")
    b = _rec(cause="PR #99 0123456789ab в /Users/b/y.py: 'другая' https://z 7")
    assert rp.fingerprint(a, "s1") == rp.fingerprint(b, "s1")
    assert len(rp.fingerprint(a, "s1")) == 12


def test_fingerprint_differs_by_cause_command_and_op() -> None:
    base = rp.fingerprint(_rec(), "s1")
    assert rp.fingerprint(_rec(cause="другое"), "s1") != base
    assert rp.fingerprint(_rec(command="status"), "s1") != base
    assert rp.fingerprint(_rec(), "s2") != base
    assert rp.fingerprint(_rec(), "") == rp.fingerprint(_rec(), "dfl")


@pytest.mark.parametrize(
    "token",
    [
        "ghp_" + "a" * 36,
        "gho_" + "b" * 36,
        "github_pat_" + "c" * 40,
        "sk-ant-api03-xyz123abc",
        "Bearer abc.def",
        "xoxb-123-abc",
        "TOKEN=abc123",
    ],
)
def test_redact_hides_token(token: str) -> None:
    secret = token.split("=")[-1].removeprefix("Bearer ")
    out = rp.redact(f"x {token} y", Path("/h"))
    assert secret not in out
    assert "<скрыто>" in out


def test_redact_home_to_tilde() -> None:
    assert rp.redact("/home/al/x and /home/al", Path("/home/al")) == "~/x and ~"


def test_build_title_and_sections() -> None:
    rep = rp.build(_rec(), FACTS)
    assert rep.title == f"[dfl-report] run: незаконченного прогона нет (fp {rep.fp})"
    for head in ("## Где остановилось", "## Вывод", "## Версии", "## Конфиг"):
        assert head in rep.body
    assert rep.body.rstrip().splitlines()[-1] == f"fp {rep.fp}"


def test_build_title_cause_cut_to_80() -> None:
    rep = rp.build(_rec(cause="x" * 200), FACTS)
    assert "x" * 80 in rep.title and "x" * 81 not in rep.title


def test_build_redacts_title_and_body() -> None:
    tok = "ghp_" + "z" * 36
    rep = rp.build(_rec(cause=f"сбой {tok}", output_tail=[tok]), FACTS)
    assert tok not in rep.title and tok not in rep.body


def test_build_truncates_to_body_limit() -> None:
    rep = rp.build(_rec(output_tail=["y" * 1000] * 200), FACTS)
    assert len(rep.body) <= rp.BODY_LIMIT
    assert "обрезано" in rep.body


def test_collect_facts_reads_runs_without_transcripts(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    d = tmp_path / "runs" / "r1"
    (d / "author-transcripts").mkdir(parents=True)
    (d / "author-transcripts" / "t.jsonl").write_text("СЕКРЕТ-ТРАНСКРИПТ")
    (d / "run.json").write_text(
        json.dumps(
            {
                "status": "stopped_author",
                "wave": 2,
                "pr": 5,
                "ops": {"a": {"status": "completed"}, "b": {"status": "started"}},
            }
        )
    )
    (d / "stop-reason.txt").write_text("стоп автора")
    facts = rp.collect_facts(_rec(run_ids=["r1"]))
    (run,) = facts.runs
    assert run["status"] == "stopped_author" and run["last_op"] == "b started"
    assert run["stop_reason"] == "стоп автора"
    body = rp.build(_rec(run_ids=["r1"]), facts).body
    assert "СЕКРЕТ-ТРАНСКРИПТ" not in body and "стоп автора" in body


def test_collect_facts_broken_config(monkeypatch) -> None:
    def broken(*a, **kw):
        raise df_config.ConfigError("битый toml")

    monkeypatch.setattr(df_config, "load", broken)
    facts = rp.collect_facts(_rec())
    assert facts.config == "конфиг не читается: битый toml"
    assert "devtools" in facts.versions
