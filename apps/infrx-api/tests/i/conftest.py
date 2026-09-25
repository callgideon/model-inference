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
    finally:
        stack.down()
