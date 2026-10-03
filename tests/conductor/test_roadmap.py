from conductor.roadmap import parse_roadmap

EPICS = {
    "eco.dark-factory": {"status": "active"},
    "eco.governance-plane": {"status": "active"},
    "eco.research-bench": {"status": "active"},
    "eco.cadence": {"status": "paused"},
    "airun.kapelle-m3": {"status": "done"},
}

GOOD = """
schema_version = 1
updated = "2026-09-29"
autonomy = 0
writer_host = "vps-conductor"
writer_since = "2026-09-29T12:00:00Z"

[[focus]]
epic = "eco.dark-factory"
goal = "todo://devtools/bundle-docs-as-oracle"

[[focus]]
epic = "eco.governance-plane"
autonomy = 1
pull_prerequisites = true

[parked]
epics = ["eco.research-bench"]
"""


def _codes(text: str | None) -> set[str]:
    return {f.code for f in parse_roadmap(text, EPICS).findings}


def test_good_roadmap() -> None:
    rm = parse_roadmap(GOOD, EPICS)
    assert rm.valid and not [f for f in rm.findings if f.severity == "error"]
    assert [f.epic for f in rm.focus] == ["eco.dark-factory", "eco.governance-plane"]
    assert rm.focus[0].rank == 1 and rm.focus[0].autonomy == 0
    assert rm.focus[1].autonomy == 1 and rm.focus[1].pull_prerequisites
    assert rm.klass("eco.dark-factory") == "focus"
    assert rm.klass("eco.research-bench") == "parked"
    assert rm.klass("eco.cadence") == "background"
    assert rm.klass(None) == "background"
    assert rm.limits["max_writes_per_run"] == 20


def test_invalid_cases() -> None:
    cases = [
        GOOD.replace('epic = "eco.governance-plane"', 'epic = "eco.nope"'),
        GOOD.replace('"eco.research-bench"', '"eco.dark-factory"'),
        GOOD.replace(
            '"eco.research-bench"]', '"eco.research-bench", "eco.research-bench"]'
        ),
        GOOD.replace('epic = "eco.governance-plane"', 'epic = "airun.kapelle-m3"'),
        GOOD.replace("todo://devtools/bundle-docs-as-oracle", "devtools#1"),
        GOOD.replace("autonomy = 0", "autonomy = 7"),
        GOOD + "\n[limits]\nmax_writes_per_run = 0\n",
        GOOD.replace('"2026-09-29T12:00:00Z"', '"вчера"'),
        GOOD.replace('[parked]\nepics = ["eco.research-bench"]', "").replace(
            'writer_since = "2026-09-29T12:00:00Z"',
            'writer_since = "2026-09-29T12:00:00Z"\nparked = 5',
        ),
        GOOD.replace('[[focus]]\nepic = "eco.dark-factory"', "focus = 3\n[x]"),
        GOOD.replace('epic = "eco.dark-factory"', 'epic = ["eco.dark-factory"]'),
        GOOD.replace('epic = "eco.dark-factory"', "epic = { x = 1 }"),
        "not = [toml",
        None,
    ]
    for text in cases:
        assert "RM-INVALID" in _codes(text), text


def test_paused_focus_is_warning() -> None:
    rm = parse_roadmap(
        GOOD.replace('epic = "eco.governance-plane"', 'epic = "eco.cadence"'), EPICS
    )
    assert rm.valid and "RM-FOCUS-PAUSED" in {f.code for f in rm.findings}


# долг среза 0 (devtools#511, п. 7)

SINCE = 'writer_since = "2026-09-29T12:00:00Z"'


def test_unquoted_toml_datetime_with_zone_is_accepted_as_utc() -> None:
    rm = parse_roadmap(
        GOOD.replace(SINCE, "writer_since = 2026-09-29T12:00:00Z"), EPICS
    )
    assert rm.valid and rm.writer_since == "2026-09-29T12:00:00Z"
    offset = GOOD.replace(SINCE, "writer_since = 2026-09-29T15:00:00+03:00")
    assert parse_roadmap(offset, EPICS).writer_since == "2026-09-29T12:00:00Z"


def test_unquoted_datetime_without_zone_is_invalid() -> None:
    for raw in ("2026-09-29T12:00:00", "2026-09-29"):
        assert "RM-INVALID" in _codes(GOOD.replace(SINCE, f"writer_since = {raw}"))


def test_writer_since_must_be_strict_rfc3339_utc() -> None:
    for bad in (
        "20261001T000000Z",
        "2026-10-01T00Z",
        "2026-10-01 00:00:00Z",
        "2026-W40-1T00:00Z",
        "2026-10-01T00:00Z",
    ):
        text = GOOD.replace(SINCE, f'writer_since = "{bad}"')
        assert "RM-INVALID" in _codes(text), bad
    ok = GOOD.replace(SINCE, 'writer_since = "2026-10-01T00:00:00.5Z"')
    assert parse_roadmap(ok, EPICS).valid


def test_unknown_limit_name_is_invalid() -> None:
    rm = parse_roadmap(GOOD + "\n[limits]\nmax_writes_per_runn = 5\n", EPICS)
    assert not rm.valid
    assert any("max_writes_per_runn" in f.detail for f in rm.findings)
