#!/usr/bin/env python3
"""R32/R40: every invariant a v2 conformance case names must be killable.

`mutants_v2.py` declares the single-edit defects; this runs them. A mutant that
survives means the case asserting that invariant proves nothing, which is a failed
suite rather than a warning.

The list is slow by contract (one pytest process per mutant), so the default suite
runs a subset and the whole list runs on demand — the same convention as the v1
list:

    uv run --frozen pytest -q tests/contracts/v2/test_mutants_v2.py          # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/contracts/v2/test_mutants_v2.py
"""
from __future__ import annotations

import os

import pytest
from infrx.contracts.conformance import v2_contracts

from . import mutants_v2

ALL = mutants_v2.MUTANTS
CASE_NAMES = {case.__name__ for case in v2_contracts.cases()}
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
# One mutant per oracle, plus both money-unit guards and both roundings: enough to
# catch a broken runner or a vacuous case in the settlement path in CI time.
SUBSET = ("units_are_interchangeable", "raw_answers_any_unit", "a_request_may_name_a_wallet",
          "the_grant_key_includes_the_campaign", "the_hold_rounds_like_a_charge",
          "the_charge_rounds_up", "settle_ignores_certainty", "revocation_is_ignored",
          "a_v1_payload_is_silently_accepted", "the_grant_amount_changes")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)


def test_the_list_is_well_formed():
    """Every mutant names at least one case, and every case it names exists. A typo
    would make a mutant unkillable-by-construction and silently pass."""
    assert len(ALL) >= 50, f"only {len(ALL)} mutants declared"
    assert len({m.name for m in ALL}) == len(ALL), "duplicate mutant names"
    for mutant in ALL:
        assert mutant.cases, f"{mutant.name} names no case"
        assert mutant.invariant, f"{mutant.name} states no invariant"
        for case in mutant.cases:
            assert case in CASE_NAMES, f"{mutant.name} names unknown case {case}"
    assert set(SUBSET) <= {m.name for m in ALL}


def test_every_v2_case_is_covered_by_a_mutant():
    """R32: one mutant per invariant a case claims. A case no mutant can break is a
    case that proves nothing."""
    covered = {case for mutant in ALL for case in mutant.cases}
    uncovered = CASE_NAMES - covered
    assert uncovered == set(), f"cases no mutant can break: {sorted(uncovered)}"


def test_every_mutant_targets_the_v2_surface():
    """A mutant outside `contracts/v2/` or the v2 fakes would be testing v1's
    guards, which this additive phase does not own."""
    for mutant in ALL:
        assert mutant.file.startswith(("contracts/v2/", "contracts/conformance/v2_")), \
            f"{mutant.name} edits {mutant.file}"


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutants_v2.run_mutant(mutant)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}. The cases {list(mutant.cases)} do not prove "
                           f"what they claim.")


# --- the runner's own honesty -------------------------------------------------
# A runner that counted a syntax error as a kill would let every mutant above pass
# while proving nothing, so each outcome is exercised deliberately. The
# classification is imported from the v1 runner, and this is what proves the
# imported classification still behaves against the v2 test path.
SELF_TESTS = (
    ("a_syntax_error_is_not_a_kill", mutants_v2.Outcome.broken_runner,
     mutants_v2.Mutant(
         name="self_syntax_error", invariant="the runner rejects a broken copy",
         file="contracts/v2/records.py", old="class WalletRef(RecordV2):",
         new="class WalletRef(RecordV2)) :::",
         cases=("credit_identity__a_consumer_credential_resolves_its_own_user_wallet",))),
    ("an_import_error_is_not_a_kill", mutants_v2.Outcome.broken_runner,
     mutants_v2.Mutant(
         name="self_import_error", invariant="the runner rejects an import-time failure",
         file="contracts/v2/records.py", old="SCHEMA_VERSION = 2",
         new="SCHEMA_VERSION = _undefined_name_at_import_time",
         cases=("credit_identity__a_consumer_credential_resolves_its_own_user_wallet",))),
    ("a_no_op_edit_survives", mutants_v2.Outcome.survived,
     mutants_v2.Mutant(
         name="self_no_op", invariant="an edit that changes nothing is a survivor",
         file="contracts/v2/records.py", old="SCHEMA_VERSION = 2",
         new="SCHEMA_VERSION = 2  # a comment changes no behaviour",
         cases=("credit_identity__a_consumer_credential_resolves_its_own_user_wallet",))),
    # A real defect whose named case cannot see it is a *survivor*: the kill has to
    # come from the case that claims the invariant, not from anywhere in the suite.
    ("a_lethal_edit_under_the_wrong_case_name_is_not_a_kill", mutants_v2.Outcome.survived,
     mutants_v2.Mutant(
         name="self_wrong_case", invariant="a kill must come from the named case",
         file="contracts/v2/records.py",
         old="                and purpose in self.purposes)", new="                and True)",
         cases=("credit_units__a_mixed_history_totals_per_unit_and_never_once",))),
    ("a_missing_anchor_is_a_failure", mutants_v2.Outcome.misdeclared,
     mutants_v2.Mutant(
         name="self_missing_anchor", invariant="the list matches the code",
         file="contracts/v2/records.py", old="this text is not in the records",
         new="nor is this",
         cases=("credit_identity__a_consumer_credential_resolves_its_own_user_wallet",))),
    ("a_mutant_with_no_case_is_a_failure", mutants_v2.Outcome.misdeclared,
     mutants_v2.Mutant(
         name="self_no_case", invariant="every mutant names a case",
         file="contracts/v2/records.py", old="SCHEMA_VERSION = 2", new="SCHEMA_VERSION = 3",
         cases=())),
)


@pytest.mark.parametrize("name,expected,mutant", SELF_TESTS, ids=[t[0] for t in SELF_TESTS])
def test_the_runner_cannot_report_a_false_kill(name, expected, mutant):
    result = mutants_v2.run_mutant(mutant)
    assert result.outcome is expected, f"{name}: got {result.outcome} - {result.detail}"
    assert result.ok is (expected is mutants_v2.Outcome.killed)


def test_a_known_lethal_mutant_is_killed_for_the_right_reason():
    """The positive control: the same machinery reports a real kill, and the failing
    test id is the case the mutant names."""
    mutant = next(m for m in ALL if m.name == "the_grant_key_includes_the_campaign")
    result = mutants_v2.run_mutant(mutant)
    assert result.killed and "1 failed" in result.detail, result.detail
