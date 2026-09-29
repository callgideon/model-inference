#!/usr/bin/env python3
"""R32: every P2 decision is killed by a named single edit (`mutants.py`).

    uv run --frozen pytest -q tests/p/teachers/test_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/p/teachers/test_mutants.py   # all
"""
from __future__ import annotations

import os

import pytest

from . import mutants as mutation_list

ALL = mutation_list.MUTANTS
CASES = mutation_list.case_names()
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
SUBSET = ("no_recheck_before_egress", "budget_exhaustion_skips_a_chunk", "holdout_labelled",
          "teacher_label_is_human", "p2_gate_reads_d7_only")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)


def test_the_list_is_well_formed():
    assert len({m.name for m in ALL}) == len(ALL), "duplicate mutant names"
    assert set(SUBSET) <= {m.name for m in ALL}
    for mutant in ALL:
        assert mutant.cases and mutant.invariant and mutant.new != mutant.old, mutant.name
        for case in mutant.cases:
            assert case in CASES, f"{mutant.name} names unknown case {case}"
        source = (mutation_list.API_DIR / "infrx" / mutant.file).read_text()
        assert source.count(mutant.old) == mutant.occurrences, mutant.name


def test_every_case_is_covered_by_a_mutant():
    covered = {case for mutant in ALL for case in mutant.cases}
    assert CASES - covered == set(), f"cases no mutant can break: {sorted(CASES - covered)}"


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, f"{mutant.name} is {result.outcome}: {result.detail}"
