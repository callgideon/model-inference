#!/usr/bin/env python3
"""R32/R40/R83: every invariant the WR-V1M-2 traces route (`lab_traces`) claims is killable by a named case.

    uv run --frozen pytest -q tests/g/lab_traces/test_mutants.py
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/lab_traces/test_mutants.py
"""
from __future__ import annotations

import os

import pytest

from ...contracts import mutants as shared
from . import mutants as mutation_list

ALL = mutation_list.MUTANTS
CASES = mutation_list.case_names()
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
# One pytest process per mutant: the default suite runs one per invariant group (mounting,
# membership, tenancy, grants, T3, pages); `INFRX_MUTANTS=all` runs the whole list.
SUBSET = ("mounted_without_traces", "membership_skipped", "deleted_request_listed",
          "one_category_releases_the_blob", "grant_shared_across_grantors",
          "content_bound_ignored", "cursor_names_the_organization")
# The ClickHouse read's mutants die only on a real stack (INFRX_LAB_API_STACK=1).
STACK = os.environ.get("INFRX_LAB_API_STACK") == "1"
SELECTED = tuple(m for m in (ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET))
                 if STACK or m.name not in mutation_list.STACK_ONLY)


def test_the_list_is_well_formed():
    """A mutant naming a case that does not exist is unkillable by construction."""
    assert len({m.name for m in ALL}) == len(ALL), "duplicate mutant names"
    for mutant in ALL:
        assert mutant.cases and mutant.invariant, mutant.name
        assert mutant.file in mutation_list.FILES, f"{mutant.name} mutates outside the router"
        for case in mutant.cases:
            assert case in CASES, f"{mutant.name} names unknown case {case}"
    assert set(SUBSET) <= {m.name for m in ALL}


def test_every_case_is_covered_by_a_mutant():
    """A case no single edit can break proves nothing."""
    covered = {case for mutant in ALL for case in mutant.cases}
    assert CASES - covered == set(), f"cases no mutant can break: {sorted(CASES - covered)}"


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
