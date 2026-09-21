"""W-side deterministic fakes: a *scripted vLLM server*, not a second engine.

`infrx.contracts.fakes.engine.FakeEngine` stands in for the whole port when another
track needs one. What W needs is the opposite: the **real** adapter, with the engine
replaced by an `httpx.MockTransport` whose SSE script is chosen per fault. No
network, no GPU, no sleeps - the clock only moves because the script moves it, so a
stall test costs microseconds and the timer really is enforced.

The content pieces are imported from the shared fake, so the same bytes exercise both
adapters and the exported conformance assertions (`text`, the split `<think>`
delimiters, "Two people unload boxes.") mean the same thing here.

Fault modes (04-verification.md's list, plus the ones only an HTTP adapter has):

| `fault` | What the upstream does |
|---|---|
| `none` | progress, deltas, one authoritative usage, `[DONE]` |
| `split_reasoning_delimiters` | the same, with `<think>` split across deltas |
| `split_tokens` | one character per delta, so every boundary is exercised |
| `prefill_stall` | headers, then keepalive comments that advance the clock past TTFT |
| `midstream_stall` | one delta, then the same past the inter-event budget |
| `missing_usage` | deltas and `[DONE]`, no usage object |
| `malformed_usage` | a usage object with a string and a null |
| `inconsistent_usage` | a usage object whose `total_tokens` does not add up |
| `over_ceiling` | usage claiming more completion tokens than were allowed |
| `engine_error_pre_headers` | HTTP 500 with an error body, before any event |
| `engine_error_post_headers` | 200, one delta, then an SSE error object |
| `abrupt_exit` | 200, one delta, then the connection dies (`httpx.ReadError`) |
| `read_timeout` | 200, one delta, then `httpx.ReadTimeout` |
| `transport_error` | the connection is refused before any response |
| `cancellation_race` | a long stream, so a cancellation lands mid-generation |
| `truncated` | deltas, then a clean end with no `[DONE]` and no finish reason |
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx

from ..contracts.conformance import Harness
from ..contracts.fakes.engine import DEFAULT_TEXT, SPLIT_REASONING
from ..contracts.fakes.support import FakeClock, SequentialIds
from ..contracts.limits import DEFAULTS, PilotSettings
from .engine import VllmEngine

SERVED_MODEL = "marlin2b"           # vLLM's `--served-model-name`, as F1 sends it
ENGINE_VERSION = "0.11.0"
# 7 s, deliberately not a divisor of the 60 s TTFT or the 20 s stall budget: a
# conformance case asserts that *more* than the budget elapsed, and a keepalive
# landing exactly on the deadline would make that comparison an equality.
KEEPALIVE_S = 7.0


def sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj)}\n\n".encode()


def chunk(content: str | None = None, *, finish_reason: str | None = None,
          usage: object = None) -> dict:
    """One OpenAI-shaped streaming chunk, as vLLM emits it."""
    choice: dict = {"index": 0, "delta": {} if content is None else {"content": content},
                    "finish_reason": finish_reason}
    body = {"id": "chatcmpl-fake", "object": "chat.completion.chunk", "model": SERVED_MODEL,
            "choices": [] if content is None and finish_reason is None else [choice]}
    if usage is not None:
        body["usage"] = usage
    return body


@dataclass
class FakeUpstream:
    """A scripted engine behind `httpx.MockTransport`.

    Observable afterwards: `requests` (every body the adapter sent, for translation
    assertions), `pings` (how many keepalives the adapter waited through before its
    timer fired), `closed`/`completed` (whether the adapter closed the stream early,
    which is how cancellation is proved to reach the engine).
    """

    fault: str = "none"
    clock: FakeClock = field(default_factory=FakeClock)
    limits: PilotSettings = DEFAULTS
    text: str = DEFAULT_TEXT
    chunk_size: int = 12
    prompt_tokens: int = 1200
    path: str = "/v1/chat/completions"
    served_model: str = SERVED_MODEL
    version: str = ENGINE_VERSION
    keepalive_s: float = KEEPALIVE_S
    max_keepalives: int = 40
    long_stream_deltas: int = 200
    requests: list = field(default_factory=list)
    pings: int = 0
    closed: bool = False
    completed: bool = False
    health_status: int = 200

    # --- the script -----------------------------------------------------------
    def deltas(self) -> tuple[str, ...]:
        if self.fault == "split_reasoning_delimiters":
            return SPLIT_REASONING
        if self.fault == "split_tokens":
            return tuple(self.text)
        if self.fault == "cancellation_race":
            return tuple("tok " for _ in range(self.long_stream_deltas))
        return tuple(self.text[i:i + self.chunk_size]
                     for i in range(0, len(self.text), self.chunk_size))

    def usage(self, produced: int) -> object:
        if self.fault == "malformed_usage":
            return {"prompt_tokens": "1200", "completion_tokens": None}
        if self.fault == "inconsistent_usage":
            return {"prompt_tokens": self.prompt_tokens, "completion_tokens": produced,
                    "total_tokens": self.prompt_tokens + produced + 4}
        if self.fault == "over_ceiling":
            return {"prompt_tokens": self.prompt_tokens, "completion_tokens": 100_000,
                    "total_tokens": self.prompt_tokens + 100_000}
        return {"prompt_tokens": self.prompt_tokens, "completion_tokens": produced,
                "total_tokens": self.prompt_tokens + produced}

    async def _keepalives(self):
        """Comments that cost time and carry no event: what a stalled engine looks
        like from the outside, and the only way a stall timer can be *observed* firing
        without waiting on the wall clock."""
        for _ in range(self.max_keepalives):
            self.clock.advance(self.keepalive_s)
            self.pings += 1
            yield b": keepalive\n\n"

    async def _stream(self):
        pieces = self.deltas()
        try:
            if self.fault == "prefill_stall":
                async for frame in self._keepalives():
                    yield frame
                self.completed = True
                return
            for index, piece in enumerate(pieces):
                yield sse(chunk(piece))
                if self.fault == "midstream_stall" and index == 0:
                    async for frame in self._keepalives():
                        yield frame
                    self.completed = True
                    return
                if index == 0 and self.fault == "engine_error_post_headers":
                    yield sse({"error": {"message": "CUDA out of memory", "type": "server_error"}})
                    self.completed = True
                    return
                if index == 0 and self.fault == "abrupt_exit":
                    raise httpx.ReadError("engine process exited")
                if index == 0 and self.fault == "read_timeout":
                    raise httpx.ReadTimeout("no tokens within the read timeout")
            if self.fault == "truncated":
                self.completed = True
                return                                  # no [DONE], no finish_reason
            yield sse(chunk(finish_reason="stop"))
            if self.fault != "missing_usage":
                yield sse(chunk(usage=self.usage(len(pieces))))
            yield b"data: [DONE]\n\n"
            self.completed = True
        finally:
            self.closed = True

    # --- transport ------------------------------------------------------------
    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            return httpx.Response(self.health_status, json={"status": "ok"})
        if path == "/version":
            return httpx.Response(200, json={"version": self.version})
        if path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": self.served_model}]})
        if path != self.path:
            return httpx.Response(404, json={"error": {"message": "no such route"}})
        self.requests.append(json.loads(request.content))
        if self.fault == "transport_error":
            raise httpx.ConnectError("connection refused", request=request)
        if self.fault == "engine_error_pre_headers":
            return httpx.Response(500, json={"error": {"message": "engine died: /dev/nvidia0",
                                                       "type": "server_error"}})
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=self._stream())

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url="http://engine.invalid",
                                 transport=httpx.MockTransport(self.handle))

    def engine(self, **kw) -> VllmEngine:
        return VllmEngine(self.client(), served_model=self.served_model, clock=self.clock,
                          limits=self.limits, path=self.path, **kw)


def engine_factory(limits: PilotSettings | None = None, *, fault: str = "none",
                   **kw: object) -> Harness:
    """The `Harness` contract for the **real** adapter: the same factory shape the
    fakes publish (`contracts/fakes/factories.py`), so
    `run_engine_conformance(engine_factory)` runs the exported suite against it.

    `failures` is `None` on purpose: fault injection here is the `fault` script, and
    the engine suite reads no `failures` hook - claiming one the adapter never
    consults would be a hook that hides a skip (r1 R32).
    """
    upstream = FakeUpstream(fault=fault, clock=FakeClock(), limits=limits or DEFAULTS)
    return Harness(port=upstream.engine(), clock=upstream.clock, ids=SequentialIds(),
                   failures=None, extra={"text": upstream.text, "fault": fault,
                                         "upstream": upstream})
