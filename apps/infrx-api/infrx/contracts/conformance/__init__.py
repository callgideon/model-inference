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
from dataclasses import dataclass, field
from typing import Any, Callable

from .jobs import jobstore_cases, streamstore_cases
from .services import (engine_cases, feedback_cases, judge_cases, mediastore_cases,
                       scheduler_cases, tracesink_cases)


@dataclass
class Harness:
    """What a factory hands a case: the adapter plus the hooks it needs.

    `extra` holds the named hooks a suite documents (for JobStore: `grant`,
    `balance`, `active_jobs`, `outbox`, `outbox_kinds`, and optionally `publish`,
    `revoke_key`, `unrevoke_key`, `suspend_org`, `unentitle`, `retune`,
    `journal_bytes`; for JudgeCoordinator: `available`, `runs`, `set_consent`,
    `revoke_consent`, `audit`). A missing optional hook makes the case return early
    rather than fail, so an adapter can adopt the suite in steps.

    The streamstore, scheduler and feedback factories also publish `extra["jobs"]`,
    the JobStore a case needs to admit a job first. The cases only ever call *port*
    operations on it, so a real adapter can pass its own JobStore there; nothing in
    a suite reads a fake's attributes.
    """

    port: Any
    clock: Any
    ids: Any
    failures: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


def run_cases(cases, factory: Callable[..., Harness]) -> int:
    for case in cases:
        asyncio.run(case(factory))
    return len(cases)


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

__all__ = ["Harness", "SUITES", "run_cases", *(f"run_{name}_conformance" for name in SUITES)]
