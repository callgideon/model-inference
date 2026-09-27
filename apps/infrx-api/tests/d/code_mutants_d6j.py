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
    _p("d6j_py_untyped_refusal", "a SQL refusal is its typed error",
       "            raise domain_error(failed) from None", "            raise", TYPED,
       file="state/lab_data.py"),
)


def kill(mutant) -> tuple[str, str]:
    return d7.kill(mutant, DB, t)


def run_code_mutant(mutant):
    return shared.run_mutant(mutant, RUNNER)
