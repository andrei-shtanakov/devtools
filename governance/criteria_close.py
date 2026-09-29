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
import ast
import hashlib
import json
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from governance import (
    charter_guard,
    criteria_check,
    criteria_contract,
    criteria_graph,
    run_state,
    runner,
    spec_runner_contract,
    task_bridge,
)
from governance.frontmatter import join_frontmatter, split_frontmatter
from governance.ops import Ops, RealOps

STATE_ROOT = Path(__file__).resolve().parent.parent / "out" / "criteria-close"
CLOSURE_NAME = "90-acceptance-closure.md"
_NODES = ("00-charter.md", "10-requirements.md", "15-behaviour-spec.md", "25-acceptance.md")


class CloseError(RuntimeError):
    """Отказ шага с названной причиной (код 2)."""


def decide_not_applicable(charter, selector_policy, installed, minimum, *, is_vendored) -> str | None:
    """Порядок: схема 1 → язык → доступность оракула (вендоринг и версия машины)."""
    if charter.schema != 2:
        return "schema-1"
    if selector_policy is not None and selector_policy.name != "pytest":
        return "language"
    if not criteria_contract.oracle_available(installed, minimum, is_vendored=is_vendored):
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
        raise CloseError(f"HEAD {head[:12]} ≠ product_sha {sha[:12]} — выполните checkout")
    return sha


# ---- рендер ---------------------------------------------------------------------


def render_closure(result, *, ws_id, code, bundle_pin, product_sha, response_sha,
                   spec_runner_version, host, content_key: str | None = None,
                   product_roots: list[str] | None = None) -> str:
    """Текст файла закрытия; result — Outcome или строка причины not-applicable.

    Frontmatter пишется `join_frontmatter` (yaml.safe_dump): отсутствующее
    значение — YAML null, не голый `-` (его не разберёт split_frontmatter).
    """
    meta: dict = {"workstream": ws_id, "code": code, "bundle_pin": bundle_pin,
                  "product_sha": product_sha, "response_sha256": response_sha,
                  "spec_runner_version": spec_runner_version, "host": host,
                  # Защита от переброса видна в git (ревью I-4): ключ содержимого
                  # и корни, по которым он посчитан, — в файле закрытия.
                  "content_key": content_key, "product_roots": product_roots}
    if isinstance(result, str):
        meta.update(closure="not-applicable", not_applicable_reason=result, human_pending=0)
        body = [f"Оракул не применим: `{result}` (спека §3.6). Приёмки по критериям нет."]
    else:
        human = sum(1 for s in result.ac_status.values() if s == "human")
        traced = sum(1 for s in result.beh_status.values() if s == "traced")
        meta.update(closure=result.closure, human_pending=human)
        body = [
            f"Прослежено {traced} из {len(result.beh_status)} test-критериев; ждут человека {human}.",
            "`traced` не утверждает способность теста упасть (спека §4.1).",
            "", "## Стоп", *([f"- {r}" for r in result.stop_reasons] or ["- нет"]),
            "", "## Отчёт", *([f"- {r}" for r in result.report_rows] or ["- нет"]),
            "", "## AC", *[f"- {k}: {v}" for k, v in sorted(result.ac_status.items())],
        ]
    return join_frontmatter(meta, "# Закрытие воркстрима\n\n" + "\n".join(body) + "\n")


# ---- публикация -----------------------------------------------------------------


def _branch(state, key: str) -> str:
    return f"criteria-close/{state.ws_id}-{hashlib.sha256(key.encode()).hexdigest()[:10]}"


def _push_closure(state, branch: str, text: str, closure: str) -> None:
    """Коммит файла закрытия во временном worktree от origin/<base>."""
    base = state.base_ref or "master"
    tmp = Path(tempfile.mkdtemp(prefix="criteria-close-"))
    wt = tmp / "wt"
    try:
        if _git(state.target_dir, "worktree", "add", "--detach", str(wt), f"origin/{base}").returncode:
            raise CloseError("git worktree add не удался")
        target = wt / state.bundle_dir / CLOSURE_NAME
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        rel = f"{state.bundle_dir}/{CLOSURE_NAME}"
        steps = [
            ("add", "--", rel),
            ("-c", "user.name=criteria-close", "-c", "user.email=criteria-close@local",
             "commit", "-q", "-m", f"criteria-close: {state.ws_id} — {closure}"),
            ("push", "--quiet", "origin", f"HEAD:refs/heads/{branch}"),
        ]
        for args in steps:
            proc = _git(wt, *args)
            if proc.returncode != 0:
                raise CloseError(f"git {args[0]}: {proc.stderr.strip()}")
    finally:
        _git(state.target_dir, "worktree", "remove", "--force", str(wt))
        shutil.rmtree(tmp, ignore_errors=True)


def _publish(state, ops: Ops, run_id: str, key: str, text: str, closure: str) -> int:
    """Идемпотентная публикация под ключом: ветка несёт ключ, устаревшие PR
    закрываются, повтор после сбоя дожимает тот же текст (I1, I2)."""
    entry = _entry(run_id, key) or {}
    if entry.get("merged"):
        return 0
    branch = _branch(state, key)
    for other_key, other in _load(run_id)["measured"].items():
        if other_key != key and other.get("pr") and not other.get("merged") and not other.get("closed"):
            ops.close_pr(state.repo_slug, other["pr"], f"устарело: новое измерение {branch}")
            _record(run_id, other_key, closed=True)
    pr = ops.find_pr(state.repo_slug, branch)
    if pr is None:
        _push_closure(state, branch, text, closure)
        pr = ops.create_pr(
            state.target_dir, state.repo_slug, branch,
            f"criteria-close: {state.ws_id} — {closure}",
            f"Файл закрытия воркстрима (срез 1 оракула). closure: {closure}.",
            "criteria-close",
        )
    _record(run_id, key, pr=pr, branch=branch)
    if ops.review(state.repo, pr) != 0:
        return 2
    head = _git(state.target_dir, "ls-remote", "origin", f"refs/heads/{branch}").stdout.split()
    if not head:
        raise CloseError(f"ветка {branch} не найдена на origin")
    if ops.merge(state.repo, pr, head[0]) != 0:
        return 2
    _record(run_id, key, merged=True)
    return 0


# ---- измерение --------------------------------------------------------------------


def _py_files(root: Path, sub: str) -> dict[str, str]:
    base = root / sub
    if not base.exists():
        return {}
    return {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8", errors="replace")
        for p in sorted(base.rglob("*.py"))
    }


def _content_sha(root: Path, subdirs: list[str]) -> str:
    """sha256 по отсортированным (путь, байты) *.py продуктовых корней и тестов
    — ключ измерения §3.1 G6 и независимый пересчёт `content_sha256` (§5.3)."""
    files = sorted({p for s in subdirs if (root / s).exists() for p in (root / s).rglob("*.py")})
    h = hashlib.sha256()
    for p in files:
        h.update(p.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(p.read_bytes())
    return h.hexdigest()


def _is_test_file(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    return (
        "tests" in parts[:-1] or "test" in parts[:-1] or name == "conftest.py"
        or name.startswith("test_") or name.endswith("_test.py")
    )


def _function_lines(root: Path, roots: list[str]) -> dict[str, set[int]]:
    """Строки внутри тел функций/методов продуктовых корней (G0 rev 8)."""
    out: dict[str, set[int]] = {}
    for r in roots:
        for path, src in _py_files(root, r).items():
            if _is_test_file(path):
                continue  # тесты внутри продуктового корня — не продукт (ревью I-2)
            lines: set[int] = set()
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
                    lines.update(range(node.body[0].lineno, (node.end_lineno or 0) + 1))
            out[path] = lines
    return out


def _schema() -> dict | None:
    path = criteria_contract.CONTRACT_DIR / "response.schema.json"
    return json.loads(path.read_text()) if path.exists() else None


def _key(pin: str, root: Path, roots: list[str]) -> str:
    return f"{pin}:{_content_sha(root, [*roots, 'tests'])}"


def _tree_key(pin: str, root: Path) -> str:
    """Ключ без известных корней (ответ-ошибка): все отслеживаемые *.py —
    надмножество; по содержимому, не по stdout ответа (ревью I-3)."""
    files = _git(root, "ls-files", "-z", "--", "*.py").stdout.split("\0")
    h = hashlib.sha256()
    for rel in sorted(f for f in files if f):
        h.update(rel.encode())
        h.update(b"\0")
        h.update((root / rel).read_bytes())
    return f"{pin}:tree:{h.hexdigest()}"


def _remote_closure(state) -> dict | None:
    """Frontmatter файла закрытия на origin/<base> — защита, видимая любой машине."""
    base = state.base_ref or "master"
    proc = _git(state.target_dir, "show", f"origin/{base}:{state.bundle_dir}/{CLOSURE_NAME}")
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


def _measure(state, ops: Ops, run_id: str, charter, nodes: dict[str, str], bundle_pin: str,
             product_sha: str, installed: str | None, host: str) -> tuple[str, str, str] | int:
    """→ (ключ, closure, текст) или код выхода (2 — отказ шага, 6 — ключ измерен)."""
    root = Path(state.target_dir)
    remote = _remote_closure(state)
    cands = [_tree_key(bundle_pin, root)]
    prev_roots = _load(run_id).get("roots")
    if prev_roots:
        cands.insert(0, _key(bundle_pin, root, prev_roots))
    if remote and remote.get("bundle_pin") == bundle_pin and remote.get("product_roots"):
        cands.insert(0, _key(bundle_pin, root, list(remote["product_roots"])))
    known = _known(run_id, cands)
    if known is not None:  # то же содержимое: без нового вызова spec-runner
        k, e = known
        if e.get("merged"):
            print("criteria-close: это содержимое уже измерено и опубликовано — §3.1 G6")
            return 6
        return k, e["closure"], e["text"]
    if remote and remote.get("content_key") in cands:
        print("criteria-close: закрытие этого содержимого уже в default-ветке — §3.1 G6: "
              "доработайте продукт")
        return 6
    graph = criteria_graph.build_graph(
        nodes["10-requirements.md"], nodes["15-behaviour-spec.md"], nodes["25-acceptance.md"]
    )
    tests = [b.id for b in criteria_graph.test_behs(graph)]
    request = {
        "protocol": 1, "owner_repo": state.repo, "workstream": state.ws_id,
        "code": charter.code, "bundle_pin": bundle_pin, "product_sha": product_sha,
        "test_criteria": [{"id": f"{charter.code}:{b}", "verify_task": False} for b in tests],
    }
    req_path = STATE_ROOT / run_id / "request.json"
    req_path.parent.mkdir(parents=True, exist_ok=True)
    req_path.write_text(json.dumps(request, indent=2))
    code, out = ops.criteria_verify(state.target_dir, str(req_path))
    response, why = criteria_check.parse_response(code, out, _schema())
    if response is None:
        print(f"criteria-close: отказ шага — {why}")
        return 2
    roots = [str(r) for r in response.get("product_roots") or []]
    lock = root / "uv.lock"
    expected = criteria_check.expected_definitions(_py_files(root, "tests"), charter.code, tests)
    problems = criteria_check.validate_response(
        request, response, expected=expected,
        function_lines=_function_lines(root, roots) if not criteria_check.roots_findings(roots) else {},
        lock_sha=hashlib.sha256(lock.read_bytes()).hexdigest() if lock.exists() else "",
        content_sha=_content_sha(root, [*roots, "tests"]) if roots else "",
    )
    if problems:
        print("criteria-close: ответ spec-runner отвергнут:\n  " + "\n  ".join(problems))
        return 2
    if response.get("not_applicable") is not None:
        result: object = str(response["not_applicable"])
        closure = "not-applicable"
    elif response.get("error") is not None:
        result = criteria_check.Outcome(
            "blocked", {}, {}, [f"ошибка уровня ответа: {response['error']}"], [])
        closure = "blocked"
    else:
        beh_status = {b["id"].split(":", 1)[1]: b["status"] for b in response["beh"]}
        result = criteria_check.outcome(graph, beh_status)
        closure = result.closure
    key = _key(bundle_pin, root, roots) if roots else _tree_key(bundle_pin, root)
    known = _known(run_id, [key])
    if known is not None:
        print(f"criteria-close: ключ измерен ({known[1].get('closure')}) — §3.1 G6: новый "
              "результат того же содержимого не публикуется; доработайте продукт")
        return 6
    text = render_closure(result, ws_id=state.ws_id, code=charter.code, bundle_pin=bundle_pin,
                          product_sha=product_sha,
                          response_sha=hashlib.sha256(out.encode()).hexdigest(),
                          spec_runner_version=installed, host=host,
                          content_key=key, product_roots=roots or None)
    data = _load(run_id)
    if roots:
        data["roots"] = roots
    data["measured"][key] = {"closure": closure, "text": text}
    _save(run_id, data)
    return key, closure, text


def run(run_id: str, ops: Ops, *, product_sha: str | None = None) -> int:
    """0 — файл закрытия опубликован; 2 — отказ шага; 6 — ключ измерен."""
    state = run_state.load(run_id)
    if state.status != "completed":
        print(f"criteria-close: прогон {run_id} в статусе {state.status!r}, нужен completed")
        return 2
    bundle_pin = runner._verified_result_sha(state)
    if bundle_pin is None:
        print("criteria-close: у прогона нет идентичности результата (finalize) — пин неизвестен")
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
            charter, spec_runner_contract.target_selector_policy(state.target_dir),
            installed, criteria_contract.read_min_version(),
            is_vendored=criteria_contract.vendored(),
        )
        if na is not None:
            key = f"{bundle_pin}:{sha}:na:{na}:{installed}"
            entry = _entry(run_id, key)
            text = entry["text"] if entry and entry.get("text") else render_closure(
                na, ws_id=state.ws_id, code=charter.code, bundle_pin=bundle_pin,
                product_sha=sha, response_sha=None, spec_runner_version=installed, host=host)
            _record(run_id, key, closure="not-applicable", text=text)
            return _publish(state, ops, run_id, key, text, "not-applicable")
        measured = _measure(state, ops, run_id, charter, nodes, bundle_pin, sha, installed, host)
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
