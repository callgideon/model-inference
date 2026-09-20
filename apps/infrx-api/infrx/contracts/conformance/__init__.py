"""Importable conformance suites: one per port, runnable against any adapter.

A track runs its real adapter through the same cases the fakes pass:

    from infrx.contracts.conformance import run_jobstore_conformance
    run_jobstore_conformance(my_factory)          # raises AssertionError on failure

`factory(limits=None, **kw) -> Harness` must return a *fresh*, empty adapter each
call, with the clock and id source the case can drive. Passing the suite on a fake
means implemented; only the suite against the real service means integrated.

Case names carry their oracle (`dur_admit__...`, `api_stream__...`), so
`pytest -k dur_settle` selects everything that serves DUR-SETTLE.
"""
from __future__ import annotations

import asyncio
from typing import Callable

from .harness import Harness, MissingHook, OPTIONAL_HOOKS, hook
from .jobs import jobstore_cases, streamstore_cases
from .services import (engine_cases, feedback_cases, judge_cases, mediastore_cases,
                       scheduler_cases, tracesink_cases)


def run_cases(cases, factory: Callable[..., Harness], *,
              skipped: list[MissingHook] | None = None) -> int:
    """Run a suite and return how many cases actually ran.

    A case that needs an optional hook the factory does not provide raises
    `MissingHook`. With `skipped` given it is collected and the run continues, and the
    caller must report it; without, it fails the run. A skip is never a pass (R32).
    """
    ran = 0
    for case in cases:
        try:
            asyncio.run(case(factory))
        except MissingHook as missing:
            if skipped is None:
                raise
            missing.case = case.__name__
            skipped.append(missing)
            continue
        ran += 1
    return ran


def run_jobstore_conformance(factory) -> int:
    return run_cases(jobstore_cases(), factory)


def run_streamstore_conformance(factory) -> int:
    return run_cases(streamstore_cases(), factory)


def run_mediastore_conformance(factory) -> int:
    return run_cases(mediastore_cases(), factory)


def run_scheduler_conformance(factory) -> int:
    return run_cases(scheduler_cases(), factory)


def run_engine_conformance(factory) -> int:
    return run_cases(engine_cases(), factory)


def run_tracesink_conformance(factory) -> int:
    return run_cases(tracesink_cases(), factory)


def run_feedback_conformance(factory) -> int:
    return run_cases(feedback_cases(), factory)


def run_judge_conformance(factory) -> int:
    return run_cases(judge_cases(), factory)


# port name -> (cases getter, runner). tests/contracts iterates this.
SUITES: dict[str, tuple[Callable[[], list], Callable[..., int]]] = {
    "jobstore": (jobstore_cases, run_jobstore_conformance),
    "streamstore": (streamstore_cases, run_streamstore_conformance),
    "mediastore": (mediastore_cases, run_mediastore_conformance),
    "scheduler": (scheduler_cases, run_scheduler_conformance),
    "engine": (engine_cases, run_engine_conformance),
    "tracesink": (tracesink_cases, run_tracesink_conformance),
    "feedback": (feedback_cases, run_feedback_conformance),
    "judge": (judge_cases, run_judge_conformance),
}

__all__ = ["Harness", "MissingHook", "OPTIONAL_HOOKS", "SUITES", "hook", "run_cases",
           *(f"run_{name}_conformance" for name in SUITES)]
