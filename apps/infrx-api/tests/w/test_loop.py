#!/usr/bin/env python3
"""W2 / DUR-FENCE + DUR-OUTPUT + DUR-SETTLE + OPS-RECOVER: the attempt loop.

    uv run --frozen pytest -q tests/w/test_loop.py
    uv run --frozen pytest -q tests/w -k dur_fence

Every case drives `infrx.worker.AttemptRunner` / `WorkerLoop` against the **shared**
fake JobStore, StreamStore and Scheduler (`infrx.contracts.fakes`) - the D2-D5 contract
until the real store exists - on one injected clock. Three engines are used, all
deterministic and none of them a process: W1's real `VllmEngine` over
`httpx.MockTransport`, the shared `FakeEngine` (R58 `{visible, raw}` deltas), and a
scripted `ScriptEngine` for the event shapes neither of the other two can produce.

Nothing here sleeps on the wall clock except the two cases that prove the task-level
deadline, and those bound it at 50 ms by shortening the store's own generation budget.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal

import pytest

from infrx.contracts import errors
from infrx.contracts.conformance import OPTIONAL_HOOKS, SUITES, builders as b, run_cases
from infrx.contracts.fakes.factories import FACTORIES
from infrx.contracts.fakes.engine import EngineFault, FakeEngine
from infrx.contracts.fakes.scheduling import FakeScheduler
from infrx.contracts.fakes.state import FakeJobStore, FakeStreamStore
from infrx.contracts.fakes.support import FailurePlan, FakeClock, SequentialIds
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import (ChunkEventType, EngineEvent, ExecutionMode, HoldState,
                                     IndexEvent, JobState, LeaseKind, OutboxKind,
                                     SettlementState, TerminalCause, Usage)
from infrx.worker import AttemptRunner, WorkerLoop, prepared_request
from infrx.worker.attempt import BATCH_MAX_EVENTS
from infrx.worker.engine import (MODEL_EOS_TOKEN_IDS, _inside_tenant_root,
                                 local_media_url)
from infrx.worker.fakes import (FAKE_MEDIA_ROOT, FakeUpstream, engine_factory as engine_harness,
                               m2_local_uri)
from infrx.worker.reasoning import filter_text
from tests.w.test_engine import Box as _Box
from tests.w.test_engine import text_prepared as _text_prepared
from tests.w.test_engine import video_work as _video_work


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# the world one case runs in
# --------------------------------------------------------------------------
@dataclass
class World:
    """One store, one journal, one index, one clock - the shape D5/Q3 will replace."""

    limits: object = DEFAULTS
    failures: FailurePlan = field(default_factory=FailurePlan)

    def __post_init__(self) -> None:
        self.clock, self.ids = FakeClock(), SequentialIds()
        self.jobs = FakeJobStore(self.clock, self.ids, limits=self.limits,
                                 failures=self.failures, prices={b.MODEL: b.DEFAULT_PRICE})
        self.stream = FakeStreamStore(self.jobs, failures=self.failures)
        self.scheduler = FakeScheduler(self.clock, limits=self.limits)
        self.relayed: list = []
        self.results: dict[str, str] = {}
        self.prompt_tokens = 1200

    # the two injected collaborators M2 and M3 own in production
    async def _relay(self, chunks) -> None:
        for chunk in chunks:
            stored = self.stream.chunks.get(chunk.job_id, [])
            assert any(held.generation == chunk.generation and held.sequence == chunk.sequence
                       for held in stored), "a chunk reached the relay before the journal"
        self.relayed.extend(chunks)

    async def _put_result(self, job_id: str, text: str, lease) -> str:
        # R147 (D10 0026): fenced like `append`, by the fake store's own fence.
        async with self.jobs._lock:
            self.jobs._fence(lease)
        self.results[job_id] = text
        return f"infrx-result:{job_id}"

    def runner(self, engine, **kw) -> AttemptRunner:
        fields = dict(jobs=self.jobs, stream=self.stream, engine=engine, clock=self.clock,
                      worker_id="worker-a", count_prompt_tokens=lambda work: self.prompt_tokens,
                      put_result=self._put_result, relay=self._relay, limits=self.limits)
        fields.update(kw)
        return AttemptRunner(**fields)

    def outcome(self, job_id: str):
        return self.jobs.jobs[job_id].outcome

    def journal(self, job_id: str, *, generation: int | None = None) -> list:
        return [chunk for chunk in self.stream.chunks.get(job_id, [])
                if generation is None or chunk.generation == generation]

    def visible(self, job_id: str) -> str:
        return "".join(chunk.payload.get("visible", "") for chunk in self.journal(job_id)
                       if chunk.event_type is ChunkEventType.delta)

    def balance(self, org_id: str = b.ORG_A):
        wallet = self.jobs.wallet(org_id)
        return {"ledger": wallet.ledger_total, "reserved": wallet.reserved_total}


async def queued(world: World, *, org_id: str = b.ORG_A, mode=ExecutionMode.stream,
                 grant: str = "25.00", **kw):
    """Admit, prepare and queue one job: the state a worker claims from."""
    world.jobs.grant(org_id, grant)
    request = b.request(world, org_id=org_id, mode=mode, max_output_tokens=256, **kw)
    admission = await world.jobs.admit(request, b.idem(request, f"idem-{request.request_id}"), ())
    lease = await world.jobs.claim_preparation(request.request_id, "prep-a")
    await world.jobs.prepared(lease, ())
    return request, admission


def candidate(world: World, request, *, attempt: int = 0) -> IndexEvent:
    return IndexEvent(event_id=world.ids.event_id(), job_id=request.request_id,
                      org_id=request.org_id, key_id=request.key_id,
                      kind=OutboxKind.inference_dispatch, execution_mode=request.execution_mode,
                      available_at=world.clock.now(), attempt=attempt)


def adapter(world: World, fault: str = "none", *, upstream_kw: dict | None = None, **kw):
    """W1's real adapter over a scripted upstream, on the world's own clock."""
    upstream = FakeUpstream(fault=fault, clock=world.clock, limits=world.limits,
                            **(upstream_kw or {}))
    return upstream, upstream.engine(**kw)


@dataclass
class ScriptEngine:
    """A port-only engine: canonical events and nothing else.

    No `terminal_cause`, no `finish_reason`, no `malformed_lines` - so a case using this
    proves the loop's *derived* outcome, the path any adapter that is not W1's takes.
    """

    events: tuple = ()
    clock: FakeClock | None = None
    advance_s: float = 0.0
    hang: bool = False
    cancel_answer: bool = True
    cancelled: list = field(default_factory=list)
    started: int = 0
    closed: int = 0

    def generate(self, lease, prepared):
        return self._events()

    async def _events(self):
        self.started += 1
        try:
            if self.hang:
                await asyncio.Event().wait()            # a silent engine, for ever
            for event in self.events:
                if self.advance_s and self.clock is not None:
                    self.clock.advance(self.advance_s)
                yield event
        finally:
            self.closed += 1

    async def cancel(self, lease) -> bool:
        self.cancelled.append((lease.job_id, lease.generation))
        return self.cancel_answer

    async def health(self) -> dict:
        return {"ready": True}

    async def drain(self) -> None:
        return None


def delta(visible: str, raw: str | None = None) -> EngineEvent:
    return EngineEvent(type=ChunkEventType.delta,
                       payload={"visible": visible, "raw": raw if raw is not None else visible})


def usage_event(usage: Usage | None = None, reason: str | None = None) -> EngineEvent:
    payload = {} if reason is None else {"reason": reason, "certainty": "unknown"}
    return EngineEvent(type=ChunkEventType.usage, payload=payload, usage=usage)


PROGRESS = EngineEvent(type=ChunkEventType.progress, payload={"phase": "running"})


# --------------------------------------------------------------------------
# F-CONTRACT: the suites the loop is written against
# --------------------------------------------------------------------------
def test_f_contract__the_loops_collaborators_pass_their_exported_suites():
    """The loop is coded to three ports, so W2's own evidence runs their exported suites
    rather than citing another track's report: `engine` against W1's real adapter, and
    `jobstore`/`streamstore` against the shared fakes that stand in for D2-D5 until the
    real store exists. A skipped case is reported, never counted (r1 R32)."""
    skipped: list = []
    ran = {}
    for port, factory in (("engine", engine_harness),
                          ("jobstore", FACTORIES["jobstore"]),
                          ("streamstore", FACTORIES["streamstore"])):
        cases, runner = SUITES[port]
        ran[port] = run_cases(cases(), factory, skipped=skipped)
    print("worker-level conformance: "
          + ", ".join(f"{port} {count} ran" for port, count in ran.items())
          + f", {len(skipped)} skipped")
    assert skipped == [], [(miss.case, miss.hook) for miss in skipped]
    assert all(count > 0 for count in ran.values()), ran
    # the fakes publish every optional hook their suites may need, so no case silently
    # asserts less than the full run
    for port in ("jobstore", "streamstore"):
        harness = FACTORIES[port]()
        missing = OPTIONAL_HOOKS[port] - set(harness.extra) - {"failures"}
        assert not missing, (port, sorted(missing))


# --------------------------------------------------------------------------
# DUR-OUTPUT: persist before relay
# --------------------------------------------------------------------------
def test_dur_output__the_answer_is_journalled_visible_only_then_relayed_and_settled():
    """The whole happy path against W1's real adapter: the customer's text reaches the
    journal with the reasoning block removed, every relayed chunk was committed first,
    and the job settles `succeeded` with an authoritative debit and a result reference."""
    async def case():
        world = World()
        request, _ = await queued(world)
        upstream, engine = adapter(world, "split_reasoning_delimiters")
        result = await world.runner(engine).run(request.request_id)

        assert result.settled and result.cause is TerminalCause.completed
        # the World's relay raises on a pre-commit object and the runner swallows relay
        # errors by design, so a clean detail is part of the persist-before-relay proof
        assert "relay failed" not in result.detail, result.detail
        outcome = result.outcome
        assert outcome.state is JobState.succeeded
        assert outcome.result_ref == f"infrx-result:{request.request_id}"
        assert outcome.usage is not None and outcome.usage.prompt_tokens == 1200
        assert outcome.settlement_state is SettlementState.settled and outcome.debit > 0
        # the engine's own count reaches the store unchanged: one token per upstream delta
        assert outcome.usage.completion_tokens == len(upstream.deltas())

        whole = "".join(upstream.deltas())
        assert world.results[request.request_id] == filter_text(whole) == "Two people unload boxes."
        assert world.visible(request.request_id) == filter_text(whole)
        # r1 R58: the journal carries `visible` and nothing else. `raw` is trace
        # capture's and `content` is its transitional alias; either in a chunk is the
        # reasoning block on its way to a customer.
        for chunk in world.journal(request.request_id):
            if chunk.event_type is ChunkEventType.delta:
                assert set(chunk.payload) == {"visible"}, chunk.payload
                # a delta with nothing for the customer (all of it was reasoning) is not
                # journalled at all: it would spend journal bytes on nothing
                assert chunk.payload["visible"], chunk
            assert "the van is" not in str(chunk.payload)
        # every relayed chunk was committed first, and only committed ones were relayed
        assert result.relayed == result.committed > 0
        assert [chunk.sequence for chunk in world.relayed] == \
               [chunk.sequence for chunk in world.journal(request.request_id)
                if chunk.event_type is not ChunkEventType.terminal]
    run(case())


def test_dur_output__a_failed_journal_write_relays_nothing_and_settles_the_write_failure():
    """`02`: on a write failure stop execution and relay, then fail safely. Nothing the
    journal refused may reach a customer, and the attempt is not a success."""
    async def case():
        world = World(failures=FailurePlan().fail(
            "append", error=errors.JournalWriteFailed("disk")))
        request, _ = await queued(world)
        _, engine = adapter(world)
        result = await world.runner(engine).run(request.request_id)

        assert result.proposed_cause is TerminalCause.journal_write_failed
        assert result.committed == 0 and result.relayed == 0 and world.relayed == []
        assert world.visible(request.request_id) == ""
        outcome = result.outcome
        assert outcome.state is JobState.failed
        assert outcome.cause is TerminalCause.journal_write_failed
        # the engine really did the work, so the count is recorded as the internal cost -
        # and r1 R21 still charges nobody, because the failure is ours
        assert outcome.usage is not None and outcome.usage.completion_tokens > 0
        assert outcome.debit == 0
        assert outcome.settlement_state is SettlementState.released_platform_absorbed
        assert world.balance()["reserved"] == 0
    run(case())


def test_dur_output__an_unconfirmed_journal_write_is_never_relayed_though_the_journal_took_it():
    """The ambiguous write: the journal committed the batch and the answer was lost. The
    chunks are durable, so the job is published and can never be regenerated - but this
    worker never saw them, so it relays nothing and settles a failure rather than a
    success it cannot prove."""
    async def case():
        world = World(failures=FailurePlan().crash_after_commit("append"))
        request, _ = await queued(world)
        _, engine = adapter(world)
        result = await world.runner(engine).run(request.request_id)

        assert result.proposed_cause is TerminalCause.journal_write_failed
        assert result.relayed == 0 and world.relayed == []
        assert world.visible(request.request_id) != ""          # committed all the same
        assert world.jobs.jobs[request.request_id].published is True
        outcome = result.outcome
        assert outcome.cause is TerminalCause.journal_write_failed
        assert outcome.state is JobState.failed and outcome.debit == 0
        assert outcome.settlement_state is SettlementState.released_platform_absorbed
        # the customer keeps nothing they were not shown, and pays nothing
        assert request.request_id not in world.results
        assert world.balance()["reserved"] == 0
    run(case())


def test_dur_settle__published_output_with_an_unknown_count_waits_for_reconciliation():
    """`02`, unknown outcomes: published output and no authoritative usage holds the hold
    for the fenced 24 h and releases it as platform-absorbed, never as free and never as
    a late customer debit."""
    async def case():
        world = World()
        request, _ = await queued(world)
        _, engine = adapter(world, "missing_usage")
        outcome = (await world.runner(engine).run(request.request_id)).outcome
        assert outcome.usage is None and outcome.debit == 0
        assert outcome.settlement_state is SettlementState.held_unknown
        assert outcome.reconcile_after is not None
        assert world.jobs.holds[request.request_id].state is HoldState.unknown
        assert world.balance()["reserved"] > 0                 # still held, not released

        world.clock.advance(world.limits.unknown_usage_reconcile_s + 1)
        await world.jobs.recover()
        settled = world.outcome(request.request_id)
        assert settled.settlement_state is SettlementState.released_platform_absorbed
        assert settled.debit == 0 and world.balance()["reserved"] == 0
    run(case())


def test_dur_output__a_batch_is_bounded_by_the_clock_and_by_its_own_size():
    """`02` §6 bounds a batch at 50 ms; `BATCH_MAX_EVENTS` bounds it when a burst arrives
    inside one clock tick, so the memory held between commits is bounded either way."""
    async def case():
        # A frozen clock: only the size bound can fire.
        world = World()
        request, _ = await queued(world)
        pieces = tuple(f"t{n} " for n in range(BATCH_MAX_EVENTS + 3))
        engine = ScriptEngine(events=(PROGRESS, *(delta(piece) for piece in pieces),
                                      usage_event(Usage.of(1200, len(pieces)))))
        appended: list[int] = []
        original = world.stream.append

        async def counting(lease, events):
            appended.append(len(events))
            return await original(lease, events)

        world.stream.append = counting
        result = await world.runner(engine).run(request.request_id)
        assert result.cause is TerminalCause.completed
        assert max(appended) <= BATCH_MAX_EVENTS, appended
        assert len(appended) >= 2, appended
        assert world.visible(request.request_id) == "".join(pieces)

        # A moving clock: the time bound fires first, one append per event.
        timed = World()
        request2, _ = await queued(timed)
        slow = ScriptEngine(events=(PROGRESS, delta("a "), delta("b "), delta("c "),
                                    usage_event(Usage.of(1200, 3))),
                            clock=timed.clock, advance_s=timed.limits.stream_batch_ms / 1000)
        batches: list[int] = []
        plain = timed.stream.append

        async def timed_append(lease, events):
            batches.append(len(events))
            return await plain(lease, events)

        timed.stream.append = timed_append
        assert (await timed.runner(slow).run(request2.request_id)).cause is TerminalCause.completed
        # The bound is checked when the next event arrives, so a batch opened at t and
        # closed at t+50ms carries two events - never more, and never the whole stream.
        assert batches == [2, 2, 1], batches
    run(case())


def test_dur_output__a_delta_without_visible_text_is_refused_not_relayed():
    """R58 and the pending F2R delta in one case: a delta payload with only `content`
    (what the shared fake still emits) is a protocol breach. Relaying `content` would
    publish the reasoning block, and relaying nothing would deliver a truncated answer as
    a whole one, so the attempt fails and nothing is journalled."""
    async def case():
        world = World()
        request, _ = await queued(world)
        engine = ScriptEngine(events=(
            PROGRESS,
            EngineEvent(type=ChunkEventType.delta, payload={"content": "<think>secret</think>hi"}),
            usage_event(Usage.of(1200, 1))))
        result = await world.runner(engine).run(request.request_id)

        assert result.proposed_cause is TerminalCause.platform_error
        assert result.outcome.state is JobState.failed
        assert world.relayed == [] and world.visible(request.request_id) == ""
        assert result.outcome.debit == 0
        assert engine.closed == 1                       # the stream was closed, not abandoned
    run(case())


def test_dur_output__a_worker_never_journals_a_terminal_event():
    """r1 R30: the terminal journal event is derived from the stored outcome inside the
    settling transaction. A worker that could append one could fake a settlement."""
    async def case():
        world = World()
        request, _ = await queued(world)
        engine = ScriptEngine(events=(PROGRESS,
                                      EngineEvent(type=ChunkEventType.terminal,
                                                  payload={"state": "succeeded"}),
                                      usage_event(Usage.of(1200, 1))))
        result = await world.runner(engine).run(request.request_id)
        assert result.proposed_cause is TerminalCause.platform_error
        terminal = [chunk for chunk in world.journal(request.request_id)
                    if chunk.event_type is ChunkEventType.terminal]
        # exactly one, and the store wrote it
        assert len(terminal) == 1 and terminal[0].payload["cause"] == "platform_error"
    run(case())


# --------------------------------------------------------------------------
# DUR-FENCE
# --------------------------------------------------------------------------
def test_dur_fence__a_superseded_generation_appends_nothing_and_settles_nothing():
    """DUR-FENCE: the lease expires, `recover` requeues the attempt as a new generation,
    and the old worker - still holding its token - can neither append nor settle."""
    async def case():
        world = World()
        request, _ = await queued(world)
        stale = await world.jobs.claim(request.request_id, "worker-dead")
        world.clock.advance(world.limits.lease_ttl_s + 1)
        produced = await world.jobs.recover()
        assert any(isinstance(event, IndexEvent) for event in produced)

        _, engine = adapter(world)
        fresh = await world.runner(engine).run(request.request_id)
        assert fresh.settled and fresh.generation == stale.generation + 1

        # the superseded worker, arriving late with a perfectly well-formed lease
        with pytest.raises(errors.DomainError) as appended:
            await world.stream.append(stale, (delta("stale"),))
        assert appended.value.code in ("stale_lease", "already_terminal")
        late = await world.runner(engine).execute(stale)
        assert late.refusal in ("stale_lease", "already_terminal") and not late.settled
        assert world.outcome(request.request_id) is fresh.outcome or \
               world.outcome(request.request_id).cause is fresh.outcome.cause
        assert world.journal(request.request_id, generation=stale.generation) == []
        assert "stale" not in world.visible(request.request_id)
    run(case())


def test_dur_fence__a_stale_worker_cannot_append_after_its_lease_expired():
    """The clock past the lease expiry is enough: no second worker is needed for a
    fenced append to be refused, and the attempt settles nothing."""
    async def case():
        world = World()
        request, _ = await queued(world)
        engine = ScriptEngine(events=(PROGRESS, delta("one "), delta("two "),
                                      usage_event(Usage.of(1200, 2))),
                              clock=world.clock, advance_s=world.limits.lease_ttl_s + 1)
        # the heartbeat is what notices first, and it is refused just as hard
        result = await world.runner(engine).run(request.request_id)
        assert not result.settled and result.refusal == "stale_lease"
        assert world.outcome(request.request_id) is None      # still running, for `recover`
        assert world.relayed == []
        # r1 R58: a lost fence never cancels - the generation this token names is not the
        # one that is running, and stopping our own task is all we may do
        assert engine.cancelled == [] and result.engine_cancel is None
    run(case())


def test_dur_fence__two_workers_claiming_one_job_produce_one_attempt():
    """A duplicate dispatch is ordinary (the outbox is at-least-once). Only one claim
    wins, the loser settles nothing, and the journal holds one generation."""
    async def case():
        world = World()
        request, _ = await queued(world)
        _, engine = adapter(world)
        runner_a = world.runner(engine)
        runner_b = world.runner(engine, worker_id="worker-b")
        first, second = await asyncio.gather(runner_a.run(request.request_id),
                                             runner_b.run(request.request_id),
                                             return_exceptions=True)
        results = [first, second]
        settled = [result for result in results if result.settled]
        lost = [result for result in results if not result.settled]
        assert len(settled) == 1 and len(lost) == 1
        assert lost[0].refusal in ("not_claimable", "already_terminal", "stale_lease")
        assert {chunk.generation for chunk in world.journal(request.request_id)} == {1}
        assert world.jobs.outbox_kinds(request.request_id).count(
            OutboxKind.usage_projection) == 1
    run(case())


def test_dur_fence__a_preparation_lease_cannot_execute_an_attempt():
    """r1 R46: the two attempt sequences have separate counters and separate work. A
    preparation lease fences M's media work; executing under one would append output to
    a job that is still `preparing`."""
    async def case():
        world = World()
        world.jobs.grant(b.ORG_A, "25.00")
        request = b.request(world, max_output_tokens=256)
        await world.jobs.admit(request, b.idem(request, "idem-prep"), ())
        preparation = await world.jobs.claim_preparation(request.request_id, "prep-a")
        assert preparation.kind is LeaseKind.preparation
        engine = ScriptEngine()
        with pytest.raises(errors.InvalidRequest):
            await world.runner(engine).execute(preparation)
        assert engine.started == 0 and world.journal(request.request_id) == []
    run(case())


def test_dur_fence__a_lost_fence_never_cancels_another_generation():
    """r1 R58: cancellation intents are keyed by `(job_id, generation)`. A worker that
    lost its fence stops its own task; it must not call `Engine.cancel` at all, because
    the only lease it holds names a generation that is no longer running."""
    async def case():
        world = World()
        request, _ = await queued(world)
        stale = await world.jobs.claim(request.request_id, "worker-dead")
        world.clock.advance(world.limits.lease_ttl_s + 1)
        await world.jobs.recover()
        engine = ScriptEngine(events=(PROGRESS, delta("x"), usage_event(Usage.of(1200, 1))))

        # the job is queued again, so the old token fences nothing
        late = await world.runner(engine).execute(stale)
        assert late.refusal == "stale_lease" and late.engine_cancel is None
        # a lost fence is not a cancellation: this job belongs to another generation, and
        # nobody asked for it to stop
        assert late.cancelled is False and not late.settled
        assert engine.cancelled == []        # nothing was cancelled on anyone's behalf
        assert engine.started == 0

        fresh = await world.runner(engine).run(request.request_id)
        assert fresh.settled and fresh.generation == stale.generation + 1
        assert engine.cancelled == []
    run(case())


def test_dur_fence__a_result_write_the_fence_refuses_settles_nothing():
    """R147 (D10 0026): the result write is fenced like `append`. When the fence refuses it -
    the lease lapsed after the last append (`stale_lease`), or the job ended under it
    (`already_terminal`) - the attempt ends as a refused `complete` ends it: the refusal is
    recorded, nothing is stored or settled, `complete` is never called and nothing is
    proposed as a platform error. Oracle: a runner that reads the refusal as a failed
    result store (`platform_error`) and goes on to settle."""
    async def case(lose, refusal):
        world = World()
        request, admission = await queued(world)
        engine = ScriptEngine(events=(PROGRESS, delta("done "), usage_event(Usage.of(1200, 1))))
        settles, complete = [], world.jobs.complete

        async def counted(lease, outcome):
            settles.append(outcome.cause)
            return await complete(lease, outcome)
        world.jobs.complete = counted

        async def lose_then_store(job_id, text, lease):
            await lose(world, admission)
            return await world._put_result(job_id, text, lease)

        result = await world.runner(engine, put_result=lose_then_store).run(request.request_id)
        assert (result.refusal, result.settled) == (refusal, False), result
        assert settles == [], f"a refused result write went on to settle: {settles}"
        assert result.proposed_cause is TerminalCause.completed, result.proposed_cause
        assert "result store failed" not in result.detail, result.detail
        assert result.cancelled is (refusal == "already_terminal") and world.results == {}
        return world.outcome(request.request_id)

    async def lapse(world, admission):
        world.clock.advance(world.limits.lease_ttl_s + 1)

    async def cancel(world, admission):
        await world.jobs.cancel(b.ORG_A, admission.job_handle)

    assert run(case(lapse, "stale_lease")) is None            # still running, for `recover`
    assert run(case(cancel, "already_terminal")).cause is TerminalCause.client_cancelled


def test_dur_fence__the_heartbeat_renews_inside_the_lease_and_is_what_a_silent_stream_polls():
    """Renewal is event driven and reads the **stored** lease back (r1 R29), and it is
    the cancellation poll for a stream that is producing nothing."""
    async def case():
        world = World()
        request, _ = await queued(world)
        # 7 s per delta (under the 20 s stall budget) against a 40 s renewal interval and
        # a 120 s TTL: twenty deltas is 140 s, so the attempt **outlives its own lease**
        # and only the renewal keeps its appends fenced.
        upstream, engine = adapter(world, "slow_deltas",
                                   upstream_kw={"text": b.MODEL[:12] * 20, "chunk_size": 12})
        result = await world.runner(engine).run(request.request_id)
        assert result.cause is TerminalCause.completed, result.detail
        assert result.deltas == 20, "the case no longer outlives the lease TTL"
        assert result.heartbeats >= 2
        assert world.jobs.jobs[request.request_id].outcome is not None

        # the same renewal, now discovering a cancellation with no output in flight
        silent = World()
        request2, admission = await queued(silent)
        holder = {}

        async def cancel_at_the_heartbeat(work):
            return 1200

        engine2 = ScriptEngine(clock=silent.clock,
                               advance_s=silent.limits.lease_heartbeat_s + 1,
                               events=(PROGRESS, delta(""), delta(""),
                                       usage_event(Usage.of(1200, 0))))
        runner = silent.runner(engine2, count_prompt_tokens=cancel_at_the_heartbeat)
        await silent.jobs.cancel(b.ORG_A, admission.job_handle)
        result2 = await runner.run(request2.request_id)
        assert result2.refusal == "already_terminal" and not result2.settled
        assert silent.outcome(request2.request_id).cause is TerminalCause.client_cancelled
    run(case())


# --------------------------------------------------------------------------
# DUR-SETTLE: accounting
# --------------------------------------------------------------------------
def test_dur_settle__usage_reaches_the_store_only_when_the_stream_says_it_is_authoritative():
    """r1 R21/R58: an unknown count is `None` plus a reason, and the hold waits for
    reconciliation rather than being billed or silently released."""
    async def case():
        for fault, reason in (("missing_usage", None), ("malformed_usage", "malformed"),
                              ("conflicting_usage", "conflicting"),
                              ("usage_below_deltas", "below_delta_count"),
                              ("prompt_out_of_range", "out_of_range")):
            world = World()
            request, _ = await queued(world)
            _, engine = adapter(world, fault)
            result = await world.runner(engine).run(request.request_id)
            outcome = result.outcome
            assert outcome.usage is None, fault
            assert outcome.debit == 0, fault
            assert outcome.cause is TerminalCause.engine_incomplete, (fault, outcome.cause)
            # output was published, so this is reconciliation, not a free release
            assert outcome.settlement_state is SettlementState.held_unknown, fault
            if reason is not None:
                assert result.usage_unknown_reason == reason, fault

        # and the authoritative one does settle a debit
        good = World()
        request, _ = await queued(good)
        _, engine = adapter(good)
        settled = (await good.runner(engine).run(request.request_id)).outcome
        assert settled.usage is not None and settled.debit > 0
        assert settled.settlement_state is SettlementState.settled
    run(case())


def test_dur_settle__two_usage_events_make_the_count_unknown():
    """R58 allows one usage event. Two is the engine contradicting itself, and a guess
    between them would be a customer's debit."""
    async def case():
        world = World()
        request, _ = await queued(world)
        engine = ScriptEngine(events=(PROGRESS, delta("a "), usage_event(Usage.of(1200, 1)),
                                      usage_event(Usage.of(1200, 9))))
        result = await world.runner(engine).run(request.request_id)
        assert result.usage_unknown_reason == "multiple_usage_events"
        assert result.outcome.usage is None and result.outcome.debit == 0
        assert result.outcome.settlement_state is SettlementState.held_unknown

        # and a delta *after* the usage event: the count cannot have covered the whole
        # answer, so the stream did not finish as far as we know (R58)
        after = World()
        request2, _ = await queued(after)
        late = ScriptEngine(events=(PROGRESS, delta("a "), usage_event(Usage.of(1200, 1)),
                                    delta("b ")))
        result2 = await after.runner(late).run(request2.request_id)
        assert result2.proposed_cause is TerminalCause.engine_incomplete
        assert result2.outcome.state is JobState.failed and result2.outcome.debit == 0
        assert request2.request_id not in after.results
    run(case())


def test_dur_settle__only_completed_and_cancellation_are_billable():
    """r1 R21: the platform's own failures and deadlines are free, whatever the engine
    produced. Only a completed answer and a customer cancellation, each with an
    authoritative count, move the ledger."""
    async def case():
        billed = []
        for fault, cause in (("none", TerminalCause.completed),
                             ("abrupt_exit", TerminalCause.engine_error),
                             ("engine_error_post_headers", TerminalCause.engine_error),
                             ("prefill_stall", TerminalCause.engine_incomplete),
                             ("midstream_stall", TerminalCause.engine_incomplete),
                             ("truncated", TerminalCause.engine_incomplete),
                             ("over_ceiling", TerminalCause.platform_error)):
            world = World()
            request, _ = await queued(world)
            _, engine = adapter(world, fault)
            outcome = (await world.runner(engine).run(request.request_id)).outcome
            assert outcome.cause is cause, (fault, outcome.cause)
            billed.append((cause, outcome.debit > 0))
            if cause is not TerminalCause.completed:
                assert outcome.debit == 0, fault
                assert outcome.state is JobState.failed, fault
        assert billed[0] == (TerminalCause.completed, True)

        # `client_cancelled` is the other cause that *may* bill, and it is the worker that
        # settles it: a sync cancellation reaches the engine, the stream stops, and the
        # count the engine reports for work it never did is an authoritative zero.
        cancel = World()
        request, _ = await queued(cancel)
        _, engine = adapter(cancel, "cancellation_race")
        lease = await cancel.jobs.claim(request.request_id, "worker-a")
        assert await engine.cancel(lease) is True          # G's synchronous cancellation
        result = await cancel.runner(engine).execute(lease)
        assert result.settled and result.cause is TerminalCause.client_cancelled
        outcome = result.outcome
        assert outcome.state is JobState.cancelled
        assert outcome.usage == Usage.of(0, 0)
        # nothing ran, so the authoritative count is zero and zero is not a debit
        assert outcome.debit == 0
        assert outcome.settlement_state is SettlementState.released_free
        assert cancel.balance()["reserved"] == 0
    run(case())


def test_dur_settle__a_finished_stream_with_a_dropped_line_is_not_a_billable_success():
    """A `data:` line we could not place is content we may have dropped, so the answer is
    not whole - the loop re-checks the three facts `completed` needs rather than trusting
    the adapter's advisory cause."""
    async def case():
        world = World()
        request, _ = await queued(world)
        _, engine = adapter(world, "bom_first")
        result = await world.runner(engine).run(request.request_id)
        assert result.proposed_cause is TerminalCause.engine_incomplete
        assert result.outcome.debit == 0 and result.outcome.state is JobState.failed

        # the same stream without the unplaceable line is a success
        clean = World()
        request2, _ = await queued(clean)
        _, plain = adapter(clean)
        assert (await clean.runner(plain).run(request2.request_id)).cause \
               is TerminalCause.completed
    run(case())


def test_dur_settle__a_finish_reason_outside_the_set_is_not_completed():
    """`abort` is a finish, not a completion. Only `stop` and `length` are."""
    async def case():
        world = World()
        request, _ = await queued(world)
        _, engine = adapter(world, "abort_finish")
        result = await world.runner(engine).run(request.request_id)
        assert result.proposed_cause is TerminalCause.engine_incomplete
        assert result.outcome.debit == 0
    run(case())


def test_dur_settle__a_lost_terminal_acknowledgment_settles_exactly_once():
    """DUR-OUTPUT/DUR-SETTLE: the settlement committed and the answer was lost. The
    identical completion replays; a different one would be a second settlement."""
    async def case():
        world = World(failures=FailurePlan().crash_after_commit("complete"))
        request, _ = await queued(world)
        _, engine = adapter(world)
        result = await world.runner(engine).run(request.request_id)
        assert result.settled and result.cause is TerminalCause.completed
        assert "terminal ack lost" in result.detail
        assert world.jobs.outbox_kinds(request.request_id).count(
            OutboxKind.usage_projection) == 1
        job = world.jobs.jobs[request.request_id]
        assert job.outcome.debit == result.outcome.debit
        # the wallet moved once: reserved released, ledger debited once
        assert world.balance()["reserved"] == 0
        assert world.balance()["ledger"] == Decimal("25.00") - result.outcome.debit
    run(case())


def test_dur_settle__a_success_the_customer_cannot_fetch_is_not_a_success():
    """`02` §7 / r1 R30: the immutable result object is stored **before** the settling
    transaction. If it cannot be, the job did not succeed and nobody is charged."""
    async def case():
        world = World()
        request, _ = await queued(world)
        _, engine = adapter(world)

        async def broken(job_id, text, lease):
            raise RuntimeError("object storage is down")

        result = await world.runner(engine, put_result=broken).run(request.request_id)
        assert result.proposed_cause is TerminalCause.platform_error
        assert result.outcome.result_ref is None and result.outcome.debit == 0
        assert result.outcome.state is JobState.failed
        assert "result store failed" in result.detail
    run(case())


def test_dur_settle__a_usage_event_can_arrive_with_a_stall_or_a_cancellation():
    """W1 handback: the usage event is emitted at the end of the stream whatever ended
    it. The count is recorded as the internal cost; the **cause** decides billing, so a
    platform deadline with a perfectly good count still charges nobody."""
    async def case():
        world = World()
        request, _ = await queued(world)
        # progress, one delta, an authoritative count, and then the clock is past the
        # persisted generation instant: the count is known, the attempt is not a success
        engine = ScriptEngine(events=(PROGRESS, delta("half an answer "),
                                      usage_event(Usage.of(1200, 1))))
        lease = await world.jobs.claim(request.request_id, "worker-a")
        world.clock.advance(world.limits.generation_timeout_s + 1)
        result = await world.runner(engine).execute(lease)
        # the store terminalizes past its own instant (r1 R29), in our own fenced call
        assert result.refusal == "already_terminal"
        outcome = world.outcome(request.request_id)
        assert outcome.cause is TerminalCause.deadline_exceeded and outcome.debit == 0
    run(case())


# --------------------------------------------------------------------------
# cancellation and deadlines, phase by phase
# --------------------------------------------------------------------------
def test_dur_fence__a_cancellation_is_honoured_in_every_phase():
    """Cancel before the claim, before `load_work`, mid-stream and after the stream: the
    store's terminal state is the signal, no phase settles a second outcome, and the one
    that is mid-stream tells the engine to stop with the **current** lease."""
    async def case():
        # 1. before the claim
        world = World()
        request, admission = await queued(world)
        await world.jobs.cancel(b.ORG_A, admission.job_handle)
        engine = ScriptEngine(events=(PROGRESS, delta("x"), usage_event(Usage.of(1200, 1))))
        result = await world.runner(engine).run(request.request_id)
        assert result.refusal == "already_terminal" and engine.started == 0

        # 2. after the claim, before `load_work`
        world2 = World()
        request2, admission2 = await queued(world2)
        lease2 = await world2.jobs.claim(request2.request_id, "worker-a")
        await world2.jobs.cancel(b.ORG_A, admission2.job_handle)
        engine2 = ScriptEngine(events=(PROGRESS, delta("x"), usage_event(Usage.of(1200, 1))))
        result2 = await world2.runner(engine2).execute(lease2)
        assert result2.refusal == "already_terminal" and engine2.started == 0
        assert not result2.settled

        # 3. mid-stream: the next committed batch is what discovers it
        world3 = World()
        request3, admission3 = await queued(world3)
        lease3 = await world3.jobs.claim(request3.request_id, "worker-a")
        cancelling = _CancelAfterFirstDelta(world3, admission3)
        result3 = await world3.runner(cancelling).execute(lease3)
        assert result3.refusal == "already_terminal" and result3.cancelled
        # r1 R58: the intent is recorded for this job and this generation, nothing else
        assert cancelling.cancelled == [(request3.request_id, lease3.generation)]
        assert result3.engine_cancel is True
        assert cancelling.closed == 1
        # the stream stopped: what the engine sent after the cancellation was never
        # journalled, and nothing was settled on top of the store's own outcome
        assert world3.visible(request3.request_id) == "the beginning "
        assert not result3.settled
        outcome3 = world3.outcome(request3.request_id)
        assert outcome3.cause is TerminalCause.client_cancelled
        assert outcome3.state is JobState.cancelled

        # 4. after the stream, before `complete`
        world4 = World()
        request4, admission4 = await queued(world4)
        lease4 = await world4.jobs.claim(request4.request_id, "worker-a")
        engine4 = ScriptEngine(events=(PROGRESS, usage_event(Usage.of(1200, 0))))
        runner4 = world4.runner(engine4)
        await world4.jobs.cancel(b.ORG_A, admission4.job_handle)
        result4 = await runner4.execute(lease4)
        assert not result4.settled and result4.refusal == "already_terminal"
        assert world4.outcome(request4.request_id).cause is TerminalCause.client_cancelled
    run(case())


class _CancelAfterFirstDelta(ScriptEngine):
    """An engine whose customer cancels the job while the stream is running. The clock
    moves by one batch interval per event, so the first delta is committed - and the
    cancellation is therefore discovered mid-stream rather than at the end."""

    def __init__(self, world: World, admission) -> None:
        super().__init__(clock=world.clock)
        self.world, self.admission = world, admission

    async def _events(self):
        self.started += 1
        batch_s = self.world.limits.stream_batch_ms / 1000
        try:
            yield PROGRESS
            self.clock.advance(batch_s)
            yield delta("the beginning ")
            self.clock.advance(batch_s)
            await self.world.jobs.cancel(b.ORG_A, self.admission.job_handle)
            yield delta("and more ")
            self.clock.advance(batch_s)
            yield usage_event(Usage.of(1200, 2))
        finally:
            self.closed += 1


def test_dur_fence__a_deadline_is_enforced_in_every_phase():
    """r1 R29: the store terminalizes past a persisted instant inside the very call that
    finds it, so no phase depends on a reaper - and the worker never settles on top."""
    async def case():
        # 1. the queue deadline, before the claim
        world = World()
        request, _ = await queued(world, mode=ExecutionMode.stream)
        world.clock.advance(world.limits.queue_wait_interactive_s + 1)
        engine = ScriptEngine(events=(PROGRESS, usage_event(Usage.of(1200, 0))))
        result = await world.runner(engine).run(request.request_id)
        assert result.refusal == "not_claimable" and engine.started == 0
        await world.jobs.recover()
        assert world.outcome(request.request_id).cause is TerminalCause.queue_wait_expired

        # 2. the generation deadline, mid-stream, found by an append
        world2 = World()
        request2, _ = await queued(world2)
        lease2 = await world2.jobs.claim(request2.request_id, "worker-a")
        slow = ScriptEngine(events=(PROGRESS, delta("one "), delta("two "),
                                    usage_event(Usage.of(1200, 2))),
                            clock=world2.clock,
                            advance_s=world2.limits.generation_timeout_s + 1)
        result2 = await world2.runner(slow).execute(lease2)
        assert result2.refusal == "already_terminal" and not result2.settled
        outcome2 = world2.outcome(request2.request_id)
        assert outcome2.cause is TerminalCause.deadline_exceeded and outcome2.debit == 0
        assert outcome2.settlement_state is SettlementState.released_platform_absorbed
    run(case())


def test_dur_fence__a_silent_engine_is_bounded_by_the_attempts_own_deadline():
    """W1 limit: the adapter checks its bounds when a chunk arrives, so an engine that
    sends nothing at all overshoots by one chunk interval - for ever, if it never sends
    one. The task-level deadline is W2's, and it is the lease's persisted instant."""
    async def case():
        limits = DEFAULTS.replace(generation_timeout_s=0.05)
        world = World(limits=limits)
        request, _ = await queued(world)
        engine = ScriptEngine(hang=True)
        # `wait_for` is the case's own backstop, not the loop's: if the attempt is not
        # bounded by the lease's instant this raises instead of settling, which is the
        # declared kill mode of the mutant that removes the bound.
        result = await asyncio.wait_for(world.runner(engine).run(request.request_id),
                                        timeout=2)
        assert result.proposed_cause is TerminalCause.deadline_exceeded
        assert engine.started == 1 and engine.closed == 1     # the stream was closed
        outcome = result.outcome
        assert outcome.cause is TerminalCause.deadline_exceeded
        assert outcome.state is JobState.failed and outcome.debit == 0
        # r1 R21: the platform's own deadline is platform-caused, so it is free
        assert outcome.settlement_state is SettlementState.released_platform_absorbed
        assert world.balance()["reserved"] == 0

        # the other direction: the bound is the lease's instant, so an attempt with 300 s
        # of generation budget left is *not* cut short - a constant here would fail every
        # legitimate stream
        patient = World()
        request2, _ = await queued(patient)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                patient.runner(ScriptEngine(hang=True)).run(request2.request_id), timeout=0.1)
        assert patient.outcome(request2.request_id) is None
    run(case())


def test_dur_settle__an_engine_cancel_that_is_refused_still_stops_the_task():
    """W1 limit 2: `cancel` can answer `False` (a full intent map, or the window between
    `generate()` and the first `__anext__`). We own the task, so `False` is not a reason
    to keep reading."""
    async def case():
        world = World()
        request, admission = await queued(world)
        lease = await world.jobs.claim(request.request_id, "worker-a")
        engine = _CancelAfterFirstDelta(world, admission)
        engine.cancel_answer = False
        result = await world.runner(engine).execute(lease)
        assert result.engine_cancel is False
        assert engine.closed == 1 and result.refusal == "already_terminal"
        assert world.outcome(request.request_id).cause is TerminalCause.client_cancelled
    run(case())


# --------------------------------------------------------------------------
# preparation handoff, refusals before anything runs
# --------------------------------------------------------------------------
def test_dur_settle__a_request_the_engine_cannot_accept_settles_free_and_runs_nothing():
    """The engine boundary re-checks what admission validated (r1 R58). A refusal there
    is ours to absorb - nothing ran, so nobody is charged - and it is settled rather than
    left for a reaper."""
    async def case():
        # an unmeasurable prompt count: preparation's number, not a guess
        world = World()
        request, _ = await queued(world)
        engine = ScriptEngine()
        result = await world.runner(engine, count_prompt_tokens=lambda work: 10 ** 30
                                    ).run(request.request_id)
        assert result.proposed_cause is TerminalCause.platform_error
        assert engine.started == 0 and result.outcome.debit == 0
        assert world.balance()["reserved"] == 0

        # a tokenizer that raises is ours too, and it is not an untyped escape
        broken = World()
        request2, _ = await queued(broken)

        def explode(work):
            raise RuntimeError("no tokenizer")

        result2 = await broken.runner(ScriptEngine(), count_prompt_tokens=explode
                                      ).run(request2.request_id)
        assert result2.proposed_cause is TerminalCause.platform_error
        assert result2.outcome.state is JobState.failed
        assert "no tokenizer" in result2.detail

        # and a media refusal is `invalid_media`, which `02` makes free
        media = World()
        refs = (b.media(b.ORG_A).model_copy(update={"duration_s": None}),)
        request3, _ = await queued(media, refs=refs)
        request3_messages = ({"role": "user",
                              "content": [{"type": "video_url",
                                           "video_url": {"url": "https://customer.example/v"}}]},)
        media.jobs.jobs[request3.request_id].request = request3.model_copy(
            update={"messages": request3_messages})
        _, engine3 = adapter(media)
        result3 = await media.runner(engine3).run(request3.request_id)
        assert result3.proposed_cause is TerminalCause.invalid_media
        assert result3.outcome.settlement_state is SettlementState.released_free
        assert result3.outcome.debit == 0

        # R61 (2): the processing cache lost the prepared file between preparation and
        # this attempt (M2 `local_uri` raises `not_found` on a miss). Nothing is sent,
        # the platform absorbs it, nobody is charged
        lost = World()
        request4, _ = await queued(lost, refs=(b.media(b.ORG_A),))
        lost.jobs.jobs[request4.request_id].request = request4.model_copy(
            update={"messages": request3_messages})

        def evicted(ref):
            raise errors.NotFound(f"media {ref.handle} is not in the processing cache")

        upstream4, engine4 = adapter(lost, local_uri=evicted)
        result4 = await lost.runner(engine4).run(request4.request_id)
        assert result4.proposed_cause is TerminalCause.platform_error
        assert result4.outcome.debit == 0 and upstream4.requests == []
    run(case())


# --------------------------------------------------------------------------
# the loop: claiming, concurrency, drain, restart
# --------------------------------------------------------------------------
def test_ops_recover__the_loop_claims_acknowledges_and_settles_every_candidate():
    """`02` §4: the index offers, `JobStore.claim` decides. Every candidate this worker
    consumed is acknowledged, whether its claim won or lost."""
    async def case():
        world = World()
        requests = []
        for _ in range(3):
            request, _ = await queued(world)
            requests.append(request)
            await world.scheduler.enqueue(candidate(world, request))
        # a preparation candidate in the same index: r1 R46/R52 make it M's, and a
        # kind-filtered claim also keeps this pool out of R60's level-1 fairness state
        preparation, _ = await queued(world)
        prepare_candidate = candidate(world, preparation).model_copy(
            update={"kind": OutboxKind.prepare_dispatch})
        await world.scheduler.enqueue(prepare_candidate)

        _, engine = adapter(world)
        loop = WorkerLoop(scheduler=world.scheduler, runner=world.runner(engine),
                          worker_id="worker-a", limits=world.limits)
        results = await loop.run(concurrency=2)
        assert len(results) == 3 and all(result.settled for result in results)
        assert prepare_candidate.event_id in world.scheduler.pending
        assert world.outcome(preparation.request_id) is None
        assert {result.cause for result in results} == {TerminalCause.completed}
        assert world.scheduler.depth() == 1               # the preparation candidate
        assert len(world.scheduler.acknowledged) == 3
        assert loop.claimed == 3 and loop.failures == []
    run(case())


def test_ops_recover__a_candidate_whose_claim_loses_is_still_acknowledged():
    """A duplicate dispatch, a job somebody already ran, a job the customer cancelled:
    the candidate is consumed either way, or the index grows a permanent ghost."""
    async def case():
        world = World()
        request, admission = await queued(world)
        await world.scheduler.enqueue(candidate(world, request))
        await world.jobs.cancel(b.ORG_A, admission.job_handle)
        loop = WorkerLoop(scheduler=world.scheduler, runner=world.runner(ScriptEngine()),
                          worker_id="worker-a", limits=world.limits)
        results = await loop.run()
        assert len(results) == 1 and results[0].refusal == "already_terminal"
        assert world.scheduler.depth() == 0 and len(world.scheduler.acknowledged) == 1
    run(case())


def test_ops_recover__a_lease_held_by_a_dead_worker_is_never_resumed():
    """OPS-RECOVER: a restart does not pick a lease back up. `recover` requeues the
    attempt as a **new** generation, which arrives as an ordinary candidate, and the
    published-output rule still forbids regenerating a job that produced any."""
    async def case():
        world = World()
        request, _ = await queued(world)
        dead = await world.jobs.claim(request.request_id, "worker-dead")
        world.clock.advance(world.limits.lease_ttl_s + 1)
        produced = await world.jobs.recover()
        events = [event for event in produced if isinstance(event, IndexEvent)]
        assert len(events) == 1 and events[0].attempt == 1
        for event in events:
            await world.scheduler.enqueue(event)

        _, engine = adapter(world)
        loop = WorkerLoop(scheduler=world.scheduler, runner=world.runner(engine),
                          worker_id="worker-b", limits=world.limits)
        results = await loop.run()
        assert len(results) == 1 and results[0].generation == dead.generation + 1
        assert results[0].cause is TerminalCause.completed

        # a job that had published output is not requeued at all
        published = World()
        request2, _ = await queued(published)
        lease2 = await published.jobs.claim(request2.request_id, "worker-dead")
        await published.stream.append(lease2, (delta("published "),))
        published.clock.advance(published.limits.lease_ttl_s + 1)
        await published.jobs.recover()
        outcome = published.outcome(request2.request_id)
        assert outcome.cause is TerminalCause.lost_after_publication
        assert outcome.debit == 0
    run(case())


def test_ops_recover__a_drain_stops_claiming_and_releases_what_it_cannot_finish():
    """Drain preserves durable jobs: what is still running at the bound is cancelled,
    not settled. The store decides - the lease expires and `recover` requeues or fails
    it - because a worker that did not finish has no outcome to propose."""
    async def case():
        world = World()
        started = asyncio.Event()
        request, _ = await queued(world)
        queued_second, _ = await queued(world)
        await world.scheduler.enqueue(candidate(world, request))
        await world.scheduler.enqueue(candidate(world, queued_second))

        class _Blocking(ScriptEngine):
            async def _events(self):
                self.started += 1
                try:
                    yield PROGRESS
                    started.set()
                    await asyncio.Event().wait()
                    yield delta("never")
                finally:
                    self.closed += 1

        engine = _Blocking()
        loop = WorkerLoop(scheduler=world.scheduler, runner=world.runner(engine),
                          worker_id="worker-a", limits=world.limits)
        running = asyncio.create_task(loop.run(concurrency=1, stop_when_idle=False))
        await asyncio.wait_for(started.wait(), timeout=2)
        report = await asyncio.wait_for(loop.drain(within_s=0.05), timeout=2)
        await asyncio.wait_for(running, timeout=2)

        assert report.released == 1 and report.claimed == 1
        assert engine.closed == 1                       # the stream was closed on the way out
        assert world.outcome(request.request_id) is None         # nothing was settled
        assert world.jobs.jobs[request.request_id].state is JobState.running
        # the second candidate was never claimed: draining stops claiming
        assert world.scheduler.depth() == 1
        # the store is what finishes it, on the lease it is still holding
        world.clock.advance(world.limits.lease_ttl_s + 1)
        await world.jobs.recover()
        assert world.jobs.jobs[request.request_id].state is JobState.queued
    run(case())


def test_ops_recover__a_drain_that_can_wait_lets_the_attempt_finish():
    """The other half of the bound: an attempt that needs a moment more finishes inside a
    generous bound and settles normally, and the loop claims nothing new while draining."""
    async def case():
        world = World()
        request, _ = await queued(world)
        second, _ = await queued(world)
        await world.scheduler.enqueue(candidate(world, request))
        await world.scheduler.enqueue(candidate(world, second))
        in_flight = asyncio.Event()

        class _Slow(ScriptEngine):
            async def _events(self):
                self.started += 1
                try:
                    yield PROGRESS
                    in_flight.set()
                    await asyncio.sleep(0.05)      # still working when the drain arrives
                    yield delta("an answer ")
                    yield usage_event(Usage.of(1200, 1))
                finally:
                    self.closed += 1

        engine = _Slow()
        loop = WorkerLoop(scheduler=world.scheduler, runner=world.runner(engine),
                          worker_id="worker-a", limits=world.limits)
        running = asyncio.create_task(loop.run(concurrency=1, stop_when_idle=False))
        await asyncio.wait_for(in_flight.wait(), timeout=2)
        report = await asyncio.wait_for(loop.drain(within_s=2.0), timeout=3)
        await asyncio.wait_for(running, timeout=2)

        assert report.released == 0 and report.finished == 1
        assert loop.results and loop.results[0].settled
        assert loop.results[0].cause is TerminalCause.completed
        # draining stops claiming: the second candidate is still in the index
        assert loop.claimed == 1 and world.scheduler.depth() == 1
        assert world.outcome(second.request_id) is None
    run(case())


def test_ops_recover__the_shared_fake_engine_drives_the_same_loop():
    """The engines are interchangeable: the shared `FakeEngine`
    settles the same way W1's adapter does, so nothing here depends on the adapter's
    extra reporting. Its `missing_usage` fault takes the reconciliation path."""
    async def case():
        world = World()
        request, _ = await queued(world)
        engine = FakeEngine(clock=world.clock, limits=world.limits)
        result = await world.runner(engine).run(request.request_id)
        assert result.cause is TerminalCause.completed
        assert world.visible(request.request_id) == FakeEngine().text
        assert result.outcome.usage is not None and result.outcome.debit > 0

        missing = World()
        request2, _ = await queued(missing)
        silent = FakeEngine(clock=missing.clock, limits=missing.limits,
                                      fault=EngineFault.missing_usage)
        result2 = await missing.runner(silent).run(request2.request_id)
        assert result2.cause is TerminalCause.engine_incomplete
        assert result2.outcome.usage is None
        assert result2.outcome.settlement_state is SettlementState.held_unknown

        # and its abrupt exit is an engine failure, not an untyped escape
        exited = World()
        request3, _ = await queued(exited)
        dying = FakeEngine(clock=exited.clock, limits=exited.limits,
                                     fault=EngineFault.abrupt_exit)
        result3 = await exited.runner(dying).run(request3.request_id)
        assert result3.proposed_cause is TerminalCause.platform_error
        assert result3.outcome.state is JobState.failed and result3.outcome.debit == 0
    run(case())


def test_dur_output__a_relay_that_fails_never_fails_a_committed_attempt():
    """The chunks are durable before the relay sees them, so a delivery failure is the
    customer's cursor to resolve (`02`), not a reason to fail a settled job."""
    async def case():
        world = World()
        request, _ = await queued(world)
        _, engine = adapter(world)

        async def broken(chunks):
            raise RuntimeError("the client went away")

        result = await world.runner(engine, relay=broken).run(request.request_id)
        assert result.cause is TerminalCause.completed
        assert result.committed > 0 and result.relayed == 0
        assert "relay failed" in result.detail
        assert world.visible(request.request_id) != ""
    run(case())


# --------------------------------------------------------------------------
# the two adapter changes S2M's pinned profile settles (engine.py, minimal)
# --------------------------------------------------------------------------
def test_api_stream__a_prepared_video_reaches_the_engine_as_a_local_file_of_its_own_tenant():
    """R61 (2) as amended / S2M D3: the engine opens the prepared object M2 materialized at
    `<root>/<org>/<profile>/<digest16>/source.<ext>` under the root it was started with
    (`--allowed-local-media-path`). The path is M2's (`local_uri`) and the adapter's to
    check: inside the root, and every segment the request's own - the organization read
    from the path and compared with the request's, never a carried field."""
    box = _Box()
    work = _video_work(box)
    prepared = prepared_request(work, 1200)
    ref = prepared.media[0]
    org, d16 = ref.org_id, ref.digest.removeprefix("sha256:")[:16]
    good = f"/srv/cache/{org}/v1/{d16}/source.mp4"

    default = FakeUpstream(clock=box.clock).engine()
    assert default.upstream_body(prepared)["messages"][0]["content"][1]["video_url"] == {
        "url": f"file://{FAKE_MEDIA_ROOT}/{org}/v1/{d16}/source.mp4"}
    # the root is a deployment fact (PROCESSING_CACHE_DIR), the engine's, not the request's
    pinned = FakeUpstream(clock=box.clock,
                          limits=DEFAULTS.replace(processing_cache_dir="/srv/cache")).engine()
    assert pinned.upstream_body(prepared)["messages"][0]["content"][1]["video_url"] == {
        "url": f"file://{good}"}

    # every segment is checked: root, organization, profile version, digest, file name
    assert _inside_tenant_root(good, "/srv/cache", org, ref)
    for bad in (f"/srv/cache/{b.ORG_B}/v1/{d16}/source.mp4",       # another tenant
                f"/other/{org}/v1/{d16}/source.mp4",               # outside the root
                f"/srv/cache/{org}/v2/{d16}/source.mp4",           # another profile
                f"/srv/cache/{org}/v1/{'0' * 16}/source.mp4",      # another object
                f"/srv/cache/{org}/v1/{d16}/passwd",               # not M2's file
                f"/srv/cache/{org}/v1/{d16}/source.mp4/source.mp4",  # a deeper path
                f"/srv/cache/{org}/v1/../../{org}/v1/{d16}/source.mp4",   # not normalized
                "/etc/passwd"):
        assert not _inside_tenant_root(bad, "/srv/cache", org, ref), bad
    # a relative root, a relative path and a request with no organization own nothing
    assert not _inside_tenant_root(good[1:], "/srv/cache", org, ref)
    assert not _inside_tenant_root(f"cache/{org}/v1/{d16}/source.mp4", "cache", org, ref)
    assert not _inside_tenant_root(f"/srv/cache//v1/{d16}/source.mp4", "/srv/cache", "", ref)

    # what the resolver returns is checked, not trusted: a foreign or non-file answer
    # is refused before anything is sent
    for answer in (f"file:///srv/cache/{b.ORG_B}/v1/{d16}/source.mp4",
                   f"https://videos.example.com/{org}/v1/{d16}/source.mp4",
                   f"http://{good}", good, None):
        with pytest.raises(errors.NotFound):
            local_media_url(ref, "/srv/cache", org, lambda _ref, a=answer: a)
    # the organization compared is the **request's**: a ref that claims org B while the
    # request is org A never becomes org B's path, even if the resolver agrees with the ref
    with pytest.raises(errors.NotFound):
        local_media_url(ref.model_copy(update={"org_id": b.ORG_B}), "/srv/cache", org,
                        m2_local_uri("/srv/cache"))
    # no resolver configured: a typed platform refusal, never a guessed path
    with pytest.raises(errors.DependencyUnavailable):
        local_media_url(ref, "/srv/cache", org, None)
    unwired = FakeUpstream(clock=box.clock).engine(local_uri=None)
    with pytest.raises(errors.DependencyUnavailable):
        unwired.upstream_body(prepared)


def test_api_stream__both_eos_ids_are_supplied_on_every_request():
    """S2M §1.2 consequence 1: the `--hf-overrides` remap loses Marlin's second EOS id, so
    the request re-supplies both. An engine that stops on only one runs every answer to
    the output ceiling, which the customer pays for."""
    box = _Box()
    engine = FakeUpstream(clock=box.clock).engine()
    body = engine.upstream_body(_text_prepared(box))
    assert body.get("stop_token_ids") == [248044, 248046] == list(MODEL_EOS_TOKEN_IDS)
    # not a caller parameter: a client cannot widen or narrow the stop set
    with pytest.raises(errors.UnsupportedParameter):
        engine.upstream_body(_text_prepared(box, parameters={"stop_token_ids": [1]}))
    # consequence 2 of the same remap is the filter's, and `visible` never carries it
    assert filter_text("<think>the van is stationary</think>Two people unload boxes.") == \
        "Two people unload boxes."


class _LyingStream:
    """An iterator that reports facts that do not add up: `terminal_cause` says the answer
    is whole, the rest of the report says it is not. W1's adapter never does this; the
    point is that the **worker** does not take the billing decision on trust."""

    def __init__(self, events, **facts) -> None:
        self._events = list(events)
        self.closed = 0
        self.__dict__.update(facts)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)

    async def aclose(self) -> None:
        self.closed += 1


@dataclass
class LyingEngine:
    events: tuple = ()
    facts: dict = field(default_factory=dict)
    stream: object = None
    cancelled: list = field(default_factory=list)

    def generate(self, lease, prepared):
        self.stream = _LyingStream(self.events, **self.facts)
        return self.stream

    async def cancel(self, lease) -> bool:
        self.cancelled.append((lease.job_id, lease.generation))
        return True

    async def health(self) -> dict:
        return {"ready": True}

    async def drain(self) -> None:
        return None


def test_dur_settle__an_adapters_completed_is_not_taken_on_trust():
    """W1 handback: `terminal_cause` is advisory and the store recomputes settlement, so
    the worker re-checks the three facts `completed` needs - the engine finished, it said
    what it used, and no line was dropped. Each missing fact on its own is enough."""
    async def case():
        lies = (
            # a completed cause with no usage at all
            ({"terminal_cause": TerminalCause.completed, "finish_reason": "stop",
              "malformed_lines": 0}, (PROGRESS, delta("an answer "))),
            # a finish reason outside the set
            ({"terminal_cause": TerminalCause.completed, "finish_reason": "abort",
              "malformed_lines": 0}, (PROGRESS, delta("an answer "),
                                      usage_event(Usage.of(1200, 1)))),
            # a line the adapter could not place: content we may have dropped
            ({"terminal_cause": TerminalCause.completed, "finish_reason": "stop",
              "malformed_lines": 1}, (PROGRESS, delta("an answer "),
                                      usage_event(Usage.of(1200, 1)))),
        )
        for facts, events in lies:
            world = World()
            request, _ = await queued(world)
            engine = LyingEngine(events=events, facts=facts)
            result = await world.runner(engine).run(request.request_id)
            assert result.proposed_cause is TerminalCause.engine_incomplete, facts
            assert result.outcome.state is JobState.failed, facts
            assert result.outcome.debit == 0, facts
            assert request.request_id not in world.results, facts
            assert engine.stream.closed == 1, facts

        # the same adapter telling the truth does settle a success
        honest = World()
        request, _ = await queued(honest)
        truthful = LyingEngine(events=(PROGRESS, delta("an answer "),
                                       usage_event(Usage.of(1200, 1))),
                               facts={"terminal_cause": TerminalCause.completed,
                                      "finish_reason": "stop", "malformed_lines": 0})
        result = await honest.runner(truthful).run(request.request_id)
        assert result.cause is TerminalCause.completed and result.outcome.debit > 0
    run(case())


def test_dur_settle__a_usage_record_that_is_not_authoritative_is_unknown():
    """r1 R58: the event's own certainty decides. A record marked `unknown` must not reach
    `complete` - the store refuses a non-authoritative usage outright, so passing one
    would lose the settlement, and believing it would bill a guess."""
    async def case():
        world = World()
        request, _ = await queued(world)
        guessed = Usage.of(1200, 40).model_copy(update={"certainty": "unknown"})
        engine = ScriptEngine(events=(PROGRESS, delta("an answer "), usage_event(guessed)))
        result = await world.runner(engine).run(request.request_id)
        assert result.settled, result.refusal
        assert result.usage_unknown_reason == "unknown"
        assert result.outcome.usage is None and result.outcome.debit == 0
        assert result.outcome.cause is TerminalCause.engine_incomplete
        assert result.outcome.settlement_state is SettlementState.held_unknown
    run(case())


# --------------------------------------------------------------------------
# independent review of 1ea3817: the reviewer's killing cases, lifted verbatim
# --------------------------------------------------------------------------
def test_gap__the_relay_never_receives_anything_the_journal_has_not_taken():
    """The World's relay raises on a pre-commit object, and the runner swallows relay
    errors - so a recording relay is what proves the order."""
    async def case():
        world = World()
        request, _ = await queued(world)
        _, engine = adapter(world)
        received = []

        async def recording(chunks):
            for chunk in chunks:
                stored = world.stream.chunks.get(request.request_id, [])
                received.append((chunk, any(getattr(chunk, "sequence", None) == held.sequence
                                            and getattr(chunk, "generation", None) == held.generation
                                            for held in stored)))

        result = await world.runner(engine, relay=recording).run(request.request_id)
        assert result.cause is TerminalCause.completed
        assert "relay failed" not in result.detail, result.detail
        assert received and all(committed for _, committed in received), received
        assert len(received) == result.committed == result.relayed
    run(case())


def test_gap__a_cancellation_that_lands_between_the_last_append_and_complete_settles_nothing():
    async def case():
        world = World()
        request, admission = await queued(world)
        engine = ScriptEngine(events=(PROGRESS, delta("done "), usage_event(Usage.of(1200, 1))))

        async def cancel_then_store(job_id, text, lease):
            await world.jobs.cancel(b.ORG_A, admission.job_handle)   # after the last append
            return f"infrx-result:{job_id}"

        result = await world.runner(engine, put_result=cancel_then_store).run(request.request_id)
        assert not result.settled and result.refusal == "already_terminal", result
        assert world.outcome(request.request_id).cause is TerminalCause.client_cancelled
    run(case())


def test_gap__a_stale_complete_settles_nothing():
    async def case():
        world = World()
        request, _ = await queued(world)
        engine = ScriptEngine(events=(PROGRESS, delta("done "), usage_event(Usage.of(1200, 1))))

        def expire_then_store(job_id, text, lease):
            world.clock.advance(world.limits.lease_ttl_s + 1)
            return f"infrx-result:{job_id}"

        result = await world.runner(engine, put_result=expire_then_store).run(request.request_id)
        assert not result.settled and result.refusal == "stale_lease", result
        assert world.outcome(request.request_id) is None
    run(case())


def test_gap__the_task_deadline_is_the_clamped_instant_not_the_generation_budget():
    async def case():
        world = World()
        # R29 clamp: the caller's deadline is far shorter than the 300 s generation budget
        request, _ = await queued(world, deadline_s=0.05)
        engine = ScriptEngine(hang=True)
        result = await asyncio.wait_for(world.runner(engine).run(request.request_id), timeout=2)
        assert result.proposed_cause is TerminalCause.deadline_exceeded
        assert engine.closed == 1
        # N4: the engine is told to stop with the current lease, not only disconnected
        assert [job for job, _ in engine.cancelled] == [result.outcome.job_id]
    run(case())


def test_gap__the_usage_settled_is_the_engines_authoritative_record_unchanged():
    async def case():
        world = World()
        request, _ = await queued(world)
        engine = ScriptEngine(events=(PROGRESS, delta("a "), delta("b "), delta("c "),
                                      usage_event(Usage.of(1200, 7))))
        result = await world.runner(engine).run(request.request_id)
        assert result.cause is TerminalCause.completed
        assert result.outcome.usage == Usage.of(1200, 7)
        assert result.outcome.debit == b.DEFAULT_PRICE.debit(1200, 7)
    run(case())


class _CancelThenKeepTalking(ScriptEngine):
    def __init__(self, world, admission):
        super().__init__(clock=world.clock)
        self.world, self.admission, self.yielded = world, admission, 0

    async def _events(self):
        self.started += 1
        batch_s = self.world.limits.stream_batch_ms / 1000
        try:
            for event in (PROGRESS, delta("one "), delta("two ")):
                self.clock.advance(batch_s); self.yielded += 1; yield event
            await self.world.jobs.cancel(b.ORG_A, self.admission.job_handle)
            for n in range(20):
                self.clock.advance(batch_s); self.yielded += 1; yield delta(f"late{n} ")
            self.yielded += 1; yield usage_event(Usage.of(1200, 22))
        finally:
            self.closed += 1


def test_gap__a_discovered_cancellation_stops_the_worker_reading_the_stream():
    async def case():
        world = World()
        request, admission = await queued(world)
        engine = _CancelThenKeepTalking(world, admission)
        result = await world.runner(engine).run(request.request_id)
        assert result.refusal == "already_terminal" and result.cancelled
        # discovered by the first append after the cancellation; the rest is never pulled
        assert engine.yielded <= 5, engine.yielded
        assert engine.closed == 1
    run(case())


def test_gap__a_sibling_prefixed_root_is_outside_the_root():
    box = _Box()
    prepared = prepared_request(_video_work(box), 1200)
    ref = prepared.media[0]
    d16 = ref.digest.removeprefix("sha256:")[:16]
    assert _inside_tenant_root(f"/srv/cache/{ref.org_id}/v1/{d16}/source.mp4", "/srv/cache", ref.org_id, ref)
    assert not _inside_tenant_root(f"/srv/cache2/{ref.org_id}/v1/{d16}/source.mp4", "/srv/cache", ref.org_id, ref)
    assert not _inside_tenant_root(f"/srv/cache-old/{ref.org_id}/v1/{d16}/source.mp4", "/srv/cache", ref.org_id, ref)
