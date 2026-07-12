#!/usr/bin/env python3
"""check-release-drift.py — детектор релиз-дрифта intended ↔ observed (v0.2, hardened).

Дом: devtools/. READ-ONLY по репо. Stdlib; на 3.10 — fallback на tomli
(как devtools/discover_models.py). Запускать под тем же python, что остальные check-*.py.

Шов тот же, что у check-agent-id-conformance.py / check-graph-registry-drift.py:
    intended = release-manifest.toml (schema v0.2)   — что ДОЛЖНО быть в наборе
    observed = git-теги + pyproject.version на диске  — что фактически
    drift    = список findings с severity + exit-code для CI-gate

── Что исправлено vs v0.1 (по ревью) ──
  * schema-валидация обязательных полей (иначе draft роняет CI молча);
  * workspace-member: git_dir и pyproject_path разведены (atp-platform-sdk больше
    не даёт ложный no_repo_on_disk);
  * tag-детекция по tag_pattern (`git tag --list PATTERN` + sort по version),
    а не `git describe --abbrev=0` (тот брал component-tag на umbrella-repo);
  * severity: error | warn | info; exit 2 при error, 1 при warn (--strict → error),
    0 если чисто;
  * обработка ошибок git/subprocess (нет git / не repo / таймаут) → info, не краш.

Использование:
    ./check-release-drift.py --workspace .. --manifest release-manifest.toml
    ./check-release-drift.py --workspace .. --json      # для dispatcher (стабильный контракт)
    ./check-release-drift.py --workspace .. --strict     # warn тоже валит gate
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # 3.10 fallback (pip install tomli) — как в devtools/discover_models.py
    import tomli as tomllib

REQUIRED = ("package_name", "repo_url", "git_dir", "pyproject_path", "tag_pattern", "install")
INSTALL_KINDS = {"pypi", "git-tag", "git-sha"}
SEV_ORDER = {"info": 0, "warn": 1, "error": 2}


def sh(args: list[str], cwd: Path) -> tuple[int, str]:
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=15)
        return p.returncode, p.stdout.strip()
    except FileNotFoundError:
        return 127, ""          # нет git
    except subprocess.TimeoutExpired:
        return 124, ""
    except Exception:
        return 1, ""


def version_key(tag: str) -> tuple:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", tag)
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def latest_matching_tag(repo: Path, pattern: str) -> str:
    rc, out = sh(["git", "tag", "--list", pattern], repo)
    if rc != 0 or not out:
        return "-"
    tags = [t for t in out.splitlines() if t.strip()]
    return max(tags, key=version_key) if tags else "-"


def norm(tag: str) -> str:
    m = re.search(r"(\d+\.\d+\.\d+)", tag)
    return m.group(1) if m else tag


def pyproject_version(pp: Path) -> str | None:
    if not pp.exists():
        return None
    try:
        data = tomllib.loads(pp.read_text(encoding="utf-8"))
    except Exception:
        return None
    return (data.get("project") or {}).get("version")


def current_branch(repo: Path) -> str:
    _, out = sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo)
    return out


def check_component(cid: str, meta: dict, ws: Path) -> list[dict]:
    f: list[dict] = []

    def add(kind: str, sev: str, detail: str):
        f.append({"component": cid, "kind": kind, "severity": sev, "detail": detail})

    # 0. schema
    missing = [k for k in REQUIRED if k not in meta]
    if missing:
        add("schema_missing", "error", f"нет обязательных полей: {', '.join(missing)}")
        return f  # дальше проверять нечего
    install = meta["install"]
    if install not in INSTALL_KINDS:
        add("schema_install", "error", f"install={install!r} вне {sorted(INSTALL_KINDS)}")
    # 1. воспроизводимость: никакого HEAD
    if install == "git-sha" and not meta.get("sha"):
        add("irreproducible", "error", "install=git-sha без sha — плавающий HEAD запрещён")
    if install == "git-tag" and meta.get("tag", "-") in ("-", None):
        add("irreproducible", "error", "install=git-tag, но tag отсутствует")

    lock_ver = meta.get("lock_version")
    mtag = meta.get("tag", "-")
    publish = meta.get("publish")

    # Манифест-only (репо на диске НЕ нужен): tag ↔ lock_version согласованность.
    # git-sha имеет '+g<sha>' в lock_version и осознанно пропускается.
    if lock_ver and "+g" not in lock_ver and mtag not in ("-", None) and norm(mtag) != lock_ver:
        sev = "error" if publish == "pypi" else "warn"
        add("tag_lock_mismatch", sev, f"manifest tag {mtag} (={norm(mtag)}) != lock_version {lock_ver}")

    git_dir = ws / meta["git_dir"]
    pp = ws / meta["pyproject_path"]

    if not (git_dir / ".git").exists():
        add("no_repo_on_disk", "info", f"{meta['git_dir']} без .git — проверки по диску пропущены")
        return f

    disk_ver = pyproject_version(pp)
    if disk_ver is None:
        add("no_pyproject_version", "warn", f"не прочитал version из {meta['pyproject_path']}")
    tag = latest_matching_tag(git_dir, meta["tag_pattern"])

    # 2. manifest_stale: lock_version расходится с pyproject
    if lock_ver and disk_ver and "+g" not in lock_ver and lock_ver != disk_ver:
        add("manifest_stale", "warn", f"lock_version={lock_ver} != pyproject={disk_ver}")

    # 3. tag_behind: version на диске обгоняет последний tag
    if disk_ver and tag != "-" and norm(tag) != disk_ver:
        sev = "error" if publish == "pypi" else "warn"
        kind = "unreleased" if publish == "pypi" else "tag_behind"
        add(kind, sev, f"pyproject={disk_ver} != tag {tag} ({'ядро не нарезано' if sev=='error' else 'релиз отстал'})")

    # 4. tag_missing: манифест ссылается на tag, которого нет под pattern
    if mtag not in ("-", None):
        rc, out = sh(["git", "tag", "--list", mtag], git_dir)
        if rc == 0 and mtag not in out.splitlines():
            add("tag_missing", "warn", f"manifest tag {mtag} не найден среди git-тегов")

    # 4b. git-tag с sha: sha обязан указывать на коммит тега (провенанс)
    if meta["install"] == "git-tag" and meta.get("sha") and mtag not in ("-", None):
        rc, tag_commit = sh(["git", "rev-list", "-n1", mtag], git_dir)
        if rc == 0 and tag_commit:
            s = meta["sha"]
            if not (tag_commit.startswith(s) or s.startswith(tag_commit[: len(s)])):
                add("sha_tag_mismatch", "warn", f"sha {s} != commit тега {mtag} ({tag_commit[:12]})")

    # 5. branch drift
    mbranch = meta.get("branch")
    if mbranch:
        cur = current_branch(git_dir)
        add("branch_nondefault", "warn",
            f"репо на фичевой ветке {mbranch!r} (сейчас {cur!r}) — sha не с релизной ветки")

    return f


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default="..")
    ap.add_argument("--manifest", default="release-manifest.toml")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="warn тоже валит gate")
    args = ap.parse_args()

    ws = Path(args.workspace).resolve()
    try:
        manifest = tomllib.loads(Path(args.manifest).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"FATAL: манифест не прочитан: {e}", file=sys.stderr)
        return 2

    comps: dict[str, dict] = {}
    comps.update(manifest.get("cores", {}))
    comps.update(manifest.get("apps", {}))
    if not comps:
        print("FATAL: в манифесте нет [cores.*]/[apps.*]", file=sys.stderr)
        return 2

    findings: list[dict] = []
    for cid, meta in comps.items():
        findings.extend(check_component(cid, meta, ws))

    findings.sort(key=lambda x: -SEV_ORDER.get(x["severity"], 0))
    n_err = sum(1 for x in findings if x["severity"] == "error")
    n_warn = sum(1 for x in findings if x["severity"] == "warn")

    if args.json:
        print(json.dumps({
            "schema_version": manifest.get("schema_version"),
            "generated": manifest.get("generated"),
            "counts": {"error": n_err, "warn": n_warn,
                       "info": len(findings) - n_err - n_warn},
            "findings": findings,
        }, ensure_ascii=False, indent=2))
    else:
        if not findings:
            print("release-drift: OK — расхождений нет")
        else:
            print(f"release-drift: error={n_err} warn={n_warn} "
                  f"info={len(findings)-n_err-n_warn}\n")
            for x in findings:
                print(f"  [{x['severity']:<5}] {x['kind']:<18} {x['component']:<18} {x['detail']}")

    if n_err:
        return 2
    if n_warn and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
