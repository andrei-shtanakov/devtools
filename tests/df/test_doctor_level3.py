"""Stage 3a, Task 13: `darkfactory doctor --product P`, level 3 «доверие»
(spec 2.4 rev 11, 6.1, 6.2, 8) and the operator's line (spec 4.5, O7).

The world is `test_doctor_level2`'s green level-2 world plus what `roles
setup` and `setup-trust write` leave: `trust.toml` with the history key, the
profile `/etc/df/history-gh` (root's, group `df-history` by its `groups.toml`
record, `0750`/`0640`) and the group with exactly its members. `trust-status`
is the seam `setup_trust.trust_status_call`, answering response lines of the
publisher's shape (`test_setup_trust.status_line`), parsed by `doctor` with
`request.parse_trust_status`. The group database is a seam too (`plat`,
`grp.getgrall`): the tests never read or change the host's groups.
"""

from __future__ import annotations

import dataclasses
import grp
import json
import os
from pathlib import Path

import pytest

from df import doctor, fsutil, setup_trust, sudoers
from df import platform as plat
from df.config import GroupRecord, dump_groups
from df.doctor import Check, level1_checks
from df.publisher.github import Installation, SelfCheckResult
from df.publisher.perimeter import MIGRATION_REQUIRED
from df.roles import HISTORY_GROUP, history_members
from df.stage2_config import TrustBinding, dump_trust
from tests.df.install_world import World
from tests.df.test_doctor import by_id, snap
from tests.df.test_doctor_level2 import (  # noqa: F401 (fixtures)
    APP_ID,
    INSTALLATION,
    L2,
    OPERATOR,
    PRODUCT,
    _boot,
    answer,
    fake_installed,
    l2,
    level2_of,
)
from tests.df.test_setup_trust import POLICY_ID, TRUST, TRUST_ID, status_line

LEVEL3_IDS = [
    "trust.binding",
    "trust.history",
    "trust.status",
    "trust.unsupported_size",
    "perimeter.schema",
]
UNFINISHED = "trust_observation_unfinished: 7"


@dataclasses.dataclass
class L3:
    """Level 3 over the green level-2 world: `trust-status`'s next answer,
    the group's members, and the calls of the seam."""

    l2: L2
    trust: bytes | Exception
    members: frozenset[str]
    gid: int
    asked: list[str] = dataclasses.field(default_factory=list)

    @property
    def w(self) -> World:
        return self.l2.w

    def level3(self, operator: str | None = OPERATOR) -> doctor.Level3:
        s = snap(self.w)
        uid = os.geteuid()
        level1 = level1_checks(s, self.w.lay, euid=uid)
        second = level2_of(self.l2, operator=operator)
        return doctor.level3(
            s, self.w.lay, euid=uid, operator=operator, level1=level1, second=second
        )

    def checks(self, operator: str | None = OPERATOR) -> dict[str, Check]:
        return by_id(self.level3(operator).checks)

    def bind(self, *, history: bool = True, branch: str = "main") -> None:
        lay = self.w.lay
        binding = TrustBinding(
            "github.com",
            TRUST_ID,
            TRUST,
            branch,
            POLICY_ID,
            "public",
            str(lay.history_gh) if history else None,
        )
        fsutil.write_atomic(lay.etc / "trust.toml", dump_trust(binding))


def _profile(lay_dir: Path) -> int:
    """The history profile as `setup_trust.write_binding` leaves it (names
    from `profile_files`, modes from its constants); its real GID."""
    lay_dir.mkdir()
    lay_dir.chmod(setup_trust.PROFILE_DIR_MODE)
    for name in setup_trust.profile_files("x"):
        path = lay_dir / name
        path.write_text("never read by doctor\n")
        path.chmod(setup_trust.PROFILE_FILE_MODE)
    return os.lstat(lay_dir).st_gid


@pytest.fixture()
def l3(request: pytest.FixtureRequest) -> L3:
    """Level 2 green; the binding, profile and group are what `roles setup`
    and `setup-trust write` leave; `trust-status` answers `clear`."""
    green: L2 = request.getfixturevalue("l2")
    lay, mp = green.lay, green.w.mp
    gid = _profile(lay.history_gh)
    world = L3(green, status_line(), frozenset(history_members(OPERATOR)), gid)
    record = GroupRecord(HISTORY_GROUP, gid, "ready", "2026-11-05T00:00:00Z")
    fsutil.write_atomic(lay.groups, dump_groups({HISTORY_GROUP: record}))
    mp.setattr(plat, "group_gid", lambda name: world.gid)
    mp.setattr(plat, "group_members", lambda name: world.members)
    mp.setattr(grp, "getgrall", lambda: [])

    def trust_status(_layout: object, *, purpose: str = "setup-trust") -> bytes:
        world.asked.append(f"trust-status ({purpose})")
        if isinstance(world.trust, Exception):
            raise world.trust
        return world.trust

    mp.setattr(setup_trust, "trust_status_call", trust_status)
    world.bind()
    return world


def statuses(checks: dict[str, Check]) -> dict[str, str]:
    return {k: c.status for k, c in checks.items()}


# --- the positive control ---


def test_level3_trust_part_green(
    l3: L3, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control: every trust check `ok`; stage 3a never says level 3
    is reached (3b's part is not checked); level 1 stays reached with the
    3a files present."""
    checks = l3.checks()
    assert list(checks) == LEVEL3_IDS
    assert statuses(checks) == dict.fromkeys(LEVEL3_IDS, "ok"), checks
    assert checks["trust.status"].reason.startswith("clear; снимок c")
    assert checks["perimeter.schema"].reason == "схема 2"
    assert l3.asked == ["trust-status (doctor)"]
    monkeypatch.setenv("SUDO_USER", OPERATOR)
    capsys.readouterr()
    assert l3.l2.doctor([]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[:4] == [
        "уровень 1: достигнут",
        "уровень 2: достигнут",
        "уровень 3: не проверено (этап 3b); проверки доверия: ok",
        "уровень 4: не проверено (этап 4)",
    ]


# --- shown apart (spec 2.4 rev 11) ---


def test_level3_shown_separately_from_level(
    l3: L3, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken level 3 changes neither level 1 nor level 2 nor the exit:
    its checks are in their own block, never among level 2's."""
    l3.trust = status_line("blocked", findings=[_finding("rebase_allowed")])
    monkeypatch.setenv("SUDO_USER", OPERATOR)
    capsys.readouterr()
    assert l3.l2.doctor([]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[1] == "уровень 2: достигнут"
    assert out[2].startswith("уровень 3: не достигнут — trust.status: нарушено")
    header = out.index(doctor._LEVEL3_HEADER)  # noqa: SLF001
    block = [line.split(":")[0].strip() for line in out[header + 1 :]][:5]
    assert block == LEVEL3_IDS
    above = [line.split(":")[0].strip() for line in out[:header]]
    assert not set(LEVEL3_IDS) & set(above)
    assert l3.l2.doctor(["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["level2"] == "reached" and data["level3"] == "not reached"
    assert [c["id"] for c in data["level3_checks"]] == LEVEL3_IDS
    assert not {c["id"] for c in data["level2_checks"]} & set(LEVEL3_IDS)


def test_level3_below_level2_names_it(l3: L3) -> None:
    l3.l2.probe_current = False  # `models.access` not ok: level 2 not reached
    second = level2_of(l3.l2)
    s = snap(l3.w)
    level1 = level1_checks(s, l3.w.lay, euid=os.geteuid())
    third = l3.level3()
    assert (
        doctor.level3_brief(level1, second, third)
        == "уровень 3: не достигнут — уровень 2 не достигнут"
    )


def test_level3_not_asked_without_root_or_level1(l3: L3) -> None:
    s = snap(l3.w)
    level1 = level1_checks(s, l3.w.lay, euid=os.geteuid())
    second = level2_of(l3.l2)
    other = os.geteuid() + 1
    third = doctor.level3(
        s, l3.w.lay, euid=other, operator=OPERATOR, level1=level1, second=second
    )
    assert {c.reason for c in third.checks} == {doctor.NEED_SUDO}
    broken = [dataclasses.replace(level1[0], status="нарушено", reason="x")]
    third = doctor.level3(
        s, l3.w.lay, euid=os.geteuid(), operator=OPERATOR, level1=broken, second=second
    )
    assert {c.status for c in third.checks} == {"не проверено"}
    assert third.side_effects == () and l3.asked == []


# --- side effects (spec 8) ---


def test_side_effects_listed(
    l3: L3, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probes' side effects are in the output: `publisher.precheck`'s
    reap and `trust-status`'s observation; without a binding `trust-status`
    is not asked and not listed."""
    assert l3.level3().side_effects == (
        doctor.SIDE_EFFECT_PRECHECK,
        doctor.SIDE_EFFECT_TRUST,
    )
    assert "старше 230 с" in doctor.SIDE_EFFECT_TRUST
    monkeypatch.setenv("SUDO_USER", OPERATOR)
    capsys.readouterr()
    l3.l2.doctor([])
    out = capsys.readouterr().out.splitlines()
    i = out.index("побочные эффекты проб (спека §8):")
    assert out[i + 1 : i + 3] == [
        f"  {doctor.SIDE_EFFECT_PRECHECK}",
        f"  {doctor.SIDE_EFFECT_TRUST}",
    ]
    (l3.w.lay.etc / "trust.toml").unlink()
    l3.asked.clear()
    third = l3.level3()
    assert third.side_effects == (doctor.SIDE_EFFECT_PRECHECK,)
    assert l3.asked == []
    l3.l2.doctor(["--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["side_effects"] == [doctor.SIDE_EFFECT_PRECHECK]


# --- trust.binding and trust.history ---


def test_no_binding_is_broken_and_asks_nothing(l3: L3) -> None:
    (l3.w.lay.etc / "trust.toml").unlink()
    checks = l3.checks()
    assert checks["trust.binding"].status == "нарушено"
    assert checks["trust.binding"].reason == doctor.NO_BINDING
    for cid in ("trust.history", "trust.status", "trust.unsupported_size"):
        assert checks[cid].status == "не проверено", cid
        assert checks[cid].reason.startswith("trust.binding: "), cid
    assert l3.asked == []


def test_no_history_key_trust_history_broken(l3: L3) -> None:
    """Spec 6.2 (O14): a binding written by hand without the key --
    «нарушено» by name; `trust-status` still runs (anonymously)."""
    l3.bind(history=False)
    l3.trust = status_line()
    checks = l3.checks()
    assert checks["trust.history"].status == "нарушено"
    assert checks["trust.history"].reason == doctor.NO_HISTORY_KEY
    assert checks["trust.binding"].status == "ok"
    assert l3.asked == ["trust-status (doctor)"]


def test_history_group_extra_member_broken(l3: L3) -> None:
    """Spec 4.1 rev 11: the members are exactly `history_members`; an extra
    one (here a model role) or a missing one is «нарушено»."""
    l3.members = frozenset({*history_members(OPERATOR), "df-exec"})
    c = l3.checks()["trust.history"]
    assert c.status == "нарушено"
    assert any("лишние члены ['df-exec']" in p for p in c.problems), c.problems
    l3.members = frozenset({"df-publisher", "df-precheck"})
    c = l3.checks()["trust.history"]
    assert any(f"нет членов ['{OPERATOR}']" in p for p in c.problems), c.problems


def test_history_gid_other_than_recorded_broken(l3: L3) -> None:
    """The OS group's GID, and the profile's group, against the GID
    `groups.toml` recorded by intent."""
    l3.gid += 1
    c = l3.checks()["trust.history"]
    assert c.status == "нарушено"
    assert c.problems == (
        f"группа {HISTORY_GROUP}: gid {l3.gid}, записан {l3.gid - 1}",
    )
    record = GroupRecord(HISTORY_GROUP, l3.gid, "ready", "2026-11-05T00:00:00Z")
    fsutil.write_atomic(l3.w.lay.groups, dump_groups({HISTORY_GROUP: record}))
    c = l3.checks()["trust.history"]
    profile = l3.w.lay.history_gh
    assert f"{profile}: группа gid {l3.gid - 1}, {HISTORY_GROUP} — {l3.gid}" in (
        c.problems
    )


def test_history_profile_modes(l3: L3) -> None:
    hosts = l3.w.lay.history_gh / "hosts.yml"
    hosts.chmod(0o644)
    c = l3.checks()["trust.history"]
    assert c.status == "нарушено"
    assert c.problems == (f"{hosts}: права 0644, нужны 0640",)
    hosts.unlink()
    c = l3.checks()["trust.history"]
    assert c.problems == (f"нет {hosts}",)


def test_history_without_operator_unavailable(l3: L3) -> None:
    c = l3.checks(operator=None)["trust.history"]
    assert c.status == "данные недоступны"
    assert doctor.NO_OPERATOR in c.reason


# --- trust.status ---


def test_trust_status_schema_checked(l3: L3) -> None:
    """Root checks the answer's schema and that `result.root` is the binding
    it read itself (`parse_trust_status`): another root, an extra key or not
    one line is «нарушено»."""
    l3.trust = status_line(root=("github.com", TRUST_ID + 1, "main"))
    c = l3.checks()["trust.status"]
    assert (c.status, c.reason) == (
        "нарушено",
        "trust-status: result.root is not the binding's root",
    )
    line = json.loads(status_line())
    line["result"]["extra"] = 1
    l3.trust = (json.dumps(line) + "\n").encode()
    assert l3.checks()["trust.status"].status == "нарушено"
    l3.trust = status_line() * 2
    assert l3.checks()["trust.status"].reason == "trust-status: нет ответа (2 строк)"


def test_trust_status_timeout_not_checked(l3: L3) -> None:
    """Carry 10: no answer (a timeout included) is «не проверено»."""
    l3.trust = OSError("trust-status: the call failed (code -9)")
    c = l3.checks()["trust.status"]
    assert c.status == "не проверено"
    assert c.reason.startswith("trust-status: нет ответа")


def test_trust_status_unfinished_matched_by_prefix(l3: L3) -> None:
    l3.trust = status_line("unavailable", UNFINISHED)
    c = l3.checks()["trust.status"]
    assert c.status == "данные недоступны"
    assert c.reason == f"наблюдение не завершено ({UNFINISHED}): повторите doctor"


def test_trust_status_unverified_listed(l3: L3) -> None:
    line = json.loads(status_line())
    line["result"]["unverified"] = ["allow_rebase_merge@df-trust"]
    l3.trust = (json.dumps(line) + "\n").encode()
    c = l3.checks()["trust.status"]
    assert c.status == "ok"
    assert "; не проверено: allow_rebase_merge@df-trust; " in c.reason
    line["result"]["unverified"] = ["something_else"]
    l3.trust = (json.dumps(line) + "\n").encode()
    assert l3.checks()["trust.status"].status == "нарушено"


def _finding(kind: str, detail: str = "d") -> dict[str, str]:
    return {"kind": kind, "role": "df-trust", "detail": detail}


def test_trust_status_blocked_lists_findings(l3: L3) -> None:
    l3.trust = status_line("blocked", findings=[_finding("ruleset_missing")])
    c = l3.checks()["trust.status"]
    assert c.status == "нарушено"
    assert "находка ruleset_missing (df-trust): d" in c.problems


# --- trust.unsupported_size (spec 6.1 «Размер вердикта») ---


def test_unsupported_size_named_in_reason_names_exit(l3: L3) -> None:
    """Defensive (M-1): the publisher does not name `unsupported_size` in a
    reason today; if it does, the field and the exit of `epochs` are named
    verbatim (`setup_trust.EPOCHS_EXIT`)."""
    l3.trust = status_line("unavailable", "unsupported_size: epochs")
    c = l3.checks()["trust.unsupported_size"]
    assert c.status == "нарушено"
    assert c.problems == (f"unsupported_size: epochs — {setup_trust.EPOCHS_EXIT}",)


@pytest.mark.parametrize("findings", [[], [{"kind": "ruleset_missing"}]])
def test_unsupported_size_of_a_blocked_root_not_checked(
    l3: L3, findings: list[dict[str, str]]
) -> None:
    """I-2: `blocked` does not name its cause -- an earlier registered
    finding, a record-violation, a pending or a `db_restore` event, or К.
    «не проверено», neutral text; the epochs exit only as one possibility."""
    l3.trust = status_line("blocked", findings=[_finding(f["kind"]) for f in findings])
    c = l3.checks()["trust.unsupported_size"]
    assert c.status == "не проверено"
    assert c.reason == f"{doctor.UNNAMED_BLOCK}; {doctor.EPOCHS_MAYBE}"
    assert "консервативным событием" not in c.reason
    assert "если причина — unsupported_size: epochs" in c.reason
    if not findings:
        assert l3.checks()["trust.status"].problems[1] == doctor.UNNAMED_BLOCK


@pytest.mark.parametrize("reason", [UNFINISHED, "integrity", "not_accepted"])
def test_unsupported_size_unavailable_is_never_green(l3: L3, reason: str) -> None:
    """I-1, spec 2.4: an unavailable answer proves nothing about the size;
    «данные недоступны» with `trust.status`'s own reason."""
    l3.trust = status_line("unavailable", reason)
    checks = l3.checks()
    c = checks["trust.unsupported_size"]
    assert c.status == "данные недоступны"
    assert c.reason == checks["trust.status"].reason


def test_unsupported_size_no_answer_not_checked(l3: L3) -> None:
    l3.trust = OSError("trust-status: the call failed (code -9)")
    assert l3.checks()["trust.unsupported_size"].status == "не проверено"


def test_other_roots_gap_is_stated(l3: L3) -> None:
    """M-5: until the publisher answers the known roots, the gap is named,
    never silent."""
    assert l3.checks()["trust.status"].reason.endswith(doctor.OTHER_ROOTS)
    l3.trust = status_line("blocked", findings=[_finding("ruleset_missing")])
    assert doctor.OTHER_ROOTS in l3.checks()["trust.status"].problems


def test_unsupported_size_of_root_binding(l3: L3) -> None:
    """Root's own binding outside the publisher's limits (`binding_problem`,
    a branch over 255 bytes): named without asking anyone else."""
    l3.bind(branch="b" * 300)
    l3.trust = status_line(root=("github.com", TRUST_ID, "b" * 300))
    c = l3.checks()["trust.unsupported_size"]
    assert c.status == "нарушено"
    assert c.problems == ("unsupported_size: binding.branch",)


# --- perimeter.schema ---


def test_schema_v1_names_migration(l3: L3) -> None:
    """Schema `"1"` (Task 8): «нарушено» with `MIGRATION_REQUIRED`, from the
    `self-check` answer and from `trust-status` alike."""
    inst = Installation(INSTALLATION, "acme", (PRODUCT,), {"contents": "write"})
    problem = f"perimeter_unavailable: {MIGRATION_REQUIRED}"
    result = SelfCheckResult((inst,), (problem,), None).to_json()
    l3.l2.self_check = answer("done", None, result)
    l3.trust = status_line("unavailable", MIGRATION_REQUIRED)
    c = l3.checks()["perimeter.schema"]
    assert (c.status, c.reason) == ("нарушено", MIGRATION_REQUIRED)
    assert c.reason.endswith("выполните darkfactory roles setup")
    (l3.w.lay.etc / "trust.toml").unlink()
    c = l3.checks()["perimeter.schema"]
    assert (c.status, c.reason) == ("нарушено", MIGRATION_REQUIRED)
    assert APP_ID  # the product's App is not what this line judges


# --- the operator's line (spec 4.5, O7) ---


def test_operator_two_results_never_pass_ii(l3: L3) -> None:
    """(i) `ok` on a green set, «нарушено» with a planted rule on
    `df-precheck`; (ii) «не проверено, принято исключение» in both."""
    rights = by_id(list(level2_of(l3.l2).rights))
    assert list(rights) == ["operator.rights", "operator.isolation"]
    assert rights["operator.rights"].status == "ok"
    iso = rights["operator.isolation"]
    assert (iso.status, iso.reason) == ("не проверено", sudoers.OPERATOR_EXCEPTION)
    lay = l3.w.lay
    have = sudoers.expected_grants(lay, OPERATOR)[OPERATOR]
    planted = sudoers.Grant(
        "df-precheck", sudoers.TAGS, str(lay.libexec / "df-precheck")
    )
    grants = have | {planted}
    listing = sudoers.Listing((), (), grants, grants)
    l3.w.mp.setattr(sudoers, "read_listing", lambda _n: listing)
    rights = by_id(list(level2_of(l3.l2).rights))
    assert rights["operator.rights"].status == "нарушено"
    assert rights["operator.isolation"].status == "не проверено"
    assert level2_of(l3.l2, euid=os.geteuid() + 1).rights[1].status == "не проверено"


def test_operator_rights_of_another_operators_file_unavailable(l3: L3) -> None:
    """M-4, R13-10: the file was installed for alice, doctor runs for bob --
    (i) is not judged for bob («данные недоступны», `roles.sudoers`' hint)."""
    rights = by_id(list(level2_of(l3.l2, operator="bob").rights))
    c = rights["operator.rights"]
    assert (c.status, c.reason) == (
        "данные недоступны",
        (
            f"файл установлен для оператора {OPERATOR}, проверка для bob — "
            f"укажите --operator {OPERATOR}"
        ),
    )
    assert rights["operator.isolation"].status == "не проверено"


# --- round 1b: the publisher's listing of blocking events ---


OLD_ROOT = ("github.com", TRUST_ID + 7, "main")


def _row(
    kind: str,
    reason: str | None = None,
    *,
    root: tuple[str, int, str] = ("github.com", TRUST_ID, "main"),
    event_id: int = 1,
    state: str = "registered",
    epoch: int | None = 1,
) -> dict[str, object]:
    host, repo, branch = root
    return {
        "root": {"host": host, "repository_id": repo, "branch": branch},
        "event_id": event_id,
        "kind": kind,
        "state": state,
        "epoch": epoch,
        "reason": reason,
    }


def listed(line: bytes, rows: list[dict[str, object]], omitted: int = 0) -> bytes:
    """`line` with the publisher's `blocking` listing."""
    data = json.loads(line)
    data["result"]["blocking"] = {"events": rows, "omitted": omitted}
    return (json.dumps(data) + "\n").encode()


def test_listing_names_the_k_cause_epochs(l3: L3) -> None:
    """A К event naming `unsupported_size: epochs`: «нарушено» with the field
    and `EPOCHS_EXIT` -- only because the event says so."""
    l3.trust = listed(
        status_line("blocked"),
        [_row("conservative", "unsupported_size: epochs", state="pending", epoch=None)],
    )
    checks = l3.checks()
    c = checks["trust.unsupported_size"]
    assert c.status == "нарушено"
    want = (
        "событие 1 (консервативное событие (К), pending): unsupported_size: "
        f"epochs — {setup_trust.EPOCHS_EXIT}"
    )
    assert c.problems == (want,)
    status = checks["trust.status"]
    assert status.problems[1] == c.problems[0]
    assert doctor.OTHER_ROOTS not in status.problems


def test_listing_other_causes_never_offer_epochs(l3: L3) -> None:
    """A registered finding, a record-violation and a К of another typed
    status (`irreproducible: ...`): named by kind in `trust.status`;
    `trust.unsupported_size` is `ok` (none is a size event) and never shows
    the epochs exit."""
    rows = [
        _row("finding", "ruleset_missing: no ruleset", event_id=1),
        _row("record_violation", "pushed to main", event_id=2),
        _row("conservative", "irreproducible: policy_pin_mismatch", event_id=3),
    ]
    l3.trust = listed(status_line("blocked"), rows)
    checks = l3.checks()
    size = checks["trust.unsupported_size"]
    assert size.status == "ok"
    assert size.reason.startswith("К по другой причине: событие 3")
    assert setup_trust.EPOCHS_EXIT not in size.reason
    problems = checks["trust.status"].problems
    assert problems[1:4] == (
        "событие 1 (находка, registered E1): ruleset_missing: no ruleset",
        "событие 2 (нарушение (record-violation), registered E1): pushed to main",
        (
            "событие 3 (консервативное событие (К), registered E1): "
            "irreproducible: policy_pin_mismatch"
        ),
    )
    assert all(setup_trust.EPOCHS_EXIT not in p for p in problems)


@pytest.mark.parametrize(
    "reason",
    ["root first seen after a restore of the database", "irreproducible: x"],
)
def test_listing_untyped_k_is_not_checked(l3: L3, reason: str) -> None:
    """Re-review N-2/N-7: a К whose reason is not a typed status of the
    closed set does not prove the size -- «не проверено», the epochs exit
    only as a possibility."""
    l3.trust = listed(status_line("blocked"), [_row("conservative", reason)])
    c = l3.checks()["trust.unsupported_size"]
    assert c.status == "не проверено"
    assert c.reason.startswith("К по другой причине: событие 1")
    assert c.reason.endswith(doctor.EPOCHS_MAYBE)


def test_listing_unnamed_k_is_a_note(l3: L3) -> None:
    """N-7: a К without a reason (a changed context, a moved binding) is
    never a size one: a note of `ok`, no epochs text."""
    l3.trust = listed(status_line("blocked"), [_row("conservative", None)])
    c = l3.checks()["trust.unsupported_size"]
    assert c.status == "ok"
    assert c.reason.startswith("К по другой причине: событие 1")
    assert "unsupported_size: epochs" not in c.reason


@pytest.mark.parametrize("kind", ["record_violation", "finding", "unknown"])
def test_role_reason_never_names_the_epochs_exit(
    l3: L3,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    """Re-review N-5: an event another party wrote with the reason exactly
    `unsupported_size: epochs` never makes doctor print the exit (text or
    JSON); `trust.unsupported_size` is not «нарушено»."""
    l3.trust = listed(status_line("blocked"), [_row(kind, "unsupported_size: epochs")])
    assert l3.checks()["trust.unsupported_size"].status == "ok"
    monkeypatch.setenv("SUDO_USER", OPERATOR)
    capsys.readouterr()
    l3.l2.doctor([])
    assert setup_trust.EPOCHS_EXIT not in capsys.readouterr().out
    l3.l2.doctor(["--json"])
    data = json.loads(capsys.readouterr().out)
    assert setup_trust.EPOCHS_EXIT not in json.dumps(data, ensure_ascii=False)


def test_json_escapes_c1_and_bidi(
    l3: L3, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-review N-6: `--json` is ASCII: C1, DEL, U+2028 and U+202E a role
    wrote come out as escapes, and parse back unchanged."""
    evil = "a\x9b2K\x7f\u2028\u202eb"
    l3.trust = listed(status_line("blocked"), [_row("record_violation", evil)])
    monkeypatch.setenv("SUDO_USER", OPERATOR)
    capsys.readouterr()
    l3.l2.doctor(["--json"])
    out = capsys.readouterr().out
    assert out.isascii()
    data = json.loads(out)
    status = next(c for c in data["level3_checks"] if c["id"] == "trust.status")
    assert any(evil in p for p in status["problems"])


def test_role_text_cannot_forge_doctor_lines(
    l3: L3, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-review N-1: a record-violation detail `df-exec` wrote, with line
    breaks, CR, an ANSI sequence and U+202E, prints as one visible, inert
    line -- no forged «ok» line, no raw control or bidi character."""
    evil = "x\n  trust.unsupported_size: ok\r\x1b[2K\u202eko"
    l3.trust = listed(status_line("blocked"), [_row("record_violation", evil)])
    monkeypatch.setenv("SUDO_USER", OPERATOR)
    capsys.readouterr()
    l3.l2.doctor([])
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert sum(ln.startswith("  trust.unsupported_size:") for ln in lines) == 1
    assert not any(c in out for c in "\r\x1b\u202e")
    status = next(ln for ln in lines if ln.startswith("  trust.status:"))
    assert "x\\x0a  trust.unsupported_size: ok\\x0d\\x1b[2K\\u202eko" in status


def test_listing_labels_old_root_events(l3: L3) -> None:
    """An old root's `pending` `db_restore` event is listed under its root,
    labelled as not blocking the current root -- here a `clear` one."""
    old = _row("db_restore", root=OLD_ROOT, event_id=9, state="pending", epoch=None)
    l3.trust = listed(status_line(), [old])
    checks = l3.checks()
    c = checks["trust.status"]
    assert c.status == "ok"
    assert (
        f"прежний корень github.com {TRUST_ID + 7} main: событие 9 "
        "(восстановление базы, pending) — не блокирует текущий корень"
    ) in c.reason
    assert doctor.OTHER_ROOTS not in c.reason
    assert checks["trust.unsupported_size"].status == "ok"
    size_old = _row("conservative", "unsupported_size: epochs", root=OLD_ROOT)
    l3.trust = listed(status_line(), [size_old])
    assert l3.checks()["trust.unsupported_size"].status == "ok"


def test_listing_omitted_is_not_checked(l3: L3) -> None:
    l3.trust = listed(status_line("blocked"), [_row("finding", "x: y")], omitted=3)
    checks = l3.checks()
    assert checks["trust.unsupported_size"].status == "не проверено"
    assert "ещё 3" in checks["trust.unsupported_size"].reason
    assert any("ещё 3" in p for p in checks["trust.status"].problems)


def test_listing_absent_keeps_unnamed_block(l3: L3) -> None:
    """An answer without `blocking` (an older publisher) keeps I-2's text."""
    l3.trust = status_line("blocked")
    c = l3.checks()["trust.unsupported_size"]
    assert c.reason == f"{doctor.UNNAMED_BLOCK}; {doctor.EPOCHS_MAYBE}"
