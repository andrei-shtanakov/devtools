"""Обёртки brief-маршрута (спека need-stage §11.3, §11.5, §11.3 п.6).

`propose` — brief-PR (бриф + заявка на одобрение) из customer-прогона в
`brief_ready`; `approve` — зеркало человеческого мержа через
`discovery approve`. Проверка brief-PR перед человеческим мержем — НЕ здесь:
её исполняет `human-merge.sh` с полномочиями человека, поэтому она в узком
защищённом `governance/brief_merge_check.py` (ревью #573). Ни у одной
обёртки нет леджера: состояние предложения — в фордже, повтор
восстанавливается его чтением.

CLI: `python -m governance.brief_tools {propose,approve} …`;
коды: 0 — результат есть; 1 — отказ; 2 — факт не установлен (повторите).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from governance import (
    approval_facts,
    approval_request,
    brief_input,
    brief_provenance,
    discovery_approval,
    policy_rule,
)
from governance import interview as iv
from governance import run_lock as rl
from governance import run_state as rs
from governance.approval_request import ApprovalRequest
from governance.facts import Outcome
from governance.stale_adapter import blob_sha1_bytes

LABEL = "human-merge-required"
APPROVED_REL = "brief-approval/customer-brief.md"


class BriefToolError(RuntimeError):
    """Отказ обёртки; `retry` — факт форджа не установлен."""

    def __init__(self, message: str, *, retry: bool = False) -> None:
        super().__init__(message)
        self.retry = retry


def brief_branch(ws_id: str) -> str:
    """Ветка brief-PR (§11.3 п.2)."""
    return f"brief/{ws_id}"


def proposal_dir(state: rs.RunState) -> str:
    """Каталог предложения в репо цели: `<bundle_dir>/00-discovery`."""
    return f"{state.bundle_dir}/00-discovery"


def expected_request(
    state: rs.RunState, brief_text: str, policy_sha: str
) -> ApprovalRequest:
    """Заявка этого прогона с пином `policy_sha` (§11.3 п.3)."""
    repo, ref, path = policy_rule.policy_source()
    return ApprovalRequest(
        brief_self_hash=discovery_approval.self_hash(brief_text),
        policy_repo=repo,
        policy_ref=ref,
        policy_path=path,
        policy_sha=policy_sha,
        run_id=state.run_id,
        ws_id=state.ws_id,
    )


def _fact(fact, what: str):
    if fact.outcome is Outcome.UNAVAILABLE:
        raise BriefToolError(f"{what}: {fact.detail} — повторите", retry=True)
    return fact


def _current_policy(ops) -> str:
    snapshot = approval_facts.policy_snapshot(ops, pinned_sha=None)
    _fact(snapshot, "политика подписи")
    if snapshot.outcome is not Outcome.FOUND:
        raise BriefToolError(f"политика подписи не установлена: {snapshot.detail}")
    return snapshot.value.sha


def _ready_brief(state: rs.RunState) -> tuple[bytes, str]:
    """§11.3 п.1: `brief_ready`, blob совпадает, бриф проходит E1, `status: draft`."""
    if state.status != "brief_ready":
        raise BriefToolError(
            f"прогон {state.run_id} в статусе {state.status}, нужен brief_ready "
            "(customer --brief-only)"
        )
    path = rs.run_dir(state.run_id) / iv.BRIEF_REL
    data = path.read_bytes()
    if blob_sha1_bytes(data) != (state.interview or {}).get("brief_blob"):
        raise BriefToolError(
            "brief.md прогона ≠ brief_blob — бриф изменён после публикации"
        )
    try:
        brief_input.inspect_brief(path)
    except brief_input.BriefInputError as exc:
        raise BriefToolError(f"бриф не проходит inspect_brief: {exc}") from exc
    text = data.decode("utf-8")
    if discovery_approval.verify(text) != discovery_approval.DEBT_STATUS:
        raise BriefToolError("бриф уже несёт подпись — предлагать нечего")
    return data, text


def _matches(
    ops, state: rs.RunState, sha: str, files, brief_text: str, dir_: str
) -> str:
    """Найденное предложение — НАШЕ (§11.3 п.4); вернуть его исходный пин политики.

    Список файлов, байты брифа и ВСЕ поля заявки, кроме `policy.sha`,
    сравниваются с ожидаемыми; `policy.sha` — версия источника политики.
    """
    if set(files) != _expected_files(dir_) or len(files) != 2:
        raise BriefToolError(f"найденное предложение меняет {list(files)} — чужое")
    brief = _fact(
        ops.repo_file_fact(state.repo_slug, sha, f"{dir_}/{approval_request.BRIEF}"),
        "бриф предложения",
    )
    req_text = _fact(
        ops.repo_file_fact(
            state.repo_slug, sha, f"{dir_}/{approval_request.FILE_NAME}"
        ),
        "заявка предложения",
    )
    if brief.outcome is not Outcome.FOUND or req_text.outcome is not Outcome.FOUND:
        raise BriefToolError(f"предложение @{sha} без брифа или заявки — чужое")
    if brief.value != brief_text:
        raise BriefToolError("бриф найденного предложения ≠ брифу прогона — чужое")
    try:
        req = approval_request.parse(req_text.value)
    except approval_request.RequestError as exc:
        raise BriefToolError(f"заявка найденного предложения: {exc}") from exc
    if req != expected_request(state, brief_text, req.policy_sha):
        raise BriefToolError("заявка найденного предложения не про этот прогон — чужое")
    repo, ref, path = policy_rule.policy_source()
    version = _fact(ops.policy_version_fact_at(repo, ref, path, req.policy_sha), "пин")
    if version.value is not True:
        raise BriefToolError(f"пин предложения {req.policy_sha} — не версия политики")
    return req.policy_sha


def _existing_pr(ops, state, number: int, brief_text: str, dir_: str) -> int:
    facts = _fact(ops.brief_pr_fact(state.repo_slug, number), f"PR #{number}").value
    sha = facts.merge_commit if facts.state == "MERGED" else facts.head_sha
    pin = _matches(ops, state, sha, facts.files, brief_text, dir_)
    if facts.state == "CLOSED":
        raise BriefToolError(
            f"brief-PR #{number} закрыт без мержа — новое предложение: --repropose "
            "(вне E2)"
        )
    if facts.state == "MERGED":
        print(
            f"brief-PR #{number} уже смержен — следующий шаг: "
            f"make brief-approve RUN={state.run_id} PR={number}"
        )
        return number
    print(f"brief-PR #{number} открыт")
    if pin != _current_policy(ops):
        print(
            f"ВНИМАНИЕ: пин политики заявки {pin} ≠ актуальной версии — "
            "human-merge откажет до мержа; нужен --repropose (вне E2)"
        )
    return number


def _expected_files(dir_: str) -> set[tuple[str, str]]:
    return {
        (f"{dir_}/{approval_request.BRIEF}", "added"),
        (f"{dir_}/{approval_request.FILE_NAME}", "added"),
    }


def _create(ops, state, branch: str, data: bytes, brief_text: str, dir_: str) -> int:
    default = _fact(
        ops.default_branch_fact(state.repo_slug), "ветка по умолчанию"
    ).value
    for name in (approval_request.BRIEF, approval_request.FILE_NAME):
        present = _fact(
            ops.repo_file_fact(state.repo_slug, default.sha, f"{dir_}/{name}"),
            f"{dir_}/{name} в базе",
        )
        if present.outcome is Outcome.FOUND:
            raise BriefToolError(f"в базе уже есть {dir_}/{name} — чужой бриф")
    target = Path(state.target_dir)
    if ops.is_dirty(state.target_dir):
        raise BriefToolError(f"{target} грязный — предложение не создаётся")
    request = expected_request(state, brief_text, _current_policy(ops))
    ops.fetch_branch(state.target_dir, default.name)
    ops.switch_to(state.target_dir, branch, default.sha)
    paths = [f"{dir_}/{approval_request.BRIEF}", f"{dir_}/{approval_request.FILE_NAME}"]
    (target / dir_).mkdir(parents=True, exist_ok=True)
    (target / paths[0]).write_bytes(data)
    (target / paths[1]).write_text(approval_request.render(request), encoding="utf-8")
    ops.commit_paths(
        state.target_dir,
        paths,
        f"brief: заявка на одобрение discovery-брифа {state.ws_id}",
    )
    # До push (ревью части B, B3): локальный коммит над базой меняет ровно
    # два пути предложения; его SHA — то, что обязан нести будущий PR.
    _, changed = ops.changed_paths(state.target_dir, default.name)
    if sorted(changed) != sorted(paths):
        raise BriefToolError(f"коммит предложения меняет {changed} — не предложение")
    head = ops.head_sha(state.target_dir, branch)
    try:
        ops.push_branch(state.target_dir, branch)
    except RuntimeError as exc:
        raise BriefToolError(
            f"push {branch} не прошёл ({exc}) — повторите: make brief-propose "
            f"RUN={state.run_id}",
            retry=True,
        ) from exc
    return _open_pr(ops, state, branch, default, request.policy_sha, head)


def _open_pr(ops, state, branch: str, default, policy_sha: str, head: str) -> int:
    """PR на ветку, проверенную по SHA `head`; голова созданного PR обязана быть им.

    Ветка изменяема: между проверкой и созданием её могли сдвинуть. PR с
    другой головой не объявляется результатом — это отказ с номером PR.
    """
    repo = policy_rule.policy_source()[0]
    body = (
        "Акт одобрения discovery-брифа (D5): мерж этого PR учёткой человека из "
        "политики подписи — это подпись брифа, а не одобрение governance-узлов.\n\n"
        f"policy: {repo}@{policy_sha}\n\n"
        "Если ruleset цели требует одобряющего ревью — перед мержем: "
        f"`sh review-pr.sh {state.repo} <этот PR>`.\n"
        "Мерж: `make human-merge ARGS='<repo> <этот PR>'`; затем "
        f"`make brief-approve RUN={state.run_id} PR=<этот PR>`.\n"
    )
    try:
        number = ops.create_pr(
            state.target_dir,
            state.repo_slug,
            branch,
            f"brief: одобрение discovery-брифа {state.ws_id}",
            body,
            LABEL,
            base=default.name,
        )
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        raise BriefToolError(
            f"PR не создан ({exc}) — повторите: make brief-propose RUN={state.run_id} "
            "(ветка уже на форджe, повтор создаст только PR)",
            retry=True,
        ) from exc
    created = _fact(ops.brief_pr_fact(state.repo_slug, number), f"PR #{number}").value
    if created.head_sha != head:
        raise BriefToolError(
            f"brief-PR #{number} создан с головой {created.head_sha}, проверялась "
            f"{head} — ветку сдвинули; предложение НЕ годно, разберитесь вручную"
        )
    print(f"brief-PR #{number} создан")
    return number


def propose(run_id: str, ops) -> int:
    """§11.3: brief-PR из `brief_ready`; повтор восстанавливается по форджу."""
    with rl.run_lock(run_id):
        state = rs.load(run_id)
        data, brief_text = _ready_brief(state)
        dir_ = proposal_dir(state)
        branch = brief_branch(state.ws_id)
        found = _fact(ops.find_brief_pr_fact(state.repo_slug, branch), "PR ветки").value
        if len(found) > 1:
            raise BriefToolError(f"неоднозначно: у {branch} несколько PR {found}")
        if found:
            return _existing_pr(ops, state, found[0], brief_text, dir_)
        head = ops.remote_branch_head_fact(state.repo_slug, branch)
        _fact(head, f"ветка {branch}")
        if head.outcome is Outcome.FOUND:
            # Окно «push есть, PR нет»: и список файлов, и содержимое — по
            # ОДНОМУ неизменяемому SHA головы (ревью части B, B3).
            default = _fact(
                ops.default_branch_fact(state.repo_slug), "ветка по умолчанию"
            ).value
            files = _fact(
                ops.compare_files_fact(state.repo_slug, default.sha, head.value),
                "diff",
            ).value
            pin = _matches(ops, state, head.value, files, brief_text, dir_)
            return _open_pr(ops, state, branch, default, pin, head.value)
        return _create(ops, state, branch, data, brief_text, dir_)


def _policy_or_fail(ops, act: brief_provenance.Act) -> str:
    got = brief_provenance.check_policy(ops, act)
    if isinstance(got, brief_provenance.Refusal):
        raise BriefToolError(got.detail, retry=got.retry)
    return got.current


def _engineer_command(state: rs.RunState, pr: int, path: Path) -> str:
    return (
        f"make spec-loop SUBJECT='{state.subject}' REPO={state.repo} ARGS='--need "
        f"--frame engineer --new-run --ws-id {state.ws_id}-eng --stakeholder "
        f"<role> --traces-to {path} --approval-pr {pr}'"
    )


def approve(run_id: str, pr: int, ops) -> Path:
    """§11.5: зеркало человеческого мержа brief-PR через `discovery approve`.

    Успех требует ВСЕГО вместе: акт по форджу, политика до и после вызова
    той же версии, код 0 и перечитанный файл, честно зеркалящий мерж.
    Возвращает путь подписанного файла (вход engineer-прогона).
    """
    with rl.run_lock(run_id) as lock:
        state = rs.load(run_id)
        act = brief_provenance.read_act(ops, state.repo_slug, pr)
        if isinstance(act, brief_provenance.Refusal):
            raise BriefToolError(act.detail, retry=act.retry)
        if act.dir != proposal_dir(state):
            raise BriefToolError(f"PR #{pr} одобряет {act.dir}, не бриф этого прогона")
        own = rs.run_dir(run_id) / iv.BRIEF_REL
        if _file_self_hash(own) != act.brief_self_hash:
            raise BriefToolError(f"заявка PR #{pr} — не про бриф этого прогона")
        before = _policy_or_fail(ops, act)
        target = rs.run_dir(run_id) / APPROVED_REL
        merged = _fact(
            ops.repo_file_fact(
                state.repo_slug, act.merge_commit, f"{act.dir}/{approval_request.BRIEF}"
            ),
            "бриф merge-коммита",
        )
        if merged.outcome is not Outcome.FOUND:
            raise BriefToolError(f"бриф в merge-коммите не найден: {merged.detail}")
        if target.exists():
            if _file_self_hash(target) != act.brief_self_hash:
                raise BriefToolError(f"{target}: на месте чужой файл")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(".customer-brief.tmp")
            tmp.write_text(merged.value, encoding="utf-8")
            os.replace(tmp, target)
        reply = ops.discovery_approve(
            str(target),
            state.repo_slug,
            pr,
            f"{act.dir}/{approval_request.BRIEF}",
            str(rs.run_dir(run_id)),
            lock_fd=lock.fd,
        )
        try:
            after = _policy_or_fail(ops, act)
        except BriefToolError as exc:
            raise BriefToolError(
                f"итог не подтверждён: подпись могла быть записана под "
                f"неустановленной политикой ({exc})",
                retry=exc.retry,
            ) from exc
        if after != before:
            raise BriefToolError(
                f"итог не подтверждён: политика сменилась во время approve "
                f"({before} → {after}); подпись могла быть записана — повторите"
            )
        _judge_approve(reply, target, act)
        print("подписано; следующий шаг:")
        print("  " + _engineer_command(state, pr, target))
        return target


def _file_self_hash(path: Path) -> str | None:
    """self-hash брифа в файле; не бриф (нет frontmatter, не UTF-8) — None.

    None не совпадает ни с одним хэшем акта — вызывающий даёт свой названный
    отказ, а не трейсбек (ревью #573).
    """
    try:
        return discovery_approval.self_hash(path.read_text(encoding="utf-8"))
    except (discovery_approval.NotABrief, UnicodeDecodeError):
        return None


def _judge_approve(reply, target: Path, act: brief_provenance.Act) -> None:
    """Код вызова — подсказка для текста; истина — перечитанный файл (§11.5 п.5)."""
    reason = reply.envelope.get("operation", {}).get("reason", "")
    if reply.code == 1:
        raise BriefToolError(f"итог approve неизвестен: {reason}", retry=True)
    if reply.code == 2:
        if reason == "brief_bytes_diverged":
            raise BriefToolError(
                "approve: байты разошлись со смерженными — файл откатан в draft"
            )
        raise BriefToolError(f"approve отказал ({reason}) — файл не тронут")
    if reply.code in (10, 11):
        axis = "линтер отклоняет бриф" if reply.code == 10 else "readiness=incomplete"
        raise BriefToolError(
            f"approve вернул {reply.code}: подпись записана, но {axis} — engineer "
            "такой upstream не примет"
        )
    data = target.read_bytes()
    refusal = brief_provenance.check_operator_brief(act, data.decode("utf-8"))
    if refusal is not None:
        raise BriefToolError(
            f"approve вернул 0, но конверт не зеркалит мерж: {refusal.detail}"
        )
    try:
        brief_input.check_customer_upstream(target, data)
    except brief_input.BriefInputError as exc:
        raise BriefToolError(f"подписанный бриф не годится в upstream: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    """CLI обёрток brief-маршрута."""
    parser = argparse.ArgumentParser(prog="brief_tools")
    sub = parser.add_subparsers(dest="command", required=True)
    p_propose = sub.add_parser("propose", help="brief-PR из customer-прогона")
    p_propose.add_argument("--run", required=True)
    p_approve = sub.add_parser("approve", help="зеркало мержа brief-PR")
    p_approve.add_argument("--run", required=True)
    p_approve.add_argument("--pr", required=True, type=int)
    args = parser.parse_args(argv)
    from governance.ops import RealOps

    ops = RealOps()
    try:
        if args.command == "propose":
            propose(args.run, ops)
            return 0
        if args.command == "approve":
            approve(args.run, args.pr, ops)
            return 0
    except rl.LockBusy as exc:
        print(f"brief-tools: {exc}", file=sys.stderr)
        return 1
    except BriefToolError as exc:
        print(f"brief-tools: {exc}", file=sys.stderr)
        return 2 if exc.retry else 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
