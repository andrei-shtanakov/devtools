"""Тесты review-pr.sh — терминальный прогон codex-ревью PR с публикацией
вердикта ревью от отдельного аккаунта (профиль GH_CONFIG_DIR).

Стратегия: git — настоящий (bare-репо играет роль origin, ref
`refs/pull/N/head` создаётся руками, как это делает GitHub); `gh` и китовый
`scripts/review/local.sh` — стабы, управляемые переменными окружения и
пишущие свои argv в лог-файлы. Так проверяется именно обвязка: маппинг кодов
выхода кита на действие ревью, guard «голова уехала», отказ публиковать при
сломанном ревьюере.

Контракт кодов выхода review-pr.sh:
  0 — чисто, approve опубликован (или dry-run);
  1 — blocker/major, request-changes опубликован (или dry-run);
  2 — конфигурация/аргументы/состояние PR/публикация;
  3 — ревьюер не отработал (проброс из кита);
  4 — голова PR уехала между прогоном и публикацией.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "review-pr.sh"

GH_STUB = """#!/usr/bin/env bash
# Стаб gh: логирует каждый вызов, отвечает по переменным GH_STUB_*.
echo "GH_CONFIG_DIR=${GH_CONFIG_DIR:-} gh $*" >> "$GH_STUB_LOG"
case "$*" in
  *"/reviews"*)
    # Ревью PR: сырой JSON страниц, как отдаёт настоящий `gh api --paginate`
    # БЕЗ --jq (devtools#75: gh отвергает --slurp+--jq, фильтр теперь гоняет
    # сам скрипт внешним jq — и тестируется всерьёз). Комбинация с --slurp
    # воспроизводит отказ настоящего gh 2.83.1.
    case "$*" in
      *--slurp*)
        echo "the --slurp option is not supported with --jq or --template" >&2
        exit 1 ;;
    esac
    if [ -n "${GH_STUB_REVIEWS_JSON:-}" ] && [ -f "$GH_STUB_REVIEWS_JSON" ]; then
      cat "$GH_STUB_REVIEWS_JSON"
    else
      echo '[]'
    fi ;;
  *"api user"*)
    echo "${GH_STUB_LOGIN:-ai-prosto}" ;;
  *baseRefName*)
    echo "${GH_STUB_BASEREF:-master} ${GH_STUB_HEADOID:?} ${GH_STUB_STATE:-OPEN}" ;;
  *headRefOid*)
    echo "${GH_STUB_HEADOID2:-${GH_STUB_HEADOID:?}}" ;;
  *"pr review"*)
    prev=""
    for a in "$@"; do
      if [ "$prev" = "--body-file" ] && [ -n "${GH_STUB_BODY_OUT:-}" ]; then
        cp "$a" "$GH_STUB_BODY_OUT"
      fi
      prev="$a"
    done ;;
esac
"""

LOCAL_SH_STUB = """#!/bin/sh
# Стаб кита: логирует argv, отдаёт управляемый вердикт и код выхода.
# НЕ содержит литерала fp-флага: feature-detect обязан видеть старый кит.
echo "local.sh $*" >> "$LOCAL_SH_LOG"
echo "stub verdict body"
if [ -n "${REVIEW_STUB_ERR:-}" ]; then
  echo "$REVIEW_STUB_ERR" >&2
fi
exit "${REVIEW_STUB_EXIT:-0}"
"""

LOCAL_SH_FP_STUB = """#!/bin/sh
# Стаб fp-кита: содержит литерал --fingerprint-only (feature-detect греппит
# файл), в fp-режиме отдаёт управляемый отпечаток и код.
echo "local.sh $*" >> "$LOCAL_SH_LOG"
case " $* " in
  *" --fingerprint-only "*)
    if [ -n "${REVIEW_STUB_FP_ERR:-}" ]; then
      echo "$REVIEW_STUB_FP_ERR" >&2
    fi
    if [ -n "${REVIEW_STUB_FP:-}" ]; then
      echo "$REVIEW_STUB_FP"
    fi
    exit "${REVIEW_STUB_FP_EXIT:-0}" ;;
esac
echo "stub verdict body"
if [ -n "${REVIEW_STUB_ERR:-}" ]; then
  echo "$REVIEW_STUB_ERR" >&2
fi
exit "${REVIEW_STUB_EXIT:-0}"
"""

LOCAL_SH_TREE_STUB = """#!/bin/sh
# Этот кит существует только в исходном чекауте: PR-head его не содержит.
# Лог доказывает одновременно две половины контракта #166: исполняется
# доверенный файл отсюда, а cwd/рабочее дерево принадлежит exact PR head.
echo "local.sh $*" >> "$LOCAL_SH_LOG"
{
  echo "script=$0"
  echo "cwd=$(pwd)"
  echo "head=$(git rev-parse HEAD)"
  echo "content=$(cat a.txt)"
} > "$REVIEW_STUB_TREE_LOG"
echo "stub verdict body"
exit 0
"""

LOCAL_SH_RELATIVE_CONFIG_STUB = """#!/bin/sh
# Относительные операторские override-пути должны быть привязаны к исходному
# доверенному checkout, хотя cwd кита находится в exact-head worktree.
{
  echo "script=$0"
  echo "kit=$REVIEW_KIT_DIR"
  echo "schema=$REVIEW_SCHEMA"
  echo "prompt=$REVIEW_PROMPT"
  echo "schema_content=$(cat "$REVIEW_SCHEMA")"
  echo "prompt_content=$(cat "$REVIEW_PROMPT")"
} > "$REVIEW_STUB_CONFIG_LOG"
echo "stub verdict body"
exit 0
"""


def _git(*args: str, cwd: Path) -> str:
    """Запустить git и вернуть stdout (строго, с проверкой кода)."""
    res = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return res.stdout.strip()


class Fleet:
    """Синтетический флот: bare-origin + клон demo с китом-стабом."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.origin = tmp_path / "origin.git"
        self.fleet_root = tmp_path / "fleet"
        self.repo = self.fleet_root / "demo"
        self.stub_bin = tmp_path / "bin"
        self.gh_log = tmp_path / "gh.log"
        self.local_log = tmp_path / "local-sh.log"
        self.body_out = tmp_path / "posted-body.md"
        self.profile_dir = tmp_path / "gh-profile"
        self.profile_dir.mkdir()

        subprocess.run(
            ["git", "init", "--bare", "-b", "master", str(self.origin)],
            check=True,
            capture_output=True,
        )
        seed = tmp_path / "seed"
        seed.mkdir()
        _git("init", "-b", "master", cwd=seed)
        _git("config", "user.email", "t@example.com", cwd=seed)
        _git("config", "user.name", "t", cwd=seed)
        (seed / "a.txt").write_text("base\n")
        _git("add", ".", cwd=seed)
        _git("commit", "-m", "base", cwd=seed)
        _git("remote", "add", "origin", str(self.origin), cwd=seed)
        _git("push", "-q", "origin", "master", cwd=seed)
        # Ветка PR: один коммит поверх master, выложен как refs/pull/7/head —
        # ровно так PR выглядит на настоящем GitHub-remote.
        (seed / "a.txt").write_text("changed\n")
        _git("commit", "-am", "pr change", cwd=seed)
        self.head_sha = _git("rev-parse", "HEAD", cwd=seed)
        _git("push", "-q", "origin", "HEAD:refs/pull/7/head", cwd=seed)

        self.fleet_root.mkdir()
        subprocess.run(
            ["git", "clone", "-q", str(self.origin), str(self.repo)],
            check=True,
            capture_output=True,
        )
        # Как в проде: origin смотрит на GitHub (из него выводится slug),
        # а insteadOf молча перенаправляет фетч в локальный bare — сети нет.
        gh_url = "git@github.com:andrei-shtanakov/demo.git"
        _git("remote", "set-url", "origin", gh_url, cwd=self.repo)
        _git("config", f"url.{self.origin}.insteadOf", gh_url, cwd=self.repo)
        self.write_kit()

        self.stub_bin.mkdir()
        gh = self.stub_bin / "gh"
        gh.write_text(GH_STUB)
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)

    def write_kit(self, text: str = LOCAL_SH_STUB) -> None:
        kit = self.repo / "scripts" / "review"
        kit.mkdir(parents=True, exist_ok=True)
        local_sh = kit / "local.sh"
        local_sh.write_text(text)
        local_sh.chmod(local_sh.stat().st_mode | stat.S_IXUSR)

    def write_reviews(self, *reviews: dict) -> str:
        """Канированный ответ reviews-эндпоинта: сырая страница, как отдаёт
        `gh api --paginate` без --jq (массив ревью; jq -s скрипта завернёт
        поток страниц сам)."""
        import json

        path = self.tmp / "reviews.json"
        path.write_text(json.dumps(list(reviews)))
        return str(path)

    def env(self, **extra: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            PATH=f"{self.stub_bin}:{env['PATH']}",
            FLEET_ROOT=str(self.fleet_root),
            REVIEW_GH_CONFIG_DIR=str(self.profile_dir),
            GH_STUB_LOG=str(self.gh_log),
            GH_STUB_HEADOID=self.head_sha,
            GH_STUB_BODY_OUT=str(self.body_out),
            LOCAL_SH_LOG=str(self.local_log),
            GIT_TERMINAL_PROMPT="0",
            GIT_SSH_COMMAND="false",
            # Герметичность от операторского конфига харнесса (devtools#121):
            # реальный ~/.config/ai-prosto/harness.env не должен влиять на
            # тесты — резолюция идёт от вшитого дефолта codex.
            AI_PROSTO_HARNESS_ENV=str(self.tmp / "no-harness.env"),
            # Бюджет платных прогонов ведётся на диске; тесты обязаны быть
            # герметичны от состояния машины оператора и друг от друга.
            REVIEW_BUDGET_DIR=str(self.tmp / "review-budget"),
        )
        # …и от env-слоя оболочки оператора (ревью #231): экспортированный
        # REVIEW_HARNESS=claude на ките-стабе без харнесс-слоя дал бы код 2
        # во всех full-run тестах, а внешний REVIEW_CMD — чужую строку.
        # Потолки дифа (ревью #250) — тот же класс: экспортированный
        # REVIEW_MAX_DIFF_BYTES оператора валил бы full-run тесты на стабе
        # кита без литерала --max-diff-bytes кодом 2.
        for key in (
            "REVIEW_HARNESS", "REVIEW_MODEL", "REVIEW_CMD",
            "REVIEW_MAX_DIFF_BYTES", "REVIEW_MAX_DIFF_FILES",
        ):
            env.pop(key, None)
        env.update(extra)
        return env

    def run(self, *args: str, **env_extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", str(SCRIPT), *args],
            env=self.env(**env_extra),
            capture_output=True,
            text=True,
            check=False,  # код выхода — предмет проверки самих тестов
        )

    def gh_calls(self) -> str:
        return self.gh_log.read_text() if self.gh_log.exists() else ""


@pytest.fixture
def fleet(tmp_path: Path) -> Fleet:
    return Fleet(tmp_path)


def test_no_args_usage(fleet: Fleet) -> None:
    res = fleet.run()
    assert res.returncode == 2
    assert "usage:" in res.stderr


def test_non_numeric_pr(fleet: Fleet) -> None:
    res = fleet.run("demo", "abc")
    assert res.returncode == 2


def test_unknown_repo(fleet: Fleet) -> None:
    res = fleet.run("nosuch", "7")
    assert res.returncode == 2
    assert "nosuch" in res.stderr


def test_repo_without_kit(fleet: Fleet) -> None:
    kit = fleet.repo / "scripts" / "review" / "local.sh"
    kit.unlink()
    res = fleet.run("demo", "7")
    assert res.returncode == 2
    assert "local.sh" in res.stderr


def test_missing_profile_dir(fleet: Fleet) -> None:
    res = fleet.run("demo", "7", REVIEW_GH_CONFIG_DIR=str(fleet.tmp / "nope"))
    assert res.returncode == 2
    assert "gh auth login" in res.stderr


def test_wrong_login_refuses(fleet: Fleet) -> None:
    res = fleet.run("demo", "7", GH_STUB_LOGIN="somebody-else")
    assert res.returncode == 2
    assert "somebody-else" in res.stderr
    assert "pr review" not in fleet.gh_calls()


def test_pr_not_open(fleet: Fleet) -> None:
    res = fleet.run("demo", "7", GH_STUB_STATE="MERGED")
    assert res.returncode == 2
    assert "MERGED" in res.stderr
    assert "pr review" not in fleet.gh_calls()


def test_clean_verdict_approves(fleet: Fleet) -> None:
    res = fleet.run("demo", "7")
    assert res.returncode == 0, res.stderr
    calls = fleet.gh_calls()
    assert "pr review 7" in calls
    assert "--approve" in calls
    assert "--repo andrei-shtanakov/demo" in calls
    # Публикация — единственные вызовы gh, и все под профилем ревью.
    for line in calls.splitlines():
        assert f"GH_CONFIG_DIR={fleet.profile_dir}" in line
    body = fleet.body_out.read_text()
    assert "stub verdict body" in body
    assert fleet.head_sha in body
    assert "codex-terminal-review" in body


def test_kit_receives_pr_range(fleet: Fleet) -> None:
    fleet.run("demo", "7")
    call = fleet.local_log.read_text()
    assert "--base origin/master" in call
    assert "--fetch" in call
    assert "--head refs/review/pr-7" in call
    assert "--format markdown" in call


def test_reviewer_reads_exact_pr_head_from_ephemeral_worktree(
    fleet: Fleet,
) -> None:
    """devtools#166: diff и дерево ревьюера относятся к одному head.

    Кит берётся из исходного доверенного чекаута: синтетический PR-head не
    содержит scripts/review/local.sh вовсе. Его cwd при этом — временный
    detached worktree exact head; основной чекаут остаётся на master и после
    выхода временный worktree зарегистрированным не остаётся.
    """
    fleet.write_kit(LOCAL_SH_TREE_STUB)
    tree_log = fleet.tmp / "review-tree.log"

    res = fleet.run(
        "demo", "7", REVIEW_STUB_TREE_LOG=str(tree_log),
    )

    assert res.returncode == 0, res.stderr
    facts = dict(
        line.split("=", 1) for line in tree_log.read_text().splitlines()
    )
    assert facts["script"] == str(fleet.repo / "scripts/review/local.sh")
    assert facts["head"] == fleet.head_sha
    assert facts["content"] == "changed"
    assert Path(facts["cwd"]) != fleet.repo
    assert _git("branch", "--show-current", cwd=fleet.repo) == "master"
    assert (fleet.repo / "a.txt").read_text() == "base\n"
    worktrees = _git("worktree", "list", "--porcelain", cwd=fleet.repo)
    assert worktrees.count("worktree ") == 1


def test_relative_review_config_resolves_from_trusted_checkout(
    fleet: Fleet,
) -> None:
    """Относительные overrides сохраняют прежнюю базу разрешения.

    Ни custom kit, ни prompt/schema не входят в PR-head. Если хотя бы один
    путь начнёт разрешаться от ephemeral worktree, прогон завершится ошибкой.
    """
    kit = fleet.repo / "trusted" / "review"
    kit.mkdir(parents=True)
    local_sh = kit / "local.sh"
    local_sh.write_text(LOCAL_SH_RELATIVE_CONFIG_STUB)
    local_sh.chmod(local_sh.stat().st_mode | stat.S_IXUSR)
    config = fleet.repo / "trusted" / "config"
    config.mkdir()
    (config / "schema.json").write_text("trusted schema\n")
    (config / "prompt.md").write_text("trusted prompt\n")
    config_log = fleet.tmp / "review-config.log"

    res = fleet.run(
        "demo",
        "7",
        REVIEW_KIT_DIR="trusted/review",
        REVIEW_SCHEMA="trusted/config/schema.json",
        REVIEW_PROMPT="trusted/config/prompt.md",
        REVIEW_STUB_CONFIG_LOG=str(config_log),
    )

    assert res.returncode == 0, res.stderr
    facts = dict(
        line.split("=", 1) for line in config_log.read_text().splitlines()
    )
    assert facts == {
        "script": str(local_sh),
        "kit": str(kit),
        "schema": str(config / "schema.json"),
        "prompt": str(config / "prompt.md"),
        "schema_content": "trusted schema",
        "prompt_content": "trusted prompt",
    }


def test_findings_request_changes(fleet: Fleet) -> None:
    res = fleet.run("demo", "7", REVIEW_STUB_EXIT="1")
    assert res.returncode == 1
    assert "--request-changes" in fleet.gh_calls()


def test_reviewer_failure_publishes_nothing(fleet: Fleet) -> None:
    res = fleet.run("demo", "7", REVIEW_STUB_EXIT="3", REVIEW_STUB_ERR="reviewer down")
    assert res.returncode == 3
    assert "reviewer down" in res.stderr
    assert "pr review" not in fleet.gh_calls()


def test_kit_config_error_propagates(fleet: Fleet) -> None:
    res = fleet.run("demo", "7", REVIEW_STUB_EXIT="2")
    assert res.returncode == 2
    assert "pr review" not in fleet.gh_calls()


def test_head_moved_aborts_publish(fleet: Fleet) -> None:
    res = fleet.run("demo", "7", GH_STUB_HEADOID2="0" * 40)
    assert res.returncode == 4
    assert "pr review" not in fleet.gh_calls()


def test_dry_run_publishes_nothing(fleet: Fleet) -> None:
    res = fleet.run("demo", "7", "--dry-run")
    assert res.returncode == 0, res.stderr
    assert "pr review" not in fleet.gh_calls()
    assert "stub verdict body" in res.stdout
    assert "--approve" in res.stdout


def test_dry_run_keeps_findings_exit_code(fleet: Fleet) -> None:
    res = fleet.run("demo", "7", "--dry-run", REVIEW_STUB_EXIT="1")
    assert res.returncode == 1
    assert "pr review" not in fleet.gh_calls()
    assert "--request-changes" in res.stdout


def test_slug_derived_from_ssh_remote(fleet: Fleet) -> None:
    # origin указывает на GitHub по ssh; insteadOf на этот URL не настроен,
    # так что фетч гарантированно падает (GIT_SSH_COMMAND=false), но slug
    # обязан попасть в `gh pr view` ДО фетча.
    _git(
        "remote",
        "set-url",
        "origin",
        "git@github.com:no-such-owner-xyz/demo-does-not-exist.git",
        cwd=fleet.repo,
    )
    res = fleet.run("demo", "7")
    assert res.returncode == 2  # фетч упал — это ожидаемо
    assert "--repo no-such-owner-xyz/demo-does-not-exist" in fleet.gh_calls()


def test_slug_derived_from_https_remote(fleet: Fleet) -> None:
    _git(
        "remote",
        "set-url",
        "origin",
        "https://github.com/no-such-owner-xyz/demo-does-not-exist.git",
        cwd=fleet.repo,
    )
    res = fleet.run("demo", "7")
    assert res.returncode == 2
    assert "--repo no-such-owner-xyz/demo-does-not-exist" in fleet.gh_calls()


def test_non_github_origin_refuses(fleet: Fleet) -> None:
    _git("remote", "set-url", "origin", "/some/local/path.git", cwd=fleet.repo)
    res = fleet.run("demo", "7")
    assert res.returncode == 2
    assert "origin" in res.stderr


# --- Дедуп по отпечатку входа (devtools#72) ---------------------------------

needs_jq = pytest.mark.skipif(
    shutil.which("jq") is None, reason="стаб reviews гоняет фильтр через jq"
)

FP = "ab" * 32
FP_OTHER = "cd" * 32
OLD_HEAD = "b" * 40


def _review(
    state: str,
    head: str,
    fp: str,
    login: str = "ai-prosto",
    markers: int = 1,
) -> dict:
    body = "## Codex CLI review — терминальный прогон\n\nверждикт...\n"
    body += f"<!-- codex-terminal-review head={head} fp={fp} -->\n" * markers
    return {"user": {"login": login}, "state": state, "body": body}


def _kit_calls(fleet: Fleet) -> list[str]:
    log = fleet.local_log.read_text() if fleet.local_log.exists() else ""
    return log.strip().splitlines()


@pytest.fixture
def fp_fleet(tmp_path: Path) -> Fleet:
    f = Fleet(tmp_path)
    f.write_kit(LOCAL_SH_FP_STUB)
    return f


# Кит, знающий потолки дифа: feature-detect в review-pr.sh идёт по литералу
# `--max-diff-bytes` в local.sh (как у --fingerprint-only), поэтому стаб несёт
# его в комментарии — argv он и так пишет в лог целиком.
LOCAL_SH_CAPS_STUB = LOCAL_SH_FP_STUB.replace(
    'case " $* " in',
    '# потолки: --max-diff-bytes N --max-diff-files N\ncase " $* " in',
    1,
)


@pytest.fixture
def caps_fleet(tmp_path: Path) -> Fleet:
    f = Fleet(tmp_path)
    f.write_kit(LOCAL_SH_CAPS_STUB)
    return f


def test_diff_caps_flags_reach_both_kit_calls(caps_fleet: Fleet) -> None:
    """Явный потолок уходит и в fp-вызов, и в полный прогон — иначе отпечаток
    отказал бы на том же потолке раньше ревью (живой прогон spec-runner#522:
    бандл 447 260 байт при умолчании 400 000). Факт поднятия — в шапке."""
    res = caps_fleet.run(
        "demo", "7", "--max-diff-bytes", "500000", "--max-diff-files", "40",
        REVIEW_STUB_FP=FP,
    )
    assert res.returncode == 0, res.stderr
    calls = _kit_calls(caps_fleet)
    assert len(calls) == 2
    for call in calls:
        assert "--max-diff-bytes 500000" in call, call
        assert "--max-diff-files 40" in call, call
    body = caps_fleet.body_out.read_text()
    assert "потолки дифа подняты явно" in body
    assert "--max-diff-bytes 500000 (флаг) --max-diff-files 40 (флаг)" in body


def test_diff_caps_env_fallback_and_flag_wins(caps_fleet: Fleet) -> None:
    """Без флага потолок берётся из REVIEW_MAX_DIFF_*: так его получает S6
    раннера (`RealOps.review` без параметров, env наследуется). Флаг сильнее."""
    # Первый круг красный: после approve второй круг не открывает
    # stop rule, а предмет теста — не бюджет.
    res = caps_fleet.run(
        "demo", "7", REVIEW_STUB_FP=FP, REVIEW_MAX_DIFF_BYTES="450000",
        REVIEW_STUB_EXIT="1",
    )
    assert res.returncode == 1, res.stderr
    assert all("--max-diff-bytes 450000" in c for c in _kit_calls(caps_fleet))
    assert all("--max-diff-files" not in c for c in _kit_calls(caps_fleet))
    # Шапка называет слой, из которого пришёл потолок: S6 раннера флагов не
    # передаёт, и «явно оператором» без источника вводило бы в заблуждение.
    assert "(env REVIEW_MAX_DIFF_BYTES)" in caps_fleet.body_out.read_text()
    caps_fleet.local_log.unlink()
    res = caps_fleet.run(
        "demo", "7", "--max-diff-bytes", "500000",
        REVIEW_STUB_FP=FP, REVIEW_MAX_DIFF_BYTES="450000",
    )
    assert res.returncode == 0, res.stderr
    assert all("--max-diff-bytes 500000" in c for c in _kit_calls(caps_fleet))


def test_diff_caps_without_override_stay_silent(caps_fleet: Fleet) -> None:
    res = caps_fleet.run("demo", "7", REVIEW_STUB_FP=FP)
    assert res.returncode == 0, res.stderr
    assert all("--max-diff" not in c for c in _kit_calls(caps_fleet))
    assert "потолки" not in caps_fleet.body_out.read_text()


@pytest.mark.parametrize(
    ("flag", "value"),
    [("--max-diff-bytes", ""), ("--max-diff-bytes", "1MiB"),
     ("--max-diff-files", ""), ("--max-diff-files", "x")],
)
def test_diff_caps_reject_empty_and_non_integer(
    caps_fleet: Fleet, flag: str, value: str,
) -> None:
    """Пустое значение молча ушло бы в умолчание кита (обещанный оверрайд
    испарился бы), нечисловое — упало бы внутри кита: отказ здесь, до
    единого вызова кита и до публикации."""
    res = caps_fleet.run("demo", "7", flag, value, REVIEW_STUB_FP=FP)
    assert res.returncode == 2, res.stderr
    assert flag in res.stderr
    assert _kit_calls(caps_fleet) == []
    assert "pr review" not in caps_fleet.gh_calls()


def test_diff_caps_env_non_integer_names_the_env_source(
    caps_fleet: Fleet,
) -> None:
    """Отказ на env-пути называет переменную, а не только флаг: в вызове S6
    раннера флага нет, и оператор искал бы его напрасно."""
    res = caps_fleet.run(
        "demo", "7", REVIEW_STUB_FP=FP, REVIEW_MAX_DIFF_BYTES="1MiB",
    )
    assert res.returncode == 2, res.stderr
    assert "REVIEW_MAX_DIFF_BYTES" in res.stderr
    assert _kit_calls(caps_fleet) == []


def test_diff_caps_refused_on_kit_without_flag(fp_fleet: Fleet) -> None:
    """Кит без --max-diff-bytes (LOCAL_SH_FP_STUB): явный оверрайд не может
    исполниться — отказ с причиной, не тихий прогон с умолчанием и не usage
    старого скрипта."""
    res = fp_fleet.run(
        "demo", "7", "--max-diff-bytes", "500000", REVIEW_STUB_FP=FP,
    )
    assert res.returncode == 2, res.stderr
    assert "не знает --max-diff-bytes" in res.stderr
    assert _kit_calls(fp_fleet) == []
    assert "pr review" not in fp_fleet.gh_calls()


def test_fp_kit_drops_fetch_and_stamps_marker(fp_fleet: Fleet) -> None:
    """fp-кит: база освежается явным fetch, оба вызова без --fetch,
    маркер несёт отпечаток."""
    res = fp_fleet.run("demo", "7", REVIEW_STUB_FP=FP)
    assert res.returncode == 0, res.stderr
    calls = _kit_calls(fp_fleet)
    assert len(calls) == 2
    assert "--fingerprint-only" in calls[0]
    assert "--format markdown" in calls[1]
    assert all("--fetch" not in c for c in calls)
    body = fp_fleet.body_out.read_text()
    assert f"head={fp_fleet.head_sha} fp={FP} -->" in body


def test_fp_prefetch_refreshes_base_tracking_ref(fp_fleet: Fleet) -> None:
    """База уехала ПОСЛЕ клонирования: явный pre-fetch обязан обновить
    refs/remotes/origin/<base> — отпечаток и ревью видят свежую базу
    (боевая находка codex-ревью PR #73)."""
    seed = fp_fleet.tmp / "seed"
    (seed / "b.txt").write_text("advance\n")
    _git("add", ".", cwd=seed)
    _git("commit", "-m", "advance base", cwd=seed)
    _git("push", "-q", "origin", "HEAD:master", cwd=seed)
    new_base = _git("rev-parse", "HEAD", cwd=seed)
    stale = _git("rev-parse", "refs/remotes/origin/master", cwd=fp_fleet.repo)
    assert stale != new_base  # предпосылка: клон реально отстал
    res = fp_fleet.run("demo", "7", REVIEW_STUB_FP=FP)
    assert res.returncode == 0, res.stderr
    cur = _git("rev-parse", "refs/remotes/origin/master", cwd=fp_fleet.repo)
    assert cur == new_base


def test_old_kit_gets_no_fp_and_keeps_fetch(fleet: Fleet) -> None:
    """Старый кит без литерала: поведение не меняется, маркер без fp."""
    res = fleet.run("demo", "7")
    assert res.returncode == 0, res.stderr
    calls = _kit_calls(fleet)
    assert len(calls) == 1 and "--fetch" in calls[0]
    assert "--fingerprint-only" not in calls[0]
    body = fleet.body_out.read_text()
    assert f"head={fleet.head_sha} -->" in body
    assert "fp=" not in body


@needs_jq
def test_inherit_same_head_publishes_nothing(fp_fleet: Fleet) -> None:
    reviews = fp_fleet.write_reviews(_review("APPROVED", fp_fleet.head_sha, FP))
    res = fp_fleet.run("demo", "7", REVIEW_STUB_FP=FP, GH_STUB_REVIEWS_JSON=reviews)
    assert res.returncode == 0, res.stderr
    assert "унаследован" in res.stdout
    assert "pr review" not in fp_fleet.gh_calls()
    calls = _kit_calls(fp_fleet)  # codex не вызывался: только fp-режим
    assert len(calls) == 1 and "--fingerprint-only" in calls[0]


@needs_jq
def test_inherit_same_head_aborts_if_head_moved(fp_fleet: Fleet) -> None:
    """Голова уехала после вычисления отпечатка: наследование same-head
    обязано дать exit 4, а не объявить зелёным неревьюенный head
    (боевая находка codex-ревью №2 PR #73)."""
    reviews = fp_fleet.write_reviews(
        _review("APPROVED", fp_fleet.head_sha, FP)
    )
    res = fp_fleet.run(
        "demo", "7",
        REVIEW_STUB_FP=FP,
        GH_STUB_REVIEWS_JSON=reviews,
        GH_STUB_HEADOID2="0" * 40,
    )
    assert res.returncode == 4
    assert "уехала" in res.stderr
    assert "pr review" not in fp_fleet.gh_calls()


@needs_jq
def test_inherit_red_verdict_keeps_exit_code(fp_fleet: Fleet) -> None:
    reviews = fp_fleet.write_reviews(
        _review("CHANGES_REQUESTED", fp_fleet.head_sha, FP)
    )
    res = fp_fleet.run("demo", "7", REVIEW_STUB_FP=FP, GH_STUB_REVIEWS_JSON=reviews)
    assert res.returncode == 1
    assert "унаследован" in res.stdout
    assert "pr review" not in fp_fleet.gh_calls()


@needs_jq
def test_inherit_new_head_republishes_same_action(fp_fleet: Fleet) -> None:
    """update-branch: то же действие, тело-наследование, маркер с новым head
    и тем же fp; codex не вызывается."""
    reviews = fp_fleet.write_reviews(_review("APPROVED", OLD_HEAD, FP))
    res = fp_fleet.run("demo", "7", REVIEW_STUB_FP=FP, GH_STUB_REVIEWS_JSON=reviews)
    assert res.returncode == 0, res.stderr
    assert "--approve" in fp_fleet.gh_calls()
    body = fp_fleet.body_out.read_text()
    assert "унаследован от прогона по head" in body
    assert OLD_HEAD in body
    assert f"head={fp_fleet.head_sha} fp={FP} -->" in body
    calls = _kit_calls(fp_fleet)
    assert len(calls) == 1 and "--fingerprint-only" in calls[0]


@needs_jq
def test_inherit_new_head_dry_run(fp_fleet: Fleet) -> None:
    reviews = fp_fleet.write_reviews(_review("CHANGES_REQUESTED", OLD_HEAD, FP))
    res = fp_fleet.run(
        "demo",
        "7",
        "--dry-run",
        REVIEW_STUB_FP=FP,
        GH_STUB_REVIEWS_JSON=reviews,
    )
    assert res.returncode == 1
    assert "pr review" not in fp_fleet.gh_calls()
    assert "унаследован" in res.stdout


@needs_jq
def test_fresh_bypasses_inheritance_but_stamps_fp(fp_fleet: Fleet) -> None:
    reviews = fp_fleet.write_reviews(_review("APPROVED", fp_fleet.head_sha, FP))
    res = fp_fleet.run(
        "demo",
        "7",
        "--fresh",
        REVIEW_STUB_FP=FP,
        GH_STUB_REVIEWS_JSON=reviews,
    )
    assert res.returncode == 0, res.stderr
    assert "/reviews" not in fp_fleet.gh_calls()  # поиск вердикта обойдён
    calls = _kit_calls(fp_fleet)
    assert any("--format markdown" in c for c in calls)  # полный прогон
    assert f"fp={FP} -->" in fp_fleet.body_out.read_text()  # fp публикуется


@needs_jq
def test_dismissed_newest_is_cache_miss(fp_fleet: Fleet) -> None:
    """Новейшее ревью DISMISSED — miss ЦЕЛИКОМ: воскрешать более старый
    вердикт из истории дедуп не имеет права."""
    reviews = fp_fleet.write_reviews(
        _review("APPROVED", fp_fleet.head_sha, FP),
        _review("DISMISSED", fp_fleet.head_sha, FP),
    )
    res = fp_fleet.run("demo", "7", REVIEW_STUB_FP=FP, GH_STUB_REVIEWS_JSON=reviews)
    assert res.returncode == 0, res.stderr
    calls = _kit_calls(fp_fleet)
    assert any("--format markdown" in c for c in calls)
    assert "--approve" in fp_fleet.gh_calls()  # опубликован СВЕЖИЙ прогон


@needs_jq
def test_duplicated_marker_is_cache_miss(fp_fleet: Fleet) -> None:
    reviews = fp_fleet.write_reviews(
        _review("APPROVED", fp_fleet.head_sha, FP, markers=2)
    )
    res = fp_fleet.run("demo", "7", REVIEW_STUB_FP=FP, GH_STUB_REVIEWS_JSON=reviews)
    assert res.returncode == 0, res.stderr
    assert any("--format markdown" in c for c in _kit_calls(fp_fleet))


@needs_jq
def test_foreign_author_is_ignored(fp_fleet: Fleet) -> None:
    """Чужое ревью с валидным маркером не наследуется — только ai-prosto."""
    reviews = fp_fleet.write_reviews(
        _review("APPROVED", fp_fleet.head_sha, FP, login="somebody-else")
    )
    res = fp_fleet.run("demo", "7", REVIEW_STUB_FP=FP, GH_STUB_REVIEWS_JSON=reviews)
    assert res.returncode == 0, res.stderr
    assert any("--format markdown" in c for c in _kit_calls(fp_fleet))


@needs_jq
def test_fp_mismatch_runs_full(fp_fleet: Fleet) -> None:
    reviews = fp_fleet.write_reviews(_review("APPROVED", fp_fleet.head_sha, FP_OTHER))
    res = fp_fleet.run("demo", "7", REVIEW_STUB_FP=FP, GH_STUB_REVIEWS_JSON=reviews)
    assert res.returncode == 0, res.stderr
    assert any("--format markdown" in c for c in _kit_calls(fp_fleet))
    assert f"fp={FP} -->" in fp_fleet.body_out.read_text()


def test_fp_mode_refusal_publishes_nothing(fp_fleet: Fleet) -> None:
    """exit 2 fp-режима — отказ: дедуп не применяется, обычная обработка."""
    res = fp_fleet.run("demo", "7", REVIEW_STUB_FP_EXIT="2")
    assert res.returncode == 2
    assert "pr review" not in fp_fleet.gh_calls()
    assert all("--format markdown" not in c for c in _kit_calls(fp_fleet))


def test_fp_empty_stdout_falls_through_to_full_run(fp_fleet: Fleet) -> None:
    """exit 0 + пустой stdout: наследовать нечего — обычный полный прогон,
    маркер без fp."""
    res = fp_fleet.run("demo", "7")
    assert res.returncode == 0, res.stderr
    assert "/reviews" not in fp_fleet.gh_calls()
    assert any("--format markdown" in c for c in _kit_calls(fp_fleet))
    assert "fp=" not in fp_fleet.body_out.read_text()


def test_fp_garbage_stdout_skips_dedup(fp_fleet: Fleet) -> None:
    res = fp_fleet.run("demo", "7", REVIEW_STUB_FP="not-a-fingerprint")
    assert res.returncode == 0, res.stderr
    assert "stdout-контракт" in res.stderr
    assert any("--format markdown" in c for c in _kit_calls(fp_fleet))
    assert "fp=" not in fp_fleet.body_out.read_text()


# --- Явная передача dry-run verdict (devtools#80) ----------------------------


def test_dry_run_writes_verdict_and_live_run_uses_it(fp_fleet: Fleet) -> None:
    verdict = fp_fleet.tmp / "verdict.out"
    dry = fp_fleet.run(
        "demo", "7", "--dry-run", "--write-verdict", str(verdict),
        REVIEW_STUB_FP=FP,
    )
    assert dry.returncode == 0, dry.stderr
    assert verdict.read_text().startswith("codex-terminal-review-verdict/v1\n")
    assert stat.S_IMODE(verdict.stat().st_mode) == 0o600
    calls_after_dry = len(_kit_calls(fp_fleet))

    live = fp_fleet.run(
        "demo", "7", "--use-verdict", str(verdict),
        REVIEW_STUB_FP=FP,
    )
    assert live.returncode == 0, live.stderr
    assert "вердикт принят из файла" in live.stdout
    assert "--approve" in fp_fleet.gh_calls()
    # Второй прогон вызывает только fp-режим, не полный codex review.
    new_calls = _kit_calls(fp_fleet)[calls_after_dry:]
    assert len(new_calls) == 1 and "--fingerprint-only" in new_calls[0]
    assert fp_fleet.body_out.read_text() == dry.stdout.split(
        "=== dry-run: действие --approve, ничего не публикуется ===\n", 1
    )[1]


@pytest.mark.parametrize("field", ["head", "fp"])
def test_verdict_context_mismatch_runs_full(fp_fleet: Fleet, field: str) -> None:
    verdict = fp_fleet.tmp / "verdict.out"
    # Первый круг красный: после approve второй круг не открывает
    # stop rule, а предмет теста — не бюджет.
    dry = fp_fleet.run(
        "demo", "7", "--dry-run", "--write-verdict", str(verdict),
        REVIEW_STUB_FP=FP, REVIEW_STUB_EXIT="1",
    )
    assert dry.returncode == 1, dry.stderr
    text = verdict.read_text()
    if field == "head":
        text = text.replace(f"head={fp_fleet.head_sha}", f"head={'0' * 40}", 1)
    else:
        text = text.replace(f"fp={FP}", f"fp={FP_OTHER}", 1)
    verdict.write_text(text)
    calls_after_dry = len(_kit_calls(fp_fleet))

    live = fp_fleet.run(
        "demo", "7", "--use-verdict", str(verdict),
        REVIEW_STUB_FP=FP,
    )
    assert live.returncode == 0, live.stderr
    assert "идёт полный прогон" in live.stderr
    assert any(
        "--format markdown" in call
        for call in _kit_calls(fp_fleet)[calls_after_dry:]
    )


def test_corrupt_verdict_body_runs_full(fp_fleet: Fleet) -> None:
    verdict = fp_fleet.tmp / "verdict.out"
    # Первый круг красный: после approve второй круг не открывает
    # stop rule, а предмет теста — не бюджет.
    dry = fp_fleet.run(
        "demo", "7", "--dry-run", "--write-verdict", str(verdict),
        REVIEW_STUB_FP=FP, REVIEW_STUB_EXIT="1",
    )
    assert dry.returncode == 1, dry.stderr
    verdict.write_text(verdict.read_text() + "corruption\n")
    calls_after_dry = len(_kit_calls(fp_fleet))
    live = fp_fleet.run(
        "demo", "7", "--use-verdict", str(verdict),
        REVIEW_STUB_FP=FP,
    )
    assert live.returncode == 0, live.stderr
    assert "повреждён" in live.stderr
    assert any(
        "--format markdown" in call
        for call in _kit_calls(fp_fleet)[calls_after_dry:]
    )


@needs_jq
def test_invalid_file_does_not_fall_through_to_github_inheritance(
    fp_fleet: Fleet,
) -> None:
    verdict = fp_fleet.tmp / "verdict.out"
    # Первый круг красный: после approve второй круг не открывает
    # stop rule, а предмет теста — не бюджет.
    dry = fp_fleet.run(
        "demo", "7", "--dry-run", "--write-verdict", str(verdict),
        REVIEW_STUB_FP=FP, REVIEW_STUB_EXIT="1",
    )
    assert dry.returncode == 1, dry.stderr
    verdict.write_text(verdict.read_text().replace(f"fp={FP}", f"fp={FP_OTHER}", 1))
    reviews = fp_fleet.write_reviews(
        _review("APPROVED", fp_fleet.head_sha, FP)
    )
    calls_after_dry = len(_kit_calls(fp_fleet))
    gh_after_dry = len(fp_fleet.gh_calls())
    live = fp_fleet.run(
        "demo", "7", "--use-verdict", str(verdict),
        REVIEW_STUB_FP=FP,
        GH_STUB_REVIEWS_JSON=reviews,
    )
    assert live.returncode == 0, live.stderr
    assert "/reviews" not in fp_fleet.gh_calls()[gh_after_dry:]
    assert any(
        "--format markdown" in call
        for call in _kit_calls(fp_fleet)[calls_after_dry:]
    )


def test_write_verdict_requires_dry_run(fp_fleet: Fleet) -> None:
    res = fp_fleet.run(
        "demo", "7", "--write-verdict", str(fp_fleet.tmp / "v"),
        REVIEW_STUB_FP=FP,
    )
    assert res.returncode == 2
    assert "только вместе с --dry-run" in res.stderr


@needs_jq
@pytest.mark.parametrize("source_head", ["same", "old"])
def test_inherited_dry_run_also_writes_verdict(
    fp_fleet: Fleet, source_head: str
) -> None:
    head = fp_fleet.head_sha if source_head == "same" else OLD_HEAD
    reviews = fp_fleet.write_reviews(_review("APPROVED", head, FP))
    verdict = fp_fleet.tmp / f"inherited-{source_head}.out"
    res = fp_fleet.run(
        "demo", "7", "--dry-run", "--write-verdict", str(verdict),
        REVIEW_STUB_FP=FP,
        GH_STUB_REVIEWS_JSON=reviews,
    )
    assert res.returncode == 0, res.stderr
    assert verdict.is_file()
    assert f"head={fp_fleet.head_sha}\n" in verdict.read_text()
    assert f"fp={FP}\n" in verdict.read_text()
    calls = _kit_calls(fp_fleet)
    assert len(calls) == 1 and "--fingerprint-only" in calls[0]


# --- свежий кит steward (devtools#222): env вместо REVIEW_CMD ---------------


def test_new_kit_full_run_labels_reviewer_from_kit(fleet: Fleet, tmp_path: Path) -> None:
    """Признак «сделано» из devtools#222: прогон на репо со свежим китом идёт
    через харнесс-слой кита — в теле ревью `harness-claude --model …`, кит
    получает REVIEW_HARNESS/REVIEW_MODEL окружением, REVIEW_CMD не собран и
    scripts/harness в PATH сабшелла кита нет."""
    from tests.test_claude_review_harness import NEW_KIT_STUB

    fleet.write_kit(NEW_KIT_STUB)
    kit_log = tmp_path / "kit.log"
    res = fleet.run("demo", "7", "--harness", "claude", HARNESS_KIT_LOG=str(kit_log))
    assert res.returncode == 0, res.stderr
    body = fleet.body_out.read_text()
    assert "`harness-claude --model claude-opus-5`" in body
    assert "claude-review" not in body
    env_lines = kit_log.read_text().splitlines()
    assert "REVIEW_HARNESS=claude" in env_lines
    assert "REVIEW_MODEL=<unset>" in env_lines
    assert "REVIEW_CMD=<unset>" in env_lines
    assert not any("scripts/harness" in line for line in env_lines if line.startswith("PATH="))


def test_old_kit_full_run_with_claude_is_refused_before_review(fleet: Fleet) -> None:
    """Копия кита без харнесс-слоя (стаб без литерала) + claude: отказ до
    прогона и до публикации — переходник снят волной devtools#228."""
    res = fleet.run("demo", "7", "--harness", "claude", "--model", "claude-sonnet-4-6")
    assert res.returncode == 2, res.stdout
    assert "без харнесс-слоя" in res.stderr
    assert "pr review" not in fleet.gh_calls()
    assert not fleet.body_out.exists()


# --- Бюджет платных прогонов (решение владельца 2026-09-18) ------------------
# Предыстория: неформальный гейт «чинить любую находку и повторять до чистого
# вердикта» дал 14 платных кругов на одном PR. Формальный порог (blocker/major
# + high) при этом НЕ нарушался — каждый новый круг открывала настоящая
# блокирующая находка. Ограничивать надо не порог, а число платных проходов, и
# ограничение обязано быть барьером, а не прозой: правило в CLAUDE.md обходится
# рационализацией (наблюдалось дважды за одну сессию), отказ инструмента — нет.
#
# Считаются ФАКТИЧЕСКИЕ вызовы модели. Отпечаток (`--fingerprint-only`) и
# наследование вердикта (`--use-verdict`) выходят до вызова ревьюера и потому
# не платят — бюджет им не расходуется.


def test_budget_allows_full_plus_targeted(fleet: Fleet) -> None:
    """Бюджет — 1 полный + 1 адресный: два платных прогона проходят, если
    первый дал блокирующую находку (иначе второй не откроет stop rule)."""
    assert fleet.run("demo", "7", REVIEW_STUB_EXIT="1").returncode == 1
    assert fleet.run("demo", "7", REVIEW_STUB_EXIT="1").returncode == 1


def test_budget_refuses_third_paid_run(fleet: Fleet) -> None:
    fleet.run("demo", "7", REVIEW_STUB_EXIT="1")
    fleet.run("demo", "7", REVIEW_STUB_EXIT="1")
    before = fleet.gh_calls().count("pr review")
    kit_before = fleet.local_log.read_text().count("--format markdown")
    res = fleet.run("demo", "7")
    assert res.returncode == 2, res.stdout
    assert "бюджет" in res.stderr.lower()
    # Отказ обязан называть путь обхода, иначе оператор не знает, что делать.
    assert "--budget-override" in res.stderr
    # Fail-closed: третий прогон не дошёл до кита и ничего не опубликовал.
    # Считаем ПРИРОСТ: в общем логе лежат публикации первых двух кругов.
    assert fleet.gh_calls().count("pr review") == before
    assert fleet.local_log.read_text().count("--format markdown") == kit_before


def test_dry_run_consumes_budget(fleet: Fleet) -> None:
    """--dry-run платит так же: сегодня все 14 кругов были dry-run, и по
    опубликованным вердиктам их было бы видно ОДИН."""
    fleet.run("demo", "7", "--dry-run", REVIEW_STUB_EXIT="1")
    fleet.run("demo", "7", "--dry-run", REVIEW_STUB_EXIT="1")
    res = fleet.run("demo", "7", "--dry-run", REVIEW_STUB_EXIT="1")
    assert res.returncode == 2, res.stdout
    assert "бюджет" in res.stderr.lower()


def test_budget_is_per_pr(fleet: Fleet) -> None:
    fleet.run("demo", "7", REVIEW_STUB_EXIT="1")
    fleet.run("demo", "7", REVIEW_STUB_EXIT="1")
    # Другой PR — свой бюджет. Ветку восьмого надо выложить: review-pr.sh
    # фетчит refs/pull/<n>/head, и без неё прогон упал бы не по бюджету.
    _git(
        "push", "-q", "origin", f"{fleet.head_sha}:refs/pull/8/head",
        cwd=fleet.repo,
    )
    assert fleet.run("demo", "8").returncode == 0


def test_budget_override_requires_reason(fleet: Fleet) -> None:
    res = fleet.run("demo", "7", "--budget-override")
    assert res.returncode == 2
    assert "причин" in res.stderr.lower()


def test_budget_override_rejects_empty_reason(fleet: Fleet) -> None:
    res = fleet.run("demo", "7", "--budget-override", "  ")
    assert res.returncode == 2
    assert "причин" in res.stderr.lower()


def test_budget_override_allows_and_records_reason(fleet: Fleet) -> None:
    fleet.run("demo", "7", REVIEW_STUB_EXIT="1")
    fleet.run("demo", "7", REVIEW_STUB_EXIT="1")
    res = fleet.run(
        "demo", "7", "--budget-override", "владелец: critical-профиль",
    )
    assert res.returncode == 0, res.stderr
    # Перерасход обязан быть виден в опубликованном вердикте, а не только в
    # локальном логе: читатель PR узнаёт о нём без чтения прозы агента.
    body = fleet.body_out.read_text()
    assert "бюджет" in body.lower()
    assert "владелец: critical-профиль" in body


def test_budget_override_not_needed_within_budget(fleet: Fleet) -> None:
    """Обход в пределах бюджета не помечает вердикт — отметка означает
    перерасход, иначе она обесценивается."""
    res = fleet.run("demo", "7", "--budget-override", "на всякий случай")
    assert res.returncode == 0, res.stderr
    assert "бюджет" not in fleet.body_out.read_text().lower()


def test_use_verdict_bypasses_exhausted_budget(fp_fleet: Fleet) -> None:
    """Перенос готового вердикта модель не зовёт — и потому бюджетом не
    ограничен. Утверждение из комментария к барьеру: без этого теста оно было
    бы такой же непроверяемой прозой, какую процесс требует убирать."""
    verdict = fp_fleet.tmp / "v.out"
    # Оба круга красные: после approve второй не открыл бы stop rule, и мы
    # упёрлись бы не в бюджет.
    assert fp_fleet.run(
        "demo", "7", "--dry-run", "--write-verdict", str(verdict),
        REVIEW_STUB_FP=FP, REVIEW_STUB_EXIT="1",
    ).returncode == 1
    assert fp_fleet.run(
        "demo", "7", "--fresh", REVIEW_STUB_FP=FP, REVIEW_STUB_EXIT="1",
    ).returncode == 1
    # Бюджет исчерпан: платный прогон отказан.
    assert fp_fleet.run(
        "demo", "7", "--fresh", REVIEW_STUB_FP=FP, REVIEW_STUB_EXIT="1",
    ).returncode == 2

    kit_before = fp_fleet.local_log.read_text().count("--format markdown")
    res = fp_fleet.run(
        "demo", "7", "--use-verdict", str(verdict), REVIEW_STUB_FP=FP,
    )
    assert res.returncode == 1, res.stderr
    # Кит для тела не звался — значит и платить было нечем.
    assert fp_fleet.local_log.read_text().count("--format markdown") == kit_before


def test_budget_override_rejects_flag_shaped_reason(fleet: Fleet) -> None:
    """Забытая причина съела бы следующий флаг: `--budget-override --dry-run`
    дало бы боевую публикацию вместо dry-run — необратимый внешний эффект от
    опечатки."""
    res = fleet.run("demo", "7", "--budget-override", "--dry-run")
    assert res.returncode == 2
    assert "не может начинаться" in res.stderr
    assert "pr review" not in fleet.gh_calls()


def test_instrument_failure_does_not_consume_budget(fleet: Fleet) -> None:
    """Отказ прибора вердикта не даёт — списывать за него значило бы тратить
    бюджет на прогоны, ни один из которых ревью не принёс."""
    assert fleet.run("demo", "7", REVIEW_STUB_EXIT="3").returncode == 3
    assert fleet.run("demo", "7", REVIEW_STUB_EXIT="3").returncode == 3
    # Бюджет не тронут: оба платных круга ещё доступны.
    assert fleet.run("demo", "7", REVIEW_STUB_EXIT="1").returncode == 1
    assert fleet.run("demo", "7", REVIEW_STUB_EXIT="1").returncode == 1
    assert fleet.run("demo", "7", REVIEW_STUB_EXIT="1").returncode == 2


def test_documented_flow_costs_one_round(fp_fleet: Fleet) -> None:
    """Штатный флоу CLAUDE.md (`--write-verdict` → `--use-verdict`) стоит
    ОДИН круг: иначе один цикл ревью съедал бы весь бюджет и обещанный
    адресный recheck был бы недостижим."""
    verdict = fp_fleet.tmp / "v.out"
    assert fp_fleet.run(
        "demo", "7", "--dry-run", "--write-verdict", str(verdict),
        REVIEW_STUB_FP=FP, REVIEW_STUB_EXIT="1",
    ).returncode == 1
    assert fp_fleet.run(
        "demo", "7", "--use-verdict", str(verdict), REVIEW_STUB_FP=FP,
    ).returncode == 1
    # Круг потрачен ровно один — адресный recheck доступен без override.
    assert fp_fleet.run(
        "demo", "7", "--fresh", REVIEW_STUB_FP=FP, REVIEW_STUB_EXIT="1",
    ).returncode == 1
    # И только теперь бюджет исчерпан.
    assert fp_fleet.run(
        "demo", "7", "--fresh", REVIEW_STUB_FP=FP, REVIEW_STUB_EXIT="1",
    ).returncode == 2

# --- Stop rule: круг открывает только блокирующая находка (devtools#256) ----
# Канон — prograph-vault `authored/rules/git-workflow.md`, stop rule (vault#135).
# Порог решает `apply-threshold.sh`: красный вердикт (кит 1) = блокирующая
# находка есть; approve (кит 0) = не добрала до порога. Значит «не
# перезапускать из-за minor» проверяется механически по коду прошлого круга, а
# не по классификации находок моделью.


def test_second_round_requires_blocking_finding(fleet: Fleet) -> None:
    """После approve второй платный круг не открывается: блокирующей находки
    не было, а non-gate находки круга не открывают."""
    assert fleet.run("demo", "7").returncode == 0
    res = fleet.run("demo", "7")
    assert res.returncode == 2, res.stdout
    assert "блокирующ" in res.stderr.lower()
    assert "--budget-override" in res.stderr


def test_second_round_allowed_after_red_verdict(fleet: Fleet) -> None:
    """Красный вердикт — законный повод для адресного recheck."""
    assert fleet.run("demo", "7", REVIEW_STUB_EXIT="1").returncode == 1
    assert fleet.run("demo", "7").returncode == 0


def test_override_opens_round_after_approve(fleet: Fleet) -> None:
    fleet.run("demo", "7")
    res = fleet.run("demo", "7", "--budget-override", "владелец: проверка")
    assert res.returncode == 0, res.stderr


@needs_jq
def test_targeted_reviews_against_last_reviewed_head(fleet: Fleet) -> None:
    """Адресный recheck смотрит фикс-коммиты относительно прошлой
    отревьюированной головы, а не весь PR относительно базы."""
    reviews = fleet.write_reviews(_review("CHANGES_REQUESTED", OLD_HEAD, FP))
    res = fleet.run(
        "demo", "7", "--targeted", GH_STUB_REVIEWS_JSON=reviews,
    )
    assert res.returncode == 0, res.stderr
    call = fleet.local_log.read_text()
    assert f"--base {OLD_HEAD}" in call
    assert "--base origin/master" not in call


@needs_jq
def test_targeted_refuses_without_previous_review(fleet: Fleet) -> None:
    """Прошлой отревьюированной головы нет — адресный режим невозможен, и это
    отказ с причиной, а не тихий полный прогон под видом адресного."""
    res = fleet.run("demo", "7", "--targeted")
    assert res.returncode == 2
    assert "отревьюированн" in res.stderr.lower()
