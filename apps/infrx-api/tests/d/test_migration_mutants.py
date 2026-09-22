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
          "d1r_unverified_reads_verified")

SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in ALWAYS)

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")


def test_the_mutant_list_is_well_formed() -> None:
    """Distinct names, a known scenario and a known check for each."""
    assert len({m.name for m in ALL}) == len(ALL), "two mutants share a name"
    for mutant in ALL:
        assert mutant.scenario in ("fresh", "upgrade", "volume", "prodlike", "credit",
                                   "upgrade05", "credit_volume", "admission"), mutant.name
        assert mutant.check in mutation_list._CHECKS, f"{mutant.name}: unknown check"
    covered = {m.check for m in ALL}
    uncovered = sorted(set(mutation_list._CHECKS) - covered)
    assert not uncovered, f"checks no mutant can break: {uncovered}"
    print(f"{len(ALL)} mutants over {len(covered)} checks; "
          f"{len(SELECTED)} selected ({'all' if FULL_RUN else 'subset'})")


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


def test_the_runner_cannot_report_a_broken_migration_as_a_kill() -> None:
    """B10's self-test: the previous runner counted `psycopg.Error` and setup failures as
    kills, and reported one mutant as "killed by setup: 0003 failed to apply"."""
    outcome, detail = mutation_list.kill(mutation_list.SELF_TEST)
    assert outcome == mutation_list.APPLY_ERROR, \
        f"a migration that does not compile was reported as {outcome}: {detail}"
    print(f"runner self-test: broken SQL classified as {outcome} -> {detail}")
