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
       SVC, "if body.rubric_version not in rubric.RUBRICS:", "if False:", RUBRIC_CFG),
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
       START, "    if rubric is None:\n", "    if False:\n", UNGRADED),
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


def case_names() -> set[str]:
    pattern = re.compile(r"^def (test_\w+)", re.MULTILINE)
    return {name for path in (API_DIR / SUITE).glob("test_*.py") if path.name != "test_mutants.py"
            and not path.name.endswith("_pg.py")
            for name in pattern.findall(path.read_text())}


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    sys.exit(shared.main(MUTANTS, RUNNER, "run AP-08's mutation list"))
