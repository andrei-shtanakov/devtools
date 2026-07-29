"""Characterization: the migrated (package-based) checker vs the old oracle.

On a frozen synthetic fleet covering every outcome class, the shared
``check_legacy_fleet`` must flag the SAME legacy relations the pre-package
devtools resolver did, the canonical Maestro→proctor edge must resolve exactly
once, and multiple blockers on one item must all survive.
"""

from __future__ import annotations

from plan_fields import ManifestIndex, RepoInput, check_legacy_fleet, parse_fleet
from tests.legacy_oracle import flagged as oracle_flagged

# --- a frozen fleet: one text per repo (None = cloned, no TODO) ---------------
MAESTRO = (
    "- [ ] **R-03b** routing @owner:o @id:r-03b\n"  # canonical pilot target (open)
    "- [x] done thing shipped @owner:o\n"  # closed target -> stale
    "- [ ] alive thing @owner:o\n"  # open target -> valid, not flagged
)
PROCTOR = (
    "- [ ] pilot @owner:o @blocked_by:todo://maestro/r-03b @id:pilot\n"  # canonical
    "- [ ] leg1 @owner:o @blocked_by:maestro#done\n"  # stale
    "- [ ] leg2 @owner:o @blocked_by:maestro#missingslug\n"  # dangling
    "- [ ] leg3 @owner:o @blocked_by:maestro#alive\n"  # valid (open) -> not flagged
    "- [ ] multi @owner:o @blocked_by:maestro#done @blocked_by:maestro#missingslug\n"
    "- [ ] leg4 @owner:o @blocked_by:emptyrepo#x\n"  # no-todo
    "- [ ] leg5 @owner:o @blocked_by:ghost#y\n"  # not in manifest -> repo-unknown
    "- [ ] leg6 @owner:o @blocked_by:absentrepo#z\n"  # manifest, no checkout
)
FLEET = {"maestro": MAESTRO, "proctor": PROCTOR, "emptyrepo": None}
# The fleet API takes a ManifestIndex, not a bare name set: identity is the
# manifest's to declare. No entry here declares a `git_dir` alias, so the index
# is exactly the old set of keys and every outcome below is unchanged.
MANIFEST = ManifestIndex(
    frozenset({"maestro", "proctor", "emptyrepo", "absentrepo"}), {}
)

INPUTS = [
    RepoInput("maestro", MAESTRO),
    RepoInput("proctor", PROCTOR),
    RepoInput("emptyrepo", todo_text=None, available=True),
]

# old outcome vocabulary -> the package code(s) that refine it
_CLASS = {
    "dangling": {"PF-BLOCKER-DANGLING"},
    "stale": {"PF-BLOCKER-STALE"},
    "no-todo": {"PF-BLOCKER-NO-TODO"},
    "not-cloned": {"PF-BLOCKER-UNRESOLVABLE", "PF-BLOCKER-REPO-UNKNOWN"},
}


def _new():
    return check_legacy_fleet(INPUTS, MANIFEST)


def test_new_checker_flags_the_same_relations_as_the_old_oracle() -> None:
    old = oracle_flagged(FLEET)
    old_rel = {(s, ln, t, slug) for (s, ln, t, slug, _) in old}
    new_rel = {(d.source_repo, d.source_line, d.target_repo, d.slug) for d in _new()}
    assert new_rel == old_rel


def test_each_relation_keeps_a_compatible_outcome_class() -> None:
    new_by_rel = {
        (d.source_repo, d.source_line, d.target_repo, d.slug): d.code for d in _new()
    }
    for s, ln, t, slug, outcome in oracle_flagged(FLEET):
        assert new_by_rel[(s, ln, t, slug)] in _CLASS[outcome]


def test_multiple_blockers_on_one_item_all_survive() -> None:
    # both blockers on the 'multi' line (proctor line 5) must be kept, with their
    # distinct outcomes — neither dropped or collapsed.
    multi = {
        (d.slug, d.code)
        for d in _new()
        if d.source_repo == "proctor" and d.source_line == 5
    }
    assert multi == {("done", "PF-BLOCKER-STALE"), ("missingslug", "PF-BLOCKER-DANGLING")}


def test_canonical_maestro_proctor_edge_resolves_exactly_once() -> None:
    snap = parse_fleet(INPUTS, MANIFEST)
    edges = [
        (e["source_node_id"], e["target_node_id"])
        for e in snap["edges"]
        if e["target_node_id"] == "todo://maestro/r-03b"
    ]
    assert edges == [("todo://proctor/pilot", "todo://maestro/r-03b")]


def test_legacy_and_canonical_never_double_count_the_pilot() -> None:
    # the @id'd pilot relation is canonical only — it must not also appear legacy
    legacy_targets = {(d.target_repo, d.slug) for d in _new()}
    assert ("maestro", "r-03b") not in legacy_targets
