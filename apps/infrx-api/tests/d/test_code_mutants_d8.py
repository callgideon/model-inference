#!/usr/bin/env python3
"""R32/R40: run D8's mutation lists (0042's ledgers, 0043's remaining requests, the Python
adapters). The SQL lists need Docker (skip visibly without it); the Python list runs the unit
file through the shared runner. A survivor fails the suite, and every check and unit case of
D8 is named by a mutant.

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_code_mutants_d8.py
"""
from __future__ import annotations

import pytest

from . import code_mutants_d8 as mutation_list
from . import migration_mutants as _d
from . import pgharness
from . import test_d8_ledgers as t
from . import test_d8_requests as q
from . import test_d8_units as units

SQL, REQUESTS, CODE = mutation_list.SQL_MUTANTS, mutation_list.REQUESTS, mutation_list.CODE_MUTANTS
_reason = pgharness.unavailable()


def test_the_lists_are_well_formed() -> None:
    names = [m.name for m in SQL + REQUESTS] + [m.name for m in CODE]
    assert len(set(names)) == len(names), "duplicate mutant names"
    stale = [f"{m.name}: {_d.anchor_count(m)}" for m in SQL + REQUESTS
             if _d.anchor_count(m) != m.occurrences]
    assert not stale, f"misdeclared SQL anchors: {stale}"
    for m in CODE:
        source = (mutation_list.shared.API_DIR / "infrx" / m.file).read_text()
        assert source.count(m.old) == m.occurrences, f"{m.name}: {source.count(m.old)}"
    assert all(m.check in t.CHECKS for m in SQL)
    assert all(m.check in q.CHECKS for m in REQUESTS)
    print(f"D8 mutants: {len(SQL)} + {len(REQUESTS)} SQL, {len(CODE)} Python")


def test_no_mutant_anchors_in_a_superseded_function_body() -> None:
    """D5 item 10b's guard for these lists: no anchor inside a function a later file
    redefines."""
    found = _d.superseded(SQL + REQUESTS)
    assert not found, found


def test_every_case_is_covered_by_a_mutant() -> None:
    uncovered = sorted(set(t.CHECKS) - {m.check for m in SQL})
    uncovered += sorted(set(q.CHECKS) - {m.check for m in REQUESTS})
    cases = {name for name in dir(units) if name.startswith("test_")}
    uncovered += sorted(cases - {c for m in CODE for c in m.cases})
    assert not uncovered, f"cases no mutant can break: {uncovered}"


@pytest.mark.skipif(_reason is not None, reason=f"task-local PostgreSQL unavailable: {_reason}")
@pytest.mark.parametrize("mutant", SQL, ids=lambda m: m.name)
def test_sql_mutant_is_killed(mutant) -> None:
    outcome, detail = mutation_list.kill(mutant)
    assert outcome == mutant.expects, (f"{mutant.name} was {outcome} by {mutant.check}: "
                                       f"{detail}. In production: {mutant.why}")
    print(f"{mutant.name}: {outcome} -> {detail}")


@pytest.mark.skipif(_reason is not None, reason=f"task-local PostgreSQL unavailable: {_reason}")
@pytest.mark.parametrize("mutant", REQUESTS, ids=lambda m: m.name)
def test_request_mutant_is_killed(mutant) -> None:
    outcome, detail = mutation_list.kill_request(mutant)
    assert outcome == mutant.expects, (f"{mutant.name} was {outcome} by {mutant.check}: "
                                       f"{detail}. In production: {mutant.why}")
    print(f"{mutant.name}: {outcome} -> {detail}")


@pytest.mark.parametrize("mutant", CODE, ids=lambda m: m.name)
def test_code_mutant_is_killed(mutant) -> None:
    result = mutation_list.run_code_mutant(mutant)
    assert result.killed, f"{mutant.name} is {result.outcome} ({mutant.invariant}): {result.detail}"
