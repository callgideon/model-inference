#!/usr/bin/env python3
"""D2 item 6: the exported v1 JobStore conformance suite against the REAL store.

Every case of `jobstore_cases()` runs on `pgstore.factory` (a fresh migrated, seeded,
FROZEN PostgreSQL database per harness). The partition is explicit and strict:

* a case NOT in `PENDING` must pass;
* a case in `PENDING` is `xfail(strict=True)` with the task that owns what it needs - if
  it starts passing, the run fails until the list is corrected (a pending list cannot
  quietly go stale);
* a case needing an optional hook this rig does not provide is SKIPPED naming the hook
  (R32: a skip is never a pass): `publish` is the StreamStore's (D4), `unsettleable`
  the settlement backlog (D5).

    uv run --frozen pytest -q tests/d/test_jobstore_conformance.py
    INFRX_D1_IMAGE=supabase uv run --frozen pytest -q tests/d/test_jobstore_conformance.py
"""
from __future__ import annotations

import asyncio

import pytest
from infrx.contracts.conformance import MissingHook, jobstore_cases

from . import pgharness, pgstore

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")

_D3_CLAIM = "D3: needs JobStore.claim (the inference lease)"
_D3_CLAIM_D5 = "D3 claim, then D5 complete (the settling transaction)"
_D3_RECOVER = "D3: needs JobStore.recover (the reaper)"

PENDING: dict[str, str] = {
    # The tombstone cases settle a job first: D3 claim + D5 complete. The TTL itself is
    # D2's and is proved on SQL in tests/d/test_admission.py (idempotency check).
    "dur_admit__expired_mapping_is_explicit_never_a_second_billable_job": _D3_CLAIM_D5,
    "dur_admit__an_active_jobs_mapping_never_expires": _D3_CLAIM_D5,
    "dur_admit__the_tombstone_ttl_runs_from_the_terminal_state": _D3_CLAIM_D5,
    "dur_admit__tombstone_is_retained_after_the_terminal_state": _D3_CLAIM_D5,
    # Cannot pass on ANY real store as written: the case pairs KEY_B with ORG_A and KEY_A
    # with ORG_B, and in PostgreSQL a key belongs to exactly one organization (the
    # composite FK `jobs_key_belongs_to_org`, R59-6), so admission answers invalid_api_key.
    # The per-scope limits themselves pass in tests/d/test_admission.py (capacity check).
    "dur_cap__total_org_and_key_limits_reject_with_retry_guidance":
        "F2 conformance: the case reuses one key across two organizations",
    "dur_fence__claim_increments_the_generation_from_the_database_clock": _D3_CLAIM,
    "dur_fence__preparation_is_claimed_and_fenced_like_execution": _D3_RECOVER,
    "dur_fence__load_work_is_fenced_and_hands_out_nothing_otherwise":
        "D3: needs JobStore.load_work (fenced like claim)",
    "dur_output__a_lost_preparation_worker_is_reaped_within_bounds": _D3_RECOVER,
    "dur_output__a_heartbeating_preparation_worker_is_terminalized_on_time":
        "D3: needs JobStore.heartbeat",
    "dur_fence__another_worker_at_the_same_generation_is_still_fenced": _D3_CLAIM,
    "dur_fence__a_lease_is_a_fencing_token_not_a_record": _D3_CLAIM,
    "dur_fence__an_expired_lease_can_neither_renew_nor_settle": _D3_CLAIM,
    "dur_fence__an_overdue_inference_lease_terminalizes_in_the_same_call": _D3_CLAIM,
    "dur_fence__a_stale_generation_is_rejected": _D3_CLAIM,
    "dur_output__recovery_requeues_only_before_publication": _D3_CLAIM,
    "dur_output__every_requeued_candidate_carries_the_right_kind": _D3_CLAIM,
    "dur_output__prepublication_retries_are_bounded": _D3_CLAIM,
    "dur_output__queue_wait_does_not_restart_on_a_requeue": _D3_CLAIM,
    "dur_output__an_accepted_job_keeps_its_admission_budgets": _D3_RECOVER,
    "dur_output__a_late_preparation_worker_finds_a_terminal_job": _D3_RECOVER,
    "dur_output__phase_deadlines_are_persisted_at_each_transition": _D3_CLAIM,
    "dur_output__no_phase_deadline_outlives_the_accepted_deadline": _D3_CLAIM,
    "dur_output__the_generation_deadline_ends_a_running_attempt": _D3_CLAIM,
    "dur_output__queue_time_is_time_spent_queued": _D3_CLAIM,
    "dur_output__a_job_past_its_queue_budget_is_not_claimable": _D3_CLAIM,
    "dur_output__the_absolute_deadline_bounds_recovery": _D3_CLAIM,
    **{name: _D3_CLAIM_D5 for name in (
        "dur_settle__one_settlement_with_exact_decimals",
        "dur_settle__the_store_rounds_half_up_once",
        "dur_settle__a_price_change_never_undersizes_the_hold",
        "dur_settle__duplicate_completion_is_idempotent_then_conflicts",
        "dur_settle__cancel_and_complete_race_has_a_single_winner",
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
        "dur_outbox__every_transition_emits_its_projection")},
}

CASES = jobstore_cases()


def _param(case):
    reason = PENDING.get(case.__name__)
    marks = [pytest.mark.xfail(strict=True, reason=reason)] if reason else []
    return pytest.param(case, id=case.__name__, marks=marks)


def test_the_pending_list_names_only_real_cases() -> None:
    names = {case.__name__ for case in CASES}
    assert set(PENDING) <= names, sorted(set(PENDING) - names)
    print(f"{len(CASES)} cases: {len(CASES) - len(PENDING)} must pass, "
          f"{len(PENDING)} pending (strict xfail) - skips reported separately")


@pytest.mark.parametrize("case", [_param(case) for case in CASES])
def test_jobstore_conformance_on_postgres(case) -> None:
    try:
        asyncio.run(case(pgstore.factory))
    except MissingHook as missing:
        pytest.skip(f"missing optional hook {missing.hook!r} (not a pass)")
