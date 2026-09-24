#!/usr/bin/env python3
"""r1 R32: run W4's mutation list, and prove the list matches the cases.

    uv run --frozen pytest -q tests/w/test_w4_mutants.py                 # fast subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/w/test_w4_mutants.py
"""
from __future__ import annotations

import os

import pytest

from tests.i import support

from . import loop_mutants, mutants as w1_list, test_w4, w3_mutants, w4_mutants as mutation_list

ALL = mutation_list.MUTANTS
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
SUBSET = ("threshold_edited_in_decide_only", "blank_sample_read_as_zero",
          "hash_comparison_dropped", "engine_refusal_untyped", "engine_state_ignored",
          "in_flight_check_dropped")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)


def test_the_list_is_well_formed():
    names = [m.name for m in ALL]
    assert len(set(names)) == len(names), "duplicate mutant names"
    others = w1_list.MUTANTS + loop_mutants.MUTANTS + w3_mutants.MUTANTS
    assert not set(names) & {m.name for m in others}
    for mutant in ALL:
        assert mutant.cases and mutant.invariant, mutant.name
        root = (mutation_list.shared.API_DIR / "infrx" if mutant.file.startswith("worker/")
                else mutation_list.REPO / "models")
        found = (root / mutant.file).read_text().count(mutant.old)
        assert found == mutant.occurrences, (mutant.name, found)
        if mutant.file.endswith(".sh"):
            assert len(mutant.cases) == 1 and mutant.cases[0] in mutation_list.SCRIPT_CHECKS
    assert set(SUBSET) <= set(names)
    assert not [m.name for m in ALL if m.dies_by], "every death is an assertion"


def test_every_w4_case_is_covered_by_a_mutant_and_every_named_case_exists():
    """R32: a case no single edit can break proves nothing; a named case that does not
    exist is a mutant that cannot die."""
    cases = {name for name in vars(test_w4) if name.startswith("test_")}
    covered = {case for mutant in ALL for case in mutant.cases}
    assert covered == cases, (sorted(cases - covered), sorted(covered - cases))


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run(mutant)
    support.blocked_off_linux(result)              # E2C (RV-12)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}. The cases {list(mutant.cases)} do not prove "
                           f"what they claim.")
