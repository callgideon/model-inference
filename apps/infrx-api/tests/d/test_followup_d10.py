#!/usr/bin/env python3
"""D10 follow-up (0022) on real PostgreSQL (both images): W5's fenced `fail_preparation`
(SQL and the `PgJobStore` adapter, as the dedicated `infrx_runtime` login) and G8's fair,
bounded flag writer `set_feature_flag` (grants, the queue, the overlapping-lockers probe).

    INFRX_D_TASK=d10 uv run --frozen pytest -q tests/d/test_followup_d10.py
    INFRX_D_TASK=d10 INFRX_D1_IMAGE=supabase uv run --frozen pytest -q tests/d/test_followup_d10.py
"""
from __future__ import annotations

import asyncio

import pytest
from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.records import (JobState, LeaseKind, SettlementState, TerminalCause)
from infrx.state import migrations
from infrx.state.jobstore import PgJobStore, connector

from . import checks_admission as ca
from . import checks_followup as cf
from . import pgharness

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_followup"
#: A local test credential for the lane's own container (as test_reads.RUNTIME_PASSWORD).
RUNTIME_PASSWORD = "infrx-d10-runtime-local"
_state: dict = {}


def _db():
    if "conn" not in _state:
        pgharness.ensure()
        pgharness.recreate(DB)
        pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
        conn = pgharness.connect(DB)
        ca.seed_admission(conn)
        _state["conn"] = conn
    return _state["conn"]


def test_fail_preparation_is_fenced_permanent_and_replayable() -> None:
    print(cf.check_fail_preparation(_db()))


def test_a_written_refetch_restarts_the_grace() -> None:
    print(cf.check_written_reregistration_refreshes(_db()))


def test_followup_privileges() -> None:
    print(cf.check_followup_privileges(_db()))


def test_the_flag_writer_queues_new_share_readers_behind_it() -> None:
    _db()
    print(cf.check_flag_writer_queues_new_readers(pgharness.connect, DB))


def test_the_flag_writer_lands_under_overlapping_lockers() -> None:
    _db()
    print(cf.check_flag_writer_lands_under_overlapping_lockers(pgharness.connect, DB))


def test_the_adapter_ends_a_preparation_as_the_runtime_login() -> None:
    """`PgJobStore.fail_preparation` (the port W5's `PreparationRunner._end` calls) over the
    dedicated `infrx_runtime` login, no `set role`: a typed `TerminalOutcome`, the identical
    retry equal, a stale lease `StaleLease`, a cause that is not a preparation's end
    `InvalidRequest`, a second worker `AlreadyTerminal`."""
    conn = _db()
    with pgharness.connect("postgres") as admin:
        admin.execute(f"alter role infrx_runtime login password '{RUNTIME_PASSWORD}'")
    store = PgJobStore(connector(pgharness.dsn(DB).replace(
        f"postgres:{pgharness.PASSWORD}@", f"infrx_runtime:{RUNTIME_PASSWORD}@"),
        set_role=False))
    world = ca.World(conn)
    request = b.request(world)
    ca.admit(conn, request, b.idem(request, request.request_id))

    async def run():
        lease = await store.claim_preparation(request.request_id, "prep-rt")
        assert lease.kind is LeaseKind.preparation
        with pytest.raises(errors.InvalidRequest):
            await store.fail_preparation(lease, TerminalCause.engine_error)
        with pytest.raises(errors.StaleLease):
            await store.fail_preparation(lease.model_copy(update={"worker_id": "other"}),
                                         TerminalCause.invalid_media)
        ended = await store.fail_preparation(lease, TerminalCause.invalid_media)
        assert (ended.job_id, ended.state, ended.cause, ended.settlement_state, ended.usage) \
            == (request.request_id, JobState.failed, TerminalCause.invalid_media,
                SettlementState.released_free, None), ended
        assert await store.fail_preparation(lease, TerminalCause.invalid_media) == ended
        with pytest.raises(errors.AlreadyTerminal):
            await store.fail_preparation(lease, TerminalCause.preparation_failed)
        with pytest.raises(errors.AlreadyTerminal):
            await store.claim_preparation(request.request_id, "prep-rt-2")
    asyncio.run(run())
