#!/usr/bin/env python3
"""r1 R32: every invariant a conformance case names must be killable.

`mutants.py` declares single-edit defects; this runs them. Each mutant is applied to
a copy of the package in a temporary directory and the cases it names are run there:
a mutant that survives means the case asserting that invariant proves nothing, which
is a failed suite, not a warning.

The list is slow by contract (one pytest process per mutant), so the default suite
runs a fast subset and the whole list runs on demand:

    uv run --frozen pytest -q tests/contracts/test_mutants.py            # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/contracts/test_mutants.py
    make api-mutants                                                     # same, root
"""
from __future__ import annotations

import os

import pytest
from infrx.contracts.conformance import SUITES

from . import mutants as mutation_list

ALL = mutation_list.MUTANTS
CASE_NAMES = {case.__name__ for _name, (cases, _runner) in SUITES.items() for case in cases()}
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
# The default suite runs one mutant per fake plus every mutant of the money path, so a
# broken runner or a vacuous case in the core settlement logic is caught in CI time;
# `make api-mutants` runs all of them.
SUBSET = ("heartbeat_stores_the_callers_lease", "cap_org_not_counted", "debit_rounds_up",
          "settles_any_cause", "capture_bytes_not_charged", "judge_settle_negative",
          "upload_expiry_ignored", "visibility_from_the_event_time",
          "feedback_operator_role_from_session")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)


def test_the_list_is_well_formed():
    """Every mutant names at least one case, and every case it names exists. A typo
    would otherwise make a mutant unkillable-by-construction and silently pass."""
    assert len(ALL) >= 100, f"only {len(ALL)} mutants declared"
    assert len({m.name for m in ALL}) == len(ALL), "duplicate mutant names"
    for mutant in ALL:
        assert mutant.cases, f"{mutant.name} names no case"
        assert mutant.invariant, f"{mutant.name} states no invariant"
        for case in mutant.cases:
            assert case in CASE_NAMES, f"{mutant.name} names unknown case {case}"
    assert set(SUBSET) <= {m.name for m in ALL}


def test_every_case_is_covered_by_a_mutant():
    """R32: one mutant per invariant a case claims. A case no mutant can break is a
    case that proves nothing, so it must be listed here deliberately."""
    covered = {case for mutant in ALL for case in mutant.cases}
    # Engine cases describe an external process's behaviour rather than an invariant of
    # our own state, so they are driven by `EngineFault` scripts, not by mutants.
    engine_cases = {case.__name__ for case in SUITES["engine"][0]()}
    uncovered = CASE_NAMES - covered - engine_cases
    assert uncovered == set(), f"cases no mutant can break: {sorted(uncovered)}"


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}. The cases {list(mutant.cases)} do not prove "
                           f"what they claim.")


# --- the runner's own honesty (r4 B2) ---------------------------------------------
# A runner that counts a syntax error as a kill would let every one of the mutants
# above pass while proving nothing, so each outcome is exercised deliberately.
SELF_TESTS = (
    ("a_syntax_error_is_not_a_kill", mutation_list.Outcome.broken_runner,
     mutation_list.Mutant(
         name="self_syntax_error", invariant="the runner rejects a broken copy",
         file="contracts/fakes/state.py", old="    async def admit(self",
         new="    async def admit(self)) :::", cases=("dur_admit__a_request_uuid_is_admitted_once",))),
    ("an_import_error_is_not_a_kill", mutation_list.Outcome.broken_runner,
     mutation_list.Mutant(
         name="self_import_error", invariant="the runner rejects an import-time failure",
         file="contracts/fakes/state.py", old="MAX_READ_LIMIT = 1000",
         new="MAX_READ_LIMIT = _undefined_name_at_import_time",
         cases=("dur_admit__a_request_uuid_is_admitted_once",))),
    ("a_no_op_edit_survives", mutation_list.Outcome.survived,
     mutation_list.Mutant(
         name="self_no_op", invariant="an edit that changes nothing is a survivor",
         file="contracts/fakes/state.py", old="MAX_READ_LIMIT = 1000",
         new="MAX_READ_LIMIT = 1000  # a comment changes no behaviour",
         cases=("dur_admit__a_request_uuid_is_admitted_once",))),
    # A real defect whose named case cannot see it is a *survivor*: the kill has to come
    # from the case that claims the invariant, not from anywhere in the suite.
    ("a_lethal_edit_under_the_wrong_case_name_is_not_a_kill", mutation_list.Outcome.survived,
     mutation_list.Mutant(
         name="self_wrong_case", invariant="a kill must come from the named case",
         file="contracts/fakes/state.py",
         old='raise errors.StateConflict(\n                    f"request {request.request_id} is already an admitted job")',
         new="pass", cases=("dur_settle__one_settlement_with_exact_decimals",))),
    ("a_missing_anchor_is_a_failure", mutation_list.Outcome.misdeclared,
     mutation_list.Mutant(
         name="self_missing_anchor", invariant="the list matches the code",
         file="contracts/fakes/state.py", old="this text is not in the fake",
         new="nor is this", cases=("dur_admit__a_request_uuid_is_admitted_once",))),
    ("a_mutant_with_no_case_is_a_failure", mutation_list.Outcome.misdeclared,
     mutation_list.Mutant(
         name="self_no_case", invariant="every mutant names a case",
         file="contracts/fakes/state.py", old="MAX_READ_LIMIT = 1000",
         new="MAX_READ_LIMIT = 1", cases=())),
)


@pytest.mark.parametrize("name,expected,mutant", SELF_TESTS, ids=[t[0] for t in SELF_TESTS])
def test_the_runner_cannot_report_a_false_kill(name, expected, mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.outcome is expected, f"{name}: got {result.outcome} - {result.detail}"
    assert not result.killed or expected is mutation_list.Outcome.killed
    # and only `killed` is accepted by the suite
    assert result.ok is (expected is mutation_list.Outcome.killed)


def test_a_known_lethal_mutant_is_killed_for_the_right_reason():
    """The positive control: the same machinery reports a real kill, and the failing
    test id is the case the mutant names."""
    mutant = next(m for m in ALL if m.name == "heartbeat_stores_the_callers_lease")
    result = mutation_list.run_mutant(mutant)
    assert result.killed and "1 failed" in result.detail


def test_the_fakes_skip_no_conformance_case():
    """R32: a case that returns early for a missing optional hook must be reported as
    skipped, never counted as run. The fakes provide every hook, so nothing skips - and
    this is what keeps `pytest -q` honest about the numbers."""
    from infrx.contracts.fakes import FACTORIES
    from infrx.contracts.conformance import OPTIONAL_HOOKS
    for port, factory in FACTORIES.items():
        provided = set(factory().extra)
        missing = OPTIONAL_HOOKS.get(port, frozenset()) - provided
        assert missing == set(), f"the {port} fake is missing hooks {sorted(missing)}"
