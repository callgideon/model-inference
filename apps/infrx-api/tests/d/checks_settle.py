"""D5: the terminal transaction (0018) as SQL-level checks, on D2's "admission" scenario
(R32/R40: each is a migration mutant's named check).

Each check drives `infrx.terminalize`/`infrx.cancel`/`infrx.recover` with the arguments
`PgJobStore` builds and rolls back. Requests are stamped by a gateway an hour behind the
database clock (`checks_leases.GATEWAY_SKEW`), so a settlement dated by the caller's clock
is an hour off. Every money path ends in `assert_no_drift`: the wallet summary equals the
immutable ledger and the active holds, in both units (the D5 acceptance, as one assertion).
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import psycopg
from psycopg.types.json import Jsonb

from infrx.contracts import money
from infrx.contracts.conformance import builders as b
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import Lease, PriceSnapshot, Usage

from . import checks_admission as ca
from . import checks_credit as cc
from . import checks_leases as cl
from .checks_dispatch import advance, call, kinds, outcome

LIMITS = cl.LIMITS
WINDOW = timedelta(seconds=DEFAULTS.unknown_usage_reconcile_s)
BILLABLE = (("completed", "succeeded"), ("client_cancelled", "cancelled"),
            ("client_disconnected", "failed"))
ABSORBED = (("sync_deadline", "failed"), ("deadline_exceeded", "failed"),
            ("engine_error", "failed"),
            ("engine_incomplete", "failed"), ("retries_exhausted", "failed"),
            ("journal_write_failed", "failed"), ("lost_after_publication", "failed"),
            ("platform_error", "failed"))
#: The fake's FREE_CAUSES: the customer was never going to be charged.
FREE = (("invalid_media", "failed"), ("preparation_failed", "failed"),
        ("queue_wait_expired", "expired"))


# --------------------------------------------------------------------- drift (item 5a)
def assert_no_drift(conn, what: str = "") -> None:
    """The acceptance "wallet summary equals immutable ledger and active holds": both
    detectors (0003 USD, 0006 CREDIT) return no drifting row."""
    usd = conn.execute("select org_id, ledger_drift, reserved_drift from "
                       "infrx.wallet_reconciliation where ledger_drift <> 0 "
                       "or reserved_drift <> 0").fetchall()
    credit = conn.execute("select wallet_id, ledger_drift, reserved_drift from "
                          "infrx.credit_wallet_reconciliation where ledger_drift <> 0 "
                          "or reserved_drift <> 0").fetchall()
    assert not usd and not credit, \
        f"{what}: the wallet summary drifted from its ledger and holds: USD {usd} CREDIT {credit}"


# --------------------------------------------------------------------- driving
def propose(job_id, cause: str = "completed", state: str = "succeeded",
            usage: tuple[int, int] | None = None, ref: str | None = None) -> dict:
    """A worker's proposal exactly as `PgJobStore` dumps a `TerminalOutcome` (the settlement
    and debit it carries are the worker's guess; the store recomputes both)."""
    return {"schema_version": 1, "job_id": str(job_id), "state": state, "cause": cause,
            "usage": None if usage is None else Usage.of(*usage).model_dump(mode="json"),
            "result_ref": ref, "settlement_state": "released_free", "debit": "0.00000000",
            "settled_at": "2026-09-20T12:00:00+00:00", "reconcile_after": None}


def settle(conn, lease: Lease, proposal: dict, regime: str = "legacy_usd"):
    """(code, admission document) of one `terminalize` call, as `PgJobStore._terminalize`."""
    return outcome(conn, "terminalize", {"lease": lease.model_dump(mode="json"),
                                         "outcome": proposal, "regime": regime,
                                         "limits": LIMITS})


def stored(conn, job_id) -> str:
    """The worker's result object (0014), stored before the settlement (02 §7)."""
    return call(conn, "put_result", {"job_id": str(job_id), "text": f"result of {job_id}"})


def ledger(conn, request_id) -> list[tuple]:
    return conn.execute("select delta_usd, kind, created_at from public.credit_ledger "
                        "where request_id = %s", (request_id,)).fetchall()


def usd(conn, org: str = b.ORG_A) -> tuple[Decimal, Decimal]:
    """(ledger_total, reserved_total) of the organization's USD wallet."""
    return conn.execute("select ledger_total, reserved_total from infrx.wallets "
                        "where org_id = %s", (org,)).fetchone()


def hold(conn, request_id) -> str:
    return conn.execute("select state from infrx.credit_holds where request_id = %s",
                        (request_id,)).fetchone()[0]


def usage_row(conn, request_id) -> dict | None:
    cur = conn.execute("select * from public.usage_events where id = %s", (request_id,))
    names = [c.name for c in cur.description]
    found = cur.fetchone()
    return None if found is None else dict(zip(names, found))


def footprint(conn, request_id) -> tuple:
    """Everything a settlement may write for one job: the job's terminal facts, its hold,
    both wallets' totals, its ledger rows, its usage row, its outbox rows and its chunks."""
    job = cl.row(conn, request_id)
    return ((job["state"], job["settled_at"], job["debit"], job["proposal"]),
            usd(conn), hold(conn, request_id), len(ledger(conn, request_id)),
            usage_row(conn, request_id) is not None, len(kinds(conn, request_id)),
            conn.execute("select count(*) from infrx.stream_chunks where job_id = %s",
                         (request_id,)).fetchone()[0])


def priced(conn, snapshot: PriceSnapshot) -> str:
    """A price version for its own model revision (same catalog alias), effective now."""
    conn.execute("insert into infrx.price_versions (price_version, model_revision, "
                 "input_rate_per_million, output_rate_per_million, token_rules_version, "
                 "effective_from) values (%s, %s, %s, %s, %s, infrx.now())",
                 (snapshot.price_version, snapshot.model_revision,
                  snapshot.input_rate_per_million, snapshot.output_rate_per_million,
                  snapshot.token_rules_version))
    return snapshot.model_revision


# --------------------------------------------------------------------- item 1
def check_settle_exact(conn) -> str:
    """DUR-SETTLE, one winner: a settled job's debit is the ADMITTED snapshot over the
    usage, half up once (the Python `money.debit`); the ledger moves by exactly that
    (one `usage` row), the hold is `settled` and its reservation released, in the settling
    transaction. The identical proposal again answers the committed outcome and writes
    nothing; the rounding boundary (0.005/M x 1 token = 0.000000005 -> 0.00000001) and an
    exact product (nothing to round) settle as Python computes them."""
    world = ca.World(conn)

    def body():
        request, lease = cl.running(conn, world)
        before = usd(conn)
        maximum = cl.row(conn, request.request_id)["maximum_hold"]
        proposal = propose(lease.job_id, usage=(1200, 340), ref=stored(conn, lease.job_id))
        code, doc = settle(conn, lease, proposal)
        assert code is None, code
        # 5a first: whatever else is wrong, the summary must still be ledger + holds
        assert_no_drift(conn, "a settled job")
        expected = b.DEFAULT_PRICE.debit(1200, 340)
        out = doc["outcome"]
        assert (out["settlement_state"], Decimal(out["debit"])) == ("settled", expected), out
        assert out["usage"] == Usage.of(1200, 340).model_dump(
            mode="json", exclude={"schema_version"}), out["usage"]
        entries = [(d, k) for d, k, _ in ledger(conn, request.request_id)]
        assert entries == [(-expected, "usage")], f"not one usage debit: {entries}"
        assert usd(conn) == (before[0] - expected, before[1] - maximum), (before, usd(conn))
        assert hold(conn, request.request_id) == "settled", hold(conn, request.request_id)
        count = len(kinds(conn, request.request_id))
        code, again = settle(conn, lease, proposal)
        assert code is None and again["outcome"] == out, f"the identical retry: {code} {again}"
        assert len(ledger(conn, request.request_id)) == 1 and \
            len(kinds(conn, request.request_id)) == count, "a replay wrote again"
        for version, rates, tokens, want in (
                ("pv_d5_tie", ("0.005", "0.60"), (1, 0), "0.00000001"),
                ("pv_d5_below", ("0.004", "0.60"), (1, 0), "0.00000000"),
                ("pv_d5_exact", ("0.15", "0.45"), (333, 111), "0.00009990")):
            snapshot = b.price(*rates, version=version, model_revision=f"{b.MODEL}-{version}")
            job, job_lease = cl.running(conn, world, model_revision=priced(conn, snapshot))
            code, doc = settle(conn, job_lease, propose(job_lease.job_id, usage=tokens,
                                                        ref=stored(conn, job_lease.job_id)))
            debit = money.format_money(Decimal(doc["outcome"]["debit"]))
            assert (code, debit) == (None, want) and \
                money.format_money(snapshot.debit(*tokens)) == want, (version, code, debit)
            assert doc["outcome"]["settlement_state"] == \
                ("released_free" if want == "0.00000000" else "settled"), doc["outcome"]
        grid = [(p, c) for p in (0, 1, 333, 1200, 30719, 30720) for c in (0, 1, 111, 2047, 2048)]
        for rates in (("0.20", "0.60"), ("0.005", "0.015"), ("0.33333333", "0.77777777")):
            snapshot = b.price(*rates)
            for p, c in grid:
                sql, = conn.execute("select infrx.debit_legacy_usd(%s, %s, %s)",
                                    (Jsonb(snapshot.model_dump(mode="json")),
                                     p, c)).fetchone()
                assert sql == snapshot.debit(p, c), (rates, p, c, sql, snapshot.debit(p, c))
        assert_no_drift(conn, "the rounding grid")
        return f"exact decimals, one debit, replayed; {3 * len(grid)} grid points = money.debit"
    return ca._in_rollback(conn, body)


def check_settle_late_data(conn) -> str:
    """DUR-SETTLE / DUR-OUTPUT, late data is internal only: after terminal, a DIFFERENT
    proposal (other usage, cause or reference) is `already_terminal` and writes nothing a
    customer reads - no ledger, usage, outbox or journal row, the job untouched; a proposal
    naming another job is refused before anything is read; a job cancelled first answers a
    late completion `already_terminal`; a foreign worker or a stale generation cannot settle
    a live job (`stale_lease`); the settled usage and proposal cannot be rewritten."""
    world = ca.World(conn)

    def body():
        request, lease = cl.running(conn, world)
        ref = stored(conn, lease.job_id)
        assert settle(conn, lease, propose(lease.job_id, usage=(1200, 340), ref=ref))[0] is None
        before = footprint(conn, request.request_id)
        for label, late in (
                ("other usage", propose(lease.job_id, usage=(9, 9), ref=ref)),
                ("another cause", propose(lease.job_id, "engine_error", "failed")),
                ("no reference", propose(lease.job_id, "client_disconnected", "failed",
                                         usage=(1200, 340)))):
            code, _ = settle(conn, lease, late)
            assert code == "already_terminal", f"{label}: {code}"
            assert footprint(conn, request.request_id) == before, f"{label} wrote something"
        other, other_lease = cl.running(conn, world)
        mine = footprint(conn, other.request_id)
        assert settle(conn, other_lease, propose(lease.job_id, usage=(1, 1)))[0] == \
            "invalid_request", "a proposal for another job was read"
        assert footprint(conn, other.request_id) == mine
        code, _ = outcome(conn, "cancel", {"org_id": other.org_id, "limits": LIMITS,
                                           "job_handle": cl.row(conn, other.request_id)
                                           ["job_handle"]})
        cancelled = footprint(conn, other.request_id)
        assert settle(conn, other_lease, propose(other.request_id, usage=(1200, 340),
                                                 ref=stored(conn, other.request_id)))[0] == \
            "already_terminal", "a completion after the cancel settled"
        assert footprint(conn, other.request_id) == cancelled, "the late completion wrote"
        # a superseded or foreign worker cannot settle a live job (the fence, DUR-FENCE)
        live, live_lease = cl.running(conn, world)
        ref = stored(conn, live.request_id)
        mine = footprint(conn, live.request_id)
        for label, token in (("a foreign worker", live_lease.model_copy(update={"worker_id": "w9"})),
                             ("a stale generation", live_lease.model_copy(update={"generation": 2}))):
            code, _ = settle(conn, token, propose(live.request_id, usage=(1200, 340), ref=ref))
            assert code == "stale_lease", f"{label} settled: {code}"
            assert footprint(conn, live.request_id) == mine, f"{label} wrote something"
        # the settled usage and the winning proposal are as immutable as the outcome
        for column, value in (("usage_prompt_tokens", 1), ("proposal", "{}")):
            try:
                with conn.transaction():
                    conn.execute(f"update infrx.jobs set {column} = %s where request_id = %s",
                                 (value, request.request_id))
            except psycopg.errors.CheckViolation:
                continue
            raise AssertionError(f"a settled job's {column} was rewritten")
        assert_no_drift(conn, "late data")
        return "late proposals already_terminal, nothing written; stale leases and another " \
            "job's proposal refused; the settled record is immutable"
    return ca._in_rollback(conn, body)


def check_settle_result_ref(conn) -> str:
    """R30: a `succeeded` proposal without a reference is `invalid_request` before anything
    is read; a reference must be THIS job's stored result (`infrx-result:<id>` with its
    0014 row) - one with no stored row, another job's (both stored), or an opaque text are
    refused, nothing moved; the job's own stored reference settles, and its result is kept
    `result_ttl_s` past the settlement (`result_expires_at`, D2 limit 9)."""
    world = ca.World(conn)

    def body():
        request, lease = cl.running(conn, world)
        other, _other_lease = cl.running(conn, world)
        before = footprint(conn, request.request_id)
        refs = {"no reference": None,
                "this job's, never stored": f"infrx-result:{lease.job_id}",
                "another job's": stored(conn, other.request_id),
                "an opaque text": "results/test/result.json"}
        code, _ = settle(conn, lease, propose(lease.job_id, usage=(1200, 340),
                                              ref=refs["no reference"]))
        assert code == "invalid_request", f"a success without a reference: {code}"
        code, _ = settle(conn, lease, propose(lease.job_id, usage=(1200, 340),
                                              ref=refs["this job's, never stored"]))
        assert code == "invalid_request", f"a reference with no stored result: {code}"
        mine = stored(conn, lease.job_id)
        for label in ("another job's", "an opaque text"):
            code, _ = settle(conn, lease, propose(lease.job_id, usage=(1200, 340),
                                                  ref=refs[label]))
            assert code == "invalid_request", f"{label} reference: {code}"
        assert footprint(conn, request.request_id) == before, "a refused reference moved"
        code, doc = settle(conn, lease, propose(lease.job_id, usage=(1200, 340), ref=mine))
        assert code is None and doc["outcome"]["result_ref"] == mine, (code, doc)
        job = cl.row(conn, request.request_id)
        assert job["result_expires_at"] == job["settled_at"] + timedelta(
            seconds=DEFAULTS.result_ttl_s), (job["result_expires_at"], job["settled_at"])
        assert_no_drift(conn, "result references")
        return "succeeded needs this job's stored result; its retention starts at settlement"
    return ca._in_rollback(conn, body)


def check_settle_causes(conn) -> str:
    """R21: with the same authoritative usage, exactly `completed`, `client_cancelled` and
    `client_disconnected` settle a debit; every other cause (our deadlines included) moves
    no ledger and releases the hold - `released_free` for the never-charged causes,
    platform-absorbed otherwise, the usage kept on the outcome. A client's cause WITHOUT
    usage, nothing published, is free; `completed` without usage is an `engine_incomplete`
    failure (platform-absorbed, its reference dropped)."""
    world = ca.World(conn)

    def body():
        seen = {}
        for cause, state in BILLABLE + ABSORBED + FREE:
            request, lease = cl.running(conn, world)
            before = usd(conn)
            maximum = cl.row(conn, request.request_id)["maximum_hold"]
            ref = stored(conn, lease.job_id) if state == "succeeded" else None
            code, doc = settle(conn, lease, propose(lease.job_id, cause, state,
                                                    usage=(1200, 340), ref=ref))
            assert code is None, (cause, code)
            out = doc["outcome"]
            charged = before[0] - usd(conn)[0]
            if (cause, state) in BILLABLE:
                assert (out["settlement_state"], charged) == \
                    ("settled", b.DEFAULT_PRICE.debit(1200, 340)), (cause, out, charged)
            else:
                want = "released_free" if (cause, state) in FREE \
                    else "released_platform_absorbed"
                assert (out["settlement_state"], charged, out["debit"]) == \
                    (want, 0, "0.00000000"), (cause, out, charged)
                assert out["usage"] is not None and \
                    cl.row(conn, request.request_id)["usage_certainty"] == "authoritative", cause
            assert usd(conn)[1] == before[1] - maximum, f"{cause}: the hold was not released"
            seen[cause] = out["settlement_state"]
        for cause, state in BILLABLE:
            request, lease = cl.running(conn, world)
            ref = stored(conn, lease.job_id) if state == "succeeded" else None
            code, doc = settle(conn, lease, propose(lease.job_id, cause, state, ref=ref))
            out = doc["outcome"]
            # the fake's order: `completed` is rewritten first, then settled as the engine
            # failure it is (platform-absorbed); a client's own cause without usage is free
            want = ("engine_incomplete", "failed", None, "released_platform_absorbed") \
                if cause == "completed" else (cause, state, None, "released_free")
            assert (code, out["debit"]) == (None, "0.00000000"), (cause, code, out)
            assert (out["cause"], out["state"], out["result_ref"],
                    out["settlement_state"]) == want, (cause, out)
        assert_no_drift(conn, "every cause")
        return f"{len(seen)} causes: 3 billable, the rest free or absorbed"
    return ca._in_rollback(conn, body)


def check_settle_envelope(conn) -> str:
    """DUR-SETTLE: usage over a token ceiling - even when its debit would fit the hold - is
    a `platform_error`, usage and reference dropped, the hold released, nothing debited;
    exactly at the ceilings settles. A debit past the hold (a job whose `maximum_hold` a
    test lowers below its snapshot's worst case) is the same free platform error, never
    an error and never an unreserved debit."""
    world = ca.World(conn)

    def body():
        for max_in, max_out, p, c in ((32, 16, 32, 4096), (32, 16, 33, 16), (30000, 16, 1, 4096)):
            request, lease = cl.running(conn, world, max_input_tokens=max_in,
                                        max_output_tokens=max_out)
            before = usd(conn)
            code, doc = settle(conn, lease, propose(lease.job_id, usage=(p, c),
                                                    ref=stored(conn, lease.job_id)))
            out = doc["outcome"]
            assert (code, out["cause"], out["state"], out["settlement_state"]) == \
                (None, "platform_error", "failed", "released_platform_absorbed"), (p, c, out)
            assert (out["usage"], out["result_ref"], out["debit"]) == \
                (None, None, "0.00000000"), out
            assert usd(conn)[0] == before[0], f"{p}/{c}: debited past the envelope"
        request, lease = cl.running(conn, world, max_input_tokens=32, max_output_tokens=16)
        code, doc = settle(conn, lease, propose(lease.job_id, usage=(32, 16),
                                                ref=stored(conn, lease.job_id)))
        assert (code, doc["outcome"]["settlement_state"]) == (None, "settled"), (code, doc)
        # A debit past the hold: the hold made smaller than its own snapshot's worst case.
        request, lease = cl.running(conn, world, max_input_tokens=32, max_output_tokens=16)
        conn.execute("alter table infrx.jobs disable trigger jobs_guard")
        conn.execute("update infrx.jobs set maximum_hold = 0.00000001 where request_id = %s",
                     (request.request_id,))
        conn.execute("alter table infrx.jobs enable trigger jobs_guard")
        before = usd(conn)
        code, doc = settle(conn, lease, propose(lease.job_id, usage=(32, 16),
                                                ref=stored(conn, lease.job_id)))
        assert code is None, f"a debit past the hold failed instead of being absorbed: {code}"
        assert (doc["outcome"]["cause"], doc["outcome"]["settlement_state"]) == \
            ("platform_error", "released_platform_absorbed"), doc["outcome"]
        assert usd(conn)[0] == before[0], "a debit past the hold was charged"
        return "over the envelope: platform_error, free; exactly at it: settled"
    return ca._in_rollback(conn, body)


def check_settle_unknown(conn) -> str:
    """02: published output with no authoritative usage is `held_unknown` whatever the
    cause (billable or not): the hold `unknown`, still reserved, `reconcile_after` = the
    database clock + the window, usage certainty `unknown`, no ledger row; a `completed`
    one keeps its cause (it did publish)."""
    world = ca.World(conn)

    def body():
        for cause, state in (("completed", "succeeded"), ("client_disconnected", "failed"),
                             ("engine_error", "failed")):
            request, lease = cl.running(conn, world)
            cl.publish(conn, lease)
            advance(conn, 3)
            now = world.clock.now()
            before = usd(conn)
            ref = stored(conn, lease.job_id) if state == "succeeded" else None
            code, doc = settle(conn, lease, propose(lease.job_id, cause, state, ref=ref))
            out = doc["outcome"]
            assert (code, out["cause"], out["settlement_state"], out["debit"]) == \
                (None, cause, "held_unknown", "0.00000000"), (cause, code, out)
            assert out["reconcile_after"] is not None and \
                cl.row(conn, request.request_id)["reconcile_after"] == now + WINDOW, out
            held = conn.execute("select state, reconcile_after from infrx.credit_holds where "
                                "request_id = %s", (request.request_id,)).fetchone()
            assert held == ("unknown", now + WINDOW) and usd(conn) == before, (cause, held)
            assert cl.row(conn, request.request_id)["usage_certainty"] == "unknown"
            assert ledger(conn, request.request_id) == [], "unknown usage was debited"
        assert_no_drift(conn, "held_unknown")
        return "published without usage: held_unknown for the window, never debited"
    return ca._in_rollback(conn, body)


def check_settle_releases(conn) -> str:
    """02 §7: the settling transaction releases every capacity reservation and the
    attempt, starts the tombstone clock (the idempotency mapping expires
    `idempotency_ttl_s` after the terminal state), writes exactly one usage and one trace
    projection, and ONE pilot usage row: the job's outcome, state, settlement, tokens, the
    ADMITTED price version and `cost_usd` = the debit."""
    world = ca.World(conn)

    def body():
        request, lease = cl.running(conn, world)
        code, doc = settle(conn, lease, propose(lease.job_id, usage=(1200, 340),
                                                ref=stored(conn, lease.job_id)))
        assert code is None, code
        job = cl.row(conn, request.request_id)
        assert cl.active_reservations(conn, request.request_id) == [], "a reservation is kept"
        assert cl.live_attempts(conn, request.request_id) == [], "the attempt is still live"
        expires, = conn.execute("select expires_at from infrx.idempotency where request_id "
                                "= %s", (request.request_id,)).fetchone()
        assert expires == job["settled_at"] + timedelta(seconds=DEFAULTS.idempotency_ttl_s), \
            (expires, job["settled_at"])
        projections = sorted(k for k in kinds(conn, request.request_id)
                             if k.endswith("projection"))
        assert projections == ["trace_projection", "usage_projection"], projections
        row = usage_row(conn, request.request_id)
        assert row is not None, "no usage row"
        want = {"settlement_regime": "pilot", "outcome": "completed", "job_state": "succeeded",
                "settlement_state": "settled", "usage_certainty": "authoritative",
                "prompt_tokens": 1200, "completion_tokens": 340,
                "price_version": b.DEFAULT_PRICE.price_version, "settlement_version": 1,
                "cost_usd": b.DEFAULT_PRICE.debit(1200, 340), "accounting_regime": "legacy_usd",
                "charged_credits": None, "org_id": request.org_id, "status": 200}
        got = {k: row[k] for k in want}
        got["org_id"] = str(got["org_id"])
        assert got == want, (got, want)
        assert doc["outcome"]["settlement_state"] == "settled"
        assert_no_drift(conn, "releases")
        return "reservations, attempt, tombstone, projections and the usage row, one transaction"
    return ca._in_rollback(conn, body)


def check_settle_clock(conn) -> str:
    """R7: every instant of a settlement is the database clock - `settled_at`, the ledger
    row's and the usage row's `created_at` (their column defaults are now(), D1), and the
    tombstone - never the gateway's (an hour behind) nor the transaction's wall clock."""
    world = ca.World(conn)

    def body():
        request, lease = cl.running(conn, world)
        advance(conn, 11)
        now = world.clock.now()
        assert settle(conn, lease, propose(lease.job_id, usage=(1200, 340),
                                           ref=stored(conn, lease.job_id)))[0] is None
        job = cl.row(conn, request.request_id)
        entry = ledger(conn, request.request_id)
        row = usage_row(conn, request.request_id)
        assert job["settled_at"] == now, (job["settled_at"], now)
        assert [created for *_, created in entry] == [now], entry
        assert row["created_at"] == now, (row["created_at"], now)
        return "settled_at, ledger and usage created_at are infrx.now()"
    return ca._in_rollback(conn, body)


def check_settle_terminal_event(conn) -> str:
    """D4 request 6 / R30: the settlement's terminal journal event is 0017's trigger's -
    exactly one, last, its payload the STORED outcome (a rewritten one included) - and the
    settling transaction writes no second one."""
    world = ca.World(conn)

    def body():
        for proposal_of, want in (
                (lambda job: propose(job, usage=(1200, 340), ref=f"infrx-result:{job}"),
                 {"state": "succeeded", "cause": "completed", "settlement_state": "settled"}),
                (lambda job: propose(job, usage=None, ref=f"infrx-result:{job}"),
                 {"state": "failed", "cause": "engine_incomplete",
                  "settlement_state": "released_platform_absorbed"})):
            request, lease = cl.running(conn, world)
            stored(conn, lease.job_id)
            code, _ = settle(conn, lease, proposal_of(lease.job_id))
            assert code is None, code
            events = conn.execute("select event_type, payload from infrx.stream_chunks where "
                                  "job_id = %s order by generation, sequence",
                                  (request.request_id,)).fetchall()
            assert [t for t, _ in events].count("terminal") == 1 and \
                events[-1] == ("terminal", want), events
        return "one terminal event, the trigger's, from the stored outcome"
    return ca._in_rollback(conn, body)


# --------------------------------------------------------------------- item 3
def _cancel(conn, request, cause=None):
    args = {"org_id": request.org_id, "limits": LIMITS,
            "job_handle": cl.row(conn, request.request_id)["job_handle"]}
    if cause is not None:
        args["cause"] = cause
    return outcome(conn, "cancel", args)


def check_cancel_cause(conn) -> str:
    """R21 (G2's D-new): `cancel` records the cause it is given in state `cancelled` -
    none given is `client_cancelled`. Unpublished, the client's causes are `released_free`
    and `sync_deadline` is `released_platform_absorbed`; published, each is `held_unknown`.
    No cause moves the ledger. A repeat with another cause answers the committed outcome."""
    world = ca.World(conn)

    def body():
        for cause, free in ((None, "released_free"), ("client_cancelled", "released_free"),
                            ("client_disconnected", "released_free"),
                            ("sync_deadline", "released_platform_absorbed")):
            for published in (False, True):
                request, lease = cl.running(conn, world)
                if published:
                    cl.publish(conn, lease)
                before = usd(conn)
                code, out = _cancel(conn, request, cause)
                want = (cause or "client_cancelled", "cancelled",
                        "held_unknown" if published else free)
                assert (code, (out["cause"], out["state"], out["settlement_state"])) == \
                    (None, want), (cause, published, code, out)
                assert usd(conn)[0] == before[0], f"{cause}: the ledger moved"
                other = "sync_deadline" if cause != "sync_deadline" else "client_cancelled"
                code, again = _cancel(conn, request, other)
                assert (code, again) == (None, out), f"a repeat changed the outcome: {again}"
        assert_no_drift(conn, "cancel causes")
        return "the cause is recorded and settled by R21; the first cause wins"
    return ca._in_rollback(conn, body)


def check_cancel_refuses_a_cause(conn) -> str:
    """R21: a canceller names a client cause or the synchronous deadline and nothing else.
    Every other `TerminalCause` and a value that is no cause at all are `invalid_request`
    before anything is read: the job still running, its hold held, nothing recorded - and
    another tenant's handle with a bad cause is the same refusal, never `not_found`."""
    world = ca.World(conn)

    def body():
        request, _lease = cl.running(conn, world)
        before = footprint(conn, request.request_id)
        for cause in ("completed", "engine_error", "deadline_exceeded", "queue_wait_expired",
                      "platform_error", "bogus", ""):
            code, _ = _cancel(conn, request, cause)
            assert code == "invalid_request", f"cancel accepted {cause!r}: {code}"
            assert footprint(conn, request.request_id) == before, f"{cause!r} changed the job"
        code, _ = outcome(conn, "cancel", {"org_id": b.ORG_B, "cause": "bogus",
                                           "limits": LIMITS, "job_handle":
                                           cl.row(conn, request.request_id)["job_handle"]})
        assert code == "invalid_request", code
        assert _cancel(conn, request)[1]["cause"] == "client_cancelled"
        return "only the three cancel causes; anything else refused, nothing changed"
    return ca._in_rollback(conn, body)


# --------------------------------------------------------------------- item 5b
def check_settle_released(conn) -> str:
    """I3B request 5: the 24 h release of an unknown-usage hold is reported by the sweep as
    `{"released": outcome}` - never as a new terminal `outcome` - once, at the window on
    the database clock; a terminalization in the same sweep stays an `outcome`."""
    world = ca.World(conn)

    def body():
        request, lease = cl.running(conn, world)
        cl.publish(conn, lease)
        assert settle(conn, lease, propose(lease.job_id, "client_disconnected",
                                           "failed"))[0] is None
        advance(conn, DEFAULTS.unknown_usage_reconcile_s)
        lapsed = cl.queued(conn, world)
        advance(conn, DEFAULTS.queue_wait_interactive_s)
        produced = call(conn, "recover", {"limits": LIMITS})
        released = [item["released"] for item in produced if "released" in item]
        outcomes = [item["outcome"] for item in produced if "outcome" in item]
        assert [o["job_id"] for o in released] == [request.request_id], produced
        assert released[0]["settlement_state"] == "released_platform_absorbed", released
        assert [o["job_id"] for o in outcomes] == [lapsed.request_id], produced
        assert [i for i in call(conn, "recover", {"limits": LIMITS}) if "released" in i] == [], \
            "a release was reported twice"
        assert_no_drift(conn, "the 24 h release")
        return "the 24 h release is `released`; a terminalization is an `outcome`"
    return ca._in_rollback(conn, body)
