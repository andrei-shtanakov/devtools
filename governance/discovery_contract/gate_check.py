"""gate_check — линтер discovery-brief по контракту DISCOVERY-BRIEF-CONTRACT.md (v1.1).

Правила GC-01…GC-16 — нормативный реестр в контракте §5. Каждое finding несёт ID
правила; выход 1, если есть хотя бы одна ошибка.

Запуск: uv run gate_check.py <brief.md> [<brief2.md> ...]
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

FRAMES = {
    "customer": {
        "required": {
            "goals": "G",
            "personas": "P",
            "jobs": "J",
            "functions": "FR",
            "nfr": "NFR",
            "constraints": "CON",
            "success_metrics": "M",
            "out_of_scope": "OUT",
        },
        "optional": {"risks": "RK"},
        "feeds": ["charter", "requirements"],
    },
    "engineer": {
        "required": {
            "systems": "S",
            "interfaces": "IF",
            "constraints": "CON",
            "arch_preferences": "AP",
            "risks": "RK",
            "feasibility_review": None,  # процесс, не секция: см. GC-05 (upstream Must-FR)
        },
        "optional": {},
        "feeds": ["system-assessment", "tech-selection"],
    },
}

COVERAGE_VALUES = {"covered", "partial", "missing"}
PRIORITY_RE = re.compile(r"\b(Must|Should|Could|Won't)\b", re.IGNORECASE)

# Запись тела: буллет `- **FR-01** …` или заголовок `#### FR-01: …`
_DEF_RE = re.compile(r"^(?:-\s+\*\*|#{2,6}\s+)([A-Z]+-\d+)")
_HEADING_RE = re.compile(r"^#{1,6}\s")
_ID_LIST_RE = re.compile(r"[A-Z]+-\d+")


@dataclass
class Entry:
    """Одна запись тела брифа (G-01, FR-02, …) со строками её блока."""

    eid: str
    lines: list[str] = field(default_factory=list)

    @property
    def prefix(self) -> str:
        return self.eid.rsplit("-", 1)[0]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def traces(self) -> list[str]:
        m = re.search(r"traces:\s*\[([^\]]*)\]", self.text)
        return _ID_LIST_RE.findall(m.group(1)) if m else []

    def has_priority(self) -> bool:
        m = re.search(r"\*\*Priority\*\*:\s*(.+)", self.text)
        return bool(m and PRIORITY_RE.search(m.group(1)))

    def priority(self) -> str | None:
        m = re.search(r"\*\*Priority\*\*:\s*(.+)", self.text)
        if m:
            p = PRIORITY_RE.search(m.group(1))
            return p.group(1).capitalize() if p else None
        return None

    def has_acceptance(self) -> bool:
        return bool(re.search(r"\*\*(Acceptance[\w ]*|Target)\*\*:", self.text))

    def owner_role(self) -> str | None:
        m = re.search(r"owner_role:\s*([\w-]+)", self.text)
        return m.group(1) if m else None

    def blocking(self) -> bool | None:
        m = re.search(r"blocking:\s*(true|false)", self.text)
        return None if m is None else m.group(1) == "true"

    def resolved(self) -> bool:
        return bool(re.search(r"resolved:\s*true", self.text))

    def status(self) -> str | None:
        m = re.search(r"(?<![\w*])status:\s*(\w+)", self.text)
        return m.group(1) if m else None


@dataclass
class Finding:
    rule: str
    level: str  # error | warning
    ref: str
    message: str

    def __str__(self) -> str:
        return f"{self.rule} {self.level} [{self.ref}] {self.message}"


@dataclass
class Brief:
    meta: dict
    entries: list[Entry]

    def by_prefix(self, prefix: str) -> list[Entry]:
        return [e for e in self.entries if e.prefix == prefix]

    def ids(self) -> set[str]:
        return {e.eid for e in self.entries}


def split_frontmatter(text: str) -> tuple[dict | None, str]:
    """Как spec-runner spec.py: ведущий ``---`` YAML-блок → (dict | None, body)."""
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---", 4)
    if end == -1:
        return None, text
    try:
        loaded = yaml.safe_load(text[4:end])
    except yaml.YAMLError:
        return None, text
    if not isinstance(loaded, dict):
        return None, text
    after = text.find("\n", end + 1)
    return loaded, (text[after + 1 :] if after != -1 else "")


def parse_body(body: str) -> list[Entry]:
    entries: list[Entry] = []
    current: Entry | None = None
    for line in body.splitlines():
        m = _DEF_RE.match(line)
        if m:
            current = Entry(eid=m.group(1), lines=[line])
            entries.append(current)
            continue
        if _HEADING_RE.match(line):
            current = None
            continue
        if current is not None:
            current.lines.append(line)
    return entries


def parse_brief(text: str) -> Brief | None:
    meta, body = split_frontmatter(text)
    if meta is None:
        return None
    return Brief(meta=meta, entries=parse_body(body))


def _repo_root(base_dir: Path) -> Path | None:
    """Ближайший вверх по дереву каталог с `.git` (корень репо брифа)."""
    d = base_dir.resolve()
    while True:
        if (d / ".git").exists():
            return d
        if d.parent == d:
            return None
        d = d.parent


def _resolve_ref(ref: str, base_dir: Path) -> Path | None:
    """Разрешить путь-ссылку traces_to: сперва от брифа, затем от корня его репо (GC-16).

    Абсолютные пути отбрасываются, а результат обязан остаться внутри корня репо
    (или base_dir, если репо нет) — защита от path traversal на непроверенном контенте.
    """
    if Path(ref).is_absolute():
        return None
    root = _repo_root(base_dir)
    bound = root if root is not None else base_dir.resolve()
    for anchor in (base_dir, bound):
        cand = (anchor / ref).resolve()
        if cand.is_file() and cand.is_relative_to(bound):
            return cand
    return None


def check(text: str, base_dir: Path | None = None) -> list[Finding]:
    """Прогнать все правила GC-01…GC-16; вернуть findings (errors + warnings)."""
    findings: list[Finding] = []
    err = lambda rule, ref, msg: findings.append(Finding(rule, "error", ref, msg))
    warn = lambda rule, ref, msg: findings.append(Finding(rule, "warning", ref, msg))

    brief = parse_brief(text)
    if brief is None:
        return [Finding("GC-01", "error", "frontmatter", "frontmatter отсутствует или не парсится")]
    meta = brief.meta

    # GC-01 schema
    if meta.get("schema") != "discovery-brief":
        err("GC-01", "schema", f"schema={meta.get('schema')!r}, ожидается 'discovery-brief'")
    if meta.get("schema_version") != 1:
        err("GC-01", "schema_version", f"schema_version={meta.get('schema_version')!r}, ожидается 1")

    # GC-02 SpecMeta-ядро
    if meta.get("spec_stage") != "discovery":
        err("GC-02", "spec_stage", f"spec_stage={meta.get('spec_stage')!r}, ожидается 'discovery'")
    for key in ("status", "generated_by", "generated_at"):
        if not meta.get(key):
            err("GC-02", key, f"поле {key} пусто или отсутствует")

    # GC-03 interview
    interview = meta.get("interview") or {}
    frame_name = interview.get("frame")
    if frame_name not in FRAMES:
        err("GC-03", "interview.frame", f"frame={frame_name!r}, ожидается customer|engineer")
        return findings  # без фрейма дальнейшие правила неприменимы
    frame = FRAMES[frame_name]
    sessions = interview.get("sessions") or []
    if not sessions:
        err("GC-03", "interview.sessions", "sessions пуст")
    for i, s in enumerate(sessions):
        if not (isinstance(s, dict) and s.get("participant_role")):
            err("GC-03", f"interview.sessions[{i}]", "нет participant_role")

    # GC-04 coverage: required-ключи + допустимые значения
    coverage = meta.get("coverage") or {}
    for key in frame["required"]:
        if key not in coverage:
            err("GC-04", f"coverage.{key}", "required-ключ фрейма отсутствует")
    for key, value in coverage.items():
        if key == "gate_passed":
            continue
        if value not in COVERAGE_VALUES:
            err("GC-04", f"coverage.{key}", f"значение {value!r} вне {{covered|partial|missing}}")

    # GC-05 covered ⇒ секция непуста
    section_map = {**frame["required"], **frame["optional"]}
    for key, prefix in section_map.items():
        if prefix and coverage.get(key) == "covered" and not brief.by_prefix(prefix):
            err("GC-05", f"coverage.{key}", f"заявлено covered, но записей {prefix}-NN в теле нет")

    # traces_to нормализуем к списку: YAML-строка (traces_to: customer.md) — тоже валидна,
    # иначе итерация по символам молча отключила бы GC-12/GC-16
    raw_traces = meta.get("traces_to") or []
    if isinstance(raw_traces, str):
        raw_traces = [raw_traces]
    elif not isinstance(raw_traces, list):
        err("GC-01", "traces_to", f"ожидается список или строка, получено {type(raw_traces).__name__}")
        raw_traces = []

    # GC-16 путь-элементы traces_to разрешаются (от брифа или корня его репо)
    path_refs = [
        r for r in raw_traces
        if isinstance(r, str) and r.endswith(".md") and not r.startswith("[[")
    ]
    if base_dir is not None:
        for ref in path_refs:
            if _resolve_ref(ref, base_dir) is None:
                err("GC-16", ref, "путь из traces_to не разрешается ни от брифа, ни от корня репо")

    # GC-05 (engineer) feasibility_review: каждый Must-FR из upstream упомянут в теле
    upstream_meta: dict | None = None
    upstream_ref: str | None = None
    if frame_name == "engineer" and base_dir is not None:
        for ref in path_refs:
            path = _resolve_ref(ref, base_dir)
            if path is not None:
                upstream_ref = ref
                upstream_meta, upstream_body = split_frontmatter(path.read_text(encoding="utf-8"))
                if coverage.get("feasibility_review") == "covered" and upstream_meta is not None:
                    for entry in parse_body(upstream_body):
                        if entry.prefix == "FR" and entry.priority() == "Must" and entry.eid not in text:
                            err("GC-05", "coverage.feasibility_review",
                                f"Must-требование {entry.eid} из {ref} не получило feasibility-вердикта")
                break

    doc_ids = brief.ids()

    # GC-06 FR → существующий G/J
    fr_all_traced = True
    for entry in brief.by_prefix("FR"):
        targets = [t for t in entry.traces() if t.split("-")[0] in ("G", "J")]
        if not targets:
            fr_all_traced = False
            err("GC-06", entry.eid, "нет traces → G/J")
        else:
            for t in targets:
                if t not in doc_ids:
                    fr_all_traced = False
                    err("GC-06", entry.eid, f"traces → {t}, но такой записи нет")

    # GC-07 M → существующий G
    for entry in brief.by_prefix("M"):
        targets = [t for t in entry.traces() if t.startswith("G-")]
        if not targets:
            err("GC-07", entry.eid, "нет traces → G")
        else:
            for t in targets:
                if t not in doc_ids:
                    err("GC-07", entry.eid, f"traces → {t}, но такой записи нет")

    # GC-08 FR: Priority+Acceptance; NFR: Acceptance/Target
    for entry in brief.by_prefix("FR"):
        if not entry.has_priority():
            err("GC-08", entry.eid, "нет **Priority** (Must|Should|Could|Won't)")
        if not entry.has_acceptance():
            err("GC-08", entry.eid, "нет **Acceptance**")
    for entry in brief.by_prefix("NFR"):
        if not entry.has_acceptance():
            err("GC-08", entry.eid, "нет **Acceptance**/**Target**")

    # GC-09 Q: owner_role+blocking; X: status
    for entry in brief.by_prefix("Q"):
        if not entry.owner_role():
            err("GC-09", entry.eid, "нет owner_role")
        if entry.blocking() is None:
            err("GC-09", entry.eid, "нет blocking: true|false")
    for entry in brief.by_prefix("X"):
        if entry.status() not in ("open", "resolved"):
            err("GC-09", entry.eid, "нет status: open|resolved")

    # GC-10 счётчики
    open_q = [e for e in brief.by_prefix("Q") if not e.resolved()]
    blocking_q = [e for e in open_q if e.blocking() is True]
    open_x = [e for e in brief.by_prefix("X") if e.status() == "open"]
    for key, actual in (
        ("open_questions", len(open_q)),
        ("blocking_open_questions", len(blocking_q)),
        ("conflicts", len(open_x)),
    ):
        declared = meta.get(key)
        if declared != actual:
            err("GC-10", key, f"frontmatter={declared!r}, по телу={actual}")

    # GC-11 gate_passed = формула §4
    required_covered = all(
        coverage.get(key) == "covered"
        and (prefix is None or bool(brief.by_prefix(prefix)))
        for key, prefix in frame["required"].items()
    )
    computed = required_covered and fr_all_traced and len(blocking_q) == 0
    declared_gate = coverage.get("gate_passed")
    if declared_gate is not computed:
        err("GC-11", "coverage.gate_passed",
            f"frontmatter={declared_gate!r}, вычислено={computed} "
            f"(required_covered={required_covered}, fr_traced={fr_all_traced}, blocking={len(blocking_q)})")

    # GC-12 engineer: upstream approved-before-downstream
    if frame_name == "engineer":
        if not raw_traces:
            err("GC-12", "traces_to", "пуст: engineer-brief обязан ссылаться на customer-brief")
        elif upstream_ref is not None:
            if upstream_meta is None:
                err("GC-12", upstream_ref, "upstream-brief не парсится")
            elif upstream_meta.get("status") != "approved":
                err("GC-12", upstream_ref,
                    f"upstream status={upstream_meta.get('status')!r}, требуется 'approved'")
        elif path_refs:
            warn("GC-12", path_refs[0], "upstream-brief не резолвится — статус не проверен")

    # GC-13 engineer: IF → S; AP → S/CON
    if frame_name == "engineer":
        for entry in brief.by_prefix("IF"):
            targets = [t for t in entry.traces() if t.startswith("S-")]
            if not targets or any(t not in doc_ids for t in targets):
                err("GC-13", entry.eid, "нет traces → существующий S")
        for entry in brief.by_prefix("AP"):
            targets = [t for t in entry.traces() if t.split("-")[0] in ("S", "CON")]
            if not targets or any(t not in doc_ids for t in targets):
                err("GC-13", entry.eid, "нет traces → существующий S/CON")

    # GC-14 customer: solution-space протёк в problem-space
    if frame_name == "customer":
        for prefix in ("S", "IF", "AP"):
            for entry in brief.by_prefix(prefix):
                warn("GC-14", entry.eid, "solution-space запись в customer-брифе")

    # GC-15 validation зеркалит фактический результат линтера (считается последним)
    declared_validation = meta.get("validation")
    has_errors = any(f.level == "error" for f in findings)
    if has_errors and declared_validation == "pass":
        err("GC-15", "validation", "validation=pass при наличии ошибок линтера")
    elif not has_errors and declared_validation != "pass":
        err("GC-15", "validation",
            f"ошибок нет, но validation={declared_validation!r} — протухшее зеркало (ожидается 'pass')")

    return findings


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    exit_code = 0
    for arg in argv:
        path = Path(arg)
        findings = check(path.read_text(encoding="utf-8"), base_dir=path.parent)
        errors = [f for f in findings if f.level == "error"]
        for f in findings:
            print(f"{path.name}: {f}")
        verdict = "FAIL" if errors else "PASS"
        print(f"{path.name}: {verdict} ({len(errors)} errors, {len(findings) - len(errors)} warnings)")
        if errors:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
