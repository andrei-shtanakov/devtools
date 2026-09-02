"""Тесты spec_run_preflight — преflight перед прогоном spec-runner.

Ретроспектива 2026-09-02 (@id:spec-run-preflight): каждый чек — прожитый
режим отказа (уроки 4–5 devtools#110, ssh-зависание, spec-runner#337).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import spec_run_preflight as pf


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return path


def _set_insteadof(repo: Path) -> None:
    # Ключ без кавычек — как настоящая настройка из CLAUDE.md, сделанная
    # в шелле (шелл снимает кавычки; в argv их быть не должно).
    subprocess.run(
        ["git", "-C", str(repo), "config",
         "url.https://github.com/.insteadOf", "git@github.com:"],
        check=True,
    )


def _levels(findings: list[pf.Finding], check: str) -> list[str]:
    return [f.level for f in findings if f.check == check]


# --- config-etalon (урок 4) -------------------------------------------------


def test_etalon_without_config_fails(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "r")
    (repo / "spec-runner.config.example.yaml").write_text("x: 1\n")
    assert _levels(pf.check_config_etalon(repo), "config-etalon") == ["FAIL"]


def test_etalon_with_conformant_config_is_clean(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "r")
    etalon = "executor:\n  execution_mode: tdd\n  tdd_runner: pytest\n"
    (repo / "spec-runner.config.example.yaml").write_text(etalon)
    (repo / "spec-runner.config.yaml").write_text(etalon)
    assert pf.check_config_etalon(repo) == []


def test_config_missing_critical_etalon_keys_fails(tmp_path: Path) -> None:
    """Приёмка PR #115: наличие файла — не соответствие. Конфиг от голого
    `--preset` существует, но без TDD-цепочки эталона — урок 4 повторится
    молча. Отсутствие критических ключей эталона — FAIL с их именами."""
    repo = _git_repo(tmp_path / "r")
    (repo / "spec-runner.config.example.yaml").write_text(
        "executor:\n"
        "  execution_mode: tdd\n"
        "  tdd_runner: pytest\n"
        "review_policy: required\n"
    )
    (repo / "spec-runner.config.yaml").write_text("model: sonnet\n")
    findings = pf.check_config_etalon(repo)
    assert _levels(findings, "config-etalon") == ["FAIL"]
    detail = findings[0].detail
    assert "execution_mode" in detail
    assert "review_policy" in detail


def test_etalon_without_critical_keys_accepts_any_config(
    tmp_path: Path,
) -> None:
    """Эталон без критических ключей ничего не требует от конфига."""
    repo = _git_repo(tmp_path / "r")
    (repo / "spec-runner.config.example.yaml").write_text("model: sonnet\n")
    (repo / "spec-runner.config.yaml").write_text("x: 1\n")
    assert pf.check_config_etalon(repo) == []


def test_no_etalon_is_clean_even_without_config(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "r")
    assert pf.check_config_etalon(repo) == []


def test_workstream_setup_doc_counts_as_etalon(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "r")
    (repo / "docs").mkdir()
    (repo / "docs" / "workstream-setup.md").write_text("# setup\n")
    assert _levels(pf.check_config_etalon(repo), "config-etalon") == ["FAIL"]


# --- insteadof-https (класс ssh-зависаний) ----------------------------------


def test_missing_insteadof_fails_with_exact_command(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "r")
    findings = pf.check_insteadof(repo)
    assert _levels(findings, "insteadof-https") == ["FAIL"]
    assert "git -C" in findings[0].detail  # команда для копипасты


def test_configured_insteadof_is_clean(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "r")
    _set_insteadof(repo)
    assert pf.check_insteadof(repo) == []


# --- prefixless-db (spec-runner#337) ----------------------------------------


def test_prefixless_db_next_to_prefixed_fails(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "r")
    spec = repo / "spec"
    spec.mkdir()
    (spec / ".executor-state.db").write_bytes(b"")
    (spec / ".executor-WS-1-state.db").write_bytes(b"data")
    findings = pf.check_prefixless_db(repo)
    assert _levels(findings, "prefixless-db") == ["FAIL"]
    assert "безопасно удалить" in findings[0].detail  # пустая


def test_nonempty_prefixless_db_warns_against_blind_delete(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path / "r")
    spec = repo / "spec"
    spec.mkdir()
    (spec / ".executor-state.db").write_bytes(b"sqlite")
    (spec / ".executor-WS-1-state.db").write_bytes(b"data")
    findings = pf.check_prefixless_db(repo)
    assert _levels(findings, "prefixless-db") == ["FAIL"]
    assert "НЕ пустая" in findings[0].detail


def test_prefixless_db_alone_is_clean(tmp_path: Path) -> None:
    """Репо, живущее БЕЗ --spec-prefix, легитимно держит дефолтную базу."""
    repo = _git_repo(tmp_path / "r")
    spec = repo / "spec"
    spec.mkdir()
    (spec / ".executor-state.db").write_bytes(b"sqlite")
    assert pf.check_prefixless_db(repo) == []


def test_only_prefixed_dbs_are_clean(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "r")
    spec = repo / "spec"
    spec.mkdir()
    (spec / ".executor-WS-1-state.db").write_bytes(b"data")
    assert pf.check_prefixless_db(repo) == []


# --- live-smoke-env (урок 5) ------------------------------------------------


def test_pinned_install_scripts_warn(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "r")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "install_pinned_checker.sh").write_text("#!/bin/sh\n")
    findings = pf.check_live_smoke_env(repo)
    assert _levels(findings, "live-smoke-env") == ["WARN"]
    assert "install_pinned_checker.sh" in findings[0].detail


def test_no_install_scripts_is_silent(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "r")
    assert pf.check_live_smoke_env(repo) == []


# --- main: exit-коды ---------------------------------------------------------


def test_main_green_repo_exits_zero(tmp_path: Path, capsys) -> None:
    repo = _git_repo(tmp_path / "clean")
    _set_insteadof(repo)
    rc = pf.main(["--repo", "clean", "--workspace", str(tmp_path)])
    assert rc == 0
    assert "готов" in capsys.readouterr().out


def test_main_fail_exits_one(tmp_path: Path, capsys) -> None:
    repo = _git_repo(tmp_path / "bad")
    (repo / "spec-runner.config.example.yaml").write_text("x: 1\n")
    _set_insteadof(repo)
    rc = pf.main(["--repo", "bad", "--workspace", str(tmp_path)])
    assert rc == 1
    assert "FAIL" in capsys.readouterr().out


def test_main_warn_only_exits_zero(tmp_path: Path, capsys) -> None:
    repo = _git_repo(tmp_path / "warny")
    _set_insteadof(repo)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "install_pinned_steward.sh").write_text("#!/bin/sh\n")
    rc = pf.main(["--repo", "warny", "--workspace", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[WARN]" in out and "готов" in out


def test_main_missing_repo_exits_two(tmp_path: Path, capsys) -> None:
    rc = pf.main(["--repo", "ghost", "--workspace", str(tmp_path)])
    assert rc == 2
    assert "не найден" in capsys.readouterr().out


def test_quoted_yaml_keys_are_recognized(tmp_path: Path) -> None:
    """Приёмка PR #115, круг 2: кавычная форма ключа — валидный YAML;
    кавычный эталон не должен опустошать required."""
    repo = _git_repo(tmp_path / "r")
    (repo / "spec-runner.config.example.yaml").write_text(
        '"execution_mode": tdd\n\'tdd_runner\': pytest\n'
    )
    (repo / "spec-runner.config.yaml").write_text("model: sonnet\n")
    findings = pf.check_config_etalon(repo)
    assert _levels(findings, "config-etalon") == ["FAIL"]
    assert "execution_mode" in findings[0].detail
    assert "tdd_runner" in findings[0].detail


def test_mismatched_quotes_are_not_a_key(tmp_path: Path) -> None:
    """`"key':` — не валидное объявление ключа; в required не попадает."""
    repo = _git_repo(tmp_path / "r")
    (repo / "spec-runner.config.example.yaml").write_text(
        "\"execution_mode': tdd\n"
    )
    (repo / "spec-runner.config.yaml").write_text("model: sonnet\n")
    assert pf.check_config_etalon(repo) == []


def test_git_status_failure_is_fail_not_clean(tmp_path: Path) -> None:
    """Приёмка PR #115, круг 2: сбой git status — не «чисто», а FAIL:
    состояние дерева неопределимо. Каталог с .git-файлом, указывающим в
    никуда, валит любой git-вызов."""
    repo = tmp_path / "broken"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: /nonexistent/gitdir\n")
    findings = pf.check_dirty_tree(repo)
    assert _levels(findings, "dirty-tree") == ["FAIL"]
    assert "неопределимо" in findings[0].detail


def test_wrong_scalar_value_of_critical_key_fails(tmp_path: Path) -> None:
    """Приёмка PR #115, круг 3: `execution_mode: direct` при эталонном
    `tdd` ломает TDD-цепочку так же, как отсутствие ключа."""
    repo = _git_repo(tmp_path / "r")
    (repo / "spec-runner.config.example.yaml").write_text(
        "executor:\n  execution_mode: tdd\n  tdd_runner: pytest\n"
    )
    (repo / "spec-runner.config.yaml").write_text(
        "executor:\n  execution_mode: direct\n  tdd_runner: pytest\n"
    )
    findings = pf.check_config_etalon(repo)
    assert _levels(findings, "config-etalon") == ["FAIL"]
    assert "execution_mode" in findings[0].detail
    assert "direct" in findings[0].detail and "tdd" in findings[0].detail


def test_block_valued_key_is_checked_by_name_only(tmp_path: Path) -> None:
    """harness_files-список: значение блочное — сверка только по имени,
    разный состав списков соответствия не рушит."""
    repo = _git_repo(tmp_path / "r")
    (repo / "spec-runner.config.example.yaml").write_text(
        "harness_files:\n  - a.py\n  - b.py\n"
    )
    (repo / "spec-runner.config.yaml").write_text(
        "harness_files:\n  - c.py\n"
    )
    assert pf.check_config_etalon(repo) == []


def test_matching_values_with_comment_and_quotes_pass(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "r")
    (repo / "spec-runner.config.example.yaml").write_text(
        "review_policy: required  # обязательное ревью\n"
    )
    (repo / "spec-runner.config.yaml").write_text(
        'review_policy: "required"\n'
    )
    assert pf.check_config_etalon(repo) == []


def test_empty_scalar_value_of_critical_key_fails(tmp_path: Path) -> None:
    """Приёмка PR #115, круг 4: `execution_mode:` без значения не задаёт
    режим так же, как отсутствие ключа — для скалярного в эталоне ключа
    это FAIL, а не соответствие."""
    repo = _git_repo(tmp_path / "r")
    (repo / "spec-runner.config.example.yaml").write_text(
        "execution_mode: tdd\n"
    )
    (repo / "spec-runner.config.yaml").write_text("execution_mode:\n")
    findings = pf.check_config_etalon(repo)
    assert _levels(findings, "config-etalon") == ["FAIL"]
    assert "пустое/блочное" in findings[0].detail
