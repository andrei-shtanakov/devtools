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
    except (ValueError, KeyError, TypeError, AttributeError) as err:
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


def overall(findings: list[Finding]) -> str:
    if not findings:
        return "clean"
    return max((f.cls for f in findings), key=lambda c: CLASS_ORDER[c])


def write_status_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def _gh(args: list[str]) -> tuple[int, str]:
    """Единственная точка вызова gh — тесты подменяют её."""
    code, out, err = sh(["gh", *args], Path.cwd(), timeout=60)
    return code, out.decode(errors="replace").strip() if code == 0 else err.strip()


def steward_repo_slug(steward: Path) -> str | None:
    code, out, _ = sh(["git", "remote", "get-url", "origin"], steward)
    if code != 0:
        return None
    import re
    match = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", out.decode().strip())
    return match.group(1) if match else None


def escalate_findings(workspace: Path, classes: list[str],
                      findings: list[Finding], host: str) -> list[dict]:
    records: list[dict] = []
    repo = steward_repo_slug(workspace / "steward")
    if repo is None:
        return [{"class": c, "action": "error",
                 "detail": "origin steward не определён"} for c in classes]
    for cls in classes:
        prefix = f"{DEDUP_PREFIX}:{cls}"
        code, out = _gh(["issue", "list", "-R", repo, "--label", "inbox",
                         "--state", "open", "--limit", "100",
                         "--json", "number,title,url"])
        if code != 0:
            records.append({"class": cls, "action": "error", "detail": out})
            continue
        try:
            existing = [i for i in json.loads(out or "[]")
                        if i["title"].startswith(prefix)]
        except (ValueError, KeyError, TypeError) as err:
            records.append({"class": cls, "action": "error",
                            "detail": f"issue list не разбирается: {err}"})
            continue
        if existing:
            records.append({"class": cls, "action": "exists",
                            "detail": f"#{existing[0]['number']} уже открыт"})
            continue
        lines = "\n".join(f"- `{f.check}`: {f.detail}"
                          for f in findings if f.cls == cls)
        body = (
            f"slug: {DEDUP_PREFIX}\n"
            f"from: devtools#{DEDUP_PREFIX}\n\n"
            f"Автосенсор devtools (host `{host}`) обнаружил класс `{cls}`:\n\n"
            f"{lines}\n\n"
            "Действие — осознанный re-vendor/refresh PR в steward; сенсор "
            "READ-ONLY и ничего не меняет сам. Статус-файл: "
            "`devtools/out/arch-evidence-freshness/status.json`.\n"
        )
        title = f"{prefix} — автосенсор devtools, host {host}"
        code, out = _gh(["issue", "create", "-R", repo, "--label", "inbox",
                         "--title", title, "--body", body])
        records.append({"class": cls,
                        "action": "created" if code == 0 else "error",
                        "detail": out})
    return records


def read_status(path: Path, now: datetime) -> int:
    """Читатель. unknown — вывод ЭТОЙ стороны: сенсор его никогда не пишет."""
    try:
        status = json.loads(path.read_text())
        schema = status["schema"]
        next_expected = parse_iso(status["next_expected_at"])
        verdict = status["status"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as err:
        print(f"unknown: статус-файл отсутствует/не разбирается ({err}) — {path}")
        return 2
    if schema != STATUS_SCHEMA:
        print(f"unknown: чужая схема статус-файла {schema!r} — {path}")
        return 2
    if now > next_expected:
        print(f"unknown: статус просрочен (next_expected_at={iso(next_expected)}, "
              f"now={iso(now)}) — последний вердикт {verdict!r} не считается")
        return 2
    print(f"{verdict}: прогон {status.get('completed_at')} host="
          f"{status.get('host')} classes={status.get('classes')}")
    return 0 if verdict == "clean" else 1


def run_sensor(workspace: Path, status_path: Path, now: datetime,
               max_age_days: int, next_expected_hours: int,
               escalate: bool) -> int:
    started = now
    findings: list[Finding] = []
    resolved: dict = {"workspace": str(workspace), "pins": {}}
    up, up_finding = resolve_upstream(workspace / "prograph")
    if up_finding:
        findings.append(up_finding)
    else:
        resolved["upstream"] = up
        vendored_findings, pins = check_vendored(workspace, up)
        findings.extend(vendored_findings)
        resolved["pins"] = pins
    findings.extend(check_evidence(workspace, now, max_age_days))
    classes = sorted({f.cls for f in findings}, key=lambda c: -CLASS_ORDER[c])
    host = socket.gethostname()
    escalations = (escalate_findings(workspace, classes, findings, host)
                   if escalate and classes else [])
    write_status_atomic(status_path, {
        "schema": STATUS_SCHEMA,
        "sensor_version": SENSOR_VERSION,
        "host": host,
        "started_at": iso(started),
        "completed_at": iso(now),
        "next_expected_at": iso(now + timedelta(hours=next_expected_hours)),
        "status": overall(findings),
        "classes": classes,
        "findings": [f.as_json() for f in findings],
        "resolved": resolved,
        "escalations": escalations,
    })
    for f in findings:
        print(f"[{f.cls}] {f.check}: {f.detail}")
    print(f"status: {overall(findings)} -> {status_path}")
    return 0 if not findings else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path,
                        default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--status-file", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--now", default=None,
                        help="ISO-время 'сейчас' (тесты); по умолчанию UTC now")
    parser.add_argument("--max-age-days", type=int, default=30)
    parser.add_argument("--next-expected-hours", type=int, default=26)
    parser.add_argument("--escalate", action="store_true")
    parser.add_argument("--read", action="store_true",
                        help="режим читателя: unknown при просрочке")
    args = parser.parse_args(argv)
    now = parse_iso(args.now) if args.now else datetime.now(timezone.utc)
    if args.read:
        return read_status(args.status_file, now)
    try:
        return run_sensor(args.workspace.resolve(), args.status_file, now,
                          args.max_age_days, args.next_expected_hours,
                          args.escalate)
    except Exception:
        traceback.print_exc()
        return 4


if __name__ == "__main__":
    sys.exit(main())
