"""Tests of df.boundary: static boundary checks and the transfer check (Task 8).

The release layout is built synthetically after the measured uv 0.11.23
layout (pre-flight §2): the managed Python under `python/<key>` with uv's
minor-version alias link, venvs whose `bin/python` and `pyvenv.cfg home` go
through the alias, scripts with the exact heads uv writes. Native inspection
(`linked_libraries`) and every run (`run_as`) are fakes.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest

from df import boundary
from df import platform as plat
from df.boundary import (
    TransferResult,
    classify_bin,
    expected_entrypoints,
    script_heads,
    static_problems,
    transfer_check,
)
from df.journal import StepFailed
from df.paths import Layout
from df.platform import LibDeps, RunResult
from tests.df.conftest import FakeLayout

PLATFORM = "macos-arm64"
TRIPLE = "macos-aarch64-none"
KEY = f"cpython-3.12.13-{TRIPLE}"
ALIAS = f"cpython-3.12-{TRIPLE}"
MACHO = b"\xcf\xfa\xed\xfe" + b"\0" * 28
ELF = b"\x7fELF" + b"\0" * 28
VERSION = "3.12.13 (main, Jun 11 2026, 03:55:21) [Clang 22.1.3 ]"
EXPECTED = {"r1": frozenset({"tool"})}
PYVENV = (
    "home = {home}\nimplementation = CPython\nuv = 0.11.23\n"
    "version_info = 3.12.13\ninclude-system-site-packages = false\nprompt = {repo}\n"
)


def _write(path: Path, data: bytes, mode: int = 0o755) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)
    return path


def write_script(rel: Path, repo: str, name: str, form: str = "direct") -> Path:
    direct, trampoline = script_heads(rel, repo)
    head = {"direct": direct, "trampoline": trampoline}[form]
    body = b"# -*- coding: utf-8 -*-\nimport sys\n"
    return _write(rel / "venv" / repo / "bin" / name, head + body)


def make_release(
    tmp_path: Path, scripts: Mapping[str, tuple[str, ...]] | None = None
) -> Path:
    """The measured layout: one key dir, the alias, venvs through the alias."""
    rel = Path(os.path.realpath(tmp_path)) / "rel"
    py = rel / "python" / KEY
    _write(py / "bin" / "python3.12", MACHO)
    os.symlink("python3.12", py / "bin" / "python3")
    os.symlink("python3.12", py / "bin" / "python")
    (py / "lib" / "python3.12" / "lib-dynload").mkdir(parents=True)
    _write(py / "lib" / "python3.12" / "os.py", b"# stdlib landmark\n", 0o644)
    _write(py / "lib" / "libpython3.12.dylib", MACHO)
    os.symlink(f"{rel}/python/{KEY}", rel / "python" / ALIAS)
    for repo, names in (scripts if scripts is not None else {"r1": ("tool",)}).items():
        add_venv(rel, repo, names)
    return rel


def add_venv(rel: Path, repo: str, names: tuple[str, ...] = ()) -> Path:
    venv = rel / "venv" / repo
    site = venv / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    (site / "_virtualenv.pth").write_bytes(b"import _virtualenv")
    (site / "_virtualenv.py").write_text("")
    home = f"{rel}/python/{ALIAS}/bin"
    (venv / "pyvenv.cfg").write_text(PYVENV.format(home=home, repo=repo))
    (venv / "bin").mkdir()
    os.symlink(f"{home}/python3.12", venv / "bin" / "python")
    os.symlink("python", venv / "bin" / "python3")
    os.symlink("python", venv / "bin" / "python3.12")
    _write(venv / "bin" / "activate", b"# activate\n", 0o644)
    for name in names:
        write_script(rel, repo, name)
    return venv


@pytest.fixture()
def deps(monkeypatch: pytest.MonkeyPatch) -> dict[Path, LibDeps]:
    """`linked_libraries` stub: per-file deps, none by default."""
    table: dict[Path, LibDeps] = {}

    def linked(path: Path) -> LibDeps | None:
        head = path.read_bytes()[:4]
        if head not in plat.MACHO_MAGIC and head != plat.ELF_MAGIC:
            return None
        return table.get(path, LibDeps((), ()))

    monkeypatch.setattr(plat, "linked_libraries", linked)
    return table


def problems(rel: Path, expected: Mapping[str, frozenset[str]] = EXPECTED) -> list[str]:
    return static_problems(rel, PLATFORM, expected)


def only(found: list[str], *needles: str) -> None:
    """Exactly one problem, mentioning every needle."""
    assert len(found) == 1, found
    assert all(n in found[0] for n in needles), found


# ---------- the measured layout passes ----------


def test_measured_layout_passes(tmp_path: Path, deps: dict[Path, LibDeps]) -> None:
    rel = make_release(tmp_path)
    assert problems(rel) == []


def test_classify_bin(tmp_path: Path) -> None:
    cases = {"m": MACHO, "e": ELF, "s": b"#!/bin/sh\n", "o": b"PK\x03\x04", "x": b"#"}
    for name, data in cases.items():
        _write(tmp_path / name, data)
    got = {n: classify_bin(tmp_path / n) for n in cases}
    assert got == {
        "m": "native",
        "e": "native",
        "s": "script",
        "o": "other",
        "x": "other",
    }
    for magic in plat.MACHO_MAGIC:
        _write(tmp_path / "f", magic + b"\0" * 4)
        assert classify_bin(tmp_path / "f") == "native"


# ---------- (а) executables of venv/*/bin ----------


@pytest.mark.parametrize(
    "shebang", ["#!/usr/bin/env python3", "#!/usr/local/bin/python3"]
)
def test_foreign_shebang_refused(
    tmp_path: Path, deps: dict[Path, LibDeps], shebang: str
) -> None:
    rel = make_release(tmp_path)
    _write(rel / "venv/r1/bin/tool", f"{shebang}\nimport sys\n".encode())
    only(problems(rel), "venv/r1/bin/tool", "интерпретатор")


def test_uv_trampoline_accepted(tmp_path: Path, deps: dict[Path, LibDeps]) -> None:
    """The exact bytes uv 0.11.23 writes above the 127-byte shebang limit."""
    rel = make_release(tmp_path)
    write_script(rel, "r1", "tool", "trampoline")
    head = (rel / "venv/r1/bin/tool").read_bytes()
    python = f"{rel}/venv/r1/bin/python".encode()
    assert head.startswith(
        b"#!/bin/sh\n'''exec' '" + python + b"' \"$0\" \"$@\"\n' '''\n"
    )
    assert problems(rel) == []


@pytest.mark.parametrize(
    "head",
    [
        # python-build-standalone's own relative form: never written for a venv.
        (
            b'#!/bin/sh\n\'\'\'exec\' "$(dirname -- "$(realpath -- "$0")")/python3.12"'
            b" \"$0\" \"$@\"\n' '''\n"
        ),
        b"#!/bin/sh\n'''exec' '/usr/bin/python3' \"$0\" \"$@\"\n' '''\n",
        b"#!{rel}/venv/r1/bin/python3\n",  # not exactly bin/python
        b"#!{rel}/venv/r2/bin/python\n",  # another environment's python
        b"#!{rel}/src/x/run.sh\n",  # F2: inside the release, outside the chain
        b"#!{rel}/venv/r1/bin/python -E\n",
    ],
)
def test_other_script_forms_refused(
    tmp_path: Path, deps: dict[Path, LibDeps], head: bytes
) -> None:
    rel = make_release(tmp_path)
    _write(rel / "venv/r1/bin/tool", head.replace(b"{rel}", str(rel).encode()))
    only(problems(rel), "venv/r1/bin/tool")


def test_native_without_shebang_accepted(
    tmp_path: Path, deps: dict[Path, LibDeps]
) -> None:
    rel = make_release(tmp_path)
    _write(rel / "venv/r1/bin/fastbin", MACHO)
    assert problems(rel) == []


def test_other_executable_refused(tmp_path: Path, deps: dict[Path, LibDeps]) -> None:
    rel = make_release(tmp_path)
    _write(rel / "venv/r1/bin/blob", b"PK\x03\x04zip")
    _write(rel / "venv/r1/bin/notes", b"text, not executable", 0o644)
    only(problems(rel), "venv/r1/bin/blob", "ни нативный, ни скрипт")


@pytest.mark.parametrize("name", ["python", "python3", "python3.12"])
def test_foreign_interpreter_link_refused(
    tmp_path: Path, deps: dict[Path, LibDeps], name: str
) -> None:
    """F2: `bin/python*` resolve in-tree to the managed `python3.12`."""
    rel = make_release(tmp_path)
    _write(rel / "other" / "py", MACHO)
    link = rel / "venv/r1/bin" / name
    link.unlink()
    os.symlink(f"{rel}/other/py", link)
    found = problems(rel)
    assert any(f"venv/r1/bin/{name}: ведёт в other/py" in p for p in found), found


def test_stray_link_in_bin_refused(tmp_path: Path, deps: dict[Path, LibDeps]) -> None:
    rel = make_release(tmp_path)
    os.symlink("tool", rel / "venv/r1/bin/tool2")
    only(problems(rel), "venv/r1/bin/tool2", "посторонняя ссылка")


def test_missing_python_link_refused(tmp_path: Path, deps: dict[Path, LibDeps]) -> None:
    rel = make_release(tmp_path)
    (rel / "venv/r1/bin/python3").unlink()
    (rel / "venv/r1/bin/python3.12").unlink()
    (rel / "venv/r1/bin/python").unlink()
    only(problems(rel), "venv/r1/bin/python", "нет интерпретатора")


# ---------- (б) pyvenv.cfg ----------


def _cfg(rel: Path, text: str) -> None:
    (rel / "venv/r1/pyvenv.cfg").write_text(text)


def test_pyvenv_home_outside_refused(tmp_path: Path, deps: dict[Path, LibDeps]) -> None:
    rel = make_release(tmp_path)
    _cfg(rel, PYVENV.format(home="/opt/homebrew/bin", repo="r1"))
    found = problems(rel)
    assert any("venv/r1: pyvenv.cfg: home вне релиза" in p for p in found), found


@pytest.mark.parametrize(
    "home",
    [
        "{rel}/python/{alias}/./bin",
        "{rel}//python/{alias}/bin",
        "{rel}/python/{alias}/lib",
        "{rel}/venv/r1/bin",
        "{rel}/python/{alias}/bin/../../{alias}/bin",
    ],
)
def test_pyvenv_home_must_be_canonical_python_bin(
    tmp_path: Path, deps: dict[Path, LibDeps], home: str
) -> None:
    rel = make_release(tmp_path)
    _cfg(rel, PYVENV.format(home=home.format(rel=rel, alias=ALIAS), repo="r1"))
    found = problems(rel)
    assert any(p.startswith("venv/r1: pyvenv.cfg: home") for p in found), found


@pytest.mark.parametrize(
    "defect", ["missing", "no-home", "no-include-system-site-packages"]
)
def test_pyvenv_cfg_is_mandatory(
    tmp_path: Path, deps: dict[Path, LibDeps], defect: str
) -> None:
    rel = make_release(tmp_path)
    cfg = rel / "venv/r1/pyvenv.cfg"
    if defect == "missing":
        cfg.unlink()
    else:
        key = "home" if defect == "no-home" else "include-system-site-packages"
        lines = cfg.read_text().splitlines()
        cfg.write_text("\n".join(x for x in lines if not x.startswith(key)) + "\n")
    found = problems(rel)
    assert any(p.startswith("venv/r1:") for p in found), found


@pytest.mark.parametrize(
    "line,ok",
    [
        ("include-system-site-packages = False", True),
        ("Include-System-Site-Packages=false", True),
        ("include-system-site-packages = true", False),
        ("include-system-site-packages = yes", False),
    ],
)
def test_pyvenv_system_site_value(
    tmp_path: Path, deps: dict[Path, LibDeps], line: str, ok: bool
) -> None:
    rel = make_release(tmp_path)
    home = f"{rel}/python/{ALIAS}/bin"
    _cfg(rel, f"home = {home}\n{line}\n")
    assert (problems(rel) == []) is ok


@pytest.mark.parametrize(
    "joint",
    ["\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029", "\x00"],
)
def test_pyvenv_cfg_line_break_differential_refused(
    tmp_path: Path, deps: dict[Path, LibDeps], joint: str
) -> None:
    """`str.splitlines` would see a second `home` where CPython (newlines
    only) sees one value, or the reverse: such a file is refused."""
    rel = make_release(tmp_path)
    cfg = rel / "venv/r1/pyvenv.cfg"
    home = f"{rel}/python/{ALIAS}/bin"
    text = cfg.read_text().replace(
        f"home = {home}\n", f"home = {home}{joint}home = /opt/elsewhere/bin\n"
    )
    cfg.write_bytes(text.encode("utf-8"))
    only(problems(rel), "venv/r1: pyvenv.cfg", "недопустимые символы")


def test_pyvenv_cfg_crlf_accepted(tmp_path: Path, deps: dict[Path, LibDeps]) -> None:
    rel = make_release(tmp_path)
    cfg = rel / "venv/r1/pyvenv.cfg"
    cfg.write_bytes(cfg.read_bytes().replace(b"\n", b"\r\n").replace(b"= ", b"=\t"))
    assert problems(rel) == []


@pytest.mark.parametrize("key", ["home", "include-system-site-packages"])
def test_pyvenv_duplicate_key_refused(
    tmp_path: Path, deps: dict[Path, LibDeps], key: str
) -> None:
    """getpath takes the first `home`, site.py the last: two are refused."""
    rel = make_release(tmp_path)
    cfg = rel / "venv/r1/pyvenv.cfg"
    value = f"{rel}/python/{ALIAS}/bin" if key == "home" else "false"
    cfg.write_text(cfg.read_text() + f"{key.upper()} = {value}\n")
    only(problems(rel), "venv/r1", "ровно по 1")


@pytest.mark.parametrize("name", ["pyvenv.cfg", "PyVenv.CFG"])
def test_pyvenv_cfg_in_bin_refused(
    tmp_path: Path, deps: dict[Path, LibDeps], name: str
) -> None:
    """F4: `site.py` reads `<exe dir>/pyvenv.cfg` before the venv's own."""
    rel = make_release(tmp_path)
    _write(rel / "venv/r1/bin" / name, b"include-system-site-packages = true\n", 0o644)
    only(problems(rel), f"venv/r1/bin/{name}", "pyvenv.cfg")


def test_environments_on_different_pythons_refused(
    tmp_path: Path, deps: dict[Path, LibDeps]
) -> None:
    rel = make_release(tmp_path, {"r1": ("tool",), "r2": ()})
    other = rel / "python" / f"cpython-3.12.14-{TRIPLE}"
    _write(other / "bin" / "python3.12", MACHO)
    (other / "lib" / "python3.12" / "lib-dynload").mkdir(parents=True)
    _cfg_r2 = rel / "venv/r2/pyvenv.cfg"
    _cfg_r2.write_text(PYVENV.format(home=f"{other}/bin", repo="r2"))
    expected = {"r1": frozenset({"tool"}), "r2": frozenset()}
    found = static_problems(rel, PLATFORM, expected)
    assert any("разные Python" in p for p in found), found


def test_python_of_another_platform_refused(
    tmp_path: Path, deps: dict[Path, LibDeps]
) -> None:
    rel = make_release(tmp_path)
    found = static_problems(rel, "linux-x86_64", EXPECTED)
    assert any("не для платформы" in p for p in found), found


# ---------- (в) native files ----------


def test_native_dep_outside_refused(tmp_path: Path, deps: dict[Path, LibDeps]) -> None:
    rel = make_release(tmp_path)
    deps[rel / "venv/r1/lib/python3.12/site-packages/_x.so"] = LibDeps(
        ("/opt/homebrew/lib/x.dylib",), ()
    )
    _write(rel / "venv/r1/lib/python3.12/site-packages/_x.so", MACHO, 0o644)
    only(problems(rel), "site-packages/_x.so", "/opt/homebrew/lib/x.dylib")


def test_rpath_origin_inside_accepted(
    tmp_path: Path, deps: dict[Path, LibDeps]
) -> None:
    """ELF: `$ORIGIN` rpaths inside the release find the soname there."""
    rel = make_release(tmp_path)
    site = rel / "venv/r1/lib/python3.12/site-packages"
    _write(site / "pkg.libs" / "libz-1.so", ELF, 0o644)
    _write(site / "pkg" / "_ext.so", ELF, 0o644)
    deps[site / "pkg" / "_ext.so"] = LibDeps(
        ("libz-1.so", "$ORIGIN/../pkg.libs/libz-1.so"), ("$ORIGIN/../pkg.libs",)
    )
    found = static_problems(rel, "linux-x86_64", EXPECTED)
    assert [p for p in found if "_ext.so" in p] == []
    deps[site / "pkg" / "_ext.so"] = LibDeps(("libz-1.so",), ())
    found = static_problems(rel, "linux-x86_64", EXPECTED)
    assert [p for p in found if "_ext.so" in p] != []  # not in the loader's dirs


def test_macho_rpath_and_loader_path_accepted(
    tmp_path: Path, deps: dict[Path, LibDeps]
) -> None:
    rel = make_release(tmp_path)
    dyn = rel / "python" / KEY / "lib/python3.12/lib-dynload"
    _write(dyn / "_tkinter.so", MACHO, 0o644)
    _write(rel / "python" / KEY / "lib/libtcl9.0.dylib", MACHO, 0o644)
    deps[dyn / "_tkinter.so"] = LibDeps(
        ("@rpath/libtcl9.0.dylib", "@loader_path/../../libtcl9.0.dylib"),
        ("@loader_path/../..",),
    )
    assert problems(rel) == []


def test_executable_path_in_python_bin_accepted(
    tmp_path: Path, deps: dict[Path, LibDeps]
) -> None:
    rel = make_release(tmp_path)
    deps[rel / "python" / KEY / "bin/python3.12"] = LibDeps(
        ("@rpath/libpython3.12.dylib",), ("@executable_path/../lib",)
    )
    assert problems(rel) == []


@pytest.mark.parametrize(
    "libdeps",
    [
        LibDeps((), ("@executable_path",)),
        LibDeps((), ("@executable_path/..",)),
        LibDeps(("@executable_path/../site-packages/_x.so",), ()),
    ],
)
def test_executable_path_outside_python_bin_refused(
    tmp_path: Path, deps: dict[Path, LibDeps], libdeps: LibDeps
) -> None:
    """F9: dyld binds `@executable_path` to the main program, not the file;
    only the interpreter's own directory may use it (each path here exists)."""
    rel = make_release(tmp_path)
    so = rel / "venv/r1/lib/python3.12/site-packages/_x.so"
    _write(so, MACHO, 0o644)
    deps[so] = libdeps
    only(problems(rel), "_x.so", "@executable_path только у python")


@pytest.mark.parametrize(
    "libdeps,needle",
    [
        (LibDeps((), (), None, ("DYLD_LIBRARY_PATH=/tmp",)), "LC_DYLD_ENVIRONMENT"),
        (LibDeps((), (), "/opt/build/ld.so"), "загрузчик"),
        (LibDeps((), ("/opt/homebrew/lib",)), "путь поиска"),
        (LibDeps((), ("",)), "путь поиска"),
        (LibDeps((), ("$LIB/x",)), "путь поиска"),
        (LibDeps((), ("lib",)), "путь поиска"),
        (LibDeps(("@rpath/libmissing.dylib",), ("@loader_path",)), "не найдена"),
        (LibDeps(("@loader_path/../../../../../../x.dylib",), ()), "escapes"),
        (LibDeps(("libfoo.dylib",), ()), "libfoo.dylib"),
    ],
)
def test_native_rules_refuse(
    tmp_path: Path, deps: dict[Path, LibDeps], libdeps: LibDeps, needle: str
) -> None:
    rel = make_release(tmp_path)
    so = rel / "venv/r1/lib/python3.12/site-packages/_x.so"
    _write(so, MACHO, 0o644)
    deps[so] = libdeps
    only(problems(rel), "_x.so", needle)


def test_rpath_inside_release_by_absolute_path_accepted(
    tmp_path: Path, deps: dict[Path, LibDeps]
) -> None:
    rel = make_release(tmp_path)
    so = rel / "venv/r1/lib/python3.12/site-packages/_x.so"
    _write(so, MACHO, 0o644)
    deps[so] = LibDeps(
        ("@rpath/libpython3.12.dylib", f"{rel}/python/{ALIAS}/lib/libpython3.12.dylib"),
        (f"{rel}/python/{ALIAS}/lib",),
    )
    assert problems(rel) == []


def test_inspection_failure_is_a_problem(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """F11: `linked_libraries` raising is a line, never an exception."""
    rel = make_release(tmp_path)

    def broken(path: Path) -> LibDeps:
        raise StepFailed("cli", "otool не установлен")

    monkeypatch.setattr(plat, "linked_libraries", broken)
    found = problems(rel)
    assert len(found) == 2 and all("otool не установлен" in p for p in found)


# ---------- (г) links, (д) entrypoints, (е) .pth ----------


def test_escaping_link_refused(tmp_path: Path, deps: dict[Path, LibDeps]) -> None:
    rel = make_release(tmp_path)
    os.symlink("/etc/passwd", rel / "python" / KEY / "lib" / "out")
    found = problems(rel)
    assert any("python/" in p and "out" in p for p in found), found


def test_missing_expected_entrypoint_refused(
    tmp_path: Path, deps: dict[Path, LibDeps]
) -> None:
    rel = make_release(tmp_path)
    found = problems(rel, {"r1": frozenset({"tool", "other"})})
    only(found, "venv/r1/bin/other", "нет ожидаемого entrypoint")


def test_entrypoint_in_wrong_case_refused(
    tmp_path: Path, deps: dict[Path, LibDeps]
) -> None:
    """F12: a case-only match is not the expected name (APFS opens it)."""
    rel = make_release(tmp_path, {"r1": ("Tool",)})
    only(problems(rel), "venv/r1/bin/tool", "регистре")


def test_entrypoint_must_be_an_executable_script(
    tmp_path: Path, deps: dict[Path, LibDeps]
) -> None:
    rel = make_release(tmp_path)
    (rel / "venv/r1/bin/tool").chmod(0o644)
    only(problems(rel), "venv/r1/bin/tool", "не исполняемый")
    _write(rel / "venv/r1/bin/tool", MACHO)
    only(problems(rel), "venv/r1/bin/tool", "не скрипт")


def test_stray_pth_refused(tmp_path: Path, deps: dict[Path, LibDeps]) -> None:
    rel = make_release(tmp_path)
    (rel / "venv/r1/lib/python3.12/site-packages/evil.pth").write_text("import os\n")
    only(problems(rel), "evil.pth")


def test_untrusted_pth_walk_is_a_problem(
    tmp_path: Path, deps: dict[Path, LibDeps]
) -> None:
    """F11: `pth_problems` raising `TreeError` becomes a line."""
    rel = make_release(tmp_path)
    (rel / "venv/r1/lib/python3.12/site-packages/x.egg-info").mkdir()
    only(problems(rel), "venv/r1", "egg")


def test_unexpected_environment_refused(
    tmp_path: Path, deps: dict[Path, LibDeps]
) -> None:
    rel = make_release(tmp_path, {"r1": ("tool",), "stray": ()})
    only(problems(rel), "venv/stray", "вне профиля")


def test_missing_environment_refused(tmp_path: Path, deps: dict[Path, LibDeps]) -> None:
    rel = make_release(tmp_path, {})
    found = problems(rel)
    assert any(p.startswith("venv") for p in found), found


# ---------- (ё) sitecustomize, ._pth, python3XY.zip ----------


def _plant(path: Path, name: str) -> None:
    if name.endswith("/"):
        (path / name.rstrip("/")).mkdir()
    else:
        _write(path / name, b"print('x')\n", 0o644)


@pytest.mark.parametrize(
    "name",
    [
        "sitecustomize.py",
        "sitecustomize.pyc",
        "sitecustomize.cpython-312-darwin.so",
        "sitecustomize/",
        "SiteCustomize.py",
    ],
)
def test_sitecustomize_refused(
    tmp_path: Path, deps: dict[Path, LibDeps], name: str
) -> None:
    rel = make_release(tmp_path)
    _plant(rel / "venv/r1/lib/python3.12/site-packages", name)
    only(problems(rel), name.rstrip("/"), "sitecustomize")


def test_sitecustomize_lookalikes_allowed(
    tmp_path: Path, deps: dict[Path, LibDeps]
) -> None:
    rel = make_release(tmp_path)
    site = rel / "venv/r1/lib/python3.12/site-packages"
    _plant(site, "sitecustomize_helper.py")
    (site / "__pycache__").mkdir()
    _plant(site / "__pycache__", "sitecustomize.cpython-312.pyc")  # no source
    assert problems(rel) == []


SYS_PATH_DIRS: Mapping[str, str] = {
    "venv-site-packages": "venv/r1/lib/python3.12/site-packages",
    "python-stdlib": f"python/{KEY}/lib/python3.12",
    "python-lib-dynload": f"python/{KEY}/lib/python3.12/lib-dynload",
}


@pytest.mark.parametrize("where", list(SYS_PATH_DIRS))
def test_sitecustomize_refused_everywhere_on_sys_path(
    tmp_path: Path, deps: dict[Path, LibDeps], where: str
) -> None:
    rel = make_release(tmp_path)
    _plant(rel / SYS_PATH_DIRS[where], "sitecustomize.py")
    only(problems(rel), f"{SYS_PATH_DIRS[where]}/sitecustomize.py")


def test_pyvenv_cfg_lone_cr_refused(tmp_path: Path, deps: dict[Path, LibDeps]) -> None:
    """Review round 1, F1: a bare `\\r` is one line for getpath and us but two
    for `site.py` (universal newlines), which keeps the last value: measured,
    this turned the base site-packages on under `-I`."""
    rel = make_release(tmp_path)
    cfg = rel / "venv/r1/pyvenv.cfg"
    cfg.write_bytes(
        cfg.read_bytes() + b"prompt = r1\rinclude-system-site-packages = true\n"
    )
    only(problems(rel), "venv/r1: pyvenv.cfg", "одиночный")


#: Each refused/absent `STARTUP_FILES` entry, at its place in the fixture.
REFUSED_STARTUP = [e for e in boundary.STARTUP_FILES if e.rule in ("refused", "absent")]


def _startup_path(rel: Path, entry: boundary.StartupFile) -> Path:
    values = {"venv": "venv/r1", "py": f"python/{KEY}", "minor": "12"}
    values["stdlib"] = f"python/{KEY}/lib/python3.12"
    name = entry.name.format(**values).replace("*", "python")
    return rel / entry.where.format(**values) / name


@pytest.mark.parametrize(
    "entry", REFUSED_STARTUP, ids=[f"{e.where}/{e.name}" for e in REFUSED_STARTUP]
)
@pytest.mark.parametrize("kind", ["file", "dir"])
def test_every_refused_startup_file_is_refused(
    tmp_path: Path, deps: dict[Path, LibDeps], entry: boundary.StartupFile, kind: str
) -> None:
    rel = make_release(tmp_path)
    path = _startup_path(rel, entry)
    if kind == "dir":
        (path / "Setup.local").parent.mkdir(parents=True)
        (path / "Setup.local").write_text("")
    else:
        _write(path, b"../../evil\n", 0o644)
    only(problems(rel), str(path.relative_to(rel)), "запрещён")


def test_startup_table_names_the_probed_build_markers() -> None:
    names = {(e.where, e.name, e.rule) for e in boundary.STARTUP_FILES}
    assert ("{py}/bin", "pybuilddir.txt", "refused") in names
    assert ("{py}/bin", "modules", "refused") in names
    assert ("{venv}/bin", "pyvenv.cfg", "refused") in names
    assert ("{py}/lib", "python3{minor}.zip", "absent") in names


@pytest.mark.parametrize("name", ["os.py", "lib-dynload"])
def test_required_startup_landmark_missing_refused(
    tmp_path: Path, deps: dict[Path, LibDeps], name: str
) -> None:
    rel = make_release(tmp_path)
    path = rel / "python" / KEY / "lib/python3.12" / name
    path.rmdir() if path.is_dir() else path.unlink()
    found = problems(rel)
    assert found and any(name in p for p in found), found


def test_base_bin_allowlist(tmp_path: Path, deps: dict[Path, LibDeps]) -> None:
    """Every measured name of uv's managed CPython 3.12 `bin` passes; any other
    name is refused (the class of startup redirections, review round 1)."""
    rel = make_release(tmp_path)
    bin_dir = rel / "python" / KEY / "bin"
    for name in sorted(boundary.base_bin_names("12")):
        if not os.path.lexists(bin_dir / name):
            _write(bin_dir / name, b"#!/bin/sh\n")
    assert problems(rel) == []
    _write(bin_dir / "python3.12-gdb.py", b"", 0o644)
    only(problems(rel), "bin/python3.12-gdb.py", "не из раскладки")


PTH_PLACES: Mapping[str, str] = {
    "venv-bin": "venv/r1/bin/python._pth",
    "python-bin": f"python/{KEY}/bin/python3.12._pth",
    "python-lib": f"python/{KEY}/lib/libpython3.12.dylib._pth",
}


@pytest.mark.parametrize("where", list(PTH_PLACES))
def test_pth_underscore_file_refused(
    tmp_path: Path, deps: dict[Path, LibDeps], where: str
) -> None:
    """(ё): `<exe>._pth` as measured (N5); the code refuses every `*._pth`."""
    rel = make_release(tmp_path)
    _write(rel / PTH_PLACES[where], b"bogus\n", 0o644)
    only(problems(rel), PTH_PLACES[where], "._pth")


@pytest.mark.parametrize("name", ["python312.zip", "Python312.ZIP"])
def test_python_zip_present_refused(
    tmp_path: Path, deps: dict[Path, LibDeps], name: str
) -> None:
    """F5: the zip is on `sys.path`; only its absence is allowed."""
    rel = make_release(tmp_path)
    _write(rel / "python" / KEY / "lib" / name, b"PK\x05\x06" + b"\0" * 18, 0o644)
    only(problems(rel), name)


# ---------- expected_entrypoints ----------


@pytest.mark.parametrize(
    "text,want",
    [
        (
            (
                '[project]\nname="a"\n[project.scripts]\nx="a:m"\n'
                "[tool.uv]\npackage=false\n"
            ),
            set(),
        ),
        ('[project]\nname="a"\n[project.scripts]\nx="a:m"\n', set()),
        (
            (
                '[project]\nname="a"\n[project.scripts]\nx="a:m"\ny="a:n"\n'
                "[build-system]\nrequires=[]\n"
            ),
            {"x", "y"},
        ),
        (
            (
                '[project]\nname="a"\n[project.scripts]\nx="a:m"\n'
                "[tool.uv]\npackage=true\n"
            ),
            {"x"},
        ),
        ('[project]\nname="a"\n[build-system]\nrequires=[]\n', set()),
        (
            (
                '[project]\nname="a"\n[project.gui-scripts]\ng="a:m"\n'
                "[build-system]\nrequires=[]\n"
            ),
            set(),
        ),
    ],
)
def test_expected_entrypoints_package_rules(
    tmp_path: Path, text: str, want: set[str]
) -> None:
    (tmp_path / "pyproject.toml").write_text(text)
    assert expected_entrypoints(tmp_path) == want


@pytest.mark.parametrize(
    "text",
    [
        '[project]\nname="a"\ndynamic=["scripts"]\n[build-system]\nrequires=[]\n',
        '[project]\nname="a"\ndynamic=1\n[build-system]\nrequires=[]\n',
        '[project]\nname="a"\ndynamic="scripts"\n[build-system]\nrequires=[]\n',
        '[project]\nname="a"\n[tool.uv]\npackage="yes"\n',
        (
            '[project]\nname="a"\n[project.scripts]\n"../x"="a:m"\n'
            "[build-system]\nrequires=[]\n"
        ),
        "not toml = = \n",
    ],
)
def test_expected_entrypoints_refusals(tmp_path: Path, text: str) -> None:
    (tmp_path / "pyproject.toml").write_text(text)
    with pytest.raises((ValueError, boundary.TreeError)):
        expected_entrypoints(tmp_path)


def test_expected_entrypoints_missing_file(tmp_path: Path) -> None:
    with pytest.raises(boundary.TreeError):
        expected_entrypoints(tmp_path)


# ---------- transfer check ----------

Run = tuple[str, tuple[str, ...], dict[str, object]]


def sys_path(rel: Path, repo: str = "r1") -> list[str]:
    """The measured `sys.path` (pre-flight §2.4), alias path for lib-dynload;
    `-P` and `-I` both keep the cwd (`""`) off it."""
    path = [
        f"{rel}/python/{KEY}/lib/python312.zip",
        f"{rel}/python/{KEY}/lib/python3.12",
        f"{rel}/python/{ALIAS}/lib/python3.12/lib-dynload",
        f"{rel}/venv/{repo}/lib/python3.12/site-packages",
    ]
    return path


class FakeRunAs:
    """`run_as` stub: the leftover find, the probe and `--help` runs."""

    def __init__(self, rel: Path) -> None:
        self.rel = rel
        self.runs: list[Run] = []
        self.find = RunResult(0, b"", b"", launched=True)
        self.answer: Callable[[str, bool], object] = lambda repo, isolated: [
            sys_path(rel, repo),
            False,
            VERSION,
        ]
        self.probe: dict[bool, RunResult] = {}
        self.help: dict[str, RunResult] = {}

    def __call__(self, user: str, argv: Sequence[str], **kw: object) -> RunResult:
        self.runs.append((user, tuple(argv), kw))
        if argv[0] == "/usr/bin/find":
            return self.find
        if "-c" in argv:
            isolated = "-I" in argv
            if isolated in self.probe:
                return self.probe[isolated]
            repo = Path(argv[0]).parent.parent.name
            out = json.dumps(self.answer(repo, isolated)).encode()
            return RunResult(0, out + b"\n", b"", launched=True)
        return self.help.get(Path(argv[0]).name, RunResult(0, b"usage", b"", True))


@pytest.fixture()
def runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeRunAs:
    fake = FakeRunAs(make_release(tmp_path))
    monkeypatch.setattr(plat, "run_as", fake)
    return fake


def run(
    fake: FakeRunAs, tmp_path: Path, expected: Mapping[str, frozenset[str]] = EXPECTED
) -> TransferResult:
    layout = Layout(tmp_path / "root")
    return transfer_check(fake.rel, layout, user="df-build", expected=expected)


def test_transfer_check_passes_and_records_python_declared(
    runner: FakeRunAs, tmp_path: Path
) -> None:
    result = run(runner, tmp_path)
    assert result == TransferResult([], {"r1": VERSION})
    home = Layout(tmp_path / "root").build_home
    python = f"{runner.rel}/venv/r1/bin/python"
    assert [argv for _u, argv, _k in runner.runs] == [
        (
            "/usr/bin/find",
            str(home),
            "-mindepth",
            "1",
            "-maxdepth",
            "1",
            "(",
            "-name",
            "tmp",
            "-o",
            "-name",
            "cache",
            ")",
            "-print",
        ),
        (python, "-P", "-c", boundary.SYS_PATH_PROBE),
        (python, "-I", "-c", boundary.SYS_PATH_PROBE),
        (f"{runner.rel}/venv/r1/bin/tool", "--help"),
    ]
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    for user, _argv, kw in runner.runs:
        assert user == "df-build"
        assert kw == {"env": env, "cwd": home, "network": False, "timeout": 60.0}


def test_direct_shebang_script_is_run(runner: FakeRunAs, tmp_path: Path) -> None:
    """A direct `#!<release>/…/python` script runs; `--help` code 1 refuses."""
    runner.help["tool"] = RunResult(1, b"", b"ModuleNotFoundError: anyio", True)
    result = run(runner, tmp_path)
    only(result.problems, "tool --help", "код 1", "anyio")


def test_trampoline_script_is_run(runner: FakeRunAs, tmp_path: Path) -> None:
    write_script(runner.rel, "r1", "tool", "trampoline")
    run(runner, tmp_path)
    assert runner.runs[-1][1] == (f"{runner.rel}/venv/r1/bin/tool", "--help")


@pytest.mark.parametrize(
    "outcome,needle",
    [
        (RunResult(-9, b"", b"", True, timed_out=True), "срок 60 с"),
        (RunResult(-9, b"", b"", True, stdout_truncated=True), "stdout"),
        (RunResult(127, b"", b"sudo: x", False), "инфраструктура"),
    ],
)
def test_help_outcomes_are_problems(
    runner: FakeRunAs, tmp_path: Path, outcome: RunResult, needle: str
) -> None:
    runner.help["tool"] = outcome
    only(run(runner, tmp_path).problems, "tool --help", needle)


def test_launch_failure_is_problem_not_pass(runner: FakeRunAs, tmp_path: Path) -> None:
    failed = RunResult(127, b"", b"sandbox-exec: denied", launched=False)
    runner.probe = {False: failed, True: failed}
    result = run(runner, tmp_path)
    assert len(result.problems) == 2
    assert all(p.startswith("инфраструктура:") for p in result.problems)
    assert result.python_declared == {}


def test_runtime_sys_path_outside_refused(runner: FakeRunAs, tmp_path: Path) -> None:
    runner.answer = lambda repo, iso: [
        [*sys_path(runner.rel, repo), "/Users/x/site"],
        False,
        VERSION,
    ]
    found = run(runner, tmp_path).problems
    assert len(found) == 2 and all("/Users/x/site" in p for p in found), found


@pytest.mark.parametrize(
    "extra",
    [
        f"python/{KEY}/lib/python3.12/site-packages",  # pip's, not the venv's
        "venv/r2/lib/python3.12/site-packages",  # F13: another member's
        "venv/r1/bin",
        "venv/r1/lib/python3.12/site-packages/../../python3.12",
    ],
)
def test_runtime_sys_path_outside_checked_dirs_refused(
    runner: FakeRunAs, tmp_path: Path, extra: str
) -> None:
    """(ж): inside the release, but not a directory (ё) checked for it."""
    add_venv(runner.rel, "r2")
    (runner.rel / "python" / KEY / "lib/python3.12/site-packages").mkdir()
    runner.answer = lambda repo, iso: [
        [*sys_path(runner.rel, repo), f"{runner.rel}/{extra}"],
        False,
        VERSION,
    ]
    found = run(runner, tmp_path).problems
    assert len(found) == 2 and all("не проверенный каталог" in p for p in found)


@pytest.mark.parametrize("item", ["relative/dir", "{rel}/venv/r1/missing"])
def test_runtime_sys_path_unresolvable_refused(
    runner: FakeRunAs, tmp_path: Path, item: str
) -> None:
    runner.answer = lambda repo, iso: [
        [*sys_path(runner.rel, repo), item.format(rel=runner.rel)],
        False,
        VERSION,
    ]
    assert len(run(runner, tmp_path).problems) == 2


def test_present_zip_on_sys_path_refused(runner: FakeRunAs, tmp_path: Path) -> None:
    """F5: only the absent zip passes (ж); a present one resolves elsewhere."""
    _write(runner.rel / "python" / KEY / "lib" / "python312.zip", b"PK", 0o644)
    found = run(runner, tmp_path).problems
    assert len(found) == 2 and all("python312.zip" in p for p in found), found


@pytest.mark.parametrize("isolated", [False, True])
def test_empty_sys_path_element_refused(
    runner: FakeRunAs, tmp_path: Path, isolated: bool
) -> None:
    """`-P`/`-I` keep the cwd (writable `build_home`) off `sys.path`; an empty
    element means it is on it, and a module there could forge the answer."""
    runner.answer = lambda repo, iso: [
        ["", *sys_path(runner.rel, repo)] if iso == isolated else sys_path(runner.rel),
        False,
        VERSION,
    ]
    only(run(runner, tmp_path).problems, "sys.path ''")


@pytest.mark.parametrize("item", ["{rel}/venv/r1/x\x00y", "{rel}/venv/" + "a" * 300])
def test_hostile_sys_path_element_is_a_problem(
    runner: FakeRunAs, tmp_path: Path, item: str
) -> None:
    """Review round 1, F3: NUL or ENAMETOOLONG is a line, never an exception."""
    runner.answer = lambda repo, iso: [
        [*sys_path(runner.rel, repo), item.format(rel=runner.rel)],
        False,
        VERSION,
    ]
    found = run(runner, tmp_path).problems
    assert len(found) == 2 and all("sys.path" in p for p in found), found


def test_enable_user_site_refused(runner: FakeRunAs, tmp_path: Path) -> None:
    runner.answer = lambda repo, iso: [
        sys_path(runner.rel, repo),
        not iso,
        VERSION,
    ]
    only(run(runner, tmp_path).problems, "ENABLE_USER_SITE=True")


@pytest.mark.parametrize(
    "stdout",
    [b"not json", b"[[], false]", b'[["/x"], "no", "v"]', b'[[1], false, "v"]'],
)
def test_unparsable_probe_refused(
    runner: FakeRunAs, tmp_path: Path, stdout: bytes
) -> None:
    runner.probe = {iso: RunResult(0, stdout, b"", True) for iso in (False, True)}
    result = run(runner, tmp_path)
    assert len(result.problems) == 2 and result.python_declared == {}


def test_find_failure_is_infrastructure_and_nothing_runs(
    runner: FakeRunAs, tmp_path: Path
) -> None:
    runner.find = RunResult(1, b"", b"find: permission denied", True)
    result = run(runner, tmp_path)
    only(result.problems, "инфраструктура", "код 1")
    assert len(runner.runs) == 1


def test_leftovers_stop_before_any_run(runner: FakeRunAs, tmp_path: Path) -> None:
    home = Layout(tmp_path / "root").build_home
    runner.find = RunResult(0, f"{home}/cache\n".encode(), b"", True)
    result = run(runner, tmp_path)
    only(result.problems, "остатки build_home")
    assert len(runner.runs) == 1


def _refuses_leftovers(
    fake_layout: FakeLayout, check: Callable[..., TransferResult]
) -> bool:
    """The leftover rule observed through the real `find` as `df-build`:
    a refusal, `build_home/cache` still there, no run but the find."""
    lay = fake_layout.layout
    (lay.build_home / "cache" / "x").mkdir(parents=True, exist_ok=True)
    fake_layout.clear()
    result = check(lay.root / "rel", lay, user="df-build", expected=EXPECTED)
    runs = [e for e in fake_layout.events if e[0] == "run_as"]
    return (
        any("остатки build_home" in p for p in result.problems)
        and (lay.build_home / "cache" / "x").is_dir()
        and [argv[0] for _k, _u, argv in runs] == ["/usr/bin/find"]  # type: ignore[index]
    )


def test_transfer_check_refuses_build_home_leftovers(
    fake_layout: FakeLayout,
) -> None:
    """S1-R9-N1 / R33.F3: real `find` through the fake `run_as`; the twin
    «transfer_check со скрытой очисткой build_home» must fail the same rule."""
    fake_layout.make_dirs()

    def hidden_cleanup(*args: object, **kw: object) -> TransferResult:
        lay = args[1]
        assert isinstance(lay, Layout)
        plat.cleanup_build_home(lay, "cache")
        return transfer_check(*args, **kw)  # type: ignore[arg-type]

    assert _refuses_leftovers(fake_layout, transfer_check)
    assert not _refuses_leftovers(fake_layout, hidden_cleanup)
