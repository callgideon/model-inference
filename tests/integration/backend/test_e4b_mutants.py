"""R32: run E4B's mutation list through the shared runner, and prove the list matches the
cases. The default run takes a subset (one per decision that can turn a certification
green); `INFRX_MUTANTS=all` (make api-mutants) runs the whole list. A survivor fails either.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import e4b_mutants as mutation_list                    # noqa: E402

ALL = mutation_list.MUTANTS
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
SUBSET = ("untyped_skip_accepted", "duplicate_job_unchecked", "interruption_unchecked")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)


def test_the_e4b_list_is_well_formed():
    """Unique names; every mutant names a case that exists and an invariant; every anchor
    occurs exactly as declared on this checkout; every death is an assertion."""
    names = [m.name for m in ALL]
    assert len(set(names)) == len(names), "duplicate mutant names"
    cases = mutation_list.case_names()
    for mutant in ALL:
        assert mutant.cases and mutant.invariant, mutant.name
        assert set(mutant.cases) <= cases, (mutant.name, set(mutant.cases) - cases)
        found = (mutation_list.REPO / mutant.file).read_text().count(mutant.old)
        assert found == mutant.occurrences, (mutant.name, found)
        assert not mutant.dies_by, mutant.name
    assert set(SUBSET) <= set(names)


def test_every_e4b_case_is_covered_by_a_mutant():
    """A case no single edit can break proves nothing."""
    covered = {case for mutant in ALL for case in mutant.cases}
    assert mutation_list.case_names() - covered == set()


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_e4b_mutant_is_killed(mutant):
    result = mutation_list.run(mutant)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}. The cases {list(mutant.cases)} do not prove "
                           f"what they claim.")
