#!/usr/bin/env python3
"""R32 for AP-08 (api-judge): one single-edit defect per decision `tests/ap08` claims.

Two lists. `MUTANTS` edits the Python (`infrx/`) through the shared runner; the copies run the
cases that need no database (`-m "not pg"`; the PostgreSQL cases skip there). `SQL_MUTANTS`
edits `0064_judge_api.sql` and is killed in process by a named check of the ap8 world
(`tests/d/code_mutants_d7.kill`: a database from the mutated migrations, the world's seed,
the check) - it needs Docker and the lane's key and skips visibly otherwise.

    INFRX_MUTANTS=all uv run --frozen pytest -q tests/ap08/test_mutants.py
    INFRX_D_TASK=ap8 INFRX_MUTANTS=all uv run --frozen pytest -q tests/ap08/test_mutants.py
    uv run --frozen python tests/ap08/mutants.py --list
"""
from __future__ import annotations

import pathlib
import re
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(API_DIR) not in sys.path:        # `python tests/ap08/mutants.py`
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Result, Runner, _m  # noqa: E402
from tests.d import migration_mutants as _d  # noqa: E402

SUITE = "tests/ap08"
SVC, RUB, DOORS = "lab/judge_api/service.py", "lab/judge_api/rubric.py", "lab/judge_api/doors.py"
ROUTE, SUB, START = "gateway/routes/lab_judge.py", "judge/submit.py", "judge/start.py"
CONTROL = "gateway/control.py"   # WR-5: R270Route moved from lab_judge.py
RUBJ, GOLD, ROLE = "judge/rubric.py", "judge/calibration/goldset.py", "lab/workers/__main__.py"

KEYED = "test_ap08_routes__run_identity_comes_from_the_idempotency_key"
ACTOR = "test_ap08_routes__only_a_verified_session_acts_and_identity_is_never_read_from_the_body"
CANCEL = "test_ap08_routes__a_cancel_is_recorded_and_the_run_reports_cancel_requested"
PAGES = "test_ap08_routes__lists_page_with_an_opaque_cursor"
RUBRIC_CFG = "test_ap08_routes__a_configuration_names_a_graded_rubric_and_calibration_is_honest"
ENVELOPE = "test_ap08_routes__a_refused_door_is_an_r270_envelope_and_a_bug_hides_its_message"
REVIEWS = "test_ap08_routes__reviews_are_human_and_keyed_and_nothing_mounts_when_off"
ABSTAIN = "test_ap08_rubric__missing_video_abstains_on_media_criteria_and_never_passes"
QUARANTINE = "test_ap08_rubric__malformed_judge_output_is_quarantined_without_its_detail"
CALIBRATED = "test_ap08_rubric__calibration_needs_enough_reference_labels"
REFUSAL = "test_ap08_units__a_door_refusal_is_the_status_r270_names"
FROZEN = "test_ap08_units__the_sample_is_frozen_by_the_run_and_bounded"
UNGRADED = "test_ap08_units__start_refuses_what_it_cannot_grade_and_waits_for_traces"
DRYRUN = "test_ap08_units__a_dry_run_worker_refuses_and_counts_it"
LOOPBACK = "test_ap08_egress__loopback_only_by_default"
LIVE_ONLY = "test_ap08_egress__the_allowlist_applies_only_in_live_mode"
HTTPS = "test_ap08_egress__a_listed_host_only_over_https"
BARE = "test_ap08_egress__an_allowlist_entry_is_a_bare_host"
READS = "test_ap08_routes__every_read_takes_the_session"
ESTIMATE = "test_ap08_routes__the_estimate_is_a_report_and_no_unpriced_model_is_offered"
FULL = "test_ap08_rubric__a_full_result_scores_every_criterion_with_its_evidence"
REGISTRY = "test_ap08_rubric__the_registry_documents_evidence_and_the_output_schema"
# api-judge-2
V_ROUND = "test_ap08_versions__a_definition_round_trips_and_its_digest_is_its_identity"
V_MALFORMED = "test_ap08_versions__a_malformed_definition_is_refused"
V_SKELETON = "test_ap08_versions__the_sop_v2_skeleton_is_definition_pending"
V_LIST = "test_ap08_routes__rubrics_list_their_state_and_a_pending_one_is_never_configured"
V_CREATE = "test_ap08_routes__a_reviewed_definition_becomes_one_immutable_version"
V_RESULTS = "test_ap08_routes__a_stored_versions_results_abstain_on_its_media_criteria"
PINNED = "test_ap08_units__start_and_collect_grade_with_the_runs_pinned_rubric"
G_TRUTH = "test_ap08_goldset__a_reviewed_set_is_operator_truth_of_one_configuration"
G_STORED = "test_ap08_goldset__a_stored_row_is_the_j2_record_it_was"
G_THRESHOLD = "test_ap08_goldset__below_the_reference_threshold_it_is_insufficient"
R_START = "test_ap08_role__the_start_job_needs_the_eligible_read"
R_EGRESS = "test_ap08_role__a_remote_judge_is_an_allowlisted_https_host_in_live_mode_only"
R_GOLD = "test_ap08_role__the_gold_set_is_a_reviewed_reference_set_or_nothing_starts"
R_CALIBRATE = "test_ap08_role__each_collect_pass_is_followed_by_the_gold_set_calibration"

MUTANTS: tuple[Mutant, ...] = (
    # --- run identity and idempotency (08a) --------------------------------------------------
    _m("run_id_is_random", "the run id is derived from the Idempotency-Key, not minted",
       SVC, 'run = str(scoped_id("judge.run", provider, user, key))', "run = str(uuid.uuid4())",
       KEYED),
    _m("key_not_scoped_to_actor", "a key is scoped to the session user",
       SVC, '"\\n".join((action, provider, user, key, *more))',
       '"\\n".join((action, provider, key, *more))', KEYED),
    _m("replay_conflict_ignored", "the same key with another body is 409",
       SVC, 'raise errors.IdempotencyConflict("this key already requested another run")',
       "pass", KEYED),
    _m("review_id_is_random", "a review's identity is derived from the Idempotency-Key",
       SVC, '"review_id": str(scoped_id("trace.review", provider, user, key, request)),',
       '"review_id": str(uuid.uuid4()),', REVIEWS),
    _m("empty_rate_table_configured", "no approved judge rate reads as unavailable (P-10)",
       SVC, 'availability = api.Availability(state="configured") if data else',
       'availability = api.Availability(state="configured") if True else', ESTIMATE),
    _m("cursor_garbage_accepted", "a cursor this API did not issue is refused",
       SVC, 'raise errors.InvalidCursor("this cursor was not issued here") from None',
       "return None", PAGES),
    _m("page_overflows", "a page holds at most `limit` rows",
       SVC, "return rows[:limit], encode_cursor(key(rows[limit - 1]))",
       "return rows, encode_cursor(key(rows[limit - 1]))", PAGES),
    _m("queued_cancel_still_queued", "a cancelled request that never started reads cancelled",
       SVC, 'return "cancelled" if row.get("cancel_requested_at") else "queued"',
       'return "queued"', CANCEL),
    _m("cancel_requested_hidden", "a cancel of in-flight work is `cancel_requested`",
       SVC, 'if cancel and op_state == "running":', "if False:", CANCEL),
    _m("ungraded_rubric_configured", "a configuration pins a rubric version the worker grades",
       SVC, '            raise errors.InvalidRequest("no such rubric version", param="rubric_version")',
       "            pass", RUBRIC_CFG),
    # --- actor and envelope ------------------------------------------------------------------
    _m("key_audience_acts", "only a verified session acts on the judge API",
       ROUTE, 'if actor.audience != "session" or not actor.user_id:',
       "if not actor.user_id:", ACTOR),
    _m("validation_not_enveloped", "request validation is an R270 422 naming the field",
       CONTROL, "                return invalid(exc, request_id(request))",
       "                raise", KEYED),
    _m("bug_message_leaks", "a bug's message never reaches the wire",
       CONTROL, "                return error_response(exc, request_id(request))",
       '                return JSONResponse({"error": str(exc)}, 500)', ENVELOPE),
    _m("models_without_session", "every read takes the verified session",
       ROUTE, "        await session_user(rt, request)\n        return control.ok(judge.models())",
       "        return control.ok(judge.models())", READS),
    # --- the door refusals -------------------------------------------------------------------
    _m("role_refusal_is_internal", "a door's role/grant refusal (42501) is 403",
       DOORS, 'if getattr(exc, "sqlstate", None) == "42501":', "if False:", REFUSAL),
    _m("budget_refusal_is_internal", "a budget refusal is 429 budget_exceeded",
       DOORS, 'return errors.RateLimitError(mapped.detail, code="budget_exceeded")',
       "return mapped", REFUSAL),
    # --- result projection (08b) and calibration (08e) ---------------------------------------
    _m("missing_video_criterion_omitted", "a media criterion without video is shown abstained",
       RUB, '            shown.append(CriterionResult(name=c.name, state="abstained", '
            'evidence="media",',
       '            continue\n            shown.append(CriterionResult(name=c.name, '
       'state="abstained", evidence="media",', ABSTAIN),
    _m("scored_evidence_text", "a scored criterion carries its evidence requirement",
       RUB, "                                         evidence=_evidence(c)))",
       '                                         evidence="text"))', FULL),
    _m("rubric_evidence_text", "the rubric documents which criteria need video",
       RUB, 'return "media" if criterion.requires_media else "text"', 'return "text"',
       REGISTRY, FULL),
    _m("quarantine_reason_dropped", "a malformed result is quarantined with its reason",
       RUB, 'quarantine_reason=result["reason"]', "quarantine_reason=None", QUARANTINE),
    _m("calibrated_below_threshold", "calibrated only with enough reference labels",
       RUB, 'if c.get("state") == "calibrated" and labels >= required and agreement is not None:',
       'if c.get("state") == "calibrated":', CALIBRATED, RUBRIC_CFG),
    # --- the worker start step (08c) ---------------------------------------------------------
    _m("sample_unbounded", "the frozen sample is at most the configured size",
       START, "chosen = tuple(ranked[:size])", "chosen = tuple(ranked)", FROZEN),
    _m("sample_unseeded", "the sample is seeded by the run id",
       START, 'hashlib.sha256(f"{run_id}:{r}".encode()).digest()',
       'hashlib.sha256(f"{r}".encode()).digest()', FROZEN),
    _m("ungraded_rubric_started", "the worker refuses a rubric version it does not grade",
       START, '        raise errors.InvalidRequest("the worker grades no such rubric version")',
       "        return None", UNGRADED),
    _m("refusal_counted_as_failure", "a dry-run/budget/permission refusal is counted refused",
       START, '            done["refused"] += 1', '            done["failed"] += 1', DRYRUN),
    # --- the P-10 egress seam ----------------------------------------------------------------
    _m("allowlist_in_any_mode", "the allowlist applies only in live mode",
       SUB, "    if mode != JUDGE_MODE_LIVE:\n        return frozenset()\n", "", LIVE_ONLY),
    _m("listed_host_over_http", "a listed host is reached over https only",
       SUB, 'approved = parts.scheme == "https" and parts.hostname in allowed_hosts',
       "approved = parts.hostname in allowed_hosts", HTTPS),
    _m("allowlist_entry_unchecked", "an allowlist entry is a bare host name",
       SUB, '        raise errors.InvalidRequest("JUDGE_PROVIDER_ALLOWLIST names bare host names")',
       "        pass", BARE),
    _m("loopback_rule_dropped", "without the allowlist only loopback http egresses",
       SUB, 'if not approved and (parts.scheme != "http" or parts.hostname not in LOCAL_HOSTS):',
       'if not approved and parts.scheme != "http":', LOOPBACK),
    # --- api-judge-2: rubric versions -------------------------------------------------------
    _m("digest_blind_to_thresholds", "a version's definition and digest carry its thresholds",
       RUBJ, '"pass_at": c.pass_at,', '"pass_at": None,', V_ROUND),
    _m("definition_keys_open", "a definition has exactly the canonical keys",
       RUBJ, "if type(doc) is not dict or set(doc) != _DEFINITION_KEYS:",
       "if type(doc) is not dict:", V_MALFORMED),
    _m("evidence_kind_unchecked", "a criterion's evidence is media or text",
       RUBJ, '                                        and c["evidence"] in _EVIDENCE for c in raw):',
       "                                        for c in raw):", V_MALFORMED),
    _m("empty_sop_step_accepted", "an SOP step is a non-empty text",
       RUBJ, "type(step) is str and 0 < len(step.strip())", "type(step) is str", V_MALFORMED),
    _m("skeleton_shape_unchecked", "a definition of a reserved version fills its skeleton",
       RUBJ, "        if (rubric.rubric_id, [(c.name, c.requires_media) for c in rubric.criteria]) != \\",
       "        if False and (rubric.rubric_id, [(c.name, c.requires_media) for c in "
       "rubric.criteria]) != \\", V_SKELETON, V_CREATE),
    _m("sop_steps_optional", "the SOP rubric names its steps",
       RUBJ, '            raise ValueError(f"version {rubric.version} names its SOP steps")',
       "            pass", V_SKELETON),
    _m("task_correctness_from_text", "task correctness needs the video",
       RUBJ, 'Criterion("task_correctness", requires_media=True)',
       'Criterion("task_correctness")', V_SKELETON),
    _m("pending_not_listed", "a pending version is listed definition_pending",
       RUB, "for v, (skeleton, why) in j.PENDING.items() if v not in stored})",
       "for v, (skeleton, why) in j.PENDING.items() if False})", V_LIST),
    _m("pending_has_identity", "a pending skeleton has no digest",
       RUB, "        digest=None if pending else j.digest(r))", "        digest=j.digest(r))",
       V_LIST),
    _m("pending_configured", "a configuration of a pending version is 409",
       SVC, "            if version in j.PENDING:\n", "            if False:\n", V_LIST),
    _m("code_version_overwritten", "a reviewed code version is immutable",
       SVC, "        if r.version in rubric.RUBRICS:\n            raise errors.StateConflict(",
       "        if False:\n            raise errors.StateConflict(", V_CREATE),
    _m("stored_version_unconfigurable", "a stored version may be configured",
       SVC, "        if version not in rubric.RUBRICS and \\\n"
            "                version not in await self.stored_rubrics(user, provider):",
       "        if version not in rubric.RUBRICS:", V_CREATE),
    _m("review_not_required", "a stored definition names the review it passed",
       RUB, "    review_ref: str = Field(min_length=1, max_length=400)",
       '    review_ref: str = Field(default="", max_length=400)', V_CREATE),
    _m("review_inside_definition", "the review record is not part of the definition",
       RUB, 'return self.model_dump(mode="json", exclude={"review_ref"})',
       'return self.model_dump(mode="json")', V_CREATE),
    _m("digest_not_the_definitions", "the stored digest is the definition's",
       SVC, '"definition": j.definition(r), "digest": j.digest(r),',
       '"definition": j.definition(r), "digest": "sha256:" + "0" * 64,', V_CREATE),
    _m("stored_results_by_code_rubric", "a stored version's results project with it",
       SVC, '        if any(r["rubric_version"] not in known for r in shown):',
       "        if False:", V_RESULTS),
    _m("start_ignores_pinned_rubric", "a run starts with the rubric its configuration pins",
       START, 'rubric = RUBRICS.get(request["rubric_version"]) if wiring.rubric_of is None else',
       'rubric = RUBRICS.get(request["rubric_version"]) if True else', PINNED),
    _m("start_version_mismatch", "the pinned rubric is the request's version",
       START, 'if rubric is None or rubric.version != request["rubric_version"]:',
       "if rubric is None:", PINNED),
    _m("collect_with_first_rubric", "a run is collected with the rubric it pins",
       SUB, "rubric = MARLIN_VIDEO_V1 if wiring.rubric_of is None else await wiring.rubric_of(run_id)",
       "rubric = MARLIN_VIDEO_V1", PINNED),
    _m("collect_without_rubric", "no gradable rubric refuses the collect",
       SUB, '        raise errors.StateConflict(f"judge run {run_id} pins no rubric this worker grades")',
       "        return run", PINNED),
    # --- api-judge-2: the gold set ----------------------------------------------------------
    _m("gold_sample_twice", "a gold set labels each sample once",
       GOLD, "if len({label.sample_id for label in self.labels}) != len(self.labels):",
       "if False:", G_TRUTH),
    _m("gold_truth_is_customer", "gold labels are operator calibration labels",
       GOLD, "author_principal=gold.reviewed_by, author_role=AuthorRole.operator,",
       "author_principal=gold.reviewed_by, author_role=AuthorRole.customer,", G_TRUTH),
    _m("limited_read_as_full", "a stored no-media result stays limited",
       GOLD, "    return JudgeScores(**result)",
       '    return JudgeScores(**{**result, "limited": False, "overall_pass": False})',
       G_STORED),
    _m("rejected_read_as_scored", "a quarantined result is read back as rejected",
       GOLD, '    if not row["accepted"]:\n        return Rejected(**result)',
       '    if False:\n        return Rejected(**result)', G_STORED),
    _m("calibration_misfiled", "the calibration is stored under the gold set's configuration",
       GOLD, "judge_model=gold.judge_model, rubric_version=gold.rubric_version)\n",
       'judge_model="judge-x", rubric_version=gold.rubric_version)\n', G_THRESHOLD),
    _m("calibrated_on_few_pairs", "fewer than MIN_PAIRS reference pairs is insufficient",
       "judge/calibration/report.py", '    if n < MIN_PAIRS:\n        verdict = "insufficient"',
       '    if False:\n        verdict = "insufficient"', G_THRESHOLD),
    # --- api-judge-2: the judge role ----------------------------------------------------------
    _m("start_job_never_composed", "the role starts queued runs once the eligible read exists",
       ROLE, "    eligible = start.eligible_read(limits)", "    eligible = None", R_START),
    _m("start_job_without_read", "no start job without the eligible read",
       ROLE, "    if eligible is not None:\n        tasks[\"judge_start\"]",
       "    if True:\n        tasks[\"judge_start\"]", R_START),
    _m("role_grades_first_rubric", "the role's wiring grades each run with its rubric",
       ROLE, "settings=limits, rubric_of=start.pg_rubric_of(connect))", "settings=limits)",
       R_START),
    _m("role_allowlist_dropped", "the role honours the operator's allowlist in live mode",
       ROLE, '            limits.judge_mode, env.get("JUDGE_PROVIDER_ALLOWLIST", "")))',
       '            "dry_run", env.get("JUDGE_PROVIDER_ALLOWLIST", "")))', R_EGRESS),
    _m("role_allowlist_any_mode", "the allowlist applies only to a live role",
       ROLE, '            limits.judge_mode, env.get("JUDGE_PROVIDER_ALLOWLIST", "")))',
       '            "live", env.get("JUDGE_PROVIDER_ALLOWLIST", "")))', R_EGRESS),
    _m("role_gold_set_unchecked", "a malformed gold set refuses the role",
       ROLE, "    except (OSError, ValueError) as unreadable:\n        raise RuntimeMisconfigured(mode, "
             "detail=f\"JUDGE_GOLD_SET", "    except OSError as unreadable:\n        raise "
             "RuntimeMisconfigured(mode, detail=f\"JUDGE_GOLD_SET",
       R_GOLD),
    _m("role_never_calibrates", "a configured gold set is graded after each collect pass",
       ROLE, "        if gold is not None:\n            await goldset.calibrate(",
       "        if False:\n            await goldset.calibrate(", R_CALIBRATE),
)

RUNNER = Runner(name="ap08", targets=(SUITE,),
                extra_args=(f"--ignore={SUITE}/test_mutants.py", "-m", "not pg"))

# --- 0064 (killed in process by the ap8 world's checks) -----------------------------------------
FILE = "0064_judge_api.sql"
DOORS_WORLD, WORKER_WORLD = "doors", "worker"


def _s(name, old, new, world, check, why, **kw) -> tuple[_d.Mutant, str]:
    return _d.Mutant(name, FILE, old, new, "ap8", check, why, **kw), world


SQL_MUTANTS: tuple[tuple[_d.Mutant, str], ...] = (
    _s("ap8_configure_conflict_ignored",
       "    perform infrx.refuse('idempotency_conflict',\n"
       "                         'this key already configured different values');", "    null;",
       DOORS_WORLD, "check_a_configuration_is_keyed_replayed_and_role_checked",
       "a reused key silently answers another configuration"),
    _s("ap8_configure_without_grant",
       "  if not found then\n    if not infrx.lab_judge_granted(p_provider_org_id, "
       "p_grantor_org_id, p_model_id) then",
       "  if not found then\n    if false then", DOORS_WORLD,
       "check_a_configuration_is_keyed_replayed_and_role_checked",
       "a provider configures judging of a model no grant names"),
    _s("ap8_budget_by_developer",
       "  perform infrx.lab_judge_door(p_provider_org_id, 'administrator');",
       "  perform infrx.lab_judge_door(p_provider_org_id, 'developer');", DOORS_WORLD,
       "check_a_budget_change_is_keyed_and_administrator_only",
       "a developer raises the provider's PROVIDER_USD cap"),
    _s("ap8_budget_replay_writes_again",
       "and payer_ref = p_payer_ref and reason = v_reason;",
       "and payer_ref = p_payer_ref and false;", DOORS_WORLD,
       "check_a_budget_change_is_keyed_and_administrator_only",
       "a retried PUT writes a new limit version per retry"),
    _s("ap8_runs_of_any_provider", "     where q.provider_org_id = p_provider_org_id\n"
       "             and (p_run_id is null or q.run_id = p_run_id)",
       "     where (p_run_id is null or q.run_id = p_run_id)", DOORS_WORLD,
       "check_runs_are_listed_cancelled_and_results_follow_the_grant",
       "another provider lists this provider's judge runs"),
    _s("ap8_cancel_keeps_the_hold",
       "              where r.run_id = p_run_id and r.state = 'prepared') then",
       "              where false) then", DOORS_WORLD,
       "check_runs_are_listed_cancelled_and_results_follow_the_grant",
       "a cancelled reserved run keeps holding the payer's budget"),
    _s("ap8_results_after_revocation",
       "  if not infrx.lab_grant_current(r.grant_id, 'external_judging') then",
       "  if false then", DOORS_WORLD,
       "check_runs_are_listed_cancelled_and_results_follow_the_grant",
       "judge text about revoked content stays readable"),
    _s("ap8_review_conflict_ignored",
       "    perform infrx.refuse('idempotency_conflict', 'this key already stored another "
       "review');", "    null;", DOORS_WORLD,
       "check_a_human_review_is_stored_once_with_its_provenance",
       "a reused key answers a different review as if stored"),
    _s("ap8_viewer_reviews", "                    and m.role in ('developer', 'administrator')) then",
       "                    ) then", DOORS_WORLD,
       "check_a_human_review_is_stored_once_with_its_provenance",
       "a viewer writes human reviews"),
    _s("ap8_authenticated_executes", "  public.lab_review_feedback(jsonb)\n  to infrx_lab_control;",
       "  public.lab_review_feedback(jsonb)\n  to infrx_lab_control, authenticated;",
       DOORS_WORLD, "check_only_the_lab_login_gains_execute_and_the_flag_gates",
       "a browser session calls the new doors over PostgREST (R271)"),
    _s("ap8_lab_login_cannot_queue", "  public.lab_judge_request_run(uuid, uuid, uuid, text),\n"
       "  public.lab_judge_calibration(uuid, uuid, int),",
       "  public.lab_judge_calibration(uuid, uuid, int),", DOORS_WORLD,
       "check_the_http_family_composes_on_the_lab_login",
       "POST /lab/v1/judge/runs answers 403 on the Lab control unit"),
    _s("ap8_cancelled_request_started",
       "       and not exists (select 1 from infrx.lab_judge_cancellations k where k.run_id = "
       "q.run_id)", "", WORKER_WORLD,
       "check_cancel_dry_run_budget_and_revocation_send_nothing",
       "a cancelled request is judged and paid for"),
    _s("ap8_started_run_requeued", "     where (r.run_id is null or r.state = 'prepared')",
       "     where true", WORKER_WORLD, "check_an_ambiguous_send_is_quarantined_and_never_resent",
       "an ambiguous run is started again (a blind resubmission)"),
)


# --- SR-AP08-1 (`sr_ap08_1.sql`, applied by the world's seed until it is allocated) -------------
SR_STORE, SR_WORKER = "store", "worker"


def _r(name, old, new, world, check, why) -> tuple[str, str, str, str, str, str]:
    return name, old, new, world, check, why


STORED = "check_a_rubric_version_is_stored_once_by_an_operator"
READ = "check_a_configuration_pins_a_stored_version_and_the_worker_reads_it"
CALIB = "check_results_are_graded_by_the_runs_rubric_and_calibrated_against_the_gold_set"
SR_MUTANTS = (
    _r("sr_any_session_stores",
       "  if not coalesce((select p.is_operator from public.profiles p where p.id = auth.uid()), "
       "false)\n  then", "  if false\n  then", SR_STORE, STORED,
       "a provider developer writes the rubric every provider's judge grades with"),
    _r("sr_conflict_ignored", "    perform infrx.refuse('idempotency_conflict',\n"
       "                         'this rubric version is stored with another definition');",
       "    null;", SR_STORE, STORED, "a second definition silently answers as the stored one"),
    _r("sr_rubric_editable", "create or replace trigger lab_judge_rubrics_immutable before "
       "update or delete", "create or replace trigger lab_judge_rubrics_immutable before delete",
       SR_STORE, STORED, "a stored version is edited in place under runs graded with it"),
    _r("sr_list_any_session", "  perform infrx.lab_judge_door(p_provider_org_id, 'viewer');\n"
       "  return (select coalesce(jsonb_agg(infrx.lab_judge_rubric_json",
       "  return (select coalesce(jsonb_agg(infrx.lab_judge_rubric_json", SR_STORE, STORED,
       "a non-member reads the Lab's rubric versions"),
    _r("sr_browser_executes", "grant execute on function public.lab_judge_rubric_create(jsonb),",
       "grant execute on function public.lab_judge_rubric_create(jsonb) to authenticated;\n"
       "grant execute on function public.lab_judge_rubric_create(jsonb),", SR_STORE, STORED,
       "a browser session calls the rubric door over PostgREST (R271)"),
    _r("sr_any_version_definition",
       "    left join infrx.lab_judge_rubrics r on r.rubric_version = c.rubric_version",
       "    left join infrx.lab_judge_rubrics r on true", SR_STORE, READ,
       "a v1 run is graded with another version's stored definition"),
    _r("sr_unsettled_results", "join infrx.lab_judge_runs j on j.run_id = l.run_id and "
       "j.state = 'completed'", "join infrx.lab_judge_runs j on j.run_id = l.run_id",
       SR_WORKER, CALIB, "a half-collected run is calibrated as if complete"),
    _r("sr_results_any_model", "             and c.judge_model = p_args->>'judge_model'\n", "",
       SR_WORKER, CALIB, "another judge model's results calibrate this one"),
    _r("sr_results_any_version",
       "             and c.rubric_version = (p_args->>'rubric_version')::int\n", "",
       SR_WORKER, CALIB, "another rubric version's results calibrate this one"),
    _r("sr_results_any_provider",
       "           where q.provider_org_id = (p_args->>'provider_org_id')::uuid\n",
       "           where true\n", SR_WORKER, CALIB,
       "another provider's judge results calibrate this provider's configuration"),
    _r("sr_results_after_revocation",
       "             and infrx.lab_grant_current(j.grant_id, 'external_judging')\n", "",
       SR_WORKER, CALIB, "judge output about revoked content keeps feeding a calibration"),
)


def case_names() -> set[str]:
    pattern = re.compile(r"^def (test_\w+)", re.MULTILINE)
    return {name for path in (API_DIR / SUITE).glob("test_*.py") if path.name != "test_mutants.py"
            and not path.name.endswith("_pg.py")
            for name in pattern.findall(path.read_text())}


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    sys.exit(shared.main(MUTANTS, RUNNER, "run AP-08's mutation list"))
