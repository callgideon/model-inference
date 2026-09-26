#!/usr/bin/env python3
"""R32/R40/R83: every invariant `test_worker_main.py` claims is killable by a named case.

    uv run --frozen pytest -q tests/w/test_worker_main_mutants.py              # fast subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/w/test_worker_main_mutants.py
    (+ the lane's harness settings for the PostgreSQL list: see worker_main_mutants.py)
"""
from __future__ import annotations

import os

import pytest

from ..contracts import mutants as shared
from . import loop_mutants, mutants as w1_list, w3_mutants, w4_mutants
from . import worker_main_mutants as mutation_list

MEMORY, PG = mutation_list.MUTANTS, mutation_list.PG_MUTANTS
ALL = MEMORY + PG
CASES = mutation_list.case_names()
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
SUBSET = ("main_store_defaulted_to_in_memory", "main_readiness_before_the_engine_check",
          # W5-F5 (E3C F-6): the reader on the runtime pool; a refusal logged every tick
          "main_reconciliation_on_the_runtime_pool", "service_privilege_refusal_every_tick")
SELECTED = MEMORY if FULL_RUN else tuple(m for m in MEMORY if m.name in SUBSET)
SELECTED_PG = PG if FULL_RUN else ()


def test_the_list_is_well_formed():
    """A mutant naming a case that does not exist is unkillable by construction; a name
    another W list uses is two mutants reported as one."""
    names = [m.name for m in ALL]
    assert len(set(names)) == len(names), "duplicate mutant names"
    others = w1_list.MUTANTS + loop_mutants.MUTANTS + w3_mutants.MUTANTS + w4_mutants.MUTANTS
    assert not set(names) & {m.name for m in others}
    for mutant in ALL:
        assert mutant.cases and mutant.invariant, mutant.name
        for case in mutant.cases:
            assert case in CASES, f"{mutant.name} names unknown case {case}"
    assert set(SUBSET) <= set(names)


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


def test_the_service_free_list_names_no_postgresql_case():
    """The default list runs anywhere; a missing harness skips only what needs it."""
    assert not any("_pg__" in case for m in MEMORY for case in m.cases)
    assert all("_pg__" in case for m in PG for case in m.cases)


def test_the_named_cases_pass_on_the_pristine_tree():
    """R83 (b): a case that fails on its own would 'kill' every mutant naming it."""
    cases = tuple(sorted({case for mutant in MEMORY for case in mutant.cases}))
    assert shared.pristine(cases, mutation_list.RUNNER) is None


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}. The cases {list(mutant.cases)} do not prove "
                           f"what they claim.")


@pytest.mark.parametrize("mutant", SELECTED_PG, ids=[m.name for m in SELECTED_PG])
def test_pg_mutant_is_killed(mutant):
    """On the lane's D harness, Valkey and MinIO (a visible skip without them)."""
    from ..d import pgharness
    reason = pgharness.unavailable()
    if reason:
        pytest.skip(f"PostgreSQL harness unavailable: {reason}")
    if not os.environ.get("INFRX_M_S3_ENDPOINT") or \
            os.environ.get("INFRX_M_S3_LOCAL_CREDS") != "1":
        pytest.skip("no local S3 endpoint (INFRX_M_S3_ENDPOINT, INFRX_M_S3_LOCAL_CREDS=1)")
    result = mutation_list.run_mutant(mutant)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}. The cases {list(mutant.cases)} do not prove "
                           f"what they claim.")
