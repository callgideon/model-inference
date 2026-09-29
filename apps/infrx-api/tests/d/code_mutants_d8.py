"""R32/R40 for D8: single-edit defects of `0042_lab_d8_ledgers.sql` (killed by the named
check of `test_d8_ledgers.py`) and `0043_lab_reads_and_proposals.sql` (the remaining LW3
requests, killed by the named check of `test_d8_requests.py`), on a database built from the
mutated set (needs Docker); and of `infrx/state/lab_pipeline.py` / `lab_consent.py`'s teacher
consent (killed by the named case of `test_d8_units.py` through the shared runner, no Docker).
The SQL runner is D7's (`code_mutants_d7.kill`).

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_code_mutants_d8.py
"""
from __future__ import annotations

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Runner
from . import code_mutants_d7 as d7
from . import migration_mutants as _d
from . import pgharness

FILE = "0042_lab_d8_ledgers.sql"
DB = f"{pgharness.DATABASE}_d8mut"

ROLES = "check_browser_roles_reach_nothing"
LABELS = "check_labels_are_append_only_one_per_key"
TRUTH = "check_ground_truth_and_synthetic_stay_distinct"
RUNS = "check_an_external_run_is_its_record_and_moves_by_cas"
MONEY = "check_one_reservation_per_key_settled_once"
NOTES = "check_notes_are_write_once"
TEACHER = "check_a_teacher_run_is_consented_per_sample"
FAILURES = "check_teacher_failures_are_an_append_only_log"
EVENTS = "check_checkpoint_events_are_signed_once_per_provider"
SUBS = "check_subscriptions_and_decisions_are_first_write"
RACE = "check_racing_reservations_hold_once"
AMBIGUOUS = "check_an_ambiguous_run_fails_only_on_the_providers_confirmation"
STORE = "check_the_adapters_compose"


def _s(name, old, new, check, why, **kw):
    return _d.Mutant(name, FILE, old, new, "lab", check, why, **kw)


SQL_MUTANTS = (
    # --- DUR-RLS
    _s("d8_service_writes", "    execute format('grant select on infrx.%I to service_role', "
       "t);", "    execute format('grant select, delete on infrx.%I to service_role', t);",
       ROLES, "the platform role deletes labels, runs or ledger rows around the RPCs"),
    _s("d8_row_security_off", "    execute format('alter table infrx.%I enable row level "
       "security', t);", "    execute format('alter table infrx.%I disable row level "
       "security', t);", ROLES, "a future browser grant exposes every provider's ledgers"),
    # --- SR-P1-1 labels
    _s("d8_label_rewritten", "    on conflict (provider_org_id, key) do nothing\n    returning * "
       "into s;", "    on conflict (provider_org_id, key) do update set body = excluded.body\n"
       "    returning * into s;", LABELS, "a replayed key rewrites a label decision"),
    _s("d8_label_conflict_silent", "    if s.body <> e then\n      perform "
       "infrx.refuse('idempotency_conflict', e->>'key' || ' holds another event');",
       "    if false then\n      perform infrx.refuse('idempotency_conflict', e->>'key' || ' "
       "holds another event');", LABELS,
       "a different decision under a used key is silently dropped as a replay"),
    _s("d8_label_any_dataset", "  if not exists (select 1 from infrx.lab_records r where r.ref = "
       "e->>'dataset_ref'\n                    and r.provider_org_id = v_provider and r.kind = "
       "'dataset') then", "  if false then", LABELS,
       "a label is logged under another provider's (or no) dataset"),
    _s("d8_label_any_annotation", "          and a.body::jsonb->>'dataset_ref' = "
       "e->>'dataset_ref') then", "          ) then", LABELS,
       "a label points at an annotation of another dataset"),
    _s("d8_label_order_lost", "  select coalesce(jsonb_agg(s.body order by s.seq), '[]') from "
       "infrx.lab_label_events s", "  select coalesce(jsonb_agg(s.body order by s.key desc), "
       "'[]') from infrx.lab_label_events s", LABELS,
       "review history reads out of order, so the last decision is wrong"),
    _s("d8_label_events_unscoped", "   where s.provider_org_id::text = p_args->>'provider_org_id'"
       "\n     and s.dataset_ref", "   where s.dataset_ref", LABELS,
       "a provider reads another provider's review log"),
    _s("d8_label_key_not_text", "     or jsonb_typeof(e->'key') is distinct from 'string'\n",
       "", LABELS, "a numeric key is coerced to text and collides with a real one"),
    _s("d8_label_mutable", "  foreach t in array array['lab_label_events', "
       "'lab_external_run_events', 'lab_pipeline_notes',", "  foreach t in array array["
       "'lab_external_run_events', 'lab_pipeline_notes',", LABELS,
       "a stored label event is edited in place"),
    # --- ground truth
    _s("d8_synthetic_truth", "  if v_doc->>'method' = 'synthetic' and v_doc->'ground_truth' = "
       "'true'::jsonb then", "  if false then", TRUTH,
       "a teacher's synthetic label is published as ground truth"),
    _s("d8_unreviewed_human", "  if v_doc->>'method' = 'human' and "
       "coalesce(v_doc->>'reviewer_id', '') = '' then", "  if false then", TRUTH,
       "a human label names no reviewer"),
    # --- SR-P3-1 external runs
    _s("d8_run_any_record", "     and r.kind = 'external_run' and r.object_id = v_id;",
       "     and r.kind = 'external_run';", RUNS,
       "a ledger row is created from another run's record"),
    _s("d8_run_foreign_record", "     where r.ref = v_fields->>'run_ref' and r.provider_org_id = "
       "v_provider\n", "     where r.ref = v_fields->>'run_ref'\n", RUNS,
       "a provider runs another provider's published record"),
    _s("d8_run_unlike_record", "    if v_fields->>'connector' is distinct from b->>'connector'\n",
       "    if false\n", RUNS, "the ledger's connector, payer or limit differ from the record"),
    _s("d8_run_recreate_resets", "      if o.doc is distinct from v_new then\n        perform "
       "infrx.refuse('state_conflict', 'this external run already exists otherwise');",
       "      if false then\n        perform infrx.refuse('state_conflict', 'this external run "
       "already exists otherwise');", RUNS, "a re-prepare of a moved run looks like a success"),
    _s("d8_run_stale_move", "  if not found or o.state is distinct from p_args->>'expected' then",
       "  if not found then", RUNS, "a stale worker's move lands over a newer state"),
    _s("d8_run_any_transition", "  execute 'create or replace trigger lab_external_runs_state "
       "before update on '", "  execute 'create or replace trigger lab_external_runs_state "
       "before delete on '", RUNS, "a prepared run jumps to completed without a submit"),
    _s("d8_run_record_fields_rewritten", "  if v_fields ?| array['run_ref', 'connector', "
       "'payer_ref', 'limit'] then", "  if false then", RUNS,
       "a move rewrites the run's limit or payer after preparation"),
    _s("d8_run_fields_dropped", "           doc = doc || v_fields || jsonb_build_object('state', "
       "v_target)", "           doc = doc || jsonb_build_object('state', v_target)", RUNS,
       "the connector's job id and cost are lost"),
    _s("d8_run_history_lost", "  values (v_provider, v_id, v_from, v_target, v_fields);", "  "
       "select 1;", RUNS, "the run's moves are not kept"),
    _s("d8_run_get_unscoped", "   where o.provider_org_id::text = p_args->>'provider_org_id'\n"
       "     and o.external_run_id", "   where o.external_run_id", RUNS,
       "a provider reads another provider's run"),
    # --- money
    _s("d8_reserve_twice", "    return infrx.lab_run_reservation_json(r);\n  end if;\n  begin\n"
       "    update infrx.lab_budgets set reserved = reserved + v_amount",
       "  end if;\n  begin\n    update infrx.lab_budgets set reserved = reserved + v_amount",
       MONEY, "a replayed reserve holds the budget again"),
    _s("d8_reserve_other_limit", "    if r.payer_ref is distinct from p_args->>'payer_ref' or "
       "r.amount <> v_amount then", "    if r.payer_ref is distinct from p_args->>'payer_ref' "
       "then", MONEY, "a key reserved at one limit answers for another"),
    _s("d8_reserve_over_cap", "  exception when check_violation then\n    perform "
       "infrx.refuse('budget_exceeded', 'the reservation exceeds the payer''s '\n"
       "                         'remaining budget');\n  end;\n  return "
       "infrx.lab_run_reservation_json(r);", "  exception when check_violation then\n    "
       "return infrx.lab_run_reservation_json(r);\n  end;\n  return "
       "infrx.lab_run_reservation_json(r);", MONEY,
       "a reservation past the cap is answered as held (and not recorded)"),
    _s("d8_reserve_unflagged", "  perform infrx.require_feature('lab_submission');\n  begin\n"
       "    v_provider := (p_args->>'provider_org_id')::uuid;\n    v_amount :=",
       "  begin\n    v_provider := (p_args->>'provider_org_id')::uuid;\n    v_amount :=",
       MONEY, "new paid work is reserved while the Lab is switched off"),
    _s("d8_settle_twice", "    return infrx.lab_run_reservation_json(r);          -- a resumed "
       "poll: the one settlement\n  end if;", "  end if;", MONEY,
       "a resumed poll settles (and spends) again"),
    _s("d8_settle_other_cost", "    if r.cost is distinct from v_cost then", "    if false then",
       MONEY, "a second, different cost is swallowed as a replay"),
    _s("d8_settle_after_release", "  if r.state = 'released' then\n    perform "
       "infrx.refuse('state_conflict', 'a released reservation is not settled');",
       "  if false then\n    perform infrx.refuse('state_conflict', 'a released reservation "
       "is not settled');", MONEY, "a released hold is also charged"),
    _s("d8_unknown_cost_free", "settled = settled + coalesce(v_cost, r.amount),",
       "settled = settled + coalesce(v_cost, 0),", MONEY,
       "an unknown provider cost is booked as free"),
    _s("d8_cost_over_hold", "  if v_cost < 0 or v_cost > r.amount then", "  if v_cost < 0 then",
       MONEY, "the provider bills past what was reserved"),
    _s("d8_hold_kept_after_settle", "     set reserved = reserved - r.amount, settled = settled + "
       "coalesce(v_cost, r.amount),", "     set settled = settled + coalesce(v_cost, "
       "r.amount),", MONEY, "a settled run keeps holding its budget"),
    _s("d8_release_after_settle", "  if r.state = 'settled' then\n    perform "
       "infrx.refuse('state_conflict', 'a settled reservation is not released');",
       "  if false then\n    perform infrx.refuse('state_conflict', 'a settled reservation is "
       "not released');", MONEY, "a settled hold is released and its budget returned"),
    _s("d8_release_keeps_hold", "    update infrx.lab_budgets set reserved = reserved - "
       "r.amount, updated_at = infrx.now()\n     where (provider_org_id, payer_ref) = "
       "(r.provider_org_id, r.payer_ref);\n    update infrx.lab_run_reservations set state = "
       "'released'", "    update infrx.lab_run_reservations set state = 'released'", MONEY,
       "a released hold never returns to the payer"),
    # --- R184 ambiguous runs
    _s("d8_ambiguous_any_actor", "    if not exists (select 1 from public.profiles pr\n"
       "                    where pr.id::text = v_fields->>'operator' and pr.is_operator) then",
       "    if false then", AMBIGUOUS, "a worker (or any caller) fails an ambiguous run"),
    _s("d8_ambiguous_unconfirmed", "    if length(btrim(coalesce(v_fields->>'confirmation_ref', "
       "''))) = 0 then", "    if false then", AMBIGUOUS,
       "an ambiguous run is failed with no written confirmation from the provider"),
    _s("d8_ambiguous_released", "                and 'submit:' || o.external_run_id = r.key and "
       "o.state = 'ambiguous') then", "                and false) then", AMBIGUOUS,
       "the platform releases an ambiguous run's hold (R184)"),
    _s("d8_ambiguous_fail_keeps_hold", "  if v_from = 'ambiguous' and v_target = 'failed' and "
       "exists (", "  if false and exists (", AMBIGUOUS,
       "a confirmed failure leaves its hold reserved for ever"),
    # --- notes
    _s("d8_note_rewritten", "    if n.body <> p_args->'body' then", "    if false then", NOTES,
       "a checkpoint's first outcome is overwritten by a redelivery"),
    _s("d8_noted_unscoped", "   where n.provider_org_id::text = p_args->>'provider_org_id' and "
       "n.key = p_args->>'key'", "   where n.key = p_args->>'key'", NOTES,
       "a provider reads another provider's notes"),
    # --- SR-P2-1 teacher runs
    _s("d8_teacher_training_optional", "                and not (infrx.lab_grant_current("
       "s.grant_id, 'external_judging')\n                         and "
       "infrx.lab_grant_current(s.grant_id, 'training'))) then", "                and not "
       "infrx.lab_grant_current(s.grant_id, 'external_judging')) then", TEACHER,
       "a sample granted for judging only is sent to a teacher for training labels"),
    _s("d8_teacher_foreign_samples", "  if cardinality(v_ids) = 0 or exists (\n       select 1 "
       "from unnest(v_ids) x where not exists (", "  if cardinality(v_ids) = 0 or false and "
       "exists (\n       select 1 from unnest(v_ids) x where not exists (", TEACHER,
       "samples of another dataset ride along unchecked"),
    _s("d8_teacher_no_snapshot", "   where s.dataset_ref = p_args->>'dataset_ref' and "
       "s.sample_id = any(v_ids)\n   group by s.grant_id;", "   where s.dataset_ref = "
       "p_args->>'dataset_ref' and s.sample_id = any(v_ids) and false\n   group by "
       "s.grant_id;", TEACHER,
       "nothing is snapshotted, so record_sent never sees a revocation"),
    _s("d8_teacher_version_ignored", "       and not (coalesce((select g.version = "
       "c.grant_version and g.effective_at <= infrx.now()", "       and not (coalesce((select "
       "g.effective_at <= infrx.now()", TEACHER, "a re-grant since the snapshot goes unnoticed"),
    _s("d8_teacher_expiry_unrechecked", "                and infrx.lab_grant_current("
       "c.grant_id, null)))", "                ))", TEACHER,
       "samples leave after their grant expired"),
    _s("d8_teacher_sent_unchecked", "  if not infrx.lab_teacher_consent_current(r.run_id) then",
       "  if false then", TEACHER, "a revoked sample leaves at record_sent"),
    _s("d8_teacher_budget_skipped", "    update infrx.lab_budgets set reserved = reserved + "
       "v_cost, updated_at = infrx.now()\n     where (provider_org_id, payer_ref) = "
       "(v_provider, r.payer_ref);\n  exception when check_violation then\n    perform "
       "infrx.refuse('budget_exceeded', 'the reservation exceeds the payer''s '\n"
       "                         'remaining budget');\n  end;\n  return "
       "infrx.lab_judge_json(r);\nend $$;\n\ncreate or replace function "
       "infrx.lab_teacher_record_sent", "    perform 1;\n  exception when check_violation then\n"
       "    perform infrx.refuse('budget_exceeded', 'the reservation exceeds the payer''s '\n"
       "                         'remaining budget');\n  end;\n  return "
       "infrx.lab_judge_json(r);\nend $$;\n\ncreate or replace function "
       "infrx.lab_teacher_record_sent", TEACHER,
       "a teacher batch holds nothing on the payer's budget"),
    _s("d8_teacher_run_any_provider", "    if r.provider_org_id <> v_provider or r.purpose <> "
       "'teacher_annotation' then\n      perform infrx.refuse('not_found', 'no such teacher "
       "run');\n    end if;\n    return infrx.lab_judge_json(r);                   -- a replay",
       "    return infrx.lab_judge_json(r);                   -- a replay", TEACHER,
       "another provider reads a teacher run by replaying its id"),
    # --- failures
    _s("d8_failures_duplicated", "      from jsonb_array_elements(coalesce(p_args->'failures', "
       "'[]')) f\n    on conflict do nothing;", "      from jsonb_array_elements(coalesce("
       "p_args->'failures', '[]')) f\n    on conflict do nothing;\n    insert into "
       "infrx.lab_teacher_failures (run_id, sample_id, reason)\n    select (p_args->>'run_id')"
       "::uuid, (f->>'sample_id')::uuid, f->>'reason' || '_again'\n      from "
       "jsonb_array_elements(coalesce(p_args->'failures', '[]')) f\n    on conflict do nothing;",
       FAILURES, "a repeated collect logs every failure again"),
    _s("d8_failures_any_run", "                    and purpose = 'teacher_annotation') then",
       "                    ) or false then", FAILURES,
       "failures are logged against a judge run (or an unknown id is a 500)"),
    _s("d8_failures_bad_reason", "  exception when check_violation or not_null_violation or "
       "invalid_text_representation\n       or invalid_parameter_value then\n    perform "
       "infrx.refuse('invalid_request', 'a failure is a sample id and a reason word');",
       "  exception when division_by_zero then\n    perform infrx.refuse('invalid_request', "
       "'a failure is a sample id and a reason word');", FAILURES,
       "a malformed failure is a 500"),
    # --- WR-B3-1 checkpoint ledger
    _s("d8_event_rewritten", "    on conflict (provider_org_id, checkpoint_id) do nothing\n"
       "    returning * into c;", "    on conflict (provider_org_id, checkpoint_id) do update "
       "set body = excluded.body, step = excluded.step\n    returning * into c;", EVENTS,
       "a later event rewrites a signed checkpoint"),
    _s("d8_event_conflict_silent", "    if c.body <> e then\n      perform "
       "infrx.refuse('idempotency_conflict', 'the checkpoint id names another event');",
       "    if false then\n      perform infrx.refuse('idempotency_conflict', 'the checkpoint "
       "id names another event');", EVENTS, "another artifact under a used id reads as a replay"),
    _s("d8_event_foreign_run", "                      and r.provider_org_id = v_provider and "
       "r.kind = 'external_run') then\n      perform infrx.refuse('not_found', 'no such "
       "external run for this provider');\n    end if;\n    insert into "
       "infrx.lab_checkpoint_events", "                      and r.kind = 'external_run') then"
       "\n      perform infrx.refuse('not_found', 'no such external run for this provider');\n"
       "    end if;\n    insert into infrx.lab_checkpoint_events", EVENTS,
       "a provider files checkpoints against another provider's run"),
    _s("d8_event_read_unscoped", "   where c.provider_org_id::text = p_args->>'provider_org_id'\n"
       "     and c.checkpoint_id::text = p_args->>'checkpoint_id';", "   where "
       "c.checkpoint_id::text = p_args->>'checkpoint_id' limit 1;", EVENTS,
       "a provider reads another provider's checkpoint event"),
    _s("d8_rejection_rewritten", "    on conflict do nothing;\n  exception when "
       "foreign_key_violation then\n    perform infrx.refuse('not_found', 'no such checkpoint "
       "event for this provider');", "    on conflict (provider_org_id, checkpoint_id) do "
       "update set reason = excluded.reason;\n  exception when foreign_key_violation then\n"
       "    perform infrx.refuse('not_found', 'no such checkpoint event for this provider');",
       EVENTS, "a later rejection rewrites why a checkpoint was refused"),
    _s("d8_events_by_arrival", "                            order by c.step, c.recorded_at, "
       "c.checkpoint_id), '[]')", "                            order by c.recorded_at, "
       "c.checkpoint_id), '[]')", EVENTS, "latest_only compares arrival, not training step"),
    _s("d8_rejected_flag_lost", "  select coalesce(jsonb_agg(jsonb_build_object('event', c.body, "
       "'rejected',\n                                               x.checkpoint_id is not "
       "null)", "  select coalesce(jsonb_agg(jsonb_build_object('event', c.body, 'rejected',\n"
       "                                               false)", EVENTS,
       "a rejected checkpoint supersedes a valid one"),
    _s("d8_subscription_rewritten", "    on conflict (subscription_id) do nothing;\n  exception",
       "    on conflict (subscription_id) do update set body = excluded.body;\n  exception",
       SUBS, "a resubmitted form rewrites a pinned suite"),
    _s("d8_subscription_foreign_run", "    if not exists (select 1 from infrx.lab_records r "
       "where r.ref = s->>'external_run_ref'\n                      and r.provider_org_id = "
       "v_provider and r.kind = 'external_run') then", "    if false then", SUBS,
       "a provider subscribes to another provider's external run"),
    _s("d8_subscriptions_unscoped", "   where x.provider_org_id::text = "
       "p_args->>'provider_org_id'\n     and x.external_run_ref", "   where x.external_run_ref",
       SUBS, "a provider lists another's subscriptions"),
    _s("d8_decision_rewritten", "    on conflict (subscription_id, checkpoint_id) do nothing;",
       "    on conflict (subscription_id, checkpoint_id) do update set state = excluded.state, "
       "reason = excluded.reason, run_id = excluded.run_id;", SUBS,
       "a redelivery decides a checkpoint a second time"),
    _s("d8_decision_shape", "  constraint lab_checkpoint_decisions_shape check ((state = "
       "'queued' and run_id is not null)", "  constraint lab_checkpoint_decisions_shape check "
       "((state = 'queued')", SUBS, "a queued decision names no run"),
    # --- the adapters' RPC contract
    _s("d8_reservation_state_misreported", "'state', r.state, 'cost', r.cost::text)",
       "'state', 'held', 'cost', r.cost::text)", STORE,
       "a settled reservation reads as still held, so P3 settles it again"),
    # --- race
    _s("d8_reserve_race", "    on conflict (provider_org_id, key) do nothing\n    returning * "
       "into r;\n  exception when foreign_key_violation then", "    returning * into r;\n  "
       "exception when foreign_key_violation then", RACE,
       "two workers racing one key both hold the budget or one fails"),
)
SQL_NAMES = tuple(m.name for m in SQL_MUTANTS)

# --- the remaining requests, `0043_lab_reads_and_proposals.sql` (test_d8_requests) ----------
REQUESTS_FILE = "0043_lab_reads_and_proposals.sql"
DB_Q = f"{pgharness.DATABASE}_d8qmut"
Q_ROLES = "check_browser_roles_reach_nothing"
Q_DATASETS = "check_the_dataset_list_is_the_providers_versions"
Q_RUNS = "check_runs_carry_their_timeline_and_finished_runs_stay_finished"
Q_EXPERIMENTS = "check_experiments_are_write_once_with_their_report"
Q_LISTING = "check_the_checkpoint_listing_is_provider_scoped"
Q_PROPOSE = "check_one_pending_proposal_at_the_displayed_fence"
Q_DECIDE = "check_a_proposal_is_decided_once_through_the_fence"
Q_JUDGE = "check_the_judge_runs_door"
Q_CALIBRATION = "check_calibrations_are_j3s_and_the_latest_stands"
Q_ACTIVE = "check_release_active_answers_the_running_head_with_pins"
Q_ELIGIBLE = "check_release_eligibility_is_current_and_default_deny"
Q_RECORD = "check_assignments_are_recorded_once_for_the_runtime"
Q_ROLE = "check_the_control_login_is_bounded_and_lab_only"
Q_SHADOW = "check_the_operator_raises_the_shadow_limit_and_only_the_operator"


def _q(name, old, new, check, why, **kw):
    return _d.Mutant(name, REQUESTS_FILE, old, new, "lab", check, why, **kw)


#: 0045 (WR-E8L-2/E8L-F1) redefines `infrx.release_active`, so an anchor inside 0043's
#: body of that function is superseded (`test_no_mutant_anchors_in_a_superseded_function_body`
#: refuses it) - its mutants move here.
IDENTITY_FILE = "0045_lab_serving_ref_identity.sql"


def _qid(name, old, new, check, why, **kw):
    return _d.Mutant(name, IDENTITY_FILE, old, new, "lab", check, why, **kw)


#: WR-E8L-4: the operator RPC to raise `lab_rollouts.shadow_limit`.
SHADOW_FILE = "0046_lab_shadow_limit_operator.sql"


def _q46(name, old, new, check, why, **kw):
    return _d.Mutant(name, SHADOW_FILE, old, new, "lab", check, why, **kw)


REQUESTS = (
    _q("q_service_writes", "    execute format('grant select on infrx.%I to service_role', "
       "t);", "    execute format('grant select, update on infrx.%I to service_role', t);",
       Q_ROLES, "the platform role edits experiments, proposals or calibrations directly"),
    _q("q_door_to_anon", "grant execute on function public.lab_judge_runs(uuid, uuid) to "
       "authenticated;", "grant execute on function public.lab_judge_runs(uuid, uuid) to "
       "authenticated, anon;", Q_ROLES, "an anonymous caller probes judge runs"),
    _q("q_datasets_unscoped", "   where r.kind = 'dataset' and r.provider_org_id::text = "
       "p_args->>'provider_org_id'", "   where r.kind = 'dataset'", Q_DATASETS,
       "a provider lists another's datasets"),
    _q("q_datasets_count_lost", "           'samples', (select count(*) from "
       "infrx.lab_dataset_samples s\n                        where s.dataset_ref = r.ref))",
       "           'samples', 0)", Q_DATASETS, "the versions list shows empty datasets"),
    _q("q_run_timeline_lost", "    'created_at', r.created_at, 'updated_at', r.updated_at,\n",
       "", Q_RUNS, "the timeline has no times"),
    _q("q_experiment_foreign_runs", "       and r.provider_org_id = v_provider\n       and r.ref "
       "in (p_args->>'baseline_run_ref', p_args->>'candidate_run_ref')) <> 2 then",
       "       and r.ref in (p_args->>'baseline_run_ref', p_args->>'candidate_run_ref')) <> 2 "
       "then", Q_EXPERIMENTS, "an experiment compares another provider's runs"),
    _q("q_experiment_rewritten", "      perform infrx.refuse('idempotency_conflict', 'this "
       "experiment id holds another launch');", "      null;", Q_EXPERIMENTS,
       "a changed resubmit answers as the stored launch"),
    _q("q_experiment_any_report", "                    and p.protocol_digest = "
       "e.protocol_digest\n", "", Q_EXPERIMENTS,
       "a report under another protocol is shown as this experiment's"),
    _q("q_experiments_unscoped", "    from infrx.lab_experiments e where e.provider_org_id::text "
       "= p_args->>'provider_org_id'", "    from infrx.lab_experiments e where true", Q_EXPERIMENTS,
       "a provider lists another's experiments"),
    _q("q_listing_leaks_evaluator", "      'subscription', s.body - 'evaluator' - "
       "'owner_user_id',", "      'subscription', s.body,", Q_LISTING,
       "the listing exposes the evaluator spec and the subscriber's identity"),
    _q("q_listing_unscoped", "    from infrx.lab_checkpoint_subscriptions s\n   where "
       "s.provider_org_id::text = p_args->>'provider_org_id'", "    from "
       "infrx.lab_checkpoint_subscriptions s\n   where true", Q_LISTING,
       "a provider lists another's subscriptions"),
    _q("q_listing_receipt_lost", "                       'receipt', rc.state, 'state', d.state,",
       "                       'receipt', null, 'state', d.state,", Q_LISTING,
       "a decision shows no receipt state"),
    _q("q_two_pending", "create unique index if not exists lab_release_proposals_one_pending\n"
       "  on infrx.lab_release_proposals (policy_ref) where state = 'proposed';",
       "create index if not exists lab_release_proposals_one_pending\n  on "
       "infrx.lab_release_proposals (policy_ref) where state = 'proposed';", Q_PROPOSE,
       "a double click files two pending proposals"),
    _q("q_stale_fence_proposed", "  if o.fence::text is distinct from p_args->>'fence' then\n"
       "    perform infrx.refuse('state_conflict', 'stale fence: the release is at ' || "
       "o.fence);\n  end if;\n  if o.state = 'rolled_back'", "  if o.state = 'rolled_back'",
       Q_PROPOSE, "a proposal made against an outdated page is filed"),
    _q("q_expand_paused", "  if o.state = 'rolled_back' or (p_args->>'kind' = 'expand' and "
       "o.state <> 'running') then", "  if o.state = 'rolled_back' then", Q_PROPOSE,
       "an expansion is proposed on a paused release"),
    _q("q_propose_foreign", "   where policy_ref = p_args->>'policy_ref'\n     and "
       "provider_org_id::text = p_args->>'provider_org_id' for update;", "   where policy_ref "
       "= p_args->>'policy_ref' for update;", Q_PROPOSE,
       "a provider proposes on another provider's release"),
    _q("q_proposal_replay_conflict", "      perform infrx.refuse('idempotency_conflict', 'this "
       "proposal id holds another proposal');", "      null;", Q_PROPOSE,
       "a different proposal under a used id answers as the stored one"),
    _q("q_decide_bypasses_fence", "    perform infrx.lab_release_transition(jsonb_build_object("
       "\n      'policy_ref', p.policy_ref, 'fence', p.fence,", "    perform "
       "infrx.lab_release_transition(jsonb_build_object(\n      'policy_ref', p.policy_ref, "
       "'fence', (select o.fence from infrx.lab_rollouts o where o.policy_ref = p.policy_ref),"
       , Q_DECIDE, "an approval lands on a release that moved since the proposal"),
    _q("q_approve_without_move", "  if coalesce((p_args->>'approve')::boolean, false) then\n"
       "    perform infrx.lab_release_transition(", "  if false then\n    perform "
       "infrx.lab_release_transition(", Q_DECIDE,
       "an approval is recorded but the release never moves"),
    _q("q_proposal_mutable", "  if old.state <> 'proposed' or new.state = 'proposed'\n",
       "  if false\n", Q_DECIDE, "a decided proposal is reopened or rewritten"),
    _q("q_judge_viewer", "  perform infrx.lab_judge_door(p_provider_org_id, 'developer');\n"
       "  return (select", "  perform infrx.lab_judge_door(p_provider_org_id, 'viewer');\n  "
       "return (select", Q_JUDGE, "a viewer reads judge scores of customer requests"),
    _q("q_judge_other_provider", "     where r.provider_org_id = p_provider_org_id and r.purpose "
       "= 'external_judging'", "     where r.purpose = 'external_judging'", Q_JUDGE,
       "a provider reads another provider's judge runs"),
    _q("q_judge_any_request", "       and p_request_id = any(r.sent_sample_ids)\n", "", Q_JUDGE,
       "a request shows judge runs that never sent it"),
    _q("q_judge_after_revocation", "       and infrx.lab_grant_current(r.grant_id, "
       "'external_judging')) x);", "       ) x);", Q_JUDGE,
       "judge output about a grantor's content is shown after the grant is revoked"),
    _q("q_judge_rationale", "                     'criterion', s->>'name', 'score', "
       "(s->>'score')::int, 'max', 5,", "                     'criterion', s->>'name', "
       "'score', (s->>'score')::int, 'max', 5, 'rationale', s->'rationale',", Q_JUDGE,
       "judge text about the grantor's content reaches the Lab"),
    _q("q_judge_state_map", "                   when 'completed' then 'collected' else "
       "'released' end,", "                   else 'released' end,", Q_JUDGE,
       "a collected run reads as released"),
    _q("q_calibration_oldest", "                                  order by k.calibration_id "
       "desc limit 1),", "                                  order by k.calibration_id limit "
       "1),", Q_CALIBRATION, "a stale calibration is shown after J3 recomputed it"),
    _q("q_calibration_any_config", "                                    and k.judge_model = "
       "c.judge_model\n", "", Q_CALIBRATION,
       "another judge model's calibration is claimed for this one"),
    _q("q_calibrated_unsupported", "     or (c->>'state' = 'calibrated'\n         and "
       "((c->>'labels')::numeric < (c->>'required')::numeric", "     or (false\n         and "
       "((c->>'labels')::numeric < (c->>'required')::numeric", Q_CALIBRATION,
       "a configuration is claimed calibrated on too few labels"),
    _qid("q_active_any_state", "   where x.endpoint_id = v_endpoint and x.state = 'running';",
       "   where x.endpoint_id = v_endpoint and x.state in ('running', 'paused');", Q_ACTIVE,
       "a paused experiment keeps routing candidates"),
    _qid("q_active_old_listing", "   order by l.version desc limit 1;", "   order by l.version "
       "limit 1;", Q_ACTIVE, "the alias routes by an outdated catalog listing"),
    # WR-E8L-2/E8L-F1 (0045): a candidate is resolved by deployment_revision_id and its ref
    # re-derived and compared whole (identity + digest), not by serving_version_id alone.
    _qid("q_active_unpinned", "    if v_label is null or v_computed is distinct from "
       "(c->>'serving_ref') then\n      perform infrx.refuse('state_conflict', 'candidate ' "
       "|| (c->>'serving_ref')", "    if false then\n      perform infrx.refuse("
       "'state_conflict', 'candidate ' || (c->>'serving_ref')",
       Q_ACTIVE, "a candidate without a published revision is routed to a null pin"),
    _qid("q_active_foreign_revision", "       and d2.provider_org_id = o.provider_org_id and "
       "sv.model_id = v_model;", "       and d2.provider_org_id = o.provider_org_id;", Q_ACTIVE,
       "a candidate pins another model's revision under this alias (same provider, a real "
       "deployment, a matching digest - only the model check stops it)"),
    _qid("q_active_digest_ignored", "    if v_label is null or v_computed is distinct from "
       "(c->>'serving_ref') then", "    if v_label is null then", Q_ACTIVE,
       "R191: a candidate whose digest is stale (the revision moved on) still routes, keyed "
       "by deployment id alone"),
    _q("q_active_to_service", "revoke all on function infrx.release_active(text), "
       "infrx.release_eligible(uuid, uuid),\n  infrx.record_rollout_assignment(jsonb) from "
       "public, anon, authenticated, service_role;", "revoke all on function "
       "infrx.release_active(text), infrx.release_eligible(uuid, uuid),\n  "
       "infrx.record_rollout_assignment(jsonb) from public, anon, authenticated;", Q_ACTIVE,
       "the routing port is reachable by every service_role holder, not the runtime only"),
    _q("q_eligible_revoked", "                          and (g.revoked_at is null or "
       "infrx.now() < g.revoked_at)\n", "", Q_ELIGIBLE,
       "a subject who revoked consent stays in the experiment"),
    _q("q_eligible_expired", "                          and (g.expires_at is null or "
       "infrx.now() < g.expires_at)\n", "", Q_ELIGIBLE,
       "an expired provider_sharing grant keeps routing a subject into a candidate"),
    _q("q_eligible_any_purpose", "                          and 'provider_sharing' = "
       "any(g.purposes)\n", "", Q_ELIGIBLE, "a capture-only grant enrols a subject"),
    _q("q_eligible_suspended", "  select coalesce((select not coalesce(org.suspended, false)\n",
       "  select coalesce((select true\n", Q_ELIGIBLE,
       "a suspended organization's traffic enters experiments"),
    _q("q_eligible_first_version", "                    order by g.version desc limit 1), "
       "false)", "                    order by g.version limit 1), false)", Q_ELIGIBLE,
       "eligibility reads the first grant version, so a revocation never applies"),
    _q("q_record_any_serving", "     or not (record->>'serving_ref' = v_doc->>'baseline_ref' or "
       "exists (", "     or not (true or exists (", Q_RECORD,
       "a coverage row names a serving the policy never offered"),
    _q("q_record_rewritten", "    on conflict (policy_id, request_id) do nothing;\n  exception "
       "when check_violation", "    on conflict (policy_id, request_id) do update set "
       "serving_ref = excluded.serving_ref;\n  exception when check_violation", Q_RECORD,
       "a retried admission re-routes an admitted request"),
    _q("q_record_foreign", "     and r.provider_org_id::text = record->>'provider_org_id';",
       ";", Q_RECORD, "an assignment is filed under another provider's policy"),
    _q("q_role_unbounded", "nobypassrls connection limit 10';", "nobypassrls connection limit "
       "-1';",
       Q_ROLE, "a crash-looping Lab release exhausts the App's connections"),
    _q("q_role_consent_writes", "  infrx.lab_deployment_aggregates(jsonb) to "
       "infrx_lab_control;", "  infrx.lab_deployment_aggregates(jsonb), "
       "infrx.lab_put_access_grant(jsonb), infrx.lab_revoke_access_grant(jsonb) to "
       "infrx_lab_control;", Q_ROLE, "a Lab credential mints or revokes a consumer's P-09 grant"),
    _q("q_role_content_refs", "  infrx.lab_deployment_aggregates(jsonb) to "
       "infrx_lab_control;", "  infrx.lab_deployment_aggregates(jsonb), "
       "infrx.lab_content_ref_redeem(jsonb) to infrx_lab_control;", Q_ROLE,
       "the Lab redeems content refs meant for the content service only"),
    _q("q_role_other_lab", "  infrx.lab_deployment_aggregates(jsonb) to "
       "infrx_lab_control;", "  infrx.lab_deployment_aggregates(jsonb), "
       "infrx.lab_list_datasets(jsonb) to infrx_lab_control;", Q_ROLE,
       "the control login reaches Lab RPCs its factory never calls"),
    _q("q_role_service_member", "grant usage on schema infrx to infrx_lab_control;",
       "grant usage on schema infrx to infrx_lab_control;\ngrant service_role to "
       "infrx_lab_control;", Q_ROLE, "the bounded login sets role service_role and holds all"),
    _q("q_role_no_clock", "grant execute on function infrx.now(), infrx.usd_price(text),",
       "grant execute on function infrx.usd_price(text),", Q_ROLE,
       "/readyz answers 503 for ever on the control login"),
    _q("q_role_no_price", "grant execute on function infrx.now(), infrx.usd_price(text),",
       "grant execute on function infrx.now(),", Q_ROLE,
       "the catalog's price read fails on the control login"),
    _q("q_role_no_control_rpcs", "  infrx.lab_control_fund(jsonb), infrx.lab_control_events(jsonb),",
       "  infrx.lab_control_fund(jsonb),", Q_ROLE, "the control events page fails on its login"),
    _q("q_role_no_memberships", "  infrx.lab_provider_memberships(jsonb), "
       "infrx.lab_access_grants(jsonb),", "  infrx.lab_access_grants(jsonb),", Q_ROLE,
       "every Lab session is refused on the control login"),
    _q("q_role_no_models", "create policy lab_control_reads_models on public.models for select "
       "to infrx_lab_control\n  using (true);", "create policy lab_control_reads_models on "
       "public.models for select to infrx_lab_control\n  using (false);", Q_ROLE,
       "a serving registration never finds its model's provider"),
    _q("q_role_no_registry_rows", "    execute format('create policy lab_control_registry on "
       "infrx.%I to infrx_lab_control '\n                   'using (true) with check (true)', "
       "r);", "    execute format('create policy lab_control_registry on infrx.%I to "
       "infrx_lab_control '\n                   'using (false) with check (true)', r);",
       Q_ROLE, "the catalog reads no serving revision on the control login"),
    _q("q_role_no_registry_writes", "    execute format('grant select, insert on infrx.%I to "
       "infrx_lab_control', r);", "    execute format('grant select on infrx.%I to "
       "infrx_lab_control', r);", Q_ROLE, "PgRegistry.put fails on the control login"),
    _q("q_role_registry_update", "    execute format('grant select, insert on infrx.%I to "
       "infrx_lab_control', r);", "    execute format('grant select, insert, update on "
       "infrx.%I to infrx_lab_control', r);", Q_ROLE,
       "the Lab edits registry rows instead of appending them"),
    _q("q_role_api_keys", "grant select (model_uuid, id, provider_org_id) on public.models to "
       "infrx_lab_control;", "grant select (model_uuid, id, provider_org_id) on public.models to "
       "infrx_lab_control;\ngrant select on public.api_keys to infrx_lab_control;", Q_ROLE,
       "the Lab login reads the App's key hashes"),
    _q46("q_shadow_no_door", "v_actor text := infrx.console_operator(p_reason, "
       "p_idempotency_key);", "v_actor text;", Q_SHADOW,
       "any authenticated caller raises another provider's shadow bound"),
    _q46("q_shadow_lowers", "  if p_shadow_limit > o.shadow_limit then", "  if true then",
       Q_SHADOW, "an operator call can shrink the shadow bound another operator granted"),
    _q46("q_shadow_negative", "  if p_shadow_limit is null or p_shadow_limit < 0 then",
       "  if false then", Q_SHADOW, "a negative shadow limit is accepted"),
)
REQUEST_NAMES = tuple(m.name for m in REQUESTS)

RUNNER = Runner(name="d8", targets=("tests/d/test_d8_units.py",))
F = "state/lab_pipeline.py"
LOG = "test_label_log__sends_the_provider_and_the_event_whole"
LEDGER = "test_run_ledger__sends_the_cas_the_key_and_the_cost_as_given"
TEACH = "test_teacher_ledger__reserves_the_dataset_and_reads_its_consent_back"
CPS = "test_checkpoint_ledger__round_trips_b3s_models_provider_scoped"
ROUTING = "test_routing__reads_the_head_as_rs_release_eligibility_and_records_once"
READS = "test_reads_and_proposals__send_the_provider_the_fence_and_a_validated_decision"


def _p(name, invariant, old, new, *cases, file=F, **kw) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases, **kw)


CODE_MUTANTS = (
    _p("d8_py_label_unscoped", "a label is the server's provider's",
       'return await self._call("lab_label_append", {"provider_org_id": provider_org_id,',
       'return await self._call("lab_label_append", {"provider_org_id": None,', LOG),
    _p("d8_py_events_other_dataset", "events are read by dataset",
       '"dataset_ref": dataset_ref})\n\n\nclass PgRunLedger:', '"dataset_ref": None})\n\n\n'
       'class PgRunLedger:', LOG),
    _p("d8_py_move_without_expected", "a move carries its CAS expectation",
       '"expected": expected, "target": target, "fields": fields})', '"target": target, '
       '"fields": fields})', LEDGER),
    _p("d8_py_fields_dropped", "a move carries its fields",
       '"expected": expected, "target": target, "fields": fields})', '"expected": expected, '
       '"target": target, "fields": {}})', LEDGER),
    _p("d8_py_cost_estimated", "an unknown cost stays unknown",
       '"key": key, "cost": cost})', '"key": key, "cost": cost or "0.00000000"})', LEDGER),
    _p("d8_py_reserve_limit_lost", "a reservation carries its limit",
       '"provider_org_id": provider_org_id, "key": key, "payer_ref": payer_ref,\n'
       '            "limit": limit})', '"provider_org_id": provider_org_id, "key": key, '
       '"payer_ref": payer_ref,\n            "limit": "0.00000000"})', LEDGER),
    _p("d8_py_note_unscoped", "a note is the provider's",
       'return await self._call("lab_pipeline_noted", {"provider_org_id": provider_org_id,',
       'return await self._call("lab_pipeline_noted", {"provider_org_id": None,', LEDGER),
    _p("d8_py_teacher_on_judge_path", "a teacher run reserves through the teacher RPC",
       'return _run(await self._call("lab_teacher_reserve", {',
       'return _run(await self._call("lab_judge_reserve", {', TEACH),
    _p("d8_py_teacher_dataset_lost", "the batch's dataset is the consent",
       '"dataset_ref": consent.grant_id, "sample_ids": list(sample_ids),',
       '"dataset_ref": None, "sample_ids": list(sample_ids),', TEACH),
    _p("d8_py_teacher_sent_on_judge_path", "a teacher run's samples leave through the "
       "teacher recheck", '"lab_teacher_record_sent", {', '"lab_judge_record_sent", {', TEACH),
    _p("d8_py_failures_dropped", "every failure is logged",
       '{"sample_id": s, "reason": r} for s, r in failures]}))["inserted"]',
       '{"sample_id": s, "reason": r} for s, r in failures][:0]}))["inserted"]', TEACH),
    _p("d8_py_teacher_consent_misread", "a teacher run's consent is its dataset",
       'consent=Consent(doc["dataset_ref"], 1) if doc.get("dataset_ref") else',
       'consent=Consent(str(doc["grant_id"]), 1) if doc.get("dataset_ref") else', TEACH,
       file="state/lab_consent.py"),
    _p("d8_py_event_by_name", "an event is stored by its wire names",
       '"event": event.model_dump(mode="json", by_alias=True)})',
       '"event": event.model_dump(mode="json")})', CPS),
    _p("d8_py_event_unscoped", "an event is read for its provider",
       'return _models()[0].model_validate(await self._call("lab_checkpoint_event", {\n'
       '            "provider_org_id": provider_org_id,', 'return _models()[0].model_validate('
       'await self._call("lab_checkpoint_event", {\n            "provider_org_id": None,', CPS),
    _p("d8_py_rejected_dropped", "an event carries whether it was rejected",
       'return [(event_type.model_validate(row["event"]), row["rejected"])',
       'return [(event_type.model_validate(row["event"]), False)', CPS),
    _p("q_py_head_unpinned", "the router gets each candidate's pin",
       "policy_ref=row[1], revisions=row[2],", "policy_ref=row[1], revisions={},", ROUTING,
       file="state/lab_rollout.py"),
    _p("q_py_shadow_unbounded", "the shadow bound is the store's",
       "                       shadow_limit=row[3])", "                       shadow_limit=10**6)",
       ROUTING, file="state/lab_rollout.py"),
    _p("q_py_no_head_is_a_release", "no running head is no release",
       "        if row is None:\n            return None\n        from ..rollouts",
       "        if row is None:\n            row = (None,) * 4\n        from ..rollouts",
       ROUTING, file="state/lab_rollout.py"),
    _p("q_py_eligible_other_org", "eligibility is the subject organization's",
       "(policy_id, auth.org_id)))[0]", "(policy_id, None)))[0]", ROUTING,
       file="state/lab_rollout.py"),
    _p("q_py_assignment_by_name", "an assignment is sent by its wire names",
       "(Jsonb(assignment.model_dump(mode=\"json\", by_alias=True)),))",
       "(Jsonb(assignment.model_dump(mode=\"json\")),))", ROUTING, file="state/lab_rollout.py"),
    _p("q_py_proposal_fence_lost", "a proposal carries the fence the page showed",
       '"policy_ref": policy_ref, "kind": kind, "fence": fence, "proposed_by": proposed_by})',
       '"policy_ref": policy_ref, "kind": kind, "fence": None, "proposed_by": proposed_by})',
       READS, file="state/lab_rollout.py"),
    _p("q_py_decision_unvalidated", "an approval's decision is a validated record",
       "        if approve:\n            records.parse(decision)", "        if False:\n"
       "            records.parse(decision)", READS, file="state/lab_rollout.py"),
    _p("q_py_reasons_dropped", "a decision keeps its reasons",
       '"decision": decision, "reasons": list(reasons)})', '"decision": decision, '
       '"reasons": []})', READS, file="state/lab_rollout.py"),
    _p("q_py_experiment_runs_swapped", "baseline and candidate stay in their places",
       '"baseline_run_ref": baseline_run_ref, "candidate_run_ref": candidate_run_ref,',
       '"baseline_run_ref": candidate_run_ref, "candidate_run_ref": baseline_run_ref,', READS,
       file="state/lab_data.py"),
    _p("q_py_datasets_unscoped", "the dataset list is the provider's",
       'return await self._call("lab_list_datasets", {"provider_org_id": provider_org_id})',
       'return await self._call("lab_list_datasets", {"provider_org_id": None})', READS,
       file="state/lab_data.py"),
    _p("q_py_listing_unscoped", "the checkpoint listing is the provider's",
       'return await self._call("lab_checkpoint_listing", {"provider_org_id": provider_org_id})',
       'return await self._call("lab_checkpoint_listing", {"provider_org_id": None})', READS),
    _p("q_py_calibration_other_model", "a calibration is its configuration's",
       '"judge_model": judge_model, "rubric_version": rubric_version,',
       '"judge_model": None, "rubric_version": rubric_version,', READS,
       file="state/lab_consent.py"),
    _p("d8_py_decision_run_lost", "a decision carries its run id",
       '"state": state, "reason": reason, "run_id": run_id})', '"state": state, "reason": '
       'reason, "run_id": None})', CPS),
)


def kill(mutant) -> tuple[str, str]:
    from . import test_d8_ledgers as t
    return d7.kill(mutant, DB, t)


def kill_request(mutant) -> tuple[str, str]:
    from . import test_d8_requests as q
    return d7.kill(mutant, DB_Q, q)


def run_code_mutant(mutant):
    return shared.run_mutant(mutant, RUNNER)
