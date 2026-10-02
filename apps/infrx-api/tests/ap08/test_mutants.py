#!/usr/bin/env python3
"""R32: every AP-08 decision is killed by a named single edit (`mutants.py`): the Python list
through the shared runner, 0064's and 0067's (SR-AP08-1) lists in process on the ap8 world
(Docker + the key).

    uv run --frozen pytest -q tests/ap08/test_mutants.py                                  # subset
    INFRX_D_TASK=ap8 INFRX_MUTANTS=all uv run --frozen pytest -q tests/ap08/test_mutants.py  # all
"""
from __future__ import annotations

import importlib
import os

import pytest

from tests.d import migration_mutants as _d

from . import mutants as mutation_list
from .conftest import pg_reason

ALL = mutation_list.MUTANTS
CASES = mutation_list.case_names()
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
SUBSET = ("run_id_is_random", "key_audience_acts", "missing_video_criterion_omitted")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)
WORLDS = {"doors": "tests.ap08.test_judge_doors_pg", "worker": "tests.ap08.test_judge_worker_pg",
          "store": "tests.ap08.test_rubric_store_pg"}
SQL = mutation_list.SQL_MUTANTS
SQL_SELECTED = SQL if FULL_RUN else SQL[:1]
SR = mutation_list.SR_MUTANTS
SR_SELECTED = SR if FULL_RUN else SR[:1]


def test_the_list_is_well_formed():
    assert len({m.name for m in ALL}) == len(ALL), "duplicate mutant names"
    assert set(SUBSET) <= {m.name for m in ALL}
    for mutant in ALL:
        assert mutant.cases and mutant.invariant and mutant.new != mutant.old, mutant.name
        for case in mutant.cases:
            assert case in CASES, f"{mutant.name} names unknown case {case}"
        source = (mutation_list.API_DIR / "infrx" / mutant.file).read_text()
        assert source.count(mutant.old) == mutant.occurrences, mutant.name
    names = [m.name for m, _ in SQL]
    assert len(set(names)) == len(names), "duplicate SQL mutant names"
    stale = [m.name for m, _ in SQL if _d.anchor_count(m) != m.occurrences]
    assert not stale, f"misdeclared 0064 anchors: {stale}"
    for mutant, world in SQL:
        assert mutant.check in importlib.import_module(WORLDS[world]).CHECKS, mutant.name
    assert len({m.name for m, _ in SR}) == len(SR), "duplicate SR mutant names"
    for mutant, world in SR:
        assert _d.anchor_count(mutant) == 1 and mutant.old != mutant.new and mutant.why, \
            f"misdeclared SR anchor: {mutant.name}"
        assert mutant.check in importlib.import_module(WORLDS[world]).CHECKS, mutant.name


def test_every_case_is_covered_by_a_mutant():
    covered = {case for mutant in ALL for case in mutant.cases}
    assert CASES - covered == set(), f"cases no mutant can break: {sorted(CASES - covered)}"


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, f"{mutant.name} is {result.outcome}: {result.detail}"


@pytest.mark.skipif(pg_reason() is not None, reason=f"{pg_reason()}")
@pytest.mark.parametrize("pair", SQL_SELECTED, ids=[m.name for m, _ in SQL_SELECTED])
def test_sql_mutant_is_killed(pair):
    from tests.d import code_mutants_d7 as d7
    from tests.d import pgharness
    mutant, world = pair
    outcome, detail = d7.kill(mutant, f"{pgharness.DATABASE}_ap8mut",
                              importlib.import_module(WORLDS[world]))
    assert outcome == _d.KILLED, (f"{mutant.name} was {outcome} by {mutant.check}: {detail}. "
                                  f"In production: {mutant.why}")


@pytest.mark.skipif(pg_reason() is not None, reason=f"{pg_reason()}")
@pytest.mark.parametrize("pair", SR_SELECTED, ids=[m.name for m, _ in SR_SELECTED])
def test_sr_mutant_is_killed(pair):
    """SR-AP08-1 (`0067_judge_rubrics.sql`) mutated in a copy of the migrations, the world's
    seed, the named check - only its assertion kills."""
    from tests.d import code_mutants_d7 as d7
    from tests.d import pgharness
    mutant, world = pair
    outcome, detail = d7.kill(mutant, f"{pgharness.DATABASE}_ap8srmut",
                              importlib.import_module(WORLDS[world]))
    assert outcome == _d.KILLED, (f"{mutant.name} was {outcome} by {mutant.check}: {detail}. "
                                  f"In production: {mutant.why}")
