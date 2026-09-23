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
* D3: `RACY` cases are non-strict xfail - see its comment.

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

_D5 = "D5: needs JobStore.complete's settlement (D3's fence runs first and holds)"

#: D3: every case whose store operations are D2's or D3's passes. What is left needs the
#: settling transaction (D5) - `complete` with a lease that holds raises
#: NotImplementedError after D3's fence.
#: D4: the three former `stream` skips (dur_fence__a_deadline_binds_append_and_complete,
#: dur_fence__an_overdue_inference_lease_terminalizes_in_the_same_call,
#: dur_settle__one_unsettleable_job_does_not_stop_the_sweep) must pass on the real append,
#: and so must the `publish` cases outside this list; the two `publish` cases below still
#: reach `complete` after their journal step.
PENDING: dict[str, str] = {
    # The tombstone cases settle a job first (D5 complete). The TTL itself is D2's and is
    # proved on SQL in tests/d/test_admission.py (idempotency check).
    "dur_admit__expired_mapping_is_explicit_never_a_second_billable_job": _D5,
    "dur_admit__an_active_jobs_mapping_never_expires": _D5,
    "dur_admit__the_tombstone_ttl_runs_from_the_terminal_state": _D5,
    "dur_admit__tombstone_is_retained_after_the_terminal_state": _D5,
    # Cannot pass on ANY real store as written: the case pairs KEY_B with ORG_A and KEY_A
    # with ORG_B, and in PostgreSQL a key belongs to exactly one organization (the
    # composite FK `jobs_key_belongs_to_org`, R59-6), so admission answers invalid_api_key.
    # The per-scope limits themselves pass in tests/d/test_admission.py (capacity check).
    "dur_cap__total_org_and_key_limits_reject_with_retry_guidance":
        "F2 conformance: the case reuses one key across two organizations",
    # Its last step settles generation 2; the stale generation-1 refusal before it is D3's
    # and passes (tests/d/test_leases.py proves it on its own).
    "dur_fence__a_stale_generation_is_rejected": _D5,
    **{name: _D5 for name in (
        "dur_settle__one_settlement_with_exact_decimals",
        "dur_settle__the_store_rounds_half_up_once",
        "dur_settle__a_price_change_never_undersizes_the_hold",
        "dur_settle__duplicate_completion_is_idempotent_then_conflicts",
        "dur_settle__an_outcome_settles_only_its_own_job",
        "dur_settle__a_rejected_settlement_moves_no_money",
        "dur_settle__a_succeeded_outcome_needs_a_result_reference",
        "dur_settle__a_delivered_success_needs_authoritative_usage",
        "dur_settle__the_winning_worker_can_always_replay_its_completion",
        "dur_settle__only_three_causes_can_charge",
        "dur_settle__a_settlement_that_cannot_journal_moves_no_money",
        "dur_settle__terminalization_releases_every_reservation",
        "dur_settle__platform_failures_are_free",
        "dur_settle__usage_beyond_the_reserved_envelope_is_a_platform_failure",
        "dur_settle__stale_and_out_of_order_transitions_are_typed_conflicts",
        # D2 listed these as `publish` skips; with D3's stand-in they now reach `complete`.
        "dur_settle__unknown_usage_is_held_then_released_as_platform_absorbed",
        "dur_settle__an_unknown_usage_hold_is_never_released_on_a_callers_clock",
        "dur_outbox__every_transition_emits_its_projection")},
}

#: Non-strict: the outcome depends on which transaction wins. When `cancel` commits first
#: the case passes on D3 alone (complete is refused `already_terminal` by the fence); when
#: complete's fence wins, the settlement after it is D5's and raises. Either result is
#: reported (XPASS / XFAIL), never hidden; D3's own race proofs are tests/d/test_lease_races.py.
RACY: dict[str, str] = {
    "dur_settle__cancel_and_complete_race_has_a_single_winner":
        "D5 when complete's fence wins the race; passes on D3 when cancel wins",
}

CASES = jobstore_cases()


#: D4 review H2: the exception each pending case must die of - D5's `complete` for `_D5`,
#: the refused key for the F2 case - so a regression before that step fails, never xfails.
RAISES = {"dur_cap__total_org_and_key_limits_reject_with_retry_guidance": errors.InvalidApiKey}


def _param(case):
    reason = PENDING.get(case.__name__)
    raises = RAISES.get(case.__name__, NotImplementedError)
    marks = [pytest.mark.xfail(strict=True, reason=reason, raises=raises)] if reason else []
    if case.__name__ in RACY:
        marks = [pytest.mark.xfail(strict=False, reason=RACY[case.__name__],
                                   raises=NotImplementedError)]
    return pytest.param(case, id=case.__name__, marks=marks)


def test_the_pending_list_names_only_real_cases() -> None:
    names = {case.__name__ for case in CASES}
    assert set(PENDING) | set(RACY) <= names, sorted((set(PENDING) | set(RACY)) - names)
    assert not set(PENDING) & set(RACY)
    print(f"{len(CASES)} cases: {len(CASES) - len(PENDING) - len(RACY)} must pass, "
          f"{len(PENDING)} pending (strict xfail), {len(RACY)} racy (non-strict) - "
          f"skips reported separately")


@pytest.mark.parametrize("case", [_param(case) for case in CASES])
def test_jobstore_conformance_on_postgres(case) -> None:
    try:
        asyncio.run(case(pgstore.factory))
    except MissingHook as missing:
        pytest.skip(f"missing optional hook {missing.hook!r} (not a pass)")
