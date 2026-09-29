"""The one docker stack the I8 pooler/privilege/monitor cases share (tests/i/pooler.py)."""
from __future__ import annotations

import pytest

from . import pooler


@pytest.fixture(scope="session")
def i8_stack():
    why = pooler.unavailable()
    if why:
        pytest.skip(f"NOT RUN (needs Linux + docker): {why}")
    stack = pooler.Stack()
    try:
        yield stack.up()
    except BlockingIOError as busy:
        # I-HARNESS-KEY: the exclusive i8 lock is held by another run (another lane, or the
        # coordinator). Its cases are NOT RUN here, never failed - a failure would also fail
        # the tests/i mutant list's pristine baseline and refuse every mutant.
        pytest.skip(f"NOT RUN (i8 harness busy): another run holds the exclusive i8 lock "
                    f"/tmp/infrx-i8-postgres-{pooler.PORTS[pooler.PG]}.lock ({busy})")
    finally:
        stack.down()
