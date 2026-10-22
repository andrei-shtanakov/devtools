"""Unit tests of df.sources: the canonical identity rules (spec 3.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from df.digest import TreeError
from df.sources import (
    SourceId,
    UnsupportedLock,
    canonical_repo_url,
    source_from_direct_url,
    source_from_lock,
)

R = Path("/var/df-build/tmp/id/src/devtools")
COMMIT = "f7f2cbcbbffc8d83870848317228c0a3487780ec"
OTHER = "0" * 40
DISPATCHER = "https://github.com/andrei-shtanakov/dispatcher"
PLAN_FIELDS_LOCK = {
    "git": f"{DISPATCHER}?subdirectory=packages%2Fplan-fields&rev={COMMIT}#{COMMIT}"
}
PLAN_FIELDS_DU = {
    "url": DISPATCHER,
    "subdirectory": "packages/plan-fields",
    "vcs_info": {"vcs": "git", "commit_id": COMMIT},
}
ROOT_DU = {"url": f"file://{R}", "dir_info": {}}


def test_plan_fields_lock_and_direct_url_equal() -> None:
    assert source_from_lock(PLAN_FIELDS_LOCK, root_dir=R) == source_from_direct_url(
        PLAN_FIELDS_DU
    )


def test_git_without_subdirectory_equal() -> None:
    url = "https://github.com/andrei-shtanakov/steward"
    lock = {"git": f"{url}.git?rev={COMMIT}#{COMMIT}"}
    du = {
        "url": "https://GitHub.com/andrei-shtanakov/steward/",
        "vcs_info": {"vcs": "git", "commit_id": COMMIT, "requested_revision": "x"},
    }
    got = source_from_lock(lock, root_dir=R)
    assert got == source_from_direct_url(du)
    assert got == SourceId("git", url=url, commit=COMMIT)


@pytest.mark.parametrize("lock", [{"editable": "."}, {"directory": "."}])
def test_root_directory_equal_with_editable_absent(lock: dict[str, str]) -> None:
    got = source_from_lock(lock, root_dir=R)
    assert got == source_from_direct_url(ROOT_DU)
    assert got == SourceId("directory", path=str(R), editable=False)


def test_directory_url_is_percent_decoded() -> None:
    du = {"url": "file://localhost/a%20b/src", "dir_info": {"editable": False}}
    assert source_from_direct_url(du).path == "/a b/src"


def test_missing_direct_url_is_registry_and_lock_registry_keeps_index() -> None:
    assert source_from_direct_url(None) == SourceId("registry")
    lock = {"registry": "https://pypi.org/simple"}
    assert source_from_lock(lock, root_dir=R).url == "https://pypi.org/simple"


def _change(change: str) -> tuple[SourceId | Exception, SourceId | Exception]:
    lock, du = dict(PLAN_FIELDS_LOCK), {**PLAN_FIELDS_DU}
    vcs = {"vcs": "git", "commit_id": COMMIT}
    root = ROOT_DU
    match change:
        case "url":
            du["url"] = "https://github.com/andrei-shtanakov/other"
        case "subdirectory":
            du["subdirectory"] = "packages/other"
        case "commit":
            du["vcs_info"] = {**vcs, "commit_id": OTHER}
        case "path":
            lock, du = {"editable": "."}, {"url": "file:///x", "dir_info": {}}
        case "editable-true":
            lock, du = {"editable": "."}, {**root, "dir_info": {"editable": True}}
        case "unknown-query-key":
            lock = {"git": f"{DISPATCHER}?foo=1#{COMMIT}"}
        case "short-commit":
            lock = {"git": f"{DISPATCHER}#{COMMIT[:12]}"}
        case "file-url-with-host":
            lock, du = {"editable": "."}, {"url": f"file://host{R}", "dir_info": {}}
        case "non-root-directory":
            lock, du = {"directory": "packages/x"}, root
        case _:
            raise AssertionError(change)
    try:
        left: SourceId | Exception = source_from_lock(lock, root_dir=R)
    except (UnsupportedLock, TreeError) as exc:
        left = exc
    try:
        right: SourceId | Exception = source_from_direct_url(du)
    except TreeError as exc:
        right = exc
    return left, right


@pytest.mark.parametrize(
    "change",
    [
        "url",
        "subdirectory",
        "commit",
        "path",
        "editable-true",
        "unknown-query-key",
        "short-commit",
        "file-url-with-host",
        "non-root-directory",
    ],
)
def test_source_negative_twins(change: str) -> None:
    left, right = _change(change)
    refused = isinstance(left, Exception) or isinstance(right, Exception)
    assert refused or left != right


@pytest.mark.parametrize(
    "query", ["subdirectory=a&subdirectory=b", "rev=1&rev=2", "rev", "a=1"]
)
def test_lock_query_repeats_and_malformed_are_unsupported(query: str) -> None:
    with pytest.raises(UnsupportedLock):
        source_from_lock({"git": f"{DISPATCHER}?{query}#{COMMIT}"}, root_dir=R)


@pytest.mark.parametrize(
    "du",
    [
        {"url": "https://x/y", "archive_info": {}},
        {"url": "https://x/y", "vcs_info": {"vcs": "hg", "commit_id": COMMIT}},
        {"url": "https://github.com/a/b", "vcs_info": {"vcs": "git"}},
        {"url": "file:///a/../b", "dir_info": {}},
        {"url": "file:///a//b", "dir_info": {}},
        {"url": "file:///a/./b", "dir_info": {}},
        {"url": "file:///a/%2e%2e/b", "dir_info": {}},
        {"url": "https://x/y"},
    ],
)
def test_direct_url_refusals(du: dict[str, object]) -> None:
    with pytest.raises(TreeError):
        source_from_direct_url(du)


@pytest.mark.parametrize(
    "url",
    ["http://github.com/a/b", "git@github.com:a/b", "https://u:p@github.com/a/b"],
)
def test_canonical_repo_url_refuses(url: str) -> None:
    with pytest.raises(TreeError):
        canonical_repo_url(url)


def test_canonical_repo_url_strips() -> None:
    got = canonical_repo_url("https://GitHub.com/A/b.git/?x=1#f")
    assert got == "https://github.com/A/b"
