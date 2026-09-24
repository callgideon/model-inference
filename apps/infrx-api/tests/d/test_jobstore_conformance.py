#!/usr/bin/env python3
"""D2 item 6: the exported v1 JobStore conformance suite against the REAL store.

Every case of `jobstore_cases()` runs on `pgstore.factory` (a fresh migrated, seeded,
FROZEN PostgreSQL database per harness). The partition is explicit and strict:

* a case NOT in `PENDING` must pass;
* a case in `PENDING` is `xfail(strict=True)` with the task that owns what it needs - if
  it starts passing, the run fails until the list is corrected (a pending list cannot
  quietly go stale);
* a case needing an optional hook this rig does not provide is SKIPPED naming the hook
  (R32: a skip is never a pass). D4: none is - `stream` is the real `PgStreamStore` and
  `publish` one chunk through its `append`.
* D5: no `RACY` partition is left - the cancel/complete race is strict (see `PENDING`).

    uv run --frozen pytest -q tests/d/test_jobstore_conformance.py
    INFRX_D1_IMAGE=supabase uv run --frozen pytest -q tests/d/test_jobstore_conformance.py
"""
from __future__ import annotations

import asyncio

import pytest
from infrx.contracts import errors
from infrx.contracts.conformance import MissingHook, jobstore_cases

from . import pgharness, pgstore

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")

#: D5: every case that needed the settlement passes - the former `_D5` cases (23 at D3,
#: unchanged by D4), the cancel-cause case (0018's `infrx.cancel` records the cause), G2's
#: R91 lookup case (0018's `infrx.idempotency_lookup`) and the former `RACY` case, now
#: strict whichever transaction wins (complete settles, or cancel commits first and
#: complete is `already_terminal`). Only a case NO real store can pass as written stays
#: pending, with its reason; RAISES names that one case only (the integration head's
#: cancel-cause and R91 entries, 75cd7bb/f52308a, are dropped with their PENDING lines).
PENDING: dict[str, str] = {
    # Cannot pass on ANY real store as written: the case pairs KEY_B with ORG_A and KEY_A
    # with ORG_B, and in PostgreSQL a key belongs to exactly one organization (the
    # composite FK `jobs_key_belongs_to_org`, R59-6), so admission answers invalid_api_key.
    # The per-scope limits themselves pass in tests/d/test_admission.py (capacity check).
    "dur_cap__total_org_and_key_limits_reject_with_retry_guidance":
        "F2 conformance: the case reuses one key across two organizations",
}

CASES = jobstore_cases()


#: D4 review H2: the exception each pending case must die of (the refused key for the F2
#: case), so a regression before that step fails, never xfails.
RAISES = {"dur_cap__total_org_and_key_limits_reject_with_retry_guidance": errors.InvalidApiKey}


def _param(case):
    reason = PENDING.get(case.__name__)
    marks = [pytest.mark.xfail(strict=True, reason=reason,
                               raises=RAISES[case.__name__])] if reason else []
    return pytest.param(case, id=case.__name__, marks=marks)


def test_the_pending_list_names_only_real_cases() -> None:
    names = {case.__name__ for case in CASES}
    assert set(PENDING) <= names, sorted(set(PENDING) - names)
    assert set(RAISES) == set(PENDING), "every pending case pins the exception it dies of"
    print(f"{len(CASES)} cases: {len(CASES) - len(PENDING)} must pass, "
          f"{len(PENDING)} pending (strict xfail) - skips reported separately")


@pytest.mark.parametrize("case", [_param(case) for case in CASES])
def test_jobstore_conformance_on_postgres(case) -> None:
    try:
        asyncio.run(case(pgstore.factory))
    except MissingHook as missing:
        pytest.skip(f"missing optional hook {missing.hook!r} (not a pass)")
