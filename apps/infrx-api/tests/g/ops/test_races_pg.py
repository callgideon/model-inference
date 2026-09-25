"""G8 point 4: races, retries and refusals on a REAL PostgreSQL (CREDIT-GRANT, CREDIT-SPEND).

Every concurrent call below runs on its own `service_role` connection (`jobstore.connector`
opens one per operation), so the database - not the event loop - serializes them. Where
the interleaving matters, a barrier holds every caller after its idempotency lookup, so
all of them have missed each other's audit row before any writes (the worst case).

    INFRX_D_TASK=g8 uv run --frozen pytest -q tests/g/ops/test_races_pg.py
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import JobState, TerminalCause, Usage
from infrx.state.journal import PgStreamStore

from . import pgworld
from .pgworld import R, admit_credit, drift, individual, needs_pg, settle, signup_rows

pytestmark = needs_pg
cc = pgworld.cc
WALLET = ("select ledger_total, reserved_total, available from infrx.credit_wallets "
          "where owner_user_id = %s")


def run(coro):
    return asyncio.run(coro)


class Lockstep:
    """The audit port, with every idempotency lookup held until `parties` have looked."""

    def __init__(self, audit, parties: int) -> None:
        self.audit, self.barrier = audit, asyncio.Barrier(parties)

    async def by_idempotency_key(self, key):
        found = await self.audit.by_idempotency_key(key)
        if found is None:
            await asyncio.wait_for(self.barrier.wait(), 10)
        return found

    async def append(self, entry):
        return await self.audit.append(entry)


def audited(w, key: str) -> int:
    return w.one("select count(*) from infrx.audit_entries where idempotency_key = %s", (key,))


def test_credit_grant__simultaneous_grants_and_a_lost_ack_land_one_entitlement():
    """A console callback, its retry racing it under the SAME key, an operator grant under
    another key and the console's own claim, all at once: one +10000 entry, every answer the
    same wallet, exactly one of them the grant (the rest replays), one audit row per key -
    and the lost acknowledgement's retry answers the recorded result. Oracle: the losing
    same-key call surfaced a raw unique-violation `Conflict` (23505) instead of the answer
    its twin recorded."""
    w = pgworld.world("g8_grant_race")
    user = individual(w)
    w.ops.audit = Lockstep(w.ops.audit, parties=3)

    async def go():
        op = await w.ops.operator(w.operator_secret)
        console = asyncio.to_thread(lambda: w.owner.execute(
            "select status, wallet_id::text from public.claim_signup_grant(%s, 'launch', null)",
            (user,)).fetchone())
        return await asyncio.gather(
            op.grant_initial(user, idempotency_key="callback-1", reason=R),
            op.grant_initial(user, idempotency_key="callback-1", reason=R),
            op.grant_initial(user, idempotency_key="operator-2", reason=R), console)
    *answers, console = run(go())
    wallets = {a["wallet_id"] for a in answers} | {console[1]}
    assert len(wallets) == 1, (answers, console)
    fresh = [a for a in answers if not a["replayed"]] + ([console] if console[0] == "granted"
                                                         else [])
    assert len(fresh) == 1, (answers, console)
    assert answers[0] == answers[1], "the same key answered two different results"
    assert signup_rows(w, user) == 1
    assert (audited(w, "callback-1"), audited(w, "operator-2")) == (1, 1)

    async def lost_ack():                                   # the response never arrived
        op = await w.ops.operator(w.operator_secret)
        return await op.grant_initial(user, idempotency_key="callback-1", reason=R)
    assert run(lost_ack()) == answers[0] and signup_rows(w, user) == 1


def unknown_usage_job(w, idem_key: str):
    """A CREDIT job whose output was published and whose usage is unknown (the client left
    after publication): its hold stays reserved until the 24 h reconciliation."""
    async def go():
        request, _ = await admit_credit(w, idem_key)
        store = pgworld.jobs(w)
        await store.prepared(await store.claim_preparation(request.request_id, "prep"))
        lease = await store.claim(request.request_id, "worker")
        await PgStreamStore(w.connect()).append(lease, b.events("published"))
        outcome, _ = await store.complete_credit(lease, b.outcome(
            request.request_id, pgworld.clocked(w), cause=TerminalCause.client_disconnected,
            state=JobState.failed, tokens=None, result_ref=None))
        return request, outcome
    return run(go())


def test_credit_spend__repeated_and_concurrent_reconciliation_answers_once_and_never_debits():
    """Unknown usage: refused before its interval (typed, nothing written); then two
    same-key reconciliations and a third under another key at once all answer
    `released_platform_absorbed`, the key audited once, the hold released exactly once,
    no debit, no drift. Oracle: the same-key loser surfaced a raw unique-violation Conflict,
    or a double release moved the reserved total twice."""
    w = pgworld.world("g8_reconcile_race")
    org = pgworld.personal_org(w, cc.CONSUMER_1)
    before = w.one(WALLET, (cc.CONSUMER_1,))
    request, outcome = unknown_usage_job(w, "unknown-1")
    assert outcome.settlement_state.value == "held_unknown"
    held = w.one(WALLET, (cc.CONSUMER_1,))
    assert held[1] > before[1]

    async def early():
        op = await w.ops.operator(w.operator_secret)
        with pytest.raises(errors.StateConflict):
            await op.reconcile(org, request.request_id, idempotency_key="rc-early", reason=R)
    run(early())
    assert audited(w, "rc-early") == 0 and w.one(WALLET, (cc.CONSUMER_1,)) == held
    w.owner.execute("select infrx_test.advance(%s)", (DEFAULTS.unknown_usage_reconcile_s,))
    w.ops.audit = Lockstep(w.ops.audit, parties=3)

    async def race():
        op = await w.ops.operator(w.operator_secret)
        return await asyncio.gather(*(op.reconcile(org, request.request_id,
                                                   idempotency_key=key, reason=R)
                                      for key in ("rc-1", "rc-1", "rc-2")))
    answers = run(race())
    assert {a["settlement"] for a in answers} == {"released_platform_absorbed"}, answers
    assert (audited(w, "rc-1"), audited(w, "rc-2")) == (1, 1)
    assert w.one(WALLET, (cc.CONSUMER_1,)) == before, "a reconciliation moved money"
    assert w.one("select count(*) from infrx.credit_ledger where request_id = %s",
                 (request.request_id,)) == 0
    assert drift(w) == []


def test_credit_spend__refusals_are_typed_and_leave_the_money_where_it_was():
    """Zero/insufficient funds, an adjustment below the reserved total, a suspended org
    (new work refused, reads kept - R33), a revoked key, a job past its deadline and a
    cancel (no usage, no debit - R106): each is a typed refusal or outcome, and afterwards
    the wallet equals its grant plus the operator's own adjustments, nothing reserved, no
    drift. Oracle: a refusal that half-wrote (a hold, a debit, a ledger row) moves a total."""
    w = pgworld.world("g8_refusals")
    user = cc.CONSUMER_1
    org = pgworld.personal_org(w, user)

    async def go():
        op = await w.ops.operator(w.operator_secret)
        store = pgworld.jobs(w)
        key = await op.issue_key(user, "k", idempotency_key="k", reason=R)
        await op.adjust(user, "-9999.99000000", idempotency_key="drain", reason=R)
        with pytest.raises(errors.InsufficientCredit):
            await admit_credit(w, "poor")
        await op.adjust(user, "99.99000000", idempotency_key="refill", reason=R)
        request, admitted = await admit_credit(w, "held")
        with pytest.raises(errors.InvalidRequest):          # below the live hold
            await op.adjust(user, "-99.00000000", idempotency_key="below", reason=R)
        cancelled = await op.cancel_job(org, admitted.job_handle, idempotency_key="c", reason=R)
        late, _ = await admit_credit(w, "late")
        w.owner.execute("select infrx_test.advance(%s)", (3600,))
        reaped = await store.recover()
        await op.set_suspension(org, "abuse", idempotency_key="s", reason=R)
        with pytest.raises(errors.OrgSuspended):
            await admit_credit(w, "suspended")
        with pytest.raises(errors.OrgSuspended):
            await op.issue_key(user, "k2", idempotency_key="k2", reason=R)
        statement = await (await w.ops.tenant(key.secret)).statement()   # reads kept (R33)
        await op.revoke_key(org, key.key_id, idempotency_key="rv", reason=R)
        with pytest.raises(errors.InvalidApiKey):
            await w.ops.tenant(key.secret)
        return cancelled, late, reaped, statement
    cancelled, late, reaped, statement = run(go())
    assert (cancelled["state"], cancelled["cause"]) == ("cancelled", "client_cancelled")
    ended = {o.job_id: o for o in reaped if hasattr(o, "settlement_state")}
    assert ended[late.request_id].debit == 0, ended
    total = Decimal("10000") - Decimal("9999.99") + Decimal("99.99")
    assert w.one(WALLET, (user,)) == (total, 0, total)
    assert statement["credit"]["ledger_total"] == f"{total:.8f}" and statement["holds"] == []
    assert statement["usage_totals"] == {"CREDIT": "0.00000000"} or \
        statement["usage_totals"] == {}, statement["usage_totals"]
    assert w.one("select count(*) from infrx.credit_ledger l join infrx.credit_wallets c "
                 "using (wallet_id) where c.owner_user_id = %s and l.kind = 'inference_debit'",
                 (user,)) == 0
    assert drift(w) == []


def test_credit_spend__concurrent_spend_and_adjustments_reconcile_exactly():
    """Eight jobs of one wallet, admitted, settled or cancelled concurrently on separate
    connections, beside concurrent operator adjustments: the ledger total is exactly the
    grant plus the adjustments minus the settled charges, one debit per settled job,
    nothing left reserved, the CREDIT usage total equals the debits, and both
    reconciliation views are clean. Oracle: a lost update of the wallet row, a second
    settlement or a hold that is never released breaks one of the equalities."""
    w = pgworld.world("g8_spend_race")
    user = cc.CONSUMER_2
    org = pgworld.personal_org(w, user)
    key = pgworld.checks_admission.C2_KEY

    async def job(n: int):
        request, admitted = await admit_credit(w, f"spend-{n}", user=user, key=key)
        if n % 3 == 0:
            outcome = await pgworld.jobs(w).cancel(org, admitted.job_handle)
            return request.request_id, None, outcome
        _, _, (outcome, settlement) = await settle(
            w, request, "credit", tokens=Usage.of(1000 + 37 * n, 100 + n))
        return request.request_id, settlement, outcome

    async def go():
        op = await w.ops.operator(w.operator_secret)
        adjust = [op.adjust(user, amount, idempotency_key=f"adj-{i}", reason=R)
                  for i, amount in enumerate(("1.25000000", "-0.50000000", "3.00000000"))]
        return await asyncio.gather(*(job(n) for n in range(8)), *adjust)
    results = run(go())
    jobs_done, adjusted = results[:8], results[8:]
    charged = sum((s.charged.value if hasattr(s.charged, "value") else Decimal(str(s.charged))
                   for _, s, _ in jobs_done if s is not None), Decimal(0))
    settled = [rid for rid, s, _ in jobs_done if s is not None]
    assert len(settled) == 5 and charged > 0
    total = Decimal("10000") + Decimal("3.75") - charged
    assert w.one(WALLET, (user,)) == (total, 0, total), (w.one(WALLET, (user,)), total)
    debits = w.owner.execute(
        "select request_id::text, -amount from infrx.credit_ledger where kind = "
        "'inference_debit' and request_id = any(%s::uuid[])", (settled,)).fetchall()
    assert sorted(r for r, _ in debits) == sorted(settled)
    assert sum(a for _, a in debits) == charged
    usage = w.one("select sum(charged_credits) from public.usage_events where org_id = %s "
                  "and accounting_regime = 'credit'", (org,))
    assert usage == charged
    assert len(adjusted) == 3 and drift(w) == []
