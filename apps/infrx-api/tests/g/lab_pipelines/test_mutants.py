#!/usr/bin/env python3
"""R32/R40/R83: every invariant the WR-P4-1 pipelines route (`lab_pipelines`) claims is
killable by a named case.

    uv run --frozen pytest -q tests/g/lab_pipelines/test_mutants.py
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/lab_pipelines/test_mutants.py
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
# identity, capabilities, the connector, write-once ids, lineage, gone, the body, the
# teacher dry run, its approval, its read-back and its form);
# `INFRX_MUTANTS=all` runs all.
SUBSET = ("mounted_without_the_switch", "body_before_identity", "viewer_reads_labels",
          "connector_not_manual", "import_conflict_unchecked", "run_holdout_unpinned",
          "gone_is_unavailable", "body_bounded_by_the_chat_cap", "dry_run_sends",
          "developer_approves", "chunk_state_not_the_ledgers", "foreign_payer_is_forbidden")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)


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
