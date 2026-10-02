"""AP-11 layer 1: the lifecycle runner's own decisions against the contract fake (conftest).
Each case names the false report a broken runner would make. The real-service proof is the
isolated run on ap11 (`runner.py --mode isolated --world ap11`), never these cases."""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

import pytest

from api_lifecycle import runner, stages
from api_lifecycle import state as st

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
MODEL = "nemostation/marlin-2b"
SECRET_VALUES = ("sk-infrx-alphaSECRET0001", "sk-infrx-betaSECRET00002",
                 "eyJhbGci.eyJzdWIi.adminSIG", "eyJhbGci.eyJzdWIi.outsideSIG")


def run(files, gateway, *extra: str, mode: str = "isolated"):
    config, secrets, state_file, out = files
    code = runner.main(["--mode", mode, "--config", str(config), "--secrets", str(secrets),
                        "--state", str(state_file), "--out", str(out), *extra],
                       transport=gateway.transport)
    return code, json.loads((out / "verdict.json").read_text())


def stage(verdict: dict, sid: str) -> dict:
    found = [entry for entry in verdict["stages"] if entry["id"] == sid]
    assert found, f"no stage {sid} in the verdict: {verdict['reasons']}"
    return found[0]


def edit(path: Path, **changes) -> None:
    payload = {**json.loads(path.read_text()), **changes}
    path.write_text(json.dumps({k: v for k, v in payload.items() if v is not None}))


def checks(entry: dict) -> dict[str, bool]:
    return {row["name"]: row["ok"] for row in entry["evidence"]["assertions"]}


# ------------------------------------------------------------------ 11a: state and resume


def test_ap11_state_and_secrets_are_separate_private_files(files, gateway):
    """Broken: a state file another user can read, or a credential written into it."""
    run(files, gateway)
    state_file = files[2]
    assert stat.S_IMODE(state_file.stat().st_mode) == 0o600
    text = state_file.read_text()
    assert not [value for value in SECRET_VALUES if value in text]
    recorded = json.loads(text)["mutations"]["09.job"]
    assert recorded["status"] == "done" and recorded["request_hash"].startswith("sha256:")
    assert recorded["key"] and recorded["key"] not in SECRET_VALUES


def test_ap11_a_readable_state_or_secrets_file_is_invalid(files, gateway):
    """Broken: a group-readable credential file accepted, or a run on a leaked state."""
    os.chmod(files[1], 0o644)
    code, verdict = run(files, gateway)
    assert (code, verdict["verdict"]) == (4, "INVALID") and gateway.sent == []
    os.chmod(files[1], 0o600)
    run(files, gateway)
    os.chmod(files[2], 0o640)
    sent = len(gateway.sent)
    code, verdict = run(files, gateway)
    assert (code, verdict["verdict"]) == (4, "INVALID") and len(gateway.sent) == sent


def test_ap11_a_state_from_another_target_is_invalid(files, gateway):
    """Broken: resuming one target's operation ids against another target."""
    run(files, gateway)
    edit(files[0], target="another-target")
    sent = len(gateway.sent)
    code, verdict = run(files, gateway)
    assert (code, verdict["verdict"]) == (4, "INVALID") and len(gateway.sent) == sent
    assert "another" in " ".join(verdict["reasons"])


def test_ap11_a_lost_acknowledgement_is_not_run_and_keeps_the_pending_key(files, gateway):
    """Broken: an unknown outcome reported as FAIL/PASS, or the key forgotten before resume."""
    gateway.lose_ack.add("POST /v1/jobs")
    code, verdict = run(files, gateway)
    assert code == 3 and stage(verdict, "09")["status"] == "NOT RUN"
    assert "resume" in " ".join(stage(verdict, "09")["reasons"])
    pending = json.loads(files[2].read_text())["mutations"]["09.job"]
    assert pending["status"] == "pending" and pending["key"] == gateway.posts("/v1/jobs")[0]
    assert stage(verdict, "11")["status"] == "NOT RUN"            # nothing after the unknown


def test_ap11_restart_retries_the_original_key_and_counts_no_new_request(files, gateway):
    """Broken: a timed-out job resubmitted under a fresh key (a second inference)."""
    gateway.lose_ack.add("POST /v1/jobs")
    run(files, gateway)
    code, verdict = run(files, gateway)
    keys = gateway.posts("/v1/jobs")
    assert keys[0] == keys[1], "the retry must carry the original Idempotency-Key"
    assert stage(verdict, "09")["status"] == "PASS"
    assert gateway.created == 3                     # 09's job, 11's sync and SSE: once each
    inference = len(keys) + len(gateway.posts("/v1/chat/completions"))
    assert json.loads(files[2].read_text())["counters"]["inference"] == inference - 1


def test_ap11_restart_reconciles_a_recorded_mutation_by_get_and_never_resends_it(files, gateway):
    """Broken: a recorded upload or job re-created because a later step of its stage failed."""
    original = gateway.route

    def flaky(request, method, path):         # the replay probe times out once
        if method == "POST" and path == "/v1/jobs" and len(gateway.posts("/v1/jobs")) == 2:
            gateway.lose_ack.add("POST /v1/jobs")
        return original(request, method, path)
    gateway.route = flaky
    code, _ = run(files, gateway)
    assert code == 3
    before = len(gateway.sent)
    run(files, gateway)
    resumed = [(m, p) for m, p, _ in gateway.sent[before:]
               if p.startswith("/v1/jobs") and "ap11probe" not in p]       # 01's probe aside
    handle = json.loads(files[2].read_text())["mutations"]["09.job"]["outputs"]["job_handle"]
    assert len(gateway.posts("/v1/uploads")) == 1
    assert resumed[0] == ("GET", f"/v1/jobs/{handle}"), resumed


def test_ap11_a_finished_stage_is_not_repeated_on_restart(files, gateway):
    """Broken: a rerun re-submitting inference for stages the state says already passed."""
    run(files, gateway)
    posts = [s for s in gateway.sent if s[0] == "POST"]
    code, verdict = run(files, gateway)
    assert [s for s in gateway.sent if s[0] == "POST"] == posts
    assert stage(verdict, "09")["resumed"] is True and stage(verdict, "09")["status"] == "PASS"


def test_ap11_a_recorded_mutation_resent_with_another_body_is_invalid(files, gateway):
    """Broken: an Idempotency-Key reused for a different request (a silent 409 or a new job)."""
    gateway.lose_ack.add("POST /v1/jobs")
    run(files, gateway)
    edit(files[0], model="nemostation/another-model")
    sent = len(gateway.sent)
    code, verdict = run(files, gateway)
    assert (code, verdict["verdict"]) == (4, "INVALID")
    assert not [s for s in gateway.sent[sent:] if s[0] == "POST" and s[1] == "/v1/jobs"]


def test_ap11_the_request_budget_bounds_inference_across_restarts(files, gateway):
    """Broken: a budget kept in memory, so every restart grants a fresh allowance."""
    edit(files[0], max_requests=2)
    run(files, gateway)
    code, verdict = run(files, gateway)
    sent = len(gateway.posts("/v1/jobs")) + len(gateway.posts("/v1/chat/completions"))
    assert sent == 2 and code == 3
    assert "budget" in " ".join(stage(verdict, "09")["reasons"])


LIVE = {"target": "approved-candidate", "max_requests": 6,
        "budget": {"amount": "5.00", "unit": "PROVIDER_USD"}}


@pytest.mark.parametrize("change", [
    {"origins": None}, {"identities": None}, {"target": None}, {"budget": None},
    {"max_requests": None}, {"max_requests": 7}, {"budget": {"amount": "5", "unit": "EUR"}},
    {"fixtures": {"consumer_a_key": "seeded"}}],
    ids=["origins", "identities", "target", "budget", "max", "max7", "unit", "fixtures"])
def test_ap11_live_is_refused_without_its_explicit_config(files, gateway, change):
    """Broken: a live run against real origins on defaults, an unbounded budget or seeds."""
    edit(files[0], **{**LIVE, "fixtures": {}, **change})
    code, verdict = run(files, gateway, mode="live")
    assert (code, verdict["verdict"]) == (4, "INVALID") and gateway.sent == []


def test_ap11_live_never_uses_a_seeded_credential(files, gateway):
    """Broken: a live pass on a key seeded behind the runner's back (verification.md 2)."""
    edit(files[0], **LIVE, fixtures={})
    code, verdict = run(files, gateway, mode="live")
    assert code == 3 and stage(verdict, "09")["status"] == "BLOCKED"
    assert "AP-03" in " ".join(stage(verdict, "09")["reasons"])
    assert not gateway.posts("/v1/jobs")


def test_ap11_inspect_mode_sends_only_reads(files, gateway):
    """Broken: an inspect run that creates an upload, a job or an inference."""
    code, verdict = run(files, gateway, mode="inspect")
    assert {method for method, _, _ in gateway.sent} == {"GET"}
    assert stage(verdict, "09")["selected"] is False and stage(verdict, "09")["status"] == "NOT RUN"
    assert code == 3 and verdict["complete_lifecycle"] is False
    owned = st.State.open(files[2], target="fake-gateway")      # a stage misdeclared reads-only
    session = runner.Session("inspect", {"origins": {"gateway": "http://g.test"}},
                             st.Secrets({}), owned, gateway.transport)
    with pytest.raises(st.InvalidRun):
        runner.Context(session, "01", runner.new_evidence()).call("POST", "/v1/uploads")
    assert {method for method, _, _ in gateway.sent} == {"GET"}


def test_ap11_cleanup_touches_only_state_owned_resources(files, gateway):
    """Broken: a cleanup that lists and deletes what it finds instead of what it created."""
    config, _, state_file, _ = files
    state_file.parent.mkdir(parents=True)
    owned = st.State.open(state_file, target="fake-gateway")
    owned.own("job", "job_ours", {"method": "DELETE", "origin": "gateway",
                                  "route": "/v1/jobs/{id}", "actor": "consumer_a"})
    owned.own("upload", "up_ours", None)
    code, verdict = run(files, gateway, mode="cleanup")
    assert gateway.sent == [("DELETE", "/v1/jobs/job_ours", None)] and code == 0
    rows = {row["id"]: row["outcome"] for row in verdict["cleanup"]}
    assert rows == {"job_ours": "gone", "up_ours": "nothing to clean (expires by retention)"}


def test_ap11_exit_codes_and_the_gate_follow_environment_md(files, gateway):
    """Broken: NOT RUN/BLOCKED exiting 0, or a FAIL hidden behind a BLOCKED stage."""
    assert runner.EXIT == {"PASS": 0, "FAIL": 1, "BLOCKED": 3, "NOT RUN": 3, "INVALID": 4}
    assert runner.worst(["PASS", "NOT RUN", "BLOCKED"]) == "BLOCKED"
    assert runner.worst(["BLOCKED", "FAIL", "INVALID"]) == "FAIL"
    assert runner.worst([]) == "NOT RUN"
    gateway.models = ["nemostation/other"]
    code, verdict = run(files, gateway)
    assert (code, verdict["verdict"], stage(verdict, "08")["status"]) == (1, "FAIL", "FAIL")


def test_ap11_no_secret_reaches_the_verdict_the_output_or_the_state(files, gateway, capsys):
    """Broken: a key, session, DSN password or Authorization header in evidence or logs -
    including a credential a server echoes back into an assertion's detail."""
    gateway.models = [MODEL, SECRET_VALUES[0]]
    run(files, gateway)
    seen = capsys.readouterr().out + files[3].joinpath("verdict.json").read_text() \
        + files[2].read_text()
    assert not [value for value in SECRET_VALUES if value in seen]
    assert "Bearer" not in seen and "authorization" not in seen.lower()
    secrets = st.Secrets({"admin": "s3cr3t-session-value"})
    redacted = json.dumps(secrets.redact({
        "note": "used s3cr3t-session-value", "dsn": "postgresql://u:pw@db:5432/x",
        "header": "Bearer abc.def-ghi", "key": "sk-infrx-0123456789abcdef",
        "jwt": "eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl", "nested": [{"password": "hunter2"}]}))
    for leaked in ("s3cr3t-session-value", ":pw@", "abc.def-ghi", "sk-infrx-0123456789",
                   "c2lnbmF0dXJl", "hunter2"):
        assert leaked not in redacted, leaked


# ------------------------------------------------------------------ 11b: the stage contracts


def test_ap11_the_stages_are_verifications_eighteen_steps():
    """Broken: a step dropped, renumbered or reworded away from verification.md's table."""
    text = (REPO / "research/plan/api-lifecycle/verification.md").read_text()
    rows = re.findall(r"^\| (\d\d) \| (.+?) \| (.+?) \|$", text, re.M)
    assert [(s.sid, s.title, s.proves) for s in stages.STAGES] == rows
    assert len(rows) == 18


def test_ap11_every_target_route_is_a_contracts_md_path():
    """Broken: a stage waiting on a route nobody is asked to build."""
    text = (REPO / "research/plan/api-lifecycle/contracts.md").read_text()
    for item in stages.STAGES:
        for route in item.routes:
            if route.owner is None:
                continue
            assert re.fullmatch(r"AP-\d\d", route.owner), route
            suffixes = (route.path, route.path.removeprefix("/lab/v1"),
                        route.path.removeprefix("/lab/v1/judge"),     # §8's relative rows
                        "..." + route.path[route.path.find("/{"):]    # §5's `.../{id}/x`
                        if "/{" in route.path else route.path)
            assert any(f"`{s}`" in text or f"`{s}/" in text or f"{s}`" in text
                       for s in suffixes), f"{item.sid}: {route.path} is not in contracts.md"


def test_ap11_a_stage_without_its_api_is_blocked_naming_the_prerequisite(files, gateway):
    """Broken: an absent API reported as a green skip, or a stage calling an unmounted route."""
    code, verdict = run(files, gateway)
    blocked = stage(verdict, "02")
    assert blocked["status"] == "BLOCKED" and "AP-04" in " ".join(blocked["reasons"])
    assert "POST /lab/v1/control/model-projects" in " ".join(blocked["reasons"])
    assert not [p for _, p, _ in gateway.sent if p.startswith(("/lab/v1/artifacts",
                                                               "/lab/v1/control/model-projects"))]
    for item in stages.STAGES:
        if item.run is None:
            entry = stage(verdict, item.sid)
            assert entry["status"] == "BLOCKED" and entry["reasons"], item.sid
    assert code == 3 and verdict["complete_lifecycle"] is False


def test_ap11_a_partly_mounted_stage_runs_its_existing_routes_and_stays_blocked(files, gateway):
    """Broken: a stage with a missing API passing on the part that exists."""
    _, verdict = run(files, gateway)
    models = stage(verdict, "08")
    assert models["status"] == "BLOCKED" and "AP-03" in " ".join(models["reasons"])
    assert checks(models) == {"the catalog lists the model under test": True}
    assert [x["route"] for x in models["evidence"]["exchanges"]] == ["/v1/models"]


def test_ap11_a_dependent_stage_is_blocked_while_its_predecessor_has_not_passed(files, gateway):
    """Broken: stage 10 polling a job stage 09 never created (or a stale handle)."""
    code, verdict = run(files, gateway, "--only", "10")
    assert stage(verdict, "10")["status"] == "BLOCKED"
    assert "needs stage 09" in " ".join(stage(verdict, "10")["reasons"])
    assert stage(verdict, "09")["selected"] is False and gateway.sent == []
    earlier = st.State.open(files[2], target="fake-gateway")    # 09 ran but did not pass
    earlier.checkpoint("09", "BLOCKED", {"job_handle": "job_stale"})
    code, verdict = run(files, gateway, "--only", "10")
    assert stage(verdict, "10")["status"] == "BLOCKED" and gateway.sent == []


def test_ap11_every_stage_records_utc_times_routes_statuses_ids_and_counters(files, gateway):
    """Broken: evidence without timing, the route template, the status or a correlation id."""
    _, verdict = run(files, gateway)
    for sid in ("01", "08", "09", "10", "11"):
        evidence = stage(verdict, sid)["evidence"]
        assert evidence["started"].endswith("Z") and evidence["ended"] >= evidence["started"]
        assert evidence["assertions"] and evidence["counters"]["requests"] >= 1
        for exchange in evidence["exchanges"]:
            assert exchange["route"].startswith("/") and isinstance(exchange["status"], int)
            assert exchange["request_id"] and exchange["ms"] >= 0
    routes = {x["route"] for x in stage(verdict, "10")["evidence"]["exchanges"]}
    assert "/v1/jobs/{handle}" in routes                 # a template, never the handle itself
    assert verdict["pins"]["head"] and verdict["fixtures"]


def test_ap11_the_consumer_stages_prove_their_rows_on_the_mounted_routes(files, gateway):
    """Broken: a replay that created a second job, a foreign read allowed, SSE without its end
    frame, or a sync call silently turned async - each must FAIL the stage, not pass it."""
    _, verdict = run(files, gateway)
    assert all(checks(stage(verdict, "09")).values()) and stage(verdict, "09")["status"] == "PASS"
    assert {"a replay returns the same job", "a changed body under the key is 409"} \
        <= set(checks(stage(verdict, "09")))
    ten = checks(stage(verdict, "10"))
    assert ten == {"the job succeeded": True,
                   "the result names the model under test": True, "the result reports usage": True,
                   "consumer B cannot read A's job": True}
    eleven = checks(stage(verdict, "11"))
    assert eleven == {"sync answers 200 in-line, never 202": True,
                      "sync names the model and reports usage": True,
                      "SSE is an event stream": True, "SSE ends with [DONE]": True,
                      "SSE carries content for the model": True}
    one = checks(stage(verdict, "01"))
    assert one == {"a valid consumer key is authenticated": True,
                   "a missing key is refused 401": True,
                   "a member session reads its workspace": True,
                   "a non-member session is refused": True,
                   "a forged session is refused 401": True}
    assert {stage(verdict, s)["status"] for s in ("01", "10", "11")} == {"BLOCKED"}


FAILS = {"replay_new_job": "09", "conflict_accepted": "09", "artifact_upload": "09",
         "foreign_read": "10", "result_model": "10", "no_usage": "10", "sse_no_done": "11",
         "sse_plain": "11", "sse_wrong_model": "11", "sync_async": "11", "sync_no_usage": "11",
         "no_member": "01", "outsider_member": "01", "forged_accepted": "01",
         "anon_allowed": "01", "key_unauthenticated": "01"}


@pytest.mark.parametrize("defect", sorted(FAILS))
def test_ap11_a_product_defect_fails_its_stage(files, gateway, defect):
    """Broken: an assertion that cannot see the defect its row exists to catch - the stage
    stays BLOCKED/PASS on a second job, a foreign read, a lost end frame or a 202 sync."""
    gateway.defects.add(defect)
    code, verdict = run(files, gateway)
    assert stage(verdict, FAILS[defect])["status"] == "FAIL" and code == 1, \
        stage(verdict, FAILS[defect])["evidence"]["assertions"]


def test_ap11_a_stage_that_asserted_nothing_is_never_a_pass():
    """Broken: an empty stage body (or one whose checks were deleted) counted as PASS."""
    assert runner.status_of([], [], []) == "NOT RUN"
    assert runner.status_of([True], [], []) == "PASS"
    assert runner.status_of([True, False], [], []) == "FAIL"
    assert runner.status_of([True], ["needs stage 09"], []) == "BLOCKED"
    assert runner.status_of([True], [], ["AP-02"]) == "BLOCKED"
    assert runner.status_of([False], [], ["AP-02"]) == "FAIL"


def test_ap11_an_operation_is_accepted_only_as_r270_states_it():
    """Broken: a 200, a missing Location or an unknown state taken for a durable operation."""
    import httpx
    doc = {"operation_id": "op-1", "kind": "deployment.create", "state": "queued",
           "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-01T00:00:00Z"}
    good = httpx.Response(202, json=doc, headers={"Location": "/lab/v1/operations/op-1"})
    assert stages.accepted_operation(good).operation_id == "op-1"
    for bad in (httpx.Response(200, json=doc, headers={"Location": "/x"}),
                httpx.Response(202, json=doc),
                httpx.Response(202, json={**doc, "state": "done"}, headers={"Location": "/x"})):
        with pytest.raises(stages.ContractBroken):
            stages.accepted_operation(bad)
