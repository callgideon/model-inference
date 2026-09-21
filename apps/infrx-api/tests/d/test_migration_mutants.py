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
          "organizations_update_not_revoked", "rpc_granted_to_authenticated",
          "profiles_operator_column_grant_widened",
          "infrx_tables_readable_by_authenticated",
          "entitlements_deny_everyone_by_default", "new_organizations_get_no_wallet",
          "wallet_money_is_not_the_domain", "no_pending_outbox_index",
          "wallet_view_without_a_tenant_predicate",
          "operator_audit_readable_by_a_member",
          "ledger_actor_is_never_masked",
          "calibration_labels_leak_into_feedback",
          "wallet_summary_answers_for_any_organization",
          "api_keys_update_not_narrowed")

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
    """The check that claims this invariant fails when the migration loses it."""
    killed_by = mutation_list.kill(mutant)
    assert killed_by is not None, (
        f"mutant {mutant.name} SURVIVED: {mutant.check} passed although "
        f"{mutant.file} lost `{mutant.old.strip()[:80]}`. In production: {mutant.why}")
    print(f"{mutant.name}: killed by {mutant.check} -> {killed_by}")
