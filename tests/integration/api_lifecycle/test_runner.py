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
                 "eyJhbGci.eyJzdWIi.adminSIG", "eyJhbGci.eyJzdWIi.outsideSIG",
                 "eyJhbGci.eyJzdWIi.alphaWEB", "eyJhbGci.eyJzdWIi.betaWEB")
SERVED = ("01", "02", "03", "08", "09", "10", "11", "12", "13", "14", "15", "16", "17")


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
    assert len(keys) >= 2 and keys[0] == keys[1], "the retry must carry the original key"
    assert stage(verdict, "09")["status"] == "PASS"
    assert gateway.created == 4             # 09's job, 11's sync and SSE, 12's capture: once
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
    edit(files[0], **LIVE, fixtures={}, composed=["AP-01"])
    code, verdict = run(files, gateway, mode="live")
    assert code == 3 and stage(verdict, "09")["status"] == "BLOCKED"
    assert "AP-03" in " ".join(stage(verdict, "09")["reasons"])
    assert not gateway.posts("/v1/jobs")


def test_ap11_inspect_mode_sends_only_reads(files, gateway):
    """Broken: an inspect run that creates an upload, a job or an inference."""
    code, verdict = run(files, gateway, mode="inspect")
    assert {method for method, _, _ in gateway.sent} == {"GET"}
    assert stage(verdict, "09")["selected"] is False and stage(verdict, "09")["status"] == "NOT RUN"
    assert code == 0 and verdict["complete_lifecycle"] is False   # 01 is the whole selection
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
    owned.data["minted"] = {"consumer_a": "consumer_a_key"}     # minted by an earlier run
    st.write_private(state_file.with_name(state_file.name + ".keys"),
                     {"consumer_a_key": "sk-infrx-alphaSECRET0001"})
    owned.own("job", "job_ours", {"method": "DELETE", "origin": "gateway",
                                  "route": "/v1/jobs/{id}", "actor": "consumer_a"})
    owned.own("upload", "up_ours", None)
    owned.own("key", "key_unreachable", {"method": "DELETE", "route": "/console/v1/keys/{id}",
                                         "actor": "consumer_b"})        # its key was never minted
    code, verdict = run(files, gateway, mode="cleanup")
    assert [s[:2] for s in gateway.sent] == [("DELETE", "/v1/jobs/job_ours")] and code == 1
    rows = {row["id"]: row["outcome"] for row in verdict["cleanup"]}
    assert rows.pop("key_unreachable").startswith("failed: BLOCKED[AP-03]")
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
    edit(files[0], composed=[c for c in json.loads(files[0].read_text())["composed"]
                             if c != "AP-04"])
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
    edit(files[0], composed=["AP-01", "AP-02"])
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
    earlier.data["minted"] = {"consumer_a": "consumer_a_key", "consumer_b": "consumer_b_key"}
    st.write_private(files[2].with_name(files[2].name + ".keys"),     # its keys resolve
                     {"consumer_a_key": SECRET_VALUES[0], "consumer_b_key": SECRET_VALUES[1]})
    earlier.checkpoint("09", "BLOCKED", {"job_handle": "job_stale"})
    code, verdict = run(files, gateway, "--only", "10")
    assert stage(verdict, "10")["status"] == "BLOCKED" and gateway.sent == []


def test_ap11_every_stage_records_utc_times_routes_statuses_ids_and_counters(files, gateway):
    """Broken: evidence without timing, the route template, the status or a correlation id."""
    _, verdict = run(files, gateway)
    for sid in SERVED:
        evidence = stage(verdict, sid)["evidence"]
        assert evidence["started"].endswith("Z") and evidence["ended"] >= evidence["started"]
        assert evidence["assertions"] and evidence["counters"]["requests"] >= 1
        for exchange in evidence["exchanges"]:
            assert exchange["route"].startswith("/") or exchange["route"] == "{part}"
            assert isinstance(exchange["status"], int)
            assert exchange["request_id"] and exchange["ms"] >= 0
    refused = [x for x in stage(verdict, "01")["evidence"]["exchanges"] if x["status"] == 401]
    assert refused and all(x["error"] in ("invalid_api_key", "unauthenticated") for x in refused)
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
    assert {"the job succeeded", "the result names the model under test",
            "the result reports usage", "consumer B cannot read A's job",
            "the ledger holds exactly that one debit for the request"} <= set(ten)
    eleven = checks(stage(verdict, "11"))
    assert {"sync answers 200 in-line, never 202", "sync names the model and reports usage",
            "SSE is an event stream", "SSE ends with [DONE]",
            "SSE carries content for the model", "one CREDIT settlement, the reserve released"} \
        <= set(eleven)
    one = checks(stage(verdict, "01"))
    assert {"a valid consumer key is authenticated", "a consumer key is never a web session (401)"} \
        <= set(checks(stage(verdict, "08")))
    assert {"a missing key is refused 401",
            "a member session reads its workspace", "a non-member session is refused",
            "a forged session is refused 401", "a key is never a web session (401)",
            "outsider holds no workspace", "consumer_a_web holds no workspace"} <= set(one)
    assert all(ten.values()) and all(eleven.values()) and all(one.values())
    assert {stage(verdict, s)["status"] for s in ("01", "10", "11")} == {"PASS"}


FAILS = {"replay_new_job": "09", "conflict_accepted": "09", "artifact_upload": "09",
         "foreign_read": "10", "result_model": "10", "no_usage": "10", "sse_no_done": "11",
         "sse_plain": "11", "sse_wrong_model": "11", "sync_async": "11", "sync_no_usage": "11",
         "no_member": "01", "outsider_member": "01", "forged_accepted": "01",
         "anon_allowed": "01", "key_unauthenticated": "08",
         # 11c
         "key_is_session": "01", "consumer_operator": "01", "outsider_workspace": "01",
         "outsider_members": "01", "grant_twice": "08", "key_twice": "08",
         "secret_rerevealed": "08", "replay_other_key": "08", "grant_not_on_account": "08",
         "hold_left": "10", "double_debit": "10",
         "console_foreign": "10", "op_fails": "02", "op_replay_new": "02",
         "artifact_missing_file": "03", "revision_mutable": "03", "revision_unpinned": "03",
         "capture_all_keys": "12", "grant_not_persisted": "12", "uncaptured_traced": "13",
         "trace_no_content": "13", "trace_wrong_pins": "13", "zero_elapsed": "13",
         "dry_run_sends": "14", "dry_run_scored": "14", "run_replay_new": "14",
         "judge_charges_consumer": "15", "budget_spent": "15", "review_twice": "16",
         "review_not_human": "16", "calibrated_on_one": "16", "content_after_revoke": "17",
         "judge_after_revoke": "17", "no_inference_id": "11"}


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


# ------------------------------------------------------------------ 11c: the served stages


def test_ap11_every_served_stage_passes_and_only_ap05_ap06_stay_blocked(files, gateway):
    """Broken: a stage whose packages are composed left BLOCKED, or one that waits on AP-05/06
    reported anything but BLOCKED naming them."""
    code, verdict = run(files, gateway)
    assert {sid: stage(verdict, sid)["status"] for sid in SERVED} == dict.fromkeys(SERVED, "PASS"), \
        {sid: stage(verdict, sid)["reasons"] for sid in SERVED}
    for sid in ("04", "05", "06", "07", "18"):         # 06's own route is mounted: it waits
        reasons = " ".join(stage(verdict, sid)["reasons"])   # on 04/05, which name AP-05/06
        assert stage(verdict, sid)["status"] == "BLOCKED" and ("AP-05" in reasons
                                                               or "AP-06" in reasons
                                                               or "needs stage 04" in reasons), sid
    assert (code, verdict["verdict"]) == (3, "BLOCKED")


def test_ap11_a_dry_run_judge_is_labelled_and_never_a_judge_result(files, gateway):
    """Broken: a dry run passed off as a judge result (no label), or a scored result kept."""
    _, verdict = run(files, gateway)
    for sid in ("14", "15"):
        assert (stage(verdict, sid)["label"] or "").startswith("dry-run"), sid
    assert not [s for s in verdict["stages"] if s["label"] and s["id"] not in ("14", "15")]
    assert "dry run: no result is ever scored" in checks(stage(verdict, "14"))
    edit(files[0], traces=False)                     # 14 cannot run: no label on a BLOCKED
    _, verdict = run(files, gateway, "--state", str(files[2].with_name("other.json")))
    assert stage(verdict, "14")["status"] == "BLOCKED" and stage(verdict, "14")["label"] is None


def test_ap11_without_a_judge_the_judge_stages_are_blocked_on_p10(files, gateway):
    """Broken: a judge stage run (or passed) without P-10 or a declared dry run."""
    edit(files[0], judge=None)
    _, verdict = run(files, gateway)
    assert stage(verdict, "14")["status"] == "BLOCKED"
    assert "P-10" in " ".join(stage(verdict, "14")["reasons"])
    assert not [p for _, p, _ in gateway.sent if p.startswith("/lab/v1/judge")]
    assert stage(verdict, "15")["status"] == "BLOCKED" and stage(verdict, "14")["label"] is None


def test_ap11_without_trace_storage_the_capture_half_is_blocked(files, gateway):
    """Broken: a captured request sent (or 13 passed) where no trace storage is composed."""
    edit(files[0], traces=False, traces_missing="WR: ap11 has no ClickHouse port")
    _, verdict = run(files, gateway)
    twelve = stage(verdict, "12")
    assert twelve["status"] == "BLOCKED" and "ClickHouse" in " ".join(twelve["reasons"])
    assert all(checks(twelve).values()) and "the grant is written" in checks(twelve)
    assert stage(verdict, "13")["status"] == "BLOCKED" and not gateway.traces


def test_ap11_a_package_the_target_does_not_compose_blocks_by_name(files, gateway):
    """Broken: a stage calling a family the target does not serve, or passing without it."""
    edit(files[0], composed=["AP-01", "AP-02", "AP-03", "AP-07"])
    _, verdict = run(files, gateway)
    for sid in ("02", "03", "14", "16"):
        assert stage(verdict, sid)["status"] == "BLOCKED", sid
    assert "BLOCKED[AP-08]" in " ".join(stage(verdict, "14")["reasons"])
    assert not [p for _, p, _ in gateway.sent
                if p.startswith(("/lab/v1/judge", "/lab/v1/artifacts",
                                 "/lab/v1/control/model-projects"))]


def test_ap11_an_unmounted_route_is_blocked_by_name_never_failed(files, gateway):
    """Broken: the framework's own 404/405 (nobody mounted the route) read as a product FAIL,
    or an R270 not-found envelope mistaken for an unmounted route."""
    import httpx
    original = gateway.route

    def bare(request, method, path):
        if path.endswith("/parts"):
            return httpx.Response(404, json={"detail": "Not Found"})
        return original(request, method, path)
    gateway.route = bare
    _, verdict = run(files, gateway)
    two = stage(verdict, "02")
    assert two["status"] == "BLOCKED" and "/parts: not mounted" in " ".join(two["reasons"])
    assert stages.unmounted(httpx.Response(405, json={"detail": "Method Not Allowed"}))
    assert not stages.unmounted(httpx.Response(404, json={"error": {"code": "not_found"}}))
    assert not stages.unmounted(httpx.Response(200, json={"detail": "x"}))


def test_ap11_keys_are_minted_through_the_api_and_kept_outside_the_state(files, gateway):
    """Broken: a minted secret written into the state or the verdict, a key file others can
    read, or a stage using a key nobody minted."""
    config = json.loads(files[0].read_text())     # a declared fallback the minted key replaces
    edit(files[0], fixtures={**config["fixtures"], "consumer_a_key": "fallback seed"})
    _, verdict = run(files, gateway)
    keys = files[2].with_name(files[2].name + ".keys")
    assert keys.exists() and stat.S_IMODE(keys.stat().st_mode) == 0o600
    minted = json.loads(keys.read_text())
    assert set(minted) == {"consumer_a_key", "consumer_b_key", "consumer_a_capture_key"}
    assert set(minted.values()) <= set(gateway.keys)
    seen = files[2].read_text() + files[3].joinpath("verdict.json").read_text()
    assert not [value for value in minted.values() if value in seen]
    state = json.loads(files[2].read_text())
    assert set(state["minted_ids"]) == {"consumer_a", "consumer_b", "consumer_a_capture"}
    assert not stage(verdict, "09")["evidence"]["fixtures"]
    os.chmod(keys, 0o644)
    code, verdict = run(files, gateway)
    assert (code, verdict["verdict"]) == (4, "INVALID")


def test_ap11_a_lost_key_acknowledgement_revokes_it_and_mints_once_more(files, gateway):
    """Broken: a key whose secret was lost silently reused (a key nobody can call with), or a
    fresh key minted while the lost one stays live."""
    gateway.lose_ack.add("POST /console/v1/keys")
    code, verdict = run(files, gateway)
    assert code == 3 and stage(verdict, "08")["status"] == "NOT RUN"
    code, verdict = run(files, gateway)
    assert stage(verdict, "08")["status"] == "PASS", stage(verdict, "08")["evidence"]
    alpha = [k for k in gateway.minted.values() if k["name"] == "ap11 consumer_a"]
    assert [k["revoked"] for k in alpha] == [True, False]
    kept = json.loads(files[2].with_name(files[2].name + ".keys").read_text())
    assert kept["consumer_a_key"] == alpha[1]["secret"]


def test_ap11_a_cas_write_resumes_with_the_version_it_first_read(files, gateway):
    """Broken: a resumed grant re-reading the (now advanced) version, so the original
    Idempotency-Key is refused as another request instead of replayed."""
    gateway.lose_ack.add("POST /console/v1/data-grants")
    code, verdict = run(files, gateway)
    assert code == 3 and stage(verdict, "12")["status"] == "NOT RUN"
    code, verdict = run(files, gateway)
    assert verdict["verdict"] != "INVALID" and stage(verdict, "12")["status"] == "PASS", \
        verdict["reasons"]
    assert json.loads(files[2].read_text())["pinned"]["12.grant_version"] == 0


HOSTED = ("04", "05", "06", "07")
HOSTING = {"readiness_from_record": "04", "consumer_key_private": "08", "dev_key_public": "06",
           "private_listed": "06", "stale_approval": "07"}


def hosted(files) -> None:
    edit(files[0], composed=json.loads(files[0].read_text())["composed"] + ["AP-05", "AP-06"])


def test_ap11_stages_04_to_07_hold_ap05_ap06_to_their_protocol(files, gateway):
    """Broken: a deployment, smoke, dev key, private call or approval that cannot pass once
    AP-05/06 are composed - or a stage without a runner reported BLOCKED without a reason."""
    hosted(files)
    code, verdict = run(files, gateway)
    assert {sid: stage(verdict, sid)["status"] for sid in HOSTED} == dict.fromkeys(HOSTED, "PASS"), \
        {sid: (stage(verdict, sid)["reasons"], checks(stage(verdict, sid))) for sid in HOSTED}
    assert json.loads(files[2].read_text())["minted_ids"]["provider_dev"]
    eighteen = stage(verdict, "18")
    assert eighteen["status"] == "BLOCKED"
    assert any("no runner implementation" in reason for reason in eighteen["reasons"])


@pytest.mark.parametrize("defect", sorted(HOSTING))
def test_ap11_a_hosting_defect_fails_its_stage(files, gateway, defect):
    """Broken: readiness read from a record, a consumer key on a private endpoint, a dev key
    spending publicly, a private model in discovery, or a stale approval accepted."""
    hosted(files)
    gateway.defects.add(defect)
    code, verdict = run(files, gateway)
    assert stage(verdict, HOSTING[defect])["status"] == "FAIL" and code == 1, \
        stage(verdict, HOSTING[defect])["evidence"]


def test_ap11_no_capacity_is_the_gpu_prerequisite_never_a_product_fail(files, gateway):
    """Broken: `capacity_unavailable` (no spare GPU) reported as a FAIL of the API under test."""
    hosted(files)
    gateway.defects.add("no_capacity")
    _, verdict = run(files, gateway)
    four = stage(verdict, "04")
    assert four["status"] == "BLOCKED" and "GPU-TARGET" in " ".join(four["reasons"])
