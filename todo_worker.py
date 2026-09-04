#!/usr/bin/env python3
"""todo-worker — run ONE `TODO.md` item, with the context pack as the gate.

`issue_worker.py` runs one GitHub issue and computes its policy in code before
the model is called: the model may raise `needs_human`, never flip the decision.
This is its sibling on the plan plane, and the shape is deliberately the same.
What differs is where the authority comes from. There the operator types
`--internal` and sees what they typed; here the gate is `execute_allowed` from
`todo_context`, derived from context the operator did not assemble — a written
requirement in the item's body, a design doc, or the originating issue.

**Why refusing beats downgrading.** `issue_worker.effective_execute` silently
turns `execute` into read-only for an external request, and that is right there:
the operator declared `--internal` themselves. Here the same silence would hide
the gate this whole tool exists to enforce, so `--mode execute` on an item that
has no written requirement FAILS, with the reason and the unread sources printed
(exit 4; 2 and 3 keep issue_worker's meanings).

Publish phases are absent by construction, exactly as in `issue_worker`: the
harness may edit the checkout and run tests, never commit, push, open a PR or
merge. A `tasks.md` spec for spec-runner is not this axis at all — it is what
`plan` produces and `skills/spec-bridge` delivers (invariant 4).

The harness is `codex`, hardcoded, like `issue_worker`. `AUTHOR_HARNESS` and the
review shim resolve a harness already, but neither covers what a worker needs:
`governance/ops.py` builds claude write-only and without a schema, and
`scripts/harness/claude-review` requires `--sandbox read-only`. A worker needs
both sandboxes WITH structured output, and that layer belongs in one shared
place, not in a third private copy — `TODO.md @id:worker-harness-layer`.

Usage (needs the pinned env, like `todo_context.py` — `uv run --frozen`):

    python todo_worker.py --repo devtools --id todo-context-pack
    python todo_worker.py --uri todo://devtools/todo-context-pack --mode execute
    python todo_worker.py --repo maestro --id foo --issues   # asks gh for the issue
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import todo_context as tc

#: The result the harness must return. `todo_line_update` is what the run
#: proposes appending to the item's line — the plan is edited by a person or by
#: a later step, never silently from here.
SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["outcome", "summary", "next_step", "changed_files",
                 "todo_line_update"],
    "properties": {
        "outcome": {"enum": ["done", "blocked", "needs_human"]},
        "summary": {"type": "string"},
        "next_step": {"type": "string"},
        "changed_files": {"type": "array", "items": {"type": "string"}},
        "todo_line_update": {"type": "string"},
    },
}

#: ADR-ECO-005 PF-2B, the same grammar `plan_fields` publishes. `parse_uri`
#: accepts `(.+)`, so an id reaching `result_path` is untrusted input: `..` or a
#: slash would write the result outside `out/`.
_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")

_NO_PUBLISH = ("Do not commit, push, open a PR, or merge — none of those phases "
               "exist here.")

_GIT_TIMEOUT = 20

#: Dirty paths printed before the list is cut. The cut is always named.
_DIRTY_SHOWN = 10


class WorkerError(Exception):
    """Bad input or unusable state — exit 2, as in `issue_worker`."""


class HarnessError(WorkerError):
    """The harness did not run, or did not return a usable result — exit 3."""


def require_id(value: Any) -> str:
    """The id (or repo) as a single safe path segment, or refuse.

    The type is checked, not assumed: `--pack` is operator-supplied JSON, so a
    numeric `item.id` is ordinary input and `fullmatch` would raise `TypeError`
    past every guard in this file (Copilot, PR #126).
    """
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise WorkerError(
            f"недопустимый идентификатор {value!r}: грамматика "
            f"[a-z0-9][a-z0-9._-]{{0,63}} (ADR-ECO-005 PF-2B)")
    return value


def require_pack(pack: Any) -> dict[str, Any]:
    """A pack shaped enough to gate and to render, or refuse.

    `--pack` takes a file the operator names, so a truncated or hand-edited one
    is ordinary input, not an impossibility. Letting it surface as a `KeyError`
    from inside `render` would be a traceback where an answer belongs.
    """
    if not isinstance(pack, dict) or not isinstance(pack.get("item"), dict):
        raise WorkerError("pack не похож на context-pack: нет объекта `item`")
    missing = [key for key in ("node_id", "completeness") if key not in pack]
    missing += [f"item.{k}" for k in ("repo", "id") if k not in pack["item"]]
    if missing:
        raise WorkerError(
            f"pack неполон, нет полей: {', '.join(missing)} — соберите его "
            f"`todo_context.py --json`, а не вручную")
    # Presence was not enough: a hand-edited pack can carry the right keys with
    # the wrong types, and the traceback just moved one line down (ревью #126).
    wrong = []
    if not isinstance(pack["completeness"], dict):
        wrong.append("completeness (ожидался объект)")
    if pack.get("checkout") is not None and not isinstance(pack["checkout"], str):
        wrong.append("checkout (ожидалась строка)")
    rules = pack.get("rules", [])
    if not isinstance(rules, list):
        wrong.append("rules (ожидался список)")
    elif not all(isinstance(rule, dict) for rule in rules):
        # the list was checked while its elements were not, so a hand-edited
        # `rules: ["CLAUDE.md"]` reached `render_rules` and raised AttributeError
        # outside every handler here (Copilot, PR #127)
        wrong.append("rules[] (ожидались объекты правил, не строки)")
    if wrong:
        raise WorkerError(f"pack с неверными типами: {', '.join(wrong)}")
    return pack


def require_checkout(pack: dict[str, Any]) -> Path:
    """The target repo's checkout on this host — where the run must happen.

    `subprocess.run` inherits the caller's cwd, which is devtools: without this
    an `execute` run for a neighbour's item would edit devtools' own tree under
    `workspace-write`, and the gate would have authorised changing one repo while
    another moved. `issue_worker` never had to ask because `issue_console` starts
    it with `tmux -c <repo_path>`; this one is called directly (ревью PR #126).
    """
    raw = pack.get("checkout")
    if not raw:
        raise WorkerError(
            "в паке нет пути к чекауту целевого репо — запускать харнесс негде. "
            "Пересоберите пак `todo_context.py --json` (поле `checkout`)")
    directory = Path(raw)
    if not (directory / ".git").exists():
        raise WorkerError(f"чекаут {directory} не похож на git-репо — не запускаю")
    return directory


def result_path(output_root: Path, repo: str, item_id: str) -> Path:
    """Where the run's structured result lands — never inside the target repo."""
    return output_root / "todo" / repo / item_id / "result.json"


def effective_execute(mode: str, pack: dict[str, Any]) -> bool:
    """`execute` needs the operator's mode AND the pack's own verdict.

    The pack's flag is not re-derived here: `todo_context.grade` owns what counts
    as a written requirement, and a second opinion in this file would be the
    divergence its own history already paid for.
    """
    completeness = pack.get("completeness") or {}
    return mode == "execute" and bool(completeness.get("execute_allowed"))


def refusal(pack: dict[str, Any]) -> str:
    """Why `execute` was refused, in the pack's own words."""
    completeness = pack.get("completeness") or {}
    lines = [
        f"todo-worker: execute запрещён — grade `{completeness.get('grade')}`: "
        f"{completeness.get('reason')}",
        "Требование для этого пункта не написано: ни тела под пунктом, ни "
        "design-дока, ни исходного issue. Запуск с записью на такой основе — "
        "импровизация, поэтому это отказ, а не тихий откат в plan.",
    ]
    unknown = completeness.get("unknown_sources") or []
    if unknown:
        lines.append(
            f"Источники, которые НЕ прочитаны: {', '.join(unknown)} — их пустота "
            f"не доказывает отсутствие требования. `--issues` спросит gh.")
    lines.append("Запустите `--mode plan`, чтобы прочитать и предложить.")
    return "\n".join(lines)


def enforce_mode(result: dict[str, Any], execute: bool) -> dict[str, Any]:
    """A `plan` run cannot have changed files: the sandbox was read-only.

    The mirror of `issue_worker.enforce_policy` — there the model cannot flip the
    acceptance decision, here it cannot report work the sandbox forbade. A claim
    of edits in plan mode is not a small inaccuracy: it is the one signal the
    operator reads to know whether the repo moved, so it becomes `needs_human`.
    """
    if execute or not result.get("changed_files"):
        return result
    result["changed_files"] = []
    result["outcome"] = "needs_human"
    result["summary"] = (
        "[todo-worker] отчёт заявлял правки файлов в режиме plan, где харнесс "
        "работал read-only — заявка снята. " + str(result.get("summary") or ""))
    return result


def render_rules(rules: list[dict[str, Any]]) -> str:
    """The repo's scope fence, inlined — or nothing, said plainly.

    `todo_context.render` keeps this text in `--json` on purpose: a fence is
    thousands of bytes and would bury the item in a human report. A prompt is not
    a report, and an `execute` run edits someone else's tree, so the fence goes
    in here — the prompt used to CLAIM it was included while it was not
    (ревью PR #126, круг 2).
    """
    if not rules:
        return ""
    blocks = []
    for rule in rules:
        cut = " (обрезан)" if rule.get("truncated") else ""
        blocks.append(f"--- {rule.get('path')}{cut} ---\n{rule.get('text') or ''}")
    return ("\n\nThe rules of the repository you are working in — its scope "
            "fence, obey it:\n\n" + "\n\n".join(blocks))


def build_prompt(rendered_pack: str, execute: bool,
                 rules: list[dict[str, Any]] | None = None) -> str:
    """The pack as the whole context, plus what this mode may do with it."""
    instructions = (
        f"Implement the item in the current repository. Run the relevant tests. "
        f"Update `TODO.md` only where its existing contract calls for it. "
        f"{_NO_PUBLISH}"
    ) if execute else (
        "Read and analyze only. Do not edit files or execute mutating commands. "
        "Propose what should be done and what stands in the way."
    )
    return f"""You are the worker for one plan item of this fleet.

Everything known about it is below — the item line, its body, the epic's goal,
the `@blocked_by` graph, design docs, and, when it was asked for, the
originating issue. Sources that were NOT read are named at the bottom under
Completeness: treat them as unknown, never as empty.

{rendered_pack}{render_rules(rules or [])}

{instructions}
Return only the structured result required by the supplied JSON schema.
"""


def load_pack(args: argparse.Namespace) -> dict[str, Any]:
    """The context pack: read from a file, or assembled by `todo_context`."""
    if args.pack:
        try:
            return json.loads(Path(args.pack).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise WorkerError(f"pack нечитаем: {exc}") from exc
    if args.uri:
        repo, item_id = tc.parse_uri(args.uri)
    elif args.repo and args.item_id:
        repo, item_id = args.repo, args.item_id
    else:
        raise WorkerError("нужен --uri, либо --repo и --id, либо --pack")
    root = tc.find_root(args.root)
    return tc.build_pack(
        root,
        Path(args.manifest) if args.manifest else tc.default_manifest(root),
        Path(args.registry) if args.registry else tc.default_registry(root),
        repo, item_id, owner=args.issues,
    )


def require_clean_tree(checkout: Path) -> None:
    """Refuse to run `execute` over uncommitted work in the target repo.

    The worker's edits and the operator's would become indistinguishable, and
    `changed_files` — the one signal saying what the run touched — stops being
    checkable. `accept-pr` paid this lesson already («грязное дерево — ложная
    фактура находок», ретроспектива 2026-09-02).
    """
    try:
        done = subprocess.run(["git", "-C", str(checkout), "status", "--porcelain"],
                              capture_output=True, text=True, timeout=_GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorkerError(f"не удалось проверить дерево {checkout}: {exc}") from exc
    if done.returncode:
        raise WorkerError(
            f"git status в {checkout} не отработал: "
            f"{(done.stderr.strip().splitlines() or ['?'])[-1]}")
    dirty = done.stdout.strip()
    if dirty:
        lines = dirty.splitlines()
        listed = "\n".join(f"  {line}" for line in lines[:_DIRTY_SHOWN])
        # naming the cut is the same rule `todo_context` keeps for its own
        # capped output: a silent cut reads as a complete list (ревью PR #127)
        rest = (f"\n  … ещё {len(lines) - _DIRTY_SHOWN} файлов"
                if len(lines) > _DIRTY_SHOWN else "")
        raise WorkerError(
            f"дерево {checkout} грязное — правки прогона было бы не отличить от "
            f"ваших, а `changed_files` стал бы непроверяемым:\n{listed}{rest}")


def run_harness(prompt: str, execute: bool, cwd: Path) -> dict[str, Any]:
    """One `codex exec` with a schema, in the sandbox the mode earns — and in the
    target repo's checkout, never in the caller's."""
    with tempfile.TemporaryDirectory(prefix="todo-worker-") as tmp:
        schema = Path(tmp) / "schema.json"
        raw = Path(tmp) / "raw-result.json"
        schema.write_text(json.dumps(SCHEMA))
        cmd = ["codex", "exec", "--ephemeral", "--output-schema", str(schema),
               "--output-last-message", str(raw), "--sandbox",
               "workspace-write" if execute else "read-only", prompt]
        try:
            done = subprocess.run(cmd, cwd=cwd)
        except (OSError, subprocess.SubprocessError) as exc:
            raise HarnessError(f"не удалось запустить codex: {exc}") from exc
        if done.returncode:
            raise HarnessError(f"codex завершился с кодом {done.returncode}")
        try:
            result = json.loads(raw.read_text())
        except (OSError, ValueError) as exc:
            raise HarnessError(f"невалидный результат codex: {exc}") from exc
        if not isinstance(result, dict):
            raise HarnessError(
                f"результат codex не объект, а {type(result).__name__} — "
                f"структурированный ответ не получен")
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--uri", help="todo://<repo>/<id>")
    parser.add_argument("--repo")
    parser.add_argument("--id", dest="item_id")
    parser.add_argument("--pack", help="готовый JSON-пак вместо сборки")
    parser.add_argument("--mode", choices=("plan", "execute"), default="plan")
    parser.add_argument("--output-root", type=Path,
                        default=Path(__file__).resolve().parent / "out",
                        help="куда писать результат; не целевой репо")
    parser.add_argument("--root", default=None, help="workspace root")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--registry", default=None, help="epics.toml")
    parser.add_argument("--issues", nargs="?", const="", default=None,
                        metavar="OWNER",
                        help="спросить gh об исходном inbox-issue "
                             "(owner по умолчанию — inbox.DEFAULT_OWNER)")
    parser.add_argument("--dry-run", action="store_true",
                        help="показать промпт и решение гейта, не звать харнесс")
    args = parser.parse_args(argv)

    try:
        pack = require_pack(load_pack(args))
        item = pack["item"]
        repo, item_id = require_id(item["repo"]), require_id(item["id"])
        execute = effective_execute(args.mode, pack)
        if args.mode == "execute" and not execute:
            print(refusal(pack), file=sys.stderr)
            return 4
        try:
            rendered = tc.render(pack)
        except (KeyError, TypeError) as exc:
            raise WorkerError(f"pack не рендерится, нет поля {exc}") from exc
        prompt = build_prompt(rendered, execute, pack.get("rules"))
        checkout = require_checkout(pack)
        if execute:
            require_clean_tree(checkout)
        if args.dry_run:
            print(f"=== dry-run: режим {args.mode}, sandbox "
                  f"{'workspace-write' if execute else 'read-only'}, "
                  f"cwd {checkout} ===")
            print(prompt)
            return 0
        result = enforce_mode(run_harness(prompt, execute, checkout), execute)
    except HarnessError as exc:
        print(f"todo-worker: {exc}", file=sys.stderr)
        return 3
    except (WorkerError, tc.ContextError) as exc:
        print(f"todo-worker: {exc}", file=sys.stderr)
        return 2

    final = result_path(args.output_root, repo, item_id)
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    print(f"\nStructured result: {final}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
