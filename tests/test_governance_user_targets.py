"""Тесты пользовательского списка целей (`governance/user_targets.py`)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from governance import user_targets as ut
from tests.df_config_fixture import use_test_real_home

URL = "git@github.com:me/my-repo.git"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    use_test_real_home(monkeypatch, h)
    return h


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True)


def _checkout(parent: Path, name: str = "my-repo", url: str = URL) -> Path:
    d = parent / name
    d.mkdir()
    _git("-C", str(d), "init", "-q")
    _git("-C", str(d), "remote", "add", "origin", url)
    return d


def _write(home: Path, body: str) -> None:
    p = ut.targets_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_load_missing_file_empty(home: Path) -> None:
    assert ut.load() == {}
    assert ut.lookup("x") is None


def test_load_roundtrip(home: Path, tmp_path: Path) -> None:
    d = tmp_path / "r"
    d.mkdir()
    _write(
        home,
        f'schema = "{ut.SCHEMA}"\n[targets.my-repo]\n'
        f'path = "{d}"\nrepo_url = "{URL}"\n',
    )
    got = ut.load()
    assert got == {"my-repo": ut.UserTarget("my-repo", d, URL)}
    assert ut.lookup("my-repo") == got["my-repo"]


def test_load_rejects_relative_path(home: Path) -> None:
    _write(
        home,
        f'schema = "{ut.SCHEMA}"\n[targets.a]\npath = "rel"\nrepo_url = "{URL}"\n',
    )
    with pytest.raises(ut.TargetsError, match="targets.toml"):
        ut.load()


def test_load_rejects_bad_schema(home: Path) -> None:
    _write(home, 'schema = "nope"\n')
    with pytest.raises(ut.TargetsError, match="schema"):
        ut.load()


def test_load_rejects_unknown_key(home: Path, tmp_path: Path) -> None:
    _write(
        home,
        f'schema = "{ut.SCHEMA}"\n[targets.a]\npath = "{tmp_path}"\n'
        f'repo_url = "{URL}"\nextra = "x"\n',
    )
    with pytest.raises(ut.TargetsError, match="extra"):
        ut.load()


def test_load_rejects_non_toml(home: Path) -> None:
    _write(home, "not = = toml")
    with pytest.raises(ut.TargetsError, match="targets.toml"):
        ut.load()


def test_register_from_git_checkout(home: Path, tmp_path: Path) -> None:
    d = _checkout(tmp_path)
    t = ut.register(d)
    assert t == ut.UserTarget("my-repo", d.resolve(), URL)
    assert ut.load() == {"my-repo": t}


def test_register_idempotent(home: Path, tmp_path: Path) -> None:
    d = _checkout(tmp_path)
    ut.register(d)
    text = ut.targets_path().read_text(encoding="utf-8")
    ut.register(d)
    assert ut.targets_path().read_text(encoding="utf-8") == text


def test_register_conflict_refused(home: Path, tmp_path: Path) -> None:
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()
    ut.register(_checkout(a))
    with pytest.raises(ut.TargetsError, match="уже зарегистрирован"):
        ut.register(_checkout(b))


def test_register_keeps_order(home: Path, tmp_path: Path) -> None:
    for n in ("zeta", "alpha"):
        p = tmp_path / n
        p.mkdir()
        ut.register(_checkout(p, n, f"git@github.com:me/{n}.git"))
    assert list(ut.load()) == ["zeta", "alpha"]


def test_register_non_git_dir(home: Path, tmp_path: Path) -> None:
    with pytest.raises(ut.TargetsError, match=str(tmp_path)):
        ut.register(tmp_path)


def test_targets_path_ignores_xdg_redirect(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "elsewhere"))
    assert ut.targets_path() == home / ".config" / "darkfactory" / "targets.toml"


def test_slug_from_url_rejects_unknown() -> None:
    assert ut.slug_from_url(URL) == "me/my-repo"
    with pytest.raises(ut.TargetsError):
        ut.slug_from_url("file:///x")


def test_cli_add_and_list(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    d = _checkout(tmp_path)
    assert ut.main(["add", str(d)]) == 0
    assert capsys.readouterr().out.split() == ["my-repo", str(d.resolve()), URL]
    assert ut.main(["list"]) == 0
    assert "my-repo" in capsys.readouterr().out
    assert ut.main(["add", str(tmp_path / "nope")]) == 2
    assert "nope" in capsys.readouterr().err


def test_register_dotted_repo_name_roundtrip(home: Path, tmp_path: Path) -> None:
    d = _checkout(tmp_path, "foo.js", "git@github.com:me/foo.js.git")
    ut.register(d)
    assert list(ut.load()) == ["foo.js"]


def test_register_non_ascii_path_roundtrip(home: Path, tmp_path: Path) -> None:
    parent = tmp_path / "проект-\U0001f600"
    parent.mkdir()
    d = _checkout(parent)
    t = ut.register(d)
    assert ut.load()["my-repo"].path == t.path == d.resolve()


def test_stale_entry_is_per_entry(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = _checkout(tmp_path, "good")
    gone = tmp_path / "gone"
    _write(
        home,
        f'schema = "{ut.SCHEMA}"\n'
        f'[targets.good]\npath = "{good.resolve()}"\nrepo_url = "{URL}"\n'
        f'[targets.stale]\npath = "{gone}"\nrepo_url = "{URL}"\n',
    )
    assert set(ut.load()) == {"good", "stale"}
    assert ut.lookup("good") is not None
    assert ut.lookup("other") is None
    with pytest.raises(ut.TargetsError) as exc:
        ut.lookup("stale")
    msg = str(exc.value)
    assert str(ut.targets_path()) in msg and "stale" in msg and "уберите запись" in msg
    assert ut.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "(нет каталога)" in out and "good" in out
    fresh = _checkout(tmp_path, "fresh")
    assert ut.register(fresh).repo == "fresh"
    assert set(ut.load()) == {"good", "stale", "fresh"}
