#!/usr/bin/env python3
"""D10: F2C.a's exported lifecycle suite (`conformance/lifecycle.py`: UPLOAD-RESTART,
ADMISSION-READY, RETENTION-DURABLE) against the REAL adapter, `state.lifecycle.PgLifecycle`,
through `pgtesting.make_lifecycle_factory` (the CREDIT world plus the suite's hooks). A case
NOT in `PENDING` must pass; a pending one is strict-xfail with the slice that builds it.

    INFRX_D_TASK=d10 uv run --frozen pytest -q tests/d/test_lifecycle_conformance.py
"""
from __future__ import annotations

import asyncio

import pytest
from infrx.contracts.conformance import MissingHook
from infrx.contracts.conformance.lifecycle import cases
from infrx.state import migrations, pgtesting

from . import pgharness, pgstore

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")

PENDING: dict[str, str] = {}

CASES = cases()
factory = pgtesting.make_lifecycle_factory(pgstore.fresh_database, pgharness.dsn,
                                           migrations.SEED_MARLIN.read_text())


def _param(case):
    reason = PENDING.get(case.__name__)
    marks = [pytest.mark.xfail(strict=True, reason=reason)] if reason else []
    return pytest.param(case, id=case.__name__, marks=marks)


def test_the_pending_list_names_only_real_cases() -> None:
    assert set(PENDING) <= {case.__name__ for case in CASES}


@pytest.mark.parametrize("case", [_param(case) for case in CASES])
def test_lifecycle_conformance_on_postgres(case) -> None:
    try:
        asyncio.run(case(factory))
    except MissingHook as missing:
        pytest.skip(f"missing optional hook {missing.hook!r} (not a pass)")
