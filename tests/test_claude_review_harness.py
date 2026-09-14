"""Тесты харнесс-слоя ревьюера в review-pr.sh: резолв харнесса (флаг > env >
~/.config/ai-prosto/harness.env > codex) и материализация под кит steward с
харнесс-слоем (REVIEW_HARNESS/REVIEW_MODEL окружением, строка ревьюера — от
`local.sh --print-review-cmd`). Переходник scripts/harness/claude-review снят
после волны devtools#228 (шаг 2 devtools#222): копия кита без харнесс-слоя
умеет только codex, claude на ней — отказ «ре-вендорьте кит».
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


DEVTOOLS = Path(__file__).resolve().parent.parent
REVIEW_PR = DEVTOOLS / "review-pr.sh"
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
    repo: str = "demo",
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
    if repo == "demo" and not (tmp_path / "fleet" / "demo").exists():
        # Резолв без явного кита — на свежем ките по умолчанию: после волны
        # devtools#228 это единственная штатная форма для claude.
        _, log = _new_kit_fleet(tmp_path)
        env.setdefault("HARNESS_KIT_LOG", str(log))
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
    assert cmd == "harness-claude --model claude-opus-5"


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
    assert cmd == "harness-claude --model claude-sonnet-4-6"


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
    assert cmd == "harness-claude --model claude-opus-5"


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


def _old_kit_fleet(tmp_path: Path) -> Path:
    fleet_root = tmp_path / "fleet"
    kit = fleet_root / "demo" / "scripts" / "review"
    kit.mkdir(parents=True)
    (kit / "local.sh").write_text("#!/bin/sh\necho old kit\n", encoding="utf-8")
    return fleet_root


def test_old_kit_codex_still_works(tmp_path: Path) -> None:
    """Копия кита без харнесс-слоя (нет литерала --print-review-cmd): codex —
    как раньше, REVIEW_CMD только при заданной модели."""
    _old_kit_fleet(tmp_path)
    assert _resolve(tmp_path, cfg="REVIEW_HARNESS=codex\n") == "codex exec"
    assert _resolve(tmp_path, argv=["--harness", "codex", "--model", "gpt-5.5"]) == "codex exec -m gpt-5.5"


def test_old_kit_claude_is_refused_not_shimmed(tmp_path: Path) -> None:
    """Переходник снят (devtools#228): claude на ките без харнесс-слоя —
    отказ кодом 2 с указанием ре-вендорить, а не тихий уход на codex."""
    _old_kit_fleet(tmp_path)
    err = _resolve(tmp_path, cfg="REVIEW_HARNESS=claude\n", expect_rc=2)
    assert "без харнесс-слоя" in err and "a2d7e71" in err


def test_no_kit_checkout_claude_is_refused(tmp_path: Path) -> None:
    err = _resolve(tmp_path, repo="nowhere", argv=["--harness", "claude"], expect_rc=2)
    assert "без харнесс-слоя" in err


def test_new_kit_probe_honours_absolute_review_kit_dir(tmp_path: Path) -> None:
    """Абсолютный REVIEW_KIT_DIR (как в прогоне через resolve_from_source)
    зонд берёт как есть — не склеивает с чекаутом и не уходит молча в ветку
    старого кита."""
    fleet_root, log = _new_kit_fleet(tmp_path)
    elsewhere = tmp_path / "kit-elsewhere"
    elsewhere.mkdir()
    local_sh = elsewhere / "local.sh"
    local_sh.write_text(NEW_KIT_STUB, encoding="utf-8")
    local_sh.chmod(local_sh.stat().st_mode | stat.S_IXUSR)
    cmd = _resolve(
        tmp_path,
        repo="demo",
        cfg="REVIEW_HARNESS=claude\n",
        env_extra={"HARNESS_KIT_LOG": str(log), "REVIEW_KIT_DIR": str(elsewhere)},
    )
    assert cmd == "harness-claude --model claude-opus-5"
    assert _kit_env(log)["REVIEW_HARNESS"] == "claude"
