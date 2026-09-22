"""D2: preparation and the dispatch outbox (0012) as SQL-level checks, on the same
"admission" scenario as `checks_admission` (R32/R40: each is a mutant's named check).

Each check drives the boundaries with the arguments the adapter builds and rolls back.
"""
from __future__ import annotations

from datetime import timedelta

import psycopg
from psycopg.types.json import Jsonb

from infrx.contracts import errors
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
        assert Lease.model_validate(lease).kind.value == "preparation"
        assert lease["generation"] == 1 and lease["worker_id"] == "prep-a"
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
        assert job(conn, request.request_id)[0] == "preparing"
        refs = (b.media(b.ORG_A),)
        code, admission = prepare(conn, lease, refs)
        assert code is None, code
        state, attempts, stored, queued_at, queue_deadline, _, _ = job(conn, request.request_id)
        assert (state, attempts) == ("queued", 1), (state, attempts)
        assert stored == [r.model_dump(mode="json") for r in refs], stored
        assert queued_at == now and queue_deadline == min(
            now + timedelta(seconds=DEFAULTS.queue_wait_interactive_s), request.deadline_at)
        assert admission["state"] == "queued"
        active = conn.execute("select kind from infrx.capacity_reservations where "
                              "request_id = %s and active order by kind",
                              (request.request_id,)).fetchall()
        assert [k for k, in active] == ["inference", "journal_bytes"], active
        live = conn.execute("select count(*) from infrx.attempts where job_id = %s and "
                            "released_at is null", (request.request_id,)).fetchone()[0]
        assert live == 0, "the finished preparation attempt is still live"
        assert sorted(kinds(conn, request.request_id)) == ["inference_dispatch",
                                                           "prepare_dispatch"]
        assert prepare(conn, lease)[0] == "stale_lease", "a spent lease queued the job twice"
        assert kinds(conn, request.request_id).count("inference_dispatch") == 1
        # an expired lease is stale (before the phase deadline)
        other = _admitted(conn, world)
        _, answer = claim(conn, other.request_id)
        advance(conn, DEFAULTS.preparation_lease_ttl_s + 1)
        assert prepare(conn, answer["lease"])[0] == "stale_lease", "an expired lease queued"
        assert job(conn, other.request_id)[0] == "preparing"
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
        assert reserved > 0
        advance(conn, DEFAULTS.preparation_timeout_s)
        assert claim(conn, request.request_id)[0] == "already_terminal"
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
        assert {a.request_id, c.request_id} <= ids(first)
        assert not {a.request_id, c.request_id} & ids(call(conn, "dispatch_pending", pending)), \
            "a claimed, unacknowledged row was handed out again inside its window"
        advance(conn, 30)
        again = call(conn, "dispatch_pending", pending)
        assert {a.request_id, c.request_id} <= ids(again), "a lost acknowledgment lost the job"
        event_a = next(e for e in again if e["job_id"] == a.request_id)
        assert event_a["kind"] == "prepare_dispatch" and event_a["attempt"] == 0
        assert call(conn, "acknowledge_dispatch", {"event_ids": [event_a["event_id"]]}) == 1
        assert call(conn, "acknowledge_dispatch", {"event_ids": [event_a["event_id"]]}) == 0
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
        return "at-least-once with a redelivery window, superseded rows acked, snapshot exact"
    return ca._in_rollback(conn, body)


def check_outbox_gc(conn) -> str:
    """Outbox expiry/GC (0013): a terminal job's unacknowledged dispatch row is expired
    (acknowledged, never deleted outright); an acknowledged row older than the retention
    is deleted unless its job is live or it is a callback delivery; nothing unacknowledged
    and nothing inside the retention is deleted; `limit` bounds a call."""
    world = ca.World(conn)

    def body():
        # start from a collected outbox, so the counts below are this check's rows only
        conn.execute("select infrx.gc_outbox('{\"retention_s\": 0, \"limit\": 10000}')")
        advance(conn, 1)
        conn.execute("select infrx.gc_outbox('{\"retention_s\": 0, \"limit\": 10000}')")
        live = _admitted(conn, world)
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

        first = call(conn, "gc_outbox", {"retention_s": 3600, "limit": 1000})
        assert first == {"expired": 1, "deleted": 0}, first
        assert rows(dead.request_id) == [
            ("callback_delivery", True, ""), ("prepare_dispatch", True, "expired: job terminal"),
            ("trace_projection", False, ""), ("usage_projection", False, "")], \
            rows(dead.request_id)
        advance(conn, 3600)
        assert call(conn, "gc_outbox", {"retention_s": 3600, "limit": 1000}) == \
            {"expired": 0, "deleted": 0}, "a row inside its retention was deleted"
        advance(conn, 1)
        second = call(conn, "gc_outbox", {"retention_s": 3600, "limit": 1000})
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
        counts = [call(conn, "gc_outbox", {"retention_s": 3600, "limit": 1})["expired"]
                  for _ in range(4)]
        assert counts == [1, 1, 1, 0], counts
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
                                            "text": "x"})[0] == "not_found"
        body_, = conn.execute("select infrx.read_result(%s, %s)", (b.ORG_A, ref)).fetchone()
        assert body_ == "a clip of a cat"
        for org, bad in ((b.ORG_B, ref), (b.ORG_A, "infrx-result:../../etc"),
                         (b.ORG_A, f"infrx-result:{b.ORG_A}")):
            try:
                with conn.transaction():
                    conn.execute("select infrx.read_result(%s, %s)", (org, bad))
            except psycopg.Error as failed:
                assert getattr(domain_error(failed), "code", None) == "not_found", (org, bad)
            else:
                raise AssertionError(f"read_result answered {org} for {bad}")
        stored = conn.execute("select digest, bytes from infrx.job_results where "
                              "request_id = %s", (request.request_id,)).fetchone()
        assert stored[1] == len("a clip of a cat".encode()) and stored[0].startswith("sha256:")
        # prompt tokens
        _, lease = claim(conn, request.request_id)
        too_many = {"lease": lease["lease"], "media": [],
                    "prompt_tokens": request.max_input_tokens + 1}
        assert outcome(conn, "prepare", too_many)[0] == "context_length_exceeded"
        code, _ = outcome(conn, "prepare", dict(too_many, prompt_tokens=1234))
        assert code is None, code
        got, = conn.execute("select prepared_prompt_tokens from infrx.jobs where "
                            "request_id = %s", (request.request_id,)).fetchone()
        assert got == 1234, got
        return "result write-once, owner-only read; prompt count stored within the ceiling"
    return ca._in_rollback(conn, body)
