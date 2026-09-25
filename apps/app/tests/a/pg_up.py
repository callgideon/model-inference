"""A2's task-local PostgreSQL for tests/a/grant-pg.test.ts: tests/d's harness on the `app-a2`
reservation (container infrx-app-a2-postgres, 127.0.0.1:55460), every migration + the shim,
then held open until SIGTERM/SIGINT; the container is removed on exit (the harness's atexit).

    cd apps/infrx-api && INFRX_D_TASK=app-a2 uv run --frozen python ../app/tests/a/pg_up.py

Prints `DSN <dsn>` once ready. The password is the harness's fixed local constant.
"""
import os
import signal
import sys
import time
from pathlib import Path

assert os.environ.get("INFRX_D_TASK") == "app-a2", "run with INFRX_D_TASK=app-a2"
sys.path[:0] = [str(Path.cwd()), str(Path.cwd() / "tests" / "d")]
import pgharness  # noqa: E402
from infrx.state import migrations  # noqa: E402

signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
pgharness.ensure()
pgharness.recreate(pgharness.DATABASE)
pgharness.apply(pgharness.DATABASE, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
print("DSN", pgharness.dsn(), flush=True)
try:
    while True:
        time.sleep(3600)
except KeyboardInterrupt:
    pass
