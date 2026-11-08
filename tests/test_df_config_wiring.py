"""Python-места читают конфиг DarkFactory (план 1 darkfactory-config, Task 4).

Учётки, профили gh и источник политики — из `config.toml`
(`governance/df_config.py`), не из констант в коде. Конфиг теста по умолчанию
даёт автофикстура `tests/conftest.py::_df_config`; здесь — свои значения.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from governance import policy_rule
from tests.df_config_fixture import write_config

ROOT = Path(__file__).resolve().parents[1]
#: Модули, где учётки владельца были литералами до Task 4.
NO_OWNER_LITERALS = (
    "governance/policy_rule.py",
    "governance/act_premerge.py",
    "governance/act_status.py",
    "governance/ops.py",
    "governance/acceptance_provenance.py",
    "governance/policy_sources.py",
    "governance/sandbox.py",
)
_OWNER_LITERAL = re.compile(r"""["'](ai-prosto|andrei-shtanakov)["']""")


def _use_config(monkeypatch: pytest.MonkeyPatch, xdg: Path, **kw: Any) -> None:
    env = write_config(xdg, **kw)
    monkeypatch.setenv("XDG_CONFIG_HOME", env["XDG_CONFIG_HOME"])


#: Профили gh из перечня песочницы — условие `strict` (§5.1).
_STRICT = {
    "mode": "strict",
    "agent_gh_config_dir": Path("~/.config/review"),
    "human_gh_config_dir": Path("~/.config/gh"),
}
SSOT = ("andrei-shtanakov/approval-policy", "main", "policy/approvers.env")


def test_policy_source_is_the_authority_root_ssot() -> None:
    """Источник — SSOT под authority-root (конфиг conftest с ним совпадает)."""
    assert policy_rule.policy_source() == SSOT
    text = policy_rule.POLICY_SOURCE_FILE.read_text(encoding="utf-8")
    assert all(f"={v}\n" in text for v in SSOT)


@pytest.mark.parametrize("mode", [{}, _STRICT], ids=["test", "strict"])
def test_config_policy_cannot_redirect_source(monkeypatch, tmp_path, mode) -> None:
    """В любом режиме `[policy]` конфига, расходящийся с SSOT, — отказ с
    обоими значениями, а не перенаправление: конфиг не выбирает доверие."""
    _use_config(monkeypatch, tmp_path, policy_repo="evil/policy", **mode)
    with pytest.raises(RuntimeError, match="расходится с authority-root SSOT") as e:
        policy_rule.policy_source()
    assert "evil/policy" in str(e.value) and SSOT[0] in str(e.value)
    _use_config(monkeypatch, tmp_path, policy_ref="other", **mode)
    with pytest.raises(RuntimeError, match="расходится"):
        policy_rule.policy_source()


def test_policy_source_test_mode_without_policy_table_is_ssot(
    monkeypatch, tmp_path
) -> None:
    """`test` без `[policy]` — сверять нечего: источник всё равно SSOT."""
    _use_config(monkeypatch, tmp_path)
    path = tmp_path / "darkfactory" / "config.toml"
    text = path.read_text(encoding="utf-8")
    path.write_text(re.sub(r"\[policy\]\n(.+\n){3}", "", text), encoding="utf-8")
    assert "[policy]" not in path.read_text(encoding="utf-8")
    assert policy_rule.policy_source() == SSOT


def test_policy_source_config_failure_is_runtime_error(monkeypatch, tmp_path):
    """Конфиг есть, но битый — тот же RuntimeError, что и битый SSOT-файл:
    вызывающие ловят его, ConfigError ушёл бы мимо."""
    path = tmp_path / "darkfactory" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text("schema = 1\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with pytest.raises(RuntimeError, match="config.toml"):
        policy_rule.policy_source()


@pytest.mark.no_df_config
def test_policy_source_without_config_file_is_ssot() -> None:
    """Файла конфига нет (раннер CI) — нет и `[policy]`: источник — SSOT (F1)."""
    assert policy_rule.policy_source() == SSOT


@pytest.mark.no_df_config
def test_agent_identity_helpers_env_first_then_config(monkeypatch, tmp_path) -> None:
    """`df_config.agent_login`/`agent_gh_config_dir`: env, иначе конфиг (F4)."""
    from governance import df_config

    with pytest.raises(df_config.ConfigError):
        df_config.agent_login()
    with pytest.raises(df_config.ConfigError):
        df_config.agent_gh_config_dir()
    env = {"REVIEW_LOGIN": "bot-e", "REVIEW_GH_CONFIG_DIR": "/x/r"}
    assert df_config.agent_login(env) == "bot-e"
    assert df_config.agent_gh_config_dir(env) == Path("/x/r")
    _use_config(monkeypatch, tmp_path, agent="bot-c", agent_gh_config_dir=tmp_path)
    assert df_config.agent_login() == "bot-c"
    assert df_config.agent_gh_config_dir() == tmp_path
    empty = {"REVIEW_LOGIN": "", "REVIEW_GH_CONFIG_DIR": "", **write_config(tmp_path)}
    assert df_config.agent_login(empty) == "ai-prosto"


@pytest.mark.no_df_config
def test_act_status_without_config_is_named_refusal(tmp_path: Path) -> None:
    """Нет профиля в env и нет конфига — `live_policy` даёт названный отказ, а
    не трассировку из `real_deps` (F3)."""
    pytest.importorskip("steward")
    from governance import act_status
    from governance.act_time import Unavailable

    state = SimpleNamespace(
        repo_slug="o/p", ws_id="w", target_dir=str(tmp_path), run_id="r"
    )
    deps = act_status.real_deps(state)  # type: ignore[arg-type]
    live = act_status.live_policy(state, deps)  # type: ignore[arg-type]
    assert isinstance(live, Unavailable)
    assert live.reason.startswith("конфиг DarkFactory: ")


def _premerge_creds(env: dict[str, str]) -> list[tuple[str, Path | None]]:
    pytest.importorskip("steward")
    from governance import act_premerge

    args = argparse.Namespace(
        merge_profile="/x/merge", repo="o/p", pr=7, app_id=None, app_key=None
    )
    deps = act_premerge.real_deps(args, env=env)
    return [(c.label, c.gh_config_dir) for c in deps.creds]


def _status_agent_dir(env: dict[str, str], tmp_path: Path) -> Path | None:
    pytest.importorskip("steward")
    from governance import act_status

    state = SimpleNamespace(
        repo_slug="o/p", ws_id="w", target_dir=str(tmp_path), run_id="r"
    )
    deps = act_status.real_deps(state, env=env)
    return {c.label: c.gh_config_dir for c in deps.creds}["profile:agent"]


def test_act_profiles_default_to_config(tmp_path: Path) -> None:
    env = {
        **write_config(tmp_path, agent_gh_config_dir=tmp_path / "bot"),
        "XDG_STATE_HOME": str(tmp_path),
    }
    assert ("profile:agent", tmp_path / "bot") in _premerge_creds(env)
    assert _status_agent_dir(env, tmp_path) == tmp_path / "bot"


def test_act_profiles_env_overrides_config(tmp_path: Path) -> None:
    env = {
        **write_config(tmp_path, agent_gh_config_dir=tmp_path / "bot"),
        "XDG_STATE_HOME": str(tmp_path),
        "REVIEW_GH_CONFIG_DIR": "/x/review",
    }
    assert ("profile:agent", Path("/x/review")) in _premerge_creds(env)
    assert _status_agent_dir(env, tmp_path) == Path("/x/review")


@pytest.mark.parametrize("rel", NO_OWNER_LITERALS)
def test_no_owner_literals(rel: str) -> None:
    """Учётки владельца — в конфиге, не строковыми литералами в коде."""
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert not _OWNER_LITERAL.findall(text), rel


def test_act_premerge_config_failure_refuses(monkeypatch, tmp_path, capsys) -> None:
    """Нет профиля в окружении и конфиг не читается — код 2 (конфигурация, как
    у скриптов) с именем файла, не трассировка и не отказ предпроверки 3."""
    pytest.importorskip("steward")
    from governance import act_premerge

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "нет"))
    argv = ["--repo", "o/p", "--pr", "7", "--head-ref", "b", "--head", "h"]
    argv += ["--merge-profile", "/x/m", "--merger", "a", "--merger-type", "User"]
    assert act_premerge.main(argv) == act_premerge.EXIT_UNSET == 2
    assert "config.toml" in capsys.readouterr().err


def test_policy_source_dangling_symlink_fails_closed(monkeypatch, tmp_path) -> None:
    """Висячая симлинка на месте конфига — не «файла нет»: отказ, а не пропуск."""
    path = tmp_path / "darkfactory" / "config.toml"
    path.parent.mkdir(parents=True)
    path.symlink_to(tmp_path / "nowhere.toml")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with pytest.raises(RuntimeError, match="config.toml"):
        policy_rule.policy_source()
