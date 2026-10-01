"""CLI conductor: status | why | plan | run | record (спека §7.2, срез 0)."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import socket
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from conductor.actions import PlanContext, plan_records
from conductor.app_calls import AppCalls, init_host
from conductor.collect import collect, read_manifest
from conductor.gh_app import AppClient, Blocked, JournalLost
from conductor.graph import canonical_id, normalizer
from conductor.host_config import (
    SHARED_DIR,
    ConfigError,
    HostConfig,
    load_host_config,
    profile_fence,
    umbrella_name,
    umbrella_repo,
)
from conductor.inputs import Inputs, RepoTodo, load_inputs, save_inputs
from conductor.journal import MutationLog, RunJournal
from conductor.opstate import (
    StateError,
    init_state,
    open_state,
    recover_state,
)
from conductor.render import render_plan, render_status, render_why
from conductor.roadmap import Roadmap, parse_roadmap
from conductor.snapshot import Result, evaluate, to_snapshot
from conductor.sources_gh import run_gh
from conductor.sources_git import fetch, read_file_at_origin
from conductor.writer import StopPoint, Writer

EXIT_OK, EXIT_ARGS, EXIT_NO_SOURCE, EXIT_CONFIG, EXIT_STOP = 0, 2, 3, 4, 5
COMMANDS = ("status", "why", "plan", "run", "record", "init-state")
# ежечасный таймер: неделя прогонов (~3 МБ каждый) — не растить диск общего VPS
KEEP_RUNS = 168


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
    p.add_argument("--config", type=Path)
    p.add_argument("--recover", action="store_true")
    p.add_argument("command", nargs="?")
    p.add_argument("target", nargs="?")
    return p


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _inputs(args: argparse.Namespace) -> Inputs | None:
    if args.replay is not None:
        replayed = load_inputs(args.replay)
        if args.roadmap is not None:  # черновик роадмапа проверяется на записи
            present = args.roadmap.is_file()
            replayed.roadmap_text = (
                args.roadmap.read_text(encoding="utf-8") if present else None
            )
            replayed.roadmap_state = "read" if present else "absent"
            replayed.roadmap_source = f"file:{args.roadmap}"
            replayed.roadmap_sha = None
        return replayed
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


def _runs(out: Path) -> list[Path]:
    if not out.is_dir():
        return []
    return sorted(d for d in out.iterdir() if (d / "snapshot.json").is_file())


def _previous(out: Path) -> dict[str, Any] | None:
    """Последний читаемый снимок; битый пропускается — он только для отчёта (I7)."""
    for run in reversed(_runs(out)):
        try:
            return json.loads((run / "snapshot.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _init_state(args: argparse.Namespace) -> int:
    """init-state [--recover]: явная инициализация состояния (срез 1, §4.6).

    Под тем же lock хоста, что и run: инициализация не пересекается с
    прогоном таймера или ручным прогоном другого профиля.
    """
    if args.config is None:
        return EXIT_ARGS
    try:
        cfg = load_host_config(args.config)
    except ConfigError as exc:
        print(f"CFG-INVALID: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    with host_lock(cfg.lock) as held:
        if not held:
            print(f"init-state: занято ({cfg.lock})", file=sys.stderr)
            return EXIT_CONFIG
        now = datetime.now(UTC)
        try:
            (recover_state if args.recover else init_state)(cfg.state_dir, now)
            init_host(SHARED_DIR, cfg.app_id, cfg.installation_id, now)
        except (StateError, OSError) as exc:
            print(f"init-state: {exc}", file=sys.stderr)
            return EXIT_CONFIG
    print(f"состояние {cfg.state_dir} готово; карантин записей 65 мин")
    return EXIT_OK


@contextmanager
def host_lock(path: Path) -> Iterator[bool]:
    """flock -n на lock хоста (§4.6, §8): True — взят, False — занят.

    Все пути записи (run --config, init-state, --recover) берут его сами;
    внешний `flock` вокруг них не нужен (он занял бы тот же файл).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _origin_roadmap(
    root: Path, epics: dict[str, dict[str, Any]], umbrella_dir: str
) -> Roadmap | None:
    """Роадмап с origin зонтика профиля перед каждым шагом (§5.1 шаг 3)."""
    umbrella = root / umbrella_dir
    if fetch(umbrella) is not None:
        return None
    text, _, state, _ = read_file_at_origin(umbrella, "roadmap.toml")
    return parse_roadmap(text, epics) if state == "read" else None


def _start_checks(client: Any, umbrella: str) -> str | None:
    """О §8.2 на старте: ключ, установка владельца, покрытие зонтика профиля."""
    try:
        if client.check_key() is None:
            return "ID-MISMATCH: ключ App"
        if not client.check_installation(umbrella.split("/")[0]):
            return "ID-MISMATCH: установка"
        if client.covers(umbrella) is not True:
            return "ID-MISMATCH: зонтик не покрыт"
    except (Blocked, JournalLost) as exc:
        return f"App недоступен: {exc}"
    return None


def level_cap(args: argparse.Namespace, inputs: Inputs) -> int:
    """Потолок прогона из CLI (О §2.4): `--level`; 0 при `--replay` (офлайн-
    входы не источник живых мутаций) и при роадмапе не с origin (`--roadmap`)."""
    if args.replay is not None or inputs.roadmap_source != "origin":
        return 0
    return args.level


def _write_phase(
    args: argparse.Namespace,
    cfg: HostConfig,
    result: Result,
    inputs: Inputs,
    run_dir: Path,
    run_id: str,
) -> tuple[int, dict[str, Any], list[dict[str, Any]]]:
    """Фаза записи (срез 1): (код, блок writer снимка, журнал действий)."""
    now = datetime.now(UTC)
    opened = open_state(cfg.state_dir, now)
    calls, rate_finding = AppCalls.open(
        SHARED_DIR, cfg.app_id, cfg.installation_id, now
    )
    findings = [f for f in (opened.finding, rate_finding) if f]
    if opened.state is None or calls is None:
        return EXIT_CONFIG, {"is_writer": False, "reason": ", ".join(findings)}, []
    client = AppClient(cfg, calls)
    umbrella = umbrella_repo(cfg, inputs.owner)
    if (problem := _start_checks(client, umbrella)) is not None:
        opened.state.end_run(now)
        reason = ", ".join([*findings, problem])
        return EXIT_CONFIG, {"is_writer": False, "reason": reason}, []
    umbrella_dir = umbrella_name(cfg)
    writer = Writer(
        cfg=cfg,
        client=client,
        state=opened.state,
        log=MutationLog(run_dir),
        journal=RunJournal(run_dir),
        fence=profile_fence(cfg, inputs.owner, list(inputs.repo_names)),
        load_roadmap=lambda: _origin_roadmap(args.root, inputs.epics, umbrella_dir),
        hostname=socket.gethostname(),
        run_id=run_id,
        level_cap=level_cap(args, inputs),
        partial=result.graph_state == "partial",
    )
    ctx = PlanContext(cfg, umbrella, client.bot_login or "", opened.state)
    try:
        reports = writer.execute(plan_records(result, inputs, ctx))
    except StopPoint as stop:
        block = {"is_writer": True, "reason": f"точка остановки {stop.name}"}
        return EXIT_STOP, block, [{"stop_point": stop.name}]
    block = {
        "is_writer": True,
        "reason": "тень" if cfg.shadow else "запись",
        "level_cap": level_cap(args, inputs),
        "findings": findings,
    }
    return (EXIT_CONFIG if findings else EXIT_OK), block, [asdict(r) for r in reports]


def _run(
    args: argparse.Namespace,
    inputs: Inputs,
    cfg: HostConfig | None,
    cfg_error: str | None,
) -> int:
    result = evaluate(inputs, level_cap(args, inputs))
    run_id = inputs.captured_at.replace(":", "")
    snap = to_snapshot(result, inputs, run_id, _previous(args.out))
    run_dir = args.out / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    save_inputs(inputs, run_dir / "inputs.json")
    code = EXIT_OK if result.roadmap.valid else EXIT_CONFIG
    if cfg_error is not None:
        snap["writer"] = {"is_writer": False, "reason": f"CFG-INVALID: {cfg_error}"}
        code = EXIT_CONFIG
    elif cfg is not None:
        write_code, snap["writer"], snap["actions"]["journal"] = _write_phase(
            args, cfg, result, inputs, run_dir, run_id
        )
        code = max(code, write_code)
    _write_atomic(
        run_dir / "snapshot.json", json.dumps(snap, ensure_ascii=False, indent=1)
    )
    for old in _runs(args.out)[:-KEEP_RUNS]:
        shutil.rmtree(old, ignore_errors=True)
    print(render_status(result))
    return code


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


def _config(args: argparse.Namespace) -> tuple[HostConfig | None, str | None]:
    """Конфиг хоста для run --config: (cfg, None) | (None, ошибка) | (None, None)."""
    if args.config is None or args.command != "run":
        return None, None
    try:
        return load_host_config(args.config), None
    except ConfigError as exc:
        return None, str(exc)


def main(argv: list[str] | None = None) -> int:
    """Точка входа; коды выхода — §7.2 (rev 10)."""
    try:
        args = _parser().parse_args(argv)
    except (argparse.ArgumentError, SystemExit):
        return EXIT_ARGS
    if args.selftest:
        return _selftest()
    if args.command == "init-state":
        return _init_state(args)
    if args.command not in COMMANDS or (
        args.command in ("why", "record") and not args.target
    ):
        return EXIT_ARGS
    cfg, cfg_error = _config(args)
    if cfg is None:
        return _command(args, cfg, cfg_error)
    with host_lock(cfg.lock) as held:  # §8: один писатель на хосте за раз
        if not held:
            print(f"run: занято ({cfg.lock}) — прогон пропущен", file=sys.stderr)
            return EXIT_OK
        return _command(args, cfg, cfg_error)


def _command(
    args: argparse.Namespace, cfg: HostConfig | None, cfg_error: str | None
) -> int:
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
        return _run(args, inputs, cfg, cfg_error)
    result = evaluate(inputs, args.level if args.command == "plan" else 0)
    if args.command == "status":
        print(render_status(result, repo=args.target))
    elif args.command == "why":
        node = canonical_id(args.target, normalizer(inputs))
        if result.graph.resolve(node) not in result.graph.nodes:
            print(f"узел не найден: {args.target}", file=sys.stderr)
            return EXIT_ARGS
        print(render_why(result, node))
    else:
        print(render_plan(result))
    return EXIT_OK if result.roadmap.valid else EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())
