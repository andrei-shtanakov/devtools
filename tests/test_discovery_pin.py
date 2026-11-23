"""Installer's discovery pin vs the vendored brief contract (devtools#830)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import check_discovery_pin as pin  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HEADER = "# Discovery Brief — контракт выходного артефакта интервьюера ({v})\n"


def _vendored() -> str:
    version = pin.contract_version(pin.VENDORED.read_text(encoding="utf-8"))
    assert version is not None
    return version


def _serving(text: str | None):
    """Fetcher that answers every request with `text` (None — unreachable)."""
    calls: list[tuple[str, str]] = []

    def fetch(commit: str, rel: str) -> bytes | None:
        calls.append((commit, rel))
        return None if text is None else text.encode("utf-8")

    return fetch, calls


def test_same_version_is_ok() -> None:
    fetch, calls = _serving(HEADER.format(v=_vendored()))
    assert pin.verify(fetch).status == "ok"
    assert calls == [(pin.pinned_sha(), pin.UPSTREAM_CONTRACT)]


def test_other_version_fails() -> None:
    """Условие #830: на пине v1.2, гейт v1.3."""
    fetch, _ = _serving(HEADER.format(v="v1.2"))
    verdict = pin.verify(fetch)
    assert verdict.status == "failed"
    assert "v1.2" in verdict.detail


def test_contract_without_version_fails() -> None:
    fetch, _ = _serving("# что-то другое\n")
    assert pin.verify(fetch).status == "failed"


def test_unreachable_upstream_is_unknown() -> None:
    fetch, _ = _serving(None)
    assert pin.verify(fetch).status == "unknown"


def test_pinned_sha_is_full() -> None:
    assert len(pin.pinned_sha()) == 40


def test_repo_comes_from_components_url() -> None:
    assert pin.pinned_repo().endswith("/discovery")


def test_non_github_url_refused(tmp_path: Path) -> None:
    bad = tmp_path / "c.toml"
    bad.write_text('[repos.discovery]\nurl = "file:///x"\nsha = "a"\n', "utf-8")
    with pytest.raises(ValueError):
        pin.pinned_repo(bad)


def test_local_checkout_carries_vendored_contract() -> None:
    """То же по соседнему checkout `../discovery`, без сети. Skip — только если
    нет самого checkout или в нём нет закреплённого коммита; коммит есть, а
    контракта по пути нет — провал."""
    repo = ROOT.parent / "discovery"
    if not (repo / ".git").exists():
        pytest.skip("нет checkout ../discovery")
    sha = pin.pinned_sha()

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
        )

    if git("cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
        pytest.skip(f"в ../discovery нет коммита {sha[:12]} (git fetch)")
    shown = git("show", f"{sha}:{pin.UPSTREAM_CONTRACT}")
    assert shown.returncode == 0, shown.stderr
    assert pin.contract_version(shown.stdout) == _vendored()
