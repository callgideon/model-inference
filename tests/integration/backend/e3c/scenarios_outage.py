"""E3C s08: PostgreSQL, the object store and Valkey unavailable, on the namespace's own
containers (`harness.Faults`: pause = a drop that hangs, SIGKILL + `compose up` = an empty
Valkey). Oracles (04 P2, brief row 6): an acceptance during the outage answers a bounded,
retryable refusal instead of hanging; after the fault is reverted the SAME key recovers ONE
job that runs and settles once (PostgreSQL is the authority); a lost Valkey index is rebuilt
from PostgreSQL with no accepted job lost. Process replacement is s05's matrix."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import world                                            # noqa: E402

import stack                                            # noqa: E402

# R130 (G7): a request meeting one stalled dependency call answers a retryable 503 within
# 40 s (E3C phase 1 proposed 45 s; the ruling is tighter). The small s08 clip spends no
# preparation budget, so the 60 s variant (a full 40 s preparation, then a stall) is not it.
BOUND_S = 40.0
MEASURE_S = 180.0        # how long the client waits to measure a late answer


def bounded_refusal(trip, tenant, messages, key: str, record_property) -> dict:
    """The acceptance during the outage: a retryable 503 (or a typed 504) within BOUND_S.
    The client waits up to MEASURE_S so the answer time is MEASURED (and recorded) even when
    it is past the bound (F-2), then the bound is asserted."""
    import httpx
    began, answer = time.monotonic(), None
    try:
        with httpx.Client(base_url=trip.box.url, timeout=MEASURE_S) as http:
            answer = http.post("/v1/jobs", headers=trip.headers(tenant, key),
                               json={"model": stack.CREDIT_ALIAS, "messages": messages})
    except httpx.TimeoutException:
        pass
    took = time.monotonic() - began
    seen = {"status": answer.status_code if answer is not None else None,
            "code": world.code(answer) if answer is not None else None,
            "seconds": round(took, 1)}
    record_property("refusal", seen)
    assert answer is not None, f"no answer within {MEASURE_S:.0f} s: an unbounded wait on " \
                               "the unavailable dependency"
    assert took <= BOUND_S, f"answered only after {took:.1f} s ({answer.status_code})"
    assert answer.status_code in (503, 504), \
        f"not a retryable refusal: {answer.status_code} {answer.text[:200]}"
    return seen


def recovers_once(trip, tenant, messages, key: str) -> None:
    again = trip.send(tenant, "async", messages, key)
    assert again.status_code == 202, f"after the outage: {again.status_code} {again.text[:200]}"
    jobs = world.job_of(trip, tenant.org_id, key)
    assert len(jobs) == 1, f"one key, {len(jobs)} jobs"
    assert world.terminal(trip, jobs[0][0], timeout=60.0) == "succeeded"
    world.settled_once(trip, jobs[0][0])
    trip.conserved(tenant)


@pytest.mark.parametrize("service", ["postgres", "s3"])
def test_s08_an_unavailable_store_is_a_bounded_refusal_then_one_job(workdir, service,
                                                                    record_property):
    with world.composed(workdir) as trip:
        alpha = trip.world.alpha
        warm = trip.send(alpha, "sync", world.TEXT, None)      # the key is cached before
        assert warm.status_code == 200, warm.text
        messages = world.TEXT if service == "postgres" else world.video_url("s08")
        key = f"e3c-s08-{service}"
        with stack.harness.Faults() as faults:
            faults.pause(service)
            bounded_refusal(trip, alpha, messages, key, record_property)
        recovers_once(trip, alpha, messages, key)


def test_s08_a_lost_valkey_index_is_rebuilt_and_loses_no_accepted_job(workdir):
    """Accepted with no worker running; Valkey SIGKILLed and restarted empty (no RDB/AOF);
    then the worker: the job is found through PostgreSQL and runs once."""
    with world.composed(workdir, start=("gateway",)) as trip:
        alpha = trip.world.alpha
        accepted = trip.send(alpha, "async", world.TEXT, "e3c-s08-valkey")
        assert accepted.status_code == 202, accepted.text
        request_id = accepted.json()["request_id"]
        with stack.harness.Faults() as faults:
            faults.kill_container("valkey")
        stack.harness.wait_valkey()
        assert not list(stack.harness.valkey_client().scan_iter("*")), "Valkey kept its index"
        trip.box.start("worker")
        assert world.terminal(trip, request_id, timeout=90.0) == "succeeded"
        world.settled_once(trip, request_id)
        trip.conserved(alpha)
