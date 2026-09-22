"""I3B.b: the world a recovery drill runs in, and the one oracle every drill ends with.

`World` is one durable store (the contracts' reference `FakeJobStore`/`FakeStreamStore` -
the D2-D5 semantics until the PostgreSQL adapter exists), one scheduling index (the fake,
or Q2's real `ValkeyScheduler` on E2's Valkey), W2's real `AttemptRunner`/`WorkerLoop`,
M2's real `MediaPreparation` when a drill needs media, and a metrics `Registry`.

Three pieces of glue are *emulated* here because the process that owns them does not
exist on this base, and each is named with the task that delivers it:

* `dispatch`   - enqueue what the store made dispatchable (Q3's outbox dispatcher);
* `prepare`    - claim a preparation lease, prepare, report `prepared` (M/W3's worker);
* `reap`       - one `JobStore.recover()` pass plus the metrics helper (W3's reaper timer).

`reconcile()` is the invariant set I3B.b demands after every drill: accepted jobs, pins,
holds and terminal usage.
"""
from __future__ import annotations

import asyncio
import importlib.util
import socket
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The repository this file lives in - the COPY's, under the mutation runner, so a mutated
# alert rule or runbook in the copy is what the cases read.
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE.parent))

import stack                                            # noqa: E402,F401  (infrx on path)

import harness                                          # noqa: E402

from infrx.contracts import errors                      # noqa: E402
from infrx.contracts.conformance import builders as b   # noqa: E402
from infrx.contracts.fakes.engine import FakeEngine     # noqa: E402
from infrx.contracts.fakes.scheduling import FakeScheduler  # noqa: E402
from infrx.contracts.fakes.state import FakeJobStore, FakeStreamStore  # noqa: E402
from infrx.contracts.fakes.support import FailurePlan, FakeClock, SequentialIds  # noqa: E402
from infrx.contracts.limits import DEFAULTS             # noqa: E402
from infrx.contracts.records import (ChunkEventType, EngineEvent, ExecutionMode,  # noqa: E402
                                     IndexEvent, JobState, OutboxKind, SettlementState)
from infrx.observe import alerts, metrics               # noqa: E402
from infrx.worker import AttemptRunner, WorkerLoop      # noqa: E402

GRANT = "25.00"
TENANTS = ((b.ORG_A, b.KEY_A), (b.ORG_B, b.KEY_B))
RULES_FILE = ROOT / "infra" / "alerts" / "alerts.json"


def delta(text: str) -> EngineEvent:
    return EngineEvent(type=ChunkEventType.delta, payload={"visible": text, "raw": text})


def free_port() -> int:
    """A loopback port nobody holds now (the fake engine is a host process, not a container)."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def m_support():
    """M's synthetic-container builders (`tests/m/support.py`), for a probe-valid clip."""
    spec = importlib.util.spec_from_file_location(
        "i3b_m_support", harness.API_ROOT / "tests" / "m" / "support.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def needs_stack():
    """The layer-3 drills run against E2's live stack; without one they skip the way every
    E2/E3B stack case does (a plain skip at layer 3 is a stage FAILURE, so this can only
    hide a case in a container-free run, never in the gate)."""
    import pytest
    if not harness.load_state() or not harness.owned_containers():
        pytest.skip("no infrx-e2 stack: run `tests/integration/run.py --layer 3`")


@dataclass
class World:
    limits: object = DEFAULTS
    scheduler: object | None = None
    failures: FailurePlan = field(default_factory=FailurePlan)

    def __post_init__(self) -> None:
        self.clock, self.ids = FakeClock(), SequentialIds()
        self.jobs = FakeJobStore(self.clock, self.ids, limits=self.limits,
                                 failures=self.failures, prices={b.MODEL: b.DEFAULT_PRICE})
        self.stream = FakeStreamStore(self.jobs, failures=self.failures)
        if self.scheduler is None:
            self.scheduler = FakeScheduler(self.clock, limits=self.limits)
        self.metrics = metrics.Registry("worker")
        self.accepted: dict[str, object] = {}         # request_id -> Admission at acceptance
        self.results: dict[str, str] = {}
        self.media = None
        for org, _key in TENANTS:
            self.jobs.grant(org, GRANT)

    # --- acceptance ---------------------------------------------------------------
    async def admit(self, tenant: int = 0, *, refs=(), mode=ExecutionMode.stream):
        org, key = TENANTS[tenant]
        request = b.request(self, org_id=org, key_id=key, mode=mode, refs=refs,
                            max_output_tokens=256)
        admission = await self.jobs.admit(request, b.idem(request, f"i3b-{request.request_id}"))
        self.accepted[admission.request_id] = admission
        self.metrics.inc("infrx_jobs_accepted_total", mode=mode, tenant=org)
        return admission

    async def queued(self, tenant: int = 0):
        """Admitted and prepared (no media): what an inference worker claims."""
        admission = await self.admit(tenant)
        lease = await self.jobs.claim_preparation(admission.request_id, "prep-a")
        await self.jobs.prepared(lease, ())
        return admission

    def candidate(self, job_id: str, *, kind=OutboxKind.inference_dispatch, attempt=0):
        admission = self.accepted[job_id]
        return IndexEvent(event_id=self.ids.event_id(), job_id=job_id, org_id=admission.org_id,
                          key_id=admission.key_id, kind=kind,
                          execution_mode=ExecutionMode.stream, available_at=self.clock.now(),
                          attempt=attempt)

    # --- the emulated glue ------------------------------------------------------------
    async def dispatch(self, *job_ids: str) -> None:
        """Q3's dispatcher, emulated: index the jobs the store made dispatchable."""
        for job_id in job_ids:
            await self.scheduler.enqueue(self.candidate(job_id))

    async def reap(self) -> tuple:
        """W3's reaper tick, emulated: one recover() pass, counted, its events indexed."""
        produced = await self.jobs.recover()
        metrics.record_recovery(self.metrics, produced)
        for item in produced:
            if isinstance(item, IndexEvent):
                await self.scheduler.enqueue(item)
        return produced

    def snapshot(self) -> tuple[IndexEvent, ...]:
        """What Q3's reconciler reads from PostgreSQL: every queued job, dispatchable."""
        return tuple(self.candidate(job.id) for job in self.jobs.jobs.values()
                     if job.state is JobState.queued)

    async def prepare(self, job_id: str, worker: str = "prep-a") -> bool:
        """M/W3's preparation worker, emulated: True when the job reached `queued`. A
        failure leaves the lease to expire - the reaper decides, never this worker."""
        lease = await self.jobs.claim_preparation(job_id, worker)
        try:
            refs = await self.media.prepare(job_id, "v1")
        except Exception:                            # noqa: BLE001 - the drill's fault
            return False
        await self.jobs.prepared(lease, refs)
        return True

    # --- workers ------------------------------------------------------------------
    def runner(self, engine, worker_id: str = "worker-a") -> AttemptRunner:
        async def put_result(job_id: str, text: str) -> str:
            self.results[job_id] = text
            return f"infrx-result:{job_id}"
        return AttemptRunner(jobs=self.jobs, stream=self.stream, engine=engine,
                             clock=self.clock, worker_id=worker_id,
                             count_prompt_tokens=lambda work: 1200, put_result=put_result,
                             limits=self.limits)

    def loop(self, engine=None, worker_id: str = "worker-a") -> WorkerLoop:
        engine = engine or FakeEngine(clock=self.clock, limits=self.limits)
        return WorkerLoop(scheduler=self.scheduler, runner=self.runner(engine, worker_id),
                          worker_id=worker_id, limits=self.limits, idle_sleep_s=0.01)

    async def finish(self, loop: WorkerLoop) -> list:
        """Run a loop until the index is dry; count every settled outcome."""
        results = await loop.run(concurrency=2, stop_when_idle=True)
        assert loop.failures == [], loop.failures
        for result in results:
            if result.outcome is not None:
                metrics.record_outcome(self.metrics, result.outcome)
        return results

    def scrape(self) -> dict:
        return alerts.parse(self.metrics.render())

    # --- the oracle ---------------------------------------------------------------
    async def reconcile(self, *, allow_unknown: int = 0) -> dict:
        """I3B.b after every drill: accepted jobs, pins, holds and terminal usage.

        * no accepted job is lost: each is still owned by its tenant and is terminal;
        * pins are the ones frozen at acceptance (price snapshot, hold, deadlines, budgets);
        * each terminal job has exactly one usage projection, a debit only when settled,
          and a succeeded job's output comes from exactly one generation (no duplicate
          executable attempt reached the customer);
        * per tenant: ledger = granted - settled debits, reserved = holds still held
          (held_unknown), available >= 0.
        Returns the counts the evidence quotes.
        """
        causes: dict[str, int] = {}
        held = 0
        for org, _key in TENANTS:
            debit, reserved = Decimal(0), Decimal(0)
            for request_id, accepted in self.accepted.items():
                if accepted.org_id != org:
                    continue
                now, outcome = await self.jobs.get_owned(org, accepted.job_handle)
                assert outcome is not None, f"accepted job {request_id} is {now.state}, not terminal"
                for pin in ("price_snapshot", "maximum_hold", "deadline_at", "budgets",
                            "admitted_at", "payload_hash"):
                    assert getattr(now, pin) == getattr(accepted, pin), (request_id, pin)
                projections = self.jobs.outbox_kinds(request_id).count(OutboxKind.usage_projection)
                assert projections == 1, (request_id, projections)
                if outcome.settlement_state is not SettlementState.settled:
                    assert outcome.debit == 0, (request_id, outcome)
                if outcome.settlement_state is SettlementState.held_unknown:
                    reserved += accepted.maximum_hold
                    held += 1
                if now.state is JobState.succeeded:
                    generations = {chunk.generation
                                   for chunk in self.stream.chunks.get(request_id, [])}
                    assert len(generations) == 1, (request_id, generations)
                debit += outcome.debit
                key = f"{outcome.state.value}/{outcome.cause.value}"
                causes[key] = causes.get(key, 0) + 1
            wallet = self.jobs.wallet(org)
            assert wallet.ledger_total == Decimal(GRANT) - debit, (org, wallet.ledger_total, debit)
            assert wallet.reserved_total == reserved, (org, wallet.reserved_total, reserved)
            assert wallet.available >= 0, (org, wallet.available)
        assert held <= allow_unknown, f"{held} holds still unknown"
        assert self.jobs.unsettleable == {}, self.jobs.unsettleable
        metrics.record_reconciliation(self.metrics, drift=0, holds_unknown=held,
                                      unsettleable=len(self.jobs.unsettleable),
                                      now=self.clock.now().timestamp())
        return {"accepted": len(self.accepted), "terminal": causes, "held_unknown": held}


def fired(world: World, before: dict, *, now: float | None = None) -> set[str]:
    """The alert rules that fire between two scrapes of the drill's metrics."""
    import json
    rules = json.loads(RULES_FILE.read_text())["rules"]
    firing = alerts.evaluate(rules, world.scrape(), before,
                             now=world.clock.now().timestamp() if now is None else now)
    return {alert["alert"] for alert in firing}


def run(body):
    return asyncio.run(body())


__all__ = ["World", "delta", "fired", "free_port", "m_support", "needs_stack", "run",
           "errors", "FakeEngine", "FakeClock"]
