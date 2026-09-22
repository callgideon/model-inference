"""W2: the worker loop around `AttemptRunner` - claiming, concurrency and drain.

    loop = WorkerLoop(scheduler=index, runner=runner, worker_id="worker-a")
    await loop.run(concurrency=2, stop_when_idle=True)      # a pool that drains the index
    await loop.drain(within_s=5.0)                          # stop claiming, then release

Three rules make this small:

* **The index is a hint (`02` §4).** `claim_candidate` offers a job; `JobStore.claim`
  decides the winner, and a candidate whose claim loses is acknowledged exactly like one
  whose claim won - it is off this worker's hands either way. The reconciler repairs
  queued jobs missing from the index; nothing here re-enqueues.
* **A lease is never resumed.** A worker that died holds a lease until it expires;
  `JobStore.recover` then requeues the attempt (prepublication only) as a **new**
  generation, which arrives here as an ordinary candidate. There is no restart path that
  picks an old lease back up, because r1 R46 makes one generation one attempt.
* **Drain preserves durable jobs.** Draining stops claiming and waits, within a bound,
  for what is in flight. What is still running at the bound is *released*, not settled:
  the task is cancelled, the lease is left to expire and the store decides - a
  prepublication attempt is requeued, a published one fails honestly
  (`lost_after_publication`). Settling on the way out would be this worker inventing an
  outcome for work it did not finish.

Only inference candidates are claimed. Preparation is its own fenced attempt sequence on
its own lease kind (r1 R46/R52) and belongs to M's preparation worker; a kind-filtered
claim also keeps this pool out of the level-1 fairness state (r1 R60).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from ..contracts.limits import DEFAULTS, PilotSettings
from ..contracts.records import IndexEvent, OutboxKind
from .attempt import AttemptResult, AttemptRunner


@dataclass
class DrainReport:
    finished: int = 0          # runners that stopped on their own inside the bound
    released: int = 0          # runners cancelled at the bound, their jobs left to the store
    claimed: int = 0           # attempts this loop claimed in total


@dataclass
class WorkerLoop:
    """A pool of sequential runners sharing one index and one attempt runner."""

    scheduler: object                    # ports.Scheduler
    runner: AttemptRunner
    worker_id: str = "worker"
    kind: OutboxKind = OutboxKind.inference_dispatch
    limits: PilotSettings = DEFAULTS
    # A real deployment polls an empty index; every test stops instead, so nothing here
    # ever sleeps unless a caller asks it to.
    idle_sleep_s: float = 0.0
    results: list[AttemptResult] = field(default_factory=list)
    claimed: int = 0
    draining: bool = False
    _tasks: list = field(default_factory=list)

    # --- one candidate --------------------------------------------------------
    async def claim_one(self) -> AttemptResult | None:
        """Take one candidate and run it. `None` means the index had nothing."""
        candidate = await self.scheduler.claim_candidate(self.worker_id, kind=self.kind)
        if candidate is None:
            return None
        self.claimed += 1
        try:
            result = await self.runner.run(candidate.job_id)
        finally:
            # Always: the candidate is consumed whether the claim won, lost or failed.
            # A job that needs another attempt is re-dispatched by the store, as a new
            # index event for a new generation.
            await self._acknowledge(candidate)
        self.results.append(result)
        return result

    async def _acknowledge(self, candidate: IndexEvent) -> None:
        try:
            await self.scheduler.acknowledge(candidate)
        except Exception:
            # An index that refuses an acknowledgment is a reconciler problem, not a
            # reason to fail an attempt whose outcome is already durable in PostgreSQL.
            pass

    # --- the pool -------------------------------------------------------------
    async def run(self, *, concurrency: int = 1, stop_when_idle: bool = True,
                  max_claims: int | None = None) -> list[AttemptResult]:
        """Run `concurrency` sequential runners until they are stopped or the index runs
        dry. Returns every result, in completion order."""
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        self.draining = False
        self._tasks = [asyncio.create_task(self._runner(stop_when_idle, max_claims),
                                           name=f"{self.worker_id}-{n}")
                       for n in range(concurrency)]
        await asyncio.gather(*self._tasks, return_exceptions=True)
        return list(self.results)

    async def _runner(self, stop_when_idle: bool, max_claims: int | None) -> None:
        while not self.draining:
            if max_claims is not None and self.claimed >= max_claims:
                return
            if await self.claim_one() is None:
                if stop_when_idle:
                    return
                await asyncio.sleep(self.idle_sleep_s)

    async def drain(self, within_s: float) -> DrainReport:
        """Stop claiming, wait for what is in flight, release the rest at the bound."""
        self.draining = True
        tasks = [task for task in self._tasks if not task.done()]
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=max(0.0, within_s))
        else:
            pending = set()
        for task in pending:
            # Released, not settled: the store fences the lease and `recover` decides.
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return DrainReport(finished=len(self._tasks) - len(pending), released=len(pending),
                           claimed=self.claimed)
