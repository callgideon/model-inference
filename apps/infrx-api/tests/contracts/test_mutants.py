#!/usr/bin/env python3
"""r1 R32: every invariant a conformance case names must be killable.

`mutants.py` declares single-edit defects; this runs them. Each mutant is applied to
a copy of the package in a temporary directory and the cases it names are run there:
a mutant that survives means the case asserting that invariant proves nothing, which
is a failed suite, not a warning.

The list is slow by contract (one pytest process per mutant), so the default suite
runs a fast subset and the whole list runs on demand:

    uv run --frozen pytest -q tests/contracts/test_mutants.py            # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/contracts/test_mutants.py
    make api-mutants                                                     # same, root
"""
from __future__ import annotations

import os

import pytest
from infrx.contracts.conformance import SUITES, V2_SUITES

from . import mutants as mutation_list
from . import test_config_and_imports, test_fixtures, test_money
from .v2 import test_conformance_v2

ALL = mutation_list.MUTANTS
# F2P wire-in item 6: the v2 cases are named by the same list and killed by the same runner.
CASE_NAMES = {case.__name__ for suites in (SUITES, V2_SUITES)
              for _name, (cases, _runner) in suites.items() for case in cases()}
# F2R: record and config invariants die in these modules, which the runner also targets.
RECORD_TESTS = {name for module in (test_fixtures, test_money, test_config_and_imports,
                                    test_conformance_v2)
                for name in vars(module) if name.startswith("test_")}
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
# The default suite runs one mutant per fake plus every mutant of the money path, so a
# broken runner or a vacuous case in the core settlement logic is caught in CI time;
# `make api-mutants` runs all of them.
SUBSET = ("heartbeat_stores_the_callers_lease", "cap_org_not_counted", "debit_rounds_up",
          "settles_any_cause", "capture_bytes_not_charged", "judge_settle_negative",
          "upload_expiry_ignored", "visibility_from_the_event_time",
          "feedback_operator_role_from_session",
          # contracts v2 (the subset `test_mutants_v2.py` ran before the fold)
          "units_are_interchangeable", "raw_answers_any_unit", "a_request_may_name_a_wallet",
          "the_grant_key_includes_the_campaign", "the_hold_rounds_like_a_charge",
          "the_charge_rounds_up", "settle_ignores_certainty", "revocation_is_ignored",
          "a_v1_payload_is_silently_accepted", "the_grant_amount_changes")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)


def test_the_list_is_well_formed():
    """Every mutant names at least one case, and every case it names exists. A typo
    would otherwise make a mutant unkillable-by-construction and silently pass."""
    assert len(ALL) >= 100, f"only {len(ALL)} mutants declared"
    assert len({m.name for m in ALL}) == len(ALL), "duplicate mutant names"
    for mutant in ALL:
        assert mutant.cases, f"{mutant.name} names no case"
        assert mutant.invariant, f"{mutant.name} states no invariant"
        for case in mutant.cases:
            assert case in CASE_NAMES | RECORD_TESTS, f"{mutant.name} names unknown case {case}"
    assert set(SUBSET) <= {m.name for m in ALL}


def test_every_case_is_covered_by_a_mutant():
    """R32: one mutant per invariant a case claims. A case no mutant can break is a
    case that proves nothing, so it must be listed here deliberately."""
    covered = {case for mutant in ALL for case in mutant.cases}
    # Engine cases describe an external process's behaviour rather than an invariant of
    # our own state, so they are driven by `EngineFault` scripts, not by mutants.
    engine_cases = {case.__name__ for case in SUITES["engine"][0]()}
    uncovered = CASE_NAMES - covered - engine_cases
    assert uncovered == set(), f"cases no mutant can break: {sorted(uncovered)}"


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}. The cases {list(mutant.cases)} do not prove "
                           f"what they claim.")


# --- the runner's own honesty (r4 B2) ---------------------------------------------
# A runner that counts a syntax error as a kill would let every one of the mutants
# above pass while proving nothing, so each outcome is exercised deliberately.
SELF_TESTS = (
    ("a_syntax_error_is_not_a_kill", mutation_list.Outcome.broken_runner,
     mutation_list.Mutant(
         name="self_syntax_error", invariant="the runner rejects a broken copy",
         file="contracts/fakes/state.py", old="    async def admit(self",
         new="    async def admit(self)) :::", cases=("dur_admit__a_request_uuid_is_admitted_once",))),
    ("an_import_error_is_not_a_kill", mutation_list.Outcome.broken_runner,
     mutation_list.Mutant(
         name="self_import_error", invariant="the runner rejects an import-time failure",
         file="contracts/fakes/state.py", old="MAX_READ_LIMIT = 1000",
         new="MAX_READ_LIMIT = _undefined_name_at_import_time",
         cases=("dur_admit__a_request_uuid_is_admitted_once",))),
    ("a_no_op_edit_survives", mutation_list.Outcome.survived,
     mutation_list.Mutant(
         name="self_no_op", invariant="an edit that changes nothing is a survivor",
         file="contracts/fakes/state.py", old="MAX_READ_LIMIT = 1000",
         new="MAX_READ_LIMIT = 1000  # a comment changes no behaviour",
         cases=("dur_admit__a_request_uuid_is_admitted_once",))),
    # A real defect whose named case cannot see it is a *survivor*: the kill has to come
    # from the case that claims the invariant, not from anywhere in the suite.
    ("a_lethal_edit_under_the_wrong_case_name_is_not_a_kill", mutation_list.Outcome.survived,
     mutation_list.Mutant(
         name="self_wrong_case", invariant="a kill must come from the named case",
         file="contracts/fakes/state.py",
         old='raise errors.StateConflict(\n                    f"request {request.request_id} is already an admitted job")',
         new="pass", cases=("dur_settle__one_settlement_with_exact_decimals",))),
    ("a_missing_anchor_is_a_failure", mutation_list.Outcome.misdeclared,
     mutation_list.Mutant(
         name="self_missing_anchor", invariant="the list matches the code",
         file="contracts/fakes/state.py", old="this text is not in the fake",
         new="nor is this", cases=("dur_admit__a_request_uuid_is_admitted_once",))),
    ("a_mutant_with_no_case_is_a_failure", mutation_list.Outcome.misdeclared,
     mutation_list.Mutant(
         name="self_no_case", invariant="every mutant names a case",
         file="contracts/fakes/state.py", old="MAX_READ_LIMIT = 1000",
         new="MAX_READ_LIMIT = 1", cases=())),
    # F2R item 9: an anchor that appears twice would mutate whichever line came first,
    # which is not the declared defect. Two copies used to accept it silently.
    ("an_anchor_that_appears_twice_is_a_failure", mutation_list.Outcome.misdeclared,
     mutation_list.Mutant(
         name="self_two_anchors", invariant="one anchor, one edit",
         file="contracts/fakes/state.py", old="        now = self.clock.now()",
         new="        now = self.clock.at(1)",
         cases=("dur_admit__a_request_uuid_is_admitted_once",))),
    # ... and an undeclared exception death is not a kill. This is the *declared*
    # `add_raises_on_a_non_byte_part` edit with its declaration removed, so the only
    # difference between a kill and a runner error is the declaration.
    ("an_undeclared_exception_death_is_not_a_kill", mutation_list.Outcome.broken_runner,
     mutation_list.Mutant(
         name="self_undeclared_crash", invariant="a kill is assertion-shaped",
         file="contracts/traces_accounting.py",
         old="        elif not isinstance(part, (bytes, bytearray, memoryview)):",
         new="        elif False:",
         cases=("trace_bounds__a_capture_belongs_to_its_own_request",))),
)


@pytest.mark.parametrize("name,expected,mutant", SELF_TESTS, ids=[t[0] for t in SELF_TESTS])
def test_the_runner_cannot_report_a_false_kill(name, expected, mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.outcome is expected, f"{name}: got {result.outcome} - {result.detail}"
    assert not result.killed or expected is mutation_list.Outcome.killed
    # and only `killed` is accepted by the suite
    assert result.ok is (expected is mutation_list.Outcome.killed)


def test_the_same_defect_is_a_kill_once_its_exception_is_declared():
    """The other half of the classification: an invariant whose honest kill *is* an
    exception (`TraceCapture.add` never raises, R37) declares it, and then the **same
    edit** is a kill rather than a runner error - the self-test above runs it
    undeclared."""
    undeclared = next(mutant for name, _expected, mutant in SELF_TESTS
                      if name == "an_undeclared_exception_death_is_not_a_kill")
    declared = next(m for m in ALL if m.name == "add_raises_on_a_non_byte_part")
    assert declared.dies_by == ("TypeError",)
    assert (declared.old, declared.new) == (undeclared.old, undeclared.new)
    assert mutation_list.run_mutant(declared).killed


def test_a_mutant_that_makes_its_case_hang_is_not_a_kill():
    """A hang is not the proof the contract asks for, and a runner that waits for ever
    proves nothing at all. Two seconds against a thirty-second sleep."""
    impatient = mutation_list.Runner(name="contracts-timeout",
                                     targets=mutation_list.CONTRACTS.targets, timeout_s=2)
    hangs = mutation_list.Mutant(
        name="self_hang", invariant="a hang is not evidence",
        file="contracts/fakes/state.py", old="MAX_READ_LIMIT = 1000",
        new="MAX_READ_LIMIT = 1000\nimport time as _t; _t.sleep(30)",
        cases=("dur_admit__a_request_uuid_is_admitted_once",))
    result = mutation_list.run_mutant(hangs, impatient)
    assert result.outcome is mutation_list.Outcome.broken_runner, result
    assert "did not finish" in result.detail


def test_every_subprocess_gets_its_own_cache_and_temporary_directory():
    """F2R item 9: two mutants running at once must not share a bytecode cache or a
    `TMPDIR`, and neither may be the worktree's. The runner sets both to directories
    inside the throwaway copy, which is what `--list` cannot prove and a source read can."""
    source = (mutation_list.API_DIR / "tests" / "contracts" / "mutants.py").read_text()
    for name in ("PYTHONPYCACHEPREFIX", "TMPDIR"):
        assert f'"{name}": str(' in source, name
    assert 'cache, temp = root / ".pycache", root / ".tmp"' in source


def test_a_known_lethal_mutant_is_killed_for_the_right_reason():
    """The positive control: the same machinery reports a real kill, and the failing
    test id is the case the mutant names."""
    mutant = next(m for m in ALL if m.name == "heartbeat_stores_the_callers_lease")
    result = mutation_list.run_mutant(mutant)
    assert result.killed and "1 failed" in result.detail


def test_the_fakes_skip_no_conformance_case():
    """R32: a case that returns early for a missing optional hook must be reported as
    skipped, never counted as run. The fakes provide every hook, so nothing skips - and
    this is what keeps `pytest -q` honest about the numbers."""
    from infrx.contracts.fakes import FACTORIES
    from infrx.contracts.conformance import OPTIONAL_HOOKS
    for port, factory in FACTORIES.items():
        harness = factory()
        # `failures` is a Harness field, not an `extra` entry, but it is read through
        # the same `hook()` helper, so it counts as provided when it is not None.
        provided = set(harness.extra) | ({"failures"} if harness.failures is not None else set())
        missing = OPTIONAL_HOOKS.get(port, frozenset()) - provided
        assert missing == set(), f"the {port} fake is missing hooks {sorted(missing)}"


def test_the_in_process_kill_rule_is_the_same_rule():
    """F2R item 9: track D's in-process checks use `assertion_kill`, so its classification is
    proved here without a database: an assertion is a kill, silence is a survivor, any other
    exception is a broken check unless declared."""
    Outcome = mutation_list.Outcome

    def asserts():
        raise AssertionError("noticed")

    def crashes():
        raise KeyError("boom")

    assert mutation_list.assertion_kill(asserts).outcome is Outcome.killed
    assert mutation_list.assertion_kill(lambda: None).outcome is Outcome.survived
    assert mutation_list.assertion_kill(crashes).outcome is Outcome.broken_runner
    assert mutation_list.assertion_kill(crashes, dies_by=(KeyError,)).outcome is Outcome.killed


# --- R83 amendment (coordinator ruling after the F2R-A review) --------------------------
def test_an_assertion_inside_the_package_is_not_the_cases_observation():
    """(a) An `assert` that fires inside `infrx/` is the package's own guard; only the
    exported conformance cases (which live in the package) and the tests assert on the
    case's behalf. The guard is evidence only when the mutant declares it."""
    report = ("=================================== FAILURES ===================================\n"
              "/x/infrx/contracts/fakes/feedback.py:119: AssertionError: guard\n"
              "/x/infrx/contracts/conformance/services.py:9: AssertionError: the case\n"
              "/x/tests/contracts/test_a.py:3: assert 1 == 2\n"
              "=========================== short test summary info ============================\n")
    deaths = mutation_list._death_kinds(report)
    honest = set(mutation_list.HONEST_DEATHS)
    assert mutation_list._undeclared(deaths, honest) == ["AssertionError@feedback.py"]
    assert mutation_list._undeclared(deaths, honest, ("AssertionError",)) == []


def _with_test_file(body: str):
    """A runner whose copy carries one extra test file (the copy only - never the tree)."""
    def layout(root):
        api = mutation_list._copy(root, mutation_list.CONTRACTS)
        (api / "tests" / "contracts" / "test_zz_runner_self_check.py").write_text(body)
        return api
    return mutation_list.Runner(name="contracts-self-check", layout=layout,
                                targets=("tests/contracts/test_zz_runner_self_check.py",))


def test_a_list_whose_cases_fail_unmutated_is_refused():
    """(b) The pristine baseline: a named case that fails on the unmutated tree would
    'kill' every mutant naming it, so the list is refused before any mutant runs - and
    `run_mutant` consults it for every mutant that belongs to a loaded list."""
    runner = _with_test_file("def test_fails_on_the_pristine_tree():\n    assert False\n")
    refused = mutation_list.pristine(("test_fails_on_the_pristine_tree",), runner)
    assert refused is not None and refused.outcome is mutation_list.Outcome.broken_runner
    assert "pristine baseline" in refused.detail
    assert "test_fails_on_the_pristine_tree" in refused.detail
    assert mutation_list._siblings(ALL[0]) is mutation_list.MUTANTS
    assert mutation_list.pristine(tuple(sorted({c for m in ALL for c in m.cases})),
                                  mutation_list.CONTRACTS) is None


def test_a_fixture_error_with_no_test_run_is_a_broken_runner():
    """(c) A setup error is `1 error`, not `N passed/failed`: the copy is broken, the
    mutant did not merely name no case."""
    runner = _with_test_file("import pytest\n\n\n@pytest.fixture\ndef boom():\n"
                             "    raise RuntimeError('setup')\n\n\n"
                             "def test_needs_a_fixture_that_errors(boom):\n    pass\n")
    noop = mutation_list.Mutant(
        name="self_fixture_error", invariant="a setup error is not evidence",
        file="contracts/fakes/state.py", old="MAX_READ_LIMIT = 1000",
        new="MAX_READ_LIMIT = 1000  # no-op", cases=("test_needs_a_fixture_that_errors",))
    result = mutation_list.run_mutant(noop, runner)
    assert result.outcome is mutation_list.Outcome.broken_runner, result
