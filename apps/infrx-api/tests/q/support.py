"""Shared fixtures for the Q suite: the real adapter behind the shared harness.

R48: a helper module, imported relatively (`from . import support`) under importlib
mode, and never importing the legacy `gateway` shim.
"""
from __future__ import annotations

import dataclasses

from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes.factories import scheduler_factory
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import ExecutionMode, IndexEvent, OutboxKind
from infrx.scheduling import MemoryScheduler

ORG_A = b.ORG_A
ORG_B = "1b1b1b1b-0000-4000-8000-000000000002"
ORG_C = "1c1c1c1c-0000-4000-8000-000000000003"
# job ids are UUIDv4 text (`08` §3), so a test that needs a stable one spells it out
JOB_1 = "2a2a2a2a-0000-4000-8000-000000000001"
JOB_2 = "2a2a2a2a-0000-4000-8000-000000000002"
JOB_3 = "2a2a2a2a-0000-4000-8000-000000000003"


def harness(limits=None, *, weights=None, cost=None, max_items=None, max_bytes=None, **kw):
    """The shared scheduler harness with the *real* adapter in `port`.

    Everything else - the clock, the id source, the fake JobStore under `extra["jobs"]`
    and the wallet hooks the conformance cases use to admit a job - comes from the
    coordinator's factory, so this adapter runs the same cases the fake does with the
    same collaborators (`08` §2). Only `port` differs.
    """
    inner = scheduler_factory(limits=limits, **kw)
    settings = limits or DEFAULTS
    extra = {name: value for name, value in {"weights": weights, "cost": cost,
                                             "max_items": max_items,
                                             "max_bytes": max_bytes}.items()
             if value is not None}
    port = MemoryScheduler(inner.clock.now, limits=settings, **extra)
    return dataclasses.replace(inner, port=port)


def event(harness_, *, org_id=ORG_A, job_id=None, kind=OutboxKind.inference_dispatch,
          available_in_s=0.0, attempt=0, mode=ExecutionMode.async_) -> IndexEvent:
    return IndexEvent(event_id=harness_.ids.event_id(), job_id=job_id or harness_.ids.uuid(),
                      org_id=org_id, key_id=b.KEY_A, kind=kind, execution_mode=mode,
                      available_at=harness_.clock.at(available_in_s), attempt=attempt)


async def drain(port, worker="worker-a", *, kind=None, limit=10_000):
    """Claim and acknowledge until the index is empty; returns the dispatch order."""
    order = []
    while len(order) < limit:
        candidate = await port.claim_candidate(worker, kind=kind)
        if candidate is None:
            return order
        order.append(candidate)
        await port.acknowledge(candidate)
    raise AssertionError(f"the index never emptied after {limit} dispatches")
