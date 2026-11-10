"""`dfl init-repo` — подготовка репо коллеги к конвейеру DarkFactory.

Открывает PR с шаблонами (профили, review-kit, конфиг spec-runner) из этого
checkout'а devtools. Мерж PR — за владельцем репо; защиту ветки и ruleset'ы
чужого репо `dfl` не читает на запись и не меняет — только отказывает, если
они требуют ревью, которого у коллеги в режиме test не будет.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from governance import user_targets
from governance.brief_tools import LABEL
from lite.install import Runner

INIT_BRANCH = "darkfactory/init-repo"
COMMIT_MESSAGE = "DarkFactory: подготовка репо (dfl init-repo)"
README_URL = "https://github.com/andrei-shtanakov/devtools/blob/master/README.md"
LABEL_COLOR = "b60205"
LABEL_DESCRIPTION = "PR мержит человек, не агент"
_SPEC_RUNNER = (
    "templates/darkfactory/target/spec-runner.config.yaml",
    "spec-runner.config.yaml",
)
_REVIEW_KIT = (
    "apply-threshold.sh",
    "build-prompt.sh",
    "checksum.sh",
    "collect-context.sh",
    "harness-claude",
    "local.sh",
    "PIN",
    "prose-paths.env",
)


def _same(path: str) -> tuple[str, str]:
    return (path, path)


#: (путь в devtools, путь в репо коллеги).
TEMPLATE_FILES: tuple[tuple[str, str], ...] = (
    *(
        _same(f"profiles/{name}.yaml")
        for name in ("team-exp", "roles", "gate-catalog", "risk-model")
    ),
    *(_same(f"scripts/review/{name}") for name in _REVIEW_KIT),
    _same(".github/codex/review-schema.json"),
    _same(".github/codex/review-prompt.md"),
    _SPEC_RUNNER,
)

Gh = Callable[[list[str]], subprocess.CompletedProcess]
DEVTOOLS = Path(__file__).resolve().parent.parent


class InitError(Exception):
    """Отказ `init-repo`: причина и следующий шаг (код 2)."""


def real_gh(argv: list[str]) -> subprocess.CompletedProcess:
    """Настоящий `gh` (argv без имени программы)."""
    return subprocess.run(["gh", *argv], capture_output=True, text=True, check=False)


def _json_values(text: str) -> list[object]:
    """Все JSON-значения подряд (`gh api --paginate` склеивает страницы)."""
    decoder, out, pos = json.JSONDecoder(), [], 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        value, pos = decoder.raw_decode(text, pos)
        out.append(value)
    return out


def _unread(what: str, done: subprocess.CompletedProcess) -> str:
    detail = (done.stderr or done.stdout or "").strip() or f"код {done.returncode}"
    return (
        f"правила ветки не прочитаны ({what}: {detail}) — это не «правил нет»; "
        "проверьте gh auth status и права на репо, затем повторите dfl init-repo"
    )


def _rules_refusal(slug: str, branch: str, gh: Gh) -> str | None:
    done = gh(["api", "--paginate", f"repos/{slug}/rules/branches/{branch}"])
    if done.returncode != 0:
        return _unread("rulesets", done)
    try:
        pages = _json_values(done.stdout or "")
        rules = [r for page in pages for r in page]  # type: ignore[union-attr]
        for rule in rules:
            count = (rule.get("parameters") or {}).get(
                "required_approving_review_count", 0
            )
            if rule.get("type") == "pull_request" and count > 0:
                return (
                    f"ruleset ветки {branch} содержит правило pull_request "
                    f"(обязательных ревью: {count}); dfl чужую защиту не меняет — "
                    "снимите требование ревью сами или смержите PR вручную позже"
                )
    except (ValueError, TypeError, AttributeError) as exc:
        return _unread("rulesets: ответ не разобран", _fake(exc))
    return None


def _fake(exc: Exception) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 1, "", str(exc))


def _protection_refusal(slug: str, branch: str, gh: Gh) -> str | None:
    done = gh(["api", f"repos/{slug}/branches/{branch}/protection"])
    if done.returncode != 0:
        text = f"{done.stderr}\n{done.stdout}"
        if "Branch not protected" in text and "404" in text:
            return None
        return _unread("защита ветки", done)
    try:
        body = json.loads(done.stdout or "{}")
        count = (body.get("required_pull_request_reviews") or {}).get(
            "required_approving_review_count", 0
        )
    except (ValueError, TypeError, AttributeError) as exc:
        return _unread("защита ветки: ответ не разобран", _fake(exc))
    if count > 0:
        return (
            f"защита ветки {branch} требует ревью (обязательных: {count}); "
            "dfl чужую защиту не меняет — снимите требование сами"
        )
    return None


def review_rules_refusal(slug: str, branch: str, gh: Gh) -> str | None:
    """Причина отказа, если ветка требует ревью (или правила не прочитаны)."""
    return _rules_refusal(slug, branch, gh) or _protection_refusal(slug, branch, gh)


def conflicts(repo: Path) -> list[str]:
    """Пути назначения, уже существующие в рабочем дереве `repo`."""
    return [dst for _, dst in TEMPLATE_FILES if (repo / dst).exists()]


def _git(run: Runner, repo: Path, *args: str) -> str:
    done = run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip()
        raise InitError(f"git {' '.join(args)}: {detail} — исправьте и повторите")
    return (done.stdout or "").strip()


def _default_branch(run: Runner, repo: Path) -> str:
    done = run(
        ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise InitError(
            "ветка по умолчанию неизвестна (нет origin/HEAD) — выполните "
            "git remote set-head origin -a и повторите"
        )
    return (done.stdout or "").strip().removeprefix("origin/")


def _preflight(run: Runner, repo: Path) -> tuple[str, str]:
    """Чистое дерево на ветке по умолчанию; (slug, ветка по умолчанию)."""
    default = _default_branch(run, repo)
    current = _git(run, repo, "rev-parse", "--abbrev-ref", "HEAD")
    if current != default:
        raise InitError(
            f"репо на ветке {current!r}, нужна {default!r} — "
            f"git switch {default} и повторите"
        )
    if _git(run, repo, "status", "--porcelain"):
        raise InitError(
            "рабочее дерево не чисто — закоммитьте или уберите изменения и повторите"
        )
    url = _git(run, repo, "config", "--get", "remote.origin.url")
    try:
        return user_targets.slug_from_url(url), default
    except user_targets.TargetsError as exc:
        raise InitError(f"{exc} — нужен origin на GitHub") from exc


def _open_pr_url(slug: str, gh: Gh) -> str | None:
    done = gh(
        ["pr", "list", "--repo", slug, "--head", INIT_BRANCH, "--state", "open",
         "--json", "url"]
    )  # fmt: skip
    if done.returncode != 0:
        raise InitError(_unread("список PR", done).replace("правила ветки", "PR"))
    prs = json.loads(done.stdout or "[]")
    return str(prs[0]["url"]) if prs else None


def _copy_files(repo: Path, devtools: Path) -> None:
    for src_rel, dst_rel in TEMPLATE_FILES:
        src, dst = devtools / src_rel, repo / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        dst.chmod(0o755 if src.stat().st_mode & 0o111 else 0o644)


def _pr_body() -> str:
    files = "\n".join(f"- `{dst}`" for _, dst in TEMPLATE_FILES)
    return (
        "Подготовка репо к конвейеру DarkFactory (`dfl init-repo`).\n\n"
        f"Добавляемые файлы:\n{files}\n\n"
        "Этот PR мержите вы — агент его не мержит.\n"
        f"Дальше: {README_URL}\n"
    )


def _ensure_label(slug: str, gh: Gh) -> None:
    done = gh(["label", "list", "--repo", slug, "--limit", "200", "--json", "name"])
    if done.returncode != 0:
        raise InitError(_unread("метки", done).replace("правила ветки", "метки"))
    if LABEL in {item["name"] for item in json.loads(done.stdout or "[]")}:
        return
    created = gh(
        ["label", "create", LABEL, "--repo", slug, "--color", LABEL_COLOR,
         "--description", LABEL_DESCRIPTION]
    )  # fmt: skip
    if created.returncode != 0:
        raise InitError(f"метка {LABEL} не создана: {created.stderr.strip()}")


def _open_pr(slug: str, base: str, gh: Gh) -> str:
    done = gh(
        ["pr", "create", "--repo", slug, "--base", base, "--head", INIT_BRANCH,
         "--title", COMMIT_MESSAGE, "--body", _pr_body()]
    )  # fmt: skip
    if done.returncode != 0:
        raise InitError(
            f"PR не создан: {(done.stderr or '').strip()} — ветка {INIT_BRANCH} "
            "уже на origin; создайте PR вручную"
        )
    url = (done.stdout or "").strip()
    _ensure_label(slug, gh)
    labeled = gh(["pr", "edit", url, "--repo", slug, "--add-label", LABEL])
    if labeled.returncode != 0:
        raise InitError(f"метка не поставлена на {url}: {labeled.stderr.strip()}")
    return url


def _publish(
    repo: Path, devtools: Path, slug: str, base: str, gh: Gh, run: Runner
) -> str:
    _git(run, repo, "switch", "-c", INIT_BRANCH)
    try:
        _copy_files(repo, devtools)
        _git(run, repo, "add", "--", *(dst for _, dst in TEMPLATE_FILES))
        _git(run, repo, "commit", "-m", COMMIT_MESSAGE)
        _git(run, repo, "push", "-u", "origin", INIT_BRANCH)
        return _open_pr(slug, base, gh)
    finally:
        _git(run, repo, "switch", base)


def _plan(repo: Path, slug: str, base: str, gh: Gh) -> str | None:
    """URL уже открытого PR; иначе `None`. Отказ — `InitError`."""
    url = _open_pr_url(slug, gh)
    if url is not None:
        return url
    refusal = review_rules_refusal(slug, base, gh)
    if refusal is not None:
        raise InitError(refusal)
    found = conflicts(repo)
    if found:
        raise InitError(
            "в репо уже есть файлы назначения: "
            + ", ".join(found)
            + " — переименуйте или удалите их (ничего не создано) и повторите"
        )
    return None


def init_repo(repo: Path, devtools: Path, gh: Gh, run: Runner = subprocess.run) -> int:
    """Открыть PR с шаблонами DarkFactory в `repo`; 0, либо 2 с причиной."""
    try:
        slug, base = _preflight(run, repo)
        existing = _plan(repo, slug, base, gh)
        if existing is not None:
            print(f"PR подготовки уже открыт: {existing} — смержите его сами")
            return 0
        url = _publish(repo, devtools, slug, base, gh, run)
    except InitError as exc:
        print(f"dfl init-repo: {exc}", file=sys.stderr)
        return 2
    print(f"PR подготовки открыт: {url} — смержите его сами, затем dfl run")
    return 0
