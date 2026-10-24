"""Task 10: `models.toml` and `darkfactory models list|set` (spec 3.6)."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from df import cli, fsutil, journal, state
from df.config import ConfigError
from df.journal import Stable, StepFailed
from df.models import (
    HARNESSES,
    ROLE_CONTEXT,
    ROLES,
    ModelChoice,
    ModelsConfig,
    config_hash,
    dump_models,
    load_models,
    parse_models,
    resolve,
)
from df.paths import Layout
from tests.df.conftest import FakeLayout
from tests.df.install_world import World, installed, make_world

P = Path(__file__).parent / "fixtures" / "models.toml"
SCHEMA = 'schema = "df/models/v1"\n'
SET_AUTHOR = ["models", "set", "author", "--harness", "claude", "--model", "m-a"]
SET_JUDGE = ["models", "set", "judge", "--harness", "codex", "--model", "m-j"]

#: Paths the audit hook records while a test listens (`_audit_opens`).
_OPENS: list[list[str]] = []


def _audit(event: str, args: tuple[object, ...]) -> None:
    if event == "open" and _OPENS and args and isinstance(args[0], (str, Path)):
        _OPENS[-1].append(str(args[0]))


sys.addaudithook(_audit)


@contextlib.contextmanager
def audit_opens() -> Iterator[list[str]]:
    """The paths opened (audit event `open`) inside the block."""
    opens: list[str] = []
    _OPENS.append(opens)
    try:
        yield opens
    finally:
        _OPENS.remove(opens)


@pytest.fixture()
def w(monkeypatch: pytest.MonkeyPatch, fake_layout: FakeLayout) -> World:
    world = make_world(fake_layout, monkeypatch)
    installed(world)
    return world


def models_path(w: World) -> Path:
    return w.lay.etc / "models.toml"


def reader_raw(models_text: bytes | None) -> Stable[state.RawSnapshot]:
    raw = state.RawSnapshot(0, None, None, None, models_text, False)
    return Stable(raw, 0)


def list_json(
    lay: Layout, capsys: pytest.CaptureFixture[str], text: bytes | None
) -> list[dict[str, object]]:
    """`models list --json` over a reader snapshot whose models text is `text`."""
    code = cli.main(
        ["models", "list", "--json"],
        mode="reader",
        read=reader_raw(text),
        lock_fd=None,
        layout=lay,
    )
    out = capsys.readouterr()
    assert code == 0, out.err
    return json.loads(out.out)


# ---------- the module ----------


def test_role_context_covers_all_roles() -> None:
    assert set(ROLE_CONTEXT) == set(ROLES)
    assert set(ROLE_CONTEXT.values()) == {"df-exec", "df-review"}
    assert {r for r, u in ROLE_CONTEXT.items() if u == "df-exec"} == {
        "interview",
        "author",
    }
    assert ROLES == (
        "interview",
        "author",
        "reviewer",
        "executor-review",
        "edge-check",
        "judge",
    )
    assert HARNESSES == ("claude", "codex")


def test_unsupported_param_refused(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """rev 8: a parameter the adapter cannot pass is a schema refusal — in
    the parser, in the value and in `models set` (nothing written)."""
    text = SCHEMA + (
        '[roles.author]\nharness = "claude"\nmodel = "m"\n'
        '[roles.author.params]\ntemperature = "0"\n'
    )
    with pytest.raises(ConfigError, match="temperature"):
        parse_models(text)
    path = w.lay.root / "m.toml"
    path.write_text(text)
    with pytest.raises(ConfigError, match="temperature"):
        load_models(path)
    with pytest.raises(ConfigError, match="temperature"):
        ModelChoice("claude", "m", None, {"temperature": "0"})
    gen = journal.read_generation(w.lay)
    assert w.writer([*SET_AUTHOR, "--param", "temperature=0"]) == 2
    assert "temperature" in capsys.readouterr().err
    assert not models_path(w).exists()
    assert journal.read_generation(w.lay) == gen


def test_product_override_wins_and_names_source() -> None:
    cfg = parse_models(P.read_text())
    choice, source = resolve(cfg, "reviewer", 42)
    assert (choice.model, choice.effort, source) == (
        "claude-sonnet-5-5",
        "medium",
        "продукт 42",
    )
    choice, source = resolve(cfg, "reviewer", 7)  # no override of product 7
    assert (choice.model, source) == ("gpt-5.4", "общий")
    choice, source = resolve(cfg, "author", 42)  # 42 overrides reviewer only
    assert (choice.model, source) == ("claude-opus-5-5", "общий")
    assert resolve(cfg, "reviewer", None)[1] == "общий"
    with pytest.raises(ConfigError, match="judge"):
        resolve(cfg, "judge", None)
    with pytest.raises(ConfigError, match="nobody"):
        resolve(cfg, "nobody", None)


def test_dump_parse_round_trip() -> None:
    cfg = parse_models(P.read_text())
    assert parse_models(dump_models(cfg)) == cfg
    assert parse_models(SCHEMA) == ModelsConfig({}, {})
    assert dump_models(ModelsConfig({}, {})) == SCHEMA


@pytest.mark.parametrize(
    "body,needle",
    [
        ('[roles.nobody]\nharness = "claude"\nmodel = "m"\n', "nobody"),
        ('[roles.author]\nharness = "gemini"\nmodel = "m"\n', "gemini"),
        ('[roles.author]\nharness = "claude"\nmodel = "m"\nextra = 1\n', "extra"),
        ('[roles.author]\nharness = "claude"\n', "model"),
        ('[roles.author]\nharness = "claude"\nmodel = ""\n', "model"),
        ('[roles.author]\nharness = "claude"\nmodel = 5\n', "model"),
        ('[roles.author]\nharness = "claude"\nmodel = "m"\neffort = ""\n', "effort"),
        ("other = 1\n", "other"),
        ('[products."x".roles.author]\nharness = "claude"\nmodel = "m"\n', "x"),
        ('[products."07".roles.author]\nharness = "claude"\nmodel = "m"\n', "07"),
        ('[products."1".other]\nharness = "claude"\n', "other"),
        ('[products."1".roles.judge]\nharness = "codex"\nmodel = "m"\nk = 1\n', "k"),
        ("roles = 1\n", "roles"),
    ],
)
def test_unknown_role_harness_key_refused(
    tmp_path: Path, body: str, needle: str
) -> None:
    with pytest.raises(ConfigError, match=needle):
        parse_models(SCHEMA + body)
    path = tmp_path / "models.toml"
    path.write_text(SCHEMA + body)
    with pytest.raises(ConfigError, match=needle):
        load_models(path)


def test_wrong_schema_refused() -> None:
    with pytest.raises(ConfigError, match="schema"):
        parse_models('schema = "df/models/v2"\n')
    with pytest.raises(ConfigError, match="schema"):
        parse_models("[roles.author\n")


def test_config_hash_is_canonical() -> None:
    a = ModelChoice("claude", "m", "high", {})
    assert config_hash(a) == config_hash(ModelChoice("claude", "m", "high", {}))
    assert len(config_hash(a)) == 64
    assert config_hash(a) != config_hash(ModelChoice("claude", "m", None, {}))
    assert config_hash(a) != config_hash(ModelChoice("codex", "m", "high", {}))


def test_env_does_not_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DF_MODEL_AUTHOR", "x")
    monkeypatch.setenv("ANTHROPIC_MODEL", "y")
    assert resolve(parse_models(P.read_text()), "author", None)[0].model == (
        "claude-opus-5-5"
    )


# ---------- models set ----------


def test_set_preserves_other_entries_and_is_atomic(
    w: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the role's entry changes; the write is one `write_atomic` of
    `models.toml` (0o644) inside an odd generation, which ends even."""
    path = models_path(w)
    path.write_text(P.read_text())
    gen = journal.read_generation(w.lay)
    writes: list[tuple[Path, int, int]] = []
    real = fsutil.write_atomic

    def write_atomic(target: Path, text: str, mode: int = 0o644) -> None:
        writes.append((target, mode, journal.read_generation(w.lay)))
        real(target, text, mode)

    monkeypatch.setattr(fsutil, "write_atomic", write_atomic)
    assert w.writer([*SET_AUTHOR, "--effort", "low"]) == 0
    assert writes == [(path, 0o644, gen + 1)]
    assert journal.read_generation(w.lay) == gen + 2
    before, after = parse_models(P.read_text()), load_models(path)
    assert after.default["author"] == ModelChoice("claude", "m-a", "low", {})
    assert after.default["reviewer"] == before.default["reviewer"]
    assert after.products == before.products
    assert os.stat(path).st_mode & 0o777 == 0o644
    assert [p for p in os.listdir(w.lay.etc) if p.endswith(".tmp")] == []


def test_set_creates_the_file(w: World, capsys: pytest.CaptureFixture[str]) -> None:
    assert not models_path(w).exists()
    assert w.writer(SET_JUDGE) == 0
    assert "judge" in capsys.readouterr().out
    assert load_models(models_path(w)).default == {
        "judge": ModelChoice("codex", "m-j", None, {})
    }


@pytest.mark.parametrize(
    "extra,needle",
    [
        (["--harness", "gemini"], "gemini"),
        (["--model", ""], "model"),
        (["--param", "novalue"], "novalue"),
    ],
)
def test_set_refuses_bad_values(
    w: World, capsys: pytest.CaptureFixture[str], extra: list[str], needle: str
) -> None:
    gen = journal.read_generation(w.lay)
    assert w.writer([*SET_AUTHOR, *extra]) == 2
    assert needle in capsys.readouterr().err
    assert not models_path(w).exists()
    assert journal.read_generation(w.lay) == gen


def test_set_refuses_unknown_role(w: World, capsys: pytest.CaptureFixture[str]) -> None:
    assert w.writer(["models", "set", "nobody", "--harness", "claude", "--model", "m"])
    assert "nobody" in capsys.readouterr().err
    assert not models_path(w).exists()


def test_set_keeps_an_invalid_file(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """An invalid `models.toml` is refused (snapshot or parse), not overwritten."""
    models_path(w).write_text(SCHEMA + "junk = 1\n")
    assert w.writer(SET_AUTHOR) == 2
    assert "junk" in capsys.readouterr().err
    assert models_path(w).read_text() == SCHEMA + "junk = 1\n"


@pytest.mark.parametrize("registry", [False, True])
def test_set_product_refused_in_stage1(
    w: World, capsys: pytest.CaptureFixture[str], registry: bool
) -> None:
    """R57: no registry parser before stage 3: exit 2, nothing written."""
    if registry:
        (w.lay.etc / "products.toml").write_text('schema = "df/products/v1"\n')
    assert w.writer([*SET_AUTHOR, "--product", "42"]) == 2
    err = capsys.readouterr().err
    wanted = "не поддерживается до этапа 3" if registry else "не зарегистрирован"
    assert wanted in err
    assert not models_path(w).exists()


def test_set_closes_an_odd_generation(w: World) -> None:
    """M9: a crash between begin and end left an odd generation without
    `pending.toml`; the next `models set` closes it with a `warn`."""
    journal.generation_begin(w.lay)
    gen = journal.read_generation(w.lay)
    assert gen % 2 == 1
    assert w.writer(SET_AUTHOR) == 0
    assert journal.read_generation(w.lay) == gen + 3
    rows = journal.read_journal(w.lay.journal)
    assert any(r["step"] == "generation" and r["event"] == "warn" for r in rows)


def test_concurrent_sets_of_two_roles_both_persist(w: World) -> None:
    """R61: two processes; A holds the lock while B loses it (exit 2 /
    `StepFailed("lock")`) and retries; both roles persist."""
    path = models_path(w)
    in_a_read, in_a_write = os.pipe()
    go_read, go_write = os.pipe()
    lost = w.lay.root / "lost"

    def run(argv: list[str], hold: bool) -> None:
        if hold:
            real = fsutil.write_atomic

            def slow(target: Path, text: str, mode: int = 0o644) -> None:
                os.write(in_a_write, b"in")
                os.read(go_read, 2)  # parent: B has lost once
                real(target, text, mode)

            fsutil.write_atomic = slow  # the child's own copy of the module
        for _ in range(500):
            try:
                os._exit(w.writer(argv))
            except StepFailed as exc:
                if exc.step != "lock":
                    os._exit(9)
                lost.touch()
                time.sleep(0.01)
        os._exit(8)

    a = os.fork()
    if a == 0:
        run(SET_AUTHOR, hold=True)
    assert os.read(in_a_read, 2) == b"in"
    b = os.fork()
    if b == 0:
        run(SET_JUDGE, hold=False)
    for _ in range(1000):
        if lost.exists():
            break
        time.sleep(0.01)
    assert lost.exists(), "B never contended for the lock"
    os.write(go_write, b"go")
    assert os.waitpid(a, 0)[1] == 0
    assert os.waitpid(b, 0)[1] == 0
    for fd in (in_a_read, in_a_write, go_read, go_write):
        os.close(fd)
    assert set(load_models(path).default) == {"author", "judge"}
    assert journal.read_generation(w.lay) % 2 == 0


def test_set_requires_root(w: World, capsys: pytest.CaptureFixture[str]) -> None:
    w.mp.setattr(cli, "ROOT_UID", os.geteuid() + 1)
    gen = journal.read_generation(w.lay)
    assert w.writer(SET_AUTHOR) == 2
    assert "требует root" in capsys.readouterr().err
    assert not models_path(w).exists()
    assert journal.read_generation(w.lay) == gen


def test_models_without_subcommand_is_usage(
    fake_layout: FakeLayout, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(
        ["models"],
        mode="reader",
        read=reader_raw(None),
        lock_fd=None,
        layout=fake_layout.layout,
    )
    assert code == 2
    assert "Traceback" not in capsys.readouterr().err


# ---------- models list ----------


def test_list_columns_text_and_json(
    fake_layout: FakeLayout, capsys: pytest.CaptureFixture[str]
) -> None:
    lay = fake_layout.layout
    code = cli.main(
        ["models", "list"],
        mode="reader",
        read=reader_raw(P.read_bytes()),
        lock_fd=None,
        layout=lay,
    )
    out = capsys.readouterr().out
    assert code == 0
    lines = out.splitlines()
    assert lines[0].split() == [
        "роль",
        "harness",
        "model",
        "effort",
        "источник",
        "проверка",
    ]
    rows = {line.split()[0]: line for line in lines[1:]}
    assert list(rows) == list(ROLES)
    assert rows["author"].split()[:5] == [
        "author",
        "claude",
        "claude-opus-5-5",
        "high",
        "общий",
    ]
    assert rows["reviewer"].split()[3] == "—"
    assert rows["judge"].split()[1:4] == ["—", "—", "—"]
    assert "не задано" in rows["judge"]
    assert all(line.rstrip().endswith("не проверено") for line in lines[1:])

    data = list_json(lay, capsys, P.read_bytes())
    assert [d["role"] for d in data] == list(ROLES)
    assert data[1] == {
        "role": "author",
        "harness": "claude",
        "model": "claude-opus-5-5",
        "effort": "high",
        "params": {},
        "source": "общий",
        "probe": "не проверено",
    }
    assert data[-1] == {
        "role": "judge",
        "harness": None,
        "model": None,
        "effort": None,
        "params": None,
        "source": None,
        "probe": "не проверено",
    }


def test_list_without_models_toml(
    fake_layout: FakeLayout, capsys: pytest.CaptureFixture[str]
) -> None:
    """R58: a fresh install has no `models.toml`: every role, unset, exit 0."""
    data = list_json(fake_layout.layout, capsys, None)
    assert [d["role"] for d in data] == list(ROLES)
    assert {d["source"] for d in data} == {None}


def test_list_refuses_invalid_models_toml(
    fake_layout: FakeLayout, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(
        ["models", "list"],
        mode="reader",
        read=reader_raw((SCHEMA + "junk = 1\n").encode()),
        lock_fd=None,
        layout=fake_layout.layout,
    )
    assert code == 2
    assert "junk" in capsys.readouterr().err


def test_list_product_refused_in_stage1(
    fake_layout: FakeLayout, capsys: pytest.CaptureFixture[str]
) -> None:
    """R57: the reader refuses `--product` with the absent-registry text,
    reading nothing."""
    code = cli.main(
        ["models", "list", "--product", "42"],
        mode="reader",
        read=reader_raw(P.read_bytes()),
        lock_fd=None,
        layout=fake_layout.layout,
    )
    assert code == 2
    assert "продукт не зарегистрирован (этап 3)" in capsys.readouterr().err


def test_list_uses_snapshot_text_only(
    fake_layout: FakeLayout,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """I2: `models list` gets snapshot A; `models.toml` is then replaced by
    B; no open of `models.toml`; the output is A's."""
    lay = fake_layout.layout
    lay.etc.mkdir(parents=True)
    path = lay.etc / "models.toml"
    a = P.read_bytes()
    path.write_text(SCHEMA + '[roles.author]\nharness = "codex"\nmodel = "B"\n')
    with audit_opens() as opens:
        data = list_json(lay, capsys, a)
    assert data[1]["model"] == "claude-opus-5-5"
    assert [p for p in opens if p.endswith("models.toml")] == []
    with audit_opens() as seen:  # the hook sees such an open (control)
        load_models(path)
    assert str(path) in seen
