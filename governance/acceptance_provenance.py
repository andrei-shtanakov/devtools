"""acceptance_provenance — происхождение штампа для гейта [x] (спека §7.2a п.6).

Зелёный только при установленном акте, связанном с ЭТИМ закрытием:
штамп = подписанное предложение (в accepted_merge) с разрешёнными правками;
PR accepted_pr этого репо влит в default, его мерж-коммит = accepted_merge,
правит ровно этот файл; политика — из доверенного источника по пину
предложения; нужен ли человек — по графу на bundle_pin; подписант
предложения — человек из снимка (или, только test, агент). Любой
неустановленный факт — находка (красный).
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
from pathlib import Path
from typing import Protocol

from governance import criteria_accept, criteria_graph, policy_rule
from governance.frontmatter import split_frontmatter

#: Учётка агента. Здесь, а не из `ops.py`: модуль и всё, что решает правило
#: гейта, — под authority-root (решение владельца 2026-10-02); `ops.py` туда
#: не входит, и константа в нём ослаблялась бы агентским PR.
AGENT_LOGIN = "ai-prosto"

CLOSURE_NAME = "90-acceptance-closure.md"
_NODES = ("10-requirements.md", "15-behaviour-spec.md", "25-acceptance.md")
_SOURCE = re.compile(r"^github:(?P<repo>[^@]+)@(?P<sha>[0-9a-f]{40}):(?P<path>.+)$")
_SHA = re.compile(r"^[0-9a-f]{40}$")  # ссылки из файла — только SHA, не опции git


class Forge(Protocol):
    """Факты форджи для гейта; None — не установлено."""

    def pr_facts(self, slug: str, pr: int) -> dict | None: ...

    def pr_files(self, slug: str, pr: int) -> list[str] | None: ...

    def default_branch(self, slug: str) -> str | None: ...

    def policy_file(self, repo: str, sha: str, path: str) -> str | None: ...

    def policy_on_ref(self, repo: str, sha: str, ref: str) -> bool | None: ...


def _gh(*args: str) -> str | None:
    try:
        done = subprocess.run(
            ["gh", *args], capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    return done.stdout if done.returncode == 0 else None


def _gh_json(*args: str) -> object | None:
    out = _gh(*args)
    try:
        return json.loads(out) if out is not None else None
    except json.JSONDecodeError:
        return None


class RealForge:
    """Форджа через `gh` (в CI — `GH_TOKEN`); любой сбой — None. Собственные
    вызовы, не `ops.py`: зависимости правила гейта — под authority-root."""

    def pr_facts(self, slug: str, pr: int) -> dict | None:
        v = _gh_json(
            "pr",
            "view",
            str(pr),
            "-R",
            slug,
            "--json",
            "state,baseRefName,mergeCommit,mergedBy",
        )
        return v if isinstance(v, dict) else None

    def pr_files(self, slug: str, pr: int) -> list[str] | None:
        v = _gh_json("pr", "view", str(pr), "-R", slug, "--json", "files")
        files = v.get("files") if isinstance(v, dict) else None
        if not isinstance(files, list):
            return None
        paths = [f.get("path") for f in files if isinstance(f, dict)]
        return paths if all(isinstance(p, str) for p in paths) else None

    def default_branch(self, slug: str) -> str | None:
        v = _gh_json("repo", "view", slug, "--json", "defaultBranchRef")
        ref = v.get("defaultBranchRef") if isinstance(v, dict) else None
        name = ref.get("name") if isinstance(ref, dict) else None
        return name if isinstance(name, str) and name else None

    def policy_file(self, repo: str, sha: str, path: str) -> str | None:
        v = _gh_json("api", f"repos/{repo}/contents/{path}?ref={sha}")
        if not isinstance(v, dict) or v.get("encoding") != "base64":
            return None
        try:
            return base64.b64decode(str(v.get("content", ""))).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None

    def policy_on_ref(self, repo: str, sha: str, ref: str) -> bool | None:
        """SHA политики — в истории `ref`. GitHub compare `BASE...HEAD` даёт
        статус HEAD относительно BASE: при BASE=sha, HEAD=ref «sha — предок
        ref» ⇔ `ahead`/`identical` (ревью круга 5 R5-M1: было перевёрнуто)."""
        v = _gh_json("api", f"repos/{repo}/compare/{sha}...{ref}?per_page=1")
        status = v.get("status") if isinstance(v, dict) else None
        if status in ("ahead", "identical"):
            return True
        if status in ("behind", "diverged"):
            return False
        return None


def _show(repo: Path, ref: str, path: str) -> str | None:
    try:
        # text=True — universal newlines, как у `read_text` в `pin_current`
        # (ревью круга 6 m6-2: иначе CRLF-узел давал ложный красный)
        done = subprocess.run(
            ["git", "-C", str(repo), "show", f"{ref}:{path}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except UnicodeDecodeError:  # R5 m-c: находка, не трейсбек
        return None
    return done.stdout if done.returncode == 0 else None


def pin_current(repo: Path, spec_dir: str, bundle_pin: object) -> bool | None:
    """Узлы на пине = узлам проверяемой ревизии (рабочее дерево гейта).

    Пин пишет автор файла (для test-only — агент): без этой сверки старый
    test-only пин или устаревший штамп прятали бы ручные критерии текущего
    бандла (ревью круга 4 R4-B1). None — не прочитано."""
    if not isinstance(bundle_pin, str) or not _SHA.match(bundle_pin):
        return None
    for n in _NODES:
        at_pin = _show(repo, bundle_pin, f"{spec_dir}/{n}")
        here = repo / spec_dir / n
        if at_pin is None or not here.is_file():
            return None
        try:
            current = here.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return None
        if at_pin != current:
            return False
    return True


def human_needed(repo: Path, spec_dir: str, bundle_pin: object) -> bool | None:
    """Нужен ли человек — по графу узлов на пине (§7.2a п.1); None — не прочитан."""
    if not isinstance(bundle_pin, str) or not _SHA.match(bundle_pin):
        return None
    texts = [_show(repo, bundle_pin, f"{spec_dir}/{n}") for n in _NODES]
    if any(t is None for t in texts):
        return None
    req, beh, acc = (t or "" for t in texts)
    return criteria_graph.human_criteria(criteria_graph.build_graph(req, beh, acc)) > 0


def _trusted_accounts(source: object, forge: Forge) -> frozenset[str] | None:
    """Состав политики по пину предложения — только из SSOT-источника."""
    m = _SOURCE.match(source) if isinstance(source, str) else None
    if m is None:
        return None
    try:
        repo, _ref, path = policy_rule.policy_source()
    except RuntimeError:
        return None
    if (m["repo"], m["path"]) != (repo, path):
        return None
    if forge.policy_on_ref(repo, m["sha"], _ref) is not True:
        return None
    content = forge.policy_file(repo, m["sha"], path)
    return policy_rule.policy_accounts(content) if content is not None else None


def stamp_findings(
    repo: Path,
    spec_dir: str,
    text: str,
    *,
    slug: str | None,
    forge: Forge | None,
    agent: str = AGENT_LOGIN,
) -> list[str]:
    """Пусто — штамп доказан актом, связанным с этим закрытием.

    `agent` — константа учётки агента (`AGENT_LOGIN`); предикат
    `criteria_close` берёт её из профиля (`ops.agent_login()`). Разойтись они
    могут только в ложный красный, не в зелёный (ревью круга 4 R4-m7)."""
    rel = f"{spec_dir}/{CLOSURE_NAME}"
    try:
        meta, _ = split_frontmatter(text)
    except ValueError:
        return ["frontmatter штампа не разбирается"]
    merge, pr = meta.get("accepted_merge"), meta.get("accepted_pr")
    if not isinstance(merge, str) or not _SHA.match(merge) or not isinstance(pr, int):
        return ["штамп без accepted_merge (SHA) / accepted_pr"]
    proposal = _show(repo, merge, rel)
    if proposal is None:
        return [f"предложение в {merge[:12]} не прочитано (история git?)"]
    try:
        expected = criteria_accept.stamp_text(proposal, merge_oid=merge, pr=pr)
    except ValueError:
        return [f"в {merge[:12]} не предложение (status ≠ proposed)"]
    if expected != text:
        return ["штамп ≠ подписанному предложению (правка после подписи?)"]
    if slug is None or forge is None:
        return ["форджа/репозиторий не установлены — происхождение не проверить"]
    facts = forge.pr_facts(slug, pr)
    files = forge.pr_files(slug, pr)
    default = forge.default_branch(slug)
    if facts is None or files is None or default is None:
        return [f"факты PR #{pr} не получены"]
    commit = facts.get("mergeCommit")
    oid = commit.get("oid") if isinstance(commit, dict) else None
    out = [
        why
        for bad, why in (
            (facts.get("state") != "MERGED", f"PR #{pr} не влит"),
            (facts.get("baseRefName") != default, f"PR #{pr} влит не в {default}"),
            (oid != merge, f"accepted_merge ≠ мерж-коммиту PR #{pr}"),
            (files != [rel], f"PR #{pr} правит не только {rel}: {files}"),
        )
        if bad
    ]
    if out:
        return out
    pmeta, _ = split_frontmatter(proposal)
    current = pin_current(repo, spec_dir, pmeta.get("bundle_pin"))
    if current is None:
        return ["узлы бандла на пине предложения не прочитаны"]
    if not current:
        return ["штамп не для текущего бандла: узлы на пине ≠ узлам ревизии"]
    accounts = _trusted_accounts(pmeta.get("policy_source"), forge)
    if accounts is None:
        return ["политика по пину предложения не прочитана из доверенного источника"]
    need = human_needed(repo, spec_dir, pmeta.get("bundle_pin"))
    if need is None:
        return ["граф бандла на пине не прочитан"]
    by = facts.get("mergedBy")
    login = by.get("login") if isinstance(by, dict) else None
    if not isinstance(login, str) or not login:
        return [f"подписант PR #{pr} не установлен"]
    if need and (login == agent or login not in accounts):
        return [f"PR #{pr} влит {login} — не человек из снимка политики"]
    if not need and login != agent and login not in accounts:
        return [f"PR #{pr} влит {login} — ни агент, ни учётка снимка"]
    return []
