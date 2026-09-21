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
          # r2: one per round-2 finding, so the default run covers every B item.
          "views_writable_by_browser_roles", "ledger_actor_masking_keys_on_the_marker",
          "ledger_keeps_the_operator_principal", "truncate_guard_dropped",
          "api_keys_insert_is_table_wide", "views_without_security_barrier",
          "wallet_total_not_moved_by_the_ledger", "consent_revocation_can_be_undone",
          "holds_need_not_belong_to_the_job", "ledger_signs_unconstrained",
          "terminal_settlement_is_rewritable", "money_leaves_the_views_as_a_number",
          "console_rpcs_answer_for_any_organization",
          "admin_orgs_duplicates_an_org_with_two_owners")

SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in ALWAYS)

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")


def test_the_mutant_list_is_well_formed() -> None:
    """Distinct names, a known scenario and a known check for each."""
    assert len({m.name for m in ALL}) == len(ALL), "two mutants share a name"
    for mutant in ALL:
        assert mutant.scenario in ("fresh", "upgrade", "volume", "prodlike"), mutant.name
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
    assert outcome == mutation_list.KILLED, (
        f"mutant {mutant.name} was {outcome.upper()} (not killed): {mutant.check} on "
        f"{mutant.file} losing `{mutant.old.strip()[:80]}` -> {detail or 'no failure'}. "
        f"In production: {mutant.why}")
    print(f"{mutant.name}: killed by {mutant.check} -> {detail}")


def test_the_runner_cannot_report_a_broken_migration_as_a_kill() -> None:
    """B10's self-test: the previous runner counted `psycopg.Error` and setup failures as
    kills, and reported one mutant as "killed by setup: 0003 failed to apply"."""
    outcome, detail = mutation_list.kill(mutation_list.SELF_TEST)
    assert outcome == mutation_list.APPLY_ERROR, \
        f"a migration that does not compile was reported as {outcome}: {detail}"
    print(f"runner self-test: broken SQL classified as {outcome} -> {detail}")
