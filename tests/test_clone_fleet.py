"""clone_fleet: ephemeral-клон набора репо из workspace-manifest.toml.

Сеть не нужна: «remote»-репо — локальные каталоги, манифест ссылается на них
file-path-URL'ами. Проверяются: клонирование по манифесту, дедуп общих
`git_dir` (atp-platform-sdk делит каталог с atp-platform), пропуск уже
существующего чекаута, ssh→https реврайт и ГРОМКИЙ отказ (exit 3) при
недоступном remote — молча уменьшенная поверхность флота читалась бы
plan-check'ом как «репо не начекаучен», т.е. нечитаемое выглядело бы чистым.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from clone_fleet import normalize_url

SCRIPT = Path(__file__).resolve().parents[1] / "clone_fleet.py"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True
    )


def _make_remote(tmp: Path, name: str) -> Path:
    repo = tmp / "remotes" / name
    repo.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-q", "-b", "master", str(repo)], check=True
    )
    _git(repo, "config", "user.email", "fixture@test")
    _git(repo, "config", "user.name", "fixture")
    (repo / "TODO.md").write_text(f"- [ ] item @owner:github:x @id:{name}-item\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo


def _manifest(tmp: Path, body: str) -> Path:
    path = tmp / "workspace-manifest.toml"
    path.write_text(body)
    return path


def _run(manifest: Path, root: Path, *extra: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--manifest", str(manifest),
         "--root", str(root), *extra],
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_clones_manifest_set_with_git_dir_dedup(tmp_path):
    alpha = _make_remote(tmp_path, "alpha")
    beta = _make_remote(tmp_path, "beta")
    manifest = _manifest(tmp_path, (
        f'[cores.alpha]\nrepo_url = "{alpha}"\ngit_dir = "alpha"\n'
        # member делит git_dir с alpha — клонится ровно один раз
        f'[cores.alpha-sdk]\nrepo_url = "{alpha}"\ngit_dir = "alpha"\n'
        f'[apps.beta]\nrepo_url = "{beta}"\ngit_dir = "beta"\n'
    ))
    root = tmp_path / "ws"
    code, out = _run(manifest, root)
    assert code == 0, out
    assert (root / "alpha" / "TODO.md").is_file()
    assert (root / "beta" / "TODO.md").is_file()
    assert "2 clone(s)" in out


def test_existing_checkout_is_skipped_not_overwritten(tmp_path):
    alpha = _make_remote(tmp_path, "alpha")
    manifest = _manifest(
        tmp_path, f'[cores.alpha]\nrepo_url = "{alpha}"\ngit_dir = "alpha"\n'
    )
    root = tmp_path / "ws"
    marker = root / "alpha" / "TODO.md"
    assert _run(manifest, root)[0] == 0
    marker.write_text("local edit\n")
    code, out = _run(manifest, root)
    assert code == 0, out
    assert marker.read_text() == "local edit\n"
    assert "1 skipped" in out


def test_unreachable_remote_fails_loud_after_trying_all(tmp_path):
    beta = _make_remote(tmp_path, "beta")
    manifest = _manifest(tmp_path, (
        f'[cores.ghost]\nrepo_url = "{tmp_path / "remotes" / "ghost"}"\n'
        'git_dir = "ghost"\n'
        f'[apps.beta]\nrepo_url = "{beta}"\ngit_dir = "beta"\n'
    ))
    root = tmp_path / "ws"
    code, out = _run(manifest, root)
    assert code == 3, out
    assert "ghost" in out
    # остальной набор всё равно попытались и склонировали
    assert (root / "beta" / "TODO.md").is_file()


def test_unparseable_manifest_exits_2_with_message(tmp_path):
    manifest = _manifest(tmp_path, "не toml [[[")
    code, out = _run(manifest, tmp_path / "ws")
    assert code == 2, out
    assert "не разобран" in out


def test_missing_manifest_exits_2(tmp_path):
    code, out = _run(tmp_path / "absent.toml", tmp_path / "ws")
    assert code == 2, out


def test_normalize_url_rewrites_ssh_to_https_only():
    assert (
        normalize_url("git@github.com:owner/repo.git", https=True)
        == "https://github.com/owner/repo.git"
    )
    ssh = "git@github.com:owner/repo.git"
    assert normalize_url(ssh, https=False) == ssh
    local = "/tmp/somewhere/repo"
    assert normalize_url(local, https=True) == local
