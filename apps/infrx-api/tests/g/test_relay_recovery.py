#!/usr/bin/env python3
"""G2 review round 2: acceptance that recovers, and waits that end on the stored bound.

    uv run --frozen pytest -q tests/g/test_relay_recovery.py

- A keyed replay is answered from the job the key maps to before anything is prepared
  (R91 `lookup`, money-B1).
- An acceptance cut short after admission is completed by the same-key retry its 503
  invites, not cancelled (money-B2).
- A refusal whose cancel is unconfirmed is not final (money-N1).
- A success whose usage is unknown is answered without counts (money-N2).
- A status read that fails once is retried (money-N4).
- The bound is the stored deadline plus the grace (money-N5, honesty-H-N3).
- The defensive paths (honesty-H-N4) have cases.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from infrx.contracts import errors, wire
from infrx.contracts.conformance import builders as b
from infrx.contracts.records import (HoldState, JobState, SettlementState, TerminalCause,
                                     TerminalOutcome, Usage)
from infrx.gateway.routes.relay import CREDIT

from . import relay_support as rs


def refusing_host() -> httpx.MockTransport:
    """The customer's media host once the presigned URL has expired."""
    return httpx.MockTransport(lambda request: httpx.Response(403))


def payloads(world) -> list[str]:
    return [key for key in world.objects.objects if key.startswith("payloads/")]


def staging(world) -> list:
    """What every `stage` call returned, in order: [0] is the first acceptance's refs."""
    stage, staged = world.media.stage, []

    async def recorded(org_id, request):
        staged.append(await stage(org_id, request))
        return staged[-1]

    world.media.stage = recorded
    return staged


def ids(refs) -> list:
    return [(ref.handle, ref.digest) for ref in refs]


def dies_before_the_wait(world) -> None:
    """The next acceptance completes (admitted, rechecked, attached), then its handler dies
    before the wait: the answer is lost and the job runs on (a SIGKILL, say)."""
    async def accept(auth, request, idem):
        await world.relay.admit(auth, request, idem)
        del world.relay._accept
        raise RuntimeError("the process died before the wait")

    world.relay._accept = accept


async def settle(world, text: str = "Two people unload boxes.") -> None:
    """The one job run to a success by hand, in either regime (W's runner cannot run a
    CREDIT job before WorkV2): prepared, leased, its result stored, then completed."""
    lease = await world.lease()
    ref = await world.put_result(lease.job_id, text)
    outcome = b.outcome(lease.job_id, world, tokens=Usage.of(1200, 5), result_ref=ref)
    complete = world.jobs.complete_credit if world.regime == CREDIT else world.jobs.complete
    await complete(lease, outcome)


# --- money-B1: a keyed replay is answered before anything is prepared ------------------
def test_dur_output__a_lost_answer_is_recovered_by_key_after_the_media_url_expired():
    """R91: the first answer (a video job, settled) is lost; by the time the client retries
    with the same key, the media host refuses the URL. The retry is answered from the
    committed job - the same 200, marked replayed - and nothing is fetched or staged again."""
    world = rs.World()
    world.during.append(world.work)
    first = rs.run(rs.call(world.app, rs.body(rs.VIDEO), key="clip-1"))
    assert first.status == 200, first.body
    staged = payloads(world)
    world.media.fetcher.transport = refusing_host()
    again = rs.run(rs.call(world.app, rs.body(rs.VIDEO), key="clip-1"))
    assert again.status == 200, again.body
    assert again.json() == first.json()
    assert again.headers.get(wire.HEADER_IDEMPOTENCY_REPLAYED) == "true"
    assert payloads(world) == staged and len(world.jobs.jobs) == 1


@pytest.mark.parametrize("lookup", ["served", "refused_until_d5"])
def test_dur_output__a_terminal_replay_is_answered_as_committed_not_rechecked(lookup):
    """A settled job's replay answers what was committed, whatever changed since: here the
    deployment's approved card rotated after the first answer. Nothing of acceptance is
    re-run on a terminal job - through R91's lookup, and through admission's own replay
    answer while the PostgreSQL store refuses the lookup (`param="lookup"`, until D5)."""
    world = rs.World(regime=CREDIT)
    if lookup == "refused_until_d5":
        async def refused(org_id, idem):
            raise errors.UnsupportedParameter("JobStore.lookup is D5's (R91)", param="lookup")
        world.jobs.lookup = refused
    world.during.append(lambda: world.clock.advance(3_600))
    first = rs.run(rs.call(world.app, rs.body(), key="k-5"))
    assert (first.status, first.json()["error"]["code"]) == (504, "deadline_exceeded")
    world.relay.active_rate_card_version = "rc_rotated_since"
    again = rs.run(rs.call(world.app, rs.body(), key="k-5"))
    assert again.headers.get(wire.HEADER_IDEMPOTENCY_REPLAYED) == "true"
    assert (again.status, again.json()["error"]["code"]) == (504, "deadline_exceeded")
    assert rs.state_of(again.json()["error"]) == "cancelled" and len(world.jobs.jobs) == 1


def test_dur_admit__a_key_naming_a_job_of_another_regime_is_a_conflict():
    """One key, one regime (the store's own admit rule), also when the lookup answers: a
    legacy relay asked with a key that names a CREDIT job refuses it as a conflict rather
    than answering another regime's job."""
    world = rs.World(regime=CREDIT)
    world.during.append(lambda: world.clock.advance(3_600))
    rs.run(rs.call(world.app, rs.body(), key="k-6"))
    world.regime = rs.LEGACY
    world.restart()
    reply = rs.run(rs.call(world.app, rs.body(), key="k-6"))
    assert (reply.status, reply.json()["error"]["code"]) == (409, "idempotency_conflict")
    assert len(world.jobs.jobs) == 1


def test_dur_output__an_in_flight_replay_prepares_nothing_and_answers_when_the_host_fails():
    """R91 (review r2 money-B1): a video job was accepted - admitted and attached - and its
    handler died before the wait. The same-key retry arrives once the media URL expired:
    nothing is fetched or staged for a mapped job, its bound refs are left as they are,
    and the retry answers the job's result."""
    world = rs.World()
    dies_before_the_wait(world)
    first = rs.run(rs.call(world.app, rs.body(rs.VIDEO), key="clip-3"))
    assert first.status == 500, first.body
    job = world.only_job()
    assert job.outcome is None and job.id in world.media.by_job
    bound, staged = world.media.by_job[job.id], payloads(world)
    world.media.fetcher.transport = refusing_host()
    world.during.append(world.work)
    again = rs.run(rs.call(world.app, rs.body(rs.VIDEO), key="clip-3"))
    assert again.status == 200, again.body
    assert again.headers.get(wire.HEADER_IDEMPOTENCY_REPLAYED) == "true"
    assert payloads(world) == staged and world.media.by_job[job.id] == bound
    assert world.only_job() is job and job.outcome.state is JobState.succeeded


# --- money-B2: an acceptance cut short is completed by the same-key retry --------------
def test_dur_admit__an_outage_after_admission_leaves_the_job_for_the_same_key_retry():
    """The media store fails while the staged refs are bound to the admitted job: a
    retryable 503, and the job is left (not cancelled as the customer's). The same-key
    retry completes the acceptance - the idempotent attach of the refs the first
    acceptance staged, never a new preparation (review r2 money-N1) - and gets the
    result."""
    world = rs.World()
    staged = staging(world)
    attach = world.media.attach
    failures = [OSError("object store unreachable at s3.internal:443")]

    async def flaky_attach(job_id, refs):
        if failures:
            raise failures.pop()
        return await attach(job_id, refs)

    world.media.attach = flaky_attach
    first = rs.run(rs.call(world.app, rs.body(rs.VIDEO), key="k-2"))
    assert (first.status, first.json()["error"]["code"]) == (503, "dependency_unavailable")
    assert first.headers.get(wire.HEADER_RETRY_AFTER)
    job = world.only_job()
    assert job.outcome is None, job.outcome
    world.during.append(world.work)
    again = rs.run(rs.call(world.app, rs.body(rs.VIDEO), key="k-2"))
    assert again.status == 200, again.body
    assert again.headers.get(wire.HEADER_IDEMPOTENCY_REPLAYED) == "true"
    assert world.only_job() is job and job.outcome.state is JobState.succeeded
    assert len(staged) == 1 and ids(world.media.by_job[job.id]) == ids(staged[0]) != []


@pytest.mark.parametrize("regime", [rs.LEGACY, CREDIT])
@pytest.mark.parametrize("lookup", ["served", "refused_until_d5"])
def test_dur_admit__a_crash_after_the_admission_commit_is_completed_by_the_retry(lookup, regime):
    """G3's DUR-ADMIT probe (G3 request (b)2): the admission committed and the answer was
    lost before the staged refs were bound to the job. The same-key retry finds the job in
    flight and attaches the refs the first acceptance staged (review r2 money-N1), so the
    job is prepared and answered - rather than ending unbilled at its preparation deadline.
    Through R91's lookup, and through admission's replay answer while the store refuses
    the lookup (until D5, when the retry re-prepares before admission says "replay"); in
    both regimes, the CREDIT one to a settled 200 (review r2 money-N5)."""
    world = rs.World(regime=regime)
    staged = staging(world)
    if lookup == "refused_until_d5":
        async def refused(org_id, idem):
            raise errors.UnsupportedParameter("JobStore.lookup is D5's (R91)", param="lookup")
        world.jobs.lookup = refused
    world.failures.crash_after_commit("admit")
    first = rs.run(rs.call(world.app, rs.body(rs.VIDEO), key="clip-9"))
    assert (first.status, first.json()["error"]["code"]) == (503, "dependency_unavailable")
    job = world.only_job()
    assert job.outcome is None and job.id not in world.media.by_job
    world.during.append(lambda: settle(world))
    again = rs.run(rs.call(world.app, rs.body(rs.VIDEO), key="clip-9"))
    assert again.status == 200, again.body
    assert again.headers.get(wire.HEADER_IDEMPOTENCY_REPLAYED) == "true"
    assert world.only_job() is job and job.outcome.state is JobState.succeeded
    assert ids(world.media.by_job[job.id]) == ids(staged[0]) != []
    if regime == CREDIT:
        assert world.jobs.jobs[job.id].settlement is not None and world.released(job)


@pytest.mark.parametrize("restarted", [False, True])
def test_dur_admit__a_replay_never_rechecks_or_cancels_a_job_that_may_be_running(restarted):
    """Review r2 money-B2. A CREDIT job was accepted (admitted, rechecked, attached) and its
    answer lost; a worker leased it and committed output; then the deployment's approved
    card rotated (a redeploy - also after a gateway restart, when this process holds none
    of the job's media state). The same-key retry is answered from the job: not rechecked
    against the new card, not re-attached, never cancelled - it gets the job's result."""
    world = rs.World(regime=CREDIT)
    dies_before_the_wait(world)
    first = rs.run(rs.call(world.app, rs.body(), key="k-8"))
    assert first.status == 500, first.body
    job, box = world.only_job(), {}

    async def runs():
        box["lease"] = await world.lease()
        await world.commit(box["lease"], "Two ", "people")

    rs.run(runs())
    if restarted:
        world.restart()
    world.relay.active_rate_card_version = "rc_rotated_since"

    async def settles():
        assert job.state is JobState.running, job.state          # untouched by the replay
        ref = await world.put_result(job.id, "Two people")
        await world.jobs.complete_credit(box["lease"], b.outcome(
            job.id, world, tokens=Usage.of(1200, 5), result_ref=ref))

    world.during.append(settles)
    again = rs.run(rs.call(world.app, rs.body(), key="k-8"))
    assert again.status == 200, again.body
    assert again.headers.get(wire.HEADER_IDEMPOTENCY_REPLAYED) == "true"
    assert again.json()["choices"][0]["message"]["content"] == "Two people"
    assert job.outcome.state is JobState.succeeded and world.released(job)


def test_dur_admit__a_catalog_outage_after_a_credit_admission_is_retryable():
    """CREDIT: the catalog read behind the pinned-revision recheck fails after
    `admit_credit` committed. That is a 503 with the job left, and the same-key retry
    passes the recheck, attaches, and answers the settled job (review r2 money-N5)."""
    world = rs.World(regime=CREDIT)
    serving = world.catalog.serving_revision
    failed = []

    async def flaky_serving(serving_version_id):
        if world.jobs.jobs and not failed:      # the first read after admission committed
            failed.append(serving_version_id)
            raise ConnectionError("postgresql://infrx:secret@db/catalog reset")
        return await serving(serving_version_id)

    world.catalog.serving_revision = flaky_serving
    first = rs.run(rs.call(world.app, rs.body(), key="k-3"))
    assert (first.status, first.json()["error"]["code"]) == (503, "dependency_unavailable")
    assert b"secret" not in first.body
    job = world.only_job()
    assert job.outcome is None, job.outcome
    world.during.append(lambda: settle(world))
    again = rs.run(rs.call(world.app, rs.body(), key="k-3"))
    assert again.headers.get(wire.HEADER_IDEMPOTENCY_REPLAYED) == "true"
    assert again.status == 200, again.body
    assert world.only_job() is job and job.outcome.state is JobState.succeeded
    assert world.jobs.jobs[job.id].settlement is not None and world.released(job)


# --- money-N1: a refusal whose cancel is unconfirmed is not final ------------------------
def test_api_modes__a_refusal_whose_cancel_is_unconfirmed_is_not_final():
    """An unapproved card refuses a CREDIT admission, but the cancel cannot be confirmed.
    The answer is a retryable 503 (the job was never bound to its media, so it cannot run),
    and the same-key retry cancels it and answers the refusal."""
    world = rs.World(regime=CREDIT)
    world.relay.active_rate_card_version = "rc_not_approved_here"
    cancel = world.jobs.cancel

    async def unreachable(org_id, handle, **cause):
        raise ConnectionError("postgresql://infrx:secret@db/infrx is unreachable")

    world.jobs.cancel = unreachable
    first = rs.run(rs.call(world.app, rs.body(), key="k-4"))
    assert (first.status, first.json()["error"]["code"]) == (503, "dependency_unavailable")
    job = world.only_job()
    assert job.outcome is None and job.id not in world.media.by_job
    world.jobs.cancel = cancel
    again = rs.run(rs.call(world.app, rs.body(), key="k-4"))
    assert (again.status, again.json()["error"]["code"]) == (400, "invalid_request")
    assert job.state is JobState.cancelled and world.released(job)


# --- money-N2: a success whose usage is unknown -------------------------------------------
def test_api_modes__a_success_with_unknown_usage_is_answered_without_counts():
    """A committed success whose usage is unknown (published, `held_unknown`) is still the
    customer's result: 200 with the text, finish `stop`, and no invented counts."""
    world = rs.World()

    async def settle_without_usage():
        lease = await world.lease()
        await world.commit(lease, "Two people")
        ref = await world.put_result(lease.job_id, "Two people.")
        await world.jobs.complete(lease, TerminalOutcome(
            job_id=lease.job_id, state=JobState.succeeded, cause=TerminalCause.completed,
            usage=None, result_ref=ref, settlement_state=SettlementState.released_free,
            settled_at=world.clock.now()))

    world.during.append(settle_without_usage)
    reply = rs.run(rs.call(world.app, rs.body()))
    assert reply.status == 200, reply.body
    answer = reply.json()
    assert world.only_job().outcome.settlement_state is SettlementState.held_unknown
    assert answer["choices"][0]["message"]["content"] == "Two people."
    assert answer["choices"][0]["finish_reason"] == "stop" and "usage" not in answer


# --- money-N4 / honesty-H-N4(b): a status read that fails once ----------------------------
def test_api_modes__a_status_read_that_fails_once_is_retried_not_the_end():
    """One failed status read during a sync wait is the database, not the job: the wait
    polls again and answers the result; the job is not cancelled."""
    world = rs.World()
    world.failures.fail("get_owned", on_call=1,
                        error=ConnectionError("postgresql://infrx:secret@db/infrx reset"))
    world.during.append(world.work)
    reply = rs.run(rs.call(world.app, rs.body()))
    assert reply.status == 200, reply.body
    assert world.only_job().outcome.state is JobState.succeeded
    assert b"secret" not in reply.body


# --- money-N5 / honesty-H-N3: the stored bound plus the grace ------------------------------
def test_api_modes__the_wait_ends_at_the_stored_deadline_plus_the_grace():
    """Legacy: admission clamps the request's deadline to the store's own budgets, and
    the gateway's bound is that stored instant plus the grace. It is not the request's
    later deadline, and it is not the stored instant itself."""
    world = rs.World(limits=rs.DEFAULTS.replace(generation_timeout_s=10.0))
    admit, cancel = world.jobs.admit, world.jobs.cancel
    at, requested = [], []

    async def admitted(request, idem):
        requested.append(request.deadline_at)
        return await admit(request, idem)

    world.jobs.admit = admitted

    async def recorded(org_id, handle, **cause):
        at.append(world.clock.now())
        return await cancel(org_id, handle, **cause)

    world.jobs.cancel = recorded
    grace = world.relay.grace_s

    def to(seconds):
        return lambda: world.clock.advance(
            (world.only_job().admission.deadline_at - world.clock.now()).total_seconds()
            + seconds)

    world.during += [to(grace / 2), to(grace + 1), lambda: world.clock.advance(3_600)]
    reply = rs.run(rs.call(world.app, rs.body()))
    admission = world.only_job().admission
    assert admission.deadline_at < requested[0]         # the store clamped it (R79)
    assert reply.status == 504 and len(at) == 1, (reply.status, at)
    assert (at[0] - admission.deadline_at).total_seconds() == pytest.approx(grace + 1)


def test_api_modes__a_pinned_revision_missing_from_the_catalog_is_not_found():
    """CREDIT: the revision admission pinned is gone from the catalog by the recheck.
    That is the contract's `not_found`, and the admitted job is cancelled (nothing ran)."""
    world = rs.World(regime=CREDIT)
    admit = world.jobs.admit_credit

    async def then_retired(request, idem):
        admission = await admit(request, idem)
        world.catalog.servings.pop(admission.pins.serving_version_id)
        return admission

    world.jobs.admit_credit = then_retired
    reply = rs.run(rs.call(world.app, rs.body()))
    assert (reply.status, reply.json()["error"]["code"]) == (404, "not_found")
    job = world.only_job()
    assert job.state is JobState.cancelled and world.released(job)


# --- honesty-H-N4(a)(c): the defensive paths -------------------------------------------------
def test_api_modes__a_task_cancelled_while_attaching_cancels_the_job():
    """The handler is cancelled while the refs are being bound to the admitted job (the
    process stopping). No identity reached the caller, so the job is cancelled."""
    world = rs.World()
    attach = world.media.attach

    async def interrupted(job_id, refs):
        asyncio.current_task().cancel()
        await asyncio.sleep(0)
        return await attach(job_id, refs)

    world.media.attach = interrupted

    async def body():
        with pytest.raises(asyncio.CancelledError):
            await rs.call(world.app, rs.body())
        await world.relay.drain(1.0)

    rs.run(body())
    job = world.only_job()
    assert job.state is JobState.cancelled
    assert world.jobs.holds[job.id].state is HoldState.released


def test_api_modes__an_invalid_media_outcome_is_rendered_as_unsupported_media():
    """A job whose media could not be prepared (`invalid_media`, free) is answered as the
    contract's `unsupported_media`, not as a server failure."""
    world = rs.World()

    async def refused_media():
        lease = await world.lease()
        await world.jobs.complete(lease, TerminalOutcome(
            job_id=lease.job_id, state=JobState.failed, cause=TerminalCause.invalid_media,
            usage=None, settlement_state=SettlementState.released_free,
            settled_at=world.clock.now()))

    world.during.append(refused_media)
    reply = rs.run(rs.call(world.app, rs.body()))
    error = reply.json()["error"]
    assert (reply.status, error["code"], rs.state_of(error)) == (400, "unsupported_media",
                                                                 "failed")
