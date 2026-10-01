"""W6 A5: the one-connection statement helper every PostgreSQL adapter shares.

Replaces the five `_call` copies (`feedback`, `jobstore`, `lab_access`, `lab_data` and
`lifecycle`'s variant), `PgJobStore._query`, `operations._Db.rows` and the body of
`traces.ship.pins.pg_rows`. Each statement runs on a connection of its own, closed whatever
happens (R09). A psycopg error becomes `error(failed)`, the adapter's typed refusal (None: it
is raised as is). `unreachable` (lifecycle, RV-03) makes an `OperationalError`, connecting
included, a retryable `DependencyUnavailable` - never an answer read as "nothing there".
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable

from ..contracts import errors

if TYPE_CHECKING:
    from psycopg.abc import Params

    from .jobstore import Connect

Refusal = Callable[[Exception], Exception]


@asynccontextmanager
async def connection(connect: Connect) -> AsyncIterator[Any]:
    """A fresh connection, closed on the way out even when the body raises (review CF-4:
    nothing is kept across calls); the `pool.connection()` shape A1's `PgSignup` takes."""
    conn = await connect()
    try:
        yield conn
    finally:
        await conn.close()


async def _run(connect: Connect, sql: str, params: Params, read: Callable[[Any], Awaitable[Any]],
               error: Refusal | None, unreachable: str | None, retry_after_s: int) -> Any:
    from psycopg import Error, OperationalError
    gone = OperationalError if unreachable is not None else ()
    try:
        async with connection(connect) as conn:
            try:
                return await read(await conn.execute(sql, params))
            except Error as failed:
                if error is None or isinstance(failed, gone):
                    raise
                raise error(failed) from None
    except gone:
        raise errors.DependencyUnavailable(unreachable, retry_after_s=retry_after_s) from None


async def _scalar(cursor: Any) -> Any:
    (result,) = await cursor.fetchone()
    return result


async def _rows(cursor: Any) -> list[tuple]:
    return await cursor.fetchall()


async def _rows_or_count(cursor: Any) -> list[tuple] | int:
    return await cursor.fetchall() if cursor.description else cursor.rowcount


async def call(connect: Connect, function: str, args: dict[str, Any], *,
               error: Refusal | None = None, unreachable: str | None = None,
               retry_after_s: int = 5) -> Any:
    """`select infrx.<function>(<args as one jsonb>)`: the function's one value."""
    from psycopg.types.json import Jsonb
    return await _run(connect, f"select infrx.{function}(%s)", (Jsonb(args),), _scalar,
                      error, unreachable, retry_after_s)


async def rows(connect: Connect, sql: str, params: Params = (), *,
               error: Refusal | None = None) -> list[tuple]:
    """Every row of one statement."""
    return await _run(connect, sql, params, _rows, error, None, 5)


async def rows_or_count(connect: Connect, sql: str,
                        params: Params = ()) -> list[tuple] | int:
    """Every row of one statement, or its row count when it returns none."""
    return await _run(connect, sql, params, _rows_or_count, None, None, 5)
