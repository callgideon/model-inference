"""D5: the operator money operations (0018 `grant_credit`, `reconcile`) and the D5 privilege
surface as SQL-level checks, on D2's "admission" scenario (R32/R40: each is a migration
mutant's named check). Each check rolls back; every money path ends in `assert_no_drift`.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from infrx.contracts.limits import DEFAULTS

from . import checks_admission as ca
from . import checks_credit as cc
from . import checks_leases as cl
from .checks_dispatch import advance, call, outcome
from .checks_settle import (assert_no_drift, credit, credit_hold, propose, settle, stored,
                            usd)


def grant(conn, wallet, amount, *, kind: str = "operator_adjustment", op: str | None = None,
          actor: str = "ops@test", reason: str = "drift correction"):
    """(code, answer) of one `grant_credit` call, as `PgLedger.adjust` sends it."""
    return outcome(conn, "grant_credit", {
        "wallet_id": str(wallet), "kind": kind, "amount": amount,
        "operation_id": op or str(uuid.uuid4()), "actor": actor, "reason": reason,
        "at": "2099-01-01T00:00:00+00:00"})


def reconcile(conn, org, request_id, op: str = "rec-1", at: str = "2099-01-01T00:00:00+00:00"):
    return outcome(conn, "reconcile", {"org_id": str(org), "request_id": str(request_id),
                                       "operation_id": op, "actor": "ops@test", "at": at})


def wallet(conn, wallet_id) -> tuple[Decimal, Decimal]:
    return conn.execute("select ledger_total, reserved_total from infrx.credit_wallets "
                        "where wallet_id = %s", (str(wallet_id),)).fetchone()


def rows(conn, table: str) -> int:
    return conn.execute(f"select count(*) from {table}").fetchone()[0]


def audits(conn, key: str) -> list[tuple]:
    return conn.execute("select action, actor_principal, target_org_id, reason from "
                        "infrx.audit_entries where idempotency_key = %s", (key,)).fetchall()


def unknown_job(conn, world, worker: str = "wu", *, regime: str = "credit"):
    """A published job settled with no usage: `held_unknown`, the 24 h window from now."""
    running = cl.credit_running if regime == "credit" else cl.running
    request, lease = running(conn, world, worker=worker)
    cl.publish(conn, lease)
    code, doc = settle(conn, lease, propose(request.request_id, "client_disconnected",
                                            "failed"), regime)
    assert (code, doc["outcome"]["settlement_state"]) == (None, "held_unknown"), (code, doc)
    return request


# --------------------------------------------------------------------- item 4
def check_adjust(conn) -> str:
    """CREDIT-SPEND / API-OPS: an operator adjustment on a consumer wallet is one
    `operator_adjustment` row (signed, the total moved by its trigger) plus one
    `admin_adjust` audit row keyed `grant_credit:<operation>`, in one transaction. A replay
    answers the same entry `replayed` and appends nothing; the operation id reused for
    another movement is `idempotency_conflict`. A result below the wallet's reserved total
    is refused typed, nothing written; so is money that is not exact CREDIT text (R11: more
    than 8 places, 10^12, zero, a number, an exponent). A retired individual's frozen wallet
    still accepts an adjustment (A1 request 7)."""
    world = ca.World(conn)

    def body():
        request, _lease = cl.credit_running(conn, world)
        wid = cl.row(conn, request.request_id)["wallet_id"]
        org = cc.personal_org(conn, cc.CONSUMER_1)
        total, reserved = wallet(conn, wid)
        op = str(uuid.uuid4())
        code, answer = grant(conn, wid, "12.50000000", op=op)
        assert code is None and answer["replayed"] is False, (code, answer)
        entry = answer["entry"]
        assert (entry["kind"], Decimal(str(entry["amount"])), entry["operation_id"]) == \
            ("operator_adjustment", Decimal("12.5"), op), entry
        assert wallet(conn, wid) == (total + Decimal("12.5"), reserved), wallet(conn, wid)
        assert audits(conn, f"grant_credit:{op}") == \
            [("admin_adjust", "ops@test", uuid.UUID(org), "drift correction")], \
            audits(conn, f"grant_credit:{op}")
        ledger, audit = rows(conn, "infrx.credit_ledger"), rows(conn, "infrx.audit_entries")
        code, again = grant(conn, wid, "12.50000000", op=op)
        assert code is None, f"a replayed adjustment was refused: {code}"
        assert (again["replayed"], again["entry"]) == (True, entry), again
        assert (rows(conn, "infrx.credit_ledger"), rows(conn, "infrx.audit_entries")) == \
            (ledger, audit), "a replay appended"
        # the same operation id for ANOTHER movement - amount, wallet or kind - is a
        # conflict, and nothing moves anywhere (review B1: each part is its own mutant)
        other_wallet = cc.wallet_of(conn, cc.CONSUMER_2)
        other_before = wallet(conn, other_wallet)
        for label, answer in (
                ("another amount", grant(conn, wid, "13.00000000", op=op)),
                ("another wallet", grant(conn, other_wallet, "12.50000000", op=op)),
                ("another kind", grant(conn, wid, "12.50000000", kind="operator_allocation",
                                       op=op))):
            assert answer[0] == "idempotency_conflict", \
                f"the operation id reused for {label} answered {answer}"
        assert (rows(conn, "infrx.credit_ledger"), wallet(conn, other_wallet)) == \
            (ledger, other_before), "a conflicting reuse moved money"
        # below the reserved total: typed, nothing written
        total, reserved = wallet(conn, wid)
        below = f"{-(total - reserved) - Decimal('0.00000001'):f}"
        code, _ = grant(conn, wid, below)
        assert code == "invalid_request", f"an adjustment below the reserved total: {code}"
        assert wallet(conn, wid) == (total, reserved) and \
            rows(conn, "infrx.credit_ledger") == ledger, "a refused adjustment wrote"
        code, down = grant(conn, wid, f"{-(total - reserved):f}")
        assert code is None and wallet(conn, wid) == (reserved, reserved), (code, down)
        for amount in ("0", "0.00000000", "1.123456789", "1000000000000", "1e3", 5, "", None):
            assert grant(conn, wid, amount)[0] == "invalid_request", f"R11 accepted {amount!r}"
        assert rows(conn, "infrx.credit_ledger") == ledger + 1, "a refused amount wrote"
        # a frozen wallet still takes an adjustment (and D5's compensations)
        conn.execute("select infrx.retire_individual(%s, 'ops@test', 'deletion', 'r-adj')",
                     (cc.CONSUMER_1,))
        assert grant(conn, wid, "1.00000000")[0] is None, "a frozen wallet refused an adjustment"
        assert_no_drift(conn, "operator adjustments")
        return "audited, idempotent, never below reserved, R11-bounded, frozen-wallet ok"
    return ca._in_rollback(conn, body)


def check_allocation(conn) -> str:
    """0006's kind rules as typed refusals: an `operator_allocation` funds a provider_dev
    wallet only, and positively; an `operator_adjustment` corrects a consumer wallet only;
    an unknown wallet is `not_found`. Nothing refused writes."""
    def body():
        consumer = cc.wallet_of(conn, cc.CONSUMER_1)
        ledger = rows(conn, "infrx.credit_ledger")
        before = wallet(conn, cc.PROVIDER_WALLET)
        code, answer = grant(conn, cc.PROVIDER_WALLET, "100.00000000", kind="operator_allocation")
        assert code is None and answer["entry"]["kind"] == "operator_allocation", (code, answer)
        assert wallet(conn, cc.PROVIDER_WALLET)[0] == before[0] + 100
        for wid, kind, amount, label in (
                (consumer, "operator_allocation", "5.00000000", "an allocation to a consumer"),
                (cc.PROVIDER_WALLET, "operator_allocation", "-5.00000000",
                 "a negative allocation"),
                (cc.PROVIDER_WALLET, "operator_adjustment", "5.00000000",
                 "an adjustment to a provider_dev wallet"),
                (str(uuid.uuid4()), "operator_adjustment", "5.00000000", "no such wallet"),
                (consumer, "signup_grant", "10000.00000000", "a signup grant"),
                (consumer, "inference_debit", "-1.00000000", "a debit")):
            code, _ = grant(conn, wid, amount, kind=kind)
            want = "not_found" if label == "no such wallet" else "invalid_request"
            assert code == want, f"{label}: {code}"
        assert rows(conn, "infrx.credit_ledger") == ledger + 1, "a refused movement wrote"
        assert_no_drift(conn, "allocations")
        return "allocation to provider_dev only; adjustment to consumer only"
    return ca._in_rollback(conn, body)


def check_reconcile_clock(conn) -> str:
    """02 / R7: `reconcile` releases a `held_unknown` hold platform-absorbed only once the
    DATABASE clock is past `reconcile_after` - a caller's `at` years later is audit data -
    through D3's release (hold released on its own wallet, the ledger never moved, no late
    charge), audited `admin_reconcile`; a replay answers the same state and writes nothing;
    a job the reaper already released answers `released_platform_absorbed`; a job with no
    unknown usage is `state_conflict`."""
    world = ca.World(conn)

    def body():
        job = unknown_job(conn, world)
        legacy = unknown_job(conn, world, "wl", regime="legacy_usd")
        before = credit(conn, job.request_id)
        usd_before = usd(conn)
        advance(conn, DEFAULTS.unknown_usage_reconcile_s - 1)
        code, _ = reconcile(conn, job.org_id, job.request_id, at="2100-01-01T00:00:00+00:00")
        assert code == "state_conflict", f"released before the window on the DB clock: {code}"
        assert credit_hold(conn, job.request_id) == "unknown"
        advance(conn, 1)
        code, answer = reconcile(conn, job.org_id, job.request_id, op="rec-a")
        assert (code, answer) == (None, {"settlement_state": "released_platform_absorbed",
                                         "replayed": False}), (code, answer)
        assert credit_hold(conn, job.request_id) == "released"
        after = credit(conn, job.request_id)
        assert after[0] == before[0], "reconcile debited the CREDIT wallet"
        assert after[1] == before[1] - cl.row(conn, job.request_id)["maximum_hold"], after
        assert [a for a, *_ in audits(conn, "reconcile:rec-a")] == ["admin_reconcile"]
        audit = rows(conn, "infrx.audit_entries")
        code, again = reconcile(conn, job.org_id, job.request_id, op="rec-a")
        assert code is None, f"a replayed reconcile was refused: {code}"
        assert (again["replayed"], again["settlement_state"]) == \
            (True, "released_platform_absorbed"), again
        assert rows(conn, "infrx.audit_entries") == audit, "a replay audited again"
        # the reaper got there first: the same end state, answered
        call(conn, "recover", {"limits": cl.LIMITS})
        code, answer = reconcile(conn, legacy.org_id, legacy.request_id, op="rec-b")
        assert (code, answer["settlement_state"]) == (None, "released_platform_absorbed"), answer
        assert usd(conn)[0] == usd_before[0], "reconcile debited the USD wallet"
        settled, settled_lease = cl.running(conn, world, worker="ws")
        settle(conn, settled_lease, propose(settled.request_id, usage=(10, 1),
                                            ref=stored(conn, settled.request_id)))
        assert reconcile(conn, settled.org_id, settled.request_id, op="rec-c")[0] == \
            "state_conflict", "a settled job was reconciled"
        assert_no_drift(conn, "reconcile")
        return "released at the window on the DB clock, never a debit; audited; idempotent"
    return ca._in_rollback(conn, body)


def check_reconcile_tenant(conn) -> str:
    """R10: another organization's request and an unknown one are the same `not_found`, and
    nothing moves; a malformed call is `invalid_request`."""
    world = ca.World(conn)

    def body():
        job = unknown_job(conn, world)
        advance(conn, DEFAULTS.unknown_usage_reconcile_s)
        before = credit(conn, job.request_id)
        for org, request_id in ((cc.personal_org(conn, cc.CONSUMER_2), job.request_id),
                                (job.org_id, str(uuid.uuid4()))):
            code, _ = reconcile(conn, org, request_id, op=f"t-{org}")
            assert code == "not_found", f"{org}/{request_id}: {code}"
        assert credit(conn, job.request_id) == before and \
            credit_hold(conn, job.request_id) == "unknown", "a foreign reconcile moved"
        assert reconcile(conn, "not-a-uuid", job.request_id)[0] == "invalid_request"
        return "tenant-bound: a foreign request is not_found"
    return ca._in_rollback(conn, body)


# --------------------------------------------------------------------- item 10a
#: D5's boundary: the platform's operations, service_role only (0004's grants kept by
#: `create or replace`, the new ones granted), and the internal bodies nobody may call.
D5_SERVICE = ("infrx.terminalize(jsonb)", "infrx.cancel(jsonb)", "infrx.claim(jsonb)",
              "infrx.grant_credit(jsonb)", "infrx.reconcile(jsonb)",
              "infrx.load_work_credit(jsonb)", "infrx.idempotency_lookup(jsonb)",
              "infrx.job_admission(uuid)")
D5_INTERNAL = ("infrx.settle_legacy_usd(uuid,numeric)", "infrx.settle_credit(uuid,numeric)",
               "infrx.debit_legacy_usd(jsonb,integer,integer)",
               "infrx.debit_credit(text,integer,integer)", "infrx.usage_doc(integer,integer)",
               "infrx.cause_carries_state(text,text)", "infrx.jobs_settlement_record_guard()",
               "infrx.release_aged_unknown(uuid,timestamp with time zone)")


def check_d5_privileges(conn) -> str:
    """R59: every function 0018 defines or redefines is executable by `service_role` only
    (never PUBLIC, anon or authenticated), and its helpers by nobody - SECURITY DEFINER
    bodies and a trigger call them."""
    def may(role: str, fn: str) -> bool:
        return conn.execute("select has_function_privilege(%s, %s, 'execute')",
                            (role, fn)).fetchone()[0]
    for fn in D5_SERVICE:
        assert may("service_role", fn), f"service_role cannot execute {fn}"
        for role in ("anon", "authenticated"):
            assert not may(role, fn), f"{role} may execute {fn}"
        assert not conn.execute("select exists (select 1 from aclexplode(coalesce(proacl, "
                                "acldefault('f', proowner))) a where a.grantee = 0 and "
                                "a.privilege_type = 'EXECUTE') from pg_proc where oid = "
                                "%s::regprocedure", (fn,)).fetchone()[0], f"PUBLIC may execute {fn}"
    for fn in D5_INTERNAL:
        for role in ("service_role", "anon", "authenticated"):
            assert not may(role, fn), f"{role} may execute the internal {fn}"
    return f"{len(D5_SERVICE)} service operations, {len(D5_INTERNAL)} internal functions"
