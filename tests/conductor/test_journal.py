"""Журнал прогона и учёт мутаций (спека среза 1, §4.7)."""

from pathlib import Path

from conductor.durable import read_jsonl
from conductor.journal import MutationLog, RunJournal, log_complete, redact

TOKEN = "ghs_" + "A" * 36
JWT = "eyJhbGciOiJSUzI1NiJ9.eyJpc3MiOiIxMSJ9.c2lnbmF0dXJlLWJ5dGVz"
PEM = "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----"


def test_redact_strings_and_nested() -> None:
    row = {"err": f"401 for {TOKEN}", "n": [JWT, {"k": PEM}], "x": 1}
    out = redact(row)
    text = str(out)
    assert TOKEN not in text and JWT not in text and "PRIVATE KEY" not in text
    assert out["x"] == 1 and "[REDACTED]" in out["err"]


def test_run_journal_redacts(tmp_path: Path) -> None:
    j = RunJournal(tmp_path)
    j.write({"reason": f"bad {TOKEN}"})
    assert TOKEN not in (tmp_path / "journal.jsonl").read_text(encoding="utf-8")


def test_mutation_log_complete_only_with_results_and_run_end(tmp_path: Path) -> None:
    log = MutationLog(tmp_path)
    seq = log.intent(
        attempt_id="a1",
        method="POST",
        endpoint="/repos/o/r/issues/1/comments",
        repo="o/r",
        marker_key="k",
        body=b'{"body": "x"}',
    )
    assert not log_complete(tmp_path / "calls.jsonl")
    log.result(seq, sent=True, outcome="ok", status=201)
    assert not log_complete(tmp_path / "calls.jsonl")
    log.end_run()
    assert log_complete(tmp_path / "calls.jsonl")
    rows = read_jsonl(tmp_path / "calls.jsonl").rows
    assert "body" not in rows[0] and len(rows[0]["body_sha256"]) == 64


def test_intent_without_result_is_incomplete(tmp_path: Path) -> None:
    log = MutationLog(tmp_path)
    log.intent(
        attempt_id="a1",
        method="POST",
        endpoint="/x",
        repo="o/r",
        marker_key="k",
        body=None,
    )
    log.end_run()
    assert not log_complete(tmp_path / "calls.jsonl")
