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
    checks = runner.classify(all_passed())
    assert {c["verdict"] for c in runner.cells(checks)} == {runner.PASS}
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
    stages = gates.app_e2e(None, tmp_path)
    assert seen == [(gates.APP_RUNNER, "browser-journey", "e3a")] and \
        stages[-1]["stage"] == "browser-journey"
