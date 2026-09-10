"""Тесты merge-pr.sh — гвард агентского мержа (ADR-ECO-011, §I12).

Стратегия: `gh` — стаб в `PATH`, пишущий КАЖДЫЙ свой вызов в лог-файл.
Журнал вызовов и есть предмет доказательств: «merge API не вызван»
проверяется отсутствием вызова `…/merge` в журнале, а не кодом возврата —
код возврата мог бы совпасть и по другой причине.

Мерж идёт прямым `PUT /pulls/{n}/merge`, а не `gh pr merge`: решение дизайна
пайплайна (2026-08-30 §8) — `gh pr merge` читает `mergeStateStatus` сам и при
`BLOCKED` отказывает своим текстом, ни разу не проверив bypass актора.

Контракт кодов выхода merge-pr.sh:
  0 — мерж выполнен (или показан при --dry-run);
  2 — конфигурация/аргументы/профиль/состояние PR/неразобранные факты;
  3 — гвард: PR запрещён к агентскому мержу, остаётся человеку;
  4 — форджа отклонила мерж (в т.ч. голова уехала после проверки).
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

from governance import approval_branches, authority_root

SCRIPT = Path(__file__).resolve().parent.parent / "merge-pr.sh"

HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"
BASE_SHA = "89abcdef0123456789abcdef0123456789abcdef"

GH_STUB = """#!/usr/bin/env bash
# Стаб gh: логирует каждый вызов, отвечает по переменным GH_STUB_*.
echo "GH_CONFIG_DIR=${GH_CONFIG_DIR:-} gh $*" >> "$GH_STUB_LOG"
case "$*" in
  *"api user"*)
    if [ -n "${GH_STUB_USER_FAIL:-}" ]; then
      echo "gh: authentication token expired" >&2
      exit 1
    fi
    echo "${GH_STUB_LOGIN:-ai-prosto}" ;;
  *headRefName*)
    if [ -n "${GH_STUB_VIEW_FAIL:-}" ]; then
      echo "gh: could not resolve to a PullRequest" >&2
      exit 1
    fi
    printf '%s\\n' "${GH_STUB_HEADREF-feat/ordinary}"
    printf '%s\\n' "${GH_STUB_HEADOID-$GH_STUB_DEFAULT_OID}"
    printf '%s\\n' "${GH_STUB_STATE:-OPEN}"
    printf '%s\\n' "${GH_STUB_BASEOID-$GH_STUB_DEFAULT_BASE}"
    printf '%s\\n' "${GH_STUB_MERGESTATE-CLEAN}"
    printf '%s\\n' "${GH_STUB_CROSSREPO-false}"
    if [ -n "${GH_STUB_LABELS:-}" ]; then
      printf '%s\\n' "$GH_STUB_LABELS"
    fi ;;
  *"/merge"*)
    # Настоящая форджа отвергает мерж, если голова уехала с проверенной:
    # PUT /pulls/{n}/merge с несовпавшим `sha` даёт 409.
    if [ -n "${GH_STUB_ACTUAL_OID:-}" ]; then
      for a in "$@"; do
        case "$a" in
          sha=*)
            if [ "${a#sha=}" != "$GH_STUB_ACTUAL_OID" ]; then
              echo "Head branch was modified. Review and try the merge again." >&2
              exit 1
            fi ;;
        esac
      done
    fi
    echo '{"merged":true}' ;;
  *"/compare/"*)
    if [ -n "${GH_STUB_COMPARE_FAIL:-}" ]; then
      echo "gh: Not Found (HTTP 404)" >&2
      exit 1
    fi
    # Тело ответа — настоящий JSON, и переданный --jq применяется НАСТОЯЩИМ
    # jq: иначе `.files[]` и `.files[]?` неотличимы, и fail-closed на
    # ответе без ключа `files` нечем проверить.
    if [ -n "${GH_STUB_COMPARE_NO_FILES:-}" ]; then
      body='{"status":"ahead"}'
    else
      body=$(printf '%s\\n' "${GH_STUB_FILES-lib/x.ex}" \
             | jq -R '{filename:.}' | jq -s '{files:.}')
    fi
    filter=""; prev=""
    for a in "$@"; do
      if [ "$prev" = "--jq" ]; then filter="$a"; fi
      prev="$a"
    done
    printf '%s' "$body" | jq -r "$filter" ;;
  *"/git/refs/heads/"*)
    if [ -n "${GH_STUB_DELETE_FAIL:-}" ]; then
      echo "Reference does not exist" >&2
      exit 1
    fi ;;
esac
"""


class Fleet:
    """Синтетический флот: каталог репо с GitHub-origin и стабом gh."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.fleet_root = tmp_path / "fleet"
        self.repo = self.fleet_root / "demo"
        self.stub_bin = tmp_path / "bin"
        self.gh_log = tmp_path / "gh.log"
        self.profile_dir = tmp_path / "gh-profile"
        self.profile_dir.mkdir()

        self.repo.mkdir(parents=True)
        subprocess.run(
            ["git", "init", "-q", "-b", "master", str(self.repo)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git", "-C", str(self.repo), "remote", "add", "origin",
                "git@github.com:andrei-shtanakov/demo.git",
            ],
            check=True,
            capture_output=True,
        )

        self.stub_bin.mkdir()
        gh = self.stub_bin / "gh"
        gh.write_text(GH_STUB)
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)

    def env(self, **extra: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            PATH=f"{self.stub_bin}:{env['PATH']}",
            FLEET_ROOT=str(self.fleet_root),
            MERGE_GH_CONFIG_DIR=str(self.profile_dir),
            GH_STUB_LOG=str(self.gh_log),
            GH_STUB_DEFAULT_OID=HEAD_SHA,
            GH_STUB_DEFAULT_BASE=BASE_SHA,
        )
        env.update(extra)
        return env

    def run(self, *args: str, **env_extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", str(SCRIPT), "demo", "7", *args],
            env=self.env(**env_extra),
            capture_output=True,
            text=True,
            check=False,  # код выхода — предмет проверки самих тестов
        )

    def gh_calls(self) -> str:
        return self.gh_log.read_text() if self.gh_log.exists() else ""

    def merge_calls(self) -> list[str]:
        """Строки журнала, которыми вызывался merge API."""
        return [ln for ln in self.gh_calls().splitlines() if "/merge" in ln]

    def delete_calls(self) -> list[str]:
        return [
            ln for ln in self.gh_calls().splitlines()
            if "/git/refs/heads/" in ln
        ]


@pytest.fixture()
def fleet(tmp_path: Path) -> Fleet:
    return Fleet(tmp_path)


# --- обычный PR: ровно ожидаемая команда мержа ------------------------------


def test_ordinary_pr_merges_with_exact_command(fleet: Fleet) -> None:
    """Доказательство 4: обычный PR → ровно та команда, и ни флага больше."""
    res = fleet.run(GH_STUB_HEADREF="feat/ordinary")
    assert res.returncode == 0, res.stderr
    assert fleet.merge_calls() == [
        f"GH_CONFIG_DIR={fleet.profile_dir} gh api -X PUT "
        "repos/andrei-shtanakov/demo/pulls/7/merge "
        f"-f merge_method=squash -f sha={HEAD_SHA}"
    ]


def test_strategy_reaches_api_and_branch_deleted_separately(
    fleet: Fleet,
) -> None:
    """Allowlist работает в обе стороны: разрешённое доезжает до API.

    Удаление ветки — отдельный вызов: у merge-API такого параметра нет,
    это был флаг `gh pr merge -d`.
    """
    res = fleet.run("--merge", "--delete-branch", GH_STUB_HEADREF="feat/ordinary")
    assert res.returncode == 0, res.stderr
    assert fleet.merge_calls() == [
        f"GH_CONFIG_DIR={fleet.profile_dir} gh api -X PUT "
        "repos/andrei-shtanakov/demo/pulls/7/merge "
        f"-f merge_method=merge -f sha={HEAD_SHA}"
    ]
    assert fleet.delete_calls() == [
        f"GH_CONFIG_DIR={fleet.profile_dir} gh api -X DELETE "
        "repos/andrei-shtanakov/demo/git/refs/heads/feat/ordinary"
    ]


def test_failed_branch_deletion_does_not_fail_the_merge(fleet: Fleet) -> None:
    """Мерж уже состоялся — ненулевой код соврал бы про него."""
    res = fleet.run(
        "--delete-branch",
        GH_STUB_HEADREF="feat/ordinary",
        GH_STUB_DELETE_FAIL="1",
    )
    assert res.returncode == 0, res.stderr
    assert "удалить не удалось" in res.stderr
    assert len(fleet.merge_calls()) == 1


@pytest.mark.parametrize(
    "flag",
    ["--admin", "--auto", "--body=x", "--subject=x", "-s"],
)
def test_free_passthrough_is_refused(fleet: Fleet, flag: str) -> None:
    """Свободного passthrough нет: неизвестный флаг — usage, не мерж."""
    res = fleet.run(flag, GH_STUB_HEADREF="feat/ordinary")
    assert res.returncode == 2
    assert "usage: merge-pr.sh" in res.stderr
    assert fleet.merge_calls() == []


def test_two_strategies_refused(fleet: Fleet) -> None:
    res = fleet.run("--squash", "--rebase", GH_STUB_HEADREF="feat/ordinary")
    assert res.returncode == 2
    assert "дважды" in res.stderr
    assert fleet.merge_calls() == []


# --- гвард 1: candidate-ветка ----------------------------------------------


def test_candidate_branch_blocks_merge(fleet: Fleet) -> None:
    """Доказательство 1a: candidate-ветка блокирует, merge API не вызван.

    Имя строится ТЕМ ЖЕ определением, из которого гвард выводит глоб
    (`contracts/approval-branches/v1/patterns.env`) — тест сверяет две
    половины сквозь язык, а не повторяет строку третий раз.
    """
    branch = approval_branches.candidate_branch("ws-42", 3, 2, 1)
    res = fleet.run(GH_STUB_HEADREF=branch)
    assert res.returncode == 3, res.stderr
    assert "candidate-PR" in res.stderr
    assert fleet.merge_calls() == []


def test_candidate_guard_survives_wave_numbering(fleet: Fleet) -> None:
    """Дописывание номеров волны/шага/заявки не выводит имя из-под гварда."""
    for wave, step, attempt in ((1, 1, 1), (12, 7, 3), (0, 0, 0)):
        branch = approval_branches.candidate_branch(
            "ws-42", wave, step, attempt
        )
        res = fleet.run(GH_STUB_HEADREF=branch)
        assert res.returncode == 3, f"{branch}: {res.stderr}"
    assert fleet.merge_calls() == []


@pytest.mark.parametrize(
    "branch",
    [
        # Прежняя форма §I12 — без номера заявки. Такие ветки могли остаться
        # в природе от захода до правки контракта; гвард обязан отбивать их
        # тоже, иначе дыра открывается ровно там, где менялась схема имён.
        "spec/ws-42-approve-3-2",
        "spec/ws-42-approve-3-2-final",
        # И совсем ранняя, одноуровневая.
        "spec/ws-42-approve-3",
    ],
)
def test_historic_approval_forms_still_blocked(fleet: Fleet, branch: str) -> None:
    res = fleet.run(GH_STUB_HEADREF=branch)
    assert res.returncode == 3, f"{branch}: {res.stderr}"
    assert fleet.merge_calls() == []


# --- гвард 2: финализирующая ветка -----------------------------------------


def test_finalize_branch_blocks_merge(fleet: Fleet) -> None:
    """Доказательство 1b: finalize-ветка блокирует отдельно от candidate."""
    branch = approval_branches.finalize_branch("ws-42", 3, 2, 1)
    res = fleet.run(GH_STUB_HEADREF=branch)
    assert res.returncode == 3, res.stderr
    # Диагностика называет ИМЕННО фазу финализации: finalize удовлетворяет и
    # форме candidate, и перепутанный порядок проверок назвал бы не ту.
    assert "финализирующий PR" in res.stderr
    assert fleet.merge_calls() == []


# --- гвард 3: лейбл --------------------------------------------------------


def test_human_merge_label_blocks_merge(fleet: Fleet) -> None:
    """Доказательство 1c: лейбл блокирует обычную по имени ветку."""
    res = fleet.run(
        GH_STUB_HEADREF="feat/ordinary",
        GH_STUB_LABELS="human-merge-required",
    )
    assert res.returncode == 3, res.stderr
    assert "human-merge-required" in res.stderr
    assert fleet.merge_calls() == []


def test_human_merge_label_found_among_others(fleet: Fleet) -> None:
    """Лейбл ищется точным совпадением среди прочих, в т.ч. с пробелами."""
    res = fleet.run(
        GH_STUB_HEADREF="feat/ordinary",
        GH_STUB_LABELS="needs review\nhuman-merge-required\ngood first issue",
    )
    assert res.returncode == 3, res.stderr
    assert fleet.merge_calls() == []


def test_similar_label_does_not_block(fleet: Fleet) -> None:
    """Совпадение точное: похожий лейбл мерж не останавливает."""
    res = fleet.run(
        GH_STUB_HEADREF="feat/ordinary",
        GH_STUB_LABELS="human-merge-required-not\nhuman-merge",
    )
    assert res.returncode == 0, res.stderr
    assert len(fleet.merge_calls()) == 1


# --- near-miss: законный агентский мерж не отбивается -----------------------


@pytest.mark.parametrize(
    "branch",
    [
        "feat/merge-pr-guard",
        "spec/ws-42-tasks",
        # `--conform-approve`: доставка approve-штампа tasks-СПЕКИ. Предмет
        # другой (не узлы бандла), мерж законно агентский — глоб обязан её
        # пропустить, иначе гвард встанет поперёк рабочего пути.
        "spec/ws-42-tasks-approve",
        "spec/ws-42-behaviour",
    ],
)
def test_ordinary_branches_are_not_blocked(fleet: Fleet, branch: str) -> None:
    res = fleet.run(GH_STUB_HEADREF=branch)
    assert res.returncode == 0, f"{branch}: {res.stderr}"
    assert len(fleet.merge_calls()) == 1


# --- fail-closed на неразобранных фактах ------------------------------------


def test_view_failure_does_not_merge(fleet: Fleet) -> None:
    """Доказательство 3: факты не получены → merge API не вызван."""
    res = fleet.run(GH_STUB_VIEW_FAIL="1")
    assert res.returncode == 2
    assert "не удалось прочитать факты" in res.stderr
    assert fleet.merge_calls() == []


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("GH_STUB_HEADREF", ""),
        ("GH_STUB_HEADREF", "null"),
        ("GH_STUB_HEADOID", ""),
        ("GH_STUB_HEADOID", "null"),
        ("GH_STUB_STATE", "null"),
    ],
)
def test_unparsed_fact_does_not_merge(fleet: Fleet, var: str, value: str) -> None:
    """Пустой и `null` — «факт не получен», а не «факта нет»."""
    res = fleet.run(**{var: value})
    assert res.returncode == 2, res.stdout
    assert fleet.merge_calls() == []


def test_garbage_head_oid_does_not_merge(fleet: Fleet) -> None:
    """Мержим ПРОВЕРЕННЫЙ oid: неразобранный мусор в него не попадает."""
    res = fleet.run(GH_STUB_HEADOID="refs/heads/whatever")
    assert res.returncode == 2
    assert "не похож на sha" in res.stderr
    assert fleet.merge_calls() == []


def test_closed_pr_does_not_merge(fleet: Fleet) -> None:
    res = fleet.run(GH_STUB_STATE="MERGED")
    assert res.returncode == 2
    assert "не открыт" in res.stderr
    assert fleet.merge_calls() == []


def test_missing_patterns_file_does_not_merge(fleet: Fleet) -> None:
    """SSOT недоступен → отказ, а не вшитый дефолт имён."""
    res = fleet.run(APPROVAL_PATTERNS=str(fleet.tmp / "nope.env"))
    assert res.returncode == 2
    assert "SSOT имён веток одобрения" in res.stderr
    assert fleet.merge_calls() == []


def test_patterns_without_template_does_not_merge(fleet: Fleet) -> None:
    broken = fleet.tmp / "broken.env"
    broken.write_text("# только комментарий\nAPPROVAL_FINALIZE_SUFFIX=-final\n")
    res = fleet.run(APPROVAL_PATTERNS=str(broken))
    assert res.returncode == 2
    assert "APPROVAL_CANDIDATE_TEMPLATE" in res.stderr
    assert fleet.merge_calls() == []


# --- профиль ---------------------------------------------------------------


def test_human_profile_does_not_merge(fleet: Fleet) -> None:
    """Человеческий мерж этой обвязкой не выполняется — и не молча."""
    res = fleet.run(GH_STUB_LOGIN="andrei-shtanakov")
    assert res.returncode == 2
    assert "только агентский мерж" in res.stderr
    assert fleet.merge_calls() == []


def test_broken_profile_does_not_merge(fleet: Fleet) -> None:
    res = fleet.run(GH_STUB_USER_FAIL="1")
    assert res.returncode == 2
    assert fleet.merge_calls() == []


def test_merge_goes_from_agent_profile(fleet: Fleet) -> None:
    """Мерж уходит от профиля ai-prosto: `merged_by` — различитель."""
    res = fleet.run(GH_STUB_HEADREF="feat/ordinary")
    assert res.returncode == 0, res.stderr
    assert fleet.merge_calls()[0].startswith(
        f"GH_CONFIG_DIR={fleet.profile_dir} "
    )


# --- голова уехала после проверки ------------------------------------------


def test_head_moved_after_check_is_refused(fleet: Fleet) -> None:
    """Доказательство 5: проверенный oid уходит в --match-head-commit, и
    форджа отклоняет мерж, когда голова уже другая."""
    res = fleet.run(
        GH_STUB_HEADREF="feat/ordinary",
        GH_STUB_ACTUAL_OID="ffffffffffffffffffffffffffffffffffffffff",
    )
    assert res.returncode == 4, res.stdout
    assert "Head branch was modified" in res.stderr
    assert f"-f sha={HEAD_SHA}" in fleet.merge_calls()[0]


def test_head_unchanged_passes_match(fleet: Fleet) -> None:
    """Тот же oid — мерж проходит: гвард ловит расхождение, а не факт флага."""
    res = fleet.run(GH_STUB_HEADREF="feat/ordinary", GH_STUB_ACTUAL_OID=HEAD_SHA)
    assert res.returncode == 0, res.stderr


# --- dry-run ---------------------------------------------------------------


def test_dry_run_shows_command_without_merging(fleet: Fleet) -> None:
    res = fleet.run("--dry-run", GH_STUB_HEADREF="feat/ordinary")
    assert res.returncode == 0, res.stderr
    assert f"-f sha={HEAD_SHA}" in res.stdout
    assert fleet.merge_calls() == []


# --- пины вызывающего: голова и база ---------------------------------------


def test_expect_head_mismatch_does_not_merge(fleet: Fleet) -> None:
    """Вызывающий проверял другую голову — вердикт не про этот код."""
    res = fleet.run("--expect-head", "f" * 40, GH_STUB_HEADREF="feat/ordinary")
    assert res.returncode == 3, res.stdout
    assert "вызывающий проверял" in res.stderr
    assert fleet.merge_calls() == []


def test_expect_head_match_merges(fleet: Fleet) -> None:
    res = fleet.run("--expect-head", HEAD_SHA, GH_STUB_HEADREF="feat/ordinary")
    assert res.returncode == 0, res.stderr
    assert len(fleet.merge_calls()) == 1


def test_expect_base_mismatch_does_not_merge(fleet: Fleet) -> None:
    """База уехала после вердикта — мерж внёс бы код в невиданную базу.

    Отказ называет ОБЕ базы — от какой вынесен вердикт и какая сейчас: без
    этого оператор видит «что-то не так», но не видит, что именно уехало.
    """
    res = fleet.run("--expect-base", "a" * 40, GH_STUB_HEADREF="feat/ordinary")
    assert res.returncode == 3, res.stdout
    assert "база уехала" in res.stderr
    assert "a" * 40 in res.stderr and BASE_SHA in res.stderr
    assert "перегоните вердикт на новой базе" in res.stderr
    assert fleet.merge_calls() == []


def test_expect_base_match_merges_and_says_so(fleet: Fleet) -> None:
    res = fleet.run("--expect-base", BASE_SHA, GH_STUB_HEADREF="feat/ordinary")
    assert res.returncode == 0, res.stderr
    assert "база пинована" in res.stdout
    assert len(fleet.merge_calls()) == 1


def test_unpinned_base_is_stated_not_implied(fleet: Fleet) -> None:
    """Отсутствие проверки базы обязано быть видно, а не выглядеть как
    пройденная проверка."""
    res = fleet.run(GH_STUB_HEADREF="feat/ordinary")
    assert res.returncode == 0, res.stderr
    assert "база НЕ пинована" in res.stdout


@pytest.mark.parametrize("state", ["BEHIND", "DIRTY"])
def test_forge_reported_stale_base_does_not_merge(
    fleet: Fleet, state: str
) -> None:
    """Мнение форджи про базу называется в отказе поимённо."""
    res = fleet.run(GH_STUB_HEADREF="feat/ordinary", GH_STUB_MERGESTATE=state)
    assert res.returncode == 3, res.stdout
    assert state in res.stderr
    assert fleet.merge_calls() == []


@pytest.mark.parametrize("state", ["CLEAN", "HAS_HOOKS", "UNSTABLE", "BLOCKED"])
def test_allowlisted_merge_state_merges(fleet: Fleet, state: str) -> None:
    """Разрешают мерж ровно четыре значения — и BLOCKED среди них намеренно.

    Ровно из-за BLOCKED дизайн (2026-08-30 §8) отказался от `gh pr merge`:
    тот отказывает сам, ни разу не проверив bypass актора. Решать про bypass —
    дело форджи, а не обвязки.
    """
    res = fleet.run(GH_STUB_HEADREF="feat/ordinary", GH_STUB_MERGESTATE=state)
    assert res.returncode == 0, f"{state}: {res.stderr}"


@pytest.mark.parametrize(
    "state",
    # BEHIND/DIRTY — запрещающие; DRAFT — мержить нечего; UNKNOWN — «форджа
    # ещё не посчитала»; пусто/null/мусор — факт не разобран; NEW_ENUM_VALUE —
    # значение, которого GitHub ещё не придумал.
    ["BEHIND", "DIRTY", "DRAFT", "UNKNOWN", "", "null", "NEW_ENUM_VALUE"],
)
def test_non_allowlisted_merge_state_does_not_merge(
    fleet: Fleet, state: str
) -> None:
    """Неустановленный факт двери не открывает — включая ЭТОТ факт.

    Регрессия на находку ревью #183 (круг 4): проверка была denylist'ом, и
    `mergeStateStatus` оставался единственным фактом PR, чьё пустое или
    неизвестное значение читалось В ПОЛЬЗУ мержа.
    """
    res = fleet.run(GH_STUB_HEADREF="feat/ordinary", GH_STUB_MERGESTATE=state)
    assert res.returncode == 3, res.stdout
    assert fleet.merge_calls() == []


def test_missing_base_oid_does_not_merge(fleet: Fleet) -> None:
    """baseRefOid — такой же fail-closed факт, как остальные."""
    res = fleet.run(GH_STUB_BASEOID="null", GH_STUB_HEADREF="feat/ordinary")
    assert res.returncode == 2, res.stdout
    assert "baseRefOid" in res.stderr
    assert fleet.merge_calls() == []


# --- гвард 4: authority-root пути -------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/ci.yml",
        "profiles/steward.yaml",
        # Перечень защищённых путей и сам защищён: иначе агент вынес бы путь
        # из-под защиты и следом смержил правку в нём.
        "contracts/authority-root/v1/paths.env",
    ],
)
def test_authority_root_paths_block_merge(fleet: Fleet, path: str) -> None:
    """CLAUDE.md: authority-root — всегда человеку. Обвязка обязана держать
    этот инвариант, раз объявлена единственным путём агентского мержа."""
    res = fleet.run(
        GH_STUB_HEADREF="feat/ordinary",
        GH_STUB_FILES=f"lib/x.ex\n{path}",
    )
    assert res.returncode == 3, res.stdout
    assert "authority-root" in res.stderr
    assert path in res.stderr
    assert fleet.merge_calls() == []


def test_authority_prefix_match_is_literal(fleet: Fleet) -> None:
    """`.github/` — литеральный префикс, а не regexp: `xgithub/` не он."""
    res = fleet.run(
        GH_STUB_HEADREF="feat/ordinary", GH_STUB_FILES="xgithub/ci.yml"
    )
    assert res.returncode == 0, res.stderr
    assert len(fleet.merge_calls()) == 1


def test_diff_is_taken_between_two_pinned_shas(fleet: Fleet) -> None:
    """Состав диффа считается между базой и ПРОВЕРЕННОЙ головой.

    Список файлов PR отражал бы голову на момент запроса и с пином не был бы
    связан — force-push между запросами подменил бы проверяемый список,
    оставив мерж на прежнем sha. Сравнение двух sha этой щели не имеет.
    """
    fleet.run(GH_STUB_HEADREF="feat/ordinary")
    compare = [ln for ln in fleet.gh_calls().splitlines() if "/compare/" in ln]
    assert len(compare) == 1
    assert f"compare/{BASE_SHA}...{HEAD_SHA}" in compare[0]
    assert "/files" not in fleet.gh_calls()


def test_compare_failure_does_not_merge(fleet: Fleet) -> None:
    """Состав диффа неизвестен — мерж не выполняется."""
    res = fleet.run(GH_STUB_HEADREF="feat/ordinary", GH_STUB_COMPARE_FAIL="1")
    assert res.returncode == 2, res.stdout
    assert "состав диффа неизвестен" in res.stderr
    assert fleet.merge_calls() == []


def test_compare_response_without_files_does_not_merge(fleet: Fleet) -> None:
    """Ответ без ключа `files` — «состав диффа неизвестен», а не «диффа нет».

    Поэтому фильтр — `.files[]`, без `?`: с `?` jq молча отдал бы пусто, и
    неизвестность снова открыла бы дверь.
    """
    res = fleet.run(
        GH_STUB_HEADREF="feat/ordinary", GH_STUB_COMPARE_NO_FILES="1"
    )
    assert res.returncode == 2, res.stdout
    assert "состав диффа неизвестен" in res.stderr
    assert fleet.merge_calls() == []


def test_missing_authority_ssot_does_not_merge(fleet: Fleet) -> None:
    """Перечень защищённых путей недоступен — отказ, а не пустой перечень."""
    res = fleet.run(
        GH_STUB_HEADREF="feat/ordinary",
        AUTHORITY_PATHS=str(fleet.tmp / "nope.env"),
    )
    assert res.returncode == 2, res.stdout
    assert "authority-root" in res.stderr
    assert fleet.merge_calls() == []


# --- пустые пины ------------------------------------------------------------


@pytest.mark.parametrize("flag", ["--expect-head", "--expect-base"])
def test_empty_pin_is_an_error_not_absence(fleet: Fleet, flag: str) -> None:
    """Пустая строка — не «пина нет», а «вызывающий пинить собирался и не смог».

    Ревью #183 (круг 4): пустой `--expect-head` молчал, тогда как для базы
    правило «отсутствие проверки отличимо от пройденной» уже действовало.
    """
    res = fleet.run(flag, "", GH_STUB_HEADREF="feat/ordinary")
    assert res.returncode == 2, res.stdout
    assert "передан пустым" in res.stderr
    assert fleet.merge_calls() == []


def test_unpinned_head_is_stated_too(fleet: Fleet) -> None:
    """Симметрия с базой: непинованная голова названа вслух."""
    res = fleet.run(GH_STUB_HEADREF="feat/ordinary")
    assert res.returncode == 0, res.stderr
    assert "голова вызывающим НЕ пинована" in res.stdout


def test_pinned_head_is_stated(fleet: Fleet) -> None:
    res = fleet.run("--expect-head", HEAD_SHA, GH_STUB_HEADREF="feat/ordinary")
    assert res.returncode == 0, res.stderr
    assert "голова пинована вызывающим" in res.stdout


# --- PR из форка -----------------------------------------------------------


def test_fork_pr_refuses_delete_branch_before_merging(fleet: Fleet) -> None:
    """Ветка форка живёт в чужом репо — удаление по имени в базовом снесло бы
    одноимённую ветку базового репо (PR из форка с `master` — сам master).

    Отказ ДО мержа, а не пропуск после: попросили то, чего сделать нельзя.
    """
    res = fleet.run(
        "--delete-branch",
        GH_STUB_HEADREF="master",
        GH_STUB_CROSSREPO="true",
    )
    assert res.returncode == 2, res.stdout
    assert "из форка" in res.stderr
    assert fleet.merge_calls() == []
    assert fleet.delete_calls() == []


def test_fork_pr_without_delete_flag_merges(fleet: Fleet) -> None:
    """Мерж PR из форка законен — запрещено только удаление его ветки."""
    res = fleet.run(GH_STUB_HEADREF="feat/x", GH_STUB_CROSSREPO="true")
    assert res.returncode == 0, res.stderr
    assert len(fleet.merge_calls()) == 1
    assert fleet.delete_calls() == []


@pytest.mark.parametrize("value", ["", "null", "yes", "1"])
def test_non_boolean_cross_repository_does_not_merge(
    fleet: Fleet, value: str
) -> None:
    """Булев факт обязан быть булевым — иначе он не разобран."""
    res = fleet.run(GH_STUB_CROSSREPO=value, GH_STUB_HEADREF="feat/x")
    assert res.returncode == 2, res.stdout
    assert fleet.merge_calls() == []


# --- SSOT имён: python-половина --------------------------------------------


def test_shell_and_python_derive_the_same_globs(fleet: Fleet) -> None:
    """Обе половины выводят из шаблона ОДИН И ТОТ ЖЕ глоб.

    Сверяется прямо, зондом `--print-globs`: расхождение вывода перестаёт
    зависеть от того, придумал ли кто-нибудь тест на конкретное имя.
    """
    res = subprocess.run(
        ["sh", str(SCRIPT), "--print-globs"],
        env=fleet.env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == [
        f"candidate={approval_branches.candidate_glob()}",
        f"finalize={approval_branches.finalize_glob()}",
    ]


#: Контракт §I12 — источник схемы имён; SSOT-шаблон обязан ей соответствовать.
CONTRACT = (
    Path(__file__).resolve().parent.parent
    / "docs" / "superpowers" / "specs"
    / "2026-09-09-tasks-supersede-contract-design.md"
)
#: Якоря маркированных пунктов, несущих схему. Смена формулировки уронит тест
#: громко — это и нужно: якорь переставляет человек, увидев, что схема ожила.
_CANDIDATE_ANCHOR = "**candidate-ветка"
_FINALIZE_ANCHOR = "**финализирующая ветка**"


def _skeleton(name: str) -> str:
    """Имя без плейсхолдеров: `{…}` и `<…>` → `@`.

    Так шаблон SSOT и схема контракта сравниваются по СКЕЛЕТУ — по литералам
    и числу мест, а не по тому, как каждая сторона зовёт свои переменные.
    """
    return re.sub(r"\{[^{}]+\}|<[^<>]+>", "@", name)


def _scheme_from_contract(anchor: str) -> str:
    """Схема имени ветки из пункта контракта, помеченного `anchor`."""
    text = CONTRACT.read_text(encoding="utf-8")
    for line in text.splitlines():
        if anchor in line:
            found = re.findall(r"`(spec/[^`]+)`", line)
            assert found, f"в пункте {anchor!r} нет схемы в бэктиках: {line}"
            return found[0]
    raise AssertionError(
        f"якорь {anchor!r} не найден в {CONTRACT.name} — схема имён могла "
        "переехать; переставьте якорь, сверив схему глазами"
    )


def test_ssot_template_matches_the_contract_scheme() -> None:
    """SSOT-шаблон обязан совпадать со схемой §I12 по скелету.

    Регрессия на находку ревью #183: шаблон нёс `-{wave}-{step}`, когда
    контракт уже требовал `-<W>-<K>-<A>`. Гвард при этом продолжал работать
    (глоб не привязан к арности), поэтому НИ ОДИН поведенческий тест
    расхождения не видел — его увидел человек. Этот тест видит.
    """
    assert _skeleton(approval_branches.candidate_template()) == _skeleton(
        _scheme_from_contract(_CANDIDATE_ANCHOR)
    )
    assert _skeleton(
        approval_branches.candidate_template()
        + approval_branches.finalize_suffix()
    ) == _skeleton(_scheme_from_contract(_FINALIZE_ANCHOR))


def test_authority_root_has_one_definition() -> None:
    """Перечень authority-root путей определён ровно в одном месте.

    До круга 4 их было два — кортеж в `accept_pr` и литерал внутри выражения
    в `runner` — и разойтись они могли молча; третье определение в shell
    закрепило бы расхождение. Тест падает, когда список снова собирают
    литералом мимо `authority_root`.
    """
    package = Path(authority_root.__file__).resolve().parent
    offenders: list[str] = []
    for path in sorted(package.glob("*.py")):
        if path.name == "authority_root.py":
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if '".github/"' in stripped or '"profiles/"' in stripped:
                offenders.append(f"{path.name}:{lineno}: {stripped}")
    assert not offenders, (
        "authority-root путь записан литералом мимо authority_root:\n"
        + "\n".join(offenders)
    )


def test_shell_reads_authority_ssot_and_hardcodes_nothing() -> None:
    """Shell-половина берёт перечень из файла, а не носит свой список."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "contracts/authority-root/v1/paths.env" in text
    # Ни один защищённый путь не записан в скрипте литералом — иначе это
    # снова второе определение, теперь через язык.
    for prefix in authority_root.prefixes():
        if prefix == "contracts/authority-root/":
            continue  # путь САМОГО SSOT-файла в скрипте, разумеется, есть
        assert prefix not in text, f"литерал {prefix!r} в merge-pr.sh"


def test_glob_is_not_bound_to_current_arity() -> None:
    """Схлопывание соседних `*` — то, чем глоб переживает смену арности."""
    assert approval_branches.candidate_glob() == "spec/*-approve-*"
    # Прежняя, менее арная форма шаблона даёт ТОТ ЖЕ глоб.
    assert (
        approval_branches._template_glob("spec/{ws_id}-approve-{wave}-{step}")
        == "spec/*-approve-*"
    )


def test_python_half_derives_from_the_same_template() -> None:
    """Строитель и глоб выведены из одного шаблона, а не написаны дважды."""
    branch = approval_branches.candidate_branch("ws-42", 3, 2, 1)
    assert approval_branches.is_candidate(branch)
    assert not approval_branches.is_finalize(branch)
    final = approval_branches.finalize_branch("ws-42", 3, 2, 1)
    assert approval_branches.is_finalize(final)
    assert not approval_branches.is_candidate("spec/ws-42-tasks-approve")


#: Известные ЗАКОННЫЕ литералы approve-веток в governance/ — не узлы бандла.
#: `spec/<ws-id>-tasks-approve` (`--conform-approve`) нормализует frontmatter
#: TASKS-спеки после `spec approve` владельца; предмет другой, мерж законно
#: агентский, гвард её намеренно пропускает. Список закрытый: любой НОВЫЙ
#: литерал обязан быть либо вызовом `approval_branches`, либо осознанным
#: пополнением этого списка — молча появиться он не может.
_ALLOWED_APPROVE_BRANCH_LITERALS = ('f"spec/{ws_id}-tasks-approve"',)


def test_no_second_definition_of_approval_branch_names() -> None:
    """Второго определения имён быть не должно — литерал в governance/ ловим.

    Тест падает, когда механика одобрения строит имя approve-ветки f-строкой
    вместо вызова `approval_branches`: это и есть та вторая строка, которая
    разъедется молча (`_approve_branch` уже разошёлся с контрактом на
    `spec/<ws-id>-bundle-approve`, и никакой глоб этого не заметил бы).
    """
    package = Path(approval_branches.__file__).resolve().parent
    offenders: list[str] = []
    for path in sorted(package.glob("*.py")):
        if path.name == "approval_branches.py":
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            stripped = line.strip()
            if stripped.startswith("#") or "spec/" not in stripped:
                continue
            if "approve" not in stripped:
                continue
            if any(ok in stripped for ok in _ALLOWED_APPROVE_BRANCH_LITERALS):
                continue
            offenders.append(f"{path.name}:{lineno}: {stripped}")
    assert not offenders, (
        "имя approve-ветки собрано литералом мимо approval_branches "
        "(стройте через approval_branches.candidate_branch/finalize_branch "
        "либо пополните _ALLOWED_APPROVE_BRANCH_LITERALS осознанно):\n"
        + "\n".join(offenders)
    )
