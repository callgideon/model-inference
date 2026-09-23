"""W-side deterministic fakes: a *scripted vLLM server*, not a second engine.

`infrx.contracts.fakes.engine.FakeEngine` stands in for the whole port when another
track needs one. What W needs is the opposite: the **real** adapter, with the engine
replaced by an `httpx.MockTransport` whose SSE script is chosen per fault. No
network, no GPU, no sleeps - the clock only moves because the script moves it, so a
stall test costs microseconds and the timer really is enforced.

The content pieces are imported from the shared fake, so the same bytes exercise both
adapters and the exported conformance assertions (`text`, the split `<think>`
delimiters, "Two people unload boxes.") mean the same thing here.

Fault modes (04-verification.md's list, plus the ones only an HTTP adapter has and the
malformed payloads a review of the adapter found unhandled):

| `fault` | What the upstream does |
|---|---|
| `none` | progress, deltas, finish, one authoritative usage, `[DONE]` |
| `split_reasoning_delimiters` | the same, with `<think>` split across deltas |
| `split_tokens` | one character per delta, so every boundary is exercised |
| `role_first` | vLLM's real first chunk: `{"role": "assistant", "content": ""}` |
| `slow_deltas` | deltas spaced under the stall budget: a slow answer is not a stall |
| `prefill_stall` | headers, then keepalive comments that advance the clock past TTFT |
| `midstream_stall` | one delta, then the same past the inter-event budget |
| `missing_usage` | deltas and `[DONE]`, no usage object |
| `malformed_usage` | a usage object with a string and a null |
| `inconsistent_usage` | a usage object whose `total_tokens` does not add up |
| `string_usage` / `negative_usage` / `bool_usage` / `nondict_usage` | counts that are not counts |
| `usage_then_delta` | usage, then more content: the count cannot be authoritative |
| `usage_below_deltas` | a usage object reporting fewer tokens than there were deltas |
| `prompt_out_of_range` | a reported prompt count larger than the whole context |
| `conflicting_usage` | two different usage objects in one stream |
| `valid_then_malformed` / `malformed_then_valid` | one of each, in both orders |
| `repeated_usage` | the *same* usage object twice: authoritative, and one event |
| `abort_finish` | `finish_reason: "abort"` - finished, but not a finish we accept |
| `over_ceiling` | usage claiming more completion tokens than were allowed |
| `runaway_output` | more output than the envelope can hold |
| `huge_delta` | one delta far larger than a journal event |
| `surrogate_delta` | content that cannot be serialised (an unpaired surrogate) |
| `bad_choices` / `bad_choice` / `bad_delta` / `bad_content` | answer-carrying fields of the wrong type |
| `null_error_message` | `{"error": {"message": null}}`, a plausible real shape |
| `stray_object` | a JSON object that is neither content, usage nor error |
| `second_choice` | a choice at `index: 1`, which `n=1` says cannot exist |
| `split_json` | one JSON object spread over two `data:` lines |
| `engine_error_pre_headers` | HTTP 500 with a streamed, oversized error body |
| `engine_error_post_headers` | 200, one delta, then an SSE error object |
| `engine_error_before_content` | 200, the role chunk, then an SSE error object and no visible delta: vLLM refusing an item it cannot encode (the 2026-09-23 box sweep) |
| `abrupt_exit` | 200, one delta, then the connection dies (`httpx.ReadError`) |
| `read_timeout` | 200, one delta, then `httpx.ReadTimeout` |
| `transport_error` / `connect_timeout` | refused / timed out before any response |
| `os_error` | a non-httpx exception mid-stream |
| `cancellation_race` | a long stream, so a cancellation lands mid-generation |
| `truncated` | deltas, then a clean end with no `[DONE]` and no finish reason |
| `split_utf8` | a CJK/emoji delta whose bytes are cut mid-character between chunks |
| `no_trailing_newline` | a well-formed stream whose last line has no `\n` |
| `long_legal_line` | one legal 8 KiB `data:` line straddling two chunks |
| `bare_newlines` | a chunk of 50,000 bare newlines: many lines, one split pass |
| `bom_first` | a byte-order mark before the first `data:` |
| `partial_tail` | the ordinary TCP shape: whole frames plus the start of the next one |
| `sse_fields` | `event:`/`id:`/`retry:` lines, which are legal and not junk |
| `no_newline_flood` | `data:` with no newline in it, in 1 MiB chunks, more than the cap |
| `slow_flood` | the same without a newline, small chunks, 30 s of clock per chunk |
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

FAKE_MEDIA_ROOT = "/srv/infrx/processing"       # a PROCESSING_CACHE_DIR for scripted cases
SERVED_MODEL = "marlin2b"           # vLLM's `--served-model-name`, as F1 sends it
ENGINE_VERSION = "0.11.0"
# 7 s, deliberately not a divisor of the 60 s TTFT or the 20 s stall budget: a
# conformance case asserts that *more* than the budget elapsed, and a keepalive
# landing exactly on the deadline would make that comparison an equality.
KEEPALIVE_S = 7.0
# A delta every 7 s is a slow engine, not a stalled one (the budget is 20 s).
SLOW_DELTA_S = 7.0
ERROR_BODY_CHUNK = 64 * 1024
ERROR_BODY_CHUNKS = 32              # 2 MiB: an adapter that reads it whole is unbounded


# The engine's own text for the sweep's refusal (vllm/v1/engine/input_processor.py:512-519
# at the pinned build, with the counts the 2026-09-23 box run reported).
ENCODER_REFUSAL = ("The decoder prompt contains a(n) video item with 21504 embedding tokens, "
                   "which exceeds the pre-allocated encoder cache size 16384. Please reduce "
                   "the input size or increase the encoder cache size by setting "
                   "--limit-mm-per-prompt at startup.")


def sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj)}\n\n".encode()


def chunk(content: str | None = None, *, finish_reason: str | None = None,
          usage: object = None, role: bool = False) -> dict:
    """One OpenAI-shaped streaming chunk, as vLLM emits it."""
    delta: dict = {}
    if role:
        delta["role"] = "assistant"
    if content is not None:
        delta["content"] = content
    choice = {"index": 0, "delta": delta, "finish_reason": finish_reason}
    body = {"id": "chatcmpl-fake", "object": "chat.completion.chunk", "model": SERVED_MODEL,
            "choices": [] if (not delta and finish_reason is None) else [choice]}
    if usage is not None:
        body["usage"] = usage
    return body


@dataclass
class FakeUpstream:
    """A scripted engine behind `httpx.MockTransport`.

    Observable afterwards: `requests` (every body the adapter sent, for translation
    assertions), `pings` (how many keepalives the adapter waited through before its timer
    fired), `frames_sent`/`chunks_sent` (how much of the script the adapter actually
    **pulled** - the honest measure of "it stopped reading"), `completed` (whether the
    script ran to its end), `error_bytes` (how much of an oversized error body was read).

    `closed` **is** proof now: the body is an `httpx.AsyncByteStream` with its own `aclose`,
    which httpx calls when the response closes, so a case can assert - inside the coroutine -
    that the adapter closed its side. (Passing an async generator as `content=` does not give
    this: httpx never closes it, and `closed` only flipped when the loop finalised the
    abandoned generator at shutdown, which is why the round-2 assertion proved nothing.)
    Whether the real socket closing stops generation in vLLM is still a Layer-3 fact for W3.
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
    filler: str = "x"                   # what `huge_delta` is made of: ASCII, CJK, emoji…
    huge_delta_points: int = 0          # code points in that delta (0 = derive from limits)
    health_status: int = 200
    health_unreachable: bool = False
    requests: list = field(default_factory=list)
    pings: int = 0
    closed: bool = False
    completed: bool = False
    error_bytes: int = 0
    chunks_sent: int = 0                # how many network chunks the adapter actually pulled
    frames_sent: int = 0                # how many SSE frames it pulled
    flood_chunk_bytes: int = 1024 * 1024
    # 24 MiB on offer, not the review's 200: the assertion is "how many chunks did the
    # adapter pull", so the fake only has to be able to give it more than the cap allows.
    flood_chunks: int = 24
    flood_clock_s: float = 0.0

    # --- the script -----------------------------------------------------------
    def deltas(self) -> tuple[str, ...]:
        if self.fault == "split_reasoning_delimiters":
            return SPLIT_REASONING
        if self.fault == "split_tokens":
            return tuple(self.text)
        if self.fault == "cancellation_race":
            return tuple("tok " for _ in range(self.long_stream_deltas))
        if self.fault == "surrogate_delta":
            return ("ok", "\ud83d")
        if self.fault == "huge_delta":
            # A delta whose *encoded* size needs several events, in whatever script the
            # case chose: the bound is bytes, not code points.
            points = self.huge_delta_points or self.limits.journal_event_max_bytes
            # exactly `points` code points, whatever the filler's length
            return ((self.filler * points)[:points],)
        if self.fault == "held_tail_flood":
            # All whitespace: the filter holds every character until the stream ends, so the
            # whole answer arrives as the final tail event.
            return (" " * (self.huge_delta_points or 200_000),)
        if self.fault == "runaway_output":
            return tuple("y" * 4096 for _ in range(64))
        return tuple(self.text[i:i + self.chunk_size]
                     for i in range(0, len(self.text), self.chunk_size))

    def usage(self, produced: int) -> object:
        if self.fault == "malformed_usage":
            return {"prompt_tokens": "1200", "completion_tokens": None}
        if self.fault == "string_usage":
            return {"prompt_tokens": self.prompt_tokens, "completion_tokens": str(produced)}
        if self.fault == "negative_usage":
            return {"prompt_tokens": self.prompt_tokens, "completion_tokens": -produced}
        if self.fault == "bool_usage":
            return {"prompt_tokens": self.prompt_tokens, "completion_tokens": True}
        if self.fault == "nondict_usage":
            return 5
        if self.fault == "inconsistent_usage":
            return {"prompt_tokens": self.prompt_tokens, "completion_tokens": produced,
                    "total_tokens": self.prompt_tokens + produced + 4}
        if self.fault == "over_ceiling":
            return {"prompt_tokens": self.prompt_tokens, "completion_tokens": 100_000,
                    "total_tokens": self.prompt_tokens + 100_000}
        if self.fault == "prompt_out_of_range":
            return {"prompt_tokens": self.limits.max_context_tokens + 7_000,
                    "completion_tokens": produced}
        if self.fault == "usage_below_deltas":
            return {"prompt_tokens": self.prompt_tokens, "completion_tokens": 1,
                    "total_tokens": self.prompt_tokens + 1}
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

    def _misshapen(self) -> dict | None:
        """The payloads whose answer-carrying fields have the wrong type, plus the two
        that must simply be survived."""
        return {
            "bad_choices": {"choices": 5},
            "bad_choice": {"choices": [5]},
            "bad_delta": {"choices": [{"index": 0, "delta": "abc"}]},
            "bad_content": {"choices": [{"index": 0, "delta": {"content": ["abc"]}}]},
            "null_error_message": {"error": {"message": None}},
            "stray_object": {"message": 123},
            "second_choice": {"choices": [{"index": 1, "delta": {"content": "other sample"}}]},
        }.get(self.fault)

    async def _flood(self):
        """A `data:` line that never ends: what an adapter buffering by line swallows whole
        (the review measured 200 MiB pulled, 800 MiB peak, before anything was checked)."""
        yield b'data: {"choices":[{"index":0,"delta":{"content":"'
        self.chunks_sent += 1
        for _ in range(self.flood_chunks):
            if self.flood_clock_s:
                self.clock.advance(self.flood_clock_s)
            self.chunks_sent += 1
            yield b"y" * self.flood_chunk_bytes

    def _body(self, frames) -> httpx.AsyncByteStream:
        """The response body as a real `AsyncByteStream`, so `aclose` is observable."""
        upstream = self

        class _CountedBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                async for frame in frames:
                    upstream.frames_sent += 1
                    yield frame

            async def aclose(self) -> None:
                upstream.closed = True
                await frames.aclose()

        return _CountedBody()

    async def _byte_script(self):
        """Faults that are about *bytes*, so they are yielded as chunks rather than frames:
        where the chunk boundaries fall is the whole point. `produced` is the number of
        content deltas the script really sent, so the usage it reports is consistent with the
        stream (an inconsistent one is `below_delta_count`, which is a different case).
        """
        produced = 1
        if self.fault == "split_utf8":
            # `ensure_ascii=False`, so the frame really carries multi-byte UTF-8 rather than
            # `\uXXXX` escapes - that is the point of the fault.
            body = (f"data: {json.dumps(chunk('日本語です😀'), ensure_ascii=False)}\n\n"
                    ).encode()
            cut = body.index("日".encode()) + 1            # mid-character, deliberately
            yield body[:cut]
            yield body[cut:]
        elif self.fault == "long_legal_line":
            # 8 KiB in one line, in 3 KiB chunks: the pending buffer passes 4 KiB - which a
            # cap of "one buffer's worth" would refuse - while the line is perfectly legal.
            body = sse(chunk("z" * 8192))
            for at in range(0, len(body), 3000):
                yield body[at:at + 3000]
        elif self.fault == "partial_tail":
            produced = 3
            # What a real read looks like: a complete frame (or two) and then the *beginning*
            # of the next one, so the buffer holds a tail that must survive to the next chunk.
            first, second, third = (sse(chunk(piece)) for piece in ("alpha ", "beta ", "gamma"))
            yield first + second[:18]
            yield second[18:] + third[:12]
            yield third[12:]
        elif self.fault == "sse_fields":
            yield b"event: message\n"
            yield b"id: 42\n"
            yield b"retry: 3000\n\n"
            yield sse(chunk(self.text[:5]))
        elif self.fault == "bare_newlines":
            produced = 0
            yield b"\n" * 50_000
        elif self.fault == "bom_first":
            produced = 0                                 # the BOM line is never content
            yield b"\xef\xbb\xbf" + sse(chunk(self.text[:4]))
        yield sse(chunk(finish_reason="stop"))
        yield sse(chunk(usage=self.usage(produced)))
        if self.fault == "no_trailing_newline":
            yield b"data: [DONE]"                        # no newline, no blank line
        else:
            yield b"data: [DONE]\n\n"

    async def _stream(self):
        pieces = self.deltas()
        try:
            if self.fault in ("split_utf8", "long_legal_line", "bare_newlines", "bom_first",
                              "no_trailing_newline", "partial_tail", "sse_fields"):
                if self.fault == "no_trailing_newline":
                    yield sse(chunk(pieces[0]))
                async for frame in self._byte_script():
                    yield frame
                self.completed = True
                return
            if self.fault in ("no_newline_flood", "slow_flood"):
                async for frame in self._flood():
                    yield frame
                self.completed = True
                return
            if self.fault == "split_json":
                # The same object, cut in half across two `data:` lines: neither half parses,
                # so the content is dropped and the answer is not whole.
                body = json.dumps(chunk(pieces[0]))
                yield f"data: {body[:len(body) // 2]}\n\n".encode()
                yield f"data: {body[len(body) // 2:]}\n\n".encode()
                yield sse(chunk(finish_reason="stop"))
                yield sse(chunk(usage=self.usage(1)))
                yield b"data: [DONE]\n\n"
                self.completed = True
                return
            if self.fault == "role_first":
                yield sse(chunk("", role=True))
            if self.fault == "engine_error_before_content":
                yield sse(chunk("", role=True))
                yield sse({"error": {"message": ENCODER_REFUSAL, "type": "BadRequestError",
                                     "code": 400}})
                self.completed = True
                return
            if self.fault == "prefill_stall":
                async for frame in self._keepalives():
                    yield frame
                self.completed = True
                return
            misshapen = self._misshapen()
            if misshapen is not None:
                yield sse(chunk(pieces[0]))
                yield sse(misshapen)
                if self.fault == "stray_object":
                    # Nothing to act on, so the stream must still complete normally.
                    yield sse(chunk(finish_reason="abort" if self.fault == "abort_finish" else "stop"))
                    yield sse(chunk(usage=self.usage(1)))
                    yield b"data: [DONE]\n\n"
                self.completed = True
                return
            for index, piece in enumerate(pieces):
                if self.fault == "slow_deltas":
                    self.clock.advance(SLOW_DELTA_S)
                yield sse(chunk(piece))
                if self.fault == "usage_then_delta" and index == 0:
                    # A count arriving before the answer is finished cannot be the count.
                    yield sse(chunk(usage={"prompt_tokens": self.prompt_tokens,
                                           "completion_tokens": 1,
                                           "total_tokens": self.prompt_tokens + 1}))
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
                if index == 0 and self.fault == "os_error":
                    raise OSError("the socket is gone")
            if self.fault == "truncated":
                self.completed = True
                return                                  # no [DONE], no finish_reason
            yield sse(chunk(finish_reason="abort" if self.fault == "abort_finish" else "stop"))
            if self.fault == "malformed_then_valid":
                yield sse(chunk(usage={"prompt_tokens": None, "completion_tokens": "7"}))
            if self.fault not in ("missing_usage", "usage_then_delta"):
                # `usage_then_delta` sends its only usage object mid-stream, so the
                # inconsistency under test is the delta that followed it, not a second
                # object (which is `conflicting_usage`).
                yield sse(chunk(usage=self.usage(len(pieces))))
            if self.fault == "repeated_usage":
                yield sse(chunk(usage=self.usage(len(pieces))))      # the identical object
            if self.fault == "valid_then_malformed":
                yield sse(chunk(usage={"prompt_tokens": "1200", "completion_tokens": None}))
            if self.fault == "conflicting_usage":
                yield sse(chunk(usage={"prompt_tokens": self.prompt_tokens + 7,
                                       "completion_tokens": len(pieces),
                                       "total_tokens": self.prompt_tokens + 7 + len(pieces)}))
            yield b"data: [DONE]\n\n"
            self.completed = True
        finally:
            self.closed = True

    async def _error_body(self):
        """A 2 MiB error body, streamed: an adapter that reads it whole is unbounded, and
        `error_bytes` says how much it actually took."""
        head = b'{"error": {"message": "engine died: /dev/nvidia0 '
        self.error_bytes += len(head)
        yield head
        for _ in range(ERROR_BODY_CHUNKS):
            part = b"trace " * (ERROR_BODY_CHUNK // 6)
            self.error_bytes += len(part)
            yield part
        tail = b'", "type": "server_error"}}'
        self.error_bytes += len(tail)
        yield tail

    # --- transport ------------------------------------------------------------
    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            if self.health_unreachable:
                raise httpx.ConnectError("connection refused", request=request)
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
        if self.fault == "connect_timeout":
            raise httpx.ConnectTimeout("the engine did not accept the connection",
                                       request=request)
        if self.fault == "engine_error_pre_headers":
            return httpx.Response(500, stream=self._body(self._error_body()))
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              stream=self._body(self._stream()))

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url="http://engine.invalid",
                                 transport=httpx.MockTransport(self.handle))

    def engine(self, **kw) -> VllmEngine:
        # A deployment sets PROCESSING_CACHE_DIR; the scripted upstream stands in for one that
        # did, unless the case configured its own root.
        limits = self.limits if self.limits.processing_cache_dir \
            else self.limits.replace(processing_cache_dir=FAKE_MEDIA_ROOT)
        kw.setdefault("local_uri", m2_local_uri(limits.processing_cache_dir))
        return VllmEngine(self.client(), served_model=self.served_model, clock=self.clock,
                          limits=limits, path=self.path, **kw)


def m2_local_uri(root: str):
    """M2's `MediaPreparation.local_uri` layout without a processing cache (R61 (2)):
    `file://<root>/<org>/<profile>/<digest16>/source.mp4`. A stand-in, not a copy - the
    adapter checks what M2 returns rather than trusting this shape."""
    return lambda ref: (f"file://{root}/{ref.org_id}/{ref.profile_version}/"
                        f"{ref.digest.removeprefix('sha256:')[:16]}/source.mp4")


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
