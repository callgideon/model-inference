"""I8 slice 1: the pooler budget holds at concurrent startup and peak, and which of the
runtime's query patterns survive transaction pooling (6543). Needs Linux + docker
(tests/i/pooler.py: PostgreSQL with the real schema behind two PgBouncers).

Failure oracles, per case:
* budget: the computed peak must be exactly what the session pooler admits - today's
  defaults (10/10) are computed FAIL *and* refused by the stand-in at the 16th client, a
  budget that passes is admitted whole. A budget script that under-counts a pool, or a
  limit model that is wrong, fails one side.
* session state: `pilot.configure_connection`'s own statements are shown lost for their
  client and leaked to another client - the defect WR-I8-1 fixes.
* prepared statements: psycopg's default auto-prepare fails once its connection is served
  by another server; `prepare_threshold=None` does not.
* transaction-scoped patterns (the admission lock, SET LOCAL, role defaults) are shown
  safe, and a session advisory lock unsafe.
"""
from __future__ import annotations

import asyncio
import re
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

from infrx import config
from infrx.gateway import pilot

from .pooler import PG_DIRECT, SESSION, TXN

API = Path(__file__).resolve().parents[2]
REPO = API.parents[1]
BUDGET = runpy.run_path(str(REPO / "infra" / "runbooks" / "pool_budget.py"))
UNITS = API / "deploy"


def _settings(dsn: str, **knobs: str):
    env = {"DATABASE_URL": dsn, **{k.upper(): v for k, v in knobs.items()}}
    return SimpleNamespace(pilot=SimpleNamespace(database_url=dsn),
                           deployment=config.deployment_from_env(env))


def _sync(dsn: str, **kw):
    import psycopg
    return psycopg.connect(dsn, autocommit=True, **kw)


# --- 1. the budget, acted out on the session pooler ---------------------------------------
async def _act_out(dsn: str, peak_rows: dict[str, int], pool_max: str) -> int:
    """Open the gateway's and the worker's pools the way the runtime does (the real
    `pilot.connection_pool`, its `configure` hook included), concurrently with the gateway's
    startup probe, then hold every pool at its maximum plus the reserved clients. Returns
    the number of client connections held at once."""
    import psycopg
    gw_pool, gw_connect = pilot.connection_pool(_settings(dsn, database_pool_max_size=pool_max))
    wk_pool, _ = pilot.connection_pool(_settings(dsn, database_pool_max_size=pool_max))
    held, extra = {gw_pool: [], wk_pool: []}, []
    try:
        # startup: the probe's own connection (pool still closed) while the worker opens
        probe, _ = await asyncio.gather(gw_connect(), wk_pool.open(wait=True, timeout=10))
        extra.append(probe)
        await gw_pool.open(wait=True, timeout=10)
        await probe.close()
        extra.remove(probe)
        # peak: both pools at max, plus the reserved clients of this pooler mode
        for pool in (gw_pool, wk_pool):
            held[pool] += await asyncio.gather(*(pool.getconn(timeout=10)
                                                 for _ in range(pool.max_size)))
        for _ in range(peak_rows["reserved"]):
            extra.append(await psycopg.AsyncConnection.connect(dsn, autocommit=True))
        everyone = held[gw_pool] + held[wk_pool] + extra
        for conn in everyone:
            await conn.execute("select 1")
        return len(everyone)
    finally:
        for conn in extra:
            await conn.close()
        for pool, conns in held.items():
            for conn in conns:
                await pool.putconn(conn)
        await asyncio.gather(gw_pool.close(), wk_pool.close())


def _session_rows(result) -> dict[str, int]:
    rows = [r for r in result["table"] if r["mode"] == "session"]
    pools = sum(r["peak"] for r in rows if "pool" in r["client"])
    return {"pools": pools, "reserved": result["verdicts"]["session"]["peak"] - pools}


def test_ops_continuous__the_computed_budget_is_what_the_session_pooler_admits(i8_stack):
    import psycopg
    import psycopg_pool
    dsn = i8_stack.dsn(SESSION)
    passing = BUDGET["budget"]({"DATABASE_POOL_MAX_SIZE": "6"}, UNITS, runtime_mode="session")
    assert passing["ok"] and passing["verdicts"]["session"]["peak"] == 13
    rows = _session_rows(passing)
    held = asyncio.run(_act_out(dsn, rows, "6"))
    assert held == passing["verdicts"]["session"]["peak"]           # admitted whole

    failing = BUDGET["budget"]({}, UNITS, runtime_mode="session")   # today's defaults
    assert not failing["ok"] and failing["verdicts"]["session"]["peak"] == 21
    with pytest.raises((psycopg.OperationalError, psycopg_pool.PoolTimeout)):
        asyncio.run(_act_out(dsn, _session_rows(failing), "10"))
    # the limit model itself: 15 clients admitted, the 16th refused by the pooler
    clients = [_sync(dsn) for _ in range(15)]
    with pytest.raises(psycopg.OperationalError, match="client connections exceeded"):
        _sync(dsn)
    for conn in clients:
        conn.close()


def test_ops_continuous__the_budget_counts_every_gateway_process(tmp_path):
    """The gateway's uvicorn `--workers` multiplies its pool: a unit with 2 workers doubles
    the gateway rows (a budget that read a constant would not notice)."""
    unit = (UNITS / "marlin2b-gateway.service").read_text()
    (tmp_path / "marlin2b-gateway.service").write_text(unit.replace("--workers 1", "--workers 2"))
    one = BUDGET["budget"]({"DATABASE_POOL_MAX_SIZE": "4"}, UNITS, runtime_mode="session")
    two = BUDGET["budget"]({"DATABASE_POOL_MAX_SIZE": "4"}, tmp_path, runtime_mode="session")
    assert two["verdicts"]["session"]["peak"] - one["verdicts"]["session"]["peak"] == 4
    assert BUDGET["mode_of"]("postgresql://u:p@h:6543/d") == "transaction"
    assert BUDGET["mode_of"]("host=h port=5432 dbname=d") == "session"
    assert BUDGET["main"](["--runtime-port", "5432"]) == 1                 # today's defaults
    assert BUDGET["main"](["--runtime-port", "5432", "--set", "DATABASE_POOL_MAX_SIZE=6"]) == 0


# --- 2. transaction pooling: what the runtime's patterns do ----------------------------
@pytest.fixture
def txn(i8_stack):
    """A transaction-mode pooler with fresh server connections (a case that leaks session
    state must not hand it to the next case), and its DSN."""
    i8_stack.write_bouncers()
    return i8_stack.dsn(TXN)


PID = "select pg_backend_pid()"


class divert:
    """Make the next statement of a client land on the server connection it did NOT use
    last (`avoid`, a backend pid): two helpers take both server connections (the stand-in
    has two), the one on the other server lets go, and `holder` - on `avoid` - keeps its
    transaction open until the block ends. A real pooler does this at random; here it is
    deterministic."""

    def __init__(self, dsn: str, avoid: int) -> None:
        self.dsn, self.avoid = dsn, avoid

    def __enter__(self):
        one, two = (_sync(self.dsn, prepare_threshold=None) for _ in range(2))
        pids = []
        for conn in (one, two):
            conn.execute("begin")
            pids.append(conn.execute(PID).fetchone()[0])
        assert self.avoid in pids, "the stand-in has exactly two server connections"
        self.holder, other = (one, two) if pids[0] == self.avoid else (two, one)
        other.execute("commit"), other.close()
        return self.holder

    def __exit__(self, *exc):
        self.holder.execute("commit"), self.holder.close()


def test_ops_continuous__session_state_is_lost_and_leaked_on_the_transaction_pooler(txn):
    a = _sync(txn, prepare_threshold=None)
    x = a.execute(PID).fetchone()[0]
    # pilot.configure_connection's statements, verbatim, as the pool runs them (server x)
    asyncio.run(_configure_sync_equivalent(a))
    ask = "select current_user, current_setting('statement_timeout'), pg_backend_pid()"
    with divert(txn, avoid=x) as holder:
        seen_by_a = a.execute(ask).fetchone()
        seen_by_other = holder.execute(ask).fetchone()
    a.close()
    assert seen_by_a[:2] == ("postgres", "0") and seen_by_a[2] != x    # lost
    assert seen_by_other == ("service_role", "15s", x)                  # leaked to a stranger

    # state/jobstore.py `connector` (the operator CLI's and the tests' Connect): a fresh
    # connection per operation whose `set role service_role` is its own transaction - the
    # operation that follows can be served by a server that never saw it
    from infrx.state.jobstore import connector

    async def one_operation():
        conn = await connector(txn)()
        try:
            y = (await (await conn.execute(PID)).fetchone())[0]
            with divert(txn, avoid=y):
                return await (await conn.execute("select current_user")).fetchone()
        finally:
            await conn.close()
    assert asyncio.run(one_operation()) == ("postgres",)


async def _configure_sync_equivalent(conn) -> None:
    statements = []

    class Recorder:
        async def execute(self, sql):
            statements.append(sql)
    await pilot.configure_connection(15_000)(Recorder())
    assert statements == ["set role service_role", "set statement_timeout = 15000"]
    for sql in statements:
        conn.execute(sql)


def _alternate(dsn: str, conn, query: str, params=(), times: int = 12) -> list[int]:
    """Run `query` (whose first column is pg_backend_pid()) `times` times, each on the
    other server connection. Returns the pids, which alternate."""
    pids = [conn.execute(query, params).fetchone()[0]]
    for _ in range(times - 1):
        with divert(dsn, avoid=pids[-1]):
            pids.append(conn.execute(query, params).fetchone()[0])
    return pids


def test_ops_continuous__auto_prepared_statements_break_on_the_transaction_pooler(txn):
    import psycopg
    query = "select pg_backend_pid(), count(*) from infrx.jobs where state = %s"
    default = _sync(txn)                                     # psycopg's prepare_threshold=5
    with pytest.raises((psycopg.errors.InvalidSqlStatementName,
                        psycopg.errors.DuplicatePreparedStatement)):
        _alternate(txn, default, query, ("queued",))
    default.close()
    unprepared = _sync(txn, prepare_threshold=None)          # WR-I8-1's setting
    pids = _alternate(txn, unprepared, query, ("queued",))
    assert len(set(pids)) == 2 and all(p != q for p, q in zip(pids, pids[1:]))
    unprepared.close()


def test_ops_continuous__transaction_scoped_patterns_survive_the_transaction_pooler(
        i8_stack, txn):
    direct = _sync(i8_stack.dsn(PG_DIRECT))
    a = _sync(txn, prepare_threshold=None)
    # 0011's admission lock: pg_advisory_xact_lock inside one statement - released at its end
    a.execute("select pg_advisory_xact_lock(infrx.admission_lock_key())")
    held = direct.execute("select count(*) from pg_locks where locktype = 'advisory'").fetchone()
    assert held == (0,)
    # SET LOCAL inside an explicit transaction: in force for it, gone after, never leaked
    with a.transaction():
        a.execute("set local role service_role")
        assert a.execute("select current_user").fetchone() == ("service_role",)
    b = _sync(txn, prepare_threshold=None)        # LIFO: the server `a` just released
    assert b.execute("select current_user").fetchone() == ("postgres",)
    b.close()
    # a SESSION advisory lock is not safe: the unlock runs on another server connection
    x = a.execute("select pg_backend_pid(), pg_advisory_lock(880088)").fetchone()[0]
    with divert(txn, avoid=x):
        assert a.execute("select pg_advisory_unlock(880088)").fetchone() == (False,)
    a.close()
    # the mitigation for a timeout: a login role's own default applies on every server
    # connection the pooler opens for it, whichever client is served
    direct.execute("drop role if exists infrx_i8_login")
    direct.execute("create role infrx_i8_login login password 'infrx-i8-local'")
    direct.execute("alter role infrx_i8_login set statement_timeout = '15s'")
    direct.close()
    i8_stack.users["infrx_i8_login"] = "infrx-i8-local"
    i8_stack.write_bouncers()
    login = i8_stack.dsn(TXN, "infrx_i8_login")
    c = _sync(login, prepare_threshold=None)
    ask = "select pg_backend_pid(), current_setting('statement_timeout')"
    pids = _alternate(login, c, ask, times=4)
    assert len(set(pids)) == 2
    for _ in range(2):
        assert c.execute(ask).fetchone()[1] == "15s"
    c.close()


# --- 3. the runtime's own patterns, pinned --------------------------------------------
RUNTIME = API / "infrx"
MIGRATIONS = REPO / "apps" / "app" / "supabase" / "migrations"
# WR-I8-1: the only session-scoped statements the runtime sends today. Each is unsafe on
# 6543 (shown above); the list shrinks when the wiring request lands, and a NEW one fails.
KNOWN_SESSION_SET = {("gateway/pilot.py", "set role service_role"),
                     ("gateway/pilot.py", "set statement_timeout = "),
                     ("state/jobstore.py", "set role service_role")}


def test_ops_continuous__the_runtime_sends_no_other_session_only_statement():
    found = set()
    for path in RUNTIME.rglob("*.py"):
        text = path.read_text()
        rel = str(path.relative_to(RUNTIME))
        for m in re.finditer(r'execute\(\s*f?"(set\s+(?!local\b|transaction\b)[^"{]*)', text, re.I):
            found.add((rel, m.group(1)))
        for banned in (r'["\']\s*(?:un)?listen\s', r"pg_advisory_lock\(", r"create\s+temp"):
            assert not re.search(banned, text, re.I), f"{rel}: {banned}"
    assert found == KNOWN_SESSION_SET
    for sql in MIGRATIONS.glob("*.sql"):
        text = re.sub(r"--.*", "", sql.read_text())
        assert not re.search(r"pg_advisory_lock\(|pg_try_advisory_lock\(", text), sql.name
        assert not re.search(r"\bLISTEN\s+\w", text, re.I), sql.name


@pytest.mark.xfail(strict=True, reason="WR-I8-1: pilot.connection_pool SETs role/timeout per "
                                       "session and auto-prepares; unsafe on 6543 until fixed")
def test_ops_continuous__the_composed_runtime_pool_is_safe_on_the_transaction_pooler(txn):
    dsn = txn

    async def run():
        pool, connect = pilot.connection_pool(_settings(dsn))
        await pool.open(wait=True, timeout=10)
        answers, last = [], None
        ask = ("select pg_backend_pid(), current_user, current_setting('statement_timeout'), "
               "count(*) from infrx.jobs where state = %s")
        try:
            for _ in range(12):
                conn = await connect()
                if last is None:
                    row = await (await conn.execute(ask, ("queued",))).fetchone()
                else:
                    with divert(dsn, avoid=last):
                        row = await (await conn.execute(ask, ("queued",))).fetchone()
                await conn.close()
                last = row[0]
                answers.append(row[1:3])
        finally:
            await pool.close()
        return answers
    answers = asyncio.run(run())
    assert len(set(answers)) == 1 and answers[0][1] != "0"
