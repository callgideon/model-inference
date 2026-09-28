"""The LAB-ACCESS worlds (`tests/l/access/worlds.py`): every case taking `world` runs on the
fake of lab-sql's RPC seam and, marked `pg`, on `PgAccessStore` over the lane's task-local
PostgreSQL (`INFRX_D_TASK=l4`: port 57503) with the 2+2+1 role matrix."""
from tests.l.access.conftest import (fake_world, pg_template, pg_world,  # noqa: F401
                                     pytest_configure, world)
