"""S2 Task 3 — vendored-in role from declarations, fail-closed (§9.7)."""

from __future__ import annotations

import pytest

from selfcheck.roles import role_of
from selfcheck.vendor import (
    DeclarationError,
    is_candidate,
    parse_declaration,
    vendor_roles,
)

H = "a" * 64
FORMAT_A = (
    "# SOURCE: steward @ 5bfd829 (master, 2026-09-21; the header tail\n"
    "# continues on the next comment line)\n"
    "# re-vendor: copy files from steward\n"
    f"{H}  scripts/review/local.sh\n"
    f"{H}  .github/codex/review-schema.json\n"
)
FORMAT_B = (
    f"{H}  approval-policy.yaml  steward@6a70d15ba586b8c17b41d33705477a42cf8ebfa5\n"
)
FORMAT_C = (
    "upstream: git@github.com:andrei-shtanakov/discovery-toolkit.git\n"
    "commit: ee93092fdfe6195c28c7392d85b41c6b94b9fe0a\n\n"
    f"DISCOVERY-BRIEF-CONTRACT.md {H}\n"
    f"gate_check.py {H}\n"
)
FORMAT_D = (
    "# VENDORED: devtools @ 8cd6456 — contracts/review-scope/v1/prose-paths.env\n"
    "# SSOT there; pinned copy here.\n"
    "a/**\n"
)


@pytest.mark.parametrize(
    ("rel", "text", "fmt", "owner", "ref", "members"),
    [
        (
            "scripts/review/PIN",
            FORMAT_A,
            "A",
            "steward",
            "5bfd829",
            ("scripts/review/local.sh", ".github/codex/review-schema.json"),
        ),
        (
            "contracts/p/v1/PIN",
            FORMAT_B,
            "B",
            "steward",
            "6a70d15ba586b8c17b41d33705477a42cf8ebfa5",
            ("contracts/p/v1/approval-policy.yaml",),
        ),
        (
            "governance/dc/PINNED.txt",
            FORMAT_C,
            "C",
            "discovery-toolkit",
            "ee93092fdfe6195c28c7392d85b41c6b94b9fe0a",
            (
                "governance/dc/DISCOVERY-BRIEF-CONTRACT.md",
                "governance/dc/gate_check.py",
            ),
        ),
        (
            "scripts/review/prose-paths.env",
            FORMAT_D,
            "D",
            "devtools",
            "8cd6456",
            ("scripts/review/prose-paths.env",),
        ),
    ],
)
def test_four_formats(rel, text, fmt, owner, ref, members) -> None:
    decl = parse_declaration(rel, text)
    assert (decl.path, decl.fmt, decl.owner, decl.ref, decl.members) == (
        rel,
        fmt,
        owner,
        ref,
        members,
    )


@pytest.mark.parametrize(
    ("rel", "text", "is_node", "expected"),
    [
        ("scripts/review/PIN", FORMAT_A, False, True),
        ("x/PINNED.txt", FORMAT_C, False, True),
        ("x/vendor.lock", "whatever\n", False, True),
        ("tools/vendor_manifest.py", "import os\n", True, False),  # code by name
        ("a.yml", "upstream: x\nname: y\n", False, False),  # no commit:
        ("a.yml", "upstream: x\ncommit: main\n", False, True),  # grammar decides
        ("a.py", "# SOURCE: https://example.com/snippet\n", True, False),  # no @
        ("a.sh", "#!/bin/sh\n# SOURCE: steward @ v1.2\n", True, True),
        ("a.sh", "#!/bin/sh\n# VENDORED: owner@abc\n", True, True),
        ("a.sh", "l1\nl2\nl3\nl4\nl5\n# VENDORED: x @ abcdef1 — p\n", True, False),
    ],
)
def test_candidates(rel, text, is_node, expected) -> None:
    assert is_candidate(rel, text, is_node=is_node) is expected


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        ("a.sh", "# VENDORED: x @ v1.2 — p\n"),  # tag instead of sha
        ("x/PINNED.txt", "upstream: u/r.git\ncommit: main\n\np " + H + "\n"),
        ("x/PIN", FORMAT_A + FORMAT_B),  # mixed formats
        ("x/PIN", "# SOURCE: steward @ 5bfd829\n"),  # no members
        ("x/PIN", f"{H}  ../escape.sh  steward@5bfd829\n"),  # '..'
        ("x/PIN", f"# SOURCE: s @ 5bfd829\n{H}  /abs/file.sh\n"),  # absolute
        ("x/PIN", "# SOURCE: s @ 5bfd829\nnot a member line\n"),
    ],
)
def test_unparsed(rel: str, text: str) -> None:
    with pytest.raises(DeclarationError):
        parse_declaration(rel, text)


def _texts() -> dict[str, str]:
    return {
        "scripts/review/PIN": FORMAT_A + f"{H}  scripts/review/prose-paths.env\n",
        "scripts/review/local.sh": "#!/bin/sh\n",
        "scripts/review/prose-paths.env": FORMAT_D,
        "scripts/review/extra.sh": "#!/bin/sh\n",
        "tests/fixtures/PIN": "garbage\n",  # role test: never a candidate
    }


def test_vendor_roles_members_and_double_declaration() -> None:
    texts = _texts()
    res = vendor_roles("devtools", sorted(texts), texts, role_of, frozenset())
    assert set(res.members) == {
        "scripts/review/local.sh",
        "scripts/review/prose-paths.env",
    }
    owners = sorted(d.owner for d in res.members["scripts/review/prose-paths.env"])
    assert owners == ["devtools", "steward"]  # listed twice, not an error
    assert "scripts/review/extra.sh" not in res.members  # ordinary analysis
    dangling = [f for f in res.findings if f.rule == "selfcheck/vendor-pin-dangling"]
    assert [(f.anchor, f.text_key, f.severity) for f in dangling] == [
        ("file:scripts/review/PIN", ".github/codex/review-schema.json", "medium")
    ]
    assert res.broken is True  # any vendor-pin-* finding
    assert not [f for f in res.findings if f.rule == "selfcheck/vendor-pin-unparsed"]


def test_unparsed_candidate_fails_closed() -> None:
    texts = {
        "kit/PIN": (
            f"# SOURCE: s @ 5bfd829\n{H}  kit/a.sh\nbroken line kit/b.sh\n"
            "also d.sh\n"  # named relative to the declaration's directory
        ),
        "kit/a.sh": "#!/bin/sh\n",
        "kit/b.sh": "#!/bin/sh\n",
        "kit/d.sh": "#!/bin/sh\n",
        "kit/c.sh": "#!/bin/sh\n",
        "lib.sh": "#!/bin/sh\n# VENDORED: owner@abc\n",
    }
    res = vendor_roles("r", sorted(texts), texts, role_of, frozenset({"lib.sh"}))
    unparsed = sorted(
        f.anchor for f in res.findings if f.rule == "selfcheck/vendor-pin-unparsed"
    )
    assert unparsed == ["file:kit/PIN", "file:lib.sh"]
    assert all(
        f.severity == "high" and f.text_key is None
        for f in res.findings
        if f.rule == "selfcheck/vendor-pin-unparsed"
    )
    # named paths of any line + every candidate itself are protected
    assert {"kit/a.sh", "kit/b.sh", "kit/d.sh", "kit/PIN", "lib.sh"} <= res.protected
    assert "kit/c.sh" not in res.protected  # P6 covers it instead
    assert res.members == {} and res.broken is True


def test_d_header_mixed_with_member_lines_is_unparsed() -> None:
    """Final review I1: a PIN with a valid `# VENDORED:` header and A-member
    lines must not parse as D (its members would be silently dropped)."""
    with pytest.raises(DeclarationError):
        parse_declaration("kit/PIN", f"# VENDORED: x @ abcdef1 — p\n{H}  kit/a.sh\n")
    with pytest.raises(DeclarationError):
        parse_declaration(
            "kit/PIN", "# VENDORED: x @ abcdef1 — p\n# SOURCE: s @ 5bfd829\n"
        )
