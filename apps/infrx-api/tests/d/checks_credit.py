"""D1R: the invariants the additive CREDIT / provider schema (0006 onward) claims.

Same contract as `checks.py`: each check raises `AssertionError` on failure and returns a
short summary on success, so `migration_mutants.py` can run it against a single-edit
mutant of a migration (R32/R40). Fixture identities come from `checks.py` where the D1
fixture already has them.
"""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

import psycopg
from infrx.state import migrations

from . import checks
from .checks import KEY_A

#: The migrations the hosted-state inventory and the upgrade fixture treat as "before".
BEFORE_D1R = ("supabase_shim.sql", "0001_init.sql", "0002_seed_models.sql",
              "0003_pilot_durable_schema.sql", "0004_pilot_roles_and_rpcs.sql",
              "0005_console_read_surface.sql")

# --- old-regime jobs accepted under 0003-0005 (item 1) -------------------------
OLD_QUEUED = "5a000000-0000-4000-8000-000000000001"     # accepted, USD hold held
OLD_SETTLED = "5a000000-0000-4000-8000-000000000002"    # settled in USD, pilot usage row
OLD_UNKNOWN = "5a000000-0000-4000-8000-000000000003"    # usage unknown, hold reserved


def split(files):
    """(the 0001-0005 schema, everything D1R adds) from `migrations.sql_for(...)`."""
    before = tuple(f for f in files if f[0] in BEFORE_D1R)
    after = tuple(f for f in files if f[0] not in BEFORE_D1R and f[0] != "test_clock.sql")
    clock = tuple(f for f in files if f[0] == "test_clock.sql")
    return before, after + clock


def seed_old_regime(conn, org_a: str) -> None:
    """Accepted pilot-regime (USD) jobs on the exact 0005 schema: one in flight with a
    held USD hold, one settled with its pilot usage row and USD debit, one whose usage is
    unknown and whose hold is still reserved. `seed_legacy` already put users, keys and
    positive/negative/sign-violating USD history in place."""
    cols = checks._JOB_COLUMNS.replace(",\n  accounting_regime", "")   # 0005 has no regime
    common = (f"'{org_a}', '{KEY_A}', 'nemostation/marlin-2b@2026-09-01', 'stream', "
              "@STATE, 'chat.completions', 'infrx-payload:@RID', "
              f"'{checks.DIGEST}', 4096, 512, 'pv-old', "
              "'{\"price_version\":\"pv-old\"}'::jsonb, 1.25000000, 1, 'full', "
              "'2026-09-21T00:00:00Z', '2026-09-21T00:10:00Z', 120, 10, 300, 60, 20, "
              "'2026-09-21T00:02:00Z'")
    conn.execute("""
    insert into infrx.price_versions (price_version, model_revision, input_rate_per_million,
      output_rate_per_million, token_rules_version, effective_from)
    values ('pv-old', 'nemostation/marlin-2b@2026-09-01', 0.20000000, 0.60000000, 'tr-1',
            '2026-01-01T00:00:00Z')""")

    def job(rid, handle, state, extra_cols="", extra_vals=""):
        conn.execute(f"insert into infrx.jobs ({cols}{extra_cols}) values ('{rid}', "
                     f"'{handle}', " + common.replace("@STATE", f"'{state}'").replace("@RID", rid)
                     + f"{extra_vals})")

    job(OLD_QUEUED, "job_old_queued", "queued")
    job(OLD_SETTLED, "job_old_settled", "succeeded",
        ", result_ref, outcome_cause, settlement_state, usage_certainty, debit, settled_at",
        ", 'infrx-result:old', 'completed', 'settled', 'authoritative', 0.50000000, "
        "'2026-09-21T00:05:00Z'")
    job(OLD_UNKNOWN, "job_old_unknown", "failed",
        ", outcome_cause, settlement_state, usage_certainty, settled_at, reconcile_after",
        ", 'engine_error', 'held_unknown', 'unknown', '2026-09-21T00:05:00Z', "
        "'2026-09-22T00:05:00Z'")
    conn.execute(f"""
    insert into infrx.credit_holds (request_id, org_id, key_id, amount, state, reconcile_after)
    values ('{OLD_QUEUED}', '{org_a}', '{KEY_A}', 1.25000000, 'held', null),
           ('{OLD_SETTLED}', '{org_a}', '{KEY_A}', 1.25000000, 'settled', null),
           ('{OLD_UNKNOWN}', '{org_a}', '{KEY_A}', 1.25000000, 'unknown',
            '2026-09-22T00:05:00Z');
    update infrx.wallets set reserved_total = 2.50000000 where org_id = '{org_a}';
    insert into public.usage_events (id, org_id, api_key_id, model_id, status, stream,
      prompt_tokens, completion_tokens, cost_usd, settlement_regime, outcome, job_state,
      settlement_state, usage_certainty, price_version, settlement_version)
    values ('{OLD_SETTLED}', '{org_a}', '{KEY_A}', 'nemostation/marlin-2b', 200, true,
            2000, 500, 0.50000000, 'pilot', 'completed', 'succeeded', 'settled',
            'authoritative', 'pv-old', 1);
    insert into public.credit_ledger (org_id, delta_usd, kind, reason, request_id,
      operation_id)
    values ('{org_a}', -0.50000000, 'usage', 'pilot usage', '{OLD_SETTLED}',
            'op-old-settled');
    """)


def capture_old_regime(conn) -> dict:
    """Every accepted-job, hold and USD wallet value the D1R upgrade must not change."""
    return {
        "jobs": conn.execute("""
            select request_id, org_id, state, price_version, price_snapshot::text,
                   maximum_hold::text, debit::text, settlement_state, outcome_cause,
                   settled_at, reconcile_after, model_revision
            from infrx.jobs order by request_id""").fetchall(),
        "holds": conn.execute("""select request_id, org_id, amount::text, state
                                 from infrx.credit_holds order by request_id""").fetchall(),
        "usd_wallets": conn.execute("""
            select org_id, ledger_total::text, reserved_total::text, available::text
            from infrx.wallets order by org_id""").fetchall(),
        "pilot_usage": conn.execute("""
            select id, cost_usd::text, settlement_regime, price_version
            from public.usage_events order by id""").fetchall(),
    }


# --- the 0001-0005 inventory (item 1) -----------------------------------------
#: Changes D1R makes to a 0001-0005 object, each named, each justified in the migration.
#: Anything else that differs is a rewrite of legacy schema.
ALLOWED_LEGACY_CHANGES = {
    # A CREDIT-regime job carries a rate card, not a USD price version (0006).
    ("column", "infrx.jobs", "price_version"): "NOT NULL relaxed; regime CHECK replaces it",
    ("column", "infrx.jobs", "price_snapshot"): "NOT NULL relaxed; regime CHECK replaces it",
    # 0008 appends the CREDIT columns to the legacy usage view; every old column keeps its
    # position and type, which the per-column entries (with attnum) still check.
    ("view", "public.console_usage", "definition"): "columns appended, none changed",
}


def inventory(conn) -> dict:
    """Constraints, triggers, function bodies and ACLs, relation/column ACLs, RLS,
    policies, column shapes and view definitions of `public` and `infrx`. Read with an
    empty search_path so every name is schema-qualified the same way on both images."""
    rows = {}
    with conn.transaction():
        conn.execute("set local search_path = pg_catalog")
        for kind, sql in _INVENTORY:
            for a, b, value in conn.execute(sql).fetchall():
                rows[(kind, a, b)] = value
    return rows


_INVENTORY = (
        ("constraint", """select conrelid::regclass::text, conname, pg_get_constraintdef(oid)
                          from pg_constraint
                          where connamespace in ('public'::regnamespace,
                                                 'infrx'::regnamespace)"""),
        ("trigger", """select tgrelid::regclass::text, tgname, pg_get_triggerdef(oid)
                       from pg_trigger where not tgisinternal"""),
        ("function", """select p.pronamespace::regnamespace::text,
                               p.oid::regprocedure::text,
                               md5(pg_get_functiondef(p.oid)) || ' ' || p.prosecdef
                               || ' ' || coalesce(p.proacl::text, '<default>')
                        from pg_proc p
                        where p.pronamespace in ('public'::regnamespace,
                                                 'infrx'::regnamespace)
                          and p.prokind = 'f'
                          and not exists (select 1 from pg_depend d
                                          where d.objid = p.oid and d.deptype = 'e')"""),
        ("relation", """select c.relkind::text, c.oid::regclass::text,
                               coalesce(c.relacl::text, '<default>') || ' rls='
                               || c.relrowsecurity
                        from pg_class c
                        where c.relnamespace in ('public'::regnamespace,
                                                 'infrx'::regnamespace)
                          and c.relkind in ('r', 'v', 'S')"""),
        ("column", """select a.attrelid::regclass::text, a.attname,
                             a.attnum || ' ' || format_type(a.atttypid, a.atttypmod)
                             || ' notnull='
                             || a.attnotnull || ' acl=' || coalesce(a.attacl::text, '')
                      from pg_attribute a join pg_class c on c.oid = a.attrelid
                      where c.relnamespace in ('public'::regnamespace,
                                               'infrx'::regnamespace)
                        and c.relkind in ('r', 'v') and a.attnum > 0
                        and not a.attisdropped"""),
        ("policy", """select schemaname || '.' || tablename, policyname,
                             cmd || ' ' || roles::text || ' ' || coalesce(qual, '')
                             || ' ' || coalesce(with_check, '')
                      from pg_policies where schemaname in ('public', 'infrx')"""),
        ("view", """select schemaname || '.' || viewname, 'definition', md5(definition)
                    from pg_views where schemaname in ('public', 'infrx')"""),
)


def check_legacy_schema_unchanged(conn, before: dict) -> str:
    """Item 1: nothing 0001-0005 created is rewritten, dropped or re-granted by D1R -
    the wallet trigger, the narrow financial RPC privileges, every constraint, trigger,
    policy, function body and ACL are identical - except the named allowances."""
    after = inventory(conn)
    changed = []
    for key, old in sorted(before.items()):
        new = after.get(key)
        if new == old or (new is not None and key in ALLOWED_LEGACY_CHANGES):
            continue
        changed.append(f"{key}: {old!r} -> {new!r}")
    assert not changed, "D1R rewrote 0001-0005 schema:\n  " + "\n  ".join(changed[:30])
    # Named, because these are what the brief lists: the USD wallet trigger and the
    # narrow financial RPC grants still exist exactly as they were.
    for key in (("trigger", "public.credit_ledger", "credit_ledger_moves_wallet"),
                ("function", "infrx", "infrx.grant_credit(jsonb)"),
                ("function", "infrx", "infrx.admit(jsonb)"),
                ("function", "public", "public.org_wallet_summary(uuid)")):
        assert key in before and after.get(key) == before[key], f"{key} changed or vanished"
    return (f"{len(before)} objects of 0001-0005 unchanged "
            f"({len(ALLOWED_LEGACY_CHANGES)} named allowances)")


def check_old_regime_preserved(conn, before: dict) -> str:
    """USD history (ledger rows, sums, usage values), accepted old-regime jobs, their USD
    holds, the USD wallet summaries and the pilot usage rows are identical - to the
    digit - after D1R."""
    legacy = checks.capture_legacy(conn, before["legacy"]["org_a"], before["legacy"]["org_b"])
    assert legacy == before["legacy"], "USD history changed across D1R"
    before, after = before["old"], capture_old_regime(conn)
    for part in before:
        assert after[part] == before[part], \
            f"old-regime {part} changed:\n  {before[part]}\n  -> {after[part]}"
    return (f"{len(after['jobs'])} accepted jobs, {len(after['holds'])} USD holds and "
            f"{len(after['usd_wallets'])} USD wallets value-identical")


def upgrade05(pgharness, database: str, files) -> tuple:
    """Build 0001-0002, seed legacy history, apply 0003-0005, accept old-regime jobs,
    take the inventory, then apply everything D1R adds. Returns the connection and the
    captured `before` state."""
    first = tuple(f for f in files if f[0] in ("supabase_shim.sql", "0001_init.sql",
                                               "0002_seed_models.sql"))
    pilot = tuple(f for f in files if f[0] in BEFORE_D1R and f not in first)
    _, d1r = split(files)
    pgharness.recreate(database)
    pgharness.apply(database, first)
    conn = pgharness.connect(database)
    legacy = checks.seed_legacy(conn)
    pgharness.apply(database, pilot)
    seed_old_regime(conn, legacy["org_a"])
    before = {"legacy": checks.capture_legacy(conn, legacy["org_a"], legacy["org_b"]),
              "inventory": inventory(conn), "old": capture_old_regime(conn)}
    pgharness.apply(database, d1r)
    return conn, before



# =============================================================================
# item 2: CREDIT wallets, ledger, entitlements, the initial grant
# =============================================================================
FIXTURES = Path(__file__).resolve().parents[2] / "infrx" / "contracts" / "fixtures" / "v2"

# Individuals created for the CREDIT fixture, one auth user (and so one personal
# organization, made by 0001's signup trigger) each, in separate statements so each
# personal organization is unambiguous.
CONSUMER_1 = "c1000000-0000-4000-8000-000000000001"
CONSUMER_2 = "c1000000-0000-4000-8000-000000000002"
UNGRANTED = "c1000000-0000-4000-8000-000000000003"      # a verified-later individual
SHARED = "c1000000-0000-4000-8000-000000000004"         # personal org with a second member
RACER = "c1000000-0000-4000-8000-000000000005"          # granted only by the race check
PROVIDER_DEV_USER = "c1000000-0000-4000-8000-000000000006"
PROVIDER_ADMIN_USER = "c1000000-0000-4000-8000-000000000007"
EVIDENCE = "email_verification/2026-09-22/test"


def _load(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def personal_org(conn, user: str) -> str:
    return str(conn.execute("select id from public.organizations where created_by = %s "
                            "order by created_at, id limit 1", (user,)).fetchone()[0])


def wallet_of(conn, user: str) -> str | None:
    row = conn.execute("select wallet_id from infrx.credit_wallets "
                       "where owner_user_id = %s and kind = 'consumer'", (user,)).fetchone()
    return str(row[0]) if row else None


def set_flag(conn, name: str, enabled: bool) -> None:
    conn.execute("update infrx.feature_flags set enabled = %s, updated_by = 'd1r-test', "
                 "reason = 'test' where name = %s", (enabled, name))


def grant(conn, user: str, *, campaign: str = "launch_2026_09", op: str | None = None,
          evidence: str = EVIDENCE) -> tuple:
    return conn.execute("select * from infrx.grant_signup_credit(%s, %s, %s, %s)",
                        (user, evidence, campaign, op)).fetchone()


def seed_credit(conn) -> None:
    """On top of `checks.seed_fixtures`: the individuals, the flags on, two grants."""
    for user in (CONSUMER_1, CONSUMER_2, UNGRANTED, SHARED, RACER, PROVIDER_DEV_USER,
                 PROVIDER_ADMIN_USER):
        conn.execute("insert into auth.users (id, email) values (%s, %s)",
                     (user, f"{user[:8]}-{user[-2:]}@example.com"))
    conn.execute("insert into public.org_members (org_id, user_id, role) values (%s, %s, "
                 "'member')", (personal_org(conn, SHARED), CONSUMER_2))
    set_flag(conn, "signup_grant", True)
    set_flag(conn, "credit_admission", True)
    grant(conn, CONSUMER_1)
    grant(conn, CONSUMER_2)
    seed_registry(conn)


# --- the operator seed and the provider fixture (item 3) -------------------------
NEMO = "b0000001-0000-4000-8000-000000000001"
OTHER_PROVIDER = "b0000009-0000-4000-8000-000000000009"
OTHER_ENDPOINT = "c0000009-0000-4000-8000-000000000009"
MODEL = "d0000001-0000-4000-8000-000000000001"
SERVING = "d0000003-0000-4000-8000-000000000003"
DEV_DEPLOYMENT = "c0000003-0000-4000-8000-000000000003"
PUBLIC_DEPLOYMENT = "c0000004-0000-4000-8000-000000000004"
PROD_ENDPOINT = "c0000002-0000-4000-8000-000000000002"
DEV_ENDPOINT = "c0000001-0000-4000-8000-000000000001"
CARD = "rc_marlin2b_2026_09_provisional"
POLICY = "dap_2026_09_01"
DEV_CARD = "rc_marlin2b_dev_internal"
PROVIDER_WALLET = "b0000003-0000-4000-8000-000000000003"


def apply_seed(conn) -> None:
    conn.execute(migrations.SEED_MARLIN.read_text())


def seed_registry(conn) -> None:
    """The operator seed exactly as an operator runs it, plus what the checks need
    beside it: a second provider, provider members, the provider's dev wallet funded
    by an audited allocation, and an internal card for the private dev deployment."""
    apply_seed(conn)
    conn.execute(f"""
    insert into infrx.provider_orgs (provider_org_id, slug, display_name, created_by)
      values ('{OTHER_PROVIDER}', 'other', 'NemoStation', 'ops');   -- same display text
    insert into infrx.endpoints (endpoint_id, provider_org_id, name, environment, created_by)
      values ('{OTHER_ENDPOINT}', '{OTHER_PROVIDER}', 'other', 'prod', 'ops');
    insert into infrx.provider_memberships (provider_org_id, user_id, role, granted_by) values
      ('{NEMO}', '{PROVIDER_DEV_USER}', 'developer', 'ops@infrx'),
      ('{NEMO}', '{PROVIDER_ADMIN_USER}', 'administrator', 'ops@infrx');
    insert into infrx.credit_wallets (wallet_id, kind, owner_provider_org_id)
      values ('{PROVIDER_WALLET}', 'provider_dev', '{NEMO}');
    insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, operation_id,
      actor, reason)
      values ('{PROVIDER_WALLET}', 'provider_dev', 'operator_allocation', 500,
              gen_random_uuid(), 'ops@infrx', 'preview budget');
    insert into infrx.rate_card_versions (rate_card_version, model_id, deployment_revision_id,
      serving_version_id, input_rate_per_million, output_rate_per_million, effective_at,
      approved_by, provisional)
      values ('{DEV_CARD}', '{MODEL}', '{DEV_DEPLOYMENT}', '{SERVING}', 400, 1200,
              '2026-09-01T00:00:00Z', 'ops@infrx', true);
    """)


class _Allowed(Exception):
    pass


def attempt(conn, sql: str, params=None, *, session: str | None = None) -> str | None:
    """None when the statement succeeded (it is rolled back either way), else the
    SQLSTATE, the constraint name if any, and the first line of the message."""
    try:
        with conn.transaction():
            if session:
                conn.execute(checks.SESSIONS[session])
            conn.execute(sql, params)
            raise _Allowed()
    except _Allowed:
        return None
    except psycopg.Error as refused:
        name = getattr(refused.diag, "constraint_name", None) or ""
        return f"{refused.sqlstate} {name} {str(refused).splitlines()[0][:120]}".strip()


def _all_refused(conn, cases, what: str) -> int:
    survived = [label for label, sql in cases if attempt(conn, sql) is None]
    assert not survived, f"the schema accepted ({what}):\n  " + "\n  ".join(survived)
    return len(cases)


def _all_accepted(conn, cases, what: str) -> int:
    refused = [f"{label}: {why}" for label, sql in cases
               if (why := attempt(conn, sql)) is not None]
    assert not refused, f"the schema refused ({what}):\n  " + "\n  ".join(refused)
    return len(cases)


def _identity_cases(conn) -> tuple[tuple, tuple]:
    w1, w2 = wallet_of(conn, CONSUMER_1), wallet_of(conn, CONSUMER_2)
    o1, o2 = personal_org(conn, CONSUMER_1), personal_org(conn, CONSUMER_2)
    adj = "00000000-0000-4000-8000-00000000ad01"
    ledger = ("insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, "
              "operation_id, request_id, actor) values ")
    refused = (
        ("a consumer wallet without its personal org",
         f"insert into infrx.credit_wallets (kind, owner_user_id) values "
         f"('consumer', '{UNGRANTED}')"),
        ("a consumer wallet that also names a provider owner",
         f"insert into infrx.credit_wallets (kind, owner_user_id, personal_org_id, "
         f"owner_provider_org_id) values ('consumer', '{UNGRANTED}', "
         f"'{personal_org(conn, UNGRANTED)}', gen_random_uuid())"),
        ("a provider_dev wallet owned by an individual",
         f"insert into infrx.credit_wallets (kind, owner_user_id) values "
         f"('provider_dev', '{UNGRANTED}')"),
        ("a second consumer wallet for one individual",
         f"insert into infrx.credit_wallets (kind, owner_user_id, personal_org_id) values "
         f"('consumer', '{CONSUMER_1}', '{personal_org(conn, UNGRANTED)}')"),
        ("a second wallet funded through one personal org",
         f"insert into infrx.credit_wallets (kind, owner_user_id, personal_org_id) values "
         f"('consumer', '{UNGRANTED}', '{o1}')"),
        ("a wallet denominated in USD",
         f"insert into infrx.credit_wallets (kind, unit, owner_user_id, personal_org_id) "
         f"values ('consumer', 'USD', '{UNGRANTED}', '{personal_org(conn, UNGRANTED)}')"),
        ("a wallet created with a balance",
         f"insert into infrx.credit_wallets (kind, owner_user_id, personal_org_id, "
         f"ledger_total) values ('consumer', '{UNGRANTED}', "
         f"'{personal_org(conn, UNGRANTED)}', 5)"),
        ("changing a wallet's kind",
         f"update infrx.credit_wallets set kind = 'provider_dev' where wallet_id = '{w1}'"),
        ("changing a wallet's owner",
         f"update infrx.credit_wallets set owner_user_id = '{CONSUMER_2}' "
         f"where wallet_id = '{w1}'"),
        ("rebinding a wallet to another org",
         f"update infrx.credit_wallets set personal_org_id = '{o2}' where wallet_id = '{w1}'"),
        ("relabelling a wallet's unit",
         f"update infrx.credit_wallets set unit = 'USD' where wallet_id = '{w1}'"),
        ("moving a total without a ledger row (even as the owner)",
         f"update infrx.credit_wallets set ledger_total = 20000 where wallet_id = '{w1}'"),
        ("moving a reservation without a hold",
         f"update infrx.credit_wallets set reserved_total = 1 where wallet_id = '{w1}'"),
        ("deleting a wallet", f"delete from infrx.credit_wallets where wallet_id = '{w1}'"),
        ("a transfer kind",
         ledger + f"('{w1}', 'consumer', 'transfer', -5, gen_random_uuid(), null, 'x')"),
        ("a signup grant of another amount",
         ledger + f"('{w1}', 'consumer', 'signup_grant', 5000, gen_random_uuid(), null, "
                  f"'platform')"),
        ("a second signup grant into a wallet, without an entitlement",
         ledger + f"('{w1}', 'consumer', 'signup_grant', 10000, gen_random_uuid(), null, "
                  f"'platform')"),
        ("a ledger row that lies about its wallet's kind",
         ledger + f"('{w1}', 'provider_dev', 'operator_adjustment', 5, gen_random_uuid(), "
                  f"null, 'ops')"),
        ("an operator allocation minting consumer credit",
         ledger + f"('{w1}', 'consumer', 'operator_allocation', 5, gen_random_uuid(), "
                  f"null, 'ops')"),
        ("a positive inference debit",
         ledger + f"('{w1}', 'consumer', 'inference_debit', 5, gen_random_uuid(), "
                  f"gen_random_uuid(), 'svc')"),
        ("an inference debit that names no request",
         ledger + f"('{w1}', 'consumer', 'inference_debit', -5, gen_random_uuid(), null, "
                  f"'svc')"),
        ("a grant that claims to settle a request",
         ledger + f"('{w1}', 'consumer', 'operator_adjustment', 5, gen_random_uuid(), "
                  f"gen_random_uuid(), 'ops')"),
        ("a zero movement",
         ledger + f"('{w1}', 'consumer', 'operator_adjustment', 0, gen_random_uuid(), null, "
                  f"'ops')"),
        ("an anonymous movement",
         ledger + f"('{w1}', 'consumer', 'operator_adjustment', 5, gen_random_uuid(), null, "
                  f"'  ')"),
        ("a debit past zero",
         ledger + f"('{w1}', 'consumer', 'operator_adjustment', -10000.00000001, "
                  f"gen_random_uuid(), null, 'ops')"),
        ("a reused operation id",
         ledger + f"('{w2}', 'consumer', 'operator_adjustment', 5, "
                  f"(select operation_id from infrx.credit_ledger where wallet_id = '{w1}' "
                  f"limit 1), null, 'ops')"),
        ("editing ledger history", f"update infrx.credit_ledger set amount = 20000 "
                                   f"where wallet_id = '{w1}'"),
        ("deleting ledger history", f"delete from infrx.credit_ledger where wallet_id = '{w1}'"),
        ("truncating the CREDIT ledger", "truncate infrx.credit_ledger cascade"),
        ("truncating CREDIT wallets", "truncate infrx.credit_wallets cascade"),
        ("a second initial entitlement under another campaign",
         f"insert into infrx.signup_entitlements (user_id, entitlement, wallet_id, amount, "
         f"verification_evidence_ref, ledger_operation_id, campaign_version) "
         f"select user_id, entitlement, wallet_id, amount, 'again', gen_random_uuid(), "
         f"'relaunch' from infrx.signup_entitlements where user_id = '{CONSUMER_1}'"),
        ("an entitlement paid into another individual's wallet",
         f"insert into infrx.signup_entitlements (user_id, entitlement, wallet_id, amount, "
         f"verification_evidence_ref, ledger_operation_id) select '{UNGRANTED}', "
         f"entitlement, wallet_id, amount, 'x', ledger_operation_id "
         f"from infrx.signup_entitlements where user_id = '{CONSUMER_1}'"),
        ("an entitlement whose money is not a signup row",
         f"with a as (insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, "
         f"operation_id, actor) values ('{w2}', 'consumer', 'operator_adjustment', 1, "
         f"'{adj}', 'ops') returning 1) "
         f"insert into infrx.signup_entitlements (user_id, entitlement, wallet_id, amount, "
         f"verification_evidence_ref, ledger_operation_id) values ('{CONSUMER_2}', "
         f"'initial_signup_grant_2', '{w2}', 10000, 'x', '{adj}')"),
        ("an entitlement for another amount",
         f"update infrx.signup_entitlements set amount = 20000 where user_id = '{CONSUMER_1}'"),
        ("re-dating or re-attributing an entitlement",
         f"update infrx.signup_entitlements set campaign_version = 'x' "
         f"where user_id = '{CONSUMER_1}'"),
        ("an entitlement with no verification evidence",
         f"insert into infrx.signup_entitlements (user_id, entitlement, wallet_id, amount, "
         f"verification_evidence_ref, ledger_operation_id) values ('{UNGRANTED}', "
         f"'initial_signup_grant', '{w1}', 10000, ' ', gen_random_uuid())"),
    )
    accepted = (
        ("an audited operator adjustment, both signs",
         ledger + f"('{w1}', 'consumer', 'operator_adjustment', 2.5, gen_random_uuid(), "
                  f"null, 'ops@infrx'), ('{w1}', 'consumer', 'operator_adjustment', "
                  f"-2.5, gen_random_uuid(), null, 'ops@infrx')"),
        ("spending exactly to zero",
         ledger + f"('{w1}', 'consumer', 'operator_adjustment', -10000, gen_random_uuid(), "
                  f"null, 'ops@infrx')"),
    )
    return refused, accepted


def check_credit_identity(conn) -> str:
    """CREDIT-IDENTITY / R59-7 / R67 / R71: ownership is an exclusive-or by kind, one
    wallet per individual and per personal org, kind/unit/owner immutable, totals move
    only by the ledger, the ledger vocabulary is closed with sign and wallet-kind rules,
    and the entitlement is one per individual, paid into their own wallet by its own
    signup row."""
    refused, accepted = _identity_cases(conn)
    n = _all_refused(conn, refused, "identity")
    m = _all_accepted(conn, accepted, "identity controls")
    return f"{n} identity/ledger violations refused, {m} controls accepted"


def check_credit_reconciles(conn) -> str:
    """R59-7: every CREDIT summary equals its ledger and its active holds."""
    drift = conn.execute("select wallet_id, ledger_drift, reserved_drift from "
                         "infrx.credit_wallet_reconciliation where ledger_drift <> 0 "
                         "or reserved_drift <> 0").fetchall()
    assert not drift, f"CREDIT wallets disagree with their ledger/holds: {drift}"
    n, = conn.execute("select count(*) from infrx.credit_wallets").fetchone()
    assert n >= 2, "the reconciliation saw no wallet"
    return f"{n} CREDIT wallets reconcile"


def check_grant(conn) -> str:
    """CREDIT-GRANT / A1 seam: one transaction creates the wallet, the entitlement and
    exactly one +10000.00000000 row; every retry (another operation id, campaign or
    organization) returns the same grant; a missing flag is a maintenance refusal; blank
    evidence, a shared organization and an unknown user are refused."""
    with conn.transaction():
        user, wallet, op, amount, _at, replayed = grant(conn, UNGRANTED)
        assert str(user) == UNGRANTED and not replayed and amount == "10000.00000000", \
            f"first grant: {(user, amount, replayed)}"
        row = conn.execute("select kind, unit, personal_org_id, ledger_total::text, "
                           "reserved_total::text, revision from infrx.credit_wallets "
                           "where wallet_id = %s", (wallet,)).fetchone()
        assert row == ("consumer", "CREDIT", uuid.UUID(personal_org(conn, UNGRANTED)),
                       "10000.00000000", "0.00000000", 1), \
            f"granted wallet: {row}"
        # Another organization, another campaign, another operation id: same grant.
        conn.execute("insert into public.organizations (name, slug, created_by) values "
                     "('second', 'second-org-x', %s)", (UNGRANTED,))
        again = grant(conn, UNGRANTED, campaign="relaunch_2027",
                      op="00000000-0000-4000-8000-00000000aa01", evidence="other")
        assert again[5] is True and again[2] == op and again[1] == wallet, \
            f"a retry minted or moved the grant: {again}"
        n, = conn.execute("select count(*) from infrx.credit_ledger where wallet_id = %s",
                          (wallet,)).fetchone()
        assert n == 1, f"{n} ledger rows for one grant"
        raise psycopg.Rollback()
    for label, sql, code in (
        ("blank evidence", f"select infrx.grant_signup_credit('{UNGRANTED}', ' ')", "22023"),
        ("a shared personal organization",
         f"select infrx.grant_signup_credit('{SHARED}', 'e')", "55000"),
        ("an unknown user", "select infrx.grant_signup_credit(gen_random_uuid(), 'e')",
         "P0002"),
    ):
        why = attempt(conn, sql)
        assert why is not None and why.startswith(code), f"{label}: {why!r}"
    with conn.transaction():
        set_flag(conn, "signup_grant", False)
        why = attempt(conn, f"select infrx.grant_signup_credit('{UNGRANTED}', 'e')")
        raise psycopg.Rollback()
    assert why is not None and why.startswith("55000"), f"grant with the flag off: {why!r}"
    return "grant: one row, idempotent across op/campaign/org, 4 refusals"


def check_grant_race(connect, database: str, attempts: int = 8) -> str:
    """CREDIT-GRANT under a race: N concurrent callers for one individual produce
    exactly one ledger row, and every caller is handed that same row."""
    barrier = threading.Barrier(attempts)
    out: list = []

    def call() -> None:
        with connect(database) as c:
            barrier.wait()
            out.append(grant(c, RACER, op=None))

    threads = [threading.Thread(target=call) for _ in range(attempts)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with connect(database) as c:
        rows = c.execute("select l.operation_id from infrx.credit_ledger l "
                         "join infrx.credit_wallets w using (wallet_id) "
                         "where w.owner_user_id = %s", (RACER,)).fetchall()
    assert len(out) == attempts, f"only {len(out)} of {attempts} callers returned"
    assert len(rows) == 1, f"{len(rows)} ledger rows after a race of {attempts}"
    assert {r[2] for r in out} == {rows[0][0]}, "callers were handed different grants"
    assert sum(1 for r in out if not r[5]) == 1, "not exactly one caller issued the grant"
    return f"{attempts} concurrent grants -> 1 ledger row, 1 issuer, {attempts - 1} replays"


def check_no_unit_conversion(conn) -> str:
    """R64/R65/R73: no function or view touches a USD amount and a CREDIT amount
    together (so none can sum or convert across units), and nothing is named like a
    converter. `public.console_usage` shows the two columns side by side, never combined."""
    usd = ("delta_usd", "cost_usd", "infrx.wallets", "infrx.credit_holds", "usd_per_m",
           "price_versions", "public.credit_ledger")
    credit = ("credit_wallets", "infrx.credit_ledger", "credit_wallet_holds",
              "charged_credits", "signup_entitlements", "rate_card_versions")
    side_by_side = {"public.console_usage"}
    found = []
    objects = conn.execute("""
        select n.nspname || '.' || p.proname, lower(p.prosrc) from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname in ('public', 'infrx')
          and not exists (select 1 from pg_depend d where d.objid = p.oid and d.deptype = 'e')
        union all
        select schemaname || '.' || viewname, lower(definition) from pg_views
        where schemaname in ('public', 'infrx')""").fetchall()
    for name, src in objects:
        if any(w in name for w in ("convert", "exchange", "to_credit", "to_usd", "usd_to",
                                   "credit_to")):
            found.append(f"{name}: named like a converter")
        both = any(t in src for t in usd) and any(t in src for t in credit)
        if both and name not in side_by_side:
            found.append(f"{name}: reads USD and CREDIT amounts together")
        if both and name in side_by_side and "sum(" in src:
            found.append(f"{name}: aggregates across units")
    assert not found, "cross-unit code exists:\n  " + "\n  ".join(found)
    return f"{len(objects)} functions/views: none converts or combines units"


def check_money_unit_cases(conn) -> str:
    """The F2P `money_unit_cases` fixture as SQL: every valid amount round-trips through
    numeric(20,8) to its canonical eight-digit text, and the out-of-range one is refused
    by the column type. (Rounding refusals - `0.000000001`, `1e3` - are the service
    parser's: PostgreSQL's numeric input rounds, so the trust boundary is money_units.)"""
    n = 0
    for case in _load("money_unit_cases"):
        if case["valid"]:
            text, = conn.execute("select %s::numeric(20,8)::text", (case["input"],)).fetchone()
            assert text == case["canonical"], f"{case}: the database spells it {text}"
            n += 1
        elif case["input"] == "1000000000000.00000000":
            assert attempt(conn, "select %s::numeric(20,8)", (case["input"],)) is not None, \
                "numeric(20,8) accepted 10^12"
            n += 1
    return f"{n} money_unit_cases executed as SQL"


def check_regime_on_usage(conn) -> str:
    """CREDIT-UNITS on usage: a row states its regime; a legacy_usd row carries no
    CREDIT field, and the regime is one of the two."""
    base = ("insert into public.usage_events (id, org_id, model_id, status, "
            "accounting_regime, charged_credits) values (gen_random_uuid(), "
            f"'{checks.ORG_A}', 'nemostation/marlin-2b', 200, ")
    n = _all_refused(conn, (
        ("a legacy row with a CREDIT charge", base + "'legacy_usd', 1)"),
        ("an unknown regime", base + "'usd', null)"),
        ("a CREDIT row with no card or serving revision", base + "'credit', 1)"),
    ), "usage regime")
    # The deployed gateway's own insert (no regime named) is still the legacy writer.
    m = _all_accepted(conn, (
        ("the deployed gateway's usage row",
         "insert into public.usage_events (id, org_id, api_key_id, model_id, status, stream, "
         "prompt_tokens, completion_tokens, video_seconds, ttft_ms, latency_ms, cached, "
         f"cost_usd) values (gen_random_uuid(), '{checks.ORG_A}', '{checks.KEY_A}', "
         "'nemostation/marlin-2b', 200, true, 10, 5, 1.5, 100, 900, false, 0.00000350)"),
    ), "legacy writer")
    regime, = conn.execute("select count(*) from public.usage_events "
                           "where accounting_regime <> 'legacy_usd'").fetchone()
    return f"{n} regime violations refused, {m} legacy writer accepted ({regime} CREDIT rows)"


# =============================================================================
# item 3: the registry
# =============================================================================
def registry_counts(conn) -> tuple:
    return tuple(conn.execute(f"select count(*) from infrx.{t}").fetchone()[0] for t in (
        "provider_orgs", "model_versions", "serving_versions", "endpoints",
        "deployment_revisions", "rate_card_versions", "data_access_policies",
        "catalog_listings"))


def _as_fixture(value):
    """A database value spelled the way the fixture JSON spells it."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    return value


def check_seed_is_the_fixtures(conn) -> str:
    """The operator seed IS the F2P fixture records (02: operators seed the identical
    records): serving revision, both deployment revisions and the rate card match their
    fixture JSON field for field; the card is labelled provisional (P-01); a second run
    of the seed changes nothing."""
    before = registry_counts(conn)
    apply_seed(conn)
    assert registry_counts(conn) == before, "re-running the seed changed the registry"
    compared = 0
    card = _load("rate_card_marlin")
    row = conn.execute("select rate_card_version, unit, meter, model_id, deployment_revision_id, "
                       "serving_version_id, input_rate_per_million::text, "
                       "output_rate_per_million::text, effective_at, approved_by, status, "
                       "hold_rounding, debit_rounding, provisional from infrx.rate_card_versions "
                       "where rate_card_version = %s", (card["rate_card_version"],)).fetchone()
    keys = ("rate_card_version", "unit", "meter", "model_id", "deployment_revision_id",
            "serving_version_id", "input_rate_per_million", "output_rate_per_million",
            "effective_at", "approved_by", "status", "hold_rounding", "debit_rounding")
    got = dict(zip(keys, map(_as_fixture, row)))
    for k in keys:
        assert got[k] == card[k], f"rate card {k}: database {got[k]!r}, fixture {card[k]!r}"
    assert row[-1] is True and "P-01" in got["approved_by"], "the card is not labelled provisional"
    compared += len(keys)
    for name in ("deployment_revision_public", "deployment_revision_private_dev"):
        fx = _load(name)
        keys = ("deployment_revision_id", "endpoint_id", "environment", "max_input_tokens",
                "max_output_tokens", "provider_org_id", "serving_version_id", "state",
                "visibility", "created_at")
        row = conn.execute(f"select {', '.join(keys)} from infrx.deployment_revisions "
                           "where deployment_revision_id = %s",
                           (fx["deployment_revision_id"],)).fetchone()
        got = dict(zip(keys, map(_as_fixture, row)))
        for k in keys:
            assert got[k] == fx[k], f"{name}.{k}: database {got[k]!r}, fixture {fx[k]!r}"
        compared += len(keys)
    fx = _load("serving_revision")
    keys = ("serving_version_id", "model_id", "model_version_id", "provider_org_id",
            "public_model_id", "revision_label", "model_repo", "model_commit",
            "weight_shard_digests", "tokenizer_digest", "chat_template_digest",
            "digest_source", "prompt_harness_ref", "preprocessor_profile_version",
            "runtime_image_ref", "engine_options_digest", "precision", "capability",
            "created_at")
    row = conn.execute("""
        select s.serving_version_id, s.model_id, s.model_version_id, s.provider_org_id, m.id,
               s.revision_label, v.model_repo, v.model_commit, v.weight_shard_digests,
               v.tokenizer_digest, v.chat_template_digest, v.digest_source,
               s.prompt_harness_ref, s.preprocessor_profile_version, s.runtime_image_ref,
               s.engine_options_digest, s.precision, s.capability, s.created_at
        from infrx.serving_versions s
        join infrx.model_versions v on v.model_version_id = s.model_version_id
        join public.models m on m.model_uuid = s.model_id
        where s.serving_version_id = %s""", (fx["serving_version_id"],)).fetchone()
    got = dict(zip(keys, map(_as_fixture, row)))
    for k in keys:
        assert got[k] == fx[k], f"serving_revision.{k}: database {got[k]!r}, fixture {fx[k]!r}"
    assert "runtime_image_digest" not in fx and conn.execute(
        "select runtime_image_digest is null from infrx.serving_versions "
        "where serving_version_id = %s", (fx["serving_version_id"],)).fetchone()[0], \
        "a moving-tag runtime gained a digest (R76)"
    compared += len(keys)
    return f"seed == F2P fixtures on {compared} fields; provisional; re-run is a no-op"


def _registry_cases() -> tuple[tuple, tuple]:
    dep = ("insert into infrx.deployment_revisions (endpoint_id, provider_org_id, environment, "
           "serving_version_id, visibility, state, max_input_tokens, max_output_tokens, "
           "created_by) values ")
    card = ("insert into infrx.rate_card_versions (rate_card_version, model_id, "
            "deployment_revision_id, serving_version_id, input_rate_per_million, "
            "output_rate_per_million, effective_at, approved_by, provisional")
    mv = ("insert into infrx.model_versions (model_id, provider_org_id, model_repo, "
          "model_commit, weight_shard_digests, tokenizer_digest, chat_template_digest, "
          "digest_source, created_by) values ")
    sha = "'sha256:" + "a" * 64 + "'"
    good_mv = (f"'{MODEL}', '{NEMO}', 'r', '{'f' * 40}', array[{sha}], {sha}, {sha}, "
               f"'served_bytes', 'ops')")
    listing = ("insert into infrx.catalog_listings (public_model_id, version, model_id, "
               "deployment_revision_id, serving_version_id, rate_card_version, effective_at, "
               "approved_by) values ")
    refused = (
        ("a public deployment on a dev endpoint",
         dep + f"('{DEV_ENDPOINT}', '{NEMO}', 'dev', '{SERVING}', 'public', 'active', 1, 1, 'o')"),
        ("a public deployment that never passed validation",
         dep + f"('{PROD_ENDPOINT}', '{NEMO}', 'prod', '{SERVING}', 'public', 'draft', 1, 1, 'o')"),
        ("a private deployment in a public state",
         dep + f"('{PROD_ENDPOINT}', '{NEMO}', 'prod', '{SERVING}', 'private', 'active', 1, 1, 'o')"),
        ("a deployment whose environment disagrees with its endpoint",
         dep + f"('{DEV_ENDPOINT}', '{NEMO}', 'prod', '{SERVING}', 'private', 'draft', 1, 1, 'o')"),
        ("deploying another provider's serving version",
         dep + f"('{OTHER_ENDPOINT}', '{OTHER_PROVIDER}', 'prod', '{SERVING}', 'private', "
               f"'draft', 1, 1, 'o')"),
        ("deploying onto another provider's endpoint",
         dep + f"('{OTHER_ENDPOINT}', '{NEMO}', 'prod', '{SERVING}', 'private', 'draft', 1, 1, 'o')"),
        ("re-pointing a deployment at another serving version",
         f"update infrx.deployment_revisions set serving_version_id = gen_random_uuid() "
         f"where deployment_revision_id = '{PUBLIC_DEPLOYMENT}'"),
        ("making a private deployment public by update",
         f"update infrx.deployment_revisions set visibility = 'public' "
         f"where deployment_revision_id = '{DEV_DEPLOYMENT}'"),
        ("widening a deployment's validated limits",
         f"update infrx.deployment_revisions set max_output_tokens = 100000 "
         f"where deployment_revision_id = '{PUBLIC_DEPLOYMENT}'"),
        ("an active deployment going back to draft",
         f"update infrx.deployment_revisions set state = 'validating' "
         f"where deployment_revision_id = '{PUBLIC_DEPLOYMENT}'"),
        ("resurrecting a retired deployment",
         f"with r as (update infrx.deployment_revisions set state = 'retired' "
         f"where deployment_revision_id = '{DEV_DEPLOYMENT}' returning 1) "
         f"select 1; update infrx.deployment_revisions set state = 'ready_private' "
         f"where deployment_revision_id = '{DEV_DEPLOYMENT}'"),
        ("deleting a deployment", f"delete from infrx.deployment_revisions "
                                  f"where deployment_revision_id = '{DEV_DEPLOYMENT}'"),
        ("re-pricing an admitted card", f"update infrx.rate_card_versions set "
         f"output_rate_per_million = 1 where rate_card_version = '{CARD}'"),
        ("deleting a card", f"delete from infrx.rate_card_versions where rate_card_version = '{CARD}'"),
        ("a USD card", card + f", unit) values ('rc_x', '{MODEL}', '{PUBLIC_DEPLOYMENT}', "
                              f"'{SERVING}', 1, 1, now(), 'o', false, 'USD')"),
        ("an unknown meter", card + f", meter) values ('rc_x', '{MODEL}', '{PUBLIC_DEPLOYMENT}', "
                                    f"'{SERVING}', 1, 1, now(), 'o', false, 'seconds-v1')"),
        ("a negative rate", card + f") values ('rc_x', '{MODEL}', '{PUBLIC_DEPLOYMENT}', "
                                   f"'{SERVING}', -1, 1, now(), 'o', false)"),
        ("an unapproved card", card + f", status) values ('rc_x', '{MODEL}', "
                                      f"'{PUBLIC_DEPLOYMENT}', '{SERVING}', 1, 1, now(), 'o', "
                                      f"false, 'proposed')"),
        ("a card with no approver", card + f") values ('rc_x', '{MODEL}', '{PUBLIC_DEPLOYMENT}', "
                                           f"'{SERVING}', 1, 1, now(), ' ', false)"),
        ("a card pricing a serving version its deployment does not run",
         card + f") values ('rc_x', '{MODEL}', '{PUBLIC_DEPLOYMENT}', gen_random_uuid(), 1, 1, "
                f"now(), 'o', false)"),
        ("a listing of a private dev deployment",
         listing + f"('nemostation/marlin-2b', 9, '{MODEL}', '{DEV_DEPLOYMENT}', '{SERVING}', "
                   f"'{DEV_CARD}', now(), 'o')"),
        ("a listing priced by another deployment's card",
         listing + f"('nemostation/marlin-2b', 9, '{MODEL}', '{PUBLIC_DEPLOYMENT}', '{SERVING}', "
                   f"'{DEV_CARD}', now(), 'o')"),
        ("moving an alias by editing its listing",
         "update infrx.catalog_listings set deployment_revision_id = deployment_revision_id, "
         "version = 2"),
        ("a listing under another model's alias",
         f"insert into public.models (id, name, provider, description, status, base_url, "
         f"served_model, input_usd_per_m, output_usd_per_m, context_tokens, input_modalities, "
         f"output_modalities) values ('x/y', 'x', 'x', 'x', 'live', 'u', 'x', 0, 0, 1, "
         f"'{{text}}', '{{text}}'); " + listing + f"('x/y', 1, '{MODEL}', '{PUBLIC_DEPLOYMENT}', "
         f"'{SERVING}', '{CARD}', now(), 'o')"),
        ("re-owning a model that has versions",
         f"update public.models set provider_org_id = '{OTHER_PROVIDER}' "
         f"where model_uuid = '{MODEL}'"),
        ("a model version owned by a provider that does not own the model",
         mv + good_mv.replace(f"'{NEMO}'", f"'{OTHER_PROVIDER}'")),
        ("a commit that is not a sha", mv + good_mv.replace("f" * 40, "main")),
        ("no weight shard", mv + good_mv.replace(f"array[{sha}]", "'{}'::text[]")),
        ("a null weight shard", mv + good_mv.replace(f"array[{sha}]", f"array[{sha}, null]")),
        ("a malformed shard digest",
         mv + good_mv.replace(f"array[{sha}]", f"array[{sha}, 'md5:abc']")),
        ("an unrecorded digest provenance", mv + good_mv.replace("'served_bytes'", "'guess'")),
        ("editing a model version's weights",
         "update infrx.model_versions set weight_shard_digests = array[]::text[]"),
        ("a second serving version with the same revision label",
         f"insert into infrx.serving_versions (model_version_id, model_id, provider_org_id, "
         f"revision_label, prompt_harness_ref, preprocessor_profile_version, runtime_image_ref, "
         f"engine_options_digest, precision, capability, created_by) select model_version_id, "
         f"model_id, provider_org_id, revision_label, prompt_harness_ref, "
         f"preprocessor_profile_version, runtime_image_ref, engine_options_digest, precision, "
         f"capability, 'o' from infrx.serving_versions where serving_version_id = '{SERVING}'"),
        ("a serving version with another billing meter",
         f"insert into infrx.serving_versions (model_version_id, model_id, provider_org_id, "
         f"revision_label, prompt_harness_ref, preprocessor_profile_version, runtime_image_ref, "
         f"engine_options_digest, precision, capability, created_by) select model_version_id, "
         f"model_id, provider_org_id, 'v2', prompt_harness_ref, preprocessor_profile_version, "
         f"runtime_image_ref, engine_options_digest, precision, capability || "
         f"'{{\"billing_meter\": \"seconds-v1\"}}', 'o' from infrx.serving_versions "
         f"where serving_version_id = '{SERVING}'"),
        ("pinning a runtime digest onto an existing serving version",
         f"update infrx.serving_versions set runtime_image_digest = {sha}"),
        ("a provider role that is a consumer role",
         f"insert into infrx.provider_memberships (provider_org_id, user_id, role, granted_by) "
         f"values ('{NEMO}', '{CONSUMER_1}', 'owner', 'ops')"),
        ("two current memberships of one provider",
         f"insert into infrx.provider_memberships (provider_org_id, user_id, role, granted_by) "
         f"values ('{NEMO}', '{PROVIDER_DEV_USER}', 'viewer', 'ops')"),
        ("promoting a membership in place",
         f"update infrx.provider_memberships set role = 'administrator' "
         f"where user_id = '{PROVIDER_DEV_USER}'"),
        ("an ungranted membership",
         f"insert into infrx.provider_memberships (provider_org_id, user_id, role, granted_by) "
         f"values ('{NEMO}', '{CONSUMER_1}', 'viewer', '')"),
        ("un-revoking a membership",
         f"update infrx.provider_memberships set revoked_at = now() "
         f"where user_id = '{PROVIDER_DEV_USER}'; update infrx.provider_memberships "
         f"set revoked_at = null where user_id = '{PROVIDER_DEV_USER}'"),
        ("deleting a membership",
         f"delete from infrx.provider_memberships where user_id = '{PROVIDER_DEV_USER}'"),
        ("a second dev wallet for one provider",
         f"insert into infrx.credit_wallets (kind, owner_provider_org_id) "
         f"values ('provider_dev', '{NEMO}')"),
        ("a dev wallet for a provider that does not exist",
         "insert into infrx.credit_wallets (kind, owner_provider_org_id) "
         "values ('provider_dev', gen_random_uuid())"),
        ("a signup grant into a provider's dev wallet",
         f"insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, operation_id, "
         f"actor) values ('{PROVIDER_WALLET}', 'provider_dev', 'signup_grant', 10000, "
         f"gen_random_uuid(), 'platform')"),
        ("renaming a provider (ownership rows are immutable)",
         f"update infrx.provider_orgs set slug = 'x' where provider_org_id = '{NEMO}'"),
    )
    accepted = (
        ("two providers may share display text (it is not a key)",
         "select 1 from infrx.provider_orgs where display_name = 'NemoStation' having count(*) = 2"),
        ("a dev deployment moves forward to retired",
         f"update infrx.deployment_revisions set state = 'retired' "
         f"where deployment_revision_id = '{DEV_DEPLOYMENT}'"),
        ("a membership is revoked once",
         f"update infrx.provider_memberships set revoked_at = now() "
         f"where user_id = '{PROVIDER_DEV_USER}'"),
        ("a new rate is a new card; the alias moves by a new listing version",
         card + f") values ('rc_marlin2b_v2', '{MODEL}', '{PUBLIC_DEPLOYMENT}', '{SERVING}', "
                f"500, 1500, now(), 'ops@infrx', false); " + listing +
         f"('nemostation/marlin-2b', 2, '{MODEL}', '{PUBLIC_DEPLOYMENT}', '{SERVING}', "
         f"'rc_marlin2b_v2', now(), 'ops@infrx')"),
        ("an operator allocation funds the provider's dev wallet",
         f"insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, operation_id, "
         f"actor) values ('{PROVIDER_WALLET}', 'provider_dev', 'operator_allocation', 1, "
         f"gen_random_uuid(), 'ops@infrx')"),
    )
    return refused, accepted


def check_registry(conn) -> str:
    """Item 3: provider-owned, immutable registry. Visibility/state/environment rules
    (R70), provider-coherent deployments, immutable cards/listings (CREDIT-RATE), digest
    provenance (R76), provider roles distinct from consumer roles, one dev wallet per
    provider and no signup money for it."""
    refused, accepted = _registry_cases()
    n = _all_refused(conn, refused, "registry")
    m = _all_accepted(conn, accepted, "registry controls")
    return f"{n} registry violations refused, {m} controls accepted"
