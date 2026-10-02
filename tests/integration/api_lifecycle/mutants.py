#!/usr/bin/env python3
"""R32/R83 for AP-11: one single-edit defect per decision the lifecycle runner claims, through
the shared runner (`apps/infrx-api/tests/contracts/mutants.py`), each killed by the named
layer-1 cases of test_runner.py in a throwaway copy (tests/integration + infrx + the two
api-lifecycle documents the contract cases read).

    apps/infrx-api/.venv/bin/python tests/integration/api_lifecycle/mutants.py [names]
    INFRX_MUTANTS=all apps/infrx-api/.venv/bin/python -m pytest -q -p no:cacheprovider \\
        tests/integration/api_lifecycle/test_mutants.py

world.py (the ap11 stack) carries no layer-1 case: its proof is the isolated run's verdict.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
API_DIR = REPO / "apps" / "infrx-api"


def _shared():
    path = API_DIR / "tests" / "contracts" / "mutants.py"
    spec = importlib.util.spec_from_file_location("ap11_shared_mutants", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


shared = _shared()
Mutant, Outcome, Result, Runner, _m = (shared.Mutant, shared.Outcome, shared.Result,
                                       shared.Runner, shared._m)

D = "tests/integration/api_lifecycle/"
R, S, C, K = D + "runner.py", D + "state.py", D + "stages/consumer.py", D + "stages/__init__.py"
TESTS = D + "test_runner.py"

PRIVATE_FILES = "test_ap11_state_and_secrets_are_separate_private_files"
READABLE = "test_ap11_a_readable_state_or_secrets_file_is_invalid"
TARGET = "test_ap11_a_state_from_another_target_is_invalid"
LOST_ACK = "test_ap11_a_lost_acknowledgement_is_not_run_and_keeps_the_pending_key"
ORIGINAL_KEY = "test_ap11_restart_retries_the_original_key_and_counts_no_new_request"
RECONCILE = "test_ap11_restart_reconciles_a_recorded_mutation_by_get_and_never_resends_it"
NOT_REPEATED = "test_ap11_a_finished_stage_is_not_repeated_on_restart"
OTHER_BODY = "test_ap11_a_recorded_mutation_resent_with_another_body_is_invalid"
BUDGET = "test_ap11_the_request_budget_bounds_inference_across_restarts"
LIVE = "test_ap11_live_is_refused_without_its_explicit_config"
SEEDED = "test_ap11_live_never_uses_a_seeded_credential"
INSPECT = "test_ap11_inspect_mode_sends_only_reads"
CLEANUP = "test_ap11_cleanup_touches_only_state_owned_resources"
EXITS = "test_ap11_exit_codes_and_the_gate_follow_environment_md"
SECRETS = "test_ap11_no_secret_reaches_the_verdict_the_output_or_the_state"
EIGHTEEN = "test_ap11_the_stages_are_verifications_eighteen_steps"
CONTRACT_PATHS = "test_ap11_every_target_route_is_a_contracts_md_path"
NO_API = "test_ap11_a_stage_without_its_api_is_blocked_naming_the_prerequisite"
PARTLY = "test_ap11_a_partly_mounted_stage_runs_its_existing_routes_and_stays_blocked"
DEPENDENT = "test_ap11_a_dependent_stage_is_blocked_while_its_predecessor_has_not_passed"
EVIDENCE = "test_ap11_every_stage_records_utc_times_routes_statuses_ids_and_counters"
ROWS = "test_ap11_the_consumer_stages_prove_their_rows_on_the_mounted_routes"
DEFECT = "test_ap11_a_product_defect_fails_its_stage"
EMPTY = "test_ap11_a_stage_that_asserted_nothing_is_never_a_pass"
OPERATION = "test_ap11_an_operation_is_accepted_only_as_r270_states_it"

MUTANTS: tuple[Mutant, ...] = (
    # ---- 11a: the two private files
    _m("state_file_world_readable", "the state file is 0600 from its first byte", S,
       "PRIVATE = 0o600", "PRIVATE = 0o644", PRIVATE_FILES),
    _m("exposed_file_accepted", "a group/world-readable state or secrets file is INVALID", S,
       "    if mode & 0o077:", "    if False:", READABLE),
    _m("resume_across_targets", "a state file resumes only its own target", S,
       'if data.get("target") != target:', "if False:", TARGET),
    _m("sent_before_recorded", "a mutation is recorded pending before it leaves", S,
       '"request_hash": request_hash, "status": "pending",',
       '"request_hash": request_hash, "status": "done",', LOST_ACK),
    _m("budget_reset_on_resume", "the request budget is persisted across restarts", S,
       "            return cls(path, data, resumed=True)",
       '            return cls(path, {**data, "counters": {"requests": 0, "inference": 0}}, '
       "resumed=True)", BUDGET),
    _m("secret_value_kept", "every value of the secrets file is scrubbed", S,
       "            if len(secret) >= 6:", "            if False:", SECRETS),
    _m("bearer_kept", "a bearer credential's shape is scrubbed", S,
       '    (re.compile(r"(?i)\\bbearer\\s+[\\w\\-.~+/=]+"), "<redacted:bearer>"),\n', "", SECRETS),
    _m("key_kept", "an sk- key's shape is scrubbed", S,
       '    (re.compile(r"\\bsk-[A-Za-z0-9_\\-]{8,}"), "<redacted:key>"),\n', "", SECRETS),
    _m("jwt_kept", "a JWT's shape is scrubbed", S,
       '    (re.compile(r"\\beyJ[\\w\\-]+\\.[\\w\\-]+\\.[\\w\\-]+"), "<redacted:jwt>"),\n', "",
       SECRETS),
    _m("dsn_password_kept", "a URL's password is scrubbed", S,
       '    (re.compile(r"(\\b[a-z][a-z0-9+.\\-]*://)[^\\s/@:]+:[^\\s/@]+@"), r"\\1<redacted>@"),\n',
       "", SECRETS),
    _m("secret_field_kept", "a field named like a credential is scrubbed whole", S,
       '"<redacted>" if str(k).lower() in SECRET_FIELDS else', '"<redacted>" if False else',
       SECRETS),
    # ---- 11a: resume and reconcile
    _m("retry_under_a_new_key", "a pending mutation is retried with its ORIGINAL key", R,
       "        if entry is None:\n            if inference:",
       '        if entry is None or entry["status"] == "pending":\n            if inference:',
       ORIGINAL_KEY),
    _m("retry_spends_budget", "a resume retry is the same request, not a new one", R,
       "        if entry is None:\n            if inference:\n                self._spend()\n",
       "        if inference:\n            self._spend()\n        if entry is None:\n", ORIGINAL_KEY),
    _m("done_mutation_resent", "a recorded outcome is never re-sent", R,
       'if entry is not None and entry["status"] == "done":', "if False:", RECONCILE),
    _m("reconcile_skipped", "a recorded outcome is confirmed by a GET on resume", R,
       "            if reconcile is not None:", "            if False:", RECONCILE),
    _m("passed_stage_rerun", "a stage that passed is not repeated", R,
       '        if recorded is not None and recorded["status"] == PASS:', "        if False:",
       NOT_REPEATED),
    _m("changed_body_reuses_key", "a recorded key is never reused for another body", R,
       'if entry is not None and entry["request_hash"] != request_hash:', "if False:",
       OTHER_BODY),
    _m("budget_ignored", "inference stops at the request budget", R,
       'if counters["inference"] >= self.session.max_requests:', "if False:", BUDGET),
    _m("unknown_outcome_continues", "nothing runs after an unknown outcome", R,
       'stop = f"not run: stage {stage.sid} was interrupted"', "stop = None", LOST_ACK),
    _m("unknown_outcome_is_a_fail", "an unknown outcome is NOT RUN (resume), never FAIL", R,
       "        except (Interrupted, KeyboardInterrupt) as lost:",
       "        except (KeyboardInterrupt,) as lost:", LOST_ACK),
    # ---- 11a: modes
    _m("live_without_origins", "live names its origins", R,
       '            problems.append("origins")', "            pass", LIVE),
    _m("live_without_identities", "live names its identities", R,
       '            problems.append("identities")', "            pass", LIVE),
    _m("no_target_accepted", "every run names its target", R,
       '    if not config.get("target"):', "    if False:", LIVE),
    _m("live_budget_unchecked", "live names an exact budget in a known unit", R,
       'api.Money.model_validate(config.get("budget") or {})',
       'api.Money.model_validate({"amount": "1", "unit": "CREDIT"})', LIVE),
    _m("live_max_raised", "live sends at most six inference requests", R,
       "LIVE_MAX_REQUESTS = 6 ", "LIVE_MAX_REQUESTS = 7 ", LIVE),
    _m("live_max_optional", "live must name max_requests", R,
       "if type(most) is not int or not 1 <= most",
       "if type(most) is int and not 1 <= most", LIVE),
    _m("live_fixtures_allowed", "live declares no fixture", R,
       '        if config.get("fixtures"):', "        if False:", LIVE),
    _m("seeded_consumer_key", "a consumer key is API-minted or a declared isolated fixture", R,
       'if found.get("audience") == "consumer" \\', "if False \\", SEEDED),
    _m("inspect_selects_mutations", "inspect selects read-only stages only", R,
       "if (s.reads_only or args.mode != \"inspect\")", "if (s.reads_only or True)", INSPECT),
    _m("inspect_sends_a_mutation", "inspect refuses any non-GET before it leaves", R,
       'if self.mode == "inspect" and method not in ("GET", "HEAD"):', "if False:", INSPECT),
    _m("cleanup_claims_unowned_work", "a resource without a cleanup route is left alone", R,
       'outcome = "nothing to clean (expires by retention)"', 'outcome = "cleaned"', CLEANUP),
    _m("cleanup_gone_is_a_failure", "an already-gone owned resource is not a failure", R,
       '"gone" if answer.status_code in (404, 410)', '"failed" if answer.status_code in (404, 410)',
       CLEANUP),
    # ---- 11a: verdict, exit codes, evidence
    _m("not_run_exits_zero", "NOT RUN exits 3, never 0", R,
       "EXIT = {PASS: 0, FAIL: 1, BLOCKED: 3, NOT_RUN: 3, INVALID: 4}",
       "EXIT = {PASS: 0, FAIL: 1, BLOCKED: 3, NOT_RUN: 0, INVALID: 4}", EXITS),
    _m("gate_is_the_best_status", "the gate is the WORST selected status", R,
       "    return max(statuses, key=RANK.__getitem__, default=NOT_RUN)",
       "    return min(statuses, key=RANK.__getitem__, default=NOT_RUN)", EXITS),
    _m("fail_ranked_below_invalid", "FAIL outranks INVALID and BLOCKED", R,
       "RANK = {PASS: 0, NOT_RUN: 1, BLOCKED: 2, INVALID: 3, FAIL: 4}",
       "RANK = {PASS: 0, NOT_RUN: 1, BLOCKED: 2, INVALID: 5, FAIL: 4}", EXITS),
    _m("verdict_unredacted", "the verdict is redacted before it is written", R,
       "    payload = secrets.redact(payload)", "    payload = payload", SECRETS),
    _m("checkpoint_unredacted", "a checkpoint's evidence is redacted in the state file", R,
       '"evidence": session.secrets.redact(evidence)}', '"evidence": evidence}', SECRETS),
    _m("headers_in_evidence", "an exchange records no header", R,
       '"method": method, "route": route, "origin": origin,',
       '"method": method, "route": route, "authorization": sent.get("Authorization"), '
       '"origin": origin,', SECRETS),
    _m("evidence_without_start", "every stage records its UTC start", R,
       'return {"started": utc_now(), "ended": None,', 'return {"started": "", "ended": None,',
       EVIDENCE),
    _m("route_instead_of_template", "evidence records the route template, never an id", R,
       '"method": method, "route": route, "origin": origin,',
       '"method": method, "route": route.format(**(params or {})), "origin": origin,', EVIDENCE),
    # ---- 11b: stage semantics
    _m("absent_route_unreported", "a missing route is a BLOCKED reason", R,
       "for owner, routes in stage.missing().items()]", "for owner, routes in {}.items()]",
       NO_API, PARTLY),
    _m("absent_api_is_a_pass", "a stage without its API is BLOCKED, never PASS", R,
       "entry.update(status=BLOCKED, reasons=missing + [",
       "entry.update(status=PASS, reasons=missing + [", NO_API),
    _m("missing_route_passes", "a partly mounted stage stays BLOCKED", R,
       "    if blocked or missing:", "    if blocked:", PARTLY, EMPTY),
    _m("empty_stage_passes", "a stage that asserted nothing is never a pass", R,
       "    return PASS if checks else NOT_RUN", "    return PASS", EMPTY),
    _m("failed_check_hidden", "any failed assertion is FAIL", R,
       "    if not all(checks):", "    if False:", EMPTY, EXITS),
    _m("predecessor_any_status", "a predecessor's outputs are read only after it PASSED", R,
       'if recorded is None or recorded["status"] != PASS:', "if recorded is None:", DEPENDENT),
    _m("owner_inverted", "the missing routes are the owned (target) ones", K,
       "            if route.owner is not None:", "            if route.owner is None:",
       NO_API, PARTLY),
    _m("a_step_reworded", "the stages are verification.md's rows verbatim", K,
       '"Roll back/retire test listing and candidate through operator/control APIs"',
       '"Roll back test listing and candidate through operator/control APIs"', EIGHTEEN),
    _m("a_route_nobody_builds", "every target route is a contracts.md path", K,
       '_r("POST", "/lab/v1/artifacts/imports", "AP-04"),',
       '_r("POST", "/lab/v1/artifacts/import", "AP-04"),', CONTRACT_PATHS),
    _m("operation_200_accepted", "a long operation answers 202", K,
       "    if response.status_code != 202:", "    if response.status_code not in (200, 202):",
       OPERATION),
    _m("operation_without_location", "a 202 operation carries a Location", K,
       '    if not response.headers.get("Location"):', "    if False:", OPERATION),
    _m("operation_unvalidated", "the operation document is R270's OperationDoc", K,
       "return api.OperationDoc.model_validate(response.json())",
       "return api.OperationDoc.model_construct(**response.json())", OPERATION),
    # ---- 11b: the mounted stages' product assertions (each against a fake defect)
    _m("catalog_unchecked", "08: the catalog lists the model under test", C,
       "listed.status_code == 200 and row is not None", "listed.status_code == 200", EXITS),
    _m("replay_handle_unchecked", "09: a replay returns the same job", C,
       '              and _json(replay).get("job_handle") == job["job_handle"], replay.status_code)',
       "              , replay.status_code)", DEFECT),
    _m("conflict_unchecked", "09: a changed body under the key is 409", C,
       "clash.status_code == 409,", "clash.status_code in (202, 409),", DEFECT),
    _m("artifact_upload_unchecked", "09: an upload is never a model artifact", C,
       '              and not {"artifact_id", "manifest"} & set(done["fields"]), done["fields"])',
       '              , done["fields"])', DEFECT),
    _m("foreign_read_unchecked", "10: consumer B cannot read A's job", C,
       "foreign.status_code == 404,", "foreign.status_code in (200, 404),", DEFECT),
    _m("result_model_unchecked", "10: the result names the model under test", C,
       '(result.get("response") or {}).get("model") == ctx.config["model"],', "True,", DEFECT),
    _m("result_usage_unchecked", "10: the result reports usage", C,
       'bool(result.get("usage")), None)', "True, None)", DEFECT),
    _m("sync_202_accepted", "11: sync answers in-line, never an implicit async 202", C,
       'sync["status"] == 200, sync["status"])', 'sync["status"] in (200, 202), sync["status"])',
       DEFECT),
    _m("sync_usage_unchecked", "11: sync reports usage", C,
       'sync["model"] == ctx.config["model"] and sync["usage"], sync)',
       'sync["model"] == ctx.config["model"], sync)', DEFECT),
    _m("sse_content_type_unchecked", "11: SSE is an event stream", C,
       'sse["status"] == 200 and sse["event_stream"], sse)', 'sse["status"] == 200, sse)', DEFECT),
    _m("sse_end_unchecked", "11: SSE ends with [DONE]", C,
       '"done": bool(frames) and frames[-1] == "[DONE]",', '"done": True,', DEFECT),
    _m("sse_model_unchecked", "11: SSE content is the model's", C,
       'sse["content"] and sse["models"] == [ctx.config["model"]], sse)',
       'sse["content"], sse)', DEFECT),
    _m("member_unchecked", "01: a member session reads its workspace", C,
       "member.status_code == 200,", "member.status_code in (200, 403),", DEFECT),
    _m("outsider_unchecked", "01: a non-member session is refused", C,
       "outsider.status_code in (403, 404),", "outsider.status_code in (200, 403, 404),", DEFECT),
    _m("forged_unchecked", "01: a forged session is refused 401", C,
       "forged.status_code == 401,", "forged.status_code in (200, 401),", DEFECT),
    _m("anonymous_unchecked", "01: a missing key is refused 401", C,
       "anonymous.status_code == 401,", "anonymous.status_code in (401, 404),", DEFECT),
    _m("valid_key_unchecked", "01: a valid consumer key is authenticated", C,
       "known.status_code == 404,", "known.status_code in (401, 404),", DEFECT),
    # the consumer stages' rows as a whole (the replay and foreign probes exist at all)
    _m("replay_probe_dropped", "09 probes replay and conflict under the job's key", C,
       '    key = {"Idempotency-Key": ctx.key("09.job")}\n',
       '    key = {"Idempotency-Key": "ap11-a-fresh-key"}\n', ROWS),
)


def case_names() -> set[str]:
    return set(re.findall(r"^def (test_\w+)\(", (REPO / TESTS).read_text(), re.M))


def _layer1(root: pathlib.Path) -> pathlib.Path:
    junk = shutil.ignore_patterns("__pycache__", ".venv", "node_modules", ".next")
    shutil.copytree(REPO / "tests" / "integration", root / "tests" / "integration", ignore=junk)
    shutil.copytree(API_DIR / "infrx", root / "apps" / "infrx-api" / "infrx", ignore=junk)
    docs = pathlib.Path("research", "plan", "api-lifecycle")
    (root / docs).mkdir(parents=True)
    for name in ("verification.md", "contracts.md"):
        shutil.copy2(REPO / docs / name, root / docs / name)
    return root


RUNNER = Runner(name="ap11", targets=(TESTS,), package="", layout=_layer1)


def run_mutant(mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run AP-11's lifecycle-runner mutation list"))
