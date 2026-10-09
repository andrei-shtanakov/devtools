"""Факты форджа для brief-PR, комментариев и версий политики (спека §11.4.6).

Каждый факт — `Fact`: неполнота (страница без `pageInfo`, курсор без
продвижения, голова PR сменилась между страницами, список упёрся в лимит)
даёт `UNAVAILABLE`, а не «то, что успели прочитать». Поля по состояниям PR:
`merged_*` обязательны у `MERGED` и пусты у `OPEN`/`CLOSED`.

Методы — миксин `BriefFactsMixin` к `RealOps` и к узкому проверяльщику
brief-PR `brief_merge_check` (его исполняет `human-merge.sh`): модуль
самодостаточен — только `subprocess` (`gh`) и `facts`, без `ops.py`.
Поэтому он под authority-root и харнесс-гвардом (ревью #573).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any

from governance.facts import Fact, Outcome, unavailable

PR_STATES = ("OPEN", "CLOSED", "MERGED")
#: Лимит `gh pr list`: ответ ровно такой длины не доказывает полноты.
PR_LIST_LIMIT = 100
#: Потолок файлов в REST compare: ответ такой длины не доказывает полноты.
COMPARE_FILES_CAP = 300

_BRIEF_PR_QUERY = (
    "query($o:String!,$n:String!,$p:Int!,$c:String){"
    "repository(owner:$o,name:$n){pullRequest(number:$p){"
    "state baseRefName headRefName headRefOid mergedAt "
    "mergedBy{login} mergeCommit{oid} "
    "files(first:100,after:$c){nodes{path changeType} "
    "pageInfo{hasNextPage endCursor}}}}}"
)
_PR_COMMENTS_QUERY = (
    "query($o:String!,$n:String!,$p:Int!,$c:String){"
    "repository(owner:$o,name:$n){pullRequest(number:$p){headRefOid "
    "comments(first:100,after:$c){nodes{id author{login} body createdAt "
    "lastEditedAt} pageInfo{hasNextPage endCursor}}}}}"
)
_DEFAULT_BRANCH_QUERY = (
    "query($o:String!,$n:String!){repository(owner:$o,name:$n){"
    "defaultBranchRef{name target{oid}}}}"
)
#: Последний коммит, тронувший `path`, в истории от `sha` включительно.
_TOUCHED_QUERY = (
    "query($o:String!,$n:String!,$s:GitObjectID!,$p:String!){"
    "repository(owner:$o,name:$n){object(oid:$s){"
    "... on Commit{history(first:1,path:$p){nodes{oid}}}}}}"
)


@dataclass(frozen=True)
class BriefPrFacts:
    """Факты PR, которые судят brief-PR (§11.4.2 п.3, §11.3 п.4)."""

    number: int
    state: str
    base_ref: str
    head_ref: str
    head_sha: str
    files: tuple[tuple[str, str], ...]
    merged_by: str | None
    merged_at: str | None
    merge_commit: str | None


@dataclass(frozen=True)
class PrComment:
    """Комментарий PR; `last_edited_at` — `None`, только если правок не было."""

    id: str
    author: str
    body: str
    created_at: str
    last_edited_at: str | None


@dataclass(frozen=True)
class DefaultBranch:
    """Ветка по умолчанию и её неизменяемая голова на момент чтения."""

    name: str
    sha: str


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _gh(args: list[str]) -> str | None:
    done = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    return done.stdout if done.returncode == 0 else None


#: Политика подписи §I12 (спека approval-policy §4.1): версия — последний
#: коммит ветки, тронувший путь; содержимое — по `object(oid:)` +
#: `file(path:)`, где «коммита нет» и «пути нет» — два разных положительных
#: отсутствия (REST 404 их сливает с недоступностью).
_POLICY_VERSION_QUERY = (
    "query($o:String!,$n:String!,$q:String!,$p:String!){"
    "repository(owner:$o,name:$n){ref(qualifiedName:$q){target{"
    "... on Commit{history(first:1,path:$p){nodes{oid}}}}}}}"
)
_REPO_FILE_QUERY = (
    "query($o:String!,$n:String!,$s:GitObjectID!,$p:String!){"
    "repository(owner:$o,name:$n){object(oid:$s){"
    "... on Commit{file(path:$p){object{"
    "... on Blob{text isBinary isTruncated}}}}}}}"
)


class BriefFactsMixin:
    """Факты форджа brief-маршрута поверх `_graphql_repository` хоста."""

    def _graphql_repository(self, query: str, **variables: str) -> dict | None:
        """`data.repository` ответа GraphQL либо None на ЛЮБОЙ сбой.

        `-F` для всех переменных, как у `remote_branch_head_fact`: строки
        (в т.ч. `GitObjectID`) уходят как есть, конвертируются только
        `true/false/null/целые`.
        """
        argv = ["gh", "api", "graphql", "-f", f"query={query}"]
        for key, value in variables.items():
            argv += ["-F", f"{key}={value}"]
        done = subprocess.run(argv, capture_output=True, text=True, check=False)
        if done.returncode != 0:
            return None
        try:
            repository = json.loads(done.stdout)["data"]["repository"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return None
        return repository if isinstance(repository, dict) else None

    def policy_version_fact(self, repo_slug: str, branch: str, path: str) -> Fact[str]:
        """SHA последнего коммита `branch`, тронувшего `path` (спека S5).

        `ref: null` — ветки нет (ABSENT); пустая история — файла по пути
        никогда не было (ABSENT); коммит удаления файла история включает —
        отсутствие тогда ловит `repo_file_fact`. Любая иная форма — UNAVAILABLE.
        """
        owner, name = repo_slug.split("/", 1)
        repository = self._graphql_repository(
            _POLICY_VERSION_QUERY,
            o=owner,
            n=name,
            q=f"refs/heads/{branch}",
            p=path,
        )
        what = f"версия {repo_slug}:{path}@{branch}"
        if repository is None or "ref" not in repository:
            return unavailable(f"{what}: запрос не удался")
        ref = repository["ref"]
        if ref is None:
            return Fact(Outcome.ABSENT, None, f"ветки {branch} в {repo_slug} нет")
        try:
            nodes = ref["target"]["history"]["nodes"]
        except (KeyError, TypeError):
            return unavailable(f"{what}: неожиданная форма ответа")
        if not isinstance(nodes, list):
            return unavailable(f"{what}: неожиданная форма истории")
        if not nodes:
            return Fact(
                Outcome.ABSENT,
                None,
                f"{path} в {repo_slug}@{branch} никогда не было",
            )
        oid = nodes[0].get("oid") if isinstance(nodes[0], dict) else None
        if not isinstance(oid, str) or not oid:
            return unavailable(f"{what}: пустой SHA")
        return Fact(Outcome.FOUND, oid, f"{path}: последний коммит {oid}")

    def repo_file_fact(self, repo_slug: str, sha: str, path: str) -> Fact[str]:
        """Текст `path` в коммите `sha`: FOUND / ABSENT / UNAVAILABLE."""
        owner, name = repo_slug.split("/", 1)
        repository = self._graphql_repository(
            _REPO_FILE_QUERY, o=owner, n=name, s=sha, p=path
        )
        what = f"{repo_slug}@{sha}:{path}"
        if repository is None or "object" not in repository:
            return unavailable(f"{what}: запрос не удался")
        commit = repository["object"]
        if commit is None:
            return Fact(Outcome.ABSENT, None, f"коммита {sha} в {repo_slug} нет")
        # Отсутствующее поле `file` — неполный ответ (или объект не коммит),
        # а не «файла нет»: только явный `file: null` — установленное
        # отсутствие (ревью плана engineer-маршрута, A8).
        if not isinstance(commit, dict) or "file" not in commit:
            return unavailable(f"{what}: ответ без поля file")
        entry = commit["file"]
        if entry is None:
            return Fact(Outcome.ABSENT, None, f"в {repo_slug}@{sha} нет {path}")
        blob = entry.get("object") if isinstance(entry, dict) else None
        # Полный текст доказан только явными `isBinary: false` и
        # `isTruncated: false` при строковом `text`: отсутствие поля, `null`
        # и иной тип — неполный ответ, не «текст целиком» (ревью плана, A10).
        if (
            not isinstance(blob, dict)
            or not isinstance(blob.get("text"), str)
            or blob.get("isBinary") is not False
            or blob.get("isTruncated") is not False
        ):
            return unavailable(f"{what}: содержимое не прочитано")
        text = blob["text"]
        return Fact(Outcome.FOUND, text, f"{path}@{sha} прочитан")

    def _pr_pages(
        self, query: str, key: str, repo_slug: str, pr: int
    ) -> tuple[dict, list[Any]] | str:
        """Все страницы списка `key` PR; строка — причина неполноты."""
        owner, name = repo_slug.split("/", 1)
        nodes: list[Any] = []
        first: dict | None = None
        cursor: str | None = None
        while True:
            variables = {"o": owner, "n": name, "p": str(pr)}
            if cursor is not None:
                variables["c"] = cursor
            repository = self._graphql_repository(query, **variables)
            node = (repository or {}).get("pullRequest")
            block = node.get(key) if isinstance(node, dict) else None
            if not isinstance(block, dict) or not isinstance(block.get("nodes"), list):
                return "запрос не удался или ответ без списка"
            info = block.get("pageInfo")
            if not isinstance(info, dict) or not isinstance(
                info.get("hasNextPage"), bool
            ):
                return "страница без pageInfo — полнота не доказана"
            if first is None:
                first = node
            elif node.get("headRefOid") != first.get("headRefOid"):
                return "голова PR сменилась между страницами"
            nodes.extend(block["nodes"])
            if not info["hasNextPage"]:
                return first, nodes
            nxt = info.get("endCursor")
            if not _nonempty(nxt) or nxt == cursor:
                return "курсор не продвигается"
            cursor = nxt

    def brief_pr_fact(self, repo_slug: str, pr: int) -> Fact[BriefPrFacts]:
        """Факты PR по состояниям; форма, которой у состояния нет, — UNAVAILABLE."""
        what = f"PR {repo_slug}#{pr}"
        got = self._pr_pages(_BRIEF_PR_QUERY, "files", repo_slug, pr)
        if isinstance(got, str):
            return unavailable(f"{what}: {got}")
        node, files = got
        state = node.get("state")
        heads = (
            node.get("baseRefName"),
            node.get("headRefName"),
            node.get("headRefOid"),
        )
        merged_by, merge_commit = node.get("mergedBy"), node.get("mergeCommit")
        if not all(v is None or isinstance(v, dict) for v in (merged_by, merge_commit)):
            return unavailable(f"{what}: mergedBy/mergeCommit не объект")
        merged = (
            (merged_by or {}).get("login"),
            node.get("mergedAt"),
            (merge_commit or {}).get("oid"),
        )
        if state not in PR_STATES or not all(_nonempty(v) for v in heads):
            return unavailable(f"{what}: неожиданная форма (state={state!r})")
        if state == "MERGED" and not all(_nonempty(v) for v in merged):
            return unavailable(f"{what}: MERGED без полного события мержа")
        if state != "MERGED" and any(v is not None for v in merged):
            return unavailable(f"{what}: {state} с полями мержа")
        pairs: list[tuple[str, str]] = []
        for entry in files:
            path = entry.get("path") if isinstance(entry, dict) else None
            change = entry.get("changeType") if isinstance(entry, dict) else None
            if not _nonempty(path) or not _nonempty(change):
                return unavailable(f"{what}: битый узел файла")
            pairs.append((str(path), str(change).lower()))
        facts = BriefPrFacts(
            number=pr,
            state=str(state),
            base_ref=str(heads[0]),
            head_ref=str(heads[1]),
            head_sha=str(heads[2]),
            files=tuple(pairs),
            merged_by=merged[0],
            merged_at=merged[1],
            merge_commit=merged[2],
        )
        return Fact(Outcome.FOUND, facts, f"{what} прочитан")

    def pr_comments_fact(self, repo_slug: str, pr: int) -> Fact[list[PrComment]]:
        """Все комментарии PR; узел без поля `lastEditedAt` — UNAVAILABLE."""
        what = f"комментарии {repo_slug}#{pr}"
        got = self._pr_pages(_PR_COMMENTS_QUERY, "comments", repo_slug, pr)
        if isinstance(got, str):
            return unavailable(f"{what}: {got}")
        comments: list[PrComment] = []
        for c in got[1]:
            # Отсутствующее поле — неполный ответ (UNAVAILABLE); явный `null`
            # — установленный факт: `lastEditedAt: null` — правок не было,
            # `author: null` — учётка удалена (автор неизвестен, такое
            # подтверждение недействительно, но факт о нём установлен).
            if not isinstance(c, dict) or not {"lastEditedAt", "author"} <= set(c):
                return unavailable(f"{what}: узел без lastEditedAt/author")
            raw_author = c["author"]
            if raw_author is None:
                author = ""
            elif isinstance(raw_author, dict) and _nonempty(raw_author.get("login")):
                author = str(raw_author["login"])
            else:
                return unavailable(f"{what}: автор комментария не прочитан")
            edited = c["lastEditedAt"]
            if not all(_nonempty(c.get(k)) for k in ("id", "body", "createdAt")) or (
                edited is not None and not _nonempty(edited)
            ):
                return unavailable(f"{what}: битый узел комментария")
            comments.append(
                PrComment(
                    id=c["id"],
                    author=author,
                    body=c["body"],
                    created_at=c["createdAt"],
                    last_edited_at=edited,
                )
            )
        return Fact(Outcome.FOUND, comments, f"{len(comments)} комментариев")

    def find_brief_pr_fact(self, repo_slug: str, head_ref: str) -> Fact[list[int]]:
        """Номера PR (любое состояние) с головой `head_ref`."""
        out = _gh(
            [
                "pr",
                "list",
                "--repo",
                repo_slug,
                "--head",
                head_ref,
                "--state",
                "all",
                "--json",
                "number",
                "--limit",
                str(PR_LIST_LIMIT),
            ]
        )
        what = f"PR ветки {head_ref}"
        try:
            items = json.loads(out or "x")
        except json.JSONDecodeError:
            return unavailable(f"{what}: запрос не удался")
        if not isinstance(items, list) or not all(
            isinstance(i, dict) and type(i.get("number")) is int for i in items
        ):
            return unavailable(f"{what}: неожиданная форма ответа")
        numbers = sorted(int(i["number"]) for i in items)
        if len(numbers) >= PR_LIST_LIMIT:
            return unavailable(f"{what}: упёрлись в лимит {PR_LIST_LIMIT}")
        return Fact(Outcome.FOUND, numbers, f"{len(numbers)} PR")

    def default_branch_fact(self, repo_slug: str) -> Fact[DefaultBranch]:
        """Ветка по умолчанию и SHA её головы."""
        owner, name = repo_slug.split("/", 1)
        repository = self._graphql_repository(_DEFAULT_BRANCH_QUERY, o=owner, n=name)
        ref = (repository or {}).get("defaultBranchRef") or {}
        branch, sha = ref.get("name"), (ref.get("target") or {}).get("oid")
        if not _nonempty(branch) or not _nonempty(sha):
            return unavailable(f"ветка по умолчанию {repo_slug} не прочитана")
        return Fact(Outcome.FOUND, DefaultBranch(str(branch), str(sha)), "прочитана")

    def policy_version_fact_at(
        self, repo_slug: str, ref: str, path: str, sha: str
    ) -> Fact[bool]:
        """`sha` — версия политики: в истории `ref` (или равен голове) и менял `path`.

        `compare <sha>...<ref>`: `sha` — base, голова `ref` — head; base-предок
        даёт `ahead`, равенство — `identical`; `behind`/`diverged` — не версия.
        «Менял `path`»: последний коммит, тронувший `path`, в истории от `sha`
        включительно — сам `sha`.
        """
        out = _gh(
            ["api", f"repos/{repo_slug}/compare/{sha}...{ref}", "--jq", ".status"]
        )
        status = (out or "").strip()
        owner, name = repo_slug.split("/", 1)
        if status not in ("ahead", "identical", "behind", "diverged"):
            return self._sha_absent_or_unavailable(owner, name, sha, path, ref)
        if status in ("behind", "diverged"):
            return Fact(Outcome.FOUND, False, f"{sha} не в истории {ref} ({status})")
        repository = self._graphql_repository(
            _TOUCHED_QUERY, o=owner, n=name, s=sha, p=path
        )
        commit = (repository or {}).get("object")
        try:
            nodes = commit["history"]["nodes"]
        except (KeyError, TypeError):
            return unavailable(f"история {path} от {sha}: не прочитана")
        if not isinstance(nodes, list) or (
            nodes
            and not (isinstance(nodes[0], dict) and _nonempty(nodes[0].get("oid")))
        ):
            return unavailable(f"история {path} от {sha}: неожиданная форма")
        touched = (
            bool(nodes) and isinstance(nodes[0], dict) and nodes[0].get("oid") == sha
        )
        return Fact(
            Outcome.FOUND, touched, f"{sha} {'менял' if touched else 'не менял'} {path}"
        )

    def _sha_absent_or_unavailable(
        self, owner: str, name: str, sha: str, path: str, ref: str
    ) -> Fact[bool]:
        """compare не дал статуса: несуществующий `sha` (REST 404) — установленный
        факт «не версия», а не недоступность форджа (иначе чужой комментарий с
        выдуманным sha навсегда держал бы «повторите»). Различает GraphQL:
        `object: null` при прочитанном `repository` — коммита нет."""
        repository = self._graphql_repository(
            _TOUCHED_QUERY, o=owner, n=name, s=sha, p=path
        )
        if isinstance(repository, dict) and "object" in repository:
            if repository["object"] is None:
                return Fact(Outcome.FOUND, False, f"{sha}: такого коммита нет")
        return unavailable(f"compare {sha}...{ref}: не установлено")

    def compare_files_fact(
        self, repo_slug: str, base: str, head: str
    ) -> Fact[tuple[tuple[str, str], ...]]:
        """Файлы диапазона `base...head` (REST compare); у потолка — UNAVAILABLE."""
        out = _gh(
            [
                "api",
                f"repos/{repo_slug}/compare/{base}...{head}",
                "--jq",
                "[.files[] | [.filename, .status]]",
            ]
        )
        try:
            rows = json.loads(out or "x")
        except json.JSONDecodeError:
            return unavailable(f"compare {base}...{head}: не прочитан")
        if not isinstance(rows, list) or not all(
            isinstance(row, list) and len(row) == 2 and all(_nonempty(v) for v in row)
            for row in rows
        ):
            return unavailable(f"compare {base}...{head}: неожиданная форма")
        pairs = tuple((str(f), str(s)) for f, s in rows)
        if len(pairs) >= COMPARE_FILES_CAP:
            return unavailable(f"compare {base}...{head}: потолок {COMPARE_FILES_CAP}")
        return Fact(Outcome.FOUND, pairs, f"{len(pairs)} файлов")
