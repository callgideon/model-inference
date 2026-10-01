#!/usr/bin/env python3
"""R32/R83: every decision of AP-02's console reads is killable by a named case.

    INFRX_MUTANTS=all INFRX_D_TASK=ap2 uv run --frozen pytest -q tests/ap02/test_mutants.py
"""
from __future__ import annotations

import ast
import os
import pathlib

import pytest

from . import mutants as mutation_list

ALL = mutation_list.MUTANTS + mutation_list.PG_MUTANTS
API = pathlib.Path(__file__).resolve().parents[2]
CASES = {n.name for f in mutation_list.FILES for n in ast.parse((API / f).read_text()).body
         if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
# The default suite runs one unit mutant; the PostgreSQL list only on a full run (on ap2).
SELECTED = ALL if FULL_RUN else mutation_list.MUTANTS[:1]


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
