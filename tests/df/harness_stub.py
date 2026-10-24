"""A localhost model-provider stub for the harness isolation tests (11-B).

The stub answers the Anthropic Messages API and the OpenAI Responses API
just enough for one turn of a real harness. It records, per request, only
what the tests assert: the method, the path, the `model` field of a JSON
body, the number of tools offered, the reasoning/effort fields, which of
the test's marker strings the body contained, and two booleans about
credentials — whether the fake key was presented, and whether any OTHER
credential was. Header values and bodies
are never stored, logged or printed: a real credential that leaked into a
request must not leak further into a test log.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Literal

#: The only key the harnesses are ever given in these tests.
FAKE_KEY = "sk-df-isolation-test-0000000000000000"
_CRED_HEADERS = ("x-api-key", "authorization")
#: Reply text of a successful turn.
REPLY = "OK"

Mode = Literal["ok", "http-500"]


@dataclass(frozen=True)
class Seen:
    """One request as the stub recorded it (no header value, no body)."""

    method: str
    path: str
    model: str | None
    tools: int
    effort: str | None
    markers: frozenset[str]
    fake_key: bool
    other_credential: bool


class Stub:
    """A running stub: its base URL, the requests seen and the answer mode."""

    def __init__(self, server: ThreadingHTTPServer) -> None:
        self.server = server
        self.seen: list[Seen] = []
        self.mode: Mode = "ok"
        #: Strings whose presence in a request body is recorded (sentinels).
        self.markers: tuple[str, ...] = ()
        self.lock = threading.Lock()

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host!s}:{port}"

    def models(self) -> list[str]:
        """Models named by requests that carried a model field."""
        return [s.model for s in self.seen if s.model is not None]

    def requests(self) -> list[Seen]:
        """The model requests (a JSON body with a model field)."""
        return [s for s in self.seen if s.model is not None]

    def leaked(self) -> bool:
        """A credential other than the fake key reached the stub."""
        return any(s.other_credential for s in self.seen)


@contextmanager
def running_stub() -> Iterator[Stub]:
    """Start the stub on 127.0.0.1 (ephemeral port); stop it on exit."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    stub = Stub(server)
    server.stub = stub  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield stub
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Silence the default access log (it would print request lines)."""

    def do_GET(self) -> None:  # noqa: N802
        self._handle(b"")

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle(b"")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        self._handle(self.rfile.read(length) if length else b"")

    def _handle(self, body: bytes) -> None:
        stub: Stub = self.server.stub  # type: ignore[attr-defined]
        data = _json_object(body)
        model = data.get("model") if isinstance(data.get("model"), str) else None
        fake, other = _credentials(self.headers.items())
        path = self.path.split("?", 1)[0]
        tools = data.get("tools")
        with stub.lock:
            found = frozenset(m for m in stub.markers if m.encode() in body)
            stub.seen.append(
                Seen(
                    self.command,
                    path,
                    model,
                    len(tools) if isinstance(tools, list) else 0,
                    _effort(data),
                    found,
                    fake,
                    other,
                )
            )
            mode = stub.mode
        if self.command != "POST" or model is None:
            self._send(404, "application/json", b'{"error":"not found"}')
        elif mode == "http-500":
            self._send(500, "application/json", _ERROR_500)
        elif path.endswith("/messages"):
            self._anthropic(model, bool(data.get("stream")))
        elif path.endswith("/responses"):
            self._openai(model)
        else:
            self._send(404, "application/json", b'{"error":"not found"}')

    def _send(self, code: int, ctype: str, payload: bytes) -> None:
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _anthropic(self, model: str, stream: bool) -> None:
        message = {
            "id": "msg_df_stub",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": REPLY}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 3, "output_tokens": 1},
        }
        if not stream:
            self._send(200, "application/json", json.dumps(message).encode())
            return
        start = dict(message, content=[], stop_reason=None)
        events = [
            ("message_start", {"type": "message_start", "message": start}),
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": REPLY},
                },
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 1},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        ]
        self._send(200, "text/event-stream", _sse(events))

    def _openai(self, model: str) -> None:
        item = {
            "id": "msg_df_stub",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": REPLY, "annotations": []}],
        }
        response = {
            "id": "resp_df_stub",
            "object": "response",
            "model": model,
            "status": "completed",
            "output": [item],
            "usage": {
                "input_tokens": 3,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": 1,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 4,
            },
        }
        created = dict(response, status="in_progress", output=[])
        events = [
            ("response.created", {"type": "response.created", "response": created}),
            (
                "response.output_item.done",
                {"type": "response.output_item.done", "output_index": 0, "item": item},
            ),
            (
                "response.completed",
                {"type": "response.completed", "response": response},
            ),
        ]
        self._send(200, "text/event-stream", _sse(events))


_ERROR_500 = json.dumps(
    {"type": "error", "error": {"type": "api_error", "message": "stub failure"}}
).encode()


def _sse(events: list[tuple[str, dict[str, object]]]) -> bytes:
    return b"".join(
        f"event: {name}\ndata: {json.dumps(data)}\n\n".encode() for name, data in events
    )


def _effort(data: dict[str, object]) -> str | None:
    """The effort a request asked for, as compact JSON of the field that
    carries it (Anthropic `output_config`/`thinking`, OpenAI `reasoning`)."""
    for key in ("output_config", "reasoning", "thinking"):
        if key in data:
            return json.dumps({key: data[key]}, sort_keys=True)
    return None


def _json_object(body: bytes) -> dict[str, object]:
    try:
        data = json.loads(body)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _credentials(headers: list[tuple[str, str]]) -> tuple[bool, bool]:
    """`(fake key presented, another credential presented)` — values are
    compared here and dropped; nothing of them leaves this function."""
    fake = other = False
    for name, value in headers:
        if name.lower() not in _CRED_HEADERS:
            continue
        token = value.removeprefix("Bearer ").strip()
        if token == FAKE_KEY:
            fake = True
        elif token:
            other = True
    return fake, other
