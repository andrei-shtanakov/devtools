"""Происхождение upstream engineer-маршрута — только факты форджа (§11.4.2–§11.4.3).

`read_act` выводит акт из brief-PR: форма изменений, заявка и бриф из
merge-коммита, пин политики акта `P`. `check_operator_brief` сверяет байты
файла (оператора или durable-копии) с актом. `check_policy` вычисляет рабочую
версию `W` из человеческих комментариев-подтверждений и сравнивает её с
актуальной `C`. Ни `run.json`, ни локальный git источником не служат (§11.1.2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

import yaml

from governance import approval_facts, approval_request, discovery_approval, policy_rule
from governance.approval_request import ApprovalRequest
from governance.brief_facts import BriefPrFacts
from governance.facts import Outcome

RECONFIRM_PREFIX = "policy-reconfirm: "
_SHA = re.compile(r"\A[0-9a-f]{40}\Z")


@dataclass(frozen=True)
class Act:
    """Акт одобрения брифа, выведенный из форджа."""

    repo: str
    pr: int
    dir: str
    merge_commit: str
    merged_by: str
    merged_at: str
    brief_self_hash: str
    act_policy_sha: str
    request: ApprovalRequest

    def as_record(self) -> dict[str, object]:
        """Запись для `interview.approval` — отчёт, не источник доверия."""
        return {
            "repo": self.repo,
            "pr": self.pr,
            "dir": self.dir,
            "merge_commit": self.merge_commit,
            "approver": self.merged_by,
            "approved_at": self.merged_at,
            "self_hash": self.brief_self_hash,
            "act_policy_sha": self.act_policy_sha,
        }


@dataclass(frozen=True)
class Refusal:
    """Отказ предиката; `retry` — факт не установлен («повторите»)."""

    reason: str
    detail: str
    retry: bool = False


@dataclass(frozen=True)
class PolicyOk:
    """Политика допускает продолжение: актуальная `current` == рабочей `working`."""

    current: str
    working: str


def _unavailable(detail: str) -> Refusal:
    return Refusal(
        "forge_unavailable", f"факт форджа не установлен — повторите: {detail}", True
    )


def reconfirm_line(policy_repo: str, sha: str) -> str:
    """Ровно та строка, которую человек оставляет комментарием (§11.4.3)."""
    return f"{RECONFIRM_PREFIX}{policy_repo}@{sha}"


def files_dir(facts: BriefPrFacts) -> str | Refusal:
    """Каталог `…/00-discovery`, если изменения PR — ровно бриф и заявка (`added`)."""
    paths = [PurePosixPath(p) for p, _ in facts.files]
    dirs = {str(p.parent) for p in paths}
    if (
        len(facts.files) != 2
        or {s for _, s in facts.files} != {"added"}
        or len(dirs) != 1
        or {p.name for p in paths}
        != {approval_request.BRIEF, approval_request.FILE_NAME}
        or PurePosixPath(next(iter(dirs))).name != "00-discovery"
    ):
        return Refusal(
            "pr_files",
            f"изменения PR {list(facts.files)} ≠ бриф + заявка в одном …/00-discovery",
        )
    return next(iter(dirs))


def _version_refusal(ops, sha: str) -> Refusal | None:
    """`sha` — версия политики из SSOT-источника (история ref, менял файл)."""
    repo, ref, path = policy_rule.policy_source()
    fact = ops.policy_version_fact_at(repo, ref, path, sha)
    if fact.outcome is Outcome.UNAVAILABLE:
        return _unavailable(fact.detail)
    if fact.value is not True:
        return Refusal("not_policy_version", f"{sha} — не версия {repo}:{path}@{ref}")
    return None


def _accounts(ops, sha: str) -> frozenset[str] | Refusal:
    """Состав версии `sha` (историческое чтение по SHA, §11.4.3)."""
    repo, _, path = policy_rule.policy_source()
    fact = ops.repo_file_fact(repo, sha, path)
    if fact.outcome is Outcome.UNAVAILABLE:
        return _unavailable(fact.detail)
    accounts = (
        policy_rule.policy_accounts(fact.value)
        if fact.outcome is Outcome.FOUND and isinstance(fact.value, str)
        else None
    )
    if accounts is None:
        return Refusal("not_policy_version", f"политика {sha}: состав не читается")
    return accounts


def _read_merged(ops, repo: str, sha: str, path: str) -> str | Refusal:
    fact = ops.repo_file_fact(repo, sha, path)
    if fact.outcome is Outcome.UNAVAILABLE:
        return _unavailable(fact.detail)
    if fact.outcome is not Outcome.FOUND or not isinstance(fact.value, str):
        return Refusal("pr_files", f"нет {path}@{sha}")
    return fact.value


def read_act(ops, repo: str, pr: int) -> Act | Refusal:
    """§11.4.2 п.3–5 и п.7: акт из brief-PR, без файла оператора."""
    pr_fact = ops.brief_pr_fact(repo, pr)
    if pr_fact.outcome is Outcome.UNAVAILABLE:
        return _unavailable(pr_fact.detail)
    facts: BriefPrFacts = pr_fact.value
    if facts.state != "MERGED" or not facts.merge_commit:
        return Refusal("pr_not_merged", f"PR #{pr}: {facts.state}")
    default = ops.default_branch_fact(repo)
    if default.outcome is Outcome.UNAVAILABLE:
        return _unavailable(default.detail)
    if facts.base_ref != default.value.name:
        return Refusal(
            "pr_wrong_base",
            f"PR #{pr} смержен в {facts.base_ref}, не в {default.value.name}",
        )
    dir_ = files_dir(facts)
    if isinstance(dir_, Refusal):
        return dir_
    req_text = _read_merged(
        ops, repo, facts.merge_commit, f"{dir_}/{approval_request.FILE_NAME}"
    )
    if isinstance(req_text, Refusal):
        return req_text
    brief_text = _read_merged(
        ops, repo, facts.merge_commit, f"{dir_}/{approval_request.BRIEF}"
    )
    if isinstance(brief_text, Refusal):
        return brief_text
    try:
        req = approval_request.parse(req_text)
    except approval_request.RequestError as exc:
        return Refusal("request_invalid", str(exc))
    if (
        req.policy_repo,
        req.policy_ref,
        req.policy_path,
    ) != policy_rule.policy_source():
        return Refusal("request_coordinates", "координаты политики заявки ≠ SSOT")
    try:
        merged_hash = discovery_approval.self_hash(brief_text)
    except discovery_approval.NotABrief as exc:
        return Refusal("brief_hash", f"бриф в merge-коммите не бриф: {exc}")
    if merged_hash != req.brief_self_hash:
        return Refusal("brief_hash", "self-hash брифа в коммите ≠ заявке")
    refusal = _version_refusal(ops, req.policy_sha)
    if refusal is not None:
        return refusal
    accounts = _accounts(ops, req.policy_sha)
    if isinstance(accounts, Refusal):
        return accounts
    if facts.merged_by not in accounts:
        return Refusal(
            "merger_not_in_act_policy",
            f"{facts.merged_by} ∉ политики акта {req.policy_sha}",
        )
    return Act(
        repo=repo,
        pr=pr,
        dir=dir_,
        merge_commit=facts.merge_commit,
        merged_by=str(facts.merged_by),
        merged_at=str(facts.merged_at),
        brief_self_hash=merged_hash,
        act_policy_sha=req.policy_sha,
        request=req,
    )


def _frontmatter(text: str) -> dict:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    try:
        meta = yaml.safe_load(text[4:end]) if end != -1 else None
    except yaml.YAMLError:
        return {}
    return meta if isinstance(meta, dict) else {}


def check_operator_brief(act: Act, text: str) -> Refusal | None:
    """§11.4.2 п.1 (CR), п.2 (честный конверт) и п.5–6 (связь с актом)."""
    if "\r" in text:
        return Refusal("operator_brief", "CR в файле: нужен LF")
    try:
        own = discovery_approval.self_hash(text)
    except discovery_approval.NotABrief as exc:
        return Refusal("operator_brief", f"не бриф: {exc}")
    debt = discovery_approval.verify(text)
    if debt is not None:
        return Refusal("operator_brief", f"подпись не честная ({debt})")
    if own != act.brief_self_hash:
        return Refusal("operator_brief", "self-hash файла ≠ смерженному брифу")
    meta = _frontmatter(text)
    if (
        meta.get("approver") != act.merged_by
        or meta.get("approved_at") != act.merged_at
    ):
        return Refusal("envelope_mismatch", "конверт не зеркалит событие мержа")
    return None


def _candidate_sha(body: str, policy_repo: str) -> str | None:
    """SHA из тела, если тело — ровно строка подтверждения; иначе None."""
    prefix = f"{RECONFIRM_PREFIX}{policy_repo}@"
    sha = body[len(prefix) :] if body.startswith(prefix) else ""
    if _SHA.match(sha) and body == reconfirm_line(policy_repo, sha):
        return sha
    return None


def working_version(ops, act: Act) -> str | Refusal:
    """`W`: SHA последнего действительного подтверждения, иначе `P` (§11.4.3).

    Недействительное подтверждение (чужая строка, правка, до мержа, не
    версия, автор вне подтверждаемой политики) пропускается; неустановленный
    факт о нём — `Refusal(retry=True)`, а не «подтверждения нет».
    """
    fact = ops.pr_comments_fact(act.repo, act.pr)
    if fact.outcome is Outcome.UNAVAILABLE:
        return _unavailable(fact.detail)
    policy_repo = policy_rule.policy_source()[0]
    working = act.act_policy_sha
    for comment in sorted(fact.value, key=lambda c: c.created_at):
        sha = _candidate_sha(comment.body, policy_repo)
        if (
            sha is None
            or comment.last_edited_at is not None
            or comment.created_at <= act.merged_at
        ):
            continue
        refusal = _version_refusal(ops, sha)
        if refusal is not None:
            if refusal.retry:
                return refusal
            continue
        accounts = _accounts(ops, sha)
        if isinstance(accounts, Refusal):
            if accounts.retry:
                return accounts
            continue
        if comment.author in accounts:
            working = sha
    return working


def check_policy(ops, act: Act) -> PolicyOk | Refusal:
    """§11.4.3 п.2–5: `W` из комментариев, `C` — актуальная, мержер в `C`."""
    snapshot = approval_facts.policy_snapshot(ops, pinned_sha=None)
    if snapshot.outcome is Outcome.UNAVAILABLE:
        return _unavailable(snapshot.detail)
    if snapshot.outcome is not Outcome.FOUND:
        return Refusal(
            "policy_forbidden", f"политика не установлена: {snapshot.detail}"
        )
    current = snapshot.value.sha
    working = working_version(ops, act)
    if isinstance(working, Refusal):
        return working
    if current != working:
        policy_repo = policy_rule.policy_source()[0]
        return Refusal(
            "upstream_policy_drift",
            f"политика сменилась: акт {act.act_policy_sha}, рабочая {working}, "
            f"актуальная {current}; подтверждение — комментарий человека из "
            f"политики в PR #{act.pr}: {reconfirm_line(policy_repo, current)}",
        )
    if act.merged_by not in snapshot.value.accounts:
        return Refusal(
            "merger_not_in_current_policy", f"{act.merged_by} ∉ политики {current}"
        )
    return PolicyOk(current, working)
