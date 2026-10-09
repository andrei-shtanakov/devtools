"""Заявка на одобрение discovery-брифа: один строгий разбор (§11.3 п.3a)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from governance import approval_request as ar
from tests.approval_request_cases import DEFECTS, GOOD


def test_render_parse_roundtrip_is_exact() -> None:
    text = ar.render(GOOD)
    assert ar.parse(text) == GOOD
    assert ar.render(ar.parse(text)) == text


def test_render_key_order_is_fixed() -> None:
    keys = [
        ln.split(":")[0]
        for ln in ar.render(GOOD).splitlines()
        if not ln.startswith(" ")
    ]
    assert keys == [
        "schema",
        "purpose",
        "brief",
        "brief_self_hash",
        "policy",
        "run_id",
        "ws_id",
    ]


@pytest.mark.parametrize(
    ("case", "mutate", "match"), DEFECTS, ids=[d[0] for d in DEFECTS]
)
def test_each_defect_refuses_twin_passes(case, mutate, match) -> None:
    good = ar.render(GOOD)
    assert ar.parse(good) == GOOD
    with pytest.raises(ar.RequestError, match=match):
        ar.parse(mutate(good))


def test_cli_check_codes(tmp_path) -> None:
    good = tmp_path / "ok.yaml"
    good.write_text(ar.render(GOOD), encoding="utf-8")
    bad = tmp_path / "bad.yaml"
    bad.write_text(ar.render(GOOD) + "1: x\n", encoding="utf-8")
    run = [sys.executable, "-m", "governance.approval_request", "check"]
    assert subprocess.run([*run, str(good)], check=False).returncode == 0
    done = subprocess.run([*run, str(bad)], check=False, capture_output=True, text=True)
    assert done.returncode == 3 and "отклонена" in done.stderr


@pytest.mark.parametrize(
    "value",
    ["123", "yes", "null", "true", "1e3", "~", "a: b", "it's", 'say "hi"', "кириллица"],
)
def test_yaml_like_ids_roundtrip_as_strings(value) -> None:  # ревью части A, A5
    from dataclasses import replace

    req = replace(GOOD, run_id=value, ws_id=value)
    assert ar.parse(ar.render(req)) == req


@pytest.mark.parametrize("newline", ["\r\n", "\r"], ids=["crlf", "cr"])
def test_cli_refuses_cr_at_file_boundary(tmp_path, newline) -> None:  # A6
    path = tmp_path / "crlf.yaml"
    path.write_bytes(ar.render(GOOD).replace("\n", newline).encode("utf-8"))
    run = [sys.executable, "-m", "governance.approval_request", "check", str(path)]
    done = subprocess.run(run, check=False, capture_output=True, text=True)
    assert done.returncode == 3 and "CR" in done.stderr
