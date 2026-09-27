"""L2 LAB-ACCESS at the privileged server entry point (`infrx.lab.access.LabAccess`).

The world is two consumers and two providers, with one user who belongs to both
products (a consumer owner and a provider-A developer). The store is the fake of
lab-sql's RPC seam; the same cases run against the PostgreSQL adapter once L2-SQL
merges (wiring request WR-L2-2).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from infrx.contracts import errors
from infrx.contracts.v2 import records as v2
from infrx.lab.access import LabAccess
from infrx.lab.access.fakes import FakeAccessStore

T0 = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
PROVIDER_A, PROVIDER_B = "a0000000-0000-4000-8000-00000000000a", "b0000000-0000-4000-8000-00000000000b"
CONSUMER_1, CONSUMER_2 = "c1000000-0000-4000-8000-0000000000c1", "c2000000-0000-4000-8000-0000000000c2"
DEV_A, DEV_B = "d0000000-0000-4000-8000-00000000000a", "d0000000-0000-4000-8000-00000000000b"
VIEWER_A = "e0000000-0000-4000-8000-00000000000a"
BOTH = "f0000000-0000-4000-8000-0000000000ff"          # consumer owner of CONSUMER_1 + dev in A
CONSUMER_ONLY = "f1000000-0000-4000-8000-0000000000f1"
MODEL = "marlin-2b"
CONTENT = v2.DataCategory.request_content


def _member(provider, user, role=v2.ProviderRole.developer):
    return v2.ProviderMembership(provider_org_id=provider, user_id=user, role=role,
                                 granted_by="platform", granted_at=T0 - timedelta(days=1))


def _grant(grantor, provider, *purposes, grant_id="90000000-0000-4000-8000-000000000001"):
    return v2.AccessGrant(grant_id=grant_id, version=1, grantor_org_id=grantor,
                          recipient_provider_org_id=provider, model_ids=(MODEL,),
                          categories=(CONTENT,), purposes=purposes, retention_days=30,
                          effective_at=T0 - timedelta(days=1), expires_at=T0 + timedelta(days=30))


def _aggregate(provider_rev="rev-a"):
    return {"deployment_revision_id": provider_rev, "window_start": T0 - timedelta(hours=1),
            "window_end": T0, "requests": 40, "errors": 1, "p95_latency_ms": 900}


def world():
    store = FakeAccessStore()
    for provider, user, role in ((PROVIDER_A, DEV_A, v2.ProviderRole.developer),
                                 (PROVIDER_B, DEV_B, v2.ProviderRole.developer),
                                 (PROVIDER_A, VIEWER_A, v2.ProviderRole.viewer),
                                 (PROVIDER_A, BOTH, v2.ProviderRole.developer)):
        store.memberships[(provider, user)] = _member(provider, user, role)
    store.put_grant(_grant(CONSUMER_1, PROVIDER_A, v2.DataPurpose.provider_sharing))
    store.put_grant(_grant(CONSUMER_2, PROVIDER_B, v2.DataPurpose.capture,
                           grant_id="90000000-0000-4000-8000-000000000002"))
    store.rows[PROVIDER_A] = [_aggregate("rev-a")]
    store.rows[PROVIDER_B] = [_aggregate("rev-b")]
    clock = {"now": T0}
    return store, clock, LabAccess(store, clock=lambda: clock["now"])


def run(coro):
    return asyncio.run(coro)


def content(access, user, provider, grantor, purpose=v2.DataPurpose.provider_sharing):
    return run(access.authorize_content(user_id=user, provider_org_id=provider,
                                        grantor_org_id=grantor, model_id=MODEL,
                                        category=CONTENT, purpose=purpose))


# --- the seam: cross-provider reads ---------------------------------------------
def test_lab_access__a_provider_member_never_reads_another_providers_data():
    """Oracle: provider B's developer reaches none of provider A's aggregates, grants or
    content — neither by naming A's org id (forged) nor through A's grantor."""
    _, _, access = world()
    assert [a.deployment_revision_id for a in run(access.aggregates(DEV_A, PROVIDER_A))] == ["rev-a"]
    with pytest.raises(errors.NotFound):
        run(access.aggregates(DEV_B, PROVIDER_A))
    with pytest.raises(errors.NotFound):
        run(access.grant_history(DEV_B, PROVIDER_A, CONSUMER_1))
    with pytest.raises(errors.Forbidden):
        content(access, DEV_B, PROVIDER_A, CONSUMER_1)
    with pytest.raises(errors.Forbidden):        # B's own workspace, A's grantor
        content(access, DEV_B, PROVIDER_B, CONSUMER_1)
    assert content(access, DEV_A, PROVIDER_A, CONSUMER_1).grantor_org_id == CONSUMER_1


# --- memberships: explicit, per provider, never from consumer ownership -----------
def test_lab_access__workspaces_are_explicit_provider_memberships_only():
    """Oracle: a consumer-only user gets no workspace (L1 denies them); the user in both
    products gets exactly their provider membership, not their consumer org."""
    _, _, access = world()
    assert run(access.workspaces(CONSUMER_ONLY)) == ()
    assert [m.provider_org_id for m in run(access.workspaces(BOTH))] == [PROVIDER_A]
    assert [m.provider_org_id for m in run(access.workspaces(DEV_B))] == [PROVIDER_B]


def test_lab_access__a_revoked_membership_loses_its_workspace_at_once():
    """Oracle: the revocation instant ends selection, aggregates and content alike."""
    store, clock, access = world()
    assert content(access, DEV_A, PROVIDER_A, CONSUMER_1)
    old = store.memberships[(PROVIDER_A, DEV_A)]
    store.memberships[(PROVIDER_A, DEV_A)] = old.model_copy(update={"revoked_at": T0})
    assert run(access.workspaces(DEV_A)) == ()
    with pytest.raises(errors.NotFound):
        run(access.aggregates(DEV_A, PROVIDER_A))
    with pytest.raises(errors.NotFound):
        run(access.grant_history(DEV_A, PROVIDER_A, CONSUMER_1))
    with pytest.raises(errors.Forbidden):
        content(access, DEV_A, PROVIDER_A, CONSUMER_1)


def test_lab_access__a_viewer_reads_aggregates_and_never_content():
    """Oracle: every role reads its own aggregates; content needs developer+ and a grant."""
    _, _, access = world()
    assert len(run(access.aggregates(VIEWER_A, PROVIDER_A))) == 1
    with pytest.raises(errors.Forbidden):
        content(access, VIEWER_A, PROVIDER_A, CONSUMER_1)


# --- the default read is redacted -------------------------------------------------
@pytest.mark.parametrize("column", ["user_id", "org_id", "api_key_id", "request_id"])
def test_lab_access__default_aggregates_carry_no_customer_identity(column):
    """Oracle: an aggregate row carrying any identity column is refused, not passed on."""
    store, _, access = world()
    store.rows[PROVIDER_A] = [{**_aggregate(), column: CONSUMER_1}]
    with pytest.raises(ValueError):
        run(access.aggregates(DEV_A, PROVIDER_A))


# --- purpose separation, expiry, revocation mid-queue ------------------------------
def test_lab_access__each_purpose_is_its_own_permission():
    """Oracle: a sharing grant is not training consent; a capture grant is not sharing."""
    _, _, access = world()
    assert content(access, DEV_A, PROVIDER_A, CONSUMER_1).purposes == (
        v2.DataPurpose.provider_sharing,)
    for purpose in (v2.DataPurpose.training, v2.DataPurpose.external_judging,
                    v2.DataPurpose.capture):
        with pytest.raises(errors.Forbidden):
            content(access, DEV_A, PROVIDER_A, CONSUMER_1, purpose)
    assert content(access, DEV_B, PROVIDER_B, CONSUMER_2, v2.DataPurpose.capture)
    with pytest.raises(errors.Forbidden):
        content(access, DEV_B, PROVIDER_B, CONSUMER_2)


def test_lab_access__revocation_denies_queued_work_on_its_next_check():
    """Oracle: authorized at enqueue, revoked before the worker's use-time check -> denied;
    the service answers from the current grant on its own clock, never a cached one."""
    store, clock, access = world()
    assert content(access, DEV_A, PROVIDER_A, CONSUMER_1)        # enqueue-time check
    store.revoke(CONSUMER_1, PROVIDER_A, at=T0 + timedelta(minutes=1))
    assert content(access, DEV_A, PROVIDER_A, CONSUMER_1)        # before the instant
    clock["now"] = T0 + timedelta(minutes=1)
    with pytest.raises(errors.Forbidden):                        # use-time check
        content(access, DEV_A, PROVIDER_A, CONSUMER_1)


def test_lab_access__an_expired_grant_denies():
    store, clock, access = world()
    clock["now"] = T0 + timedelta(days=30)
    with pytest.raises(errors.Forbidden):
        content(access, DEV_A, PROVIDER_A, CONSUMER_1)


# --- grant history: the C/J/T port -------------------------------------------------
def test_lab_access__grant_history_keeps_every_version_for_the_recipient_only():
    """Oracle: history names every version (source ids for C/J/T) and is scoped to the
    member's own provider; history never authorizes (a revoked version is listed and the
    content read is still refused)."""
    store, clock, access = world()
    store.revoke(CONSUMER_1, PROVIDER_A, at=T0)
    history = run(access.grant_history(DEV_A, PROVIDER_A, CONSUMER_1))
    assert [(g.version, g.revoked_at) for g in history] == [(1, None), (2, T0)]
    assert {g.grant_id for g in history} == {"90000000-0000-4000-8000-000000000001"}
    assert run(access.grant_history(DEV_B, PROVIDER_B, CONSUMER_1)) == ()
    with pytest.raises(errors.Forbidden):
        content(access, DEV_A, PROVIDER_A, CONSUMER_1)
