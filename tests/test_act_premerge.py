"""Предпроверка агентской доставки candidate review/v1 (§8.2; Task 15)."""

from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

pytest.importorskip("steward")

from steward.approvalfacts import v3  # noqa: E402

from governance import act_decision, act_facts, act_premerge, act_trust  # noqa: E402
from governance.act_check import ActivationView  # noqa: E402
from governance.act_decision import ReviewView  # noqa: E402
from governance.act_epochs import Epoch  # noqa: E402
from governance.act_policy import parse_policy_set  # noqa: E402
from governance.act_request import (  # noqa: E402
    NodeEntry,
    NodeRequest,
    PolicyPin,
    render_manifest,
)
from governance.act_source import SourceV2  # noqa: E402
from governance.act_time import Unavailable, parse_ts  # noqa: E402
from governance.facts import Fact, Outcome  # noqa: E402
from tests.act_cases import FILES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SLUG = "owner/p"
HEAD = "1" * 40
ACT = "a" * 40
BRANCH = "spec/ws-7-approve-2-1-1"
NOW = parse_ts("2026-10-12T12:00:00Z")
MODES = f"schema: approval-policy-modes/v1\nmodes:\n  {SLUG}: review\n".encode()
POLICY = parse_policy_set(ACT, {**FILES, "policy/modes.yaml": MODES})
SNAP = act_trust.TrustSnapshot(
    act_trust.TrustRoot("github.com", 11, "main", "s" * 40),
    "owner/df-trust",
    SourceV2("owner/approval-policy", "main", ACT, frozenset({"owner"})),
    (Epoch(1, ACT),),
)
BINDING = act_trust.Binding("github.com", 11, "owner/df-trust", "main", 22, "public")
REAL_LOAD_TRUST = act_trust.load_trust
REAL_EFFECTIVE = act_facts.effective
CLEAN = {"admin": False, "maintain": False, "push": False}
TOKEN_CRED = act_trust.Credential("env:GH_TOKEN", token="ghp_x")


def manifest(
    pin: str = ACT,
    request_id: str = "approve-2-1-1",
    ws_id: str = "ws-7",
    product: str = SLUG,
) -> str:
    return render_manifest(
        NodeRequest(
            request_id,
            ws_id,
            product,
            {"requirements": NodeEntry("spec/10-r.md", "h", 1)},
            {"requirements": {}},
            PolicyPin("owner/approval-policy", "main", pin, dict(POLICY.files_sha256)),
        )
    )


class Journal:
    """Журнал периметра: регистрация блокирует следующие решения (§6.3)."""

    def __init__(self, state: str = "clear") -> None:
        self.state, self.recorded = state, []

    def status(self, snap):
        return self.state

    def record(self, snap, source, detail):
        self.recorded.append((source, detail))
        self.state = "blocked"
        return None


class World:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.unverified: tuple[str, ...] = ()
        self.trust: object = None
        self.activation_at = "2026-10-12T08:00:00Z"
        self.activation = ACT
        self.approve_at = "2026-10-12T11:59:00Z"
        self.manifest: object = manifest()
        self.reads: list[object] = []
        self.slept: list[float] = []
        self.clock = [NOW]
        self.journal: object = Journal()
        self.installations = None
        self.creds: tuple[act_trust.Credential, ...] = ()
        self.perms: dict[str, dict] = {}
        self.sets: dict[str, object] = {ACT: POLICY}
        self.monkeypatch = monkeypatch
        monkeypatch.setattr(
            act_trust,
            "load_trust",
            lambda *a, **k: self.trust or act_trust.Trust(SNAP, self.unverified),
        )
        monkeypatch.setattr(
            act_trust,
            "load_anchor_times",
            lambda *a: {ACT: parse_ts(self.activation_at)},
        )
        monkeypatch.setattr(act_trust, "load_ranges", lambda *a: self.ranges())
        monkeypatch.setattr(act_premerge, "_manifest_text", lambda *a: self.manifest)
        monkeypatch.setattr(act_facts, "open_review", lambda *a: self.next_facts())
        monkeypatch.setattr(
            act_facts,
            "effective",
            lambda facts, clock: {
                r.login: ReviewView(
                    r.review_id, r.login, r.type_hint, r.state, r.commit_id
                )
                for r in facts.reviews
            },
        )

    def ranges(self):
        view = ActivationView(
            ("activation", self.activation),
            self.activation,
            parse_ts(self.activation_at),
        )
        return {1: (view,)}, dict(self.sets, **{self.activation: POLICY})

    def real_perimeter(self) -> None:
        """Настоящий `load_trust`: снимок подменён, журнал и самоотчёт — свои."""
        self.monkeypatch.setattr(act_trust, "load_trust", REAL_LOAD_TRUST)
        self.monkeypatch.setattr(act_trust, "read_trust_snapshot", lambda anon, b: SNAP)
        self.monkeypatch.setattr(act_trust, "_rulesets", lambda anon, snap: None)
        self.monkeypatch.setattr(act_trust, "_rebase", lambda anon, snap: None)

    def as_credential(self, cred, path):
        perms = self.perms.get(cred.label, CLEAN)
        return Fact(Outcome.FOUND, {"permissions": perms}, "ok")

    def next_facts(self):
        if self.reads:
            return self.reads.pop(0)
        review = v3.Review("11", "alice", "User", "APPROVED", HEAD, self.approve_at)
        return v3.ReviewFacts(SLUG, 7, HEAD, "master", "OPEN", (review,), ())

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.clock[0] = self.clock[0] + timedelta(seconds=seconds)

    def deps(self, **over) -> act_premerge.PremergeDeps:
        deps = act_premerge.PremergeDeps(
            readers=act_trust.Readers(self.as_credential),
            gh=object(),
            binding=BINDING,
            journal=self.journal,
            now=lambda: self.clock[0],
            sleep=self.sleep,
            creds=self.creds,
            installations=self.installations,
        )
        return replace(deps, **over)

    def run(self, *extra: str, branch: str = BRANCH, **over) -> int:
        argv = [
            "--repo", SLUG, "--pr", "7", "--head", HEAD, "--head-ref", branch,
            "--merge-profile", "/x/review", "--merger", "df-reviewer[bot]",
            "--merger-type", "Bot", *extra,
        ]  # fmt: skip
        return act_premerge.main(argv, deps=self.deps(**over))


@pytest.fixture()
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    return World(monkeypatch)


def test_clean_premerge_passes(world: World) -> None:
    assert world.run() == 0 and world.slept == []


def test_recent_approve_waits_past_second(world: World) -> None:
    """Approve 1 с назад → сон до max + 2 с."""
    world.approve_at = "2026-10-12T11:59:59Z"
    assert world.run() == 0
    assert world.slept == [1.0]


def test_recent_activation_waits(world: World) -> None:
    world.activation_at = "2026-10-12T11:59:59Z"
    assert world.run() == 0 and world.slept == [1.0]


def test_recent_dismissal_waits(world: World) -> None:
    """Отзыв чужого ревью — тоже событие решения: граница от его `created_at`."""
    first = world.next_facts()
    other = v3.Review("12", "bob", "User", "DISMISSED", HEAD, "2026-10-12T11:00:00Z")
    facts = replace(
        first,
        reviews=(*first.reviews, other),
        dismissals=(v3.Dismissal("12", "carol", "2026-10-12T11:59:59Z", "APPROVED"),),
    )
    world.reads = [facts, facts]
    assert world.run() == 0 and world.slept == [1.0]


def test_boundary_exactly_two_seconds_ago_does_not_sleep(world: World) -> None:
    world.approve_at = "2026-10-12T11:59:58Z"
    assert world.run() == 0 and world.slept == []


def test_boundary_unparsed_stamp_is_unset() -> None:
    """Task 16 (`_boundary`): метка события не разобрана — граница не вычислена,
    «не установлено» (2), а не отказ и не граница без этого события."""
    from types import SimpleNamespace

    facts = SimpleNamespace(
        reviews=[SimpleNamespace(submitted_at="не-метка")], dismissals=[]
    )
    with pytest.raises(act_premerge._Stop, match="метка события не разобрана") as got:
        act_premerge._boundary(facts, [])
    assert got.value.code == act_premerge.EXIT_UNSET


def test_wait_of_exactly_ten_seconds_is_allowed(world: World) -> None:
    world.approve_at = "2026-10-12T12:00:08Z"
    assert world.run() == 0 and world.slept == [10.0]


def test_clock_skew_beyond_ten_seconds_unset(world: World, capsys) -> None:
    world.approve_at = "2026-10-12T12:00:09Z"
    assert world.run() == 2 and "часы расходятся" in capsys.readouterr().err
    assert world.slept == []


def test_unparsable_stamp_is_unset(world: World, capsys) -> None:
    world.approve_at = "2026-10-12 12:00"
    assert world.run() == 2 and "метка события" in capsys.readouterr().err


def test_stale_pin_refused(world: World, capsys) -> None:
    world.manifest = manifest(pin="b" * 40)
    assert world.run() == 3 and "policy_changed" in capsys.readouterr().err


@pytest.mark.parametrize("field", ["request_id", "ws_id", "product"])
def test_manifest_of_other_identity_refused(world: World, capsys, field: str) -> None:
    """Идентичность (product, ws_id, request_id) — по ветке и `--repo`, не по манифесту."""
    other = {"request_id": "approve-2-1-2", "ws_id": "ws-8", "product": "owner/other"}
    world.manifest = manifest(**{field: other[field]})
    assert world.run() == 3 and f"манифест: {field}" in capsys.readouterr().err


def test_malformed_manifest_refused(world: World, capsys) -> None:
    world.manifest = "schema: something-else\n"
    assert world.run() == 3 and "манифест: " in capsys.readouterr().err


def test_manifest_unreadable_is_unset(world: World, capsys) -> None:
    world.manifest = Unavailable("HTTP 502")
    assert world.run() == 2 and "манифест: HTTP 502" in capsys.readouterr().err


def test_review_facts_head_differs_refused(world: World, capsys) -> None:
    """B2-3/B3-8: ревью-факты PR названы на другой голове, чем `--head` мержа; approve
    человека в фактах есть на голове `--head` — отказ только проверкой головы."""
    review = v3.Review("11", "alice", "User", "APPROVED", HEAD, world.approve_at)
    world.reads = [v3.ReviewFacts(SLUG, 7, "2" * 40, "master", "OPEN", (review,), ())]
    assert world.run() == 3 and "≠ --head" in capsys.readouterr().err


def test_review_facts_unreadable_is_unset(world: World, capsys) -> None:
    world.reads = [Unavailable("GraphQL: 502")]
    assert world.run() == 2 and "ревью PR #7: GraphQL" in capsys.readouterr().err


def test_effective_unavailable_is_unset(world: World, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        act_facts, "effective", lambda *a: Unavailable("previous_state")
    )
    assert world.run() == 2 and "previous_state" in capsys.readouterr().err


def test_no_approve_refused(world: World, capsys) -> None:
    world.reads = [v3.ReviewFacts(SLUG, 7, HEAD, "master", "OPEN", (), ())]
    assert world.run() == 3 and "нет approve человека" in capsys.readouterr().err


def test_reread_sees_dismissal_unset(world: World, capsys) -> None:
    world.approve_at = "2026-10-12T11:59:59Z"
    first = world.next_facts()
    dismissed = replace(
        first,
        reviews=(replace(first.reviews[0], state="DISMISSED"),),
        dismissals=(v3.Dismissal("11", "carol", "2026-10-12T12:00:01Z", "APPROVED"),),
    )
    world.reads = [first, dismissed]
    assert (
        world.run() == 2 and "изменились за время ожидания" in capsys.readouterr().err
    )


def test_reread_sees_new_activation_unset(world: World, monkeypatch, capsys) -> None:
    """Повторное чтение текущей активации: новая активация за ожидание — 2."""
    calls: list[int] = []

    def ranges():
        calls.append(1)
        if len(calls) > 1:
            world.activation = "c" * 40
        return World.ranges(world)

    monkeypatch.setattr(act_trust, "load_ranges", lambda *a: ranges())
    assert (
        world.run() == 2 and "изменились за время ожидания" in capsys.readouterr().err
    )


def test_merger_outside_allowlist_refused(world: World, capsys) -> None:
    assert world.run("--merger", "stranger", "--merger-type", "User") == 3
    assert "не пройдёт D3" in capsys.readouterr().err


def test_merger_allowed_is_decide_predicate() -> None:
    """D3 заранее — тот же предикат, что `decide`: агент из mergers, человек из
    approvers; агент вне mergers и неизвестный — нет."""
    assert act_decision.merger_allowed(POLICY, "df-reviewer[bot]", "Bot")
    assert act_decision.merger_allowed(POLICY, "alice", "User")
    assert not act_decision.merger_allowed(POLICY, "ai-prosto", "User")
    assert not act_decision.merger_allowed(POLICY, "stranger", "User")


def test_no_manifest_refused(world: World, capsys) -> None:
    world.manifest = None
    assert world.run() == 3 and "нет манифеста" in capsys.readouterr().err


@pytest.mark.parametrize(
    "mode,code",
    [
        (act_trust.PRE_MIGRATION, 3),
        ("merge", 3),
        (Unavailable("ACT_MODE: обратное переключение review → merge"), 2),
    ],
    ids=["pre_migration", "merge", "unset"],
)
def test_product_mode_gate(world: World, monkeypatch, capsys, mode, code: int) -> None:
    """B1-5: режим — по истории; неустановленный (удаление, откат) — 2, не отказ-legacy."""
    monkeypatch.setattr(act_trust, "product_mode", lambda *a: mode)
    assert world.run() == code
    reason = "режим продукта: " if code == 2 else "— не review"
    assert reason in capsys.readouterr().err


def test_policy_history_unavailable_is_unset(world: World, monkeypatch, capsys) -> None:
    monkeypatch.setattr(act_trust, "load_ranges", lambda *a: Unavailable("history"))
    assert world.run() == 2 and "история источника политики" in capsys.readouterr().err


def test_current_activation_unavailable_is_unset(world, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        act_trust, "current_activation", lambda *a: Unavailable("пусто")
    )
    assert world.run() == 2 and "текущая активация: пусто" in capsys.readouterr().err


def test_current_policy_set_unavailable_is_unset(world, monkeypatch, capsys) -> None:
    world.sets = {ACT: Unavailable("смешение версий")}
    monkeypatch.setattr(
        act_trust, "load_ranges", lambda *a: (World.ranges(world)[0], world.sets)
    )
    assert world.run() == 2 and "смешение версий" in capsys.readouterr().err


def test_existing_block_stops_premerge(world: World, capsys) -> None:
    """B1-4: блок, зарегистрированный другим потребителем, останавливает мерж."""
    world.real_perimeter()
    world.creds = (TOKEN_CRED,)
    world.journal = Journal("blocked")
    assert world.run() == 2
    assert "заблокирован" in capsys.readouterr().err


def test_detected_violation_registered_and_blocks_restart(world: World, capsys) -> None:
    """B1-4: нарушение, найденное предпроверкой, записано; повторный запуск видит блок."""
    world.real_perimeter()
    world.creds = (TOKEN_CRED,)
    world.perms = {"env:GH_TOKEN": {"admin": True, "maintain": True, "push": True}}
    assert world.run() == 2
    assert world.journal.recorded and world.journal.recorded[0][0] == "approval-policy"
    capsys.readouterr()
    world.perms = {}
    assert world.run() == 2
    assert "заблокирован" in capsys.readouterr().err


def test_real_perimeter_clean_passes(world: World) -> None:
    """Двойник двух тестов выше: тот же настоящий `load_trust` без нарушения — 0."""
    world.real_perimeter()
    world.creds = (TOKEN_CRED,)
    assert world.run() == 0 and world.journal.recorded == []


def test_journal_unavailable_is_unset(world: World, capsys) -> None:
    world.journal = Unavailable("журнал периметра (dev): integrity")
    assert world.run() == 2 and "журнал периметра: " in capsys.readouterr().err


def test_binding_unreadable_is_unset(world: World, capsys) -> None:
    assert world.run(binding=Unavailable("привязка: не файл root")) == 2
    assert "не файл root" in capsys.readouterr().err


def test_app_installation_violation_recorded(world: World, capsys) -> None:
    """B1-11: установка App на репо доверия — у держателя ключа; событие журнала, 2."""
    world.installations = lambda binding: ("df-trust", "установка App 9 видит df-trust")
    assert world.run() == 2
    assert world.journal.recorded == [("df-trust", "установка App 9 видит df-trust")]
    assert act_premerge.REGISTERED in capsys.readouterr().err


def test_app_installation_registration_failure_named(world: World, capsys) -> None:
    class Failing(Journal):
        def record(self, snap, source, detail):
            return Unavailable("журнал занят")

    world.journal = Failing()
    world.installations = lambda binding: ("df-trust", "установка видит df-trust")
    assert world.run() == 2
    err = capsys.readouterr().err
    assert "регистрация: журнал занят" in err and act_premerge.REGISTERED not in err


def test_app_installations_unknown_is_unset(world: World, capsys) -> None:
    world.installations = lambda binding: Unavailable("GET /app/installations: 502")
    assert world.run() == 2 and world.journal.recorded == []
    assert "установки App: " in capsys.readouterr().err


def test_app_installations_clean_passes(world: World) -> None:
    world.installations = lambda binding: None
    assert world.run() == 0 and world.journal.recorded == []


def test_no_binding_refused(world: World, capsys) -> None:
    assert world.run(binding=None, journal=Unavailable("нет привязки")) == 3
    assert "без корня доверия" in capsys.readouterr().err


def test_unverified_premise_printed_but_not_blocking(world, capsys) -> None:
    world.unverified = ("allow_rebase_merge",)
    assert world.run() == 0
    assert "unverified premise: allow_rebase_merge" in capsys.readouterr().err


def test_non_candidate_branch_refused(world: World, capsys) -> None:
    """Глоб обвязки (`spec/*-approve-*`) шире формы: остаток — отказ здесь."""
    assert world.run(branch="spec/ws-7-approve-x") == 3
    assert "не форма candidate" in capsys.readouterr().err


def test_broken_branch_ssot_is_unset(world: World, monkeypatch, tmp_path, capsys):
    broken = tmp_path / "patterns.env"
    broken.write_text("APPROVAL_FINALIZE_SUFFIX=-final\n")
    monkeypatch.setattr(act_premerge, "PATTERNS", broken)
    assert world.run() == 2 and "APPROVAL_CANDIDATE_TEMPLATE" in capsys.readouterr().err


@pytest.mark.parametrize(
    "branch,expected",
    [
        ("spec/ws-7-approve-2-1-1", ("ws-7", "approve-2-1-1")),
        ("spec/a-b-approve-10-2-33", ("a-b", "approve-10-2-33")),
        ("spec/ws-7-approve-2-1-1-final", None),
        ("spec/ws-7-approve-2-1", None),
        ("xspec/ws-7-approve-2-1-1", None),
        ("feat/x", None),
    ],
)
def test_request_of_branch(branch: str, expected) -> None:
    assert act_premerge.request_of_branch(branch) == expected


def test_premerge_chain_has_no_df_and_no_projection() -> None:
    """Р-16 и п. 8 заметок части A: цепочка обвязки — без df/ и без node_approval,
    но с узким журналом периметра."""
    from tests.harness_trust import closure

    files, problems, _third = closure(ROOT, "governance/act_premerge.py")
    assert problems == []
    assert "governance/act_journal_wire.py" in files
    assert not any(f.startswith("df/") for f in files)
    assert (
        not {
            "governance/node_approval.py",
            "governance/stale_adapter.py",
            "governance/ops.py",
            "governance/act_journal.py",
        }
        & files
    )


@pytest.mark.slow
def test_premerge_runs_as_isolated_file(tmp_path: Path) -> None:
    """Боевая форма запуска настоящим `uv` (офлайн, из кэша): `--help` разбирается
    после импорта всей цепочки, steward есть в окружении группы governance."""
    env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ["HOME"],
        "UV_PROJECT_ENVIRONMENT": str(tmp_path / "venv"),
        "UV_OFFLINE": "1",
    }
    form = [
        "uv", "run", "--frozen", "--exact", "--group", "governance", "--no-config",
        "--no-env-file", "python", "-I",
    ]  # fmt: skip
    done = subprocess.run(
        [*form, str(ROOT / "governance/act_premerge.py"), "--help"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False, timeout=600,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert "usage: act_premerge" in done.stdout
    probe = subprocess.run(
        [*form, "-c", "import steward.approvalfacts.v3"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False, timeout=600,
    )  # fmt: skip
    assert probe.returncode == 0, probe.stderr


@pytest.mark.parametrize(
    "raw,expected",
    [
        (b"schema: x\n", "schema: x\n"),
        (None, None),
        (Unavailable("HTTP 502"), Unavailable("HTTP 502")),
        (b"\xff\xfe", Unavailable("m.yaml: не UTF-8")),
    ],
    ids=["bytes", "absent", "unavailable", "not_utf8"],
)
def test_manifest_text_reads_head_bytes(monkeypatch, raw, expected) -> None:
    seen: list[tuple] = []

    def contents(gh, repo, path, ref):
        seen.append((gh, repo, path, ref))
        return raw

    monkeypatch.setattr(act_premerge, "contents_bytes", contents)
    gh = object()
    got = act_premerge._manifest_text(
        act_premerge.PremergeDeps(
            readers=act_trust.Readers(lambda c, p: Fact(Outcome.FOUND, {}, "ok")),
            gh=gh, binding=BINDING, journal=Journal(), now=lambda: NOW,
            sleep=lambda s: None, creds=(),
        ),
        SLUG, HEAD, "m.yaml",
    )  # fmt: skip
    assert got == expected and seen == [(gh, SLUG, "m.yaml", HEAD)]


def test_real_deps_without_binding_refuses(capsys) -> None:
    """Боевая сборка без привязки (conftest: файла нет) — отказ 3 до любой сети."""
    argv = [
        "--repo", SLUG, "--pr", "7", "--head", HEAD, "--head-ref", BRANCH,
        "--merge-profile", "/x/merge", "--merger", "m", "--merger-type", "User",
    ]  # fmt: skip
    assert act_premerge.main(argv) == 3
    assert "без корня доверия" in capsys.readouterr().err


def test_merger_type_reaches_d3(world: World, capsys) -> None:
    """`--merger-type` — часть D3: человек из approvers с типом Bot — не человек."""
    assert world.run("--merger", "alice", "--merger-type", "User") == 0
    assert world.run("--merger", "alice", "--merger-type", "Bot") == 3
    assert "не пройдёт D3" in capsys.readouterr().err


def test_real_deps_holds_agent_and_merge_profiles(tmp_path: Path) -> None:
    """Самоотчёт — по тому, что процесс держит: профиль агента из окружения,
    профиль мержа из `--merge-profile`, токены окружения."""
    import argparse

    args = argparse.Namespace(
        merge_profile="/x/merge", repo=SLUG, pr=7, app_id=None, app_key=None
    )
    env = {
        "REVIEW_GH_CONFIG_DIR": "/x/review",
        "GH_TOKEN": "ghp_t",
        "XDG_STATE_HOME": str(tmp_path),
    }
    deps = act_premerge.real_deps(args, env=env)
    assert [(c.label, c.gh_config_dir, c.token) for c in deps.creds] == [
        ("profile:agent", Path("/x/review"), None),
        ("profile:merge", Path("/x/merge"), None),
        ("env:GH_TOKEN", None, "ghp_t"),
    ]
    assert deps.binding is None and isinstance(deps.journal, Unavailable)
    assert deps.installations is None


def _real_clock(world: World, monkeypatch, *reviews) -> None:
    """Настоящие `Clock`/`effective` (steward): равная секунда — по `before`."""
    monkeypatch.setattr(act_facts, "effective", REAL_EFFECTIVE)
    facts = v3.ReviewFacts(SLUG, 7, HEAD, "master", "OPEN", tuple(reviews), ())
    world.reads = [facts, facts]


def _approve(at: str, *, state: str = "APPROVED", commit: str = HEAD) -> v3.Review:
    return v3.Review("11", "alice", "User", state, commit, at)


def test_approve_in_current_second_is_unset(world: World, monkeypatch, capsys):
    """Ревью PR #673: approve той же секунды, что T, срезу T ещё не виден —
    «не установлено» (2, повтор уместен), а не отказ 3."""
    _real_clock(world, monkeypatch, _approve("2026-10-12T12:00:00Z"))
    assert world.run() == 2
    assert "в текущей секунде" in capsys.readouterr().err


def test_approve_one_second_ago_is_the_act(world: World, monkeypatch) -> None:
    _real_clock(world, monkeypatch, _approve("2026-10-12T11:59:59Z"))
    assert world.run() == 0 and world.slept == [1.0]


@pytest.mark.parametrize(
    "reviews",
    [
        (),
        (_approve("2026-10-12T12:00:00Z", commit="2" * 40),),
        (_approve("2026-10-12T12:00:00Z", state="COMMENTED"),),
    ],
    ids=["no_approve", "approve_on_other_commit", "same_second_not_approve"],
)
def test_no_act_without_same_second_approve_refused(
    world, monkeypatch, capsys, reviews
):
    _real_clock(world, monkeypatch, *reviews)
    assert world.run() == 3
    assert "нет approve человека" in capsys.readouterr().err


@pytest.mark.parametrize(
    "extra",
    [("--app-id", "5"), ("--app-key", "/k.pem")],
    ids=["id_only", "key_only"],
)
def test_app_half_pair_is_usage_error(world: World, capsys, extra) -> None:
    """Task 15a: `--app-id`/`--app-key` — оба или ни одного; половина — отказ
    разбора (2) до любых чтений, не откат на режим профиля."""
    with pytest.raises(SystemExit) as exc:
        world.run(*extra)
    assert exc.value.code == 2
    assert "нужны оба --app-id и --app-key" in capsys.readouterr().err


def test_real_deps_app_mode_wires_installations(monkeypatch, tmp_path) -> None:
    """`--app-id`/`--app-key` → самоотчёт установок держателя ключа; ключ и id
    доходят до `installations_refusal` с id репо доверия из привязки."""
    import argparse

    from governance import act_app_token

    seen: list[tuple] = []
    monkeypatch.setattr(
        act_app_token,
        "installations_refusal",
        lambda app_id, key, repos, **kw: seen.append((app_id, key, repos, kw)),
    )
    args = argparse.Namespace(
        merge_profile="/x/merge", repo=SLUG, pr=7, app_id=5, app_key=Path("/k.pem")
    )
    deps = act_premerge.real_deps(args, env={"XDG_STATE_HOME": str(tmp_path)})
    assert deps.installations is not None
    assert deps.installations(BINDING) is None
    assert seen == [(5, Path("/k.pem"), {"approval-policy": 22, "df-trust": 11}, {})]
