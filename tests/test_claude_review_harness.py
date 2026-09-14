"""Тесты харнесс-слоя ревьюера: переходник claude-review + резолюция в
review-pr.sh (devtools#121, срочный перевод ревьюера ai-prosto на claude).

Переходник говорит на codex-диалекте review-kit снаружи и на
claude-диалекте внутри; сломанный ревьюер обязан выходить не-0 (кит
превратит в свой код 3), а не оставлять пустой/битый вердикт — молчаливый
approve исключён по построению.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

DEVTOOLS = Path(__file__).resolve().parent.parent
SHIM = DEVTOOLS / "scripts" / "harness" / "claude-review"
REVIEW_PR = DEVTOOLS / "review-pr.sh"

ENVELOPE_OK = {
    "is_error": False,
    "subtype": "success",
    "structured_output": {"findings": [], "verdict": "approve"},
}


def _fake_claude(tmp_path: Path, envelope: object, exit_code: int = 0) -> Path:
    """Фейковый `claude` в PATH: пишет argv в лог, отдаёт заданный конверт."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    payload = json.dumps(envelope) if not isinstance(envelope, str) else envelope
    script = bin_dir / "claude"
    script.write_text(
        "#!/bin/sh\n"
        f'printf \'%s\\n\' "$@" > "{tmp_path}/claude-argv.log"\n'
        f"cat > /dev/null\n"  # съесть stdin-промпт, как настоящий -p
        f"printf '%s' '{payload}'\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return bin_dir


def _run_shim(
    tmp_path: Path, bin_dir: Path, *, args: list[str] | None = None
) -> tuple[subprocess.CompletedProcess, Path]:
    schema = tmp_path / "schema.json"
    schema.write_text('{"type": "object"}', encoding="utf-8")
    verdict = tmp_path / "verdict.json"
    argv = args if args is not None else [
        "--sandbox", "read-only",
        "--output-schema", str(schema),
        "--output-last-message", str(verdict),
        "-",
    ]
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    res = subprocess.run(
        ["sh", str(SHIM), *argv],
        input="промпт ревью\n",
        capture_output=True,
        text=True,
        env=env,
    )
    return res, verdict


# --- переходник: успех и режимы отказа --------------------------------------


def test_shim_success_writes_structured_output(tmp_path: Path) -> None:
    bin_dir = _fake_claude(tmp_path, ENVELOPE_OK)
    res, verdict = _run_shim(tmp_path, bin_dir)
    assert res.returncode == 0, res.stderr
    assert json.loads(verdict.read_text(encoding="utf-8")) == (
        ENVELOPE_OK["structured_output"]
    )


def test_shim_argv_contract(tmp_path: Path) -> None:
    """Что реально ушло в claude: модель, схема, read-only набор tools,
    режимы изоляции. Ослабление любого из них — регрессия безопасности."""
    bin_dir = _fake_claude(tmp_path, ENVELOPE_OK)
    res, _ = _run_shim(tmp_path, bin_dir)
    assert res.returncode == 0
    argv = (tmp_path / "claude-argv.log").read_text(encoding="utf-8").split("\n")
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "claude-opus-5"  # дефолт
    for flag in (
        "-p", "--json-schema", "--output-format", "--restricted",
        "--strict-mcp-config", "--no-session-persistence",
        "--permission-prompts", "--tools",
    ):
        assert flag in argv, f"нет {flag}"
    tools = argv[argv.index("--tools") + 1:argv.index("--tools") + 4]
    assert tools == ["Read", "Glob", "Grep"]
    assert "Bash" not in argv and "Edit" not in argv and "Write" not in argv


def test_shim_model_flag_overrides_default(tmp_path: Path) -> None:
    bin_dir = _fake_claude(tmp_path, ENVELOPE_OK)
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    verdict = tmp_path / "v.json"
    res, _ = _run_shim(
        tmp_path, bin_dir,
        args=[
            "--model", "claude-sonnet-4-6",
            "--sandbox", "read-only",
            "--output-schema", str(schema),
            "--output-last-message", str(verdict),
            "-",
        ],
    )
    assert res.returncode == 0
    argv = (tmp_path / "claude-argv.log").read_text(encoding="utf-8").split("\n")
    assert argv[argv.index("--model") + 1] == "claude-sonnet-4-6"


@pytest.mark.parametrize(
    "envelope",
    [
        {**ENVELOPE_OK, "is_error": True},
        {**ENVELOPE_OK, "subtype": "error_during_execution"},
        {**ENVELOPE_OK, "structured_output": None},
        "не json вовсе",
    ],
    ids=["is_error", "bad_subtype", "empty_structured", "broken_stdout"],
)
def test_shim_failure_modes_exit_nonzero_without_verdict(
    tmp_path: Path, envelope: object
) -> None:
    bin_dir = _fake_claude(tmp_path, envelope)
    res, verdict = _run_shim(tmp_path, bin_dir)
    assert res.returncode == 3
    assert not verdict.exists()


def test_shim_claude_crash_is_exit_3(tmp_path: Path) -> None:
    bin_dir = _fake_claude(tmp_path, ENVELOPE_OK, exit_code=1)
    res, verdict = _run_shim(tmp_path, bin_dir)
    assert res.returncode == 3
    assert not verdict.exists()


def test_shim_refuses_unknown_arg_and_wrong_sandbox(tmp_path: Path) -> None:
    """Неизвестный флаг и не-read-only sandbox — конфигурационный отказ (2),
    не «продолжить как понял»."""
    bin_dir = _fake_claude(tmp_path, ENVELOPE_OK)
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    base = [
        "--sandbox", "read-only",
        "--output-schema", str(schema),
        "--output-last-message", str(tmp_path / "v.json"),
        "-",
    ]
    res, _ = _run_shim(tmp_path, bin_dir, args=[*base, "--новый-флаг"])
    assert res.returncode == 2
    res, _ = _run_shim(
        tmp_path, bin_dir,
        args=["--sandbox", "workspace-write", *base[2:]],
    )
    assert res.returncode == 2


# --- review-pr.sh: резолюция харнесса ---------------------------------------

#: Стаб СВЕЖЕГО кита (steward @ a2d7e71): несёт литерал --print-review-cmd
#: (feature-detect), резолвит REVIEW_HARNESS/REVIEW_MODEL как настоящий
#: local.sh §4 (REVIEW_CMD-оверрайд; пустая REVIEW_MODEL — код 2) и пишет в
#: HARNESS_KIT_LOG то, что review-pr.sh ему передал окружением.
NEW_KIT_STUB = """#!/bin/sh
{
  echo "REVIEW_CMD=${REVIEW_CMD-<unset>}"
  echo "REVIEW_HARNESS=${REVIEW_HARNESS-<unset>}"
  echo "REVIEW_MODEL=${REVIEW_MODEL-<unset>}"
  echo "PATH=$PATH"
  echo "CWD=$(pwd -P)"
} >> "${HARNESS_KIT_LOG:?}"
# Шум в stderr — как у настоящего кита (предупреждения префлайта): в строку
# ревьюера попасть не должен.
echo "kit stderr noise" >&2
if [ -n "${REVIEW_KIT_STUB_FAIL:-}" ]; then
  echo "кит отказал: адаптера нет" >&2; exit 2
fi
if [ -n "${REVIEW_CMD:-}" ]; then
  review_cmd="$REVIEW_CMD"
else
  if [ -n "${REVIEW_MODEL+x}" ] && [ -z "$REVIEW_MODEL" ]; then
    echo "REVIEW_MODEL задан пустым" >&2; exit 2
  fi
  case "${REVIEW_HARNESS-codex}" in
    codex) review_cmd="codex exec${REVIEW_MODEL:+ -m $REVIEW_MODEL}" ;;
    claude) review_cmd="harness-claude --model ${REVIEW_MODEL:-claude-opus-5}" ;;
    *) echo "неизвестный харнесс" >&2; exit 2 ;;
  esac
fi
case " $* " in
  *" --print-review-cmd "*) echo "$review_cmd"; exit 0 ;;
esac
echo "stub verdict body"
exit 0
"""


def _new_kit_fleet(tmp_path: Path) -> tuple[Path, Path]:
    """FLEET_ROOT с репо `demo`, чей scripts/review/local.sh — свежий кит."""
    fleet_root = tmp_path / "fleet"
    kit = fleet_root / "demo" / "scripts" / "review"
    kit.mkdir(parents=True)
    local_sh = kit / "local.sh"
    local_sh.write_text(NEW_KIT_STUB, encoding="utf-8")
    local_sh.chmod(local_sh.stat().st_mode | stat.S_IXUSR)
    return fleet_root, tmp_path / "kit.log"


def _resolve(
    tmp_path: Path,
    *,
    argv: list[str] = (),
    env_extra: dict[str, str] | None = None,
    cfg: str | None = None,
    repo: str = "dummy",
    expect_rc: int = 0,
) -> str:
    cfg_path = tmp_path / "harness.env"
    if cfg is not None:
        cfg_path.write_text(cfg, encoding="utf-8")
    env = {
        **os.environ,
        "AI_PROSTO_HARNESS_ENV": str(cfg_path),
        # Герметичность: без FLEET_ROOT зонд нашёл бы настоящий чекаут
        # репо `dummy`/`demo` в воркспейсе.
        "FLEET_ROOT": str(tmp_path / "fleet"),
    }
    env.pop("REVIEW_HARNESS", None)
    env.pop("REVIEW_MODEL", None)
    env.pop("REVIEW_CMD", None)
    env.update(env_extra or {})
    res = subprocess.run(
        ["sh", str(REVIEW_PR), repo, "1", "--print-review-cmd", *argv],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res.returncode == expect_rc, res.stderr
    return res.stdout.strip() if expect_rc == 0 else res.stderr.strip()


def test_resolution_builtin_default_is_codex(tmp_path: Path) -> None:
    """Вшитый дефолт — исторический codex: скрипт не несёт знания о
    подписке; claude включается операторским конфигом."""
    assert _resolve(tmp_path) == "codex exec"


def test_resolution_config_file_flips_to_claude(tmp_path: Path) -> None:
    cmd = _resolve(
        tmp_path,
        cfg="REVIEW_HARNESS=claude\nREVIEW_MODEL=claude-opus-5\n",
    )
    assert cmd == "claude-review --model claude-opus-5"


def test_resolution_env_beats_config(tmp_path: Path) -> None:
    cmd = _resolve(
        tmp_path,
        cfg="REVIEW_HARNESS=claude\nREVIEW_MODEL=claude-opus-5\n",
        env_extra={"REVIEW_HARNESS": "codex", "REVIEW_MODEL": "gpt-5.5"},
    )
    assert cmd == "codex exec -m gpt-5.5"


def test_resolution_cli_beats_env_and_config(tmp_path: Path) -> None:
    cmd = _resolve(
        tmp_path,
        argv=["--harness", "claude", "--model", "claude-sonnet-4-6"],
        cfg="REVIEW_HARNESS=codex\n",
        env_extra={"REVIEW_HARNESS": "codex"},
    )
    assert cmd == "claude-review --model claude-sonnet-4-6"


def test_resolution_external_review_cmd_wins_without_flags(
    tmp_path: Path,
) -> None:
    cmd = _resolve(
        tmp_path,
        cfg="REVIEW_HARNESS=claude\n",
        env_extra={"REVIEW_CMD": "codex exec --model спец"},
    )
    assert cmd == "codex exec --model спец"


def test_resolution_codex_without_model_keeps_kit_default(
    tmp_path: Path,
) -> None:
    """Без модели REVIEW_CMD не выставляется — историческая строка
    `codex exec` в отпечатках опубликованных вердиктов остаётся валидной."""
    cmd = _resolve(tmp_path, cfg="REVIEW_HARNESS=codex\n")
    assert cmd == "codex exec"


def test_resolution_unknown_harness_is_config_error(tmp_path: Path) -> None:
    cfg_path = tmp_path / "harness.env"
    cfg_path.write_text("REVIEW_HARNESS=gemini\n", encoding="utf-8")
    env = {**os.environ, "AI_PROSTO_HARNESS_ENV": str(cfg_path)}
    # Герметичность от оболочки оператора (боевое claude-ревью PR #121,
    # круг 2): env-слой перекрыл бы конфиг, и die 2 не достигался бы.
    env.pop("REVIEW_CMD", None)
    env.pop("REVIEW_HARNESS", None)
    env.pop("REVIEW_MODEL", None)
    res = subprocess.run(
        ["sh", str(REVIEW_PR), "dummy", "1", "--print-review-cmd"],
        capture_output=True, text=True, env=env,
    )
    assert res.returncode == 2
    assert "gemini" in res.stderr


def test_shim_is_executable() -> None:
    """Кит зовёт `claude-review` голым именем через PATH — файл обязан быть
    исполняемым (боевой смоук devtools#106: Permission denied)."""
    assert os.access(SHIM, os.X_OK)


def test_resolution_explicit_codex_ignores_foreign_model(tmp_path: Path) -> None:
    """Боевое claude-ревью PR #121 (minor 1): `--harness codex` при конфиге
    claude+claude-opus-5 НЕ наследует чужую модель — иначе собрался бы
    `codex exec -m claude-opus-5` и умер на неизвестной модели."""
    cmd = _resolve(
        tmp_path,
        argv=["--harness", "codex"],
        cfg="REVIEW_HARNESS=claude\nREVIEW_MODEL=claude-opus-5\n",
    )
    assert cmd == "codex exec"


def test_resolution_explicit_flag_beats_external_review_cmd(
    tmp_path: Path,
) -> None:
    """Боевое claude-ревью PR #121 (minor 2): явный `--harness codex`
    перекрывает и внешний REVIEW_CMD — «флаг побеждает» из usage верен."""
    cmd = _resolve(
        tmp_path,
        argv=["--harness", "codex"],
        env_extra={"REVIEW_CMD": "claude-review --model claude-opus-5"},
    )
    assert cmd == "codex exec"


def test_resolution_env_harness_ignores_config_model(tmp_path: Path) -> None:
    """Харнесс со слоя env не наследует модель слоя конфига."""
    cmd = _resolve(
        tmp_path,
        cfg="REVIEW_HARNESS=claude\nREVIEW_MODEL=claude-opus-5\n",
        env_extra={"REVIEW_HARNESS": "codex"},
    )
    assert cmd == "codex exec"


def test_config_accepts_export_prefix_and_indent(tmp_path: Path) -> None:
    """Боевое claude-ревью PR #121, круг 2: env-файловая запись
    `export KEY=value` (и ведущие пробелы) принимается — молчаливый откат
    на codex сжигал бы лимит без единого предупреждения."""
    cmd = _resolve(
        tmp_path,
        cfg="  export REVIEW_HARNESS=claude\nexport REVIEW_MODEL=claude-opus-5\n",
    )
    assert cmd == "claude-review --model claude-opus-5"


# --- review-pr.sh: свежий кит (devtools#222) ---------------------------------


def _kit_env(log: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in log.read_text().splitlines())


def test_new_kit_default_is_codex_via_kit_env(tmp_path: Path) -> None:
    fleet_root, log = _new_kit_fleet(tmp_path)
    cmd = _resolve(tmp_path, repo="demo", env_extra={"HARNESS_KIT_LOG": str(log)})
    assert cmd == "codex exec"
    env = _kit_env(log)
    assert env["REVIEW_HARNESS"] == "codex"
    assert env["REVIEW_MODEL"] == "<unset>"
    assert env["REVIEW_CMD"] == "<unset>"


def test_new_kit_claude_config_goes_through_kit_not_shim(tmp_path: Path) -> None:
    """Свежий кит: строка ревьюера — от кита (`harness-claude`), окружение
    несёт REVIEW_HARNESS/REVIEW_MODEL, REVIEW_CMD не собирается и
    scripts/harness в PATH не подмешивается."""
    fleet_root, log = _new_kit_fleet(tmp_path)
    cmd = _resolve(
        tmp_path,
        repo="demo",
        cfg="REVIEW_HARNESS=claude\nREVIEW_MODEL=claude-opus-5\n",
        env_extra={"HARNESS_KIT_LOG": str(log)},
    )
    assert cmd == "harness-claude --model claude-opus-5"
    env = _kit_env(log)
    assert env["REVIEW_HARNESS"] == "claude"
    assert env["REVIEW_MODEL"] == "claude-opus-5"
    assert env["REVIEW_CMD"] == "<unset>"
    assert "scripts/harness" not in env["PATH"]
    # Кит зовётся из чекаута репо (как run_kit), не из cwd оператора.
    assert Path(env["CWD"]) == (fleet_root / "demo").resolve()


def test_new_kit_cli_flags_beat_env_and_config(tmp_path: Path) -> None:
    fleet_root, log = _new_kit_fleet(tmp_path)
    cmd = _resolve(
        tmp_path,
        repo="demo",
        argv=["--harness", "claude", "--model", "claude-sonnet-4-6"],
        cfg="REVIEW_HARNESS=codex\n",
        env_extra={"REVIEW_HARNESS": "codex", "REVIEW_MODEL": "gpt-5.5", "HARNESS_KIT_LOG": str(log)},
    )
    assert cmd == "harness-claude --model claude-sonnet-4-6"
    assert _kit_env(log)["REVIEW_MODEL"] == "claude-sonnet-4-6"


def test_new_kit_harness_flag_without_model_does_not_inherit_env_model(
    tmp_path: Path,
) -> None:
    """Модель привязана к слою: `--harness codex` при REVIEW_MODEL=claude-opus-5
    в окружении обязан дать `codex exec`, а не `codex exec -m claude-opus-5`
    (и не пустую REVIEW_MODEL, которую кит отверг бы кодом 2)."""
    fleet_root, log = _new_kit_fleet(tmp_path)
    cmd = _resolve(
        tmp_path,
        repo="demo",
        argv=["--harness", "codex"],
        env_extra={"REVIEW_HARNESS": "claude", "REVIEW_MODEL": "claude-opus-5", "HARNESS_KIT_LOG": str(log)},
    )
    assert cmd == "codex exec"
    assert _kit_env(log)["REVIEW_MODEL"] == "<unset>"


def test_new_kit_external_review_cmd_wins_without_flags(tmp_path: Path) -> None:
    fleet_root, log = _new_kit_fleet(tmp_path)
    cmd = _resolve(
        tmp_path,
        repo="demo",
        cfg="REVIEW_HARNESS=claude\n",
        env_extra={"REVIEW_CMD": "codex exec --model спец", "HARNESS_KIT_LOG": str(log)},
    )
    assert cmd == "codex exec --model спец"
    assert not log.exists(), "с внешним REVIEW_CMD кит для строки не зовётся"


def test_new_kit_refusal_is_propagated_not_guessed(tmp_path: Path) -> None:
    """Кит отказал (код 2, например адаптера harness-claude нет) —
    review-pr.sh передаёт отказ и не подставляет строку ревьюера сам."""
    fleet_root, log = _new_kit_fleet(tmp_path)
    err = _resolve(
        tmp_path,
        repo="demo",
        argv=["--harness", "claude"],
        env_extra={"HARNESS_KIT_LOG": str(log), "REVIEW_KIT_STUB_FAIL": "1"},
        expect_rc=2,
    )
    assert "кит отказал: адаптера нет" in err
    assert "print-review-cmd" in err


def test_old_kit_keeps_shim_path(tmp_path: Path) -> None:
    """Старая копия кита (без литерала --print-review-cmd) — прежняя ветка:
    REVIEW_CMD с голым `claude-review` и переходник в PATH."""
    fleet_root = tmp_path / "fleet"
    kit = fleet_root / "demo" / "scripts" / "review"
    kit.mkdir(parents=True)
    (kit / "local.sh").write_text("#!/bin/sh\necho old kit\n", encoding="utf-8")
    cmd = _resolve(tmp_path, repo="demo", cfg="REVIEW_HARNESS=claude\n")
    assert cmd == "claude-review --model claude-opus-5"
