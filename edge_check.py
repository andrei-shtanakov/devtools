#!/usr/bin/env python3
"""edge-check — смысловая проверка узла бандла против его оснований.

Срез 1 плана `docs/superpowers/plans/2026-09-21-edge-check-v1.md`: одна
проверка, вызываемая оператором. Состав рёбер, инвалидация и публикация —
срезы 2 и 3, здесь их нет.

Коды выхода: 0 — PASS или N/A; 1 — FAIL; 2 — аргументы/конфигурация;
3 — ERROR (проверка не завершена).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from governance.edge_check import run_check
from governance.edge_check.rules import EdgeCheckError

_EXIT = {"PASS": 0, "N/A": 0, "FAIL": 1, "ERROR": 3}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edge", required=True)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--subject", required=True, action="append", type=Path)
    parser.add_argument("--basis", required=True, action="append")
    parser.add_argument("--contracts", type=Path,
                        default=Path(__file__).parent / "contracts/edge-check/v1")
    parser.add_argument("--model", default="claude-opus-5")
    parser.add_argument("--effort")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    bases: list[tuple[str, Path]] = []
    for raw in args.basis:
        role, sep, path = raw.partition("=")
        if not sep or not role or not path:
            print(f"--basis ожидает role=path, получено {raw!r}", file=sys.stderr)
            return 2
        bases.append((role, Path(path)))

    try:
        record = run_check(
            args.edge, args.bundle, args.subject, bases,
            contracts_dir=args.contracts, model=args.model,
            effort=args.effort, timeout=args.timeout,
        )
    except EdgeCheckError as exc:  # только конфигурация: неизвестное ребро
        print(f"edge-check: {exc}", file=sys.stderr)
        return 2

    text = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=False)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return _EXIT[record["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
