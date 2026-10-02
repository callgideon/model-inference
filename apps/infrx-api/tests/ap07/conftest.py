"""AP-07's worlds: LAB-ACCESS's two (`tests/l/access/worlds.py`) on the lane's key (`INFRX_D_TASK=ap7`:
PostgreSQL 57559). The `pg` half skips visibly without Docker."""
from tests.l.access.conftest import (fake_world, pg_template, pg_world,  # noqa: F401
                                     pytest_configure, world)

# The ap7 stack's fixtures (`test_trace_stack.py`), shared with `test_eligible.py`.
from tests.ap07.test_trace_stack import bucket, clickhouse, stack  # noqa: E402, F401
