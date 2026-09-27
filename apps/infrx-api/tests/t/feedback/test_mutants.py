#!/usr/bin/env python3
"""R32/R40/R83: every decision T2F (and T2I's WR-3 lookup) claims is killed by a named edit.

    uv run --frozen pytest -q tests/t/feedback/test_mutants.py                       # subset
    INFRX_D_TASK=t2f INFRX_T2F_STACK=1 INFRX_MUTANTS=all \\
        uv run --frozen pytest -q tests/t/feedback/test_mutants.py

A ClickHouse-only mutant skips without INFRX_T2F_STACK=1; the PostgreSQL list skips without a
usable Docker (a skip is never a pass).
"""
from __future__ import annotations

import os

import pytest

from . import mutants as mutation_list

ALL, PG = mutation_list.MUTANTS, mutation_list.PG_MUTANTS
CASES = mutation_list.case_names()
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
STACK = os.environ.get("INFRX_T2F_STACK") == "1"
SUBSET = ("outage_swallowed", "orphan_acknowledged")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)
Mutant, Outcome = mutation_list.Mutant, mutation_list.Outcome
needs_stack = pytest.mark.skipif(not STACK, reason="T2F (owner: T): a ClickHouse-only mutant - "
                                 "start infrx-t2f-clickhouse and export INFRX_T2F_STACK=1")


def _pg_unavailable() -> str | None:
    from tests.d import pgharness
    return pgharness.unavailable()


def test_the_lists_are_well_formed():
    names = [m.name for m in ALL + PG]
    assert len(set(names)) == len(names), "duplicate mutant names"
    assert set(SUBSET) <= {m.name for m in ALL}
    assert mutation_list.NEEDS_STACK <= {m.name for m in ALL}
    checks = set(mutation_list.pg_checks())
    for mutant in ALL + PG:
        assert mutant.cases and mutant.invariant and mutant.new != mutant.old, mutant.name
        source = (mutation_list.API_DIR / "infrx" / mutant.file).read_text()
        assert source.count(mutant.old) == 1, f"{mutant.name}: {source.count(mutant.old)}"
        for case in mutant.cases:
            assert case in (CASES if mutant in ALL else checks), f"{mutant.name}: {case}"


def test_every_case_is_covered_by_a_mutant():
    covered = {case for mutant in ALL + PG for case in mutant.cases}
    every = CASES | set(mutation_list.pg_checks())
    assert every - covered == set(), f"cases no mutant can break: {sorted(every - covered)}"


@pytest.mark.parametrize("mutant", [
    pytest.param(m, marks=needs_stack) if m.name in mutation_list.NEEDS_STACK else m
    for m in SELECTED], ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, f"{mutant.name} is {result.outcome}: {result.detail}"


@pytest.mark.skipif(not FULL_RUN, reason="the PostgreSQL list runs with INFRX_MUTANTS=all")
@pytest.mark.parametrize("mutant", PG, ids=[m.name for m in PG])
def test_pg_mutant_is_killed_in_process(mutant):
    reason = _pg_unavailable()
    if reason:
        pytest.skip(f"task-local PostgreSQL unavailable: {reason}")
    result = mutation_list.kill_in_process(mutant)
    assert result.killed, f"{mutant.name} is {result.outcome}: {result.detail}"


def test_the_in_process_kill_cannot_report_a_false_kill():
    """A no-op edit survives: the in-process kill is an assertion, not an import error."""
    if _pg_unavailable():
        pytest.skip("task-local PostgreSQL unavailable")
    anchor = "PINS = (f\"select"
    no_op = Mutant("self_no_op", "a comment changes nothing", mutation_list.PINS, anchor,
                   "# no-op\n" + anchor, (mutation_list.VERSIONS,))
    assert mutation_list.kill_in_process(no_op).outcome == Outcome.survived
