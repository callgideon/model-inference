"""G8 point 1 on the port fakes: the trusted account and statement reads.

The same invariants `test_accounts_pg.py` proves on PostgreSQL, at the seam the mutants
in `mutants.py` edit (the PG file is a real-service suite outside the mutant runner).
"""
from __future__ import annotations

import asyncio

import pytest

from infrx.contracts import errors
from infrx.contracts.records import HoldState
from infrx.contracts.v2 import fixtures as v2fix
from infrx.contracts.v2 import records as v2
from infrx.contracts.v2.money_units import Credit
from infrx.operations import service
from infrx.operations.ports import HoldView

from . import fakes
from .fakes import ORG_A, ORG_B, USER_A, USER_B, USER_C

R = "support ticket 42"


def run(coro):
    return asyncio.run(coro)


def usage_rows(org: str, n: int) -> v2.UsageHistory:
    row = v2fix.BUILDERS["usage_credit.json"]()
    return v2.UsageHistory(org_id=org, entries=tuple(
        v2.UsageRecordV2(**{**row.model_dump(), "org_id": org}) for _ in range(n)))


def test_credit_identity__the_account_read_is_the_individuals_own_exact_credit():
    """`account` resolves the individual's personal org and wallet from the identity:
    exact CREDIT strings, its own holds and per-unit usage, the reserved amount inside
    `reserved_total`. A wallet bound elsewhere is Forbidden, never read. Oracle: an
    account read that skipped the binding (or keyed holds by another id) shows a
    foreign wallet or foreign holds."""
    w = fakes.world()

    async def go():
        op = await w.ops.operator(w.operator_secret)
        await op.grant_initial(USER_A, idempotency_key="ga", reason=R)
        await op.grant_initial(USER_B, idempotency_key="gb", reason=R)
        w.accounts.usage_by_org[ORG_A] = usage_rows(ORG_A, 2)
        w.accounts.holds_by_org[ORG_B] = (HoldView(v2fix.IDS.request, HoldState.held,
                                                   Credit("3.5")),)
        account = await op.account(USER_A)
        with pytest.raises(errors.Forbidden):
            await op.account(USER_C)            # C's wallet is bound to B's org
        with pytest.raises(errors.NotFound):
            await op.account(fakes.UNVERIFIED)
        return account
    account = run(go())
    charged = Credit(v2fix.BUILDERS["usage_credit.json"]().charged_amount)
    assert account == {
        "user_id": USER_A, "personal_org_id": ORG_A, "suspension": None,
        "verification_evidence_ref": f"email-verified:{USER_A}",
        "credit": {"wallet_id": service.stable_id("wallet", USER_A), "kind": "consumer",
                   "unit": "CREDIT", "ledger_total": "10000.00000000",
                   "reserved_total": "0.00000000", "available": "10000.00000000",
                   "spent": str(charged + charged), "spent_complete": True},
        "holds": [], "usage_totals": {"CREDIT": str(charged + charged)}}, account


def test_credit_spend__a_statement_never_reports_a_short_spend_as_complete():
    """`spent` is the settled CREDIT total; when the usage page is full some older rows
    may be missing, so the statement says so (None, incomplete) instead of a short sum.
    Oracle: a page-capped sum reported as complete understates what was spent."""
    w = fakes.world()

    async def go():
        op = await w.ops.operator(w.operator_secret)
        await op.grant_initial(USER_A, idempotency_key="ga", reason=R)
        key = await op.issue_key(USER_A, "k", idempotency_key="k", reason=R)
        tenant = await w.ops.tenant(key.secret)
        empty = await tenant.statement()
        w.accounts.usage_by_org[ORG_A] = usage_rows(ORG_A, service.USAGE_PAGE - 1)
        partial = await tenant.statement()
        w.accounts.usage_by_org[ORG_A] = usage_rows(ORG_A, service.USAGE_PAGE)
        return empty, partial, await tenant.statement()
    empty, partial, full = run(go())
    assert (empty["credit"]["spent"], empty["credit"]["spent_complete"]) == ("0.00000000", True)
    assert empty["usage_totals"] == {}                                   # R73
    assert partial["credit"]["spent_complete"] is True
    assert (full["credit"]["spent"], full["credit"]["spent_complete"]) == (None, False)
    assert empty["credit"]["unit"] == "CREDIT" and "legacy_usd" not in empty["credit"]
