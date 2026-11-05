"""Предпроверка агентской доставки candidate `review/v1` (спека §8.2, §9.1).

Этот файл ИСПОЛНЯЕТ `merge-pr.sh` — файлом, в своём окружении:
`uv run --frozen --exact --group governance --no-config --no-env-file python -I
<этот файл> …` (форму распознаёт `tests/harness_trust.py`). Цепочка импортов —
только защищённые `governance/act_*`, `facts`, `ssot_env`; журнал периметра —
узкий клиент `act_journal_wire` (без `df/`, ревизия b1): уже зарегистрированный
блок останавливает мерж, обнаруженное здесь нарушение регистрируется. Коды:
0 — мерж разрешён (граница секунды выдержана), 3 — отказ, 2 — не установлено.
Предпроверка не подпись: окончательно решает `check_delivery` (Task 13).

`node_approval` и `stale_adapter` в цепочку не входят: проекция self-hash
(`act_projection`) здесь не импортируется. Если правка их втянет, инвариант
`tests/harness_trust.py` потребует внести их в authority-root и
`_HARNESS_PREFIXES` (п. 8 заметок части A).

The precheck program of df (protocol v2, `act_precheck_v2`; df stage 3a,
Task 7) runs the same check through `decide` with the merger of its request
(N4), the derived-credential mode (N3), the pinned P and the D16 surface; its
import closure must not reach `jwt`, so the App mode of the CLI loads
`act_app_installations` by name (`real_deps`).
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, "") and "governance" not in sys.modules:
    # Исполнение файлом под `python -I`: пакет — явно по пути, без дерева в
    # sys.path (тот же приём, что `brief_merge_check.py`).
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
import importlib  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
from collections.abc import Callable, Mapping, Sequence  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402
from typing import Any  # noqa: E402

from governance import act_facts, act_journal_wire, act_trust, ssot_env  # noqa: E402
from governance.act_anon import contents_bytes  # noqa: E402
from governance.act_check import ActivationView, live_policy_refusal  # noqa: E402
from governance.act_decision import find_act, merger_allowed  # noqa: E402
from governance.act_identity import RequestIdentity, manifest_refusal  # noqa: E402
from governance.act_policy import PolicySet  # noqa: E402
from governance.act_request import (  # noqa: E402
    NodeRequest,
    RequestError,
    parse_manifest,
)
from governance.act_time import Clock, Unavailable, parse_ts  # noqa: E402

EXIT_OK, EXIT_UNSET, EXIT_REFUSED = 0, 2, 3
#: Мерж — не раньше 2 с после последнего события решения (§8.2).
GAP = timedelta(seconds=2)
#: Дольше ждать нельзя: такая граница значит, что часы расходятся.
MAX_WAIT = 10.0
PATTERNS = (
    Path(__file__).resolve().parent.parent
    / "contracts/approval-branches/v1/patterns.env"
)
REGISTERED = " — событие записано в журнал периметра; мерж не выполнен"

Installations = Callable[[act_trust.Binding], "act_trust.Found"]
"""Самоотчёт установок App у держателя ключа (Task 15a); `None` — режим профиля."""
Surface = Callable[[RequestIdentity, NodeRequest], "str | None"]
"""The D16 surface of the whole PR (df precheck v2): a refusal reason or `None`."""
#: The module of the CLI's App mode, loaded by name (see the module docstring).
APP_INSTALLATIONS = "governance.act_app_installations"


class _Stop(Exception):
    def __init__(self, code: int, text: str) -> None:
        super().__init__(text)
        self.code = code


@dataclass(frozen=True)
class PremergeDeps:
    """Внешние чтения предпроверки; тесты подменяют целиком."""

    readers: act_trust.Readers
    gh: Any
    binding: Any  # Binding | None | Unavailable
    journal: Any  # act_trust.Journal | Unavailable — по привязке
    now: Callable[[], datetime]
    sleep: Callable[[float], None]
    creds: tuple[act_trust.Credential, ...] | act_trust.KeyHolderCovered
    installations: Installations | None = None
    #: The history token by the df launch contract (the approved `gh`, §3.10).
    token_source: Callable[[Path], str | Unavailable] | None = None
    #: The pinned head P of `approval-policy` (df precheck v2, spec B §6.1).
    policy_head: str | None = None
    #: The trust snapshot reader (df precheck v2: the pair by SHA).
    snapshot: act_trust.SnapshotReader | None = None
    surface: Surface | None = None


def request_of_branch(head_ref: str) -> tuple[str, str] | None:
    """`(ws_id, request_key)` по форме candidate из SSOT `patterns.env`;
    finalize и иные формы — `None`. Битый SSOT — `RuntimeError`."""
    template = ssot_env.read_key(
        PATTERNS, "APPROVAL_CANDIDATE_TEMPLATE", "patterns.env"
    )
    pattern = re.escape(template)  # полное совпадение: finalize (`…-final`) не подходит
    for name, group in (
        ("ws_id", r"(?P<ws_id>.+?)"),
        ("wave", r"(?P<wave>\d+)"),
        ("step", r"(?P<step>\d+)"),
        ("attempt", r"(?P<attempt>\d+)"),
    ):
        pattern = pattern.replace(re.escape("{" + name + "}"), group)
    match = re.fullmatch(pattern, head_ref)
    if match is None:
        return None
    key = f"approve-{match['wave']}-{match['step']}-{match['attempt']}"
    return match["ws_id"], key


def _manifest_text(
    deps: PremergeDeps, repo: str, head: str, path: str
) -> str | None | Unavailable:
    raw = contents_bytes(deps.gh, repo, path, head)
    if not isinstance(raw, bytes):
        return raw
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return Unavailable(f"{path}: не UTF-8")


Live = tuple[ActivationView, PolicySet, Sequence[ActivationView]]


def _live(deps: PremergeDeps, trust: act_trust.Trust, product: str) -> Live:
    """Текущая активация, её набор и диапазон открытой эпохи; режим продукта —
    по ПОЛНОЙ истории (B1-5)."""
    now = deps.now()
    anchors = act_trust.load_anchor_times(trust, now)  # транспорт — из `trust`
    loaded = act_trust.load_ranges(trust, anchors, now, deps.journal)
    if isinstance(loaded, Unavailable):
        raise _Stop(EXIT_UNSET, f"история источника политики: {loaded.reason}")
    ranges, sets = loaded
    mode = act_trust.product_mode(trust, ranges, sets, product, now)
    if isinstance(mode, Unavailable):
        raise _Stop(EXIT_UNSET, f"режим продукта: {mode.reason}")
    if mode != "review":
        raise _Stop(EXIT_REFUSED, f"ACT_MODE[{product}] = {mode} — не review")
    current = act_trust.current_activation(trust, ranges)
    if isinstance(current, Unavailable):
        raise _Stop(EXIT_UNSET, f"текущая активация: {current.reason}")
    policy = sets.get(current.merge_commit, Unavailable("набор не прочитан"))
    if isinstance(policy, Unavailable):
        raise _Stop(EXIT_UNSET, f"набор текущей активации: {policy.reason}")
    # `current_activation` уже прочитал этот диапазон — он есть и он кортеж.
    span = ranges[trust.epochs[-1].epoch]
    assert not isinstance(span, Unavailable)
    return current, policy, span


def _boundary(facts: Any, span: Sequence[ActivationView]) -> datetime:
    """Последнее событие, способное повлиять на решение (§8.2)."""
    try:
        stamps = [a.at for a in span]
        stamps += [parse_ts(r.submitted_at) for r in facts.reviews if r.submitted_at]
        stamps += [parse_ts(d.created_at) for d in facts.dismissals]
    except ValueError as exc:
        raise _Stop(EXIT_UNSET, f"метка события не разобрана: {exc}") from exc
    return max(stamps)


def _trust(deps: PremergeDeps) -> act_trust.Trust:
    """Привязка, журнал, проверки периметра; App — самоотчёт установок (B1-11)."""
    if deps.binding is None:
        raise _Stop(
            EXIT_REFUSED, "заявка review/v1 без корня доверия (нет /etc/df/trust.toml)"
        )
    if isinstance(deps.binding, Unavailable):
        raise _Stop(EXIT_UNSET, deps.binding.reason)
    if isinstance(deps.journal, Unavailable):
        raise _Stop(EXIT_UNSET, f"журнал периметра: {deps.journal.reason}")
    trust = act_trust.load_trust(
        deps.readers,
        deps.binding,
        deps.journal,
        creds=deps.creds,
        token_source=deps.token_source,
        snapshot=deps.snapshot,
        policy_head=deps.policy_head,
    )
    if isinstance(trust, Unavailable):
        raise _Stop(EXIT_UNSET, trust.reason)
    if deps.installations is not None:
        found = deps.installations(deps.binding)
        if isinstance(found, Unavailable):
            raise _Stop(EXIT_UNSET, f"установки App: {found.reason}")
        if found is not None:
            failed = deps.journal.record(trust.snapshot, found[0], found[1])
            tail = REGISTERED if failed is None else f"; регистрация: {failed.reason}"
            raise _Stop(EXIT_UNSET, f"нарушение периметра: {found[1]}{tail}")
    for name in trust.unverified:
        print(f"unverified premise: {name}", file=sys.stderr)
    return trust


def _check(args: argparse.Namespace, deps: PremergeDeps) -> None:
    try:
        branch = request_of_branch(args.head_ref)
    except RuntimeError as exc:
        raise _Stop(EXIT_UNSET, str(exc)) from exc
    if branch is None:
        raise _Stop(EXIT_REFUSED, f"ветка {args.head_ref!r} — не форма candidate")
    ident = RequestIdentity(args.repo, *branch)
    trust = _trust(deps)
    current, policy, span = _live(deps, trust, args.repo)
    text = _manifest_text(deps, args.repo, args.head, ident.manifest_path)
    if isinstance(text, Unavailable):
        raise _Stop(EXIT_UNSET, f"манифест: {text.reason}")
    if text is None:
        raise _Stop(
            EXIT_REFUSED, f"в {args.head[:12]} нет манифеста {ident.request_id}"
        )
    try:
        req = parse_manifest(text)
    except RequestError as exc:
        raise _Stop(EXIT_REFUSED, f"манифест: {exc}") from exc
    refusal = manifest_refusal(req, ident) or live_policy_refusal(
        req.policy, current.merge_commit
    )
    if refusal is not None:
        raise _Stop(EXIT_REFUSED, refusal)
    off_surface = None if deps.surface is None else deps.surface(ident, req)
    if off_surface is not None:
        raise _Stop(EXIT_REFUSED, f"surface_violation: {off_surface}")
    if not merger_allowed(policy, args.merger, args.merger_type):
        raise _Stop(EXIT_REFUSED, f"мержер {args.merger} не пройдёт D3")
    facts = _act(args, deps, policy)
    wait = (_boundary(facts, span) + GAP - deps.now()).total_seconds()
    if wait > MAX_WAIT:
        raise _Stop(EXIT_UNSET, f"граница секунды через {wait:.0f} с — часы расходятся")
    if wait > 0:
        deps.sleep(wait)
    again = act_facts.open_review(deps.gh, args.repo, args.pr)
    current2, _policy2, _span2 = _live(deps, trust, args.repo)
    if again != facts or current2 != current:
        raise _Stop(
            EXIT_UNSET, "ревью или текущая активация изменились за время ожидания"
        )


def _act(args: argparse.Namespace, deps: PremergeDeps, policy: PolicySet) -> Any:
    """D1 на `--head`: факты ревью названы на этой голове и акт на ней есть."""
    facts = act_facts.open_review(deps.gh, args.repo, args.pr)
    if isinstance(facts, Unavailable):
        raise _Stop(EXIT_UNSET, f"ревью PR #{args.pr}: {facts.reason}")
    if facts.head_sha != args.head:
        raise _Stop(
            EXIT_REFUSED, f"голова PR {facts.head_sha[:12]} ≠ --head {args.head[:12]}"
        )
    now = deps.now()
    effective = act_facts.effective(facts, Clock(now))
    if isinstance(effective, Unavailable):
        raise _Stop(EXIT_UNSET, effective.reason)
    if find_act(args.head, effective, policy) is None:
        if _approve_in_current_second(facts, args.head, now):
            raise _Stop(
                EXIT_UNSET,
                f"approve на {args.head[:12]} — в текущей секунде: на срезе T он "
                "ещё не предшествует T (§8.2); повтор уместен",
            )
        raise _Stop(EXIT_REFUSED, f"нет approve человека на {args.head[:12]}")
    return facts


def _approve_in_current_second(facts: Any, head: str, now: datetime) -> bool:
    """APPROVED на `head` с `submitted_at` (до секунды) ≥ T: срез T его не видит
    (равная секунда предшествует T только по `before`), но через секунду увидит —
    это «не установлено», а не отказ (ревью PR #673)."""
    for r in facts.reviews:
        if r.state != "APPROVED" or r.commit_id != head or not r.submitted_at:
            continue
        try:
            if parse_ts(r.submitted_at) >= now:
                return True
        except ValueError:
            return True  # метку не разобрать — тоже не установлено
    return False


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="act_premerge")
    for name in ("--repo", "--head", "--head-ref", "--merge-profile", "--merger"):
        p.add_argument(name, required=True)
    p.add_argument("--pr", required=True, type=int)
    p.add_argument("--merger-type", required=True, choices=("User", "Bot"))
    p.add_argument("--app-id", type=int, default=None)
    p.add_argument("--app-key", type=Path, default=None)
    return p


def real_deps(
    args: argparse.Namespace, env: Mapping[str, str] | None = None
) -> PremergeDeps:
    """Боевые чтения: GraphQL под профилем агента, привязка, журнал периметра,
    самоотчёт `gh`; транспорт источников доверия строит `load_trust` (Task 11c)."""
    env = dict(os.environ) if env is None else env
    home = Path.home()
    agent = Path(env.get("REVIEW_GH_CONFIG_DIR") or home / ".config" / "review")
    binding = act_trust.load_binding()
    journal: Any = Unavailable("нет привязки")
    if isinstance(binding, act_trust.Binding):
        journal = act_journal_wire.journal_for_premerge(
            env=env,
            state_home=Path(env.get("XDG_STATE_HOME") or home / ".local" / "state"),
            run_id=f"merge-pr:{args.repo}#{args.pr}",
            policy_repository_id=binding.approval_policy_repository_id,
        )
    return PremergeDeps(
        readers=act_trust.default_readers(),
        gh=act_facts.gh_transport(agent),
        binding=binding,
        journal=journal,
        now=lambda: datetime.now(UTC).replace(microsecond=0),
        sleep=time.sleep,
        creds=act_trust.credentials(
            env, agent_dir=agent, merge_dir=Path(args.merge_profile)
        ),
        installations=(
            None
            if args.app_id is None
            else importlib.import_module(APP_INSTALLATIONS).app_installations(
                args.app_id, args.app_key
            )
        ),
    )


def main(argv: Sequence[str] | None = None, deps: PremergeDeps | None = None) -> int:
    """CLI предпроверки; см. докстринг модуля."""
    parser = _parser()
    try:
        args = parser.parse_args(argv)
        if (args.app_id is None) != (args.app_key is None):
            parser.error("App-режим: нужны оба --app-id и --app-key")
    except SystemExit:
        # argparse (и `--help`): предпроверки не было — отказ 3, «не мержить и не
        # повторять»; 2 `merge-pr.sh` читает как «повтор уместен», а расхождение
        # версий обвязки повтор не лечит (интеграционное ревью части B, m-7).
        return EXIT_REFUSED
    if deps is None:
        deps = real_deps(args)
    code, reason = decide(args, deps)
    if code != EXIT_OK:
        print(f"act_premerge: {reason}", file=sys.stderr)
    return code


def decide(args: argparse.Namespace, deps: PremergeDeps) -> tuple[int, str]:
    """The check without printing: `(EXIT_OK, "")` or the exit code and reason
    (`main`; the df precheck v2 maps 3 to `refuse`, 2 to `unset`)."""
    try:
        _check(args, deps)
    except _Stop as stop:
        return stop.code, str(stop)
    return EXIT_OK, ""


if __name__ == "__main__":
    sys.exit(main())
