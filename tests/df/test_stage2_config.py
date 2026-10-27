"""Stage-2 schemas: publisher, products, trust and the run record (Task 2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from df.config import STATE_SCHEMAS, Account, ConfigError
from df.stage2_config import (
    OPERATIONS,
    ModelPin,
    Product,
    PublisherConfig,
    RunRecord,
    TrustBinding,
    dump_products,
    dump_publisher,
    dump_run,
    expected_publisher,
    load_products,
    load_trust,
    parse_products,
    parse_publisher,
    parse_run,
    parse_trust,
)

PUB = 'schema = "df/publisher/v1"\n'
ROW = (
    "[[product]]\n"
    "repository_id = {rid}\n"
    'full_name = "o/{rid}"\n'
    'profile = "fork"\n'
    "product_app_id = 11\n"
    "product_installation_id = 12\n"
    'machine_user = "bot"\n'
    'checkout = "/home/df-exec/{rid}"\n'
)
TRUST = (
    'schema = "df/trust/v1"\n'
    'host = "github.com"\n'
    "df_trust_repository_id = 5\n"
    'df_trust_full_name = "o/df-trust"\n'
    'df_trust_branch = "main"\n'
    "approval_policy_repository_id = 6\n"
    'transport = "{transport}"\n'
)


def accounts() -> dict[str, Account]:
    return {
        n: Account(n, uid, uid, "ready", "t")
        for n, uid in (("df-exec", 601), ("df-review", 602), ("df-publisher", 603))
    }


def pin() -> ModelPin:
    return ModelPin("claude", "1.0", "m", "m-actual", None, {"a": "b"}, "h" * 8)


def record() -> RunRecord:
    return RunRecord(
        "r1", "rel1", "d" * 64, "a" * 64, 7, {"author": pin()},
        ("github.com", 5, "main"), "2026-10-27T00:00:00+00:00", 501,
    )  # fmt: skip


def test_publisher_expected_table() -> None:
    cfg = expected_publisher(accounts())
    assert cfg.senders[601] == {"merge", "record-violation", "perimeter-status"}
    assert cfg.senders[602] == {
        "publish-review",
        "read-token",
        "record-violation",
        "perimeter-status",
    }
    assert 603 not in cfg.senders
    assert cfg.api_base == "https://api.github.com"


def test_publisher_roundtrip_and_unknown_key() -> None:
    cfg = expected_publisher(accounts())
    text = dump_publisher(cfg)
    assert parse_publisher(text) == cfg
    with pytest.raises(ConfigError, match="unknown key"):
        parse_publisher(text + "extra = 1\n")


def test_publisher_dump_is_sorted_and_deterministic() -> None:
    text = dump_publisher(expected_publisher(accounts()))
    assert text == dump_publisher(parse_publisher(text))
    assert text.index("601") < text.index("602")


def test_publisher_unknown_operation_refused() -> None:
    with pytest.raises(ConfigError, match="operation"):
        parse_publisher(
            PUB + '[[sender]]\nuid = 601\noperations = ["shell"]\n',
        )


def test_publisher_all_operations_known() -> None:
    assert "merge" in OPERATIONS and len(OPERATIONS) == 5


@pytest.mark.parametrize(
    "rows",
    [
        "[[sender]]\nuid = 1\noperations = []\n",
        '[[sender]]\nuid = 1\noperations = ["merge", "merge"]\n',
        '[[sender]]\nuid = -1\noperations = ["merge"]\n',
        '[[sender]]\nuid = true\noperations = ["merge"]\n',
        ('[[sender]]\nuid = 1\noperations = ["merge"]\n') * 2,
        '[[sender]]\nuid = 1\noperations = "merge"\n',
        "[[sender]]\nuid = 1\n",
    ],
)
def test_publisher_bad_sender_refused(rows: str) -> None:
    with pytest.raises(ConfigError):
        parse_publisher(PUB + rows)


def test_publisher_wrong_schema() -> None:
    with pytest.raises(ConfigError, match="schema"):
        parse_publisher('schema = "df/publisher/v2"\n')


@pytest.mark.parametrize(
    "base",
    [
        "https://api.github.com",
        "http://127.0.0.1:1",
        "http://127.0.0.1:8080",
        "http://127.0.0.1:65535",
    ],
)
def test_api_base_accepted(base: str) -> None:
    cfg = parse_publisher(PUB + f'api_base = "{base}"\n')
    assert cfg.api_base == base
    assert parse_publisher(dump_publisher(cfg)) == cfg


@pytest.mark.parametrize(
    "base",
    [
        "https://api.github.com/",
        "https://api.github.com/v3",
        "http://localhost:8080",
        "http://127.0.0.1",
        "http://127.0.0.1:0",
        "http://127.0.0.1:65536",
        "http://127.0.0.1:080",
        "http://127.0.0.1:8080/",
        "https://127.0.0.1:8080",
        "http://127.0.0.2:8080",
        "https://example.com",
        "http://api.github.com",
        "",
    ],
)
def test_api_base_refused(base: str) -> None:
    with pytest.raises(ConfigError, match="api_base"):
        parse_publisher(PUB + f'api_base = "{base}"\n')


def test_api_base_trailing_newline_refused() -> None:
    with pytest.raises(ConfigError, match="api_base"):
        parse_publisher(PUB + 'api_base = "http://127.0.0.1:80\\n"\n')


def test_api_base_wrong_type() -> None:
    with pytest.raises(ConfigError, match="api_base"):
        parse_publisher(PUB + "api_base = 1\n")


def test_products_roundtrip_and_keys() -> None:
    text = ROW.format(rid=9) + ROW.format(rid=3)
    prods = parse_products('schema = "df/products/v1"\n' + text)
    assert sorted(prods) == [3, 9]
    assert prods[3] == Product(3, "o/3", "fork", 11, 12, "bot", "/home/df-exec/3")
    assert parse_products(dump_products(prods)) == prods
    assert dump_products(prods).index("o/3") < dump_products(prods).index("o/9")


def test_products_duplicate_repository_id() -> None:
    text = 'schema = "df/products/v1"\n' + ROW.format(rid=1) + ROW.format(rid=1)
    with pytest.raises(ConfigError, match="duplicate"):
        parse_products(text)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t.replace('profile = "fork"', 'profile = "admin"'),
        lambda t: t.replace("product_app_id = 11", "product_app_id = 0"),
        lambda t: t.replace("repository_id = 1", "repository_id = -1"),
        lambda t: t.replace('machine_user = "bot"', "machine_user = 1"),
        lambda t: t.replace('checkout = "/home/df-exec/1"\n', ""),
        lambda t: t + "extra = 1\n",
    ],
)
def test_products_bad_row(mutate) -> None:  # noqa: ANN001
    text = mutate('schema = "df/products/v1"\n' + ROW.format(rid=1))
    with pytest.raises(ConfigError):
        parse_products(text)


def test_products_absent_file(tmp_path: Path) -> None:
    assert load_products(tmp_path / "products.toml") == {}


def test_products_load(tmp_path: Path) -> None:
    f = tmp_path / "products.toml"
    f.write_text('schema = "df/products/v1"\n' + ROW.format(rid=2))
    assert list(load_products(f)) == [2]


def test_trust_parse_and_root_id(tmp_path: Path) -> None:
    b = parse_trust(TRUST.format(transport="public"))
    assert b == TrustBinding("github.com", 5, "o/df-trust", "main", 6, "public")
    assert b.root_id == ("github.com", 5, "main")
    f = tmp_path / "trust.toml"
    f.write_text(TRUST.format(transport="public"))
    assert load_trust(f) == b
    assert load_trust(tmp_path / "absent.toml") is None


def test_trust_transport_public_only() -> None:
    with pytest.raises(ConfigError, match="transport"):
        parse_trust(TRUST.format(transport="app"))


def test_trust_unknown_key() -> None:
    with pytest.raises(ConfigError, match="unknown key"):
        parse_trust(TRUST.format(transport="public") + "x = 1\n")


def test_run_record_roundtrip() -> None:
    rec = record()
    assert parse_run(dump_run(rec)) == rec
    no_root = RunRecord(**{**rec.__dict__, "root_id": None})
    assert parse_run(dump_run(no_root)) == no_root


def test_run_record_canonical_output() -> None:
    text = dump_run(record())
    data = json.loads(text)
    assert data["schema"] == "df/run/v1"
    assert data["models"]["author"]["effort"] is None
    assert data["root_id"] == ["github.com", 5, "main"]
    assert text == json.dumps(data, sort_keys=True, separators=(",", ":"))


def test_run_schema_registered() -> None:
    assert STATE_SCHEMAS["runs/<id>.json"] == "df/run/v1"


def test_run_record_rejects_unknown_field() -> None:
    with pytest.raises(ConfigError):
        parse_run(json.dumps({**json.loads(dump_run(record())), "x": 1}))


def test_run_record_rejects_unknown_pin_field() -> None:
    data = json.loads(dump_run(record()))
    data["models"]["author"]["x"] = 1
    with pytest.raises(ConfigError):
        parse_run(json.dumps(data))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(schema="df/run/v2"),
        lambda d: d.pop("run_id"),
        lambda d: d.update(product="7"),
        lambda d: d.update(started_by_uid=True),
        lambda d: d.update(root_id=["h", 1]),
        lambda d: d.update(root_id=["h", "1", "b"]),
        lambda d: d["models"]["author"].update(params=[]),
    ],
)
def test_run_record_bad_values(mutate) -> None:  # noqa: ANN001
    data = json.loads(dump_run(record()))
    mutate(data)
    with pytest.raises(ConfigError):
        parse_run(json.dumps(data))


@pytest.mark.parametrize("text", ["{", "[]", "null", "\xff"])
def test_run_record_not_json_object(text: str) -> None:
    with pytest.raises(ConfigError):
        parse_run(text)


def test_publisher_config_is_immutable() -> None:
    cfg = PublisherConfig({601: frozenset({"merge"})}, "https://api.github.com")
    with pytest.raises(TypeError):
        cfg.senders[1] = frozenset()  # type: ignore[index]
