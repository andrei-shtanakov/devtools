"""Обёртки brief-маршрута (§11.3, §11.5, §11.3 п.6) на стенде форджа."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from governance import approval_request as ar
from governance import brief_tools as bt
from governance import interview as iv
from governance import run_state as rs
from governance.brief_facts import BriefPrFacts
from governance.facts import Fact, Outcome, unavailable
from governance.stale_adapter import blob_sha1_bytes
from tests.forge_fake import (
    BASE,
    C1,
    DIR,
    DRAFT,
    HEAD,
    MERGE,
    REPO,
    FakeForge,
    P,
    consistent_world,
    request,
)

RUN_ID = "WS-1-abc123"
BRANCH = "brief/WS-1"
FILES = ((f"{DIR}/brief.md", "added"), (f"{DIR}/{ar.FILE_NAME}", "added"))


@dataclass
class ToolOps(FakeForge):
    """Стенд форджа + git-эффекты обёрток (журнал `calls`)."""

    calls: list[tuple] = field(default_factory=list)
    remote_heads: dict[str, str] = field(default_factory=dict)
    dirty: bool = False
    next_pr: int = 42
    push_error: str | None = None

    def remote_branch_head_fact(self, repo_slug: str, branch: str) -> Fact[str]:
        if "remote" in self.unavailable_facts:
            return unavailable("remote")
        sha = self.remote_heads.get(branch)
        if sha is None:
            return Fact(Outcome.ABSENT, None, "нет ветки")
        return Fact(Outcome.FOUND, sha, "ветка")

    def is_dirty(self, target_dir: str) -> bool:
        return self.dirty

    def fetch_branch(self, target_dir: str, branch: str) -> bool:
        self.calls.append(("fetch_branch", branch))
        return True

    def switch_to(self, target_dir: str, branch: str, start_point: str) -> None:
        self.calls.append(("switch_to", branch, start_point))

    def commit_paths(self, target_dir, paths, message, force_paths=()) -> None:
        self.calls.append(("commit_paths", tuple(paths)))
        root = Path(target_dir)
        for path in paths:
            self.files[(HEAD, path)] = (root / path).read_text(encoding="utf-8")

    create_error: str | None = None
    #: Голова, с которой форджа откроет PR (сдвиг ветки между проверкой и PR).
    pr_head: str | None = None

    #: Пути, которые локальный коммит меняет сверх предложения (хук и т.п.).
    extra_changed: tuple[str, ...] = ()

    def changed_paths(self, target_dir: str, base_branch: str):
        committed = next(c for c in reversed(self.calls) if c[0] == "commit_paths")
        return BASE, [*committed[1], *self.extra_changed]

    def head_sha(self, target_dir: str, branch: str) -> str:
        return HEAD

    def push_branch(self, target_dir: str, branch: str) -> None:
        if self.push_error:
            raise RuntimeError(self.push_error)
        self.calls.append(("push_branch", branch))
        self.remote_heads[branch] = HEAD
        self.compare[(BASE, branch)] = FILES

    def create_pr(
        self,
        target_dir,
        repo_slug,
        branch,
        title,
        body,
        label,
        *,
        draft=False,
        base=None,
    ) -> int:
        if self.create_error:
            raise RuntimeError(self.create_error)
        self.calls.append(("create_pr", branch, label, base, body))
        head = self.pr_head or self.remote_heads.get(branch, HEAD)
        self.prs[self.next_pr] = BriefPrFacts(
            self.next_pr, "OPEN", base or "", branch, head, FILES, None, None, None
        )
        return self.next_pr


@pytest.fixture()
def runs_root(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    return tmp_path / "runs"


def _brief_ready_run(
    tmp_path: Path, status: str = "brief_ready", text: str = DRAFT
) -> None:
    target = tmp_path / "target"
    target.mkdir(exist_ok=True)
    state = rs.new_run(
        subject="s",
        repo="alpha",
        repo_slug=REPO,
        ws_id="WS-1",
        target_dir=str(target),
        bundle_dir="workstreams/WS-1/spec",
        profile="profiles/team-exp.yaml",
        run_id=RUN_ID,
        merge_authority="human",
        interview={
            **iv.InterviewSpec(
                "customer", "po", REPO, None, None, brief_only=True
            ).as_state(),
            "session_id": "s-1",
            "brief_blob": blob_sha1_bytes(text.encode()),
        },
    )
    state.status = status
    brief = rs.run_dir(RUN_ID) / iv.BRIEF_REL
    brief.parent.mkdir(parents=True, exist_ok=True)
    brief.write_text(text, encoding="utf-8")
    rs.save(state)


def _ops(monkeypatch, *, with_pr: bool = False) -> ToolOps:
    world = consistent_world(monkeypatch)
    ops = ToolOps()
    ops.files.update(world.files)
    ops.policy_versions.update(world.policy_versions)
    ops.policy_head = world.policy_head
    if with_pr:
        ops.prs.update(world.prs)
    return ops


# --- §11.3: brief-propose (Task 11) ---


def test_nothing_exists_creates_two_file_pr(
    tmp_path, runs_root, monkeypatch
) -> None:  # T47
    _brief_ready_run(tmp_path)
    ops = _ops(monkeypatch)
    assert bt.propose(RUN_ID, ops) == 42
    assert ("switch_to", BRANCH, BASE) in ops.calls  # от immutable SHA базы форджа
    commit = next(c for c in ops.calls if c[0] == "commit_paths")
    assert sorted(commit[1]) == sorted(p for p, _ in FILES)
    pr = next(c for c in ops.calls if c[0] == "create_pr")
    assert pr[1:4] == (BRANCH, "human-merge-required", "main")
    assert f"policy: andrei-shtanakov/approval-policy@{P}" in pr[4]
    written = ar.parse(ops.files[(HEAD, f"{DIR}/{ar.FILE_NAME}")])
    assert written == request()


def test_merged_pr_with_our_content_is_reported(
    tmp_path, runs_root, monkeypatch, capsys
) -> None:
    _brief_ready_run(tmp_path)
    ops = _ops(monkeypatch, with_pr=True)
    assert bt.propose(RUN_ID, ops) == 7
    assert not any(c[0] in ("create_pr", "push_branch") for c in ops.calls)
    assert "уже смержен" in capsys.readouterr().out


def test_open_pr_with_drifted_pin_keeps_pin_and_warns(
    tmp_path, runs_root, monkeypatch, capsys
) -> None:
    _brief_ready_run(tmp_path)
    ops = _ops(monkeypatch, with_pr=True)
    ops.prs[7] = replace(
        ops.prs[7], state="OPEN", merged_by=None, merged_at=None, merge_commit=None
    )
    ops.add_policy(C1)  # актуальная версия ушла вперёд
    assert bt.propose(RUN_ID, ops) == 7
    out = capsys.readouterr().out
    assert "открыт" in out and "--repropose" in out
    assert not any(c[0] == "create_pr" for c in ops.calls)


def test_open_pr_without_drift_is_reported_without_warning(  # T47
    tmp_path, runs_root, monkeypatch, capsys
) -> None:
    _brief_ready_run(tmp_path)
    ops = _ops(monkeypatch, with_pr=True)
    ops.prs[7] = replace(
        ops.prs[7], state="OPEN", merged_by=None, merged_at=None, merge_commit=None
    )
    assert bt.propose(RUN_ID, ops) == 7
    out = capsys.readouterr().out
    assert "открыт" in out and "ВНИМАНИЕ" not in out
    assert not any(c[0] in ("create_pr", "push_branch") for c in ops.calls)


def test_closed_pr_refuses_with_repropose_hint(
    tmp_path, runs_root, monkeypatch
) -> None:
    _brief_ready_run(tmp_path)
    ops = _ops(monkeypatch, with_pr=True)
    ops.prs[7] = replace(
        ops.prs[7], state="CLOSED", merged_by=None, merged_at=None, merge_commit=None
    )
    with pytest.raises(bt.BriefToolError, match="--repropose"):
        bt.propose(RUN_ID, ops)


def test_branch_without_pr_creates_only_pr(tmp_path, runs_root, monkeypatch) -> None:
    _brief_ready_run(tmp_path)
    ops = _ops(monkeypatch)
    ops.remote_heads[BRANCH] = HEAD
    ops.compare[(BASE, HEAD)] = FILES
    assert bt.propose(RUN_ID, ops) == 42
    assert not any(c[0] in ("commit_paths", "push_branch") for c in ops.calls)


def test_branch_without_pr_after_base_moved_is_still_ours(
    tmp_path, runs_root, monkeypatch
) -> None:
    _brief_ready_run(tmp_path)
    ops = _ops(monkeypatch)
    ops.default_branch = replace(ops.default_branch, sha="f2" * 20)  # база ушла вперёд
    ops.remote_heads[BRANCH] = HEAD
    ops.compare[("f2" * 20, HEAD)] = FILES  # compare судит от merge-base
    assert bt.propose(RUN_ID, ops) == 42


def test_branch_with_foreign_files_refuses(tmp_path, runs_root, monkeypatch) -> None:
    _brief_ready_run(tmp_path)
    ops = _ops(monkeypatch)
    ops.remote_heads[BRANCH] = HEAD
    ops.compare[(BASE, HEAD)] = FILES + (("x.txt", "added"),)
    with pytest.raises(bt.BriefToolError, match="чужое"):
        bt.propose(RUN_ID, ops)


def test_foreign_file_in_base_refuses(tmp_path, runs_root, monkeypatch) -> None:
    _brief_ready_run(tmp_path)
    ops = _ops(monkeypatch)
    ops.files[(BASE, f"{DIR}/brief.md")] = "чужой бриф"
    with pytest.raises(bt.BriefToolError, match="чужой"):
        bt.propose(RUN_ID, ops)
    assert not any(c[0] == "switch_to" for c in ops.calls)


def test_two_prs_on_branch_is_ambiguous(tmp_path, runs_root, monkeypatch) -> None:
    _brief_ready_run(tmp_path)
    ops = _ops(monkeypatch, with_pr=True)
    ops.prs[8] = ops.prs[7]
    with pytest.raises(bt.BriefToolError, match="неоднозначно"):
        bt.propose(RUN_ID, ops)


_REQ = f"{DIR}/{ar.FILE_NAME}"
_BRIEF = f"{DIR}/brief.md"


def _request_at(**changes):
    return lambda ops, sha: ops.files.__setitem__(
        (sha, _REQ), ar.render(request(**changes))
    )


def _extra_file(ops, sha) -> None:
    if 7 in ops.prs:
        ops.set_pr(files=FILES + (("x.txt", "added"),))
    else:
        ops.compare[(BASE, HEAD)] = FILES + (("x.txt", "added"),)


#: (id, порча содержимого предложения по его SHA, фраза отказа) — T48.
_FOREIGN = [
    ("run_id", _request_at(run_id="WS-1-other"), "не про этот прогон"),
    ("ws_id", _request_at(ws_id="WS-2"), "не про этот прогон"),
    ("policy_repo", _request_at(policy_repo="o/other"), "не про этот прогон"),
    ("policy_ref", _request_at(policy_ref="dev"), "не про этот прогон"),
    ("policy_path", _request_at(policy_path="x.env"), "не про этот прогон"),
    (
        "purpose",
        lambda ops, sha: ops.files.__setitem__(
            (sha, _REQ),
            ops.files[(sha, _REQ)].replace("discovery-brief-approval\n", "x\n", 1),
        ),
        "заявка найденного предложения",
    ),
    (
        "brief_bytes",
        lambda ops, sha: ops.files.__setitem__((sha, _BRIEF), DRAFT + "\n"),
        "≠ брифу прогона",
    ),
    ("no_request", lambda ops, sha: ops.files.pop((sha, _REQ)), "без брифа или заявки"),
    (
        "not_a_version",
        lambda ops, sha: ops.policy_versions.__setitem__(P, False),
        "не версия",
    ),
    ("extra_file", _extra_file, "чужое"),
]
#: Пути восстановления §11.3 п.4: (id, SHA, где лежит предложение).
_PATHS = ["merged-pr", "open-pr", "branch-without-pr"]


def _proposal_world(monkeypatch, path: str) -> tuple[ToolOps, str]:
    if path == "branch-without-pr":
        ops = _ops(monkeypatch)
        ops.remote_heads[BRANCH] = HEAD
        ops.compare[(BASE, HEAD)] = FILES
        return ops, HEAD
    ops = _ops(monkeypatch, with_pr=True)
    if path == "open-pr":
        ops.prs[7] = replace(
            ops.prs[7], state="OPEN", merged_by=None, merged_at=None, merge_commit=None
        )
        return ops, HEAD
    return ops, MERGE


@pytest.mark.parametrize("path", _PATHS)
def test_found_proposal_with_our_content_is_ours(  # T48 двойник
    tmp_path, runs_root, monkeypatch, path
) -> None:
    _brief_ready_run(tmp_path)
    ops, _ = _proposal_world(monkeypatch, path)
    assert bt.propose(RUN_ID, ops) == (42 if path == "branch-without-pr" else 7)
    assert not any(c[0] in ("commit_paths", "push_branch") for c in ops.calls)


@pytest.mark.parametrize("path", _PATHS)
@pytest.mark.parametrize(
    ("spoil", "phrase"),
    [(f, p) for _, f, p in _FOREIGN],
    ids=[i for i, _, _ in _FOREIGN],
)
def test_found_proposal_with_foreign_content_refuses(  # T48 (каждое поле — пара)
    tmp_path, runs_root, monkeypatch, path, spoil, phrase
) -> None:
    _brief_ready_run(tmp_path)
    ops, sha = _proposal_world(monkeypatch, path)
    spoil(ops, sha)
    with pytest.raises(bt.BriefToolError, match=phrase):
        bt.propose(RUN_ID, ops)
    effects = ("create_pr", "commit_paths", "push_branch")
    assert not any(c[0] in effects for c in ops.calls)


@pytest.mark.parametrize("bad", ["not_ready", "approved", "blob"])
def test_run_preconditions(tmp_path, runs_root, monkeypatch, bad) -> None:  # T49
    if bad == "not_ready":
        _brief_ready_run(tmp_path, status="waiting_interview")
    elif bad == "approved":
        from tests.forge_fake import SIGNED

        _brief_ready_run(tmp_path, text=SIGNED)
    else:
        _brief_ready_run(tmp_path)
        (rs.run_dir(RUN_ID) / iv.BRIEF_REL).write_text(DRAFT + "\n", encoding="utf-8")
    with pytest.raises(bt.BriefToolError):
        bt.propose(RUN_ID, _ops(monkeypatch))


def test_forge_unavailable_is_retry(tmp_path, runs_root, monkeypatch) -> None:
    _brief_ready_run(tmp_path)
    ops = _ops(monkeypatch)
    ops.unavailable_facts.add("find")
    with pytest.raises(bt.BriefToolError) as exc:
        bt.propose(RUN_ID, ops)
    assert exc.value.retry


def test_dirty_target_refuses_before_git(tmp_path, runs_root, monkeypatch) -> None:
    _brief_ready_run(tmp_path)
    ops = _ops(monkeypatch)
    ops.dirty = True
    with pytest.raises(bt.BriefToolError, match="грязный"):
        bt.propose(RUN_ID, ops)
    assert not any(c[0] == "switch_to" for c in ops.calls)


# --- ревью части B, круг 1: B3 и окна сбоев (T47/T48) ---

from tests.approval_request_cases import DEFECTS  # noqa: E402


def test_branch_moved_between_check_and_pr_is_not_success(  # B3
    tmp_path, runs_root, monkeypatch
) -> None:
    _brief_ready_run(tmp_path)
    ops = _ops(monkeypatch)
    ops.remote_heads[BRANCH] = HEAD
    ops.compare[(BASE, HEAD)] = FILES
    ops.pr_head = "f0" * 20  # ветку сдвинули на тот же набор путей
    with pytest.raises(bt.BriefToolError, match="НЕ годно"):
        bt.propose(RUN_ID, ops)


def test_recovery_compares_files_at_the_checked_sha(  # B3
    tmp_path, runs_root, monkeypatch
) -> None:
    _brief_ready_run(tmp_path)
    ops = _ops(monkeypatch)
    ops.remote_heads[BRANCH] = HEAD
    ops.compare[(BASE, BRANCH)] = FILES  # по имени ветки — не годится
    with pytest.raises(bt.BriefToolError):  # compare по SHA HEAD не задан → retry
        bt.propose(RUN_ID, ops)
    ops.compare[(BASE, HEAD)] = FILES
    assert bt.propose(RUN_ID, ops) == 42


def test_push_failure_is_retry_and_rerun_recovers(
    tmp_path, runs_root, monkeypatch
) -> None:
    _brief_ready_run(tmp_path)
    ops = _ops(monkeypatch)
    ops.push_error = "rejected: fetch first"
    with pytest.raises(bt.BriefToolError) as exc:
        bt.propose(RUN_ID, ops)
    assert exc.value.retry and not any(c[0] == "create_pr" for c in ops.calls)
    ops.push_error = None
    assert bt.propose(RUN_ID, ops) == 42


def test_create_pr_failure_then_rerun_creates_only_pr(
    tmp_path, runs_root, monkeypatch
) -> None:
    _brief_ready_run(tmp_path)
    ops = _ops(monkeypatch)
    ops.create_error = "HTTP 502"
    with pytest.raises(bt.BriefToolError) as exc:
        bt.propose(RUN_ID, ops)
    assert exc.value.retry and BRANCH in ops.remote_heads
    ops.create_error = None
    ops.compare[(BASE, HEAD)] = FILES
    commits = sum(1 for c in ops.calls if c[0] == "commit_paths")
    assert bt.propose(RUN_ID, ops) == 42
    assert (
        sum(1 for c in ops.calls if c[0] == "commit_paths") == commits
    )  # без нового коммита


@pytest.mark.parametrize("field", ["policy_ref", "policy_path"])
def test_found_pr_with_foreign_policy_coordinates_refuses(  # T48 (поля ref/path)
    tmp_path, runs_root, monkeypatch, field
) -> None:
    _brief_ready_run(tmp_path)
    ops = _ops(monkeypatch, with_pr=True)
    value = "dev" if field == "policy_ref" else "x.env"
    ops.files[(MERGE, f"{DIR}/{ar.FILE_NAME}")] = ar.render(request(**{field: value}))
    with pytest.raises(bt.BriefToolError):
        bt.propose(RUN_ID, ops)


@pytest.mark.parametrize(("case", "mutate", "_m"), DEFECTS, ids=[d[0] for d in DEFECTS])
def test_found_pr_with_ambiguous_request_refuses(  # T21b через propose
    tmp_path, runs_root, monkeypatch, case, mutate, _m
) -> None:
    _brief_ready_run(tmp_path)
    ops = _ops(monkeypatch, with_pr=True)
    ops.files[(MERGE, f"{DIR}/{ar.FILE_NAME}")] = mutate(ar.render(request()))
    with pytest.raises(bt.BriefToolError):
        bt.propose(RUN_ID, ops)


def test_local_commit_with_extra_path_refuses_before_push(  # B3 (до push)
    tmp_path, runs_root, monkeypatch
) -> None:
    _brief_ready_run(tmp_path)
    ops = _ops(monkeypatch)
    ops.extra_changed = (".pre-commit-generated",)
    with pytest.raises(bt.BriefToolError, match="не предложение"):
        bt.propose(RUN_ID, ops)
    assert not any(c[0] == "push_branch" for c in ops.calls)
