"""criteria_close — закрытие воркстрима по оракулу бандла, срез 1 (спека §7.1).

Измерение (spec-runner verify --criteria) → сверка (§5.3) → исход (§3.3) →
файл закрытия `workstreams/<ws>/spec/90-acceptance-closure.md` агентским PR
(создаёт учётка оператора, scope-аттестация ai-prosto, мерж merge-pr.sh).
Ревизий, подписи и штампа нет — это срез 2. Флага обхода стопа нет и быть
не должно (спека §3.3).

Идентичность измерения (ревью среза 1, C2): бандл читается по пину из
git-объектов; продукт — только чистый чекаут ровно на `product_sha`, который
предок живой default-ветки. Публикация — из временного worktree от
`origin/<base>`: чекаут оператора не трогается (I2).
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from governance import (
    approval_branches,
    charter_guard,
    criteria_check,
    criteria_contract,
    criteria_graph,
    criteria_product,
    run_state,
    runner,
    spec_runner_contract,
    task_bridge,
)
from governance.frontmatter import join_frontmatter, split_frontmatter
from governance.ops import Ops, RealOps

STATE_ROOT = Path(__file__).resolve().parent.parent / "out" / "criteria-close"
CLOSURE_NAME = "90-acceptance-closure.md"
_NODES = (
    "00-charter.md",
    "10-requirements.md",
    "15-behaviour-spec.md",
    "25-acceptance.md",
)


class CloseError(RuntimeError):
    """Отказ шага с названной причиной (код 2)."""


def decide_not_applicable(
    charter, selector_policy, installed, minimum, *, is_vendored
) -> str | None:
    """Порядок: схема 1 → язык → доступность оракула (вендоринг и версия машины)."""
    if charter.schema != 2:
        return "schema-1"
    if selector_policy is not None and selector_policy.name != "pytest":
        return "language"
    if not criteria_contract.oracle_available(
        installed, minimum, is_vendored=is_vendored
    ):
        return "spec-runner-version"
    return None


# ---- состояние: измерено и опубликовано -------------------------------------


def _state_path(run_id: str) -> Path:
    return STATE_ROOT / run_id / "state.json"


def _load(run_id: str) -> dict:
    p = _state_path(run_id)
    data = json.loads(p.read_text()) if p.exists() else {}
    data.setdefault("measured", {})
    return data


def _save(run_id: str, data: dict) -> None:
    p = _state_path(run_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(p)


def _entry(run_id: str, key: str) -> dict | None:
    return _load(run_id)["measured"].get(key)


def _record(run_id: str, key: str, **fields) -> None:
    data = _load(run_id)
    data["measured"].setdefault(key, {}).update(fields)
    _save(run_id, data)


# ---- git ----------------------------------------------------------------------


def _git(repo: str | Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )


def _show(repo: str, ref: str, path: str) -> str:
    proc = _git(repo, "show", f"{ref}:{path}")
    if proc.returncode != 0:
        raise CloseError(f"{path} недоступен на {ref[:12]}: {proc.stderr.strip()}")
    return proc.stdout


def _bundle_at_pin(state, pin: str) -> dict[str, str]:
    """Узлы замыкания по пину из git-объектов (§3.1), не из рабочего дерева."""
    return {n: _show(state.target_dir, pin, f"{state.bundle_dir}/{n}") for n in _NODES}


def _verify_product(state, product_sha: str | None) -> str:
    """Чистое дерево, HEAD == product_sha, product_sha — предок origin/<base>."""
    base = state.base_ref or "master"
    if _git(state.target_dir, "fetch", "--quiet", "origin", base).returncode != 0:
        raise CloseError(f"fetch origin {base} не удался")
    tip = _git(state.target_dir, "rev-parse", f"origin/{base}").stdout.strip()
    sha = product_sha or tip
    full = _git(state.target_dir, "rev-parse", "--verify", f"{sha}^{{commit}}")
    if full.returncode != 0:
        raise CloseError(f"product_sha {sha} не коммит")
    sha = full.stdout.strip()
    if _git(state.target_dir, "merge-base", "--is-ancestor", sha, tip).returncode != 0:
        raise CloseError(f"product_sha {sha[:12]} не предок origin/{base}")
    if _git(state.target_dir, "status", "--porcelain").stdout.strip():
        raise CloseError("рабочее дерево не чистое — измерение невоспроизводимо")
    head = _git(state.target_dir, "rev-parse", "HEAD").stdout.strip()
    if head != sha:
        raise CloseError(
            f"HEAD {head[:12]} ≠ product_sha {sha[:12]} — выполните checkout"
        )
    return sha


# ---- рендер ---------------------------------------------------------------------


def render_closure(
    result,
    *,
    ws_id,
    code,
    bundle_pin,
    product_sha,
    response_sha,
    spec_runner_version,
    host,
    content_key: str | None = None,
    product_roots: list[str] | None = None,
    measured_inputs: dict | None = None,
) -> str:
    """Текст файла закрытия; result — Outcome или строка причины not-applicable.

    Frontmatter пишется `join_frontmatter` (yaml.safe_dump): отсутствующее
    значение — YAML null, не голый `-` (его не разберёт split_frontmatter).
    """
    meta: dict = {
        "workstream": ws_id,
        "code": code,
        "bundle_pin": bundle_pin,
        "product_sha": product_sha,
        "response_sha256": response_sha,
        "spec_runner_version": spec_runner_version,
        "host": host,
        # Защита от переброса видна в git (ревью I-4): ключ содержимого,
        # корни и вход измерения (test_files/excluded ответа) — в файле
        # закрытия, чтобы другая машина могла пересчитать content_sha256
        # на новом product_sha и подтвердить G6 без вызова spec-runner.
        "content_key": content_key,
        "product_roots": product_roots,
        "measured_inputs": measured_inputs,
    }
    if isinstance(result, str):
        meta.update(
            closure="not-applicable", not_applicable_reason=result, human_pending=0
        )
        body = [
            f"Оракул не применим: `{result}` (спека §3.6). Приёмки по критериям нет."
        ]
    else:
        human = sum(1 for s in result.ac_status.values() if s == "human")
        traced = sum(1 for s in result.beh_status.values() if s == "traced")
        meta.update(closure=result.closure, human_pending=human)
        body = [
            f"Прослежено {traced} из {len(result.beh_status)} test-критериев; ждут человека {human}.",
            "`traced` не утверждает способность теста упасть (спека §4.1).",
            "",
            "## Стоп",
            *([f"- {r}" for r in result.stop_reasons] or ["- нет"]),
            "",
            "## Отчёт",
            *([f"- {r}" for r in result.report_rows] or ["- нет"]),
            "",
            "## AC",
            *[f"- {k}: {v}" for k, v in sorted(result.ac_status.items())],
        ]
    return join_frontmatter(meta, "# Закрытие воркстрима\n\n" + "\n".join(body) + "\n")


# ---- публикация -----------------------------------------------------------------


def _branch(state, key: str) -> str:
    return (
        f"criteria-close/{state.ws_id}-{hashlib.sha256(key.encode()).hexdigest()[:10]}"
    )


def _push_closure(state, branch: str, text: str, closure: str) -> None:
    """Коммит файла закрытия во временном worktree от origin/<base>."""
    base = state.base_ref or "master"
    tmp = Path(tempfile.mkdtemp(prefix="criteria-close-"))
    wt = tmp / "wt"
    try:
        if _git(
            state.target_dir, "worktree", "add", "--detach", str(wt), f"origin/{base}"
        ).returncode:
            raise CloseError("git worktree add не удался")
        target = wt / state.bundle_dir / CLOSURE_NAME
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        rel = f"{state.bundle_dir}/{CLOSURE_NAME}"
        steps = [
            ("add", "--", rel),
            (
                "-c",
                "user.name=criteria-close",
                "-c",
                "user.email=criteria-close@local",
                "commit",
                "-q",
                "-m",
                f"criteria-close: {state.ws_id} — {closure}",
            ),
            ("push", "--quiet", "origin", f"HEAD:refs/heads/{branch}"),
        ]
        for args in steps:
            proc = _git(wt, *args)
            if proc.returncode != 0:
                raise CloseError(f"git {args[0]}: {proc.stderr.strip()}")
    finally:
        _git(state.target_dir, "worktree", "remove", "--force", str(wt))
        shutil.rmtree(tmp, ignore_errors=True)


def _adopt_branch(state, branch: str, text: str) -> bool:
    """True — ветка ключа уже на origin и несёт ровно этот файл закрытия."""
    ref = f"refs/heads/{branch}"
    if not _git(
        state.target_dir, "ls-remote", "--exit-code", "origin", ref
    ).stdout.strip():
        return False
    if _git(
        state.target_dir,
        "fetch",
        "--quiet",
        "origin",
        f"+{ref}:refs/criteria-close/{branch}",
    ).returncode:
        raise CloseError(f"fetch {branch} не удался")
    shown = _git(
        state.target_dir,
        "show",
        f"refs/criteria-close/{branch}:{state.bundle_dir}/{CLOSURE_NAME}",
    )
    if shown.returncode != 0 or shown.stdout != text:
        raise CloseError(
            f"ветка {branch} на origin несёт другой файл закрытия — удалите её "
            f"(`git push origin --delete {branch}`) и повторите"
        )
    return True


def _publish(state, ops: Ops, run_id: str, key: str, text: str, closure: str) -> int:
    """Идемпотентная публикация под ключом: ветка несёт ключ, устаревшие PR
    закрываются, повтор после сбоя дожимает тот же текст (I1, I2)."""
    entry = _entry(run_id, key) or {}
    if entry.get("merged"):
        return 0
    branch = _branch(state, key)
    for other_key, other in _load(run_id)["measured"].items():
        if (
            other_key != key
            and other.get("pr")
            and not other.get("merged")
            and not other.get("closed")
        ):
            ops.close_pr(
                state.repo_slug, other["pr"], f"устарело: новое измерение {branch}"
            )
            _record(run_id, other_key, closed=True)
    pr = ops.find_pr(state.repo_slug, branch)
    if pr is None:
        # Ветка уже на origin (PR закрыт или create_pr упал после push) —
        # усыновить её при совпадении содержимого, а не пушить новый коммит
        # (non-fast-forward запер бы ключ навсегда; ревью #482).
        if not _adopt_branch(state, branch, text):
            _push_closure(state, branch, text, closure)
        try:
            pr = ops.create_pr(
                state.target_dir,
                state.repo_slug,
                branch,
                f"criteria-close: {state.ws_id} — {closure}",
                f"Файл закрытия воркстрима (срез 1 оракула). closure: {closure}.",
                "criteria-close",
            )
        except (subprocess.CalledProcessError, OSError, RuntimeError) as exc:
            # отказ шага с диагностикой, а не трейсбек (живая приёмка: в
            # целевом репо не было метки criteria-close; RuntimeError — gh вышел 0
            # без URL PR, ревью #537); ветка уже на origin —
            # повтор её усыновит
            detail = (getattr(exc, "stderr", "") or str(exc)).strip()
            raise CloseError(
                f"gh pr create не удался для {branch} (метка criteria-close и "
                f"права в {state.repo_slug}?): {detail[-300:]}"
            ) from exc
    _record(run_id, key, pr=pr, branch=branch)
    if ops.review(state.repo, pr) != 0:
        return 2
    head = _git(
        state.target_dir, "ls-remote", "origin", f"refs/heads/{branch}"
    ).stdout.split()
    if not head:
        raise CloseError(f"ветка {branch} не найдена на origin")
    if ops.merge(state.repo, pr, head[0]) != 0:
        return 2
    _record(run_id, key, merged=True)
    return 0


def _approval_pr_open(ops: Ops, state) -> bool | None:
    """Открыт ли PR ветки одобрения воркстрима (§3.5 п.2); None — не установлено.

    Префикс — из SSOT шаблона ветки одобрения (`approval_branches`), общий у
    candidate и finalize; незнакомое состояние PR — не «закрыт», а None."""
    try:
        template = approval_branches.candidate_template()
        prefix = template[: template.index("{wave}")].replace("{ws_id}", state.ws_id)
        prs = ops.prs_by_head_prefix(state.repo_slug, prefix)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError):
        return None
    states = [pr.get("state") for pr in prs]
    if any(s not in ("OPEN", "CLOSED", "MERGED") for s in states):
        return None
    return "OPEN" in states


# ---- измерение --------------------------------------------------------------------


def _schema() -> dict | None:
    path = criteria_contract.CONTRACT_DIR / "response.schema.json"
    return json.loads(path.read_text()) if path.exists() else None


# Не-.py файлы, решающие исход измерения: ответ-ошибку (`lock-not-current`,
# `product-roots-*`, `environment-selection-invalid`) чинят именно их правкой.
# Прочие не-.py (README, доки) в ключ не входят — иначе любая правка текста
# покупала бы перемер, то есть второй шанс флаки-тесту (§3.1 G6; devtools#515).
_MEASUREMENT_CONFIG = frozenset(
    {
        "uv.lock",
        "pyproject.toml",
        "pytest.ini",
        "tox.ini",
        "setup.cfg",
        "spec-runner.config.yaml",
        "spec/executor.config.yaml",
    }
)


def _tree_key(pin: str, root: Path, product_sha: str) -> str:
    """Ключ без известных корней (ответ-ошибка): отслеживаемые *.py и файлы
    `_MEASUREMENT_CONFIG` из дерева `product_sha` — надмножество; по git-объектам
    (id blob = хеш содержимого), не по рабочему дереву и не по stdout ответа."""
    listed = _git(root, "ls-tree", "-r", "-z", product_sha)
    if listed.returncode != 0:
        raise CloseError(f"ls-tree {product_sha[:12]}: {listed.stderr.strip()}")
    h = hashlib.sha256()
    for entry in sorted(e for e in listed.stdout.split("\0") if e):
        meta, _, rel = entry.partition("\t")
        if rel.endswith(".py") or rel in _MEASUREMENT_CONFIG:
            h.update(rel.encode())
            h.update(b"\0")
            h.update(meta.split()[-1].encode())
    return f"{pin}:tree2:{h.hexdigest()}"


def _remote_closure(state) -> dict | None:
    """Frontmatter файла закрытия на origin/<base> — защита, видимая любой машине."""
    base = state.base_ref or "master"
    proc = _git(
        state.target_dir, "show", f"origin/{base}:{state.bundle_dir}/{CLOSURE_NAME}"
    )
    if proc.returncode != 0:
        return None
    try:
        meta, _ = split_frontmatter(proc.stdout)
    except ValueError:
        return None
    return meta


def _known(run_id: str, keys: list[str]) -> tuple[str, dict] | None:
    for k in keys:
        e = _entry(run_id, k)
        if e and e.get("text"):
            return k, e
    return None


def _outside_py_digest(tree: criteria_product.Tree, files: tuple[str, ...]) -> str:
    """sha256 отсортированных (путь, blob-sha) всех отслеживаемых .py вне
    продуктовых файлов на `tree.sha`, плюс `_MEASUREMENT_CONFIG` файлов,
    присутствующих на `tree.sha` — слепок владельцев токена (§1.4) и входа
    измерения. blob-sha — из `Tree.entries()`, не из содержимого (ревью I-2:
    новый тестовый файл с токеном обязан сдвинуть пред-проверку G6, не
    только content_sha256, который его не видит — content_sha256 строится
    по test_files ОТВЕТА). `_MEASUREMENT_CONFIG` входит тем же правилом
    (ревью m-1): существующий inipath меняет content_sha256 через test_files
    ответа, но ДОБАВЛЕНИЕ нового pytest.ini/tox.ini/setup.cfg не меняет ни
    content_sha256, ни .py-слепок — без этой ветки такая правка не покупала
    бы перемер."""
    outside = _outside_entries(tree, files)
    h = hashlib.sha256()
    for path in sorted(outside, key=lambda p: p.encode()):
        h.update(path.encode())
        h.update(b"\0")
        h.update(outside[path].encode())
        h.update(b"\0")
    return h.hexdigest()


def _outside_entries(
    tree: criteria_product.Tree, files: tuple[str, ...]
) -> dict[str, str]:
    """Путь → blob-sha входа вне ключа: отслеживаемые .py вне продукта и
    `_MEASUREMENT_CONFIG` на `tree.sha` (то, что слепит `_outside_py_digest`)."""
    entries = tree.entries()
    config = {name for name in _MEASUREMENT_CONFIG if name in entries}
    candidates = set(criteria_product.tracked_py(tree)) | config
    return {p: entries[p][1] for p in candidates.difference(files)}


def _changed_outside_key(
    root: Path, prev_sha: str, product_sha: str
) -> list[str] | None:
    """Файлы вне ключа, чьи байты различаются между измеренным `prev_sha` и
    `product_sha`; `None` — не установить (дерево недоступно/конфиг не читается)."""
    found = []
    try:
        for sha in (prev_sha, product_sha):
            tree = criteria_product.Tree(root, sha)
            decl = criteria_product.read_declaration(tree)
            found.append(
                _outside_entries(tree, criteria_product.resolve_roots(tree, decl.roots))
            )
    except criteria_product.ProductError:
        return None
    before, after = found
    return sorted(
        p for p in before.keys() | after.keys() if before.get(p) != after.get(p)
    )


def _measured_sha(entry: dict | None, remote: dict | None, key: str) -> str | None:
    """product_sha измерения ключа `key`: локальная запись (её frontmatter),
    иначе закрытие на default-ветке (машина без локального состояния)."""
    if entry and entry.get("text"):
        try:
            meta, _ = split_frontmatter(entry["text"])
        except ValueError:
            meta = {}
        if isinstance(meta.get("product_sha"), str):
            return meta["product_sha"]
    if remote and remote.get("content_key") == key:
        sha = remote.get("product_sha")
        return sha if isinstance(sha, str) else None
    return None


def _key_measured_message(
    root: Path, key: str, product_sha: str, prev_sha: str | None
) -> str:
    """Отказ «ключ измерен» (код 6). Изменились файлы вне ключа — назвать
    их, не утверждая, что они решали исход (решение владельца по
    `criteria-close-key-outside-helpers`: ограничение v1, ключ не расширяем)."""
    head = f"criteria-close: ключ измерен ({key}) — §3.1 G6"
    changed = _changed_outside_key(root, prev_sha, product_sha) if prev_sha else None
    if changed is None:
        return (
            f"{head}: новый результат не опубликован; изменения вне ключа "
            "измерения установить не удалось (прежний product_sha недоступен)"
        )
    if not changed:
        return f"{head}: новый результат того же содержимого не публикуется"
    return (
        f"{head}. Изменились файлы вне ключа измерения: {', '.join(changed)}; "
        "ключ остался прежним. Новый результат не опубликован "
        "(ограничение v1: ключ не покрывает файлы вне продукта и тестов ответа; "
        "перечислены .py и конфиги измерения, не-.py данные тестов не "
        "отслеживаются)"
    )


def _test_named(path: str) -> bool:
    """Имена файлов, которые pytest собирает по умолчанию (`python_files`),
    плюс conftest.py."""
    name = path.rsplit("/", 1)[-1]
    return (
        name == "conftest.py"
        or fnmatch.fnmatchcase(name, "test_*.py")
        or fnmatch.fnmatchcase(name, "*_test.py")
    )


def _owner_candidates(
    tree: criteria_product.Tree,
    files: tuple[str, ...],
    test_files: list[str],
    excluded: list[str],
) -> list[str]:
    """Кандидаты во владельцы токена (§3.1, ревью #532): отслеживаемые .py
    вне продукта, которые pytest в принципе мог бы собрать, — test_files
    ответа ∪ .py под skipped/ignored-путями ∪ имена по шаблонам pytest по
    умолчанию (`_test_named`). Владелец в файле вне этих границ не виден —
    названная граница (иначе вендоренные фикстуры с токенами вечно
    блокировали бы закрытие)."""
    test_set = set(test_files)
    return [
        p
        for p in criteria_product.tracked_py(tree)
        if p not in files
        and (
            p in test_set
            or _test_named(p)
            or any(criteria_check._covers(e, p) for e in excluded)
        )
    ]


def _recompute_measurement_key(
    bundle_pin: str, root: Path, product_sha: str, stored: object
) -> str | None:
    """G6: пересчитывает `f"{bundle_pin}:v1:{content_sha256}"` на
    `product_sha` из `stored` — ранее сохранённый вход измерения
    (`measured_inputs`/локальный `inputs`: test_files/excluded/outside_py).
    `outside_py` — только вход пред-проверки, не часть ключа (ревью #532):
    его сдвиг перевызывает spec-runner, но второй результат того же ключа
    не публикуется — это держит пост-проверка в `_measure`. `None` — вход
    неприменим (форма `stored` не та, конфиг
    на новом product_sha снесён/невалиден, или вне-продуктовый .py-слепок
    разошёлся, то есть появился/исчез тестовый файл — ревью I-2) —
    измерять заново. Форма `stored` — данные из committed frontmatter или
    локального state.json, не доверяется побайтово (ревью M-1): любая
    неверная форма отбрасывает кандидата, а не роняет KeyError/TypeError."""
    if not isinstance(stored, dict):
        return None
    test_files, excluded, outside_py = (
        stored.get("test_files"),
        stored.get("excluded"),
        stored.get("outside_py"),
    )
    if (
        not isinstance(test_files, list)
        or not all(isinstance(p, str) for p in test_files)
        or not isinstance(excluded, list)
        or not all(isinstance(p, str) for p in excluded)
        or not isinstance(outside_py, str)
    ):
        return None
    tree = criteria_product.Tree(root, product_sha)
    try:
        decl = criteria_product.read_declaration(tree)
        files = criteria_product.resolve_roots(tree, decl.roots)
        lock = tree.blob("uv.lock")
        lock_sha = hashlib.sha256(lock).hexdigest() if lock is not None else ""
        content = criteria_product.content_sha256(
            tree, decl, lock_sha, test_files, excluded
        )
    except criteria_product.ProductError:
        return None
    if _outside_py_digest(tree, files) != outside_py:
        return None
    return f"{bundle_pin}:v1:{content}"


def _measure(
    state,
    ops: Ops,
    run_id: str,
    charter,
    nodes: dict[str, str],
    bundle_pin: str,
    product_sha: str,
    installed: str | None,
    host: str,
) -> tuple[str, str, str] | int:
    """→ (ключ, closure, текст) или код выхода (2 — отказ шага, 6 — ключ измерен)."""
    root = Path(state.target_dir)
    # G6 пред-проверка (C.4): вход уже измеренного ответа пересчитывает
    # content_sha256 на текущем product_sha — совпало с уже измеренным
    # ключом, новый вызов spec-runner не нужен. Два источника входа:
    # (а) этот run_id на этой машине (data["inputs"]); (б) закрытие уже на
    # origin/<base> (I-4, ревью среза 1) — другая машина без локального
    # state пересчитывает по `measured_inputs` из frontmatter и сверяет с
    # его же `content_key`, без обращения к (а).
    remote = _remote_closure(state)
    cands = [_tree_key(bundle_pin, root, product_sha)]
    inputs = _load(run_id).get("inputs")
    if inputs:
        k = _recompute_measurement_key(bundle_pin, root, product_sha, inputs)
        if k is not None:
            cands.insert(0, k)
    if (
        remote
        and remote.get("bundle_pin") == bundle_pin
        and remote.get("product_roots")
        and remote.get("measured_inputs")
    ):
        k = _recompute_measurement_key(
            bundle_pin, root, product_sha, remote["measured_inputs"]
        )
        if k is not None:
            cands.insert(0, k)
    known = _known(run_id, cands)
    if known is not None:  # то же содержимое: без нового вызова spec-runner
        k, e = known
        if e.get("merged"):
            print(
                "criteria-close: это содержимое уже измерено и опубликовано — §3.1 G6"
            )
            return 6
        return k, e["closure"], e["text"]
    if remote and remote.get("content_key") in cands:
        print(
            "criteria-close: закрытие этого содержимого уже в default-ветке — §3.1 G6: "
            "доработайте продукт"
        )
        return 6

    graph = criteria_graph.build_graph(
        nodes["10-requirements.md"],
        nodes["15-behaviour-spec.md"],
        nodes["25-acceptance.md"],
    )
    tests = [b.id for b in criteria_graph.test_behs(graph)]
    request = {
        "protocol": 1,
        "owner_repo": state.repo_slug,
        "workstream": state.ws_id,
        "code": charter.code,
        "bundle_pin": bundle_pin,
        "product_sha": product_sha,
        "test_criteria": [
            {"id": f"{charter.code}:{b}", "verify_task": False} for b in tests
        ],
    }
    req_path = STATE_ROOT / run_id / "request.json"
    req_path.parent.mkdir(parents=True, exist_ok=True)
    req_path.write_text(json.dumps(request, indent=2))
    code, out = ops.criteria_verify(state.target_dir, str(req_path))
    parsed, why = criteria_check.parse_response(code, out, _schema())
    if parsed is None:
        print(f"criteria-close: отказ шага — {why}")
        return 2
    resp = parsed.response
    if "request" in resp:
        if resp["request"] != request:
            # §5.3: эхо не доверяется ни в одной ветке, включая ошибку —
            # чужой/устаревший ответ (product_sha/code/bundle_pin не те) не
            # должен сжигать G6 этого содержимого публикацией blocked под его
            # ключом (review I-1). Ничего не пишется и не публикуется.
            print("criteria-close: отказ шага — эхо request не совпало с запросом")
            return 2
    elif parsed.branch == "error" and not parsed.retryable:
        # §5.3, поправлена этой веткой: эхо обязателен и на ветке ошибки без
        # повтора (exit 3) — честный производитель опускает `request` только
        # на `request-invalid` (exit 2, retryable, spec-runner
        # criteria_measure.py). Без проверки чужой/устаревший exit-3 без эхо
        # сжёг бы G6 правильного содержимого публикацией blocked под его
        # ключом — ровно та дыра, которую должен был закрыть I-1 (review I-2).
        print("criteria-close: отказ шага — ответ-ошибка (exit 3) без эхо request")
        return 2
    if parsed.branch == "error" and parsed.retryable:
        e = resp["error"]
        print(
            f"criteria-close: повторяемый отказ spec-runner — {e['kind']}: {e['detail']}"
        )
        return 2  # ключ не пишется: повтор — не второе измерение (C.4)
    if parsed.branch == "error":
        result: object = criteria_check.Outcome(
            "blocked",
            {},
            {},
            [f"ошибка ответа: {resp['error']['kind']}: {resp['error']['detail']}"],
            [],
        )
        closure, key, roots, measured_inputs = (
            "blocked",
            _tree_key(bundle_pin, root, product_sha),
            None,
            None,
        )
    else:
        tree = criteria_product.Tree(root, product_sha)
        try:
            decl = criteria_product.read_declaration(tree)
            files = criteria_product.resolve_roots(tree, decl.roots)
            lock = tree.blob("uv.lock")
            excluded = [
                e["path"]
                for e in resp["collection_excluded"]
                if e["how"] != "deselected"
            ]
            lock_sha = hashlib.sha256(lock).hexdigest() if lock is not None else ""
            content = criteria_product.content_sha256(
                tree, decl, lock_sha, resp["test_files"], excluded
            )
            # m-4: синтаксис продукта, который devtools не разбирает, —
            # именованный ProductError, пойманный здесь же, а не тихий
            # fallback снаружи try.
            function_lines = criteria_product.function_body_lines(tree, list(files))
        except criteria_product.ProductError as exc:
            print(f"criteria-close: ответ spec-runner отвергнут — {exc}")
            return 2
        sources: dict[str, str] = {}
        for p in _owner_candidates(tree, files, resp["test_files"], excluded):
            blob = tree.blob(p)
            if blob is None:
                # отслеживаемый путь без blob — сбой git-объекта, не «файла
                # нет» (ls-tree его только что перечислил); отказ шага, не
                # пустой источник, который тихо теряет владельца (review m-2).
                raise CloseError(
                    f"{p}: blob недоступен на {product_sha[:12]} (git show)"
                )
            sources[p] = blob.decode("utf-8", errors="replace")
        checked = criteria_check.validate_answer(
            request,
            resp,
            declared_roots=decl.roots,
            resolved_files=files,
            groups=decl.groups,
            extras=decl.extras,
            lock_sha=lock_sha,
            content_sha=content,
            function_lines=function_lines,
            owners_map=criteria_check.owners(sources, charter.code, tests),
            installed=installed,
        )
        if checked.problems:
            print(
                "criteria-close: ответ spec-runner отвергнут:\n  "
                + "\n  ".join(checked.problems)
            )
            return 2
        result = criteria_check.outcome(graph, checked.beh_status, checked.beh_reason)
        result.report_rows.extend(f"{b}: {n}" for b, n in sorted(checked.notes.items()))
        measured_inputs = {
            "test_files": resp["test_files"],
            "excluded": excluded,
            # вне-продуктовый .py-слепок — вход пред-проверки G6, не ключа:
            # новый тестовый файл с токеном обязан её сдвинуть (ревью I-2).
            "outside_py": _outside_py_digest(tree, files),
        }
        closure, key, roots = (
            result.closure,
            f"{bundle_pin}:v1:{content}",
            list(decl.roots),
        )

    # G6 пост-проверка (ревью #532): пред-проверка могла пропустить уже
    # измеренный ключ (сдвинулся outside_py — новый/удалённый вне-продуктовый
    # .py или конфиг измерения), но второй результат того же содержимого не
    # публикуется и не затирает запись ключа (pr/branch/merged).
    # Вход этого ответа сохраняется ДО пост-проверки: иначе на том же
    # содержимом каждый следующий прогон снова платил бы измерение, чтобы
    # ответить 6 (пред-проверка видела бы вход прошлого sha; ревью #532).
    if measured_inputs is not None:
        data = _load(run_id)
        data["inputs"] = measured_inputs
        _save(run_id, data)
    known = _known(run_id, [key])
    if known is not None and not known[1].get("merged"):
        return key, known[1]["closure"], known[1]["text"]  # первый результат
    if known is not None or (remote and remote.get("content_key") == key):
        prev = _measured_sha(known[1] if known else None, remote, key)
        print(_key_measured_message(root, key, product_sha, prev))
        return 6
    text = render_closure(
        result,
        ws_id=state.ws_id,
        code=charter.code,
        bundle_pin=bundle_pin,
        product_sha=product_sha,
        response_sha=hashlib.sha256(out.encode()).hexdigest(),
        spec_runner_version=installed,
        host=host,
        content_key=key,
        product_roots=roots,
        measured_inputs=measured_inputs,
    )
    data = _load(run_id)
    data["measured"][key] = {"closure": closure, "text": text}
    _save(run_id, data)
    return key, closure, text


def run(run_id: str, ops: Ops, *, product_sha: str | None = None) -> int:
    """0 — файл закрытия опубликован; 2 — отказ шага; 6 — ключ измерен."""
    state = run_state.load(run_id)
    if state.status != "completed":
        print(
            f"criteria-close: прогон {run_id} в статусе {state.status!r}, нужен completed"
        )
        return 2
    bundle_pin = runner._verified_result_sha(state)
    if bundle_pin is None:
        print(
            "criteria-close: у прогона нет идентичности результата (finalize) — пин неизвестен"
        )
        return 2
    try:
        nodes = _bundle_at_pin(state, bundle_pin)
        charter = charter_guard.read_charter(nodes["00-charter.md"])
        if charter.malformed:
            raise CloseError("frontmatter charter не разбирается")
        sha = _verify_product(state, product_sha)
        installed = task_bridge.spec_runner_version()
        host = socket.gethostname()
        na = decide_not_applicable(
            charter,
            spec_runner_contract.target_selector_policy(state.target_dir),
            installed,
            criteria_contract.read_min_version(),
            is_vendored=criteria_contract.vendored(),
        )
        if na is not None:
            key = f"{bundle_pin}:{sha}:na:{na}:{installed}"
            entry = _entry(run_id, key)
            text = (
                entry["text"]
                if entry and entry.get("text")
                else render_closure(
                    na,
                    ws_id=state.ws_id,
                    code=charter.code,
                    bundle_pin=bundle_pin,
                    product_sha=sha,
                    response_sha=None,
                    spec_runner_version=installed,
                    host=host,
                )
            )
            _record(run_id, key, closure="not-applicable", text=text)
            return _publish(state, ops, run_id, key, text, "not-applicable")
        measured = _measure(
            state, ops, run_id, charter, nodes, bundle_pin, sha, installed, host
        )
        if isinstance(measured, int):
            return measured
        key, closure, text = measured
        return _publish(state, ops, run_id, key, text, closure)
    except CloseError as exc:
        print(f"criteria-close: отказ шага — {exc}")
        return 2


def main(argv: list[str] | None = None) -> int:
    """CLI `make criteria-close ARGS='--run <id> [--product-sha <sha>]'`."""
    parser = argparse.ArgumentParser(prog="criteria-close")
    parser.add_argument("--run", required=True)
    parser.add_argument("--product-sha")
    args = parser.parse_args(argv)
    return run(args.run, RealOps(), product_sha=args.product_sha)


if __name__ == "__main__":
    sys.exit(main())
