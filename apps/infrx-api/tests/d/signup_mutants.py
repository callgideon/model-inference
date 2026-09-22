"""R32/R40 for A1: single-edit defects of migration 0015 and of `infrx/state/signup.py`.

Two lists, two runners, one kill rule:

* `MIGRATION_MUTANTS` join the D list (`migration_mutants.py` imports this module on its
  last line; importing appends them to its `MUTANTS` and their checks to its `_CHECKS`).
  Each builds a database from a mutated 0015 on the D1R "credit" scenario and runs the
  named `checks_signup` check in process - the D runner, `assertion_kill` underneath.
* `MUTANTS` (code) go through the shared runner (`tests/contracts/mutants.py`, R83) and
  name pure cases of `tests/d/test_signup.py`, which need no database: a nested pytest
  cannot take the D harness's port lock while the parent suite holds it.

    INFRX_MUTANTS=all uv run --frozen pytest -q tests/d/test_migration_mutants.py
    uv run --frozen pytest -q tests/d/test_signup.py -k code_mutant
"""
from __future__ import annotations

from ..contracts import mutants as shared
from . import checks_signup, pgharness
from . import migration_mutants as _d

SIGNUP = "0015_signup_eligibility.sql"


def _m(name, old, new, check, why, **kw):
    return _d.Mutant(name, SIGNUP, old, new, "credit", check, why, **kw)


MIGRATION_MUTANTS = (
    _m("a1_unverified_mints",
       "  if v_evidence is null or v_email is null or length(btrim(v_email)) = 0 then",
       "  if v_email is null or length(btrim(v_email)) = 0 then",
       "signup_eligibility", "an unverified signup mints 10,000 CREDIT"),
    _m("a1_soft_deleted_is_verified",
       "   where u.id = p_user_id and to_jsonb(u)->>'deleted_at' is null;",
       "   where u.id = p_user_id;",
       "signup_eligibility", "a deleted account keeps claiming"),
    _m("a1_identity_digest_is_case_sensitive",
       "encode(sha256(convert_to(lower(btrim(v_email)), 'UTF8')), 'hex')",
       "encode(sha256(convert_to(v_email, 'UTF8')), 'hex')",
       "signup_eligibility", "re-registering the address in another case is a new human"),
    _m("a1_identity_reuse_allowed",
       "      if v_claimant is distinct from p_user_id then",
       "      if false then",
       "signup_eligibility", "delete + re-create with the same address earns another grant"),
    _m("a1_usd_balance_not_a_hold",
       "                            where l.org_id = v_org) <> 0 then",
       "                            where l.org_id = v_org) <> 0 and false then",
       "signup_eligibility", "R72: a nonzero legacy USD account is silently granted"),
    _m("a1_denial_not_recorded",
       "    perform infrx.record_signup_denial(p_user_id, 'unverified');",
       "    null;",
       "signup_eligibility", "held/denied onboarding leaves no reason for operators"),
    _m("a1_denial_enumerates_unknown",
       "  select p_user, p_reason where exists (select 1 from public.profiles p "
       "where p.id = p_user)",
       "  select p_user, p_reason",
       "signup_eligibility", "an unknown id answers differently from an unverified one"),
    _m("a1_replay_reverifies",
       "  if exists (select 1 from infrx.signup_entitlements e\n"
       "             where e.user_id = p_user_id and e.entitlement = 'initial_signup_grant') then",
       "  if exists (select 1 from infrx.signup_entitlements e\n"
       "             where e.user_id = p_user_id and false) then",
       "signup_eligibility", "a granted individual whose verification lapses is denied their "
       "own grant"),
    _m("a1_membership_not_frozen",
       "               and w.personal_org_id = any (array[old.org_id, new.org_id])) then",
       "               and false) then",
       "signup_binding", "a second member spends through, or reads, a funded personal org"),
    _m("a1_owner_removable",
       "               and w.personal_org_id = any (array[old.org_id, new.org_id])) then",
       "               and w.personal_org_id = any (array[new.org_id])) then",
       "signup_binding", "the wallet owner is removed from the org their wallet funds"),
    _m("a1_claim_callable_by_browsers",
       "grant execute on function public.claim_signup_grant(uuid, text, uuid) to service_role;",
       "grant execute on function public.claim_signup_grant(uuid, text, uuid) "
       "to service_role, authenticated;",
       "signup_privileges", "a browser session claims for any user id it names"),
    _m("a1_claim_keeps_default_acl",
       "revoke all on function public.claim_signup_grant(uuid, text, uuid)\n"
       "  from public, anon, authenticated;",
       "-- mutant: default ACL kept",
       "signup_privileges", "Supabase's default EXECUTE lets anon call the grant"),
    _m("a1_service_writes_claims",
       "revoke all on infrx.signup_identity_claims, infrx.signup_denials, "
       "infrx.retired_individuals\n  from public, anon, authenticated, service_role;",
       "revoke all on infrx.signup_identity_claims, infrx.signup_denials, "
       "infrx.retired_individuals\n  from public, anon, authenticated;",
       "signup_privileges", "the platform role forges identity claims or retirements"),
    _m("a1_claim_race_raises",
       "      values (v_digest, p_user_id) on conflict do nothing;",
       "      values (v_digest, p_user_id);",
       "signup_race", "concurrent callback retries fail instead of answering the grant"),
    _m("a1_retired_wallet_spends",
       "create or replace trigger credit_wallet_holds_frozen before insert",
       "create or replace trigger credit_wallet_holds_frozen before update",
       "signup_retirement", "a deleted account keeps spending its credit"),
    _m("a1_retired_keeps_keys",
       "   where coalesce(user_id, created_by) = p_user and revoked_at is null;",
       "   where false;",
       "signup_retirement", "a deleted account's API keys keep working"),
    _m("a1_retired_org_not_suspended",
       "    perform infrx.set_suspension(v_org, true, 'operator_request', p_actor, p_reason,",
       "    perform infrx.set_suspension(v_org, false, 'operator_request', p_actor, p_reason,",
       "signup_retirement", "a deleted account's organization stays active"),
    _m("a1_retired_profile_kept",
       "     set email = 'retired+' || p_user || '@invalid', full_name = null, avatar_url = null",
       "     set email = email, full_name = full_name, avatar_url = avatar_url",
       "signup_retirement", "personal data survives an account deletion"),
    _m("a1_retirement_not_idempotent",
       "  if v_at is not null then\n    return v_at;\n  end if;\n  insert into "
       "infrx.retired_individuals",
       "  insert into infrx.retired_individuals",
       "signup_retirement", "a retried deletion request fails"),
    _m("a1_retired_regranted",
       "create or replace trigger credit_ledger_signup_frozen before insert on infrx.credit_ledger",
       "create or replace trigger credit_ledger_signup_frozen before update on infrx.credit_ledger",
       "signup_retirement", "the grant seam mints into a retired individual's wallet"),
    _m("a1_backfill_grants_unverified",
       "    perform infrx.record_signup_denial(p_user_id, 'unverified');\n    return query select "
       "'unverified'::text",
       "    return query select 'granted'::text",
       "signup_backfill", "the backfill counts unverified accounts as granted"),
)

_d._CHECKS.update({
    "signup_eligibility": checks_signup.check_eligibility,
    "signup_binding": checks_signup.check_binding,
    "signup_privileges": checks_signup.check_signup_privileges,
    "signup_race": lambda conn: checks_signup.check_claim_race(pgharness.connect, _d.MUT_DB),
    "signup_retirement": checks_signup.check_retirement,
    "signup_backfill": lambda conn: (checks_signup.seed_hosted(conn),
                                     checks_signup.check_backfill(conn))[1],
})
_d.MUTANTS = _d.MUTANTS + MIGRATION_MUTANTS


# --- code mutants: infrx/state/signup.py, through the shared runner ------------------
F = "state/signup.py"
RUNNER = shared.Runner(name="a1", targets=("tests/d/test_signup.py",))


def _c(name, invariant, old, new, *cases, **kw) -> shared.Mutant:
    return shared._m(name, invariant, F, old, new, *cases, **kw)


MUTANTS: tuple[shared.Mutant, ...] = (
    _c("unverified_is_forbidden", "an unverified individual reads as not found (no enumeration)",
       '        if status == "unverified":', "        if False:",
       "test_answer__denials_are_not_found_or_forbidden"),
    _c("denial_is_not_found", "a held/denied grant names itself as Forbidden",
       '        raise errors.Forbidden(f"signup grant refused: {status}")',
       '        raise errors.NotFound(f"signup grant refused: {status}")',
       "test_answer__denials_are_not_found_or_forbidden"),
    _c("binding_unchecked", "a grant bound to another org than the identity's is refused",
       "    if str(org) != identity.personal_org_id:", "    if False:",
       "test_answer__a_grant_bound_elsewhere_is_forbidden"),
    _c("replay_flag_lost", "a replay is reported as one (the operator never re-audits a grant)",
       "    return grant, status == REPLAYED", "    return grant, False",
       "test_answer__granted_and_replayed"),
    _c("unverified_identity", "an individual without verification evidence has no identity",
       "    if row is None or row[1] is None or row[2] is None:",
       "    if row is None or row[1] is None:",
       "test_identity_from__unverified_and_orgless_are_none"),
    _c("grant_outside_a_transaction", "a refused port answer rolls the grant back",
       "        async with self.pool.connection() as conn, conn.transaction():",
       "        async with self.pool.connection() as conn:",
       "test_grant_initial__one_transaction_rolled_back_on_refusal"),
    _c("operation_id_dropped", "the G6B operation id is the ledger row's (crash replay)",
       '                CLAIM, (identity.user_id, "", operation_id))).fetchone()',
       '                CLAIM, (identity.user_id, "", None))).fetchone()',
       "test_grant_initial__one_transaction_rolled_back_on_refusal"),
    _c("backfill_one_transaction", "each individual is claimed in their own transaction",
       "                with conn.transaction():", "                if True:",
       "test_backfill__pages_per_user_transactions_and_errors"),
    _c("backfill_stops_on_refusal", "one refused individual does not stop the backfill",
       '                if state is None or state == "55000" and "maintenance" in str(failed):',
       "                if True:",
       "test_backfill__pages_per_user_transactions_and_errors",
       dies_by=("FakeRefusal",)),
    _c("backfill_pages_overlap", "keyset pages advance past the last id",
       "        after = rows[-1][0]", "        after = rows[0][0]",
       "test_backfill__pages_per_user_transactions_and_errors"),
    _c("backfill_swallows_maintenance", "flag off stops the backfill loudly",
       '                if state is None or state == "55000" and "maintenance" in str(failed):',
       '                if state is None:',
       "test_backfill__maintenance_stops_the_run"),
)
