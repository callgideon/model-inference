"""T2I WR-3: D5's pins lookup (`shipper.PinsLookup`) on PostgreSQL, and the one-statement
helper the trace relays share.

A CREDIT job carries the identities D5 resolved at admission in its own columns (0006); the
lookup reads them for the organization that owns the job only. A legacy USD job, another
organization's request or no job answers None. A PostgreSQL failure raises
`DependencyUnavailable` - "ask again later" holds the segment, where None would ship the row
without its versions for ever (a replay inserts the same version).
"""
from __future__ import annotations

from ...contracts import errors
from ...contracts.v2.records import AdmissionPins

FIELDS = ("model_id", "requested_model", "deployment_revision_id", "serving_version_id",
          "rate_card_version", "policy_version")
PINS = (f"select {', '.join(FIELDS)} from infrx.jobs "
        "where org_id = %s and request_id = %s and accounting_regime = 'credit'")


async def pg_rows(connect, sql: str, params) -> list[tuple] | int:
    """One statement on a connection of its own: its rows, or its row count when it returns
    none. Every PostgreSQL failure, connecting included, is `DependencyUnavailable`."""
    from psycopg import Error
    try:
        conn = await connect()
        try:
            cursor = await conn.execute(sql, params)
            return await cursor.fetchall() if cursor.description else cursor.rowcount
        finally:
            await conn.close()
    except Error as failed:
        raise errors.DependencyUnavailable(f"postgres: {type(failed).__name__}") from None


class PgPins:
    def __init__(self, connect) -> None:
        self.connect = connect

    async def __call__(self, org_id: str, request_id: str) -> AdmissionPins | None:
        rows = await pg_rows(self.connect, PINS, (org_id, request_id))
        if not rows:
            return None
        return AdmissionPins(**{name: None if value is None else str(value)
                                for name, value in zip(FIELDS, rows[0])})
