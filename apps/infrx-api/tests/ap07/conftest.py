"""AP-07's worlds: LAB-ACCESS's two (`tests/l/access/worlds.py`) on the lane's key (`INFRX_D_TASK=ap7`:
PostgreSQL 57559). The `pg` half skips visibly without Docker."""
import pathlib

import pytest

from tests.d import pgharness
from tests.l.access import worlds
from tests.l.access.conftest import (CASE, fake_world, pg_template,  # noqa: F401
                                     pytest_configure, world)

# The ap7 stack's fixtures (`test_trace_stack.py`), shared with `test_eligible.py`.
from tests.ap07.test_trace_stack import bucket, clickhouse, stack  # noqa: E402, F401

#: FAKE until 0066 merges: api-schema-2's `infrx.lab_withdraw_access_grant`, verbatim.
WITHDRAW_DOOR = pathlib.Path(__file__).with_name("withdraw_door.sql")


@pytest.fixture
def pg_world(pg_template):  # noqa: F811
    """LAB-ACCESS's `pg_world`, plus the 0066 withdraw door until 0066 is on the base (then
    this fixture and `withdraw_door.sql` go, and `pg_world` is imported again)."""
    pgharness.assert_ours("copy a database in")
    with pgharness.connect("postgres") as admin:
        admin.execute(f'drop database if exists "{CASE}" with (force)')
        admin.execute(f'create database "{CASE}" template "{pg_template}"')
    with pgharness.connect(CASE) as conn:
        conn.execute(WITHDRAW_DOOR.read_text())
        yield worlds.PgWorld(conn, pgharness.dsn(CASE))
