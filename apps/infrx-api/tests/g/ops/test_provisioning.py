"""G6B.a: operator provisioning over the shared ports, with no frontend process.

API-OPS, API-AUTH and CREDIT-IDENTITY against the fakes in `fakes.py`. Every case
name is claimed by a mutant in `mutants.py` (R32/R40).
"""
from __future__ import annotations

import asyncio
import dataclasses
import pathlib

import pytest

from infrx.contracts import errors
from infrx.contracts.v2.records import CredentialAudience, INITIAL_SIGNUP_GRANT
from infrx.operations import service

from . import fakes
from .fakes import ORG_A, ORG_B, USER_A, USER_B, USER_C, UNVERIFIED

R = "support ticket 42"


def run(coro):
    return asyncio.run(coro)


async def provision(w, user=USER_A, key="k1"):
    op = await w.ops.operator(w.operator_secret)
    await op.grant_initial(user, idempotency_key=f"grant-{user}", reason=R)
    return op, await op.issue_key(user, "sweep", idempotency_key=key, reason=R)


def test_api_ops__a_verified_individual_is_granted_keyed_and_reads_its_balance():
    w = fakes.world()

    async def go():
        op, issued = await provision(w)
        tenant = await w.ops.tenant(issued.secret)
        balance = await tenant.balance()
        assert tenant.auth.org_id == ORG_A and tenant.auth.user_id == USER_A
        assert tenant.auth.audience is CredentialAudience.consumer
        assert balance.available == INITIAL_SIGNUP_GRANT
        assert [e.action for e in w.audit.entries] == ["admin_grant", "admin_set_entitlements"]
        assert all(e.actor_principal == fakes.OPERATOR_KEY for e in w.audit.entries)
    run(go())


def test_api_ops__the_secret_is_stored_only_as_its_hash_and_never_audited():
    w = fakes.world()

    async def go():
        _, issued = await provision(w)
        row = w.tenants.keys[issued.key_id]
        assert row.key_hash == service.hash_key(issued.secret) != issued.secret
        assert issued.secret.startswith(service.KEY_PREFIX)
        assert len(issued.secret) == len(service.KEY_PREFIX) + service.KEY_BODY
        assert row.prefix == issued.secret[:service.PREFIX_CHARS]
        body = issued.secret[len(service.KEY_PREFIX):]
        assert body not in repr(w.audit.entries) and body not in repr(row)
        assert body not in repr(issued)
    run(go())


def test_api_ops__the_secret_is_revealed_once():
    w = fakes.world()

    async def go():
        op, first = await provision(w)
        again = await op.issue_key(USER_A, "sweep", idempotency_key="k1", reason=R)
        assert first.secret and again.secret is None and again.replayed
        assert again.key_id == first.key_id
        assert len([k for k in w.tenants.keys.values() if k.org_id == ORG_A]) == 1
        # A crash after the row was written and before the audit: the replay finds the
        # row by its deterministic id and still does not reveal a second secret.
        w.audit.fail_next = True
        with pytest.raises(errors.DependencyUnavailable):
            await op.issue_key(USER_A, "ci", idempotency_key="k2", reason=R)
        crashed = await op.issue_key(USER_A, "ci", idempotency_key="k2", reason=R)
        assert crashed.secret is None and crashed.replayed
        assert len([k for k in w.tenants.keys.values() if k.org_id == ORG_A]) == 2
    run(go())


def test_api_auth__a_forged_operator_is_refused():
    w = fakes.world()

    async def go():
        _, issued = await provision(w)
        with pytest.raises(errors.Forbidden):
            await w.ops.operator(issued.secret)            # a consumer key
        with pytest.raises(errors.InvalidApiKey):
            await w.ops.operator(service.new_secret())     # a well-formed unknown key
        with pytest.raises(errors.InvalidApiKey):
            await w.ops.operator("")
        # The operator's own row names another audience and the tenant path refuses it.
        with pytest.raises(errors.Forbidden):
            await w.ops.tenant(w.operator_secret)
    run(go())


def test_api_auth__a_revoked_operator_key_is_refused():
    w = fakes.world()

    async def go():
        op = await w.ops.operator(w.operator_secret)
        await op.revoke_key(fakes.OPERATOR_ORG, fakes.OPERATOR_KEY, idempotency_key="r", reason=R)
        with pytest.raises(errors.InvalidApiKey):
            await w.ops.operator(w.operator_secret)
    run(go())


def test_api_auth__a_revoked_key_reads_nothing():
    w = fakes.world()

    async def go():
        op, issued = await provision(w)
        await op.revoke_key(ORG_A, issued.key_id, idempotency_key="rv", reason=R)
        first = w.tenants.keys[issued.key_id].revoked_at
        fakes.later(w.clock, seconds=5)
        await op.revoke_key(ORG_A, issued.key_id, idempotency_key="rv2", reason=R)
        assert w.tenants.keys[issued.key_id].revoked_at == first     # one-way
        with pytest.raises(errors.InvalidApiKey):
            await w.ops.tenant(issued.secret)
    run(go())


def test_api_auth__rotation_issues_before_it_revokes():
    w = fakes.world()

    async def go():
        op, old = await provision(w)
        new = await op.rotate_key(ORG_A, old.key_id, "sweep-2", idempotency_key="rot", reason=R)
        assert new.secret and new.key_id != old.key_id and new.org_id == ORG_A
        assert (await w.ops.tenant(new.secret)).auth.user_id == USER_A
        with pytest.raises(errors.InvalidApiKey):
            await w.ops.tenant(old.secret)
        ops_logged = [e.after["operation"] for e in w.audit.entries]
        assert ops_logged[-2:] == ["key_issue", "key_revoke"]
    run(go())


def test_api_auth__a_foreign_tenant_key_is_not_found_through_another_org():
    w = fakes.world()

    async def go():
        op, issued = await provision(w)
        with pytest.raises(errors.NotFound):
            await op.revoke_key(ORG_B, issued.key_id, idempotency_key="x", reason=R)
        with pytest.raises(errors.NotFound):
            await op.rotate_key(ORG_B, issued.key_id, "n", idempotency_key="y", reason=R)
        assert (await w.ops.tenant(issued.secret)).auth.org_id == ORG_A
        assert w.tenants.keys[issued.key_id].revoked_at is None
        # A legacy key of A's in a shared org B: rotation would mint A a key into an
        # organization that is not A's personal org.
        legacy = dataclasses.replace(w.tenants.keys[issued.key_id], key_id=fakes.USER_B[:-1] + "9",
                                     org_id=ORG_B, key_hash="0" * 64)
        w.tenants.keys[legacy.key_id] = legacy
        with pytest.raises(errors.Forbidden):
            await op.rotate_key(ORG_B, legacy.key_id, "n", idempotency_key="z", reason=R)
        assert [k for k in w.tenants.keys.values() if k.org_id == ORG_B] == [legacy]
    run(go())


def test_credit_identity__a_wallet_bound_to_another_org_is_refused():
    w = fakes.world()

    async def go():
        op = await w.ops.operator(w.operator_secret)
        with pytest.raises(errors.Forbidden):
            await op.issue_key(USER_C, "n", idempotency_key="c1", reason=R)
        with pytest.raises(errors.Forbidden):
            await op.adjust(USER_C, "5", idempotency_key="c2", reason=R)
        with pytest.raises(errors.Forbidden):
            await op.grant_initial(USER_C, idempotency_key="c3", reason=R)
        assert w.ledger.entries == {} and w.audit.entries == []
    run(go())


def test_credit_identity__an_unverified_user_gets_no_grant_and_no_key():
    w = fakes.world()

    async def go():
        op = await w.ops.operator(w.operator_secret)
        for call in (op.grant_initial(UNVERIFIED, idempotency_key="u1", reason=R),
                     op.issue_key(UNVERIFIED, "n", idempotency_key="u2", reason=R)):
            with pytest.raises(errors.NotFound):
                await call
        assert w.ledger.entries == {} and w.tenants.keys.keys() == {fakes.OPERATOR_KEY}
    run(go())


def test_credit_identity__no_key_without_a_metered_wallet():
    w = fakes.world()

    async def go():
        op = await w.ops.operator(w.operator_secret)
        with pytest.raises(errors.NotFound):
            await op.issue_key(USER_B, "n", idempotency_key="nw", reason=R)   # not granted yet
        assert w.tenants.keys.keys() == {fakes.OPERATOR_KEY}
    run(go())


def test_credit_identity__a_replayed_grant_is_deduplicated():
    w = fakes.world()

    async def go():
        op = await w.ops.operator(w.operator_secret)
        first = await op.grant_initial(USER_A, idempotency_key="g1", reason=R)
        again = await op.grant_initial(USER_A, idempotency_key="g1", reason=R)
        other = await op.grant_initial(USER_A, idempotency_key="g2-new-campaign", reason=R)
        assert first == again and not first["replayed"] and other["replayed"]
        assert len(w.ledger.entries) == 1
        assert w.ledger.total(USER_A) == INITIAL_SIGNUP_GRANT
        await op.grant_initial(USER_B, idempotency_key="g3", reason=R)
        assert w.ledger.total(USER_B) == INITIAL_SIGNUP_GRANT and len(w.ledger.entries) == 2
    run(go())


def test_api_ops__a_replayed_adjustment_is_deduplicated():
    w = fakes.world()

    async def go():
        op, _ = await provision(w)
        await op.adjust(USER_A, "25.5", idempotency_key="adj", reason=R)
        await op.adjust(USER_A, "25.5", idempotency_key="adj", reason=R)
        with pytest.raises(errors.IdempotencyConflict):
            await op.adjust(USER_A, "99", idempotency_key="adj", reason=R)
        # Crash between the ledger append and the audit row: the retry reaches the
        # ledger with the same operation id and appends nothing.
        w.audit.fail_next = True
        with pytest.raises(errors.DependencyUnavailable):
            await op.adjust(USER_A, "-0.5", idempotency_key="adj2", reason=R)
        await op.adjust(USER_A, "-0.5", idempotency_key="adj2", reason=R)
        assert w.ledger.total(USER_A) == INITIAL_SIGNUP_GRANT + service.Credit("25")
        assert len(w.ledger.entries) == 3
    run(go())


def test_api_ops__an_adjustment_moves_only_the_individuals_wallet():
    w = fakes.world()

    async def go():
        op, _ = await provision(w)
        await op.grant_initial(USER_B, idempotency_key="gb", reason=R)
        result = await op.adjust(USER_A, "5", idempotency_key="a5", reason=R)
        assert result["wallet_id"] == w.ledger.wallets.by_user[USER_A].wallet_id
        assert w.ledger.total(USER_B) == INITIAL_SIGNUP_GRANT
        assert w.ledger.total(USER_A) == INITIAL_SIGNUP_GRANT + service.Credit("5")
        entry = w.ledger.entries[result["entry_id"]]
        assert entry.actor == fakes.OPERATOR_KEY and entry.reason == R
        with pytest.raises(errors.InvalidRequest):
            await op.adjust(USER_A, "0", idempotency_key="a0", reason=R)
        for bad in ("1e3", "0.000000001", "five", 5.5):
            with pytest.raises(errors.InvalidRequest):
                await op.adjust(USER_A, bad, idempotency_key="ab", reason=R)
    run(go())


def test_api_ops__suspension_refuses_new_keys_and_keeps_prose_in_the_audit():
    w = fakes.world()

    async def go():
        op, _ = await provision(w)
        with pytest.raises(errors.InvalidRequest):
            await op.set_suspension(ORG_A, "because I said so", idempotency_key="s0", reason=R)
        await op.set_suspension(ORG_A, "abuse", idempotency_key="s1", reason="burst from 1.2.3.4")
        assert w.tenants.suspended == {ORG_A: "abuse"}
        entry = w.audit.entries[-1]
        assert entry.action == "admin_set_suspension" and entry.reason == "burst from 1.2.3.4"
        assert entry.before == {"suspension": None} and entry.target_org_id == ORG_A
        with pytest.raises(errors.OrgSuspended):
            await op.issue_key(USER_A, "n", idempotency_key="k9", reason=R)
        await op.set_suspension(ORG_A, None, idempotency_key="s2", reason=R)
        assert (await op.issue_key(USER_A, "n", idempotency_key="k9", reason=R)).secret
    run(go())


def test_api_ops__every_write_needs_a_reason_and_an_idempotency_key():
    w = fakes.world()

    async def go():
        op = await w.ops.operator(w.operator_secret)
        for kwargs in ({"idempotency_key": "", "reason": R},
                       {"idempotency_key": "x" * 256, "reason": R},
                       {"idempotency_key": "ok", "reason": "  "},
                       {"idempotency_key": "ok", "reason": "r" * 501}):
            with pytest.raises(errors.InvalidRequest):
                await op.grant_initial(USER_A, **kwargs)
        assert w.ledger.entries == {} and w.audit.entries == []
    run(go())


def test_api_ops__operations_never_touch_a_balance():
    """No direct balance edit: the package names neither wallet total, so credit can
    move only through the A1/D5 ledger port."""
    package = pathlib.Path(service.__file__).parent
    source = "".join(p.read_text() for p in sorted(package.glob("*.py")))
    for forbidden in ("ledger_total", "reserved_total", "delta_usd", "cost_usd"):
        assert forbidden not in source, forbidden
