from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "check-arch-evidence-freshness.py"


@pytest.fixture(scope="session")
def sensor():
    spec = importlib.util.spec_from_file_location("arch_freshness_sensor", _SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"не удалось загрузить модуль сенсора из {_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["arch_freshness_sensor"] = mod
    spec.loader.exec_module(mod)
    return mod


#: CI ставит `GOVERNANCE_REQUIRED=1` там, где группа governance синхронизирована:
#: скип по её отсутствию (`importorskip("steward")`, «gate-check CLI недоступен
#: … без группы governance») тогда роняет прогон, а не исчезает молча
#: (devtools#493: 219 тестов раннера не исполнялись с 30.08, потому что CI-шаг
#: перечислял модули руками). Сверяется только ПРИЧИНА скипа, не путь файла
#: (ревью #499). Образец — SELFCHECK_REQUIRE_TOOLS.
_GOVERNANCE_REQUIRED = os.environ.get("GOVERNANCE_REQUIRED") == "1"
_GOVERNANCE_SKIP = re.compile(r"steward|группы governance")
_skipped_for_governance: list[str] = []


def _skip_reason(report: pytest.CollectReport | pytest.TestReport) -> str:
    longrepr = report.longrepr
    return longrepr[2] if isinstance(longrepr, tuple) else str(longrepr)


def _note_governance_skip(report: pytest.CollectReport | pytest.TestReport) -> None:
    if (
        _GOVERNANCE_REQUIRED
        and report.skipped
        and _GOVERNANCE_SKIP.search(_skip_reason(report))
    ):
        _skipped_for_governance.append(report.nodeid)


def _arg_path(arg: str) -> tuple[str, ...]:
    """Directory-wise sort key of a command-line test argument (absolute path)."""
    return Path(arg.split("::", 1)[0]).resolve().parts


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    """Keep command-line arguments of one directory adjacent.

    Since pytest 9 a conftest's fixtures belong to the `Package` node that
    first collected it. Arguments like `tests/df/a.py tests/b.py tests/df/c.py`
    re-enter `tests/df` as a NEW node and `c.py` loses every fixture of
    `tests/df/conftest.py` (`fixture 'world' not found`, TODO
    `df-tests-world-fixture-order`). A stable sort by path restores the
    adjacency; the order inside one file is untouched."""
    config = session.config
    config.args = sorted(config.args, key=_arg_path)


def pytest_collectreport(report: pytest.CollectReport) -> None:
    _note_governance_skip(report)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    _note_governance_skip(report)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if _skipped_for_governance:
        names = ", ".join(_skipped_for_governance)
        print(f"\nGOVERNANCE_REQUIRED=1, но группа governance недоступна: {names}")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture(autouse=True)
def _halt_gate_admits(monkeypatch):
    """Стоп-кран (D2) зовёт GitHub из start/verify/reopen; в тестах он по
    умолчанию «пускает», а кейсы стопа подменяют его сами. Без группы
    governance раннер не импортируется — тогда и подменять нечего."""
    try:
        from governance import runner
    except ImportError:
        return
    monkeypatch.setattr(runner, "_HALT_GATE", lambda slug: None)


@pytest.fixture(autouse=True)
def _act_isolation(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Изоляция путей акта (Task 14): привязка корня доверия — несуществующий
    файл (машина разработчика с настоящей `/etc/df/trust.toml` не уводит тесты
    в сеть), `mode_probe` — продукт до миграции (§10 legacy-эпоха, тот же исход
    admission, что дал бы пустой журнал режима). Тесты `act_*` подменяют
    `mode_probe` сами; без зависимостей модуля подменять нечего.

    Настройки установки (`$XDG_CONFIG_HOME/darkfactory/act.toml`, `real_deps`)
    — из пустого временного каталога: настоящий `~/.config/darkfactory/act.toml`
    машины (например, `act_migration = "draining"` у разработчика миграции) на
    исход набора не влияет; тесты, которым нужны свои настройки, подставляют
    их сами (ревью PR #671)."""
    monkeypatch.setenv(
        "XDG_CONFIG_HOME", str(tmp_path_factory.getbasetemp() / "xdg-config")
    )
    try:
        from governance import act_status, act_trust
    except ImportError:
        return
    monkeypatch.setattr(
        act_trust, "BINDING_PATH", tmp_path_factory.getbasetemp() / "no-trust.toml"
    )
    pre = act_status.LivePolicy(act_trust.PRE_MIGRATION, None, None, None, None)
    monkeypatch.setattr(act_status, "mode_probe", lambda state, deps=None: pre)


#: Каталоги профилей gh владельца — из перечня песочницы (strict, §5.1);
#: `~/` раскрывает загрузчик при чтении, подменённый тестом HOME не ломает его.
_STRICT_GH_DIRS = {
    "agent_gh_config_dir": Path("~/.config/review"),
    "human_gh_config_dir": Path("~/.config/gh"),
}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "no_df_config: без конфига DarkFactory (окружение раннера CI, F7)",
    )


@pytest.fixture(autouse=True)
def _df_config(
    _act_isolation: None,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Конфиг DarkFactory (`$XDG_CONFIG_HOME/darkfactory/config.toml`, план 1
    darkfactory-config, Task 4): strict с нынешними значениями владельца — они
    живут в данных теста (`tests/df_config_fixture.py`), не в коде. Переменные
    оператора, перекрывшие бы конфиг, сняты; тест со своим конфигом пишет его
    в свой каталог и выставляет `XDG_CONFIG_HOME` сам. Маркер `no_df_config` —
    конфига нет вовсе (окружение раннера CI)."""
    from tests.df_config_fixture import (
        ENV_OVERRIDES,
        use_test_real_home,
        write_config,
    )

    for name in ENV_OVERRIDES:
        monkeypatch.delenv(name, raising=False)
    # Якорь режима test: `pw_dir` учётки — временный дом теста.
    use_test_real_home(monkeypatch, tmp_path_factory.mktemp("df-real-home"))
    if request.node.get_closest_marker("no_df_config"):
        # Пустой `XDG_CONFIG_HOME`: файла конфига нет, как на раннере CI.
        empty = tmp_path_factory.mktemp("no-df-config")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(empty))
        return
    # Раскладка тестов — прежняя: соседи devtools лежат рядом с ним.
    write_config(
        Path(os.environ["XDG_CONFIG_HOME"]),
        mode="strict",
        workspace_root=Path(__file__).resolve().parent.parent.parent,
        **_STRICT_GH_DIRS,
    )


#: Хосты, на которые настоящий opener `act_anon` ходит в тестах (loopback-стенды).
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
NETWORK_REFUSED = "тест обратился в сеть мимо шва open_url"


@pytest.fixture(autouse=True)
def _act_no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Настоящий opener `AnonTransport` (транспорт, собранный без `open_url`) в
    тестах ходит только на loopback; иной хост — `pytest.fail` с названной
    причиной, а не сетевой запрос (ревью Task 11c, Minor 1). Боевой код не
    меняется."""
    from urllib.parse import urlsplit

    try:
        from governance import act_anon
    except ImportError:
        return
    real = act_anon._default_open

    def guarded() -> object:
        opener = real()

        def open_url(req: object, *args: object, **kwargs: object) -> object:
            url = getattr(req, "full_url", req)
            if urlsplit(str(url)).hostname not in _LOOPBACK:
                pytest.fail(f"{NETWORK_REFUSED}: {url}")
            return opener(req, *args, **kwargs)

        return open_url

    monkeypatch.setattr(act_anon, "_default_open", guarded)


@pytest.fixture(autouse=True)
def _act_app_no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Боевой opener `act_app_token` (личность и установки App, Task 15a) в тестах
    в сеть не ходит: обращение — `pytest.fail`; тесты подменяют HTTP-шов сами."""
    try:
        from governance import act_app_token
    except ImportError:
        return

    def refuse(req: object, *args: object, **kwargs: object) -> object:
        url = getattr(req, "full_url", req)
        pytest.fail(f"{NETWORK_REFUSED}: {url}")

    # Метод экземпляра, не сам opener: его обработчики (`_NoRedirect`) сверяются.
    monkeypatch.setattr(act_app_token._OPENER, "open", refuse)
