#!/usr/bin/env python3
"""D10-0026 (R147, E3C-CELLS F-1) on real PostgreSQL: the worker's result write is fenced
like `append` - in SQL (`checks_leases.check_result_fence`, also the 0026 migration mutants'
check), through the worker's port, re-run with its grants unchanged, and 0001-0025 left
byte-identical.

    INFRX_D_TASK=d4 uv run --frozen pytest -q tests/d/test_result_fence_d10.py
"""
from __future__ import annotations

import asyncio
import subprocess

import pytest
from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import TerminalCause
from infrx.state import migrations

from . import checks_admission as ca
from . import checks_leases as cl
from . import pgharness, pgstore

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_result_fence"
FENCE = "0026_fenced_result.sql"
#: The committed base D10-0026 started from (claude/consumer-v1: 0001-0025 final).
BASE = "e0087a8f"
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


def test_a_result_write_is_fenced_like_append() -> None:
    print(cl.check_result_fence(_db()))


def test_the_worker_port_passes_its_lease_and_null_is_already_terminal() -> None:
    """`PgJobStore.put_result(job_id, text, lease)`, the worker's call: generation 1 lapsed
    and generation 2 of the SAME worker id runs - generation 1 is `StaleLease` before and
    after generation 2 stores; past R29's instant the fence ends the job and the port raises
    `AlreadyTerminal`, never a NULL reference. Oracle: a port that drops the lease (0014's
    unfenced write) or hands the NULL back as a reference."""
    h = pgstore.factory()
    h.extra["grant"](b.ORG_A, "25.00")
    store = h.extra["store"]

    async def running(key: str):
        request = b.request(h)
        await h.port.admit(request, b.idem(request, key))
        await h.port.prepared(await h.port.claim_preparation(request.request_id, "p"), ())
        return request, await store.claim(request.request_id, "worker-a")

    async def body():
        request, stale = await running("lapsed")
        h.clock.advance(DEFAULTS.lease_ttl_s)
        await store.recover()
        live = await store.claim(request.request_id, "worker-a")
        assert live.generation == stale.generation + 1, live
        with pytest.raises(errors.StaleLease):
            await store.put_result(request.request_id, "stale", stale)
        ref = await store.put_result(request.request_id, "live", live)
        assert ref == f"infrx-result:{request.request_id}", ref
        with pytest.raises(errors.StaleLease):
            await store.put_result(request.request_id, "stale", stale)
        late, lease = await running("late")
        h.clock.advance((lease.generation_deadline_at - h.clock.now()).total_seconds())
        with pytest.raises(errors.AlreadyTerminal):
            await store.put_result(late.request_id, "too late", lease)
        ended = h.extra["conn"].execute("select outcome_cause from infrx.jobs where "
                                        "request_id = %s", (late.request_id,)).fetchone()
        assert ended == (TerminalCause.deadline_exceeded.value,), ended
    asyncio.run(body())


def test_0026_is_re_runnable_and_keeps_the_grants() -> None:
    """Applied a second time: the same definition and ACL; the ACL is 0014's and 0021's
    (service_role and infrx_runtime execute, the browser roles do not)."""
    conn = _db()
    snap = ("select md5(pg_get_functiondef(p.oid)), coalesce(p.proacl::text, ''), p.prosecdef "
            "from pg_proc p where p.oid = 'infrx.put_result(jsonb)'::regprocedure")
    before = conn.execute(snap).fetchall()
    pgharness.apply(DB, tuple(f for f in migrations.sql_for(shim=pgharness.NEEDS_SHIM)
                              if f[0] == FENCE))
    assert conn.execute(snap).fetchall() == before, "0026 re-applied changed put_result"
    may = {role: conn.execute("select has_function_privilege(%s, 'infrx.put_result(jsonb)', "
                              "'execute')", (role,)).fetchone()[0]
           for role in ("service_role", "infrx_runtime", "anon", "authenticated")}
    assert may == {"service_role": True, "infrx_runtime": True, "anon": False,
                   "authenticated": False}, may
    assert before[0][2], "put_result is no longer SECURITY DEFINER"
    print(cl.check_result_fence(conn))


def test_0001_to_0025_are_byte_identical_to_the_base() -> None:
    root = subprocess.run(("git", "rev-parse", "--show-toplevel"), capture_output=True,
                          text=True, cwd=migrations.DIR)
    if root.returncode != 0 or subprocess.run(
            ("git", "cat-file", "-e", f"{BASE}^{{commit}}"), cwd=migrations.DIR).returncode:
        pytest.skip(f"no git checkout with {BASE}")
    frozen = sorted(p.name for p in migrations.DIR.glob("00*.sql") if p.name < "0026_")
    assert len(frozen) == 25, frozen
    diff = subprocess.run(("git", "diff", "--name-only", BASE, "--", *frozen),
                          capture_output=True, text=True, cwd=migrations.DIR)
    assert diff.returncode == 0 and diff.stdout == "", diff.stdout or diff.stderr
