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
from typing import AsyncIterator

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "apps" / "infrx-api"))

from infrx.contracts.fakes.engine import DEFAULT_TEXT, SPLIT_REASONING, EngineFault  # noqa: E402
from infrx.contracts.fakes.support import FakeClock  # noqa: E402
from infrx.contracts.limits import DEFAULTS, PilotSettings  # noqa: E402
from infrx.contracts.records import (ChunkEventType, EngineEvent, Lease,  # noqa: E402
                                     PreparedRequest, Usage)

STALL_COMMENT = ": infrx-stall "
DONE = "data: [DONE]\n\n"
CHUNK_SIZE = 12


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
                 limits: PilotSettings = DEFAULTS, stall_real_s: float = 0.0) -> None:
        self.default_fault = EngineFault(fault)
        self.text, self.prompt_tokens, self.model = text, prompt_tokens, model
        self.limits, self.stall_real_s = limits, stall_real_s
        self.drained = False
        self.cancelled: set[str] = set()
        self.seen: list[dict] = []

    # ---------------------------------------------------------- scripting

    def deltas(self, fault: EngineFault) -> tuple[str, ...]:
        if fault is EngineFault.split_reasoning_delimiters:
            return SPLIT_REASONING
        return tuple(self.text[i:i + CHUNK_SIZE] for i in range(0, len(self.text), CHUNK_SIZE))

    def _chunk(self, delta: dict, *, finish: str | None = None, usage: object = None) -> str:
        body = {"id": f"chatcmpl-{uuid.uuid4()}", "object": "chat.completion.chunk",
                "created": int(time.time()), "model": self.model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        if usage is not None:
            body["usage"] = usage
        return f"data: {json.dumps(body, separators=(',', ':'))}\n\n"

    def _usage(self, completion_tokens: int) -> dict:
        return {"prompt_tokens": self.prompt_tokens, "completion_tokens": completion_tokens,
                "total_tokens": self.prompt_tokens + completion_tokens}

    def script(self, fault: EngineFault, job_id: str) -> list[str | float]:
        """The SSE lines for one request, floats standing for a declared stall.

        A cancelled job answers with usage only, for the work actually done - the shape
        `api_stream__a_cancellation_race_reports_what_was_produced` pins.
        """
        if job_id and job_id in self.cancelled:
            return [self._chunk({}, finish="cancelled", usage=self._usage(0)), DONE]
        role = self._chunk({"role": "assistant"})
        pieces = self.deltas(fault)
        body = [self._chunk({"content": piece}) for piece in pieces]
        if fault is EngineFault.prefill_stall:
            return [role, float(self.limits.ttft_timeout_s + 1), DONE]
        if fault is EngineFault.midstream_stall:
            return [role, body[0], float(self.limits.tpot_stall_s + 1), DONE]
        if fault is EngineFault.abrupt_exit:
            return [role, body[0], "ABORT"]
        tail_usage = self._usage(len(pieces))
        if fault is EngineFault.missing_usage:
            return [role, *body, self._chunk({}, finish="stop"), DONE]
        if fault is EngineFault.malformed_usage:
            # Structurally wrong on purpose: counts as a string and a null. A client that
            # coerces these into integers invents authoritative usage out of nothing.
            bad = {"prompt_tokens": str(self.prompt_tokens), "completion_tokens": None}
            return [role, *body, self._chunk({}, finish="stop", usage=bad), DONE]
        return [role, *body, self._chunk({}, finish="stop", usage=tail_usage), DONE]

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
            return await self._json(send, 200, self.control(self._body_json(body)))
        if path == "/_control/cancel" and method == "POST":
            job_id = str(self._body_json(body).get("job_id", ""))
            if job_id:
                self.cancelled.add(job_id)
            return await self._json(send, 200, {"cancelled": sorted(self.cancelled)})
        if path == "/v1/chat/completions" and method == "POST":
            return await self._chat(scope, body, send)
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
                "text": self.text, "prompt_tokens": self.prompt_tokens}

    def control(self, payload: dict) -> dict:
        """Set the process-wide default. Used when the client under test cannot be made to
        send a fault header - a gateway, for instance."""
        if "fault" in payload:
            self.default_fault = EngineFault(str(payload["fault"]))
        if "drained" in payload:
            self.drained = bool(payload["drained"])
        if "text" in payload:
            self.text = str(payload["text"])
        if "prompt_tokens" in payload:
            self.prompt_tokens = int(payload["prompt_tokens"])
        if payload.get("reset"):
            self.cancelled.clear()
            self.seen.clear()
        return self.state()

    def _fault_for(self, scope, request: dict) -> EngineFault:
        """Header beats body beats process default, so one request can be scripted
        without disturbing a concurrent one."""
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        named = headers.get("x-infrx-fault") or request.get("infrx_fault")
        return EngineFault(str(named)) if named else self.default_fault

    async def _chat(self, scope, body: bytes, send) -> None:
        request = self._body_json(body)
        fault = self._fault_for(scope, request)
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
        for line in self.script(fault, job_id):
            if line == "ABORT":
                # The engine process dies mid-body: no terminator, no [DONE]. Raising here
                # makes the server tear the connection down, which is what the client must
                # survive; a clean close would be indistinguishable from a finished stream.
                raise EngineProcessExited("fake vLLM aborted the response body")
            if isinstance(line, float):
                if self.stall_real_s:
                    import asyncio
                    await asyncio.sleep(self.stall_real_s)
                line = f"{STALL_COMMENT}{line}\n\n"
            await send({"type": "http.response.body", "body": line.encode(), "more_body": True})
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
        argv = [sys.executable, str(Path(__file__).resolve()), "--port", str(self.port),
                "--fault", self.fault, "--stall-real-s", str(self.stall_real_s)]
        self._drop_log()        # a restart after a kill would otherwise orphan the old one
        # The child's stderr goes to a temporary file, not to DEVNULL: a server that dies on
        # startup (a busy port is the obvious one) otherwise reports only its exit status,
        # and "exited 3" with no reason costs whoever reads it an afternoon.
        self._log = tempfile.NamedTemporaryFile(prefix="infrx-e2-fake-vllm-", suffix=".log",
                                                delete=False)
        self.process = subprocess.Popen(argv, stdout=self._log, stderr=subprocess.STDOUT,
                                        env={**os.environ, "PYTHONUNBUFFERED": "1"})
        import httpx
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.process.poll() is not None:
                raise RuntimeError(f"fake vLLM exited {self.process.returncode} during startup "
                                   f"on port {self.port}: {self._tail()}")
            try:
                if httpx.get(f"{self.base_url}/health", timeout=1.0).status_code == 200:
                    return self
            except Exception:                        # noqa: BLE001 - not listening yet
                time.sleep(0.05)
        raise RuntimeError(f"fake vLLM did not answer /health within {timeout}s "
                           f"on port {self.port}: {self._tail()}")

    def _tail(self, lines: int = 6) -> str:
        log = getattr(self, "_log", None)
        if log is None:
            return "(no log)"
        try:
            return " | ".join(Path(log.name).read_text().strip().splitlines()[-lines:])
        except OSError:
            return "(log unreadable)"

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
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
            self.process = None
        self._drop_log()

    def _drop_log(self) -> None:
        log = getattr(self, "_log", None)
        if log is not None:
            log.close()
            Path(log.name).unlink(missing_ok=True)
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
                        for event in self._events(data):
                            yield event
        except httpx.HTTPError as exc:
            raise EngineProcessExited(f"engine stream failed: {type(exc).__name__}") from exc
        if not saw_terminator:
            # A 200 that stops without its terminator is not a finished generation. E1
            # measured the same shape on the client side; treating it as success is how a
            # truncated stream becomes an accepted, billable request.
            raise EngineProcessExited("engine stream ended without [DONE]")

    def _events(self, data: str) -> list[EngineEvent]:
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
            events.append(EngineEvent(type=ChunkEventType.delta,
                                      payload={"content": delta["content"]}))
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

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="controllable fake vLLM for E2")
    parser.add_argument("--port", type=int, default=55580)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--fault", default="none",
                        choices=[fault.value for fault in EngineFault])
    parser.add_argument("--stall-real-s", type=float, default=0.0,
                        help="also sleep this long at a declared stall")
    args = parser.parse_args(argv)
    import uvicorn
    app = FakeVllmApp(fault=args.fault, stall_real_s=args.stall_real_s)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
