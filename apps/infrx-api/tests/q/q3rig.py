"""Q3's world: the contract `FakeJobStore` as PostgreSQL, D2's dispatch outbox over it
(`outboxfake`), one index (memory or task-local Valkey) and one `Reconciler`.

Workers are the W2 loop's shape, spelled out so a case can stop between steps: take a
candidate, let the store decide (`claim_preparation`/`claim`), acknowledge the candidate
whatever the store said. Every successful claim is counted per job, which is the oracle
for "a stale candidate never acquires a second lease".
"""
from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes.factories import jobstore_factory
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import ExecutionMode, JobState, OutboxKind
from infrx.scheduling import MemoryScheduler, ValkeyScheduler
from infrx.scheduling.reconcile import Reconciler

from . import vkharness
from .outboxfake import FakeDispatchOutbox, OutboxLost

ORG_A, KEY_A, ORG_B, KEY_B = b.ORG_A, b.KEY_A, b.ORG_B, b.KEY_B
ADAPTERS = ("memory", "valkey")
PREP, INFER = OutboxKind.prepare_dispatch, OutboxKind.inference_dispatch
REFUSED = (errors.NotClaimable, errors.AlreadyTerminal, errors.NotFound, errors.StaleLease)


@dataclass
class World:
    h: Any
    outbox: FakeDispatchOutbox
    index: Any
    rec: Reconciler
    leases: Counter = field(default_factory=Counter)        # job_id -> inference claims won
    prepared: Counter = field(default_factory=Counter)      # job_id -> preparations won
    admitted: list = field(default_factory=list)

    @property
    def jobs(self):
        return self.h.port


def make_index(adapter: str, now, **kw):
    if adapter == "memory":
        return MemoryScheduler(now, **kw)
    return ValkeyScheduler(vkharness.client(), now, namespace=vkharness.namespace(), **kw)


#: D3 request 5 / D5 item 9e: `INFRX_Q3_STORE=postgres` runs every case on the REAL store -
#: the PostgreSQL `PgJobStore` and D2's dispatch outbox functions on the D harness's own
#: task-local database (`tests/d/pgstore`), with the store-side reads the cases make of the
#: fake (`pgtesting.PgDispatchOutbox`, `pgtesting.JobsView`).
STORE = os.environ.get("INFRX_Q3_STORE", "fake")


def world(adapter: str, **index_kw) -> World:
    # Async jobs: their 600 s queue budget outlives the redeliveries a case waits for
    # (interactive jobs expire after 10 s queued); room for more than 8 in preparation.
    limits = DEFAULTS.replace(max_preparing_jobs=256)
    if STORE == "postgres":
        from infrx.state import pgtesting
        from ..d import pgstore
        # The real store runs each step slower than the fake, so the concurrent drills keep
        # more jobs active at once: room for them (a rig setting, not store semantics).
        h = pgstore.factory(limits=limits.replace(max_active_jobs=256, max_active_jobs_per_org=256,
                                                  max_active_jobs_per_key=256))
        store, rows = h.extra["store"], h.extra["conn"]
        store.jobs = pgtesting.JobsView(rows)         # `w.jobs.jobs[id]`, read from the rows
        outbox = pgtesting.PgDispatchOutbox(store, rows, lost=OutboxLost)
    else:
        h = jobstore_factory(limits=limits)
        outbox = FakeDispatchOutbox(h.port)
    for org in (ORG_A, ORG_B):
        h.extra["grant"](org, "1000")
    index = make_index(adapter, h.clock.now, **index_kw)
    return World(h, outbox, index, Reconciler(outbox, index, h.clock.now))


async def admit(w: World, org: str = ORG_A) -> str:
    request = b.request(w.h, org_id=org, key_id=KEY_A if org == ORG_A else KEY_B,
                        mode=ExecutionMode.async_)
    admission = await w.jobs.admit(request, b.idem(request, None))
    w.admitted.append(admission.request_id)
    return admission.request_id


async def admit_in_order(w: World, n: int) -> list[str]:
    """`n` admissions 1 us apart. PostgreSQL breaks an `available_at` tie on a random
    uuid (the fake's ids are monotonic), so a case that relies on delivery order must
    not tie (review FID-2)."""
    jobs = []
    for _ in range(n):
        jobs.append(await admit(w))
        w.h.clock.advance(1e-6)
    return jobs


async def prepare_one(w: World, worker: str = "prep"):
    """One preparation worker step; the candidate, or None if the index had none."""
    candidate = await w.index.claim_candidate(worker, kind=PREP)
    if candidate is None:
        return None
    try:
        lease = await w.jobs.claim_preparation(candidate.job_id, worker)
        w.prepared[candidate.job_id] += 1
        await w.jobs.prepared(lease, ())
    except REFUSED:
        pass
    finally:
        await w.index.acknowledge(candidate)
    return candidate


async def infer_one(w: World, worker: str = "gpu", *, finish: bool = True):
    """One inference worker step; (candidate, lease or None), or None if idle."""
    candidate = await w.index.claim_candidate(worker, kind=INFER)
    if candidate is None:
        return None
    lease = None
    try:
        lease = await w.jobs.claim(candidate.job_id, worker)
        w.leases[candidate.job_id] += 1
        if finish:
            await w.jobs.complete(lease, b.outcome(candidate.job_id, w.h))
    except REFUSED:
        pass
    finally:
        await w.index.acknowledge(candidate)
    return candidate, lease


async def run_dry(w: World, *, limit: int = 1000) -> int:
    """Drain and work until nothing moves; the number of worker steps taken."""
    steps = 0
    while steps < limit:
        await w.rec.drain()
        moved = (await prepare_one(w)) or (await infer_one(w))
        if not moved:
            return steps
        steps += 1
    raise AssertionError(f"the pipeline never went idle in {limit} steps")


async def finish(w: World, *, rounds: int = 20) -> None:
    """`run_dry`, then let deferred rows come back (`redelivery_s`), until every admitted
    job is terminal."""
    for _ in range(rounds):
        await run_dry(w)
        if all(w.jobs.jobs[job_id].terminal for job_id in w.admitted):
            return
        w.h.clock.advance(w.rec.redelivery_s)
    raise AssertionError(f"jobs left unfinished: "
                         f"{[j for j in w.admitted if not w.jobs.jobs[j].terminal]}")


def state(w: World, job_id: str) -> JobState:
    return w.jobs.jobs[job_id].state


def settled(w: World) -> None:
    """The acceptance: every accepted job terminal or still owned by a live lease, and no
    job ever held a second preparation or inference lease."""
    now = w.h.clock.now()
    for job_id in w.admitted:
        job = w.jobs.jobs[job_id]
        owned = job.state is JobState.running and job.lease is not None \
            and job.lease.expires_at > now
        assert job.terminal or owned, f"{job_id} is {job.state} and nobody owns it"
    assert max(w.leases.values(), default=0) <= 1, f"a second inference lease: {w.leases}"
    assert max(w.prepared.values(), default=0) <= 1, f"a second preparation: {w.prepared}"


async def members(w: World) -> dict[str, str]:
    return await w.index.members()
