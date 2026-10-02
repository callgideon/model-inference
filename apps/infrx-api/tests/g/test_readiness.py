#!/usr/bin/env python3
"""E4C run 2 (register row 94): the gateway's journal readiness probe is one bounded,
constant-cost statement, not `infrx.journal_usage()`'s four aggregates every 5 s.

    INFRX_D_TASK=ap2 uv run --frozen pytest -q tests/g/test_readiness.py

The unit cases need nothing; the PostgreSQL cases use `tests/d`'s disposable harness on the
task's key (a fresh migrated database) and skip visibly without Docker (a skip is never a
pass).
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from infrx.gateway import readiness
from infrx.state import journal
from infrx.state.journal import PgStreamStore

from tests.d import pgharness, pgstore

UNAVAILABLE = pgharness.unavailable()
needs_pg = pytest.mark.skipif(UNAVAILABLE is not None,
                              reason=f"task-local PostgreSQL unavailable: {UNAVAILABLE}")


class Stream:
    """A journal whose `usage()` hangs (the slow hosted aggregates) and whose `ready()`
    answers or fails as told."""

    def __init__(self, ready_fails: bool = False) -> None:
        self.ready_fails, self.asked = ready_fails, []

    async def usage(self):
        self.asked.append("usage")
        await asyncio.Event().wait()

    async def ready(self):
        self.asked.append("ready")
        if self.ready_fails:
            raise ConnectionError("postgresql://infrx:secret@db/infrx is unreachable")
        return True


@pytest.mark.parametrize("ready_fails", [False, True])
def test_journal_probe__asks_the_bounded_ready_never_the_usage_aggregates(ready_fails):
    """The probe's answer is `ready()`'s: True when it answers, False when it fails - and
    `usage()` (which hangs here, as the four aggregates did on the slow hosted database) is
    never asked. Oracle: a probe on `usage()` reads False within its bound and asks usage."""
    stream = Stream(ready_fails)
    assert readiness.Probe(readiness.journal_check(stream), timeout_s=0.5)() is not ready_fails
    assert stream.asked == ["ready"]


@pytest.mark.parametrize("fault", ["hangs", "raises"])
def test_probe__a_check_that_hangs_or_fails_reads_unavailable_and_leaves_no_thread(fault):
    """`Probe`, moved from `gateway/pilot.py`: a first answer that hangs past the bound or
    raises reads False, and the thread it was asked on has ended."""
    async def check():
        if fault == "raises":
            raise ConnectionError("postgresql://infrx:secret@db/infrx is unreachable")
        await asyncio.Event().wait()

    before = set(threading.enumerate())
    assert readiness.Probe(check, timeout_s=0.05)() is False
    assert set(threading.enumerate()) <= before


class Connection:
    def __init__(self) -> None:
        self.executed, self.closed = [], False

    async def execute(self, sql, params=None):
        self.executed.append((sql, params))

    async def close(self):
        self.closed = True


def test_journal_ready__is_one_server_bounded_primary_key_lookup_on_one_connection():
    """`ready()` sends ONE simple-protocol message (no parameters: the 2 s bound and the
    lookup travel together as one implicit transaction, so `set local` needs no session
    state) on one connection it closes; never `journal_usage`. Oracle: a dropped bound,
    a second round trip or the aggregates fail it."""
    conn = Connection()

    async def connect():
        return conn

    assert asyncio.run(PgStreamStore(connect).ready()) is True
    assert conn.executed == [(journal.READY_SQL, None)] and conn.closed
    assert journal.READY_SQL.startswith("set local statement_timeout = 2000; select ")
    assert "journal_usage" not in journal.READY_SQL


def _as(database: str, role: str):
    """A connection per call as `role` (the gateway's two logins: 0004's `service_role`
    and 0021's dedicated `infrx_runtime`), with no session statement timeout of its own."""
    async def connect():
        import psycopg
        conn = await psycopg.AsyncConnection.connect(pgharness.dsn(database), autocommit=True,
                                                     prepare_threshold=None)
        await conn.execute(f"set role {role}")
        return conn
    return connect


@needs_pg
@pytest.mark.parametrize("role", ["service_role", "infrx_runtime"])
def test_journal_ready_pg__answers_on_the_migrated_schema_on_the_primary_key(role):
    """On 0001-0059+ the probe answers as either gateway login (0021 grants `infrx_runtime`
    SELECT on the journal), and its lookup plans on `stream_chunks_pkey` - a constant-cost
    descent, never a scan of the journal (whose pruned prefix leaves dead pages a `limit 1`
    seq scan reads). Oracle: a missing grant, a seq scan or a server error fails it."""
    database = pgstore.fresh_database()
    assert asyncio.run(PgStreamStore(_as(database, role)).ready()) is True
    lookup = journal.READY_SQL.split("; ", 1)[1]
    with pgharness.connect(database) as owner:
        plan = owner.execute(f"explain (format json) {lookup}").fetchone()[0]
    assert "stream_chunks_pkey" in str(plan) and "Seq Scan" not in str(plan), plan


@needs_pg
def test_journal_ready_pg__a_stalled_journal_is_cancelled_by_the_server_within_its_bound():
    """While another transaction holds the journal (a stalled database, as in E4C run 2),
    `ready()` is cancelled by the SERVER at its 2 s bound - it fails, it does not hold a
    pooled connection for the session's 15 s statement timeout. Oracle: without the bound
    the lookup waits on the lock until the client gives up at 8 s."""
    from psycopg import errors as pg

    database = pgstore.fresh_database()
    with pgharness.connect(database, autocommit=False) as holder:
        holder.execute("lock table infrx.stream_chunks in access exclusive mode")
        started, answer = time.monotonic(), None
        try:
            asyncio.run(asyncio.wait_for(PgStreamStore(_as(database, "service_role")).ready(),
                                         8))
        except Exception as failure:      # noqa: BLE001 - the assertion names what it was
            answer = failure
        elapsed = time.monotonic() - started
        holder.rollback()
    assert isinstance(answer, pg.QueryCanceled), repr(answer)
    assert 1.5 <= elapsed < 4.0, elapsed


def test_probe_bounds__a_pool_wait_plus_the_server_bound_fits_inside_the_probe_bound():
    """The bounds' order under a database stall (the api-probe ruling request): a probe
    waits at most the pool's connect timeout (5 s) for a connection, then at most
    `READY_TIMEOUT_MS` (2 s) for the server, so it fails by a typed error before
    `PROBE_TIMEOUT_S` (10 s) and never holds a connection for the 15 s statement timeout.
    Oracle: a widened server bound or a shortened probe bound breaks the order."""
    from infrx.config import DEPLOYMENT_DEFAULTS

    connect_s = DEPLOYMENT_DEFAULTS.database_pool_connect_timeout_s
    ready_s = journal.READY_TIMEOUT_MS / 1000
    assert connect_s + ready_s < readiness.PROBE_TIMEOUT_S
    assert journal.READY_TIMEOUT_MS < DEPLOYMENT_DEFAULTS.database_pool_statement_timeout_ms


def test_probe_wiring__the_pilot_composes_the_journal_probe_on_ready():
    """WR-PROBE-1 composed: `pilot`'s names are `readiness`'s objects, and the journal probe
    `build_ingress_deps` registers answers from `ready()` while `usage()` hangs. Oracle: the
    base's `journal_check` (on `usage()`) reads False here."""
    from infrx.gateway import pilot

    assert (pilot.Probe, pilot.journal_check, pilot.PROBE_TIMEOUT_S) == (
        readiness.Probe, readiness.journal_check, readiness.PROBE_TIMEOUT_S)
    stream = Stream()
    assert pilot.Probe(pilot.journal_check(stream), timeout_s=0.5)() is True
    assert stream.asked == ["ready"]
