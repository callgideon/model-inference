"""D3: fenced leases, recovery and cancellation (0016) as SQL-level checks, on D2's
"admission" scenario (R32/R40: each is a migration mutant's named check).

Each check drives the boundaries with the arguments `PgJobStore` builds and rolls back, so
checks never see each other's rows. The clock is the frozen test clock (`advance`).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import threading
import time

import psycopg
from psycopg.types.json import Jsonb

from infrx.contracts.conformance import builders as b
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import Lease, LeaseKind
from infrx.state.jobstore import PgJobStore, domain_error

from . import checks_admission as ca
from . import checks_credit as cc
from .checks_dispatch import advance, call, claim, kinds, outcome, prepare

LIMITS = PgJobStore(None)._lease_limits()
TTL, PREP_TTL = DEFAULTS.lease_ttl_s, DEFAULTS.preparation_lease_ttl_s


def d3(conn, function: str, **args):
    """(code, answer) of one D3 boundary call with the store's limits."""
    return outcome(conn, function, {"limits": LIMITS, **args})


def lease_of(answer) -> Lease:
    return Lease.model_validate(answer["lease"])


def dump(lease: Lease, **changes) -> dict:
    return lease.model_copy(update=changes).model_dump(mode="json")


def queued(conn, world, **kw):
    """Admitted, prepared: (request, queued_at)."""
    request = b.request(world, **kw)
    ca.admit(conn, request, b.idem(request, request.request_id))
    _, answer = claim(conn, request.request_id)
    assert prepare(conn, answer["lease"])[0] is None, "the fixture job did not queue"
    return request


def running(conn, world, worker: str = "w1", **kw):
    request = queued(conn, world, **kw)
    code, answer = d3(conn, "claim", job_id=request.request_id, worker_id=worker)
    assert code is None, f"the fixture claim was refused: {code}"
    return request, lease_of(answer)


def row(conn, request_id: str) -> dict:
    cur = conn.execute("select * from infrx.jobs where request_id = %s", (request_id,))
    names = [c.name for c in cur.description]
    return dict(zip(names, cur.fetchone()))


def live_attempts(conn, request_id: str) -> list[tuple]:
    return conn.execute("select kind, generation, worker_id from infrx.attempts "
                        "where job_id = %s and released_at is null order by kind",
                        (request_id,)).fetchall()


def reserved(conn, org: str = b.ORG_A) -> Decimal:
    return conn.execute("select reserved_total from infrx.wallets where org_id = %s",
                        (org,)).fetchone()[0]


def active_reservations(conn, request_id: str) -> list[str]:
    return [k for k, in conn.execute("select kind from infrx.capacity_reservations where "
                                     "request_id = %s and active order by kind",
                                     (request_id,))]


def publish(conn, lease: Lease) -> None:
    """D4's first committed chunk, as its protocol: the fence, then the marker."""
    assert conn.execute("select infrx.fence_lease(%s, array['inference'], 86400)",
                        (Jsonb(lease.model_dump(mode="json")),)).fetchone()[0] is None, \
        "the fixture publish was fenced out"
    conn.execute("update infrx.jobs set published = true where request_id = %s",
                 (lease.job_id,))


def reaped(produced: list, key: str = "outcome") -> dict:
    """The one item a sweep produced, as an assertion (never an unpacking error)."""
    assert len(produced) == 1 and key in produced[0], f"expected one {key}: {produced}"
    return produced[0][key]


def assert_released(conn, request_id: str, what: str) -> None:
    """A terminalization with nothing to reconcile: hold, reservations, attempts."""
    hold = conn.execute("select state from infrx.credit_holds where request_id = %s",
                        (request_id,)).fetchone()[0]
    assert hold == "released", f"{what}: the hold is {hold}"
    assert active_reservations(conn, request_id) == [], f"{what}: reservations still active"
    assert live_attempts(conn, request_id) == [], f"{what}: an attempt is still live"
    assert sorted(k for k in kinds(conn, request_id) if k.endswith("projection")) == \
        ["trace_projection", "usage_projection"], f"{what}: projections {kinds(conn, request_id)}"


# --------------------------------------------------------------------- item 1
def check_claim_generation(conn) -> str:
    """DUR-FENCE claim: only a queued job is claimable; the lease is minted on the database
    clock with the R20 instants (generation = min(now + budget, deadline_at), first token
    = min(now + budget, generation)); the queued interval is charged to the queue budget
    (R38); a second claimer, a job past its queue or absolute deadline, a terminal job and
    an unknown job are refused, typed; a requeue's claim is the NEXT generation."""
    world = ca.World(conn)

    def body():
        request = b.request(world)
        ca.admit(conn, request, b.idem(request, request.request_id))
        assert d3(conn, "claim", job_id=request.request_id, worker_id="w1")[0] == \
            "not_claimable", "a preparing job was claimable"
        _, prep = claim(conn, request.request_id)
        prepare(conn, prep["lease"])
        advance(conn, 7)
        now = world.clock.now()
        code, answer = d3(conn, "claim", job_id=request.request_id, worker_id="w1")
        assert code is None, code
        lease = lease_of(answer)
        deadline = row(conn, request.request_id)["deadline_at"]
        gen = min(now + timedelta(seconds=DEFAULTS.generation_timeout_s), deadline)
        assert (lease.kind.value, lease.generation, lease.worker_id) == \
            ("inference", 1, "w1"), lease
        assert lease.acquired_at == now and lease.expires_at == now + timedelta(seconds=TTL), \
            f"not the database clock: {lease}"
        assert lease.generation_deadline_at == gen, (lease.generation_deadline_at, gen)
        assert lease.first_token_deadline_at == min(
            now + timedelta(seconds=DEFAULTS.ttft_timeout_s), gen), lease
        job = row(conn, request.request_id)
        assert (job["state"], job["queue_wait_used_s"], job["queued_at"]) == \
            ("running", 7.0, None), "the queued interval was not charged to the queue (R38)"
        assert d3(conn, "claim", job_id=request.request_id, worker_id="w2")[0] == \
            "not_claimable", "a running job was claimed twice"
        assert live_attempts(conn, request.request_id) == [("inference", 1, "w1")], \
            "a refused claimer left an attempt"
        # a requeue (lost lease) is claimed as the NEXT generation, by any worker
        advance(conn, TTL + 1)
        call(conn, "recover", {"limits": LIMITS})
        code, answer = d3(conn, "claim", job_id=request.request_id, worker_id="w1")
        assert code is None and lease_of(answer).generation == 2, (code, answer)
        # R20: with less time left than the budgets, both instants are the deadline
        tight = queued(conn, world, deadline_s=30)
        code, answer = d3(conn, "claim", job_id=tight.request_id, worker_id="w1")
        edge = row(conn, tight.request_id)["deadline_at"]
        assert code is None and (lease_of(answer).generation_deadline_at,
                                 lease_of(answer).first_token_deadline_at) == (edge, edge), \
            f"a phase instant outlived the accepted deadline: {code} {answer}"
        # past the persisted queue instant: refused, and nothing changes
        late = queued(conn, world)
        advance(conn, DEFAULTS.queue_wait_interactive_s)
        assert d3(conn, "claim", job_id=late.request_id, worker_id="w1")[0] == \
            "not_claimable", "a job past its queue deadline was claimed"
        assert row(conn, late.request_id)["state"] == "queued", 'failed: row(conn, late.request_id)["state"] == "queued"'
        # past the absolute deadline (a queue instant that would not stop it)
        short = queued(conn, world, deadline_s=5)
        conn.execute("update infrx.jobs set queue_deadline_at = null where request_id = %s",
                     (short.request_id,))
        advance(conn, 6)
        assert d3(conn, "claim", job_id=short.request_id, worker_id="w1")[0] == \
            "not_claimable", "a job past its absolute deadline was claimed"
        assert d3(conn, "claim", job_id=str(world.ids.uuid()), worker_id="w1")[0] == \
            "not_found", "an unknown job was not not_found"
        d3(conn, "cancel", org_id=late.org_id, job_handle=row(conn, late.request_id)["job_handle"])
        assert d3(conn, "claim", job_id=late.request_id, worker_id="w1")[0] == \
            "already_terminal", "a terminal job was not already_terminal"
        return "generation minted under the job lock, R20 instants, R38 charge, typed refusals"
    return ca._in_rollback(conn, body)


def check_fence(conn) -> str:
    """DUR-FENCE: every fenced boundary (heartbeat, load_work, terminalize) refuses a wrong
    kind, a foreign worker at the same generation, a stale generation and an expired lease
    `stale_lease` with no change; renews only the STORED lease (a forged record changes
    nothing, R29); and past a phase deadline - even with the lease still live, and before
    lease expiry is considered (R55) - terminalizes the job in that call, commits it and
    answers `already_terminal` (R39): released, never debited."""
    world = ca.World(conn)

    def body():
        request, lease = running(conn, world)
        forged = {"a preparation token": dump(lease, kind=LeaseKind.preparation,
                                              first_token_deadline_at=None),
                  "a foreign worker": dump(lease, worker_id="w2"),
                  "a stale generation": dump(lease, generation=2)}
        for fn in ("heartbeat", "load_work", "terminalize"):
            extra = {"outcome": {}} if fn == "terminalize" else {}
            for label, token in forged.items():
                code, _ = d3(conn, fn, lease=token, **extra)
                assert code == "stale_lease", f"{fn} with {label}: {code}"
        assert row(conn, request.request_id)["state"] == "running", 'failed: row(conn, request.request_id)["state"] == "running"'
        # the stored lease renews; a forged record's deadlines are ignored
        advance(conn, 40)
        now = world.clock.now()
        code, answer = d3(conn, "heartbeat", lease=dump(
            lease, generation_deadline_at=now + timedelta(days=9),
            acquired_at=now, expires_at=now + timedelta(days=9)))
        assert code is None, code
        renewed = lease_of(answer)
        assert renewed.expires_at == now + timedelta(seconds=TTL), renewed
        assert renewed.generation_deadline_at == lease.generation_deadline_at, \
            "a worker rewrote its generation deadline"
        # the live holder may execute; the settlement after the fence is D5's
        code, work = d3(conn, "load_work", lease=lease.model_dump(mode="json"))
        assert code is None and work["request"]["request_id"] == request.request_id, code
        media = b.request(world)
        ca.admit(conn, media, b.idem(media, media.request_id))
        _, prep = claim(conn, media.request_id)
        refs = [b.media(b.ORG_A).model_dump(mode="json")]
        prepare(conn, prep["lease"], (b.media(b.ORG_A),))
        _, took = d3(conn, "claim", job_id=media.request_id, worker_id="wm")
        code, work = d3(conn, "load_work", lease=took["lease"])
        assert work["prepared_refs"] == refs, f"load_work lost the prepared refs: {work}"
        assert d3(conn, "terminalize", lease=lease.model_dump(mode="json"),
                  outcome={})[0] == "untyped 0A000", "terminalize's settlement is not D5's stub"
        # expired (before any deadline): stale, and nothing changes
        advance(conn, TTL + 1)
        for fn in ("heartbeat", "load_work"):
            assert d3(conn, fn, lease=lease.model_dump(mode="json"))[0] == "stale_lease", fn
        assert row(conn, request.request_id)["state"] == "running", \
            "an expired lease inside the deadline terminalized the job"
        # past the generation instant, lease LIVE: terminalized here (R29)
        for fn in ("heartbeat", "load_work", "terminalize"):
            job, live = running(conn, world, worker=f"w-{fn}")
            conn.execute("update infrx.attempts set expires_at = expires_at + interval '1 day'"
                         " where job_id = %s", (job.request_id,))
            before = reserved(conn)
            advance(conn, DEFAULTS.generation_timeout_s)
            extra = {"outcome": {}} if fn == "terminalize" else {}
            code, _ = d3(conn, fn, lease=live.model_dump(mode="json"), **extra)
            assert code == "already_terminal", f"{fn} past the generation deadline: {code}"
            done = row(conn, job.request_id)
            assert (done["state"], done["outcome_cause"], done["settlement_state"],
                    done["debit"]) == ("failed", "deadline_exceeded",
                                       "released_platform_absorbed", 0), (fn, done)
            assert reserved(conn) == before - done["maximum_hold"], f"{fn}: hold not released"
            assert_released(conn, job.request_id, fn)
            assert d3(conn, fn, lease=live.model_dump(mode="json"), **extra)[0] == \
                "already_terminal", f"{fn} after the terminalization"
        # past the deadline with the lease ALSO expired: the deadline is checked first
        job, dead = running(conn, world, worker="w-both")
        advance(conn, DEFAULTS.generation_timeout_s + 1)
        assert d3(conn, "heartbeat", lease=dead.model_dump(mode="json"))[0] == \
            "already_terminal", "expiry was checked before the phase deadline (R55)"
        assert row(conn, job.request_id)["outcome_cause"] == "deadline_exceeded", 'failed: row(conn, job.request_id)["outcome_cause"] == "deadline_exceeded"'
        return "kind/owner/generation/expiry fenced, stored lease renewed, R29 terminalizes first"
    return ca._in_rollback(conn, body)


def check_preparation_fence(conn) -> str:
    """R46/R52: a preparation lease is fenced by the same function - renewed, never past
    `preparation_deadline_at`; past it, heartbeat and load_work terminalize the job
    `preparation_failed` (released_free) in the same call; load_work hands a preparation
    lease the admitted request, no prepared refs yet, the admitted snapshot and budgets."""
    world = ca.World(conn)

    def body():
        request = b.request(world)
        admitted = ca.admit(conn, request, b.idem(request, request.request_id))
        _, answer = claim(conn, request.request_id)
        prep = lease_of(answer)
        code, work = d3(conn, "load_work", lease=prep.model_dump(mode="json"))
        assert code is None and work["prepared_refs"] == [], (code, work)
        assert d3(conn, "terminalize", lease=prep.model_dump(mode="json"),
                  outcome={})[0] == "stale_lease", "a preparation lease reached the settlement"
        # R29: the work carries the deadline the store keeps, not the caller's
        far = b.request(world, deadline_s=10_000)
        ca.admit(conn, far, b.idem(far, far.request_id))
        _, far_lease = claim(conn, far.request_id)
        _, far_work = d3(conn, "load_work", lease=far_lease["lease"])
        kept = row(conn, far.request_id)["deadline_at"]
        assert datetime.fromisoformat(far_work["request"]["deadline_at"]) == kept, \
            (far_work["request"]["deadline_at"], kept)
        assert work["admission"]["price_snapshot"] == admitted["price_snapshot"], 'failed: work["admission"]["price_snapshot"] == admitted["price_snapshot"]'
        phase = row(conn, request.request_id)["preparation_deadline_at"]
        while world.clock.now() + timedelta(seconds=PREP_TTL * 0.75) < phase:
            advance(conn, PREP_TTL * 0.75)
            code, answer = d3(conn, "heartbeat", lease=prep.model_dump(mode="json"))
            assert code is None, code
            assert lease_of(answer).expires_at == min(
                world.clock.now() + timedelta(seconds=PREP_TTL), phase), answer
        assert lease_of(answer).expires_at == phase, "a renewal bought time past the phase"
        for fn in ("heartbeat", "load_work"):
            job = b.request(world)
            ca.admit(conn, job, b.idem(job, job.request_id))
            _, answer = claim(conn, job.request_id)
            advance(conn, DEFAULTS.preparation_timeout_s)
            code, _ = d3(conn, fn, lease=answer["lease"])
            assert code == "already_terminal", f"{fn} past the preparation deadline: {code}"
            done = row(conn, job.request_id)
            assert (done["outcome_cause"], done["settlement_state"]) == \
                ("preparation_failed", "released_free"), (fn, done)
            assert_released(conn, job.request_id, fn)
        return "preparation renewed within its phase; past it terminalized in the call"
    return ca._in_rollback(conn, body)


# --------------------------------------------------------------------- item 2
def check_cancel(conn) -> str:
    """Cancellation terminalizes through the same transaction that releases: every
    nonterminal state ends `cancelled`/`client_cancelled`, the hold is released (free:
    nothing was published), every reservation and attempt is released, the two projections
    are written once, and the worker's next fenced call is `already_terminal`. After
    publication the hold stays reserved `held_unknown` with the 24 h window. A terminal
    job answers its committed outcome and writes nothing; another tenant's handle is
    `not_found`; a CREDIT job releases its CREDIT hold."""
    world = ca.World(conn)

    def handle(request) -> str:
        return row(conn, request.request_id)["job_handle"]

    def body():
        preparing = b.request(world)
        ca.admit(conn, preparing, b.idem(preparing, preparing.request_id))
        waiting = queued(conn, world)
        busy, lease = running(conn, world)
        for request in (preparing, waiting, busy):
            before = reserved(conn)
            code, out = d3(conn, "cancel", org_id=request.org_id, job_handle=handle(request))
            assert code is None, code
            assert (out["state"], out["cause"], out["settlement_state"], out["debit"]) == \
                ("cancelled", "client_cancelled", "released_free", "0.00000000"), out
            assert reserved(conn) == before - row(conn, request.request_id)["maximum_hold"], \
                "the cancellation did not release the hold"
            assert_released(conn, request.request_id, "cancel")
        assert d3(conn, "heartbeat", lease=lease.model_dump(mode="json"))[0] == \
            "already_terminal", "the cancelled job's worker kept its lease"
        # a terminal job answers the committed outcome and writes nothing
        count = len(kinds(conn, busy.request_id))
        code, again = d3(conn, "cancel", org_id=busy.org_id, job_handle=handle(busy))
        assert code is None and again["cause"] == "client_cancelled", again
        assert len(kinds(conn, busy.request_id)) == count, "a repeated cancel wrote again"
        # tenancy: ORG_B cannot cancel ORG_A's job, and it is untouched
        other = queued(conn, world)
        assert d3(conn, "cancel", org_id=b.ORG_B, job_handle=handle(other))[0] == \
            "not_found", "another tenant cancelled the job"
        assert row(conn, other.request_id)["state"] == "queued", 'failed: row(conn, other.request_id)["state"] == "queued"'
        # after publication: held for reconciliation, not released
        shown, shown_lease = running(conn, world)
        publish(conn, shown_lease)
        before = reserved(conn)
        now = world.clock.now()
        code, out = d3(conn, "cancel", org_id=shown.org_id, job_handle=handle(shown))
        assert (out["settlement_state"], out["debit"]) == ("held_unknown", "0.00000000"), out
        hold = conn.execute("select state, reconcile_after from infrx.credit_holds "
                            "where request_id = %s", (shown.request_id,)).fetchone()
        window = now + timedelta(seconds=DEFAULTS.unknown_usage_reconcile_s)
        assert hold == ("unknown", window) and reserved(conn) == before, \
            f"published output was released instead of reconciled: {hold}"
        assert row(conn, shown.request_id)["usage_certainty"] == "unknown", 'failed: row(conn, shown.request_id)["usage_certainty"] == "unknown"'
        # CREDIT: the CREDIT hold, through its own trigger
        org = cc.personal_org(conn, cc.CONSUMER_1)
        credit = ca.credit_request(world, ca.C1_KEY, org)
        ca.admit(conn, credit, b.idem(credit, credit.request_id), regime="credit")
        code, out = d3(conn, "cancel", org_id=org, job_handle=handle(credit))
        state = conn.execute("select state from infrx.credit_wallet_holds where request_id = %s",
                             (credit.request_id,)).fetchone()[0]
        assert (code, out["settlement_state"], state) == (None, "released_free", "released"), \
            (code, out, state)
        return "every nonterminal state cancels and releases; published reconciles; tenant-safe"
    return ca._in_rollback(conn, body)


# --------------------------------------------------------------------- item 3
def _recover(conn) -> list[dict]:
    return call(conn, "recover", {"limits": LIMITS})


def _decide(conn, request_id: str) -> list:
    """The reaper's per-job decision on its own, from the fresh row - what it does when a
    renewal (or any other commit) lands between its candidate scan and its row lock."""
    return conn.execute("select infrx.recover_job(%s, infrx.now(), %s, %s)",
                        (request_id, DEFAULTS.max_prepublication_retries,
                         DEFAULTS.unknown_usage_reconcile_s)).fetchone()[0]


def check_recover_requeue(conn) -> str:
    """The reaper, inference side: a live lease is left alone (by the sweep AND by the
    per-job decision under the lock); an expired one BEFORE publication is requeued as a
    new attempt (attempts + 1, the attempt released, the job queued with only the unspent
    queue remainder, R38) with exactly one inference_dispatch, returned as an IndexEvent
    carrying that row's own id; after MAX_PREPUBLICATION_RETRIES requeues it is
    `retries_exhausted`; AFTER publication it is `lost_after_publication` and never
    requeued; past the generation instant (lease live) it is `deadline_exceeded`; a queued
    job is left alone until its queue instant, then `queue_wait_expired` (released_free)."""
    world = ca.World(conn)

    def body():
        request = queued(conn, world)
        advance(conn, 3)                              # 3 s of the queue budget used
        code, answer = d3(conn, "claim", job_id=request.request_id, worker_id="w1")
        assert code is None, code
        advance(conn, TTL - 1)
        assert _recover(conn) == [] and _decide(conn, request.request_id) == [], \
            "a live lease was reaped"
        advance(conn, 1)
        now = world.clock.now()
        event = reaped(_recover(conn), "index_event")
        job = row(conn, request.request_id)
        assert (job["state"], job["attempts"], job["published"]) == ("queued", 1, False), job
        assert job["queue_wait_used_s"] == 3.0 and job["queue_deadline_at"] == min(
            now + timedelta(seconds=job["budget_queue_wait_s"] - 3.0), job["deadline_at"]), \
            "the requeue did not keep only the unspent queue remainder (R38)"
        assert live_attempts(conn, request.request_id) == [], "the lost attempt is still live"
        dispatches = conn.execute(
            "select event_id from infrx.outbox where aggregate_id = %s "
            "and kind = 'inference_dispatch' order by created_at, event_id",
            (request.request_id,)).fetchall()
        assert len(dispatches) == 2 and str(dispatches[-1][0]) == event["event_id"] and \
            event["attempt"] == 1 and event["kind"] == "inference_dispatch", \
            (dispatches, event)
        assert _decide(conn, request.request_id) == [], "a queued job was reaped early"
        # the retry counter: MAX_PREPUBLICATION_RETRIES requeues, then retries_exhausted
        for attempt in range(2, DEFAULTS.max_prepublication_retries + 1):
            d3(conn, "claim", job_id=request.request_id, worker_id="w1")
            advance(conn, TTL)
            reaped(_recover(conn), "index_event")
            assert row(conn, request.request_id)["attempts"] == attempt, \
                f"requeue {attempt} was not counted"
        d3(conn, "claim", job_id=request.request_id, worker_id="w1")
        advance(conn, TTL)
        out = reaped(_recover(conn))
        assert out["cause"] == "retries_exhausted", out
        assert_released(conn, request.request_id, "retries_exhausted")
        # after publication: never requeued
        shown, lease = running(conn, world)
        publish(conn, lease)
        advance(conn, TTL)
        out = reaped(_recover(conn))
        assert (out["cause"], out["settlement_state"]) == \
            ("lost_after_publication", "held_unknown"), out
        assert kinds(conn, shown.request_id).count("inference_dispatch") == 1, \
            "a published job was redispatched"
        # the generation instant ends a run whose lease is still live
        slow, lease = running(conn, world)
        conn.execute("update infrx.attempts set expires_at = expires_at + interval '1 day' "
                     "where job_id = %s", (slow.request_id,))
        advance(conn, DEFAULTS.generation_timeout_s)
        out = reaped(_recover(conn))
        assert out["cause"] == "deadline_exceeded", out
        # a queued job past its queue instant
        waiting = queued(conn, world)
        advance(conn, DEFAULTS.queue_wait_interactive_s)
        out = reaped(_recover(conn))
        assert (out["job_id"], out["state"], out["cause"], out["settlement_state"]) == \
            (waiting.request_id, "expired", "queue_wait_expired", "released_free"), out
        return "requeue before publication (bounded, R38), fail after; phase instants bind"
    return ca._in_rollback(conn, body)


def check_recover_preparation(conn) -> str:
    """The reaper, preparation side (R46/R52): a live preparation lease is left alone; an
    expired one is released and the job redispatched (`prepare_dispatch`, still preparing,
    no index event); after the first claim plus MAX_PREPUBLICATION_RETRIES it is settled
    `preparation_failed` without a further dispatch; past `preparation_deadline_at` it is
    `preparation_failed`."""
    world = ca.World(conn)

    def body():
        request = b.request(world)
        ca.admit(conn, request, b.idem(request, request.request_id))
        for attempt in range(1, DEFAULTS.max_prepublication_retries + 1):
            code, answer = claim(conn, request.request_id, f"p{attempt}")
            assert code is None and answer["lease"]["generation"] == attempt, (code, answer)
            advance(conn, PREP_TTL - 1)
            assert _recover(conn) == [] and _decide(conn, request.request_id) == [], \
                "a live preparation lease was reaped"
            assert live_attempts(conn, request.request_id), "a live preparation was released"
            advance(conn, 1)
            assert _recover(conn) == [], "a preparation reap produced an index event"
            assert row(conn, request.request_id)["state"] == "preparing", \
                "a reaped preparation left the preparing state"
            assert live_attempts(conn, request.request_id) == [], "the lost lease is live"
            assert kinds(conn, request.request_id).count("prepare_dispatch") == attempt + 1, \
                "the reaped preparation was not redispatched"
        claim(conn, request.request_id, "last")
        advance(conn, PREP_TTL)
        out = reaped(_recover(conn))
        assert out["cause"] == "preparation_failed", out
        assert kinds(conn, request.request_id).count("prepare_dispatch") == \
            DEFAULTS.max_prepublication_retries + 1, "an exhausted preparation was redispatched"
        assert_released(conn, request.request_id, "preparation retries")
        # the wall-clock bound, whatever the count
        late = b.request(world)
        ca.admit(conn, late, b.idem(late, late.request_id))
        advance(conn, DEFAULTS.preparation_timeout_s)
        out = reaped(_recover(conn))
        assert (out["job_id"], out["cause"]) == (late.request_id, "preparation_failed"), out
        return "lost preparation redispatched within the bound, then settled"
    return ca._in_rollback(conn, body)


def check_recover_unknown_release(conn) -> str:
    """02/R21: an unknown-usage hold is released platform-absorbed by the reaper only at
    `reconcile_after` on the database clock (not a second before), once, with its usage
    projection; the ledger never moves; the terminal settlement changes only along
    held_unknown -> released_platform_absorbed (the 0003 guard amendment)."""
    world = ca.World(conn)

    def refused(request_id: str, to: str) -> bool:
        try:
            with conn.transaction():
                conn.execute("update infrx.jobs set settlement_state = %s, reconcile_after = "
                             "case when %s = 'held_unknown' then infrx.now() end "
                             "where request_id = %s", (to, to, request_id))
        except psycopg.errors.CheckViolation:
            return True
        return False

    def body():
        request, lease = running(conn, world)
        publish(conn, lease)
        ledger = conn.execute("select ledger_total from infrx.wallets where org_id = %s",
                              (b.ORG_A,)).fetchone()[0]
        advance(conn, TTL)
        assert reaped(_recover(conn))["settlement_state"] == "held_unknown", 'failed: held_unknown'
        held = reserved(conn)
        assert refused(request.request_id, "settled"), "a held_unknown settlement became settled"
        advance(conn, DEFAULTS.unknown_usage_reconcile_s - 1)
        assert _recover(conn) == [] and reserved(conn) == held, "released before the window"
        advance(conn, 1)
        out = reaped(_recover(conn))
        assert (out["settlement_state"], out["reconcile_after"], out["debit"]) == \
            ("released_platform_absorbed", None, "0.00000000"), out
        maximum = row(conn, request.request_id)["maximum_hold"]
        assert reserved(conn) == held - maximum, "the unknown hold was not released"
        assert conn.execute("select ledger_total from infrx.wallets where org_id = %s",
                            (b.ORG_A,)).fetchone()[0] == ledger, "unknown usage was debited"
        assert kinds(conn, request.request_id).count("usage_projection") == 2, \
            "the release did not write its usage projection"
        assert conn.execute("select infrx.release_aged_unknown(%s, infrx.now())",
                            (request.request_id,)).fetchone()[0] == [], \
            "a released hold was released again"
        for to in ("settled", "held_unknown", "released_free"):
            assert refused(request.request_id, to), f"a terminal settlement moved to {to}"
        return "unknown usage released platform-absorbed at the window, never debited"
    return ca._in_rollback(conn, body)


def check_recover_isolation(conn) -> str:
    """One job the sweep cannot reap is reported (`unsettleable`, with its SQLSTATE) and
    rolled back alone; every other overdue job is still reaped in the same pass."""
    world = ca.World(conn)

    def body():
        stuck = queued(conn, world)
        others = [queued(conn, world) for _ in range(2)]
        conn.execute(f"""create function pg_temp.stuck() returns trigger language plpgsql as $$
            begin
              if new.request_id = '{stuck.request_id}' and new.settled_at is not null then
                raise exception 'stuck' using errcode = 'P0003';
              end if;
              return new;
            end $$""")
        conn.execute("create trigger zz_stuck before update on infrx.jobs for each row "
                     "execute function pg_temp.stuck()")
        advance(conn, DEFAULTS.queue_wait_interactive_s)
        try:
            with conn.transaction():
                produced = _recover(conn)
        except psycopg.Error as failed:
            raise AssertionError(f"the sweep stopped at the stuck job: {failed}") from None
        reaped_ids = {item["outcome"]["job_id"] for item in produced if "outcome" in item}
        stuck_items = [item["unsettleable"] for item in produced if "unsettleable" in item]
        assert reaped_ids == {o.request_id for o in others}, f"the sweep stopped: {produced}"
        assert [(s["job_id"], s["code"]) for s in stuck_items] == \
            [(stuck.request_id, "P0003")], stuck_items
        assert row(conn, stuck.request_id)["state"] == "queued" and \
            active_reservations(conn, stuck.request_id), "the stuck job half-terminalized"
        return "one unreapable job is reported and rolled back alone"
    return ca._in_rollback(conn, body)


# --------------------------------------------------------------------- item 6 (SQL half)
def rpc(conn, function: str, args: dict):
    """(error code or None, answer) of one boundary call on its own connection; a refusal
    after a committed terminalization is its code."""
    try:
        answer = conn.execute(f"select infrx.{function}(%s)", (Jsonb(args),)).fetchone()[0]
    except psycopg.Error as failed:
        mapped = domain_error(failed)
        return getattr(mapped, "code", f"untyped {failed.sqlstate}"), None
    if isinstance(answer, dict) and answer.get("refusal"):
        return answer["refusal"]["code"], answer
    return None, answer


def waiting_on_a_lock(observer, pid: int, within_s: float = 10.0) -> None:
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline:
        state = observer.execute("select wait_event_type from pg_stat_activity where pid = %s",
                                 (pid,)).fetchone()
        if state and state[0] == "Lock":
            return
        time.sleep(0.02)
    raise AssertionError(f"backend {pid} never waited on the first transaction's lock")


def lockstep(observer, first, second):
    """`first = (conn, call)` runs in an OPEN transaction; `second` starts in a thread and
    must be seen WAITING on a lock; then the first commits (an aborted one rolls back).
    Returns both answers."""
    a, other = first[0], second[0]
    a.execute("begin")
    one = first[1](a)
    out: dict = {}
    thread = threading.Thread(target=lambda: out.setdefault("two", second[1](other)))
    thread.start()
    try:
        waiting_on_a_lock(observer, other.info.backend_pid)
    finally:
        a.execute("commit")
        thread.join(10)
    assert not thread.is_alive(), "the second transaction never finished"
    return one, out["two"]


def check_lease_races(connect, database: str) -> str:
    """DUR-FENCE under real transactions (the migration mutants' concurrency check; the full
    set is tests/d/test_lease_races.py): two claimers serialize on the job row (the second
    waits, then is `not_claimable`); a heartbeat after an uncommitted requeue waits on the
    job row and is then `stale_lease`; a cancel behind complete's R29 terminalization waits
    and answers the committed outcome."""
    owner = connect(database)

    def service():
        conn = connect(database)
        conn.execute("set role service_role")
        return conn

    world = ca.World(owner)
    job = queued(owner, world)
    claim_args = {"job_id": job.request_id, "worker_id": "w1", "limits": LIMITS}
    first, second = lockstep(owner, (service(), lambda c: rpc(c, "claim", claim_args)),
                             (service(), lambda c: rpc(c, "claim",
                                                       dict(claim_args, worker_id="w2"))))
    assert first[0] is None and second[0] == "not_claimable", (first, second)
    lease = lease_of(first[1])
    advance(owner, TTL)
    first, second = lockstep(
        owner, (service(), lambda c: rpc(c, "recover", {"limits": LIMITS})),
        (service(), lambda c: rpc(c, "heartbeat", {"lease": lease.model_dump(mode="json"),
                                                   "limits": LIMITS})))
    assert second[0] == "stale_lease", (first, second)
    _, again = rpc(service(), "claim", dict(claim_args, worker_id="w3"))
    advance(owner, DEFAULTS.generation_timeout_s)
    first, second = lockstep(
        owner, (service(), lambda c: rpc(c, "terminalize", {
            "lease": again["lease"], "outcome": {}, "limits": LIMITS})),
        (service(), lambda c: rpc(c, "cancel", {
            "org_id": b.ORG_A, "job_handle": row(owner, job.request_id)["job_handle"],
            "limits": LIMITS})))
    assert first[0] == "already_terminal" and second[0] is None and \
        second[1]["cause"] == "deadline_exceeded", (first, second)
    return "claim, heartbeat and cancel serialize on the job row"


def check_lease_privileges(conn) -> str:
    """The new surface: `load_work`/`recover` for the service role only; the fence, the
    terminalization, the reaper's per-job bodies and the hold quarantines for nobody (only
    SECURITY DEFINER bodies call them)."""
    callable_ = ("infrx.load_work(jsonb)", "infrx.recover(jsonb)")
    internal = ("infrx.fence_lease(jsonb,text[],double precision)",
                "infrx.terminalize_no_usage(uuid,text,text,double precision)",
                "infrx.recover_job(uuid,timestamp with time zone,integer,double precision)",
                "infrx.release_aged_unknown(uuid,timestamp with time zone)",
                "infrx.quarantine_hold_legacy_usd(uuid,timestamp with time zone)",
                "infrx.quarantine_hold_credit(uuid,timestamp with time zone)",
                "infrx.lease_limit(jsonb,text)")

    def may(role: str, fn: str) -> bool:
        return conn.execute("select has_function_privilege(%s, %s, 'execute')",
                            (role, fn)).fetchone()[0]
    for fn in callable_:
        assert may("service_role", fn), f"service_role cannot execute {fn}"
        for role in ("anon", "authenticated"):
            assert not may(role, fn), f"{role} may execute {fn}"
    for fn in internal:
        for role in ("service_role", "anon", "authenticated"):
            assert not may(role, fn), f"{role} may execute the internal {fn}"
    return f"{len(callable_)} service operations, {len(internal)} internal functions"
