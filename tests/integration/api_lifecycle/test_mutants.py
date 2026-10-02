"""R32/R83: every decision AP-11's runner claims is killed by a named single edit
(`mutants.py`), and every layer-1 case is named by one.

    apps/infrx-api/.venv/bin/python -m pytest -q -p no:cacheprovider \\
        tests/integration/api_lifecycle/test_mutants.py                     # subset
    INFRX_MUTANTS=all ... tests/integration/api_lifecycle/test_mutants.py   # the whole list
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mutation_list = _load("ap11_mutants", "mutants.py")
MUTANTS = mutation_list.MUTANTS
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
SUBSET = ("retry_under_a_new_key", "absent_api_is_a_pass")
SELECTED = MUTANTS if FULL_RUN else tuple(m for m in MUTANTS if m.name in SUBSET)
Mutant, Outcome = mutation_list.Mutant, mutation_list.Outcome


def test_the_list_is_well_formed():
    assert len({m.name for m in MUTANTS}) == len(MUTANTS), "duplicate mutant names"
    assert set(SUBSET) <= {m.name for m in MUTANTS}
    known = mutation_list.case_names()
    for mutant in MUTANTS:
        assert mutant.cases and mutant.invariant and mutant.new != mutant.old, mutant.name
        assert not mutant.dies_by, f"{mutant.name}: every death is an assertion"
        assert set(mutant.cases) <= known, f"{mutant.name} names an unknown case"
        source = (mutation_list.REPO / mutant.file).read_text()
        assert source.count(mutant.old) == mutant.occurrences, mutant.name


def test_every_case_is_covered_by_a_mutant():
    named = {case for m in MUTANTS for case in m.cases}
    assert mutation_list.case_names() - named == set(), sorted(mutation_list.case_names() - named)


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, f"{mutant.name} is {result.outcome}: {result.detail}"


R = mutation_list.R
SELF_TESTS = (
    (Outcome.survived, Mutant("self_no_op", "a comment changes nothing", R,
                              'BASE = "AP-11", "API-LIFECYCLE", "b05eb6f4"',
                              'BASE = "AP-11", "API-LIFECYCLE", "b05eb6f4"  # no-op',
                              (mutation_list.EXITS,))),
    (Outcome.misdeclared, Mutant("self_missing_anchor", "the list matches the code", R,
                                 "not in the runner", "x", (mutation_list.EXITS,))),
)


@pytest.mark.parametrize("expected,mutant", SELF_TESTS, ids=[m.name for _, m in SELF_TESTS])
def test_the_runner_cannot_report_a_false_kill(expected, mutant):
    assert mutation_list.run_mutant(mutant).outcome == expected
