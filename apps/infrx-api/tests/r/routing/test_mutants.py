#!/usr/bin/env python3
"""R32/R40: every decision R1 claims is killed by a named edit.

    cd apps/infrx-api && uv run --frozen pytest -q tests/r/routing/test_mutants.py     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/r/routing/test_mutants.py
"""
from __future__ import annotations

import os

import pytest

from . import mutants as mutation_list

ALL = mutation_list.MUTANTS
CASES = mutation_list.case_names()
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
SUBSET = ("explicit_pin_routed", "shadow_admitted_and_charged")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)


def test_the_list_is_well_formed():
    names = [m.name for m in ALL]
    assert len(set(names)) == len(names), "duplicate mutant names"
    assert set(SUBSET) <= set(names)
    for mutant in ALL:
        assert mutant.cases and mutant.invariant and mutant.new != mutant.old, mutant.name
        source = (mutation_list.API_DIR / "infrx" / mutant.file).read_text()
        assert source.count(mutant.old) == 1, f"{mutant.name}: {source.count(mutant.old)}"
        assert set(mutant.cases) <= CASES, mutant.name


def test_every_case_is_covered_by_a_mutant():
    covered = {case for mutant in ALL for case in mutant.cases}
    assert CASES - covered == set(), f"cases no mutant can break: {sorted(CASES - covered)}"


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, f"{mutant.name} is {result.outcome}: {result.detail}"
