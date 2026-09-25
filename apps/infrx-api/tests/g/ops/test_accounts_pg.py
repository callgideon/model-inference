"""G8 point 1: the trusted headless account operations on a REAL PostgreSQL.

CREDIT-GRANT / CREDIT-IDENTITY / API-OPS through the operator tool's own composition
(`cli.build_operations`): a fresh verified individual is onboarded once, resolved to the
personal organization it created (never its first membership), keyed, and read back as
exact CREDIT strings; every operator write is audited under its own 0009 action.

    INFRX_D_TASK=g8 uv run --frozen pytest -q tests/g/ops/test_accounts_pg.py
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.operations import cli, service

from . import pgworld
from .pgworld import R, individual, needs_pg, personal_org, signup_rows

pytestmark = needs_pg
GRANT = "10000.00000000"
ZERO = "0.00000000"


def run(coro):
    return asyncio.run(coro)


def fresh_credit(wallet_id: str, total: str = GRANT, reserved: str = ZERO,
                 available: str = GRANT, spent: str = ZERO) -> dict:
    return {"wallet_id": wallet_id, "kind": "consumer", "unit": "CREDIT", "ledger_total": total,
            "reserved_total": reserved, "available": available, "spent": spent,
            "spent_complete": True}


def test_credit_grant__a_fresh_verified_individual_is_onboarded_once_with_exact_credit(tmp_path,
                                                                                    capsys):
    """Onboarding with no frontend: grant, key, and the trusted reads agree exactly -
    the operator's `account` and the individual's own `statement` show the same wallet,
    10000.00000000 CREDIT available, nothing reserved or spent. Oracle: a DTO that read a
    rounded, float or unit-less figure, or another org's wallet, differs from the literal."""
    w = pgworld.world("g8_onboard")
    user = individual(w)
    org = personal_org(w, user)

    async def go():
        op = await w.ops.operator(w.operator_secret)
        with pytest.raises(errors.NotFound):             # no wallet yet, so no key (G6B)
            await op.issue_key(user, "early", idempotency_key="k0", reason=R)
        before = await op.account(user)
        assert before["credit"] == {"wallet_id": None, "unit": "CREDIT", "spent": ZERO,
                                    "spent_complete": True}, before
        granted = await op.grant_initial(user, idempotency_key="g1", reason=R)
        assert (granted["amount"], granted["replayed"]) == (GRANT, False), granted
        issued = await op.issue_key(user, "sweep", idempotency_key="k1", reason=R)
        account = await op.account(user)
        statement = await (await w.ops.tenant(issued.secret)).statement()
        return granted, issued, account, statement

    granted, issued, account, statement = run(go())
    assert account == {"user_id": user, "personal_org_id": org, "suspension": None,
                       "verification_evidence_ref": account["verification_evidence_ref"],
                       "credit": fresh_credit(granted["wallet_id"]), "holds": [],
                       "usage_totals": {}}, account
    assert account["verification_evidence_ref"].startswith("email_confirmed_at/")
    assert statement == {"org_id": org, "user_id": user, "credit": account["credit"],
                         "holds": [], "usage_totals": {}}, statement
    assert issued.org_id == org and signup_rows(w, user) == 1
    # The same reads through the CLI: JSON, the key file never printed.
    env = {cli.OPERATOR_KEY_ENV: w.operator_secret}
    assert cli.main(["account", "--user", user], ops=w.ops, environ=env) == 0
    assert json.loads(capsys.readouterr().out) == account
    key_file = tmp_path / "sweep.key"
    key_file.write_text(issued.secret + "\n")
    assert cli.main(["statement", "--key-file", str(key_file)], ops=w.ops, environ={}) == 0
    out = capsys.readouterr().out
    assert json.loads(out) == statement and issued.secret[9:] not in out


def test_credit_grant__the_entitlement_is_per_individual_across_callbacks_and_org_changes():
    """CREDIT-GRANT / R71: the console's callback (and its retry, and a new campaign), the
    operator's grant under new idempotency keys, a second organization the individual
    creates and a membership elsewhere all land on ONE +10000 entry in ONE wallet bound to
    the first personal org. Oracle: any path keyed by org, campaign or operation id would
    mint a second entry or bind a second wallet."""
    w = pgworld.world("g8_entitle")
    user = individual(w)
    org = personal_org(w, user)
    op_id = str(uuid.uuid4())
    first = w.owner.execute("select status, wallet_id::text from public.claim_signup_grant("
                            "%s, 'launch_2026_09', %s)", (user, op_id)).fetchone()
    assert first[0] == "granted", first
    for campaign, retry_op in (("launch_2026_09", op_id), ("relaunch", None)):
        again = w.owner.execute("select status, wallet_id::text from public.claim_signup_grant("
                                "%s, %s, %s)", (user, campaign, retry_op)).fetchone()
        assert again == ("replayed", first[1]), again
    # Organization changes: a second org of their own, and a membership in a legacy org.
    side = str(uuid.uuid4())
    w.owner.execute("insert into public.organizations (id, name, slug, created_by) values "
                    "(%s, 'side', %s, %s)", (side, f"side-{side[:8]}", user))
    w.owner.execute("insert into public.org_members (org_id, user_id, role) values (%s, %s, "
                    "'owner'), (%s, %s, 'member')", (side, user, b.ORG_A, user))

    async def go():
        op = await w.ops.operator(w.operator_secret)
        answers = [await op.grant_initial(user, idempotency_key=key, reason=R)
                   for key in ("g-new-1", "g-new-2")]
        return answers, await op.account(user), await op.issue_key(user, "k", idempotency_key="k",
                                                                    reason=R)
    answers, account, issued = run(go())
    assert all(a["replayed"] and a["wallet_id"] == first[1] for a in answers), answers
    assert signup_rows(w, user) == 1
    assert w.one("select count(*) from infrx.signup_entitlements where user_id = %s",
                 (user,)) == 1
    assert w.one("select count(*) from infrx.credit_wallets where owner_user_id = %s",
                 (user,)) == 1
    assert account["personal_org_id"] == org and issued.org_id == org
    assert account["credit"] == fresh_credit(first[1]), account


def test_credit_identity__the_account_is_the_personal_org_not_the_first_membership():
    """RV-06's shape, headless: an individual whose FIRST membership (by time) is a legacy
    shared org still resolves to the organization they created. A key filed under that
    membership org cannot read or spend the personal wallet (R66). Oracle: resolving by
    first membership would bind the wallet and issue the key into the legacy org."""
    w = pgworld.world("g8_identity")
    user = individual(w)
    org = personal_org(w, user)
    w.owner.execute("insert into public.org_members (org_id, user_id, role, created_at) values "
                    "(%s, %s, 'member', '2000-01-01T00:00:00Z')", (b.ORG_A, user))
    first_membership = w.one("select org_id::text from public.org_members where user_id = %s "
                             "order by created_at limit 1", (user,))
    assert first_membership == b.ORG_A != org          # the scenario discriminates
    stray = service.new_secret()
    w.owner.execute("insert into public.api_keys (org_id, created_by, name, prefix, key_hash, "
                    "audience, user_id) values (%s, %s, 'legacy', %s, %s, 'consumer', %s)",
                    (b.ORG_A, user, stray[:service.PREFIX_CHARS], service.hash_key(stray), user))

    async def go():
        op = await w.ops.operator(w.operator_secret)
        granted = await op.grant_initial(user, idempotency_key="g", reason=R)
        issued = await op.issue_key(user, "k", idempotency_key="k", reason=R)
        account = await op.account(user)
        own = await (await w.ops.tenant(issued.secret)).statement()
        with pytest.raises(errors.Forbidden):
            await (await w.ops.tenant(stray)).statement()
        return granted, issued, account, own
    granted, issued, account, own = run(go())
    assert w.one("select personal_org_id::text from infrx.credit_wallets where wallet_id = %s",
                 (granted["wallet_id"],)) == org
    assert account["personal_org_id"] == org and issued.org_id == org
    assert own["org_id"] == org and own["credit"]["wallet_id"] == granted["wallet_id"]


def test_credit_identity__provider_development_allocation_is_separate_and_starts_at_zero():
    """02-credits: a provider_dev wallet is the provider organization's, starts at zero and
    is funded only by an operator allocation; the individual's signup grant, its replay and
    an operator adjustment never reach it, and a provider role adds no second grant.
    Oracle: a grant or adjustment routed by membership would move the dev wallet."""
    w = pgworld.world("g8_provider")
    user = individual(w)
    provider, dev_wallet = str(uuid.uuid4()), str(uuid.uuid4())
    w.owner.execute("insert into infrx.provider_orgs (provider_org_id, slug, display_name, "
                    "created_by) values (%s, %s, 'Lab', 'ops')", (provider, f"lab-{provider[:8]}"))
    w.owner.execute("insert into infrx.provider_memberships (provider_org_id, user_id, role, "
                    "granted_by) values (%s, %s, 'developer', 'ops@infrx')", (provider, user))
    w.owner.execute("insert into infrx.credit_wallets (wallet_id, kind, owner_provider_org_id) "
                    "values (%s, 'provider_dev', %s)", (dev_wallet, provider))

    def dev():
        return w.owner.execute("select ledger_total::text, reserved_total::text, "
                               "(select count(*) from infrx.credit_ledger where wallet_id = %s) "
                               "from infrx.credit_wallets where wallet_id = %s",
                               (dev_wallet, dev_wallet)).fetchone()
    assert dev() == (ZERO, ZERO, 0)

    async def go():
        op = await w.ops.operator(w.operator_secret)
        await op.grant_initial(user, idempotency_key="g1", reason=R)
        await op.grant_initial(user, idempotency_key="g2", reason=R)
        await op.adjust(user, "5.00000000", idempotency_key="a1", reason=R)
        return await op.account(user)
    account = run(go())
    assert dev() == (ZERO, ZERO, 0), "the individual's money reached the provider dev wallet"
    assert account["credit"]["ledger_total"] == "10005.00000000"
    assert signup_rows(w, user) == 1
    refused = pgworld.cc.attempt(
        w.owner, "insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, "
                 "operation_id, actor) values (%s, 'provider_dev', 'signup_grant', 10000, "
                 "gen_random_uuid(), 'platform')", (dev_wallet,))
    assert refused is not None and refused.startswith("23514"), refused


def test_api_ops__every_operator_write_is_audited_under_its_own_action():
    """G8 on 0009's vocabulary: one audit row per operator write, under the action that
    names it - a key issue is `admin_key_issue`, an adjustment `admin_adjust`, a
    cancellation `admin_job_cancel`, never a borrowed `admin_set_entitlements`/`admin_grant`.
    Oracle: G6B's interim mapping filed four kinds of write under two unrelated actions."""
    w = pgworld.world("g8_audit")
    user = individual(w)
    org = personal_org(w, user)

    async def go():
        op = await w.ops.operator(w.operator_secret)
        await op.grant_initial(user, idempotency_key="a-grant", reason=R)
        issued = await op.issue_key(user, "k", idempotency_key="a-issue", reason=R)
        await op.adjust(user, "1.00000000", idempotency_key="a-adjust", reason=R)
        await op.set_suspension(org, "abuse", idempotency_key="a-suspend", reason=R)
        await op.set_suspension(org, None, idempotency_key="a-lift", reason=R)
        await op.rotate_key(org, issued.key_id, "k2", idempotency_key="a-rotate", reason=R)
    run(go())
    rows = dict(w.owner.execute(
        "select idempotency_key, action from infrx.audit_entries where idempotency_key like 'a-%%'"
    ).fetchall())
    assert rows == {"a-grant": "admin_grant", "a-issue": "admin_key_issue",
                    "a-adjust": "admin_adjust", "a-suspend": "admin_set_suspension",
                    "a-lift": "admin_set_suspension", "a-rotate:issue": "admin_key_issue",
                    "a-rotate:revoke": "admin_key_revoke"}, rows
