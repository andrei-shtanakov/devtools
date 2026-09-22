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
CHANGES_REQUESTED (не гасится); 3 — факт не установлен (ревью/сеть);
4 — голова PR не равна записанной (чужой push).
"""

from __future__ import annotations

import json
import posixpath
from pathlib import Path

from governance import approval_ledger as al
from governance import approve_node as an
from governance import bundle_dag
from governance.ops import Ops, review_login
from governance.run_state import RunState

EDGE_MARKER = "ai-prosto-edge-check"
CODE_OK, CODE_CHANGES, CODE_UNRESOLVED, CODE_HEAD_MOVED = 0, 1, 3, 4


def evidence_dir(state: RunState) -> str:
    """`workstreams/<ws>/evidence/edge-check` — рядом с `spec/`, вне гейта."""
    parent = posixpath.dirname(state.bundle_dir.rstrip("/"))
    return f"{parent}/evidence/edge-check" if parent else "evidence/edge-check"


def evidence_files(state: RunState, records: dict[tuple[str, str], dict]) -> dict[str, bytes]:
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
) -> list[str]:
    """Пути коммита заявки вне объявленной поверхности D16.

    Поверхность: файлы узлов заявки, файлы их downstream-замыкания (каскад
    `stale` того же коммита), `00-discovery/*` для W1 (charter несёт
    source-слой сам), `evidence/edge-check/`. Всё прочее — отказ до PR.
    """
    head = op.get("head_sha")
    changed = ops.commit_files(state.target_dir, head) if head else None
    if changed is None:
        raise an._unresolved(f"состав коммита заявки {head!r}")
    names = dict(bundle_dag.node_filenames(dag))
    nodes = set(op["nodes"])
    for node in list(nodes):
        nodes |= an._downstream_closure(dag, node)
    allowed = {f"{state.bundle_dir}/{names[n]}" for n in nodes if n in names}
    prefixes = [evidence_dir(state) + "/"]
    if "charter" in op["nodes"]:
        prefixes.append(f"{state.bundle_dir}/00-discovery/")
    return sorted(
        path for path in changed
        if path not in allowed and not any(path.startswith(p) for p in prefixes)
    )


def level_verdict(records: dict[tuple[str, str], dict]) -> str:
    verdicts = {r.get("verdict") for r in records.values()}
    if "ERROR" in verdicts or not records:
        return "ERROR"
    return "FAIL" if "FAIL" in verdicts else "PASS"


def checklist_body(state: RunState, head: str, records: dict[tuple[str, str], dict]) -> str:
    lines = [
        f"<!-- {EDGE_MARKER} head={head} -->",
        f"Edge-check волны W{state.wave} прогона `{state.run_id}`: "
        f"{level_verdict(records)}.",
        "",
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
        f"Evidence: `{evidence_dir(state)}/<node>--<edge>.json` — по файлу на "
        "ребро; действующие результаты — леджер прогона (D12).",
        "Это одобрение публикует результат edge-check (D16) и снимает правило "
        "одобряющего ревью; подписью узла оно НЕ является — подпись создаёт "
        "мерж candidate человеком из allowlist.",
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
) -> int:
    """Опубликовать candidate волны с evidence и одобряющим ревью; → код."""
    dag = bundle_dag.dag_for(legacy_bundle)
    op = state.ops[key]
    head = op.get("head_sha")
    if not head:
        raise RuntimeError(
            f"заявка {key} без head_sha — ветка не приведена к снимку; "
            "сперва propose_from_source"
        )
    if level_verdict(records) != "PASS":
        return CODE_CHANGES
    bad = surface_violations(state, ops, dag, op)
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
            ours = (
                ops.is_ancestor(state.target_dir, remote, head)
                if remote else False
            )
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
        ops.commit_paths(
            state.target_dir,
            list(files),
            f"evidence: edge-check волны W{state.wave} для заявки {key} "
            "(fleet-agent)",
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
    if an.pr_head(state, ops, pr) != op["head_sha"]:
        return CODE_HEAD_MOVED
    reviews = ops.pr_reviews(state.repo_slug, pr)
    if reviews is None:
        return CODE_UNRESOLVED
    effective = _effective_reviews(reviews)
    if "CHANGES_REQUESTED" in effective.values():
        return CODE_CHANGES
    marker_line = f"<!-- {EDGE_MARKER} head={head} -->"
    latest = ops.latest_review_body(state.repo_slug, pr) or ""
    if marker_line in latest and effective.get(review_login()) == "APPROVED":
        return CODE_OK
    body = checklist_body(state, head, records)
    if not ops.publish_review(
        state.repo_slug, pr, event="APPROVE", body=body, marker=EDGE_MARKER
    ):
        return CODE_UNRESOLVED
    return CODE_OK
