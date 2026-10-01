"""R32/R40 for D6J: single-edit defects of `0031_lab_consent.sql` (killed by the named check of
`test_d6j_consent.py` on a database built from the mutated set, needs Docker) and of
`infrx/state/lab_consent.py` (killed by the named case of `test_d6j_units.py` through the
shared runner, no Docker). The SQL runner is D7's (`code_mutants_d7.kill`) with this list's
database and world.

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_code_mutants_d6j.py
"""
from __future__ import annotations

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Runner
from . import code_mutants_d7 as d7
from . import migration_mutants as _d
from . import pgharness
from . import test_d6j_consent as t
from . import test_d6j_judge as judge_world

FILE = "0031_lab_consent.sql"
DB = f"{pgharness.DATABASE}_d6jmut"

ROLES = "check_browser_roles_reach_nothing"
FLAG = "check_submission_has_its_own_flag"
BUDGET = "check_budget_is_provider_usd_under_a_named_payer"
CONSENT = "check_consent_is_snapshotted_and_current_before_egress"
INTENT = "check_reserve_intent_ack_and_settle"
AMBIGUOUS = "check_ambiguous_is_quarantined_never_resubmitted"
OTHER = "check_other_providers_reach_nothing"
RACE = "check_concurrent_reservations_never_exceed_the_limit"
ONE_INTENT = "check_one_submit_intent_under_contention"
STATES = "check_transitions_are_the_contracts"
STORE = "check_the_store_composes"


def _s(name, old, new, check, why, **kw):
    return _d.Mutant(name, FILE, old, new, "lab", check, why, **kw)


SQL_MUTANTS = (
    # --- DUR-RLS
    _s("d6j_service_writes", "execute format('grant select on infrx.%I to service_role', t);",
       "execute format('grant select, delete on infrx.%I to service_role', t);", ROLES,
       "the platform role rewrites budgets and submissions around every RPC check"),
    _s("d6j_row_security_off", "execute format('alter table infrx.%I enable row level security'",
       "execute format('alter table infrx.%I disable row level security'", ROLES,
       "a future grant to a browser role exposes every provider's budgets"),
    # --- enablement
    _s("d6j_prepare_ungated", "  perform infrx.require_feature('lab_submission');\n  select * "
       "into r from", "  select * into r from", FLAG,
       "external work is reserved while submission is switched off"),
    _s("d6j_moves_ungated", "  perform infrx.require_feature('lab_submission');\n  select * "
       "into s from infrx.lab_submissions\n   where external_run_id",
       "  select * into s from infrx.lab_submissions\n   where external_run_id", FLAG,
       "a submission egresses while submission is switched off"),
    _s("d6j_flag_is_feedbacks", "infrx.require_feature('lab_submission')",
       "infrx.require_feature('feedback')", FLAG,
       "enabling customer feedback also enables paid external submission", occurrences=2),
    # --- JUDGE-BUDGET: the payer's budget
    _s("d6j_foreign_payer", "  if v_parts[2] <> v_provider::text then", "  if false then",
       BUDGET, "a provider sets a budget on another provider's payer"),
    _s("d6j_any_ref_kind_payer", "  if v_parts is null or v_parts[1] <> 'payer' then",
       "  if v_parts is null then", BUDGET, "a serving ref is billed as a payer"),
    _s("d6j_limit_unattributed", "           p_args->>'actor', p_args->>'reason'",
       "           'platform', p_args->>'reason'", BUDGET,
       "a raised spending cap names no one"),
    _s("d6j_cap_unchecked", "  constraint lab_budgets_within_limit check (reserved + settled <= "
       "limit_value)", "  constraint lab_budgets_within_limit check (true)", BUDGET,
       "a limit is lowered under what is already held and spent"),
    _s("d6j_negative_limit", "  limit_value numeric(20, 8) not null check (limit_value >= 0),\n"
       "  reserved", "  limit_value numeric(20, 8) not null,\n  reserved", BUDGET,
       "a negative cap is stored"),
    _s("d6j_credit_run_prepared", "  if v_doc->'budget'->'limit'->>'unit' is distinct from "
       "'PROVIDER_USD' then", "  if false then", BUDGET,
       "a CREDIT amount is reserved against a USD budget (units mixed)"),
    _s("d6j_unfunded_payer_prepared", "  foreign key (provider_org_id, payer_ref) references "
       "infrx.lab_budgets on delete restrict,\n  -- the intent", "  -- the intent", BUDGET,
       "a run whose payer has no budget is prepared with nothing reserved"),
    # --- consent
    _s("d6j_any_purpose", "not infrx.lab_grant_current(x.grant_id, v_doc->>'purpose')",
       "not infrx.lab_grant_current(x.grant_id, null)", CONSENT,
       "content granted for training is sent to an external judge"),
    _s("d6j_no_snapshot", "           where d.dataset_ref = v_doc->>'dataset_ref') x;",
       "           where false) x;", CONSENT,
       "the consent a submission relied on is not recorded, so nothing is rechecked"),
    _s("d6j_snapshot_version_ignored", "                  and (c.grant_version <> (select "
       "max(g.version)", "                  and (false and c.grant_version <> (select "
       "max(g.version)", CONSENT,
       "a grant re-issued with another scope passes as the consent that was snapshotted"),
    _s("d6j_currency_ignored", "                       or not infrx.lab_grant_current(c.grant_id, "
       "s.purpose))) then", "                       or false)) then", CONSENT,
       "content whose grant expired after preparation is sent"),
    _s("d6j_replay_rechecks_consent", "  select * into s from infrx.lab_submissions where "
       "external_run_ref = r.ref;\n  if found then", "  select * into s from "
       "infrx.lab_submissions where false;\n  if found then", CONSENT,
       "a retried prepare after revocation loses its own submission's answer"),
    _s("d6j_unsent_work_charged", "    if v_cost > 0 and s.state <> 'submitted' then",
       "    if false then", CONSENT, "a cancelled run that never left is charged"),
    _s("d6j_release_keeps_reservation", "    update infrx.lab_budgets set reserved = reserved - "
       "s.reserved, settled", "    update infrx.lab_budgets set reserved = reserved, settled",
       CONSENT, "finished runs keep holding budget until the payer is starved"),
    # --- reserve -> intent -> ack
    _s("d6j_batch_rewritable", "    if (v_to = 'submitted' and s.external_batch_id is distinct "
       "from v_batch)", "    if (false)", INTENT,
       "a second ack re-points the run at another provider batch"),
    _s("d6j_settle_twice", "and s.settled <> v_cost) then", "and false) then", INTENT,
       "a retried settlement with another cost is accepted"),
    _s("d6j_replay_moves_again", "    return infrx.lab_submission_json(s);\n  end if;\n  -- (the "
       "guard", "  end if;\n  -- (the guard", INTENT,
       "a retried ack after a lost answer fails as a bad transition"),
    _s("d6j_ack_without_batch", "    if v_batch is null then", "    if false then", INTENT,
       "an ack with no batch id is a raw constraint error"),
    _s("d6j_cost_over_reservation", "    if v_cost < 0 or v_cost > s.reserved then",
       "    if v_cost < 0 then", INTENT, "a run spends more than it reserved"),
    _s("d6j_credit_cost", "  if p_args ? 'cost' and p_args->'cost'->>'unit' is distinct from "
       "'PROVIDER_USD' then", "  if false then", INTENT,
       "a CREDIT cost is settled against a USD budget"),
    _s("d6j_completed_free", "    if v_to = 'completed' and not p_args ? 'cost' then",
       "    if false then", INTENT, "completed paid work settles nothing"),
    _s("d6j_rpc_move_unchecked", "  if not infrx.lab_submission_may(s.state, v_to) then",
       "  if false then", INTENT,
       "an undeclared settlement move runs the budget negative before being refused"),
    _s("d6j_moves_unguarded", "  if new.state is distinct from old.state\n     and not "
       "infrx.lab_submission_may(old.state, new.state) then", "  if false then", INTENT,
       "a direct write moves a finished submission back to submitting"),
    # --- ambiguous
    _s("d6j_ambiguous_resubmits", "  if v_to = 'submitting' and s.state in ('submitting', "
       "'ambiguous') then", "  if v_to = 'submitting' and s.state in ('submitting') then",
       AMBIGUOUS, "an ambiguous submit is retried as a state error, inviting a blind resubmit"),
    _s("d6j_quarantine_without_reason", "    if p_args->>'reason' is null then",
       "    if false then", AMBIGUOUS, "a quarantine records no reason"),
    # --- LAB-ACCESS: other providers
    _s("d6j_prepare_any_provider", "     and provider_org_id = v_provider and kind = "
       "'external_run';", "     and kind = 'external_run';", OTHER,
       "a provider prepares (and reads) another provider's external run"),
    _s("d6j_move_any_provider", "     and provider_org_id = (p_args->>'provider_org_id')::uuid "
       "for update;", "     for update;", OTHER, "a provider submits another provider's run"),
    _s("d6j_read_any_provider", "     and provider_org_id = (p_args->>'provider_org_id')::uuid;\n"
       "  if not found then\n    perform infrx.refuse('not_found', 'no such external run",
       "     ;\n  if not found then\n    perform infrx.refuse('not_found', 'no such external run",
       OTHER, "a provider reads another provider's submission"),
    _s("d6j_budget_any_provider", "   where provider_org_id = (p_args->>'provider_org_id')::uuid\n"
       "     and payer_ref = p_args->>'payer_ref';", "   where payer_ref = p_args->>'payer_ref';",
       OTHER, "a provider reads another provider's budget"),
    _s("d6j_squat_replays", "    select * into s from infrx.lab_submissions where "
       "external_run_ref = r.ref;\n    if not found", "    select * into s from "
       "infrx.lab_submissions where external_run_id = r.object_id;\n    if not found", OTHER,
       "a provider publishing a run under another's id reads that provider's submission"),
    # --- contention
    _s("d6j_reservation_not_held", "    update infrx.lab_budgets set reserved = reserved + "
       "v_amount, updated_at", "    update infrx.lab_budgets set updated_at", RACE,
       "concurrent runs are prepared past the payer's cap"),
    _s("d6j_intent_unlocked", "     and provider_org_id = (p_args->>'provider_org_id')::uuid "
       "for update;", "     and provider_org_id = (p_args->>'provider_org_id')::uuid;",
       ONE_INTENT, "two racing submitters each record an intent and both egress"),
    # --- R161
    _s("d6j_extra_move", "('submitted', 'completed'), ('submitted', 'failed'), ('submitted', "
       "'cancelled'))", "('submitted', 'completed'), ('submitted', 'failed'), ('submitted', "
       "'cancelled'), ('submitted', 'submitting'))", STATES,
       "an accepted run can be submitted again"),
    _s("d6j_settled_misreported", "'reserved', s.reserved::text, 'settled', s.settled::text,",
       "'reserved', s.reserved::text, 'settled', s.reserved::text,", STORE,
       "the store reports the reservation as the spend"),
)
SQL_NAMES = tuple(m.name for m in SQL_MUTANTS)

RUNNER = Runner(name="d6j", targets=("tests/d/test_d6j_units.py",))
F = "state/lab_consent.py"
CALLS = "test_calls__carry_the_callers_provider_and_only_the_fields_given"
TYPED = "test_refusals__are_typed_so_a_duplicate_submit_is_never_retried_blind"
JUDGE_UNITS = "test_judge__sends_the_ports_fields_and_types_the_answers"
JUDGE_RUNS_IN_UNITS = "test_judge__runs_in_sends_the_providers_states_and_limit"


def _p(name, invariant, old, new, *cases, file=F, **kw) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases, **kw)


CODE_MUTANTS = (
    _p("d6j_py_prepare_unscoped", "a preparation is the caller's provider's",
       '"provider_org_id": provider_org_id, "external_run_ref": external_run_ref,',
       '"external_run_ref": external_run_ref,', CALLS),
    _p("d6j_py_budget_unscoped", "a budget read is the caller's provider's",
       'return await self._call("lab_budget", {"provider_org_id": provider_org_id,\n'
       '                                               "payer_ref": payer_ref})',
       'return await self._call("lab_budget", {"payer_ref": payer_ref})', CALLS),
    _p("d6j_py_none_fields_sent", "only the fields given are sent",
       "**{k: v for k, v in fields.items() if v is not None}}", "**fields}", CALLS),
    _p("d6j_py_begin_is_an_ack", "the intent is the submitting move",
       'await self._move(external_run_id, "submitting", provider_org_id)',
       'await self._move(external_run_id, "submitted", provider_org_id)', CALLS),
    _p("d6j_py_ack_without_batch", "an ack names its batch",
       '                                external_batch_id=external_batch_id)', ')', CALLS),
    _p("d6j_py_quarantine_without_reason", "a quarantine names its reason",
       'provider_org_id, reason=reason)', 'provider_org_id)', CALLS),
    _p("d6j_py_finish_drops_cost", "a settlement carries its cost",
       'return await self._move(external_run_id, state, provider_org_id, cost=cost)',
       'return await self._move(external_run_id, state, provider_org_id)', CALLS),
    _p("d6j_py_limit_unattributed", "a limit names its actor and reason",
       '"actor": actor, "reason": reason})', '"reason": reason})', CALLS),
    _p("d6j_py_judge_max_cost_unit", "a reservation is the PROVIDER_USD worst case",
       '"price_version": price_version, "max_cost": str(max_cost.raw("PROVIDER_USD"))}))',
       '"price_version": price_version, "max_cost": "0"}))', JUDGE_UNITS),
    _p("d6j_py_judge_consent_version", "the snapshot is the grant AND its version",
       '"grant_id": consent.grant_id, "grant_version": consent.version,',
       '"grant_id": consent.grant_id, "grant_version": None,', JUDGE_UNITS),
    _p("d6j_py_judge_rejected_accepted", "a rejection is stored as not accepted",
       '"accepted": hasattr(r, "scores")', '"accepted": True', JUDGE_UNITS),
    _p("d6j_py_judge_overrun_swallowed", "an overrun settlement raises",
       '        if "refused" in answer:', '        if False:', JUDGE_UNITS),
    _p("d6j_py_judge_sent_ids_dropped", "the samples that left are read back",
       '        sent_ids=tuple(doc["sent_sample_ids"]))', '        sent_ids=())', JUDGE_UNITS),
    _p("d6j_py_judge_created_lost", "only the creator of the intent egresses",
       '        return _run(answer["run"]), answer["created"]',
       '        return _run(answer["run"]), True', JUDGE_UNITS),
    _p("d6j_py_judge_runs_in_unscoped", "a run listing is the caller's provider's",
       '"provider_org_id": provider_org_id, "states": list(states), "limit": limit})]',
       '"states": list(states), "limit": limit})]', JUDGE_RUNS_IN_UNITS),
    _p("d6j_py_untyped_refusal", "a SQL refusal is its typed error",
       "function, args, error=domain_error)", "function, args, error=None)", TYPED,
       file="state/lab_data.py"),
)


def kill(mutant) -> tuple[str, str]:
    return d7.kill(mutant, DB, t)


# --- SR-J2-1: the judge ledger, `0036_lab_judge_ledger.sql` (test_d6j_judge's world) ------
JUDGE_FILE = "0036_lab_judge_ledger.sql"
J_ROLES = "check_browser_roles_reach_nothing"
J_FLAG = "check_the_ledger_is_off_without_its_flag"
J_CONSENT = "check_a_reservation_needs_the_current_external_judging_consent"
J_BUDGET = "check_the_budget_is_the_payers_one_provider_usd_cap"
J_INTENT = "check_one_intent_then_the_samples_that_left_then_the_ack"
J_REVOKED = "check_a_revocation_between_consent_and_submit_stops_egress"
J_AMBIGUOUS = "check_an_ambiguous_submit_is_quarantined_never_resubmitted"
J_RESULTS = "check_results_are_stored_once_and_settled_once"
J_RACE = "check_concurrent_reservations_never_exceed_the_budget"
J_ONE = "check_a_duplicate_submit_under_contention_creates_one_intent"
J_STORE = "check_the_store_composes"
J_RUNS_IN = "check_runs_in_lists_this_providers_runs_by_state_oldest_first"
RUNS_IN_FILE = "0049_lab_judge_runs_in.sql"


def _j(name, old, new, check, why, file=JUDGE_FILE, **kw):
    return _d.Mutant(name, file, old, new, "lab", check, why, **kw)


JUDGE = (
    _j("d6jj_service_writes", "execute format('grant select on infrx.%I to service_role', t);",
       "execute format('grant select, delete on infrx.%I to service_role', t);", J_ROLES,
       "the platform role erases judge holds and results"),
    _j("d6jj_flag_ignored", "  perform infrx.require_feature('lab_submission');\n  select * "
       "into r from infrx.lab_judge_runs where run_id = v_run;", "  select * into r from "
       "infrx.lab_judge_runs where run_id = v_run;", J_FLAG,
       "a judge run reserves budget while the Lab submission flag is off"),
    _j("d6jj_moves_ignore_flag", "  perform infrx.require_feature('lab_submission');\n  select "
       "* into r from infrx.lab_judge_runs where run_id = p_run for update;", "  select * into "
       "r from infrx.lab_judge_runs where run_id = p_run for update;", J_FLAG,
       "a run is submitted while the flag is off"),
    _j("d6jj_consent_any_version", "  select coalesce((select g.version = p_version and "
       "g.recipient_provider_org_id = p_provider", "  select coalesce((select "
       "g.recipient_provider_org_id = p_provider", J_CONSENT,
       "a run is reserved under a superseded (narrower or revoked) grant version"),
    _j("d6jj_consent_any_provider", "g.version = p_version and g.recipient_provider_org_id = "
       "p_provider\n", "g.version = p_version\n", J_CONSENT,
       "a provider judges under another provider's grant"),
    _j("d6jj_consent_question_only", "                          and g.categories @> "
       "array['request_content', 'response_content']", "                          and "
       "g.categories && array['request_content', 'response_content']", J_CONSENT,
       "the answers leave under a grant for the questions only"),
    _j("d6jj_consent_any_purpose", "     and infrx.lab_grant_current(p_grant, "
       "'external_judging')", "     and infrx.lab_grant_current(p_grant, null)", J_CONSENT,
       "a training-only grant sends content to an external judge"),
    _j("d6jj_reserve_foreign_replay", "    if r.provider_org_id <> v_provider then\n      "
       "perform infrx.refuse('not_found', 'no such judge run');\n    end if;\n    return "
       "infrx.lab_judge_json(r);                   -- a replay", "    return "
       "infrx.lab_judge_json(r);                   -- a replay", J_BUDGET,
       "another provider reads a judge run by guessing its id"),
    _j("d6jj_budget_unheld", "    update infrx.lab_budgets set reserved = reserved + v_cost, "
       "updated_at = infrx.now()\n     where (provider_org_id, payer_ref) = (v_provider, "
       "r.payer_ref);", "    null;", J_BUDGET, "a judge run holds nothing: the cap is fiction"),
    _j("d6jj_budget_raw", "  exception when check_violation then\n    perform infrx.refuse("
       "'budget_exceeded', 'the reservation exceeds", "  exception when raise_exception then\n"
       "    perform infrx.refuse('budget_exceeded', 'the reservation exceeds", J_BUDGET,
       "an over-budget reservation is a 500"),
    _j("d6jj_duplicate_samples", "    check (cardinality(sample_ids) >= 1 and "
       "infrx.lab_distinct(sample_ids)),", "    check (cardinality(sample_ids) >= 1),",
       J_BUDGET, "a sample is sent (and paid for) twice"),
    _j("d6jj_every_caller_creates", "  if r.state <> 'prepared' then\n    return "
       "jsonb_build_object('run', infrx.lab_judge_json(r), 'created', false);",
       "  if r.state not in ('prepared', 'submitting') then\n    return "
       "jsonb_build_object('run', infrx.lab_judge_json(r), 'created', false);", J_INTENT,
       "a double click egresses twice"),
    _j("d6jj_foreign_samples_sent", "  if cardinality(v_ids) = 0 or not v_ids <@ "
       "r.sample_ids then", "  if cardinality(v_ids) = 0 then", J_INTENT,
       "content of samples outside the run leaves"),
    _j("d6jj_sent_rewritten", "  if r.sent_sample_ids is not null then\n    if "
       "r.sent_sample_ids = v_ids then", "  if false then\n    if r.sent_sample_ids = v_ids "
       "then", J_INTENT, "the record of what left is rewritten after egress"),
    _j("d6jj_ack_any_batch", "    perform infrx.refuse('idempotency_conflict', 'the run was "
       "accepted as another batch');", "    null;", J_INTENT,
       "a second batch id overwrites the one the provider bills"),
    _j("d6jj_sent_without_consent", "  if not infrx.lab_judge_consent_current("
       "r.provider_org_id, r.grant_id, r.grant_version) then", "  if false then", J_REVOKED,
       "content leaves after the grantor revoked"),
    _j("d6jj_sweep_everything", "     where state = 'submitting'\n       and updated_at < "
       "infrx.now() - make_interval(secs => (p_args->>'older_than_s')::int)", "     where "
       "state = 'submitting'", J_REVOKED, "a live worker's submission is quarantined"),
    _j("d6jj_sweep_unaudited", "  select run_id, 'lease_expired', 'the submitting worker went "
       "silent' from expired;", "  select run_id, 'lease_expired', 'the submitting worker "
       "went silent' from expired where false;", J_REVOKED,
       "a swept run has no record of why it is ambiguous"),
    _j("d6jj_quarantine_releases", "  update infrx.lab_judge_runs set state = 'ambiguous' "
       "where run_id = r.run_id\n  returning * into r;", "  update infrx.lab_judge_runs set "
       "state = 'ambiguous' where run_id = r.run_id\n  returning * into r;\n  update "
       "infrx.lab_budgets set reserved = reserved - r.reserved where (provider_org_id, "
       "payer_ref) = (r.provider_org_id, r.payer_ref);", J_AMBIGUOUS,
       "an unknown outcome frees the budget the provider may still bill"),
    _j("d6jj_quarantine_unaudited", "  insert into infrx.lab_judge_audit (run_id, event, "
       "reason)\n  values (r.run_id, 'quarantine', p_args->>'reason');", "", J_AMBIGUOUS,
       "a quarantine leaves no record"),
    _j("d6jj_release_to_any_state", "  if v_to is null or v_to not in ('failed', "
       "'cancelled') then", "  if v_to is null then", J_AMBIGUOUS,
       "a 'release' to submitted frees the hold of a batch the provider may bill"),
    _j("d6jj_release_undeclared", "  execute 'create or replace trigger lab_judge_runs_state "
       "before update on '", "  execute 'create or replace trigger lab_judge_runs_state "
       "before insert on '", J_AMBIGUOUS, "an ambiguous run is cancelled, forgetting its batch"),
    _j("d6jj_release_keeps_hold", "  update infrx.lab_budgets set reserved = reserved - "
       "r.reserved, updated_at = infrx.now()\n   where (provider_org_id, payer_ref) = "
       "(r.provider_org_id, r.payer_ref);\n  update infrx.lab_judge_runs set state = v_to",
       "  update infrx.lab_judge_runs set state = v_to", J_AMBIGUOUS,
       "a failed run keeps its hold forever"),
    _j("d6jj_release_repeats", "  if r.state = v_to then\n    return infrx.lab_judge_json(r);",
       "  if false then\n    return infrx.lab_judge_json(r);", J_AMBIGUOUS,
       "a retried release frees the hold a second time, taking it from other runs"),
    _j("d6jj_results_unsent", "  if exists (select 1 from jsonb_array_elements("
       "p_args->'results') x\n              where not", "  if false and exists (select 1 from "
       "jsonb_array_elements(p_args->'results') x\n              where not", J_RESULTS,
       "a score is stored for a sample the judge never saw"),
    _j("d6jj_results_not_idempotent", "  on conflict (run_id, sample_id, rubric_version) do "
       "nothing;", ";", J_RESULTS,
       "a repeated poll fails collection instead of ignoring the results already stored"),
    _j("d6jj_settle_twice", "  if r.state = 'completed' then\n    return "
       "infrx.lab_judge_json(r);                   -- settled once", "  if false then\n    "
       "return infrx.lab_judge_json(r);                   -- settled once", J_RESULTS,
       "a late poll settles the run a second time"),
    _j("d6jj_settle_overrun", "  if v_actual > r.reserved then", "  if false then", J_RESULTS,
       "a provider bills past the reservation unnoticed"),
    _j("d6jj_overrun_unaudited", "    insert into infrx.lab_judge_audit (run_id, event, "
       "reason)\n    values (r.run_id, 'settle_refused', 'billed ' || v_actual || ' over the "
       "reservation '\n            || r.reserved);\n", "", J_RESULTS,
       "an overrun leaves no record for the operator"),
    _j("d6jj_settle_keeps_hold", "  update infrx.lab_budgets set reserved = reserved - "
       "r.reserved, settled = settled + v_actual,", "  update infrx.lab_budgets set reserved "
       "= reserved, settled = settled + v_actual,", J_RESULTS,
       "a completed run keeps holding its worst case"),
    _j("d6jj_budget_unlocked", "  exception when check_violation then\n    perform "
       "infrx.refuse('budget_exceeded', 'the reservation exceeds the payer''s '",
       "  exception when check_violation then\n    return infrx.lab_judge_json(r);\n    "
       "perform infrx.refuse('budget_exceeded', 'the reservation exceeds the payer''s '",
       J_RACE, "an over-cap run is prepared without a hold"),
    _j("d6jj_intent_unlocked", "  select * into r from infrx.lab_judge_runs where run_id = "
       "p_run for update;", "  select * into r from infrx.lab_judge_runs where run_id = "
       "p_run;", J_ONE, "two racing workers are each told they created the intent"),
    _d.Mutant("d6jj_reserved_misreported", "0042_lab_d8_ledgers.sql", "    'price_version', "
              "r.price_version, 'reserved', "
       "r.reserved::text,", "    'price_version', r.price_version, 'reserved', "
       "coalesce(r.actual, 0)::text,", "lab", J_STORE, "the port reports no hold"),
    # (0042, D8 SR-P2-1, redefines lab_judge_json with purpose and dataset_ref: the anchor
    # follows the live body)
    # --- WR-LSQ-C2A: the collect/reconcile listing, `0049_lab_judge_runs_in.sql` -----------
    _j("d6jc2a_empty_states_unbounded", "  if cardinality(v_states) = 0 then\n    perform "
       "infrx.refuse('invalid_request', 'a judge run listing names at least one state');\n"
       "  end if;\n", "", J_RUNS_IN, "a worker asking for every state gets an unbounded scan",
       file=RUNS_IN_FILE),
    _j("d6jc2a_any_provider", "           where provider_org_id = (p_args->>'provider_org_id')"
       "::uuid\n", "           where true\n", J_RUNS_IN,
       "a worker reads another provider's judge runs", file=RUNS_IN_FILE),
    _j("d6jc2a_any_state", "             and state = any(v_states)\n", "", J_RUNS_IN,
       "collect and reconcile are handed runs in the wrong state", file=RUNS_IN_FILE),
)
JUDGE_NAMES = tuple(m.name for m in JUDGE)


def kill_judge(mutant) -> tuple[str, str]:
    return d7.kill(mutant, DB, judge_world)


# --- SR-C3L-1: the Lab's judge doors, `0037_lab_judge_doors.sql` (test_d6j_doors' world) ----
DOORS_FILE = "0037_lab_judge_doors.sql"
D_ROLES = "check_only_current_members_at_the_right_role_pass_a_door"
D_CONFIG = "check_a_configuration_needs_the_grantors_current_judging_grant"
D_BUDGET = "check_the_budget_door_writes_the_administrators_own_payer"
D_REQUEST = "check_a_run_request_is_idempotent_and_foreign_ids_are_unknown"
D_LABELS = "check_calibration_is_the_providers_own_labels_in_bounded_pages"
D_RACE = "check_a_concurrent_double_click_queues_one_run"


def _k(name, old, new, check, why, **kw):
    return _d.Mutant(name, DOORS_FILE, old, new, "lab", check, why, **kw)


DOORS = (
    _k("c3l_doors_ignore_flag", "  perform infrx.require_feature('lab_submission');\n  if not "
       "exists", "  if not exists", D_ROLES,
       "the launched App's database answers Lab judge doors before the Lab is enabled"),
    _k("c3l_any_member_role", "                    and array_position(array['viewer', "
       "'developer', 'administrator'], m.role)\n                        >= array_position("
       "array['viewer', 'developer', 'administrator'], p_min))", "                    )",
       D_ROLES, "a viewer queues paid judge runs and an developer sets the budget"),
    _k("c3l_revoked_member_passes", "                    and (m.revoked_at is null or "
       "infrx.now() < m.revoked_at)\n                    and array_position", "\n"
       "                    and array_position", D_ROLES,
       "a removed member keeps spending the provider's judge budget"),
    _k("c3l_anon_executes", "  public.lab_judge_calibration(uuid, uuid, int) from public, "
       "anon;", "  public.lab_judge_calibration(uuid, uuid, int) from public;\ngrant execute "
       "on function public.lab_judge_calibration(uuid, uuid, int) to anon;", D_ROLES,
       "an anonymous caller reaches the door"),
    _k("c3l_grant_any_model", "                          and p_model::text = any(g.model_ids)\n",
       "", D_CONFIG, "a provider judges a model the grantor never shared"),
    _k("c3l_grant_question_only", "                          and g.categories @> "
       "array['request_content', 'response_content']\n                          and "
       "'external_judging'", "                          and g.categories && "
       "array['request_content', 'response_content']\n                          and "
       "'external_judging'", D_CONFIG, "answers leave under a grant for questions only"),
    _k("c3l_grant_any_purpose", "                          and 'external_judging' = any("
       "g.purposes)\n", "", D_CONFIG, "a training-only grant configures an external judge"),
    _k("c3l_grant_expiry_ignored", "                          and (g.expires_at is null or "
       "infrx.now() < g.expires_at)\n                          and p_model", "\n"
       "                          and p_model", D_CONFIG,
       "an expired grant still configures a judge"),
    _k("c3l_grant_revocation_ignored", "                          and g.revoked_at is null\n",
       "", D_CONFIG, "a revoked grant still configures a judge"),
    _k("c3l_grant_old_version", "                    order by g.version desc limit 1), false)",
       "                    order by g.version asc limit 1), false)", D_CONFIG,
       "the first grant version decides forever"),
    _k("c3l_config_unattributed", "      p_rubric_version, p_sample_size, auth.uid())",
       "      p_rubric_version, p_sample_size, p_provider_org_id)", D_CONFIG,
       "the configuration does not record who made it"),
    _k("c3l_budget_any_payer", "  if (infrx.lab_ref_parts(p_payer_ref))[1:2] is distinct from\n"
       "     array['payer', p_provider_org_id::text] then\n    raise exception 'a budget",
       "  if false then\n    raise exception 'a budget", D_BUDGET,
       "an administrator writes another provider's payer budget"),
    _k("c3l_budget_any_unit", "  if p_limit->>'unit' is distinct from 'PROVIDER_USD' then",
       "  if false then", D_BUDGET, "a CREDIT figure is taken as a PROVIDER_USD budget"),
    _k("c3l_budget_unattributed", "    'payer_ref', p_payer_ref, 'limit', p_limit->>'value', "
       "'actor', auth.uid(),", "    'payer_ref', p_payer_ref, 'limit', p_limit->>'value', "
       "'actor', 'lab',", D_BUDGET, "the limit history does not name the administrator"),
    _k("c3l_request_foreign_run", "    if r.provider_org_id <> p_provider_org_id then\n      "
       "raise exception 'no such judge run' using errcode = 'P0002';\n    end if;\n  else",
       "  else", D_REQUEST, "another provider's run id answers that provider's request"),
    _k("c3l_request_foreign_config", "     where config_id = p_config_id and provider_org_id = "
       "p_provider_org_id;", "     where config_id = p_config_id;", D_REQUEST,
       "a provider queues a run on another provider's configuration and grant"),
    _k("c3l_request_any_payer", "      raise exception 'a run is paid by this provider''s own "
       "payer' using errcode = '42501';", "      null;", D_REQUEST,
       "a run is queued against another provider's payer"),
    _k("c3l_request_after_revocation", "    if not infrx.lab_judge_granted(p_provider_org_id, "
       "c.grantor_org_id, c.model_id) then", "    if false then", D_REQUEST,
       "a run is queued after the grantor revoked"),
    _k("c3l_double_click_twice", "    on conflict (run_id) do nothing\n    returning * into r;",
       "    returning * into r;", D_RACE, "a concurrent double click is a 500 for one tab"),
    _k("c3l_labels_any_provider", "           where r.provider_org_id = p_provider_org_id\n",
       "           where true\n", D_LABELS, "a provider reads another provider's judge labels"),
    _k("c3l_labels_no_keyset", "             and (p_after is null or l.label_id > p_after)\n",
       "", D_LABELS, "the next page repeats the first"),
    _k("c3l_labels_unbounded", "           limit least(greatest(coalesce(p_limit, 50), 0), 50)) "
       "x);", "           limit greatest(coalesce(p_limit, 50), 0)) x);", D_LABELS,
       "one call reads every label"),
    _k("c3l_labels_after_revocation", "             and infrx.lab_grant_current(r.grant_id, "
       "'external_judging')\n", "", D_LABELS,
       "a provider keeps reading labels about a revoked grantor's content"),
    _k("c3l_labels_carry_rationale", "            'accepted', x.accepted, 'overall_pass', "
       "x.result->'overall_pass',", "            'accepted', x.accepted, 'result', x.result, "
       "'overall_pass', x.result->'overall_pass',", D_LABELS,
       "the judge's rationale and notes about the grantor's content reach the provider"),
    _k("c3l_scores_carry_rationale", "            'scores', (select coalesce(jsonb_agg("
       "jsonb_build_object('name', s->'name',\n", "            'scores', (select coalesce("
       "jsonb_agg(s || jsonb_build_object('name', s->'name',\n", D_LABELS,
       "each criterion's rationale reaches the provider"),
)
DOORS_NAMES = tuple(m.name for m in DOORS)


def kill_doors(mutant) -> tuple[str, str]:
    from . import test_d6j_doors as doors_world
    return d7.kill(mutant, DB, doors_world)


def run_code_mutant(mutant):
    return shared.run_mutant(mutant, RUNNER)
