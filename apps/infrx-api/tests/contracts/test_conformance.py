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
import dataclasses
import inspect

import pytest
from infrx.contracts import ports, records
from infrx.contracts.conformance import (MissingHook, OPTIONAL_HOOKS, SUITES,
                                          run_cases)
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


def test_a_hookless_factory_skips_and_never_silently_passes():
    """r1 R32 / r4 B3: with **no** optional hook at all, every case that depends on one
    is skipped naming it, and every case that runs really is hook-free. A case that
    quietly asserted less would show up here as a pass it has not earned.

    The report is printed so an adapter's evidence can quote it rather than claim it.
    """
    report: dict[str, dict[str, str]] = {}
    for port, (cases, _runner) in SUITES.items():
        def hookless(limits=None, _port=port, **kw):
            harness = FACTORIES[_port](limits=limits, **kw)
            required = set(harness.extra) - OPTIONAL_HOOKS.get(_port, frozenset())
            return dataclasses.replace(
                harness, failures=None,
                extra={name: harness.extra[name] for name in required})

        skipped: list[MissingHook] = []
        ran = run_cases(cases(), hookless, skipped=skipped)
        report[port] = {missing.case: missing.hook for missing in skipped}
        assert ran + len(skipped) == len(cases())
        # The streamstore and feedback suites must admit a job first, so every one of
        # their cases depends on the `jobs` hook: a suite skipping entirely is honest,
        # a suite *passing* entirely on no hooks would not be.
        assert ran > 0 or port in ("streamstore", "feedback"), \
            f"{port}: every case needed a hook"
    print("\nhookless-factory skips:")
    for port, skips in sorted(report.items()):
        print(f"  {port}: {len(skips)} skipped " + (str(sorted(skips.items())) if skips else ""))
    # the hook-dependent cases are exactly the ones that skipped: any case that reads a
    # hook through `hook()` appears here, and no other case does
    hook_dependent = {port: {case for case, _hook in skips.items()}
                      for port, skips in report.items()}
    assert hook_dependent["jobstore"], "no jobstore case reported its hooks"
    assert "dur_admit__a_refused_admission_reserves_nothing" in hook_dependent["jobstore"]
    assert "judge_budget__revoked_or_missing_consent_is_refused_before_egress" \
        in hook_dependent["judge"]
    assert "dur_output__loss_after_publication_is_a_terminal_failure" in hook_dependent["jobstore"]


def test_the_fakes_skip_nothing():
    """The other half: with the real factories every case runs, so the headline count
    is what it says it is."""
    for port, (cases, runner) in SUITES.items():
        skipped: list[MissingHook] = []
        ran = run_cases(cases(), FACTORIES[port], skipped=skipped)
        assert skipped == [], f"{port} skipped {[(s.case, s.hook) for s in skipped]}"
        assert ran == len(cases())


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
SYNCHRONOUS = {("engine", "generate"), ("tracesink", "open"), ("tracesink", "reap")}


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


def test_a_sink_built_from_the_declared_signatures_alone_satisfies_the_suite():
    """r4 F1: the exported cases must be callable against a sink written from
    `ports.TraceSink`/`ports.TraceCapture` **and nothing else**.

    The previous round's port declared `open(request_id, org_id, mode)` while the suite
    called `open(..., deadline)`, so an adapter matching the Protocol exactly passed
    `isinstance` and then died with a `TypeError` inside a conformance case. This builds
    the minimal sink the signatures describe and makes every call the suite makes.
    """
    from infrx.contracts.records import TraceLossReason, TraceMode, TraceOfferResult

    class MinimalCapture:
        """Written from the Protocol's docstrings: no-op, but shaped exactly."""

        def __init__(self):
            self.finished = None

        def add(self, part):
            return isinstance(part, (bytes, str))

        async def finish(self, envelope):
            if self.finished is None:
                self.finished = TraceOfferResult.accepted_in_memory
            return self.finished

        async def abandon(self, reason):
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class MinimalSink:
        def open(self, request_id, org_id, mode, deadline_at):
            return MinimalCapture()

        async def offer(self, envelope):
            return TraceOfferResult.accepted_in_memory

        async def stats(self):
            return {"accepted": 0, "dropped": 0, "in_memory": 0, "in_memory_content_bytes": 0,
                    "in_memory_metadata_bytes": 0, "open_captures": 0, "appended": 0,
                    "fsynced": 0, "loss_reasons": {}}

        async def flush(self, deadline):
            return await self.stats()

        def reap(self, grace_s):
            return 0

    sink, capture_factory = MinimalSink(), MinimalCapture()
    assert isinstance(sink, ports.TraceSink)
    assert isinstance(capture_factory, ports.TraceCapture)
    # every shape the suite relies on, against the declaration alone
    clock = FACTORIES["tracesink"]().clock
    capture = sink.open("00000000-0000-4000-8000-000000000001",
                        "11111111-0000-4000-8000-000000000001", records.TraceMode.full,
                        clock.at(600))
    assert capture.add("bytes") is True
    with capture as entered:
        assert entered is capture
    assert asyncio.run(capture.abandon(records.TraceLossReason.abandoned)) is None
    assert sink.reap(60.0) == 0
    assert asyncio.run(sink.stats())["loss_reasons"] == {}
    # and the real fake accepts the same calls in the same order
    real = FACTORIES["tracesink"]()
    live = real.port.open("00000000-0000-4000-8000-000000000002",
                          "11111111-0000-4000-8000-000000000001", records.TraceMode.full,
                          real.clock.at(600))
    with live:
        assert live.add(b"bytes") is True
    assert real.port.reap(60.0) == 0
    for mode in (records.TraceMode.off, records.TraceMode.minimal):
        noop = real.port.open("00000000-0000-4000-8000-000000000003",
                              "11111111-0000-4000-8000-000000000001", mode, real.clock.at(600))
        assert noop.add("x") is False
        with noop:
            pass


def test_the_trace_capture_shape_is_what_the_port_declares():
    """r1 R27: `add` is synchronous (the request path cannot await), `finish` and
    `abandon` are not."""
    sink = FACTORIES["tracesink"]().port
    capture = sink.open("00000000-0000-4000-8000-000000000001",
                        "11111111-0000-4000-8000-000000000001", records.TraceMode.full,
                        FACTORIES["tracesink"]().clock.at(600))
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
