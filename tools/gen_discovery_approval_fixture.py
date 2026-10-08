"""Dev-only: подписать фикстуру T40 настоящей командой `discovery approve`.

Запуск — из чекаута discovery на пиненом коммите (`PINNED.txt` вендоренной копии):

    cd ../discovery
    uv run --frozen python -I <devtools>/tools/gen_discovery_approval_fixture.py \\
        <devtools>/tests/fixtures/discovery_approval <pinned-sha>

`approve` исполняется целиком (`discovery.cli.main`), подменена только точка
композиции форджа `cli.build_forge` — так же, как в тестах самого discovery:
факты мержа и политика заданы ниже явно. Черновик берётся из
`<fixtures>/draft-brief.md` (вендорен в devtools вместе с фикстурой).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from discovery.forge import PullRequest

from discovery import approval, cli, policy

TARGET_REPO = "owner/alpha"
BRIEF_PATH = "workstreams/WS-1/spec/00-discovery/brief.md"
HUMAN = "andrei-shtanakov"
MERGED_AT = "2026-10-08T10:00:00Z"
MERGE_SHA = "d1" * 20
POLICY_SHA = "a1" * 20
POLICY_REPO = "andrei-shtanakov/approval-policy"


@dataclass
class _Forge:
    """Факты форджа для одного акта; `calls` — что спросил `approve`."""

    draft: str
    calls: list[tuple] = field(default_factory=list)

    def pull_request(self, repo: str, number: int) -> PullRequest:
        self.calls.append(("pull_request", repo, number))
        return PullRequest("MERGED", HUMAN, MERGED_AT, MERGE_SHA)

    def pull_request_files(self, repo: str, number: int) -> list[str]:
        self.calls.append(("pull_request_files", repo, number))
        return [BRIEF_PATH]

    def file_at(self, repo: str, commit: str, path: str) -> str | None:
        self.calls.append(("file_at", repo, commit, path))
        if repo == POLICY_REPO:
            return f"{policy.ALLOWLIST_KEY}={HUMAN}\n"
        return self.draft

    def latest_commit_touching(self, repo: str, ref: str, path: str) -> str | None:
        self.calls.append(("latest_commit_touching", repo, ref, path))
        return POLICY_SHA


def main() -> int:
    fixtures, pinned = Path(sys.argv[1]), sys.argv[2]
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if head != pinned:
        print(f"discovery HEAD {head} ≠ пина {pinned}", file=sys.stderr)
        return 2
    os.environ.pop(policy.ALLOWLIST_KEY, None)
    draft = (fixtures / "draft-brief.md").read_text(encoding="utf-8")
    forge = _Forge(draft)
    cli.build_forge = lambda: forge  # type: ignore[assignment]
    with tempfile.TemporaryDirectory() as tmp:
        brief = Path(tmp) / BRIEF_PATH
        brief.parent.mkdir(parents=True)
        brief.write_text(draft, encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(
                [
                    "approve",
                    str(brief),
                    "--repo",
                    TARGET_REPO,
                    "--pr",
                    "7",
                    "--path",
                    BRIEF_PATH,
                ]
            )
        if code != 0:
            print(f"approve вернул {code}: {out.getvalue()}", file=sys.stderr)
            return 2
        signed = brief.read_text(encoding="utf-8")
    (fixtures / "signed-brief.md").write_text(signed, encoding="utf-8")
    meta = {
        "discovery_commit": pinned,
        "approve_exit": code,
        "self_hash": approval.self_hash(draft),
        "verify_signed": approval.verify(signed),
        "verify_draft": approval.verify(draft),
        "merge_event": {"login": HUMAN, "merged_at": MERGED_AT, "commit": MERGE_SHA},
        "forge_calls": [list(c) for c in forge.calls],
    }
    (fixtures / "signed-brief.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
