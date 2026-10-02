"""Гейт [x]: происхождение штампа (спека §7.2a п.6, решение владельца 2026-10-02)."""

from __future__ import annotations

import subprocess

import pytest

from governance import acceptance_provenance as ap
from governance import approval_facts as af
from governance import criteria_accept as ca

SPEC = "workstreams/ws/spec"
REL = f"{SPEC}/90-acceptance-closure.md"
REQ = "#### FR-01: A\n**Priority**: Must\n"
BEH_TEST = (
    "#### BEH-01: a\n`traces: [FR-01]`\n"
    "- **checked_by**: `status: planned` `kind: unit` `owner: qa`\n"
)
BEH_HUMAN = BEH_TEST + (
    "#### BEH-02: b\n`traces: [FR-01]`\n"
    "- **checked_by**: `status: planned` `kind: manual` `owner: qa`\n"
)
ACC_TEST = "#### AC-01: a · verification: test\ntraces: [FR-01]\nscenarios: [BEH-01]\n"
ACC_HUMAN = ACC_TEST + (
    "#### AC-02: b · verification: manual\ntraces: [FR-01]\nscenarios: [BEH-02]\n"
)
SHA = "a" * 40


def _git(repo, *args):
    return subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "-c",
            "core.autocrlf=false",
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _source():
    repo, _ref, path = af.policy_source()
    return f"github:{repo}@{SHA}:{path}"


class FakeForge:
    def __init__(self, merge, login, files=None, default="master", base="master"):
        self.facts = {
            "state": "MERGED",
            "baseRefName": base,
            "mergeCommit": {"oid": merge},
            "mergedBy": {"login": login},
        }
        self.files = files if files is not None else [REL]
        self.default = default
        self.down = False
        self.on_ref = True

    def pr_facts(self, slug, pr):
        return None if self.down else dict(self.facts)

    def pr_files(self, slug, pr):
        return None if self.down else list(self.files)

    def default_branch(self, slug):
        return None if self.down else self.default

    def policy_file(self, repo, sha, path):
        if self.down or sha != SHA or repo != af.policy_source()[0]:
            return None
        return f"{af.APPROVER_ALLOWLIST_ENV}=owner-human\n"

    def policy_on_ref(self, repo, sha, ref):
        return None if self.down else self.on_ref


def _signed(tmp_path, human=True, source=None):
    """Репо: бандл на пине → предложение (merge) → штамп. → (repo, merge, stamp)."""
    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", "-b", "master", str(repo)], check=True)
    spec = repo / SPEC
    spec.mkdir(parents=True)
    (spec / "10-requirements.md").write_text(REQ)
    (spec / "15-behaviour-spec.md").write_text(BEH_HUMAN if human else BEH_TEST)
    (spec / "25-acceptance.md").write_text(ACC_HUMAN if human else ACC_TEST)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "bundle")
    pin = _git(repo, "rev-parse", "HEAD")
    closure = f"---\nclosure: traced\nbundle_pin: {pin}\n---\nтело\n"
    proposal = ca.proposal_text(closure, source or _source())
    (repo / REL).write_text(proposal)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "proposal")
    merge = _git(repo, "rev-parse", "HEAD")
    stamp = ca.stamp_text(proposal, merge_oid=merge, pr=7)
    (repo / REL).write_text(stamp)
    _git(repo, "commit", "-qam", "stamp")
    return repo, merge, stamp


def _check(repo, text, forge, slug="o/r"):
    return ap.stamp_findings(repo, SPEC, text, slug=slug, forge=forge)


def test_human_signed_stamp_is_green(tmp_path):
    repo, merge, stamp = _signed(tmp_path)
    assert _check(repo, stamp, FakeForge(merge, "owner-human")) == []


def test_test_only_agent_merge_is_green(tmp_path):
    repo, merge, stamp = _signed(tmp_path, human=False)
    assert _check(repo, stamp, FakeForge(merge, "ai-prosto")) == []


def test_agent_merge_with_human_criterion_is_red(tmp_path):
    repo, merge, stamp = _signed(tmp_path)
    assert _check(repo, stamp, FakeForge(merge, "ai-prosto"))


def test_outsider_merge_is_red(tmp_path):
    repo, merge, stamp = _signed(tmp_path, human=False)
    assert _check(repo, stamp, FakeForge(merge, "stranger"))


def test_fabricated_stamp_is_red(tmp_path):
    """Выдуманный штамп: accepted_merge указывает не на предложение."""
    repo, merge, stamp = _signed(tmp_path)
    pin = _git(repo, "rev-parse", "HEAD~2")
    fake = stamp.replace(merge, pin)
    assert _check(repo, fake, FakeForge(pin, "owner-human"))


def test_foreign_pr_is_red(tmp_path):
    """Ссылка на чужое предложение: PR влит человеком, но правит иной файл
    или его мерж-коммит — не accepted_merge."""
    repo, merge, stamp = _signed(tmp_path)
    assert _check(repo, stamp, FakeForge(merge, "owner-human", files=["other.md"]))
    assert _check(repo, stamp, FakeForge("b" * 40, "owner-human"))


def test_edited_after_signature_is_red(tmp_path):
    repo, merge, stamp = _signed(tmp_path)
    assert _check(
        repo, stamp.replace("тело", "другое тело"), FakeForge(merge, "owner-human")
    )


def test_pr_into_other_branch_or_not_merged_is_red(tmp_path):
    repo, merge, stamp = _signed(tmp_path)
    assert _check(repo, stamp, FakeForge(merge, "owner-human", base="dev"))
    forge = FakeForge(merge, "owner-human")
    forge.facts["state"] = "CLOSED"
    assert _check(repo, stamp, forge)


def test_unavailable_forge_is_red(tmp_path):
    repo, merge, stamp = _signed(tmp_path)
    forge = FakeForge(merge, "owner-human")
    forge.down = True
    assert _check(repo, stamp, forge)
    assert _check(repo, stamp, None)
    assert _check(repo, stamp, FakeForge(merge, "owner-human"), slug=None)


def test_untrusted_policy_source_is_red(tmp_path):
    """Ревью круга 4 R4-m2: чужой источник — в самом ПРЕДЛОЖЕНИИ (штамп честный)."""
    evil = f"github:evil/policy@{SHA}:policy/approvers.env"
    repo, merge, stamp = _signed(tmp_path, source=evil)
    assert _check(repo, stamp, FakeForge(merge, "owner-human"))


def test_policy_sha_off_ref_is_red(tmp_path):
    """R4-m1: SHA политики не в истории APPROVAL_POLICY_REF (ветка, форк)."""
    repo, merge, stamp = _signed(tmp_path)
    forge = FakeForge(merge, "owner-human")
    forge.on_ref = False
    assert _check(repo, stamp, forge)


def _add_manual_ac(repo):
    spec = repo / SPEC
    (spec / "15-behaviour-spec.md").write_text(BEH_HUMAN)
    (spec / "25-acceptance.md").write_text(ACC_HUMAN)
    _git(repo, "commit", "-qam", "manual AC")


def test_stale_test_only_stamp_after_manual_ac_is_red(tmp_path):
    """Ревью круга 4 R4-B1: штамп test-only версии, затем в бандл добавлен
    ручной AC — штамп не для текущего бандла."""
    repo, merge, stamp = _signed(tmp_path, human=False)
    _add_manual_ac(repo)
    assert _check(repo, stamp, FakeForge(merge, "ai-prosto"))


def test_old_test_only_pin_in_new_proposal_is_red(tmp_path):
    """R4-B1, атака: бандл уже с ручным AC, агент пишет предложение со старым
    test-only пином и мержит его сам."""
    repo, _merge, _stamp = _signed(tmp_path, human=False)
    old_pin = _git(repo, "rev-parse", "HEAD~2")
    _add_manual_ac(repo)
    closure = f"---\nclosure: traced\nbundle_pin: {old_pin}\n---\nтело\n"
    proposal = ca.proposal_text(closure, _source())
    (repo / REL).write_text(proposal)
    _git(repo, "commit", "-qam", "proposal with old pin")
    merge = _git(repo, "rev-parse", "HEAD")
    stamp = ca.stamp_text(proposal, merge_oid=merge, pr=8)
    (repo / REL).write_text(stamp)
    _git(repo, "commit", "-qam", "stamp")
    assert _check(repo, stamp, FakeForge(merge, "ai-prosto"))
    assert ap.pin_current(repo, SPEC, old_pin) is False


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ('{"status": "ahead"}', True),
        ('{"status": "identical"}', True),
        ('{"status": "behind"}', False),
        ('{"status": "diverged"}', False),
        ('{"status": "weird"}', None),
        ("not json", None),
        (None, None),
    ],
)
def test_real_forge_policy_on_ref_semantics(monkeypatch, payload, expected):
    """R5-M1: sha...ref — `ahead`/`identical` значат «sha в истории ref»."""
    monkeypatch.setattr(ap, "_gh", lambda *a: payload)
    assert ap.RealForge().policy_on_ref("o/p", SHA, "main") is expected


def test_real_forge_policy_file_decodes_base64(monkeypatch):
    import base64 as b64

    body = b64.b64encode(f"{af.APPROVER_ALLOWLIST_ENV}=x\n".encode()).decode()
    monkeypatch.setattr(
        ap, "_gh", lambda *a: f'{{"encoding": "base64", "content": "{body}"}}'
    )
    expected = f"{af.APPROVER_ALLOWLIST_ENV}=x\n"
    assert ap.RealForge().policy_file("o/p", SHA, "p") == expected
    monkeypatch.setattr(ap, "_gh", lambda *a: None)
    assert ap.RealForge().policy_file("o/p", SHA, "p") is None


def test_crlf_nodes_are_current(tmp_path):
    """Ревью круга 6 m6-2: узел с CRLF — не ложный «не текущий бандл»."""
    repo, _merge, _stamp = _signed(tmp_path)
    node = repo / SPEC / "10-requirements.md"
    node.write_bytes(REQ.replace("\n", "\r\n").encode())
    _git(repo, "commit", "-qam", "crlf")
    assert ap.pin_current(repo, SPEC, _git(repo, "rev-parse", "HEAD")) is True


def test_non_sha_refs_never_reach_git(tmp_path):
    """R4-m5: ссылка из файла — только SHA (не опция git)."""
    repo, merge, stamp = _signed(tmp_path)
    assert _check(
        repo, stamp.replace(merge, "--output=x"), FakeForge(merge, "owner-human")
    )
    assert ap.human_needed(repo, SPEC, "--output=x") is None
    assert ap.pin_current(repo, SPEC, "--output=x") is None


@pytest.mark.parametrize(("human", "need"), [(True, True), (False, False)])
def test_human_needed_by_graph_at_pin(tmp_path, human, need):
    repo, _merge, _stamp = _signed(tmp_path, human=human)
    pin = _git(repo, "rev-parse", "HEAD~2")
    assert ap.human_needed(repo, SPEC, pin) is need
    assert ap.human_needed(repo, SPEC, "f" * 40) is None
    assert ap.human_needed(repo, SPEC, None) is None


def test_policy_accounts():
    key = af.APPROVER_ALLOWLIST_ENV
    assert af.policy_accounts(f"{key}=a, b\n") == frozenset({"a", "b"})
    assert af.policy_accounts("") is None
    assert af.policy_accounts(f"{key}= , \n") is None
    assert af.policy_accounts(f"{key}=a\n{key}=b\n") is None
