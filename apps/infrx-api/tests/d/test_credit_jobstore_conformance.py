#!/usr/bin/env python3
"""D5 item 9c: the exported CREDIT JobStore conformance suite (`credit_jobstore_cases()`, the
F2P wire-in) against the REAL store, through `pgtesting.make_credit_jobstore_factory` (the v1
rig plus the v2 fixture world as rows and the `credit_balance`/`credit_grant`/
`register_credential`/`publish_rate_card` hooks). Partitioned like the v1 suite: a case NOT
in `PENDING` must pass; a pending one is strict-xfail with its owner and the exception it
dies of; a missing hook is a skip (none is).

    INFRX_D_TASK=d5 uv run --frozen pytest -q tests/d/test_credit_jobstore_conformance.py
"""
from __future__ import annotations

import asyncio

import pytest
from infrx.contracts import errors
from infrx.contracts.conformance import MissingHook
from infrx.contracts.conformance.v2_contracts import credit_jobstore_cases
from infrx.state import migrations, pgtesting

from . import pgharness, pgstore

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")

_G1R = ("G1R request 2(c), unassigned: PostgreSQL admission of a provider_dev credential on "
        "its own dev endpoint is not built (0011 answers a provider_dev key `not_found`)")
PENDING: dict[str, str] = {
    "credit_admit__refusals_leave_no_job_and_no_hold": _G1R,
    "credit_settle__an_unknown_usage_hold_is_reconciled_on_the_credit_wallet": _G1R,
    "credit_admit__a_replay_is_pinned_and_never_crosses_regimes":
        "0011 (D2 review M7) answers a key of the other regime `state_conflict`; the fake "
        "answers `idempotency_conflict` (both 409) - a contract delta for F/the coordinator",
}
RAISES = {"credit_admit__refusals_leave_no_job_and_no_hold": errors.NotFound,
          "credit_settle__an_unknown_usage_hold_is_reconciled_on_the_credit_wallet":
              errors.NotFound,
          "credit_admit__a_replay_is_pinned_and_never_crosses_regimes": errors.StateConflict}

CASES = credit_jobstore_cases()
factory = pgtesting.make_credit_jobstore_factory(pgstore.fresh_database, pgharness.dsn,
                                                 migrations.SEED_MARLIN.read_text())


def _param(case):
    reason = PENDING.get(case.__name__)
    marks = [pytest.mark.xfail(strict=True, reason=reason,
                               raises=RAISES[case.__name__])] if reason else []
    return pytest.param(case, id=case.__name__, marks=marks)


def test_the_pending_list_names_only_real_cases() -> None:
    names = {case.__name__ for case in CASES}
    assert set(PENDING) <= names and set(RAISES) == set(PENDING), sorted(set(PENDING) - names)
    print(f"{len(CASES)} cases: {len(CASES) - len(PENDING)} must pass, "
          f"{len(PENDING)} pending (strict xfail)")


@pytest.mark.parametrize("case", [_param(case) for case in CASES])
def test_credit_jobstore_conformance_on_postgres(case) -> None:
    try:
        asyncio.run(case(factory))
    except MissingHook as missing:
        pytest.skip(f"missing optional hook {missing.hook!r} (not a pass)")
