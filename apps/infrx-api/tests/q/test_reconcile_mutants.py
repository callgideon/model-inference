#!/usr/bin/env python3
"""R32: run the Q3 mutation list. A surviving mutant is a failed suite.

Gated like Q1's and Q2's lists: by default a subset (one mutant per mechanism) plus the
self-tests that the Q3 wiring cannot report a false kill; the whole list on demand.

    uv run --frozen pytest -q tests/q/test_reconcile_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/q/test_reconcile_mutants.py   # all
"""
from __future__ import annotations

import os

import pytest

from . import reconcile_mutants as mutation_list
from . import vkharness

_reason = vkharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local Valkey unavailable: {_reason}")

ALL = mutation_list.MUTANTS
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
# The three rules everything else rests on: index before acknowledging, top up after a
# rebuild, rebuild when the index refuses a wanted event as acknowledged.
SUBSET = ("the_row_is_acknowledged_before_it_is_indexed",
          "the_rebuild_is_not_topped_up",
          "an_acknowledged_wanted_job_is_left_out")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)


@pytest.fixture(autouse=True, scope="module")
def _one_server_for_the_whole_list():
    """Start the task-local server once, in this process, so every mutant subprocess finds
    it running. Otherwise the first subprocess of each run starts it and removes it at
    exit, and the next one starts it again - a docker run and a readiness wait per mutant,
    which is where the list failed under host load."""
    vkharness.ensure()


def test_the_list_is_well_formed():
    """Unique names, an invariant and a case each, every anchor exactly once in its file,
    and every case a test that exists in the suite the runner aims at."""
    assert len({m.name for m in ALL}) == len(ALL), "duplicate mutant names"
    assert set(SUBSET) <= {m.name for m in ALL}
    suite = (mutation_list.API_DIR / mutation_list.PATHS).read_text()
    print(f"\nQ3 mutants: {len(ALL)} declared, {len(SELECTED)} selected "
          f"({'INFRX_MUTANTS=all' if FULL_RUN else 'default subset'})")
    for mutant in ALL:
        source = (mutation_list.API_DIR / "infrx" / mutant.file).read_text()
        assert mutant.cases and mutant.invariant, mutant.name
        assert source.count(mutant.old) == 1, \
            f"{mutant.name}: anchor appears {source.count(mutant.old)} times"
        assert mutant.new != mutant.old, f"{mutant.name} changes nothing"
        for case in mutant.cases:
            assert f"def {case}(" in suite, f"{mutant.name} names a missing case {case}"


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant, paths=mutation_list.PATHS)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}. The cases {list(mutant.cases)} do not prove "
                           f"what they claim.")


SELF_TESTS = (
    ("a_no_op_edit_survives", mutation_list.Outcome.survived,
     mutation_list.Mutant(name="self_no_op", invariant="an edit that changes nothing survives",
                          file=mutation_list.RECONCILE, old="    max_batches: int = 10",
                          new="    max_batches: int = 10  # a comment changes nothing",
                          cases=("test_q3_drain__every_dispatch_row_is_indexed_once_and_"
                                 "acknowledged",))),
    # A real defect (dead candidates are kept) named against a case with no dead one.
    ("a_defect_the_named_case_cannot_see_is_not_a_kill", mutation_list.Outcome.survived,
     mutation_list.Mutant(name="self_wrong_case", invariant="the kill comes from the named case",
                          file=mutation_list.RECONCILE,
                          old="        for job_id in dead:\n            await self.index.remove(job_id)",
                          new="        for job_id in dead:\n            pass",
                          cases=("test_q3_drain__every_dispatch_row_is_indexed_once_and_"
                                 "acknowledged",))),
)


@pytest.mark.parametrize("name,expected,mutant", SELF_TESTS, ids=[t[0] for t in SELF_TESTS])
def test_the_runner_cannot_report_a_false_kill(name, expected, mutant):
    result = mutation_list.run_mutant(mutant, paths=mutation_list.PATHS)
    assert result.outcome is expected, f"{name}: got {result.outcome} - {result.detail}"
