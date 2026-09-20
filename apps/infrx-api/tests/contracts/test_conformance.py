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
from infrx.contracts import ports
from infrx.contracts.conformance import SUITES
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
    asyncio.run(case(FACTORIES[port]))


@pytest.mark.parametrize("port", sorted(SUITES))
def test_runner_runs_the_whole_suite(port):
    """The importable entry point a track calls with its own factory."""
    _cases, runner = SUITES[port]
    assert runner(FACTORIES[port]) == len(_cases())


@pytest.mark.parametrize("port", sorted(PROTOCOLS))
def test_fake_satisfies_its_protocol(port):
    """Shape check: the fake has every operation the ports table names, async."""
    protocol, adapter = PROTOCOLS[port], FACTORIES[port]().port
    assert isinstance(adapter, protocol)
    for name in protocol.__protocol_attrs__:
        operation = getattr(adapter, name)
        assert callable(operation), name
        assert (inspect.iscoroutinefunction(operation)
                or inspect.isasyncgenfunction(operation)), f"{port}.{name} is not async"


def test_every_port_has_a_suite_and_a_fake():
    assert set(SUITES) == set(FACTORIES) == set(PROTOCOLS)


def test_case_names_carry_their_oracle():
    oracles = ("dur_admit", "dur_cap", "dur_fence", "dur_output", "dur_settle", "dur_outbox",
               "trace_bounds", "feedback_ack", "judge_budget", "api_stream", "media_sec",
               "media_parity")
    for port, case in CASES:
        assert case.__name__.split("__")[0] in oracles, (port, case.__name__)
        assert case.__doc__, f"{case.__name__} has no docstring naming its invariant"
