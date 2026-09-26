"""D2: preparation and the dispatch outbox (0012) as SQL-level checks, on the same
"admission" scenario as `checks_admission` (R32/R40: each is a mutant's named check).

Each check drives the boundaries with the arguments the adapter builds and rolls back.
"""
from __future__ import annotations

from datetime import timedelta

import psycopg
from psycopg.types.json import Jsonb

from infrx.contracts.conformance import builders as b
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import Lease
from infrx.state.jobstore import domain_error

from . import checks_admission as ca
from . import checks_credit as cc

LIMITS = {"preparation_lease_ttl_s": DEFAULTS.preparation_lease_ttl_s,
          "max_prepublication_retries": DEFAULTS.max_prepublication_retries}


def call(conn, function: str, args) -> object:
    return conn.execute(f"select infrx.{function}(%s)", (Jsonb(args),)).fetchone()[0]


def outcome(conn, function: str, args) -> tuple[str | None, object]:
    """(error code or None, answer) - a committed-then-refused answer counts as its code."""
    try:
        with conn.transaction():
            answer = call(conn, function, args)
    except psycopg.Error as failed:
        mapped = domain_error(failed)
        return (getattr(mapped, "code", f"untyped {failed.sqlstate}"), None)
    if isinstance(answer, dict) and answer.get("refusal"):
        return answer["refusal"]["code"], answer
    return None, answer


def claim(conn, job_id: str, worker: str = "prep-a"):
    return outcome(conn, "claim_preparation", {"job_id": job_id, "worker_id": worker,
                                               "limits": LIMITS})


def prepare(conn, lease: dict, media=()):
    return outcome(conn, "prepare", {"lease": lease,
                                     "media": [m.model_dump(mode="json") for m in media]})


def advance(conn, seconds: float) -> None:
    conn.execute("select infrx_test.advance(%s)", (float(seconds),))


def job(conn, request_id: str) -> tuple:
    return conn.execute("""select state, preparation_attempts, prepared_refs, queued_at,
        queue_deadline_at, outcome_cause, settlement_state from infrx.jobs
        where request_id = %s""", (request_id,)).fetchone()


def kinds(conn, request_id: str) -> list[str]:
    return [k for k, in conn.execute("select kind from infrx.outbox where aggregate_id = %s "
                                     "order by created_at, event_id", (request_id,))]


def _admitted(conn, world, **kw):
    request = b.request(world, **kw)
    ca.admit(conn, request, b.idem(request, request.request_id))
    return request


def check_prepare_transition(conn) -> str:
    """preparing -> queued (02 §3, R46): only the live preparation lease of the right kind,
    generation and worker moves it; the prepared refs are stored once and must be the job's
    own tenant's; the preparation unit and the attempt are released; the queue instant is
    min(now + queue budget, deadline_at) (R20/R38); exactly one inference_dispatch."""
    world = ca.World(conn)

    def body():
        request = _admitted(conn, world)
        code, answer = claim(conn, request.request_id)
        assert code is None, code
        lease = answer["lease"]
        assert Lease.model_validate(lease).kind.value == "preparation", 'failed: Lease.model_validate(lease).kind.value == "preparation"'
        assert lease["generation"] == 1 and lease["worker_id"] == "prep-a", 'failed: lease["generation"] == 1 and lease["worker_id"] == "prep-a"'
        now = world.clock.now()
        assert lease["expires_at"] == (now + timedelta(
            seconds=DEFAULTS.preparation_lease_ttl_s)).isoformat(), lease
        assert claim(conn, request.request_id, "prep-b")[0] == "not_claimable", \
            "a second live preparation lease was granted"
        forged = {"a foreign worker": dict(lease, worker_id="prep-b"),
                  "a stale generation": dict(lease, generation=2),
                  "an inference token": dict(lease, kind="inference",
                                             first_token_deadline_at=lease["expires_at"])}
        for label, fake in forged.items():
            assert prepare(conn, fake)[0] == "stale_lease", label
        assert prepare(conn, lease, (b.media(b.ORG_B),))[0] == "forbidden", \
            "another tenant's prepared media was stored"
        assert job(conn, request.request_id)[0] == "preparing", 'failed: job(conn, request.request_id)[0] == "preparing"'
        refs = (b.media(b.ORG_A),)
        code, admission = prepare(conn, lease, refs)
        assert code is None, code
        state, attempts, stored, queued_at, queue_deadline, _, _ = job(conn, request.request_id)
        assert (state, attempts) == ("queued", 1), (state, attempts)
        assert stored == [r.model_dump(mode="json") for r in refs], stored
        assert queued_at == now and queue_deadline == min(
            now + timedelta(seconds=DEFAULTS.queue_wait_interactive_s), request.deadline_at), 'failed: queued_at == now and queue_deadline == min( now + timedelta(seconds=DEFAULTS.queue_wait_interactive_s), reques'
        assert admission["state"] == "queued", 'failed: admission["state"] == "queued"'
        active = conn.execute("select kind from infrx.capacity_reservations where "
                              "request_id = %s and active order by kind",
                              (request.request_id,)).fetchall()
        assert [k for k, in active] == ["inference", "journal_bytes"], active
        live = conn.execute("select count(*) from infrx.attempts where job_id = %s and "
                            "released_at is null", (request.request_id,)).fetchone()[0]
        assert live == 0, "the finished preparation attempt is still live"
        assert sorted(kinds(conn, request.request_id)) == ["inference_dispatch",
                                                           "prepare_dispatch"], 'failed: sorted(kinds(conn, request.request_id)) == ["inference_dispatch", "prepare_dispatch"]'
        assert prepare(conn, lease)[0] == "stale_lease", "a spent lease queued the job twice"
        assert kinds(conn, request.request_id).count("inference_dispatch") == 1, 'failed: kinds(conn, request.request_id).count("inference_dispatch") == 1'
        # an expired lease is stale (before the phase deadline)
        other = _admitted(conn, world)
        _, answer = claim(conn, other.request_id)
        advance(conn, DEFAULTS.preparation_lease_ttl_s + 1)
        assert prepare(conn, answer["lease"])[0] == "stale_lease", "an expired lease queued"
        assert job(conn, other.request_id)[0] == "preparing", 'failed: job(conn, other.request_id)[0] == "preparing"'
        # and the expired lease is superseded by the next claim, at generation 2
        code, again = claim(conn, other.request_id, "prep-b")
        assert code is None and again["lease"]["generation"] == 2, (code, again)
        return "fenced on kind/generation/worker/expiry, tenant-checked, one dispatch"
    return ca._in_rollback(conn, body)


def check_preparation_deadline_terminalizes(conn) -> str:
    """R29/R39/R55: past `preparation_deadline_at` both `claim_preparation` and a late
    `prepared` terminalize the job (`preparation_failed`, released_free) in the SAME call,
    commit it, and then refuse - the hold, every reservation and the attempt are released,
    the usage/trace projections are written. The retry bound (R46) terminalizes too."""
    world = ca.World(conn)

    def body():
        for route in ("claim", "prepared", "retries"):
            request = _admitted(conn, world)
            before = conn.execute("select reserved_total from infrx.wallets where org_id = %s",
                                  (b.ORG_A,)).fetchone()[0]
            with conn.transaction():          # the clock moves only inside this route
                if route == "retries":
                    for n in range(DEFAULTS.max_prepublication_retries + 1):
                        code, answer = claim(conn, request.request_id, f"prep-{n}")
                        assert code is None, (n, code)
                        advance(conn, DEFAULTS.preparation_lease_ttl_s)
                    code, _ = claim(conn, request.request_id, "prep-last")
                    expected = "not_claimable"
                elif route == "prepared":
                    _, answer = claim(conn, request.request_id)
                    advance(conn, DEFAULTS.preparation_timeout_s)
                    code, _ = prepare(conn, answer["lease"])
                    expected = "already_terminal"
                else:
                    advance(conn, DEFAULTS.preparation_timeout_s)
                    code, _ = claim(conn, request.request_id)
                    expected = "already_terminal"
                assert code == expected, (route, code)
                state = job(conn, request.request_id)
                assert (state[0], state[5], state[6]) == \
                    ("failed", "preparation_failed", "released_free"), (route, state)
                held = conn.execute("select state from infrx.credit_holds where "
                                    "request_id = %s", (request.request_id,)).fetchone()[0]
                assert held == "released", (route, held)
                after = conn.execute("select reserved_total from infrx.wallets where "
                                     "org_id = %s", (b.ORG_A,)).fetchone()[0]
                assert after == before - b.hold_for(request, b.DEFAULT_PRICE), \
                    (route, before, after)
                live = conn.execute("""select (select count(*) from
                    infrx.capacity_reservations where request_id = %s and active),
                    (select count(*) from infrx.attempts where job_id = %s
                     and released_at is null)""",
                                    (request.request_id, request.request_id)).fetchone()
                assert live == (0, 0), (route, live)
                assert sorted(kinds(conn, request.request_id)) == [
                    "prepare_dispatch", "trace_projection", "usage_projection"], route
                raise psycopg.Rollback()
        return "claim, late prepared and the retry bound each terminalize, commit, refuse"
    return ca._in_rollback(conn, body)


def check_credit_job_terminalization_releases_credit(conn) -> str:
    """The CREDIT half of the same terminalization: the CREDIT hold is released through
    its own trigger, the wallet's reservation returns, nothing touches the USD tables."""
    world = ca.World(conn)

    def body():
        org = cc.personal_org(conn, cc.CONSUMER_1)
        request = ca.credit_request(world, ca.C1_KEY, org)
        ca.admit(conn, request, b.idem(request, request.request_id), regime="credit")
        wallet = cc.wallet_of(conn, cc.CONSUMER_1)
        reserved = conn.execute("select reserved_total from infrx.credit_wallets where "
                                "wallet_id = %s", (wallet,)).fetchone()[0]
        assert reserved > 0, 'failed: reserved > 0'
        advance(conn, DEFAULTS.preparation_timeout_s)
        assert claim(conn, request.request_id)[0] == "already_terminal", 'failed: claim(conn, request.request_id)[0] == "already_terminal"'
        after = conn.execute("select (select reserved_total from infrx.credit_wallets where "
                             "wallet_id = %s), (select state from infrx.credit_wallet_holds "
                             "where request_id = %s)", (wallet, request.request_id)).fetchone()
        assert after == (0, "released"), after
        return "a CREDIT job past its preparation deadline releases its CREDIT hold"
    return ca._in_rollback(conn, body)


def check_dispatch_relay(conn) -> str:
    """DUR-OUTBOX in SQL: `dispatch_pending` hands each wanted row out once per
    redelivery window (at-least-once: an unacknowledged row returns after it); a row whose
    job moved on is acknowledged as superseded, never handed out; acknowledgment is
    idempotent; `dispatch_snapshot` is every accepted job that wants a dispatch - a live
    preparation lease or a terminal state excludes it - with its latest event."""
    world = ca.World(conn)
    pending = {"limit": 1000, "worker_id": "relay", "redelivery_s": 30}

    def ids(answer):
        return {event["job_id"] for event in answer}

    def body():
        a = _admitted(conn, world)
        c = _admitted(conn, world)
        first = call(conn, "dispatch_pending", pending)
        assert {a.request_id, c.request_id} <= ids(first), 'failed: {a.request_id, c.request_id} <= ids(first)'
        assert not {a.request_id, c.request_id} & ids(call(conn, "dispatch_pending", pending)), \
            "a claimed, unacknowledged row was handed out again inside its window"
        advance(conn, 30)
        again = call(conn, "dispatch_pending", pending)
        assert {a.request_id, c.request_id} <= ids(again), "a lost acknowledgment lost the job"
        event_a = next(e for e in again if e["job_id"] == a.request_id)
        assert event_a["kind"] == "prepare_dispatch" and event_a["attempt"] == 0, 'failed: event_a["kind"] == "prepare_dispatch" and event_a["attempt"] == 0'
        assert call(conn, "acknowledge_dispatch", {"event_ids": [event_a["event_id"]], "worker_id": "relay"}) == 1, 'failed: call(conn, "acknowledge_dispatch", {"event_ids": [event_a["event_id"]], "worker_id": "relay"}) == 1'
        assert call(conn, "acknowledge_dispatch", {"event_ids": [event_a["event_id"]], "worker_id": "relay"}) == 0, 'failed: call(conn, "acknowledge_dispatch", {"event_ids": [event_a["event_id"]], "worker_id": "relay"}) == 0'
        advance(conn, 30)
        assert a.request_id not in ids(call(conn, "dispatch_pending", pending)), \
            "an acknowledged row was delivered again"
        # c is prepared: its prepare_dispatch is superseded, its inference_dispatch wanted
        _, lease = claim(conn, c.request_id)
        prepare(conn, lease["lease"])
        advance(conn, 30)
        out = call(conn, "dispatch_pending", pending)
        mine = [e for e in out if e["job_id"] == c.request_id]
        assert [e["kind"] for e in mine] == ["inference_dispatch"], mine
        superseded = conn.execute("select acknowledged_at is not null, last_error from "
                                  "infrx.outbox where aggregate_id = %s and kind = "
                                  "'prepare_dispatch'", (c.request_id,)).fetchone()
        assert superseded == (True, "superseded"), superseded
        # the snapshot: a live preparation lease excludes, a terminal job excludes
        d = _admitted(conn, world)
        claim(conn, d.request_id)
        snap = conn.execute("select infrx.dispatch_snapshot()").fetchone()[0]
        by_job = {e["job_id"]: e["kind"] for e in snap}
        assert by_job.get(a.request_id) == "prepare_dispatch", by_job
        assert by_job.get(c.request_id) == "inference_dispatch", by_job
        assert d.request_id not in by_job, "a job under a live preparation lease was indexed"
        advance(conn, DEFAULTS.preparation_lease_ttl_s)
        snap = conn.execute("select infrx.dispatch_snapshot()").fetchone()[0]
        assert d.request_id in {e["job_id"] for e in snap}, "an expired lease hid its job"
        conn.execute("select infrx.terminalize_unstarted(%s, 'preparation_failed')",
                     (a.request_id,))
        snap = conn.execute("select infrx.dispatch_snapshot()").fetchone()[0]
        assert a.request_id not in {e["job_id"] for e in snap}, "a terminal job was indexed"
        # the rebuild fence (OB-1): rows acknowledged at/after `since` whose job still
        # wants them are pending again; older acks, superseded and terminal rows are not
        def deliver(job_):
            ev = next(e for e in call(conn, "dispatch_pending", pending)
                      if e["job_id"] == job_.request_id)
            call(conn, "acknowledge_dispatch", {"event_ids": [ev["event_id"]],
                                                "worker_id": "relay"})

        early = _admitted(conn, world)
        deliver(early)
        advance(conn, 5)
        since = conn.execute("select infrx.now()").fetchone()[0]
        late = _admitted(conn, world)
        deliver(late)
        racing = _admitted(conn, world)       # relay B claims it; its ack arrives after reopen
        call(conn, "dispatch_pending", dict(pending, worker_id="relay-b"))
        moved = _admitted(conn, world)                # queued since: its row superseded
        _, lease = claim(conn, moved.request_id)
        prepare(conn, lease["lease"])
        call(conn, "dispatch_pending", pending)
        call(conn, "reopen_dispatch", {"since": since.isoformat()})
        state = dict(conn.execute(
            "select aggregate_id::text, acknowledged_at is null from infrx.outbox where "
            "kind = 'prepare_dispatch' and aggregate_id in (%s, %s, %s)",
            (early.request_id, late.request_id, moved.request_id)).fetchall())
        assert state == {early.request_id: False, late.request_id: True,
                         moved.request_id: False}, f"reopen fenced the wrong rows: {state}"
        # OB-6b: the reopen clears the claim itself, not only the acknowledgment
        racing_ev, claimed_at, claimed_by = conn.execute(
            "select event_id::text, claimed_at, claimed_by from infrx.outbox where "
            "aggregate_id = %s and kind = 'prepare_dispatch'", (racing.request_id,)).fetchone()
        assert (claimed_at, claimed_by) == (None, None), \
            f"reopen left relay B's claim on the row: {(claimed_at, claimed_by)}"
        # OB-1b: relay B's LATE acknowledgment (its index write was wiped by the rebuild)
        # is refused, so the row stays pending for the rebuilding relay's pump
        late_ack = call(conn, "acknowledge_dispatch", {"event_ids": [racing_ev],
                                                       "worker_id": "relay-b"})
        assert late_ack == 0, "a late acknowledgment landed after the reopen: job stranded"
        assert racing.request_id in {e["job_id"] for e in
                                     call(conn, "dispatch_pending", pending)}, \
            "the reopened row was not handed to the next pump"
        # ...and only the relay now holding the claim may acknowledge it
        assert call(conn, "acknowledge_dispatch", {"event_ids": [racing_ev],
                                                   "worker_id": "relay-b"}) == 0, \
            "a relay acknowledged a row another relay holds"
        assert call(conn, "acknowledge_dispatch", {"event_ids": [racing_ev],
                                                   "worker_id": "relay"}) == 1, \
            "the claim holder could not acknowledge"
        return ("at-least-once with a redelivery window, superseded rows acked, snapshot "
                "exact, rebuild fence reopens only rows acked since")
    return ca._in_rollback(conn, body)


def check_outbox_gc(conn) -> str:
    """Outbox expiry/GC (0013): a terminal job's unacknowledged dispatch row is expired
    (acknowledged, never deleted outright); an acknowledged row older than the retention
    is deleted unless its job is live or it is a callback delivery; nothing unacknowledged
    and nothing inside the retention is deleted; `limit` bounds a call."""
    world = ca.World(conn)

    def body():
        # start from a collected outbox, so the counts below are this check's rows only
        # (tombstone 0 too: terminal jobs committed by other checks must go now, not at
        # the tombstone_s=0 call below - found by the full-suite order, not by -k)
        clean = '{"retention_s": 0, "tombstone_s": 0, "limit": 10000}'
        conn.execute("select infrx.gc_outbox(%s)", (clean,))
        advance(conn, 1)
        conn.execute("select infrx.gc_outbox(%s)", (clean,))
        live = _admitted(conn, world)
        waiting = _admitted(conn, world)       # live, its prepare_dispatch NOT acknowledged
        dead = _admitted(conn, world)
        conn.execute("select infrx.terminalize_unstarted(%s, 'preparation_failed')",
                     (dead.request_id,))
        # an acknowledged row of the live job, and a callback row nobody may delete
        conn.execute("update infrx.outbox set acknowledged_at = infrx.now() where "
                     "aggregate_id = %s", (live.request_id,))
        conn.execute("insert into infrx.outbox (event_id, aggregate_id, org_id, kind, "
                     "available_at, acknowledged_at) values (gen_random_uuid(), %s, %s, "
                     "'callback_delivery', infrx.now(), infrx.now())",
                     (dead.request_id, b.ORG_A))

        def rows(request_id):
            return sorted(conn.execute(
                "select kind, acknowledged_at is not null, coalesce(last_error, '') from "
                "infrx.outbox where aggregate_id = %s", (request_id,)).fetchall())

        gc = {"retention_s": 3600, "tombstone_s": 7200, "limit": 1000}
        first = call(conn, "gc_outbox", gc)
        assert first == {"expired": 1, "deleted": 0}, first
        assert rows(dead.request_id) == [
            ("callback_delivery", True, ""), ("prepare_dispatch", True, "expired: job terminal"),
            ("trace_projection", False, ""), ("usage_projection", False, "")], \
            rows(dead.request_id)
        advance(conn, 3600)
        # exactly at the retention instant (no tombstone in the way): kept
        assert call(conn, "gc_outbox", dict(gc, tombstone_s=0)) == \
            {"expired": 0, "deleted": 0}, "a row inside its retention was deleted"
        advance(conn, 1)
        # past the retention, but the job is still inside its tombstone (OB-3)
        assert call(conn, "gc_outbox", gc) == {"expired": 0, "deleted": 0}, \
            "a terminal job's rows were deleted inside its idempotency tombstone"
        advance(conn, 3600)
        second = call(conn, "gc_outbox", gc)
        assert second == {"expired": 0, "deleted": 1}, second
        assert rows(dead.request_id) == [
            ("callback_delivery", True, ""), ("trace_projection", False, ""),
            ("usage_projection", False, "")], rows(dead.request_id)
        assert rows(live.request_id) == [("prepare_dispatch", True, "")], \
            "a live job lost its dispatch row"
        # bounded: three expired rows, one per call
        more = [_admitted(conn, world) for _ in range(3)]
        for m in more:
            conn.execute("select infrx.terminalize_unstarted(%s, 'preparation_failed')",
                         (m.request_id,))
        counts = [call(conn, "gc_outbox", dict(gc, limit=1))["expired"] for _ in range(4)]
        assert counts == [1, 1, 1, 0], counts
        # and the delete step is bounded the same way, once they are past both windows
        advance(conn, 7201)
        deleted = [call(conn, "gc_outbox", dict(gc, limit=1))["deleted"] for _ in range(4)]
        assert deleted == [1, 1, 1, 0], f"the delete step is not bounded: {deleted}"
        # OB-2: a LIVE job's undelivered dispatch row survived every call above
        assert rows(waiting.request_id) == [("prepare_dispatch", False, "")], \
            f"GC expired or deleted a live job's pending dispatch: {rows(waiting.request_id)}"
        return "terminal dispatch rows expired; acknowledged rows past retention deleted, bounded"
    return ca._in_rollback(conn, body)


def check_results_and_prompt_tokens(conn) -> str:
    """W2's two requests, in SQL: `put_result` stores one immutable, tenant-bound result
    per job (same text replays the reference, different text is state_conflict, unknown
    job not_found); `read_result` answers the owner only; `prepare` stores preparation's
    prompt count once and refuses one past the job's `max_input_tokens`."""
    world = ca.World(conn)

    def body():
        request = _admitted(conn, world)
        args = {"job_id": request.request_id, "text": "a clip of a cat"}
        ref = call(conn, "put_result", args)
        assert ref == f"infrx-result:{request.request_id}", ref
        assert call(conn, "put_result", args) == ref, "the same result did not replay"
        assert outcome(conn, "put_result", dict(args, text="another answer"))[0] == \
            "state_conflict", "a second writer replaced the stored result"
        assert outcome(conn, "put_result", {"job_id": str(__import__("uuid").uuid4()),
                                            "text": "x"})[0] == "not_found", 'failed: outcome(conn, "put_result", {"job_id": str(__import__("uuid").uuid4()), "text": "x"})[0] == "not_found"'
        # D10 (0020): a stored result is read through its job's committed outcome and
        # persisted expiry - before the outcome commits the owner's read is `result_pending`
        # (the served read is tests/d/checks_content.py's, on a settled job)
        try:
            with conn.transaction():
                conn.execute("select infrx.read_result(%s, %s)", (b.ORG_A, ref))
        except psycopg.Error as failed:
            assert getattr(domain_error(failed), "code", None) == "result_pending", failed
        else:
            raise AssertionError("an unsettled job's result was served")
        for org, bad in ((b.ORG_B, ref), (b.ORG_A, "infrx-result:../../etc"),
                         (b.ORG_A, f"infrx-result:{b.ORG_A}"),
                         (b.ORG_A, "infrx-result:" + "-" * 36)):
            try:
                with conn.transaction():
                    conn.execute("select infrx.read_result(%s, %s)", (org, bad))
            except psycopg.Error as failed:
                assert getattr(domain_error(failed), "code", None) == "not_found", (org, bad)
            else:
                raise AssertionError(f"read_result answered {org} for {bad}")
        stored = conn.execute("select digest, bytes from infrx.job_results where "
                              "request_id = %s", (request.request_id,)).fetchone()
        assert stored[1] == len("a clip of a cat".encode()) and stored[0].startswith("sha256:"), 'failed: stored[1] == len("a clip of a cat".encode()) and stored[0].startswith("sha256:")'
        # prompt tokens
        _, lease = claim(conn, request.request_id)
        too_many = {"lease": lease["lease"], "media": [],
                    "prompt_tokens": request.max_input_tokens + 1}
        assert outcome(conn, "prepare", too_many)[0] == "context_length_exceeded", 'failed: outcome(conn, "prepare", too_many)[0] == "context_length_exceeded"'
        code, _ = outcome(conn, "prepare", dict(too_many, prompt_tokens=1234))
        assert code is None, code
        got, = conn.execute("select prepared_prompt_tokens from infrx.jobs where "
                            "request_id = %s", (request.request_id,)).fetchone()
        assert got == 1234, got
        return "result write-once, owner-only read; prompt count stored within the ceiling"
    return ca._in_rollback(conn, body)


def check_dispatch_details(conn) -> str:
    """The relay's small print (review OB-4..OB-6), one assertion each: `limit` bounds a
    read; rows come out oldest first; a row not yet available is not handed out;
    acknowledgment touches dispatch rows only; `release_dispatch` hands a claimed row back
    at once; `fail_dispatch` records without acknowledging; the snapshot names a job's
    LATEST event, carries the phase's own attempt counter, and skips a job under a live
    lease of EITHER kind."""
    world = ca.World(conn)
    pending = {"limit": 1000, "worker_id": "relay", "redelivery_s": 30}

    def body():
        call(conn, "dispatch_pending", pending)            # claim everything older
        advance(conn, 60)
        first = _admitted(conn, world)
        # OB-8: a read with no worker id is refused - its claim could never be acknowledged
        nobody = {k: v for k, v in pending.items() if k != "worker_id"}
        for bad in (nobody, dict(pending, worker_id=None), dict(pending, worker_id="  ")):
            assert outcome(conn, "dispatch_pending", bad)[0] == "invalid_request", \
                f"dispatch_pending claimed rows for no worker: {bad}"
        second = _admitted(conn, world)
        # a row inserted LAST but available EARLIEST, and one available only in an hour
        conn.execute("insert into infrx.outbox (event_id, aggregate_id, org_id, kind, "
                     "available_at) values (gen_random_uuid(), %s, %s, 'prepare_dispatch', "
                     "infrx.now() - interval '1 hour')", (second.request_id, b.ORG_A))
        future = _admitted(conn, world)
        conn.execute("update infrx.outbox set available_at = infrx.now() + interval '1 hour' "
                     "where aggregate_id = %s", (future.request_id,))
        # the order must come from ORDER BY, not from whichever plan happens to run: with
        # index scans off the heap returns rows in insertion order (the earliest last)
        conn.execute("set local enable_indexscan = off")
        conn.execute("set local enable_bitmapscan = off")
        one = call(conn, "dispatch_pending", dict(pending, limit=1))
        conn.execute("set local enable_indexscan = on")
        conn.execute("set local enable_bitmapscan = on")
        assert len(one) == 1, f"limit 1 handed out {len(one)} rows"
        oldest = conn.execute("select event_id::text from infrx.outbox where aggregate_id = %s "
                              "and available_at < infrx.now()", (second.request_id,)).fetchone()
        assert oldest and one[0]["event_id"] == oldest[0], \
            f"not oldest first: got {one[0]['job_id']}"
        rest = call(conn, "dispatch_pending", pending)
        assert future.request_id not in {e["job_id"] for e in rest}, \
            "a row not yet available was handed out"
        # release: a claimed row is pending again at once; fail: recorded, not acknowledged
        ev = next(e for e in rest if e["job_id"] == first.request_id)
        assert call(conn, "release_dispatch", {"event_ids": [ev["event_id"]]}) == 1, \
            "release_dispatch released nothing"
        again = call(conn, "dispatch_pending", pending)
        assert first.request_id in {e["job_id"] for e in again}, \
            "a released row waited for the redelivery window"
        assert call(conn, "fail_dispatch", {"event_id": ev["event_id"], "error": "boom"}) == 1
        row = conn.execute("select acknowledged_at is null, last_error from infrx.outbox "
                           "where event_id = %s", (ev["event_id"],)).fetchone()
        assert row == (True, "boom"), f"fail_dispatch acknowledged or lost the error: {row}"
        # acknowledgment is for dispatch rows only
        # (claimed by this relay, so only the kind predicate can refuse it)
        proj = conn.execute("insert into infrx.outbox (event_id, aggregate_id, org_id, kind, "
                            "available_at, claimed_at, claimed_by) values (gen_random_uuid(), "
                            "%s, %s, 'usage_projection', infrx.now(), infrx.now(), 'relay') "
                            "returning event_id::text", (first.request_id, b.ORG_A)).fetchone()[0]
        assert call(conn, "acknowledge_dispatch", {"event_ids": [proj],
                                                   "worker_id": "relay"}) == 0, \
            "the dispatch ack acknowledged a projection row"
        # the snapshot: the latest event, the phase's own attempt counter
        advance(conn, 1)
        later = conn.execute("insert into infrx.outbox (event_id, aggregate_id, org_id, kind, "
                             "available_at) values (gen_random_uuid(), %s, %s, "
                             "'prepare_dispatch', infrx.now()) returning event_id::text",
                             (first.request_id, b.ORG_A)).fetchone()[0]
        _, lease = claim(conn, first.request_id)          # preparation_attempts = 1
        advance(conn, DEFAULTS.preparation_lease_ttl_s)
        snap = {e["job_id"]: e for e in
                conn.execute("select infrx.dispatch_snapshot()").fetchone()[0]}
        assert snap[first.request_id]["event_id"] == later, "the snapshot kept an older event"
        assert snap[first.request_id]["attempt"] == 1, \
            f"the prepare event carries attempt {snap[first.request_id]['attempt']}, not the " \
            "preparation counter"
        # a queued job under a live INFERENCE lease is being worked (OB-5)
        queued = _admitted(conn, world)
        _, lease = claim(conn, queued.request_id)
        prepare(conn, lease["lease"])
        conn.execute("""insert into infrx.attempts (job_id, kind, generation, worker_id,
            acquired_at, expires_at, generation_deadline_at, first_token_deadline_at)
            values (%s, 'inference', 1, 'w', infrx.now(), infrx.now() + interval '2 min',
                    infrx.now() + interval '5 min', infrx.now() + interval '1 min')""",
                     (queued.request_id,))
        snap = {e["job_id"] for e in conn.execute("select infrx.dispatch_snapshot()").fetchone()[0]}
        assert queued.request_id not in snap, "a job under a live inference lease was indexed"
        return "limit, order, availability, release, fail, dispatch-only ack, latest event, " \
               "phase attempt, either lease kind"
    return ca._in_rollback(conn, body)


def check_preparation_claim_race(connect, database: str, rounds: int = 10) -> str:
    """Review OB-6b: two workers claim the same preparing job at the same instant from two
    connections - exactly one lease and one `not_claimable`, every round (the job row lock
    in `claim_preparation` is what serializes them)."""
    import threading
    for n in range(rounds):
        with connect(database) as setup:
            request = _admitted(setup, ca.World(setup))
        results: list = []
        barrier = threading.Barrier(2)

        def attempt(worker: str) -> None:
            with connect(database) as conn:
                barrier.wait()
                results.append(claim(conn, request.request_id, worker)[0])

        workers = [threading.Thread(target=attempt, args=(f"prep-{w}",)) for w in "ab"]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        with connect(database) as cleanup:
            cleanup.execute("select infrx.terminalize_unstarted(%s, 'preparation_failed')",
                            (request.request_id,))
        assert sorted(results, key=str) == sorted([None, "not_claimable"], key=str), \
            f"round {n}: two concurrent preparation claims answered {results}"
    return f"{rounds} rounds of two concurrent claims: one lease, one not_claimable each"
