#!/usr/bin/env python3
"""clone_fleet.py — ephemeral-клон набора репо из workspace-manifest.toml.

Дом: devtools/. Часть scheduled fleet plan-check
(@id:scheduled-fleet-plan-check): workflow клонирует заявленный манифестом
набор в ephemeral workspace и гоняет настоящий кросс-репный plan-check.

Правила:
  * набор = секции [cores.*] / [apps.*] / [tools.*] с repo_url и git_dir;
    записи, делящие git_dir (workspace-member), клонятся ОДИН раз;
  * --https переписывает ssh-форму git@github.com:owner/repo.git в
    https://github.com/owner/repo.git — CI-раннер без ssh-ключа клонирует
    публичные репо анонимно; прочие URL (file-path в тестах) не трогаются;
  * существующий чекаут пропускается, не перезаписывается;
  * недоступный remote — ГРОМКИЙ отказ: докачиваем остальных, но выходим
    кодом 3. Молча уменьшенная поверхность флота читалась бы plan-check'ом
    как «репо не начекаучен» — нечитаемое выглядело бы чистым.

Exit: 0 — весь набор на месте; 3 — хотя бы один клон не удался; 2 — не
разобран манифест/аргументы. Stdlib, Python 3.11+.

Использование:
    ./clone_fleet.py --manifest <workspace-manifest.toml> --root <dir> [--https] [--depth 1]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

SECTIONS = ("cores", "apps", "tools")
_SSH_FORM = re.compile(r"^git@github\.com:(?P<slug>[^/]+/.+?)(?:\.git)?$")


def normalize_url(url: str, *, https: bool) -> str:
    """ssh-форма → https при --https; всё прочее — как есть."""
    if not https:
        return url
    match = _SSH_FORM.match(url)
    if match is None:
        return url
    return f"https://github.com/{match.group('slug')}.git"


def manifest_set(manifest: Path) -> dict[str, str]:
    """git_dir → repo_url; первый победил (дедуп workspace-member'ов)."""
    data = tomllib.loads(manifest.read_text())
    out: dict[str, str] = {}
    for section in SECTIONS:
        for entry in data.get(section, {}).values():
            git_dir, repo_url = entry.get("git_dir"), entry.get("repo_url")
            if git_dir and repo_url and git_dir not in out:
                out[git_dir] = repo_url
    return out


def clone_fleet(
    manifest: Path, root: Path, *, https: bool, depth: int
) -> int:
    root.mkdir(parents=True, exist_ok=True)
    cloned, skipped, failed = 0, 0, []
    for git_dir, repo_url in sorted(manifest_set(manifest).items()):
        target = root / git_dir
        if (target / ".git").exists():
            skipped += 1
            continue
        url = normalize_url(repo_url, https=https)
        proc = subprocess.run(
            ["git", "clone", "--quiet", "--depth", str(depth), url,
             str(target)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            failed.append(git_dir)
            print(f"FAIL {git_dir}: {proc.stderr.strip()}", file=sys.stderr)
        else:
            cloned += 1
    print(f"clone-fleet: {cloned} clone(s), {skipped} skipped, "
          f"{len(failed)} failed -> {root}")
    return 3 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--https", action="store_true",
                        help="переписать ssh-URL'ы на https (CI без ключа)")
    parser.add_argument("--depth", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        return clone_fleet(
            args.manifest, args.root, https=args.https, depth=args.depth
        )
    except (OSError, tomllib.TOMLDecodeError) as err:
        print(f"clone-fleet: манифест не прочитан/не разобран: {err}",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
