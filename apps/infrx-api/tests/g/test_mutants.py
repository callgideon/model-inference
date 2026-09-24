#!/usr/bin/env python3
"""r1 R32: every invariant this suite claims must be killable, and the runner that
proves it must not be able to lie.

    uv run --frozen pytest -q tests/g/test_mutants.py
"""
from __future__ import annotations

import os

import pytest

from tests.i import support

from . import mutants as mutation_list

ALL = mutation_list.MUTANTS
CASES = mutation_list.case_names()
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
# One pytest process per mutant, so the default suite runs one mutant per mutated
# file plus the two that decide whether a secret can leak; the whole list runs with
# `INFRX_MUTANTS=all`, as the contracts list does. A survivor fails the suite either
# way - the subset only changes how long `make api-test` takes.
SUBSET = ("body_cap_removed", "anonymous_request_accepted", "unhandled_exception_text_leaks",
          "unsupported_parameters_ignored", "pilot_starts_unreachable", "key_cache_unbounded",
          "input_ceiling_ignores_the_output", "unset_mode_starts_legacy",
          # One per blocking finding of review round 1, so the default suite would
          # have caught each of them.
          "recursion_error_escapes", "envelope_render_unprotected", "messages_unbounded",
          "param_echoed_unfiltered", "admission_ignores_db_deadline", "cap_counts_one_chunk",
          "http_exceptions_unwrapped", "trace_default_is_full",
          # the cutover: the full mount and never process memory from settings
          "composition_root_drops_jobs", "objects_from_settings_in_memory",
          "build_info_not_required_in_pilot", "release_sha_accepts_a_short_id")
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
    uncovered = CASES - covered
    assert uncovered == set(), f"cases no mutant can break: {sorted(uncovered)}"


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    support.blocked_off_linux(result)              # E2C (RV-12)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}. The cases {list(mutant.cases)} do not prove "
                           f"what they claim.")


SELF_TESTS = (
    ("a no-op edit survives", mutation_list.Outcome.survived,
     mutation_list.Mutant(name="self_no_op", invariant="an edit that changes nothing survives",
                          file="gateway/routes/validate.py", old="MAX_STOP_SEQUENCES = 4",
                          new="MAX_STOP_SEQUENCES = 4  # a comment changes no behaviour",
                          cases=("test_f_base__only_n_equals_one_is_supported",))),
    ("a syntax error is not a kill", mutation_list.Outcome.broken_runner,
     mutation_list.Mutant(name="self_syntax_error", invariant="a broken copy is not a kill",
                          file="gateway/routes/validate.py", old="def check_messages(body: dict, allowed_mime=frozenset())",
                          new="def check_messages(body: dict,)) :::",
                          cases=("test_f_base__only_n_equals_one_is_supported",))),
    ("a lethal edit under the wrong case name is not a kill", mutation_list.Outcome.survived,
     mutation_list.Mutant(name="self_wrong_case", invariant="the kill comes from the named case",
                          file="gateway/routes/validate.py",
                          old="                if videos > MAX_VIDEO_PARTS:",
                          new="                if False:",
                          cases=("test_f_base__public_health_is_generic",))),
    ("a missing anchor is a failure", mutation_list.Outcome.misdeclared,
     mutation_list.Mutant(name="self_missing_anchor", invariant="the list matches the code",
                          file="gateway/routes/validate.py", old="this text is not in the module",
                          new="nor is this",
                          cases=("test_f_base__only_n_equals_one_is_supported",))),
    ("a mutant with no case is a failure", mutation_list.Outcome.misdeclared,
     mutation_list.Mutant(name="self_no_case", invariant="every mutant names a case",
                          file="gateway/routes/validate.py", old="MAX_STOP_SEQUENCES = 4",
                          new="MAX_STOP_SEQUENCES = 1", cases=())),
)


@pytest.mark.parametrize("name,expected,mutant", SELF_TESTS, ids=[t[0] for t in SELF_TESTS])
def test_the_runner_cannot_report_a_false_kill(name, expected, mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.outcome is expected, f"{name}: got {result.outcome} - {result.detail}"
    assert result.ok is (expected is mutation_list.Outcome.killed)
