"""R32/R40 for D7: single-edit defects of `0029_lab_data.sql` (killed by the named check of
`test_d7_lab_data.py` on a database built from the mutated set, needs Docker) and of
`infrx/state/lab_data.py` (killed by the named case of `test_d7_units.py` through the shared
runner, no Docker). The SQL runner is L2-SQL's (`code_mutants_l2sql.kill`'s classification)
with this lane's database and seed.

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_code_mutants_d7.py
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import psycopg
from infrx.state import migrations

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Runner
from . import migration_mutants as _d
from . import pgharness
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
RACE = "check_two_publishers_race_to_one_version"
KILL = "check_a_kill_around_commit_recovers_once"
PLANS = "check_large_fixture_queries_use_their_indexes"


def _s(name, old, new, check, why, **kw):
    return _d.Mutant(name, FILE, old, new, "lab", check, why, **kw)


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
    _s("d7_finish_digest_unstable", "'results', v_results, 'cost', p_args->'cost')::text",
       "'results', v_results, 'cost', p_args->'cost', 'at', clock_timestamp())::text", KILL,
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
       "delete restrict\n", "  foreign key (source_id) references infrx.lab_sources on delete "
       "restrict\n", REFS, "a sample claims a grant its content was not captured under"),
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
       "v_results) > 0) then", " then", LEASES, "a failed attempt records a scored result"),
    _s("d7_evaluator_of_any_provider", "\n                 or (infrx.lab_ref_parts(x->>"
       "'evaluator_ref'))[2]\n                    is distinct from v_lease->>'provider_org_id') "
       "then", ") then", LEASES, "a result is attributed to another provider's evaluator"),
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
    _s("d7_run_never_succeeds", "    update infrx.lab_eval_runs set state = 'succeeded', "
       "updated_at = infrx.now()", "    update infrx.lab_eval_runs set state = state, "
       "updated_at = infrx.now()", STATES, "a run whose cases all finished stays running"),
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
    _s("d7_redelivered_early", "       and (o.claimed_at is null or o.claimed_at\n",
       "       and (o.claimed_at is null or true or o.claimed_at\n", RELAY,
       "every pump re-sends events another relay is still working on"),
    _s("d7_acknowledged_redelivered", "     where o.acknowledged_at is null and o.available_at "
       "<= infrx.now()", "     where o.available_at <= infrx.now()", RELAY,
       "acknowledged events are delivered forever"),
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
SQL_NAMES = tuple(m.name for m in SQL_MUTANTS)

RUNNER = Runner(name="d7", targets=("tests/d/test_d7_units.py",))
F = "state/lab_data.py"
GRANT = "test_grant_ref__names_the_recipient_and_the_version"
PUB = "test_publish__sends_the_canonical_bytes_of_the_callers_own_record"
RESOLVE = "test_resolve__is_the_parsed_record_and_a_refusal_is_typed"
CALLS = "test_calls__carry_the_callers_provider_the_lease_and_the_cost"
OUTBOX = "test_outbox__is_the_relays_store_half_with_the_claimant_on_every_ack"


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
       "            raise domain_error(failed) from None", "            raise", RESOLVE),
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
       '"results": results, "cost": cost})', '"results": results})', CALLS),
    _p("d7_py_recover_raw", "recover answers the count",
       '        return (await self._call("lab_recover", {}))["expired"]',
       '        return await self._call("lab_recover", {})', CALLS),
    _p("d7_py_checkpoint_unscoped", "a checkpoint move is the caller's provider's",
       '"provider_org_id": provider_org_id, "checkpoint_id": checkpoint_id,\n            '
       '"state": state}', '"checkpoint_id": checkpoint_id,\n            "state": state}', CALLS),
    _p("d7_py_pending_without_claimant", "a claim names its relay",
       '"limit": limit, "worker_id": worker_id, "redelivery_s": redelivery_s}',
       '"limit": limit, "redelivery_s": redelivery_s}', OUTBOX),
    _p("d7_py_ack_without_claimant", "an ack lands only for the claimant",
       '{"event_ids": list(event_ids),\n                                                   '
       '"worker_id": worker_id}', '{"event_ids": list(event_ids)}', OUTBOX),
    _p("d7_py_events_as_dicts", "the relay reads event.event_id",
       "        return [LabEvent(**row) for row in", "        return [row for row in", OUTBOX),
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


def run_code_mutant(mutant):
    return shared.run_mutant(mutant, RUNNER)
