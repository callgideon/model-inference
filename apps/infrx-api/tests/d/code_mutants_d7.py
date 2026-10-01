"""R32/R40 for D7: single-edit defects of `0029_lab_data.sql` (killed by the named check of
`test_d7_lab_data.py` on a database built from the mutated set, needs Docker) and of
`infrx/state/lab_data.py` (killed by the named case of `test_d7_units.py` through the shared
runner, no Docker). The SQL runner is L2-SQL's (`code_mutants_l2sql.kill`'s classification)
with this lane's database and seed.

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_code_mutants_d7.py
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from tempfile import TemporaryDirectory

import psycopg
from infrx.state import migrations

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Runner
from . import migration_mutants as _d
from . import pgharness
from . import test_d7_followup as followup
from . import test_d7_lab_data as t

FILE = "0029_lab_data.sql"
DB = f"{pgharness.DATABASE}_d7mut"

ROLES = "check_browser_roles_reach_nothing"
PUBLISH = "check_publication_is_content_addressed_and_immutable"
REFS = "check_refs_resolve_only_for_their_provider"
STALE = "check_a_stale_grant_stops_access_and_scheduling"
LEASES = "check_leases_are_fenced_and_one_result_per_case"
STATES = "check_run_and_checkpoint_states_follow_the_contract"
CHECKPOINT = "check_checkpoint_delivery_is_received_once"
RELAY = "check_the_relay_redelivers_a_lost_acknowledgment"
KINDS = "check_a_roles_relay_claims_only_its_own_kinds"
KINDS_FILE = "0050_lab_outbox_pending_kinds.sql"
JOBS = "check_import_jobs_are_a_durable_claim_and_lease_queue"
JOBS_FILE = "0051_lab_import_jobs.sql"
RACE = "check_two_publishers_race_to_one_version"
KILL = "check_a_kill_around_commit_recovers_once"
PLANS = "check_large_fixture_queries_use_their_indexes"


def _s(name, old, new, check, why, file=FILE, **kw):
    return _d.Mutant(name, file, old, new, "lab", check, why, **kw)


SQL_MUTANTS = (
    # --- DUR-RLS
    _s("d7_service_writes", "execute format('grant select on infrx.%I to service_role', t);",
       "execute format('grant select, delete on infrx.%I to service_role', t);", ROLES,
       "the platform role edits Lab state around every RPC check"),
    _s("d7_row_security_off", "execute format('alter table infrx.%I enable row level security'",
       "execute format('alter table infrx.%I disable row level security'", ROLES,
       "a future grant to a browser role exposes every provider's rows"),
    # --- DATA-IMMUTABLE
    _s("d7_digest_not_rederived", "  constraint lab_records_content_addressed check (ref = 'lab:' "
       "|| kind || ':' || provider_org_id", "  constraint lab_records_content_addressed check "
       "(true or ref = 'lab:' || kind || ':' || provider_org_id", PUBLISH,
       "a stored ref names bytes other than the ones stored"),
    _s("d7_version_redefinable", "create unique index if not exists lab_records_one_version",
       "create index if not exists lab_records_one_version", PUBLISH,
       "changed bytes publish as the same dataset version"),
    _s("d7_replay_refused", "    if not exists (select 1 from infrx.lab_records where ref = v_ref) "
       "then", "    if true then", PUBLISH,
       "republishing the same bytes (a retried upload) fails"),
    _s("d7_records_editable", "array['lab_sources', 'lab_records', 'lab_dataset_samples',\n"
       "                           'lab_eval_results'] loop", "array['lab_sources', "
       "'lab_dataset_samples',\n                           'lab_eval_results'] loop", PUBLISH,
       "a published manifest is edited in place"),
    _s("d7_split_ignored", "               where v_doc->'splits'->n ? (s->>'sample_id')),",
       "               where n = 'train'),", PUBLISH,
       "holdout samples are stored as training data"),
    _s("d7_parent_unresolved", "         else exists (select 1 from infrx.lab_records r\n",
       "         else true or exists (select 1 from infrx.lab_records r\n", PUBLISH,
       "a derived version names a parent that was never published"),
    _s("d7_publishers_not_serialized", "    on conflict do nothing;\n  exception when check_violation "
       "or not_null_violation or invalid_text_representation then\n    perform infrx.refuse("
       "'invalid_request', 'not a referable", "    on conflict (ref) do nothing;\n  exception when "
       "check_violation or not_null_violation or invalid_text_representation then\n    perform "
       "infrx.refuse('invalid_request', 'not a referable", RACE,
       "the losing publisher of a version gets a raw constraint error, not state_conflict"),
    _s("d7_finish_digest_unstable", "'results', v_results, 'cost', p_args->'cost')\n    || "
       "jsonb_strip_nulls", "'results', v_results, 'cost', p_args->'cost', 'at', "
       "clock_timestamp())\n    || jsonb_strip_nulls", KILL,
       "a finish retried after a lost answer is refused as a different outcome"),
    # --- DATA-RIGHTS: foreign ids
    _s("d7_resolve_any_provider", "   where ref = p_args->>'ref' and provider_org_id = "
       "(p_args->>'provider_org_id')::uuid;", "   where ref = p_args->>'ref';", REFS,
       "a provider reads another provider's manifest by its ref"),
    _s("d7_publish_for_another_provider", "  if v_doc->>'provider_org_id' is distinct from "
       "v_provider::text then", "  if false then", REFS,
       "a provider publishes a record under another provider's id"),
    _s("d7_source_of_any_provider", "                                        where s.ref = v_ref "
       "and s.provider_org_id = v_provider)", "                                        where "
       "s.ref = v_ref)", REFS, "a manifest cites another provider's source"),
    _s("d7_grant_to_any_provider", "     and g.recipient_provider_org_id = p_provider and "
       "infrx.lab_grant_ref(g) = p_ref", "     and infrx.lab_grant_ref(g) = p_ref", REFS,
       "a provider cites a grant made to another provider"),
    _s("d7_grant_ref_any_spelling", "     and g.recipient_provider_org_id = p_provider and "
       "infrx.lab_grant_ref(g) = p_ref", "     and g.recipient_provider_org_id = p_provider", REFS,
       "a grant ref with a forged provider or digest resolves"),
    _s("d7_mutable_ref_accepted", "    if v_parts is null then", "    if false then", REFS,
       "a label ref (@latest) is stored where an immutable ref must be"),
    _s("d7_unowned_kind_any_provider", "           then v_parts[2] = v_provider::text",
       "           then true", REFS, "a run names another provider's serving revision"),
    _s("d7_sample_not_bound_to_its_source", "  constraint lab_dataset_samples_source_grant foreign "
       "key (source_id, grant_id)\n    references infrx.lab_sources (source_id, grant_id) on "
       "delete restrict\n", "  check (true)\n", REFS,
       "a sample claims a grant its content was not captured under"),
    _s("d7_source_id_redefinable", "  if (s.provider_org_id, s.content_digest, s.grant_id, "
       "s.grant_version)", "  if (s.provider_org_id, p_args->>'content_digest', s.grant_id, "
       "s.grant_version)", REFS, "one source id silently names other content"),
    _s("d7_unknown_grant_is_forbidden", "  if g.grant_id is null then\n    perform infrx.refuse("
       "'not_found', 'no such grant for this provider');\n  end if;\n", "", REFS,
       "a foreign grant id is told apart from an unknown one"),
    _s("d7_malformed_source_raw_error", "  exception when check_violation or not_null_violation "
       "then\n    perform infrx.refuse('invalid_request', 'a source is", "  exception when "
       "raise_exception then\n    perform infrx.refuse('invalid_request', 'a source is", REFS,
       "a malformed import digest is a server error instead of a 400"),
    _s("d7_malformed_record_raw_error", "or not_null_violation or invalid_text_representation "
       "then\n    perform infrx.refuse('invalid_request', 'not a referable", "or "
       "invalid_text_representation then\n    perform infrx.refuse('invalid_request', "
       "'not a referable", REFS, "an unreferable schema is a server error instead of a 400"),
    # --- DATA-RIGHTS: current grants
    _s("d7_revocation_ignored", "  select coalesce((select g.revoked_at is null\n",
       "  select coalesce((select true\n", STALE, "a revoked grant keeps giving access"),
    _s("d7_expiry_ignored", "                          and (g.expires_at is null or "
       "g.expires_at > infrx.now())\n", "", STALE, "an expired grant keeps giving access"),
    _s("d7_any_purpose", "                          and (p_purpose is null or p_purpose = "
       "any(g.purposes))\n", "", STALE, "a grant for training is read as consent to capture"),
    _s("d7_oldest_version_current", "                    order by g.version desc limit 1), "
       "false)", "                    order by g.version limit 1), false)", STALE,
       "the version a manifest pinned outlives its revocation"),
    _s("d7_schedule_ungated", "  if exists (select 1 from infrx.lab_dataset_samples s\n",
       "  if false and exists (select 1 from infrx.lab_dataset_samples s\n", STALE,
       "a run is scheduled over revoked content"),
    _s("d7_source_under_revoked_grant", "  if not infrx.lab_grant_current(g.grant_id, null) then",
       "  if false then", STALE, "new content is registered under a revoked grant"),
    _s("d7_samples_of_any_provider", "     and r.provider_org_id = (p_args->>'provider_org_id')"
       "::uuid\n     and infrx.lab_grant_current", "     and infrx.lab_grant_current", STALE,
       "a provider lists another provider's accessible samples"),
    # --- EVAL-DURABLE
    _s("d7_cases_unbounded", "   order by s.sample_id limit (v_doc->>'max_cases')::int;",
       "   order by s.sample_id;", LEASES, "a run evaluates more cases than it was budgeted"),
    _s("d7_run_created_twice", "  if not found then\n    -- run:<run_id> replays (R161)",
       "  if false then\n    -- run:<run_id> replays (R161)", LEASES,
       "creating a run again fails (or doubles its cases and events)"),
    _s("d7_run_replays_to_any_provider", "from infrx.lab_eval_runs where run_ref = r.ref) then",
       "from infrx.lab_eval_runs where run_id = r.object_id) then", LEASES,
       "a provider publishing another's run_id reads that provider's run and squats its own"),
    _s("d7_run_event_missing", "  insert into infrx.lab_outbox (provider_org_id, kind, payload)"
       "\n  values (v_provider, 'eval_run',", "  perform (v_provider, 'eval_run',", LEASES,
       "a created run is never announced to the runner"),
    _s("d7_lease_any_provider", "  select * into r from infrx.lab_eval_runs where run_id = "
       "(p_args->>'run_id')::uuid\n     and provider_org_id = (p_args->>'provider_org_id')::uuid "
       "for update;", "  select * into r from infrx.lab_eval_runs where run_id = "
       "(p_args->>'run_id')::uuid\n     for update;", LEASES,
       "a provider leases another provider's cases"),
    _s("d7_fence_any_provider", "  select * into r from infrx.lab_eval_runs where run_id = "
       "(p_lease->>'run_id')::uuid\n     and provider_org_id = (p_lease->>'provider_org_id')"
       "::uuid for update;", "  select * into r from infrx.lab_eval_runs where run_id = "
       "(p_lease->>'run_id')::uuid\n     for update;", LEASES,
       "a lease token relabelled with another provider still writes"),
    _s("d7_finished_attempt_renewed", "  if not found or a.state <> 'leased' then",
       "  if not found then", LEASES, "a finished attempt is heartbeated back to life"),
    _s("d7_worker_unfenced", "  if a.worker_id is distinct from p_lease->>'worker_id' then",
       "  if false then", LEASES, "another worker writes the attempt it does not hold"),
    _s("d7_expiry_unfenced", "  if infrx.now() >= a.expires_at then", "  if false then", LEASES,
       "an expired lease writes after its case was handed to another worker"),
    _s("d7_heartbeat_does_not_renew", "  update infrx.lab_eval_attempts set expires_at = "
       "infrx.now() + v_ttl", "  update infrx.lab_eval_attempts set expires_at = expires_at",
       LEASES, "a live worker's long case is reaped under it"),
    _s("d7_recover_live_leases", "              where x.state = 'leased' and x.expires_at <= "
       "infrx.now()", "              where x.state = 'leased'", LEASES,
       "the reaper takes cases from workers still inside their lease"),
    _s("d7_recover_strands_the_case", "  update infrx.lab_eval_cases c set state = 'pending'\n",
       "  update infrx.lab_eval_cases c set state = 'leased'\n", LEASES,
       "an expired case is never offered again"),
    _s("d7_replay_ignores_the_outcome", "    if a.finish_digest <> v_digest then",
       "    if false then", LEASES, "a second, different outcome is acknowledged as the first"),
    _s("d7_no_finish_replay", "  if a.finish_digest is not null then", "  if false then",
       LEASES, "a finish retried after a lost answer is refused as stale"),
    _s("d7_replay_any_worker",
       " and x.worker_id = v_lease->>'worker_id'\n     and r.provider_org_id",
       "\n     and r.provider_org_id", LEASES,
       "another worker presenting a finished lease reads the attempt it never held"),
    _s("d7_replay_any_provider",
       "\n     and r.provider_org_id = (v_lease->>'provider_org_id')::uuid;\n  if a.finish_digest",
       ";\n  if a.finish_digest", LEASES,
       "another provider presenting a finished lease reads this provider's attempt"),
    _s("d7_failed_with_results", "\n     or (v_outcome = 'failed' and jsonb_array_length("
       "v_results) > 0)\n", "\n", LEASES, "a failed attempt records a scored result"),
    _s("d7_evaluator_of_any_provider", "                                 where e.ref = "
       "x->>'evaluator_ref'\n                                   and e.provider_org_id = "
       "(v_lease->>'provider_org_id')::uuid))", "                                 where e.ref = "
       "x->>'evaluator_ref'))", LEASES,
       "a result is attributed to another provider's evaluator"),
    _s("d7_results_per_attempt", "  primary key (run_id, case_id, evaluator_ref),\n", "", LEASES,
       "one case holds two results from one evaluator"),
    _s("d7_results_editable", "'lab_dataset_samples',\n                           "
       "'lab_eval_results'] loop", "'lab_dataset_samples'] loop", LEASES,
       "a recorded result is rewritten after the fact"),
    _s("d7_cost_dropped", "      cost_unit = p_args->'cost'->>'unit', cost_value = "
       "(p_args->'cost'->>'value')::numeric,", "      cost_unit = null, cost_value = null,",
       LEASES, "an attempt's spend is invisible"),
    # --- R161 machines
    _s("d7_extra_transition", "    ('run', 'running', 'cancelled'),",
       "    ('run', 'running', 'cancelled'), ('run', 'succeeded', 'cancelled'),", STATES,
       "a finished run is cancelled and its evidence withdrawn"),
    _s("d7_runs_unguarded", "  foreach t in array array['lab_eval_runs:run', ",
       "  foreach t in array array[", STATES, "a run moves between any two states"),
    _s("d7_run_never_succeeds", "      state = case when exists (select 1 from "
       "infrx.lab_eval_cases c", "      state = case when false and exists (select 1 from "
       "infrx.lab_eval_cases c", STATES, "a run whose cases were evaluated ends failed"),
    _s("d7_cancelled_run_leases", "    perform infrx.refuse('already_terminal', 'run ' || "
       "r.run_id || ' is ' || r.state);", "    null;", STATES,
       "a cancelled run keeps handing out cases"),
    _s("d7_cancelled_run_fenced_live", "    perform infrx.refuse('stale_lease', 'run ' || "
       "r.run_id || ' is ' || r.state);", "    null;", STATES,
       "a worker keeps writing results into a cancelled run"),
    # --- duplicate callback
    _s("d7_redelivery_queues_again", "      perform infrx.refuse('idempotency_conflict', 'the "
       "checkpoint id names another artifact');\n    end if;\n    return "
       "infrx.lab_receipt_json(c);", "      perform infrx.refuse('idempotency_conflict', 'the "
       "checkpoint id names another artifact');\n    end if;", CHECKPOINT,
       "a redelivered checkpoint is evaluated twice"),
    _s("d7_redelivery_other_artifact", "       (v_provider, p_args->>'external_run_ref', "
       "p_args->>'artifact_digest') then", "       (c.provider_org_id, c.external_run_ref, "
       "c.artifact_digest) then", CHECKPOINT, "a different artifact under a received id is "
       "acknowledged as received"),
    _s("d7_checkpoint_foreign_run", "                 and provider_org_id = v_provider and kind "
       "= 'external_run') then", "                 and kind = 'external_run') then", CHECKPOINT,
       "a checkpoint is received into another provider's external run"),
    _s("d7_checkpoint_any_provider", "     and provider_org_id = (p_args->>'provider_org_id')"
       "::uuid\n  returning * into c;", "\n  returning * into c;", CHECKPOINT,
       "a provider moves another provider's checkpoint"),
    _s("d7_checkpoints_unguarded", "'lab_eval_attempts:attempt', "
       "'lab_checkpoint_receipts:checkpoint'] loop", "'lab_eval_attempts:attempt'] loop",
       CHECKPOINT,
       "a received checkpoint is marked evaluated without validation"),
    _s("d7_malformed_checkpoint_raw_error", "  exception when check_violation or not_null_violation"
       " or invalid_text_representation then\n    perform infrx.refuse('invalid_request', 'a "
       "checkpoint", "  exception when raise_exception then\n    perform infrx.refuse("
       "'invalid_request', 'a checkpoint", CHECKPOINT,
       "a malformed callback digest is a server error instead of a 400"),
    # --- outbox
    _s("d7_ack_by_anyone", "     and claimed_by = p_args->>'worker_id' and acknowledged_at is "
       "null;", "     and acknowledged_at is null;", RELAY,
       "a dead relay's late ack marks events another relay has not indexed yet"),
    # d7_redelivered_early/d7_acknowledged_redelivered: `lab_outbox_pending`'s body moved to
    # 0050 in full (E3L-F2/PROPOSE_FILE's reasoning: a `create or replace` overwrites whatever
    # 0029's text says, so a mutant of the OLD file would be silently masked and never run).
    _s("d7_redelivered_early", "       and (o.claimed_at is null or o.claimed_at\n",
       "       and (o.claimed_at is null or true or o.claimed_at\n", RELAY,
       "every pump re-sends events another relay is still working on", file=KINDS_FILE),
    _s("d7_acknowledged_redelivered", "     where o.acknowledged_at is null and o.available_at "
       "<= infrx.now()", "     where o.available_at <= infrx.now()", RELAY,
       "acknowledged events are delivered forever", file=KINDS_FILE),
    # --- WR-LSQ-C2B: a role's relay claims only its own kinds, `0050_lab_outbox_pending_kinds.sql`
    _s("d7c2b_kinds_ignored", "       and (p_args->'kinds' is null or "
       "jsonb_array_length(p_args->'kinds') = 0\n            or o.kind = "
       "any(array(select jsonb_array_elements_text(p_args->'kinds'))))\n", "", KINDS,
       "an eval role's relay claims, times out on and redelivers a checkpoints-role event",
       file=KINDS_FILE),
    # --- WR-N4-3: a durable import-job queue, `0051_lab_import_jobs.sql` -------------------
    _s("n4j_enqueue_any_provider", "    if j.provider_org_id <> v_provider then\n      "
       "perform infrx.refuse('not_found', 'no such import job for this provider');\n    "
       "end if;\n", "", JOBS, "a replay from another provider reads someone else's import job",
       file=JOBS_FILE),
    _s("n4j_enqueue_not_idempotent", "  select * into j from infrx.lab_import_jobs where "
       "job_id = v_id;\n  if found then\n", "  select * into j from infrx.lab_import_jobs "
       "where job_id = v_id;\n  if false then\n", JOBS,
       "a replayed enqueue opens a second job of the same id", file=JOBS_FILE),
    _s("n4j_claim_ignores_lease_timeout", "        or (j.state = 'running' and j.updated_at\n"
       "            <= infrx.now() - make_interval(secs => (p_args->>'redelivery_s')"
       "::float8))\n", "", JOBS, "a dead I5 worker's job is never redelivered", file=JOBS_FILE),
    _s("n4j_heartbeat_any_worker", "     and claimed_by = p_args->>'worker_id'\n", "\n", JOBS,
       "a worker that never claimed the job extends its lease", file=JOBS_FILE),
    _s("n4j_finish_ignores_holder", "  if j.state <> 'running' or j.claimed_by <> "
       "p_args->>'worker_id' then\n", "  if false then\n", JOBS,
       "a worker that does not hold the job finishes it anyway", file=JOBS_FILE),
    _s("n4j_finish_not_idempotent", "  if j.state = v_to then\n    return "
       "infrx.lab_import_job_json(j);                 -- a retried ack: already there\n  "
       "end if;\n", "", JOBS, "a retried finish ack is refused instead of answered",
       file=JOBS_FILE),
    _s("n4j_read_any_provider", "     and provider_org_id = (p_args->>'provider_org_id')"
       "::uuid;", "  ;", JOBS, "a provider reads another provider's import job",
       file=JOBS_FILE),
    # --- D7.c plans
    _s("d7_no_pending_index", "create index if not exists lab_eval_cases_pending\n  on "
       "infrx.lab_eval_cases (run_id, case_id) where state = 'pending';\n", "", PLANS,
       "leasing the next case walks every finished case of the run"),
    _s("d7_no_expiry_index", "create index if not exists lab_eval_attempts_expiry\n  on "
       "infrx.lab_eval_attempts (expires_at) where state = 'leased';\n", "", PLANS,
       "the reaper scans every attempt ever made"),
    _s("d7_no_outbox_index", "create index if not exists lab_outbox_pending\n  on "
       "infrx.lab_outbox (available_at) where acknowledged_at is null;\n", "", PLANS,
       "every pump scans every event ever written"),
)
#: 0034 redefines these three 0029 bodies (F7 register_source/receive_checkpoint; F4,
#: RSI-3 and WR-B-2 finish_attempt), so their mutants live in 0034 (D5 item 10b).
FOLLOWUP_FILE = "0034_lab_eval_followup.sql"
MOVED = {"d7_finish_digest_unstable", "d7_source_id_redefinable", "d7_unknown_grant_is_forbidden",
         "d7_malformed_source_raw_error", "d7_source_under_revoked_grant",
         "d7_replay_ignores_the_outcome", "d7_no_finish_replay", "d7_replay_any_worker",
         "d7_replay_any_provider", "d7_failed_with_results", "d7_evaluator_of_any_provider",
         "d7_cost_dropped", "d7_run_never_succeeds", "d7_redelivery_queues_again",
         "d7_redelivery_other_artifact", "d7_checkpoint_foreign_run",
         "d7_malformed_checkpoint_raw_error"}
SQL_MUTANTS = tuple(dataclasses.replace(m, file=FOLLOWUP_FILE) if m.name in MOVED else m
                    for m in SQL_MUTANTS)
SQL_NAMES = tuple(m.name for m in SQL_MUTANTS)

# ------------------------------------------------------------------- the 0034 follow-up
DB_F = f"{pgharness.DATABASE}_d7fmut"
ROLES_F = "check_browser_roles_reach_nothing"
REAPER = "check_the_reaper_skips_a_locked_attempt"
ALL_FAILED = "check_a_run_whose_every_case_failed_fails"
SOURCELESS = "check_a_sample_without_its_source_is_refused"
MID_RUN = "check_a_revocation_stops_leasing_mid_run"
IDS = "check_source_and_checkpoint_ids_are_per_provider"
UNITS = "check_costs_are_in_a_unit_of_the_runs_budgets"
ERRORS = "check_a_failed_attempt_keeps_its_error_code"
EVALUATORS = "check_evaluators_are_registered_specs_of_their_provider"
RESULTS = "check_the_results_read_is_the_runs_own"
RELEASE = "check_a_released_lease_consumes_no_attempt"
REPORTS = "check_reports_are_write_once_by_digest"
USES = "check_dataset_uses_are_the_captured_grant_scope"
STORE = "check_the_store_composes"


def _f(name, old, new, check, why, file=FOLLOWUP_FILE, **kw):
    return _d.Mutant(name, file, old, new, "lab", check, why, **kw)


FOLLOWUP = (
    _f("d7f_service_writes", "execute format('grant select on infrx.%I to service_role', t);",
       "execute format('grant select, delete on infrx.%I to service_role', t);", ROLES_F,
       "the platform role deletes evaluators and reports around the RPCs"),
    _f("d7f_row_security_off", "execute format('alter table infrx.%I enable row level security'",
       "execute format('alter table infrx.%I disable row level security'", ROLES_F,
       "a future browser grant exposes every provider's reports"),
    _f("d7f_evaluators_editable", "'create or replace trigger %I before update or delete on "
       "infrx.%I '", "'create or replace trigger %I before delete on infrx.%I '", ROLES_F,
       "a registered evaluator spec is edited under its ref"),
    _f("d7f_reaper_waits", "              for update skip locked)", "              for update)",
       REAPER, "the reaper blocks behind an in-flight finish while holding cases",
       file=FILE),
    _f("d7f_all_failed_succeeds", "and c.state = 'done')", "and c.state in ('done', 'failed'))",
       ALL_FAILED, "a run in which nothing was evaluated reports success"),
    _f("d7f_sourceless_sample_published", "        where jsonb_typeof(s->'source_ref') is "
       "distinct from 'string') then", "        where s->'source_ref' is null) then", SOURCELESS,
       "a manifest is published short of a sample whose source is not a string"),
    _f("d7f_no_shape_trigger", "create or replace trigger lab_records_shape before insert on "
       "infrx.lab_records\n  for each row execute function infrx.lab_records_shape();\n", "",
       SOURCELESS, "a manifest is published short of its sourceless samples"),
    _f("d7f_results_ignore_revocation", "                    and infrx.lab_grant_current("
       "s.grant_id, 'provider_sharing')) then", "                    ) then", MID_RUN,
       "revoked content keeps producing results until the run ends"),
    _f("d7f_result_rights_off", "  for each row when (new.state = 'succeeded') execute function "
       "infrx.lab_result_rights();", "  for each row when (false) execute function "
       "infrx.lab_result_rights();", MID_RUN, "a revocation mid-run changes nothing"),
    _f("d7f_rights_refuse_leases", "create or replace trigger lab_eval_attempts_rights before "
       "update on infrx.lab_eval_attempts\n  for each row when (new.state = 'succeeded')",
       "create or replace trigger lab_eval_attempts_rights before insert or update on "
       "infrx.lab_eval_attempts\n  for each row when (new.state in ('leased', 'succeeded'))",
       MID_RUN, "a revoked case can never be leased to end it, so the run never finishes"),
    _f("d7f_source_replay_any_provider", "  select * into s from infrx.lab_sources where "
       "provider_org_id = v_provider\n     and source_id = v_source;", "  select * into s from "
       "infrx.lab_sources where source_id = v_source limit 1;", IDS,
       "a provider's source id collides with (and reveals) another provider's source"),
    _f("d7f_checkpoint_ids_global", "         and conrelid = 'infrx.lab_checkpoint_receipts'"
       "::regclass) = 1 then", "         and conrelid = 'infrx.lab_checkpoint_receipts'"
       "::regclass) = 2 then", IDS, "checkpoint ids stay global: the first provider blocks "
       "every other"),
    _f("d7f_cost_any_unit", "        where r.run_id = a.run_id and b->'limit'->>'unit' = "
       "p_args->'cost'->>'unit') then", "        where r.run_id = a.run_id) then", UNITS,
       "a PROVIDER_USD cost is summed into a CREDIT-budgeted run"),
    _f("d7f_error_dropped", "      finish_digest = v_digest, error_code = p_args->>'error'",
       "      finish_digest = v_digest, error_code = null", ERRORS,
       "why an attempt failed is lost"),
    _f("d7f_error_on_success", "\n     or (v_outcome = 'succeeded' and p_args->>'error' is not "
       "null) then", " then", ERRORS, "a successful attempt records a failure code"),
    _f("d7f_error_not_in_digest", "\n    || jsonb_strip_nulls(jsonb_build_object('error', "
       "p_args->'error'))", "", ERRORS, "a retried finish silently keeps another error"),
    _f("d7f_error_unchecked", "  check (error_code ~ '^[a-z][a-z0-9_:.-]{0,99}$');",
       "  check (true);", ERRORS, "free text (a prompt, a secret) is stored as an error code"),
    _f("d7f_evaluator_not_content_addressed", "  constraint lab_evaluators_content_addressed "
       "check (ref = 'lab:evaluator:'", "  constraint lab_evaluators_content_addressed check "
       "(true or ref = 'lab:evaluator:'", EVALUATORS,
       "a stored evaluator ref names bytes other than its spec"),
    _f("d7f_run_evaluator_unchecked", "  if new.kind = 'run' and not exists (",
       "  if false and not exists (", EVALUATORS, "a run names an evaluator nobody registered"),
    _f("d7f_result_evaluator_unregistered", "  if exists (select 1 from "
       "jsonb_array_elements(v_results) x\n              where not exists (select 1 from "
       "infrx.lab_evaluators e", "  if false and exists (select 1 from "
       "jsonb_array_elements(v_results) x\n              where not exists (select 1 from "
       "infrx.lab_evaluators e", EVALUATORS, "a result is filed under an unregistered evaluator"),
    _f("d7f_evaluator_read_any_provider", "\n     and provider_org_id = (p_args->>"
       "'provider_org_id')::uuid;\n  if not found then\n    perform infrx.refuse('not_found', "
       "'no such evaluator", ";\n  if not found then\n    perform infrx.refuse('not_found', "
       "'no such evaluator", EVALUATORS, "a provider reads another provider's evaluator spec"),
    _f("d7f_evaluator_not_json", "    perform (p_args->>'body')::jsonb;\n", "", EVALUATORS,
       "a spec a worker cannot parse is registered"),
    _f("d7f_results_any_provider", "  if not exists (select 1 from infrx.lab_eval_runs where "
       "run_id = v_run\n                 and provider_org_id", "  if not exists (select 1 from "
       "infrx.lab_eval_runs where run_id = v_run\n                 or provider_org_id", RESULTS,
       "a provider reads another provider's results"),
    _f("d7f_results_cost_dropped", "          jsonb_build_object('unit', a.cost_unit, 'value', "
       "a.cost_value::text) end)", "          null end)", RESULTS,
       "B2 compares runs without their costs"),
    _f("d7f_results_no_error", "        'error', a.error_code, 'cost',", "        'error', null, "
       "'cost',", RESULTS, "B2 cannot tell errors from misses"),
    _f("d7f_release_consumes", "  update infrx.lab_eval_cases set state = 'pending', attempts = "
       "attempts - 1", "  update infrx.lab_eval_cases set state = 'pending', attempts = attempts",
       RELEASE, "a 402 counts toward max_attempts"),
    _f("d7f_release_unfenced", "  a infrx.lab_eval_attempts%rowtype := infrx.lab_fence("
       "p_args->'lease');", "  a infrx.lab_eval_attempts%rowtype := (select x from "
       "infrx.lab_eval_attempts x where x.run_id = (p_args->'lease'->>'run_id')::uuid and "
       "x.case_id = (p_args->'lease'->>'case_id')::uuid and x.attempt = (p_args->'lease'->>"
       "'attempt')::int);", RELEASE, "another worker's lease is given back under it"),
    _f("d7f_release_keeps_row", "  delete from infrx.lab_eval_attempts\n   where (run_id, "
       "case_id, attempt) = (a.run_id, a.case_id, a.attempt);\n", "", RELEASE,
       "a released case can never be leased again"),
    _f("d7f_report_digest_not_content", "  v_digest text := 'sha256:' || encode(sha256("
       "convert_to(p_args->>'body', 'UTF8')), 'hex');", "  v_digest text := 'sha256:' || "
       "encode(sha256(convert_to(p_args->>'body' || ' ', 'UTF8')), 'hex');", REPORTS,
       "a report is stored under a digest that is not its content's"),
    _f("d7f_report_not_content_addressed", "  constraint lab_eval_reports_content_addressed\n"
       "    check (report_digest =", "  constraint lab_eval_reports_content_addressed\n    check "
       "(true or report_digest =", REPORTS, "a report row is forged under any digest"),
    _f("d7f_report_any_provider_runs", "       and r.provider_org_id = v_provider\n       and "
       "r.ref in", "\n       and r.ref in", REPORTS,
       "a provider files a decision about another provider's runs"),
    _f("d7f_report_any_schema", "  if v_doc->>'schema' is distinct from 'infrx.eval_report.1' "
       "then", "  if false then", REPORTS, "any JSON is stored as an evaluation decision"),
    _f("d7f_report_read_any_provider", "  select * into e from infrx.lab_eval_reports where "
       "report_digest = p_args->>'report_digest'\n     and provider_org_id = (p_args->>"
       "'provider_org_id')::uuid;", "  select * into e from infrx.lab_eval_reports where "
       "report_digest = p_args->>'report_digest';", REPORTS,
       "a provider reads another provider's decision"),
    _f("d7f_report_replay_fails", "    on conflict (report_digest) do nothing;", "    ;",
       REPORTS, "storing a report again (a retry) fails"),
    _f("d7f_uses_any_provider", "\n             and r.provider_org_id = (p_args->>"
       "'provider_org_id')::uuid) x", ") x", USES, "a provider learns another's data sources"),
    _f("d7f_uses_current_version", "              on (g.grant_id, g.version) = (s.grant_id, "
       "s.grant_version)", "              on g.grant_id = s.grant_id", USES,
       "a dataset claims categories its data was never captured under"),
    _f("d7f_uses_wrong_category", "'model_id', m, 'category', c) u",
       "'model_id', m, 'category', 'feedback') u", STORE,
       "H1's gate checks a category the data does not have"),
)
FOLLOWUP_NAMES = tuple(m.name for m in FOLLOWUP)

RUNNER = Runner(name="d7", targets=("tests/d/test_d7_units.py",))
F = "state/lab_data.py"
GRANT = "test_grant_ref__names_the_recipient_and_the_version"
PUB = "test_publish__sends_the_canonical_bytes_of_the_callers_own_record"
RESOLVE = "test_resolve__is_the_parsed_record_and_a_refusal_is_typed"
CALLS = "test_calls__carry_the_callers_provider_the_lease_and_the_cost"
OUTBOX = "test_outbox__is_the_relays_store_half_with_the_claimant_on_every_ack"
IMPORT_JOBS = "test_import_jobs__is_a_durable_lease_queue_scoped_to_its_provider"
FOLLOW = "test_followup__error_release_results_evaluators_reports_and_uses"
VARIANT_UNITS = "test_variant__a_comparison_is_sent_as_its_canonical_bytes_and_read_back_whole"


def _p(name, invariant, old, new, *cases, **kw) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=F, old=old, new=new, cases=cases, **kw)


CODE_MUTANTS = (
    _p("d7_py_grant_ref_of_the_grantor", "a grant ref names its recipient provider",
       'return f"lab:grant:{grant.recipient_provider_org_id}:', 'return f"lab:grant:'
       '{grant.grantor_org_id}:', GRANT),
    _p("d7_py_grant_ref_without_version", "a grant ref pins one version",
       '{"grant_id": grant.grant_id, "version": grant.version}', '{"grant_id": grant.grant_id}',
       GRANT),
    _p("d7_py_publish_for_any_provider", "a provider publishes only its own records",
       "        if records.parse(payload).provider_org_id != provider_org_id:",
       "        if records.parse(payload).provider_org_id is None:", PUB),
    _p("d7_py_publish_unvalidated", "the contract refuses before the database",
       "        if records.parse(payload).provider_org_id != provider_org_id:",
       "        if payload['provider_org_id'] != provider_org_id:", PUB),
    _p("d7_py_publish_python_json", "the stored bytes are RFC 8785",
       '"body": records.canonical(payload).decode()}', '"body": json.dumps(payload)}', PUB),
    _p("d7_py_resolve_raw_row", "a resolved ref is the parsed record",
       '        return records.parse(json.loads(row["body"]))', "        return row", RESOLVE),
    _p("d7_py_untyped_refusal", "a SQL refusal is its typed error",
       "function, args, error=domain_error)", "function, args, error=None)", RESOLVE),
    _p("d7_py_resolve_unscoped", "resolve is provider-scoped",
       '{"provider_org_id": provider_org_id, "ref": ref}', '{"ref": ref}', RESOLVE),
    _p("d7_py_source_without_grant", "a source names its grant",
       '"content_digest": content_digest, "grant_ref": grant_ref, "actor": actor}',
       '"content_digest": content_digest, "actor": actor}', CALLS),
    _p("d7_py_access_without_purpose", "access is per purpose",
       '"dataset_ref": dataset_ref, "purpose": purpose}', '"dataset_ref": dataset_ref}', CALLS),
    _p("d7_py_lease_unscoped", "a lease is the caller's provider's",
       '"provider_org_id": provider_org_id, "run_id": run_id, "worker_id": worker_id,',
       '"run_id": run_id, "worker_id": worker_id,', CALLS),
    _p("d7_py_finish_drops_cost", "an attempt's cost is recorded",
       '"results": results, "cost": cost}', '"results": results}', CALLS),
    _p("d7_py_recover_raw", "recover answers the count",
       '        return (await self._call("lab_recover", {}))["expired"]',
       '        return await self._call("lab_recover", {})', CALLS),
    _p("d7_py_checkpoint_unscoped", "a checkpoint move is the caller's provider's",
       '"provider_org_id": provider_org_id, "checkpoint_id": checkpoint_id,\n            '
       '"state": state}', '"checkpoint_id": checkpoint_id,\n            "state": state}', CALLS),
    _p("d7_py_pending_without_claimant", "a claim names its relay",
       '"limit": limit, "worker_id": worker_id, "redelivery_s": redelivery_s,',
       '"limit": limit, "redelivery_s": redelivery_s,', OUTBOX),
    _p("d7_py_pending_ignores_role_kinds", "WR-LSQ-C2B: a claim narrows to the role's own "
       "kinds", '            "kinds": list(kinds)})]',
       '            "kinds": []})]', OUTBOX),
    _p("d7_py_ack_without_claimant", "an ack lands only for the claimant",
       '{"event_ids": list(event_ids),\n                                                   '
       '"worker_id": worker_id}', '{"event_ids": list(event_ids)}', OUTBOX),
    _p("d7_py_events_as_dicts", "the relay reads event.event_id",
       "        return [LabEvent(**row) for row in", "        return [row for row in", OUTBOX),
    # --- the 0034 follow-up
    _p("d7_py_error_dropped", "a failed attempt's error is sent",
       '            args["error"] = error', "            pass", FOLLOW),
    _p("d7_py_error_always_sent", "no error key without an error (a stable finish digest)",
       "        if error is not None:", "        if True:", FOLLOW),
    _p("d7_py_release_without_lease", "a release names its lease",
       '"lab_release_attempt", {"lease": lease})', '"lab_release_attempt", {})', FOLLOW),
    _p("d7_py_results_unscoped", "a results read is the caller's provider's",
       '"lab_run_results", {"provider_org_id": provider_org_id,\n'
       '                                                    "run_id": run_id})',
       '"lab_run_results", {"run_id": run_id})', FOLLOW),
    _p("d7_py_evaluator_python_json", "an evaluator is its RFC 8785 bytes",
       '"body": records.canonical(spec).decode()}', '"body": json.dumps(spec)}', FOLLOW),
    _p("d7_py_evaluator_raw_row", "an evaluator reads back as its spec",
       "        return json.loads(row[\"body\"])\n", "        return row\n", FOLLOW),
    _p("d7_py_report_digest_unchecked", "a forged report digest never reaches the database",
       '        if report.get("report_digest", digest) != digest:', "        if False:", FOLLOW),
    _p("d7_py_report_digest_in_body", "the digest is of the report without itself",
       '{k: v for k, v in report.items() if k != "report_digest"}', "dict(report)", FOLLOW),
    _p("d7_py_report_read_drops_digest", "a report reads back with its digest",
       '        return {**json.loads(row["body"]), "report_digest": row["report_digest"]}',
       '        return json.loads(row["body"])', FOLLOW),
    _p("d7_py_uses_raw", "uses are the port's DatasetUse records",
       "        return tuple(DatasetUse.model_validate(row) for row in",
       "        return tuple(row for row in", FOLLOW),
    _p("d7_py_uses_unscoped", "uses are of the caller's own dataset",
       '"lab_dataset_uses", {"provider_org_id": provider_org_id, "dataset_ref": dataset_ref}',
       '"lab_dataset_uses", {"dataset_ref": dataset_ref}', FOLLOW),
    _p("d7_py_variant_python_json", "a comparison is stored as its RFC 8785 bytes",
       '"body": records.canonical(comparison).decode()}))["comparison_digest"]',
       '"body": json.dumps(comparison)}))["comparison_digest"]', VARIANT_UNITS),
    _p("d7_py_variant_read_raw", "comparisons read back as their documents",
       '        return [json.loads(row["body"]) for row in await self._call(',
       '        return [row for row in await self._call(', VARIANT_UNITS),
    _p("d7_py_variant_read_unscoped", "comparisons are the caller's provider's",
       '            "lab_variant_comparisons", {"provider_org_id": provider_org_id,\n'
       '                                        "report_digest": report_digest})]',
       '            "lab_variant_comparisons", {"report_digest": report_digest})]',
       VARIANT_UNITS),
    _p("d7_py_import_finish_unscoped", "WR-N4-3: a finish names the worker holding the lease",
       '"job_id": job_id, "worker_id": worker_id, "state": state, "result": result,',
       '"job_id": job_id, "state": state, "result": result,', IMPORT_JOBS),
)


def kill(mutant, db: str = DB, world=t) -> tuple[str, str]:
    """migration_mutants.kill's classification on this lane's database and a world module's
    seed and CHECKS (D7's by default; the later lab-sql lists pass their own)."""
    pgharness.ensure()
    with TemporaryDirectory(prefix=f"infrx-dlab-{mutant.name}-") as tmp:
        directory = Path(tmp)
        refused = _d._mutate(directory, mutant)
        if refused is not None:
            return _d.MISDECLARED, refused
        try:
            pgharness.recreate(db)
            pgharness.apply(db, migrations.sql_for(shim=pgharness.NEEDS_SHIM,
                                                   directory=directory))
        except (AssertionError, psycopg.Error) as broken:
            return _d.APPLY_ERROR, _d._first_line(broken)
        try:
            with pgharness.connect(db) as conn:
                world.seed(conn)
                return _d._run(world.CHECKS[mutant.check], conn)
        except (AssertionError, psycopg.Error) as during_setup:
            return _d.SETUP_ERROR, _d._first_line(during_setup)


def kill_followup(mutant) -> tuple[str, str]:
    return kill(mutant, DB_F, followup)


def run_code_mutant(mutant):
    return shared.run_mutant(mutant, RUNNER)


# --- WR-R3-2: variant comparisons, `0040_lab_variant_comparisons.sql` (test_d7_variant) ----
VARIANT_FILE = "0040_lab_variant_comparisons.sql"
DB_V = f"{pgharness.DATABASE}_d7vmut"
V_ROLES = "check_browser_roles_reach_nothing"
V_STORED = "check_a_comparison_is_stored_once_beside_its_report"
V_LINEAGE = "check_a_comparison_rests_on_the_providers_variant_and_report"
V_STORE = "check_the_store_composes"


def _v(name, old, new, check, why, **kw):
    return _d.Mutant(name, VARIANT_FILE, old, new, "lab", check, why, **kw)


VARIANT = (
    _v("r3_service_edits", "grant select on infrx.lab_variant_comparisons to service_role;",
       "grant select, delete on infrx.lab_variant_comparisons to service_role;", V_ROLES,
       "the platform role deletes the evidence an optimization claim rests on"),
    _v("r3_rows_mutable", "create or replace trigger lab_variant_comparisons_immutable before "
       "update or delete", "create or replace trigger lab_variant_comparisons_immutable before "
       "delete", V_STORED, "a stored comparison is rewritten after the fact"),
    _v("r3_not_content_addressed", "  constraint lab_variant_comparisons_content_addressed\n"
       "    check (comparison_digest = 'sha256:' || encode(sha256(convert_to(body, 'UTF8')), "
       "'hex')),\n", "", V_STORED, "a row claims a digest its bytes do not have"),
    _v("r3_replay_second_row", "    on conflict (comparison_digest) do nothing;", ";", V_STORED,
       "storing the same comparison again is a 500"),
    _v("r3_read_any_provider", "     and c.provider_org_id = (p_args->>'provider_org_id')::uuid",
       "", V_STORED, "a provider reads another's comparisons"),
    _v("r3_any_variant", "  if not exists (select 1 from infrx.lab_records r where r.ref = "
       "v_doc->>'variant_ref'\n                  and r.kind = 'variant' and r.provider_org_id = "
       "v_provider)\n     or", "  if false\n     or", V_LINEAGE,
       "a comparison names another provider's (or no) variant"),
    _v("r3_any_record_is_a_variant", "                  and r.kind = 'variant' and "
       "r.provider_org_id = v_provider)", "                  and r.provider_org_id = "
       "v_provider)", V_LINEAGE, "a run record is taken for the variant"),
    _v("r3_any_report", "     or not exists (select 1 from infrx.lab_eval_reports e\n"
       "                     where e.report_digest = v_doc->>'report_digest'\n"
       "                       and e.provider_org_id = v_provider) then", " then", V_LINEAGE,
       "a comparison rests on another provider's report (or a raw FK error)"),
    _v("r3_claim_unproven", "  constraint lab_variant_comparisons_claim_is_equivalence\n"
       "    check (not optimization_claimed or outcome = 'equivalent')\n", "  constraint "
       "lab_variant_comparisons_claim_is_equivalence check (true)\n", V_LINEAGE,
       "an inconclusive comparison claims an optimization"),
    _v("r3_any_schema", "  if v_doc->>'schema' is distinct from 'infrx.variant_comparison.1' "
       "then", "  if false then", V_LINEAGE, "any JSON is stored as a comparison"),
    _v("r3_read_empty", "   where c.report_digest = p_args->>'report_digest'\n",
       "   where false and c.report_digest = p_args->>'report_digest'\n", V_STORE,
       "the stored comparison never reads back"),
)
VARIANT_NAMES = tuple(m.name for m in VARIANT)


def kill_variant(mutant) -> tuple[str, str]:
    from . import test_d7_variant as variant_world
    return kill(mutant, DB_V, variant_world)
