#!/usr/bin/env python3
"""r1 R32: run W2's mutation list, and prove the list matches the cases.

    uv run --frozen pytest -q tests/w/test_loop_mutants.py               # fast subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/w/test_loop_mutants.py

One pytest process per mutant, so the whole list runs on demand and a subset runs by
default - the split `tests/w/test_mutants.py` and `tests/contracts/test_mutants.py` use.
The runner's own honesty (no false kill, no hidden skip) is proved once, in
`tests/w/test_mutants.py`; this file shares that runner.
"""
from __future__ import annotations

import os

import pytest

from . import loop_mutants as mutation_list
from . import mutants as w1_list

ALL = mutation_list.MUTANTS
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
# One mutant per area: the journal payload, persist-before-relay, fencing, the lease
# renewal, the billing decision, the loop's acknowledgment and the two S2M items.
SUBSET = ("journal_carries_the_raw_text", "relay_without_a_commit",
          "lease_loss_cancels_the_engine", "heartbeat_never_renews",
          "completed_taken_on_trust", "candidate_never_acknowledged",
          "media_sent_as_a_bare_key", "only_one_eos_id")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)


def test_the_list_is_well_formed():
    """Every mutant names an invariant and at least one case, no name repeats, and the
    declared-failure-mode escape hatch stays rare - a list where most mutants need one is
    a list of broken mutants."""
    assert len(ALL) >= 30, f"only {len(ALL)} mutants declared"
    assert len({m.name for m in ALL}) == len(ALL), "duplicate mutant names"
    assert not {m.name for m in ALL} & {m.name for m in w1_list.MUTANTS}, "name clash with W1"
    for mutant in ALL:
        assert mutant.cases, f"{mutant.name} names no case"
        assert mutant.invariant, f"{mutant.name} states no invariant"
        assert mutant.file in ("worker/attempt.py", "worker/loop.py", "worker/engine.py"), \
            mutant.file
        for name in mutant.allowed_errors:
            assert name.isidentifier() and name not in w1_list.KILL_ERRORS, name
    assert set(SUBSET) <= {m.name for m in ALL}
    declared = [m.name for m in ALL if m.allowed_errors]
    assert len(declared) <= len(ALL) // 5, declared


def test_every_owned_case_is_covered_by_a_mutant():
    """R32: an invariant no single edit can break is an invariant the case does not really
    prove. The exceptions are declared here, with their reason."""
    import tests.w.test_loop as loop_tests

    covered = {case for mutant in ALL for case in mutant.cases}
    cases = {name for name in vars(loop_tests) if name.startswith("test_")}
    # These five assert about collaborators rather than about the worker's own logic, and
    # no edit to `worker/` can break them. The first four are the **store's**
    # (`contracts/fakes/state.py`, the coordinator's mutation list): the reconciliation
    # window, the requeue of a dead worker's attempt, and the terminalization R29 does in
    # its own call. The fifth is the **adapter's** finish-reason and dropped-line rule,
    # which `tests/w/mutants.py` already covers; the worker's own re-check of the same
    # facts is `..._an_adapters_completed_is_not_taken_on_trust`, which four mutants kill.
    # Each is kept here because a worker that settled on top of one would be a W2 defect.
    exempt = {"test_dur_settle__published_output_with_an_unknown_count_waits_for_reconciliation",
              "test_ops_recover__a_lease_held_by_a_dead_worker_is_never_resumed",
              "test_dur_settle__a_finish_reason_outside_the_set_is_not_completed",
              "test_dur_settle__a_usage_event_can_arrive_with_a_stall_or_a_cancellation",
              "test_dur_settle__a_finished_stream_with_a_dropped_line_is_not_a_billable_success",
              # asserts about the **exported suites and their factories**, not about this
              # module: breaking it means editing the contracts package, whose mutation
              # list is the coordinator's
              "test_f_contract__the_loops_collaborators_pass_their_exported_suites"}
    uncovered = cases - covered - exempt
    assert uncovered == set(), f"cases no mutant can break: {sorted(uncovered)}"
    assert exempt <= cases, sorted(exempt - cases)


def test_every_mutant_case_exists():
    """A case name that does not exist is a mutant that cannot die."""
    import tests.w.test_loop as loop_tests

    cases = {name for name in vars(loop_tests) if name.startswith("test_")}
    named = {case for mutant in ALL for case in mutant.cases}
    assert named <= cases, sorted(named - cases)


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run(mutant)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}. The cases {list(mutant.cases)} do not prove "
                           f"what they claim.")
