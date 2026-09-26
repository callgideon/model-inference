"""E3A runner rules, no stack needed: `python -m pytest -q tests/integration/app`.

Each test names the broken behaviour it catches (its failure oracle)."""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def load(name: str, file: str):
    """Under a name of its own: E3C's runner is also `runner.py` in the same pytest session."""
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


edge = load("e3a_edge", "edge.py")
runner = load("e3a_runner", "runner.py")

SECRET = "e3a-unit-secret"


def report(*tests) -> dict:
    """A Playwright JSON report with one spec per (title, status, skip reason, error)."""
    specs = [{"title": title, "tests": [{
        "annotations": [{"type": "skip", "description": why}] if why else [],
        "results": [{"status": status, "errors": [{"message": error}] if error else []}]}]}
        for title, status, why, error in tests]
    return {"suites": [{"title": "journey.e2e.ts", "specs": [], "suites": [{"specs": specs}]}]}


def all_passed() -> dict:
    return report(*((f"{name}: x", "passed", "", "") for name in runner.CHECKS))


def test_cells_are_exactly_the_manifests_e3a_test_ids():
    """Oracle: a renamed/dropped test_id in the manifest or a cell with no check under it."""
    tasks = json.loads((runner.REPO / "research/plan/tasks.json").read_text())["tasks"]
    e3a = next(task for task in tasks if task["id"] == "E3A")
    assert list(runner.TEST_IDS) == e3a["test_ids"]
    covered = {tid for ids, _ in runner.CHECKS.values() for tid in ids} | {"APP-JOURNEY"}
    assert covered == set(runner.TEST_IDS)


def test_every_check_is_a_journey_test_title():
    """Oracle: a check the spec file does not define would be NOT RUN for ever, silently."""
    spec = (runner.APP_DIR / "tests/e2e/journey.e2e.ts").read_text()
    titles = {runner.TITLE.match(line.split('test("', 1)[1]).group(1)
              for line in spec.splitlines() if line.startswith('test("')}
    assert titles == set(runner.CHECKS)


def test_all_green_is_pass_and_absent_or_skipped_is_never_pass():
    """Oracle: a skip, a missing test or a deselected check counted as a pass."""
    table = runner.cells(runner.classify(all_passed()))
    assert {c["journey"] for c in table} == {runner.PASS}
    assert {c["id"] for c in table if c["verdict"] != runner.PASS} == set(runner.DELEGATED)
    checks = runner.classify(report(("text-sync: x", "passed", "", "")))
    table = {c["id"]: c["verdict"] for c in runner.cells(checks)}
    assert table["API-MODES"] == runner.NOT_RUN and table["APP-JOURNEY"] == runner.NOT_RUN
    skipped = runner.classify(report(
        ("create-key: x", "skipped", "NOT RUN[C3A,U2] C3A and U2 not merged: absent", "")))
    assert skipped["create-key"]["status"] == runner.NOT_RUN
    assert "C3A" in skipped["create-key"]["reason"]
    only = runner.classify(all_passed(), {"text-sync"})
    assert only["usage-balance"]["status"] == runner.NOT_RUN
    assert only["text-sync"]["status"] == runner.PASS


def test_cells_the_journey_does_not_exercise_are_never_pass():
    """Oracle (0-/1-E3A-PREP-R1): DUR-FENCE, DUR-OUTBOX, DUR-CAP or CREDIT-RATE reported PASS
    although the journey never races a stale worker, loses Valkey state, admits concurrently
    across keys/orgs or publishes a rate; or a red journey check under one of them hidden."""
    assert set(runner.DELEGATED) == {"DUR-CAP", "DUR-FENCE", "DUR-OUTBOX", "CREDIT-RATE"}
    table = {c["id"]: c for c in runner.cells(runner.classify(all_passed()))}
    for test_id in runner.DELEGATED:
        cell = table[test_id]
        assert (cell["verdict"], cell["journey"]) == (runner.NOT_RUN, runner.PASS), cell
        assert cell["reasons"][0].startswith("NOT RUN[delegated]"), cell
    assert table["API-MODES"]["verdict"] == table["API-MODES"]["journey"] == runner.PASS
    tests = [(f"{name}: x", "passed", "", "") for name in runner.CHECKS if name != "rate-rejection"]
    red = runner.cells(runner.classify(report(*tests, ("rate-rejection: x", "failed", "", "429"))))
    assert {c["id"]: c["verdict"] for c in red}["DUR-FENCE"] == runner.FAIL


# The E3C-FINAL evidence's verdict line and scenario rows, in both of its row styles.
EVIDENCE = """- **Verdict: BACKEND-LOCAL PASS.** Gate PASS.
3. **Final run 2 (`27a69619`): the evidence run. Gate PASS.** Verdict `/x/final/verdict.json`
| Scenario | Status | Cases |
|---|---|---|
| s05 crash at each step | PASS | 9/9 |
| s08 dependency outages | PASS | 3/3 |
| s09 CREDIT transition, lock bound | PASS | 4/4 |
| **s13** discovery vs admission | **PASS** | 2/2 |
"""


def test_delegated_cells_pass_only_on_the_e3c_final_reference():
    """Oracle (E3A item 4): a delegated cell reported as a bare PASS, PASS without the
    E3C-FINAL reference (document absent, not the accepted run, a scenario row missing or not
    PASS), or a red journey check under it hidden by the reference."""
    green = runner.classify(all_passed())
    table = {c["id"]: c for c in runner.cells(green, EVIDENCE)}
    want = "PASS[delegated to E3C-FINAL 27a69619 s05,s08]"
    assert table["DUR-OUTBOX"]["verdict"] == want, table["DUR-OUTBOX"]
    assert table["DUR-OUTBOX"]["reasons"][0].startswith(want) and \
        runner.E3C_FINAL["evidence"] in table["DUR-OUTBOX"]["reasons"][0]
    for evidence, why in ((None, "not on this tree"),
                          (EVIDENCE.replace("BACKEND-LOCAL PASS", "BACKEND-LOCAL FAIL"),
                           "does not record"),
                          (EVIDENCE.replace("27a69619", "0000000"), "does not record"),
                          (EVIDENCE.replace("| s08 dependency outages | PASS |",
                                            "| s08 dependency outages | FAIL |"), "s08: FAIL"),
                          (EVIDENCE.replace("| s05 crash at each step | PASS | 9/9 |", ""),
                           "s05: no row")):
        cell = {c["id"]: c for c in runner.cells(green, evidence)}["DUR-OUTBOX"]
        assert cell["verdict"] == runner.NOT_RUN, (why, cell)
        assert why in cell["reasons"][0], (why, cell["reasons"][0])
    tests = [(f"{name}: x", "passed", "", "") for name in runner.CHECKS if name != "async-poll"]
    red = runner.cells(runner.classify(report(*tests, ("async-poll: x", "failed", "", "boom"))),
                       EVIDENCE)
    assert {c["id"]: c["verdict"] for c in red}["DUR-OUTBOX"] == runner.FAIL


def test_an_oracle_no_e3c_scenario_carries_is_not_run_even_with_the_reference():
    """Oracle (0-E3A-RUN-RV-1, 1-S-1, 1-S-2): DUR-FENCE bound to s05 (a SIGKILL, no stale/new
    generation race), DUR-CAP to s09 (grants, no cross-key admission race) or CREDIT-RATE to
    s09/s13 (no published rate change, no unknown/private/unpriced refusal) reads PASS[delegated]
    and turns the gate APP-LOCAL PASS on a green journey and the accepted E3C-FINAL document."""
    uncarried = {"DUR-CAP", "DUR-FENCE", "CREDIT-RATE"}
    table = {c["id"]: c for c in runner.cells(runner.classify(all_passed()), EVIDENCE)}
    for test_id in uncarried:
        cell = table[test_id]
        assert (cell["verdict"], cell["journey"]) == (runner.NOT_RUN, runner.PASS), cell
        assert cell["reasons"][0].startswith("NOT RUN[delegated] NOT carried"), cell
        assert "NOT carried: " in cell["reasons"][0], cell   # the gap is named
    gate = runner.base(runner.worst(c["verdict"] for c in table.values()))
    assert (gate, runner.EXIT[gate]) == (runner.NOT_RUN, 3)
    assert {tid for tid, c in table.items()
            if runner.base(c["verdict"]) != runner.PASS} == uncarried
    assert {tid for tid, (scenarios, _) in runner.DELEGATED.items() if not scenarios} == uncarried


def _need_stack_following_the_env():
    import os
    return os.environ.get("INFRX_E2_NAMESPACE", "e3c")


def _need_stack_pinned():
    return "e3c"


def _template_with_hosted_uid():
    return HOSTED_AUTH_UID  # noqa: F821 - read as source only


def _template_without():
    return None


def test_the_runner_leaves_the_namespace_and_auth_uid_to_e3c():
    """Oracle (E3A-WR-1/2 applied): the runner still pins `world.NAMESPACE` or replaces
    `auth.uid()` per clone (two copies of E3C's harness rules), or runs on a tree without E3C's
    wirings instead of saying BLOCKED (the world stage INVALID, or signed-in reads that see no
    user and deny vacuously)."""
    from types import SimpleNamespace
    source = (HERE / "runner.py").read_text()
    assert "world.NAMESPACE =" not in source and "HOSTED_AUTH_UID" not in source.replace(
        '"HOSTED_AUTH_UID"', "").replace("hasattr(stack, ", "")
    wired = runner.missing_wirings(SimpleNamespace(need_stack=_need_stack_following_the_env),
                                   SimpleNamespace(HOSTED_AUTH_UID="x",
                                                   _template=_template_with_hosted_uid))
    assert wired == []
    bare = runner.missing_wirings(SimpleNamespace(need_stack=_need_stack_pinned),
                                  SimpleNamespace(_template=_template_without))
    assert [m.split(" ")[0] for m in bare] == ["E3A-WR-1", "E3A-WR-2"]
    half = runner.missing_wirings(SimpleNamespace(need_stack=_need_stack_following_the_env),
                                  SimpleNamespace(HOSTED_AUTH_UID="x", _template=_template_without))
    assert [m.split(" ")[0] for m in half] == ["E3A-WR-2"]


def test_a_failure_fails_its_cells_and_a_harness_error_is_invalid():
    """Oracle: a red check hidden by a green sibling, or a harness fault reported as product."""
    tests = [(f"{name}: x", "passed", "", "") for name in runner.CHECKS if name != "low-funds"]
    checks = runner.classify(report(*tests, ("low-funds: x", "failed", "", "expected 402")))
    table = {c["id"]: c["verdict"] for c in runner.cells(checks)}
    assert table["CREDIT-SPEND"] == runner.FAIL and table["DUR-ADMIT"] == runner.FAIL
    assert table["CREDIT-GRANT"] == runner.PASS
    invalid = runner.classify(report(("retry: x", "failed", "", "INVALID[harness] control /facts")))
    assert invalid["retry"]["status"] == runner.INVALID
    assert runner.worst([runner.PASS, runner.NOT_RUN, runner.FAIL]) == runner.FAIL


def test_a_seam_is_detected_only_by_a_failing_guarded_check():
    """Oracle: a broken seam that the journey did not notice reported as detected."""
    red = runner.classify(report(("signup-verify-grant: x", "failed", "", "one grant")))
    assert runner.seam_stage("grant-guard", red)["verdict"] == runner.FAIL
    green = runner.classify(all_passed())
    survived = runner.seam_stage("fixture-port", green)
    assert survived["verdict"] == runner.PASS and "NOT DETECTED" in survived["detail"]


def test_the_app_env_is_loopback_in_the_block_only():
    """Oracle: a hosted Supabase/gateway URL, or a port outside e4b, reaching the App."""
    ok = {"NEXT_PUBLIC_SUPABASE_URL": "http://127.0.0.1:56860", "PATH": "/usr/bin"}
    assert runner.local_env(ok) == ok
    for bad in ("https://abc.supabase.co", "http://127.0.0.1:56932", "http://localhost:56860",
                "https://127.0.0.1:56860", "http://10.0.0.1:56860"):
        with pytest.raises(ValueError):
            runner.local_env({"NEXT_PUBLIC_SUPABASE_URL": bad})
    assert {runner.EDGE_PORT, runner.CONTROL_PORT, runner.APP_PORT} <= set(runner.BLOCK)


def test_the_app_env_states_its_environment():
    """Oracle (1-E3A-PREP-R2): `next start` (NODE_ENV=production) with neither VERCEL_ENV nor
    INFRX_APP_ENVIRONMENT - I2A's instrumentation (lib/deploy/env.ts) refuses to serve, and the
    run goes INVALID on any tree that carries it."""
    env = runner.app_env("anon", "service", 56840)
    assert env["INFRX_APP_ENVIRONMENT"] == "development" and "VERCEL_ENV" not in env
    assert "NODE_ENV" not in env                          # next start sets production itself
    assert env["INFRX_API_BASE_URL"] == "http://127.0.0.1:56840"
    assert env["NEXT_PUBLIC_SUPABASE_URL"] == f"http://127.0.0.1:{runner.EDGE_PORT}"


def test_an_app_that_never_answers_leaves_no_process(monkeypatch, tmp_path):
    """Oracle (1-E3A-PREP-R3): `next start` exits or never answers, the runner raises, and its
    process group keeps running (holding the App port into the next run's preflight)."""
    import os
    import signal
    monkeypatch.setattr(runner.NextApp, "URL", "http://127.0.0.1:9/login")   # nothing answers
    monkeypatch.setattr(runner.NextApp, "READY_S", 1.0)
    for script in ("sleep 60 & exec sleep 60", "sleep 60 & exit 3"):
        monkeypatch.setattr(runner.NextApp, "ARGV", ["sh", "-c", script])
        app = runner.NextApp({"PATH": os.environ["PATH"]}, tmp_path)
        with pytest.raises(RuntimeError):
            app.__enter__()
        try:
            os.killpg(app.process.pid, 0)
            os.killpg(app.process.pid, signal.SIGKILL)
            alive = True
        except ProcessLookupError:
            alive = False
        assert not alive, f"{script!r}: its process group outlived the failed start"


def test_teardown_counts_a_held_port_as_left_behind(monkeypatch):
    """Oracle (1-E3A-PREP-R3): teardown PASS while a process still holds one of the runner's
    ports (it only looked at docker names)."""
    import socket
    from types import SimpleNamespace
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout="infrx-e4b-postgres\ninfrx-e3c-postgres\n"))
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = held.getsockname()[1]
        assert runner.leftovers((port,)) == ["infrx-e4b-postgres", f"127.0.0.1:{port}"]
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=""))
    assert runner.leftovers((port,)) == []


def test_merged_lanes_follow_the_manifest_status():
    """Oracle: a planned lane treated as merged (its checks would FAIL instead of NOT RUN)."""
    tasks = {"tasks": [{"id": "C0", "status": "implemented"}, {"id": "C3A", "status": "planned"},
                       {"id": "U4", "status": "integrated"}]}
    assert runner.merged_lanes(tasks) == {"C0", "U4"}


def test_edge_tokens_are_signed_expiring_and_postgrest_shaped():
    """Oracle: a forged or expired token accepted by the auth stand-in."""
    token = edge.sign({"sub": "u", "role": "authenticated", "exp": int(time.time()) + 60}, SECRET)
    assert edge.verify(token, SECRET)["sub"] == "u"
    assert edge.verify(token, "another-secret") is None
    head, body, mac = token.split(".")
    assert edge.verify(f"{head}.{body}.{mac[::-1]}", SECRET) is None
    old = edge.sign({"sub": "u", "exp": int(time.time()) - 1}, SECRET)
    assert edge.verify(old, SECRET) is None


def test_edge_email_link_is_one_use_and_signin_needs_verification():
    """Oracle: a replayed verification link that signs in again (a second claim path), or an
    unverified user signing in."""
    stand_in = edge.Edge("", "http://127.0.0.1:1", SECRET, "http://127.0.0.1:56870")
    stand_in._db = lambda *args: None                     # no database in a unit test
    status, user = stand_in.signup({"email": "A@x.invalid", "password": "long-enough"},
                                   "http://127.0.0.1:56870/auth/callback?next=/welcome")
    assert status == 200 and user["email_confirmed_at"] is None
    assert stand_in.token("password", {"email": "a@x.invalid", "password": "long-enough"})[1][
        "code"] == "email_not_confirmed"
    link, = stand_in.mail["a@x.invalid"]
    assert link.startswith("http://127.0.0.1:56870/auth/callback?next=/welcome&token_hash=")
    token = link.split("token_hash=")[1].split("&")[0]
    status, session = stand_in.verify_token({"token_hash": token, "type": "signup"})
    assert status == 200 and edge.verify(session["access_token"], SECRET)["role"] == \
        "authenticated"
    assert stand_in.verify_token({"token_hash": token})[1]["code"] == "otp_expired"
    stand_in.recover({"email": "a@x.invalid"}, "http://127.0.0.1:56870/auth/callback?next=/x")
    recovery = stand_in.mail["a@x.invalid"][-1].split("token_hash=")[1].split("&")[0]
    assert stand_in.verify_token({"token_hash": recovery, "type": "signup"})[0] == 403
    assert stand_in.recover({"email": "nobody@x.invalid"}, None) == (200, {})
    assert "nobody@x.invalid" not in stand_in.mail
    assert stand_in.token("password", {"email": "a@x.invalid", "password": "wrong-one!"})[1][
        "code"] == "invalid_credentials"
    status, again = stand_in.token("refresh_token", {"refresh_token": session["refresh_token"]})
    assert status == 200 and stand_in.stats["refresh_grants"] == 1
    assert stand_in.get_user("Bearer " + again["access_token"])[0] == 200
    stand_in.logout("Bearer " + again["access_token"])
    assert stand_in.get_user("Bearer " + again["access_token"])[1]["code"] == "session_not_found"


def test_a_frozen_port_freezes_reads_only_and_per_user():
    """Oracle: the fixture-port seam freezing a write (the grant) - then it would not be a
    mocked READ port - or sharing one user's answers with another."""
    frozen = edge.Edge("", "", SECRET, "http://127.0.0.1:56870", freeze=True)
    live = edge.Edge("", "", SECRET, "http://127.0.0.1:56870")
    bearer = "Bearer " + edge.sign({"sub": "u1", "exp": int(time.time()) + 60}, SECRET)
    other = "Bearer " + edge.sign({"sub": "u2", "exp": int(time.time()) + 60}, SECRET)
    assert live.frozen_key("GET", "api_keys", "", b"", bearer) is None
    assert frozen.frozen_key("POST", "rpc/claim_signup_grant", "", b"{}", bearer) is None
    assert frozen.frozen_key("PATCH", "api_keys", "", b"{}", bearer) is None
    read = frozen.frozen_key("POST", "rpc/consumer_jobs", "", b"{}", bearer)
    assert read is not None and read != frozen.frozen_key("POST", "rpc/consumer_jobs", "",
                                                          b"{}", other)
    assert frozen.frozen_key("GET", "console_credit_wallets", "select=*", b"", bearer)


def test_the_app_e2e_gate_runs_this_runner_and_takes_its_verdict(tmp_path, monkeypatch):
    """Oracle: the app-e2e gate still reporting the browser journey NOT RUN (or PASS) without
    running E3A's runner, or passing a runner whose verdict and exit disagree."""
    import sys
    sys.path.insert(0, str(HERE.parent))
    import gates
    assert gates.APP_RUNNER == HERE / "runner.py"
    fake = tmp_path / "runner.py"
    fake.write_text("import json, sys, pathlib\nout = pathlib.Path(sys.argv[sys.argv.index('--out')"
                    " + 1]); out.mkdir(parents=True, exist_ok=True)\n(out / 'verdict.json')"
                    ".write_text(json.dumps({'verdict': 'NOT RUN'})); sys.exit(3)\n")
    stage = gates.e3c_stage(tmp_path, fake, "browser-journey", "e3a")
    assert (stage["stage"], stage["verdict"]) == ("browser-journey", "NOT RUN")
    assert stage["command"].endswith(f"--out {tmp_path / 'e3a'}")
    seen = []
    monkeypatch.setattr(gates, "preflight_stage", lambda *a, **k: {"stage": "p", "verdict": "PASS"})
    monkeypatch.setattr(gates, "run", lambda name, argv, cwd, out, env=None: (
        0, 0.1, (tmp_path / "log").write_text("ok") and tmp_path / "log"))
    monkeypatch.setattr(gates, "e3c_stage", lambda out, runner=None, name="", sub="": seen.append(
        (runner, name, sub)) or {"stage": name, "verdict": "NOT RUN"})
    # WR-E3A-1: the gate refuses at console-install when apps/app/node_modules is absent
    # (gates.py app_e2e); the oracle must not depend on the working tree, so point REPO at a
    # tree that has the directory.
    (tmp_path / "apps" / "app" / "node_modules").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(gates, "REPO", tmp_path)
    stages = gates.app_e2e(None, tmp_path)
    assert seen == [(gates.APP_RUNNER, "browser-journey", "e3a")] and \
        stages[-1]["stage"] == "browser-journey"
