"""R32/R40 for L2-SQL: single-edit defects of `0027_lab_access.sql` (killed by the named check
of `test_l2sql_access.py` on a database built from the mutated set, needs Docker) and of
`infrx/state/lab_access.py` (killed by the named case of `test_l2sql_units.py` through the
shared runner, no Docker).

The SQL runner is migration_mutants' (`_mutate`, `_run`: only an AssertionError from the named
check kills; an apply or setup failure is its own outcome) with this lane's database and seed,
so neither `migration_mutants.py` nor its `_CHECKS` is touched.

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_code_mutants_l2sql.py
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
from . import test_l2sql_access as t

FILE = "0027_lab_access.sql"
DB = f"{pgharness.DATABASE}_l2mut"

ROLES = "check_browser_roles_reach_nothing"
MEMBERS = "check_memberships_are_the_users_own_rows"
OWNER = "check_only_the_grantor_owner_writes_a_scope_of_the_recipients_models"
VERSIONS = "check_every_change_is_a_new_immutable_version"
AGG = "check_default_aggregates_are_own_deployments_without_identity"
RACE = "check_two_writers_of_one_grant_get_consecutive_versions"
CONTENT = "check_content_needs_a_current_developer_and_a_current_grant"


def _s(name, old, new, check, why, **kw):
    return _d.Mutant(name, FILE, old, new, "lab", check, why, **kw)


SQL_MUTANTS = (
    _s("l2_member_writes_the_grant", "\n                 and m.user_id = (p_args->>'actor_user_id')"
       "::uuid and m.role = 'owner') then", "\n                 and m.user_id = (p_args->>"
       "'actor_user_id')::uuid) then", OWNER,
       "any member of a consumer organization shares its data with a provider"),
    _s("l2_suspended_grantor_writes", "  if (select o.suspended from public.organizations o where "
       "o.id = v_grantor) then", "  if false then", OWNER,
       "a suspended organization changes its data sharing (R33)"),
    _s("l2_model_of_another_provider", "\n          and pm.provider_org_id = v_provider)) then",
       ")) then", OWNER, "a grant names a model the recipient does not own"),
    _s("l2_unknown_provider", "  recipient_provider_org_id uuid not null references "
       "infrx.provider_orgs on delete restrict,", "  recipient_provider_org_id uuid not null,",
       OWNER, "a grant to a provider that does not exist"),
    _s("l2_any_category", "  categories text[] not null check (categories <@ array["
       "'request_content',", "  categories text[] not null check (true or categories <@ array["
       "'request_content',", OWNER, "a category outside the contract is granted"),
    _s("l2_any_purpose", "    check (purposes <@ array['capture',",
       "    check (true or purposes <@ array['capture',", OWNER,
       "a purpose outside the four permissions is granted"),
    _s("l2_retention_unbounded", "check (retention_days between 1 and 90)",
       "check (retention_days >= 0)", OWNER, "a grant keeps data beyond 90 days"),
    _s("l2_expiry_in_the_past", "  expires_at timestamptz check (expires_at > effective_at),",
       "  expires_at timestamptz,", OWNER, "an already expired grant is written as current"),
    _s("l2_version_not_incremented", "coalesce(prev.version, 0) + 1", "1", VERSIONS,
       "a change of scope fails instead of becoming the next version"),
    _s("l2_new_grant_id_per_change", "coalesce(prev.grant_id, gen_random_uuid())",
       "gen_random_uuid()", VERSIONS, "the source reference changes with every version"),
    _s("l2_revoked_twice", "  if prev.grant_id is null or prev.revoked_at is not null then",
       "  if prev.grant_id is null then", VERSIONS, "a revoked grant is revoked again"),
    _s("l2_revocation_drops_the_scope", "    prev.model_ids, prev.categories, prev.purposes, "
       "prev.retention_days, prev.effective_at,", "    prev.model_ids, prev.categories, '{}', "
       "prev.retention_days, prev.effective_at,", VERSIONS,
       "the history no longer says what was revoked"),
    _s("l2_revocation_not_recorded", "    prev.expires_at, infrx.now(), (p_args->>'actor_user_id')"
       "::uuid)", "    prev.expires_at, null, (p_args->>'actor_user_id')::uuid)", CONTENT,
       "a revoked grant keeps authorizing content"),
    _s("l2_grants_editable", "create or replace trigger lab_access_grants_immutable before "
       "update or delete\n  on infrx.lab_access_grants for each row execute function "
       "infrx.forbid_update_delete();\n", "", VERSIONS, "a grant's history is rewritten"),
    _s("l2_grants_truncatable", "create or replace trigger lab_access_grants_no_truncate before "
       "truncate\n  on infrx.lab_access_grants for each statement execute function "
       "infrx.forbid_truncate();\n", "", VERSIONS, "the grant history is truncated"),
    _s("l2_history_of_every_provider", "\n     and g.recipient_provider_org_id = (p_args->>"
       "'recipient_provider_org_id')::uuid\n$$;", "\n$$;", VERSIONS,
       "one provider reads the grantor's grants to another"),
    _s("l2_history_newest_first", "jsonb_agg(infrx.lab_grant_json(g) order by g.version)",
       "jsonb_agg(infrx.lab_grant_json(g) order by g.version desc)", VERSIONS,
       "the current grant is read as the oldest version"),
    _s("l2_memberships_of_everyone", "   where m.user_id = (p_args->>'user_id')::uuid\n",
       "   where true\n", MEMBERS, "a user selects another user's provider workspaces"),
    _s("l2_aggregates_of_every_provider",
       "     where d.provider_org_id = (p_args->>'provider_org_id')::uuid\n", "     where true\n",
       AGG, "a provider reads another provider's traffic"),
    _s("l2_aggregates_ignore_the_window", "\n       and j.admitted_at > win.since_at and "
       "j.admitted_at <= win.until_at", "", AGG, "a window reports traffic outside it"),
    _s("l2_service_writes_grants", "grant select on infrx.lab_access_grants to service_role;",
       "grant select, insert on infrx.lab_access_grants to service_role;", ROLES,
       "the platform role writes a grant around the owner check"),
    _s("l2_writers_race", "  perform pg_advisory_xact_lock(hashtextextended(\n"
       "    'lab_access_grants/'", "  perform (hashtextextended(\n    'lab_access_grants/'", RACE,
       "two simultaneous grant changes fail instead of becoming consecutive versions"),
)
SQL_NAMES = tuple(m.name for m in SQL_MUTANTS)

RUNNER = Runner(name="l2sql", targets=("tests/d/test_l2sql_units.py",))
F = "state/lab_access.py"
MEMBERSHIPS = "test_memberships__the_users_rows_without_the_display_name_latest_per_provider"
GRANTS = "test_grants__history_of_the_pair_and_the_latest_is_current"
WRITES = "test_writes__carry_the_actor_and_refusals_are_typed"
AGGREGATES = "test_aggregates__are_the_providers_rows"


def _p(name, invariant, old, new, *cases) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=F, old=old, new=new, cases=cases)


CODE_MUTANTS = (
    _p("l2_py_display_name_kept", "R156: the name is display-only, never a membership field",
       "if k not in DISPLAY_ONLY}", "}", MEMBERSHIPS),
    _p("l2_py_membership_of_any_provider", "a membership is the named provider's",
       "\n                if m.provider_org_id == provider_org_id]", "]", MEMBERSHIPS),
    _p("l2_py_oldest_membership", "the latest row is the current one",
       "        return rows[-1] if rows else None", "        return rows[0] if rows else None",
       MEMBERSHIPS),
    _p("l2_py_oldest_grant_is_current", "the current grant is the latest version",
       "        return history[-1] if history else None",
       "        return history[0] if history else None", GRANTS),
    _p("l2_py_history_of_the_grantor_only", "history is per (grantor, provider)",
       '"recipient_provider_org_id": provider_org_id})]', '})]', GRANTS),
    _p("l2_py_write_without_actor", "the SQL decides ownership from the actor",
       '{**grant, "actor_user_id": actor_user_id}', "dict(grant)", WRITES),
    _p("l2_py_untyped_refusal", "a SQL refusal is its typed error",
       "function, args, error=domain_error)", "function, args, error=None)", WRITES),
    _p("l2_py_aggregates_unscoped", "aggregates are the named provider's",
       '{"provider_org_id": provider_org_id})', '{})', AGGREGATES),
)


def kill(mutant) -> tuple[str, str]:
    """migration_mutants.kill's classification on this lane's database and seed."""
    pgharness.ensure()
    with TemporaryDirectory(prefix=f"infrx-dlab-{mutant.name}-") as tmp:
        directory = Path(tmp)
        refused = _d._mutate(directory, mutant)
        if refused is not None:
            return _d.MISDECLARED, refused
        try:
            pgharness.recreate(DB)
            pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM,
                                                   directory=directory))
        except (AssertionError, psycopg.Error) as broken:
            return _d.APPLY_ERROR, _d._first_line(broken)
        try:
            with pgharness.connect(DB) as conn:
                t.seed(conn)
                return _d._run(t.CHECKS[mutant.check], conn)
        except (AssertionError, psycopg.Error) as during_setup:
            return _d.SETUP_ERROR, _d._first_line(during_setup)


def run_code_mutant(mutant):
    return shared.run_mutant(mutant, RUNNER)
