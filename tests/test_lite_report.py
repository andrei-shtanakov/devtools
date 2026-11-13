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


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("git https://user:hunter2@github.com/o/r.git", "hunter2"),
        ('{"token": "abc 123"}', "abc 123"),
        ("{'api_key': 'k-1'}", "k-1"),
        ("GH_AUTH=xyz", "xyz"),
        ("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
        ("key sk-" + "A" * 24, "A" * 24),
        ("passwd: s3cret", "s3cret"),
        ("Authorization: token abc123", "abc123"),
        ("password=my secret phrase", "phrase"),
        ("https://user:p@ss@github.com/o/r", "ss@github"),
    ],
)
def test_redact_hides_secret_forms(text: str, secret: str) -> None:
    assert secret not in rp.redact(text, Path("/h"))


def test_redact_keeps_text_before_secret_and_other_lines() -> None:
    out = rp.redact("x password=a b\nследующая строка", Path("/h"))
    assert out == "x password=<скрыто>\nследующая строка"


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


# --- dfl report: показ, подтверждение, отправка (Task 5) ---

import subprocess  # noqa: E402

from lite import failure_log as fl  # noqa: E402

ISSUE = "https://github.com/andrei-shtanakov/devtools/issues/7"


class FakeGh:
    def __init__(self, listing: list[dict] | None = None, rc: int = 0) -> None:
        self.calls: list[tuple[list[str], Path | None, str | None]] = []
        self.listing = listing or []
        self.rc = rc

    def __call__(self, argv, profile, body=None) -> subprocess.CompletedProcess:
        self.calls.append((list(argv), profile, body))
        if self.rc:
            return subprocess.CompletedProcess(argv, self.rc, "", "gh: нет сети")
        if argv[:2] == ["issue", "list"]:
            out = json.dumps(self.listing)
        elif argv[:2] == ["issue", "comment"]:
            out = ISSUE + "#issuecomment-1\n"
        else:
            out = ISSUE + "\n"
        return subprocess.CompletedProcess(argv, 0, out, "")

    def verbs(self) -> list[str]:
        return [" ".join(a[:2]) for a, _, _ in self.calls]


@pytest.fixture()
def failed(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(rp, "_versions", lambda: {"devtools": "abc"})
    monkeypatch.setattr(rp, "_profile", lambda: tmp_path / "gh-human")
    monkeypatch.setattr(rp, "report_repo", lambda: "o/devtools")
    cwd = tmp_path / "repo"
    cwd.mkdir()
    fl.write(cwd, _rec(output_tail=["вывод", "ghp_" + "q" * 36]))
    return cwd


def _report(
    cwd: Path,
    gh: FakeGh,
    *,
    answer: str = "y",
    tty: bool = True,
    print_only: bool = False,
) -> int:
    return rp.run_report(
        cwd, print_only=print_only, gh=gh, ask=lambda _q: answer, is_tty=lambda: tty
    )


def test_report_without_record_says_none(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    gh = FakeGh()
    assert _report(tmp_path, gh) == 0
    assert "записанного сбоя нет" in capsys.readouterr().out
    assert gh.calls == []


def test_report_print_only_never_calls_gh(failed: Path, capsys) -> None:
    gh = FakeGh()
    assert _report(failed, gh, print_only=True) == 0
    assert "[dfl-report] run:" in capsys.readouterr().out
    assert gh.calls == []


def test_report_no_tty_refuses_without_sending(failed: Path, capsys) -> None:
    gh = FakeGh()
    assert _report(failed, gh, tty=False) == 2
    assert "--print" in capsys.readouterr().err
    assert gh.calls == []


def test_report_confirm_creates_issue(failed: Path, tmp_path: Path, capsys) -> None:
    gh = FakeGh()
    assert _report(failed, gh) == 0
    assert gh.verbs() == ["issue list", "issue create"]
    argv, profile, body = gh.calls[1]
    assert argv[argv.index("-R") + 1] == "o/devtools"
    assert profile == tmp_path / "gh-human"
    assert body is not None and "## Вывод" in body
    out = capsys.readouterr().out
    assert ISSUE in out
    assert rp.PUBLIC_WARNING in out


def test_report_duplicate_goes_to_comment(failed: Path, capsys) -> None:
    fp = rp.build(fl.load(failed), rp.Facts()).fp  # type: ignore[arg-type]
    gh = FakeGh(
        listing=[{"number": 7, "url": ISSUE, "title": f"[dfl-report] x (fp {fp})"}]
    )
    asked: list[str] = []
    code = rp.run_report(
        failed,
        print_only=False,
        gh=gh,
        ask=lambda q: asked.append(q) or "y",
        is_tty=lambda: True,
    )
    assert code == 0
    assert gh.verbs() == ["issue list", "issue comment"]
    assert ISSUE in asked[0]


def test_report_ignores_listing_with_other_fp(failed: Path) -> None:
    gh = FakeGh(
        listing=[
            {"number": 7, "url": ISSUE, "title": "[dfl-report] x (fp 000000000000)"}
        ]
    )
    assert _report(failed, gh) == 0
    assert gh.verbs() == ["issue list", "issue create"]


def test_report_decline_sends_nothing(failed: Path, capsys) -> None:
    gh = FakeGh()
    assert _report(failed, gh, answer="") == 0
    assert gh.verbs() == ["issue list"]
    assert "не отправлено" in capsys.readouterr().out


@pytest.mark.parametrize("answer", ["y", "Y", "да"])
def test_report_yes_answers(failed: Path, answer: str) -> None:
    gh = FakeGh()
    assert _report(failed, gh, answer=answer) == 0
    assert gh.verbs()[-1] == "issue create"


def test_report_gh_failure_suggests_print(failed: Path, capsys) -> None:
    assert _report(failed, FakeGh(rc=1)) == 2
    assert "dfl report --print" in capsys.readouterr().err


def test_report_token_not_in_sent_body(failed: Path) -> None:
    gh = FakeGh()
    _report(failed, gh)
    assert all("ghp_" not in (body or "") for _, _, body in gh.calls)


def test_profile_falls_back_to_default_gh(monkeypatch) -> None:
    def broken(*a, **kw):
        raise df_config.ConfigError("x")

    monkeypatch.setattr(df_config, "load", broken)
    assert rp._profile() is None


# --- финальное ревью: отпечаток (I4) и Markdown публичного тела ---


def test_fingerprint_ignores_run_id_suffix() -> None:
    a = _rec(cause="прогон cli-x-20261111-09de97 остановлен")
    b = _rec(cause="прогон cli-x-20261112-c4f581 остановлен")
    assert rp.fingerprint(a, "") == rp.fingerprint(b, "")
    assert rp.normalize("facade deface") == "facade deface"  # слова из hex-букв целы


def test_build_last_op_only_from_runs_changed_by_call() -> None:
    run = {
        "run_id": "r",
        "status": "s",
        "wave": "1",
        "pr": "None",
        "last_op_name": "s4",
        "last_op": "s4 started",
        "stop_reason": "",
        "note": "",
    }
    changed = rp.build(_rec(), rp.Facts(runs=[run]))
    stale = rp.build(
        _rec(runs_note="вызов его не менял"),
        rp.Facts(runs=[{**run, "note": "вызов его не менял"}]),
    )
    assert changed.fp == rp.fingerprint(_rec(), "s4")
    assert stale.fp == rp.fingerprint(_rec(), "")


def test_build_neutralizes_mentions_and_refs() -> None:
    rep = rp.build(_rec(cause="PR #12 от @alice"), FACTS)
    assert "@alice" not in rep.title and "#12" not in rep.title
    body_outside_code = rep.body.split("## Вывод")[0]
    assert "`PR #12 от @alice`" in body_outside_code


def test_build_fence_longer_than_backticks_in_output() -> None:
    rep = rp.build(_rec(output_tail=["```", "after"]), FACTS)
    assert "````\n" in rep.body


def test_report_ctrl_d_is_decline(failed: Path, capsys) -> None:
    def eof(_q: str) -> str:
        raise EOFError

    gh = FakeGh()
    code = rp.run_report(failed, print_only=False, gh=gh, ask=eof, is_tty=lambda: True)
    assert code == 0
    assert gh.verbs() == ["issue list"]
    assert "не отправлено" in capsys.readouterr().out


def test_versions_survive_broken_components(monkeypatch) -> None:
    from lite import install

    def broken(*a, **kw):
        raise install.InstallError("битая комплектация")

    monkeypatch.setattr(rp, "load_components", broken)
    monkeypatch.setattr(rp, "_cmd", lambda argv: "v")
    assert rp._versions()["components"] == "нет (битая комплектация)"


def test_report_repo_is_origin_of_installed_devtools(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "devtools"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "remote",
            "add",
            "origin",
            "git@github.com:me/devtools.git",
        ],
        check=True,
    )
    monkeypatch.setattr(rp, "DEVTOOLS", repo)
    assert rp.report_repo() == "me/devtools"


def test_report_without_origin_refuses_with_print_hint(
    failed: Path, monkeypatch, capsys
) -> None:
    def none() -> str:
        raise rp.ReportError(
            "репо для отчётов не определено: нет origin — " + rp.PRINT_HINT
        )

    monkeypatch.setattr(rp, "report_repo", none)
    gh = FakeGh()
    assert _report(failed, gh) == 2
    assert "dfl report --print" in capsys.readouterr().err
    assert gh.calls == []


def _log_file(tmp_path: Path, body: str) -> str:
    path = tmp_path / "20261113T000000000000Z-run.log"
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_tail_from_log_includes_child_output_redacted(tmp_path: Path) -> None:
    body = "# dfl run уровень 0\nchild-line\ntoken=ghp_abc\n# код выхода: 1\n"
    rec = _rec(log_path=_log_file(tmp_path, body), output_tail=["tail-only"])
    assert rp.tail_lines(rec) == ["child-line", "token=ghp_abc"]
    report = rp.build(rec, FACTS)
    assert "child-line" in report.body
    assert "tail-only" not in report.body
    assert "ghp_abc" not in report.body


def test_tail_level3_uses_output_tail(tmp_path: Path) -> None:
    rec = _rec(log_path=_log_file(tmp_path, "child-line\n"), verbosity=3)
    assert rp.tail_lines(rec) == ["строка 1", "строка 2"]


def test_tail_missing_log_uses_output_tail(tmp_path: Path) -> None:
    rec = _rec(log_path=str(tmp_path / "нет.log"))
    assert rp.tail_lines(rec) == ["строка 1", "строка 2"]


def test_tail_no_log_path_uses_output_tail() -> None:
    assert rp.tail_lines(_rec()) == ["строка 1", "строка 2"]


def test_tail_caps_200_lines(tmp_path: Path) -> None:
    body = "".join(f"l{i}\n" for i in range(500))
    tail = rp.tail_lines(_rec(log_path=_log_file(tmp_path, body)))
    assert len(tail) == 200 and tail[-1] == "l499"


def test_tail_appends_crash_traceback_from_output_tail(tmp_path: Path) -> None:
    out = ["шум", "Traceback (most recent call last):", "  File x", "KeyError: 1"]
    rec = _rec(log_path=_log_file(tmp_path, "child\n# код выхода: 1\n"))
    rec = _rec(log_path=rec.log_path, output_tail=out)
    assert rp.tail_lines(rec) == ["child", *out[1:]]


def test_tail_no_traceback_nothing_appended(tmp_path: Path) -> None:
    rec = _rec(log_path=_log_file(tmp_path, "child\n"), output_tail=["шум"])
    assert rp.tail_lines(rec) == ["child"]
