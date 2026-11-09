"""Барьер миграции акта по продукту (спека §10 «Единый барьер миграции», Q9).

Полнота выводится из форджи, локальные леджеры — дополнение (другую
установку локально не видно). Пустой отчёт без draining и без аттестации
других установок — не полнота (`gaps`). Вызывается из `darkfactory setup-repo
apply` (спека B) в человеческом контексте дважды: перед предложением
активации и непосредственно перед её мержем. Точки вызова — на стороне спеки B
(handoff), в этом репо их нет.

Записи форджи читаются в той форме, которую отдаёт `RealOps` (контракт формы —
`tests/test_act_barrier.py`); неожиданная форма или исключение чтения — пробел
полноты, а не пропуск и не «есть живые».
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from governance import act_status, df_config
from governance import approval_branches as ab
from governance import approval_ledger as al
from governance import run_state as rs
from governance.act_identity import RequestIdentity, manifest_refusal
from governance.act_request import ACT_REVIEW_V1, RequestError, parse_manifest
from governance.act_time import parse_ts
from governance.facts import Outcome
from governance.frontmatter import split_frontmatter

PENDING = "approval_pending"
#: Выходы CLI: пусто и полно; есть живые; инвалидация отвергнута; неполно.
EXIT_CLEAN, EXIT_LIVE, EXIT_REFUSED, EXIT_INCOMPLETE = 0, 1, 2, 3


class Refused(RuntimeError):
    """Действие человеческого контекста отвергнуто (CLI: код 2)."""


@dataclass(frozen=True)
class PrRecord:
    """Запись списка PR форджи: номер, ветка головы, состояние."""

    number: int
    branch: str
    state: str


_PR_STATES = frozenset({"OPEN", "CLOSED", "MERGED"})


@dataclass(frozen=True)
class BarrierReport:
    """Живые заявки без маркера; `complete` — отчёт полон (нет `gaps`)."""

    items: tuple[str, ...]
    complete: bool
    gaps: tuple[str, ...]


@dataclass(frozen=True)
class Unmarked:
    """Не маркер — с причиной ЭТОЙ проверки (ревизия b4: у каждой проверки своя
    причина, двойник проверяет ровно её)."""

    reason: str


NOT_REQUEST_FORM = "ветка не формы заявки"
NO_MANIFEST_FILE = "манифеста нет в голове"
MANIFEST_UNPARSED = "манифест не разобран"
MANIFEST_FOREIGN = "манифест чужой заявки"
NO_MARKER = "маркера миграции нет"
MARKER_AUTHOR = "маркер не от human_approvers"
MARKER_EDITED = "маркер правлен"
MARKER_FOREIGN = "маркер не этой заявки"
SIGNED_STATUS = "статус узла в базе не approved"
SIGNED_OTHER = "подпись другого акта"
NODE_MISSING = "узла нет в базе"
NO_STAMP_RUN = "нет прогона с interview.approval этого PR"
NO_STAMP_FILE = "нет файла штампа брифа"


def _kind(branch: str) -> str | None:
    if ab.is_finalize(branch):
        return "finalize"
    if ab.is_candidate(branch):
        return "candidate"
    return "brief" if branch.startswith("brief/") else None


def _identity(product: str, branch: str) -> RequestIdentity | None:
    """Полная идентичность заявки ветки candidate/finalize (B1-14); форма — по
    SSOT круговой подстановкой (`act_status.candidate_identity`)."""
    stem = (
        branch.removesuffix(ab.finalize_suffix()) if ab.is_finalize(branch) else branch
    )
    parsed = act_status.candidate_identity(stem)
    if parsed is None:
        return None
    return RequestIdentity(product, *parsed)


def _head(product: str, ops: Any, number: int) -> str | None:
    head = ops.pr_facts(product, number).get("headRefOid")
    return head if isinstance(head, str) else None


def _marked(product: str, ops: Any, number: int, branch: str) -> bool | str | Unmarked:
    """Действительный манифест `review/v1` ЭТОЙ заявки в голове PR (B1-13): разобран
    строго и называет её идентичность — `True`; иначе `Unmarked(причина)`. Строка —
    факт не установлен."""
    ident = _identity(product, branch)
    if ident is None:
        return Unmarked(NOT_REQUEST_FORM)
    head = _head(product, ops, number)
    if head is None:
        return f"#{number}: голова не прочитана"
    fact = ops.repo_file_fact(product, head, ident.manifest_path)
    if fact.outcome is Outcome.UNAVAILABLE:
        return f"#{number}: манифест не прочитан — {fact.detail}"
    if fact.outcome is not Outcome.FOUND or fact.value is None:
        return Unmarked(NO_MANIFEST_FILE)
    try:
        req = parse_manifest(fact.value)
    except RequestError as exc:
        return Unmarked(f"{MANIFEST_UNPARSED}: {exc}")
    refusal = manifest_refusal(req, ident)
    if refusal is not None:
        return Unmarked(f"{MANIFEST_FOREIGN}: {refusal}")
    return True


def _migrated(
    product: str, ops: Any, number: int, ident: RequestIdentity, humans: frozenset[str]
) -> bool | str | Unmarked:
    """Маркер `act_migrated` именно этой заявки — дословно, от человека, без правок;
    отвергнутый маркер — `Unmarked` с причиной последней отвергнувшей проверки."""
    fact = ops.pr_comments_fact(product, number)
    if fact.outcome is not Outcome.FOUND or fact.value is None:
        return f"#{number}: комментарии не прочитаны — {fact.detail}"
    why = NO_MARKER
    for c in fact.value:
        if "act-migrated" not in c.body:
            continue
        if c.author not in humans:
            why = MARKER_AUTHOR
        elif c.last_edited_at is not None:
            why = MARKER_EDITED
        elif c.body.strip() != ident.migration_marker:
            why = MARKER_FOREIGN
        else:
            return True
    return Unmarked(why)


def _signed(text: str, by: str, at: str) -> str | None:
    """Подпись этого (или более позднего) акта в базе — `None`; иначе причина."""
    try:
        meta, _ = split_frontmatter(text)
    except ValueError:
        return SIGNED_STATUS  # frontmatter не разобран — узел не одобрен
    if meta.get("status") != "approved":
        return SIGNED_STATUS
    if meta.get("approved_by") == by and meta.get("approved_at") == at:
        return None
    later = meta.get("approved_at")
    try:
        covered = isinstance(later, str) and parse_ts(later) > parse_ts(at)
    except ValueError:
        covered = False  # метка не разобрана — покрытие не доказано
    return None if covered else SIGNED_OTHER


def _pending(text: str) -> bool:
    """Узел в голове candidate отдан на одобрение (`status: approval_pending`)."""
    try:
        meta, _ = split_frontmatter(text)
    except ValueError:
        return False  # не узел бандла
    return meta.get("status") == PENDING


def _candidate_nodes(product: str, ops: Any, number: int) -> list[str] | str:
    """Узлы, которые candidate отдал на одобрение: его файлы со `status:
    approval_pending` в его голове — независимо от того, что выбрал finalize
    (B1-13). Строка — факт не установлен."""
    files = ops.brief_pr_fact(product, number)
    head = _head(product, ops, number)
    if files.outcome is not Outcome.FOUND or files.value is None or head is None:
        return f"#{number}: состав или голова candidate не прочитаны"
    nodes: list[str] = []
    for path, _change in files.value.files:
        text = ops.repo_file_fact(product, head, path)
        if text.outcome is Outcome.UNAVAILABLE:
            return f"#{number}: {path} в голове candidate не прочитан"
        found = text.outcome is Outcome.FOUND and text.value and path.endswith(".md")
        if found and _pending(text.value):
            nodes.append(path)
    return nodes


def _legacy_resolved(
    product: str, ops: Any, number: int, branch: str, merged: Collection[str]
) -> str | tuple[str] | None:
    """MERGED legacy-candidate разобран (`None`), только если смёржен его finalize и
    каждый отданный им узел подписан в базе этим или более поздним актом."""
    if branch + ab.finalize_suffix() not in merged:
        return f"#{number} candidate MERGED без смёрженного finalize — pending-finalize"
    nodes = _candidate_nodes(product, ops, number)
    if isinstance(nodes, str):
        return (nodes,)
    if not nodes:
        return (
            f"#{number} candidate MERGED: узлов на одобрении не найдено "
            "— разберите вручную"
        )
    facts = ops.pr_facts(product, number)
    by = (facts.get("mergedBy") or {}).get("login")
    at = facts.get("mergedAt")
    if not isinstance(by, str) or not isinstance(at, str):
        return (f"#{number}: кто и когда смёржил candidate — не прочитано",)
    tip = ops.default_branch_fact(product)
    if tip.outcome is not Outcome.FOUND or tip.value is None:
        return (f"#{number}: голова базы не прочитана",)
    for path in nodes:
        text = ops.repo_file_fact(product, tip.value.sha, path)
        if text.outcome is Outcome.UNAVAILABLE:
            return (f"#{number}: {path} в базе не прочитан",)
        why = (
            NODE_MISSING
            if text.outcome is not Outcome.FOUND or text.value is None
            else _signed(text.value, by, at)
        )
        if why is not None:
            return f"#{number} candidate MERGED: {path} — {why} — merged-but-unrecorded"
    return None


def _record(raw: object) -> PrRecord | None:
    """Запись `RealOps.prs_by_head_prefix` (`{"number", "head": {"ref"}, "state"}`);
    иная форма — `None` (вызывающий объявляет список неполным)."""
    if not isinstance(raw, dict):
        return None
    head = raw.get("head")
    branch = head.get("ref") if isinstance(head, dict) else None
    number, state = raw.get("number"), raw.get("state")
    if (
        type(number) is not int
        or not isinstance(branch, str)
        or state not in _PR_STATES
    ):
        return None
    return PrRecord(number, branch, str(state))


def _listing(product: str, ops: Any) -> tuple[list[PrRecord], str | None]:
    """Все PR продукта; сбой списка или запись неожиданной формы — причина
    неполноты (записи правильной формы всё равно судятся)."""
    try:
        records = [_record(r) for r in ops.prs_by_head_prefix(product, "")]
    except Exception as exc:  # noqa: BLE001 — любой сбой чтения списка — пробел (N3)
        return [], str(exc) or repr(exc)
    found = [r for r in records if r is not None]
    if len(found) != len(records):
        return found, f"{len(records) - len(found)} записей неожиданной формы"
    return found, None


def _judge(
    product: str,
    ops: Any,
    pr: PrRecord,
    merged: Collection[str],
    humans: frozenset[str],
    confirmed: frozenset[int],
    live_local: Mapping[int, str],
) -> str | tuple[str] | None:
    """Пункт отчёта, `None` — разобран, `(gap,)` — факт не установлен.

    CLOSED разобран, только если живой локальной заявки этого candidate нет
    (`live_local`: номер candidate-PR → заявка): закрытие кнопкой форджи запись
    не терминализует — это делает следующий вызов агента (ревью PR #675)."""
    branch, state, number = pr.branch, pr.state, pr.number
    kind = _kind(branch)
    if kind is None:
        return None
    if state == "CLOSED":
        label = live_local.get(number) if kind == "candidate" else None
        if label is None:
            return None
        return (
            f"#{number} candidate CLOSED без мержа — живая заявка {label} без маркера"
        )
    ident = _identity(product, branch)
    unmigrated = NO_MARKER
    if ident is not None:
        migrated = _migrated(product, ops, number, ident, humans)
        if isinstance(migrated, str):
            return (migrated,)
        if migrated is True:
            return None
        if isinstance(migrated, Unmarked):
            unmigrated = migrated.reason
    if kind == "brief":
        if state == "OPEN":
            return f"#{number} brief-PR OPEN — заявка v1 без маркера"
        stamp = None if number in confirmed else _local_brief_stamp(product, number)
        if stamp is None:
            return None
        return (
            f"#{number} brief-PR MERGED — штамп не доказан локально ({stamp}); "
            f"требует подтверждения человека (--confirm-resolved {number})"
        )
    marked = _marked(product, ops, number, branch)
    if isinstance(marked, str):
        return (marked,)
    if marked is True:
        return None
    reason = marked.reason if isinstance(marked, Unmarked) else NOT_REQUEST_FORM
    if state == "OPEN":
        return f"#{number} {kind} OPEN без маркера: {reason}; {unmigrated}"
    if kind == "finalize":
        return None  # судится вместе со своим candidate
    return _legacy_resolved(product, ops, number, branch, merged)


def _local_brief_stamp(product: str, number: int) -> str | None:
    """Локальное доказательство штампа брифа — `None`; иначе причина. Доказывает
    только прогон этого продукта и этого PR с файлом штампа."""
    why = NO_STAMP_RUN
    for run_id in rs.all_run_ids():
        try:
            state = rs.load(run_id)
        except (OSError, ValueError, TypeError):
            continue  # нечитаемый прогон ничего не доказывает
        approval = (state.interview or {}).get("approval") or {}
        if approval.get("repo") == product and approval.get("pr") == number:
            stamped = rs.run_dir(run_id) / "brief-approval" / "customer-brief.md"
            if stamped.is_file():
                return None
            why = NO_STAMP_FILE
    return why


def _local_items(
    product: str, runs: Iterable[str]
) -> tuple[list[tuple[str, str, int | None]], list[str]]:
    """Живые заявки локальных леджеров без маркера — `(пункт, заявка, candidate-PR)`,
    ЛЮБЫЕ, с PR и без (ревью PR #675: закрытый candidate живой заявки форджа-проход
    разобранным не считает, но и PR вне списка не должен прятать заявку);
    нечитаемый леджер — пробел полноты. Дубли с форджа-проходом снимает
    `live_unmarked`."""
    items: list[tuple[str, str, int | None]] = []
    gaps: list[str] = []
    for run_id in runs:
        try:
            state = rs.load(run_id)
        except (OSError, ValueError, TypeError) as exc:
            gaps.append(f"леджер {run_id} не читается: {exc}")
            continue
        if state.repo_slug != product:
            continue
        for nums, op in al.live_requests(state):
            if op.get("act") == ACT_REVIEW_V1:
                continue
            pr = op.get("candidate_pr")
            where = "до PR" if pr is None else f"(candidate #{pr})"
            label = f"{run_id}:{al.request_key(*nums)}"
            items.append((f"{label} — заявка без маркера {where}", label, pr))
    return items, gaps


def live_unmarked(
    product: str,
    ops: Any,
    *,
    humans: frozenset[str],
    confirmed: frozenset[int],
    attested: Sequence[str],
    draining: bool,
    runs: Iterable[str],
) -> BarrierReport:
    """Живые заявки без маркера продукта по форджу и локальным леджерам (§10 п. 2)."""
    items: list[str] = []
    gaps: list[str] = []
    if not draining:
        gaps.append("draining не включён (act.toml act_migration)")
    if not attested:
        gaps.append("draining других установок не подтверждён (--attest-drained)")
    prs, broken = _listing(product, ops)
    if broken is not None:
        gaps.append(f"список PR {product} неполон: {broken}")
    merged = {p.branch for p in prs if p.state == "MERGED"}
    local, local_gaps = _local_items(product, runs)
    live_local = {pr: label for _item, label, pr in local if pr is not None}
    reported: set[int] = set()
    for pr in prs:
        try:
            verdict = _judge(product, ops, pr, merged, humans, confirmed, live_local)
        except Exception as exc:  # noqa: BLE001 — любой сбой чтения форджи — пробел
            verdict = (f"#{pr.number}: факты PR не установлены — {exc!r}",)
        if isinstance(verdict, tuple):
            gaps.append(verdict[0])
        elif verdict is not None:
            items.append(verdict)
            reported.add(pr.number)
    items += [item for item, _label, pr in local if pr is None or pr not in reported]
    return BarrierReport(
        tuple(items), not (gaps or local_gaps), tuple(gaps + local_gaps)
    )


def live_review_requests(ops_by_product: Mapping[str, Any]) -> BarrierReport:
    """Живые заявки `review/v1` продуктов источника (предпросмотр Q9, спека B):
    OPEN candidate с манифестом; пустой список при `gaps` — не «последствий нет»."""
    items: list[str] = []
    gaps: list[str] = []
    for product, ops in ops_by_product.items():
        prs, broken = _listing(product, ops)
        if broken is not None:
            gaps.append(f"{product}: список PR неполон — {broken}")
        for pr in prs:
            if pr.state != "OPEN" or _kind(pr.branch) != "candidate":
                continue
            try:
                marked = _marked(product, ops, pr.number, pr.branch)
            except Exception as exc:  # noqa: BLE001 — сбой чтения форджи — пробел
                marked = f"#{pr.number}: факты PR не установлены — {exc!r}"
            if isinstance(marked, str):
                gaps.append(f"{product}{marked}")  # голова/манифест PR не прочитаны
            elif marked is True:
                items.append(f"{product}#{pr.number} — живая заявка review/v1")
    return BarrierReport(tuple(items), not gaps, tuple(gaps))


#: Отказ `act_barrier` в режиме `test` (миграции акта там нет).
TEST_MODE_REFUSAL = "режим test: миграция акта не применяется"


def _config_refusal() -> str | None:
    """Причина отказа по режиму конфига DarkFactory или `None` (strict).

    Нет файла конфига — strict; битый конфиг и режим `test` — отказ."""
    if not os.path.lexists(df_config.config_path(os.environ)):
        return None
    try:
        cfg = df_config.load()
    except df_config.ConfigError as exc:
        return f"конфиг DarkFactory: {exc}"
    return TEST_MODE_REFUSAL if cfg.mode == "test" else None


def invalidate(
    product: str, ops: Any, pr: int, *, humans: frozenset[str], caller: str
) -> str:
    """`act_migrated` по форджу: маркер от человека из `human_approvers` (§10 п. 2);
    локальный леджер — только в прогонах того же `(repo_slug, ws_id)` (B1-14)."""
    if caller not in humans:
        raise Refused(
            f"{caller} не в human_approvers текущей активации — маркер не ставится"
        )
    prs, broken = _listing(product, ops)
    branch = next((p.branch for p in prs if p.number == pr), None)
    if branch is None and broken is not None:
        raise RuntimeError(f"список PR {product} неполон: {broken}")
    ident = _identity(product, branch or "")
    if ident is None:
        raise Refused(f"PR #{pr} не candidate/finalize заявки узла")
    ops.comment(product, pr, ident.migration_marker)
    for run_id in rs.all_run_ids():
        try:
            state = rs.load(run_id)
        except (OSError, ValueError, TypeError):
            continue  # нечитаемый леджер называет следующий отчёт (`_local_items`)
        if getattr(state, "mode", "strict") == "test":
            continue  # прогон test миграции акта не знает
        if (state.repo_slug, state.ws_id) != (ident.product, ident.ws_id):
            continue  # тот же ключ в другом воркстриме — другая заявка (B1-14)
        op = state.ops.get(ident.request_id)
        if op is not None and al.is_live(op):
            al.invalidate_request(state, ident.request_id, "act_migrated")
    return ident.request_id


def human_context(
    caller: str | None, humans: frozenset[str], flags: Sequence[str]
) -> None:
    """Флаги человеческого контекста (`--invalidate`, `--confirm-resolved`,
    `--attest-drained`) — только от логина из `human_approvers` текущей
    активации; иначе `Refused` (defense-in-depth: агент не самоаттестуется)."""
    if flags and (caller or "") not in humans:
        raise Refused(
            f"{', '.join(flags)} — только человек из human_approvers; "
            f"вызывающий {caller or '(не назван)'}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI барьера: 0 — пусто и полно; 1 — есть живые; 2 — действие человеческого
    контекста отвергнуто; 3 — неполно (в т. ч. любой сбой чтения).

    Ошибка разбора аргументов и `--help` — тоже 3: отчёта нет, значит барьер не
    пройден (fail-closed); argparse печатает свою справку/причину в stderr/stdout,
    а коды 0 «пусто» и 2 «отвергнуто» не возникают (ревью Task 16, N2)."""
    try:
        return _main(argv)
    except SystemExit:  # argparse: --help или неверный аргумент — отчёта нет
        return EXIT_INCOMPLETE
    except Refused as exc:
        print(f"act_barrier: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except Exception as exc:  # noqa: BLE001 — любой сбой — «неполно», не «есть живые»
        print(f"act_barrier: {exc!r} — барьер неполон", file=sys.stderr)
        return EXIT_INCOMPLETE


def _main(argv: Sequence[str] | None) -> int:
    from governance.act_settings import config_home, load_settings
    from governance.act_time import Unavailable
    from governance.ops import RealOps

    p = argparse.ArgumentParser(prog="act_barrier")
    p.add_argument("--product", required=True)
    p.add_argument("--confirm-resolved", type=int, action="append", default=[])
    p.add_argument("--attest-drained", action="append", default=[])
    p.add_argument("--invalidate", type=int)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    refusal = _config_refusal()
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return EXIT_REFUSED
    ops = RealOps()
    probe = SimpleNamespace(repo_slug=args.product, run_id="act-barrier", base_ref=None)
    live = act_status.live_policy(probe, act_status.real_deps(probe))  # type: ignore[arg-type]
    draining = load_settings(config_home(os.environ)).draining
    if isinstance(live, Unavailable):
        print(f"act_barrier: текущая активация — {live.reason}", file=sys.stderr)
        return EXIT_INCOMPLETE
    humans = frozenset(live.policy.approvers)
    flags = [
        name
        for name, used in (
            ("--invalidate", args.invalidate is not None),
            ("--confirm-resolved", bool(args.confirm_resolved)),
            ("--attest-drained", bool(args.attest_drained)),
        )
        if used
    ]
    caller = ops.caller_login() if flags else None
    human_context(caller, humans, flags)
    if args.invalidate is not None:
        key = invalidate(
            args.product, ops, args.invalidate, humans=humans, caller=caller or ""
        )
        print(f"act_migrated: {key}")
    report = live_unmarked(
        args.product,
        ops,
        humans=humans,
        confirmed=frozenset(args.confirm_resolved),
        attested=tuple(args.attest_drained),
        draining=draining,
        runs=rs.all_run_ids(),
    )
    if args.json:
        body = {"items": report.items, "complete": report.complete, "gaps": report.gaps}
        print(json.dumps(body, ensure_ascii=False))
    else:
        print("\n".join([*report.items, *(f"gap: {g}" for g in report.gaps)]))
    if not report.complete:
        return EXIT_INCOMPLETE
    return EXIT_LIVE if report.items else EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())
