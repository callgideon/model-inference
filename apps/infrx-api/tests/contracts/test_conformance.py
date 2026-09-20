#!/usr/bin/env python3
"""F-CONTRACT: every conformance suite, applied to the fakes.

Each test id is the oracle plus the invariant, so `-k dur_settle` runs everything
that serves DUR-SETTLE and a track owner can see which cases its adapter must
also pass. Green here means implemented, never integrated.

    uv run --frozen pytest -q tests/contracts/test_conformance.py
    uv run --frozen pytest -q tests/contracts/test_conformance.py -k dur_fence
"""
from __future__ import annotations

import asyncio
import inspect

import pytest
from infrx.contracts import ports, records
from infrx.contracts.conformance import MissingHook, SUITES, run_cases
from infrx.contracts.fakes import FACTORIES

CASES = [(name, case) for name, (cases, _runner) in SUITES.items() for case in cases()]

PROTOCOLS = {
    "jobstore": ports.JobStore,
    "streamstore": ports.StreamStore,
    "mediastore": ports.MediaStore,
    "scheduler": ports.Scheduler,
    "engine": ports.Engine,
    "tracesink": ports.TraceSink,
    "feedback": ports.FeedbackService,
    "judge": ports.JudgeCoordinator,
}


@pytest.mark.parametrize("port,case", CASES, ids=[f"{port}-{case.__name__}" for port, case in CASES])
def test_fake_passes_conformance_case(port, case):
    try:
        asyncio.run(case(FACTORIES[port]))
    except MissingHook as missing:
        # r1 R32: a case the factory cannot drive is *skipped*, naming the hook, and
        # never counted as a pass. The fakes provide every hook, so this never fires
        # here; an adapter's own run is where it does.
        pytest.skip(f"{case.__name__} needs the optional hook {missing.hook!r}")


@pytest.mark.parametrize("port", sorted(SUITES))
def test_runner_runs_the_whole_suite(port):
    """The importable entry point a track calls with its own factory. `run_cases`
    refuses to treat a missing hook as a pass: without a `skipped` list it raises."""
    _cases, runner = SUITES[port]
    assert runner(FACTORIES[port]) == len(_cases())


def test_a_missing_hook_is_a_skip_not_a_pass():
    """R32, on the mechanism itself: a factory without an optional hook makes the
    case raise `MissingHook`, `run_cases` refuses it by default, and a caller that
    collects skips is told which hook and which case."""
    def crippled(limits=None, **kw):
        harness = FACTORIES["jobstore"](limits=limits, **kw)
        harness.extra.pop("publish")
        return harness

    cases, runner = SUITES["jobstore"]
    with pytest.raises(MissingHook) as caught:
        runner(crippled)
    assert caught.value.hook == "publish"
    skipped: list[MissingHook] = []
    ran = run_cases(cases(), crippled, skipped=skipped)
    assert skipped and all(missing.hook == "publish" for missing in skipped)
    assert {missing.case for missing in skipped}
    assert ran == len(cases()) - len(skipped)


# Deliberately synchronous, each for a stated reason: `Engine.generate` returns an
# async iterator, and `TraceSink.open` / `TraceCapture.add` run on the request path
# where they may not await (r1 R27).
SYNCHRONOUS = {("engine", "generate"), ("tracesink", "open")}


@pytest.mark.parametrize("port", sorted(PROTOCOLS))
def test_fake_satisfies_its_protocol(port):
    """Shape check: the fake has every operation the ports table names, async unless
    the contract says otherwise."""
    protocol, adapter = PROTOCOLS[port], FACTORIES[port]().port
    assert isinstance(adapter, protocol)
    for name in protocol.__protocol_attrs__:
        operation = getattr(adapter, name)
        assert callable(operation), name
        if (port, name) in SYNCHRONOUS:
            assert not inspect.iscoroutinefunction(operation), f"{port}.{name} became async"
            continue
        assert (inspect.iscoroutinefunction(operation)
                or inspect.isasyncgenfunction(operation)), f"{port}.{name} is not async"


def test_the_trace_capture_shape_is_what_the_port_declares():
    """r1 R27: `add` is synchronous (the request path cannot await), `finish` and
    `abandon` are not."""
    sink = FACTORIES["tracesink"]().port
    capture = sink.open("00000000-0000-4000-8000-000000000001",
                        "11111111-0000-4000-8000-000000000001", records.TraceMode.full)
    assert isinstance(capture, ports.TraceCapture)
    assert not inspect.iscoroutinefunction(capture.add)
    for name in ("finish", "abandon"):
        assert inspect.iscoroutinefunction(getattr(capture, name)), name


def test_every_port_has_a_suite_and_a_fake():
    assert set(SUITES) == set(FACTORIES) == set(PROTOCOLS)


def test_case_names_carry_their_oracle():
    oracles = ("dur_admit", "dur_cap", "dur_fence", "dur_output", "dur_settle", "dur_outbox",
               "trace_bounds", "feedback_ack", "judge_budget", "api_stream", "media_sec",
               "media_parity")
    for port, case in CASES:
        assert case.__name__.split("__")[0] in oracles, (port, case.__name__)
        assert case.__doc__, f"{case.__name__} has no docstring naming its invariant"
