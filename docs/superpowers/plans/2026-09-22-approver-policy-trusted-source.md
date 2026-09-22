# Политика подписи §I12 из репозитория `approval-policy` — план имплементации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allowlist учёток, чей мерж создаёт подпись узла, читается из репозитория `andrei-shtanakov/approval-policy` по закреплённому commit SHA; окружение и CLI его не переопределяют; смена версии при живой заявке терминализирует её с причиной.

**Architecture:** Координаты источника — SSOT-файл под authority-root; два GraphQL-факта в `Ops` (версия по пути, содержимое по SHA); `approval_facts.policy_snapshot` собирает снимок с пятью машинными отказами; фаза 1 `approve_node` закрепляет снимок в заявке (`policy`), фаза 2 перечитывает по пину и сверяет; `human-merge.sh` сверяет логин и пин против того же источника через общий `ssot_env.sh`.

**Tech Stack:** Python 3.12, `uv run --frozen pytest`, `gh api graphql`, POSIX sh. Стенд `tests/test_governance_approve_node.py` (настоящий git + фейк-форджа), стаб `gh` в `tests/test_human_merge.py`.

**Spec:** `docs/superpowers/specs/2026-09-22-approver-policy-trusted-source-design.md` (spec converged; §9 — подтверждение владельцем уточнения D3).

## Global Constraints

- Репозиторий политики: `andrei-shtanakov/approval-policy`, ветка `main`, файл `policy/approvers.env`, ключ `AUTHORIZED_APPROVER_ACCOUNTS` (S1, S4).
- Версия = SHA последнего коммита `main`, тронувшего `policy/approvers.env` (S5).
- Переменная `AUTHORIZED_APPROVER_ACCOUNTS` в окружении — именованный отказ на любой фазе, заявка сохраняется (S7).
- Чтение политики — дефолтным профилем `gh` оператора, не `~/.config/review` (S9).
- Отпечаток политики: `POLICY_SCHEME = "v1"`, `v1:` + sha1 отсортированного перечня через запятую — не менять (§3.2).
- `Authorization.source` = `github:<repo>@<sha>:<path>` (§4.4).
- Заявка без поля `policy` на фазе 2 — `invalidated` (§4.6).
- Коммиты: сообщения по-русски, трейлеры `Epic: eco.dark-factory` и `Co-Authored-By` в финальном блоке.
- PR пары мержит **человек** (`human-merge.sh` и `contracts/authority-root/` в дифе).
- Тесты: `uv run --frozen pytest <файл> -q`; полный прогон ~10 минут.

---

### Task 1: Bootstrap репозитория политики (внешний эффект — только с подтверждением владельца)

**Files:**
- Внешний репозиторий `andrei-shtanakov/approval-policy` (создаётся `gh`)

**Interfaces:**
- Produces: репозиторий с `policy/approvers.env` и `README.md` на `main`; ruleset по S3; ноль соавторов.

- [ ] **Step 1: Получить явное подтверждение владельца.** Создание репозитория — внешний эффект; без слова владельца («создавай») задача не выполняется, остальные задачи плана от неё не зависят (тесты используют фейки).

- [ ] **Step 2: Создать репозиторий и файлы**

```bash
mkdir -p /tmp/approval-policy/policy && cd /tmp/approval-policy
cat > policy/approvers.env <<'EOF'
# Allowlist учёток, чей мерж candidate-PR создаёт подпись узла бандла (§I12).
# Меняет только владелец репозитория; devtools читает файл по commit SHA
# последнего коммита, тронувшего этот путь.
AUTHORIZED_APPROVER_ACCOUNTS=andrei-shtanakov
EOF
cat > README.md <<'EOF'
# approval-policy

Политика подписи узлов governance-бандла (§I12 devtools). Единственный
источник значения `AUTHORIZED_APPROVER_ACCOUNTS`.

- Меняет: владелец, PR в `main`. Соавторов у репозитория нет намеренно.
- Читает: devtools (`governance/approval_facts.policy_snapshot`,
  `human-merge.sh`) по SHA последнего коммита, тронувшего
  `policy/approvers.env`.
- Правило флота: `prograph-vault/authored/rules/approver-policy.md`.
EOF
git init -q -b main && git add -A && git commit -qm "policy: AUTHORIZED_APPROVER_ACCOUNTS=andrei-shtanakov"
gh repo create andrei-shtanakov/approval-policy --public --source . --push
```

- [ ] **Step 3: Ruleset `main` (S3)**

```bash
gh api -X POST repos/andrei-shtanakov/approval-policy/rulesets --input - <<'EOF'
{"name":"main","target":"branch","enforcement":"active",
 "conditions":{"ref_name":{"include":["refs/heads/main"],"exclude":[]}},
 "rules":[{"type":"deletion"},{"type":"non_fast_forward"},
          {"type":"required_linear_history"},
          {"type":"pull_request","parameters":{"required_approving_review_count":0,
           "dismiss_stale_reviews_on_push":false,"require_code_owner_review":false,
           "require_last_push_approval":false,"required_review_thread_resolution":false}}],
 "bypass_actors":[]}
EOF
```

- [ ] **Step 4: Проверить доступ и чтение**

```bash
gh api repos/andrei-shtanakov/approval-policy/collaborators --jq '.[].login'   # только andrei-shtanakov
gh api graphql -f query='query($o:String!,$n:String!,$q:String!,$p:String!){repository(owner:$o,name:$n){ref(qualifiedName:$q){target{... on Commit{history(first:1,path:$p){nodes{oid}}}}}}}' -F o=andrei-shtanakov -F n=approval-policy -F q=refs/heads/main -F p=policy/approvers.env --jq '.data.repository.ref.target.history.nodes[0].oid'
```
Expected: логин владельца; 40-символьный SHA.

---

### Task 2: SSOT координат источника под authority-root

**Files:**
- Create: `contracts/approval-policy-source/v1/source.env`
- Modify: `contracts/authority-root/v1/paths.env` (одна строка `AUTHORITY_ROOT_PREFIXES=`)
- Create: `ssot_env.sh` (общий shell-читатель; `merge-pr.sh` переводится на него)
- Modify: `merge-pr.sh` (функция `ssot_key` заменяется на `. "$script_dir/ssot_env.sh"`)
- Test: `tests/test_governance_authority_root.py`, `tests/test_merge_pr.py` (существующие тесты `ssot_key` должны остаться зелёными)

**Interfaces:**
- Produces: `contracts/approval-policy-source/v1/source.env` с ключами `APPROVAL_POLICY_REPO`, `APPROVAL_POLICY_REF`, `APPROVAL_POLICY_PATH`; shell-функция `ssot_key <file> <key> <what>` из `ssot_env.sh` (требует определённого `die`).

- [ ] **Step 1: Failing test — префикс под authority-root**

```python
# tests/test_governance_authority_root.py
def test_approval_policy_source_is_authority_root() -> None:
    """Координаты источника политики подписи агент не вправе перенаправить
    своим PR под агентским мержем (спека approval-policy S8)."""
    assert "contracts/approval-policy-source/" in authority_root.prefixes()
    assert authority_root.touched(
        ["contracts/approval-policy-source/v1/source.env"]
    ) == ["contracts/approval-policy-source/v1/source.env"]
```

- [ ] **Step 2: Run** `uv run --frozen pytest tests/test_governance_authority_root.py -q -k approval_policy_source` — FAIL (префикса нет).

- [ ] **Step 3: Создать SSOT и добавить префикс**

`contracts/approval-policy-source/v1/source.env`:
```
# Координаты источника политики подписи §I12 (спека 2026-09-22-approver-policy-trusted-source, S8).
# Файл — authority-root: PR, меняющий его, мержит человек.
APPROVAL_POLICY_REPO=andrei-shtanakov/approval-policy
APPROVAL_POLICY_REF=main
APPROVAL_POLICY_PATH=policy/approvers.env
```

`contracts/authority-root/v1/paths.env` — в конец значения `AUTHORITY_ROOT_PREFIXES` добавить ` contracts/approval-policy-source/`.

- [ ] **Step 4: Вынести `ssot_key` в `ssot_env.sh`**

Создать `ssot_env.sh` с содержимым функции `ssot_key` из `merge-pr.sh` (строки с комментарием «Вызывать ТОЛЬКО как …» по `printf '%s\n' "$_value"`) без изменений тела; в шапке файла:
```sh
# ssot_env.sh — shell-половина разбора SSOT KEY=VALUE (см. governance/ssot_env.py).
# Подключается `. "$script_dir/ssot_env.sh"` ПОСЛЕ определения `die`.
# Третьей копии формата быть не должно: python и этот файл — обе половины.
```
В `merge-pr.sh` на месте определения функции: `. "$script_dir/ssot_env.sh"`. `script_dir` в `merge-pr.sh` определяется ниже функции — перенести строку `script_dir=$(cd "$(dirname "$0")" && pwd)` выше подключения. Докстроки, называющие «`ssot_key` в `merge-pr.sh`» и «две половины» (`governance/ssot_env.py:3`, `tests/test_ssot_env_format.py:3`), — на `ssot_env.sh`; `tests/test_ssot_env_format.py` гоняет настоящий `merge-pr.sh --print-globs` и обязан остаться зелёным.

- [ ] **Step 5: Run** `uv run --frozen pytest tests/test_governance_authority_root.py tests/test_merge_pr.py -q` — PASS (включая существующий `test_every_harness_input_is_authority_root_but_the_kit`: разность `harness − authority` не меняется).

- [ ] **Step 6: Commit** `git add contracts/ ssot_env.sh merge-pr.sh tests/test_governance_authority_root.py && git commit -m "feat(governance): координаты источника политики — SSOT под authority-root; ssot_key в общий ssot_env.sh"`

---

### Task 3: Два GraphQL-факта форджи в `Ops`

**Files:**
- Modify: `governance/ops.py` (протокол `Ops` рядом с `remote_branch_head_fact`, строка 173; `RealOps` рядом с реализацией, строка 1259; константы запросов рядом с `_REMOTE_BRANCH_HEAD_QUERY`, строка 34)
- Test: `tests/test_governance_ops_policy_facts.py` (новый; `subprocess.run` подменяется)

**Interfaces:**
- Produces: `Ops.policy_version_fact(repo_slug: str, branch: str, path: str) -> Fact[str]` — `FOUND(sha)` / `ABSENT` (нет ветки или история по пути пуста) / `UNAVAILABLE`; `Ops.repo_file_fact(repo_slug: str, sha: str, path: str) -> Fact[str]` — `FOUND(text)` / `ABSENT` (нет коммита или пути) / `UNAVAILABLE` (сбой, бинарный, усечённый).

- [ ] **Step 1: Failing tests**

```python
# tests/test_governance_ops_policy_facts.py
"""GraphQL-факты политики подписи: отсутствие отличимо от недоступности."""
from __future__ import annotations

import json
import subprocess

from governance.facts import Outcome
from governance.ops import RealOps

REPO, BRANCH, PATH, SHA = "o/policy", "main", "policy/approvers.env", "a" * 40


def _gh(monkeypatch, payload: dict | None, rc: int = 0) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        out = json.dumps(payload) if payload is not None else ""
        return subprocess.CompletedProcess(argv, rc, stdout=out, stderr="boom" if rc else "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_version_found_is_last_commit_touching_the_path(monkeypatch) -> None:
    calls = _gh(monkeypatch, {"data": {"repository": {"ref": {"target": {"history": {"nodes": [{"oid": SHA}]}}}}}})
    fact = RealOps().policy_version_fact(REPO, BRANCH, PATH)
    assert fact.outcome is Outcome.FOUND and fact.value == SHA
    assert "-F" in calls[0] and f"p={PATH}" in calls[0], "история запрашивается ПО ПУТИ"


def test_version_absent_when_branch_missing_or_history_empty(monkeypatch) -> None:
    _gh(monkeypatch, {"data": {"repository": {"ref": None}}})
    assert RealOps().policy_version_fact(REPO, BRANCH, PATH).outcome is Outcome.ABSENT
    _gh(monkeypatch, {"data": {"repository": {"ref": {"target": {"history": {"nodes": []}}}}}})
    assert RealOps().policy_version_fact(REPO, BRANCH, PATH).outcome is Outcome.ABSENT


def test_version_unavailable_on_rc_or_odd_shape(monkeypatch) -> None:
    _gh(monkeypatch, None, rc=1)
    assert RealOps().policy_version_fact(REPO, BRANCH, PATH).outcome is Outcome.UNAVAILABLE
    _gh(monkeypatch, {"data": {"repository": None}})
    assert RealOps().policy_version_fact(REPO, BRANCH, PATH).outcome is Outcome.UNAVAILABLE


def test_file_found_absent_unavailable(monkeypatch) -> None:
    _gh(monkeypatch, {"data": {"repository": {"object": {"file": {"object": {"text": "K=v\n", "isBinary": False, "isTruncated": False}}}}}})
    fact = RealOps().repo_file_fact(REPO, SHA, PATH)
    assert fact.outcome is Outcome.FOUND and fact.value == "K=v\n"
    _gh(monkeypatch, {"data": {"repository": {"object": None}}})
    assert RealOps().repo_file_fact(REPO, SHA, PATH).outcome is Outcome.ABSENT
    _gh(monkeypatch, {"data": {"repository": {"object": {"file": None}}}})
    assert RealOps().repo_file_fact(REPO, SHA, PATH).outcome is Outcome.ABSENT
    _gh(monkeypatch, {"data": {"repository": {"object": {"file": {"object": {"text": None, "isBinary": True, "isTruncated": False}}}}}})
    assert RealOps().repo_file_fact(REPO, SHA, PATH).outcome is Outcome.UNAVAILABLE
```

- [ ] **Step 2: Run** `uv run --frozen pytest tests/test_governance_ops_policy_facts.py -q` — FAIL (`AttributeError`).

- [ ] **Step 3: Реализация**

Константы (рядом с `_REMOTE_BRANCH_HEAD_QUERY`):
```python
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
```

Протокол `Ops` (после `remote_branch_head_fact`):
```python
    def policy_version_fact(
        self, repo_slug: str, branch: str, path: str
    ) -> Fact[str]: ...

    def repo_file_fact(self, repo_slug: str, sha: str, path: str) -> Fact[str]: ...
```

`RealOps` (после `remote_branch_head_fact`):
```python
    def _graphql(self, query: str, **variables: str) -> dict | None:
        """`data.repository` ответа GraphQL либо None (любой сбой)."""
        argv = ["gh", "api", "graphql", "-f", f"query={query}"]
        for key, value in variables.items():
            argv += ["-F", f"{key}={value}"]
        done = subprocess.run(argv, capture_output=True, text=True)
        if done.returncode != 0:
            return None
        try:
            repository = json.loads(done.stdout)["data"]["repository"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return None
        return repository if isinstance(repository, dict) else None

    def policy_version_fact(
        self, repo_slug: str, branch: str, path: str
    ) -> Fact[str]:
        """SHA последнего коммита `branch`, тронувшего `path` (спека S5).

        `ref: null` — ветки нет; пустая история — файла по пути никогда не
        было; коммит удаления файла история включает — отсутствие тогда
        ловит `repo_file_fact`. Любая иная форма — UNAVAILABLE.
        """
        owner, name = repo_slug.split("/", 1)
        repository = self._graphql(
            _POLICY_VERSION_QUERY, o=owner, n=name, q=f"refs/heads/{branch}", p=path
        )
        if repository is None or "ref" not in repository:
            return unavailable(f"версия {repo_slug}:{path}@{branch}: запрос не удался")
        ref = repository["ref"]
        if ref is None:
            return Fact(Outcome.ABSENT, None, f"ветки {branch} в {repo_slug} нет")
        try:
            nodes = ref["target"]["history"]["nodes"]
        except (KeyError, TypeError):
            return unavailable(f"версия {repo_slug}:{path}@{branch}: неожиданная форма")
        if not nodes:
            return Fact(Outcome.ABSENT, None, f"{path} в {repo_slug}@{branch} никогда не было")
        oid = nodes[0].get("oid") if isinstance(nodes[0], dict) else None
        if not isinstance(oid, str) or not oid:
            return unavailable(f"версия {repo_slug}:{path}@{branch}: пустой SHA")
        return Fact(Outcome.FOUND, oid, f"{path}: последний коммит {oid}")

    def repo_file_fact(self, repo_slug: str, sha: str, path: str) -> Fact[str]:
        """Текст `path` в коммите `sha`: FOUND / ABSENT / UNAVAILABLE."""
        owner, name = repo_slug.split("/", 1)
        repository = self._graphql(_REPO_FILE_QUERY, o=owner, n=name, s=sha, p=path)
        if repository is None or "object" not in repository:
            return unavailable(f"{repo_slug}@{sha}:{path}: запрос не удался")
        commit = repository["object"]
        if commit is None:
            return Fact(Outcome.ABSENT, None, f"коммита {sha} в {repo_slug} нет")
        entry = commit.get("file") if isinstance(commit, dict) else None
        if entry is None:
            return Fact(Outcome.ABSENT, None, f"в {repo_slug}@{sha} нет {path}")
        blob = entry.get("object") if isinstance(entry, dict) else None
        text = blob.get("text") if isinstance(blob, dict) else None
        if not isinstance(text, str) or blob.get("isBinary") or blob.get("isTruncated"):
            return unavailable(f"{repo_slug}@{sha}:{path}: содержимое не прочитано")
        return Fact(Outcome.FOUND, text, f"{path}@{sha} прочитан")
```

- [ ] **Step 4: Run** `uv run --frozen pytest tests/test_governance_ops_policy_facts.py -q` — PASS.

- [ ] **Step 5: Commit** `git add governance/ops.py tests/test_governance_ops_policy_facts.py && git commit -m "feat(ops): GraphQL-факты политики подписи — версия по пути и содержимое по SHA"`

---

### Task 4: `approval_facts` — снимок политики вместо переменной окружения

**Files:**
- Modify: `governance/approval_facts.py` (блок `APPROVER_ALLOWLIST_ENV` … `authorized_signature`, строки 195–300)
- Test: `tests/test_governance_approval_facts.py` (тесты `test_allowlist_is_empty_by_default`, `test_configured_account_signs_and_review_circuit_never_does`, `test_allowlist_drops_empty_items` заменяются)

**Interfaces:**
- Produces:
  - `POLICY_SOURCE_FILE: Path` (= `contracts/approval-policy-source/v1/source.env`), `policy_source() -> tuple[str, str, str]` (repo, ref, path; `RuntimeError` на битом SSOT);
  - `PolicySnapshot(repo, ref, path, sha, accounts: frozenset[str], fingerprint: str)` (frozen dataclass) с `as_record() -> dict[str, str]` (без `accounts`);
  - `POLICY_REFUSAL_ENV/SOURCE/ABSENT/SUPERSEDED/EMPTY` — строковые `kind`;
  - `PolicyRefusal(kind: str, detail: str, pinned: str | None = None, current: str | None = None)`;
  - `policy_snapshot(ops, *, pinned_sha: str | None) -> Fact[PolicySnapshot]` — `FOUND(snapshot)` / `FORBIDDEN` с `value=PolicyRefusal` / `UNAVAILABLE`;
  - `policy_fingerprint(accounts: Iterable[str]) -> str`;
  - `authorized_signature(event, snapshot) -> Fact[Authorization]`;
  - `approver_allowlist` удалена.

- [ ] **Step 1: Failing tests** (заменить три старых теста)

```python
# tests/test_governance_approval_facts.py — вместо трёх тестов про env
from dataclasses import dataclass, field
from governance.facts import Fact, Outcome, unavailable

SHA_A, SHA_B = "a" * 40, "b" * 40


@dataclass
class PolicyOps:
    """Фейк форджи политики: версия по пути и тексты по SHA."""
    version: Fact = field(default_factory=lambda: Fact(Outcome.FOUND, SHA_A, ""))
    files: dict[str, Fact] = field(default_factory=lambda: {
        SHA_A: Fact(Outcome.FOUND, "AUTHORIZED_APPROVER_ACCOUNTS=andrei-shtanakov\n", ""),
    })
    calls: list[tuple] = field(default_factory=list)

    def policy_version_fact(self, repo, branch, path):
        self.calls.append(("version", repo, branch, path)); return self.version

    def repo_file_fact(self, repo, sha, path):
        self.calls.append(("file", repo, sha, path))
        return self.files.get(sha, Fact(Outcome.ABSENT, None, "нет"))


def test_snapshot_found_pins_sha_and_fingerprint(monkeypatch) -> None:
    monkeypatch.delenv(af.APPROVER_ALLOWLIST_ENV, raising=False)
    fact = af.policy_snapshot(PolicyOps(), pinned_sha=None)
    assert fact.outcome is Outcome.FOUND
    snap = fact.value
    assert snap.sha == SHA_A and snap.accounts == frozenset({"andrei-shtanakov"})
    assert snap.fingerprint == "v1:31bf16586b6cfbb69a29b23d8850bfe57931d99b"
    assert snap.repo == "andrei-shtanakov/approval-policy" and snap.path == "policy/approvers.env"


def test_env_variable_is_a_named_refusal_before_any_forge_call(monkeypatch) -> None:
    monkeypatch.setenv(af.APPROVER_ALLOWLIST_ENV, "andrei-shtanakov")
    ops = PolicyOps()
    fact = af.policy_snapshot(ops, pinned_sha=None)
    assert fact.outcome is Outcome.FORBIDDEN and fact.value.kind == af.POLICY_REFUSAL_ENV
    assert ops.calls == [], "до форджи дело не дошло"
    assert "approval-policy" in fact.detail and "учётки нет в" not in fact.detail


def test_superseded_when_current_version_differs_from_pin(monkeypatch) -> None:
    monkeypatch.delenv(af.APPROVER_ALLOWLIST_ENV, raising=False)
    fact = af.policy_snapshot(PolicyOps(), pinned_sha=SHA_B)
    assert fact.outcome is Outcome.FORBIDDEN
    assert fact.value.kind == af.POLICY_REFUSAL_SUPERSEDED
    assert fact.value.pinned == SHA_B and fact.value.current == SHA_A


def test_empty_after_parsing_is_forbidden_not_found(monkeypatch) -> None:
    monkeypatch.delenv(af.APPROVER_ALLOWLIST_ENV, raising=False)
    ops = PolicyOps(files={SHA_A: Fact(Outcome.FOUND, "AUTHORIZED_APPROVER_ACCOUNTS= , ,\n", "")})
    fact = af.policy_snapshot(ops, pinned_sha=None)
    assert fact.outcome is Outcome.FORBIDDEN and fact.value.kind == af.POLICY_REFUSAL_EMPTY


def test_unavailable_version_or_file_keeps_outcome_unavailable(monkeypatch) -> None:
    monkeypatch.delenv(af.APPROVER_ALLOWLIST_ENV, raising=False)
    assert af.policy_snapshot(PolicyOps(version=unavailable("net")), pinned_sha=None).outcome is Outcome.UNAVAILABLE
    ops = PolicyOps(files={SHA_A: unavailable("net")})
    assert af.policy_snapshot(ops, pinned_sha=None).outcome is Outcome.UNAVAILABLE


def test_absent_branch_or_file_is_forbidden_absent(monkeypatch) -> None:
    monkeypatch.delenv(af.APPROVER_ALLOWLIST_ENV, raising=False)
    fact = af.policy_snapshot(PolicyOps(version=Fact(Outcome.ABSENT, None, "нет ветки")), pinned_sha=None)
    assert fact.outcome is Outcome.FORBIDDEN and fact.value.kind == af.POLICY_REFUSAL_ABSENT
    fact = af.policy_snapshot(PolicyOps(files={}), pinned_sha=None)
    assert fact.outcome is Outcome.FORBIDDEN and fact.value.kind == af.POLICY_REFUSAL_ABSENT


def test_signature_judges_login_against_snapshot(monkeypatch) -> None:
    monkeypatch.delenv(af.APPROVER_ALLOWLIST_ENV, raising=False)
    snap = af.policy_snapshot(PolicyOps(), pinned_sha=None).value
    assert isinstance(snap, af.PolicySnapshot)
    human = af.authorized_signature(MergeEvent("andrei-shtanakov", "2026-09-10T08:00:00Z", "abc"), snap)
    agent = af.authorized_signature(MergeEvent("ai-prosto", "2026-09-10T08:00:00Z", "abc"), snap)
    assert human.outcome is Outcome.FOUND
    assert human.value.source == f"github:andrei-shtanakov/approval-policy@{SHA_A}:policy/approvers.env"
    assert human.value.policy == snap.fingerprint
    assert agent.outcome is Outcome.FORBIDDEN and agent.established


def test_env_reader_is_gone() -> None:
    assert not hasattr(af, "approver_allowlist")
```

- [ ] **Step 2: Run** `uv run --frozen pytest tests/test_governance_approval_facts.py -q` — FAIL.

- [ ] **Step 3: Реализация** (заменяет `approver_allowlist`, `policy_fingerprint()`, `authorized_signature(event)`)

```python
from collections.abc import Iterable
from pathlib import Path
from governance import ssot_env

APPROVER_ALLOWLIST_ENV = "AUTHORIZED_APPROVER_ACCOUNTS"   # ключ в approvers.env и имя ОТКАЗА S7
POLICY_SOURCE_FILE = (
    Path(__file__).resolve().parent.parent / "contracts" / "approval-policy-source" / "v1" / "source.env"
)
POLICY_SCHEME = "v1"
POLICY_REFUSAL_ENV = "env"
POLICY_REFUSAL_SOURCE = "source"
POLICY_REFUSAL_ABSENT = "absent"
POLICY_REFUSAL_SUPERSEDED = "superseded"
POLICY_REFUSAL_EMPTY = "empty"
INVALIDATION_POLICY_CHANGED = "policy_changed"   # префикс причины invalidated при смене версии


def policy_source() -> tuple[str, str, str]:
    """(repo, ref, path) из SSOT под authority-root; RuntimeError на битом файле."""
    what = "SSOT источника политики подписи"
    return (
        ssot_env.read_key(POLICY_SOURCE_FILE, "APPROVAL_POLICY_REPO", what),
        ssot_env.read_key(POLICY_SOURCE_FILE, "APPROVAL_POLICY_REF", what),
        ssot_env.read_key(POLICY_SOURCE_FILE, "APPROVAL_POLICY_PATH", what),
    )


def policy_fingerprint(accounts: Iterable[str]) -> str:
    payload = ",".join(sorted(accounts)).encode("utf-8")
    return f"{POLICY_SCHEME}:{hashlib.sha1(payload).hexdigest()}"


@dataclass(frozen=True)
class PolicySnapshot:
    repo: str
    ref: str
    path: str
    sha: str
    accounts: frozenset[str]
    fingerprint: str

    @property
    def source(self) -> str:
        return f"github:{self.repo}@{self.sha}:{self.path}"

    def as_record(self) -> dict[str, str]:
        return {"repo": self.repo, "ref": self.ref, "path": self.path,
                "sha": self.sha, "fingerprint": self.fingerprint}


@dataclass(frozen=True)
class PolicyRefusal:
    kind: str
    detail: str
    pinned: str | None = None
    current: str | None = None


PolicyFact = Fact[PolicySnapshot | PolicyRefusal]   # FOUND несёт снимок, FORBIDDEN — отказ


def _forbidden(kind: str, detail: str, **extra: str) -> PolicyFact:
    return Fact(Outcome.FORBIDDEN, PolicyRefusal(kind, detail, **extra), detail)


def policy_snapshot(ops: Ops, *, pinned_sha: str | None) -> PolicyFact:
    """Снимок политики из репозитория (спека §4.2); порядок проверок — таблица §4.2."""
    if os.environ.get(APPROVER_ALLOWLIST_ENV) is not None:
        return _forbidden(
            POLICY_REFUSAL_ENV,
            f"{APPROVER_ALLOWLIST_ENV} выставлена в окружении, но переменная "
            "больше не источник политики — источник репозиторий approval-policy; "
            "снимите переменную и повторите",
        )
    try:
        repo, ref, path = policy_source()
    except RuntimeError as exc:
        return _forbidden(POLICY_REFUSAL_SOURCE, f"конфигурация источника политики не читается: {exc}")
    version = ops.policy_version_fact(repo, ref, path)
    if version.outcome is Outcome.UNAVAILABLE:
        return unavailable(f"версия политики {repo}:{path}@{ref} не установлена: {version.detail}")
    if version.outcome is Outcome.ABSENT:
        return _forbidden(POLICY_REFUSAL_ABSENT, f"источник политики пуст: {version.detail}")
    sha = version.value
    assert isinstance(sha, str)
    if pinned_sha is not None and sha != pinned_sha:
        return _forbidden(
            POLICY_REFUSAL_SUPERSEDED,
            f"политика сменилась: закреплена {pinned_sha}, актуальная {sha}",
            pinned=pinned_sha, current=sha,
        )
    content = ops.repo_file_fact(repo, sha, path)
    if content.outcome is Outcome.UNAVAILABLE:
        return unavailable(f"содержимое политики {repo}@{sha}:{path} не прочитано: {content.detail}")
    if content.outcome is Outcome.ABSENT:
        return _forbidden(POLICY_REFUSAL_ABSENT, f"в версии {sha} нет {path}")
    text = content.value
    assert isinstance(text, str)
    lines = ssot_env.definition_lines(text, APPROVER_ALLOWLIST_ENV)
    if len(lines) != 1 or not lines[0]:
        reason = "ключ отсутствует" if not lines else ("дубль ключа" if len(lines) > 1 else "пустое значение")
        return _forbidden(POLICY_REFUSAL_EMPTY, f"{path}@{sha}: {reason} {APPROVER_ALLOWLIST_ENV} — подписать не может никто")
    accounts = frozenset(part.strip() for part in lines[0].split(",") if part.strip())
    if not accounts:
        return _forbidden(POLICY_REFUSAL_EMPTY, f"{path}@{sha}: {APPROVER_ALLOWLIST_ENV} без единого логина — подписать не может никто")
    snapshot = PolicySnapshot(repo, ref, path, sha, accounts, policy_fingerprint(accounts))
    return Fact(Outcome.FOUND, snapshot, f"политика {snapshot.source}, отпечаток {snapshot.fingerprint}")


def authorized_signature(event: MergeEvent, snapshot: PolicySnapshot) -> Fact[Authorization]:
    if event.login in snapshot.accounts:
        return Fact(
            Outcome.FOUND,
            Authorization(event.login, snapshot.fingerprint, snapshot.source),
            f"{event.login} авторизован политикой {snapshot.source}",
        )
    return Fact(
        Outcome.FORBIDDEN, None,
        f"мерж от {event.login}: учётки нет в политике {snapshot.source} — подписи этот мерж не создаёт",
    )
```
`_definition_lines` → публичное `definition_lines` в `ssot_env.py` (единственный вызывающий — `read_key`, строка 55). Потребители `policy_snapshot` сужают `value` через `isinstance` (`PolicySnapshot` на `FOUND`, `PolicyRefusal` на `FORBIDDEN`) — контракт `Fact(Generic[T])` соблюдён типом `PolicyFact`. Шапки `governance/ssot_env.py` («две половины») и `tests/test_ssot_env_format.py` (`ssot_key` в `merge-pr.sh`) — на `ssot_env.sh`. Докстроки — перенести доводы из прежних (граница defense-in-depth уточняется: «в меру authority-root»).

- [ ] **Step 4: Run** `uv run --frozen pytest tests/test_governance_approval_facts.py -q` — PASS. Остальной suite на этом шаге красный (потребители) — ожидаемо до Task 6.

- [ ] **Step 5: Commit** `git add governance/approval_facts.py governance/ssot_env.py tests/test_governance_approval_facts.py && git commit -m "feat(governance): снимок политики подписи из approval-policy — пять отказов, env не читается"`

---

### Task 5: Ледгер — `policy` в заявке

**Files:**
- Modify: `governance/approval_ledger.py:359-421` (`start_request`)
- Test: `tests/test_governance_approval_ledger.py`

**Interfaces:**
- Produces: `start_request(..., upstream_pins, policy: dict[str, str] | None = None)`; запись заявки несёт `"policy": policy` (dict с ключами `repo, ref, path, sha, fingerprint` либо `None` для старого формата — только тесты старого формата).

- [ ] **Step 1: Failing test**

```python
def test_request_pins_policy_snapshot_write_ahead(state) -> None:
    key = al.start_request(
        state, "WS", 1, 0, 1, ["charter"], {"charter": "h"}, {"charter": {}},
        policy={"repo": "o/p", "ref": "main", "path": "policy/approvers.env",
                "sha": "a" * 40, "fingerprint": "v1:x"},
    )
    assert rs.load(state.run_id).ops[key]["policy"]["sha"] == "a" * 40
```
(имя фикстуры состояния — как у соседних тестов файла; литерал `"AUTHORIZED_APPROVER_ACCOUNTS"` в `_merge` заменить на `"github:o/p@" + "a"*40 + ":policy/approvers.env"` и ожидание `source` соответственно.)

- [ ] **Step 2: Run** — FAIL (`TypeError: unexpected keyword policy`).

- [ ] **Step 3: Реализация** — параметр `policy: dict[str, str] | None = None`, в записи после `"upstream_pins"`: `"policy": dict(policy) if policy else None,` с комментарием «закреплённая версия политики подписи (спека approval-policy §4.3); None — заявка старого формата, на фазе 2 она `invalidated` (§4.6)».

- [ ] **Step 4: Run** `uv run --frozen pytest tests/test_governance_approval_ledger.py -q` — PASS.

- [ ] **Step 5: Commit** `git add governance/approval_ledger.py tests/test_governance_approval_ledger.py && git commit -m "feat(ledger): заявка закрепляет версию политики подписи"`

---

### Task 6: `approve_node` — фаза 1 закрепляет, фаза 2 перечитывает по пину

**Files:**
- Modify: `governance/approve_node.py` (`_propose` 290–372; `_publish_candidate`/`_candidate_body` 1104–1170; `_reconcile_candidate` 1384–1490; сообщения 1426–1437, 1852–1860)
- Test: `tests/test_governance_approve_node.py` (стенд `Forge`/`Ops` 110–232; фикстура `world` 252; тесты 2428–2510 переписываются)

**Interfaces:**
- Consumes: `af.policy_snapshot`, `af.PolicyRefusal`, `af.POLICY_REFUSAL_*`, `af.authorized_signature(event, snapshot)`, `al.start_request(..., policy=)`.
- Produces: заявка с `policy`; `authorization.source` вида `github:…@<sha>:…`; тело candidate со строкой `policy: <repo>@<sha>`.

- [ ] **Step 1: Стенд — фейк источника политики**

В `Forge` добавить:
```python
    #: Источник политики подписи (спека approval-policy): версия по пути и
    #: тексты по SHA; `mute` с ключами "policy_version"/"policy_file" даёт
    #: UNAVAILABLE. Подмена текста под тем же SHA моделирует «прочитано не то».
    policy_sha: str | None = "p" * 40
    policy_files: dict[str, str] = field(default_factory=lambda: {
        "p" * 40: f"AUTHORIZED_APPROVER_ACCOUNTS={HUMAN}\n",
    })
```
В `Ops`:
```python
    def policy_version_fact(self, repo_slug, branch, path):
        if "policy_version" in self.forge.mute:
            return unavailable("policy: нет сети")
        if self.forge.policy_sha is None:
            return Fact(Outcome.ABSENT, None, "политики нет")
        return Fact(Outcome.FOUND, self.forge.policy_sha, "ok")

    def repo_file_fact(self, repo_slug, sha, path):
        if "policy_file" in self.forge.mute:
            return unavailable("policy: нет сети")
        text = self.forge.policy_files.get(sha)
        if text is None:
            return Fact(Outcome.ABSENT, None, "нет файла")
        return Fact(Outcome.FOUND, text, "ok")
```
Импорт `from governance.facts import Fact, Outcome, unavailable`. В фикстуре `world` строку `monkeypatch.setenv(af.APPROVER_ALLOWLIST_ENV, HUMAN)` заменить на `monkeypatch.delenv(af.APPROVER_ALLOWLIST_ENV, raising=False)`.

- [ ] **Step 2: Failing tests** (вместо `test_empty_allowlist_refuses_before_any_candidate_is_created`, `test_empty_allowlist_refusal_is_not_about_the_merger`, `test_no_op_over_approved_node_survives_empty_allowlist`, `test_finalize_without_policy_refuses_and_keeps_the_request`; тест `test_agent_merge_invalidates_and_names_the_allowlist` — заменить `match=af.APPROVER_ALLOWLIST_ENV` на `match="approval-policy"`; в тесте на 1600–1615 `setenv("")` заменить на `world.forge.mute.add("policy_version")` и снять строку `assert af.approver_allowlist() == frozenset()` — функции больше нет; ожидание прежнее: фаза 3 источник не читает)

```python
POLICY_SHA_2 = "q" * 40


def test_candidate_pins_the_policy_version(world: World) -> None:
    approve(world, "charter")
    key, op = only_request(world)
    assert op["policy"]["sha"] == "p" * 40
    assert op["policy"]["fingerprint"] == "v1:31bf16586b6cfbb69a29b23d8850bfe57931d99b"
    body = world.forge.prs[op["candidate_pr"]]["body"]
    assert f"policy: andrei-shtanakov/approval-policy@{'p' * 40}" in body
    assert af.APPROVER_ALLOWLIST_ENV not in body


def test_env_variable_refuses_before_any_effect(world: World, monkeypatch) -> None:
    monkeypatch.setenv(af.APPROVER_ALLOWLIST_ENV, HUMAN)
    with pytest.raises(RuntimeError, match="больше не источник"):
        approve(world, "charter")
    assert world.state.ops == {} and world.forge.prs == {}


def test_unavailable_policy_is_unresolved_and_creates_nothing(world: World) -> None:
    world.forge.mute.add("policy_version")
    with pytest.raises(RuntimeError, match="факт не установлен"):
        approve(world, "charter")
    assert world.state.ops == {} and world.forge.prs == {}


def test_empty_policy_refuses_before_candidate_without_blaming_a_login(world: World) -> None:
    world.forge.policy_files["p" * 40] = "AUTHORIZED_APPROVER_ACCOUNTS= , ,\n"
    with pytest.raises(RuntimeError) as caught:
        approve(world, "charter")
    assert "подписать не может никто" in str(caught.value)
    assert "учётки нет в" not in str(caught.value)
    assert world.state.ops == {}


def test_no_op_over_approved_node_survives_unavailable_policy(world: World) -> None:
    drive_to_approved(world, "charter")
    world.sync()
    world.forge.mute.add("policy_version")
    assert "no-op" in approve(world, "charter").message


def test_policy_change_invalidates_and_new_candidate_pins_the_new_version(
    world: World,
) -> None:
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    world.forge.policy_sha = POLICY_SHA_2
    world.forge.policy_files[POLICY_SHA_2] = f"AUTHORIZED_APPROVER_ACCOUNTS={HUMAN}\n"
    with pytest.raises(RuntimeError) as caught:
        approve(world, "charter")
    assert "политика сменилась" in str(caught.value)
    record = world.state.ops[key]
    assert record["status"] == al.STATUS_INVALIDATED
    assert record["reason"].startswith("policy_changed:")
    assert record["authorization"] is None
    assert record["policy"]["sha"] == "p" * 40 and record["candidate_pr"] == op["candidate_pr"], "история сохранена"
    # повторное установление авторизации — новый candidate под новым пином
    outcome = approve(world, "charter")
    assert outcome.request is not None and outcome.request != key
    assert world.state.ops[outcome.request]["policy"]["sha"] == POLICY_SHA_2
    merge_pr(world, world.state.ops[outcome.request]["candidate_pr"])
    approve(world, "charter")
    assert world.state.ops[outcome.request]["status"] == al.STATUS_COMPLETED


def test_substituted_content_under_same_sha_keeps_the_request(world: World) -> None:
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    world.forge.policy_files["p" * 40] = "AUTHORIZED_APPROVER_ACCOUNTS=someone-else\n"
    with pytest.raises(RuntimeError, match="прочитано не то"):
        approve(world, "charter")
    assert al.is_live(world.state.ops[key])
    assert world.state.ops[key]["authorization"] is None


def test_env_variable_at_phase_two_refuses_and_keeps_the_request(
    world: World, monkeypatch
) -> None:
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    monkeypatch.setenv(af.APPROVER_ALLOWLIST_ENV, HUMAN)
    with pytest.raises(RuntimeError, match="больше не источник"):
        approve(world, "charter")
    assert al.is_live(world.state.ops[key])


def test_completed_request_records_policy_source(world: World) -> None:
    drive_to_approved(world, "charter")
    _, op = only_request(world)
    assert op["authorization"]["source"] == (
        f"github:andrei-shtanakov/approval-policy@{'p' * 40}:policy/approvers.env"
    )
    assert op["authorization"]["policy"] == op["policy"]["fingerprint"]


def test_legacy_request_without_policy_is_invalidated(world: World) -> None:
    approve(world, "charter")
    key, op = only_request(world)
    merge_pr(world, op["candidate_pr"])
    world.state.ops[key]["policy"] = None
    rs.save(world.state)
    with pytest.raises(RuntimeError, match="не закрепила версию"):
        approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED


def test_join_reads_policy_under_the_live_request_pin(world: World) -> None:
    """design+acceptance — один уровень: второй узел присоединяется к заявке
    первого только под её пином; сменившаяся версия — отказ без записей."""
    for node in ("charter", "requirements", "behaviour-spec"):
        drive_to_approved(world, node)
    world.sync()
    approve(world, "design")
    key, op = only_request(world)
    world.forge.policy_sha = POLICY_SHA_2
    world.forge.policy_files[POLICY_SHA_2] = f"AUTHORIZED_APPROVER_ACCOUNTS={HUMAN}\n"
    with pytest.raises(RuntimeError, match="политика сменилась"):
        approve(world, "acceptance")
    assert world.state.ops[key]["nodes"] == ["design"], "узел не дописан"
    assert al.is_live(world.state.ops[key])
```

- [ ] **Step 3: Run** `uv run --frozen pytest tests/test_governance_approve_node.py -q -k "policy or env_variable or legacy_request or join_reads"` — FAIL.

- [ ] **Step 4: Реализация — фаза 1** (в `_propose`, вместо блока `if not af.approver_allowlist(): raise …`, ПОСЛЕ веток no-op/долга и ДО `_require_upstream_ready`)

```python
    # Политика подписи закрепляется ДО первой записи (спека approval-policy
    # §4.3): новая заявка — актуальная версия; присоединение к живой —
    # только под её пином. Ветки no-op и долга выше намеренно: они ничего
    # не создают.
    # ОДНО решение «присоединяюсь ли», принятое до чтения политики и
    # переиспользованное ниже: второе определение разошлось бы с первым
    # (класс `ApprovedDag`). Read-only: открытая волна, сверка отпечатка
    # состава (как в `_wave_for`), `live_request_for_step`,
    # `_still_accumulating`. Записи (`_close_obsolete_wave`, `_wave_for`,
    # `extend_request`/`start_request`) — ПОСЛЕ снимка (§4.3 «до первой записи»).
    step = _levels(dag)[node_id]
    nodes = bundle_dag.composition(dag)
    fingerprint = bundle_dag.composition_fingerprint(nodes)
    joined = _join_target(state, ops, fingerprint, step)   # (nums, op) | None
    joined_pin = None
    if joined is not None:
        joined_policy = joined[1].get("policy")
        if not joined_policy:
            raise RuntimeError(
                f"живая заявка {al.request_key(*joined[0])} не закрепила версию "
                "политики (старый формат) — присоединиться к ней нельзя; дождитесь "
                "её терминализации (фаза 2 объявит её invalidated) и повторите"
            )
        joined_pin = joined_policy["sha"]
    policy = af.policy_snapshot(ops, pinned_sha=joined_pin)
    if policy.outcome is Outcome.UNAVAILABLE:
        raise _unresolved(f"политика подписи для узла {node_id}: {policy.detail}")
    if policy.outcome is Outcome.FORBIDDEN:
        assert isinstance(policy.value, af.PolicyRefusal)
        raise RuntimeError(_policy_refusal_text(policy.value, joined is not None))
    snapshot = policy.value
    assert isinstance(snapshot, af.PolicySnapshot)
    if joined is not None and snapshot.fingerprint != joined[1]["policy"]["fingerprint"]:
        raise RuntimeError(
            f"политика {snapshot.source}: прочитано не то, что закрепляла заявка "
            f"{al.request_key(*joined[0])} — узел не дописан, ничего не записано"
        )
```
где
```python
def _join_target(state, ops, fingerprint: str, step: int):
    """Живая заявка шага в ОТКРЫТОЙ волне с тем же отпечатком состава, к
    которой узел присоединится; None — будет новая. Read-only зеркало решения
    `_close_obsolete_wave` + `_wave_for` + `live_request_for_step` +
    `_still_accumulating` — без записей."""
    wave = al.open_wave(state)
    if wave is None:
        return None
    record = al.wave_records(state).get(wave) or {}
    if _intent_of(record, wave)["fingerprint"] != fingerprint:   # fail-closed на битой записи, как _close_obsolete_wave
        return None          # `_close_obsolete_wave` ниже закроет её как obsolete
    joined = al.live_request_for_step(state, wave, step)
    if joined is None or not _still_accumulating(state, ops, joined[1]):
        return None
    return joined


def _policy_refusal_text(refusal: af.PolicyRefusal, joining: bool) -> str:
    if refusal.kind == af.POLICY_REFUSAL_SUPERSEDED and joining:
        return (
            f"{refusal.detail}; живая заявка закреплена на старой версии — "
            "дождитесь её терминализации фазой 2, затем новый candidate"
        )
    return f"{refusal.detail}. Ничего не создано; повторите вызов после исправления источника"
```
Ниже по `_propose` существующий блок `_close_obsolete_wave` / `_wave_for` / `live_request_for_step` / `_still_accumulating` **заменяется** использованием уже вычисленных `joined`, `nodes`, `fingerprint`, `step`: `_close_obsolete_wave(state, nodes, fingerprint)`; `wave = _wave_for(state, nodes, fingerprint)`; если `joined is not None` — `extend_request` по его ключу, иначе `start_request(..., policy=snapshot.as_record())`. Точное имя поля отпечатка в записи волны (`intent.fingerprint`) взять из `open_wave_record`/`_wave_for` при реализации — `_join_target` обязан читать тот же ключ, что пишет `_wave_for`; тест: `_join_target` и решение `_propose` совпадают на трёх стендах (открытая волна с тем же отпечатком; с другим; заявка с вмерженным candidate → `_still_accumulating` False).

- [ ] **Step 5: Реализация — тексты** — в `_publish_candidate` (строка 1146), `_candidate_body` (1159), `_reconcile_candidate` (1430, 1435), финализация (1857) заменить `af.APPROVER_ALLOWLIST_ENV` на `_policy_ref(op)`:
```python
def _policy_ref(op: dict) -> str:
    policy = op.get("policy") or {}
    if not policy:
        return "политики (заявка старого формата)"
    return f"политики approval-policy@{policy['sha']}"
```
и в `_candidate_body` добавить после «Узлы: …» строку `f"policy: {policy['repo']}@{policy['sha']}"` **только при `op.get("policy")`** (возобновление `CREATE_CANDIDATE` заявки старого формата существует — `_advance`; без гварда — `TypeError`); её читает `human-merge.sh`.

- [ ] **Step 6: Реализация — фаза 2** (в `_reconcile_candidate` перед `signature = af.authorized_signature(merged)`)

```python
    pinned = op.get("policy")
    if not pinned:
        al.invalidate_request(state, key, "заявка не закрепила версию политики (старый формат)")
        raise RuntimeError(
            f"заявка {key} не закрепила версию политики — invalidated; "
            "восстановление: новый candidate над теми же узлами"
        )
    policy = af.policy_snapshot(ops, pinned_sha=pinned["sha"])
    if policy.outcome is Outcome.UNAVAILABLE:
        raise _unresolved(f"политика подписи для мержа PR #{pr}: {policy.detail}")
    if policy.outcome is Outcome.FORBIDDEN:
        refusal = policy.value
        assert refusal is not None
        if refusal.kind == af.POLICY_REFUSAL_SUPERSEDED:
            # Причина с машинным префиксом (решение владельца 2026-09-22):
            # отличима от прочих invalidated, запись заявки сохраняется целиком.
            al.invalidate_request(state, key, f"{af.INVALIDATION_POLICY_CHANGED}: {refusal.detail}")
            raise RuntimeError(
                f"{refusal.detail}. Заявка {key} — invalidated; повторное "
                "установление авторизации — новый candidate над теми же узлами"
            )
        raise RuntimeError(
            f"{refusal.detail}. Заявка {key} жива, факт мержа не записан; "
            "исправьте источник/окружение и повторите"
        )
    snapshot = policy.value
    assert snapshot is not None
    if snapshot.fingerprint != pinned["fingerprint"]:
        raise RuntimeError(
            f"политика {snapshot.source}: прочитано не то, что закрепляла заявка "
            f"{key} (отпечаток {snapshot.fingerprint} ≠ {pinned['fingerprint']}) — "
            "подмена или сбой пути чтения; заявка жива, ничего не записано"
        )
    signature = af.authorized_signature(merged, snapshot)
```
Ветка `Outcome.UNAVAILABLE` у `signature` (добавлена #338) удаляется — она больше недостижима.

- [ ] **Step 7: Run** `uv run --frozen pytest tests/test_governance_approve_node.py -q` — PASS целиком (включая `test_invalidated_request_recovers_end_to_end`).

- [ ] **Step 8: Commit** `git add governance/approve_node.py tests/test_governance_approve_node.py && git commit -m "feat(approve_node): фаза 1 закрепляет версию политики, фаза 2 перечитывает по пину; смена версии — invalidated"`

---

### Task 7: `human-merge.sh` — логин и пин против репозитория политики

**Files:**
- Modify: `human-merge.sh` (блок «Allowlist — тот же env…» строки 84–97; usage; шапка)
- Test: `tests/test_human_merge.py` (стаб `gh` получает ветки `api graphql`; `AUTHORIZED_APPROVER_ACCOUNTS=HUMAN` из `env()` убирается)

**Interfaces:**
- Consumes: `ssot_env.sh` (Task 2), `contracts/approval-policy-source/v1/source.env`.
- Produces: коды выхода прежние; новый отказ 3 «политика сменилась после candidate»; отказ 2 «тело PR без строки policy:»; отказ 3 на выставленную переменную.

- [ ] **Step 1: Failing tests** (расширить стаб: в `case "$*"` добавить ветки)

```bash
  *"api graphql"*)
    # Стаб игнорирует --jq и печатает уже «отжатые» значения — как ветка pr view.
    case "$*" in
      *history*) printf '%s\n' "${GH_STUB_POLICY_SHA:-pppppppppppppppppppppppppppppppppppppppp}" ;;
      *)         printf 'AUTHORIZED_APPROVER_ACCOUNTS=%s\n' "${GH_STUB_POLICY_ACCOUNTS:-andrei-shtanakov}" ;;
    esac ;;
```
и в ветке `pr view` добавить четвёртую строку тела: `printf '%s\n' "${GH_STUB_BODY-policy: andrei-shtanakov/approval-policy@pppppppppppppppppppppppppppppppppppppppp}"` (скрипт запрашивает `--json state,headRefOid,mergeStateStatus,body` и читает 4 строки; тело — одной строкой через `--jq '.body | gsub("\n";" ")'`).

```python
def test_login_outside_policy_is_refused(fleet: Fleet) -> None:
    done = fleet.run("demo", "7", GH_STUB_POLICY_ACCOUNTS="someone-else")
    assert done.returncode == 3 and "approval-policy" in done.stderr
    assert "/merge" not in fleet.gh_log.read_text()


def test_policy_changed_after_candidate_is_refused_before_merge(fleet: Fleet) -> None:
    done = fleet.run("demo", "7", GH_STUB_POLICY_SHA="q" * 40)
    assert done.returncode == 3 and "сменилась" in done.stderr
    assert "/merge" not in fleet.gh_log.read_text()


def test_body_without_policy_line_is_refused(fleet: Fleet) -> None:
    done = fleet.run("demo", "7", GH_STUB_BODY="без пина")
    assert done.returncode == 2 and "policy:" in done.stderr


def test_env_variable_is_refused(fleet: Fleet) -> None:
    done = fleet.run("demo", "7", AUTHORIZED_APPROVER_ACCOUNTS=HUMAN)
    assert done.returncode == 3 and "больше не источник" in done.stderr
```
Существующие тесты «пустой allowlist — отказ 3» удалить; «логин не в списке» — заменить на `test_login_outside_policy_is_refused`. Код для «тело без `policy:`» — **2** (состояние PR, не авторизация актора); спека §4.5 говорила «отказ» без кода в одном месте и «код 3» в другом — править спеку на 2 тем же PR.

- [ ] **Step 2: Run** `uv run --frozen pytest tests/test_human_merge.py -q` — FAIL.

- [ ] **Step 3: Реализация** — заменить блок allowlist:

```sh
. "$script_dir/ssot_env.sh"
# Источник политики подписи — репозиторий approval-policy (спека
# 2026-09-22-approver-policy-trusted-source, §4.5). Переменная окружения
# больше не источник: выставленная — отказ, чтобы старое правило не
# исполнялось молча не так, как задумано.
[ -z "${AUTHORIZED_APPROVER_ACCOUNTS+x}" ] || die 3 "AUTHORIZED_APPROVER_ACCOUNTS выставлена, но переменная больше не источник политики — источник репозиторий approval-policy; снимите её"
src="$script_dir/contracts/approval-policy-source/v1/source.env"
p_repo=$(ssot_key "$src" APPROVAL_POLICY_REPO "SSOT источника политики") || exit $?
p_ref=$(ssot_key "$src" APPROVAL_POLICY_REF "SSOT источника политики") || exit $?
p_path=$(ssot_key "$src" APPROVAL_POLICY_PATH "SSOT источника политики") || exit $?
p_owner="${p_repo%%/*}"; p_name="${p_repo#*/}"
```
Чтение PR расширить: `--json state,headRefOid,mergeStateStatus,body --jq '.state, .headRefOid, .mergeStateStatus, (.body // "" | gsub("\n";" "))'` (`// ""` — PR без тела даёт пустую строку, а не ошибку jq), четвёртая строка — `body`. Чтение `text` через `--jq` при `file: null` печатает строку `null` → отказ «без AUTHORIZED…» (код 3): для скрипта это приемлемо и названо здесь. Пин из тела:
```sh
pin=$(printf '%s\n' "$body" | sed -n 's/.*policy: [^@ ]*@\([0-9a-f]\{40\}\).*/\1/p' | head -n 1)
[ -n "$pin" ] || die 2 "PR ${slug}#${pr}: тело без строки 'policy: <repo>@<sha>' — candidate старого формата, новый candidate"
current=$(gh_h api graphql -f 'query=query($o:String!,$n:String!,$q:String!,$p:String!){repository(owner:$o,name:$n){ref(qualifiedName:$q){target{... on Commit{history(first:1,path:$p){nodes{oid}}}}}}}' \
    -F "o=$p_owner" -F "n=$p_name" -F "q=refs/heads/$p_ref" -F "p=$p_path" \
    --jq '.data.repository.ref.target.history.nodes[0].oid' 2>&1) \
    || die 2 "версия политики не прочитана: $current"
[ "$current" = "$pin" ] || die 3 "политика сменилась после candidate (закреплена $pin, актуальная $current) — мерж не создаст подписи; новый candidate"
text=$(gh_h api graphql -f 'query=query($o:String!,$n:String!,$s:GitObjectID!,$p:String!){repository(owner:$o,name:$n){object(oid:$s){... on Commit{file(path:$p){object{... on Blob{text}}}}}}}' \
    -F "o=$p_owner" -F "n=$p_name" -F "s=$pin" -F "p=$p_path" \
    --jq '.data.repository.object.file.object.text' 2>&1) \
    || die 2 "политика $p_repo@$pin не прочитана: $text"
allow=$(printf '%s\n' "$text" | sed -n 's/^[[:space:]]*AUTHORIZED_APPROVER_ACCOUNTS=//p' | head -n 1 | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
[ -n "$allow" ] || die 3 "политика $p_repo@$pin без AUTHORIZED_APPROVER_ACCOUNTS — подписать не может никто"
```
Цикл сверки `login` против `$allow` — прежний; сообщение отказа: `"логин '$login' не входит в политику $p_repo@$pin ($allow)"`. Usage и шапка: «логин обязан входить в политику approval-policy; переменная окружения — отказ». Порядок: сначала логин (`gh_h api user`), потом PR, потом политика — как сейчас, чтобы «профиль не отвечает» отбивался раньше сетевых чтений.

- [ ] **Step 4: Run** `uv run --frozen pytest tests/test_human_merge.py tests/test_merge_pr.py -q` — PASS.

- [ ] **Step 5: Commit** `git add human-merge.sh tests/test_human_merge.py && git commit -m "feat(human-merge): логин и пин политики сверяются против approval-policy до мержа"`

---

### Task 8: Документы, страж, полный прогон, PR

**Files:**
- Modify: `CLAUDE.md` (секция «Мерж — агент по умолчанию», оговорка про `AUTHORIZED_APPROVER_ACCOUNTS`), `README.md` (упоминания переменной), `docs/superpowers/specs/2026-09-09-tasks-supersede-contract-design.md` (строки 2069, 2130–2131, строка про пустой дефолт, строка про мерж вне allowlist), `docs/superpowers/specs/2026-09-15-need-stage-design.md:447` (сноска на новую спеку), `TODO.md` (пункт `approver-policy-trusted-source` → `[x] — PR #N`, и заявка prograph-vault#147 расширяется комментарием), `tests/test_governance_approve_node.py` (AST-страж)

- [ ] **Step 1: AST-страж** (рядом с существующей AST-проверкой `approve_node`, строка ~2306)

```python
def test_no_module_reads_the_allowlist_from_the_environment() -> None:
    """Единственное чтение переменной — отказ S7 в policy_snapshot."""
    import ast, pathlib
    def names_the_key(node: ast.AST) -> bool:
        return (
            (isinstance(node, ast.Name) and node.id == "APPROVER_ALLOWLIST_ENV")
            or (isinstance(node, ast.Attribute) and node.attr == "APPROVER_ALLOWLIST_ENV")
            or (isinstance(node, ast.Constant) and node.value == "AUTHORIZED_APPROVER_ACCOUNTS")
        )

    hits = set()
    for path in pathlib.Path(an.__file__).parent.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            reads_env = (
                isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") in ("get", "getenv")
                and any(names_the_key(a) for a in node.args)
            ) or (
                isinstance(node, ast.Subscript) and names_the_key(node.slice)
            )
            if reads_env:
                hits.add(path.name)
    assert hits == {"approval_facts.py"}, hits
```

- [ ] **Step 2: Правки документов** — по таблице §5 спеки: в §I12 строка про пустой дефолт → «фаза 1 отказывает до candidate по снимку из approval-policy; на фазе 2 пустота недостижима (SHA неизменяем)»; строка 2069 и 2130–2131: «повтор устанавливает факт по закреплённой версии политики, не по актуальной»; новая строка таблицы: «версия политики сменилась между candidate и установлением факта | заявка → `invalidated` с обеими версиями; новый candidate закрепляет новую версию | ≠0».

- [ ] **Step 3: Полный прогон** `uv run --frozen pytest -q` — ожидается PASS (≈10 мин); `make plan-check-selftest` — OK.

- [ ] **Step 4: Commit** `git add CLAUDE.md README.md TODO.md docs/superpowers/specs/2026-09-09-tasks-supersede-contract-design.md docs/superpowers/specs/2026-09-15-need-stage-design.md docs/superpowers/specs/2026-09-22-approver-policy-trusted-source-design.md docs/superpowers/plans/2026-09-22-approver-policy-trusted-source.md tests/test_governance_approve_node.py && git commit -m "docs: политика подписи читается из approval-policy — CLAUDE.md, §I12, TODO"`

- [ ] **Step 5: Draft PR → TODO с номером → ready.** Ветка `spec/approver-policy-trusted-source` (спека уже там). PR трогает `human-merge.sh` и `contracts/authority-root/` — **мержит человек** (`make human-merge` без переменной, после Task 1). До мержа — правка правила волта (prograph-vault#147): команда без `AUTHORIZED_APPROVER_ACCOUNTS=`, ссылка на репозиторий.

---

## Self-review

- **Покрытие спеки:** §3.1 bootstrap — Task 1; §3.3 SSOT + authority-root — Task 2; §4.1 факты — Task 3; §4.2 снимок и таблица отказов — Task 4; §4.3 фаза 1 и присоединение — Task 6 (Step 4); §4.4 фаза 2, S6, отпечаток — Task 6 (Step 6); §4.5 human-merge — Task 7; §4.6 старый формат — Task 6; §4.7 удаление env-чтения — Task 4 + страж Task 8; §4.8 ледгер — Task 5; §5 документы — Task 8; §6 тесты — по задачам; §8 порядок раскатки — Task 8 Step 5.
- **Плейсхолдеров нет.** Имена сквозные: `policy_version_fact`, `repo_file_fact`, `policy_snapshot`, `PolicyRefusal.kind`, `POLICY_REFUSAL_*`, `start_request(..., policy=)`, `_policy_ref`, `_live_join_pin`.
- **Типы:** `Fact[PolicySnapshot]` с `value: PolicyRefusal` на `FORBIDDEN` — единственное место, где `value` не типа снимка; отмечено в Task 4 (`_forbidden`). Если ревью пары сочтёт это нарушением контракта `Fact[T]`, завести `Fact[PolicySnapshot | PolicyRefusal]`.
