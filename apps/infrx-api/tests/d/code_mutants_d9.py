"""R32/R40 for D9: single-edit defects of `0033_lab_rollout.sql` (killed by the named check of
`test_d9_rollout.py` on a database built from the mutated set, needs Docker) and of
`infrx/state/lab_rollout.py` (killed by the named case of `test_d9_units.py` through the shared
runner, no Docker). The SQL runner is D7's (`code_mutants_d7.kill`).

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_code_mutants_d9.py
"""
from __future__ import annotations

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Runner
from . import code_mutants_d7 as d7
from . import migration_mutants as _d
from . import pgharness
from . import test_d9_rollout as t

FILE = "0033_lab_rollout.sql"
DB = f"{pgharness.DATABASE}_d9mut"

ROLES = "check_browser_roles_reach_nothing"
START = "check_policies_are_validated_and_one_live_per_endpoint"
MOVES = "check_transitions_are_fenced_and_decisions_are_history"
ASSIGN = "check_assignment_is_stable_and_is_the_contracts_cohort"
PINS = "check_explicit_pins_are_honoured"
OTHER = "check_other_providers_reach_nothing"
RACE = "check_concurrent_moves_make_one_decision"
RETRY = "check_repeat_assignment_under_contention"
STORE = "check_the_store_composes"


def _s(name, old, new, check, why, **kw):
    return _d.Mutant(name, FILE, old, new, "lab", check, why, **kw)


SQL_MUTANTS = (
    # --- DUR-RLS
    _s("d9_service_writes", "execute format('grant select on infrx.%I to service_role', t);",
       "execute format('grant select, delete on infrx.%I to service_role', t);", ROLES,
       "the platform role rewrites rollout history around the RPCs"),
    _s("d9_row_security_off", "execute format('alter table infrx.%I enable row level security'",
       "execute format('alter table infrx.%I disable row level security'", ROLES,
       "a future browser grant exposes every provider's experiments"),
    # --- D9.a policies
    _s("d9_overweight", "        from jsonb_array_elements(v_doc->'candidates') c) > 10000",
       "        from jsonb_array_elements(v_doc->'candidates') c) > 20000", START,
       "a policy allocates more than all traffic to candidates"),
    _s("d9_shadow_serves", "     or (v_doc->>'mode' <> 'canary' and exists (",
       "     or (false and exists (", START, "a shadow policy serves candidate output"),
    _s("d9_foreign_policy", "   where r.ref = p_ref and r.provider_org_id = p_provider and "
       "r.kind = 'policy';", "   where r.ref = p_ref and r.kind = 'policy';", START,
       "a provider runs another provider's policy"),
    _s("d9_two_live_per_endpoint", "create unique index if not exists "
       "lab_rollouts_one_live_per_endpoint\n  on infrx.lab_rollouts (endpoint_id) where state "
       "in ('running', 'paused');", "create index if not exists "
       "lab_rollouts_one_live_per_endpoint\n  on infrx.lab_rollouts (endpoint_id) where state "
       "in ('running', 'paused');", START,
       "two policies split one endpoint's traffic past 100%"),
    _s("d9_stopped_blocks_endpoint", "where state in ('running', 'paused');",
       "where state in ('running', 'paused', 'stopped');", START,
       "a stopped experiment blocks the endpoint forever"),
    _s("d9_start_unattributed", "      (p_args->>'decided_by')::uuid, p_args->>'reason');\n  "
       "exception when unique_violation", "      '00000000-0000-4000-8000-000000000000', "
       "p_args->>'reason');\n  exception when unique_violation", START,
       "a start names no deciding user"),
    # --- ROLLOUT-RECOVER: fenced transitions and decisions
    _s("d9_fence_ignored", "  if o.fence is distinct from (p_args->>'fence')::bigint then",
       "  if false then", MOVES, "a stale publisher's move lands over a newer decision"),
    _s("d9_fence_not_bumped", "  update infrx.lab_rollouts set state = v_to, policy_ref = v_ref, "
       "fence = o.fence + 1,", "  update infrx.lab_rollouts set state = v_to, policy_ref = v_ref, "
       "fence = o.fence,", MOVES, "a fence never goes stale, so racing moves all land"),
    _s("d9_expand_paused", "    when v_action = 'expand' and o.state = 'running' then 'running'",
       "    when v_action = 'expand' and o.state in ('running', 'paused') then 'running'", MOVES,
       "a held experiment is expanded (and silently resumed)"),
    _s("d9_terminal_resumes", "    when v_action = 'resume' and o.state = 'paused' then 'running'",
       "    when v_action = 'resume' and o.state <> 'running' then 'running'", MOVES,
       "a rolled-back experiment comes back"),
    _s("d9_expand_without_evidence", "    if cardinality(v_evidence) = 0 or exists (",
       "    if exists (", MOVES, "an experiment expands on missing evidence"),
    _s("d9_any_record_is_evidence", "           select 1 from infrx.lab_records r where r.ref = x "
       "and r.kind = 'run'", "           select 1 from infrx.lab_records r where r.ref = x",
       MOVES, "a policy record is cited as evaluation evidence"),
    _s("d9_expand_to_any_version", "       or (v_doc->>'version')::int <= (select",
       "       or (v_doc->>'version')::int < (select", MOVES,
       "an 'expansion' re-applies the version already in force"),
    _s("d9_expand_to_other_policy", "    if (v_doc->>'policy_id')::uuid <> o.policy_id\n       or",
       "    if false\n       or", MOVES, "one policy's rollout switches to another policy"),
    _s("d9_decision_unrecorded", "      case v_action when 'expand' then 'expand' when 'pause' "
       "then 'hold'", "      case v_action when 'expand' then 'expand' when 'pause' then null",
       MOVES, "a hold is not recorded as a decision"),
    _s("d9_evidence_dropped", "      case when v_action = 'expand' then v_evidence else '{}' end,",
       "      '{}',", MOVES, "an expansion's evidence is not kept with its decision"),
    _s("d9_history_editable", "  foreach t in array array['lab_rollout_events', "
       "'lab_rollout_assignments'] loop", "  foreach t in array array["
       "'lab_rollout_assignments'] loop", MOVES, "a rollback decision is edited away"),
    # --- ROLLOUT-PIN: assignment
    _s("d9_bucket_off_contract", "  v_bucket := (('x' || substr(v_digest, 1, 8))::bit(32)::bigint "
       "% 10000)::int;", "  v_bucket := (('x' || substr(v_digest, 1, 7))::bit(28)::bigint "
       "% 10000)::int;", ASSIGN, "SQL and the contract put a subject in different cohorts"),
    _s("d9_digest_by_version", "  v_digest := encode(sha256(convert_to((v_doc->>'policy_id') || "
       "E'\\n'", "  v_digest := encode(sha256(convert_to((v_doc->>'version') || E'\\n'",
       ASSIGN, "every policy version reshuffles the cohorts"),
    _s("d9_cumulative_weights_lost", "                                with ordinality w(value, m) "
       "where w.m <= c.n)", "                                with ordinality w(value, m) "
       "where w.m = c.n)", ASSIGN, "later candidates get the wrong share"),
    _s("d9_retry_reassigned", "  if a.request_id is null then", "  if false then", ASSIGN,
       "a retried request gets no answer (and is re-routed by its caller)"),
    _s("d9_rolled_back_still_canary", "    coalesce(v_explicit, case when o.state = 'running' "
       "then (", "    coalesce(v_explicit, case when o.state <> 'paused' then (", ASSIGN,
       "a rolled-back experiment keeps sending new requests to the candidate"),
    _s("d9_subject_stored", "  values (o.policy_id, (p_args->>'request_id')::uuid, o.policy_ref, "
       "'sha256:' || v_digest,", "  values (o.policy_id, (p_args->>'request_id')::uuid, "
       "o.policy_ref || (p_args->>'subject_key'), 'sha256:' || v_digest,", ASSIGN,
       "a customer account id is stored in a provider-visible record"),
    # --- explicit pins
    _s("d9_pin_ignored", "    coalesce(v_explicit, case when", "    coalesce(null, case when",
       PINS, "an explicitly pinned client is moved into the experiment"),
    _s("d9_pin_any_provider", "  if v_explicit is not null and (v_parts[1] is distinct from "
       "'serving'\n                                 or v_parts[2] is distinct from "
       "o.provider_org_id::text) then", "  if v_explicit is not null and (v_parts[1] is "
       "distinct from 'serving') then", PINS, "a request is pinned to another provider's model"),
    _s("d9_pin_any_kind", "(v_parts[1] is distinct from 'serving'\n", "(false\n", PINS,
       "a request is pinned to an evaluator ref"),
    _s("d9_pin_marked_cohort", "    case when v_explicit is null then 'cohort' else 'explicit' end)",
       "    'cohort')", PINS, "an explicit pin is recorded as a cohort draw"),
    # --- LAB-ACCESS
    _s("d9_move_any_provider", "     and provider_org_id = (p_args->>'provider_org_id')::uuid "
       "for update;", "     for update;", OTHER, "a provider rolls back another's experiment"),
    _s("d9_assign_any_provider", "     and provider_org_id = (p_args->>'provider_org_id')::uuid "
       "for share;", "     for share;", OTHER, "a provider draws cohorts from another's policy"),
    _s("d9_read_any_provider", "  select * into o from infrx.lab_rollouts where policy_id = "
       "(p_args->>'policy_id')::uuid\n     and provider_org_id = (p_args->>'provider_org_id')::uuid;",
       "  select * into o from infrx.lab_rollouts where policy_id = "
       "(p_args->>'policy_id')::uuid;", OTHER, "a provider reads another's experiment"),
    # --- races
    _s("d9_moves_unlocked", "     and provider_org_id = (p_args->>'provider_org_id')::uuid "
       "for update;", "     and provider_org_id = (p_args->>'provider_org_id')::uuid;", RACE,
       "racing publishers each record a decision at one fence"),
    _s("d9_retry_race_raw", "  on conflict (policy_id, request_id) do nothing\n", "\n", RETRY,
       "a retry racing the first assignment fails instead of getting its answer"),
    _s("d9_json_fence_misreported", "    'policy_ref', o.policy_ref, 'state', o.state, 'fence', "
       "o.fence,", "    'policy_ref', o.policy_ref, 'state', o.state, 'fence', o.fence - 1,",
       STORE, "a caller reads a fence that is already stale"),
)
SQL_NAMES = tuple(m.name for m in SQL_MUTANTS)

RUNNER = Runner(name="d9", targets=("tests/d/test_d9_units.py",))
F = "state/lab_rollout.py"
CALLS = "test_calls__carry_the_provider_the_fence_and_expansion_evidence_only"


def _p(name, invariant, old, new, *cases, **kw) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=F, old=old, new=new, cases=cases, **kw)


CODE_MUTANTS = (
    _p("d9_py_move_without_fence", "a move carries its fence",
       '"policy_id": policy_id, "fence": fence,', '"policy_id": policy_id,', CALLS),
    _p("d9_py_evidence_on_every_move", "only an expansion carries a policy and evidence",
       '        if action == "expand":\n', '        if True:\n', CALLS),
    _p("d9_py_expansion_without_evidence", "an expansion carries its evidence",
       'args.update(policy_ref=policy_ref, evidence_refs=list(evidence_refs or ()))',
       'args.update(policy_ref=policy_ref)', CALLS),
    _p("d9_py_assign_unscoped", "an assignment is the caller's provider's",
       '        args = {"provider_org_id": provider_org_id, "policy_id": policy_id,\n'
       '                "request_id": request_id,',
       '        args = {"policy_id": policy_id,\n                "request_id": request_id,',
       CALLS),
    _p("d9_py_pin_dropped", "an explicit pin is sent",
       '            args["explicit_serving_ref"] = explicit_serving_ref', '            pass',
       CALLS),
    _p("d9_py_start_unattributed", "a start names its deciding user",
       '"policy_ref": policy_ref,\n            "decided_by": decided_by, "reason": reason}',
       '"policy_ref": policy_ref,\n            "reason": reason}', CALLS),
)


def kill(mutant) -> tuple[str, str]:
    return d7.kill(mutant, DB, t)


def run_code_mutant(mutant):
    return shared.run_mutant(mutant, RUNNER)
