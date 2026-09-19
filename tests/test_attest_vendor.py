"""Тесты attest-vendor.sh — детерминированная аттестация целостности
вендор-копии review-kit на волновом PR ре-вендора (решение владельца
2026-09-19). Два фикс-захода после ревью: 6af24ee (C1/C2/I1-I4, см.
.superpowers/sdd/vendor-attest/review.md + fix-brief.md) и 1ce441b
(монотонность апстрима к базе PR, честное «N из M» в теле, тест с
враждебным checksum.sh в голове — см. rereview.md).

Принцип, проверяемый этим набором: НИ ОДИН факт, определяющий исход, не
может приходить из дерева проверяемого PR. Инвентарь и полнота PIN
сверяются checksum.sh, ИЗВЛЕЧЁННЫМ ИЗ АПСТРИМА (не из головы PR — это и
есть ядро C1: старая версия исполняла checksum.sh из дерева головы, и PR,
сузивший собственный инвентарь, получал зелёную аттестацию, не показав
подменённый файл — test_narrowed_inventory_attack_is_caught и
test_hostile_checksum_sh_in_head_is_ignored_apstream_enforced проверяют
именно это, второй — с враждебной копией checksum.sh ПРЯМО В ГОЛОВЕ PR).
Апстрим также обязан двигаться только ВПЕРЁД относительно апстрима базы
PR (монотонность, I1) — иначе ancestry-проверка одна не ловит откат на
старый, но всё ещё валидный коммит доверенной ветки steward. Поэтому
фикстура несёт НАСТОЯЩИЙ (не стаб с управляемым кодом выхода) checksum.sh
в апстриме steward — сокращённая, но функционирующая версия контракта
steward/scripts/review/checksum.sh с маленьким составом кита (3
обязательных члена + 1 переходный вместо 7 боевых), чтобы фикстура
оставалась компактной, сохраняя реальную логику: полнота PIN (включая
`?`-семантику переходных членов), сверка хешей, отказ на постороннем файле.

Стратегия — по образцу tests/test_review_pr.py: git настоящий (bare-репо в
роли origin демо-репо И origin апстрима steward), `gh` — стаб.

Контракт кодов выхода attest-vendor.sh:
  0 — сверка чиста, аттестация опубликована (или dry-run, или дедуп);
  2 — конфигурация/аргументы/состояние PR/публикация/неустановленный факт;
  3 — сверка НЕ прошла (апстрим не предок доверенной ветки или откат
      относительно апстрима базы PR, PR трогает пути вне кита, расхождение
      с апстримом, красный checksum.sh);
  4 — голова PR уехала между сверкой и публикацией.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "attest-vendor.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("jq") is None,
    reason="attest-vendor.sh требует jq безусловно (fail-closed дизайн)",
)

GH_STUB = """#!/usr/bin/env bash
# Стаб gh: логирует каждый вызов, отвечает по переменным GH_STUB_*.
echo "GH_CONFIG_DIR=${GH_CONFIG_DIR:-} gh $*" >> "$GH_STUB_LOG"
case "$*" in
  *"/reviews"*)
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


def _git(*args: str, cwd: Path) -> str:
    res = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
    )
    return res.stdout.strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


KIT_MEMBERS = (
    "scripts/review/build-prompt.sh",
    "scripts/review/local.sh",
    "scripts/review/checksum.sh",
)
# Переходный (опциональный) член — `?path` в required_kit_default: легально
# отсутствует и в PIN, и на диске (двухшаговый ре-вендор состава кита,
# devtools#228). Именно на этом члене проверяется «честное N из M» (residual
# 1 ре-ревью): checksum.sh зелёный без него, тело обязано назвать его
# поимённо, а не растворить в «все совпали».
OPTIONAL_MEMBER = "scripts/review/harness-claude"

# Сокращённая, но НАСТОЯЩАЯ реализация контракта steward/scripts/review/
# checksum.sh: реальное хеширование, реальная проверка полноты PIN против
# зашитого инвентаря (`required_kit`, включая переходную `?`-семантику),
# реальный отказ на постороннем файле. Состав — 3 обязательных члена + 1
# переходный вместо 7 боевых, чтобы фикстура оставалась компактной; логика
# инвариантов — та же самая по духу, не стаб с управляемым `exit $CODE`.
REAL_CHECKSUM_SH = """#!/bin/sh
set -eu
usage() {{ echo "usage: checksum.sh --pin <file> [--root <dir>]" >&2; }}
hash_file() {{
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    else
        shasum -a 256 "$1" | cut -d' ' -f1
    fi
}}
required_kit_default="{required_kit}"
required_kit="$required_kit_default"
pin=""; root=""
while [ $# -gt 0 ]; do
    case "$1" in
        --pin) pin="$2"; shift 2 ;;
        --root) root="$2"; shift 2 ;;
        *) usage; exit 2 ;;
    esac
done
[ -n "$pin" ] || {{ usage; exit 2; }}
[ -f "$pin" ] || {{ echo "нет файла PIN: $pin" >&2; exit 2; }}
if [ -z "$root" ]; then
    case "$pin" in
        */scripts/review/PIN) root=${{pin%/scripts/review/PIN}} ;;
        scripts/review/PIN) root="." ;;
        *) echo "PIN не на каноническом пути" >&2; exit 2 ;;
    esac
fi
checked=0
failed=0
seen=""
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    hash_expected=${{line%%  *}}
    path=${{line#*  }}
    case "$hash_expected" in
        *[!0-9a-f]*|'') echo "битая строка PIN: $line" >&2; exit 2 ;;
    esac
    [ ${{#hash_expected}} -eq 64 ] || {{ echo "битая строка PIN: $line" >&2; exit 2; }}
    case " $required_kit " in
        *" $path "*|*" ?$path "*) ;;
        *) echo "PIN перечисляет файл вне состава кита: $path" >&2; exit 2 ;;
    esac
    checked=$((checked + 1))
    seen="$seen $path"
    if [ ! -f "$root/$path" ]; then
        echo "ФАЙЛ ОТСУТСТВУЕТ: $path" >&2
        failed=$((failed + 1))
        continue
    fi
    actual=$(hash_file "$root/$path")
    if [ "$actual" != "$hash_expected" ]; then
        echo "РАСХОЖДЕНИЕ: $path" >&2
        failed=$((failed + 1))
    fi
done < "$pin"
[ "$checked" -gt 0 ] || {{ echo "PIN пуст" >&2; exit 2; }}
missing=""
for member in $required_kit; do
    case "$member" in
        '?'*)
            optional_path=${{member#?}}
            case " $seen " in
                *" $optional_path "*) ;;
                *)
                    if [ -e "$root/$optional_path" ]; then
                        echo "переходный член присутствует, но не запинован: \
$optional_path" >&2
                        failed=$((failed + 1))
                    fi
                    ;;
            esac
            continue ;;
    esac
    case " $seen " in
        *" $member "*) ;;
        *) missing="$missing $member" ;;
    esac
done
[ -z "$missing" ] || {{
    echo "PIN не покрывает состав кита:$missing" >&2; exit 2; }}
if [ "$failed" -gt 0 ]; then
    echo "копия кита разошлась с PIN: $failed из $checked файла(ов)." >&2
    exit 1
fi
echo "копия кита совпадает с PIN: $checked файла(ов)."
""".format(required_kit=" ".join(KIT_MEMBERS) + " ?" + OPTIONAL_MEMBER)


class Fleet:
    """Синтетический флот: bare-origin демо-репо + bare-origin апстрима
    steward (реальный клон с трекинг-веткой origin/master — ancestry-
    проверка I1 нуждается в настоящем remote, не в голом `git init`)."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.demo_origin = tmp_path / "demo-origin.git"
        self.steward_origin = tmp_path / "steward-origin.git"
        self.fleet_root = tmp_path / "fleet"
        self.repo = self.fleet_root / "demo"
        self.steward = self.fleet_root / "steward"
        self.stub_bin = tmp_path / "bin"
        self.gh_log = tmp_path / "gh.log"
        self.body_out = tmp_path / "posted-body.md"
        self.profile_dir = tmp_path / "gh-profile"
        self.profile_dir.mkdir()
        self.fleet_root.mkdir()

        for origin in (self.demo_origin, self.steward_origin):
            subprocess.run(
                ["git", "init", "--bare", "-b", "master", str(origin)],
                check=True, capture_output=True,
            )

        # --- steward: настоящий клон апстрима, а не голый git init -------
        subprocess.run(
            ["git", "clone", "-q", str(self.steward_origin), str(self.steward)],
            check=True, capture_output=True,
        )
        _git("config", "user.email", "t@example.com", cwd=self.steward)
        _git("config", "user.name", "t", cwd=self.steward)
        self._write_kit(self.steward, "base\n")
        self._write_checksum(self.steward)
        _git("add", ".", cwd=self.steward)
        _git("commit", "-m", "vendor base", cwd=self.steward)
        _git("push", "-q", "origin", "master", cwd=self.steward)
        self.upstream_sha = _git("rev-parse", "HEAD", cwd=self.steward)

        # --- demo: seed + вендор-ветка PR ---------------------------------
        seed = tmp_path / "seed"
        _git("init", "-b", "master", cwd=self._mk(seed))
        _git("config", "user.email", "t@example.com", cwd=seed)
        _git("config", "user.name", "t", cwd=seed)
        (seed / "README.md").write_text("demo\n")
        _git("add", ".", cwd=seed)
        _git("commit", "-m", "base", cwd=seed)
        _git("remote", "add", "origin", str(self.demo_origin), cwd=seed)
        _git("push", "-q", "origin", "master", cwd=seed)

        self.seed = seed
        self._write_kit(seed, "base\n")
        self._write_checksum(seed)
        self._write_pin(seed, self.upstream_sha)
        _git("add", ".", cwd=seed)
        _git("commit", "-m", "vendor: re-vendor review-kit", cwd=seed)
        self.head_sha = _git("rev-parse", "HEAD", cwd=seed)
        _git("push", "-q", "origin", "HEAD:refs/pull/7/head", cwd=seed)

        subprocess.run(
            ["git", "clone", "-q", str(self.demo_origin), str(self.repo)],
            check=True, capture_output=True,
        )
        gh_url = "git@github.com:andrei-shtanakov/demo.git"
        _git("remote", "set-url", "origin", gh_url, cwd=self.repo)
        _git(
            "config", f"url.{self.demo_origin}.insteadOf", gh_url, cwd=self.repo,
        )

        self.stub_bin.mkdir()
        gh = self.stub_bin / "gh"
        gh.write_text(GH_STUB)
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)

    @staticmethod
    def _mk(path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _write_kit(self, root: Path, content: str) -> None:
        """Пишет ОБА generic-члена (build-prompt.sh, local.sh) — не
        checksum.sh, у него фиксированное содержимое (_write_checksum), и
        не OPTIONAL_MEMBER, который по умолчанию легально отсутствует."""
        for member in KIT_MEMBERS[:2]:
            f = root / member
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content)

    def _write_checksum(self, root: Path) -> None:
        checksum = root / "scripts" / "review" / "checksum.sh"
        checksum.parent.mkdir(parents=True, exist_ok=True)
        checksum.write_text(REAL_CHECKSUM_SH)
        checksum.chmod(checksum.stat().st_mode | stat.S_IXUSR)

    def _write_pin(
        self, root: Path, upstream_sha: str, members: tuple[str, ...] = KIT_MEMBERS,
    ) -> None:
        pin = root / "scripts" / "review" / "PIN"
        pin.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"# SOURCE: steward @ {upstream_sha} (master, test)"]
        for member in members:
            lines.append(f"{_sha256(root / member)}  {member}")
        pin.write_text("\n".join(lines) + "\n")

    def push_head(self) -> None:
        """Закоммитить текущее состояние seed как новую голову PR #7."""
        _git("add", ".", cwd=self.seed)
        _git("commit", "-m", "tamper", cwd=self.seed)
        self.head_sha = _git("rev-parse", "HEAD", cwd=self.seed)
        _git(
            "push", "-q", "-f", "origin", "HEAD:refs/pull/7/head", cwd=self.seed,
        )

    def rebase_pr_on_merged_vendor(
        self,
        base_upstream_sha: str,
        base_content: str,
        head_upstream_sha: str,
        head_content: str,
    ) -> None:
        """Смержить в master УЖЕ отревьюенный вендор `base_upstream_sha`
        (отдельным чекаутом, не трогая `seed`), затем открыть НОВУЮ голову
        PR #7 поверх этого master, вендорящую `head_upstream_sha`. Нужно
        тестам монотонности (I1): апстрим базы PR читается ИЗ origin/master,
        апстрим головы — из PIN PR-ветки."""
        base_checkout = self.tmp / "base-vendor-checkout"
        subprocess.run(
            ["git", "clone", "-q", str(self.demo_origin), str(base_checkout)],
            check=True, capture_output=True,
        )
        _git("config", "user.email", "t@example.com", cwd=base_checkout)
        _git("config", "user.name", "t", cwd=base_checkout)
        self._write_kit(base_checkout, base_content)
        self._write_checksum(base_checkout)
        self._write_pin(base_checkout, base_upstream_sha)
        _git("add", ".", cwd=base_checkout)
        _git("commit", "-m", "vendor: base state (уже смержено)", cwd=base_checkout)
        _git("push", "-q", "origin", "HEAD:master", cwd=base_checkout)

        _git("fetch", "-q", "origin", "master", cwd=self.seed)
        _git("checkout", "-B", "pr-branch", "origin/master", cwd=self.seed)
        self._write_kit(self.seed, head_content)
        self._write_checksum(self.seed)
        self._write_pin(self.seed, head_upstream_sha)
        _git("add", ".", cwd=self.seed)
        _git("commit", "-m", "vendor: re-vendor review-kit (v2)", cwd=self.seed)
        self.head_sha = _git("rev-parse", "HEAD", cwd=self.seed)
        _git(
            "push", "-q", "-f", "origin", "HEAD:refs/pull/7/head", cwd=self.seed,
        )

    def env(self, **extra: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            PATH=f"{self.stub_bin}:{env['PATH']}",
            FLEET_ROOT=str(self.fleet_root),
            REVIEW_GH_CONFIG_DIR=str(self.profile_dir),
            GH_STUB_LOG=str(self.gh_log),
            GH_STUB_HEADOID=self.head_sha,
            GH_STUB_BODY_OUT=str(self.body_out),
            GIT_TERMINAL_PROMPT="0",
            GIT_SSH_COMMAND="false",
        )
        env.update(extra)
        return env

    def run(self, *args: str, **env_extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", str(SCRIPT), *args],
            env=self.env(**env_extra),
            capture_output=True,
            text=True,
            check=False,
        )

    def gh_calls(self) -> str:
        return self.gh_log.read_text() if self.gh_log.exists() else ""

    def reviews_file(self, *reviews: dict) -> str:
        path = self.tmp / "reviews.json"
        path.write_text(json.dumps(list(reviews)))
        return str(path)


def _review(state: str, body: str, login: str = "ai-prosto") -> dict:
    return {"user": {"login": login}, "state": state, "body": body}


@pytest.fixture
def fleet(tmp_path: Path) -> Fleet:
    return Fleet(tmp_path)


# --- аргументы / конфигурация -----------------------------------------------


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


def test_unknown_steward(fleet: Fleet) -> None:
    res = fleet.run("demo", "7", STEWARD_DIR=str(fleet.tmp / "nope-steward"))
    assert res.returncode == 2
    assert "steward" in res.stderr
    assert "pr review" not in fleet.gh_calls()


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


# --- сверка чистая, тело не содержит ничего из дерева PR (C2) --------------


def test_clean_verdict_approves(fleet: Fleet) -> None:
    res = fleet.run("demo", "7")
    assert res.returncode == 0, res.stderr
    calls = fleet.gh_calls()
    assert "pr review 7" in calls
    assert "--approve" in calls
    assert "--repo andrei-shtanakov/demo" in calls
    for line in calls.splitlines():
        assert f"GH_CONFIG_DIR={fleet.profile_dir}" in line
    body = fleet.body_out.read_text()
    assert fleet.head_sha in body
    assert fleet.upstream_sha in body
    # N из M: 3 присутствующих обязательных члена из 4 (переходный
    # harness-claude легально отсутствует на этом репо — residual 1).
    assert f"{len(KIT_MEMBERS)} из {len(KIT_MEMBERS) + 1} член(ов)" in body
    assert OPTIONAL_MEMBER in body
    assert "монотонность не проверялась" in body
    # C2: тело — только наш собственный текст. Ни путей из PIN, ни сырого
    # вывода апстримного checksum.sh (даже успешного) в теле нет; исключение
    # — OPTIONAL_MEMBER выше, он читается из ДОВЕРЕННОГО апстримного
    # checksum.sh, не из PR.
    assert "build-prompt.sh" not in body
    assert "local.sh" not in body
    assert "копия кита совпадает с PIN" not in body


def test_optional_member_fully_vendored_reports_full_count(fleet: Fleet) -> None:
    """Когда переходный член ТОЖЕ довезён (и в steward, и в голове, PIN его
    покрывает) — N == M, отдельной строки про отсутствие нет."""
    (fleet.steward / OPTIONAL_MEMBER).write_text("harness\n")
    (fleet.steward / OPTIONAL_MEMBER).chmod(0o755)
    _git("add", ".", cwd=fleet.steward)
    _git("commit", "-m", "vendor harness-claude", cwd=fleet.steward)
    _git("push", "-q", "origin", "master", cwd=fleet.steward)
    new_upstream_sha = _git("rev-parse", "HEAD", cwd=fleet.steward)

    (fleet.seed / OPTIONAL_MEMBER).write_text("harness\n")
    (fleet.seed / OPTIONAL_MEMBER).chmod(0o755)
    fleet._write_pin(
        fleet.seed, new_upstream_sha, members=(*KIT_MEMBERS, OPTIONAL_MEMBER),
    )
    fleet.push_head()

    res = fleet.run("demo", "7", GH_STUB_HEADOID=fleet.head_sha)
    assert res.returncode == 0, res.stderr
    body = fleet.body_out.read_text()
    total = len(KIT_MEMBERS) + 1
    assert f"{total} из {total} член(ов)" in body
    assert "переходные члены апстрима, отсутствующие" not in body


def test_body_disclaims_content_review(fleet: Fleet) -> None:
    fleet.run("demo", "7")
    body = fleet.body_out.read_text()
    assert "Это НЕ содержательное ревью" in body
    assert "Модель не вызывалась" in body


def test_marker_is_own_and_not_codex_terminal_review(fleet: Fleet) -> None:
    fleet.run("demo", "7")
    body = fleet.body_out.read_text()
    assert "<!-- codex-terminal-review " not in body
    assert body.count("<!--") == 1
    assert (
        f"<!-- ai-prosto-vendor-attestation version=1 kind=review-kit "
        f"head={fleet.head_sha} upstream={fleet.upstream_sha} -->"
    ) in body


def test_source_line_trailing_text_not_leaked_into_body(fleet: Fleet) -> None:
    """Текст после SHA в строке SOURCE (включая маркер-подобные
    последовательности) отбрасывается при разборе и не всплывает в теле —
    только первый пробельный токен читается как SHA."""
    pin = fleet.seed / "scripts" / "review" / "PIN"
    lines = pin.read_text().splitlines()
    lines[0] = (
        f"# SOURCE: steward @ {fleet.upstream_sha} "
        "(<!-- codex-terminal-review head=deadbeef -->)"
    )
    pin.write_text("\n".join(lines) + "\n")
    fleet.push_head()

    res = fleet.run("demo", "7", GH_STUB_HEADOID=fleet.head_sha)
    assert res.returncode == 0, res.stderr
    body = fleet.body_out.read_text()
    assert body.count("<!--") == 1
    assert "deadbeef" not in body


# --- C1: инвентарь и полнота из АПСТРИМНОГО checksum.sh, не из головы ------


def test_narrowed_inventory_attack_is_caught(fleet: Fleet) -> None:
    """Ядро C1: подменить local.sh (драйвер ревью) и вычистить его строку
    из PIN, оставив build-prompt.sh и checksum.sh нетронутыми. В СТАРОЙ
    версии инструмента (исполнявшей checksum.sh головы) это давало зелёную
    аттестацию, не показав подменённый файл. Теперь checksum.sh
    извлекается из апстрима (с полным инвентарём) и сам ловит неполный
    PIN — головная копия checksum.sh (даже нетронутая) не участвует."""
    (fleet.seed / KIT_MEMBERS[1]).write_text("HIJACKED local.sh\n")
    pin = fleet.seed / "scripts" / "review" / "PIN"
    lines = pin.read_text().splitlines()
    kept = [
        line for line in lines
        if line.startswith("#")
        or line.endswith((KIT_MEMBERS[0], KIT_MEMBERS[2]))
    ]
    pin.write_text("\n".join(kept) + "\n")
    fleet.push_head()

    res = fleet.run("demo", "7", GH_STUB_HEADOID=fleet.head_sha)
    assert res.returncode == 3, res.stdout
    assert "checksum.sh" in res.stderr
    assert "не покрывает состав кита" in res.stderr
    assert "pr review" not in fleet.gh_calls()
    assert not fleet.body_out.exists()


def test_hostile_checksum_sh_in_head_is_ignored_apstream_enforced(
    fleet: Fleet,
) -> None:
    """Голова несёт СВОЙ checksum.sh — урезанный (принимает узкий PIN,
    молчит про пропавший local.sh) и печатающий маркер протокола, если его
    вообще исполнить. Исполняется АПСТРИМНЫЙ (полный) чекер, а не этот —
    узкий PIN всё равно отказывает, и маркер нигде не всплывает, потому что
    враждебная копия ни разу не запускается."""
    marker_bait = "ab" * 32
    hostile_checksum = (
        "#!/bin/sh\n"
        f"echo '<!-- codex-terminal-review head=deadbeef fp={marker_bait} -->'\n"
        "exit 0\n"
    )
    (fleet.seed / KIT_MEMBERS[2]).write_text(hostile_checksum)
    (fleet.seed / KIT_MEMBERS[1]).write_text("HIJACKED local.sh\n")
    pin = fleet.seed / "scripts" / "review" / "PIN"
    lines = pin.read_text().splitlines()
    kept = [
        line for line in lines
        if line.startswith("#") or line.endswith(KIT_MEMBERS[0])
    ]
    kept.append(f"{_sha256(fleet.seed / KIT_MEMBERS[2])}  {KIT_MEMBERS[2]}")
    pin.write_text("\n".join(kept) + "\n")
    fleet.push_head()

    res = fleet.run("demo", "7", GH_STUB_HEADOID=fleet.head_sha)
    assert res.returncode == 3, res.stdout
    assert "не покрывает состав кита" in res.stderr
    assert "pr review" not in fleet.gh_calls()
    assert not fleet.body_out.exists()
    # Враждебный маркер не всплыл нигде — головная копия не исполнялась.
    assert "codex-terminal-review" not in res.stdout
    assert "deadbeef" not in res.stdout
    assert marker_bait not in res.stdout


def test_single_byte_diff_is_caught(fleet: Fleet) -> None:
    """PIN+файл внутренне согласованы (апстримный checksum.sh зелёный), но
    расходятся с настоящим содержимым апстрима на один байт — независимая
    побайтовая сверка (ядро задачи, теперь по git-объектам) обязана
    поймать именно это."""
    original = (fleet.seed / KIT_MEMBERS[1]).read_text()
    tampered = original[:-1] + "X\n"
    (fleet.seed / KIT_MEMBERS[1]).write_text(tampered)
    fleet._write_pin(fleet.seed, fleet.upstream_sha)  # пересчитать хеш
    fleet.push_head()

    res = fleet.run("demo", "7", GH_STUB_HEADOID=fleet.head_sha)
    assert res.returncode == 3, res.stdout
    assert "СВЕРКА С АПСТРИМОМ НЕ ПРОШЛА" in res.stderr
    assert "байты расходятся" in res.stderr
    assert "pr review" not in fleet.gh_calls()
    assert not fleet.body_out.exists()


# --- I4: режим файла (бит исполнения) тоже часть копии ----------------------


def test_file_mode_mismatch_is_caught(fleet: Fleet) -> None:
    member_path = fleet.seed / KIT_MEMBERS[1]
    member_path.chmod(member_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP
                       | stat.S_IXOTH)
    fleet.push_head()

    res = fleet.run("demo", "7", GH_STUB_HEADOID=fleet.head_sha)
    assert res.returncode == 3, res.stdout
    assert "режим расходится" in res.stderr
    assert "pr review" not in fleet.gh_calls()


# --- I1: апстрим обязан быть предком доверенной ветки steward --------------


def test_upstream_not_ancestor_of_trusted_branch_is_refused(fleet: Fleet) -> None:
    """SOURCE называет РЕАЛЬНЫЙ, но не влитый в master коммит steward
    (боковая ветка) — существующий коммит недостаточен, нужен предок
    доверенной ветки."""
    _git("checkout", "-b", "evil", cwd=fleet.steward)
    (fleet.steward / KIT_MEMBERS[0]).write_text("evil vendor\n")
    _git("commit", "-am", "evil vendor", cwd=fleet.steward)
    evil_sha = _git("rev-parse", "HEAD", cwd=fleet.steward)
    _git("push", "-q", "origin", "evil", cwd=fleet.steward)
    _git("checkout", "master", cwd=fleet.steward)

    fleet._write_pin(fleet.seed, evil_sha)
    fleet.push_head()

    res = fleet.run("demo", "7", GH_STUB_HEADOID=fleet.head_sha)
    assert res.returncode == 3, res.stdout
    assert "НЕ является предком" in res.stderr
    assert "pr review" not in fleet.gh_calls()


def test_unresolvable_upstream_commit_is_config_error(fleet: Fleet) -> None:
    fleet._write_pin(fleet.seed, "0" * 40)
    fleet.push_head()
    res = fleet.run("demo", "7", GH_STUB_HEADOID=fleet.head_sha)
    assert res.returncode == 2
    assert "не найден" in res.stderr
    assert "pr review" not in fleet.gh_calls()


def test_short_sha_is_rejected(fleet: Fleet) -> None:
    pin = fleet.seed / "scripts" / "review" / "PIN"
    lines = pin.read_text().splitlines()
    lines[0] = f"# SOURCE: steward @ {fleet.upstream_sha[:7]} (short)"
    pin.write_text("\n".join(lines) + "\n")
    fleet.push_head()
    res = fleet.run("demo", "7", GH_STUB_HEADOID=fleet.head_sha)
    assert res.returncode == 2
    assert "40 hex" in res.stderr
    assert "pr review" not in fleet.gh_calls()


# --- монотонность ре-вендора: апстрим не может откатиться назад ------------


def _advance_steward(fleet: Fleet, content: str) -> str:
    fleet._write_kit(fleet.steward, content)
    _git("add", ".", cwd=fleet.steward)
    _git("commit", "-m", "vendor v2", cwd=fleet.steward)
    _git("push", "-q", "origin", "master", cwd=fleet.steward)
    return _git("rev-parse", "HEAD", cwd=fleet.steward)


def test_monotonic_forward_revendor_is_accepted(fleet: Fleet) -> None:
    """База PR уже везёт v1 (fleet.upstream_sha, смержено), голова
    двигается вперёд на v2 (потомок v1 в истории steward) — законный
    ре-вендор новой версии кита, обязан пройти."""
    v2_sha = _advance_steward(fleet, "v2\n")
    fleet.rebase_pr_on_merged_vendor(
        fleet.upstream_sha, "base\n", v2_sha, "v2\n",
    )

    res = fleet.run("demo", "7", GH_STUB_HEADOID=fleet.head_sha)
    assert res.returncode == 0, res.stderr
    body = fleet.body_out.read_text()
    assert "потомок апстрима базы PR" in body
    assert fleet.upstream_sha in body


def test_downgrade_revendor_is_refused(fleet: Fleet) -> None:
    """База PR уже везёт v2 (свежее, смержено), голова откатывает на v1 —
    v1 РЕАЛЬНО существует и остаётся предком origin/master steward (ancestry
    в одиночку это пропустила бы), но это откат правки внутри кита: этот
    класс атаки закрывает именно монотонность к базе PR, а не ancestry."""
    v2_sha = _advance_steward(fleet, "v2\n")
    fleet.rebase_pr_on_merged_vendor(
        v2_sha, "v2\n", fleet.upstream_sha, "base\n",
    )

    res = fleet.run("demo", "7", GH_STUB_HEADOID=fleet.head_sha)
    assert res.returncode == 3, res.stdout
    assert "это откат" in res.stderr
    assert "pr review" not in fleet.gh_calls()
    assert not fleet.body_out.exists()


def test_first_revendor_in_repo_skips_monotonicity_with_note(fleet: Fleet) -> None:
    """Дефолтная фикстура (без rebase_pr_on_merged_vendor) — это ровно
    случай «в базе PR нет PIN»: монотонность пропускается ГРОМКО, не
    молча."""
    res = fleet.run("demo", "7")
    assert res.returncode == 0, res.stderr
    assert "первый ре-вендор" in res.stderr
    assert "монотонность не проверялась" in fleet.body_out.read_text()


# --- I2: PR обязан целиком лежать внутри состава кита ------------------------


def test_pr_touching_path_outside_kit_is_refused(fleet: Fleet) -> None:
    (fleet.seed / ".github").mkdir(exist_ok=True)
    (fleet.seed / ".github" / "workflows.yml").write_text("on: push\n")
    fleet.push_head()

    res = fleet.run("demo", "7", GH_STUB_HEADOID=fleet.head_sha)
    assert res.returncode == 3, res.stdout
    assert "вне состава кита" in res.stderr
    assert ".github/workflows.yml" in res.stderr
    assert "pr review" not in fleet.gh_calls()


# --- I3: красный вердикт не гасится, дедуп -----------------------------------


def test_red_verdict_blocks_publication(fleet: Fleet) -> None:
    reviews = fleet.reviews_file(_review("CHANGES_REQUESTED", "не так"))
    res = fleet.run("demo", "7", GH_STUB_REVIEWS_JSON=reviews)
    assert res.returncode == 2, res.stdout
    assert "CHANGES_REQUESTED" in res.stderr
    assert "не гасит" in res.stderr
    assert "pr review" not in fleet.gh_calls()


def test_dedup_idempotent_on_same_head(fleet: Fleet) -> None:
    first = fleet.run("demo", "7")
    assert first.returncode == 0, first.stderr
    published_body = fleet.body_out.read_text()
    calls_after_first = fleet.gh_calls().count("pr review")

    reviews = fleet.reviews_file(_review("APPROVED", published_body))
    second = fleet.run("demo", "7", GH_STUB_REVIEWS_JSON=reviews)
    assert second.returncode == 0, second.stderr
    assert "уже опубликована" in second.stdout
    assert fleet.gh_calls().count("pr review") == calls_after_first


# --- голова уехала -----------------------------------------------------------


def test_head_moved_aborts_publish(fleet: Fleet) -> None:
    res = fleet.run("demo", "7", GH_STUB_HEADOID2="0" * 40)
    assert res.returncode == 4
    assert "уехала" in res.stderr
    assert "pr review" not in fleet.gh_calls()


# --- dry-run -----------------------------------------------------------------


def test_dry_run_prints_body_and_publishes_nothing(fleet: Fleet) -> None:
    res = fleet.run("demo", "7", "--dry-run")
    assert res.returncode == 0, res.stderr
    assert "pr review" not in fleet.gh_calls()
    assert "Vendor-copy integrity attestation" in res.stdout
    assert "ai-prosto-vendor-attestation" in res.stdout
    assert not fleet.body_out.exists()


def test_dry_run_still_enforces_checks(fleet: Fleet) -> None:
    (fleet.seed / KIT_MEMBERS[1]).write_text("HIJACKED local.sh\n")
    pin = fleet.seed / "scripts" / "review" / "PIN"
    lines = pin.read_text().splitlines()
    kept = [
        line for line in lines
        if line.startswith("#")
        or line.endswith((KIT_MEMBERS[0], KIT_MEMBERS[2]))
    ]
    pin.write_text("\n".join(kept) + "\n")
    fleet.push_head()

    res = fleet.run("demo", "7", "--dry-run", GH_STUB_HEADOID=fleet.head_sha)
    assert res.returncode == 3
    assert "pr review" not in fleet.gh_calls()


# --- разбор PIN --------------------------------------------------------------


def test_missing_source_line_is_config_error(fleet: Fleet) -> None:
    pin = fleet.seed / "scripts" / "review" / "PIN"
    lines = [
        line for line in pin.read_text().splitlines()
        if not line.startswith("# SOURCE:")
    ]
    pin.write_text("\n".join(lines) + "\n")
    fleet.push_head()

    res = fleet.run("demo", "7", GH_STUB_HEADOID=fleet.head_sha)
    assert res.returncode == 2
    assert "SOURCE" in res.stderr
    assert "pr review" not in fleet.gh_calls()


def test_missing_pin_is_config_error(fleet: Fleet) -> None:
    (fleet.seed / "scripts" / "review" / "PIN").unlink()
    fleet.push_head()

    res = fleet.run("demo", "7", GH_STUB_HEADOID=fleet.head_sha)
    assert res.returncode == 2
    assert "PIN" in res.stderr
    assert "pr review" not in fleet.gh_calls()


# --- изолированность рабочего дерева -----------------------------------------


def test_target_repo_worktree_untouched(fleet: Fleet) -> None:
    fleet.run("demo", "7")
    assert _git("branch", "--show-current", cwd=fleet.repo) == "master"
    worktrees = _git("worktree", "list", "--porcelain", cwd=fleet.repo)
    assert worktrees.count("worktree ") == 1
