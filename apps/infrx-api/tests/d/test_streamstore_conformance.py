#!/usr/bin/env python3
"""D4 item 7: the exported v1 StreamStore conformance suite against the REAL store.

Every case of `streamstore_cases()` runs on `pgtesting.make_streamstore_factory` (the port is
the `PgStreamStore`, `jobs` the `PgJobStore`, one FROZEN migrated database per harness). The
partition is explicit and strict, as the JobStore suite's:

* a case NOT in `PENDING` must pass;
* a case in `PENDING` is `xfail(strict=True)` with the task that owns what it needs - if it
  starts passing, the run fails until the list is corrected;
* a case needing an optional hook this rig does not provide is SKIPPED naming the hook
  (R32: a skip is never a pass) - the rig provides every StreamStore hook, so none is.

    INFRX_D_TASK=d4 uv run --frozen pytest -q tests/d/test_streamstore_conformance.py
"""
from __future__ import annotations

import asyncio

import pytest
from infrx.contracts.conformance import MissingHook, streamstore_cases
from infrx.state import pgtesting

from . import pgharness, pgstore

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")

_D5 = "D5: needs JobStore.complete's settlement (D3's fence runs first and holds)"

#: Each of these settles a job through `complete` before its journal step; D4 writes the
#: terminal event of every OTHER terminalization, and D5's settlement will get its own from
#: the same 0017 trigger.
PENDING: dict[str, str] = {name: _D5 for name in (
    "dur_output__a_worker_cannot_forge_a_terminal_event",
    "dur_output__an_expired_journal_is_gone_not_regenerated",
    "dur_cap__stored_unexpired_bytes_keep_counting",
    "dur_output__the_terminal_event_is_written_once_with_the_settlement",
    "dur_settle__the_terminal_event_belongs_to_the_settling_transaction")}

CASES = streamstore_cases()
factory = pgtesting.make_streamstore_factory(pgstore.fresh_database, pgharness.dsn)


def _param(case):
    reason = PENDING.get(case.__name__)
    # review H2: each pending case must fail FOR its stated reason (D5's complete), so a
    # regression in the half that runs before `complete` is a failure, never an xfail
    marks = [pytest.mark.xfail(strict=True, reason=reason, raises=NotImplementedError)] \
        if reason else []
    return pytest.param(case, id=case.__name__, marks=marks)


def test_the_pending_list_names_only_real_cases() -> None:
    names = {case.__name__ for case in CASES}
    assert set(PENDING) <= names, sorted(set(PENDING) - names)
    print(f"{len(CASES)} cases: {len(CASES) - len(PENDING)} must pass, "
          f"{len(PENDING)} pending (strict xfail) - skips reported separately")


@pytest.mark.parametrize("case", [_param(case) for case in CASES])
def test_streamstore_conformance_on_postgres(case) -> None:
    try:
        asyncio.run(case(factory))
    except MissingHook as missing:
        pytest.skip(f"missing optional hook {missing.hook!r} (not a pass)")
