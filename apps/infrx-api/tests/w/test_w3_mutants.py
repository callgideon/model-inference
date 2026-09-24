#!/usr/bin/env python3
"""r1 R32: run W3's mutation list, and prove the list matches the cases.

    uv run --frozen pytest -q tests/w/test_w3_mutants.py                 # fast subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/w/test_w3_mutants.py
"""
from __future__ import annotations

import os

import pytest

from tests.i import support

from . import loop_mutants, mutants as w1_list, w3_mutants as mutation_list

ALL = mutation_list.MUTANTS
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
SUBSET = ("image_is_a_moving_tag", "published_beyond_loopback", "record_flag_drift",
          "reaper_enqueues_nothing", "released_jobs_not_recorded", "ready_ignores_the_engine",
          "server_timing_in_seconds", "partly_dead_pool_stays_live")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)
CASE_FILES = ("tests.w.test_serving", "tests.w.test_service")


def _cases() -> set[str]:
    import importlib
    return {name for module in CASE_FILES for name in vars(importlib.import_module(module))
            if name.startswith("test_")}


def test_the_list_is_well_formed():
    names = [m.name for m in ALL]
    assert len(set(names)) == len(names), "duplicate mutant names"
    assert not set(names) & {m.name for m in w1_list.MUTANTS + loop_mutants.MUTANTS}
    for mutant in ALL:
        assert mutant.cases and mutant.invariant, mutant.name
    assert set(SUBSET) <= set(names)
    declared = [m.name for m in ALL if m.dies_by]
    assert len(declared) <= len(ALL) // 5, declared     # the escape hatch stays rare


def test_every_w3_case_is_covered_by_a_mutant_and_every_named_case_exists():
    """R32: a case no single edit can break proves nothing; a named case that does not
    exist is a mutant that cannot die."""
    covered = {case for mutant in ALL for case in mutant.cases}
    assert covered == _cases(), (sorted(_cases() - covered), sorted(covered - _cases()))


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run(mutant)
    support.blocked_off_linux(result)              # E2C (RV-12)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}. The cases {list(mutant.cases)} do not prove "
                           f"what they claim.")
