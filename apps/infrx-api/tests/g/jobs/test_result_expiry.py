#!/usr/bin/env python3
"""RESULT-EXPIRY (G7 item 3, RV-11): the persisted `result_expires_at` is the one authority
for every read - status, result, an idempotent sync replay, a restarted gateway.

The routes used to recompute the expiry from the *current* `result_ttl_s` over
`settled_at`, so a retune after settlement moved an already promised lifetime, and a row
settled before the expiry was carried read as available. Now every read applies F2C.b's
`lifecycle.read_outcome` (store clock), and an expiry is never recomputed from settings.

States a reader can see, on `GET /v1/jobs/{h}/result` (status mirrors them):
not terminal 409 `result_pending`; available 200 with the response; a terminal
non-success or held usage 200 without one; past the persisted expiry, or a success with
none persisted, 410 `result_expired` - while status metadata (state, cause, usage) stays
readable; unknown, malformed and another tenant's handle one identical 404.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from infrx.contracts import wire
from infrx.contracts.fixtures import DIR as FIXTURES
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import TerminalOutcome

from .. import relay_support as rs
from .test_jobs import post, refusal, result, status
from .world import JobsWorld

CASES = json.loads((FIXTURES.parent / "v2" / "result_read_cases.json").read_text())
# F2C.b's classification -> what the result route answers.
ROUTE = {"pending": (409, "result_pending"), "available": (200, "response"),
         "no_result": (200, None), "held_unknown": (200, None),
         "expired": (410, "result_expired"), "unavailable": (410, "result_expired")}


def settled(world):
    assert post(world).status == 202
    rs.run(world.work())
    return world.only_job()


def test_result_expiry__a_ttl_retune_never_moves_a_promised_expiry():
    """RV-11: the configured TTL changes after settlement (a restart with another
    RESULT_TTL_S). Status reports, and the result route honours, the instant the settling
    transaction persisted - a shorter TTL does not cut it, a longer one does not extend it."""
    world = JobsWorld()
    job = settled(world)
    promised = job.outcome.result_expires_at
    ttl = world.limits.result_ttl_s
    assert promised == job.outcome.settled_at + timedelta(seconds=ttl)
    for retuned in (ttl * 2, ttl / 4):
        world.restart()                                   # a new gateway process...
        world.relay.limits = world.limits.replace(result_ttl_s=retuned)   # ...retuned
        assert wire.JobStatus.model_validate(status(world).json()).result_expires_at == promised
    world.clock.advance(ttl * 3 / 4)                      # past the shortened TTL
    assert result(world).status == 200
    world.relay.limits = world.limits.replace(result_ttl_s=ttl * 2)
    world.clock.advance(ttl / 4)                          # exactly the promised instant
    assert refusal(result(world)) == (410, "result_expired")
    assert status(world).json()["result_available"] is False


def test_result_expiry__a_success_with_no_persisted_expiry_is_never_served():
    """A row settled before the expiry was carried: no instant is invented from settings -
    the content is 410 while the metadata (state, usage) stays readable."""
    world = JobsWorld()
    job = settled(world)
    job.outcome = job.outcome.model_copy(update={"result_expires_at": None})
    assert refusal(result(world)) == (410, "result_expired")
    body = status(world).json()
    assert (body["state"], body["result_available"], body["usage_certainty"]) == (
        "succeeded", False, "authoritative")
    assert "result_expires_at" not in body and body["usage"]


def test_result_expiry__a_sync_replay_after_the_expiry_is_410_never_the_content():
    """An idempotent sync replay is a read too: before the persisted expiry it answers the
    committed result - even with the gateway's clock past it (the store clock judges,
    R29/R79); after it (the key still answering: the idempotency TTL is longer), and for a
    success with no persisted expiry, `result_expired` - never the content again."""
    world = JobsWorld(limits=DEFAULTS.replace(result_ttl_s=3_600.0))
    world.during.append(world.work)
    first = rs.run(rs.call(world.app, rs.body(), key="k-sync"))
    assert first.status == 200, first.body
    world.clock.advance(3_000)
    world.skew_s = 3_600.0                                # the gateway past, the store not
    again = rs.run(rs.call(world.app, rs.body(), key="k-sync"))
    assert (again.status, again.json()) == (200, first.json())
    job = world.only_job()
    promised = job.outcome
    job.outcome = promised.model_copy(update={"result_expires_at": None})
    unpersisted = rs.run(rs.call(world.app, rs.body(), key="k-sync"))
    assert refusal(unpersisted) == (410, "result_expired")
    job.outcome = promised
    world.clock.advance(600)                              # the store at the promised instant
    late = rs.run(rs.call(world.app, rs.body(), key="k-sync"))
    assert refusal(late) == (410, "result_expired")
    assert len(world.jobs.jobs) == 1                      # nothing re-admitted


def test_result_expiry__the_route_answers_f2c_b_s_classification_table():
    """Every row of F2C.b's cross-language table (`result_read_cases.json`), installed on a
    job and read at the row's store instant: the result route answers what its class
    means, and status reports availability and the persisted instant only when available."""
    world = JobsWorld()
    job = settled(world)
    ref = job.outcome.result_ref
    for case in CASES:
        outcome = case.get("outcome")
        if outcome is not None:
            outcome = TerminalOutcome.model_validate(
                {**outcome, "job_id": job.id,
                 **({"result_ref": ref} if outcome.get("result_ref") else {})})
        job.outcome = outcome
        now = datetime.fromisoformat(case["now"].replace("Z", "+00:00"))

        async def at(now=now):
            return now

        world.jobs.db_now = at
        reply = result(world)
        code, what = ROUTE[case["expected"]]
        assert reply.status == code, (case["name"], reply.body)
        if code == 200:
            assert ("response" in reply.json()) is (what == "response"), case["name"]
        elif what is not None:
            assert reply.json()["error"]["code"] == what, case["name"]
        body = status(world).json()
        available = case["expected"] == "available"
        assert body["result_available"] is available, case["name"]
        assert ("result_expires_at" in body) is available, case["name"]
