#!/usr/bin/env python3
"""R32/R40: run composition batch 6's SQL mutation list (needs Docker; skips visibly without
it). A survivor fails the suite, and every check of `test_c6_reads.py` is named by a mutant.

    INFRX_D_TASK=r2 uv run --frozen pytest -q tests/d/test_code_mutants_c6.py
"""
from __future__ import annotations

import pytest

from . import code_mutants_c6 as mutation_list
from . import migration_mutants as _d
from . import pgharness
from . import test_c6_judge as j
from . import test_c6_reads as t

SQL, JUDGE = mutation_list.SQL_MUTANTS, mutation_list.JUDGE
_reason = pgharness.unavailable()


def test_the_lists_are_well_formed() -> None:
    names = [m.name for m in SQL + JUDGE]
    assert len(set(names)) == len(names), "duplicate mutant names"
    stale = [f"{m.name}: {_d.anchor_count(m)}" for m in SQL + JUDGE
             if _d.anchor_count(m) != m.occurrences]
    assert not stale, f"misdeclared SQL anchors: {stale}"
    assert all(m.check in t.CHECKS for m in SQL)
    assert all(m.check in j.CHECKS for m in JUDGE)


def test_no_mutant_anchors_in_a_superseded_function_body() -> None:
    found = _d.superseded(SQL + JUDGE)
    assert not found, found


def test_every_case_is_covered_by_a_mutant() -> None:
    uncovered = sorted(set(t.CHECKS) - {m.check for m in SQL})
    uncovered += sorted(set(j.CHECKS) - {m.check for m in JUDGE})
    assert not uncovered, f"cases no mutant can break: {uncovered}"


@pytest.mark.skipif(_reason is not None, reason=f"task-local PostgreSQL unavailable: {_reason}")
@pytest.mark.parametrize("mutant", SQL, ids=lambda m: m.name)
def test_sql_mutant_is_killed(mutant) -> None:
    outcome, detail = mutation_list.kill(mutant)
    assert outcome == _d.KILLED, (f"{mutant.name} was {outcome} by {mutant.check}: {detail}. "
                                  f"In production: {mutant.why}")
    print(f"{mutant.name}: {outcome} -> {detail}")


@pytest.mark.skipif(_reason is not None, reason=f"task-local PostgreSQL unavailable: {_reason}")
@pytest.mark.parametrize("mutant", JUDGE, ids=lambda m: m.name)
def test_judge_mutant_is_killed(mutant) -> None:
    outcome, detail = mutation_list.kill_judge(mutant)
    assert outcome == _d.KILLED, (f"{mutant.name} was {outcome} by {mutant.check}: {detail}. "
                                  f"In production: {mutant.why}")
    print(f"{mutant.name}: {outcome} -> {detail}")
