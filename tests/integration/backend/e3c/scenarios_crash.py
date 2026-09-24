"""E3C s05: a real process crash at each step of the brief's list - upload, admission,
readiness, attachment, preparation, outbox, claim, output, settlement.

Each case holds ONE box process at the named step of the real code path (`world.POINTS`),
SIGKILLs its process group, starts a replacement and drives recovery the way the product
would (the client retries under the same key; the database clock is moved past the lease or
redelivery window a lost holder would have kept). The oracle is the brief's: the job ends
once, never executes before its acceptance completed, keeps its authorized media, has one
output and at most one settlement (E3B's port-level drills dr01-dr07 are the in-process
versions; these cross the process boundary with a real kill)."""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import world                                            # noqa: E402

import pilotbox                                         # noqa: E402
import stack                                            # noqa: E402
from scenarios_admission import held_request, not_executed_while_held  # noqa: E402
from scenarios_upload import complete, create, put, runs_on             # noqa: E402

GATEWAY = ("upload", "admission", "readiness", "attachment", "outbox")
WORKER = ("prep", "claim", "output", "settle")
# Short leases, waited out in real time. Moving the database clock instead is not a lease
# lapse: the Valkey index schedules a requeued candidate at its database `available_at`,
# which the processes' wall clocks then reach only that much later (measured: a +121 s move
# left a requeued job unclaimed for 90 s).
LEASES = {"LEASE_TTL_S": "8", "LEASE_HEARTBEAT_S": "2", "PREPARATION_LEASE_TTL_S": "6"}


def recovered(trip, tenant, request_id: str, handle: str) -> dict:
    """Before publication: the job's one terminal outcome is success - settled once, prepared
    once from the engine's count, the API agreeing."""
    state = world.terminal(trip, request_id, timeout=90.0)
    cause, = trip.one("select outcome_cause from infrx.jobs where request_id = %s", request_id)
    assert state == "succeeded", \
        f"recovery ended the job {state} ({cause}): {world.executed(trip, request_id)}"
    world.settled_once(trip, request_id)
    status = trip.until_terminal(tenant, handle)
    assert status["state"] == "succeeded" and status["result_available"], status
    stored, = trip.one("select prepared_prompt_tokens from infrx.jobs where request_id = %s",
                       request_id)
    assert stored == pilotbox.ENGINE_PROMPT_TOKENS, stored
    trip.conserved(tenant)
    return status


def never_regenerated(trip, tenant, request_id: str, handle: str) -> None:
    """After publication (02 §6, E3B dr06): the output is never regenerated - the job ends
    `failed / lost_after_publication` with ONE inference generation, no charge and no hold
    left held; its journal carries that one generation only."""
    state = world.terminal(trip, request_id, timeout=90.0)
    cause, = trip.one("select outcome_cause from infrx.jobs where request_id = %s", request_id)
    assert (state, cause) == ("failed", "lost_after_publication"), (state, cause)
    assert world.attempts(trip, request_id) == 1, "a published job was run again"
    world.settled_once(trip, request_id)
    events = trip.http.get(f"/v1/jobs/{handle}/events", headers=trip.headers(tenant))
    generations = {frame_id.split("-")[0] for frame in pilotbox.frames(events.text)
                   if (frame_id := pilotbox.frame_id(frame))}
    assert len(generations) <= 1, f"frames from generations {sorted(generations)}"
    trip.conserved(tenant)


def held_put(trip, tenant, handle: str, data: bytes) -> None:
    """The PUT the gateway holds after storing the bytes (its answer never comes)."""
    import httpx
    try:
        with httpx.Client(base_url=trip.box.url, timeout=60.0) as http:
            put(http, trip, tenant, handle, data)
    except httpx.HTTPError:
        pass                                    # the gateway was killed under it


def retried(trip, tenant, messages, key: str) -> tuple[str, str]:
    """The client's same-key retry on the replacement gateway: the same job, replayed."""
    again = trip.send(tenant, "async", messages, key)
    assert again.status_code == 202, f"the same-key retry: {again.status_code} {again.text[:200]}"
    (request_id, _), = world.job_of(trip, tenant.org_id, key)
    assert again.json()["request_id"] == request_id
    return request_id, again.json()["job_handle"]


@pytest.mark.parametrize("point", GATEWAY)
def test_s05_a_gateway_crash_at_each_step_recovers_once(workdir, point):
    with world.composed(workdir, start=("worker",), **LEASES) as trip:
        alpha, key = trip.world.alpha, f"e3c-s05-{point}"
        trip.box.start("gateway", INFRX_E3C_BARRIER=point)
        if point == "upload":
            data = pilotbox.clip()
            ticket = create(trip.http, trip, alpha, data)
            handle = ticket.json()["upload_handle"]
            threading.Thread(target=held_put, args=(trip, alpha, handle, data),
                             daemon=True).start()
            trip.box.reached("gateway")
            trip.box.kill("gateway")
            trip.box.start("gateway")
            again = put(trip.http, trip, alpha, handle, data)
            assert again.status_code == 204, \
                f"the PUT retried after the crash: {again.status_code} {again.text[:200]}"
            assert complete(trip.http, trip, alpha, handle).status_code == 200
            runs_on(trip, trip.http, alpha, ticket.json()["destination_ref"], key)
            return
        messages = world.TEXT if point in ("readiness", "outbox") else world.video_url(point)
        if point == "outbox":
            accepted = trip.send(alpha, "async", messages, key)
            assert accepted.status_code == 202, accepted.text
            trip.box.reached("gateway")
            trip.box.kill("gateway")
            trip.box.start("gateway")               # the relay's claim comes back (30 s)
            recovered(trip, alpha, accepted.json()["request_id"], accepted.json()["job_handle"])
            return
        held_request(trip, alpha, messages, key)
        trip.box.reached("gateway")
        trip.box.kill("gateway")
        (request_id, _), = world.wait_for(lambda: world.job_of(trip, alpha.org_id, key), 10,
                                          "the admission row")
        if point == "readiness":
            # nobody completes this acceptance until the client retries: nothing may run
            not_executed_while_held(trip, request_id)
        trip.box.start("gateway")
        request_id, handle = retried(trip, alpha, messages, key)
        recovered(trip, alpha, request_id, handle)


@pytest.mark.parametrize("point", WORKER)
def test_s05_a_worker_crash_at_each_step_recovers_once(workdir, point):
    with world.composed(workdir, start=("gateway",), **LEASES) as trip:
        alpha, key = trip.world.alpha, f"e3c-s05-{point}"
        trip.box.start("worker", INFRX_E3C_BARRIER=point)
        messages = world.video_url(point) if point == "prep" else world.TEXT
        if point == "output":
            trip.engine.control(delta_gap_s=0.05)         # several appends, one of them held
        accepted = trip.send(alpha, "async", messages, key)
        assert accepted.status_code == 202, accepted.text
        request_id, handle = accepted.json()["request_id"], accepted.json()["job_handle"]
        trip.box.reached("worker")
        trip.box.kill("worker")
        trip.engine.control(delta_gap_s=0.0)
        trip.box.start("worker")                    # the lapsed lease is reaped, requeued
        if point in ("output", "settle"):           # the first chunk published the job
            never_regenerated(trip, alpha, request_id, handle)
        else:
            recovered(trip, alpha, request_id, handle)
