"""The `backend` stage's own arithmetic (run.py --layer 3): what counts as run, pending,
detected and failed. Layer 1 - no container; the JUnit XML is written by hand."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run                                              # noqa: E402
import stack                                            # noqa: E402

import harness                                          # noqa: E402

XML = """<testsuites><testsuite>
<testcase classname="b.test_drills" name="test_e3b_dr01[fake]"/>
<testcase classname="b.test_schema" name="test_e3b_db03_detects_a_missing_settlement_guard"/>
<testcase classname="b.test_drills" name="test_e3b_dr01[postgres]">
  <skipped message="PENDING[X1] no adapter"/></testcase>
<testcase classname="b.test_journey" name="test_backend_journey[sync-text]">
  <skipped message="Skipped: PENDING[X2,X3] not mounted"/></testcase>
%s
</testsuite></testsuites>"""
# X1-X3 are no task: the arithmetic must not depend on which tasks tasks.json has merged
# (a merged id is stale - `test_a_pending_id_naming_a_merged_task_fails_the_stage`).


def test_pending_cases_are_counted_by_their_unblocking_id_and_never_as_passes():
    cases = run.classify(XML % "")
    assert cases["passed"] == ["b.test_drills::test_e3b_dr01[fake]",
                               "b.test_schema::test_e3b_db03_detects_a_missing_settlement_guard"]
    assert cases["pending"] == {"X1": ["b.test_drills::test_e3b_dr01[postgres]"],
                                "X2": ["b.test_journey::test_backend_journey[sync-text]"],
                                "X3": ["b.test_journey::test_backend_journey[sync-text]"]}
    assert run.backend_verdict(cases, 0) == run.PENDING
    status, summary = run.backend_summary(cases, 0)
    assert status == run.PENDING, "the stage's own status, not only the verdict helper"
    assert (summary["passed"], summary["pending"], summary["failed"]) == (2, 2, 0)
    assert summary["detected"] == ["test_e3b_db03_detects_a_missing_settlement_guard"]
    assert summary["pending_by_id"] == {"X1": 1, "X2": 1, "X3": 1}


def test_an_expected_failure_is_not_pending_even_if_it_says_so():
    """rv08: `pytest.xfail("PENDING[D2] ...")` is not `stack.pending()`: it lands in
    `skipped`, which fails the stage."""
    cases = run.classify(XML % '<testcase classname="x" name="xf"><skipped '
                               'type="pytest.xfail" message="PENDING[X1] later"/></testcase>')
    assert cases["skipped"] == ["x::xf"] and "x::xf" not in cases["pending"].get("X1", [])
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
                                       postgrest_down=lambda: [], RESIDUAL=stack.RESIDUAL)
    monkeypatch.setattr(run, "_backend_stack", lambda: fake_stack)
    monkeypatch.setattr(run, "shell", fake_shell)
    report = run.Report()
    run.backend(report)
    stage = next(entry for entry in report.stages if entry["stage"] == "backend")
    assert stage["status"] == run.PENDING
    assert (stage["detail"]["passed"], stage["detail"]["pending"]) == (2, 2)
    assert report.exit_code == 3


# ------------------------------------------------------------------ E3B phase 2, item 1

def test_a_drill_pends_only_on_the_stubs_it_drives():
    """1a: D6's three stubs never go away in backend-first scope, so the probe must be per
    drill - a drill that drives no stub runs (dr01 drives `admit` only), and one that does
    pends on the task that stub names."""
    owners = {"append": "D4", "terminalize": "D5", "accept_feedback": "D6",
              "reserve_judge": "D6", "record_submission": "D6"}
    assert stack.stubbed(("admit",), owners) == {}
    assert stack.stubbed(("admit", "claim", "append"), owners) == {"append": "D4"}
    assert stack.stubbed(("terminalize", "cancel"), {}) == {}


def test_no_pending_id_names_a_merged_task_unless_it_is_a_named_residual(monkeypatch):
    """1c: every pending id is a task of tasks.json - or one of I3B's owner references
    (`recoverykit.OWNERS`: work no task schedules, named by its document and owner) - and one
    tasks.json marks implemented or integrated is a blocker only as a RESIDUAL with its reason,
    and never for an E3B case (`stack.pending` refuses it)."""
    tasks = {task["id"]: task["status"] for task in json.loads(
        (harness.REPO_ROOT / "research" / "plan" / "tasks.json").read_text())["tasks"]}
    # Review H2: every vocabulary a counted pending comes through - E3B's and I3B's kit.
    sys.path.insert(0, str(Path(__file__).resolve().parent / "recovery"))
    import recoverykit
    vocabulary = {**stack.PENDING, **recoverykit.PENDING}
    kit_owners = getattr(recoverykit, "OWNERS", {})        # the I3B follow-up adds OWNERS
    # R3-1: an owner both name (G2-R1) is one reference - the same text on both sides.
    assert all(stack.OWNERS[o] == kit_owners[o] for o in set(stack.OWNERS) & set(kit_owners))
    owners = set(stack.OWNERS) | set(kit_owners)
    # ... which are no task, and which the stage's PENDING[..] parser reads whole.
    assert owners.isdisjoint(tasks) and all(run.PENDING_MARK.fullmatch(f"PENDING[{o}]")
                                            for o in owners)
    assert set(vocabulary) - owners <= set(tasks), set(vocabulary) - owners - set(tasks)
    merged = {task for task in vocabulary if tasks.get(task) in ("implemented", "integrated")}
    assert merged <= set(stack.RESIDUAL), (merged, set(stack.RESIDUAL))
    # R3-1: a RESIDUAL id this tree's tasks.json does not yet mark merged (G2, merged on the
    # integration head with its cutover held) must name the owner reference replacing it.
    for task in set(stack.RESIDUAL) - merged:
        assert any(owner in stack.RESIDUAL[task] for owner in owners), (task, merged)
    assert all(reason.strip() for reason in stack.RESIDUAL.values())
    import pytest
    # A synthetic RESIDUAL id as well, so the refusal stays pinned the day RESIDUAL empties.
    monkeypatch.setitem(stack.PENDING, "X9", "synthetic")
    monkeypatch.setitem(stack.RESIDUAL, "X9", "synthetic")
    refused = []
    for task in stack.RESIDUAL:
        try:
            stack.pending(task, why="an E3B case may not name a merged task")
        except AssertionError:
            refused.append(task)
        except pytest.skip.Exception:            # it pended: the refusal is gone
            pass
    assert refused == list(stack.RESIDUAL), refused


def test_a_pending_id_naming_a_merged_task_fails_the_stage():
    """Review H2: whatever vocabulary a `PENDING[..]` skip came through, an id tasks.json
    marks implemented OR integrated fails the stage (D1R is implemented, D1 integrated). A
    RESIDUAL id is excused only in I3B's recovery cases (R3-1, cd8099b; the held-cutover case
    pins that half); an owner reference is no task and never stale."""
    cases = run.classify(XML % '<testcase classname="x" name="stale"><skipped '
                               'message="PENDING[D1R] a merged task"/></testcase>'
                               '<testcase classname="x" name="old"><skipped '
                               'message="PENDING[D1] an integrated task"/></testcase>')
    assert run.stale_pending(cases) == ["D1", "D1R"]
    status, summary = run.backend_summary(cases, 0)
    assert (status, summary["stale_pending"]) == (run.FAIL, ["D1", "D1R"])
    # An owner reference (I3B's recoverykit.OWNERS) is no task: pending, never stale.
    owner = run.classify(XML % '<testcase classname="x" name="rc08b"><skipped '
                               'message="PENDING[I2B-R4] no worker entry point"/></testcase>')
    assert owner["pending"]["I2B-R4"] == ["x::rc08b"] and run.stale_pending(owner) == []
    assert run.backend_summary(run.classify(XML % ""), 0)[1]["stale_pending"] is None


def test_e3b_cases_pend_on_the_held_cutover_never_on_a_merged_task(monkeypatch):
    """R3-1: a merged task whose cutover is held (G2, G3) is no pending owner; the cutover
    request is (`G2-R1`). Every E3B case that waits on the cutover - 9 journeys, the dataset
    resume, dr11 - is run here and its skip read back: each names G2-R1 and none names a
    merged task (`stack.pending` refuses a RESIDUAL id outright). `stale_pending` states the
    rule for any skip: RESIDUAL excuses a merged id only in I3B's recovery cases."""
    import functools

    import pytest
    import test_drills
    import test_journey
    runs = [functools.partial(test_journey.test_backend_journey, kind, mode)
            for kind in test_journey.BY_INPUT for mode in test_journey.BY_MODE]
    runs += [test_journey.test_backend_journey__dataset_client_resume,
             test_drills.test_e3b_dr11_client_disconnect_mid_stream_is_pending]
    pended, refused = {}, []
    for number, case in enumerate(runs):
        try:
            with pytest.raises(pytest.skip.Exception) as skipped:
                case()
        except AssertionError as refusal:           # stack.pending refused a merged id
            refused.append(str(refusal))
            continue
        for task in run.PENDING_MARK.search(skipped.value.msg).group(1).split(","):
            pended.setdefault(task, []).append(f"b.test_journey::cutover{number}")
    assert refused == [], f"E3B cases keyed on a merged task: {refused}"
    assert len(pended.get("G2-R1", ())) == len(runs) == 11, pended
    assert run.stale_pending({"pending": pended}) == [], pended
    # The rule itself, on an id merged on every tree (G1R), made RESIDUAL for the check.
    monkeypatch.setitem(stack.RESIDUAL, "G1R", "a merged task I3B still names")
    e3b = "b.test_journey::test_backend_journey[sync-text]"
    i3b = "tests.integration.backend.recovery.test_recovery::test_i3b_rc03"
    assert run.stale_pending({"pending": {"G1R": [e3b, i3b]}}) == ["G1R"]
    assert run.stale_pending({"pending": {"G1R": [i3b], "G2-R1": [e3b]}}) == []


def test_a_live_defect_is_refused_outside_this_processs_clones(monkeypatch):
    """Review F6: `stack.defect` edits only a clone this process made - never the template,
    E2's database or another process's clone - and refuses before it connects anywhere."""
    import contextlib
    import types

    import psycopg
    import pytest
    reached = []                      # a connection the guard should have prevented
    monkeypatch.setattr(psycopg, "connect", lambda dsn, **kw: reached.append(dsn) or
                        contextlib.nullcontext(types.SimpleNamespace(execute=lambda *a: None)))
    for name in (stack.TEMPLATE, stack.harness.PG_DATABASE, f"{stack.harness.PG_DATABASE}_1_1"):
        monkeypatch.setattr(stack, "current_database", lambda name=name: name)
        with pytest.raises(AssertionError, match="refusing a defect"):
            stack.defect("select 1")
    assert reached == [], f"a defect reached {reached}"
