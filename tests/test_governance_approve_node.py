"""Двухфазное одобрение узла бандла (§I12): фазы, крэш-окна, восстановление.

Стенд НАСТОЯЩИЙ там, где предмет — состояние: `origin` — bare-репозиторий,
`target` — его клон, а мерж candidate-PR выполняется отдельным клоном
`human` и уезжает в origin. Подменена только форджа (PR как записи), потому
что её у нас нет; git, ветки, коммиты, `merge-base --is-ancestor` и
`git show` — настоящие.

Так сделано ради одного класса ошибок: фаза 3 работает поверх состояния,
ПРОИЗВЕДЁННОГО предыдущим шагом конвейера (мерж candidate), и фикстура
«идеального base», собранная руками, проверяла бы не то, что происходит.
Здесь base каждого следующего шага получается ровно так, как получается в
жизни.
"""

from __future__ import annotations

import ast
import inspect
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from governance import approval_facts as af
from governance import approval_ledger as al
from governance import approve_node as an
from governance import bundle_dag
from governance import bundle_inputs
from governance import merge_gate as mg
from governance import node_approval as na
from governance import run_state as rs
from governance.frontmatter import join_frontmatter, split_frontmatter
from governance.ops import RealOps

BUNDLE = "spec"
WS_ID = "WS-T1"
SLUG = "owner/alpha"
HUMAN = "andrei-shtanakov"
AGENT = "ai-prosto"
MERGED_AT = "2026-09-10T08:00:00Z"
AGENT_MERGED_AT = "2026-09-10T08:05:00Z"

#: Активный DAG стенда — тот же, что у моста; списываем его состав, а не
#: переопределяем: разойдясь, тест проверял бы другой граф.
NODES: dict[str, tuple[str, ...]] = {
    "00-charter.md": (),
    "10-requirements.md": ("charter",),
    "15-behaviour-spec.md": ("requirements",),
    "20-design.md": ("requirements", "behaviour-spec"),
    "25-acceptance.md": ("requirements", "behaviour-spec"),
    "30-decomposition.md": ("design", "acceptance"),
}


def _git(where: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(where), *args],
        capture_output=True, text=True, check=True,
    )
    return done.stdout.strip()


def _show(where: Path, spec: str) -> str:
    """`git show <ref>:<path>` БЕЗ обрезки хвоста.

    Отдельно от `_git`, который `strip`'ает вывод: у файла бандла
    завершающий перевод строки — часть байтов, и обрезав его, фикстура
    считала бы self-hash по содержимому, которого в репозитории нет. На
    этом тест уже один раз обвинил рабочий код.
    """
    done = subprocess.run(
        ["git", "-C", str(where), "show", spec],
        capture_output=True, text=True, check=True,
    )
    return done.stdout


def _node_text(node: str, *, status: str = "draft", body: str = "") -> str:
    return (
        "---\n"
        f"node: {node}\n"
        f"status: {status}\n"
        "version: 1\n"
        "approved_by: ''\n"
        "approved_at: ''\n"
        "---\n"
        "\n"
        f"{body or f'Содержание узла {node}.'}\n"
    )


@dataclass
class Forge:
    """Мини-форджа: PR — записи, состояния которых меняет мерж или закрытие."""

    origin: Path
    prs: dict[int, dict] = field(default_factory=dict)
    next_number: int = 400
    #: Примитивы, которые «не отвечают» — так вводится НЕустановленный факт.
    mute: set[str] = field(default_factory=set)
    #: `close_pr` не подтверждает закрытие (нет прав либо уже закрыт — #177).
    close_confirms: bool = True
    #: Клон, которым исполняются мержи (и человеческие, и агентские).
    merger: Path | None = None
    #: Код обвязки `merge-pr.sh` для агентского мержа finalize (ADR-ECO-011
    #: D5): 0 — мерж исполняется от ai-prosto; иное — «обвязка отказала»,
    #: PR остаётся человеку.
    agent_merge_rc: int = 0
    #: Очередь кодов на ближайшие вызовы `merge` (транзиентные отказы):
    #: исчерпана — действует `agent_merge_rc`.
    agent_merge_rc_seq: list[int] = field(default_factory=list)
    #: Журнал вызовов `Ops.merge`: (pr, пин головы).
    merge_calls: list[tuple[int, str]] = field(default_factory=list)
    #: Ревью конкретного PR: `[{"login": ..., "state": ...}, ...]`. PR без
    #: записи отдаёт `default_reviews` — нормальное состояние finalize-PR
    #: ПОСЛЕ scope-аттестации, в котором агентский мерж и пробуется.
    reviews: dict[int, list[dict]] = field(default_factory=dict)
    default_reviews: list[dict] = field(
        default_factory=lambda: [{"login": AGENT, "state": "APPROVED"}]
    )

    def head_of(self, branch: str) -> str | None:
        done = subprocess.run(
            ["git", "-C", str(self.origin), "rev-parse", f"refs/heads/{branch}"],
            capture_output=True, text=True,
        )
        return done.stdout.strip() if done.returncode == 0 else None


class Ops(RealOps):
    """RealOps с подменённой ТОЛЬКО форджей: git остаётся настоящим."""

    def __init__(self, forge: Forge) -> None:
        self.forge = forge

    def find_pr(self, repo_slug: str, branch: str, *, any_state: bool = False):
        if "find_pr" in self.forge.mute:
            raise RuntimeError("find_pr: gh pr list rc=1: no network")
        for number, pr in sorted(self.forge.prs.items()):
            if pr["branch"] != branch:
                continue
            if not any_state and pr["state"] != "OPEN":
                continue
            return number
        return None

    def pr_facts(self, repo_slug: str, pr: int) -> dict:
        if "pr_facts" in self.forge.mute:
            raise RuntimeError("gh pr view: connection reset")
        rec = self.forge.prs[pr]
        return {
            "state": rec["state"],
            "headRefOid": self.forge.head_of(rec["branch"]),
            "mergedBy": rec.get("mergedBy"),
            "mergedAt": rec.get("mergedAt"),
            "mergeCommit": rec.get("mergeCommit"),
            "isDraft": rec["draft"],
        }

    def create_pr(
        self,
        target_dir: str,
        repo_slug: str,
        branch: str,
        title: str,
        body: str,
        label: str,
        *,
        draft: bool = False,
    ) -> int:
        self.forge.next_number += 1
        number = self.forge.next_number
        self.forge.prs[number] = {
            "branch": branch, "title": title, "body": body,
            "label": label, "draft": draft, "state": "OPEN",
        }
        return number

    def close_pr(self, repo_slug: str, pr: int, comment: str) -> bool:
        if not self.forge.close_confirms:
            return False
        self.forge.prs[pr]["state"] = "CLOSED"
        self.forge.prs[pr]["closed_with"] = comment
        return True

    def delete_remote_branch(self, repo_slug: str, branch: str) -> bool:
        done = subprocess.run(
            ["git", "-C", str(self.forge.origin), "update-ref", "-d",
             f"refs/heads/{branch}"],
            capture_output=True, text=True,
        )
        return done.returncode == 0

    def pr_reviews(self, repo_slug: str, pr: int) -> list[dict] | None:
        """Ревью PR. `mute` даёт None — «факт не получен», а не исключение:
        контракт `RealOps.pr_reviews` именно таков, и вызывающий обязан
        читать его fail-closed."""
        if "pr_reviews" in self.forge.mute:
            return None
        return list(self.forge.reviews.get(pr, self.forge.default_reviews))

    def merge(
        self, repo_name: str, pr: int, sha: str, base: str | None = None
    ) -> int:
        """Агентский мерж — зеркало гвардов `merge-pr.sh`, не сети.

        Лейбл `human-merge-required` и уехавшая голова отказывают так же,
        как обвязка (коды 3 и 2); `agent_merge_rc` моделирует любой иной
        отказ. Успех — настоящий merge-коммит в origin от ai-prosto:
        реконсиляция сверяет байты base, а не ответ форджи.
        """
        self.forge.merge_calls.append((pr, sha))
        rec = self.forge.prs[pr]
        if rec["label"] == an.HUMAN_MERGE_LABEL:
            return 3
        if self.forge.head_of(rec["branch"]) != sha:
            return 2
        rc = (
            self.forge.agent_merge_rc_seq.pop(0)
            if self.forge.agent_merge_rc_seq
            else self.forge.agent_merge_rc
        )
        if rc != 0:
            return rc
        _merge_into_origin(self.forge, pr, login=AGENT, when=AGENT_MERGED_AT)
        return 0


@dataclass
class World:
    origin: Path
    target: Path
    human: Path
    forge: Forge
    ops: Ops
    state: rs.RunState

    def base_text(self, fname: str) -> str:
        return _show(self.target, f"master:{BUNDLE}/{fname}")

    def base_meta(self, fname: str) -> dict:
        return split_frontmatter(self.base_text(fname))[0]

    def sync(self) -> None:
        _git(self.target, "switch", "master")
        _git(self.target, "pull", "--ff-only")


@pytest.fixture()
def world(tmp_path: Path, monkeypatch) -> World:
    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setenv(af.APPROVER_ALLOWLIST_ENV, HUMAN)
    monkeypatch.setattr(an, "_SLEEP", lambda seconds: None)
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "master", str(origin)], check=True
    )
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "master", str(seed)], check=True)
    _git(seed, "config", "user.email", "t@e.st")
    _git(seed, "config", "user.name", "test")
    (seed / BUNDLE).mkdir()
    for fname in NODES:
        node = fname.rsplit(".", 1)[0].split("-", 1)[1]
        (seed / BUNDLE / fname).write_text(_node_text(node), encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "bundle")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "-u", "origin", "master")

    target = tmp_path / "target"
    human = tmp_path / "human"
    for clone in (target, human):
        subprocess.run(
            ["git", "clone", "-q", str(origin), str(clone)], check=True
        )
        _git(clone, "config", "user.email", "t@e.st")
        _git(clone, "config", "user.name", "test")

    forge = Forge(origin=origin, merger=human)
    state = rs.new_run(
        subject="одобрение узлов",
        repo="alpha",
        repo_slug=SLUG,
        ws_id=WS_ID,
        target_dir=str(target),
        bundle_dir=BUNDLE,
        profile="profiles/team-exp.yaml",
        run_id="r-approve",
    )
    state.base_ref = "master"
    rs.save(state)
    return World(origin, target, human, forge, Ops(forge), state)


def merge_pr(
    world: World, pr: int, *, login: str = HUMAN, when: str = MERGED_AT
) -> str:
    """Человек мержит PR: настоящий merge-коммит уезжает в origin."""
    return _merge_into_origin(world.forge, pr, login=login, when=when)


def _merge_into_origin(forge: Forge, pr: int, *, login: str, when: str) -> str:
    rec = forge.prs[pr]
    assert rec["state"] == "OPEN", f"PR #{pr} уже {rec['state']}"
    clone = forge.merger
    assert clone is not None
    _git(clone, "fetch", "-q", "origin")
    _git(clone, "switch", "-q", "master")
    _git(clone, "reset", "-q", "--hard", "origin/master")
    _git(
        clone, "merge", "-q", "--no-ff", f"origin/{rec['branch']}",
        "-m", f"Merge pull request #{pr}",
    )
    sha = _git(clone, "rev-parse", "HEAD")
    _git(clone, "push", "-q", "origin", "master")
    rec.update(
        state="MERGED",
        mergedBy={"login": login},
        mergedAt=when,
        mergeCommit={"oid": sha},
    )
    return sha


def approve(
    world: World, node: str, *, legacy_bundle: int | None = None
) -> an.ApprovalOutcome:
    return an.approve_node(
        world.state, world.ops, node, legacy_bundle=legacy_bundle
    )


def drive_to_approved(
    world: World,
    node: str,
    *,
    login: str = HUMAN,
    legacy_bundle: int | None = None,
) -> None:
    """Провести узел через обе фазы целиком — как это делает оператор.

    Именно вызовами механики, а не подстановкой «уже одобренного» файла:
    следующий шаг обязан работать поверх состояния, которое произвёл
    предыдущий.
    """
    first = approve(world, node, legacy_bundle=legacy_bundle)
    assert first.request is not None
    candidate = world.state.ops[first.request]["candidate_pr"]
    merge_pr(world, candidate, login=login)
    # Второй вызов выносит конверт И мержит его агентом (ADR-ECO-011 D5):
    # заявка завершена тем же вызовом, третьего не нужно.
    approve(world, node, legacy_bundle=legacy_bundle)
    assert world.state.ops[first.request]["status"] == al.STATUS_COMPLETED


def declare_human_merge(world: World) -> None:
    """Репо-политика «Мерж: человек» (CLAUDE.md цели): finalize ждёт человека.

    Не «отказ обвязки», а настройка — та самая, которой ADR-ECO-011 D5
    оставляет человеческий мерж finalize. Файл едет в base через origin
    (untracked он делал бы target грязным — механика отказывает до старта).
    """
    _git(world.human, "fetch", "-q", "origin")
    _git(world.human, "switch", "-q", "master")
    _git(world.human, "reset", "-q", "--hard", "origin/master")
    (world.human / "CLAUDE.md").write_text(
        "# alpha\n\n- Мерж: человек\n", encoding="utf-8"
    )
    _git(world.human, "add", "CLAUDE.md")
    _git(world.human, "commit", "-qm", "policy: Мерж: человек")
    _git(world.human, "push", "-q", "origin", "master")
    world.sync()


def only_request(world: World) -> tuple[str, dict]:
    (nums, op), = al.requests(world.state)
    return al.request_key(*nums), op


# --- Граница `find_pr`: characterization ---------------------------------


def test_absent_pr_only_opens_the_candidate_and_never_buries(
    world: World,
) -> None:
    """`ABSENT` от `find_pr` продвигает ЖИВУЮ заявку и ничего не хоронит.

    Стенд один и тот же на все три исхода — иначе «`ABSENT` не хоронит»
    вакуумно: оно не отличало бы осторожный классификатор от стенда, где
    вообще ничто не хоронит. Здесь стенд доказывает, что хоронить он умеет:
    закрытый без мержа candidate даёт `abandoned`, агентский мерж —
    `invalidated`, а `ABSENT` оставляет заявку живой.
    """
    approve(world, "charter")
    key, op = only_request(world)
    assert op["status"] == al.STATUS_STARTED and al.is_live(op)
    assert op["candidate_pr"] is not None, "ABSENT привёл к созданию PR"

    # Тот же стенд умеет хоронить — обе дороги:
    world.forge.prs[op["candidate_pr"]]["state"] = "CLOSED"
    approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_ABANDONED

    second = approve(world, "charter")
    assert second.request is not None and second.request != key
    merge_pr(world, world.state.ops[second.request]["candidate_pr"], login=AGENT)
    with pytest.raises(RuntimeError, match="invalidated"):
        approve(world, "charter")
    assert world.state.ops[second.request]["status"] == al.STATUS_INVALIDATED


def test_find_pr_failure_keeps_the_request_alive(world: World) -> None:
    """Сбой поиска PR — не «PR нет»: второй PR на ту же ветку не заводится."""
    approve(world, "charter")
    key, _ = only_request(world)
    world.state.ops[key]["candidate_pr"] = None  # крэш-окно: PR ещё не записан
    rs.save(world.state)
    world.forge.mute.add("find_pr")
    with pytest.raises(RuntimeError, match="факт не установлен"):
        approve(world, "charter")
    assert al.is_live(world.state.ops[key])
    assert len(world.forge.prs) == 1, "второй PR не создан"


# --- Фаза 1 -------------------------------------------------------------


def test_phase1_writes_four_values_and_no_signature(world: World) -> None:
    """`approval_pending`, `version + 1`, пины с base, self-hash — и всё."""
    drive_to_approved(world, "charter")
    approve(world, "requirements")
    key, op = _request_over(world, "requirements")
    branch_text = _show(
        world.target, f"{op['branch']}:{BUNDLE}/10-requirements.md"
    )
    meta, _ = split_frontmatter(branch_text)
    assert meta["status"] == na.STATUS_APPROVAL_PENDING
    assert meta["version"] == 2
    assert meta["upstream_hashes"] == {
        "charter": _blob(world, "00-charter.md")
    }
    assert meta[na.SELF_HASH_KEY] == na.self_hash(branch_text)
    assert not meta["approved_by"] and not meta["approved_at"]
    assert op["content_hashes"]["requirements"] == meta[na.SELF_HASH_KEY]
    assert key in world.state.ops


def test_brief_source_pin_survives_candidate_and_finalize(world: World) -> None:
    source_blob = _enable_brief(world)

    first = approve(world, "charter")
    assert first.request is not None
    op = world.state.ops[first.request]
    assert op["upstream_pins"]["charter"] == {
        "discovery-brief": source_blob
    }
    candidate_text = _show(
        world.target, f"{op['branch']}:{BUNDLE}/00-charter.md"
    )
    assert split_frontmatter(candidate_text)[0]["upstream_hashes"] == {
        "discovery-brief": source_blob
    }

    merge_pr(world, op["candidate_pr"])
    approve(world, "charter")   # конверт + агентский мерж finalize
    world.sync()

    assert world.base_meta("00-charter.md")["upstream_hashes"] == {
        "discovery-brief": source_blob
    }
    resolved = bundle_inputs.direct_blobs(
        world.state, world.ops, bundle_dag.BUNDLE_DAG, "charter", "master"
    )
    assert na.node_debt(
        "charter", world.base_text("00-charter.md"), resolved.value or {}
    ) is None


def test_candidate_snapshot_rejects_source_changed_at_head(world: World) -> None:
    _enable_brief(world)
    first = approve(world, "charter")
    op = world.state.ops[first.request]
    source = world.target / BUNDLE / "00-discovery/brief.md"
    source.write_text("changed source\n", encoding="utf-8")
    _git(world.target, "add", "-A")
    _git(world.target, "commit", "-qm", "mutate source")
    changed_head = _git(world.target, "rev-parse", "HEAD")

    assert an._snapshot_is_published(
        world.state, world.ops, bundle_dag.BUNDLE_DAG, op, changed_head
    ) is False


def test_candidate_snapshot_rejects_removed_source_pin(world: World) -> None:
    _enable_brief(world)
    first = approve(world, "charter")
    op = world.state.ops[first.request]
    path = world.target / BUNDLE / "00-charter.md"
    meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
    meta.pop("upstream_hashes")
    path.write_text(join_frontmatter(meta, body), encoding="utf-8")
    _git(world.target, "add", "-A")
    _git(world.target, "commit", "-qm", "remove source pin")
    changed_head = _git(world.target, "rev-parse", "HEAD")

    assert an._snapshot_is_published(
        world.state, world.ops, bundle_dag.BUNDLE_DAG, op, changed_head
    ) is False


def test_candidate_pr_is_ready_and_labelled_at_creation(world: World) -> None:
    """PR обычный, не draft, и метка стоит с рождения (§I12).

    Draft мержем не завершается, а мерж здесь и есть предмет: оставь его
    draft'ом — человек не сможет сделать единственное, ради чего PR создан,
    а реконсиляция будет честно докладывать «ждём человека».
    """
    approve(world, "charter")
    _, op = only_request(world)
    record = world.forge.prs[op["candidate_pr"]]
    assert record["draft"] is False
    assert record["label"] == an.HUMAN_MERGE_LABEL


def test_cascade_is_recursive_and_sweeps_pending_too(world: World) -> None:
    """Каскад идёт вглубь и метит `approval_pending` наравне с `approved`.

    Стенд собран конвейером: три верхних узла одобрены целиком, а у design
    вмержен только candidate — значит В BASE он лежит `approval_pending`
    (статус ветки тут не годится: каскад работает поверх base). К моменту
    переодобрения charter ниже лежат ОБА каскадируемых статуса.
    """
    drive_to_approved(world, "charter")
    drive_to_approved(world, "requirements")
    drive_to_approved(world, "behaviour-spec")
    approve(world, "design")
    merge_pr(world, _request_over(world, "design")[1]["candidate_pr"])
    world.sync()
    assert world.base_meta("20-design.md")["status"] == (
        na.STATUS_APPROVAL_PENDING
    )
    _mutate_body(world, "00-charter.md", "Правленый чартер.")
    approve(world, "charter")
    _, op = _request_over(world, "charter")
    branch = op["branch"]
    after = {
        fname: split_frontmatter(
            _show(world.target, f"{branch}:{BUNDLE}/{fname}")
        )[0]["status"]
        for fname in NODES
    }
    assert after["00-charter.md"] == na.STATUS_APPROVAL_PENDING
    assert after["10-requirements.md"] == na.STATUS_STALE
    assert after["15-behaviour-spec.md"] == na.STATUS_STALE
    assert after["20-design.md"] == na.STATUS_STALE, (
        "approval_pending обязан быть помечен: его предложение посчитано "
        "против блоба, которого после каскада не станет"
    )
    assert after["25-acceptance.md"] == "draft"


def test_node_and_cascade_go_in_one_commit(world: World) -> None:
    """Частичный коммит оставил бы в ветке approved-узел с ложным пином."""
    drive_to_approved(world, "charter")
    drive_to_approved(world, "requirements")
    world.sync()
    _mutate_body(world, "00-charter.md", "Ещё раз правленый чартер.")
    approve(world, "charter")
    _, op = _request_over(world, "charter")
    touched = _git(
        world.target, "show", "--name-only", "--format=", op["head_sha"]
    ).split()
    assert sorted(touched) == sorted(
        [f"{BUNDLE}/00-charter.md", f"{BUNDLE}/10-requirements.md"]
    )


def test_repeat_over_honest_node_is_a_traceless_noop(world: World) -> None:
    """Пункт 4: ни записи, ни PR, ни инкремента версии."""
    drive_to_approved(world, "charter")
    before_prs = set(world.forge.prs)
    before_version = world.base_meta("00-charter.md")["version"]
    outcome = approve(world, "charter")
    assert "no-op" in outcome.message
    assert set(world.forge.prs) == before_prs
    assert world.base_meta("00-charter.md")["version"] == before_version


def test_approved_with_diverged_pins_is_fail_closed(world: World) -> None:
    """Пункт 6: молчаливая перепиновка — и есть дефект spec-runner#410."""
    drive_to_approved(world, "charter")
    drive_to_approved(world, "requirements")
    world.sync()
    _mutate_body(world, "00-charter.md", "Чартер уехал мимо контракта.")
    with pytest.raises(RuntimeError, match="разошедшимися пинами"):
        approve(world, "requirements")


def test_dependent_levels_cannot_share_a_candidate(world: World) -> None:
    """Готовность upstream — по base, и только по нему.

    Пока окончательные байты upstream не в base, его downstream на
    одобрение не выносится: пин, посчитанный по head'у открытой ветки,
    ссылался бы на состояние, которого в base никогда не будет.
    """
    approve(world, "charter")  # candidate открыт, в base charter ещё draft
    with pytest.raises(RuntimeError, match="одним candidate-PR не выносятся"):
        approve(world, "requirements")


def test_two_nodes_of_one_level_share_one_candidate(world: World) -> None:
    """Независимые узлы уровня накапливаются в ОДНОЙ ветке и одном PR (§I12).

    Это единственное, что уменьшает число кругов, и работает оно только
    если второй вызов читает ГОЛОВУ ветки: затри он работу первого —
    в PR остался бы один узел из двух.
    """
    for node in ("charter", "requirements", "behaviour-spec"):
        drive_to_approved(world, node)
    approve(world, "design")
    approve(world, "acceptance")

    key, op = _request_over(world, "design")
    assert op["nodes"] == ["design", "acceptance"]
    assert _request_over(world, "acceptance")[0] == key
    assert len(world.forge.prs) == 7, "второго PR на шаг не заведено"
    branch = op["branch"]
    for fname in ("20-design.md", "25-acceptance.md"):
        meta, _ = split_frontmatter(
            _show(world.target, f"{branch}:{BUNDLE}/{fname}")
        )
        assert meta["status"] == na.STATUS_APPROVAL_PENDING, fname
    merge_pr(world, op["candidate_pr"])
    approve(world, "design")   # конверт + агентский мерж finalize
    world.sync()
    for node, fname in (("design", "20-design.md"), ("acceptance", "25-acceptance.md")):
        assert na.node_debt(
            node,
            world.base_text(fname),
            {
                "requirements": _blob(world, "10-requirements.md"),
                "behaviour-spec": _blob(world, "15-behaviour-spec.md"),
            },
        ) is None, node


def test_unknown_node_id_lists_the_allowed_ones(world: World) -> None:
    with pytest.raises(RuntimeError, match="не входит в активный DAG"):
        approve(world, "spec/20-design.md")


# --- Накопление узлов: два блокера ревью #188 ---------------------------


def _level_three(world: World) -> None:
    """Довести стенд до уровня, где design и acceptance независимы."""
    for node in ("charter", "requirements", "behaviour-spec"):
        drive_to_approved(world, node)


def test_merged_candidate_is_not_joined_but_starts_a_new_attempt(
    world: World,
) -> None:
    """Присоединяться можно, пока candidate ФАКТИЧЕСКИ открыт.

    Леджер отвечает «шаг ещё не сделан», пока фактов мержа нет в записи, —
    и на этом расхождении присоединение стоило человеческого акта: коммит
    уходил в ветку уже вмерженного PR, второго PR не создавалось, в base
    узел не попадал никогда, а следующий вызов хоронил ВСЮ заявку с
    причиной «тело узла правили после мержа» — правки, которой не было.

    Стенд построен именно на этом состоянии: живая заявка с созданным и
    УЖЕ ВМЕРЖЕННЫМ PR, финализация не запускалась.
    """
    _level_three(world)
    approve(world, "design")
    design_key, design_op = _request_over(world, "design")
    merge_pr(world, design_op["candidate_pr"])
    prs_before = set(world.forge.prs)

    approve(world, "acceptance")

    assert world.state.ops[design_key]["nodes"] == ["design"], (
        "снимок вмерженной заявки не дописан"
    )
    acceptance_key, acceptance_op = _request_over(world, "acceptance")
    assert acceptance_key != design_key
    assert acceptance_op["attempt"] == design_op["attempt"] + 1
    assert acceptance_op["step"] == design_op["step"], "тот же уровень"
    assert acceptance_op["branch"] != design_op["branch"]
    assert set(world.forge.prs) - prs_before, "заведён свой PR"
    assert acceptance_op["candidate_pr"] != design_op["candidate_pr"]

    # И обе доводятся до конца: акт над design не сгорел.
    approve(world, "design")
    assert world.state.ops[design_key]["status"] == al.STATUS_COMPLETED
    merge_pr(world, acceptance_op["candidate_pr"])
    approve(world, "acceptance")
    assert world.state.ops[acceptance_key]["status"] == al.STATUS_COMPLETED
    world.sync()
    upstreams = {
        "requirements": _blob(world, "10-requirements.md"),
        "behaviour-spec": _blob(world, "15-behaviour-spec.md"),
    }
    for node, fname in (
        ("design", "20-design.md"), ("acceptance", "25-acceptance.md")
    ):
        assert na.node_debt(node, world.base_text(fname), upstreams) is None


def test_unavailable_candidate_state_blocks_joining_without_writes(
    world: World,
) -> None:
    """Состояние не установлено — отказ БЕЗ записей.

    Присоединиться вслепую значит рискнуть человеческим актом, а завести
    новую заявку вслепую — опубликовать второе mergeable предложение рядом
    с неизвестным первым.
    """
    _level_three(world)
    approve(world, "design")
    before = dict(world.state.ops)
    world.forge.mute.add("pr_facts")

    with pytest.raises(RuntimeError, match="факт не установлен"):
        approve(world, "acceptance")
    assert dict(rs.load("r-approve").ops) == before


def test_resume_republishes_a_joined_node_that_never_reached_the_pr(
    world: World,
) -> None:
    """Возобновление доигрывает НЕВЫПОЛНЕННЫЙ шаг, а не отвечает «ждём».

    Отказ между коммитом и успешным push штатен и возобновляем, но узел
    остаётся в снимке заявки и не попадает в PR. «Ждём мержа» здесь —
    тупик: человек видит один узел из двух, а после мержа заявка хоронится
    целиком, и автоматического выхода нет.

    Стенд — присоединение к ЖИВОЙ заявке с уже созданным PR, а не свежая
    заявка: на свежей возобновление идёт через создание candidate и
    публикует само.
    """
    _level_three(world)
    approve(world, "design")
    key, op = _request_over(world, "design")
    pr = op["candidate_pr"]
    real_push = world.ops.push_branch
    attempts = {"n": 0}

    def flaky(target_dir: str, branch: str) -> None:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("push_branch: rc=1: сеть отвалилась")
        real_push(target_dir, branch)

    world.ops.push_branch = flaky  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="push_branch"):
        approve(world, "acceptance")

    joined = world.state.ops[key]
    assert joined["nodes"] == ["design", "acceptance"], "снимок расширен"
    assert world.forge.head_of(joined["branch"]) != joined["head_sha"], (
        "коммит в опубликованную голову не попал"
    )

    outcome = approve(world, "acceptance")
    assert "допубликовано" in outcome.message
    assert world.forge.head_of(joined["branch"]) == joined["head_sha"]
    assert len(world.forge.prs) == 7, "второго PR не заведено"
    branch_files = _git(
        world.target, "show", "--name-only", "--format=", joined["head_sha"]
    )
    assert "25-acceptance.md" in branch_files

    # Выход есть и он обычный: заявка доводится до конца обоими узлами.
    merge_pr(world, pr)
    approve(world, "design")   # конверт + агентский мерж finalize
    world.sync()
    assert world.state.ops[key]["status"] == al.STATUS_COMPLETED
    upstreams = {
        "requirements": _blob(world, "10-requirements.md"),
        "behaviour-spec": _blob(world, "15-behaviour-spec.md"),
    }
    for node, fname in (
        ("design", "20-design.md"), ("acceptance", "25-acceptance.md")
    ):
        assert na.node_debt(node, world.base_text(fname), upstreams) is None


def test_pr_finished_off_our_commit_invalidates_instead_of_waiting(
    world: World,
) -> None:
    """Пара к предыдущему: тот же разрыв, но PR уже закрыт.

    Доигрывать нечего — предложение заявки человеку не предъявлялось, а
    факт установлен обеими прочитанными величинами. Значит терминальный
    статус с причиной и обычное восстановление, а не вечное «ждём».
    """
    _level_three(world)
    approve(world, "design")
    key, op = _request_over(world, "design")
    real_push = world.ops.push_branch
    attempts = {"n": 0}

    def flaky(target_dir: str, branch: str) -> None:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("push_branch: rc=1: сеть отвалилась")
        real_push(target_dir, branch)

    world.ops.push_branch = flaky  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="push_branch"):
        approve(world, "acceptance")
    world.forge.prs[op["candidate_pr"]]["state"] = "CLOSED"

    with pytest.raises(RuntimeError, match="не на предложении заявки"):
        approve(world, "acceptance")
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED
    assert "лежат не все узлы снимка" in world.state.ops[key]["reason"]


def test_completed_needs_the_envelope_in_base_not_a_merged_pr(
    world: World,
) -> None:
    """Состояние PR — не конверт: `completed` пишется по БАЙТАМ в base."""
    declare_human_merge(world)
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    approve(world, "charter")
    finalize = world.state.ops[key]["finalize_pr"]
    # Форджа говорит «вмержен», а конверта в base нет.
    world.forge.prs[finalize].update(
        state="MERGED", mergedBy={"login": HUMAN}, mergedAt=MERGED_AT,
        mergeCommit={"oid": _stray_commit(world)},
    )

    with pytest.raises(RuntimeError, match="конверта там нет"):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED


def test_join_to_a_request_without_a_commit_carries_both_nodes(
    world: World,
) -> None:
    """Присоединение к заявке БЕЗ коммита выносит весь снимок, не дельту.

    Прежде публикация дописывала только новый узел, и ранее вынесенный в
    PR не попадал вовсе: мерж подписывал половину предложения, а вторая
    половина хоронила заявку на следующем заходе.

    Стенд — заявка, у которой намерение записано, а эффектов не было
    (падение сразу после `start_request`).
    """
    _level_three(world)
    approve(world, "design")
    key, op = _request_over(world, "design")
    world.state.ops[key].update(head_sha=None, candidate_pr=None)
    rs.save(world.state)
    world.forge.prs.clear()
    _git(world.target, "push", "-q", "origin", "--delete", op["branch"])
    world.sync()

    approve(world, "acceptance")

    joined = world.state.ops[key]
    assert joined["nodes"] == ["design", "acceptance"]
    published = _git(
        world.target, "show", "--name-only", "--format=", joined["head_sha"]
    )
    for fname in ("20-design.md", "25-acceptance.md"):
        assert fname in published, f"{fname} не вынесен"
    merge_pr(world, joined["candidate_pr"])
    approve(world, "design")   # конверт + агентский мерж finalize
    world.sync()
    upstreams = {
        "requirements": _blob(world, "10-requirements.md"),
        "behaviour-spec": _blob(world, "15-behaviour-spec.md"),
    }
    for node, fname in (
        ("design", "20-design.md"), ("acceptance", "25-acceptance.md")
    ):
        assert na.node_debt(node, world.base_text(fname), upstreams) is None


def test_node_that_never_reached_a_commit_is_still_published(
    world: World,
) -> None:
    """Признак «опубликовано» спрашивает СОСТАВ, а не голову.

    Узел, дошедший до снимка и не дошедший до коммита, оставляет голову PR
    прежней — нашей же. Сверка по `head_sha` объявляла бы предложение
    опубликованным, и узел не попал бы в PR НИКОГДА: ни одного вызова,
    который бы это заметил, не оставалось.
    """
    _level_three(world)
    approve(world, "design")
    key, op = _request_over(world, "design")
    head_before = op["head_sha"]
    real_commit = world.ops.commit_paths
    attempts = {"n": 0}

    def flaky(target_dir: str, paths: list[str], message: str) -> None:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("commit_paths: диск кончился")
        real_commit(target_dir, paths, message)

    world.ops.commit_paths = flaky  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="commit_paths"):
        approve(world, "acceptance")
    # Упавший заход оставил правки незакоммиченными; оператор снимает их
    # по процедуре из dirty-гарда — заявка приведёт ветку к снимку заново.
    _git(world.target, "checkout", "--", ".")

    joined = world.state.ops[key]
    assert joined["nodes"] == ["design", "acceptance"], "снимок расширен"
    assert joined["head_sha"] == head_before, "коммита не было"
    assert world.forge.head_of(joined["branch"]) == head_before, (
        "голова PR совпадает с записанной — по ней расхождения не видно"
    )

    outcome = approve(world, "acceptance")
    assert "допубликовано" in outcome.message
    published = _git(
        world.target,
        "show",
        "--name-only",
        "--format=",
        world.state.ops[key]["head_sha"],
    )
    assert "25-acceptance.md" in published
    assert len(world.forge.prs) == 7, "второго PR не заведено"


def test_publishing_twice_changes_nothing(world: World) -> None:
    """Приведение ветки к снимку идемпотентно: второй заход не коммитит.

    Стенд — ПОВТОРНАЯ публикация (крэш-окно «PR создан, номер не
    записан»), а не повтор над открытым candidate: только там приведение
    выполняется дважды. Без этой пары «доводит до снимка» не отличалось бы
    от «дописывает каждый раз», а лишний коммит менял бы блоб узла и плодил
    бы долг у downstream на ровном месте.

    Отсюда же видно, зачем выносимые байты считаются от BASE: считай их от
    ветки, второй заход поднял бы `version` ещё раз и получил бы другие
    байты на том же снимке.
    """
    _level_three(world)
    approve(world, "design")
    key, op = _request_over(world, "design")
    head_before = op["head_sha"]
    existing = op["candidate_pr"]
    world.state.ops[key]["candidate_pr"] = None
    rs.save(world.state)

    approve(world, "design")
    assert world.state.ops[key]["head_sha"] == head_before, "нового коммита нет"
    assert world.state.ops[key]["candidate_pr"] == existing
    assert len(world.forge.prs) == 7


@pytest.mark.parametrize("stage", ["candidate", "finalize"])
def test_pending_upstream_diagnostics_names_the_awaited_pr(
    world: World, stage: str
) -> None:
    """Отказ называет ДЕЙСТВИЕ, которого система ждёт — на обоих шагах.

    У заявки два PR, и ждут они по очереди: пока candidate открыт, ждут
    его мержа; после мержа candidate ждут мержа КОНВЕРТА. Назови
    диагностика candidate на втором шаге — оператор уйдёт на вмерженный PR
    и вернётся ни с чем, то есть получит ровно тот лишний круг, ради
    устранения которого номер PR в процедуру и попал (issue #189).

    Прежняя редакция теста фиксировала как раз неверную процедуру: стенд
    мержил candidate, а ожидание сверялось с его же номером — и докстринг
    утверждал про открытый PR то, чего в стенде уже не было.
    """
    drive_to_approved(world, "charter")
    declare_human_merge(world)
    approve(world, "requirements")
    key, op = _request_over(world, "requirements")
    if stage == "candidate":
        expected = op["candidate_pr"]
    else:
        merge_pr(world, op["candidate_pr"])
        approve(world, "requirements")   # конверт вынесен, в base ещё pending
        expected = world.state.ops[key]["finalize_pr"]
        assert expected != op["candidate_pr"]
    assert (
        world.forge.prs[expected]["state"] == "OPEN"
    ), "ждут именно этого PR"

    with pytest.raises(RuntimeError) as failure:
        approve(world, "behaviour-spec")
    message = str(failure.value)
    assert f"#{expected}" in message, "назван не тот PR"
    assert "authorized_approver_accounts" in message
    assert "--approve-node" not in message, (
        "оператора не отправляют за тем, чего система не ждёт"
    )


def test_dropped_node_makes_the_request_unexecutable_with_a_way_out(
    world: World,
) -> None:
    """Узел заявки выпал из активного DAG — заявка хоронится, выход есть.

    Correction удалил файл узла из бандла; продолжать заявку нечем. Раньше
    здесь вылетал голый `KeyError` из `_filename` — трассировка без
    диагноза и без выхода (issue #190).

    Терминализация журнальная: вызов по самому выпавшему узлу получает
    свой отказ — состав DAG его не содержит, и перечень допустимых id
    назван. Выход — обычный новый candidate по актуальному составу, и он
    тут же проверяется.
    """
    _level_three(world)
    approve(world, "design")
    approve(world, "acceptance")
    key, op = _request_over(world, "design")
    assert op["nodes"] == ["design", "acceptance"]
    _drop_node(world, "25-acceptance.md")

    with pytest.raises(RuntimeError, match="не входит в активный DAG"):
        approve(world, "acceptance", legacy_bundle=5)
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED
    assert "выпали из состава" in world.state.ops[key]["reason"]

    # Выход исполним: следующий вызов заводит проход по новому составу.
    approve(world, "design", legacy_bundle=5)
    assert al.wave_records(world.state)[1]["status"] == al.WAVE_OBSOLETE
    fresh_key, fresh = _request_over(world, "design")
    assert fresh_key != key and fresh["wave"] == 2
    assert fresh["nodes"] == ["design"]
    assert fresh["candidate_pr"] is not None


def test_dropped_node_terminalizes_on_composition_not_on_the_close(
    world: World,
) -> None:
    """Хоронит СОСТАВ, а не результат закрытия — это разные величины.

    Состав установлен положительно: он сверен с фактическим каталогом
    бандла в base. Исход `close_pr` свёрнут (#177: `False` и при
    отсутствии прав, и когда уже закрыт) и права терминализовать не имеет
    — и не имеет его здесь ни в какую сторону: заявка уже похоронена
    установленным фактом, а неподтверждённое закрытие лишь не даёт вызову
    доложить об успехе.

    Порядок «запись durable раньше сетевого эффекта» — тот же, что у
    инвалидации заявок ниже по течению (§I12): только запись объясняет
    закрытие.

    Тупика нет, и ЭТА половина теста — про то, почему (major третьего
    круга ревью #191). Раньше повтор обходил только живых, похороненная
    запись из обхода выпадала, и закрытие доводить было нечем: вызов по
    её узлам отказывал на составе, `_update` терминальную запись не
    мутирует, а мержаемый PR оставался открытым молча. Теперь обход
    находит свою похороненную запись по маркеру `invalidated_by` и
    доводит закрытие САМ — без руки оператора и независимо от того, по
    какому узлу пришёл вызов.
    """
    _level_three(world)
    approve(world, "design")
    approve(world, "acceptance")
    key, op = _request_over(world, "design")
    _drop_node(world, "25-acceptance.md")
    world.forge.close_confirms = False

    with pytest.raises(RuntimeError, match="закрытие PR"):
        approve(world, "design", legacy_bundle=5)
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED
    assert "выпали из состава" in world.state.ops[key]["reason"]
    assert world.forge.prs[op["candidate_pr"]]["state"] == "OPEN"

    # Повтор, пока закрытие по-прежнему не подтверждается: вызов снова
    # берётся ЗА ТО ЖЕ закрытие и снова отказывает честно. Проверяется
    # именно это — что путь к недоведённому эффекту существует, а не что
    # оператору называют чужую процедуру.
    with pytest.raises(RuntimeError, match="закрытие PR") as retry:
        approve(world, "design", legacy_bundle=5)
    assert str(op["candidate_pr"]) in str(retry.value)
    assert world.forge.prs[op["candidate_pr"]]["state"] == "OPEN"

    # Закрытие подтвердилось — обход доводит эффект САМ. Руками состояние
    # PR здесь не правится намеренно: правка руками спрятала бы ровно ту
    # работу, ради которой major и заведён.
    world.forge.close_confirms = True
    approve(world, "design", legacy_bundle=5)
    assert world.forge.prs[op["candidate_pr"]]["state"] == "CLOSED"
    assert _request_over(world, "design")[1]["wave"] == 2


def test_request_with_all_nodes_dropped_is_reachable_and_settled(
    world: World,
) -> None:
    """Заявка, у которой выпали ВСЕ узлы, достижима и хоронится.

    Вторая половина #190, и из пяти хвостов она была опаснее прочих:
    такая заявка не встречалась ни одному вызову — по её узлам приходил
    отказ «нет в активном DAG», по соседним вызов уходил в предложение, —
    и оставалась живой навсегда, держа открытым МЕРЖАЕМЫЙ candidate-PR.
    Человеческий мерж вернул бы удалённый файл в base.

    Стенд: заявка ровно над выпавшим узлом, вызов — по СОСЕДНЕМУ узлу,
    который в составе есть. Прежняя проверка внутри продвижения на этот
    вызов не смотрела вовсе.
    """
    _level_three(world)
    approve(world, "acceptance")
    doomed_key, doomed = _request_over(world, "acceptance")
    assert doomed["nodes"] == ["acceptance"], "все узлы заявки — выпадут"
    pr = doomed["candidate_pr"]
    _drop_node(world, "25-acceptance.md")

    # Вызов по ДРУГОМУ узлу: прежде он уходил в предложение и о заявке не
    # вспоминал.
    approve(world, "design", legacy_bundle=5)

    settled = world.state.ops[doomed_key]
    assert settled["status"] == al.STATUS_INVALIDATED
    assert "выпали из состава" in settled["reason"]
    assert world.forge.prs[pr]["state"] == "CLOSED", (
        "мержаемое предложение снято со стола"
    )
    # Текст закрытия называет ТУ причину, что записана в журнале
    # (issue #193). Прежний был зашит под другое погребение и утверждал,
    # будто upstream выносится на одобрение заново, а заявку сняла она
    # сама: здесь upstream никто не выносит и байты не «прежние» — узла
    # в бандле нет вовсе.
    comment = world.forge.prs[pr]["closed_with"]
    assert settled["reason"] in comment
    assert "выпали из состава" in comment
    assert "upstream выносится на одобрение заново" not in comment


def test_buried_request_with_all_nodes_dropped_gets_its_close_finished(
    world: World,
) -> None:
    """Третий заход на ту же болезнь — теперь класс, а не случай.

    Худшая комбинация двух прежних хвостов: у заявки выпали ВСЕ узлы
    (значит вызова по её узлам не будет никогда — он отказывает на
    составе) И закрытие её PR не подтвердилось (значит запись уже
    терминальна, `is_live` False, а `_update` её не мутирует). Раньше
    сходились три отказа сразу, и мержаемый candidate оставался открытым
    навсегда, без единого пути его закрыть и без единого слова об этом.

    Инвариант, который тест держит: у терминализованной записи, чей
    сетевой эффект мог не состояться, есть путь довести эффект на
    повторе — и путь не зависит от того, по какому узлу пришёл вызов.
    Здесь оба вызова приходят по ЧУЖОМУ узлу, и этого достаточно.
    """
    _level_three(world)
    approve(world, "acceptance")
    doomed_key, doomed = _request_over(world, "acceptance")
    assert doomed["nodes"] == ["acceptance"], "все узлы заявки — выпадут"
    pr = doomed["candidate_pr"]
    _drop_node(world, "25-acceptance.md")
    world.forge.close_confirms = False

    # Заход 1: запись хоронится, закрытие не подтверждается, вызов честно
    # отказывает. Предложение остаётся на столе — и это ещё не дефект.
    with pytest.raises(RuntimeError, match="закрытие PR"):
        approve(world, "design", legacy_bundle=5)
    buried = world.state.ops[doomed_key]
    assert buried["status"] == al.STATUS_INVALIDATED
    assert buried["invalidated_by"] == an._OUTSIDE_DAG, (
        "своя похоронная операция помечена — по этому маркеру её и найдут"
    )
    assert not al.is_live(buried), "запись терминальна, обход живых её не даст"
    assert world.forge.prs[pr]["state"] == "OPEN"

    # Заход 2 — дефект был ЗДЕСЬ: обход по живым эту запись не отдавал,
    # и закрытие доводить было нечем. Теперь отдаёт, и оно доводится.
    world.forge.close_confirms = True
    approve(world, "design", legacy_bundle=5)
    assert world.forge.prs[pr]["state"] == "CLOSED", (
        "мержаемое предложение снято со стола на повторе"
    )
    assert world.state.ops[doomed_key]["status"] == al.STATUS_INVALIDATED, (
        "запись не переписана — доводился эффект, а не решение"
    )


# --- Крэш-окна ----------------------------------------------------------


def test_crash_before_commit_redoes_phase1_on_the_same_request(
    world: World,
) -> None:
    """Заявка есть, коммита нет: повтор доигрывает фазу 1, не заводя новую."""
    approve(world, "charter")
    key, op = only_request(world)
    # Откат к состоянию «намерение записано, эффектов не было».
    world.state.ops[key].update(head_sha=None, candidate_pr=None)
    rs.save(world.state)
    world.forge.prs.clear()
    _git(world.target, "push", "-q", "origin", "--delete", op["branch"])
    world.sync()

    approve(world, "charter")
    assert [nums for nums, _ in al.requests(world.state)] == [(1, 0, 1)]
    again = world.state.ops[key]
    assert again["head_sha"] is not None and again["candidate_pr"] is not None


def test_crash_after_commit_adopts_the_pr_by_recorded_head(
    world: World,
) -> None:
    """PR усыновляется по записанному `head_sha`, а не по имени ветки."""
    approve(world, "charter")
    key, op = only_request(world)
    existing = op["candidate_pr"]
    world.state.ops[key]["candidate_pr"] = None  # номер записать не успели
    rs.save(world.state)

    approve(world, "charter")
    assert world.state.ops[key]["candidate_pr"] == existing
    assert len(world.forge.prs) == 1, "второго PR не создано"


def test_foreign_push_under_our_branch_name_is_fail_closed(
    world: World,
) -> None:
    """Под нашим именем чужая работа — публикация не состоится.

    Первым срабатывает не сверка head'а, а сам push: разошедшуюся ветку
    origin не принимает как non-ff. Это и есть наблюдаемое поведение, и
    тест закрепляет его, а не желаемое; отдельная сверка head'а —
    следующий тест.
    """
    approve(world, "charter")
    key, op = only_request(world)
    world.state.ops[key]["candidate_pr"] = None
    rs.save(world.state)
    world.forge.prs.clear()
    _push_foreign_commit(world, op["branch"])

    with pytest.raises(RuntimeError, match="non-fast-forward"):
        approve(world, "charter")
    assert al.is_live(world.state.ops[key]), "заявка не тронута"
    assert not world.forge.prs, "PR под чужой работой не заведён"


def test_pr_with_a_different_head_is_not_adopted(world: World) -> None:
    """Усыновление — по записанному `head_sha`, а не по имени ветки.

    Проверяется сама сверка: PR на ветке есть, его head — не наш. Имя
    говорит, где смотреть; идентичность устанавливает запись.
    """
    world.forge.prs[500] = {
        "branch": "master", "title": "чужой", "body": "",
        "label": "", "draft": False, "state": "OPEN",
    }
    with pytest.raises(RuntimeError, match="чужая работа"):
        an._adopt_or_create_pr(
            world.state, world.ops, "master", "dead" * 10, "t", "b"
        )
    assert len(world.forge.prs) == 1, "второго PR не создано"


def test_repeat_over_open_candidate_waits_and_creates_nothing(
    world: World,
) -> None:
    approve(world, "charter")
    key, op = only_request(world)
    outcome = approve(world, "charter")
    assert "ждём мержа" in outcome.message
    assert len(world.forge.prs) == 1
    assert world.state.ops[key]["finalize_pr"] is None


def test_crash_between_merge_and_finalize_resumes_the_same_request(
    world: World,
) -> None:
    """Вмерженный candidate заявку не закрывает: следующий шаг — финализация."""
    declare_human_merge(world)
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    approve(world, "charter")
    assert world.state.ops[key]["finalize_pr"] is not None
    assert al.is_live(world.state.ops[key])
    # Повтор при открытом финализирующем PR второго не заводит.
    outcome = approve(world, "charter")
    assert "жива всегда" in outcome.message
    assert len(world.forge.prs) == 2


def test_finalize_pr_is_adopted_by_its_own_head(world: World) -> None:
    """У конверта свой коммит и свой записанный head — одно поле не может
    назвать два разных коммита."""
    declare_human_merge(world)
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    approve(world, "charter")
    recorded = world.state.ops[key]
    assert recorded["finalize_head_sha"] not in (None, recorded["head_sha"])
    existing = recorded["finalize_pr"]
    world.state.ops[key]["finalize_pr"] = None
    rs.save(world.state)
    approve(world, "charter")
    assert world.state.ops[key]["finalize_pr"] == existing


# --- Фаза 3: конверт и сверки -------------------------------------------


def test_finalize_writes_only_the_envelope(world: World) -> None:
    """Ни тела, ни пинов, ни self-hash, ни `version` фаза 3 не касается."""
    drive_to_approved(world, "charter")
    approve(world, "requirements")
    key, op = _request_over(world, "requirements")
    merge_pr(world, op["candidate_pr"])
    world.sync()  # снимок берётся с ФАКТИЧЕСКОГО base, а не с протухшего клона
    before = split_frontmatter(world.base_text("10-requirements.md"))
    approve(world, "requirements")   # конверт + агентский мерж finalize
    assert world.state.ops[key]["status"] == al.STATUS_COMPLETED
    world.sync()
    after_text = world.base_text("10-requirements.md")
    after_meta, after_body = split_frontmatter(after_text)

    assert after_meta["status"] == na.STATUS_APPROVED
    assert after_meta["approved_by"] == HUMAN
    assert after_meta["approved_at"] == MERGED_AT
    assert after_meta["version"] == before[0]["version"]
    assert after_meta["upstream_hashes"] == before[0]["upstream_hashes"]
    assert after_meta[na.SELF_HASH_KEY] == before[0][na.SELF_HASH_KEY]
    assert after_body == before[1]
    assert na.node_debt(
        "requirements",
        after_text,
        {"charter": _blob(world, "00-charter.md")},
    ) is None


def test_agent_merge_invalidates_and_names_the_allowlist(
    world: World,
) -> None:
    """Мерж вне allowlist — установленный факт: подписи он не создаёт."""
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"], login=AGENT)
    with pytest.raises(RuntimeError, match=af.APPROVER_ALLOWLIST_ENV):
        approve(world, "charter")
    record = world.state.ops[key]
    assert record["status"] == al.STATUS_INVALIDATED
    assert AGENT in record["reason"]
    assert world.base_meta("00-charter.md")["status"] == (
        na.STATUS_APPROVAL_PENDING
    ), "конверт не поставлен"


def test_body_edited_after_the_merge_invalidates(world: World) -> None:
    """Сверка пересчётом ловит правку ТЕЛА узла после мержа (T2).

    Поле сравнивается с ПЕРЕСЧЁТОМ по фактическим байтам, а не с самим
    собой: правку тела запись заявки не замечает по построению.
    """
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    _push_body_edit(world, "00-charter.md", "Тело подменили после мержа.")
    with pytest.raises(RuntimeError, match="сверка фазы 3 не сошлась"):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED
    assert "вмержены не те байты" in world.state.ops[key]["reason"]


def test_body_edited_with_a_refreshed_field_still_invalidates(
    world: World,
) -> None:
    """Первое из двух сравнений self-hash: те ли байты вмержены (T2).

    Правку тела ВМЕСТЕ с обновлением поля вторая сверка («поле равно
    пересчёту») не замечает по построению — поле и пересчёт согласованы.
    Ловит её только сравнение пересчёта с тем, что вынесла ЗАЯВКА: вопрос
    «вмержены ли те байты, которые человек видел в PR», отвечается снимком,
    а не файлом.
    """
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    _push_edit(world, "00-charter.md", body="Подменённое тело.", refresh=True)
    with pytest.raises(RuntimeError, match="сверка фазы 3 не сошлась"):
        approve(world, "charter")
    reason = world.state.ops[key]["reason"]
    assert "вмержены не те байты" in reason
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED


def test_pins_repaired_after_the_merge_still_invalidate(world: World) -> None:
    """Второе из двух сравнений пинов: их правку после мержа ловит снимок.

    Апстрим уехал, а пины «починили» под него — сравнение с ФАКТИЧЕСКИМИ
    блобами сходится, и одно оно пропустило бы одобрение, посчитанное по
    другим байтам. Расхождение со снимком заявки его и ловит.
    """
    drive_to_approved(world, "charter")
    approve(world, "requirements")
    key, op = _request_over(world, "requirements")
    merge_pr(world, op["candidate_pr"])
    _push_edit(world, "00-charter.md", body="Апстрим уехал.")
    _repin(world, "10-requirements.md", "charter", "00-charter.md")

    with pytest.raises(RuntimeError, match="сверка фазы 3 не сошлась"):
        approve(world, "requirements")
    assert "снимка заявки" in world.state.ops[key]["reason"]


def test_phase1_refuses_bytes_other_than_the_request_carried(
    world: World,
) -> None:
    """Страховка фазы 1: на ветке обязаны лежать те байты, что вынесены.

    Ветка заявки начинается от base, поэтому в норме расхождению взяться
    неоткуда — но правило «не публиковать то, чего заявка не выносила»
    обязано быть механическим, а не выводимым из рассуждения.
    """
    approve(world, "charter")
    key, op = only_request(world)
    world.state.ops[key].update(
        head_sha=None, candidate_pr=None, content_hashes={"charter": "beef" * 10}
    )
    rs.save(world.state)
    with pytest.raises(RuntimeError, match="не те, что вынесла заявка"):
        approve(world, "charter")


def test_upstream_moved_between_phases_invalidates(world: World) -> None:
    """Уехавший между фазами upstream — заявка устарела (T3)."""
    drive_to_approved(world, "charter")
    approve(world, "requirements")
    key, op = _request_over(world, "requirements")
    merge_pr(world, op["candidate_pr"])
    _push_body_edit(world, "00-charter.md", "Апстрим уехал вперёд.")
    with pytest.raises(RuntimeError, match="сверка фазы 3 не сошлась"):
        approve(world, "requirements")
    reason = world.state.ops[key]["reason"]
    assert "пины" in reason and "фактических блобов upstream" in reason


def test_merge_commit_outside_base_history_invalidates(world: World) -> None:
    """Merge-коммит обязан быть В ИСТОРИИ base — иначе подписывать нечего.

    Коммит здесь ДОСТУПЕН клону (иначе исход был бы «установить не
    удалось», и заявку хоронить было бы нельзя) и при этом не является
    предком master: `git merge-base --is-ancestor` различает эти два случая
    сам, и различие перенесено в тип.
    """
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    stray = _stray_commit(world)
    world.forge.prs[op["candidate_pr"]]["mergeCommit"] = {"oid": stray}
    with pytest.raises(RuntimeError, match="не в истории master"):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED
    assert "отсутствует в истории" in world.state.ops[key]["reason"]


@pytest.mark.parametrize("field", ["mergedBy", "mergedAt", "mergeCommit"])
def test_merge_identity_diverged_on_resume_invalidates(
    world: World, field: str
) -> None:
    """Сверка идентичности — про ВОЗОБНОВЛЕНИЕ, и там она не тождество.

    Факты мержа записаны прошлым запуском, а читаются заново. Расходиться
    может любая из ТРЁХ величин, и каждая проверяется отдельно: сверь мы
    только логин, подменённый merge-коммит прошёл бы насквозь, а именно он
    отвечает на вопрос «тот ли это акт».
    """
    declare_human_merge(world)
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    world.forge.mute.add("pr_facts")
    with pytest.raises(RuntimeError, match="факт не установлен"):
        approve(world, "charter")  # факты мержа ещё не записаны
    world.forge.mute.clear()
    approve(world, "charter")      # записаны, финализирующий PR создан
    world.state.ops[key]["finalize_pr"] = None   # крэш-окно до записи номера
    rs.save(world.state)
    world.forge.prs[op["candidate_pr"]][field] = {
        "mergedBy": {"login": "someone-else"},
        "mergedAt": "2026-01-01T00:00:00Z",
        "mergeCommit": {"oid": _stray_commit(world)},
    }[field]

    with pytest.raises(RuntimeError, match="не совпали с записанными"):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED


def test_self_hash_field_tampered_after_the_merge_invalidates(
    world: World,
) -> None:
    """Второе из двух сравнений self-hash: пройдёт ли узел предикат.

    Правку САМОГО ПОЛЯ первое сравнение не замечает по построению: поле
    вырезано из проекции, значит пересчёт по байтам не меняется и со
    снимком заявки сходится. Ловит её только сверка поля с пересчётом — без
    неё конверт лёг бы на узел, который потом не проходит предикат.
    """
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    _tamper_field(world, "00-charter.md", na.SELF_HASH_KEY, "beef" * 10)

    with pytest.raises(RuntimeError, match="сверка фазы 3 не сошлась"):
        approve(world, "charter")
    assert "тело узла правили после мержа" in world.state.ops[key]["reason"]
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED


def test_closed_finalize_pr_invalidates(world: World) -> None:
    declare_human_merge(world)
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    approve(world, "charter")
    world.forge.prs[world.state.ops[key]["finalize_pr"]]["state"] = "CLOSED"
    with pytest.raises(RuntimeError, match="закрыт без мержа"):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED


@pytest.mark.parametrize(
    "what",
    ["pr_facts", "show_file", "is_ancestor", "merge_event"],
)
def test_unresolved_fact_never_buries_the_request(
    world: World, what: str
) -> None:
    """Неустановленный факт — отказ с сохранением ЖИВОЙ заявки.

    По источнику неизвестности на каждую сверку фазы 3: факты PR (номер и
    состояние), байты узла в base (self-hash и пины считаются по ним),
    принадлежность merge-коммита истории base, полнота самого события
    мержа (из него берётся личность). Ни один не вправе похоронить заявку.

    Пара к этому тесту — соседние: те же сверки на УСТАНОВЛЕННОМ факте
    дают `invalidated`, то есть стенд умеет хоронить, и молчание здесь
    означает осторожность, а не бессилие.
    """
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    if what == "is_ancestor":
        world.ops.is_ancestor = lambda *a, **k: None  # type: ignore[method-assign]
    elif what == "show_file":
        world.ops.show_file = lambda *a, **k: None  # type: ignore[method-assign]
    elif what == "merge_event":
        # PR вмержен, но личности в ответе нет: событие неполно, значит
        # ни подписать узел, ни быть сверенным оно не может.
        world.forge.prs[op["candidate_pr"]]["mergedBy"] = None
    else:
        world.forge.mute.add(what)
    with pytest.raises(RuntimeError, match="факт не установлен"):
        approve(world, "charter")
    assert al.is_live(world.state.ops[key]), "заявка сохранена"
    assert world.state.ops[key]["reason"] is None, "причины нет — не хоронили"
    assert world.state.ops[key]["status"] == al.STATUS_STARTED


# --- Решение об авторизации: один раз, с отпечатком политики ------------


def test_authorization_decision_is_recorded_with_the_policy(
    world: World,
) -> None:
    """Вместе с фактами мержа пишется, ПО КАКОЙ политике он авторизован."""
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    approve(world, "charter")
    auth = rs.load("r-approve").ops[key]["authorization"]
    assert auth["login"] == HUMAN
    assert auth["policy"] == af.policy_fingerprint()
    assert auth["source"] == af.APPROVER_ALLOWLIST_ENV


def test_policy_change_does_not_reauthorize_the_past(
    world: World, monkeypatch
) -> None:
    """Фаза 3 сверяет ЦЕЛОСТНОСТЬ решения, а не применяет allowlist заново.

    Список сужается до пустого между записью решения и финализацией — то
    есть настолько, что подписать не может никто. Заявка, честно
    классифицированная раньше, обязана дойти: иначе правка конфигурации
    убивала бы прошлое, а ровно от этого §I12 сделал решение записанным.
    """
    declare_human_merge(world)
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    approve(world, "charter")                    # решение записано
    world.state.ops[key]["finalize_pr"] = None   # крэш-окно до записи номера
    rs.save(world.state)
    monkeypatch.setenv(af.APPROVER_ALLOWLIST_ENV, "")
    assert af.approver_allowlist() == frozenset()

    approve(world, "charter")                    # возобновление
    assert world.state.ops[key]["finalize_pr"] is not None
    assert al.is_live(world.state.ops[key])
    merge_pr(world, world.state.ops[key]["finalize_pr"])
    approve(world, "charter")
    world.sync()
    assert na.node_debt("charter", world.base_text("00-charter.md"), {}) is None


@pytest.mark.parametrize(
    ("broken", "why"),
    [
        (None, "решения нет вовсе"),
        (
            {"login": "someone-else", "policy": "v1:0", "source": "X"},
            "решение про другой мерж",
        ),
        (
            {"login": HUMAN, "policy": "", "source": "X"},
            "решение без политики — непроверяемо",
        ),
    ],
)
def test_broken_authorization_record_is_not_accepted(
    world: World, broken: dict | None, why: str
) -> None:
    """Целостность: записанное решение обязано быть про ЭТОТ мерж и полным.

    Пара к предыдущему тесту. Не перепроверять allowlist можно ровно
    потому, что решение записано и ПРОВЕРЯЕМО; каждая из трёх поломок
    делает его непроверяемым по-своему, и ни одна не должна проходить.
    """
    declare_human_merge(world)
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    approve(world, "charter")
    world.state.ops[key]["finalize_pr"] = None
    world.state.ops[key]["authorization"] = broken
    rs.save(world.state)

    with pytest.raises(RuntimeError, match="целого решения об авторизации"):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED, why


# --- Волна: один проход по DAG ------------------------------------------


def test_wave_is_opened_with_its_intent(world: World) -> None:
    """Проход открывается ЗАПИСЬЮ с составом DAG, а не по пустому месту."""
    approve(world, "charter")
    record = rs.load("r-approve").ops["approve-wave-1"]
    nodes = bundle_dag.composition(bundle_dag.dag_for(None))
    assert record["status"] == al.WAVE_OPEN
    assert record["intent"]["dag"] == list(nodes)
    assert record["intent"]["fingerprint"] == (
        bundle_dag.composition_fingerprint(nodes)
    )
    assert _request_over(world, "charter")[1]["wave"] == 1


def test_wave_outlives_the_gap_between_levels(world: World) -> None:
    """Между вмерженным уровнем и заведённым следующим живой заявки нет —
    и волна обязана это пережить.

    Это штатная середина прохода, через неё идёт каждый уровень DAG.
    Закройся волна здесь, `K` не рос бы никогда, а `A` не рос бы вовсе.
    """
    drive_to_approved(world, "charter")
    assert al.live_requests(world.state) == [], "живых заявок нет"
    assert al.open_wave(world.state) == 1, "волна жива: проход не завершён"

    approve(world, "requirements")
    _, second = _request_over(world, "requirements")
    assert second["wave"] == 1, "тот же проход"
    assert second["step"] == 1, "K вырос по уровню"


def test_approve_node_never_writes_completed(world: World) -> None:
    """`--approve-node` волну не завершает — даже когда DAG стал одобрен.

    Последнего вызова в предписанном цикле не существует: после мержа
    финализирующего PR последнего уровня оператор уходит в доставку.
    Поэтому `completed` пишет реконсиляция после сошедшегося гейта, а не
    этот путь.
    """
    for node in ("charter", "requirements", "behaviour-spec", "design",
                 "acceptance", "decomposition"):
        drive_to_approved(world, node)
    record = rs.load("r-approve").ops["approve-wave-1"]
    assert record["status"] == al.WAVE_OPEN, (
        "весь DAG одобрен, но закрывать волну этому вызову не поручено"
    )


def _dag_state(world: World, legacy: int | None = None) -> an.DagState:
    return an.read_dag_state(
        world.state, world.ops, bundle_dag.dag_for(legacy)
    )


def _reconcile(world: World, legacy: int | None = None) -> str | None:
    """Путь части 3 целиком: гейт даёт свидетельство, оно идёт в переход.

    Свидетельство получается ТОЛЬКО из сошедшегося предиката — обойти это
    в тесте так же нельзя, как в доставке.
    """
    verdict = _dag_state(world, legacy)
    assert verdict.evidence is not None, (
        f"гейт не сошёлся: debts={[d.node_id for d in verdict.debts]}, "
        f"unresolved={verdict.unresolved!r}"
    )
    return an.reconcile_wave_after_approved_dag(world.state, verdict.evidence)


def test_reconciliation_needs_evidence_not_a_reason_to_recompute() -> None:
    """`UNAVAILABLE` в переход не передаётся — это выражено СИГНАТУРОЙ.

    У функции нет ни `ops`, ни `dag`: она физически не может сходить в
    сеть, применить предикат заново или вывести одобренность по-своему.
    Проверка внутри была бы слабее — её можно обойти, забыв позвать.
    """
    params = inspect.signature(
        an.reconcile_wave_after_approved_dag
    ).parameters
    assert list(params) == ["state", "approved"]
    assert params["approved"].annotation in ("ApprovedDag", an.ApprovedDag)


def test_gate_predicate_yields_evidence_only_when_it_converges(
    world: World,
) -> None:
    """Свидетельство даёт ТОЛЬКО сошедшийся предикат по всему DAG.

    Три исхода на одном стенде, иначе «нет свидетельства» не отличает
    строгий предикат от сломанного: долг у узла → долги названы,
    неустановленный факт → третий исход, всё одобрено → свидетельство.
    """
    for node in ("charter", "requirements", "behaviour-spec", "design",
                 "acceptance", "decomposition"):
        drive_to_approved(world, node)
    _push_edit(world, "30-decomposition.md", body="Долг у терминального узла.")
    in_debt = _dag_state(world)
    assert in_debt.evidence is None
    assert [d.node_id for d in in_debt.debts] == ["decomposition"]
    assert not in_debt.unresolved

    world.ops.show_file = lambda *a, **k: None  # type: ignore[method-assign]
    unknown = _dag_state(world)
    assert unknown.evidence is None and unknown.debts == ()
    assert "#177" in unknown.unresolved
    del world.ops.show_file

    drive_to_approved(world, "decomposition")
    green = _dag_state(world)
    assert green.evidence is not None
    assert green.debts == () and not green.unresolved
    assert green.evidence.nodes == bundle_dag.composition(
        bundle_dag.dag_for(None)
    )


def test_predicate_sees_nodes_outside_the_terminal_closure(
    world: World,
) -> None:
    """Предикат идёт по КАЖДОМУ узлу состава, а не по терминальному уровню.

    На боевом бандле разница не видна: там все шесть узлов — предки
    decomposition, и долг любого из них ломает пины терминального. Но
    активный DAG не цепочка, и контракт требует свойство именно про узел
    ВНЕ транзитивного замыкания терминального — иначе проход объявлялся бы
    состоявшимся, оставив в DAG узел, до которого очередь не дошла.

    Форма графа здесь и есть предмет теста, поэтому состав объявляется
    другой: `acceptance` — корень, не лежащий ни на одном пути к
    терминальному `requirements`. Байты при этом настоящие и произведены
    конвейером, а не выложены руками.
    """
    for node in ("charter", "requirements", "behaviour-spec", "acceptance"):
        drive_to_approved(world, node)
    off_path = (
        ("00-charter.md", ()),
        ("25-acceptance.md", ()),
        ("10-requirements.md", ("charter",)),
    )
    green = an.read_dag_state(world.state, world.ops, off_path)
    assert green.evidence is not None, "стенд начинает с одобренного состава"

    _push_edit(world, "25-acceptance.md", body="Долг вне пути к терминальному.")
    verdict = an.read_dag_state(world.state, world.ops, off_path)
    assert verdict.evidence is None, (
        "узел вне замыкания терминального обязан быть увиден"
    )
    assert [d.node_id for d in verdict.debts] == ["acceptance"]


def test_matching_intent_completes_the_wave(world: World) -> None:
    """Совпадение состава свидетельства с intent → `completed`."""
    for node in ("charter", "requirements", "behaviour-spec", "design",
                 "acceptance", "decomposition"):
        drive_to_approved(world, node)
    assert _reconcile(world) == al.WAVE_COMPLETED
    assert rs.load("r-approve").ops["approve-wave-1"]["status"] == (
        al.WAVE_COMPLETED
    )


def test_diverged_intent_makes_the_wave_obsolete(world: World) -> None:
    """Положительное расхождение → `obsolete` с ОБОИМИ составами.

    Проход по составу, которого больше нет, нечем завершить: остаток
    волны не «заброшен», а обессмыслен.
    """
    for node in ("charter", "requirements", "behaviour-spec", "design",
                 "acceptance", "decomposition"):
        drive_to_approved(world, node)
    intent = rs.load("r-approve").ops["approve-wave-1"]["intent"]

    # Гейт сошёлся на ДРУГОМ активном DAG — так выглядит доставка,
    # которой назвали иной `--legacy-bundle`, чем тот, по которому шёл
    # проход. Узлы одобрены, а проход шёл по другому составу.
    assert _reconcile(world, 3) == al.WAVE_OBSOLETE
    closed = rs.load("r-approve").ops["approve-wave-1"]
    assert closed["status"] == al.WAVE_OBSOLETE
    assert closed["intent"] == intent, "intent не переписан"
    assert "decomposition" in closed["intent"]["dag"]
    assert "decomposition" not in closed["actual"]["dag"]
    assert closed["reason"]


@pytest.mark.parametrize("outcome", [al.WAVE_COMPLETED, al.WAVE_OBSOLETE])
def test_repeat_after_either_outcome_is_a_noop(
    world: World, outcome: str
) -> None:
    """Повтор после ОБОИХ исходов не пишет ничего — на этом стоит
    право вызывающего повторить доставку после падения."""
    for node in ("charter", "requirements", "behaviour-spec", "design",
                 "acceptance", "decomposition"):
        drive_to_approved(world, node)
    if outcome == al.WAVE_OBSOLETE:
        assert _reconcile(world, 3) == al.WAVE_OBSOLETE
    else:
        assert _reconcile(world) == al.WAVE_COMPLETED
    before = dict(rs.load("r-approve").ops["approve-wave-1"])

    assert _reconcile(world) is None, "судьба уже записана"
    assert rs.load("r-approve").ops["approve-wave-1"] == before


def test_reconciliation_without_an_open_wave_writes_nothing(
    world: World,
) -> None:
    """Отсутствие волны допуску не мешает: статус волны его не решает."""
    evidence = an.ApprovedDag(("charter",), "v1:любой")
    assert an.reconcile_wave_after_approved_dag(world.state, evidence) is None
    assert al.wave_records(world.state) == {}


def test_broken_intent_is_fail_closed_without_terminalizing(
    world: World,
) -> None:
    """Повреждённый intent не закрывает волну НИ В ОДИН исход.

    Сравнивать не с чем: закрыть проход можно только сравнением двух
    записанных величин. Терминализовать по отсутствию величины значило бы
    завести ту же эвристику, которую §I12 запрещает для заявки. Выход
    человеческий — запись волны неприкосновенна (§I4), и отказ его
    называет.
    """
    approve(world, "charter")
    world.state.ops["approve-wave-1"]["intent"] = {"dag": None}
    rs.save(world.state)
    before = dict(rs.load("r-approve").ops["approve-wave-1"])
    evidence = an.ApprovedDag(("charter",), "v1:любой")

    with pytest.raises(RuntimeError, match="повреждён либо неполон"):
        an.reconcile_wave_after_approved_dag(world.state, evidence)
    after = rs.load("r-approve").ops["approve-wave-1"]
    assert after == before, "леджер не изменился"
    assert after["status"] == al.WAVE_OPEN
    with pytest.raises(RuntimeError, match="run.json"):
        an.reconcile_wave_after_approved_dag(world.state, evidence)


def test_wave_fate_is_durable_and_not_recomputed(world: World) -> None:
    """Закрытие — ЗАПИСЬ, а не вычисление.

    После `completed` активный DAG снова обзаводится долгом; вычисляемое
    закрытие «раскрылось» бы обратно, и новый проход унаследовал бы номер
    прежнего.
    """
    for node in ("charter", "requirements", "behaviour-spec", "design",
                 "acceptance", "decomposition"):
        drive_to_approved(world, node)
    assert _reconcile(world) == al.WAVE_COMPLETED

    _push_edit(world, "00-charter.md", body="Поздняя коррекция.")
    assert rs.load("r-approve").ops["approve-wave-1"]["status"] == (
        al.WAVE_COMPLETED
    ), "запись не пересчитывается по сегодняшнему состоянию"
    approve(world, "charter")
    _, fresh = _request_over(world, "charter")
    assert fresh["wave"] == 2, "новый проход — новая волна"


def test_approve_node_closes_the_obsolete_wave_and_opens_the_next(
    world: World,
) -> None:
    """`--approve-node` закрывает устаревший проход и тем же вызовом
    заводит следующий — оба действия из одного прочитанного состава."""
    drive_to_approved(world, "charter")
    first = rs.load("r-approve").ops["approve-wave-1"]
    _drop_node(world, "25-acceptance.md")

    approve(world, "requirements", legacy_bundle=5)
    closed = rs.load("r-approve").ops["approve-wave-1"]
    assert closed["status"] == al.WAVE_OBSOLETE
    assert closed["intent"] == first["intent"]
    assert "acceptance" not in closed["actual"]["dag"]
    fresh = rs.load("r-approve").ops["approve-wave-2"]
    assert fresh["status"] == al.WAVE_OPEN
    assert fresh["intent"]["dag"] == closed["actual"]["dag"]
    assert _request_over(world, "requirements")[1]["wave"] == 2


# --- Инвалидация живых заявок ниже --------------------------------------


def test_new_request_over_upstream_kills_live_downstream(
    world: World,
) -> None:
    """Заявка ниже снимается ДО публикации нашего candidate, её PR закрыт.

    Текст закрытия проверяется ДОСЛОВНО против журнала (issue #193): он
    обязан нести ту же причину, что записана в леджере, а не вторую
    формулировку рядом с первой. Разойдись они, человек, открывший
    закрытый PR, и человек, читающий `run.json`, получили бы разные
    объяснения одного события — и краснеть бы это не начало.
    """
    drive_to_approved(world, "charter")
    approve(world, "requirements")
    doomed_key, doomed = _request_over(world, "requirements")
    doomed_pr = doomed["candidate_pr"]
    world.sync()
    _mutate_body(world, "00-charter.md", "Чартер переодобряется.")

    approve(world, "charter")
    killed = world.state.ops[doomed_key]
    assert killed["status"] == al.STATUS_INVALIDATED
    assert killed["invalidated_by"] == _request_over(world, "charter")[0]
    assert world.forge.prs[doomed_pr]["state"] == "CLOSED"
    comment = world.forge.prs[doomed_pr]["closed_with"]
    assert killed["reason"] in comment, "причина — дословно из журнала"
    assert doomed_key in comment, "названа снятая заявка, а не чужая"


def test_unconfirmed_close_blocks_publication_and_resumes(
    world: World,
) -> None:
    """Не подтвердилось закрытие — mergeable candidate НЕ публикуется.

    И это не тупик: повтор доигрывает с того же шага, как только закрытие
    подтверждается.
    """
    drive_to_approved(world, "charter")
    approve(world, "requirements")
    doomed_key, doomed = _request_over(world, "requirements")
    world.sync()
    _mutate_body(world, "00-charter.md", "Чартер переодобряется.")
    world.forge.close_confirms = False

    with pytest.raises(RuntimeError, match="candidate НЕ опубликован"):
        approve(world, "charter")
    assert world.state.ops[doomed_key]["status"] == al.STATUS_INVALIDATED
    assert world.forge.prs[doomed["candidate_pr"]]["state"] == "OPEN"
    charter_key, charter_op = _request_over(world, "charter")
    assert charter_op["candidate_pr"] is None, "предложение не опубликовано"

    world.forge.close_confirms = True
    approve(world, "charter")
    assert world.state.ops[charter_key]["candidate_pr"] is not None
    assert world.forge.prs[doomed["candidate_pr"]]["state"] == "CLOSED"


def test_merged_pr_of_a_doomed_request_does_not_block(world: World) -> None:
    """Вмерженный PR снятой заявки закрывать нечего — и он не блокирует.

    Его предложение со стола уже ушло — в base, — а долг, который оно там
    создало, объявит каскад нашего же approve.
    """
    drive_to_approved(world, "charter")
    approve(world, "requirements")
    doomed_key, doomed = _request_over(world, "requirements")
    merge_pr(world, doomed["candidate_pr"])
    world.sync()
    _mutate_body(world, "00-charter.md", "Чартер переодобряется.")

    approve(world, "charter")
    assert world.state.ops[doomed_key]["status"] == al.STATUS_INVALIDATED
    assert world.forge.prs[doomed["candidate_pr"]]["state"] == "MERGED", (
        "вмерженный PR закрывать нечего — и пытаться нельзя"
    )
    assert _request_over(world, "charter")[1]["candidate_pr"] is not None


# --- Восстановление: от отказа до успешного продолжения -----------------


def test_invalidated_request_recovers_end_to_end(world: World) -> None:
    """Приёмочный: агентский мерж → invalidated → новая заявка → одобрен.

    §7 требует у восстановления не описанной процедуры, а исполнимого
    пути. Номер попытки растёт, имя ветки другое, и одобрение доводится до
    конца — узел в base проходит предикат целиком.
    """
    approve(world, "charter")
    dead_key, dead = only_request(world)
    merge_pr(world, dead["candidate_pr"], login=AGENT)
    with pytest.raises(RuntimeError, match="invalidated"):
        approve(world, "charter")

    approve(world, "charter")
    live_key, live = _live_request(world)
    assert live_key != dead_key
    assert live["attempt"] == dead["attempt"] + 1
    assert live["branch"] != dead["branch"]

    merge_pr(world, live["candidate_pr"])
    approve(world, "charter")   # конверт + агентский мерж finalize
    outcome = approve(world, "charter")
    world.sync()

    assert world.state.ops[live_key]["status"] == al.STATUS_COMPLETED
    assert "no-op" in outcome.message or "завершена" in outcome.message
    assert na.node_debt("charter", world.base_text("00-charter.md"), {}) is None


def test_reopened_pr_of_a_terminal_request_refuses(world: World) -> None:
    """Терминальная запись против форджи — расхождение решает человек."""
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"], login=AGENT)
    with pytest.raises(RuntimeError, match="invalidated"):
        approve(world, "charter")
    world.forge.prs[op["candidate_pr"]]["state"] = "OPEN"
    with pytest.raises(RuntimeError, match="переоткрыт"):
        approve(world, "charter")
    assert len(al.requests(world.state)) == 1, "вторая заявка не заведена"


# --- Вспомогательное ----------------------------------------------------


def _enable_brief(world: World) -> str:
    """Commit one immutable discovery source and attach its descriptor."""
    _git(world.human, "fetch", "-q", "origin")
    _git(world.human, "switch", "-q", "master")
    _git(world.human, "reset", "-q", "--hard", "origin/master")
    source = world.human / BUNDLE / "00-discovery/brief.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("discovery source\n", encoding="utf-8")
    _git(world.human, "add", "-A")
    _git(world.human, "commit", "-qm", "add discovery source")
    _git(world.human, "push", "-q", "origin", "master")
    world.sync()
    blob = _git(world.target, "rev-parse", f"master:{BUNDLE}/00-discovery/brief.md")
    world.state.brief = {
        "frame": "customer",
        "primary": "00-discovery/brief.md",
        "requirements_source": "00-discovery/brief.md",
        "source_paths": ["00-discovery/brief.md"],
        "source_blobs": {"discovery-brief": blob},
    }
    rs.save(world.state)
    return blob


def test_changed_discovery_source_is_forbidden_without_retry_advice(
    world: World,
) -> None:
    _enable_brief(world)
    source = world.human / BUNDLE / "00-discovery/brief.md"
    source.write_text("changed discovery source\n", encoding="utf-8")
    _git(world.human, "add", "-A")
    _git(world.human, "commit", "-qm", "change discovery source")
    _git(world.human, "push", "-q", "origin", "master")
    world.sync()

    with pytest.raises(RuntimeError, match="запрещены") as failure:
        approve(world, "charter")

    message = str(failure.value)
    assert "новый workstream/run" in message
    assert "повторите вызов" not in message


def _blob(world: World, fname: str) -> str:
    return _git(world.target, "rev-parse", f"master:{BUNDLE}/{fname}")


def _request_over(world: World, node: str) -> tuple[str, dict]:
    for nums, op in al.requests(world.state):
        if node in op["nodes"] and al.is_live(op):
            return al.request_key(*nums), op
    for nums, op in reversed(al.requests(world.state)):
        if node in op["nodes"]:
            return al.request_key(*nums), op
    raise AssertionError(f"заявки над {node} нет")


def _live_request(world: World) -> tuple[str, dict]:
    (nums, op), = al.live_requests(world.state)
    return al.request_key(*nums), op


def _mutate_body(world: World, fname: str, body: str) -> None:
    """Правка тела узла в base — как её делает correction-PR соседа."""
    _push_body_edit(world, fname, body)


def _push_body_edit(world: World, fname: str, body: str) -> None:
    _push_edit(world, fname, body=body)


def _push_edit(
    world: World, fname: str, *, body: str | None = None, refresh: bool = False
) -> None:
    """Правка узла В BASE — как её делает correction-PR соседа.

    `refresh` дополнительно пересчитывает `approved_content_hash` под новые
    байты: так выглядит «аккуратная» подмена, которую поле само о себе
    рассказать не может.
    """
    _git(world.human, "fetch", "-q", "origin")
    _git(world.human, "switch", "-q", "master")
    _git(world.human, "reset", "-q", "--hard", "origin/master")
    path = world.human / BUNDLE / fname
    meta, old_body = split_frontmatter(path.read_text(encoding="utf-8"))
    text = join_frontmatter(meta, (body + "\n") if body is not None else old_body)
    if refresh:
        meta, new_body = split_frontmatter(text)
        meta[na.SELF_HASH_KEY] = na.self_hash(text)
        text = join_frontmatter(meta, new_body)
    path.write_text(text, encoding="utf-8")
    _git(world.human, "add", "-A")
    _git(world.human, "commit", "-qm", f"correction: {fname}")
    _git(world.human, "push", "-q", "origin", "master")
    world.sync()


def _tamper_field(world: World, fname: str, key: str, value: str) -> None:
    """Подмена ОДНОГО поля frontmatter в base, без правки тела."""
    _git(world.human, "fetch", "-q", "origin")
    _git(world.human, "switch", "-q", "master")
    _git(world.human, "reset", "-q", "--hard", "origin/master")
    path = world.human / BUNDLE / fname
    meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
    meta[key] = value
    path.write_text(join_frontmatter(meta, body), encoding="utf-8")
    _git(world.human, "add", "-A")
    _git(world.human, "commit", "-qm", f"tamper: {fname}.{key}")
    _git(world.human, "push", "-q", "origin", "master")
    world.sync()


def _repin(world: World, fname: str, upstream: str, upstream_file: str) -> None:
    """«Починить» пин узла под уехавший upstream — правка в обход контракта."""
    _git(world.human, "fetch", "-q", "origin")
    _git(world.human, "switch", "-q", "master")
    _git(world.human, "reset", "-q", "--hard", "origin/master")
    path = world.human / BUNDLE / fname
    meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
    pins = dict(meta.get("upstream_hashes") or {})
    pins[upstream] = _blob(world, upstream_file)
    meta["upstream_hashes"] = pins
    path.write_text(join_frontmatter(meta, body), encoding="utf-8")
    _git(world.human, "add", "-A")
    _git(world.human, "commit", "-qm", f"repin: {fname}")
    _git(world.human, "push", "-q", "origin", "master")
    world.sync()


def _push_foreign_commit(world: World, branch: str) -> None:
    _git(world.human, "fetch", "-q", "origin")
    _git(world.human, "switch", "-q", "-C", "foreign", f"origin/{branch}")
    (world.human / "чужое.md").write_text("чужая работа\n", encoding="utf-8")
    _git(world.human, "add", "-A")
    _git(world.human, "commit", "-qm", "чужой коммит")
    _git(world.human, "push", "-q", "-f", "origin", f"foreign:{branch}")


def _drop_node(world: World, fname: str) -> None:
    """Узел уходит из бандла — состав активного DAG меняется по-настоящему."""
    _git(world.human, "fetch", "-q", "origin")
    _git(world.human, "switch", "-q", "master")
    _git(world.human, "reset", "-q", "--hard", "origin/master")
    _git(world.human, "rm", "-q", f"{BUNDLE}/{fname}")
    _git(world.human, "commit", "-qm", f"drop: {fname}")
    _git(world.human, "push", "-q", "origin", "master")
    world.sync()


def _stray_commit(world: World) -> str:
    """Коммит вне истории master, но ДОСТУПНЫЙ клону target.

    Доступность существенна: недоступный объект даёт «установить не
    удалось», а тест про установленный факт «этого коммита нет в base».
    """
    _git(world.human, "switch", "-q", "-C", "stray", "origin/master")
    (world.human / "мимо.md").write_text("вне base\n", encoding="utf-8")
    _git(world.human, "add", "-A")
    _git(world.human, "commit", "-qm", "коммит вне base")
    _git(world.human, "push", "-q", "origin", "stray")
    _git(world.target, "fetch", "-q", "origin", "stray")
    return _git(world.human, "rev-parse", "HEAD")


# --- Структурный лок: волна ничего не разрешает -------------------------

#: API записи волны. Читать его вправе ровно два модуля: сам леджер и
#: механика одобрения (выбор номера прохода и реконсиляция после гейта).
_WAVE_API = (
    "wave_records",
    "open_wave_record",
    "open_wave",
    "complete_wave",
    "obsolete_wave",
    "WAVE_PREFIX",
    "WAVE_OPEN",
    "WAVE_COMPLETED",
    "WAVE_OBSOLETE",
)

#: Модули, которым читать статус волны разрешено.
_WAVE_READERS = {"approval_ledger.py", "approve_node.py"}


def test_wave_status_is_not_readable_by_delivery() -> None:
    """Статус волны в решении о допуске не участвует — и не сможет.

    Контракт запрещает читать волну как основание допустить доставку:
    это и был бы возврат ей права одобрять, отменённого §I7. Обещание
    «мы так не делаем» держится на слове ровно до первого, кто захочет
    «дешёвую» проверку, — поэтому оно вынесено в механику.

    Часть 3 подключит к доставке ОДНУ точку — `reconcile_wave_after_approved_dag`,
    — и она живёт в `approve_node`. Тест поймает попытку дотянуться до
    записи волны напрямую: доставка вправе позвать реконсиляцию, но не
    вправе спросить у волны, можно ли ей идти.
    """
    package = Path(an.__file__).resolve().parent
    offenders: list[str] = []
    for path in sorted(package.glob("*.py")):
        if path.name in _WAVE_READERS:
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for name in _WAVE_API:
                if name in stripped:
                    offenders.append(f"{path.name}:{lineno}: {stripped}")
    assert not offenders, (
        "запись волны читается вне разрешённых модулей:\n"
        + "\n".join(offenders)
    )


def test_the_wave_api_names_exist(monkeypatch) -> None:
    """Пара к предыдущему: перечень имён не разъехался с леджером.

    Опечатка в `_WAVE_API` сделала бы прошлый тест вакуумным — он искал бы
    строки, которых в коде нет вовсе, и молчал бы на любом нарушении.
    """
    for name in _WAVE_API:
        assert hasattr(al, name), name


# --- Структурный лок: продвижение заявки не читает состав DAG -----------

#: Имена резолвера АКТИВНОГО СОСТАВА. `node_id` сюда не входит намеренно:
#: он отображает имя файла в node-id и о составе графа не отвечает.
_RESOLVER = {"composition", "composition_fingerprint", "dag_for", "read_dag_state"}


def _calls_reachable_from(entry: str) -> set[str]:
    """Имена, вызываемые из `entry` транзитивно внутри `approve_node`.

    Обход по AST модуля: у каждой функции собираются имена вызовов
    (`f()` и `mod.f()`), затем обход идёт по тем из них, которые в этом же
    модуле определены. Так «не вызывает» становится свойством КОДА, а не
    того, что автор туда не написал вызов.
    """
    tree = ast.parse(Path(an.__file__).read_text(encoding="utf-8"))
    bodies = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }
    seen: set[str] = set()
    reached: set[str] = set()
    frontier = [entry]
    while frontier:
        current = frontier.pop()
        if current in seen or current not in bodies:
            continue
        seen.add(current)
        for node in ast.walk(bodies[current]):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            name = (
                target.attr
                if isinstance(target, ast.Attribute)
                else target.id
                if isinstance(target, ast.Name)
                else None
            )
            if name is None:
                continue
            reached.add(name)
            frontier.append(name)
    return reached


def test_advance_never_resolves_the_active_dag() -> None:
    """Продвижение живой заявки состав активного DAG не читает.

    Заявка реконсилируется по ЗАПИСАННОМУ: candidate-байты, PR, мерж,
    авторизация, фаза финализации. Даже если текущий DAG уже изменился,
    корректное завершение старой заявки лишь фиксирует состоявшийся акт
    над прежним intent — доставку оно не разрешает. Прочитай `_advance`
    состав, и между двумя чтениями появилось бы решение, основанное на
    разных снимках.
    """
    touched = _calls_reachable_from("_advance") & _RESOLVER
    assert not touched, f"_advance дотянулся до резолвера состава: {touched}"


def test_the_proposal_path_does_resolve_it() -> None:
    """Пара к предыдущему: резолвер вообще вызывается там, где должен.

    Без этой половины первый тест вакуумен — «не вызывает» не отличало бы
    разделение путей от мёртвого резолвера, который не зовут нигде.
    """
    assert _calls_reachable_from("_propose") & _RESOLVER
    assert _RESOLVER <= (
        _calls_reachable_from("_propose") | _calls_reachable_from("approve_node")
        | {"read_dag_state"}
    )


# --- ADR-ECO-011 D5: finalize мержит агент по умолчанию ------------------


def test_finalize_is_merged_by_agent_in_the_same_call(world: World) -> None:
    """Дефолт D5: второй вызов выносит конверт, мержит его от ai-prosto и
    завершает заявку; подпись в base — от мержера candidate, не finalize."""
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    outcome = approve(world, "charter")
    rec = world.state.ops[key]
    finalize = world.forge.prs[rec["finalize_pr"]]
    assert finalize["label"] == "", "лейбл человеку по дефолту не ставится"
    assert finalize["state"] == "MERGED"
    assert finalize["mergedBy"] == {"login": AGENT}
    assert world.forge.merge_calls == [
        (rec["finalize_pr"], rec["finalize_head_sha"])
    ], "мерж — с пином головы конверта"
    assert rec["status"] == al.STATUS_COMPLETED
    assert "завершена" in outcome.message
    world.sync()
    meta = world.base_meta("00-charter.md")
    assert meta["status"] == na.STATUS_APPROVED
    assert meta["approved_by"] == HUMAN
    assert meta["approved_at"] == MERGED_AT


def test_repo_human_policy_labels_finalize_and_waits(world: World) -> None:
    """«Мерж: человек» в CLAUDE.md цели — лейбл, ни одного вызова мержа,
    заявка ждёт; человеческий мерж завершает её следующим вызовом."""
    declare_human_merge(world)
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    outcome = approve(world, "charter")
    finalize_pr = world.state.ops[key]["finalize_pr"]
    assert world.forge.prs[finalize_pr]["label"] == an.HUMAN_MERGE_LABEL
    assert world.forge.prs[finalize_pr]["state"] == "OPEN"
    assert world.forge.merge_calls == []
    assert "Мержит его учётка" in outcome.message
    assert "человек" in outcome.message
    assert "ждём мержа" in approve(world, "charter").message
    merge_pr(world, finalize_pr)
    approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_COMPLETED


def test_safety_unknown_routes_finalize_to_human(world: World, monkeypatch) -> None:
    """Ось safety (срез steward) читается fail-closed: unknown = человек."""
    monkeypatch.setattr(
        an, "load_safety",
        lambda: mg.Safety(agent_merge_allowed=None, actor_class="unknown"),
    )
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    approve(world, "charter")
    finalize_pr = world.state.ops[key]["finalize_pr"]
    assert world.forge.prs[finalize_pr]["label"] == an.HUMAN_MERGE_LABEL
    assert world.forge.merge_calls == []
    assert al.is_live(world.state.ops[key])


def test_empty_allowlist_refuses_before_any_candidate_is_created(
    world: World, monkeypatch
) -> None:
    """Пустая политика — отказ ДО заявки и PR, а не после человеческого акта.

    Пустой дефолт `AUTHORIZED_APPROVER_ACCOUNTS` — правильный fail-closed и
    остаётся как есть. Дефект был в МОМЕНТЕ: механика заводила candidate-PR,
    человек его мержил, и только потом слышала, что подписи этот мерж не
    создаёт (devtools#278). На шаге предложения известно всё нужное:
    политика пуста ⇒ ни один мерж подписи не даст ⇒ предлагать акт незачем.
    """
    monkeypatch.setenv(af.APPROVER_ALLOWLIST_ENV, "")
    with pytest.raises(RuntimeError) as caught:
        approve(world, "charter")
    message = str(caught.value)
    assert af.APPROVER_ALLOWLIST_ENV in message
    assert world.state.ops == {}, "заявка не заводится"
    assert world.forge.prs == {}, "candidate-PR не создаётся"


def test_empty_allowlist_refusal_is_not_about_the_merger(
    world: World, monkeypatch
) -> None:
    """Причина — «политика недоступна», и она отличима от «мержер не
    авторизован»: вторая обвиняет человека в том, чего он не делал."""
    monkeypatch.setenv(af.APPROVER_ALLOWLIST_ENV, "")
    with pytest.raises(RuntimeError) as caught:
        approve(world, "charter")
    message = str(caught.value)
    assert "пуст" in message, "названа пустота политики, а не учётка"
    assert "не входит в" not in message, "это формулировка отказа мержеру"


def test_no_op_over_approved_node_survives_empty_allowlist(
    world: World, monkeypatch
) -> None:
    """Повтор над честно одобренным узлом ничего не создаёт, поэтому пустая
    политика ему не помеха: отказ здесь был бы ложным."""
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_COMPLETED
    world.sync()
    monkeypatch.setenv(af.APPROVER_ALLOWLIST_ENV, "")
    outcome = approve(world, "charter")
    assert "no-op" in outcome.message


def test_finalize_without_policy_refuses_and_keeps_the_request(
    world: World, monkeypatch
) -> None:
    """Политика не пережила границу процессов — отказ, не `invalidated`
    (@id:approver-allowlist-process-boundary).

    Прогон S7 2026-09-21, candidate #315: мерж выполнен учёткой из
    allowlist процессом A, финализацию запустил процесс B без переменной в
    окружении — контур увидел «подписать не может никто» и похоронил
    живую заявку. Candidate существует только под непустой политикой
    (devtools#278), поэтому пустота на фазе 2 — потеря значения, а не
    решение о мержере: факт не установлен, заявка сохраняется, повтор с
    политикой завершает её тем же candidate.
    """
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])          # процесс A: политика есть
    monkeypatch.setenv(af.APPROVER_ALLOWLIST_ENV, "")   # процесс B: нет

    with pytest.raises(RuntimeError) as caught:
        approve(world, "charter")

    message = str(caught.value)
    assert "пуст" in message, "названа недоступность политики"
    assert "учётки нет в" not in message, "это формулировка отказа мержеру"
    record = world.state.ops[key]
    assert al.is_live(record), record.get("reason")
    assert record["status"] != al.STATUS_INVALIDATED
    assert record.get("authorization") is None, (
        "решение об авторизации не записано"
    )
    assert world.forge.prs[op["candidate_pr"]]["state"] == "MERGED"

    monkeypatch.setenv(af.APPROVER_ALLOWLIST_ENV, HUMAN)
    approve(world, "charter")                    # повтор ТОЙ ЖЕ заявки
    assert world.state.ops[key]["status"] == al.STATUS_COMPLETED
    assert world.state.ops[key]["authorization"]["login"] == HUMAN


AUTHORED_FRONTMATTER_LINES = (
    "traces_to: [discovery-brief]",
    'brief_sha256: "e3b0c44298fc1c149afbfbf4c8996fb92427ae41e4649b934ca495991b7852b8"',
)


def _seed_authored_charter(world: World) -> None:
    """Узел charter с авторскими строками во flow-стиле и с кавычками —
    ровно те формы, которые `yaml.safe_dump` переписывает по-своему."""
    _git(world.human, "fetch", "-q", "origin")
    _git(world.human, "switch", "-q", "master")
    _git(world.human, "reset", "-q", "--hard", "origin/master")
    (world.human / BUNDLE / "00-charter.md").write_text(
        "---\n"
        "node: charter\n"
        "status: draft\n"
        "version: 1\n"
        + "\n".join(AUTHORED_FRONTMATTER_LINES) + "\n"
        "approved_by: ''\n"
        "approved_at: ''\n"
        "---\n"
        "\n"
        "Содержание узла charter.\n",
        encoding="utf-8",
    )
    _git(world.human, "add", "-A")
    _git(world.human, "commit", "-qm", "charter: авторский frontmatter")
    _git(world.human, "push", "-q", "origin", "master")
    world.sync()


def test_approval_stamp_leaves_authored_frontmatter_bytes_alone(
    world: World,
) -> None:
    """Диф candidate/finalize содержит ТОЛЬКО строки, которые штамп меняет
    по смыслу (@id:approval-stamp-frontmatter-roundtrip).

    Прогон S7 2026-09-21, candidate #316: `--approve-node` прогонял
    frontmatter через `split_frontmatter`/`join_frontmatter`, и в дифе
    появлялись правки, которых автор не делал — flow-список становился
    блочным, с хэшей слетали кавычки. Человек перед подписью читал шум
    вместо трёх содержательных строк, а «авторский текст сохраняется» по
    конвейеру в целом было неверно.
    """
    _seed_authored_charter(world)
    first = approve(world, "charter")
    assert first.request is not None
    candidate = world.state.ops[first.request]["candidate_pr"]
    branch = world.forge.prs[candidate]["branch"]
    proposed = _show(world.target, f"origin/{branch}:{BUNDLE}/00-charter.md")
    for line in AUTHORED_FRONTMATTER_LINES:
        assert line in proposed.splitlines(), (
            f"candidate переписал авторскую строку {line!r}:\n{proposed}"
        )

    merge_pr(world, candidate)
    approve(world, "charter")
    world.sync()
    final = world.base_text("00-charter.md")
    for line in AUTHORED_FRONTMATTER_LINES:
        assert line in final.splitlines(), (
            f"finalize переписал авторскую строку {line!r}:\n{final}"
        )
    meta = world.base_meta("00-charter.md")
    assert meta["status"] == na.STATUS_APPROVED
    assert meta["approved_by"] == HUMAN
    assert meta[na.SELF_HASH_KEY] == na.self_hash(final)


def test_agent_merge_refusal_leaves_finalize_to_human(world: World) -> None:
    """Отказ обвязки — не ошибка: PR без лейбла остаётся человеку, заявка
    жива; повторный вызов пробует агентский мерж снова (ревью #233), а
    человеческий мерж завершает заявку."""
    world.forge.agent_merge_rc = 4          # форджа отклонила — не транзиент
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    outcome = approve(world, "charter")
    finalize_pr = world.state.ops[key]["finalize_pr"]
    assert world.forge.prs[finalize_pr]["state"] == "OPEN"
    assert world.forge.prs[finalize_pr]["label"] == ""
    assert len(world.forge.merge_calls) == 1, "код 4 не повторяется"
    assert "отказал кодом 4" in outcome.message
    assert "human-merge" in outcome.message, (
        "отказ самой форджи — повод звать человека; снимается только совет "
        "там, где лечит аттестация"
    )
    assert al.is_live(world.state.ops[key])
    again = approve(world, "charter")
    assert len(world.forge.merge_calls) == 2, "повтор пробует мерж снова"
    assert "отказал кодом 4" in again.message
    merge_pr(world, finalize_pr)
    approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_COMPLETED


def test_finalize_without_approving_review_names_the_attestation_step(
    world: World,
) -> None:
    """Нет действующего одобрения — мержа не пробуем вовсе, а называем шаг.

    Находка 2 контрольного прогона S7 (2026-09-21): правило форджи требует
    одного одобряющего ревью, у свежего finalize-PR его нет, и шесть узлов
    из шести отказали кодом 4 с советом «мержит человек». Совет неверный:
    мержит тот же агент — после scope-аттестации.
    """
    world.forge.default_reviews = []
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    outcome = approve(world, "charter")
    finalize_pr = world.state.ops[key]["finalize_pr"]
    assert world.forge.merge_calls == [], "мерж не пробуется без одобрения"
    assert str(finalize_pr) in outcome.message
    assert "review-pr.sh" in outcome.message
    assert "--approve-node" in outcome.message
    assert "мержит человек" not in outcome.message
    # Обёртка отказа дописывала совет безусловно, и он противоречил бы
    # инструкции выше: лечит аттестация, а не человеческий мерж.
    assert "human-merge" not in outcome.message
    assert al.is_live(world.state.ops[key])
    # Повторный заход по тому же PR идёт другим путём (`_reconcile_finalize`)
    # и обязан говорить то же самое.
    again = approve(world, "charter")
    assert world.forge.merge_calls == []
    assert "review-pr.sh" in again.message
    assert "human-merge" not in again.message


def test_finalize_merges_after_the_attestation_appears(world: World) -> None:
    """Позитивный двойник: аттестация опубликована — тот же вызов мержит.

    Повтор продолжает ТОТ ЖЕ finalize-PR, номер не меняется.
    """
    world.forge.default_reviews = []
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    approve(world, "charter")
    finalize_pr = world.state.ops[key]["finalize_pr"]
    assert world.forge.merge_calls == []
    world.forge.reviews[finalize_pr] = [{"login": AGENT, "state": "APPROVED"}]
    approve(world, "charter")
    assert world.state.ops[key]["finalize_pr"] == finalize_pr, "тот же PR"
    assert len(world.forge.merge_calls) == 1
    assert world.state.ops[key]["status"] == al.STATUS_COMPLETED


def test_unknown_review_list_is_fail_closed(world: World) -> None:
    """Список ревью не получен — «одобрения нет» и «его не видно» неразличимы,
    поэтому мержа нет и об этом говорится прямо."""
    world.forge.mute.add("pr_reviews")
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    outcome = approve(world, "charter")
    assert world.forge.merge_calls == []
    assert "не получен" in outcome.message
    assert al.is_live(world.state.ops[key])


def test_changes_requested_over_earlier_approval_is_not_effective(
    world: World,
) -> None:
    """Действующее — ПОСЛЕДНЕЕ ревью логина: `CHANGES_REQUESTED` поверх
    прежнего `APPROVED` одобрением больше не является."""
    world.forge.default_reviews = [
        {"login": AGENT, "state": "APPROVED"},
        {"login": AGENT, "state": "CHANGES_REQUESTED"},
    ]
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    approve(world, "charter")
    assert world.forge.merge_calls == []
    assert al.is_live(world.state.ops[key])


def test_human_approval_counts_as_well_as_the_contour(world: World) -> None:
    """Проверка предсказывает правило форджи, а не строже его: одобрение
    человека засчитывается так же, как аттестация ревью-контура."""
    world.forge.default_reviews = [{"login": HUMAN, "state": "APPROVED"}]
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    approve(world, "charter")
    assert len(world.forge.merge_calls) == 1
    assert world.state.ops[key]["status"] == al.STATUS_COMPLETED


def test_transient_refusal_is_retried_within_the_call(world: World) -> None:
    """UNKNOWN у форджи / сеть (код 2) — транзиент: повтор с паузой в том же
    вызове, мерж состоялся, заявка завершена."""
    world.forge.agent_merge_rc_seq = [2, 2, 0]
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    approve(world, "charter")
    assert len(world.forge.merge_calls) == 3
    assert world.state.ops[key]["status"] == al.STATUS_COMPLETED
    assert world.forge.prs[world.state.ops[key]["finalize_pr"]]["mergedBy"] == {
        "login": AGENT
    }


def test_open_finalize_is_merged_by_agent_on_repeat_call(world: World) -> None:
    """Прошлый заход исчерпал попытки — следующий вызов мержит сам, без
    человека: дефолт D5 не деградирует до человеческого мержа."""
    world.forge.agent_merge_rc_seq = [2, 2, 2]
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    first = approve(world, "charter")
    assert "отказал кодом 2" in first.message
    assert "повторный вызов пробует снова" in first.message
    assert al.is_live(world.state.ops[key])
    second = approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_COMPLETED
    assert "завершена" in second.message
    assert len(world.forge.merge_calls) == 4


def test_guard_code_is_not_retried_and_not_promised(world: World) -> None:
    """Код 3 — гвард обвязки: одна попытка, без паузы, диагностика не обещает
    успешный повтор (ревью #233)."""
    world.forge.agent_merge_rc = 3
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    outcome = approve(world, "charter")
    assert len(world.forge.merge_calls) == 1
    assert "отказал кодом 3" in outcome.message
    assert "повтор не поможет" in outcome.message
    assert "пробует снова" not in outcome.message
    assert al.is_live(world.state.ops[key])


def test_repeat_call_restores_missing_envelope_object_by_fetch(
    world: World,
) -> None:
    """Клон пересоздан при живом леджере: объект конверта подтягивается
    веткой заявки, гейт формы читает его и агент мержит (ревью #233)."""
    world.forge.agent_merge_rc_seq = [2, 2, 2]
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    approve(world, "charter")
    rec = world.state.ops[key]
    _git(world.target, "switch", "-q", "master")
    _git(world.target, "branch", "-q", "-D", rec["finalize_branch"])
    _git(
        world.target, "update-ref", "-d",
        f"refs/remotes/origin/{rec['finalize_branch']}",
    )
    _git(world.target, "reflog", "expire", "--expire=now", "--all")
    _git(world.target, "gc", "-q", "--prune=now")
    gone = f"{rec['finalize_head_sha']}^{{commit}}"   # голый sha не проверяет объект
    assert world.ops.rev_parse(str(world.target), gone) is None
    approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_COMPLETED


def test_finalize_merge_policy_axes(tmp_path: Path, monkeypatch) -> None:
    """Дефолт agent; репо «Мерж: человек» — human; safety не-agent — human;
    объявление прогона (`merge_authority`) на finalize НЕ влияет."""
    assert an.finalize_merge_policy(str(tmp_path))[0] == "agent"
    monkeypatch.setattr(
        an, "load_safety",
        lambda: mg.Safety(agent_merge_allowed=False, actor_class="agent"),
    )
    who, why = an.finalize_merge_policy(str(tmp_path))
    assert (who, "safety" in why) == ("human", True)
    monkeypatch.setattr(
        an, "load_safety",
        lambda: mg.Safety(agent_merge_allowed=True, actor_class="human"),
    )
    assert an.finalize_merge_policy(str(tmp_path))[0] == "human"
    monkeypatch.undo()
    (tmp_path / "CLAUDE.md").write_text("Мерж: человек\n", encoding="utf-8")
    who, why = an.finalize_merge_policy(str(tmp_path))
    assert (who, "authority" in why) == ("human", True)


def test_malformed_envelope_is_not_merged_by_agent(world: World, monkeypatch) -> None:
    """Подсадка: конверт, тронувший тело узла, агент НЕ мержит — гейт формы
    дифа стоит до `ops.merge`, PR остаётся человеку, заявка жива."""
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    real_update = an.update_frontmatter
    monkeypatch.setattr(
        an, "update_frontmatter",
        lambda text, updates: real_update(text, updates) + "лишняя строка\n",
    )
    outcome = approve(world, "charter")
    finalize_pr = world.state.ops[key]["finalize_pr"]
    assert world.forge.merge_calls == []
    assert world.forge.prs[finalize_pr]["state"] == "OPEN"
    assert "трогает тело узла" in outcome.message
    assert "НЕ выполнен" in outcome.message
    assert al.is_live(world.state.ops[key])


def test_envelope_form_defect_names_each_deviation(world: World) -> None:
    """Единичная проверка формы: чужая подпись, лишний файл, нечитаемый sha."""
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    world.forge.agent_merge_rc = 4          # конверт остаётся веткой
    approve(world, "charter")
    rec = world.state.ops[key]
    head = rec["finalize_head_sha"]
    dag = bundle_dag.BUNDLE_DAG
    assert an._envelope_form_defect(
        world.state, world.ops, dag, rec, ["charter"], head
    ) is None
    wrong_sig = dict(rec, merged_by="someone-else")
    defect = an._envelope_form_defect(
        world.state, world.ops, dag, wrong_sig, ["charter"], head
    )
    assert defect is not None and "не равен записи заявки" in defect
    defect = an._envelope_form_defect(
        world.state, world.ops, dag, rec, ["charter", "requirements"], head
    )
    assert defect is not None and "ожидались ровно" in defect
    defect = an._envelope_form_defect(
        world.state, world.ops, dag, rec, ["charter"], "0" * 40
    )
    assert defect is not None and "не читается" in defect
