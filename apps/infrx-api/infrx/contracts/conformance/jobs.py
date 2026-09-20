"""JobStore and StreamStore conformance: DUR-ADMIT, DUR-CAP, DUR-FENCE,
DUR-OUTPUT, DUR-SETTLE, DUR-OUTBOX and the stream half of API-STREAM.

Every case is an `async def` taking the factory, named for the oracle it serves.
Concurrency uses `asyncio.gather` and the injected clock: no sleeps, no network.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace

from .. import errors, money
from ..limits import DEFAULTS
from ..records import (ChunkEventType, Cursor, ExecutionMode, JobState, OutboxKind,
                       SettlementState, TerminalCause)
from . import builders as b

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


async def _admit(harness, *, org_id=b.ORG_A, key_id=b.KEY_A, key="idem-1", grant="25.00",
                 mode=ExecutionMode.stream, **kw):
    """Fund the wallet, build a request and admit it. Returns (request, admission)."""
    if grant is not None:
        harness.extra["grant"](org_id, grant)
    request = b.request(harness, org_id=org_id, key_id=key_id, mode=mode, **kw)
    admission = await harness.port.admit(request, b.idem(request, key), (), b.hold_for(request))
    return request, admission


async def _running(harness, jobs=None, **kw):
    """Admit, prepare, claim: a job with a live lease. Returns (request, admission, lease)."""
    port = jobs or harness.port
    if jobs is not None:
        harness = replace(harness, port=jobs)
    request, admission = await _admit(harness, **kw)
    await port.prepared(admission.job_handle, ())
    lease = await port.claim(request.request_id, "worker-a")
    return request, admission, lease


# --------------------------------------------------------------------------
# DUR-ADMIT
# --------------------------------------------------------------------------
async def dur_admit__acceptance_creates_job_hold_reservations_and_outbox(factory):
    """DUR-ADMIT: no acceptance without a job, a hold and a dispatch outbox event."""
    harness = factory()
    request, admission = await _admit(harness)
    assert admission.state is JobState.preparing and admission.replayed is False
    assert admission.job_handle.startswith("job_")
    assert admission.job_handle not in request.request_id      # opaque, not derived
    assert admission.maximum_hold == b.hold_for(request, admission.price_snapshot)
    kinds = {reservation.kind for reservation in admission.reservations}
    assert {kind.value for kind in kinds} == {"preparation", "inference", "journal_bytes"}
    assert OutboxKind.prepare_dispatch in [event.kind for event in admission.outbox]
    stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert stored.request_id == request.request_id and outcome is None
    try:
        await harness.port.get_owned(b.ORG_B, admission.job_handle)
    except errors.NotFound:
        pass
    else:
        raise AssertionError("another org could read the job by handle")


async def dur_admit__idempotent_replay_returns_the_same_identity(factory):
    """DUR-ADMIT: the same key and payload return the original acceptance, once."""
    harness = factory()
    request, first = await _admit(harness)
    again = await harness.port.admit(request, b.idem(request, "idem-1"), (),
                                     b.hold_for(request))
    assert (again.request_id, again.job_handle) == (first.request_id, first.job_handle)
    assert again.replayed is True
    wallet = harness.extra["balance"](request.org_id)
    assert wallet["reserved"] == first.maximum_hold        # one hold, not two
    assert len(harness.extra["active_jobs"]()) == 1


async def dur_admit__a_request_uuid_is_admitted_once(factory):
    """DUR-ADMIT: the request UUID is the job key (06: "no second active hold per
    request"), so re-admitting the same request never reserves twice. Without an
    idempotency key there is nothing to replay, so it is a typed conflict."""
    harness = factory()
    request, first = await _admit(harness, key=None)
    reserved = harness.extra["balance"](request.org_id)["reserved"]
    for key in (None, "a-late-key"):
        try:
            await harness.port.admit(request, b.idem(request, key), (), b.hold_for(request))
        except errors.DomainError as exc:
            assert errors.http_status(exc.code) == 409, exc.code
        else:
            raise AssertionError(f"the same request was admitted twice (key={key!r})")
    assert harness.extra["balance"](request.org_id)["reserved"] == reserved
    assert len(harness.extra["active_jobs"]()) == 1


async def dur_admit__changed_payload_with_the_same_key_is_a_conflict(factory):
    """DUR-ADMIT: reusing a key with a different canonical payload is 409."""
    harness = factory()
    request, _ = await _admit(harness)
    try:
        await harness.port.admit(request, b.idem(request, "idem-1", payload="different"), (),
                                 b.hold_for(request))
    except errors.IdempotencyConflict as exc:
        assert errors.http_status(exc.code) == 409
    else:
        raise AssertionError("a changed payload was admitted under the same key")


async def dur_admit__crash_after_commit_then_retry_does_not_double_reserve(factory):
    """DUR-ADMIT: killed after commit, before the acknowledgment; the retry with
    the same key returns the committed acceptance and reserves nothing more."""
    harness = factory()
    if harness.failures is None:
        return
    harness.failures.crash_after_commit("admit")
    harness.extra["grant"](b.ORG_A, "25.00")
    request = b.request(harness)
    try:
        await harness.port.admit(request, b.idem(request, "idem-1"), (), b.hold_for(request))
    except Exception as exc:                       # the answer was lost, not the commit
        assert type(exc).__name__ == "CrashAfterCommit", exc
    else:
        raise AssertionError("crash_after_commit did not fire")
    retried = await harness.port.admit(request, b.idem(request, "idem-1"), (),
                                       b.hold_for(request))
    assert retried.replayed is True
    assert harness.extra["balance"](request.org_id)["reserved"] == retried.maximum_hold
    assert len(harness.extra["active_jobs"]()) == 1


async def dur_admit__expired_mapping_is_explicit_never_a_second_billable_job(factory):
    """DUR-ADMIT: an expired idempotency mapping answers 410, it does not re-admit."""
    harness = factory()
    request, admission = await _admit(harness)
    await _settle(harness, request, admission)
    harness.clock.advance(DEFAULTS.idempotency_ttl_s + 1)
    try:
        await harness.port.admit(request, b.idem(request, "idem-1"), (), b.hold_for(request))
    except errors.IdempotencyExpired as exc:
        assert errors.http_status(exc.code) == 410
    else:
        raise AssertionError("an expired key silently admitted a new job")
    assert len(harness.extra["active_jobs"]()) == 0


async def dur_admit__tombstone_is_retained_after_the_terminal_state(factory):
    """DUR-ADMIT: within 24h of terminal, the key still replays the same identity."""
    harness = factory()
    request, admission = await _admit(harness)
    await _settle(harness, request, admission)
    harness.clock.advance(DEFAULTS.idempotency_ttl_s - 60)
    replay = await harness.port.admit(request, b.idem(request, "idem-1"), (),
                                      b.hold_for(request))
    assert replay.job_handle == admission.job_handle and replay.replayed is True
    assert replay.state in {JobState.succeeded, JobState.failed, JobState.cancelled,
                            JobState.expired}


async def dur_admit__revocation_and_suspension_are_rechecked_in_the_transaction(factory):
    """DUR-ADMIT: a cached identity never bypasses revocation, suspension or
    entitlement (01's identity section, r1 R10). All three are rechecked in the
    admitting transaction, so a key revoked or a model withdrawn a millisecond ago
    cannot buy one more billable job."""
    harness = factory()
    revoke, suspend = harness.extra.get("revoke_key"), harness.extra.get("suspend_org")
    if revoke is None or suspend is None:
        return                                     # optional hook; adapter may skip
    harness.extra["grant"](b.ORG_A, "25.00")
    revoke(b.KEY_A)
    request = b.request(harness)
    try:
        await harness.port.admit(request, b.idem(request, "k-revoked"), (), b.hold_for(request))
    except errors.InvalidApiKey:
        pass
    else:
        raise AssertionError("a revoked key was admitted")
    harness.extra["unrevoke_key"](b.KEY_A)
    suspend(b.ORG_A)
    request = b.request(harness)
    try:
        await harness.port.admit(request, b.idem(request, "k-suspended"), (), b.hold_for(request))
    except errors.OrgSuspended:
        pass
    else:
        raise AssertionError("a suspended org was admitted")
    if harness.extra.get("unentitle") is None:
        return                                     # optional hook; adapter may skip
    harness = factory()                            # a fresh store, with its own hooks
    harness.extra["grant"](b.ORG_A, "25.00")
    request = b.request(harness)
    harness.extra["unentitle"](b.ORG_A, request.model_revision)
    try:
        await harness.port.admit(request, b.idem(request, "k-unentitled"), (),
                                 b.hold_for(request))
    except errors.ModelNotEntitled as exc:
        assert errors.http_status(exc.code) == 403
    else:
        raise AssertionError("an unentitled org was admitted")
    assert harness.extra["balance"](b.ORG_A)["reserved"] == 0
    assert len(harness.extra["active_jobs"]()) == 0
    harness.extra["entitle"](b.ORG_A, request.model_revision)
    admitted = await harness.port.admit(request, b.idem(request, "k-entitled"), (),
                                        b.hold_for(request))
    assert admitted.state is JobState.preparing


async def dur_admit__an_idempotency_scope_belongs_to_the_requests_own_org(factory):
    """DUR-ADMIT / r1 R10: two tenant-bearing arguments must name the same org.
    A request from ORG_B carrying ORG_A's idempotency scope would otherwise replay
    ORG_A's admission: its job handle, its price snapshot and its hold."""
    harness = factory()
    request_a, admission_a = await _admit(harness, key="shared-key")
    harness.extra["grant"](b.ORG_B, "25.00")
    request_b = b.request(harness, org_id=b.ORG_B, key_id=b.KEY_B)
    foreign = b.idem(request_a, "shared-key").model_copy(update={"org_id": b.ORG_A})
    try:
        await harness.port.admit(request_b, foreign, (), b.hold_for(request_b))
    except errors.DomainError as exc:
        assert errors.http_status(exc.code) in (403, 404), exc.code
    else:
        raise AssertionError("a request replayed another org's admission")
    stored, _ = await harness.port.get_owned(b.ORG_A, admission_a.job_handle)
    assert stored.request_id == request_a.request_id
    assert len(harness.extra["active_jobs"](org_id=b.ORG_B)) == 0
    assert harness.extra["balance"](b.ORG_B)["reserved"] == 0
    # and a request may not carry another tenant's media either
    foreign_media = b.request(harness, refs=(b.media(b.ORG_B),))
    try:
        await harness.port.admit(foreign_media, b.idem(foreign_media, "foreign-media"), (),
                                 b.hold_for(foreign_media))
    except errors.DomainError as exc:
        assert errors.http_status(exc.code) in (403, 404), exc.code
    else:
        raise AssertionError("a request carrying another org's media was admitted")


async def dur_admit__a_refused_admission_reserves_nothing(factory):
    """DUR-ADMIT: nothing is reserved before everything is checked. An unpriced
    model fails closed (01: "missing model/rates reject admission"), and the refused
    attempt leaves no journal reservation, no hold and no job behind - otherwise a
    client retrying an invalid request drains the journal budget. Bad monetary input
    is refused the same way, as a typed domain error rather than a ValueError."""
    harness = factory()
    harness.extra["grant"](b.ORG_A, "25.00")
    journal_bytes = harness.extra.get("journal_bytes")
    unpriced = b.request(harness).model_copy(update={"parameters": {}})
    for _ in range(3):
        try:
            await harness.port.admit(unpriced, b.idem(unpriced, "unpriced"), (), money.ZERO)
        except errors.DomainError as exc:
            assert errors.http_status(exc.code) in (400, 403, 404), exc.code
        else:
            raise AssertionError("an unpriced model was admitted")
    assert len(harness.extra["active_jobs"]()) == 0
    assert harness.extra["balance"](b.ORG_A)["reserved"] == 0
    if journal_bytes is not None:
        assert journal_bytes() == 0, "a refused admission leaked a journal reservation"
    for bad in ("1e5", "NaN", "0.000000001", "1000000000000.00", 0.5):
        request = b.request(harness)
        try:
            await harness.port.admit(request, b.idem(request, f"bad-{bad!r}"), (), bad)
        except errors.DomainError as exc:
            assert errors.http_status(exc.code) in (400, 402), exc.code
        else:
            raise AssertionError(f"hold {bad!r} was accepted")
    assert len(harness.extra["active_jobs"]()) == 0
    assert harness.extra["balance"](b.ORG_A)["reserved"] == 0


# --------------------------------------------------------------------------
# DUR-CAP
# --------------------------------------------------------------------------
async def dur_cap__total_org_and_key_limits_reject_with_retry_guidance(factory):
    """DUR-CAP: capacity is refused with 429 and Retry-After, never oversubscribed."""
    harness = factory(limits=DEFAULTS.replace(max_active_jobs_per_key=2, max_active_jobs=3,
                                              max_active_jobs_per_org=3))
    harness.extra["grant"](b.ORG_A, "100.00")
    for n in range(2):
        request = b.request(harness)
        await harness.port.admit(request, b.idem(request, f"key-{n}"), (), b.hold_for(request))
    request = b.request(harness)
    try:
        await harness.port.admit(request, b.idem(request, "key-over"), (), b.hold_for(request))
    except errors.CapacityExhausted as exc:
        assert errors.http_status(exc.code) == 429 and exc.retry_after_s >= 1
    else:
        raise AssertionError("the per-key active job limit was exceeded")
    # a different key in the same org still fits under the org limit
    request = b.request(harness, key_id=b.KEY_B)
    await harness.port.admit(request, b.idem(request, "key-other"), (), b.hold_for(request))
    assert len(harness.extra["active_jobs"]()) == 3


async def dur_cap__admission_reserves_preparation_capacity(factory):
    """DUR-CAP / r1 R1: admission takes a `preparation` reservation against
    MAX_PREPARING_JOBS, so preparation cannot be oversubscribed even while job
    slots are free. The reservation is released at `prepared`, which lets the next
    request in; PREPARATION_CONCURRENCY stays the host worker-pool size."""
    harness = factory(limits=DEFAULTS.replace(max_preparing_jobs=2))
    harness.extra["grant"](b.ORG_A, "100.00")
    admitted = []
    for n in range(2):
        request = b.request(harness)
        admitted.append(await harness.port.admit(request, b.idem(request, f"p-{n}"), (),
                                                 b.hold_for(request)))
    request = b.request(harness)
    try:
        await harness.port.admit(request, b.idem(request, "p-over"), (), b.hold_for(request))
    except errors.CapacityExhausted as exc:
        assert errors.http_status(exc.code) == 429 and exc.retry_after_s >= 1
    else:
        raise AssertionError("preparation capacity was oversubscribed")
    assert len(harness.extra["active_jobs"]()) == 2      # job slots were still free
    kinds = {r.kind for r in admitted[0].reservations if r.active}
    assert {kind.value for kind in kinds} >= {"preparation"}
    await harness.port.prepared(admitted[0].job_handle, ())
    after = await harness.port.admit(request, b.idem(request, "p-after"), (),
                                     b.hold_for(request))
    assert after.state is JobState.preparing


async def dur_cap__journal_reservation_must_fit_the_global_budget(factory):
    """DUR-CAP: when the journal reservation cannot be taken, admission is refused
    before acceptance, with its own code."""
    limits = DEFAULTS.replace(journal_total_bytes=DEFAULTS.journal_job_reserve_bytes * 2)
    harness = factory(limits=limits)
    harness.extra["grant"](b.ORG_A, "100.00")
    for n in range(2):
        request = b.request(harness)
        await harness.port.admit(request, b.idem(request, f"j-{n}"), (), b.hold_for(request))
    request = b.request(harness)
    try:
        await harness.port.admit(request, b.idem(request, "j-over"), (), b.hold_for(request))
    except errors.JournalCapacityExhausted as exc:
        assert errors.http_status(exc.code) == 429 and exc.retry_after_s >= 1
    else:
        raise AssertionError("the journal budget was oversubscribed")


async def dur_cap__hold_cannot_exceed_the_available_balance(factory):
    """DUR-CAP: no negative available balance; the maximum hold is the gate."""
    harness = factory()
    request = b.request(harness)
    harness.extra["grant"](request.org_id, "0.00000100")
    try:
        await harness.port.admit(request, b.idem(request, "poor"), (), b.hold_for(request))
    except errors.InsufficientCredit as exc:
        assert errors.http_status(exc.code) == 402
    else:
        raise AssertionError("a hold larger than the balance was accepted")
    balance = harness.extra["balance"](request.org_id)
    assert balance["available"] >= 0 and balance["reserved"] == 0


async def dur_cap__a_negative_maximum_hold_is_refused(factory):
    """DUR-CAP: no negative available balance. A negative hold would reduce the
    reserved total, fabricate credit out of an empty wallet and let the next
    admission settle a real debit against it."""
    harness = factory()
    harness.extra["grant"](b.ORG_A, "0")
    request = b.request(harness)
    try:
        await harness.port.admit(request, b.idem(request, "negative"), (),
                                 -money.parse("10.00"))
    except errors.DomainError as exc:
        assert errors.http_status(exc.code) in (400, 402), exc.code
    else:
        raise AssertionError("a negative maximum hold was reserved")
    balance = harness.extra["balance"](b.ORG_A)
    assert balance["reserved"] == 0 and balance["available"] == 0
    assert len(harness.extra["active_jobs"]()) == 0


async def dur_cap__concurrent_admissions_never_oversubscribe(factory):
    """DUR-CAP: eight concurrent admissions against room for three: exactly three
    win, the balance never goes negative and no reservation is double counted."""
    harness = factory(limits=DEFAULTS.replace(max_active_jobs=3, max_active_jobs_per_org=3,
                                              max_active_jobs_per_key=3))
    harness.extra["grant"](b.ORG_A, "100.00")
    requests = [b.request(harness) for _ in range(8)]

    async def attempt(index, request):
        try:
            return await harness.port.admit(request, b.idem(request, f"c-{index}"), (),
                                            b.hold_for(request))
        except (errors.CapacityExhausted, errors.InsufficientCredit) as exc:
            return exc

    results = await asyncio.gather(*(attempt(i, r) for i, r in enumerate(requests)))
    admitted = [r for r in results if not isinstance(r, Exception)]
    assert len(admitted) == 3, [type(r).__name__ for r in results]
    balance = harness.extra["balance"](b.ORG_A)
    assert balance["available"] >= 0
    assert balance["reserved"] == sum((a.maximum_hold for a in admitted), start=money.ZERO)
    assert len({a.job_handle for a in admitted}) == 3


# --------------------------------------------------------------------------
# DUR-FENCE
# --------------------------------------------------------------------------
async def dur_fence__claim_increments_the_generation_from_the_database_clock(factory):
    """DUR-FENCE: claiming moves queued -> running and mints a fenced lease."""
    harness = factory()
    request, admission = await _admit(harness)
    try:
        await harness.port.claim(request.request_id, "worker-a")
    except errors.NotClaimable:
        pass
    else:
        raise AssertionError("a preparing job was claimable")
    queued = await harness.port.prepared(admission.job_handle, ())
    assert queued.state is JobState.queued
    lease = await harness.port.claim(request.request_id, "worker-a")
    assert lease.generation == 1 and lease.worker_id == "worker-a"
    assert lease.acquired_at == harness.clock.now()              # database time
    assert lease.expires_at > lease.acquired_at
    try:
        await harness.port.claim(request.request_id, "worker-b")
    except errors.NotClaimable:
        pass
    else:
        raise AssertionError("a running job was claimed twice")


async def dur_fence__an_expired_lease_can_neither_renew_nor_settle(factory):
    """DUR-FENCE: past its expiry a lease is stale for every mutation."""
    harness = factory()
    request, admission, lease = await _running(harness)
    harness.clock.advance(DEFAULTS.lease_heartbeat_s)
    renewed = await harness.port.heartbeat(lease)
    assert renewed.expires_at > lease.expires_at
    harness.clock.advance(DEFAULTS.lease_ttl_s + 1)
    for call in (harness.port.heartbeat(renewed),
                 harness.port.complete(renewed, b.outcome(request.request_id, harness))):
        try:
            await call
        except errors.StaleLease:
            pass
        else:
            raise AssertionError("an expired lease mutated the job")


async def dur_fence__a_stale_generation_is_rejected(factory):
    """DUR-FENCE: after recovery and a new claim, the old generation is fenced.

    The queue budget is widened because a lost 120 s lease would otherwise take the
    job past its persisted queue deadline (r1 R20) before it can be reclaimed; this
    case is about generation fencing, not about queueing."""
    harness = factory(limits=DEFAULTS.replace(queue_wait_interactive_s=10_000))
    request, admission, first = await _running(harness)
    harness.clock.advance(DEFAULTS.lease_ttl_s + 1)
    await harness.port.recover()
    second = await harness.port.claim(request.request_id, "worker-b")
    assert second.generation == first.generation + 1
    try:
        await harness.port.complete(first, b.outcome(request.request_id, harness))
    except errors.StaleLease:
        pass
    else:
        raise AssertionError("a fenced worker settled the job")
    settled = await harness.port.complete(second, b.outcome(request.request_id, harness))
    assert settled.settlement_state is SettlementState.settled


# --------------------------------------------------------------------------
# DUR-OUTPUT (recovery side; the journal cases are in the stream suite)
# --------------------------------------------------------------------------
async def dur_output__recovery_requeues_only_before_publication(factory):
    """DUR-OUTPUT: a lost attempt with no committed output may run again."""
    harness = factory()
    request, admission, lease = await _running(harness)
    harness.clock.advance(DEFAULTS.lease_ttl_s + 1)
    produced = await harness.port.recover()
    assert produced, "recovery produced nothing for an expired lease"
    stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert stored.state is JobState.queued and outcome is None


async def dur_output__loss_after_publication_is_a_terminal_failure(factory):
    """DUR-OUTPUT: once output is committed the request is never regenerated. The
    attempt published tokens and never reported authoritative usage, so r1 R21 sends
    it through reconciliation instead: no debit now, and none ever (02: late evidence
    records internal cost but never becomes a delayed customer debit)."""
    harness = factory()
    publish = harness.extra.get("publish")
    if publish is None:
        return
    request, admission, lease = await _running(harness)
    await publish(lease)
    before = harness.extra["balance"](request.org_id)
    harness.clock.advance(DEFAULTS.lease_ttl_s + 1)
    await harness.port.recover()
    stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert stored.state is JobState.failed
    assert outcome.cause is TerminalCause.lost_after_publication
    assert outcome.debit == 0 and outcome.settlement_state in {
        SettlementState.held_unknown, SettlementState.released_platform_absorbed,
        SettlementState.released_free}
    if outcome.settlement_state is SettlementState.held_unknown:
        harness.clock.advance(DEFAULTS.unknown_usage_reconcile_s + 1)
        await harness.port.recover()
        _stored, final = await harness.port.get_owned(request.org_id, admission.job_handle)
        assert final.settlement_state is SettlementState.released_platform_absorbed
    after = harness.extra["balance"](request.org_id)
    assert after["reserved"] == 0 and after["ledger"] == before["ledger"]


async def dur_output__prepublication_retries_are_bounded(factory):
    """DUR-OUTPUT: retries are capped, then the job fails honestly."""
    harness = factory(limits=DEFAULTS.replace(max_prepublication_retries=1,
                                              queue_wait_interactive_s=10_000))
    request, admission, lease = await _running(harness)
    for _ in range(3):
        harness.clock.advance(DEFAULTS.lease_ttl_s + 1)
        await harness.port.recover()
        stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
        if outcome is not None:
            break
        await harness.port.claim(request.request_id, "worker-a")
    assert outcome is not None, "retries were unbounded"
    assert outcome.cause is TerminalCause.retries_exhausted and outcome.debit == 0


async def dur_output__queue_wait_does_not_restart_on_a_requeue(factory):
    """DUR-OUTPUT: the queue-wait budget starts at the durable queued transition
    and is never extended by a retry (01, limits table), so queue time accumulated
    before a lost attempt still counts after the requeue."""
    harness = factory(limits=DEFAULTS.replace(queue_wait_interactive_s=10))
    request, admission = await _admit(harness, mode=ExecutionMode.sync, deadline_s=10_000)
    await harness.port.prepared(admission.job_handle, ())
    harness.clock.advance(9)                       # 9s of a 10s queue budget spent
    await harness.port.claim(request.request_id, "worker-a")
    harness.clock.advance(DEFAULTS.lease_ttl_s + 1)
    await harness.port.recover()            # lost, requeued
    stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert stored.state is JobState.queued and outcome is None
    harness.clock.advance(9)                       # 18s of queue wait in total
    await harness.port.recover()
    stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert outcome is not None, "the requeue handed the job a fresh queue-wait budget"
    assert outcome.cause is TerminalCause.queue_wait_expired and outcome.debit == 0


async def dur_output__an_accepted_job_keeps_its_admission_budgets(factory):
    """DUR-OUTPUT / r1 R4: the deadline budgets are snapshotted at admission, so a
    configuration change afterwards never alters an accepted job. The snapshot is on
    the admission, and the store's expiry decisions read it, not current settings."""
    harness = factory(limits=DEFAULTS.replace(queue_wait_interactive_s=10))
    retune = harness.extra.get("retune")
    request, admission = await _admit(harness, mode=ExecutionMode.sync, deadline_s=10_000)
    assert admission.budgets.queue_wait_s == 10
    assert admission.budgets.generation_s == DEFAULTS.generation_timeout_s
    await harness.port.prepared(admission.job_handle, ())
    if retune is None:
        return                                     # optional hook; adapter may skip
    retune(queue_wait_interactive_s=1)             # an operator tightens the budget
    harness.clock.advance(5)                       # past the new budget, inside the old
    await harness.port.recover()
    stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert outcome is None and stored.state is JobState.queued, \
        "a configuration change expired an already accepted job"
    harness.clock.advance(6)                       # now past the accepted 10s budget
    await harness.port.recover()
    _stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert outcome is not None and outcome.cause is TerminalCause.queue_wait_expired


async def dur_output__phase_deadlines_are_persisted_at_each_transition(factory):
    """DUR-OUTPUT / r1 R20: the store derives each phase instant from the database
    clock at the transition into that phase, never beyond `deadline_at`, and hands it
    to whoever enforces it. The instants are facts on the record, not a budget every
    worker re-derives against its own clock, and a configuration change after
    admission moves none of them."""
    harness = factory()
    retune = harness.extra.get("retune")
    request, admission = await _admit(harness, deadline_s=10_000)
    assert admission.preparation_deadline_at == harness.clock.at(admission.budgets.preparation_s)
    assert admission.queue_deadline_at is None          # not queued yet
    harness.clock.advance(7)
    queued = await harness.port.prepared(admission.job_handle, ())
    first_queue_deadline = queued.queue_deadline_at
    assert first_queue_deadline == harness.clock.at(queued.budgets.queue_wait_s)
    assert queued.preparation_deadline_at == admission.preparation_deadline_at
    if retune is not None:
        retune(queue_wait_interactive_s=1, generation_timeout_s=1, preparation_timeout_s=1)
    lease = await harness.port.claim(request.request_id, "worker-a")
    assert lease.generation_deadline_at == harness.clock.at(queued.budgets.generation_s)
    assert lease.first_token_deadline_at == harness.clock.at(queued.budgets.first_token_s)
    assert lease.first_token_deadline_at <= lease.generation_deadline_at
    # a prepublication requeue re-claims, and the queue instant is still the first one
    harness.clock.advance(DEFAULTS.lease_ttl_s + 1)
    await harness.port.recover()
    requeued, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
    if outcome is None:
        assert requeued.state is JobState.queued
        assert requeued.queue_deadline_at == first_queue_deadline, \
            "a requeue moved the queue deadline"
        assert requeued.preparation_deadline_at == admission.preparation_deadline_at


async def dur_output__no_phase_deadline_outlives_the_accepted_deadline(factory):
    """DUR-OUTPUT / r1 R20: a job accepted with less time left than a phase budget
    gets the absolute deadline as its phase instant. The accepted `deadline_at` is the
    ceiling for every phase, so no budget can extend a job past what the customer was
    promised."""
    harness = factory()
    request, admission = await _admit(harness, deadline_s=5)
    assert admission.budgets.preparation_s > 5          # the budget is the larger one
    assert admission.preparation_deadline_at == admission.deadline_at
    queued = await harness.port.prepared(admission.job_handle, ())
    assert queued.queue_deadline_at == admission.deadline_at
    lease = await harness.port.claim(request.request_id, "worker-a")
    assert lease.generation_deadline_at == admission.deadline_at
    assert lease.first_token_deadline_at <= admission.deadline_at
    harness.clock.advance(6)
    await harness.port.recover()
    _stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert outcome is not None and outcome.debit == 0
    assert outcome.cause in {TerminalCause.deadline_exceeded, TerminalCause.queue_wait_expired}


async def dur_output__the_generation_deadline_ends_a_running_attempt(factory):
    """DUR-OUTPUT / r1 R20 with R21: a running attempt past its persisted generation
    deadline is terminalized by the reaper even while its lease is still live, and the
    cause is our own deadline, so the customer is not charged for it."""
    harness = factory(limits=DEFAULTS.replace(generation_timeout_s=30, lease_ttl_s=10_000,
                                              lease_heartbeat_s=1_000))
    request, admission, lease = await _running(harness, deadline_s=10_000)
    before = harness.extra["balance"](request.org_id)
    harness.clock.advance(31)
    assert harness.clock.now() < lease.expires_at      # the lease itself is still valid
    await harness.port.recover()
    stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert outcome is not None, "a run past its generation deadline was left running"
    assert outcome.cause is TerminalCause.deadline_exceeded
    assert outcome.debit == 0 and stored.state in {JobState.failed, JobState.expired}
    after = harness.extra["balance"](request.org_id)
    assert after["ledger"] == before["ledger"] and after["reserved"] == 0


async def dur_output__a_job_past_its_queue_budget_is_not_claimable(factory):
    """DUR-OUTPUT: the queue-wait budget bounds execution, not just reporting. A job
    the customer has already been told to give up on must not start running because
    a worker happened to claim it before the reconciler reached it."""
    harness = factory(limits=DEFAULTS.replace(queue_wait_interactive_s=10))
    request, admission = await _admit(harness, mode=ExecutionMode.sync, deadline_s=10_000)
    await harness.port.prepared(admission.job_handle, ())
    harness.clock.advance(60)                      # six times the 10s budget
    try:
        await harness.port.claim(request.request_id, "worker-a")
    except errors.NotClaimable:
        pass
    else:
        raise AssertionError("a job past its queue-wait budget was claimed")
    await harness.port.recover()
    _stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert outcome is not None and outcome.cause is TerminalCause.queue_wait_expired
    assert outcome.debit == 0


async def dur_output__the_absolute_deadline_bounds_recovery(factory):
    """DUR-OUTPUT: bounded retries never extend the accepted deadline."""
    harness = factory()
    request, admission, lease = await _running(harness, deadline_s=DEFAULTS.lease_ttl_s + 5)
    harness.clock.advance(DEFAULTS.lease_ttl_s + 10)
    await harness.port.recover()
    stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert outcome is not None and stored.state in {JobState.failed, JobState.expired}
    assert outcome.debit == 0


# --------------------------------------------------------------------------
# DUR-SETTLE
# --------------------------------------------------------------------------
async def _settle(harness, request, admission, *, tokens=None, cause=TerminalCause.completed,
                  state=JobState.succeeded):
    await harness.port.prepared(admission.job_handle, ())
    lease = await harness.port.claim(request.request_id, "worker-a")
    return await harness.port.complete(
        lease, b.outcome(request.request_id, harness, cause=cause, state=state, tokens=tokens))


async def dur_settle__one_settlement_with_exact_decimals(factory):
    """DUR-SETTLE: the debit is the price snapshot applied once, half up."""
    harness = factory()
    request, admission = await _admit(harness)
    before = harness.extra["balance"](request.org_id)
    outcome = await _settle(harness, request, admission, tokens=b.usage(1200, 340))
    expected = admission.price_snapshot.debit(1200, 340)
    assert outcome.settlement_state is SettlementState.settled and outcome.debit == expected
    after = harness.extra["balance"](request.org_id)
    assert after["ledger"] == before["ledger"] - expected
    # the hold is released in the same transaction that debits
    assert after["reserved"] == 0 and after["available"] == after["ledger"]


async def dur_settle__duplicate_completion_is_idempotent_then_conflicts(factory):
    """DUR-SETTLE: one winner. The identical call replays; a different one is a
    typed conflict and changes no money."""
    harness = factory()
    request, admission = await _admit(harness)
    await harness.port.prepared(admission.job_handle, ())
    lease = await harness.port.claim(request.request_id, "worker-a")
    proposal = b.outcome(request.request_id, harness, tokens=b.usage(1200, 340))
    first = await harness.port.complete(lease, proposal)
    again = await harness.port.complete(lease, proposal)
    assert again == first
    ledger = harness.extra["balance"](request.org_id)["ledger"]
    try:
        await harness.port.complete(lease, b.outcome(request.request_id, harness,
                                                     tokens=b.usage(9, 9)))
    except errors.AlreadyTerminal:
        pass
    else:
        raise AssertionError("a second different settlement was accepted")
    assert harness.extra["balance"](request.org_id)["ledger"] == ledger


async def dur_settle__cancel_and_complete_race_has_a_single_winner(factory):
    """DUR-SETTLE: a completed job stays completed; the loser sees the committed
    outcome or a typed conflict, and exactly one settlement exists."""
    harness = factory()
    request, admission = await _admit(harness)
    await harness.port.prepared(admission.job_handle, ())
    lease = await harness.port.claim(request.request_id, "worker-a")

    async def cancel():
        try:
            return await harness.port.cancel(request.org_id, admission.job_handle)
        except errors.DomainError as exc:
            return exc

    async def complete():
        try:
            return await harness.port.complete(
                lease, b.outcome(request.request_id, harness, tokens=b.usage(1200, 340)))
        except errors.DomainError as exc:
            return exc

    results = await asyncio.gather(cancel(), complete())
    outcomes = [r for r in results if not isinstance(r, Exception)]
    assert outcomes, [type(r).__name__ for r in results]
    stored, final = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert final is not None
    assert all(o.cause is final.cause and o.debit == final.debit for o in outcomes)
    assert final.state in {JobState.succeeded, JobState.cancelled}
    balance = harness.extra["balance"](request.org_id)
    assert balance["reserved"] == 0                      # the hold resolved exactly once


async def dur_settle__unknown_usage_is_held_then_released_as_platform_absorbed(factory):
    """DUR-SETTLE: no authoritative usage means no debit; the hold is released only
    after the attempt is fenced, the job is terminal and 24h have passed."""
    harness = factory()
    publish = harness.extra.get("publish")
    request, admission = await _admit(harness)
    await harness.port.prepared(admission.job_handle, ())
    lease = await harness.port.claim(request.request_id, "worker-a")
    if publish is not None:
        await publish(lease)
    outcome = await harness.port.complete(
        lease, b.outcome(request.request_id, harness, cause=TerminalCause.client_disconnected,
                         state=JobState.failed, tokens=None, result_ref=None))
    if publish is None:
        return
    assert outcome.settlement_state is SettlementState.held_unknown
    assert outcome.debit == 0 and outcome.reconcile_after is not None
    held = harness.extra["balance"](request.org_id)
    assert held["reserved"] == admission.maximum_hold

    harness.clock.advance(DEFAULTS.unknown_usage_reconcile_s - 60)
    await harness.port.recover()
    assert harness.extra["balance"](request.org_id)["reserved"] == admission.maximum_hold

    harness.clock.advance(120)
    await harness.port.recover()
    after = harness.extra["balance"](request.org_id)
    assert after["reserved"] == 0
    assert after["ledger"] == held["ledger"]             # late evidence never retrodebits
    _, final = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert final.settlement_state is SettlementState.released_platform_absorbed


async def dur_settle__an_unknown_usage_hold_is_never_released_on_a_callers_clock(factory):
    """DUR-SETTLE / r1 R7: the 24 h reconciliation window is measured by the
    database clock inside the transaction. `recover` takes no caller time, so a
    caller claiming it is three days later cannot release a live hold early."""
    harness = factory()
    publish = harness.extra.get("publish")
    if publish is None:
        return
    request, admission = await _admit(harness)
    await harness.port.prepared(admission.job_handle, ())
    lease = await harness.port.claim(request.request_id, "worker-a")
    await publish(lease)
    outcome = await harness.port.complete(
        lease, b.outcome(request.request_id, harness, cause=TerminalCause.client_disconnected,
                         state=JobState.failed, tokens=None, result_ref=None))
    assert outcome.settlement_state is SettlementState.held_unknown
    harness.clock.advance(DEFAULTS.unknown_usage_reconcile_s - 1)
    try:
        # A signature that still accepts a time must treat it as no more than a
        # bound; one that refuses it outright is equally correct.
        await harness.port.recover(harness.clock.at(3 * 86_400))
    except TypeError:
        await harness.port.recover()
    held = harness.extra["balance"](request.org_id)
    assert held["reserved"] == admission.maximum_hold, \
        "a caller-supplied time released an unknown-usage hold before 24h"
    _stored, still = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert still.settlement_state is SettlementState.held_unknown
    harness.clock.advance(2)                       # now the database clock is past 24h
    await harness.port.recover()
    assert harness.extra["balance"](request.org_id)["reserved"] == 0


async def dur_settle__an_outcome_settles_only_its_own_job(factory):
    """DUR-SETTLE / r1 R10: `complete` refuses an outcome built for another job, so
    job A can never be settled with job B's usage, debit and result reference."""
    harness = factory()
    request_a, admission_a = await _admit(harness, key="own-a")
    request_b, admission_b = await _admit(harness, key="own-b")
    await harness.port.prepared(admission_a.job_handle, ())
    lease = await harness.port.claim(request_a.request_id, "worker-a")
    before = harness.extra["balance"](b.ORG_A)
    try:
        await harness.port.complete(lease, b.outcome(request_b.request_id, harness,
                                                    tokens=b.usage(1200, 340)))
    except errors.DomainError as exc:
        assert errors.http_status(exc.code) in (400, 404, 409), exc.code
    else:
        raise AssertionError("an outcome for another job settled this one")
    assert harness.extra["balance"](b.ORG_A) == before
    for handle in (admission_a.job_handle, admission_b.job_handle):
        _stored, outcome = await harness.port.get_owned(b.ORG_A, handle)
        assert outcome is None


async def dur_settle__a_rejected_settlement_moves_no_money(factory):
    """DUR-SETTLE: validation happens before the wallet moves. An outcome whose
    cause and state disagree is a typed conflict with the ledger, the hold and the
    job's state all untouched - never a debit committed on a job that then failed to
    become terminal."""
    from ..records import TerminalOutcome
    harness = factory()
    request, admission = await _admit(harness)
    await harness.port.prepared(admission.job_handle, ())
    lease = await harness.port.claim(request.request_id, "worker-a")
    before = harness.extra["balance"](request.org_id)
    # `TerminalOutcome` refuses to build this pair, so the only way one reaches a
    # store is unvalidated (a queue payload, a row read back); a real adapter must
    # refuse it just as the record does.
    bogus = TerminalOutcome.model_construct(
        job_id=request.request_id, state=JobState.cancelled, cause=TerminalCause.completed,
        usage=b.usage(1200, 340), result_ref=None, debit=money.ZERO,
        settlement_state=SettlementState.settled, settled_at=harness.clock.now(),
        schema_version=1, reconcile_after=None)
    try:
        await harness.port.complete(lease, bogus)
    except (errors.DomainError, ValueError):
        pass
    else:
        raise AssertionError("a cause/state mismatch was settled")
    assert harness.extra["balance"](request.org_id) == before
    stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert outcome is None and stored.state is JobState.running
    # the honest completion still works afterwards
    settled = await harness.port.complete(lease, b.outcome(request.request_id, harness,
                                                           tokens=b.usage(1200, 340)))
    assert settled.settlement_state is SettlementState.settled


async def dur_settle__a_delivered_success_needs_authoritative_usage(factory):
    """DUR-SETTLE: `completed` with no authoritative usage is not a free success.
    Output chunks never bill (01), so a worker that claims a delivered result without
    reporting usage produces an honest engine failure, not a succeeded job at zero
    cost with a result the customer can fetch."""
    harness = factory()
    request, admission = await _admit(harness)
    before = harness.extra["balance"](request.org_id)
    outcome = await _settle(harness, request, admission, tokens=None)
    assert outcome.state is not JobState.succeeded, "a success was settled without usage"
    assert outcome.cause in {TerminalCause.engine_incomplete, TerminalCause.platform_error}
    assert outcome.debit == 0 and outcome.result_ref is None
    assert outcome.settlement_state in {SettlementState.released_free,
                                        SettlementState.released_platform_absorbed}
    after = harness.extra["balance"](request.org_id)
    assert after["ledger"] == before["ledger"] and after["reserved"] == 0


async def dur_settle__the_winning_worker_can_always_replay_its_completion(factory):
    """DUR-SETTLE: a crash after the settling commit must be resolvable. The
    worker's identical retry returns the committed outcome even when the store
    rewrote the settlement (usage beyond the reserved envelope), instead of a
    conflict the worker cannot act on."""
    harness = factory()
    request, admission = await _admit(harness, max_output_tokens=16, max_input_tokens=32)
    await harness.port.prepared(admission.job_handle, ())
    lease = await harness.port.claim(request.request_id, "worker-a")
    proposal = b.outcome(request.request_id, harness, tokens=b.usage(32, 4096))
    first = await harness.port.complete(lease, proposal)
    assert first.cause is TerminalCause.platform_error      # the store rewrote it
    assert await harness.port.complete(lease, proposal) == first
    ledger = harness.extra["balance"](request.org_id)["ledger"]
    try:
        await harness.port.complete(lease, b.outcome(request.request_id, harness,
                                                     tokens=b.usage(1, 1)))
    except errors.AlreadyTerminal:
        pass
    else:
        raise AssertionError("a different second settlement was accepted")
    assert harness.extra["balance"](request.org_id)["ledger"] == ledger


async def dur_settle__only_three_causes_can_charge(factory):
    """DUR-SETTLE / r1 R21: exactly `completed`, `client_cancelled` and
    `client_disconnected` settle a debit, and only with authoritative usage. Our own
    deadlines are platform-caused, so `sync_deadline`, `deadline_exceeded` and
    `queue_wait_expired` cost the customer nothing **even with authoritative usage**:
    the tokens were produced, and the platform absorbs them."""
    harness = factory()
    billable = ((TerminalCause.completed, JobState.succeeded),
                (TerminalCause.client_cancelled, JobState.cancelled),
                (TerminalCause.client_disconnected, JobState.failed))
    absorbed = ((TerminalCause.sync_deadline, JobState.failed),
                (TerminalCause.deadline_exceeded, JobState.failed),
                (TerminalCause.queue_wait_expired, JobState.expired),
                (TerminalCause.engine_error, JobState.failed),
                (TerminalCause.engine_incomplete, JobState.failed),
                (TerminalCause.retries_exhausted, JobState.failed),
                (TerminalCause.journal_write_failed, JobState.failed),
                (TerminalCause.invalid_media, JobState.failed),
                (TerminalCause.preparation_failed, JobState.failed),
                (TerminalCause.platform_error, JobState.failed))
    for cause, state in billable:
        request, admission = await _admit(harness, key=f"bill-{cause.value}")
        before = harness.extra["balance"](request.org_id)
        outcome = await _settle(harness, request, admission, cause=cause, state=state,
                               tokens=b.usage(1200, 340))
        expected = admission.price_snapshot.debit(1200, 340)
        assert outcome.settlement_state is SettlementState.settled, cause
        assert outcome.debit == expected and expected > 0, cause
        after = harness.extra["balance"](request.org_id)
        assert after["ledger"] == before["ledger"] - expected, cause
        assert after["reserved"] == before["reserved"] - admission.maximum_hold, cause
    for cause, state in absorbed:
        # the same authoritative usage, a cause the platform absorbs
        request, admission = await _admit(harness, key=f"free-{cause.value}")
        before = harness.extra["balance"](request.org_id)
        outcome = await _settle(harness, request, admission, cause=cause, state=state,
                               tokens=b.usage(1200, 340))
        assert outcome.debit == 0, f"{cause} charged the customer"
        assert outcome.settlement_state in {SettlementState.released_free,
                                            SettlementState.released_platform_absorbed}, cause
        after = harness.extra["balance"](request.org_id)
        assert after["ledger"] == before["ledger"], cause
        assert after["reserved"] == before["reserved"] - admission.maximum_hold, cause


async def dur_settle__platform_failures_are_free(factory):
    """DUR-SETTLE: platform-caused failures and invalid preparation cost nothing."""
    harness = factory()
    for cause, state in ((TerminalCause.platform_error, JobState.failed),
                         (TerminalCause.invalid_media, JobState.failed),
                         (TerminalCause.engine_error, JobState.failed)):
        request, admission = await _admit(harness, key=f"free-{cause.value}")
        before = harness.extra["balance"](request.org_id)
        outcome = await _settle(harness, request, admission, cause=cause, state=state, tokens=None)
        assert outcome.debit == 0, cause
        assert outcome.settlement_state in {SettlementState.released_free,
                                            SettlementState.released_platform_absorbed}, cause
        after = harness.extra["balance"](request.org_id)
        assert after["ledger"] == before["ledger"]       # nothing was charged
        assert after["reserved"] == before["reserved"] - admission.maximum_hold


async def dur_settle__usage_beyond_the_reserved_envelope_is_a_platform_failure(factory):
    """DUR-SETTLE: a protocol violation past the validated envelope is reconciled
    as a platform failure, never charged as an unreserved debit."""
    harness = factory()
    request, admission = await _admit(harness, max_output_tokens=16, max_input_tokens=32)
    before = harness.extra["balance"](request.org_id)
    outcome = await _settle(harness, request, admission, tokens=b.usage(32, 4096))
    assert outcome.debit == 0 and outcome.cause is TerminalCause.platform_error
    assert outcome.settlement_state is SettlementState.released_platform_absorbed
    after = harness.extra["balance"](request.org_id)
    assert after["ledger"] == before["ledger"] and after["reserved"] == 0


async def dur_settle__stale_and_out_of_order_transitions_are_typed_conflicts(factory):
    """DUR-SETTLE: the state machine refuses shortcuts without side effects."""
    harness = factory()
    request, admission = await _admit(harness)
    await harness.port.prepared(admission.job_handle, ())
    try:
        await harness.port.prepared(admission.job_handle, ())
    except errors.StateConflict as exc:
        assert errors.http_status(exc.code) == 409
    else:
        raise AssertionError("preparing -> queued ran twice")
    lease = await harness.port.claim(request.request_id, "worker-a")
    await harness.port.complete(lease, b.outcome(request.request_id, harness))
    for call in (harness.port.prepared(admission.job_handle, ()),
                 harness.port.claim(request.request_id, "worker-b")):
        try:
            await call
        except errors.AlreadyTerminal:
            pass
        else:
            raise AssertionError("a terminal job accepted a transition")
    cancelled = await harness.port.cancel(request.org_id, admission.job_handle)
    assert cancelled.cause is TerminalCause.completed     # the committed outcome wins


# --------------------------------------------------------------------------
# DUR-OUTBOX
# --------------------------------------------------------------------------
async def dur_outbox__every_transition_emits_its_projection(factory):
    """DUR-OUTBOX: dispatch and projection events exist for replayable delivery."""
    harness = factory()
    request, admission = await _admit(harness)
    await _settle(harness, request, admission, tokens=b.usage(1200, 340))
    kinds = harness.extra["outbox_kinds"](request.request_id)
    for expected in (OutboxKind.prepare_dispatch, OutboxKind.inference_dispatch,
                     OutboxKind.usage_projection, OutboxKind.trace_projection):
        assert expected in kinds, (expected, kinds)
    events = harness.extra["outbox"](request.request_id)
    assert len({event.event_id for event in events}) == len(events)   # stable, unique ids


def jobstore_cases():
    return [
        dur_admit__acceptance_creates_job_hold_reservations_and_outbox,
        dur_admit__idempotent_replay_returns_the_same_identity,
        dur_admit__a_request_uuid_is_admitted_once,
        dur_admit__changed_payload_with_the_same_key_is_a_conflict,
        dur_admit__crash_after_commit_then_retry_does_not_double_reserve,
        dur_admit__expired_mapping_is_explicit_never_a_second_billable_job,
        dur_admit__tombstone_is_retained_after_the_terminal_state,
        dur_admit__revocation_and_suspension_are_rechecked_in_the_transaction,
        dur_admit__an_idempotency_scope_belongs_to_the_requests_own_org,
        dur_admit__a_refused_admission_reserves_nothing,
        dur_cap__total_org_and_key_limits_reject_with_retry_guidance,
        dur_cap__admission_reserves_preparation_capacity,
        dur_cap__journal_reservation_must_fit_the_global_budget,
        dur_cap__hold_cannot_exceed_the_available_balance,
        dur_cap__a_negative_maximum_hold_is_refused,
        dur_cap__concurrent_admissions_never_oversubscribe,
        dur_fence__claim_increments_the_generation_from_the_database_clock,
        dur_fence__an_expired_lease_can_neither_renew_nor_settle,
        dur_fence__a_stale_generation_is_rejected,
        dur_output__recovery_requeues_only_before_publication,
        dur_output__loss_after_publication_is_a_terminal_failure,
        dur_output__prepublication_retries_are_bounded,
        dur_output__queue_wait_does_not_restart_on_a_requeue,
        dur_output__an_accepted_job_keeps_its_admission_budgets,
        dur_output__phase_deadlines_are_persisted_at_each_transition,
        dur_output__no_phase_deadline_outlives_the_accepted_deadline,
        dur_output__the_generation_deadline_ends_a_running_attempt,
        dur_output__a_job_past_its_queue_budget_is_not_claimable,
        dur_output__the_absolute_deadline_bounds_recovery,
        dur_settle__one_settlement_with_exact_decimals,
        dur_settle__duplicate_completion_is_idempotent_then_conflicts,
        dur_settle__cancel_and_complete_race_has_a_single_winner,
        dur_settle__unknown_usage_is_held_then_released_as_platform_absorbed,
        dur_settle__an_unknown_usage_hold_is_never_released_on_a_callers_clock,
        dur_settle__an_outcome_settles_only_its_own_job,
        dur_settle__a_rejected_settlement_moves_no_money,
        dur_settle__a_delivered_success_needs_authoritative_usage,
        dur_settle__the_winning_worker_can_always_replay_its_completion,
        dur_settle__only_three_causes_can_charge,
        dur_settle__platform_failures_are_free,
        dur_settle__usage_beyond_the_reserved_envelope_is_a_platform_failure,
        dur_settle__stale_and_out_of_order_transitions_are_typed_conflicts,
        dur_outbox__every_transition_emits_its_projection,
    ]


# ==========================================================================
# StreamStore
# ==========================================================================
async def _stream_job(harness, **kw):
    jobs = harness.extra["jobs"]
    inner = replace(harness, port=jobs)
    request, admission = await _admit(inner, **kw)
    await jobs.prepared(admission.job_handle, ())
    lease = await jobs.claim(request.request_id, "worker-a")
    return request, admission, lease


async def dur_output__append_commits_before_it_relays(factory):
    """DUR-OUTPUT: a crash after the commit still leaves the events readable, so
    nothing is relayed that is not durable."""
    harness = factory()
    request, admission, lease = await _stream_job(harness)
    if harness.failures is not None:
        harness.failures.crash_after_commit("append")
        try:
            await harness.port.append(lease, b.events("Hello"))
        except Exception as exc:
            assert type(exc).__name__ == "CrashAfterCommit", exc
        else:
            raise AssertionError("crash_after_commit did not fire")
        chunks, _ = await harness.port.read_owned(request.org_id, admission.job_handle, None, 10)
        assert [chunk.payload["content"] for chunk in chunks] == ["Hello"]
    committed = await harness.port.append(lease, b.events(" world"))
    assert all(chunk.persisted_at == harness.clock.now() for chunk in committed)


async def dur_output__the_first_append_sets_the_publication_marker(factory):
    """DUR-OUTPUT: the first committed chunk forbids regeneration for ever after."""
    harness = factory()
    jobs = harness.extra["jobs"]
    request, admission, lease = await _stream_job(harness)
    await harness.port.append(lease, b.events("Hello"))
    harness.clock.advance(DEFAULTS.lease_ttl_s + 1)
    await jobs.recover()
    stored, outcome = await jobs.get_owned(request.org_id, admission.job_handle)
    assert outcome is not None and outcome.cause is TerminalCause.lost_after_publication
    assert stored.state is JobState.failed


async def dur_output__cursors_are_generation_then_sequence(factory):
    """DUR-OUTPUT / API-STREAM: cursors carry the generation, so a replay after a
    new attempt can never splice two attempts together."""
    harness = factory()
    request, admission, lease = await _stream_job(harness)
    chunks = await harness.port.append(lease, b.events("a", "b", "c"))
    assert [chunk.cursor.token for chunk in chunks] == ["1-1", "1-2", "1-3"]
    page, cursor = await harness.port.read_owned(request.org_id, admission.job_handle,
                                                 Cursor.parse("1-1"), 10)
    assert [chunk.cursor.token for chunk in page] == ["1-2", "1-3"]
    assert cursor.token == "1-3"
    empty, same = await harness.port.read_owned(request.org_id, admission.job_handle, cursor, 10)
    assert empty == () and same == cursor


async def api_stream__reads_are_bounded_and_ownership_checked(factory):
    """API-STREAM: replay is paginated and tenant bound."""
    harness = factory()
    request, admission, lease = await _stream_job(harness)
    await harness.port.append(lease, b.events(*[f"piece-{n}" for n in range(5)]))
    page, cursor = await harness.port.read_owned(request.org_id, admission.job_handle, None, 2)
    assert len(page) == 2 and cursor.token == "1-2"
    rest, _ = await harness.port.read_owned(request.org_id, admission.job_handle, cursor, 100)
    assert len(rest) == 3
    try:
        await harness.port.read_owned(b.ORG_B, admission.job_handle, None, 10)
    except errors.NotFound:
        pass
    else:
        raise AssertionError("another org replayed the journal")
    for bad in (0, -1):
        try:
            await harness.port.read_owned(request.org_id, admission.job_handle, None, bad)
        except errors.InvalidRequest:
            pass
        else:
            raise AssertionError(f"limit {bad} was accepted")


async def dur_output__a_pruned_prefix_is_an_explicit_replay_gap(factory):
    """DUR-OUTPUT: a gap is reported, never filled by regenerating output."""
    # A short chunk TTL so the prefix expires while the lease is still valid.
    harness = factory(limits=DEFAULTS.replace(journal_chunk_ttl_s=30))
    request, admission, lease = await _stream_job(harness)
    await harness.port.append(lease, b.events("a", "b"))
    harness.clock.advance(31)
    await harness.port.append(lease, b.events("c"))
    removed = await harness.port.expire(harness.clock.now())
    assert removed == 2
    try:
        await harness.port.read_owned(request.org_id, admission.job_handle, Cursor.parse("1-0"), 10)
    except errors.ReplayGap as exc:
        assert errors.http_status(exc.code) == 410
    else:
        raise AssertionError("a pruned prefix was replayed silently")
    page, _ = await harness.port.read_owned(request.org_id, admission.job_handle,
                                            Cursor.parse("1-2"), 10)
    assert [chunk.payload["content"] for chunk in page] == ["c"]


async def dur_output__an_expired_journal_is_gone_not_regenerated(factory):
    """DUR-OUTPUT: after the journal TTL the events are 410, and status remains."""
    harness = factory()
    jobs = harness.extra["jobs"]
    request, admission, lease = await _stream_job(harness)
    await harness.port.append(lease, b.events("a"))
    await jobs.complete(lease, b.outcome(request.request_id, harness, tokens=b.usage(10, 1)))
    harness.clock.advance(DEFAULTS.journal_chunk_ttl_s + 1)
    await harness.port.expire(harness.clock.now())
    try:
        await harness.port.read_owned(request.org_id, admission.job_handle, None, 10)
    except errors.JournalExpired as exc:
        assert errors.http_status(exc.code) == 410
    else:
        raise AssertionError("an expired journal still served events")
    stored, outcome = await jobs.get_owned(request.org_id, admission.job_handle)
    assert outcome is not None                      # status survives the journal


async def dur_cap__stored_unexpired_bytes_keep_counting(factory):
    """DUR-CAP: a terminal job's retained bytes still consume the global journal
    budget until they are pruned."""
    harness = factory()
    usage_bytes = harness.extra.get("journal_bytes")
    if usage_bytes is None:
        return
    jobs = harness.extra["jobs"]
    request, admission, lease = await _stream_job(harness)
    await harness.port.append(lease, b.events("a" * 100))
    stored_before = usage_bytes()
    await jobs.complete(lease, b.outcome(request.request_id, harness, tokens=b.usage(10, 1)))
    after_terminal = usage_bytes()
    assert 0 < after_terminal < stored_before        # reservation freed, bytes remain
    harness.clock.advance(DEFAULTS.journal_chunk_ttl_s + 1)
    await harness.port.expire(harness.clock.now())
    assert usage_bytes() == 0


async def dur_fence__a_stale_worker_cannot_append(factory):
    """DUR-FENCE: an expired or superseded lease appends nothing. (Wide queue budget
    for the same reason as `dur_fence__a_stale_generation_is_rejected`.)"""
    harness = factory(limits=DEFAULTS.replace(queue_wait_interactive_s=10_000))
    jobs = harness.extra["jobs"]
    request, admission, first = await _stream_job(harness)
    harness.clock.advance(DEFAULTS.lease_ttl_s + 1)
    try:
        await harness.port.append(first, b.events("stale"))
    except errors.StaleLease:
        pass
    else:
        raise AssertionError("an expired lease appended output")
    await jobs.recover()
    second = await jobs.claim(request.request_id, "worker-b")
    await harness.port.append(second, b.events("fresh"))
    try:
        await harness.port.append(first, b.events("stale again"))
    except errors.StaleLease:
        pass
    else:
        raise AssertionError("a superseded generation appended output")
    page, _ = await harness.port.read_owned(request.org_id, admission.job_handle, None, 10)
    assert [chunk.payload["content"] for chunk in page] == ["fresh"]
    assert page[0].generation == 2


async def dur_output__an_oversize_event_is_refused(factory):
    """DUR-OUTPUT: never silently truncate; refuse the write."""
    harness = factory(limits=DEFAULTS.replace(journal_event_max_bytes=64))
    request, admission, lease = await _stream_job(harness)
    try:
        await harness.port.append(lease, b.events("x" * 200))
    except errors.JournalWriteFailed:
        pass
    else:
        raise AssertionError("an oversize event was journalled")
    page, _ = await harness.port.read_owned(request.org_id, admission.job_handle, None, 10)
    assert page == ()


async def dur_cap__a_job_cannot_store_past_its_journal_reservation(factory):
    """DUR-CAP: 02 requires per-job *and* global byte limits, so one job's journal
    cannot grow into the global budget past the bytes reserved for it."""
    limits = DEFAULTS.replace(journal_job_reserve_bytes=4096, journal_event_max_bytes=1024,
                              journal_total_bytes=1 << 20)
    harness = factory(limits=limits)
    request, admission, lease = await _stream_job(harness)
    event = "x" * 900
    for _ in range(4):
        await harness.port.append(lease, b.events(event))      # ~3.6 KiB of 4 KiB
    try:
        await harness.port.append(lease, b.events(event))
    except errors.JournalCapacityExhausted as exc:
        assert errors.http_status(exc.code) == 429 and exc.retry_after_s >= 1
    else:
        raise AssertionError("a job stored past its per-job journal reservation")
    journal_bytes = harness.extra.get("journal_bytes")
    if journal_bytes is not None:
        assert journal_bytes() <= limits.journal_job_reserve_bytes


async def dur_settle__the_terminal_event_belongs_to_the_settling_transaction(factory):
    """DUR-SETTLE (02 §7): one transaction inserts the terminal outcome, the
    authoritative usage, the ledger settlement, the capacity releases *and* the
    terminal journal event. Reading the journal straight after the settlement must
    already show it: a crash between two writes would leave a settled job whose
    terminal event nothing ever repairs."""
    harness = factory()
    jobs = harness.extra["jobs"]
    request, admission, lease = await _stream_job(harness)
    await harness.port.append(lease, b.events("a"))
    outcome = await jobs.complete(lease, b.outcome(request.request_id, harness,
                                                  tokens=b.usage(10, 1)))
    page, _ = await harness.port.read_owned(request.org_id, admission.job_handle, None, 10)
    terminal = [chunk for chunk in page if chunk.event_type is ChunkEventType.terminal]
    assert len(terminal) == 1, "the settlement committed without its terminal event"
    assert terminal[0].payload["settlement_state"] == outcome.settlement_state.value
    assert await harness.port.finalize_in_transaction(outcome) == terminal[0]
    # cancellation terminalizes through the same transaction
    other, admitted, lease = await _stream_job(harness, key="cancelled")
    cancelled = await jobs.cancel(other.org_id, admitted.job_handle)
    page, _ = await harness.port.read_owned(other.org_id, admitted.job_handle, None, 10)
    assert [chunk.event_type for chunk in page] == [ChunkEventType.terminal]
    assert page[0].payload["cause"] == cancelled.cause.value


async def dur_output__the_terminal_event_is_written_once_with_the_settlement(factory):
    """DUR-OUTPUT: the terminal journal event belongs to the settling transaction."""
    harness = factory()
    jobs = harness.extra["jobs"]
    request, admission, lease = await _stream_job(harness)
    await harness.port.append(lease, b.events("a"))
    proposal = b.outcome(request.request_id, harness, tokens=b.usage(10, 1))
    try:
        await harness.port.finalize_in_transaction(proposal)
    except errors.StateConflict:
        pass
    else:
        raise AssertionError("the terminal event was written before the settlement")
    outcome = await jobs.complete(lease, proposal)
    terminal = await harness.port.finalize_in_transaction(outcome)
    assert terminal.event_type is ChunkEventType.terminal
    assert await harness.port.finalize_in_transaction(outcome) == terminal     # replay safe
    page, _ = await harness.port.read_owned(request.org_id, admission.job_handle, None, 10)
    assert [chunk.event_type for chunk in page][-1] is ChunkEventType.terminal
    assert len([chunk for chunk in page if chunk.event_type is ChunkEventType.terminal]) == 1


def streamstore_cases():
    return [
        dur_output__append_commits_before_it_relays,
        dur_output__the_first_append_sets_the_publication_marker,
        dur_output__cursors_are_generation_then_sequence,
        api_stream__reads_are_bounded_and_ownership_checked,
        dur_output__a_pruned_prefix_is_an_explicit_replay_gap,
        dur_output__an_expired_journal_is_gone_not_regenerated,
        dur_cap__stored_unexpired_bytes_keep_counting,
        dur_cap__a_job_cannot_store_past_its_journal_reservation,
        dur_fence__a_stale_worker_cannot_append,
        dur_output__an_oversize_event_is_refused,
        dur_output__the_terminal_event_is_written_once_with_the_settlement,
        dur_settle__the_terminal_event_belongs_to_the_settling_transaction,
    ]
