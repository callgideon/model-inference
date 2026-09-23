#!/usr/bin/env python3
"""D3 item 6: the lease races under REAL PostgreSQL transactions (both images).

Two kinds of proof, per race:

* LOCK-STEP, both orders, deterministic: the first transaction runs its boundary call and
  stays OPEN; the second is started in a thread and must be seen waiting on a lock (in
  `pg_stat_activity`) - or, for the reaper, must return at once, because it takes job rows
  SKIP LOCKED - and only then does the first commit. The answer of each order is asserted.
* STRESS: N callers released together by a barrier, several rounds, on fresh jobs.

Every boundary is called as `service_role` with the adapter's arguments. The "publication"
side is D4's append protocol written out (the fence, then the marker, in one transaction)
until D4 exists - so the append races prove the fence and the marker, not the journal.

    uv run --frozen pytest -q tests/d/test_lease_races.py
"""
from __future__ import annotations

import threading
import time

import psycopg
import pytest
from psycopg.types.json import Jsonb

from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes.support import DEFAULT_START
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import Lease
from infrx.state.jobstore import domain_error

from . import checks_admission as ca
from . import checks_leases as cl
from . import pgharness, pgstore
from .checks_leases import lockstep, rpc

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
TTL, GEN = DEFAULTS.lease_ttl_s, DEFAULTS.generation_timeout_s
ROUNDS, CALLERS = 5, 8


class Rig:
    """A fresh migrated, seeded, FROZEN database (a template clone), funded."""

    def __init__(self) -> None:
        self.db = pgstore.fresh_database()
        self.owner = pgharness.connect(self.db)
        self.owner.execute("select infrx_test.freeze(%s)", (DEFAULT_START,))
        self.owner.execute("insert into public.credit_ledger (org_id, delta_usd, kind, "
                           "reason) values (%s, 100, 'grant', 'races')", (b.ORG_A,))
        self.world = ca.World(self.owner)

    def service(self):
        conn = pgharness.connect(self.db)
        conn.execute("set role service_role")
        return conn

    def advance(self, seconds: float) -> None:
        self.owner.execute("select infrx_test.advance(%s)", (float(seconds),))

    def queued(self) -> str:
        return cl.queued(self.owner, self.world).request_id

    def preparing(self) -> str:
        request = cl.gateway_request(self.world)
        ca.admit(self.owner, request, b.idem(request, request.request_id))
        return request.request_id

    def running(self, worker: str = "w1") -> Lease:
        job = self.queued()
        code, answer = rpc(self.service(), "claim", {"job_id": job, "worker_id": worker,
                                                     "limits": cl.LIMITS})
        assert code is None, code
        return Lease.model_validate(answer["lease"])

    def job(self, job_id: str) -> dict:
        return cl.row(self.owner, job_id)

    def reserved(self):
        return cl.reserved(self.owner)

    def projections(self, job_id: str) -> int:
        return cl.kinds(self.owner, job_id).count("usage_projection")

    def handle(self, job_id: str) -> str:
        return self.job(job_id)["job_handle"]


def lease_args(lease: Lease, **extra) -> dict:
    return {"lease": lease.model_dump(mode="json"), "limits": cl.LIMITS, **extra}


def publish(conn, lease: Lease):
    """D4's append protocol, written out: the fence, then the marker, one transaction
    (the caller's). Runs as the owner: `fence_lease` is internal, as D4's body will be."""
    try:
        refusal = conn.execute("select infrx.fence_lease(%s, array['inference'], %s)",
                               (Jsonb(lease.model_dump(mode="json")),
                                DEFAULTS.unknown_usage_reconcile_s)).fetchone()[0]
    except psycopg.Error as failed:
        return domain_error(failed).code, None
    if refusal:
        return refusal["code"], None
    conn.execute("update infrx.jobs set published = true where request_id = %s",
                 (lease.job_id,))
    return None, True


def together(calls) -> list:
    """Release every call at once (a barrier), each on its own connection; answers in order."""
    barrier = threading.Barrier(len(calls))
    out: list = [None] * len(calls)

    def run(i, conn, call):
        barrier.wait()
        out[i] = call(conn)
    threads = [threading.Thread(target=run, args=(i, conn, call))
               for i, (conn, call) in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert not any(thread.is_alive() for thread in threads), "a racer never finished"
    return out


def live(rig: Rig, job_id: str) -> list[tuple]:
    return cl.live_attempts(rig.owner, job_id)


# --------------------------------------------------------------------- two claimers
def test_race__two_claimers_mint_exactly_one_generation() -> None:
    """DUR-FENCE: claims of one queued job serialize on its row; the loser is
    `not_claimable` and leaves no attempt; one generation exists."""
    rig = Rig()
    job = rig.queued()
    claim = {"job_id": job, "worker_id": "w1", "limits": cl.LIMITS}
    first, second = lockstep(rig.owner, (rig.service(), lambda c: rpc(c, "claim", claim)),
                             (rig.service(), lambda c: rpc(c, "claim",
                                                           dict(claim, worker_id="w2"))))
    assert first[0] is None and second[0] == "not_claimable", (first, second)
    assert live(rig, job) == [("inference", 1, "w1")], live(rig, job)
    for _ in range(ROUNDS):
        job = rig.queued()
        answers = together([(rig.service(), lambda c, w=w: rpc(c, "claim", {
            "job_id": job, "worker_id": f"w{w}", "limits": cl.LIMITS}))
            for w in range(CALLERS)])
        leases = [a for code, a in answers if code is None]
        assert len(leases) == 1 and sorted(code for code, _ in answers if code) == \
            ["not_claimable"] * (CALLERS - 1), answers
        assert [(k, g) for k, g, _ in live(rig, job)] == [("inference", 1)], live(rig, job)
        rpc(rig.service(), "cancel", {"org_id": b.ORG_A, "job_handle": rig.handle(job),
                                      "limits": cl.LIMITS})


def test_race__a_rebuild_during_a_claim_never_sees_half_a_claim() -> None:
    """DUR-OUTBOX x DUR-FENCE: `claim` leases AND leaves `queued` in one transaction. A
    rebuild snapshot taken while the claim is uncommitted sees the whole pre-claim job
    (queued, no live attempt) and offers it; the claimer it feeds waits on the job row and
    is `not_claimable` once the claim commits; a snapshot after the commit never offers it
    again. No reader ever sees a lease on a queued job or a running job without one.

    D2's `reopen_dispatch` (the rebuild fence) takes no job-row lock: it only un-acknowledges
    outbox rows and never mutates a job. Run during the uncommitted claim it does not wait
    and reopens the job's delivered dispatch row; once the claim commits the relay finds the
    job running and supersedes the row, so the claim stands alone - whoever re-reads the
    job under its lock (claim, claim_preparation, dispatch_pending) decides."""
    rig = Rig()
    job = rig.queued()
    observer = rig.service()
    since = observer.execute("select infrx.now()").fetchone()[0]
    with rig.service() as relay:                     # the dispatch was delivered: acknowledged
        cl.pump(relay)

    def seen():
        state = rig.job(job)["state"], [(k, g) for k, g, _ in live(rig, job)]
        offered = job in {e["job_id"] for e in observer.execute(
            "select infrx.dispatch_snapshot()").fetchone()[0]}
        return state, offered

    claimer, rival = rig.service(), rig.service()
    claimer.execute("begin")
    code, _ = rpc(claimer, "claim", {"job_id": job, "worker_id": "w1", "limits": cl.LIMITS})
    assert code is None, code
    assert seen() == (("queued", []), True), "a reader saw half a claim"
    assert observer.execute("select infrx.reopen_dispatch(%s)", (Jsonb({
        "since": since.isoformat()}),)).fetchone()[0] == 1, "the fence did not reopen the row"
    out: dict = {}
    thread = threading.Thread(target=lambda: out.setdefault("rival", rpc(rival, "claim", {
        "job_id": job, "worker_id": "w2", "limits": cl.LIMITS})))
    thread.start()
    try:
        cl.waiting_on_a_lock(rig.owner, rival.info.backend_pid)
    finally:
        claimer.execute("commit")
        thread.join(10)
    assert out["rival"][0] == "not_claimable", out
    assert seen() == (("running", [("inference", 1)]), False), \
        "a committed claim is still offered, or not whole"
    with rig.service() as relay:
        assert job not in {e["job_id"] for e in cl.pump(relay)}, \
            "the row reopened during the claim dispatched a running job"
    assert live(rig, job) == [("inference", 1, "w1")], live(rig, job)


def test_race__a_rebuild_during_a_preparation_claim_redelivers_a_row_the_claim_refuses() -> None:
    """The `claim_preparation` side of the same interleaving (confirmation FC-4): the fence,
    run during the uncommitted preparation claim, reopens the delivered `prepare_dispatch`
    row; after the commit the job is still `preparing`, so the relay REDELIVERS the row (not
    superseded, as for a running job) - and the second claimer it feeds is `not_claimable`.
    A duplicate candidate, never a second lease (Limit 9)."""
    rig = Rig()
    job = rig.preparing()
    observer = rig.service()
    since = observer.execute("select infrx.now()").fetchone()[0]
    with rig.service() as relay:                     # the dispatch was delivered: acknowledged
        cl.pump(relay)
    claimer = rig.service()
    claimer.execute("begin")
    code, _ = rpc(claimer, "claim_preparation", {"job_id": job, "worker_id": "prep-a",
                                                 "limits": cl.LIMITS})
    assert code is None, code
    assert observer.execute("select infrx.reopen_dispatch(%s)", (Jsonb({
        "since": since.isoformat()}),)).fetchone()[0] == 1, "the fence did not reopen the row"
    claimer.execute("commit")
    with rig.service() as relay:
        again = [e["kind"] for e in cl.pump(relay) if e["job_id"] == job]
    assert again == ["prepare_dispatch"], f"the reopened row was not redelivered: {again}"
    code, _ = rpc(rig.service(), "claim_preparation", {"job_id": job, "worker_id": "prep-b",
                                                       "limits": cl.LIMITS})
    assert code == "not_claimable", f"the redelivered candidate leased again: {code}"
    assert live(rig, job) == [("preparation", 1, "prep-a")], live(rig, job)


# --------------------------------------------------------------------- recover vs heartbeat
def test_race__recover_and_heartbeat_never_both_win() -> None:
    """DUR-FENCE: a heartbeat in flight is never requeued under it (the reaper skips a
    locked job and decides from the fresh row next time), and a heartbeat that lands after
    the requeue is `stale_lease` - never a renewed lease on a queued job."""
    rig = Rig()
    # heartbeat first: renewed inside the old expiry, committed after it
    lease = rig.running()
    rig.advance(TTL - 20)
    a, reaper = rig.service(), rig.service()
    a.execute("begin")
    code, renewed = rpc(a, "heartbeat", lease_args(lease))
    assert code is None, code
    rig.advance(21)                                   # past the ORIGINAL expiry
    started = time.monotonic()
    assert rpc(reaper, "recover", {"limits": cl.LIMITS}) == (None, []), \
        "the reaper acted on a job whose heartbeat was in flight"
    assert time.monotonic() - started < 5, "the reaper waited on a locked job"
    a.execute("commit")
    assert rpc(reaper, "recover", {"limits": cl.LIMITS}) == (None, []), \
        "the reaper requeued a renewed lease"
    job = rig.job(lease.job_id)
    assert job["state"] == "running" and live(rig, lease.job_id) == [("inference", 1, "w1")]
    assert Lease.model_validate(renewed["lease"]).expires_at > lease.expires_at
    # recover first: the requeue commits, then the heartbeat is stale
    lost = rig.running("w2")
    rig.advance(TTL)
    first, second = lockstep(
        rig.owner, (rig.service(), lambda c: rpc(c, "recover", {"limits": cl.LIMITS})),
        (rig.service(), lambda c: rpc(c, "heartbeat", lease_args(lost))))
    assert first[0] is None and [i for i in first[1] if "index_event" in i], first
    assert second[0] == "stale_lease", second
    job = rig.job(lost.job_id)
    assert (job["state"], job["attempts"], live(rig, lost.job_id)) == ("queued", 1, []), job


# --------------------------------------------------------------------- cancel vs complete
def test_race__cancel_and_complete_have_one_terminal_outcome() -> None:
    """DUR-SETTLE/DUR-FENCE: cancel and complete's fenced terminalization (R29, past the
    generation deadline) serialize on the job row. Whichever commits first is the outcome;
    the other answers it (cancel) or is refused `already_terminal` (complete). The hold is
    released once and one usage projection exists. A complete whose fence holds (the D5
    settlement raises) leaves nothing, and a cancel after it wins."""
    rig = Rig()
    baseline = rig.reserved()

    def settle(lease):
        return lambda c: rpc(c, "terminalize", lease_args(lease, outcome={}))

    def cancel(job):
        return lambda c: rpc(c, "cancel", {"org_id": b.ORG_A, "job_handle": rig.handle(job),
                                           "limits": cl.LIMITS})
    # complete first
    lease = rig.running()
    rig.advance(GEN)
    first, second = lockstep(rig.owner, (rig.service(), settle(lease)),
                             (rig.service(), cancel(lease.job_id)))
    assert first[0] == "already_terminal" and second[0] is None, (first, second)
    assert second[1]["cause"] == "deadline_exceeded", "the waiting cancel invented an outcome"
    # cancel first
    other = rig.running()
    rig.advance(GEN)
    first, second = lockstep(rig.owner, (rig.service(), cancel(other.job_id)),
                             (rig.service(), settle(other)))
    assert first[1]["cause"] == "client_cancelled" and second[0] == "already_terminal", \
        (first, second)
    # a complete whose fence holds: the settlement (D5) raises, and the error aborts its
    # transaction at once (locks included) - nothing of it remains, the cancel then wins
    held = rig.running()
    assert settle(held)(rig.service())[0] == "untyped 0A000"
    assert rig.job(held.job_id)["state"] == "running" and rig.projections(held.job_id) == 0
    assert cancel(held.job_id)(rig.service())[1]["cause"] == "client_cancelled"
    for job in (lease.job_id, other.job_id, held.job_id):
        assert rig.projections(job) == 1, f"{job}: two terminal projections"
    assert rig.reserved() == baseline, "a hold was released twice or not at all"
    # stress: cancels and past-deadline heartbeats released together
    for _ in range(ROUNDS):
        lease = rig.running()
        rig.advance(GEN)
        half = CALLERS // 2
        answers = together([(rig.service(), cancel(lease.job_id)) for _ in range(half)] +
                           [(rig.service(), lambda c, l=lease: rpc(c, "heartbeat",
                                                                   lease_args(l)))
                            for _ in range(half)])
        causes = {a["cause"] for code, a in answers[:half] if code is None}
        assert len(causes) == 1 and all(code is None for code, _ in answers[:half]), answers
        assert all(code == "already_terminal" for code, _ in answers[half:]), answers
        assert rig.job(lease.job_id)["outcome_cause"] in causes
        assert rig.projections(lease.job_id) == 1 and rig.reserved() == baseline


# --------------------------------------------------------------------- append after publication
def test_race__publication_is_honoured_and_a_late_append_is_fenced() -> None:
    """DUR-OUTPUT: a publication committed while the reaper runs is never regenerated (the
    reaper skipped the locked job and then fails it `lost_after_publication`); an append
    that lands after a requeue is `stale_lease` and publishes nothing; publication vs
    cancel decides held_unknown vs released_free by commit order; an append after
    `lost_after_publication` is `already_terminal`."""
    rig = Rig()
    # publication first, the reaper in the middle, then the lease is lost
    lease = rig.running()
    writer = pgharness.connect(rig.db)
    writer.execute("begin")
    assert publish(writer, lease) == (None, True)
    rig.advance(TTL)
    assert rpc(rig.service(), "recover", {"limits": cl.LIMITS}) == (None, []), \
        "the reaper acted under an uncommitted publication"
    writer.execute("commit")
    code, produced = rpc(rig.service(), "recover", {"limits": cl.LIMITS})
    assert [i["outcome"]["cause"] for i in produced] == ["lost_after_publication"], produced
    assert cl.kinds(rig.owner, lease.job_id).count("inference_dispatch") == 1, \
        "a published job was redispatched"
    assert publish(pgharness.connect(rig.db), lease)[0] == "already_terminal", \
        "an append after lost_after_publication was accepted"
    # the requeue first: the late append is stale and publishes nothing
    lost = rig.running("w2")
    rig.advance(TTL)
    first, second = lockstep(
        rig.owner, (rig.service(), lambda c: rpc(c, "recover", {"limits": cl.LIMITS})),
        (pgharness.connect(rig.db), lambda c: publish(c, lost)))
    assert second[0] == "stale_lease", second
    job = rig.job(lost.job_id)
    assert (job["state"], job["published"]) == ("queued", False), job
    # publication vs cancel, both orders
    shown = rig.running("w3")
    first, second = lockstep(
        rig.owner, (pgharness.connect(rig.db), lambda c: publish(c, shown)),
        (rig.service(), lambda c: rpc(c, "cancel", {
            "org_id": b.ORG_A, "job_handle": rig.handle(shown.job_id), "limits": cl.LIMITS})))
    assert second[1]["settlement_state"] == "held_unknown", "a published cancel was released"
    unseen = rig.running("w4")
    first, second = lockstep(
        rig.owner, (rig.service(), lambda c: rpc(c, "cancel", {
            "org_id": b.ORG_A, "job_handle": rig.handle(unseen.job_id), "limits": cl.LIMITS})),
        (pgharness.connect(rig.db), lambda c: publish(c, unseen)))
    assert first[1]["settlement_state"] == "released_free" and \
        second[0] == "already_terminal", (first, second)
    assert rig.job(unseen.job_id)["published"] is False, "a cancelled job was published"
