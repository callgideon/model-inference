#!/usr/bin/env python3
"""W5 / ADMISSION-READY + DUR-FENCE + OPS-RECOVER: execution readiness and bounded recovery.

    uv run --frozen pytest -q tests/w/test_w5.py

Service-free, on PREP-WORKER's rig (`test_prep_worker.Prep`: F's fake store and index on one
clock, E2's fake engine in process, M's real `MediaPreparation`) and W2's world
(`test_loop.World`). The durable attach record a pre-D10 store keeps is `Prep.attached`;
the execution-ready marker is F2C.a's reference adapter `FakeLifecycle` (the D1 decision:
the admission records the normalized source manifest, possibly EMPTY, and the marker in one
transaction; a preparation claim requires the marker) wired as the runner's `readiness=`,
where the product wires D10's PostgreSQL adapter.
"""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import time
import uuid
from types import SimpleNamespace

import pytest

from infrx.config import Settings
from infrx.contracts import errors
from infrx.contracts.conformance import Harness, acceptance, builders as b
from infrx.contracts.fakes.factories import lifecycle_factory
from infrx.contracts.fakes.lifecycle import FakeLifecycle
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import (HoldState, IndexEvent, JobState, OutboxKind,
                                     SettlementState, TerminalCause, Usage, UsageCertainty)
from infrx.contracts.v2 import fixtures as v2fix
from infrx.contracts.v2.lifecycle import AdmissionExpectation
from infrx.contracts.v2.records import AccountingRegime
from infrx.media.prepare import MediaProfile
from infrx.media.video import Media
from infrx.observe import alerts
from infrx.observe.metrics import Registry
from infrx.worker import WorkerLoop, WorkerService
from infrx.worker import __main__ as worker_main
from infrx.worker.preparation import (ENCODER_CACHE_TOKENS, PreparationResult, PreparationRunner,
                                      most_video_tokens)
from infrx.worker.service import PgReconciliation
from tests.w.test_loop import ScriptEngine, World, candidate, delta, queued, usage_event
from tests.m.support import mp4
from tests.w.test_prep_worker import COUNT, Prep, counted as tokenized, outcome, tokenizer, within


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- the D1 port
def lifecycle(prep: Prep, *, gated: bool = True) -> FakeLifecycle:
    """F2C's reference `ReadinessStore` over `prep`'s store, wired as the runner's
    `readiness=` (D10's adapter in the product). `gated=False` is the transition door
    D10's 0019 keeps behind its feature flag: a claim that does not check the marker - the
    worker's own check is then the only barrier."""
    store = FakeLifecycle(prep.store, prep.clock, prep.harness.ids)
    prep.runner.readiness = store if gated else Ungated(store)
    return store


class Ungated:
    """`readiness` from the marker table, `claim_preparation` through the JobStore's
    previous-runtime door (no marker check)."""

    def __init__(self, store: FakeLifecycle) -> None:
        self.store = store

    async def readiness(self, job_id: str):
        return await self.store.readiness(job_id)

    async def claim_preparation(self, job_id: str, worker_id: str):
        return await self.store.jobs.claim_preparation(job_id, worker_id)


async def admit_ready(prep: Prep, store: FakeLifecycle):
    """G7's one-phase admission (D1): the job, its EMPTY manifest and the marker at once."""
    if prep.credit:
        request = b.request(prep.harness, org_id=v2fix.IDS.consumer_org,
                            key_id=v2fix.IDS.consumer_key, model_revision=v2fix.REQUESTED_MODEL)
        expectation = AdmissionExpectation(accounting_regime=AccountingRegime.credit,
                                           rate_card_version=v2fix.RATE_CARD_VERSION)
    else:
        prep.harness.extra["grant"](b.ORG_A, "25.00")
        request = b.request(prep.harness, org_id=b.ORG_A)
        expectation = AdmissionExpectation(accounting_regime=AccountingRegime.legacy_usd)
    await store.admit_ready(request, b.idem(request, request.request_id), expectation)
    return request


def job(prep: Prep, request):
    return prep.store.jobs[request.request_id]


def released_once(prep: Prep, request) -> bool:
    """The hold was released - not settled, not still held - and exactly one terminal
    projection was written for the job."""
    return (prep.store.holds[request.request_id].state is HoldState.released
            and prep.store.outbox_kinds(request.request_id).count(
                OutboxKind.usage_projection) == 1)


# --------------------------------------------------------------------------- 1. readiness
@pytest.mark.parametrize("credit", [False, True], ids=["legacy", "credit"])
def test_w5_ready__a_text_job_is_never_prepared_before_its_durable_manifest(tmp_path, credit):
    """RV-05's canary: a text-only job whose admission committed but whose manifest has not
    landed (the gateway's late card/capability rechecks still running) is NOT prepared: the
    engine is never asked, no count is stored, the job stays `preparing` - exactly as a
    video job waits. The attempt ends typed (`not_claimable`, F2C's `not_ready`) at the
    bounded wait, never hangs. Removing the readiness barrier prepares it at once."""
    prep = Prep(tmp_path, credit=credit, attach_wait_s=0.2)

    async def case():
        request = await prep.admit(ready=False)
        result = await within(prep.runner.run(request.request_id), 5.0)
        return request, result

    request, result = run(case())
    assert isinstance(result, PreparationResult), result
    assert (result.cause, result.refusal) == (None, "not_claimable"), result
    assert prep.app.tokenized == [], prep.app.tokenized
    assert job(prep, request).state is JobState.preparing
    assert job(prep, request).prompt_tokens is None


@pytest.mark.parametrize("postcheck", ["passes", "refuses"])
def test_w5_ready__a_late_postcheck_decides_before_anything_is_prepared(tmp_path, postcheck):
    """RV-05 (delayed and rejected postchecks): the worker's attempt starts right after the
    admission commits. When the gateway's recheck then passes, it records the EMPTY manifest
    (an attach of no media) and the worker prepares the job - an empty manifest is completed
    work, not missing work. When it refuses, the gateway cancels the job: nothing was ever
    counted, the hold is released once and the worker's attempt ends typed."""
    prep = Prep(tmp_path, attach_wait_s=1.0)

    async def case():
        request = await prep.admit(ready=False)
        attempt = asyncio.create_task(prep.runner.run(request.request_id))
        await asyncio.sleep(0.2)                  # the recheck, after the admission commit
        if postcheck == "passes":
            prep.attached[request.request_id] = ()
        else:
            await prep.store.cancel(request.org_id, job(prep, request).admission.job_handle)
        return request, await within(attempt, 5.0)

    request, result = run(case())
    if postcheck == "passes":
        assert (result.cause, result.prompt_tokens) == ("prepared", COUNT), result
        assert len(prep.app.tokenized) == 1 and job(prep, request).state is JobState.queued
    else:
        assert result.cause is None and result.refusal is not None, result
        assert prep.app.tokenized == [], prep.app.tokenized
        assert job(prep, request).state is JobState.cancelled
        assert released_once(prep, request)


@pytest.mark.parametrize("credit", [False, True], ids=["legacy", "credit"])
def test_w5_ready__the_d1_marker_is_what_the_worker_reads(tmp_path, credit):
    """With F2C's `ReadinessStore` wired the committed marker is the barrier, the work read
    through the same doors (`CreditWork` in the CREDIT regime): a text job `admit_ready`
    admitted with its EMPTY manifest is prepared at once - no attach record exists for it
    (a pre-D10 store cannot write one) and no wait is spent. A job the previous runtime
    admitted has no marker: its claim is refused `not_ready`, nothing is prepared or claimed
    (`preparation_attempts` stays 0)."""
    prep = Prep(tmp_path, credit=credit)                  # the product wait (10 s)
    store = lifecycle(prep)

    async def case():
        request = await admit_ready(prep, store)
        began = time.monotonic()
        result = await prep.runner.run(request.request_id)
        took = time.monotonic() - began
        legacy = await prep.admit()                        # attach record, no marker
        return request, result, took, legacy, await prep.runner.run(legacy.request_id)

    request, result, took, legacy, refused = run(case())
    assert (result.cause, result.prompt_tokens) == ("prepared", COUNT) and took < 2.0, \
        (result, took)
    assert job(prep, request).state is JobState.queued
    assert refused.refusal == "not_claimable", refused
    assert job(prep, legacy).preparation_attempts == 0 and len(prep.app.tokenized) == 1


@pytest.mark.parametrize("video", [False, True], ids=["text", "video"])
def test_w5_ready__no_marker_is_never_legacy_ready(tmp_path, video):
    """The cutover rule (no backfill): with the `ReadinessStore` wired, a job the previous
    runtime admitted is NOT READY even when the claim door does not check the marker (D10's
    transition door) and the previous gateway's durable attach record exists for it - the
    worker never falls back to that record. Nothing is prepared or counted; the attempt ends
    typed at the bounded wait."""
    prep = Prep(tmp_path, attach_wait_s=0.2)
    lifecycle(prep, gated=False)

    async def case():
        request = await prep.admit(video=video)             # its attach record, no marker
        return request, await within(prep.runner.run(request.request_id), 5.0)

    request, result = run(case())
    assert (result.cause, result.refusal) == (None, "not_claimable"), result
    assert prep.app.tokenized == [] and job(prep, request).prompt_tokens is None
    assert prep.attached[request.request_id] == request.media   # the record was there


def test_w5_ready__an_unready_job_ends_at_its_preparation_deadline_released_once(tmp_path):
    """ADMISSION-READY's bound: a job that never became ready - a crash between the admission
    and its manifest on a two-phase store, or the previous runtime's job under D1 - is not
    an orphan. The reaper settles it at `preparation_deadline_at` (`preparation_failed`),
    its hold released exactly once, and a second sweep changes nothing."""
    prep = Prep(tmp_path)
    lifecycle(prep)

    async def case():
        request = await prep.admit()                       # no marker: never claimable
        assert (await prep.runner.run(request.request_id)).refusal == "not_claimable"
        prep.clock.advance(job(prep, request).budgets.preparation_s + 1)
        first = await prep.store.recover()
        second = await prep.store.recover()
        return request, first, second

    request, first, second = run(case())
    outcome = job(prep, request).outcome
    assert outcome is not None, job(prep, request).state
    assert (outcome.state, outcome.cause, outcome.settlement_state) == (
        JobState.failed, TerminalCause.preparation_failed, SettlementState.released_free)
    assert [o.job_id for o in first] == [request.request_id] and second == ()
    assert released_once(prep, request) and prep.app.tokenized == []


# --------------------------------------------------------------------------- 2. crash boundaries
def dispatches(store, request, kind: OutboxKind) -> list:
    """The durable dispatch rows of one job (the relay's input; the index is only a hint)."""
    return [event for event in store.outbox
            if event.aggregate_id == request.request_id and event.kind is kind]


def redelivered(store, row) -> IndexEvent:
    """What the relay/reconciler hands the index for a durable dispatch row - here under a
    fresh event id, as a reconciler repair (a replay of the same id is deduplicated)."""
    job_ = store.jobs[row.aggregate_id]
    return IndexEvent(event_id=str(uuid.uuid4()), job_id=job_.id, org_id=job_.request.org_id,
                      key_id=job_.request.key_id, kind=row.kind,
                      execution_mode=job_.request.execution_mode, available_at=row.available_at,
                      attempt=0)


def counted(prompt: int, completion: int) -> Usage:
    return Usage(prompt_tokens=prompt, completion_tokens=completion,
                 total_tokens=prompt + completion, certainty=UsageCertainty.authoritative)


async def lapse(prep: Prep) -> tuple:
    """The dead preparation worker's lease lapses; the next worker's reaper runs."""
    prep.clock.advance(DEFAULTS.preparation_lease_ttl_s + 1)
    return await prep.store.recover()


def test_w5_crash__accepted_but_never_ready_is_bounded_and_released_once(tmp_path):
    """Crash after admit (RV-05): the gateway died between the admission commit and its
    manifest, on a store that lets the claim through. Every attempt is refused before the
    engine is asked; each lapsed lease is requeued within MAX_PREPUBLICATION_RETRIES, and the
    job then ends `preparation_failed` with its hold released once - no orphan, no count."""
    prep = Prep(tmp_path, attach_wait_s=0.05)

    async def case():
        request = await prep.admit(ready=False)
        refusals = []
        while job(prep, request).outcome is None and len(refusals) < 10:
            refusals.append((await prep.runner.run(request.request_id)).refusal)
            await lapse(prep)
        return request, refusals

    request, refusals = run(case())
    bound = 1 + DEFAULTS.max_prepublication_retries
    assert refusals == ["not_claimable"] * bound, refusals
    assert job(prep, request).outcome.cause is TerminalCause.preparation_failed
    assert released_once(prep, request) and prep.app.tokenized == []


def test_w5_crash__a_lost_queue_wakeup_loses_no_job_and_a_redelivery_runs_it_once():
    """Ready before the queue wakeup: the job is `queued` with its durable inference dispatch
    row, but the index never received it (Valkey flushed). The pool finds nothing - Valkey is
    a wakeup, not the authority - and the row is still there; its redelivery, twice (a
    repair racing a replay), runs the job exactly once: one generation in the journal, one
    settlement, one debit at the admitted price."""
    async def case():
        world = World()
        request, admission = await queued(world)
        engine = ScriptEngine(events=(delta("Two people "), delta("unload boxes."),
                                      usage_event(counted(1200, 2))))
        loop = WorkerLoop(scheduler=world.scheduler, runner=world.runner(engine),
                          worker_id="worker-a", limits=world.limits)
        idle = await loop.run()
        rows = dispatches(world.jobs, request, OutboxKind.inference_dispatch)
        for row in rows * 2:
            await world.scheduler.enqueue(redelivered(world.jobs, row))
        results = await loop.run()
        return world, request, admission, engine, idle, rows, results

    world, request, admission, engine, idle, rows, results = run(case())
    assert idle == [] and len(rows) == 1, (idle, rows)
    assert sorted(str(r.cause or r.refusal) for r in results) == [
        "already_terminal", "completed"], results
    settled = world.outcome(request.request_id)
    assert settled.debit == admission.price_snapshot.debit(1200, 2) and engine.started == 1
    assert {chunk.generation for chunk in world.journal(request.request_id)} == {1}
    assert world.jobs.outbox_kinds(request.request_id).count(OutboxKind.usage_projection) == 1


@pytest.mark.parametrize("crash", ["before_the_object", "before_prepared_commits",
                                   "after_prepared_commits"])
def test_w5_crash__a_preparation_that_dies_mid_way_is_prepared_once(tmp_path, crash):
    """Preparing before the object/row commit: the worker process dies (an untyped failure
    propagates: crash-only) before M writes the prepared object, after the object but before
    `prepared` commits, or after it commits with the answer lost. The next worker process
    (its own count memo, R107) prepares the job once from the durable state: one prepared
    object at its content address, one stored count, one inference dispatch; a job whose
    `prepared` did commit is never prepared again (the redelivered candidate is a lost
    claim)."""
    prep = Prep(tmp_path)

    async def case():
        request = await prep.admit(video=True)
        real = prep.media.prepare
        if crash == "before_the_object":
            async def dies(job_id, profile):
                raise RuntimeError("the worker process died")
            prep.media.prepare = dies
        elif crash == "before_prepared_commits":
            prep.harness.failures.raise_("prepared", RuntimeError("the worker process died"))
        else:
            prep.harness.failures.crash_after_commit("prepared")
        died = await outcome(prep.runner.run(request.request_id))
        prep.media.prepare = real
        if crash == "after_prepared_commits":
            await prep.store.recover()             # queued already: nothing to requeue
        else:
            await lapse(prep)
        successor = PreparationRunner(jobs=prep.jobs, media=prep.media, engine=prep.engine,
                                      worker_id="prep-next", limits=prep.runner.limits)
        again = await successor.run(request.request_id)
        return request, died, again

    request, died, again = run(case())
    assert isinstance(died, type) and issubclass(died, Exception), died       # it died
    prepared = [key for key in prep.media.objects.objects if key.endswith("/prepared")]
    assert len(prepared) == 1, prep.media.objects.objects.keys()
    assert job(prep, request).state is JobState.queued
    assert job(prep, request).prompt_tokens == COUNT
    assert len(dispatches(prep.store, request, OutboxKind.inference_dispatch)) == 1
    retries = dispatches(prep.store, request, OutboxKind.prepare_dispatch)
    if crash == "after_prepared_commits":
        assert again.refusal == "not_claimable" and len(retries) == 1, (again, retries)
        assert len(prep.app.tokenized) == 1
    else:
        assert (again.cause, again.prompt_tokens) == ("prepared", COUNT), again
        assert len(retries) == 2, retries                  # the reaper's fresh dispatch (R93)
        assert len(prep.app.tokenized) == (1 if crash == "before_the_object" else 2)


class Dying(ScriptEngine):
    """An engine that streams its events, then goes silent while the worker still holds them
    in its unflushed batch - where a process death lands between engine and journal."""

    yielded: int = 0

    async def _events(self):
        self.started += 1
        for event in self.events:
            self.yielded += 1
            yield event
        await asyncio.Event().wait()


@pytest.mark.parametrize("published", [False, True], ids=["unpublished", "published"])
def test_w5_crash__engine_output_before_the_journal_commit_is_never_relayed_or_billed(published):
    """Engine submitted before the journal cursor commit: the worker process dies while the
    engine is generating. Unpublished (no chunk committed), the next generation runs it once:
    the journal and the relay hold only generation 2, and the one debit is generation 2's
    usage at the admitted price - no exactly-once engine promise, but once-only output and
    accounting. Published (the first append committed), it is never regenerated: the engine
    is not asked again, nothing is debited (`lost_after_publication`)."""
    async def case():
        world = World()
        request, admission = await queued(world)
        dying = Dying(events=(delta("lost "),))
        first = asyncio.create_task(world.runner(dying).run(request.request_id))
        while not dying.yielded:                         # the runner holds an unjournalled delta
            await asyncio.sleep(0)
        if published:
            lease = world.jobs.jobs[request.request_id].lease
            await world.stream.append(lease, (delta("published "),))
        first.cancel()                                  # the process dies mid-generation
        await asyncio.gather(first, return_exceptions=True)
        world.clock.advance(world.limits.lease_ttl_s + 1)
        for event in await world.jobs.recover():
            if isinstance(event, IndexEvent):
                await world.scheduler.enqueue(event)
        engine = ScriptEngine(events=(delta("Two people unload boxes."),
                                      usage_event(counted(1200, 5))))
        results = await WorkerLoop(scheduler=world.scheduler, runner=world.runner(engine),
                                   worker_id="worker-b", limits=world.limits).run()
        return world, request, admission, engine, results

    world, request, admission, engine, results = run(case())
    settled = world.outcome(request.request_id)
    if published:
        assert (settled.cause, settled.debit, engine.started, results) == (
            TerminalCause.lost_after_publication, 0, 0, []), (settled, results)
    else:
        assert [(r.generation, r.cause) for r in results] == [(2, TerminalCause.completed)]
        assert settled.debit == admission.price_snapshot.debit(1200, 5)
        assert {c.generation for c in world.journal(request.request_id)} == {2}
        assert {c.generation for c in world.relayed} == {2}
        assert world.visible(request.request_id) == "Two people unload boxes."
    assert world.jobs.outbox_kinds(request.request_id).count(OutboxKind.usage_projection) == 1


def test_w5_crash__a_cancel_during_preparation_or_before_the_claim_runs_nothing_more(tmp_path):
    """Cancellation races: a job cancelled while the engine counts its prompt is not queued
    (`prepared` is refused typed, the attempt answered, never a dead runner) and its hold is
    released once; a job cancelled after it was queued is never run (the claim is refused,
    the engine never asked) and settles once."""
    prep = Prep(tmp_path)
    prep.app.control({"tokenize_delay_s": 0.3})

    async def preparing():
        request = await prep.admit()
        attempt = asyncio.create_task(prep.runner.run(request.request_id))
        while not prep.app.tokenized:
            await asyncio.sleep(0.01)
        await prep.store.cancel(request.org_id, job(prep, request).admission.job_handle)
        return request, await outcome(within(attempt, 5.0))

    async def queued_then_cancelled():
        world = World()
        request, admission = await queued(world)
        await world.jobs.cancel(request.org_id, admission.job_handle)
        await world.scheduler.enqueue(candidate(world, request))
        engine = ScriptEngine(events=(delta("never"),))
        results = await WorkerLoop(scheduler=world.scheduler, runner=world.runner(engine),
                                   worker_id="worker-a", limits=world.limits).run()
        return world, request, engine, results

    request, result = run(preparing())
    assert isinstance(result, PreparationResult) and result.refusal == "already_terminal", \
        result
    assert job(prep, request).state is JobState.cancelled and released_once(prep, request)
    assert dispatches(prep.store, request, OutboxKind.inference_dispatch) == []
    world, other, engine, results = run(queued_then_cancelled())
    assert [r.refusal for r in results] == ["already_terminal"] and engine.started == 0
    assert world.jobs.outbox_kinds(other.request_id).count(OutboxKind.usage_projection) == 1


# --------------------------------------------------------------------------- S3 F4: reconciliation
class Reads:
    """A reconciliation reader answering `(drift rows, unknown holds)` in turn, or raising."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)

    async def __call__(self) -> tuple[int, int]:
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def gauges(registry: Registry) -> dict[str, float]:
    """The reconciliation series of one scrape, by name (the process label dropped)."""
    names = ("infrx_reconciliation_drift", "infrx_holds_unknown", "infrx_unsettleable_jobs",
             "infrx_reconciliation_last_success_timestamp_seconds")
    found = {name: value for (name, _), value in alerts.parse(registry.render()).items()
             if name in names}
    runs = {dict(labels)["result"]: value for (name, labels), value
            in alerts.parse(registry.render()).items()
            if name == "infrx_reconciliation_runs_total"}
    return {**found, **{f"runs_{result}": value for result, value in runs.items()}}


def test_w5_reconcile__each_reaper_tick_publishes_the_reconciliation_gauges():
    """S3 F4: the worker's reaper tick is the periodic reconciliation pass, so it publishes
    `infrx_reconciliation_drift`, `infrx_holds_unknown` and `infrx_unsettleable_jobs` (the
    reaper's own unsettleable set) on the worker's /metrics - the series the
    ReconciliationDrift/UnsettleableJobs alerts and the soak's `reconciled_at_end` read. A
    failed read is counted and leaves the last pass standing; the reaper lives on."""
    async def case():
        world, registry = World(), Registry("worker")
        engine = ScriptEngine()
        service = WorkerService(
            loop=WorkerLoop(scheduler=world.scheduler, runner=world.runner(engine),
                            worker_id="worker-w5", limits=world.limits),
            jobs=world.jobs, engine=engine, metrics=registry, reap_interval_s=3600,
            reconciliation=Reads((2, 1), RuntimeError("the database is gone"), (0, 0)))
        world.jobs.unsettleable["00000000-0000-4000-8000-00000000dead"] = "journal full"
        await service.reap_once()
        first = gauges(registry)
        await outcome(service.reap_once())
        failed, errors_after = gauges(registry), service.reap_errors
        await service.reap_once()
        return first, failed, errors_after, gauges(registry)

    first, failed, errors_after, healthy = run(case())
    assert first == {"infrx_reconciliation_drift": 2, "infrx_holds_unknown": 1,
                     "infrx_unsettleable_jobs": 1, "runs_drift": 1}, first
    assert failed == first and errors_after == 1, (failed, errors_after)
    assert (healthy.get("infrx_reconciliation_drift"), healthy.get("runs_ok")) == (0, 1), healthy
    assert healthy.get("infrx_reconciliation_last_success_timestamp_seconds", 0) > 0


def test_w5_reconcile_pg__the_detector_views_count_drift_and_unknown_holds():
    """On PostgreSQL (the D harness, task w5): `PgReconciliation` reads 0003/0006's detector
    views and the unknown-usage holds as the worker's `service_role` - zero drift on a
    consistent database, one drift row once a wallet summary disagrees with its ledger."""
    from tests.d import pgharness, pgstore
    reason = pgharness.unavailable()
    if reason:
        pytest.skip(f"W5: the D harness is unavailable: {reason}")
    from infrx.state.jobstore import connector
    name = pgstore.fresh_database()
    read = PgReconciliation(connector(pgharness.dsn(name)))
    clean = run(read())
    with pgharness.connect(name) as conn:
        conn.execute("set session_replication_role = replica")      # past the summary guard
        conn.execute("insert into infrx.wallets (org_id, ledger_total, reserved_total) "
                     "values (gen_random_uuid(), 1, 0)")
    drifted = run(read())
    assert clean == (0, 0) and drifted == (1, 0), (clean, drifted)


# --------------------------------------------------------------------------- 3. refusals
class FailPreparation:
    """R104's pending third candidate, a fenced `fail_preparation(lease, cause)`, drafted over
    F's fake store for W5 (F2C freezes it on the port, D10 implements it on PostgreSQL):
    fenced on the preparation lease exactly like `prepared`; the job ends `failed` with
    `cause` (`invalid_media` or `preparation_failed`), no usage, settled by R21 - released
    free - with its capacity and hold released in the same transaction. `calls` records it."""

    def __init__(self, jobs, store) -> None:
        self.jobs, self.store, self.calls = jobs, store, []

    def __getattr__(self, name):
        return getattr(self.jobs, name)

    async def fail_preparation(self, lease, cause: TerminalCause):
        self.calls.append((lease.job_id, cause))
        if cause not in (TerminalCause.invalid_media, TerminalCause.preparation_failed):
            raise errors.InvalidRequest(f"{cause} does not end a preparation")
        async with self.store._lock:
            job_ = self.store._fence_preparation(lease)
            return self.store._terminalize(job_, cause, None, None, JobState.failed)


def failing(prep: Prep) -> FailPreparation:
    prep.runner.jobs = FailPreparation(prep.runner.jobs, prep.store)
    return prep.runner.jobs


CAP = DEFAULTS.replace(max_video_seconds=82.0)          # the deployed cap (P-20 decision B)


@pytest.mark.parametrize("refusal", ["over_the_cap", "over_the_context"])
def test_w5_refuse__a_permanent_refusal_ends_the_job_once(tmp_path, refusal):
    """A refusal the same input can never outgrow - a clip over the deployed 82 s cap (an
    acceptance from before the cap moved), a prompt past the job's `max_input_tokens` - ends
    the job at the FIRST attempt through the fenced `fail_preparation`: `failed` with an
    actionable cause (`invalid_media` / `preparation_failed`), released free, its hold
    released once. No lease is left to lapse, the reaper redispatches nothing, and no second
    attempt ever reaches the engine."""
    prep = Prep(tmp_path)
    ends = failing(prep)

    async def case():
        if refusal == "over_the_cap":
            request = await prep.admit(video=True, clip=mp4(seconds=100.0))   # admitted at 120
            prep.media.profile = MediaProfile.pinned(CAP)
        else:
            request = await prep.admit()
            prep.app.control({"tokenize_count": 40_000})      # past max_input_tokens 30,720
        result = await prep.runner.run(request.request_id)
        await lapse(prep)
        return request, result

    request, result = run(case())
    cause, code = {"over_the_cap": (TerminalCause.invalid_media, "unsupported_media"),
                   "over_the_context": (TerminalCause.preparation_failed,
                                        "context_length_exceeded")}[refusal]
    assert (result.refusal, result.ended) == (code, cause.value), result
    assert ends.calls == [(request.request_id, cause)]
    settled = job(prep, request).outcome
    assert settled is not None and (settled.state, settled.cause, settled.settlement_state) == (
        JobState.failed, cause, SettlementState.released_free), settled
    assert released_once(prep, request) and job(prep, request).preparation_attempts == 1
    assert len(dispatches(prep.store, request, OutboxKind.prepare_dispatch)) == 1
    assert "file://" not in result.detail and prep.root not in result.detail
    assert len(prep.app.tokenized) == (0 if refusal == "over_the_cap" else 1)


def test_w5_refuse__a_temporary_failure_is_retried_within_its_bound_never_ended_early(tmp_path):
    """The tokenizer down is temporary: nothing is ended by the worker, the lapsed lease is
    requeued within MAX_PREPUBLICATION_RETRIES and the store ends the job
    `preparation_failed` once the bound is spent - the hold released once, never cycled
    past the bound."""
    prep = Prep(tmp_path)
    ends = failing(prep)
    prep.app.control({"tokenize_fault": "down"})

    async def case():
        request = await prep.admit()
        refusals = []
        while job(prep, request).outcome is None and len(refusals) < 10:
            refusals.append((await prep.runner.run(request.request_id)).refusal)
            await lapse(prep)
        return request, refusals

    request, refusals = run(case())
    assert refusals == ["dependency_unavailable"] * (1 + DEFAULTS.max_prepublication_retries)
    assert ends.calls == [] and job(prep, request).outcome.cause is TerminalCause.preparation_failed
    assert released_once(prep, request)


def test_w5_refuse__a_clip_past_the_encoder_budget_is_refused_before_it_is_queued(tmp_path):
    """P-23: `/tokenize` does not apply the engine's encoder-cache check, so with a raised cap
    (112 s here) a clip whose video item needs more than the served budget (16,384 embedding
    tokens) would be counted, queued and then refused by the chat route. Preparation refuses
    it permanently instead - `invalid_media`, ended once, never queued."""
    limits = DEFAULTS.replace(max_video_seconds=112.0)
    prep = Prep(tmp_path, limits=limits)
    failing(prep)
    prep.app.control({"tokenize_count": 22_000})

    async def case():
        request = await prep.admit(video=True, clip=mp4(seconds=112.0, width=1920, height=1080))
        return request, await prep.runner.run(request.request_id)

    request, result = run(case())
    assert (result.refusal, result.ended) == ("unsupported_media", "invalid_media"), result
    assert dispatches(prep.store, request, OutboxKind.inference_dispatch) == []
    assert released_once(prep, request)


def decide():
    """W4's measured processor arithmetic (`models/marlin2b/measure/decide.py`)."""
    path = pathlib.Path(__file__).resolve().parents[4] / "models/marlin2b/measure/decide.py"
    spec = importlib.util.spec_from_file_location("w5_decide", path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_w5_refuse__the_video_bound_is_the_processors_worst_case_at_every_duration():
    """Not duration alone: the check's ceiling is the most video tokens the pinned processor
    gives ANY geometry at the budget (up to two frames fewer sampled, each allowed more
    pixels - W4's `decide.worst_tokens`, which reproduces the engine's measured refusals),
    at every duration up to the cap; and the deployed 82 s cap is exactly the longest
    duration whose worst case fits the served encoder budget."""
    measured, media = decide(), Media(SimpleNamespace(settings=Settings()))
    for seconds in [*range(1, 121), 12.5, 81.5, 82.25]:
        budget = media.budget_kwargs(seconds)
        assert most_video_tokens(budget["size"]["longest_edge"], budget["min_frames"]) == \
            measured.worst_tokens(seconds), seconds
    assert measured.ceiling_s(ENCODER_CACHE_TOKENS) == CAP.max_video_seconds == 82


def test_w5_refuse__the_maximum_geometry_at_the_cap_is_prepared(tmp_path):
    """The maximum supported input: an 82 s clip at 1080p whose engine count is the
    processor's worst case for its budget (16,154 video tokens - above the
    `longest_edge // 2048` = 16,072 a fully sampled clip gives, below the 16,384 encoder
    budget) is counted and queued, not refused as an engine off its profile."""
    worst = most_video_tokens(Media(SimpleNamespace(settings=Settings())).budget_kwargs(82.0)
                              ["size"]["longest_edge"], 4)
    prep = Prep(tmp_path, limits=CAP, transport=tokenizer(tokenized(worst + 40, worst)))
    failing(prep)

    async def case():
        request = await prep.admit(video=True, clip=mp4(seconds=82.0, width=1920, height=1080))
        return request, await prep.runner.run(request.request_id)

    request, result = run(case())
    assert worst == 16_154 and (result.cause, result.prompt_tokens) == ("prepared", worst + 40), \
        result
    assert job(prep, request).state is JobState.queued


def test_w5_refuse__a_permanent_refusal_racing_a_cancel_settles_once(tmp_path):
    """The client's cancel lands while the over-cap clip is being probed: the job is already
    `cancelled` when the refusal wants to end it, so `fail_preparation` is refused
    (`already_terminal`) - the attempt answers, nothing settles twice, the client's cause
    stands and the hold was released once."""
    prep = Prep(tmp_path)
    ends = failing(prep)

    async def case():
        request = await prep.admit(video=True, clip=mp4(seconds=100.0))
        prep.media.profile = MediaProfile.pinned(CAP)
        real = prep.media.prepare

        async def cancelled_meanwhile(job_id, profile):
            await prep.store.cancel(request.org_id, job(prep, request).admission.job_handle)
            return await real(job_id, profile)
        prep.media.prepare = cancelled_meanwhile
        return request, await outcome(prep.runner.run(request.request_id))

    request, result = run(case())
    assert isinstance(result, PreparationResult), result
    assert (result.refusal, result.ended) == ("unsupported_media", "already_terminal"), result
    assert len(ends.calls) == 1 and job(prep, request).outcome.cause is \
        TerminalCause.client_cancelled
    assert released_once(prep, request)


class Doors:
    """The lifecycle port as a composed preparation runner sees it: `claim_preparation` is the
    door the runner claims through and `readiness` the one it reads the manifest from; every
    other operation is the adapter's own."""

    def __init__(self, runner: PreparationRunner, port) -> None:
        self.runner, self.port = runner, port
        self.claim_preparation = runner.claims.claim_preparation
        self.readiness = runner.readiness.readiness

    def __getattr__(self, name):
        return getattr(self.port, name)


def test_w5_ready__the_acceptance_transcripts_replay_through_the_runners_doors():
    """F2C.d's acceptance oracle: every committed lifecycle transcript - the ADMISSION-READY
    cases in both regimes among them (an EMPTY manifest ready and claimable, no marker never
    claimable, a replay answering the recorded marker) - replays unchanged through the doors
    a preparation runner composed with the reference adapter claims and reads through."""
    def factory(**windows) -> Harness:
        harness = lifecycle_factory(**windows)
        runner = PreparationRunner(jobs=harness.extra["jobs"], media=None, engine=None,
                                   worker_id="prep-w", readiness=harness.port)
        return Harness(port=Doors(runner, harness.port), clock=harness.clock,
                       ids=harness.ids, failures=harness.failures, extra=harness.extra)

    assert acceptance.replay(factory) == []


def test_w5_ready_pg__the_worker_prepares_only_what_admit_ready_marked(tmp_path):
    """Integrated (D10's `PgLifecycle` on this lane's PostgreSQL; a visible skip where the
    adapter is not on the tree): the worker, claiming and reading through D10's adapter,
    prepares the text job `admit_ready` marked with its EMPTY manifest - the count stored in
    `infrx.jobs` - and refuses the job the previous runtime admitted (no marker) at the claim,
    in both regimes, without asking the engine."""
    lifecycle_module = pytest.importorskip(
        "infrx.state.lifecycle", reason="D10's PgLifecycle is not on this tree")
    from tests.d import pgharness, pgstore
    reason = pgharness.unavailable()
    if reason:
        pytest.skip(f"W5: the D harness is unavailable: {reason}")
    from infrx.state import migrations, pgtesting
    from infrx.state.jobstore import connector
    harness = pgtesting.make_credit_jobstore_factory(
        pgstore.fresh_database, pgharness.dsn, migrations.SEED_MARLIN.read_text())()
    conn = harness.extra["conn"]
    store = lifecycle_module.PgLifecycle(connector(pgharness.dsn(harness.extra["database"])))
    prep = Prep(tmp_path, credit=True)
    prep.runner.jobs, prep.runner.readiness = worker_main.CreditWork(harness.port), store
    card = AdmissionExpectation(accounting_regime=AccountingRegime.credit,
                                rate_card_version=v2fix.RATE_CARD_VERSION)

    def request():
        return b.request(harness, org_id=v2fix.IDS.consumer_org, key_id=v2fix.IDS.consumer_key,
                         model_revision=v2fix.REQUESTED_MODEL)

    async def case():
        marked, previous = request(), request()
        await store.admit_ready(marked, b.idem(marked, "w5-marked"), card)
        await harness.port.admit_credit(previous, b.idem(previous, "w5-previous"))
        return (marked, await prep.runner.run(marked.request_id),
                previous, await prep.runner.run(previous.request_id))

    marked, prepared, previous, refused = run(case())
    rows = {row[0]: row[1:] for row in conn.execute(
        "select request_id::text, prepared_prompt_tokens, preparation_attempts from infrx.jobs "
        "where request_id in (%s, %s)", (marked.request_id, previous.request_id)).fetchall()}
    assert (prepared.cause, prepared.prompt_tokens) == ("prepared", COUNT), prepared
    assert refused.refusal == "not_claimable" and len(prep.app.tokenized) == 1, refused
    # the previous runtime's job was never even claimed: the claim went through the marker
    assert rows == {marked.request_id: (COUNT, 1), previous.request_id: (None, 0)}, rows
