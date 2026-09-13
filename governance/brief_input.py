"""Fail-closed intake and portable materialization of discovery briefs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from governance.discovery_contract import gate_check
from governance.stale_adapter import blob_sha1

PRIMARY_REL = "00-discovery/brief.md"


class BriefInputError(RuntimeError):
    """The discovery source cannot safely open a governance run."""


@dataclass(frozen=True)
class BriefSource:
    """Validated source files and their portable bundle destinations."""

    frame: Literal["customer", "engineer"]
    primary_input: Path
    primary_rel: str
    requirements_input: Path
    requirements_rel: str
    source_paths: tuple[str, ...]
    source_blobs: tuple[tuple[str, str], ...]

    def as_state(self) -> dict[str, object]:
        """JSON-safe descriptor; input-machine paths deliberately omitted."""
        return {
            "frame": self.frame,
            "primary": self.primary_rel,
            "requirements_source": self.requirements_rel,
            "source_paths": list(self.source_paths),
            "source_blobs": dict(self.source_blobs),
        }


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise BriefInputError(f"discovery-brief {path} не читается: {exc}") from exc


def _gate(path: Path, text: str) -> gate_check.Brief:
    findings = gate_check.check(text, base_dir=path.parent)
    errors = [finding for finding in findings if finding.level == "error"]
    if errors:
        rendered = "\n".join(
            f"- {finding.rule} [{finding.ref}]: {finding.message}"
            for finding in errors
        )
        raise BriefInputError(f"discovery gate отказал для {path}:\n{rendered}")
    parsed = gate_check.parse_brief(text)
    if parsed is None:  # defensive: GC-01 above should already have refused
        raise BriefInputError(f"discovery-brief {path} не разобран после gate pass")
    return parsed


def _portable_ref(raw: object) -> str:
    refs = [raw] if isinstance(raw, str) else raw
    if not isinstance(refs, list):
        raise BriefInputError("engineer traces_to должен быть списком путей")
    path_refs = [
        ref
        for ref in refs
        if isinstance(ref, str) and ref.endswith(".md") and not ref.startswith("[[")
    ]
    if len(path_refs) != 1:
        raise BriefInputError(
            "engineer brief должен нести ровно один customer *.md в traces_to"
        )
    ref = path_refs[0]
    pure = PurePosixPath(ref)
    if (
        not ref
        or "\\" in ref
        or pure.is_absolute()
        or ".." in pure.parts
        or pure.as_posix() == "brief.md"
    ):
        raise BriefInputError(
            f"engineer traces_to {ref!r} непереносим: нужен относительный "
            "*.md без '..' и без коллизии с brief.md"
        )
    return pure.as_posix()


def _resolve_like_gate(ref: str, base_dir: Path) -> Path | None:
    """Mirror GC-16 resolution without depending on a vendored private API."""
    root = base_dir.resolve()
    cursor = root
    while cursor.parent != cursor:
        if (cursor / ".git").exists():
            root = cursor
            break
        cursor = cursor.parent
    for anchor in (base_dir.resolve(), root):
        candidate = (anchor / ref).resolve()
        if candidate.is_file() and candidate.is_relative_to(root):
            return candidate
    return None


def inspect_brief(path: Path) -> BriefSource:
    """Validate an input brief and resolve its effective requirements source."""
    path = path.resolve()
    primary_text = _read(path)
    primary = _gate(path, primary_text)
    interview = primary.meta.get("interview") or {}
    frame = interview.get("frame") if isinstance(interview, dict) else None
    primary_blob = blob_sha1(primary_text)
    if frame == "customer":
        return BriefSource(
            frame="customer",
            primary_input=path,
            primary_rel=PRIMARY_REL,
            requirements_input=path,
            requirements_rel=PRIMARY_REL,
            source_paths=(PRIMARY_REL,),
            source_blobs=(("discovery-brief", primary_blob),),
        )
    if frame != "engineer":
        raise BriefInputError(f"неизвестный interview.frame: {frame!r}")

    ref = _portable_ref(primary.meta.get("traces_to") or [])
    customer_path = _resolve_like_gate(ref, path.parent)
    if customer_path is None:
        raise BriefInputError(f"customer upstream {ref!r} не разрешается")
    customer_text = _read(customer_path)
    customer = _gate(customer_path, customer_text)
    customer_interview = customer.meta.get("interview") or {}
    customer_frame = (
        customer_interview.get("frame")
        if isinstance(customer_interview, dict)
        else None
    )
    if customer_frame != "customer":
        raise BriefInputError(
            f"engineer upstream {ref!r} имеет frame={customer_frame!r}, "
            "ожидался customer"
        )
    if customer.meta.get("status") != "approved":
        raise BriefInputError(
            f"engineer upstream {ref!r} не approved: "
            f"status={customer.meta.get('status')!r}"
        )
    requirements_rel = f"00-discovery/{ref}"
    return BriefSource(
        frame="engineer",
        primary_input=path,
        primary_rel=PRIMARY_REL,
        requirements_input=customer_path,
        requirements_rel=requirements_rel,
        source_paths=(PRIMARY_REL, requirements_rel),
        source_blobs=(
            ("discovery-brief", primary_blob),
            ("discovery-customer", blob_sha1(customer_text)),
        ),
    )


def _current_blobs(source: BriefSource) -> dict[str, str]:
    primary_text = _read(source.primary_input)
    found = {"discovery-brief": blob_sha1(primary_text)}
    if source.frame == "engineer":
        found["discovery-customer"] = blob_sha1(
            _read(source.requirements_input)
        )
    return found


def materialize(source: BriefSource, target_dir: Path, bundle_dir: str) -> None:
    """Copy the validated source bytes into their fixed bundle layout."""
    expected = dict(source.source_blobs)
    actual = _current_blobs(source)
    if actual != expected:
        raise BriefInputError(
            f"discovery source изменился после intake: ожидалось {expected}, "
            f"сейчас {actual}"
        )
    bundle = target_dir / bundle_dir
    pairs = [(source.primary_input, source.primary_rel)]
    if source.frame == "engineer":
        pairs.append((source.requirements_input, source.requirements_rel))
    for input_path, rel in pairs:
        destination = bundle / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(input_path.read_bytes())


def inspect_materialized(target_dir: Path, bundle_dir: str) -> BriefSource:
    """Rebuild a source descriptor from a materialized bundle."""
    return inspect_brief(target_dir / bundle_dir / PRIMARY_REL)

