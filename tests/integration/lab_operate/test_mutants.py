"""R32/R83: every decision E3L claims is killed by a named single edit (`mutants.py`).

    apps/infrx-api/.venv/bin/python -m pytest -q -p no:cacheprovider \\
        tests/integration/lab_operate/test_mutants.py                         # subset
    INFRX_MUTANTS=all ... tests/integration/lab_operate/test_mutants.py       # layer-1 list
    INFRX_MUTANTS=all INFRX_E2_NAMESPACE=e3l ... test_mutants.py              # + the stack list
                                                              (after runner.py --keep)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutants as mutation_list                         # noqa: E402

LAYER1, STACK = mutation_list.MUTANTS, mutation_list.STACK_MUTANTS
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
SUBSET = ("not_run_is_a_pass", "unbound_case_runs")
SELECTED = LAYER1 if FULL_RUN else tuple(m for m in LAYER1 if m.name in SUBSET)
SELECTED_STACK = STACK if FULL_RUN else ()
Mutant, Outcome = mutation_list.Mutant, mutation_list.Outcome


def test_the_lists_are_well_formed():
    everything = LAYER1 + STACK
    assert len({m.name for m in everything}) == len(everything), "duplicate mutant names"
    assert set(SUBSET) <= {m.name for m in LAYER1}
    known = mutation_list.case_names() | mutation_list.stack_case_names()
    for mutant in everything:
        assert mutant.cases and mutant.invariant and mutant.new != mutant.old, mutant.name
        assert not mutant.dies_by, f"{mutant.name}: every death is an assertion"
        for case in mutant.cases:
            assert case in known, f"{mutant.name} names unknown case {case}"
        base = mutation_list.API_DIR if mutant in STACK else mutation_list.REPO
        assert (base / mutant.file).read_text().count(mutant.old) == mutant.occurrences, mutant.name


def test_every_case_is_covered_by_a_mutant():
    layer1 = {case for m in LAYER1 for case in m.cases}
    stack = {case for m in STACK for case in m.cases}
    assert mutation_list.case_names() - layer1 == set(), \
        sorted(mutation_list.case_names() - layer1)
    assert mutation_list.stack_case_names() - stack == set(), \
        sorted(mutation_list.stack_case_names() - stack)


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, f"{mutant.name} is {result.outcome}: {result.detail}"


@pytest.mark.parametrize("mutant", SELECTED_STACK, ids=[m.name for m in SELECTED_STACK])
def test_stack_mutant_is_killed(mutant):
    """On a kept e3l stack (runner.py --keep); a visible skip without one."""
    why = mutation_list.claim_the_kept_stack()
    if why:
        pytest.skip(f"NOT RUN: {why}")
    result = mutation_list.run_mutant(mutant)
    assert result.killed, f"{mutant.name} is {result.outcome}: {result.detail}"


R = mutation_list.R
SELF_TESTS = (
    (Outcome.survived, Mutant("self_no_op", "a comment changes nothing", R,
                              'NAMESPACE = "e3l"', 'NAMESPACE = "e3l"  # no-op',
                              (mutation_list.NAMESPACE,))),
    (Outcome.misdeclared, Mutant("self_missing_anchor", "the list matches the code", R,
                                 "not in the runner", "x", (mutation_list.NAMESPACE,))),
)


@pytest.mark.parametrize("expected,mutant", SELF_TESTS, ids=[m.name for _, m in SELF_TESTS])
def test_the_runner_cannot_report_a_false_kill(expected, mutant):
    assert mutation_list.run_mutant(mutant).outcome == expected
