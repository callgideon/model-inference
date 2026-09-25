"""E3C s09 (CREDIT-CUTOVER) and s11 (reconcile under settlement / cancel / collector).

s09: A1's signup callback and the operator's grant delivered concurrently and repeatedly -
one entitlement, one 10,000 CREDIT grant; a USD job admitted before CREDIT serving, in flight
while a CREDIT-regime worker runs - its own units and USD price snapshot, no CREDIT row, no
conversion; G8's transition report (dry run) and its unapproved-card refusal.

s11: the operator CLI's `reconcile` and `cancel` as processes racing a live generation and
its settlement - typed answers, no money moved by a reconcile of a live job, one terminal
outcome, every charge = the admitted card x usage, nothing negative."""
from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import world                                            # noqa: E402

import stack                                            # noqa: E402


# ------------------------------------------------------------------ s09


def granted_once(trip, name: str, callbacks: int = 8) -> dict:
    """A verified individual; `callbacks` concurrent A1 claims and two concurrent CLI grants
    under different idempotency keys. Exactly one entitlement, one grant row, 10,000."""
    import psycopg
    with psycopg.connect(stack.harness.pg_dsn(trip.world.database), autocommit=True) as conn:
        user, _org = stack.seed_individual(conn, name)
    start = threading.Barrier(callbacks + 2)

    def claim(_):
        with psycopg.connect(stack.harness.pg_dsn(trip.world.database), autocommit=True) as c:
            start.wait()
            return c.execute("select status from public.claim_signup_grant(%s, '', null)",
                             (user,)).fetchone()[0]

    def grant(n):
        start.wait()
        return world.cli(trip, "grant", "--user", user, "--idempotency-key", f"g-{name}-{n}",
                         "--reason", "e3c concurrent grant")
    with ThreadPoolExecutor(callbacks + 2) as pool:
        claims = [pool.submit(claim, n) for n in range(callbacks)]
        grants = [pool.submit(grant, n) for n in range(2)]
        answers = {"claims": sorted(f.result() for f in claims),
                   "grants": [f.result() for f in grants]}
    entitlements, = trip.one("select count(*) from infrx.signup_entitlements where user_id = %s",
                             user)
    grants_rows, total = trip.one(
        "select count(*), coalesce(sum(l.amount), 0) from infrx.credit_ledger l join "
        "infrx.credit_wallets w on w.wallet_id = l.wallet_id where w.owner_user_id = %s and "
        "l.kind = 'signup_grant'", user)
    assert (entitlements, grants_rows, total) == (1, 1, Decimal("10000")), \
        f"not one grant: entitlements {entitlements}, grant rows {grants_rows}, total {total}; " \
        f"{answers}"
    assert all(status == 0 for status, _ in answers["grants"]), answers
    return answers


def test_s09_concurrent_signup_callbacks_and_grants_grant_exactly_once(workdir):
    with world.composed(workdir, start=()) as trip:
        granted_once(trip, "delta")


def test_nc_credit_cutover__s09_detects_a_grant_that_is_not_unique(workdir):
    """Negative control: the grant's uniqueness dropped on the clone (E3B's db09 defect,
    reused); s09's grant oracle must report a second grant."""
    from test_journey import _signup_grant_not_unique
    with world.composed(workdir, start=()) as trip:
        assert stack.current_database() == trip.world.database
        _signup_grant_not_unique()
        with pytest.raises(AssertionError, match="not one grant"):
            granted_once(trip, "epsilon")


def usd_job(trip, key: str) -> str:
    """A legacy-USD admission for the builders' ORG_A (funded 100 USD), through D2's
    `PgJobStore.admit` - the in-flight USD job a CREDIT cutover finds. Its request id."""
    import asyncio
    import uuid
    from datetime import datetime, timezone

    import psycopg

    from infrx.contracts.conformance import builders as b
    from infrx.contracts.records import ExecutionMode
    from infrx.state.jobstore import PgJobStore, connector
    with psycopg.connect(stack.harness.pg_dsn(trip.world.database), autocommit=True) as conn:
        conn.execute("insert into public.credit_ledger (org_id, delta_usd, kind, reason) values "
                     "(%s, 100, 'grant', 'e3c historical USD')", (b.ORG_A,))
    rig = SimpleNamespace(clock=SimpleNamespace(now=lambda: datetime.now(timezone.utc)),
                          ids=SimpleNamespace(uuid=lambda: str(uuid.uuid4())))
    request = b.request(rig, mode=ExecutionMode.async_, max_output_tokens=64)
    store = PgJobStore(connector(stack.harness.pg_dsn(trip.world.database)))
    admission = asyncio.run(store.admit(request, b.idem(request, key)))
    return admission.request_id


def test_s09_a_historical_usd_job_keeps_its_units_while_credit_serves(workdir):
    """A USD job in flight when a CREDIT-regime worker runs: it is never settled or held in
    CREDIT, no CREDIT wallet appears for its organization, its USD hold ends settled at its
    own price snapshot or released, and the CREDIT tenants' books do not move."""
    from infrx.contracts.conformance import builders as b
    with world.composed(workdir, start=("gateway",)) as trip:
        before = {t.name: trip.wallet(t) for t in (trip.world.alpha, trip.world.beta)}
        request_id = usd_job(trip, "e3c-s09-usd")
        usd_before = trip.one("select ledger_total from infrx.wallets where org_id = %s",
                              b.ORG_A)[0]
        trip.box.start("worker")
        time.sleep(15.0)                     # the CREDIT worker has had the job's dispatch
        world.set_clock(trip.world.database, 7200.0)
        world.wait_for(lambda: trip.one("select state from infrx.credit_holds where "
                                        "request_id = %s", request_id)[0] != "held", 60,
                       "the USD hold ended")
        credit = trip.one("select (select count(*) from infrx.credit_wallet_holds where "
                          "request_id = %s), (select count(*) from infrx.credit_ledger where "
                          "request_id = %s), (select count(*) from infrx.credit_wallets where "
                          "personal_org_id = %s)", request_id, request_id, b.ORG_A)
        assert credit == (0, 0, 0), f"a USD job touched CREDIT books: {credit}"
        regime = trip.db("select accounting_regime from public.usage_events where id = %s",
                         request_id)
        assert regime in ([], [("legacy_usd",)]), regime
        hold, = trip.one("select state from infrx.credit_holds where request_id = %s",
                         request_id)
        usd_after = trip.one("select ledger_total from infrx.wallets where org_id = %s",
                             b.ORG_A)[0]
        charged = usd_before - usd_after
        assert hold in ("settled", "released") and charged >= 0, (hold, charged)
        assert (hold == "released") == (charged == 0), (hold, charged)
        for tenant in (trip.world.alpha, trip.world.beta):
            assert trip.wallet(tenant) == before[tenant.name], f"{tenant.name}'s CREDIT moved"


def cli_commands() -> set[str]:
    from infrx.operations import cli
    sub = next(a for a in cli.parser()._actions if a.dest == "cmd")
    return set(sub.choices)


def books(trip) -> dict:
    """What a transition must never move: the flags, every wallet, every ledger row."""
    return {"flags": trip.db("select name, enabled from infrx.feature_flags order by name"),
            "usd": trip.db("select org_id::text, ledger_total from infrx.wallets order by 1"),
            "credit": trip.db("select wallet_id::text, sum(amount) from infrx.credit_ledger "
                              "group by 1 order by 1"),
            "usd_ledger": trip.one("select count(*) from public.credit_ledger")[0]}


def test_s09_the_credit_transition_is_a_reported_dry_run_first(workdir):
    """G8 (`credit-transition`) with a historical USD job in flight: the dry run reports the
    USD inventory and its blockers and writes nothing; applying it at the PROVISIONAL seed
    card (P-01 pending) is refused `card_unapproved` and changes no flag, wallet or ledger
    row - no implicit conversion, the USD job keeps its regime."""
    commands = {c for c in cli_commands() if any(w in c for w in ("transition", "activate",
                                                                  "cutover", "inventory"))}
    if "credit-transition" not in commands:
        world.blocked("G8", why="no CREDIT transition / activation command in the operator CLI "
                                f"(commands: {sorted(cli_commands())})")
    with world.composed(workdir, start=("gateway",)) as trip:
        request_id = usd_job(trip, "e3c-s09-transition")
        before = books(trip)
        status, probe = world.cli(trip, "credit-transition", "--dry-run")
        cards = {c["rate_card_version"]: c for c in probe["inventory"]["cards"]}
        seed = cards[stack.SEED_CARD]
        rates = ("--card", stack.SEED_CARD, "--input-rate", seed["input_rate"],
                 "--output-rate", seed["output_rate"])
        status, dry = world.cli(trip, "credit-transition", "--dry-run", *rates)
        codes = {b["code"] for b in dry["blockers"]}
        assert status == 1 and "card_unapproved" in codes, (status, dry["blockers"])
        flying = dry["inventory"]["in_flight"].get("legacy_usd", {})
        assert sum(flying.values()) >= 1, f"the USD job is missing from the inventory: {flying}"
        assert books(trip) == before, "a dry run wrote"
        status, applied = world.cli(trip, "credit-transition", *rates, "--idempotency-key",
                                    "t-e3c", "--reason", "e3c: unapproved card must refuse")
        assert status == 1, f"an unapproved card was applied: {applied}"
        assert books(trip) == before, "a refused transition moved flags or money"
        assert trip.db("select accounting_regime from infrx.jobs where request_id = %s",
                       request_id) == [("legacy_usd",)]


LOCK_BOUND_S = 5.0     # the transition's drain bound in this case


def test_s09_a_transition_meeting_a_parked_admission_refuses_within_its_bound(workdir):
    """G8 lock bound (R117/R118 amended; b06e3757): an admission in flight holds its regime's
    flag FOR SHARE (0021 `require_feature`) until it commits. A transition that must freeze
    that flag waits no longer than its drain bound, then refuses `open_transactions` with
    nothing applied - never an UPDATE parked behind the admission for ever. The parked
    admission is the exact call admission makes, left open on its own connection."""
    import psycopg
    with world.composed(workdir, start=("gateway",)) as trip:
        before = books(trip)
        with psycopg.connect(stack.harness.pg_dsn(trip.world.database)) as parked:
            parked.execute("select infrx.require_feature('credit_admission')")  # txn open
            began = time.monotonic()
            status, answer = world.cli(trip, "credit-transition", "--to", "legacy_usd",
                                       "--drain-timeout-s", str(LOCK_BOUND_S),
                                       "--poll-s", "0.5", "--idempotency-key", "t-e3c-lock",
                                       "--reason", "e3c: a parked admission bounds the freeze")
            took = time.monotonic() - began
            parked.rollback()
        # the CLI's refusal is the `state_conflict` envelope naming the blockers
        assert status == 1 and answer.get("error") == "state_conflict" \
            and "open_transactions" in answer.get("message", ""), (status, answer)
        # the drain bound + the statement margin + one CLI process start and inventory
        assert took <= LOCK_BOUND_S + 15, f"the refusal took {took:.1f} s"
        assert books(trip) == before, "a refused transition moved flags or money"


# ------------------------------------------------------------------ s11


def running(trip, request_id: str) -> None:
    world.wait_for(lambda: world.attempts(trip, request_id) > 0, 30, "the inference attempt")


def test_s11_reconcile_of_a_live_job_is_typed_and_moves_no_money(workdir):
    with world.composed(workdir) as trip:
        alpha = trip.world.alpha
        trip.engine.control(delta_gap_s=0.2)
        accepted = trip.send(alpha, "async", world.TEXT, "e3c-s11-live")
        request_id = accepted.json()["request_id"]
        running(trip, request_id)
        before = trip.wallet(alpha)
        status, answer = world.cli(trip, "reconcile", "--org", alpha.org_id, "--request",
                                   request_id, "--idempotency-key", "rc-live",
                                   "--reason", "e3c reconcile a live job")
        assert status in (0, 1) and ("settlement" in answer or "error" in answer), \
            f"reconcile of a live job answered untyped: {status} {answer}"
        assert trip.wallet(alpha) == before, "a reconcile of a live job moved money"
        trip.engine.control(delta_gap_s=0.0)
        assert world.terminal(trip, request_id, timeout=90.0) == "succeeded"
        world.settled_once(trip, request_id)
        settled = trip.wallet(alpha)
        status, answer = world.cli(trip, "reconcile", "--org", alpha.org_id, "--request",
                                   request_id, "--idempotency-key", "rc-after",
                                   "--reason", "e3c reconcile a settled job")
        assert status == 0 or answer.get("error") == "state_conflict", \
            f"reconcile of a settled job answered untyped: {status} {answer}"
        assert trip.wallet(alpha) == settled, "a reconcile after settlement moved money"
        world.settled_once(trip, request_id)
        trip.conserved(alpha)


def test_s11_cancel_racing_completion_and_reconcile_ends_once(workdir):
    """A slow generation; the CLI's cancel and reconcile fired together as it finishes: one
    terminal outcome the API agrees with, at most one debit equal to card x usage, no hold
    left held, nothing negative."""
    with world.composed(workdir) as trip:
        alpha = trip.world.alpha
        trip.engine.control(delta_gap_s=0.05)
        accepted = trip.send(alpha, "async", world.TEXT, "e3c-s11-race")
        request_id, handle = accepted.json()["request_id"], accepted.json()["job_handle"]
        running(trip, request_id)
        start = threading.Barrier(2)

        def fire(args):
            start.wait()
            return world.cli(trip, *args)
        with ThreadPoolExecutor(2) as pool:
            answers = list(pool.map(fire, (
                ("cancel", "--org", alpha.org_id, "--job", handle, "--idempotency-key", "c-race",
                 "--reason", "e3c race"),
                ("reconcile", "--org", alpha.org_id, "--request", request_id,
                 "--idempotency-key", "rc-race", "--reason", "e3c race"))))
        for status, answer in answers:
            assert status in (0, 1) and answer and "raw" not in answer, answers
        trip.engine.control(delta_gap_s=0.0)
        state = world.terminal(trip, request_id, timeout=90.0)
        api = trip.until_terminal(alpha, handle)
        assert api["state"] == state, (api, state)
        books = world.money(trip, request_id)
        assert len(books["debits"]) <= 1 and "held" not in books["holds"], books
        trip.conserved(alpha)


def scrubbed_content(trip, request_id: str) -> dict:
    """The content-bearing rows s06 requires scrubbed past the persisted expiry."""
    return {"job_results.body": trip.one("select count(*) from infrx.job_results where "
                                         "request_id = %s and body <> ''", request_id)[0],
            "jobs.request_record messages": trip.one(
                "select count(*) from infrx.jobs where request_id = %s and "
                "request_record::text like %s", request_id, "%Describe the van.%")[0],
            "stream_chunks deltas": trip.one("select count(*) from infrx.stream_chunks where "
                                             "job_id = %s and event_type = 'delta'",
                                             request_id)[0]}


def test_s11_reconcile_never_recreates_scrubbed_content(workdir):
    """Reconcile racing M6's retention pass (a fresh collector process) on a settled job past
    its persisted expiry: whichever wins, the content ends scrubbed (nothing recreated), the
    reconcile answers typed, no money moves, the settlement stays single."""
    with world.composed(workdir, RESULT_TTL_S="600") as trip:
        alpha = trip.world.alpha
        answer = trip.send(alpha, "sync", world.TEXT, "e3c-s11-scrub")
        assert answer.status_code == 200, answer.text
        request_id = answer.headers["inference-id"]
        settled = trip.wallet(alpha)
        world.set_clock(trip.world.database, 4000.0)      # past the result TTL and journal's
        start = threading.Barrier(2)

        def reconcile():
            start.wait()
            return world.cli(trip, "reconcile", "--org", alpha.org_id, "--request", request_id,
                             "--idempotency-key", "rc-scrub", "--reason", "e3c scrub race")

        def collect():
            start.wait()
            return world.collectors(trip, count=1)
        with ThreadPoolExecutor(2) as pool:
            reconciled, collected = pool.submit(reconcile), pool.submit(collect)
            (status, typed), answers = reconciled.result(), collected.result()
        assert status == 0 or typed.get("error") == "state_conflict", \
            f"reconcile racing the scrub answered untyped: {status} {typed}"
        again = world.collectors(trip, count=1)              # a pass after the race
        content = scrubbed_content(trip, request_id)
        assert not any(content.values()), \
            f"content present after the race: {content} (passes: {answers}, {again})"
        assert trip.wallet(alpha) == settled, "the race moved money"
        world.settled_once(trip, request_id)
        trip.conserved(alpha)
