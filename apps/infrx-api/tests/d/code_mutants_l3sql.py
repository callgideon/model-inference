"""R32/R40 for L3-SQL: single-edit defects of `0032_lab_control.sql` (killed by the named check
of `test_l3sql_control.py` on a database built from the mutated set, needs Docker) and of
`infrx/state/lab_control.py` (killed by the named case of `test_l3sql_units.py` through the
shared runner, no Docker). The SQL runner is D7's (`code_mutants_d7.kill`).

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_code_mutants_l3sql.py
"""
from __future__ import annotations

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Runner
from . import code_mutants_d7 as d7
from . import migration_mutants as _d
from . import pgharness
from . import test_l3sql_control as t

FILE = "0032_lab_control.sql"
DB = f"{pgharness.DATABASE}_l3mut"

ROLES = "check_browser_roles_reach_nothing"
MOVES = "check_providers_move_only_their_own_private_revisions"
APPROVE = "check_an_operator_approves_a_validated_proposal"
DEV = "check_dev_revisions_never_reach_app_discovery"
ROLLBACK = "check_rollback_is_a_new_listing_and_admitted_jobs_keep_their_pins"
WALLET = "check_the_dev_wallet_opens_at_zero_and_is_funded_only_by_audited_allocation"
RACE = "check_concurrent_publications_take_consecutive_versions"
STORE = "check_the_store_composes"


def _s(name, old, new, check, why, **kw):
    return _d.Mutant(name, FILE, old, new, "lab", check, why, **kw)


SQL_MUTANTS = (
    # --- DUR-RLS
    _s("l3_service_edits_audit", "grant select on infrx.lab_control_events to service_role;",
       "grant select, delete on infrx.lab_control_events to service_role;", ROLES,
       "the platform role erases who published what"),
    _s("l3_row_security_off", "alter table infrx.lab_control_events enable row level security;",
       "alter table infrx.lab_control_events disable row level security;", ROLES,
       "a future browser grant exposes every provider's control history"),
    # --- LAB-ACCESS: provider moves
    _s("l3_move_any_provider", "   where deployment_revision_id = (p_args->>'deployment_revision"
       "_id')::uuid\n     and provider_org_id = (p_args->>'provider_org_id')::uuid for update;\n"
       "  if not found then\n    perform infrx.refuse('not_found', 'no such deployment revision "
       "for this provider');\n  end if;\n  if v_to = 'active'", "   where deployment_revision_id"
       " = (p_args->>'deployment_revision_id')::uuid for update;\n  if not found then\n    "
       "perform infrx.refuse('not_found', 'no such deployment revision for this provider');\n"
       "  end if;\n  if v_to = 'active'", MOVES, "a provider validates another's revision"),
    _s("l3_provider_drains_public", "  if not v_operator and d.visibility = 'public'",
       "  if false and d.visibility = 'public'", MOVES,
       "a provider takes the public model down without an operator"),
    _s("l3_move_activates", "  if v_to = 'active' then\n    perform infrx.refuse('forbidden', "
       "'a public revision is activated only", "  if false then\n    perform infrx.refuse("
       "'forbidden', 'a public revision is activated only", MOVES,
       "an operator move activates a proposal with no listing, card or dev validation"),
    _s("l3_bad_move_raw", "  exception when check_violation or not_null_violation then\n    "
       "perform infrx.refuse('state_conflict', 'deployment revision: '", "  exception when "
       "raise_exception then\n    perform infrx.refuse('state_conflict', 'deployment revision: '",
       MOVES, "a forbidden move surfaces as the unclassified 0007 error"),
    _s("l3_move_unaudited", "  values (d.provider_org_id, d.deployment_revision_id, 'move', "
       "d.state, v_to,\n    p_args->>'actor'", "  values (d.provider_org_id, "
       "d.deployment_revision_id, 'move', d.state, v_to,\n    'platform'", MOVES,
       "the audit does not name who moved a revision"),
    _s("l3_history_any_provider", "   where e.provider_org_id = (p_args->>'provider_org_id')::uuid"
       "\n     and", "   where true\n     and", MOVES, "a provider reads another's history"),
    # --- LAB-PUBLISH: approval
    _s("l3_provider_approves", "    perform infrx.refuse('forbidden', 'a provider proposes; an "
       "operator approves');", "    null;", APPROVE,
       "a provider publishes its own model to every consumer"),
    _s("l3_approve_any_provider", "   where deployment_revision_id = (p_args->>'deployment_revision"
       "_id')::uuid\n     and provider_org_id = (p_args->>'provider_org_id')::uuid for update;\n"
       "  if not found then\n    perform infrx.refuse('not_found', 'no such deployment revision "
       "for this provider');\n  end if;\n  if d.state <> 'proposed_public'", "   where "
       "deployment_revision_id = (p_args->>'deployment_revision_id')::uuid for update;\n  if not "
       "found then\n    perform infrx.refuse('not_found', 'no such deployment revision for this "
       "provider');\n  end if;\n  if d.state <> 'proposed_public'", APPROVE,
       "an approval attributed to the wrong provider lands"),
    _s("l3_unvalidated_published", "                    and x.environment = 'dev' and x.state = "
       "'ready_private') then", "                    and x.environment = 'dev') then", APPROVE,
       "a serving version no dev smoke validated is published"),
    _s("l3_card_not_effective", "                    and c.effective_at <= infrx.now()) then",
       "                    ) then", APPROVE, "a revision is listed at a card not yet in force"),
    _s("l3_approval_unaudited_version", "  values (d.provider_org_id, d.deployment_revision_id, "
       "'approve', d.state, 'active',\n    p_args->>'public_model_id', v_version,",
       "  values (d.provider_org_id, d.deployment_revision_id, 'approve', d.state, 'active',\n"
       "    p_args->>'public_model_id', 1,", APPROVE,
       "the audit names a listing version that is not the one written"),
    _s("l3_listing_raw_error", "exception when foreign_key_violation or not_null_violation then\n"
       "  perform infrx.refuse('invalid_request', 'the alias", "exception when raise_exception "
       "then\n  perform infrx.refuse('invalid_request', 'the alias", APPROVE,
       "a wrong alias is a server error instead of a 400"),
    # --- dev never discoverable
    _s("l3_private_approvable", "  if d.state <> 'proposed_public' then\n    perform "
       "infrx.refuse('state_conflict', 'only a proposed", "  if d.state not in "
       "('proposed_public', 'ready_private') then\n    perform infrx.refuse('state_conflict', "
       "'only a proposed", DEV, "a validated dev revision is pushed at App discovery"),
    # --- rollback
    _s("l3_rollback_by_provider", "    perform infrx.refuse('forbidden', 'a rollback is an "
       "operator''s');", "    null;", ROLLBACK, "a provider re-points the public alias"),
    _s("l3_rollback_unlisted", "  if l.version is null then", "  if false then", ROLLBACK,
       "an alias rolls back to a revision it never listed"),
    _s("l3_rollback_drained", "  if d.state <> 'active' then\n    perform infrx.refuse("
       "'state_conflict', 'a rollback target", "  if d.state = 'retired' then\n    perform "
       "infrx.refuse('state_conflict', 'a rollback target", ROLLBACK,
       "consumers are routed to a draining revision"),
    _s("l3_rollback_to_current", "     = d.deployment_revision_id then", "     = null then",
       ROLLBACK, "a no-op rollback writes a listing version"),
    _s("l3_rollback_new_card", "  v_version := infrx.lab_list_alias(d.deployment_revision_id, "
       "v_alias, l.rate_card_version,", "  v_version := infrx.lab_list_alias("
       "d.deployment_revision_id, v_alias, (select c.rate_card_version from "
       "infrx.rate_card_versions c where c.deployment_revision_id = d.deployment_revision_id "
       "order by c.created_at desc limit 1),", ROLLBACK,
       "a rollback silently reprices the old revision"),
    _s("l3_jobs_repinned", "  return v_version;\nexception when foreign_key_violation",
       "  update infrx.jobs set deployment_revision_id = p_deployment, serving_version_id = "
       "(select serving_version_id from infrx.deployment_revisions where deployment_revision_id"
       " = p_deployment), rate_card_version = p_card where deployment_revision_id <> "
       "p_deployment and state not in ('succeeded', 'failed', 'cancelled', 'expired');\n  "
       "return v_version;\nexception when foreign_key_violation", ROLLBACK,
       "an alias switch rewrites the pins of queued jobs"),
    # --- the dev wallet
    _s("l3_wallet_for_anyone", "  if not exists (select 1 from infrx.provider_orgs where "
       "provider_org_id = v_provider) then", "  if false then", WALLET,
       "a wallet is opened for a provider that does not exist (a raw error)"),
    _s("l3_second_dev_wallet", "  values ('provider_dev', v_provider)\n  on conflict "
       "(owner_provider_org_id) where kind = 'provider_dev' do nothing;",
       "  values ('provider_dev', v_provider);", WALLET,
       "a replayed opening errors instead of answering the one wallet"),
    # --- races
    _s("l3_alias_unserialized", "  perform pg_advisory_xact_lock(hashtextextended("
       "'catalog_listings/' || p_alias, 0));", "", RACE,
       "two approvals of one alias at once collide on the listing version"),
    _s("l3_rollback_unserialized", "  perform pg_advisory_xact_lock(hashtextextended("
       "'catalog_listings/' || v_alias, 0));", "", RACE,
       "two rollbacks at once both list the old revision"),
    _s("l3_history_empty", "  select coalesce(jsonb_agg(infrx.lab_control_event_json(e) order "
       "by e.event_id), '[]')", "  select coalesce(jsonb_agg(infrx.lab_control_event_json(e) "
       "order by e.event_id) filter (where false), '[]')", STORE,
       "the provider sees no audit of its moves"),
)
SQL_NAMES = tuple(m.name for m in SQL_MUTANTS)

RUNNER = Runner(name="l3sql", targets=("tests/d/test_l3sql_units.py",))
F = "state/lab_control.py"
CALLS = "test_calls__carry_the_provider_and_the_operator_marker_only_where_it_belongs"


def _p(name, invariant, old, new, *cases, **kw) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=F, old=old, new=new, cases=cases, **kw)


CODE_MUTANTS = (
    _p("l3_py_move_unscoped", "a move is the caller's provider's",
       '"provider_org_id": provider_org_id, "deployment_revision_id": deployment_revision_id,\n'
       '            "state": state,', '"deployment_revision_id": deployment_revision_id,\n'
       '            "state": state,', CALLS),
    _p("l3_py_move_as_operator", "a provider's move is not an operator's",
       "reason: str, operator: bool = False)", "reason: str, operator: bool = True)", CALLS),
    _p("l3_py_approve_without_card", "an approval names its card",
       '"public_model_id": public_model_id, "rate_card_version": rate_card_version,',
       '"public_model_id": public_model_id,', CALLS),
    _p("l3_py_rollback_unattributed", "a rollback names its actor",
       '            "actor": actor, "operator": True, "reason": reason})\n\n    async def open',
       '            "operator": True, "reason": reason})\n\n    async def open', CALLS),
    _p("l3_py_history_unscoped", "history is one revision's when asked",
       '"provider_org_id": provider_org_id, "deployment_revision_id": deployment_revision_id})',
       '"provider_org_id": provider_org_id})', CALLS),
)


def kill(mutant) -> tuple[str, str]:
    return d7.kill(mutant, DB, t)


def run_code_mutant(mutant):
    return shared.run_mutant(mutant, RUNNER)
