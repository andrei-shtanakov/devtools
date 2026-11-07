"""Загрузчик конфига DarkFactory (дизайн darkfactory-config §5.1, план 1 Task 2).

Фикстура пишет TOML во временный `XDG_CONFIG_HOME`; загрузчик получает окружение
явно. Значения владельца живут только в данных теста, не в модуле.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from governance import df_config, sandbox, secret_dirs
from governance.df_config import ConfigError

ROOT = Path(__file__).resolve().parent.parent

STRICT = """\
schema = "darkfactory-config/v1"
mode = "strict"

[accounts]
human = "andrei-shtanakov"
agent = "ai-prosto"
human_gh_config_dir = "~/.config/gh"
agent_gh_config_dir = "~/.config/review"

[policy]
repo = "andrei-shtanakov/approval-policy"
ref = "main"
path = "policy/approvers.env"

[workspace]
root = "~/labs/all_ai_orchestrators"

[harness]
author = "claude"
model = "claude-opus-5"

[app]
id = 0
key_file = "~/.config/darkfactory/apps/0.pem"
"""


def _write(home: Path, text: str) -> dict[str, str]:
    path = home / "darkfactory" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return {"XDG_CONFIG_HOME": str(home)}


def _drop_table(text: str, table: str) -> str:
    out: list[str] = []
    skipping = False
    for line in text.splitlines(keepends=True):
        if line.startswith("["):
            skipping = line.strip() == f"[{table}]"
        if not skipping:
            out.append(line)
    return "".join(out)


def _load_text(tmp_path: Path, text: str) -> df_config.DfConfig:
    return df_config.load(_write(tmp_path, text))


def _refused(tmp_path: Path, text: str) -> str:
    with pytest.raises(ConfigError) as exc:
        _load_text(tmp_path, text)
    return str(exc.value)


def test_strict_full_config_loads(tmp_path: Path) -> None:
    cfg = _load_text(tmp_path, STRICT)
    assert cfg.mode == "strict"
    assert cfg.accounts.agent == "ai-prosto"
    assert cfg.accounts.human == "andrei-shtanakov"
    assert cfg.policy is not None
    assert cfg.policy.repo == "andrei-shtanakov/approval-policy"
    assert cfg.policy.ref == "main"
    assert cfg.policy.path == "policy/approvers.env"
    assert cfg.workspace_root == Path.home() / "labs/all_ai_orchestrators"
    assert cfg.author_harness == "claude"
    assert cfg.author_model == "claude-opus-5"
    assert cfg.app_id == 0
    assert cfg.app_key_file == Path.home() / ".config/darkfactory/apps/0.pem"


def test_config_path_uses_config_home_rule(tmp_path: Path) -> None:
    env = {"XDG_CONFIG_HOME": str(tmp_path)}
    assert df_config.config_path(env) == tmp_path / "darkfactory" / "config.toml"
    rel = {"XDG_CONFIG_HOME": "relative/dir"}
    assert df_config.config_path(rel) == Path.home() / ".config/darkfactory/config.toml"


def test_missing_file_names_path(tmp_path: Path) -> None:
    env = {"XDG_CONFIG_HOME": str(tmp_path)}
    with pytest.raises(ConfigError) as exc:
        df_config.load(env)
    assert str(tmp_path / "darkfactory" / "config.toml") in str(exc.value)


def test_broken_toml_refused(tmp_path: Path) -> None:
    msg = _refused(tmp_path, "mode = \n")
    assert "config.toml" in msg


def test_unknown_mode(tmp_path: Path) -> None:
    msg = _refused(tmp_path, STRICT.replace('mode = "strict"', 'mode = "loose"'))
    assert "mode" in msg and "config.toml" in msg


def test_missing_mode(tmp_path: Path) -> None:
    assert "mode" in _refused(tmp_path, STRICT.replace('mode = "strict"\n', ""))


def test_missing_schema(tmp_path: Path) -> None:
    msg = _refused(tmp_path, STRICT.replace('schema = "darkfactory-config/v1"\n', ""))
    assert "schema" in msg


def test_wrong_schema_version(tmp_path: Path) -> None:
    text = STRICT.replace("darkfactory-config/v1", "darkfactory-config/v2")
    assert "schema" in _refused(tmp_path, text)


def test_wrong_type_app_id(tmp_path: Path) -> None:
    assert "app.id" in _refused(tmp_path, STRICT.replace("id = 0", 'id = "0"'))


def test_bool_app_id_refused(tmp_path: Path) -> None:
    assert "app.id" in _refused(tmp_path, STRICT.replace("id = 0", "id = true"))


def test_wrong_type_account(tmp_path: Path) -> None:
    text = STRICT.replace('agent = "ai-prosto"', "agent = 1")
    assert "accounts.agent" in _refused(tmp_path, text)


def test_empty_string_refused(tmp_path: Path) -> None:
    text = STRICT.replace('agent = "ai-prosto"', 'agent = ""')
    assert "accounts.agent" in _refused(tmp_path, text)


def test_table_as_scalar_refused(tmp_path: Path) -> None:
    text = _drop_table(STRICT, "workspace").replace(
        'mode = "strict"', 'mode = "strict"\nworkspace = "x"'
    )
    assert "workspace" in _refused(tmp_path, text)


def test_unknown_key_refused(tmp_path: Path) -> None:
    msg = _refused(tmp_path, STRICT.replace("[accounts]", "[acounts]"))
    assert "неизвестный ключ acounts" in msg


def test_unknown_nested_key_refused(tmp_path: Path) -> None:
    text = STRICT.replace('ref = "main"', 'ref = "main"\nbranch = "x"')
    assert "неизвестный ключ policy.branch" in _refused(tmp_path, text)


@pytest.mark.parametrize(
    ("table", "key"),
    [
        ("accounts", "accounts"),
        ("workspace", "workspace"),
        ("harness", "harness"),
    ],
)
def test_required_table_missing(tmp_path: Path, table: str, key: str) -> None:
    assert key in _refused(tmp_path, _drop_table(STRICT, table))


def test_required_key_missing(tmp_path: Path) -> None:
    text = STRICT.replace('model = "claude-opus-5"\n', "")
    assert "harness.model" in _refused(tmp_path, text)


def test_strict_requires_policy(tmp_path: Path) -> None:
    assert "policy" in _refused(tmp_path, _drop_table(STRICT, "policy"))


def test_strict_policy_key_missing(tmp_path: Path) -> None:
    text = STRICT.replace('ref = "main"\n', "")
    assert "policy.ref" in _refused(tmp_path, text)


def test_test_mode_policy_optional(tmp_path: Path) -> None:
    text = _drop_table(STRICT, "policy").replace('mode = "strict"', 'mode = "test"')
    cfg = _load_text(tmp_path, text)
    assert cfg.mode == "test"
    assert cfg.policy is None


def test_app_optional(tmp_path: Path) -> None:
    cfg = _load_text(tmp_path, _drop_table(STRICT, "app"))
    assert cfg.app_id is None and cfg.app_key_file is None


def test_app_half_configured_refused(tmp_path: Path) -> None:
    text = STRICT.replace('key_file = "~/.config/darkfactory/apps/0.pem"\n', "")
    assert "app.key_file" in _refused(tmp_path, text)


@pytest.mark.parametrize("key", ["human_gh_config_dir", "agent_gh_config_dir"])
@pytest.mark.parametrize(
    "value", ["~/creds/r", "~/.config/gh/../../creds", "~/.config", "/etc/gh"]
)
def test_strict_gh_dir_outside_sandbox_refused(
    tmp_path: Path, key: str, value: str
) -> None:
    default = {
        "human_gh_config_dir": "~/.config/gh",
        "agent_gh_config_dir": "~/.config/review",
    }[key]
    text = STRICT.replace(f'{key} = "{default}"', f'{key} = "{value}"')
    assert f"accounts.{key}" in _refused(tmp_path, text)
    cfg = _load_text(tmp_path, text.replace('mode = "strict"', 'mode = "test"'))
    expected = Path(value) if value.startswith("/") else Path.home() / value[2:]
    assert getattr(cfg.accounts, key) == expected


@pytest.mark.parametrize("rel", secret_dirs.SECRET_DIRS)
def test_strict_gh_dir_every_secret_dir_accepted(tmp_path: Path, rel: str) -> None:
    text = STRICT.replace(
        'agent_gh_config_dir = "~/.config/review"', f'agent_gh_config_dir = "~/{rel}"'
    )
    assert _load_text(tmp_path, text).accounts.agent_gh_config_dir == (
        Path.home() / rel
    )


def test_tilde_expanded_relative_refused(tmp_path: Path) -> None:
    cfg = _load_text(tmp_path, STRICT)
    assert cfg.accounts.agent_gh_config_dir == Path.home() / ".config/review"
    assert cfg.accounts.agent_gh_config_dir.is_absolute()
    text = STRICT.replace('root = "~/labs/all_ai_orchestrators"', 'root = "rel/dir"')
    assert "workspace.root" in _refused(tmp_path, text)
    text = STRICT.replace('root = "~/labs/all_ai_orchestrators"', 'root = "~user/x"')
    assert "workspace.root" in _refused(tmp_path, text)


def test_secret_dirs_single_source() -> None:
    """Один перечень: песочница и загрузчик держат тот же объект."""
    assert secret_dirs.SECRET_DIRS == (
        ".config/review",
        ".config/gh",
        ".config/ai-prosto",
        ".ssh",
    )
    assert sandbox.SECRET_DIRS is secret_dirs.SECRET_DIRS
    assert df_config.SECRET_DIRS is secret_dirs.SECRET_DIRS


def test_no_owner_fallback() -> None:
    text = (ROOT / "governance" / "df_config.py").read_text(encoding="utf-8")
    assert "ai-prosto" not in text
    assert "andrei-shtanakov" not in text


def test_module_imports_only_stdlib_and_allowed() -> None:
    """Импорты загрузчика: stdlib, `act_settings`, `secret_dirs` — без sandbox/ops."""
    import ast

    tree = ast.parse((ROOT / "governance" / "df_config.py").read_text("utf-8"))
    allowed = {"governance.act_settings", "governance.secret_dirs"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
        else:
            continue
        for mod in mods:
            top = mod.split(".")[0]
            assert (
                top in sys.stdlib_module_names
                or top == "__future__"
                or (mod in allowed)
            ), mod


def test_import_without_config(tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, "-c", "import governance.df_config"],
        cwd=ROOT,
        env={**os.environ, "XDG_CONFIG_HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr


def test_load_reads_every_call(tmp_path: Path) -> None:
    env = _write(tmp_path, STRICT)
    assert df_config.load(env).accounts.agent == "ai-prosto"
    _write(tmp_path, STRICT.replace('agent = "ai-prosto"', 'agent = "bot-2"'))
    assert df_config.load(env).accounts.agent == "bot-2"


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("mode", "strict"),
        ("accounts.human", "andrei-shtanakov"),
        ("accounts.agent", "ai-prosto"),
        ("accounts.human_gh_config_dir", str(Path.home() / ".config/gh")),
        ("accounts.agent_gh_config_dir", str(Path.home() / ".config/review")),
        ("policy.repo", "andrei-shtanakov/approval-policy"),
        ("policy.ref", "main"),
        ("policy.path", "policy/approvers.env"),
        ("workspace.root", str(Path.home() / "labs/all_ai_orchestrators")),
    ],
)
def test_get_value(tmp_path: Path, key: str, expected: str) -> None:
    assert df_config.get_value(_load_text(tmp_path, STRICT), key) == expected


def test_get_value_unknown_key(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="harness.author"):
        df_config.get_value(_load_text(tmp_path, STRICT), "harness.author")


def test_get_value_policy_absent_in_test(tmp_path: Path) -> None:
    text = _drop_table(STRICT, "policy").replace('mode = "strict"', 'mode = "test"')
    with pytest.raises(ConfigError, match="policy.repo"):
        df_config.get_value(_load_text(tmp_path, text), "policy.repo")


def _cli(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(ROOT / "governance" / "df_config.py"), *args],
        cwd="/",
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_get_and_error(tmp_path: Path) -> None:
    env = _write(tmp_path, STRICT)
    ok = _cli(env, "get", "accounts.agent")
    assert (ok.returncode, ok.stdout) == (0, "ai-prosto\n"), ok.stderr
    bad = _cli(env, "get", "accounts.nope")
    assert bad.returncode == 2 and bad.stdout == ""
    assert "accounts.nope" in bad.stderr and "config.toml" in bad.stderr


def test_cli_missing_file_and_usage(tmp_path: Path) -> None:
    env = {"XDG_CONFIG_HOME": str(tmp_path)}
    missing = _cli(env, "get", "mode")
    assert missing.returncode == 2
    assert str(tmp_path / "darkfactory" / "config.toml") in missing.stderr
    assert _cli(env).returncode == 2
    assert _cli(env, "set", "mode").returncode == 2


def _sh(env: dict[str, str], key: str) -> subprocess.CompletedProcess[str]:
    script = (
        f'script_dir="$PWD"; . ./df_config.sh; v=$(df_cfg {key}) || exit $?; echo $v'
    )
    return subprocess.run(
        ["sh", "-c", script],
        cwd=ROOT,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )


def test_df_cfg_sh(tmp_path: Path) -> None:
    env = _write(tmp_path, STRICT)
    ok = _sh(env, "accounts.agent")
    assert (ok.returncode, ok.stdout) == (0, "ai-prosto\n"), ok.stderr
    bad = _sh(env, "accounts.nope")
    assert bad.returncode == 2 and bad.stdout == ""
    assert "accounts.nope" in bad.stderr


def test_df_cfg_sh_without_script_dir(tmp_path: Path) -> None:
    env = _write(tmp_path, STRICT)
    done = subprocess.run(
        ["sh", "-c", ". ./df_config.sh; v=$(df_cfg mode) || exit $?; echo $v"],
        cwd=ROOT,
        env={k: v for k, v in {**os.environ, **env}.items() if k != "script_dir"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 2 and done.stdout == ""
    assert "script_dir" in done.stderr
