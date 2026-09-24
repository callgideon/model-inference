#!/usr/bin/env python3
"""R32/R40/R83: every invariant `test_pilot_media.py` claims is killable by a named case.

    uv run --frozen pytest -q tests/m/test_pilot_mutants.py
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/m/test_pilot_mutants.py
"""
from __future__ import annotations

import os

import pytest

from ..contracts import mutants as shared
from ..d import pgharness
from . import pilot_mutants as mutation_list

MEMORY, PG = mutation_list.MUTANTS, mutation_list.PG_MUTANTS
ALL = MEMORY + PG
CASES = mutation_list.case_names()
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
# One per gap in the default suite; the whole list with `INFRX_MUTANTS=all`.
SUBSET = ("upload_ref_not_resolved", "attach_not_persisted")
SELECTED = MEMORY if FULL_RUN else tuple(m for m in MEMORY if m.name in SUBSET)
SELECTED_PG = PG if FULL_RUN else ()


def test_the_list_is_well_formed():
    """A mutant naming a case that does not exist is unkillable by construction."""
    assert len({m.name for m in ALL}) == len(ALL), "duplicate mutant names"
    for mutant in ALL:
        assert mutant.cases and mutant.invariant, mutant.name
        for case in mutant.cases:
            assert case in CASES, f"{mutant.name} names unknown case {case}"
    assert set(SUBSET) <= {m.name for m in ALL}


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
    cases = tuple(sorted({case for mutant in MEMORY for case in mutant.cases}))
    assert shared.pristine(cases, mutation_list.RUNNER) is None


def test_the_memory_list_needs_no_database():
    """The PostgreSQL cases are named only by the PostgreSQL list, so the default list runs
    anywhere and a missing Docker skips only what needs it."""
    assert not any(case.startswith("test_mpilot_pg__") for m in MEMORY for case in m.cases)
    assert all(case.startswith("test_mpilot_pg__") for m in PG for case in m.cases)


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}. The cases {list(mutant.cases)} do not prove "
                           f"what they claim.")


@pytest.mark.parametrize("mutant", SELECTED_PG, ids=[m.name for m in SELECTED_PG])
def test_pg_mutant_is_killed(mutant):
    """The attach record's SQL, on the D harness's PostgreSQL (visible skip without Docker)."""
    reason = pgharness.unavailable()
    if reason:
        pytest.skip(f"PostgreSQL harness unavailable: {reason}")
    result = mutation_list.run_mutant(mutant)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}. The cases {list(mutant.cases)} do not prove "
                           f"what they claim.")
