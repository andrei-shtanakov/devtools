"""The publisher's supervised precheck client (stage 2b, Task 7): an executable
script in `tmp_path` stands in for `sudo -u df-precheck .../df-precheck`, a fake
GitHub mints and revokes the read token."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path

import pytest

from df import platform
from df.paths import Layout
from df.publisher import github, ops, precheck
from df.publisher import main as pmain
from df.publisher.main import Context
from df.publisher.ops import OpContext
from df.publisher.precheck import PrecheckResult, PrFacts
from df.publisher.request import Refused, Response
from df.stage2_config import dump_publisher, parse_publisher
from governance import act_precheck
from tests.df.publisher_world import SHA, World, raw_of
from tests.df.test_publisher_ops import (
    PRODUCT,
    REPO,
    FakeGitHub,
    Reply,
    _reply,
    gh,  # noqa: F401  (fixture)
    key_text,  # noqa: F401  (fixture)
    pytestmark,  # noqa: F401  (skip without openssl)
)

TOKEN = "ghs_SECRETVALUE"
TIP = "c" * 40
STOP = "workstreams/ws/approvals/stops/n1-g2.yaml"
FACTS = PrFacts(7, "stop/ws-n1-g2", "master", TIP, (("added", STOP, None),))
PR = {"number": 12, "head": {"sha": SHA, "ref": "stop/ws-n1-g2"}}
ADMIN = "sha256:" + "d" * 64

#: The stand-in for the precheck: reads the request, keeps a copy, answers
#: with a verdict bound to it, changed by `OVER` (`None` drops a key), then
#: runs `CODE`.
SCRIPT = """#!{python} -I
import hashlib, json, os, signal, sys, time
raw = sys.stdin.buffer.read()
with open({capture!r}, "wb") as f:
    f.write(raw)
req = json.loads(raw)
body = {{k: v for k, v in req.items() if k != "token"}}
canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
verdict = {{
    "schema": "df/precheck-verdict/v1",
    "nonce": req["nonce"],
    "request_digest": hashlib.sha256(canonical.encode()).hexdigest(),
    "repository_id": req["repository_id"],
    "pr": req["pr"],
    "head_sha": req["head_sha"],
    "kind": req["kind"],
    "outcome": "allow",
    "reason": "",
    "version": "df-precheck/1",
    "admin_digest": {admin!r},
}}
for key, value in json.loads({over!r}).items():
    if value is None:
        verdict.pop(key, None)
    else:
        verdict[key] = value
TOKEN = req["token"]
{code}
"""
PRINT = 'sys.stdout.write(json.dumps(verdict) + "\\n")'


class RevokingGitHub(FakeGitHub):
    """The fake GitHub that also answers `DELETE /installation/token`."""

    def __init__(self, world: World) -> None:
        super().__init__(world)
        self.revoked: list[str] = []
        self.revoke_reply: Reply = (204, {}, b"")

    def route(
        self,
        method: str,
        path: str,
        query: str,
        headers: Mapping[str, str],
        data: dict[str, object] | None,
    ) -> Reply:
        if (method, path) == ("DELETE", "/installation/token"):
            self.revoked.append(headers["Authorization"].removeprefix("Bearer "))
            return self.revoke_reply
        return super().route(method, path, query, headers, data)


class Stand:
    """One `admin_precheck` call against a script in `tmp_path`."""

    def __init__(self, world: World, fake: RevokingGitHub, tmp_path: Path) -> None:
        self.world, self.fake, self.tmp = world, fake, tmp_path
        self.capture = tmp_path / "request.json"
        self.script = tmp_path / "df-precheck"
        self.done = tmp_path / "done"
        self.git_base = "https://github.com"

    def answer(self, code: str = PRINT, **over: object) -> None:
        """The script's verdict changes and its code after reading."""
        self.script.write_text(
            SCRIPT.format(
                python=sys.executable,
                capture=str(self.capture),
                admin=ADMIN,
                over=json.dumps(over),
                code=code,
            )
        )
        self.script.chmod(0o755)

    def oc(self) -> OpContext:
        lay = self.world.lay
        return OpContext(
            lay,
            PRODUCT,
            lay.app_key(42),
            Path(str(gh_openssl())),
            self.fake,
            time.time(),
            github.API,
            {7: PRODUCT},
            self.git_base,
        )

    def call(self, facts: PrFacts = FACTS) -> PrecheckResult:
        return precheck.admin_precheck("stop", self.oc(), PR, facts)

    def sent(self) -> dict[str, object]:
        return json.loads(self.capture.read_bytes())


def gh_openssl() -> str:
    from tests.df.test_publisher_ops import OPENSSL

    assert OPENSSL is not None
    return OPENSSL


@pytest.fixture()
def stand(
    world: World,
    gh: FakeGitHub,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Stand:
    fake = RevokingGitHub(world)
    monkeypatch.setattr(github, "default_transport", fake)
    s = Stand(world, fake, tmp_path)
    monkeypatch.setattr(precheck, "command", lambda _layout: (str(s.script),))
    s.answer()
    return s


@pytest.fixture()
def no_signals(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Every signal the client tries to send (there must be none)."""
    sent: list[object] = []
    monkeypatch.setattr(os, "kill", lambda *a: sent.append(a))
    monkeypatch.setattr(os, "killpg", lambda *a: sent.append(a))
    monkeypatch.setattr(subprocess.Popen, "send_signal", lambda *a: sent.append(a))
    return sent


def reason(result: PrecheckResult) -> str | None:
    return None if result.refused is None else result.refused.reason


# --- the command --------------------------------------------------------------


def test_command_is_fixed() -> None:
    """Requirement 2: sudo to the role, the wrapper, no argument (the rule is
    `df-precheck ""`)."""
    assert precheck.command(Layout(Path("/"))) == (
        "/usr/bin/sudo",
        "-n",
        "-u",
        "df-precheck",
        "/opt/df/libexec/df-precheck",
    )


def test_supervised_with_fixed_limits(
    stand: Stand, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, object]] = []
    real = platform.run_supervised

    def spy(argv: list[str], data: bytes, **kw: object) -> platform.SupervisedResult:
        seen.append({"argv": tuple(argv), **kw})
        return real(argv, data, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(platform, "run_supervised", spy)
    stand.call()
    assert seen == [
        {
            "argv": (str(stand.script),),
            "limit": 64 << 10,
            "wait": 200.0,
            "env": {"PATH": "/usr/bin:/bin", "LANG": "C"},
        }
    ]


# --- a bound verdict ----------------------------------------------------------


def test_allow_bound_verdict_passes(stand: Stand) -> None:
    result = stand.call()
    assert result.refused is None
    assert (result.version, result.admin_digest, result.revoke) == (
        "df-precheck/1",
        ADMIN,
        "ok",
    )
    parsed = act_precheck.parse_request(stand.capture.read_bytes())
    assert result.request_digest == parsed.digest


def test_request_digest_matches_sent_request(stand: Stand) -> None:
    """The request the precheck got passes its own strict schema; the digest
    computed there (independently, without the token) is the bound one."""
    result = stand.call()
    raw = stand.capture.read_bytes()
    parsed = act_precheck.parse_request(raw)
    sent = json.loads(raw)
    body = {k: v for k, v in sent.items() if k != "token"}
    assert act_precheck.request_digest(body) == parsed.digest == result.request_digest
    assert sent["token"] == TOKEN and TOKEN not in json.dumps(body)
    assert (parsed.kind, parsed.repository_id, parsed.full_name, parsed.pr) == (
        "stop",
        7,
        REPO,
        12,
    )
    assert (parsed.head_sha, parsed.head_ref, parsed.base_ref, parsed.base_tip) == (
        SHA,
        "stop/ws-n1-g2",
        "master",
        TIP,
    )
    assert parsed.changes == (("added", STOP, None),)
    assert (parsed.api_base, parsed.git_base) == (github.API, "https://github.com")


def test_nonce_is_fresh_per_call(stand: Stand) -> None:
    stand.call()
    first = stand.sent()["nonce"]
    stand.call()
    assert stand.sent()["nonce"] != first


def test_refuse_verdict_is_a_refusal(stand: Stand) -> None:
    stand.answer(outcome="refuse", reason="already_stopped: node n1 g2")
    result = stand.call()
    assert reason(result) == "already_stopped: node n1 g2"
    assert result.version == "df-precheck/1" and result.request_digest is not None


def test_unset_verdict_is_a_refusal(stand: Stand) -> None:
    stand.answer(outcome="unset", reason="deadline")
    assert reason(stand.call()) == "precheck_unset: deadline"


# --- binding ------------------------------------------------------------------


def test_verdict_for_other_head_refused(stand: Stand) -> None:
    stand.answer(head_sha="b" * 40)
    result = stand.call()
    assert reason(result) == "precheck_failed: binding_mismatch"
    assert result.request_digest is None and result.version is None


def test_nonce_mismatch_refused(stand: Stand) -> None:
    stand.answer(nonce="0" * 32)
    assert reason(stand.call()) == "precheck_failed: binding_mismatch"


def test_digest_mismatch_refused(stand: Stand) -> None:
    stand.answer(request_digest="e" * 64)
    assert reason(stand.call()) == "precheck_failed: binding_mismatch"


@pytest.mark.parametrize(
    "over,failure",
    [
        ({"repository_id": 8}, "binding_mismatch"),
        ({"pr": 13}, "binding_mismatch"),
        ({"kind": "candidate"}, "parse"),  # outside protocol v1: the schema
    ],
    ids=["repository", "pr", "kind"],
)
def test_other_bound_field_refused(
    stand: Stand, over: dict[str, object], failure: str
) -> None:
    stand.answer(**over)
    assert reason(stand.call()) == f"precheck_failed: {failure}"


# --- strict parsing -----------------------------------------------------------


def test_extra_key_refused(stand: Stand) -> None:
    stand.answer(extra=1)
    assert reason(stand.call()) == "precheck_failed: parse"


@pytest.mark.parametrize(
    "code,over",
    [
        (PRINT, {"version": None}),
        (PRINT, {"outcome": "maybe"}),
        (PRINT, {"repository_id": True}),
        (PRINT, {"pr": "12"}),
        (PRINT, {"admin_digest": "d" * 64}),
        (PRINT, {"version": ""}),
        (PRINT, {"reason": "x" * 2049 + "!" * 20}),
        (PRINT, {"schema": "df/precheck-verdict/v2"}),
        ("sys.stdout.write(json.dumps(verdict))", {}),
        ('sys.stdout.write(json.dumps(verdict) + "\\n\\n")', {}),
        ('sys.stdout.write(json.dumps(verdict, indent=1) + "\\n")', {}),
        ('sys.stdout.write("[]\\n")', {}),
        ('sys.stdout.write("not json\\n")', {}),
        ("sys.stdout.write('{\"a\": NaN}\\n')", {}),
        ('sys.stdout.buffer.write(b"\\xff\\n")', {}),
        (
            'sys.stdout.write(json.dumps(verdict)[:-1] + \', "kind": "stop"}\\n\')',
            {},
        ),
        ("pass", {}),
        (PRINT, {"outcome": "refuse"}),  # a refusal without a reason
        (PRINT, {"outcome": "unset"}),
        (PRINT, {"reason": "why"}),  # allow carries no reason
        (PRINT, {"version": "v" * 65}),
        (PRINT, {"version": "df-precheck/1 "}),
        (PRINT, {"version": "df\u2011precheck/1"}),
        ('sys.stdout.write(json.dumps(verdict) + " \\n")', {}),
        ('sys.stdout.write(json.dumps(verdict) + "\\r\\n")', {}),
        ('sys.stdout.write(" " + json.dumps(verdict) + "\\n")', {}),
        ('sys.stdout.write(json.dumps(verdict, separators=(",", ":")) + "\\n")', {}),
    ],
    ids=[
        "missing-key",
        "outcome",
        "bool-id",
        "str-pr",
        "admin-digest",
        "empty-version",
        "long-reason",
        "schema",
        "no-newline",
        "two-lines",
        "multi-line",
        "not-object",
        "not-json",
        "nan",
        "not-utf8",
        "duplicate-key",
        "empty",
        "refuse-no-reason",
        "unset-no-reason",
        "allow-with-reason",
        "long-version",
        "version-space",
        "version-charset",
        "trailing-space",
        "crlf",
        "leading-space",
        "other-separators",
    ],
)
def test_malformed_verdict_refused(
    stand: Stand, code: str, over: dict[str, object]
) -> None:
    stand.answer(code, **over)
    assert reason(stand.call()) == "precheck_failed: parse"


def test_truncated_reason_at_the_cap_accepted(stand: Stand) -> None:
    """The precheck's own cap (2048 + the marker) is within the client's."""
    long = "x" * act_precheck.MAX_REASON + act_precheck.TRUNCATED
    stand.answer(outcome="refuse", reason=long)
    assert reason(stand.call()) == long


# --- supervision --------------------------------------------------------------


def test_oversize_drained_not_killed(stand: Stand, no_signals: list[object]) -> None:
    """1 MiB of output, then the script exits by itself: `oversize`, and it
    was never signalled (it got to write its marker after the bulk)."""
    stand.answer(
        "sys.stdout.buffer.write(b'x' * (1 << 20)); sys.stdout.flush()\n"
        f"open({str(stand.done)!r}, 'w').close()"
    )
    assert reason(stand.call()) == "precheck_failed: oversize"
    assert stand.done.exists() and no_signals == []


def test_oversize_with_a_valid_verdict_first_refused(stand: Stand) -> None:
    stand.answer(PRINT + "\nsys.stdout.write(' ' * (70 << 10))")
    assert reason(stand.call()) == "precheck_failed: oversize"


def test_wait_expiry_is_timeout_without_signal(
    stand: Stand, no_signals: list[object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(precheck, "PRECHECK_WAIT", 0.3)
    stand.answer(f"time.sleep(1.5)\nopen({str(stand.done)!r}, 'w').close()\n{PRINT}")
    started = time.monotonic()
    result = stand.call()
    assert reason(result) == "precheck_failed: timeout"
    assert time.monotonic() - started < 1.5
    assert stand.fake.revoked == [TOKEN]  # revoked without waiting for it
    for _ in range(100):  # never signalled: it lives to its own end
        if stand.done.exists():
            break
        time.sleep(0.05)
    assert stand.done.exists() and no_signals == []


def test_signal_exit_is_timeout(stand: Stand) -> None:
    stand.answer("os.kill(os.getpid(), signal.SIGTERM)")
    assert reason(stand.call()) == "precheck_failed: timeout"


def test_nonzero_exit_refused(stand: Stand, monkeypatch: pytest.MonkeyPatch) -> None:
    stand.answer(PRINT + "\nsys.exit(2)")
    assert reason(stand.call()) == "precheck_failed: exit"

    def late(*_a: object, **_kw: object) -> platform.SupervisedResult:
        return platform.SupervisedResult(1, None, b"", False, 176.0)

    def early(*_a: object, **_kw: object) -> platform.SupervisedResult:
        return platform.SupervisedResult(1, None, b"", False, 174.0)

    monkeypatch.setattr(platform, "run_supervised", late)
    assert reason(stand.call()) == "precheck_failed: timeout"  # sudo's own bound
    monkeypatch.setattr(platform, "run_supervised", early)
    assert reason(stand.call()) == "precheck_failed: exit"


def test_launch_failure_refused(stand: Stand) -> None:
    stand.script.unlink()
    result = stand.call()
    assert reason(result) == "precheck_failed: exit"
    assert stand.fake.revoked == [TOKEN]


# --- the token ----------------------------------------------------------------


def test_read_token_only(stand: Stand) -> None:
    stand.call()
    assert stand.fake.tokens == [
        {"repository_ids": [7], "permissions": dict(ops.READ_PERMISSIONS)}
    ]
    assert stand.sent()["token"] == TOKEN


def _raise(*_a: object, **_kw: object) -> platform.SupervisedResult:
    raise RuntimeError("supervisor broke")


@pytest.mark.parametrize("case", ["allow", "exit", "binding", "exception"])
def test_token_revoked_after_call_even_on_failure(
    stand: Stand, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    if case == "exit":
        stand.answer("sys.exit(1)")
    elif case == "binding":
        stand.answer(nonce="0" * 32)
    elif case == "exception":
        monkeypatch.setattr(platform, "run_supervised", _raise)
        with pytest.raises(RuntimeError):
            stand.call()
        assert stand.fake.revoked == [TOKEN]
        return
    result = stand.call()
    assert stand.fake.revoked == [TOKEN] and result.revoke == "ok"
    deletes = [i for i, (m, _p, _d) in enumerate(stand.fake.calls) if m == "DELETE"]
    assert deletes == [len(stand.fake.calls) - 1]  # the last call


def test_revoke_failure_is_reported_without_token(stand: Stand) -> None:
    stand.fake.revoke_reply = _reply({"message": f"bad {TOKEN}"}, 500)
    result = stand.call()
    assert result.refused is None
    assert result.revoke == "github_unavailable: http 500"
    stand.fake.revoke_reply = _reply({"message": f"no such token {TOKEN}"}, 404)
    result = stand.call()
    assert result.revoke.startswith("github_404: ") and TOKEN not in result.revoke


def test_token_absent_from_result_and_evidence(stand: Stand) -> None:
    """A precheck echoing the token in its reason does not carry it on."""
    stand.answer('verdict.update(outcome="refuse", reason="leak " + TOKEN)\n' + PRINT)
    result = stand.call()
    assert reason(result) == "leak [token]"
    assert TOKEN not in repr(result)
    assert TOKEN not in json.dumps(result.evidence())
    assert set(result.evidence()) == {
        "version",
        "admin_digest",
        "revoke",
        "request_digest",
    }


def test_token_not_minted_for_bad_facts(stand: Stand) -> None:
    """Requirement 4: the publisher holds its own checks; nothing is asked
    for a PR of another repository, and no token is minted."""
    with pytest.raises(Refused) as info:
        stand.call(PrFacts(8, "stop/ws-n1-g2", "master", TIP, FACTS.changes))
    assert info.value.reason == "head_repo_foreign"
    assert stand.fake.tokens == [] and not stand.capture.exists()


def test_only_stop_is_asked(stand: Stand) -> None:
    with pytest.raises(ValueError, match="candidate"):
        precheck.admin_precheck("candidate", stand.oc(), PR, FACTS)
    assert stand.fake.tokens == []


def test_request_over_the_limit_not_sent(stand: Stand) -> None:
    many = tuple(("added", "x" * 300 + str(i), None) for i in range(250))
    result = stand.call(PrFacts(7, "stop/ws-n1-g2", "master", TIP, many))
    assert reason(result) == "precheck_request_too_large"
    assert not stand.capture.exists() and stand.fake.revoked == [TOKEN]


# --- the module ---------------------------------------------------------------


def test_publisher_precheck_stdlib_only() -> None:
    """The key holder imports no third-party code (decision 1): stdlib and
    `df` only, `governance` neither directly nor through `df`."""
    tree = ast.parse(Path(precheck.__file__).read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split(".")[0])
    assert names - {"__future__", "df"} <= set(sys.stdlib_module_names)
    code = (
        "import sys, df.publisher.precheck; "
        "print(sorted(m for m in sys.modules if m.split('.')[0] in "
        "('governance', 'yaml', 'steward')))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert out.stdout.strip() == "[]"


def test_protocol_constants_equal_the_precheck() -> None:
    """The repeated half of the protocol and the three rungs stay equal."""
    from df import cli, sudoers

    shape = act_precheck.verdict(
        act_precheck.parse_request(_valid_request()), "allow", "", ADMIN
    )
    assert set(shape) == set(precheck._VERDICT_CHECKS)
    assert precheck.parse_verdict((json.dumps(shape) + "\n").encode()) == shape
    assert precheck.KINDS == act_precheck.KINDS
    assert precheck.REQUEST_SCHEMA == act_precheck.REQUEST_SCHEMA
    assert precheck.VERDICT_SCHEMA == act_precheck.VERDICT_SCHEMA
    assert precheck.MAX_REASON == act_precheck.MAX_REASON + len(act_precheck.TRUNCATED)
    assert precheck.LIMIT == act_precheck.MAX_REQUEST
    assert dict(precheck.PRECHECK_ENV) == cli.PRECHECK_ENV
    assert (
        act_precheck.DEADLINE
        < precheck.SUDO_TIMEOUT_AFTER
        < sudoers.PRECHECK_TIMEOUT
        < precheck.PRECHECK_WAIT
    )


def _valid_request() -> bytes:
    request = precheck._request(
        "stop", _bare_oc(), 12, SHA, FACTS, TOKEN
    )  # no network: only the dict
    assert set(request) == set(act_precheck.REQUEST_KEYS)
    return json.dumps(request).encode()


def _bare_oc() -> OpContext:
    return OpContext(
        Layout(Path("/")),
        PRODUCT,
        Path("/k"),
        Path("/o"),
        github.default_transport,
        0.0,
        github.API,
        {7: PRODUCT},
        "https://github.com",
    )


def test_request_digest_equals_the_precheck_rule() -> None:
    data = json.loads(_valid_request())
    data["changes"][0][1] = "workstreams/ws/approvals/stops/ü-g2.yaml"
    assert precheck.request_digest(data) == act_precheck.request_digest(data)


# --- git_base through the frame -----------------------------------------------


def _probe(ctx: Context) -> Response:
    """A `merge` stand-in: the frame's `OpContext` straight into the client."""
    oc = ops.build_context(ctx)
    result = precheck.admin_precheck("stop", oc, PR, FACTS)
    return Response("done", None, {"refused": reason(result)})


@pytest.mark.parametrize(
    "git_base", [None, "http://127.0.0.1:9418"], ids=["default", "custom"]
)
def test_git_base_default_and_custom_reach_request(
    stand: Stand,
    world: World,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    git_base: str | None,
) -> None:
    path = world.lay.etc / "publisher.toml"
    cfg = parse_publisher(path.read_text())
    if git_base is not None:
        from dataclasses import replace

        path.write_text(dump_publisher(replace(cfg, git_base=git_base)))
    monkeypatch.setitem(pmain.OPERATION_TABLE, "merge", _probe)
    _, resp = world.run("merge", raw_of("merge"), capsys=capsys)
    assert (resp.outcome, resp.result) == ("done", {"refused": None})
    assert stand.sent()["git_base"] == (git_base or "https://github.com")


def test_git_base_invalid_refused_by_frame(
    stand: Stand,
    world: World,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = world.lay.etc / "publisher.toml"
    text = path.read_text()
    assert text.startswith('schema = "df/publisher/v1"\n')
    path.write_text(text.replace("\n", '\ngit_base = "http://github.com"\n', 1))
    monkeypatch.setitem(pmain.OPERATION_TABLE, "merge", _probe)
    _, resp = world.run("merge", raw_of("merge"), capsys=capsys)
    assert (resp.outcome, resp.reason) == ("refused", "publisher_config_invalid")
    assert not stand.capture.exists()
