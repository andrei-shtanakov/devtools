"""CLI conductor: status | why | plan | run | record (спека §7.2, срез 0)."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from conductor.collect import collect, read_manifest
from conductor.inputs import Inputs, RepoTodo, load_inputs, save_inputs
from conductor.render import render_plan, render_status, render_why
from conductor.roadmap import parse_roadmap
from conductor.snapshot import evaluate, to_snapshot
from conductor.sources_gh import run_gh

EXIT_OK, EXIT_ARGS, EXIT_NO_SOURCE, EXIT_CONFIG = 0, 2, 3, 4
COMMANDS = ("status", "why", "plan", "run", "record")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="conductor", exit_on_error=False)
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--root", type=Path, default=Path(".."))
    p.add_argument("--manifest", type=Path)
    p.add_argument("--roadmap", type=Path)
    p.add_argument("--replay", type=Path)
    p.add_argument("--no-fetch", action="store_true")
    p.add_argument("--out", type=Path, default=Path("out/conductor"))
    p.add_argument("--level", type=int, choices=range(4), default=0)
    p.add_argument("command", nargs="?")
    p.add_argument("target", nargs="?")
    return p


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _inputs(args: argparse.Namespace) -> Inputs | None:
    if args.replay is not None:
        return load_inputs(args.replay)
    errors: list[str] = []
    if args.manifest is not None:
        if not args.manifest.is_file():
            print(f"нет манифеста {args.manifest}", file=sys.stderr)
            return None
        text: str | None = args.manifest.read_text(encoding="utf-8")
        origin: tuple[str, str | None] = (f"file:{args.manifest}", None)
    else:
        text, origin, errors = read_manifest(args.root, not args.no_fetch)
    if text is None:
        print("; ".join(errors), file=sys.stderr)
        return None
    return collect(
        args.root,
        text,
        origin,
        args.roadmap,
        not args.no_fetch,
        run_gh,
        socket.gethostname(),
        _now(),
        errors,
    )


def _previous(out: Path) -> dict[str, Any] | None:
    runs = (
        sorted(d for d in out.glob("*") if (d / "snapshot.json").is_file())
        if out.is_dir()
        else []
    )
    if not runs:
        return None
    return json.loads((runs[-1] / "snapshot.json").read_text(encoding="utf-8"))


def _run(args: argparse.Namespace, inputs: Inputs) -> int:
    result = evaluate(inputs, 0)
    run_id = inputs.captured_at.replace(":", "")
    snap = to_snapshot(result, inputs, run_id, _previous(args.out))
    run_dir = args.out / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "snapshot.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    save_inputs(inputs, run_dir / "inputs.json")
    print(render_status(result))
    return EXIT_OK if result.roadmap.valid else EXIT_CONFIG


def _selftest() -> int:
    manifest = '[cores.a]\nrepo_url = "git@github.com:own/a.git"\ngit_dir = "a"\n'
    inp = Inputs(
        captured_at="2026-09-29T00:00:00Z",
        host="selftest",
        owner="own",
        manifest_text=manifest,
        todos=[RepoTodo("a", "- [ ] x @owner:TBD @id:x\n", "s", "read")],
        gh_records=[],
        gh_state="read",
        gh_detail="",
        roadmap_text=None,
        roadmap_state="absent",
        roadmap_source="selftest",
        roadmap_sha=None,
        epics={},
        epics_state="read",
        epics_detail="",
    )
    ok = [e.node_id for e in evaluate(inp, 0).queue] == ["todo://a/x"]
    print("selftest:", "ok" if ok else "FAIL")
    return EXIT_OK if ok else 1


def main(argv: list[str] | None = None) -> int:
    """Точка входа; коды выхода — §7.2 (rev 10)."""
    try:
        args = _parser().parse_args(argv)
    except (argparse.ArgumentError, SystemExit):
        return EXIT_ARGS
    if args.selftest:
        return _selftest()
    if args.command not in COMMANDS or (
        args.command in ("why", "record") and not args.target
    ):
        return EXIT_ARGS
    inputs = _inputs(args)
    if inputs is None:
        return EXIT_NO_SOURCE
    if all(t.state == "error" for t in inputs.todos) and inputs.gh_state != "read":
        print("ни один источник не прочитан", file=sys.stderr)
        return EXIT_NO_SOURCE
    if args.command == "record":
        save_inputs(inputs, Path(args.target) / "inputs.json")
        valid = parse_roadmap(inputs.roadmap_text, inputs.epics).valid
        return EXIT_OK if valid else EXIT_CONFIG
    if args.command == "run":
        return _run(args, inputs)
    result = evaluate(inputs, args.level if args.command == "plan" else 0)
    if args.command == "status":
        print(render_status(result, repo=args.target))
    elif args.command == "why":
        print(render_why(result, args.target))
    else:
        print(render_plan(result))
    return EXIT_OK if result.roadmap.valid else EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())
