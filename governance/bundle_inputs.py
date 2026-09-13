"""Single source of truth for direct governance-node input blobs."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from governance import approval_facts as af
from governance import brief_input
from governance import bundle_dag
from governance.facts import Fact, Outcome, unavailable
from governance.ops import Ops
from governance.run_state import RunState
from governance.stale_adapter import blob_sha1, blob_sha1_bytes


def _filename(
    dag: tuple[tuple[str, tuple[str, ...]], ...], node: str
) -> str:
    for filename, _upstreams in dag:
        if bundle_dag.node_id(filename) == node:
            return filename
    raise KeyError(node)


def _upstreams(
    dag: tuple[tuple[str, tuple[str, ...]], ...], node: str
) -> tuple[str, ...]:
    for filename, upstreams in dag:
        if bundle_dag.node_id(filename) == node:
            return upstreams
    raise KeyError(node)


def _source_inputs(state: RunState, node: str) -> Fact[dict[str, str]]:
    if node != "charter" or state.brief is None:
        return Fact(Outcome.FOUND, {}, "supplemental source inputs отсутствуют")
    descriptor = state.brief
    primary = descriptor.get("primary")
    requirements_source = descriptor.get("requirements_source")
    frame = descriptor.get("frame")
    paths = descriptor.get("source_paths")
    expected = descriptor.get("source_blobs")
    if (
        not isinstance(primary, str)
        or not isinstance(requirements_source, str)
        or frame not in ("customer", "engineer")
        or not isinstance(paths, list)
        or not all(isinstance(path, str) for path in paths)
        or not isinstance(expected, dict)
        or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in expected.items()
        )
    ):
        return unavailable("brief descriptor в run.json повреждён")
    mapping = {"discovery-brief": primary}
    if frame == "engineer":
        mapping["discovery-customer"] = requirements_source
    if set(mapping) != set(expected) or set(mapping.values()) != set(paths):
        return unavailable(
            "brief descriptor расходится между frame/source_paths/source_blobs"
        )
    if primary != brief_input.PRIMARY_REL:
        return unavailable("brief descriptor несёт неканонический primary path")
    for path in paths:
        pure = PurePosixPath(path)
        if (
            "\\" in path
            or pure.is_absolute()
            or ".." in pure.parts
            or not path.startswith("00-discovery/")
        ):
            return unavailable(
                f"brief descriptor несёт непереносимый source path {path!r}"
            )
    if (
        (frame == "customer" and requirements_source != primary)
        or (frame == "engineer" and requirements_source == primary)
    ):
        return unavailable(
            "brief descriptor расходится между frame и requirements_source"
        )
    return Fact(Outcome.FOUND, mapping, "supplemental source inputs прочитаны")


def direct_blobs(
    state: RunState,
    ops: Ops,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    node: str,
    ref: str | None,
    *,
    known_texts: dict[str, str] | None = None,
) -> Fact[dict[str, str]]:
    """Return actual blobs of every direct DAG and supplemental input.

    ``ref=None`` means the current worktree (S4 prospective check); any
    string is one immutable git ref used uniformly for ordinary upstreams and
    discovery source files. Descriptor hashes are expectations only: bytes at
    the requested ref are always read and hashed, and a mismatch is a typed
    refusal rather than a silently trusted caller-supplied hash.
    """
    source_fact = _source_inputs(state, node)
    if source_fact.outcome is not Outcome.FOUND or source_fact.value is None:
        return Fact(source_fact.outcome, None, source_fact.detail)
    named_paths = {
        upstream: _filename(dag, upstream)
        for upstream in _upstreams(dag, node)
    }
    named_paths.update(source_fact.value)
    source_names = set(source_fact.value)
    actual: dict[str, str] = {}
    for name, relative in named_paths.items():
        bundle_path = f"{state.bundle_dir}/{relative}"
        if known_texts is not None and relative in known_texts:
            text = known_texts[relative]
        elif ref is None and name in source_names:
            path = Path(state.target_dir) / bundle_path
            try:
                data = path.read_bytes()
            except OSError as exc:
                return unavailable(f"байты {bundle_path} в worktree: {exc}")
            actual[name] = blob_sha1_bytes(data)
            continue
        elif ref is None:
            path = Path(state.target_dir) / bundle_path
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                return unavailable(f"байты {bundle_path} в worktree: {exc}")
        elif name in source_names:
            bytes_fact = af.read_blob_bytes(
                ops, state.target_dir, ref, bundle_path
            )
            if (
                bytes_fact.outcome is not Outcome.FOUND
                or bytes_fact.value is None
            ):
                return Fact(bytes_fact.outcome, None, bytes_fact.detail)
            actual[name] = blob_sha1_bytes(bytes_fact.value)
            continue
        else:
            fact = af.read_blob_text(
                ops, state.target_dir, ref, bundle_path
            )
            if fact.outcome is not Outcome.FOUND or fact.value is None:
                return Fact(fact.outcome, None, fact.detail)
            text = fact.value
        actual[name] = blob_sha1(text)

    expected = state.brief.get("source_blobs", {}) if state.brief else {}
    for source_name, expected_blob in expected.items():
        if source_name in actual and actual[source_name] != expected_blob:
            return Fact(
                Outcome.FORBIDDEN,
                None,
                f"{source_name}: blob {actual[source_name]} != discovery "
                f"descriptor {expected_blob}",
            )
    return Fact(Outcome.FOUND, actual, "direct input blobs прочитаны")
