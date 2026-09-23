#!/usr/bin/env python3
"""R32: every invariant D1's checks claim must be killable.

One database per mutant, built from a mutated copy of the migrations in a temporary
directory; the checked-in migrations are never edited. A surviving mutant is a failed
task, not a warning.

The list is slow by contract (one database build per mutant), so the default run takes
one mutant per check plus every mutant of the money path, and the whole list runs on
demand - the same split `tests/contracts/test_mutants.py` uses:

    uv run --frozen pytest -q tests/d/test_migration_mutants.py            # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/d/test_migration_mutants.py
"""
from __future__ import annotations

import os

import pytest

from . import migration_mutants as mutation_list
from . import pgharness

ALL = mutation_list.MUTANTS
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")

#: Money, provenance and the production clock always run; the rest one per check.
ALWAYS = ("ledger_precision_rounds_history", "usage_cost_precision_rounds_history",
          "wallet_import_starts_everyone_at_zero",
          "historical_usage_enters_the_settlement_regime", "ledger_history_is_editable",
          "a_debit_on_any_outcome", "operator_entry_without_the_marker",
          "the_publication_marker_can_be_cleared",
          "the_clock_offset_works_in_production",
          "organizations_update_not_narrowed", "rpc_granted_to_authenticated",
          "profiles_operator_column_grant_widened",
          "infrx_tables_readable_by_authenticated",
          "entitlements_deny_everyone_by_default", "new_organizations_get_no_wallet",
          "wallet_money_is_not_the_domain", "no_pending_outbox_index",
          # r2: one per round-2 finding.
          "views_writable_by_browser_roles", "ledger_actor_masking_keys_on_the_marker",
          "ledger_select_is_table_wide_again", "truncate_guard_dropped",
          "api_keys_insert_is_table_wide", "views_without_security_barrier",
          "wallet_total_not_moved_by_the_ledger", "consent_revocation_can_be_undone",
          "holds_need_not_belong_to_the_job", "ledger_signs_unconstrained",
          "terminal_settlement_is_rewritable", "money_leaves_the_views_as_a_number",
          "console_rpcs_answer_for_any_organization",
          "admin_orgs_duplicates_an_org_with_two_owners",
          # r3: one per round-3 finding.
          "org_settings_without_a_tenant_predicate", "consent_history_view_without_barrier",
          "revocation_can_be_re_dated", "maximum_hold_is_mutable",
          "delivery_destination_not_composite", "ledger_sign_rules_made_valid",
          "pilot_usage_trigger_on_insert_only", "judge_money_is_a_number",
          "pending_reconciliation_counts_every_hold", "spent_keeps_its_negative_sign",
          "infrx_default_privileges_to_authenticated", "purchase_may_be_negative",
          # r4: one per round-4 finding.
          "wallet_insert_may_fund_the_row", "pilot_usage_key_tenant_unchecked",
          "console_usage_joins_any_key", "outbox_tenant_trigger_on_insert_only",
          "ledger_grant_drops_the_description",
          "failed_requests_excludes_the_boundary",
          # D1R: one per claimed check, plus the money path.
          "d1r_total_moved_without_ledger", "d1r_ledger_does_not_move_the_wallet",
          "d1r_grant_retry_mints_again", "d1r_grant_key_includes_campaign",
          "d1r_signup_grant_any_amount", "d1r_ledger_has_a_transfer",
          "d1r_hold_reserves_nothing", "d1r_hold_never_released",
          "d1r_unknown_usage_debited_later", "d1r_two_debits_per_request",
          "d1r_settled_at_another_card", "d1r_job_repinned_to_the_next_card",
          "d1r_wallet_not_the_admissions", "d1r_legacy_row_carries_credit",
          "d1r_public_dev_deployment", "d1r_rate_card_editable",
          "d1r_credit_admission_ignores_flag", "d1r_grant_callable_by_browsers",
          "d1r_service_writes_money_directly", "d1r_resolve_serves_unpriced",
          "d1r_wallet_page_shows_every_wallet", "d1r_ledger_page_without_barrier",
          "d1r_seam_renamed", "d1r_seed_card_not_provisional", "d1r_rerun_resets_flags",
          "d1r_signup_enabled_on_apply", "d1r_upgrade_imports_usd_as_credit",
          "d1r_gateway_row_needs_a_regime", "d1r_regrants_a_legacy_view",
          "d1r_credit_ledger_without_rls", "d1r_new_views_keep_default_acl",
          "d1r_summary_callable_by_anon", "d1r_audit_replays_twice", "d1r_key_unrevoked",
          "d1r_unverified_reads_verified",
          # D2: the money path, fencing, tenant isolation and the relay.
          "d2_usd_balance_unchecked", "d2_hold_rounds_to_nearest",
          "d2_replay_ignores_the_payload", "d2_admission_lock_dropped",
          "d2_terminalize_keeps_the_usd_reservation", "d2_zero_credit_hold_written",
          "d2_credit_wallet_reached_through_any_org",
          "d2_consumer_spends_on_a_private_deployment", "d2_prepare_any_generation",
          "d2_superseded_rows_dispatched", "d2_result_read_across_tenants",
          "d2_media_delete_ignores_last_use", "d2_usd_statement_reads_credit",
          "d2_credit_ledger_rounds_to_cents",
          # D3: fencing, publication/regeneration, money release and tenant isolation.
          "d3_fence_ignores_the_generation", "d3_fence_ignores_the_worker",
          "d3_fence_expiry_before_the_deadline", "d3_fence_without_the_row_lock",
          "d3_claim_without_the_row_lock", "d3_cancel_any_tenant",
          "d3_published_output_released", "d3_terminalization_keeps_the_hold",
          "d3_requeue_after_publication", "d3_retries_unbounded",
          "d3_held_unknown_may_be_rewritten", "d3_one_stuck_job_stops_the_sweep",
          "d3_service_operations_callable_by_browsers", "d3_prep_retries_unbounded",
          # D3 review fix round: the CREDIT unknown-usage path and the guard's source side.
          "d3_quarantine_releases_the_credit_hold",
          "d3_published_credit_released_not_quarantined",
          "d3_unknown_release_keeps_the_credit_hold", "d3_settled_usage_may_become_absorbed",
          # D4: fencing, publication, R30, tenant isolation, the byte budget and the clock.
          "d4_append_without_the_fence", "d4_append_accepts_a_preparation_lease",
          "d4_r29_refusal_raised_rolls_back", "d4_append_without_the_row_lock",
          "d4_terminal_event_accepted_when_last", "d4_oversize_event_stored",
          "d4_job_ceiling_ignores_the_terminal_reserve", "d4_published_not_set",
          "d4_stored_counted_beside_the_reservation", "d4_terminal_payload_from_the_old_row",
          "d4_no_terminal_event_on_cancel", "d4_terminal_event_legacy_regime_only",
          "d4_read_any_tenant", "d4_gap_is_an_empty_page", "d4_expire_on_the_callers_clock",
          "d4_prune_frees_twice", "d4_append_granted_to_authenticated",
          # D5: the settlement's money path, fencing, tenant isolation, the clock and grants.
          "d5_replay_after_the_fence", "d5_settle_before_the_fence", "d5_result_ref_unchecked",
          "d5_disconnected_not_billable", "d5_sync_deadline_billed", "d5_debit_rounds_down",
          "d5_debit_rounded_twice", "d5_debit_above_hold", "d5_wallet_total_written_directly",
          "d5_usage_debit_without_ledger_row", "d5_hold_not_moved_on_settle",
          "d5_unknown_usage_released", "d5_credit_debit_before_hold",
          "d5_credit_settles_at_the_active_card", "d5_credit_debits_the_usd_wallet",
          "d5_regimes_cross", "d5_cancel_accepts_any_cause", "d5_adjust_replay_appends",
          "d5_adjust_below_reserved", "d5_reconcile_on_callers_clock", "d5_reconcile_debits",
          "d5_reconcile_any_tenant", "d5_settle_without_the_row_lock",
          "d5_takes_the_scope_lock", "d5_grant_credit_granted_to_authenticated",
          "d5_lookup_any_org_scope", "d5_lookup_any_payload",
          # D5 review round: the operator money path and exact arithmetic
          "d5_grant_replay_ignores_wallet", "d5_grant_without_wallet_lock",
          "d5_grant_concurrent_reuse_untyped", "d5_reconcile_replay_any_request",
          "d5_credit_debit_in_float", "d5_legacy_debit_in_float")

SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in ALWAYS)

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")


def test_the_mutant_list_is_well_formed() -> None:
    """Distinct names, a known scenario and a known check for each."""
    assert len({m.name for m in ALL}) == len(ALL), "two mutants share a name"
    # review H-N5: a typo in ALWAYS would silently drop a money-path mutant from the
    # default subset (`make api-test`)
    missing = sorted(set(ALWAYS) - {m.name for m in ALL})
    assert not missing, f"ALWAYS names mutants the list does not declare: {missing}"
    for mutant in ALL:
        assert mutant.scenario in ("fresh", "upgrade", "volume", "prodlike", "credit",
                                   "upgrade05", "credit_volume", "admission"), mutant.name
        assert mutant.check in mutation_list._CHECKS, f"{mutant.name}: unknown check"
    # R83 / review H4: every anchor appears exactly as often as its mutant declares,
    # checked statically, so a stale list fails here rather than one mutant at a time.
    stale = [f"{m.name}: {mutation_list.anchor_count(m)} != {m.occurrences}" for m in ALL
             if mutation_list.anchor_count(m) != m.occurrences]
    assert not stale, "misdeclared anchors:\n  " + "\n  ".join(stale)
    covered = {m.check for m in ALL}
    uncovered = sorted(set(mutation_list._CHECKS) - covered)
    assert not uncovered, f"checks no mutant can break: {uncovered}"
    print(f"{len(ALL)} mutants over {len(covered)} checks; "
          f"{len(SELECTED)} selected ({'all' if FULL_RUN else 'subset'})")


def test_no_mutant_anchors_in_a_superseded_function_body() -> None:
    """D5 item 10b: no mutant edits a function body a later migration redefines (0018
    redefines 0016's `cancel`, `claim` and `release_aged_unknown` and 0011's
    `job_admission`; their mutants moved with them). Checked statically, like the anchors."""
    found = mutation_list.superseded(ALL)
    assert not found, "mutants on superseded bodies:\n  " + "\n  ".join(found)
    print(f"{len(ALL)} mutants: none anchored in a superseded function body")


def test_the_supersession_guard_catches_a_mutant_left_behind() -> None:
    """The guard's own kill: one of the three cancel mutants left on 0016's (superseded)
    `infrx.cancel` is reported."""
    import dataclasses
    left = dataclasses.replace(next(m for m in ALL if m.name == "d3_cancel_any_tenant"),
                               file=mutation_list.LEASES)
    assert mutation_list.anchor_count(left) == 1, "the fixture must still find its anchor"
    found = mutation_list.superseded([left])
    assert len(found) == 1 and "infrx.cancel" in found[0] and "0018" in found[0], found
    print(f"guard self-test: {found[0]}")


@pytest.mark.parametrize("mutant", SELECTED, ids=lambda m: m.name)
def test_mutant_is_killed(mutant) -> None:
    """The check that claims this invariant fails when the migration loses it.

    R40: only an assertion failure raised by the NAMED check counts. An apply or setup
    error is its own outcome and fails this test too - a migration that does not build
    proves nothing about the invariant.
    """
    outcome, detail = mutation_list.kill(mutant)
    assert outcome == mutant.expects, (
        f"mutant {mutant.name} was {outcome.upper()}, expected {mutant.expects.upper()}: "
        f"{mutant.check} on {mutant.file} losing `{mutant.old.strip()[:80]}` -> "
        f"{detail or 'no failure'}. In production: {mutant.why}")
    assert mutant.expects_detail in detail, (
        f"mutant {mutant.name} was {outcome} but for the wrong reason: expected the "
        f"detail to name `{mutant.expects_detail}`, got {detail!r}")
    print(f"{mutant.name}: {outcome} by {mutant.check} -> {detail}")


def test_the_runner_reports_a_stale_anchor_as_misdeclared() -> None:
    """Review H4: a mutant whose anchor is gone is `misdeclared`, never a crash or a kill."""
    import dataclasses
    stale = dataclasses.replace(mutation_list.SELF_TEST, name="self_test_stale_anchor",
                                old="create table infrx.no_such_relation (")
    outcome, detail = mutation_list.kill(stale)
    assert outcome == mutation_list.MISDECLARED, f"a stale anchor was {outcome}: {detail}"
    print(f"runner self-test: stale anchor classified as {outcome} -> {detail}")


def test_the_runner_cannot_report_a_broken_migration_as_a_kill() -> None:
    """B10's self-test: the previous runner counted `psycopg.Error` and setup failures as
    kills, and reported one mutant as "killed by setup: 0003 failed to apply"."""
    outcome, detail = mutation_list.kill(mutation_list.SELF_TEST)
    assert outcome == mutation_list.APPLY_ERROR, \
        f"a migration that does not compile was reported as {outcome}: {detail}"
    print(f"runner self-test: broken SQL classified as {outcome} -> {detail}")
