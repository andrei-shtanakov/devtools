"""The `libexec/` wrappers of stage 2a (Task 4)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from df import libexec
from df import platform as plat
from df.paths import Layout
from tests.df.conftest import FakeLayout

FIXTURES = Path(__file__).parent / "fixtures" / "libexec"
PYTHON = Path("/opt/df/admin-python/cpython-3.12.13-linux-x86_64-gnu/bin/python3.12")
OTHER_PYTHON = Path(
    "/opt/df/admin-python/cpython-3.12.14-linux-x86_64-gnu/bin/python3.12"
)
NAMES = {
    "libexec/df-launch": "df-launch",
    "libexec/df-review-run": "df-review-run",
    "libexec/df-publish": "df-publish",
    "libexec/df-precheck": "df-precheck",
    "bin/df-run-start": "df-run-start",
}


@pytest.fixture()
def fake(fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch) -> FakeLayout:
    """Directories in place; the ancestor policy is the host's, not under test."""
    fake_layout.make_dirs()
    fake_layout.layout.libexec.mkdir()
    monkeypatch.setattr(plat, "trusted_path_problems", lambda _path: [])
    return fake_layout


def test_wrappers_are_the_five_of_the_briefs() -> None:
    assert libexec.WRAPPERS == tuple(NAMES)


@pytest.mark.parametrize("name", NAMES)
def test_wrapper_text_exact(name: str) -> None:
    text = libexec.wrapper_text(Layout(Path("/")), name, PYTHON)
    assert text == (FIXTURES / f"{NAMES[name]}.txt").read_text(encoding="utf-8")


def test_wrapper_text_precheck() -> None:
    """Plan decision 4: the entry's `precheck serve`, no argument of its own
    (sudoers allows none: `df-precheck ""`)."""
    text = libexec.wrapper_text(Layout(Path("/")), "libexec/df-precheck", PYTHON)
    assert text == (
        f"#!/bin/sh\nexec {PYTHON} -I -B -S /opt/df/bin/darkfactory-entry.py "
        'precheck serve "$@"\n'
    )
    assert libexec.wrapper_path(Layout(Path("/")), "libexec/df-precheck") == Path(
        "/opt/df/libexec/df-precheck"
    )


def test_wrapper_text_unknown_name() -> None:
    with pytest.raises(ValueError, match="libexec/df-other"):
        libexec.wrapper_text(Layout(Path("/")), "libexec/df-other", PYTHON)


def test_wrapper_text_quotes_odd_paths() -> None:
    text = libexec.wrapper_text(
        Layout(Path("/")), "libexec/df-launch", Path("/opt/a b/python")
    )
    assert "exec '/opt/a b/python' -I -B -S" in text


def written(fake: FakeLayout) -> Layout:
    libexec.install_wrappers(fake.layout, PYTHON)
    return fake.layout


def test_install_wrappers_modes_and_clean_verdict(fake: FakeLayout) -> None:
    lay = written(fake)
    for name in libexec.WRAPPERS:
        mode = (lay.opt / name).stat().st_mode & 0o7777
        assert mode == 0o755, name
    assert libexec.wrapper_problems(lay, PYTHON) == []


def test_wrapper_problems_missing(fake: FakeLayout) -> None:
    problems = libexec.wrapper_problems(fake.layout, PYTHON)
    assert len(problems) == 5 and all("нет файла" in p for p in problems)


def test_wrapper_problems_foreign_owner(fake: FakeLayout) -> None:
    lay = written(fake)
    fake.set_owner(lay.opt / "libexec/df-publish", 4242)
    (problem,) = libexec.wrapper_problems(lay, PYTHON)
    assert "df-publish" in problem and "владелец uid 4242" in problem


@pytest.mark.parametrize("mode", [0o775, 0o700, 0o4755])
def test_wrapper_problems_wrong_mode(fake: FakeLayout, mode: int) -> None:
    lay = written(fake)
    os.chmod(lay.opt / "bin/df-run-start", mode)
    (problem,) = libexec.wrapper_problems(lay, PYTHON)
    assert "df-run-start" in problem and f"{mode:04o}" in problem


def test_wrapper_problems_other_text(fake: FakeLayout) -> None:
    lay = written(fake)
    (lay.opt / "libexec/df-launch").write_text("#!/bin/sh\nexit 0\n")
    (problem,) = libexec.wrapper_problems(lay, PYTHON)
    assert "df-launch" in problem and "текст" in problem


def test_wrapper_problems_symlink_instead_of_file(fake: FakeLayout) -> None:
    lay = written(fake)
    path = lay.opt / "libexec/df-launch"
    target = lay.opt / "libexec/real"
    path.rename(target)
    path.symlink_to(target)
    (problem,) = libexec.wrapper_problems(lay, PYTHON)
    assert "df-launch" in problem and "не обычный файл" in problem


def test_wrapper_problems_stale_python(fake: FakeLayout) -> None:
    lay = written(fake)
    problems = libexec.wrapper_problems(lay, OTHER_PYTHON)
    assert len(problems) == 5 and all("текст" in p for p in problems)


def test_wrapper_problems_untrusted_chain(
    fake: FakeLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    lay = written(fake)
    monkeypatch.setattr(plat, "trusted_path_problems", lambda p: [f"{p}: плохо"])
    problems = libexec.wrapper_problems(lay, PYTHON)
    assert len(problems) == 5 and all("плохо" in p for p in problems)


def test_install_wrappers_replaces_a_stale_one(fake: FakeLayout) -> None:
    lay = written(fake)
    libexec.install_wrappers(lay, OTHER_PYTHON)
    assert libexec.wrapper_problems(lay, OTHER_PYTHON) == []
    assert libexec.wrapper_problems(lay, PYTHON) != []


def test_wrapper_paths_agree_with_layout(fake: FakeLayout) -> None:
    lay = fake.layout
    assert lay.opt / "bin/df-run-start" == lay.run_start
    assert lay.opt / "libexec" == lay.libexec
    for name in libexec.WRAPPERS:
        assert libexec.wrapper_path(lay, name) == lay.opt / name
    with pytest.raises(ValueError):
        libexec.wrapper_path(lay, "libexec/df-other")


def test_missing_wrapper_of_a_set_up_host_is_stale(fake: FakeLayout) -> None:
    """A 2a host (wrappers present, `df-precheck` absent) after a 2b install:
    `install` says «roles setup» (stale); a stage-1 host (none) does not."""
    assert not libexec.wrappers_stale(fake.layout, PYTHON)
    lay = written(fake)
    assert not libexec.wrappers_stale(lay, PYTHON)
    (lay.libexec / "df-precheck").unlink()
    assert libexec.wrappers_present(lay)
    assert libexec.wrappers_stale(lay, PYTHON)
