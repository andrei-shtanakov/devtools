"""criteria_close — закрытие воркстрима по оракулу бандла, срез 1 (спека §7.1).

Измерение (spec-runner verify --criteria) → сверка (§5.3) → исход (§3.3) →
файл закрытия `workstreams/<ws>/spec/90-acceptance-closure.md` агентским PR
(создаёт учётка оператора, scope-аттестация ai-prosto, мерж merge-pr.sh).
Ревизий, подписи и штампа нет — это срез 2. Флага обхода стопа нет и быть
не должно (спека §3.3).
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import socket
import sys
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
from governance.frontmatter import join_frontmatter
from governance.ops import Ops, RealOps

STATE_ROOT = Path(__file__).resolve().parent.parent / "out" / "criteria-close"
CLOSURE_NAME = "90-acceptance-closure.md"


def decide_not_applicable(charter, selector_policy, installed, minimum, *, is_vendored) -> str | None:
    """Порядок: схема 1 → язык → доступность оракула (вендоринг и версия машины)."""
    if charter.schema != 2:
        return "schema-1"
    if selector_policy is not None and selector_policy.name != "pytest":
        return "language"
    if not criteria_contract.oracle_available(installed, minimum, is_vendored=is_vendored):
        return "spec-runner-version"
    return None


def _state_path(run_id: str) -> Path:
    return STATE_ROOT / run_id / "state.json"


def _load(run_id: str) -> dict:
    p = _state_path(run_id)
    return json.loads(p.read_text()) if p.exists() else {"measured": {}}


def _save(run_id: str, data: dict) -> None:
    p = _state_path(run_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(p)


def _already_measured(run_id: str, key: str) -> str | None:
    return _load(run_id)["measured"].get(key)


def _record_measured(run_id: str, key: str, closure: str) -> None:
    data = _load(run_id)
    data["measured"][key] = closure
    _save(run_id, data)


def render_closure(result, *, ws_id, code, bundle_pin, product_sha, response_sha,
                   spec_runner_version, host) -> str:
    """Текст файла закрытия; result — Outcome или строка причины not-applicable.

    Frontmatter пишется `join_frontmatter` (yaml.safe_dump): отсутствующее
    значение — YAML null, не голый `-` (его не разберёт split_frontmatter).
    """
    meta: dict = {"workstream": ws_id, "code": code, "bundle_pin": bundle_pin,
                  "product_sha": product_sha, "response_sha256": response_sha,
                  "spec_runner_version": spec_runner_version, "host": host}
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


def _publish(state, ops: Ops, text: str, closure: str) -> int:
    """Агентский PR файла закрытия: write-ahead ветки, аттестация, мерж."""
    rel = f"{state.bundle_dir}/{CLOSURE_NAME}"
    branch = f"criteria-close/{state.ws_id}"
    existing = ops.find_pr(state.repo_slug, branch)
    if existing is None:
        ops.ensure_branch(state.target_dir, branch)
        (Path(state.target_dir) / rel).write_text(text, encoding="utf-8")
        ops.commit_paths(state.target_dir, [rel], f"criteria-close: {state.ws_id} — {closure}")
        ops.push_branch(state.target_dir, branch)
        pr = ops.create_pr(state.target_dir, state.repo_slug, branch,
                           f"criteria-close: {state.ws_id} — {closure}",
                           f"Файл закрытия воркстрима (срез 1 оракула). closure: {closure}.",
                           "criteria-close")
    else:
        pr = existing
    if ops.review(state.repo, pr) != 0:
        return 2
    head = ops.head_sha(state.target_dir, branch)
    return 0 if ops.merge(state.repo, pr, head) == 0 else 2

def run(run_id: str, ops: Ops, *, product_sha: str | None = None) -> int:
    state = run_state.load(run_id)
    if state.status != "completed":
        print(f"criteria-close: прогон {run_id} в статусе {state.status!r}, нужен completed")
        return 2
    bundle_pin = runner._verified_result_sha(state)
    if bundle_pin is None:
        print("criteria-close: у прогона нет идентичности результата (finalize) — пин неизвестен")
        return 2
    bundle = Path(state.target_dir) / state.bundle_dir
    charter = charter_guard.read_charter((bundle / "00-charter.md").read_text(encoding="utf-8"))
    product_sha = product_sha or ops.head_sha(state.target_dir, "HEAD")
    installed = task_bridge.spec_runner_version()
    host = socket.gethostname()
    na = decide_not_applicable(
        charter, spec_runner_contract.target_selector_policy(state.target_dir),
        installed, criteria_contract.read_min_version(),
        is_vendored=criteria_contract.vendored(),
    )
    if na is not None:
        text = render_closure(na, ws_id=state.ws_id, code=charter.code,
                              bundle_pin=bundle_pin, product_sha=product_sha, response_sha=None,
                              spec_runner_version=installed, host=host)
        return _publish(state, ops, text, "not-applicable")
    return _measure_and_publish(state, ops, charter, bundle, bundle_pin, product_sha,
                                installed, host)


def _py_files(root: Path, sub: str) -> dict[str, str]:
    base = root / sub
    if not base.exists():
        return {}
    return {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8", errors="replace")
        for p in sorted(base.rglob("*.py"))
    }


def _content_sha(root: Path, subdirs: list[str] | None = None) -> str:
    """sha256 по отсортированным (путь, байты) *.py.

    `subdirs=None` — все *.py репо: ключ запрета повторного измерения (§3.1
    G6) до ответа, когда продуктовые корни ещё неизвестны (надмножество —
    строже). С `subdirs` — продуктовые корни ответа + tests: независимый
    пересчёт `content_sha256` ответа (§5.3), итог производителя не принимается.
    """
    bases = [root] if subdirs is None else [root / s for s in subdirs]
    files = sorted({
        p for b in bases if b.exists() for p in b.rglob("*.py")
        if not any(part.startswith(".") for part in p.relative_to(root).parts)
    })
    h = hashlib.sha256()
    for p in files:
        h.update(p.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(p.read_bytes())
    return h.hexdigest()


def _function_lines(root: Path, roots: list[str]) -> dict[str, set[int]]:
    """Строки внутри тел функций/методов продуктовых корней (G0 rev 8)."""
    out: dict[str, set[int]] = {}
    for r in roots:
        for path, src in _py_files(root, r).items():
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


def _measure_and_publish(state, ops: Ops, charter, bundle: Path, bundle_pin: str,
                         product_sha: str, installed: str | None, host: str) -> int:
    """Измерение → сверка → исход → файл закрытия (спека §3.1–3.3, §5)."""
    root = Path(state.target_dir)
    graph = criteria_graph.build_graph(
        (bundle / "10-requirements.md").read_text(encoding="utf-8"),
        (bundle / "15-behaviour-spec.md").read_text(encoding="utf-8"),
        (bundle / "25-acceptance.md").read_text(encoding="utf-8"),
    )
    key = f"{bundle_pin}:{_content_sha(root)}"
    prior = _already_measured(state.run_id, key)
    if prior is not None:
        print(f"criteria-close: ключ измерен ({prior}) — §3.1 G6: доработайте продукт")
        return 6
    tests = [b.id for b in criteria_graph.test_behs(graph)]
    request = {
        "protocol": 1, "owner_repo": state.repo, "workstream": state.ws_id,
        "code": charter.code, "bundle_pin": bundle_pin, "product_sha": product_sha,
        "test_criteria": [{"id": f"{charter.code}:{b}", "verify_task": False} for b in tests],
    }
    req_path = STATE_ROOT / state.run_id / "request.json"
    req_path.parent.mkdir(parents=True, exist_ok=True)
    req_path.write_text(json.dumps(request, indent=2))
    code, out = ops.criteria_verify(state.target_dir, str(req_path))
    response, why = criteria_check.parse_response(code, out, _schema())
    if response is None:
        print(f"criteria-close: отказ шага — {why}")
        return 2
    if response.get("error") is not None:
        result = criteria_check.Outcome(
            "blocked", {}, {}, [f"ошибка уровня ответа: {response['error']}"], [])
    else:
        expected = criteria_check.expected_definitions(_py_files(root, "tests"), charter.code, tests)
        lock = root / "uv.lock"
        problems = criteria_check.validate_response(
            request, response, expected=expected,
            function_lines=_function_lines(root, list(response.get("product_roots", []))),
            lock_sha=hashlib.sha256(lock.read_bytes()).hexdigest() if lock.exists() else "",
            content_sha=_content_sha(root, [*response.get("product_roots", []), "tests"]),
        )
        if problems:
            print("criteria-close: ответ spec-runner отвергнут:\n  " + "\n  ".join(problems))
            return 2
        beh_status = {b["id"].split(":", 1)[1]: b["status"] for b in response["beh"]}
        result = criteria_check.outcome(graph, beh_status)
    _record_measured(state.run_id, key, result.closure)
    text = render_closure(result, ws_id=state.ws_id, code=charter.code, bundle_pin=bundle_pin,
                          product_sha=product_sha,
                          response_sha=hashlib.sha256(out.encode()).hexdigest(),
                          spec_runner_version=installed, host=host)
    return _publish(state, ops, text, result.closure)


def main(argv: list[str] | None = None) -> int:
    """CLI `make criteria-close ARGS='--run <id> [--product-sha <sha>]'`."""
    parser = argparse.ArgumentParser(prog="criteria-close")
    parser.add_argument("--run", required=True)
    parser.add_argument("--product-sha")
    args = parser.parse_args(argv)
    return run(args.run, RealOps(), product_sha=args.product_sha)


if __name__ == "__main__":
    sys.exit(main())
