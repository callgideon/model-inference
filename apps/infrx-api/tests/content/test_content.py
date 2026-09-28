"""C2 TRACE-TENANT / LAB-ACCESS / CONSOLE-FLOWS: Lab content access (`infrx.content`) over the
C2-RPC contract fake (`infrx.content.fakes`), T3 retention and the object store.

    uv run --frozen pytest -q tests/content

The real-PostgreSQL half (WR-C2-5): the same cases on lab-sql's `PgContentRefs` (0041) and
L2's `PgAccessStore` - `INFRX_D_TASK=lab-c2` (+ `INFRX_C2_S3=1` for M3's store on MinIO).
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from datetime import timedelta

import pytest

from infrx.content import MAX_TTL_S, ContentAccess, handle_digest
from infrx.contracts import errors, ids
from infrx.contracts.records import ContentState
from infrx.contracts.v2 import records as v2
from infrx.state.lab_data import grant_ref
from infrx.traces import ship

from . import world_pg
from .world import (A, B, BODY, BOTH, C1, C2, DEV_A, DEV_A2, DEV_B, MODEL_A, MODEL_B, REQ, REQ_B,
                    SHARING, VIEWER_A, World, s3_store)


def run(coroutine):
    return asyncio.run(coroutine)


S3 = os.environ.get("INFRX_C2_S3") == "1"
PG = world_pg.unavailable()


@pytest.fixture(params=["memory", pytest.param("s3", marks=[pytest.mark.s3, pytest.mark.skipif(
    not S3, reason="C2 on M3's S3ObjectStore: start infrx-lab-c2-s3 (MinIO on 57506) and "
                   "export INFRX_C2_S3=1")]), pytest.param("pg", marks=[pytest.mark.pg, pytest.mark.skipif(
    PG is not None, reason=f"C2-RPC on task-local PostgreSQL: {PG}")])])
def w(request):
    """Every case in memory; with INFRX_C2_S3=1 over the real M3 object store; with
    INFRX_D_TASK=lab-c2 on lab-sql's RPC (and MinIO too when INFRX_C2_S3=1)."""
    objects = s3_store(f"test/lab-c2/{uuid.uuid4().hex}/") if request.param == "s3" or (
        request.param == "pg" and S3) else None
    if request.param != "pg":
        yield World(objects)
        return
    with world_pg.pgharness.connect(world_pg.fresh_database()) as conn:
        yield world_pg.PgWorld(conn, objects)


# --- the seam: a ref reaches its own grant's content, for its own recipient only ---------
def test_content__a_cross_provider_ref_or_grant_exposes_nothing(w):
    """Oracle: provider B's developer cannot read A's ref, nor issue one under A's grant,
    nor reach C1's request through B's own grant; A's developer reads the content."""
    handle, binding = run(w.issue())
    assert (binding.grantor_org_id, binding.request_id) == (C1, REQ)
    read = run(w.read(handle))
    assert (read.state, read.content) == (ContentState.available, BODY)
    with pytest.raises(errors.NotFound):
        run(w.read(handle, user=DEV_B, provider=B))
    with pytest.raises(errors.NotFound):           # A's grant ref named by provider B
        run(w.issue(user=DEV_B, provider=B, grant=w.ref(C1, A)))
    with pytest.raises(errors.NotFound):           # B's own grant, C1's request
        run(w.issue(user=DEV_B, provider=B, grantor=C2, request=REQ))


def test_content__a_forged_grant_ref_is_not_found(w):
    """Oracle: a grant ref with another grant id, a wrong digest, or another provider's
    genuine grant confirms nothing; C2's request is not under C1's grant."""
    genuine = w.ref(C1, A)
    other_id = genuine.replace(w.store.grants[(C1, A)].grant_id,
                               "99999999-0000-4000-8000-000000000099")
    wrong_digest = genuine[:-4] + "0000"
    for forged in (other_id, wrong_digest, w.ref(C2, B)):
        with pytest.raises(errors.NotFound):
            run(w.issue(grant=forged))
    with pytest.raises(errors.NotFound):
        run(w.issue(request=REQ_B))
    assert w.refs.refs == {}


def test_content__a_ref_is_redeemable_by_the_user_it_was_issued_to_only(w):
    """Oracle: another developer of the same provider holding the handle gets not_found."""
    handle, _ = run(w.issue())
    with pytest.raises(errors.NotFound):
        run(w.read(handle, user=DEV_A2))
    assert run(w.read(handle)).state is ContentState.available


def test_content__a_ref_is_bound_to_its_recipient_provider(w):
    """Oracle: its own user, a developer of both providers, reading as provider B: not_found."""
    handle, _ = run(w.issue())
    with pytest.raises(errors.NotFound):
        run(w.read(handle, user=DEV_A, provider=B))


def test_content__revocation_fails_an_existing_ref_closed(w):
    """Oracle: a ref issued before the revocation reads nothing after it, and holds nothing."""
    handle, _ = run(w.issue())
    w.store.revoke(C1, A, w.store.now)
    with pytest.raises(errors.Forbidden):
        run(w.read(handle))
    assert run(w.access.holds(C1, REQ)) is False


def test_content__a_revoked_membership_fails_an_existing_ref_closed(w):
    handle, _ = run(w.issue())
    w.store.memberships[(A, DEV_A)] = w.store.memberships[(A, DEV_A)].model_copy(
        update={"revoked_at": w.store.now})
    with pytest.raises(errors.Forbidden):
        run(w.read(handle))


def test_content__a_narrowed_grant_fails_an_existing_ref_closed(w):
    """Oracle: the grant's CURRENT version decides: a new version without the purpose, or
    without one of the two categories, denies a ref issued under the old one."""
    handle, _ = run(w.issue())
    w.grant(C1, A, MODEL_A, categories=(v2.DataCategory.response_content,))
    with pytest.raises(errors.Forbidden):
        run(w.read(handle))
    w.grant(C1, A, MODEL_A)
    assert run(w.read(handle)).state is ContentState.available
    w.grant(C1, A, MODEL_A, purposes=(v2.DataPurpose.training,))
    with pytest.raises(errors.Forbidden):
        run(w.read(handle))


def test_content__the_ref_expires_at_its_ttl(w):
    """Oracle: past its expiry a ref is gone, even with the grant and content intact."""
    handle, binding = run(w.issue())
    assert binding.expires_at == w.store.now + timedelta(seconds=w.access.ttl_s)
    w.advance(w.access.ttl_s - 1)
    assert run(w.read(handle)).state is ContentState.available
    assert run(w.access.holds(C1, REQ)) is True
    assert run(w.access.holds(C1, REQ_B)) is False       # a ref holds its own request only
    w.advance(1)
    with pytest.raises(errors.Gone):
        run(w.read(handle))
    assert run(w.access.holds(C1, REQ)) is False


def test_content__the_grant_expiry_and_retention_bound_the_ref(w):
    """Oracle: a ref never outlives its grant, nor the job's age plus retention_days; past
    retention no ref is issued at all."""
    w.grant(C1, A, MODEL_A, expires_in=timedelta(seconds=30))
    _, binding = run(w.issue())
    assert binding.expires_at == w.store.now + timedelta(seconds=30)
    w.grant(C1, A, MODEL_A, retention_days=1)
    w.advance(86_400 - 120)                  # the job is 60 s old at the world's start
    _, binding = run(w.issue())
    assert binding.expires_at == w.refs.jobs[(C1, REQ)][1] + timedelta(days=1)
    w.advance(60)
    with pytest.raises(errors.Gone):
        run(w.issue())


def test_content__retention_shrunk_after_issue_fails_the_ref_closed(w):
    handle, _ = run(w.issue())
    w.advance(120)
    w.grant(C1, A, MODEL_A, retention_days=1)
    w.refs.jobs[(C1, REQ)] = (MODEL_A, w.store.now - timedelta(days=1))
    with pytest.raises(errors.Gone):
        run(w.read(handle))


def test_content__each_purpose_is_its_own_permission(w):
    """Oracle: a provider_sharing grant does not issue a training ref."""
    with pytest.raises(errors.Forbidden):
        run(w.issue(purpose=v2.DataPurpose.training))
    assert w.refs.refs == {}


def test_content__both_content_categories_are_required(w):
    """Oracle: the content object carries the request AND the response."""
    w.grant(C1, A, MODEL_A, categories=(v2.DataCategory.response_content,))
    with pytest.raises(errors.Forbidden):
        run(w.issue())


def test_content__the_model_is_the_jobs_never_the_callers(w):
    """Oracle: a job served on a model outside the grant is refused, whatever else holds."""
    w.job(C1, REQ, MODEL_B)
    with pytest.raises(errors.Forbidden):
        run(w.issue())


def test_content__a_viewer_issues_no_ref(w):
    with pytest.raises(errors.Forbidden):
        run(w.issue(user=VIEWER_A))


def test_content__a_storage_key_is_never_a_ref(w):
    """Oracle: a key, a path or a malformed handle is not_found before the RPC is asked."""
    run(w.issue())
    asked = []
    redeem = w.refs.redeem
    w.refs.redeem = lambda **kw: asked.append(kw) or redeem(**kw)
    for bad in (ship.content_key(C1, "seg-0000:0"), "tc_short", "../tc_" + "a" * 43, None):
        with pytest.raises(errors.NotFound):
            run(w.read(bad))
    assert asked == []


def test_content__the_database_never_sees_the_handle(w):
    """Oracle: the RPC is given the handle's SHA-256 only; the handle is a fresh `tc_`."""
    handle, _ = run(w.issue())
    ids.require_handle(handle, ids.TRACE_CONTENT_HANDLE_RE)
    assert list(w.refs.refs) == [hashlib.sha256(handle.encode()).hexdigest()]
    assert handle_digest(handle) in w.refs.refs and handle not in str(w.refs.refs)
    assert run(w.issue())[0] != handle


def test_content__the_rpc_refuses_a_reused_handle_an_unbounded_ttl_or_no_category(w):
    """The contract's own input rules: a handle is issued once; the ttl is bounded; a ref
    names at least one category."""
    args = dict(handle_sha256="0" * 64, user_id=DEV_A, provider_org_id=A,
                grant_ref=w.ref(C1, A), request_id=REQ, purpose=SHARING, categories=BOTH,
                ttl_s=60)
    run(w.refs.issue(**args))
    with pytest.raises(errors.Conflict):
        run(w.refs.issue(**args))
    for bad in ({"ttl_s": 0}, {"ttl_s": MAX_TTL_S + 1}, {"categories": ()}):
        with pytest.raises(errors.InvalidRequest):
            run(w.refs.issue(**{**args, "handle_sha256": "1" * 64, **bad}))


def test_content__the_ttl_is_bounded():
    w = World()
    for ttl in (0, MAX_TTL_S + 1):
        with pytest.raises(ValueError):
            ContentAccess(w.refs, w.retention, ttl_s=ttl)
    assert ContentAccess(w.refs, w.retention, ttl_s=MAX_TTL_S).ttl_s == MAX_TTL_S


# --- T3 retention and the content states ----------------------------------------------
def test_content__a_deleted_request_is_expired_despite_the_object(w):
    """Oracle: T3's logical deletion wins over physical presence; no object is read."""
    handle, _ = run(w.issue())
    run(w.retention.delete(C1, REQ, "customer"))
    w.objects.reads.clear()
    read = run(w.read(handle))
    assert (read.state, read.content, w.objects.reads) == (ContentState.expired, None, [])


def test_content__content_past_its_bound_is_expired_despite_the_object(w):
    w.grant(C1, A, MODEL_A, retention_days=90)
    w.refs.jobs[(C1, REQ)] = (MODEL_A, w.store.now)
    w.edit(C1, "seg-0000:0", started_at=w.store.now - timedelta(days=w.retention.content_days))
    handle, _ = run(w.issue())
    w.objects.reads.clear()
    read = run(w.read(handle))
    assert (read.state, read.content, w.objects.reads) == (ContentState.expired, None, [])


def test_content__missing_content_is_a_state_never_a_crash(w):
    """CONSOLE-FLOWS: not yet projected, metadata only, never stored, gone, or unrenderable
    - each is a state without content."""
    cases = {}
    for n, (content, stored) in enumerate(((None, True), (b"{}", False), (b"{not json", True),
                                           (b'{"v": 1, "raw": "<script>"}', True))):
        request = f"5e5e5e5e-0000-4000-8000-00000000000{n}"
        w.job(C1, request, MODEL_A)
        w.trace(C1, request, content, stored=stored)
        cases[request] = ContentState.metadata_only if content is None else ContentState.lost
    pending = "5e5e5e5e-0000-4000-8000-00000000000f"
    w.job(C1, pending, MODEL_A)
    cases[pending] = ContentState.pending
    gone = "5e5e5e5e-0000-4000-8000-00000000000e"
    w.job(C1, gone, MODEL_A)
    row = w.trace(C1, gone, b"{}")
    run(w.objects.delete(row.content_key))
    cases[gone] = ContentState.lost
    for request, state in cases.items():
        handle, _ = run(w.issue(request=request))
        read = run(w.read(handle))
        assert (request, read.state, read.content) == (request, state, None)


def test_content__a_live_ref_holds_the_object_against_the_sweep(w):
    """Oracle: T3 keeps content a live ref references, and sweeps it once the ref ends."""
    from infrx.traces.retention import Retention
    run(w.issue())
    swept = Retention(w.tombstones, w.traces, None, w.objects, holds=w.access.holds,
                      clock=lambda: w.store.now)
    run(w.retention.delete(C1, REQ, "customer"))
    assert run(swept.sweep())["held"] == 1
    assert run(w.objects.get(ship.content_key(C1, "seg-0000:0"))) is not None
    w.advance(w.access.ttl_s)
    assert run(swept.sweep())["cleaned"] == 1
    assert run(w.objects.get(ship.content_key(C1, "seg-0000:0"))) is None


def test_content__the_grant_ref_names_a_version_and_rights_follow_the_current_one(w):
    """R166: an older version's ref still names the grant; the current version decides."""
    old = w.ref(C1, A)
    w.grant(C1, A, MODEL_A)
    assert grant_ref(w.store.grants[(C1, A)]) != old
    _, binding = run(w.issue(grant=old))
    assert binding.grant_version == 2
