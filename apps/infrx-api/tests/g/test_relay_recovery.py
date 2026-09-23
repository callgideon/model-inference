#!/usr/bin/env python3
"""G2 review round 2: acceptance that recovers.

    uv run --frozen pytest -q tests/g/test_relay_recovery.py

- A keyed replay is answered from the job the key maps to before anything is prepared
  (R91 `lookup`, money-B1).
- An acceptance cut short after admission is completed by the same-key retry its 503
  invites, not cancelled (money-B2).
- A refusal whose cancel is unconfirmed is not final (money-N1).
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from infrx.contracts import errors, wire
from infrx.contracts.records import (HoldState, JobState, SettlementState, TerminalCause,
                                     TerminalOutcome)
from infrx.gateway.routes.relay import CREDIT

from . import relay_support as rs


def refusing_host() -> httpx.MockTransport:
    """The customer's media host once the presigned URL has expired."""
    return httpx.MockTransport(lambda request: httpx.Response(403))


def payloads(world) -> list[str]:
    return [key for key in world.objects.objects if key.startswith("payloads/")]


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


# --- money-B2: an acceptance cut short is completed by the same-key retry --------------
def test_dur_admit__an_outage_after_admission_leaves_the_job_for_the_same_key_retry():
    """The media store fails while the staged refs are bound to the admitted job: a
    retryable 503, and the job is left (not cancelled as the customer's). The same-key
    retry completes the acceptance - the idempotent attach - and gets the result."""
    world = rs.World()
    attach = world.media.attach
    failures = [OSError("object store unreachable at s3.internal:443")]

    async def flaky_attach(job_id, refs):
        if failures:
            raise failures.pop()
        return await attach(job_id, refs)

    world.media.attach = flaky_attach
    first = rs.run(rs.call(world.app, rs.body(), key="k-2"))
    assert (first.status, first.json()["error"]["code"]) == (503, "dependency_unavailable")
    assert first.headers.get(wire.HEADER_RETRY_AFTER)
    job = world.only_job()
    assert job.outcome is None, job.outcome
    world.during.append(world.work)
    again = rs.run(rs.call(world.app, rs.body(), key="k-2"))
    assert again.status == 200, again.body
    assert again.headers.get(wire.HEADER_IDEMPOTENCY_REPLAYED) == "true"
    assert world.only_job() is job and job.outcome.state is JobState.succeeded


def test_dur_admit__a_catalog_outage_after_a_credit_admission_is_retryable():
    """CREDIT: the catalog read behind the pinned-revision recheck fails after
    `admit_credit` committed. That is a 503 with the job left, and the same-key retry
    passes the recheck and waits on the same job."""
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
    world.during.append(lambda: world.clock.advance(3_600))       # the wait ends
    again = rs.run(rs.call(world.app, rs.body(), key="k-3"))
    assert again.headers.get(wire.HEADER_IDEMPOTENCY_REPLAYED) == "true"
    assert (again.status, again.json()["error"]["code"]) == (504, "deadline_exceeded")
    assert world.only_job() is job and job.outcome.cause is TerminalCause.sync_deadline


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
