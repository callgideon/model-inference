#!/usr/bin/env python3
"""R32/R83: every invariant W5 claims is killable by a named case (tests/w/w5_mutants.py).

    uv run --frozen pytest -q tests/w/test_w5_mutants.py              # fast subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/w/test_w5_mutants.py
"""
from __future__ import annotations

import os

import pytest

from ..contracts import mutants as shared
from . import loop_mutants, mutants as w1_list, prep_worker_mutants, w3_mutants, w4_mutants
from . import w5_mutants as mutation_list
from . import worker_main_mutants

ALL = mutation_list.MUTANTS
CASES = mutation_list.case_names()
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
SUBSET = ("w5_readiness_barrier_media_only",)
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)


def test_the_list_is_well_formed():
    """A mutant naming a case that does not exist is unkillable by construction; a name
    another W list uses is two mutants reported as one."""
    names = [m.name for m in ALL]
    assert len(set(names)) == len(names), "duplicate mutant names"
    others = (w1_list.MUTANTS + loop_mutants.MUTANTS + w3_mutants.MUTANTS + w4_mutants.MUTANTS
              + worker_main_mutants.MUTANTS + worker_main_mutants.PG_MUTANTS
              + prep_worker_mutants.MUTANTS + prep_worker_mutants.PG_MUTANTS + shared.MUTANTS)
    assert not set(names) & {m.name for m in others}
    for mutant in ALL:
        assert mutant.cases and mutant.invariant, mutant.name
        for case in mutant.cases:
            assert case in CASES, f"{mutant.name} names unknown case {case}"
    assert set(SUBSET) <= set(names)
    assert not [m.name for m in ALL if m.dies_by], "every death is an assertion"


def test_every_case_is_covered_by_a_mutant():
    """A case no single edit can break proves nothing."""
    covered = {case for mutant in ALL for case in mutant.cases}
    assert CASES - covered == set(), f"cases no mutant can break: {sorted(CASES - covered)}"


def test_every_anchor_is_in_the_source_as_often_as_declared():
    """An edit that moves an anchor is found in the default suite, not only by `all`."""
    moved = [m.name for m in ALL
             if (mutation_list.API_DIR / "infrx" / m.file).read_text().count(m.old)
             != m.occurrences]
    assert moved == [], f"anchors no longer in the source: {moved}"


def test_the_named_cases_pass_on_the_pristine_tree():
    """R83 (b): a case that fails on its own would 'kill' every mutant naming it."""
    cases = tuple(sorted({case for mutant in ALL for case in mutant.cases}))
    assert shared.pristine(cases, mutation_list.RUNNER) is None


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}. The cases {list(mutant.cases)} do not prove "
                           f"what they claim.")
