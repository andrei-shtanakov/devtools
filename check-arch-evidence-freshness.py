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


def resolve_upstream(prograph: Path) -> tuple[dict | None, Finding | None]:
    if not (prograph / ".git").exists():
        return None, Finding("upstream:prograph", "unavailable",
                             f"клона prograph нет: {prograph}")
    code, out, err = sh(["git", "ls-remote", "--symref", "origin", "HEAD"], prograph)
    if code != 0:
        return None, Finding("upstream:prograph", "unavailable",
                             f"origin недоступен: {err.strip() or code}")
    branch = head_sha = None
    for line in out.decode().splitlines():
        if line.startswith("ref:"):
            branch = line.split()[1].removeprefix("refs/heads/")
        elif line.endswith("HEAD"):
            head_sha = line.split()[0]
    if not branch or not head_sha:
        return None, Finding("upstream:prograph", "unavailable",
                             "ls-remote не вернул HEAD/symref")
    code, _, err = sh(["git", "fetch", "--quiet", "origin", branch], prograph)
    if code != 0:
        return None, Finding("upstream:prograph", "unavailable",
                             f"fetch origin/{branch} не удался: {err.strip()}")
    code, out, _ = sh(["git", "remote", "get-url", "origin"], prograph)
    remote = out.decode().strip() if code == 0 else "?"
    return {"remote": remote, "default_branch": branch, "head_sha": head_sha}, None


def upstream_bytes(prograph: Path, sha: str, relpath: str) -> bytes | None:
    code, out, _ = sh(["git", "cat-file", "blob", f"{sha}:{relpath}"], prograph)
    return out if code == 0 else None


def upstream_ls(prograph: Path, sha: str, reldir: str) -> list[str]:
    code, out, _ = sh(
        ["git", "ls-tree", "--name-only", sha, "--", reldir.rstrip("/") + "/"],
        prograph,
    )
    if code != 0:
        return []
    return sorted(Path(line).name for line in out.decode().splitlines() if line)


def check_vendored(workspace: Path, up: dict) -> tuple[list[Finding], dict]:
    findings: list[Finding] = []
    pins: dict[str, dict[str, str]] = {}
    prograph = workspace / "prograph"
    for name, vendored_rel, upstream_rel in VENDORED:
        vdir = workspace / vendored_rel
        pin_file = vdir / "PIN"
        if not vdir.is_dir() or not pin_file.is_file():
            findings.append(Finding(
                f"vendored:{name}", "unavailable",
                f"вендоренной копии/PIN нет: {vdir}"))
            continue
        pin = parse_pin(pin_file.read_text())
        pins[name] = {"source": pin.get("source", "?"),
                      "sha256": pin.get("sha256", "?")}
        # 1) расширение поверхности — ДО пофайлового сравнения
        ours = sorted(p.name for p in vdir.iterdir()
                      if p.is_file() and p.name != "PIN")
        theirs = upstream_ls(prograph, up["head_sha"], upstream_rel)
        extra = sorted(set(theirs) - set(ours))
        if extra:
            findings.append(Finding(
                f"surface:{name}", "drift",
                f"upstream добавил в {upstream_rel}: {', '.join(extra)} — "
                "поверхность расширилась, пересчёт её не видел бы"))
        # 2) пофайловое сравнение нашей поверхности
        for fname in ours:
            theirs_bytes = upstream_bytes(
                prograph, up["head_sha"], f"{upstream_rel}/{fname}")
            if theirs_bytes is None:
                findings.append(Finding(
                    f"schema-drift:{name}", "drift",
                    f"{fname} исчез из upstream {upstream_rel}"))
            elif theirs_bytes != (vdir / fname).read_bytes():
                findings.append(Finding(
                    f"schema-drift:{name}", "drift",
                    f"{fname}: origin/{up['default_branch']} "
                    f"({up['head_sha'][:9]}) отличается от копии — "
                    "нужен осознанный re-vendor PR в steward"))
    return findings, pins


def _manifest_changed_after(steward: Path, rel: str, cutoff: datetime) -> str | None:
    """Описание изменения манифеста позже cutoff, или None. Грязь тоже считается."""
    code, out, _ = sh(["git", "status", "--porcelain", "--", rel], steward)
    if code == 0 and out.decode().strip():
        return "манифест изменён и не закоммичен (не отражён в отчёте)"
    code, out, _ = sh(["git", "log", "-1", "--format=%cI", "--", rel], steward)
    if code != 0 or not out.decode().strip():
        return None
    committed = parse_iso(out.decode().strip())
    if committed > cutoff:
        return f"манифест коммитнут {iso(committed)} — позже отчёта {iso(cutoff)}"
    return None


def check_evidence(workspace: Path, now: datetime, max_age_days: int) -> list[Finding]:
    findings: list[Finding] = []
    evidence = workspace / EVIDENCE_REL
    steward = workspace / "steward"
    report_path = evidence / "conformance-report.json"
    manifest_rel = str(Path(EVIDENCE_REL).relative_to("steward") / "intended-graph.yaml")
    if not (evidence / "intended-graph.yaml").is_file():
        findings.append(Finding("evidence-missing:intended-graph.yaml", "stale",
                                "манифеста WS-005 нет"))
    if not report_path.is_file():
        findings.append(Finding("evidence-missing:conformance-report", "stale",
                                f"отчёта нет: {report_path}"))
        return findings
    try:
        report = json.loads(report_path.read_text())
        indexed_at = parse_iso(report["snapshot"]["indexed_at"])
        generated_at = parse_iso(report["generated_at"])
    except (ValueError, KeyError, TypeError) as err:
        findings.append(Finding("evidence-unreadable:conformance-report", "stale",
                                f"отчёт не разбирается: {err}"))
        return findings
    age = now - indexed_at
    if age > timedelta(days=max_age_days):
        findings.append(Finding(
            "evidence-age:conformance-report", "stale",
            f"snapshot.indexed_at={iso(indexed_at)} старше {max_age_days}д "
            f"(возраст {age.days}д)"))
    newer = _manifest_changed_after(steward, manifest_rel, generated_at)
    if newer:
        findings.append(Finding("manifest-newer:intended-graph.yaml", "stale", newer))
    return findings
