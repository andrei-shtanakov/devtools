"""Доверенный контекст (спека A rev 16 §6.1–6.5; приёмка №19, 28, 29, 33–36, 39, 45)."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import subprocess
from dataclasses import replace
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("steward")

from steward.approvalfacts import v3  # noqa: E402

from governance import act_anon, act_facts, act_source, act_trust  # noqa: E402
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
    return act_trust.load_trust(
        act_trust.Readers(as_credential(answers or {})),
        binding(),
        journal or Journal(),
        creds=creds,
        open_url=_Wire(routes),
    )


def _via(trust: act_trust.Trust, anon: object) -> act_trust.Trust:
    """Доверие, прочитанное транспортом `anon` (стенд якорей и диапазонов)."""
    return replace(trust, snapshot=replace(trust.snapshot, transport=anon))


TOKEN = "ghp_History0token"
HISTORY_TOML = BINDING_TOML + 'history_gh_config_dir = "/etc/df/gh-history"\n'
DF_TOKEN_ENV = (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GH_HOST",
    "GH_ENTERPRISE_TOKEN",
    "GITHUB_ENTERPRISE_TOKEN",
)


class _Done:
    def __init__(self, rc: int, out: str = "", err: str = "") -> None:
        self.returncode, self.stdout, self.stderr = rc, out, err


class _Answer(io.BytesIO):
    def __init__(self, status: int, body: object) -> None:
        super().__init__(json.dumps(body).encode())
        self.status, self.headers = status, Message()


class _Wire:
    """`open_url` настоящего `AnonTransport` поверх маршрутов `trust_routes`;
    журнал `(путь, заголовки)` каждого ушедшего запроса."""

    def __init__(self, routes: dict) -> None:
        self.routes, self.calls = routes, []

    def __call__(self, req, timeout: float):  # noqa: ANN001
        path = req.full_url.removeprefix(act_anon.API)
        self.calls.append((path, dict(req.header_items())))
        return _Answer(*self.routes[path])


class _Tokens:
    """`token_source`: журнал вызовов; значения — по очереди (последнее повторяется)."""

    def __init__(self, *values: object) -> None:
        self.values, self.calls = list(values), []

    def __call__(self, config_dir: Path) -> object:
        self.calls.append(config_dir)
        return self.values[min(len(self.calls), len(self.values)) - 1]


def _profile(tmp_path: Path) -> Path:
    profile = tmp_path / "gh-history"
    profile.mkdir()
    (profile / "hosts.yml").write_text("github.com:\n  user: reader\n")
    return profile


def _with_history(tmp_path: Path) -> act_trust.Binding:
    return replace(binding(), history_gh_config_dir=tmp_path / "gh-history")


def _self_reported(routes: dict) -> dict:
    """Ответы `GET repos/{repo}` с `permissions` — для самоотчёта транспортом (Р-18)."""
    routes[f"repos/{DT}"] = (200, {"full_name": DT, **_perms()})
    routes[f"repos/{AP}"] = (200, {**routes[f"repos/{AP}"][1], **_perms()})
    return routes


def _decide(
    b, routes_or_wire, *, creds=(AGENT,), tokens=None, readers=None, journal=None
):
    """`load_trust` только через швы `open_url`/`token_source`."""
    wire = (
        routes_or_wire if isinstance(routes_or_wire, _Wire) else _Wire(routes_or_wire)
    )
    got = act_trust.load_trust(
        readers or act_trust.Readers(as_credential({})),
        b,
        journal or Journal(),
        creds=creds,
        open_url=wire,
        token_source=tokens or _Tokens(TOKEN),
    )
    return got, wire


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


def test_binding_oversize_refused(tmp_path: Path) -> None:
    """M2: файл больше `MAX_BINDING` не разбирается усечённым."""
    big = tmp_path / "trust.toml"
    big.write_text(BINDING_TOML + "#" * act_trust.MAX_BINDING + "\n", encoding="utf-8")
    got = act_trust.load_binding(big, owner_uid=os.getuid())
    assert isinstance(got, Unavailable) and "больше" in got.reason


@pytest.mark.parametrize("mode", [0o664, 0o646], ids=["group", "world"])
def test_binding_writable_by_others_refused(tmp_path: Path, mode: int) -> None:
    """M3: привязка, которую пишет группа или все, — не доверенная."""
    path = tmp_path / "trust.toml"
    path.write_text(BINDING_TOML, encoding="utf-8")
    path.chmod(mode)
    got = act_trust.load_binding(path, owner_uid=os.getuid())
    assert isinstance(got, Unavailable) and "без записи группы/всех" in got.reason


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
    wire = _Wire(trust_routes(None, J1))
    act_trust.load_trust(
        act_trust.Readers(as_credential({})),
        binding(),
        Journal(),
        creds=(AGENT,),
        open_url=wire,
    )
    assert not any("rulesets" in p or "bypass" in p for p, _ in wire.calls)


def test_issue_signal_not_part_of_protocol() -> None:
    """Приёмка №39: issues не читаются вовсе."""
    wire = _Wire(trust_routes(None, J1))
    act_trust.load_trust(
        act_trust.Readers(as_credential({})),
        binding(),
        Journal(),
        creds=(AGENT,),
        open_url=wire,
    )
    assert not any("/issues" in p for p, _ in wire.calls)


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
        _via(_trust((Epoch(1, BOOT),)), Anon({})), parse_ts("2026-10-12T10:00:00Z")
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
        _via(_trust(epochs), Anon({})), parse_ts("2026-10-12T10:00:00Z")
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
        _via(trust, v3.replay_transport(spec["transport"])),
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
        _via(trust, v3.replay_transport(spec["transport"])),
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
        _via(_trust(epochs), Anon({})),
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
        _via(_trust((Epoch(1, BOOT),)), Anon({})),
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
        _via(_trust((Epoch(1, BOOT),)), Anon({})),
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
        _via(_trust((Epoch(1, BOOT),)), Anon({})),
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


def test_chain_longer_than_max_unavailable(monkeypatch) -> None:
    """M3: first-parent обход ограничен `MAX_CHAIN`."""
    monkeypatch.setattr(act_trust, "MAX_CHAIN", 1)
    got = act_trust.read_trust_snapshot(Anon(trust_routes(J1, J1)), binding())
    assert isinstance(got, Unavailable) and "цепочка длиннее 1" in got.reason


def test_credentials_list_distinct_merge_profile(tmp_path: Path) -> None:
    """M3: отдельный профиль мержа — свой credential."""
    got = act_trust.credentials(
        {"GITHUB_TOKEN": "b"},
        agent_dir=tmp_path / "review",
        merge_dir=tmp_path / "merge",
    )
    assert [c.label for c in got] == [
        "profile:agent",
        "profile:merge",
        "env:GITHUB_TOKEN",
    ]
    assert got[1].gh_config_dir == tmp_path / "merge"


def test_self_report_without_permissions_names_reason() -> None:
    """M6: самоотчёт получен, но без поля `permissions` — причина не «ok»."""
    journal = Journal()
    answers = {("profile:agent", f"repos/{DT}"): Fact(Outcome.FOUND, {"id": 1}, "ok")}
    got = load(trust_routes(None, J1), journal, answers=answers)
    assert isinstance(got, Unavailable) and journal.records == []
    assert got.reason.endswith("права credential на источнике не получены")


def test_load_trust_without_credentials_unavailable() -> None:
    """M8: пустой набор credential не проходит самоотчёт «вакуумно»."""
    wire = _Wire(trust_routes(None, J1))
    got = act_trust.load_trust(
        act_trust.Readers(as_credential({})),
        binding(),
        Journal(),
        creds=(),
        open_url=wire,
    )
    assert isinstance(got, Unavailable) and "ни одного credential" in got.reason
    assert [p for p, _ in wire.calls] == []


def test_gh_get_pins_host_and_drops_host_env(monkeypatch) -> None:
    """M1: самоотчёт — только `github.com`; `GH_HOST`, enterprise-токены не идут."""
    seen: list[tuple[list[str], dict[str, str]]] = []

    class Done:
        returncode, stdout, stderr = 0, "{}", ""

    def run(argv, *, env, **kw):  # noqa: ANN001, ANN003
        seen.append((argv, env))
        return Done()

    for var in (
        "GH_HOST",
        "GH_ENTERPRISE_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
        "GITHUB_TOKEN",
    ):
        monkeypatch.setenv(var, "stray")
    monkeypatch.setattr(act_trust.subprocess, "run", run)
    act_trust.gh_get(AGENT, "repos/o/p")
    argv, env = seen[0]
    assert argv == ["gh", "api", "--hostname", "github.com", "repos/o/p"]
    assert not {
        "GH_HOST",
        "GH_ENTERPRISE_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
        "GITHUB_TOKEN",
        "GH_TOKEN",
    } & set(env)
    assert env["GH_CONFIG_DIR"] == "/x/review"


# --- токен истории (С-1, спека rev 18 §6.2; Task 11c) ----------------------------


def test_binding_history_key_optional() -> None:
    assert binding().history_gh_config_dir is None
    got = act_trust.parse_binding(HISTORY_TOML)
    assert got.history_gh_config_dir == Path("/etc/df/gh-history")


@pytest.mark.parametrize(
    "value", ['""', '"gh-history"', "1"], ids=["empty", "relative", "int"]
)
def test_binding_history_key_must_be_absolute_path(value: str) -> None:
    with pytest.raises(
        act_trust.TrustError, match="history_gh_config_dir — абсолютный путь"
    ):
        act_trust.parse_binding(BINDING_TOML + f"history_gh_config_dir = {value}\n")


def test_binding_history_key_agrees_with_installer() -> None:
    """Р-6 + R8: оба разборщика принимают ключ с тем же значением и отвергают те же
    недопустимые значения (абсолютный путь — и только он)."""
    from df.config import ConfigError
    from df.stage2_config import parse_trust

    ours, theirs = act_trust.parse_binding(HISTORY_TOML), parse_trust(HISTORY_TOML)
    assert ours.history_gh_config_dir == Path(theirs.history_gh_config_dir)
    assert parse_trust(BINDING_TOML).history_gh_config_dir is None
    for value in ('""', '"gh-history"', "1"):
        text = BINDING_TOML + f"history_gh_config_dir = {value}\n"
        with pytest.raises(act_trust.TrustError):
            act_trust.parse_binding(text)
        with pytest.raises(ConfigError, match="history_gh_config_dir"):
            parse_trust(text)


def test_history_token_spawn_argv_and_env(monkeypatch, tmp_path: Path) -> None:
    """R2: `gh auth token --hostname github.com` под профилем; окружение — как у `gh_get`."""
    seen: list[tuple[list[str], dict[str, str]]] = []

    def run(argv, *, env, **kw):  # noqa: ANN001, ANN003
        seen.append((argv, env))
        assert kw["timeout"] == 60
        return _Done(0, out=TOKEN + "\n")

    for var in DF_TOKEN_ENV:
        monkeypatch.setenv(var, "stray")
    monkeypatch.setattr(act_trust.subprocess, "run", run)
    assert act_trust.history_token(_profile(tmp_path)) == TOKEN
    argv, env = seen[0]
    assert argv == ["gh", "auth", "token", "--hostname", "github.com"]
    assert not set(DF_TOKEN_ENV) & set(env)
    assert env["GH_CONFIG_DIR"] == str(tmp_path / "gh-history")


SECRET = "ghp_Leak0secret"


@pytest.mark.parametrize(
    ("case", "answer", "reason"),
    [
        ("missing", None, "профиль токена истории"),
        ("no_hosts", None, "профиль токена истории"),
        (
            "exit",
            _Done(1, out=SECRET, err=f"bad {SECRET}"),
            "gh auth token (profile:history): код выхода 1",
        ),
        (
            "empty",
            _Done(0, out="\n", err=SECRET),
            "gh auth token (profile:history): пустой вывод",
        ),
        (
            "form",
            _Done(0, out=f"{SECRET} extra\n"),
            "gh auth token (profile:history): вывод не в форме токена",
        ),
        (
            "oserror",
            OSError(f"exec failed: {SECRET}"),
            "gh auth token (profile:history): OSError",
        ),
        (
            "timeout",
            subprocess.TimeoutExpired(["gh"], 60, output=SECRET, stderr=SECRET),
            "gh auth token (profile:history): TimeoutExpired",
        ),
    ],
    ids=["missing", "no_hosts", "exit", "empty", "form", "oserror", "timeout"],
)
def test_history_token_failures_are_unavailable(
    monkeypatch, tmp_path: Path, case: str, answer: object, reason: str
) -> None:
    """R5 / Р-20, Р-21, C1-02: профиля нет — `gh` не запускается (keyring); причина —
    фиксированная строка с кодом выхода или именем класса исключения, ни stdout, ни
    stderr, ни текста исключения (секрет — в каждом из них)."""
    profile = tmp_path / "gh-history"
    if case == "no_hosts":
        profile.mkdir()
    elif case != "missing":
        _profile(tmp_path)

    def run(*a, **k):  # noqa: ANN002, ANN003
        if answer is None:
            pytest.fail("gh запущен без профиля")
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(act_trust.subprocess, "run", run)
    got = act_trust.history_token(profile)
    assert isinstance(got, Unavailable) and reason in got.reason
    assert SECRET not in got.reason


@pytest.mark.parametrize(
    "answer",
    [
        _Done(1, out=SECRET, err=SECRET),
        _Done(0, out=f"{SECRET} x"),
        OSError(SECRET),
        subprocess.TimeoutExpired(["gh"], 60, output=SECRET, stderr=SECRET),
    ],
    ids=["exit", "form", "oserror", "timeout"],
)
def test_history_token_error_absent_from_final_reason(
    monkeypatch, tmp_path, answer
) -> None:
    """C1-02: и в конечной причине решения (`history_transport` → `load_trust`, настоящий
    `history_token` по умолчанию) секрета нет; запросов нет."""
    _profile(tmp_path)

    def run(*a, **k):  # noqa: ANN002, ANN003
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(act_trust.subprocess, "run", run)
    wire = _Wire({})
    got = act_trust.load_trust(
        act_trust.Readers(as_credential({})),
        _with_history(tmp_path),
        Journal(),
        creds=(AGENT,),
        open_url=wire,
    )
    assert isinstance(got, Unavailable) and "токен истории: gh auth token" in got.reason
    assert SECRET not in got.reason and wire.calls == []


def test_credential_repr_hides_token() -> None:
    assert "s3cret" not in repr(act_trust.Credential("env:GH_TOKEN", token="s3cret"))


def test_gh_get_refuses_history_profile(monkeypatch) -> None:
    """Р-18: самоотчёт токена истории не идёт через `gh` (токен не уходит в окружение)."""
    monkeypatch.setattr(act_trust.subprocess, "run", lambda *a, **k: pytest.fail("gh"))
    cred = act_trust.Credential(
        "profile:history", gh_config_dir=Path("/etc/df/gh-history")
    )
    got = act_trust.gh_get(cred, "repos/o/p")
    assert got.outcome is Outcome.UNAVAILABLE
    assert "только через транспорт истории" in got.detail


def test_transport_fact_failure_is_unavailable() -> None:
    cred = act_trust.Credential("profile:history")
    got = act_trust.transport_fact(
        Anon({"repos/o/p": (403, {"message": "x"})}), cred, "repos/o/p"
    )
    assert got.outcome is Outcome.UNAVAILABLE
    assert got.detail.startswith("GET repos/o/p (profile:history): HTTP 403")


def test_transport_fact_passes_body_exactly() -> None:
    """C1-01: успешное тело ответа уходит в `Fact` без изменений."""
    body = {
        "full_name": AP,
        "permissions": {"push": True, "maintain": False, "admin": False, "pull": True},
        "extra": [1, {"k": None}],
    }
    cred = act_trust.Credential("profile:history")
    got = act_trust.transport_fact(Anon({"repos/o/p": (200, body)}), cred, "repos/o/p")
    assert got.outcome is Outcome.FOUND and got.value == body


# --- вход решения строит транспорт сам (C6, решение контроллера) -------------------


def test_history_transport_built_from_binding(monkeypatch, tmp_path: Path) -> None:
    """(а) Ключа нет — анонимно, `token_source` не вызывается. Ключ `D` — ровно один вызов
    `token_source(D)`, все запросы решения с этим токеном, самоотчёт `profile:history`
    на обоих репо — тем же транспортом; `Trust.transport` — тот же объект."""
    monkeypatch.setattr(act_trust.subprocess, "run", lambda *a, **k: pytest.fail("gh"))
    tokens = _Tokens(TOKEN, "ghp_Second0value")
    got, wire = _decide(
        binding(), _self_reported(trust_routes(None, J1)), tokens=tokens
    )
    assert isinstance(got, act_trust.Trust) and tokens.calls == [] and wire.calls
    assert all("Authorization" not in h for _, h in wire.calls)
    got, wire = _decide(
        _with_history(tmp_path), _self_reported(trust_routes(None, J1)), tokens=tokens
    )
    assert isinstance(got, act_trust.Trust)
    assert tokens.calls == [tmp_path / "gh-history"]
    assert all(h.get("Authorization") == f"Bearer {TOKEN}" for _, h in wire.calls)
    assert {p for p, _ in wire.calls} >= {f"repos/{AP}", f"repos/{DT}"}
    assert isinstance(got.transport, act_anon.AnonTransport)
    assert {p for p, _ in wire.calls} == set(got.transport.sent)
    assert TOKEN not in repr(got)


@pytest.mark.parametrize("keyed", [False, True], ids=["no_key", "key"])
@pytest.mark.parametrize(
    "extra",
    [
        act_trust.Credential(
            "profile:history", gh_config_dir=Path("/etc/df/gh-history")
        ),
        act_trust.Credential("profile:history", token="t"),
        act_trust.Credential("env:OTHER_TOKEN", token="t"),
        act_trust.Credential("profile:agent", gh_config_dir=Path("/x"), token="t"),
        act_trust.Credential("env:GH_TOKEN", gh_config_dir=Path("/x"), token="t"),
        act_trust.Credential("profile:agent"),
        act_trust.Credential("profile:merge"),
        act_trust.Credential("env:GH_TOKEN"),
        act_trust.Credential("env:GITHUB_TOKEN"),
    ],
    ids=[
        "history_dir",
        "history_token",
        "unknown_label",
        "profile_with_token",
        "env_with_dir",
        "agent_without_dir",
        "merge_without_dir",
        "gh_token_without_token",
        "github_token_without_token",
    ],
)
def test_caller_history_credential_refused_before_io(
    tmp_path, keyed: bool, extra
) -> None:
    """(б) Credential вне закрытого набора форм `credentials()` → `HISTORY_NOT_WIRED`:
    ни одного запроса, `token_source` не вызывается."""
    tokens = _Tokens(TOKEN)
    b = _with_history(tmp_path) if keyed else binding()
    got, wire = _decide(b, trust_routes(None, J1), creds=(AGENT, extra), tokens=tokens)
    assert isinstance(got, Unavailable) and got.reason == act_trust.HISTORY_NOT_WIRED
    assert wire.calls == [] and tokens.calls == []


@pytest.mark.parametrize("keyed", [False, True], ids=["no_key", "key"])
def test_injected_transport_cannot_reach_trust_reads(tmp_path, keyed: bool) -> None:
    """(в) Объект, похожий на прежние `Readers` с транспортом (`anon`, `history`), — его
    транспорт не получает ни одного запроса источников доверия; все чтения — через
    `open_url` транспорта, построенного `load_trust`."""

    class Recorder:
        def __init__(self) -> None:
            self.sent: list[str] = []

        def send(self, request):  # noqa: ANN001
            self.sent.append(request.path)
            return v3.Response(200, body={})

    stale = Recorder()
    readers = SimpleNamespace(
        anon=stale,
        history=act_trust.Credential("profile:history"),
        as_credential=as_credential({}),
    )
    b = _with_history(tmp_path) if keyed else binding()
    got, wire = _decide(b, _self_reported(trust_routes(None, J1)), readers=readers)
    assert isinstance(got, act_trust.Trust)
    assert stale.sent == [] and wire.calls and got.transport is not stale


@pytest.mark.parametrize(
    "stale_first", [False, True], ids=["clear", "stale_then_clear"]
)
def test_one_token_per_decision_through_journal(
    monkeypatch, tmp_path, stale_first: bool
) -> None:
    """C7-01: настоящий путь журнала (`journal_for` → `DevJournal` → `ForgeEpochs.head`) и
    повтор `STALE`: `token_source` вызван ровно один раз на решение, каждый запрос — с T1,
    T2 не появляется ни в одном заголовке. Тот же `_Tokens` стоит и на месте
    `history_token` по умолчанию — построение транспорта в перечитывании его бы вызвало."""
    from governance import act_journal

    tokens = _Tokens(TOKEN, "ghp_Second0value")
    monkeypatch.setattr(act_trust, "history_token", tokens)
    monkeypatch.setattr(act_trust.subprocess, "run", lambda *a, **k: pytest.fail("gh"))
    monkeypatch.setattr(act_journal, "_publisher_present", lambda: False)
    b = _with_history(tmp_path)
    journal = act_journal.journal_for(
        state_home=tmp_path / "state",
        run_id="r",
        binding=b,
        env={},
        policy_repository_id=22,
    )
    if stale_first:  # первый ответ журнала — STALE: load_trust перечитывает снимок
        real_status = journal.status
        answers = [act_trust.STALE]
        monkeypatch.setattr(
            journal,
            "status",
            lambda snap: answers.pop() if answers else real_status(snap),
        )
    got, wire = _decide(
        b, _self_reported(trust_routes(None, J1)), tokens=tokens, journal=journal
    )
    assert isinstance(got, act_trust.Trust)
    assert tokens.calls == [tmp_path / "gh-history"]
    assert all(h.get("Authorization") == f"Bearer {TOKEN}" for _, h in wire.calls)
    assert not any("ghp_Second0value" in str(h) for _, h in wire.calls)
    heads = [p for p, _ in wire.calls if p == "repositories/11"]
    assert len(heads) >= (2 if stale_first else 1)


@pytest.fixture()
def history_world(monkeypatch, tmp_path: Path):
    """Ключ в привязке; профиль агента — стенд; `gh` запрещён.
    Возвращает `load(routes, journal) -> Trust | Unavailable`."""
    monkeypatch.setattr(act_trust.subprocess, "run", lambda *a, **k: pytest.fail("gh"))

    def run(routes: dict, journal: Journal) -> object:
        return _decide(_with_history(tmp_path), routes, journal=journal)[0]

    return run


@pytest.mark.parametrize(("repo", "role"), [(AP, "approval-policy"), (DT, "df-trust")])
def test_history_self_report_push_is_violation(
    history_world, repo: str, role: str
) -> None:
    """R3, C1-01: ответ `GET repos/{repo}` транспортом истории с `push: true` —
    нарушение периметра этого репо (событие + отказ)."""
    routes = _self_reported(trust_routes(None, J1))
    routes[f"repos/{repo}"] = (200, {**routes[f"repos/{repo}"][1], **_perms(True)})
    journal = Journal()
    got = history_world(routes, journal)
    assert isinstance(got, Unavailable) and got.reason.startswith(
        "нарушение периметра: "
    )
    assert len(journal.records) == 1 and journal.records[0][0] == role
    assert journal.records[0][1].startswith(f"profile:history на {repo}: ")


@pytest.mark.parametrize("repo", [AP, DT])
@pytest.mark.parametrize(
    ("answer", "tail"),
    [
        ((200, {"full_name": "x"}), "права credential на источнике не получены"),
        ((403, {"message": "Forbidden"}), "HTTP 403"),
    ],
    ids=["no_permissions", "http_403"],
)
def test_history_self_report_unreadable_is_unavailable(
    history_world, repo: str, answer: tuple, tail: str
) -> None:
    """R3, C1-01: нет `permissions` или HTTP 403 транспортом истории — `Unavailable`
    без события, с меткой credential и репо."""
    routes = _self_reported(trust_routes(None, J1))
    routes[f"repos/{repo}"] = answer
    journal = Journal()
    got = history_world(routes, journal)
    assert isinstance(got, Unavailable) and journal.records == []
    assert f"самоотчёт profile:history на {repo}: " in got.reason
    assert got.reason.endswith(tail)


def test_as_credential_never_called_for_history(tmp_path) -> None:
    """`readers.as_credential` получает только переданные не-history credential."""
    asked: list[str] = []

    def recorder(cred: act_trust.Credential, path: str) -> Fact[object]:
        asked.append(cred.label)
        return Fact(Outcome.FOUND, _perms(), "ok")

    got, _ = _decide(
        _with_history(tmp_path),
        _self_reported(trust_routes(None, J1)),
        readers=act_trust.Readers(recorder),
    )
    assert isinstance(got, act_trust.Trust)
    assert asked == ["profile:agent", "profile:agent"]


@pytest.mark.parametrize(
    "why",
    ["профиль токена истории /etc/df/gh-history не найден", "gh auth token: exit 1"],
)
def test_broken_history_token_no_anonymous_fallback(tmp_path, why: str) -> None:
    """R5 / Р-19, отрицательный двойник: ключ есть, токена нет — ни одного запроса."""
    got, wire = _decide(
        _with_history(tmp_path),
        trust_routes(None, J1),
        tokens=_Tokens(Unavailable(why)),
    )
    assert isinstance(got, Unavailable) and got.reason == f"токен истории: {why}"
    assert wire.calls == []


# --- матрица самоотчёта (C2-01: механизм вместо точечных тестов) ------------------
# Строки — виды credential, столбцы — состояния самоотчёта на одном репо. Четыре вида
# передаёт `credentials()`, `profile:history` создаёт `load_trust` (ключ в привязке).
# `gh_get` — записывающий стенд. Не чистая клетка отличается от чистой ТОЛЬКО
# состоянием этого credential.

KINDS = (
    "profile:agent",
    "profile:merge",
    "profile:history",
    "env:GH_TOKEN",
    "env:GITHUB_TOKEN",
)
STATES = ("push", "no_permissions", "http_403")
CELLS = [(k, s, r) for k in KINDS for s in STATES for r in (AP, DT)]
ROLE = {AP: "approval-policy", DT: "df-trust"}


def _gh_answer(state: str, repo: str) -> Fact[object]:
    return {
        "push": Fact(Outcome.FOUND, _perms(True), "ok"),
        "no_permissions": Fact(Outcome.FOUND, {"full_name": repo}, "ok"),
        "http_403": unavailable(f"gh api repos/{repo}: HTTP 403"),
    }[state]


def _wire_answer(routes: dict, state: str, repo: str) -> tuple[int, object]:
    return {
        "push": (200, {**routes[f"repos/{repo}"][1], **_perms(True)}),
        "no_permissions": (200, {"full_name": repo}),
        "http_403": (403, {"message": "Forbidden"}),
    }[state]


def _matrix(monkeypatch, tmp_path: Path, cell: tuple[str, str, str] | None):
    """Один прогон `load_trust` со всеми пятью видами; `cell` — (вид, состояние, репо)
    единственного отклонения или `None` (все чисты)."""
    seen: list[tuple[act_trust.Credential, str]] = []
    kind, state, repo = cell or (None, None, None)

    def gh_get(cred: act_trust.Credential, path: str) -> Fact[object]:
        seen.append((cred, path))
        if cred.label == kind and path == f"repos/{repo}":
            return _gh_answer(state, repo)
        return Fact(Outcome.FOUND, _perms(), "ok")

    monkeypatch.setattr(act_trust, "gh_get", gh_get)
    monkeypatch.setattr(act_trust.subprocess, "run", lambda *a, **k: pytest.fail("gh"))
    routes = _self_reported(trust_routes(None, J1))
    if kind == "profile:history":
        routes[f"repos/{repo}"] = _wire_answer(routes, state, repo)
    creds = act_trust.credentials(
        {"GH_TOKEN": "tok-gh", "GITHUB_TOKEN": "tok-github"},
        agent_dir=tmp_path / "review",
        merge_dir=tmp_path / "merge",
    )
    journal = Journal()
    got, wire = _decide(
        _with_history(tmp_path),
        routes,
        creds=creds,
        readers=act_trust.default_readers(),
        journal=journal,
    )
    return got, journal, seen, creds, wire


def test_self_report_matrix_clean_routes_every_credential(
    monkeypatch, tmp_path
) -> None:
    """Чистая строка: решение есть; каждый переданный credential дошёл до `gh_get` ТЕМ ЖЕ
    объектом на оба репо; history — только транспортом, с заголовком токена."""
    got, journal, seen, creds, wire = _matrix(monkeypatch, tmp_path, None)
    assert isinstance(got, act_trust.Trust) and journal.records == []
    assert {c.label for c in creds} == set(KINDS) - {"profile:history"}
    assert all(c.label != "profile:history" for c, _ in seen)
    for cred in creds:
        assert [p for c, p in seen if c is cred] == [f"repos/{AP}", f"repos/{DT}"]
    assert {
        p for p, h in wire.calls if h.get("Authorization") == f"Bearer {TOKEN}"
    } >= {f"repos/{AP}", f"repos/{DT}"}


@pytest.mark.parametrize(
    ("kind", "state", "repo"), CELLS, ids=[f"{k}-{s}-{ROLE[r]}" for k, s, r in CELLS]
)
def test_self_report_matrix_cell(
    monkeypatch, tmp_path, kind: str, state: str, repo: str
) -> None:
    """Каждая не чистая клетка: `push` — нарушение с событием ровно этого credential и
    репо; `no_permissions`/`http_403` — `Unavailable` без события с меткой credential."""
    got, journal, seen, _creds, _ = _matrix(monkeypatch, tmp_path, (kind, state, repo))
    assert isinstance(got, Unavailable)
    if (
        kind != "profile:history"
    ):  # маршрутизация: самоотчёт этого credential дошёл до gh_get
        assert any(c.label == kind and p == f"repos/{repo}" for c, p in seen)
    if state == "push":
        assert got.reason.startswith("нарушение периметра: ")
        assert journal.records == [(ROLE[repo], journal.records[0][1])]
        assert journal.records[0][1].startswith(f"{kind} на {repo}: ")
    else:
        assert journal.records == []
        assert f"самоотчёт {kind} на {repo}: " in got.reason


def test_history_violation_wins_over_passed_credential_gap(history_world) -> None:
    """Отклонение 11c-D1: нарушение `profile:history` регистрируется, даже если
    самоотчёт переданного credential недоступен (как «нарушение побеждает» в 12a)."""
    routes = _self_reported(trust_routes(None, J1))
    routes[f"repos/{DT}"] = (200, {**routes[f"repos/{DT}"][1], **_perms(True)})
    journal = Journal()
    got = act_trust.load_trust(
        act_trust.Readers(
            as_credential({("profile:agent", f"repos/{AP}"): unavailable("HTTP 404")})
        ),
        replace(binding(), history_gh_config_dir=Path("/etc/df/gh-history")),
        journal,
        creds=(AGENT,),
        open_url=_Wire(routes),
        token_source=_Tokens(TOKEN),
    )
    assert isinstance(got, Unavailable) and got.reason.startswith("нарушение периметра")
    assert journal.records[0][1].startswith(f"profile:history на {DT}: ")


@pytest.mark.parametrize(
    "value", ["", "ghp_x y", "ghp\nx", 7], ids=["empty", "space", "newline", "int"]
)
def test_token_source_value_not_token_form_refused(tmp_path, value: object) -> None:
    """Отклонение 11c-D2: значение шва `token_source` не в форме токена — отказ без
    запросов (заголовок не строится из произвольной строки)."""
    got, wire = _decide(
        _with_history(tmp_path), trust_routes(None, J1), tokens=_Tokens(value)
    )
    assert isinstance(got, Unavailable)
    assert got.reason == "токен истории: значение не в форме токена"
    assert wire.calls == []


@pytest.mark.parametrize("via", ["same", "symlink"])
@pytest.mark.parametrize("label", ["profile:agent", "profile:merge"])
def test_profile_shared_with_history_refused_before_io(
    tmp_path: Path, label: str, via: str
) -> None:
    """Ревью Task 11c, Minor 3 (решение контроллера): профиль агента/мержа, который
    разрешается в каталог профиля истории, — отказ до ввода-вывода."""
    profile = _profile(tmp_path)
    shared = profile
    if via == "symlink":
        shared = tmp_path / "alias"
        shared.symlink_to(profile)
    cred = act_trust.Credential(label, gh_config_dir=shared)
    creds = (cred,) if label == "profile:agent" else (AGENT, cred)
    tokens = _Tokens(TOKEN)
    got, wire = _decide(
        _with_history(tmp_path), trust_routes(None, J1), creds=creds, tokens=tokens
    )
    assert isinstance(got, Unavailable)
    assert got.reason == act_trust.HISTORY_PROFILE_SHARED
    assert wire.calls == [] and tokens.calls == []


def test_profile_separation_only_with_history_key(tmp_path: Path) -> None:
    """Без ключа истории каталог профиля агента ничем не ограничен."""
    cred = act_trust.Credential("profile:agent", gh_config_dir=_profile(tmp_path))
    got, _ = _decide(binding(), trust_routes(None, J1), creds=(cred,))
    assert isinstance(got, act_trust.Trust)
