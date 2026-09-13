"""Fail-closed intake and portable materialization of discovery briefs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
from typing import Literal

from governance.discovery_contract import gate_check
from governance.stale_adapter import blob_sha1_bytes

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


def _read_bytes(path: Path) -> bytes:
    try:
        data = path.read_bytes()
        data.decode("utf-8")
        return data
    except (OSError, UnicodeError) as exc:
        raise BriefInputError(f"discovery-brief {path} не читается: {exc}") from exc


def _decode(data: bytes) -> str:
    """Decode UTF-8 and mirror Python text-mode newline normalization."""
    return data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


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
    coverage = parsed.meta.get("coverage")
    gate_passed = (
        coverage.get("gate_passed") if isinstance(coverage, dict) else None
    )
    blocking = parsed.meta.get("blocking_open_questions")
    blocking_clear = (
        isinstance(blocking, int)
        and not isinstance(blocking, bool)
        and blocking == 0
    )
    if gate_passed is not True or not blocking_clear:
        raise BriefInputError(
            f"discovery admission отказал для {path}: "
            "coverage.gate_passed должен быть true и "
            "blocking_open_questions — 0; "
            f"получено gate_passed={gate_passed!r}, "
            f"blocking_open_questions={blocking!r}"
        )
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
    primary_data = _read_bytes(path)
    primary_text = _decode(primary_data)
    primary = _gate(path, primary_text)
    interview = primary.meta.get("interview") or {}
    frame = interview.get("frame") if isinstance(interview, dict) else None
    primary_blob = blob_sha1_bytes(primary_data)
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
    customer_data = _read_bytes(customer_path)
    customer_text = _decode(customer_data)
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
            ("discovery-customer", blob_sha1_bytes(customer_data)),
        ),
    )


def _current_blobs(source: BriefSource) -> dict[str, str]:
    found = {
        "discovery-brief": blob_sha1_bytes(_read_bytes(source.primary_input))
    }
    if source.frame == "engineer":
        found["discovery-customer"] = blob_sha1_bytes(
            _read_bytes(source.requirements_input)
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


_REQ_HEAD_RE = re.compile(r"^####\s+((?:FR|NFR)-\d+[a-z]?):", re.M)
_REQ_NEAR_RE = re.compile(r"^#{2,6}\s+((?:FR|NFR)-[^\s:]*)", re.M)
_SECTION_RE = re.compile(r"^#{1,3}\s", re.M)
_PRIORITY_RE = re.compile(r"^\*\*Priority\*\*:\s*(\S+)", re.M)


def _downstream_requirements(
    text: str,
) -> tuple[dict[str, list[str | None]], list[str]]:
    """Strict requirements blocks plus explicit near-miss findings."""
    findings: list[str] = []
    strict_starts = {match.start() for match in _REQ_HEAD_RE.finditer(text)}
    for near in _REQ_NEAR_RE.finditer(text):
        if near.start() not in strict_starts:
            findings.append(
                f"{near.group(1)}: заголовок requirements не соответствует "
                "машинной грамматике `#### FR-NN:`/`#### NFR-NN:`"
            )
    heads = list(_REQ_HEAD_RE.finditer(text))
    parsed: dict[str, list[str | None]] = {}
    for index, head in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(text)
        block = text[head.end():end]
        section = _SECTION_RE.search(block)
        if section is not None:
            block = block[:section.start()]
        priority = _PRIORITY_RE.search(block)
        parsed.setdefault(head.group(1), []).append(
            priority.group(1) if priority is not None else None
        )
    return parsed, findings


def requirements_findings(source_text: str, requirements_text: str) -> list[str]:
    """Check exact FR/NFR carry from discovery source to requirements.

    This is deliberately syntactic, not an LLM semantic judgment. The source
    IDs define the coverage set; downstream prose and additional requirements
    remain outside this guard.
    """
    source = gate_check.parse_brief(source_text)
    if source is None:
        return ["discovery source не разбирается как brief"]
    source_entries = [
        entry for entry in source.entries if entry.prefix in ("FR", "NFR")
    ]
    source_counts: dict[str, int] = {}
    for entry in source_entries:
        source_counts[entry.eid] = source_counts.get(entry.eid, 0) + 1

    downstream, findings = _downstream_requirements(requirements_text)
    for source_id, count in sorted(source_counts.items()):
        if count != 1:
            findings.append(
                f"{source_id}: discovery source объявляет id {count} раза "
                "(ожидается ровно один)"
            )
            continue
        occurrences = downstream.get(source_id, [])
        if len(occurrences) != 1:
            findings.append(
                f"{source_id}: в requirements найдено {len(occurrences)} "
                "объявлений (ожидается ровно одно)"
            )

    must_ids = {
        entry.eid
        for entry in source_entries
        if entry.prefix == "FR" and entry.priority() == "Must"
    }
    for source_id in sorted(must_ids):
        priorities = downstream.get(source_id, [])
        if len(priorities) == 1 and priorities[0] != "Must":
            rendered = priorities[0] if priorities[0] is not None else "отсутствует"
            findings.append(
                f"{source_id}: source Priority Must понижен; downstream "
                f"Priority={rendered!r}"
            )
    return findings
