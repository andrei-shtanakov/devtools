#!/usr/bin/env python3
"""inbox — READ-ONLY fleet view of open `inbox` issues (ADR-ECO-006).

An `inbox` issue is a cross-repo request addressed to the repo it sits in. It
is **not** a plan item: it becomes one when that repo's owner adds a `TODO.md`
item carrying the issue's `slug:`. Acceptance is therefore *derived* here —
open issue x local TODO.md — and stored nowhere, so nothing can drift.

The GitHub half is one fleet-wide search rather than a per-repo walk. The local
half needs sibling working copies, which is why this lives in devtools and why
the vault fleet report (which has no bodies and no clones) can only *list*
inbox issues, never resolve acceptance.

Exit code is 0 whenever `gh` is missing, unauthenticated, or offline: this runs
inside `make morning`, and a daily ritual that breaks offline stops being run.
Exit 2 is reserved for a missing `plan-fields` package (see the import guard
below); every other path, including all `gh` failures, exits 0.

Runtime: the shared `plan-fields` package needs Python 3.12, so this script runs
under `uv` like `check-plan-fields.py`. The other devtools scripts stay stdlib.

Usage (via uv, so the pinned package resolves):
    make inbox
    uv run --frozen python inbox.py --root .. --owner <github-login>
    uv run --frozen python inbox.py --selftest   # built-in checks, no network
"""

from __future__ import annotations

import argparse
import functools
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    from plan_fields import scrape_items
    from plan_fields.fleet import canonical_name
except ImportError:  # pragma: no cover - exercised by humans, not the suite
    sys.stderr.write(
        "inbox.py needs the 'plan-fields' package (Python >=3.12).\n"
        "Run it through uv so the pinned dependency resolves:\n"
        "    make inbox\n"
        "    uv run --frozen python inbox.py\n"
        "One implementation of the plan-fields contract, shared with\n"
        "check-plan-fields.py (ADR-ECO-005 PF-7) — never a private regex here.\n"
    )
    raise SystemExit(2)

DEFAULT_OWNER = "andrei-shtanakov"
LABEL = "inbox"
# GitHub's search API returns at most 1000 results for any query; `gh` paginates
# up to `--limit`, so this is the ceiling of what can be asked for, not a page
# size. Reaching it means the answer may be incomplete — which we say out loud.
SEARCH_LIMIT = 1000

# A field is a line of its own; a mention inside prose is not a field. Without
# the line anchor, "see slug: x in the docs" would be read as a declaration.
FIELD = "^[ \t]*{name}:[ \t]*(\\S+)[ \t]*$"


def parse_field(body: str, name: str) -> str | None:
    """Read one `name: value` field from an issue body, or None if absent."""
    match = re.search(FIELD.format(name=re.escape(name)), body, re.MULTILINE)
    return match.group(1) if match else None


def discover_repos(root: Path) -> dict[str, Path]:
    """Map lowercased canonical repo name to its TODO.md, for clones present here.

    Discovery is devtools' own concern (the wrapper keeps its `build_inputs` for
    the same reason); only *parsing* comes from the package. Names come from
    `canonical_name`, never from the directory: `maestro` upstream is `Maestro/`
    on this disk, and GitHub reports the canonical spelling.
    """
    found: dict[str, Path] = {}
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / ".git").exists():
            todo = child / "TODO.md"
            if todo.is_file():
                found[canonical_name(child).lower()] = todo
    return found


@functools.lru_cache(maxsize=None)
def _item_texts(todo: Path) -> tuple[str, ...]:
    """Raw text of every checkbox item in `todo`, read and scraped once per run.

    Cached because `render` asks about the same repo once per issue: three
    requests to one repo would otherwise re-read and re-scrape the same file
    three times. This is a short-lived CLI, so a cache that never invalidates
    is correct here — the file cannot change under us mid-run.
    """
    if not todo.is_file():
        return ()
    text = todo.read_text(encoding="utf-8", errors="ignore")
    return tuple(item.raw_text for item in scrape_items(text))


def is_accepted(slug: str, todo: Path) -> bool:
    """True when `slug` appears on a checkbox item of `todo`.

    Items come from the shared scraper, so "what counts as a plan item" is the
    package's answer, not a second one invented here. Prose that merely mentions
    a slug is not an item and does not count.

    The test itself is a substring over the item's raw text, which is weaker
    than it reads: `benchmark-2` also matches an item saying `benchmark-20`.
    Tightening it is the package's call under ADR-ECO-005 D9 — a private
    stricter rule here would be exactly the divergence that ADR removes.
    """
    return any(slug in raw for raw in _item_texts(todo))


def _well_formed(issue: object) -> bool:
    """True when a record carries every field `render` indexes without a guard.

    `body` is deliberately not required: a request with no body is malformed as
    a *request* (no `slug:`), which `render` already reports as
    `МАЛФОРМИРОВАН`. That is a different failure from a record this tool cannot
    even print, and collapsing the two would hide real requests.
    """
    if not isinstance(issue, dict):
        return False
    repo = issue.get("repository")
    return (
        isinstance(issue.get("number"), int)
        and isinstance(issue.get("title"), str)
        and isinstance(repo, dict)
        and isinstance(repo.get("name"), str)
    )


def parse_search_output(stdout: str) -> list[dict] | None:
    """Validate gh's JSON into records `render` can trust, or None if unusable.

    Three distinct failure shapes, each reported rather than crashed on:
    unparseable JSON, a top-level value that is not a list, and records missing
    the fields `render` indexes. A malformed record is dropped **loudly** —
    dropping it silently would understate the inbox, and letting it through
    would raise `KeyError` inside `make morning`, which must never fail.
    """
    try:
        data = json.loads(stdout or "[]")
    except json.JSONDecodeError:
        print("inbox: gh returned unparseable JSON; skipping", file=sys.stderr)
        return None
    if not isinstance(data, list):
        print(
            f"inbox: gh returned {type(data).__name__}, expected a list; skipping",
            file=sys.stderr,
        )
        return None
    good = [issue for issue in data if _well_formed(issue)]
    dropped = len(data) - len(good)
    if dropped:
        print(
            f"inbox: dropped {dropped} malformed record(s) from gh output",
            file=sys.stderr,
        )
    # Say so rather than quietly showing a truncated fleet: a capped list that
    # looks complete is worse than a smaller list that admits it is capped.
    if len(data) >= SEARCH_LIMIT:
        print(
            f"inbox: hit GitHub's {SEARCH_LIMIT}-result search ceiling; "
            "the list may be truncated",
            file=sys.stderr,
        )
    return good


def search_inbox(owner: str) -> list[dict] | None:
    """Every open `inbox` issue across the owner's repos, or None if gh cannot.

    One search, not a per-repo walk. `body` is required, not decorative: without
    it there is no `slug:` and acceptance cannot be derived, which would reduce
    the morning ritual to a list of requests with no indication which still need
    a decision.

    `gh` paginates internally up to `--limit`, so asking for the API's own
    ceiling costs nothing when the fleet has three requests and stops the tool
    from inventing a lower cap of its own.
    """
    cmd = [
        "gh", "search", "issues",
        "--owner", owner,
        "--label", LABEL,
        "--state", "open",
        "--limit", str(SEARCH_LIMIT),
        "--json", "repository,number,title,body",
    ]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"inbox: gh unavailable ({exc}); skipping", file=sys.stderr)
        return None
    if done.returncode != 0:
        detail = done.stderr.strip().splitlines()
        print(
            f"inbox: gh failed ({detail[-1] if detail else '?'}); skipping",
            file=sys.stderr,
        )
        return None
    return parse_search_output(done.stdout)


def render(issues: list[dict], repos: dict[str, Path]) -> tuple[list[str], int]:
    """Format issues as lines, and count the ones still awaiting a decision.

    `repos` maps lowercased canonical repo name to its TODO.md, as
    `discover_repos` returns it. GitHub reports the canonical name while the
    directory on disk may differ, so lookup goes through that map and never
    through `repository == dirname`.

    Records are assumed well-formed: `parse_search_output` has already dropped
    anything missing the indexed fields. Guarding again here would split one
    contract across two places and make it unclear which owns it.
    """
    lines: list[str] = []
    pending = 0
    for issue in issues:
        repo = issue["repository"]["name"]
        ref = f"{repo}#{issue['number']}"
        slug = parse_field(issue.get("body") or "", "slug")
        sender = parse_field(issue.get("body") or "", "from") or "?"
        todo = repos.get(repo.lower())
        if slug is None:
            state = "МАЛФОРМИРОВАН — нет slug:"
        elif todo is None:
            state = "не проверить — репо не склонирован здесь"
        elif is_accepted(slug, todo):
            state = "принят"
        else:
            state = "НЕ ПРИНЯТ"
            pending += 1
        lines.append(f"  {ref:<28} {state:<34} от {sender}  — {issue['title']}")
    return lines, pending


def _selftest() -> int:
    """Exercise body parsing and acceptance derivation, without GitHub."""
    import contextlib
    import io
    import tempfile

    # `parse_search_output` is the boundary where untrusted gh output becomes
    # records the rest of the script indexes without guards. Every failure
    # shape must yield a reason and a usable value, never an exception —
    # `make morning` must not break on a bad response.
    ok = '[{"repository":{"name":"r"},"number":1,"title":"t","body":"slug: s"}]'
    assert len(parse_search_output(ok) or []) == 1, "valid record rejected"
    assert parse_search_output("") == [], "empty stdout must be an empty list"

    noise = io.StringIO()
    with contextlib.redirect_stderr(noise):
        assert parse_search_output("not json at all") is None, "bad JSON not caught"
        assert parse_search_output('{"a": 1}') is None, "non-list top level accepted"
        # Missing `number`, and a non-dict — both unprintable, both dropped.
        partial = '[{"repository":{"name":"r"},"title":"t"}, "junk", ' + ok[1:-1] + "]"
        kept = parse_search_output(partial)
        assert kept is not None and len(kept) == 1, "malformed records not dropped"
    assert "unparseable" in noise.getvalue(), "bad JSON dropped silently"
    assert "expected a list" in noise.getvalue(), "wrong top level dropped silently"
    assert "malformed" in noise.getvalue(), "malformed records dropped silently"

    # A body-less record is malformed as a *request*, not as a record: it must
    # survive parsing so render can report it, rather than vanish.
    no_body = '[{"repository":{"name":"r"},"number":2,"title":"t"}]'
    assert len(parse_search_output(no_body) or []) == 1, "body-less record dropped"

    body = "slug: benchmark-2\nfrom: arbiter#crossover-gate\n\nProse here.\n"
    assert parse_field(body, "slug") == "benchmark-2", "slug not parsed"
    assert parse_field(body, "from") == "arbiter#crossover-gate", "from not parsed"
    assert parse_field("no fields here", "slug") is None, "absent field must be None"
    # A mention inside prose is not a field: the field is a line of its own.
    assert (
        parse_field("see slug: x in the docs", "slug") is None
    ), "inline mention read as field"

    with tempfile.TemporaryDirectory() as tmp:
        todo = Path(tmp) / "TODO.md"
        todo.write_text(
            "# TODO\n\n- [ ] Run the second task_type sweep benchmark-2 @owner:andrei\n"
            "- [x] Something else\n",
            encoding="utf-8",
        )
        assert is_accepted("benchmark-2", todo), "present slug read as not accepted"
        assert not is_accepted("benchmark-3", todo), "absent slug read as accepted"
        # Documented limitation (ADR-ECO-006): the test is
        # a substring over the checkbox line, so a longer slug containing this one
        # matches. Asserted so the weakness is visible rather than discovered later.
        assert is_accepted("benchmark", todo), "substring behaviour changed silently"

        prose_only = Path(tmp) / "PROSE.md"
        prose_only.write_text(
            "benchmark-2 is discussed here but not tracked\n", encoding="utf-8"
        )
        assert not is_accepted("benchmark-2", prose_only), "non-checkbox line counted"

    # render() holds the repo-key matching and the state derivation — the
    # riskiest logic here — and `--selftest` is the only regression net in a
    # repo with no CI. Exercise every state, not just the happy one.
    with tempfile.TemporaryDirectory() as tmp:
        todo = Path(tmp) / "TODO.md"
        todo.write_text(
            "- [ ] Second task_type sweep benchmark-2 @owner:andrei\n",
            encoding="utf-8",
        )
        repos = {"atp-platform": todo}

        def issue(number: int, body: str, repo: str = "atp-platform") -> dict:
            return {
                "repository": {"name": repo},
                "number": number,
                "title": "t",
                "body": body,
            }

        lines, pending = render([], repos)
        assert lines == [] and pending == 0, "empty inbox must render nothing"

        body = "slug: benchmark-2\nfrom: arbiter#crossover-gate\n"
        lines, pending = render([issue(1, body)], repos)
        assert pending == 0, "accepted issue must not count as pending"
        assert "принят" in lines[0], "present slug must read as accepted"

        lines, pending = render([issue(2, "slug: nope\nfrom: arbiter#x\n")], repos)
        assert pending == 1, "not-accepted issue must count as pending"
        assert "НЕ ПРИНЯТ" in lines[0], "absent slug must read as not accepted"

        lines, pending = render([issue(3, "no fields at all\n")], repos)
        assert pending == 0, "malformed issue must not count as pending"
        assert "МАЛФОРМИРОВАН" in lines[0], "missing slug must be flagged"

        lines, pending = render([issue(4, "slug: x\n", repo="not-cloned")], repos)
        assert pending == 0, "unresolvable repo must not count as pending"
        assert "не проверить" in lines[0], "uncloned repo must degrade visibly"

    print("selftest OK")
    return 0


def main() -> int:
    """Print the fleet's open inbox issues. Never fails on a missing gh."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=None, help="workspace root")
    parser.add_argument("--owner", default=DEFAULT_OWNER, help="GitHub owner to search")
    parser.add_argument(
        "--selftest", action="store_true", help="run built-in checks and exit"
    )
    args = parser.parse_args()
    if args.selftest:
        return _selftest()

    default_root = Path(__file__).resolve().parent.parent
    root = Path(args.root).resolve() if args.root else default_root
    issues = search_inbox(args.owner)
    if issues is None:
        return 0
    if not issues:
        print("Входящие (inbox): пусто.")
        return 0

    repos = discover_repos(root)
    lines, pending = render(issues, repos)
    print(f"Входящие (inbox): {len(issues)}, из них не принято {pending}")
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
