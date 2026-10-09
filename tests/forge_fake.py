"""Стенд форджа brief-маршрута (§11.4.2–§11.4.3) и согласованный «мир».

`FakeForge` реализует факты форджа, которые читают предикаты акта и
политики; `consistent_world()` строит мир, где всё сходится: brief-PR #7
смержен человеком из политики `P`, в merge-коммите бриф и заявка, политика
с тех пор не менялась. Тест портит ровно один факт (правило пар §11.7).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from governance import approval_request as ar
from governance import discovery_approval as da
from governance.brief_facts import BriefPrFacts, DefaultBranch, PrComment
from governance.facts import Fact, Outcome, unavailable

FIX = Path(__file__).parent / "fixtures" / "discovery_approval"
DRAFT = (FIX / "draft-brief.md").read_text(encoding="utf-8")
SIGNED = (FIX / "signed-brief.md").read_text(encoding="utf-8")
HUMAN = "andrei-shtanakov"
MERGED_AT = "2026-10-08T10:00:00Z"
REPO = "owner/alpha"
DIR = "workstreams/WS-1/spec/00-discovery"
POLICY_REPO = "andrei-shtanakov/approval-policy"
POLICY_REF = "main"
POLICY_PATH = "policy/approvers.env"
P = "a1" * 20
C1 = "c1" * 20
C2 = "c2" * 20
MERGE = "d1" * 20
HEAD = "e1" * 20
BASE = "f1" * 20
PR = 7


@dataclass
class FakeForge:
    """Факты форджа по полям; `unavailable_facts` роняет выбранные факты."""

    prs: dict[int, BriefPrFacts] = field(default_factory=dict)
    files: dict[tuple[str, str], str] = field(default_factory=dict)
    comments: dict[int, list[PrComment]] = field(default_factory=dict)
    default_branch: DefaultBranch = field(
        default_factory=lambda: DefaultBranch("main", BASE)
    )
    policy_head: str = P
    #: sha → (в истории ref, менял файл политики)
    policy_versions: dict[str, bool] = field(default_factory=dict)
    compare: dict[tuple[str, str], tuple[tuple[str, str], ...]] = field(
        default_factory=dict
    )
    unavailable_facts: set[str] = field(default_factory=set)

    def _down(self, name: str) -> bool:
        return name in self.unavailable_facts

    def brief_pr_fact(self, repo_slug: str, pr: int) -> Fact[BriefPrFacts]:
        if self._down("pr") or pr not in self.prs:
            return unavailable(f"PR #{pr}")
        return Fact(Outcome.FOUND, self.prs[pr], "pr")

    def find_brief_pr_fact(self, repo_slug: str, head_ref: str) -> Fact[list[int]]:
        if self._down("find"):
            return unavailable("find")
        found = sorted(n for n, p in self.prs.items() if p.head_ref == head_ref)
        return Fact(Outcome.FOUND, found, "find")

    def default_branch_fact(self, repo_slug: str) -> Fact[DefaultBranch]:
        if self._down("default"):
            return unavailable("default")
        return Fact(Outcome.FOUND, self.default_branch, "default")

    def pr_comments_fact(self, repo_slug: str, pr: int) -> Fact[list[PrComment]]:
        if self._down("comments"):
            return unavailable("comments")
        return Fact(Outcome.FOUND, list(self.comments.get(pr, [])), "comments")

    def repo_file_fact(self, repo_slug: str, sha: str, path: str) -> Fact[str]:
        if self._down("file"):
            return unavailable("file")
        text = self.files.get((sha, path))
        if text is None:
            return Fact(Outcome.ABSENT, None, f"нет {path}@{sha}")
        return Fact(Outcome.FOUND, text, "file")

    def policy_version_fact(self, repo_slug: str, branch: str, path: str) -> Fact[str]:
        if self._down("policy"):
            return unavailable("policy")
        return Fact(Outcome.FOUND, self.policy_head, "head")

    def policy_version_fact_at(
        self, repo_slug: str, ref: str, path: str, sha: str
    ) -> Fact[bool]:
        if self._down("history"):
            return unavailable("history")
        return Fact(Outcome.FOUND, self.policy_versions.get(sha, False), "history")

    def compare_files_fact(
        self, repo_slug: str, base: str, head: str
    ) -> Fact[tuple[tuple[str, str], ...]]:
        if self._down("compare") or (base, head) not in self.compare:
            return unavailable("compare")
        return Fact(Outcome.FOUND, self.compare[(base, head)], "compare")

    # Удобства мира — не часть контракта Ops.
    def add_policy(self, sha: str, accounts: str = HUMAN, *, head: bool = True) -> None:
        """Новая версия политики `sha` с составом `accounts`."""
        self.policy_versions[sha] = True
        self.files[(sha, POLICY_PATH)] = f"AUTHORIZED_APPROVER_ACCOUNTS={accounts}\n"
        if head:
            self.policy_head = sha

    def reconfirm(
        self,
        sha: str,
        *,
        author: str = HUMAN,
        created: str = "2026-10-09T00:00:00Z",
        edited: str | None = None,
        body: str | None = None,
    ) -> None:
        """Комментарий-подтверждение политики в brief-PR."""
        text = body if body is not None else f"policy-reconfirm: {POLICY_REPO}@{sha}"
        self.comments.setdefault(PR, []).append(
            PrComment(
                f"C{len(self.comments.get(PR, []))}", author, text, created, edited
            )
        )

    def set_pr(self, **changes: object) -> None:
        """Заменить поля brief-PR #7."""
        self.prs[PR] = replace(self.prs[PR], **changes)  # type: ignore[arg-type]


def request(**changes: str) -> ar.ApprovalRequest:
    """Заявка мира (по умолчанию — согласованная)."""
    base = ar.ApprovalRequest(
        brief_self_hash=da.self_hash(DRAFT),
        policy_repo=POLICY_REPO,
        policy_ref=POLICY_REF,
        policy_path=POLICY_PATH,
        policy_sha=P,
        run_id="WS-1-abc123",
        ws_id="WS-1",
    )
    return replace(base, **changes)


def consistent_world(monkeypatch=None) -> FakeForge:
    """Мир, где brief-PR #7 — действительный акт одобрения брифа фикстуры."""
    if monkeypatch is not None:
        monkeypatch.delenv("AUTHORIZED_APPROVER_ACCOUNTS", raising=False)
    forge = FakeForge()
    forge.prs[PR] = BriefPrFacts(
        number=PR,
        state="MERGED",
        base_ref="main",
        head_ref="brief/WS-1",
        head_sha=HEAD,
        files=((f"{DIR}/brief.md", "added"), (f"{DIR}/{ar.FILE_NAME}", "added")),
        merged_by=HUMAN,
        merged_at=MERGED_AT,
        merge_commit=MERGE,
    )
    for sha in (MERGE, HEAD):
        forge.files[(sha, f"{DIR}/brief.md")] = DRAFT
        forge.files[(sha, f"{DIR}/{ar.FILE_NAME}")] = ar.render(request())
    forge.add_policy(P)
    return forge
