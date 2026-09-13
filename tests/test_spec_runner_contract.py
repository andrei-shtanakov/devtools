from pathlib import Path

import pytest

from governance.spec_runner_contract import (
    SELECTOR_POLICIES,
    is_file_target_form,
    target_selector_policy,
)


def test_vendored_adapter_capabilities() -> None:
    assert SELECTOR_POLICIES["pytest"].supports_file_targets is True
    assert SELECTOR_POLICIES["exunit"].supports_file_targets is False
    assert SELECTOR_POLICIES["exunit"].node_id_hint == "path:line"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("tests/test_a.py", True),
        ("tests/test_a.py::test_a", False),
        ("test/a_test.exs:12", False),
        ("tests/test_*.py", False),
        ("-k test_a", False),
        ("", False),
    ],
)
def test_file_target_form_matches_declared_class(raw: str, expected: bool) -> None:
    assert is_file_target_form(raw) is expected


def test_target_policy_reads_flat_and_legacy_config(tmp_path: Path) -> None:
    (tmp_path / "spec-runner.config.yaml").write_text(
        "tdd_runner: exunit\n", encoding="utf-8"
    )
    assert target_selector_policy(tmp_path) == SELECTOR_POLICIES["exunit"]

    (tmp_path / "spec-runner.config.yaml").unlink()
    legacy = tmp_path / "spec" / "executor.config.yaml"
    legacy.parent.mkdir()
    legacy.write_text("executor:\n  tdd_runner: pytest\n", encoding="utf-8")
    assert target_selector_policy(tmp_path) == SELECTOR_POLICIES["pytest"]


def test_absent_or_inferred_runner_leaves_resolution_to_spec_runner(
    tmp_path: Path,
) -> None:
    assert target_selector_policy(tmp_path) is None
    (tmp_path / "spec-runner.config.yaml").write_text(
        "commands:\n  test: pytest\n", encoding="utf-8"
    )
    assert target_selector_policy(tmp_path) is None


def test_broken_or_unknown_config_refuses_fail_closed(tmp_path: Path) -> None:
    config = tmp_path / "spec-runner.config.yaml"
    config.write_text("tdd_runner: [", encoding="utf-8")
    with pytest.raises(ValueError, match="не читается"):
        target_selector_policy(tmp_path)

    config.write_text("tdd_runner: future-runner\n", encoding="utf-8")
    with pytest.raises(ValueError, match="вендоренном словаре"):
        target_selector_policy(tmp_path)
