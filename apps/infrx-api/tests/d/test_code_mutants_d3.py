#!/usr/bin/env python3
"""R32/R40: run D3's adapter mutation list (no Docker). A surviving mutant fails the suite.

    uv run --frozen pytest -q tests/d/test_code_mutants_d3.py
"""
from __future__ import annotations

import pytest

from . import code_mutants_d3 as mutation_list

ALL = mutation_list.MUTANTS


def test_the_list_is_well_formed() -> None:
    assert len({m.name for m in ALL}) == len(ALL), "duplicate mutant names"
    for mutant in ALL:
        assert mutant.cases and mutant.invariant and mutant.new != mutant.old, mutant.name
        source = (mutation_list.shared.API_DIR / "infrx" / mutant.file).read_text()
        assert source.count(mutant.old) == mutant.occurrences, \
            f"{mutant.name}: anchor appears {source.count(mutant.old)} times"
    print(f"D3 code mutants: {len(ALL)}")


@pytest.mark.parametrize("mutant", ALL, ids=[m.name for m in ALL])
def test_mutant_is_killed(mutant) -> None:
    result = mutation_list.shared.run_mutant(mutant, mutation_list.RUNNER)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}")
