"""Валидация пинов секции `[tools.*]` (приёмка devtools#105 от prograph-vault).

Обе копии инструмента обходили только `cores` и `apps`, поэтому пины пяти
инструментов не проверял никто — и это было незаметно, потому что детектор до
секции не доходил.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "check-release-drift.py"


@pytest.fixture(scope="module")
def drift() -> Any:
    spec = importlib.util.spec_from_file_location("release_drift", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["release_drift"] = module
    spec.loader.exec_module(module)
    return module


def _tool(**over) -> dict:
    meta = {
        "package_name": "robin-toolkit",
        "repo_url": "git@github.com:o/robin-toolkit.git",
        "git_dir": "robin-toolkit",
        "pyproject_path": "robin-toolkit/pyproject.toml",
        "tag_pattern": "v*",
        "install": "git-sha",
        "publish": "none",
        "sha": "3e871628abe0",
    }
    meta.update(over)
    return meta


def _repo(tmp_path: Path, name: str) -> Path:
    d = tmp_path / name
    (d / ".git").mkdir(parents=True)
    return d


def test_tools_section_is_walked(drift, tmp_path, monkeypatch, capsys) -> None:
    """Проверяется ПОВЕДЕНИЕ main(), а не текст исходника: прежняя версия
    ассертила подстроку в `inspect.getsource` и осталась бы зелёной при любой
    поломке обхода (ревью PR #139)."""
    manifest = tmp_path / "m.toml"
    manifest.write_text(
        '''schema_version = "1"
[tools.robin-toolkit]
package_name = "robin-toolkit"
repo_url = "git@github.com:o/robin-toolkit.git"
git_dir = "robin-toolkit"
pyproject_path = "robin-toolkit/pyproject.toml"
tag_pattern = "v*"
install = "git-sha"
publish = "none"
''', encoding="utf-8")
    _repo(tmp_path, "robin-toolkit")
    monkeypatch.setattr(
        sys, "argv",
        ["check-release-drift.py", "--workspace", str(tmp_path),
         "--manifest", str(manifest), "--json"],
    )
    drift.main()
    import json as _json
    payload = _json.loads(capsys.readouterr().out)
    kinds = {f["kind"] for f in payload["findings"]
             if f["component"] == "robin-toolkit"}
    assert kinds, f"секция [tools.*] не обойдена: {payload}"
    assert "irreproducible" in kinds, f"пин без sha не пойман: {kinds}"


def test_a_pin_without_sha_is_an_error(drift, tmp_path) -> None:
    """Смысл просьбы: плавающий HEAD у инструмента так же невоспроизводим, как
    у ядра."""
    _repo(tmp_path, "robin-toolkit")
    found = drift.check_component("robin-toolkit", _tool(sha=""), tmp_path)
    assert any(x["kind"] == "irreproducible" and x["severity"] == "error"
               for x in found), found


def test_apps_keep_the_warning_when_pyproject_disappears(drift, tmp_path) -> None:
    """Первая версия правки вешала понижение на `publish != "pypi"`, а им
    помечены ВСЕ 15 apps — настоящие Python-пакеты. Пропавший у них pyproject
    обязан оставаться предупреждением (ревью PR #139)."""
    _repo(tmp_path, "robin-toolkit")
    found = drift.check_component("maestro", _tool(), tmp_path, "apps")
    vers = [x for x in found if x["kind"] == "no_pyproject_version"]
    assert vers and vers[0]["severity"] == "warn", vers


def test_a_pinned_tool_without_pyproject_is_info_not_warn(drift, tmp_path) -> None:
    """Инструмент, который ничего не публикует и не является Python-пакетом
    (Elixir, TypeScript, Obsidian-хранилище — так и записано в манифесте), не
    имеет версии релиза, с которой можно разойтись. Это `info`, иначе включение
    секции добавило бы четыре предупреждения, ни одно из которых не дрейф."""
    _repo(tmp_path, "robin-toolkit")
    found = drift.check_component("robin-toolkit", _tool(), tmp_path, "tools")
    vers = [x for x in found if x["kind"] == "no_pyproject_version"]
    assert vers, "факт остаётся видимым"
    assert vers[0]["severity"] == "info", vers


def test_a_publishing_component_without_pyproject_still_warns(drift, tmp_path) -> None:
    """Антирегрессия: для публикуемого компонента пропавший pyproject —
    по-прежнему предупреждение, а не тишина."""
    _repo(tmp_path, "robin-toolkit")
    found = drift.check_component(
        "core", _tool(publish="pypi", install="pypi"), tmp_path, "cores")
    vers = [x for x in found if x["kind"] == "no_pyproject_version"]
    assert vers and vers[0]["severity"] == "warn", vers


def test_a_publishing_tool_keeps_the_warning(drift, tmp_path) -> None:
    """Понижение — пересечение трёх условий, и `publish` среди них: инструмент,
    который публикуется, обязан остаться предупреждением, даже будучи в секции
    tools (ревью PR #139, круг 2)."""
    _repo(tmp_path, "robin-toolkit")
    found = drift.check_component(
        "robin-toolkit", _tool(publish="pypi"), tmp_path, "tools")
    vers = [x for x in found if x["kind"] == "no_pyproject_version"]
    assert vers and vers[0]["severity"] == "warn", vers
