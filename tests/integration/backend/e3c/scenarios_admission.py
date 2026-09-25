"""E3C s04 (ADMISSION-READY, RV-05): no preparation or inference before durable eligibility.

The gateway process is held at the step between the admission commit and the completion of
its acceptance (`Relay._admitted`: the CREDIT card / capability rechecks, then the attach),
with the real worker process running. The oracle is F2C-L's (R110): a job is executed only
once the store records it ready (`world.durably_ready`, D10's `ReadinessStore.readiness`); a
tree without that record has no durable eligibility at all, so execution there is RV-05.
Bounded recovery: a gateway SIGKILLed at that step leaves no permanent orphan hold (R111: a
marker-less job ends at its preparation deadline, released free) - asserted with the
database clock moved past every deadline. A permanent preparation refusal ends the job on
its first refusal, not after lapsed leases (D-19, W5)."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import world                                            # noqa: E402

import stack                                            # noqa: E402

WINDOW_S = 8.0           # how long the held acceptance is watched for premature execution
PAST_EVERY_DEADLINE_S = 7200.0


def held_request(trip, tenant, messages, key: str) -> tuple[threading.Thread, dict]:
    """`POST /v1/jobs` on a thread of its own (the gateway will hold it); its answer lands in
    the dict when the gateway answers or dies."""
    import httpx
    answer: dict = {}

    def send():
        try:
            with httpx.Client(base_url=trip.box.url, timeout=120.0) as http:
                answer["response"] = http.post("/v1/jobs", headers=trip.headers(tenant, key),
                                               json={"model": stack.CREDIT_ALIAS,
                                                     "messages": messages})
        except Exception as gone:                     # noqa: BLE001 - a killed gateway
            answer["error"] = type(gone).__name__
    thread = threading.Thread(target=send, daemon=True)
    thread.start()
    return thread, answer


def admitted_while_held(trip, tenant, messages, key: str) -> str:
    """Start the held request; the request id the gateway admitted before it held."""
    held_request(trip, tenant, messages, key)
    trip.box.reached("gateway")
    (request_id, state), = world.wait_for(lambda: world.job_of(trip, tenant.org_id, key), 10,
                                          "the admission row")
    return request_id


def not_executed_while_held(trip, request_id: str) -> None:
    """For WINDOW_S: no preparation claim (R110: `claim_preparation` refuses `not_ready`),
    no prepared count, no inference, no charge - unless the store records the job ready. The
    whole window is observed, so the failure says how far the job got."""
    end, seen = time.monotonic() + WINDOW_S, {}
    while time.monotonic() < end:
        ran = world.executed(trip, request_id)
        if any(ran.values()) and not world.durably_ready(trip, request_id):
            seen = {k: max(v, seen.get(k, 0)) for k, v in ran.items()}
        time.sleep(0.2)
    assert not seen, f"executed before durable eligibility (RV-05): {seen}"


def orphan_bounded(trip, request_id: str) -> None:
    """Past every deadline the job is terminal and its hold is not held (no permanent orphan)."""
    world.set_clock(trip.world.database, PAST_EVERY_DEADLINE_S)
    world.terminal(trip, request_id, timeout=90.0)
    assert "held" not in world.money(trip, request_id)["holds"], world.money(trip, request_id)


def hold_point() -> str:
    """The post-admission step, or - on a tree that admits in one phase (F2C-L R110) - the
    admission commit itself, where the job is ready and executing it is correct."""
    return "readiness" if world.has_point("readiness") else "admission"


@pytest.mark.parametrize("media", ["text", "video"])
def test_s04_nothing_executes_while_the_acceptance_is_incomplete(workdir, media):
    """Held between admission and the acceptance's completion: the worker executes nothing
    (text-only is RV-05's case: its preparer waits for no attach); then the gateway is
    SIGKILLed there and the job cannot hold money for ever."""
    with world.composed(workdir, start=("worker",)) as trip:
        trip.box.start("gateway", INFRX_E3C_BARRIER=hold_point())
        messages = world.TEXT if media == "text" else world.video_url("s04")
        request_id = admitted_while_held(trip, trip.world.alpha, messages, f"e3c-s04-{media}")
        try:
            not_executed_while_held(trip, request_id)
        finally:
            trip.box.kill("gateway")
        orphan_bounded(trip, request_id)
        world.settled_once(trip, request_id)


def unserve_text(trip) -> None:
    """The pinned serving revision stops declaring text input (a capability change the
    acceptance must honour). Registry rows are immutable by trigger, so on this disposable
    clone the trigger is lifted for the one update, as its owner."""
    import psycopg
    with psycopg.connect(stack.harness.pg_dsn(trip.world.database), autocommit=True) as conn:
        conn.execute("alter table infrx.serving_versions disable trigger user")
        conn.execute("update infrx.serving_versions set capability = jsonb_set(capability, "
                     "'{input_modalities}', '[\"video\"]') where serving_version_id = %s",
                     (trip.world.alpha.pins.serving_version_id,))
        conn.execute("alter table infrx.serving_versions enable trigger user")


def test_s04_late_rejection_refuses_before_any_execution(workdir):
    """A text request whose capability is withdrawn between admission and the acceptance's
    recheck: refused, cancelled, nothing prepared, run or charged, hold released. On a tree
    that admits in one phase the capability is checked in the admission transaction, so the
    same withdrawal before the request admits nothing at all."""
    with world.composed(workdir, start=("worker",)) as trip:
        alpha = trip.world.alpha
        if not world.has_point("readiness"):
            trip.box.start("gateway")
            unserve_text(trip)
            refused = trip.send(alpha, "async", world.TEXT, "e3c-s04-late")
            assert refused.status_code in (400, 404, 415), refused.text
            assert world.job_of(trip, alpha.org_id, "e3c-s04-late") == []
            return
        trip.box.start("gateway", INFRX_E3C_BARRIER="readiness")
        thread, answer = held_request(trip, alpha, world.TEXT, "e3c-s04-late")
        trip.box.reached("gateway")
        (request_id, _), = world.wait_for(lambda: world.job_of(trip, alpha.org_id,
                                                               "e3c-s04-late"), 10, "admission")
        unserve_text(trip)
        try:
            not_executed_while_held(trip, request_id)
        finally:
            trip.box.release("gateway")
            thread.join(timeout=60)
        response = answer.get("response")
        assert response is not None and response.status_code in (400, 404, 415), answer
        assert world.terminal(trip, request_id) == "cancelled"
        assert not any(world.executed(trip, request_id).values()), world.executed(trip, request_id)
        world.settled_once(trip, request_id)


def test_s04_a_permanent_preparation_refusal_ends_the_job_on_its_first_refusal(workdir):
    """A job whose preparation meets a PERMANENT refusal - the engine counts its prompt past
    the job's context (`context_length_exceeded`, W5 item 3's `PERMANENT` class) - fails on
    the first preparation attempt, `preparation_failed`, its hold released once: not after
    lapsed preparation leases (D-19). (Phase 1-2 deleted the staged object instead; W5 rules
    a vanished object `not_found`, TEMPORARY - retried within `preparation_deadline_at` -
    so that premise no longer names a permanent refusal.)"""
    with world.composed(workdir, start=("gateway",)) as trip:
        alpha = trip.world.alpha
        accepted = trip.send(alpha, "async", world.TEXT, "e3c-s04-context")
        assert accepted.status_code == 202, accepted.text
        request_id = accepted.json()["request_id"]
        trip.engine.control(tokenize_count=OVER_CONTEXT)
        trip.box.start("worker")
        began = time.monotonic()
        state = world.terminal(trip, request_id, timeout=45.0)
        assert state == "failed", state
        cause, = trip.one("select outcome_cause from infrx.jobs where request_id = %s",
                          request_id)
        assert cause == "preparation_failed", cause
        assert world.attempts(trip, request_id, "preparation") == 1, \
            f"a permanent refusal took {world.attempts(trip, request_id, 'preparation')} " \
            f"preparation attempts ({time.monotonic() - began:.0f} s)"
        assert world.attempts(trip, request_id) == 0, "a refused preparation reached inference"
        world.settled_once(trip, request_id)


#: Past every context the pilot serves (`MAX_CONTEXT_TOKENS` 32,768).
OVER_CONTEXT = 1_000_000
