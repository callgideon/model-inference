"""JobStore and StreamStore conformance: DUR-ADMIT, DUR-CAP, DUR-FENCE,
DUR-OUTPUT, DUR-SETTLE, DUR-OUTBOX and the stream half of API-STREAM.

Every case is an `async def` taking the factory, named for the oracle it serves.
Concurrency uses `asyncio.gather` and the injected clock: no sleeps, no network.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from .. import errors, money
from ..limits import DEFAULTS
from ..records import (ChunkEventType, Cursor, ExecutionMode, IndexEvent, JobState,
                       LeaseKind, MediaKind, OutboxKind, ReservationKind,
                       SettlementState, TerminalCause, states_for_cause)
from . import builders as b
from .harness import hook

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


async def _admit(harness, *, org_id=b.ORG_A, key_id=b.KEY_A, key="idem-1", grant="25.00",
                 mode=ExecutionMode.stream, price=None, **kw):
    """Fund the wallet, build a request and admit it. Returns (request, admission).

    `price` is the snapshot the store's price source is expected to answer with (r1 R45):
    the request carries no price and, since R53, neither does the call. It is asserted
    against what the store actually snapshotted, so a case that moves the price source and
    passes the wrong snapshot here fails rather than agreeing with itself.
    """
    if grant is not None:
        harness.extra["grant"](org_id, grant)
    request = b.request(harness, org_id=org_id, key_id=key_id, mode=mode, **kw)
    admission = await harness.port.admit(request, b.idem(request, key), ())
    if price is not None:
        assert admission.price_snapshot == price, "the store snapshotted a different price"
        assert admission.maximum_hold == b.hold_for(request, price), \
            "the store derived a hold the expected snapshot does not explain"
    return request, admission


async def _prepare(port, job_id, *, media=(), worker="prep-a"):
    """r1 R46: claim a preparation lease, then hand back the prepared refs under it.

    `prepared` is fenced on that lease, so the two calls always travel together; a case
    that wants to fence one of them apart does so explicitly.
    """
    lease = await port.claim_preparation(job_id, worker)
    return await port.prepared(lease, media)


async def _running(harness, jobs=None, **kw):
    """Admit, prepare, claim: a job with a live lease. Returns (request, admission, lease)."""
    port = jobs or harness.port
    if jobs is not None:
        harness = replace(harness, port=jobs)
    request, admission = await _admit(harness, **kw)
    await _prepare(port, request.request_id)
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
    again = await harness.port.admit(request, b.idem(request, "idem-1"), ())
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
            await harness.port.admit(request, b.idem(request, key), ())
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
        await harness.port.admit(request, b.idem(request, "idem-1", payload="different"), ())
    except errors.IdempotencyConflict as exc:
        assert errors.http_status(exc.code) == 409
    else:
        raise AssertionError("a changed payload was admitted under the same key")


async def dur_admit__lookup_reads_the_mapped_job_and_writes_nothing(factory):
    """DUR-ADMIT / R91: `lookup(org, idem)` answers the job an idempotency scope maps to -
    the admission, marked replayed, and its committed outcome - and admits, reserves and
    writes nothing. No key, an unmapped key and the same key in another org's scope answer
    None; a changed payload is the 409 `admit` gives; a scope naming another org than the
    caller is refused (R10); a mapping expired after its terminal state answers None."""
    harness = factory()
    request, admission = await _admit(harness)

    def state():
        return (harness.extra["balance"](request.org_id)["reserved"],
                len(harness.extra["active_jobs"]()), len(harness.extra["outbox"]()))

    before = state()
    found = await harness.port.lookup(request.org_id, b.idem(request, "idem-1"))
    assert found is not None, "a mapped scope answered nothing"
    mapped, outcome = found
    assert (mapped.request_id, mapped.job_handle) == (admission.request_id, admission.job_handle)
    assert mapped.replayed is True and outcome is None
    assert state() == before, "lookup wrote something"
    assert await harness.port.lookup(request.org_id, b.idem(request, None)) is None
    assert await harness.port.lookup(request.org_id, b.idem(request, "never-used")) is None
    try:
        await harness.port.lookup(request.org_id, b.idem(request, "idem-1", payload="changed"))
    except errors.IdempotencyConflict:
        pass
    else:
        raise AssertionError("lookup answered a changed payload under the same key")
    other = b.request(harness, org_id=b.ORG_B, key_id=b.KEY_B)
    assert await harness.port.lookup(b.ORG_B, b.idem(other, "idem-1")) is None
    try:
        await harness.port.lookup(b.ORG_B, b.idem(request, "idem-1"))
    except (errors.Forbidden, errors.NotFound):
        pass
    else:
        raise AssertionError("one organization read another's idempotency scope")
    cancelled = await harness.port.cancel(request.org_id, admission.job_handle)
    _mapped, outcome = await harness.port.lookup(request.org_id, b.idem(request, "idem-1"))
    assert outcome == cancelled
    harness.clock.advance(DEFAULTS.idempotency_ttl_s + 1)
    assert await harness.port.lookup(request.org_id, b.idem(request, "idem-1")) is None


async def dur_admit__crash_after_commit_then_retry_does_not_double_reserve(factory):
    """DUR-ADMIT: killed after commit, before the acknowledgment; the retry with
    the same key returns the committed acceptance and reserves nothing more."""
    harness = factory()
    plan = hook(harness, "failures")
    plan.crash_after_commit("admit")
    harness.extra["grant"](b.ORG_A, "25.00")
    request = b.request(harness)
    try:
        await harness.port.admit(request, b.idem(request, "idem-1"), ())
    except Exception as exc:                       # the answer was lost, not the commit
        assert type(exc).__name__ == "CrashAfterCommit", exc
    else:
        raise AssertionError("crash_after_commit did not fire")
    retried = await harness.port.admit(request, b.idem(request, "idem-1"), ())
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
        await harness.port.admit(request, b.idem(request, "idem-1"), ())
    except errors.IdempotencyExpired as exc:
        assert errors.http_status(exc.code) == 410
    else:
        raise AssertionError("an expired key silently admitted a new job")
    assert len(harness.extra["active_jobs"]()) == 0


async def dur_admit__the_tombstone_ttl_runs_from_the_terminal_state(factory):
    """DUR-ADMIT (01: "retain mappings/tombstones at least 24h **after terminal
    state**"): the clock starts when the job ends, not when it was admitted. A store
    measuring from `admitted_at` would drop the mapping of a long job the moment it
    finished, and the client's retry would buy a second billable job."""
    harness = factory(limits=DEFAULTS.replace(queue_wait_interactive_s=300,
                                              generation_timeout_s=300,
                                              lease_ttl_s=10_000, lease_heartbeat_s=1_000))
    request, admission = await _admit(harness, deadline_s=700)
    await _prepare(harness.port, admission.request_id)
    lease = await harness.port.claim(request.request_id, "worker-a")
    harness.clock.advance(250)                     # a long, honest attempt
    await harness.port.complete(lease, b.outcome(request.request_id, harness,
                                                 tokens=b.usage(1200, 340)))
    # 86,350s after *terminal* is still inside the 24h tombstone, though it is well
    # past 24h since admission
    harness.clock.advance(DEFAULTS.idempotency_ttl_s - 50)
    replay = await harness.port.admit(request, b.idem(request, "idem-1"), ())
    assert replay.replayed is True and replay.job_handle == admission.job_handle
    assert len(harness.extra["active_jobs"]()) == 0
    harness.clock.advance(100)                     # now past it
    try:
        await harness.port.admit(request, b.idem(request, "idem-1"), ())
    except errors.IdempotencyExpired:
        pass
    else:
        raise AssertionError("the tombstone outlived its TTL or admitted a second job")


async def dur_admit__an_active_jobs_mapping_never_expires(factory):
    """DUR-ADMIT (01: "active jobs never lose mappings"): the tombstone TTL starts at
    the terminal state. While a job is still running, its key keeps replaying however
    long the attempt takes - otherwise a slow job's retry would mint a second billable
    job beside the one still executing."""
    harness = factory(limits=DEFAULTS.replace(queue_wait_interactive_s=10_000,
                                              generation_timeout_s=10_000,
                                              lease_ttl_s=10_000, lease_heartbeat_s=1_000,
                                              idempotency_ttl_s=60))
    request, admission = await _admit(harness, deadline_s=10_000)
    await _prepare(harness.port, admission.request_id)
    lease = await harness.port.claim(request.request_id, "worker-a")
    harness.clock.advance(5_000)                   # far past the 60s tombstone TTL
    assert harness.extra["balance"](request.org_id)["reserved"] == admission.maximum_hold
    replay = await harness.port.admit(request, b.idem(request, "idem-1"), ())
    assert replay.replayed is True and replay.job_handle == admission.job_handle
    assert len(harness.extra["active_jobs"]()) == 1
    assert harness.extra["balance"](request.org_id)["reserved"] == admission.maximum_hold
    # only after it is terminal does the clock on the mapping start
    await harness.port.complete(lease, b.outcome(request.request_id, harness,
                                                 tokens=b.usage(1200, 340)))
    harness.clock.advance(61)
    try:
        await harness.port.admit(request, b.idem(request, "idem-1"), ())
    except errors.IdempotencyExpired:
        pass
    else:
        raise AssertionError("an expired tombstone replayed or admitted again")


async def dur_admit__tombstone_is_retained_after_the_terminal_state(factory):
    """DUR-ADMIT: within 24h of terminal, the key still replays the same identity."""
    harness = factory()
    request, admission = await _admit(harness)
    await _settle(harness, request, admission)
    harness.clock.advance(DEFAULTS.idempotency_ttl_s - 60)
    replay = await harness.port.admit(request, b.idem(request, "idem-1"), ())
    assert replay.job_handle == admission.job_handle and replay.replayed is True
    assert replay.state in {JobState.succeeded, JobState.failed, JobState.cancelled,
                            JobState.expired}


async def dur_admit__revocation_and_suspension_are_rechecked_in_the_transaction(factory):
    """DUR-ADMIT: a cached identity never bypasses revocation, suspension or
    entitlement (01's identity section, r1 R10). All three are rechecked in the
    admitting transaction, so a key revoked or a model withdrawn a millisecond ago
    cannot buy one more billable job."""
    harness = factory()
    revoke, suspend = hook(harness, "revoke_key"), hook(harness, "suspend_org")
    harness.extra["grant"](b.ORG_A, "25.00")
    revoke(b.KEY_A)
    request = b.request(harness)
    try:
        await harness.port.admit(request, b.idem(request, "k-revoked"), ())
    except errors.InvalidApiKey:
        pass
    else:
        raise AssertionError("a revoked key was admitted")
    hook(harness, "unrevoke_key")(b.KEY_A)
    suspend(b.ORG_A)
    request = b.request(harness)
    try:
        await harness.port.admit(request, b.idem(request, "k-suspended"), ())
    except errors.OrgSuspended:
        pass
    else:
        raise AssertionError("a suspended org was admitted")
    hook(harness, "unentitle")
    harness = factory()                            # a fresh store, with its own hooks
    harness.extra["grant"](b.ORG_A, "25.00")
    request = b.request(harness)
    hook(harness, "unentitle")(b.ORG_A, request.model_revision)
    try:
        await harness.port.admit(request, b.idem(request, "k-unentitled"), ())
    except errors.ModelNotEntitled as exc:
        assert errors.http_status(exc.code) == 403
    else:
        raise AssertionError("an unentitled org was admitted")
    assert harness.extra["balance"](b.ORG_A)["reserved"] == 0
    assert len(harness.extra["active_jobs"]()) == 0
    hook(harness, "entitle")(b.ORG_A, request.model_revision)
    admitted = await harness.port.admit(request, b.idem(request, "k-entitled"), ())
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
        await harness.port.admit(request_b, foreign, ())
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
        await harness.port.admit(foreign_media, b.idem(foreign_media, "foreign-media"), ())
    except errors.DomainError as exc:
        assert errors.http_status(exc.code) in (403, 404), exc.code
    else:
        raise AssertionError("a request carrying another org's media was admitted")


async def dur_admit__a_deadline_must_be_one_the_store_can_keep(factory):
    # D2 must run this case on the movable DB clock (the store's `db_now`), not a host clock.
    """DUR-ADMIT / r1 R29: a deadline already past is a job nothing may ever run, and
    one beyond preparation + queue + generation is a promise the store cannot keep -
    it would pin a preparation unit, a journal reservation and a hold for as long as
    the caller likes."""
    harness = factory()
    harness.extra["grant"](b.ORG_A, "25.00")
    journal_bytes = hook(harness, "journal_bytes")
    horizon = b.default_deadline_s(ExecutionMode.stream)
    for deadline_s in (-1, 0):
        request = b.request(harness, deadline_s=deadline_s)
        try:
            await harness.port.admit(request, b.idem(request, f"dl-{deadline_s}"), ())
        except errors.DomainError as exc:
            assert errors.http_status(exc.code) == 400, (deadline_s, exc.code)
        else:
            raise AssertionError(f"a deadline of {deadline_s}s was accepted")
    assert len(harness.extra["active_jobs"]()) == 0
    assert harness.extra["balance"](b.ORG_A)["reserved"] == 0
    assert journal_bytes() == 0
    # The store clamps a future deadline to its own clock/budgets. A gateway clock
    # slightly ahead is not an invalid customer request (F2.2 / R-3).
    for deadline_s in (horizon - 1, horizon, horizon + 1, horizon * 100):
        local = factory()
        local.extra["grant"](b.ORG_A, "25.00")
        request = b.request(local, deadline_s=deadline_s)
        original = request.deadline_at
        expected = min(original, local.clock.now() + timedelta(seconds=horizon))
        key = b.idem(request, f"dl-{deadline_s}")
        admitted = await local.port.admit(request, key, ())
        assert admitted.deadline_at == expected
        assert request.deadline_at == original, "admission mutated the caller's request"
        replay = await local.port.admit(request, key, ())
        assert replay.deadline_at == expected


async def dur_cap__a_credit_grant_is_never_negative(factory):
    """DUR-CAP / r1 R11 (02: "grants are positive"): a negative grant would be a debit
    outside the settlement path, taking credit from an organization with no usage row,
    no outcome and no audit trail. Corrections are compensating entries the store
    writes itself."""
    grant = harness_grant = harness = factory()
    balance = harness.extra["balance"]
    harness.extra["grant"](b.ORG_A, "10.00")
    before = balance(b.ORG_A)
    for amount in ("-1.00", "-0.00000001"):
        try:
            harness.extra["grant"](b.ORG_A, amount)
        except errors.DomainError as exc:
            assert errors.http_status(exc.code) == 400, exc.code
        except ValueError:
            pass                      # a typed domain error is preferred, but refusing counts
        else:
            raise AssertionError(f"a grant of {amount} was applied")
        assert balance(b.ORG_A) == before
    del grant, harness_grant


async def dur_admit__a_refused_admission_reserves_nothing(factory):
    """DUR-ADMIT: nothing is reserved before everything is checked. An unpriced
    model fails closed (01: "missing model/rates reject admission"), and the refused
    attempt leaves no journal reservation, no hold and no job behind - otherwise a
    client retrying an invalid request drains the journal budget.

    r1 R53 removed the "bad monetary input" half: there is no caller-supplied hold left
    to be bad. `money_input`'s boundary is still exercised by `grant`, `reserve` and
    `settle`, which do take caller money."""
    harness = factory()
    harness.extra["grant"](b.ORG_A, "25.00")
    journal_bytes = hook(harness, "journal_bytes")
    # r1 R45: "unpriced" is the price source having no row for the model, not a missing
    # request parameter. Omitting a parameter proved nothing about a store that reads its
    # own `price_versions` relation, which is every real store - and a store that read the
    # parameter again would pass a case that merely left it out. So the request here
    # *carries* one, generously priced, and it must have no influence whatsoever.
    set_price = hook(harness, "set_price")
    smuggled = b.price("0.00000001", "0.00000001").model_dump(mode="json")
    unpriced_model = f"{b.MODEL}-unpriced"
    unpriced = b.request(harness, model_revision=unpriced_model,
                         parameters={"price_snapshot": smuggled})
    for _ in range(3):
        try:
            await harness.port.admit(unpriced, b.idem(unpriced, "unpriced"), ())
        except errors.DomainError as exc:
            assert errors.http_status(exc.code) in (400, 403, 404), exc.code
        else:
            raise AssertionError("an unpriced model was admitted")
    # and withdrawing the price of a model that had one closes the same door, as does a
    # price source answering with another model's rates (which would settle this job at
    # them)
    for label, seeded in (("withdrawn", None),
                          ("mismatched", b.price(model_revision=unpriced_model))):
        set_price(b.MODEL, seeded)
        bad_price = b.request(harness)
        try:
            await harness.port.admit(bad_price, b.idem(bad_price, label), ())
        except errors.DomainError as exc:
            assert errors.http_status(exc.code) in (400, 403, 404), exc.code
        else:
            raise AssertionError(f"a {label} price was admitted")
    set_price(b.MODEL, b.DEFAULT_PRICE)
    assert len(harness.extra["active_jobs"]()) == 0
    assert harness.extra["balance"](b.ORG_A)["reserved"] == 0
    assert journal_bytes() == 0, "a refused admission leaked a journal reservation"
    # r1 R45: and a *priced* model whose request also carries a `price_snapshot` parameter
    # ignores it completely - the snapshot and the hold both come from the price source.
    # G1 rejects such a parameter outright as `unsupported_parameter`; the store simply
    # does not look at it.
    carrying = b.request(harness, parameters={"price_snapshot": smuggled})
    admitted = await harness.port.admit(carrying, b.idem(carrying, "carrying"), ())
    assert admitted.price_snapshot == b.DEFAULT_PRICE, \
        "the store read a price out of the request"
    assert admitted.maximum_hold == b.hold_for(carrying, b.DEFAULT_PRICE)
    assert admitted.maximum_hold > 0, "the smuggled price would have made the hold zero"
    # r1 R53: a caller cannot name money here at all, so the only remaining way for a
    # hold to be wrong is for the store to derive it wrongly - which is what
    # `dur_settle__a_price_change_never_undersizes_the_hold` pins. The one job now live is
    # the `carrying` admission just above, which the store *accepted* - correctly, at its
    # own price.
    assert [job.request.request_id for job in harness.extra["active_jobs"]()] == \
        [carrying.request_id]
    assert harness.extra["balance"](b.ORG_A)["reserved"] == admitted.maximum_hold


# --------------------------------------------------------------------------
# DUR-CAP
# --------------------------------------------------------------------------
async def dur_admit__the_token_ceilings_are_range_checked(factory):
    """DUR-ADMIT / r1 R55: the store range-checks the request's token ceilings, and a
    refused admission changes nothing at all.

    `Field(ge=0)` on the record let `0/0` through, and a zero output ceiling makes a
    **zero hold**: the job was admitted having reserved nothing, so whatever the engine
    produced was unmetered output against an empty reservation. `40000/4096` was admitted
    too, although 01 caps a request at `MAX_CONTEXT_TOKENS` after preprocessing, so the
    reserved envelope was a promise the model could not keep. The store owns the limits, so
    the store checks them - inside the transaction, like every other admission check.
    """
    harness = factory()
    harness.extra["grant"](b.ORG_A, "25.00")
    journal_bytes = hook(harness, "journal_bytes")
    ceiling = DEFAULTS.max_output_tokens
    context = DEFAULTS.max_context_tokens
    refused = (
        (0, 0, "a zero output ceiling reserves nothing and meters nothing"),
        (30_720, 0, "a zero output ceiling"),
        (0, 2_048, "a zero input ceiling"),
        (-1, 16, "a negative input ceiling"),
        (16, -1, "a negative output ceiling"),
        (30_720, ceiling + 1, "an output ceiling past MAX_OUTPUT_TOKENS"),
        # t05: and one whose context sum is comfortably legal, so the **upper bound itself**
        # is what refuses it. Every other over-ceiling row above also breaks the context
        # cap, so dropping `<= MAX_OUTPUT_TOKENS` left them all still failing.
        (100, ceiling + 1, "an output ceiling past MAX_OUTPUT_TOKENS with a legal context sum"),
        (40_000, 4_096, "a request past MAX_CONTEXT_TOKENS"),
        (context, ceiling, "input at the context cap plus any output"),
    )
    for max_input, max_output, what in refused:
        before = harness.extra["balance"](b.ORG_A)
        try:
            request = b.request(harness, max_input_tokens=max_input,
                                max_output_tokens=max_output)
        except Exception:
            # The record refuses a negative outright; the port must refuse it too when it
            # can be built, and both answers are "not admitted".
            continue
        try:
            await harness.port.admit(request, b.idem(request, f"ceil-{max_input}-{max_output}"),
                                     ())
        except errors.DomainError as exc:
            assert exc.code in ("invalid_request", "context_length_exceeded"), exc.code
            assert errors.http_status(exc.code) == 400
        else:
            raise AssertionError(f"admitted {what}: {max_input}/{max_output}")
        # state exactly unchanged: no job, no hold, no journal reservation
        assert len(harness.extra["active_jobs"]()) == 0, what
        assert harness.extra["balance"](b.ORG_A) == before, what
        assert journal_bytes() == 0, what
    # and the boundary is inclusive on both sides: the largest legal request is admitted,
    # with a hold that is not zero.
    largest = b.request(harness, max_input_tokens=context - ceiling, max_output_tokens=ceiling)
    admitted = await harness.port.admit(largest, b.idem(largest, "ceil-largest"), ())
    assert admitted.maximum_hold > 0
    # exactly at the output ceiling, with room to spare in the context: admitted.
    at_ceiling = b.request(harness, max_input_tokens=100, max_output_tokens=ceiling)
    admitted_at_ceiling = await harness.port.admit(
        at_ceiling, b.idem(at_ceiling, "ceil-exact"), ())
    assert admitted_at_ceiling.maximum_hold > 0
    smallest_request = b.request(harness, max_input_tokens=1, max_output_tokens=1)
    smallest = await harness.port.admit(smallest_request,
                                        b.idem(smallest_request, "ceil-smallest"), ())
    assert smallest.maximum_hold > 0, "even the smallest request reserves something"


async def dur_cap__total_org_and_key_limits_reject_with_retry_guidance(factory):
    """DUR-CAP: capacity is refused with 429 and Retry-After, never oversubscribed.
    Each of the three scopes is tripped on its own, with the other two slack, so a
    store that enforces only one of them cannot pass."""
    # per-key: 2 of 2 on KEY_A while the org and total ceilings are far away
    harness = factory(limits=DEFAULTS.replace(max_active_jobs_per_key=2, max_active_jobs=64,
                                              max_active_jobs_per_org=16))
    harness.extra["grant"](b.ORG_A, "100.00")
    for n in range(2):
        request = b.request(harness)
        await harness.port.admit(request, b.idem(request, f"key-{n}"), ())
    request = b.request(harness)
    try:
        await harness.port.admit(request, b.idem(request, "key-over"), ())
    except errors.CapacityExhausted as exc:
        assert errors.http_status(exc.code) == 429 and exc.retry_after_s >= 1
    else:
        raise AssertionError("the per-key active job limit was exceeded")
    # a different key in the same org still fits under the org limit
    request = b.request(harness, key_id=b.KEY_A2)
    await harness.port.admit(request, b.idem(request, "key-other"), ())
    assert len(harness.extra["active_jobs"]()) == 3

    # per-org: two keys, 3 of 3 for ORG_A, with the total ceiling and the per-key
    # ceiling both slack, so only the org counter can refuse it
    harness = factory(limits=DEFAULTS.replace(max_active_jobs_per_org=3, max_active_jobs=64,
                                              max_active_jobs_per_key=8))
    harness.extra["grant"](b.ORG_A, "100.00")
    for n, key_id in enumerate((b.KEY_A, b.KEY_A2, b.KEY_A)):
        request = b.request(harness, key_id=key_id)
        await harness.port.admit(request, b.idem(request, f"org-{n}"), ())
    request = b.request(harness, key_id=b.KEY_A2)
    try:
        await harness.port.admit(request, b.idem(request, "org-over"), ())
    except errors.CapacityExhausted:
        pass
    else:
        raise AssertionError("the per-org active job limit was exceeded")
    # ... while another organization is unaffected by ORG_A's ceiling
    harness.extra["grant"](b.ORG_B, "100.00")
    other = b.request(harness, org_id=b.ORG_B, key_id=b.KEY_B)
    await harness.port.admit(other, b.idem(other, "org-b"), ())
    assert len(harness.extra["active_jobs"](org_id=b.ORG_A)) == 3

    # total: two organizations filling 4 of 4 with both per-scope ceilings slack
    harness = factory(limits=DEFAULTS.replace(max_active_jobs=4, max_active_jobs_per_org=16,
                                              max_active_jobs_per_key=8))
    # Four keys, each its own organization's: a key never spans two organizations.
    for org_id, key_id in ((b.ORG_A, b.KEY_A), (b.ORG_A, b.KEY_A2),
                           (b.ORG_B, b.KEY_B), (b.ORG_B, b.KEY_B2)):
        harness.extra["grant"](org_id, "100.00")
        request = b.request(harness, org_id=org_id, key_id=key_id)
        await harness.port.admit(request, b.idem(request, f"all-{org_id}-{key_id}"), ())
    request = b.request(harness, org_id=b.ORG_A, key_id=b.KEY_A)
    try:
        await harness.port.admit(request, b.idem(request, "all-over"), ())
    except errors.CapacityExhausted:
        pass
    else:
        raise AssertionError("the total active job limit was exceeded")
    assert len(harness.extra["active_jobs"]()) == 4


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
        admitted.append(await harness.port.admit(request, b.idem(request, f"p-{n}"), ()))
    request = b.request(harness)
    try:
        await harness.port.admit(request, b.idem(request, "p-over"), ())
    except errors.CapacityExhausted as exc:
        assert errors.http_status(exc.code) == 429 and exc.retry_after_s >= 1
    else:
        raise AssertionError("preparation capacity was oversubscribed")
    assert len(harness.extra["active_jobs"]()) == 2      # job slots were still free
    kinds = {r.kind for r in admitted[0].reservations if r.active}
    assert {kind.value for kind in kinds} >= {"preparation"}
    await _prepare(harness.port, admitted[0].request_id)
    after = await harness.port.admit(request, b.idem(request, "p-after"), ())
    assert after.state is JobState.preparing


async def dur_cap__journal_reservation_must_fit_the_global_budget(factory):
    """DUR-CAP: when the journal reservation cannot be taken, admission is refused
    before acceptance, with its own code."""
    limits = DEFAULTS.replace(journal_total_bytes=DEFAULTS.journal_job_reserve_bytes * 2)
    harness = factory(limits=limits)
    harness.extra["grant"](b.ORG_A, "100.00")
    for n in range(2):
        request = b.request(harness)
        await harness.port.admit(request, b.idem(request, f"j-{n}"), ())
    request = b.request(harness)
    try:
        await harness.port.admit(request, b.idem(request, "j-over"), ())
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
        await harness.port.admit(request, b.idem(request, "poor"), ())
    except errors.InsufficientCredit as exc:
        assert errors.http_status(exc.code) == 402
    else:
        raise AssertionError("a hold larger than the balance was accepted")
    balance = harness.extra["balance"](request.org_id)
    assert balance["available"] >= 0 and balance["reserved"] == 0


async def dur_cap__a_hold_is_checked_against_available_not_the_ledger(factory):
    """DUR-CAP (01: "available = total minus reserved"): the gate is the **available**
    balance, so an outstanding hold reduces what the next admission may reserve. A
    store comparing against the ledger total would let two jobs reserve the same
    credit and settle both, taking the wallet negative."""
    harness = factory()
    harness.extra["grant"](b.ORG_A, "0.01000000")
    first = b.request(harness, max_input_tokens=30_000, max_output_tokens=2_000)
    # r1 R53: the expected hold, which the store derives for itself.
    hold = b.hold_for(first)
    assert hold * 2 > Decimal("0.01") >= hold, "the case needs two holds to overflow one wallet"
    admitted = await harness.port.admit(first, b.idem(first, "avail-1"), ())
    assert admitted.maximum_hold == hold, "the store derived a different hold"
    balance = harness.extra["balance"](b.ORG_A)
    assert balance["reserved"] == hold and balance["available"] == Decimal("0.01") - hold
    second = b.request(harness, max_input_tokens=30_000, max_output_tokens=2_000)
    try:
        await harness.port.admit(second, b.idem(second, "avail-2"), ())
    except errors.InsufficientCredit as exc:
        assert errors.http_status(exc.code) == 402
    else:
        raise AssertionError("two holds were reserved against one wallet's credit")
    after = harness.extra["balance"](b.ORG_A)
    assert after["reserved"] == hold and after["available"] >= 0
    assert len(harness.extra["active_jobs"]()) == 1


async def dur_cap__a_negative_maximum_hold_is_refused(factory):
    """DUR-CAP / r1 R53: the hold is the **store's**, and a caller has no way to name one.

    This case used to hand `admit` a negative hold and assert a typed refusal. R53 takes
    the argument away, so that defect is unrepresentable and what needs pinning instead is
    the property it was standing in for: the reserved total is exactly the hold the store
    derived from its own snapshot, it is never negative, and it is what the balance gate
    compares against. A caller that cannot supply money cannot fabricate credit.
    """
    harness = factory()
    harness.extra["grant"](b.ORG_A, "0")
    request = b.request(harness)
    try:
        await harness.port.admit(request, b.idem(request, "no-credit"), ())
    except Exception as exc:
        # A **typed** refusal: a store that let a `ValueError` or a validation error out
        # of `admit` would answer 500 to a request it knows it cannot fund.
        assert isinstance(exc, errors.DomainError), f"untyped refusal: {type(exc).__name__}"
        assert exc.code == "insufficient_credit", exc.code
        assert errors.http_status(exc.code) == 402
    else:
        raise AssertionError("an unfunded admission reserved a hold")
    balance = harness.extra["balance"](b.ORG_A)
    assert balance["reserved"] == 0 and balance["available"] == 0
    assert len(harness.extra["active_jobs"]()) == 0
    # funded, the derived hold is positive, is the reserved total, and is the ceiling
    # formula applied to the admitted snapshot - not a number anybody handed the store
    harness.extra["grant"](b.ORG_A, "25.00")
    funded = b.request(harness)
    admission = await harness.port.admit(funded, b.idem(funded, "funded"), ())
    assert admission.maximum_hold > 0
    assert admission.maximum_hold == admission.price_snapshot.maximum_hold(
        funded.max_input_tokens, funded.max_output_tokens)
    assert harness.extra["balance"](b.ORG_A)["reserved"] == admission.maximum_hold


async def dur_cap__concurrent_admissions_never_oversubscribe(factory):
    """DUR-CAP: eight concurrent admissions against room for three: exactly three
    win, the balance never goes negative and no reservation is double counted."""
    harness = factory(limits=DEFAULTS.replace(max_active_jobs=3, max_active_jobs_per_org=3,
                                              max_active_jobs_per_key=3))
    harness.extra["grant"](b.ORG_A, "100.00")
    requests = [b.request(harness) for _ in range(8)]

    async def attempt(index, request):
        try:
            return await harness.port.admit(request, b.idem(request, f"c-{index}"), ())
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
    queued = await _prepare(harness.port, admission.request_id)
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


async def dur_fence__preparation_is_claimed_and_fenced_like_execution(factory):
    """DUR-FENCE / r1 R46: `prepared` is fenced on a preparation lease.

    Preparation used to be addressed by job handle and needed no token at all, so a
    superseded preparation worker returning late could queue the job with its own
    (possibly half-written) refs. It is now the same discipline as execution: its own
    generation counter, one live lease at a time, owner and expiry checked, and the R29
    phase deadline enforced in the same call. The two kinds of lease are not
    interchangeable, and nothing renews a preparation lease.
    """
    harness = factory()
    request, admission = await _admit(harness)
    lease = await harness.port.claim_preparation(request.request_id, "prep-a")
    assert lease.kind is LeaseKind.preparation and lease.generation == 1
    assert lease.job_id == request.request_id            # internal ops speak job ids
    assert lease.acquired_at == harness.clock.now()      # database time
    assert lease.generation_deadline_at == admission.preparation_deadline_at
    assert lease.first_token_deadline_at is None, "a preparation lease has no first token"
    # r1 R52: a preparation lease is *short* - shorter than an inference one - so a lost
    # preparation host is reaped while the phase budget still has room for the retries R46
    # allows. With one 120 s TTL against a 120 s budget there was never room for any.
    assert lease.expires_at == harness.clock.at(DEFAULTS.preparation_lease_ttl_s)
    assert DEFAULTS.preparation_lease_ttl_s < DEFAULTS.lease_ttl_s
    assert lease.expires_at <= lease.generation_deadline_at, \
        "a preparation lease may not outlive the phase it fences"
    # And when the phase is *shorter* than the lease TTL, the clamp is what enforces that -
    # the only configuration where it is observable, and the one a tight media budget
    # produces. An unclamped lease would outlive the phase it fences, so the expiry
    # refusal would stop firing and the phase deadline would be the only thing left.
    short_limits = DEFAULTS.replace(preparation_timeout_s=10.0)
    short = factory(limits=short_limits)
    short_request, short_admission = await _admit(
        short, key="prep-short", deadline_s=b.default_deadline_s(limits=short_limits))
    short_lease = await short.port.claim_preparation(short_request.request_id, "prep-a")
    assert DEFAULTS.preparation_lease_ttl_s > 10.0, "the case needs the phase to be shorter"
    assert short_lease.expires_at == short_admission.preparation_deadline_at, \
        "a preparation lease was granted past the phase deadline"
    # one live preparation at a time: two workers writing prepared refs for one job is
    # the media equivalent of two workers appending output
    try:
        await harness.port.claim_preparation(request.request_id, "prep-b")
    except errors.NotClaimable:
        pass
    else:
        raise AssertionError("a job was prepared by two workers at once")
    # r1 R46/s13: a **superseded** worker's heartbeat is fenced like every other mutation.
    # w1 holds generation 1; once w2 has claimed generation 2, w1's heartbeat must be
    # `stale_lease` - a heartbeat that skipped the fence would renew *w2's* lease on w1's
    # behalf, so the worker that lost the job would keep the job alive for the one that has
    # it, and two preparations would run believing they were fenced.
    superseded = factory()
    s13_request, _ = await _admit(superseded, key="prep-superseded")
    w1 = await superseded.port.claim_preparation(s13_request.request_id, "w1")
    superseded.clock.advance(DEFAULTS.preparation_lease_ttl_s + 1)
    await superseded.port.recover()                       # reaps w1's expired lease
    w2 = await superseded.port.claim_preparation(s13_request.request_id, "w2")
    assert w2.generation == w1.generation + 1
    before_w2 = w2.expires_at
    for forged in (w1, w1.model_copy(update={"worker_id": "w2"}),
                   w2.model_copy(update={"generation": w1.generation})):
        try:
            await superseded.port.heartbeat(forged)
        except errors.StaleLease:
            pass
        else:
            raise AssertionError("a superseded preparation worker renewed a lease")
    superseded.clock.advance(1)
    live = await superseded.port.heartbeat(w2)
    assert live.expires_at > before_w2, "the holder's own heartbeat still renews"
    assert live.worker_id == "w2" and live.generation == w2.generation

    # r1 R52: a preparation lease renews like any other - a worker doing 100 s of
    # legitimate transcoding has to say so - but **never past the phase deadline**, or a
    # renewal would buy preparation time the job was never granted.
    harness.clock.advance(DEFAULTS.preparation_lease_ttl_s / 2)
    renewed = await harness.port.heartbeat(lease)
    assert renewed.kind is LeaseKind.preparation and renewed.generation == lease.generation
    assert renewed.expires_at > lease.expires_at, "a preparation heartbeat renews nothing"
    assert renewed.expires_at <= renewed.generation_deadline_at
    lease = renewed
    # Right up against the phase deadline the renewal is clamped, not extended. On its own
    # harness, because this one has to move the clock most of the way through the phase and
    # the fencing checks below need a lease that is still live.
    # A worker doing a long, legitimate transcode heartbeats its way across the phase -
    # which is the whole reason the lease may be shorter than the work - and the renewal
    # right at the end is clamped to the phase deadline rather than extending past it.
    clamping = factory()
    clamp_request, clamp_admission = await _admit(clamping, key="prep-clamp")
    clamp_lease = await clamping.port.claim_preparation(clamp_request.request_id, "prep-a")
    step = DEFAULTS.preparation_lease_ttl_s * 0.75
    while clamping.clock.now() + timedelta(seconds=step) < clamp_admission.preparation_deadline_at:
        clamping.clock.advance(step)
        beat = await clamping.port.heartbeat(clamp_lease)
        # Strictly later until the clamp bites, then equal to the phase deadline: a
        # renewal extends the lease, never the phase.
        if clamp_lease.expires_at < clamp_admission.preparation_deadline_at:
            assert beat.expires_at > clamp_lease.expires_at, "a heartbeat did not renew"
        assert beat.expires_at <= clamp_admission.preparation_deadline_at
        clamp_lease = beat
    assert clamp_lease.expires_at == clamp_admission.preparation_deadline_at, \
        "a preparation heartbeat bought time past the phase deadline"
    assert clamping.clock.now() > clamp_request.created_at + timedelta(
        seconds=DEFAULTS.preparation_lease_ttl_s), \
        "the loop must outlast one lease, or nothing was renewed across it"
    # and it cannot stand in for an inference lease
    for call, expected in ((harness.port.complete(lease, b.outcome(request.request_id, harness)),
                            errors.StaleLease),
                           (harness.port.load_work(lease.model_copy(
                               update={"kind": LeaseKind.inference,
                                       "first_token_deadline_at":
                                           lease.generation_deadline_at})), errors.StaleLease)):
        try:
            await call
        except expected:
            pass
        else:
            raise AssertionError(f"{expected.__name__} was not raised for a preparation lease")
    # a foreign worker, a stale generation and an inference token are all fenced out of
    # `prepared`
    for forged in (lease.model_copy(update={"worker_id": "prep-b"}),
                   lease.model_copy(update={"generation": lease.generation + 1}),
                   lease.model_copy(update={"kind": LeaseKind.inference,
                                            "first_token_deadline_at":
                                                lease.generation_deadline_at})):
        try:
            await harness.port.prepared(forged, ())
        except errors.StaleLease:
            pass
        else:
            raise AssertionError("a forged preparation lease queued the job")
    stored, _ = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert stored.state is JobState.preparing, "a fenced preparation worker moved the job"
    # r1 R10: prepared refs are the job's own tenant's, checked at the write. A foreign
    # ref reaching `prepared` means preparation produced an object for the wrong
    # organization, and storing it would file another tenant's content under this job.
    try:
        await harness.port.prepared(lease, (b.media(b.ORG_B),))
    except (errors.Forbidden, errors.NotFound) as exc:
        assert errors.http_status(exc.code) in (403, 404), exc.code
    else:
        raise AssertionError("prepared accepted another organization's media")
    stored_after, _ = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert stored_after.state is JobState.preparing, "a refused prepared moved the job"
    queued = await harness.port.prepared(lease, (b.media(b.ORG_A),))
    assert queued.state is JobState.queued
    # and the spent lease is spent: the phase is over, so the token fences nothing
    try:
        await harness.port.prepared(lease, ())
    except errors.StaleLease:
        pass
    else:
        raise AssertionError("a spent preparation lease queued the job again")
    # The sharp edge of "not interchangeable": this preparation lease names the *same*
    # job, worker and generation as the inference lease that follows it, and is still
    # live. Only its `kind` says it may not settle the job.
    assert lease.generation == 1 and harness.clock.now() < lease.expires_at
    inference = await harness.port.claim(request.request_id, lease.worker_id)
    assert inference.generation == lease.generation and inference.kind is LeaseKind.inference
    try:
        await harness.port.complete(lease, b.outcome(request.request_id, harness))
    except errors.StaleLease:
        pass
    else:
        raise AssertionError("a preparation lease settled the job")
    # An expired preparation lease is stale before anything reaps it, and before the
    # preparation deadline it would be terminalized on: a short lease TTL separates the
    # two, so this is the expiry check and not R29's phase deadline.
    tight = factory()
    expiring_request, expiring_admission = await _admit(tight, key="prep-expiry")
    expiring = await tight.port.claim_preparation(expiring_request.request_id, "prep-a")
    tight.clock.advance(tight_ttl := DEFAULTS.preparation_lease_ttl_s + 1)
    assert tight_ttl < DEFAULTS.preparation_timeout_s, "the phase deadline would fire first"
    try:
        await tight.port.prepared(expiring, ())
    except errors.StaleLease:
        pass
    else:
        raise AssertionError("an expired preparation lease queued the job")
    left, still_open = await tight.port.get_owned(expiring_request.org_id,
                                                 expiring_admission.job_handle)
    assert left.state is JobState.preparing and still_open is None, \
        "the expired preparation worker queued the job anyway"


async def dur_fence__load_work_is_fenced_and_hands_out_nothing_otherwise(factory):
    """DUR-FENCE / r1 R46: `load_work` is the only way a lease holder reads what it must
    execute, and it is fenced like a mutation.

    That matters more than it looks: if a worker is handed the request out of band then
    being fenced costs it nothing, because it still holds everything it needs to run. A
    stale, foreign or wrong-kind lease gets a typed refusal and **no data**.
    """
    harness = factory()
    refs = (b.media(b.ORG_A),)
    request, admission = await _admit(harness, refs=refs)
    preparation = await harness.port.claim_preparation(request.request_id, "prep-a")
    before = await harness.port.load_work(preparation)
    assert before.request == request and before.media_refs == refs
    assert before.prepared_refs == (), "nothing is prepared before preparation runs"
    assert before.price_snapshot == admission.price_snapshot
    assert before.budgets == admission.budgets
    prepared_refs = (b.media(b.ORG_A, kind=MediaKind.upload),)
    await harness.port.prepared(preparation, prepared_refs)
    lease = await harness.port.claim(request.request_id, "worker-a")
    work = await harness.port.load_work(lease)
    assert work.prepared_refs == prepared_refs and work.media_refs == refs
    assert work.price_snapshot == admission.price_snapshot
    # r1 R53/R4: and it keeps carrying them after the store is reconfigured underneath it.
    # `load_work` reading *current* values would hand a running worker a price the customer
    # was never quoted and budgets the job was never granted - the same mistake as pricing
    # at settlement, one phase earlier.
    hook(harness, "set_price")(b.MODEL, b.price("9.99", "9.99"))
    hook(harness, "retune")(generation_timeout_s=DEFAULTS.generation_timeout_s * 3,
                            ttft_timeout_s=DEFAULTS.ttft_timeout_s * 3,
                            tpot_stall_s=DEFAULTS.tpot_stall_s * 3,
                            queue_wait_interactive_s=DEFAULTS.queue_wait_interactive_s * 3)
    after = await harness.port.load_work(lease)
    assert after.price_snapshot == admission.price_snapshot, \
        "load_work reported the current price, not the admitted snapshot"
    assert after.budgets == admission.budgets, \
        "load_work reported current budgets, not the R4 snapshot"
    assert after.budgets.generation_s == DEFAULTS.generation_timeout_s
    hook(harness, "set_price")(b.MODEL, b.DEFAULT_PRICE)
    # every way of not holding the lease: another worker, a stale generation, the wrong
    # kind, an unknown job, and an expired lease
    forgeries = (lease.model_copy(update={"worker_id": "worker-b"}),
                 lease.model_copy(update={"generation": lease.generation + 1}),
                 lease.model_copy(update={"kind": LeaseKind.preparation,
                                          "first_token_deadline_at": None}),
                 lease.model_copy(update={"job_id": harness.ids.uuid()}))
    for forged in forgeries:
        try:
            await harness.port.load_work(forged)
        except (errors.StaleLease, errors.NotFound) as exc:
            assert exc.code in ("stale_lease", "not_found"), exc.code
        else:
            raise AssertionError("load_work handed work to a lease holder it had fenced")
    harness.clock.advance(DEFAULTS.lease_ttl_s + 1)
    try:
        await harness.port.load_work(lease)
    except (errors.StaleLease, errors.AlreadyTerminal):
        pass
    else:
        raise AssertionError("an expired lease still loaded its work")


async def dur_output__a_lost_preparation_worker_is_reaped_within_bounds(factory):
    """DUR-OUTPUT / r1 R46 + R29: a preparation host that dies is reaped, and the job
    stays preparable - but not for ever.

    `recover` releases an expired preparation lease and leaves the job `preparing`, so a
    worker lost with most of its budget left costs a retry rather than the request. The
    bound is the same one the prepublication inference path uses,
    `MAX_PREPUBLICATION_RETRIES` further claims (three in total), and
    `preparation_deadline_at` bounds it in wall-clock terms whatever the count says.

    **On default limits** (r1 R52). This used to widen `preparation_timeout_s` to 600 s and
    shrink `lease_ttl_s` to 10 s, which made the three claims reachable in the case and
    nowhere else: with one 120 s lease TTL against a 120 s preparation budget the first
    reap arrived exactly as the phase expired, so a real deployment got no retries at all
    and the case was describing a profile nobody runs. `PREPARATION_LEASE_TTL_S` = 30 is
    what makes the bound real, and this now proves it against the shipped numbers.
    """
    limits = DEFAULTS
    harness = factory()
    request, admission = await _admit(harness)
    assert limits.preparation_lease_ttl_s * (limits.max_prepublication_retries + 1) \
        <= limits.preparation_timeout_s, \
        "the default profile must leave room for every retry R46 allows"
    outbox = harness.extra["outbox"]
    first = await harness.port.claim_preparation(request.request_id, "prep-a")
    dispatches = len([e for e in outbox(request.request_id)
                      if e.kind is OutboxKind.prepare_dispatch])
    harness.clock.advance(limits.preparation_lease_ttl_s + 1)
    await harness.port.recover()
    stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert stored.state is JobState.preparing and outcome is None, \
        "a lost preparation worker cost the whole request"
    # and the reaper puts it back on the queue: the admission's own dispatch event was
    # consumed by the worker that died, so without a new one nothing ever tries again.
    assert len([e for e in outbox(request.request_id)
                if e.kind is OutboxKind.prepare_dispatch]) == dispatches + 1, \
        "the reaped preparation was never redispatched"
    second = await harness.port.claim_preparation(request.request_id, "prep-b")
    assert second.generation == first.generation + 1, "the reaped attempt was not counted"
    try:
        await harness.port.prepared(first, ())
    except errors.StaleLease:
        pass
    else:
        raise AssertionError("the reaped worker came back and queued the job")
    # the third claim is the last one: `max_prepublication_retries` further attempts
    harness.clock.advance(limits.preparation_lease_ttl_s + 1)
    await harness.port.recover()
    third = await harness.port.claim_preparation(request.request_id, "prep-c")
    assert third.generation == 3
    assert third.acquired_at < admission.preparation_deadline_at, \
        "three claims must fit inside the default preparation budget"
    # r1 R52: after the last permitted loss the reaper settles the job itself. Emitting
    # another `prepare_dispatch` would queue work whose only possible outcome is
    # `claim_preparation` terminalizing it - a dispatch that exists to fail - and the hold
    # would stay reserved until some worker happened to pick it up.
    before_last = len([e for e in outbox(request.request_id)
                       if e.kind is OutboxKind.prepare_dispatch])
    harness.clock.advance(limits.preparation_lease_ttl_s + 1)
    produced = await harness.port.recover()
    assert any(getattr(item, "cause", None) is TerminalCause.preparation_failed
               for item in produced), "the reaper did not settle the exhausted preparation"
    assert len([e for e in outbox(request.request_id)
                if e.kind is OutboxKind.prepare_dispatch]) == before_last, \
        "the reaper redispatched work that could only terminalize"
    try:
        await harness.port.claim_preparation(request.request_id, "prep-d")
    except (errors.NotClaimable, errors.AlreadyTerminal):
        pass
    else:
        raise AssertionError("preparation retries were unbounded")
    stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert outcome is not None and outcome.cause is TerminalCause.preparation_failed
    assert harness.extra["balance"](request.org_id)["reserved"] == 0, \
        "an exhausted preparation left the hold behind"
    # r1 R46/q08: the count check in `claim_preparation` is observable on its own, with
    # **no `recover` in between** - a pool whose workers simply re-claim after their lease
    # expires must still be bounded, because nothing guarantees a reaper has run. Three
    # claims at t=0, 30 and 60, then `not_claimable` at t=90.
    unreaped = factory()
    q08_request, q08 = await _admit(unreaped, key="prep-unreaped")
    for attempt in range(limits.max_prepublication_retries + 1):
        lease = await unreaped.port.claim_preparation(q08_request.request_id, f"w{attempt}")
        assert lease.generation == attempt + 1
        unreaped.clock.advance(limits.preparation_lease_ttl_s)
    try:
        await unreaped.port.claim_preparation(q08_request.request_id, "one-too-many")
    except (errors.NotClaimable, errors.AlreadyTerminal) as exc:
        assert exc.code in ("not_claimable", "already_terminal"), exc.code
    else:
        raise AssertionError("preparation retries were unbounded without a reaper")
    _q08_stored, q08_outcome = await unreaped.port.get_owned(q08_request.org_id, q08.job_handle)
    assert q08_outcome is not None and q08_outcome.cause is TerminalCause.preparation_failed
    assert unreaped.extra["balance"](q08_request.org_id)["reserved"] == 0

    # and the wall-clock bound holds independently: past the preparation instant the job
    # is `preparation_failed` whatever the attempt count
    fresh_request, fresh = await _admit(harness, key="prep-deadline")
    await harness.port.claim_preparation(fresh_request.request_id, "prep-a")
    harness.clock.advance(limits.preparation_timeout_s + 1)
    await harness.port.recover()
    _, overdue = await harness.port.get_owned(fresh_request.org_id, fresh.job_handle)
    assert overdue is not None and overdue.cause is TerminalCause.preparation_failed


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


async def dur_fence__an_overdue_inference_lease_terminalizes_in_the_same_call(factory):
    """DUR-FENCE / r1 R29 + R55: the ordering is claimed for **both** paths.

    R29 names `heartbeat`, `append`, `prepared` and `complete`: past a phase deadline the
    store terminalizes the job *in that same operation*, so the outcome never depends on
    reaper timing. Only the preparation path was pinned, so putting the lease-expiry check
    back in front of the deadline check on the inference path survived - and that is the
    same defect B1 was: a worker whose lease and deadline have both passed gets
    `stale_lease`, the job stays `running`, and the customer's hold stays reserved.

    Three states, deliberately separated by a lease TTL shorter than the generation budget:

    * lease expired **and** the deadline passed -> `already_terminal`, the job is
      `deadline_exceeded`, and the hold is released in that call;
    * lease expired **before** any deadline -> `stale_lease`, state unchanged;
    * a superseded generation -> `stale_lease`, and it never terminalizes on the new
      holder's behalf.
    """
    limits = DEFAULTS.replace(lease_ttl_s=120.0, generation_timeout_s=130.0)
    deadline_s = b.default_deadline_s(ExecutionMode.stream, limits)

    async def overdue(key):
        """A running job whose lease expires at t+120 and whose generation ends at t+130."""
        harness = factory(limits=limits)
        request, admission = await _admit(harness, key=key, deadline_s=deadline_s)
        await _prepare(harness.port, request.request_id)
        lease = await harness.port.claim(request.request_id, "worker-a")
        assert lease.expires_at == harness.clock.at(limits.lease_ttl_s)
        assert lease.generation_deadline_at == harness.clock.at(limits.generation_timeout_s)
        assert lease.expires_at < lease.generation_deadline_at, \
            "the case needs the lease to expire before the deadline"
        return harness, request, admission, lease

    # 1. both passed: every fenced operation terminalizes here and releases the hold.
    for operation in ("complete", "heartbeat", "append", "load_work"):
        harness, request, admission, lease = await overdue(f"t02-{operation}")
        stream = hook(harness, "stream")
        funded = harness.extra["balance"](request.org_id)
        assert funded["reserved"] == admission.maximum_hold > 0
        harness.clock.advance(limits.generation_timeout_s + 1)
        invoke = {
            "complete": lambda: harness.port.complete(lease,
                                                      b.outcome(request.request_id, harness)),
            "heartbeat": lambda: harness.port.heartbeat(lease),
            "append": lambda: stream.append(lease, b.events("late")),
            "load_work": lambda: harness.port.load_work(lease),
        }[operation]
        try:
            await invoke()
        except errors.AlreadyTerminal:
            pass
        else:
            raise AssertionError(f"{operation} past the generation deadline did not refuse")
        stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
        assert outcome is not None, \
            f"{operation} refused but left the job running: R29 wants it settled here"
        assert outcome.cause is TerminalCause.deadline_exceeded, outcome.cause
        assert outcome.debit == 0 and outcome.settlement_state in (
            SettlementState.released_free, SettlementState.released_platform_absorbed)
        assert not any(reservation.active for reservation in stored.reservations)
        after = harness.extra["balance"](request.org_id)
        assert after["reserved"] == 0, f"{operation} left the hold reserved for a reaper"
        assert after["ledger"] == funded["ledger"], "our own deadline charges nothing (R21)"

    # 2. the lease alone: expired, but the job still has deadline left. A stale worker is
    # fenced and **nothing** changes - a lost lease is a requeue for `recover` to decide,
    # not a terminal state this call may invent.
    harness, request, admission, lease = await overdue("t02-lease-only")
    harness.clock.advance(limits.lease_ttl_s + 1)
    assert harness.clock.now() < lease.generation_deadline_at
    try:
        await harness.port.heartbeat(lease)
    except errors.StaleLease as exc:
        assert exc.code == "stale_lease", exc.code
    else:
        raise AssertionError("an expired lease renewed itself")
    stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert outcome is None and stored.state is JobState.running, \
        "an expired lease inside the deadline terminalized the job early"
    assert harness.extra["balance"](request.org_id)["reserved"] == admission.maximum_hold

    # 3. a superseded generation, past the deadline: still `stale_lease`, and it must not
    # terminalize on the new holder's behalf.
    harness, request, admission, lease = await overdue("t02-superseded")
    harness.clock.advance(limits.lease_ttl_s + 1)
    await harness.port.recover()                          # requeues; generation moves on
    second = await harness.port.claim(request.request_id, "worker-b")
    assert second.generation == lease.generation + 1
    try:
        await harness.port.heartbeat(lease)
    except errors.StaleLease:
        pass
    else:
        raise AssertionError("a superseded worker renewed a lease")
    _stored, still_running = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert still_running is None, "a superseded worker terminalized the new holder's job"


async def dur_fence__another_worker_at_the_same_generation_is_still_fenced(factory):
    """DUR-FENCE: the lease names a worker, not just a generation. Two processes that
    both believe they hold generation N - a duplicated dispatch, a rescheduled pod -
    must not both append, heartbeat or settle: the owner is part of the fence."""
    harness = factory()
    request, admission, lease = await _running(harness)
    forged = lease.model_copy(update={"worker_id": "worker-b"})
    for call in (harness.port.heartbeat(forged),
                 harness.port.complete(forged, b.outcome(request.request_id, harness))):
        try:
            await call
        except errors.StaleLease:
            pass
        else:
            raise AssertionError("a second worker at the same generation mutated the job")
    stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert outcome is None and stored.state is JobState.running
    # the real owner still works
    renewed = await harness.port.heartbeat(lease)
    assert renewed.worker_id == "worker-a"


async def dur_fence__a_lease_is_a_fencing_token_not_a_record(factory):
    """DUR-FENCE / r1 R29: the store renews its **own** lease. A worker that edits
    the record it hands back - its deadlines, its generation, its acquisition time -
    changes nothing, or it could grant itself unlimited generation time and the reaper
    would believe it."""
    harness = factory(limits=DEFAULTS.replace(generation_timeout_s=30, lease_ttl_s=10_000,
                                              lease_heartbeat_s=1_000,
                                              queue_wait_interactive_s=10_000))
    request, admission, lease = await _running(harness, deadline_s=10_000)
    forged = lease.model_copy(update={
        "generation_deadline_at": harness.clock.at(9_000),
        "first_token_deadline_at": harness.clock.at(8_000),
        "acquired_at": harness.clock.at(1_000),
        "expires_at": harness.clock.at(9_999)})
    renewed = await harness.port.heartbeat(forged)
    assert renewed.generation_deadline_at == lease.generation_deadline_at, \
        "a worker rewrote its own generation deadline"
    assert renewed.first_token_deadline_at == lease.first_token_deadline_at
    assert renewed.acquired_at == lease.acquired_at
    # and the forged deadline buys no time: past the stored one, the job is terminal
    harness.clock.advance(31)
    for call in (harness.port.heartbeat(forged),
                 harness.port.complete(forged, b.outcome(request.request_id, harness,
                                                         tokens=b.usage(1200, 340)))):
        try:
            await call
        except (errors.AlreadyTerminal, errors.StaleLease):
            pass
        else:
            raise AssertionError("a forged lease outlived the stored generation deadline")
    _stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert outcome is not None and outcome.debit == 0
    assert outcome.cause is TerminalCause.deadline_exceeded


async def dur_fence__a_deadline_binds_append_and_complete(factory):
    """DUR-FENCE / r1 R29: past the persisted generation deadline neither `append` nor
    `complete` may proceed - **published output does not buy more time** - and the
    store terminalizes the job in that same operation, with no debit. A worker holding
    a live lease must not be able to keep generating, or settle usage it produced after
    the deadline the customer was promised."""
    for published in (False, True):
        harness = factory(limits=DEFAULTS.replace(generation_timeout_s=30, lease_ttl_s=10_000,
                                                  lease_heartbeat_s=1_000,
                                                  queue_wait_interactive_s=200))
        publish = hook(harness, "publish")
        request, admission, lease = await _running(harness, key=f"bind-{published}",
                                                   deadline_s=230)
        if published:
            await publish(lease)
        before = harness.extra["balance"](request.org_id)
        harness.clock.advance(31)                  # 31s into a 30s generation budget
        assert harness.clock.now() < lease.expires_at
        try:
            await harness.port.complete(lease, b.outcome(request.request_id, harness,
                                                         tokens=b.usage(1200, 340)))
        except (errors.AlreadyTerminal, errors.StaleLease):
            pass
        else:
            raise AssertionError(f"a settlement past the generation deadline was accepted "
                                 f"(published={published})")
        stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
        assert outcome is not None, "complete did not terminalize the overdue job"
        assert outcome.cause is TerminalCause.deadline_exceeded, outcome.cause
        assert outcome.debit == 0, "usage produced past the deadline was charged"
        after = harness.extra["balance"](request.org_id)
        assert after["ledger"] == before["ledger"]
        # and the same for `append`, on a fresh job of the same store
        stream = hook(harness, "stream")
        request, admission, lease = await _running(harness, key=f"bind-append-{published}",
                                                   deadline_s=230)
        if published:
            await stream.append(lease, b.events("first"))
        harness.clock.advance(31)
        try:
            await stream.append(lease, b.events("too late"))
        except (errors.AlreadyTerminal, errors.StaleLease):
            pass
        else:
            raise AssertionError(f"an append past the generation deadline was accepted "
                                 f"(published={published})")
        _stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
        assert outcome is not None and outcome.cause is TerminalCause.deadline_exceeded
        assert outcome.debit == 0


async def dur_fence__a_stale_generation_is_rejected(factory):
    """DUR-FENCE: after recovery and a new claim, the old generation is fenced. The
    **same worker** reclaims, so the generation is the only thing that differs: a store
    that compared only the worker id would let the first attempt settle the job the
    second attempt is running.

    r1 R38 is what makes this work on default limits: the 120 s lease loss is time
    spent *running*, which the queue budget never sees, so the requeued job still has
    its unspent queue remainder and an ordinary interactive job can be retried."""
    harness = factory()
    request, admission, first = await _running(harness)
    harness.clock.advance(DEFAULTS.lease_ttl_s + 1)
    await harness.port.recover()
    second = await harness.port.claim(request.request_id, "worker-a")
    assert second.generation == first.generation + 1
    assert second.worker_id == first.worker_id
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
async def dur_output__every_requeued_candidate_carries_the_right_kind(factory):
    """DUR-OUTPUT / r1 R52 + s18: what `recover` emits is labelled for the phase it belongs
    to, for **both** lease kinds.

    A requeue after a lost *inference* attempt labelled `prepare_dispatch` would be handed
    to the preparation pool, which would call `claim_preparation` on a `queued` job, be
    refused, and hand it back - for ever, while the index looked busy and the inference pool
    saw nothing. The mirror mistake sends a `preparing` job to the inference pool. So the
    kind of every event recover produces is asserted, not just its existence.
    """
    harness = factory()
    outbox = harness.extra["outbox"]

    # A lost inference attempt before publication: requeued as an inference candidate.
    request, admission, lease = await _running(harness)
    harness.clock.advance(DEFAULTS.lease_ttl_s + 1)
    produced = await harness.port.recover()
    events = [item for item in produced if isinstance(item, IndexEvent)]
    assert len(events) == 1, f"one requeue expected, got {produced}"
    assert events[0].kind is OutboxKind.inference_dispatch, \
        f"a lost inference attempt was requeued as {events[0].kind}"
    assert events[0].job_id == request.request_id and not events[0].is_preparation
    dispatches = [event.kind for event in outbox(request.request_id)]
    assert OutboxKind.inference_dispatch in dispatches
    assert dispatches.count(OutboxKind.prepare_dispatch) == 1, \
        "a lost inference attempt emitted a preparation dispatch"

    # A lost preparation worker: redispatched for preparation, and never as an inference
    # candidate - the job is still `preparing`, so no inference worker can do anything.
    other, other_admission = await _admit(harness, key="kinds-prep")
    await harness.port.claim_preparation(other.request_id, "prep-a")
    harness.clock.advance(DEFAULTS.preparation_lease_ttl_s + 1)
    prep_produced = await harness.port.recover()
    assert not [item for item in prep_produced if isinstance(item, IndexEvent)
                and item.job_id == other.request_id], \
        "a lost preparation worker produced an index candidate"
    prep_dispatches = [event.kind for event in outbox(other.request_id)]
    assert prep_dispatches.count(OutboxKind.prepare_dispatch) == 2, prep_dispatches
    assert OutboxKind.inference_dispatch not in prep_dispatches, \
        "a job still preparing was dispatched for inference"
    del other_admission, admission, lease


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
    publish = hook(harness, "publish")
    request, admission, lease = await _running(harness)
    await publish(lease)
    before = harness.extra["balance"](request.org_id)
    harness.clock.advance(DEFAULTS.lease_ttl_s + 1)
    await harness.port.recover()
    stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert stored.state is JobState.failed
    assert outcome.cause is TerminalCause.lost_after_publication
    # r1 R21: published tokens with unknown usage reconcile, whatever the cause. The
    # held reservation is what makes the reconciliation backlog visible (02 alerts on
    # it), so releasing it at once would hide an attempt whose real cost we never
    # learned; the customer is charged either way, which is to say never.
    assert outcome.debit == 0
    assert outcome.settlement_state is SettlementState.held_unknown
    assert outcome.reconcile_after is not None
    assert harness.extra["balance"](request.org_id)["reserved"] == admission.maximum_hold
    harness.clock.advance(DEFAULTS.unknown_usage_reconcile_s + 1)
    await harness.port.recover()
    _stored, final = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert final.settlement_state is SettlementState.released_platform_absorbed
    after = harness.extra["balance"](request.org_id)
    assert after["reserved"] == 0 and after["ledger"] == before["ledger"]


async def dur_output__any_committed_chunk_is_publication(factory):
    """DUR-OUTPUT (02: "persisting output before a client reads is enough to prohibit
    regeneration"): the publication marker is set by the **first committed chunk**,
    whatever its type. A store that only counted deltas would regenerate a request
    whose progress or usage event a client has already seen."""
    from ..records import EngineEvent
    for event in (EngineEvent(type=ChunkEventType.progress, payload={"phase": "running"}),
                  EngineEvent(type=ChunkEventType.usage, payload={"tokens": 1}),
                  EngineEvent(type=ChunkEventType.error, payload={"code": "engine_error"})):
        harness = factory()
        jobs = hook(harness, "jobs")
        request, admission, lease = await _stream_job(harness, key=f"pub-{event.type.value}")
        committed = await harness.port.append(lease, (event,))
        assert len(committed) == 1
        harness.clock.advance(DEFAULTS.lease_ttl_s + 1)
        await jobs.recover()
        stored, outcome = await jobs.get_owned(request.org_id, admission.job_handle)
        assert outcome is not None, f"a {event.type.value} chunk did not forbid regeneration"
        assert outcome.cause is TerminalCause.lost_after_publication, event.type
        assert stored.state is JobState.failed and outcome.debit == 0


async def dur_output__prepublication_retries_are_bounded(factory):
    """DUR-OUTPUT: retries are capped, then the job fails honestly."""
    harness = factory(limits=DEFAULTS.replace(max_prepublication_retries=1))
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
    harness = factory(limits=DEFAULTS.replace(queue_wait_interactive_s=10,
                                              generation_timeout_s=10_000))
    request, admission = await _admit(harness, mode=ExecutionMode.sync, deadline_s=10_000)
    await _prepare(harness.port, admission.request_id)
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
    retune = hook(harness, "retune")
    request, admission = await _admit(harness, mode=ExecutionMode.sync)
    assert admission.budgets.queue_wait_s == 10
    assert admission.budgets.generation_s == DEFAULTS.generation_timeout_s
    await _prepare(harness.port, admission.request_id)
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


async def dur_output__a_late_preparation_worker_finds_a_terminal_job(factory):
    """DUR-OUTPUT / r1 R29: the preparation instant binds `prepared` itself. A
    preparation worker that comes back after its deadline finds the job already
    terminal, and its own call is what terminalized it - otherwise a dead preparation
    would pin a preparation unit, a journal reservation and a hold until a reaper ran,
    and the outcome would depend on reaper timing."""
    limits = DEFAULTS.replace(preparation_timeout_s=30)
    harness = factory(limits=limits)
    request, admission = await _admit(harness,
                                      deadline_s=b.default_deadline_s(ExecutionMode.stream, limits))
    assert admission.preparation_deadline_at == harness.clock.at(30)
    before = harness.extra["balance"](request.org_id)
    harness.clock.advance(31)
    # A worker must not even be handed a lease for a job nobody is waiting for any more:
    # the claim is refused and is itself what terminalizes the job, so the outcome never
    # depends on when a reaper runs, and no preparation attempt is spent on a dead job.
    try:
        await harness.port.claim_preparation(admission.request_id, "prep-a")
    except (errors.AlreadyTerminal, errors.NotClaimable, errors.StateConflict):
        pass
    else:
        raise AssertionError("a preparation lease was issued past the preparation deadline")
    stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert outcome is not None, "the late claim did not terminalize the job"
    assert outcome.cause is TerminalCause.preparation_failed
    assert outcome.state is JobState.failed and outcome.debit == 0
    assert outcome.settlement_state is SettlementState.released_free
    after = harness.extra["balance"](request.org_id)
    assert after["reserved"] == 0 and after["ledger"] == before["ledger"]
    assert not any(reservation.active for reservation in stored.reservations)
    journal_bytes = hook(harness, "journal_bytes")
    # only the terminal event of the settling transaction is still stored
    assert journal_bytes() < DEFAULTS.journal_job_reserve_bytes
    # and the freed preparation unit is immediately usable again
    assert len(harness.extra["active_jobs"]()) == 0

    # a preparation worker that never comes back at all is the reaper's job
    other, admitted = await _admit(harness, key="never-returns",
                                   deadline_s=b.default_deadline_s(ExecutionMode.stream, limits))
    harness.clock.advance(31)
    await harness.port.recover()
    _stored, reaped = await harness.port.get_owned(other.org_id, admitted.job_handle)
    assert reaped is not None, "an abandoned preparation was never reaped"
    assert reaped.cause is TerminalCause.preparation_failed and reaped.debit == 0
    assert harness.extra["balance"](other.org_id)["reserved"] == 0

    # r1 R46 + R29: the same holds for a worker holding a **live** preparation lease.
    # A long lease TTL separates the two refusals, so this is the phase deadline binding
    # `prepared` itself rather than the lease simply having expired - which is the case
    # where the store must terminalize in that same call.
    # r1 R52 makes the third variant - a worker holding a **live** lease past the phase
    # deadline - unreachable by construction: `claim_preparation` and `heartbeat` both clamp
    # `expires_at` to `preparation_deadline_at`, so the lease can never outlive the phase and
    # the lease-expiry refusal always fires first. `_fence_preparation` still enforces the
    # phase deadline, as defence for an adapter that forgets to clamp, and the clamp itself
    # is what `dur_fence__preparation_is_claimed_and_fenced_like_execution` pins.


async def dur_output__a_heartbeating_preparation_worker_is_terminalized_on_time(factory):
    """DUR-OUTPUT / r1 R29 + R55: past `preparation_deadline_at` the store terminalizes in
    **that same operation**, even for a worker that did everything right.

    The reviewer's reproduction, on default limits. R52 clamps every preparation lease to
    `preparation_deadline_at`, so the lease and the phase expire together - and with the
    expiry check ahead of the deadline check, a worker that heartbeated faithfully to
    t=110 s and came back at t=121 s got `stale_lease`, the job stayed `preparing` with
    `outcome=None`, and the customer's hold stayed reserved until some reaper happened to
    run. R29 exists precisely so the outcome does not depend on reaper timing, so the
    phase deadline is now enforced **before** lease expiry (R55) and `_enforce_deadlines`
    stops being dead code.
    """
    harness = factory()
    request, admission = await _admit(harness)
    before = harness.extra["balance"](request.org_id)
    assert before["reserved"] == admission.maximum_hold > 0
    lease = await harness.port.claim_preparation(request.request_id, "prep-a")
    # heartbeat every 10 s, as a real worker would, right up to the phase deadline
    beats = 0
    while harness.clock.now() + timedelta(seconds=10) < admission.preparation_deadline_at:
        harness.clock.advance(10)
        lease = await harness.port.heartbeat(lease)
        beats += 1
    assert beats >= 10, f"the case needs a long-running preparation, got {beats} beats"
    assert lease.expires_at == admission.preparation_deadline_at, "R52: clamped to the phase"
    # one second past the phase deadline: every fenced operation must terminalize the job
    # in the same call, and each must be the call that does it.
    for operation in ("prepared", "heartbeat", "load_work"):
        fresh_request, fresh = await _admit(harness, key=f"late-{operation}")
        fresh_lease = await harness.port.claim_preparation(fresh_request.request_id, "prep-a")
        harness.clock.advance(DEFAULTS.preparation_timeout_s + 1)
        funded = harness.extra["balance"](fresh_request.org_id)
        invoke = {"prepared": lambda: harness.port.prepared(fresh_lease, ()),
                  "heartbeat": lambda: harness.port.heartbeat(fresh_lease),
                  "load_work": lambda: harness.port.load_work(fresh_lease)}[operation]
        try:
            await invoke()
        except errors.AlreadyTerminal:
            pass
        else:
            raise AssertionError(f"{operation} past the preparation deadline did not refuse")
        stored, outcome = await harness.port.get_owned(fresh_request.org_id, fresh.job_handle)
        assert outcome is not None, \
            f"{operation} refused but left the job non-terminal: R29 wants it settled here"
        assert outcome.cause is TerminalCause.preparation_failed
        assert outcome.state is JobState.failed and outcome.debit == 0
        assert not any(reservation.active for reservation in stored.reservations), \
            f"{operation} left the reservations active"
        after = harness.extra["balance"](fresh_request.org_id)
        assert after["reserved"] == funded["reserved"] - fresh.maximum_hold, \
            f"{operation} left the hold reserved until a reaper ran"
        assert after["ledger"] == funded["ledger"], "a failed preparation charges nothing"


async def dur_output__phase_deadlines_are_persisted_at_each_transition(factory):
    """DUR-OUTPUT / r1 R20: the store derives each phase instant from the database
    clock at the transition into that phase, never beyond `deadline_at`, and hands it
    to whoever enforces it. The instants are facts on the record, not a budget every
    worker re-derives against its own clock, and a configuration change after
    admission moves none of them."""
    # A queue budget wide enough that the requeue below really happens: this case is
    # about where the instants come from, not about queue expiry.
    harness = factory(limits=DEFAULTS.replace(queue_wait_interactive_s=10_000,
                                              generation_timeout_s=90_000))
    retune = hook(harness, "retune")
    request, admission = await _admit(harness, deadline_s=100_000)
    assert admission.preparation_deadline_at == harness.clock.at(admission.budgets.preparation_s)
    assert admission.queue_deadline_at is None          # not queued yet
    harness.clock.advance(7)
    queued = await _prepare(harness.port, admission.request_id)
    first_queue_deadline = queued.queue_deadline_at
    assert first_queue_deadline == harness.clock.at(queued.budgets.queue_wait_s)
    assert queued.preparation_deadline_at == admission.preparation_deadline_at
    retune(queue_wait_interactive_s=1, generation_timeout_s=1, preparation_timeout_s=1)
    lease = await harness.port.claim(request.request_id, "worker-a")
    assert lease.generation_deadline_at == harness.clock.at(queued.budgets.generation_s)
    assert lease.first_token_deadline_at == harness.clock.at(queued.budgets.first_token_s)
    assert lease.first_token_deadline_at <= lease.generation_deadline_at
    # r1 R38: a prepublication requeue gets only the unspent remainder of the queue
    # budget. The job sat queued for 7s of its 10,000s here, so the new deadline is
    # that much shorter in unspent time, and the used time is persisted.
    harness.clock.advance(DEFAULTS.lease_ttl_s + 1)
    await harness.port.recover()
    requeued, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert outcome is None and requeued.state is JobState.queued, \
        "the requeue this case is about did not happen"
    assert requeued.queue_wait_used_s == 0, "time spent running was charged to the queue"
    assert requeued.queue_deadline_at == harness.clock.at(queued.budgets.queue_wait_s)
    remaining_now = (requeued.queue_deadline_at - harness.clock.now()).total_seconds()
    first_remaining = (first_queue_deadline - queued.admitted_at).total_seconds()
    assert remaining_now <= first_remaining, "a requeue bought queue time"
    assert requeued.preparation_deadline_at == admission.preparation_deadline_at
    # and the second attempt's generation instants are its own, derived at its claim
    second = await harness.port.claim(request.request_id, "worker-b")
    assert second.generation == 2
    assert second.generation_deadline_at == harness.clock.at(queued.budgets.generation_s)
    assert second.generation_deadline_at > lease.generation_deadline_at


async def dur_output__no_phase_deadline_outlives_the_accepted_deadline(factory):
    """DUR-OUTPUT / r1 R20: a job accepted with less time left than a phase budget
    gets the absolute deadline as its phase instant. The accepted `deadline_at` is the
    ceiling for every phase, so no budget can extend a job past what the customer was
    promised."""
    harness = factory()
    request, admission = await _admit(harness, deadline_s=5)
    assert admission.budgets.preparation_s > 5          # the budget is the larger one
    assert admission.preparation_deadline_at == admission.deadline_at
    queued = await _prepare(harness.port, admission.request_id)
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
                                              lease_heartbeat_s=1_000,
                                              queue_wait_interactive_s=10_000))
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


async def dur_output__queue_time_is_time_spent_queued(factory):
    """DUR-OUTPUT / r1 R38: the queue budget is cumulative time **in** `queued`, not
    wall time since the first transition. Time spent `running` belongs to the
    generation budget, so a lost lease does not eat the queue budget; the used time is
    persisted, the remainder never grows, and the deadline never reaches past
    `deadline_at`. This is what keeps an interactive retry possible at all."""
    harness = factory(limits=DEFAULTS.replace(queue_wait_interactive_s=100,
                                              lease_ttl_s=30, lease_heartbeat_s=10))
    request, admission = await _admit(harness, mode=ExecutionMode.sync)
    assert admission.queue_wait_used_s == 0 and admission.queue_deadline_at is None
    queued = await _prepare(harness.port, admission.request_id)
    assert queued.queue_deadline_at == harness.clock.at(100)

    harness.clock.advance(40)                      # 40s queued
    lease = await harness.port.claim(request.request_id, "worker-a")
    running, _ = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert running.queue_wait_used_s == 40, running.queue_wait_used_s

    harness.clock.advance(31)                      # 31s running: lost lease, requeued
    await harness.port.recover()
    requeued, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert outcome is None and requeued.state is JobState.queued, "the retry was not possible"
    assert requeued.queue_wait_used_s == 40, "running time was charged to the queue budget"
    # 60s of queue budget left, and not a second more
    assert requeued.queue_deadline_at == harness.clock.at(60)
    second = await harness.port.claim(request.request_id, "worker-a")
    assert second.generation == 2

    harness.clock.advance(20)                      # 20s more running
    await harness.port.heartbeat(second)
    harness.clock.advance(31)                      # lease lost again
    await harness.port.recover()
    again, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert outcome is None and again.queue_wait_used_s == 40
    assert again.queue_deadline_at == harness.clock.at(60)     # still exactly the remainder

    harness.clock.advance(61)                      # 101s queued in total
    await harness.port.recover()
    _stored, expired = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert expired is not None, "the queue budget never expired"
    assert expired.cause is TerminalCause.queue_wait_expired and expired.debit == 0


async def dur_output__a_job_past_its_queue_budget_is_not_claimable(factory):
    """DUR-OUTPUT: the queue-wait budget bounds execution, not just reporting. A job
    the customer has already been told to give up on must not start running because
    a worker happened to claim it before the reconciler reached it."""
    harness = factory(limits=DEFAULTS.replace(queue_wait_interactive_s=10,
                                              generation_timeout_s=10_000))
    request, admission = await _admit(harness, mode=ExecutionMode.sync, deadline_s=10_000)
    await _prepare(harness.port, admission.request_id)
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
    await _prepare(harness.port, admission.request_id)
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


async def dur_settle__the_store_rounds_half_up_once(factory):
    """DUR-SETTLE: the debit is the price snapshot applied **once**, rounded half up,
    by the store. Driven through `complete` on the rounding boundary, so an adapter
    whose SQL rounds up, truncates, or rounds each token line separately is caught:
    the ledger movement is asserted, not just the returned number."""
    harness = factory()
    # r1 R45: the rate is the *store's* fact, so each round moves the injectable price
    # source and admits at whatever it then answers. Handing four snapshots to four
    # requests used to look like a rate test while really testing that the store echoed
    # the client's own price back at it.
    set_price = hook(harness, "set_price")
    # 1 prompt token at 0.005/M = 0.000000005 USD: exactly the half-up boundary, and
    # the ceiling-versus-half-up divergence the money fixtures pin.
    boundary = b.price(input_rate="0.005", output_rate="0.60")
    below = b.price(input_rate="0.004", output_rate="0.60")
    for snapshot, tokens, expected in ((boundary, (1, 0), "0.00000001"),
                                       (below, (1, 0), "0.00000000"),
                                       (b.price(), (1200, 340), "0.00044400"),
                                       (b.price(input_rate="0.15", output_rate="0.45"),
                                        # 0.000099900 exactly: nothing to round, so a
                                        # store that rounds twice or ceilings is caught
                                        (333, 111), "0.00009990")):
        set_price(b.MODEL, snapshot)
        request, admission = await _admit(harness, price=snapshot, key=f"round-{expected}",
                                          max_input_tokens=30_720, max_output_tokens=2_048)
        assert admission.price_snapshot == snapshot, \
            "the store settled at a price it did not snapshot from its own price source"
        before = harness.extra["balance"](request.org_id)
        outcome = await _settle(harness, request, admission, tokens=b.usage(*tokens))
        assert money.format_money(outcome.debit) == expected, (snapshot.input_rate_per_million,
                                                               tokens)
        after = harness.extra["balance"](request.org_id)
        assert money.format_money(before["ledger"] - after["ledger"]) == expected
        assert after["reserved"] == before["reserved"] - admission.maximum_hold
        if outcome.debit == 0:
            assert outcome.settlement_state is SettlementState.released_free
        else:
            assert outcome.settlement_state is SettlementState.settled


async def dur_settle__a_price_change_never_undersizes_the_hold(factory):
    """DUR-SETTLE / r1 R53: the hold and the snapshot come from the same transaction.

    The exact failure this closes: the gateway validates against 0.20/M, the rate moves to
    2.00/M, and admission then snapshots 2.00 while holding what 0.20 needed. A perfectly
    valid in-envelope completion no longer fits its own hold, so the store calls it a
    protocol violation - `platform_error`, usage discarded, debit zero - and the platform
    eats a request it should have charged for, or refuses one it should have run. The hold
    is a promise about a price, so it can only be computed from the price that was taken.

    Also pins the other direction (R53/R45): settlement uses the **admitted** snapshot, so
    a rate that moves after admission does not reprice a job that is already running.
    """
    harness = factory()
    set_price = hook(harness, "set_price")
    cheap, dear = b.price("0.20", "0.60"), b.price("2.00", "6.00")
    harness.extra["grant"](b.ORG_A, "25.00")

    # The rate moves between validation and admission. Nothing the caller did changes.
    set_price(b.MODEL, cheap)
    request = b.request(harness, max_input_tokens=30_720, max_output_tokens=2_048)
    validated = cheap.maximum_hold(request.max_input_tokens, request.max_output_tokens)
    set_price(b.MODEL, dear)
    admission = await harness.port.admit(request, b.idem(request, "moved"), ())

    assert admission.price_snapshot == dear, "admission must snapshot the price it took"
    expected = dear.maximum_hold(request.max_input_tokens, request.max_output_tokens)
    assert admission.maximum_hold == expected, \
        "the hold must cover the snapshot that was taken, not the one the caller saw"
    assert admission.maximum_hold > validated, "the case needs the price to have risen"
    assert harness.extra["balance"](b.ORG_A)["reserved"] == expected

    # A completion inside the envelope therefore settles the exact debit, not a platform
    # failure: the whole point of holding the ceiling is that the ceiling fits.
    await _prepare(harness.port, request.request_id)
    lease = await harness.port.claim(request.request_id, "worker-a")
    prompt, completion = request.max_input_tokens, request.max_output_tokens
    outcome = await harness.port.complete(
        lease, b.outcome(request.request_id, harness, tokens=b.usage(prompt, completion)))
    assert outcome.settlement_state is SettlementState.settled, \
        f"an in-envelope completion settled {outcome.settlement_state}"
    assert outcome.cause is TerminalCause.completed and outcome.usage is not None
    assert outcome.debit == dear.debit(prompt, completion)
    assert outcome.debit <= admission.maximum_hold, "a debit never exceeds its hold"

    # And the other direction: a rate that moves *after* admission does not reprice the
    # job. The admitted snapshot is the one the customer was quoted.
    set_price(b.MODEL, cheap)
    later = b.request(harness, max_input_tokens=1_000, max_output_tokens=100)
    later_admission = await harness.port.admit(later, b.idem(later, "before-move"), ())
    assert later_admission.price_snapshot == cheap
    set_price(b.MODEL, dear)
    await _prepare(harness.port, later.request_id, worker="prep-b")
    later_lease = await harness.port.claim(later.request_id, "worker-b")
    # `load_work` hands the worker the admitted snapshot, never the current one (R53)
    work = await harness.port.load_work(later_lease)
    assert work.price_snapshot == cheap, "load_work must carry the admitted snapshot"
    settled = await harness.port.complete(
        later_lease, b.outcome(later.request_id, harness, tokens=b.usage(800, 80)))
    assert settled.debit == cheap.debit(800, 80), \
        "settlement repriced the job at the current rate"
    set_price(b.MODEL, b.DEFAULT_PRICE)


async def dur_cap__the_hold_rounds_up_never_half_up(factory):
    """DUR-CAP / 01 §4 + r1 R53/s01: the maximum hold rounds **up**, and a debit rounds
    half up. Every other case used rates whose product is exact, so the two agreed and a
    store using half-up for the hold passed them all.

    Half-up on the hold under-reserves by one unit in the last place, which is the one
    number that must never be short: `debit <= maximum_hold` is what makes an in-envelope
    completion settleable, so an under-reserved hold turns a legitimate request into a
    `platform_error` the platform eats.
    """
    # Room for the seeded table below: the property needs many admissions, and the default
    # per-key ceiling is eight.
    harness = factory(limits=DEFAULTS.replace(max_active_jobs=64, max_active_jobs_per_org=64,
                                              max_active_jobs_per_key=64,
                                              max_preparing_jobs=64))
    set_price = hook(harness, "set_price")
    harness.extra["grant"](b.ORG_A, "100.00")

    # 1001 x 0.33333333/M + 7 x 0.77777777/M = 0.00033911110772 : ceiling gives
    # 0.00033912 too, so the pair that separates them is the one below.
    divergent = b.price("0.33333333", "0.77777777")
    set_price(b.MODEL, divergent)
    request = b.request(harness, max_input_tokens=1001, max_output_tokens=7)
    admission = await harness.port.admit(request, b.idem(request, "s01"), ())
    exact = (Decimal(1001) * Decimal("0.33333333")
             + Decimal(7) * Decimal("0.77777777")) / Decimal(1_000_000)
    assert money.format_money(admission.maximum_hold) == "0.00033912", \
        f"the hold must round up: {money.format_money(admission.maximum_hold)}"
    assert admission.maximum_hold >= exact, "a hold below the exact worst case under-reserves"
    assert admission.maximum_hold == money.ceiling(exact)
    assert money.ceiling(exact) != money.half_up(exact), \
        "the case needs rates where ceiling and half-up differ, or it proves nothing"

    # And the property, over a seeded table: whatever the rates and ceilings, the hold is
    # never less than the worst-case debit it exists to cover. One `admit` per row, so the
    # table is small and deterministic (seed 20260921, as the trace lattice uses).
    rng = random.Random(20260921)
    for row in range(24):
        rates = (f"0.{rng.randrange(1, 10 ** 8):08d}", f"0.{rng.randrange(1, 10 ** 8):08d}")
        max_input = rng.randrange(1, DEFAULTS.max_context_tokens - DEFAULTS.max_output_tokens)
        max_output = rng.randrange(1, DEFAULTS.max_output_tokens + 1)
        snapshot = b.price(*rates)
        set_price(b.MODEL, snapshot)
        seeded = b.request(harness, max_input_tokens=max_input, max_output_tokens=max_output)
        try:
            row_admission = await harness.port.admit(seeded, b.idem(seeded, f"s01-{row}"), ())
        except errors.InsufficientCredit:
            continue                      # a rich row: the property is about rounding
        worst = row_admission.price_snapshot.debit(max_input, max_output)
        assert row_admission.maximum_hold >= worst, \
            (f"row {row}: hold {money.format_money(row_admission.maximum_hold)} is below the "
             f"worst-case debit {money.format_money(worst)} at {rates} for "
             f"{max_input}/{max_output}")
    set_price(b.MODEL, b.DEFAULT_PRICE)


async def dur_admit__a_replay_reports_the_original_hold_and_price(factory):
    """DUR-ADMIT / r1 R53+R6/s05: an idempotent replay reports the **original** admission.

    Since R53 the store derives the hold, so a replay that re-derives it from the *current*
    price answers with a number that was never reserved. The durable hold stays right - the
    wallet is untouched - which is exactly why this needs asserting on the **returned
    record**: the client's retry is told a price and a reservation that do not exist, and
    a console rendering it shows the customer a figure nothing backs.
    """
    harness = factory()
    set_price = hook(harness, "set_price")
    harness.extra["grant"](b.ORG_A, "25.00")
    cheap, dear = b.price("0.20", "0.60"), b.price("2.00", "6.00")

    set_price(b.MODEL, cheap)
    request = b.request(harness)
    idem = b.idem(request, "s05")
    first = await harness.port.admit(request, idem, ())
    assert first.price_snapshot == cheap and not first.replayed
    reserved = harness.extra["balance"](b.ORG_A)["reserved"]
    assert reserved == first.maximum_hold

    set_price(b.MODEL, dear)
    # t15: and the clock moves, because a retry arrives later than the original by
    # definition - that is the whole point of a replay, and `admitted_at` refreshed to the
    # retry's clock would report an acceptance time no deadline on the job was derived from.
    harness.clock.advance(7)
    replay = await harness.port.admit(request, idem, ())
    assert replay.replayed is True
    assert replay.price_snapshot == cheap, \
        "a replay reported the current price, not the one the job was admitted at"
    assert replay.maximum_hold == first.maximum_hold, \
        (f"a replay reported {money.format_money(replay.maximum_hold)} against the "
         f"{money.format_money(first.maximum_hold)} actually reserved")
    # t15: field by field, not a hand-picked few. `admitted_at` is the one a store is most
    # likely to refresh - it is "now" on every other path - and a replay reporting the
    # retry's clock would tell a client its job was accepted at a time no deadline was
    # derived from. `replayed` is the only field allowed to differ.
    for field in type(first).model_fields:
        if field == "replayed":
            continue
        assert getattr(replay, field) == getattr(first, field), \
            f"a replay reported a different {field}: " \
            f"{getattr(first, field)!r} vs {getattr(replay, field)!r}"
    assert first.replayed is False
    # and the wallet never moved, which is what makes the returned record the only place
    # this defect is visible
    assert harness.extra["balance"](b.ORG_A)["reserved"] == reserved
    assert len(harness.extra["active_jobs"]()) == 1
    set_price(b.MODEL, b.DEFAULT_PRICE)


async def dur_settle__duplicate_completion_is_idempotent_then_conflicts(factory):
    """DUR-SETTLE: one winner. The identical call replays; a different one is a
    typed conflict and changes no money."""
    harness = factory()
    request, admission = await _admit(harness)
    await _prepare(harness.port, admission.request_id)
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
    await _prepare(harness.port, admission.request_id)
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
    publish = hook(harness, "publish")
    request, admission = await _admit(harness)
    await _prepare(harness.port, admission.request_id)
    lease = await harness.port.claim(request.request_id, "worker-a")
    await publish(lease)
    outcome = await harness.port.complete(
        lease, b.outcome(request.request_id, harness, cause=TerminalCause.client_disconnected,
                         state=JobState.failed, tokens=None, result_ref=None))
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
    publish = hook(harness, "publish")
    request, admission = await _admit(harness)
    await _prepare(harness.port, admission.request_id)
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
    await _prepare(harness.port, admission_a.request_id)
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
    await _prepare(harness.port, admission.request_id)
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


async def dur_settle__a_succeeded_outcome_needs_a_result_reference(factory):
    """DUR-SETTLE / r1 R30: a success the customer cannot fetch is not a success. It
    would settle a debit for a result that was never stored, and `GET /result` would
    answer 404 for a job the ledger says was delivered."""
    harness = factory()
    request, admission = await _admit(harness)
    await _prepare(harness.port, admission.request_id)
    lease = await harness.port.claim(request.request_id, "worker-a")
    before = harness.extra["balance"](request.org_id)
    try:
        await harness.port.complete(lease, b.outcome(request.request_id, harness,
                                                     tokens=b.usage(1200, 340),
                                                     result_ref=None))
    except errors.DomainError as exc:
        assert errors.http_status(exc.code) in (400, 409), exc.code
    else:
        raise AssertionError("a succeeded outcome settled without a result reference")
    assert harness.extra["balance"](request.org_id) == before
    _stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert outcome is None
    settled = await harness.port.complete(lease, b.outcome(request.request_id, harness,
                                                           tokens=b.usage(1200, 340)))
    assert settled.result_ref and settled.settlement_state is SettlementState.settled


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
    await _prepare(harness.port, admission.request_id)
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


async def dur_settle__cancelling_after_publication_reconciles(factory):
    """DUR-SETTLE: a cancellation after output was published has produced tokens
    nobody counted, so it is `held_unknown` with the hold still held, not an immediate
    release. Releasing it at once would drop the attempt out of the reconciliation
    backlog 02 wants alerted on."""
    harness = factory()
    publish = hook(harness, "publish")
    request, admission, lease = await _running(harness)
    await publish(lease)
    before = harness.extra["balance"](request.org_id)
    outcome = await harness.port.cancel(request.org_id, admission.job_handle)
    assert outcome.cause is TerminalCause.client_cancelled
    assert outcome.settlement_state is SettlementState.held_unknown
    assert outcome.reconcile_after is not None and outcome.debit == 0
    assert harness.extra["balance"](request.org_id)["reserved"] == admission.maximum_hold
    harness.clock.advance(DEFAULTS.unknown_usage_reconcile_s + 1)
    await harness.port.recover()
    _stored, final = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert final.settlement_state is SettlementState.released_platform_absorbed
    after = harness.extra["balance"](request.org_id)
    assert after["reserved"] == 0 and after["ledger"] == before["ledger"]
    # a cancellation before any output stays free: nothing was produced to reconcile
    other, admitted = await _admit(harness, key="cancel-early")
    early = await harness.port.cancel(other.org_id, admitted.job_handle)
    assert early.settlement_state is SettlementState.released_free
    assert harness.extra["balance"](other.org_id)["reserved"] == 0


async def dur_settle__cancel_records_its_cause_and_settles_by_r21(factory):
    """DUR-SETTLE / R21 (G2 D-new): `cancel(..., cause=)` records the cause it is given, in
    state `cancelled`, and settles by it. Before any output the client's causes
    (`client_cancelled`, `client_disconnected`) are `released_free`, and the platform's own
    `sync_deadline` is `released_platform_absorbed`. After publication every cause is
    `held_unknown` with the hold still held, released platform-absorbed after the fenced
    24 h. No cause moves the ledger: a cancel carries no usage. A repeat cancel with a
    different cause answers the committed outcome and moves nothing."""
    harness = factory()
    publish = hook(harness, "publish")
    unpublished = {TerminalCause.client_cancelled: SettlementState.released_free,
                   TerminalCause.client_disconnected: SettlementState.released_free,
                   TerminalCause.sync_deadline: SettlementState.released_platform_absorbed}
    held = []
    for cause, settlement in unpublished.items():
        for published in (False, True):
            request, admission, lease = await _running(harness, key=f"{cause.value}-{published}")
            if published:
                await publish(lease)
            before = harness.extra["balance"](request.org_id)
            outcome = await harness.port.cancel(request.org_id, admission.job_handle, cause=cause)
            assert (outcome.cause, outcome.state) == (cause, JobState.cancelled), (cause, outcome)
            assert outcome.debit == 0, cause
            _stored, committed = await harness.port.get_owned(request.org_id, admission.job_handle)
            assert committed == outcome, (cause, committed)
            after = harness.extra["balance"](request.org_id)
            assert after["ledger"] == before["ledger"], cause
            # A second cancel naming another cause answers the committed outcome, first
            # cause and all, and moves nothing.
            other = next(c for c in unpublished if c is not cause)
            again = await harness.port.cancel(request.org_id, admission.job_handle, cause=other)
            _stored, still = await harness.port.get_owned(request.org_id, admission.job_handle)
            assert again == committed and still == committed, (cause, other, again, still)
            assert harness.extra["balance"](request.org_id) == after, (cause, other)
            if published:
                assert outcome.settlement_state is SettlementState.held_unknown, cause
                assert outcome.reconcile_after is not None, cause
                assert after["reserved"] == before["reserved"], (cause, "released at once")
                held.append((request, admission, cause))
            else:
                assert outcome.settlement_state is settlement, (cause, outcome.settlement_state)
                assert after["reserved"] == before["reserved"] - admission.maximum_hold, cause
    harness.clock.advance(DEFAULTS.unknown_usage_reconcile_s + 1)
    await harness.port.recover()
    for request, admission, cause in held:
        _stored, final = await harness.port.get_owned(request.org_id, admission.job_handle)
        assert (final.cause, final.settlement_state) == (
            cause, SettlementState.released_platform_absorbed), (cause, final)
    assert harness.extra["balance"](b.ORG_A)["reserved"] == 0


async def dur_settle__cancel_refuses_any_other_cause_and_changes_nothing(factory):
    """DUR-SETTLE / R21: a canceller names a client cause or the synchronous deadline and
    nothing else. Every other `TerminalCause` - `completed`, the platform's failures, the
    queue and generation deadlines - and a value that is no cause at all are
    `invalid_request`: the job is still running, its hold still held, nothing recorded,
    and the same job then cancels normally."""
    harness = factory()
    request, admission, _lease = await _running(harness)
    before = harness.extra["balance"](request.org_id)
    allowed = {TerminalCause.client_cancelled, TerminalCause.client_disconnected,
               TerminalCause.sync_deadline}
    for cause in (*(c for c in TerminalCause if c not in allowed), "bogus"):
        try:
            await harness.port.cancel(request.org_id, admission.job_handle, cause=cause)
        except errors.DomainError as exc:
            assert isinstance(exc, errors.InvalidRequest), (cause, exc.code)
        else:
            raise AssertionError(f"cancel accepted the cause {cause!r}")
        _stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
        assert outcome is None, f"the refused cause {cause!r} terminalized the job"
        assert harness.extra["balance"](request.org_id) == before, cause
    cancelled = await harness.port.cancel(request.org_id, admission.job_handle)
    assert (cancelled.cause, cancelled.state) == (TerminalCause.client_cancelled,
                                                  JobState.cancelled)


async def dur_settle__a_published_job_past_its_deadline_reconciles(factory):
    """DUR-SETTLE / r1 R21+R29: a job killed by our own generation deadline **after**
    publishing output produced tokens nobody counted, so it is `held_unknown` with the
    hold held until the fenced 24 h, not released on the spot. Releasing at once would
    drop it out of the reconciliation backlog 02 wants alerted on."""
    harness = factory(limits=DEFAULTS.replace(generation_timeout_s=30, lease_ttl_s=10_000,
                                              lease_heartbeat_s=1_000,
                                              queue_wait_interactive_s=200))
    publish = hook(harness, "publish")
    request, admission, lease = await _running(harness, deadline_s=230)
    await publish(lease)
    before = harness.extra["balance"](request.org_id)
    harness.clock.advance(31)
    await harness.port.recover()
    _stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert outcome is not None and outcome.cause is TerminalCause.deadline_exceeded
    assert outcome.settlement_state is SettlementState.held_unknown, outcome.settlement_state
    assert outcome.reconcile_after is not None and outcome.debit == 0
    assert harness.extra["balance"](request.org_id)["reserved"] == admission.maximum_hold
    harness.clock.advance(DEFAULTS.unknown_usage_reconcile_s + 1)
    await harness.port.recover()
    _stored, final = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert final.settlement_state is SettlementState.released_platform_absorbed
    after = harness.extra["balance"](request.org_id)
    assert after["reserved"] == 0 and after["ledger"] == before["ledger"]


async def dur_settle__a_settlement_that_cannot_journal_moves_no_money(factory):
    """DUR-SETTLE / r1 R39: every capacity check a settling transaction can fail on
    happens before any wallet, outcome or reservation mutation.

    Swept across the reservation size **and every terminal cause**, because the boundary
    is where this breaks: a store that reserves the widest payload it happens to think of
    (r4 F2 found `platform_error` hand-picked, 3 bytes short of
    `journal_write_failed`) passes at a tiny reserve where even the check fails, and then
    releases a hold and refuses the caller at 94-96 bytes. Whatever happens, the
    settlement is all or nothing.
    """
    causes = [(cause, next(iter(states_for_cause(cause)))) for cause in TerminalCause]
    refused_at_least_once = False
    for reserve in (60, 80, 90, 94, 95, 96, 100, 120, 200):
        limits = DEFAULTS.replace(journal_job_reserve_bytes=reserve,
                                  journal_event_max_bytes=max(8, reserve // 2),
                                  journal_total_bytes=1 << 20)
        for cause, state in causes:
            harness = factory(limits=limits)
            request, admission = await _admit(harness, key=f"r39-{reserve}-{cause.value}")
            await _prepare(harness.port, admission.request_id)
            lease = await harness.port.claim(request.request_id, "worker-a")
            before = harness.extra["balance"](request.org_id)
            usage = b.usage(1200, 340) if cause in (TerminalCause.completed,) else None
            proposal = b.outcome(request.request_id, harness, cause=cause, state=state,
                                 tokens=usage,
                                 result_ref="results/x.json" if state is JobState.succeeded
                                 else None)
            try:
                settled = await harness.port.complete(lease, proposal)
            except errors.JournalCapacityExhausted as exc:
                refused_at_least_once = True
                assert errors.http_status(exc.code) == 429 and exc.retry_after_s >= 1
                # nothing moved: no ledger change, the hold still held, the job still
                # running, its reservations still active
                assert harness.extra["balance"](request.org_id) == before, \
                    f"{cause.value} at {reserve}B: money moved in a refused settlement"
                stored, outcome = await harness.port.get_owned(request.org_id,
                                                              admission.job_handle)
                assert outcome is None, f"{cause.value} at {reserve}B: a half-settled job"
                assert stored.state is JobState.running
                assert any(r.active for r in stored.reservations
                           if r.kind is ReservationKind.inference)
            else:
                # it committed, so the terminal event is there and the money is resolved
                assert settled.settlement_state is not None
                _stored, outcome = await harness.port.get_owned(request.org_id,
                                                                admission.job_handle)
                assert outcome is not None and outcome.cause is settled.cause
                assert harness.extra["balance"](request.org_id)["reserved"] == 0 \
                    or outcome.settlement_state is SettlementState.held_unknown
    assert refused_at_least_once, "the sweep never crossed the capacity boundary"


async def dur_settle__one_unsettleable_job_does_not_stop_the_sweep(factory):
    """DUR-SETTLE / r1 R39: `recover` is the only thing that releases the holds of
    overdue jobs, so one job it cannot settle - no journal capacity for its terminal
    event - must not abort the sweep. The refusal is reported, and every other job is
    still reaped."""
    # Room for a clean terminal event, but not for one behind ~165 bytes of output: the
    # stuck job is the one that published, the others are still queued.
    limits = DEFAULTS.replace(journal_job_reserve_bytes=180, journal_event_max_bytes=180,
                              journal_total_bytes=1 << 20, queue_wait_interactive_s=30)
    harness = factory(limits=limits)
    unsettleable = hook(harness, "unsettleable")
    stuck, stuck_admission = await _admit(harness, key="stuck")
    await _prepare(harness.port, stuck_admission.request_id)
    others = []
    for n in range(3):
        request, admission = await _admit(harness, key=f"reapable-{n}")
        await _prepare(harness.port, admission.request_id)
        others.append((request, admission))
    # fill the stuck job's journal so its terminal event cannot fit
    stream = hook(harness, "stream")
    lease = await harness.port.claim(stuck.request_id, "worker-a")
    await stream.append(lease, b.events("x" * 70))               # ~84 bytes stored
    # past the queue deadline for the queued jobs *and* past the lease of the published
    # one, so the sweep has to terminalize all four
    harness.clock.advance(DEFAULTS.lease_ttl_s + 1)
    produced = await harness.port.recover()
    assert produced, "recovery produced nothing"
    reported = unsettleable()
    assert stuck.request_id in reported, f"the unsettleable job was not reported: {reported}"
    _stored, stuck_outcome = await harness.port.get_owned(stuck.org_id,
                                                         stuck_admission.job_handle)
    assert stuck_outcome is None                   # still open, deliberately
    for request, admission in others:
        _stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
        assert outcome is not None, "a reapable job was skipped because another job stuck"
        assert outcome.cause is TerminalCause.queue_wait_expired
        # a reaped job's hold is released: that is *why* one stuck job must not stop the
        # sweep, so asserting it is the point of this case
        assert outcome.settlement_state is SettlementState.released_free
        assert outcome.debit == 0
    # and once there is room, the stuck job settles on the next sweep
    assert await stream.expire(harness.clock.at(DEFAULTS.journal_chunk_ttl_s + 1)) >= 0
    harness.clock.advance(DEFAULTS.journal_chunk_ttl_s + 1)
    await stream.expire()
    await harness.port.recover()
    _stored, settled = await harness.port.get_owned(stuck.org_id, stuck_admission.job_handle)
    assert settled is not None, "the stuck job never settled once there was room"
    assert stuck.request_id not in unsettleable()


async def dur_settle__terminalization_releases_every_reservation(factory):
    """DUR-SETTLE (02: "terminalization releases execution/preparation capacity and
    unused journal reservation"): the reservation rows are deactivated, and a later
    read says so. Leaving them active would let capacity accounting rebuilt from those
    rows refuse admissions for jobs that finished hours ago."""
    harness = factory()
    request, admission = await _admit(harness)
    assert all(reservation.active for reservation in admission.reservations)
    queued = await _prepare(harness.port, admission.request_id)
    preparation = {r.kind: r for r in queued.reservations}[ReservationKind.preparation]
    assert preparation.active is False, "preparation capacity was still held after prepared"
    lease = await harness.port.claim(request.request_id, "worker-a")
    await harness.port.complete(lease, b.outcome(request.request_id, harness,
                                                 tokens=b.usage(1200, 340)))
    stored, outcome = await harness.port.get_owned(request.org_id, admission.job_handle)
    assert outcome is not None
    assert not any(reservation.active for reservation in stored.reservations), \
        "a terminal job still reports active capacity reservations"
    assert {r.kind for r in stored.reservations} == set(ReservationKind)
    journal_bytes = hook(harness, "journal_bytes")
    # the unused journal *reservation* went too: only the bytes actually stored
    # (here just the terminal event) still count, until they are pruned
    assert journal_bytes() < DEFAULTS.journal_job_reserve_bytes


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
    """DUR-SETTLE: a protocol violation past the validated envelope is reconciled as a
    platform failure, never charged as an unreserved debit. The envelope is the token
    *ceilings*, not the money: usage one token over the output ceiling is refused even
    though its debit would still fit inside the hold, because a store that only
    compared the amount would charge for tokens it never reserved."""
    harness = factory()
    # (max_input, max_output, prompt, completion): the last two pairs are over a token
    # ceiling while their debit still fits inside the hold, which is the case a store
    # comparing only amounts would charge for.
    inside = 0
    # r1 R55 range-checks the ceilings, so the last row uses the largest output ceiling a
    # request may actually carry (`MAX_OUTPUT_TOKENS`) rather than an impossible 50 000.
    for max_input, max_output, prompt, completion in ((32, 16, 32, 4096), (32, 16, 33, 16),
                                                      (30_000, 16, 1, 4096),
                                                      (1_000, DEFAULTS.max_output_tokens,
                                                       1_001, 1)):
        request, admission = await _admit(harness, max_output_tokens=max_output,
                                          max_input_tokens=max_input,
                                          key=f"envelope-{max_input}-{max_output}-{prompt}")
        before = harness.extra["balance"](request.org_id)
        inside_the_hold = admission.price_snapshot.debit(prompt, completion) <= \
            admission.maximum_hold
        inside += int(inside_the_hold)
        outcome = await _settle(harness, request, admission,
                                tokens=b.usage(prompt, completion))
        assert outcome.debit == 0, (prompt, completion, inside_the_hold)
        assert outcome.cause is TerminalCause.platform_error, (prompt, completion)
        assert outcome.settlement_state is SettlementState.released_platform_absorbed
        assert outcome.usage is None and outcome.result_ref is None
        after = harness.extra["balance"](request.org_id)
        assert after["ledger"] == before["ledger"], (prompt, completion)
        assert after["reserved"] == before["reserved"] - admission.maximum_hold
    assert inside >= 2, "no case had usage over a ceiling whose debit still fit the hold"
    # exactly at the ceilings is not beyond it: that one settles normally
    request, admission = await _admit(harness, max_output_tokens=16, max_input_tokens=32,
                                      key="envelope-exact")
    outcome = await _settle(harness, request, admission, tokens=b.usage(32, 16))
    assert outcome.settlement_state is SettlementState.settled
    assert outcome.debit == admission.price_snapshot.debit(32, 16)


async def dur_settle__stale_and_out_of_order_transitions_are_typed_conflicts(factory):
    """DUR-SETTLE: the state machine refuses shortcuts without side effects."""
    harness = factory()
    request, admission = await _admit(harness)
    # r1 R46: hold on to the preparation lease, so replaying it after the phase is over
    # can be checked as well as re-claiming a phase that has ended.
    preparation = await harness.port.claim_preparation(request.request_id, "prep-a")
    await harness.port.prepared(preparation, ())
    for spent, expected in ((harness.port.prepared(preparation, ()), errors.StaleLease),
                            (harness.port.claim_preparation(request.request_id, "prep-b"),
                             errors.NotClaimable)):
        try:
            await spent
        except expected as exc:
            assert exc.code in ("stale_lease", "not_claimable"), exc.code
        else:
            raise AssertionError("preparing -> queued ran twice")
    lease = await harness.port.claim(request.request_id, "worker-a")
    await harness.port.complete(lease, b.outcome(request.request_id, harness))
    for call in (harness.port.claim_preparation(request.request_id, "prep-b"),
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
    events = hook(harness, "outbox")(request.request_id)
    assert len({event.event_id for event in events}) == len(events)   # stable, unique ids


def jobstore_cases():
    return [
        dur_admit__acceptance_creates_job_hold_reservations_and_outbox,
        dur_admit__idempotent_replay_returns_the_same_identity,
        dur_admit__a_request_uuid_is_admitted_once,
        dur_admit__changed_payload_with_the_same_key_is_a_conflict,
        dur_admit__lookup_reads_the_mapped_job_and_writes_nothing,
        dur_admit__crash_after_commit_then_retry_does_not_double_reserve,
        dur_admit__expired_mapping_is_explicit_never_a_second_billable_job,
        dur_admit__an_active_jobs_mapping_never_expires,
        dur_admit__the_tombstone_ttl_runs_from_the_terminal_state,
        dur_admit__tombstone_is_retained_after_the_terminal_state,
        dur_admit__revocation_and_suspension_are_rechecked_in_the_transaction,
        dur_admit__an_idempotency_scope_belongs_to_the_requests_own_org,
        dur_admit__a_deadline_must_be_one_the_store_can_keep,
        dur_admit__a_refused_admission_reserves_nothing,
        dur_admit__the_token_ceilings_are_range_checked,
        dur_cap__total_org_and_key_limits_reject_with_retry_guidance,
        dur_cap__admission_reserves_preparation_capacity,
        dur_cap__journal_reservation_must_fit_the_global_budget,
        dur_cap__hold_cannot_exceed_the_available_balance,
        dur_cap__a_hold_is_checked_against_available_not_the_ledger,
        dur_cap__a_negative_maximum_hold_is_refused,
        dur_cap__a_credit_grant_is_never_negative,
        dur_cap__the_hold_rounds_up_never_half_up,
        dur_admit__a_replay_reports_the_original_hold_and_price,
        dur_cap__concurrent_admissions_never_oversubscribe,
        dur_fence__claim_increments_the_generation_from_the_database_clock,
        dur_fence__preparation_is_claimed_and_fenced_like_execution,
        dur_fence__load_work_is_fenced_and_hands_out_nothing_otherwise,
        dur_output__a_lost_preparation_worker_is_reaped_within_bounds,
        dur_output__a_heartbeating_preparation_worker_is_terminalized_on_time,
        dur_fence__another_worker_at_the_same_generation_is_still_fenced,
        dur_fence__a_lease_is_a_fencing_token_not_a_record,
        dur_fence__a_deadline_binds_append_and_complete,
        dur_fence__an_expired_lease_can_neither_renew_nor_settle,
        dur_fence__an_overdue_inference_lease_terminalizes_in_the_same_call,
        dur_fence__a_stale_generation_is_rejected,
        dur_output__recovery_requeues_only_before_publication,
        dur_output__every_requeued_candidate_carries_the_right_kind,
        dur_output__loss_after_publication_is_a_terminal_failure,
        dur_output__prepublication_retries_are_bounded,
        dur_output__queue_wait_does_not_restart_on_a_requeue,
        dur_output__an_accepted_job_keeps_its_admission_budgets,
        dur_output__a_late_preparation_worker_finds_a_terminal_job,
        dur_output__phase_deadlines_are_persisted_at_each_transition,
        dur_output__no_phase_deadline_outlives_the_accepted_deadline,
        dur_output__the_generation_deadline_ends_a_running_attempt,
        dur_output__queue_time_is_time_spent_queued,
        dur_output__a_job_past_its_queue_budget_is_not_claimable,
        dur_output__the_absolute_deadline_bounds_recovery,
        dur_settle__one_settlement_with_exact_decimals,
        dur_settle__the_store_rounds_half_up_once,
        dur_settle__a_price_change_never_undersizes_the_hold,
        dur_settle__duplicate_completion_is_idempotent_then_conflicts,
        dur_settle__cancel_and_complete_race_has_a_single_winner,
        dur_settle__unknown_usage_is_held_then_released_as_platform_absorbed,
        dur_settle__an_unknown_usage_hold_is_never_released_on_a_callers_clock,
        dur_settle__an_outcome_settles_only_its_own_job,
        dur_settle__a_rejected_settlement_moves_no_money,
        dur_settle__a_succeeded_outcome_needs_a_result_reference,
        dur_settle__a_delivered_success_needs_authoritative_usage,
        dur_settle__the_winning_worker_can_always_replay_its_completion,
        dur_settle__only_three_causes_can_charge,
        dur_settle__cancelling_after_publication_reconciles,
        dur_settle__cancel_records_its_cause_and_settles_by_r21,
        dur_settle__cancel_refuses_any_other_cause_and_changes_nothing,
        dur_settle__a_published_job_past_its_deadline_reconciles,
        dur_settle__a_settlement_that_cannot_journal_moves_no_money,
        dur_settle__one_unsettleable_job_does_not_stop_the_sweep,
        dur_settle__terminalization_releases_every_reservation,
        dur_settle__platform_failures_are_free,
        dur_settle__usage_beyond_the_reserved_envelope_is_a_platform_failure,
        dur_settle__stale_and_out_of_order_transitions_are_typed_conflicts,
        dur_outbox__every_transition_emits_its_projection,
    ]


# ==========================================================================
# StreamStore
# ==========================================================================
async def _stream_job(harness, **kw):
    jobs = hook(harness, "jobs")
    inner = replace(harness, port=jobs)
    request, admission = await _admit(inner, **kw)
    await _prepare(jobs, admission.request_id)
    lease = await jobs.claim(request.request_id, "worker-a")
    return request, admission, lease


async def dur_output__append_commits_before_it_relays(factory):
    """DUR-OUTPUT: a crash after the commit still leaves the events readable, so
    nothing is relayed that is not durable."""
    harness = factory()
    request, admission, lease = await _stream_job(harness)
    plan = hook(harness, "failures")
    plan.crash_after_commit("append")
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
    jobs = hook(harness, "jobs")
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


async def api_stream__a_cursor_past_the_head_is_invalid(factory):
    """API-STREAM: a cursor the journal never issued is a 400, not an empty page a
    client polls for ever. A pruned prefix is still a 410 gap, and the head itself is
    a legitimate "nothing new yet"."""
    harness = factory()
    request, admission, lease = await _stream_job(harness)
    await harness.port.append(lease, b.events("a", "b"))
    page, head = await harness.port.read_owned(request.org_id, admission.job_handle, None, 10)
    assert head.token == "1-2"
    empty, same = await harness.port.read_owned(request.org_id, admission.job_handle, head, 10)
    assert empty == () and same == head            # at the head: simply nothing new
    for beyond in ("1-3", "2-1", "9-9"):
        try:
            await harness.port.read_owned(request.org_id, admission.job_handle,
                                          Cursor.parse(beyond), 10)
        except errors.InvalidCursor as exc:
            assert errors.http_status(exc.code) == 400
        else:
            raise AssertionError(f"cursor {beyond} was accepted past the head")


async def dur_output__a_worker_cannot_forge_a_terminal_event(factory):
    """DUR-OUTPUT / r1 R30: the terminal journal event is derived from the stored
    outcome inside the settling transaction. A worker that could append one, or
    re-mint one after the journal expired, could fake a settlement the ledger never
    made - and recharge journal bytes while doing it."""
    from ..records import EngineEvent
    harness = factory()
    jobs = hook(harness, "jobs")
    journal_bytes = hook(harness, "journal_bytes")
    request, admission, lease = await _stream_job(harness)
    forged = (EngineEvent(type=ChunkEventType.terminal,
                          payload={"state": "succeeded", "cause": "completed",
                                   "settlement_state": "settled"}),)
    try:
        await harness.port.append(lease, forged)
    except errors.DomainError as exc:
        assert errors.http_status(exc.code) in (400, 409), exc.code
    else:
        raise AssertionError("a worker appended a terminal event")
    page, _ = await harness.port.read_owned(request.org_id, admission.job_handle, None, 10)
    assert page == ()
    await harness.port.append(lease, b.events("real output"))
    outcome = await jobs.complete(lease, b.outcome(request.request_id, harness,
                                                   tokens=b.usage(10, 1)))
    terminal = await harness.port.finalize_in_transaction(outcome)
    assert terminal.event_type is ChunkEventType.terminal
    assert terminal.payload["settlement_state"] == outcome.settlement_state.value
    # a caller's own outcome is a lookup key, not content: a fabricated one is refused
    # rather than answered, and after expiry nothing is re-minted
    lying = outcome.model_copy(update={"settlement_state": SettlementState.released_free})
    try:
        await harness.port.finalize_in_transaction(lying)
    except errors.DomainError as exc:
        assert errors.http_status(exc.code) in (400, 409), exc.code
    else:
        raise AssertionError("a fabricated outcome was confirmed by the journal")
    assert (await harness.port.finalize_in_transaction(outcome)) == terminal
    harness.clock.advance(DEFAULTS.journal_chunk_ttl_s + 1)
    await harness.port.expire(harness.clock.now())
    bytes_after_expiry = journal_bytes() if journal_bytes is not None else None
    try:
        await harness.port.finalize_in_transaction(outcome)
    except errors.DomainError as exc:
        assert errors.http_status(exc.code) == 410, exc.code
    else:
        raise AssertionError("a terminal event was minted after the journal expired")
    assert journal_bytes() == bytes_after_expiry, "finalize recharged journal bytes"


async def dur_output__no_terminal_event_anywhere_in_a_batch(factory):
    """DUR-OUTPUT / r1 R30: a worker may not append a terminal event **anywhere** in a
    batch. Checking only the first event would let `[delta, terminal]` through, and the
    forged chunk would then be the one a later settlement finds and keeps, so the
    journal would show a terminal state the ledger never agreed to."""
    from ..records import EngineEvent
    harness = factory()
    jobs = hook(harness, "jobs")
    request, admission, lease = await _stream_job(harness)
    terminal = EngineEvent(type=ChunkEventType.terminal,
                           payload={"state": "succeeded", "cause": "completed",
                                    "settlement_state": "settled"})
    for batch in ((terminal,), (*b.events("a"), terminal), (terminal, *b.events("b")),
                  (*b.events("a"), terminal, *b.events("b"))):
        try:
            await harness.port.append(lease, batch)
        except errors.DomainError as exc:
            assert errors.http_status(exc.code) in (400, 409), exc.code
        else:
            raise AssertionError("a batch containing a terminal event was appended")
    page, _ = await harness.port.read_owned(request.org_id, admission.job_handle, None, 10)
    assert page == (), "a refused batch committed part of itself"
    # and the real settlement still writes exactly one terminal event, its own
    cancelled = await jobs.cancel(request.org_id, admission.job_handle)
    page, _ = await harness.port.read_owned(request.org_id, admission.job_handle, None, 10)
    terminals = [chunk for chunk in page if chunk.event_type is ChunkEventType.terminal]
    assert len(terminals) == 1
    assert terminals[0].payload["cause"] == cancelled.cause.value
    assert terminals[0].payload["settlement_state"] == cancelled.settlement_state.value


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


async def dur_output__expiry_never_runs_on_a_callers_clock(factory):
    """DUR-OUTPUT / r1 R7: `expire` reads the database clock. A caller passing a time a
    year ahead must prune nothing that is still live, or a client could have another
    tenant's - or its own - replay window destroyed by asking nicely."""
    harness = factory()
    request, admission, lease = await _stream_job(harness)
    await harness.port.append(lease, b.events("still live"))
    assert await harness.port.expire(harness.clock.at(365 * 86_400)) == 0, \
        "a caller's clock pruned a live journal"
    page, _ = await harness.port.read_owned(request.org_id, admission.job_handle, None, 10)
    assert [chunk.payload["content"] for chunk in page] == ["still live"]
    # and the database clock still prunes when it really is past the TTL
    harness.clock.advance(DEFAULTS.journal_chunk_ttl_s + 1)
    assert await harness.port.expire() >= 1
    try:
        await harness.port.read_owned(request.org_id, admission.job_handle, None, 10)
    except errors.JournalExpired:
        pass
    else:
        raise AssertionError("an expired journal still served events")


async def dur_output__an_expired_journal_is_gone_not_regenerated(factory):
    """DUR-OUTPUT: after the journal TTL the events are 410, and status remains."""
    harness = factory()
    jobs = hook(harness, "jobs")
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
    usage_bytes = hook(harness, "journal_bytes")
    jobs = hook(harness, "jobs")
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
    """DUR-FENCE: an expired or superseded lease appends nothing."""
    harness = factory()
    jobs = hook(harness, "jobs")
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
    limits = DEFAULTS.replace(journal_job_reserve_bytes=8192, journal_event_max_bytes=1024,
                              journal_total_bytes=1 << 20)
    harness = factory(limits=limits)
    request, admission, lease = await _stream_job(harness)
    event = "x" * 900
    for _ in range(7):
        # ~6.4 KiB of the 7 KiB an append may use: the last KiB of the 8 KiB
        # reservation is held back for the terminal event of the settling transaction.
        await harness.port.append(lease, b.events(event))
    try:
        await harness.port.append(lease, b.events(event))
    except errors.JournalCapacityExhausted as exc:
        assert errors.http_status(exc.code) == 429 and exc.retry_after_s >= 1
    else:
        raise AssertionError("a job stored past its per-job journal reservation")
    journal_bytes = hook(harness, "journal_bytes")
    assert journal_bytes() <= limits.journal_job_reserve_bytes


async def dur_settle__the_terminal_event_belongs_to_the_settling_transaction(factory):
    """DUR-SETTLE (02 §7): one transaction inserts the terminal outcome, the
    authoritative usage, the ledger settlement, the capacity releases *and* the
    terminal journal event. Reading the journal straight after the settlement must
    already show it: a crash between two writes would leave a settled job whose
    terminal event nothing ever repairs."""
    harness = factory()
    jobs = hook(harness, "jobs")
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
    jobs = hook(harness, "jobs")
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
        dur_output__any_committed_chunk_is_publication,
        dur_output__cursors_are_generation_then_sequence,
        api_stream__reads_are_bounded_and_ownership_checked,
        api_stream__a_cursor_past_the_head_is_invalid,
        dur_output__a_worker_cannot_forge_a_terminal_event,
        dur_output__no_terminal_event_anywhere_in_a_batch,
        dur_output__expiry_never_runs_on_a_callers_clock,
        dur_output__a_pruned_prefix_is_an_explicit_replay_gap,
        dur_output__an_expired_journal_is_gone_not_regenerated,
        dur_cap__stored_unexpired_bytes_keep_counting,
        dur_cap__a_job_cannot_store_past_its_journal_reservation,
        dur_fence__a_stale_worker_cannot_append,
        dur_output__an_oversize_event_is_refused,
        dur_output__the_terminal_event_is_written_once_with_the_settlement,
        dur_settle__the_terminal_event_belongs_to_the_settling_transaction,
    ]
