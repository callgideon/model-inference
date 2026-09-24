#!/usr/bin/env python3
"""D10.a on real PostgreSQL (both images): the checks of `checks_ready.py` on the "admission"
scenario, the two-connection races, and the adapter across a crash after COMMIT and a new
process (`PgLifecycle` is stateless, so a new instance IS another process).

    INFRX_D_TASK=d10 uv run --frozen pytest -q tests/d/test_ready.py
    INFRX_D_TASK=d10 INFRX_D1_IMAGE=supabase uv run --frozen pytest -q tests/d/test_ready.py
"""
from __future__ import annotations

import asyncio

import pytest
from infrx.contracts.conformance import builders as b
from infrx.contracts.v2.lifecycle import AdmissionExpectation
from infrx.contracts.v2.records import AccountingRegime
from infrx.media.attachments import PgAttachments
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lifecycle import PgLifecycle

from . import checks_admission as ca
from . import checks_credit as cc
from . import checks_ready as cr
from . import pgharness

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_ready"
_state: dict = {}
CARD = AdmissionExpectation(accounting_regime=AccountingRegime.credit,
                            rate_card_version=cc.CARD)


def _db():
    if "conn" not in _state:
        pgharness.ensure()
        pgharness.recreate(DB)
        pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
        conn = pgharness.connect(DB)
        ca.seed_admission(conn)
        _state["conn"] = conn
    return _state["conn"]


def _store() -> PgLifecycle:
    """A new adapter over its own connections: another process."""
    return PgLifecycle(connector(pgharness.dsn(DB)), upload_window_s=cr.WINDOW_S,
                       grace_s=cr.GRACE_S, retention_s=cr.RETENTION_S)


def test_ready_marker() -> None:
    print(cr.check_ready_marker(_db()))


def test_ready_refusals_admit_nothing_and_keep_the_key() -> None:
    print(cr.check_ready_refusals(_db()))


def test_upload_ticket_rows() -> None:
    print(cr.check_upload_ticket(_db()))


def test_register_guards() -> None:
    print(cr.check_register_guards(_db()))


def test_ready_privileges() -> None:
    print(cr.check_ready_privileges(_db()))


def test_ready_races() -> None:
    _db()
    print(cr.check_ready_races(pgharness.connect, DB))


def test_crash_after_commit_a_new_process_finds_the_same_job() -> None:
    """ADMISSION-READY / D10.d item 5: the admission commits and its answer is lost (the
    gateway dies); a NEW adapter retrying with the same key finds the same job, hold,
    outbox rows and marker - nothing is admitted twice."""
    conn = _db()
    world = ca.World(conn)
    clip = cr.source_ref(cr.c1_org(conn), b"crash-clip")
    cr.register(conn, clip)
    request = cr.credit_request(conn, world, (clip,))
    idem = b.idem(request, "crash-after-commit")

    def identities():
        return conn.execute(
            "select j.job_handle, (select array_agg(h.amount::text || h.state) from "
            "infrx.credit_wallet_holds h where h.request_id = j.request_id), (select "
            "array_agg(o.event_id::text order by o.event_id) from infrx.outbox o where "
            "o.aggregate_id = j.request_id), (select count(*) from infrx.credit_ledger l "
            "where l.request_id = j.request_id), r.ready_at, r.source_count "
            "from infrx.jobs j join infrx.job_readiness r on r.job_id = j.request_id "
            "where j.request_id = %s", (request.request_id,)).fetchone()

    async def run():
        await _store().admit_ready(request, idem, CARD)       # committed; answer "lost"
        before = identities()
        admission, readiness = await _store().admit_ready(request, idem, CARD)
        assert admission.replayed and admission.request_id == request.request_id
        assert readiness is not None and readiness.sources[0].ref.handle == clip.handle
        assert await _store().readiness(request.request_id) == readiness
        assert identities() == before, "the retry changed the job's identities"
        assert conn.execute("select count(*) from infrx.jobs where request_id = %s",
                            (request.request_id,)).fetchone()[0] == 1
    asyncio.run(run())


def test_the_previous_runtime_reads_a_job_admit_ready_admitted() -> None:
    """D5 rollback compatibility: the previous worker's durable attach read (PgAttachments,
    0003 `job_media`) returns the manifest `admit_ready` wrote, the previous gateway's
    same-refs attach is a no-op, and 0012's old claim door leases the job."""
    conn = _db()
    world = ca.World(conn)
    clip = cr.source_ref(cr.c1_org(conn), b"rollback-clip")
    cr.register(conn, clip)
    request = cr.credit_request(conn, world, (clip,))

    async def run():
        await _store().admit_ready(request, b.idem(request, "rollback"), CARD)
        attachments = PgAttachments(connector(pgharness.dsn(DB)))
        refs = await attachments.get(request.request_id)
        assert refs is not None and [r.handle for r in refs] == [clip.handle]
        await attachments.put(request.request_id, refs)       # the old gateway's attach
        assert await attachments.get(request.request_id) == refs
    asyncio.run(run())
    assert cr.refusal(conn, "claim_preparation", {"job_id": request.request_id,
                                                  "worker_id": "old-worker",
                                                  "limits": cr.PREP_LIMITS}) is None


def test_the_cutover_gate_needs_paused_admission_and_no_unmarked_preparing_job() -> None:
    """The operator check I8 scripts (0019 `readiness_cutover_check`): not ready while
    admission is on, or while a preparing job lacks a marker; ready once both hold."""
    conn = _db()
    world = ca.World(conn)

    def body():
        gate = lambda: conn.execute("select infrx.readiness_cutover_check()").fetchone()[0]
        assert gate()["ready"] is False and gate()["admission_paused"] is False
        old = cr.credit_request(conn, world)
        ca.admit(conn, old, b.idem(old, "cutover-old"), regime="credit")
        for flag in ("credit_admission", "legacy_usd_admission"):
            cc.set_flag(conn, flag, False)
        waiting = gate()
        assert waiting["ready"] is False and waiting["unmarked_preparing"] >= 1, waiting
        conn.execute("update infrx.jobs set state = 'failed', outcome_cause = "
                     "'preparation_failed', settlement_state = 'released_free', "
                     "settled_at = infrx.now() where state = 'preparing' and request_id "
                     "not in (select job_id from infrx.job_readiness)")
        assert gate()["ready"] is True, gate()
        return "gate closed while admitting or while an unmarked job prepares"
    print(ca._in_rollback(conn, body))


def test_abort_is_durable_idempotent_and_final() -> None:
    """UPLOAD-RESTART, abort (verifier on F2C-L a): an abort committed by one process is the
    ticket every other process reads; repeating it answers the same ticket; a finalized
    ticket cannot be aborted (`upload_not_open`) and a receipt over `max_bytes` is
    `too_large` - each through a NEW adapter."""
    from infrx.contracts import errors
    from infrx.contracts.records import MediaKind, UploadState
    from infrx.contracts.v2.lifecycle import LifecycleRefusal, UploadConstraints, refusal_of
    conn = _db()
    org = cr.c1_org(conn)
    constraints = UploadConstraints.parse({"max_bytes": 64}, max_media_bytes=1 << 26,
                                          allowed_mime=frozenset(cr.MP4))

    async def run():
        ticket = await _store().create(org, constraints)
        aborted = await _store().abort(org, ticket.upload_handle,
                                       LifecycleRefusal.mime_not_accepted)
        assert aborted.state is UploadState.aborted and \
            aborted.refusal is LifecycleRefusal.mime_not_accepted
        assert await _store().abort(org, ticket.upload_handle,
                                    LifecycleRefusal.too_large) == aborted, "abort moved"
        for call in (_store().resolve(org, ticket.upload_handle),
                     _store().acknowledge_put(org, ticket.upload_handle, bytes=1,
                                              digest=cr.digest(b"x"))):
            try:
                await call
            except errors.DomainError as refused:
                assert refusal_of(refused) in (LifecycleRefusal.upload_not_finalized,
                                               LifecycleRefusal.upload_not_open), refused
            else:
                raise AssertionError("an aborted ticket answered")
        big = await _store().create(org, constraints)
        try:
            await _store().acknowledge_put(org, big.upload_handle, bytes=65,
                                           digest=cr.digest(b"y" * 65))
        except errors.RequestTooLarge as refused:
            assert refusal_of(refused) is LifecycleRefusal.too_large
        else:
            raise AssertionError("a receipt over max_bytes was accepted")
        data = b"abort-finalized"
        done = await _store().create(org, constraints)
        await _store().acknowledge_put(org, done.upload_handle, bytes=len(data),
                                       digest=cr.digest(data))
        ref = cr.source_ref(org, data, handle=done.upload_handle, kind=MediaKind.upload)
        await _store().complete(org, done.upload_handle, ref)
        try:
            await _store().abort(org, done.upload_handle, LifecycleRefusal.too_large)
        except errors.StateConflict as refused:
            assert refusal_of(refused) is LifecycleRefusal.upload_not_open
        else:
            raise AssertionError("a finalized ticket was aborted")
    asyncio.run(run())


def test_an_unreachable_store_is_retain_and_report() -> None:
    """RV-03: when the authority cannot be reached the adapter answers a typed, retryable
    `dependency_unavailable` - never an empty candidate page a collector would act on."""
    from infrx.contracts import errors
    store = PgLifecycle(connector("postgresql://postgres:x@127.0.0.1:1/none?connect_timeout=1"))

    async def run():
        try:
            await store.candidates(after=None, limit=1)
        except errors.DependencyUnavailable:
            return
        raise AssertionError("an unreachable store answered")
    asyncio.run(run())
