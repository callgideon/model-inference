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
L = D + "stages/lab.py"
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
SERVED = "test_ap11_every_served_stage_passes_and_only_ap05_ap06_stay_blocked"
DRY = "test_ap11_a_dry_run_judge_is_labelled_and_never_a_judge_result"
NO_JUDGE = "test_ap11_without_a_judge_the_judge_stages_are_blocked_on_p10"
NO_TRACES = "test_ap11_without_trace_storage_the_capture_half_is_blocked"
UNCOMPOSED = "test_ap11_a_package_the_target_does_not_compose_blocks_by_name"
UNMOUNTED = "test_ap11_an_unmounted_route_is_blocked_by_name_never_failed"
MINTED = "test_ap11_keys_are_minted_through_the_api_and_kept_outside_the_state"
LOST_KEY = "test_ap11_a_lost_key_acknowledgement_revokes_it_and_mints_once_more"
PINNED = "test_ap11_a_cas_write_resumes_with_the_version_it_first_read"

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
       'if found.get("audience") == "consumer" and not minted and not fixture:', "if False:",
       SEEDED),
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
    _m("refusal_code_dropped", "a refused exchange records its R270 code", R,
       '"error": error_code(response),', '"error": None,', EVIDENCE),
    _m("route_instead_of_template", "evidence records the route template, never an id", R,
       '"method": method, "route": route, "origin": origin,',
       '"method": method, "route": route.format(**(params or {})), "origin": origin,', EVIDENCE),
    # ---- 11b: stage semantics
    _m("absent_route_unreported", "a missing route is a BLOCKED reason", R,
       "for owner, routes in stage.missing(composed).items()]", "for owner, routes in {}.items()]",
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
       "            if route.owner is not None and route.owner not in composed:",
       "            if route.owner is None and route.owner not in composed:",
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
       "A's job\", foreign.status_code == 404,", "A's job\", foreign.status_code in (200, 404),",
       DEFECT),
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
    # ---- 11c: composition, minted keys, pinned versions, labels (runner + stage contracts)
    _m("composed_ignored", "a route the target does not compose is missing", K,
       "            if route.owner is not None and route.owner not in composed:",
       "            if route.owner is not None and False:", UNCOMPOSED),
    _m("unserved_stage_called", "a stage none of whose routes is served is never called", R,
       "        if stage.run is None or not served:", "        if stage.run is None:",
       UNCOMPOSED, NO_API),
    _m("unmounted_ignored", "the framework's own 404/405 is BLOCKED by name", R,
       "        if contracts.unmounted(response):", "        if False:", UNMOUNTED),
    _m("any_404_unmounted", "an R270 not-found envelope is a mounted route's answer", K,
       '    return response.status_code == 404 and isinstance(body, dict) and set(body) == {"detail"}',
       "    return response.status_code == 404", UNMOUNTED),
    _m("minted_secret_in_state", "the state holds a minted key's id, never its secret", R,
       '        state.data["minted"][holder] = name',
       '        state.data["minted"][holder] = secret', MINTED),
    _m("minted_file_unwritten", "a minted secret is kept 0600 beside the state", R,
       "        write_private(minted_file(state.path), minted)\n", "", MINTED),
    _m("minted_file_exposed", "a readable minted-key file is INVALID", R,
       "        require_private(path)\n        secrets.values.update",
       "        secrets.values.update", MINTED),
    _m("minted_not_reloaded", "a resume reads the minted keys back", R,
       "        secrets.values.update(json.loads(path.read_text()))", "        pass",
       ORIGINAL_KEY),
    _m("minted_reported_as_fixture", "a minted key is never reported as a fixture", R,
       "        fixture = not minted and secret in", "        fixture = secret in", MINTED),
    _m("pinned_reread", "a CAS version is read once and pinned for the resume", R,
       "        if name not in kept:", "        if True:", PINNED),
    _m("key_check_without_a_key", "01 checks a key only once one resolves", R,
       "        except Blocked:\n            return False", "        except Blocked:\n            return True",
       SERVED),
    _m("label_dropped", "a stand-in's label reaches the verdict", R,
       '                     label=evidence.get("label"))', "                     label=None)", DRY),
    _m("cleanup_stops_at_one_row", "one row's missing credential never stops the others", R,
       '                outcome = f"failed: {why}"', "                raise", CLEANUP),
    _m("dry_run_unlabelled", "14's dry run is labelled", L,
       "    if mode == \"dry_run\":\n        ctx.label(DRY_RUN)", "    if False:\n        ctx.label(DRY_RUN)",
       DRY),
    _m("judge_without_p10", "14 is BLOCKED on P-10 without a live judge or a dry run", L,
       '    if mode not in ("dry_run", "live"):', "    if False:", NO_JUDGE),
    _m("capture_without_storage", "12 sends no captured request without trace storage", C,
       '    if not ctx.config.get("traces"):', "    if False:", NO_TRACES),
    _m("lost_secret_key_reused", "a key whose secret was lost is never used", C,
       '        if ctx.minted(holder) == made["key_id"]:', "        if True:", LOST_KEY),
    _m("lost_secret_key_left_live", "a key whose secret was lost is revoked", C,
       '        ctx.call("DELETE", "/console/v1/keys/{id}", params={"id": made["key_id"]}, actor=web,\n'
       '                 headers={"Idempotency-Key": ctx.key(attempt) + ".revoke"})\n', "", LOST_KEY),
    # ---- 11c: the served stages' product assertions (each against a fake defect)
    _m("inference_id_unchecked", "11: a sync answer names its request", C,
       'bool(sync["request_id"]), None)', "True, None)", DEFECT),
    _m("key_shaped_session", "01: a key is never a web session", C,
       "keyed.status_code == 401,", "keyed.status_code in (200, 401),", DEFECT),
    _m("consumer_operator_unchecked", "01: no operator console for a consumer", C,
       '(allowed.get("actions") or {}).get("operator_console") is False', "True", DEFECT),
    _m("outsider_workspace_unchecked", "01: a non-member holds no workspace", C,
       'ctx.check(f"{actor} holds no workspace", mine == [], mine)',
       'ctx.check(f"{actor} holds no workspace", True, mine)', DEFECT),
    _m("outsider_members_unchecked", "01: an outsider cannot list the members", C,
       "listed.status_code in (403, 404),", "listed.status_code in (200, 403, 404),", DEFECT),
    _m("second_grant_unchecked", "08: a second claim replays", C,
       'again.get("status") == "replayed", again.get("status"))', "True, again.get(\"status\"))",
       DEFECT),
    _m("account_grant_unchecked", "08: the grant is on the account", C,
       '                  me.get("state") == "ready" and grant.get("state") == "granted"\n'
       '                  and (grant.get("amount") or {}).get("unit") == "CREDIT", grant)',
       "                  True, grant)", DEFECT),
    _m("replay_key_unchecked", "08: a retried creation is the same key", C,
       '              (replay.get("key") or {}).get("key_id") == made["key_id"]\n              and ',
       "              ", DEFECT),
    _m("rerevealed_secret_unchecked", "08: a replay never re-reveals the secret", C,
       '              and replay.get("secret") is None and replay.get("replayed") is True,',
       '              and replay.get("replayed") is True,', DEFECT),
    _m("live_keys_unchecked", "08: one live key despite the retried creation", C,
       '[k.get("id") for k in named] == [made["key_id"]], len(named))', "True, len(named))",
       DEFECT),
    _m("held_reserve_unchecked", "10: the reserve is released", C,
       '              and row.get("hold_state") in SETTLED_HOLDS,', "              and True,", DEFECT),
    _m("debit_count_unchecked", "10: exactly one ledger debit for the request", C,
       "              len(debits) == 1 and _amount", "              _amount", DEFECT),
    _m("console_foreign_unchecked", "10: B's console cannot read A's request", C,
       "    ctx.check(\"consumer B's console cannot read A's request\", foreign.status_code == 404,",
       "    ctx.check(\"consumer B's console cannot read A's request\", True,", DEFECT),
    _m("capture_scope_unchecked", "12: only the test key captures", C,
       '              and capture["modes"].get(earlier) in ("off", None), capture["modes"])',
       '              , capture["modes"])', DEFECT),
    _m("grant_persistence_unchecked", "12: the grant is persisted", C,
       '              len(listed) == 1 and sorted(listed[0].get("purposes") or ()) == [\n'
       '                  "external_judging", "provider_sharing"], listed)', "              True, listed)",
       DEFECT),
    _m("operation_failure_accepted", "02: the verification operation succeeds", L,
       'ctx.require("the verification operation succeeds", final.get("state") == "succeeded",',
       'ctx.require("the verification operation succeeds", True,', DEFECT),
    _m("operation_replay_unchecked", "02: the operation completes once", L,
       '              _json(again).get("operation_id") == done["operation_id"]\n              and ',
       "              ", DEFECT),
    _m("manifest_unchecked", "03: the manifest covers every uploaded file", L,
       '              == sorted((f["relative_path"], f["sha256"]) for f in out["manifest"])\n'
       '              and artifact', "              is not None and artifact", DEFECT),
    _m("revision_pins_unchecked", "03: the revision pins its profile", L,
       '              and revision.get("profile"), {k: revision.get(k) for k in (',
       "              , {k: revision.get(k) for k in (", DEFECT),
    _m("revision_replay_unchecked", "03: the revision is immutable", L,
       '              again.get("serving_version_id") == revision.get("serving_version_id"),',
       "              True,", DEFECT),
    _m("trace_pins_unchecked", "13: same model pins as the call", L,
       '              and detail.get("price_version") and detail.get("model_id")\n'
       '              == ctx.config["model_uuid"], {k',
       '              and detail.get("price_version"), {k', DEFECT),
    _m("trace_content_unchecked", "13: the content is the request's real capture", L,
       '              and TEXT[0]["content"] in str(detail.get("content") or ""),', "              ,",
       DEFECT),
    _m("zero_elapsed_accepted", "13: timing is never zero", L,
       'detail["elapsed_ms"] > 0, detail.get("elapsed_ms"))',
       'detail["elapsed_ms"] >= 0, detail.get("elapsed_ms"))', DEFECT),
    _m("uncaptured_unchecked", "13: the uncaptured earlier request has no content", L,
       '                  before.status_code == 404 or _json(before).get("access_state") == "not_captured",',
       "                  True,", DEFECT),
    _m("run_replay_unchecked", "14: a replayed run request is the same run", L,
       '              and (_json(replay).get("resource_id") or _json(replay).get("operation_id"))\n'
       '              == run["run_id"], replay.status_code)', "              , replay.status_code)",
       DEFECT),
    _m("dry_run_send_unchecked", "14: a dry run sends nothing and spends nothing", L,
       '                  doc.get("domain_state") in NOT_SCORED and doc.get("sent") == 0\n'
       '                  and not _money(doc.get("settled")), {k',
       '                  doc.get("domain_state") in NOT_SCORED, {k', DEFECT),
    _m("dry_run_score_unchecked", "14: a dry run's result is never scored", L,
       '                  results is not None and not [r for r in results if r.get("state") == "scored"],',
       "                  results is not None,", DEFECT),
    _m("consumer_judge_charge_unchecked", "15: the consumer is not charged for judging", L,
       "                  all(_money(after.get(k)) == _money(before.get(k)) and _money(after.get(k))\n"
       "                      is not None for k",
       "                  all(_money(after.get(k)) is not None for k", DEFECT),
    _m("dry_run_budget_unchecked", "15: a dry run reserves and settles nothing", L,
       '                  _money(first.get("reserved")) == 0 and _money(first.get("settled")) == 0,',
       "                  True,", DEFECT),
    _m("review_replay_unchecked", "16: the review is immutable", L,
       '              again.get("review_id") == review["review_id"], again.get("review_id"))',
       '              True, again.get("review_id"))', DEFECT),
    _m("review_provenance_unchecked", "16: the review's provenance is human", L,
       '                and review["provenance"] == "human", review)', "                , review)",
       DEFECT),
    _m("calibration_unchecked", "16: an inadequate reference sample is never calibrated", L,
       '              calibration.get("state") in ("insufficient", "uncalibrated")\n'
       '              and (calibration.get("labels") or 0) < (calibration.get("required") or 1),',
       "              True,", DEFECT),
    _m("revoked_content_unchecked", "17: revoked content is no longer readable", L,
       '              after.get("request_id") == out["request_id"] and "content" not in after\n'
       '              and after.get("access") == "metadata" and after.get("access_state") == "revoked",',
       '              after.get("request_id") == out["request_id"],', DEFECT),
    _m("grant_without_feedback", "12's grant covers the feedback 16 reviews", C,
       '        "categories": ["request_content", "response_content", "feedback"],',
       '        "categories": ["request_content", "response_content"],', SERVED),
    _m("payer_not_version_4", "14's payer ref is 0029's lab ref (version-4 ids)", L,
       "payer_id = uuid.UUID(bytes=seed.digest()[:16], version=4)",
       "payer_id = uuid.uuid5(uuid.NAMESPACE_URL, seed.hexdigest())", SERVED),
    _m("revoked_judge_unchecked", "17: a follow-up judge run is refused", L,
       "              follow.status_code in (403, 409, 422), follow.status_code)",
       "              follow.status_code in (202, 403, 409, 422), follow.status_code)", DEFECT),
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
