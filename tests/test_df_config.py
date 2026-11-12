"""Загрузчик конфига DarkFactory (дизайн darkfactory-config §5.1, план 1 Task 2).

Фикстура пишет TOML во временный `XDG_CONFIG_HOME`; загрузчик получает окружение
явно. Значения владельца живут только в данных теста, не в модуле.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from governance import act_settings, df_config, sandbox, secret_dirs
from governance.df_config import ConfigError
from tests.df_config_fixture import anchor_test_config, write_config

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
    anchor_test_config(home)  # режим test — из канонического пути (якорь)
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
    if rel == ".config/gh":  # человеку — иной профиль: совпадение в strict отказ
        text = text.replace(
            'human_gh_config_dir = "~/.config/gh"',
            'human_gh_config_dir = "~/.config/review"',
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


@pytest.mark.parametrize("key", ["workspace.root", "accounts.agent_gh_config_dir"])
def test_tilde_double_slash_refused(tmp_path: Path, key: str) -> None:
    """`~//x` дал бы `/x` (Path.home() / "/x") — корень вместо домашнего каталога."""
    old = {
        "workspace.root": 'root = "~/labs/all_ai_orchestrators"',
        "accounts.agent_gh_config_dir": 'agent_gh_config_dir = "~/.config/review"',
    }[key]
    name = key.split(".")[-1]
    text = STRICT.replace(old, f'{name} = "~//etc/gh"')
    assert key in _refused(tmp_path, text)
    assert key in _refused(tmp_path, text.replace('mode = "strict"', 'mode = "test"'))


def test_strict_same_gh_dir_for_both_accounts_refused(tmp_path: Path) -> None:
    """Один профиль на обе учётки стёр бы различитель merged_by agent/human."""
    text = STRICT.replace(
        'human_gh_config_dir = "~/.config/gh"',
        'human_gh_config_dir = "~/.config/review"',
    )
    msg = _refused(tmp_path, text)
    assert "accounts.human_gh_config_dir" in msg
    assert "accounts.agent_gh_config_dir" in msg
    cfg = _load_text(tmp_path, text.replace('mode = "strict"', 'mode = "test"'))
    assert cfg.accounts.human_gh_config_dir == cfg.accounts.agent_gh_config_dir


def test_strict_same_login_for_both_accounts_refused(tmp_path: Path) -> None:
    """Одна учётка на обе роли стёрла бы различитель merged_by agent/human;
    в test — допустимо (§5.3 п.1)."""
    text = re.sub(r'(?m)^agent = ".*"$', 'agent = "same-login"', STRICT)
    text = re.sub(r'(?m)^human = ".*"$', 'human = "same-login"', text)
    msg = _refused(tmp_path, text)
    assert "accounts.agent" in msg and "accounts.human" in msg
    cfg = _load_text(tmp_path, text.replace('mode = "strict"', 'mode = "test"'))
    assert cfg.accounts.agent == cfg.accounts.human == "same-login"


def test_strict_gh_dirs_match_sandbox() -> None:
    """Перечень strict в загрузчике равен перечню, закрытому песочницей."""
    assert secret_dirs.SECRET_DIRS == (
        ".config/review",
        ".config/gh",
        ".config/ai-prosto",
        ".ssh",
    )
    assert sandbox.SECRET_DIRS is secret_dirs.SECRET_DIRS
    assert df_config.STRICT_GH_DIRS == secret_dirs.SECRET_DIRS


def test_act_settings_config_home_is_df_config() -> None:
    assert act_settings.config_home is df_config.config_home


def test_no_owner_fallback() -> None:
    text = (ROOT / "governance" / "df_config.py").read_text(encoding="utf-8")
    # Каталог профиля gh в перечне strict — путь, а не учётка.
    text = text.replace(".config/ai-prosto", "")
    assert "ai-prosto" not in text
    assert "andrei-shtanakov" not in text


def test_df_config_imports_stdlib_only() -> None:
    """Загрузчик вендорится дословно: ни одного импорта `governance.*`."""
    import ast

    tree = ast.parse((ROOT / "governance" / "df_config.py").read_text("utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
        else:
            continue
        for mod in mods:
            top = mod.split(".")[0]
            assert top in sys.stdlib_module_names or top == "__future__", mod


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
    with pytest.raises(ConfigError, match="harness.nope"):
        df_config.get_value(_load_text(tmp_path, STRICT), "harness.nope")


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


def test_get_harness_keys(tmp_path: Path) -> None:
    env = _write(tmp_path, STRICT)
    assert _cli(env, "get", "harness.author").stdout == "claude\n"
    assert _cli(env, "get", "harness.model").stdout == "claude-opus-5\n"


def test_cli_get_mode_from_foreign_cwd(tmp_path: Path) -> None:
    env = _write(tmp_path, STRICT)
    done = _cli(env, "get", "mode")
    assert (done.returncode, done.stdout) == (0, "strict\n"), done.stderr


def test_banner_none_in_strict(tmp_path: Path) -> None:
    assert df_config.test_banner(_load_text(tmp_path, STRICT)) is None


def test_banner_lists_relaxations_in_test(tmp_path: Path) -> None:
    cfg = _load_text(tmp_path, STRICT.replace('mode = "strict"', 'mode = "test"'))
    assert df_config.TEST_RELAXATIONS == (
        "репо политики утверждения не используется: утверждающий — accounts.human",
        (
            "песочница авторинга: claude запускается обычным процессом"
            " пользователя (нет sandbox-exec, допуска и связки ключей)"
        ),
        (
            "белый список целей: цель — из targets.toml рядом с конфигом,"
            " манифест флота необязателен"
        ),
        (
            "одобрение ревью: вердикт публикуется комментарием"
            " (GitHub не даёт одобрить свой PR), вердикт обязателен"
        ),
        (
            "корень доверия df (/etc/df/trust.toml) не используется:"
            " акт — по мержу (режим до миграции)"
        ),
        "акт человека не требуется (test): candidate-PR мержит раннер после зелёных проверок",
        "класс актора не применяется (test): finalize не сверяет класс актора; kill switch действует",
        "утверждение задач человеком не требуется (test): tasks-PR, spec approve и approve-PR проводит раннер",
        "мерж PR с кодом — за вами (test): приёмка проверяет PR с кодом, но не мержит его",
        "проверок нет (test): у PR нет .github/workflows и чеков — CI не ждём; чеки CI вне GitHub Actions, пришедшие позже 2 минут, не учитываются",
        "одна учётка GitHub: accounts.human может совпадать с accounts.agent",
    )
    banner = df_config.test_banner(cfg)
    assert banner is not None
    lines = banner.splitlines()
    assert lines[0] == "режим test: по сравнению со strict выключено"
    assert lines[1:] == [f"  - {item}" for item in df_config.TEST_RELAXATIONS]


SKIP_CONSTANTS = (
    ("runner", "ACT_SKIP_TEST"),
    ("approve_node", "ACTOR_SKIP_TEST"),
    ("auto_tasks", "TASKS_SKIP_TEST"),
    ("accept_pr", "ACCEPT_NO_MERGE_TEST"),
    ("pr_checks", "NO_CHECKS_TEST"),
)


def test_banner_names_every_test_skip() -> None:
    import importlib

    for module, const in SKIP_CONSTANTS:
        text = getattr(importlib.import_module(f"governance.{module}"), const)
        assert any(line.startswith(text) for line in df_config.TEST_RELAXATIONS), const


def _module_skip_constants(source: str) -> set[str]:
    """Имена `*_TEST`, присвоенные на уровне модуля, в любой форме значения."""
    import ast

    names: set[str] = set()
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        names |= {
            t.id
            for t in targets
            if isinstance(t, ast.Name) and t.id.isupper() and t.id.endswith("_TEST")
        }
    return names


def test_skip_constant_scan_sees_every_form() -> None:
    source = (
        'A_TEST = "x"\n'
        'B_TEST = (\n    "long"\n)\n'
        'C_TEST: str = "y"\n'
        "def f():\n    LOCAL_TEST = 1\n"
        'lower_test = "z"\n'
    )
    assert _module_skip_constants(source) == {"A_TEST", "B_TEST", "C_TEST"}


def test_banner_skip_constants_are_complete() -> None:
    root = Path(__file__).resolve().parents[1] / "governance"
    found: set[str] = set()
    for path in root.rglob("*.py"):
        found |= _module_skip_constants(path.read_text(encoding="utf-8"))
    assert found == {const for _, const in SKIP_CONSTANTS}


BAD_LOGINS = [
    "alice,bob",
    "alice bob",
    " alice",
    "",
    "-alice",
    "alice-",
    "a--b",
    "a" * 40,
]


@pytest.mark.parametrize("mode", ["strict", "test"])
@pytest.mark.parametrize("key", ["human", "agent"])
@pytest.mark.parametrize("login", BAD_LOGINS)
def test_login_must_be_single_github_login(
    tmp_path: Path, mode: str, key: str, login: str
) -> None:
    other = {"human": "agent", "agent": "human"}[key]
    text = STRICT.replace('mode = "strict"', f'mode = "{mode}"')
    text = re.sub(rf'^{key} = ".*"$', f'{key} = "{login}"', text, flags=re.MULTILINE)
    text = re.sub(rf'^{other} = ".*"$', f'{other} = "bob"', text, flags=re.MULTILINE)
    assert f"accounts.{key}" in _refused(tmp_path, text)


@pytest.mark.parametrize("mode", ["strict", "test"])
@pytest.mark.parametrize("login", ["ai-prosto", "andrei-shtanakov", "a1-b2", "a" * 39])
def test_valid_logins_pass(tmp_path: Path, mode: str, login: str) -> None:
    text = STRICT.replace('mode = "strict"', f'mode = "{mode}"')
    text = re.sub(r'^human = ".*"$', f'human = "{login}"', text, flags=re.MULTILINE)
    text = re.sub(r'^agent = ".*"$', 'agent = "bob"', text, flags=re.MULTILINE)
    assert _load_text(tmp_path, text).accounts.human == login


def test_cli_mode_or_strict_without_file(tmp_path: Path) -> None:
    done = _cli({"XDG_CONFIG_HOME": str(tmp_path)}, "mode-or-strict")
    assert (done.returncode, done.stdout) == (0, "strict\n"), done.stderr


def test_cli_mode_or_strict_broken_file(tmp_path: Path) -> None:
    cfg = tmp_path / "darkfactory" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("mode = [", encoding="utf-8")
    done = _cli({"XDG_CONFIG_HOME": str(tmp_path)}, "mode-or-strict")
    assert done.returncode == 2 and done.stdout == ""
    assert "конфиг DarkFactory:" in done.stderr


def test_cli_mode_or_strict_reads_mode(tmp_path: Path) -> None:
    cfg = tmp_path / "darkfactory" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(STRICT, encoding="utf-8")
    done = _cli({"XDG_CONFIG_HOME": str(tmp_path)}, "mode-or-strict")
    assert (done.returncode, done.stdout) == (0, "strict\n"), done.stderr


def test_mode_or_strict_no_file(tmp_path: Path) -> None:
    assert df_config.mode_or_strict({"XDG_CONFIG_HOME": str(tmp_path)}) == "strict"


def test_mode_or_strict_reads_test(tmp_path: Path) -> None:
    env = write_config(tmp_path, mode="test")
    assert df_config.mode_or_strict(env) == "test"


def test_mode_or_strict_dangling_symlink_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "darkfactory" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.symlink_to(tmp_path / "nowhere.toml")
    with pytest.raises(df_config.ConfigError):
        df_config.mode_or_strict({"XDG_CONFIG_HOME": str(tmp_path)})


def test_mode_or_strict_broken_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "darkfactory" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("это не toml [", encoding="utf-8")
    with pytest.raises(df_config.ConfigError):
        df_config.mode_or_strict({"XDG_CONFIG_HOME": str(tmp_path)})


def test_no_strict_rule_copies() -> None:
    """Правило «нет файла = strict» живёт только в `df_config.mode_or_strict`.

    `policy_rule.py` — другая форма (возвращает конфиг/`None`, а не режим),
    поэтому ему `lexists(df_config.config_path(` разрешён."""
    root = Path(__file__).resolve().parent.parent
    allowed = {"policy_rule.py"}
    needle = "lexists(df_config.config_path("
    found = {
        p.name
        for pattern in ("governance/*.py", "lite/*.py")
        for p in root.glob(pattern)
        if needle in p.read_text(encoding="utf-8")
    }
    assert found <= allowed, sorted(found - allowed)


def test_test_approver_version_value() -> None:
    import hashlib

    assert (
        df_config.test_approver_version("alice")
        == hashlib.sha256(b"AUTHORIZED_APPROVER_ACCOUNTS=alice\n").hexdigest()[:40]
    )


def test_policy_rule_sha_unchanged(tmp_path: Path) -> None:
    """sha test_policy — прежнее значение (литерал посчитан до рефакторинга)
    и строка ключа совпадает с `policy_rule.APPROVER_ALLOWLIST_ENV`."""
    import hashlib

    from governance import policy_rule

    assert f"{policy_rule.APPROVER_ALLOWLIST_ENV}=alice\n" == (
        "AUTHORIZED_APPROVER_ACCOUNTS=alice\n"
    )
    text = _drop_table(STRICT, "policy").replace('mode = "strict"', 'mode = "test"')
    cfg = _load_text(tmp_path, text.replace("andrei-shtanakov", "alice"))
    assert cfg.accounts.human == "alice"
    legacy = hashlib.sha256(b"AUTHORIZED_APPROVER_ACCOUNTS=alice\n").hexdigest()[:40]
    assert df_config.test_approver_version(cfg.accounts.human) == legacy
    assert df_config.get_value(cfg, "test.approver_version") == legacy


def test_get_test_approver_version_strict_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="только для режима test"):
        df_config.get_value(_load_text(tmp_path, STRICT), "test.approver_version")


def test_cli_test_approver_version_strict_refused(tmp_path: Path) -> None:
    env = _write(tmp_path, STRICT)
    res = _cli(env, "get", "test.approver_version")
    assert res.returncode == 2 and "только для режима test" in res.stderr


def test_cli_usage_lists_mode_or_strict() -> None:
    res = _cli({}, "bogus")
    assert res.returncode == 2 and "mode-or-strict" in res.stderr
