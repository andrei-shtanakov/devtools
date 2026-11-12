"""`dfl init-repo` (lite/init_repo.py): PR с шаблонами в репо коллеги.

Рабочий репо + локальный bare-remote в `tmp_path` (адрес origin подменён на
GitHub-форму через `insteadOf`); `gh` — скриптованный фейк, сети нет.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from governance import brief_tools as bt
from lite import dfl
from lite import init_repo as ir
from tests.df_config_fixture import use_test_real_home, write_config

SLUG = "o/alpha"
URL = f"git@github.com:{SLUG}.git"
DEVTOOLS = Path(__file__).resolve().parent.parent
PR_URL = "https://github.com/o/alpha/pull/1"
LABEL_CALL = ("api", f"repos/{SLUG}/issues/1/labels")
REVIEW_RULE = [
    {
        "type": "pull_request",
        "parameters": {"required_approving_review_count": 1},
    }
]


def _done(out: str = "", err: str = "", rc: int = 0):
    return subprocess.CompletedProcess([], rc, out, err)


class FakeGh:
    """`gh` по ключу из двух первых аргументов; журнал вызовов."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.answers: dict[tuple[str, str], subprocess.CompletedProcess] = {
            ("pr", "list"): _done("[]"),
            ("api", "user"): _done("alice\n"),
            ("api", "--paginate"): _done("[]"),
            ("api", f"repos/{SLUG}/branches/main/protection"): _done(
                '{"message":"Branch not protected"}',
                "gh: Branch not protected (HTTP 404)",
                1,
            ),
            ("pr", "create"): _done(PR_URL + "\n"),
            ("label", "list"): _done("[]"),
            ("label", "create"): _done(),
            LABEL_CALL: _done(),
        }

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(list(argv))
        return self.answers[(argv[0], argv[1])]

    def called(self, *prefix: str) -> list[list[str]]:
        return [c for c in self.calls if c[: len(prefix)] == list(prefix)]


def _pr(author: str = "alice", cross: bool = False, url: str = PR_URL) -> dict:
    return {
        "url": url,
        "isCrossRepository": cross,
        "author": {"login": author},
        "headRepositoryOwner": {"login": author},
    }


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


@pytest.fixture()
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key in ("NAME", "EMAIL"):
        monkeypatch.setenv(f"GIT_AUTHOR_{key}", "t")
        monkeypatch.setenv(f"GIT_COMMITTER_{key}", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@example.com")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@example.com")
    bare = tmp_path / "bare.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(bare))
    repo = tmp_path / "work"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", f"url.{bare}.insteadOf", URL)
    _git(repo, "remote", "add", "origin", URL)
    (repo / "README.md").write_text("x\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    _git(repo, "push", "-u", "origin", "main")
    _git(repo, "remote", "set-head", "origin", "main")
    return repo


def _remote_branches(work: Path) -> str:
    return _git(work, "ls-remote", "--heads", "origin")


# --- review_rules_refusal -------------------------------------------------


def test_rules_ruleset_requires_review_refused() -> None:
    gh = FakeGh()
    gh.answers[("api", "--paginate")] = _done(json.dumps(REVIEW_RULE))
    out = ir.review_rules_refusal(SLUG, "main", gh)
    assert out is not None and "pull_request" in out
    assert "не меняет" in out


def test_rules_paginated_pages_concatenated() -> None:
    gh = FakeGh()
    page = json.dumps(REVIEW_RULE)
    gh.answers[("api", "--paginate")] = _done("[]" + page)
    assert ir.review_rules_refusal(SLUG, "main", gh) is not None


def test_protection_requires_review_refused() -> None:
    gh = FakeGh()
    body = {"required_pull_request_reviews": {"required_approving_review_count": 2}}
    gh.answers[("api", f"repos/{SLUG}/branches/main/protection")] = _done(
        json.dumps(body)
    )
    out = ir.review_rules_refusal(SLUG, "main", gh)
    assert out is not None and "защита ветки" in out and "не меняет" in out


def test_protection_403_refused_not_none() -> None:
    gh = FakeGh()
    gh.answers[("api", f"repos/{SLUG}/branches/main/protection")] = _done(
        "", "gh: Forbidden (HTTP 403)", 1
    )
    out = ir.review_rules_refusal(SLUG, "main", gh)
    assert out is not None and "не «правил нет»" in out


def test_rules_unpaginated_error_refused() -> None:
    gh = FakeGh()
    gh.answers[("api", "--paginate")] = _done("", "gh: Server Error (HTTP 500)", 1)
    out = ir.review_rules_refusal(SLUG, "main", gh)
    assert out is not None and "не «правил нет»" in out


def test_no_rules_no_protection_ok() -> None:
    assert ir.review_rules_refusal(SLUG, "main", FakeGh()) is None


def test_protection_without_reviews_ok() -> None:
    gh = FakeGh()
    gh.answers[("api", f"repos/{SLUG}/branches/main/protection")] = _done("{}")
    gh.answers[("api", "--paginate")] = _done(json.dumps([{"type": "deletion"}]))
    assert ir.review_rules_refusal(SLUG, "main", gh) is None


# --- init_repo -------------------------------------------------------------


def test_conflicts_listed_nothing_created(work: Path, capsys) -> None:
    (work / "profiles").mkdir()
    (work / "profiles/team-exp.yaml").write_text("mine\n")
    (work / "spec-runner.config.yaml").write_text("mine\n")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "mine")
    _git(work, "push", "origin", "main")
    before = _git(work, "rev-list", "--count", "--all")
    assert ir.conflicts(work) == ["profiles/team-exp.yaml", "spec-runner.config.yaml"]
    gh = FakeGh()
    assert ir.init_repo(work, DEVTOOLS, gh) == 2
    err = capsys.readouterr().err
    assert "profiles/team-exp.yaml" in err and "spec-runner.config.yaml" in err
    assert _git(work, "branch", "--list", ir.INIT_BRANCH) == ""
    assert _git(work, "rev-list", "--count", "--all") == before
    assert gh.called("pr", "create") == []


def test_happy_path_pushes_branch_and_opens_pr(work: Path, capsys) -> None:
    gh = FakeGh()
    assert ir.init_repo(work, DEVTOOLS, gh) == 0
    assert ir.INIT_BRANCH in _remote_branches(work)

    def shown(p: str) -> bytes:
        return subprocess.run(
            ["git", "show", f"origin/{ir.INIT_BRANCH}:{p}"],
            cwd=work,
            capture_output=True,
            check=True,
        ).stdout

    _git(work, "fetch", "origin")
    for src, dst in ir.TEMPLATE_FILES:
        assert shown(dst) == (DEVTOOLS / src).read_bytes(), dst
    ls = _git(work, "ls-tree", f"origin/{ir.INIT_BRANCH}", "scripts/review/")
    assert (
        "100755 blob"
        in [line for line in ls.splitlines() if line.endswith("harness-claude")][0]
    )
    create = gh.called("pr", "create")
    assert len(create) == 1 and "--base" in create[0]
    assert create[0][create[0].index("--base") + 1] == "main"
    assert _git(work, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert PR_URL in capsys.readouterr().out
    assert (work / "profiles").exists() is False
    body = create[0][create[0].index("--body") + 1]
    assert "этот PR мержите вы" in body.lower() or "мержите вы" in body


def test_working_tree_copy_is_executable(work: Path) -> None:
    gh = FakeGh()
    ir.init_repo(work, DEVTOOLS, gh)
    _git(work, "switch", ir.INIT_BRANCH)
    assert os.access(work / "scripts/review/harness-claude", os.X_OK)


def test_label_created_when_absent(work: Path) -> None:
    gh = FakeGh()
    ir.init_repo(work, DEVTOOLS, gh)
    assert [c[2] for c in gh.called("label", "create")] == [bt.LABEL]
    assert gh.called(*LABEL_CALL)[0][-1] == f"labels[]={bt.LABEL}"


def test_label_kept_when_present(work: Path) -> None:
    gh = FakeGh()
    gh.answers[("label", "list")] = _done(json.dumps([{"name": bt.LABEL}]))
    ir.init_repo(work, DEVTOOLS, gh)
    assert gh.called("label", "create") == []
    assert gh.called(*LABEL_CALL)


def test_existing_init_pr_is_reported(work: Path, capsys) -> None:
    gh = FakeGh()
    gh.answers[("pr", "list")] = _done(json.dumps([_pr()]))
    assert ir.init_repo(work, DEVTOOLS, gh) == 0
    assert PR_URL in capsys.readouterr().out
    assert gh.called("pr", "create") == []
    assert ir.INIT_BRANCH not in _remote_branches(work)
    assert _git(work, "branch", "--list", ir.INIT_BRANCH) == ""


def test_dirty_tree_refused(work: Path, capsys) -> None:
    (work / "stray.txt").write_text("x\n")
    gh = FakeGh()
    assert ir.init_repo(work, DEVTOOLS, gh) == 2
    assert "не чисто" in capsys.readouterr().err
    assert gh.calls == []


def test_not_default_branch_refused(work: Path, capsys) -> None:
    _git(work, "switch", "-c", "feature")
    assert ir.init_repo(work, DEVTOOLS, FakeGh()) == 2
    assert "main" in capsys.readouterr().err


def test_branch_rules_refusal_creates_nothing(work: Path, capsys) -> None:
    gh = FakeGh()
    gh.answers[("api", "--paginate")] = _done(json.dumps(REVIEW_RULE))
    assert ir.init_repo(work, DEVTOOLS, gh) == 2
    assert "pull_request" in capsys.readouterr().err
    assert _git(work, "branch", "--list", ir.INIT_BRANCH) == ""


def test_copied_kit_matches_pin() -> None:
    pins = {}
    for line in (DEVTOOLS / "scripts/review/PIN").read_text().splitlines():
        if line and not line.startswith("#"):
            digest, path = line.split(maxsplit=1)
            pins[path] = digest
    dests = {dst: src for src, dst in ir.TEMPLATE_FILES}
    assert pins
    for path, digest in pins.items():
        assert path in dests, path
        got = hashlib.sha256((DEVTOOLS / dests[path]).read_bytes()).hexdigest()
        assert got == digest, path


def test_template_files_cover_brief() -> None:
    dsts = [d for _, d in ir.TEMPLATE_FILES]
    assert len(dsts) == len(set(dsts)) == 4 + 8 + 2 + 1
    assert all((DEVTOOLS / s).is_file() for s, _ in ir.TEMPLATE_FILES)
    assert ir.LABEL == bt.LABEL


def test_spec_runner_template_selects_claude() -> None:
    text = (
        DEVTOOLS / "templates/darkfactory/target/spec-runner.config.yaml"
    ).read_text()
    lines = text.splitlines()
    for want in (
        'claude_command: "claude"',
        'claude_model: "sonnet"',
        "skip_permissions: true",
        'review_command: "claude"',
        'review_model: "sonnet"',
        "integration_pr: true",
        "spec_governance: strict",
        "execution_mode: standard",
        "review_policy: required",
    ):
        assert want in lines
    assert not any(line.startswith("commands:") for line in lines)
    assert "# commands:" in lines


def test_dfl_init_repo_subcommand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    use_test_real_home(monkeypatch, home)
    for key, value in write_config(tmp_path / "xdg").items():
        monkeypatch.setenv(key, value)
    seen: list[Path] = []

    def fake(repo: Path, devtools: Path, gh) -> int:
        seen.append(repo)
        assert (devtools / "lite").is_dir() and callable(gh)
        return 0

    monkeypatch.setattr(dfl, "init_repo", fake)
    assert dfl.main(["--cwd", str(tmp_path), "init-repo"]) == 0
    assert seen == [tmp_path]


# --- fix round 1 --------------------------------------------------------


def test_existing_pr_run_ensures_label(work: Path) -> None:
    gh = FakeGh()
    gh.answers[("pr", "list")] = _done(json.dumps([_pr()]))
    assert ir.init_repo(work, DEVTOOLS, gh) == 0
    assert [c[2] for c in gh.called("label", "create")] == [bt.LABEL]
    assert gh.called(*LABEL_CALL)[0][-1] == f"labels[]={bt.LABEL}"


def test_label_failure_then_rerun_labels(work: Path) -> None:
    gh = FakeGh()
    gh.answers[LABEL_CALL] = _done("", "boom", 1)
    assert ir.init_repo(work, DEVTOOLS, gh) == 2
    gh.answers[("pr", "list")] = _done(json.dumps([_pr()]))
    gh.answers[LABEL_CALL] = _done()
    assert ir.init_repo(work, DEVTOOLS, gh) == 0
    assert len(gh.called(*LABEL_CALL)) == 2


def test_leftover_branch_refused_with_cleanup(work: Path, capsys) -> None:
    gh = FakeGh()
    gh.answers[("pr", "create")] = _done("", "HTTP 500", 1)
    assert ir.init_repo(work, DEVTOOLS, gh) == 2
    assert _git(work, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    capsys.readouterr()
    assert ir.init_repo(work, DEVTOOLS, FakeGh()) == 2
    err = capsys.readouterr().err
    assert f"git branch -D {ir.INIT_BRANCH}" in err
    assert f"git push origin --delete {ir.INIT_BRANCH}" in err


def test_fork_pr_refused(work: Path, capsys) -> None:
    gh = FakeGh()
    fork = _pr(author="alice", cross=True, url="https://x/fork/1")
    gh.answers[("pr", "list")] = _done(json.dumps([fork]))
    assert ir.init_repo(work, DEVTOOLS, gh) == 2
    err = capsys.readouterr().err
    assert "https://x/fork/1" in err and "не ваши" in err
    assert gh.called(*LABEL_CALL) == []


def test_other_author_pr_refused(work: Path, capsys) -> None:
    gh = FakeGh()
    gh.answers[("pr", "list")] = _done(json.dumps([_pr(author="mallory")]))
    assert ir.init_repo(work, DEVTOOLS, gh) == 2
    assert "не ваши" in capsys.readouterr().err
    assert gh.called("pr", "create") == []


def test_own_pr_accepted(work: Path, capsys) -> None:
    gh = FakeGh()
    gh.answers[("pr", "list")] = _done(json.dumps([_pr()]))
    assert ir.init_repo(work, DEVTOOLS, gh) == 0
    assert PR_URL in capsys.readouterr().out


@pytest.mark.parametrize("key", [("pr", "list"), ("label", "list")])
def test_garbage_json_is_refusal_not_traceback(work: Path, key, capsys) -> None:
    gh = FakeGh()
    gh.answers[key] = _done("<html>nope")
    assert ir.init_repo(work, DEVTOOLS, gh) == 2
    assert "не разобран" in capsys.readouterr().err


def test_label_exists_beyond_search_window(work: Path) -> None:
    gh = FakeGh()
    gh.answers[("label", "list")] = _done(json.dumps([{"name": "other"}]))
    gh.answers[("label", "create")] = _done("", "label already exists", 1)
    assert ir.init_repo(work, DEVTOOLS, gh) == 0
    assert gh.called(*LABEL_CALL)


def test_label_search_is_exact_match(work: Path) -> None:
    gh = FakeGh()
    gh.answers[("label", "list")] = _done(json.dumps([{"name": bt.LABEL + "-x"}]))
    ir.init_repo(work, DEVTOOLS, gh)
    assert gh.called("label", "create")
    assert "--search" in gh.called("label", "list")[0]


def test_ruleset_code_owner_review_refused() -> None:
    gh = FakeGh()
    rule = [
        {
            "type": "pull_request",
            "parameters": {
                "required_approving_review_count": 0,
                "require_code_owner_review": True,
            },
        }
    ]
    gh.answers[("api", "--paginate")] = _done(json.dumps(rule))
    assert ir.review_rules_refusal(SLUG, "main", gh) is not None


def test_protection_code_owner_review_refused() -> None:
    gh = FakeGh()
    body = {
        "required_pull_request_reviews": {
            "required_approving_review_count": 0,
            "require_code_owner_reviews": True,
        }
    }
    gh.answers[("api", f"repos/{SLUG}/branches/main/protection")] = _done(
        json.dumps(body)
    )
    assert ir.review_rules_refusal(SLUG, "main", gh) is not None


def test_pr_body_links_colleague_doc(work: Path) -> None:
    gh = FakeGh()
    ir.init_repo(work, DEVTOOLS, gh)
    create = gh.called("pr", "create")[0]
    assert "docs/darkfactory-for-colleagues.md" in create[create.index("--body") + 1]


# --- .gitignore (devtools#735) ---------------------------------------------

_IGNORE_LINES = ["__pycache__/", "*.pyc", ".venv/", ".pytest_cache/", ".ruff_cache/"]


def _shown(work: Path, path: str) -> str:
    _git(work, "fetch", "origin")
    return _git(work, "show", f"origin/{ir.INIT_BRANCH}:{path}")


def test_gitignore_created_when_absent(work: Path) -> None:
    assert ir.init_repo(work, DEVTOOLS, FakeGh()) == 0
    text = _shown(work, ".gitignore")
    for line in _IGNORE_LINES:
        assert line in text.splitlines()


def test_gitignore_appends_only_missing_lines(work: Path) -> None:
    (work / ".gitignore").write_text("dist/\n  __pycache__/  \n*.pyc", encoding="utf-8")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "own ignore")
    _git(work, "push", "origin", "main")
    gh = FakeGh()
    assert ir.init_repo(work, DEVTOOLS, gh) == 0
    lines = _shown(work, ".gitignore").splitlines()
    assert lines[:3] == ["dist/", "  __pycache__/  ", "*.pyc"]
    added = lines[lines.index("# DarkFactory (dfl init-repo)") + 1 :]
    assert added == [".venv/", ".pytest_cache/", ".ruff_cache/"]
    body = gh.called("pr", "create")[0]
    assert ".gitignore` (недостающие строки)" in body[body.index("--body") + 1]


def test_gitignore_untouched_when_complete(work: Path) -> None:
    own = "\n".join(_IGNORE_LINES) + "\n"
    (work / ".gitignore").write_text(own, encoding="utf-8")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "own ignore")
    _git(work, "push", "origin", "main")
    gh = FakeGh()
    assert ir.init_repo(work, DEVTOOLS, gh) == 0
    assert _shown(work, ".gitignore") + "\n" == own
    _git(work, "fetch", "origin")
    changed = _git(work, "diff", "--name-only", "main", f"origin/{ir.INIT_BRANCH}")
    assert ".gitignore" not in changed.splitlines()
    body = gh.called("pr", "create")[0]
    assert "недостающие строки" not in body[body.index("--body") + 1]


def test_gitignore_symlink_is_conflict(work: Path, capsys) -> None:
    (work / "other").write_text("x\n")
    (work / ".gitignore").symlink_to("other")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "link")
    _git(work, "push", "origin", "main")
    assert ir.conflicts(work) == [".gitignore"]
    assert ir.init_repo(work, DEVTOOLS, FakeGh()) == 2
    assert ".gitignore" in capsys.readouterr().err
