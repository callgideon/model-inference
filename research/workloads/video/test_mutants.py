"""Every decision in check_discovery.py is killed by a named single edit (LANE-RULES addendum).

A mutant is killed only when every case it names fails against the mutated checker; a case
no mutant names is itself a failure. Run: python3 -m unittest test_mutants -v
"""
import types
import unittest
from pathlib import Path
from unittest import mock

import test_check_discovery as T

SRC = Path(T.C.__file__)
# (name, old, new, cases that must all fail)
MUTANTS = (
    ("manifest_status_unchecked", "    if not re.search(r\"^\\*\\*Manifest status:\\*\\* planned\\b\", text, re.M):",
     "    if False:", ("test_manifest_status_must_stay_planned",)),
    ("missing_table_passes", 'return errors + ["no `## Contract fields` table"]', "return errors",
     ("test_missing_contract_table_fails",)),
    ("missing_field_skipped", '            errors.append(f"{field}: required contract field missing")\n', "",
     ("test_missing_required_field_fails",)),
    ("any_input_id_accepted", "or marker.group(1) not in spec.inputs", "", ("test_blocked_must_name_an_allowed_input",)),
    ("blocked_without_detail", r'r"BLOCKED: (P-\d+) — (.{12,})"', r'r"BLOCKED: (P-\d+)(.*)"',
     ("test_blocked_must_state_what_is_missing",)),
    ("unsourced_value_accepted", "elif source in EMPTY:", "elif False:", ("test_unsourced_value_fails",)),
    ("blocked_trial_not_forced", "if blocked and not", "if False and not", ("test_blocked_row_forces_blocked_trial_status",)),
    ("log_position_unchecked", "if not headings or not re.fullmatch", "if not headings or False and not re.fullmatch",
     ("test_log_must_be_the_last_section",)),
    ("table_parse_wrong_column", "out[cells[0]] = (cells[1], cells[2])", "out[cells[0]] = (cells[1], cells[1])",
     ("test_unsourced_value_fails",)),
    ("trial_status_wrong_word", r'r"^\*\*Trial status:\*\* BLOCKED"', r'r"^\*\*Trial status:\*\* APPROVED"',
     ("test_valid_fixture_passes", "test_real_documents_pass")),
)


def run(old, new, cases):
    """Names of the listed cases that still pass against the mutated checker."""
    src = SRC.read_text()
    assert src.count(old) == 1, f"anchor not unique/present: {old!r}"
    module = types.ModuleType("check_discovery")
    module.__file__ = str(SRC)
    exec(compile(src.replace(old, new), str(SRC), "exec"), module.__dict__)
    survivors = []
    with mock.patch.object(T, "C", module):
        for case in cases:
            result = unittest.TestResult()
            T.Checks(case).run(result)
            if result.wasSuccessful():
                survivors.append(case)
    return survivors


class Mutants(unittest.TestCase):
    def test_every_case_is_covered_by_a_mutant(self):
        cases = {n for n in dir(T.Checks) if n.startswith("test_")}
        self.assertEqual(cases - {c for m in MUTANTS for c in m[3]}, set())

    def test_the_list_is_well_formed(self):
        self.assertEqual(len({m[0] for m in MUTANTS}), len(MUTANTS))
        for name, old, new, cases in MUTANTS:
            self.assertTrue(cases and old != new, name)

    def test_every_mutant_is_killed(self):
        for name, old, new, cases in MUTANTS:
            with self.subTest(name):
                self.assertEqual(run(old, new, cases), [], f"{name} survived")

    def test_a_no_op_survives(self):
        # the runner cannot report a false kill
        self.assertEqual(run("EMPTY = {", "EMPTY = {  # no-op\n    ", ("test_valid_fixture_passes",)),
                         ["test_valid_fixture_passes"])


if __name__ == "__main__":
    unittest.main()
