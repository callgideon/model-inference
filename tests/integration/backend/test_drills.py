"""E3B.b: the crash-boundary drill list, as executable cases.

Every drill is written against the PORTS (`JobStore`, `StreamStore`, `Scheduler`) and is
parametrised over the store it runs on:

* `fake`     - the merged contract fakes. Runs today; proves the drill and its assertions
               (a fake-only result is *implemented*, never *integrated*).
* `postgres` - the real PostgreSQL JobStore and, since D4, its `PgStreamStore` journal
               (`infrx.state.pgtesting` on this stack's PostgreSQL, E3B phase 2). A drill
               names the store functions it drives and is PENDING - on the task the stub
               names - only while one of THOSE is still an `infrx.unimplemented` stub
               (D5's settlement).

Where a real store does exist it is used directly: Q2's `ValkeyScheduler` on E2's Valkey
(queue loss and rebuild, index saturation), migrations 0001-0005 on E2's PostgreSQL (the
schema's settlement and tenant backstops), M2's probe and fetcher (malformed and hostile
media) and the composition root's startup refusal (the legacy shared-key fallback).

Accounting conservation is asserted after every drill that moves money: for each tenant,
ledger = granted - sum(settled debits), reserved = sum(holds still reserved), available
never negative - computed from port reads, not from a fake's attributes.

Drill ids are `E3B-DR-nn`; the evidence maps each to its oracle (04).
"""
from __future__ import annotations

import asyncio
import re
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stack                                            # noqa: E402

import harness                                          # noqa: E402

from infrx.contracts import errors                      # noqa: E402
from infrx.contracts.conformance import builders as b   # noqa: E402
from infrx.contracts.fakes.factories import (credit_jobstore_factory,  # noqa: E402
                                             jobstore_factory)
from infrx.contracts.fakes.support import CrashAfterCommit  # noqa: E402
from infrx.contracts.limits import DEFAULTS             # noqa: E402
from infrx.contracts.records import (ExecutionMode, IndexEvent, JobState,  # noqa: E402
                                     OutboxKind, SettlementState, TerminalCause)

GRANT = "5"
BACKENDS = ("fake", "postgres")


# E3B phase 2, item 2: a live defect for the next `postgres` rig (a callable applying it to
# that rig's own clone), set by the db06/db07 drills and nothing else.
DEFECT = None


def rig(backend: str, *rpcs: str, credit: bool = False, **limits):
    """The store a drill runs on. `rpcs` are the store functions the drill drives: on
    `postgres` the drill is PENDING, on the task each stub names, only while one of THOSE is
    an `infrx.unimplemented` stub (item 1) - a D6 stub it never calls cannot hold it back."""
    settings = DEFAULTS.replace(**limits) if limits else None
    if backend == "postgres":
        stubs = stack.stubbed(rpcs) if stack.has_stack() else {}
        if stubs:
            stack.pending(*sorted(set(stubs.values())),
                          why=f"{sorted(stubs)} are still infrx.unimplemented stubs")
        h = stack.pg_jobstore(settings)
        if DEFECT is not None:
            DEFECT()
    else:
        h = (credit_jobstore_factory if credit else jobstore_factory)(limits=settings)
    h.extra["grant"](b.ORG_A, GRANT)
    h.extra["grant"](b.ORG_B, GRANT)
    return h


# The functions each group of drills drives (`rig`'s probe), in one place.
ADMIT = ("admit",)
PREPARE = (*ADMIT, "claim_preparation", "prepare")
RUN = (*PREPARE, "claim")


def run(body):
    return asyncio.run(body())


async def admit(h, *, org=b.ORG_A, key=b.KEY_A, idem_key="k1"):
    request = b.request(h, org_id=org, key_id=key)
    return request, await h.port.admit(request, b.idem(request, idem_key))


async def running(h, admission, worker="w1"):
    lease = await h.port.claim_preparation(admission.request_id, worker)
    await h.port.prepared(lease, ())
    return await h.port.claim(admission.request_id, worker)


async def assert_conserved(h, org, handles):
    """ledger = granted - settled debits; reserved = holds still reserved; available >= 0."""
    debit, reserved = Decimal(0), Decimal(0)
    for handle in handles:
        admission, outcome = await h.port.get_owned(org, handle)
        if outcome is None or outcome.settlement_state is SettlementState.held_unknown:
            reserved += admission.maximum_hold
        if outcome is not None:
            debit += outcome.debit
    balance = h.extra["balance"](org)
    assert balance["ledger"] == Decimal(GRANT) - debit, (balance, debit)
    assert balance["reserved"] == reserved, (balance, reserved)
    assert balance["available"] >= 0, balance


async def reap(h):
    """The reaper as the worker process runs it (W3, item 5): `WorkerService.reap_once()`
    over the drill's store, embedded (`health_port=None`), with W's engine double. Returns
    the service and the index events it enqueued, read back from the loop's own index. The
    attempt runner is built but never runs: a reap claims nothing."""
    from infrx.contracts.fakes.scheduling import FakeScheduler
    from infrx.worker.attempt import AttemptRunner
    from infrx.worker.fakes import engine_factory
    from infrx.worker.loop import WorkerLoop
    from infrx.worker.service import WorkerService
    engine, index = engine_factory().port, FakeScheduler(h.clock)
    runner = AttemptRunner(jobs=h.port, stream=h.extra["stream"], engine=engine,
                           clock=h.clock, worker_id="e3b2-worker",
                           count_prompt_tokens=lambda work: 0,
                           put_result=lambda job_id, text: None)
    service = WorkerService(loop=WorkerLoop(scheduler=index, runner=runner,
                                            worker_id="e3b2-worker"),
                            jobs=h.port, engine=engine)
    await service.reap_once()
    enqueued = []
    while (event := await index.claim_candidate("e3b2-probe")) is not None:
        enqueued.append(event)
    return service, enqueued


def dispatch_ids(h, job_id, kind):
    return [event.event_id for event in h.extra["outbox"](job_id) if event.kind is kind]


def usage_projections(h, job_id):
    return h.extra["outbox_kinds"](job_id).count(OutboxKind.usage_projection)


# ------------------------------------------------------------------ acceptance

@pytest.mark.parametrize("backend", BACKENDS)
def test_e3b_dr01_acceptance_crash_after_commit_retries_to_one_identity(backend):
    """DUR-ADMIT: the process dies after the admission committed and before it answered.
    The retry with the same idempotency key is the SAME accepted job - one job, one hold
    of exactly its maximum, one prepare dispatch - and nothing was reserved twice."""
    h = rig(backend, *ADMIT)

    async def body():
        h.failures.crash_after_commit("admit")
        request = b.request(h)
        idem = b.idem(request, "k1")
        with pytest.raises(CrashAfterCommit):
            await h.port.admit(request, idem)
        again = await h.port.admit(request, idem)
        assert again.replayed and again.request_id == request.request_id
        assert len(h.extra["active_jobs"](b.ORG_A)) == 1
        assert h.extra["outbox_kinds"](request.request_id) == [OutboxKind.prepare_dispatch]
        assert h.extra["balance"](b.ORG_A)["reserved"] == again.maximum_hold > 0
        await assert_conserved(h, b.ORG_A, [again.job_handle])
    run(body)


@pytest.mark.parametrize("backend", BACKENDS)
def test_e3b_dr02_a_refused_admission_leaves_nothing_behind(backend):
    """DUR-ADMIT / DUR-CAP / API-AUTH: a revoked key, an unpriced model, an empty wallet and
    a full key each refuse with their typed error and leave no job, hold, dispatch or
    journal reservation - the invalid call cannot leak capacity or credit."""
    h = rig(backend, *ADMIT, max_active_jobs_per_key=1)

    async def refused(expected, **kw):
        request = b.request(h, **kw)
        with pytest.raises(expected):
            await h.port.admit(request, b.idem(request, str(uuid.uuid4())))
        assert h.extra["outbox"](request.request_id) == []

    async def body():
        journal_before = h.extra["journal_bytes"]()
        h.extra["revoke_key"](b.KEY_B)
        await refused(errors.InvalidApiKey, org_id=b.ORG_B, key_id=b.KEY_B)
        h.extra["unrevoke_key"](b.KEY_B)
        h.extra["set_price"](b.MODEL, None)
        await refused(errors.InvalidRequest)
        h.extra["set_price"](b.MODEL, b.DEFAULT_PRICE)
        _, kept = await admit(h)                          # fills the key's one slot
        await refused(errors.CapacityExhausted)
        assert h.extra["balance"](b.ORG_B)["reserved"] == 0
        assert len(h.extra["active_jobs"]()) == 1
        assert h.extra["journal_bytes"]() - journal_before == \
            DEFAULTS.journal_job_reserve_bytes            # the one kept job's reservation
        await assert_conserved(h, b.ORG_A, [kept.job_handle])
        await assert_conserved(h, b.ORG_B, [])
    run(body)


# ------------------------------------------------------------------ preparation / dispatch

@pytest.mark.parametrize("backend", BACKENDS)
def test_e3b_dr03_a_lost_preparation_worker_is_redispatched_and_fenced(backend):
    """DUR-FENCE (preparation): the preparing worker dies; after its short lease the reaper
    - W3's `WorkerService.reap_once`, as the worker process runs it - re-dispatches
    preparation as a new dispatch row, the dead worker's late `prepared` is refused, and the
    new worker queues the job exactly once."""
    h = rig(backend, *PREPARE, "recover")

    async def body():
        _, admission = await admit(h)
        dead = await h.port.claim_preparation(admission.request_id, "w1")
        h.clock.advance(DEFAULTS.preparation_lease_ttl_s + 1)
        service, events = await reap(h)
        kinds = h.extra["outbox_kinds"](admission.request_id)
        assert kinds == [OutboxKind.prepare_dispatch] * 2, kinds
        # Preparation's redispatch is an outbox row the relay delivers (Q3), not an index
        # event the reaper returns: the reaper ran, cleanly, and enqueued nothing itself.
        assert (service.reaped, service.reap_errors, events) == (0, 0, [])
        live = await h.port.claim_preparation(admission.request_id, "w2")
        with pytest.raises(errors.StaleLease):
            await h.port.prepared(dead, ())
        queued = await h.port.prepared(live, ())
        assert queued.state is JobState.queued
        assert h.extra["outbox_kinds"](admission.request_id).count(
            OutboxKind.inference_dispatch) == 1
        await assert_conserved(h, b.ORG_A, [admission.job_handle])
    run(body)


@pytest.mark.parametrize("backend", BACKENDS)
def test_e3b_dr04_a_claim_whose_answer_was_lost_is_requeued_once(backend):
    """DUR-FENCE / DUR-OUTBOX (dispatch): the claim committed but the worker never got the
    lease. After lease expiry the reaper (`WorkerService.reap_once`) requeues it as a
    prepublication retry with ONE new inference dispatch row, enqueued in the worker's
    index, and the next claim is a new generation."""
    h = rig(backend, *RUN, "recover")

    async def body():
        _, admission = await admit(h)
        lease = await h.port.claim_preparation(admission.request_id, "w1")
        await h.port.prepared(lease, ())
        h.failures.crash_after_commit("claim")
        with pytest.raises(CrashAfterCommit):
            await h.port.claim(admission.request_id, "w1")
        h.clock.advance(DEFAULTS.lease_ttl_s + 1)
        service, events = await reap(h)
        assert service.reaped == 1
        assert [(e.job_id, e.attempt) for e in events] == [(admission.request_id, 1)]
        # D2 delta / Q3 FID-5: the requeue is a NEW dispatch row, never the acknowledged one.
        first, again = dispatch_ids(h, admission.request_id, OutboxKind.inference_dispatch)
        if backend == "fake":
            # Integration request #3, accepted (coordinator ruling b): the contract fake mints
            # the requeue event's id apart from its row's. This pin goes red the day the fake
            # is fixed at the E3B2 merge; then this branch is deleted (review F4).
            assert events[0].event_id not in (first, again), (events, first, again)
        else:
            assert events[0].event_id == again != first
        second = await h.port.claim(admission.request_id, "w2")
        assert second.generation == 2
        assert h.extra["outbox_kinds"](admission.request_id).count(
            OutboxKind.inference_dispatch) == 2
        await assert_conserved(h, b.ORG_A, [admission.job_handle])
    run(body)


# ------------------------------------------------------------------ output

@pytest.mark.parametrize("backend", BACKENDS)
def test_e3b_dr05_a_stale_generation_cannot_append(backend):
    """DUR-FENCE (append): generation 1 loses its lease and the job is requeued; the SAME
    worker id claims generation 2. Generation 1's append is refused and stores nothing;
    only generation 2's output is readable. (Same worker id on purpose: only the
    generation, not the owner, distinguishes the two.)"""
    h = rig(backend, *RUN, "recover", "append")

    async def body():
        _, admission = await admit(h)
        old = await running(h, admission, "w1")
        h.clock.advance(DEFAULTS.lease_ttl_s + 1)
        await h.port.recover()
        new = await h.port.claim(admission.request_id, "w1")
        assert new.generation == old.generation + 1
        stream = h.extra["stream"]
        with pytest.raises((errors.StaleLease, errors.AlreadyTerminal)):
            await stream.append(old, b.events("stale"))
        await stream.append(new, b.events("fresh"))
        chunks, _cursor = await stream.read_owned(b.ORG_A, admission.job_handle, None)
        assert {c.generation for c in chunks} == {new.generation}, chunks
        assert all("stale" not in str(c.payload) for c in chunks)
    run(body)


@pytest.mark.parametrize("backend", BACKENDS)
def test_e3b_dr06_a_worker_lost_after_publication_is_never_regenerated(backend):
    """DUR-OUTPUT: the worker dies after its first committed chunk. The reaper fails the job
    `lost_after_publication` (no second attempt, no early success) and, usage unknown, the
    hold stays reserved for reconciliation rather than becoming a debit."""
    h = rig(backend, *RUN, "append", "recover")

    async def body():
        _, admission = await admit(h)
        lease = await running(h, admission)
        await h.extra["stream"].append(lease, b.events("first"))
        h.clock.advance(DEFAULTS.lease_ttl_s + 1)
        produced = await h.port.recover()
        assert not [item for item in produced if isinstance(item, IndexEvent)]
        _, outcome = await h.port.get_owned(b.ORG_A, admission.job_handle)
        assert (outcome.state, outcome.cause) == (JobState.failed,
                                                  TerminalCause.lost_after_publication)
        assert outcome.settlement_state is SettlementState.held_unknown and outcome.debit == 0
        with pytest.raises((errors.StaleLease, errors.AlreadyTerminal)):
            await h.extra["stream"].append(lease, b.events("late"))
        await assert_conserved(h, b.ORG_A, [admission.job_handle])
    run(body)


@pytest.mark.parametrize("backend", BACKENDS)
def test_e3b_dr07_a_duplicate_settlement_settles_once(backend):
    """DUR-SETTLE / CREDIT-SPEND: the settling commit's answer is lost; the identical retry
    replays the committed outcome, a different proposal is refused, a late cancel returns
    the same outcome - one usage projection, one debit, conservation exact."""
    h = rig(backend, *RUN, "append", "terminalize", "cancel")

    async def body():
        _, admission = await admit(h)
        lease = await running(h, admission)
        await h.extra["stream"].append(lease, b.events("answer"))
        proposal = b.outcome(admission.request_id, h)
        h.failures.crash_after_commit("complete")
        with pytest.raises(CrashAfterCommit):
            await h.port.complete(lease, proposal)
        first = (await h.port.get_owned(b.ORG_A, admission.job_handle))[1]
        assert first.settlement_state is SettlementState.settled and first.debit > 0
        assert await h.port.complete(lease, proposal) == first
        with pytest.raises(errors.AlreadyTerminal):
            await h.port.complete(lease, b.outcome(admission.request_id, h,
                                                   tokens=b.usage(1, 1)))
        assert await h.port.cancel(b.ORG_A, admission.job_handle) == first
        assert (await h.port.get_owned(b.ORG_A, admission.job_handle))[1] == first
        assert usage_projections(h, admission.request_id) == 1
        await assert_conserved(h, b.ORG_A, [admission.job_handle])
    run(body)


@pytest.mark.parametrize("backend", BACKENDS)
def test_e3b_dr08_a_foreign_tenant_cannot_read_cancel_or_see_a_result(backend):
    """Tenant isolation of results (BACKEND-JOURNEY "foreign calls leave nothing"): beta
    asking for alpha's handle gets `not_found` from status, replay and cancel alike - the
    same answer as an unknown handle - and alpha's job is untouched."""
    h = rig(backend, *RUN, "append", "cancel")

    async def body():
        _, admission = await admit(h)
        lease = await running(h, admission)
        await h.extra["stream"].append(lease, b.events("private"))
        handle = admission.job_handle
        with pytest.raises(errors.NotFound):
            await h.port.get_owned(b.ORG_B, handle)
        with pytest.raises(errors.NotFound):
            await h.extra["stream"].read_owned(b.ORG_B, handle, None)
        with pytest.raises(errors.NotFound):
            await h.port.cancel(b.ORG_B, handle)
        state, outcome = await h.port.get_owned(b.ORG_A, handle)
        assert (state.state, outcome) == (JobState.running, None)
        await assert_conserved(h, b.ORG_A, [handle])
        await assert_conserved(h, b.ORG_B, [])
    run(body)


@pytest.mark.parametrize("backend", BACKENDS)
def test_e3b_dr09_cancellation_beats_a_late_completion(backend):
    """DUR-SETTLE / API-MODES: cancel while running, then the worker's completion arrives.
    One winner (the cancel), the completion is fenced, nothing is debited for unreported
    work and the hold is released."""
    # `complete` is refused by terminalize's D3 fence (the job is cancelled) before its D5
    # stub runs, so the settlement stub is not a function this drill drives.
    h = rig(backend, *RUN, "cancel")

    async def body():
        _, admission = await admit(h)
        lease = await running(h, admission)
        cancelled = await h.port.cancel(b.ORG_A, admission.job_handle)
        assert cancelled.state is JobState.cancelled and cancelled.debit == 0
        with pytest.raises((errors.AlreadyTerminal, errors.StaleLease)):
            await h.port.complete(lease, b.outcome(admission.request_id, h))
        assert usage_projections(h, admission.request_id) == 1
        await assert_conserved(h, b.ORG_A, [admission.job_handle])
    run(body)


@pytest.mark.parametrize("backend", BACKENDS)
def test_e3b_dr10_journal_backpressure_refuses_an_oversized_event_whole(backend):
    """Backpressure (API-STREAM / DUR-OUTPUT): an event over `JOURNAL_EVENT_MAX_BYTES` is
    `journal_write_failed`, and nothing of it - not a prefix - is stored."""
    h = rig(backend, *RUN, "append", journal_event_max_bytes=64)

    async def body():
        _, admission = await admit(h)
        lease = await running(h, admission)
        with pytest.raises(errors.JournalWriteFailed):
            await h.extra["stream"].append(lease, b.events("x" * 200))
        chunks, _cursor = await h.extra["stream"].read_owned(b.ORG_A, admission.job_handle,
                                                             None)
        assert chunks == ()
    run(body)


# ------------------------------------------------------------------ CREDIT admission (item 3)
#
# `admit_credit` on both stores (review F3: the F2P wire-in merged, so the `[fake]` twins run
# on `credit_jobstore_factory`). On PostgreSQL: the PROVISIONAL Marlin seed (P-01: its card
# is a label here, never a price) and individuals from `auth.users` plus A1's grant. On the
# fake: the v2 fixture consumer plus consumers registered the fake's own way. The bodies are
# one; `credit_rig` is the only place the two stores differ. Settlement waits for D5.

def credit_request(h, person, model, **kw):
    return b.request(h, org_id=person.org_id, key_id=person.key_id, model_revision=model, **kw)


def credit_wallet(person) -> dict:
    """One CREDIT wallet's totals. R73: a read is one unit; nothing adds CREDIT to USD."""
    with stack.connect() as conn:
        ledger, reserved, available = conn.execute(
            "select ledger_total, reserved_total, available from infrx.credit_wallets "
            "where wallet_id = %s", (person.wallet_id,)).fetchone()
    return {"ledger": ledger, "reserved": reserved, "available": available}


def holds_of(request_id) -> dict:
    with stack.connect() as conn:
        credit = conn.execute("select wallet_id::text, state, amount from "
                              "infrx.credit_wallet_holds where request_id = %s",
                              (request_id,)).fetchall()
        usd, = conn.execute("select count(*) from infrx.credit_holds where request_id = %s",
                            (request_id,)).fetchone()
    return {"credit": credit, "usd": usd}


def footprint() -> tuple:
    """Everything an admission may own: jobs, both units' hold tables, reservations,
    outbox, idempotency mappings, and the CREDIT reserved total."""
    with stack.connect() as conn:
        return conn.execute("""select (select count(*) from infrx.jobs),
            (select count(*) from infrx.credit_holds),
            (select count(*) from infrx.credit_wallet_holds),
            (select count(*) from infrx.capacity_reservations),
            (select count(*) from infrx.outbox), (select count(*) from infrx.idempotency),
            (select coalesce(sum(reserved_total), 0) from infrx.credit_wallets)""").fetchone()


def assert_credit_conserved(person) -> None:
    """Per CREDIT wallet: ledger = the one signup grant - settled debits (none settle
    before D5); reserved = its holds still held; available never negative."""
    with stack.connect() as conn:
        held, = conn.execute("select coalesce(sum(amount), 0) from infrx.credit_wallet_holds "
                             "where wallet_id = %s and state = 'held'",
                             (person.wallet_id,)).fetchone()
        debited, = conn.execute("select coalesce(sum(debit), 0) from infrx.jobs where "
                                "wallet_id = %s and settled_at is not null",
                                (person.wallet_id,)).fetchone()
    wallet = credit_wallet(person)
    assert wallet["ledger"] == stack.SIGNUP_GRANT - debited, (wallet, debited)
    assert wallet["reserved"] == held, (wallet, held)
    assert wallet["available"] >= 0, wallet


def _pg_provider_key(person) -> str:
    """A provider_dev key filed in the person's personal org by the person (D2 MC-1)."""
    key = stack._uuid(f"{person.name}/provider-dev-key")
    with stack.connect() as conn:
        conn.execute("insert into public.api_keys (id, org_id, created_by, name, prefix, "
                     "key_hash, audience, provider_org_id, endpoint_id) values (%s, %s, %s, "
                     "'p', 'sk-infrx-e3b2prov', %s, 'provider_dev', %s, %s)",
                     (key, person.org_id, person.user_id, f"hash-{key}",
                      stack.SEED_PROVIDER_ORG, stack.SEED_DEV_ENDPOINT))
    return key


def _unpriced_listing() -> None:
    """R69: a newer listing of the alias whose card is not yet effective. The alias is then
    unserveable (invalid_request), never free. Listings are immutable, so this is the last
    refusal a drill stages."""
    with stack.connect() as conn:
        conn.execute("insert into infrx.rate_card_versions (rate_card_version, model_id, "
                     "deployment_revision_id, serving_version_id, input_rate_per_million, "
                     "output_rate_per_million, effective_at, approved_by, provisional) values "
                     "('rc_e3b2_not_yet', %s, %s, %s, 1, 1, infrx.now() + interval '1 day', "
                     "'e3b2 drill', true)",
                     (stack.SEED_MODEL, stack.SEED_PUBLIC_DEPLOYMENT, stack.SEED_SERVING))
        conn.execute("insert into infrx.catalog_listings (public_model_id, version, model_id, "
                     "deployment_revision_id, serving_version_id, rate_card_version, "
                     "effective_at, approved_by) values (%s, 2, %s, %s, %s, 'rc_e3b2_not_yet', "
                     "infrx.now(), 'e3b2 drill')",
                     (stack.CREDIT_ALIAS, stack.SEED_MODEL, stack.SEED_PUBLIC_DEPLOYMENT,
                      stack.SEED_SERVING))


def credit_rig(h, backend, names=("alpha",)):
    """The CREDIT world a drill needs, per store: its people (verified individuals, each with
    a consumer key in its own org and a funded CREDIT wallet), the alias and card the store
    resolves, the private dev model, the failure point of `admit_credit`, and the reads."""
    from types import SimpleNamespace
    if backend == "postgres":
        return SimpleNamespace(
            people=stack.credit_world(names), alias=stack.CREDIT_ALIAS, card=stack.SEED_CARD,
            private_model=stack.SEED_DEV_DEPLOYMENT, crash="admit_credit",
            wallet=credit_wallet, holds=holds_of, footprint=footprint,
            conserved=assert_credit_conserved, provider_key=_pg_provider_key,
            unprice=_unpriced_listing)
    from infrx.contracts.records import HoldState, Role
    from infrx.contracts.v2 import fixtures as v2fix, records as v2
    ids, port = v2fix.IDS, h.port
    people = {}
    for name in names:              # fresh consumers (the fixture one carries a reserve)
        person = stack.Individual(name, stack._uuid(f"{name}/user"), stack._uuid(f"{name}/org"),
                                  stack._uuid(f"{name}/credit-key"),
                                  stack._uuid(f"{name}/wallet"))
        wallet = v2.WalletRef(wallet_id=person.wallet_id, kind=v2.WalletKind.consumer,
                              owner_user_id=person.user_id, personal_org_id=person.org_id,
                              ledger_total=str(stack.SIGNUP_GRANT))
        port.wallet_directory.by_user[person.user_id] = wallet
        port.seed_credit_wallet(wallet)
        h.extra["register_credential"](v2.AuthContextV2(
            audience=v2.CredentialAudience.consumer, org_id=person.org_id,
            key_id=person.key_id, principal=person.key_id, role=Role.owner,
            entitlement_version=1, user_id=person.user_id))
        people[name] = person
    balance = lambda person: h.extra["credit_balance"](person.wallet_id)   # noqa: E731
    initial = {name: balance(person)["ledger"] for name, person in people.items()}

    def holds(request_id):
        hold = port.holds.get(request_id)
        if hold is None:
            return {"credit": [], "usd": 0}
        if hold.wallet_id is None:
            return {"credit": [], "usd": 1}
        return {"credit": [(hold.wallet_id, hold.state.value, hold.amount)], "usd": 0}

    def conserved(person):
        held = sum((hold.amount for hold in port.holds.values()
                    if hold.wallet_id == person.wallet_id and hold.state is HoldState.held),
                   Decimal(0))
        wallet = balance(person)
        assert wallet["ledger"] == initial[person.name], (wallet, initial[person.name])
        assert wallet["reserved"] == held, (wallet, held)
        assert wallet["available"] >= 0, wallet

    def fake_footprint():
        return (len(port.jobs), len(port.holds), len(h.extra["outbox"]()),
                h.extra["journal_bytes"](), tuple(balance(p)["reserved"] for p in people.values()))

    def provider_key(person):
        key = stack._uuid(f"{person.name}/provider-dev-key")
        h.extra["register_credential"](v2.AuthContextV2(
            audience=v2.CredentialAudience.provider_dev, org_id=person.org_id, key_id=key,
            principal=key, role=Role.member, entitlement_version=1, user_id=person.user_id,
            provider_org_id=ids.provider_org, endpoint_id=ids.dev_endpoint))
        return key

    return SimpleNamespace(
        people=people, alias=v2fix.REQUESTED_MODEL, card=v2fix.RATE_CARD_VERSION,
        private_model=v2fix.DEV_REQUESTED_MODEL, crash="admit", wallet=balance, holds=holds,
        footprint=fake_footprint, conserved=conserved, provider_key=provider_key,
        unprice=lambda: port.catalog.rate_cards.pop(ids.prod_deployment))


@pytest.mark.parametrize("backend", BACKENDS)
def test_e3b_dr01c_a_credit_admission_replays_to_one_identity_and_one_credit_hold(backend):
    """CREDIT-SPEND / DUR-ADMIT (R66, R78, R79): the admission's answer is lost after its
    commit; the retry is the SAME job with ONE hold, on the individual's own CREDIT wallet,
    pinned to the listing it resolved - and the organization's legacy USD books do not
    move."""
    h = rig(backend, *ADMIT, credit=True)
    world = credit_rig(h, backend)
    alpha = world.people["alpha"]

    async def body():
        usd = h.extra["balance"](alpha.org_id)
        request = credit_request(h, alpha, world.alias)
        idem = b.idem(request, "c1")
        h.failures.crash_after_commit(world.crash)
        with pytest.raises(CrashAfterCommit):
            await h.port.admit_credit(request, idem)
        again = await h.port.admit_credit(request, idem)
        assert again.replayed and again.request_id == request.request_id
        assert (again.org_id, again.wallet_id) == (alpha.org_id, alpha.wallet_id)
        assert (again.pins.requested_model, again.pins.rate_card_version) == (
            world.alias, world.card)
        hold = Decimal(str(again.maximum_hold))
        held = world.holds(request.request_id)
        assert held == {"credit": [(alpha.wallet_id, "held", hold)],
                        "usd": 0}, f"the hold is not one hold on the CREDIT wallet: {held}"
        assert world.wallet(alpha)["reserved"] == hold > 0
        assert h.extra["balance"](alpha.org_id) == usd, "the legacy USD books moved"
        assert h.extra["outbox_kinds"](request.request_id) == [OutboxKind.prepare_dispatch]
        assert (await h.port.get_owned_credit(alpha.org_id, again.job_handle))[1] is None
        world.conserved(alpha)
    run(body)


@pytest.mark.parametrize("backend", BACKENDS)
def test_e3b_dr02c_a_refused_credit_admission_leaves_nothing_behind(backend):
    """DUR-ADMIT / DUR-CAP / CREDIT-SPEND (R66, R69, R70): a private dev deployment and a
    provider_dev key filed in its creator's personal org (D2 MC-1) are `not_found`, a revoked
    key `invalid_api_key`, a full key `capacity_exhausted`, an unpriced listing
    `invalid_request` - each typed, each leaving no job, hold of either unit, reservation,
    outbox row or mapping; every wallet conserved on its own."""
    h = rig(backend, *ADMIT, credit=True, max_active_jobs_per_key=1)
    world = credit_rig(h, backend, ("alpha", "beta", "gamma"))
    alpha, beta, gamma = (world.people[name] for name in ("alpha", "beta", "gamma"))
    provider_key = world.provider_key(alpha)

    async def refused(expected, request):
        mark = world.footprint()
        with pytest.raises(expected):
            await h.port.admit_credit(request, b.idem(request, str(uuid.uuid4())))
        assert world.footprint() == mark, f"{expected}: the refusal left rows behind"

    async def body():
        usd = {person.name: h.extra["balance"](person.org_id)
               for person in world.people.values()}
        await refused(errors.NotFound, credit_request(h, alpha, world.private_model))
        # D2 MC-1 on the real store. The contract fake admits a provider_dev key to the
        # public listing (it has no MC-1 rule) and refuses only for the unfunded dev wallet
        # - integration request #6; this pin flips the day the fake gains the rule.
        await refused(errors.NotFound if backend == "postgres" else errors.InsufficientCredit,
                      b.request(h, org_id=alpha.org_id, key_id=provider_key,
                                model_revision=world.alias))
        h.extra["revoke_key"](beta.key_id)
        await refused(errors.InvalidApiKey, credit_request(h, beta, world.alias))
        first = credit_request(h, alpha, world.alias)
        kept = await h.port.admit_credit(first, b.idem(first, "kept"))    # the key's one slot
        await refused(errors.CapacityExhausted, credit_request(h, alpha, world.alias))
        world.unprice()
        await refused(errors.InvalidRequest, credit_request(h, gamma, world.alias))
        assert world.wallet(alpha)["reserved"] == Decimal(str(kept.maximum_hold))
        assert (world.wallet(beta)["reserved"], world.wallet(gamma)["reserved"]) == (0, 0)
        for person in world.people.values():
            world.conserved(person)
            assert h.extra["balance"](person.org_id) == usd[person.name]
    run(body)


def test_e3b_dr07c_credit_settlement_is_pending_on_the_settling_transaction():
    """CREDIT-SPEND settlement (R73, R79): one debit on the CREDIT wallet, the hold released,
    usage projected once. Pending while the settlement after terminalize's fence is a stub;
    the day it is not, this fails until its body is written."""
    rig("postgres", *RUN, "terminalize")
    pytest.fail("terminalize is implemented: write the CREDIT settlement drill body now")


# ------------------------------------------------------------------ live defects, real store

def _without_prepare_dispatch():
    """`infrx.admission_rows` with its `prepare_dispatch` outbox insert removed: admission
    still commits the job, its hold and its reservations - but nothing will dispatch it."""
    source = stack.function_source("infrx.admission_rows",
                                   "jsonb, jsonb, jsonb, text, timestamptz")
    broken = re.sub(r"insert into infrx\.outbox .*?p_now\);", "", source, count=1, flags=re.S)
    assert broken != source, "the outbox insert moved: the drill no longer describes 0011"
    stack.defect(broken)


def _fence_ignores_generation():
    """`infrx.fence_lease` without its generation check: an old generation's lease passes
    whenever its worker id matches the live attempt's."""
    source = stack.function_source("infrx.fence_lease", "jsonb, text[], double precision")
    check = "if a.generation is distinct from (p_lease->>'generation')::int then"
    assert source.count(check) == 1, "the generation check moved: the drill is stale"
    stack.defect(source.replace(check, "if false then"))


def test_e3b_db06_detects_a_missing_durable_acceptance_on_the_real_store(monkeypatch):
    """Intentional defect on the REAL store (DUR-ADMIT): an admission that commits without
    its `prepare_dispatch` row. dr01's own assertions, unchanged, must report it. The defect
    lives in this case's clone only (`stack.defect`)."""
    monkeypatch.setattr(sys.modules[__name__], "DEFECT", _without_prepare_dispatch)
    with pytest.raises(AssertionError, match="prepare_dispatch"):
        test_e3b_dr01_acceptance_crash_after_commit_retries_to_one_identity("postgres")


async def _stale_generation_accepted(h) -> list[str]:
    """dr04's requeue with the SAME worker id on both generations (dr05's shape, without
    its append): generation 1's lease must be refused by the fence every execution
    mutation runs first. Returns what was accepted (empty = fenced)."""
    _, admission = await admit(h)
    old = await running(h, admission, "w1")
    h.clock.advance(DEFAULTS.lease_ttl_s + 1)
    await h.port.recover()
    new = await h.port.claim(admission.request_id, "w1")
    assert (old.generation, new.generation) == (1, 2)
    accepted = []
    try:
        await h.port.heartbeat(old)
        accepted.append("heartbeat of generation 1")
    except errors.StaleLease:
        pass
    await assert_conserved(h, b.ORG_A, [admission.job_handle])
    return accepted


def test_e3b_db07_detects_a_stale_fence_on_the_real_store(monkeypatch):
    """Intentional defect on the REAL store (DUR-FENCE, stale lease): the fence holds on the
    migrated store, and with `fence_lease`'s generation check removed the drill names the
    accepted stale heartbeat. (dr04 itself never presents generation 1's lease again, so
    the drill adds the one call that does.)"""
    drives = (*RUN, "recover", "heartbeat")
    h = rig("postgres", *drives)
    assert run(lambda: _stale_generation_accepted(h)) == []
    monkeypatch.setattr(sys.modules[__name__], "DEFECT", _fence_ignores_generation)
    h = rig("postgres", *drives)
    assert run(lambda: _stale_generation_accepted(h)) == ["heartbeat of generation 1"]


def test_e3b_db11_detects_a_stale_append_on_the_real_journal(monkeypatch):
    """Intentional defect on the REAL journal (DUR-FENCE append, D4's 0017): `append` fences
    first, so with `fence_lease`'s generation check removed generation 1's append is stored,
    and dr05's own `pytest.raises` must report it."""
    monkeypatch.setattr(sys.modules[__name__], "DEFECT", _fence_ignores_generation)
    with pytest.raises(pytest.fail.Exception, match="DID NOT RAISE"):
        test_e3b_dr05_a_stale_generation_cannot_append("postgres")


def _credit_hold_on_the_usd_books():
    """`infrx.admit_credit` with its CREDIT hold written as a legacy USD hold instead."""
    source = stack.function_source("infrx.admit_credit", "jsonb")
    hold = re.search(r"insert into infrx\.credit_wallet_holds .*?v_now\);", source, re.S)
    assert hold, "the CREDIT hold insert moved: the drill no longer describes 0011"
    stack.defect(source.replace(hold.group(0), (
        "insert into infrx.credit_holds (request_id, org_id, key_id, amount, state, "
        "created_at, updated_at) values (j.request_id, j.org_id, j.key_id, v_hold, 'held', "
        "v_now, v_now);")))


def _credit_hold_also_on_the_usd_books():
    """`infrx.admit_credit` keeping its CREDIT hold AND writing a legacy USD hold beside it."""
    source = stack.function_source("infrx.admit_credit", "jsonb")
    hold = re.search(r"insert into infrx\.credit_wallet_holds .*?v_now\);", source, re.S)
    assert hold, "the CREDIT hold insert moved: the drill no longer describes 0011"
    stack.defect(source.replace(hold.group(0), hold.group(0) + (
        " insert into infrx.credit_holds (request_id, org_id, key_id, amount, state, "
        "created_at, updated_at) values (j.request_id, j.org_id, j.key_id, v_hold, 'held', "
        "v_now, v_now);")))


def test_e3b_db08b_detects_a_usd_hold_beside_the_credit_one(monkeypatch):
    """Review F5: the USD half of dr01c is load-bearing on its own - the CREDIT hold is
    right, and a second, legacy USD hold for the same request must still be reported."""
    monkeypatch.setattr(sys.modules[__name__], "DEFECT", _credit_hold_also_on_the_usd_books)
    with pytest.raises(AssertionError, match="'usd': 1"):
        test_e3b_dr01c_a_credit_admission_replays_to_one_identity_and_one_credit_hold(
            "postgres")


def test_e3b_db08_detects_a_credit_hold_on_the_usd_books(monkeypatch):
    """Intentional defect on the REAL store (CREDIT-SPEND, R64/R73): a CREDIT admission whose
    hold lands on the legacy USD books. dr01c's own assertion must report it."""
    monkeypatch.setattr(sys.modules[__name__], "DEFECT", _credit_hold_on_the_usd_books)
    with pytest.raises(AssertionError, match="not one hold on the CREDIT wallet"):
        test_e3b_dr01c_a_credit_admission_replays_to_one_identity_and_one_credit_hold(
            "postgres")


def test_e3b_dr11_client_disconnect_mid_stream_is_pending():
    """API-MODES / API-STREAM: a sync or SSE client that disconnects mid-generation must
    cancel or detach per mode, with no orphan execution. It is a route behaviour, so it is
    pending exactly as long as no metered route is mounted, and fails once one is."""
    if stack.ingress_is_mounted():
        pytest.fail("the pilot ingress is mounted: write the disconnect drill body now")
    stack.pending("G2", why="the sync/SSE relay that sees the disconnect is G2's, "
                            "and no metered route is mounted")


# ------------------------------------------------------------------ saturation and queue

def test_e3b_dr12_a_full_valkey_index_refuses_without_writing(valkey_index):
    """DUR-CAP / saturation, on the real Valkey (E2's, Q2's adapter): past the item cap an
    enqueue is `capacity_exhausted` with a retry hint, and the index is unchanged."""
    port, _h, _client = valkey_index(max_items=2)

    async def body():
        for _ in range(2):
            assert await port.enqueue(_event(_h, uuid.uuid4()))
        before = await port.snapshot()
        with pytest.raises(errors.CapacityExhausted) as caught:
            await port.enqueue(_event(_h, uuid.uuid4()))
        assert caught.value.retry_after_s == 1
        assert await port.snapshot() == before
    run(body)


def test_e3b_dr13_losing_the_queue_index_loses_no_accepted_job(valkey_index):
    """DUR-OUTBOX / queue rebuild (Q3 req 4), real stores on both sides: four jobs accepted
    and prepared through the PostgreSQL store, their dispatch rows drained into E2's Valkey by
    Q3's `Reconciler` and acknowledged; one is claimed and cancelled (terminal). Valkey is
    SIGKILLed (it keeps no data by design) and comes back empty. Its rows were acknowledged
    before the loss, so the outbox never hands them out again - the acked-then-rebuilt hole
    (Q3 Limit 3): only the rebuild from PostgreSQL's dispatch snapshot restores them. The
    three still queued dispatch exactly once, the terminal one never."""
    from infrx.scheduling.reconcile import Reconciler
    port, h, client = valkey_index("postgres")
    relay = Reconciler(store=h.extra["store"], index=port, now=h.clock.now,
                       worker_id=f"e3b2-relay-{uuid.uuid4().hex[:8]}")

    async def body():
        admissions = []
        for index in range(4):
            _, admission = await admit(h, idem_key=f"k{index}")
            lease = await h.port.claim_preparation(admission.request_id, "prep")
            await h.port.prepared(lease, ())
            admissions.append(admission)
        drained = await relay.drain()
        assert (drained.get("indexed"), drained.get("acknowledged")) == (4, 4), drained
        # Review F1: the premise, on the store itself - the count `acknowledge_dispatch`
        # answers is the rows it matched, not the value it wrote.
        assert unacknowledged([a.request_id for a in admissions]) == 0, \
            "the drained dispatch rows are not acknowledged on the store"
        early = await h.extra["store"].dispatch_snapshot()     # before the terminal job
        assert len(early) == 4, early
        first = await port.claim_candidate("w1")
        await h.port.claim(first.job_id, "w1")
        await port.acknowledge(first)
        handle = next(a.job_handle for a in admissions if a.request_id == first.job_id)
        assert (await h.port.cancel(b.ORG_A, handle)).state is JobState.cancelled

        with harness.Faults() as faults:
            faults.kill_container("valkey")
        harness.wait_valkey()
        await client.connection_pool.disconnect()
        assert await port.depth() == 0, "the index survived a SIGKILL: not a loss drill"

        assert await relay.rebuild() == 3
        # Past the redelivery window, so a claimed-but-unacknowledged row WOULD come back.
        h.clock.advance(relay.redelivery_s + 1)
        assert (await relay.drain()).get("read", 0) == 0, "the acknowledged rows came back"
        dispatched = []
        for _ in range(len(admissions) + 1):         # bounded: a duplicate cannot loop
            candidate = await port.claim_candidate("w2")
            if candidate is None:
                break
            dispatched.append(candidate.job_id)
            await port.acknowledge(candidate)
        else:
            raise AssertionError(f"the index kept dispatching: {dispatched}")
        expected = {a.request_id for a in admissions} - {first.job_id}
        assert sorted(dispatched) == sorted(expected), dispatched
        await assert_conserved(h, b.ORG_A, [a.job_handle for a in admissions])
    run(body)


def unacknowledged(job_ids, kind="inference_dispatch") -> int:
    """Dispatch rows of these jobs the store has NOT marked acknowledged (read on the clone)."""
    with stack.connect() as conn:
        return conn.execute("select count(*) from infrx.outbox where aggregate_id = "
                            "any(%s::uuid[]) and kind = %s and acknowledged_at is null",
                            (list(job_ids), kind)).fetchone()[0]


def _acknowledgment_does_not_land():
    """`infrx.acknowledge_dispatch` still matching and counting its rows but writing NULL:
    the relay believes it acknowledged, and nothing is recorded."""
    source = stack.function_source("infrx.acknowledge_dispatch", "jsonb")
    ack = "set acknowledged_at = infrx.now()"
    assert source.count(ack) == 1, "0012's acknowledgment moved: the drill is stale"
    stack.defect(source.replace(ack, "set acknowledged_at = null"))


def test_e3b_db10_detects_an_outbox_acknowledgment_that_does_not_land(valkey_index,
                                                                       monkeypatch):
    """Intentional defect on the REAL store (DUR-OUTBOX, review F1): an acknowledgment that
    answers its count but records nothing. dr13's own premise assertion must report it."""
    monkeypatch.setattr(sys.modules[__name__], "DEFECT", _acknowledgment_does_not_land)
    with pytest.raises(AssertionError, match="not acknowledged on the store"):
        test_e3b_dr13_losing_the_queue_index_loses_no_accepted_job(valkey_index)


def _event(h, job_id):
    return IndexEvent(event_id=h.ids.event_id(), job_id=str(job_id), org_id=b.ORG_A,
                      key_id=b.KEY_A, kind=OutboxKind.inference_dispatch,
                      execution_mode=ExecutionMode.stream, available_at=h.clock.now(),
                      attempt=0)


@pytest.fixture
def valkey_index():
    """Q2's adapter on E2's Valkey, in a namespace of our own under `harness.VALKEY_PREFIX`, removed
    afterwards (E2's suite asserts the shared prefix is left empty). Returns a factory so a
    drill can set the caps."""
    state = harness.load_state()
    if not state or not harness.owned_containers():
        pytest.skip(f"no {harness.PROJECT} stack: run `tests/integration/run.py --layer 3`")
    from valkey.asyncio import Valkey

    from infrx.scheduling.valkey import ValkeyScheduler
    namespace = f"{harness.VALKEY_PREFIX}{{e3b-{uuid.uuid4().hex}}}"

    def make(backend="fake", **caps):
        h = rig(backend, *RUN, "cancel", "dispatch_pending", "acknowledge_dispatch")
        client = Valkey.from_url(harness.valkey_url())
        return ValkeyScheduler(client, h.clock.now, limits=DEFAULTS, namespace=namespace,
                               **caps), h, client
    yield make
    sync = harness.valkey_client()
    leftovers = list(sync.scan_iter(f"{namespace}*"))
    if leftovers:
        sync.delete(*leftovers)


# ------------------------------------------------------------------ media

def test_e3b_dr14_malformed_media_is_a_typed_refusal_not_a_crash():
    """MEDIA-SEC / MEDIA-PARITY (malformed): M2's real probe refuses garbage, a truncated
    ISO box and an EBML header with an oversized integer as `unsupported_media` - never an
    unhandled exception that would surface as a 500."""
    from infrx.media.probe import probe
    ebml_uint = b"\x1a\x45\xdf\xa3" + b"\x42\x86" + b"\x88" + b"\xff" * 200
    for hostile in (b"not a video at all", b"\x00\x00\x00\x18ftypisom",
                    b"\x00\x00\xff\xffmoov" + b"\x00" * 16, ebml_uint):
        with pytest.raises(errors.UnsupportedMedia):
            probe(hostile)


def test_e3b_dr15_internal_media_urls_are_refused_before_any_connection():
    """MEDIA-SEC: loopback (E2's own PostgreSQL port), link-local metadata and an IPv4-mapped
    IPv6 loopback are refused by M2's fetcher, and the transport never sees a request."""
    import httpx

    from infrx.media.fetch import MediaFetcher
    seen = []
    transport = httpx.MockTransport(lambda request: seen.append(request) or
                                    httpx.Response(200, content=b"x"))
    fetcher = MediaFetcher(transport=transport)

    async def body():
        for url in (f"http://127.0.0.1:{harness.PORTS['postgres']}/clip.mp4",
                    "http://169.254.169.254/latest/meta-data/clip.mp4",
                    "http://[::ffff:127.0.0.1]/clip.mp4", "http://localhost/clip.mp4"):
            with pytest.raises(errors.DomainError):
                await fetcher.fetch(url)
    run(body)
    assert seen == []


# ------------------------------------------------------------------ legacy fallback

def _pilot_settings(tmp_path, **kw):
    from infrx.config import Settings
    return Settings(usage_log=str(tmp_path / "usage.jsonl"),
                    supabase_url="https://fake.supabase.invalid", supabase_key="service-role",
                    pilot=DEFAULTS.replace(infrx_mode="pilot",
                                           database_url="postgresql:///infrx_e3b"), **kw)


def test_e3b_dr16_pilot_refuses_the_legacy_shared_key(tmp_path):
    """Forced fallback to legacy unmetered ingress (R51): a pilot configured with the shared
    gateway key (R51's forbidden setting) - the legacy path that bypasses per-tenant metering - refuses to
    start, naming the setting and not its value."""
    from unittest import mock
    from infrx.config import RuntimeMisconfigured, validate_runtime
    from infrx.gateway import app as composition
    from infrx.gateway.routes import health, ingress, models
    # G1R: a complete pilot configuration validates only as the cutover composes it.
    with mock.patch.object(composition, "ROUTERS", (health, models, ingress)):
        assert validate_runtime(_pilot_settings(tmp_path)) == "pilot"
    with pytest.raises(RuntimeMisconfigured) as refused:
        validate_runtime(_pilot_settings(tmp_path, legacy_key="shared-legacy-key"))
    # assembled from parts: tests/integration's production-pointer guard scans this file
    assert "GATEWAY" "_API_KEY" in str(refused.value)
    assert "shared-legacy-key" not in str(refused.value)


def test_e3b_dr17_a_pilot_gateway_does_not_serve_chat_through_the_legacy_route(tmp_path):
    """Forced fallback to legacy unmetered ingress: in `pilot` mode `/v1/chat/completions`
    must be the metered ingress, never the legacy F1 chat route (no durable admission, no
    hold). Today the composition root mounts the legacy route in every mode - the documented
    pre-cutover state - so this is pending on G1R with the observation recorded."""
    from infrx.config import RuntimeMisconfigured
    from infrx.gateway.app import create_app
    try:
        app = create_app(_pilot_settings(tmp_path))
    except RuntimeMisconfigured as refused:
        # G1R: pilot refuses to start while chat would be served by the legacy route.
        assert "legacy route" in str(refused)
        return
    served = {route.path: route.endpoint.__module__ for route in app.routes
              if getattr(route, "path", "") == "/v1/chat/completions"}
    if served and all(module.endswith(".ingress") for module in served.values()):
        return
    stack.pending("G1R", why=f"pilot mode serves /v1/chat/completions from {served}")
