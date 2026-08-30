#!/usr/bin/env python3
"""Run one issue analysis/implementation with a structured Codex result.

Policy-гейт: decision (accept/reject) детерминирован инициатором и вычислен
кодом до вызова Codex; модель может поднять needs_human, но не перевернуть
политику. Publish-фазы (commit/push/PR/merge) намеренно отсутствуют.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "kind", "summary", "todo", "next_step", "changed_files"],
    "properties": {
        "decision": {"enum": ["accept", "reject", "needs_human"]},
        "kind": {"enum": ["document", "research", "code", "fix", "unknown"]},
        "summary": {"type": "string"},
        "todo": {"type": "string"},
        "next_step": {"type": "string"},
        "changed_files": {"type": "array", "items": {"type": "string"}},
    },
}


def policy_decision(internal: bool) -> str:
    """Детерминированная политика: внутренний → accept, внешний → reject."""
    return "accept" if internal else "reject"


def enforce_policy(result: dict, decision: str) -> dict:
    """LLM не может перевернуть политику; needs_human — единственное исключение."""
    if result.get("decision") != "needs_human":
        result["decision"] = decision
    return result


def result_path(output_root: Path, repo: str, number: int) -> Path:
    return output_root / "issues" / repo / str(number) / "result.json"


def effective_execute(mode: str, internal: bool) -> bool:
    """External requests are never allowed to cross the read-only boundary."""
    return mode == "execute" and internal


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True)
    p.add_argument("--owner", required=True,
                   help="owner репо issue — gh резолвит по slug, не по cwd")
    p.add_argument("--number", required=True, type=int)
    p.add_argument("--author", required=True)
    p.add_argument("--kind", required=True)
    p.add_argument("--mode", choices=("plan", "execute"), default="plan")
    p.add_argument("--internal", choices=("yes", "no"), required=True)
    p.add_argument("--output-root", required=True, type=Path,
                   help="абсолютный devtools/out (результат не в целевом репо)")
    p.add_argument("--url", default="")
    args = p.parse_args()
    issue = subprocess.run(
        ["gh", "issue", "view", str(args.number),
         "--repo", f"{args.owner}/{args.repo}",
         "--json", "title,body,author,labels,url"], capture_output=True, text=True,
    )
    if issue.returncode:
        print(issue.stderr.strip())
        return 2
    payload = json.loads(issue.stdout)
    internal = args.internal == "yes"
    execute = effective_execute(args.mode, internal)
    decision = policy_decision(internal)
    instructions = (
        "Implement the issue in the current repository. Update TODO.md when "
        "its existing contract calls for it. Run relevant tests. Do not "
        "commit, push, open a PR, or merge."
    ) if execute else (
        "Read and analyze only. Do not edit files or execute mutating "
        "commands."
    )
    prompt = f"""You are the worker for GitHub issue {args.repo}#{args.number}.
The initiator is {args.author}; the deterministic preliminary kind is {args.kind}.
Issue data: {json.dumps(payload, ensure_ascii=False)}

Acceptance policy is decided by code, not by you: decision={decision}
(initiator is {'internal' if internal else 'external'}). Keep that decision in
your structured result; you may return needs_human instead only when the issue
data is too incomplete to analyze.
{instructions}
Return only the structured result required by the supplied JSON schema.
"""
    final = result_path(args.output_root, args.repo, args.number)
    final.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="issue-worker-") as tmp:
        schema = Path(tmp) / "schema.json"
        raw = Path(tmp) / "raw-result.json"
        schema.write_text(json.dumps(SCHEMA))
        cmd = ["codex", "exec", "--ephemeral", "--output-schema", str(schema),
               "--output-last-message", str(raw), "--sandbox",
               "workspace-write" if execute else "read-only", prompt]
        done = subprocess.run(cmd)
        if done.returncode:
            return done.returncode
        try:
            result = json.loads(raw.read_text())
        except (OSError, ValueError) as exc:
            print(f"issue-worker: невалидный результат codex: {exc}")
            return 3
    final.write_text(json.dumps(enforce_policy(result, decision),
                                ensure_ascii=False, indent=2))
    print(f"\nStructured result: {final}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
