"""Проверка brief-PR перед человеческим мержем (спека need-stage §11.3 п.6).

Этот файл ИСПОЛНЯЕТ `human-merge.sh` — с полномочиями человека, из дерева
devtools. Поэтому он и вся цепочка его локальных импортов (`brief_facts`,
`facts`, `approval_request`, `discovery_approval` с вендоренной копией,
`policy_rule`, `ssot_env`, `governance/__init__`) плюс `pyproject.toml` и
`uv.lock` — под authority-root и харнесс-гвардом (ревью #573): агентский PR,
ослабивший проверку, сам через неё не прошёл бы. От `ops.py` и раннера модуль
не зависит намеренно — чем уже цепочка, тем меньше защищённых путей.

Запуск — только так (форму распознаёт тест инварианта, иная — отказ):
`uv run --frozen --exact --no-config --no-env-file python -I <этот файл> …`.
`-I` отсекает `PYTHONPATH`, пользовательский site и текущий каталог. Ни один
каталог дерева в `sys.path` НЕ попадает (ревью #573, круг 2: корень впереди
stdlib дал бы подмену `json.py`/`argparse.py`, подброшенной в корень репо):
пакет `governance` загружается явно по пути, его подмодули — только из
каталога пакета. Инвариант это проверяет пробой боевого процесса.
"""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

if __package__ in (None, "") and "governance" not in sys.modules:
    # Исполнение файлом под `python -I`: пакет — явно по пути, без дерева в
    # sys.path (подмодули `governance.*` ищутся только в каталоге пакета).
    import importlib.util

    _pkg_dir = Path(__file__).resolve().parent
    _spec = importlib.util.spec_from_file_location(
        "governance",
        _pkg_dir / "__init__.py",
        submodule_search_locations=[str(_pkg_dir)],
    )
    assert _spec is not None and _spec.loader is not None
    _pkg = importlib.util.module_from_spec(_spec)
    sys.modules["governance"] = _pkg
    _spec.loader.exec_module(_pkg)

import argparse  # noqa: E402

from governance import approval_request, discovery_approval, policy_rule  # noqa: E402
from governance.brief_facts import BriefFactsMixin  # noqa: E402
from governance.facts import Fact, Outcome  # noqa: E402

#: Коды выхода CLI (см. `main`); их же разбирает `human-merge.sh`.
EXIT_RETRY = 10
EXIT_REFUSED = 11
EXIT_ERROR = 12


class CheckError(Exception):
    """Проверка не дала результата; `retry` — факт не установлен, повторите."""

    def __init__(self, message: str, *, retry: bool = False) -> None:
        super().__init__(message)
        self.retry = retry


class MergeRefused(CheckError):
    """Заявка brief-PR не прошла проверку (код 3)."""


class GhFacts(BriefFactsMixin):
    """Факты форджа через `gh` — без `ops.py`."""


def proposal_dir(files: tuple[tuple[str, str], ...]) -> str | None:
    """Каталог `…/00-discovery`, если изменения — ровно бриф и заявка (`added`)."""
    paths = [PurePosixPath(p) for p, _ in files]
    dirs = {str(p.parent) for p in paths}
    if (
        len(files) != 2
        or {s for _, s in files} != {"added"}
        or len(dirs) != 1
        or {p.name for p in paths}
        != {approval_request.BRIEF, approval_request.FILE_NAME}
        or PurePosixPath(next(iter(dirs))).name != "00-discovery"
    ):
        return None
    return next(iter(dirs))


def _fact(fact: Fact, what: str) -> Fact:
    if fact.outcome is Outcome.UNAVAILABLE:
        raise CheckError(f"{what}: {fact.detail} — повторите", retry=True)
    return fact


def check_merge(repo: str, pr: int, head: str, facts) -> str:
    """§11.3 п.6: brief-PR по ПРОВЕРЕННОЙ голове `head`; вернуть пин политики.

    Заявка и бриф читаются форджем по `head`; мерж вызывающий пинует тем же
    SHA. Тело и метки PR в решении не участвуют.
    """
    pr_facts = _fact(facts.brief_pr_fact(repo, pr), f"PR #{pr}").value
    if pr_facts.state != "OPEN":
        raise MergeRefused(f"brief-PR #{pr} не открыт ({pr_facts.state})")
    if pr_facts.head_sha != head:
        raise CheckError(
            f"голова PR #{pr} — {pr_facts.head_sha}, проверялась {head}: перепроверьте",
            retry=True,
        )
    if not pr_facts.head_ref.startswith("brief/"):
        raise MergeRefused(f"PR #{pr} — не brief-PR ({pr_facts.head_ref})")
    default = _fact(facts.default_branch_fact(repo), "ветка по умолчанию").value
    if pr_facts.base_ref != default.name:
        raise MergeRefused(f"база PR #{pr} — {pr_facts.base_ref}, не {default.name}")
    dir_ = proposal_dir(pr_facts.files)
    if dir_ is None:
        raise MergeRefused(
            f"изменения PR {list(pr_facts.files)} ≠ бриф + заявка в одном …/00-discovery"
        )
    texts = {}
    for name in (approval_request.FILE_NAME, approval_request.BRIEF):
        fact = _fact(facts.repo_file_fact(repo, head, f"{dir_}/{name}"), name)
        if fact.outcome is not Outcome.FOUND:
            raise MergeRefused(f"в голове PR нет {dir_}/{name}")
        texts[name] = fact.value
    try:
        req = approval_request.parse(texts[approval_request.FILE_NAME])
    except approval_request.RequestError as exc:
        raise MergeRefused(f"заявка: {exc}") from exc
    source = policy_rule.policy_source()
    if (req.policy_repo, req.policy_ref, req.policy_path) != source:
        raise MergeRefused("координаты политики заявки ≠ SSOT")
    try:
        own = discovery_approval.self_hash(texts[approval_request.BRIEF])
    except discovery_approval.NotABrief as exc:
        raise MergeRefused(f"бриф PR: {exc}") from exc
    if own != req.brief_self_hash:
        raise MergeRefused("brief_self_hash заявки ≠ брифу той же головы")
    repo_p, ref_p, path_p = source
    current = _fact(facts.policy_version_fact(repo_p, ref_p, path_p), "политика")
    if current.outcome is not Outcome.FOUND:
        raise MergeRefused(
            f"актуальная версия политики не установлена: {current.detail}"
        )
    if req.policy_sha != current.value:
        raise MergeRefused(
            f"политика сменилась после предложения (пин {req.policy_sha}, "
            f"актуальная {current.value}) — мерж не создал бы годного акта; --repropose"
        )
    return req.policy_sha


def main(argv: list[str] | None = None) -> int:
    """CLI: stdout — ТОЛЬКО проверенный пин (его забирает human-merge.sh).

    Коды — свои, вне пространства кодов `uv` и интерпретатора (0/1/2, 127):
    0 — годно; 10 — факт не установлен (повторите); 11 — отказ заявки;
    12 — прочая ошибка. Иначе отказ самого `uv run` (код 2) был бы неотличим
    от «повторите» (ревью #573, круг 4).
    """
    parser = argparse.ArgumentParser(prog="brief_merge_check")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    try:
        pin = check_merge(args.repo, args.pr, args.head, GhFacts())
    except MergeRefused as exc:
        print(f"brief-merge-check: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except CheckError as exc:
        print(f"brief-merge-check: {exc}", file=sys.stderr)
        return EXIT_RETRY if exc.retry else EXIT_ERROR
    print(f"brief-PR #{args.pr}: заявка годна", file=sys.stderr)
    print(pin)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
