#!/usr/bin/env python3
"""check-arch-evidence-freshness.py — freshness/drift-сенсор арх-evidence (v0.1).

Дом: devtools/. Исполняет scheduled-обязательство
todo://steward/arch-evidence-freshness-watch («вне CI этого репо» — здесь):
  A. upstream-drift обеих вендоренных prograph-схем steward — сравнение с
     origin/<default> prograph (resolved SHA записывается), НЕ с рабочим деревом;
     до пофайлового сравнения — проверка расширения upstream-поверхности.
  B. свежесть evidence-пары WS-005 (intended-graph.yaml + conformance-report.json)
     по snapshot.indexed_at + сигнал «манифест новее отчёта».

Два слоя статусов (не смешивать): сенсор пишет только статус ПРОГОНА —
clean|drift|stale|unavailable (stale = evidence отсутствует/просрочено,
unavailable = сравнение не состоялось). `unknown` сенсор не пишет НИКОГДА:
это выводимый статус читателя (--read) — просроченный или отсутствующий
статус-файл по next_expected_at читается как unknown, не как последний зелёный.
Поэтому неожиданный краш = exit 4 БЕЗ записи статус-файла: просрочку поймает
читатель; частичная запись лгала бы.

READ-ONLY: при красном — inbox-issue в steward (ADR-ECO-006, только под
--escalate) с дедуп-ключом в заголовке `arch-evidence-freshness-watch:<class>`;
никакого автоматического ре-вендора.

Использование:
    ./check-arch-evidence-freshness.py --workspace ..            # прогон
    ./check-arch-evidence-freshness.py --workspace .. --escalate # прогон + issue
    ./check-arch-evidence-freshness.py --read                    # читатель
Exit: прогон 0=clean, 1=есть findings, 4=краш сенсора;
      читатель 0=clean, 1=non-clean, 2=unknown.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

SENSOR_VERSION = "0.1.0"
STATUS_SCHEMA = "arch-evidence-freshness-status/v1"
CLASS_ORDER = {"clean": 0, "unavailable": 1, "stale": 2, "drift": 3}
DEDUP_PREFIX = "arch-evidence-freshness-watch"

VENDORED = (
    ("intended-graph", "steward/contracts/prograph-intended-graph/v1",
     "contracts/intended-graph/v1"),
    ("conformance-report", "steward/contracts/prograph-conformance-report/v1",
     "contracts/conformance-report/v1"),
)
EVIDENCE_REL = "steward/workstreams/WS-005-gate-verdicts/spec"
DEFAULT_STATUS = Path(__file__).resolve().parent / "out/arch-evidence-freshness/status.json"


@dataclass
class Finding:
    check: str
    cls: str  # clean|drift|stale|unavailable — слой сенсора, unknown не бывает
    detail: str

    def as_json(self) -> dict[str, str]:
        return {"check": self.check, "class": self.cls, "detail": self.detail}


def parse_pin(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip():
            out[key.strip()] = value.strip()
    return out


def parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sh(args: list[str], cwd: Path, timeout: int = 60) -> tuple[int, bytes, str]:
    """(код, stdout-байты, stderr-текст); отсутствие бинаря/таймаут — код -1."""
    try:
        proc = subprocess.run(args, cwd=cwd, capture_output=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr.decode(errors="replace")
    except (FileNotFoundError, subprocess.TimeoutExpired) as err:
        return -1, b"", str(err)
