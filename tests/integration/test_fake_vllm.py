"""Layer 1: the fake vLLM and its engine adapter, over real HTTP, no container needed.

The suite the contract cares about is the *exported* one
(`infrx.contracts.conformance.run_engine_conformance`): the same cases the in-memory fake
passes, run against an adapter that decodes SSE off a socket. Everything else here pins the
wire behaviour a conformance case cannot see - that a stall is declared rather than slept,
that a torn connection is distinguishable from a finished one, that cancellation is keyed
by job.

No sleeps, no wall-clock assertions: the stall is a clock advance (08 §2).
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness                                          # noqa: E402

harness.api_on_path()

import fake_vllm                                        # noqa: E402
from fake_vllm import EngineProcessExited, FakeVllmServer, HttpEngine, engine_factory  # noqa: E402
from infrx.contracts.conformance import MissingHook, run_engine_conformance  # noqa: E402
from infrx.contracts.fakes.engine import DEFAULT_TEXT, EngineFault  # noqa: E402
from infrx.contracts.fakes.support import FakeClock  # noqa: E402
from infrx.contracts.limits import DEFAULTS  # noqa: E402
from infrx.contracts.records import (ChunkEventType, LeaseKind, Lease,  # noqa: E402
                                     PreparedRequest)

# A port inside E's range that the compose stack does not use, so this file can run while
# the stack is up.
TEST_PORT = harness.PORTS["fake_vllm"] + 1
SERVER: FakeVllmServer | None = None


def setup_module(module) -> None:                        # noqa: ARG001 - pytest hook
    global SERVER
    SERVER = FakeVllmServer(TEST_PORT).start()


def teardown_module(module) -> None:                     # noqa: ARG001 - pytest hook
    if SERVER is not None:
        SERVER.stop()


def adapter(fault: str = "none", clock: FakeClock | None = None) -> HttpEngine:
    SERVER.reset()
    return HttpEngine(SERVER.base_url, clock=clock or FakeClock(), fault=fault)


def lease(job_id: str | None = None, clock: FakeClock | None = None) -> Lease:
    clock = clock or FakeClock()
    now = clock.now()
    return Lease(job_id=job_id or str(uuid.uuid4()), kind=LeaseKind.inference, generation=1,
                 worker_id="e2-worker", acquired_at=now,
                 expires_at=clock.at(DEFAULTS.lease_ttl_s),
                 generation_deadline_at=clock.at(DEFAULTS.generation_timeout_s),
                 first_token_deadline_at=clock.at(DEFAULTS.ttft_timeout_s))


def prepared() -> PreparedRequest:
    return PreparedRequest(request_id=str(uuid.uuid4()), model_revision="infrx-e2/fake-vllm",
                           messages=({"role": "user", "content": "Describe this clip."},),
                           max_output_tokens=256, prompt_tokens=1200)


async def drain(engine: HttpEngine, the_lease: Lease) -> list:
    return [event async for event in engine.generate(the_lease, prepared())]


# ------------------------------------------------------------------ F-CONTRACT

def test_the_exported_engine_conformance_suite_passes_over_http():
    """F-CONTRACT: the same suite the in-memory fake passes, against an HTTP adapter.

    A conformance case skipped for a missing hook is a skip, never a pass (R32), so the
    factory's hooks are asserted present before the suite runs.
    """
    factory = engine_factory(SERVER)
    harness_obj = factory()
    assert harness_obj.extra.get("text") == DEFAULT_TEXT, "the only optional engine hook"
    ran = run_engine_conformance(factory)
    assert ran == 8, f"expected the eight exported engine cases, ran {ran}"


def test_every_fault_mode_04_requires_is_implemented():
    """04 §Test environments: prefill stall, midstream stall, malformed usage,
    cancellation race, abrupt exit, plus 08 §2's split reasoning delimiters."""
    required = {"prefill_stall", "midstream_stall", "malformed_usage", "missing_usage",
                "cancellation_race", "abrupt_exit", "split_reasoning_delimiters"}
    assert required <= {fault.value for fault in EngineFault}
    for name in sorted(required):
        state = SERVER.control(fault=name)
        assert state["fault"] == name, name
    SERVER.reset()


# ------------------------------------------------------------------ wire behaviour

def test_a_stall_is_declared_not_slept_and_moves_only_the_injected_clock():
    """The stall comment is the whole mechanism: the adapter advances its clock and the
    suite needs no sleep. A test that waited 61 real seconds would be deleted by the first
    person in a hurry."""
    clock = FakeClock()
    engine = adapter("prefill_stall", clock)
    started = clock.now()
    events = asyncio.run(drain(engine, lease(clock=clock)))
    assert [event.type for event in events] == [ChunkEventType.progress]
    advanced = (clock.now() - started).total_seconds()
    assert advanced > DEFAULTS.ttft_timeout_s, advanced
    assert engine.stalled_s == advanced

    raw = httpx.post(f"{SERVER.base_url}/v1/chat/completions",
                     json={"stream": True, "model": "m", "messages": [],
                           "infrx_fault": "prefill_stall"}, timeout=10.0).text
    assert fake_vllm.STALL_COMMENT in raw, "the stall must be visible on the wire"
    assert raw.rstrip().endswith("[DONE]"), "a stall ends the stream cleanly"


def test_a_midstream_stall_delivers_a_delta_and_then_no_usage():
    clock = FakeClock()
    engine = adapter("midstream_stall", clock)
    events = asyncio.run(drain(engine, lease(clock=clock)))
    assert any(event.type is ChunkEventType.delta for event in events)
    assert not any(event.type is ChunkEventType.usage for event in events)
    assert engine.stalled_s > DEFAULTS.tpot_stall_s


def test_an_abrupt_exit_tears_the_connection_and_is_not_a_domain_error():
    """An engine process dying is not a customer error. It must also not look like a
    finished stream: a 200 whose body stops is exactly how a truncated generation becomes
    an accepted, billable request."""
    from infrx.contracts import errors
    engine = adapter("abrupt_exit")
    with pytest.raises(EngineProcessExited) as raised:
        asyncio.run(drain(engine, lease()))
    assert not isinstance(raised.value, errors.DomainError)
    assert SERVER.alive(), "only the response is aborted; the server itself survives"


def test_a_stream_without_its_terminator_is_a_failure_not_a_success():
    """The same rule from the client's side: no `[DONE]`, no success. This is the shape
    E1 measured on the bench client (`truncated_stream`), asserted here at the port."""
    truncated = ("data: " + json.dumps({"choices": [{"index": 0, "delta": {"role": "assistant"}}]})
                 + "\n\ndata: "
                 + json.dumps({"choices": [{"index": 0, "delta": {"content": "hi"}}]}) + "\n\n")

    def handler(request):                                # noqa: ARG001
        return httpx.Response(200, text=truncated,
                              headers={"content-type": "text/event-stream"})

    engine = HttpEngine(SERVER.base_url, clock=FakeClock(),
                        client_factory=lambda **kw: httpx.AsyncClient(
                            transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(EngineProcessExited, match="without \\[DONE\\]"):
        asyncio.run(drain(engine, lease()))


def test_malformed_and_missing_usage_never_become_authoritative_tokens():
    """R21/§5: unknown usage is the ABSENCE of usage. A client that coerces `"1200"` into
    an integer invents an authoritative token count, and an invented count is a debit."""
    for fault in ("malformed_usage", "missing_usage"):
        events = asyncio.run(drain(adapter(fault), lease()))
        assert all(event.usage is None for event in events), fault
    assert HttpEngine._usage({"prompt_tokens": "1200", "completion_tokens": None}) is None
    assert HttpEngine._usage({"prompt_tokens": 1200, "completion_tokens": True}) is None
    assert HttpEngine._usage({"prompt_tokens": 1200, "completion_tokens": -1}) is None
    assert HttpEngine._usage({"prompt_tokens": 1.0, "completion_tokens": 2}) is None
    good = HttpEngine._usage({"prompt_tokens": 1200, "completion_tokens": 7})
    assert good is not None and good.completion_tokens == 7
    assert good.certainty.value == "authoritative"


def test_split_reasoning_delimiters_never_appear_whole_in_one_chunk():
    events = asyncio.run(drain(adapter("split_reasoning_delimiters"), lease()))
    deltas = [event.payload["raw"] for event in events
              if event.type is ChunkEventType.delta]
    joined = "".join(deltas)
    assert joined.count("<think>") == 1 and joined.count("</think>") == 1
    assert not any("<think>" in delta or "</think>" in delta for delta in deltas)


def test_cancellation_is_keyed_by_job_and_leaves_other_jobs_alone():
    """A cancel that cancelled everything would pass a single-job case and be useless."""
    engine = adapter("cancellation_race")
    cancelled, untouched = lease(), lease()
    assert asyncio.run(engine.cancel(cancelled)) is True

    events = asyncio.run(drain(engine, cancelled))
    usage = [event for event in events if event.type is ChunkEventType.usage]
    assert len(usage) == 1 and usage[0].usage.completion_tokens == 0

    other = asyncio.run(drain(engine, untouched))
    assert any(event.type is ChunkEventType.delta for event in other), \
        "cancelling one job must not cancel another"


SERVER_TEXT_CHUNKS = tuple(DEFAULT_TEXT[i:i + fake_vllm.CHUNK_SIZE]
                           for i in range(0, len(DEFAULT_TEXT), fake_vllm.CHUNK_SIZE))


def test_a_cancel_that_arrives_after_the_first_delta_bills_only_what_was_produced():
    """r1 review, same pass: the script was precomputed, so a cancel arriving mid-stream was
    ignored entirely - seven chunks and `completion_tokens: 5` for a request the client had
    already abandoned. W2/E3 depend on the other shape: stop, and report the deltas actually
    emitted. Driven over a real socket with a real interleaving.
    """
    SERVER.reset()
    job_id = str(uuid.uuid4())
    payload = {"model": "m", "stream": True, "messages": [], "infrx_job_id": job_id,
               "infrx_fault": "cancellation_race"}
    deltas, usage_events, saw_done = 0, [], False
    with httpx.Client(timeout=10.0) as client:
        with client.stream("POST", f"{SERVER.base_url}/v1/chat/completions",
                           json=payload) as response:
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    saw_done = True
                    break
                chunk = json.loads(data)
                if (chunk["choices"][0].get("delta") or {}).get("content"):
                    deltas += 1
                    if deltas == 1:            # the cancel lands while the stream is open
                        assert httpx.post(f"{SERVER.base_url}/_control/cancel",
                                          json={"job_id": job_id},
                                          timeout=5.0).status_code == 200
                if "usage" in chunk:
                    usage_events.append(chunk["usage"])
    assert saw_done, "a cancelled stream still ends cleanly"
    assert len(usage_events) == 1, usage_events
    assert usage_events[0]["completion_tokens"] == deltas, \
        f"reported {usage_events[0]['completion_tokens']} for {deltas} deltas actually sent"
    assert 1 <= deltas < len(SERVER_TEXT_CHUNKS), \
        f"it produced something ({deltas}) and really stopped early"
    SERVER.reset()


def test_an_unknown_fault_is_a_4xx_not_a_500():
    """Same pass: a caller asking for a fault that does not exist is a client error. A 500
    would make a typo in a test look like a broken engine."""
    SERVER.reset()
    body = httpx.post(f"{SERVER.base_url}/v1/chat/completions", timeout=10.0,
                      json={"stream": True, "model": "m", "messages": [],
                            "infrx_fault": "nope"})
    assert body.status_code == 400, body.status_code
    error = body.json()["error"]
    assert error["code"] == "unsupported_parameter" and "nope" in error["message"]
    assert "prefill_stall" in error["known"], "it says what it would have accepted"
    header = httpx.post(f"{SERVER.base_url}/v1/chat/completions", timeout=10.0,
                        json={"stream": True, "model": "m", "messages": []},
                        headers={"X-Infrx-Fault": "not-a-fault"})
    assert header.status_code == 400
    control = httpx.post(f"{SERVER.base_url}/_control", json={"fault": "invented"}, timeout=5.0)
    assert control.status_code == 400
    assert control.json()["error"]["code"] == "unsupported_parameter"
    assert httpx.get(f"{SERVER.base_url}/_control", timeout=5.0).json()["fault"] == "none", \
        "a refused control changed nothing"


def test_chunk_ids_and_created_are_seedable():
    """Same pass: with a seed the transcript is comparable between runs; without one the ids
    are random and `created` is the wall clock, as a real engine's are."""
    def lines(app):
        return [payload for _kind, payload in app.script(EngineFault.none, "")]

    assert lines(fake_vllm.FakeVllmApp(seed=7)) == lines(fake_vllm.FakeVllmApp(seed=7))
    assert lines(fake_vllm.FakeVllmApp(seed=7)) != lines(fake_vllm.FakeVllmApp(seed=8))
    assert lines(fake_vllm.FakeVllmApp()) != lines(fake_vllm.FakeVllmApp()), \
        "no seed means no determinism, which is what a real engine looks like"
    assert f'"created":{fake_vllm.SEEDED_CREATED + 1}' in lines(fake_vllm.FakeVllmApp(seed=7))[0]


def test_a_per_request_fault_precedence_is_header_then_body_then_default():
    """Three levels, all three checked: so one scripted request cannot disturb a concurrent
    one, and a client that cannot send a header (a gateway under test) still has /_control."""
    SERVER.control(fault="missing_usage")
    try:
        both = httpx.post(f"{SERVER.base_url}/v1/chat/completions", timeout=10.0,
                          json={"stream": True, "model": "m", "messages": [],
                                "infrx_fault": "missing_usage"},
                          headers={"X-Infrx-Fault": "split_reasoning_delimiters"})
        assert "<th" in both.text and "ink>" in both.text, "the header wins over the body"
        body_only = httpx.post(f"{SERVER.base_url}/v1/chat/completions", timeout=10.0,
                               json={"stream": True, "model": "m", "messages": [],
                                     "infrx_fault": "split_reasoning_delimiters"}).text
        assert "<th" in body_only and "ink>" in body_only, "the body wins over the default"
        default = httpx.post(f"{SERVER.base_url}/v1/chat/completions", timeout=10.0,
                             json={"stream": True, "model": "m", "messages": []}).text
        assert '"usage"' not in default, "the process default applies when neither is given"
    finally:
        SERVER.reset()


def test_health_and_drain_and_the_non_stream_path_answer():
    engine = adapter()
    assert asyncio.run(engine.health())["ready"] is True
    asyncio.run(engine.drain())
    assert asyncio.run(engine.health())["ready"] is False
    SERVER.reset()
    body = httpx.post(f"{SERVER.base_url}/v1/chat/completions",
                      json={"stream": False, "model": "m", "messages": []}, timeout=10.0).json()
    assert body["choices"][0]["message"]["content"] == DEFAULT_TEXT
    assert body["usage"]["completion_tokens"] > 0
    assert httpx.get(f"{SERVER.base_url}/v1/models", timeout=5.0).json()["data"][0]["id"]
    assert httpx.get(f"{SERVER.base_url}/nope", timeout=5.0).status_code == 404


# ------------------------------------------------------------------ process-kill fault

def test_killing_the_engine_process_is_a_transport_failure_and_it_restarts():
    """Fault injection: process loss. SIGKILL, not SIGTERM - no flush, no goodbye. The
    client must see a transport failure rather than an empty success, and the harness must
    be able to put the engine back."""
    victim = FakeVllmServer(TEST_PORT + 1).start()
    engine = HttpEngine(victim.base_url, clock=FakeClock(), timeout=5.0)
    assert asyncio.run(engine.health())["ready"] is True
    assert victim.kill(signal.SIGKILL) != 0
    assert not victim.alive()
    with pytest.raises(httpx.HTTPError):
        asyncio.run(engine.health())
    with pytest.raises(EngineProcessExited):
        asyncio.run(drain(engine, lease()))
    victim.start()
    try:
        assert asyncio.run(engine.health())["ready"] is True
    finally:
        victim.stop()


# ------------------------------------------------------------------ canary

def test_the_server_log_is_never_left_behind_in_the_temp_directory():
    """r2 review minor M43: `TemporaryFile` -> `NamedTemporaryFile(delete=False)` leaked four
    logs and nothing noticed. The file is unlinked at creation, so it must never be visible in
    $TMPDIR - not while the server runs, not after `stop()`, and not after a `kill()` either,
    which is the path that used to leak (the owner dies, so nothing removes the name).
    """
    import tempfile
    # The temp directory is redirected to one this case owns, for two reasons: the assertion is
    # about the directory the server actually writes into, and a MUTANT that reintroduces the
    # leak then leaks into a directory that vanishes with this case instead of into the shared
    # /tmp (measured: proving this claim used to leave 16 files behind).
    previous = tempfile.tempdir
    with tempfile.TemporaryDirectory(prefix="e2-logscope-") as scope:
        tempfile.tempdir = scope
        try:
            left = lambda: sorted(Path(scope).iterdir())
            assert left() == [], scope
            victim = FakeVllmServer(TEST_PORT + 5).start()
            try:
                assert left() == [], f"the server's log is visible in its temp dir: {left()}"
                # It still has to be READABLE - the diagnostic is the point of keeping it.
                assert victim._tail() is not None
                assert asyncio.run(HttpEngine(victim.base_url,
                                              clock=FakeClock()).health())["ready"]
            finally:
                victim.stop()
            assert left() == [], f"stop() must leave nothing either: {left()}"

            killed = FakeVllmServer(TEST_PORT + 5).start()
            killed.kill(signal.SIGKILL)
            assert left() == [], \
                f"a SIGKILLed server must leave no log - the owner is gone, so nothing can " \
                f"unlink a name: {left()}"
            killed.stop()
            assert left() == []
        finally:
            tempfile.tempdir = previous


def test_canary_intentional_failure_is_detected():
    """E2 acceptance: "intentional failure is detected rather than skipped".

    Off by default. `INFRX_E2_CANARY=fail` makes this case fail on purpose; run.py runs it
    that way and treats a green result as the failure, because a green result would mean the
    runner cannot see a broken test. It is a failure, deliberately NOT a skip: a skip is
    what we are proving does not happen.
    """
    if os.environ.get("INFRX_E2_CANARY") == "fail":
        raise AssertionError("E2 canary: this failure is intentional (INFRX_E2_CANARY=fail)")
    assert os.environ.get("INFRX_E2_CANARY") in (None, "", "off")
