"""Доверенный контекст (спека A rev 16 §6.1–6.5; приёмка №19, 28, 29, 33–36, 39, 45)."""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

import pytest

pytest.importorskip("steward")

from steward.approvalfacts import v3  # noqa: E402

from governance import act_facts, act_source, act_trust  # noqa: E402
from governance.act_epochs import Epoch  # noqa: E402
from governance.act_time import Unavailable, parse_ts  # noqa: E402
from governance.facts import Fact, Outcome, unavailable  # noqa: E402

DT = "owner/df-trust"
AP = "owner/approval-policy"
BOOT = "b" * 40
C0, C1, C2 = "0" * 40, "1" * 40, "2" * 40
SOURCE_ENV = (
    f"APPROVAL_POLICY_REPO={AP}\nAPPROVAL_POLICY_BRANCH=main\n"
    f"BOOTSTRAP_ACTIVATION={BOOT}\nPOLICY_ADMINS=owner\n"
)
J1 = f"schema: approval-policy-epochs/v1\nepochs:\n  - epoch: 1\n    anchor: {BOOT}\n"
J2 = (
    "schema: approval-policy-epochs/v1\nepochs:\n"
    f"  - epoch: 1\n    anchor: {BOOT}\n    last_confirmed: {BOOT}\n"
    "    valid_until: '2026-10-20T00:00:00Z'\n    closed_reason: x\n"
    f"  - epoch: 2\n    anchor: {'c' * 40}\n"
)
BINDING_TOML = (
    'schema = "df/trust/v1"\nhost = "github.com"\ndf_trust_repository_id = 11\n'
    f'df_trust_full_name = "{DT}"\ndf_trust_branch = "main"\n'
    'approval_policy_repository_id = 22\ntransport = "public"\n'
)


def _oid(kind: str, raw: bytes) -> str:
    return hashlib.sha1(f"{kind} {len(raw)}\0".encode() + raw).hexdigest()


def _file(text: str) -> dict:
    raw = text.encode()
    return {
        "type": "file",
        "encoding": "base64",
        "content": base64.b64encode(raw).decode(),
        "sha": _oid("blob", raw),
    }


def _commit_world(
    repo: str, commit: str, files: dict[str, str], **body: object
) -> dict:
    """Коммит стенда с полным деревом: `contents_bytes` (Task 11b) привязывает
    байты к blob-oid записи дерева и устанавливает отсутствие только по дереву."""
    routes: dict[str, tuple[int, object]] = {}

    def tree(prefix: str) -> str:
        entries, names = (
            [],
            sorted(
                {n[len(prefix) :].split("/")[0] for n in files if n.startswith(prefix)}
            ),
        )
        for name in names:
            full = prefix + name
            if full in files:
                entries.append(
                    {"path": name, "type": "blob", "sha": _file(files[full])["sha"]}
                )
                routes[f"repos/{repo}/contents/{full}?ref={commit}"] = (
                    200,
                    _file(files[full]),
                )
            else:
                entries.append({"path": name, "type": "tree", "sha": tree(full + "/")})
        oid = _oid("tree", f"{commit}:{prefix}:{entries}".encode())
        routes[f"repos/{repo}/git/trees/{oid}"] = (
            200,
            {"tree": entries, "truncated": False},
        )
        return oid

    routes[f"repos/{repo}/commits/{commit}"] = (
        200,
        {**body, "commit": {"tree": {"sha": tree("")}}},
    )
    return routes


class Anon:
    """Анонимный транспорт стенда: путь → (статус, тело); журнал путей."""

    def __init__(self, routes: dict[str, tuple[int, object]]) -> None:
        self.routes = routes
        self.sent: list[str] = []

    def send(self, request: v3.Request) -> v3.Response:
        self.sent.append(request.path)
        if request.path not in self.routes:
            return v3.Response(None, error="stand: unexpected")
        status, body = self.routes[request.path]
        return v3.Response(status, body=body)


def trust_routes(
    *journals: str | None,
    rules: list[str] | None = None,
    rebase: object = None,
    rebase_present: bool = True,
) -> dict:
    chain = [C0, C1, C2][: len(journals)]
    routes: dict[str, tuple[int, object]] = {
        "repositories/11": (200, {"full_name": DT, "private": False}),
        "repositories/22": (200, {"full_name": AP, "private": False}),
        f"repos/{DT}/branches/main": (200, {"commit": {"sha": chain[-1]}}),
    }
    for i, sha in enumerate(chain):
        parents = [{"sha": chain[i - 1]}] if i else []
        files = {"source.env": SOURCE_ENV}
        text = journals[i]
        if text is not None:
            files["epochs.yaml"] = text
        routes.update(_commit_world(DT, sha, files, sha=sha, parents=parents))
    kinds = (
        rules if rules is not None else ["deletion", "non_fast_forward", "pull_request"]
    )
    for repo in (DT, AP):
        routes[f"repos/{repo}/rules/branches/main"] = (
            200,
            [{"type": k} for k in kinds],
        )
    repo_body: dict = {"full_name": AP}
    if rebase_present:
        repo_body["allow_rebase_merge"] = rebase
    routes[f"repos/{AP}"] = (200, repo_body)
    return routes


def binding() -> act_trust.Binding:
    return act_trust.parse_binding(BINDING_TOML)


class Journal:
    def __init__(self, states: list[object] | None = None) -> None:
        self.states = states or ["clear"]
        self.records: list[tuple[str, str]] = []

    def status(self, snap: act_trust.TrustSnapshot) -> object:
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]

    def record(self, snap: act_trust.TrustSnapshot, source: str, detail: str) -> None:
        self.records.append((source, detail))


def _perms(push: object = False) -> dict:
    return {"permissions": {"push": push, "maintain": False, "admin": False}}


def as_credential(answers: dict[tuple[str, str], Fact[object]]):
    def read(cred: act_trust.Credential, path: str) -> Fact[object]:
        return answers.get((cred.label, path), Fact(Outcome.FOUND, _perms(), "ok"))

    return read


AGENT = act_trust.Credential("profile:agent", gh_config_dir=Path("/x/review"))


def load(routes: dict, journal: Journal | None = None, creds=(AGENT,), answers=None):
    readers = act_trust.Readers(Anon(routes), as_credential(answers or {}))
    return act_trust.load_trust(readers, binding(), journal or Journal(), creds=creds)


# --- привязка (Р-6; без Р-1 — ревизия b1) -----------------------------------


def test_binding_parse_agrees_with_installer() -> None:
    from df.stage2_config import parse_trust

    ours, theirs = binding(), parse_trust(BINDING_TOML)
    assert (
        ours.host,
        ours.df_trust_repository_id,
        ours.df_trust_full_name,
        ours.df_trust_branch,
        ours.approval_policy_repository_id,
    ) == (
        theirs.host,
        theirs.df_trust_repository_id,
        theirs.df_trust_full_name,
        theirs.df_trust_branch,
        theirs.approval_policy_repository_id,
    )


@pytest.mark.parametrize(
    ("text", "match"),
    [
        (BINDING_TOML.replace('"public"', '"private"'), "только transport"),
        (BINDING_TOML + "extra = 1\n", "ключи"),
        (
            BINDING_TOML.replace(
                "df_trust_repository_id = 11", "df_trust_repository_id = 0"
            ),
            "положительные целые",
        ),
    ],
    ids=["transport", "keys", "ids"],
)
def test_binding_rejects_bad_text(text: str, match: str) -> None:
    with pytest.raises(act_trust.TrustError, match=match):
        act_trust.parse_binding(text)


def test_binding_absent_is_none(tmp_path: Path) -> None:
    assert (
        act_trust.load_binding(tmp_path / "trust.toml", owner_uid=os.getuid()) is None
    )


def test_binding_symlink_or_foreign_owner_refused(tmp_path: Path) -> None:
    real = tmp_path / "real.toml"
    real.write_text(BINDING_TOML, encoding="utf-8")
    link = tmp_path / "trust.toml"
    link.symlink_to(real)
    assert isinstance(act_trust.load_binding(link, owner_uid=os.getuid()), Unavailable)
    foreign = act_trust.load_binding(real, owner_uid=os.getuid() + 1)
    assert isinstance(foreign, Unavailable) and "не файл root" in foreign.reason
    assert isinstance(
        act_trust.load_binding(real, owner_uid=os.getuid()), act_trust.Binding
    )


# --- снимок df-trust (§6.1 п. 1, §6.5, №45) --------------------------------


def test_snapshot_reads_both_files_from_one_sha() -> None:
    anon = Anon(trust_routes(None, J1, J2))
    snap = act_trust.read_trust_snapshot(anon, binding())
    assert isinstance(snap, act_trust.TrustSnapshot)
    assert snap.root == act_trust.TrustRoot("github.com", 11, "main", C2)
    assert [e.epoch for e in snap.epochs] == [1, 2]
    assert snap.source.bootstrap_activation == BOOT
    assert f"repos/{DT}/contents/source.env?ref={C2}" in anon.sent
    assert not any("default_branch" in p for p in anon.sent)


def test_rewritten_closed_epoch_in_history_unavailable() -> None:
    """Приёмка №45: любая мутация, кроме атомарного закрытия."""
    broken = J2.replace(f"anchor: {BOOT}\n    last", f"anchor: {'d' * 40}\n    last")
    snap = act_trust.read_trust_snapshot(Anon(trust_routes(J1, broken)), binding())
    assert (
        isinstance(snap, Unavailable) and "недопустимый переход журнала" in snap.reason
    )


def test_first_version_must_be_bootstrap() -> None:
    snap = act_trust.read_trust_snapshot(Anon(trust_routes(J2)), binding())
    assert isinstance(snap, Unavailable) and "не bootstrap" in snap.reason


def test_journal_deleted_after_appearing_unavailable() -> None:
    snap = act_trust.read_trust_snapshot(Anon(trust_routes(J1, None)), binding())
    assert isinstance(snap, Unavailable) and "epochs.yaml удалён" in snap.reason


@pytest.mark.parametrize(
    ("patch", "reason"),
    [
        (
            {
                "repositories/11": (
                    200,
                    {"full_name": "evil/df-trust", "private": False},
                )
            },
            "привязка —",
        ),
        (
            {"repositories/11": (200, {"full_name": DT, "private": True})},
            "только публичные",
        ),
        (
            {
                "repositories/22": (
                    200,
                    {"full_name": "evil/approval-policy", "private": False},
                )
            },
            "привязка —",
        ),
    ],
    ids=["df_trust_name", "private_root", "policy_name"],
)
def test_root_substitution_refused(patch: dict, reason: str) -> None:
    """§6.5: иной repository_id/имя, приватный корень, source.env на чужой источник."""
    routes = {**trust_routes(None, J1), **patch}
    got = act_trust.read_trust_snapshot(Anon(routes), binding())
    assert isinstance(got, Unavailable) and reason in got.reason


def test_branch_head_unreadable_is_unavailable() -> None:
    """b4: ответ форджи не 200 — `_get` отказывает с путём и кодом."""
    routes = {**trust_routes(None, J1), f"repos/{DT}/branches/main": (500, None)}
    got = act_trust.read_trust_snapshot(Anon(routes), binding())
    assert isinstance(got, Unavailable) and "branches/main: HTTP 500" in got.reason


def test_branch_head_not_named_is_unavailable() -> None:
    """b4: ответ о ветке без SHA головы — снимок не читается дальше."""
    routes = {
        **trust_routes(None, J1),
        f"repos/{DT}/branches/main": (200, {"commit": {}}),
    }
    got = act_trust.read_trust_snapshot(Anon(routes), binding())
    assert isinstance(got, Unavailable) and "голова main не прочитана" in got.reason


def test_commit_answer_for_other_sha_unavailable() -> None:
    """b4: ответ о другом коммите в цепочке first-parent — `_chain` отказывает."""
    routes = trust_routes(J1, J1)
    _, body = routes[f"repos/{DT}/commits/{C1}"]
    assert isinstance(body, dict)  # дерево коммита остаётся: source.env читается
    routes[f"repos/{DT}/commits/{C1}"] = (200, {**body, "sha": C0, "parents": []})
    got = act_trust.read_trust_snapshot(Anon(routes), binding())
    assert isinstance(got, Unavailable) and "ответ не о том коммите" in got.reason


def test_gh_get_failures_are_unavailable(monkeypatch) -> None:
    """b4: `gh api` упал / ответ не JSON — `Unavailable` с путём и credential."""

    class Done:
        def __init__(self, rc, out="", err=""):
            self.returncode, self.stdout, self.stderr = rc, out, err

    cred = act_trust.Credential("profile:agent", gh_config_dir=Path("/x/review"))
    monkeypatch.setattr(
        act_trust.subprocess, "run", lambda *a, **k: Done(1, err="boom")
    )
    failed = act_trust.gh_get(cred, "repos/o/p")
    assert (
        failed.outcome is Outcome.UNAVAILABLE
        and "(profile:agent): boom" in failed.detail
    )
    monkeypatch.setattr(act_trust.subprocess, "run", lambda *a, **k: Done(0, out="{"))
    garbled = act_trust.gh_get(cred, "repos/o/p")
    assert garbled.outcome is Outcome.UNAVAILABLE and "ответ не JSON" in garbled.detail


def test_unreadable_history_version_unavailable() -> None:
    """Версия есть в дереве звена, но байты не прочитаны (403) — не «отсутствие»."""
    routes = trust_routes(J1, J1)
    routes[f"repos/{DT}/contents/epochs.yaml?ref={C0}"] = (403, {"message": "rate"})
    got = act_trust.read_trust_snapshot(Anon(routes), binding())
    assert isinstance(got, Unavailable) and "HTTP 403" in got.reason


def test_history_version_absent_only_by_full_tree() -> None:
    """Task 11b: усечённое дерево звена — `Unavailable`, а не «журнала ещё нет»."""
    routes = trust_routes(None, J1)
    _, body = routes[f"repos/{DT}/commits/{C0}"]
    assert isinstance(body, dict)
    tree = body["commit"]["tree"]["sha"]
    routes[f"repos/{DT}/git/trees/{tree}"] = (200, {"tree": [], "truncated": True})
    got = act_trust.read_trust_snapshot(Anon(routes), binding())
    assert isinstance(got, Unavailable) and "прочитано не полностью" in got.reason


def test_load_source_not_called(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden() -> None:
        raise AssertionError("load_source в пути решения")

    monkeypatch.setattr(act_source, "load_source", forbidden)
    assert isinstance(load(trust_routes(None, J1)), act_trust.Trust)


# --- периметр (§6.2 б, §6.3) ------------------------------------------------


def test_clean_trust_carries_root_and_premise() -> None:
    trust = load(trust_routes(None, J1, rebase=None), Journal())
    assert isinstance(trust, act_trust.Trust)
    assert trust.root.snapshot_sha == C1
    assert trust.unverified == ("allow_rebase_merge",)


def test_blocked_journal_blocks() -> None:
    """Приёмка №36: событие периметра сохраняет блокировку."""
    got = load(trust_routes(None, J1), Journal(["blocked"]))
    assert isinstance(got, Unavailable) and "периметр заблокирован" in got.reason


def test_stale_snapshot_reloads() -> None:
    journal = Journal([act_trust.STALE, "clear"])
    got = load(trust_routes(None, J1), journal)
    assert isinstance(got, act_trust.Trust)
    assert journal.states == ["clear"]  # STALE израсходован: снимок перечитан


def test_token_with_push_records_and_blocks() -> None:
    """Приёмка №29, №34 (обнаруживший потребитель)."""
    journal = Journal()
    tok = act_trust.Credential("env:GH_TOKEN", token="t")
    answers = {("env:GH_TOKEN", f"repos/{AP}"): Fact(Outcome.FOUND, _perms(True), "ok")}
    got = load(trust_routes(None, J1), journal, creds=(AGENT, tok), answers=answers)
    assert isinstance(got, Unavailable) and got.reason.startswith(
        "нарушение периметра: "
    )
    assert journal.records[0][0] == "approval-policy"
    assert journal.records[0][1].startswith("env:GH_TOKEN на ")


def test_merge_profile_with_push_on_df_trust() -> None:
    """P5-2: профиль review чист, профиль мержа с push на df-trust."""
    journal = Journal()
    merge = act_trust.Credential("profile:merge", gh_config_dir=Path("/x/merge"))
    answers = {
        ("profile:merge", f"repos/{DT}"): Fact(Outcome.FOUND, _perms(True), "ok")
    }
    got = load(trust_routes(None, J1), journal, creds=(AGENT, merge), answers=answers)
    assert isinstance(got, Unavailable) and journal.records[0][0] == "df-trust"
    assert journal.records[0][1].startswith("profile:merge на ")


def test_violation_wins_over_other_unavailable() -> None:
    journal = Journal()
    tok = act_trust.Credential("env:GH_TOKEN", token="t")
    answers = {
        ("profile:agent", f"repos/{AP}"): unavailable("404"),
        ("env:GH_TOKEN", f"repos/{DT}"): Fact(Outcome.FOUND, _perms(True), "ok"),
    }
    load(trust_routes(None, J1), journal, creds=(AGENT, tok), answers=answers)
    assert journal.records and journal.records[0][0] == "df-trust"


def test_self_report_404_is_unavailable_without_record() -> None:
    journal = Journal()
    answers = {("profile:agent", f"repos/{DT}"): unavailable("HTTP 404")}
    got = load(trust_routes(None, J1), journal, answers=answers)
    assert isinstance(got, Unavailable) and journal.records == []
    assert got.reason.startswith("самоотчёт profile:agent")


def test_ruleset_without_pull_request_is_violation() -> None:
    journal = Journal()
    got = load(trust_routes(None, J1, rules=["deletion", "non_fast_forward"]), journal)
    assert (
        isinstance(got, Unavailable)
        and journal.records
        and "нет правил" in journal.records[0][1]
    )


def test_missing_bypass_field_not_read() -> None:
    """Приёмка №33: рантайм не делает вывода «bypass нет» — поле не запрашивается."""
    anon = Anon(trust_routes(None, J1))
    act_trust.load_trust(
        act_trust.Readers(anon, as_credential({})), binding(), Journal(), creds=(AGENT,)
    )
    assert not any("rulesets" in p or "bypass" in p for p in anon.sent)


def test_issue_signal_not_part_of_protocol() -> None:
    """Приёмка №39: issues не читаются вовсе."""
    anon = Anon(trust_routes(None, J1))
    act_trust.load_trust(
        act_trust.Readers(anon, as_credential({})), binding(), Journal(), creds=(AGENT,)
    )
    assert not any("/issues" in p for p in anon.sent)


def test_rebase_false_is_verified() -> None:
    trust = load(trust_routes(None, J1, rebase=False), Journal())
    assert trust.unverified == ()


def test_rebase_true_is_violation() -> None:
    journal = Journal()
    assert isinstance(load(trust_routes(None, J1, rebase=True), journal), Unavailable)
    assert journal.records and journal.records[0][1].endswith(
        "allow_rebase_merge = true"
    )


@pytest.mark.parametrize("present", [True, False])
def test_rebase_hidden_is_unverified_premise(present: bool) -> None:
    trust = load(trust_routes(None, J1, rebase=None, rebase_present=present), Journal())
    assert isinstance(trust, act_trust.Trust)  # тип — до обращения к полям (B3-11)
    assert trust.unverified == ("allow_rebase_merge",)


@pytest.mark.parametrize(
    "answer", [(500, {"m": "x"}), (200, []), (200, {"allow_rebase_merge": "false"})]
)
def test_rebase_unreadable_is_unavailable(answer: tuple) -> None:
    journal = Journal()
    routes = trust_routes(None, J1)
    routes[f"repos/{AP}"] = answer
    assert isinstance(load(routes, journal), Unavailable) and journal.records == []


def test_credentials_deduplicate_and_list_what_process_holds(tmp_path: Path) -> None:
    review = tmp_path / "review"
    got = act_trust.credentials(
        {"GH_TOKEN": "a", "GITHUB_TOKEN": "a"}, agent_dir=review, merge_dir=review
    )
    assert [c.label for c in got] == ["profile:agent", "env:GH_TOKEN"]


# --- якоря и диапазоны (№19, №28, e7, §6; без Р-4 — ревизия b1) ------------


def _trust(
    epochs: tuple[Epoch, ...],
    source_repo: str = AP,
    branch: str = "main",
    anchor: str = BOOT,
) -> act_trust.Trust:
    snap = act_trust.TrustSnapshot(
        act_trust.TrustRoot("github.com", 11, "main", C1),
        DT,
        act_source.SourceV2(source_repo, branch, anchor, frozenset({"owner"})),
        epochs,
    )
    return act_trust.Trust(snap, ())


def _activation(sha: str, at: str) -> act_facts.ActivationFact:
    return act_facts.ActivationFact(1, sha, parse_ts(at), {})


def test_anchor_time_from_activation_not_commit_date(monkeypatch) -> None:
    """Приёмка №19: время якоря — merged_at PR, а не дата коммита."""
    monkeypatch.setattr(
        act_facts,
        "activation",
        lambda t, s, sha: _activation(sha, "2026-10-12T08:00:00Z"),
    )
    times = act_trust.load_anchor_times(
        Anon({}), _trust((Epoch(1, BOOT),)), parse_ts("2026-10-12T10:00:00Z")
    )
    assert times == {BOOT: parse_ts("2026-10-12T08:00:00Z")}


def test_old_anchor_unavailable_is_kept_not_blocking(monkeypatch) -> None:
    """e7 + B1-10: все якоря читаются; недоступный старый — значение, а не отказ."""

    def activation(t, s, sha):  # noqa: ANN001
        if sha == BOOT:
            return Unavailable("object_missing")
        return _activation(sha, "2026-10-12T08:00:00Z")

    monkeypatch.setattr(act_facts, "activation", activation)
    epochs = (Epoch(1, BOOT, BOOT, "2026-10-11T00:00:00Z", "x"), Epoch(2, "c" * 40))
    times = act_trust.load_anchor_times(
        Anon({}), _trust(epochs), parse_ts("2026-10-12T10:00:00Z")
    )
    assert isinstance(times[BOOT], Unavailable)
    assert times["c" * 40] == parse_ts("2026-10-12T08:00:00Z")


def _history_case(name: str) -> tuple[dict, act_trust.Trust]:
    import json

    root = Path(__file__).resolve().parent.parent
    spec = json.loads(
        (root / "contracts/approval-facts/v3/vectors/history" / name).read_text("utf-8")
    )
    epochs = (Epoch(1, spec["anchor"]),)
    return spec, _trust(epochs, spec["repo"], spec["branch"], spec["anchor"])


@pytest.mark.parametrize(
    "name",
    [
        "rebase_multi_commit.json",
        "rebase_multi_commit_unassociated.json",
        "direct_push.json",
    ],
)
def test_history_violation_records_event(name: str) -> None:
    """Приёмка №28 (многокоммитный rebase-merge), №21 (прямой push): HistoryViolation
    открытой эпохи → событие периметра, Unavailable."""
    spec, trust = _history_case(name)
    journal = Journal()
    got = act_trust.load_ranges(
        v3.replay_transport(spec["transport"]),
        trust,
        {spec["anchor"]: parse_ts("2026-01-01T00:00:00Z")},
        parse_ts("2027-01-01T00:00:00Z"),
        journal,
    )
    assert isinstance(got, Unavailable) and journal.records[0][0] == "approval-policy"
    assert got.reason.startswith("нарушение периметра")


def test_single_commit_rebase_merge_not_detected(monkeypatch) -> None:
    """Приёмка №28, предел: однокоммитный rebase-merge принят, как squash."""
    spec, trust = _history_case("rebase_single_commit_accepted.json")
    monkeypatch.setattr(
        act_trust, "policy_set", lambda anon, source, a: Unavailable("stub")
    )
    journal = Journal()
    got = act_trust.load_ranges(
        v3.replay_transport(spec["transport"]),
        trust,
        {spec["anchor"]: parse_ts("2026-01-01T00:00:00Z")},
        parse_ts("2027-01-01T00:00:00Z"),
        journal,
    )
    assert not isinstance(got, Unavailable) and journal.records == []
    assert isinstance(got[0][1], tuple)


def test_closed_epoch_break_registers_event(monkeypatch) -> None:
    """§6.3 (ревизия b1, B1-4): нарушение формы истории в диапазоне ЗАКРЫТОЙ
    подтверждённой эпохи — тоже событие периметра и отказ."""
    monkeypatch.setattr(
        act_facts,
        "history",
        lambda t, s, a, u: (
            act_facts.HistoryBreak("commit_without_pr: x")
            if u
            else (act_facts.ActivationFact(1, a, parse_ts("2026-10-12T08:00:00Z"), {}),)
        ),
    )
    monkeypatch.setattr(
        act_trust, "policy_set", lambda anon, source, a: Unavailable("stub")
    )
    epochs = (Epoch(1, BOOT, BOOT, "2026-10-11T00:00:00Z", "x"), Epoch(2, "c" * 40))
    journal = Journal()
    got = act_trust.load_ranges(
        Anon({}),
        _trust(epochs),
        {
            BOOT: parse_ts("2026-10-01T00:00:00Z"),
            "c" * 40: parse_ts("2026-10-12T08:00:00Z"),
        },
        parse_ts("2026-10-12T10:00:00Z"),
        journal,
    )
    assert isinstance(got, Unavailable)
    assert journal.records == [("approval-policy", "commit_without_pr: x")]


def test_unknown_anchor_time_makes_range_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(act_facts, "history", lambda t, s, a, u: pytest.fail("read"))
    got = act_trust.load_ranges(
        Anon({}),
        _trust((Epoch(1, BOOT),)),
        {BOOT: Unavailable("object_missing")},
        parse_ts("2026-10-12T10:00:00Z"),
        Journal(),
    )
    assert isinstance(got[0][1], Unavailable)
    assert got[0][1].reason == "якорь эпохи 1: object_missing"


# --- режим продукта сейчас (§10; ревизия b1, B1-5) ----------------------------------


def _set(mode: str | None) -> object:
    from governance.act_policy import parse_policy_set
    from tests.act_cases import FILES

    modes = f"  owner/p: {mode}\n" if mode else "  owner/other: merge\n"
    return parse_policy_set(
        BOOT,
        {
            **FILES,
            "policy/modes.yaml": (
                "schema: approval-policy-modes/v1\nmodes:\n" + modes
            ).encode(),
        },
    )


def _view(name: str, hms: str):
    from governance.act_check import ActivationView

    sha = f"{name:0<40}"
    return ActivationView(("activation", sha), sha, parse_ts(f"2026-10-12T{hms}Z"))


def _mode(modes: list[str | None], *, epochs=None, ranges=None):
    views = [_view(f"p{i}", f"0{i}:00:00") for i in range(len(modes))]
    sets = {v.merge_commit: _set(m) for v, m in zip(views, modes, strict=True)}
    trust = _trust(epochs or (Epoch(1, views[0].merge_commit),))
    return act_trust.product_mode(
        trust,
        ranges or {1: tuple(views)},
        sets,
        "owner/p",
        parse_ts("2026-10-12T12:00:00Z"),
    )


def test_mode_never_entered_is_pre_migration() -> None:
    assert _mode([None, None]) == act_trust.PRE_MIGRATION


def test_current_activation_is_last_of_open_epoch_or_unavailable() -> None:
    """B3-2: «текущая активация» — последняя активация ОТКРЫТОЙ эпохи; пустая или
    недочитанная открытая эпоха — `Unavailable`, не активация закрытой."""
    v0, v1, v2 = (
        _view("p0", "01:00:00"),
        _view("p1", "02:00:00"),
        _view("p2", "03:00:00"),
    )
    epochs = (
        Epoch(1, v0.merge_commit, v0.merge_commit, "2026-10-12T02:30:00Z", "x"),
        Epoch(2, v2.merge_commit),
    )
    trust = _trust(epochs)
    assert act_trust.current_activation(trust, {1: (v0, v1), 2: (v2,)}) == v2
    empty = act_trust.current_activation(trust, {1: (v0, v1), 2: ()})
    assert (
        isinstance(empty, Unavailable)
        and empty.reason == "в открытой эпохе нет активаций"
    )
    assert isinstance(
        act_trust.current_activation(trust, {1: (v0, v1), 2: Unavailable("x")}),
        Unavailable,
    )


def test_mode_current_values() -> None:
    assert _mode([None, "merge"]) == "merge"
    assert _mode([None, "merge", "review"]) == "review"


@pytest.mark.parametrize(
    ("modes", "reason"),
    [
        (["merge", None], "удалён после первой записи"),
        (["review", None], "удалён после первой записи"),
        (["merge", "review", "merge"], "обратное переключение review → merge"),
    ],
    ids=["merge_removed", "review_removed", "rolled_back"],
)
def test_mode_removed_or_rolled_back_is_unavailable(modes: list, reason: str) -> None:
    got = _mode(modes)
    assert isinstance(got, Unavailable) and reason in got.reason


def test_mode_history_with_unread_set_is_unavailable() -> None:
    """b4: набор активации в истории режима не прочитан — `_modes` отказывает."""
    views = [_view("p0", "00:00:00"), _view("p1", "01:00:00")]
    sets = {views[0].merge_commit: _set("merge")}  # набора p1 нет
    got = act_trust.product_mode(
        _trust((Epoch(1, views[0].merge_commit),)),
        {1: tuple(views)},
        sets,
        "owner/p",
        parse_ts("2026-10-12T12:00:00Z"),
    )
    assert isinstance(got, Unavailable) and got.reason.startswith(
        f"набор {views[1].merge_commit[:12]}"
    )


def test_mode_needs_prior_epoch_unless_review() -> None:
    v1, v2 = _view("p0", "01:00:00"), _view("p1", "02:00:00")
    epochs = (
        Epoch(1, v1.merge_commit, v1.merge_commit, "2026-10-12T01:30:00Z", "x"),
        Epoch(2, v2.merge_commit),
    )
    sets = {v1.merge_commit: _set("review"), v2.merge_commit: _set("merge")}
    now = parse_ts("2026-10-12T12:00:00Z")
    gone = {1: Unavailable("object_missing"), 2: (v2,)}
    got = act_trust.product_mode(_trust(epochs), gone, sets, "owner/p", now)
    assert isinstance(got, Unavailable) and got.reason.startswith(
        "история режима, эпоха 1"
    )
    full = {1: (v1,), 2: (v2,)}
    assert isinstance(
        act_trust.product_mode(_trust(epochs), full, sets, "owner/p", now), Unavailable
    )  # review в E1, merge в E2 — откат через границу эпох
    sets[v2.merge_commit] = _set("review")
    assert (
        act_trust.product_mode(_trust(epochs), gone, sets, "owner/p", now) == "review"
    )


def test_history_read_failure_no_event(monkeypatch) -> None:
    monkeypatch.setattr(
        act_facts, "history", lambda t, s, a, u: Unavailable("http: 403")
    )
    journal = Journal()
    got = act_trust.load_ranges(
        Anon({}),
        _trust((Epoch(1, BOOT),)),
        {BOOT: parse_ts("2026-10-12T08:00:00Z")},
        parse_ts("2026-10-12T10:00:00Z"),
        journal,
    )
    assert isinstance(got[0][1], Unavailable) and journal.records == []


def test_policy_sets_after_t_not_loaded(monkeypatch) -> None:
    acts = (
        _activation(BOOT, "2026-10-12T08:00:00Z"),
        _activation("e" * 40, "2026-10-12T11:00:00Z"),
    )
    monkeypatch.setattr(act_facts, "history", lambda t, s, a, u: acts)
    loaded: list[str] = []
    monkeypatch.setattr(
        act_trust,
        "policy_set",
        lambda anon, source, a: loaded.append(a.merge_commit) or Unavailable("x"),
    )
    act_trust.load_ranges(
        Anon({}),
        _trust((Epoch(1, BOOT),)),
        {BOOT: parse_ts("2026-10-12T08:00:00Z")},
        parse_ts("2026-10-12T10:00:00Z"),
        Journal(),
    )
    assert loaded == [BOOT]


def _policy_routes(files: dict[str, str], commit: str) -> dict:
    return _commit_world(AP, commit, files)


POLICY = {
    "policy/approvers.env": "AUTHORIZED_APPROVER_ACCOUNTS=alice\n"
    "AUTHORIZED_MERGER_ACCOUNTS=df-reviewer[bot]\n",
    "policy/modes.yaml": (
        "schema: approval-policy-modes/v1\nmodes:\n  owner/p: review\n"
    ),
    "policy/actors.yaml": "version: 1\nhuman_identities: [github:alice]\n"
    "agent_identities: [github:df-reviewer]\n",
}


def _fact(files: dict[str, str]) -> act_facts.ActivationFact:
    digests = {k: hashlib.sha256(v.encode()).hexdigest() for k, v in files.items()}
    return act_facts.ActivationFact(1, BOOT, parse_ts("2026-10-12T08:00:00Z"), digests)


def test_policy_set_from_one_commit() -> None:
    got = act_trust.policy_set(
        Anon(_policy_routes(POLICY, BOOT)),
        _trust((Epoch(1, BOOT),)).source,
        _fact(POLICY),
    )
    assert not isinstance(got, Unavailable) and got.modes == {"owner/p": "review"}


def test_policy_bytes_not_matching_activation_is_mixing() -> None:
    """Приёмка №32 для читателя: байты не те, что посчитал steward."""
    other = {**POLICY, "policy/modes.yaml": POLICY["policy/modes.yaml"] + "# x\n"}
    got = act_trust.policy_set(
        Anon(_policy_routes(other, BOOT)),
        _trust((Epoch(1, BOOT),)).source,
        _fact(POLICY),
    )
    assert isinstance(got, Unavailable) and got.reason.startswith("смешение версий")


def test_activation_without_actors_file_unavailable() -> None:
    """О3: отсутствующий файл не входит в files_sha256 → набор неполон."""
    partial = {k: v for k, v in POLICY.items() if k != "policy/actors.yaml"}
    got = act_trust.policy_set(
        Anon(_policy_routes(POLICY, BOOT)),
        _trust((Epoch(1, BOOT),)).source,
        _fact(partial),
    )
    assert isinstance(got, Unavailable) and got.reason.endswith(
        ": нет policy/actors.yaml"
    )
