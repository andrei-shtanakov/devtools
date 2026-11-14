"""Адаптер публикации edge-check — срез 3 (спека edge-check §8, D14–D16; S7).

Ревью candidate-PR волны = публикация результата edge-check: evidence
(`.json` по ребру) едет в ветку заявки поверх её головы, а одобряющее
ревью со своим маркером снимает правило «одно одобряющее ревью» — прозу
candidate scope-аттестация не покрывает (S7-прогон, находка 6).

Доверие — к происхождению (D12): адаптер читает записи ИЗ КАТАЛОГА
ПРОГОНА (леджер координатора), в ветку только пишет; JSON из ветки он не
читает, поэтому автор PR не может подсунуть себе `PASS`.

Порядок (S7): заявка собрана по всем узлам волны и приведена к снимку
(`propose_from_source(push=False)`), затем ОДИН evidence-коммит поверх
головы, `head_sha` заявки перезаписывается на него (иначе повтор
`_publish_candidate` встал бы на предка и `push -u` отказал non-ff), затем
push + PR + одно ревью на эту голову. Повтор идемпотентен: evidence уже в
дереве головы теми же байтами ⇒ коммита нет, `head_sha` не меняется,
ревью с маркером на эту голову уже есть ⇒ публикуется ничего.

Коды: 0 — опубликовано; 1 — вердикт не PASS либо у человека действующее
CHANGES_REQUESTED (не гасится); 3 — факт не установлен (ревью/сеть либо
форджа не догнала наш push за отведённое ожидание — причина печатается);
4 — голова PR не равна записанной и не наша (чужой push).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from governance import act_surface, bundle_dag
from governance import approval_ledger as al
from governance import approve_node as an
from governance.act_request import ACT_REVIEW_V1
from governance.edge_check import override as edge_override
from governance.ops import Ops, review_login
from governance.run_state import RunState

EDGE_MARKER = "ai-prosto-edge-check"
CODE_OK, CODE_CHANGES, CODE_UNRESOLVED, CODE_HEAD_MOVED = 0, 1, 3, 4

#: Форджа догоняет push не сразу: `headRefOid` PR ещё секунды после push
#: отдаёт прежнюю голову (живой прогон Linux 12.11: ложный «чужой push»
#: сразу после evidence-коммита). Голова перечитывается до
#: `_HEAD_SETTLE_TRIES` раз с паузой `_HEAD_SETTLE_DELAY` секунд.
_HEAD_SETTLE_TRIES = 5
_HEAD_SETTLE_DELAY = 3.0
#: Точка подмены паузы в тестах (как `approve_node._SLEEP`).
_SLEEP = time.sleep


def evidence_dir(state: RunState) -> str:
    """`workstreams/<ws>/evidence/edge-check` — вне гейта; место задаёт общая
    поверхность D16 (`act_surface.evidence_dir`), не `bundle_dir` (спека df §5.4).
    """
    return act_surface.evidence_dir(state.ws_id)


def evidence_files(
    state: RunState, records: dict[tuple[str, str], dict]
) -> dict[str, bytes]:
    """Путь в репо → байты evidence, по файлу на ребро (`<node>--<edge>.json`)."""
    root = evidence_dir(state)
    return {
        f"{root}/{node}--{edge}.json": (
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        for (node, edge), record in sorted(records.items())
    }


def surface_violations(
    state: RunState,
    ops: Ops,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    op: dict,
    key: str,
) -> list[str]:
    """Пути коммита заявки `key` вне объявленной поверхности D16.

    Поверхность считает общая `act_surface.surface_violations` — та же, что
    у предпроверки издателя (спека df §5.4, O6); здесь только состав
    коммита. Evidence допускается лишь `*.json` своего воркстрима — сужение
    rev 11, общее для конвейера и издателя.
    """
    head = op.get("head_sha")
    changed = ops.commit_files(state.target_dir, head) if head else None
    if changed is None:
        raise an._unresolved(f"состав коммита заявки {head!r}")
    return act_surface.surface_violations(
        changed,
        ws_id=state.ws_id,
        request_key=key,
        nodes=op["nodes"],
        bundle_dir=state.bundle_dir,
        dag=dag,
        act=op.get("act"),
    )


#: Вердикт уровня, принятого человеком вопреки замечаниям (только `test`).
OVERRIDE = "OVERRIDE"
OVERRIDE_TEXT = "принято человеком вопреки замечаниям"
#: Digest'ы находок, принятых человеком (`edge_override.finding_digest`).
Overridden = set[str] | None


def level_verdict(
    records: dict[tuple[str, str], dict], overridden: Overridden = None
) -> str:
    """Вердикт уровня; `OVERRIDE` — FAIL, digest каждой блокирующей находки
    которого (пересчитан по записям) — в `overridden` (`edge_override.accepted`)."""
    verdicts = {r.get("verdict") for r in records.values()}
    if "ERROR" in verdicts or not records:
        return "ERROR"
    if "FAIL" not in verdicts:
        return "PASS"
    if overridden and edge_override.accepted(records, overridden) is not None:
        return OVERRIDE
    return "FAIL"


def edge_verdict(
    head: str, records: dict[tuple[str, str], dict], overridden: Overridden = None
) -> dict:
    """Запись вердикта edge-check для операции candidate (план 3c-2).

    Голова candidate-PR, вердикт уровня и хэши входа по ребру (subject и
    основания из записей прогона) — то, на чём автоматический шаг `test`
    сверяет, что положительный вердикт относится к этим байтам.
    """
    return {
        "head": head,
        "verdict": level_verdict(records, overridden),
        "inputs": {
            f"{node}--{edge}": sorted(
                str(f.get("sha256", ""))
                for f in [*record.get("subject", []), *record.get("bases", [])]
            )
            for (node, edge), record in sorted(records.items())
        },
    }


def checklist_body(
    state: RunState,
    head: str,
    records: dict[tuple[str, str], dict],
    act: str | None = None,
    overridden: Overridden = None,
) -> str:
    verdict = level_verdict(records, overridden)
    lines = [
        f"<!-- {EDGE_MARKER} head={head} -->",
        f"Edge-check волны W{state.wave} прогона `{state.run_id}`: {verdict}.",
        "",
    ]
    if verdict == OVERRIDE and overridden:
        keys = edge_override.accepted(records, overridden) or set()
        lines += [
            f"Узел: {OVERRIDE_TEXT} (режим test, журнал "
            f"`{edge_override.JOURNAL}` прогона): "
            + ", ".join(f"`{n}/{e}/{r}`" for n, e, r in sorted(keys))
            + ".",
            "",
        ]
    lines += [
        "| узел | ребро | вердикт | check_identity | subject sha256 |",
        "|---|---|---|---|---|",
    ]
    for (node, edge), record in sorted(records.items()):
        subject = ", ".join(f.get("sha256", "")[:12] for f in record.get("subject", []))
        lines.append(
            f"| {node} | {edge} | {record.get('verdict')} | "
            f"{str(record.get('check_identity', ''))[:12]} | {subject} |"
        )
    lines += [
        "",
        (
            f"Evidence: `{evidence_dir(state)}/<node>--<edge>.json` — по файлу на "
            "ребро; действующие результаты — леджер прогона (D12)."
        ),
        (
            "Это одобрение публикует результат edge-check (D16) и снимает правило "
            "одобряющего ревью; подписью узла оно НЕ является — акт одобрения — "
            "approve этого PR человеком из human_approvers по его голове "
            "(review/v1, §9.1); мержить candidate не нужно, его доставляет агент."
            if act == ACT_REVIEW_V1
            else "Это одобрение публикует результат edge-check (D16) и снимает "
            "правило одобряющего ревью; подписью узла оно НЕ является — подпись "
            "создаёт мерж candidate человеком из allowlist."
        ),
    ]
    return "\n".join(lines)


def _already_in_head(
    state: RunState, ops: Ops, head: str, files: dict[str, bytes]
) -> bool:
    return all(
        ops.show_file_bytes(state.target_dir, head, path) == data
        for path, data in files.items()
    )


def _effective_reviews(reviews: list[dict]) -> dict[str, str]:
    effective: dict[str, str] = {}
    for review in reviews:
        stateful = str(review.get("state", ""))
        if stateful != "PENDING":
            effective[str(review.get("login"))] = stateful
    return effective


def publish_wave(
    state: RunState,
    ops: Ops,
    key: str,
    records: dict[tuple[str, str], dict],
    *,
    legacy_bundle: int | None = None,
    overridden: Overridden = None,
) -> int:
    """Опубликовать candidate волны с evidence и одобряющим ревью; → код.

    `overridden` — digest'ы находок, принятых человеком вопреки замечаниям (раннер
    передаёт их только при вердикте шага edge `OVERRIDE`); действуют лишь
    в `test`, в `strict` FAIL остаётся FAIL.
    """
    dag = bundle_dag.dag_for(legacy_bundle)
    op = state.ops[key]
    head = op.get("head_sha")
    if not head:
        raise RuntimeError(
            f"заявка {key} без head_sha — ветка не приведена к снимку; "
            "сперва propose_from_source"
        )
    if state.mode != "test":
        overridden = None
    if level_verdict(records, overridden) not in ("PASS", OVERRIDE):
        return CODE_CHANGES
    bad = surface_violations(state, ops, dag, op, key)
    if bad:
        raise RuntimeError(
            f"коммит заявки {key} ({head[:8]}) меняет пути вне поверхности "
            f"candidate (D16): {', '.join(bad)} — PR не создаётся"
        )
    # Уже созданный PR (режим reapprove: `approve_node` пушит и создаёт PR
    # ДО адаптера) сверяется с записанной головой ДО evidence-коммита:
    # чужой push поверх головы — код 4, а не сырой non-ff на push. Голова
    # PR, являющаяся ПРЕДКОМ записанной, — наша же (повтор после
    # evidence-коммита, не дошедшего до push), это не чужой push.
    existing = op.get("candidate_pr")
    if existing is not None:
        remote = an.pr_head(state, ops, existing)
        if remote != head:
            ops.fetch_branch(state.target_dir, op["branch"])
            ours = ops.is_ancestor(state.target_dir, remote, head) if remote else False
            if ours is None:
                return CODE_UNRESOLVED
            if not ours:
                return CODE_HEAD_MOVED
    files = evidence_files(state, records)
    if not _already_in_head(state, ops, head, files):
        an.switch_to_request(state, ops, key)
        for path, data in files.items():
            target = Path(state.target_dir) / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        an._commit_paths(
            ops,
            state.target_dir,
            list(files),
            f"evidence: edge-check волны W{state.wave} для заявки {key} (fleet-agent)",
            force_paths=tuple(files),
        )
        new_head = ops.rev_parse(state.target_dir, "HEAD")
        if new_head is None:
            raise an._unresolved("HEAD после evidence-коммита")
        al.record_head_sha(state, key, new_head)
        head = new_head
    an.publish_request(state, ops, key, legacy_bundle=legacy_bundle)
    op = state.ops[key]
    pr = op["candidate_pr"]
    code = _pushed_head_code(state, ops, pr, op)
    if code is not None:
        return code
    reviews = ops.pr_reviews(state.repo_slug, pr)
    if reviews is None:
        return CODE_UNRESOLVED
    effective = _effective_reviews(reviews)
    if "CHANGES_REQUESTED" in effective.values():
        return CODE_CHANGES
    # `test`: одна учётка — автор и ревьюер, GitHub не даёт одобрить свой
    # PR, и edge-check едет комментарием; дедуп — по маркеру этой головы
    # при `COMMENTED`. Неизвестный режим — strict (план 3c-2).
    test = state.mode == "test"
    event, published = ("COMMENT", "COMMENTED") if test else ("APPROVE", "APPROVED")
    marker_line = f"<!-- {EDGE_MARKER} head={head} -->"
    latest = ops.latest_review_body(state.repo_slug, pr) or ""
    if marker_line in latest and effective.get(review_login()) == published:
        return CODE_OK
    body = checklist_body(state, head, records, op.get("act"), overridden)
    if not ops.publish_review(
        state.repo_slug, pr, event=event, body=body, marker=EDGE_MARKER
    ):
        return CODE_UNRESOLVED
    return CODE_OK


def _pushed_head_code(state: RunState, ops: Ops, pr: int, op: dict) -> int | None:
    """Голова PR после нашего push: `None` — равна записанной, иначе код.

    Форджа может отставать (см. `_HEAD_SETTLE_TRIES`). Голова PR, оставшаяся
    ПРЕДКОМ записанной и после ожидания, — наша же, не догнанная форджей:
    `CODE_UNRESOLVED` (повтор resume), а не `CODE_HEAD_MOVED`. Чужой push —
    голова, которая предком записанной не является.
    """
    head = op["head_sha"]
    remote = None
    for attempt in range(_HEAD_SETTLE_TRIES):
        remote = an.pr_head(state, ops, pr)
        if remote == head:
            return None
        if attempt + 1 < _HEAD_SETTLE_TRIES:
            _SLEEP(_HEAD_SETTLE_DELAY)
    if remote is None:
        return CODE_UNRESOLVED
    ops.fetch_branch(state.target_dir, op["branch"])
    ours = ops.is_ancestor(state.target_dir, remote, head)
    if ours is None or ours:
        print(
            f"publish: голова PR #{pr} ({remote[:8]}) не догнала наш push "
            f"({head[:8]}) за {_HEAD_SETTLE_TRIES} чтений — форджа отстаёт, "
            "не чужой push; повторите resume"
        )
        return CODE_UNRESOLVED
    return CODE_HEAD_MOVED
