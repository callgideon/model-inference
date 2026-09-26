"""E3C s14 (DUR-FENCE): a stale generation races the new one at every mutation.

A real worker process is held right after its claim (`world.POINTS["claim"]`): it keeps
generation 1's lease and stops renewing it, as a paused or partitioned process does. The
lease lapses in real time (s05's LEASES), the reaper requeues the unpublished job and a NEW
generation claims it:

* `another-process` - a second worker process (another owner);
* `same-process` - the held process's own second runner (the SAME owner, so only the
  generation tells the two leases apart: the check nc-dur-fence removes).

While generation 2 streams, the held process is released to race on its own path, and
generation 1's token - the attempt row its claim minted, `infrx.lease_doc` - is presented at
every door the worker mutates through, through the product's own adapters on the box's own
login: append output, renew, load the work (R46), take a second inference or preparation
slot, write the result object (R147, D10 0026), settle. Oracle (04 DUR-FENCE): each refused
(`stale_lease` by the fence, `not_claimable` by the claims); generation 2 completes and
settles exactly once; the journal and the result are generation 2's alone; the engine served
one generation; the reservations are released once and the wallet reconciles."""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import world                                            # noqa: E402

from scenarios_crash import LEASES                      # noqa: E402

#: Generation 2's answer: 20 deltas of 12 characters (fake_vllm.CHUNK_SIZE), GAP_S apart -
#: 10 s of stream against the well under a second the stale probes take.
TEXT = "The van is red. " * 15
GAP_S = 0.5
STALE = "output of a generation that lost its lease"
#: Each door the stale generation tries, and the refusal the product owes it there. The
#: result door writes STALE, a text that differs from generation 2's (E3C-CELLS F-1: an
#: unfenced write of it ended generation 2 `platform_error`).
REFUSED = {"append": "stale_lease", "renew": "stale_lease", "load_work": "stale_lease",
           "claim": "not_claimable", "claim_preparation": "not_claimable",
           "result": "stale_lease", "settle": "stale_lease"}


def lease(trip, request_id: str, generation: int):
    """The inference lease of `generation` exactly as its claim minted it: the fencing token
    the process that claimed it holds."""
    from infrx.contracts.records import Lease
    doc, = trip.one("select infrx.lease_doc(a) from infrx.attempts a where a.job_id = %s and "
                    "a.kind = 'inference' and a.generation = %s", request_id, generation)
    return Lease.model_validate(doc)


def stale_probes(trip, stale) -> dict[str, str]:
    """`stale` at every door, in the worker's own call shapes, on the box's database login
    (the dedicated runtime login under INFRX_E3C_RUNTIME_LOGIN=1): the refusal's code, or
    ACCEPTED. Settlement last: accepted, it would end the job."""
    from infrx import config
    from infrx.contracts import errors
    from infrx.contracts.records import (ChunkEventType, EngineEvent, JobState,
                                         SettlementState, TerminalCause, TerminalOutcome)
    from infrx.gateway import pilot
    from infrx.state.jobstore import PgJobStore, connector
    from infrx.state.journal import PgStreamStore
    from infrx.state.lifecycle import PgLifecycle
    limits = config.from_env(trip.box.env).pilot
    connect = connector(limits.database_url,
                        set_role=not pilot.dedicated_login(limits.database_url))
    jobs = PgJobStore(connect, limits=limits)
    journal = PgStreamStore(connect, limits=limits)
    lifecycle = PgLifecycle(connect, limits=limits)
    proposal = TerminalOutcome(job_id=stale.job_id, state=JobState.failed,
                               cause=TerminalCause.platform_error,
                               settlement_state=SettlementState.released_free,
                               settled_at=datetime.now(timezone.utc))
    doors = {
        "append": lambda: journal.append(stale, (EngineEvent(type=ChunkEventType.delta,
                                                             payload={"visible": STALE}),)),
        "renew": lambda: jobs.heartbeat(stale),
        "load_work": lambda: jobs.load_work_credit(stale),
        "claim": lambda: jobs.claim(stale.job_id, stale.worker_id),
        "claim_preparation": lambda: lifecycle.claim_preparation(stale.job_id, stale.worker_id),
        "result": lambda: jobs.put_result(stale.job_id, STALE, stale),
        "settle": lambda: jobs.complete_credit(stale, proposal),
    }

    async def probe() -> dict[str, str]:
        answers = {}
        for door, call in doors.items():
            try:
                answers[door] = f"ACCEPTED ({type(await call()).__name__})"
            except errors.DomainError as refused:
                answers[door] = refused.code
        return answers
    return asyncio.run(probe())


@pytest.mark.parametrize("successor", ["another-process", "same-process"])
def test_s14_a_stale_generation_is_refused_at_every_mutation(workdir, successor,
                                                             record_property):
    same = successor == "same-process"
    with world.composed(workdir, start=("gateway",), **LEASES) as trip:
        alpha = trip.world.alpha
        trip.engine.control(text=TEXT, delta_gap_s=GAP_S)
        # one runner holds generation 1; `same-process` keeps a second one for generation 2
        trip.box.start("worker", INFRX_E3C_BARRIER="claim",
                       WORKER_CONCURRENCY="2" if same else "1")
        accepted = trip.send(alpha, "async", world.TEXT, f"e3c-s14-{successor}")
        assert accepted.status_code == 202, accepted.text
        request_id, handle = accepted.json()["request_id"], accepted.json()["job_handle"]
        held = trip.box.reached("worker")
        stale = lease(trip, request_id, 1)
        assert request_id in held["jobs"] and stale.worker_id in held["jobs"], (held, stale)
        world.wait_for(lambda: trip.db(
            "select 1 from infrx.attempts where job_id = %s and kind = 'inference' and "
            "generation = 1 and expires_at <= infrx.now()", request_id), 30,
            "generation 1's lease lapsed")
        other = None if same else world.second_worker(trip.box)
        try:
            if other is not None:
                other.start("worker")            # its start-up reap requeues the job
            world.wait_for(lambda: trip.db(
                "select 1 from infrx.stream_chunks where job_id = %s and generation = 2 and "
                "event_type = 'delta'", request_id), 60, "generation 2's first committed chunk")
            live = lease(trip, request_id, 2)
            record_property("owners", {"generation 1": stale.worker_id,
                                       "generation 2": live.worker_id})
            if (live.worker_id == stale.worker_id) != same:
                raise world.harness.HarnessError(
                    f"premise ({successor}): generation 2 is {live.worker_id}'s, generation 1 "
                    f"{stale.worker_id}'s")
            trip.box.release("worker")           # the held process races on its own path
            if not trip.db("select 1 from infrx.jobs j join infrx.attempts a on a.job_id = "
                           "j.request_id where j.request_id = %s and j.settled_at is null and "
                           "a.kind = 'inference' and a.generation = 2 and a.released_at is "
                           "null", request_id):
                raise world.harness.HarnessError(
                    f"premise: generation 2 ended before the stale probes (raise the stream "
                    f"length): {world.diagnose(trip, request_id)}")
            answers = stale_probes(trip, stale)
            record_property("stale_probes", answers)
            accepted_at = {door: answer for door, answer in answers.items()
                           if answer.startswith("ACCEPTED")}
            assert not accepted_at, f"stale generation 1 accepted at {sorted(accepted_at)} " \
                                    f"while generation 2 ran (DUR-FENCE): {answers}"
            if "already_terminal" in answers.values():
                raise world.harness.HarnessError(
                    f"premise: generation 2 settled during the stale probes: {answers}")
            assert answers == REFUSED, f"not the fence's refusals: {answers}"
            assert world.terminal(trip, request_id, timeout=90.0) == "succeeded", \
                world.diagnose(trip, request_id)
        finally:
            if other is not None:
                other.stop("worker")
        cause, = trip.one("select outcome_cause from infrx.jobs where request_id = %s",
                          request_id)
        assert cause == "completed", cause
        world.settled_once(trip, request_id)
        trip.conserved(alpha)
        assert trip.one("select count(*) from infrx.stream_chunks where job_id = %s and "
                        "generation = 1", request_id) == (0,), "generation 1's output stored"
        result = trip.http.get(f"/v1/jobs/{handle}/result", headers=trip.headers(alpha))
        assert result.status_code == 200, result.text
        assert result.json()["response"]["choices"][0]["message"]["content"] == TEXT, \
            f"not generation 2's answer: {result.text[:300]}"
        timeline = trip.db("select generation, released_at, acquired_at from infrx.attempts "
                           "where job_id = %s and kind = 'inference' order by generation",
                           request_id)
        assert [row[0] for row in timeline] == [1, 2], timeline
        assert timeline[0][1] is not None and timeline[0][1] <= timeline[1][2], \
            f"two live generations at once: {timeline}"
        served = trip.engine.control()["requests"]
        assert served == 1, f"the engine served {served} generations of one job"
        assert trip.db("select kind, active from infrx.capacity_reservations where "
                       "request_id = %s order by kind", request_id) == [
            ("inference", False), ("journal_bytes", False), ("preparation", False)]
