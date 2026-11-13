"""`dfl init-repo` — подготовка репо коллеги к конвейеру DarkFactory.

Открывает PR с шаблонами (профили, review-kit, конфиг spec-runner) из этого
checkout'а devtools. Мерж PR — за владельцем репо; защиту ветки и ruleset'ы
чужого репо `dfl` не читает на запись и не меняет — только отказывает, если
они требуют ревью, которого у коллеги в режиме test не будет.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

from governance import user_targets
from governance.brief_tools import LABEL
from lite.install import Runner
from lite.output import refusal, say

INIT_BRANCH = "darkfactory/init-repo"
COMMIT_MESSAGE = "DarkFactory: подготовка репо (dfl init-repo)"
COLLEAGUE_DOC = "docs/darkfactory-for-colleagues.md"
LABEL_COLOR = "b60205"
LABEL_DESCRIPTION = "PR мержит человек, не агент"
_SPEC_RUNNER = (
    "templates/darkfactory/target/spec-runner.config.yaml",
    "spec-runner.config.yaml",
)
GITIGNORE = ".gitignore"
_GITIGNORE_TEMPLATE = "templates/darkfactory/target/gitignore"
_GITIGNORE_MARK = "# DarkFactory (dfl init-repo)"
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
            params = rule.get("parameters") or {}
            count = params.get("required_approving_review_count", 0)
            owners = bool(params.get("require_code_owner_review"))
            if rule.get("type") == "pull_request" and (count > 0 or owners):
                return (
                    f"ruleset ветки {branch} содержит правило pull_request "
                    f"(обязательных ревью: {count}, ревью владельцев кода: "
                    f"{owners}); dfl чужую защиту не меняет — "
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
        reviews = body.get("required_pull_request_reviews") or {}
        count = reviews.get("required_approving_review_count", 0)
        owners = bool(reviews.get("require_code_owner_reviews"))
    except (ValueError, TypeError, AttributeError) as exc:
        return _unread("защита ветки: ответ не разобран", _fake(exc))
    if count > 0 or owners:
        return (
            f"защита ветки {branch} требует ревью (обязательных: {count}, "
            f"ревью владельцев кода: {owners}); "
            "dfl чужую защиту не меняет — снимите требование сами"
        )
    return None


def review_rules_refusal(slug: str, branch: str, gh: Gh) -> str | None:
    """Причина отказа, если ветка требует ревью (или правила не прочитаны)."""
    return _rules_refusal(slug, branch, gh) or _protection_refusal(slug, branch, gh)


def conflicts(repo: Path) -> list[str]:
    """Пути назначения, уже существующие в рабочем дереве `repo`.

    `.gitignore` дополняется, а не заменяется, — конфликт он лишь тогда,
    когда это не обычный файл (ссылка, каталог): дописывать туда нельзя.
    """
    found = [dst for _, dst in TEMPLATE_FILES if (repo / dst).exists()]
    ignore = repo / GITIGNORE
    if ignore.is_symlink() or (ignore.exists() and not ignore.is_file()):
        found.append(GITIGNORE)
    return found


def gitignore_update(repo: Path, devtools: Path) -> str | None:
    """Новый текст `.gitignore` репо или `None`, если менять нечего (#735).

    Нет файла — копия шаблона. Есть — в конец дописываются только строки
    шаблона, которых в нём нет (сравнение по строке без пробелов по краям);
    свои строки владельца не трогаются и не переупорядочиваются.
    """
    template = (devtools / _GITIGNORE_TEMPLATE).read_text(encoding="utf-8")
    path = repo / GITIGNORE
    if not path.exists():
        return template
    current = path.read_text(encoding="utf-8")
    have = {line.strip() for line in current.splitlines()}
    wanted = [
        line for line in template.splitlines() if line and not line.startswith("#")
    ]
    missing = [line for line in wanted if line not in have]
    if not missing:
        return None
    sep = "" if not current or current.endswith("\n") else "\n"
    return f"{current}{sep}\n{_GITIGNORE_MARK}\n" + "\n".join(missing) + "\n"


def _git(run: Runner, repo: Path, *args: str) -> str:
    done = run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip()
        raise InitError(f"git {' '.join(args)}: {detail} — исправьте и повторите")
    return (done.stdout or "").strip()


def _default_branch(run: Runner, repo: Path) -> str:
    """Ветка по умолчанию из `origin/HEAD`; нет ссылки — `set-head -a` и снова.

    `origin/HEAD` ставит только `git clone`; у репо из `git init` +
    `git remote add` его нет. `git remote set-head origin -a` спрашивает
    форджу и меняет лишь локальную ссылку — безопасно сделать самим.
    """
    branch = _origin_head(run, repo)
    if branch is not None:
        return branch
    _git(run, repo, "remote", "set-head", "origin", "-a")
    branch = _origin_head(run, repo)
    if branch is None:
        raise InitError(
            "ветка по умолчанию неизвестна (нет origin/HEAD даже после "
            "git remote set-head origin -a) — проверьте origin: git remote -v"
        )
    say("origin/HEAD не было — выполнено git remote set-head origin -a")
    return branch


def _origin_head(run: Runner, repo: Path) -> str | None:
    done = run(
        ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        return None
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


def _parse(done: subprocess.CompletedProcess, what: str) -> object:
    """JSON ответа `gh`; неразобранный ответ — отказ, а не traceback."""
    try:
        return json.loads(done.stdout or "[]")
    except ValueError as exc:
        raise InitError(
            f"ответ gh ({what}) не разобран: {exc} — повторите dfl init-repo"
        ) from exc


def _gh_login(gh: Gh) -> str:
    done = gh(["api", "user", "-q", ".login"])
    login = (done.stdout or "").strip()
    if done.returncode != 0 or not login:
        raise InitError(
            "не удалось узнать вашу учётку GitHub (gh api user) — "
            "проверьте gh auth status и повторите"
        )
    return login


def _is_mine(pr: dict, login: str) -> bool:
    author = pr.get("author") or {}
    return pr.get("isCrossRepository") is False and author.get("login") == login


def _open_pr_url(slug: str, gh: Gh) -> str | None:
    """URL своего открытого PR из `INIT_BRANCH`; чужой PR с этой веткой — отказ."""
    done = gh(
        ["pr", "list", "--repo", slug, "--head", INIT_BRANCH, "--state", "open",
         "--json", "url,isCrossRepository,author,headRepositoryOwner"]
    )  # fmt: skip
    if done.returncode != 0:
        raise InitError(_unread("список PR", done).replace("правила ветки", "PR"))
    prs = _parse(done, "pr list")
    if not isinstance(prs, list):
        raise InitError("ответ gh (pr list) не список — повторите dfl init-repo")
    if not prs:
        return None
    login = _gh_login(gh)
    foreign = [str(p.get("url")) for p in prs if not _is_mine(p, login)]
    if foreign:
        raise InitError(
            f"открытые PR с веткой {INIT_BRANCH} не ваши: {', '.join(foreign)} — "
            "это не PR подготовки (форк или чужой автор); проверьте их, "
            "закройте и повторите dfl init-repo"
        )
    return str(prs[0]["url"])


def _copy_files(repo: Path, devtools: Path) -> None:
    for src_rel, dst_rel in TEMPLATE_FILES:
        src, dst = devtools / src_rel, repo / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        dst.chmod(0o755 if src.stat().st_mode & 0o111 else 0o644)


def _doc_link(run: Runner, devtools: Path) -> str:
    """Ссылка на памятку коллеги по origin devtools; без origin — путь в репо."""
    done = run(
        ["git", "config", "--get", "remote.origin.url"],
        cwd=devtools, capture_output=True, text=True, check=False,
    )  # fmt: skip
    try:
        slug = user_targets.slug_from_url((done.stdout or "").strip())
    except user_targets.TargetsError:
        return f"{COLLEAGUE_DOC} в репо devtools"
    return f"https://github.com/{slug}/blob/master/{COLLEAGUE_DOC}"


def _pr_body(link: str, ignore: bool) -> str:
    lines = [f"- `{dst}`" for _, dst in TEMPLATE_FILES]
    if ignore:
        lines.append(f"- `{GITIGNORE}` (недостающие строки)")
    files = "\n".join(lines)
    return (
        "Подготовка репо к конвейеру DarkFactory (`dfl init-repo`).\n\n"
        f"Добавляемые файлы:\n{files}\n\n"
        "Этот PR мержите вы — агент его не мержит.\n"
        f"Дальше: {link}\n"
    )


def _label_exists(slug: str, gh: Gh) -> bool:
    done = gh(
        ["label", "list", "--repo", slug, "--search", LABEL, "--limit", "100",
         "--json", "name"]
    )  # fmt: skip
    if done.returncode != 0:
        raise InitError(_unread("метки", done).replace("правила ветки", "метки"))
    items = _parse(done, "label list")
    if not isinstance(items, list):
        raise InitError("ответ gh (label list) не список — повторите dfl init-repo")
    return LABEL in {i.get("name") for i in items if isinstance(i, dict)}


def _ensure_label(slug: str, gh: Gh) -> None:
    """Метка есть в репо (создать, если нет); существующая не трогается."""
    if _label_exists(slug, gh):
        return
    created = gh(
        ["label", "create", LABEL, "--repo", slug, "--color", LABEL_COLOR,
         "--description", LABEL_DESCRIPTION]
    )  # fmt: skip
    text = f"{created.stderr}\n{created.stdout}"
    if created.returncode != 0 and "already exists" not in text:
        raise InitError(f"метка {LABEL} не создана: {created.stderr.strip()}")


def _label_pr(slug: str, url: str, gh: Gh) -> None:
    """Метка в репо и на PR подготовки; идемпотентно, на каждом запуске."""
    _ensure_label(slug, gh)
    number = url.rstrip("/").rsplit("/", 1)[-1]
    if not number.isdigit():
        raise InitError(f"номер PR не разобран из {url} — повторите dfl init-repo")
    # REST, а не `gh pr edit`: тот в gh 2.70 падает на снятых Projects (classic).
    done = gh(
        ["api", f"repos/{slug}/issues/{number}/labels", "-f", f"labels[]={LABEL}"]
    )
    if done.returncode != 0:
        raise InitError(
            f"метка не поставлена на {url}: {done.stderr.strip()} — "
            "повторите dfl init-repo"
        )


def _open_pr(slug: str, base: str, gh: Gh, body: str) -> str:
    done = gh(
        ["pr", "create", "--repo", slug, "--base", base, "--head", INIT_BRANCH,
         "--title", COMMIT_MESSAGE, "--body", body]
    )  # fmt: skip
    if done.returncode != 0:
        raise InitError(
            f"PR не создан: {(done.stderr or '').strip()} — ветка {INIT_BRANCH} "
            "уже на origin; создайте PR вручную или удалите ветки "
            f"(git branch -D {INIT_BRANCH}; git push origin --delete {INIT_BRANCH})"
        )
    url = (done.stdout or "").strip()
    _label_pr(slug, url, gh)
    return url


def _publish(
    repo: Path, devtools: Path, slug: str, base: str, gh: Gh, run: Runner
) -> str:
    _git(run, repo, "switch", "-c", INIT_BRANCH)
    try:
        _copy_files(repo, devtools)
        paths = [dst for _, dst in TEMPLATE_FILES]
        ignore = gitignore_update(repo, devtools)
        if ignore is not None:
            (repo / GITIGNORE).write_text(ignore, encoding="utf-8")
            paths.append(GITIGNORE)
        _git(run, repo, "add", "--", *paths)
        _git(run, repo, "commit", "-m", COMMIT_MESSAGE)
        _git(run, repo, "push", "-u", "origin", INIT_BRANCH)
        body = _pr_body(_doc_link(run, devtools), ignore is not None)
        return _open_pr(slug, base, gh, body)
    finally:
        _git(run, repo, "switch", base)


def _leftover_check(run: Runner, repo: Path) -> None:
    if _git(run, repo, "branch", "--list", INIT_BRANCH):
        raise InitError(
            f"локальная ветка {INIT_BRANCH} осталась от прерванного запуска, а PR "
            f"нет — удалите: git branch -D {INIT_BRANCH} (и, если она уже на origin, "
            f"git push origin --delete {INIT_BRANCH}), затем повторите"
        )


def _plan(run: Runner, repo: Path, slug: str, base: str, gh: Gh) -> str | None:
    """URL уже открытого PR; иначе `None`. Отказ — `InitError`."""
    url = _open_pr_url(slug, gh)
    if url is not None:
        return url
    _leftover_check(run, repo)
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
        existing = _plan(run, repo, slug, base, gh)
        if existing is not None:
            _label_pr(slug, existing, gh)
            say(f"PR подготовки уже открыт: {existing} — смержите его сами")
            return 0
        url = _publish(repo, devtools, slug, base, gh, run)
    except InitError as exc:
        say(refusal("dfl init-repo", str(exc)), err=True)
        return 2
    say(f"PR подготовки открыт: {url} — смержите его сами, затем dfl run")
    return 0
