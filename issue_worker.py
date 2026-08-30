#!/usr/bin/env python3
"""Run one issue analysis/implementation with a structured Codex result."""

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


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True)
    p.add_argument("--number", required=True, type=int)
    p.add_argument("--author", required=True)
    p.add_argument("--kind", required=True)
    p.add_argument("--mode", choices=("plan", "execute"), default="plan")
    p.add_argument("--internal", choices=("yes", "no"), required=True)
    p.add_argument("--url", default="")
    args = p.parse_args()
    issue = subprocess.run(
        ["gh", "issue", "view", str(args.number),
         "--json", "title,body,author,labels,url"], capture_output=True, text=True,
    )
    if issue.returncode:
        print(issue.stderr.strip())
        return 2
    payload = json.loads(issue.stdout)
    # External requests are never allowed to cross the read-only boundary.
    execute = args.mode == "execute" and args.internal == "yes"
    prompt = f"""You are the worker for GitHub issue {args.repo}#{args.number}.
The initiator is {args.author}; the deterministic preliminary kind is {args.kind}.
Issue data: {json.dumps(payload, ensure_ascii=False)}

First validate acceptance and kind. Initiator policy says internal={args.internal}.
{'Implement the issue in the current repository. Update TODO.md when its existing contract calls for it. Run relevant tests. Do not commit, push, open a PR, or merge.' if execute else 'Read and analyze only. Do not edit files or execute mutating commands.'}
Return only the structured result required by the supplied JSON schema. For an external initiator,
use needs_human unless repository policy explicitly establishes authority to accept it.
"""
    with tempfile.TemporaryDirectory(prefix="issue-worker-") as tmp:
        schema = Path(tmp) / "schema.json"
        result = Path.cwd() / f".issue-{args.number}-result.json"
        schema.write_text(json.dumps(SCHEMA))
        cmd = ["codex", "exec", "--ephemeral", "--output-schema", str(schema),
               "--output-last-message", str(result), "--sandbox",
               "workspace-write" if execute else "read-only", prompt]
        done = subprocess.run(cmd)
    if done.returncode:
        return done.returncode
    print(f"\nStructured result: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
