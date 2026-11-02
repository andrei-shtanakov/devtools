"""Транспорт источников доверия строит только `act_trust.history_transport` (С-1, C6).

ДЕТЕКТОР, А НЕ ДОКАЗАТЕЛЬСТВО ПОЛНОТЫ. Сканер ищет в `governance/` построение
`AnonTransport` вне фабрики в трёх формах: прямой вызов, атрибутный
(`act_anon.AnonTransport(...)`) и через алиас `from … import AnonTransport as T`.
Присваивания и прочие обходы он не ловит и ловить не обязан: транспорт, построенный
где угодно ещё, до решения не доходит — `load_trust` строит свой. Реестр
`WIRING_SITES` — известные боевые места сборки зависимостей решения; тест прогоняет
каждое через `load_trust` с ключом истории (перекрёстная проверка, не полнота).
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("steward")

from governance import act_status, act_trust  # noqa: E402
from governance.act_time import Unavailable  # noqa: E402
from tests.test_act_trust import (  # noqa: E402
    AP,
    J1,
    TOKEN,
    Journal,
    _perms,
    _self_reported,
    _Tokens,
    _Wire,
    as_credential,
    trust_routes,
)

ROOT = Path(__file__).resolve().parent.parent
ENV = {"REVIEW_GH_CONFIG_DIR": "/x/review", "MERGE_GH_CONFIG_DIR": "/x/merge"}
Site = Callable[[Path], tuple[act_trust.Readers, tuple[act_trust.Credential, ...]]]
_CTOR = "AnonTransport"
#: Единственное разрешённое место построения — исключается поимённо.
_FACTORY = frozenset({"governance/act_trust.py::history_transport"})


def _status_site(tmp_path: Path):
    state = SimpleNamespace(
        repo_slug="o/p", ws_id="w", target_dir=str(tmp_path), run_id="r"
    )
    env = {
        **ENV,
        "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
        "XDG_STATE_HOME": str(tmp_path),
    }
    deps = act_status.real_deps(state, env=env)
    return deps.readers, deps.creds


#: `<файл>::<функция>` → сборка зависимостей решения этим местом. Task 15 добавляет
#: обязательную строку `governance/act_premerge.py::real_deps`.
WIRING_SITES: dict[str, Site] = {
    "governance/act_status.py::real_deps": _status_site,
}


def _constructions(rel: str, text: str) -> set[str]:
    """Места построения `AnonTransport` трёх распознаваемых форм в одном модуле."""
    tree = ast.parse(text)
    names = {_CTOR} | {
        a.asname
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for a in node.names
        if a.name == _CTOR and a.asname
    }
    found: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            f = node.func if isinstance(node, ast.Call) else None
            if (isinstance(f, ast.Name) and f.id in names) or (
                isinstance(f, ast.Attribute) and f.attr == _CTOR
            ):
                found.add(f"{rel}::{fn.name}")
    return found - _FACTORY


@pytest.mark.parametrize(
    "src",
    [
        "from governance.act_anon import AnonTransport\ndef f():\n    AnonTransport()\n",
        "from governance import act_anon\ndef f():\n    act_anon.AnonTransport(token='t')\n",
        "from governance.act_anon import AnonTransport as T\ndef f():\n    T()\n",
    ],
    ids=["direct", "attribute", "import_alias"],
)
def test_detector_sees_claimed_forms(src: str) -> None:
    assert _constructions("governance/x.py", src) == {"governance/x.py::f"}


def test_detector_excludes_only_the_factory() -> None:
    src = (
        "from governance.act_anon import AnonTransport\n"
        "def history_transport(b):\n    return AnonTransport()\n"
        "def default_readers():\n    return AnonTransport()\n"
    )
    assert _constructions("governance/act_trust.py", src) == {
        "governance/act_trust.py::default_readers"
    }


def test_no_trust_transport_outside_factory() -> None:
    found = {
        site
        for path in sorted((ROOT / "governance").rglob("*.py"))
        if path.name != "act_anon.py"
        for site in _constructions(
            path.relative_to(ROOT).as_posix(), path.read_text("utf-8")
        )
    }
    assert found == set()


@pytest.mark.parametrize("site", sorted(WIRING_SITES))
def test_wiring_site_decides_with_history_token(
    monkeypatch, tmp_path: Path, site: str
) -> None:
    """Сборка места проходит закрытый набор форм credentials, а токен истории с `push`
    на источнике даёт нарушение периметра, а не решение."""
    with_key = act_trust.Binding(
        "github.com",
        11,
        "owner/df-trust",
        "main",
        22,
        "public",
        tmp_path / "gh-history",
    )
    routes = _self_reported(trust_routes(None, J1))
    routes[f"repos/{AP}"] = (200, {**routes[f"repos/{AP}"][1], **_perms(True)})
    monkeypatch.setattr(act_trust, "load_binding", lambda *a, **k: with_key)
    monkeypatch.setattr(act_trust, "gh_get", as_credential({}))
    readers, creds = WIRING_SITES[site](tmp_path)
    got = act_trust.load_trust(
        readers,
        with_key,
        Journal(),
        creds=creds,
        open_url=_Wire(routes),
        token_source=_Tokens(TOKEN),
    )
    assert isinstance(got, Unavailable)
    assert got.reason.startswith("нарушение периметра: profile:history на ")


#: Швы ввода-вывода решения: внедряют только тесты (ревью Task 11c, Minor 6).
_SEAMS = frozenset({"open_url", "token_source"})
_ENTRIES = frozenset({"load_trust", "history_transport"})
#: Единственная боевая передача швов — `load_trust` в свою фабрику.
_SEAM_PASS = frozenset({("governance/act_trust.py::load_trust", "history_transport")})


def _seam_calls(rel: str, text: str) -> set[str]:
    """Вызовы `load_trust`/`history_transport` со швом (`open_url`, `token_source`
    или `**kwargs`) в модуле — в функциях, теле класса и на уровне модуля:
    `<файл>::<функция | <module>>-><вызываемое>`."""
    found: set[str] = set()

    def visit(node: ast.AST, owner: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name)
                continue
            if isinstance(child, ast.Call):
                f = child.func
                name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
                seam = any(k.arg is None or k.arg in _SEAMS for k in child.keywords)
                site = f"{rel}::{owner}"
                if name in _ENTRIES and seam and (site, name) not in _SEAM_PASS:
                    found.add(f"{site}->{name}")
            visit(child, owner)

    visit(ast.parse(text), "<module>")
    return found


@pytest.mark.parametrize(
    ("src", "found"),
    [
        ("def f(b):\n    load_trust(r, b, j, creds=c, open_url=o)\n", True),
        ("def f(b):\n    act_trust.history_transport(b, token_source=t)\n", True),
        ("def f(b, **kw):\n    act_trust.load_trust(r, b, j, **kw)\n", True),
        ("def f(b):\n    act_trust.load_trust(r, b, j, creds=c)\n", False),
        ("def f(b):\n    other(b, open_url=o)\n", False),
        ("load_trust(r, b, j, creds=c, open_url=o)\n", True),
        ("class K:\n    t = history_transport(b, token_source=t)\n", True),
    ],
    ids=[
        "name_open_url",
        "attr_token_source",
        "kwargs",
        "clean",
        "other_callee",
        "module_level",
        "class_body",
    ],
)
def test_seam_detector_sees_claimed_forms(src: str, found: bool) -> None:
    assert bool(_seam_calls("governance/x.py", src)) is found


def test_seam_detector_exempts_only_load_trust_factory_call() -> None:
    src = (
        "def load_trust(b, open_url=None):\n"
        "    return history_transport(b, open_url=open_url)\n"
        "def real_deps(b):\n    return history_transport(b, open_url=1)\n"
    )
    assert _seam_calls("governance/act_trust.py", src) == {
        "governance/act_trust.py::real_deps->history_transport"
    }


def test_no_production_caller_injects_seams() -> None:
    """Боевой вызов со швом подал бы решению поддельные источники доверия."""
    found = {
        site
        for path in sorted((ROOT / "governance").rglob("*.py"))
        for site in _seam_calls(
            path.relative_to(ROOT).as_posix(), path.read_text("utf-8")
        )
    }
    assert found == set()
