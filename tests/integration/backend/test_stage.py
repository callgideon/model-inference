"""The `backend` stage's own arithmetic (run.py --layer 3): what counts as run, pending,
detected and failed. Layer 1 - no container; the JUnit XML is written by hand."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run                                              # noqa: E402

XML = """<testsuites><testsuite>
<testcase classname="b.test_drills" name="test_e3b_dr01[fake]"/>
<testcase classname="b.test_schema" name="test_e3b_db03_detects_a_missing_settlement_guard"/>
<testcase classname="b.test_drills" name="test_e3b_dr01[postgres]">
  <skipped message="PENDING[D2] no adapter"/></testcase>
<testcase classname="b.test_journey" name="test_backend_journey[sync-text]">
  <skipped message="Skipped: PENDING[G1R,G2] not mounted"/></testcase>
%s
</testsuite></testsuites>"""


def test_pending_cases_are_counted_by_their_unblocking_id_and_never_as_passes():
    cases = run.classify(XML % "")
    assert cases["passed"] == ["b.test_drills::test_e3b_dr01[fake]",
                               "b.test_schema::test_e3b_db03_detects_a_missing_settlement_guard"]
    assert cases["pending"] == {"D2": ["b.test_drills::test_e3b_dr01[postgres]"],
                                "G1R": ["b.test_journey::test_backend_journey[sync-text]"],
                                "G2": ["b.test_journey::test_backend_journey[sync-text]"]}
    assert run.backend_verdict(cases, 0) == run.PENDING


def test_a_failure_a_plain_skip_or_an_empty_run_fails_the_stage():
    failing = run.classify(XML % '<testcase classname="x" name="y"><failure/></testcase>')
    assert failing["failed"] == ["x::y"] and run.backend_verdict(failing, 1) == run.FAIL
    skipped = run.classify(XML % '<testcase classname="x" name="z">'
                                 '<skipped message="no stack"/></testcase>')
    assert skipped["skipped"] == ["x::z"] and run.backend_verdict(skipped, 0) == run.FAIL
    empty = run.classify("<testsuites/>")
    assert run.backend_verdict(empty, 0) == run.FAIL
    assert run.backend_verdict(run.classify(XML % ""), 2) == run.FAIL


def test_only_a_fully_run_suite_passes():
    done = run.classify("<testsuites><testcase classname='a' name='b'/></testsuites>")
    assert run.backend_verdict(done, 0) == run.PASS
