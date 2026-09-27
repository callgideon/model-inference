"""Oracles for the discovery-document checker (X1/X3/X5; VIDEO-/ROBOT-/BACKEND-CONTRACT).

Each case names the broken behaviour it catches. Run from this directory:

    cd research/workloads/video && python3 -m unittest test_check_discovery test_mutants -v
"""
import unittest

import check_discovery as C

SPEC = C.Spec(inputs=("P-13", "P-09"), fields=("Deadline", "Transport"))
VALID = """# Doc

**Manifest status:** planned
**Trial status:** BLOCKED: P-13 — see §1.

## Contract fields

| Field | Value | Source |
|---|---|---|
| Deadline | BLOCKED: P-13 — the owner's end-to-end deadline per event class | — |
| Transport | none mounted; finite clips only | `marlin-sop.md` §5.4 |

## Audit log

- 2026-09-27: created.
"""


def errors(text, spec=SPEC):
    return C.check(text, spec)


class Checks(unittest.TestCase):
    def test_valid_fixture_passes(self):
        # catches: a checker that rejects a well-formed document (every rule too strict)
        self.assertEqual(errors(VALID), [])

    def test_real_documents_pass(self):
        # catches: a published discovery document that breaks its own contract rules, or is missing
        for path, spec in C.SPECS.items():
            self.assertTrue((C.ROOT / path).is_file(), path)
            self.assertEqual(C.check((C.ROOT / path).read_text(), spec), [], path)

    def test_missing_required_field_fails(self):
        # catches: a contract that silently omits a failure-oracle field (e.g. no deadline row)
        text = VALID.replace("| Deadline | BLOCKED: P-13 — the owner's end-to-end deadline per event class | — |\n", "")
        self.assertTrue(any("Deadline" in e for e in errors(text)))

    def test_missing_contract_table_fails(self):
        # catches: a document with no contract table passing because there are no rows to check
        text = VALID.replace("## Contract fields", "## Notes")
        self.assertTrue(errors(text))

    def test_unsourced_value_fails(self):
        # catches: an invented acceptance target (a value with no source and no BLOCKED marker)
        text = VALID.replace("BLOCKED: P-13 — the owner's end-to-end deadline per event class | — |", "2 s p95 | — |")
        self.assertTrue(any("Deadline" in e for e in errors(text)))

    def test_blocked_must_name_an_allowed_input(self):
        # catches: a BLOCKED marker that cites no pending input, or one this task does not own
        for bad in ("BLOCKED: — the owner's deadline per event class", "BLOCKED: P-99 — the owner's deadline per event class"):
            text = VALID.replace("BLOCKED: P-13 — the owner's end-to-end deadline per event class", bad)
            self.assertTrue(any("Deadline" in e for e in errors(text)), bad)

    def test_blocked_must_state_what_is_missing(self):
        # catches: "BLOCKED: P-13" with no exact missing input named
        text = VALID.replace("BLOCKED: P-13 — the owner's end-to-end deadline per event class", "BLOCKED: P-13")
        self.assertTrue(any("Deadline" in e for e in errors(text)))

    def test_field_the_trial_status_calls_missing_must_be_blocked(self):
        # catches: a row given a sourced value while the document's own trial status says it was never supplied
        text = VALID.replace("see §1.", "no owner has supplied the transport.")
        self.assertTrue(any("Transport" in e for e in errors(text)))

    def test_blocked_row_forces_blocked_trial_status(self):
        # catches: a trial declared ready while a contract field is still blocked
        text = VALID.replace("**Trial status:** BLOCKED: P-13 — see §1.", "**Trial status:** approved")
        self.assertTrue(any("Trial status" in e for e in errors(text)))

    def test_manifest_status_must_stay_planned(self):
        # catches: discovery promoting its own manifest status before the owner approves
        for bad in ("**Manifest status:** implemented", ""):
            text = VALID.replace("**Manifest status:** planned", bad)
            self.assertTrue(any("Manifest status" in e for e in errors(text)), bad)

    def test_log_must_be_the_last_section(self):
        # catches: a document whose audit log is missing or not the final section
        for text in (VALID.replace("## Audit log", "## History"), VALID + "\n## Later\n\ntext\n"):
            self.assertTrue(any("log" in e for e in errors(text)))


if __name__ == "__main__":
    unittest.main()
