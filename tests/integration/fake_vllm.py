"""A controllable fake vLLM: an ASGI app, a real HTTP server, and the `ports.Engine`
adapter that speaks to it.

Why all three in one file: the server's wire behaviour and the client that decodes it are
one contract. Splitting them invites the drift where the server emits something no client
reads and both halves' tests pass.

Fault modes, exactly the set 04-verification.md §Test environments requires:
`prefill_stall`, `midstream_stall`, `malformed_usage`, `missing_usage`,
`cancellation_race`, `abrupt_exit`, `split_reasoning_delimiters`. The names and the split
delimiters are lifted from `infrx.contracts.fakes.engine` rather than restated, so the
in-memory fake and this HTTP one cannot disagree about what a fault is called or what a
split `<think>` looks like.

**A stall is declared, not waited out.** The stream emits an SSE comment
`: infrx-stall <seconds>` and the adapter advances its injected clock by that much. This
is the same choice the in-memory fake documents ("stalls are expressed as clock advances
between yields, because the timeout policy belongs to W, not to the engine"): the suite
then has no sleeps and no wall-clock flake, and a case asserts how much time passed rather
than that something timed out. `--stall-real-s` adds a real delay on top for a test that
wants to watch a socket actually go quiet.

**A stall ends the stream cleanly** (`data: [DONE]`, no `finish_reason`, no usage) while an
abrupt exit aborts the connection mid-body. The adapter treats those differently on
purpose: a clean end with nothing to settle is an incomplete generation W must reconcile,
and a torn connection is an engine process that died.

Control surface (not vLLM's; prefixed `_control` so it can never be confused for one):
`POST /_control` sets the default fault / drained flag, `POST /_control/cancel` marks a job
cancelled, `GET /_control` reports state. A single request can also select its own fault
with the `X-Infrx-Fault` header or an `infrx_fault` body field, which is what the
conformance factory uses.

Run standalone:  python tests/integration/fake_vllm.py --port 55580
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import tempfile
import sys
import time
import uuid
from pathlib import Path
from random import Random
from typing import AsyncIterator

HERE = Path(__file__).resolve().parent
# r1 review B3: `HERE.parents[1]` is wrong inside a mutation runner's temporary copy - there is
# no `apps/infrx-api` beside it, so the server child died with ModuleNotFoundError in
# setup_module and the runner counted that as a kill (a comment-only edit "died" too). The
# repository root is the same override `harness.py` reads, so a copy points at the real tree.
REPO_ROOT = Path(os.environ.get("INFRX_E2_REPO_ROOT") or HERE.parents[1])
sys.path.insert(0, str(REPO_ROOT / "apps" / "infrx-api"))

from infrx.worker.reasoning import ReasoningFilter
from infrx.contracts.fakes.engine import DEFAULT_TEXT, SPLIT_REASONING, EngineFault  # noqa: E402
from infrx.contracts.fakes.support import FakeClock  # noqa: E402
from infrx.contracts.limits import DEFAULTS, PilotSettings  # noqa: E402
from infrx.contracts.records import (ChunkEventType, EngineEvent, Lease,  # noqa: E402
                                     PreparedRequest, Usage)

STALL_COMMENT = ": infrx-stall "
DONE = "data: [DONE]\n\n"
CHUNK_SIZE = 12


SEEDED_CREATED = 1_758_000_000     # a fixed `created` base when --seed is given
# Time between deltas on the cancellation path only, so a cancel can actually arrive
# mid-stream. Without it the whole body is emitted before any client could send one.
# E2R: was 0.02, which gave the client's cancel POST 4 x 20 ms = 80 ms for a round trip.
# Measured failing on this 16-core host at load average 14.5 (the E2 stack plus the other
# suites running): `assert 5 < 5` - every chunk was out before the cancel landed, so the case
# read as "it did not stop early" when nothing was wrong with the code under test. 0.2 s gives
# that round trip 800 ms. It is still a race rather than a synchronisation; see README.md's
# residual limit. This is the only fault path in this file that waits at all.
CANCEL_GAP_S = 0.2
# PREP-WORKER: vLLM's `POST /tokenize` for a chat body. The count is the one this fake reports
# as `usage.prompt_tokens` (`prompt_tokens`), so preparation's count and the settled usage
# agree as they do on a real engine. A video part is expanded as the pinned profile expands
# it on vLLM (marlin-sop.md §1.5: one `video_token_id` per merged patch, `size.longest_edge
# // 2048` of them for a full-resolution clip) inside that count. `tokenize_fault`:
# `unexpanded` answers ONE placeholder per video (what a tokenizer that skips the multimodal
# processor would say), `down` answers 503.
VIDEO_TOKEN_ID = 248057            # research/models/marlin2b/config.json `video_token_id`
PIXELS_PER_TOKEN = 2048
TOKENIZE_FAULTS = ("none", "unexpanded", "down")


class UnknownFault(ValueError):
    """A caller asked for a fault that does not exist. A 4xx, never a 500 (r1 review)."""

    def __init__(self, named: str) -> None:
        self.named = named
        super().__init__(named)

    def envelope(self) -> dict:
        return {"error": {"message": f"unknown fault {self.named!r}", "type":
                          "invalid_request_error", "code": "unsupported_parameter",
                          "known": [fault.value for fault in EngineFault]}}


class EngineProcessExited(RuntimeError):
    """The engine died mid-request. Deliberately NOT a `DomainError`: an external process
    crashing is not a customer error, and the conformance case asserts that."""


# ============================================================== server (ASGI)

class FakeVllmApp:
    """A minimal OpenAI/vLLM-compatible chat server with scripted faults.

    Raw ASGI rather than FastAPI: aborting a response mid-body is the abrupt-exit fault,
    and at this size a framework only stands between the test and the bytes.
    """

    def __init__(self, *, fault: str = "none", text: str = DEFAULT_TEXT,
                 prompt_tokens: int = 1200, model: str = "infrx-e2/fake-vllm",
                 limits: PilotSettings = DEFAULTS, stall_real_s: float = 0.0,
                 seed: int | None = None, delta_gap_s: float = 0.0) -> None:
        self.default_fault = EngineFault(fault)
        self.text, self.prompt_tokens, self.model = text, prompt_tokens, model
        self.limits, self.stall_real_s = limits, stall_real_s
        self.delta_gap_s = delta_gap_s
        self.tokenize_fault, self.tokenize_delay_s = "none", 0.0
        self.tokenized: list[dict] = []           # every /tokenize body, as received
        self.rng = Random(seed) if seed is not None else None
        self.emitted = 0
        self.drained = False
        self.cancelled: set[str] = set()
        self.seen: list[dict] = []
        # E3B phase 3 (dr11): streams the CLIENT abandoned mid-body - what vLLM sees when a
        # worker cancels a generation (it closes the connection). Never reset.
        self.disconnected = 0

    # ---------------------------------------------------------- scripting

    def deltas(self, fault: EngineFault) -> tuple[str, ...]:
        if fault is EngineFault.split_reasoning_delimiters:
            return SPLIT_REASONING
        return tuple(self.text[i:i + CHUNK_SIZE] for i in range(0, len(self.text), CHUNK_SIZE))

    def _chunk(self, delta: dict, *, finish: str | None = None, usage: object = None) -> str:
        body = {"id": self._next_id(), "object": "chat.completion.chunk",
                "created": self._created(), "model": self.model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        if usage is not None:
            body["usage"] = usage
        return f"data: {json.dumps(body, separators=(',', ':'))}\n\n"

    def _next_id(self) -> str:
        """Seedable (same pass): with `--seed` the chunk ids and `created` are a function of
        the seed, so a captured transcript is comparable between runs. Without one they are
        random and wall-clock, as a real engine's are."""
        if self.rng is None:
            return f"chatcmpl-{uuid.uuid4()}"
        self.emitted += 1
        return f"chatcmpl-{uuid.UUID(int=self.rng.getrandbits(128), version=4)}"

    def _created(self) -> int:
        return SEEDED_CREATED + self.emitted if self.rng is not None else int(time.time())

    def _usage(self, completion_tokens: int) -> dict:
        return {"prompt_tokens": self.prompt_tokens, "completion_tokens": completion_tokens,
                "total_tokens": self.prompt_tokens + completion_tokens}

    def script(self, fault: EngineFault, job_id: str) -> list[tuple[str, object]]:
        """What to do, step by step: `("line", sse)`, `("delta", sse)`, `("stall", seconds)`,
        `("abort", None)`. Deltas are a step of their own so the streaming loop can count what
        it has really produced and answer a cancel with that number.

        A job cancelled BEFORE the stream starts answers with usage only, for zero work - the
        shape `api_stream__a_cancellation_race_reports_what_was_produced` pins. A cancel that
        arrives mid-stream is handled by `_chat`, not here, because a precomputed script cannot
        see it (r1 review, same pass).
        """
        if job_id and job_id in self.cancelled:
            return [("line", self._chunk({}, finish="cancelled", usage=self._usage(0))),
                    ("line", DONE)]
        role = ("line", self._chunk({"role": "assistant"}))
        pieces = self.deltas(fault)
        body = [("delta", self._chunk({"content": piece})) for piece in pieces]
        if fault is EngineFault.prefill_stall:
            return [role, ("stall", float(self.limits.ttft_timeout_s + 1)), ("line", DONE)]
        if fault is EngineFault.midstream_stall:
            return [role, body[0], ("stall", float(self.limits.tpot_stall_s + 1)),
                    ("line", DONE)]
        if fault is EngineFault.abrupt_exit:
            return [role, body[0], ("abort", None)]
        if fault is EngineFault.missing_usage:
            return [role, *body, ("line", self._chunk({}, finish="stop")), ("line", DONE)]
        if fault is EngineFault.malformed_usage:
            # Structurally wrong on purpose: counts as a string and a null. A client that
            # coerces these into integers invents authoritative usage out of nothing.
            bad = {"prompt_tokens": str(self.prompt_tokens), "completion_tokens": None}
            return [role, *body, ("line", self._chunk({}, finish="stop", usage=bad)),
                    ("line", DONE)]
        return [role, *body,
                ("line", self._chunk({}, finish="stop", usage=self._usage(len(pieces)))),
                ("line", DONE)]

    # ---------------------------------------------------------- ASGI

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "lifespan":
            return await self._lifespan(receive, send)
        if scope["type"] != "http":
            return
        path, method = scope["path"], scope["method"]
        body = await self._read(receive)
        if path == "/health" and method == "GET":
            return await self._json(send, 200, {"ready": not self.drained,
                                                "max_num_seqs": self.limits.engine_max_num_seqs,
                                                "fault": self.default_fault.value,
                                                "model": self.model})
        if path == "/v1/models" and method == "GET":
            return await self._json(send, 200, {"object": "list",
                                                "data": [{"id": self.model, "object": "model"}]})
        if path == "/_control" and method == "GET":
            return await self._json(send, 200, self.state())
        if path == "/_control" and method == "POST":
            try:
                return await self._json(send, 200, self.control(self._body_json(body)))
            except UnknownFault as bad:
                return await self._json(send, 400, bad.envelope())
        if path == "/_control/cancel" and method == "POST":
            job_id = str(self._body_json(body).get("job_id", ""))
            if job_id:
                self.cancelled.add(job_id)
            return await self._json(send, 200, {"cancelled": sorted(self.cancelled)})
        if path == "/v1/chat/completions" and method == "POST":
            return await self._chat(scope, body, send, receive)
        if path == "/tokenize" and method == "POST":
            return await self._tokenize(self._body_json(body), send)
        await self._json(send, 404, {"error": {"message": "no route", "type": "not_found_error",
                                               "code": "not_found"}})

    async def _lifespan(self, receive, send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return

    @staticmethod
    async def _read(receive) -> bytes:
        chunks = b""
        while True:
            message = await receive()
            chunks += message.get("body", b"")
            if not message.get("more_body"):
                return chunks

    @staticmethod
    def _body_json(body: bytes) -> dict:
        try:
            parsed = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    async def _json(send, status: int, payload: dict) -> None:
        raw = json.dumps(payload).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(raw)).encode())]})
        await send({"type": "http.response.body", "body": raw})

    def state(self) -> dict:
        return {"fault": self.default_fault.value, "drained": self.drained,
                "cancelled": sorted(self.cancelled), "requests": len(self.seen),
                "text": self.text, "prompt_tokens": self.prompt_tokens,
                "delta_gap_s": self.delta_gap_s, "disconnected": self.disconnected,
                "tokenize_fault": self.tokenize_fault, "tokenize_delay_s": self.tokenize_delay_s,
                "tokenized": len(self.tokenized)}

    def control(self, payload: dict) -> dict:
        """Set the process-wide default. Used when the client under test cannot be made to
        send a fault header - a gateway, for instance."""
        if "fault" in payload:
            try:
                self.default_fault = EngineFault(str(payload["fault"]))
            except ValueError:
                raise UnknownFault(str(payload["fault"])) from None
        if "drained" in payload:
            self.drained = bool(payload["drained"])
        if "text" in payload:
            self.text = str(payload["text"])
        if "prompt_tokens" in payload:
            self.prompt_tokens = int(payload["prompt_tokens"])
        if "delta_gap_s" in payload:          # E3B phase 3: a generation slow enough to leave
            self.delta_gap_s = max(0.0, float(payload["delta_gap_s"]))
        if payload.get("tokenize_fault") in TOKENIZE_FAULTS:
            self.tokenize_fault = payload["tokenize_fault"]
        if "tokenize_delay_s" in payload:     # PREP-WORKER: a preparation slow enough to drain
            self.tokenize_delay_s = max(0.0, float(payload["tokenize_delay_s"]))
        if payload.get("reset"):
            self.cancelled.clear()
            self.seen.clear()
        return self.state()

    async def _tokenize(self, request: dict, send) -> None:
        """vLLM's `/tokenize` for a chat body: `{count, max_model_len, tokens}`."""
        self.tokenized.append(request)
        if self.tokenize_delay_s:
            import asyncio
            await asyncio.sleep(self.tokenize_delay_s)
        if self.tokenize_fault == "down":
            return await self._json(send, 503, {"error": {"message": "tokenizer unavailable",
                                                          "type": "server_error"}})
        videos = sum(1 for message in request.get("messages") or ()
                     if isinstance(message, dict) and isinstance(message.get("content"), list)
                     for part in message["content"]
                     if isinstance(part, dict) and part.get("type") == "video_url")
        size = ((request.get("mm_processor_kwargs") or {}).get("size") or {})
        expanded = int(size.get("longest_edge", 0)) // PIXELS_PER_TOKEN
        pads = videos * (1 if self.tokenize_fault == "unexpanded" else expanded)
        count = self.prompt_tokens
        tokens = [VIDEO_TOKEN_ID] * min(pads, count) + [1] * max(0, count - pads)
        return await self._json(send, 200, {"count": count, "max_model_len":
                                            self.limits.max_context_tokens, "tokens": tokens})

    def _fault_for(self, scope, request: dict) -> EngineFault:
        """Header beats body beats process default, so one request can be scripted without
        disturbing a concurrent one. An unknown name is the CALLER's error: `UnknownFault`,
        which the routes turn into a 4xx, never a 500 (r1 review, same pass)."""
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        named = headers.get("x-infrx-fault") or request.get("infrx_fault")
        if not named:
            return self.default_fault
        try:
            return EngineFault(str(named))
        except ValueError:
            raise UnknownFault(str(named)) from None

    async def _chat(self, scope, body: bytes, send, receive=None) -> None:
        request = self._body_json(body)
        try:
            fault = self._fault_for(scope, request)
        except UnknownFault as bad:
            return await self._json(send, 400, bad.envelope())
        job_id = str(request.get("infrx_job_id", ""))
        self.seen.append({"fault": fault.value, "job_id": job_id,
                          "stream": bool(request.get("stream")),
                          "model": request.get("model"),
                          "max_tokens": request.get("max_tokens")})
        if not request.get("stream"):
            return await self._json(send, 200, self._non_stream(fault))
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream"),
                                (b"cache-control", b"no-cache")]})
        gap = CANCEL_GAP_S if fault is EngineFault.cancellation_race else self.delta_gap_s
        import asyncio
        gone = asyncio.Event()

        async def watch() -> None:
            # The body is read, so the next message is the client leaving.
            while receive is not None and (await receive())["type"] != "http.disconnect":
                pass
            if receive is not None:
                gone.set()
        watcher = asyncio.create_task(watch())
        try:
            await self._stream(fault, job_id, send, gap, gone)
        finally:
            watcher.cancel()

    async def _stream(self, fault, job_id, send, gap, gone) -> None:
        produced = 0
        for kind, payload in self.script(fault, job_id):
            if gone.is_set():
                self.disconnected += 1
                return
            # A cancel that arrives WHILE the stream is running: the precomputed script cannot
            # see it, so the loop checks between sends and answers with usage for the deltas
            # actually emitted. W2/E3 depend on this shape, and the old code ignored a
            # mid-stream cancel entirely (seven chunks, completion_tokens 5).
            if produced and job_id and job_id in self.cancelled:
                for line in (self._chunk({}, finish="cancelled", usage=self._usage(produced)),
                             DONE):
                    await send({"type": "http.response.body", "body": line.encode(),
                                "more_body": True})
                break
            if kind == "abort":
                # The engine process dies mid-body: no terminator, no [DONE]. Raising here
                # makes the server tear the connection down, which is what the client must
                # survive; a clean close would be indistinguishable from a finished stream.
                raise EngineProcessExited("fake vLLM aborted the response body")
            if kind == "stall":
                if self.stall_real_s:
                    import asyncio
                    await asyncio.sleep(self.stall_real_s)
                payload = f"{STALL_COMMENT}{payload}\n\n"
            elif kind == "delta":
                produced += 1
            await send({"type": "http.response.body", "body": str(payload).encode(),
                        "more_body": True})
            if kind == "delta" and gap:
                # The ONE real delay in this file, and only on the cancellation path: without
                # a gap between deltas the whole body is emitted before any client could
                # possibly cancel, so the race the fault exists to model cannot happen. It is
                # a few milliseconds of yielding, not a timeout being waited out.
                import asyncio
                await asyncio.sleep(gap)
        if gone.is_set():
            self.disconnected += 1
            return
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    def _non_stream(self, fault: EngineFault) -> dict:
        pieces = self.deltas(fault)
        usage: object = self._usage(len(pieces))
        if fault is EngineFault.missing_usage:
            usage = None
        elif fault is EngineFault.malformed_usage:
            usage = {"prompt_tokens": str(self.prompt_tokens), "completion_tokens": None}
        payload = {"id": f"chatcmpl-{uuid.uuid4()}", "object": "chat.completion",
                   "created": int(time.time()), "model": self.model,
                   "choices": [{"index": 0, "finish_reason": "stop",
                                "message": {"role": "assistant", "content": "".join(pieces)}}]}
        if usage is not None:
            payload["usage"] = usage
        return payload


# ============================================================== server process

class FakeVllmServer:
    """The app in its own OS process, so "kill the engine" is a real kill.

    An in-process server cannot be SIGKILLed without taking the test runner with it, and
    the fault the harness must offer is process loss.
    """

    def __init__(self, port: int, *, fault: str = "none", stall_real_s: float = 0.0) -> None:
        self.port, self.fault, self.stall_real_s = port, fault, stall_real_s
        self.process: subprocess.Popen | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 30.0) -> "FakeVllmServer":
        # `--host` explicitly, never the CLI default: a change to that default would otherwise
        # take every server in the suite down at `setup_module` (17 collection errors, measured
        # against the reviewer's V7 mutant) instead of failing the one case that guards it.
        argv = [sys.executable, str(Path(__file__).resolve()), "--port", str(self.port),
                "--host", "127.0.0.1",
                "--fault", self.fault, "--stall-real-s", str(self.stall_real_s)]
        self._drop_log()        # a restart after a kill would otherwise orphan the old one
        # The child's stderr goes to a temporary file, not to DEVNULL: a server that dies on
        # startup (a busy port is the obvious one) otherwise reports only its exit status, and
        # "exited 3" with no reason costs whoever reads it an afternoon. The file is UNLINKED
        # the moment it is created (`TemporaryFile`), so it cannot be leaked even when this
        # process is killed outright - which is exactly what the SIGTERM drill does to itself
        # under the mutant that removes the handler (measured: 28 leaked logs in $TMPDIR).
        self._log = tempfile.TemporaryFile(prefix="infrx-e2-fake-vllm-", suffix=".log")
        # r1 review, same pass: its OWN process group, so a signal handler can take the whole
        # server down with one `killpg` and a SIGTERM to run.py cannot leave it orphaned on a
        # task-local port for the next run to trip over.
        self.process = subprocess.Popen(argv, stdout=self._log, stderr=subprocess.STDOUT,
                                        env={**os.environ, "PYTHONUNBUFFERED": "1"},
                                        start_new_session=True)
        import httpx
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.process.poll() is not None:
                # r1 review B3 side effect: a raise here used to leak the log file, because
                # only `stop()` dropped it and a caller that never started has nothing to stop.
                reason = self._tail()
                self._drop_log()
                raise RuntimeError(f"fake vLLM exited {self.process.returncode} during startup "
                                   f"on port {self.port}: {reason}")
            try:
                if httpx.get(f"{self.base_url}/health", timeout=1.0).status_code == 200:
                    return self
            except Exception:                        # noqa: BLE001 - not listening yet
                time.sleep(0.05)
        reason = self._tail()
        self.stop()
        raise RuntimeError(f"fake vLLM did not answer /health within {timeout}s "
                           f"on port {self.port}: {reason}")

    def _tail(self, lines: int = 6) -> str:
        log = getattr(self, "_log", None)
        if log is None:
            return "(no log)"
        try:
            log.seek(0)
            text = log.read().decode(errors="replace")
        except (OSError, ValueError):
            return "(log unreadable)"
        return " | ".join(text.strip().splitlines()[-lines:]) or "(no output)"

    def control(self, **payload) -> dict:
        import httpx
        return httpx.post(f"{self.base_url}/_control", json=payload, timeout=5.0).json()

    def reset(self, fault: str = "none") -> dict:
        return self.control(fault=fault, drained=False, reset=True)

    def kill(self, sig: int = signal.SIGKILL) -> int:
        """Process-loss injection. Returns the exit status the process ended with."""
        if self.process is None:
            raise RuntimeError("not started")
        self.process.send_signal(sig)
        return self.process.wait(timeout=15)

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self) -> None:
        """Take the whole process GROUP down: uvicorn may have children, and a survivor holds
        a task-local port that the next run's preflight then reports as busy."""
        if self.process is not None:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                if self.process.poll() is not None:
                    break
                try:
                    os.killpg(os.getpgid(self.process.pid), sig)
                except (ProcessLookupError, PermissionError):
                    self.process.send_signal(sig)
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    continue
            self.process = None
        self._drop_log()

    def _drop_log(self) -> None:
        """Closing is enough: the file was unlinked at creation, so the last close frees it."""
        log = getattr(self, "_log", None)
        if log is not None:
            log.close()
        self._log = None

    def __enter__(self) -> "FakeVllmServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


# ============================================================== engine adapter

class HttpEngine:
    """`ports.Engine` over the HTTP wire above: the adapter E2 runs the exported
    `run_engine_conformance` suite against.

    It is a *real* adapter in the sense that matters for F-CONTRACT - the events come off a
    socket, decoded from SSE, not handed over in memory - and a test double in the sense
    that the peer is a fake engine, not vLLM. Passing the suite here proves the wire format
    and the decoder agree with the contract; it does not prove anything about vLLM.
    """

    def __init__(self, base_url: str, *, clock: FakeClock, limits: PilotSettings = DEFAULTS,
                 fault: str = "none", timeout: float = 30.0, client_factory=None) -> None:
        self.base_url, self.clock, self.limits = base_url.rstrip("/"), clock, limits
        self.fault, self.timeout = fault, timeout
        # Injected like every other collaborator, so a case can hand it a transport that
        # produces a wire shape the server cannot be asked for (a 200 that stops without
        # its terminator) without monkeypatching httpx for the whole process.
        self.client_factory = client_factory or (lambda **kw: __import__("httpx").AsyncClient(**kw))
        self.stalled_s = 0.0

    # ---- ports.Engine

    async def generate(self, lease: Lease, prepared: PreparedRequest) -> AsyncIterator[EngineEvent]:
        import httpx
        payload = {"model": prepared.model_revision, "stream": True,
                   "stream_options": {"include_usage": True},
                   "max_tokens": prepared.max_output_tokens,
                   "messages": [dict(message) for message in prepared.messages],
                   "infrx_fault": self.fault, "infrx_job_id": str(lease.job_id)}
        saw_terminator = False
        # R80 (`{visible, raw}`): the customer's text is the reasoning-filtered one, computed
        # with the same filter the real adapter uses; the held tail is emitted at the end.
        reasoning = ReasoningFilter()
        try:
            async with self.client_factory(timeout=self.timeout) as client:
                async with client.stream("POST", f"{self.base_url}/v1/chat/completions",
                                         json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        if line.startswith(STALL_COMMENT):
                            # A declared stall: move the injected clock, do not sleep.
                            seconds = float(line[len(STALL_COMMENT):].strip())
                            self.stalled_s += seconds
                            self.clock.advance(seconds)
                            continue
                        if not line.startswith("data:"):
                            continue
                        data = line[len("data:"):].strip()
                        if data == "[DONE]":
                            saw_terminator = True
                            break
                        for event in self._events(data, reasoning):
                            yield event
        except httpx.HTTPError as exc:
            raise EngineProcessExited(f"engine stream failed: {type(exc).__name__}") from exc
        tail = reasoning.close()
        if tail:
            yield EngineEvent(type=ChunkEventType.delta, payload={"visible": tail, "raw": ""})
        if not saw_terminator:
            # A 200 that stops without its terminator is not a finished generation. E1
            # measured the same shape on the client side; treating it as success is how a
            # truncated stream becomes an accepted, billable request.
            raise EngineProcessExited("engine stream ended without [DONE]")

    def _events(self, data: str, reasoning: ReasoningFilter) -> list[EngineEvent]:
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            return []
        events: list[EngineEvent] = []
        choice = (chunk.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}
        if delta.get("role") and not delta.get("content"):
            # vLLM's opening role chunk is "generation has started", which is exactly the
            # contract's `progress` event. Nothing is billable about it.
            events.append(EngineEvent(type=ChunkEventType.progress,
                                      payload={"phase": "running"}))
        if delta.get("content"):
            raw = delta["content"]
            events.append(EngineEvent(type=ChunkEventType.delta,
                                      payload={"visible": reasoning.feed(raw), "raw": raw}))
        if "usage" in chunk:
            events.append(EngineEvent(type=ChunkEventType.usage, payload={"usage": chunk["usage"]},
                                      usage=self._usage(chunk["usage"])))
        return events

    @staticmethod
    def _usage(raw: object) -> Usage | None:
        """Authoritative usage, or nothing. Never a coercion: a server that sends
        `"1200"` or `null` has not told us how many tokens it used, and inventing an
        integer here is how a malformed response becomes a debit (08 §5, R21)."""
        if not isinstance(raw, dict):
            return None
        prompt, completion = raw.get("prompt_tokens"), raw.get("completion_tokens")
        if not all(isinstance(value, int) and not isinstance(value, bool)
                   for value in (prompt, completion)):
            return None
        if prompt < 0 or completion < 0:
            return None
        return Usage.of(prompt, completion)

    async def cancel(self, lease: Lease) -> bool:
        import httpx
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/_control/cancel",
                                         json={"job_id": str(lease.job_id)})
            return response.status_code == 200

    async def health(self) -> dict[str, object]:
        import httpx
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            return (await client.get(f"{self.base_url}/health")).json()

    async def drain(self) -> None:
        import httpx
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            await client.post(f"{self.base_url}/_control", json={"drained": True})


def engine_factory(server: FakeVllmServer):
    """The conformance factory (see `infrx/contracts/README.md`, "Running a conformance
    suite against a real adapter"): fresh adapter per call, honours `limits`, accepts
    `fault`, ignores anything else."""
    from infrx.contracts.fakes.support import SequentialIds
    from infrx.contracts.conformance import Harness

    def factory(limits=None, *, fault: str = "none", **_):
        settings = limits or DEFAULTS
        server.reset()
        clock = FakeClock()
        engine = HttpEngine(server.base_url, clock=clock, limits=settings, fault=fault)
        return Harness(port=engine, clock=clock, ids=SequentialIds(),
                       extra={"text": DEFAULT_TEXT})

    return factory


# ============================================================== standalone

LOOPBACK = ("127.0.0.1", "::1", "localhost")


def _serve(app, host: str, port: int) -> None:
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="warning")


# Injected so a test can check the CLI's guards without ever opening a socket: a case that
# proves "--host 0.0.0.0 is refused" must not bind 0.0.0.0 when the guard is mutated away -
# it would block for ever and, worse, actually listen on every interface.
SERVE = _serve


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="controllable fake vLLM for E2")
    # A literal on purpose: W3's mutant runner copies this file WITHOUT the harness, so it may
    # not import it (E3B phase 2 measured the breakage). Every harness start passes --port
    # (`harness.PORTS["fake_vllm"]`, which INFRX_E2_NAMESPACE moves); only a hand run uses this.
    parser.add_argument("--port", type=int, default=55580)
    parser.add_argument("--host", default="127.0.0.1",
                        help="loopback only unless --allow-non-loopback is given")
    parser.add_argument("--allow-non-loopback", action="store_true",
                        help="explicitly permit a non-loopback bind (nothing in this task "
                             "needs it; a scripted engine reachable from the network is a "
                             "hazard, not a feature)")
    parser.add_argument("--fault", default="none",
                        choices=[fault.value for fault in EngineFault])
    parser.add_argument("--stall-real-s", type=float, default=0.0,
                        help="also sleep this long at a declared stall")
    parser.add_argument("--seed", type=int,
                        help="make chunk ids and `created` a function of this seed")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # Same pass: refuse a non-loopback bind unless it was asked for in so many words. Every
    # port this task uses is published on 127.0.0.1; a fake engine answering on 0.0.0.0 is
    # something anyone on the network can script.
    if args.host not in LOOPBACK and not args.allow_non_loopback:
        raise SystemExit(f"refusing to bind {args.host}: pass --allow-non-loopback to mean it "
                         f"(loopback is {', '.join(LOOPBACK)})")
    app = FakeVllmApp(fault=args.fault, stall_real_s=args.stall_real_s, seed=args.seed)
    SERVE(app, args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
