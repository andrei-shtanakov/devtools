"""The consumer's side of `perimeter-status` (stage 2a, Task 11)."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from df import perimeter_client
from df import platform as plat
from df.perimeter_client import PerimeterState, RootId, TrustView
from df.publisher import request
from df.publisher.request import Response, perimeter_result

ROOT = RootId("github.com", 5, "main")
S1, S2 = "1" * 40, "2" * 40
T1, T2 = TrustView(S1, 1), TrustView(S2, 2)


def done(state: str, reason: str | None, sha: str | None, epoch: int | None) -> bytes:
    result = perimeter_result(state, reason, sha, epoch)
    return (request.emit(Response("done", None, result)) + "\n").encode()


def answer(outcome: str, reason: str | None = None) -> bytes:
    resp = Response(outcome, reason)  # type: ignore[arg-type]
    return (request.emit(resp) + "\n").encode()


class Publisher:
    """A scripted `call`: each step is the bytes to return or an exception."""

    def __init__(self, *steps: bytes | Exception) -> None:
        self.steps = list(steps)
        self.sent: list[dict[str, object]] = []

    def __call__(self, data: bytes) -> bytes:
        self.sent.append(json.loads(data))
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def ids(self) -> list[object]:
        return [s["request_id"] for s in self.sent]


def no_reload() -> TrustView:
    raise AssertionError("reload not expected")


def run(pub: Publisher, trust: TrustView = T1, **kw: object) -> PerimeterState:
    reload = kw.pop("reload", no_reload)
    assert callable(reload)
    return perimeter_client.status(
        "run-1",
        ROOT,
        trust,
        reload=reload,  # type: ignore[arg-type]
        call=pub,
        **kw,  # type: ignore[arg-type]
    )


def test_client_request_is_a_valid_status_request() -> None:
    pub = Publisher(done("clear", None, S1, 1))
    assert run(pub) == PerimeterState("clear", None)
    raw = json.dumps(pub.sent[0]).encode()
    req = request.parse("perimeter-status", raw)
    assert req.fields["root_id"] == {
        "host": "github.com",
        "repository_id": 5,
        "branch": "main",
    }
    assert req.fields["expected_snapshot_sha"] == S1
    assert req.run_id == "run-1"
    assert "epoch" not in req.fields  # the client never names an epoch


def test_client_blocked() -> None:
    assert run(Publisher(done("blocked", None, S1, 1))) == PerimeterState(
        "blocked", None
    )


def test_client_new_request_id_after_stale() -> None:
    pub = Publisher(
        answer("stale_snapshot", "snapshot_moved"), done("clear", None, S2, 2)
    )
    reloads: list[int] = []

    def reload() -> TrustView:
        reloads.append(1)
        return T2

    assert run(pub, reload=reload) == PerimeterState("clear", None)
    assert reloads == [1]
    first, second = pub.sent
    assert first["request_id"] != second["request_id"]
    assert second["expected_snapshot_sha"] == S2


def test_client_same_request_id_on_retry() -> None:
    pub = Publisher(
        OSError("sudo did not start"),
        b"",  # a non-zero exit without JSON
        b"not json\n",
        done("blocked", None, S1, 1),
    )
    assert run(pub, attempts=4) == PerimeterState("blocked", None)
    assert len(set(pub.ids())) == 1
    assert len(pub.sent) == 4


def test_client_two_lines_are_not_an_answer() -> None:
    line = done("clear", None, S1, 1)
    pub = Publisher(line + line, line)
    assert run(pub) == PerimeterState("clear", None)
    assert len(set(pub.ids())) == 1 and len(pub.sent) == 2


@pytest.mark.parametrize(
    ("sha", "epoch"), [(S2, 1), (S1, 2), (None, 1), (S1, None), (S1, True)]
)
def test_client_snapshot_mismatch_unavailable(sha: str | None, epoch: object) -> None:
    for state in ("clear", "blocked"):
        got = run(Publisher(done(state, None, sha, epoch)))  # type: ignore[arg-type]
        assert got == PerimeterState("unavailable", "snapshot_mismatch")


@pytest.mark.parametrize(
    "reason",
    [
        "no_binding",
        "busy",
        "integrity",
        "lost",
        "not_initialized",
        "restore_pending",
        "epochs_unavailable",
    ],
)
def test_client_keeps_unavailable_reason(reason: str) -> None:
    got = run(Publisher(done("unavailable", reason, None, None)))
    assert got == PerimeterState("unavailable", reason)


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [("refused", "root_mismatch"), ("unknown", "in_flight")],
)
def test_client_other_outcomes_are_unavailable(outcome: str, reason: str) -> None:
    got = run(Publisher(answer(outcome, reason)))
    assert got == PerimeterState("unavailable", f"{outcome}: {reason}")


def test_client_bad_result_is_a_failed_call() -> None:
    bad = (request.emit(Response("done", None, {"state": "open"})) + "\n").encode()
    pub = Publisher(bad, done("clear", None, S1, 1))
    assert run(pub) == PerimeterState("clear", None)
    assert len(set(pub.ids())) == 1


def test_client_attempts_exhausted() -> None:
    pub = Publisher(OSError("x"), OSError("y"), b"")
    assert run(pub) == PerimeterState("unavailable", "attempts_exhausted: 0 lines")
    stale = Publisher(*(answer("stale_snapshot", "snapshot_moved") for _ in range(3)))
    got = run(stale, reload=lambda: T2)
    assert got == PerimeterState("unavailable", "attempts_exhausted: stale_snapshot")
    assert len(set(stale.ids())) == 3


def test_sudo_call(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[list[str], bytes]] = []

    def fake(result: plat.RunResult) -> Callable[..., plat.RunResult]:
        def run_with_input(
            argv: list[str], data: bytes, **_kw: object
        ) -> plat.RunResult:
            seen.append((argv, data))
            return result

        return run_with_input

    line = done("clear", None, S1, 1)
    monkeypatch.setattr(
        plat, "run_with_input", fake(plat.RunResult(2, line, b"", True))
    )
    assert perimeter_client.sudo_call(b"{}") == line
    argv, data = seen[0]
    publish = str(Path("/opt/df/libexec/df-publish"))
    assert argv == ["/usr/bin/sudo", "-n", "-u", "df-publisher", publish,
                    "perimeter-status"]  # fmt: skip
    assert data == b"{}"
    for broken in (
        plat.RunResult(1, b"", b"", False),
        plat.RunResult(-9, line, b"", True, timed_out=True),
        plat.RunResult(0, line, b"", True, stdout_truncated=True),
    ):
        monkeypatch.setattr(plat, "run_with_input", fake(broken))
        with pytest.raises(OSError):
            perimeter_client.sudo_call(b"{}")


def test_client_reload_failure_is_unavailable() -> None:
    def reload() -> TrustView:
        raise ValueError("df-trust unreadable")

    pub = Publisher(answer("stale_snapshot", "snapshot_moved"))
    got = run(pub, reload=reload)
    assert got == PerimeterState("unavailable", "reload_failed")


def test_client_default_call_uses_the_given_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from df.paths import Layout

    lay = Layout(Path(os.path.realpath(tmp_path)))
    seen: list[list[str]] = []

    def run_with_input(argv: list[str], _data: bytes, **_kw: object) -> plat.RunResult:
        seen.append(argv)
        return plat.RunResult(0, done("clear", None, S1, 1), b"", True)

    monkeypatch.setattr(plat, "run_with_input", run_with_input)
    got = perimeter_client.status("run-1", ROOT, T1, reload=no_reload, layout=lay)
    assert got == PerimeterState("clear", None)
    assert seen[0][4] == str(lay.libexec / "df-publish")
