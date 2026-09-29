#!/usr/bin/env python3
"""R32/R40/R83: every invariant the WR-R4-1 releases routes (`lab_releases`) claims is
killable by a named case.

    uv run --frozen pytest -q tests/g/lab_releases/test_mutants.py
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/lab_releases/test_mutants.py
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
# identity, capabilities, the records, the fence, the verdict, the body);
# `INFRX_MUTANTS=all` runs all.
SUBSET = ("mounted_without_the_switch", "body_before_identity", "developer_proposes",
          "proposals_withheld", "fence_from_the_read_model", "expand_without_an_expand_verdict",
          "kind_open")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)
PG = mutation_list.PG_MUTANTS


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


def test_every_pg_case_is_covered_by_a_pg_mutant():
    """C7-RV-6 / 1-LR7-RV-5: each PostgreSQL case (p3's, r2's composition verdict) is named by
    its own list (never the fake runner's) and runs on its own key."""
    import re

    def defined(path):
        return set(re.findall(r"^def (test_\w+)\(", (shared.API_DIR / path).read_text(), re.M))
    assert {case for m in PG for case in m.cases} == set(mutation_list.PG_KEYS)
    assert defined(mutation_list.PG_FILE) == {mutation_list.PG_CASE}
    assert mutation_list.COMP_PG_CASE in defined(mutation_list.COMP_PG_FILE)
    assert all(len(m.cases) == 1 for m in PG)
    assert not {m.name for m in PG} & {m.name for m in ALL}
    assert all(m.file in mutation_list.FILES for m in PG)


@pytest.mark.parametrize("mutant", PG if FULL_RUN else (), ids=[m.name for m in PG] if FULL_RUN
                         else ())
def test_pg_mutant_is_killed(mutant):
    """On its case's key only, and only when asked (`INFRX_LAB_RELEASES_PG=1`): a visible
    skip."""
    from ...d import pgharness
    key = mutation_list.pg_key(mutant)
    if os.environ.get("INFRX_LAB_RELEASES_PG") != "1" or \
            os.environ.get("INFRX_D_TASK") != key or pgharness.unavailable():
        pytest.skip(f"the {key} PostgreSQL half runs on request (INFRX_LAB_RELEASES_PG=1, "
                    f"INFRX_D_TASK={key})")
    result = mutation_list.run_mutant(mutant)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}. The cases {list(mutant.cases)} do not prove "
                           f"what they claim.")
