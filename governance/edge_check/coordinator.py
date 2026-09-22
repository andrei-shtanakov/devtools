"""Координатор edge-check волны — срез 2 (спека edge-check §6, D9/D10/D12).

Состав рёбер выводится из `upstream` профиля прогона (D2): для каждого узла
уровня волны — одно ребро со всеми его основаниями (D1), для charter — два
ребра (по брифу заказчика и по инженерному брифу), потому что загрузчик не
поддерживает несколько оснований вместе с `applicability`, а инженерный
бриф в customer-маршруте отсутствует законно (`N/A`).

Основания читаются из BASE (`ops.show_file`), а не из рабочего дерева:
инвариант 3 спеки sequential-node-approval — проверка идёт против тех же
байтов, что запинованы; source-слой charter (`00-discovery/*`) — из
рабочего дерева волны, где его кладёт `materialize-brief`.

Результаты пишутся в КАТАЛОГ ПРОГОНА (не в worktree цели — иначе
`approve_node` отказал бы на входе по `is_dirty`): по файлу на ребро в
`<run_dir>/edge-check/w<wave>/<node>--<edge_id>.json` и строкой в леджер
`<run_dir>/edge-check/ledger.jsonl`. Ключ результата — D9 (subject,
основания с хэшами, решение о применимости, `check_identity`); действующий
результат ключа — последняя завершённая попытка (D10).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from governance import bundle_dag
from governance.edge_check.check import run_check
from governance.edge_check.rules import EdgeCheckError
from governance.ops import Ops
from governance.policy_sources import profile_levels
from governance.run_state import RunState

DEFAULT_CONTRACTS = Path(__file__).resolve().parents[2] / "contracts/edge-check/v1"
DEFAULT_MODEL = "claude-opus-5"
LEDGER_NAME = "ledger.jsonl"

#: Короткие имена ролей в идентификаторах рёбер (`behaviour-vs-requirements`).
_SHORT = {"behaviour-spec": "behaviour"}

#: Рёбра charter: (edge_id, роль основания). Оба — всегда; отсутствие
#: инженерного брифа даёт `N/A` по applicability набора, не `ERROR`.
CHARTER_EDGES = (
    ("charter-vs-customer-brief", "customer-brief"),
    ("charter-vs-engineer-brief", "engineer-brief"),
)
_DISCOVERY_ROLES = frozenset(role for _, role in CHARTER_EDGES)

_EXIT = {"PASS": 0, "FAIL": 1, "ERROR": 3}


@dataclass(frozen=True)
class Edge:
    """Одна проверка: узел, набор правил, основания `роль → узел/источник`."""

    node: str
    edge_id: str
    bases: dict[str, str]


@dataclass(frozen=True)
class LevelResult:
    """Итог уровня: записи по (узел, ребро), сводный вердикт и код выхода."""

    records: dict[tuple[str, str], dict]
    verdict: str
    exit_code: int


def edge_id_for(node: str, upstream: list[str]) -> str:
    """`<узел>-vs-<основание>[-<основание>…]` короткими именами ролей."""
    short = [_SHORT.get(u, u) for u in upstream]
    return f"{_SHORT.get(node, node)}-vs-{'-'.join(short)}"


def edges_for_level(profile_artifacts: list[dict], level: int) -> list[Edge]:
    """Рёбра узлов уровня `level` по `upstream` профиля (D2).

    Делегаты (`delegate:`) проверке не подлежат — это не документы бандла.
    Порядок — порядок артефактов в профиле.
    """
    lv = profile_levels(profile_artifacts)
    edges: list[Edge] = []
    for artifact in profile_artifacts:
        node = str(artifact["id"])
        if lv[node] != level or artifact.get("delegate"):
            continue
        if node == "charter":
            edges += [Edge(node, eid, {role: role}) for eid, role in CHARTER_EDGES]
            continue
        upstream = [str(u) for u in artifact.get("upstream") or []]
        edges.append(Edge(node, edge_id_for(node, upstream), {u: u for u in upstream}))
    return edges


def result_key(
    subject: list[dict], bases: list[dict], absence: list[dict], check_identity: str
) -> str:
    """Ключ результата D9: временные пути и время проверки не входят."""
    canon = json.dumps(
        {
            "subject": [[f["role"], f["path"], f["sha256"]] for f in subject],
            "bases": [[f["role"], f["path"], f["sha256"]] for f in bases],
            "absence": [[a["path"], a["rule_id"]] for a in absence],
            "check_identity": check_identity,
        },
        ensure_ascii=False,
        sort_keys=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def run_level(
    state: RunState,
    ops: Ops,
    run_dir: Path,
    wave: int,
    profile_path: Path,
    *,
    call: Callable[[str], str] | None = None,
    contracts_dir: Path = DEFAULT_CONTRACTS,
    model: str = DEFAULT_MODEL,
    effort: str | None = None,
    timeout: int = 600,
) -> LevelResult:
    """Проверить все рёбра узлов волны `wave` (1-based; уровень = wave − 1).

    Сводный вердикт: любой `ERROR` → `ERROR` (код 3), иначе любой `FAIL` →
    `FAIL` (код 1), иначе `PASS` (код 0; `N/A` рёбра засчитываются как
    пройденные — отсутствие их основания разрешено правилом). Профиль без
    узлов этого уровня — `EdgeCheckError("no_edges")`: проверка, которой не
    с чего начаться, не может быть зелёной молчанием (D4).
    """
    level = wave - 1
    artifacts = (yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}).get(
        "artifacts", []
    )
    edges = edges_for_level(artifacts, level)
    if not edges:
        raise EdgeCheckError(
            "no_edges", f"профиль {profile_path} не объявляет узлов уровня {level}"
        )
    out_dir = run_dir / "edge-check" / f"w{wave}"
    out_dir.mkdir(parents=True, exist_ok=True)
    records: dict[tuple[str, str], dict] = {}
    for edge in edges:
        record = _check_edge(
            state, ops, edge, out_dir, contracts_dir, model, effort, timeout, call
        )
        record["node"] = edge.node
        record["wave"] = wave
        record["result_key"] = result_key(
            record["subject"], record["bases"], record["absence"],
            record["check_identity"],
        )
        path = out_dir / f"{edge.node}--{edge.edge_id}.json"
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        _append_ledger(run_dir, record, path)
        records[(edge.node, edge.edge_id)] = record
    verdicts = {r["verdict"] for r in records.values()}
    verdict = "ERROR" if "ERROR" in verdicts else "FAIL" if "FAIL" in verdicts else "PASS"
    return LevelResult(records, verdict, _EXIT[verdict])


def load_level_records(run_dir: Path, wave: int) -> dict[tuple[str, str], dict]:
    """Записи рёбер волны из каталога прогона (по файлу на ребро)."""
    out_dir = run_dir / "edge-check" / f"w{wave}"
    records: dict[tuple[str, str], dict] = {}
    for path in sorted(out_dir.glob("*.json")) if out_dir.is_dir() else []:
        record = json.loads(path.read_text(encoding="utf-8"))
        records[(record["node"], record["edge"])] = record
    return records


def effective_results(run_dir: Path) -> dict[str, dict]:
    """Действующий результат по ключу D9 — последняя завершённая попытка (D10)."""
    ledger = run_dir / "edge-check" / LEDGER_NAME
    if not ledger.is_file():
        return {}
    effective: dict[str, dict] = {}
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            effective[entry["key"]] = entry
    return effective


def _append_ledger(run_dir: Path, record: dict, path: Path) -> None:
    entry = {
        "key": record["result_key"],
        "attempt_id": record["attempt_id"],
        "wave": record["wave"],
        "node": record["node"],
        "edge": record["edge"],
        "verdict": record["verdict"],
        "started_at": record["started_at"],
        "finished_at": record["finished_at"],
        "file": str(path.relative_to(run_dir)),
    }
    ledger = run_dir / "edge-check" / LEDGER_NAME
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _check_edge(
    state: RunState,
    ops: Ops,
    edge: Edge,
    out_dir: Path,
    contracts_dir: Path,
    model: str,
    effort: str | None,
    timeout: int,
    call: Callable[[str], str] | None,
) -> dict:
    """Собрать объявленный вход ребра во временный каталог и провести проверку.

    Каталог входа — под каталогом прогона: `run_check` требует, чтобы subject
    и основания лежали внутри `bundle_dir`, а основания берутся из base, не
    из дерева. Отсутствующий файл в каталог не кладётся — `run_check` сам
    классифицирует отсутствие (`missing_mandatory_input` либо `N/A`).
    """
    input_dir = out_dir / "input" / f"{edge.node}--{edge.edge_id}"
    if input_dir.exists():
        shutil.rmtree(input_dir)
    input_dir.mkdir(parents=True)
    names = dict(bundle_dag.node_filenames(bundle_dag.BUNDLE_DAG))
    worktree = Path(state.target_dir) / state.bundle_dir
    subject_rel = names.get(edge.node)
    if subject_rel is None:
        return _config_error(edge, "unknown_node", f"узла {edge.node!r} нет в DAG")
    _copy_if_present(worktree / subject_rel, input_dir / subject_rel)
    bases: list[tuple[str, Path]] = []
    for role, source in edge.bases.items():
        if role in _DISCOVERY_ROLES:
            rel = _discovery_path(state, role)
            if rel is None:
                bases.append((role, input_dir / "00-discovery" / f"{role}.absent"))
                continue
            _copy_if_present(worktree / rel, input_dir / rel)
        else:
            rel = names.get(source)
            if rel is None:
                return _config_error(
                    edge, "unknown_node", f"основания {source!r} нет в DAG"
                )
            text = ops.show_file(
                state.target_dir, state.base_ref or "master",
                f"{state.bundle_dir}/{rel}",
            )
            if text is not None:
                (input_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                (input_dir / rel).write_text(text, encoding="utf-8")
        bases.append((role, input_dir / rel))
    return run_check(
        edge.edge_id, input_dir, [input_dir / subject_rel], bases,
        contracts_dir=contracts_dir, model=model, effort=effort,
        timeout=timeout, call=call,
    )


def _discovery_path(state: RunState, role: str) -> str | None:
    """Путь источника discovery-плоскости относительно `bundle_dir`, если есть.

    `customer-brief` — `requirements_source` дескриптора (в customer-фрейме
    совпадает с primary); `engineer-brief` — primary только в
    engineer-фрейме, иначе отсутствует (законно: `N/A`).
    """
    brief = state.brief
    if not brief:
        return None
    if role == "customer-brief":
        value = brief.get("requirements_source")
    else:
        value = brief.get("primary") if brief.get("frame") == "engineer" else None
    return value if isinstance(value, str) and value else None


def _copy_if_present(src: Path, dst: Path) -> None:
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())


def _config_error(edge: Edge, code: str, reason: str) -> dict:
    """Запись `ERROR` для ребра, до которого `run_check` не дошёл."""
    from governance.edge_check.check import SCHEMA_VERSION, _now

    now = _now()
    return {
        "schema_version": SCHEMA_VERSION,
        "edge": edge.edge_id,
        "attempt_id": hashlib.sha1(f"{edge.node}{now}".encode()).hexdigest(),
        "started_at": now,
        "finished_at": now,
        "check_identity": "",
        "subject": [],
        "bases": [],
        "absence": [],
        "criteria": [],
        "findings": [],
        "verdict": "ERROR",
        "error_code": code,
        "reason": reason,
    }
