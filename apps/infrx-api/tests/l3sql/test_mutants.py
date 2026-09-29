#!/usr/bin/env python3
"""R32/R40/R83: every decision of lab-sql LW7's Python ports is killable by a named case.

    INFRX_MUTANTS=all uv run --frozen pytest -q tests/l3sql/test_mutants.py
"""
from __future__ import annotations

import ast
import os
import pathlib

import pytest

from . import mutants as mutation_list

ALL = mutation_list.MUTANTS
UNITS = pathlib.Path(__file__).with_name("test_lw7_units.py")
CASES = {n.name for n in ast.parse(UNITS.read_text()).body
         if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
SELECTED = ALL if FULL_RUN else ALL[:1]


def test_the_list_is_well_formed():
    assert len({m.name for m in ALL}) == len(ALL), "duplicate mutant names"
    for mutant in ALL:
        assert mutant.cases and mutant.invariant, mutant.name
        assert set(mutant.cases) <= CASES, f"{mutant.name} names an unknown case"


def test_every_case_is_covered_by_a_mutant():
    covered = {case for mutant in ALL for case in mutant.cases}
    assert CASES - covered == set(), f"cases no mutant can break: {sorted(CASES - covered)}"


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, f"{mutant.name} is {result.outcome} ({mutant.invariant}): {result.detail}"
