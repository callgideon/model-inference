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
    status, summary = run.backend_summary(cases, 0)
    assert status == run.PENDING, "the stage's own status, not only the verdict helper"
    assert (summary["passed"], summary["pending"], summary["failed"]) == (2, 2, 0)
    assert summary["detected"] == ["test_e3b_db03_detects_a_missing_settlement_guard"]
    assert summary["pending_by_id"] == {"D2": 1, "G1R": 1, "G2": 1}


def test_an_expected_failure_is_not_pending_even_if_it_says_so():
    """rv08: `pytest.xfail("PENDING[D2] ...")` is not `stack.pending()`: it lands in
    `skipped`, which fails the stage."""
    cases = run.classify(XML % '<testcase classname="x" name="xf"><skipped '
                               'type="pytest.xfail" message="PENDING[D2] later"/></testcase>')
    assert cases["skipped"] == ["x::xf"] and "x::xf" not in cases["pending"].get("D2", [])
    assert run.backend_summary(cases, 0)[0] == run.FAIL


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


def test_the_backend_stage_reports_the_summary_of_what_its_suite_produced(monkeypatch):
    """rv07: `backend()` itself - PostgREST and the pytest run stubbed - adds a PENDING stage
    whose counts are the suite's, so the call site cannot bypass the verdict."""
    import types

    def fake_shell(argv, **_):
        junit = next(arg.split("=", 1)[1] for arg in argv if arg.startswith("--junitxml="))
        Path(junit).write_text(XML % "")
        return {"exit": 0, "argv": " ".join(argv)}

    fake_stack = types.SimpleNamespace(postgrest_up=lambda: "postgrest/test",
                                       postgrest_down=lambda: [])
    monkeypatch.setattr(run, "_backend_stack", lambda: fake_stack)
    monkeypatch.setattr(run, "shell", fake_shell)
    report = run.Report()
    run.backend(report)
    stage = next(entry for entry in report.stages if entry["stage"] == "backend")
    assert stage["status"] == run.PENDING
    assert (stage["detail"]["passed"], stage["detail"]["pending"]) == (2, 2)
    assert report.exit_code == 3
