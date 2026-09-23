#!/usr/bin/env python3
"""r1 R32: M1-L2's guards must be killable. A surviving mutant fails the suite.

    uv run --frozen pytest -q tests/m/test_s3_mutants.py                  # the subset
    INFRX_M_S3_ENDPOINT=http://127.0.0.1:55500 INFRX_MUTANTS=all \\
        uv run --frozen pytest -q tests/m/test_s3_mutants.py               # every mutant

A mutant only an S3 case can see skips, naming its owner, when no endpoint is exported.
"""
from __future__ import annotations

import os

import pytest

from . import s3_mutants as mutation_list
from . import test_s3

ALL = mutation_list.MUTANTS
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
SUBSET = ("s3_prefix_unchecked", "s3_transport_failure_is_absence")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)


def test_the_list_is_well_formed():
    names = {name for name in vars(test_s3) if name.startswith("test_")}
    assert len({m.name for m in ALL}) == len(ALL), "duplicate mutant names"
    assert set(SUBSET) <= {m.name for m in ALL} and mutation_list.NEEDS_S3 <= {m.name for m in ALL}
    source = {m.file: (mutation_list.shared.API_DIR / "infrx" / m.file).read_text() for m in ALL}
    for mutant in ALL:
        assert mutant.cases and mutant.invariant and mutant.new != mutant.old, mutant.name
        assert set(mutant.cases) <= names, f"{mutant.name} names an unknown case"
        assert source[mutant.file].count(mutant.old) == mutant.occurrences, \
            f"{mutant.name}: anchor appears {source[mutant.file].count(mutant.old)} times"


@pytest.mark.parametrize("mutant", [
    pytest.param(m, marks=test_s3.needs_s3) if m.name in mutation_list.NEEDS_S3 else m
    for m in SELECTED], ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}. The tests {list(mutant.cases)} do not prove "
                           f"what they claim.")
