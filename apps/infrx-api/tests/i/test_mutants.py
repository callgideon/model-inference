#!/usr/bin/env python3
"""r1 R32/R40: every invariant `tests/i` claims must be killable by a single edit, and
the runner that proves it must not be able to lie.

    uv run --frozen pytest -q tests/i/test_mutants.py            # the default subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/i/test_mutants.py
"""
from __future__ import annotations

import os

import pytest

from . import mutants as mutation_list

ALL = mutation_list.MUTANTS
CASES = mutation_list.case_names()
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
# One pytest process per mutant. The default suite runs the fail-open defect itself,
# one per mutated file and every mutant that decides whether a secret can leak; the
# whole list runs with `INFRX_MUTANTS=all`. A survivor fails the suite either way.
SUBSET = ("denied_read_tolerated", "unknown_error_is_not_found", "validation_skipped",
          "problems_ignored", "rename_before_validate", "unknown_mode_accepted",
          "composition_gate_removed", "value_printed_on_success",
          "value_printed_on_refusal", "value_passed_as_an_argument",
          "staged_file_is_world_readable", "stale_staged_file_kept",
          "python_pin_dropped", "transport_check_dropped", "engine_digest_unchecked",
          "transport_logs_not_silenced", "manifest_modes_drift", "unset_mode_starts_legacy",
          # I2B: one per file it added, and every one that decides a refusal or the edge
          "probe_on_host_despite_image", "unknown_setting_accepted", "worker_killed_before_drain",
          "gateway_public_bind", "engine_second_setting_source", "valkey_public_bind",
          "dockerfile_base_by_tag", "metrics_public", "maintenance_proxies",
          "units_before_preflight", "edge_opened_unready", "maintenance_not_sticky",
          "drain_leaves_edge_open", "rollback_to_unmetered_allowed",
          "reviewed_digest_not_enforced", "failure_not_rolled_back", "ssm_drops_arguments",
          "revert_runtime_before_tree")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)


def test_the_list_is_well_formed():
    """A mutant naming a case that does not exist is unkillable by construction, and
    would pass silently."""
    assert len({m.name for m in ALL}) == len(ALL), "duplicate mutant names"
    for mutant in ALL:
        assert mutant.cases, f"{mutant.name} names no case"
        assert mutant.invariant, f"{mutant.name} states no invariant"
        for case in mutant.cases:
            assert case in CASES, f"{mutant.name} names unknown case {case}"
    assert set(SUBSET) <= {m.name for m in ALL}


def test_every_case_is_covered_by_a_mutant():
    """A case no single edit can break proves nothing."""
    covered = {case for mutant in ALL for case in mutant.cases}
    assert CASES - covered == set(), f"cases no mutant can break: {sorted(CASES - covered)}"


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}. The cases {list(mutant.cases)} do not prove "
                           f"what they claim.")


# --- the runner's own honesty (r1 R32: a runner that counts the wrong kill is worse
# --- than no runner) ---------------------------------------------------------------
SELF_TESTS = (
    ("a mutant with no case is misdeclared",
     mutation_list.Mutant("nocase", "nothing", mutation_list.P, "x", "y", ()),
     mutation_list.Outcome.misdeclared),
    ("a missing anchor is misdeclared, never skipped",
     mutation_list.Mutant("noanchor", "nothing", mutation_list.P,
                          "this text is not in preflight.py", "y",
                          ("test_deploy_failclosed__a_valid_install_replaces_the_file_and_restarts",)),
     mutation_list.Outcome.misdeclared),
    ("a syntax error is a broken runner, not a kill",
     mutation_list.Mutant("syntax", "nothing", mutation_list.P, "def apply(cfg: Config)",
                          "def apply(cfg: Config",
                          ("test_deploy_failclosed__a_valid_install_replaces_the_file_and_restarts",)),
     mutation_list.Outcome.broken_runner),
    ("a defect the named case does not notice survives",
     mutation_list.Mutant("survivor", "nothing", mutation_list.P,
                          'parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])',
                          'parser = argparse.ArgumentParser(description="")',
                          ("test_deploy_failclosed__a_valid_install_replaces_the_file_and_restarts",)),
     mutation_list.Outcome.survived),
    # review r1 B1: the copied tree used to be flat, which made `support.REPO` resolve
    # to `/` and the real-engine-script case fail in **every** copy whatever the edit -
    # so a mutant that changed nothing reported `killed`. A no-op edit naming that case
    # is the smallest thing that notices, and it only passes if the copy reproduces the
    # repository's layout.
    ("a no-op edit cannot kill the case that reads the real engine script",
     mutation_list.Mutant("noop", "nothing", mutation_list.P,
                          "REFUSED, RESTART_FAILED = 2, 3",
                          "REFUSED, RESTART_FAILED = 2, 3  # no-op",
                          ("test_deploy_failclosed__the_repository_engine_script_is_checked_as_it_stands",)),
     mutation_list.Outcome.survived),
)


@pytest.mark.parametrize("name,mutant,expected", SELF_TESTS, ids=[s[0] for s in SELF_TESTS])
def test_the_runner_reports_the_right_outcome(name, mutant, expected):
    result = mutation_list.run_mutant(mutant)
    assert result.outcome is expected, f"{name}: got {result.outcome} ({result.detail})"
    assert not result.killed
