"""L2 LAB-ACCESS at the privileged server entry point (`infrx.lab.access.LabAccess`).

The world (`worlds.py`) is two consumer organizations and two providers, with BOTH in both
products: the owner of CONSUMER_1's organization and a provider-A developer. Every case taking
`world` runs twice: on the fake of lab-sql's RPC seam and, marked `pg`, on `PgAccessStore` over
PostgreSQL with migrations 0001-0027 (`INFRX_D_TASK=l2`). The redaction case is fake-only (the
RPC cannot return an identity column; L2-SQL's own check pins its shape); the direct-DB-role
case is PostgreSQL-only.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import psycopg
import pytest

from infrx.contracts import errors
from infrx.contracts.v2 import records as v2
from tests.d import checks

from .worlds import CONTENT, FAR


def run(coro):
    return asyncio.run(coro)


def content(w, user, provider, grantor, purpose=v2.DataPurpose.provider_sharing):
    return run(w.access.authorize_content(user_id=user, provider_org_id=provider,
                                          grantor_org_id=grantor, model_id=w.MODELS[provider],
                                          category=CONTENT, purpose=purpose))


def revisions(w, user, provider):
    return [a.deployment_revision_id for a in run(w.access.aggregates(user, provider))]


def spaces(w, user):
    return [(s.membership.provider_org_id, s.provider_name)
            for s in run(w.access.workspaces(user))]


# --- the seam: cross-provider reads ---------------------------------------------
def test_lab_access__a_provider_member_never_reads_another_providers_data(world):
    """Oracle: provider B's developer reaches none of provider A's aggregates, grants or
    content - neither by naming A's org id (forged) nor through A's grantor."""
    w = world
    assert revisions(w, w.DEV_A, w.A) == w.REVISIONS[w.A] != []
    assert revisions(w, w.DEV_B, w.B) == w.REVISIONS[w.B]
    with pytest.raises(errors.NotFound):
        run(w.access.aggregates(w.DEV_B, w.A))
    with pytest.raises(errors.NotFound):
        run(w.access.grant_history(w.DEV_B, w.A, w.C1))
    with pytest.raises(errors.Forbidden):
        content(w, w.DEV_B, w.A, w.C1)
    with pytest.raises(errors.Forbidden):        # B's own workspace, A's grantor
        content(w, w.DEV_B, w.B, w.C1)
    assert content(w, w.DEV_A, w.A, w.C1).grantor_org_id == w.C1


# --- memberships: explicit, per provider, never from consumer ownership -----------
def test_lab_access__workspaces_are_explicit_provider_memberships_only(world):
    """Oracle (R156): a consumer-only user (the owner of a consumer organization) gets no
    workspace; the user in both products gets exactly their provider membership, not their
    consumer organization; each workspace carries its provider's display name."""
    w = world
    assert run(w.access.workspaces(w.CONSUMER_ONLY)) == ()
    assert spaces(w, w.BOTH) == [(w.A, w.NAMES[w.A])]
    assert spaces(w, w.DEV_B) == [(w.B, w.NAMES[w.B])]


def test_lab_access__a_revoked_membership_loses_its_workspace_at_once(world):
    """Oracle: the revocation instant ends selection, aggregates, history and content alike."""
    w = world
    assert content(w, w.DEV_A, w.A, w.C1)
    w.revoke_membership(w.A, w.DEV_A)
    assert run(w.access.workspaces(w.DEV_A)) == ()
    with pytest.raises(errors.NotFound):
        run(w.access.aggregates(w.DEV_A, w.A))
    with pytest.raises(errors.NotFound):
        run(w.access.grant_history(w.DEV_A, w.A, w.C1))
    with pytest.raises(errors.Forbidden):
        content(w, w.DEV_A, w.A, w.C1)


def test_lab_access__a_viewer_reads_aggregates_and_never_content_or_grants(world):
    """Oracle (0-F2): every role reads its own aggregates; content AND the grant history
    (which names the grantor's organization and scope) need developer+. The refusal does not
    depend on the grantor, so it tells a viewer nothing about who granted."""
    w = world
    assert revisions(w, w.VIEWER_A, w.A) == w.REVISIONS[w.A]
    with pytest.raises(errors.Forbidden):
        content(w, w.VIEWER_A, w.A, w.C1)
    for grantor in (w.C1, w.C2):
        with pytest.raises(errors.Forbidden):
            run(w.access.grant_history(w.VIEWER_A, w.A, grantor))


# --- the default read is redacted -------------------------------------------------
@pytest.mark.parametrize("column", ["user_id", "org_id", "api_key_id", "request_id"])
def test_lab_access__default_aggregates_carry_no_customer_identity(fake_world, column):
    """Oracle: an aggregate row carrying any identity column is refused, not passed on."""
    w = fake_world
    w.store.rows[w.A] = [{**w.store.rows[w.A][0], column: w.C1}]
    with pytest.raises(ValueError):
        run(w.access.aggregates(w.DEV_A, w.A))


# --- purpose separation, expiry, revocation mid-queue, the clock ---------------------
def test_lab_access__each_purpose_is_its_own_permission(world):
    """Oracle: a sharing grant is not training consent; a capture grant is not sharing."""
    w = world
    assert content(w, w.DEV_A, w.A, w.C1).purposes == (v2.DataPurpose.provider_sharing,)
    for purpose in (v2.DataPurpose.training, v2.DataPurpose.external_judging,
                    v2.DataPurpose.capture):
        with pytest.raises(errors.Forbidden):
            content(w, w.DEV_A, w.A, w.C1, purpose)
    assert content(w, w.DEV_B, w.B, w.C2, v2.DataPurpose.capture)
    with pytest.raises(errors.Forbidden):
        content(w, w.DEV_B, w.B, w.C2)


def test_lab_access__revocation_denies_queued_work_on_its_next_check(world):
    """Oracle: authorized at enqueue, revoked before the worker's use-time check -> denied;
    every check reads the current grant and the store clock, never a cached answer."""
    w = world
    assert content(w, w.DEV_A, w.A, w.C1)                        # enqueue-time check
    w.advance(60)
    w.revoke_grant(w.C1, w.A)
    with pytest.raises(errors.Forbidden):                        # use-time check
        content(w, w.DEV_A, w.A, w.C1)


def test_lab_access__an_expired_grant_denies(world):
    w = world
    w.put_grant(w.C1, w.A, v2.DataPurpose.provider_sharing, expires_in=timedelta(days=1))
    assert content(w, w.DEV_A, w.A, w.C1)
    w.advance(86_400)
    with pytest.raises(errors.Forbidden):
        content(w, w.DEV_A, w.A, w.C1)


def test_lab_access__revocation_and_grants_are_judged_on_the_store_clock(world):
    """Oracle (0-F1, R7/R79): the process clock plays no part. With the store clock years
    ahead of this process, a grant and a membership revoked at store time T are refused at T
    (the process clock has not reached T), and a grant written at T authorizes at T (the
    process clock sees it in its future)."""
    w = world
    w.freeze(FAR)
    w.revoke_grant(w.C1, w.A)
    w.revoke_membership(w.A, w.BOTH)
    with pytest.raises(errors.Forbidden):
        content(w, w.DEV_A, w.A, w.C1)
    assert run(w.access.workspaces(w.BOTH)) == ()
    w.put_grant(w.C1, w.A, v2.DataPurpose.provider_sharing)
    assert content(w, w.DEV_A, w.A, w.C1).effective_at == FAR == w.now()


# --- grant history: the C/J/T port -------------------------------------------------
def test_lab_access__grant_history_keeps_every_version_for_the_recipient_only(world):
    """Oracle: history names every version (source ids for C/J/T), the revocation on the
    store clock, and is scoped to the member's own provider; history never authorizes (a
    revoked version is listed and the content read is still refused)."""
    w = world
    w.advance(60)
    w.revoke_grant(w.C1, w.A)
    history = run(w.access.grant_history(w.DEV_A, w.A, w.C1))
    assert [(g.version, g.revoked_at) for g in history] == [(1, None), (2, w.now())]
    assert len({g.grant_id for g in history}) == 1
    assert run(w.access.grant_history(w.DEV_B, w.B, w.C1)) == ()
    with pytest.raises(errors.Forbidden):
        content(w, w.DEV_A, w.A, w.C1)


# --- direct DB roles (PostgreSQL only) ------------------------------------------------
@pytest.mark.pg
def test_lab_access_pg__a_foreign_member_is_refused_at_both_doors(pg_world):
    """Oracle (LAB-ACCESS, direct DB roles and the privileged entry point): no browser session
    (anonymous, DEV_B, the consumer-only owner, even BOTH, who reads A through the server)
    reads provider A's grants, memberships or aggregates from the database, by table or by
    RPC; the service role does, through the RPC; and at the server entry point DEV_B and the
    consumer-only owner are refused A's workspace while BOTH is served."""
    w, conn = pg_world, pg_world.conn
    pair = f'{{"grantor_org_id": "{w.C1}", "recipient_provider_org_id": "{w.A}"}}'
    probes = [f"select * from infrx.lab_access_grants where grantor_org_id = '{w.C1}'",
              f"select * from infrx.provider_memberships where provider_org_id = '{w.A}'",
              f"select infrx.lab_access_grants('{pair}'::jsonb)",
              f"select infrx.lab_provider_memberships('{{\"user_id\": \"{w.DEV_A}\"}}'::jsonb)",
              f"select infrx.lab_deployment_aggregates('{{\"provider_org_id\": \"{w.A}\"}}')"]
    sessions = {"anon": checks.SESSIONS["anon"],
                **{user: checks._jwt(user) for user in (w.DEV_B, w.CONSUMER_ONLY, w.BOTH)}}
    for session, setup in sessions.items():
        for sql in probes:
            with pytest.raises(psycopg.Error) as refused, conn.transaction():
                conn.execute(setup)
                conn.execute(sql)
            assert refused.value.sqlstate == "42501", (session, sql, str(refused.value))
    with conn.transaction():
        conn.execute(checks.SESSIONS["service"])
        assert len(conn.execute(probes[2]).fetchone()[0]) == 1
    with pytest.raises(errors.NotFound):
        run(w.access.aggregates(w.DEV_B, w.A))
    with pytest.raises(errors.NotFound):
        run(w.access.grant_history(w.CONSUMER_ONLY, w.A, w.C1))
    assert revisions(w, w.BOTH, w.A) == w.REVISIONS[w.A]
