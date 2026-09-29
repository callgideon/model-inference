"""R32/R40 for L3-SQL: single-edit defects of `0032_lab_control.sql` (killed by the named check
of `test_l3sql_control.py` on a database built from the mutated set, needs Docker) and of
`infrx/state/lab_control.py` (killed by the named case of `test_l3sql_units.py` through the
shared runner, no Docker). The SQL runner is D7's (`code_mutants_d7.kill`).

WR-LSQ-9 (LW4): `0044_lab_control_reads.sql` and 0043's router grant (`READS_SQL`, killed by
`test_l3sql_reads.py`'s checks the same way) and the four `ControlReads` selects of
`lab_control.py` (`READS_PY`: the module constant edited in this process - the store reads it
at call time - and the named check run on a fresh database; `kill_read`).

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_code_mutants_l3sql.py
"""
from __future__ import annotations

from dataclasses import dataclass

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Runner
from . import code_mutants_d7 as d7
from . import migration_mutants as _d
from . import pgharness
from . import test_l3sql_control as t
from . import test_l3sql_reads as r

FILE = "0032_lab_control.sql"
DB = f"{pgharness.DATABASE}_l3mut"

ROLES = "check_browser_roles_reach_nothing"
MOVES = "check_transitions_are_a_cas_on_the_providers_own_private_revisions"
PROPOSE = "check_a_proposal_comes_from_a_validated_dev_source"
KEYS = "check_dev_keys_are_scoped_to_the_providers_dev_endpoint"
PUBLISH = "check_publication_is_a_cas_on_the_listing_version"
DEV = "check_dev_revisions_never_reach_app_discovery"
ROLLBACK = "check_rollback_is_a_new_listing_and_admitted_jobs_keep_their_pins"
WALLET = "check_the_dev_wallet_opens_at_zero_and_is_funded_only_by_audited_allocation"
RACE = "check_two_operators_racing_publish_once"
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
    # --- LAB-ACCESS: transitions
    _s("l3_move_any_provider", "   where deployment_revision_id = v_id and provider_org_id = "
       "v_provider\n     and visibility = 'private' for update;", "   where "
       "deployment_revision_id = v_id\n     and visibility = 'private' for update;", MOVES,
       "a provider validates another provider's revision"),
    _s("l3_move_public", "   where deployment_revision_id = v_id and provider_org_id = "
       "v_provider\n     and visibility = 'private' for update;", "   where "
       "deployment_revision_id = v_id and provider_org_id = v_provider\n     for update;",
       MOVES, "a provider drains its public model or activates a proposal unlisted"),
    _s("l3_move_without_cas", "  if d.state is distinct from p_args->>'expected' then",
       "  if false then", MOVES, "a validation that read a stale state retires the revision"),
    _s("l3_bad_move_raw", "  exception when check_violation or not_null_violation then\n    "
       "perform infrx.refuse('state_conflict', 'deployment revision: '", "  exception when "
       "raise_exception then\n    perform infrx.refuse('state_conflict', 'deployment revision: '",
       MOVES, "a forbidden move surfaces as the unclassified 0007 error"),
    _s("l3_move_unattributed", "  perform infrx.lab_control_audit(v_provider, 'lab_transition', "
       "p_args->>'actor',", "  perform infrx.lab_control_audit(v_provider, 'lab_transition', "
       "'platform',", MOVES, "the audit does not name who moved a revision"),
    _s("l3_move_before_lost", "    jsonb_build_object('state', p_args->>'expected'));",
       "    null);", MOVES, "the audit loses the state a move started from"),
    _s("l3_audit_actor_raw", "exception when check_violation or not_null_violation then\n  "
       "perform infrx.refuse('invalid_request', 'a control move names its actor');",
       "exception when raise_exception then\n  perform infrx.refuse('invalid_request', "
       "'a control move names its actor');", MOVES,
       "an unattributed move is a server error instead of a 400"),
    _s("l3_events_any_provider", "   where e.provider_org_id = (p_args->>'provider_org_id')"
       "::uuid", "   where true", MOVES, "a provider reads another's control history"),
    # --- LAB-PUBLISH: proposals
    _s("l3_propose_foreign_source", "  if not found or s.provider_org_id is distinct from "
       "(p->>'provider_org_id')::uuid then", "  if not found then", PROPOSE,
       "a proposal filed by one provider publishes another's validated serving"),
    _s("l3_propose_unvalidated", "  if (s.environment, s.visibility, s.state) <> ('dev', "
       "'private', 'ready_private')\n     or", "  if false\n     or", PROPOSE,
       "a serving version no dev smoke validated is proposed"),
    _s("l3_propose_other_serving", "     or s.serving_version_id is distinct from "
       "(p->>'serving_version_id')::uuid then", "     or false then", PROPOSE,
       "a proposal silently swaps in the source's serving version"),
    _s("l3_propose_any_state", "  if p->>'state' is distinct from 'proposed_public' then",
       "  if false then", PROPOSE, "a malformed proposal is accepted as another"),
    _s("l3_propose_unaudited", "  perform infrx.lab_control_audit(s.provider_org_id, "
       "'lab_propose',", "  perform infrx.lab_control_audit(s.provider_org_id, "
       "'lab_transition',", PROPOSE, "the audit does not say a publication was proposed"),
    # --- LAB-ACCESS: dev credentials
    _s("l3_key_any_environment", "     and provider_org_id = v_provider and environment = "
       "'dev';", "     and provider_org_id = v_provider;", KEYS,
       "a provider_dev credential opens a prod endpoint"),
    _s("l3_key_any_provider", "     and provider_org_id = v_provider and environment = "
       "'dev';", "     and environment = 'dev';", KEYS,
       "one provider mints a credential on another's dev endpoint"),
    _s("l3_key_in_the_members_org", "    values (v_provider, (p_args->>'user_id')::uuid, "
       "p_args->>'name',", "    values ((select m.org_id from public.org_members m where "
       "m.user_id = (p_args->>'user_id')::uuid limit 1), (p_args->>'user_id')::uuid, "
       "p_args->>'name',", KEYS,
       "the key is filed in a consumer org: auth.context refuses it, or bills that org"),
    _s("l3_key_without_org", "  on conflict (id) do nothing;\n  begin\n    insert into "
       "public.api_keys", "    and false;\n  begin\n    insert into public.api_keys", KEYS,
       "the first dev key of a provider cannot be issued"),
    _s("l3_key_hash_audited", "    jsonb_build_object('endpoint_id', e.endpoint_id, 'prefix', "
       "p_args->>'prefix'));", "    jsonb_build_object('endpoint_id', e.endpoint_id, "
       "'prefix', p_args->>'key_hash'));", KEYS, "the audit leaks the credential's hash"),
    # --- LAB-PUBLISH: publication
    _s("l3_publish_without_cas", "  if cur.version is distinct from (p_args->>'expected_version')"
       "::int then\n    perform infrx.refuse('state_conflict', v_alias || ' is no longer",
       "  if false then\n    perform infrx.refuse('state_conflict', v_alias || ' is no longer",
       PUBLISH, "an operator's approval overwrites a listing it never saw"),
    _s("l3_publish_retires_previous", "    update infrx.deployment_revisions set state = "
       "'active'\n     where deployment_revision_id = d.deployment_revision_id;",
       "    update infrx.deployment_revisions set state = 'active'\n     where "
       "deployment_revision_id = d.deployment_revision_id;\n    update "
       "infrx.deployment_revisions set state = 'draining' where deployment_revision_id = "
       "cur.deployment_revision_id;", PUBLISH,
       "a publication drains the revision queued jobs are pinned to"),
    _s("l3_publish_version_skip", "    values (v_alias, coalesce(cur.version, 0) + 1,",
       "    values (v_alias, coalesce(cur.version, 0) + 2,", PUBLISH,
       "the listing skips a version the next CAS then misreads"),
    _s("l3_publish_raw_error", "  when foreign_key_violation or check_violation or "
       "not_null_violation\n       or invalid_text_representation then", "  when "
       "raise_exception then", PUBLISH, "a card of another model is a server error, not a 400"),
    _s("l3_publish_unaudited", "  perform infrx.lab_control_audit(d.provider_org_id, "
       "'lab_publish',", "  perform infrx.lab_control_audit(d.provider_org_id, "
       "'lab_rollback',", PUBLISH, "the audit misnames a publication"),
    _s("l3_publish_any_state", "  if d.state <> 'proposed_public' then", "  if false then",
       DEV, "a validated dev revision is pushed at App discovery"),
    # --- rollback
    _s("l3_rollback_without_cas", "  if cur.version is distinct from (p_args->>'expected_"
       "version')::int then\n    perform infrx.refuse('state_conflict', v_alias || ' moved on",
       "  if false then\n    perform infrx.refuse('state_conflict', v_alias || ' moved on",
       ROLLBACK, "a rollback undoes a publication the operator never saw"),
    _s("l3_rollback_to_current", "   where public_model_id = v_alias and version = v_to and "
       "v_to < cur.version;", "   where public_model_id = v_alias and version = v_to;",
       ROLLBACK, "a no-op rollback writes a listing version"),
    _s("l3_rollback_drained", "  if d.state <> 'active' then", "  if d.state = 'retired' then",
       ROLLBACK, "consumers are routed to a draining revision"),
    _s("l3_rollback_new_card", "      t.serving_version_id, t.rate_card_version, infrx.now(), "
       "p_args->>'actor')", "      t.serving_version_id, (select c.rate_card_version from "
       "infrx.rate_card_versions c where c.deployment_revision_id = t.deployment_revision_id "
       "order by c.created_at desc limit 1), infrx.now(), p_args->>'actor')", ROLLBACK,
       "a rollback silently reprices the old revision"),
    _s("l3_jobs_repinned", "    returning * into l;\n  exception when check_violation or "
       "not_null_violation then\n    perform infrx.refuse('invalid_request', 'a rollback",
       "    returning * into l;\n    update infrx.jobs set deployment_revision_id = "
       "t.deployment_revision_id, serving_version_id = t.serving_version_id, "
       "rate_card_version = t.rate_card_version where deployment_revision_id <> "
       "t.deployment_revision_id;\n  exception when check_violation or not_null_violation "
       "then\n    perform infrx.refuse('invalid_request', 'a rollback", ROLLBACK,
       "an alias switch rewrites the pins of admitted jobs"),
    # --- the dev wallet
    _s("l3_fund_for_anyone", "  if not exists (select 1 from infrx.provider_orgs where "
       "provider_org_id = v_provider) then", "  if false then", WALLET,
       "a wallet for a provider that does not exist is a raw error"),
    _s("l3_fund_audited_twice", "  if not (v_answer->>'replayed')::boolean then",
       "  if true then", WALLET, "a replayed allocation is audited as a second one"),
    _s("l3_fund_as_adjustment", "    'kind', 'operator_allocation', 'amount', p_args->'amount',",
       "    'kind', 'operator_adjustment', 'amount', p_args->'amount',", WALLET,
       "a dev wallet cannot be funded (adjustments are consumer-only)"),
    _s("l3_fund_unaudited", "    perform infrx.lab_control_audit(v_provider, 'lab_fund',",
       "    perform infrx.lab_control_audit(v_provider, 'lab_publish',", WALLET,
       "the provider's history does not show its funding"),
    # --- races
    _s("l3_publish_unserialized", "  perform pg_advisory_xact_lock(hashtextextended("
       "'catalog_listings/' || v_alias, 0));\n  select * into cur from infrx.catalog_listings "
       "where public_model_id = v_alias\n   order by version desc limit 1;\n  if cur.version "
       "is distinct from (p_args->>'expected_version')::int then\n    perform infrx.refuse("
       "'state_conflict', v_alias || ' is no longer", "  select * into cur from "
       "infrx.catalog_listings where public_model_id = v_alias\n   order by version desc "
       "limit 1;\n  if cur.version is distinct from (p_args->>'expected_version')::int then\n"
       "    perform infrx.refuse('state_conflict', v_alias || ' is no longer", RACE,
       "a racing approval is refused for a colliding card, not for the version it lost"),
    _s("l3_rollback_unserialized", "  perform pg_advisory_xact_lock(hashtextextended("
       "'catalog_listings/' || v_alias, 0));\n  select * into cur from infrx.catalog_listings "
       "where public_model_id = v_alias\n   order by version desc limit 1;\n  if cur.version "
       "is distinct from (p_args->>'expected_version')::int then\n    perform infrx.refuse("
       "'state_conflict', v_alias || ' moved on", "  select * into cur from "
       "infrx.catalog_listings where public_model_id = v_alias\n   order by version desc "
       "limit 1;\n  if cur.version is distinct from (p_args->>'expected_version')::int then\n"
       "    perform infrx.refuse('state_conflict', v_alias || ' moved on", RACE,
       "two rollbacks at once collide on the listing key (a 500)"),
    _s("l3_events_empty", "'after', e.after, 'at', e.at) order by e.event_id), '[]')",
       "'after', e.after, 'at', e.at) order by e.event_id) filter (where false), '[]')",
       STORE, "the provider sees no audit of its moves"),
)
SQL_NAMES = tuple(m.name for m in SQL_MUTANTS)

RUNNER = Runner(name="l3sql", targets=("tests/d/test_l3sql_units.py",))
F = "state/lab_control.py"
CALLS = "test_calls__carry_the_servers_identity_and_the_cas_expectations"
READS = "test_reads__a_malformed_id_is_absent_without_a_query"


def _p(name, invariant, old, new, *cases, **kw) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=F, old=old, new=new, cases=cases, **kw)


CODE_MUTANTS = (
    _p("l3_py_move_unscoped", "a move is the caller's provider's",
       '            "provider_org_id": provider_org_id, "expected": '
       'DeploymentState(expected).value,', '            "expected": '
       'DeploymentState(expected).value,', CALLS),
    _p("l3_py_move_without_expectation", "a move carries the state it read",
       '"expected": DeploymentState(expected).value,', '"expected": DeploymentState(to).value,',
       CALLS),
    _p("l3_py_publish_wrong_rate", "the card's input rate is the input rate",
       '"input_rate_per_million": str(card.input_rate_per_million.raw("CREDIT")),',
       '"input_rate_per_million": str(card.output_rate_per_million.raw("CREDIT")),', CALLS),
    _p("l3_py_publish_without_cas", "a publication carries the version it read",
       '"public_model_id": public_model_id, "expected_version": expected_version,\n'
       '            "actor": actor, "reason": reason, "card"', '"public_model_id": '
       'public_model_id, "expected_version": None,\n            "actor": actor, "reason": '
       'reason, "card"', CALLS),
    _p("l3_py_rollback_swapped", "a rollback's target is not its expectation",
       '"public_model_id": public_model_id, "to_version": to_version,',
       '"public_model_id": public_model_id, "to_version": expected_version,', CALLS),
    _p("l3_py_fund_unscoped", "an allocation funds the named provider",
       '"provider_org_id": provider_org_id, "amount": str(amount),',
       '"provider_org_id": None, "amount": str(amount),', CALLS),
    _p("l3_py_key_unattributed", "a dev key names the member it was issued to",
       '            "user_id": user_id, "key_hash": key_hash,',
       '            "user_id": None, "key_hash": key_hash,', CALLS),
    _p("l3_py_malformed_id_queried", "a malformed id never reaches a uuid column",
       "        if not _uuid(deployment_revision_id):\n            return None",
       "        if False:\n            return None", READS),
    _p("l3_py_malformed_endpoint_queried", "a malformed endpoint id has no alias, unqueried",
       "        if not _uuid(endpoint_id):\n            return None",
       "        if False:\n            return None", READS),
)

# --- WR-LSQ-9 (LW4) -----------------------------------------------------------------------
DB_R = f"{pgharness.DATABASE}_l3rmut"
R_ROWS = "check_the_control_reads_are_the_registrys_rows"
R_LOGIN = "check_the_control_login_holds_what_operations_reads"
R_ROUTER = "check_the_router_functions_are_the_runtime_logins_alone"
#: LSQ5-m1/m2 (WR-E8L-2's endpoint_alias oracle).
R_ORDER = "check_endpoint_alias_orders_across_aliases_by_time_not_by_either_ones_version"


def _r(name, old, new, check, why, file="0044_lab_control_reads.sql", **kw):
    return _d.Mutant(name, file, old, new, "lab", check, why, **kw)


READS_SQL = (
    _r("l3r_login_no_listings", "grant select on infrx.catalog_listings to infrx_lab_control;",
       "", R_LOGIN, "the control service's deployments list and R2's serving read fail on "
       "the Lab's own login"),
    _r("l3r_login_no_policy", "create policy lab_control_reads_listings on "
       "infrx.catalog_listings for select\n  to infrx_lab_control using (true);", "", R_LOGIN,
       "row security hides every listing: deployments read unpriced, R2 sees no alias"),
    _r("l3r_login_writes_listings", "grant select on infrx.catalog_listings to "
       "infrx_lab_control;", "grant select, insert on infrx.catalog_listings to "
       "infrx_lab_control;", R_LOGIN, "the Lab login lists a revision around the "
       "publication CAS"),
    _r("l3r_router_for_the_lab_login", "grant execute on function infrx.release_active(text), "
       "infrx.release_eligible(uuid, uuid),\n  infrx.record_rollout_assignment(jsonb) to "
       "infrx_runtime;", "grant execute on function infrx.release_active(text), "
       "infrx.release_eligible(uuid, uuid),\n  infrx.record_rollout_assignment(jsonb) to "
       "infrx_runtime, infrx_lab_control;", R_ROUTER, "the Lab's login records routing "
       "assignments for consumer traffic", file="0043_lab_reads_and_proposals.sql"),
    _r("l3r_router_not_the_runtimes", "grant execute on function infrx.release_active(text), "
       "infrx.release_eligible(uuid, uuid),\n  infrx.record_rollout_assignment(jsonb) to "
       "infrx_runtime;", "grant execute on function infrx.release_active(text),\n  "
       "infrx.record_rollout_assignment(jsonb) to infrx_runtime;", R_ROUTER,
       "the router cannot read eligibility on its own login and fails every candidate",
       file="0043_lab_reads_and_proposals.sql"),
)


@dataclass(frozen=True)
class ReadMutant:
    """One edit of a `lab_control.py` select constant; `old` is anchored in the source too."""

    name: str
    constant: str
    old: str
    new: str
    check: str
    why: str


READS_PY = (
    ReadMutant("l3r_servings_any_provider", "_PROVIDER_SERVINGS",
               '"where s.provider_org_id = %s "', "where %s::uuid is not null ", R_ROWS,
               "a provider's model list shows another provider's serving revisions"),
    ReadMutant("l3r_servings_newest_first", "_PROVIDER_SERVINGS",
               '"order by s.created_at, s.serving_version_id"',
               "order by s.created_at desc, s.serving_version_id", R_ROWS,
               "the model list is not oldest first (the fake's order)"),
    ReadMutant("l3r_deployments_any_provider", "_PROVIDER_DEPLOYMENTS",
               '"where provider_org_id = %s "', "where %s::uuid is not null ", R_ROWS,
               "a provider's deployments list shows another provider's revisions"),
    ReadMutant("l3r_deployments_newest_first", "_PROVIDER_DEPLOYMENTS",
               '"order by created_at, deployment_revision_id"',
               "order by created_at desc, deployment_revision_id", R_ROWS,
               "the deployments list is not oldest first"),
    ReadMutant("l3r_alias_any_endpoint", "_ENDPOINT_ALIAS",
               '"where d.endpoint_id = %s "', "where %s::uuid is not null ", R_ROWS,
               "R2 rolls back an alias its endpoint does not serve"),
    ReadMutant("l3r_alias_by_version_not_time", "_ENDPOINT_ALIAS",
               "order by l.created_at desc, l.public_model_id desc, l.version desc limit 1",
               "order by l.version desc limit 1", R_ORDER,
               "LSQ5-m1: a second alias's newer listing on a shared endpoint loses to an "
               "older listing under an alias with a higher version number"),
    ReadMutant("l3r_listings_any_alias", "_LISTINGS",
               '"from infrx.catalog_listings where public_model_id = %s order by version"',
               "from infrx.catalog_listings where %s::text is not null order by version",
               R_ROWS, "R2 picks a rollback target from another alias's history"),
    ReadMutant("l3r_listings_newest_first", "_LISTINGS",
               '"from infrx.catalog_listings where public_model_id = %s order by version"',
               "from infrx.catalog_listings where public_model_id = %s order by version desc",
               R_ROWS, "listing versions are not oldest first: R2's newest match is the oldest"),
)
READS_NAMES = tuple(m.name for m in READS_SQL) + tuple(m.name for m in READS_PY)


def kill_reads_sql(mutant) -> tuple[str, str]:
    return d7.kill(mutant, DB_R, r)


def kill_read(mutant: ReadMutant) -> tuple[str, str]:
    """The constant edited (the anchor minus its source quotes) for one check on a fresh
    database, then restored."""
    from infrx.state import lab_control, migrations
    old = mutant.old.strip('"')
    source = getattr(lab_control, mutant.constant)
    if source.count(old) != 1:
        return _d.MISDECLARED, f"{mutant.constant} holds {source.count(old)} anchors"
    setattr(lab_control, mutant.constant, source.replace(old, mutant.new))
    try:
        pgharness.ensure()
        pgharness.recreate(DB_R)
        pgharness.apply(DB_R, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
        with pgharness.connect(DB_R) as conn:
            r.seed(conn)
            return _d._run(r.CHECKS[mutant.check], conn)
    finally:
        setattr(lab_control, mutant.constant, source)


def kill(mutant) -> tuple[str, str]:
    return d7.kill(mutant, DB, t)


def run_code_mutant(mutant):
    return shared.run_mutant(mutant, RUNNER)
