"""E3C s12 (VERIFY-REPRO): the verdict's own rules, with no stack - a missing service is
BLOCKED, a skip is never a pass, a red case fails the gate, a negative control counts only
over a passing scenario; and the matrix covers the brief. Collected by `pytest
tests/integration` (layer 1) and by the runner (these are s12's cases)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import runner                                           # noqa: E402

TASKS = json.loads((HERE.parents[3] / "research/plan/tasks.json").read_text())["tasks"]


def junit(*cases: tuple[str, str, str]) -> str:
    """(name, outcome, message) -> a JUnit document as pytest writes it."""
    body = []
    for name, outcome, message in cases:
        inner = {"pass": "", "fail": f'<failure message="{message}"/>',
                 "error": f'<error message="{message}"/>',
                 "skip": f'<skipped type="pytest.skip" message="{message}"/>',
                 "xfail": f'<skipped type="pytest.xfail" message="{message}"/>'}[outcome]
        body.append(f'<testcase classname="m" name="{name}">{inner}</testcase>')
    return f"<testsuites><testsuite>{''.join(body)}</testsuite></testsuites>"


def test_s12_a_missing_or_skipped_case_is_never_a_pass():
    result = runner.classify(junit(
        ("test_s01_journey", "pass", ""),
        ("test_s02_two_gateways", "skip", "BLOCKED[D10,M5] upload port absent"),
        ("test_s03_upload", "skip", "no stack"),
        ("test_s04_ready", "skip", "INVALID[premise] engine answered nothing"),
        ("test_s05_crash[claim]", "xfail", "known"),
        ("test_s06_retention", "error", "boom")), required={})
    status = {sid: entry["status"] for sid, entry in result["scenarios"].items()}
    assert (status["s01"], status["s02"], status["s03"], status["s04"], status["s05"],
            status["s06"]) == ("PASS", "BLOCKED", "NOT RUN", "INVALID", "NOT RUN", "FAIL")
    assert status["s07"] == "NOT RUN", "a scenario with no case in the report ran nothing"
    assert runner.gate(result) == "FAIL"


def test_s12_the_gate_is_the_worst_status_and_exits_as_e2c_does():
    only_pass = junit(*((f"test_{sid}_x", "pass", "") for sid in runner.SCENARIOS),
                      *((f"{c['case']}_x", "pass", "") for c in runner.CONTROLS.values()
                        if c.get("case")),
                      *((f"test_nc_{nc[3:].replace('-', '_')}__{c['scenario']}_x", "pass", "")
                        for nc, c in runner.CONTROLS.items()))
    result = runner.classify(only_pass, required={})
    # the two revert-type controls cannot run on one tree: the gate stays open
    assert {nc for nc, entry in result["controls"].items() if entry["status"] != "PASS"} == \
        {nc for nc, c in runner.CONTROLS.items() if c.get("revert")}
    assert runner.gate(result) == "NOT RUN" and runner.EXIT["NOT RUN"] == 3
    blocked = runner.classify(only_pass.replace(
        '<testcase classname="m" name="test_s03_create_put_complete_x"></testcase>',
        '<testcase classname="m" name="test_s03_create_put_complete_x"><skipped '
        'message="BLOCKED[M5] x"/></testcase>'), required={})
    assert blocked["scenarios"]["s03"]["status"] == "BLOCKED"
    assert blocked["controls"]["nc-upload-restart"]["status"] == "NOT RUN", \
        "a control over a scenario that did not pass proves nothing"
    assert runner.gate(blocked) == "BLOCKED"
    assert (runner.EXIT["PASS"], runner.EXIT["FAIL"], runner.EXIT["BLOCKED"],
            runner.EXIT["INVALID"]) == (0, 1, 3, 4)


def test_s12_a_control_that_is_not_detected_fails_the_gate():
    result = runner.classify(junit(("test_s01_cli_identity_x", "pass", ""),
                                   ("test_nc_journey_revoke__s01_x", "fail", "not detected")))
    assert result["controls"]["nc-journey-revoke"]["status"] == "FAIL"
    assert runner.gate(result) == "FAIL"


def test_s12_a_control_counts_over_the_case_it_guards_not_the_whole_scenario():
    """nc-credit-cutover removes the grant's uniqueness: it is meaningful when the grant case
    passes, whatever another s09 case waits for; over a red guarded case it proves nothing."""
    result = runner.classify(junit(
        ("test_s09_concurrent_signup_callbacks_x", "pass", ""),
        ("test_s09_the_credit_transition_x", "skip", "BLOCKED[G8] no command"),
        ("test_nc_credit_cutover__s09_x", "pass", ""),
        ("test_s07_one_persisted_expiry_x", "fail", "RV-11"),
        ("test_nc_result_expiry__s07_x", "pass", "")))
    assert result["scenarios"]["s09"]["status"] == "BLOCKED"
    assert result["controls"]["nc-credit-cutover"]["status"] == "PASS"
    assert result["controls"]["nc-result-expiry"]["status"] == "NOT RUN"


def test_s12_the_matrix_covers_the_brief_and_the_task():
    task = next(t for t in TASKS if t["id"] == "E3C")
    covered = {test_id for s in runner.SCENARIOS.values() for test_id in s["test_ids"]}
    assert set(task["test_ids"]) <= covered, set(task["test_ids"]) - covered
    assert {s["row"] for s in runner.SCENARIOS.values()} >= set(range(1, 10)), \
        "every row of the brief's scenario table has a scenario"
    lanes = {lane for s in runner.SCENARIOS.values() for lane in s["lanes"]}
    assert lanes <= set(task["integration_dependencies"]) | set(task["start_dependencies"])
    oracles = {c["oracle"] for c in runner.CONTROLS.values()}
    corrective = {"UPLOAD-RESTART", "RETENTION-DURABLE", "ADMISSION-READY", "RESULT-EXPIRY",
                  "CREDIT-CUTOVER", "BACKEND-JOURNEY", "VERIFY-REPRO"}
    assert corrective <= oracles, corrective - oracles


def test_s12_scenario_modules_are_not_collected_by_the_default_suites():
    """`pytest tests/integration` (E2's layer 1 and E3B's backend stage) must not collect a
    red scenario: they run only through the runner, which names them."""
    files = [Path(f).name for f in runner.scenario_files()]
    assert files[:-1] and all(name.startswith("scenarios_") for name in files[:-1])


def test_s12_every_fault_point_and_bypass_names_code_that_exists():
    """A renamed seam must fail HERE, loudly, not turn a crash drill into a no-op."""
    import world
    for point, candidates in world.POINTS.items():
        assert all(c[3] in ("before", "after") for c in candidates), point
        # `readiness` may legitimately vanish (F2C-L R110: one-phase admission); every other
        # step of the brief's crash list must exist in the tree
        assert world.has_point(point) or point == "readiness", point
    assert set(world.BYPASSES) >= {"upload-local", "expiry-recompute", "revoke-ignored",
                                   "tenant-blind"}


@pytest.mark.parametrize("name", ["upload-local", "expiry-recompute", "revoke-ignored",
                                  "tenant-blind"])
def test_s12_every_bypass_installs_on_this_tree(name):
    """A negative control whose bypass targets code the tree no longer has is INVALID, not a
    proof: each bypass must install (in a fresh process - it monkeypatches the tree). E3C
    rerun: G7 removed `Jobs.result_expiry`, so the phase-2 `expiry-recompute` could not
    install; it now rewrites the outcome `Relay._owned` returns."""
    import subprocess
    done = subprocess.run([sys.executable, "-c", f"import world; world.BYPASSES[{name!r}]()"],
                          cwd=str(HERE), capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-800:]


def test_s12_the_namespace_is_the_reserved_block():
    """WR-1's row, however it is provided: the e3c layout is tasklocal's 56900-56999."""
    import world
    from infrx.contracts import tasklocal
    block = tasklocal.local_services("e3c")
    assert block["compose"].host_port == 55500 + world.OFFSET
    assert block["postgres"].host_port == 55532 + world.OFFSET


@pytest.mark.parametrize("lanes", [(), ("Q9",)])
def test_s12_blocked_must_name_known_lanes(lanes):
    import world
    with pytest.raises(AssertionError, match="known lanes"):
        world.blocked(*lanes, why="x")


def test_s12_no_stack_blocks_every_scenario():
    """VERIFY-REPRO: a service that cannot be provisioned is BLOCKED for every scenario and
    the gate exits 3 - measured live too (the pinned MinIO digest answering 401)."""
    result = runner.blocked_all(runner.classify("<testsuites/>"), "services: pull failed")
    assert {e["status"] for e in result["scenarios"].values()} == {"BLOCKED"}
    assert runner.gate(result) == "BLOCKED" and runner.EXIT[runner.gate(result)] == 3


def test_nc_verify_repro__s12_a_required_case_that_did_not_run_keeps_the_gate_open():
    """Negative control for VERIFY-REPRO: remove one required case from an otherwise green
    report (a skip, or simply absent) - the gate must not pass."""
    green = [(f"test_{sid}_x", "pass", "") for sid in runner.SCENARIOS]
    for broken in ([c for c in green if c[0] != "test_s05_x"],
                   [c if c[0] != "test_s05_x" else ("test_s05_x", "skip", "no stack")
                    for c in green]):
        result = runner.classify(junit(*broken))
        assert result["scenarios"]["s05"]["status"] == "NOT RUN"
        assert runner.gate(result) != "PASS"


# ------------------------------------------------------------------ fix round (verification)


def green(required=None) -> list[tuple[str, str, str]]:
    """Every required case of the manifest and every control case, passing."""
    required = runner.REQUIRED if required is None else required
    return [*((name, "pass", "") for names in required.values() for name in names),
            *((f"test_nc_{nc[3:].replace('-', '_')}__{c['scenario']}_x", "pass", "")
              for nc, c in runner.CONTROLS.items())]


def test_s12_a_scenario_missing_a_required_case_is_not_run():
    """0-MUT-2 / 2-ACC-3: a case deselected (`-k`) or deleted leaves no trace in the JUnit;
    the manifest makes its scenario NOT RUN, and the gate cannot pass even once phase 2
    supplies both revert controls."""
    partial = [c for c in green() if not (c[0].startswith("test_s05_") and "[settle]" not in c[0])]
    result = runner.classify(junit(*partial))
    assert result["scenarios"]["s05"]["status"] == "NOT RUN"
    assert any("required case absent" in r for r in result["scenarios"]["s05"]["reasons"])
    for nc, control in runner.CONTROLS.items():
        if control.get("revert"):
            result["controls"][nc]["status"] = runner.control_verdict("FAIL")
    assert runner.gate(result) == "NOT RUN"
    whole = runner.classify(junit(*green()))
    for nc, control in runner.CONTROLS.items():
        if control.get("revert"):
            whole["controls"][nc]["status"] = runner.control_verdict("FAIL")
    assert runner.gate(whole) == "PASS", "the manifest itself must be satisfiable"


def test_s12_the_manifest_is_exactly_what_the_scenario_modules_define():
    """A case deleted from a module (or added without the manifest) fails HERE."""
    import subprocess
    done = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q",
                           "-p", "no:cacheprovider", *runner.scenario_files()],
                          cwd=str(HERE), capture_output=True, text=True, timeout=120)
    collected = {line.split("::", 1)[1] for line in done.stdout.splitlines() if "::" in line}
    plain = {name for name in collected if not name.startswith("test_nc_")}
    assert plain == {n for names in runner.REQUIRED.values() for n in names}, done.stdout[-800:]


@pytest.mark.parametrize("reverted,expected", [
    ("FAIL", "PASS"), ("PASS", "FAIL"), ("BLOCKED", "BLOCKED"), ("NOT RUN", "NOT RUN"),
    ("INVALID", "INVALID")])
def test_s12_a_revert_control_passes_only_when_the_reverted_tree_is_red(reverted, expected):
    """0-MUT-3: the only path by which ADMISSION-READY / RETENTION-DURABLE can pass."""
    assert runner.control_verdict(reverted) == expected


@pytest.mark.parametrize("message,expected", [
    ("harness.HarnessError: docker compose up failed", "INVALID"),
    ("psycopg.OperationalError: database infrx_e3c_1_2 does not exist", "INVALID"),
    ("RuntimeError: [Errno 98] address already in use", "INVALID"),
    ("AssertionError: the gateway never reached its fault point in 60s", "INVALID"),
    ("AssertionError: executed before durable eligibility", "FAIL")])
def test_s12_a_harness_error_is_invalid_not_a_product_fail(message, expected):
    """2-ACC-2: infrastructure that broke under the case is INVALID[harness] (still never a
    pass); FAIL stays for assertions about product state."""
    for outcome in ("fail", "error"):
        result = runner.classify(junit(("test_s06_x", outcome, message)), required={})
        assert result["scenarios"]["s06"]["status"] == expected, (outcome, message)


def test_s12_a_held_namespace_lock_blocks_the_run_and_touches_nothing(tmp_path, monkeypatch):
    """0-MUT-1 / 1-RULES-1 / 2-ACC-1: a second runner never provisions, never tears down."""
    import fcntl

    import world                     # E2's harness loads under THIS session's namespace
    assert world.harness
    monkeypatch.setenv("INFRX_E2_NAMESPACE", "e3c")     # main() sets it; restored after
    monkeypatch.setattr(runner, "LOCK", tmp_path / "e3c.lock")

    def touched(*_a, **_k):
        raise AssertionError("a blocked run touched the stack")
    monkeypatch.setattr(runner, "provision", touched)
    monkeypatch.setattr(runner, "teardown", touched)
    with open(runner.LOCK, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert runner.main(["--out", str(tmp_path / "out")]) == 3
    verdict = json.loads((tmp_path / "out" / "verdict.json").read_text())
    assert verdict["verdict"] == "BLOCKED" and verdict["lock"]["held"] is False
    assert all(s["status"] == "BLOCKED" and "another run" in s["reasons"][0]
               for s in verdict["scenarios"])


def test_s12_a_fake_only_box_is_invalid_before_any_process_starts(tmp_path):
    """2-ACC-5: a box not on the namespace's real PostgreSQL / S3 / Valkey in pilot mode is
    refused as INVALID[fake-only] before it spawns anything, and classifies as a non-pass."""
    import world
    real = {"INFRX_MODE": "pilot", "DATABASE_URL": world.harness.pg_dsn("x"),
            "S3_MEDIA_BUCKET": world.harness.S3_BUCKET,
            "S3_ENDPOINT_URL": world.harness.s3_endpoint(),
            "VALKEY_URL": world.harness.valkey_url()}
    assert world.fake_only(real) == []
    for name, value in (("INFRX_MODE", "local"), ("DATABASE_URL", ""),
                        ("S3_ENDPOINT_URL", "http://127.0.0.1:1"), ("VALKEY_URL", "")):
        box = world.Box({**real, name: value}, "http://127.0.0.1:1", tmp_path, 1)
        with pytest.raises(pytest.skip.Exception, match=r"INVALID\[fake-only\]") as skipped:
            box.start("gateway")
        assert not box.processes
        result = runner.classify(junit(("test_s02_x", "skip", str(skipped.value))),
                                 required={})
        assert result["scenarios"]["s02"]["status"] == "INVALID"


def test_s12_the_readiness_oracle_and_admission_point_follow_d10s_port(monkeypatch):
    """2-ACC-4: D10 puts `admit_ready`/`readiness` on `infrx.state.lifecycle.PgLifecycle`;
    the oracle and the admission hold must find them there, not only on PgJobStore."""
    import types
    import world
    assert world.POINTS["admission"][0][:3] == ("infrx.state.lifecycle", "PgLifecycle",
                                                "admit_ready")

    class PgLifecycle:
        def __init__(self, connect, **_kw):
            self.connect = connect

        async def readiness(self, job_id):
            return {"job_id": job_id}

        async def admit_ready(self, *args):
            return None
    fake = types.ModuleType("infrx.state.lifecycle")
    fake.PgLifecycle = PgLifecycle
    monkeypatch.setitem(sys.modules, "infrx.state.lifecycle", fake)
    reader = world.readiness_reader("postgresql://nobody@127.0.0.1:1/x")
    assert reader is not None and reader.__self__.__class__ is PgLifecycle
    assert world.point_target("admission")[0] is PgLifecycle


def test_s12_a_barrier_holds_on_whichever_candidate_the_process_calls(monkeypatch, tmp_path):
    """Phase 2: D10's `PgLifecycle.admit_ready` exists while the gateway still admits through
    `PgJobStore.admit_credit` (G7 composes the former). The admission hold must fire on the
    step actually taken, once - not wait on the first candidate for ever."""
    import asyncio
    import types
    import world

    class First:
        async def admit_ready(self, *args):
            return "never called"

    class Second:
        async def admit_credit(self, job):
            return f"admitted {job}"
    for name, cls in (("e3c_fake_first", First), ("e3c_fake_second", Second)):
        module = types.ModuleType(name)
        setattr(module, cls.__name__, cls)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setitem(world.POINTS, "admission", (
        ("e3c_fake_first", "First", "admit_ready", "after", False),
        ("e3c_fake_second", "Second", "admit_credit", "after", False)))
    monkeypatch.setattr(First, "admit_ready", First.admit_ready)
    monkeypatch.setattr(Second, "admit_credit", Second.admit_credit)
    marker = tmp_path / "barrier-gateway.json"
    world.install_barrier("admission", marker)

    async def call_then_release():
        task = asyncio.create_task(Second().admit_credit("job-1"))
        for _ in range(100):
            if marker.exists():
                break
            await asyncio.sleep(0.01)
        assert marker.exists() and not task.done(), "the called candidate did not hold"
        assert json.loads(marker.read_text())["jobs"][0] == "job-1"
        marker.unlink()
        assert await task == "admitted job-1"
        assert await Second().admit_credit("job-2") == "admitted job-2"   # once only
        assert not marker.exists()
    asyncio.run(call_then_release())


def test_s12_a_collector_that_cannot_run_is_blocked_not_passed():
    """A collector answering `blocked` (the durable ticket authority with no retention pass:
    M6) makes the case BLOCKED[M6] - never a green 'deleted nothing'."""
    import world
    world.collector_blocked([{"deleted": [], "uploads_expired": 0}])        # a real pass
    with pytest.raises(pytest.skip.Exception, match=r"BLOCKED\[M6\]") as skipped:
        world.collector_blocked([{"deleted": []}, {"blocked": "M6", "why": "no pass"}])
    result = runner.classify(junit(("test_s06_x", "skip", str(skipped.value))), required={})
    assert result["scenarios"]["s06"]["status"] == "BLOCKED"


def test_s12_the_dedicated_runtime_login_is_a_real_box_database():
    """WR-4: a box on `infrx_runtime` over this namespace's PostgreSQL is on the real
    services; the same login on another port is not."""
    from urllib.parse import urlsplit
    import world
    owner = world.harness.pg_dsn("x")
    parts = urlsplit(owner)
    runtime = owner.replace(f"{parts.username}:{parts.password}@", "infrx_runtime:s@", 1)
    real = {"INFRX_MODE": "pilot", "DATABASE_URL": runtime,
            "S3_MEDIA_BUCKET": world.harness.S3_BUCKET,
            "S3_ENDPOINT_URL": world.harness.s3_endpoint(),
            "VALKEY_URL": world.harness.valkey_url()}
    assert world.fake_only(real) == []
    elsewhere = runtime.replace(f":{parts.port}/", ":1/")
    assert world.fake_only({**real, "DATABASE_URL": elsewhere}) == ["DATABASE_URL"]
