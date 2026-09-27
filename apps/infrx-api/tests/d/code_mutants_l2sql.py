"""R32/R40 for L2-SQL (`0027_lab_access.sql`): single-edit defects, each killed by the named
check of `test_l2sql_access.py` on a database built from the mutated migration set.

The runner is migration_mutants' (`_mutate`, `_run`: only an AssertionError from the named
check kills; an apply or setup failure is its own outcome), with this lane's own database and
seed, so neither `migration_mutants.py` nor its `_CHECKS` is touched.

    INFRX_D_TASK=dlab INFRX_MUTANTS=all uv run --frozen pytest -q tests/d/test_code_mutants_l2sql.py
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import psycopg
from infrx.state import migrations

from . import migration_mutants as _d
from . import pgharness
from . import test_l2sql_access as t

FILE = "0027_lab_access.sql"
DB = f"{pgharness.DATABASE}_l2mut"


def _m(name, old, new, check, why, **kw):
    return _d.Mutant(name, FILE, old, new, "lab", check, why, **kw)


OWNER = "check_only_the_data_owner_grants_a_model_of_the_recipient"
SCOPE = "check_a_grant_is_purpose_model_and_recipient_specific"
REVOKE = "check_revocation_and_expiry_deny_immediately"
HISTORY = "check_grant_history_is_scoped"
AGG = "check_default_aggregates_are_own_deployments_without_identity"
ROLES = "check_browser_roles_reach_nothing"

MUTANTS = (
    _m("l2_member_grants_the_org", "m.org_id = v_source\n                 and m.user_id = v_actor "
       "and m.role = 'owner'", "m.org_id = v_source\n                 and m.user_id = v_actor",
       OWNER, "any member of a consumer organization shares its data with a provider"),
    _m("l2_suspended_org_grants", "  if (select o.suspended from public.organizations o where "
       "o.id = v_source) then", "  if false then", OWNER,
       "a suspended organization changes its data-sharing configuration (R33)"),
    _m("l2_expiry_unbounded", "\n     or v_expires > infrx.now() + interval '366 days' then",
       " then", OWNER, "a grant that never expires in practice"),
    _m("l2_model_of_another_provider",
       "  constraint lab_data_grants_model_is_the_recipients foreign key (model_id, "
       "provider_org_id)\n    references public.models (model_uuid, provider_org_id) on delete "
       "restrict,\n", "", OWNER, "a grant names a model the recipient does not own"),
    _m("l2_role_alone_authorizes", "     and exists (select 1 from infrx.lab_data_grants g",
       "     or exists (select 1 from infrx.lab_data_grants g", SCOPE,
       "a provider developer reads customer data with no grant (role alone)"),
    _m("l2_purpose_ignored", "\n                    and g.purpose = p_args->>'purpose'", "",
       SCOPE, "an evaluation grant authorizes training"),
    _m("l2_category_ignored", "\n                    and g.data_category = p_args->>'data_category'",
       "", SCOPE, "a trace grant authorizes content"),
    _m("l2_recipient_ignored",
       "                  where g.provider_org_id = (p_args->>'provider_org_id')::uuid\n"
       "                    and g.source_org_id",
       "                  where g.source_org_id", SCOPE,
       "a grant to provider A lets provider B's members read the data"),
    _m("l2_viewer_uses_data", "  ('viewer', 'read_aggregates'), ('developer', 'read_aggregates'),",
       "  ('viewer', 'read_aggregates'), ('viewer', 'use_granted_data'), "
       "('developer', 'read_aggregates'),", SCOPE,
       "a viewer role reads granted customer data"),
    _m("l2_revocation_ignored", "                    and g.revoked_at is null and g.expires_at "
       "> infrx.now())", "                    and g.expires_at > infrx.now())", REVOKE,
       "a revoked grant keeps authorizing"),
    _m("l2_expiry_ignored", " and g.expires_at > infrx.now())", ")", REVOKE,
       "an expired grant keeps authorizing"),
    _m("l2_revoked_membership_counts", "\n                    and m.revoked_at is null and "
       "c.capability", "\n                    and c.capability", REVOKE,
       "a removed provider member keeps its data access"),
    _m("l2_revocation_reversible", "\n     or old.revoked_at is not null then", " then", REVOKE,
       "a revocation is undone by an update"),
    _m("l2_grant_deletable", "create or replace trigger lab_data_grants_guard before update or "
       "delete\n", "create or replace trigger lab_data_grants_guard before update\n", REVOKE,
       "grant history is erased"),
    _m("l2_anyone_revokes", "  if not found or not exists (select 1 from public.org_members m",
       "  if not found or false and not exists (select 1 from public.org_members m", REVOKE,
       "a stranger revokes (or learns of) another organization's grant"),
    _m("l2_viewer_reads_history", "  if v_provider is not null and infrx.lab_provider_can(v_user, "
       "v_provider, 'use_granted_data')", "  if v_provider is not null and "
       "infrx.lab_provider_can(v_user, v_provider, 'read_aggregates')", HISTORY,
       "a viewer lists the organizations that shared data with its provider"),
    _m("l2_anyone_reads_source_history", "  if v_source is not null and exists (select 1 from "
       "public.org_members m where", "  if v_source is not null and true or exists (select 1 "
       "from public.org_members m where", HISTORY,
       "a provider member lists a consumer organization's grants to every provider"),
    _m("l2_aggregates_of_every_provider", "           where d.provider_org_id = v_provider\n",
       "           where true\n", AGG, "a provider reads another provider's traffic"),
    _m("l2_aggregates_without_membership",
       "  if not infrx.lab_provider_can((p_args->>'user_id')::uuid, v_provider, "
       "'read_aggregates') then", "  if false then", AGG,
       "a consumer or another provider reads a provider's aggregates"),
    _m("l2_service_writes_grants", "    execute format('grant select on %s to service_role', r);",
       "    execute format('grant select, insert, update on %s to service_role', r);", ROLES,
       "the platform role writes a grant around the owner check"),
)
NAMES = tuple(m.name for m in MUTANTS)


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
